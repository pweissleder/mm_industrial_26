from threading import Lock

from app.navigation.base import BuildingNavigator, NavigatorFactory
from app.storage import BuildingRepository, StoredBuilding


class NavigatorRegistry:
    """One navigator per stored building, kept across chats so edits (closures, new doors)
    persist; rebuilt when the building is re-ingested."""

    def __init__(self, repository: BuildingRepository, factory: NavigatorFactory) -> None:
        self._repository = repository
        self._factory = factory
        self._cache: dict[str, tuple[StoredBuilding, BuildingNavigator]] = {}
        self._lock = Lock()

    def get(self, building_id: str) -> BuildingNavigator | None:
        stored = self._repository.get(building_id)
        if stored is None:
            return None
        with self._lock:
            cached = self._cache.get(building_id)
            if cached is None or cached[0] is not stored:
                cached = (stored, self._factory(stored.data))
                self._cache[building_id] = cached
        return cached[1]
