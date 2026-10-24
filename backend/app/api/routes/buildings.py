from typing import Any

import networkx as nx
from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import RepositoryDep
from app.api.schemas import BuildingDetail, BuildingSummary, FloorOut
from app.domain.building import BuildingFile
from app.storage import BuildingRepository, StoredBuilding
from navgraph.costs import OrganizerCost
from navgraph.graph import RoomGraph
from navgraph.model import OUTSIDE, Building

router = APIRouter(prefix="/buildings", tags=["buildings"])


def _summary(stored: StoredBuilding) -> BuildingSummary:
    meta = stored.data.building
    return BuildingSummary(
        id=meta.id,
        name=meta.name,
        dataset=stored.dataset,
        source=meta.source,
        license=meta.license,
        ingested_at=stored.ingested_at,
    )


def _get(repository: BuildingRepository, building_id: str) -> StoredBuilding:
    stored = repository.get(building_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Building '{building_id}' not found")
    return stored


@router.get("", response_model=list[BuildingSummary])
def list_buildings(repository: RepositoryDep) -> list[BuildingSummary]:
    return [_summary(b) for b in repository.list()]


@router.get("/{building_id}", response_model=BuildingDetail)
def get_building(building_id: str, repository: RepositoryDep) -> BuildingDetail:
    stored = _get(repository, building_id)
    data = stored.data
    return BuildingDetail(
        **_summary(stored).model_dump(),
        attribution=data.building.attribution,
        floors=[
            FloorOut.model_validate(f.model_dump())
            for f in sorted(data.floors, key=lambda f: f.level)
        ],
        space_count=len(data.spaces),
        asset_count=len(data.assets),
    )


@router.get("/{building_id}/model", response_model=BuildingFile)
def get_building_model(building_id: str, repository: RepositoryDep) -> BuildingFile:
    """The full building (floors, spaces, openings, connectors, assets), e.g. for 3D rendering."""
    return _get(repository, building_id).data


@router.get("/{building_id}/graph")
def get_building_graph(
    building_id: str, repository: RepositoryDep, request: Request
) -> dict[str, Any]:
    """The level-1 navgraph RoomGraph as node-link JSON, for the debug graph view."""
    building = Building.from_dict(_get(repository, building_id).data.model_dump())
    # Closures come from the one scenario (NavigationService), so the overlay matches AriNav.
    navigation = getattr(request.app.state, "navigation", None)
    for element in navigation.closed_elements(building_id) if navigation else []:
        if (
            element in building.spaces
            or element in building.openings
            or element in building.connectors
        ):
            building.set_closed(element, True)
    graph = RoomGraph(building).g
    cost = OrganizerCost()
    for a, b, e in graph.edges(data=True):
        e["cost_m"] = _edge_cost(building, cost, a, b, e)
    return nx.node_link_data(graph, edges="edges")


def _edge_cost(
    building: Building, cost: OrganizerCost, a: str, b: str, e: dict[str, Any]
) -> float | None:
    """Routing length (m) of one hop: anchor -> door -> anchor, or the stair/lift ride."""
    try:
        if e["kind"] == "stairs":
            return cost.stairs(e["rise_m"], e["floors"])
        if e["kind"] == "lift":
            return round(cost.lift_board() + cost.lift_ride(e["rise_m"], e["floors"]), 3)
        door = e["position"]
        return round(
            sum(
                cost.walk(s, s.anchor, door)
                for s in (building.spaces[sid] for sid in (a, b) if sid != OUTSIDE)
            ),
            3,
        )
    except Exception:  # degenerate room geometry: show the edge without a time
        return None
