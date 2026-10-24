"""Deterministic facts about a building, used to ground LLM-written location metadata."""

from collections import Counter, defaultdict, deque

from pydantic import BaseModel, Field
from shapely.geometry import Polygon

from app.domain.building import BuildingFile

OUTSIDE = "outside"
OUTDOOR = "outdoor"


class SpaceStats(BaseModel):
    space_id: str
    floor_id: str
    level: int
    name: str
    ref: str
    type: str
    room_type: str
    area_m2: float
    perimeter_m: float
    centroid: list[float]
    doors: int
    entrances: int
    widest_opening_m: float
    neighbours: list[str]
    step_free_access: bool
    equipment: dict[str, int]
    asset_count: int


class FloorStats(BaseModel):
    floor_id: str
    level: int
    name: str
    elevation_m: float
    height_m: float
    space_count: int
    room_count: int
    area_m2: float
    outdoor_area_m2: float
    area_by_type: dict[str, float]
    area_by_room_type: dict[str, float]
    equipment_by_category: dict[str, int]
    top_equipment: dict[str, int]
    entrances: int


class ConnectorStats(BaseModel):
    id: str
    kind: str
    name: str
    levels: list[int]
    step_free: bool


class LocationStats(BaseModel):
    building_id: str
    name: str
    source: str
    license: str
    attribution: str
    floor_count: int
    levels: list[int]
    missing_levels: list[int]
    total_area_m2: float
    space_count: int
    room_count: int
    asset_count: int
    equipment_by_category: dict[str, int]
    top_equipment: dict[str, int]
    machines: dict[str, int]
    has_lift: bool
    has_stairs: bool
    has_step_free_entrance: bool
    step_free_coverage: float
    floors: list[FloorStats] = Field(default_factory=list)
    connectors: list[ConnectorStats] = Field(default_factory=list)
    spaces: dict[str, SpaceStats] = Field(default_factory=dict)


def _step_free_reachable(building: BuildingFile) -> set[str]:
    adj: dict[str, set[str]] = defaultdict(set)
    for o in building.openings:
        if not o.step_free:
            continue
        for a in o.space_ids:
            for b in o.space_ids:
                if a != b:
                    adj[a].add(b)
    for c in building.vertical_connectors:
        if not c.step_free:
            continue
        for a in c.space_ids:
            for b in c.space_ids:
                if a != b:
                    adj[a].add(b)
    seen = {OUTSIDE}
    queue = deque([OUTSIDE])
    while queue:
        for nxt in adj[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _top(counter: Counter[str], n: int) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n])


