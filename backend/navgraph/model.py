"""Layer 0: the building as typed objects, loaded from (and saved back to) building.json.

Everything here is plain facts. Geometry that is expensive to compute (walkable outline, anchor,
area) is cached lazily on each Space. Edits (add a door, close a lift, move a machine) happen
here and bump `Building.version`, so the graph layers know to refresh."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Iterable

from shapely.geometry import Point, Polygon

OUTSIDE = "outside"

# Space types from the data format
ROOM, CORRIDOR, STAIR, LIFT, HALL, OUTDOOR = "room", "corridor", "stair", "lift", "hall", "outdoor"
# Spaces that are circulation, not destinations ("rooms" in counting questions exclude these)
CIRCULATION_TYPES = {CORRIDOR, STAIR, LIFT}
CIRCULATION_ROOM_TYPES = {"corridor", "stairwell", "lift"}


@dataclass
class Floor:
    id: str
    level: int
    name: str
    elevation_m: float
    height_m: float


@dataclass
class Space:
    id: str
    floor_id: str
    type: str          # room | corridor | stair | lift | hall | outdoor
    room_type: str     # office, kitchen, production_zone, generic_room, ...
    name: str
    ref: str | None
    polygon_coords: list[list[float]]
    synthetic: bool = False
    closed: bool = False           # closed by set_closed until reopened
    # filled in by Building.index()
    opening_ids: list[str] = field(default_factory=list)
    stop_ids: list[str] = field(default_factory=list)      # connector ids stopping here
    asset_ids: list[str] = field(default_factory=list)

    # ---- cached geometry -------------------------------------------------------------
    @cached_property
    def polygon(self) -> Polygon:
        poly = Polygon(self.polygon_coords)
        return poly if poly.is_valid else poly.buffer(0)

    @cached_property
    def area_m2(self) -> float:
        return float(self.polygon.area)

    @cached_property
    def centroid(self) -> tuple[float, float]:
        c = self.polygon.centroid
        return (c.x, c.y)

    @cached_property
    def anchor(self) -> tuple[float, float]:
        """A point guaranteed inside the room: where routes start and end."""
        p = self.polygon.representative_point()
        return (p.x, p.y)

    @property
    def is_circulation(self) -> bool:
        return self.type in CIRCULATION_TYPES or self.room_type in CIRCULATION_ROOM_TYPES

    @property
    def is_production_bay(self) -> bool:
        """Factory bays (hall + *_zone): a destination you may enter, never a shortcut."""
        return self.type == HALL and self.room_type.endswith("_zone")

    def invalidate_geometry(self) -> None:
        for name in ("polygon", "area_m2", "centroid", "anchor"):
            self.__dict__.pop(name, None)


@dataclass
class Opening:
    id: str
    floor_id: str
    space_ids: tuple[str, str]     # may contain OUTSIDE
    kind: str                      # door | opening | entrance
    position: tuple[float, float]
    width_m: float
    step_free: bool
    synthetic: bool = False
    closed: bool = False

    @property
    def is_entrance(self) -> bool:
        return OUTSIDE in self.space_ids

    def other_side(self, space_id: str) -> str:
        a, b = self.space_ids
        return b if a == space_id else a


@dataclass
class Connector:
    """A staircase or lift. `stops` are (floor_id, space_id), sorted bottom to top."""
    id: str
    kind: str                      # stairs | lift
    name: str
    stops: list[tuple[str, str]]
    step_free: bool
    synthetic: bool = False
    closed: bool = False


@dataclass
class Asset:
    id: str
    name: str
    type: str
    category: str                  # furniture | fixture | machine | equipment
    space_id: str
    floor_id: str
    position: tuple[float, float]
    footprint: dict = field(default_factory=dict)
    source: str = "dataset"


class Building:
    """All facts about one building plus its edit state (closures, version)."""

    def __init__(self, meta: dict, floors: Iterable[Floor], spaces: Iterable[Space],
                 openings: Iterable[Opening], connectors: Iterable[Connector], assets: Iterable[Asset]):
        self.meta = meta
        self.floors: dict[str, Floor] = {f.id: f for f in floors}
        self.spaces: dict[str, Space] = {s.id: s for s in spaces}
        self.openings: dict[str, Opening] = {o.id: o for o in openings}
        self.connectors: dict[str, Connector] = {c.id: c for c in connectors}
        self.assets: dict[str, Asset] = {a.id: a for a in assets}
        self.version = 0
        self.index()

    # ---- loading / saving ---------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Building":
        path = Path(path)
        file = path / "building.json" if path.is_dir() else path
        return cls.from_dict(json.loads(file.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, d: dict) -> "Building":
        floors = [Floor(f["id"], int(f["level"]), f["name"], float(f["elevation_m"]), float(f["height_m"]))
                  for f in d["floors"]]
        levels = {f.id: f.level for f in floors}
        spaces = [Space(s["id"], s["floor_id"], s["type"], s["room_type"], s["name"], s.get("ref"),
                        s["polygon"], s.get("synthetic", False), s.get("closed", False)) for s in d["spaces"]]
        openings = [Opening(o["id"], o["floor_id"], tuple(o["space_ids"]), o["kind"], tuple(o["position"]),
                            float(o["width_m"]), bool(o["step_free"]), o.get("synthetic", False), o.get("closed", False))
                    for o in d["openings"]]
        connectors = []
        for v in d["vertical_connectors"]:
            stops = sorted(zip(v["floor_ids"], v["space_ids"]), key=lambda st: levels[st[0]])
            connectors.append(Connector(v["id"], v["kind"], v["name"], stops, bool(v["step_free"]),
                                        v.get("synthetic", False), v.get("closed", False)))
        assets = [Asset(a["id"], a["name"], a["type"], a["category"], a["space_id"], a["floor_id"],
                        tuple(a["position"]), a.get("footprint", {}), a.get("source", "dataset"))
                  for a in d.get("assets", [])]
        b = cls(d.get("building", {}), floors, spaces, openings, connectors, assets)
        for eid in d.get("closed", []):        # older files kept closures only in this list
            b.set_closed(eid)
        return b

    def to_dict(self) -> dict:
        return {
            "building": self.meta,
            "floors": [vars(f).copy() for f in self.floors.values()],
            "spaces": [{"id": s.id, "floor_id": s.floor_id, "type": s.type, "room_type": s.room_type,
                        "name": s.name, "ref": s.ref, "polygon": s.polygon_coords, "synthetic": s.synthetic, "closed": s.closed}
                       for s in self.spaces.values()],
            "openings": [{"id": o.id, "floor_id": o.floor_id, "space_ids": list(o.space_ids), "kind": o.kind,
                          "position": list(o.position), "width_m": o.width_m, "step_free": o.step_free,
                          "synthetic": o.synthetic, "closed": o.closed} for o in self.openings.values()],
            "vertical_connectors": [{"id": c.id, "kind": c.kind, "name": c.name,
                                     "floor_ids": [f for f, _ in c.stops], "space_ids": [s for _, s in c.stops],
                                     "step_free": c.step_free, "synthetic": c.synthetic, "closed": c.closed}
                                    for c in self.connectors.values()],
            "assets": [{"id": a.id, "name": a.name, "category": a.category, "type": a.type,
                        "space_id": a.space_id, "floor_id": a.floor_id, "position": list(a.position),
                        "footprint": a.footprint, "source": a.source} for a in self.assets.values()],
            "closed": sorted(self.closed),     # summary of the per-element flags above
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")

    def index(self) -> None:
        """Rebuild the back-references (space -> openings / stops / assets)."""
        for s in self.spaces.values():
            s.opening_ids, s.stop_ids, s.asset_ids = [], [], []
        for o in self.openings.values():
            for sid in o.space_ids:
                if sid in self.spaces:
                    self.spaces[sid].opening_ids.append(o.id)
        for c in self.connectors.values():
            for _, sid in c.stops:
                if sid in self.spaces:
                    self.spaces[sid].stop_ids.append(c.id)
        for a in self.assets.values():
            if a.space_id in self.spaces:
                self.spaces[a.space_id].asset_ids.append(a.id)
        self.version += 1

    # ---- convenience ----------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.meta.get("id", "")

    @property
    def name(self) -> str:
        return self.meta.get("name", self.id)

    def floor_of(self, space_id: str) -> Floor:
        return self.floors[self.spaces[space_id].floor_id]

    def floors_by_level(self) -> list[Floor]:
        return sorted(self.floors.values(), key=lambda f: f.level)

    def floor_by_level(self, level: int) -> Floor | None:
        return next((f for f in self.floors.values() if f.level == level), None)

    def spaces_on(self, floor_id: str) -> list[Space]:
        return [s for s in self.spaces.values() if s.floor_id == floor_id]

    def entrances(self) -> list[Opening]:
        return [o for o in self.openings.values() if o.is_entrance]

    def element(self, element_id: str) -> Space | Opening | Connector:
        el = self.spaces.get(element_id) or self.openings.get(element_id) or self.connectors.get(element_id)
        if el is None:
            raise KeyError(element_id)
        return el

    @property
    def closed(self) -> set[str]:
        """Ids of closed spaces, openings and connectors (the flags on the elements are the truth)."""
        return {e.id for d in (self.spaces, self.openings, self.connectors) for e in d.values() if e.closed}

    def is_closed(self, element_id: str) -> bool:
        try:
            return self.element(element_id).closed
        except KeyError:
            return False

    # ---- edits (every edit bumps version) ---------------------------------------------------
    def set_closed(self, element_id: str, closed: bool = True) -> None:
        self.element(element_id).closed = closed
        self.version += 1

    def add_opening(self, opening: Opening) -> Opening:
        self.openings[opening.id] = opening
        self.index()
        return opening

    def remove_opening(self, opening_id: str) -> None:
        del self.openings[opening_id]
        self.index()

    def add_space(self, space: Space) -> Space:
        self.spaces[space.id] = space
        self.index()
        return space

    def move_asset(self, asset_id: str, space_id: str, position: tuple[float, float] | None = None) -> Asset:
        asset = self.assets[asset_id]
        space = self.spaces[space_id]
        asset.space_id, asset.floor_id = space_id, space.floor_id
        asset.position = tuple(position) if position else space.anchor
        self.index()
        return asset

    def remove_asset(self, asset_id: str) -> Asset:
        asset = self.assets.pop(asset_id)
        self.index()
        return asset

    def new_id(self, prefix: str, kind: str) -> str:
        """Next free id like '<prefix>-O0123' for kind 'O' (opening), 'S', 'V', 'A'."""
        existing = {**self.spaces, **self.openings, **self.connectors, **self.assets}
        n = 1
        while f"{prefix}-{kind}{n:04d}" in existing:
            n += 1
        return f"{prefix}-{kind}{n:04d}"
