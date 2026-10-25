from datetime import UTC, datetime
from threading import Lock

from app.domain.building import BuildingFile
from app.storage.base import StaleBuildingError, StoredBuilding


class InMemoryBuildingRepository:
    """Process-local store; contents are lost on restart."""

    def __init__(self) -> None:
        self._buildings: dict[str, StoredBuilding] = {}
        self._lock = Lock()

    def save(
        self, dataset: str, data: BuildingFile, *, expected: BuildingFile | None = None
    ) -> bool:
        stored = StoredBuilding(dataset=dataset, ingested_at=datetime.now(UTC), data=data)
        with self._lock:
            current = self._buildings.get(stored.id)
            if expected is not None and (current is None or current.data is not expected):
                raise StaleBuildingError(stored.id)
            replaced = current is not None
            self._buildings[stored.id] = stored
        return replaced

    def get(self, building_id: str) -> StoredBuilding | None:
        return self._buildings.get(building_id)

    def list(self) -> list[StoredBuilding]:
        return sorted(self._buildings.values(), key=lambda b: b.data.building.name)
