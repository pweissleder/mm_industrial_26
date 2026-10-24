"""NavigationService: the one authoritative navigation state per ingested building.

Building data comes from the BuildingRepository (the ingested `BuildingFile`, as a kit-format dict);
the RAM scenario per building is a set of closed element ids plus a `revision`. The AriNav UI, the
assistant tools and voice all call this service. Restarting the backend clears scenarios.

Caches (per building, dropped when the building is re-ingested):
- the official kit 1.1.0 BuildingMap with its per-space walk lengths (closure independent) and its
  graphs per (closures, step_free) - a closure change simply selects another graph key;
- precision room contexts (free geometry + corner visibility graph) per (room, profile, width,
  safety, forced endpoint assets) - closures never change a room's geometry;
- the precision support verdict (footprints checked against the original floor SVGs).
"""

import copy
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import networkx as nx

from app.config import Settings
from app.navigation import kit, location, search
from app.navigation.errors import NavigationError
from app.navigation.official import KIT_COMMIT, KIT_VERSION
from app.navigation.official import building_map as official_map
from app.navigation.ported import challenge, diagnostics, geometry, operations, precision
from app.navigation.ported.validation import validate_footprints
from app.navigation.response import official_response, precision_response
from app.storage import BuildingRepository

JSON = dict[str, Any]
K = TypeVar("K")
V = TypeVar("V")
ENDPOINT_KINDS = ("space", "asset", "opening", "point")
PROFILES = ("stationary", "all", "none")
GRAPH_CACHE_SIZE = 8
CONTEXT_CACHE_SIZE = 400
PRECISION_MAX_SECONDS = 20.0
DOOR = official_map.DOOR_PREFIX


