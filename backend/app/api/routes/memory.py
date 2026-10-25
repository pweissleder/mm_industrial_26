"""Global agent memory: running statistics, raw events and chat history (in-memory, read-only
except for a reset)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.assistant.memory import AgentMemory

router = APIRouter(prefix="/memory", tags=["memory"])


def _memory(request: Request) -> AgentMemory:
    assistant = getattr(request.app.state, "assistant", None)
    if assistant is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Assistant not available")
    memory: AgentMemory = assistant.memory
    return memory


@router.get("/stats")
def memory_stats(request: Request) -> dict[str, Any]:
    return _memory(request).stats()


@router.get("/events")
def memory_events(
    request: Request,
    kind: Literal["search", "route"] | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    return _memory(request).recent_events(kind, limit)


@router.get("/messages")
def memory_messages(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=2000),
) -> list[dict[str, Any]]:
    memory = _memory(request)
    return memory.search_messages(q, limit) if q else memory.recent_messages(limit)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def memory_clear(request: Request) -> None:
    _memory(request).clear()
