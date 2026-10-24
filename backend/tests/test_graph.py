from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import HumanMessage

from app.agent import build_graph


async def test_graph_remembers_thread_history(fake_model: GenericFakeChatModel) -> None:
    graph = build_graph(fake_model)
    config = {"configurable": {"thread_id": "t1"}}

    await graph.ainvoke({"messages": [HumanMessage("Where is the 3D printer?")]}, config)
    result = await graph.ainvoke({"messages": [HumanMessage("Thanks")]}, config)

    # human, ai, human, ai
    assert len(result["messages"]) == 4
    assert result["messages"][1].text == "The 3D printer is in room 2.04."
