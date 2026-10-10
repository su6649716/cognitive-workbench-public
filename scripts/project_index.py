#!/usr/bin/env python3
"""Maintain project identity and user-owned planning baselines; original CLI retained."""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from pathlib import Path
from _state_common import (cognitive_home, now_iso, file_lock, atomic_json, merge_dicts,
                           validate_index, validate_baseline, nonempty, PROJECT_STATUSES)

STATUSES = PROJECT_STATUSES
DEFAULT_INDEX = cognitive_home() / "memories" / "PROJECT_INDEX.json"
PURPOSE_READY = {"user_explicit", "user_confirmed"}
CONFIRMATION_READY = {"user_explicit", "user_confirmed", "implementing", "validated"}
SAMPLE_READY = {"accepted", "not_required"}
LIST_FIELDS = {"aliases": "alias", "artifacts": "artifact", "run_entries": "run_entry",
               "remote_urls": "remote_url", "upstream_projects": "upstream_project",
               "downstream_projects": "downstream_project", "product_docs": "product_doc",
               "source_of_truth": "source_of_truth"}


def load_index(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 3, "projects": []}
    value = validate_index(json.loads(path.read_text(encoding="utf-8")))
    value.setdefault("schema_version", 1)
    return value

# Preserve names used by existing callers.
atomic_write = atomic_json
index_lock = file_lock


def unique(values: list[str]) -> list[str]:
    result, seen = [], set()
    for raw in values:
        value = raw.strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold()); result.append(value)
    return result


def resolve_existing(value: str, label: str, *, directory: bool = False) -> str:
    path = Path(value).expanduser().resolve()
    if not path.exists() or (directory and not path.is_dir()):
        raise ValueError(f"{label} does not exist or has wrong type: {path}")
    return str(path)


def parse_json_object(raw: str, label: str) -> dict:
    value = json.loads(raw)
    validate_baseline(value, label)
    return value


def matches(project: dict, query: str) -> bool:
    keys = ("name", "root", "aliases", "artifacts", "run_entries", "system_name", "component_role",
            "upstream_projects", "downstream_projects", "product_docs", "source_of_truth",
            "planning_baseline", "interface_baseline")
    return any(query.casefold() in json.dumps(project.get(k, ""), ensure_ascii=False).casefold() for k in keys)


def find_one(projects: list[dict], query: str) -> dict:
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("project query is empty")
    exact = [p for p in projects if needle in {str(p.get("id", "")).casefold(), p["name"].casefold(), p["root"].casefold(), *[a.casefold() for a in p.get("aliases", [])]}]
    candidates = exact or [p for p in projects if matches(p, query)]
    if len(candidates) != 1:
        raise ValueError("project not found" if not candidates else "project query is ambiguous")
    return candidates[0]


def command_upsert(args: argparse.Namespace) -> int:
    if not nonempty(args.name):
        raise ValueError("project name cannot be empty")
    if args.status is not None and args.status not in STATUSES:
        raise ValueError("unsupported status")
    root = resolve_existing(args.root, "project root", directory=True)
    args.artifact = [resolve_existing(x, "artifact") for x in args.artifact]
    args.product_doc = [resolve_existing(x, "product document") for x in args.product_doc]
    planning = parse_json_object(args.planning_baseline_json, "planning baseline") if args.planning_baseline_json is not None else None
    interface = parse_json_object(args.interface_baseline_json, "interface baseline") if args.interface_baseline_json is not None else None
    with file_lock(args.index):
        data = load_index(args.index)
        projects = data["projects"]
        candidates = [p for p in projects if p["root"] == root]
        if not candidates:
            candidates = [p for p in projects if args.name.casefold() in {p["name"].casefold(), *[x.casefold() for x in p.get("aliases", [])]}]
        if len(candidates) > 1:
            raise ValueError("project identity is ambiguous")
        timestamp = now_iso()
        if candidates:
            project = candidates[0]
        else:
            project = {"id": f"prj_{uuid.uuid4().hex[:12]}", "created_at": timestamp, "status": "unknown", "thread_ids": []}
            projects.append(project)
        previous = project.get("name", "")
        project.update(name=args.name.strip(), root=root, updated_at=timestamp)
        if args.status is not None:
            project["status"] = args.status
        project.setdefault("status", "unknown")
        for key, attr in LIST_FIELDS.items():
            values = [*project.get(key, []), *getattr(args, attr)]
            if key == "aliases":
                values.append(previous)
            project[key] = unique(values)
        project["aliases"] = [a for a in project["aliases"] if a.casefold() != args.name.casefold()]
        project["thread_ids"] = unique([*project.get("thread_ids", []), *([args.thread_id] if args.thread_id else [])])
        for key in ("system_name", "component_role"):
            if getattr(args, key) is not None:
                project[key] = getattr(args, key).strip()
        if args.checkpoint is not None:
            project["last_checkpoint"] = args.checkpoint
        if planning is not None:
            project["planning_baseline"] = merge_dicts(project.get("planning_baseline", {}), planning) if args.merge_baseline else planning
        if interface is not None:
            project["interface_baseline"] = merge_dicts(project.get("interface_baseline", {}), interface) if args.merge_baseline else interface
        data["schema_version"] = max(data.get("schema_version", 1), 3)
        validate_index(data)
        atomic_json(args.index, data)
    print(json.dumps(project, ensure_ascii=False, indent=2))
    return 0


