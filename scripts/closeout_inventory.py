#!/usr/bin/env python3
"""Read-only project knowledge inventory; emits metadata, never edits or cleans.

Design informed by KKKKhazix/khazix-skills neat-freak (MIT, see bundled notice).
This implementation uses Python's standard library. Candidate files are not
verified instructions, deletion approvals, or evidence of deployed behavior.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any

EXCLUDED = {'.git', 'node_modules', '.next', 'dist', 'build', '.venv', 'venv',
            '__pycache__', 'target', 'vendor', '.turbo', '.cache', '.pytest_cache'}
RULE_NAMES = {'AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', 'CLAUDE.local.md',
              'GEMINI.md', '.cursorrules', '.windsurfrules'}
ANCESTOR_RULES = ('AGENTS.override.md', 'AGENTS.md', 'CLAUDE.md',
                  'CLAUDE.local.md', '.claude/CLAUDE.md')
MEMORY_NAMES = {'MEMORY.md', 'memory_summary.md', 'raw_memories.md',
                'COGNITIVE_EVENTS.jsonl', 'PROJECT_INDEX.json'}
RESIDUE = re.compile(r'^(?:plan|todo|implementation[-_]notes)(?:[.\-_]|$)|'
                     r'(?:[_-](?:old|backup|bak|v[0-9]+))(?:\.|$)|'
                     r'^(?:debug[-_]|tmp[-_])', re.I)


def describe(path: Path, root: Path) -> dict[str, Any]:
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = str(path)
    info = path.lstat()
    entry: dict[str, Any] = {'path': label, 'bytes': info.st_size}
    if stat.S_ISLNK(info.st_mode):
        entry.update(kind='symlink', target=os.readlink(path))
        try:
            target = path.resolve(strict=False)
            if not target.is_relative_to(root):
                entry['link_state'] = 'external-target-unverified'
            else:
                entry['link_state'] = 'internal-existing' if target.exists() else 'broken'
        except (OSError, RuntimeError):
            entry['link_state'] = 'unresolved'
    elif stat.S_ISDIR(info.st_mode):
        entry['kind'] = 'directory'
    elif stat.S_ISREG(info.st_mode):
        entry['kind'] = 'file'
    else:
        entry['kind'] = 'special'
    return entry


def is_rule(relative: str) -> bool:
    path = Path(relative)
    return (path.name in RULE_NAMES or
            any(fragment in '/' + relative for fragment in ('/.claude/rules/', '/.cursor/rules/'))
            and path.suffix.lower() in {'.md', '.mdc'})


def parse_status(data: bytes) -> list[dict[str, str]]:
    """Parse git porcelain v1 -z, preserving spaces/newlines and rename pairs."""
    tokens = data.split(b'\0')
    rows: list[dict[str, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]; i += 1
        if not token:
            continue
        if len(token) < 4 or token[2:3] != b' ':
            raise ValueError('malformed git status metadata')
        code = token[:2].decode('ascii')
        row = {'status': code, 'path': os.fsdecode(token[3:])}
        if any(c in code for c in 'RC'):
            if i >= len(tokens) or not tokens[i]:
                raise ValueError('missing rename/copy source')
            row['original_path'] = os.fsdecode(tokens[i]); i += 1
        rows.append(row)
    return rows


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    # Avoid ambient worktree/index redirection and optional status writes/hooks.
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        env.pop(key, None)
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', LC_ALL='C')
    return subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                           '-c', 'core.untrackedCache=false', '-C', str(root), *args],
                          capture_output=True, check=False, env=env, timeout=8)


def git_inventory(root: Path) -> dict[str, Any]:
    def required(*args: str) -> bytes:
        result = run_git(root, *args)
        if result.returncode:
            raise ValueError(f'git {args[0]} failed (exit {result.returncode})')
        return result.stdout
    try:
        repo = run_git(root, 'rev-parse', '--show-toplevel')
        if repo.returncode:
            has_marker = any(os.path.lexists(p / '.git') for p in (root, *root.parents))
            if (repo.returncode == 128 and not has_marker and
                    b'not a git repository' in repo.stderr):
                return {'status': 'not-applicable', 'reason': 'no Git repository', 'dirty': None}
            return {'status': 'error', 'reason': 'cannot identify Git root', 'dirty': None}
        head = run_git(root, 'rev-parse', '--verify', '--quiet', 'HEAD')
        if head.returncode not in (0, 1):
            raise ValueError('cannot read HEAD')
        branch = run_git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        if branch.returncode not in (0, 1):
            raise ValueError('cannot read branch')
        # Whole repository state is metadata only; do not claim subdirectory ownership.
        state = required('status', '--porcelain=v1', '-z', '--untracked-files=all')
        changes = parse_status(state)
        trees = required('worktree', 'list', '--porcelain', '-z')
        worktrees = [os.fsdecode(t[9:]) for t in trees.split(b'\0') if t.startswith(b'worktree ')]
        return {'status': 'verified-metadata', 'root': os.fsdecode(repo.stdout.rstrip(b'\n')),
                'head': head.stdout.decode('ascii').strip() if head.returncode == 0 else None,
                'head_state': 'committed' if head.returncode == 0 else 'unborn',
                'branch': os.fsdecode(branch.stdout.rstrip(b'\n')) if branch.returncode == 0 else None,
                'branch_state': 'named' if branch.returncode == 0 else 'detached',
                'dirty': bool(changes), 'changes': changes, 'worktrees': worktrees,
                'scope_note': 'Git metadata covers the repository; no outside worktree contents were read.'}
    except FileNotFoundError:
        return {'status': 'unavailable', 'reason': 'Git is not installed or not on PATH', 'dirty': None}
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'reason': 'Git metadata command timed out', 'dirty': None}
    except (OSError, ValueError, UnicodeError) as exc:
        return {'status': 'error', 'reason': str(exc), 'dirty': None}


def inventory(project: Path, *, max_entries: int = 20000,
              ancestor_rules: bool = False) -> dict[str, Any]:
    root = project.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('project must be an existing directory')
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError('select an exact project, not filesystem root or home')
    if max_entries < 1:
        raise ValueError('max-entries must be positive')
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    excluded: list[str] = []
    stack = [root]
    visited = 0
    truncated = False
    while stack and not truncated:
        directory = stack.pop()
        try:
            with os.scandir(directory) as listing:
                for child in listing:
                    if visited >= max_entries:
                        truncated = True; break
                    visited += 1
                    path = Path(child.path)
                    item = describe(path, root)
                    if item['kind'] == 'directory' and child.name in EXCLUDED:
                        excluded.append(item['path']); continue
                    entries.append(item)
                    if item['kind'] == 'directory':
                        stack.append(path)
        except OSError as exc:
            errors.append({'path': str(directory.relative_to(root)), 'error': type(exc).__name__})
    entries.sort(key=lambda x: x['path'])
    ancestors = []
    if ancestor_rules:
        for parent in root.parents:
            for name in ANCESTOR_RULES:
                path = parent / name
                try:
                    if os.path.lexists(path):
                        ancestors.append(describe(path, root))
                except OSError as exc:
                    errors.append({'path': str(path), 'error': type(exc).__name__})
    files = [x for x in entries if x['kind'] == 'file']
    git = git_inventory(root)
    complete = not truncated and not errors
    return {
        'schema_version': 1, 'project_root': str(root),
        'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'read_only': True, 'content_read': False, 'network_used': False,
        'content_read_definition': 'No document body is extracted or output. Git may inspect tracked files for status.',
        'inventory_complete': complete, 'truncated': truncated, 'max_entries': max_entries,
        'entries_seen': visited, 'file_count': len(files), 'excluded_directories': sorted(excluded),
        'entries': entries,
        'markdown': [x for x in files if Path(x['path']).suffix.lower() in {'.md', '.mdx'}],
        'rule_candidates': [x for x in entries if x['kind'] in {'file', 'symlink'} and is_rule(x['path'])],
        'ancestor_rule_candidates': ancestors,
        'memory_candidates': [x for x in entries if Path(x['path']).name in MEMORY_NAMES],
        'residue_candidates': [dict(x, judgement='unreviewed-name-hint', deletion_authorized=False)
                               for x in entries if RESIDUE.search(Path(x['path']).name)],
        'symlinks': [x for x in entries if x['kind'] == 'symlink'],
        'errors': errors, 'git': git,
        'interpretation': 'Metadata only. Verify relevant content, actual rule loading and permissions separately.'
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--max-entries', type=int, default=20000)
    parser.add_argument('--ancestor-rules', action='store_true')
    args = parser.parse_args()
    try:
        result = inventory(args.project, max_entries=args.max_entries,
                           ancestor_rules=args.ancestor_rules)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'invalid-input', 'error': str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['inventory_complete'] and result['git']['status'] not in {'error', 'unavailable'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
