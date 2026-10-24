"""Small, fast mesh builders (numpy + earcut) used by the floor-plan builder.

trimesh's own `extrude_polygon` is correct but too slow to call tens of thousands of times
for the biggest OSM buildings, so walls are extruded here directly into vertex/face arrays.
All builders work in the dataset frame (z up).
"""

from collections.abc import Iterable

import mapbox_earcut
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

MIN_AREA = 1e-6


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """Non-degenerate polygons inside any shapely geometry."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > MIN_AREA else []
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if p.area > MIN_AREA]
    parts = getattr(geom, "geoms", None)
    if parts is None:
        return []
    out: list[Polygon] = []
    for g in parts:
        out.extend(polygons_of(g))
    return out


def _ring_xy(coords: Iterable[tuple[float, ...]]) -> np.ndarray:
    xy = np.asarray([c[:2] for c in coords], dtype=np.float64)
    if len(xy) > 1 and np.allclose(xy[0], xy[-1]):
        xy = xy[:-1]
    return xy


def signed_area(xy: np.ndarray) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def triangulate_ring(xy: np.ndarray) -> np.ndarray:
    """Triangles (m, 3) of a simple ring, CCW (normal +z), indices into `xy` itself.

    The ring's vertices are used verbatim (no new vertices), so a floor mesh carries the
    data polygon exactly.
    """
    n = len(xy)
    if n < 3:
        return np.zeros((0, 3), dtype=np.int64)
    idx = np.asarray(
        mapbox_earcut.triangulate_float64(xy, np.array([n], dtype=np.uint32)), dtype=np.int64
    ).reshape(-1, 3)
    if len(idx) == 0:  # degenerate ring: fan so the mesh is never empty
        idx = np.array([[0, i, i + 1] for i in range(1, n - 1)], dtype=np.int64)
    return orient_up(xy, idx)


def orient_up(xy: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Flip triangles whose 2D winding is clockwise so all normals face +z."""
    if len(faces) == 0:
        return faces
    a, b, c = xy[faces[:, 0]], xy[faces[:, 1]], xy[faces[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    out = faces.copy()
    flip = cross < 0
    out[flip] = out[flip][:, [0, 2, 1]]
    return out


def extrude(polys: list[Polygon], z0: float, z1: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed prisms of shapely polygons (holes allowed) from z0 to z1 as (verts, faces)."""
    verts: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    offset = 0
    for poly in polys:
        rings = [_ring_xy(poly.exterior.coords)] + [_ring_xy(r.coords) for r in poly.interiors]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            continue
        xy = np.vstack(rings)
        ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
        tri = np.asarray(mapbox_earcut.triangulate_float64(xy, ends), dtype=np.int64)
        tri = orient_up(xy, tri.reshape(-1, 3))
        n = len(xy)
        bottom = np.column_stack([xy, np.full(n, z0)])
        top = np.column_stack([xy, np.full(n, z1)])
        verts.extend([bottom, top])
        f_parts = [tri[:, [0, 2, 1]] + offset, tri + offset + n]
        # side quads per ring edge, outward facing for a CCW exterior / CW holes
        start = 0
        for k, ring in enumerate(rings):
            m = len(ring)
            i = np.arange(m) + start
            j = (np.arange(m) + 1) % m + start
            ccw = signed_area(ring) > 0
            outward = ccw if k == 0 else not ccw
            b0, b1, t0, t1 = i + offset, j + offset, i + offset + n, j + offset + n
            if outward:
                quads = np.vstack([np.column_stack([b0, b1, t1]), np.column_stack([b0, t1, t0])])
            else:
                quads = np.vstack([np.column_stack([b0, t1, b1]), np.column_stack([b0, t0, t1])])
            f_parts.append(quads)
            start += m
        faces.append(np.vstack(f_parts))
        offset += 2 * n
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(verts), np.vstack(faces)


def oriented_box(
    cx: float, cy: float, z0: float, length: float, width: float, height: float, angle: float
) -> tuple[np.ndarray, np.ndarray]:
    """Box with its length along direction `angle` (radians), bottom at z0."""
    c, s = np.cos(angle), np.sin(angle)
    hl, hw = length / 2, width / 2
    local = np.array([[-hl, -hw], [hl, -hw], [hl, hw], [-hl, hw]])
    xy = np.column_stack(
        [cx + local[:, 0] * c - local[:, 1] * s, cy + local[:, 0] * s + local[:, 1] * c]
    )
    return extrude([Polygon(xy)], z0, z0 + height)


def concat(parts: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    verts: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    offset = 0
    for v, f in parts:
        if len(v) == 0 or len(f) == 0:
            continue
        verts.append(v)
        faces.append(f + offset)
        offset += len(v)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(verts), np.vstack(faces)
