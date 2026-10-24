# Dialog simulation `live_medium`

- Mode: **live** · model `claude-sonnet-5-5` · effort `medium` · spoken: False
- Scenarios passed: **4/13** (run 13, partial 0, skipped 0)
- Turns passed: **8/21** run (0 errored, 0 skipped)
- Total estimated cost: **$0.2354** (budget $1.00)
- Median Claude latency: **6021.2 ms** (max 10745.4 ms; tools median 18.1 ms; wall median 6177.8 ms)
- Replies with 1-2 sentences: 2
- Started: 2026-10-24T21:09:24+00:00 · buildings: `proc-campus-dev-009`, `proc-factory-dev-003`

## Scenarios

| Scenario | Building | Result | Turns passed | Failed checks |
|---|---|---|---|---|
| coffee_nearest | proc-factory-dev-003 | FAIL | 0/2 | short |
| toilet_unknown_start | proc-factory-dev-003 | PASS | 2/2 |  |
| break_relax | proc-campus-dev-009 | FAIL | 0/1 | short |
| cnc_ambiguous | proc-factory-dev-003 | FAIL | 1/2 | short |
| stt_errors | proc-factory-dev-003 | FAIL | 0/1 | short |
| unknown_room | proc-factory-dev-003 | FAIL | 0/1 | short |
| facility_unavailable | proc-factory-dev-003 | PASS | 1/1 |  |
| closure_flow | proc-factory-dev-003 | FAIL | 2/5 | short |
| wheelchair_floors | proc-factory-dev-003 | FAIL | 0/2 | short |
| trolley_precision | proc-factory-dev-003 | FAIL | 0/1 | short |
| long_campus | proc-campus-dev-009 | FAIL | 0/1 | short |
| benchmark_fact | proc-factory-dev-003 | PASS | 1/1 |  |
| missing_destination | proc-factory-dev-003 | PASS | 1/1 |  |

## Turns

