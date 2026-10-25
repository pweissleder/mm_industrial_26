"""RouteResponse assembly (docs/PHASE_A_INTERFACES.md) from the official and precision routers.

`path`, `transitions` and `waypoints` are derived only from router output; directions come from the
router (official text) or from the checked precision segments, never from a language model.
"""

import math
import re
from collections.abc import Callable
from typing import Any

JSON = dict[str, Any]
SAME_POINT_M = 1e-6


def _xy(value: Any) -> list[float]:
    return [float(value[0]), float(value[1])]


def _endpoint_out(end: JSON, position: Any) -> JSON:
    out: JSON = {
        "kind": end.get("kind"),
        "id": end.get("id"),
        "space_id": end.get("space_id"),
        "floor_id": end.get("floor_id"),
        "position": _xy(position) if position is not None else None,
    }
    for key in ("requested_position", "resolution", "is_exit", "side_space_id"):
        if end.get(key) is not None:
            out[key] = end[key]
    return out


# ---------------------------------------------------------------- official


def official_response(result: JSON, building: JSON) -> JSON:
    """RouteResponse fields for a `challenge.official_route` result."""
    openings = {o["id"]: o for o in building["openings"]}
    status = {"ok": "ok", "unreachable": "unreachable", "blocked_endpoint": "blocked"}[
        result["status"]
    ]
    path: list[JSON] = []
    transitions: list[JSON] = []
    vertical = list(result["transitions"])
    door_ids = [e for e in result["element_path"] if e in openings]

    def walk(segment: JSON) -> JSON:
        return {
            "floor_id": segment["floor_id"],
            "space_id": segment["space_id"],
            "kind": "walk",
            "points": [_xy(p) for p in segment["points"]],
        }

    if all("order" in item for item in [*result["segments"], *vertical]):
        # Walk segments and vertical moves in the router's step order (`order`, set by
        # challenge._assemble_leg), so a route that leaves a floor and comes back without walking
        # in between (stairs down, other stairs up) keeps its rides where they happen.
        events = sorted(
            [(s["order"], s, True) for s in result["segments"]]
            + [(m["order"], m, False) for m in vertical],
            key=lambda event: event[0],
        )
        for _, item, is_walk in events:
            if is_walk:
                path.append(walk(item))
            else:
                path.append(_connector_entry(item["kind"], item["element_id"], item))
                transitions.append(_vertical_transition(item))
        vertical = []
    else:
        # Without step order: walk segments in traversal order; before each one, the vertical
        # moves that bring the walker from the current floor to the segment's floor (one ride per
        # floor hop; a route may start in a stairwell or lift). See `_ride_to`.
        floor = (result.get("start") or {}).get("floor_id")
        for segment in result["segments"]:
            floor = _ride_to(segment["floor_id"], floor, vertical, path, transitions, lambda m: m)
            path.append(walk(segment))
    for move in vertical:  # vertical moves with no walk after them (e.g. ends at a stair node)
        path.append(_connector_entry(move["kind"], move["element_id"], move))
        transitions.append(_vertical_transition(move))
    # Door transitions in the order the doors are passed, inserted before the next floor change.
    door_transitions = []
    for door_id in door_ids:
        opening = openings[door_id]
        door_transitions.append(
            {
                "kind": "door",
                "element_id": door_id,
                "from_floor_id": opening["floor_id"],
                "to_floor_id": opening["floor_id"],
                "position": _xy(opening["position"]),
                "schematic": False,
                "cost_m": 0.0,
                "is_exit": "outside" in opening["space_ids"],
            }
        )
    ordered = _merge_transitions(result["element_path"], door_transitions, transitions)
    response: JSON = {
        "mode": "official",
        "status": status,
        "reachable": bool(result["reachable"]),
        "checked": "official_rules",
        "length_m": result["cost_m"],
        "walk_m": result["walk_m"],
        "vertical_cost_m": result["vertical_cost_m"],
        "start": _endpoint_out(result["start"], result["start"].get("position")),
        "target": _endpoint_out(result["target"], result["target"].get("position")),
        "via": [_endpoint_out(v, v.get("position")) for v in result.get("via", [])],
        "space_path": result["space_path"],
        "element_path": result["element_path"],
        "path": path,
        "transitions": ordered,
        "directions": readable_directions(result["directions"], building),
        "directions_raw": result["directions"],
        "legs": [
            {k: leg[k] for k in ("index", "space_path", "element_path", "cost_m", "walk_m")}
            | {"vertical_cost_m": leg["vertical_cost_m"]}
            for leg in result["legs"]
        ],
        "rules": result["rules"],
        "kit": result["kit"],
        "steps": result["steps"],
        "warnings": result["warnings"],
        "assumptions": result["assumptions"],
    }
    if result.get("unreachable_reason"):
        response["unreachable_reason"] = result["unreachable_reason"]
    if result.get("blocked_element_id"):
        response["blocked_element_id"] = result["blocked_element_id"]
    response["waypoints"] = (
        waypoints(path, ordered, building, response["start"], response["target"])
        if response["reachable"]
        else []
    )
    return response


