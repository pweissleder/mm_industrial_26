from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from app.agent import (
    AnthropicManagedAgentRunner,
    ManagedAgentRunner,
    build_graph,
    build_managed_agent_graph,
)
from app.api.routes import (
    buildings,
    chat,
    health,
    ingestion,
    managed_agent,
    pipeline,
    uploads,
)
from app.config import Settings, get_settings
from app.navigation import NavgraphNavigator, NavigatorFactory, NavigatorRegistry
from app.pipeline.runner import PipelineRunner
from app.storage import BuildingRepository, InMemoryBuildingRepository
from app.tracing import configure_tracing


def create_chat_model(settings: Settings) -> BaseChatModel | None:
    if not settings.anthropic_api_key:
        return None
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=SecretStr(settings.anthropic_api_key),
    )


def create_managed_agent_runner(settings: Settings) -> ManagedAgentRunner | None:
    if not (
        settings.anthropic_api_key
        and settings.managed_agent_id
        and settings.managed_agent_environment_id
    ):
        return None
    return AnthropicManagedAgentRunner(
        AsyncAnthropic(api_key=settings.anthropic_api_key),
        agent_id=settings.managed_agent_id,
        environment_id=settings.managed_agent_environment_id,
    )


def create_app(
    settings: Settings | None = None,
    model: BaseChatModel | None = None,
    managed_agent_runner: ManagedAgentRunner | None = None,
    building_repository: BuildingRepository | None = None,
    navigator_factory: NavigatorFactory = NavgraphNavigator,
    pipeline_runner: PipelineRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tracing_enabled = configure_tracing(settings)
        repository = building_repository or InMemoryBuildingRepository()
        app.state.building_repository = repository
        app.state.navigators = NavigatorRegistry(repository, navigator_factory)
        app.state.graph = build_graph(model or create_chat_model(settings))
        runner = managed_agent_runner or create_managed_agent_runner(settings)
        app.state.managed_agent_graph = runner and build_managed_agent_graph(runner)
        app.state.pipeline_runner = pipeline_runner or PipelineRunner.from_settings(
            settings, repository=app.state.building_repository
        )
        yield
        await app.state.pipeline_runner.wait()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(managed_agent.router, prefix="/api")
    app.include_router(ingestion.router, prefix="/api")
    app.include_router(buildings.router, prefix="/api")
    app.include_router(uploads.router, prefix="/api")
    app.include_router(pipeline.router, prefix="/api")
    return app


app = create_app()
