"""Customer-dialog matrix. The SAME scenarios run offline (scripted reference plan, real tools)
and live (real Claude); checks are deterministic and never call a model.

Turn fields:
  user    – what the customer says (may contain deliberate speech-recognition errors)
  plan    – OFFLINE ONLY: the tool calls a correct assistant would make, then its reply text
  expect  – checks on the actual result (see run_sim.check)
  confirm – harness presses Confirm (True) / Cancel (False) on the newest proposal after the turn
"""
F = 'proc-factory-dev-003'
C = 'proc-campus-dev-009'
f = lambda s: f'{F}-{s}'
c = lambda s: f'{C}-{s}'


def find(query, kind=None, floor=None, near=False):
    return ('find_places', {'query': query, 'kind': kind, 'floor_level': floor, 'near_start': near})


def session(**values):
    args = {'start': None, 'destination': None, 'clear_start': False, 'clear_destination': False, 'mode': None,
            'step_free': None, 'forklift': None, 'profile': None, 'width_m': None, 'safety_m': None}
    args.update(values)
    return ('update_session', args)


def route(start=None, destination=None, mode=None, step_free=None, assume_closed=()):
    return ('plan_route', {'start': start, 'destination': destination, 'mode': mode, 'step_free': step_free,
                           'assume_closed': list(assume_closed)})


space = lambda i: {'kind': 'space', 'id': i}
asset = lambda i: {'kind': 'asset', 'id': i}

