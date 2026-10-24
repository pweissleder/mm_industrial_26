"""LIVE check (paid): sends a short scripted English dialog to the running lab server.

Usage: python -B live_check.py --port 8770 --out live/run1.json "message one" "message two" ...
Records tools, replies, timings and token usage. No keys are read or printed here.
"""
import argparse
import http.cookiejar
import json
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8770)
parser.add_argument('--building', default='proc-factory-dev-003')
parser.add_argument('--out', required=True)
parser.add_argument('messages', nargs='+')
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.open(base + '/api/assistant/session').read()


def post(path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'X-Prototype-Action': 'local', 'Origin': base})
    try:
        with opener.open(request, timeout=300) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read())


log = []
ui = {'building_id': args.building}
for message in args.messages:
    if message.startswith('!confirm'):
        proposal = next((t['ui']['proposal'] for t in reversed(log) if t.get('ui', {}).get('proposal')), None)
        value = post('/api/assistant/confirm', {'proposal_id': proposal['proposal_id'], 'approved': True})
        log.append({'user': message, 'confirm': value.get('message') or value})
        print('CONFIRM ->', value.get('message') or value)
        continue
    began = time.perf_counter()
    value = post('/api/assistant/turn', {'text': message, 'ui': ui, 'speak': False})
    wall = (time.perf_counter() - began) * 1000
    if 'error' in value:
        print('ERROR', value['error']); log.append({'user': message, 'error': value['error']}); continue
    entry = {'user': message, 'reply': value['reply'], 'status': value['status'],
             'tools': [{'tool': t['tool'], 'input': t['input'], 'status': t['status'], 'output': t['output']} for t in value['tools']],
             'ui': {k: v for k, v in value['ui'].items() if k in ('proposal', 'endpoints', 'settings', 'focus', 'building_id')},
             'route_status': (value['ui'].get('route') or {}).get('result', {}).get('status'),
             'timing_ms': value['timing_ms'], 'wall_ms': round(wall), 'usage': value['usage']}
    log.append(entry)
    print(f"\nUSER: {message}\nTOOLS: {[(t['tool'], t['status']) for t in value['tools']]}\nBOT: {value['reply']}\n"
          f"  [{value['timing_ms']} · in {value['usage']['input_tokens']} out {value['usage']['output_tokens']} · ${value['usage']['estimated_usd']}]")
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
Path(args.out).write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')
print('\nsession usage:', log[-1].get('usage', {}).get('session') if log else None)
