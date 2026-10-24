from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from app.api.deps import RepositoryDep, SettingsDep
from app.api.schemas import DatasetList, IngestionRequest, IngestionResponse
from app.ingestion import DatasetNotFoundError, ingest_dataset, list_datasets

router = APIRouter(tags=["ingestion"])


@router.get("/datasets", response_model=DatasetList)
def get_datasets(settings: SettingsDep) -> DatasetList:
    return DatasetList(datasets=list_datasets(settings.datasets_dir))


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
