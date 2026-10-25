"""Assemble a building.json from plan primitives (SVG-parsed or vision-traced) + equipment.csv.

Geometry, space types and opening positions/kinds come from the plan; ids, names, room
types and assets from equipment.csv where a space has equipment. What neither source holds
(floor heights, door widths, room types of empty rooms) gets a default and is returned as a
gap, which the reconstruct agent may fill through `Overrides` before assembling again.
"""

import csv
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import Point, Polygon

from app.domain.building import BuildingFile
from app.pipeline.tools.svgplan import PlanFloor, Transform

DEFAULT_HEIGHT_M = 3.0
DEFAULT_DOOR_WIDTH_M = 0.9
MAX_OPENING_WIDTH_M = 50.0
ASSET_SNAP_M = 0.5  # an asset centre this close to a space counts as inside it
CONNECTOR_OVERLAP = 0.3  # share of the smaller stair/lift footprint that must overlap
TYPICAL_ROOM_M2 = 15.0  # scale guess when nothing else fixes it
# Room type of a space without equipment; `None` = the plan can't tell (a gap).
DEFAULT_ROOM_TYPES: dict[str, tuple[str, bool]] = {
    "corridor": ("corridor", True),
    "stair": ("stairwell", True),
    "lift": ("lift", True),
    "hall": ("lobby", False),
    "outdoor": ("balcony", False),
    "room": ("generic_room", False),
}
CONNECTOR_KINDS = {"stair": ("stairs", "Stairs"), "lift": ("lift", "Lift")}
SPACE_NUMBER = re.compile(r"-S(\d+)$")


@dataclass
class Overrides:
    """What the agent decided for gaps; applied on every assembly."""

    floors: dict[int, dict[str, Any]] = field(default_factory=dict)  # elevation_m/height_m/name
    spaces: dict[str, dict[str, str]] = field(default_factory=dict)  # room_type/name
    opening_widths: dict[str, float] = field(default_factory=dict)
    scales: dict[int, float] = field(default_factory=dict)  # metres per pixel


@dataclass
class Reconstruction:
    building: BuildingFile
    gaps: list[dict[str, Any]]
    stats: dict[str, Any]


def read_equipment(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _floor_rows(rows: list[dict[str, str]], level: int) -> list[dict[str, str]]:
    return [r for r in rows if _int(r.get("floor_level")) == level]


def _int(value: str | None) -> int | None:
    try:
        return int(float(value or ""))
    except ValueError:
        return None


def _float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value or "")
    except ValueError:
        return default


def fit_transforms(
    floors: list[PlanFloor], rows: list[dict[str, str]]
) -> tuple[dict[int, Transform], list[dict[str, Any]]]:
    """Per-floor pixel→metre transforms: fitted to the CSV, else borrowed from fitted floors."""
    from app.pipeline.tools.svgplan import fit_transform

    transforms: dict[int, Transform] = {}
    gaps: list[dict[str, Any]] = []
    for floor in floors:
        if floor.transform is not None:
            transforms[floor.level] = floor.transform
            continue
        floor_rows = _floor_rows(rows, floor.level)
        fit = fit_transform(
            floor.asset_centres_px, [(_float(r["x_m"]), _float(r["y_m"])) for r in floor_rows]
        )
        if fit is not None and fit[1] < 1.0:
            transforms[floor.level] = fit[0]
    fitted = dict(transforms)
    for floor in floors:
        if floor.level in transforms:
            continue
        if fitted:
            k = statistics.median(t.k for t in fitted.values())
            transforms[floor.level] = _align(floor, k, floors, fitted)
            gaps.append(
                {
                    "kind": "floor_alignment",
                    "target": floor.level,
                    "detail": "no equipment to fit this floor; scale and offset borrowed from "
                    "other floors (stair/lift alignment)",
                }
            )
        else:
            areas = [Polygon(s.polygon_px).area for s in floor.spaces if len(s.polygon_px) >= 3]
            k = (statistics.median(areas) / TYPICAL_ROOM_M2) ** 0.5 if areas else 10.0
            us = [p[0] for s in floor.spaces for p in s.polygon_px] or [0.0]
            vs = [p[1] for s in floor.spaces for p in s.polygon_px] or [0.0]
            transforms[floor.level] = Transform(k, min(us), max(vs))
            gaps.append(
                {
                    "kind": "scale",
                    "target": floor.level,
                    "detail": f"no equipment.csv positions; scale guessed at {1 / k:.4f} m/px "
                    f"(median room {TYPICAL_ROOM_M2:.0f} m²). Fix with set_scale.",
                }
            )
    return transforms, gaps


