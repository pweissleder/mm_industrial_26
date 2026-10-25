"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "mm-industrial-26 API"
    environment: str = "development"

    # Origins allowed to call the API directly (the Astro dev server proxies /api,
    # so this only matters when the frontend talks to the backend cross-origin).
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4321"])

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"

    # Building assistant (app/assistant): Claude tool loop over the navigation service, and
    # ElevenLabs voice. Without the keys the assistant/voice endpoints report "offline".
    assistant_model: str = "claude-sonnet-5-5"
    assistant_effort: str = "medium"
    # Call Wrapped: a recap after every N ended conversations (0 = off), written by this model.
    recap_every_sessions: int = Field(default=5, ge=0)
    recap_model: str = "claude-sonnet-5-5"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    # Input guardrail: rules always run; with an API key this model also classifies
    # each chat message (in scope, off-topic, prompt injection, unsafe).
    guardrail_classifier_enabled: bool = True
    guardrail_model: str = "claude-haiku-5-5"

    # Claude Managed Agents; create both once with scripts/setup_managed_agent.py.
    managed_agent_id: str | None = None
    managed_agent_environment_id: str | None = None

    # Folder whose subfolders are ingestible datasets (one building.json each).
    datasets_dir: Path = BACKEND_DIR / "data" / "datasets"
    # Per-file limit for POST /api/datasets/{dataset}/files.
    max_upload_bytes: int = 50_000_000

    # Multi-user presence: the selectable people (JSON list), the directory of state that must
    # survive restarts (last-known positions; mount it in production), and the session lease.
    profiles_file: Path = BACKEND_DIR / "data" / "people" / "profiles.json"
    state_dir: Path = BACKEND_DIR / "data" / "state"
    user_lease_seconds: float = Field(default=30.0, gt=0)

    # Ingestion pipeline: JSON workspace, asset library seed, and per-role models.
    workspace_dir: Path = BACKEND_DIR / "data" / "workspace"
    asset_library_seed_dir: Path = BACKEND_DIR / "data" / "asset_library" / "seed"
    pipeline_orchestrator_model: str = "claude-opus-5-5"
    pipeline_validator_model: str = "claude-opus-5-5"
    pipeline_worker_model: str = "claude-sonnet-5-5"
    pipeline_summary_model: str = "claude-haiku-5-5"
    pipeline_max_retries: int = 2

    # LangSmith; app.tracing exports these so LangChain/LangGraph pick them up.
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "mm-industrial-26"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    @field_validator(
        "anthropic_api_key",
        "elevenlabs_api_key",
        "managed_agent_id",
        "managed_agent_environment_id",
        "langsmith_api_key",
        mode="before",
    )
    @classmethod
    def _blank_as_none(cls, value: object) -> object:
        # `KEY=` in .env yields "", which should mean "not configured".
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
