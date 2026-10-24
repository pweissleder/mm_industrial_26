"""Offline tests for challenge.py: official kit 1.1.0 routing plus drawable geometry."""
import copy
import itertools
import json
import math
import unittest

from shapely.geometry import Point, Polygon

import challenge
import data
from errors import ModelError

FACTORY = 'proc-factory-dev-003'
F = FACTORY + '-'
# One building per source family plus a campus; all their warm-up routes are checked exactly.
SAMPLE_BUILDINGS = ('proc-factory-dev-003', 'proc-campus-dev-009', 'osm-way22744230', 'osm-way27326387',
                    'resplan-14509', 'resplan-1677', 'swiss-1785', 'swiss-2211')


def space(sid):
    return {'kind': 'space', 'id': sid}


def route(building, start, target, **kw):
    return challenge.challenge_route(building, {'start': start, 'target': target, **kw})


def official_route(building, start, target, step_free=False, closed=(), via=None):
    bmap = data.reference_map(building)
    for element in closed:
        bmap.set_closed(element, True)
    return bmap.route(start, target, step_free, via=via)


def polyline_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def synthetic(rooms, doors):
    """Minimal kit-format building: rooms {id: polygon}, doors [(id, a, b, position)]."""
    return {'building': {'id': 'synthetic', 'source': 'procedural'},
            'floors': [{'id': 'X-F0', 'level': 0, 'name': 'Ground floor', 'elevation_m': 0.0}],
            'spaces': [{'id': sid, 'floor_id': 'X-F0', 'type': 'room', 'room_type': 'office', 'name': sid, 'ref': sid,
                        'polygon': poly} for sid, poly in rooms.items()],
            'openings': [{'id': oid, 'floor_id': 'X-F0', 'space_ids': [a, b], 'kind': 'door', 'position': pos,
                          'width_m': 0.9, 'step_free': True} for oid, a, b, pos in doors],
            'vertical_connectors': [], 'assets': []}


class ChallengeFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = data.load_building(FACTORY)
        cls.spaces = {s['id']: s for s in cls.b['spaces']}
        cls.openings = {o['id']: o for o in cls.b['openings']}
        cls.connectors = {c['id']: c for c in cls.b['vertical_connectors']}

    def assert_official(self, res, start_sid, target_sid, **kw):
        """Shape, internal consistency and identity with the official BuildingMap.route."""
        self.assertEqual((res['model'], res['status'], res['reachable']), ('challenge', 'ok', True))
        expected = official_route(self.b, start_sid, target_sid, **kw)
        self.assertEqual(res['cost_m'], expected.length_m)
        self.assertEqual(res['space_path'], expected.space_path)
        self.assertEqual(res['directions'], expected.steps)
        self.assertEqual((res['space_path'][0], res['space_path'][-1]), (start_sid, target_sid))
        for element in res['element_path']:
            self.assertTrue(element in self.openings or element in self.connectors, element)
        self.assertAlmostEqual(res['cost_m'], sum(s['cost_m'] for s in res['steps']), delta=0.005 + 1e-9)
        self.assertAlmostEqual(res['cost_m'], res['walk_m'] + res['vertical_cost_m'], delta=0.005 + 1e-9)
        for step in res['steps']:
            if step['type'] == 'walk' and step['clear_walk']:
                # Drawn geometry replays the official walk: equal to the 3-decimal edge weight.
                self.assertAlmostEqual(step['drawn_length_m'], step['cost_m'], delta=6e-4)
        drawn = sum(polyline_length(seg['points']) for seg in res['segments'])
        self.assertAlmostEqual(drawn, res['walk_m'], delta=6e-4 * max(1, len(res['steps'])))
        for seg in res['segments']:
            self.assertGreaterEqual(len(seg['points']), 2)
            self.assertEqual(self.spaces[seg['space_id']]['floor_id'], seg['floor_id'])
        json.dumps(res, allow_nan=False)

    def test_same_floor_route_matches_official(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'))
        self.assert_official(res, F + 'F0-S021', F + 'F0-S030')
        self.assertEqual(res['space_path'], [F + 'F0-S021', F + 'F0-S016', F + 'F0-S030'])
        self.assertEqual(res['element_path'], [F + 'F0-O0026', F + 'F0-O0035'])
        self.assertEqual((res['transitions'], res['vertical_cost_m']), ([], 0.0))
        self.assertEqual(res['segments'][0]['points'][0], list(res['start']['position']))
        self.assertEqual(res['label'], 'Official challenge routing')
        self.assertEqual(res['kit'], {'version': '1.1.0', 'commit': data.KIT_COMMIT})
        # Directions name only real doors/connectors: one "Walk ... through" line per door crossed.
        through = [d for d in res['directions'] if ' through the ' in d]
        self.assertEqual(len(through), len(res['element_path']))

    def test_drawn_walk_uses_landings_and_clearance(self):
        res = route(self.b, space(F + 'F0-S015'), space(F + 'F0-S001'))
        self.assert_official(res, F + 'F0-S015', F + 'F0-S001')
        door_points = {tuple(o['position']) for o in self.b['openings']}
        bm = challenge.official()
        for seg in res['segments']:
            poly = Polygon(self.spaces[seg['space_id']]['polygon'])
            allowed = poly.buffer(-bm.WALL_CLEARANCE_M).buffer(bm.COVER_TOLERANCE_M + 1e-6)
            for p in seg['points']:
                if tuple(p) not in door_points and tuple(p) != tuple(res['start']['position']) \
                        and tuple(p) != tuple(res['target']['position']):
                    self.assertTrue(allowed.covers(Point(p)), (seg['space_id'], p))
            if tuple(seg['points'][-1]) in door_points:
                # the official landing: 30 cm in from the walls for a door on a straight wall
                self.assertAlmostEqual(math.dist(seg['points'][-1], seg['points'][-2]), bm.DOOR_LANDING_M, places=6)

    def test_vertical_costs_follow_rise(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'))
        self.assert_official(res, F + 'F0-S021', F + 'F1-S010')
        [t] = res['transitions']
        self.assertEqual((t['kind'], t['element_id']), ('stairs', F + 'V01'))
        self.assertAlmostEqual(t['delta_elevation_m'], 3.5)
        self.assertAlmostEqual(t['cost_m'], 7.0)
        lift = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), closed=[F + 'V01'])
        self.assert_official(lift, F + 'F0-S021', F + 'F1-S010', closed=[F + 'V01'])
        [t2] = lift['transitions']
        self.assertEqual((t2['kind'], t2['element_id'], t2['cost_m']), ('lift', F + 'V02', 18.5))
        self.assertEqual((lift['rules']['stairs_m_per_m'], lift['rules']['lift_wait_m'], lift['rules']['lift_m_per_m']), (2.0, 15.0, 1.0))
        self.assertFalse(lift['rules']['outside_link'])

    def test_step_free_and_closures(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), step_free=True)
        self.assert_official(res, F + 'F0-S021', F + 'F1-S010', step_free=True)
        self.assertTrue(res['transitions'] and all(t['kind'] == 'lift' for t in res['transitions']))
        blocked = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), step_free=True, closed=[F + 'V02'])
        self.assertEqual(blocked['status'], 'unreachable')
        self.assertIn('step-free', blocked['unreachable_reason'])
        base = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'))
        detour = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), closed=[F + 'F0-S017'])
        self.assert_official(detour, F + 'F0-S021', F + 'F1-S010', closed=[F + 'F0-S017'])
        self.assertNotIn(F + 'F0-S017', detour['space_path'])
        self.assertGreater(detour['cost_m'], base['cost_m'])
        door = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-O0026'])
        self.assertEqual(door['status'], 'unreachable')
        self.assertIn('closures', door['unreachable_reason'])
        self.assertIn(F + 'F0-O0026', door['unreachable_reason'])
        end = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-S030'])
        self.assertEqual((end['status'], end['reachable'], end['cost_m']), ('blocked_endpoint', False, None))

    def test_unreachable_shape(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-S016'])
        self.assertEqual((res['status'], res['reachable'], res['cost_m']), ('unreachable', False, None))
        self.assertEqual((res['space_path'], res['element_path'], res['steps'], res['segments'], res['directions']),
                         ([], [], [], [], []))
        self.assertIn(F + 'F0-S016', res['unreachable_reason'])
        json.dumps(res, allow_nan=False)

    def test_no_route_round_the_outside(self):
        with self.assertRaises(ModelError):
            route(self.b, space(F + 'F0-S015'), space(F + 'F0-S021'), allow_outside=True)
        res = route(self.b, space(F + 'F0-S015'), space(F + 'F0-S021'))
        self.assertNotIn('outside', res['space_path'])

    def test_asset_door_point_endpoints_resolve_to_spaces(self):
        asset = next(a for a in self.b['assets'] if a['space_id'] == F + 'F0-S021')
        res = route(self.b, space(F + 'F0-S030'), {'kind': 'asset', 'id': asset['id']})
        self.assert_official(res, F + 'F0-S030', F + 'F0-S021')
        self.assertEqual(res['target']['requested_position'], asset['position'])
        self.assertEqual(res['target']['position'], list(data.reference_map(self.b).anchor(F + 'F0-S021')))
        self.assertIn('holds it', res['target']['resolution'])
        door = route(self.b, {'kind': 'door', 'id': F + 'F0-O0026', 'space_id': F + 'F0-S021'}, space(F + 'F0-S030'))
        self.assertEqual(door['cost_m'], res['cost_m'])
        point = route(self.b, {'kind': 'point', 'space_id': F + 'F0-S016', 'position': [6.0, 30.0]}, space(F + 'F0-S030'))
        self.assert_official(point, F + 'F0-S016', F + 'F0-S030')

    def test_tour_legs_match_official_via(self):
        a, b_, c = F + 'F0-S021', F + 'F1-S010', F + 'F0-S030'
        tour = challenge.challenge_route(self.b, {'start': space(a), 'via': [space(b_)], 'target': space(c)})
        expected = official_route(self.b, a, c, via=[b_])
        self.assertEqual((tour['cost_m'], tour['space_path'], tour['directions']),
                         (expected.length_m, expected.space_path, expected.steps))
        self.assertEqual(len(tour['legs']), 2)
        for leg, (x, y) in zip(tour['legs'], [(a, b_), (b_, c)]):
            single = official_route(self.b, x, y)
            self.assertEqual((leg['cost_m'], leg['space_path']), (single.length_m, single.space_path))
        self.assertEqual(tour['element_path'], tour['legs'][0]['element_path'] + tour['legs'][1]['element_path'])

    def test_production_transit_rule_is_opt_in_and_unofficial(self):
        start, target = space(F + 'F0-S015'), space(F + 'F0-S021')
        default = route(self.b, start, target)
        self.assertEqual(default['status'], 'ok')
        self.assertEqual(default['rules']['production_transit'], 'allowed (official)')
        self.assertEqual(default['rules']['unofficial_rules_active'], [])
        middle = default['space_path'][1:-1]
        self.assertTrue(any(self.spaces[s]['room_type'] in challenge.PRODUCTION_ROOM_TYPES for s in middle))
        strict = route(self.b, start, target, no_production_transit=True)
        self.assertIn(challenge.UNOFFICIAL, strict['rules']['production_transit'])
        self.assertTrue(any(challenge.UNOFFICIAL in w for w in strict['warnings']))
        if strict['reachable']:
            for s in strict['space_path'][1:-1]:
                self.assertNotIn(self.spaces[s]['room_type'], challenge.PRODUCTION_ROOM_TYPES)
            self.assertGreaterEqual(strict['cost_m'], default['cost_m'])
        else:
            self.assertIn('production', strict['unreachable_reason'])
        into = route(self.b, space(F + 'F0-S015'), space(F + 'F0-S001'), no_production_transit=True)
        self.assertEqual(into['space_path'][-1], F + 'F0-S001')

    def test_forklift_rule_is_opt_in_and_unofficial(self):
        self.assertEqual(route(self.b, space(F + 'F0-S011'), space(F + 'F0-S015'))['rules']['forklift'], 'off')
        res = route(self.b, space(F + 'F0-S011'), space(F + 'F0-S015'), forklift=True)
        self.assertIn(challenge.UNOFFICIAL, res['rules']['forklift'])
        self.assertTrue(any('forklift' in w.lower() and challenge.UNOFFICIAL in w for w in res['warnings']))
        if res['reachable']:
            for element in res['element_path']:
                self.assertGreaterEqual(self.openings[element]['width_m'], challenge.FORKLIFT_MIN_WIDTH_M)
        office = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), forklift=True)
        self.assertEqual(office['status'], 'unreachable')
        self.assertIn('forklift', office['unreachable_reason'])

    def test_invalid_input(self):
        good = {'start': space(F + 'F0-S021'), 'target': space(F + 'F0-S030')}
        bad_requests = [
            None, {}, {**good, 'start': None}, {**good, 'target': {'kind': 'room', 'id': 'x'}},
            {**good, 'start': space('nope')}, {**good, 'closed': ['nope']}, {**good, 'closed': 'x'},
            {**good, 'wall_clearance_m': 0.3}, {**good, 'wall_clearance_m': float('nan')},
            {**good, 'step_free': 'yes'}, {**good, 'forklift': 1}, {**good, 'no_production_transit': 'no'},
            {**good, 'via': 'x'}, {**good, 'allow_outside': True},
            {**good, 'start': {'kind': 'point', 'space_id': F + 'F0-S021', 'position': [500, 500]}},
            {**good, 'start': {'kind': 'door', 'id': F + 'F0-O0026', 'space_id': F + 'F0-S030'}},
            {**good, 'target': {'kind': 'asset', 'id': 'missing'}},
        ]
        for request in bad_requests:
            with self.subTest(request=request), self.assertRaises(ModelError) as ctx:
                challenge.challenge_route(self.b, request)
            self.assertEqual(ctx.exception.code, 'invalid_input')
        with self.assertRaises(ModelError):
            challenge.challenge_route({'no': 'spaces'}, good)


