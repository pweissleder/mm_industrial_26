"""Navigation API (`/api/nav/...`, docs/PHASE_A_INTERFACES.md) over the one NavigationService.

Errors are `{"error": {"code", "message", "details"}}`: 404 unknown building, 409 stale
`expected_revision`, 422 invalid input / unknown place / no precision route (with
`details.diagnosis`).
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.navigation import NavigationError, NavigationService
from app.navigation.errors import HTTP_STATUS

router = APIRouter(prefix="/nav", tags=["navigation"])
JSON = dict[str, Any]
JSONBody = Annotated[JSON, Body()]


def get_navigation(request: Request) -> NavigationService:
    service: NavigationService | None = getattr(request.app.state, "navigation", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Navigation is not configured")
    return service


NavigationDep = Annotated[NavigationService, Depends(get_navigation)]


def error_response(error: NavigationError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS.get(error.code, 422),
        content={"error": error.as_dict()},
    )


def respond(call: Callable[[], JSON]) -> JSON | JSONResponse:
    try:
        return call()
    except NavigationError as error:
        return error_response(error)


@router.get("/specs", response_model=None)
def operation_specs(navigation: NavigationDep) -> JSON | JSONResponse:
    """The nine benchmark operations and their fields."""
    return respond(lambda: {"operations": navigation.operation_specs()})


@router.get("/examples", response_model=None)
def examples(
    navigation: NavigationDep, split: Annotated[str, Query(pattern="^(dev|train)$")] = "dev"
) -> JSON | JSONResponse:
    """Kit 1.1.0 Train/Dev benchmark questions (404 `not_configured` without the kit checkout)."""
    return respond(lambda: navigation.examples(split))


@router.get("/{building_id}/state", response_model=None)
def state(building_id: str, navigation: NavigationDep) -> JSON | JSONResponse:
    return respond(lambda: navigation.state(building_id))


@router.get("/{building_id}/search", response_model=None)
def search(
    building_id: str,
    navigation: NavigationDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    kind: str | None = None,
    floor_level: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 12,
) -> JSON | JSONResponse:
    def run() -> JSON:
        results = navigation.search(building_id, q, kind, floor_level, limit)
        out: JSON = {"results": results}
        if not results:
            out["did_you_mean"] = navigation.suggestions(building_id, q)
        return out

    return respond(run)


@router.post("/{building_id}/nearest", response_model=None)
def nearest(building_id: str, body: JSONBody, navigation: NavigationDep) -> JSON | JSONResponse:
    """`{from, category, step_free?, limit?}` -> candidates ranked by official walking cost."""
    return respond(
        lambda: navigation.nearest_from(
            building_id,
            body.get("from"),  # type: ignore[arg-type]
            body.get("category"),  # type: ignore[arg-type]
            body.get("step_free", False),
            body.get("limit", 5),
        )
    )


@router.post("/{building_id}/route", response_model=None)
def route(building_id: str, body: JSONBody, navigation: NavigationDep) -> JSON | JSONResponse:
    return respond(lambda: navigation.route(building_id, body))


@router.post("/{building_id}/preview-closure", response_model=None)
def preview_closure(
    building_id: str, body: JSONBody, navigation: NavigationDep
) -> JSON | JSONResponse:
    """`{element_ids, from?, step_free?}` -> rooms becoming unreachable/longer; no change."""
    return respond(
        lambda: navigation.preview_closure(
            building_id,
            body.get("element_ids"),  # type: ignore[arg-type]
            body.get("from"),
            body.get("step_free", False),
        )
    )


@router.post("/{building_id}/scenario", response_model=None)
def scenario(building_id: str, body: JSONBody, navigation: NavigationDep) -> JSON | JSONResponse:
    """`{action: close|open|reset, element_id?, expected_revision}` -> new state (409 if stale)."""
    return respond(
        lambda: navigation.change(
            building_id,
            body.get("action"),  # type: ignore[arg-type]
            body.get("element_id"),
            body.get("expected_revision"),  # type: ignore[arg-type]
        )
    )


@router.post("/{building_id}/operation", response_model=None)
def operation(building_id: str, body: JSONBody, navigation: NavigationDep) -> JSON | JSONResponse:
    """`{operation, arguments}` -> one of the nine benchmark operations incl. `formatted_answer`."""
    return respond(
        lambda: navigation.operation(
            building_id,
            body.get("operation"),  # type: ignore[arg-type]
            body.get("arguments", {}),
        )
    )
