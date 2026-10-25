from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app.api.deps import RepositoryDep, SettingsDep
from app.api.schemas import DatasetAvailability, DatasetList, IngestionRequest, IngestionResponse
from app.config import Settings
from app.ingestion import (
    DatasetNotFoundError,
    IngestionSummary,
    dataset_name_taken,
    ingest_dataset,
    list_datasets,
    list_pending_datasets,
    open_dataset,
)
from app.ingestion.service import DATASET_NAME
from app.storage import BuildingRepository

router = APIRouter(tags=["ingestion"])


@router.get("/datasets", response_model=DatasetList)
def get_datasets(settings: SettingsDep) -> DatasetList:
    return DatasetList(
        datasets=list_datasets(settings.datasets_dir),
        needs_reconstruction=list_pending_datasets(settings.datasets_dir),
    )


@router.get("/datasets/{dataset}/availability", response_model=DatasetAvailability)
def get_availability(dataset: str, settings: SettingsDep) -> DatasetAvailability:
    """Whether `dataset` is free for a new upload (`POST .../files?new=true`)."""
    reason = None
    if not DATASET_NAME.match(dataset) or len(dataset) > 200:
        reason = "Use letters, digits, - and _ only, starting with a letter or digit."
    elif dataset_name_taken(settings.datasets_dir, dataset):
        reason = f"A location named '{dataset}' already exists."
    return DatasetAvailability(dataset=dataset, available=reason is None, reason=reason)


def _ingest(
    load: Callable[..., IngestionSummary],
    repository: BuildingRepository,
    settings: Settings,
    dataset: str,
) -> IngestionResponse:
    try:
        summary = load(repository, settings.datasets_dir, dataset)
    except DatasetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Dataset '{dataset}' not found") from None
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Dataset '{dataset}' has an invalid building.json: {exc.error_count()} error(s)",
        ) from exc
    return IngestionResponse(**asdict(summary))


@router.post("/ingestions", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
def create_ingestion(
    body: IngestionRequest, repository: RepositoryDep, settings: SettingsDep
) -> IngestionResponse:
    """(Re)load the dataset's building.json, replacing the live version (edits, closures)."""
    return _ingest(ingest_dataset, repository, settings, body.dataset)


@router.post("/datasets/{dataset}/open", response_model=IngestionResponse)
def open_building(
    dataset: str, repository: RepositoryDep, settings: SettingsDep
) -> IngestionResponse:
    """The dataset's building, loading it only if needed: an open never resets live state."""
    return _ingest(open_dataset, repository, settings, dataset)
