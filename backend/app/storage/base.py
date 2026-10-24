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


class BuildingRepository(Protocol):
    def save(self, dataset: str, data: BuildingFile) -> bool:
        """Store a building, replacing any with the same ID. Returns True if replaced."""
        ...

    def get(self, building_id: str) -> StoredBuilding | None: ...

    def list(self) -> list[StoredBuilding]: ...
