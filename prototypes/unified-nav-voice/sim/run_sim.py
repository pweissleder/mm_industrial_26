"""Run the customer-dialog matrix offline (scripted plan, free) or live (real Claude, costs money).

  python -B sim/run_sim.py --offline                       # reference plans, real tools/data
  pwsh scripts/Run-Sim.ps1 -- --live --max-usd 1.0          # real Claude (key injected by DPAPI launcher)
Options: --only id1,id2  --model claude-sonnet-5-5|claude-haiku-5-5  --effort low|medium|high  --label name
Writes reports/sim_<label>.json (every turn: input, context, expected, actual, tools, verdicts) + .md summary.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import networkx as nx  # noqa: E402
from assistant import Assistant, AssistantError  # noqa: E402
from service import Service  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

ID_PATTERN = re.compile(r'proc-[a-z]+-|-F-?\d+-S\d{3}|\bA\d{5}\b|\bO\d{4}\b|osm-way|swiss-\d')
NEGATION = re.compile(r"\b(no|not|isn't|aren't|can't|cannot|couldn't|doesn't|don't|unfortunately|unable)\b", re.I)
CATEGORY = {'coffee': ({'coffee_machine'}, {'kitchen', 'canteen'}), 'toilet': (set(), {'toilet'})}


class Scripted:
    """Offline stand-in for Claude that replays a scenario turn's reference plan."""

    def __init__(self):
        self.queue = []

    def create(self, **_kwargs):
        step = self.queue.pop(0)
        if isinstance(step, str):
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': step}], 'usage': {}}
        name, args = step
        return {'stop_reason': 'tool_use', 'usage': {}, 'content': [
            {'type': 'tool_use', 'id': f'toolu_{time.perf_counter_ns()}', 'name': name, 'input': args}]}


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]


def nearest(service, building_id, start_space, category, step_free=False):
    assets, room_types = CATEGORY[category]
    model = service.model(building_id)
    data = model.data
    targets = {s['id'] for s in data['spaces'] if s.get('room_type') in room_types}
    targets |= {a['space_id'] for a in data['assets'] if a.get('type') in assets and a.get('space_id')}
    graph = model.graph(step_free)
    costs = nx.single_source_dijkstra_path_length(graph, start_space, weight='weight') if start_space in graph else {}
    reachable = [(costs[t], t) for t in targets if t in costs]
    return min(reachable)[1] if reachable else None


def holding(data, endpoint):
    if not endpoint:
        return None
    if endpoint['kind'] == 'asset':
        return next((a.get('space_id') for a in data['assets'] if a['id'] == endpoint['id']), None)
    return endpoint['id']


def check(expect, out, session, service, building_id, before_closed):
    reply, tools, ui = out.get('reply', ''), out.get('tools', []), out.get('ui', {})
    data = service.model(building_id).data
    names = {s['id']: s.get('name') or s['id'] for s in data['spaces']}
    routes = [t for t in tools if t['tool'] == 'plan_route']
    ok_route = ui.get('route')
    request = ok_route['request'] if ok_route else (ui.get('route_error') or {}).get('request')
    closed = set(service.model(building_id).closed)
    verdicts = {}
    verdicts['no_ids_in_reply'] = not ID_PATTERN.search(reply)
    for key, value in expect.items():
        if key == 'tools':
            verdicts[key] = all(any(t['tool'] == name for t in tools) for name in value)
        elif key == 'state_start':
            verdicts[key] = holding(data, session.state['start']) == value
        elif key == 'state_step_free':
            verdicts[key] = session.state['step_free'] is value
        elif key == 'state_mode':
            verdicts[key] = session.state['mode'] == value
        elif key == 'state_width':
            verdicts[key] = abs(session.state['width_m'] - value) < 1e-6
        elif key == 'short':
            verdicts[key] = len(sentences(reply)) <= 3 and len(reply) <= 350
        elif key == 'asks':
            verdicts[key] = '?' in reply or bool(re.search(r'\b(tell me|let me know|which one|say where)\b', reply, re.I))
        elif key == 'no_route':
            verdicts[key] = not ok_route
        elif key == 'route_found':
            verdicts[key] = bool(ok_route) and ok_route['result'].get('reachable', True) is not False
        elif key == 'route_unreachable':
            res = (ok_route or {}).get('result', {})
            verdicts[key] = bool(routes) and (not ok_route or res.get('reachable') is False)
        elif key == 'route_from':
            verdicts[key] = bool(request) and holding(data, request['start']) == holding(data, {'kind': 'space', 'id': value}) \
                or bool(request) and request['start'].get('id') == value
        elif key == 'route_to':
            verdicts[key] = bool(request) and request['target'].get('id') in {value} | {holding(data, {'kind': 'asset', 'id': value})}
        elif key in ('mentions_nearest', 'route_to_nearest'):
            want = nearest(service, building_id, holding(data, session.state['start']), value, session.state['step_free'])
            if key == 'mentions_nearest':
                label = names.get(want, '')
                number = re.search(r'\d+\.\d+|[A-Z]-[A-Z]\d', label)
                verdicts[key] = bool(want) and (label.lower() in reply.lower() or bool(number and number.group() in reply))
            else:
                verdicts[key] = bool(request) and holding(data, request['target']) == want
            verdicts[key + '_expected'] = names.get(want)
        elif key == 'mentions_any':
            verdicts[key] = any(word.lower() in reply.lower() for word in value)
        elif key == 'honest_missing':
            verdicts[key] = bool(NEGATION.search(reply)) and not ok_route
        elif key == 'tools_any':
            verdicts[key] = any(t['tool'] in value for t in tools)
        elif key == 'previewed_closed':
            verdicts[key] = any(value in (t['input'] or {}).get('assume_closed', []) + (t['input'] or {}).get('element_ids', [])
                                for t in tools if t['tool'] in ('plan_route', 'preview_closure'))
        elif key == 'assumed_closed':
            verdicts[key] = any(value in (t['input'] or {}).get('assume_closed', []) for t in routes)
        elif key == 'unchanged':
            verdicts[key] = closed == before_closed
        elif key == 'proposal':
            p = ui.get('proposal') or {}
            verdicts[key] = (p.get('action'), p.get('element_id')) == tuple(value)
        elif key == 'closed_now':
            verdicts[key] = value in closed
        elif key == 'open_now':
            verdicts[key] = value not in closed
        elif key == 'uses':
            res = (ok_route or {}).get('result', {})
            verdicts[key] = any(t.get('kind') == value for t in res.get('transitions', []))
        else:
            verdicts[key] = None
    passed = all(v for k, v in verdicts.items() if not k.endswith('_expected') and v is not None)
    return passed, verdicts


