# Phase A interfaces — navigation, assistant, voice in AriNav

One authoritative state: `app.state.navigation` (`NavigationService`) holds, per ingested building,
the building data from the `BuildingRepository` plus the RAM scenario (closures, `revision`).
The AriNav UI (manual actions), the Claude assistant tools and voice all call this service; nothing
keeps a second copy. Restarting the backend clears scenarios (as the repository does today).

The team's navgraph engine (Alex, `backend/navgraph`, `app.navigation.{base,navgraph_engine,registry}`)
behind `/api/chat` and `/api/buildings/{id}/graph` reads the same repository and **mirrors** this
scenario (`app.navigation.scenario_sync`): closures are copied in before every call and its write
tools (`set_closed`, `add_door`, `move_asset`, builder tools, `save`) are not offered. The graph
overlay shows the scenario closures. Routing answers in AriNav come from the vendored official
kit 1.1.0 router; navgraph is not used for them.

Ownership (separate modules, separate tests):

| Area | Backend module | Routes | Tests |
|---|---|---|---|
| Navigation core (official 1.1.0 router, precision router, search, nearest, operations, scenario, location) | `backend/app/navigation/` | `backend/app/api/routes/navigation.py` (`/api/nav/...`), `location.py` | `backend/tests/navigation/` |
| Assistant (Claude tool loop, dialog state) + voice (ElevenLabs) | `backend/app/assistant/` | `backend/app/api/routes/assistant.py` (`/api/assistant/...`) | `backend/tests/assistant/` |
| UI | `frontend/src/components/arinav/`, `frontend/src/lib/` | – | Vitest |

## Endpoint (start/target) — tagged union

```jsonc
{"kind": "space",   "id": "<space id>"}                          // room/zone
{"kind": "asset",   "id": "<asset id>"}                          // machine/equipment (official: ends in its space; precision: access point)
{"kind": "opening", "id": "<opening id>", "side_space_id": "<space id>"?}   // a door, opening or exit door; side optional for official
{"kind": "point",   "space_id": "<space id>", "position": [x, y]} // map click; precision may snap ≤ snap_m (reported)
```

Unknown kinds/IDs → `422 {"error": {"code": "invalid_input"|"unknown_place", "message", "details"}}`.
An **exit** is an opening whose `space_ids` contains `"outside"`. Exits are valid route targets/starts;
the official route ends at the exit door (graph node `door:<id>`), never continues outside.

## Navigation API (`/api/nav`)

| Method & path | Body / query | Returns |
|---|---|---|
| `GET /api/nav/{building_id}/state` | – | `{building_id, revision, closed: [ids], support: {official: true, precision: bool, precision_reason}}` |
| `GET /api/nav/{building_id}/search` | `q`, `kind?` (`room`/`asset`/`opening`/`exit`/`connector`), `floor_level?`, `limit?` | `{results: [{kind, id, name, type, floor_id, floor_level, space_id?, space_name?, is_exit?, closed?, score}]}` |
| `POST /api/nav/{building_id}/nearest` | `{from: Endpoint, category: "exit" \| "room_type:<t>" \| "asset_type:<t>" \| "query:<words>", step_free?, limit?}` | `{results: [{endpoint, name, floor_level, walk_cost_m}], from}` — ranked by **official walking cost**, unreachable omitted |
| `POST /api/nav/{building_id}/route` | `RouteRequest` | `RouteResponse` or 422 with `details.diagnosis` |
| `POST /api/nav/{building_id}/preview-closure` | `{element_ids, from?: Endpoint, step_free?}` | rooms that become unreachable / longer; changes nothing |
| `POST /api/nav/{building_id}/scenario` | `{action: "close"\|"open"\|"reset", element_id?, expected_revision}` | new state; `409` if `expected_revision` is stale |
| `POST /api/nav/{building_id}/operation` | `{operation, arguments}` (nine benchmark operations) | result incl. `formatted_answer` |
| `GET /api/nav/specs` | – | operation specs |
| `GET /api/nav/examples` | `split=dev\|train` | Train/Dev examples (only when the kit questions are configured) |
| `POST /api/location/resolve` | see `LOCALIZATION` below | `{status, endpoint, candidates, explanation}` |

### RouteRequest

