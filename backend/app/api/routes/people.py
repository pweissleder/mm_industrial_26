"""People and presence (`/api/users`, `/api/user-sessions`, `/api/buildings/{id}/users`).

Choosing a person is passwordless and everyone has the same rights. The session token returned by
`POST /api/user-sessions` is sent back as the `X-AriNav-Session` header; it proves which client
controls a person (and may move them), not who someone is. Errors are
`{"error": {"code", "message", "details"}}`: 404 unknown person/building, 409 person in use,
401 session ended, 422 invalid input.

`WS /api/live?building_id=...` pushes `{"type": "users", "building_id", "users"}` (the same list
as `GET /api/buildings/{id}/users`) on connect and whenever someone there moves, arrives, leaves,
comes online or goes offline; `{"type": "ping"}` keeps idle connections open.
"""

import asyncio
import contextlib
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.presence import PresenceError, PresenceService
from app.presence.live import LiveHub

router = APIRouter(tags=["people"])
JSON = dict[str, Any]
JSONBody = Annotated[JSON, Body()]
SESSION_HEADER = "X-AriNav-Session"
PING_SECONDS = 25.0


def get_presence(request: Request) -> PresenceService:
    presence: PresenceService = request.app.state.presence
    return presence


PresenceDep = Annotated[PresenceService, Depends(get_presence)]
TokenDep = Annotated[str | None, Header(alias=SESSION_HEADER)]


def respond(call: Callable[[], JSON], status_code: int = 200) -> JSON | JSONResponse:
    try:
        result = call()
    except PresenceError as error:
        return JSONResponse(status_code=error.status, content={"error": error.as_dict()})
    return result if status_code == 200 else JSONResponse(status_code=status_code, content=result)


@router.get("/users", response_model=None)
def users(presence: PresenceDep) -> JSON:
    """The selectable people and whether each is in use (occupied) right now."""
    return presence.directory()


@router.post("/user-sessions", response_model=None)
def claim(body: JSONBody, presence: PresenceDep, token: TokenDep = None) -> JSON | JSONResponse:
    """Become `profile_id`. With the header of this client's own valid lease (a reload), the same
    session continues; a person another client holds is refused with 409."""
    return respond(lambda: presence.claim(body.get("profile_id"), token), status.HTTP_201_CREATED)


@router.get("/user-sessions/current", response_model=None)
def current(presence: PresenceDep, token: TokenDep = None) -> JSON | JSONResponse:
    return respond(lambda: presence.current(token))


@router.post("/user-sessions/current/heartbeat", response_model=None)
def heartbeat(presence: PresenceDep, token: TokenDep = None) -> JSON | JSONResponse:
    return respond(lambda: presence.heartbeat(token))


@router.delete("/user-sessions/current", status_code=status.HTTP_204_NO_CONTENT)
def release(presence: PresenceDep, token: TokenDep = None) -> Response:
    """Free the person for others; their last position stays (offline)."""
    presence.release(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/user-sessions/current/position", response_model=None)
def set_position(
    body: JSONBody, presence: PresenceDep, token: TokenDep = None
) -> JSON | JSONResponse:
    """`{building_id, endpoint, label?, heading?, source?, moving?}`: place this session's person
    at a validated endpoint (room, asset, door or a point inside a room)."""
    return respond(lambda: presence.set_position(token, body))


@router.delete("/user-sessions/current/position", response_model=None)
def clear_position(presence: PresenceDep, token: TokenDep = None) -> JSON | JSONResponse:
    """Remove this session's person from the map (an explicit clear, unlike disconnecting)."""
    return respond(lambda: presence.clear_position(token))


@router.get("/buildings/{building_id}/users", response_model=None)
def building_users(building_id: str, presence: PresenceDep) -> JSON:
    """Positioned people of the building, online and offline, with revisions and timestamps."""
    return presence.building_users(building_id)


@router.websocket("/live")
async def live(websocket: WebSocket, building_id: str) -> None:
    """The building's people, pushed on every change (see the module docstring)."""
    presence: PresenceService = websocket.app.state.presence
    hub: LiveHub = websocket.app.state.live
    await websocket.accept()

    async def push() -> None:
        users = await run_in_threadpool(presence.building_users, building_id)
        await websocket.send_json({"type": "users", **users})

    async def drain() -> None:  # the client sends nothing we need; this notices it leaving
        while True:
            await websocket.receive_text()

    with hub.subscribe(building_id) as changes:
        reader = asyncio.create_task(drain())
        try:
            await push()
            while not reader.done():
                try:
                    await asyncio.wait_for(changes.get(), PING_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"type": "ping"})
                    continue
                await push()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await reader
