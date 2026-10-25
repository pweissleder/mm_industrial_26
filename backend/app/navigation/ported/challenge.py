# ruff: noqa: E501, B905, SIM103  (ported prototype code: long messages kept verbatim)
"""Official challenge routing = the kit v1.1.0 route model, computed by the official code.

Every cost, path and reachability verdict comes from `starter_agent/building_map.py` of kit v1.1.0
(vendored unchanged in app.navigation.official, blob id checked by a test): its graph (door-to-door
walks on polygon.buffer(-0.15), door landing 0.30 m, cover tolerance 0.02, edges rounded to 3
decimals, stairs 2 m per metre of rise, lift 15 m + 1 m per metre once per ride with every pair of
stops joined, no outside link), its Dijkstra search, `spaces_passed` and `directions`, and its route
length (rounded to 2 decimals). Endpoints resolve to spaces like the official `resolve_space` (an
item resolves to the room or bay that holds it).

This module only adds what the official router does not return: element_path, per-leg results,
transitions, and drawable XY segments that replay the official walk geometry for each walked edge
(door position -> 30 cm landing -> shortest visibility path on the shrunken outline -> landing -> door).

Two opt-in flags are NOT part of kit v1.1.0 (organizer remarks only) and default to off:
`forklift` and `no_production_transit`. When set they prune the official graph and are labelled
unofficial in `rules` and `warnings`.

Ported from prototypes/unified-nav-voice/challenge.py. Port changes (routing algorithm unchanged):
- endpoint kind `opening` (a door, opening or exit door): the route starts/ends at the official
  graph node `door:<id>` itself (an exit route ends at the exit door, never outside). The legacy
  `door` kind (door side -> its space) was replaced by it;
- an optional per-map `graph_cache` (BuildingMap.graph results by closures and step_free) so a
  long-lived scenario map does not rebuild its graph per call;
- directions for door endpoints use the official wording with the door named at the ends.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import threading
from collections import OrderedDict

import networkx as nx
import numpy as np
import shapely

from app.navigation import official as kit
from app.navigation.errors import ModelError
from app.navigation.official import building_map as _building_map

MODEL_LABEL = "Official challenge routing"
UNOFFICIAL = "unofficial (organizer remark, not in kit 1.1.0)"
FORKLIFT_MIN_WIDTH_M = 3.0
PRODUCTION_ROOM_TYPES = ("additive_zone", "assembly_zone", "production_zone")
MAX_VIA = 25
_MAP_CACHE: OrderedDict = OrderedDict()
_MAP_CACHE_SIZE = 8
_VIS_CACHE: OrderedDict = OrderedDict()
_VIS_CACHE_SIZE = 64
_CACHE_LOCK = threading.Lock()

FORKLIFT_WARNING = (
    f"Forklift rules are {UNOFFICIAL}: no stairs, every opening used at least "
    f"{FORKLIFT_MIN_WIDTH_M:.1f} m wide (unknown width = unusable), lifts only with a declared width of at "
    f"least {FORKLIFT_MIN_WIDTH_M:.1f} m. The data has no forklift-aisle classification, so aisles are not checked."
)
PRODUCTION_WARNING = (
    f"No transit through production areas ({', '.join(PRODUCTION_ROOM_TYPES)}) is {UNOFFICIAL}; "
    "such a space may still be a start, stop or target."
)


def official():
    return _building_map


def reference_map(building):
    """A fresh official BuildingMap on a private copy of the building dict."""
    return _building_map.BuildingMap(copy.deepcopy(building))


# ---------------------------------------------------------------- request parsing


def _bool(request, name):
    value = request.get(name, False)
    if not isinstance(value, bool):
        raise ModelError("invalid_input", f"{name} must be a boolean")
    return value


def _endpoint(bmap, spec, name):
    """Resolve like the official resolve_space: a space is itself, an item its holding space.
    An opening is its own official graph node `door:<id>`."""
    bm = official()
    if not isinstance(spec, dict):
        raise ModelError("invalid_input", f"{name} must be an endpoint object")
    kind = spec.get("kind")
    if kind == "space":
        sid = spec.get("id")
        if not isinstance(sid, str) or sid not in bmap.spaces:
            raise ModelError("invalid_input", f"{name}: unknown space id")
        out = {
            "kind": "space",
            "id": sid,
            "space_id": sid,
            "requested_position": None,
            "resolution": "Space node at its representative_point (official anchor).",
        }
    elif kind == "asset":
        aid = spec.get("id")
        asset = bmap.assets.get(aid) if isinstance(aid, str) else None
        if asset is None or asset.get("space_id") not in bmap.spaces:
            raise ModelError("invalid_input", f"{name}: unknown asset id")
        out = {
            "kind": "asset",
            "id": aid,
            "space_id": asset["space_id"],
            "requested_position": asset.get("position"),
            "resolution": "Official rule: a route to an item ends in the room or bay that holds it.",
        }
    elif kind == "point":
        sid = spec.get("space_id")
        if not isinstance(sid, str) or sid not in bmap.spaces:
            raise ModelError("invalid_input", f"{name}: unknown space_id")
        position = spec.get("position")
        if (
            not isinstance(position, (list, tuple))
            or len(position) != 2
            or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in position
            )
        ):
            raise ModelError("invalid_input", f"{name}.position must be [x, y] with finite numbers")
        from shapely.geometry import Point

        if official().outline(bmap.spaces[sid]["polygon"]).distance(Point(position)) > 1e-6:
            raise ModelError("invalid_input", f"{name}: position is outside the space polygon")
        out = {
            "kind": "point",
            "id": None,
            "space_id": sid,
            "requested_position": [float(position[0]), float(position[1])],
            "resolution": "Official graph has one node per space: the point resolves to its space.",
        }
    elif kind == "opening":
        oid = spec.get("id")
        opening = bmap.openings.get(oid) if isinstance(oid, str) else None
        sides = [s for s in (opening or {}).get("space_ids") or [] if s in bmap.spaces]
        if opening is None or not sides:
            raise ModelError("invalid_input", f"{name}: unknown opening id")
        side = spec.get("side_space_id")
        if side is not None and side not in sides:
            raise ModelError(
                "invalid_input", f"{name}: side_space_id must be one of the opening's spaces"
            )
        is_exit = bm.OUTSIDE in opening["space_ids"]
        out = {
            "kind": "opening",
            "id": oid,
            "space_id": side or sides[0],
            "node": bm.DOOR_PREFIX + oid,
            "requested_position": opening.get("position"),
            "is_exit": is_exit,
            "floor_id": opening["floor_id"],
            "position": [float(opening["position"][0]), float(opening["position"][1])],
            "resolution": (
                "The route starts/ends at the door itself (official graph node door:<id>); "
                + (
                    "an exit route ends at the exit door: kit 1.1.0 has no route outside."
                    if is_exit
                    else "either side may be used."
                )
            ),
        }
        return out
    else:
        raise ModelError("invalid_input", f"{name}.kind must be space, asset, opening or point")
    out["node"] = out["space_id"]
    out["floor_id"] = bmap.spaces[out["space_id"]]["floor_id"]
    out["position"] = list(bmap.anchor(out["space_id"]))
    return out


def _parse(bmap, request):
    if not isinstance(request, dict):
        raise ModelError("invalid_input", "request must be an object")
    closed = request.get("closed", [])
    if not isinstance(closed, list) or not all(isinstance(c, str) for c in closed):
        raise ModelError(
            "invalid_input", "closed must be a list of space, opening or connector ids"
        )
    unknown = sorted(
        {
            c
            for c in closed
            if c not in bmap.spaces and c not in bmap.openings and c not in bmap.connectors
        }
    )
    if unknown:
        raise ModelError("invalid_input", "Unknown closed id(s): " + ", ".join(unknown))
    clearance = request.get("wall_clearance_m", official().WALL_CLEARANCE_M)
    if (
        isinstance(clearance, bool)
        or not isinstance(clearance, (int, float))
        or clearance != official().WALL_CLEARANCE_M
    ):
        raise ModelError(
            "invalid_input", "wall_clearance_m is fixed at 0.15 m by the official route model"
        )
    if request.get("allow_outside", False) is not False:
        raise ModelError(
            "invalid_input",
            "Kit 1.1.0 has no route round the outside; allow_outside is not supported",
        )
    via = request.get("via", [])
    if not isinstance(via, list) or len(via) > MAX_VIA:
        raise ModelError("invalid_input", f"via must be a list of at most {MAX_VIA} endpoints")
    flags = {
        "step_free": _bool(request, "step_free"),
        "forklift": _bool(request, "forklift"),
        "no_production_transit": _bool(request, "no_production_transit"),
    }
    endpoints = [_endpoint(bmap, request.get("start"), "start")]
    endpoints += [_endpoint(bmap, v, f"via[{i}]") for i, v in enumerate(via)]
    endpoints.append(_endpoint(bmap, request.get("target"), "target"))
    return sorted(set(closed)), flags, endpoints


# ---------------------------------------------------------------- official graph


def _view(bmap, closed):
    """The official map with these closures; shares the (closure-independent) walk cache."""
    view = copy.copy(bmap)
    view.closed = set(closed)
    view.last_route, view.last_route_points = None, []
    return view


def official_graph(view, step_free):
    """BuildingMap.graph(step_free) of this view, memoised in the map's optional `graph_cache`
    (shared by every view of a long-lived map). The cached graph must not be mutated."""
    cache = getattr(view, "graph_cache", None)
    if cache is None:
        return view.graph(step_free)
    key = (frozenset(view.closed), bool(step_free))
    graph = cache.get(key)
    if graph is None:
        graph = view.graph(step_free)
        cache[key] = graph
    return graph


def _graph(view, flags, leg_ends=()):
    try:
        graph = official_graph(view, flags["step_free"])
    except KeyError as error:
        raise ModelError(
            "geometry_underdetermined",
            "Building data lacks a field the official route model needs",
            {"missing": str(error)},
        ) from error
    if flags["forklift"] or flags["no_production_transit"]:
        graph = graph.copy()  # the pruning below must not touch a cached graph
    if flags["forklift"]:
        _forklift(view, graph)
    if flags["no_production_transit"]:
        _no_production_transit(view, graph, set(leg_ends))
    return graph


def _width(element):
    value = element.get("width_m")
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        else None
    )


def _forklift(view, graph):
    narrow = [
        "door:" + oid for oid, o in view.openings.items() if (_width(o) or 0) < FORKLIFT_MIN_WIDTH_M
    ]
    graph.remove_nodes_from(narrow)
    drop = []
    for u, v, key, edge in graph.edges(keys=True, data=True):
        if "via" in edge:
            connector = view.connectors[edge["via"]]
            if (
                connector["kind"] != official().LIFT
                or (_width(connector) or 0) < FORKLIFT_MIN_WIDTH_M
            ):
                drop.append((u, v, key))
    graph.remove_edges_from(drop)


def _no_production_transit(view, graph, ends):
    production = {
        sid for sid, s in view.spaces.items() if s.get("room_type") in PRODUCTION_ROOM_TYPES
    }
    # A pass-through walk is a door-to-door edge inside the space, or entering its node when it is no stop.
    drop = [
        (u, v, k)
        for u, v, k, e in graph.edges(keys=True, data=True)
        if e.get("space") in production and u != e["space"] and v != e["space"]
    ]
    graph.remove_edges_from(drop)
    graph.remove_nodes_from([sid for sid in production if sid not in ends])


def _leg(graph, a, b):
    try:
        length, nodes = nx.single_source_dijkstra(graph, a, b, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return length, nodes


# ---------------------------------------------------------------- drawable geometry


def _space_visibility(space):
    """Walkable area, cover-tolerant area, corners and corner-to-corner visibility of one space, built
    exactly as in building_map.walking_lengths (visibility_matrix over the shrunken outline's corners)."""
    bm = official()
    key = hashlib.sha1(json.dumps([space.get("id"), space["polygon"]]).encode()).hexdigest()
    with _CACHE_LOCK:
        hit = _VIS_CACHE.get(key)
        if hit is not None:
            _VIS_CACHE.move_to_end(key)
            return hit
    polygon = bm.outline(space["polygon"])
    walkable = polygon.buffer(-bm.WALL_CLEARANCE_M)
    if walkable.is_empty or walkable.geom_type != "Polygon":
        walkable = polygon
    allowed = walkable.buffer(bm.COVER_TOLERANCE_M)
    corners = [
        (float(x), float(y))
        for ring in [walkable.exterior, *walkable.interiors]
        for x, y in list(ring.coords)[:-1]
    ]
    matrix = bm.visibility_matrix(allowed, corners) if corners else np.zeros((0, 0))
    hit = (allowed, np.asarray(corners, dtype=float).reshape(-1, 2), matrix)
    with _CACHE_LOCK:
        if len(_VIS_CACHE) >= _VIS_CACHE_SIZE:
            _VIS_CACHE.popitem(last=False)
        _VIS_CACHE[key] = hit
    return hit


def _sight(allowed, point, targets):
    """Straight lengths from point to each target where the segment is clear (official covers test)."""
    if not len(targets):
        return np.zeros(0)
    origin = np.asarray(point, dtype=float)
    straight = np.hypot(targets[:, 0] - origin[0], targets[:, 1] - origin[1])
    lines = shapely.linestrings(np.stack([np.broadcast_to(origin, targets.shape), targets], axis=1))
    clear = shapely.covers(allowed, lines) | (straight == 0)
    return np.where(clear, straight, np.inf)


def _walk_points(view, space_id, a, b):
    """Shortest walk between two points of a space on the official visibility graph (same walkable area,
    cover tolerance and corners as building_map.walking_lengths). Returns (points, length, clear)."""
    bm = official()
    allowed, corners, matrix = _space_visibility(view.spaces[space_id])
    ends = np.asarray([a, b], dtype=float)
    direct = _sight(allowed, a, ends[1:])[0]
    from_a, to_b = _sight(allowed, a, corners), _sight(allowed, b, corners)
    # Dense Dijkstra from a over the corners; b is reached from a corner (to_b) or directly.
    n = len(corners)
    dist = from_a.copy()
    prev = np.full(n, -1)
    done = np.zeros(n, dtype=bool)
    best, best_corner = direct, -2
    for _ in range(n):
        pending = np.where(done, np.inf, dist)
        u = int(np.argmin(pending)) if n else 0
        if not n or not math.isfinite(pending[u]) or pending[u] >= best:
            break
        done[u] = True
        if dist[u] + to_b[u] < best:
            best, best_corner = dist[u] + to_b[u], u
        candidate = dist[u] + matrix[u]
        better = (candidate < dist) & ~done
        dist[better] = candidate[better]
        prev[better] = u
    if not math.isfinite(best):
        return [list(a), list(b)], math.dist(a, b) * bm.BLOCKED_DETOUR_FACTOR, False
    chain, i = [], best_corner
    while i >= 0:
        chain.append([float(corners[i][0]), float(corners[i][1])])
        i = int(prev[i])
    return [list(a), *reversed(chain), list(b)], float(best), True


def _landing(view, space_id, node):
    """(node position, landing point inside space_id) as in building_map.space_walks."""
    bm = official()
    if node.startswith(bm.DOOR_PREFIX):
        opening = view.openings[node[len(bm.DOOR_PREFIX) :]]
        position = (float(opening["position"][0]), float(opening["position"][1]))
        return position, bm.door_landing(bm.outline(view.spaces[space_id]["polygon"]), position)
    anchor = view.anchor(node)
    return anchor, anchor


def _edge_geometry(view, space_id, u, v):
    pu, lu = _landing(view, space_id, u)
    pv, lv = _landing(view, space_id, v)
    inner, length, clear = _walk_points(view, space_id, lu, lv)
    points = [list(pu)]
    for p in [*inner, list(pv)]:
        if p != points[-1]:
            points.append(p)
    return points, length + math.dist(pu, lu) + math.dist(pv, lv), clear


# ---------------------------------------------------------------- assembly


def _assemble_leg(view, graph, nodes, warnings):
    bm = official()
    element_path, steps, segments, transitions = [], [], [], []
    walk = vertical = 0.0
    for u, v in itertools.pairwise(nodes):
        order = len(steps)  # route order of this step (segments/transitions carry it)
        edge = view.cheapest(graph, u, v)
        weight = edge["weight"]
        if "via" in edge:
            connector = view.connectors[edge["via"]]
            f1, f2 = view.spaces[u]["floor_id"], view.spaces[v]["floor_id"]
            rise = abs(view.floors[f2]["elevation_m"] - view.floors[f1]["elevation_m"])
            element_path.append(edge["via"])
            vertical += weight
            steps.append(
                {
                    "type": connector["kind"],
                    "element_id": edge["via"],
                    "from": u,
                    "to": v,
                    "from_floor": f1,
                    "to_floor": f2,
                    "cost_m": weight,
                }
            )
            transitions.append(
                {
                    "element_id": edge["via"],
                    "kind": connector["kind"],
                    "name": connector.get("name"),
                    "from_floor": f1,
                    "to_floor": f2,
                    "from_space": u,
                    "to_space": v,
                    "from_position": list(view.anchor(u)),
                    "to_position": list(view.anchor(v)),
                    "delta_elevation_m": rise,
                    "cost_m": weight,
                    "order": order,
                }
            )
            continue
        sid = edge["space"]
        walk += weight
        points, drawn, clear = _edge_geometry(view, sid, u, v)
        if not clear:
            warnings.append(
                f"No clear walk inside {sid} between {u} and {v}; the official cost uses "
                f"{bm.BLOCKED_DETOUR_FACTOR} x the straight distance, drawn as a straight line."
            )
        steps.append(
            {
                "type": "walk",
                "space_id": sid,
                "from": u,
                "to": v,
                "cost_m": weight,
                "drawn_length_m": round(drawn, 6),
                "clear_walk": clear,
            }
        )
        if v.startswith(bm.DOOR_PREFIX):
            element_path.append(v[len(bm.DOOR_PREFIX) :])
        floor_id = view.spaces[sid]["floor_id"]
        if segments and segments[-1]["space_id"] == sid and segments[-1]["points"][-1] == points[0]:
            segments[-1]["points"].extend(points[1:])
        else:
            segments.append(
                {"floor_id": floor_id, "space_id": sid, "points": points, "order": order}
            )
    return {
        "element_path": element_path,
        "steps": steps,
        "segments": [s for s in segments if len(s["points"]) >= 2],
        "transitions": transitions,
        "walk_m": round(walk, 3),
        "vertical_cost_m": round(vertical, 3),
    }


def _node_info(view, node):
    floor_id, point = view.node_point(node)
    return {"id": node, "floor_id": floor_id, "position": [point[0], point[1]]}


def _door_label(view, opening_id):
    opening = view.openings[opening_id]
    if official().OUTSIDE in opening["space_ids"]:
        return f"the exit {opening_id}"
    return f"the {'door' if opening['kind'] == 'door' else 'opening'} {opening_id}"


def directions(view, graph, nodes):
    """BuildingMap.directions; for a route that starts or ends at a door node the same wording and
    logic, with that door named (the official helper only describes space nodes)."""
    bm = official()
    first, last = nodes[0], nodes[-1]
    if not first.startswith(bm.DOOR_PREFIX) and not last.startswith(bm.DOOR_PREFIX):
        return view.directions(graph, nodes)

    def where(node, neighbour):
        opening_id = node.removeprefix(bm.DOOR_PREFIX)
        if neighbour is not None:
            space_id = view.cheapest(graph, node, neighbour)["space"]
        else:
            space_id = next(s for s in view.openings[opening_id]["space_ids"] if s in view.spaces)
        return f"{_door_label(view, opening_id)} of {view.describe_space(space_id)}"

    single = len(nodes) == 1
    if first.startswith(bm.DOOR_PREFIX):
        steps = [f"Start at {where(first, None if single else nodes[1])}."]
    else:
        steps = [f"Start in {view.describe_space(first)}."]
    walked = 0.0
    for index, (previous, current) in enumerate(itertools.pairwise(nodes), start=1):
        edge = view.cheapest(graph, previous, current)
        walked += edge["weight"]
        if "via" in edge:
            connector = view.connectors[edge["via"]]
            verb = "Take" if connector["kind"] == bm.LIFT else "Use"
            steps.append(
                f"{verb} {connector['name']} ({connector['kind']}) to "
                f"{view.floor_name(view.spaces[current]['floor_id'])}."
            )
            walked = 0.0
        elif current.startswith(bm.DOOR_PREFIX) and index + 1 < len(nodes):
            opening = view.openings[current.removeprefix(bm.DOOR_PREFIX)]
            following = view.cheapest(graph, current, nodes[index + 1])["space"]
            passage = "through the door" if opening["kind"] == "door" else "through the opening"
            steps.append(
                f"Walk about {walked:.0f} m {passage} into {view.spaces[following]['name']}."
            )
            walked = 0.0
    if last.startswith(bm.DOOR_PREFIX):
        steps.append(
            f"Walk about {walked:.0f} m. You have arrived at {where(last, None if single else nodes[-2])}."
        )
    else:
        steps.append(f"Walk about {walked:.0f} m. You have arrived at {view.describe_space(last)}.")
    return steps


def _explain(view, flags, endpoints, closed):
    """Which constraint blocks the route: relax closures, step-free and the unofficial flags."""
    applicable = [
        name
        for name, on in (
            ("closures", bool(closed)),
            ("step_free", flags["step_free"]),
            ("forklift", flags["forklift"]),
            ("production_transit", flags["no_production_transit"]),
        )
        if on
    ]

    def solvable(relaxed):
        trial = dict(flags)
        if "step_free" in relaxed:
            trial["step_free"] = False
        if "forklift" in relaxed:
            trial["forklift"] = False
        if "production_transit" in relaxed:
            trial["no_production_transit"] = False
        trial_view = _view(view, [] if "closures" in relaxed else closed)
        used = set()
        for a, b in itertools.pairwise(endpoints):
            graph = _graph(trial_view, trial, (a["space_id"], b["space_id"]))
            leg = _leg(graph, a["node"], b["node"])
            if leg is None:
                return None
            used |= {n.removeprefix(official().DOOR_PREFIX) for n in leg[1]}
            used |= {
                trial_view.cheapest(graph, u, v).get("via") for u, v in itertools.pairwise(leg[1])
            }
        return used

    for size in range(1, len(applicable) + 1):
        hits = []
        for combo in itertools.combinations(applicable, size):
            used = solvable(set(combo))
            if used is None:
                continue
            if combo == ("closures",):
                hit = sorted(c for c in closed if c in used) or closed
                hits.append("reachable if closures are lifted: " + ", ".join(hit))
            elif size == 1:
                hits.append(
                    {
                        "step_free": "reachable without the step-free restriction",
                        "forklift": "reachable without the unofficial forklift restrictions",
                        "production_transit": "reachable only through production areas (unofficial no-transit rule)",
                    }[combo[0]]
                )
            else:
                hits.append("(" + " + ".join(combo) + ")")
        if hits:
            return (
                "; ".join(hits)
                if size == 1
                else "reachable only if several constraints are relaxed together: "
                + " or ".join(hits)
            )
    return "no connection in the building data (kit 1.1.0 has no route round the outside)"


def _blocked(end, closed):
    """The closed element that blocks an endpoint itself (its space, or the door it is)."""
    if end["kind"] == "opening":
        return end["id"] if end["id"] in closed else None
    return end["space_id"] if end["space_id"] in closed else None


def official_route(bmap, request):
    """Official kit 1.1.0 route on an official BuildingMap (closures in `request['closed']`)."""
    bm = official()
    closed, flags, endpoints = _parse(bmap, request)
    view = _view(bmap, closed)
    unofficial = [name for name in ("forklift", "no_production_transit") if flags[name]]
    rules = {
        "source": f"kit {kit.KIT_VERSION} starter_agent/building_map.py @ {kit.KIT_COMMIT[:7]}",
        "wall_clearance_m": bm.WALL_CLEARANCE_M,
        "door_landing_m": bm.DOOR_LANDING_M,
        "cover_tolerance_m": bm.COVER_TOLERANCE_M,
        "edge_length_decimals": bm.LENGTH_DECIMALS,
        "route_length_decimals": 2,
        "stairs_m_per_m": bm.STAIRS_M_PER_RISE,
        "lift_wait_m": bm.LIFT_FIXED_M,
        "lift_m_per_m": bm.LIFT_M_PER_RISE,
        "outside_link": False,
        "step_free": flags["step_free"],
        "closed": closed,
        "forklift": f"on, {UNOFFICIAL}" if flags["forklift"] else "off",
        "forklift_min_width_m": FORKLIFT_MIN_WIDTH_M,
        "production_transit": f"forbidden, {UNOFFICIAL}"
        if flags["no_production_transit"]
        else "allowed (official)",
        "production_room_types": list(PRODUCTION_ROOM_TYPES),
        "unofficial_rules_active": unofficial,
    }
    assumptions = [
        "Official kit 1.1.0 route model (README section 3), computed by its own building_map.py: walks inside a "
        "space go door to door 15 cm off the walls round corners, a door lands 30 cm inside, edges rounded to 3 "
        "decimals, route length to 2 decimals.",
        "Stairs 2 m per metre of rise; a lift ride 15 m once plus 1 m per metre of rise; no route round the outside.",
        "Furniture and machines are not obstacles; the drawn line is the official walk geometry, not a clearance check.",
    ]
    if any(e["kind"] in ("asset", "point") for e in endpoints):
        assumptions.append(
            "Asset and point endpoints resolve to their space (official resolve_space); "
            "the route starts/ends at the space node."
        )
    if any(e["kind"] == "opening" for e in endpoints):
        assumptions.append(
            "Door endpoints are the official graph node of the door itself; an exit route ends at "
            "the exit door (kit 1.1.0 has no route outside)."
        )
    warnings = []
    if flags["forklift"]:
        warnings.append(FORKLIFT_WARNING)
    if flags["no_production_transit"]:
        warnings.append(PRODUCTION_WARNING)
    result = {
        "model": "challenge",
        "label": MODEL_LABEL,
        "kit": {"version": kit.KIT_VERSION, "commit": kit.KIT_COMMIT},
        "status": "ok",
        "reachable": True,
        "space_path": [],
        "element_path": [],
        "cost_m": None,
        "walk_m": 0.0,
        "vertical_cost_m": 0.0,
        "legs": [],
        "steps": [],
        "segments": [],
        "transitions": [],
        "directions": [],
        "nodes": [],
        "start": _public(endpoints[0]),
        "target": _public(endpoints[-1]),
        "via": [_public(e) for e in endpoints[1:-1]],
        "rules": rules,
        "unreachable_reason": None,
        "assumptions": assumptions,
        "warnings": warnings,
    }
    for end in endpoints:
        blocked = _blocked(end, closed)
        if blocked is not None:
            role = "start" if end is endpoints[0] else "target" if end is endpoints[-1] else "stop"
            what = "door" if end["kind"] == "opening" else "space"
            result.update(
                status="blocked_endpoint",
                reachable=False,
                blocked_element_id=blocked,
                unreachable_reason=f"The {role} {what} {blocked} is closed.",
            )
            return result
    base = (
        _graph(view, flags, [e["space_id"] for e in endpoints])
        if not flags["no_production_transit"]
        else None
    )
    legs, all_nodes, steps_before = [], [], 0
    for index, (a, b) in enumerate(itertools.pairwise(endpoints)):
        graph = base if base is not None else _graph(view, flags, (a["space_id"], b["space_id"]))
        found = _leg(graph, a["node"], b["node"])
        if found is None:
            reason = _explain(view, flags, endpoints, closed)
            if len(endpoints) > 2:
                reason = f"leg {index + 1} ({a['node']} -> {b['node']}): {reason}"
            result.update(
                status="unreachable", reachable=False, unreachable_reason=reason, warnings=warnings
            )
            return result
        length, nodes = found
        part = _assemble_leg(view, graph, nodes, warnings)
        for item in part["segments"] + part["transitions"]:  # route order across legs
            item["order"] += steps_before
        steps_before += len(part["steps"])
        legs.append(
            {
                "index": index,
                "from": _public(a),
                "to": _public(b),
                "space_path": view.spaces_passed(graph, nodes),
                "element_path": part["element_path"],
                "cost_m": round(length, 2),
                "length_unrounded_m": length,
                "walk_m": part["walk_m"],
                "vertical_cost_m": part["vertical_cost_m"],
                "nodes": nodes,
                "_part": part,
                "_graph": graph,
            }
        )
        all_nodes += nodes if not all_nodes else nodes[1:]
    total = sum(leg["length_unrounded_m"] for leg in legs)
    if base is not None:
        route_directions = directions(view, base, all_nodes)
        space_path = view.spaces_passed(base, all_nodes)
    else:  # per-leg graphs: official helpers per leg, joined
        route_directions, space_path = [], []
        for leg in legs:
            text = directions(view, leg["_graph"], leg["nodes"])
            route_directions += text if not route_directions else text[1:]
            sp = leg["space_path"]
            space_path += sp if not space_path else sp[1:]
    if not unofficial and all(e["kind"] != "opening" for e in endpoints):
        # Cross-check against the official entry point itself (same graph, same search). Door endpoints
        # are an extension the official route() cannot resolve, so they are checked by the tests instead.
        check_view = _view(bmap, closed)
        if base is not None:
            check_view.graph = lambda step_free: (
                base if step_free == flags["step_free"] else bmap.graph(step_free)
            )
        check = check_view.route(
            endpoints[0]["space_id"],
            endpoints[-1]["space_id"],
            flags["step_free"],
            via=[e["space_id"] for e in endpoints[1:-1]],
        )
        if (check.length_m, check.space_path, check.steps) != (
            round(total, 2),
            space_path,
            route_directions,
        ):
            raise ModelError(
                "source_drift", "Route assembly differs from the official BuildingMap.route result"
            )
    result.update(
        space_path=space_path,
        cost_m=round(total, 2),
        directions=route_directions,
        element_path=[e for leg in legs for e in leg["_part"]["element_path"]],
        walk_m=round(sum(leg["walk_m"] for leg in legs), 3),
        vertical_cost_m=round(sum(leg["vertical_cost_m"] for leg in legs), 3),
        steps=[s for leg in legs for s in leg["_part"]["steps"]],
        segments=[s for leg in legs for s in leg["_part"]["segments"]],
        transitions=[t for leg in legs for t in leg["_part"]["transitions"]],
        nodes=[_node_info(view, n) for n in all_nodes],
        legs=[
            {
                k: leg[k]
                for k in (
                    "index",
                    "from",
                    "to",
                    "space_path",
                    "element_path",
                    "cost_m",
                    "walk_m",
                    "vertical_cost_m",
                )
            }
            for leg in legs
        ],
    )
    return result


def _public(end):
    keys = (
        "kind",
        "id",
        "space_id",
        "node",
        "floor_id",
        "position",
        "requested_position",
        "resolution",
        "is_exit",
    )
    return {k: end[k] for k in keys if k in end}


def _content_key(building):
    """Walk lengths depend only on spaces and openings."""
    return hashlib.sha256(
        json.dumps(
            [building.get("spaces"), building.get("openings")], sort_keys=True, default=str
        ).encode()
    ).hexdigest()


def official_map(building):
    """Fresh official BuildingMap for a building dict. Like the kit's BuildingMap.load_cached, the walk
    lengths of identical spaces/openings are computed once on a pristine map and copied in."""
    key = _content_key(building)
    with _CACHE_LOCK:
        cached = _MAP_CACHE.get(key)
        if cached is not None:
            _MAP_CACHE.move_to_end(key)
    if cached is None:
        pristine = reference_map(building)
        pristine.graph(step_free=False)
        cached = pristine.walks
        with _CACHE_LOCK:
            if len(_MAP_CACHE) >= _MAP_CACHE_SIZE:
                _MAP_CACHE.popitem(last=False)
            _MAP_CACHE[key] = cached
    bmap = reference_map(building)
    bmap.walks = dict(cached)
    return bmap


def prime_map_cache(bmap):
    """Register the complete walk lengths of a long-lived official map (e.g. a Service scenario) so
    official_map() on a copy of the same building does not recompute them."""
    building = bmap.data
    key = _content_key(building)
    with _CACHE_LOCK:
        known = key in _MAP_CACHE
    if not known:
        bmap.graph(step_free=False)
        with _CACHE_LOCK:
            if len(_MAP_CACHE) >= _MAP_CACHE_SIZE:
                _MAP_CACHE.popitem(last=False)
            _MAP_CACHE[key] = dict(bmap.walks)


def challenge_route(building: dict, request: dict) -> dict:
    """Official challenge routing for a building dict (see module docstring)."""
    if not isinstance(building, dict) or not isinstance(building.get("spaces"), list):
        raise ModelError("invalid_input", "building must be an object with a spaces list")
    try:
        bmap = official_map(building)
    except (KeyError, TypeError) as error:
        raise ModelError(
            "invalid_input", "building lacks fields of the kit format", {"missing": str(error)}
        ) from error
    return official_route(bmap, request)
