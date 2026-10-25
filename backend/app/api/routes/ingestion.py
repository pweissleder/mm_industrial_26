from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app.api.deps import RepositoryDep, SettingsDep
from app.api.schemas import DatasetAvailability, DatasetList, IngestionRequest, IngestionResponse
from app.ingestion import (
    DatasetNotFoundError,
    dataset_name_taken,
    ingest_dataset,
    list_datasets,
    list_pending_datasets,
)
from app.ingestion.service import DATASET_NAME

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


@router.post("/ingestions", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
def create_ingestion(
    body: IngestionRequest, repository: RepositoryDep, settings: SettingsDep
) -> IngestionResponse:
    try:
        summary = ingest_dataset(repository, settings.datasets_dir, body.dataset)
    except DatasetNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Dataset '{body.dataset}' not found"
        ) from None
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Dataset '{body.dataset}' has an invalid building.json: {exc.error_count()} error(s)",
        ) from exc
    return IngestionResponse(**asdict(summary))
