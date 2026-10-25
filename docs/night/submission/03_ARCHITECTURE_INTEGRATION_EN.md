# AriNav — Architecture and Integration Notes
**Updated with the night build (branch `noah/night-immersive`); numbers from `docs/night/TEST_SUMMARY.md`.**

## Purpose
A conversational indoor-navigation prototype that combines structured building data, deterministic tools and an interactive map. The assistant handles questions, clarification and tool selection. Geometry and route validity remain in code.

## System boundaries
| Layer | Responsibility | Verify before submission |
|---|---|---|
|Building repository|Load shared rooms, openings, floors and assets|Current ingestion path and supported data|
|Navigation service|Official reference mode; separate precision mode|Current API, exact endpoint handling, supported geometry|
|Scenario state|Closures and confirmed edits with revision handling|UI/assistant share current state|
|Assistant|Intent, dialogue context, validated tool calls|Actual model/config; no unconfirmed edits|
|Voice|ElevenLabs transcription and speech playback|Working live entrypoint, key storage, failure fallback|
|AriNav renderer|Map, route, selection and optional POV|Real renderer stack, mobile limits|
|Simulation|Controlled user pose; camera independent|Exact-vs-schematic transitions; deterministic tests|
|Optional history|Opt-in local events and recap|Deletion/reset and no raw audio by default|

## Data and routing
The Synera kit supplies structured JSON, equipment tables and visualization files. Kit1.1.0 changes expected route answers and routing conventions; pin the actual reference commit used. Official routing ignores furniture. Precision routing uses selected footprints and declared clearance and is not available for every data source. Do not present benchmark cost as physical walking distance or real travel time.

The virtual camera and user position are separate. Device rotation may change heading after calibration; it does not independently determine a real indoor location. Synthetic camera-localization experiments, if any, must be identified separately.

## Verification summary (night build, 2026-10-25)
- Code: branch `noah/night-immersive` (from main `86d0ca3`); not merged.
- Reference/data: official kit 1.1.0 router, vendored and blob-pinned; SyneraKit dev datasets.
- Structured benchmark: 655/655 (unchanged after all night changes).
- Warm-up routes / locate / count: 1338/1338, 553/553, 239/239.
- Seeded navigation: 140/140 (+600/600 on three more seeds), independent path validation.
- Movement: 54/54 cases on real buildings, 990/990 stress.
- Offline state/tool scenarios: 60/60 (scripted tool calls; not language understanding).
- Live dialogs: 19/20 (Claude Sonnet 5.5, 20 dialogs, 7 held out and passed on first run), $0.30 per run.
- Browser/visual: immersive 14/14, guidance 14/14, Phase A 10/10, HQ 8/8, three recorded live scenes.
- Physical phone/microphone tests: NOT TESTED.
- Known unsupported: precision routing for some data families; camera localisation not built.

## Security and privacy
API keys remain server-side/local secure storage. No public raw transcripts or audio. User/location memory is optional, local and deletable. Confirmation required for scenario writes; constraint changes are explicit. No certified accessibility or emergency-routing guarantee.

## Reproduction
- Windows: `scripts/windows/Start-AriNav.ps1 -DatasetsDir <dev data> [-Live] [-Profile core|immersive|showcase]`
  (Python 3.11 backend via uv or backend/.venv, Node frontend; keys only via `Register-ApiKey.ps1`,
  DPAPI, injected into the backend process; env names ANTHROPIC_API_KEY, ELEVENLABS_API_KEY).
- Tests: `backend: .venv/Scripts/python -m pytest -q`, `scripts/nav_benchmark.py`, `scripts/nav_cases.py`,
  `scripts/dialog_scenarios_report.py`; `frontend: npx vitest run`; browser: `scripts/e2e/*.mjs`
  with playwright-core (PW_MODULE) against the running app.

## Limitations
- Positions are simulated or stated, never measured; floor changes are schematic.
- Phone orientation and microphone not tested on hardware; frame times only from software rendering.
- ElevenLabs quota ran out during the night: spoken hints pause until renewed.
- Rebuild starts from the dataset file; in-session asset moves keep HQ marked outdated.
- Precision routing is not available for every data family.
