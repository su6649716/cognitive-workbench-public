#!/usr/bin/env python3
"""Self-contained project engineering checks; text presence is not model behavior."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

class ProjectBaselineContracts(unittest.TestCase):
    def read(self, path):
        return (ROOT/path).read_text(encoding='utf-8')

    def test_P01_single_skill_and_no_source_archive_dependency(self):
        self.assertEqual(list(ROOT.rglob('SKILL.md')), [ROOT/'SKILL.md'])
        self.assertFalse(list(ROOT.rglob('*.zip')))
        self.assertNotIn('$establish-project-baseline', self.read('agents/openai.yaml'))
        for p in (ROOT/'scripts').glob('*.py'):
            self.assertNotIn('provenance' + '/', p.read_text())

    def test_P02_current_entry_and_complete_engineering_routing(self):
        text=self.read('SKILL.md')
        self.assertIn('version: "2.4.0"',text)
        for name in ('project-baseline.md','project-baseline-defaults.md','engineering-baseline.md','architecture-baseline.md'):
            self.assertIn('(references/'+name+')',text)

    def test_P03_current_rules_remove_automatic_legacy_inheritance(self):
        for path in ('SKILL.md','references/project-baseline.md','references/architecture-baseline.md',
                     'references/project-baseline-defaults.md','references/interface-translation.md',
                     'assets/project-baseline/AGENT_ENTRY.md'):
            with self.subTest(path=path):
                text=self.read(path)
                self.assertIn('全新',text)
                self.assertNotIn('用户明确要求 → 项目已有有效约定',text)
        self.assertIn('维护任务', self.read('SKILL.md'))

    def test_P04_replaced_confirmation_not_revived(self):
        main=self.read('SKILL.md')
        self.assertIn('被本轮取代的决定',main)
        self.assertIn('不能再以旧基线曾被确认为由恢复',self.read('references/collaborative-definition.md'))

    def test_P05_new_design_is_not_permission_to_destroy(self):
        self.assertIn('独立设计不授权删除或覆盖原文件',self.read('SKILL.md'))
        self.assertIn('不擅自删除或覆盖原文件',self.read('assets/project-baseline/AGENT_ENTRY.md'))
        self.assertIn('在独立目标目录施工',self.read('references/engineering-baseline.md'))

    def test_P06_defaults_are_conditional_not_byte_identical_contract(self):
        text=self.read('references/project-baseline-defaults.md')
        self.assertIn('可由当前目标替换',text)
        for section in ('## 运行边界与技术选择','## 模块与数据','## 公共接口','## 界面与交互',
                        '## 外部服务与长任务','## 运行维护与用量','## 交付和持续修改'):
            self.assertIn(section,text)
        self.assertIn('骨架可改',self.read('assets/project-baseline/PROJECT_BASELINE.md'))

    def test_P07_concrete_choices_and_delivery_not_just_documentation(self):
        text=self.read('references/project-baseline.md')
        for phrase in ('已确定、已实现、已验证','开发任务不能只交规范','设计：','实现：','生产：',
                       '写入责任','空白骨架','拟建','product_docs','公共约定变动'):
            self.assertIn(phrase,text)

    def test_P08_authorization_and_single_current_project_source(self):
        text=self.read('references/project-baseline.md')
        for phrase in ('项目级','不重复追加','只读审查不因发现缺基线而写','不新建记忆库','user_confirmed',
                       '不修改 `~/.codex/AGENTS.md`','被本轮明确替代','全新独立目录不复制旧项目入口'):
            self.assertIn(phrase,text)

    def test_P09_links_and_templates_are_within_package(self):
        for doc in [ROOT/'SKILL.md', *sorted((ROOT/'references').glob('*.md'))]:
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', doc.read_text()):
                if '://' in target or target.startswith('#'): continue
                resolved=(doc.parent/target.split('#')[0]).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                self.assertTrue(resolved.is_file(),f'{doc.name}: {target}')
        for name in ('AGENT_ENTRY.md','PROJECT_BASELINE.md'):
            self.assertTrue((ROOT/'assets/project-baseline'/name).is_file())

    def test_P10_current_sources_not_historical_installation(self):
        text=self.read('references/sources.md')
        self.assertIn('只有认知助手一个入口',text)
        self.assertIn('不要求加载任何旧版',text)
        self.assertNotIn('逐字节一致',text)


class ProjectIndexContracts(unittest.TestCase):
    """Real CLI operations on isolated project/ledger paths."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cw-project-baseline-')
        self.path=Path(self.tmp.name)
        self.project=self.path/'project';self.project.mkdir()
        (self.project/'docs').mkdir()
        self.old_doc=self.project/'docs/architecture.md';self.old_doc.write_text('# Existing architecture\n',encoding='utf-8')
        self.new_doc=self.project/'docs/PROJECT_BASELINE.md';self.new_doc.write_text('# Fixture baseline\n已确定；并不证明用户确认。\n',encoding='utf-8')
        self.index=self.path/'state/index.json'
        self.env={**os.environ,'COGNITIVE_HOME':str(self.path/'isolated-home'),
                  'PYTHONDONTWRITEBYTECODE':'1','GIT_CONFIG_GLOBAL':os.devnull,
                  'GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0'}
        self.plan={'purpose_status':'assistant_inferred','boundary_status':'draft','design_status':'draft',
                   'baseline_status':'current','baseline_source':'synthetic-not-user-confirmation'}
        self.base=self.upsert('--product-doc',str(self.old_doc),'--status','active',
                              '--planning-baseline-json',json.dumps(self.plan))
        self.assertEqual(self.base.returncode,0,self.base.stderr)
        self.initial=json.loads(self.base.stdout)
    def tearDown(self):self.tmp.cleanup()
    def cli(self,*args):
        return subprocess.run([sys.executable,'-S',str(ROOT/'scripts/project_index.py'),'--index',str(self.index),*args],
                              text=True,capture_output=True,env=self.env,timeout=15)
    def upsert(self,*args):
        return self.cli('upsert','--name','Baseline fixture','--root',str(self.project),*args)

    def test_I09_baseline_document_registration_preserves_old_state(self):
        result=self.upsert('--product-doc',str(self.new_doc))
        self.assertEqual(result.returncode,0,result.stderr)
        item=json.loads(result.stdout)
        self.assertEqual(item['id'],self.initial['id'])
        self.assertEqual(item['status'],'active')
        self.assertEqual(item['planning_baseline'],self.plan)
        self.assertEqual(set(item['product_docs']),{str(self.new_doc),str(self.old_doc)})

    def test_I10_missing_planned_document_is_not_registered_as_existing(self):
        before=self.index.read_bytes()
        result=self.upsert('--product-doc',str(self.project/'docs/not-yet-built.md'))
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.index.read_bytes(),before)

    def test_I11_repeated_document_registration_is_idempotent_in_identity(self):
        for _ in range(2):self.assertEqual(self.upsert('--product-doc',str(self.new_doc)).returncode,0)
        index=json.loads(self.index.read_text(encoding='utf-8'))
        self.assertEqual(len(index['projects']),1)
        item=index['projects'][0]
        self.assertEqual(item['id'],self.initial['id'])
        self.assertEqual(item['product_docs'].count(str(self.new_doc)),1)

    def test_I12_read_only_lookup_does_not_change_state_or_create_entries(self):
        self.assertEqual(self.upsert('--product-doc',str(self.new_doc)).returncode,0)
        before=self.index.read_bytes()
        reply=self.cli('find','--query','PROJECT_BASELINE.md')
        self.assertEqual(reply.returncode,0,reply.stderr)
        self.assertEqual(len(json.loads(reply.stdout)),1)
        self.assertEqual(self.index.read_bytes(),before)
        self.assertFalse((self.project/'AGENTS.md').exists())
        self.assertFalse((self.path/'isolated-home/memories/COGNITIVE_EVENTS.jsonl').exists())

    def test_I13_engineering_document_does_not_bypass_semantic_gate(self):
        self.new_doc.write_text('# Fixture\n已验证\n',encoding='utf-8')
        self.assertEqual(self.upsert('--product-doc',str(self.new_doc)).returncode,0)
        reply=self.cli('gate','--query','Baseline fixture','--risk','high','--target-stage','full','--user-facing')
        self.assertEqual(reply.returncode,1,reply.stderr)
        self.assertFalse(json.loads(reply.stdout)['allowed'])
        self.assertEqual(json.loads(self.index.read_text())['projects'][0]['planning_baseline'],self.plan)


    def test_P11_new_project_has_no_automatic_previous_baseline(self):
        fresh=self.path/'fresh';fresh.mkdir()
        before_plan=dict(self.plan)
        reply=self.cli('upsert','--name','Fresh project','--root',str(fresh),'--status','active')
        self.assertEqual(reply.returncode,0,reply.stderr)
        created=json.loads(reply.stdout)
        self.assertNotEqual(created['id'],self.initial['id'])
        self.assertNotIn('planning_baseline',created)
        self.assertNotIn('interface_baseline',created)
        self.assertEqual(created.get('product_docs',[]),[])
        projects=json.loads(self.index.read_text())['projects']
        current=next(x for x in projects if x['id']==self.initial['id'])
        self.assertEqual(current['planning_baseline'],before_plan)


if __name__=='__main__':
    unittest.main(verbosity=2)
