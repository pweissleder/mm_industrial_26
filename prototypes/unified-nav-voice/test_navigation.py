import copy
import json
import math
import unittest

from data import load_building, reference_map, source_hash
from errors import ModelError
from geometry import build_space, asset_polygon
from navigation import navigate
from test_geometry import rectangle, asset, fixture


def door(oid,a,b,position,width=1.4,step_free=True,floor='F0'):
    return {'id':oid,'space_ids':[a,b],'floor_id':floor,'position':list(position),'width_m':width,'step_free':step_free,'kind':'door'}


def point(space,xy):return {'kind':'point','space_id':space,'position':list(xy)}


class NavigationTests(unittest.TestCase):
    def test_target_asset_with_inconsistent_floor_is_rejected(self):
        b=fixture(assets=[asset()]);b['assets'][0]['floor_id']='wrong-floor'
        with self.assertRaises(ModelError) as error:
            navigate(b,{'start':point('A',(1,1)),'target':{'kind':'asset','id':'machine'}})
        self.assertEqual(error.exception.code,'geometry_unsupported')

    def test_invalid_structured_endpoint_types_are_model_errors(self):
        for req in [
            {'start':{'kind':'space','id':[]},'target':point('A',(1,1))},
            {'start':point([], (1,1)),'target':point('A',(1,1))},
            {'start':point('A',(1,1)),'target':{'kind':'asset','id':[]}},
            {'start':point('A',(1,1)),'target':point('A',(2,1)),'profile':[]},
        ]:
            with self.subTest(req=req),self.assertRaises(ModelError) as caught:navigate(fixture(),req)
            self.assertEqual(caught.exception.code,'invalid_input')

    def test_single_room_exact_direct_and_immutable_input(self):
        b=fixture();before=copy.deepcopy(b)
        r=navigate(b,{'start':point('A',(1,1)),'target':point('A',(9,1))})
        self.assertAlmostEqual(r['length_m'],8)
        self.assertTrue(r['fully_checked'])
        self.assertEqual(r['transitions'],[])
        self.assertEqual(b,before)

    def test_multispace_door_crossing_segments_are_continuous(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6,w=6,h=6)],openings=[door('AB','A','B',(6,3))],assets=[asset(position=(3,3),width=2,depth=2)])
        r=navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})
        self.assertEqual([x['id'] for x in r['transitions']],['AB'])
        self.assertGreater(r['length_m'],10)
        for a,z in zip(r['segments'],r['segments'][1:]):self.assertEqual(a['points'][-1],z['points'][0])
        self.assertAlmostEqual(sum(s['length_m'] for s in r['segments']),r['length_m'])
        for segment in r['segments']:
            if segment['kind']=='local_visibility':
                build_space(b,segment['space_id']).validate_path(segment['points'])

    def test_no_gap_teleport_and_no_unmarked_wall_crossing(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6.01,w=6,h=6)],openings=[door('gap','A','B',(6,3))])
        with self.assertRaises(ModelError) as caught:navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})
        self.assertEqual(caught.exception.code,'geometry_unsupported')
        b['openings']=[]
        with self.assertRaises(ModelError) as caught:navigate(b,{'start':point('A',(1,3)),'target':point('B',(11,3))})
        self.assertEqual(caught.exception.code,'model_blocked')

    def test_closure_step_free_and_too_narrow_door(self):
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('B',x=6,w=6,h=6)],openings=[door('AB','A','B',(6,3))])
        req={'start':point('A',(1,3)),'target':point('B',(11,3))}
        for extra in [{'closed':['AB']},{'closed':['A']}]:
            with self.subTest(extra=extra),self.assertRaises(ModelError):navigate(b,{**req,**extra})
        b['openings'][0]['step_free']=False
        with self.assertRaises(ModelError):navigate(b,{**req,'step_free':True})
        self.assertTrue(navigate(b,req)['fully_checked'])
        b['openings'][0]['width_m']=.5
        with self.assertRaises(ModelError) as caught:navigate(b,req)
        self.assertIn('door_too_narrow',caught.exception.details['unsupported_transition_counts'])

    def test_global_alternative_uses_real_local_costs(self):
        b=fixture(spaces=[rectangle('A',w=10,h=10),rectangle('B',x=10,w=10,h=10),rectangle('C',y=10,w=10,h=10),rectangle('D',x=10,y=10,w=10,h=10)],
                  openings=[door('AB','A','B',(10,5)),door('AC','A','C',(5,10)),door('BD','B','D',(15,10)),door('CD','C','D',(10,15))],
                  assets=[asset(sid='B',position=(15,4.9),width=8.4,depth=7.8)])
        req={'start':point('A',(1,5)),'target':point('D',(19,15))}
        chosen=navigate(b,req)
        forced=navigate(b,{**req,'closed':['AC']})
        self.assertEqual([t['id'] for t in chosen['transitions']],['AC','CD'])
        self.assertEqual([t['id'] for t in forced['transitions']],['AB','BD'])
        self.assertLess(chosen['length_m']+.1,forced['length_m'])

    def test_machine_target_uses_access_candidate_and_auto_space_is_disclosed(self):
        b=fixture(assets=[asset()])
        r=navigate(b,{'start':{'kind':'space','id':'A'},'target':{'kind':'asset','id':'machine'}})
        selected=r['target']['selected_position']
        self.assertNotEqual(selected,b['assets'][0]['position'])
        self.assertIn('Automatically',r['start']['resolution'])
        ctx=build_space(b,'A');self.assertTrue(ctx.segment_valid(selected,selected))

    def test_vertical_requires_explicit_schematic_opt_in_and_separate_cost(self):
        floors=[{'id':'F0','level':0,'elevation_m':0,'name':'Ground'},{'id':'F1','level':1,'elevation_m':3,'name':'Upper'}]
        cs=[{'id':'stair','kind':'stairs','floor_ids':['F0','F1'],'space_ids':['A','U'],'step_free':False},
            {'id':'lift','kind':'lift','floor_ids':['F0','F1'],'space_ids':['A','U'],'step_free':True}]
        b=fixture(spaces=[rectangle('A',w=6,h=6),rectangle('U',w=6,h=6,floor='F1')],connectors=cs,floors=floors)
        req={'start':point('A',(1,1)),'target':point('U',(4,4))}
        with self.assertRaises(ModelError) as caught:navigate(b,req)
        self.assertEqual(caught.exception.code,'geometry_unsupported')
        r=navigate(b,{**req,'allow_schematic_vertical':True,'step_free':True})
        self.assertFalse(r['fully_checked'])
        self.assertEqual(r['schematic_transition_cost_m'],21)
        self.assertEqual(r['transitions'][0]['kind'],'lift')
        self.assertAlmostEqual(r['search_cost_m_equivalent'],r['length_m']+21)
        self.assertTrue(all(s['floor_id'] in {'F0','F1'} for s in r['segments']))
        with self.assertRaises(ModelError):navigate(b,{**req,'allow_schematic_vertical':True,'step_free':True,'closed':['lift']})

    def test_real_s019_reference_collision_detour_and_source_unchanged(self):
        bid='proc-factory-dev-003';before=source_hash(bid);b=load_building(bid);snapshot=copy.deepcopy(b)
        ref=reference_map(b);old=ref.route('proc-factory-dev-003-F0-S019','proc-factory-dev-003-F0-S001').length_m
        sid='proc-factory-dev-003-F0-S019'
        req={'start':{'kind':'door','id':'proc-factory-dev-003-F0-O0023','space_id':sid},'target':{'kind':'door','id':'proc-factory-dev-003-F0-O0024','space_id':sid}}
        free=navigate(b,{**req,'profile':'stationary'})
        detour=navigate(b,{**req,'profile':'all'})
        self.assertAlmostEqual(free['length_m'],4.299998,places=5)
        self.assertGreater(detour['length_m'],free['length_m'])
        for segment in detour['segments']:build_space(b,segment['space_id'],'all').validate_path(segment['points'])
        self.assertEqual(source_hash(bid),before)
        self.assertEqual(b,snapshot)
        self.assertEqual(reference_map(b).route('proc-factory-dev-003-F0-S019','proc-factory-dev-003-F0-S001').length_m,old)

    def test_real_multispace_reception_to_cnc_target(self):
        b=load_building();r=navigate(b,{'start':{'kind':'space','id':'proc-factory-dev-003-F0-S019'},'target':{'kind':'asset','id':'proc-factory-dev-003-A00001'},'profile':'all'})
        self.assertTrue(r['fully_checked'])
        self.assertTrue(any(t['id']=='proc-factory-dev-003-F0-O0024' for t in r['transitions']))
        self.assertNotEqual(r['target']['selected_position'],b['assets'][0]['position'])
        self.assertGreater(r['length_m'],0)

    def test_second_real_factory_local_safe_navigation(self):
        b=load_building('proc-factory-dev-004');space=b['spaces'][0]
        ctx=build_space(b,space['id']);p=ctx.automatic_point()
        # A nonzero safe endpoint, chosen from the same real free component.
        candidates=[(p[0]+dx,p[1]+dy) for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]]
        q=next(q for q in candidates if ctx.segment_valid(p,q))
        r=navigate(b,{'start':point(space['id'],p),'target':point(space['id'],q)})
        self.assertTrue(r['fully_checked']);self.assertAlmostEqual(r['length_m'],1)


if __name__=='__main__':unittest.main(verbosity=2)
