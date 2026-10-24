"""Floor-plan geometry: spaces, walls, openings, stair/lift markers and connector lines.

Conventions (kept from the Blender pipeline, see `.claude/skills/creation_pipeline.md`):

- Every space is a flat floor mesh carrying its polygon verbatim at z = floor elevation.
- The source has no walls, so walls are built from room outlines: a band `WALL_T` wide
  inset inside each outline, `height_m` of the floor high, with a gap for every opening of
  that space (opening position +- width/2 projected onto the nearest outline edge).
- `hall` zones and `outdoor` spaces stay open: no walls, only thin floor lines around halls
  and wide threshold strips where the data has an opening into a hall.
- Overlapping storeys (an 8 m hall with a 3.5 m office level over part of it): each wall
  stretch stops under any higher floor slab that covers it in 2D.
- Stairs are the stairwell space plus a low stepped marker; lifts get a car-sized box; each
  vertical connector is a thin line through the representative points of its stops.
- Openings are instances of a unit box (like assets): node origin = (x, y, elevation),
  width along the outline edge, so the validator reads position/width from the node matrix.

Node tree: `building` -> `floor:<floor_id>` -> `<space_id>`, `walls:<space_id>`,
`marker:<space_id>`, `<opening_id>`, `hall_lines:<floor_id>`; connectors hang off
`building` because they span floors. All static meshes are stored in world coordinates
(glTF frame) under identity node transforms.
"""

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.domain.building import BuildingFile, FloorIn, OpeningIn, SpaceIn
from app.pipeline.tools.geo3d.frame import asset_matrix, to_gltf_matrix, to_gltf_points
from app.pipeline.tools.geo3d.mesh_utils import (
    concat,
    extrude,
    oriented_box,
    polygons_of,
    triangulate_ring,
)

WALL_T = 0.12  # wall thickness, inset inside the room outline (m)
DOOR_SNAP_MAX = 1.0  # openings further than this from a space outline cut no gap (m)
STRIP_DEPTH = 0.2  # threshold strip depth across a door (m)
HALL_STRIP_DEPTH = 0.6  # wide threshold strip where an opening touches a hall (m)
STRIP_HEIGHT = 0.02
HALL_LINE_W = 0.08
CONNECTOR_RADIUS = 0.06
NO_WALL_TYPES = frozenset({"hall", "outdoor"})
EPS = 1e-6

RGBA = tuple[int, int, int, int]

# Fill colours, by room_type first, then by space type (also used by the preview).
ROOM_TYPE_COLORS: dict[str, RGBA] = {
    "production_zone": (253, 240, 178, 255),
    "additive_zone": (250, 220, 160, 255),
    "assembly_zone": (247, 230, 150, 255),
    "warehouse_zone": (220, 220, 220, 255),
    "logistics_zone": (210, 214, 222, 255),
    "stairwell": (243, 177, 133, 255),
    "lift": (178, 165, 210, 255),
    "corridor": (222, 222, 222, 255),
    "toilet": (190, 220, 235, 255),
    "bathroom": (190, 220, 235, 255),
    "kitchen": (214, 236, 200, 255),
    "server_room": (205, 200, 230, 255),
    "technical_room": (205, 200, 230, 255),
    "balcony": (200, 228, 190, 255),
}
TYPE_COLORS: dict[str, RGBA] = {
    "room": (242, 238, 230, 255),
    "corridor": (222, 222, 222, 255),
    "stair": (243, 177, 133, 255),
    "lift": (178, 165, 210, 255),
    "hall": (253, 240, 178, 255),
    "outdoor": (200, 228, 190, 255),
}
WALL_COLOR: RGBA = (70, 70, 75, 255)
HALL_LINE_COLOR: RGBA = (230, 190, 30, 255)
MARKER_COLOR: RGBA = (150, 100, 70, 255)
CONNECTOR_COLOR: RGBA = (200, 40, 40, 255)
OPENING_COLORS: dict[str, RGBA] = {
    "door": (140, 70, 20, 255),
    "entrance": (210, 40, 40, 255),
    "opening": (150, 150, 150, 255),
}


def space_color(space_type: str, room_type: str) -> RGBA:
    return ROOM_TYPE_COLORS.get(room_type) or TYPE_COLORS.get(space_type, (235, 235, 235, 255))