def command_find(args: argparse.Namespace) -> int:
    result = [p for p in load_index(args.index)["projects"] if matches(p, args.query)]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result else 1


def command_list(args: argparse.Namespace) -> int:
    result = load_index(args.index)["projects"]
    if args.status:
        result = [p for p in result if p.get("status") == args.status]
    print(json.dumps(sorted(result, key=lambda p: p.get("updated_at", ""), reverse=True), ensure_ascii=False, indent=2))
    return 0


def command_system(args: argparse.Namespace) -> int:
    result = [p for p in load_index(args.index)["projects"] if str(p.get("system_name", "")).casefold() == args.name.casefold()]
    print(json.dumps({"system_name": args.name, "components": sorted(result, key=lambda p: p["name"].casefold())}, ensure_ascii=False, indent=2))
    return 0 if result else 1


def command_gate(args: argparse.Namespace) -> int:
    project = find_one(load_index(args.index)["projects"], args.query)
    planning = project.get("planning_baseline", {})
    statuses = {k: planning.get(k, "missing") for k in ("purpose_status", "boundary_status", "design_status", "sample_status", "baseline_status", "baseline_source")}
    reasons = []
    if args.risk != "low":
        if statuses["baseline_status"] != "current":
            reasons.append("planning baseline is missing, stale or superseded")
        if not nonempty(planning.get("baseline_source")):
            reasons.append("planning baseline has no traceable source")
        if statuses["purpose_status"] not in PURPOSE_READY:
            reasons.append("purpose is not explicit or confirmed by the user")
        if statuses["boundary_status"] not in PURPOSE_READY:
            reasons.append("component responsibility and boundary are not user-confirmed")
        if statuses["design_status"] not in CONFIRMATION_READY:
            reasons.append("recommended form and acceptance criteria are not user-confirmed")
    if args.target_stage == "full" and args.user_facing and args.risk == "high":
        if statuses["sample_status"] not in SAMPLE_READY:
            reasons.append("representative sample is not accepted")
        elif statuses["sample_status"] == "not_required" and not nonempty(planning.get("sample_not_required_reason")):
            reasons.append("sample is marked not_required without a reason")
    result = {"allowed": not reasons, "project": {k: project.get(k) for k in ("id", "name", "root")},
              "risk": args.risk, "target_stage": args.target_stage, "user_facing": args.user_facing,
              "statuses": statuses, "reasons": reasons,
              "next_action": "proceed within the confirmed scope" if not reasons else "return to affected collaborative definition; update the existing baseline after confirmation"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["allowed"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    s = p.add_subparsers(dest="command", required=True)
    up = s.add_parser("upsert")
    up.add_argument("--name", required=True); up.add_argument("--root", required=True)
    for flag in LIST_FIELDS.values():
        up.add_argument("--" + flag.replace("_", "-"), action="append", default=[])
    for flag in ("thread-id", "system-name", "component-role", "checkpoint", "planning-baseline-json", "interface-baseline-json"):
        up.add_argument("--" + flag, default=None)
    up.add_argument("--merge-baseline", action="store_true", help="merge supplied baseline fields; default preserves the original whole-object replacement")
    up.add_argument("--status", choices=sorted(STATUSES), default=None)
    up.set_defaults(handler=command_upsert)
    finding = s.add_parser("find"); finding.add_argument("--query", required=True); finding.set_defaults(handler=command_find)
    listing = s.add_parser("list"); listing.add_argument("--status", choices=sorted(STATUSES)); listing.set_defaults(handler=command_list)
    system = s.add_parser("system"); system.add_argument("--name", required=True); system.set_defaults(handler=command_system)
    gate = s.add_parser("gate"); gate.add_argument("--query", required=True)
    gate.add_argument("--risk", choices=("low", "medium", "high"), required=True)
    gate.add_argument("--target-stage", choices=("sample", "full"), required=True)
    gate.add_argument("--user-facing", action="store_true"); gate.set_defaults(handler=command_gate)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
