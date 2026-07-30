#!/usr/bin/env python3
"""Regression tests for cognitive-workbench's deterministic helpers."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LEDGER = SCRIPT_DIR / "cognitive_ledger.py"
PROJECT_INDEX = SCRIPT_DIR / "project_index.py"
GIT_GUARD = SCRIPT_DIR / "git_project_guard.py"


def run(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class ProjectIndexTests(unittest.TestCase):
    def test_upsert_merges_aliases_and_finds_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "source"
            artifact = root / "认知生长学校.app"
            project.mkdir()
            artifact.write_text("bundle", encoding="utf-8")
            index = root / "index.json"

            created = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "upsert",
                "--name",
                "认知生长学校",
                "--root",
                str(project),
                "--alias",
                "cognitive-growth-reader",
                "--artifact",
                str(artifact),
                "--run-entry",
                "http://127.0.0.1:5173/",
                "--thread-id",
                "thread-1",
                "--status",
                "active",
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            updated = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "upsert",
                "--name",
                "认知生长学校",
                "--root",
                str(project),
                "--alias",
                "cognitive-school-recovered",
                "--checkpoint",
                "abc123",
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            payload = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["projects"]), 1)
            self.assertIn("cognitive-school-recovered", payload["projects"][0]["aliases"])

            found = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "find",
                "--query",
                "认知生长学校.app",
            )
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertEqual(len(json.loads(found.stdout)), 1)


class LedgerTests(unittest.TestCase):
    def test_rejects_duplicate_close_and_unreviewable_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "events.jsonl"
            first = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "task_close",
                "--project",
                "测试项目",
                "--thread-id",
                "thread-1",
                "--source",
                "test",
                "--statement",
                "保留一个收口事件",
                "--status",
                "closed",
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_event = json.loads(first.stdout)

            duplicate = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "task_close",
                "--project",
                "测试项目",
                "--thread-id",
                "thread-1",
                "--episode-id",
                first_event["episode_id"],
                "--source",
                "test",
                "--statement",
                "再次收口同一阶段",
                "--status",
                "closed",
            )
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("update existing event", duplicate.stderr)

            no_review = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "hypothesis",
                "--project",
                "另一个项目",
                "--thread-id",
                "thread-2",
                "--source",
                "test",
                "--statement",
                "验证一个判断",
                "--status",
                "active",
            )
            self.assertEqual(no_review.returncode, 2)
            self.assertIn("--due-at or --review-trigger", no_review.stderr)

            accepted = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "hypothesis",
                "--project",
                "另一个项目",
                "--thread-id",
                "thread-2",
                "--source",
                "test",
                "--statement",
                "验证一个判断",
                "--review-trigger",
                "首个真实样本返回后",
                "--status",
                "active",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            hypothesis = json.loads(accepted.stdout)
            relinked = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "update",
                "--id",
                hypothesis["id"],
                "--thread-id",
                "thread-2-relinked",
            )
            self.assertEqual(relinked.returncode, 0, relinked.stderr)

            audit = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "audit",
            )
            self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["healthy"])


class GitGuardTests(unittest.TestCase):
    def test_skill_marker_and_close_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text("# test\n", encoding="utf-8")
            source = root / "worker.py"
            source.write_text("print('v1')\n", encoding="utf-8")

            initialized = run(
                "python3",
                str(GIT_GUARD),
                "--path",
                str(root),
                "--init-if-missing",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertTrue(json.loads(initialized.stdout)["initialized_now"])

            run("git", "config", "user.name", "Codex Test", cwd=root)
            run("git", "config", "user.email", "codex@example.invalid", cwd=root)
            run("git", "add", "--", "SKILL.md", "worker.py", cwd=root)
            base_commit = run("git", "commit", "-m", "baseline", cwd=root)
            self.assertEqual(base_commit.returncode, 0, base_commit.stderr)
            baseline = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()

            source.write_text("print('v2')\n", encoding="utf-8")
            run("git", "add", "--", "worker.py", cwd=root)
            changed = run("git", "commit", "-m", "change worker", cwd=root)
            self.assertEqual(changed.returncode, 0, changed.stderr)

            closed = run(
                "python3",
                str(GIT_GUARD),
                "--path",
                str(root),
                "--check-close",
                "--baseline-head",
                baseline,
                "--task-path",
                "worker.py",
            )
            self.assertEqual(closed.returncode, 0, closed.stderr)
            self.assertTrue(json.loads(closed.stdout)["closure"]["ready"])

            source.write_text("print('v3')\n", encoding="utf-8")
            open_result = run(
                "python3",
                str(GIT_GUARD),
                "--path",
                str(root),
                "--check-close",
                "--baseline-head",
                baseline,
                "--task-path",
                "worker.py",
            )
            self.assertFalse(json.loads(open_result.stdout)["closure"]["ready"])


if __name__ == "__main__":
    unittest.main()