| Scenario | # | User | Result | Failed checks | Tools | Reply | USD | Claude ms |
|---|---|---|---|---|---|---|---|---|
| coffee_nearest | 1 | I'm in Office 0.38. Where can I get a coffee? | FAIL | short | find_places(ok), update_session(ok), find_nearest(ok) | The closest coffee machine is in Kitchen 0.28, on the same floor as you. It's about 21 m away. Go out into the office c… | 0.0237 | 7211.9 |
| coffee_nearest | 2 | And how do I get there? | FAIL | short | plan_route(ok) | From Office 0.38, go through the door into Office corridor 0.16. Follow it for about 16 m to the door into Kitchen 0.28… | 0.0065 | 3716.3 |
| toilet_unknown_start | 1 | Where is the nearest toilet? | PASS |  |  | I need a starting point to work out the nearest toilet, and I don't know where you are yet. Which room are you in? You … | 0.0020 | 2021.7 |
| toilet_unknown_start | 2 | I'm in the quality lab. | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest toilet is Toilet 0.22, about 10 m from the quality lab on the same floor. Go out into the office corridor, … | 0.0131 | 6428.5 |
| break_relax | 1 | I'm in Lab 0.02 and need a break. Where can I relax and get… | FAIL | short | find_places(ok), update_session(ok), find_nearest(ok) | Canteen 0.32 is on your floor and only about 12 m from Lab 0.02. You go out into the corridor and then straight into th… | 0.0131 | 6697.4 |
| cnc_ambiguous | 1 | Where is the CNC mill? | PASS |  | find_places(ok) | There are three CNC mills (H-A1-A, H-A1-B and H-A1-C). All of them are in Production zone H-A1 on the ground floor. Tel… | 0.0053 | 2747.0 |
| cnc_ambiguous | 2 | The B one. I am at hall access 0.19. | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | I've mapped the route from Hall access 0.19 to CNC mill H-A1-B. It's about 12 m. You go through the door into Productio… | 0.0168 | 8380.7 |
| stt_errors | 1 | How do I get from the hall axis zero point one nine to the … | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | I took "hall axis 0.19" to mean Hall access 0.19, and "quality lap" to mean Quality lab 0.21. Both are on the ground fl… | 0.0157 | 8123.6 |
| unknown_room | 1 | Take me to the cafeteria on floor 7. | FAIL | short | find_places(ok) | This building only has two floors: the ground floor and floor 1. It has no floor 7. Do you want me to look for a cafete… | 0.0045 | 3506.0 |
| facility_unavailable | 1 | Is there a gym or a shower here? | PASS |  | find_places(ok), find_places(ok) | I couldn't find a gym or a shower in Fertigung Ost 27. If you'd like, I can look for the nearest toilet, a kitchen or c… | 0.0065 | 6583.1 |
| closure_flow | 1 | I'm in Meeting room 0.20. What would happen if the office c… | FAIL | short | find_places(ok), update_session(ok), find_places(ok), preview_closure(ok) | If Office corridor 0.16 closed, you'd be cut off. I've taken Meeting room 0.20 as your starting point. Of the 78 rooms … | 0.0152 | 9033.0 |
| closure_flow | 2 | Okay, close the corridor for cleaning. | PASS · confirm=applied |  | find_places(ok), propose_change(ok) | I've proposed closing Office corridor 0.16 for cleaning. Nothing changes until you press Confirm. Once it's closed, Mee… | 0.0083 | 4839.7 |
| closure_flow | 3 | How do I get to the quality lab now? | FAIL | short | find_places(ok), plan_route(ok) | I can't find a route to Quality lab 0.21 right now. Office corridor 0.16 is closed, and Meeting room 0.20 only connects… | 0.0095 | 4292.0 |
| closure_flow | 4 | Cleaning is done, please open the corridor again. | PASS · confirm=applied |  | propose_change(ok) | I've proposed reopening Office corridor 0.16. Nothing changes until you press Confirm. After that I can plan your route… | 0.0060 | 2445.9 |
| closure_flow | 5 | And now? | FAIL | short | plan_route(ok) | The route to Quality lab 0.21 works again. It's about 10 m. You go from Meeting room 0.20 through Office corridor 0.16 … | 0.0076 | 3180.1 |
| wheelchair_floors | 1 | I use a wheelchair. How do I get from hall access 0.19 to O… | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | There's a step-free route of about 19 m, and it uses the lift. It goes from Hall access 0.19 through Office corridor 0.… | 0.0181 | 10745.4 |
| wheelchair_floors | 2 | And then to the kitchen 1.07? | FAIL | short | find_places(ok), plan_route(ok) | From Office 1.04, the kitchen is about 18 m away on the same floor, so you don't need the lift. You go out into Office … | 0.0115 | 5968.8 |
| trolley_precision | 1 | I'm pushing a 0.9 metre wide trolley from logistics zone H-… | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | I found a route for your 0.9 m trolley from Logistics zone H-C5 to Warehouse zone H-A5. It's about 38 m and is checked … | 0.0213 | 9999.7 |
| long_campus | 1 | From the lecture hall 0.31 to Lab 2.08, please. | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | I've mapped the route from Lecture hall 0.31 to Lab 2.08. It's about 46 m. You go out to the corridor and take Staircas… | 0.0166 | 8316.0 |
| benchmark_fact | 1 | Which is the largest kitchen on floor 1, and how big is it? | PASS |  | building_fact(ok) | The largest kitchen on floor 1 is Kitchen 1.14. It's about 28 m². | 0.0043 | 2614.1 |
| missing_destination | 1 | I am in Office 0.23. Can you show me the way? | PASS |  | find_places(ok), update_session(ok) | I've set Office 0.23 as your starting point. Where would you like to go? It can be a room, a toilet, a coffee machine o… | 0.0098 | 6021.2 |

Checks are deterministic (scripts/dialog_sim.py); the full per-turn record (input, ui, reply, tools, state, route summary, usage, timing, verdicts) is in the JSON report.