def readable_directions(lines: list[str], building: JSON) -> list[str]:
    """The router's direction lines without element ids ("Office 1.04 [b-F1-S004]" ->
    "Office 1.04", "the exit b-F0-O0018 of ..." -> "the exit of ..."); the wording and numbers
    stay the official router's. The original lines remain in `directions_raw`."""
    ids = {
        item["id"]
        for key in ("spaces", "openings", "vertical_connectors", "assets")
        for item in building.get(key, [])
    }

    def bracket(match: re.Match[str]) -> str:
        return "" if match.group(1) in ids else match.group(0)

    def bare(match: re.Match[str]) -> str:
        return "" if match.group(1) in ids else match.group(0)

    out = []
    for line in lines:
        text = re.sub(r" \[([^\]\s]+)\]", bracket, line)
        out.append(re.sub(r" ([^\s,.;:()\[\]]+)", bare, text))
    return out


def _precision_move(move: JSON) -> JSON:
    """A precision router transition in the official move shape."""
    return {
        "kind": move["kind"],
        "element_id": move["id"],
        "from_floor": move["from_floor_id"],
        "to_floor": move["to_floor_id"],
        "from_position": move["from_position"],
        "to_position": move["to_position"],
        "cost_m": move["reference_cost_m"],
    }


def _ride_to(
    floor_id: str,
    current: str | None,
    vertical: list[JSON],
    path: list[JSON],
    transitions: list[JSON],
    shape: Callable[[JSON], JSON],
) -> str:
    """Append the queued vertical moves that lead from `current` to `floor_id` (in order) and
    return the new current floor. Moves are consumed only while the walker is not yet there and
    the next move starts on the current floor, so a return to an earlier floor works too."""
    while vertical and current is not None and current != floor_id:
        move = shape(vertical[0])
        if move["from_floor"] != current:
            break
        vertical.pop(0)
        path.append(_connector_entry(move["kind"], move["element_id"], move))
        transitions.append(_vertical_transition(move))
        current = move["to_floor"]
    return floor_id


def _connector_entry(kind: str, element_id: str, move: JSON) -> JSON:
    position = _xy(move["from_position"])
    return {
        "floor_id": move["from_floor"],
        "space_id": move.get("from_space"),
        "kind": "connector",
        "connector_kind": kind,
        "element_id": element_id,
        "from_floor_id": move["from_floor"],
        "to_floor_id": move["to_floor"],
        "to_space_id": move.get("to_space"),
        "to_position": _xy(move["to_position"]),
        "cost_m": move["cost_m"],
        "points": [position, position],
    }


def _vertical_transition(move: JSON) -> JSON:
    return {
        "kind": move["kind"],
        "element_id": move["element_id"],
        "from_floor_id": move["from_floor"],
        "to_floor_id": move["to_floor"],
        "position": _xy(move["from_position"]),
        "to_position": _xy(move["to_position"]),
        "schematic": True,
        "cost_m": move["cost_m"],
    }


def _merge_transitions(
    element_path: list[str], doors: list[JSON], vertical: list[JSON]
) -> list[JSON]:
    """Doors and vertical moves in element_path order (element_path lists both, in route order)."""
    queues = {"door": list(doors), "vertical": list(vertical)}
    by_id: dict[str, list[JSON]] = {}
    for item in queues["door"] + queues["vertical"]:
        by_id.setdefault(item["element_id"], []).append(item)
    ordered = []
    for element in element_path:
        bucket = by_id.get(element)
        if bucket:
            ordered.append(bucket.pop(0))
    leftovers = [item for bucket in by_id.values() for item in bucket]
    return ordered + leftovers


# ---------------------------------------------------------------- precision


