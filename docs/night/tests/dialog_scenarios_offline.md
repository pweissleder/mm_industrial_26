# Offline dialog / state / tool scenarios

**What this is:** 60 defined scenarios that drive the REAL assistant (session state, UI
sync, tool handlers, step-free/width guards, proposals/closures) on the real NavigationService
and real buildings. Claude is replaced by a scripted client that returns the tool calls a model
would make, turn by turn, then a final text.

**What this is NOT:** a language-understanding test. Whether Claude chooses these tool calls for a
given sentence is not tested here (that is covered by a few live dialogs). No network, no API key,
no constant-text mock.

Generated 2026-10-25 01:52 UTC by `backend/scripts/dialog_scenarios_report.py` from
`backend/tests/assistant/test_dialog_scenarios.py` (pytest exit code 0).

Result legend: `passed`; `xfail` = the scenario documents a genuine defect of the current code
(strict xfail: it fails today, the reason is the defect); `FAILED` = regression; `skipped` =
building data missing.

## Totals

passed: 60 (of 60)

| Matrix row | passed | xfail | FAILED | skipped |
|---|---|---|---|---|
| C01 | 4 | 0 | 0 | 0 |
| C02 | 4 | 0 | 0 | 0 |
| C03 | 5 | 0 | 0 | 0 |
| C04 | 2 | 0 | 0 | 0 |
| C05 | 3 | 0 | 0 | 0 |
| C06 | 1 | 0 | 0 | 0 |
| C07 | 2 | 0 | 0 | 0 |
| C08 | 2 | 0 | 0 | 0 |
| C09 | 2 | 0 | 0 | 0 |
| H01 | 1 | 0 | 0 | 0 |
| H02 | 2 | 0 | 0 | 0 |
| H03 | 1 | 0 | 0 | 0 |
| INV-1 | 1 | 0 | 0 | 0 |
| INV-2 | 3 | 0 | 0 | 0 |
| INV-3 | 6 | 0 | 0 | 0 |
| INV-4 | 7 | 0 | 0 | 0 |
| INV-5 | 3 | 0 | 0 | 0 |
| INV-7 | 1 | 0 | 0 | 0 |
| S01 | 4 | 0 | 0 | 0 |
| S02 | 3 | 0 | 0 | 0 |
| S03 | 2 | 0 | 0 | 0 |
| S05 | 1 | 0 | 0 | 0 |

## Scenarios

