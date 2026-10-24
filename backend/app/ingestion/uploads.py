"""Store uploaded files (building.json, 3D models, floor plans, …) in a dataset folder."""

import re
from dataclasses import dataclass
from pathlib import Path

from app.domain.building import BuildingFile
from app.ingestion.service import DATASET_NAME, DatasetNotFoundError

FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALLOWED_SUFFIXES = frozenset(
    {".json", ".glb", ".gltf", ".png", ".jpg", ".jpeg", ".svg", ".csv", ".pdf", ".txt", ".md"}
)


class InvalidUploadError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetFile:
    name: str
    size_bytes: int


def _dataset_dir(datasets_dir: Path, dataset: str) -> Path:
    # The name is validated so it can only ever point at a direct child folder.
    if not DATASET_NAME.match(dataset):
        raise InvalidUploadError(f"Invalid dataset name '{dataset}'")
    return datasets_dir / dataset


def _file_name(filename: str) -> str:
    # Browsers may send a path; only the last segment is kept.
    name = Path(filename.replace("\\", "/")).name
    if not FILE_NAME.match(name):
        raise InvalidUploadError(f"Invalid file name '{filename}'")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise InvalidUploadError(f"File type of '{name}' is not allowed")
    return name


def save_upload(datasets_dir: Path, dataset: str, filename: str, content: bytes) -> DatasetFile:
    """Write one file to `<datasets_dir>/<dataset>/`, creating the dataset if needed.

    A `building.json` is validated first, so an invalid one never replaces a good one.
    """
    folder = _dataset_dir(datasets_dir, dataset)
    name = _file_name(filename)
    if name == "building.json":
        BuildingFile.model_validate_json(content)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)
    return DatasetFile(name=name, size_bytes=len(content))


def list_files(datasets_dir: Path, dataset: str) -> list[DatasetFile]:
    try:
        folder = _dataset_dir(datasets_dir, dataset)
    except InvalidUploadError:
        raise DatasetNotFoundError(dataset) from None
    if not folder.is_dir():
        raise DatasetNotFoundError(dataset)
    return [
        DatasetFile(name=p.name, size_bytes=p.stat().st_size)
        for p in sorted(folder.iterdir())
        if p.is_file() and not p.name.startswith(".")
    ]


def dataset_file(datasets_dir: Path, dataset: str, filename: str) -> Path:
    try:
        path = _dataset_dir(datasets_dir, dataset) / _file_name(filename)
    except InvalidUploadError:
        raise DatasetNotFoundError(dataset) from None
    if not path.is_file():
        raise DatasetNotFoundError(dataset)
    return path
