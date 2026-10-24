from fastapi import APIRouter, HTTPException, status

from app.api.deps import RepositoryDep
from app.api.schemas import BuildingDetail, BuildingSummary, FloorOut
from app.storage import StoredBuilding

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


@router.get("", response_model=list[BuildingSummary])
def list_buildings(repository: RepositoryDep) -> list[BuildingSummary]:
    return [_summary(b) for b in repository.list()]


@router.get("/{building_id}", response_model=BuildingDetail)
def get_building(building_id: str, repository: RepositoryDep) -> BuildingDetail:
    stored = repository.get(building_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Building '{building_id}' not found")
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
