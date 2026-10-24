"""BuildingNavigator backed by the vendored `navgraph` package (backend/navgraph)."""

import json
from pathlib import Path
from typing import Any

import navgraph
from app.domain.building import BuildingFile
from navgraph.agent import BuildingIntelligence
from navgraph.model import Building

_GUIDE = (Path(navgraph.__file__).parent / "AGENT_GUIDE.md").read_text(encoding="utf-8")

# `save` writes to a file path the model picks, so it is never exposed.
_HIDDEN_TOOLS = frozenset({"save"})


class NavgraphNavigator:
    def __init__(self, data: BuildingFile) -> None:
        self._bi = BuildingIntelligence(Building.from_dict(data.model_dump()))
        self._tools = [t for t in self._bi.tool_schemas() if t["name"] not in _HIDDEN_TOOLS]

    def primer(self) -> str:
        return f"{self._bi.primer()}\n\n{_GUIDE}"

    def tool_schemas(self) -> list[dict[str, Any]]:
        return self._tools

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name in _HIDDEN_TOOLS:
            return json.dumps({"error": "unknown_tool"})
        return self._bi.call_json(name, args)

    @property
    def version(self) -> int:
        return self._bi.b.version

    def snapshot(self) -> dict[str, Any]:
        return self._bi.b.to_dict()
