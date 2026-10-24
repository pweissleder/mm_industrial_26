"""Create or extend a building step by step (level 4: 'build a building from floor plans by
conversation'), and check that what was built makes sense before anyone routes through it."""

from __future__ import annotations

from shapely.geometry import Point

from .geometry import shared_wall_midpoint
from .graph import RoomGraph
from .model import OUTSIDE, Asset, Building, Connector, Floor, Opening, Space
from .rules import STEP_FREE, WALK

DEFAULT_DOOR_WIDTH = {"office": 0.9, "lift": 1.1, "lab": 1.2, "seminar_room": 1.2, "meeting_room": 1.2,
                      "toilet": 1.2, "stairwell": 1.2, "storage": 1.6, "server_room": 1.6}


class BuildingBuilder:
    def __init__(self, building: Building | None = None, building_id: str = "new-building", name: str = "New building"):
        self.b = building or Building({"id": building_id, "name": name, "source": "built", "units": "m"},
                                      [], [], [], [], [])

    # ---- creation -----------------------------------------------------------------------------
    def add_floor(self, level: int, name: str | None = None, elevation_m: float | None = None,
                  height_m: float = 3.5) -> Floor:
        fid = f"{self.b.id}-F{level}"
        if elevation_m is None:
            elevation_m = level * height_m
        f = Floor(fid, level, name or ("Ground floor" if level == 0 else f"Floor {level}" if level > 0 else f"Basement {-level}"),
                  float(elevation_m), float(height_m))
        self.b.floors[fid] = f
        self.b.version += 1
        return f

    def add_space(self, floor_id: str, name: str, polygon: list[list[float]], type: str = "room",
                  room_type: str = "generic_room", ref: str | None = None) -> Space:
        sid = self.b.new_id(floor_id, "S")
        s = Space(sid, floor_id, type, room_type, name, ref, [list(map(float, p)) for p in polygon], synthetic=True)
        if not s.polygon.is_valid or s.polygon.area <= 0:
            raise ValueError(f"polygon of {name} is not a valid area")
        return self.b.add_space(s)

    def add_door(self, first: str, second: str, position: tuple[float, float] | None = None,
                 width_m: float | None = None, kind: str = "door", step_free: bool = True) -> Opening:
        """Door between two spaces (or a space and 'outside'). Without a position it goes in the
        middle of their shared wall."""
        b = self.b
        inner = first if first != OUTSIDE else second
        floor_id = b.spaces[inner].floor_id
        if position is None:
            if OUTSIDE in (first, second):
                raise ValueError("an entrance needs a position on the outer wall")
            position = shared_wall_midpoint(b.spaces[first].polygon, b.spaces[second].polygon)
            if position is None:
                raise ValueError(f"{b.spaces[first].name} and {b.spaces[second].name} do not share a wall")
        if width_m is None:
            width_m = max(DEFAULT_DOOR_WIDTH.get(b.spaces[s].room_type, 0.9) for s in (first, second) if s != OUTSIDE)
        if OUTSIDE in (first, second):
            kind = "entrance"
        o = Opening(b.new_id(floor_id, "O"), floor_id, (first, second), kind, tuple(position), float(width_m),
                    step_free, synthetic=True)
        return b.add_opening(o)

    def add_connector(self, kind: str, name: str, space_ids: list[str]) -> Connector:
        b = self.b
        stops = sorted(((b.spaces[s].floor_id, s) for s in space_ids), key=lambda st: b.floors[st[0]].level)
        c = Connector(b.new_id(b.id, "V"), kind, name, stops, step_free=(kind == "lift"), synthetic=True)
        b.connectors[c.id] = c
        b.index()
        return c

    def add_asset(self, space_id: str, name: str, type: str, category: str = "equipment",
                  position: tuple[float, float] | None = None) -> Asset:
        s = self.b.spaces[space_id]
        a = Asset(self.b.new_id(self.b.id, "A"), name, type, category, space_id, s.floor_id,
                  tuple(position) if position else s.anchor, source="generated")
        self.b.assets[a.id] = a
        self.b.index()
        return a

    # ---- checks -------------------------------------------------------------------------------
    def validate(self) -> list[dict]:
        """Problems a person should fix or confirm. Empty list = consistent."""
        b, issues = self.b, []
        for o in b.openings.values():
            for sid in o.space_ids:
                if sid != OUTSIDE and sid not in b.spaces:
                    issues.append({"severity": "error", "element": o.id, "problem": f"joins unknown space {sid}"})
                elif sid != OUTSIDE and b.spaces[sid].polygon.exterior.distance(Point(o.position)) > 0.6:
                    issues.append({"severity": "warning", "element": o.id,
                                   "problem": f"door is {b.spaces[sid].polygon.exterior.distance(Point(o.position)):.1f} m from the wall of {b.spaces[sid].name}"})
        for c in b.connectors.values():
            if len({f for f, _ in c.stops}) != len(c.stops):
                issues.append({"severity": "error", "element": c.id, "problem": "two stops on the same floor"})
        if not b.entrances():
            issues.append({"severity": "error", "element": b.id, "problem": "no entrance: nothing is reachable from outside"})
        else:
            from dataclasses import replace as _r
            from .rules import BAY_READINGS
            rg = RoomGraph(b)
            reach = rg.reachable(OUTSIDE, WALK)
            reach_any = rg.reachable(OUTSIDE, _r(WALK, no_pass_through=BAY_READINGS[-1][1]))
            reach_sf = rg.reachable(OUTSIDE, STEP_FREE)
            for s in b.spaces.values():
                if s.id not in reach_any:
                    issues.append({"severity": "error", "element": s.id, "problem": f"{s.name} cannot be reached from any entrance"})
                elif s.id not in reach:
                    issues.append({"severity": "warning", "element": s.id,
                                   "problem": f"{s.name} can only be reached by crossing another factory zone"})
                elif s.id not in reach_sf and s.type == "room":
                    issues.append({"severity": "info", "element": s.id, "problem": f"{s.name} is not reachable step-free"})
        from shapely.strtree import STRtree
        for f in b.floors.values():
            spaces = b.spaces_on(f.id)
            tree = STRtree([s.polygon for s in spaces])
            for i, s in enumerate(spaces):
                for j in tree.query(s.polygon):
                    o = spaces[int(j)]
                    if int(j) > i and s.polygon.intersection(o.polygon).area > 0.5:
                        issues.append({"severity": "warning", "element": s.id,
                                       "problem": f"{s.name} overlaps {o.name} by {s.polygon.intersection(o.polygon).area:.1f} m²"})
        return issues
