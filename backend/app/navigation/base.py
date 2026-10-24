"""Interface between the agent and whatever engine reasons about a building.

The agent only sees a primer, a list of tool schemas and a way to call them, so the
engine behind it (navgraph today) can be swapped without touching the agent or routes.
"""

from collections.abc import Callable
from typing import Any, Protocol

from app.domain.building import BuildingFile


class BuildingNavigator(Protocol):
    def primer(self) -> str:
        """Building facts and rules of conduct for the agent's system prompt."""
        ...

    def tool_schemas(self) -> list[dict[str, Any]]:
        """Anthropic-format tool definitions: name, description, input_schema."""
        ...

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Run a tool and return its JSON result. Never raises; errors come back as JSON."""
        ...

    @property
    def version(self) -> int:
        """Bumped by every edit (closures, added/moved things, new doors)."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """The building as it is now, edits included, in building.json format."""
        ...


NavigatorFactory = Callable[[BuildingFile], BuildingNavigator]
