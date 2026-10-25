# mm_industrial_26 — Talk to Your Building

Industrial Agents Hackathon 2026, Track 01. Challenge brief and data live in [`problem_statement/`](problem_statement/).

## Stack

| Part | Tech |
|---|---|
| [`frontend/`](frontend/) | Astro 7 + TypeScript (strict), React islands via `@astrojs/react`, Vitest + Testing Library |
| [`backend/`](backend/) | Python 3.11, FastAPI, LangGraph, Claude API (`anthropic` SDK + `langchain-anthropic`), Claude Managed Agents, LangSmith tracing, pytest, ruff, mypy |

```
frontend/  Astro app — pages in src/pages, React components in src/components, API client in src/lib/api.ts
backend/   FastAPI app — app/main.py (factory), app/api (routes, schemas), app/agent (LangGraph graphs),
           app/domain (building model), app/ingestion (building.json import),
           app/storage (storage interface + in-memory store), data/datasets (sample data)
```

Works the same on macOS, Windows and Linux. There are two ways to run it: everything in Docker, or each app natively with npm and uv.

## Configuration

```bash
cp backend/.env.example backend/.env
```

Add `ANTHROPIC_API_KEY` / `LANGSMITH_API_KEY` there. Without `ANTHROPIC_API_KEY` the agent runs in **offline mode** (echoes your message), so the whole stack works without keys. Both ways of running read this file.

## Run with Docker (both apps together)

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with Compose).

```bash
docker compose up --build
```

Stop with Ctrl+C, or `docker compose down` if it runs detached (`-d`). Source folders are mounted into the containers, so code changes reload automatically (file watching uses polling, which is what works reliably on Windows and macOS bind mounts). After changing dependencies, rebuild with `--build` (the frontend container also reinstalls on start when `package-lock.json` no longer matches its `node_modules` volume). Set `FRONTEND_PORT` / `BACKEND_PORT` to use other host ports.

## Run the live version (Docker)

`compose.prod.yaml` builds the frontend statically and serves it with nginx, which also proxies `/api` to the backend (same origin, so no CORS). The backend runs without reload, reads `backend/.env` if present, and keeps `data/datasets` (including uploads) in the named volume `datasets`, seeded with the bundled samples on first start.

```bash
docker compose -f compose.prod.yaml up --build -d
```

Open http://localhost:8080 (change with `ARINAV_PORT`). Ingested buildings are still in memory, but the app re-ingests the selected dataset on load, so a restart loses nothing visible. Stop with `docker compose -f compose.prod.yaml down` (add `-v` to also delete uploads).

## Run natively (each app on its own)

