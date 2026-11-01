import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import wrangle as w


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.last = self.root / 'answer.txt'

    def test_modes_and_literal_prompt(self):
        prompt = 'Do not expand $(touch escaped) or `id`'
        for harness, flag, default, writable in [
            ('claude', '--permission-mode', 'plan', 'acceptEdits'),
            ('agy', '--mode', 'plan', 'accept-edits'),
            ('codex', '-s', 'read-only', 'workspace-write'),
        ]:
            for write, expected in [(False, default), (True, writable)]:
                args = w.parser().parse_args([harness] + (['--write'] if write else []))
                argv = w.command(args, prompt, self.last)
                self.assertEqual(argv[argv.index(flag) + 1], expected)
                self.assertIn(prompt, argv)

    def test_no_opencode_or_conflicting_permissions(self):
        for args in [['opencode'], ['claude', '--write', '--read-only']]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                w.parser().parse_args(args)

    def test_native_model_and_effort_are_preserved(self):
        args = w.parser().parse_args(['codex', '--model', 'explicit-model', '--effort', 'xhigh'])
        argv = w.command(args, 'test', self.last)
        self.assertEqual(argv[argv.index('-m') + 1], 'explicit-model')
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        self.assertIn('forced_login_method="chatgpt"', argv)
        self.assertIn('model_provider="openai"', argv)

    def test_resume_uses_each_harness_native_flag_without_mutating_the_thread(self):
        codex = w.command(w.parser().parse_args(['codex', '--resume', 'thread-1']), 'summarize', self.last)
        self.assertEqual(codex[:3], ['codex', 'exec', 'fork'])
        self.assertEqual(codex[-2:], ['thread-1', 'summarize'])
        self.assertIn('--ephemeral', codex)
        self.assertNotIn('-s', codex)
        self.assertIn('sandbox_mode="read-only"', codex)
        claude = w.command(w.parser().parse_args(['claude', '--resume', 'sess-1']), 'summarize', self.last)
        self.assertEqual(claude[claude.index('--resume') + 1], 'sess-1')
        self.assertIn('--fork-session', claude)
        agy = w.command(w.parser().parse_args(['agy', '--resume', 'conv-1']), 'summarize', self.last)
        self.assertEqual(agy[agy.index('--conversation') + 1], 'conv-1')

    def test_codex_service_tier_and_raw_config_pass_through(self):
        args = w.parser().parse_args(['codex', '--service-tier', 'ultrafast', '--config', 'model_reasoning_summary="detailed"'])
        argv = w.command(args, 'test', self.last)
        self.assertEqual(argv[argv.index('service_tier="ultrafast"') - 1], '-c')
        self.assertEqual(argv[argv.index('model_reasoning_summary="detailed"') - 1], '-c')
        self.assertNotIn('model_reasoning_effort="ultrafast"', argv)
        for harness in ('claude', 'agy'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                w.main([harness, '--prompt', 'x', '--service-tier', 'fast', '--dry-run'])

    def test_claude_result_and_nonzero_failure(self):
        out = 'diagnostic\n' + json.dumps(dict(type='result', subtype='success', result='answer',
                  total_cost_usd=0.02, usage={'input_tokens': 10}))
        result = w.parse_result('claude', 0, out, '', self.last)
        self.assertTrue(result['ok'])
        self.assertEqual(result['reported_cost_usd'], 0.02)
        self.assertFalse(w.parse_result('claude', 1, out, 'process failed', self.last)['ok'])

    def test_agy_success_and_failure(self):
        for status, ok in [('SUCCESS', True), ('ERROR', False)]:
            out = json.dumps(dict(status=status, response='partial or complete', error='quota'))
            result = w.parse_result('agy', 0, out, '', self.last)
            self.assertEqual(result['ok'], ok)
            self.assertIsNone(result['reported_cost_usd'])
            if not ok:
                self.assertEqual(result['error'], 'quota')

    def test_a_read_only_run_with_no_text_is_a_failure_that_names_what_it_was_denied(self):
        denied = [dict(action='command', display_name='RunCommand')]
        agy = json.dumps(dict(status='SUCCESS', response='', denied_actions=denied))
        result = w.parse_result('agy', 0, agy, '', self.last)
        self.assertFalse(result['ok'])
        self.assertIn('Empty response', result['error'])
        self.assertIn('RunCommand', result['error'])
        self.assertEqual(result['denied_actions'], denied)
        claude = json.dumps(dict(type='result', subtype='success', result='   '))
        self.assertFalse(w.parse_result('claude', 0, claude, '', self.last)['ok'])
        codex = '\n'.join(map(json.dumps, [dict(type='turn.completed', usage={})]))
        self.assertFalse(w.parse_result('codex', 0, codex, '', self.last)['ok'])

    def test_an_implementation_run_may_end_without_text(self):
        claude = json.dumps(dict(type='result', subtype='success', result=''))
        result = w.parse_result('claude', 0, claude, '', self.last, require_text=False)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['error'])

    def test_main_enforces_text_for_read_only_runs_only(self):
        empty = json.dumps(dict(status='SUCCESS', response='', conversation_id='c'))
        for flags, expected in [([], 1), (['--write'], 0)]:
            with patch.object(w, 'check_subscription'), patch.object(w, 'launch', return_value=(0, empty, '')):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(w.main(['agy', '--prompt', 'x', *flags]), expected)

    def test_codex_completion_and_failure(self):
        rows = [dict(type='thread.started', thread_id='session'),
                dict(type='item.completed', item=dict(type='agent_message', text='answer')),
                dict(type='turn.completed', usage={'output_tokens': 8})]
        out = '\n'.join(map(json.dumps, rows))
        result = w.parse_result('codex', 0, out, '', self.last)
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'answer')
        self.assertEqual(result['session_id'], 'session')
        failed = out + '\n' + json.dumps(dict(type='turn.failed', error={'message': 'plan gap'}))
        self.assertFalse(w.parse_result('codex', 0, failed, '', self.last)['ok'])
        self.assertFalse(w.parse_result('codex', 0, '', '', self.last)['ok'])

    def test_claude_auth_does_not_accept_api_or_missing_login(self):
        for status in [dict(loggedIn=True, authMethod='api_key'), dict(loggedIn=False)]:
            with patch.dict(os.environ, {}, clear=True), patch.object(w, 'launch', return_value=(0, json.dumps(status), '')):
                with self.assertRaises(w.Failure):
                    w.check_subscription('claude', self.root)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'secret-test'}, clear=True):
            with self.assertRaises(w.Failure) as failure:
                w.check_subscription('claude', self.root)
            self.assertNotIn('secret-test', str(failure.exception))

    def test_subscription_status(self):
        statuses = [('claude', json.dumps(dict(loggedIn=True, authMethod='claude.ai')), ''),
                    ('codex', '', 'Logged in using ChatGPT')]
        for harness, out, err in statuses:
            with patch.dict(os.environ, {}, clear=True), patch.object(w, 'launch', return_value=(0, out, err)):
                w.check_subscription(harness, self.root)
        with patch.object(w, 'launch', return_value=(0, '', 'Logged in using an API key')):
            with self.assertRaises(w.Failure):
                w.check_subscription('codex', self.root)

    def test_launch_closes_stdin(self):
        code, out, _ = w.launch([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], self.root, 5)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "''")

    def test_timeout_kills_children(self):
        marker = self.root / 'child-survived'
        child = f'import time, signal; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1); Path({str(marker)!r}).touch()'
        parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(10)'
        with self.assertRaises(w.Failure) as failure:
            w.launch([sys.executable, '-c', parent], self.root, 0.3)
        self.assertEqual(failure.exception.code, 5)
        time.sleep(1)
        self.assertFalse(marker.exists())

    def test_dry_run_is_offline_and_includes_files(self):
        source = self.root / 'source.txt'
        source.write_text('evidence', encoding='utf-8')
        output = io.StringIO()
        with patch.object(w, 'launch', side_effect=AssertionError('must be offline')), contextlib.redirect_stdout(output):
            code = w.main(['claude', '--prompt', 'question', '--file', str(source), '--dry-run'])
        self.assertEqual(code, 0)
        self.assertIn('evidence', json.loads(output.getvalue())['argv'][2])

    def test_end_to_end_with_fake_cli(self):
        binary = self.root / 'agy'
        binary.write_text('#!' + sys.executable + '\nimport json\nprint(json.dumps(dict(status="SUCCESS", response="answer")))\n')
        binary.chmod(0o755)
        result_file = self.root / 'nested' / 'result.json'
        with patch.dict(os.environ, {'PATH': str(self.root)}):
            proc = subprocess.run([sys.executable, str(Path(w.__file__)), 'agy', '--prompt', 'question',
                                   '--result-file', str(result_file)],
                                  capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['text'], 'answer')
        self.assertEqual(json.loads(result_file.read_text(encoding='utf-8')), json.loads(proc.stdout))

    def test_large_answer_is_file_backed_with_inline_override(self):
        answer = 'First line\n' + 'x' * 5000
        binary = self.root / 'agy'
        binary.write_text('#!' + sys.executable + '\nimport json\n'
                          + f'print(json.dumps(dict(status="SUCCESS", response={answer!r})))\n')
        binary.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(self.root)}):
            compact = subprocess.run(
                [sys.executable, str(Path(w.__file__)), 'agy', '--prompt', 'question',
                 '--output-dir', str(self.root)], capture_output=True, text=True, timeout=5)
            inline = subprocess.run(
                [sys.executable, str(Path(w.__file__)), 'agy', '--prompt', 'question', '--inline'],
                capture_output=True, text=True, timeout=5)
        self.assertEqual(compact.returncode, 0, compact.stderr)
        result = json.loads(compact.stdout)
        self.assertNotIn('text', result)
        self.assertEqual(result['bytes'], len(answer.encode('utf-8')))
        self.assertEqual(result['lines'], 2)
        path = Path(result['file'])
        self.assertEqual(path.parent.resolve(), self.root.resolve())
        self.assertEqual(path.read_text(encoding='utf-8'), answer)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(inline.stdout)['text'], answer)

    def test_codex_answer_survives_runner_cleanup(self):
        answer = 'Codex answer\n' + 'y' * 5000
        binary = self.root / 'codex'
        binary.write_text(
            '#!' + sys.executable + '\nimport json, sys\nfrom pathlib import Path\n'
            'if sys.argv[1:3] == ["login", "status"]:\n'
            '    print("Logged in using ChatGPT")\n'
            'else:\n'
            f'    Path(sys.argv[sys.argv.index("-o") + 1]).write_text({answer!r})\n'
            '    print(json.dumps(dict(type="turn.completed", usage={})))\n'
        )
        binary.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(self.root)}):
            proc = subprocess.run(
                [sys.executable, str(Path(w.__file__)), 'codex', '--prompt', 'question',
                 '--output-dir', str(self.root)], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertNotIn('text', result)
        self.assertEqual(Path(result['file']).read_text(encoding='utf-8'), answer)

    def test_transcript_is_readable_before_completion(self):
        transcript = self.root / 'native.jsonl'
        release = self.root / 'release'
        binary = self.root / 'agy'
        binary.write_text('#!' + sys.executable + '\nimport json,time\nfrom pathlib import Path\n'
                          'print(json.dumps(dict(status="PROGRESS", response="working")), flush=True)\n'
                          f'deadline=time.monotonic()+5\nwhile not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n'
                          'print(json.dumps(dict(status="SUCCESS", response="finished")), flush=True)\n')
        binary.chmod(0o755)
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        proc = subprocess.Popen([sys.executable, str(Path(w.__file__)), 'agy', '--prompt', 'question',
                                 '--transcript-file', str(transcript)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and (not transcript.exists() or 'working' not in transcript.read_text()):
                time.sleep(.01)
            self.assertIn('working', transcript.read_text())
            self.assertIsNone(proc.poll())
            release.touch()
            out, err = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, err)
            result = json.loads(out)
            self.assertEqual(result['text'], 'finished')
            self.assertEqual(result['transcript_file'], str(transcript.resolve()))
            self.assertEqual(transcript.stat().st_mode & 0o777, 0o600)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_transcript_preserves_partial_output_on_timeout(self):
        transcript = self.root / 'timeout.log'
        with self.assertRaises(w.Failure):
            w.launch([sys.executable, '-c', 'import time; print("started",flush=True); time.sleep(5)'],
                     self.root, .2, transcript_file=transcript)
        self.assertEqual(transcript.read_text(), 'started\n')

    def test_transcript_refuses_existing_files_and_symlinks(self):
        existing = self.root / 'existing'
        existing.write_text('keep')
        link = self.root / 'link'
        link.symlink_to(existing)
        for path in (existing, link):
            with self.assertRaises(FileExistsError):
                w.launch([sys.executable, '-c', 'print("changed")'], self.root, 1, transcript_file=path)
        self.assertEqual(existing.read_text(), 'keep')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(w.main(['agy', '--prompt', 'question', '--transcript-file', str(existing)]), 2)
        self.assertNotIn('transcript_file', json.loads(output.getvalue()))

    def test_transcript_dry_run_writes_nothing_and_result_path_is_distinct(self):
        transcript = self.root / 'dry.log'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(w.main(['agy', '--prompt', 'question', '--transcript-file', str(transcript), '--dry-run']), 0)
        self.assertFalse(transcript.exists())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            w.main(['agy', '--prompt', 'question', '--transcript-file', str(transcript), '--result-file', str(transcript)])


if __name__ == '__main__':
    unittest.main()
