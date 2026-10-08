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
STATE_SYNC = SCRIPT_DIR / "sync_state.py"
SKILL_ROOT = SCRIPT_DIR.parent


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
            product_doc = root / "PRODUCT.md"
            project.mkdir()
            artifact.write_text("bundle", encoding="utf-8")
            product_doc.write_text("# 产品原则\n", encoding="utf-8")
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
                "--system-name",
                "认知成长系统",
                "--component-role",
                "学习内容生产与阅读",
                "--downstream-project",
                "学习观测台",
                "--product-doc",
                str(product_doc),
                "--source-of-truth",
                "学习记录以本项目数据库为准",
                "--planning-baseline-json",
                json.dumps(
                    {
                        "ultimate_outcome": "完成真实学习任务",
                        "component_job": "阅读与复盘",
                        "not_responsible": ["替用户作答"],
                    },
                    ensure_ascii=False,
                ),
                "--interface-baseline-json",
                json.dumps(
                    {
                        "primary_action": "继续阅读",
                        "visual_semantics": "输入与来源事实清楚区分",
                    },
                    ensure_ascii=False,
                ),
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

            found_by_role = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "find",
                "--query",
                "内容生产",
            )
            self.assertEqual(found_by_role.returncode, 0, found_by_role.stderr)

            found_by_plan = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "find",
                "--query",
                "替用户作答",
            )
            self.assertEqual(found_by_plan.returncode, 0, found_by_plan.stderr)

            observer = root / "observer"
            observer.mkdir()
            second = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "upsert",
                "--name",
                "学习观测台",
                "--root",
                str(observer),
                "--system-name",
                "认知成长系统",
                "--component-role",
                "学习结果汇总与复盘",
                "--upstream-project",
                "认知生长学校",
            )
            self.assertEqual(second.returncode, 0, second.stderr)

            system = run(
                "python3",
                str(PROJECT_INDEX),
                "--index",
                str(index),
                "system",
                "--name",
                "认知成长系统",
            )
            self.assertEqual(system.returncode, 0, system.stderr)
            components = json.loads(system.stdout)["components"]
            self.assertEqual({item["name"] for item in components}, {"认知生长学校", "学习观测台"})
            stored = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(stored["schema_version"], 3)
            reader = next(item for item in stored["projects"] if item["name"] == "认知生长学校")
            self.assertEqual(reader["planning_baseline"]["component_job"], "阅读与复盘")
            self.assertEqual(reader["interface_baseline"]["primary_action"], "继续阅读")


