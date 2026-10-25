# Navigation case suite (backend, official mode)

Seed `20261025`, 140 cases, generated 2026-10-25T01:58:34Z by `backend/scripts/nav_cases.py` (run from backend/: `.venv/Scripts/python scripts/nav_cases.py --n 140 --seed 20261025`).

**140/140 passed.** Expectations come from an independent BFS over the building.json openings/vertical connectors (closures and step-free rules applied), never from the router; every returned route is re-validated independently (adjacency via element_path, closures, step-free, length = drawn polyline + vertical cost from floor elevations within max(1 %, 0.05 m), continuity, polyline inside the path's spaces within 0.3 m, the same length/inside/floor-order/end-space rules on the joined waypoints the UI animates, endpoints preserved, unreachable diagnosis category). See the module docstring for the exact rules.

Buildings: `proc-campus-dev-000` (proc-campus), `proc-campus-dev-013` (proc-campus), `proc-factory-dev-003` (proc-factory), `proc-factory-dev-007` (proc-factory), `swiss-1785` (swiss), `swiss-2074` (swiss), `resplan-1465` (resplan), `resplan-14509` (resplan), `osm-way360544293` (osm), `osm-way27326387` (osm).

### By group

| | total | pass | fail | guaranteed reachable (pass/total) | blocked/unsupported (pass/total) |
|---|---:|---:|---:|---:|---:|
| guaranteed_reachable | 88 | 88 | 0 | 88/88 | 0/0 |
| blocked_or_unsupported | 52 | 52 | 0 | 0/0 | 52/52 |

### By category

| | total | pass | fail | guaranteed reachable (pass/total) | blocked/unsupported (pass/total) |
|---|---:|---:|---:|---:|---:|
| reachable | 41 | 41 | 0 | 38/38 | 3/3 |
| step_free | 22 | 22 | 0 | 13/13 | 9/9 |
| endpoints | 30 | 30 | 0 | 28/28 | 2/2 |
| closures | 25 | 25 | 0 | 9/9 | 16/16 |
| invalid | 22 | 22 | 0 | 0/0 | 22/22 |

### By family

| | total | pass | fail | guaranteed reachable (pass/total) | blocked/unsupported (pass/total) |
|---|---:|---:|---:|---:|---:|
| proc-campus | 33 | 33 | 0 | 18/18 | 15/15 |
| proc-factory | 28 | 28 | 0 | 21/21 | 7/7 |
| swiss | 27 | 27 | 0 | 15/15 | 12/12 |
| resplan | 26 | 26 | 0 | 19/19 | 7/7 |
| osm | 26 | 26 | 0 | 15/15 | 11/11 |

### By building

| | total | pass | fail | guaranteed reachable (pass/total) | blocked/unsupported (pass/total) |
|---|---:|---:|---:|---:|---:|
| proc-campus-dev-000 | 17 | 17 | 0 | 10/10 | 7/7 |
| proc-campus-dev-013 | 16 | 16 | 0 | 8/8 | 8/8 |
| proc-factory-dev-003 | 14 | 14 | 0 | 11/11 | 3/3 |
| proc-factory-dev-007 | 14 | 14 | 0 | 10/10 | 4/4 |
| swiss-1785 | 14 | 14 | 0 | 5/5 | 9/9 |
| swiss-2074 | 13 | 13 | 0 | 10/10 | 3/3 |
| resplan-1465 | 13 | 13 | 0 | 9/9 | 4/4 |
| resplan-14509 | 13 | 13 | 0 | 10/10 | 3/3 |
| osm-way360544293 | 13 | 13 | 0 | 7/7 | 6/6 |
| osm-way27326387 | 13 | 13 | 0 | 8/8 | 5/5 |

### Variants

| category/variant | cases | expected outcomes |
|---|---:|---|
| closures/connector:assume_closed | 1 | reachable: 1 |
| closures/connector:scenario_change | 3 | reachable: 3 |
| closures/door:assume_closed | 5 | reachable: 2, unreachable: 3 |
| closures/door:scenario_change | 13 | reachable: 3, unreachable: 10 |
| closures/room:assume_closed | 2 | unreachable: 2 |
| closures/room:scenario_change | 1 | unreachable: 1 |
| endpoints/asset_start | 7 | reachable: 7 |
| endpoints/asset_target | 3 | reachable: 2, unreachable: 1 |
| endpoints/door_start | 3 | reachable: 2, unreachable: 1 |
| endpoints/exit_target | 5 | reachable: 5 |
| endpoints/opening_start_side | 3 | reachable: 3 |
| endpoints/point_start | 3 | reachable: 3 |
| endpoints/point_start_exact | 5 | reachable: 5 |
| endpoints/point_target | 1 | reachable: 1 |
| invalid/assume_unknown | 1 | error:unknown_place: 1 |
| invalid/bad_kind | 2 | error:invalid_input: 2 |
| invalid/bad_mode | 1 | error:invalid_input: 1 |
| invalid/bad_side | 1 | error:invalid_input: 1 |
| invalid/change_unknown | 1 | error:unknown_place: 1 |
| invalid/closed_start_room | 2 | blocked: 2 |
| invalid/closed_target_exit | 2 | blocked: 2 |
| invalid/point_nan | 2 | error:invalid_input: 2 |
| invalid/point_outside | 2 | error:invalid_input: 2 |
| invalid/precision_unsupported | 1 | error:geometry_unsupported: 1 |
| invalid/stale_revision | 1 | error:revision_conflict: 1 |
| invalid/step_free_string | 1 | error:invalid_input: 1 |
| invalid/unknown_asset | 2 | error:unknown_place: 2 |
| invalid/unknown_building | 1 | error:unknown_building: 1 |
| invalid/unknown_space | 2 | error:unknown_place: 2 |
| reachable/cross_floor | 22 | reachable: 19, unreachable: 3 |
| reachable/same_floor | 18 | reachable: 18 |
| reachable/same_floor_via_other_floor | 1 | reachable: 1 |
| step_free/cross_floor | 6 | reachable: 3, unreachable: 3 |
| step_free/non_step_free_opening | 4 | unreachable: 4 |
| step_free/random_pair | 12 | reachable: 10, unreachable: 2 |

## Failures

None.
