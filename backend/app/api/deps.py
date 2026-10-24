from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.agent import AgentGraph, ManagedAgentGraph
from app.config import Settings
from app.navigation import NavigatorRegistry
from app.storage import BuildingRepository


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_repository(request: Request) -> BuildingRepository:
    repository: BuildingRepository = request.app.state.building_repository
    return repository


def get_graph(request: Request) -> AgentGraph:
    graph: AgentGraph = request.app.state.graph
    return graph


def get_managed_agent_graph(request: Request) -> ManagedAgentGraph:
    graph: ManagedAgentGraph | None = request.app.state.managed_agent_graph
    if graph is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Managed agent not configured: set ANTHROPIC_API_KEY, MANAGED_AGENT_ID "
            "and MANAGED_AGENT_ENVIRONMENT_ID.",
        )
    return graph


def get_navigators(request: Request) -> NavigatorRegistry:
    navigators: NavigatorRegistry = request.app.state.navigators
    return navigators


RepositoryDep = Annotated[BuildingRepository, Depends(get_repository)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
NavigatorsDep = Annotated[NavigatorRegistry, Depends(get_navigators)]
GraphDep = Annotated[AgentGraph, Depends(get_graph)]
ManagedAgentGraphDep = Annotated[ManagedAgentGraph, Depends(get_managed_agent_graph)]