@dataclass
class FloorplanResult:
    scene: trimesh.Scene
    index: dict[str, dict[str, Any]]
    stats: dict[str, Any] = field(default_factory=dict)


_MATERIALS: dict[RGBA, Any] = {}


def material(rgba: RGBA) -> Any:
    """One shared PBR material per colour (keeps the GLB small)."""
    mat = _MATERIALS.get(rgba)
    if mat is None:
        mat = trimesh.visual.material.PBRMaterial(
            name=f"c{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}",
            baseColorFactor=list(rgba),
            metallicFactor=0.0,
            roughnessFactor=0.9,
            doubleSided=True,
        )
        _MATERIALS[rgba] = mat
    return mat


def make_mesh(verts_dataset: np.ndarray, faces: np.ndarray, rgba: RGBA) -> trimesh.Trimesh:
    """Mesh in the glTF frame from dataset-frame vertices, without trimesh processing
    (so the vertices stay exactly as given)."""
    mesh = trimesh.Trimesh(
        vertices=to_gltf_points(verts_dataset), faces=np.asarray(faces), process=False
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=material(rgba))
    return mesh


def unit_box_gltf(rgba: RGBA) -> trimesh.Trimesh:
    """Unit box (x, y in [-0.5, 0.5], z in [0, 1], dataset frame) converted to glTF."""
    mesh = trimesh.creation.box(extents=[1, 1, 1])
    mesh.apply_translation([0, 0, 0.5])
    mesh.apply_transform(to_gltf_matrix())
    mesh.visual = trimesh.visual.TextureVisuals(material=material(rgba))
    return mesh


def floor_node(floor_id: str) -> str:
    return f"floor:{floor_id}"


def _nearest_edge(xy: np.ndarray, p: np.ndarray) -> tuple[float, np.ndarray, float]:
    """(distance, projected point, edge angle in radians) of point p to a closed ring."""
    a = xy
    b = np.roll(xy, -1, axis=0)
    ab = b - a
    len2 = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.where(len2 > 0, len2, 1.0), 0.0, 1.0)
    proj = a + ab * t[:, None]
    d = np.linalg.norm(proj - p, axis=1)
    d = np.where(len2 > 0, d, np.inf)
    k = int(np.argmin(d))
    return float(d[k]), proj[k], math.atan2(float(ab[k, 1]), float(ab[k, 0]))


def _door_gap(xy: np.ndarray, op: OpeningIn) -> Polygon | None:
    p = np.asarray(op.position[:2], dtype=np.float64)
    dist, proj, ang = _nearest_edge(xy, p)
    if dist > DOOR_SNAP_MAX or op.width_m <= 0:
        return None
    c, s = math.cos(ang), math.sin(ang)
    hl, hw = op.width_m / 2, WALL_T * 3
    corners = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    return Polygon([(proj[0] + u * c - v * s, proj[1] + u * s + v * c) for u, v in corners])


def _safe_polygon(points: list[list[float]]) -> Polygon:
    poly = Polygon([(p[0], p[1]) for p in points])
    if not poly.is_valid:
        fixed = polygons_of(poly.buffer(0))
        poly = max(fixed, key=lambda g: g.area) if fixed else poly
    return poly


@dataclass
class _Cover:
    elevation: float
    geom: Any  # prepared shapely geometry


def _covers_above(
    floors: list[FloorIn], polys: dict[str, Polygon], spaces: list[SpaceIn]
) -> dict[str, list["_Cover"]]:
    """Per floor id: higher floors whose slab starts below this floor's top (sorted)."""
    by_floor: dict[str, list[Polygon]] = defaultdict(list)
    for s in spaces:
        if s.type != "outdoor" and s.id in polys:
            by_floor[s.floor_id].append(polys[s.id])
    union_cache: dict[str, Any] = {}
    out: dict[str, list[_Cover]] = {}
    for f in floors:
        top = f.elevation_m + f.height_m
        covers: list[_Cover] = []
        for g in sorted(floors, key=lambda g: g.elevation_m):
            if f.elevation_m + EPS < g.elevation_m < top - EPS and by_floor.get(g.id):
                if g.id not in union_cache:
                    u = unary_union(by_floor[g.id]).buffer(EPS)
                    shapely.prepare(u)
                    union_cache[g.id] = u
                covers.append(_Cover(g.elevation_m, union_cache[g.id]))
        out[f.id] = covers
    return out


