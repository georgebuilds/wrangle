#!/usr/bin/env python3
"""List native harness models, refreshing a local cache after 24 hours."""
import argparse
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import time

from wrangle import Failure, launch, objects

HARNESSES = ('claude', 'agy', 'codex')
FAMILIES = {
    'claude': ('claude', 'opus', 'sonnet', 'haiku', 'fable'),
    'agy': ('gemini', 'argon', 'carbon', 'flash', 'pro'),
    'codex': ('gpt', 'astra', 'sol', 'luna', 'terra', 'o3', 'o4'),
}
TTL = 24 * 60 * 60


def infer_harness(spec):
    """Map '[harness:]model[:tier]' to (harness, model_spec)."""
    text = (spec or '').strip()
    if ':' in text and text.split(':', 1)[0] in HARNESSES:
        harness, rest = text.split(':', 1)
        if not rest.strip():
            raise Failure(f'Expected model name after {harness}: in {spec!r}')
        return harness, rest.strip()
    lower = text.lower()
    if lower.startswith('claude-'):
        return 'claude', text
    if lower.startswith(('gpt-', 'o3', 'o4')):
        return 'codex', text
    if lower.startswith('gemini-'):
        return 'agy', text
    tokens = [t for t in re.split(r'[:_\-\s/().]+', lower) if t]
    for harness, words in FAMILIES.items():
        if any(w in tokens for w in words):
            return harness, text
    raise Failure(f'Cannot infer harness for {spec!r}; prefix with claude:, codex:, or agy:')


def needs_resolution(harness, model):
    """True when model is a family alias or shorthand rather than an exact ID."""
    lower = model.strip().lower()
    if harness == 'claude':
        return not lower.startswith('claude-')
    tokens = [t for t in re.split(r'[:_\-\s/()]+', lower) if t]
    if harness == 'codex':
        return (':' in lower or lower in FAMILIES['codex']
                or any(lower.startswith(w + '-') or lower.startswith(w + ' ')
                       for w in ('astra', 'sol', 'luna', 'terra')))
    if harness == 'agy':
        if ':' in lower or lower in FAMILIES['agy']:
            return True
        if any(w in tokens for w in FAMILIES['agy']):
            return not re.fullmatch(r'gemini-[0-9.]+-[a-z0-9]+-(?:low|medium|high|max)', lower)
    return False


def match_model(harness, requested, models):
    """Resolve a requested family alias or shorthand against a harness's model list."""
    clean = requested.strip()
    for entry in models:
        if entry.get('id') == clean:
            return entry.get('resolved_id') or entry['id']
    tokens = [t for t in re.split(r'[:_\-\s/()]+', clean.lower()) if t]
    matches = []
    for entry in models:
        haystack = f"{entry.get('id', '')} {entry.get('name', '')}".lower()
        entry_tokens = set(re.split(r'[:_\-\s/()]+', haystack))
        if harness == 'agy' and any(t in FAMILIES['agy'] for t in tokens):
            if 'gemini' not in entry_tokens:
                continue
        if tokens and all(t in entry_tokens for t in tokens):
            matches.append(entry)
    if matches:
        if harness == 'agy' and not any(t in ('low', 'medium', 'high', 'max') for t in tokens):
            preferred = next((m for m in matches if str(m.get('id', '')).endswith('-medium')), matches[0])
            return preferred.get('resolved_id') or preferred['id']
        return matches[0].get('resolved_id') or matches[0]['id']
    raise Failure(f'Cannot resolve {harness}:{clean} to a current exact model; supply its exact ID.')


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


def resolve_spec(spec, root, refresh=False, offline=False, timeout=120):
    """Resolve '[harness:]model[:tier]' to dict(harness, requested_model, model)."""
    harness, requested = infer_harness(spec)
    if not needs_resolution(harness, requested):
        return dict(harness=harness, requested_model=requested, model=requested)
    listing = cached_roster(harness, root, refresh=refresh, offline=offline, timeout=timeout)
    if listing.get('stale') or listing.get('error'):
        raise Failure(f'Cannot resolve {harness}:{requested} to a current exact model; supply its exact ID.')
    resolved = match_model(harness, requested, listing.get('models', []))
    return dict(harness=harness, requested_model=requested, model=resolved)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('harness', nargs='?', choices=HARNESSES)
    ap.add_argument('--resolve', action='append', default=[], metavar='SPEC',
                    help='resolve a model family or [harness:]model[:tier] spec to exact harness and model ID')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--refresh', action='store_true', help='fetch even if the cache is fresh')
    mode.add_argument('--offline', action='store_true', help='read cached data only')
    ap.add_argument('--timeout', type=int, default=120, help='seconds per harness refresh')
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error('--timeout must be positive')
    root = Path(os.environ.get('WRANGLE_HOME', Path.home() / '.config' / 'wrangle')) / 'roster'
    if args.resolve:
        try:
            resolved = [resolve_spec(spec, root, args.refresh, args.offline, args.timeout)
                        for spec in args.resolve]
        except Failure as exc:
            print(json.dumps(dict(ok=False, error=str(exc))))
            return 2
        print(json.dumps(resolved[0] if len(resolved) == 1 else resolved, indent=2))
        return 0
    results = [cached_roster(h, root, args.refresh, args.offline, args.timeout)
               for h in ([args.harness] if args.harness else HARNESSES)]
    print(json.dumps(results, indent=2))
    return int(any(r['error'] or not r['models'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