def _align(
    floor: PlanFloor, k: float, floors: list[PlanFloor], fitted: dict[int, Transform]
) -> Transform:
    """Offset that puts this floor's stairs/lifts on top of a fitted floor's."""
    anchors_m = [
        fitted[f.level].to_m(*Polygon(s.polygon_px).centroid.coords[0])
        for f in floors
        if f.level in fitted
        for s in f.spaces
        if s.type in CONNECTOR_KINDS
    ]
    own = [
        Polygon(s.polygon_px).centroid.coords[0] for s in floor.spaces if s.type in CONNECTOR_KINDS
    ]
    best: tuple[int, Transform] | None = None
    for u, v in own:
        for x, y in anchors_m:
            candidate = Transform(k, u - k * x, v + k * y)
            score = sum(
                any(
                    np.hypot(*np.subtract(candidate.to_m(a, b), anchor)) < 0.5
                    for anchor in anchors_m
                )
                for a, b in own
            )
            if best is None or score > best[0]:
                best = (score, candidate)
    if best is not None:
        return best[1]
    # No stairs or lifts to match: centre this floor on a fitted one.
    level, transform = next(iter(fitted.items()))
    reference = next(f for f in floors if f.level == level)
    ref_box = shapely.bounds(shapely.union_all([Polygon(s.polygon_px) for s in reference.spaces]))
    rx, ry = transform.to_m((ref_box[0] + ref_box[2]) / 2, (ref_box[1] + ref_box[3]) / 2)
    box = shapely.bounds(shapely.union_all([Polygon(s.polygon_px) for s in floor.spaces]))
    u, v = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return Transform(k, u - k * rx, v + k * ry)


def _round(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 3), round(point[1], 3)]


def _floor_name(level: int) -> str:
    if level == 0:
        return "Ground floor"
    return f"Basement {-level}" if level < 0 else f"Floor {level}"


def _label(room_type: str) -> str:
    return room_type.replace("_", " ").capitalize()


