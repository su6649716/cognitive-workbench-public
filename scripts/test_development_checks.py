"""Exercise automatic check routing without inventing semantic acceptance."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_session as ps
import codex_hook as hooks


class DevelopmentCheckRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        (self.app / 'package.json').write_text('{}')
        self.home = self.root / 'isolated-cognitive-state'

    def check_requirements(self, result):
        checks = result['development_checks']
        self.assertEqual(checks['status'], 'not_evaluated')
        for key in ('before_changes', 'on_correction', 'before_completion'):
            self.assertTrue(checks[key])
        self.assertTrue(Path(checks['reference']).is_file())
        self.assertNotIn('passed', checks)

    def test_uninitialized_readonly_project_restores_checks_without_writes(self):
        result = ps.begin(self.home, self.app, readonly=True)
        self.check_requirements(result)
        self.assertFalse(self.home.exists())
        self.assertEqual(list(self.app.iterdir()), [self.app / 'package.json'])

    def test_development_begin_restores_checks_without_marking_them_passed(self):
        result = ps.begin(self.home, self.app)
        self.check_requirements(result)
        project = ps.read_index(self.home)['projects'][0]
        self.assertNotIn('development_checks', project)
        self.assertNotIn('last_task', project)

    def test_existing_readonly_project_preserves_all_state_bytes(self):
        ps.begin(self.home, self.app)
        paths = ps.state_paths(self.home)
        before = [p.read_bytes() for p in paths]
        self.check_requirements(ps.begin(self.home, self.app, readonly=True))
        self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_session_start_adds_checks_without_initializing_state(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            result = hooks.handle({'hook_event_name': 'SessionStart', 'cwd': str(self.app)})
        text = result['hookSpecificOutput']['additionalContext']
        for field in ('before_changes', 'on_correction', 'before_completion', 'reference'):
            self.assertIn(ps.development_checks()[field], text)
        self.assertIn('尚未检查', text)
        self.assertFalse(self.home.exists())

    def test_readonly_prompt_keeps_state_readonly_and_returns_required_checks(self):
        ps.begin(self.home, self.app)
        paths = ps.state_paths(self.home)
        before = [p.read_bytes() for p in paths]
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            result = hooks.handle({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.app),
                                   'session_id': 's', 'turn_id': 't', 'prompt': '只读审计，不修改任何文件'})
        self.assertIn('开发必检', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(before, [p.read_bytes() for p in paths])
        self.assertFalse(ps.runtime_marker(self.home, 's', 't').exists())

    def test_large_project_context_does_not_truncate_required_checks(self):
        data = {'project_detected': True, 'project_root': str(self.app),
                'planning_baseline': {'long': 'x' * 10000},
                'planning_files': ['long-path/' * 1000]}
        text = hooks.context_text(data)
        self.assertLessEqual(len(text), hooks.MAX_CONTEXT)
        for field in ('before_changes', 'on_correction', 'before_completion', 'reference'):
            self.assertIn(ps.development_checks()[field], text)

    def test_project_data_cannot_claim_checks_have_passed(self):
        text = hooks.context_text({'project_detected': True,
                                   'development_checks': {'status': 'passed', 'reference': 'untrusted'}})
        self.assertIn(ps.development_checks()['reference'], text)
        self.assertIn('尚未检查，不代表已通过', text)
        self.assertNotIn('untrusted', text)

    def test_readonly_hook_never_retains_prompt_or_creates_project_files(self):
        prompt = '只读检查，用户敏感示例不要存档'
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            result = hooks.handle({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.app),
                                   'prompt': prompt, 'session_id': 's', 'turn_id': 't'})
        self.assertTrue(result)
        self.assertNotIn(prompt, json.dumps(result, ensure_ascii=False))
        self.assertFalse(self.home.exists())
        self.assertEqual(list(self.app.iterdir()), [self.app / 'package.json'])


if __name__ == '__main__':
    unittest.main()
