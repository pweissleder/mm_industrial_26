import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
from data import load_building,source_hash,read_member,reference_map,REFERENCE
from errors import ModelError
from service import Service
from validation import validate_footprints

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service=Service();self.bid='proc-factory-dev-003';self.prefix=self.bid+'-'

    def test_copy_isolation_and_reference_regression(self):
        original=load_building(self.bid);a=self.prefix+'F0-S020';b=self.prefix+'F0-S021'
        expected=reference_map(original).route(a,b).length_m
        request={'start':{'kind':'space','id':a},'target':{'kind':'space','id':b},'step_free':False}
        self.assertEqual(self.service.reference_route(self.bid,request)['benchmark_cost_m'],expected)
        self.service.change(self.bid,{'action':'close','element':a})
        self.assertEqual(load_building(self.bid),original)
        self.service.change(self.bid,{'action':'reset'})
        self.assertEqual(self.service.reference_route(self.bid,request)['benchmark_cost_m'],expected)
        self.assertEqual(source_hash(self.bid),hashlib.sha256(read_member('data/dev/'+self.bid+'/building.json')).hexdigest())
        self.assertEqual((REFERENCE/'starter_agent/building_map.py').read_bytes().replace(b'\r\n',b'\n'),read_member('starter_agent/building_map.py').replace(b'\r\n',b'\n'))

    def test_ram_add_reset_and_revision(self):
        before=self.service.building(self.bid)
        first=self.prefix+'F0-S001';second=self.prefix+'F0-S002'
        changed=self.service.change(self.bid,{'action':'add_door','first':first,'second':second})
        self.assertEqual(len(changed['data']['openings']),len(before['data']['openings'])+1)
        self.assertGreater(changed['revision'],before['revision'])
        other='proc-factory-dev-004';sid=load_building(other)['spaces'][0]['id']
        self.service.change(other,{'action':'close','element':sid})
        self.service.change(self.bid,{'action':'reset'})
        self.assertEqual(self.service.building(self.bid)['data'],before['data'])
        self.assertFalse(self.service.building(other)['closed'])

    def test_invalid_scenario_atomic(self):
        before=self.service.building(self.bid)
        with self.assertRaises(ModelError):self.service.change(self.bid,{'action':'close','element':'not-real'})
        self.assertEqual(before,self.service.building(self.bid))

    def test_svg_proof_two_buildings_and_unknown_source(self):
        for bid in ['proc-factory-dev-003','proc-factory-dev-004','proc-campus-dev-000']:
            proof=validate_footprints(bid)
            self.assertTrue(proof['verified'],proof)
            self.assertGreater(proof['assets_checked'],0)
            self.assertLess(proof['max_discrepancy_m'],1e-5)
        self.assertFalse(validate_footprints('resplan-14509')['verified'])
        with self.assertRaises(ModelError):self.service.navigate('resplan-14509',{})

    def test_catalog_and_unknown_data(self):
        catalog=self.service.catalog();self.assertEqual(sum(r['split']=='dev' for r in catalog),40);self.assertEqual({r['split'] for r in catalog},{'dev','train'})
        self.assertEqual(len(self.service.catalog(include_train=False)),40)
        from data import examples
        self.assertTrue({q['building_id'] for q in examples('train')}<={r['id'] for r in catalog})
        self.assertIn(self.bid,{r['id'] for r in catalog})
        with self.assertRaises(ModelError):load_building('../../x')
        with self.assertRaises(ModelError):read_member('../.env')

    def test_operation_and_closed_scenario_contract(self):
        args={'operation':'floor_area_total','arguments':{'floor_id':self.prefix+'F0','room_type':'office'}}
        result=self.service.operation(self.bid,args)
        self.assertEqual(result['model'],'benchmark');self.assertIn('Answer:',result['formatted_answer'])
        self.assertTrue(result['value']>0)

    def test_comparison_uses_identical_inner_endpoints_and_measured_clearance(self):
        sid=self.prefix+'F0-S019'
        result=self.service.navigate(self.bid,{'start':{'kind':'door','id':self.prefix+'F0-O0023','space_id':sid},
            'target':{'kind':'door','id':self.prefix+'F0-O0024','space_id':sid},'profile':'all'})
        comparison=result['reference_comparison']
        self.assertTrue(comparison['same_endpoints'])
        self.assertEqual(comparison['segments'][0]['points'][0],result['start']['positions'][0])
        self.assertEqual(comparison['segments'][0]['points'][-1],result['target']['selected_position'])
        self.assertTrue(comparison['obstacle_intersections'])
        self.assertAlmostEqual(comparison['anchor_comparison_length_m'],4.30000219,places=6)
        self.assertGreaterEqual(result['validation_summary']['minimum_selected_obstacle_clearance_m'],result['clearance_m'])

    def test_invalid_closure_container_is_structured(self):
        for closed in ['not-list',{},[[]],[False]]:
            with self.subTest(closed=closed),self.assertRaises(ModelError) as error:
                self.service.navigate(self.bid,{'closed':closed})
            self.assertEqual(error.exception.code,'invalid_input')

    def test_failed_reference_comparison_does_not_discard_physical_route(self):
        request={'start':{'kind':'space','id':self.prefix+'F0-S020'},'target':{'kind':'space','id':self.prefix+'F0-S021'}}
        with patch.object(self.service,'reference_route',side_effect=KeyError('synthetic comparison failure')):
            result=self.service.navigate(self.bid,request)
        self.assertTrue(result['fully_checked'])
        self.assertEqual(result['reference_comparison']['status'],'not_comparable')

if __name__=='__main__':unittest.main(verbosity=2)
