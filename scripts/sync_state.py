#!/usr/bin/env python3
"""Create and optionally push an exact, validated cognitive-state snapshot.

No repository creation, no source-state edits, no global installation. The old
--codex-home/--repo/--push interface remains; a push is a separately authorized act.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from _state_common import (atomic_bytes, atomic_json, file_lock, now_iso,
                           parse_ledger, validate_index, cognitive_home)

SECRET_PATTERNS = {
    "github_token": re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.I),
}
MANIFEST = "SYNC_MANIFEST.json"
FIXED = {"STATE_INFO.json", "memories/PROJECT_INDEX.json", "memories/COGNITIVE_EVENTS.jsonl", "config/AGENTS.md"}
HEX256 = re.compile(r"[a-f0-9]{64}\Z")


def run(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False, timeout=45)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(arguments, 124, stdout="", stderr="command timed out")


def g(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", *args], repo)


def checked(result: subprocess.CompletedProcess, label: str) -> str:
    if result.returncode:
        # Do not echo arbitrary git stderr containing credentials or remote URLs.
        raise RuntimeError(f"{label} failed (exit {result.returncode})")
    return result.stdout


def managed_path(value: str) -> bool:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        return False
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in {"..", "."} for x in p.parts) or str(p) != value:
        return False
    return value in FIXED or (len(p.parts) == 3 and p.parts[0] == "automations" and p.parts[2] == "automation.toml" and not p.parts[1].startswith("."))


def safe_target(root: Path, relative: str) -> Path:
    if relative != MANIFEST and not managed_path(relative):
        raise ValueError("unrecognized managed path")
    target = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symbolic links are not allowed in managed path: {relative}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("managed path escapes repository")
    if target.exists() and not target.is_file():
        raise ValueError(f"managed file path is not a file: {relative}")
    return target


def scan_bytes(data: bytes) -> list[str]:
    text = data.decode("utf-8")
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def scan_secrets(path: Path) -> list[str]:
    return scan_bytes(path.read_bytes())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_json(path: Path) -> None:
    validate_index(json.loads(path.read_text(encoding="utf-8")))


def validate_jsonl(path: Path) -> int:
    return len(parse_ledger(path.read_text(encoding="utf-8")))


def source_map(codex_home: Path) -> list[tuple[Path, Path]]:
    result = [(codex_home / "STATE_INFO.json", Path("STATE_INFO.json")),
              (codex_home / "memories/PROJECT_INDEX.json", Path("memories/PROJECT_INDEX.json")),
              (codex_home / "memories/COGNITIVE_EVENTS.jsonl", Path("memories/COGNITIVE_EVENTS.jsonl"))]
    if (codex_home / "AGENTS.md").exists() or (codex_home / "AGENTS.md").is_symlink():
        result.append((codex_home / "AGENTS.md", Path("config/AGENTS.md")))
    auto = codex_home / "automations"
    if auto.exists():
        for path in sorted(auto.glob("*/automation.toml")):
            result.append((path, Path("automations") / path.parent.name / "automation.toml"))
    return result


def collect(home: Path) -> tuple[dict[str, bytes], int]:
    blobs = {}
    for source, relative in source_map(home):
        rel = relative.as_posix()
        if not managed_path(rel):
            raise ValueError("invalid automation directory name")
        current = source
        while current != home:
            if current.is_symlink():
                raise ValueError(f"symbolic source is not allowed: {rel}")
            if current == current.parent:
                raise ValueError("source escapes state home")
            current = current.parent
        if not source.is_file():
            raise ValueError(f"required state file missing: {rel}")
        # Copy bytes once: validation and writing consume this same snapshot.
        data = source.read_bytes()
        found = scan_bytes(data)
        if found:
            raise ValueError(f"secret scan blocked the snapshot: {rel}: {','.join(found)}")
        blobs[rel] = data
    marker = json.loads(blobs["STATE_INFO.json"].decode())
    if not isinstance(marker, dict) or marker.get("generation") != "cognitive-workbench-2.4" or marker.get("schema_version") != 1:
        raise ValueError("state marker is not a freshly initialized 2.4 store")
    validate_index(json.loads(blobs["memories/PROJECT_INDEX.json"].decode()))
    rows = parse_ledger(blobs["memories/COGNITIVE_EVENTS.jsonl"].decode())
    return blobs, len(rows)


def decode_manifest(data: bytes | None) -> dict | None:
    if data is None:
        return None
    value = json.loads(data.decode())
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("files"), list):
        raise ValueError("invalid previous sync manifest")
    seen = set()
    for item in value["files"]:
        if not isinstance(item, dict) or not managed_path(item.get("path")) or item["path"] in seen:
            raise ValueError("invalid/duplicate path in previous manifest")
        if not isinstance(item.get("sha256"), str) or not HEX256.fullmatch(item["sha256"]):
            raise ValueError("invalid manifest hash")
        if not isinstance(item.get("bytes"), int) or isinstance(item["bytes"], bool) or item["bytes"] < 0:
            raise ValueError("invalid manifest byte count")
        seen.add(item["path"])
    return value


def ensure_repo(repo: Path) -> Path:
    root = Path(checked(g(repo, "rev-parse", "--show-toplevel"), "repository root read").strip()).resolve()
    if root != repo.resolve():
        raise ValueError("--repo must be the exact state repository root")
    return Path(checked(g(repo, "rev-parse", "--absolute-git-dir"), "Git directory read").strip())


def require_empty_index(repo: Path) -> None:
    staged = g(repo, "diff", "--cached", "--quiet")
    if staged.returncode == 1:
        raise ValueError("pre-existing staged changes; preserved untouched, snapshot blocked")
    if staged.returncode != 0:
        raise RuntimeError("cannot inspect index; snapshot blocked")


def checked_push_target(repo: Path, remote: str, branch: str) -> str:
    if not remote or remote.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.-]+", remote):
        raise ValueError("invalid remote name")
    if not branch or branch.startswith("-") or g(repo, "check-ref-format", "refs/heads/" + branch).returncode:
        raise ValueError("invalid target branch")
    current = g(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if current.returncode != 0 or current.stdout.strip() != branch:
        raise ValueError("current branch does not match requested target; no snapshot/commit/push performed")
    urls = checked(g(repo, "remote", "get-url", "--push", "--all", remote), "push destination read").splitlines()
    if len(urls) != 1 or not urls[0] or urls[0].startswith("-"):
        raise ValueError("exactly one unambiguous push destination is required")
    return urls[0]


def plan_snapshot(home: Path, repo: Path) -> dict:
    blobs, rows = collect(home)
    manifest_path = safe_target(repo, MANIFEST)
    previous_bytes = manifest_path.read_bytes() if manifest_path.exists() else None
    previous = decode_manifest(previous_bytes)
    old = {item["path"]: item for item in previous["files"]} if previous else {}
    items = [{"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for rel, data in sorted(blobs.items())]
    current = {item["path"]: item for item in items}
    changed, removed = [], sorted(set(old) - set(current))
    for rel in sorted(set(old) | set(current)):
        target = safe_target(repo, rel)
        actual = target.read_bytes() if target.exists() else None
        if rel in old and actual is not None and hashlib.sha256(actual).hexdigest() != old[rel]["sha256"]:
            # A previous uncommitted managed snapshot may be retried if it is
            # already exactly the source desired for this run; other edits block.
            if actual != blobs.get(rel):
                raise ValueError(f"managed destination has independent edits: {rel}; preserved")
        if rel not in old and actual is not None and actual != blobs[rel]:
            raise ValueError(f"unowned destination conflict: {rel}; preserved")
        if rel in current and actual != blobs[rel]:
            changed.append(rel)
    same_content = previous is not None and previous.get("files") == items and previous.get("ledger_rows") == rows
    manifest = previous if same_content else {"schema_version": 1, "generated_at": now_iso(), "ledger_rows": rows,
                 "files": items, "restore_policy": "compare and merge by project/event identity; never overwrite newer local state"}
    payload = previous_bytes if same_content else (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    expected = dict(blobs); expected[MANIFEST] = payload
    return {"manifest": manifest, "expected": expected, "write_paths": changed,
            "delete_paths": [rel for rel in removed if (repo / rel).exists()],
            "manifest_changed": payload != previous_bytes,
            "changed": bool(changed or removed or payload != previous_bytes)}


def apply_plan(repo: Path, plan: dict) -> None:
    # All conflicts, structure and content scans were checked before any mutation.
    for rel in plan["write_paths"]:
        atomic_bytes(safe_target(repo, rel), plan["expected"][rel])
    for rel in plan["delete_paths"]:
        safe_target(repo, rel).unlink()
    if plan["manifest_changed"]:
        atomic_bytes(safe_target(repo, MANIFEST), plan["expected"][MANIFEST])


def snapshot(codex_home: Path, repo: Path) -> dict:
    home, repo = codex_home.expanduser().resolve(), repo.expanduser().resolve()
    gitdir = ensure_repo(repo)
    with file_lock(gitdir / "cognitive-sync"):
        require_empty_index(repo)
        plan = plan_snapshot(home, repo)
        apply_plan(repo, plan)
        return plan["manifest"]


def blob_at(repo: Path, ref: str, rel: str) -> bytes | None:
    # Bytes mode preserves CRLF and exact UTF-8 data for hashes and secret scans.
    r = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=repo, capture_output=True, check=False, timeout=20)
    return r.stdout if r.returncode == 0 else None


def changed_paths(repo: Path, cached: bool = False) -> list[str]:
    args = ["diff", "--name-only", "-z"]
    if cached: args.append("--cached")
    return [p for p in checked(g(repo, *args), "changed-path read").split("\0") if p]


def commit_and_push(repo: Path, expected: dict[str, bytes] | None = None, *, remote: str = "origin", branch: str = "main") -> dict:
    """Caller must serialize the snapshot transaction. No original index is reset."""
    push_url = checked_push_target(repo, remote, branch)
    require_empty_index(repo)
    current_manifest = decode_manifest(safe_target(repo, MANIFEST).read_bytes())
    if current_manifest is None: raise ValueError("no snapshot manifest")
    current = {i["path"]: i for i in current_manifest["files"]}
    old_manifest = decode_manifest(blob_at(repo, "HEAD", MANIFEST))
    old = {i["path"]: i for i in old_manifest["files"]} if old_manifest else {}
    deleted = sorted(set(old) - set(current))
    allowed = set(current) | set(deleted) | {MANIFEST}
    desired = expected or {rel: safe_target(repo, rel).read_bytes() for rel in (*current, MANIFEST)}
    if set(desired) != set(current) | {MANIFEST}:
        raise ValueError("expected snapshot set differs from manifest")
    for rel in (*current, MANIFEST):
        data = safe_target(repo, rel).read_bytes()
        if data != desired[rel]: raise ValueError("snapshot changed before staging; aborting")
        if rel in current and hashlib.sha256(data).hexdigest() != current[rel]["sha256"]:
            raise ValueError("snapshot bytes differ from manifest")
        if scan_bytes(data): raise ValueError(f"secret scan blocked staged candidate: {rel}")
    marker = json.loads(desired["STATE_INFO.json"].decode())
    if marker.get("generation") != "cognitive-workbench-2.4": raise ValueError("invalid 2.4 identity")
    validate_index(json.loads(desired["memories/PROJECT_INDEX.json"].decode()))
    parse_ledger(desired["memories/COGNITIVE_EVENTS.jsonl"].decode())
    # Only existing exact files and HEAD-tracked removals are passed to git add.
    stage = sorted(set(current) | {MANIFEST} | {p for p in deleted if blob_at(repo, "HEAD", p) is not None})
    checked(g(repo, "--literal-pathspecs", "add", "--", *stage), "exact-path staging")
    staged = changed_paths(repo, cached=True)
    if not set(staged).issubset(allowed):
        raise ValueError("unexpected staged path; index preserved, commit blocked")
    for rel in staged:
        data = blob_at(repo, "", rel)  # ':path' reads the index, not the worktree.
        if rel in deleted:
            if data is not None: raise ValueError("managed deletion is not staged as deletion")
        else:
            if data is None or data != desired[rel]: raise ValueError("staged bytes differ from validated source; commit blocked")
            if scan_bytes(data): raise ValueError("secret scan blocked actual staged content")
    if staged:
        checked(g(repo, "commit", "-m", "Sync validated cognitive state " + now_iso()), "local state commit")
    head = checked(g(repo, "rev-parse", "--verify", "HEAD^{commit}"), "local commit read").strip()
    if staged:
        actual_paths = [p for p in checked(g(repo, "diff-tree", "--root", "--no-commit-id", "-r", "--name-only", "-z", head), "committed-path read").split("\0") if p]
        if not set(actual_paths).issubset(allowed):
            raise ValueError("commit hook added unrelated paths; local commit preserved, no push")
    # Hooks are allowed by Git; verify what the actual resulting commit contains.
    for rel, data in desired.items():
        if blob_at(repo, head, rel) != data:
            raise ValueError("committed snapshot differs from expected; no push performed")
    for rel in deleted:
        if blob_at(repo, head, rel) is not None:
            raise ValueError("committed deletion is missing; no push performed")
    if checked_push_target(repo, remote, branch) != push_url:
        raise ValueError("push destination changed during transaction; local commit preserved")
    remote_read = g(repo, "ls-remote", "--exit-code", push_url, "refs/heads/" + branch)
    if remote_read.returncode not in (0, 2): raise RuntimeError("cannot read remote target; local commit preserved")
    remote_sha = remote_read.stdout.split()[0] if remote_read.returncode == 0 and remote_read.stdout.strip() else None
    pushed = remote_sha != head
    if pushed:
        checked(g(repo, "push", remote, f"HEAD:refs/heads/{branch}"), "push; local commit is preserved")
    confirmed = checked(g(repo, "ls-remote", "--exit-code", push_url, "refs/heads/" + branch), "remote verification").split()
    if not confirmed or confirmed[0] != head:
        raise RuntimeError("remote target SHA does not match local commit")
    return {"changed": bool(staged), "pushed": pushed, "commit": head, "remote_verified": True,
            "remote_commit": confirmed[0], "branch": branch}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--codex-home", type=Path, default=cognitive_home())
    p.add_argument("--repo", type=Path, default=cognitive_home() / "state-backup/cognitive-workbench-state")
    p.add_argument("--push", action="store_true")
    p.add_argument("--remote", default="origin"); p.add_argument("--branch", default="main")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    try:
        home, repo = a.codex_home.expanduser().resolve(), a.repo.expanduser().resolve()
        gitdir = ensure_repo(repo)
        # A dry run does not even create a lock file or any snapshot file.
        if a.dry_run:
            require_empty_index(repo)
            if a.push: checked_push_target(repo, a.remote, a.branch)
            plan = plan_snapshot(home, repo)
            print(json.dumps({"status": "preview", "changed": plan["changed"], "write_paths": plan["write_paths"], "delete_paths": plan["delete_paths"], "manifest_changed": plan["manifest_changed"]}, ensure_ascii=False, indent=2)); return 0
        with file_lock(gitdir / "cognitive-sync"):
            require_empty_index(repo)
            if a.push: checked_push_target(repo, a.remote, a.branch)
            plan = plan_snapshot(home, repo)
            apply_plan(repo, plan)
            result = {"status": "snapshot_ready", "repo": str(repo), "files": len(plan["manifest"]["files"]),
                      "ledger_rows": plan["manifest"]["ledger_rows"], "changed": plan["changed"],
                      "pushed": False, "write_paths": plan["write_paths"], "delete_paths": plan["delete_paths"]}
            if a.push:
                result.update(commit_and_push(repo, plan["expected"], remote=a.remote, branch=a.branch))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, indent=2)); return 1

if __name__ == "__main__":
    raise SystemExit(main())
