"""Shared state I/O and validation. Python stdlib only; POSIX file locks.

Validation checks stored structure, not whether a user really confirmed a decision.
Unknown fields are retained. Legacy omissions are tolerated on reads, not invented.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

EVENT_TYPES = {"expansion_gate", "hypothesis", "task_close", "portfolio_review"}
EVENT_STATUSES = {"open", "active", "supported", "refuted", "inconclusive", "closed", "cancelled"}
PROJECT_STATUSES = {"active", "validating", "maintenance", "paused", "closed", "unknown"}
BASELINE_STATES = {
    "purpose_status": {"assistant_inferred", "user_explicit", "user_confirmed"},
    "boundary_status": {"draft", "user_explicit", "user_confirmed"},
    "design_status": {"draft", "proposal_presented", "user_explicit", "user_confirmed", "implementing", "validated"},
    "sample_status": {"pending", "accepted", "not_required"},
    "baseline_status": {"current", "stale", "superseded"},
}


def cognitive_home() -> Path:
    return Path(os.environ.get("COGNITIVE_HOME") or (Path.home() / ".cognitive-workbench")).expanduser()


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_iso(value: str) -> dt.datetime:
    if not nonempty(value):
        raise ValueError("timestamp must be a nonempty ISO string")
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)


@contextlib.contextmanager
def file_lock(path: Path):
    """Cooperating writers lock the complete read/check/write transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with lock.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def merge_dicts(base: dict, incoming: dict) -> dict:
    import copy
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate_baseline(value: Any, label: str = "planning baseline") -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    for key, choices in BASELINE_STATES.items():
        if key in value and (not isinstance(value[key], str) or value[key] not in choices):
            raise ValueError(f"invalid {label}.{key}")
    for key in ("baseline_source", "sample_not_required_reason"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"{label}.{key} must be a string, not null or an object")


def validate_index(value: Any) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("projects"), list):
        raise ValueError("project index must contain a projects list")
    version = value.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("index schema_version must be a positive integer")
    seen_ids, seen_roots = set(), set()
    for p in value["projects"]:
        if not isinstance(p, dict) or not all(nonempty(p.get(k)) for k in ("name", "root")):
            raise ValueError("each project requires nonempty name and root strings")
        if "id" in p:
            if not nonempty(p["id"]) or p["id"] in seen_ids:
                raise ValueError("invalid or duplicate project id")
            seen_ids.add(p["id"])
        if p["root"] in seen_roots:
            raise ValueError("duplicate project root")
        seen_roots.add(p["root"])
        if "status" in p and (not isinstance(p["status"], str) or p["status"] not in PROJECT_STATUSES):
            raise ValueError("invalid project status")
        for key in ("aliases", "artifacts", "run_entries", "thread_ids", "remote_urls", "upstream_projects", "downstream_projects", "product_docs", "source_of_truth"):
            if key in p and (not isinstance(p[key], list) or not all(isinstance(x, str) for x in p[key])):
                raise ValueError(f"project.{key} must be a list of strings")
        for key in ("planning_baseline", "interface_baseline"):
            if key in p:
                validate_baseline(p[key], key)
    return value


def validate_ledger_rows(rows: list[dict]) -> list[dict]:
    """Read v1 event/update rows without rewriting legacy omissions."""
    ids, updates = {}, []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"ledger row {n} must be an object")
        kind = row.get("record_kind")
        if kind == "event":
            if not nonempty(row.get("id")) or row["id"] in ids:
                raise ValueError(f"invalid or duplicate event id at row {n}")
            if not isinstance(row.get("type"), str) or row["type"] not in EVENT_TYPES:
                raise ValueError(f"unsupported event type at row {n}")
            if not nonempty(row.get("project")) or not nonempty(row.get("statement")):
                raise ValueError(f"event at row {n} requires project and statement")
            ids[row["id"]] = row
        elif kind == "update":
            if not nonempty(row.get("target_id")) or not isinstance(row.get("changes"), dict):
                raise ValueError(f"invalid update at row {n}")
            if set(row["changes"]) & {"id", "record_kind", "type", "project", "episode_id", "parent_id", "statement", "prediction"}:
                raise ValueError(f"update changes immutable event identity/prediction at row {n}")
            updates.append(row)
        else:
            raise ValueError(f"unknown record_kind at row {n}")
        data = row if kind == "event" else row["changes"]
        if "status" in data and (not isinstance(data["status"], str) or data["status"] not in EVENT_STATUSES):
            raise ValueError(f"invalid event status at row {n}")
        if "metadata" in data and not isinstance(data["metadata"], dict):
            raise ValueError(f"metadata must be an object at row {n}")
        for key in ("thread_id", "thread_unavailable_reason", "source", "outcome", "due_at", "review_trigger"):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                raise ValueError(f"{key} must be a string or null at row {n}")
        if data.get("due_at"):
            parse_iso(data["due_at"])
    for row in updates:
        if row["target_id"] not in ids:
            raise ValueError("ledger update points to missing event")
    # Parent linkage is checked when present; old rows without it remain valid.
    for row in ids.values():
        parent_id = row.get("parent_id")
        if parent_id:
            if not isinstance(parent_id, str) or parent_id not in ids:
                raise ValueError("event parent is missing")
            parent = ids[parent_id]
            if parent["project"].casefold() != row["project"].casefold():
                raise ValueError("event parent belongs to a different project")
            if row.get("episode_id") and row["episode_id"] != (parent.get("episode_id") or parent["id"]):
                raise ValueError("event episode conflicts with parent")
    return rows


def parse_ledger(text: str) -> list[dict]:
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on ledger line {n}: {exc.msg}") from exc
    return validate_ledger_rows(rows)
