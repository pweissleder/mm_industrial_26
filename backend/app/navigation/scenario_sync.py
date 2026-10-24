"""Keeps the team's navgraph navigators on the one scenario held by `NavigationService`.

The building data has a single source (the BuildingRepository) and so must the scenario: a
closure confirmed in AriNav has to be visible to the navgraph `/chat` agent and the graph overlay,
and the navgraph agent must not keep closures or edits of its own that AriNav never sees. So:

- before every call the navigator's closures are set to the service's current closures;
- the tools that would change the navgraph copy (`set_closed`, `add_door`, `move_asset`, the
  builder tools, `save`) are not offered and refused if called. Changes go through AriNav's
  human-confirmed flow (assistant proposal + Confirm, or the developer panel).
"""

import json
from collections.abc import Callable, Iterable
from typing import Any

from app.domain.building import BuildingFile
from app.navigation.base import BuildingNavigator, NavigatorFactory
from app.navigation.navgraph_engine import NavgraphNavigator

WRITE_TOOLS = frozenset(
    {
        "set_closed",
        "add_door",
        "move_asset",
        "add_floor",
        "add_space",
        "add_entrance",
        "add_connector",
        "save",
    }
)
READ_ONLY_MESSAGE = (
    "This agent is read-only. Closures and edits are made in AriNav and need a person to "
    "confirm them (assistant proposal + Confirm, or the developer panel)."
)


class ScenarioNavgraphNavigator(NavgraphNavigator):
    def __init__(self, data: BuildingFile, closed: Callable[[], Iterable[str]]) -> None:
        super().__init__(data)
        self._closed = closed
        self._tools = [t for t in self._tools if t["name"] not in WRITE_TOOLS]

    def _mirror(self) -> None:
        building = self._bi.b
        want = set(self._closed())
        for element in building.closed - want:
            building.set_closed(element, False)
        for element in want - building.closed:
            try:
                building.set_closed(element, True)
            except KeyError:  # an id navgraph does not model; nothing to mirror
                continue

    def primer(self) -> str:
        self._mirror()
        return super().primer()

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name in WRITE_TOOLS:
            return json.dumps({"error": "read_only", "message": READ_ONLY_MESSAGE})
        self._mirror()
        return super().call(name, args)


def scenario_navigator_factory(closed: Callable[[str], Iterable[str]]) -> NavigatorFactory:
    """A `NavigatorFactory` whose navigators read closures via `closed(building_id)`."""

    def factory(data: BuildingFile) -> BuildingNavigator:
        building_id = data.building.id
        return ScenarioNavgraphNavigator(data, lambda: closed(building_id))

    return factory
