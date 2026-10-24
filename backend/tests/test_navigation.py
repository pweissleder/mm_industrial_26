import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.agent import AgentContext, build_graph
from app.config import BACKEND_DIR, Settings
from app.domain.building import BuildingFile
from app.main import create_app
from app.navigation import NavgraphNavigator

DATASET = "proc-factory-dev-003"
SAMPLE = BACKEND_DIR / "data" / "datasets" / DATASET / "building.json"


@pytest.fixture(scope="module")
def navigator() -> NavgraphNavigator:
    return NavgraphNavigator(BuildingFile.model_validate_json(SAMPLE.read_bytes()))


class ToolFakeModel(GenericFakeChatModel):
    """Fake chat model that accepts tools and records what it was given."""

    bound_tools: list[str] = []

    def bind_tools(
        self, tools: Sequence[dict[str, Any] | type | Callable[..., Any]], **kwargs: Any
    ) -> Runnable[Any, BaseMessage]:
        self.bound_tools = [t["name"] for t in tools if isinstance(t, dict)]
        return self


def test_navigator_routes_on_sample_building(navigator: NavgraphNavigator) -> None:
    result = json.loads(
        navigator.call("route", {"start": "0.23", "goal": "1.08", "profile": "step_free"})
    )

    assert result["reachable"] is True
    assert result["length_m"] == pytest.approx(59.39)
    assert "Fertigung Ost 27" in navigator.primer()


def test_navigator_hides_file_writes(navigator: NavgraphNavigator) -> None:
    assert "save" not in {t["name"] for t in navigator.tool_schemas()}
    assert json.loads(navigator.call("save", {"path": "/tmp/x.json"})) == {"error": "unknown_tool"}


async def test_agent_calls_navigator_tools(navigator: NavgraphNavigator) -> None:
    model = ToolFakeModel(
        messages=iter(
            [
                AIMessage("", tool_calls=[{"name": "find", "args": {"query": "0.23"}, "id": "c1"}]),
                AIMessage("Office 0.23 is on the ground floor."),
            ]
        )
    )
    graph = build_graph(model)

    result = await graph.ainvoke(
        {"messages": [HumanMessage("Where is 0.23?")]},
        {"configurable": {"thread_id": "t1"}},
        context=AgentContext(navigator=navigator),
    )

    tool_message = result["messages"][2]
    assert isinstance(tool_message, ToolMessage) and "error" not in json.loads(tool_message.text)
    assert result["messages"][-1].text == "Office 0.23 is on the ground floor."
    assert "route" in model.bound_tools


def test_chat_with_unknown_building_is_404(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "Hi", "building_id": "nope"})

    assert response.status_code == 404


def test_chat_with_ingested_building(settings: Settings) -> None:
    model = ToolFakeModel(messages=iter([AIMessage("Hello from the factory.")]))
    with TestClient(create_app(settings, model=model)) as client:
        client.post("/api/ingestions", json={"dataset": DATASET})
        response = client.post("/api/chat", json={"message": "Hi", "building_id": DATASET})

    assert response.json()["reply"] == "Hello from the factory."
    assert "route" in model.bound_tools
