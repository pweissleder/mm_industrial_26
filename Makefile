.PHONY: install dev backend frontend test test-live lint format

install: ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm ci

backend: ## Run FastAPI on :8000 with reload
	cd backend && uv run uvicorn app.main:app --reload --port 8000 $$( [ -f .env ] && echo --env-file .env )

frontend: ## Run Astro on :4321 (proxies /api to :8000)
	cd frontend && npm run dev

dev: ## Run backend and frontend together (Ctrl+C stops both)
	@trap 'kill 0' INT TERM EXIT; $(MAKE) backend & $(MAKE) frontend & wait

test: ## Run all tests
	cd backend && uv run pytest
	cd frontend && npm test

test-live: ## Run tests that call real services (LangSmith); needs keys in backend/.env
	cd backend && uv run pytest -m live

lint: ## Lint and type-check both apps
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app scripts
	cd frontend && npm run check

format: ## Auto-format backend code
	cd backend && uv run ruff format . && uv run ruff check --fix .
