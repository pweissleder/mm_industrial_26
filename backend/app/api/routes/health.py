from fastapi import APIRouter, Request

from app.api.deps import SettingsDep
from app.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        llm_enabled=settings.anthropic_api_key is not None,
        managed_agent_enabled=request.app.state.managed_agent_graph is not None,
        tracing_enabled=request.app.state.tracing_enabled,
    )
