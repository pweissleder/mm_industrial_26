from typing import Any

import networkx as nx
from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import NavigatorsDep, RepositoryDep
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


def _live(navigators: NavigatorsDep, building_id: str) -> dict[str, Any]:
    """The building with the agent's edits (closures, moved/added things, new doors)."""
    navigator = navigators.get(building_id)
    if navigator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Building '{building_id}' not found")
    return navigator.snapshot()


@router.get("/{building_id}/model", response_model=BuildingFile)
def get_building_model(building_id: str, navigators: NavigatorsDep) -> dict[str, Any]:
    """The full building with live edits and closures, e.g. for 3D rendering."""
    return _live(navigators, building_id)


@router.get("/{building_id}/graph")
def get_building_graph(
    building_id: str, navigators: NavigatorsDep, request: Request
) -> dict[str, Any]:
    """The level-1 navgraph RoomGraph as node-link JSON, for the debug graph view.

    People in the building are nodes too (`user:<id>`, kind "user"), linked to the room they are
    in by a "person" edge whose `cost_m` is the walk from the room's anchor to them.
    """
    building = Building.from_dict(_live(navigators, building_id))
    graph = RoomGraph(building).g
    cost = OrganizerCost()
    for a, b, e in graph.edges(data=True):
        e["cost_m"] = _edge_cost(building, cost, a, b, e)
    presence = getattr(request.app.state, "presence", None)
    for person in presence.located(building_id) if presence else []:
        space = building.spaces.get(person["space_id"] or "")
        if space is None or space.id not in graph:
            continue
        node = f"user:{person['id']}"
        graph.add_node(
            node,
            kind="user",
            name=person["name"],
            color=person["color"],
            status=person["status"],
            floor_id=person["floor_id"],
            anchor=person["position"],
            connected_to_outside=graph.nodes[space.id].get("connected_to_outside", True),
        )
        try:
            walk = round(cost.walk(space, space.anchor, tuple(person["position"])), 3)
        except Exception:  # degenerate room geometry: the link without a length
            walk = None
        graph.add_edge(
            space.id,
            node,
            key=node,
            kind="person",
            element=node,
            step_free=True,
            synthetic=False,
            closed=False,
            cost_m=walk,
        )
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
