import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import threads as t


class FixtureStores(unittest.TestCase):
    """Fake copies of every harness's store under one temporary home."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'Code' / 'repo'
        self.other = self.home / 'Code' / 'other'
        self.repo.mkdir(parents=True)
        self.other.mkdir(parents=True)
        env = {'WRANGLE_THREADS_HOME': str(self.home), 'XDG_DATA_HOME': str(self.home / '.local' / 'share'),
               'CODEX_HOME': '', 'CLAUDE_CONFIG_DIR': '', 'GEMINI_HOME': ''}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write_codex()
        self.write_claude()
        self.write_agy()
        self.write_opencode()

    def write_codex(self):
        root = self.home / '.codex'
        rollout = root / 'sessions' / '2026' / '10' / '24' / 'rollout-codex-1.jsonl'
        rollout.parent.mkdir(parents=True)
        records = [
            {'type': 'session_meta', 'payload': {'id': 'codex-1', 'cwd': str(self.repo), 'timestamp': '2026-10-24T10:00:00Z'}},
            {'type': 'event_msg', 'timestamp': '2026-10-24T10:00:01Z', 'payload': {'type': 'item_completed', 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': 'plan the controller refactor'}]}}},
            {'type': 'response_item', 'timestamp': '2026-10-24T10:00:02Z', 'payload': {'type': 'reasoning', 'encrypted_content': 'gAAAA-not-readable'}},
            {'type': 'response_item', 'timestamp': '2026-10-24T10:00:03Z', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'input': 'ls'}},
            {'type': 'event_msg', 'timestamp': '2026-10-24T10:00:04Z', 'payload': {'type': 'item_completed', 'item': {'type': 'AgentMessage', 'content': [{'type': 'Text', 'text': 'Here is the plan: split the controller into three actions.'}]}}},
        ]
        rollout.write_text('\n'.join(json.dumps(r) for r in records) + '\n', encoding='utf-8')
        db = sqlite3.connect(root / 'state_5.sqlite')
        db.execute('CREATE TABLE threads (id TEXT, name TEXT, title TEXT, first_user_message TEXT, model TEXT, cwd TEXT, git_branch TEXT, rollout_path TEXT, created_at_ms INTEGER, updated_at_ms INTEGER, recency_at_ms INTEGER, archived INTEGER, thread_source TEXT, source TEXT)')
        rows = [
            ('codex-1', 'Controller refactor plan', 'plan the controller refactor', 'plan the controller refactor', 'gpt-6-astra', str(self.repo), 'main', str(rollout), 1_000_000, 2_000_000, 2_000_000, 0, 'user', 'cli'),
            ('codex-2', None, '', 'unrelated', 'gpt-6.1-sol', str(self.other), 'main', str(rollout), 1_000_000, 3_000_000, 3_000_000, 0, 'user', 'cli'),
            ('codex-sub', None, '', '', 'gpt-6.1-sol', str(self.repo), 'main', str(rollout), 1_000_000, 4_000_000, 4_000_000, 0, 'subagent', '{"subagent":{}}'),
            ('codex-old', 'Archived one', '', '', 'gpt-6.1-sol', str(self.repo), 'main', str(rollout), 1_000_000, 5_000_000, 5_000_000, 1, 'user', 'cli'),
        ]
        db.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        db.commit()
        db.close()

    def write_claude(self):
        project = self.home / '.claude' / 'projects' / '-Code-repo'
        project.mkdir(parents=True)
        main = [
            {'type': 'user', 'cwd': str(self.repo), 'gitBranch': 'feature', 'timestamp': '2026-10-23T09:00:00Z', 'message': {'role': 'user', 'content': 'review the controller'}},
            {'type': 'ai-title', 'aiTitle': 'Controller review'},
            {'type': 'assistant', 'cwd': str(self.repo), 'timestamp': '2026-10-23T09:00:05Z', 'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'content': [{'type': 'thinking', 'thinking': ''}, {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'x'}}, {'type': 'text', 'text': 'The controller does too much.'}]}},
            {'type': 'user', 'cwd': str(self.repo), 'timestamp': '2026-10-23T09:00:06Z', 'isSidechain': True, 'message': {'role': 'user', 'content': 'sidechain prompt'}},
        ]
        (project / 'claude-1.jsonl').write_text('\n'.join(json.dumps(r) for r in main) + '\n', encoding='utf-8')
        side = [{'type': 'user', 'cwd': str(self.repo), 'timestamp': '2026-10-23T09:00:06Z', 'isSidechain': True, 'message': {'role': 'user', 'content': 'only a sidechain'}}]
        (project / 'claude-side.jsonl').write_text('\n'.join(json.dumps(r) for r in side) + '\n', encoding='utf-8')

    def write_agy(self):
        root = self.home / '.gemini' / 'antigravity-cli'
        root.mkdir(parents=True)
        db = sqlite3.connect(root / 'conversation_summaries.db')
        db.execute('CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, preview TEXT, step_count INTEGER, last_modified_time TEXT, last_user_input_time TEXT, workspace_uris TEXT, agent_name TEXT, parent_conversation_id TEXT)')
        db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?)',
                   ('agy-1', 'Controller audit', 'Controller audit', 12, '2026-10-22 08:00:00+00:00', '2026-10-22 08:00:00+00:00', json.dumps([f'file://{self.repo}']), '', ''))
        db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?)',
                   ('agy-sub', '', '', 3, '2026-10-22 08:30:00+00:00', '2026-10-22 08:30:00+00:00', json.dumps([f'file://{self.repo}']), 'research', 'agy-1'))
        db.commit()
        db.close()
        (root / 'history.jsonl').write_text(json.dumps({'display': 'audit the controller for me', 'timestamp': 1_761_120_000_000, 'conversationId': 'agy-1'}) + '\n', encoding='utf-8')
        chats = self.home / '.gemini' / 'tmp' / 'repo' / 'chats'
        chats.mkdir(parents=True)
        (chats / 'session-2026-10-20T10-00-abc.json').write_text(json.dumps({
            'sessionId': 'saved-1', 'startTime': '2026-10-20T10:00:00.000Z', 'lastUpdated': '2026-10-20T10:05:00.000Z',
            'messages': [{'type': 'user', 'content': [{'text': 'saved question about preact'}]}, {'type': 'gemini', 'content': 'saved answer'}],
        }), encoding='utf-8')

    def write_opencode(self):
        path = self.home / '.local' / 'share' / 'opencode' / 'opencode.db'
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, time_archived INTEGER)')
        db.execute('CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)')
        db.execute('CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)')
        db.execute('INSERT INTO session VALUES (?,?,?,?,?,?,?,?)', ('ses-1', None, str(self.repo), 'Controller split', 'big-pickle', 1_000, 6_000_000, None))
        db.execute('INSERT INTO message VALUES (?,?,?,?)', ('m1', 'ses-1', 1, json.dumps({'role': 'user', 'time': {'created': 1_000}})))
        db.execute('INSERT INTO message VALUES (?,?,?,?)', ('m2', 'ses-1', 2, json.dumps({'role': 'assistant', 'time': {'created': 2_000}})))
        db.execute('INSERT INTO part VALUES (?,?,?,?,?)', ('p1', 'm1', 'ses-1', 1, json.dumps({'type': 'text', 'text': 'split the controller'})))
        db.execute('INSERT INTO part VALUES (?,?,?,?,?)', ('p2', 'm2', 'ses-1', 2, json.dumps({'type': 'text', 'text': 'Done in three actions.'})))
        db.commit()
        db.close()

    def run_cli(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = t.main(list(argv))
        return code, output.getvalue()

    def test_stores_report_presence_and_readability(self):
        code, out = self.run_cli('stores')
        self.assertEqual(code, 0)
        rows = {r['harness']: r for r in json.loads(out)}
        self.assertTrue(all(rows[h]['present'] for h in t.HARNESSES))
        self.assertEqual(rows['agy']['readable'], 'prompts')
        self.assertEqual(rows['codex']['readable'], 'full')

    def test_list_filters_by_cwd_and_hides_subagents_and_archived(self):
        code, out = self.run_cli('list', '--cwd', str(self.repo))
        rows = json.loads(out)
        ids = [r['id'] for r in rows]
        self.assertEqual(code, 0)
        self.assertIn('codex-1', ids)
        self.assertNotIn('codex-2', ids)
        self.assertNotIn('codex-sub', ids)
        self.assertNotIn('codex-old', ids)
        self.assertNotIn('claude-side', ids)
        self.assertNotIn('agy-sub', ids)
        self.assertIn('ses-1', ids)
        _, widened = self.run_cli('list', '--any-cwd', '--include-subagents', '--include-archived')
        self.assertEqual({r['id'] for r in json.loads(widened)} - set(ids), {'codex-2', 'codex-sub', 'codex-old', 'claude-side', 'agy-sub', 'saved-1'})
        self.assertNotIn('sort_ms', rows[0])

    def test_family_words_pick_harness_and_model(self):
        _, out = self.run_cli('list', '--any-cwd', '--harness', 'astra', '--model', 'astra')
        self.assertEqual([r['id'] for r in json.loads(out)], ['codex-1'])
        _, out = self.run_cli('list', '--any-cwd', '--harness', 'opus')
        self.assertEqual({r['harness'] for r in json.loads(out)}, {'claude'})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli('list', '--harness', 'excel')

    def test_search_ranks_by_matched_words_and_reads_prompts_when_asked(self):
        _, out = self.run_cli('search', 'controller', 'refactor', '--cwd', str(self.repo))
        hits = json.loads(out)
        self.assertEqual(hits[0]['id'], 'codex-1')
        self.assertEqual(hits[0]['score'], 2)
        self.assertTrue(hits[0]['snippet'])
        _, out = self.run_cli('search', 'preact', '--any-cwd')
        self.assertEqual([h['id'] for h in json.loads(out)], ['saved-1'])
        _, out = self.run_cli('search', 'actions', '--cwd', str(self.repo))
        self.assertEqual(json.loads(out), [])
        _, out = self.run_cli('search', 'actions', '--cwd', str(self.repo), '--deep')
        self.assertEqual({h['id'] for h in json.loads(out)}, {'codex-1', 'ses-1'})

    def test_show_renders_turns_and_skips_what_is_not_readable(self):
        code, out = self.run_cli('show', 'codex', 'codex-1', '--inline')
        self.assertEqual(code, 0)
        self.assertIn('[user]', out)
        self.assertIn('split the controller into three actions', out)
        self.assertNotIn('gAAAA', out)
        self.assertNotIn('exec: ls', out)
        _, with_tools = self.run_cli('show', 'codex', 'codex-1', '--roles', 'user,assistant,tool', '--inline')
        self.assertIn('exec: ls', with_tools)
        _, claude = self.run_cli('show', 'claude', 'claude-1', '--inline')
        self.assertIn('The controller does too much.', claude)
        self.assertNotIn('sidechain prompt', claude)
        _, agy = self.run_cli('show', 'agy', 'agy-1', '--inline')
        self.assertIn('fidelity: prompts', agy)
        self.assertIn('audit the controller for me', agy)
        self.assertIn('resume it natively', agy)
        _, opencode = self.run_cli('show', 'opencode', 'ses-1', '--inline')
        self.assertIn('Done in three actions.', opencode)
        code, missing = self.run_cli('show', 'codex', 'nope')
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(missing)['ok'])

    def test_show_writes_to_a_file_on_request_or_when_large(self):
        target = self.home / 'out' / 'thread.txt'
        _, out = self.run_cli('show', 'codex', 'codex-1', '--out', str(target))
        self.assertEqual(json.loads(out)['file'], str(target))
        self.assertIn('[assistant]', target.read_text(encoding='utf-8'))
        with patch.object(t, 'MAX_INLINE_BYTES', 10):
            _, large = self.run_cli('show', 'codex', 'codex-1')
        spilled = Path(json.loads(large)['file'])
        self.addCleanup(spilled.unlink)
        self.assertIn('[user]', spilled.read_text(encoding='utf-8'))

    def test_resume_prints_native_commands_that_leave_the_thread_intact(self):
        _, out = self.run_cli('resume', 'codex', 'codex-1')
        resume = json.loads(out)['resume']
        self.assertIn('codex exec fork', resume['headless'])
        self.assertIn('--resume codex-1', resume['wrangle'])
        _, claude = self.run_cli('resume', 'claude', 'claude-1')
        self.assertIn('--fork-session', json.loads(claude)['resume']['headless'])
        _, agy = self.run_cli('resume', 'agy', 'agy-1')
        self.assertIn('--conversation agy-1', json.loads(agy)['resume']['interactive'])

    def test_since_accepts_durations_and_dates(self):
        self.assertIsNone(t.parse_since(None))
        self.assertLess(t.parse_since('7d'), t.parse_since('1h'))
        self.assertEqual(t.parse_since('2026-01-01'), 1_767_225_600_000)


if __name__ == '__main__':
    unittest.main()
