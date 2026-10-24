"""What the assistant needs from navigation: the one authoritative NavigationService.

The assistant never keeps its own building or scenario copy; every fact comes from these calls.
"""

from typing import Any, Protocol

# The navigation core's error class, so `except NavigationError` here catches what the
# NavigationService raises (one class, not a look-alike).
from app.navigation.errors import NavigationError

JSON = dict[str, Any]

__all__ = ["JSON", "NavigationError", "NavigationPort"]


class NavigationPort(Protocol):
    def buildings(self) -> list[JSON]:
        """Ingested buildings: `[{id, name, source, floors}]`."""
        ...

    def building(self, building_id: str) -> JSON:
        """The building in kit format (floors, spaces, openings, vertical_connectors, assets)."""
        ...

    def state(self, building_id: str) -> JSON:
        """`{building_id, revision, closed, support}`."""
        ...

    def search(
        self, building_id: str, query: str, kind: str | None, floor_level: int | None, limit: int
    ) -> list[JSON]: ...

    def nearest(
        self, building_id: str, origin: JSON, category: str, step_free: bool, limit: int
    ) -> list[JSON]: ...

    def route(self, building_id: str, request: JSON) -> JSON: ...

    def preview_closure(
        self, building_id: str, element_ids: list[str], origin: JSON | None, step_free: bool
    ) -> JSON: ...

    def change(
        self, building_id: str, action: str, element_id: str | None, expected_revision: int
    ) -> JSON: ...

    def operation(self, building_id: str, operation: str, arguments: JSON) -> JSON: ...

    def operation_specs(self) -> list[JSON]: ...
