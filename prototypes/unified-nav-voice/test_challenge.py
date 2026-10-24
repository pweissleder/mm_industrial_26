"""Offline tests for challenge.py (Official Challenge Routing)."""
import copy
import json
import math
import time
import unittest

from shapely.geometry import Point, Polygon

import challenge
import data
from errors import ModelError

FACTORY = 'proc-factory-dev-003'
F = FACTORY + '-'


def space(sid):
    return {'kind': 'space', 'id': sid}


def route(building, start, target, **kw):
    return challenge.challenge_route(building, {'start': start, 'target': target, **kw})


def synthetic(rooms, doors, floors=None):
    """Minimal building dict: rooms {id: (polygon, room_type)}, doors [(id, a, b, pos, width)]."""
    floors = floors or [{'id': 'X-F0', 'level': 0, 'elevation_m': 0.0}]
    return {
        'building': {'id': 'synthetic-' + '-'.join(sorted(rooms))},
        'floors': floors,
        'spaces': [{'id': sid, 'floor_id': 'X-F0', 'type': 'room', 'room_type': rt, 'name': sid, 'polygon': poly}
                   for sid, (poly, rt) in rooms.items()],
        'openings': [{'id': oid, 'floor_id': 'X-F0', 'space_ids': [a, b], 'kind': 'door', 'position': pos,
                      'width_m': width, 'step_free': True} for oid, a, b, pos, width in doors],
        'vertical_connectors': [], 'assets': []}


def polyline_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


class ChallengeFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = data.load_building(FACTORY)
        cls.spaces = {s['id']: s for s in cls.b['spaces']}
        cls.openings = {o['id']: o for o in cls.b['openings']}

    def assert_consistent(self, res, start_sid, target_sid):
        self.assertEqual(res['model'], 'challenge')
        self.assertEqual(res['status'], 'ok')
        self.assertTrue(res['reachable'])
        sp, ep = res['space_path'], res['element_path']
        self.assertEqual(sp[0], start_sid)
        self.assertEqual(sp[-1], target_sid)
        self.assertEqual(len(ep), len(sp) - 1)
        connectors = {c['id']: c for c in self.b['vertical_connectors']}
        for i, element in enumerate(ep):
            pair = {sp[i], sp[i + 1]}
            if element in self.openings:
                self.assertTrue(pair <= set(self.openings[element]['space_ids']))
            else:
                self.assertIn(element, connectors)
                self.assertTrue(pair <= set(connectors[element]['space_ids']))
        self.assertAlmostEqual(res['cost_m'], sum(s['cost_m'] for s in res['steps']), places=6)
        self.assertAlmostEqual(res['cost_m'], res['walk_m'] + res['vertical_cost_m'], places=6)
        drawn = 0.0
        for seg in res['segments']:
            self.assertGreaterEqual(len(seg['points']), 2)
            self.assertEqual(self.spaces[seg['space_id']]['floor_id'], seg['floor_id'])
            drawn += polyline_length(seg['points'])
        self.assertAlmostEqual(drawn, res['walk_m'], places=5)
        json.dumps(res, allow_nan=False)

    def test_same_floor_route_is_ordered_and_consistent(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'))
        self.assert_consistent(res, F + 'F0-S021', F + 'F0-S030')
        self.assertEqual(res['space_path'], [F + 'F0-S021', F + 'F0-S016', F + 'F0-S030'])
        self.assertEqual(res['element_path'], [F + 'F0-O0026', F + 'F0-O0035'])
        self.assertEqual(res['transitions'], [])
        self.assertEqual(res['vertical_cost_m'], 0.0)
        for a, b in zip(res['segments'], res['segments'][1:]):
            self.assertEqual(a['points'][-1], b['points'][0])
        self.assertEqual(res['segments'][0]['points'][0], list(res['start']['position']))
        self.assertIn('representative_point', ' '.join(res['assumptions']))

    def test_vertical_costs_use_elevation(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'))
        self.assert_consistent(res, F + 'F0-S021', F + 'F1-S010')
        [t] = res['transitions']
        self.assertEqual((t['kind'], t['element_id']), ('stairs', F + 'V01'))
        self.assertAlmostEqual(t['delta_elevation_m'], 3.5)
        self.assertAlmostEqual(t['cost_m'], 7.0)
        self.assertAlmostEqual(res['vertical_cost_m'], 7.0)
        # stairs closed -> the lift (one ride) is used at 15 + 3.5
        res2 = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), closed=[F + 'V01'])
        [t2] = res2['transitions']
        self.assertEqual((t2['kind'], t2['element_id']), ('lift', F + 'V02'))
        self.assertAlmostEqual(t2['cost_m'], 18.5)
        self.assertGreater(res2['cost_m'], res['cost_m'])
        self.assertEqual(res2['rules']['stairs_m_per_m'], 2.0)
        self.assertEqual((res2['rules']['lift_wait_m'], res2['rules']['lift_m_per_m']), (15.0, 1.0))

    def test_parallel_vertical_edges_are_kept(self):
        m = challenge._Model(self.b)
        params = challenge._Params(0.15, False, False, False, [], True)
        a = challenge._resolve_endpoint(m, space(F + 'F0-S021'), 'start')
        b = challenge._resolve_endpoint(m, space(F + 'F1-S010'), 'target')
        leg = challenge._Leg(m, params, a, b, challenge._new_notes())
        costs = sorted(w for edges in leg.vertical.values() for _, w in edges)
        self.assertEqual(costs, [7.0, 7.0, 18.5, 18.5])

    def test_missing_elevation_is_unsupported_not_invented(self):
        b = copy.deepcopy(self.b)
        for floor in b['floors']:
            if floor['id'].endswith('F1'):
                del floor['elevation_m']
        res = route(b, space(F + 'F0-S021'), space(F + 'F1-S010'))
        self.assertEqual(res['status'], 'unsupported')
        self.assertFalse(res['reachable'])
        self.assertIn('elevation_m', res['unreachable_reason'])
        same = route(b, space(F + 'F0-S021'), space(F + 'F0-S030'))
        self.assertEqual(same['status'], 'ok')

    def test_step_free_excludes_stairs(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), step_free=True)
        self.assert_consistent(res, F + 'F0-S021', F + 'F1-S010')
        self.assertTrue(res['transitions'])
        self.assertTrue(all(t['kind'] == 'lift' for t in res['transitions']))
        self.assertAlmostEqual(res['vertical_cost_m'], 18.5)
        blocked = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), step_free=True, closed=[F + 'V02'])
        self.assertEqual(blocked['status'], 'unreachable')
        self.assertIn('step-free', blocked['unreachable_reason'])

    def test_closure_forces_detour_or_unreachable(self):
        base = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'))
        self.assertIn(F + 'F0-S017', base['space_path'])
        detour = route(self.b, space(F + 'F0-S021'), space(F + 'F1-S010'), closed=[F + 'F0-S017'])
        self.assertEqual(detour['status'], 'ok')
        self.assertNotIn(F + 'F0-S017', detour['space_path'])
        self.assertGreater(detour['cost_m'], base['cost_m'])
        door = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-O0026'])
        self.assertEqual(door['status'], 'unreachable')
        self.assertIn('closures', door['unreachable_reason'])
        self.assertIn(F + 'F0-O0026', door['unreachable_reason'])
        end = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-S030'])
        self.assertEqual(end['status'], 'blocked_endpoint')
        self.assertFalse(end['reachable'])
        self.assertIn(F + 'F0-S030', end['unreachable_reason'])

    def test_unreachable_shape(self):
        res = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), closed=[F + 'F0-S016'])
        self.assertEqual(res['status'], 'unreachable')
        self.assertFalse(res['reachable'])
        self.assertIsNone(res['cost_m'])
        self.assertEqual((res['space_path'], res['element_path'], res['steps'], res['segments']), ([], [], [], []))
        self.assertIn(F + 'F0-S016', res['unreachable_reason'])
        json.dumps(res, allow_nan=False)

    def test_production_zone_not_transit_on_real_factory(self):
        start, target = space(F + 'F0-S015'), space(F + 'F0-S021')
        strict = route(self.b, start, target)
        self.assertEqual(strict['status'], 'unreachable')
        self.assertIn('production', strict['unreachable_reason'])
        relaxed = challenge._challenge_route(self.b, {'start': start, 'target': target}, relax={'production_transit'})
        self.assertEqual(relaxed['status'], 'ok')
        middle = relaxed['space_path'][1:-1]
        self.assertTrue(any(self.spaces[s]['room_type'] in challenge.PRODUCTION_ROOM_TYPES for s in middle))
        # production zone as target is allowed
        res = route(self.b, start, space(F + 'F0-S001'))
        self.assert_consistent(res, F + 'F0-S015', F + 'F0-S001')
        for s in res['space_path'][1:-1]:
            self.assertNotIn(self.spaces[s]['room_type'], challenge.PRODUCTION_ROOM_TYPES)
        self.assertEqual(res['rules']['production_room_types'], list(challenge.PRODUCTION_ROOM_TYPES))

    def test_allow_outside_is_opt_in_and_marked(self):
        res = route(self.b, space(F + 'F0-S015'), space(F + 'F0-S021'), allow_outside=True)
        self.assertEqual(res['status'], 'ok')
        self.assertIn('outside', res['space_path'])
        self.assertIn("'outside'", ' '.join(res['assumptions']))

    def test_forklift_partial_rules(self):
        res = route(self.b, space(F + 'F0-S011'), space(F + 'F0-S015'), forklift=True)
        self.assert_consistent(res, F + 'F0-S011', F + 'F0-S015')
        self.assertEqual(res['rules']['forklift'], 'partial')
        self.assertTrue(any('forklift' in w.lower() and 'partial' in w.lower() for w in res['warnings']))
        for element in res['element_path']:
            self.assertGreaterEqual(self.openings[element]['width_m'], 3.0)
        office = route(self.b, space(F + 'F0-S021'), space(F + 'F0-S030'), forklift=True)
        self.assertEqual(office['status'], 'unreachable')
        self.assertIn('forklift', office['unreachable_reason'])
        upstairs = route(self.b, space(F + 'F1-S004'), space(F + 'F1-S004'), forklift=True)
        self.assertEqual(upstairs['status'], 'ok')

    def test_endpoint_kinds_and_via(self):
        door = route(self.b, {'kind': 'door', 'id': F + 'F0-O0026', 'space_id': F + 'F0-S021'}, space(F + 'F0-S030'))
        self.assert_consistent(door, F + 'F0-S021', F + 'F0-S030')
        self.assertEqual(door['start']['position'], self.openings[F + 'F0-O0026']['position'])
        asset = next(a for a in self.b['assets'] if a['space_id'] == F + 'F0-S021')
        res = route(self.b, space(F + 'F0-S030'), {'kind': 'asset', 'id': asset['id']})
        self.assert_consistent(res, F + 'F0-S030', F + 'F0-S021')
        point = route(self.b, {'kind': 'point', 'space_id': F + 'F0-S016', 'position': [6.0, 30.0]}, space(F + 'F0-S030'))
        self.assert_consistent(point, F + 'F0-S016', F + 'F0-S030')
        tour = challenge.challenge_route(self.b, {'start': space(F + 'F0-S021'), 'via': [space(F + 'F0-S030')],
                                                 'target': space(F + 'F0-S021')})
        self.assert_consistent(tour, F + 'F0-S021', F + 'F0-S021')
        self.assertEqual(len(tour['legs']), 2)
        self.assertEqual(tour['space_path'], [F + 'F0-S021', F + 'F0-S016', F + 'F0-S030', F + 'F0-S016', F + 'F0-S021'])
        self.assertAlmostEqual(tour['cost_m'], sum(leg['cost_m'] for leg in tour['legs']))

    def test_clearance_respected_except_door_stubs(self):
        res = route(self.b, space(F + 'F0-S015'), space(F + 'F0-S001'))
        door_points = {tuple(o['position']) for o in self.b['openings']}
        c = res['rules']['wall_clearance_m']
        for seg in res['segments']:
            inner = Polygon(self.spaces[seg['space_id']]['polygon']).buffer(-c + 1e-6)
            for p in seg['points']:
                if tuple(p) not in door_points:
                    self.assertTrue(inner.covers(Point(p)), (seg['space_id'], p))
            # door stubs are short and perpendicular: wall_clearance_m long
            if tuple(seg['points'][-1]) in door_points:
                self.assertAlmostEqual(math.dist(seg['points'][-1], seg['points'][-2]), c, places=6)

    def test_invalid_input(self):
        good = {'start': space(F + 'F0-S021'), 'target': space(F + 'F0-S030')}
        bad_requests = [
            None, {}, {**good, 'start': None}, {**good, 'target': {'kind': 'room', 'id': 'x'}},
            {**good, 'start': space('nope')}, {**good, 'closed': ['nope']}, {**good, 'closed': 'x'},
            {**good, 'wall_clearance_m': -1}, {**good, 'wall_clearance_m': float('nan')},
            {**good, 'step_free': 'yes'}, {**good, 'forklift': 1}, {**good, 'via': 'x'},
            {**good, 'start': {'kind': 'point', 'space_id': F + 'F0-S021', 'position': [500, 500]}},
            {**good, 'start': {'kind': 'door', 'id': F + 'F0-O0026', 'space_id': F + 'F0-S030'}},
            {**good, 'target': {'kind': 'asset', 'id': 'missing'}},
        ]
        for request in bad_requests:
            with self.assertRaises(ModelError) as ctx:
                challenge.challenge_route(self.b, request)
            self.assertEqual(ctx.exception.code, 'invalid_input')
        with self.assertRaises(ModelError):
            challenge.challenge_route({'no': 'spaces'}, good)


