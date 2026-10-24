"""LangGraph agent: an assistant node with per-thread memory and, when a building is
selected, a tool loop over that building's navigator."""

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.navigation import BuildingNavigator


@dataclass(frozen=True)
class AgentContext:
    """Per-run context. Without a navigator the agent answers from the conversation alone."""

    navigator: BuildingNavigator | None = None


AgentGraph = CompiledStateGraph[MessagesState, AgentContext, MessagesState, MessagesState]

SYSTEM_PROMPT = (
    "You are the voice of a building. Answer questions about rooms, routes and "
    "equipment concisely and in a friendly tone."
)


def build_graph(
    model: BaseChatModel | None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> AgentGraph:
    """Compile the agent graph.

    With `model=None` the graph runs in offline mode and echoes the user, so the
    stack works end to end without an API key.
    """

    async def assistant(
        state: MessagesState, runtime: Runtime[AgentContext]
    ) -> dict[str, list[AIMessage]]:
        if model is None:
            last = state["messages"][-1].content
            return {"messages": [AIMessage(content=f"(offline mode) You said: {last}")]}
        navigator = runtime.context.navigator if runtime.context else None
        if navigator is None:
            response = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        else:
            prompt = f"{SYSTEM_PROMPT}\n\n{navigator.primer()}"
            response = await model.bind_tools(navigator.tool_schemas()).ainvoke(
                [SystemMessage(prompt), *state["messages"]]
            )
        return {"messages": [response]}

    def tools(state: MessagesState, runtime: Runtime[AgentContext]) -> dict[str, list[ToolMessage]]:
        navigator = runtime.context.navigator
        assert navigator is not None  # only reached when the model was given tools
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        # shortcut: navgraph is sync and fast once built; move to a thread if calls get slow.
        return {
            "messages": [
                ToolMessage(navigator.call(c["name"], c["args"]), tool_call_id=c["id"])
                for c in last.tool_calls
            ]
        }

    def next_step(state: MessagesState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    builder = StateGraph(MessagesState, context_schema=AgentContext)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", tools)
    builder.add_edge(START, "assistant")
    builder.add_conditional_edges("assistant", next_step, ["tools", END])
    builder.add_edge("tools", "assistant")
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
