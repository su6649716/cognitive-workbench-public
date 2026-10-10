"""Tests for new-state isolation, project lifecycle, and trusted hook boundaries."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import project_session as ps
import codex_hook as hooks


def git(root, *args):
    res = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if res.returncode: raise RuntimeError(res.stderr)
    return res.stdout.strip()


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'new-v24-state'
        self.repo = self.root / 'app'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'README.md').write_text('hello', encoding='utf8')
        git(self.repo, 'add', 'README.md')
        git(self.repo, 'commit', '-qm', 'init')

    def test_01_2_4_initializes_only_new_dir(self):
        self.assertTrue(ps.init(self.home))
        self.assertFalse(ps.init(self.home))
        meta = json.loads((self.home/'STATE_INFO.json').read_text())
        self.assertEqual(meta['generation'], 'cognitive-workbench-2.4')
        self.assertEqual(ps.read_index(self.home)['projects'], [])

    def test_02_refuses_to_adopt_old_unmarked_state(self):
        (self.home/'memories').mkdir(parents=True)
        (self.home/ps.INDEX).write_text('{"projects":[]}')
        with self.assertRaises(ValueError): ps.init(self.home)

    def test_03_peek_has_no_writes_when_uninitialized(self):
        data = ps.begin(self.home, self.repo, readonly=True)
        self.assertFalse(data['registered'])
        self.assertFalse(self.home.exists())

    def test_04_register_idempotent_and_restores(self):
        first = ps.begin(self.home, self.repo)
        second = ps.begin(self.home, self.repo)
        self.assertEqual(first['project_id'], second['project_id'])
        self.assertEqual(len(ps.read_index(self.home)['projects']), 1)

    def test_05_manual_complete_preserves_identity(self):
        ps.begin(self.home, self.repo)
        done = ps.complete(self.home, self.repo, '本轮功能已完成')
        self.assertEqual(done['last_task']['summary'], '本轮功能已完成')
        self.assertEqual((self.home/ps.LEDGER).read_text(), '')

    def test_06_completion_rejects_fake_sha(self):
        ps.begin(self.home, self.repo)
        with self.assertRaises(ValueError): ps.complete(self.home, self.repo, 'test', checkpoint='fake')
        self.assertIsNone(ps.begin(self.home, self.repo)['last_task'])

    def test_07_completion_accepts_true_git_head(self):
        ps.begin(self.home, self.repo)
        sha = git(self.repo, 'rev-parse', 'HEAD')
        ps.complete(self.home, self.repo, 'done', checkpoint=sha)
        self.assertEqual(ps.begin(self.home, self.repo)['last_checkpoint'], sha)

    def test_08_hook_context_does_not_create_project_on_read(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            ev = {'hook_event_name':'SessionStart','cwd':str(self.repo),'session_id':'s1'}
            r = hooks.handle(ev)
        self.assertIn('additionalContext', r['hookSpecificOutput'])
        self.assertFalse(self.home.exists())

    def test_08b_readonly_user_prompt_does_not_initialize_state(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            hooks.handle({'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),
                          'session_id':'s','turn_id':'t','prompt':'只读审计，不修改'})
        self.assertFalse(self.home.exists())

    def test_09_hook_records_only_real_git_change(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            begin={'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'}
            stop={'hook_event_name':'Stop','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'}
            hooks.handle(begin)
            (self.repo/'README.md').write_text('changed',encoding='utf8')
            hooks.handle(stop)
        entry = ps.read_index(self.home)['projects'][0]
        self.assertEqual(entry['last_observed_change']['paths'], ['README.md'])
        self.assertIn('not semantic completion', entry['last_observed_change']['evidence'])
        self.assertEqual((self.home/ps.LEDGER).read_text(),'')

    def test_10_no_git_change_no_project_creation(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            hooks.handle({'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
            hooks.handle({'hook_event_name':'Stop','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
        self.assertEqual(ps.read_index(self.home)['projects'], [])

    def test_11_git_commit_tracks_changed_paths(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            hooks.handle({'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
            (self.repo/'new.py').write_text('pass',encoding='utf8')
            git(self.repo, 'add', 'new.py')
            git(self.repo, 'commit', '-qm', 'new module')
            hooks.handle({'hook_event_name':'Stop','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
        item = ps.read_index(self.home)['projects'][0]
        self.assertEqual(item['last_observed_change']['paths'], ['new.py'])
        self.assertNotEqual(item['last_observed_change']['before_head'],item['last_observed_change']['after_head'])

    def test_12_hook_repeat_stop_does_not_duplicate(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            hooks.handle({'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
            (self.repo/'README.md').write_text('changed')
            hooks.handle({'hook_event_name':'Stop','cwd':str(self.repo),'session_id':'s1','turn_id':'t1'})
            result=ps.observe_stop(self.home,self.repo,'s1','t1')
        self.assertFalse(result['updated'])
        self.assertEqual(len(ps.read_index(self.home)['projects']),1)

    def test_13_new_project_same_name_different_root_not_mixed(self):
        r2=self.root/'other-app';r2.mkdir();git(r2,'init','-q')
        ps.begin(self.home,self.repo,name='same-name')
        ps.begin(self.home,r2,name='same-name')
        ids=[p['id'] for p in ps.read_index(self.home)['projects']]
        self.assertEqual(len(set(ids)),2)

    def test_14_invalid_git_repo_or_broad_path_not_registered(self):
        self.assertFalse(ps.begin(self.home,self.root).get('project_detected'))
        self.assertFalse(self.home.exists())

    def test_15_single_prompt_replayed_does_not_reset_git_baseline(self):
        ps.init(self.home)
        ps.capture_baseline(self.home,self.repo,'s','t')
        before=json.loads(ps.runtime_marker(self.home,'s','t').read_text())['before']['fingerprint']
        (self.repo/'README.md').write_text('changed')
        ps.capture_baseline(self.home,self.repo,'s','t')
        again=json.loads(ps.runtime_marker(self.home,'s','t').read_text())['before']['fingerprint']
        self.assertEqual(before,again)

    def test_16_marker_is_required_for_old_state(self):
        ps.init(self.home)
        data=json.loads((self.home/'STATE_INFO.json').read_text())
        data['generation']='old'
        (self.home/'STATE_INFO.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError): ps.read_index(self.home)

    def test_17_sync_source_only_new_state(self):
        import sync_state
        ps.init(self.home)
        src = sync_state.source_map(self.home)
        self.assertEqual({y.as_posix() for _,y in src}, {ps.STATE_FILE, ps.INDEX, ps.LEDGER})
        blob, count = sync_state.collect(self.home)
        self.assertEqual(set(blob), {ps.STATE_FILE, ps.INDEX, ps.LEDGER})
        self.assertEqual(count, 0)

    def test_18_hook_does_not_consume_chat_prompt_as_project_fact(self):
        with patch.dict(os.environ, {'COGNITIVE_HOME': str(self.home)}):
            hooks.handle({'hook_event_name':'UserPromptSubmit','cwd':str(self.repo),'session_id':'s','turn_id':'t','prompt':'秘密用户数据'})
        for item in (self.home/'runtime').iterdir():
            if item.suffix == '.json':
                self.assertNotIn('秘密用户数据', item.read_text())
        self.assertNotIn('秘密用户数据', json.dumps(ps.read_index(self.home),ensure_ascii=False))

if __name__ == '__main__': unittest.main()
