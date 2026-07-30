#!/usr/bin/env python3
"""Inspect a code project and safely initialize Git when explicitly requested."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_MARKERS = {
    "SKILL.md",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "CMakeLists.txt",
    "Dockerfile",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service-account.json",
}
GENERATED_DIRS = {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}


def run(command: list[str], cwd: Path, timeout: float = 12) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        message = (stderr + f"\ncommand timed out after {timeout:g}s").strip()
        return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=message)


def git(cwd: Path, *args: str, timeout: float = 12) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd, timeout=timeout)


def existing_repo_root(path: Path) -> Path | None:
    result = git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def has_project_marker(path: Path) -> bool:
    if any((path / marker).exists() for marker in PROJECT_MARKERS):
        return True
    return any(path.glob("*.csproj")) or any(path.glob("*.xcodeproj"))


def child_projects(path: Path) -> list[str]:
    candidates: list[str] = []
    try:
        children = list(path.iterdir())
    except OSError:
        return candidates
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / ".git").exists() or has_project_marker(child):
            candidates.append(str(child.resolve()))
    return sorted(candidates)


def is_broad_target(path: Path) -> bool:
    home = Path.home().resolve()
    broad = {
        Path("/").resolve(),
        home,
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Downloads").resolve(),
        (home / "Developer").resolve(),
        (home / ".codex").resolve(),
    }
    return path in broad


def get_bool_config(root: Path, key: str) -> bool | None:
    result = git(root, "config", "--bool", "--get", key)
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def sensitive_unignored(root: Path) -> list[str]:
    candidates: set[Path] = set()
    for pattern in (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "credentials*.json", "service-account*.json"):
        candidates.update(path for path in root.glob(pattern) if path.is_file())

    exposed: list[str] = []
    for path in sorted(candidates):
        name = path.name
        lowered = name.lower()
        if any(marker in lowered for marker in (".example", ".sample", ".template")):
            continue
        if not (
            name in SENSITIVE_NAMES
            or name.startswith(".env.")
            or path.suffix.lower() in SENSITIVE_SUFFIXES
            or name.startswith("credentials")
            or name.startswith("service-account")
        ):
            continue
        relative = str(path.relative_to(root))
        ignored = git(root, "check-ignore", "-q", "--", relative)
        if ignored.returncode != 0:
            exposed.append(relative)
    return exposed


def generated_unignored(root: Path) -> list[str]:
    exposed: list[str] = []
    for name in sorted(GENERATED_DIRS):
        path = root / name
        if not path.exists():
            continue
        ignored = git(root, "check-ignore", "-q", "--", name)
        if ignored.returncode != 0:
            exposed.append(name)
    return exposed


def initialize(path: Path) -> None:
    result = git(path, "init", "--initial-branch=main")
    if result.returncode == 0:
        return
    fallback = git(path, "init")
    if fallback.returncode != 0:
        raise ValueError((fallback.stderr or result.stderr).strip() or "git init failed")
    git(path, "branch", "-M", "main")


def inspect(path: Path, init_if_missing: bool) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"project path is not a directory: {resolved}")

    root = existing_repo_root(resolved)
    initialized_now = False
    if root is None:
        candidates = child_projects(resolved)
        if not init_if_missing:
            return {
                "status": "git_missing",
                "requested_path": str(resolved),
                "project_candidates": candidates,
            }
        if is_broad_target(resolved):
            raise ValueError(f"refusing to initialize broad directory: {resolved}")
        if candidates:
            raise ValueError(
                "refusing to initialize a container directory; select the exact project: "
                + ", ".join(candidates[:10])
            )
        if not has_project_marker(resolved):
            raise ValueError(f"no recognized code project marker found: {resolved}")
        initialize(resolved)
        root = existing_repo_root(resolved)
        if root is None:
            raise ValueError("git init completed but repository root could not be resolved")
        initialized_now = True

    head_result = git(root, "rev-parse", "HEAD")
    branch_result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    status_result = git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        timeout=8,
    )
    status_scan_timed_out = status_result.returncode == 124
    if status_scan_timed_out:
        status_result = git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
            timeout=5,
        )
    remote_result = git(root, "remote")
    status_lines = [line for line in status_result.stdout.splitlines() if line]

    return {
        "status": "ready",
        "requested_path": str(resolved),
        "repo_root": str(root),
        "initialized_now": initialized_now,
        "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else "main",
        "dirty": bool(status_lines),
        "working_tree": status_lines[:200],
        "working_tree_truncated": len(status_lines) > 200,
        "remotes": [line for line in remote_result.stdout.splitlines() if line],
        "auto_commit": get_bool_config(root, "codex.autoCommit"),
        "auto_push": get_bool_config(root, "codex.autoPush"),
        "warnings": {
            "sensitive_unignored": sensitive_unignored(root),
            "generated_unignored": generated_unignored(root),
            "working_tree_scan_timed_out": status_scan_timed_out,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--init-if-missing", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = inspect(args.path, args.init_if_missing)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
