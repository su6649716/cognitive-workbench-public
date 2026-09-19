#!/usr/bin/env python3
"""Create a validated, secret-scanned snapshot of cognitive-workbench state."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


SECRET_PATTERNS = {
    "github_token": re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
}


def run(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)


def validate_json(path: Path) -> None:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")


def validate_jsonl(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            count += 1
    return count


def scan_secrets(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def source_map(codex_home: Path) -> list[tuple[Path, Path]]:
    sources = [
        (codex_home / "memories" / "PROJECT_INDEX.json", Path("memories/PROJECT_INDEX.json")),
        (
            codex_home / "memories" / "COGNITIVE_EVENTS.jsonl",
            Path("memories/COGNITIVE_EVENTS.jsonl"),
        ),
        (codex_home / "AGENTS.md", Path("config/AGENTS.md")),
    ]
    automation_root = codex_home / "automations"
    if automation_root.exists():
        for path in sorted(automation_root.glob("*/automation.toml")):
            sources.append((path, Path("automations") / path.parent.name / path.name))
    return sources


def snapshot(codex_home: Path, repo: Path) -> dict[str, object]:
    if not (repo / ".git").is_dir():
        raise ValueError(f"state repository is not initialized: {repo}")

    sources = source_map(codex_home)
    missing = [str(source) for source, _ in sources[:3] if not source.is_file()]
    if missing:
        raise ValueError("required state files are missing: " + ", ".join(missing))

    validate_json(sources[0][0])
    ledger_rows = validate_jsonl(sources[1][0])
    findings: dict[str, list[str]] = {}
    for source, _ in sources:
        matches = scan_secrets(source)
        if matches:
            findings[str(source)] = matches
    if findings:
        summary = "; ".join(f"{path}: {','.join(kinds)}" for path, kinds in findings.items())
        raise ValueError("secret scan blocked the snapshot: " + summary)

    copied: list[dict[str, object]] = []
    for source, relative in sources:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
        copied.append(
            {
                "path": relative.as_posix(),
                "bytes": destination.stat().st_size,
                "sha256": digest(destination),
            }
        )

    manifest = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "ledger_rows": ledger_rows,
        "files": copied,
        "restore_policy": "compare and merge by project/event identity; never overwrite newer local state",
    }
    manifest_path = repo / "SYNC_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def commit_and_push(repo: Path) -> dict[str, object]:
    add = run(
        ["git", "add", "--", "memories", "config", "automations", "SYNC_MANIFEST.json"], repo
    )
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add failed")
    changed = run(["git", "diff", "--cached", "--quiet"], repo)
    if changed.returncode == 0:
        return {"changed": False, "pushed": False, "commit": None}
    if changed.returncode != 1:
        raise RuntimeError(changed.stderr.strip() or "git diff failed")
    message = "Sync cognitive state " + dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    commit = run(["git", "commit", "-m", message], repo)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or "git commit failed")
    head = run(["git", "rev-parse", "HEAD"], repo)
    if head.returncode != 0:
        raise RuntimeError(head.stderr.strip() or "cannot resolve commit")
    push = run(["git", "push", "origin", "main"], repo)
    if push.returncode != 0:
        raise RuntimeError("local snapshot committed but push failed: " + push.stderr.strip())
    return {"changed": True, "pushed": True, "commit": head.stdout.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.home() / ".codex" / "state-backup" / "cognitive-workbench-state",
    )
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    try:
        manifest = snapshot(args.codex_home.expanduser().resolve(), args.repo.expanduser().resolve())
        result: dict[str, object] = {
            "status": "snapshot_ready",
            "repo": str(args.repo.expanduser().resolve()),
            "files": len(manifest["files"]),
            "ledger_rows": manifest["ledger_rows"],
        }
        if args.push:
            result.update(commit_and_push(args.repo.expanduser().resolve()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
