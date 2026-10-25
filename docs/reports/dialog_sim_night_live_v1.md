# Dialog simulation `night_live_v1`

- Mode: **live** · model `claude-sonnet-5-5` · effort `medium` · spoken: False
- Scenarios passed: **19/20** (run 20, partial 0, skipped 0)
- Turns passed: **30/31** run (0 errored, 0 skipped)
- Total estimated cost: **$0.3019** (budget $1.50)
- Median Claude latency: **4269.0 ms** (max 18285.3 ms; tools median 9.6 ms; wall median 4291.5 ms)
- Replies with 1-2 sentences: 19
- Started: 2026-10-25T01:25:38+00:00 · buildings: `proc-campus-dev-009`, `proc-factory-dev-003`, `swiss-1689`

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
| wheelchair_floors | proc-factory-dev-003 | PASS | 2/2 |  |
| trolley_precision | proc-factory-dev-003 | FAIL | 0/1 | short |
| long_campus | proc-campus-dev-009 | PASS | 1/1 |  |
| benchmark_fact | proc-factory-dev-003 | PASS | 1/1 |  |
| missing_destination | proc-factory-dev-003 | PASS | 1/1 |  |
| wheelchair_lift_closed | proc-factory-dev-003 | PASS | 2/2 |  |
| leak_room_below | swiss-1689 | PASS | 1/1 |  |
| whats_on_my_right | proc-factory-dev-003 | PASS | 2/2 |  |
| left_unknown_heading | proc-factory-dev-003 | PASS | 1/1 |  |
| joke_and_thanks | proc-factory-dev-003 | PASS | 2/2 |  |
| nearest_exit_opening | proc-factory-dev-003 | PASS | 1/1 |  |
| missing_referent | proc-factory-dev-003 | PASS | 1/1 |  |

## Turns