def assemble(
    building_id: str,
    floors: list[PlanFloor],
    rows: list[dict[str, str]],
    overrides: Overrides | None = None,
    source_ref: dict[str, Any] | None = None,
) -> Reconstruction:
    overrides = overrides or Overrides()
    floors = sorted(floors, key=lambda f: f.level)
    transforms, gaps = fit_transforms(floors, rows)
    for level, metres_per_px in overrides.scales.items():
        if level in transforms:
            old = transforms[level]
            transforms[level] = Transform(1 / metres_per_px, old.bx, old.by)
            gaps = [g for g in gaps if not (g["kind"] == "scale" and g["target"] == level)]
    csv_building = next((r["building_id"] for r in rows if r.get("building_id")), None)

    def new_id(csv_space_id: str) -> str:
        if csv_building and csv_space_id.startswith(csv_building):
            return building_id + csv_space_id[len(csv_building) :]
        return f"{building_id}-{csv_space_id}"

    out_floors: list[dict[str, Any]] = []
    out_spaces: list[dict[str, Any]] = []
    out_openings: list[dict[str, Any]] = []
    space_map: dict[str, str] = {}  # csv space id -> new space id
    polygons: dict[str, Polygon] = {}
    defaulted_floors: list[int] = []
    defaulted_widths = 0

    heights = {
        f.level: float(overrides.floors.get(f.level, {}).get("height_m", DEFAULT_HEIGHT_M))
        for f in floors
    }
    for floor in floors:
        level = floor.level
        transform = transforms[level]
        floor_id = f"{building_id}-F{level}"
        floor_rows = _floor_rows(rows, level)
        custom = overrides.floors.get(level, {})
        if level >= 0:
            elevation = sum(heights[lv] for lv in heights if 0 <= lv < level)
        else:
            elevation = -sum(heights[lv] for lv in heights if level <= lv < 0)
        if "height_m" not in custom and "elevation_m" not in custom:
            defaulted_floors.append(level)
        out_floors.append(
            {
                "id": floor_id,
                "level": level,
                "name": custom.get("name")
                or floor.floor_name
                or next((r["floor_name"] for r in floor_rows if r.get("floor_name")), None)
                or _floor_name(level),
                "elevation_m": round(float(custom.get("elevation_m", elevation)), 3),
                "height_m": heights[level],
            }
        )

        shapes = [
            Polygon([transform.to_m(u, v) for u, v in s.polygon_px]).buffer(0)
            if len(s.polygon_px) >= 3
            else Polygon()
            for s in floor.spaces
        ]
        # Which CSV space each drawn space is: vote with the equipment standing in it.
        votes: list[Counter[tuple[str, str, str, str]]] = [Counter() for _ in shapes]
        for row in floor_rows:
            index = _containing(shapes, (_float(row["x_m"]), _float(row["y_m"])))
            if index is not None and row.get("space_id"):
                key = (row["space_id"], row.get("space_name", ""), row.get("room_ref", ""))
                votes[index][(*key, row.get("room_type", ""))] += 1
        refs: dict[int, str] = {}
        for text, (u, v) in floor.labels:
            index = _containing(shapes, transform.to_m(u, v), snap=0.0)
            if index is not None:
                refs.setdefault(index, text)

        used = {new_id(r["space_id"]) for r in floor_rows if r.get("space_id")}
        last_number = 0
        floor_space_ids: list[str] = []
        for index, (plan, shape) in enumerate(zip(floor.spaces, shapes, strict=True)):
            known = votes[index].most_common(1)[0][0] if votes[index] else None
            space_id = new_id(known[0]) if known else ""
            if not space_id or space_id in polygons:
                # Kit ids rise in drawing order (with gaps): continue after the previous space.
                counter = last_number + 1
                space_id = f"{building_id}-F{level}-S{counter:03d}"
                while space_id in used or space_id in polygons:
                    counter += 1
                    space_id = f"{building_id}-F{level}-S{counter:03d}"
            match = SPACE_NUMBER.search(space_id)
            last_number = int(match[1]) if match else last_number + 1
            if known:
                space_map.setdefault(known[0], space_id)
            ref = refs.get(index) or (known[2] if known else "") or ""
            room_type = (known[3] if known else "") or plan.room_type
            if not room_type:
                room_type, certain = DEFAULT_ROOM_TYPES.get(plan.type, ("generic_room", False))
                if not certain and space_id not in overrides.spaces:
                    gaps.append(
                        {
                            "kind": "space_room_type",
                            "target": space_id,
                            "detail": f"{plan.type} without equipment or label; "
                            f"defaulted to room_type '{room_type}'",
                        }
                    )
            custom_space = overrides.spaces.get(space_id, {})
            room_type = custom_space.get("room_type", room_type)
            name = (
                custom_space.get("name")
                or (known[1] if known else "")
                or plan.name
                or f"{_label(room_type)} {ref or index + 1}".strip()
            )
            exterior = [_round(transform.to_m(u, v)) for u, v in plan.polygon_px]
            out_spaces.append(
                {
                    "id": space_id,
                    "floor_id": floor_id,
                    "type": plan.type,
                    "room_type": room_type,
                    "name": name,
                    "ref": ref,
                    "polygon": exterior,
                    "synthetic": False,
                }
            )
            polygons[space_id] = shape
            floor_space_ids.append(space_id)

        exteriors = np.array([s.exterior if not s.is_empty else None for s in shapes])
        for number, opening in enumerate(floor.openings, start=1):
            position = transform.to_m(*opening.position_px)
            distances = shapely.distance(exteriors, Point(position))
            order = [
                i for i in np.argsort(distances) if shapes[i].is_valid and not shapes[i].is_empty
            ]
            if not order:
                continue
            first = floor_space_ids[order[0]]
            if opening.kind == "entrance" or len(order) < 2:
                pair = [first, "outside"]
            else:
                pair = [first, floor_space_ids[order[1]]]
            opening_id = f"{building_id}-F{level}-O{number:04d}"
            width = overrides.opening_widths.get(opening_id)
            if width is None:
                width = DEFAULT_DOOR_WIDTH_M
                if opening.kind == "opening" and pair[1] != "outside":
                    shared = (
                        shapes[order[0]].exterior.intersection(shapes[order[1]].buffer(0.05)).length
                    )
                    width = min(max(shared, DEFAULT_DOOR_WIDTH_M), MAX_OPENING_WIDTH_M)
                else:
                    defaulted_widths += 1
            out_openings.append(
                {
                    "id": opening_id,
                    "floor_id": floor_id,
                    "space_ids": pair,
                    "kind": opening.kind,
                    "position": _round(position),
                    "width_m": round(float(width), 3),
                    "step_free": True,
                    "synthetic": False,
                }
            )

    if defaulted_floors:
        gaps.append(
            {
                "kind": "floor_heights",
                "target": defaulted_floors,
                "detail": f"heights not drawn; defaulted to {DEFAULT_HEIGHT_M} m per storey",
            }
        )
    if defaulted_widths:
        gaps.append(
            {
                "kind": "opening_widths",
                "target": defaulted_widths,
                "detail": f"door widths not drawn; {defaulted_widths} defaulted to "
                f"{DEFAULT_DOOR_WIDTH_M} m",
            }
        )

    connectors = _connectors(building_id, out_floors, out_spaces, polygons)
    assets, unplaced = _assets(building_id, rows, space_map, out_spaces, polygons)
    if unplaced:
        gaps.append(
            {
                "kind": "asset_space",
                "target": unplaced[:20],
                "detail": f"{len(unplaced)} assets lie in no drawn space and were left out",
            }
        )
    name = next((f.building_name for f in floors if f.building_name), None) or building_id
    data = {
        "building": {
            "id": building_id,
            "name": name,
            "source": "reconstructed",
            "source_ref": source_ref or {},
            "license": "unknown",
            "attribution": "Reconstructed from uploaded floor plans",
            "split": "uploaded",
            "units": "m",
        },
        "floors": out_floors,
        "spaces": out_spaces,
        "openings": out_openings,
        "vertical_connectors": connectors,
        "assets": assets,
    }
    stats = {
        "floors": len(out_floors),
        "spaces": len(out_spaces),
        "spaces_from_csv": len(space_map),
        "openings": len(out_openings),
        "vertical_connectors": len(connectors),
        "assets": len(assets),
        "methods": {f.level: f.source for f in floors},
        "scale_m_per_px": {lv: round(1 / t.k, 6) for lv, t in transforms.items()},
    }
    return Reconstruction(BuildingFile.model_validate(data), gaps, stats)


