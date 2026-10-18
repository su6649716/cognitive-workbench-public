#!/usr/bin/env python3
"""Local contracts and real metadata tests, not model-behavior evaluations."""
from __future__ import annotations
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT/'scripts/closeout_inventory.py'
spec = importlib.util.spec_from_file_location('closeout_inventory', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cw23-knowledge-')
        self.parent = Path(self.temp.name)
        self.project = self.parent/'project'; self.project.mkdir()
        self.env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'GIT_CONFIG_NOSYSTEM':'1',
                    'GIT_CONFIG_GLOBAL':os.devnull, 'GIT_TERMINAL_PROMPT':'0'}
    def tearDown(self): self.temp.cleanup()
    def put(self, rel, value='fixture'):
        p=self.project/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value,encoding='utf-8');return p
    def cli(self,*args):
        return subprocess.run([sys.executable,'-S',str(SCRIPT),'--project',str(self.project),*args],
                              capture_output=True,text=True,env=self.env,timeout=20)
    def git(self,*args):
        p=subprocess.run(['git','-C',str(self.project),*args],capture_output=True,env=self.env,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr);return p.stdout
    def start_git(self):
        self.git('init','-b','main');self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
    def snapshot(self):
        return {str(p.relative_to(self.project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.project.rglob('*') if p.is_file() and not p.is_symlink()}

    def test_non_git_project_metadata_without_contents(self):
        self.put('README.md','private marker MUST_NOT_OUTPUT');self.put('.env','secret-marker-not-real')
        p=self.cli();self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout);self.assertEqual(data['git']['status'],'not-applicable')
        self.assertNotIn('MUST_NOT_OUTPUT',p.stdout);self.assertNotIn('secret-marker',p.stdout)
        self.assertEqual(data['markdown'][0]['path'],'README.md')
        self.assertFalse(data['content_read'])
    def test_inventory_has_no_write_or_generated_memory_mutation(self):
        self.put('AGENTS.md');self.put('memory/MEMORY.md','machine generated -- leave alone')
        self.put('notes/PLAN.md');before=self.snapshot()
        data=mod.inventory(self.project);self.assertEqual(self.snapshot(),before)
        self.assertEqual(data['memory_candidates'][0]['path'],'memory/MEMORY.md')
        self.assertTrue(data['read_only'])
    def test_dependencies_and_builds_are_excluded(self):
        self.put('node_modules/pkg/README.md');self.put('dist/README.md');self.put('docs/use.mdx')
        data=mod.inventory(self.project)
        self.assertEqual([x['path'] for x in data['markdown']],['docs/use.mdx'])
        self.assertIn('node_modules',data['excluded_directories'])
    def test_symlinks_not_followed_and_external_content_not_read(self):
        outside=self.parent/'outside';outside.mkdir();(outside/'SECRET.md').write_text('not read')
        (self.project/'external').symlink_to(outside,target_is_directory=True)
        (self.project/'BROKEN.md').symlink_to('missing.md')
        self.put('README.md');(self.project/'alias.md').symlink_to('README.md')
        data=mod.inventory(self.project)
        self.assertNotIn('external/SECRET.md',[x['path'] for x in data['entries']])
        states={x['path']:x['link_state'] for x in data['symlinks']}
        self.assertEqual(states['external'],'external-target-unverified')
        self.assertEqual(states['BROKEN.md'],'broken');self.assertEqual(states['alias.md'],'internal-existing')
    def test_ancestor_rules_opt_in_without_body_access(self):
        (self.parent/'AGENTS.md').write_text('parent-private-marker')
        self.assertEqual(mod.inventory(self.project)['ancestor_rule_candidates'],[])
        data=mod.inventory(self.project,ancestor_rules=True)
        self.assertTrue(any(x['path']==str(self.parent/'AGENTS.md') for x in data['ancestor_rule_candidates']))
        self.assertNotIn('parent-private-marker',json.dumps(data))
    def test_limit_reports_partial_instead_of_complete(self):
        for n in range(5):self.put(f'{n}.md')
        p=self.cli('--max-entries','2');self.assertEqual(p.returncode,1)
        data=json.loads(p.stdout);self.assertTrue(data['truncated']);self.assertFalse(data['inventory_complete'])
        self.assertEqual(data['entries_seen'],2)
    def test_limit_counts_directories_too(self):
        for n in range(8):(self.project/f'd{n}').mkdir()
        data=mod.inventory(self.project,max_entries=3)
        self.assertEqual(data['entries_seen'],3);self.assertTrue(data['truncated'])
    def test_bad_input_and_zero_limit_return_two(self):
        p=self.cli('--max-entries','0');self.assertEqual(p.returncode,2)
        self.assertEqual(json.loads(p.stdout)['status'],'invalid-input')
        p=subprocess.run([sys.executable,'-S',str(SCRIPT),'--project',str(self.parent/'missing')],capture_output=True,text=True)
        self.assertEqual(p.returncode,2)
    def test_rule_candidates_include_nested_override_without_loaded_claim(self):
        for rel in ['AGENTS.md','src/AGENTS.override.md','.claude/rules/backend.md','.cursor/rules/api.mdc']:
            self.put(rel)
        data=mod.inventory(self.project)
        self.assertEqual(len(data['rule_candidates']),4)
        self.assertFalse(any('loaded' in x for x in data['rule_candidates']))
    def test_residue_name_is_only_candidate_not_delete(self):
        for name in ['PLAN.md','server_old.py','important.py']:self.put(name)
        data=mod.inventory(self.project)
        self.assertEqual({x['path'] for x in data['residue_candidates']},{'PLAN.md','server_old.py'})
        self.assertTrue(all(not x['deletion_authorized'] for x in data['residue_candidates']))
        self.assertTrue((self.project/'server_old.py').exists())
    def test_empty_git_repository_is_not_an_error(self):
        self.start_git();data=mod.inventory(self.project)
        self.assertEqual(data['git']['head_state'],'unborn');self.assertEqual(data['git']['branch'],'main')
        self.assertEqual(data['git']['status'],'verified-metadata')
    def test_dirty_names_newlines_and_index_bytes_preserved(self):
        self.start_git();self.put('a file.txt');self.git('add','--','a file.txt');self.git('commit','-m','base')
        index=self.project/'.git/index';before=index.read_bytes()
        self.put('a file.txt','changed');self.put('换行\n文件.md')
        data=mod.inventory(self.project)
        self.assertTrue(data['git']['dirty'])
        self.assertEqual({x['path'] for x in data['git']['changes']},{'a file.txt','换行\n文件.md'})
        self.assertEqual(index.read_bytes(),before)
    def test_staged_rename_preserves_original_and_target(self):
        self.start_git();self.put('old name.md');self.git('add','--','old name.md');self.git('commit','-m','base')
        self.git('mv','--','old name.md','new name.md')
        data=mod.inventory(self.project)
        row=data['git']['changes'][0]
        self.assertEqual(row['path'],'new name.md');self.assertEqual(row['original_path'],'old name.md')
    def test_detached_head_not_falsely_named_main(self):
        self.start_git();self.put('README.md');self.git('add','README.md');self.git('commit','-m','base')
        self.git('checkout','--detach')
        data=mod.inventory(self.project)
        self.assertEqual(data['git']['branch_state'],'detached');self.assertIsNone(data['git']['branch'])
    def test_git_failure_or_unavailable_never_report_clean(self):
        with patch.object(mod,'run_git',side_effect=FileNotFoundError()):
            data=mod.git_inventory(self.project)
        self.assertEqual(data['status'],'unavailable');self.assertIsNone(data['dirty'])
        with patch.object(mod,'run_git',return_value=subprocess.CompletedProcess([],128,b'',b'permission denied')):
            data=mod.git_inventory(self.project)
        self.assertEqual(data['status'],'error');self.assertIsNone(data['dirty'])
    def test_metadata_scan_error_is_partial(self):
        with patch.object(mod.os,'scandir',side_effect=PermissionError('fixture')):
            data=mod.inventory(self.project)
        self.assertFalse(data['inventory_complete']);self.assertTrue(data['errors'])
    def test_porcelain_malformed_rename_is_rejected(self):
        with self.assertRaises(ValueError):mod.parse_status(b'R  target\0')
    def test_help_does_not_offer_mutation_or_network(self):
        p=self.cli('--help');self.assertEqual(p.returncode,0)
        for flag in ['--delete','--apply','--push','--deploy']:self.assertNotIn(flag,p.stdout)


class KnowledgeContracts(unittest.TestCase):
    def read(self,p):return (ROOT/p).read_text(encoding='utf-8')
    def test_one_skill_with_current_version_and_direct_reference(self):
        self.assertEqual(list(ROOT.rglob('SKILL.md')),[ROOT/'SKILL.md'])
        self.assertIn('version: "2.4.1"',self.read('SKILL.md'))
        self.assertIn('(references/knowledge-closeout.md)',self.read('SKILL.md'))
        self.assertNotIn('$neat-freak',self.read('agents/openai.yaml'))
    def test_no_new_event_type_or_memory_store(self):
        from cognitive_ledger import EVENT_TYPES
        self.assertEqual(EVENT_TYPES,{'expansion_gate','hypothesis','task_close','portfolio_review'})
        text=self.read('references/knowledge-closeout.md')
        self.assertIn('同一问题更新已有 `task_close`',text);self.assertIn('不新增独立洁癖事件',text)
    def test_readonly_generated_memory_and_history_boundaries(self):
        text=self.read('references/knowledge-closeout.md')
        for phrase in ['平台生成记忆默认只读','账本用 update 保留原预测和历史','只读检查','不改项目']:
            self.assertIn(phrase,text)
    def test_no_code_as_automatic_normative_truth(self):
        text=self.read('references/knowledge-closeout.md')
        self.assertIn('不得把 bug 反写为正确需求',text)
        self.assertIn('文件候选不等于实际加载链',text)
    def test_closeout_does_not_expand_to_all_workspaces_or_release(self):
        text=self.read('references/knowledge-closeout.md')
        self.assertIn('用户明确要求整个 workspace 才扩大内容审计',text)
        self.assertIn('没有部署目标就不检查生产',text)
        self.assertIn('不因存在一个 Git remote 自动启动发布流程',text)
    def test_publish_evidence_and_post_report_cleanup(self):
        text=self.read('references/knowledge-closeout.md')
        for phrase in ['PR 合并不证明部署','canonical 用户入口','取得用户看完本次结果后的明确确认',
                       '初始“做完清理”不替代','盘点脚本没有删除']:
            self.assertIn(phrase,text)
    def test_existing_closeout_and_engineering_both_route_to_alignment(self):
        for name in ['task-close.md','project-baseline.md','routing.md']:
            self.assertIn('(knowledge-closeout.md)',self.read('references/'+name))
        self.assertIn('最终字节',self.read('references/task-close.md'))
    def test_license_exact_and_no_old_archives_shipped(self):
        p=ROOT/'licenses/neat-freak-MIT.txt';data=p.read_bytes()
        digest=hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
        self.assertEqual(digest,'2d848c9f3fed5317d59e0b88cafa289b42a29d2d')
        self.assertFalse(list(ROOT.rglob('*.zip')))
        self.assertIn('322346ded8129436b3f64707789a73e732ae24d9',self.read('references/sources.md'))

if __name__=='__main__':unittest.main(verbosity=2)
