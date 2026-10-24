"""Claude Managed Agents: Anthropic runs the agent loop and hosts its sandbox.

The agent and environment are created once by `scripts/setup_managed_agent.py`;
at runtime we only open sessions against their stored IDs.
"""

from typing import NotRequired, Protocol, TypedDict

from anthropic import AsyncAnthropic
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.guardrails import InputGuard, add_input_guard


class ManagedAgentReply(TypedDict):
    session_id: str
    text: str


class ManagedAgentRunner(Protocol):
    async def run(self, message: str, session_id: str | None) -> ManagedAgentReply: ...


class AnthropicManagedAgentRunner:
    def __init__(self, client: AsyncAnthropic, agent_id: str, environment_id: str) -> None:
        self._client = client
        self._agent_id = agent_id
        self._environment_id = environment_id

    async def run(self, message: str, session_id: str | None) -> ManagedAgentReply:
        if session_id is None:
            session = await self._client.beta.sessions.create(
                agent=self._agent_id, environment_id=self._environment_id
            )
            session_id = session.id

        sessions = self._client.beta.sessions
        parts: list[str] = []
        # Stream-first: open the stream before sending so no early events are missed.
        async with await sessions.events.stream(session_id=session_id) as stream:
            await sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
            )
            async for event in stream:
                if event.type == "agent.message":
                    parts.extend(block.text for block in event.content if block.type == "text")
                elif (
                    event.type in ("agent.tool_use", "agent.mcp_tool_use")
                    and event.evaluated_permission == "ask"
                ):
                    # Nobody approves calls from an HTTP request, so deny paused ones.
                    await sessions.events.send(
                        session_id=session_id,
                        events=[
                            {
                                "type": "user.tool_confirmation",
                                "tool_use_id": event.id,
                                "result": "deny",
                            }
                        ],
                    )
                elif event.type == "session.status_idle":
                    if event.stop_reason.type != "requires_action":
                        break
                elif event.type == "session.status_terminated":
                    break

        return {"session_id": session_id, "text": "".join(parts)}


class ManagedAgentState(MessagesState):
    session_id: NotRequired[str]


ManagedAgentGraph = CompiledStateGraph[
    ManagedAgentState, None, ManagedAgentState, ManagedAgentState
]


def build_managed_agent_graph(
    runner: ManagedAgentRunner,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    guard: InputGuard | None = None,
) -> ManagedAgentGraph:
    """One LangGraph node that delegates the turn to a Managed Agents session.

    The session ID lives in graph state, so each LangGraph thread keeps talking to
    the same Managed Agents session (and its sandbox) across turns. The input `guard`
    runs first, so blocked turns never reach the session.
    """

    async def managed_agent(state: ManagedAgentState) -> dict[str, object]:
        reply = await runner.run(state["messages"][-1].text, state.get("session_id"))
        return {"messages": [AIMessage(content=reply["text"])], "session_id": reply["session_id"]}

    builder = StateGraph(ManagedAgentState)
    builder.add_node("managed_agent", managed_agent)
    add_input_guard(builder, guard or InputGuard(), "managed_agent")
    builder.add_edge("managed_agent", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
