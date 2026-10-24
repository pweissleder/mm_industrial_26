from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from langchain_core.messages import HumanMessage

from app.api.deps import GraphDep
from app.api.schemas import ChatRequest, ChatResponse, GuardrailInfo
from app.guardrails import guardrail_of
from app.tracing import graph_config

router = APIRouter(tags=["chat"])


def chat_response(result: dict[str, Any], thread_id: str) -> ChatResponse:
    """The last message as the reply, flagged when the input guardrail produced it."""
    last = result["messages"][-1]
    guardrail = guardrail_of(last)
    return ChatResponse(
        reply=last.text,
        thread_id=thread_id,
        guardrail=GuardrailInfo.model_validate(guardrail) if guardrail else None,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, graph: GraphDep) -> ChatResponse:
    thread_id = body.thread_id or str(uuid4())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)]},
        config=graph_config("chat", thread_id),
    )
    return chat_response(result, thread_id)
