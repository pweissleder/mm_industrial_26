"""Keeps the team's navgraph navigators on the one scenario held by `NavigationService`.

The building data has a single source (the BuildingRepository) and so must the scenario: a closure
made anywhere (AriNav's confirmed proposal, the developer panel, or the navgraph `/chat` agent's
`set_closed`) has to be the same closure for the official router, the assistant, the navgraph
agent, the graph overlay and the served building model. So:

- before every call, `primer()` and `snapshot()` the navigator's closures are set to the service's;
- `set_closed` runs in navgraph (its name resolution and answer) and the resulting change is written
  through to the service (`NavigationService.change`), which stays the authority (revision);
- asset edits (`add_asset`, `move_asset`, `remove_asset`) stay local to the navgraph copy and show
  in the served model; the official router ignores furniture by rule, so routes are unaffected;
- topology edits (`add_door`, the builder tools) and `save` are not offered: they would change the
  building under the official router without it knowing (team decision to revisit).
"""

import json
from typing import Any, Protocol

from app.domain.building import BuildingFile
from app.navigation.base import BuildingNavigator, NavigatorFactory
from app.navigation.errors import NavigationError
from app.navigation.navgraph_engine import NavgraphNavigator

BLOCKED_TOOLS = frozenset(
    {"add_door", "add_floor", "add_space", "add_entrance", "add_connector", "save"}
)
BLOCKED_MESSAGE = (
    "Adding doors, spaces, entrances, connectors or floors is not available here: the official "
    "router would not see them. Closures and things (assets) can be changed."
)


class Scenario(Protocol):
    """The part of NavigationService the mirror needs."""

    def closed_elements(self, building_id: str) -> list[str]: ...

    def state(self, building_id: str) -> dict[str, Any]: ...

    def change(
        self, building_id: str, action: str, element_id: str | None, expected_revision: int
    ) -> dict[str, Any]: ...


class ScenarioNavgraphNavigator(NavgraphNavigator):
    def __init__(self, data: BuildingFile, scenario: Scenario) -> None:
        super().__init__(data)
        self._building_id = data.building.id
        self._scenario = scenario
        self._tools = [t for t in self._tools if t["name"] not in BLOCKED_TOOLS]

    def _mirror(self) -> None:
        building = self._bi.b
        want = set(self._scenario.closed_elements(self._building_id))
        for element in building.closed - want:
            building.set_closed(element, False)
        for element in want - building.closed:
            try:
                building.set_closed(element, True)
            except KeyError:  # an id navgraph does not model; nothing to mirror
                continue

    def _write_through(self, before: set[str]) -> str | None:
        """Push navgraph's closure changes to the service; an error message if refused."""
        after = self._bi.b.closed
        for action, elements in (("close", after - before), ("open", before - after)):
            for element in sorted(elements):
                revision = self._scenario.state(self._building_id)["revision"]
                try:
                    self._scenario.change(self._building_id, action, element, revision)
                except NavigationError as error:
                    return error.message
        return None

    def primer(self) -> str:
        self._mirror()
        return super().primer()

    def snapshot(self) -> dict[str, Any]:
        self._mirror()
        return super().snapshot()

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name in BLOCKED_TOOLS:
            return json.dumps({"error": "not_available", "message": BLOCKED_MESSAGE})
        self._mirror()
        before = set(self._bi.b.closed)
        result = super().call(name, args)
        if name == "set_closed":
            refused = self._write_through(before)
            self._mirror()  # the service's state wins either way
            if refused:
                return json.dumps({"error": "change_refused", "message": refused})
        return result


def scenario_navigator_factory(scenario: Scenario) -> NavigatorFactory:
    """A `NavigatorFactory` whose navigators share `scenario`'s closures (read and write)."""

    def factory(data: BuildingFile) -> BuildingNavigator:
        return ScenarioNavgraphNavigator(data, scenario)

    return factory
