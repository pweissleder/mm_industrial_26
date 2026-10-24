"""Explicit dataset examples are a test lab, not answer-dependent routing."""
import copy
import hashlib
import unittest
from unittest.mock import patch

from data import ARCHIVE
from errors import ModelError
from service import Service


class ExampleLabTests(unittest.TestCase):
    def setUp(self):
        self.service=Service()

    def test_train_dev_only_and_copy_isolation(self):
        for split in ('train','dev'):
            rows=self.service.examples(split)
            self.assertTrue(rows)
            self.assertTrue(all({'id','building_id','kind','question','inputs','answer'}<=r.keys() for r in rows))
            before=copy.deepcopy(rows[0]);rows[0]['inputs']['injected']=True
            self.assertEqual(self.service.examples(split)[0],before)
        for split in ('test','../test',''):
            with self.assertRaises(ModelError):self.service.examples(split)

    def test_actual_dev_example_matches_without_data_mutation(self):
        before=hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
        example=next(q for q in self.service.examples() if q['kind']=='largest_room')
        result=self.service.example(example['building_id'],{'split':'dev','id':example['id']})
        self.assertTrue(result['comparison']['matches_reference'])
        self.assertEqual(result['example']['expected'],example['answer'])
        self.assertIn('no language benchmark',result['comparison']['scope'])
        self.assertEqual(before,hashlib.sha256(ARCHIVE.read_bytes()).hexdigest())

    def test_route_dependent_examples_match_kit_1_1_0_answers(self):
        # errand / what_if_door answers changed in kit 1.1.0; the lab compares with the 1.1.0 files.
        for kind in ('errand','what_if_door'):
            example=next(q for q in self.service.examples() if q['kind']==kind)
            result=self.service.example(example['building_id'],{'split':'dev','id':example['id']})
            self.assertTrue(result['comparison']['matches_reference'],(kind,example['id'],result['formatted_answer']))
            self.assertEqual(result['cost_model'],'official_kit_1_1_0')

    def test_answer_is_not_calculation_input(self):
        example=next(q for q in self.service.examples() if q['kind']=='largest_room')
        fake={'formatted_answer':'Answer: impossible','operation':example['kind'],'value':-1}
        with patch.object(self.service,'operation',return_value=fake) as calculation:
            result=self.service.example(example['building_id'],{'id':example['id']})
        self.assertEqual(calculation.call_args.args,(example['building_id'],{'operation':example['kind'],'arguments':example['inputs']}))
        self.assertFalse(result['comparison']['matches_reference'])

    def test_bad_id_building_and_split_are_rejected(self):
        example=self.service.examples()[0]
        for building,args in [('missing',{'id':example['id']}),
                              (example['building_id'],{'id':'missing'}),
                              (example['building_id'],{'id':example['id'],'split':'test'}),
                              (example['building_id'],{'id':3})]:
            with self.assertRaises(ModelError):self.service.example(building,args)

    def test_diagnosis_includes_live_ram_closures(self):
        building='proc-factory-dev-003'
        closed=[building+'-F0-O0023',building+'-F0-O0024']
        for element in closed:self.service.change(building,{'action':'close','element':element})
        with self.assertRaises(ModelError) as caught:
            self.service.navigate(building,{'start':{'kind':'space','id':building+'-F0-S019'},
                                           'target':{'kind':'asset','id':building+'-A00001'}})
        diagnosis=caught.exception.details['diagnosis']
        self.assertEqual(diagnosis['category'],'closed')
        marked={item['id'] for item in diagnosis['elements']}
        self.assertTrue(marked)
        self.assertTrue(marked<=set(closed))

    def test_example_split_validation_and_mutating_consumer_are_isolated(self):
        ex=self.service.examples()[0];before=copy.deepcopy(ex)
        def mutate(building,request):
            request['arguments']['injected']=True
            return {'formatted_answer':'Answer: impossible','operation':ex['kind'],'value':-1}
        with patch.object(self.service,'operation',side_effect=mutate):
            self.service.example(ex['building_id'],{'id':ex['id']})
        self.assertEqual(self.service.examples()[0],before)
        for split in ([],{},None):
            with self.assertRaises(ModelError):self.service.examples(split)
            with self.assertRaises(ModelError):self.service.example(ex['building_id'],{'id':ex['id'],'split':split})

    def test_diagnostic_failure_preserves_original_error_and_reference_is_separate(self):
        b='proc-factory-dev-003';args={'start':{'kind':'space','id':b+'-F0-S019'},'target':{'kind':'space','id':b+'-F0-S001'},'closed':[b+'-F0-S019']}
        with patch('diagnostics.annotate_error',side_effect=AttributeError('injected diagnostic failure')):
            with self.assertRaises(ModelError) as caught:self.service.navigate(b,args)
        self.assertEqual(caught.exception.code,'blocked_by_closure')
        self.assertTrue(caught.exception.details['diagnosis']['diagnostic_failure'])
        with patch.object(self.service,'reference_route',return_value={'model':'reference'}) as reference,patch('diagnostics.annotate_error') as diagnostic:
            self.assertEqual(self.service.navigate(b,{'model':'reference'})['model'],'reference')
            reference.assert_called_once();diagnostic.assert_not_called()

    def test_cached_source_evidence_never_receives_request_diagnosis(self):
        shared={'verified':False,'reason':'controlled unverified convention'}
        building='proc-factory-dev-003'
        captured=[]
        with patch('validation.validate_footprints',return_value=shared):
            for safety in (.05,.6):
                with self.assertRaises(ModelError) as caught:
                    self.service.navigate(building,{'safety_m':safety})
                captured.append(caught.exception.details['diagnosis']['clearance_m'])
        self.assertNotIn('diagnosis',shared)
        self.assertEqual(captured,[.35,.8999999999999999])


if __name__=='__main__':unittest.main()
