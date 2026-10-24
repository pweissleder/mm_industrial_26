from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from app.agent import ManagedAgentRunner, build_managed_agent_graph
from app.agent.managed import ManagedAgentReply
from app.config import Settings
from app.main import create_app


class FakeRunner(ManagedAgentRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, message: str, session_id: str | None) -> ManagedAgentReply:
        self.calls.append((message, session_id))
        return {"session_id": session_id or "sesn_1", "text": f"managed: {message}"}


async def test_graph_reuses_session_within_thread() -> None:
    runner = FakeRunner()
    graph = build_managed_agent_graph(runner)
    config = {"configurable": {"thread_id": "t1"}}

    await graph.ainvoke({"messages": [HumanMessage("first")]}, config)
    result = await graph.ainvoke({"messages": [HumanMessage("second")]}, config)

    assert runner.calls == [("first", None), ("second", "sesn_1")]
    assert result["session_id"] == "sesn_1"
    assert result["messages"][-1].text == "managed: second"


def test_endpoint_returns_reply(settings: Settings) -> None:
    with TestClient(create_app(settings, managed_agent_runner=FakeRunner())) as client:
        response = client.post("/api/managed-agent/chat", json={"message": "Where is dock B?"})

    assert response.status_code == 200
    assert response.json()["reply"] == "managed: Where is dock B?"


def test_endpoint_unavailable_when_not_configured(client: TestClient) -> None:
    response = client.post("/api/managed-agent/chat", json={"message": "hi"})

    assert response.status_code == 503


def test_health_reports_managed_agent_only_when_runnable(settings: Settings) -> None:
    # IDs alone are not enough: without an API key no runner is created.
    settings.managed_agent_id = "agent_1"
    settings.managed_agent_environment_id = "env_1"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["managed_agent_enabled"] is False
