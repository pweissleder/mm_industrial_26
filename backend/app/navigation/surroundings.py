"""What is around a person (night task §7, S01/S02): things, doors and neighbouring rooms
relative to a pose and view heading, from the building data only.

Headings follow the building frame used everywhere (degrees counter-clockwise from +X). Sides are
four 90° sectors around the view direction; "right" is clockwise. Things ahead that
are clearly off to one side (a door on the right-hand wall further down a corridor) also carry
`wall_side`, which is how people say it ("the second door on your right").
Visibility is a plain line of
sight inside the person's own room outline: something in another room is reported as behind a wall
(or through a door), never as visible. Without a heading no sides are given (ask, don't guess).
"""

import math
from typing import Any

from shapely.geometry import LineString, Polygon

JSON = dict[str, Any]
SIDES = ("ahead", "left", "behind", "right")
LATERAL_M = 0.4  # this far off the line of sight counts as "on the right/left" further along
WALL_SIDE_MAX_M = 10.0
WALL_SIDE_MIN_DEG = 8.0


def _side(rel: float) -> str:
    a = abs(rel)
    if a <= 45:
        return "ahead"
    if a >= 135:
        return "behind"
    return "left" if rel > 0 else "right"


def _relative(
    origin: tuple[float, float], heading: float | None, target: tuple[float, float]
) -> JSON:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    out: JSON = {"distance_m": round(math.hypot(dx, dy), 1)}
    if heading is not None:
        bearing = math.degrees(math.atan2(dy, dx)) % 360
        rel = (bearing - heading + 540) % 360 - 180  # + = counter-clockwise = left
        out |= {"side": _side(rel), "relative_deg": round(rel)}
        lateral = -math.sin(math.radians(rel)) * math.hypot(dx, dy)  # + = to the right
        # Only near and clearly off-axis: far away a few degrees of heading error would move doors
        # of the opposite wall to "this side".
        near = math.hypot(dx, dy) <= WALL_SIDE_MAX_M and abs(rel) >= WALL_SIDE_MIN_DEG
        if out["side"] == "ahead" and abs(lateral) >= LATERAL_M and near:
            out["wall_side"] = "right" if lateral > 0 else "left"
    return out


def surroundings(
    data: JSON,
    closed: set[str],
    space_id: str,
    position: list[float],
    heading_deg: float | None,
    radius_m: float = 12.0,
    limit: int = 40,
) -> JSON:
    spaces = {s["id"]: s for s in data["spaces"]}
    floors = {f["id"]: f for f in data["floors"]}
    here = spaces[space_id]
    room = Polygon(here["polygon"]).buffer(0.05)
    origin = (float(position[0]), float(position[1]))

    def visible(target: tuple[float, float]) -> bool:
        if math.dist(origin, target) < 1e-6:
            return True
        return bool(room.covers(LineString([origin, target])))

    items: list[JSON] = []
    for asset in data["assets"]:
        if asset.get("floor_id") != here["floor_id"]:
            continue
        target = (float(asset["position"][0]), float(asset["position"][1]))
        rel = _relative(origin, heading_deg, target)
        if rel["distance_m"] > radius_m:
            continue
        same_room = asset.get("space_id") == space_id
        items.append(
            {
                "kind": "thing",
                "name": asset.get("name"),
                "type": asset.get("type"),
                "room": spaces.get(asset.get("space_id"), {}).get("name"),
                "visible": same_room and visible(target),
                "where": "in this room" if same_room else "in another room (behind a wall)",
                **rel,
            }
        )
    for opening in data["openings"]:
        if space_id not in opening["space_ids"]:
            continue
        target = (float(opening["position"][0]), float(opening["position"][1]))
        other = next((s for s in opening["space_ids"] if s != space_id), None)
        to = "outside" if other == "outside" else spaces.get(other or "", {}).get("name")
        items.append(
            {
                "kind": "exit" if other == "outside" else "door",
                "name": f"{'Exit' if other == 'outside' else 'Door'} to {to}",
                "closed": opening["id"] in closed or (other in closed if other else False),
                "visible": True,  # on the room's own outline
                "where": "on this room's wall",
                **_relative(origin, heading_deg, target),
            }
        )
    items.sort(key=lambda i: (not i.get("visible"), i["distance_m"]))
    out: JSON = {
        "room": here.get("name"),
        "floor": floors.get(here["floor_id"], {}).get("name"),
        "heading_known": heading_deg is not None,
        "items": items[:limit],
        "rules": "Sides are relative to the person's view direction (right = clockwise). "
        "wall_side marks things ahead that are on the right/left-hand side further along. "
        "'visible' means a clear line inside this room; other rooms are behind walls.",
    }
    if heading_deg is None:
        out["note"] = (
            "The view direction is unknown: ask which way the person faces; no sides given."
        )
    return out