def run(args):
    service = Service()
    live = args.live
    if live:
        from assistant import LiveTransport
        transport = LiveTransport()
    else:
        transport = Scripted()
    assistant = Assistant(service, transport, model=args.model, effort=args.effort)
    selected = [s for s in SCENARIOS if not args.only or s['id'] in args.only.split(',')]
    results, spent = [], 0.0
    for number, scenario in enumerate(selected):
        service.scenarios.clear()
        token = f'{number:032x}'
        record = {'id': scenario['id'], 'title': scenario['title'], 'building': scenario['building'], 'turns': []}
        for turn in scenario['turns']:
            if live and spent >= args.max_usd:
                record['turns'].append({'user': turn['user'], 'skipped': 'budget'})
                continue
            session = assistant.session(token)
            before = set(service.model(scenario['building']).closed)
            context = assistant.snapshot(session) if session.state['building_id'] else None
            if not live:
                transport.queue = list(turn['plan'])
            began = time.perf_counter()
            try:
                out = assistant.turn(token, turn['user'], {'building_id': scenario['building']}, spoken=args.voice)
            except AssistantError as error:
                out = {'reply': '', 'tools': [], 'ui': {}, 'error': str(error), 'usage': {}, 'timing_ms': {}}
            wall = (time.perf_counter() - began) * 1000
            spent += (out.get('usage') or {}).get('estimated_usd', 0) or 0
            passed, verdicts = check(turn['expect'], out, session, service, scenario['building'], before)
            entry = {'user': turn['user'], 'context_before': context, 'expected': turn['expect'], 'passed': passed,
                     'verdicts': verdicts, 'reply': out.get('reply'), 'error': out.get('error'),
                     'tools': [{'tool': t['tool'], 'input': t['input'], 'status': t['status'],
                                'output': t.get('output')} for t in out.get('tools', [])],
                     'route_request': (out.get('ui', {}).get('route') or out.get('ui', {}).get('route_error') or {}).get('request'),
                     'proposal': out.get('ui', {}).get('proposal'), 'timing_ms': {**out.get('timing_ms', {}), 'wall_ms': round(wall)},
                     'usage': out.get('usage')}
            if 'confirm' in turn and out.get('ui', {}).get('proposal'):
                try:
                    entry['confirmation'] = assistant.confirm(token, out['ui']['proposal']['proposal_id'], turn['confirm'])['status']
                except Exception as error:  # recorded, not raised: the matrix must finish
                    entry['confirmation'] = f'failed: {error}'
            record['turns'].append(entry)
        record['passed'] = all(t.get('passed') for t in record['turns'])
        results.append(record)
        print(f"{'PASS' if record['passed'] else 'FAIL'}  {scenario['id']}")
    turns = [t for r in results for t in r['turns'] if 'skipped' not in t]
    claude = [t['timing_ms'].get('claude_ms', 0) for t in turns]
    summary = {'label': args.label, 'mode': 'live' if live else 'offline', 'model': args.model, 'effort': args.effort,
               'scenarios_passed': sum(r['passed'] for r in results), 'scenarios': len(results),
               'turns_passed': sum(t['passed'] for t in turns), 'turns': len(turns),
               'estimated_usd': round(spent, 4),
               'latency_ms': {'claude_median': sorted(claude)[len(claude) // 2] if claude else None,
                              'claude_max': max(claude) if claude else None,
                              'tools_median': sorted(t['timing_ms'].get('tools_ms', 0) for t in turns)[len(turns) // 2] if turns else None},
               'concise_1_2_sentences': sum(1 for t in turns if t['reply'] and len(sentences(t['reply'])) <= 2)}
    out_dir = ROOT / 'reports'
    out_dir.mkdir(exist_ok=True)
    (out_dir / f'sim_{args.label}.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=2,
                                                                ensure_ascii=False, default=str), encoding='utf-8')
    lines = [f"# Dialog simulation `{args.label}`", '', '```', json.dumps(summary, indent=2), '```', '',
             '| Scenario | Result | Failed checks |', '|---|---|---|']
    for r in results:
        failed = sorted({k for t in r['turns'] for k, v in t.get('verdicts', {}).items() if v is False})
        lines.append(f"| {r['id']} | {'PASS' if r['passed'] else 'FAIL'} | {', '.join(failed)} |")
    (out_dir / f'sim_{args.label}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--only')
    parser.add_argument('--model', default='claude-sonnet-5-5', choices=['claude-sonnet-5-5', 'claude-haiku-5-5'])
    parser.add_argument('--effort', default='medium', choices=['low', 'medium', 'high'])
    parser.add_argument('--voice', action='store_true', help='mark turns as spoken (shorter replies)')
    parser.add_argument('--max-usd', type=float, default=1.0)
    parser.add_argument('--label', default='run')
    run(parser.parse_args())
