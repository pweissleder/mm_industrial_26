from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.api.deps import SettingsDep
from app.api.schemas import DatasetFileList, DatasetFileOut, UploadResponse
from app.ingestion import (
    DatasetNotFoundError,
    InvalidUploadError,
    dataset_file,
    list_files,
    save_upload,
)

router = APIRouter(prefix="/datasets/{dataset}/files", tags=["uploads"])


@router.get("", response_model=DatasetFileList)
def get_files(dataset: str, settings: SettingsDep) -> DatasetFileList:
    try:
        files = list_files(settings.datasets_dir, dataset)
    except DatasetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Dataset '{dataset}' not found") from None
    return DatasetFileList(
        dataset=dataset, files=[DatasetFileOut(name=f.name, size_bytes=f.size_bytes) for f in files]
    )


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_files(
    dataset: str, files: Annotated[list[UploadFile], File()], settings: SettingsDep
) -> UploadResponse:
    """Store files in `DATASETS_DIR/<dataset>/`; the dataset is created if it doesn't exist.

    Upload a `building.json` to make the dataset ingestible via `POST /api/ingestions`.
    """
    saved = []
    for upload in files:
        content = await upload.read(settings.max_upload_bytes + 1)
        name = upload.filename or ""
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"'{name}' exceeds {settings.max_upload_bytes // 1_000_000} MB",
            )
        try:
            f = save_upload(settings.datasets_dir, dataset, name, content)
        except InvalidUploadError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"'{name}' is not a valid building.json: {exc.error_count()} error(s)",
            ) from exc
        saved.append(DatasetFileOut(name=f.name, size_bytes=f.size_bytes))
    ingestible = (settings.datasets_dir / dataset / "building.json").is_file()
    return UploadResponse(dataset=dataset, files=saved, ingestible=ingestible)


@router.get("/{filename}", response_class=FileResponse)
def download_file(dataset: str, filename: str, settings: SettingsDep) -> FileResponse:
    try:
        return FileResponse(dataset_file(settings.datasets_dir, dataset, filename))
    except DatasetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{filename}' not found") from None
