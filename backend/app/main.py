from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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
    assistant,
    buildings,
    chat,
    health,
    ingestion,
    location,
    managed_agent,
    memory,
    navigation,
    pipeline,
    uploads,
)
from app.assistant import Assistant
from app.assistant.voice import ElevenLabsVoice
from app.config import Settings, get_settings
from app.guardrails import AnthropicInputClassifier, InputGuard
from app.navigation import NavigatorFactory, NavigatorRegistry, build_navigation_service
from app.navigation.scenario_sync import scenario_navigator_factory
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


def create_assistant(settings: Settings, navigation_service: Any) -> Assistant:
    """The AriNav assistant over the one NavigationService; offline (no Claude) without a key."""
    transport = None
    if settings.anthropic_api_key:
        from app.assistant.transport import LiveTransport

        transport = LiveTransport(settings.anthropic_api_key)
    return Assistant(
        navigation_service,
        transport,
        model=settings.assistant_model,
        effort=settings.assistant_effort,
    )


def create_voice(settings: Settings) -> ElevenLabsVoice | None:
    if not settings.elevenlabs_api_key:
        return None
    return ElevenLabsVoice(settings.elevenlabs_api_key, settings.elevenlabs_voice_id)


def create_input_guard(settings: Settings) -> InputGuard:
    if not (settings.anthropic_api_key and settings.guardrail_classifier_enabled):
        return InputGuard()
    return InputGuard(
        AnthropicInputClassifier(
            AsyncAnthropic(api_key=settings.anthropic_api_key), model=settings.guardrail_model
        )
    )


def create_app(
    settings: Settings | None = None,
    model: BaseChatModel | None = None,
    managed_agent_runner: ManagedAgentRunner | None = None,
    building_repository: BuildingRepository | None = None,
    navigator_factory: NavigatorFactory | None = None,
    pipeline_runner: PipelineRunner | None = None,
    assistant_transport: Any = None,
    input_guard: InputGuard | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tracing_enabled = configure_tracing(settings)
        repository = building_repository or InMemoryBuildingRepository()
        app.state.building_repository = repository
        # One scenario: NavigationService owns closures/revision; navgraph navigators share it
        # (closures written through, mirrored before every call; see scenario_sync).
        app.state.navigation = build_navigation_service(settings, repository)
        app.state.navigators = NavigatorRegistry(
            repository,
            navigator_factory or scenario_navigator_factory(app.state.navigation, repository),
        )
        app.state.assistant = create_assistant(settings, app.state.navigation)
        if assistant_transport is not None:
            app.state.assistant.transport = assistant_transport
        app.state.voice = create_voice(settings)
        guard = input_guard or create_input_guard(settings)
        app.state.graph = build_graph(model or create_chat_model(settings), guard=guard)
        runner = managed_agent_runner or create_managed_agent_runner(settings)
        app.state.managed_agent_graph = runner and build_managed_agent_graph(runner, guard=guard)
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
    app.include_router(navigation.router, prefix="/api")
    app.include_router(location.router, prefix="/api")
    app.include_router(assistant.router, prefix="/api")
    app.include_router(memory.router, prefix="/api")
    return app


app = create_app()
