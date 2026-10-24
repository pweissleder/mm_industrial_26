"""Building intelligence: the questions that need geometry, not just a search.

All functions take a NavGraph (which carries the Building and a CostModel) and ids, and
return plain data. The agent facade wraps them as tools."""

from __future__ import annotations

import math
from dataclasses import replace

from shapely.geometry import Polygon

from .geometry import overlap_area, shared_wall_length, shared_wall_midpoint
from .graph import NavGraph
from .model import OUTSIDE, Building, Opening, Space
from .rules import STEP_FREE, WALK, Profile, wide_load


# ---- rooms and areas ------------------------------------------------------------------------
def rooms_of_type(b: Building, floor_id: str | None = None, room_type: str | None = None) -> list[Space]:
    return [s for s in b.spaces.values()
            if (floor_id is None or s.floor_id == floor_id) and (room_type is None or s.room_type == room_type)]


def total_area(b: Building, floor_id: str, room_type: str) -> float:
    return sum(s.area_m2 for s in rooms_of_type(b, floor_id, room_type))


def largest_room(b: Building, floor_id: str, room_type: str, top: int = 3) -> list[tuple[Space, float]]:
    rooms = sorted(rooms_of_type(b, floor_id, room_type), key=lambda s: -s.area_m2)
    return [(s, s.area_m2) for s in rooms[:top]]


def is_countable_room(s: Space) -> bool:
    """A 'room' in reachability/delivery counts: type == room (not halls, balconies, corridors,
    stairwells or lifts). Verified against all train+dev answers."""
    return s.type == "room"


def is_wall_neighbour_candidate(s: Space) -> bool:
    """For shared-wall counts: any room or hall, but not corridors, stairwells, lifts or balconies."""
    return not s.is_circulation and s.type != "outdoor"


# ---- 3D geometry -----------------------------------------------------------------------------
def centre_3d(b: Building, space_id: str) -> tuple[float, float, float]:
    s = b.spaces[space_id]
    return (*s.centroid, b.floors[s.floor_id].elevation_m)


def straight_line_3d(b: Building, first: str, second: str) -> float:
    return math.dist(centre_3d(b, first), centre_3d(b, second))


def stacked(b: Building, space_id: str, direction: str = "above", top: int = 3) -> list[tuple[Space, float]]:
    """Spaces on the next floor up/down ranked by how much of this room's outline they cover."""
    s = b.spaces[space_id]
    level = b.floors[s.floor_id].level
    floors = sorted((f for f in b.floors.values() if (f.level > level if direction == "above" else f.level < level)),
                    key=lambda f: abs(f.level - level))
    if not floors:
        return []
    target = floors[0].id
    ranked = [(o, overlap_area(s.polygon, o.polygon)) for o in b.spaces_on(target)]
    ranked = [r for r in ranked if r[1] > 1e-6]
    return sorted(ranked, key=lambda r: -r[1])[:top]


def wall_neighbours(b: Building, space_id: str, min_length: float = 1.0, tolerance: float = 0.35,
                    include_circulation: bool = False) -> list[tuple[Space, float]]:
    """Spaces on the same floor sharing at least `min_length` m of wall with this one."""
    s = b.spaces[space_id]
    out = []
    env = s.polygon.buffer(tolerance)
    for o in b.spaces_on(s.floor_id):
        if o.id == s.id or (not include_circulation and not is_wall_neighbour_candidate(o)):
            continue
        if not env.intersects(o.polygon):
            continue
        length = shared_wall_length(s.polygon, o.polygon, tolerance)
        if length >= min_length:
            out.append((o, length))
    return sorted(out, key=lambda r: -r[1])


# ---- reachability ------------------------------------------------------------------------------
def reachable_rooms(nav: NavGraph, start: str, floor_id: str | None, profile: Profile) -> list[Space]:
    reach = nav.rooms.reachable(start, profile)
    b = nav.b
    return [b.spaces[sid] for sid in reach
            if sid != OUTSIDE and is_countable_room(b.spaces[sid])
            and (floor_id is None or b.spaces[sid].floor_id == floor_id)]


# ---- things --------------------------------------------------------------------------------------
def spaces_with_asset(b: Building, asset_type: str) -> list[str]:
    return sorted({a.space_id for a in b.assets.values() if a.type == asset_type})


def errand(nav: NavGraph, start: str, asset_type: str, floor_id: str, room_type: str,
           profile: Profile = WALK) -> dict:
    """Go to the nearest room holding `asset_type`, then carry it to the largest `room_type` on `floor_id`."""
    via, d1 = nav.nearest(start, spaces_with_asset(nav.b, asset_type), profile)
    target = largest_room(nav.b, floor_id, room_type, top=1)[0][0].id
    if via is None:
        return {"reachable": False}
    r = nav.route(via, target, profile)
    return {"reachable": r.reachable, "via": via, "target": target, "first_leg_m": d1,
            "second_leg_m": r.length_m, "total_m": d1 + r.length_m}


# ---- what if --------------------------------------------------------------------------------------
def door_on_shared_wall(b: Building, first: str, second: str, width_m: float = 0.9) -> Opening:
    s1, s2 = b.spaces[first], b.spaces[second]
    if s1.floor_id != s2.floor_id:
        raise ValueError("a door can only join two spaces on the same floor")
    mid = shared_wall_midpoint(s1.polygon, s2.polygon)
    if mid is None:
        raise ValueError(f"{s1.name} and {s2.name} do not share a wall")
    oid = b.new_id(s1.floor_id, "O-new")
    return Opening(oid, s1.floor_id, (first, second), "door", mid, width_m, True, synthetic=True)


def what_if_door(nav: NavGraph, first: str, second: str, floor_id: str, min_gain_m: float = 1.0,
                 profile: Profile = WALK) -> dict:
    """Add a door (temporarily) and count rooms on `floor_id` whose walk out of the building
    gets shorter by more than `min_gain_m`."""
    b = nav.b
    rooms = [s.id for s in b.spaces_on(floor_id) if is_countable_room(s)]
    before = nav.distances_to_exit(profile, rooms)
    door = door_on_shared_wall(b, first, second)
    b.add_opening(door)
    try:
        after = nav.distances_to_exit(profile, rooms)
    finally:
        b.remove_opening(door.id)
    better = [r for r in rooms if after.get(r, math.inf) < before.get(r, math.inf) - min_gain_m]
    return {"door_position": door.position, "improved": better, "count": len(better)}
