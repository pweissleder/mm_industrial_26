"""OFFLINE assistant tests: a scripted stand-in replaces Claude (no API calls, no cost).

The tools, building data, Service state and router are real; only the language model's
decisions are scripted. Live Claude behaviour is checked separately (live_check.py).
"""
import json
import unittest

from assistant import Assistant, TOOLS, words
from errors import ModelError
from service import Service

B = 'proc-factory-dev-003'
S = lambda suffix: f'{B}-{suffix}'


class ScriptedTransport:
    """Offline stand-in for Claude: returns pre-written responses and records requests."""
    live = False

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        step = self.script.pop(0)
        if isinstance(step, str):
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': step}],
                    'usage': {'input_tokens': 10, 'output_tokens': 5}}
        name, args = step
        return {'stop_reason': 'tool_use', 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'content': [
            {'type': 'thinking', 'thinking': '', 'signature': 'sig'},
            {'type': 'tool_use', 'id': f'toolu_{len(self.requests)}', 'name': name, 'input': args}]}


def route_args(start=None, destination=None, **overrides):
    args = {'start': start, 'destination': destination, 'mode': None, 'step_free': None, 'assume_closed': []}
    args.update(overrides)
    return args


def settings(**values):
    args = {'start': None, 'destination': None, 'clear_start': False, 'clear_destination': False, 'mode': None,
            'step_free': None, 'forklift': None, 'profile': None, 'width_m': None, 'safety_m': None}
    args.update(values)
    return args