def _wall_mesh(
    poly: Polygon,
    xy: np.ndarray,
    openings: list[OpeningIn],
    floor: FloorIn,
    covers: list["_Cover"],
) -> tuple[np.ndarray, np.ndarray, int, float]:
    """(verts, faces, n_gaps, min_height) of the wall band of one space (dataset frame)."""
    inner = poly.buffer(-WALL_T, join_style="mitre")
    band = poly.difference(inner) if not inner.is_empty else poly
    gaps = [g for g in (_door_gap(xy, op) for op in openings) if g is not None]
    if gaps:
        band = band.difference(unary_union(gaps))
    remaining = band
    parts: list[tuple[np.ndarray, np.ndarray]] = []
    min_h = floor.height_m
    for cov in covers:
        if remaining.is_empty:
            break
        if not cov.geom.intersects(remaining):
            continue
        under = remaining.intersection(cov.geom)
        pieces = polygons_of(under)
        if pieces:
            h = cov.elevation - floor.elevation_m
            parts.append(extrude(pieces, floor.elevation_m, floor.elevation_m + h))
            min_h = min(min_h, h)
        remaining = remaining.difference(cov.geom)
    pieces = polygons_of(remaining)
    if pieces:
        parts.append(extrude(pieces, floor.elevation_m, floor.elevation_m + floor.height_m))
    v, f = concat(parts)
    return v, f, len(gaps), min_h


def _marker_mesh(space: SpaceIn, poly: Polygon, floor: FloorIn) -> tuple[np.ndarray, np.ndarray]:
    inner = poly.buffer(-WALL_T * 2)
    if inner.is_empty or inner.area < 0.05:
        inner = poly
    rect = inner.minimum_rotated_rectangle
    coords = np.asarray(rect.exterior.coords)[:4] if isinstance(rect, Polygon) else None
    if coords is None or len(coords) < 4:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    e0, e1 = coords[1] - coords[0], coords[2] - coords[1]
    l0, l1 = float(np.linalg.norm(e0)), float(np.linalg.norm(e1))
    long_vec, length, width = (e0, l0, l1) if l0 >= l1 else (e1, l1, l0)
    ang = math.atan2(float(long_vec[1]), float(long_vec[0]))
    cx, cy = (float(v) for v in coords.mean(axis=0))
    z0 = floor.elevation_m
    c, s = math.cos(ang), math.sin(ang)

    def strip(u0: float, u1: float, w: float) -> Polygon:
        corners = [(u0, -w / 2), (u1, -w / 2), (u1, w / 2), (u0, w / 2)]
        return Polygon([(cx + u * c - v * s, cy + u * s + v * c) for u, v in corners])

    # clip to the (inset) outline so L-shaped stairwells don't spill into neighbours
    if space.type == "lift":
        h = min(2.4, floor.height_m)
        car = strip(-length * 0.4, length * 0.4, width * 0.8).intersection(inner)
        return extrude(polygons_of(car), z0, z0 + h)
    # stairs: a flight of low steps rising along the long side, half a storey high
    n = 8
    rise = min(floor.height_m, 4.0) * 0.5
    step = length / n
    parts = []
    for i in range(n):
        u0 = -length / 2 + step * i
        piece = strip(u0, u0 + step, width * 0.9).intersection(inner)
        parts.append(extrude(polygons_of(piece), z0, z0 + rise * (i + 1) / n))
    return concat(parts)


def _hall_lines(polys: list[Polygon], z: float) -> tuple[np.ndarray, np.ndarray]:
    lines = [
        p.exterior.buffer(HALL_LINE_W / 2, cap_style="flat", join_style="mitre") for p in polys
    ]
    return extrude(polygons_of(unary_union(lines)), z, z + 0.01)


