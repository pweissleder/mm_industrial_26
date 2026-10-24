# Dialog simulation `live_medium_v2`

- Mode: **live** · model `claude-sonnet-5-5` · effort `medium` · spoken: False
- Scenarios passed: **12/13** (run 13, partial 0, skipped 0)
- Turns passed: **20/21** run (0 errored, 0 skipped)
- Total estimated cost: **$0.2302** (budget $1.00)
- Median Claude latency: **6105.3 ms** (max 9754.3 ms; tools median 12.1 ms; wall median 6122.0 ms)
- Replies with 1-2 sentences: 14
- Started: 2026-10-24T21:13:01+00:00 · buildings: `proc-campus-dev-009`, `proc-factory-dev-003`

## Scenarios

| Scenario | Building | Result | Turns passed | Failed checks |
|---|---|---|---|---|
| coffee_nearest | proc-factory-dev-003 | PASS | 2/2 |  |
| toilet_unknown_start | proc-factory-dev-003 | PASS | 2/2 |  |
| break_relax | proc-campus-dev-009 | PASS | 1/1 |  |
| cnc_ambiguous | proc-factory-dev-003 | PASS | 2/2 |  |
| stt_errors | proc-factory-dev-003 | PASS | 1/1 |  |
| unknown_room | proc-factory-dev-003 | PASS | 1/1 |  |
| facility_unavailable | proc-factory-dev-003 | PASS | 1/1 |  |
| closure_flow | proc-factory-dev-003 | PASS | 5/5 |  |
| wheelchair_floors | proc-factory-dev-003 | FAIL | 1/2 | short |
| trolley_precision | proc-factory-dev-003 | PASS | 1/1 |  |
| long_campus | proc-campus-dev-009 | PASS | 1/1 |  |
| benchmark_fact | proc-factory-dev-003 | PASS | 1/1 |  |
| missing_destination | proc-factory-dev-003 | PASS | 1/1 |  |

## Turns

