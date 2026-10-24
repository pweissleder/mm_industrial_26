from uuid import uuid4

from fastapi import APIRouter
from langchain_core.messages import HumanMessage

from app.api.deps import GraphDep
from app.api.schemas import ChatRequest, ChatResponse
from app.tracing import graph_config

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, graph: GraphDep) -> ChatResponse:
    thread_id = body.thread_id or str(uuid4())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)]},
        config=graph_config("chat", thread_id),
    )
    return ChatResponse(reply=result["messages"][-1].text, thread_id=thread_id)
