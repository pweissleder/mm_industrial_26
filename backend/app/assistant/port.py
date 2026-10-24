"""What the assistant needs from navigation: the one authoritative NavigationService.

The assistant never keeps its own building or scenario copy; every fact comes from these calls.
"""

from typing import Any, Protocol

JSON = dict[str, Any]


class NavigationError(Exception):
    """A navigation call was refused: unknown place, no route, unsupported geometry, stale
    revision.
    """

    def __init__(self, code: str, message: str, details: JSON | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


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
