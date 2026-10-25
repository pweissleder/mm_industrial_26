"""Two memories under one prefix.

Opt-in activity memory: sessions, whitelisted events, export, delete and a recap. Only the client
can turn it on (by creating a session). Events have a closed schema: no audio, images or
transcripts; a search intent is at most 120 characters.

Global agent memory: running statistics, raw events and chat history (in-memory, read-only except
for a reset).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.assistant.memory import AgentMemory
from app.config import Settings
from app.memory import MemoryStore, SessionKind, SessionNotFoundError, compute_recap

router = APIRouter(prefix="/memory", tags=["memory"])

SessionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Short = Annotated[str, Field(min_length=1, max_length=80)]


def get_memory_store(request: Request) -> MemoryStore:
    """Created lazily under `<workspace_dir>/memory`; tests may preset `app.state.memory_store`."""
    store: MemoryStore | None = getattr(request.app.state, "memory_store", None)
    if store is None:
        settings: Settings = request.app.state.settings
        store = MemoryStore(settings.workspace_dir / "memory")
        request.app.state.memory_store = store
    return store


StoreDep = Annotated[MemoryStore, Depends(get_memory_store)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoData(_Strict):
    pass


class SearchIntentData(_Strict):
    text: Name


class DestinationData(_Strict):
    name: Name
    kind: Short
    id: Annotated[str, Field(max_length=200)] | None = None


class MovedData(_Strict):
    xy_m: Annotated[float, Field(ge=0, le=10_000, allow_inf_nan=False)]
    floor_id: Annotated[str, Field(min_length=1, max_length=200)]
    floor_name: Name | None = None


class RerouteData(_Strict):
    reason: Short


class ArrivalData(_Strict):
    name: Name
    floor_name: Name | None = None


class SettingData(_Strict):
    key: Annotated[str, Field(min_length=1, max_length=60)]
    value: Annotated[str, Field(max_length=120)] | bool | int | float


class _Event(_Strict):
    session_id: SessionId


class SearchIntentEvent(_Event):
    type: Literal["search_intent"]
    data: SearchIntentData


class DestinationEvent(_Event):
    type: Literal["destination_confirmed"]
    data: DestinationData


class NavEvent(_Event):
    type: Literal["nav_start", "nav_pause", "nav_resume", "nav_stop"]
    data: NoData = Field(default_factory=NoData)


class MovedEvent(_Event):
    type: Literal["moved"]
    data: MovedData


class RerouteEvent(_Event):
    type: Literal["reroute"]
    data: RerouteData


class ArrivalEvent(_Event):
    type: Literal["arrival"]
    data: ArrivalData


class SettingEvent(_Event):
    type: Literal["setting_confirmed"]
    data: SettingData


MemoryEventIn = Annotated[
    SearchIntentEvent
    | DestinationEvent
    | NavEvent
    | MovedEvent
    | RerouteEvent
    | ArrivalEvent
    | SettingEvent,
    Field(discriminator="type"),
]


class SessionCreate(_Strict):
    kind: SessionKind = "real"


class SessionOut(BaseModel):
    session_id: str
    kind: SessionKind
    created_at: str


class EventOut(BaseModel):
    type: str
    at: str
    data: dict[str, Any]


class ExportOut(SessionOut):
    events: list[EventOut]


class RecapOut(BaseModel):
    session_id: str
    kind: SessionKind
    label: str
    synthetic: bool
    created_at: str
    event_count: int
    places_reached: list[str]
    places_reached_count: int
    arrivals: int
    walked_m: float
    floors_visited: list[str]
    favourite_destination: str | None
    navigations_started: int
    navigations_completed: int
    reroutes: int
    simulated_minutes: float


def _not_found(session_id: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Memory session '{session_id}' not found")


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreate, store: StoreDep) -> SessionOut:
    info = store.create_session(body.kind)
    return SessionOut(session_id=info.session_id, kind=info.kind, created_at=info.created_at)


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def add_event(body: MemoryEventIn, store: StoreDep) -> EventOut:
    data = body.data.model_dump(exclude_none=True)
    try:
        event = store.append(body.session_id, body.type, data)
    except SessionNotFoundError:
        raise _not_found(body.session_id) from None
    return EventOut.model_validate(event)


@router.get("/recap", response_model=RecapOut)
def recap(session_id: str, store: StoreDep) -> RecapOut:
    try:
        info, events = store.read(session_id)
    except SessionNotFoundError:
        raise _not_found(session_id) from None
    return RecapOut.model_validate(compute_recap(info, events))


@router.get("/sessions/{session_id}/export", response_model=ExportOut)
def export_session(session_id: str, store: StoreDep) -> ExportOut:
    try:
        info, events = store.read(session_id)
    except SessionNotFoundError:
        raise _not_found(session_id) from None
    return ExportOut(
        session_id=info.session_id,
        kind=info.kind,
        created_at=info.created_at,
        events=[EventOut.model_validate(e) for e in events],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, store: StoreDep) -> Response:
    try:
        store.delete(session_id)
    except SessionNotFoundError:
        raise _not_found(session_id) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Global agent memory ------------------------------------------------------------------------


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
