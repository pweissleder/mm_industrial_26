from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from langchain_core.messages import HumanMessage

from app.agent import AgentContext
from app.api.deps import GraphDep, NavigatorsDep
from app.api.schemas import ChatRequest, ChatResponse
from app.tracing import graph_config

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, graph: GraphDep, navigators: NavigatorsDep) -> ChatResponse:
    navigator = None
    if body.building_id is not None:
        navigator = navigators.get(body.building_id)
        if navigator is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Building '{body.building_id}' not found"
            )
    thread_id = body.thread_id or str(uuid4())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)]},
        config=graph_config("chat", thread_id, building_id=body.building_id or ""),
        context=AgentContext(navigator=navigator),
    )
    return ChatResponse(reply=result["messages"][-1].text, thread_id=thread_id)