class CollaborativeDefinitionGateTests(unittest.TestCase):
    def upsert_baseline(
        self, index: Path, project: Path, baseline: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return run(
            "python3",
            str(PROJECT_INDEX),
            "--index",
            str(index),
            "upsert",
            "--name",
            "高返工产品",
            "--root",
            str(project),
            "--planning-baseline-json",
            json.dumps(baseline, ensure_ascii=False),
            "--status",
            "active",
        )

    def check_gate(
        self,
        index: Path,
        *,
        risk: str,
        target_stage: str,
        user_facing: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "python3",
            str(PROJECT_INDEX),
            "--index",
            str(index),
            "gate",
            "--query",
            "高返工产品",
            "--risk",
            risk,
            "--target-stage",
            target_stage,
        ]
        if user_facing:
            arguments.append("--user-facing")
        return run(*arguments)

    def test_high_risk_work_requires_user_owned_definition_and_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "source"
            project.mkdir()
            index = root / "index.json"

            inferred = self.upsert_baseline(
                index,
                project,
                {
                    "purpose_status": "assistant_inferred",
                    "boundary_status": "draft",
                    "design_status": "draft",
                    "sample_status": "pending",
                    "baseline_status": "current",
                },
            )
            self.assertEqual(inferred.returncode, 0, inferred.stderr)
            blocked = self.check_gate(
                index, risk="high", target_stage="full", user_facing=True
            )
            self.assertEqual(blocked.returncode, 1, blocked.stderr)
            blocked_payload = json.loads(blocked.stdout)
            self.assertFalse(blocked_payload["allowed"])
            self.assertTrue(
                any("purpose" in reason for reason in blocked_payload["reasons"])
            )
            self.assertIn(
                "planning baseline has no traceable source",
                blocked_payload["reasons"],
            )

            confirmed = self.upsert_baseline(
                index,
                project,
                {
                    "purpose_status": "user_confirmed",
                    "boundary_status": "user_confirmed",
                    "design_status": "user_confirmed",
                    "sample_status": "pending",
                    "baseline_status": "current",
                    "baseline_source": "thread-1",
                },
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            sample_allowed = self.check_gate(
                index, risk="high", target_stage="sample", user_facing=True
            )
            self.assertEqual(sample_allowed.returncode, 0, sample_allowed.stderr)
            self.assertTrue(json.loads(sample_allowed.stdout)["allowed"])

            full_blocked = self.check_gate(
                index, risk="high", target_stage="full", user_facing=True
            )
            self.assertEqual(full_blocked.returncode, 1, full_blocked.stderr)
            self.assertIn(
                "representative sample is not accepted",
                json.loads(full_blocked.stdout)["reasons"],
            )

            unjustified_skip = self.upsert_baseline(
                index,
                project,
                {
                    "purpose_status": "user_confirmed",
                    "boundary_status": "user_confirmed",
                    "design_status": "user_confirmed",
                    "sample_status": "not_required",
                    "baseline_status": "current",
                    "baseline_source": "thread-1",
                },
            )
            self.assertEqual(unjustified_skip.returncode, 0, unjustified_skip.stderr)
            skip_blocked = self.check_gate(
                index, risk="high", target_stage="full", user_facing=True
            )
            self.assertEqual(skip_blocked.returncode, 1, skip_blocked.stderr)
            self.assertIn(
                "sample is marked not_required without a reason",
                json.loads(skip_blocked.stdout)["reasons"],
            )

            accepted = self.upsert_baseline(
                index,
                project,
                {
                    "purpose_status": "user_confirmed",
                    "boundary_status": "user_confirmed",
                    "design_status": "user_confirmed",
                    "sample_status": "accepted",
                    "baseline_status": "current",
                    "baseline_source": "thread-1",
                },
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            full_allowed = self.check_gate(
                index, risk="high", target_stage="full", user_facing=True
            )
            self.assertEqual(full_allowed.returncode, 0, full_allowed.stderr)
            self.assertTrue(json.loads(full_allowed.stdout)["allowed"])

    def test_low_risk_mechanical_work_is_not_blocked_by_missing_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "source"
            project.mkdir()
            index = root / "index.json"
            created = self.upsert_baseline(index, project, {})
            self.assertEqual(created.returncode, 0, created.stderr)

            allowed = self.check_gate(index, risk="low", target_stage="full")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue(json.loads(allowed.stdout)["allowed"])

    def test_stale_baseline_pauses_medium_risk_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "source"
            project.mkdir()
            index = root / "index.json"
            created = self.upsert_baseline(
                index,
                project,
                {
                    "purpose_status": "user_confirmed",
                    "boundary_status": "user_confirmed",
                    "design_status": "user_confirmed",
                    "sample_status": "not_required",
                    "sample_not_required_reason": "non-visual backend change",
                    "baseline_status": "stale",
                },
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            blocked = self.check_gate(index, risk="medium", target_stage="full")
            self.assertEqual(blocked.returncode, 1, blocked.stderr)
            self.assertIn(
                "planning baseline is missing, stale or superseded",
                json.loads(blocked.stdout)["reasons"],
            )


class SystemCoherenceReferenceTests(unittest.TestCase):
    def test_system_coherence_contract_contains_required_boundaries(self) -> None:
        reference = (SKILL_ROOT / "references" / "system-coherence.md").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "广思考，窄执行",
            "横向角色闭环",
            "纵向数据闭环",
            "必要联动",
            "价值扩展",
            "远期机会",
            "本轮最终实施范围",
            "获客与培训协同",
            "一个员工可能绑定多个微信",
            "经营观测组件的只读下游协议",
            "最终要改变的现实",
            "当前交付物不负责",
            "纠正传播",
            "interface-translation.md",
            "collaborative-definition.md",
            "scripts/project_index.py gate",
        ):
            self.assertIn(phrase, reference)

        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/system-coherence.md", skill)
        self.assertIn("scripts/project_index.py system", skill)
        self.assertIn("planning_baseline", skill)
        self.assertIn("interface_baseline", skill)
        self.assertIn("references/collaborative-definition.md", skill)

        collaborative = (
            SKILL_ROOT / "references" / "collaborative-definition.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "目的的归属属于用户",
            "assistant_inferred",
            "方案竞争",
            "渐进式开工",
            "语义变化暂停",
            "scripts/project_index.py gate",
        ):
            self.assertIn(phrase, collaborative)


class InterfaceTranslationReferenceTests(unittest.TestCase):
    def test_interface_translation_connects_task_logic_to_visual_semantics(self) -> None:
        reference = (SKILL_ROOT / "references" / "interface-translation.md").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "不是第五个工作流",
            "先选择操作形态",
            "界面合同",
            "来源事实、可编辑输入、系统结果如何区分",
            "失败与恢复",
            "视觉语义",
            "真实任务样本",
            "不要因为进入界面转译而自动增加",
        ):
            self.assertIn(phrase, reference)


class InteractionEntryTests(unittest.TestCase):
    def test_one_phrase_entry_and_action_boundaries_are_explicit(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            '中文名“认知助手”',
            "认知一下",
            "启动认知助手",
            "用认知助手看看",
            "已经启动、这次准备怎么处理、是否会直接执行",
            "先分析",
            "然后直接做",
            "收口",
            "继承原句的动作动词和授权",
        ):
            self.assertIn(phrase, skill)

        interface = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "认知助手"', interface)
        self.assertIn("$cognitive-workbench", interface)


class LedgerTests(unittest.TestCase):
    def test_rejects_duplicate_close_and_unreviewable_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "events.jsonl"
            missing_outcome = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "task_close",
                "--project",
                "缺少结果",
                "--thread-id",
                "thread-0",
                "--source",
                "test",
                "--statement",
                "不能空结果关闭",
                "--status",
                "closed",
            )
            self.assertEqual(missing_outcome.returncode, 2)
            self.assertIn("requires --outcome", missing_outcome.stderr)

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
                "--outcome",
                "决定已确认并完成验证",
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
                "--outcome",
                "不应写入",
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

    def test_due_lists_overdue_and_watch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "events.jsonl"
            overdue = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "hypothesis",
                "--project",
                "测试项目",
                "--thread-id",
                "thread-due",
                "--source",
                "test",
                "--statement",
                "昨天到期的判断",
                "--due-at",
                "2020-01-01T00:00:00+08:00",
            )
            self.assertEqual(overdue.returncode, 0, overdue.stderr)
            watching = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--type",
                "hypothesis",
                "--project",
                "测试项目",
                "--thread-id",
                "thread-watch",
                "--source",
                "test",
                "--statement",
                "等触发条件的判断",
                "--review-trigger",
                "首个真实样本返回后",
            )
            self.assertEqual(watching.returncode, 0, watching.stderr)

            due = run(
                "python3",
                str(LEDGER),
                "--ledger",
                str(ledger),
                "due",
            )
            self.assertEqual(due.returncode, 1, due.stdout + due.stderr)
            payload = json.loads(due.stdout)
            self.assertEqual(len(payload["overdue"]), 1)
            self.assertEqual(len(payload["watch"]), 1)
            self.assertEqual(payload["overdue"][0]["statement"], "昨天到期的判断")


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


