import math
import time
import unittest
from shapely.geometry import Point, Polygon, LineString

from errors import ModelError
from geometry import (asset_polygon, build_space, analyze_space, door_side,
                      access_candidates, parameters)


def rectangle(sid='A', x=0, y=0, w=10, h=8, floor='F0'):
    return {'id': sid, 'floor_id': floor, 'type': 'room', 'room_type': 'office',
            'name': sid, 'polygon': [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}


def asset(aid='machine', sid='A', position=(5,4), width=2, depth=3, angle=0, kind='cnc_mill', floor='F0'):
    return {'id': aid, 'space_id': sid, 'floor_id': floor, 'position': list(position),
            'name': aid, 'type': kind, 'category': 'machine', 'source': 'generated',
            'footprint': {'width_m': width, 'depth_m': depth, 'rotation_deg': angle}}


def fixture(spaces=None, assets=None, openings=None, connectors=None, floors=None):
    return {'building': {'id':'synthetic-test', 'source':'procedural', 'source_ref':{'generator':'factory'}},
            'floors': floors or [{'id':'F0','level':0,'elevation_m':0,'name':'Ground'}],
            'spaces': spaces or [rectangle()], 'assets': assets or [],
            'openings': openings or [], 'vertical_connectors': connectors or []}


class GeometryTests(unittest.TestCase):
    def test_free_direct_no_anchor(self):
        ctx=build_space(fixture(),'A')
        points=ctx.path((1,1),(9,1))
        self.assertEqual(points,[[1.,1.],[9.,1.]])
        self.assertAlmostEqual(sum(math.dist(a,b) for a,b in zip(points,points[1:])),8)

    def test_rotated_footprint_centre_ccw(self):
        poly=asset_polygon(asset(position=(4,3),width=4,depth=2,angle=90))
        self.assertAlmostEqual(poly.area,8)
        self.assertEqual(tuple(round(x,6) for x in poly.bounds),(3,1,5,5))
        self.assertAlmostEqual(poly.centroid.x,4)
        self.assertAlmostEqual(poly.centroid.y,3)

    def test_machine_detour_original_clearance_every_edge(self):
        b=fixture(assets=[asset()]);ctx=build_space(b,'A',safety_m=.1)
        points=ctx.path((1,4),(9,4))
        self.assertGreater(len(points),2)
        route=LineString(points)
        self.assertGreater(route.length,8)
        self.assertGreaterEqual(route.distance(asset_polygon(b['assets'][0])),.4-1e-7)
        ctx.validate_path(points)

    def test_full_barrier_disconnected_not_invented(self):
        ctx=build_space(fixture(assets=[asset(position=(5,4),width=1,depth=8)]),'A')
        with self.assertRaises(ModelError) as caught:ctx.path((1,4),(9,4))
        self.assertEqual(caught.exception.code,'disconnected')

    def test_hole_is_not_walkable_and_is_avoided(self):
        room=rectangle();room['holes']=[[[4,2],[6,2],[6,6],[4,6]]]
        ctx=build_space(fixture(spaces=[room]),'A')
        points=ctx.path((1,4),(9,4))
        self.assertGreater(len(points),2)
        self.assertTrue(ctx.room.covers(LineString(points)))
        self.assertFalse(ctx.free.covers(Point(5,4)))
        ctx.validate_path(points)

    def test_blocked_and_outside_points_fail_without_projection(self):
        ctx=build_space(fixture(assets=[asset()]),'A')
        for p in [(5,4),(-1,4),(.01,4)]:
            with self.subTest(p=p),self.assertRaises(ModelError) as caught:ctx.path(p,(9,4))
            self.assertEqual(caught.exception.code,'blocked_endpoint')

    def test_spatial_asset_selection_crosses_declared_room_assignment(self):
        b=fixture(assets=[asset(sid='wrong-room-id',position=(.2,4),width=2,depth=2)])
        ctx=build_space(b,'A')
        self.assertEqual(len(ctx.obstacle_records),1)
        self.assertFalse(ctx.free.covers(Point(.8,4)))

    def test_missing_footprint_and_invalid_room_report_structured_error(self):
        bad=asset();del bad['footprint']
        with self.assertRaises(ModelError):build_space(fixture(assets=[bad]),'A')
        room=rectangle();room['polygon']=[[0,0],[5,5],[5,0],[0,5]]
        with self.assertRaises(ModelError) as caught:build_space(fixture(spaces=[room]),'A')
        self.assertEqual(caught.exception.code,'geometry_unsupported')

    def test_parameter_finiteness_and_ranges(self):
        for value in [float('nan'),float('inf'),True,-1,0]:
            with self.subTest(value=value),self.assertRaises(ModelError):parameters('all',value,0)
        with self.assertRaises(ModelError):parameters('bad',.6,.1)

    def test_profiles_do_not_classify_forklift_as_stationary(self):
        b=fixture(assets=[asset(kind='forklift')])
        self.assertEqual(len(build_space(b,'A','stationary').obstacle_records),0)
        self.assertEqual(len(build_space(b,'A','all').obstacle_records),1)

    def test_machine_candidates_are_outside_target_not_its_centre(self):
        a=asset();ctx=build_space(fixture(assets=[a]),'A')
        candidates=access_candidates(ctx,a,(1,4))
        self.assertGreater(len(candidates),3)
        for p in candidates:
            self.assertTrue(ctx.segment_valid(p,p))
            self.assertGreaterEqual(Point(p).distance(asset_polygon(a)),ctx.clearance-1e-7)

    def test_door_width_and_no_wall_clearance_trap(self):
        ctx=build_space(fixture(),'A')
        opening={'id':'door','position':[0,4],'width_m':1.4}
        side=door_side(ctx,opening)
        self.assertAlmostEqual(side['inner'][0],.350001)
        self.assertTrue(ctx.segment_valid(side['inner'],side['inner']))
        opening['width_m']=.6
        with self.assertRaises(ModelError) as caught:door_side(ctx,opening)
        self.assertEqual(caught.exception.code,'door_too_narrow')

    def test_door_oversized_corner_or_blocked_is_rejected(self):
        ctx=build_space(fixture(),'A')
        for opening in [{'id':'wide','position':[0,4],'width_m':8.1},
                        {'id':'corner','position':[0,0],'width_m':1}]:
            with self.assertRaises(ModelError) as caught:door_side(ctx,opening)
            self.assertEqual(caught.exception.code,'geometry_unsupported')
        blocked=build_space(fixture(assets=[asset(position=(.8,4),width=1.2,depth=2)]),'A')
        with self.assertRaises(ModelError) as caught:door_side(blocked,{'id':'blocked','position':[0,4],'width_m':1.4})
        self.assertEqual(caught.exception.code,'portal_blocked')

    def test_analyze_exports_original_and_derived_geometries(self):
        result=analyze_space(fixture(assets=[asset()]),'A')
        self.assertEqual(result['geometry']['room']['type'],'Polygon')
        self.assertTrue(result['geometry']['components'])
        self.assertEqual(result['geometry']['obstacles'][0]['id'],'machine')

    def test_computation_deadline_and_vertex_limit_are_structured(self):
        ctx=build_space(fixture(),'A',deadline=time.perf_counter()-1)
        with self.assertRaises(ModelError) as caught:ctx.path((1,1),(9,1))
        self.assertEqual(caught.exception.code,'computation_limit')
        room=rectangle();room['polygon']=list(Point(0,0).buffer(6,quad_segs=200).exterior.coords)
        ctx=build_space(fixture(spaces=[room],assets=[asset(position=(0,0),width=2,depth=4)]),'A')
        with self.assertRaises(ModelError) as caught:ctx.path((-4,0),(4,0))
        self.assertEqual(caught.exception.code,'computation_limit')


if __name__=='__main__':unittest.main(verbosity=2)
