"""Geometry helpers: walking distance inside a space (around corners, off the walls),
plus the polygon relations the agent needs (shared walls, stacking, wall midpoints)."""

from __future__ import annotations

import heapq
import itertools
import math

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points
from shapely.prepared import prep

Pt = tuple[float, float]
EPS = 1e-7


class WalkableArea:
    """The part of a space a person can walk in (outline shrunk by `clearance`), with a
    visibility graph over its reflex corners, so geodesic distances can be answered fast."""

    def __init__(self, polygon: Polygon, clearance: float = 0.15):
        self.raw = polygon
        self.clearance = clearance
        self._raw_area: WalkableArea | None = None
        # round corners, as the 1.1.0 rules do
        shrunk = polygon.buffer(-clearance) if clearance > 0 else polygon
        parts = [p for p in _parts(shrunk) if p.area > 1e-4]
        if not parts:                       # space narrower than 2 x clearance: walk the centre
            parts = list(_parts(polygon))
        self.parts = [orient(p, 1.0) for p in parts]
        # 2 cm: the 1.1.0 "clear line" tolerance
        self._tolerance = 0.02 if clearance > 0 else EPS * 10
        self._prepared = [prep(p.buffer(self._tolerance)) for p in self.parts]
        self._shapes: dict[int, Polygon] = {}
        self._corners = [self._reflex_corners(p) for p in self.parts]
        self._corner_graph = [self._visibility(i) for i in range(len(self.parts))]
        self._cache: dict[tuple[Pt, Pt], float] = {}

    # -- public -----------------------------------------------------------------------------
    def snap(self, p: Pt) -> tuple[int, Pt]:
        """Move a point (e.g. a door on the wall) to the nearest walkable spot; return (part, point)."""
        P = Point(p)
        best = None
        for i, part in enumerate(self.parts):
            if part.covers(P):
                return i, (P.x, P.y)
            q = nearest_points(part, P)[0]
            d = q.distance(P)
            if best is None or d < best[0]:
                best = (d, i, (q.x, q.y))
        return best[1], best[2]

    def distance(self, a: Pt, b: Pt) -> float:
        key = (a, b) if a <= b else (b, a)
        if key not in self._cache:
            self._cache[key] = self._geodesic(a, b)
        return self._cache[key]

    def path(self, a: Pt, b: Pt) -> list[Pt]:
        """The actual polyline (for drawing routes and giving turn-by-turn directions)."""
        return self._geodesic(a, b, want_path=True)

    # -- internals --------------------------------------------------------------------------
    def _area(self, part: int):
        if part not in self._shapes:
            self._shapes[part] = self.parts[part].buffer(self._tolerance)
            shapely.prepare(self._shapes[part])
        return self._shapes[part]

    def _seen(self, part: int, p: Pt) -> dict[int, float]:
        """Corners visible from p, with their distances."""
        cs = self._corners[part]
        if not cs:
            return {}
        clear = shapely.covers(self._area(part), shapely.linestrings([[p, c] for c in cs]))
        seen = zip(cs, clear, strict=True)
        return {i: math.dist(p, c) for i, (c, ok) in enumerate(seen) if ok or c == p}

    def _visible(self, part: int, a: Pt, b: Pt) -> bool:
        return a == b or self._prepared[part].covers(LineString([a, b]))

    @staticmethod
    def _reflex_corners(poly: Polygon) -> list[Pt]:
        out = []
        for ring in [poly.exterior, *poly.interiors]:
            cs = list(ring.coords)[:-1]
            n = len(cs)
            for i in range(n):
                (x0, y0), (x1, y1), (x2, y2) = cs[i - 1], cs[i], cs[(i + 1) % n]
                if (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1) < -EPS:   # right turn = reflex
                    out.append((x1, y1))
        return out

    def _visibility(self, part: int) -> dict[int, list[tuple[int, float]]]:
        cs = self._corners[part]
        g: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(cs))}
        pairs = list(itertools.combinations(range(len(cs)), 2))
        if not pairs:
            return g
        # one vectorised covers() call: round wall offsets give rooms many corners
        lines = shapely.linestrings([[cs[i], cs[j]] for i, j in pairs])
        clear = shapely.covers(self._area(part), lines)
        for (i, j), ok in zip(pairs, clear, strict=True):
            if ok or cs[i] == cs[j]:
                d = math.dist(cs[i], cs[j])
                g[i].append((j, d))
                g[j].append((i, d))
        return g

    def _geodesic(self, a: Pt, b: Pt, want_path: bool = False):
        off_area = any(not any(pp.covers(Point(p)) for pp in self._prepared) for p in (a, b))
        if self.clearance > 0 and off_area:
            # point off the walkable area (tiny rooms): 1.1.0 rules charge 1.5x the straight line
            return [a, b] if want_path else 1.5 * math.dist(a, b)
        pa, a = self.snap(a)
        pb, b = self.snap(b)
        if pa != pb:
            if self.clearance == 0:        # separate pieces of the outline itself: no walk between them
                return [] if want_path else math.inf
            # Clearance split the space at a pinch (in the data: drawing slivers 4-29 cm wide inside
            # corridors). Walk the room's own outline instead: still inside the room, never through a wall.
            if self._raw_area is None:
                self._raw_area = WalkableArea(self.raw, clearance=0.0)
            return self._raw_area.path(a, b) if want_path else self._raw_area.distance(a, b)
        part = pa
        if self._visible(part, a, b):
            return [a, b] if want_path else math.dist(a, b)
        cs = self._corners[part]
        n = len(cs)
        # Dijkstra: node n = start, node n+1 = goal, 0..n-1 = corners
        dist = {n: 0.0}
        prev: dict[int, int] = {}
        pq = [(0.0, n)]
        goal_links = self._seen(part, b)
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            if u == n + 1:
                break
            if u == n:
                nbrs = list(self._seen(part, a).items())
            else:
                nbrs = list(self._corner_graph[part][u])
                if u in goal_links:
                    nbrs.append((n + 1, goal_links[u]))
            for v, w in nbrs:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if n + 1 not in dist:              # no visible path: unreachable, never a straight line
            return [] if want_path else math.inf
        if not want_path:
            return dist[n + 1]
        pts, u = [b], n + 1
        while u in prev:
            u = prev[u]
            pts.append(a if u == n else cs[u])
        return pts[::-1]


def _parts(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def shared_wall_length(a: Polygon, b: Polygon, tolerance: float = 0.35) -> float:
    """Length of a's outline that runs within `tolerance` of b's outline (walls have thickness)."""
    near = b.exterior.buffer(tolerance)
    return a.exterior.intersection(near).length


def shared_wall_midpoint(a: Polygon, b: Polygon, tolerance: float = 0.35) -> Pt | None:
    """Middle of the wall a and b share (where a new door goes)."""
    seg = a.exterior.intersection(b.exterior.buffer(tolerance))
    if seg.is_empty or seg.length == 0:
        return None
    lines = [seg] if seg.geom_type == "LineString" else [g for g in getattr(seg, "geoms", []) if g.length > 0]
    longest = max(lines, key=lambda g: g.length)
    m = longest.interpolate(0.5, normalized=True)
    # put the door half-way across the wall gap
    q = nearest_points(b.exterior, m)[0]
    return ((m.x + q.x) / 2, (m.y + q.y) / 2)


def overlap_area(a: Polygon, b: Polygon) -> float:
    try:
        return a.intersection(b).area
    except Exception:
        return a.buffer(0).intersection(b.buffer(0)).area
