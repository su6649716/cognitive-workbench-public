#!/usr/bin/env python3
"""Inspect a project and its Git checkpoint without confusing missing evidence with clean state."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_MARKERS = {"SKILL.md", "package.json", "pyproject.toml", "setup.py", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "CMakeLists.txt", "Dockerfile"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
GENERATED_DIRS = {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}


def run(command: list[str], cwd: Path, timeout: float = 12) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, stdout="", stderr="command timed out")


def git(cwd: Path, *args: str, timeout: float = 12) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd, timeout)


def must(result: subprocess.CompletedProcess, action: str) -> str:
    if result.returncode:
        raise ValueError(f"Git {action} failed (exit {result.returncode}); state is unknown")
    return result.stdout


def existing_repo_root(path: Path) -> Path | None:
    result = git(path, "rev-parse", "--show-toplevel")
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    # Do not treat repository corruption or safety/read errors as a missing repo.
    if result.returncode == 128 and "not a git repository" in (result.stderr or "").lower():
        return None
    raise ValueError("cannot determine repository root; refusing initialization")


def has_project_marker(path: Path) -> bool:
    return any((path / n).exists() for n in PROJECT_MARKERS) or any(path.glob("*.csproj")) or any(path.glob("*.xcodeproj"))


def child_projects(path: Path) -> list[str]:
    return sorted(str(p.resolve()) for p in path.iterdir() if p.is_dir() and not p.name.startswith(".") and ((p / ".git").exists() or has_project_marker(p)))


def is_broad_target(path: Path) -> bool:
    home = Path.home().resolve()
    return path in {Path("/").resolve(), home, *[(home / n).resolve() for n in ("Desktop", "Documents", "Downloads", "Developer", ".codex")]}


def get_bool_config(root: Path, key: str) -> bool | None:
    r = git(root, "config", "--bool", "--get", key)
    if r.returncode == 1: return None
    if r.returncode: raise ValueError(f"cannot read {key}")
    return r.stdout.strip() == "true"


def sensitive_unignored(root: Path) -> list[str]:
    paths = must(git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"), "sensitive-path listing").split("\0")
    result = []
    for relative in paths:
        name = Path(relative).name.lower()
        if any(m in name for m in (".example", ".sample", ".template")):
            continue
        if name == ".env" or name.startswith(".env.") or name.startswith("credentials") or name.startswith("service-account") or Path(name).suffix in SENSITIVE_SUFFIXES:
            result.append(relative)
    return sorted(set(result))


def generated_unignored(root: Path) -> list[str]:
    return [n for n in sorted(GENERATED_DIRS) if (root / n).exists() and git(root, "check-ignore", "-q", "--", n).returncode != 0]


def normalize_task_paths(root: Path, values: list[str]) -> list[str]:
    result = []
    for value in values:
        p = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        try: relative = p.relative_to(root).as_posix()
        except ValueError as exc: raise ValueError("task path is outside repository") from exc
        if relative == ".git" or relative.startswith(".git/"): raise ValueError("task path cannot include Git internals")
        if relative not in result: result.append(relative)
    if not result: raise ValueError("close check requires at least one --task-path")
    return result


def path_in_scope(path: str, scopes: list[str]) -> bool:
    return any(s == "." or path == s or path.startswith(s.rstrip("/") + "/") for s in scopes)


def porcelain_records(text: str) -> list[tuple[str, str]]:
    pieces, result, i = text.split("\0"), [], 0
    while i < len(pieces):
        entry = pieces[i]; i += 1
        if not entry: continue
        if len(entry) < 4: raise ValueError("invalid Git porcelain output")
        status, path = entry[:2], entry[3:]
        result.append((status, path))
        if "R" in status or "C" in status:
            if i >= len(pieces) or not pieces[i]: raise ValueError("incomplete Git rename status")
            result.append((status, pieces[i])); i += 1
    return result


def porcelain_path(line: str) -> str:
    # Compatibility for callers handling the printable working_tree list.
    return line[3:] if len(line) > 3 else ""


def close_check(root: Path, baseline_head: str | None, task_path_values: list[str], working_tree: list[str]) -> dict[str, Any]:
    scopes = normalize_task_paths(root, task_path_values)
    result = {"ready": False, "baseline_head": baseline_head, "current_head": None, "task_paths": scopes,
              "committed_paths": [], "dirty_task_paths": [], "unrelated_dirty_paths": [], "reasons": []}
    reasons = result["reasons"]
    current = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if current.returncode:
        reasons.append("repository has no readable commit"); return result
    head = current.stdout.strip(); result["current_head"] = head
    status = git(root, "--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *scopes)
    if status.returncode:
        reasons.append("cannot read task working-tree status; state is unknown"); return result
    result["dirty_task_paths"] = [p for _, p in porcelain_records(status.stdout)]
    result["unrelated_dirty_paths"] = [porcelain_path(line) for line in working_tree if not path_in_scope(porcelain_path(line), scopes)]
    if result["dirty_task_paths"]: reasons.append("task paths still contain uncommitted changes")
    baseline = None if not baseline_head or baseline_head.lower() == "none" else baseline_head
    result["baseline_head"] = baseline
    if baseline:
        if baseline.startswith("-"):
            reasons.append("invalid baseline revision"); return result
        resolved = git(root, "rev-parse", "--verify", baseline + "^{commit}")
        if resolved.returncode:
            reasons.append("baseline is not a valid commit"); return result
        base_sha = resolved.stdout.strip()
        ancestry = git(root, "merge-base", "--is-ancestor", base_sha, head)
        if ancestry.returncode != 0:
            reasons.append("baseline is not an ancestor of HEAD or ancestry could not be verified"); return result
        if base_sha == head: reasons.append("HEAD has not advanced from the recorded baseline")
        changed = git(root, "--literal-pathspecs", "diff", "--name-only", "-z", base_sha, head, "--", *scopes)
    else:
        changed = git(root, "--literal-pathspecs", "show", "--pretty=format:", "--name-only", "-z", head, "--", *scopes)
    if changed.returncode:
        reasons.append("cannot read committed paths"); return result
    result["committed_paths"] = [p for p in changed.stdout.split("\0") if p]
    if not result["committed_paths"]: reasons.append("no committed change was found under the declared task paths")
    result["ready"] = not reasons
    return result


def initialize(path: Path) -> None:
    r = git(path, "init", "--initial-branch=main")
    if r.returncode:
        must(git(path, "init"), "initialize")
        must(git(path, "branch", "-M", "main"), "set initial branch")


def inspect(path: Path, init_if_missing: bool, *, check_close: bool = False, baseline_head: str | None = None, task_paths: list[str] | None = None) -> dict:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir(): raise ValueError("project path is not a directory")
    root, initialized = existing_repo_root(resolved), False
    if root is None:
        children = child_projects(resolved)
        if not init_if_missing: return {"status": "git_missing", "requested_path": str(resolved), "project_candidates": children}
        if is_broad_target(resolved) or children or not has_project_marker(resolved):
            raise ValueError("refusing to initialize broad/container/non-project directory")
        initialize(resolved); root = existing_repo_root(resolved); initialized = True
        if root is None: raise ValueError("initialized repository root is unresolved")
    status = must(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"), "working-tree read")
    records = porcelain_records(status)
    lines = [s + " " + p for s, p in records]
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode not in (0, 1): raise ValueError("cannot determine current branch")
    head_result = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    remotes = must(git(root, "remote"), "remote listing").splitlines()
    data = {"status": "ready", "requested_path": str(resolved), "repo_root": str(root),
            "initialized_now": initialized, "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
            "branch": branch.stdout.strip() if branch.returncode == 0 else None,
            "detached_head": branch.returncode == 1, "dirty": bool(records),
            "working_tree": lines[:200], "working_tree_truncated": len(lines) > 200,
            "remotes": remotes, "auto_commit": get_bool_config(root, "codex.autoCommit"),
            "auto_push": get_bool_config(root, "codex.autoPush"),
            "warnings": {"sensitive_unignored": sensitive_unignored(root), "generated_unignored": generated_unignored(root), "working_tree_scan_timed_out": False}}
    if check_close: data["closure"] = close_check(root, baseline_head, task_paths or [], lines)
    return data


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", type=Path, required=True)
    p.add_argument("--init-if-missing", action="store_true")
    p.add_argument("--check-close", action="store_true")
    p.add_argument("--baseline-head")
    p.add_argument("--task-path", action="append", default=[])
    return p


def main() -> int:
    a = build_parser().parse_args()
    try:
        result = inspect(a.path, a.init_if_missing, check_close=a.check_close, baseline_head=a.baseline_head, task_paths=a.task_path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Kept compatible: inspect succeeded => 0; callers MUST inspect closure.ready.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