| ID | Matrix row | What is checked | Result |
|---|---|---|---|
| C01a | C01 | UI start set, then explicitly cleared (start=null): nearest exit asks for the start (missing_start), no route from the old room, no nearest() call | passed |
| C01b | C01 | a UI start that the UI no longer sends (key omitted, older clients) is dropped too | passed |
| C01c | C01 | a start the USER stated (update_session) survives turns without a UI start; the exit route then starts there and ends at an exit door | passed |
| C01d | C01 | destination=null from the UI clears the destination: plan_route with null endpoints fails honestly (missing_endpoint) instead of reusing the old destination | passed |
| C02a | C02 | wheelchair user, only lift closed (confirmed scenario), destination on another floor; model passes step_free=false: request stays step-free, unreachable, no stairs, constraint note returned | passed |
| C02b | C02 | lift closed, find_nearest exit with step_free=false from F1: nearest() still asked step-free, no exit offered, no route, constraint note | passed |
| C02c | C02 | what-if (assume_closed=[lift]) for a step-free user: unreachable, no stairs; the real scenario is untouched and the next route uses the open lift again | passed |
| C02d | C02 | lift closed in precision mode, model drops step_free: failed honestly (no official fallback, mode stays precision), step-free request, constraint note, no route shown | passed |
| C03a | C03 | step-free survives a UI mode switch to precision and back and a new turn; the official route then uses the lift, never the stairs | passed |
| C03b | C03 | UI building switch keeps step-free and width but clears start, destination and the previous building's conversation | passed |
| C03c | C03 | only an explicit user change lifts step-free: UI toggle off, back on via update_session, then the user lifts it; only then a stairs route (lift closed) is ok | passed |
| C03d | C03 | reroute after a scenario change by another consumer: same session endpoints, step-free still requested, now unreachable without stairs | passed |
| C03e | C03 | a precision route echoes the binding width; a UI mode switch keeps width/safety | passed |
| C04a | C04 | inspected machine reaches the model context by name; start/destination untouched | passed |
| C04b | C04 | an unknown inspected item is dropped (not kept from before); the 'selection' alias works; inspected=null clears | passed |
| C05a | C05 | exact map-point start routes to an exit door: point start (requested XY) and final opening position preserved | passed |
| C05b | C05 | a map point in an unknown room is refused with a ui_note; no fallback to the earlier start | passed |
| C05c | C05 | a map point far outside its room polygon is refused at sync (ui_note), not stored as 'a point in Office 1.04'; a click 0.2 m outside the wall is kept, moved just inside, and the router accepts it | passed |
| C06a | C06 | successive vertical moves (campus): F0->F4 by lift, then from the arrival room F4->F1; floors visited in order, the second route starts where the first arrived | passed |
| C07a | C07 | propose closing the lift changes nothing; Confirm applies it, drops the stale last_route, the proposal cannot be confirmed twice; the next turn sees the closure | passed |
| C07b | C07 | a cancelled proposal changes nothing and the model is told it was cancelled | passed |
| C08a | C08 | missing referent ('the biggest one'): a clarifying text-only turn mutates nothing (state, UI, navigation calls) | passed |
| C08b | C08 | 'the second one' resolves from the previous turn's ranked results: the model context carries the found places in order (name = kind:id); the route goes to the second | passed |
| C09a | C09 | search returns an opening (exit) and an asset; routes to them keep the typed endpoint: the exit route ends at the door, the asset route targets the asset (not room centre) | passed |
| C09b | C09 | an opening id passed as a space is refused (unknown_place), no router call, no guessing of another type | passed |
| S01_east | S01 | pose heading 0 deg (CCW from +X): the corridor door due east is 'ahead'; the direction filter keeps only that side; heading normalised to [0, 360) | passed |
| S01_north | S01 | pose heading 90 deg (CCW from +X): the corridor door due east is 'right'; the direction filter keeps only that side; heading normalised to [0, 360) | passed |
| S01_west | S01 | pose heading 180 deg (CCW from +X): the corridor door due east is 'behind'; the direction filter keeps only that side; heading normalised to [0, 360) | passed |
| S01_south | S01 | pose heading -90 deg (CCW from +X): the corridor door due east is 'left'; the direction filter keeps only that side; heading normalised to [0, 360) | passed |
| S02_occluded | S02 | server racks in the next room are reported 'behind a wall', never visible; things in the own room are visible | passed |
| S02_no_heading | S02 | unknown view direction: no sides are given and the result tells the model to ask; the direction filter is not applied | passed |
| S02_no_pose | S02 | no pose (cleared): describe_surroundings refuses (missing_pose) instead of guessing | passed |
| S03_leak_below | S03 | leak in Office 1.04: room_stacked below = the largest-overlap room on F0 (Hall access 0.19), highlighted; no route, no state change | passed |
| S03_no_floor_below | S03 | room_stacked below the ground floor and malformed arguments are honest errors (invalid_input), nothing invented | passed |
| S05_chitchat | S05 | a joke/thanks turn without tools passes the reply through and mutates nothing (no navigation call, state and step-free unchanged) | passed |
| H01_only_navigation_counts | H01 | activity recap counts only motion/arrivals inside a navigation; preview motion, a duplicate arrival and motion after arrival do not count; paused time excluded | passed |
| H02_reset_and_isolation | H02 | two assistant sessions are isolated; a reset session comes back without the old pose, start, step-free or history | passed |
| H02_deleted_history_not_resurrected | H02 | a deleted activity session cannot be appended to or read again; new ids are fresh | passed |
| H03_synthetic_labelled | H03 | demo and test sessions are labelled synthetic in the recap; real ones are not | passed |
| INV_search_does_not_move_start | INV-2 | find_places and find_nearest (no route) only highlight: start and destination stay, no endpoints/route in the UI payload, no router call | passed |
| INV_preview_closure_changes_nothing | INV-2 | preview_closure for a step-free user reports consequences but changes neither the scenario, the revision, the session nor creates a proposal | passed |
| INV_clear_is_real | INV-1 | 'Clear simulated location' (start=null, pose=null) really clears: surroundings and nearest refuse, the model sees no start/pose | passed |
| INV_step_free_tightens_only | INV-4 | session step-free + find_nearest(step_free=false, and_route): nearest() and the route request are step-free; the route uses the lift | passed |
| INV_step_free_tool_tightens_persists | INV-4 | a tool argument step_free=true tightens the session and the next route stays step-free | passed |
| INV_step_free_guard_defence | INV-4 | fault injection: a router that ignores step_free returns a stairs route; the assistant's guard turns it into 'no step-free route', shows no route and records a failed route | passed |
| INV_lift_closed_no_stairs_fallback | INV-4 | lift closed, step-free nearest toilet with route: the same-floor toilet is chosen (the closer one downstairs is not offered via stairs), no vertical transition | passed |
| INV_route_constraints_echoed | INV-4 | precision + step-free + width/safety from the UI: the route request and the route's own constraints echo exactly the session settings (guidance reroutes reuse them) | passed |
| INV_width_no_silent_fallback | INV-4 | precision with a 1.0 m width that no door path supports: failed with the 'offer official' hint, no silent official route, width and mode unchanged, one router call | passed |
| INV_width_binding_nearest | INV-4 | precision mode with a required width of 1.0 m: find_nearest must not rank kitchens whose only doors are 0.9 m as reachable (they are named as not passable); at 0.6 m the closest kitchen is offered again and the route to it is obstacle-checked | passed |
| INV_location_source_simulation | INV-3 | UI start with start_source='simulation' is labelled known_from='simulation' in the snapshot and in the session_state the model sees | passed |
| INV_location_source_manual | INV-3 | UI start with start_source=None is labelled known_from='manual' in the snapshot and in the session_state the model sees | passed |
| INV_location_source_qr | INV-3 | UI start with start_source='qr' is labelled known_from='qr' in the snapshot and in the session_state the model sees | passed |
| INV_location_source_hypothesis | INV-3 | UI start with start_source='hypothesis' is labelled known_from='hypothesis' in the snapshot and in the session_state the model sees | passed |
| INV_location_source_unknown_not_manual | INV-3 | an unsupported source (e.g. an experimental image estimate) must not be labelled 'manual' (= user-confirmed) | passed |
| INV_location_source_after_update_session | INV-3 | a start the user states after an earlier QR start must not inherit the stale 'qr' label | passed |
| INV_stale_pose_rejected | INV-5 | invalid poses are discarded, never kept from the previous turn: foreign-building room, non-finite position; a non-finite heading becomes 'unknown' | passed |
| INV_pose_outside_room_rejected | INV-5 | a pose whose position lies far outside its claimed room is discarded | passed |
| INV_route_preview_does_not_move_user | INV-2 | 'how far is it from the kitchen to the lab?' (plan_route with an explicit other start) must not relocate the user: the next nearest search still starts at Office 1.04 | passed |
| INV_unknown_room_honest_error | INV-7 | unknown room ids from the model give unknown_place for route and update_session; start and destination are untouched; no router call | passed |
| INV_building_switch_by_tool | INV-5 | select_building: unknown id refused; a real switch clears start, proposals and history (the old proposal cannot be confirmed); an unknown UI building is refused and the state stays | passed |

