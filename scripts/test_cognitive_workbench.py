#!/usr/bin/env python3
"""Offline regression suite: 12 migrated original test contracts plus targeted fixes.

All mutable data and Git remotes are synthetic TemporaryDirectory fixtures.
No API, model, user-state, global installation or external push is used.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
# Prevent user Git global config, hooks and signing from affecting test fixtures.
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
import sync_state as sync
import project_index as idx
import cognitive_ledger as ledger
import git_project_guard as guard
from validate_skill import validate
from _state_common import validate_index, parse_ledger


def run(*args, cwd=None):
    return subprocess.run([str(a) for a in args], cwd=cwd, text=True, capture_output=True, env=ENV, timeout=30)


def git(repo, *args):
    p = run("git", *args, cwd=repo)
    if p.returncode:
        raise AssertionError(f"git {args[0]} failed: {p.stderr}")
    return p.stdout.strip()


def cli(script, *args):
    return run(sys.executable, "-S", SCRIPTS / script, *args)


def payload(p):
    if p.returncode:
        raise AssertionError(f"command failed: {p.stdout} {p.stderr}")
    return json.loads(p.stdout)


def init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Offline Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "README.md").write_text("fixture\n")
    git(root, "add", "--", "README.md"); git(root, "commit", "-m", "baseline")
    return root


def fixture(root, automation=True):
    home = root / "home"; repo = init_repo(root / "state"); remote = root / "origin.git"
    git(root, "init", "--bare", "--initial-branch=main", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    (home / "memories").mkdir(parents=True)
    (home / "memories/PROJECT_INDEX.json").write_text('{"schema_version":3,"projects":[]}\n')
    # Original sync tests used structurally incomplete rows. This fixture now
    # preserves their intended sync behavior while satisfying S07's domain check.
    event = {"record_kind": "event", "id": "evt-fixture", "type": "task_close", "project": "fixture", "statement": "fixture decision", "source": "offline test", "status": "closed", "outcome": "recorded", "thread_id": "test-only"}
    (home / "memories/COGNITIVE_EVENTS.jsonl").write_text(json.dumps(event) + "\n")
    (home / "STATE_INFO.json").write_text(json.dumps({"schema_version":1, "generation":"cognitive-workbench-2.4", "state_id":"test", "created_at":"2026-10-10T00:00:00+08:00"}))
    (home / "AGENTS.md").write_text("# Synthetic rules\n")
    if automation:
        p = home / "automations/weekly/automation.toml"; p.parent.mkdir(parents=True); p.write_text('name = "weekly"\n')
    return home, repo, remote


def sync_cli(home, repo, *args):
    return cli("sync_state.py", "--codex-home", home, "--repo", repo, *args)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cw-redesign-test-")
        self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def index_upsert(self, baseline=None, *extra):
        project = self.root / "project"; project.mkdir(exist_ok=True)
        args = ["--index", self.root / "index.json", "upsert", "--name", "测试项目", "--root", project]
        if baseline is not None: args += ["--planning-baseline-json", json.dumps(baseline, ensure_ascii=False)]
        return cli("project_index.py", *args, *extra)
    def gate(self, risk="high", stage="full", facing=True):
        args = ["--index", self.root / "index.json", "gate", "--query", "测试项目", "--risk", risk, "--target-stage", stage]
        if facing: args += ["--user-facing"]
        return cli("project_index.py", *args)
    def record(self, kind="task_close", project="P", statement="decision", *extra):
        return cli("cognitive_ledger.py", "--ledger", self.root / "events.jsonl", "record", "--type", kind, "--project", project, "--source", "fixture", "--statement", statement, "--thread-id", "thread-1", *extra)


class CoreContracts(FixtureCase):
    """Core identity, state, workflow and engineering regression contracts."""
    def test_O01_upsert_merges_aliases_and_finds_artifact(self):
        a = self.root / "成品.app"; a.write_text("bundle")
        d = self.root / "PRODUCT.md"; d.write_text("source of truth")
        first = payload(self.index_upsert({"component_job": "阅读与复盘"}, "--alias", "reader", "--artifact", a, "--product-doc", d, "--system-name", "系统", "--component-role", "内容生产", "--interface-baseline-json", '{"primary_action":"继续阅读"}', "--status", "active"))
        payload(self.index_upsert(None, "--alias", "school", "--checkpoint", "abc"))
        for query in ("成品.app", "内容生产", "阅读与复盘", "school"):
            self.assertEqual(len(payload(cli("project_index.py", "--index", self.root / "index.json", "find", "--query", query))), 1)
        p = payload(cli("project_index.py", "--index", self.root / "index.json", "system", "--name", "系统"))["components"][0]
        self.assertEqual(p["id"], first["id"]); self.assertEqual(p["status"], "active")
        self.assertEqual(p["interface_baseline"]["primary_action"], "继续阅读")
        observer=self.root/"observer"; observer.mkdir()
        payload(cli("project_index.py","--index",self.root/"index.json","upsert","--name","观测台","--root",observer,"--system-name","系统","--upstream-project","测试项目"))
        components=payload(cli("project_index.py","--index",self.root/"index.json","system","--name","系统"))["components"]
        self.assertEqual({x["name"] for x in components},{"测试项目","观测台"})
        stored=json.loads((self.root/"index.json").read_text()); self.assertEqual(stored["schema_version"],3)
        self.assertIn("school", stored["projects"][0]["aliases"])

    def test_O02_high_risk_requires_definition_and_sample(self):
        payload(self.index_upsert({"purpose_status":"assistant_inferred","boundary_status":"draft","design_status":"draft","sample_status":"pending","baseline_status":"current"}))
        self.assertFalse(json.loads(self.gate().stdout)["allowed"])
        ready = {"purpose_status":"user_confirmed","boundary_status":"user_confirmed","design_status":"user_confirmed","sample_status":"pending","baseline_status":"current","baseline_source":"thread-1"}
        payload(self.index_upsert(ready)); self.assertTrue(payload(self.gate(stage="sample"))["allowed"])
        self.assertFalse(json.loads(self.gate().stdout)["allowed"])
        payload(self.index_upsert({"sample_status":"not_required"}, "--merge-baseline")); self.assertFalse(json.loads(self.gate().stdout)["allowed"])
        payload(self.index_upsert({"sample_status":"accepted"}, "--merge-baseline")); self.assertTrue(payload(self.gate())["allowed"])
    def test_O03_low_risk_not_blocked_by_missing_baseline(self):
        payload(self.index_upsert({})); self.assertTrue(payload(self.gate("low", facing=False))["allowed"])
    def test_O04_stale_baseline_pauses_medium_risk(self):
        payload(self.index_upsert({"purpose_status":"user_confirmed","boundary_status":"user_confirmed","design_status":"user_confirmed","baseline_status":"stale","baseline_source":"thread-1"}))
        self.assertIn("planning baseline is missing, stale or superseded", json.loads(self.gate("medium").stdout)["reasons"])
    def test_O05_system_coherence_contract_preserved(self):
        text = (ROOT / "references/system-coherence.md").read_text()
        for phrase in ("广思考，窄执行","横向角色闭环","纵向数据闭环","必要联动","价值扩展","远期机会","本轮最终实施范围","获客与培训协同","一个员工可能绑定多个微信","经营观测组件的只读下游协议","最终要改变的现实","当前交付物不负责","纠正传播","interface-translation.md","collaborative-definition.md","scripts/project_index.py gate"):
            self.assertIn(phrase, text)
        main = (ROOT / "SKILL.md").read_text()
        for phrase in ("references/system-coherence.md","scripts/project_index.py system","planning_baseline","interface_baseline","references/collaborative-definition.md"):
            self.assertIn(phrase, main)
        collaborative=(ROOT/"references/collaborative-definition.md").read_text()
        for phrase in ("目的的归属属于用户","assistant_inferred","方案竞争","渐进式开工","语义变化暂停","scripts/project_index.py gate"):
            self.assertIn(phrase,collaborative)
    def test_O06_interface_translation_contract_preserved(self):
        text = (ROOT / "references/interface-translation.md").read_text()
        for phrase in ("不是第五个工作流","先选择操作形态","界面合同","来源事实、可编辑输入、系统结果如何区分","失败与恢复","视觉语义","真实任务样本","不要因为进入界面转译而自动增加"):
            self.assertIn(phrase, text)
    def test_O07_entry_and_action_boundaries_preserved(self):
        text = (ROOT / "SKILL.md").read_text()
        for phrase in ('中文名“认知助手”',"认知一下","启动认知助手","用认知助手看看","已经启动、这次准备怎么处理、是否会直接执行","先分析","然后直接做","收口","继承原句的动作动词和授权"):
            self.assertIn(phrase, text)
        self.assertIn('$cognitive-workbench', (ROOT / 'agents/openai.yaml').read_text())
    def test_O08_duplicate_close_and_unreviewable_hypothesis_rejected(self):
        self.assertEqual(self.record("task_close", "P", "no outcome", "--status", "closed").returncode, 2)
        e = payload(self.record("task_close", "P", "decision", "--status", "closed", "--outcome", "saved"))
        self.assertEqual(self.record("task_close", "P", "another close", "--episode-id", e["episode_id"]).returncode, 2)
        self.assertEqual(self.record("hypothesis", "Q", "predict", "--status", "active").returncode, 2)
        h = payload(self.record("hypothesis", "Q", "predict", "--status", "active", "--review-trigger", "sample returns"))
        payload(cli("cognitive_ledger.py", "--ledger", self.root / "events.jsonl", "update", "--id", h["id"], "--thread-id", "new-thread"))
        self.assertTrue(payload(cli("cognitive_ledger.py", "--ledger", self.root / "events.jsonl", "audit"))["healthy"])
    def test_O09_due_lists_overdue_and_watch(self):
        payload(self.record("hypothesis", "P", "past", "--due-at", "2020-01-01T00:00:00+08:00"))
        payload(self.record("hypothesis", "P", "watch", "--review-trigger", "sample returns"))
        result = cli("cognitive_ledger.py", "--ledger", self.root / "events.jsonl", "due")
        self.assertEqual(result.returncode, 1); p = json.loads(result.stdout)
        self.assertEqual((len(p["overdue"]), len(p["watch"])), (1, 1))
    def test_O10_git_skill_marker_and_close(self):
        project = self.root / "code"; project.mkdir(); (project / "SKILL.md").write_text("test\n")
        (project / "worker.py").write_text("v1\n")
        self.assertTrue(payload(cli("git_project_guard.py", "--path", project, "--init-if-missing"))["initialized_now"])
        git(project,"config","user.name","Test"); git(project,"config","user.email","test@example.invalid")
        git(project,"add","--","SKILL.md","worker.py"); git(project,"commit","-m","base"); base = git(project,"rev-parse","HEAD")
        (project / "worker.py").write_text("v2\n"); git(project,"add","--","worker.py"); git(project,"commit","-m","change")
        self.assertTrue(payload(cli("git_project_guard.py","--path",project,"--check-close","--baseline-head",base,"--task-path","worker.py"))["closure"]["ready"])
        (project / "worker.py").write_text("v3\n")
        self.assertFalse(payload(cli("git_project_guard.py","--path",project,"--check-close","--baseline-head",base,"--task-path","worker.py"))["closure"]["ready"])
    def test_O11_snapshot_validates_and_copies_state(self):
        home, repo, _ = fixture(self.root)
        p = payload(sync_cli(home,repo)); self.assertEqual(p["ledger_rows"],1)
        for rel in ("memories/PROJECT_INDEX.json","automations/weekly/automation.toml"):
            self.assertTrue((repo / rel).is_file())
    def test_O12_snapshot_blocks_secret_like_content(self):
        home,repo,_ = fixture(self.root); (home / "AGENTS.md").write_text("token ghp_" + "SYNTHETIC" * 4)
        p = sync_cli(home,repo); self.assertEqual(p.returncode,1); self.assertIn("secret scan blocked",p.stdout)
        self.assertFalse((repo / "SYNC_MANIFEST.json").exists())


class SyncRegressions(FixtureCase):
    def test_S01_no_content_change_no_new_commit(self):
        home,repo,remote=fixture(self.root); a=payload(sync_cli(home,repo,"--push")); first=(repo/sync.MANIFEST).read_bytes()
        b=payload(sync_cli(home,repo,"--push")); self.assertFalse(b["changed"]); self.assertFalse(b["pushed"])
        self.assertEqual(a["commit"],b["commit"]); self.assertEqual(first,(repo/sync.MANIFEST).read_bytes())
        self.assertEqual(git(remote,"rev-parse","main"),b["commit"])
    def test_S02_unscanned_extra_not_committed(self):
        home,repo,_=fixture(self.root); (repo/"config").mkdir(); extra=repo/"config/extra.txt"; extra.write_text("token ghp_" + "SYNTHETIC"*4)
        payload(sync_cli(home,repo,"--push"))
        self.assertNotEqual(run("git","cat-file","-e","HEAD:config/extra.txt",cwd=repo).returncode,0)
        self.assertTrue(extra.exists()); self.assertIn("config/extra.txt",git(repo,"ls-files","--others","--exclude-standard"))
    def test_S03_preexisting_staged_preserved_and_blocks(self):
        home,repo,_=fixture(self.root); (repo/"unrelated.txt").write_text("keep"); git(repo,"add","--","unrelated.txt")
        before=git(repo,"diff","--cached"); head=git(repo,"rev-parse","HEAD")
        self.assertEqual(sync_cli(home,repo,"--push").returncode,1)
        self.assertEqual(before,git(repo,"diff","--cached")); self.assertEqual(head,git(repo,"rev-parse","HEAD"))
        self.assertFalse((repo/sync.MANIFEST).exists())
    def test_S04_wrong_branch_blocks_before_mutation(self):
        home,repo,remote=fixture(self.root); p=payload(sync_cli(home,repo,"--push")); old=(repo/sync.MANIFEST).read_bytes()
        git(repo,"checkout","-b","candidate"); (home/"AGENTS.md").write_text("new")
        self.assertEqual(sync_cli(home,repo,"--push").returncode,1)
        self.assertEqual((repo/sync.MANIFEST).read_bytes(),old); self.assertEqual(git(remote,"rev-parse","main"),p["commit"])
    def test_S05_removed_managed_automation_deleted_only(self):
        home,repo,_=fixture(self.root); payload(sync_cli(home,repo,"--push"))
        extra=repo/"automations/keep.txt"; extra.write_text("keep")
        (home/"automations/weekly/automation.toml").unlink()
        payload(sync_cli(home,repo,"--push")); self.assertFalse((repo/"automations/weekly/automation.toml").exists()); self.assertTrue(extra.exists())
    def test_S06_empty_automations_valid(self):
        home,repo,_=fixture(self.root,False); p=payload(sync_cli(home,repo,"--push")); self.assertEqual(p["files"],4); self.assertTrue(p["remote_verified"])
    def test_S07_invalid_domain_objects_block(self):
        home,repo,_=fixture(self.root); (home/"memories/PROJECT_INDEX.json").write_text('{"projects":"wrong"}')
        self.assertEqual(sync_cli(home,repo).returncode,1); self.assertFalse((repo/sync.MANIFEST).exists())
        (home/"memories/PROJECT_INDEX.json").write_text('{"projects":[]}'); (home/"memories/COGNITIVE_EVENTS.jsonl").write_text('{"not_an_event":true}')
        self.assertEqual(sync_cli(home,repo).returncode,1)
    def test_DRY_RUN_does_not_write_snapshot(self):
        home,repo,_=fixture(self.root); p=payload(sync_cli(home,repo,"--dry-run")); self.assertEqual(p["status"],"preview")
        self.assertFalse((repo/sync.MANIFEST).exists()); self.assertFalse((repo/".git/cognitive-sync.lock").exists())
    def test_destination_conflict_preserved(self):
        home,repo,_=fixture(self.root); payload(sync_cli(home,repo,"--push")); (repo/"config/AGENTS.md").write_text("user edits")
        self.assertEqual(sync_cli(home,repo,"--push").returncode,1); self.assertEqual((repo/"config/AGENTS.md").read_text(),"user edits")
    def test_source_symlink_rejected(self):
        home,repo,_=fixture(self.root); target=home/"AGENTS.md"; target.unlink(); other=self.root/"private.txt"; other.write_text("secret context")
        target.symlink_to(other); self.assertEqual(sync_cli(home,repo).returncode,1); self.assertFalse((repo/sync.MANIFEST).exists())
    def test_destination_symlink_rejected(self):
        home,repo,_=fixture(self.root); other=self.root/"outside"; other.mkdir(); (repo/"config").symlink_to(other,target_is_directory=True)
        self.assertEqual(sync_cli(home,repo).returncode,1); self.assertEqual(list(other.iterdir()),[])
    def test_custom_confirmed_branch_verified(self):
        home,repo,remote=fixture(self.root); git(repo,"checkout","-b","candidate")
        p=payload(sync_cli(home,repo,"--push","--branch","candidate")); self.assertEqual(p["commit"],git(remote,"rev-parse","candidate"))
        self.assertNotEqual(run("git","rev-parse","--verify","main",cwd=remote).returncode,0)
    def test_detached_push_rejected(self):
        home,repo,_=fixture(self.root); git(repo,"checkout","--detach")
        self.assertEqual(sync_cli(home,repo,"--push").returncode,1); self.assertFalse((repo/sync.MANIFEST).exists())
    def test_actual_staged_bytes_must_match(self):
        home,repo,_=fixture(self.root); payload(sync_cli(home,repo))
        # A clean filter changing uploaded bytes must be caught before commit.
        (repo/".gitattributes").write_text("config/AGENTS.md filter=mutate\n")
        git(repo,"config","filter.mutate.clean","sed s/Synthetic/Altered/g")
        p=sync_cli(home,repo,"--push"); self.assertEqual(p.returncode,1); self.assertIn("staged bytes differ",p.stdout)
    def test_precommit_injection_never_pushed(self):
        home,repo,remote=fixture(self.root)
        hook=repo/".git/hooks/pre-commit"; hook.write_text("#!/bin/sh\nprintf injected > surprise.txt\ngit add -- surprise.txt\n"); hook.chmod(0o700)
        p=sync_cli(home,repo,"--push"); self.assertEqual(p.returncode,1); self.assertIn("unrelated paths",p.stdout)
        self.assertNotEqual(run("git","rev-parse","--verify","main",cwd=remote).returncode,0)
    def test_noop_retry_pushes_same_pending_commit(self):
        home,repo,remote=fixture(self.root)
        hook=remote/"hooks/pre-receive"; hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o700)
        first=sync_cli(home,repo,"--push"); self.assertEqual(first.returncode,1); head=git(repo,"rev-parse","HEAD")
        hook.unlink(); second=payload(sync_cli(home,repo,"--push")); self.assertEqual(second["commit"],head); self.assertFalse(second["changed"])
        self.assertTrue(second["remote_verified"])


class StateRegressions(FixtureCase):
    def test_C01_parent_derived_episode_deduped(self):
        e=payload(self.record()); p=self.record("task_close","P","different close","--parent-id",e["id"])
        self.assertEqual(p.returncode,2)
    def test_C01_parent_project_and_episode_validated(self):
        e=payload(self.record())
        self.assertEqual(self.record("hypothesis","Q","h","--parent-id",e["id"],"--review-trigger","sample").returncode,2)
        self.assertEqual(self.record("hypothesis","P","h","--parent-id",e["id"],"--episode-id","wrong","--review-trigger","sample").returncode,2)
    def test_C02_distinct_questions_same_thread_allowed(self):
        a=payload(self.record()); b=payload(self.record("task_close","P","new question"))
        self.assertNotEqual(a["episode_id"],b["episode_id"])
        self.assertEqual(self.record().returncode,2)
    def test_C02_same_statement_new_explicit_episode_allowed(self):
        payload(self.record()); self.assertEqual(self.record("task_close","P","decision","--episode-id","different-question").returncode,0)
    def test_C03_review_scoped_and_structural_status_separate(self):
        payload(self.record("hypothesis","Other","past","--due-at","2020-01-01T00:00:00Z")); payload(self.record("hypothesis","P","watch","--review-trigger","sample"))
        p=payload(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","audit","--project","P")); self.assertTrue(p["healthy"])
        allp=json.loads(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","audit").stdout)
        self.assertFalse(allp["healthy"]); self.assertTrue(allp["structural_healthy"]); self.assertTrue(allp["review_needed"])
        self.assertEqual(payload(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","due","--project","P"))["overdue"],[])
    def test_C04_concurrent_records_single_episode(self):
        args=[sys.executable,"-S",str(SCRIPTS/"cognitive_ledger.py"),"--ledger",str(self.root/"events.jsonl"),"record","--type","task_close","--project","P","--source","fixture","--thread-id","t","--episode-id","one","--statement"]
        processes=[subprocess.Popen([*args,str(n)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=ENV) for n in range(6)]
        codes=[]
        for p in processes: p.communicate(timeout=30); codes.append(p.returncode)
        self.assertEqual(codes.count(0),1); self.assertEqual(codes.count(2),5)
        self.assertEqual(len(parse_ledger((self.root/"events.jsonl").read_text())),1)
    def test_C05_updating_checkpoint_preserves_status(self):
        payload(self.index_upsert(None,"--status","active")); self.assertEqual(payload(self.index_upsert(None,"--checkpoint","new"))["status"],"active")
    def test_C06_null_source_and_invalid_states_rejected(self):
        self.assertEqual(self.index_upsert({"baseline_source":None}).returncode,2)
        self.assertEqual(self.index_upsert({"purpose_status":"pretend"}).returncode,2)
    def test_legacy_metadata_and_unknown_extensions_survive(self):
        project=self.root/"project"; project.mkdir(); original={"schema_version":1,"vendor":"retain","projects":[{"id":"legacy-id","name":"测试项目","root":str(project),"status":"paused","custom":{"x":1},"planning_baseline":{"extension":{"x":2}}}]}
        (self.root/"index.json").write_text(json.dumps(original))
        p=payload(self.index_upsert({"component_job":"read"},"--merge-baseline","--alias","alias")); self.assertEqual(p["id"],"legacy-id"); self.assertEqual(p["status"],"paused")
        self.assertEqual(p["planning_baseline"]["extension"],{"x":2}); self.assertEqual(json.loads((self.root/"index.json").read_text())["vendor"],"retain")
    def test_baseline_default_replacement_and_optional_merge(self):
        payload(self.index_upsert({"component_job":"old","invariants":["keep"]}))
        replaced=payload(self.index_upsert({"component_job":"new"}))
        self.assertNotIn("invariants",replaced["planning_baseline"])
        merged=payload(self.index_upsert({"invariants":["new invariant"]},"--merge-baseline"))
        self.assertEqual(merged["planning_baseline"]["component_job"],"new")
    def test_ledger_append_preserves_original_and_merges(self):
        e=payload(self.record("task_close","P","decision","--metadata-json",'{"a":{"x":1}}')); before=(self.root/"events.jsonl").read_bytes()
        payload(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","update","--id",e["id"],"--metadata-json",'{"a":{"y":2}}',"--note","new evidence"))
        self.assertTrue((self.root/"events.jsonl").read_bytes().startswith(before))
        p=payload(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","list"))[0]; self.assertEqual(p["metadata"]["a"],{"x":1,"y":2})
    def test_open_hypothesis_cannot_lose_review_point(self):
        e=payload(self.record("hypothesis","P","h","--review-trigger","sample"))
        self.assertEqual(cli("cognitive_ledger.py","--ledger",self.root/"events.jsonl","update","--id",e["id"],"--review-trigger","").returncode,2)
    def test_old_ledger_missing_optional_fields_is_readable(self):
        row={"record_kind":"event","id":"legacy","type":"task_close","project":"P","statement":"old decision","status":"closed","extension":"keep"}
        f=self.root/"events.jsonl"; f.write_text(json.dumps(row)+"\n"); before=f.read_bytes()
        p=payload(cli("cognitive_ledger.py","--ledger",f,"list"))[0]; self.assertEqual(p["episode_id"],"legacy"); self.assertEqual(p["extension"],"keep"); self.assertEqual(before,f.read_bytes())


class GitRegressions(FixtureCase):
    def test_C07_nonancestor_baseline_rejected(self):
        repo=init_repo(self.root/"code"); base=git(repo,"rev-parse","HEAD")
        git(repo,"checkout","--orphan","unrelated"); (repo/"worker.py").write_text("x"); git(repo,"add","--","worker.py"); git(repo,"commit","-m","other root")
        p=payload(cli("git_project_guard.py","--path",repo,"--check-close","--baseline-head",base,"--task-path","worker.py"))
        self.assertFalse(p["closure"]["ready"]); self.assertTrue(any("ancestor" in r for r in p["closure"]["reasons"]))
    def test_C07_status_read_failure_not_clean(self):
        repo=init_repo(self.root/"code"); real=guard.git
        def fail_status(root,*args,**kwargs):
            if "status" in args: return subprocess.CompletedProcess(args,124,stdout="",stderr="timeout")
            return real(root,*args,**kwargs)
        with patch.object(guard,"git",side_effect=fail_status):
            with self.assertRaises(ValueError): guard.inspect(repo,False)
    def test_C07_detached_not_fabricated_main(self):
        repo=init_repo(self.root/"code"); git(repo,"checkout","--detach")
        p=payload(cli("git_project_guard.py","--path",repo)); self.assertIsNone(p["branch"]); self.assertTrue(p["detached_head"])
    def test_user_unrelated_dirty_file_preserved(self):
        repo=init_repo(self.root/"code"); base=git(repo,"rev-parse","HEAD"); (repo/"user.txt").write_text("preserve")
        (repo/"worker.py").write_text("x"); git(repo,"add","--","worker.py"); git(repo,"commit","-m","task")
        p=payload(cli("git_project_guard.py","--path",repo,"--check-close","--baseline-head",base,"--task-path","worker.py"))
        self.assertTrue(p["closure"]["ready"]); self.assertIn("user.txt",p["closure"]["unrelated_dirty_paths"]); self.assertEqual((repo/"user.txt").read_text(),"preserve")
    def test_container_directory_not_initialized(self):
        parent=self.root/"projects"; child=parent/"one"; child.mkdir(parents=True); (child/"SKILL.md").write_text("x")
        p=cli("git_project_guard.py","--path",parent,"--init-if-missing"); self.assertEqual(p.returncode,2); self.assertFalse((parent/".git").exists())
    def test_unicode_and_rename_paths_preserved(self):
        repo=init_repo(self.root/"code"); (repo/"旧文件.py").write_text("x"); git(repo,"add","--","旧文件.py"); git(repo,"commit","-m","file"); base=git(repo,"rev-parse","HEAD")
        git(repo,"mv","旧文件.py","新文件.py")
        p=payload(cli("git_project_guard.py","--path",repo,"--check-close","--baseline-head",base,"--task-path","旧文件.py","--task-path","新文件.py"))
        self.assertFalse(p["closure"]["ready"]); self.assertIn("新文件.py",p["closure"]["dirty_task_paths"])
    def test_nested_sensitive_filename_detected(self):
        repo=init_repo(self.root/"code"); (repo/"nested").mkdir(); (repo/"nested/.env").write_text("SYNTHETIC=1")
        p=payload(cli("git_project_guard.py","--path",repo)); self.assertIn("nested/.env",p["warnings"]["sensitive_unignored"])


class PackageChecks(unittest.TestCase):
    def test_structure_and_all_reference_links(self):
        p=validate(ROOT); self.assertTrue(p["valid"],p["errors"])
    def test_four_workflows_and_thirteen_methods_preserved(self):
        self.assertEqual(ledger.EVENT_TYPES,{"expansion_gate","hypothesis","task_close","portfolio_review"})
        text=(ROOT/"references/capability-map.md").read_text()
        for name in ("信息溯源","信息压缩","事实观点拆分","MECE拆解","因果分离","逆向推演","链式推导","第一性原理","假设验证","瓶颈识别","机会成本","止损预设","正反论证"):
            self.assertIn(name,text)
    def test_rule_boundaries_present_not_behavioral_proof(self):
        text=(ROOT/"SKILL.md").read_text()
        for clause in ("只读优先于持久化","把当前助手本身列为承担者","就按更合适的路线交付","用户已明确否决的路线","只更新受影响部分","达到当前完成条件就交付"):
            self.assertIn(clause,text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
