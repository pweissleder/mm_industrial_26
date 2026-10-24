# Phase A — AriNav with navigation, assistant and voice

Branch `noah/phase-a-integration` = team `main` (incl. Alex's navgraph, PR #1) + the Phase A
modules. Not merged into `main`; that needs the team's approval.

AriNav (team UI) is the only UI. It now has two modes:

- **Assistant** (default): chat with Claude, push-to-talk voice (ElevenLabs STT/TTS, transcript
  shown), routes on the 3D map, directions under the reply, changes only after Confirm.
- **Developer**: manual routing without AI — endpoints by search or map pick (rooms, assets,
  doors, exits, exact points), official/precision mode, step-free, clearance settings, nearest
  exit, scenario close/open/reset/preview, kit Train/Dev examples, last assistant tool calls.

Without keys everything except chat/voice works (Developer mode, search, routing, scenario).

**HQ models are not built automatically** (Developer → *HQ model* → “Auto-build HQ for new
buildings”, default off, stored per browser). With a key on the server, the team's ingestion
pipeline calls Claude (measured: about $0.30–1.50 per building). New buildings open in the free
browser preview (LQ); the top-bar chip “LQ only · Build HQ” starts a build on request.
Already processed buildings still load their HQ model (`backend/data/workspace/`, local,
gitignored; an unchanged input is not rebuilt). Switching auto-build on only affects buildings
opened afterwards.

## Who owns what

| Area | Code | Origin |
|---|---|---|
| App shell, 3D viewer, sidebar, uploads, ingestion, pipeline, storage | `frontend/src/components/arinav/*` (base), `backend/app/{ingestion,pipeline,storage,domain,agent}` | Team (unchanged except the wiring below) |
| navgraph engine, `/api/chat` agent, graph overlay | `backend/navgraph`, `backend/app/navigation/{base,navgraph_engine,registry}.py`, `scene.ts` graph layer | Team (Alex) |
| Navigation core: official kit 1.1.0 router (vendored, blob-pinned), precision router, search, nearest, scenario, nine benchmark operations, location | `backend/app/navigation/{service,response,search,location,kit,errors}.py`, `official/`, `ported/` | Phase A |
| Scenario sharing for navgraph (one scenario) | `backend/app/navigation/scenario_sync.py` | Phase A |
| Assistant (Claude tool loop, dialog state, confirmation) and voice | `backend/app/assistant/` | Phase A |
| HTTP | `backend/app/api/routes/{navigation,location,assistant}.py` | Phase A |
| UI modules | `ChatDock.tsx` (assistant/voice dock, team links kept), `ChatReply.tsx`, `DevPanel.tsx`, `useNavigation.ts`, `useVoice.ts`, `routeGeometry.ts`, route overlay in `scene.ts`, `lib/{nav,voice,guidance}.ts`, `styles/arinav-phase-a.css`, `public/recorder.js` | Phase A |
| Team files touched for wiring | `main.py` (services + routers), `buildings.py` (graph overlay shows scenario closures), `AriNavApp.tsx`, `Sidebar.tsx`, `Viewer3D.tsx`, `DetailsCard.tsx`, `Icons.tsx`, `api.ts`, `Layout.astro`, `pyproject.toml`, `config.py`, `.env.example` | merge/wiring |

Contracts: `docs/PHASE_A_INTERFACES.md` (API), `docs/PHASE_B_CONTRACT.md` (guidance data).

### Graph models — decision taken, team to confirm

There are two route engines now: the **vendored official kit 1.1.0 router** (Phase A, behind all
AriNav routes and the assistant) and **navgraph** (team, behind `/api/chat` and the graph
overlay). Both read the single `BuildingRepository`; the scenario lives only in
`NavigationService`; navgraph shares it (its `set_closed` writes through, asset edits stay in the
navgraph copy and the served model, topology edits are not offered). AriNav's routing was not switched to
navgraph because the official router is the judges' code and is verified on all reference sets;
switching would need an equivalence run first (`backend/navgraph_checks` + `scripts/nav_benchmark.py`
on the same items). Open team decision: keep both, or consolidate on one engine after that run.

## Start

Requirements: Python 3.11 + [uv](https://docs.astral.sh/uv/), Node ≥ 22.12. Once:
`cd backend && uv sync` and `cd frontend && npm ci`.

- **Windows (PowerShell 5.1 or 7)**, keys stored once with DPAPI
  (`scripts/windows/Register-ApiKey.ps1 -Provider Anthropic`, `... -Provider ElevenLabs`):
  `powershell -File scripts/windows/Start-AriNav.ps1 -Live -DatasetsDir <folder with building datasets>`
  (without `-Live`: offline, no AI). Uses `uv` if found, else `backend/.venv`.
- **Docker / native**: as in `README.md`; put `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` in
  `backend/.env` (`.env.example` lists `ASSISTANT_MODEL`, `ASSISTANT_EFFORT`, `ELEVENLABS_VOICE_ID`).
- Open http://localhost:4321, pick a building in **Location → Building**.
- Kit Train/Dev examples and the benchmark harness need the kit sources:
  `SYNERA_REFERENCE_DIR` (checkout at kit commit dc03c44) and `SYNERA_KIT_ZIP` (kit ZIP).

## Tests and evidence (all re-runnable)

| What | Command | Result (24.10.2026) |
|---|---|---|
| Backend unit + integration (team + Phase A) | `cd backend && uv run pytest` | 256 passed, 3 skipped |
| Benchmark harness, kit 1.1.0, official scorer | `cd backend && uv run python scripts/nav_benchmark.py` | benchmark **655/655**, warm-up routes **1338/1338**, locate **553/553**, count **239/239** → `docs/reports/nav_benchmark_1_1_0.md` |
| API matrix, 5 building families × 3, incl. negative cases | `python scripts/e2e/api_matrix.py --per-family 3` (running backend) | **226/226** → `docs/reports/e2e_api_matrix.json` |
| Frontend | `cd frontend && npm run lint && npm test && npm run build` | 0 errors, 23 tests, build ok |
| Browser E2E offline (no AI) | `node scripts/e2e/browser_e2e.mjs` (needs `playwright-core`, installed Chrome) | **10/10** → `docs/reports/browser/browser_e2e.json` + screenshots |
| Browser E2E live: assistant + voice | `VOICE_WAV=… node scripts/e2e/browser_e2e.mjs --live` | **14/14** → `docs/reports/browser/browser_e2e_live.json`, screenshots 06–09 |
| Multi-turn dialog matrix, 13 scenarios / 21 turns (live Claude Sonnet 5.5, effort medium) | `cd backend && uv run python scripts/dialog_sim.py --live --max-usd 1` (key in env) | **12/13 scenarios, 20/21 turns**, 0,23 $, Claude median 6,1 s → `docs/reports/dialog_sim_live_medium_v2.md`; only miss: one reply of 4 sentences (length check). First run before restoring the prototype's style rules: 8/21, all misses were length |

The voice sample for the browser test is synthesised with the app's ElevenLabs client
(`scripts/e2e/make_voice_sample.py`) and played by Chrome's fake microphone.

## Fixed in this phase (with regression tests)

1. Route to an exit ended inside a room / at the stairs: multi-floor stair rides without walking on
   the intermediate floors were drawn out of order (`response.py`, `test_response_order.py`).
2. Ambiguous follow-up exhausted the assistant's tool steps and ended with a limit message: the
   assistant asks one focused question; a reached budget ends with a normal answer
   (`test_case_b_*`, `test_ambiguous_follow_up_*`, live browser check).
3. First chat message always failed (422: `session_id` required, contract says optional).
4. Assistant routes were not shown (`ui.route` was wrapped, contract says RouteResponse).
5. Assistant and navigation had two look-alike error classes, so refused navigation calls inside
   a tool would have escaped the assistant's error handling (one class now).
6. Directions showed element ids; exit labels showed ids; styles for all Phase A UI parts were
   missing; stair segments were drawn as floor lines; the UI kept a stale scenario.
7. Windows start scripts needed PowerShell 7 (not installed on the demo machine).

## Known limits

- Scenarios and assistant sessions live in RAM (as the team repository does); a backend restart
  clears them.
- Precision routing is supported for procedural factory/campus buildings with original SVGs;
  other sources get a clear `unsupported` and the official route.
- The official route ignores furniture (kit rule); "obstacle-checked" is only claimed for
  precision results.
- Production-zone transit and forklift rules are not in kit 1.1.0; available only as labelled
  opt-in (`unofficial`), never default.
- Lint: `ruff`/`mypy` report pre-existing issues in team files `backend/scripts/navgraph_viz.py`
  and `backend/app/api/routes/buildings.py` (also on `main`); Phase A code is clean.
- Live turns take ~4–11 s (Claude median 6 s, several tool rounds); voice STT/TTS each ~0.7–0.9 s.
- Live API spend this round: about 0.75 $ for assistant/voice tests plus about 2.1 $ for three HQ pipeline
  runs started by opening unprocessed buildings in the live app (before auto-build was switched off).
- Real microphone test by a person is still open (automated test uses a synthetic voice).
