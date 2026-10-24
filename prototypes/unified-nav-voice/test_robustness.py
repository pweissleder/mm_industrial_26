"""Checks that the independent audit oracle accepts good and rejects bad paths."""
import copy
import math
import unittest
from navigation import navigate
from robustness_audit import independent_check
from test_geometry import fixture, rectangle, asset
from test_navigation import door, point


class IndependentAuditTests(unittest.TestCase):
    def request(self, start, target):
        return {'start':start,'target':target,'profile':'all','width_m':.6,'safety_m':.05}

    def test_accepts_route_across_actual_aperture(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6,w=6,h=6)],openings=[door('AB','A','B',(6,3))])
        req=self.request(point('A',(1,3)),point('B',(11,3)))
        route=navigate(b,req)
        proof=independent_check(b,req,route)
        self.assertAlmostEqual(proof['length_m'],10)
        self.assertGreaterEqual(proof['minimum_wall_m'],.35-2e-6)

    def test_rejects_tampered_route_through_obstacle(self):
        b=fixture(assets=[asset(position=(5,3),width=2,depth=2)])
        req=self.request(point('A',(1,3)),point('A',(9,3)))
        route=navigate(b,req)
        independent_check(b,req,route)
        broken=copy.deepcopy(route)
        broken['segments'][0]['points']=[[1,3],[9,3]]
        broken['segments'][0]['length_m']=8
        broken['length_m']=8
        with self.assertRaisesRegex(AssertionError,'obstacle clearance'):
            independent_check(b,req,broken)

    def test_rejects_tampered_wall_clearance(self):
        b=fixture();req=self.request(point('A',(1,1)),point('A',(9,1)))
        route=navigate(b,req)
        route['segments'][0]['points']=[[1,.1],[9,.1]]
        with self.assertRaisesRegex(AssertionError,'wall clearance'):
            independent_check(b,req,route)

    def test_rejects_tampered_length(self):
        b=fixture();req=self.request(point('A',(1,1)),point('A',(9,1)))
        route=navigate(b,req);route['length_m']+=1
        with self.assertRaisesRegex(AssertionError,'total length mismatch'):
            independent_check(b,req,route)


if __name__=='__main__':unittest.main()
