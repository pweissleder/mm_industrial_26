"""Shared no-leaf apertures, start access and conservative failure explanations."""
import copy
import math
import unittest
from unittest.mock import patch
from shapely.geometry import LineString

from data import load_building
from diagnostics import annotate_error
from errors import ModelError
from geometry import build_space, door_side, effective_opening, asset_polygon
from navigation import navigate
from test_geometry import rectangle, fixture, asset
from test_navigation import door, point


def open_halls():
    spaces = [rectangle('A', w=6, h=6), rectangle('B', x=6, w=6, h=6)]
    for s in spaces:s['type']='hall'
    opening = door('AB','A','B',(6,3),6.1);opening['kind']='opening';opening['synthetic']=False
    return fixture(spaces=spaces,openings=[opening])


class PortalUsabilityTests(unittest.TestCase):
    def test_full_shared_aperture_uses_actual_width_without_mutation(self):
        b=open_halls();before=copy.deepcopy(b)
        r=navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})
        t=r['transitions'][0]
        self.assertAlmostEqual(t['width_m'],6)
        self.assertEqual(t['aperture']['nominal_width_m'],6.1)
        self.assertEqual(t['aperture']['effective_width_m'],6)
        self.assertEqual(len(t['aperture']['segment']),2)
        self.assertAlmostEqual(r['length_m'],10)
        self.assertEqual(b,before)
        self.assertTrue(r['fully_checked'])

    def test_physical_doors_room_boundaries_and_offset_openings_not_repaired(self):
        for mode in ('door','entrance','room','synthetic','offcentre','campus'):
            b=open_halls()
            if mode in {'door','entrance'}:b['openings'][0]['kind']=mode
            elif mode=='room':b['spaces'][0]['type']='room'
            elif mode=='synthetic':b['openings'][0]['synthetic']=True
            elif mode=='offcentre':b['openings'][0]['position']=[6,3.1]
            elif mode=='campus':b['building']['source_ref']['generator']='campus'
            with self.subTest(mode=mode),self.assertRaises(ModelError):
                navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})

    def test_gap_and_obstacle_are_not_ignored(self):
        b=open_halls();b['spaces'][1]=rectangle('B',x=6.01,w=6,h=6);b['spaces'][1]['type']='hall'
        with self.assertRaises(ModelError):navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})
        b=open_halls();b['assets']=[asset(position=(6,3),width=1,depth=6)]
        with self.assertRaises(ModelError):navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})

    def test_effective_width_is_checked_after_restricting_nominal_width(self):
        b=open_halls();physical=effective_opening(b,b['openings'][0])
        # Width6.1 must never make a person envelope6.05 fit a physical6m edge.
        ctx=build_space(b,'A',width_m=4,safety_m=1.025)
        with self.assertRaises(ModelError) as caught:door_side(ctx,physical)
        self.assertEqual(caught.exception.code,'door_too_narrow')

    def test_start_asset_is_safe_explicit_access_and_consistent_floor(self):
        a=asset();b=fixture(assets=[a]);r=navigate(b,{'start':{'kind':'asset','id':a['id']},'target':point('A',(1,1))})
        self.assertNotEqual(r['start']['positions'][0],a['position'])
        self.assertIn('Asset start',r['start']['resolution'])
        self.assertGreaterEqual(LineString(r['segments'][0]['points']).distance(asset_polygon(a)),.35-1e-7)
        b['assets'][0]['floor_id']='wrong'
        with self.assertRaises(ModelError) as caught:navigate(b,{'start':{'kind':'asset','id':a['id']},'target':point('A',(1,1))})
        self.assertEqual(caught.exception.code,'geometry_unsupported')

    def test_real_closed_reception_door_has_valid_hall_alternative(self):
        b=load_building('proc-factory-dev-003');before=copy.deepcopy(b)
        req={'start':{'kind':'space','id':'proc-factory-dev-003-F0-S019'},'target':{'kind':'asset','id':'proc-factory-dev-003-A00001'},'profile':'all'}
        direct=navigate(b,req)
        alternate=navigate(b,{**req,'closed':['proc-factory-dev-003-F0-O0024']})
        self.assertGreater(alternate['length_m'],direct['length_m'])
        self.assertNotIn('proc-factory-dev-003-F0-O0024',[t['id'] for t in alternate['transitions']])
        self.assertTrue(any(t.get('aperture') for t in alternate['transitions']))
        self.assertEqual(b,before)
        self.assertTrue(alternate['fully_checked'])

    def diagnose(self,b,req):
        try:navigate(b,req)
        except ModelError as error:return annotate_error(b,req,error).details['diagnosis']
        self.fail('Expected failed route')

    def test_diagnostics_closure_width_and_empty_room_are_distinct(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6,w=6,h=6)],openings=[door('AB','A','B',(6,3))])
        req={'start':point('A',(1,3)),'target':point('B',(11,3))}
        self.assertEqual(self.diagnose(b,{**req,'closed':['AB']})['category'],'closed')
        self.assertEqual(self.diagnose(b,{**req,'width_m':1.2,'safety_m':.3})['category'],'too_narrow')
        self.assertEqual(self.diagnose(b,{'start':{'kind':'space','id':'A'},'target':{'kind':'space','id':'B'},'width_m':5,'safety_m':2})['category'],'unreachable_in_model')

    def test_irrelevant_unsupported_does_not_turn_unknown_into_certainty(self):
        b=fixture(assets=[asset(position=(5,4),width=1,depth=8)])
        req={'start':point('A',(1,4)),'target':point('A',(9,4))}
        d=self.diagnose(b,req)
        self.assertEqual(d['category'],'unresolved')
        self.assertNotIn('is unreachable',d['summary'])

    def test_blocked_point_marks_actual_selected_obstacle(self):
        b=fixture(assets=[asset()])
        d=self.diagnose(b,{'start':point('A',(5,4)),'target':point('A',(1,1))})
        self.assertEqual(d['category'],'unreachable_in_model')
        self.assertTrue(any(item['id']=='machine' and 'footprint' in item['reason'] for item in d['elements']))

    def test_diagnostic_does_not_blame_irrelevant_closed_or_narrow_leaf(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6,w=6,h=6),rectangle('C',y=6,w=6,h=6)],
                  openings=[door('needed','A','B',(6,3)),door('irrelevant','A','C',(3,6))])
        req={'start':point('A',(1,3)),'target':point('B',(11,3))}
        for options in ({'closed':['needed','irrelevant']},{'width_m':1.2,'safety_m':.3}):
            d=self.diagnose(b,{**req,**options})
            self.assertEqual({e['id'] for e in d['elements']},{'needed'})

    def test_failed_diagnosis_does_not_retain_proven_element_labels(self):
        b=fixture(assets=[asset()])
        with patch('diagnostics.prepare_assets',side_effect=ValueError('controlled diagnostic failure')):
            d=self.diagnose(b,{'start':point('A',(5,4)),'target':point('A',(1,1))})
        self.assertEqual(d['category'],'unresolved')
        self.assertEqual(d['elements'],[])


if __name__=='__main__':unittest.main()