```jsonc
{ "mode": "official" | "precision",
  "start": Endpoint, "target": Endpoint, "via": [Endpoint]?,
  "step_free": false,
  "assume_closed": [ids]?,                 // preview only, merged with scenario closures, never stored
  // precision only:
  "profile": "stationary" | "all" | "none", "width_m": 0.6, "safety_m": 0.05,
  "snap_m": 0, "allow_schematic_vertical": true,
  // unofficial, opt-in, labelled (not in kit 1.1.0):
  "unofficial": {"no_production_transit": false, "forklift": false} }
```

### RouteResponse (also the Phase B animation contract)

```jsonc
{ "mode": "official" | "precision", "status": "ok" | "unreachable" | "unsupported" | "blocked",
  "reachable": true, "checked": "official_rules" | "obstacle_checked" | "schematic_vertical",
  "length_m": 35.2,                        // official: kit cost (walk + stairs/lift); precision: checked XY length
  "walk_m": 16.7, "vertical_cost_m": 18.5, "revision": 3,
  "start":  {"kind", "id", "space_id", "floor_id", "position": [x, y]},
  "target": {"kind", "id", "space_id", "floor_id", "position": [x, y]},
  "space_path": [...], "element_path": [...],
  "path": [{"floor_id", "space_id", "kind": "walk" | "door" | "connector", "points": [[x, y], ...]}],
  "transitions": [{"kind": "door" | "stairs" | "lift", "element_id", "from_floor_id", "to_floor_id",
                   "position": [x, y], "schematic": bool, "cost_m": 7.0}],
  "waypoints": [{"index", "floor_id", "space_id", "elevation_m", "position": [x, y], "heading_deg",
                 "kind": "start" | "walk" | "door" | "stairs" | "lift" | "target",
                 "element_id"?, "distance_m", "fraction"}],   // Phase B: camera/walker follows these
  "directions": ["Walk about 12 m through the door into Office corridor 0.16.", ...],  // router text, ids removed
  "directions_raw": [...],                 // the router's own lines (with element ids)
  "unreachable_reason"?: str, "diagnosis"?: {...}, "warnings": [...], "assumptions": [...],
  "elapsed_ms": 12.3 }
```

`heading_deg`: direction of travel in the building XY frame (0° = +x, counter-clockwise).
`fraction` = `distance_m` / total distance along `path` (vertical moves count their cost).
`space_id` = the space the walker is in on reaching the point; the waypoints' space sequence equals
`space_path`. Phase B builds on these fields: see `docs/PHASE_B_CONTRACT.md`.
Schematic vertical moves have identical start/end XY and `schematic: true`.

## Assistant API (`/api/assistant`)

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/assistant/status` | – | `{live, voice, model, effort, tts_speed: {min, max, default}}` |
| `POST /api/assistant/turn` | `{session_id?, text, ui: UiState, speak?, voice?, tts_speed?}` (no `session_id` on the first turn: the server creates one and returns it) | `TurnResponse` |
| `POST /api/assistant/confirm` | `{session_id, proposal_id, approved}` | `{status: "applied"\|"cancelled", message, state}` |
| `POST /api/assistant/reset` | `{session_id}` | `{status}` |
| `POST /api/assistant/transcribe` | `{audio: base64 WAV 16 kHz mono, 0.1–10 s}` | `{text, stt_ms}` |

`UiState` (explicit user choices only): `{building_id, start?: Endpoint, destination?: Endpoint,
mode?: "official"|"precision", step_free?, profile?, width_m?, safety_m?}`.

`TurnResponse`: `{session_id, reply, status, phases: ["Finding destinations", "Checking routes",
"Showing directions", ...], ui: {route?: RouteResponse, route_request?, route_error?, focus?: {kind, id},
highlight?: [ids], endpoints?: {start, destination}, settings?, proposal?: {proposal_id, action,
element_id, element_name, element_kind}, operation?, building_id?}, tools: [{tool, input, status,
output, elapsed_ms}] /* developer mode */, usage: {input_tokens, output_tokens, cache_*,
estimated_usd}, timing_ms: {claude_ms, tools_ms, total_ms, tts_ms?}, audio_pcm16?: base64, sample_rate}`.

Rules: search and preview never change the scenario; only `/confirm` (human) or `/api/nav/.../scenario`
(developer UI) do. The assistant never returns raw tool errors or limit messages as replies.

## Location (`POST /api/location/resolve`)

`{building_id, source: "map_click"|"room_id"|"door_qr"|"simulated", space_id?, opening_id?,
side_space_id?, position?: [x, y], floor_id?}` → `{status: "resolved"|"ambiguous"|"outside_building",
endpoint, candidates, explanation, simulated: bool}`. QR/indoor positioning are later sources of the same call.