class ChallengeSyntheticTests(unittest.TestCase):
    L_ROOM = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]

    def test_walk_bends_round_the_corner_on_shrunken_outline(self):
        rooms = {'L': self.L_ROOM, 'A': [[10, 0], [14, 0], [14, 4], [10, 4]], 'B': [[0, 10], [4, 10], [4, 14], [0, 14]]}
        b = synthetic(rooms, [('LA', 'L', 'A', [10, 2]), ('LB', 'L', 'B', [2, 10])])
        res = route(b, space('A'), space('B'))
        self.assertEqual(res['space_path'], ['A', 'L', 'B'])
        self.assertEqual(res['cost_m'], official_route(b, 'A', 'B').length_m)
        [walk_l] = [s for s in res['steps'] if s['space_id'] == 'L']
        self.assertTrue(walk_l['clear_walk'])
        self.assertAlmostEqual(walk_l['drawn_length_m'], walk_l['cost_m'], delta=6e-4)
        seg = next(s for s in res['segments'] if s['space_id'] == 'L')
        self.assertGreater(len(seg['points']), 4)  # door, landing, corner(s), landing, door
        self.assertEqual(seg['points'][0], [10.0, 2.0])
        self.assertEqual(seg['points'][1], [9.7, 2.0])  # 30 cm landing
        corner = Polygon(self.L_ROOM).buffer(-0.15)
        self.assertTrue(any(corner.exterior.distance(Point(p)) < 1e-9 for p in seg['points'][2:-2]))
        straight = math.dist((10, 2), (2, 10))
        self.assertGreater(walk_l['cost_m'], straight)


