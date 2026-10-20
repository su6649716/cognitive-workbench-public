#!/usr/bin/env python3
"""Fresh 2.4 project state and deterministic project-turn lifecycle.

No legacy migration, no writes inside user repositories, no semantic guesses.
Git snapshots record only identifiers and paths, never diffs or prompt text.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from _state_common import (
    atomic_json, cognitive_home, file_lock, now_iso, parse_ledger, validate_index,
    nonempty,
)

STATE_FILE = 'STATE_INFO.json'
INDEX = 'memories/PROJECT_INDEX.json'
LEDGER = 'memories/COGNITIVE_EVENTS.jsonl'
MARKERS = ('SKILL.md', 'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod',
           'requirements.txt', 'setup.py', 'pom.xml', 'Dockerfile', '.git')


def state_paths(home: Path) -> tuple[Path, Path, Path]:
    return home / STATE_FILE, home / INDEX, home / LEDGER


def check_state(home: Path) -> None:
    marker, index, ledger = state_paths(home)
    if not marker.exists():
        raise ValueError('fresh 2.4 state not initialized; run init (legacy state is never adopted automatically)')
    meta = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(meta, dict) or meta.get('generation') != 'cognitive-workbench-2.4' or meta.get('schema_version') != 1:
        raise ValueError('state marker does not match 2.4, refusing to read it')
    validate_index(json.loads(index.read_text(encoding='utf-8')))
    parse_ledger(ledger.read_text(encoding='utf-8'))


def init(home: Path) -> bool:
    """Initialize only empty state. Never import or overwrite an old cognitive store."""
    home = home.expanduser().absolute()
    if home.is_symlink():
        raise ValueError('state home cannot be a symlink')
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker, index, ledger = state_paths(home)
    with file_lock(home / STATE_FILE):
        if marker.exists():
            check_state(home)
            return False
        if index.exists() or ledger.exists():
            raise ValueError('unmarked state already exists; refusing to reuse or overwrite')
        index.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_json(index, {'schema_version': 3, 'projects': []})
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        atomic_json(marker, {'schema_version': 1, 'generation': 'cognitive-workbench-2.4',
                             'created_at': now_iso(), 'state_id': 'cw24_' + uuid.uuid4().hex[:16]})
        return True


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                              check=False, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(['git'], 127, b'', b'')


def identify_project(path: Path) -> Path | None:
    """Git root preferred; a non-Git root must have a recognizable marker."""
    path = path.expanduser().resolve()
    if not path.is_dir():
        return None
    root = run_git(path, 'rev-parse', '--show-toplevel')
    if root.returncode == 0 and root.stdout:
        path = Path(os.fsdecode(root.stdout.strip())).resolve()
    elif not any((path / marker).exists() for marker in MARKERS):
        return None
    if path == Path('/') or path == Path.home().resolve() or path.name in {'.codex', '.cognitive-workbench'}:
        return None
    return path


def read_index(home: Path) -> dict:
    check_state(home)
    return validate_index(json.loads((home / INDEX).read_text(encoding='utf-8')))


def find_project(index: dict, root: Path) -> dict | None:
    return next((p for p in index['projects'] if p['root'] == str(root)), None)


def current_git(root: Path) -> dict:
    head = run_git(root, 'rev-parse', '--verify', 'HEAD')
    status = run_git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=normal')
    if status.returncode:
        return {'available': False}
    head_sha = os.fsdecode(head.stdout.strip()) if head.returncode == 0 else None
    paths = []
    # -z output is unquoted. Git rename status includes a second path, keep both.
    entries = status.stdout.split(b'\0')
    for entry in entries:
        if len(entry) >= 4 and entry[2:3] == b' ':
            paths.append(os.fsdecode(entry[3:]))
        elif entry:
            paths.append(os.fsdecode(entry))
    paths = sorted(set(paths))[:200]
    # Stat fingerprints detect edits to an already dirty file without storing content.
    stats = []
    for name in paths:
        p = root / name
        try:
            stat = p.lstat()
            stats.append((name, stat.st_size, stat.st_mtime_ns))
        except (OSError, ValueError):
            stats.append((name, None, None))
    raw = json.dumps([head_sha, os.fsdecode(status.stdout), stats], ensure_ascii=False).encode()
    return {'available': True, 'head': head_sha, 'paths': paths,
            'dirty_count': len(paths), 'fingerprint': hashlib.sha256(raw).hexdigest()}


def plan_files(root: Path) -> list[str]:
    candidates = ('docs/PROJECT_PLAN.md', 'docs/PROJECT_BASELINE.md', 'docs/architecture.md',
                  'PRODUCT.md', 'README.md', 'AGENTS.md')
    return [str(root / item) for item in candidates if (root / item).is_file()]


def development_checks() -> dict:
    """Restore required checks, never fabricate the result of performing them."""
    return {
        'reference': str(Path(__file__).resolve().parent.parent / 'references/development-checks.md'),
        'before_changes': '核对定稿边界与实际运行依赖',
        'on_correction': '排查所有同类对象，仅修复不一致处',
        'before_completion': '按真实页面或实际接口、命令取得对应验收证据',
        'ui_boundaries': '新增或修改UI组件时，主动检查数量增长、长内容、窄容器及必要异常状态；复用公共规则，验证完整访问、关键操作和选择保留，反查同类消费者',
        'status': 'not_evaluated',
    }


def register(home: Path, root: Path, name: str | None) -> dict:
    with file_lock(home / INDEX):
        doc = validate_index(json.loads((home / INDEX).read_text(encoding='utf-8')))
        existing = find_project(doc, root)
        if existing:
            return existing
        timestamp = now_iso()
        item = {'id': 'prj_' + uuid.uuid4().hex[:12], 'name': name or root.name,
                'root': str(root), 'created_at': timestamp, 'updated_at': timestamp,
                'status': 'unknown', 'aliases': [], 'product_docs': [], 'thread_ids': []}
        doc['projects'].append(item)
        atomic_json(home / INDEX, doc)
        return item


def context(home: Path, root: Path, project: dict | None) -> dict:
    history = parse_ledger((home / LEDGER).read_text(encoding='utf-8')) if (home / LEDGER).exists() else []
    # Stable identifier only; do not mix records from a different project of same name.
    events = [line for line in history if line.get('record_kind') == 'event'
              and project and line.get('project') == project['id']]
    return {'project_root': str(root), 'project_id': project.get('id') if project else None,
            'project_name': project.get('name') if project else root.name,
            'planning_baseline': project.get('planning_baseline') if project else None,
            'last_task': project.get('last_task') if project else None,
            'last_observed_change': project.get('last_observed_change') if project else None,
            'last_checkpoint': project.get('last_checkpoint') if project else None,
            'planning_files': plan_files(root), 'decision_events': len(events),
            'recent_decisions': [e.get('statement') for e in events[-3:]],
            'development_checks': development_checks(),
            'state_home': str(home)}


def runtime_marker(home: Path, session: str, turn: str) -> Path:
    key = hashlib.sha256((session + '\0' + turn).encode()).hexdigest()
    return home / 'runtime' / (key + '.json')


def capture_baseline(home: Path, root: Path, session: str, turn: str) -> None:
    """Record only a temporary before-state; the project index stays untouched."""
    marker = runtime_marker(home, session, turn)
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(home / "runtime/turns"):
        if not marker.exists():
            atomic_json(marker, {"root": str(root), "session": session, "turn": turn,
                                "before": current_git(root), "recorded_at": now_iso()})


def begin(home: Path, path: Path, *, name: str | None = None, session: str | None = None,
          turn: str | None = None, readonly: bool = False) -> dict:
    root = identify_project(path)
    if not root:
        return {'project_detected': False, 'reason': 'not a recognized project root'}
    if readonly:
        if not (home / STATE_FILE).exists():
            return {'project_detected': True, 'registered': False, 'state_initialized': False,
                    'project_root': str(root), 'development_checks': development_checks()}
        doc = read_index(home)
        found = find_project(doc, root)
    else:
        init(home)
        found = register(home, root, name)
    data = context(home, root, found)
    data['project_detected'] = True
    data['registered'] = found is not None
    if session and turn and not readonly:
        capture_baseline(home, root, session, turn)
        data['turn_baseline_recorded'] = True
    return data


def complete(home: Path, path: Path, summary: str, checkpoint: str | None = None) -> dict:
    if not summary or not summary.strip():
        raise ValueError('summary cannot be blank')
    root = identify_project(path)
    if not root:
        raise ValueError('project root not recognized')
    with file_lock(home / INDEX):
        doc = read_index(home)
        project = find_project(doc, root)
        if not project:
            raise ValueError('project not registered; run begin')
        if checkpoint:
            git = current_git(root)
            if not git.get('available') or git.get('head') != checkpoint:
                raise ValueError('provided checkpoint must match current Git HEAD')
            project['last_checkpoint'] = checkpoint
        project['last_task'] = {'summary': summary.strip()[:600], 'recorded_at': now_iso(),
                                'source': 'agent-explicit-closeout'}
        project['updated_at'] = now_iso()
        atomic_json(home / INDEX, doc)
        return {'project_id': project['id'], 'last_task': project['last_task'],
                'ledger_events_written': 0}


def observe_stop(home: Path, path: Path, session: str, turn: str) -> dict:
    marker = runtime_marker(home, session, turn)
    if not marker.exists():
        return {'updated': False, 'reason': 'no matching turn baseline'}
    with file_lock(home / "runtime/turns"):
        if not marker.exists():
            return {'updated': False, 'reason': 'already handled'}
        baseline = json.loads(marker.read_text(encoding='utf-8'))
        root = identify_project(path)
        if not root or baseline.get('root') != str(root):
            return {'updated': False, 'reason': 'project changed or missing; baseline preserved'}
        before = baseline['before']; after = current_git(root)
        if before.get('available') and after.get('available') and before['fingerprint'] != after['fingerprint']:
            register(home, root, None)  # register only after a real Git change, not on read-only questions
            with file_lock(home / INDEX):
                index = read_index(home)
                project = find_project(index, root)
                if not project:
                    raise ValueError('cannot find registered project')
                previous = project.get('last_observed_change', {})
                committed_paths = []
                if before.get('head') and after.get('head') and before['head'] != after['head']:
                    diff = run_git(root, 'diff', '--name-only', '--no-renames', before['head'], after['head'])
                    if diff.returncode == 0:
                        committed_paths = os.fsdecode(diff.stdout).splitlines()[:200]
                observation = {'at': now_iso(), 'source': 'git-stop-hook',
                               'before_head': before.get('head'), 'after_head': after.get('head'),
                               'paths': sorted(set(before.get('paths', [])) | set(after.get('paths', [])) | set(committed_paths))[:200],
                               'before_fingerprint': before['fingerprint'],
                               'after_fingerprint': after['fingerprint'],
                               'dirty_count': after.get('dirty_count'),
                               'evidence': 'Git HEAD/status/file metadata; not semantic completion'}
                # Avoid writes if previous observation happened to be identical.
                if (previous.get('before_fingerprint'), previous.get('after_fingerprint')) != (observation['before_fingerprint'], observation['after_fingerprint']):
                    project['last_observed_change'] = observation
                    project['updated_at'] = now_iso()
                    atomic_json(home / INDEX, index)
                result = {'updated': True, 'observation': observation, 'ledger_events_written': 0}
        else:
            result = {'updated': False, 'reason': 'no verifiable Git change this turn'}
        marker.unlink(missing_ok=True)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home', type=Path, default=cognitive_home())
    commands = ap.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    start = commands.add_parser('begin')
    start.add_argument('--project', type=Path, default=Path.cwd())
    start.add_argument('--name')
    start.add_argument('--session')
    start.add_argument('--turn')
    start.add_argument('--readonly', action='store_true')
    peek = commands.add_parser('peek')
    peek.add_argument('--project', type=Path, default=Path.cwd())
    finish = commands.add_parser('complete')
    finish.add_argument('--project', type=Path, default=Path.cwd())
    finish.add_argument('--summary', required=True)
    finish.add_argument('--checkpoint')
    stop = commands.add_parser('observe-stop')
    stop.add_argument('--project', type=Path, default=Path.cwd())
    stop.add_argument('--session', required=True)
    stop.add_argument('--turn', required=True)
    args = ap.parse_args()
    home = args.home.expanduser().absolute()
    try:
        if args.command == 'init':
            result = {'home': str(home), 'initialized_now': init(home)}
        elif args.command in ('begin', 'peek'):
            result = begin(home, args.project, name=getattr(args, 'name', None),
                           session=getattr(args, 'session', None), turn=getattr(args, 'turn', None),
                           readonly=(args.command == 'peek' or getattr(args, 'readonly', False)))
        elif args.command == 'complete':
            result = complete(home, args.project, args.summary, args.checkpoint)
        else:
            result = observe_stop(home, args.project, args.session, args.turn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f'project_session: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
