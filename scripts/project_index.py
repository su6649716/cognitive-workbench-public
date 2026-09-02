#!/usr/bin/env python3
"""Maintain a small, evidence-based index of local projects and their entry points."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


DEFAULT_INDEX = Path.home() / ".codex" / "memories" / "PROJECT_INDEX.json"
STATUSES = {"active", "validating", "maintenance", "paused", "closed", "unknown"}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 3, "projects": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("projects"), list):
        raise ValueError("project index must contain a projects list")
    value.setdefault("schema_version", 1)
    return value


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


@contextlib.contextmanager
def index_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def parse_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def resolve_existing(value: str, label: str, *, directory: bool = False) -> str:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    if directory and not path.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")
    return str(path)


def matches(project: dict[str, Any], query: str) -> bool:
    needle = query.casefold()
    fields = [
        str(project.get("name", "")),
        str(project.get("root", "")),
        *[str(value) for value in project.get("aliases", [])],
        *[str(value) for value in project.get("artifacts", [])],
        *[str(value) for value in project.get("run_entries", [])],
        str(project.get("system_name", "")),
        str(project.get("component_role", "")),
        *[str(value) for value in project.get("upstream_projects", [])],
        *[str(value) for value in project.get("downstream_projects", [])],
        *[str(value) for value in project.get("product_docs", [])],
        *[str(value) for value in project.get("source_of_truth", [])],
        json.dumps(project.get("planning_baseline", {}), ensure_ascii=False),
        json.dumps(project.get("interface_baseline", {}), ensure_ascii=False),
    ]
    return any(needle in field.casefold() for field in fields)


def command_upsert(args: argparse.Namespace) -> int:
    if args.status not in STATUSES:
        raise ValueError(f"unsupported status: {args.status}")
    root = resolve_existing(args.root, "project root", directory=True)
    artifacts = [resolve_existing(value, "artifact") for value in args.artifact]
    product_docs = [resolve_existing(value, "product document") for value in args.product_doc]
    run_entries = unique(args.run_entry)
    with index_lock(args.index):
        payload = load_index(args.index)
        payload["schema_version"] = max(int(payload.get("schema_version", 1)), 3)
        projects: list[dict[str, Any]] = payload["projects"]

        candidates = [project for project in projects if str(project.get("root")) == root]
        if not candidates:
            candidates = [
                project
                for project in projects
                if args.name.casefold()
                in {
                    str(project.get("name", "")).casefold(),
                    *[str(value).casefold() for value in project.get("aliases", [])],
                }
            ]
        if len(candidates) > 1:
            raise ValueError("project identity is ambiguous; resolve duplicate names before updating")

        timestamp = now_iso()
        if candidates:
            project = candidates[0]
        else:
            project = {
                "id": f"prj_{uuid.uuid4().hex[:12]}",
                "created_at": timestamp,
                "aliases": [],
                "artifacts": [],
                "run_entries": [],
                "thread_ids": [],
                "remote_urls": [],
                "upstream_projects": [],
                "downstream_projects": [],
                "product_docs": [],
                "source_of_truth": [],
            }
            projects.append(project)

        previous_name = str(project.get("name", "")).strip()
        project.update(
            {
                "name": args.name.strip(),
                "root": root,
                "status": args.status,
                "updated_at": timestamp,
            }
        )
        project["aliases"] = unique(
            [*project.get("aliases", []), previous_name, *args.alias]
        )
        project["aliases"] = [
            value for value in project["aliases"] if value.casefold() != args.name.casefold()
        ]
        project["artifacts"] = unique([*project.get("artifacts", []), *artifacts])
        project["run_entries"] = unique([*project.get("run_entries", []), *run_entries])
        project["thread_ids"] = unique(
            [*project.get("thread_ids", []), *([args.thread_id] if args.thread_id else [])]
        )
        project["remote_urls"] = unique(
            [*project.get("remote_urls", []), *args.remote_url]
        )
        if args.system_name:
            project["system_name"] = args.system_name.strip()
        if args.component_role:
            project["component_role"] = args.component_role.strip()
        project["upstream_projects"] = unique(
            [*project.get("upstream_projects", []), *args.upstream_project]
        )
        project["downstream_projects"] = unique(
            [*project.get("downstream_projects", []), *args.downstream_project]
        )
        project["product_docs"] = unique(
            [*project.get("product_docs", []), *product_docs]
        )
        project["source_of_truth"] = unique(
            [*project.get("source_of_truth", []), *args.source_of_truth]
        )
        if args.checkpoint:
            project["last_checkpoint"] = args.checkpoint
        if args.planning_baseline_json is not None:
            project["planning_baseline"] = parse_json_object(
                args.planning_baseline_json, "planning baseline"
            )
        if args.interface_baseline_json is not None:
            project["interface_baseline"] = parse_json_object(
                args.interface_baseline_json, "interface baseline"
            )

        atomic_write(args.index, payload)
    print(json.dumps(project, ensure_ascii=False, indent=2))
    return 0


def command_find(args: argparse.Namespace) -> int:
    projects = [
        project for project in load_index(args.index)["projects"] if matches(project, args.query)
    ]
    print(json.dumps(projects, ensure_ascii=False, indent=2))
    return 0 if projects else 1


def command_list(args: argparse.Namespace) -> int:
    projects = load_index(args.index)["projects"]
    if args.status:
        projects = [project for project in projects if project.get("status") == args.status]
    projects = sorted(projects, key=lambda item: str(item.get("updated_at", "")), reverse=True)
    print(json.dumps(projects, ensure_ascii=False, indent=2))
    return 0


def command_system(args: argparse.Namespace) -> int:
    needle = args.name.strip().casefold()
    projects = [
        project
        for project in load_index(args.index)["projects"]
        if str(project.get("system_name", "")).strip().casefold() == needle
    ]
    projects = sorted(projects, key=lambda item: str(item.get("name", "")).casefold())
    result = {"system_name": args.name.strip(), "components": projects}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if projects else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    subparsers = parser.add_subparsers(dest="command", required=True)

    upsert = subparsers.add_parser("upsert", help="create or update one project identity")
    upsert.add_argument("--name", required=True)
    upsert.add_argument("--root", required=True)
    upsert.add_argument("--alias", action="append", default=[])
    upsert.add_argument("--artifact", action="append", default=[])
    upsert.add_argument("--run-entry", action="append", default=[])
    upsert.add_argument("--thread-id", default=None)
    upsert.add_argument("--remote-url", action="append", default=[])
    upsert.add_argument("--system-name", default=None)
    upsert.add_argument("--component-role", default=None)
    upsert.add_argument("--upstream-project", action="append", default=[])
    upsert.add_argument("--downstream-project", action="append", default=[])
    upsert.add_argument("--product-doc", action="append", default=[])
    upsert.add_argument("--source-of-truth", action="append", default=[])
    upsert.add_argument("--status", default="unknown")
    upsert.add_argument("--checkpoint", default=None)
    upsert.add_argument("--planning-baseline-json", default=None)
    upsert.add_argument("--interface-baseline-json", default=None)
    upsert.set_defaults(handler=command_upsert)

    finding = subparsers.add_parser("find", help="find projects by name, alias or path")
    finding.add_argument("--query", required=True)
    finding.set_defaults(handler=command_find)

    listing = subparsers.add_parser("list", help="list indexed projects")
    listing.add_argument("--status", choices=sorted(STATUSES), default=None)
    listing.set_defaults(handler=command_list)

    system = subparsers.add_parser("system", help="list components in one parent system")
    system.add_argument("--name", required=True)
    system.set_defaults(handler=command_system)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
