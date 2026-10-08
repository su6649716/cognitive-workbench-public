#!/usr/bin/env python3
"""Append-only local ledger for cognitive-workbench events."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any


def cognitive_home() -> Path:
    """State base dir. Set COGNITIVE_HOME to run this skill outside Codex
    (e.g. Claude Code); defaults to the Codex home."""
    override = os.environ.get("COGNITIVE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


DEFAULT_LEDGER = cognitive_home() / "memories" / "COGNITIVE_EVENTS.jsonl"
EVENT_TYPES = {"expansion_gate", "hypothesis", "task_close", "portfolio_review"}
STATUSES = {"open", "active", "supported", "refuted", "inconclusive", "closed", "cancelled"}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(value: str) -> dt.datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed


def append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()


def load_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            rows.append(value)
    return rows


def merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge mappings without discarding earlier metadata."""
    merged = dict(base)
    for key, value in incoming.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_dicts(existing, value)
        else:
            merged[key] = value
    return merged


def apply_changes(event: dict[str, Any], changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if key == "metadata" and isinstance(value, dict):
            existing = event.get("metadata")
            event["metadata"] = merge_dicts(existing if isinstance(existing, dict) else {}, value)
        elif key == "notes_append":
            notes = event.setdefault("notes", [])
            if not isinstance(notes, list):
                notes = []
                event["notes"] = notes
            if isinstance(value, list):
                notes.extend(str(item) for item in value)
        else:
            event[key] = value


def fold_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    pending_updates: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        kind = row.get("record_kind")
        if kind == "event":
            event_id = str(row.get("id", ""))
            if not event_id:
                continue
            events[event_id] = dict(row)
            events[event_id].setdefault("episode_id", event_id)
            events[event_id].setdefault("parent_id", None)
            events[event_id].setdefault("notes", [])
            order.append(event_id)
            for update in pending_updates.pop(event_id, []):
                apply_changes(events[event_id], update.get("changes", {}))
                events[event_id]["updated_at"] = update.get("recorded_at")
        elif kind == "update":
            target_id = str(row.get("target_id", ""))
            if target_id in events:
                apply_changes(events[target_id], row.get("changes", {}))
                events[target_id]["updated_at"] = row.get("recorded_at")
            elif target_id:
                pending_updates.setdefault(target_id, []).append(row)

    return [events[event_id] for event_id in order if event_id in events]


def parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    return value


def same_episode(event: dict[str, Any], episode_id: str) -> bool:
    return str(event.get("episode_id") or event.get("id")) == episode_id


def guard_new_event(events: list[dict[str, Any]], args: argparse.Namespace) -> None:
    same_project_type = [
        event
        for event in events
        if str(event.get("project", "")).casefold() == args.project.casefold()
        and event.get("type") == args.type
    ]
    for event in reversed(same_project_type):
        if str(event.get("statement", "")).strip() == args.statement.strip():
            raise ValueError(f"duplicate event statement; update existing event {event.get('id')}")

    if args.episode_id:
        same_stage = [event for event in same_project_type if same_episode(event, args.episode_id)]
        if args.type in {"expansion_gate", "task_close", "portfolio_review"} and same_stage:
            raise ValueError(
                f"{args.type} already exists in this episode; update existing event "
                f"{same_stage[-1].get('id')}"
            )
        live_hypotheses = [
            event
            for event in same_stage
            if args.type == "hypothesis" and event.get("status") in {"open", "active"}
        ]
        if live_hypotheses:
            raise ValueError(
                "an open hypothesis already exists in this episode; update or resolve event "
                f"{live_hypotheses[-1].get('id')}"
            )

    if args.thread_id:
        same_thread = [
            event
            for event in same_project_type
            if str(event.get("thread_id") or "") == args.thread_id
        ]
        if args.type in {"expansion_gate", "task_close", "portfolio_review"} and same_thread:
            raise ValueError(
                "this workflow already exists for the task; update event "
                f"{same_thread[-1].get('id')}"
            )
        live_same_thread = [
            event for event in same_thread if event.get("status") in {"open", "active"}
        ]
        if live_same_thread:
            raise ValueError(
                "a live event of this workflow already exists for the task; update event "
                f"{live_same_thread[-1].get('id')}"
            )


def command_record(args: argparse.Namespace) -> int:
    if args.type not in EVENT_TYPES:
        raise ValueError(f"unsupported type: {args.type}")
    if args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")
    if args.due_at:
        parse_iso(args.due_at)

    if not args.thread_id and not args.thread_unavailable_reason:
        raise ValueError("record requires --thread-id or --thread-unavailable-reason")
    if args.type == "hypothesis" and not args.due_at and not args.review_trigger:
        raise ValueError("hypothesis requires --due-at or --review-trigger")
    if args.status == "closed" and not str(args.outcome or "").strip():
        raise ValueError("closed event requires --outcome")

    rows = load_lines(args.ledger)
    folded = fold_events(rows)
    existing_events = {event["id"]: event for event in folded}
    guard_new_event(folded, args)
    parent = None
    if args.parent_id:
        parent = existing_events.get(args.parent_id)
        if parent is None:
            raise ValueError(f"parent event not found: {args.parent_id}")

    recorded_at = now_iso()
    event_id = f"evt_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    episode_id = args.episode_id
    if not episode_id and parent:
        episode_id = str(parent.get("episode_id") or parent.get("id"))
    if not episode_id:
        episode_id = event_id
    payload = {
        "record_kind": "event",
        "id": event_id,
        "recorded_at": recorded_at,
        "type": args.type,
        "project": args.project,
        "thread_id": args.thread_id,
        "episode_id": episode_id,
        "parent_id": args.parent_id,
        "source": args.source,
        "statement": args.statement,
        "prediction": args.prediction,
        "due_at": args.due_at,
        "review_trigger": args.review_trigger,
        "status": args.status,
        "outcome": args.outcome,
        "metadata": parse_metadata(args.metadata_json),
        "notes": [],
        "thread_unavailable_reason": args.thread_unavailable_reason,
    }
    append_line(args.ledger, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_update(args: argparse.Namespace) -> int:
    rows = load_lines(args.ledger)
    events = {event.get("id"): event for event in fold_events(rows)}
    if args.id not in events:
        raise ValueError(f"event not found: {args.id}")
    if args.status and args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")
    if args.thread_id and args.thread_unavailable_reason:
        raise ValueError("use either --thread-id or --thread-unavailable-reason, not both")

    changes: dict[str, Any] = {}
    if args.status:
        changes["status"] = args.status
    if args.outcome is not None:
        changes["outcome"] = args.outcome
    if args.due_at is not None:
        if args.due_at:
            parse_iso(args.due_at)
        changes["due_at"] = args.due_at or None
    if args.review_trigger is not None:
        changes["review_trigger"] = args.review_trigger or None
    if args.thread_id is not None:
        changes["thread_id"] = args.thread_id or None
        changes["thread_unavailable_reason"] = None
    if args.thread_unavailable_reason is not None:
        changes["thread_unavailable_reason"] = args.thread_unavailable_reason or None
    if args.metadata_json:
        changes["metadata"] = parse_metadata(args.metadata_json)
    if args.note:
        changes["notes_append"] = [args.note]
    if not changes:
        raise ValueError("update requires at least one change")

    candidate = dict(events[args.id])
    apply_changes(candidate, changes)
    if (
        candidate.get("type") == "hypothesis"
        and candidate.get("status") in {"open", "active"}
        and not candidate.get("due_at")
        and not candidate.get("review_trigger")
    ):
        raise ValueError("open hypothesis requires due_at or review_trigger")
    if (
        (args.status == "closed" or args.outcome is not None)
        and candidate.get("status") == "closed"
        and not str(candidate.get("outcome") or "").strip()
    ):
        raise ValueError("closed event requires outcome")

    payload = {
        "record_kind": "update",
        "target_id": args.id,
        "recorded_at": now_iso(),
        "changes": changes,
    }
    append_line(args.ledger, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_list(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        raise ValueError("limit must be greater than zero")
    events = fold_events(load_lines(args.ledger))
    if args.type:
        events = [event for event in events if event.get("type") == args.type]
    if args.status:
        events = [event for event in events if event.get("status") == args.status]
    if args.project:
        needle = args.project.casefold()
        events = [event for event in events if needle in str(event.get("project", "")).casefold()]
    if args.thread_id:
        events = [event for event in events if str(event.get("thread_id") or "") == args.thread_id]
    if args.episode_id:
        events = [event for event in events if str(event.get("episode_id") or event.get("id")) == args.episode_id]
    if args.due_before:
        cutoff = parse_iso(args.due_before)
        filtered = []
        for event in events:
            due_at = event.get("due_at")
            if due_at and parse_iso(str(due_at)) <= cutoff:
                filtered.append(event)
        events = filtered
    events = events[-args.limit :]
    print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


def command_audit(args: argparse.Namespace) -> int:
    events = fold_events(load_lines(args.ledger))
    duplicate_workflows: list[dict[str, Any]] = []
    acknowledged_legacy_duplicates: list[dict[str, str]] = []
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for event in events:
        if event.get("type") not in {"expansion_gate", "task_close", "portfolio_review"}:
            continue
        migration = event.get("metadata", {}).get("lifecycle_migration", {})
        legacy_target = migration.get("legacy_duplicate_of") if isinstance(migration, dict) else None
        if legacy_target:
            acknowledged_legacy_duplicates.append(
                {"event_id": str(event.get("id")), "canonical_event_id": str(legacy_target)}
            )
            continue
        key = (
            str(event.get("project", "")).casefold(),
            str(event.get("episode_id") or event.get("id")),
            str(event.get("type")),
        )
        grouped.setdefault(key, []).append(str(event.get("id")))
    for (project, episode_id, event_type), ids in grouped.items():
        if len(ids) > 1:
            duplicate_workflows.append(
                {
                    "project": project,
                    "episode_id": episode_id,
                    "type": event_type,
                    "event_ids": ids,
                }
            )

    missing_thread = [
        str(event.get("id"))
        for event in events
        if not event.get("thread_id") and not event.get("thread_unavailable_reason")
    ]
    missing_review = [
        str(event.get("id"))
        for event in events
        if event.get("type") == "hypothesis"
        and event.get("status") in {"open", "active"}
        and not event.get("due_at")
        and not event.get("review_trigger")
    ]
    closed_without_outcome: list[str] = []
    acknowledged_legacy_outcomes: list[str] = []
    for event in events:
        if event.get("status") != "closed" or str(event.get("outcome") or "").strip():
            continue
        migration = event.get("metadata", {}).get("lifecycle_migration", {})
        legacy_reason = (
            migration.get("legacy_missing_outcome_reason")
            if isinstance(migration, dict)
            else None
        )
        if legacy_reason:
            acknowledged_legacy_outcomes.append(str(event.get("id")))
        else:
            closed_without_outcome.append(str(event.get("id")))
    due_now = dt.datetime.now().astimezone()
    overdue = [
        str(event.get("id"))
        for event in events
        if event.get("status") in {"open", "active"}
        and event.get("due_at")
        and parse_iso(str(event.get("due_at"))) <= due_now
    ]
    payload = {
        "event_count": len(events),
        "missing_task_link": missing_thread,
        "open_hypothesis_missing_review_point": missing_review,
        "closed_events_missing_outcome": closed_without_outcome,
        "acknowledged_legacy_missing_outcome": acknowledged_legacy_outcomes,
        "duplicate_workflows_in_episode": duplicate_workflows,
        "acknowledged_legacy_duplicates": acknowledged_legacy_duplicates,
        "overdue_open_events": overdue,
        "healthy": not (
            missing_thread
            or missing_review
            or closed_without_outcome
            or duplicate_workflows
            or overdue
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["healthy"] else 1


def command_due(args: argparse.Namespace) -> int:
    """List hypotheses needing review now: overdue by due_at, plus
    open/active ones waiting on a review_trigger (human judgment)."""
    events = fold_events(load_lines(args.ledger))
    due_now = dt.datetime.now().astimezone()
    overdue: list[dict[str, str]] = []
    watch: list[dict[str, str]] = []
    for event in events:
        if event.get("type") != "hypothesis":
            continue
        if event.get("status") not in {"open", "active"}:
            continue
        brief = {
            "id": str(event.get("id")),
            "project": str(event.get("project", "")),
            "statement": str(event.get("statement", ""))[:60],
            "status": str(event.get("status")),
        }
        due_at = event.get("due_at")
        if due_at and parse_iso(str(due_at)) <= due_now:
            brief["due_at"] = str(due_at)
            overdue.append(brief)
        elif event.get("review_trigger"):
            brief["review_trigger"] = str(event.get("review_trigger"))
            watch.append(brief)
    print(json.dumps({"overdue": overdue, "watch": watch}, ensure_ascii=False, indent=2))
    return 0 if not overdue else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="append a new event")
    record.add_argument("--type", required=True)
    record.add_argument("--project", required=True)
    record.add_argument("--thread-id", default=None)
    record.add_argument("--thread-unavailable-reason", default=None)
    record.add_argument("--episode-id", default=None)
    record.add_argument("--parent-id", default=None)
    record.add_argument("--source", required=True)
    record.add_argument("--statement", required=True)
    record.add_argument("--prediction", default=None)
    record.add_argument("--due-at", default=None)
    record.add_argument("--review-trigger", default=None)
    record.add_argument("--status", default="open")
    record.add_argument("--outcome", default=None)
    record.add_argument("--metadata-json", default=None)
    record.set_defaults(handler=command_record)

    update = subparsers.add_parser("update", help="append an event update")
    update.add_argument("--id", required=True)
    update.add_argument("--status", default=None)
    update.add_argument("--outcome", default=None)
    update.add_argument("--due-at", default=None)
    update.add_argument("--review-trigger", default=None)
    update.add_argument("--thread-id", default=None)
    update.add_argument("--thread-unavailable-reason", default=None)
    update.add_argument("--metadata-json", default=None)
    update.add_argument("--note", default=None)
    update.set_defaults(handler=command_update)

    listing = subparsers.add_parser("list", help="list folded current events")
    listing.add_argument("--type", default=None)
    listing.add_argument("--status", default=None)
    listing.add_argument("--project", default=None)
    listing.add_argument("--thread-id", default=None)
    listing.add_argument("--episode-id", default=None)
    listing.add_argument("--due-before", default=None)
    listing.add_argument("--limit", type=int, default=100)
    listing.set_defaults(handler=command_list)

    audit = subparsers.add_parser("audit", help="report linkage, review and duplication gaps")
    audit.set_defaults(handler=command_audit)

    due = subparsers.add_parser("due", help="list hypotheses needing review now")
    due.set_defaults(handler=command_due)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
