from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from langchain_core.messages import HumanMessage

from app.agent import AgentContext
from app.api.deps import GraphDep, NavigatorsDep
from app.api.schemas import ChatRequest, ChatResponse, GuardrailInfo
from app.guardrails import guardrail_of
from app.tracing import graph_config

router = APIRouter(tags=["chat"])


def chat_response(
    result: dict[str, Any], thread_id: str, building_changed: bool = False
) -> ChatResponse:
    """The last message as the reply, flagged when the input guardrail produced it."""
    last = result["messages"][-1]
    guardrail = guardrail_of(last)
    return ChatResponse(
        reply=last.text,
        thread_id=thread_id,
        guardrail=GuardrailInfo.model_validate(guardrail) if guardrail else None,
        building_changed=building_changed,
    )


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
    version = navigator.version if navigator else None
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)]},
        config=graph_config("chat", thread_id, building_id=body.building_id or ""),
        context=AgentContext(navigator=navigator),
    )
    return chat_response(
        result,
        thread_id,
        building_changed=navigator is not None and navigator.version != version,
    )