def _containing(
    shapes: list[Polygon], point: tuple[float, float], snap: float = ASSET_SNAP_M
) -> int | None:
    """Smallest space containing `point`, else the nearest within `snap` metres."""
    probe = Point(point)
    inside = [i for i, s in enumerate(shapes) if not s.is_empty and s.covers(probe)]
    if inside:
        return min(inside, key=lambda i: shapes[i].area)
    if snap <= 0 or not shapes:
        return None
    distances = [s.distance(probe) if not s.is_empty else np.inf for s in shapes]
    nearest = int(np.argmin(distances))
    return nearest if distances[nearest] <= snap else None


def _connectors(
    building_id: str,
    floors: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    polygons: dict[str, Polygon],
) -> list[dict[str, Any]]:
    """Chain stair/lift spaces that overlap on the nearest floor above that has one."""
    levels = {f["id"]: f["level"] for f in floors}
    ordered = sorted(floors, key=lambda f: f["level"])
    out: list[dict[str, Any]] = []
    for space_type, (kind, label) in CONNECTOR_KINDS.items():
        by_floor = {
            f["id"]: [
                s["id"] for s in spaces if s["floor_id"] == f["id"] and s["type"] == space_type
            ]
            for f in ordered
        }
        above: dict[str, str] = {}
        taken: set[str] = set()
        for position, floor in enumerate(ordered):
            for space_id in by_floor[floor["id"]]:
                shape = polygons[space_id]
                for upper in ordered[position + 1 :]:
                    scored = [
                        (
                            shape.intersection(polygons[o]).area
                            / min(shape.area, polygons[o].area),
                            o,
                        )
                        for o in by_floor[upper["id"]]
                        if o not in taken and min(shape.area, polygons[o].area) > 0
                    ]
                    scored = [c for c in scored if c[0] >= CONNECTOR_OVERLAP]
                    if scored:
                        target = max(scored)[1]
                        above[space_id] = target
                        taken.add(target)
                        break
        starts = [s for s in above if s not in taken]
        for start in sorted(starts, key=lambda s: (levels[_floor_of(s, spaces)], s)):
            chain = [start]
            while chain[-1] in above:
                chain.append(above[chain[-1]])
            number = sum(1 for c in out if c["kind"] == kind) + 1
            out.append(
                {
                    "id": f"{building_id}-V{len(out) + 1:02d}",
                    "kind": kind,
                    "name": f"{label} {number}",
                    "floor_ids": [_floor_of(s, spaces) for s in chain],
                    "space_ids": chain,
                    "step_free": kind == "lift",
                    "synthetic": False,
                }
            )
    return out