class StateSyncTests(unittest.TestCase):
    def test_snapshot_validates_and_copies_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            repo = root / "state-repo"
            (codex_home / "memories").mkdir(parents=True)
            (codex_home / "automations" / "weekly").mkdir(parents=True)
            repo.mkdir()
            run("git", "init", "-b", "main", cwd=repo)
            (codex_home / "memories" / "PROJECT_INDEX.json").write_text(
                '{"schema_version": 1, "projects": []}\n', encoding="utf-8"
            )
            (codex_home / "memories" / "COGNITIVE_EVENTS.jsonl").write_text(
                '{"id":"evt-1","record_kind":"event"}\n', encoding="utf-8"
            )
            (codex_home / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
            (codex_home / "automations" / "weekly" / "automation.toml").write_text(
                'name = "weekly"\n', encoding="utf-8"
            )

            result = run(
                "python3",
                str(STATE_SYNC),
                "--codex-home",
                str(codex_home),
                "--repo",
                str(repo),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["ledger_rows"], 1)
            self.assertTrue((repo / "memories" / "PROJECT_INDEX.json").is_file())
            self.assertTrue((repo / "automations" / "weekly" / "automation.toml").is_file())

    def test_snapshot_blocks_secret_like_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            repo = root / "state-repo"
            (codex_home / "memories").mkdir(parents=True)
            repo.mkdir()
            run("git", "init", "-b", "main", cwd=repo)
            (codex_home / "memories" / "PROJECT_INDEX.json").write_text(
                '{"schema_version": 1, "projects": []}\n', encoding="utf-8"
            )
            (codex_home / "memories" / "COGNITIVE_EVENTS.jsonl").write_text(
                '{"id":"evt-1"}\n', encoding="utf-8"
            )
            (codex_home / "AGENTS.md").write_text(
                "token ghp_abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8"
            )

            result = run(
                "python3",
                str(STATE_SYNC),
                "--codex-home",
                str(codex_home),
                "--repo",
                str(repo),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("secret scan blocked", result.stdout)


if __name__ == "__main__":
    unittest.main()