SCENARIOS = [
    {'id': 'coffee_nearest', 'building': F, 'title': 'Coffee near me, then "how do I get there?"', 'turns': [
        {'user': "I'm in Office 0.38. Where can I get a coffee?",
         'plan': [find('office 0.38', 'room'), session(start=space(f('F0-S038'))), find('coffee', None, None, True),
                  'The closest coffee machine is in Kitchen 0.28, a short walk from your office.'],
         'expect': {'state_start': f('F0-S038'), 'tools': ['find_places'], 'mentions_nearest': 'coffee', 'short': True}},
        {'user': 'And how do I get there?',
         'plan': [route(None, space(f('F0-S028'))), 'Head out into the office corridor; Kitchen 0.28 is about 15 m away.'],
         'expect': {'tools': ['plan_route'], 'route_from': f('F0-S038'), 'route_to_nearest': 'coffee', 'short': True}},
    ]},
    {'id': 'toilet_unknown_start', 'building': F, 'title': 'Nearest toilet without a known position', 'turns': [
        {'user': 'Where is the nearest toilet?',
         'plan': [find('toilet', 'room', None, True), 'There is a toilet on the ground floor, Toilet 0.22. Where are you right now, so I can show the way?'],
         'expect': {'no_route': True, 'mentions_any': ['toilet'], 'asks': True}},
        {'user': "I'm in the quality lab.",
         'plan': [find('quality lab', 'room'), session(start=space(f('F0-S021'))), find('toilet', 'room', None, True),
                  route(None, space(f('F0-S022'))), 'Toilet 0.22 is the closest, about 10 m from the quality lab.'],
         'expect': {'state_start': f('F0-S021'), 'tools': ['plan_route'], 'route_to_nearest': 'toilet', 'short': True}},
    ]},
    {'id': 'break_relax', 'building': C, 'title': 'Vague intent: somewhere to relax', 'turns': [
        {'user': "I'm in Lab 0.02 and need a break. Where can I relax and get something to eat?",
         'plan': [find('lab 0.02', 'room'), session(start=space(c('F0-S002'))), find('canteen', 'room', None, True),
                  'The canteen on the ground floor is close to you, Canteen 0.32.'],
         'expect': {'state_start': c('F0-S002'), 'mentions_any': ['canteen', 'kitchen'], 'short': True}},
    ]},
    {'id': 'cnc_ambiguous', 'building': F, 'title': 'Ambiguous machine name', 'turns': [
        {'user': 'Where is the CNC mill?',
         'plan': [find('cnc mill', 'equipment'), 'There are three CNC mills, all in Production zone H-A1: A, B and C. Which one do you need?'],
         'expect': {'asks': True, 'mentions_any': ['three', '3'], 'no_route': True}},
        {'user': 'The B one. I am at hall access 0.19.',
         'plan': [find('hall access 0.19', 'room'), session(start=space(f('F0-S019')), destination=asset(f('A00002'))),
                  route(None, asset(f('A00002'))), 'CNC mill H-A1-B is just through the door into Production zone H-A1.'],
         'expect': {'route_from': f('F0-S019'), 'route_to': f('A00002'), 'short': True}},
    ]},
    {'id': 'stt_errors', 'building': F, 'title': 'Speech-recognition errors in room names', 'turns': [
        {'user': 'How do I get from the hall axis zero point one nine to the quality lap?',
         'plan': [find('hall access 0.19', 'room'), find('quality lab', 'room'),
                  route(space(f('F0-S019')), space(f('F0-S021'))), 'From Hall access 0.19 the quality lab is about 18 m, through the office corridor.'],
         'expect': {'route_from': f('F0-S019'), 'route_to': f('F0-S021'), 'short': True}},
    ]},
    {'id': 'unknown_room', 'building': F, 'title': 'Unknown room and floor', 'turns': [
        {'user': 'Take me to the cafeteria on floor 7.',
         'plan': [find('cafeteria', 'room'), 'This building has no cafeteria and no floor 7; there are kitchens on the ground floor and floor 1. Shall I take you to one?'],
         'expect': {'no_route': True, 'honest_missing': True, 'short': True}},
    ]},
    {'id': 'facility_unavailable', 'building': F, 'title': 'Facility that does not exist', 'turns': [
        {'user': 'Is there a gym or a shower here?',
         'plan': [find('gym', None), find('shower', None), "I can't find a gym or a shower in this building."],
         'expect': {'no_route': True, 'honest_missing': True, 'short': True}},
    ]},
    {'id': 'closure_flow', 'building': F, 'title': 'What-if, closure with confirmation, then undo', 'turns': [
        {'user': "I'm in Meeting room 0.20. What would happen if the office corridor 0.16 were closed?",
         'plan': [find('meeting room 0.20', 'room'), session(start=space(f('F0-S020'))), find('office corridor 0.16', 'room'),
                  find('quality lab', 'room'), route(None, space(f('F0-S021')), assume_closed=[f('F0-S016')]),
                  'Then you could not reach the quality lab from here; the corridor is the only way. Nothing has been changed.'],
         'expect': {'tools_any': ['preview_closure', 'plan_route'], 'previewed_closed': f('F0-S016'), 'unchanged': True, 'short': True}},
        {'user': 'Okay, close the corridor for cleaning.',
         'plan': [('propose_change', {'action': 'close', 'element_id': f('F0-S016')}),
                  'I have prepared closing Office corridor 0.16; please press Confirm.'],
         'expect': {'proposal': ('close', f('F0-S016')), 'unchanged': True, 'short': True}, 'confirm': True},
        {'user': 'How do I get to the quality lab now?',
         'plan': [route(None, space(f('F0-S021'))), 'Right now there is no way to the quality lab, because the office corridor is closed.'],
         'expect': {'closed_now': f('F0-S016'), 'route_unreachable': True, 'short': True}},
        {'user': 'Cleaning is done, please open the corridor again.',
         'plan': [('propose_change', {'action': 'open', 'element_id': f('F0-S016')}), 'Press Confirm to reopen Office corridor 0.16.'],
         'expect': {'proposal': ('open', f('F0-S016')), 'short': True}, 'confirm': True},
        {'user': 'And now?',
         'plan': [route(None, space(f('F0-S021'))), 'Now the quality lab is about 13 m away through the corridor again.'],
         'expect': {'route_found': True, 'route_to': f('F0-S021'), 'short': True, 'open_now': f('F0-S016')}},
    ]},
    {'id': 'wheelchair_floors', 'building': F, 'title': 'Wheelchair user, upper floor', 'turns': [
        {'user': 'I use a wheelchair. How do I get from hall access 0.19 to Office 1.04?',
         'plan': [find('hall access 0.19', 'room'), find('office 1.04', 'room'), session(step_free=True),
                  route(space(f('F0-S019')), space(f('F1-S004')), step_free=True), 'Take the lift up to floor 1; Office 1.04 is then close by.'],
         'expect': {'state_step_free': True, 'route_to': f('F1-S004'), 'uses': 'lift', 'mentions_any': ['lift', 'elevator'], 'short': True}},
        {'user': 'And then to the kitchen 1.07?',
         'plan': [find('kitchen 1.07', 'room'), route(space(f('F1-S004')), space(f('F1-S007'))), 'Kitchen 1.07 is on the same floor, a short walk from Office 1.04.'],
         'expect': {'route_from': f('F1-S004'), 'route_to': f('F1-S007'), 'state_step_free': True, 'short': True}},
    ]},
    {'id': 'trolley_precision', 'building': F, 'title': 'Wide trolley avoiding machines', 'turns': [
        {'user': "I'm pushing a 0.9 metre wide trolley from logistics zone H-C5 to warehouse zone H-A5. I need to stay clear of the machines.",
         'plan': [find('logistics zone h-c5', 'room'), find('warehouse zone h-a5', 'room'),
                  session(mode='precision', width_m=0.9), route(space(f('F0-S015')), space(f('F0-S005')), mode='precision'),
                  'With a 0.9 m trolley there is a checked path to warehouse zone H-A5.'],
         'expect': {'state_mode': 'precision', 'state_width': 0.9, 'tools': ['plan_route'], 'short': True}},
    ]},
    {'id': 'long_campus', 'building': C, 'title': 'Long multi-floor route in a campus building', 'turns': [
        {'user': 'From the lecture hall 0.31 to Lab 2.08, please.',
         'plan': [find('lecture hall 0.31', 'room'), find('lab 2.08', 'room'), route(space(c('F0-S031')), space(c('F2-S008'))),
                  'Lab 2.08 is on floor 2; take the stairs or lift up from near the lecture hall.'],
         'expect': {'route_from': c('F0-S031'), 'route_to': c('F2-S008'), 'route_found': True, 'short': True}},
    ]},
    {'id': 'benchmark_fact', 'building': F, 'title': 'Building fact question', 'turns': [
        {'user': 'Which is the largest kitchen on floor 1, and how big is it?',
         'plan': [('benchmark_operation', {'operation': 'largest_room', 'arguments_json': '{"floor_id": "%s", "room_type": "kitchen"}' % f('F1')}),
                  'Kitchen 1.14 is the largest on floor 1, at about 28 m².'],
         'expect': {'tools': ['benchmark_operation'], 'mentions_any': ['1.14'], 'short': True}},
    ]},
    {'id': 'missing_destination', 'building': F, 'title': 'Route request without destination', 'turns': [
        {'user': 'I am in Office 0.23. Can you show me the way?',
         'plan': [find('office 0.23', 'room'), session(start=space(f('F0-S023'))), 'Sure — where do you want to go?'],
         'expect': {'state_start': f('F0-S023'), 'no_route': True, 'asks': True, 'short': True}},
    ]},
]
