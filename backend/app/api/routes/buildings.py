from typing import Any

import networkx as nx
from fastapi import APIRouter, HTTPException, status

from app.api.deps import RepositoryDep
from app.api.schemas import BuildingDetail, BuildingSummary, FloorOut
from app.domain.building import BuildingFile
from app.storage import BuildingRepository, StoredBuilding
from navgraph.graph import RoomGraph
from navgraph.model import Building

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
def get_building_graph(building_id: str, repository: RepositoryDep) -> dict[str, Any]:
    """The level-1 navgraph RoomGraph as node-link JSON, for the debug graph view."""
    data = _get(repository, building_id).data
    return nx.node_link_data(RoomGraph(Building.from_dict(data.model_dump())).g, edges="edges")
