"""Structured navigation errors (ported from the prototype's `errors.ModelError`)."""

from typing import Any


class NavigationError(ValueError):
    """A refused navigation call: invalid input, unknown place, unsupported geometry, stale
    revision, ... `code` is machine-readable, `message` user-presentable, `details` structured."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


# Name used by the ported prototype modules.
ModelError = NavigationError

# HTTP status per error code; everything else is 422.
HTTP_STATUS = {
    "unknown_building": 404,
    "not_configured": 404,
    "revision_conflict": 409,
    "source_unavailable": 503,
}
