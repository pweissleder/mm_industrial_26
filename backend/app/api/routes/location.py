"""`POST /api/location/resolve`: a user location (map click, room id, door QR, simulated) to a
route endpoint (docs/PHASE_A_INTERFACES.md, LOCALIZATION)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.routes.navigation import JSON, JSONBody, NavigationDep, respond

router = APIRouter(prefix="/location", tags=["location"])


@router.post("/resolve", response_model=None)
def resolve(body: JSONBody, navigation: NavigationDep) -> JSON | JSONResponse:
    """`{building_id, source, space_id?, opening_id?, side_space_id?, position?, floor_id?}` ->
    `{status, endpoint, candidates, explanation, simulated}`."""
    return respond(lambda: navigation.resolve_location(body))
