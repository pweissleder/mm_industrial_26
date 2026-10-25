"""Call Wrapped recaps: the latest or one by id (pending until generated), and a manual trigger."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.assistant.recap import RecapService

router = APIRouter(prefix="/recaps", tags=["recaps"])


def _recaps(request: Request) -> RecapService:
    recaps: RecapService | None = getattr(request.app.state, "recaps", None)
    if recaps is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Recaps not available")
    return recaps


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_recap(request: Request) -> dict[str, Any]:
    """Recap the latest conversations now, without waiting for the configured count."""
    stub = _recaps(request).force()
    if stub is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No conversations to recap yet")
    return stub


@router.get("/latest")
def latest_recap(request: Request) -> dict[str, Any]:
    recap = _recaps(request).memory.recap()
    if recap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recap yet")
    return recap


@router.get("/{recap_id}")
def get_recap(recap_id: str, request: Request) -> dict[str, Any]:
    recap = _recaps(request).memory.recap(recap_id)
    if recap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown recap")
    return recap
