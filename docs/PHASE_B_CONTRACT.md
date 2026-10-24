# Phase B starting point — guidance data contract

Phase B (live guidance along a route: following the user, re-routing, arrival; later camera and
HQ animation) builds on data that Phase A already delivers. Nothing in Phase A animates, moves a
camera or simulates walking. This file fixes what Phase B can rely on.

Code: `frontend/src/lib/guidance.ts` (types + `progressAt`, tested in `guidance.test.ts`);
backend source of every field: `backend/app/navigation/response.py` (`official_response`,
`precision_response`, `waypoints`).

## 1. Route (input, computed by the backend — `POST /api/nav/{building_id}/route`)

| Need | Field | Notes |
|---|---|---|
| Computed waypoints | `waypoints[]` `{index, floor_id, space_id, elevation_m, position [x,y], heading_deg, kind, element_id?, distance_m, fraction}` | One per polyline vertex, door and floor change. `kind`: `start`, `walk`, `door`, `stairs`, `lift`, `target`. `distance_m` = walk length + vertical cost so far; `fraction` = share of the total. |
| Room sequence | `space_path[]`; per waypoint `space_id` | The waypoints' space sequence equals `space_path` (tested). A door waypoint keeps the space before the door. |
| Floor sequence | `waypoints[].floor_id`, `path[].floor_id` | Vertical moves keep XY; the floor switches at the move's end. |
| Direction of travel | `waypoints[].heading_deg` | Building XY frame, 0° = +x, counter-clockwise; kept through vertical moves. |
| Special transitions | `transitions[]` `{kind: door\|stairs\|lift, element_id, from_floor_id, to_floor_id, position, to_position?, schematic, cost_m, is_exit?}` | In route order. Exit doors have `is_exit: true`; a route to an exit ends at that door. One stairs/lift entry per floor hop. |
| Geometry to draw | `path[]` `{floor_id, space_id, kind: walk\|connector, points}` | Connector segments are zero-length markers (drawn as vertical moves). |
| Words | `directions[]` (no ids), `directions_raw[]` (router lines) | Router text only, never model text. |
| Guarantee | `mode`, `checked` | `official_rules` (kit 1.1.0, furniture ignored), `obstacle_checked`, `schematic_vertical`. |
| Revision | `revision` | Scenario revision the route was computed under. |

## 2. Location (input)

`POST /api/location/resolve` → `{status: resolved|ambiguous|outside_building, endpoint, candidates,
explanation, simulated}` from `source: map_click | room_id | door_qr | simulated` (QR and indoor
positioning are later sources of the same call). In the UI the simulated location is
`useNavigation().simulated` and is sent to the assistant as the user's start. Phase B type:
`GuidanceLocation {source, floor_id, space_id, position, heading_deg?, accuracy_m?, at}`.

## 3. Progress (derived, client side)

`progressAt(route, distance_m)` → `RouteProgress {distance_m, remaining_m, fraction,
waypoint_index, floor_id, space_id, position, heading_deg, next_transition, to_next_transition_m}`.
Pure and deterministic; Phase B maps a (simulated or real) location to a distance along the route
and calls it. Off-route detection (distance from the polyline above a threshold) is Phase B work.

## 4. Session state and revisions

`GuidanceState {status: idle|active|paused|recomputing|arrived|off_route, start, target, route,
scenario_revision, route_revision, location, progress}`.

- `scenario_revision` = `route.revision`. The scenario is shared (`GET /api/nav/{id}/state`
  → `{revision, closed}`); the UI reloads it after every assistant turn and on window focus.
  When `state.revision > scenario_revision`, guidance must recompute
  (`reason: scenario_changed`).
- `route_revision` increments with each recomputation inside one session.
- Changes to the scenario only happen through human confirmation (assistant proposal + Confirm)
  or the developer panel; guidance never changes the scenario.

## 5. Events

`GuidanceEvent` (see `guidance.ts`): `start`, `pause`, `resume`, `progress`, `transition_ahead`
(door/stairs/lift with distance), `recompute` (`scenario_changed | off_route | user_request |
settings_changed`), `arrive`, `error`. Voice (ElevenLabs TTS) and the map consume the same events;
the backend has no guidance endpoint yet.

## Not in Phase A (deliberately)

Camera paths, walker animation, virtual walking simulation, HQ-model animation, real indoor
positioning, server-side guidance sessions.
