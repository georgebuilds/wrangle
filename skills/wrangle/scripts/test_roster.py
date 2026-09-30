import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import roster as r
from wrangle import Failure


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = dict(harness='agy', fetched_at=time.time() - 2*r.TTL, models=[dict(id='old', name='Old')])
        (self.root / 'agy.json').write_text(json.dumps(self.old))

    def test_stale_refresh_replaces_removed_models(self):
        with patch.object(r, 'discover', return_value=[dict(id='new', name='New')]) as discover:
            result = r.cached_roster('agy', self.root)
        discover.assert_called_once()
        self.assertFalse(result['stale'])
        self.assertEqual(result['models'], [dict(id='new', name='New')])
        self.assertGreater(result['fetched_at'], self.old['fetched_at'])

    def test_fresh_cache_skips_discovery_and_force_refreshes(self):
        self.old['fetched_at'] = time.time()
        (self.root / 'agy.json').write_text(json.dumps(self.old))
        with patch.object(r, 'discover', return_value=[dict(id='new', name='New')]) as discover:
            r.cached_roster('agy', self.root)
            discover.assert_not_called()
            r.cached_roster('agy', self.root, refresh=True)
            discover.assert_called_once()

    def test_failure_retains_timestamp_and_models(self):
        for error in [Failure('offline'), FileNotFoundError('not installed')]:
            with patch.object(r, 'discover', side_effect=error):
                result = r.cached_roster('agy', self.root, refresh=True)
            self.assertTrue(result['stale'])
            self.assertTrue(result['error'])
            self.assertEqual(result['fetched_at'], self.old['fetched_at'])
            self.assertEqual(result['models'], self.old['models'])
            self.assertEqual(json.loads((self.root / 'agy.json').read_text()), self.old)

    def test_offline_never_calls_cli(self):
        with patch.object(r, 'discover', side_effect=AssertionError('offline')):
            result = r.cached_roster('agy', self.root, offline=True)
            missing = r.cached_roster('codex', self.root, offline=True)
        self.assertTrue(result['stale'])
        self.assertEqual(result['models'], self.old['models'])
        self.assertIsNone(missing['fetched_at'])

    def test_empty_discovery_cannot_replace_good_roster(self):
        with patch.object(r, 'discover', return_value=[]):
            result = r.cached_roster('agy', self.root)
        self.assertTrue(result['error'])
        self.assertEqual(result['models'], self.old['models'])

    def test_malformed_cache_can_be_refreshed(self):
        (self.root / 'agy.json').write_text('not json')
        with patch.object(r, 'discover', return_value=[dict(id='new', name='New')]):
            self.assertFalse(r.cached_roster('agy', self.root)['stale'])

    def test_claude_requests_metadata_and_keeps_no_account_data(self):
        payload = dict(type='control_response', response=dict(request_id='roster', response=dict(
            models=[dict(value='sonnet', displayName='Sonnet', resolvedModel='current-sonnet')],
            account={'email': 'private@example.test'})))
        with patch.object(r, 'launch', return_value=(0, json.dumps(payload), '')) as launch:
            models = r.discover('claude', 5)
        request = json.loads(launch.call_args.args[3])
        self.assertEqual(request['request']['subtype'], 'initialize')
        self.assertEqual(models, [dict(id='sonnet', name='Sonnet', resolved_id='current-sonnet', efforts=[])])

    def test_agy_parses_native_table(self):
        with patch.object(r, 'launch', return_value=(0, 'Fetching...\ngemini-new\tGemini New\n', '')):
            self.assertEqual(r.discover('agy', 5), [dict(id='gemini-new', name='Gemini New')])

    def test_codex_handshake_pagination_without_inference(self):
        binary = self.root / 'codex'
        binary.write_text('#!' + sys.executable + '\n' + '''
import json, sys
initialized = False
for line in sys.stdin:
    req = json.loads(line)
    method = req['method']
    if method == 'initialize':
        result = {}
    elif method == 'initialized':
        initialized = True
        continue
    elif method == 'model/list' and initialized:
        second = req['params']['cursor'] == 'next'
        result = {'data': [{'model': 'second' if second else 'first', 'displayName': 'Model', 'supportedReasoningEfforts': [{'reasoningEffort':'high'}]}], 'nextCursor': None if second else 'next'}
    else:
        raise RuntimeError('Unexpected request: ' + method)
    print(json.dumps({'id':req['id'], 'result':result}), flush=True)
''')
        binary.chmod(0o755)
        with patch.dict(os.environ, {'PATH': str(self.root)}):
            models = r.codex_models(self.root, 5)
        self.assertEqual([m['id'] for m in models], ['first', 'second'])
        self.assertEqual(models[0]['efforts'], ['high'])

    def test_partial_failure_does_not_hide_other_harnesses(self):
        def discover(harness, timeout):
            if harness == 'agy':
                raise Failure('unavailable')
            return [dict(id='model', name='Model')]
        out = io.StringIO()
        with patch.object(r, 'discover', side_effect=discover), patch.dict(os.environ, {'WRANGLE_HOME':str(self.root)}), contextlib.redirect_stdout(out):
            code = r.main(['--refresh'])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(len(result), 3)
        self.assertTrue(result[1]['error'])
        self.assertEqual(result[2]['models'][0]['id'], 'model')


if __name__ == '__main__':
    unittest.main()
