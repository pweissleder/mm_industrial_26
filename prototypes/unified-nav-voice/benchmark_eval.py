"""Deterministic offline benchmark harness for kit v1.1.0 (no model or network calls).

Runs every Train/Dev benchmark question (questions.jsonl) through Service.operation and the official
scorer, and every warm-up question (questions_basic.jsonl) through deterministic tools:
- locate / count: plain lookups on the building data, parsed from the question wording;
- route / route_step_free / route_with_closure / tour: Service.navigate in 'challenge' (official) mode,
  with the endpoints, closure and stops taken from the structured answer fields, exactly as the
  starter kit's own `starter_agent.evaluate --oracle` does (resolving names in the question is the
  language agent's job, not the router's).

    .venv/Scripts/python -B benchmark_eval.py                      # train + dev, writes reports/benchmark_1_1_0.*
    .venv/Scripts/python -B benchmark_eval.py --splits dev --out reports/dev_only
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROUTE_KINDS = ('route', 'route_step_free', 'route_with_closure', 'tour')
LENGTH_RELATIVE = 0.01


def load_questions(split, basic, root=None):
    """Kit questions via the verified data layer, or (diagnostics only) straight from `root/<split>/*/`."""
    if root is None:
        import data
        return list(data.basic_examples(split) if basic else data.examples(split))
    name = 'questions_basic.jsonl' if basic else 'questions.jsonl'
    rows = []
    for path in sorted(Path(root, split).glob('*/' + name)):
        rows += [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return rows


def _short(value, limit=12):
    if isinstance(value, list) and len(value) > limit:
        return value[:limit] + [f'... {len(value) - limit} more']
    return value


# ------------------------------------------------------------------ benchmark questions

def run_benchmark(service, question, split, source):
    from errors import ModelError
    import data
    request = {'operation': question['kind'], 'arguments': copy.deepcopy(question.get('inputs', {}))}
    record = {'section': 'benchmark', 'id': question['id'], 'split': split, 'building': question['building_id'],
              'source': source, 'kind': question['kind'], 'inputs': question.get('inputs'),
              'expected': question['answer'], 'tool': 'Service.operation', 'request': request}
    try:
        result = service.operation(question['building_id'], copy.deepcopy(request))
    except ModelError as error:
        record.update(ok=False, actual={'error': error.code, 'message': error.message}, cause='error:' + error.code)
        return record
    reply = result['formatted_answer']
    ok = bool(data.reference_module('score').score_reply(question['answer'], reply))
    actual = {'formatted_answer': reply, 'value': result.get('value'), 'ids': _short(result.get('ids', []))}
    for key in ('via', 'target'):
        if key in result:
            actual[key] = result[key]
    record.update(ok=ok, actual=actual, cause=None if ok else _benchmark_cause(question, result))
    return record


def _benchmark_cause(question, result):
    answer = question['answer']
    if answer.get('type') == 'number' and isinstance(result.get('value'), (int, float)):
        return 'count_off' if answer.get('relative_tolerance', 0) == 0 else 'value_off'
    if answer.get('type') == 'space':
        return 'wrong_space_or_area'
    return 'wrong_value'


# ------------------------------------------------------------------ warm-up questions

def _floor_by_phrase(building, phrase):
    phrase = phrase.strip().lower()
    hits = [f for f in building['floors'] if f.get('name', '').lower() == phrase]
    return hits[0] if len(hits) == 1 else None


def _label(asset):
    """Display label of an asset: its name without the trailing room/instance ref ('Seating row 0.18-A')."""
    name = asset.get('name', '').strip().lower()
    return name.rsplit(' ', 1)[0] if ' ' in name else name


def _types_for_phrase(building, phrase):
    """Asset types a wording refers to: exact type ('toilet'), else the display label ('seating row')."""
    by_type = {a['type'] for a in building['assets'] if a.get('type', '').replace('_', ' ').lower() == phrase}
    return by_type or {a['type'] for a in building['assets'] if _label(a) == phrase}


def run_locate(building, question, split, source):
    """'Where is the <thing>?' -> the unique asset with that exact name, type or display label."""
    match = re.match(r'^\s*Where is (?:the |a |an )?(.+?)\s*\?\s*$', question['question'], re.IGNORECASE)
    phrase = match.group(1).strip().lower() if match else None
    by_name = [a for a in building['assets'] if phrase and a.get('name', '').lower() == phrase]
    types = _types_for_phrase(building, phrase) if phrase else set()
    candidates = by_name or [a for a in building['assets'] if a.get('type') in types]
    floors = {f['id']: f for f in building['floors']}
    expected = question['answer']
    record = {'section': 'warmup', 'id': question['id'], 'split': split, 'building': question['building_id'],
              'source': source, 'kind': 'locate', 'inputs': {'phrase': phrase}, 'expected': expected,
              'tool': 'asset lookup by exact name or type', 'request': {'phrase': phrase}}
    if len(candidates) != 1:
        record.update(ok=False, actual={'candidates': _short([a['id'] for a in candidates])},
                      cause='no_match' if not candidates else 'ambiguous_phrase')
        return record
    asset = candidates[0]
    actual = {'asset_id': asset['id'], 'space_id': asset['space_id'], 'floor_level': floors[asset['floor_id']]['level']}
    ok = all(actual[k] == expected.get(k) for k in actual)
    record.update(ok=ok, actual=actual, cause=None if ok else 'different_asset')
    return record


def run_count(building, question, split, source):
    """'How many <type> items are on the <floor name>?' -> number of assets of that type on that floor."""
    match = re.match(r'^\s*How many (.+?) items are on (?:the )?(.+?)\s*\?\s*$', question['question'], re.IGNORECASE)
    expected = question['answer']
    record = {'section': 'warmup', 'id': question['id'], 'split': split, 'building': question['building_id'],
              'source': source, 'kind': 'count', 'expected': expected, 'tool': 'asset count by type and floor'}
    if not match:
        record.update(inputs=None, request=None, ok=False, actual=None, cause='unparsed_question')
        return record
    phrase = match.group(1).strip().lower()
    types = _types_for_phrase(building, phrase)
    asset_type = next(iter(types)) if len(types) == 1 else phrase.replace(' ', '_')
    floor = _floor_by_phrase(building, match.group(2))
    record.update(inputs={'phrase': phrase, 'floor': match.group(2)}, request={'asset_type': asset_type,
                  'floor_id': floor and floor['id']})
    if floor is None or len(types) > 1:
        record.update(ok=False, actual={'types': sorted(types)}, cause='unknown_floor' if floor is None else 'ambiguous_phrase')
        return record
    count = sum(1 for a in building['assets'] if a.get('type') == asset_type and a.get('floor_id') == floor['id'])
    actual = {'count': count, 'asset_type': asset_type, 'floor_level': floor['level']}
    ok = actual == {k: expected.get(k) for k in actual}
    record.update(ok=ok, actual=actual, cause=None if ok else 'count_off')
    return record


def _route_request(question):
    answer = question['answer']
    space = lambda sid: {'kind': 'space', 'id': sid}
    if question['kind'] == 'tour':
        stops = answer['stops']
        request = {'model': 'challenge', 'start': space(stops[0]), 'via': [space(s) for s in stops[1:-1]],
                   'target': space(stops[-1])}
    else:
        request = {'model': 'challenge', 'start': space(answer['from_space_id']), 'target': space(answer['to_space_id'])}
    request['step_free'] = question['kind'] == 'route_step_free'
    request['closed'] = [answer['closed_element_id']] if answer.get('closed_element_id') else []
    return request


def _expected_legs(answer, kind):
    if kind == 'tour':
        return [(leg['space_path'], leg['element_path'], leg['optimal_length_m']) for leg in answer['legs']]
    return [(answer['space_path'], answer['element_path'], answer['optimal_length_m'])]


def run_route(service, question, split, source):
    from errors import ModelError
    request = _route_request(question)
    answer = question['answer']
    expected_reachable = answer.get('reachable') is not False
    record = {'section': 'warmup', 'id': question['id'], 'split': split, 'building': question['building_id'],
              'source': source, 'kind': question['kind'], 'inputs': {k: answer[k] for k in
              ('from_space_id', 'to_space_id', 'closed_element_id', 'stops') if k in answer},
              'expected': answer, 'tool': 'Service.navigate(model=challenge)', 'request': request}
    checks = {'expected_reachable': expected_reachable}
    try:
        result = service.navigate(question['building_id'], copy.deepcopy(request))
    except ModelError as error:
        record.update(ok=False, checks=checks, actual={'error': error.code, 'message': error.message},
                      cause='error:' + error.code)
        return record
    reachable = bool(result.get('reachable'))
    legs = result.get('legs') or []
    actual_legs = ([(leg.get('space_path'), leg.get('element_path'), leg.get('cost_m')) for leg in legs]
                   if question['kind'] == 'tour' else
                   [(result.get('space_path'), result.get('element_path'), result.get('cost_m'))])
    actual = {'reachable': reachable, 'status': result.get('status'), 'cost_m': result.get('cost_m'),
              'unreachable_reason': result.get('unreachable_reason')}
    if question['kind'] == 'tour':
        actual['legs'] = [{'space_path': s, 'element_path': e, 'cost_m': c} for s, e, c in actual_legs]
    else:
        actual.update(space_path=result.get('space_path'), element_path=result.get('element_path'))
    checks['reachability_agrees'] = reachable == expected_reachable
    if expected_reachable and reachable:
        wanted = _expected_legs(answer, question['kind'])
        same_count = len(wanted) == len(actual_legs)
        checks['space_path'] = same_count and all(w[0] == a[0] for w, a in zip(wanted, actual_legs))
        checks['element_path'] = same_count and all(w[1] == a[1] for w, a in zip(wanted, actual_legs))
        optimal, cost = float(answer['optimal_length_m']), result.get('cost_m')
        good = isinstance(cost, (int, float)) and math.isfinite(cost)
        checks['length_within_1pct'] = good and abs(cost - optimal) <= LENGTH_RELATIVE * optimal + 1e-9
        checks['length_within_0_01m'] = good and abs(cost - optimal) <= 0.01 + 1e-9
        checks['length_equal_2dp'] = good and round(cost, 2) == round(optimal, 2)
        checks['length_error_m'] = (cost - optimal) if good else None
        if question['kind'] == 'tour' and same_count:
            # Tour answers sum the rounded legs; the official route() rounds the total once.
            checks['legs_length_equal_2dp'] = all(isinstance(a[2], (int, float)) and round(a[2], 2) == round(w[2], 2)
                                                  for w, a in zip(wanted, actual_legs))
        walks = [s for s in result.get('steps', []) if s.get('type') == 'walk' and 'drawn_length_m' in s]
        if walks:  # drawable geometry replays each official walk edge (3-decimal weights)
            checks['drawn_walks_match_cost'] = all(abs(s['drawn_length_m'] - s['cost_m']) <= 6e-4
                                                   for s in walks if s.get('clear_walk'))
            checks['fallback_walks'] = sum(not s.get('clear_walk') for s in walks)
        ok = checks['space_path'] and checks['element_path'] and checks['length_within_0_01m']
        if ok:
            cause = None
        elif not checks['space_path']:
            cause = 'different_space_path'
        elif not checks['element_path']:
            cause = 'different_element_path'
        elif checks['length_within_1pct']:
            cause = 'length_off_under_1pct'
        else:
            cause = 'length_off_over_1pct'
    else:
        ok = checks['reachability_agrees']
        cause = None if ok else ('expected_unreachable_got_route' if reachable else 'expected_route_got_unreachable')
    record.update(ok=ok, checks=checks, actual=actual, cause=cause)
    return record


# ------------------------------------------------------------------ summary

def _rate(ok, total):
    return {'ok': ok, 'total': total}


def summarise(records):
    bench = [r for r in records if r['section'] == 'benchmark']
    warm = [r for r in records if r['section'] == 'warmup']
    out = {'benchmark': {'overall': _rate(sum(r['ok'] for r in bench), len(bench))}, 'warmup': {}}
    for key in ('split', 'kind', 'source'):
        groups = defaultdict(list)
        for r in bench:
            groups[r[key]].append(r)
        out['benchmark']['by_' + key] = {k: _rate(sum(r['ok'] for r in v), len(v)) for k, v in sorted(groups.items())}
    for kind in ('locate', 'count', *ROUTE_KINDS):
        rows = [r for r in warm if r['kind'] == kind]
        entry = {'overall': _rate(sum(r['ok'] for r in rows), len(rows)),
                 'by_split': {s: _rate(sum(r['ok'] for r in rows if r['split'] == s), sum(r['split'] == s for r in rows))
                              for s in sorted({r['split'] for r in rows})}}
        if kind in ROUTE_KINDS:
            entry.update(_route_stats(rows))
        out['warmup'][kind] = entry
    routes = [r for r in warm if r['kind'] in ROUTE_KINDS]
    out['warmup']['all_routes'] = {'overall': _rate(sum(r['ok'] for r in routes), len(routes)), **_route_stats(routes)}
    routes_no_tour = [r for r in routes if r['kind'] != 'tour']
    out['warmup']['routes_without_tours'] = {'overall': _rate(sum(r['ok'] for r in routes_no_tour), len(routes_no_tour)),
                                             **_route_stats(routes_no_tour)}
    sources = defaultdict(list)
    for r in routes:
        sources[r['source']].append(r)
    out['warmup']['routes_by_source'] = {k: _rate(sum(r['ok'] for r in v), len(v)) for k, v in sorted(sources.items())}
    failures = [r for r in records if not r['ok']]
    out['failures_by_cause'] = dict(Counter(f"{r['section']}:{r['kind']}:{r['cause']}" for r in failures).most_common())
    out['failures_by_source'] = dict(Counter(f"{r['section']}:{r['source']}" for r in failures).most_common())
    return out


def _route_stats(rows):
    checks = [r.get('checks', {}) for r in rows]
    reachable = [c for c in checks if c.get('expected_reachable')]
    unreachable = [c for c in checks if c.get('expected_reachable') is False]
    both = [c for c in checks if 'space_path' in c]
    errors = sorted(abs(c['length_error_m']) / 1 for c in both if c.get('length_error_m') is not None)
    return {
        'expected_reachable': len(reachable), 'expected_unreachable': len(unreachable),
        'reachability_agrees': _rate(sum(bool(c.get('reachability_agrees')) for c in checks), len(rows)),
        'reachability_agrees_expected_reachable': _rate(sum(bool(c.get('reachability_agrees')) for c in reachable), len(reachable)),
        'reachability_agrees_expected_unreachable': _rate(sum(bool(c.get('reachability_agrees')) for c in unreachable), len(unreachable)),
        'compared_reachable_pairs': len(both),
        'space_path_exact': _rate(sum(c['space_path'] for c in both), len(reachable)),
        'element_path_exact': _rate(sum(c['element_path'] for c in both), len(reachable)),
        'length_within_1pct': _rate(sum(c['length_within_1pct'] for c in both), len(reachable)),
        'length_within_0_01m': _rate(sum(c['length_within_0_01m'] for c in both), len(reachable)),
        'length_equal_2dp': _rate(sum(c['length_equal_2dp'] for c in both), len(reachable)),
        'tour_legs_length_equal_2dp': _rate(sum(bool(c.get('legs_length_equal_2dp')) for c in both),
                                            sum('legs_length_equal_2dp' in c for c in both)),
        'drawn_geometry_matches_cost': _rate(sum(bool(c.get('drawn_walks_match_cost')) for c in both),
                                             sum('drawn_walks_match_cost' in c for c in both)),
        'routes_with_blocked_walk_fallback': sum(bool(c.get('fallback_walks')) for c in both),
        'max_abs_length_error_m': errors[-1] if errors else None,
        'median_abs_length_error_m': errors[len(errors) // 2] if errors else None,
    }


def _fmt(rate):
    return f"{rate['ok']}/{rate['total']}"


def markdown(report):
    s = report['summary']
    lines = [f"# Benchmark harness vs kit 1.1.0 answers: {report['label']}", '',
             f"Code pinned to kit {report['kit']['version']} (commit `{report['kit']['commit']}`); questions from "
             f"{report['kit']['questions_root']}. Offline, deterministic, no model calls. "
             f"Runtime {report['runtime_s']['total']:.1f} s (benchmark {report['runtime_s']['benchmark']:.1f} s, "
             f"warm-up {report['runtime_s']['warmup']:.1f} s). Splits: {', '.join(report['splits'])}.", '',
             '## Benchmark questions (official scorer)', '',
             f"Overall **{_fmt(s['benchmark']['overall'])}**; " + ', '.join(f"{k} {_fmt(v)}" for k, v in s['benchmark']['by_split'].items()), '',
             '| Kind | Correct |', '|---|---|']
    lines += [f'| {k} | {_fmt(v)} |' for k, v in s['benchmark']['by_kind'].items()]
    lines += ['', 'By source: ' + ', '.join(f"{k} {_fmt(v)}" for k, v in s['benchmark']['by_source'].items()), '',
              '## Warm-up questions', '',
              '| Kind | Correct | Reachability agrees | space_path exact | element_path exact | length ≤1 % | length ≤0.01 m |',
              '|---|---|---|---|---|---|---|']
    for kind in ('locate', 'count', *ROUTE_KINDS, 'routes_without_tours', 'all_routes'):
        v = s['warmup'][kind]
        if 'reachability_agrees' in v:
            lines.append(f"| {kind} | {_fmt(v['overall'])} | {_fmt(v['reachability_agrees'])} | {_fmt(v['space_path_exact'])} | "
                         f"{_fmt(v['element_path_exact'])} | {_fmt(v['length_within_1pct'])} | {_fmt(v['length_within_0_01m'])} |")
        else:
            lines.append(f"| {kind} | {_fmt(v['overall'])} | – | – | – | – | – |")
    lines += ['', 'Route rows: "Correct" = reachability agrees and, if reachable, identical space_path and element_path '
              'and length within 0.01 m. Path/length columns count against expected-reachable questions.',
              f"Exact to the centimetre (2 decimals): routes without tours {_fmt(s['warmup']['routes_without_tours']['length_equal_2dp'])}; "
              f"tours: every leg {_fmt(s['warmup']['tour']['tour_legs_length_equal_2dp'])}, total {_fmt(s['warmup']['tour']['length_equal_2dp'])} "
              '(a tour answer sums the rounded legs, the official route() rounds the total once, so totals may differ by 0.01 m).',
              f"Drawn segments equal the official walk edge lengths (±0.6 mm) on {_fmt(s['warmup']['all_routes']['drawn_geometry_matches_cost'])} "
              f"reachable routes; routes with a blocked-walk fallback edge (1.5 × straight, drawn straight): "
              f"{s['warmup']['all_routes']['routes_with_blocked_walk_fallback']}.", '',
              'Routes by source: ' + ', '.join(f"{k} {_fmt(v)}" for k, v in s['warmup']['routes_by_source'].items()), '',
              '## Failures by cause', '']
    if s['failures_by_cause']:
        lines += ['| Section:kind:cause | Count |', '|---|---|'] + [f'| {k} | {v} |' for k, v in s['failures_by_cause'].items()]
        lines += ['', 'By source: ' + ', '.join(f'{k} {v}' for k, v in s['failures_by_source'].items())]
        sample = [r for r in report['failures']][:25]
        lines += ['', '### First failures', '']
        lines += [f"- `{r['id']}` ({r['kind']}, {r['source']}): {r['cause']}" for r in sample]
    else:
        lines.append('None.')
    lines.append('')
    return '\n'.join(lines)


def run(splits=('train', 'dev'), questions_root=None, label='after', progress=False):
    from service import Service
    service = Service()
    sources = {row['id']: row['source'] for row in service.catalog()}
    records, timing = [], {'benchmark': 0.0, 'warmup': 0.0}
    began = time.perf_counter()
    for split in splits:
        t = time.perf_counter()
        for question in load_questions(split, False, questions_root):
            records.append(run_benchmark(service, question, split, sources.get(question['building_id'])))
        timing['benchmark'] += time.perf_counter() - t
        t = time.perf_counter()
        for index, question in enumerate(load_questions(split, True, questions_root)):
            bid = question['building_id']
            if question['kind'] in ('locate', 'count'):
                building = service.model(bid).data
                runner = run_locate if question['kind'] == 'locate' else run_count
                records.append(runner(building, question, split, sources.get(bid)))
            else:
                records.append(run_route(service, question, split, sources.get(bid)))
            if progress and index % 200 == 0:
                print(f'[{split}] warm-up {index}', file=sys.stderr, flush=True)
        timing['warmup'] += time.perf_counter() - t
    timing['total'] = time.perf_counter() - began
    import data
    report = {'label': label, 'splits': list(splits),
              'kit': {'version': getattr(data, 'KIT_VERSION', '1.0.0'), 'commit': getattr(data, 'KIT_COMMIT', data.REFERENCE_COMMIT),
                      'questions_root': str(questions_root) if questions_root else 'data layer (verified)'},
              'runtime_s': timing, 'summary': summarise(records), 'failures': [r for r in records if not r['ok']]}
    return report, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--splits', default='train,dev')
    parser.add_argument('--out', default=str(HERE / 'reports' / 'benchmark_1_1_0'), help='output path prefix (.json/.md)')
    parser.add_argument('--label', default='after')
    parser.add_argument('--questions-root', default=None, help='diagnostics only: read <root>/<split>/*/questions*.jsonl directly')
    parser.add_argument('--progress', action='store_true')
    args = parser.parse_args(argv)
    splits = tuple(s for s in args.splits.split(',') if s)
    report, _ = run(splits, args.questions_root, args.label, args.progress)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix('.json').write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    out.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    s = report['summary']
    print(f"benchmark {_fmt(s['benchmark']['overall'])} | warm-up routes {_fmt(s['warmup']['all_routes']['overall'])} | "
          f"locate {_fmt(s['warmup']['locate']['overall'])} | count {_fmt(s['warmup']['count']['overall'])} | "
          f"{report['runtime_s']['total']:.1f} s -> {out.with_suffix('.json')}")
    return report


if __name__ == '__main__':
    main()