## Known defects (strict xfail)

None open.

## Defects these scenarios found, fixed since (now regression tests)

- **C05c** (passed): Fixed; was: sync_ui/check_endpoint only checks that the point's space exists, not that the position lies inside it (the error text claims 'not inside a room'); the bad point is stored and the router refuses it only later
- **INV_width_binding_nearest** (passed): Fixed; was: find_nearest ignores the session width (NavigationPort.nearest has no width parameter): Kitchen 0.28 behind a 0.9 m door is ranked first for a 1.0 m width
- **INV_location_source_unknown_not_manual** (passed): Fixed; was: sync_ui maps any unknown start_source to 'manual', so an unverified estimate is presented to the model as a manually confirmed position
- **INV_location_source_after_update_session** (passed): Fixed; was: update_session (and plan_route) set state.start but never start_source; the previous UI source ('qr') stays and is shown as known_from for the new start
- **INV_pose_outside_room_rejected** (passed): Fixed; was: _check_pose only checks that the space id exists and the XY is finite; a pose 60 m away from Office 1.04 is accepted and describe_surroundings answers from it
- **INV_route_preview_does_not_move_user** (passed): Fixed; was: tool_plan_route writes an explicit start into session.state['start'] (and keeps the old start_source), so a route between two other places moves the user

Matrix rows: C01-C09, S01-S03, S05, H01-H03 from docs/night/ACCEPTANCE_MATRIX.csv; INV-n = invariant n of docs/night/NIGHT_TASK.md section 'Invarianten'.
