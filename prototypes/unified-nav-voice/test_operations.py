"""Synthetic contracts plus honest structured-input Dev checks; no language agent."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest

import data
from errors import ModelError
from operations import compute,format_answer,operation_specs,write_csv


def room(sid,x=0,y=0,width=4,depth=4,floor='F0',kind='room',room_type='office'):
    return {'id':sid,'floor_id':floor,'name':'Room '+sid,'ref':sid,'type':kind,'room_type':room_type,
            'polygon':[[x,y],[x+width,y],[x+width,y+depth],[x,y+depth]],'synthetic':False}


def building():
    return {'building':{'id':'synthetic','source':'procedural','units':'m'},
            'floors':[{'id':'F0','level':0,'name':'Ground','elevation_m':0},{'id':'F1','level':1,'name':'First','elevation_m':3}],
            'spaces':[room('A'),room('B',4),room('C',8),room('H',0,4,kind='hall'),room('Z',floor='F1')],
            'openings':[{'id':'AB','floor_id':'F0','space_ids':['A','B'],'kind':'door','position':[4,2],'width_m':1.1,'step_free':True},
                        {'id':'BC','floor_id':'F0','space_ids':['B','C'],'kind':'door','position':[8,2],'width_m':.8,'step_free':True},
                        {'id':'entrance','floor_id':'F0','space_ids':['A','outside'],'kind':'entrance','position':[0,2],'width_m':1.2,'step_free':True}],
            'vertical_connectors':[{'id':'lift','name':'Lift','kind':'lift','floor_ids':['F0','F1'],'space_ids':['A','Z'],'step_free':True}],
            'assets':[{'id':'item','name':'Pickup','type':'chair','space_id':'B','floor_id':'F0','position':[5,2]}]}


class OperationTests(unittest.TestCase):
    def test_specs_cover_nine_without_question_metadata(self):
        specs=operation_specs()
        self.assertEqual(len(specs),9)
        self.assertEqual(len({s['name'] for s in specs}),9)
        self.assertTrue(all('fields' in s for s in specs))
        self.assertFalse(any(f['name'] in {'question','answer','kind','inputs'} for s in specs for f in s['fields']))

    def test_area_largest_empty_and_input_validation(self):
        b=building();b['spaces'][1]=room('B',4,width=6)
        result=compute(b,'largest_room',{'floor_id':'F0','room_type':'office'})
        self.assertEqual(result['space_id'],'B');self.assertEqual(result['value'],24)
        self.assertEqual(compute(b,'floor_area_total',{'floor_id':'F0','room_type':'missing'})['value'],0)
        with self.assertRaises(ModelError):compute(b,'largest_room',{'floor_id':'F0','room_type':'missing'})
        with self.assertRaises(ModelError):compute(b,'wide_delivery_count',{'start':'A','floor_id':'F0','min_width':float('nan')})
        with self.assertRaises(ModelError):compute(b,'wide_delivery_count',{'start':'A','floor_id':'F0','min_width':True})

    def test_width_filter_inclusive_room_filter_and_scenario_closures(self):
        b=building();before=copy.deepcopy(b)
        result=compute(b,'wide_delivery_count',{'start':'A','floor_id':'F0','min_width':1.1})
        self.assertEqual(result['ids'],['A','B'])
        result=compute(b,'wide_delivery_count',{'start':'A','floor_id':'F0','min_width':1.1,'_scenario_closed':['AB']})
        self.assertEqual(result['ids'],['A'])
        result=compute(b,'step_free_reachable_count',{'start':'A','floor_id':'F1','closed':'lift'})
        self.assertEqual(result['value'],0)
        with self.assertRaises(ModelError):compute(b,'wide_delivery_count',{'start':'A','floor_id':'F0','min_width':1,'_scenario_closed':['A']})
        self.assertEqual(b,before)

    def test_errand_uses_room_anchors_and_validates_reachability(self):
        b=building()
        result=compute(b,'errand',{'start':'A','asset_type':'chair','floor_id':'F0','room_type':'office'})
        self.assertEqual(result['via'],'B');self.assertEqual(result['target'],'A')
        self.assertEqual(result['value'],8)
        with self.assertRaises(ModelError):compute(b,'errand',{'start':'A','asset_type':'chair','floor_id':'F0','room_type':'office','_scenario_closed':['AB']})

    def test_stacked_area_not_centroid_and_uncertain_xy_requires_opt_in(self):
        b=building();b['spaces']+=[room('Y',x=0,y=0,width=1,depth=1,floor='F1')]
        result=compute(b,'room_stacked',{'start':'A','direction':'above'})
        self.assertEqual(result['space_id'],'Z');self.assertEqual(result['trace']['overlap_m2'],16)
        b['building']['source']='swiss_dwellings'
        with self.assertRaises(ModelError):compute(b,'room_stacked',{'start':'A','direction':'above'})
        self.assertEqual(compute(b,'room_stacked',{'start':'A','direction':'above','assume_aligned':True})['space_id'],'Z')
        with self.assertRaises(ModelError):compute(b,'room_stacked',{'start':'A','direction':'sideways'})

    def test_shared_walls_corner_contact_not_neighbor_and_halls_count(self):
        b=building();b['spaces']+=[room('touch',x=4,y=-4),room('corridor',x=-4,kind='corridor')]
        result=compute(b,'shared_walls_count',{'start':'A'})
        self.assertEqual(set(result['ids']),{'B','H'})
        self.assertNotIn('touch',result['ids']);self.assertNotIn('corridor',result['ids'])

    def test_3d_uses_centroid_and_true_elevation(self):
        b=building()
        self.assertEqual(compute(b,'straight_line_3d',{'first':'A','second':'Z'})['value'],3)
        b['spaces'][0]['polygon']=[[0,0],[4,0],[4,1],[1,1],[1,4],[0,4]]
        result=compute(b,'straight_line_3d',{'first':'A','second':'Z'})
        self.assertAlmostEqual(result['trace']['first_centroid'][0],9.5/7)
        self.assertEqual(result['trace']['second_centroid'][2],3)

    def test_what_if_midpoint_copy_and_exact_threshold(self):
        b=building();before=copy.deepcopy(b)
        # Remove A-B link; new midpoint door makes B/C newly reachable from entrance.
        b['openings']=[o for o in b['openings'] if o['id']!='AB'];before=copy.deepcopy(b)
        result=compute(b,'what_if_door',{'first':'A','second':'B','floor_id':'F0'})
        self.assertAlmostEqual(result['trace']['door']['position'][0],4)
        self.assertAlmostEqual(result['trace']['door']['position'][1],2)
        self.assertEqual(set(result['ids']),{'B','C'})
        self.assertEqual(b,before)
        unchanged=compute(building(),'what_if_door',{'first':'A','second':'B','floor_id':'F0','improvement_m':0})
        self.assertEqual(unchanged['value'],0)  # Strict improvement, not >= threshold.
        with self.assertRaises(ModelError):compute(b,'what_if_door',{'first':'A','second':'Z','floor_id':'F0'})
        b['spaces'].append(room('corner',x=4,y=-4))
        with self.assertRaises(ModelError):compute(b,'what_if_door',{'first':'A','second':'corner','floor_id':'F0'})

    def test_duplicate_names_use_room_ref_and_csv_quotes_utf8(self):
        result={'operation':'room_stacked','space_id':'Z','space_name':'Büro','space_ref':'1.02','value':'Z'}
        answer=format_answer('room_stacked',result)
        self.assertEqual(answer,'Answer: 1.02')
        rows=[('id1','Büro, "A"\n'+answer)]
        parsed=list(csv.reader(io.StringIO(write_csv(rows).decode('utf-8'))))
        self.assertEqual(parsed,[['question_id','answer'],['id1',rows[0][1]]])
        scorer=data.reference_module('score')
        self.assertTrue(scorer.score_reply({'type':'number','value':2,'relative_tolerance':0,'absolute_tolerance':0},format_answer('shared_walls_count',{'operation':'shared_walls_count','value':2})))

    def check_split(self,split,expected_total):
        """Kit 1.1.0 answers (verified against the pinned commit) through the unchanged official scorer."""
        scorer=data.reference_module('score');before=hashlib.sha256(data.ARCHIVE.read_bytes()).hexdigest()
        kinds=set();total=0;failed=[]
        for example in data.examples(split):
            with self.subTest(kind=example['kind'],question=example['id']):
                b=data.load_building(example['building_id']);snapshot=copy.deepcopy(b)
                result=compute(b,example['kind'],example['inputs'])
                ok=scorer.score_reply(example['answer'],format_answer(example['kind'],result))
                if not ok:failed.append(example['id'])
                self.assertTrue(ok)
                self.assertEqual(b,snapshot)
                kinds.add(example['kind']);total+=1
        self.assertEqual(total,expected_total);self.assertEqual(len(kinds),9);self.assertEqual(failed,[])
        self.assertEqual(hashlib.sha256(data.ARCHIVE.read_bytes()).hexdigest(),before)

    def test_all_dev_tools_against_unchanged_official_scorer(self):
        self.check_split('dev',138)

    def test_all_train_tools_against_unchanged_official_scorer(self):
        self.check_split('train',517)

    def test_errand_and_what_if_use_official_1_1_0_route_lengths(self):
        b=building()
        errand=compute(b,'errand',{'start':'A','asset_type':'chair','floor_id':'F0','room_type':'office'})
        model=data.reference_map(b)
        self.assertAlmostEqual(errand['trace']['pickup_length_m'],model.route('A','B').length_m,places=2)
        self.assertEqual(errand['cost_model'],'official_kit_1_1_0')
        # No route round the outside: two entrances do not join separate wings.
        wings=building();wings['openings']=[o for o in wings['openings'] if o['id']!='AB']
        wings['openings'].append({'id':'entrance-c','floor_id':'F0','space_ids':['C','outside'],'kind':'entrance','position':[12,2],'width_m':1.2,'step_free':True})
        with self.assertRaises(ModelError):compute(wings,'errand',{'start':'A','asset_type':'chair','floor_id':'F0','room_type':'office'})
        # B already walks out through C's entrance (6 m); via the new A-B door it is also 6 m: no gain.
        result=compute(wings,'what_if_door',{'first':'A','second':'B','floor_id':'F0'})
        self.assertEqual(result['ids'],[])
        self.assertEqual(result['trace']['door']['width_m'],.9)


if __name__=='__main__':unittest.main(verbosity=2)
