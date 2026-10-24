"""Resolve a user location to a route endpoint (POST /api/location/resolve).

Sources: `map_click` (point-in-polygon on a floor), `room_id`, `door_qr` (an opening and optionally
its side) and `simulated` (the same inputs, flagged `simulated: true`). Indoor positioning would be
a later source of the same call. Nothing is stored.
"""

import math
from typing import Any

from shapely.geometry import Point, Polygon

from app.navigation.errors import NavigationError
from app.navigation.search import inside_space, is_exit, opening_name

JSON = dict[str, Any]
SOURCES = ("map_click", "room_id", "door_qr", "simulated")
# A click this close to a room (but outside every room) is offered as an ambiguous candidate.
NEAR_M = 0.5


def _polygon(space: JSON) -> Polygon:
    polygon = Polygon(space["polygon"])
    return polygon if polygon.is_valid else polygon.buffer(0)


def _position(value: Any) -> list[float]:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(
            isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
            for v in value
        )
    ):
        raise NavigationError("invalid_input", "position must be [x, y] with finite numbers")
    return [float(value[0]), float(value[1])]


def resolve(building: JSON, request: JSON) -> JSON:
    source = request.get("source")
    if source not in SOURCES:
        raise NavigationError("invalid_input", "source must be one of " + ", ".join(SOURCES))
    simulated = source == "simulated"
    floors = {f["id"]: f for f in building["floors"]}
    spaces = {s["id"]: s for s in building["spaces"]}
    openings = {o["id"]: o for o in building["openings"]}

    def done(status: str, endpoint: JSON | None, candidates: list[JSON], explanation: str) -> JSON:
        return {
            "status": status,
            "endpoint": endpoint,
            "candidates": candidates,
            "explanation": explanation,
            "simulated": simulated,
            "source": source,
        }

    def candidate(space: JSON, probability: float | None = None, **extra: Any) -> JSON:
        row: JSON = {
            "space_id": space["id"],
            "name": space.get("name") or space["id"],
            "floor_id": space["floor_id"],
            "floor_level": floors.get(space["floor_id"], {}).get("level"),
        }
        if probability is not None:
            row["probability"] = probability
        return row | extra

    opening_id = request.get("opening_id")
    if source == "door_qr" or (simulated and opening_id is not None):
        if not isinstance(opening_id, str) or opening_id not in openings:
            raise NavigationError("unknown_place", "Unknown opening_id", {"opening_id": opening_id})
        opening = openings[opening_id]
        sides = [s for s in opening["space_ids"] if s in spaces]
        side = request.get("side_space_id")
        if side is not None and side not in sides:
            raise NavigationError(
                "invalid_input",
                "side_space_id must be one of the opening's spaces",
                {"side_space_id": side},
            )
        if side is None and len(sides) == 1:
            side = sides[0]
        endpoint: JSON = {"kind": "opening", "id": opening_id}
        if side is not None:
            endpoint["side_space_id"] = side
        name = opening_name(opening, spaces)
        if side is None:
            return done(
                "ambiguous",
                endpoint,
                [candidate(spaces[s], 0.5) for s in sides],
                f"{name}: which side are you on?",
            )
        return done(
            "resolved",
            endpoint,
            [candidate(spaces[side], 1.0)],
            f"At {name}, on the side of {spaces[side].get('name') or side}"
            + (" (exit door)." if is_exit(opening) else "."),
        )

    space_id = request.get("space_id")
    position = request.get("position")
    if source == "room_id" or (simulated and space_id is not None and position is None):
        if not isinstance(space_id, str) or space_id not in spaces:
            raise NavigationError("unknown_place", "Unknown space_id", {"space_id": space_id})
        space = spaces[space_id]
        return done(
            "resolved",
            {"kind": "space", "id": space_id},
            [candidate(space, 1.0)],
            f"In {space.get('name') or space_id}.",
        )

    # map_click (or simulated with a position)
    xy = _position(position)
    point = Point(xy)
    if isinstance(space_id, str):
        if space_id not in spaces:
            raise NavigationError("unknown_place", "Unknown space_id", {"space_id": space_id})
        space = spaces[space_id]
        if _polygon(space).distance(point) > 1e-6:
            raise NavigationError(
                "invalid_input", "position is outside the given space", {"space_id": space_id}
            )
        return done(
            "resolved",
            {"kind": "point", "space_id": space_id, "position": xy},
            [candidate(space, 1.0)],
            f"In {space.get('name') or space_id} at the selected point.",
        )
    floor_id = request.get("floor_id")
    if not isinstance(floor_id, str) or floor_id not in floors:
        raise NavigationError(
            "invalid_input", "map_click needs floor_id (or space_id) with position"
        )
    on_floor = [s for s in building["spaces"] if s["floor_id"] == floor_id]
    hits = [s for s in on_floor if _polygon(s).covers(point)]
    if len(hits) == 1:
        space = hits[0]
        return done(
            "resolved",
            {"kind": "point", "space_id": space["id"], "position": xy},
            [candidate(space, 1.0)],
            f"In {space.get('name') or space['id']} at the selected point.",
        )
    if len(hits) > 1:
        share = round(1 / len(hits), 3)
        return done(
            "ambiguous",
            None,
            [candidate(s, share) for s in hits],
            "The point lies on the boundary of several spaces; choose one.",
        )
    near = sorted(
        ((_polygon(s).distance(point), s) for s in on_floor),
        key=lambda item: (item[0], item[1]["id"]),
    )
    close = [(d, s) for d, s in near if d <= NEAR_M]
    if close:
        return done(
            "ambiguous",
            None,
            [candidate(s, None, distance_m=round(d, 3)) for d, s in close[:5]],
            "The point is just outside the rooms; choose the nearest room.",
        )
    exits = [o for o in building["openings"] if is_exit(o) and o.get("floor_id") == floor_id]
    nearest_exit = min(exits, key=lambda o: (math.dist(o["position"], xy), o["id"]), default=None)
    rows = []
    if nearest_exit is not None:
        inside = inside_space(nearest_exit, spaces)
        if inside is not None:
            rows.append(
                candidate(
                    spaces[inside],
                    None,
                    opening_id=nearest_exit["id"],
                    distance_m=round(math.dist(nearest_exit["position"], xy), 3),
                )
            )
    return done(
        "outside_building",
        None,
        rows,
        "The point is outside the building on this floor"
        + ("; the nearest entrance is listed." if rows else "."),
    )
