# Dialog simulation `scripted`

- Mode: **scripted** · model `claude-sonnet-5-5` · effort `medium` · spoken: False
- Scenarios passed: **0/13** (run 13, partial 0, skipped 0)
- Turns passed: **0/21** run (0 errored, 0 skipped)
- Total estimated cost: **$0.0000** (budget $1.00)
- Median Claude latency: **0.0 ms** (max 0.0 ms; tools median 0.0 ms; wall median 10.8 ms)
- Replies with 1-2 sentences: 21
- Started: 2026-10-24T21:07:36+00:00 · buildings: `proc-campus-dev-009`, `proc-factory-dev-003`

## Scenarios

| Scenario | Building | Result | Turns passed | Failed checks |
|---|---|---|---|---|
| coffee_nearest | proc-factory-dev-003 | FAIL | 0/2 | mentions_nearest, route_from, route_to_nearest, state_start, tools, tools_any |
| toilet_unknown_start | proc-factory-dev-003 | FAIL | 0/2 | asks, mentions_any, route_to_nearest, state_start, tools |
| break_relax | proc-campus-dev-009 | FAIL | 0/1 | mentions_any, state_start |
| cnc_ambiguous | proc-factory-dev-003 | FAIL | 0/2 | asks, mentions_any, route_from, route_to |
| stt_errors | proc-factory-dev-003 | FAIL | 0/1 | route_from, route_to |
| unknown_room | proc-factory-dev-003 | FAIL | 0/1 | honest_missing |
| facility_unavailable | proc-factory-dev-003 | FAIL | 0/1 | honest_missing |
| closure_flow | proc-factory-dev-003 | FAIL | 0/5 | closed_now, previewed_closed, proposal, route_found, route_to, route_unreachable, tools_any |
| wheelchair_floors | proc-factory-dev-003 | FAIL | 0/2 | mentions_any, route_from, route_to, state_step_free, uses |
| trolley_precision | proc-factory-dev-003 | FAIL | 0/1 | state_mode, state_width, tools |
| long_campus | proc-campus-dev-009 | FAIL | 0/1 | route_found, route_from, route_to |
| benchmark_fact | proc-factory-dev-003 | FAIL | 0/1 | mentions_any, tools |
| missing_destination | proc-factory-dev-003 | FAIL | 0/1 | asks, state_start |

## Turns

| Scenario | # | User | Result | Failed checks | Tools | Reply | USD | Claude ms |
|---|---|---|---|---|---|---|---|---|
| coffee_nearest | 1 | I'm in Office 0.38. Where can I get a coffee? | FAIL | state_start, tools_any, mentions_nearest |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| coffee_nearest | 2 | And how do I get there? | FAIL | tools, route_from, route_to_nearest |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| toilet_unknown_start | 1 | Where is the nearest toilet? | FAIL | mentions_any, asks |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| toilet_unknown_start | 2 | I'm in the quality lab. | FAIL | state_start, tools, route_to_nearest |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| break_relax | 1 | I'm in Lab 0.02 and need a break. Where can I relax and get… | FAIL | state_start, mentions_any |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| cnc_ambiguous | 1 | Where is the CNC mill? | FAIL | asks, mentions_any |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| cnc_ambiguous | 2 | The B one. I am at hall access 0.19. | FAIL | route_from, route_to |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| stt_errors | 1 | How do I get from the hall axis zero point one nine to the … | FAIL | route_from, route_to |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| unknown_room | 1 | Take me to the cafeteria on floor 7. | FAIL | honest_missing |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| facility_unavailable | 1 | Is there a gym or a shower here? | FAIL | honest_missing |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| closure_flow | 1 | I'm in Meeting room 0.20. What would happen if the office c… | FAIL | tools_any, previewed_closed |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| closure_flow | 2 | Okay, close the corridor for cleaning. | FAIL · confirm=no_proposal | proposal |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| closure_flow | 3 | How do I get to the quality lab now? | FAIL | closed_now, route_unreachable |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| closure_flow | 4 | Cleaning is done, please open the corridor again. | FAIL · confirm=no_proposal | proposal |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| closure_flow | 5 | And now? | FAIL | route_found, route_to |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| wheelchair_floors | 1 | I use a wheelchair. How do I get from hall access 0.19 to O… | FAIL | state_step_free, route_to, uses, mentions_any |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| wheelchair_floors | 2 | And then to the kitchen 1.07? | FAIL | route_from, route_to, state_step_free |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| trolley_precision | 1 | I'm pushing a 0.9 metre wide trolley from logistics zone H-… | FAIL | state_mode, state_width, tools |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| long_campus | 1 | From the lecture hall 0.31 to Lab 2.08, please. | FAIL | route_from, route_to, route_found |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| benchmark_fact | 1 | Which is the largest kitchen on floor 1, and how big is it? | FAIL | tools, mentions_any |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |
| missing_destination | 1 | I am in Office 0.23. Can you show me the way? | FAIL | state_start, asks |  | Scripted reply: this offline run only exercises the dialog harness. | 0.0000 | 0.0 |

Checks are deterministic (scripts/dialog_sim.py); the full per-turn record (input, ui, reply, tools, state, route summary, usage, timing, verdicts) is in the JSON report.
