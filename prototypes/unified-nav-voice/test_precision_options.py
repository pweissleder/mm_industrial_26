"""Precision router options: furniture profile 'none' and controlled endpoint snapping."""
import unittest

from data import load_building
from errors import ModelError
from navigation import navigate

B = 'proc-factory-dev-003'
S = lambda suffix: f'{B}-{suffix}'
INSIDE_CNC = [-7.791, 8.193]  # centre of CNC mill H-A1-A, i.e. inside its footprint


def request(start, target, **extra):
    return {'model': 'precise', 'start': start, 'target': target, 'profile': 'stationary', 'width_m': .6,
            'safety_m': .05, 'step_free': False, 'allow_schematic_vertical': False, 'closed': [], **extra}


class PrecisionOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.building = load_building(B)

    def route(self, **kwargs):
        return navigate(self.building, request(**kwargs))

    def test_profile_none_ignores_furniture_but_stays_checked(self):
        ends = dict(start={'kind': 'space', 'id': S('F0-S020')}, target={'kind': 'space', 'id': S('F0-S021')})
        free = self.route(**ends, profile='none')
        self.assertEqual(free['status'], 'validated_2d_model')
        self.assertTrue(any('ignored' in w for w in free['warnings']))
        with self.assertRaises(ModelError):
            self.route(**ends, profile='everything')

    def test_point_inside_machine_is_rejected_without_snapping(self):
        with self.assertRaises(ModelError) as caught:
            self.route(start={'kind': 'point', 'space_id': S('F0-S001'), 'position': INSIDE_CNC},
                       target={'kind': 'space', 'id': S('F0-S019')})
        self.assertEqual(caught.exception.code, 'blocked_endpoint')

    def test_controlled_snap_leaves_only_the_containing_obstacle(self):
        out = self.route(start={'kind': 'point', 'space_id': S('F0-S001'), 'position': INSIDE_CNC},
                         target={'kind': 'space', 'id': S('F0-S019')}, snap_m=2.0)
        snap = out['snapped_endpoints'][0]
        self.assertEqual(snap['role'], 'start')
        self.assertLessEqual(snap['distance_m'], 2.0)
        self.assertGreater(snap['distance_m'], 1.0)  # half the 2.5 m machine depth plus clearance
        self.assertTrue(any('moved' in w for w in out['warnings']))
        self.assertTrue(out['fully_checked'])

    def test_snap_limit_is_respected(self):
        with self.assertRaises(ModelError):
            self.route(start={'kind': 'point', 'space_id': S('F0-S001'), 'position': INSIDE_CNC},
                       target={'kind': 'space', 'id': S('F0-S019')}, snap_m=0.3)

    def test_no_snap_through_walls(self):
        # A point that lies in the neighbouring room is never pulled into the declared room.
        neighbour = next(s for s in self.building['spaces'] if s['id'] == S('F0-S019'))
        xs = [p[0] for p in neighbour['polygon']]; ys = [p[1] for p in neighbour['polygon']]
        point = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]
        with self.assertRaises(ModelError):
            self.route(start={'kind': 'point', 'space_id': S('F0-S020'), 'position': point},
                       target={'kind': 'space', 'id': S('F0-S021')}, snap_m=2.0)
        with self.assertRaises(ModelError):
            self.route(start={'kind': 'space', 'id': S('F0-S020')}, target={'kind': 'space', 'id': S('F0-S021')}, snap_m=5)


if __name__ == '__main__':
    unittest.main()
