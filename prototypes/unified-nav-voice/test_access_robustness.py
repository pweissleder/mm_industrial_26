"""Regressions for missed narrow access intervals, not benchmark answers."""
import math
import unittest

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from data import load_building
from errors import ModelError
from geometry import access_candidates, asset_polygon, build_space
from navigation import navigate


class AccessRobustnessTests(unittest.TestCase):
    def test_short_offcentre_interval_is_not_lost(self):
        # L-shaped planning room: target is accessible only through a narrow
        # off-centre interval; neither old corners nor midpoints lie in it.
        room = unary_union([box(8, 0, 12, 8), box(6.8, 3.8, 8, 4.8)])
        asset = {'id': 'machine', 'type': 'cnc_mill', 'floor_id': 'F0',
                 'space_id': 'R', 'position': [5, 5],
                 'footprint': {'width_m': 4, 'depth_m': 2, 'rotation_deg': 0}}
        building = {'building': {'source': 'procedural', 'source_ref': {'generator': 'factory'}},
                    'spaces': [{'id': 'R', 'floor_id': 'F0', 'polygon': list(room.exterior.coords)}],
                    'assets': [asset]}
        ctx = build_space(building, 'R')
        start = (10, 2)
        candidates = access_candidates(ctx, asset, start)
        valid = []
        for target in candidates:
            try:
                path = ctx.path(start, target)
            except ModelError:
                continue
            # Separate checks use original polygons and full line distances.
            line = LineString(path) if len(path) > 1 else Point(path[0])
            self.assertTrue(room.covers(line))
            self.assertGreaterEqual(line.distance(room.boundary), .35-1e-7)
            self.assertGreaterEqual(line.distance(asset_polygon(asset)), .35-1e-7)
            valid.append(target)
        self.assertTrue(valid)

    def test_real_factory_high_clearance_access_interval(self):
        b = load_building('proc-factory-dev-007')
        target = next(a for a in b['assets'] if a['id'].endswith('-A00007'))
        for profile in ('stationary', 'all'):
            with self.subTest(profile=profile):
                args = {'start': {'kind': 'space', 'id': target['space_id']},
                        'target': {'kind': 'asset', 'id': target['id']},
                        'profile': profile, 'width_m': .6, 'safety_m': .6}
                result = navigate(b, args)
                self.assertTrue(result['fully_checked'])
                room = Polygon(next(s for s in b['spaces'] if s['id'] == target['space_id'])['polygon'])
                machine = asset_polygon(target)
                for segment in result['segments']:
                    pts = segment['points']
                    line = LineString(pts) if len(pts) > 1 else Point(pts[0])
                    self.assertTrue(room.covers(line))
                    self.assertGreaterEqual(line.distance(room.boundary), .9-1e-7)
                    self.assertGreaterEqual(line.distance(machine), .9-1e-7)
                self.assertTrue(math.isfinite(result['length_m']))


if __name__ == '__main__':
    unittest.main()
