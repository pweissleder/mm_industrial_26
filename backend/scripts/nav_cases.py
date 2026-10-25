"""Reproducible navigation case suite with an independent validity check of the produced routes.

A fixed subset of real buildings (two per family: procedural campus, procedural factory, Swiss
Dwellings, ResPlan, OSM) is ingested into an in-memory repository behind one real
`NavigationService`. A seeded generator (default seed 20261025) builds deliberately combined cases:

1. reachable   - space -> space in official mode;
2. step_free   - step_free=True variants (cross-floor pairs, non-step-free doors);
3. endpoints   - asset / opening (exit, door, door side) / exact point endpoints;
4. closures    - a door, lift/stairs or room on the plain route closed (scenario `change` API or
                 request-level `assume_closed`), then re-routed;
5. invalid     - deliberately unsupported or invalid requests (unknown ids, closed start room,
                 point outside its room, stale revision, precision without support, ...).

The expectation of every routing case comes from an INDEPENDENT model built from building.json
(a breadth-first search over openings and vertical connectors honouring closures and step-free
rules), never from the router. Every returned route is then checked independently:

- consecutive spaces of `space_path` are joined, in order, by the next element of `element_path`,
  an opening whose space_ids contain both (same floor) or a vertical connector serving both (for
  stairs: adjacent stops in level order);
- no closed element (scenario or request) appears in the route; step-free routes contain no
  stairs and no non-step-free opening;
- `length_m` equals the drawn polyline length (XY, per floor) plus the vertical costs computed from
  the floor elevations (stairs 2 m per metre of rise, lift 15 m + 1 m per metre), within
  max(1 %, 0.05 m); the polyline is continuous (connectors bridge floors);
- every polyline sample (<= 0.25 m apart) lies inside, or within 0.3 m of, the union of the path's
  spaces on that floor (shapely);
- start and target are preserved (space/asset/point resolve to the requested space, an opening
  endpoint starts/ends exactly at the door, an exact point start starts exactly at the point).

Run from backend/:
    .venv/Scripts/python scripts/nav_cases.py
    .venv/Scripts/python scripts/nav_cases.py --n 140 --seed 20261025
Outputs docs/night/tests/nav_cases.json and docs/night/tests/nav_cases.md.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:  # allow `python scripts/nav_cases.py`
    sys.path.insert(0, str(BACKEND_DIR))

from app.ingestion.service import ingest_dataset  # noqa: E402
from app.navigation import NavigationService  # noqa: E402
from app.navigation.errors import NavigationError  # noqa: E402
from app.storage import InMemoryBuildingRepository  # noqa: E402

JSON = dict[str, Any]
DEFAULT_SEED = 20261025
DEFAULT_N = 140
DEFAULT_DATASETS = Path(
    os.environ.get(
        "ARINAV_DATASETS_DIR",
        str(Path.home() / "Documents" / "HackathonTransfer" / "SyneraKit-data" / "dev"),
    )
)
DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "night" / "tests"
FAMILIES: dict[str, list[str]] = {
    "proc-campus": ["proc-campus-dev-000", "proc-campus-dev-013"],
    "proc-factory": ["proc-factory-dev-003", "proc-factory-dev-007"],
    "swiss": ["swiss-1785", "swiss-2074"],
    "resplan": ["resplan-1465", "resplan-14509"],
    "osm": ["osm-way360544293", "osm-way27326387"],
}
CATEGORIES = ("reachable", "step_free", "endpoints", "closures", "invalid")
WEIGHTS = {"reachable": 28, "step_free": 16, "endpoints": 22, "closures": 18, "invalid": 16}
OUTSIDE = "outside"
STAIRS_M_PER_RISE = 2.0  # kit route model, recomputed independently from floor elevations
LIFT_FIXED_M = 15.0
LIFT_M_PER_RISE = 1.0
LENGTH_REL = 0.01
LENGTH_ABS = 0.05
INSIDE_TOL_M = 0.3
SAMPLE_M = 0.25
SAME_M = 1e-6


# ------------------------------------------------------------------ independent building model


class Model:
    """The building as plain data plus geometry; nothing here calls the router."""

    def __init__(self, data: JSON, family: str) -> None:
        self.data = data
        self.family = family
        self.id: str = data["building"]["id"]
        self.floors = {f["id"]: f for f in data["floors"]}
        self.spaces = {s["id"]: s for s in data["spaces"]}
        self.openings = {o["id"]: o for o in data["openings"]}
        self.connectors = {c["id"]: c for c in data["vertical_connectors"]}
        self.assets = {a["id"]: a for a in data["assets"] if a.get("space_id") in self.spaces}
        self._bounce: list[list[list[str]]] | None = None
        self.polygons: dict[str, Any] = {}
        for sid, space in self.spaces.items():
            polygon = Polygon(space["polygon"])
            self.polygons[sid] = polygon if polygon.is_valid else polygon.buffer(0)
        self.exits = sorted(o for o, op in self.openings.items() if OUTSIDE in op["space_ids"])
        self.doors = sorted(
            o
            for o, op in self.openings.items()
            if OUTSIDE not in op["space_ids"] and len(self.sides(o)) == 2
        )

    def sides(self, opening_id: str) -> list[str]:
        return [s for s in self.openings[opening_id]["space_ids"] if s in self.spaces]

    def floor_of(self, space_id: str) -> str:
        return str(self.spaces[space_id]["floor_id"])

    def stops(self, connector_id: str) -> list[tuple[str, str]]:
        c = self.connectors[connector_id]
        pairs = [(f, s) for f, s in zip(c["floor_ids"], c["space_ids"], strict=True)]
        return sorted(pairs, key=lambda fs: self.floors[fs[0]]["level"])

    def connector_links(self, connector_id: str) -> list[tuple[str, str]]:
        """Space pairs a connector joins: every pair of lift stops, adjacent stair stops."""
        spaces = [s for _, s in self.stops(connector_id)]
        if self.connectors[connector_id]["kind"] == "lift":
            return list(itertools.combinations(spaces, 2))
        return list(itertools.pairwise(spaces))

    def usable(self, element_id: str, closed: set[str], step_free: bool) -> bool:
        record = self.openings.get(element_id) or self.connectors.get(element_id)
        if record is None or element_id in closed:
            return False
        return bool(record.get("step_free")) or not step_free

    def adjacency(self, closed: set[str], step_free: bool) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {s: set() for s in self.spaces if s not in closed}
        for oid in self.openings:
            if not self.usable(oid, closed, step_free):
                continue
            sides = [s for s in self.sides(oid) if s not in closed]
            for a, b in itertools.combinations(sides, 2):
                adj[a].add(b)
                adj[b].add(a)
        for cid in self.connectors:
            if not self.usable(cid, closed, step_free):
                continue
            for a, b in self.connector_links(cid):
                if a in adj and b in adj:
                    adj[a].add(b)
                    adj[b].add(a)
        return adj

    @staticmethod
    def components(adj: dict[str, set[str]]) -> list[list[str]]:
        seen: set[str] = set()
        out = []
        for root in sorted(adj):
            if root in seen:
                continue
            group, queue = [], deque([root])
            seen.add(root)
            while queue:
                current = queue.popleft()
                group.append(current)
                for nxt in adj[current] - seen:
                    seen.add(nxt)
                    queue.append(nxt)
            out.append(sorted(group))
        return out

    def floor_bounce_spaces(self) -> list[list[list[str]]]:
        """Clusters of same-floor space groups that are connected only through another floor:
        per (floor, building component), the floor-only components when there are several."""
        if self._bounce is None:
            full = self.adjacency(set(), False)
            component = {s: i for i, g in enumerate(self.components(full)) for s in g}
            # Same-floor links only (connectors always join different floors).
            flat = {
                s: {n for n in ns if self.floor_of(n) == self.floor_of(s)} for s, ns in full.items()
            }
            clusters: dict[tuple[str, int], list[list[str]]] = defaultdict(list)
            for group in self.components(flat):
                clusters[(self.floor_of(group[0]), component[group[0]])].append(group)
            self._bounce = [c for _, c in sorted(clusters.items()) if len(c) > 1]
        return self._bounce

    def endpoint_spaces(self, spec: JSON, closed: set[str], step_free: bool) -> set[str] | str:
        """Spaces an endpoint attaches to, or 'blocked' when the endpoint element itself is closed
        (its space, or the door it is)."""
        kind = spec["kind"]
        if kind == "opening":
            oid = spec["id"]
            if oid in closed:
                return "blocked"
            if not self.usable(oid, closed, step_free):
                return set()
            # The door is one node joining all its open sides, whichever side was named.
            return {s for s in self.sides(oid) if s not in closed}
        sid = spec["space_id"] if kind == "point" else spec["id"]
        if kind == "asset":
            sid = self.assets[spec["id"]]["space_id"]
        return "blocked" if sid in closed else {sid}

    def verdict(self, start: JSON, target: JSON, closed: set[str], step_free: bool) -> str:
        """Independent expectation: reachable / unreachable / blocked (BFS over spaces)."""
        a = self.endpoint_spaces(start, closed, step_free)
        b = self.endpoint_spaces(target, closed, step_free)
        if a == "blocked" or b == "blocked":
            return "blocked"
        assert isinstance(a, set) and isinstance(b, set)
        if not a or not b:
            return "unreachable"
        if a & b:
            return "reachable"
        adj = self.adjacency(closed, step_free)
        seen, queue = set(a), deque(a)
        while queue:
            current = queue.popleft()
            if current in b:
                return "reachable"
            for nxt in adj.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return "unreachable"

    def random_point(self, rng: random.Random, space_id: str, margin: float = 0.3) -> list[float]:
        polygon = self.polygons[space_id]
        inner = polygon.buffer(-margin)
        if inner.is_empty:
            inner = polygon.buffer(-0.05)
        area = inner if not inner.is_empty else polygon
        x0, y0, x1, y1 = area.bounds
        for _ in range(400):
            p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
            if area.contains(Point(p)):
                return [round(p[0], 3), round(p[1], 3)]
        rp = polygon.representative_point()
        return [round(rp.x, 3), round(rp.y, 3)]


# ------------------------------------------------------------------ independent route check


def _vertical_cost(model: Model, connector_id: str, a: str, b: str) -> float:
    rise = abs(
        float(model.floors[model.floor_of(b)]["elevation_m"])
        - float(model.floors[model.floor_of(a)]["elevation_m"])
    )
    if model.connectors[connector_id]["kind"] == "lift":
        return LIFT_FIXED_M + LIFT_M_PER_RISE * rise
    return STAIRS_M_PER_RISE * rise


def _joins(model: Model, element: str, a: str, b: str) -> bool:
    if element in model.openings:
        sides = model.sides(element)
        return a in sides and b in sides and model.floor_of(a) == model.floor_of(b)
    if element in model.connectors:
        return (a, b) in model.connector_links(element) or (b, a) in model.connector_links(element)
    return False


def _finite_xy(p: Any) -> bool:
    return (
        isinstance(p, list | tuple)
        and len(p) >= 2
        and all(isinstance(v, int | float) and math.isfinite(v) for v in p[:2])
    )


def check_route(
    model: Model,
    request: JSON,
    response: JSON,
    closed: set[str],
    skip_length: bool = False,
) -> tuple[list[JSON], JSON]:
    """Independent validity checks of a reachable official route. Returns (failures, metrics)."""
    fails: list[JSON] = []
    metrics: JSON = {}

    def fail(check: str, detail: Any) -> None:
        fails.append({"check": check, "detail": detail})

    step_free = bool(request.get("step_free"))
    start, target = request["start"], request["target"]
    space_path: list[str] = list(response.get("space_path") or [])
    element_path: list[str] = list(response.get("element_path") or [])
    if response.get("status") != "ok" or response.get("reachable") is not True:
        fail("status", {"status": response.get("status"), "reachable": response.get("reachable")})
    if not space_path:
        fail("space_path", "empty space_path on a reachable route")
        return fails, metrics
    unknown = [s for s in space_path if s not in model.spaces]
    if unknown:
        fail("space_path", {"unknown_spaces": unknown})
        return fails, metrics

    # 1. element-by-element adjacency of the space path
    elements = list(element_path)
    vertical_cost = 0.0
    for a, b in itertools.pairwise(space_path):
        if not elements:
            fail("adjacency", {"pair": [a, b], "problem": "element_path exhausted"})
            break
        element = elements.pop(0)
        if not _joins(model, element, a, b):
            fail("adjacency", {"pair": [a, b], "element": element, "problem": "does not join"})
            continue
        if element in model.connectors:
            vertical_cost += _vertical_cost(model, element, a, b)
    if target["kind"] == "opening":
        if elements != [target["id"]]:
            fail("adjacency", {"trailing_elements": elements, "expected": [target["id"]]})
        elif space_path[-1] not in model.sides(target["id"]):
            fail("adjacency", {"target_door_not_on_last_space": target["id"]})
    elif elements:
        fail("adjacency", {"trailing_elements": elements})
    metrics["vertical_cost_m"] = round(vertical_cost, 3)

    # 2. closures / step-free
    used = set(space_path) | set(element_path)
    if start["kind"] == "opening":
        used.add(start["id"])
    reported_closed = set(response.get("closed") or [])
    if not closed <= reported_closed:
        fail("closures", {"missing_in_response_closed": sorted(closed - reported_closed)})
    through = sorted(used & (closed | reported_closed))
    if through:
        fail("closures", {"route_uses_closed": through})
    if step_free:
        stairs = [e for e in used if model.connectors.get(e, {}).get("kind") == "stairs"]
        steps = [e for e in used if e in model.openings and not model.openings[e]["step_free"]]
        stairs += [
            t.get("element_id")
            for t in response.get("transitions") or []
            if t.get("kind") == "stairs" and t.get("element_id") not in stairs
        ]
        if stairs or steps:
            fail("step_free", {"stairs": sorted(stairs), "non_step_free_openings": sorted(steps)})
        if (response.get("constraints") or {}).get("step_free") is not True:
            fail("step_free", "constraints.step_free not echoed")

    # 3. geometry: continuity, finite points, length
    path = response.get("path") or []
    walk_m = 0.0
    previous: tuple[str, list[float]] | None = None
    for index, entry in enumerate(path):
        points = entry.get("points") or []
        if not all(_finite_xy(p) for p in points):
            fail("finite", {"path_index": index})
            continue
        floor = entry.get("floor_id")
        if floor not in model.floors:
            fail("floors", {"path_index": index, "floor_id": floor})
        if entry.get("kind") == "connector":
            if previous and previous[0] == entry["from_floor_id"]:
                gap = math.dist(previous[1], points[0])
                if gap > SAME_M:
                    fail("continuity", {"path_index": index, "gap_m": round(gap, 4)})
            previous = (entry["to_floor_id"], list(entry["to_position"]))
            continue
        if previous and previous[0] == floor and points:
            gap = math.dist(previous[1], points[0])
            if gap > SAME_M:
                fail("continuity", {"path_index": index, "gap_m": round(gap, 4)})
        elif previous and previous[0] != floor:
            fail("continuity", {"path_index": index, "floor_jump_without_connector": True})
        for p, q in itertools.pairwise(points):
            walk_m += math.dist(p[:2], q[:2])
        if points:
            previous = (floor, list(points[-1]))
    metrics["drawn_walk_m"] = round(walk_m, 3)
    length = response.get("length_m")
    if not isinstance(length, int | float) or not math.isfinite(length):
        fail("length", {"length_m": length})
    elif not skip_length:
        expected = walk_m + vertical_cost
        tol = max(LENGTH_REL * expected, LENGTH_ABS)
        metrics["length_delta_m"] = round(float(length) - expected, 4)
        if abs(float(length) - expected) > tol:
            detail = {
                "length_m": length,
                "drawn_walk_m": round(walk_m, 3),
                "vertical_cost_m": round(vertical_cost, 3),
                "delta_m": round(float(length) - expected, 3),
            }
            notes = [w for w in response.get("warnings") or [] if "No clear walk" in w]
            if notes:
                detail["router_warnings"] = notes[:3]
            fail("length", detail)
    for w in response.get("waypoints") or []:
        if not _finite_xy(w.get("position")):
            fail("finite", {"waypoint": w.get("index")})
            break

    # 4. polyline inside the union of the path's spaces on its floor
    by_floor: dict[str, list[str]] = defaultdict(list)
    for sid in space_path:
        by_floor[model.floor_of(sid)].append(sid)
    areas = {
        f: prep(unary_union([model.polygons[s] for s in sids]).buffer(INSIDE_TOL_M))
        for f, sids in by_floor.items()
    }
    outside: list[JSON] = []
    for index, entry in enumerate(path):
        if entry.get("kind") == "connector":
            continue
        floor = entry.get("floor_id")
        area = areas.get(floor)
        points = [p[:2] for p in entry.get("points") or [] if _finite_xy(p)]
        if area is None:
            outside.append({"path_index": index, "floor_not_on_space_path": floor})
            continue
        if not points:
            outside.append({"path_index": index, "problem": "walk entry without points"})
            continue
        pairs = itertools.pairwise(points) if len(points) > 1 else [(points[0], points[0])]
        for p, q in pairs:
            bad = _first_outside(area, p, q)
            if bad is not None:
                outside.append(
                    {"path_index": index, "space_id": entry.get("space_id"), "point": bad}
                )
                break
        if len(outside) >= 5:
            break
    if outside:
        fail("inside", outside[:5])

    # 4b. the waypoints (what the UI animates): same rules on the joined polyline
    check_waypoints(model, response, space_path, areas, skip_length, fail, metrics)

    # 5. endpoints preserved
    fail_endpoint(model, "start", start, response, space_path, fail)
    fail_endpoint(model, "target", target, response, space_path, fail)
    return fails, metrics


def _first_outside(area: Any, p: list[float], q: list[float]) -> list[float] | None:
    """First sample (<= SAMPLE_M apart) of segment p-q not covered by `area`, else None."""
    length = math.dist(p, q)
    line = LineString([p, q]) if length > SAME_M else None
    count = max(1, math.ceil(length / SAMPLE_M))
    for k in range(count + 1):
        sample = line.interpolate(k / count, normalized=True) if line else Point(p)
        if not area.covers(sample):
            return [round(sample.x, 3), round(sample.y, 3)]
    return None


def check_waypoints(
    model: Model,
    response: JSON,
    space_path: list[str],
    areas: dict[str, Any],
    skip_length: bool,
    fail: Any,
    metrics: JSON,
) -> None:
    """Waypoints joined in order: XY length per floor + vertical cost from elevations equals
    length_m; every joined same-floor segment stays in the path's spaces; the floor sequence is
    the space path's; the first/last waypoint lie in the start/target space."""
    points = [w for w in response.get("waypoints") or [] if _finite_xy(w.get("position"))]
    if not points:
        fail("waypoints", "no waypoints on a reachable route")
        return
    xy = vertical = 0.0
    outside: list[JSON] = []
    for a, b in itertools.pairwise(points):
        if a["floor_id"] == b["floor_id"]:
            xy += math.dist(a["position"][:2], b["position"][:2])
            area = areas.get(a["floor_id"])
            bad = (
                _first_outside(area, a["position"][:2], b["position"][:2])
                if area is not None
                else a["position"][:2]
            )
            if bad is not None and len(outside) < 5:
                outside.append({"from_index": a.get("index"), "point": bad})
            continue
        # The departure waypoint carries the connector boarded there (an arrival point that is
        # also the next departure carries the next connector).
        connector = model.connectors.get(a.get("element_id") or b.get("element_id") or "")
        if connector is None:
            fail("waypoint_floors", {"floor_change_without_connector": b.get("index")})
            continue
        rise = abs(
            float(model.floors[b["floor_id"]]["elevation_m"])
            - float(model.floors[a["floor_id"]]["elevation_m"])
        )
        lift = connector["kind"] == "lift"
        vertical += LIFT_FIXED_M + LIFT_M_PER_RISE * rise if lift else STAIRS_M_PER_RISE * rise
    if outside:
        fail("waypoint_inside", outside)
    metrics["waypoint_xy_m"] = round(xy, 3)
    length = response.get("length_m")
    if not skip_length and isinstance(length, int | float):
        expected = xy + vertical
        if abs(float(length) - expected) > max(LENGTH_REL * expected, LENGTH_ABS):
            detail = {
                "length_m": length,
                "waypoint_xy_m": round(xy, 3),
                "vertical_cost_m": round(vertical, 3),
                "delta_m": round(float(length) - expected, 3),
            }
            fail("waypoint_length", detail)
    floors = [k for k, _ in itertools.groupby(w["floor_id"] for w in points)]
    path_floors = [k for k, _ in itertools.groupby(model.floor_of(s) for s in space_path)]
    if floors != path_floors:
        fail("waypoint_floors", {"waypoints": floors, "space_path": path_floors})
    for role, w, sid in (
        ("start", points[0], space_path[0]),
        ("target", points[-1], space_path[-1]),
    ):
        polygon = model.polygons[sid].buffer(INSIDE_TOL_M)
        if w["floor_id"] != model.floor_of(sid) or not polygon.covers(Point(w["position"][:2])):
            fail(
                "endpoint",
                {
                    role: "first/last waypoint not in the route's end space",
                    "space": sid,
                    "waypoint": {"floor_id": w["floor_id"], "position": w["position"]},
                },
            )