def compute_location_stats(building: BuildingFile) -> LocationStats:
    levels_by_floor = {f.id: f.level for f in building.floors}
    reachable = _step_free_reachable(building)

    doors: Counter[str] = Counter()
    entrances: Counter[str] = Counter()
    widest: dict[str, float] = defaultdict(float)
    neighbours: dict[str, set[str]] = defaultdict(set)
    floor_entrances: Counter[str] = Counter()
    has_sf_entrance = False
    for o in building.openings:
        is_entrance = OUTSIDE in o.space_ids
        if is_entrance:
            floor_entrances[o.floor_id] += 1
            has_sf_entrance = has_sf_entrance or o.step_free
        for sid in o.space_ids:
            if sid == OUTSIDE:
                continue
            doors[sid] += 1
            if is_entrance:
                entrances[sid] += 1
            widest[sid] = max(widest[sid], o.width_m)
            for other in o.space_ids:
                if other not in (sid, OUTSIDE):
                    neighbours[sid].add(other)

    equip: dict[str, Counter[str]] = defaultdict(Counter)
    floor_cat: dict[str, Counter[str]] = defaultdict(Counter)
    floor_types: dict[str, Counter[str]] = defaultdict(Counter)
    all_cat: Counter[str] = Counter()
    all_types: Counter[str] = Counter()
    machines: Counter[str] = Counter()
    for a in building.assets:
        equip[a.space_id][a.type] += 1
        floor_cat[a.floor_id][a.category] += 1
        floor_types[a.floor_id][a.type] += 1
        all_cat[a.category] += 1
        all_types[a.type] += 1
        if a.category == "machine":
            machines[a.type] += 1

    spaces: dict[str, SpaceStats] = {}
    for s in building.spaces:
        poly = Polygon(s.polygon)
        c = poly.centroid
        e = equip.get(s.id, Counter())
        spaces[s.id] = SpaceStats(
            space_id=s.id,
            floor_id=s.floor_id,
            level=levels_by_floor.get(s.floor_id, 0),
            name=s.name,
            ref=s.ref,
            type=s.type,
            room_type=s.room_type,
            area_m2=round(abs(poly.area), 2),
            perimeter_m=round(poly.length, 2),
            centroid=[round(c.x, 2), round(c.y, 2)] if not c.is_empty else [0.0, 0.0],
            doors=doors[s.id],
            entrances=entrances[s.id],
            widest_opening_m=widest[s.id],
            neighbours=sorted(neighbours[s.id]),
            step_free_access=s.id in reachable,
            equipment=dict(sorted(e.items())),
            asset_count=sum(e.values()),
        )

    floors: list[FloorStats] = []
    for f in sorted(building.floors, key=lambda f: f.level):
        fs = [s for s in spaces.values() if s.floor_id == f.id]
        by_type: defaultdict[str, float] = defaultdict(float)
        by_room: defaultdict[str, float] = defaultdict(float)
        outdoor = 0.0
        for ss in fs:
            if ss.type == OUTDOOR:
                outdoor += ss.area_m2
                continue
            by_type[ss.type] += ss.area_m2
            by_room[ss.room_type] += ss.area_m2
        floors.append(
            FloorStats(
                floor_id=f.id,
                level=f.level,
                name=f.name,
                elevation_m=f.elevation_m,
                height_m=f.height_m,
                space_count=len(fs),
                room_count=sum(1 for s in fs if s.type == "room"),
                area_m2=round(sum(by_type.values()), 2),
                outdoor_area_m2=round(outdoor, 2),
                area_by_type={k: round(v, 2) for k, v in by_type.items()},
                area_by_room_type={k: round(v, 2) for k, v in by_room.items()},
                equipment_by_category=dict(floor_cat[f.id]),
                top_equipment=_top(floor_types[f.id], 10),
                entrances=floor_entrances[f.id],
            )
        )

    connectors = [
        ConnectorStats(
            id=c.id,
            kind=c.kind,
            name=c.name,
            levels=sorted(levels_by_floor[i] for i in c.floor_ids if i in levels_by_floor),
            step_free=c.step_free,
        )
        for c in building.vertical_connectors
    ]

    levels = sorted(levels_by_floor.values())
    missing = (
        [lv for lv in range(levels[0], levels[-1] + 1) if lv not in levels_by_floor.values()]
        if levels
        else []
    )
    indoor = [s for s in spaces.values() if s.type != OUTDOOR]
    coverage = sum(s.step_free_access for s in indoor) / len(indoor) if indoor else 0.0

    return LocationStats(
        building_id=building.building.id,
        name=building.building.name,
        source=building.building.source,
        license=building.building.license,
        attribution=building.building.attribution,
        floor_count=len(building.floors),
        levels=levels,
        missing_levels=missing,
        total_area_m2=round(sum(f.area_m2 for f in floors), 2),
        space_count=len(spaces),
        room_count=sum(f.room_count for f in floors),
        asset_count=len(building.assets),
        equipment_by_category=dict(all_cat),
        top_equipment=_top(all_types, 15),
        machines=dict(machines),
        has_lift=any(c.kind == "lift" for c in building.vertical_connectors),
        has_stairs=any(c.kind == "stairs" for c in building.vertical_connectors),
        has_step_free_entrance=has_sf_entrance,
        step_free_coverage=round(coverage, 3),
        floors=floors,
        connectors=connectors,
        spaces=spaces,
    )


def space_brief(stats: SpaceStats) -> str:
    """One compact line describing a space, for batching into a cheap LLM prompt."""
    short_id = (
        stats.space_id.split("-", 1)[-1]
        if "-F" not in stats.space_id
        else (stats.space_id[stats.space_id.index("-F") + 1 :])
    )
    kind = stats.type if stats.type == stats.room_type else f"{stats.type}/{stats.room_type}"
    parts = [
        short_id,
        stats.name or "(unnamed)",
        kind,
        f"{stats.area_m2:.1f} m²",
        f"{stats.doors} door{'s' if stats.doors != 1 else ''}",
    ]
    if stats.equipment:
        top = sorted(stats.equipment.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        parts.append(", ".join(f"{t}×{n}" for t, n in top))
    parts.append("step-free" if stats.step_free_access else "not step-free")
    return " | ".join(parts)