class LRUCache(Generic[K, V]):
    """A small bounded mapping (least recently used entries are dropped)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()


@dataclass
class _Entry:
    building_id: str
    dataset: str
    ingested_at: datetime
    data: JSON
    bmap: Any  # official BuildingMap (long-lived; walks and graph_cache shared by its views)
    revision: int = 0
    closed: set[str] = field(default_factory=set)
    support: JSON | None = None
    precision_cache: LRUCache[tuple[Any, ...], Any] = field(
        default_factory=lambda: LRUCache(CONTEXT_CACHE_SIZE)
    )
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def spaces(self) -> dict[str, JSON]:
        return self.bmap.spaces  # type: ignore[no-any-return]

    @property
    def openings(self) -> dict[str, JSON]:
        return self.bmap.openings  # type: ignore[no-any-return]

    @property
    def connectors(self) -> dict[str, JSON]:
        return self.bmap.connectors  # type: ignore[no-any-return]

    @property
    def assets(self) -> dict[str, JSON]:
        return self.bmap.assets  # type: ignore[no-any-return]

    def element_kind(self, element_id: str) -> str | None:
        if element_id in self.spaces:
            return "space"
        if element_id in self.openings:
            return "opening"
        if element_id in self.connectors:
            return "connector"
        return None


def _bool(value: Any, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise NavigationError("invalid_input", f"{name} must be a boolean")
    return value


def _number(value: Any, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise NavigationError("invalid_input", f"{name} must be a finite number")
    return float(value)


def _ms(began: float) -> float:
    return round((time.perf_counter() - began) * 1000, 2)


class NavigationService:
    def __init__(self, repository: BuildingRepository, datasets_dir: Path | None = None) -> None:
        self.repository = repository
        self.datasets_dir = datasets_dir
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ building access

    def _entry(self, building_id: str) -> _Entry:
        if not isinstance(building_id, str):
            raise NavigationError("invalid_input", "building_id must be a string")
        stored = self.repository.get(building_id)
        if stored is None:
            raise NavigationError(
                "unknown_building",
                f"Building '{building_id}' is not ingested",
                {"building_id": building_id},
            )
        with self._lock:
            entry = self._entries.get(building_id)
            if entry is None or entry.ingested_at != stored.ingested_at:
                data = stored.data.model_dump()
                bmap = challenge.reference_map(data)
                bmap.graph_cache = LRUCache(GRAPH_CACHE_SIZE)
                entry = _Entry(
                    building_id=building_id,
                    dataset=stored.dataset,
                    ingested_at=stored.ingested_at,
                    data=data,
                    bmap=bmap,
                    # Re-ingesting replaces the building: closures dropped, revision moves on.
                    revision=0 if entry is None else entry.revision + 1,
                )
                self._entries[building_id] = entry
            return entry

    def buildings(self) -> list[JSON]:
        """Ingested buildings: `[{id, name, source, dataset, floors: [{id, level, name}]}]`."""
        rows = []
        for stored in self.repository.list():
            data = stored.data
            rows.append(
                {
                    "id": data.building.id,
                    "name": data.building.name,
                    "source": data.building.source,
                    "dataset": stored.dataset,
                    "floors": [
                        {"id": f.id, "level": f.level, "name": f.name}
                        for f in sorted(data.floors, key=lambda f: f.level)
                    ],
                }
            )
        return rows

    def building(self, building_id: str) -> JSON:
        """The building in kit format (a private copy; the scenario lives in `state`)."""
        return copy.deepcopy(self._entry(building_id).data)

    building_dict = building

    def closed_elements(self, building_id: str) -> list[str]:
        """The scenario's closed element ids (cheap; for mirrors such as the navgraph agent)."""
        entry = self._entry(building_id)
        with entry.lock:
            return sorted(entry.closed)

    def state(self, building_id: str) -> JSON:
        entry = self._entry(building_id)
        with entry.lock:
            support = self._support(entry)
            return {
                "building_id": building_id,
                "revision": entry.revision,
                "closed": sorted(entry.closed),
                "support": {
                    "official": True,
                    "precision": bool(support["verified"]),
                    "precision_reason": None if support["verified"] else support.get("reason"),
                },
                "kit": {"version": KIT_VERSION, "commit": KIT_COMMIT},
            }

    def _svg_reader(self, entry: _Entry) -> Callable[[int], bytes | None]:
        def read(level: int) -> bytes | None:
            if self.datasets_dir is None:
                return None
            path = self.datasets_dir / entry.dataset / f"floor_{level}.svg"
            return path.read_bytes() if path.is_file() else None

        return read

    def _support(self, entry: _Entry) -> JSON:
        """Precision routing needs the verified procedural convention and original-SVG proof."""
        if entry.support is None:
            try:
                geometry.check_convention(entry.data)
            except NavigationError as error:
                entry.support = {"verified": False, "reason": error.message}
            else:
                entry.support = validate_footprints(entry.data, self._svg_reader(entry))
        return entry.support

    # ------------------------------------------------------------ input checks

    def _check_endpoint(self, entry: _Entry, spec: Any, name: str) -> JSON:
        """Structure and IDs of an Endpoint; unknown IDs are `unknown_place`."""
        if not isinstance(spec, dict):
            raise NavigationError("invalid_input", f"{name} must be an endpoint object")
        kind = spec.get("kind")
        if kind not in ENDPOINT_KINDS:
            raise NavigationError(
                "invalid_input",
                f"{name}.kind must be one of " + ", ".join(ENDPOINT_KINDS),
                {"kind": kind},
            )
        if kind == "point":
            sid = spec.get("space_id")
            if not isinstance(sid, str) or sid not in entry.spaces:
                raise NavigationError("unknown_place", f"{name}: unknown space_id", {"id": sid})
            return {"kind": "point", "space_id": sid, "position": spec.get("position")}
        ident = spec.get("id")
        pool = {"space": entry.spaces, "asset": entry.assets, "opening": entry.openings}[kind]
        if not isinstance(ident, str) or ident not in pool:
            raise NavigationError(
                "unknown_place", f"{name}: no {kind} with id {ident!r}", {"kind": kind, "id": ident}
            )
        out: JSON = {"kind": kind, "id": ident}
        if kind == "opening" and spec.get("side_space_id") is not None:
            side = spec["side_space_id"]
            sides = [s for s in entry.openings[ident]["space_ids"] if s in entry.spaces]
            if side not in sides:
                raise NavigationError(
                    "invalid_input",
                    f"{name}.side_space_id must be one of the opening's spaces",
                    {"side_space_id": side, "allowed": sides},
                )
            out["side_space_id"] = side
        return out

    def _check_elements(self, entry: _Entry, ids: Any, name: str) -> list[str]:
        if ids is None:
            return []
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise NavigationError("invalid_input", f"{name} must be a list of element ids")
        unknown = [x for x in ids if entry.element_kind(x) is None]
        if unknown:
            raise NavigationError(
                "unknown_place",
                f"Unknown element id(s) in {name}: " + ", ".join(unknown[:5]),
                {"unknown": unknown[:20]},
            )
        return list(dict.fromkeys(ids))

    # ------------------------------------------------------------ search / nearest

    def search(
        self,
        building_id: str,
        query: str,
        kind: str | None = None,
        floor_level: int | None = None,
        limit: int = 12,
    ) -> list[JSON]:
        """Rooms, assets, openings (exits flagged) and connectors matching `query`, best first."""
        entry = self._entry(building_id)
        return search.search(entry.data, query, kind, floor_level, limit, entry.closed)

    def suggestions(self, building_id: str, query: str) -> list[str]:
        return search.suggestions(self._entry(building_id).data, query)

    def _graph(self, entry: _Entry, closed: set[str], step_free: bool) -> Any:
        return challenge.official_graph(challenge._view(entry.bmap, closed), step_free)

    def _origin(self, entry: _Entry, spec: JSON, closed: set[str]) -> JSON:
        resolved: JSON = challenge._endpoint(entry.bmap, spec, "from")
        blocked = challenge._blocked(resolved, closed)
        if blocked is not None:
            raise NavigationError(
                "blocked_by_closure",
                f"The start {resolved['kind']} is closed ({blocked})",
                {"element_id": blocked},
            )
        return resolved

    def walk_costs(
        self, building_id: str, origin: JSON, step_free: bool = False
    ) -> dict[str, float]:
        """Official kit 1.1.0 walking cost (m, 2 decimals) from `origin` to every reachable space
        and door node (`door:<id>`), with the scenario closures."""
        entry = self._entry(building_id)
        spec = self._check_endpoint(entry, origin, "from")
        step_free = _bool(step_free, "step_free")
        with entry.lock:
            resolved = self._origin(entry, spec, entry.closed)
            graph = self._graph(entry, entry.closed, step_free)
            if resolved["node"] not in graph:
                return {}
            costs = nx.single_source_dijkstra_path_length(graph, resolved["node"], weight="weight")
        return {str(node): round(float(cost), 2) for node, cost in costs.items()}

    def nearest(
        self,
        building_id: str,
        origin: JSON,
        category: str,
        step_free: bool = False,
        limit: int = 5,
    ) -> list[JSON]:
        """Candidates of `category` ranked by OFFICIAL walking cost from `origin` (single-source
        Dijkstra on the official graph with closures/step-free); unreachable ones are omitted.
        Categories: `exit`, `room_type:<t>`, `asset_type:<t>`, `query:<words>`."""
        return self.nearest_from(building_id, origin, category, step_free, limit)["results"]

    def nearest_from(
        self,
        building_id: str,
        origin: JSON,
        category: str,
        step_free: bool = False,
        limit: int = 5,
    ) -> JSON:
        entry = self._entry(building_id)
        spec = self._check_endpoint(entry, origin, "from")
        step_free = _bool(step_free, "step_free")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise NavigationError("invalid_input", "limit must be 1..100")
        candidates = self._candidates(entry, category)
        with entry.lock:
            resolved = self._origin(entry, spec, entry.closed)
            graph = self._graph(entry, entry.closed, step_free)
            costs = (
                nx.single_source_dijkstra_path_length(graph, resolved["node"], weight="weight")
                if resolved["node"] in graph
                else {}
            )
        rows = []
        floors = {f["id"]: f for f in entry.data["floors"]}
        for nodes, row in candidates:
            reachable = [(costs[n], n) for n in nodes if n in costs]
            if not reachable:
                continue
            cost, node = min(reachable)
            endpoint = row["endpoint"]
            if row.get("connector_stop"):
                endpoint = {"kind": "space", "id": node}
            rows.append(
                {
                    **row,
                    "endpoint": endpoint,
                    "floor_level": floors.get(row["floor_id"], {}).get("level"),
                    "walk_cost_m": round(float(cost), 2),
                }
            )
        rows.sort(key=lambda r: (r["walk_cost_m"], str(r["name"]), r["id"]))
        for row in rows:
            row.pop("connector_stop", None)
        return {
            "results": rows[:limit],
            "from": {
                k: resolved.get(k) for k in ("kind", "id", "space_id", "floor_id", "position")
            },
            "category": category,
            "step_free": step_free,
            "revision": entry.revision,
            "cost_model": f"official kit {KIT_VERSION} walking cost (m)",
        }

    def _candidates(self, entry: _Entry, category: Any) -> list[tuple[list[str], JSON]]:
        """(graph nodes, result row) per candidate place of a nearest-category."""
        if not isinstance(category, str) or not category.strip():
            raise NavigationError("invalid_input", "category is required")
        name, _, value = category.strip().partition(":")
        name = name.strip().lower()
        value = value.strip()
        spaces = entry.spaces
        out: list[tuple[list[str], JSON]] = []

        def space_row(space: JSON) -> JSON:
            return {
                "kind": "space",
                "id": space["id"],
                "name": space.get("name") or space["id"],
                "type": space.get("room_type"),
                "floor_id": space["floor_id"],
                "space_id": space["id"],
                "space_name": space.get("name"),
                "endpoint": {"kind": "space", "id": space["id"]},
            }

        def asset_row(asset: JSON) -> JSON:
            room = spaces.get(asset["space_id"], {})
            return {
                "kind": "asset",
                "id": asset["id"],
                "name": asset.get("name") or asset["id"],
                "type": asset.get("type"),
                "floor_id": asset["floor_id"],
                "space_id": asset["space_id"],
                "space_name": room.get("name"),
                "endpoint": {"kind": "asset", "id": asset["id"]},
            }

        def opening_row(opening: JSON) -> JSON:
            inside = search.inside_space(opening, spaces)
            return {
                "kind": "opening",
                "id": opening["id"],
                "name": search.opening_name(opening, spaces),
                "type": opening.get("kind"),
                "floor_id": opening["floor_id"],
                "space_id": inside,
                "space_name": spaces.get(inside or "", {}).get("name"),
                "is_exit": search.is_exit(opening),
                "endpoint": {"kind": "opening", "id": opening["id"]},
            }

        def normalised(text: str) -> list[str]:
            base = text.lower().replace(" ", "_").replace("-", "_")
            return [base, base[:-1]] if base.endswith("s") else [base]

        if name == "exit" and not value:
            for opening in entry.openings.values():
                if search.is_exit(opening):
                    out.append(([DOOR + opening["id"]], opening_row(opening)))
        elif name == "room_type" and value:
            for wanted in normalised(value):
                out = [
                    ([s["id"]], space_row(s))
                    for s in spaces.values()
                    if wanted in (s.get("room_type"), s.get("type"))
                ]
                if out:
                    break
        elif name == "asset_type" and value:
            for wanted in normalised(value):
                out = [
                    ([a["space_id"]], asset_row(a))
                    for a in entry.assets.values()
                    if a.get("type") == wanted and a.get("space_id") in spaces
                ]
                if out:
                    break
        elif name == "query" and value:
            for hit in search.search(entry.data, value, None, None, None):
                if hit["kind"] == "room":
                    out.append(([hit["id"]], space_row(spaces[hit["id"]])))
                elif hit["kind"] == "asset" and hit["space_id"] in spaces:
                    out.append(([hit["space_id"]], asset_row(entry.assets[hit["id"]])))
                elif hit["kind"] == "opening":
                    out.append(([DOOR + hit["id"]], opening_row(entry.openings[hit["id"]])))
                elif hit["kind"] == "connector":
                    connector = entry.connectors[hit["id"]]
                    stops = [s for s in connector.get("space_ids") or [] if s in spaces]
                    row = {
                        "kind": "connector",
                        "id": connector["id"],
                        "name": connector.get("name") or connector["id"],
                        "type": connector.get("kind"),
                        "floor_id": (connector.get("floor_ids") or [None])[0],
                        "space_id": stops[0] if stops else None,
                        "endpoint": {"kind": "space", "id": stops[0] if stops else None},
                        "connector_stop": True,
                    }
                    out.append((stops, row))
        else:
            raise NavigationError(
                "invalid_input",
                "category must be exit, room_type:<type>, asset_type:<type> or query:<words>",
                {"category": category},
            )
        return out

    # ------------------------------------------------------------ routing

    def route(self, building_id: str, request: JSON) -> JSON:
        """RouteRequest -> RouteResponse (docs/PHASE_A_INTERFACES.md). Official outcomes (ok,
        unreachable, blocked) are responses; precision failures raise with `details.diagnosis`."""
        began = time.perf_counter()
        entry = self._entry(building_id)
        if not isinstance(request, dict):
            raise NavigationError("invalid_input", "The route request must be an object")
        mode = request.get("mode") or "official"
        if mode not in ("official", "precision"):
            raise NavigationError("invalid_input", "mode must be official or precision")
        start = self._check_endpoint(entry, request.get("start"), "start")
        target = self._check_endpoint(entry, request.get("target"), "target")
        via_raw = request.get("via") or []
        if not isinstance(via_raw, list):
            raise NavigationError("invalid_input", "via must be a list of endpoints")
        via = [self._check_endpoint(entry, v, f"via[{i}]") for i, v in enumerate(via_raw)]
        step_free = _bool(request.get("step_free"), "step_free")
        assume = self._check_elements(entry, request.get("assume_closed"), "assume_closed")
        unofficial = request.get("unofficial") or {}
        if not isinstance(unofficial, dict):
            raise NavigationError("invalid_input", "unofficial must be an object")
        with entry.lock:
            closed = sorted(entry.closed | set(assume))
            if mode == "official":
                response = self._official(entry, start, target, via, step_free, closed, unofficial)
            else:
                if via:
                    raise NavigationError(
                        "invalid_input", "via stops are only supported in official mode"
                    )
                response = self._precision(entry, request, start, target, step_free, closed)
            response["revision"] = entry.revision
        response["closed"] = closed
        response["assume_closed"] = assume
        response["elapsed_ms"] = _ms(began)
        return response

    def _official(
        self,
        entry: _Entry,
        start: JSON,
        target: JSON,
        via: list[JSON],
        step_free: bool,
        closed: list[str],
        unofficial: JSON,
    ) -> JSON:
        result = challenge.official_route(
            entry.bmap,
            {
                "start": start,
                "target": target,
                "via": via,
                "step_free": step_free,
                "closed": closed,
                "forklift": _bool(unofficial.get("forklift"), "unofficial.forklift"),
                "no_production_transit": _bool(
                    unofficial.get("no_production_transit"), "unofficial.no_production_transit"
                ),
            },
        )
        response = official_response(result, entry.data)
        if not response["reachable"]:
            reason = response.get("unreachable_reason") or ""
            if response["status"] == "blocked":
                category, elements = "closed", [response.get("blocked_element_id")]
            elif "closures are lifted" in reason:
                category = "closed"
                elements = [c for c in closed if c in reason]
            elif "step-free" in reason:
                category, elements = "step_free", []
            else:
                category, elements = "unreachable_in_model", []
            response["diagnosis"] = {
                "category": category,
                "summary": reason,
                "elements": [{"id": e} for e in elements if e],
            }
        return response

    def _precision(
        self,
        entry: _Entry,
        request: JSON,
        start: JSON,
        target: JSON,
        step_free: bool,
        closed: list[str],
    ) -> JSON:
        support = self._support(entry)
        if not support["verified"]:
            reason = str(support.get("reason") or "not verified")
            raise NavigationError(
                "geometry_unsupported",
                "Precision routing is not available for this building: " + reason,
                {
                    "status": "unsupported",
                    "diagnosis": {"category": "unsupported", "summary": reason, "elements": []},
                },
            )
        profile = request.get("profile") or "stationary"
        if profile not in PROFILES:
            raise NavigationError("invalid_input", "profile must be stationary, all or none")
        assumptions: list[str] = []
        legacy = {
            "start": self._precision_endpoint(entry, start, closed, assumptions),
            "target": self._precision_endpoint(entry, target, closed, assumptions),
            "profile": profile,
            "width_m": _number(request.get("width_m"), "width_m", 0.6),
            "safety_m": _number(request.get("safety_m"), "safety_m", 0.05),
            "snap_m": _number(request.get("snap_m"), "snap_m", 0.0),
            "step_free": step_free,
            "allow_schematic_vertical": _bool(
                request.get("allow_schematic_vertical"), "allow_schematic_vertical", True
            ),
            "closed": closed,
            "max_seconds": PRECISION_MAX_SECONDS,
        }
        try:
            result = precision.navigate(entry.data, legacy, cache=entry.precision_cache)
        except NavigationError as error:
            raise self._diagnose(entry, legacy, error) from None
        openings = entry.openings
        for role, spec in (("start", start), ("target", target)):
            if spec["kind"] != "opening":
                continue
            # The precision router ends at the validated door approach point (door_side "inner");
            # the straight stub to the door centre was checked by door_side as well.
            centre = [float(v) for v in openings[spec["id"]]["position"][:2]]
            end = result[role]
            inner = end["positions"][0] if role == "start" else end["selected_position"]
            stub = [centre, list(inner)] if role == "start" else [list(inner), centre]
            segment = {
                "floor_id": openings[spec["id"]]["floor_id"],
                "space_id": legacy[role]["space_id"],
                "kind": "validated_door_approach",
                "opening_id": spec["id"],
                "points": stub,
                "length_m": math.dist(stub[0], stub[1]),
                "aperture": None,
                "validation": "door approach stub checked by door_side (walls, obstacles)",
            }
            if role == "start":
                result["segments"].insert(0, segment)
            else:
                result["segments"].append(segment)
            result["length_m"] += segment["length_m"]
            end["position"] = centre
        response = precision_response(result, entry.data, start, target)
        response["assumptions"] = response["assumptions"] + assumptions
        return response

    def _precision_endpoint(
        self, entry: _Entry, spec: JSON, closed: list[str], assumptions: list[str]
    ) -> JSON:
        if spec["kind"] != "opening":
            return dict(spec)
        opening = entry.openings[spec["id"]]
        sides = [s for s in opening["space_ids"] if s in entry.spaces]
        side = spec.get("side_space_id")
        if side is None:
            open_sides = [s for s in sides if s not in closed] or sides
            side = open_sides[0]
            if len(sides) > 1:
                assumptions.append(f"Door {spec['id']} is approached from {side} (no side given).")
        return {"kind": "door", "id": spec["id"], "space_id": side}

    def _diagnose(self, entry: _Entry, legacy: JSON, error: NavigationError) -> NavigationError:
        try:
            diagnosed = diagnostics.annotate_error(entry.data, legacy, error)
        except Exception:  # the original rejection stands (prototype behaviour)
            error.details["diagnosis"] = {
                "category": "unresolved",
                "summary": (
                    "The original rejection stands; a reliable diagnosis could not be produced."
                ),
                "elements": [],
                "diagnostic_failure": True,
            }
            diagnosed = error
        category = (diagnosed.details.get("diagnosis") or {}).get("category")
        diagnosed.details["status"] = {"unsupported": "unsupported", "closed": "blocked"}.get(
            category, "unreachable"
        )
        return diagnosed  # type: ignore[no-any-return]

    # ------------------------------------------------------------ scenario

    def preview_closure(
        self,
        building_id: str,
        element_ids: list[str],
        origin: JSON | None = None,
        step_free: bool = False,
    ) -> JSON:
        """What closing `element_ids` would do (official route model), measured from `origin` or,
        without one, from the building entrances. Never changes the scenario."""
        entry = self._entry(building_id)
        ids = self._check_elements(entry, element_ids, "element_ids")
        if not ids:
            raise NavigationError("invalid_input", "element_ids must name at least one element")
        step_free = _bool(step_free, "step_free")
        spaces = entry.spaces
        spec = self._check_endpoint(entry, origin, "from") if origin is not None else None
        with entry.lock:
            base = set(entry.closed)
            before_graph = self._graph(entry, base, step_free)
            after_graph = self._graph(entry, base | set(ids), step_free)
            if spec is not None:
                resolved = self._origin(entry, spec, base)
                sources = [resolved["node"]]
                measured = (
                    entry.bmap.describe_space(resolved["space_id"])
                    if resolved["kind"] != "opening"
                    else search.opening_name(entry.openings[resolved["id"]], spaces)
                )
            else:
                sources = sorted(
                    {
                        i
                        for o in entry.openings.values()
                        if search.is_exit(o)
                        for i in o["space_ids"]
                        if i in spaces
                    }
                )
                measured = "the building entrances"
            revision = entry.revision

        def costs(graph: Any) -> dict[str, float]:
            present = [x for x in sources if x in graph]
            if not present:
                return {}
            found = nx.multi_source_dijkstra_path_length(graph, present, weight="weight")
            return {str(k): float(v) for k, v in found.items()}

        before, after = costs(before_graph), costs(after_graph)
        rooms = [sid for sid in spaces if sid in before and sid not in ids]
        lost = sorted(
            (sid for sid in rooms if sid not in after),
            key=lambda s: (spaces[s].get("name") or s, s),
        )
        longer = sorted(
            (
                (after[sid] - before[sid], sid)
                for sid in rooms
                if sid in after and after[sid] - before[sid] > 1.0
            ),
            reverse=True,
        )

        def label(element: str) -> str:
            if element in entry.openings:
                return search.opening_name(entry.openings[element], spaces)
            record = spaces.get(element) or entry.connectors.get(element) or {}
            return str(record.get("name") or element)

        return {
            "closed_for_preview": [
                {"id": x, "name": label(x), "kind": entry.element_kind(x)} for x in ids
            ],
            "measured_from": measured,
            "step_free": step_free,
            "rooms_reachable_before": len(rooms),
            "rooms_unreachable_after": len(lost),
            "unreachable_examples": [spaces[x].get("name") or x for x in lost[:8]],
            "unreachable_ids": lost,
            "rooms_longer_by_more_than_1_m": len(longer),
            "largest_detours": [
                {"room": spaces[x].get("name") or x, "id": x, "extra_m": round(d, 1)}
                for d, x in longer[:5]
            ],
            "changed": False,
            "revision": revision,
            "note": "Preview only; nothing was changed. Official route model.",
        }

    def change(
        self,
        building_id: str,
        action: str,
        element_id: str | None,
        expected_revision: int,
    ) -> JSON:
        """Close/open an element or reset the building's scenario; atomic on `expected_revision`
        (a stale revision raises `revision_conflict`, HTTP 409)."""
        entry = self._entry(building_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise NavigationError("invalid_input", "expected_revision must be an integer")
        if action not in ("close", "open", "reset"):
            raise NavigationError("invalid_input", "action must be close, open or reset")
        with entry.lock:
            if expected_revision != entry.revision:
                raise NavigationError(
                    "revision_conflict",
                    "The scenario changed meanwhile; reload the state and try again",
                    {"revision": entry.revision, "expected_revision": expected_revision},
                )
            if action == "reset":
                changed = bool(entry.closed)
                entry.closed.clear()
            else:
                if not isinstance(element_id, str) or entry.element_kind(element_id) is None:
                    raise NavigationError(
                        "unknown_place",
                        "Choose an existing room, door/opening, lift or stairs",
                        {"element_id": element_id},
                    )
                changed = (element_id in entry.closed) != (action == "close")
                if action == "close":
                    entry.closed.add(element_id)
                else:
                    entry.closed.discard(element_id)
            if changed:
                entry.revision += 1
        state = self.state(building_id)
        state["changed"] = changed
        return state

    # ------------------------------------------------------------ benchmark operations

    def operation_specs(self) -> list[JSON]:
        return operations.operation_specs()  # type: ignore[no-any-return]

    def operation(self, building_id: str, operation: str, arguments: JSON) -> JSON:
        """One of the nine benchmark operations on the building with the scenario closures
        (graph operations use the official kit 1.1.0 route model)."""
        entry = self._entry(building_id)
        if not isinstance(arguments, dict):
            raise NavigationError("invalid_input", "arguments must be an object")
        with entry.lock:
            challenge.prime_map_cache(entry.bmap)
            inputs = copy.deepcopy(arguments)
            inputs["_scenario_closed"] = sorted(entry.closed)
            result: JSON = operations.compute(copy.deepcopy(entry.data), operation, inputs)
            result["formatted_answer"] = operations.format_answer(operation, result)
            result["revision"] = entry.revision
        result["scenario_note"] = (
            "RAM closures apply to graph operations; physical obstacles never alter benchmark costs"
        )
        return result

    def examples(self, split: str = "dev") -> JSON:
        """Kit 1.1.0 Train/Dev benchmark questions (only when the kit checkout is configured)."""
        if split not in kit.SPLITS:
            raise NavigationError("invalid_input", "split must be train or dev")
        available, reason = kit.questions_available()
        if not available:
            raise NavigationError("not_configured", reason)
        ingested = {b.id for b in self.repository.list()}
        rows = [{**q, "ingested": q.get("building_id") in ingested} for q in kit.examples(split)]
        return {"split": split, "source": reason, "examples": rows}

    # ------------------------------------------------------------ location

    def resolve_location(self, request: JSON) -> JSON:
        if not isinstance(request, dict):
            raise NavigationError("invalid_input", "The request must be an object")
        entry = self._entry(request.get("building_id"))  # type: ignore[arg-type]
        return location.resolve(entry.data, request)


def build_navigation_service(
    settings: Settings, repository: BuildingRepository
) -> NavigationService:
    """The service for `app.state.navigation` (datasets_dir provides the original floor SVGs)."""
    return NavigationService(repository, settings.datasets_dir)