def _floor_of(space_id: str, spaces: list[dict[str, Any]]) -> str:
    return str(next(s["floor_id"] for s in spaces if s["id"] == space_id))


def _assets(
    building_id: str,
    rows: list[dict[str, str]],
    space_map: dict[str, str],
    spaces: list[dict[str, Any]],
    polygons: dict[str, Polygon],
) -> tuple[list[dict[str, Any]], list[str]]:
    floor_spaces: dict[str, list[str]] = {}
    for space in spaces:
        floor_spaces.setdefault(space["floor_id"], []).append(space["id"])
    assets: list[dict[str, Any]] = []
    unplaced: list[str] = []
    for row in rows:
        level = _int(row.get("floor_level"))
        floor_id = f"{building_id}-F{level}"
        position = (_float(row.get("x_m")), _float(row.get("y_m")))
        space_id = space_map.get(row.get("space_id", ""))
        if space_id is None and floor_id in floor_spaces:
            candidates = floor_spaces[floor_id]
            index = _containing([polygons[s] for s in candidates], position)
            space_id = candidates[index] if index is not None else None
        if space_id is None:
            unplaced.append(row.get("asset_id", "?"))
            continue
        assets.append(
            {
                "id": row["asset_id"],
                "name": row.get("name", "") or row.get("type", ""),
                "category": row.get("category", ""),
                "type": row.get("type", ""),
                "space_id": space_id,
                "floor_id": floor_id,
                "position": _round(position),
                "footprint": {
                    "width_m": _float(row.get("width_m"), 0.5),
                    "depth_m": _float(row.get("depth_m"), 0.5),
                    "rotation_deg": _float(row.get("rotation_deg")),
                },
                "source": row.get("source", "") or "equipment.csv",
            }
        )
    return assets, unplaced
