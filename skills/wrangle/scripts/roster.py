#!/usr/bin/env python3
"""List native harness models, refreshing a local cache after 24 hours."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

from wrangle import Failure, launch, objects

HARNESSES = ('claude', 'agy', 'codex')
TTL = 24 * 60 * 60


def codex_models(cwd, timeout):
    """Request model metadata without creating a thread or starting a turn."""
    argv = ['codex', 'app-server', '-c', 'model_provider="openai"',
            '-c', 'forced_login_method="chatgpt"']
    with subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as proc, selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        buffer = b''

        def request(message):
            nonlocal buffer
            proc.stdin.write((json.dumps(message) + '\n').encode())
            proc.stdin.flush()
            if 'id' not in message:
                return None
            while time.monotonic() < deadline:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    row = json.loads(line)
                    if row.get('id') == message['id']:
                        if 'error' in row:
                            raise Failure('Codex model-list request failed: ' + str(row['error']))
                        return row['result']
                if selector.select(max(0, deadline - time.monotonic())):
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        raise Failure('Codex closed before returning its model list')
                    buffer += chunk
            raise Failure('Codex model-list request timed out', 5)

        try:
            request(dict(id=1, method='initialize', params=dict(clientInfo=dict(name='wrangle', version='1'))))
            request(dict(method='initialized', params={}))
            models, seen = [], set()
            cursor = None
            while True:
                page = request(dict(id=2, method='model/list', params=dict(limit=100, cursor=cursor)))
                models.extend(dict(id=m['model'], name=m['displayName'],
                                   efforts=[e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                                   default_effort=m.get('defaultReasoningEffort')) for m in page['data'])
                cursor = page.get('nextCursor')
                if not cursor:
                    return models
                if cursor in seen:
                    raise Failure('Codex returned a repeated model-list cursor')
                seen.add(cursor)
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def discover(harness, timeout):
    with tempfile.TemporaryDirectory(prefix='wrangle-roster-') as cwd:
        if harness == 'codex':
            return codex_models(cwd, timeout)
        if harness == 'claude':
            argv = ['claude', '--print', '--input-format', 'stream-json', '--output-format',
                    'stream-json', '--verbose', '--no-session-persistence', '--strict-mcp-config',
                    '--mcp-config', '{"mcpServers":{}}', '--settings', '{"disableAllHooks":true}']
            message = dict(type='control_request', request_id='roster', request=dict(subtype='initialize'))
            code, out, _ = launch(argv, cwd, timeout, json.dumps(message) + '\n')
            if code == 0:
                for row in objects(out):
                    response = row.get('response', {})
                    if row.get('type') == 'control_response' and response.get('request_id') == 'roster':
                        return [dict(id=m['value'], name=m['displayName'], resolved_id=m.get('resolvedModel'),
                                     efforts=m.get('supportedEffortLevels', []))
                                for m in response.get('response', {})['models']]
        else:
            code, out, _ = launch(['agy', 'models'], cwd, timeout)
            if code == 0:
                return [dict(id=parts[0].strip(), name=parts[1].strip())
                        for line in out.splitlines() if len(parts := line.split('\t', 1)) == 2]
    raise Failure(f'{harness} did not return a model list; check its login and installed version')


def cached_roster(harness, root, refresh=False, offline=False, timeout=120):
    path = root / f'{harness}.json'
    try:
        cached = json.loads(path.read_text())
        if not isinstance(cached.get('models'), list) or not isinstance(cached.get('fetched_at'), (int, float)):
            cached = {}
    except (OSError, ValueError, AttributeError):
        cached = {}
    stale = not cached or time.time() - cached['fetched_at'] >= TTL
    error = None
    if not offline and (refresh or stale):
        try:
            models = discover(harness, timeout)
            if not models:
                raise Failure(f'{harness} returned an empty model list')
            fresh = dict(harness=harness, fetched_at=time.time(), models=models)
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as fh:
                temp = Path(fh.name)
                json.dump(fresh, fh, indent=2)
            try:
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
            cached, stale = fresh, False
        except (Failure, OSError, ValueError, KeyError, TypeError) as exc:
            error, stale = str(exc), True
    return dict(harness=harness, fetched_at=cached.get('fetched_at'), stale=stale,
                error=error, models=cached.get('models', []))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('harness', nargs='?', choices=HARNESSES)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--refresh', action='store_true', help='fetch even if the cache is fresh')
    mode.add_argument('--offline', action='store_true', help='read cached data only')
    ap.add_argument('--timeout', type=int, default=120, help='seconds per harness refresh')
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error('--timeout must be positive')
    root = Path(os.environ.get('WRANGLE_HOME', Path.home() / '.config' / 'wrangle')) / 'roster'
    results = [cached_roster(h, root, args.refresh, args.offline, args.timeout)
               for h in ([args.harness] if args.harness else HARNESSES)]
    print(json.dumps(results, indent=2))
    return int(any(r['error'] or not r['models'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