class OfficialWarmupRegressionTests(unittest.TestCase):
    """Every warm-up route of a fixed building sample (all four sources), kit 1.1.0 answers, exact."""

    def test_sample_matches_expected_answers_exactly(self):
        from service import Service
        service = Service()
        checked = unreachable = 0
        rows = [q for q in data.basic_examples('dev') if q['building_id'] in SAMPLE_BUILDINGS
                and q['kind'] in ('route', 'route_step_free', 'route_with_closure', 'tour')]
        self.assertGreaterEqual(len(rows), 40)
        for q in rows:
            a = q['answer']
            stops = a['stops'] if q['kind'] == 'tour' else [a['from_space_id'], a['to_space_id']]
            request = {'model': 'challenge', 'start': space(stops[0]), 'target': space(stops[-1]),
                       'via': [space(s) for s in stops[1:-1]], 'step_free': q['kind'] == 'route_step_free',
                       'closed': [a['closed_element_id']] if a.get('closed_element_id') else []}
            with self.subTest(question=q['id']):
                res = service.navigate(q['building_id'], request)
                if a.get('reachable') is False:
                    self.assertFalse(res['reachable']);self.assertIsNone(res['cost_m']);unreachable += 1
                    continue
                self.assertTrue(res['reachable'])
                self.assertLessEqual(abs(res['cost_m'] - a['optimal_length_m']), 0.01 * a['optimal_length_m'])
                legs = a['legs'] if q['kind'] == 'tour' else [a]
                got = res['legs'] if q['kind'] == 'tour' else [res]
                # Each leg is exact to the centimetre. A tour answer is the sum of the rounded legs, the
                # official BuildingMap.route rounds the unrounded sum once: they may differ by 0.01 m.
                for leg, want in zip(got, legs):
                    self.assertAlmostEqual(leg['cost_m'], want['optimal_length_m'], delta=0.0051)
                self.assertAlmostEqual(res['cost_m'], a['optimal_length_m'], delta=0.0051 if q['kind'] != 'tour' else 0.0101)
                self.assertEqual([l['space_path'] for l in got], [l['space_path'] for l in legs])
                self.assertEqual([l['element_path'] for l in got], [l['element_path'] for l in legs])
                checked += 1
        self.assertGreater(checked, 30);self.assertGreater(unreachable, 0)


if __name__ == '__main__':
    unittest.main()