Needs [uv](https://docs.astral.sh/uv/) (installs Python 3.11 from `backend/.python-version`) and Node.js ≥ 22.12. Use two terminals:

```bash
cd backend
uv sync
uv run poe dev
```

```bash
cd frontend
npm ci
npm run dev
```

Backend tasks run through [Poe the Poet](https://poethepoet.natn.io/) (`uv run poe` lists them), the Python counterpart to `npm run`:

| | Backend (`cd backend`) | Frontend (`cd frontend`) |
|---|---|---|
| Dev server with reload | `uv run poe dev` | `npm run dev` |
| Tests | `uv run poe test` | `npm test` |
| Lint + type-check | `uv run poe lint` | `npm run lint` |
| Format | `uv run poe format` | — |
| Live LangSmith test | `uv run poe test-live` | — |

## URLs

- Frontend: http://localhost:4321
- Backend: http://localhost:8000 (OpenAPI docs at `/docs`)

The browser only talks to the Astro dev server: it proxies `/api/*` to FastAPI (`BACKEND_URL`, see `frontend/astro.config.mjs`; in Docker it points at the `backend` service), so there is no CORS in local dev. For split deployments, set `PUBLIC_API_BASE_URL` in the frontend and `CORS_ORIGINS` in the backend.

## AriNav web app

The frontend is **AriNav** (after Ariadne and her thread), one React island on `/` (`frontend/src/components/arinav/`), styled after synera.ai and responsive down to phones:

- **3D view** (centre): three.js renders any ingested `building.json` (room slabs coloured by type, walls with door gaps, assets as boxes by category, stairs/lifts as tubes), or an uploaded `.glb`. Drag to orbit; right-drag, middle-drag, shift-drag or two fingers pan along the ground, and the pan tool in the camera bar makes a plain drag (one finger) pan. Click or tap to inspect: the selection turns magenta (rooms get an outlined volume, assets a box) and the camera flies to it.
- **Location** (left panel): building picker, floor focus (higher floors hidden, lower ones ghosted), search over rooms and assets, room list.
- **View** (left panel): camera presets, layer toggles, explode floors, wall height, room colours, shadows, grid.
- **Chat** (bottom): in *Assistant* mode the Claude building assistant with push-to-talk voice (`/api/assistant`, routes drawn on the map, changes only after *Confirm*); without the assistant API it talks to the agent via `POST /api/chat` `{ message, thread_id? }` (one-node LangGraph agent with per-thread memory, returns `{ reply, thread_id }`).
- **Assistant / Developer** (top bar): *Developer* adds manual routing (official kit 1.1.0 or precision), nearest exit, scenario closures and the kit examples — usable without any AI key. Details, start script and tests: [`docs/PHASE_A_INTEGRATION.md`](docs/PHASE_A_INTEGRATION.md). Live 3D navigation (virtual walker, spoken hints, rerouting): [`docs/PHASE_B_GUIDE.md`](docs/PHASE_B_GUIDE.md).
- **Upload assets** (top right): creates a new location. You name it first (checked live, case-insensitively, against existing datasets), then add files; the backend stores them in `DATASETS_DIR/<dataset>/`, and a `building.json` is ingested and opened right away. Without one, SVG floor plans (`floor_N.svg`) plus `equipment.csv` are enough (PNG/JPG plans alone are not supported yet): the pipeline's first stage reconstructs `building.json` from them, the Low preview shows *Low n/a* until it has, and the location is named after the dataset.
- **Two qualities** (tag next to the building name): every building first shows the **Low** preview generated in the browser. The [ingestion pipeline](#agentic-ingestion-pipeline) runs in the background when started from the *LQ only · Build HQ* chip; with *Developer → HQ model → Auto-build* switched on (off by default, because a build calls Claude when a key is set) it also starts on every upload and on opening a dataset that was never processed (a failed run is not restarted on its own). Once it has published a model, the view switches to **HQ**: the pipeline's `model.glb` with the cleaned `building.json` and room descriptions, still clickable and filterable by floor. The *3D source* toggle switches between HQ and the preview.
- **Processing** (chip in the top bar): stage progress of the run. When the pipeline asks for a review, a dialog opens with the issues, plan previews and a note field, and answers with *Accept with warnings*, *Retry stage* or *Abort run*. Failed runs can be retried and finished ones rebuilt from the same dialog.

On tablets the panel slides over the view; on phones it becomes a bottom sheet.

## Data ingestion and storage

Buildings use the starter kit's format (`building.json`: floors, spaces, openings, vertical connectors, assets; see `problem_statement/.../README.md` § 3). Ingested buildings go through the `BuildingRepository` interface in `backend/app/storage/`. The only implementation today is `InMemoryBuildingRepository`, so **stored buildings are lost when the backend restarts**. To add a persistent store, implement the same three methods (`save`, `get`, `list`) and pass it to `create_app(building_repository=...)`; routes and ingestion don't change.

| Endpoint | What it does |
|---|---|
| `GET /api/datasets` | Lists ingestible datasets (subfolders of `DATASETS_DIR` that contain a `building.json`), plus `needs_reconstruction`: uploads with only SVG floor plans (`floor_N.svg` + `equipment.csv`) whose `building.json` the pipeline's `reconstruct` stage still has to build |
| `POST /api/ingestions` `{"dataset": "proc-factory-dev-003"}` | Validates and stores that dataset; re-ingesting replaces the building |
| `GET /api/buildings`, `GET /api/buildings/{id}` | Ingested buildings, with floors and counts |
| `GET /api/buildings/{id}/model` | The full building (spaces, openings, connectors, assets), used for 3D rendering |
| `GET /api/datasets/{dataset}/availability` | Whether the name is free for a new dataset (`available`, `reason`) |
| `POST /api/datasets/{dataset}/files` (multipart, field `files`) | Stores uploads in `DATASETS_DIR/<dataset>/`, creating the dataset; a `building.json` is validated first. With `?new=true` (used by the app) a taken name returns 409 and a failed upload leaves no folder behind. Allowed: `.json .glb .gltf .png .jpg .svg .csv .pdf .txt .md`, up to `MAX_UPLOAD_BYTES` (50 MB) each |
| `GET /api/datasets/{dataset}/files`, `GET /api/datasets/{dataset}/files/{name}` | Lists or downloads a dataset's files (e.g. a `.glb` for the viewer) |

One sample dataset ships in `backend/data/datasets/` (procedural factory "Fertigung Ost 27", CC0). To ingest any of the kit's 280 buildings without copying them, set `DATASETS_DIR=../problem_statement/industrial-agents-hackathon-2026/data/dev` (or `train`/`test`) in `backend/.env`.

Try it from the OpenAPI docs at http://localhost:8000/docs, or with curl (macOS/Linux shells):

```bash
curl -X POST localhost:8000/api/ingestions -H 'Content-Type: application/json' -d '{"dataset":"proc-factory-dev-003"}'
```

## Agentic ingestion pipeline

`backend/app/pipeline/` turns a dataset into a **location**: a validated and cleaned building, a to-scale 3D model (`model.glb`) and metadata per building, floor and space. A LangGraph graph runs fixed stages. After each stage, an orchestrator gate (Opus) decides whether to proceed, retry the stage that caused a problem, ask a human, or abort.

| # | Stage | Tools (deterministic) | Agent |
|---|---|---|---|
| 1 | Raw validation | 21 issue checks, JSON-patch with re-validation, per-floor translate, overlap trim | Sonnet fixes every open issue in the cleaned copy (the raw file is never touched) or accepts it with a reason; lessons go to `memory/source_quirks.json` |
| 2 | Relationship graph build + validate | — | **skeletons**, implemented later |
| 3.1 | Floorplan | trimesh/shapely: slabs, space floors, walls with door gaps, connectors | Sonnet reviews stats + plan images |
| 3.2 | Equipment | one instanced mesh per asset type from the asset library | Sonnet flags types with missing or implausible models |
| 3.3 | Variant creator | declarative specs (box/cylinder/prism in the unit box), validation, preview | Sonnet designs a spec per missing type; saved to the library and reused across locations |
| 3.4 | 3D validator | GLB re-read and compared node by node (1 mm / 0.1°), plan renders | Opus compares the plans with the dataset's floor images and attributes deviations to a stage |
| 4 | Summarizer | areas, equipment, step-free access per space | Sonnet writes building and floor text; Haiku writes space descriptions |

Without `ANTHROPIC_API_KEY` (or with `--offline`), only the deterministic tools run, so the whole pipeline works and is tested offline. A failed stage then pauses the run for review.

```bash
cd backend
uv run poe ingest proc-factory-dev-003            # asks in the terminal when a run needs review
uv run poe ingest --all --datasets-dir ../problem_statement/industrial-agents-hackathon-2026/data/dev --offline --on-review accept_with_warnings
uv run poe ingest proc-factory-dev-003 --graph-only   # benchmark variant: stages 1-2 only, deterministic
```

**Graph-only mode** (`--graph-only`, or `"mode": "graph"` in the API) is the benchmark variant: raw validation with deterministic, geometry-preserving cleanup, then graph build and validation; no 3D model, no metadata, no model calls. `uv run poe benchmark` ingests every building this way and answers its questions with Claude Haiku 5.5; see [`SUBMISSION/BENCHMARK.md`](SUBMISSION/BENCHMARK.md).

| Endpoint | What it does |
|---|---|
| `POST /api/pipeline-runs` `{"dataset", "force"?, "mode"?}` | Starts a run in the background (202) and returns its `RunRecord`; `mode` is `full` (default) or `graph` |
| `GET /api/pipeline-runs[/{run_id}]` | Run status, per-stage progress, orchestrator decisions, token usage per agent; `?dataset=` filters the list (newest first) |
| `POST /api/pipeline-runs/{run_id}/resume` `{"review_id", "option", "note"?}` | Answers a `needs_review` run. `pending_review` holds the prompt for a frontend: title, message, issues, artifacts, and the options `accept_with_warnings`, `retry` or `abort` |
| `GET /api/locations[/{id}]`, `GET /api/locations/{id}/model.glb` | Locations (`has_model` in the list), their metadata, and the 3D model |
| `GET /api/locations/{id}/building` | The cleaned `building.json` |
| `GET /api/locations/{id}/files/{path}` | An image or model the pipeline wrote, e.g. a review's `3d/previews/plan_floor_0.png` |

State lives as JSON in `backend/data/workspace/` (gitignored):
- `locations/<id>/`: `raw.json`, `building.json` (cleaned), `patches.json`, `issues.json`, `location.json` (pillar status and metadata), `3d/` and `reports/`
- `runs/`
- `asset_library/`: overrides of the committed seeds in `backend/data/asset_library/seed/`
- `memory/`

Re-running an unchanged input is skipped unless you pass `force`.

Paused runs can only be resumed while the backend process is alive, because the LangGraph checkpointer is in memory.

## LangSmith tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` (plus `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` for EU accounts) in `backend/.env`. On startup the backend exports these so every LangGraph run is traced to `LANGSMITH_PROJECT`; `/api/health` shows `tracing_enabled`. Each run is named after its endpoint (`chat`, `managed_agent_chat`) with the conversation's `thread_id` in its metadata, and ingestions are traced as `ingest_dataset`.

`uv run poe test` checks tracing offline with a mocked LangSmith client. `uv run poe test-live` sends one real trace and reads it back from your project.

## Claude Managed Agents

[Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) let Anthropic run the agent loop and host a per-session sandbox (bash, files, code execution). The backend calls them from a LangGraph node (`backend/app/agent/managed.py`); the Managed Agents session ID lives in the graph state, so one conversation thread keeps one session.

The agent and environment are persistent resources: create them **once**, not per request.

```bash
cd backend
uv run poe setup-managed-agent
```

Put the printed `MANAGED_AGENT_ID` and `MANAGED_AGENT_ENVIRONMENT_ID` into `backend/.env`, then use `POST /api/managed-agent/chat` (same body as `/api/chat`). It returns 503 until configured. Running sessions show up in the Claude Console.

## Quality

CI (`.github/workflows/ci.yml`) runs lint, tests and the frontend build on Ubuntu, Windows and macOS, and starts the Docker Compose stack to check that the frontend reaches the backend. `.gitattributes` keeps line endings as LF so Windows checkouts behave the same in containers.