def precision_directions(building: JSON, result: JSON) -> list[str]:
    """Followable steps from the checked segments: only real doors and connectors, no turns.
    Ported from the prototype assistant's `precision_directions`."""
    floors = {f["id"]: f for f in building["floors"]}
    spaces = {s["id"]: s for s in building["spaces"]}
    vertical = [t for t in result.get("transitions", []) if t.get("kind") in {"lift", "stairs"}]
    steps: list[str] = []
    walked = 0.0
    previous: tuple[Any, Any] | None = None
    for segment in result.get("segments", []):
        space, floor = segment.get("space_id"), segment.get("floor_id")
        if previous and floor != previous[1]:
            move = vertical.pop(0) if vertical else {}
            verb = {"lift": "take the lift", "stairs": "use the stairs"}.get(
                move.get("kind", ""), "change floors"
            )
            steps.append(
                f"Walk about {walked:.0f} m, then {verb} to "
                f"{floors.get(floor, {}).get('name', 'the next floor')}."
            )
            walked = 0.0
        elif previous and space != previous[0]:
            steps.append(
                f"Walk about {walked:.0f} m through the door into "
                f"{spaces.get(space, {}).get('name', 'the next room')}."
            )
            walked = 0.0
        walked += segment.get("length_m") or 0.0
        previous = (space, floor)
    if previous:
        steps.append(
            f"Walk about {walked:.0f} m to arrive in "
            f"{spaces.get(previous[0], {}).get('name', 'the destination')}."
        )
    return steps


def precision_response(result: JSON, building: JSON, start: JSON, target: JSON) -> JSON:
    """RouteResponse fields for a `precision.navigate` result (plus door-endpoint stubs already
    merged into `result['segments']` by the service)."""
    path: list[JSON] = []
    transitions: list[JSON] = []
    vertical = [t for t in result["transitions"] if t.get("kind") in {"stairs", "lift"}]
    doors = [t for t in result["transitions"] if t.get("kind") == "door"]
    door_queue = list(doors)
    floor = (result.get("start") or {}).get("floor_id") or start.get("floor_id")
    for segment in result["segments"]:
        floor = _ride_to(segment["floor_id"], floor, vertical, path, transitions, _precision_move)
        is_door = segment.get("kind") == "validated_door_approach"
        path.append(
            {
                "floor_id": segment["floor_id"],
                "space_id": segment["space_id"],
                "kind": "door" if is_door else "walk",
                "points": [_xy(p) for p in segment["points"]],
                **({"element_id": segment.get("opening_id")} if is_door else {}),
            }
        )
        if (
            is_door
            and door_queue
            and segment.get("opening_id") == door_queue[0]["id"]
            and _xy(segment["points"][-1]) == _xy(door_queue[0]["points"][1])
        ):
            door = door_queue.pop(0)
            transitions.append(
                {
                    "kind": "door",
                    "element_id": door["id"],
                    "from_floor_id": door["floor_id"],
                    "to_floor_id": door["floor_id"],
                    "position": _xy(door["points"][1]),
                    "schematic": False,
                    "cost_m": door["length_xy_m"],
                    "width_m": door.get("width_m"),
                }
            )
    for move in map(_precision_move, vertical):  # moves with no walk after them
        path.append(_connector_entry(move["kind"], move["element_id"], move))
        transitions.append(_vertical_transition(move))
    status = result["status"]
    space_path: list[str] = []
    for segment in result["segments"]:
        if not space_path or space_path[-1] != segment["space_id"]:
            space_path.append(segment["space_id"])
    element_path = [t["element_id"] for t in transitions]
    for role, end in (("start", start), ("target", target)):
        if end.get("kind") == "opening" and end["id"] not in element_path:
            if role == "start":
                element_path.insert(0, end["id"])
            else:
                element_path.append(end["id"])
    start_out = _endpoint_out(
        {**result["start"], **start},
        result["start"].get("position") or result["start"]["positions"][0],
    )
    target_out = _endpoint_out(
        {**result["target"], **target},
        result["target"].get("position") or result["target"]["selected_position"],
    )
    response: JSON = {
        "mode": "precision",
        "status": "ok",
        "reachable": True,
        "checked": "schematic_vertical" if status == "schematic_vertical" else "obstacle_checked",
        "length_m": round(result["length_m"], 3),
        "walk_m": round(result["length_m"], 3),
        "vertical_cost_m": result["schematic_transition_cost_m"],
        "start": start_out,
        "target": target_out,
        "space_path": space_path,
        "element_path": element_path,
        "path": path,
        "transitions": transitions,
        "directions": precision_directions(building, result),
        "profile": result["profile"],
        "width_m": result["width_m"],
        "safety_m": result["safety_m"],
        "clearance_m": result["clearance_m"],
        "fully_checked": result["fully_checked"],
        "snapped_endpoints": result["snapped_endpoints"],
        "validation_summary": result["validation_summary"],
        "unsupported_transitions": result["unsupported_transitions"][:30],
        "warnings": result["warnings"],
        "assumptions": result["assumptions"],
        "metrics": result["metrics"],
    }
    response["waypoints"] = waypoints(path, transitions, building, start_out, target_out)
    return response