| Scenario | # | User | Result | Failed checks | Tools | Reply | USD | Claude ms |
|---|---|---|---|---|---|---|---|---|
| coffee_nearest | 1 | I'm in Office 0.38. Where can I get a coffee? | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The closest coffee machine is in Kitchen 0.28, about 21 m from Office 0.38 on the same floor. Kitchen 0.31 is the next … | 0.0239 | 6716.3 |
| coffee_nearest | 2 | And how do I get there? | PASS |  | plan_route(ok) | Head out of Office 0.38 into Office corridor 0.16, then follow it to Kitchen 0.28. It's about 21 m in total, all on the… | 0.0077 | 3989.9 |
| toilet_unknown_start | 1 | Where is the nearest toilet? | PASS |  |  | Where are you right now? Tell me a room or pick your spot on the map, and I'll find the nearest toilet from there. | 0.0020 | 1184.5 |
| toilet_unknown_start | 2 | I'm in the quality lab. | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest toilet is Toilet 0.22, about 10 m away on the same floor. You go through the office corridor to reach it. | 0.0131 | 9754.3 |
| break_relax | 1 | I'm in Lab 0.02 and need a break. Where can I relax and get… | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest kitchen is Kitchen 3.23, about 53 m away. It's three floors up by stairs. Kitchen 0.15 is on your own floor… | 0.0156 | 9056.7 |
| cnc_ambiguous | 1 | Where is the CNC mill? | PASS |  | find_places(ok) | There are three CNC mills (H-A1-A, H-A1-B and H-A1-C). All are in Production zone H-A1 on the ground floor. Tell me whe… | 0.0053 | 2872.1 |
| cnc_ambiguous | 2 | The B one. I am at hall access 0.19. | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | CNC mill H-A1-B is about 12 m from Hall access 0.19. It's on the same floor, just through the door into Production zone… | 0.0165 | 8971.7 |
| stt_errors | 1 | How do I get from the hall axis zero point one nine to the … | PASS |  | find_places(ok), find_places(ok), plan_route(ok) | From Hall access 0.19 to Quality lab 0.21 is about 15 m on the ground floor. You go through Office corridor 0.16, and t… | 0.0112 | 5754.3 |
| unknown_room | 1 | Take me to the cafeteria on floor 7. | PASS |  | find_places(ok) | This building only has the ground floor and Floor 1, so there's no floor 7. Do you want me to look for a canteen or kit… | 0.0044 | 3057.4 |
| facility_unavailable | 1 | Is there a gym or a shower here? | PASS |  | find_places(ok), find_places(ok) | I couldn't find a gym or a shower in this building. Would you like me to look for a toilet or changing room instead? | 0.0063 | 4114.4 |
| closure_flow | 1 | I'm in Meeting room 0.20. What would happen if the office c… | PASS |  | find_places(ok), find_places(ok), update_session(ok), preview_closure(ok) | Closing Office corridor 0.16 would cut you off almost completely. From Meeting room 0.20, 77 of the 78 rooms you can re… | 0.0146 | 7353.4 |
| closure_flow | 2 | Okay, close the corridor for cleaning. | PASS · confirm=applied |  | find_places(ok), propose_change(ok) | Please press Confirm to close Office corridor 0.16 for cleaning. Until then nothing changes, and once it's closed you'l… | 0.0080 | 6105.3 |
| closure_flow | 3 | How do I get to the quality lab now? | PASS |  | find_places(ok), plan_route(ok) | I can't find a route to Quality lab 0.21 right now, because the closed Office corridor 0.16 is blocking the way. Would … | 0.0091 | 6125.3 |
| closure_flow | 4 | Cleaning is done, please open the corridor again. | PASS · confirm=applied |  | find_places(ok), propose_change(ok) | Please press Confirm to reopen Office corridor 0.16. Once it's open, I can route you to the quality lab. | 0.0083 | 5556.1 |
| closure_flow | 5 | And now? | PASS |  | plan_route(ok) | The route to Quality lab 0.21 is open again. It's about 10 m on the ground floor, through Office corridor 0.16. | 0.0071 | 4055.2 |
| wheelchair_floors | 1 | I use a wheelchair. How do I get from hall access 0.19 to O… | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | There's a step-free route of about 19 m. It goes through Office corridor 0.16, up the lift to Floor 1, then on to Offic… | 0.0169 | 9135.8 |
| wheelchair_floors | 2 | And then to the kitchen 1.07? | PASS |  | find_places(ok), plan_route(ok) | From Office 1.04 it's about 19 m to Kitchen 1.07, all on Floor 1 and step-free. The route goes via Office corridor 1.01… | 0.0099 | 7182.7 |
| trolley_precision | 1 | I'm pushing a 0.9 metre wide trolley from logistics zone H-… | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | I found an obstacle-checked route for your 0.9 m trolley, with a 5 cm safety margin. It's about 38 m, all on the ground… | 0.0201 | 9059.7 |
| long_campus | 1 | From the lecture hall 0.31 to Lab 2.08, please. | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | Lab 2.08 is about 46 m from Lecture hall 0.31. The route takes Staircase 2 up two floors to Floor 2. | 0.0162 | 7428.1 |
| benchmark_fact | 1 | Which is the largest kitchen on floor 1, and how big is it? | PASS |  | building_fact(ok) | The largest kitchen on floor 1 is Kitchen 1.14, at about 28 m². | 0.0044 | 3637.8 |
| missing_destination | 1 | I am in Office 0.23. Can you show me the way? | PASS |  | find_places(ok), update_session(ok) | You're in Office 0.23. Where would you like to go? I can also take you to the nearest exit, toilet or coffee machine. | 0.0095 | 5249.6 |

Checks are deterministic (scripts/dialog_sim.py); the full per-turn record (input, ui, reply, tools, state, route summary, usage, timing, verdicts) is in the JSON report.
