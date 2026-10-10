#!/usr/bin/env python3
"""Append-only cognitive events; compatible CLI, scoped review and locked writes."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from _state_common import (cognitive_home, now_iso, parse_iso, nonempty, file_lock,
                           merge_dicts, parse_ledger, EVENT_TYPES, EVENT_STATUSES)

DEFAULT_LEDGER = cognitive_home() / "memories" / "COGNITIVE_EVENTS.jsonl"
STATUSES = EVENT_STATUSES


def append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_lines(path: Path) -> list[dict]:
    return parse_ledger(path.read_text(encoding="utf-8")) if path.exists() else []


def apply_changes(event: dict, changes: dict) -> None:
    for key, value in changes.items():
        if key == "metadata" and isinstance(value, dict):
            event[key] = merge_dicts(event.get(key) or {}, value)
        elif key == "notes_append":
            event.setdefault("notes", []).extend(str(x) for x in value)
        else:
            event[key] = value


def fold_events(rows: list[dict]) -> list[dict]:
    import copy
    events, order, pending = {}, [], {}
    for row in rows:
        if row.get("record_kind") == "event":
            eid = row["id"]
            event = copy.deepcopy(row)
            event.setdefault("episode_id", eid)
            event.setdefault("parent_id", None)
            event.setdefault("notes", [])
            events[eid] = event
            order.append(eid)
            for update in pending.pop(eid, []):
                apply_changes(event, update["changes"])
                event["updated_at"] = update.get("recorded_at")
        elif row.get("record_kind") == "update":
            eid = row["target_id"]
            if eid not in events:
                pending.setdefault(eid, []).append(row)
            else:
                apply_changes(events[eid], row["changes"])
                events[eid]["updated_at"] = row.get("recorded_at")
    return [events[eid] for eid in order]


def parse_metadata(raw: str | None) -> dict:
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    return value


def same_episode(event: dict, eid: str) -> bool:
    return (event.get("episode_id") or event["id"]) == eid


def guard_new_event(events: list[dict], args: argparse.Namespace) -> None:
    candidates = [e for e in events if e["project"].casefold() == args.project.casefold() and e["type"] == args.type]
    stage = [e for e in candidates if same_episode(e, args.episode_id)]
    if stage and args.type in {"expansion_gate", "task_close", "portfolio_review"}:
        raise ValueError(f"workflow already exists in this episode; update existing event {stage[-1]['id']}")
    if any(e.get("status") in {"open", "active"} for e in stage) and args.type == "hypothesis":
        raise ValueError("an open hypothesis exists in this episode; update or resolve it first")
    if any(e["statement"].strip() == args.statement.strip() for e in stage):
        raise ValueError("duplicate statement in this episode; update existing event")
    # A thread may contain different questions. Without an explicit episode, an
    # exact repeat in the same task is still caught instead of spawning duplicates.
    if not args.explicit_episode and args.thread_id:
        for e in candidates:
            if e.get("thread_id") == args.thread_id and e["statement"].strip() == args.statement.strip():
                raise ValueError(f"duplicate statement in task; update existing event {e['id']}")


def command_record(args: argparse.Namespace) -> int:
    if args.type not in EVENT_TYPES or args.status not in STATUSES:
        raise ValueError("unsupported event type or status")
    if not all(nonempty(x) for x in (args.project, args.statement, args.source)):
        raise ValueError("project, statement and source must be nonempty")
    if bool(nonempty(args.thread_id)) == bool(nonempty(args.thread_unavailable_reason)):
        raise ValueError("record requires --thread-id or --thread-unavailable-reason, not both")
    if args.type == "hypothesis" and not args.due_at and not nonempty(args.review_trigger):
        raise ValueError("hypothesis requires --due-at or --review-trigger")
    if args.due_at:
        parse_iso(args.due_at)
    if args.status == "closed" and not nonempty(args.outcome):
        raise ValueError("closed event requires --outcome")
    metadata = parse_metadata(args.metadata_json)
    with file_lock(args.ledger):
        events = fold_events(load_lines(args.ledger))
        by_id = {e["id"]: e for e in events}
        event_id = f"evt_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        args.explicit_episode = bool(args.episode_id or args.parent_id)
        if args.parent_id:
            parent = by_id.get(args.parent_id)
            if not parent:
                raise ValueError("parent event not found")
            if parent["project"].casefold() != args.project.casefold():
                raise ValueError("parent belongs to a different project")
            inherited = parent.get("episode_id") or parent["id"]
            if args.episode_id and args.episode_id != inherited:
                raise ValueError("episode conflicts with parent")
            args.episode_id = inherited
        elif args.episode_id:
            foreign = [e for e in events if same_episode(e, args.episode_id) and e["project"].casefold() != args.project.casefold()]
            if foreign:
                raise ValueError("episode belongs to a different project")
        args.episode_id = args.episode_id or event_id
        guard_new_event(events, args)
        payload = {"record_kind": "event", "id": event_id, "recorded_at": now_iso(),
                   "type": args.type, "project": args.project, "thread_id": args.thread_id,
                   "episode_id": args.episode_id, "parent_id": args.parent_id,
                   "source": args.source, "statement": args.statement, "prediction": args.prediction,
                   "due_at": args.due_at, "review_trigger": args.review_trigger,
                   "status": args.status, "outcome": args.outcome, "metadata": metadata,
                   "notes": [], "thread_unavailable_reason": args.thread_unavailable_reason}
        append_line(args.ledger, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_update(args: argparse.Namespace) -> int:
    if args.status and args.status not in STATUSES:
        raise ValueError("unsupported status")
    if args.thread_id is not None and args.thread_unavailable_reason is not None:
        raise ValueError("use either thread id or unavailable reason")
    changes = {}
    for key in ("status", "outcome", "due_at", "review_trigger", "thread_id", "thread_unavailable_reason"):
        val = getattr(args, key)
        if val is not None:
            changes[key] = val or None
    if args.due_at:
        parse_iso(args.due_at)
    if args.thread_id is not None:
        if not nonempty(args.thread_id):
            raise ValueError("use --thread-unavailable-reason rather than clearing the task link")
        changes["thread_unavailable_reason"] = None
    if args.thread_unavailable_reason is not None:
        if not nonempty(args.thread_unavailable_reason):
            raise ValueError("thread unavailable reason cannot be empty")
        changes["thread_id"] = None
    if args.metadata_json:
        changes["metadata"] = parse_metadata(args.metadata_json)
    if args.note:
        changes["notes_append"] = [args.note]
    if not changes:
        raise ValueError("update requires at least one change")
    with file_lock(args.ledger):
        events = {e["id"]: e for e in fold_events(load_lines(args.ledger))}
        if args.id not in events:
            raise ValueError("event not found")
        candidate = merge_dicts({}, events[args.id])
        apply_changes(candidate, changes)
        if candidate["type"] == "hypothesis" and candidate.get("status") in {"open", "active"} and not candidate.get("due_at") and not nonempty(candidate.get("review_trigger")):
            raise ValueError("open hypothesis requires due_at or review_trigger")
        if candidate.get("status") == "closed" and not nonempty(candidate.get("outcome")):
            raise ValueError("closed event requires outcome")
        payload = {"record_kind": "update", "target_id": args.id, "recorded_at": now_iso(), "changes": changes}
        append_line(args.ledger, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def scoped(events: list[dict], args: argparse.Namespace, substring: bool = False) -> list[dict]:
    result = events
    for key in ("type", "status", "thread_id"):
        value = getattr(args, key, None)
        if value:
            result = [e for e in result if e.get(key) == value]
    if getattr(args, "project", None):
        q = args.project.casefold()
        result = [e for e in result if (q in e["project"].casefold() if substring else q == e["project"].casefold())]
    if getattr(args, "episode_id", None):
        result = [e for e in result if same_episode(e, args.episode_id)]
    return result


def command_list(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        raise ValueError("limit must be greater than zero")
    events = scoped(fold_events(load_lines(args.ledger)), args, substring=True)
    if args.due_before:
        cutoff = parse_iso(args.due_before)
        events = [e for e in events if e.get("due_at") and parse_iso(e["due_at"]) <= cutoff]
    print(json.dumps(events[-args.limit:], ensure_ascii=False, indent=2))
    return 0


def command_audit(args: argparse.Namespace) -> int:
    events = scoped(fold_events(load_lines(args.ledger)), args)
    grouped, legacy = {}, []
    for e in events:
        if e["type"] not in {"expansion_gate", "task_close", "portfolio_review"}:
            continue
        migration = e.get("metadata", {}).get("lifecycle_migration", {})
        target = migration.get("legacy_duplicate_of") if isinstance(migration, dict) else None
        if target:
            legacy.append({"event_id": e["id"], "canonical_event_id": target})
            continue
        key = (e["project"].casefold(), e.get("episode_id") or e["id"], e["type"])
        grouped.setdefault(key, []).append(e["id"])
    duplicates = [{"project": p, "episode_id": ep, "type": t, "event_ids": ids} for (p, ep, t), ids in grouped.items() if len(ids) > 1]
    missing_task = [e["id"] for e in events if not e.get("thread_id") and not e.get("thread_unavailable_reason")]
    missing_review = [e["id"] for e in events if e["type"] == "hypothesis" and e.get("status") in {"open", "active"} and not e.get("due_at") and not e.get("review_trigger")]
    missing_outcome, legacy_outcome = [], []
    for e in events:
        if e.get("status") != "closed" or nonempty(e.get("outcome")):
            continue
        migration = e.get("metadata", {}).get("lifecycle_migration", {})
        acknowledged = isinstance(migration, dict) and migration.get("legacy_missing_outcome_reason")
        (legacy_outcome if acknowledged else missing_outcome).append(e["id"])
    now = dt.datetime.now().astimezone()
    overdue = [e["id"] for e in events if e.get("status") in {"open", "active"} and e.get("due_at") and parse_iso(e["due_at"]) <= now]
    structural = not (missing_task or missing_review or missing_outcome or duplicates)
    payload = {"event_count": len(events), "missing_task_link": missing_task,
               "open_hypothesis_missing_review_point": missing_review,
               "closed_events_missing_outcome": missing_outcome,
               "acknowledged_legacy_missing_outcome": legacy_outcome,
               "duplicate_workflows_in_episode": duplicates, "acknowledged_legacy_duplicates": legacy,
               "overdue_open_events": overdue, "healthy": structural and not overdue,
               "structural_healthy": structural, "review_needed": bool(overdue),
               "scope": {"project": args.project, "episode_id": args.episode_id}}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["healthy"] else 1


def command_due(args: argparse.Namespace) -> int:
    events = scoped(fold_events(load_lines(args.ledger)), args)
    overdue, watch = [], []
    now = dt.datetime.now().astimezone()
    for e in events:
        if e["type"] != "hypothesis" or e.get("status") not in {"open", "active"}:
            continue
        brief = {k: e.get(k) for k in ("id", "project", "statement", "status")}
        brief["statement"] = brief["statement"][:60]
        if e.get("due_at") and parse_iso(e["due_at"]) <= now:
            brief["due_at"] = e["due_at"]
            overdue.append(brief)
        elif e.get("review_trigger"):
            brief["review_trigger"] = e["review_trigger"]
            watch.append(brief)
    print(json.dumps({"overdue": overdue, "watch": watch}, ensure_ascii=False, indent=2))
    return 0 if not overdue else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subs = p.add_subparsers(dest="command", required=True)
    rec = subs.add_parser("record")
    for flag in ("type", "project", "source", "statement"):
        rec.add_argument("--" + flag, required=True)
    for flag in ("thread-id", "thread-unavailable-reason", "episode-id", "parent-id", "prediction", "due-at", "review-trigger", "outcome", "metadata-json"):
        rec.add_argument("--" + flag, default=None)
    rec.add_argument("--status", default="open")
    rec.set_defaults(handler=command_record)
    up = subs.add_parser("update")
    up.add_argument("--id", required=True)
    for flag in ("status", "outcome", "due-at", "review-trigger", "thread-id", "thread-unavailable-reason", "metadata-json", "note"):
        up.add_argument("--" + flag, default=None)
    up.set_defaults(handler=command_update)
    listing = subs.add_parser("list")
    for flag in ("type", "status", "project", "thread-id", "episode-id", "due-before"):
        listing.add_argument("--" + flag, default=None)
    listing.add_argument("--limit", type=int, default=100)
    listing.set_defaults(handler=command_list)
    for name, func in (("audit", command_audit), ("due", command_due)):
        sp = subs.add_parser(name)
        for flag in ("project", "episode-id", "thread-id"):
            sp.add_argument("--" + flag, default=None)
        sp.set_defaults(handler=func)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