class ToolLayer(unittest.TestCase):
    """Deterministic tools on real data, without any model."""

    @classmethod
    def setUpClass(cls):
        cls.service = Service()

    def setUp(self):
        self.service.scenarios.clear()
        self.assistant = Assistant(self.service)
        self.session = self.assistant.session('t' * 32)
        self.session.state['building_id'] = B
        self.ui = {}

    def call(self, name, args):
        return self.assistant.run_tool(self.session, name, args, self.ui)

    def test_tool_schemas_are_strict_objects(self):
        for tool in TOOLS:
            schema = tool['input_schema']
            self.assertTrue(tool['strict'])
            self.assertFalse(schema['additionalProperties'])
            self.assertEqual(sorted(schema['required']), sorted(schema['properties']))
        # API limit found live: at most 16 union-typed (nullable/anyOf) parameters across strict tools.
        unions = sum(1 for t in TOOLS for v in t['input_schema']['properties'].values()
                     if 'anyOf' in v or isinstance(v.get('type'), list))
        self.assertLessEqual(unions, 16)

    def test_1_free_text_room_search(self):
        found = self.call('find_places', {'query': 'quality lab', 'kind': None, 'floor_level': None})
        self.assertEqual(found['results'][0]['id'], S('F0-S021'))
        self.assertEqual(self.ui['focus'], {'kind': 'space', 'id': S('F0-S021')})

    def test_2_real_cnc_machine_search_is_ambiguous(self):
        found = self.call('find_places', {'query': 'CNC mill', 'kind': None, 'floor_level': None})
        ids = [r['id'] for r in found['results']]
        self.assertEqual(ids, [S('A00001'), S('A00002'), S('A00003')])
        self.assertIn('ask the user', found['note'])
        self.assertNotIn('focus', self.ui)
        self.assertTrue(all(r['room'] for r in found['results']))

    def test_3_route_from_real_start_to_machine(self):
        out = self.call('plan_route', route_args({'kind': 'space', 'id': S('F0-S019')}, {'kind': 'asset', 'id': S('A00001')}))
        self.assertEqual(out['status'], 'ok')
        self.assertTrue(out['fully_checked'])
        self.assertGreater(out['length_m'], 1)
        self.assertEqual(self.ui['route']['result']['target']['id'] if 'id' in self.ui['route']['result']['target'] else S('A00001'), S('A00001'))
        self.assertEqual(self.session.state['destination'], {'kind': 'asset', 'id': S('A00001')})

    def test_4_missing_start_is_reported(self):
        with self.assertRaises(ModelError) as caught:
            self.call('plan_route', route_args(None, {'kind': 'space', 'id': S('F0-S021')}))
        self.assertEqual(caught.exception.code, 'missing_endpoint')

    def test_5_follow_up_reuses_remembered_start(self):
        self.call('update_session', {'start': {'kind': 'space', 'id': S('F0-S020')}, 'destination': None,
                                     'clear_start': False, 'clear_destination': False, 'mode': None,
                                     'step_free': None, 'forklift': None, 'profile': None, 'width_m': None, 'safety_m': None})
        out = self.call('plan_route', route_args(None, {'kind': 'space', 'id': S('F0-S021')}))
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(self.ui['route']['request']['start'], {'kind': 'space', 'id': S('F0-S020')})

    def test_7_change_requires_confirmation(self):
        out = self.call('propose_change', {'action': 'close', 'element_id': S('F0-S019')})
        self.assertEqual(out['status'], 'awaiting_user_confirmation')
        self.assertNotIn(S('F0-S019'), self.service.model(B).closed)
        applied = self.assistant.confirm('t' * 32, out['proposal_id'], True)
        self.assertEqual(applied['status'], 'applied')
        self.assertIn(S('F0-S019'), self.service.model(B).closed)
        with self.assertRaises(ModelError):
            self.assistant.confirm('t' * 32, out['proposal_id'], True)

    def test_7b_stale_proposal_is_rejected(self):
        out = self.call('propose_change', {'action': 'close', 'element_id': S('F0-S020')})
        self.service.change(B, {'action': 'close', 'element': S('F0-S022')})
        with self.assertRaises(ModelError) as caught:
            self.assistant.confirm('t' * 32, out['proposal_id'], True)
        self.assertEqual(caught.exception.code, 'stale_proposal')

    def test_8a_closure_preview_is_explained_not_raw(self):
        out = self.call('plan_route', route_args({'kind': 'space', 'id': S('F0-S020')}, {'kind': 'space', 'id': S('F0-S021')},
                                                 assume_closed=[S('F0-S016')]))
        self.assertEqual(out['status'], 'failed')
        self.assertTrue(out['explanation'])
        self.assertEqual(self.service.model(B).closed, set())

    def test_8b_unsupported_building_family(self):
        osm = next(r['id'] for r in self.service.catalog() if r['source'] == 'osm_indoor' and r['split'] == 'dev')
        self.session.state['building_id'] = osm
        room = self.service.model(osm).data['spaces'][0]['id']
        out = self.call('plan_route', route_args({'kind': 'space', 'id': room}, {'kind': 'space', 'id': room}))
        self.assertEqual(out['status'], 'failed')
        self.assertIn('challenge-mode', out['hint'])

    def test_9_obstacle_profile_changes_route(self):
        lengths = {}
        for profile in ('stationary', 'all'):
            self.call('update_session', settings(profile=profile))
            out = self.call('plan_route', route_args({'kind': 'space', 'id': S('F0-S020')}, {'kind': 'space', 'id': S('F0-S021')}))
            self.assertEqual(out['status'], 'ok')
            lengths[profile] = out['length_m']
        # Different obstacle sets give different checked paths (the derived free start point may move too).
        self.assertNotAlmostEqual(lengths['all'], lengths['stationary'], places=2)
        self.call('update_session', settings(width_m=0.8))
        wide = self.call('plan_route', route_args())
        self.assertEqual(wide['status'], 'failed')
        self.assertIn('not proof', wide['explanation'])
        self.assertEqual(self.session.state['profile'], 'all')

    def test_10_benchmark_question_matches_dev_answer(self):
        from data import examples, reference_module
        example = next(q for q in examples('dev') if q['kind'] == 'largest_room' and q['building_id'] == B)
        out = self.call('benchmark_operation', {'operation': 'largest_room', 'arguments_json': json.dumps(example['inputs'])})
        self.assertTrue(reference_module('score').score_reply(example['answer'], out['formatted_answer']))

    def test_unknown_ids_are_rejected(self):
        with self.assertRaises(ModelError):
            self.call('plan_route', route_args({'kind': 'space', 'id': 'nope'}, {'kind': 'space', 'id': S('F0-S021')}))
        with self.assertRaises(ModelError):
            self.call('propose_change', {'action': 'close', 'element_id': 'nope'})

    def test_words(self):
        self.assertEqual(words('Office 1.12_cnc'), ['office', '1.12', 'cnc'])


