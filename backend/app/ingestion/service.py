"""Ingest a dataset folder (starter-kit format) into building storage."""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from langsmith import traceable

from app.domain.building import BuildingFile
from app.storage import BuildingRepository

DATASET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# Only SVG floor plans can be reconstructed for now; PNG/JPG tracing is switched off.
PLAN_IMAGE = re.compile(r"^floor_(-?\d+)\.svg$", re.I)


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
    # False when `open_dataset` found the building already loaded and left it as it was.
    loaded: bool = True


@dataclass(frozen=True)
class DatasetInputs:
    has_building: bool
    reconstructable: bool  # no building.json, but floor plans to rebuild it from


def dataset_inputs(dataset_dir: Path) -> DatasetInputs:
    has_building = (dataset_dir / "building.json").is_file()
    has_plans = dataset_dir.is_dir() and any(
        p.is_file() and PLAN_IMAGE.match(p.name) for p in dataset_dir.iterdir()
    )
    return DatasetInputs(has_building=has_building, reconstructable=not has_building and has_plans)


def list_datasets(datasets_dir: Path) -> list[str]:
    if not datasets_dir.is_dir():
        return []
    return sorted(p.parent.name for p in datasets_dir.glob("*/building.json"))


def list_pending_datasets(datasets_dir: Path) -> list[str]:
    """Folders without a building.json that the pipeline can reconstruct one for."""
    if not datasets_dir.is_dir():
        return []
    return sorted(
        p.name for p in datasets_dir.iterdir() if p.is_dir() and dataset_inputs(p).reconstructable
    )


def _building_file(datasets_dir: Path, dataset: str) -> Path:
    # The name is validated so it can only ever point at a direct child folder.
    if not DATASET_NAME.match(dataset):
        raise DatasetNotFoundError(dataset)
    path = datasets_dir / dataset / "building.json"
    if not path.is_file():
        raise DatasetNotFoundError(dataset)
    return path


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _dataset_lock(dataset: str) -> threading.Lock:
    """One lock per dataset, so concurrent opens/reloads of it never interleave."""
    if not DATASET_NAME.match(dataset):
        raise DatasetNotFoundError(dataset)
    with _locks_guard:
        return _locks.setdefault(dataset, threading.Lock())


def _summary(dataset: str, data: BuildingFile, *, replaced: bool, loaded: bool) -> IngestionSummary:
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
        loaded=loaded,
    )


@traceable(
    run_type="tool", name="ingest_dataset", process_inputs=lambda i: {"dataset": i["dataset"]}
)
def ingest_dataset(
    repository: BuildingRepository, datasets_dir: Path, dataset: str
) -> IngestionSummary:
    """Validate `<datasets_dir>/<dataset>/building.json` and store it.

    Re-ingesting a building replaces its previous version.
    """
    with _dataset_lock(dataset):
        data = BuildingFile.model_validate_json(_building_file(datasets_dir, dataset).read_bytes())
        replaced = repository.save(dataset, data)
    return _summary(dataset, data, replaced=replaced, loaded=True)


def open_dataset(
    repository: BuildingRepository, datasets_dir: Path, dataset: str
) -> IngestionSummary:
    """The dataset's building, ingesting it only if it is not loaded yet.

    Opening is idempotent: a building another browser already opened keeps its live version
    (asset edits, scenario closures) instead of being reset to `building.json`. An explicit
    reload is `ingest_dataset`.
    """
    with _dataset_lock(dataset):
        loaded = next((b for b in repository.list() if b.dataset == dataset), None)
        if loaded is not None:
            return _summary(dataset, loaded.data, replaced=False, loaded=False)
        data = BuildingFile.model_validate_json(_building_file(datasets_dir, dataset).read_bytes())
        replaced = repository.save(dataset, data)
    return _summary(dataset, data, replaced=replaced, loaded=True)
