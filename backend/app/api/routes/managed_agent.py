from uuid import uuid4

from fastapi import APIRouter
from langchain_core.messages import HumanMessage

from app.api.deps import ManagedAgentGraphDep
from app.api.routes.chat import chat_response
from app.api.schemas import ChatRequest, ChatResponse
from app.tracing import graph_config

router = APIRouter(prefix="/managed-agent", tags=["managed-agent"])


@router.post("/chat", response_model=ChatResponse)
async def managed_agent_chat(body: ChatRequest, graph: ManagedAgentGraphDep) -> ChatResponse:
    thread_id = body.thread_id or str(uuid4())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)]},
        config=graph_config("managed_agent_chat", thread_id),
    )
    return chat_response(result, thread_id)