# ---------------------------------------------------------------- waypoints


def waypoints(
    path: list[JSON], transitions: list[JSON], building: JSON, start: JSON, target: JSON
) -> list[JSON]:
    """Phase B animation points along `path`: one per polyline vertex, door and floor change.

    `space_id` is the space the walker is in on reaching the point (a door vertex keeps the space
    before the door; the next point carries the space behind it).

    `distance_m` accumulates XY walking length plus the cost of each vertical move; `fraction` is
    `distance_m` over the total; `heading_deg` is the travel direction to the next point that has a
    different XY (0 deg = +x, counter-clockwise), kept through vertical moves.
    """
    elevation = {f["id"]: float(f.get("elevation_m") or 0.0) for f in building["floors"]}
    points: list[JSON] = []

    def add(
        floor_id: str,
        xy: list[float],
        kind: str,
        step: float,
        element: str | None,
        space_id: str | None = None,
    ) -> None:
        if points:
            last = points[-1]
            if last["floor_id"] == floor_id and math.dist(last["position"], xy) <= SAME_POINT_M:
                if element is not None and last["kind"] == "walk":
                    last["kind"], last["element_id"] = kind, element
                return
        distance = (points[-1]["distance_m"] if points else 0.0) + step
        item: JSON = {"floor_id": floor_id, "position": xy, "kind": kind, "distance_m": distance}
        if space_id is not None:
            item["space_id"] = space_id
        if element is not None:
            item["element_id"] = element
        points.append(item)

    for entry in path:
        if entry["kind"] == "connector":
            xy = entry["points"][0]
            kind = entry.get("connector_kind") or "stairs"
            add(
                entry["from_floor_id"],
                xy,
                kind,
                _step(points, entry["from_floor_id"], xy),
                entry["element_id"],
                entry.get("space_id"),
            )
            if points[-1]["floor_id"] == entry["from_floor_id"]:
                points[-1]["kind"], points[-1]["element_id"] = kind, entry["element_id"]
            # Arrival where the connector actually lands on the next floor (its XY may differ).
            add(
                entry["to_floor_id"],
                entry.get("to_position") or xy,
                kind,
                float(entry.get("cost_m") or 0.0),
                entry["element_id"],
                entry.get("to_space_id"),
            )
            continue
        for xy in entry["points"]:
            step = _step(points, entry["floor_id"], xy)
            add(entry["floor_id"], xy, "walk", step, None, entry.get("space_id"))
    if not points:
        for end in (start, target):
            if end.get("position") is not None and end.get("floor_id") is not None:
                points.append(
                    {
                        "floor_id": end["floor_id"],
                        "space_id": end.get("space_id"),
                        "position": end["position"],
                        "kind": "walk",
                        "distance_m": 0.0,
                    }
                )
    # Mark doors in route order on the first matching vertex after the previous mark.
    cursor = 0
    for transition in transitions:
        if transition["kind"] != "door":
            continue
        for index in range(cursor, len(points)):
            item = points[index]
            if (
                item["floor_id"] == transition["from_floor_id"]
                and math.dist(item["position"], transition["position"]) <= SAME_POINT_M
            ):
                if item["kind"] == "walk":
                    item["kind"], item["element_id"] = "door", transition["element_id"]
                cursor = index + 1
                break
    if points:
        points[0]["kind"] = "start"
        if len(points) > 1:
            points[-1]["kind"] = "target"
    total = points[-1]["distance_m"] if points else 0.0
    heading = 0.0
    for index, item in enumerate(points):
        following = next(
            (
                p
                for p in points[index + 1 :]
                if math.dist(p["position"], item["position"]) > SAME_POINT_M
            ),
            None,
        )
        if following is not None:
            dx = following["position"][0] - item["position"][0]
            dy = following["position"][1] - item["position"][1]
            heading = math.degrees(math.atan2(dy, dx)) % 360.0
        item["index"] = index
        item["elevation_m"] = elevation.get(item["floor_id"], 0.0)
        item["heading_deg"] = round(heading, 3)
        item["distance_m"] = round(item["distance_m"], 4)
        item["fraction"] = round(item["distance_m"] / total, 6) if total > 0 else 0.0
    if points and total > 0:
        points[-1]["fraction"] = 1.0
    return points


def _step(points: list[JSON], floor_id: str, xy: list[float]) -> float:
    """XY length from the previous waypoint (0 across floors: vertical moves add their cost)."""
    if not points or points[-1]["floor_id"] != floor_id:
        return 0.0
    return math.dist(points[-1]["position"], xy)