def _first_last_point(response: JSON, role: str) -> list[float] | None:
    points = [w["position"] for w in response.get("waypoints") or []]
    if not points:
        return None
    return list(points[0] if role == "start" else points[-1])


def fail_endpoint(
    model: Model, role: str, spec: JSON, response: JSON, space_path: list[str], fail: Any
) -> None:
    out = response.get(role) or {}
    end_space = space_path[0] if role == "start" else space_path[-1]
    kind = spec["kind"]
    if out.get("kind") != kind:
        fail("endpoint", {role: "kind changed", "requested": kind, "returned": out.get("kind")})
        return
    if kind == "opening":
        oid = spec["id"]
        door = [float(v) for v in model.openings[oid]["position"][:2]]
        drawn = _first_last_point(response, role)
        if out.get("id") != oid:
            fail("endpoint", {role: "opening id changed", "returned": out.get("id")})
        if end_space not in model.sides(oid):
            fail("endpoint", {role: "route space is not a side of the door", "space": end_space})
        if drawn is None or math.dist(drawn, door) > SAME_M:
            fail("endpoint", {role: "route does not start/end at the door", "drawn": drawn})
        return
    if kind == "space":
        wanted = spec["id"]
    elif kind == "asset":
        wanted = model.assets[spec["id"]]["space_id"]
        if out.get("id") != spec["id"]:
            fail("endpoint", {role: "asset id not echoed", "returned": out.get("id")})
        asset_pos = model.assets[spec["id"]].get("position")
        if asset_pos and list(out.get("requested_position") or [])[:2] != list(asset_pos)[:2]:
            fail("endpoint", {role: "asset position not echoed"})
    else:  # point
        wanted = spec["space_id"]
        if list(out.get("requested_position") or []) != [float(v) for v in spec["position"]]:
            fail("endpoint", {role: "requested_position not echoed"})
        if role == "start" and response.get("_exact_start"):
            drawn = _first_last_point(response, role)
            if drawn is None or math.dist(drawn, spec["position"]) > SAME_M:
                warnings = [w for w in response.get("warnings") or [] if "exact_start" in w]
                fail(
                    "endpoint",
                    {
                        role: "exact_start route does not start at the point",
                        "drawn": drawn,
                        "warnings": warnings,
                    },
                )
    if out.get("space_id") != wanted or end_space != wanted:
        fail(
            "endpoint",
            {
                role: "space not preserved",
                "wanted": wanted,
                "returned": out.get("space_id"),
                "path_end": end_space,
            },
        )


