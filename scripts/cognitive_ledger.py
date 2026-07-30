#!/usr/bin/env python3
"""Append-only local ledger for cognitive-workbench events."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any


DEFAULT_LEDGER = Path.home() / ".codex" / "memories" / "COGNITIVE_EVENTS.jsonl"
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


def command_record(args: argparse.Namespace) -> int:
    if args.type not in EVENT_TYPES:
        raise ValueError(f"unsupported type: {args.type}")
    if args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")
    if args.due_at:
        parse_iso(args.due_at)

    rows = load_lines(args.ledger)
    existing_events = {event["id"]: event for event in fold_events(rows)}
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
        "status": args.status,
        "outcome": None,
        "metadata": parse_metadata(args.metadata_json),
        "notes": [],
    }
    append_line(args.ledger, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_update(args: argparse.Namespace) -> int:
    rows = load_lines(args.ledger)
    known_ids = {row.get("id") for row in rows if row.get("record_kind") == "event"}
    if args.id not in known_ids:
        raise ValueError(f"event not found: {args.id}")
    if args.status and args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")

    changes: dict[str, Any] = {}
    if args.status:
        changes["status"] = args.status
    if args.outcome is not None:
        changes["outcome"] = args.outcome
    if args.due_at is not None:
        if args.due_at:
            parse_iso(args.due_at)
        changes["due_at"] = args.due_at or None
    if args.metadata_json:
        changes["metadata"] = parse_metadata(args.metadata_json)
    if args.note:
        changes["notes_append"] = [args.note]
    if not changes:
        raise ValueError("update requires at least one change")

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="append a new event")
    record.add_argument("--type", required=True)
    record.add_argument("--project", required=True)
    record.add_argument("--thread-id", default=None)
    record.add_argument("--episode-id", default=None)
    record.add_argument("--parent-id", default=None)
    record.add_argument("--source", required=True)
    record.add_argument("--statement", required=True)
    record.add_argument("--prediction", default=None)
    record.add_argument("--due-at", default=None)
    record.add_argument("--status", default="open")
    record.add_argument("--metadata-json", default=None)
    record.set_defaults(handler=command_record)

    update = subparsers.add_parser("update", help="append an event update")
    update.add_argument("--id", required=True)
    update.add_argument("--status", default=None)
    update.add_argument("--outcome", default=None)
    update.add_argument("--due-at", default=None)
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
