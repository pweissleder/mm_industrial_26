"""Demo LangGraph agent: a single assistant node with per-thread memory."""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

AgentGraph = CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]

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

    async def assistant(state: MessagesState) -> dict[str, list[AIMessage]]:
        if model is None:
            last = state["messages"][-1].content
            return {"messages": [AIMessage(content=f"(offline mode) You said: {last}")]}
        response = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("assistant", assistant)
    builder.add_edge(START, "assistant")
    builder.add_edge("assistant", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
