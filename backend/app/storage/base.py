"""Storage interface for ingested buildings.

Routes and ingestion depend only on this protocol, so the backing technology
(in-memory today, a real database later) can change without touching them.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.building import BuildingFile


@dataclass(frozen=True)
class StoredBuilding:
    dataset: str
    ingested_at: datetime
    data: BuildingFile

    @property
    def id(self) -> str:
        return self.data.building.id


class StaleBuildingError(RuntimeError):
    """A save based on a version that is no longer the stored one (someone saved meanwhile)."""


class BuildingRepository(Protocol):
    def save(
        self, dataset: str, data: BuildingFile, *, expected: BuildingFile | None = None
    ) -> bool:
        """Store a building, replacing any with the same ID. Returns True if replaced.

        With `expected`, the save only happens while the stored version is still that one
        (compared by identity), else it raises `StaleBuildingError`.
        """
        ...

    def get(self, building_id: str) -> StoredBuilding | None: ...

    def list(self) -> list[StoredBuilding]: ...