| Scenario | # | User | Result | Failed checks | Tools | Reply | USD | Claude ms |
|---|---|---|---|---|---|---|---|---|
| coffee_nearest | 1 | I'm in Office 0.38. Where can I get a coffee? | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest coffee machine is in Kitchen 0.28 on the same floor, about 21 m away. You go through the office corridor to… | 0.0256 | 18285.3 |
| coffee_nearest | 2 | And how do I get there? | PASS |  | plan_route(ok) | It's about 21 m on the ground floor. You go out of Office 0.38 into Office corridor 0.16, then along it into Kitchen 0.… | 0.0071 | 3384.0 |
| toilet_unknown_start | 1 | Where is the nearest toilet? | PASS |  |  | Where are you right now? Tell me a room or landmark, or pick your spot on the map, and I'll find the nearest toilet fro… | 0.0022 | 1247.9 |
| toilet_unknown_start | 2 | I'm in the quality lab. | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest toilet is Toilet 0.22, about 10 m away on the same floor. You go out through the office corridor. | 0.0135 | 5889.2 |
| break_relax | 1 | I'm in Lab 0.02 and need a break. Where can I relax and get… | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | Canteen 0.32 is on the same floor, about 13 m from Lab 0.02. You can eat there and take a break. Shall I show you the r… | 0.0128 | 10087.6 |
| cnc_ambiguous | 1 | Where is the CNC mill? | PASS |  | find_places(ok) | There are three CNC mills (H-A1-A, H-A1-B and H-A1-C), all in Production zone H-A1 on the ground floor. Tell me where y… | 0.0056 | 3146.0 |
| cnc_ambiguous | 2 | The B one. I am at hall access 0.19. | PASS |  | find_places(ok), update_session(ok), plan_route(ok) | CNC mill H-A1-B is about 12 m from Hall access 0.19. It's on the same floor, just through the door into Production zone… | 0.0139 | 6583.3 |
| stt_errors | 1 | How do I get from the hall axis zero point one nine to the … | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | The route from Hall access 0.19 to Quality lab 0.21 is about 15 m, all on the ground floor. You go through Office corri… | 0.0161 | 8255.4 |
| unknown_room | 1 | Take me to the cafeteria on floor 7. | PASS |  | find_places(ok) | This building only has the ground floor and Floor 1, so there's no floor 7. Do you want me to look for a cafeteria or c… | 0.0047 | 3009.2 |
| facility_unavailable | 1 | Is there a gym or a shower here? | PASS |  | find_places(ok), find_places(ok) | I couldn't find a gym or a shower in this building. I can look for toilets, a canteen or a kitchen instead if that help… | 0.0067 | 4017.8 |
| closure_flow | 1 | I'm in Meeting room 0.20. What would happen if the office c… | PASS |  | find_places(ok), find_places(ok), update_session(ok), preview_closure(ok) | If Office corridor 0.16 closed, you'd be cut off from almost everything. From Meeting room 0.20, 77 of the 78 rooms you… | 0.0152 | 7355.3 |
| closure_flow | 2 | Okay, close the corridor for cleaning. | PASS · confirm=applied |  | propose_change(ok) | I've proposed closing Office corridor 0.16 for cleaning. Please press Confirm to apply it. Until you do, nothing change… | 0.0052 | 3471.1 |
| closure_flow | 3 | How do I get to the quality lab now? | PASS |  | find_places(ok), plan_route(ok) | You can't reach Quality lab 0.21 from Meeting room 0.20 right now. The closed Office corridor 0.16 is the only way thro… | 0.0096 | 4269.0 |
| closure_flow | 4 | Cleaning is done, please open the corridor again. | PASS · confirm=applied |  | propose_change(ok) | I've proposed reopening Office corridor 0.16. Please press Confirm to apply it. | 0.0061 | 2309.0 |
| closure_flow | 5 | And now? | PASS |  | plan_route(ok) | The Quality lab 0.21 is reachable again. It's about 10 m from Meeting room 0.20, through Office corridor 0.16. | 0.0076 | 2824.5 |
| wheelchair_floors | 1 | I use a wheelchair. How do I get from hall access 0.19 to O… | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | There's a step-free route of about 35 m. It goes through Office corridor 0.16, then up in the Office lift to Floor 1 an… | 0.0172 | 7721.1 |
| wheelchair_floors | 2 | And then to the kitchen 1.07? | PASS |  | find_places(ok), plan_route(ok) | From Office 1.04, the kitchen is about 20 m away on the same floor, so you won't need the lift. The route is step-free … | 0.0104 | 4514.5 |
| trolley_precision | 1 | I'm pushing a 0.9 metre wide trolley from logistics zone H-… | FAIL | short | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | There's an obstacle-checked route for your 0.9 m trolley, about 38 m. It goes from Logistics zone H-C5 through Main ais… | 0.0210 | 8550.2 |
| long_campus | 1 | From the lecture hall 0.31 to Lab 2.08, please. | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | The route from Lecture hall 0.31 to Lab 2.08 is about 46 m. It goes up two floors by Staircase 2, from the ground floor… | 0.0170 | 8384.6 |
| benchmark_fact | 1 | Which is the largest kitchen on floor 1, and how big is it? | PASS |  | building_fact(ok) | The largest kitchen on floor 1 is Kitchen 1.14, at about 28 m². | 0.0046 | 2450.2 |
| missing_destination | 1 | I am in Office 0.23. Can you show me the way? | PASS |  | find_places(ok), update_session(ok) | You're in Office 0.23. Where would you like to go? It could be a room, a toilet, the nearest exit, or somewhere else. | 0.0097 | 5583.0 |
| wheelchair_lift_closed | 1 | I use a wheelchair. I'm at hall access 0.19 and need to get… | PASS |  | find_places(ok), find_places(ok), update_session(ok), plan_route(ok) | There's no step-free route to Office 1.04 right now because the Office lift is closed. If the lift reopens, you could g… | 0.0161 | 7916.3 |
| wheelchair_lift_closed | 2 | Okay. Then just take me to kitchen 0.28 instead. | PASS |  | find_places(ok), plan_route(ok) | Kitchen 0.28 is on the same floor, about 47 m away, and the route is step-free. It goes through Office corridor 0.16. | 0.0098 | 4303.1 |
| leak_room_below | 1 | The dishwasher in kitchen 1.17 is leaking. Which room direc… | PASS |  | find_places(ok), building_fact(error), building_fact(ok) | The room directly underneath Kitchen 1.17 is Living room 0.59 (apartment 12) on the ground floor. This assumes the floo… | 0.0109 | 5853.3 |
| whats_on_my_right | 1 | What's on my right? | PASS |  | describe_surroundings(ok) | On your right you can see a filing cabinet about 1.3 m away. Behind the wall is the locker room (Locker room 0.39), wit… | 0.0055 | 2395.8 |
| whats_on_my_right | 2 | And what is behind me now? | PASS |  | describe_surroundings(ok) | Directly behind you, about 2.5 m away, is an open door to Office corridor 0.16. You can see it from where you are. | 0.0046 | 2315.3 |
| left_unknown_heading | 1 | What is on my left? | PASS |  |  | Which way are you facing? I need that to tell your left from your right. You can also tell me a landmark you're facing,… | 0.0027 | 1570.6 |
| joke_and_thanks | 1 | Tell me a quick joke. | PASS |  |  | Why did the architect go broke? Because he kept losing his floor plans! | 0.0020 | 1096.5 |
| joke_and_thanks | 2 | Thanks, that's all for now! | PASS |  |  | You're welcome! Just ask if you need directions or anything else in the building. | 0.0021 | 994.8 |
| nearest_exit_opening | 1 | I'm in Office 0.38. Take me to the nearest exit. | PASS |  | find_places(ok), update_session(ok), find_nearest(ok) | The nearest exit is the one at the end of Office corridor 0.16, about 44 m from Office 0.38 on the ground floor. The ro… | 0.0143 | 5719.6 |
| missing_referent | 1 | Take me to the biggest one. | PASS |  |  | Biggest what do you mean: the largest room, the largest room of a certain type (like a workshop or toilet), or somethin… | 0.0023 | 1545.3 |

Checks are deterministic (scripts/dialog_sim.py); the full per-turn record (input, ui, reply, tools, state, route summary, usage, timing, verdicts) is in the JSON report.
