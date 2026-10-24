"""Ingest a dataset folder (starter-kit format) into building storage."""

import re
from dataclasses import dataclass
from pathlib import Path

from langsmith import traceable

from app.domain.building import BuildingFile
from app.storage import BuildingRepository

DATASET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class DatasetNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class IngestionSummary:
    dataset: str
    building_id: str
    building_name: str
    floors: int
    spaces: int
    openings: int
    vertical_connectors: int
    assets: int
    replaced: bool


def list_datasets(datasets_dir: Path) -> list[str]:
    if not datasets_dir.is_dir():
        return []
    return sorted(p.parent.name for p in datasets_dir.glob("*/building.json"))


def _building_file(datasets_dir: Path, dataset: str) -> Path:
    # The name is validated so it can only ever point at a direct child folder.
    if not DATASET_NAME.match(dataset):
        raise DatasetNotFoundError(dataset)
    path = datasets_dir / dataset / "building.json"
    if not path.is_file():
        raise DatasetNotFoundError(dataset)
    return path


@traceable(
    run_type="tool", name="ingest_dataset", process_inputs=lambda i: {"dataset": i["dataset"]}
)
def ingest_dataset(
    repository: BuildingRepository, datasets_dir: Path, dataset: str
) -> IngestionSummary:
    """Validate `<datasets_dir>/<dataset>/building.json` and store it.

    Re-ingesting a building replaces its previous version.
    """
    data = BuildingFile.model_validate_json(_building_file(datasets_dir, dataset).read_bytes())
    replaced = repository.save(dataset, data)
    meta = data.building

    return IngestionSummary(
        dataset=dataset,
        building_id=meta.id,
        building_name=meta.name,
        floors=len(data.floors),
        spaces=len(data.spaces),
        openings=len(data.openings),
        vertical_connectors=len(data.vertical_connectors),
        assets=len(data.assets),
        replaced=replaced,
    )