# ------------------------------------------------------------------ running requests


def outcome_of(call: Any) -> tuple[str, JSON | None, JSON]:
    """(outcome, response, error) of a service call; outcome is reachable / unreachable /
    blocked / error:<code> / crash:<Exception>."""
    try:
        response = call()
    except NavigationError as error:
        return "error:" + error.code, None, {"code": error.code, "message": error.message}
    except Exception as error:  # noqa: BLE001 - a crash is a finding, never fatal to the suite
        return f"crash:{type(error).__name__}", None, {"message": repr(error)[:400]}
    if not isinstance(response, dict):
        return "crash:non_dict", None, {}
    if "status" not in response:  # e.g. a change() state
        return "ok", response, {}
    status = response.get("status")
    if status == "ok" and response.get("reachable"):
        return "reachable", response, {}
    return str(status), response, {}


def jsonable(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def summary_of(response: JSON | None) -> JSON | None:
    if response is None:
        return None
    if "status" not in response:
        return {k: response.get(k) for k in ("revision", "closed", "changed")}
    return {
        "status": response.get("status"),
        "length_m": response.get("length_m"),
        "space_path": response.get("space_path"),
        "element_path": response.get("element_path"),
        "waypoints": len(response.get("waypoints") or []),
        "warnings": len(response.get("warnings") or []),
        "unreachable_reason": response.get("unreachable_reason"),
        "diagnosis": (response.get("diagnosis") or {}).get("category"),
    }


class Suite:
    def __init__(self, datasets: Path, seed: int) -> None:
        self.seed = seed
        self.repository = InMemoryBuildingRepository()
        self.models: dict[str, Model] = {}
        self.missing: list[str] = []
        for family, ids in FAMILIES.items():
            for bid in ids:
                if not (datasets / bid / "building.json").is_file():
                    self.missing.append(bid)
                    continue
                ingest_dataset(self.repository, datasets, bid)
                raw = json.loads((datasets / bid / "building.json").read_text(encoding="utf-8"))
                self.models[bid] = Model(raw, family)
        self.service = NavigationService(self.repository, datasets_dir=None)
        self.bounced: set[str] = set()  # buildings that already had a floor-bounce case

    # -- helpers

    def revision(self, bid: str) -> int:
        return int(self.service.state(bid)["revision"])

    def reset(self, bid: str) -> None:
        if self.service.closed_elements(bid):
            self.service.change(bid, "reset", None, self.revision(bid))

    def route(self, bid: str, request: JSON) -> tuple[str, JSON | None, JSON]:
        return outcome_of(lambda: self.service.route(bid, dict(request)))

    def pair(
        self, rng: random.Random, m: Model, cross_floor: bool | None = None
    ) -> tuple[str, str]:
        ids = sorted(m.spaces)
        for _ in range(200):
            a, b = rng.choice(ids), rng.choice(ids)
            if a == b:
                continue
            if cross_floor is None or (m.floor_of(a) != m.floor_of(b)) == cross_floor:
                return a, b
        a, b = rng.sample(ids, 2)
        return a, b

    @staticmethod
    def sp(space_id: str) -> JSON:
        return {"kind": "space", "id": space_id}

    def official(self, start: JSON, target: JSON, **extra: Any) -> JSON:
        return {"mode": "official", "start": start, "target": target, **extra}

    # -- generic routing case with independent expectation

    def routing_case(
        self,
        case: JSON,
        m: Model,
        request: JSON,
        closed: set[str],
        skip_length: bool = False,
    ) -> None:
        step_free = bool(request.get("step_free"))
        assume = set(request.get("assume_closed") or [])
        expect = m.verdict(request["start"], request["target"], closed | assume, step_free)
        case.update(request=request, expect=expect)
        outcome, response, error = self.route(m.id, request)
        case.update(outcome=outcome, error=error or None, response=summary_of(response))
        if outcome != expect:
            case["failed"].append(
                {"check": "verdict", "detail": {"expect": expect, "got": outcome}}
            )
        if outcome == "reachable" and response is not None:
            response = {**response, "_exact_start": bool(request.get("exact_start"))}
            fails, metrics = check_route(m, request, response, closed | assume, skip_length)
            case["failed"].extend(fails)
            case["metrics"] = metrics
        elif response is not None and (response.get("waypoints") or response.get("path")):
            case["failed"].append({"check": "status", "detail": "non-ok route carries geometry"})
        if outcome == "unreachable" and response is not None:
            # The diagnosis must name the constraint the independent model says is responsible.
            all_closed = closed | assume
            ends = (request["start"], request["target"])
            if all_closed and m.verdict(*ends, set(), step_free) == "reachable":
                wanted: str | None = "closed"
            elif step_free and m.verdict(*ends, all_closed, False) == "reachable":
                wanted = "step_free"
            elif m.verdict(*ends, set(), False) == "reachable":
                wanted = None  # only several relaxations together help: not checked
            else:
                wanted = "unreachable_in_model"
            got = (response.get("diagnosis") or {}).get("category")
            case["diagnosis_expected"] = wanted
            if wanted is not None and got != wanted:
                case["failed"].append(
                    {"check": "diagnosis", "detail": {"expect": wanted, "got": got}}
                )

    # -- categories

    def gen_reachable(self, case: JSON, rng: random.Random, m: Model) -> None:
        multi = len(m.floors) > 1
        bounce = m.floor_bounce_spaces()
        roll = rng.random()
        if bounce and (m.id not in self.bounced or roll < 0.3):
            self.bounced.add(m.id)
            # Same floor, but (independently) only connected through another floor.
            first, second = rng.sample(rng.choice(bounce), 2)
            a, b = rng.choice(first), rng.choice(second)
            case["variant"] = "same_floor_via_other_floor"
        else:
            cross = roll < 0.65 if multi else False
            a, b = self.pair(rng, m, cross)
            case["variant"] = "cross_floor" if cross else "same_floor"
        self.routing_case(case, m, self.official(self.sp(a), self.sp(b)), set())

    def gen_step_free(self, case: JSON, rng: random.Random, m: Model) -> None:
        non_sf_doors = sorted(o for o, op in m.openings.items() if not op["step_free"])
        options = ["cross_floor"] if len(m.floors) > 1 else []
        options += ["random_pair"]
        if non_sf_doors:
            options.append("non_step_free_opening")
        variant = rng.choice(options)
        case["variant"] = variant
        if variant == "non_step_free_opening":
            door = rng.choice(non_sf_doors)
            target = {"kind": "opening", "id": door}
            start = self.sp(rng.choice(sorted(m.spaces)))
            request = self.official(start, target, step_free=True)
        else:
            a, b = self.pair(rng, m, variant == "cross_floor")
            request = self.official(self.sp(a), self.sp(b), step_free=True)
        self.routing_case(case, m, request, set())

    def gen_endpoints(self, case: JSON, rng: random.Random, m: Model) -> None:
        options = ["asset_target", "asset_start", "point_start", "point_start_exact"]
        options += ["point_target", "opening_start_side"]
        if m.exits:
            options.append("exit_target")
        if m.doors:
            options.append("door_start")
        variant = rng.choice(options)
        case["variant"] = variant
        spaces = sorted(m.spaces)
        other = self.sp(rng.choice(spaces))
        skip_length = False
        extra: JSON = {}
        if variant in ("asset_target", "asset_start"):
            asset = {"kind": "asset", "id": rng.choice(sorted(m.assets))}
            start, target = (other, asset) if variant == "asset_target" else (asset, other)
        elif variant in ("point_start", "point_start_exact", "point_target"):
            sid = rng.choice(spaces)
            point = {"kind": "point", "space_id": sid, "position": m.random_point(rng, sid)}
            start, target = (other, point) if variant == "point_target" else (point, other)
            if variant == "point_start_exact":
                extra["exact_start"] = True
                skip_length = True  # length_m stays the official cost from the space (checked
                # against the plain twin below)
        elif variant == "exit_target":
            start, target = other, {"kind": "opening", "id": rng.choice(m.exits)}
        elif variant == "door_start":
            start, target = {"kind": "opening", "id": rng.choice(m.doors)}, other
        else:  # opening_start_side
            pool = m.doors or m.exits
            oid = rng.choice(pool)
            start = {"kind": "opening", "id": oid, "side_space_id": rng.choice(m.sides(oid))}
            target = other
        request = self.official(start, target, **extra)
        self.routing_case(case, m, request, set(), skip_length)
        if variant == "point_start_exact" and case["outcome"] == "reachable":
            twin = self.official(start, target)
            outcome, response, _ = self.route(m.id, twin)
            got = (case.get("response") or {}).get("length_m")
            if outcome != "reachable" or response is None or response["length_m"] != got:
                case["failed"].append(
                    {
                        "check": "length",
                        "detail": {
                            "exact_start_length_m": got,
                            "plain_twin": outcome,
                            "plain_length_m": response and response.get("length_m"),
                        },
                    }
                )
            else:
                fails, _ = check_route(m, twin, response, set())
                case["failed"].extend(
                    {"check": "twin_" + f["check"], "detail": f["detail"]} for f in fails
                )

    def gen_closures(self, case: JSON, rng: random.Random, m: Model) -> None:
        # A plain route with something to close: a door, lift/stairs or intermediate room.
        plain: JSON | None = None
        for _ in range(25):
            cross = len(m.floors) > 1 and rng.random() < 0.5
            a, b = self.pair(rng, m, cross)
            req = self.official(self.sp(a), self.sp(b))
            outcome, response, _ = self.route(m.id, req)
            if outcome == "reachable" and response and len(response["space_path"]) >= 3:
                plain = response
                break
        if plain is None:
            case.update(
                variant="no_route_found",
                request=None,
                expect="skipped",
                outcome="skipped",
            )
            case["failed"].append({"check": "generator", "detail": "no plain route to close"})
            return
        connectors = [e for e in plain["element_path"] if e in m.connectors]
        doors = [e for e in plain["element_path"] if e in m.openings]
        rooms = plain["space_path"][1:-1]
        choices = [("connector", connectors), ("door", doors), ("room", rooms)]
        weights = [3 if connectors else 0, 4 if doors else 0, 2 if rooms else 0]
        kind, pool = rng.choices(choices, weights=weights)[0]
        lifts = [c for c in pool if m.connectors.get(c, {}).get("kind") == "lift"]
        pool = sorted(set(lifts or pool))
        start, target = self.sp(plain["space_path"][0]), self.sp(plain["space_path"][-1])
        # Prefer (60 %) an element whose closure leaves a detour, judged by the independent BFS
        # only, so the suite has both re-routes and blocked outcomes.
        detours = [e for e in pool if m.verdict(start, target, {e}, False) == "reachable"]
        element = rng.choice(detours if detours and rng.random() < 0.6 else pool)
        mechanism = "assume_closed" if rng.random() < 0.35 else "scenario_change"
        case["variant"] = f"{kind}:{mechanism}"
        case["plain_route"] = summary_of(plain)
        case["closed_element"] = element
        request = self.official(start, target)
        try:
            if mechanism == "assume_closed":
                request["assume_closed"] = [element]
                self.routing_case(case, m, request, set())
            else:
                before = self.revision(m.id)
                state = self.service.change(m.id, "close", element, before)
                if element not in state["closed"] or state["revision"] != before + 1:
                    case["failed"].append(
                        {"check": "scenario", "detail": {"state": summary_of(state)}}
                    )
                self.routing_case(case, m, request, {element})
                case["scenario"] = {"action": "close", "element_id": element, "revision": before}
        finally:
            self.reset(m.id)
        # The reset restores the plain route exactly.
        outcome, again, _ = self.route(m.id, self.official(request["start"], request["target"]))
        if (
            outcome != "reachable"
            or again is None
            or (
                again["space_path"],
                again["length_m"],
            )
            != (plain["space_path"], plain["length_m"])
        ):
            case["failed"].append({"check": "reset_restores", "detail": summary_of(again)})

    def gen_invalid(self, case: JSON, rng: random.Random, m: Model, variant: str) -> None:
        spaces = sorted(m.spaces)
        a, b = self.pair(rng, m)
        bid = m.id
        case["variant"] = variant
        expect = "error:invalid_input"
        request: JSON | None = None
        call = None
        try:
            if variant == "unknown_space":
                request = self.official(self.sp(a), self.sp(bid + "-F9-S999"))
                expect = "error:unknown_place"
            elif variant == "unknown_asset":
                request = self.official({"kind": "asset", "id": bid + "-A99999"}, self.sp(b))
                expect = "error:unknown_place"
            elif variant == "bad_kind":
                request = self.official({"kind": "room", "id": a}, self.sp(b))
            elif variant == "point_outside":
                x0, y0, x1, y1 = m.polygons[a].bounds
                point = {"kind": "point", "space_id": a, "position": [x1 + 40.0, y1 + 40.0]}
                request = self.official(point, self.sp(b))
            elif variant == "point_nan":
                point = {"kind": "point", "space_id": a, "position": [float("nan"), 1.0]}
                request = self.official(point, self.sp(b))
            elif variant == "bad_mode":
                request = {**self.official(self.sp(a), self.sp(b)), "mode": "teleport"}
            elif variant == "step_free_string":
                request = self.official(self.sp(a), self.sp(b), step_free="yes")
            elif variant == "assume_unknown":
                request = self.official(self.sp(a), self.sp(b), assume_closed=[bid + "-X0"])
                expect = "error:unknown_place"
            elif variant == "bad_side":
                oid = rng.choice(m.doors or m.exits)
                wrong = next(s for s in spaces if s not in m.sides(oid))
                request = self.official(
                    {"kind": "opening", "id": oid, "side_space_id": wrong}, self.sp(b)
                )
            elif variant == "closed_start_room":
                state = self.service.change(bid, "close", a, self.revision(bid))
                case["scenario"] = {"action": "close", "element_id": a, "state": summary_of(state)}
                request = self.official(self.sp(a), self.sp(b))
                expect = "blocked"
            elif variant == "closed_target_exit":
                oid = rng.choice(m.exits)
                state = self.service.change(bid, "close", oid, self.revision(bid))
                case["scenario"] = {
                    "action": "close",
                    "element_id": oid,
                    "state": summary_of(state),
                }
                request = self.official(self.sp(a), {"kind": "opening", "id": oid})
                expect = "blocked"
            elif variant == "unknown_building":
                request = self.official(self.sp(a), self.sp(b))
                expect = "error:unknown_building"
                call = lambda: self.service.route(bid + "-nope", dict(request))  # noqa: E731
            elif variant == "stale_revision":
                stale = self.revision(bid) + 7
                element = rng.choice(m.doors or m.exits)
                request = {"action": "close", "element_id": element, "expected_revision": stale}
                expect = "error:revision_conflict"
                call = lambda: self.service.change(bid, "close", element, stale)  # noqa: E731
            elif variant == "change_unknown":
                request = {"action": "close", "element_id": bid + "-X0", "expected_revision": 0}
                expect = "error:unknown_place"
                call = lambda: self.service.change(  # noqa: E731
                    bid, "close", bid + "-X0", self.revision(bid)
                )
            elif variant == "precision_unsupported":
                request = {**self.official(self.sp(a), self.sp(b)), "mode": "precision"}
                expect = "error:geometry_unsupported"
            else:
                raise ValueError(variant)
            case.update(request=jsonable(request), expect=expect)
            if call is None:
                outcome, response, error = self.route(bid, request)
            else:
                outcome, response, error = outcome_of(call)
            case.update(outcome=outcome, error=error or None, response=summary_of(response))
            if outcome != expect:
                case["failed"].append(
                    {"check": "verdict", "detail": {"expect": expect, "got": outcome}}
                )
            if response is not None and response.get("waypoints"):
                case["failed"].append({"check": "status", "detail": "refused route has waypoints"})
            if outcome == "blocked" and response is not None:
                closed_id = case["scenario"]["element_id"]
                if response.get("blocked_element_id") != closed_id:
                    detail = {"expect": closed_id, "got": response.get("blocked_element_id")}
                    case["failed"].append({"check": "blocked_element", "detail": detail})
            if variant == "stale_revision" and self.service.closed_elements(bid):
                case["failed"].append({"check": "scenario", "detail": "stale change was applied"})
        finally:
            self.reset(bid)


INVALID_VARIANTS = (
    "unknown_space",
    "closed_start_room",
    "point_outside",
    "unknown_asset",
    "bad_kind",
    "closed_target_exit",
    "point_nan",
    "stale_revision",
    "bad_mode",
    "step_free_string",
    "assume_unknown",
    "bad_side",
    "unknown_building",
    "change_unknown",
    "precision_unsupported",
)


def quotas(n: int) -> dict[str, int]:
    total = sum(WEIGHTS.values())
    out = {c: n * WEIGHTS[c] // total for c in CATEGORIES}
    out["reachable"] += n - sum(out.values())
    return out


def run(n: int, seed: int, datasets: Path) -> tuple[list[JSON], JSON]:
    suite = Suite(datasets, seed)
    if not suite.models:
        raise SystemExit(f"No dataset found under {datasets}")
    buildings = list(suite.models)
    rng = random.Random(seed)
    plan: list[tuple[str, str]] = []
    for category, count in quotas(n).items():
        for k in range(count):
            plan.append((category, buildings[k % len(buildings)]))
    cases: list[JSON] = []
    invalid_counter = 0
    for index, (category, bid) in enumerate(plan):
        case_seed = rng.randrange(2**32)
        case_rng = random.Random(case_seed)
        m = suite.models[bid]
        case: JSON = {
            "index": index,
            "case_seed": case_seed,
            "building": bid,
            "family": m.family,
            "category": category,
            "failed": [],
        }
        began = time.perf_counter()
        try:
            if category == "invalid":
                variant = INVALID_VARIANTS[invalid_counter % len(INVALID_VARIANTS)]
                invalid_counter += 1
                if variant == "closed_target_exit" and not m.exits:
                    variant = "unknown_space"
                suite.gen_invalid(case, case_rng, m, variant)
            else:
                getattr(suite, "gen_" + category)(case, case_rng, m)
        except Exception as error:  # noqa: BLE001 - a harness crash is reported as a case failure
            case.setdefault("expect", "?")
            case.setdefault("outcome", f"harness_crash:{type(error).__name__}")
            case["failed"].append({"check": "harness", "detail": repr(error)[:400]})
            suite.reset(bid)
        case["elapsed_ms"] = round((time.perf_counter() - began) * 1000, 1)
        expect = case.get("expect")
        case["group"] = (
            "guaranteed_reachable" if expect == "reachable" else "blocked_or_unsupported"
        )
        case["passed"] = not case["failed"]
        cases.append(jsonable(case))
        mark = "ok  " if case["passed"] else "FAIL"
        print(
            f"{mark} #{index:03d} {category:<9} {bid:<22} {case.get('variant', ''):<26} "
            f"expect={expect} got={case.get('outcome')}"
        )
    meta = {
        "seed": seed,
        "n": len(cases),
        "datasets_dir": str(datasets),
        "buildings": {bid: m.family for bid, m in suite.models.items()},
        "missing_buildings": suite.missing,
        "quotas": quotas(n),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return cases, meta


# ------------------------------------------------------------------ report


def _table(rows: list[tuple[str, list[JSON]]], title: str) -> list[str]:
    out = [
        f"### {title}",
        "",
        "| | total | pass | fail | guaranteed reachable (pass/total) | "
        "blocked/unsupported (pass/total) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, items in rows:
        reach = [c for c in items if c["group"] == "guaranteed_reachable"]
        block = [c for c in items if c["group"] != "guaranteed_reachable"]
        passed = sum(c["passed"] for c in items)
        out.append(
            f"| {name} | {len(items)} | {passed} | {len(items) - passed} | "
            f"{sum(c['passed'] for c in reach)}/{len(reach)} | "
            f"{sum(c['passed'] for c in block)}/{len(block)} |"
        )
    out.append("")
    return out


def markdown(cases: list[JSON], meta: JSON) -> str:
    passed = sum(c["passed"] for c in cases)
    lines = [
        "# Navigation case suite (backend, official mode)",
        "",
        f"Seed `{meta['seed']}`, {meta['n']} cases, generated {meta['generated_utc']} by "
        "`backend/scripts/nav_cases.py` (run from backend/: "
        f"`.venv/Scripts/python scripts/nav_cases.py --n {meta['n']} --seed {meta['seed']}`).",
        "",
        f"**{passed}/{len(cases)} passed.** Expectations come from an independent BFS over the "
        "building.json openings/vertical connectors (closures and step-free rules applied), never "
        "from the router; every returned route is re-validated independently (adjacency via "
        "element_path, closures, step-free, length = drawn polyline + vertical cost from floor "
        "elevations within max(1 %, 0.05 m), continuity, polyline inside the path's spaces within "
        "0.3 m, the same length/inside/floor-order/end-space rules on the joined waypoints the UI "
        "animates, endpoints preserved, unreachable diagnosis category). See the module docstring "
        "for the exact rules.",
        "",
        "Buildings: " + ", ".join(f"`{b}` ({f})" for b, f in meta["buildings"].items()) + ".",
    ]
    if meta["missing_buildings"]:
        lines.append("Missing (skipped): " + ", ".join(meta["missing_buildings"]) + ".")
    lines.append("")
    groups: dict[str, list[JSON]] = defaultdict(list)
    for c in cases:
        groups[c["group"]].append(c)
    lines += _table(
        [(g, groups[g]) for g in ("guaranteed_reachable", "blocked_or_unsupported")], "By group"
    )
    by_cat = [(c, [x for x in cases if x["category"] == c]) for c in CATEGORIES]
    lines += _table(by_cat, "By category")
    families = list(dict.fromkeys(c["family"] for c in cases))
    lines += _table([(f, [x for x in cases if x["family"] == f]) for f in families], "By family")
    lines += _table(
        [(b, [x for x in cases if x["building"] == b]) for b in meta["buildings"]], "By building"
    )
    variants = Counter(f"{c['category']}/{c.get('variant')}" for c in cases)
    lines += [
        "### Variants",
        "",
        "| category/variant | cases | expected outcomes |",
        "|---|---:|---|",
    ]
    for key in sorted(variants):
        expects = Counter(
            str(c.get("expect")) for c in cases if f"{c['category']}/{c.get('variant')}" == key
        )
        lines.append(
            f"| {key} | {variants[key]} | "
            + ", ".join(f"{k}: {v}" for k, v in sorted(expects.items()))
            + " |"
        )
    lines.append("")
    failures = [c for c in cases if not c["passed"]]
    checks = Counter(f["check"] for c in failures for f in c["failed"])
    lines += ["## Failures", ""]
    if not failures:
        lines += ["None.", ""]
    else:
        lines.append("Failed checks: " + ", ".join(f"{k} x{v}" for k, v in checks.most_common()))
        lines.append("")
        for c in failures:
            lines.append(
                f"- **#{c['index']:03d}** `{c['building']}` {c['category']}/{c.get('variant')} "
                f"(case_seed {c['case_seed']}): expect `{c.get('expect')}`, got "
                f"`{c.get('outcome')}`"
            )
            lines.append(f"  - request: `{json.dumps(c.get('request'))[:400]}`")
            if c.get("closed_element"):
                lines.append(f"  - closed element: `{c['closed_element']}`")
            for f in c["failed"]:
                lines.append(f"  - {f['check']}: `{json.dumps(f['detail'])[:600]}`")
        lines.append("")
    if checks.get("continuity"):
        lines += [
            "### Note on `continuity` failures",
            "",
            "Seen so far only on routes that leave a floor and come back to it without walking on "
            "the intermediate floor (e.g. F1 -stairs-> F0 -other stairs-> F1 in swiss-1785, where "
            "F0-S035 is only passed through as a node). `app/navigation/response.py` "
            "(`official_response` -> `_ride_to`) only consumes a queued vertical move while the "
            "next walk segment is on a different floor, so both moves stay queued and are "
            "appended after the last walk segment: `path`/`waypoints` jump straight across the "
            "floor between the two stairwells (through walls), then ride F1->F0->F1 after the "
            "target, so the last waypoint is not in the target space and the waypoint length "
            "differs from `length_m`. `space_path`, `element_path` and `length_m` themselves are "
            "correct. Fix direction: interleave walk segments and vertical moves in the router's "
            "step order instead of matching floors.",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="number of cases (default 140)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    began = time.perf_counter()
    cases, meta = run(args.n, args.seed, args.datasets_dir)
    meta["elapsed_s"] = round(time.perf_counter() - began, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "nav_cases.json").write_text(
        json.dumps({"meta": meta, "cases": cases}, indent=1), encoding="utf-8"
    )
    (args.out / "nav_cases.md").write_text(markdown(cases, meta), encoding="utf-8")
    passed = sum(c["passed"] for c in cases)
    print(f"{passed}/{len(cases)} passed in {meta['elapsed_s']} s -> {args.out}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
