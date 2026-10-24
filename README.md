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

Stop with Ctrl+C, or `docker compose down` if it runs detached (`-d`). Source folders are mounted into the containers, so code changes reload automatically (file watching uses polling, which is what works reliably on Windows and macOS bind mounts). After changing dependencies, rebuild with `--build`.

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

## Demo feature

`POST /api/chat` `{ message, thread_id? }` runs a one-node LangGraph agent with per-thread memory and returns `{ reply, thread_id }`. The React island `ChatDemo` (on `/`) checks `GET /api/health` and lets you chat with it.

## Data ingestion and storage

Buildings use the starter kit's format (`building.json`: floors, spaces, openings, vertical connectors, assets; see `problem_statement/.../README.md` § 3). Ingested buildings go through the `BuildingRepository` interface in `backend/app/storage/`. The only implementation today is `InMemoryBuildingRepository`, so **stored buildings are lost when the backend restarts**. To add a persistent store, implement the same three methods (`save`, `get`, `list`) and pass it to `create_app(building_repository=...)`; routes and ingestion don't change.

| Endpoint | What it does |
|---|---|
| `GET /api/datasets` | Lists ingestible datasets (subfolders of `DATASETS_DIR` that contain a `building.json`) |
| `POST /api/ingestions` `{"dataset": "proc-factory-dev-003"}` | Validates and stores that dataset; re-ingesting replaces the building |
| `GET /api/buildings`, `GET /api/buildings/{id}` | Ingested buildings, with floors and counts |

One sample dataset ships in `backend/data/datasets/` (procedural factory "Fertigung Ost 27", CC0). To ingest any of the kit's 280 buildings without copying them, set `DATASETS_DIR=../problem_statement/industrial-agents-hackathon-2026/data/dev` (or `train`/`test`) in `backend/.env`.

Try it from the OpenAPI docs at http://localhost:8000/docs, or with curl (macOS/Linux shells):

```bash
curl -X POST localhost:8000/api/ingestions -H 'Content-Type: application/json' -d '{"dataset":"proc-factory-dev-003"}'
```

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