class ChallengeSyntheticTests(unittest.TestCase):
    L_ROOM = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]

    def test_visibility_path_in_nonconvex_room(self):
        b = synthetic({'L': (self.L_ROOM, 'office')}, [])
        c = 0.15
        bent = route(b, {'kind': 'point', 'space_id': 'L', 'position': [8, 2]},
                     {'kind': 'point', 'space_id': 'L', 'position': [2, 8]}, wall_clearance_m=c)
        self.assertEqual(bent['status'], 'ok')
        [seg] = bent['segments']
        straight = math.dist((8, 2), (2, 8))
        self.assertGreater(bent['cost_m'], straight)
        self.assertGreater(len(seg['points']), 2)
        # optimum wraps the arc of radius c around (4, 4): bounds from both sides
        upper = math.dist((8, 2), (4 - c, 4 - c)) + math.dist((4 - c, 4 - c), (2, 8))
        self.assertLessEqual(bent['cost_m'], upper + 1e-9)
        self.assertGreaterEqual(bent['cost_m'], math.dist((8, 2), (4, 4)) + math.dist((4, 4), (2, 8)))
        inner = Polygon(self.L_ROOM).buffer(-c + 1e-6)
        for p in seg['points']:
            self.assertTrue(inner.covers(Point(p)), p)
        visible = route(b, {'kind': 'point', 'space_id': 'L', 'position': [1, 1]},
                        {'kind': 'point', 'space_id': 'L', 'position': [8, 2]}, wall_clearance_m=c)
        self.assertEqual(visible['segments'][0]['points'], [[1.0, 1.0], [8.0, 2.0]])
        self.assertAlmostEqual(visible['cost_m'], math.dist((1, 1), (8, 2)))

    def test_production_transit_synthetic(self):
        rooms = {'A': ([[0, 0], [4, 0], [4, 4], [0, 4]], 'office'),
                 'P': ([[4, 0], [8, 0], [8, 4], [4, 4]], 'production_zone'),
                 'B': ([[8, 0], [12, 0], [12, 4], [8, 4]], 'office'),
                 'C': ([[0, -3], [12, -3], [12, 0], [0, 0]], 'corridor')}
        doors = [('AP', 'A', 'P', [4, 2], 1.0), ('PB', 'P', 'B', [8, 2], 1.0),
                 ('AC', 'A', 'C', [1, 0], 1.0), ('CB', 'C', 'B', [11, 0], 1.0)]
        b = synthetic(rooms, doors)
        res = route(b, space('A'), space('B'))
        self.assertEqual(res['space_path'], ['A', 'C', 'B'])
        loose = challenge._challenge_route(b, {'start': space('A'), 'target': space('B')}, relax={'production_transit'})
        self.assertEqual(loose['space_path'], ['A', 'P', 'B'])
        self.assertLess(loose['cost_m'], res['cost_m'])
        into = route(b, space('A'), space('P'))
        self.assertEqual(into['space_path'], ['A', 'P'])
        out = route(b, space('P'), space('B'))
        self.assertEqual(out['space_path'], ['P', 'B'])
        # closing the corridor leaves only the forbidden transit
        blocked = route(b, space('A'), space('B'), closed=['C'])
        self.assertEqual(blocked['status'], 'unreachable')
        self.assertIn('production', blocked['unreachable_reason'])
        self.assertIn('closures', blocked['unreachable_reason'])

    def test_narrow_opening_unusable(self):
        rooms = {'A': ([[0, 0], [4, 0], [4, 4], [0, 4]], 'office'),
                 'B': ([[4, 0], [8, 0], [8, 4], [4, 4]], 'office')}
        b = synthetic(rooms, [('AB', 'A', 'B', [4, 2], 0.25)])
        res = route(b, space('A'), space('B'))
        self.assertEqual(res['status'], 'unreachable')
        self.assertIn('wall clearance 0', res['unreachable_reason'])
        self.assertEqual(route(b, space('A'), space('B'), wall_clearance_m=0.1)['status'], 'ok')

    def test_invalid_polygon_does_not_crash(self):
        bow = [[0, 0], [4, 4], [4, 0], [0, 4]]
        b = synthetic({'A': ([[0, 0], [4, 0], [4, 4], [0, 4]], 'office'), 'X': (bow, 'office')},
                      [('AX', 'A', 'X', [4, 2], 1.0)])
        res = route(b, space('A'), space('X'))
        json.dumps(res, allow_nan=False)
        self.assertIn(res['status'], {'ok', 'unreachable'})


class ChallengeOtherBuildingTests(unittest.TestCase):
    def check_building(self, bid):
        b = data.load_building(bid)
        ids = [s['id'] for s in b['spaces']]
        start, target = ids[0], ids[len(ids) // 2]
        t0 = time.perf_counter()
        res = route(b, space(start), space(target))
        elapsed = time.perf_counter() - t0
        json.dumps(res, allow_nan=False)
        self.assertIn(res['status'], {'ok', 'unreachable'})
        if res['status'] == 'ok':
            self.assertEqual((res['space_path'][0], res['space_path'][-1]), (start, target))
            self.assertEqual(len(res['element_path']), len(res['space_path']) - 1)
            self.assertAlmostEqual(res['cost_m'], sum(s['cost_m'] for s in res['steps']), places=6)
        else:
            self.assertTrue(res['unreachable_reason'])
        self.assertLess(elapsed, 2.0)
        return res

    def test_campus_building(self):
        res = self.check_building('proc-campus-dev-009')
        self.assertEqual(res['status'], 'ok')

    def test_osm_building(self):
        self.check_building('osm-way22744230')


if __name__ == '__main__':
    unittest.main()