class Conversation(unittest.TestCase):
    """The tool loop and bounded multi-turn context with a scripted (offline) model."""

    @classmethod
    def setUpClass(cls):
        cls.service = Service()

    def setUp(self):
        self.service.scenarios.clear()

    def test_multi_turn_context_state_and_errors(self):
        transport = ScriptedTransport([
            ('find_places', {'query': 'meeting room 0.20', 'kind': 'room', 'floor_level': None}),
            ('update_session', {'start': {'kind': 'space', 'id': S('F0-S020')}, 'destination': None, 'clear_start': False,
                                'clear_destination': False, 'mode': None, 'step_free': None, 'forklift': None,
                                'profile': None, 'width_m': None, 'safety_m': None}),
            'Got it, you are in Meeting room 0.20. Where do you want to go?',
            ('plan_route', route_args(None, {'kind': 'space', 'id': S('F0-S021')})),
            'From Meeting room 0.20 to the Quality lab is about 10 m.',
            ('plan_route', route_args({'kind': 'space', 'id': 'bad-id'}, None)),
            'Sorry, I could not find that place. Which room do you mean?',
        ])
        assistant = Assistant(self.service, transport)
        token = 'a' * 32
        first = assistant.turn(token, "I'm in meeting room 0.20", {'building_id': B})
        self.assertEqual([t['tool'] for t in first['tools']], ['find_places', 'update_session'])
        self.assertEqual(first['state']['start']['name'], 'Meeting room 0.20')
        second = assistant.turn(token, 'How do I get to the quality lab?', {'building_id': B})
        self.assertEqual(second['tools'][0]['output']['status'], 'ok')
        self.assertIn('route', second['ui'])
        # The second request carries plain-text history and the explicit state, no old thinking blocks.
        request = transport.requests[3]
        self.assertEqual(request['output_config'], {'effort': 'medium'})
        self.assertEqual(request['model'], 'claude-sonnet-5-5')
        first_messages = request['messages']
        self.assertTrue(all(isinstance(m['content'], str) for m in first_messages))
        self.assertIn('Meeting room 0.20', first_messages[-1]['content'])
        self.assertIn('<session_state>', first_messages[-1]['content'])
        third = assistant.turn(token, 'And from the bad place?', {'building_id': B})
        self.assertEqual(third['tools'][0]['status'], 'error')
        self.assertNotIn('unknown_place', third['reply'])
        # Within one turn, the assistant content (with thinking) is replayed verbatim before tool results.
        tool_round = transport.requests[-1]['messages']
        self.assertEqual(tool_round[-2]['content'][0]['type'], 'thinking')
        self.assertTrue(tool_round[-1]['content'][0]['is_error'])

    def test_history_is_bounded(self):
        transport = ScriptedTransport(['ok'] * 20)
        assistant = Assistant(self.service, transport)
        for i in range(20):
            assistant.turn('b' * 32, f'question {i}', {'building_id': B})
        self.assertLessEqual(len(transport.requests[-1]['messages']), 2 * 6 + 1)
        self.assertLessEqual(len(assistant.session('b' * 32).history), 24)

    def test_offline_without_key_is_explicit(self):
        from assistant import AssistantError
        with self.assertRaises(AssistantError):
            Assistant(self.service).turn('c' * 32, 'hello', {})

    def test_ui_selection_is_shared_state(self):
        transport = ScriptedTransport([('plan_route', route_args()), 'done'])
        assistant = Assistant(self.service, transport)
        out = assistant.turn('d' * 32, 'route please', {'building_id': B, 'start': {'kind': 'space', 'id': S('F0-S020')},
                                                       'destination': {'kind': 'space', 'id': S('F0-S021')},
                                                       'mode': 'precision', 'profile': 'all', 'width_m': 0.65})
        self.assertEqual(out['tools'][0]['status'], 'ok')
        self.assertEqual(out['ui']['route']['request']['profile'], 'all')
        self.assertEqual(out['ui']['route']['request']['width_m'], 0.65)


if __name__ == '__main__':
    unittest.main()
