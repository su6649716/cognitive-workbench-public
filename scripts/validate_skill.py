#!/usr/bin/env python3
"""Validate this package's constrained YAML frontmatter, links and Python syntax.

This is a local structural checker, not an official validator or behavioral eval.
"""
from __future__ import annotations
import ast
import json
import re
from pathlib import Path


def validate(root: Path) -> dict:
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    errors = []
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        return {"valid": False, "errors": ["missing YAML frontmatter"]}
    front = parts[1]
    nm = re.search(r"^name:\s*([a-z0-9-]+)\s*$", front, re.M)
    name = nm.group(1) if nm else ""
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64 or name != root.name:
        errors.append("invalid name or directory mismatch")
    dm = re.search(r"^description: >-\n((?:  .*(?:\n|$))+)", front, re.M)
    description = " ".join(line.strip() for line in dm.group(1).splitlines()) if dm else ""
    if not description or len(description) > 1024:
        errors.append("invalid description")
    if len(text.splitlines()) >= 500:
        errors.append("main file exceeds this package's size budget")
    reference_count = 0
    for doc in [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]:
        source = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", source):
            if "://" in target or target.startswith("#"):
                continue
            p = (doc.parent / target.split("#")[0]).resolve()
            if not p.is_relative_to(root.resolve()) or not p.is_file():
                errors.append(f"broken/outside link: {doc.name} -> {target}")
            reference_count += 1
    main_links = set(re.findall(r"\]\((references/[^)]+)\)", text))
    for p in (root / "references").glob("*.md"):
        if p.relative_to(root).as_posix() not in main_links:
            errors.append(f"reference is not directly reachable from main: {p.name}")
    for p in (root / "scripts").glob("*.py"):
        try: ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc: errors.append(f"{p.name}: {exc.msg}")
    config = (root / "agents/openai.yaml").read_text(encoding="utf-8")
    for need in ('display_name: "认知助手"', "$cognitive-workbench", "allow_implicit_invocation: true"):
        if need not in config: errors.append("missing expected host metadata")
    return {"valid": not errors, "name": name, "description_characters": len(description),
            "skill_lines": len(text.splitlines()), "local_links_checked": reference_count,
            "reference_files": len(list((root / "references").glob("*.md"))), "errors": errors,
            "validation_type": "package_specific_static_validation_not_official_certification"}

if __name__ == "__main__":
    result = validate(Path(__file__).resolve().parent.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] else 1)