def build_floorplan(building: BuildingFile) -> FloorplanResult:
    t0 = time.perf_counter()
    scene = trimesh.Scene()
    index: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = defaultdict(int)

    def add_node(
        name: str,
        parent: str,
        extras: dict[str, Any],
        geometry: str | None = None,
        matrix: np.ndarray | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"frame_to": name, "frame_from": parent, "metadata": extras}
        if geometry is not None:
            kwargs["geometry"] = geometry
        if matrix is not None:
            kwargs["matrix"] = matrix
        scene.graph.update(**kwargs)
        index[name] = extras

    meta = building.building
    add_node("building", str(scene.graph.base_frame), {"kind": "building", "id": meta.id})
    floors = {f.id: f for f in building.floors}
    for fl in sorted(building.floors, key=lambda fl: fl.level):
        add_node(
            floor_node(fl.id),
            "building",
            {
                "kind": "floor",
                "id": fl.id,
                "level": fl.level,
                "elevation_m": fl.elevation_m,
                "height_m": fl.height_m,
            },
        )

    polys: dict[str, Polygon] = {}
    rings: dict[str, np.ndarray] = {}
    for s in building.spaces:
        if s.floor_id not in floors or len(s.polygon) < 3:
            stats["skipped_spaces"] += 1
            continue
        polys[s.id] = _safe_polygon(s.polygon)
        rings[s.id] = np.asarray([p[:2] for p in s.polygon], dtype=np.float64)
    spaces_by_id = {s.id: s for s in building.spaces}
    openings_by_space: dict[str, list[OpeningIn]] = defaultdict(list)
    for op in building.openings:
        for sid in op.space_ids:
            openings_by_space[sid].append(op)
    covers = _covers_above(building.floors, polys, building.spaces)
    stats["overlapping_floors"] = sorted(fid for fid, c in covers.items() if c)

    hall_polys: dict[str, list[Polygon]] = defaultdict(list)
    for s in building.spaces:
        if s.id not in polys:
            continue
        floor = floors[s.floor_id]
        parent = floor_node(s.floor_id)
        xy = rings[s.id]
        # space floor: the data polygon verbatim at z = elevation
        faces = triangulate_ring(xy)
        verts = np.column_stack([xy, np.full(len(xy), floor.elevation_m)])
        geom = f"mesh:{s.id}"
        scene.geometry[geom] = make_mesh(verts, faces, space_color(s.type, s.room_type))
        add_node(
            s.id,
            parent,
            {
                "kind": "space",
                "id": s.id,
                "floor_id": s.floor_id,
                "type": s.type,
                "room_type": s.room_type,
                "ref": s.ref,
                "name": s.name,
            },
            geometry=geom,
        )
        stats["spaces"] += 1
        if s.type == "hall":
            hall_polys[s.floor_id].append(polys[s.id])
        if s.type in NO_WALL_TYPES:
            continue
        v, f, n_gaps, min_h = _wall_mesh(
            polys[s.id], xy, openings_by_space.get(s.id, []), floor, covers[s.floor_id]
        )
        stats["door_gaps"] += n_gaps
        if len(f):
            name = f"walls:{s.id}"
            scene.geometry[f"mesh:{name}"] = make_mesh(v, f, WALL_COLOR)
            capped = min_h < floor.height_m - EPS
            stats["capped_walls"] += int(capped)
            add_node(
                name,
                parent,
                {
                    "kind": "walls",
                    "space_id": s.id,
                    "floor_id": s.floor_id,
                    "height_m": floor.height_m,
                    "min_height_m": round(min_h, 6),
                    "capped": capped,
                },
                geometry=f"mesh:{name}",
            )
            stats["walls"] += 1
        if s.type in ("stair", "lift"):
            v, f = _marker_mesh(s, polys[s.id], floor)
            if len(f):
                name = f"marker:{s.id}"
                scene.geometry[f"mesh:{name}"] = make_mesh(v, f, MARKER_COLOR)
                add_node(
                    name,
                    parent,
                    {"kind": "marker", "marker": s.type, "space_id": s.id, "floor_id": s.floor_id},
                    geometry=f"mesh:{name}",
                )
                stats["markers"] += 1

    for fid, hp in hall_polys.items():
        v, f = _hall_lines(hp, floors[fid].elevation_m + 0.002)
        if len(f):
            name = f"hall_lines:{fid}"
            scene.geometry[f"mesh:{name}"] = make_mesh(v, f, HALL_LINE_COLOR)
            add_node(name, floor_node(fid), {"kind": "hall_lines", "floor_id": fid}, f"mesh:{name}")

    # openings: unit-box instances, one shared mesh per kind
    for op in building.openings:
        if op.floor_id not in floors:
            stats["skipped_openings"] += 1
            continue
        floor = floors[op.floor_id]
        p = np.asarray(op.position[:2], dtype=np.float64)
        best: tuple[float, float] | None = None
        touches_hall = False
        for sid in op.space_ids:
            if sid not in rings:
                continue
            sp = spaces_by_id[sid]
            touches_hall = touches_hall or sp.type in NO_WALL_TYPES
            d, _, ang = _nearest_edge(rings[sid], p)
            if best is None or d < best[0]:
                best = (d, ang)
        angle = math.degrees(best[1]) if best else 0.0
        depth = HALL_STRIP_DEPTH if touches_hall else STRIP_DEPTH
        geom = f"unit_opening:{op.kind}"
        if geom not in scene.geometry:
            scene.geometry[geom] = unit_box_gltf(OPENING_COLORS.get(op.kind, (120, 120, 120, 255)))
        add_node(
            op.id,
            floor_node(op.floor_id),
            {
                "kind": "opening",
                "id": op.id,
                "floor_id": op.floor_id,
                "opening_kind": op.kind,
                "space_ids": list(op.space_ids),
                "position": [float(op.position[0]), float(op.position[1])],
                "width_m": op.width_m,
                "depth_m": depth,
            },
            geometry=geom,
            matrix=asset_matrix(
                float(p[0]),
                float(p[1]),
                floor.elevation_m,
                op.width_m,
                depth,
                STRIP_HEIGHT,
                angle,
            ),
        )
        stats["openings"] += 1

    # vertical connectors: thin tubes through the stops' representative points
    for vc in building.vertical_connectors:
        stops = []
        for fid, sid in zip(vc.floor_ids, vc.space_ids, strict=False):
            if sid in polys and fid in floors:
                rp = polys[sid].representative_point()
                stops.append((floors[fid].elevation_m, rp.x, rp.y))
        stops.sort()
        parts = []
        for (z0, x0, y0), (z1, x1, y1) in zip(stops, stops[1:], strict=False):
            seg = np.array([[x0, y0, z0], [x1, y1, z1]])
            if np.linalg.norm(seg[1] - seg[0]) < EPS:
                continue
            cyl = trimesh.creation.cylinder(radius=CONNECTOR_RADIUS, segment=seg, sections=6)
            parts.append((np.asarray(cyl.vertices), np.asarray(cyl.faces)))
        if len(stops) < 2:
            stats["connectors_without_stops"] += 1
        v, f = concat(parts)
        if not len(f):  # keep one node per connector even if it has no extent
            x, y = (stops[0][1], stops[0][2]) if stops else (0.0, 0.0)
            z = stops[0][0] if stops else 0.0
            v, f = oriented_box(x, y, z, 0.12, 0.12, 0.12, 0.0)
        geom = f"mesh:{vc.id}"
        scene.geometry[geom] = make_mesh(v, f, CONNECTOR_COLOR)
        add_node(
            vc.id,
            "building",
            {
                "kind": "connector",
                "id": vc.id,
                "connector_kind": vc.kind,
                "floor_ids": list(vc.floor_ids),
                "space_ids": list(vc.space_ids),
                "stops_z": [s[0] for s in stops],
            },
            geometry=geom,
        )
        stats["connectors"] += 1

    stats["build_s"] = round(time.perf_counter() - t0, 3)
    return FloorplanResult(scene=scene, index=index, stats=dict(stats))


def building_bounds(building: BuildingFile) -> tuple[np.ndarray, np.ndarray]:
    """Dataset-frame (min, max) corners of all space polygons and floor heights."""
    pts = [p[:2] for s in building.spaces for p in s.polygon]
    pts += [o.position[:2] for o in building.openings]
    pts += [a.position[:2] for a in building.assets]
    xy = np.asarray(pts, dtype=np.float64) if pts else np.zeros((1, 2))
    zs = [f.elevation_m for f in building.floors] + [
        f.elevation_m + f.height_m for f in building.floors
    ]
    zs = zs or [0.0]
    return (
        np.array([*xy.min(axis=0), min(zs)]),
        np.array([*xy.max(axis=0), max(zs)]),
    )
