"""Official Challenge Routing: deterministic ordered space sequence under the new organizer rules.

Pure function `challenge_route(building, request)`; no I/O, no model clients, no
mutation of the building. Separate from the precision router in navigation.py.

Cost model (organizer rules, no official evaluator yet):
- Inside a space: exact shortest path inside the room polygon shrunk by
  `wall_clearance_m` (round joins), ignoring every asset. Visibility graph over
  the reflex vertices of the shrunk polygon; straight line when visible.
- Openings are crossed exactly at their `position`. The position is linked into
  the shrunk polygon of each side by a straight stub to the nearest walkable
  point (for a door on a straight wall this is the perpendicular stub of length
  `wall_clearance_m`). Openings narrower than 2 x clearance are unusable.
- Stairs: 2 m per metre of |delta elevation_m| between consecutive stops.
  Lifts: 15 m + 1 m per metre of |delta elevation_m| per ride between any stops.
- Production room types may be start/target but never transit.
"""
from __future__ import annotations

import heapq
import itertools
import math

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

from errors import ModelError

OUTSIDE = 'outside'
DEFAULT_CLEARANCE_M = 0.15
MAX_CLEARANCE_M = 2.0
STAIRS_M_PER_M = 2.0
LIFT_WAIT_M = 15.0
LIFT_M_PER_M = 1.0
FORKLIFT_MIN_WIDTH_M = 3.0
PRODUCTION_ROOM_TYPES = ('additive_zone', 'assembly_zone', 'production_zone')
QUAD_SEGS = 8
VIS_TOL = 1e-7
LONG_STUB_M = 0.5
MAX_VIA = 25
_CACHE_LIMIT = 6000
_GEO_CACHE: dict = {}

START, TARGET = ('start',), ('target',)
RELAXABLE = ('closures', 'step_free', 'forklift', 'production_transit', 'wall_clearance')

FORKLIFT_WARNING = ('Forklift rules are partial: the data has no official forklift-aisle classification, '
                    'so aisle membership is not checked. Enforced only: no stairs, every opening used is '
                    f'at least {FORKLIFT_MIN_WIDTH_M:.1f} m wide (unknown width = unusable), lifts only with '
                    f'a declared width of at least {FORKLIFT_MIN_WIDTH_M:.1f} m.')


# ---------------------------------------------------------------- geometry

def _parse_polygon(space):
    """Return (polygon or None, note) where note is None, 'repaired' or 'unusable'."""
    raw = space.get('polygon')
    try:
        if isinstance(raw, dict) and 'type' in raw:
            poly = shape(raw)
        elif isinstance(raw, dict):
            poly = Polygon(raw['exterior'], raw.get('holes', []))
        elif raw and isinstance(raw[0][0], (list, tuple)):
            poly = Polygon(raw[0], raw[1:])
        else:
            poly = Polygon(raw)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None, 'unusable'
    if poly.is_empty or not all(math.isfinite(v) for v in poly.bounds):
        return None, 'unusable'
    if poly.is_valid and poly.geom_type in ('Polygon', 'MultiPolygon'):
        return poly, None
    fixed = make_valid(poly)
    parts = [g for g in getattr(fixed, 'geoms', [fixed]) if g.geom_type in ('Polygon', 'MultiPolygon')]
    fixed = unary_union(parts) if parts else None
    if fixed is None or fixed.is_empty or fixed.area <= 0:
        return None, 'unusable'
    return fixed, 'repaired'


def _parts(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == 'Polygon':
        return [geom]
    return [p for g in getattr(geom, 'geoms', []) for p in _parts(g)]


class _SpaceGeo:
    """Shrunk walkable area of one space plus cached exact shortest paths."""

    def __init__(self, poly, clearance):
        self.poly = poly
        self.clearance = clearance
        walk = poly.buffer(-clearance, quad_segs=QUAD_SEGS) if clearance > 0 else poly
        parts = [orient(p, 1.0) for p in _parts(walk) if p.area > 1e-12]
        self.walk = (parts[0] if len(parts) == 1 else MultiPolygon(parts)) if parts else Polygon()
        self.empty = not parts
        self.convex = (len(parts) == 1 and not parts[0].interiors
                       and parts[0].convex_hull.area - parts[0].area <= 1e-9 * max(1.0, parts[0].area))
        self._tol_geom = self.walk.buffer(VIS_TOL) if parts else None
        if parts:
            shapely.prepare(self._tol_geom)
        self._tol = prep(self._tol_geom) if parts else None
        self._reflex = None
        self._adj = None
        self._attach = {}
        self._seen = {}
        self._paths = {}

    def visible(self, a, b):
        if a == b:
            return self._tol.covers(Point(a))
        return self._tol.covers(LineString([a, b]))

    def attach(self, p):
        """Nearest walkable point E of p and the stub length |pE|."""
        hit = self._attach.get(p)
        if hit is None:
            if self._tol.covers(Point(p)):
                hit = (p, 0.0)
            else:
                e = nearest_points(self.walk, Point(p))[0]
                e = (e.x, e.y)
                hit = (e, math.dist(p, e))
            self._attach[p] = hit
        return hit

    def _vis_graph(self):
        """Reflex vertices and their visibility edges.

        Only tangent edges are kept: a shortest path bends at a reflex vertex u
        only if both ring neighbours of u lie on one side of the edge line.
        Visibility is checked vectorised against the tolerance-buffered area.
        """
        if self._reflex is None:
            verts, nbrs = [], []
            for part in _parts(self.walk):
                for ring in [part.exterior, *part.interiors]:
                    coords = list(ring.coords)[:-1]
                    n = len(coords)
                    for i, p in enumerate(coords):
                        a, b = coords[i - 1], coords[(i + 1) % n]
                        cross = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
                        if cross < -1e-12:  # right turn with the region on the left = reflex
                            verts.append((p[0], p[1]))
                            nbrs.append((a, b))
            self._reflex, self._nbrs = verts, nbrs
            adj = {i: [] for i in range(len(verts))}
            pairs = [(i, j) for i, j in itertools.combinations(range(len(verts)), 2)
                     if self._tangent(i, verts[j]) and self._tangent(j, verts[i])]
            for (i, j), ok in zip(pairs, self._covers_many([(verts[i], verts[j]) for i, j in pairs])):
                if ok:
                    d = math.dist(verts[i], verts[j])
                    adj[i].append((j, d))
                    adj[j].append((i, d))
            self._adj = adj
        return self._reflex, self._adj

    def _tangent(self, i, q):
        u = self._reflex[i]
        a, b = self._nbrs[i]
        dx, dy = q[0] - u[0], q[1] - u[1]
        sa = dx * (a[1] - u[1]) - dy * (a[0] - u[0])
        sb = dx * (b[1] - u[1]) - dy * (b[0] - u[0])
        return sa * sb >= -1e-12

    def _covers_many(self, segments):
        if not segments:
            return []
        lines = shapely.linestrings([[a, b] for a, b in segments])
        return [bool(v) for v in shapely.covers(self._tol_geom, lines)]

    def _seen_from(self, e):
        hit = self._seen.get(e)
        if hit is None:
            verts, _ = self._vis_graph()
            cand = [i for i, v in enumerate(verts) if v != e and self._tangent(i, e)]
            oks = self._covers_many([(e, verts[i]) for i in cand])
            hit = [(i, math.dist(e, verts[i])) for i, ok in zip(cand, oks) if ok]
            hit += [(i, 0.0) for i, v in enumerate(verts) if v == e]
            self._seen[e] = hit
        return hit

    def path(self, p, r):
        """(length, points) from p to r; length is inf if not connected."""
        if p == r:
            return 0.0, [p]
        key = (p, r)
        hit = self._paths.get(key)
        if hit is not None:
            return hit
        rev = self._paths.get((r, p))
        if rev is not None:
            hit = (rev[0], list(reversed(rev[1])))
            self._paths[key] = hit
            return hit
        hit = self._compute(p, r)
        self._paths[key] = hit
        return hit

    def _compute(self, p, r):
        if self.empty:
            return math.inf, []
        ep, sp = self.attach(p)
        er, sr = self.attach(r)
        if self.convex or self.visible(ep, er):
            mid = [ep, er]
            core = math.dist(ep, er)
        else:
            verts, adj = self._vis_graph()
            targets = dict(self._seen_from(er))
            dist, prev, heap, done = {}, {}, [], set()
            for i, d in self._seen_from(ep):
                if d < dist.get(i, math.inf):
                    dist[i], prev[i] = d, None
                    heapq.heappush(heap, (d, i))
            best, best_i = math.inf, None
            while heap:
                d, i = heapq.heappop(heap)
                if i in done:
                    continue
                if d >= best:
                    break
                done.add(i)
                if i in targets and d + targets[i] < best:
                    best, best_i = d + targets[i], i
                for j, w in adj[i]:
                    nd = d + w
                    if nd < dist.get(j, math.inf):
                        dist[j], prev[j] = nd, i
                        heapq.heappush(heap, (nd, j))
            if best_i is None:
                return math.inf, []
            chain, i = [], best_i
            while i is not None:
                chain.append(verts[i])
                i = prev[i]
            mid = [ep, *reversed(chain), er]
            core = best
        points = [p]
        for q in [*mid, r]:
            if q != points[-1]:
                points.append(q)
        return sp + core + sr, points


def _geo_for(building_id, space, poly, clearance):
    key = (building_id, space.get('id'), clearance, repr(space.get('polygon')))
    geo = _GEO_CACHE.get(key)
    if geo is None:
        if len(_GEO_CACHE) >= _CACHE_LIMIT:
            _GEO_CACHE.clear()
        geo = _SpaceGeo(poly, clearance)
        _GEO_CACHE[key] = geo
    return geo


# ---------------------------------------------------------------- validation

def _bool(request, name):
    value = request.get(name, False)
    if not isinstance(value, bool):
        raise ModelError('invalid_input', f'{name} must be a boolean')
    return value


def _xy(value, name):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise ModelError('invalid_input', f'{name} must be [x, y] with finite numbers')
    return (float(value[0]), float(value[1]))


def _elevation(floor):
    value = floor.get('elevation_m') if isinstance(floor, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _connector_stops(connector):
    """[(floor_id, space_id, position or None)] for floor_ids/space_ids or stops formats."""
    if isinstance(connector.get('stops'), list):
        stops = []
        for stop in connector['stops']:
            if not isinstance(stop, dict):
                return None
            pos = stop.get('position', connector.get('position'))
            stops.append((stop.get('floor_id'), stop.get('space_id'), pos))
        return stops
    floors, spaces = connector.get('floor_ids'), connector.get('space_ids')
    if not isinstance(floors, list) or not isinstance(spaces, list) or len(floors) != len(spaces):
        return None
    return [(f, s, connector.get('position')) for f, s in zip(floors, spaces)]


# ---------------------------------------------------------------- model

class _Model:
    def __init__(self, building):
        if not isinstance(building, dict) or not isinstance(building.get('spaces'), list):
            raise ModelError('invalid_input', 'building must be an object with a spaces list')
        info = building.get('building') if isinstance(building.get('building'), dict) else {}
        self.building_id = info.get('id', '')
        self.spaces = {s['id']: s for s in building['spaces'] if isinstance(s, dict) and isinstance(s.get('id'), str)}
        self.openings = {o['id']: o for o in building.get('openings', []) or []
                         if isinstance(o, dict) and isinstance(o.get('id'), str)}
        self.connectors = {c['id']: c for c in building.get('vertical_connectors', []) or []
                           if isinstance(c, dict) and isinstance(c.get('id'), str)}
        self.floors = {f['id']: f for f in building.get('floors', []) or [] if isinstance(f, dict) and 'id' in f}
        self.assets = {a['id']: a for a in building.get('assets', []) or []
                       if isinstance(a, dict) and isinstance(a.get('id'), str)}
        self._polys = {}

    def polygon(self, sid):
        if sid not in self._polys:
            self._polys[sid] = _parse_polygon(self.spaces[sid])
        return self._polys[sid]

    def geo(self, sid, clearance):
        poly, _ = self.polygon(sid)
        if poly is None:
            return None
        return _geo_for(self.building_id, self.spaces[sid], poly, clearance)

    def anchor(self, sid):
        poly, _ = self.polygon(sid)
        if poly is None:
            return None
        p = poly.representative_point()
        return (p.x, p.y)


class _Params:
    def __init__(self, clearance, step_free, forklift, allow_outside, closed, production_rule):
        self.clearance = clearance
        self.step_free = step_free
        self.forklift = forklift
        self.allow_outside = allow_outside
        self.closed = frozenset(closed)
        self.production_rule = production_rule

    def relaxed(self, names):
        return _Params(0.0 if 'wall_clearance' in names else self.clearance,
                       False if 'step_free' in names else self.step_free,
                       False if 'forklift' in names else self.forklift,
                       self.allow_outside,
                       frozenset() if 'closures' in names else self.closed,
                       False if 'production_transit' in names else self.production_rule)


def _resolve_endpoint(model, spec, name):
    if not isinstance(spec, dict):
        raise ModelError('invalid_input', f'{name} must be an endpoint object')
    kind = spec.get('kind')
    if kind == 'space':
        sid = spec.get('id')
        if not isinstance(sid, str) or sid not in model.spaces:
            raise ModelError('invalid_input', f'{name}: unknown space id')
        anchor = model.anchor(sid)
        return {'kind': 'space', 'id': sid, 'space_id': sid, 'position': anchor,
                'resolution': 'Representative point of the space polygon (starter-kit anchor).'}
    if kind == 'asset':
        aid = spec.get('id')
        asset = model.assets.get(aid) if isinstance(aid, str) else None
        if asset is None:
            raise ModelError('invalid_input', f'{name}: unknown asset id')
        sid = asset.get('space_id')
        if sid not in model.spaces:
            raise ModelError('invalid_input', f'{name}: asset has no known space')
        pos = _xy(asset.get('position'), f'{name} asset position')
        return {'kind': 'asset', 'id': aid, 'space_id': sid, 'position': pos,
                'resolution': 'Asset position (assets are not obstacles), linked to the nearest walkable point.'}
    if kind == 'point':
        sid = spec.get('space_id')
        if not isinstance(sid, str) or sid not in model.spaces:
            raise ModelError('invalid_input', f'{name}: unknown space_id')
        pos = _xy(spec.get('position'), f'{name}.position')
        poly, _ = model.polygon(sid)
        if poly is not None and poly.distance(Point(pos)) > 1e-6:
            raise ModelError('invalid_input', f'{name}: position is outside the space polygon')
        return {'kind': 'point', 'id': None, 'space_id': sid, 'position': pos,
                'resolution': 'User point, linked to the nearest walkable point if inside the wall clearance.'}
    if kind == 'door':
        oid, sid = spec.get('id'), spec.get('space_id')
        opening = model.openings.get(oid) if isinstance(oid, str) else None
        if opening is None or sid not in (opening.get('space_ids') or []) or sid not in model.spaces:
            raise ModelError('invalid_input', f'{name}: choose an existing opening and one of its spaces')
        pos = _xy(opening.get('position'), f'{name} opening position')
        return {'kind': 'door', 'id': oid, 'space_id': sid, 'position': pos,
                'resolution': 'Opening position on the selected side.'}
    raise ModelError('invalid_input', f'{name}.kind must be space, asset, point or door')


def _parse_request(model, request):
    if not isinstance(request, dict):
        raise ModelError('invalid_input', 'request must be an object')
    clearance = request.get('wall_clearance_m', DEFAULT_CLEARANCE_M)
    if (isinstance(clearance, bool) or not isinstance(clearance, (int, float)) or not math.isfinite(clearance)
            or not 0 <= clearance <= MAX_CLEARANCE_M):
        raise ModelError('invalid_input', f'wall_clearance_m must be a number between 0 and {MAX_CLEARANCE_M}')
    closed = request.get('closed', [])
    if not isinstance(closed, list) or not all(isinstance(c, str) for c in closed):
        raise ModelError('invalid_input', 'closed must be a list of space, opening or connector ids')
    unknown = [c for c in closed if c not in model.spaces and c not in model.openings and c not in model.connectors]
    if unknown:
        raise ModelError('invalid_input', 'Unknown closed id(s): ' + ', '.join(sorted(set(unknown))))
    via = request.get('via', [])
    if not isinstance(via, list) or len(via) > MAX_VIA:
        raise ModelError('invalid_input', f'via must be a list of at most {MAX_VIA} endpoints')
    params = _Params(float(clearance), _bool(request, 'step_free'), _bool(request, 'forklift'),
                     _bool(request, 'allow_outside'), closed, True)
    endpoints = [_resolve_endpoint(model, request.get('start'), 'start')]
    endpoints += [_resolve_endpoint(model, v, f'via[{i}]') for i, v in enumerate(via)]
    endpoints.append(_resolve_endpoint(model, request.get('target'), 'target'))
    return params, endpoints


# ---------------------------------------------------------------- search

class _Leg:
    """One request-specific graph search between two resolved endpoints."""

    def __init__(self, model, params, start, target, notes):
        self.m, self.p, self.start, self.target, self.notes = model, params, start, target, notes
        p = params
        self.portals = {}
        self.opening_sides = {}
        for oid, o in model.openings.items():
            if oid in p.closed:
                continue
            width = o.get('width_m')
            width = None if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) else float(width)
            if p.step_free and o.get('step_free') is not True:
                continue
            if p.forklift and (width is None or width < FORKLIFT_MIN_WIDTH_M):
                continue
            if width is not None and width < 2 * p.clearance:
                continue
            if width is None:
                notes['unknown_width'].add(oid)
            try:
                _xy(o.get('position'), 'opening position')
            except ModelError:
                notes['bad_opening'].add(oid)
                continue
            sides = []
            for sid in o.get('space_ids') or []:
                if sid == OUTSIDE:
                    if p.allow_outside:
                        sides.append(OUTSIDE)
                elif sid in model.spaces and sid not in p.closed:
                    sides.append(sid)
                elif sid not in model.spaces:
                    notes['bad_opening'].add(oid)
            sides = list(dict.fromkeys(sides))
            if not sides:
                continue
            node = ('o', oid)
            self.opening_sides[oid] = sides
            for sid in sides:
                self.portals.setdefault(sid, []).append(node)
        self.stop_info = {}
        self.vertical = {}
        for cid, c in model.connectors.items():
            kind = c.get('kind')
            if cid in p.closed or kind not in ('stairs', 'lift'):
                continue
            if kind == 'stairs' and (p.step_free or p.forklift):
                continue
            if kind == 'lift' and p.step_free and c.get('step_free') is False:
                continue
            if kind == 'lift' and p.forklift:
                width = c.get('width_m')
                if isinstance(width, bool) or not isinstance(width, (int, float)) or width < FORKLIFT_MIN_WIDTH_M:
                    continue
            stops = _connector_stops(c)
            if not stops:
                notes['bad_connector'].add(cid)
                continue
            usable = []
            for i, (fid, sid, pos) in enumerate(stops):
                if sid not in model.spaces or sid in p.closed:
                    continue
                fid = fid if fid in model.floors else model.spaces[sid].get('floor_id')
                elev = _elevation(model.floors.get(fid))
                if elev is None:
                    notes['missing_elevation'].add(cid)
                    continue
                point = None
                if pos is not None:
                    try:
                        point = _xy(pos, 'connector position')
                    except ModelError:
                        point = None
                if point is None:
                    point = model.anchor(sid)
                    notes['stop_anchor'] = True
                if point is None:
                    continue
                node = ('c', cid, i)
                self.stop_info[node] = (sid, fid, elev, point, kind)
                self.portals.setdefault(sid, []).append(node)
                usable.append(node)
            if kind == 'stairs':
                order = sorted(range(len(stops)), key=lambda i: (_elevation(model.floors.get(stops[i][0])) or 0.0, i))
                # consecutive stops along the staircase; a removed stop interrupts it
                for a, b in zip(order, order[1:]):
                    na, nb = ('c', cid, a), ('c', cid, b)
                    if na in self.stop_info and nb in self.stop_info:
                        cost = STAIRS_M_PER_M * abs(self.stop_info[na][2] - self.stop_info[nb][2])
                        self.vertical.setdefault(na, []).append((nb, cost))
                        self.vertical.setdefault(nb, []).append((na, cost))
            else:
                for na, nb in itertools.permutations(usable, 2):
                    cost = LIFT_WAIT_M + LIFT_M_PER_M * abs(self.stop_info[na][2] - self.stop_info[nb][2])
                    self.vertical.setdefault(na, []).append((nb, cost))
        self.portals.setdefault(target['space_id'], []).append(TARGET)

    def spaces_of(self, node):
        if node == START:
            return [self.start['space_id']]
        if node == TARGET:
            return []
        if node[0] == 'o':
            return self.opening_sides[node[1]]
        return [self.stop_info[node][0]]

    def point(self, node, sid):
        if node == START:
            return self.start['position']
        if node == TARGET:
            return self.target['position']
        if node[0] == 'o':
            return tuple(float(v) for v in self.m.openings[node[1]]['position'])
        return self.stop_info[node][3]

    def transit_forbidden(self, sid):
        return (self.p.production_rule and sid != OUTSIDE
                and self.m.spaces[sid].get('room_type') in PRODUCTION_ROOM_TYPES)

    def walk(self, sid, u, v):
        if sid == OUTSIDE:
            return 0.0, []
        geo = self.m.geo(sid, self.p.clearance)
        if geo is None:
            self.notes['unusable_polygon'].add(sid)
            return math.inf, []
        if self.m.polygon(sid)[1] == 'repaired':
            self.notes['repaired_polygon'].add(sid)
        return geo.path(self.point(u, sid), self.point(v, sid))

    def search(self):
        """Return list of edges (u, v, kind, label, cost) or None."""
        counter = itertools.count()
        dist, prev = {START: 0.0}, {START: None}
        heap = [(0.0, next(counter), START)]
        done = set()
        while heap:
            d, _, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if u == TARGET:
                break
            for sid in self.spaces_of(u):
                candidates = self.portals.get(sid, [])
                if u != START and self.transit_forbidden(sid):
                    candidates = [TARGET] if TARGET in candidates else []
                for v in candidates:
                    if v == u or v in done:
                        continue
                    w, _ = self.walk(sid, u, v)
                    nd = d + w
                    if nd < dist.get(v, math.inf):
                        dist[v], prev[v] = nd, (u, 'walk', sid, w)
                        heapq.heappush(heap, (nd, next(counter), v))
            for v, w in self.vertical.get(u, []):
                if v in done:
                    continue
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, (u, 'vertical', None, w)
                    heapq.heappush(heap, (nd, next(counter), v))
        if TARGET not in done:
            return None
        edges, v = [], TARGET
        while prev[v] is not None:
            u, kind, sid, w = prev[v]
            edges.append((u, v, kind, sid, w))
            v = u
        return list(reversed(edges))


def _label(leg, node):
    if node == START:
        return 'start'
    if node == TARGET:
        return 'target'
    if node[0] == 'o':
        return node[1]
    return f'{node[1]}@{leg.stop_info[node][0]}'


def _assemble(leg, edges):
    m = leg.m
    space_path, element_path, steps, segments, transitions = [], [], [], [], []
    walk_m = vertical_m = 0.0
    current = None
    for u, v, kind, sid, w in edges:
        if kind == 'walk':
            if current is None:
                space_path.append(sid)
            elif sid != current:
                element_path.append(u[1])
                steps.append({'type': 'door', 'element_id': u[1], 'from': current, 'to': sid, 'cost_m': 0.0})
                space_path.append(sid)
            current = sid
            walk_m += w
            steps.append({'type': 'outside' if sid == OUTSIDE else 'walk', 'space_id': sid,
                          'from': _label(leg, u), 'to': _label(leg, v), 'cost_m': w})
            if sid != OUTSIDE:
                _, points = leg.walk(sid, u, v)
                pts = [[p[0], p[1]] for p in points]
                if segments and segments[-1]['space_id'] == sid and segments[-1]['points'][-1] == pts[0]:
                    segments[-1]['points'].extend(pts[1:])
                else:
                    segments.append({'floor_id': m.spaces[sid].get('floor_id'), 'space_id': sid, 'points': pts})
        else:
            s1, f1, _, _, ck = leg.stop_info[u]
            s2, f2, _, _, _ = leg.stop_info[v]
            if current is None:
                space_path.append(s1)
            element_path.append(u[1])
            space_path.append(s2)
            current = s2
            vertical_m += w
            steps.append({'type': ck, 'element_id': u[1], 'from': s1, 'to': s2,
                          'from_floor': f1, 'to_floor': f2, 'cost_m': w})
            transitions.append({'element_id': u[1], 'kind': ck, 'from_floor': f1, 'to_floor': f2,
                                'from_space': s1, 'to_space': s2,
                                'delta_elevation_m': abs(leg.stop_info[u][2] - leg.stop_info[v][2]), 'cost_m': w})
    if not space_path:
        space_path = [leg.start['space_id']]
    segments = [s for s in segments if len(s['points']) >= 2]
    used_doors = [e for e in element_path if e in m.openings]
    return {'space_path': space_path, 'element_path': element_path, 'cost_m': walk_m + vertical_m,
            'walk_m': walk_m, 'vertical_cost_m': vertical_m, 'steps': steps, 'segments': segments,
            'transitions': transitions, 'used_doors': used_doors}


def _solve_leg(model, params, a, b, notes):
    """Return (status, leg result or None, reason or None)."""
    for end, name in ((a, 'start'), (b, 'target')):
        if end['space_id'] in params.closed:
            return 'blocked_endpoint', None, f"The {name} space {end['space_id']} is closed."
        if end['kind'] == 'door' and end['id'] in params.closed:
            return 'blocked_endpoint', None, f"The {name} opening {end['id']} is closed."
        geo = model.geo(end['space_id'], params.clearance)
        if geo is None or geo.empty:
            return ('blocked_endpoint', None,
                    f"The {name} space {end['space_id']} has no walkable area at wall clearance {params.clearance} m.")
    leg = _Leg(model, params, a, b, notes)
    edges = leg.search()
    if edges is None:
        return 'unreachable', None, None
    result = _assemble(leg, edges)
    for oid in result['used_doors']:
        for sid in model.openings[oid].get('space_ids') or []:
            if sid in model.spaces:
                geo = model.geo(sid, params.clearance)
                if geo is not None and not geo.empty:
                    _, stub = geo.attach(tuple(float(v) for v in model.openings[oid]['position']))
                    if stub > params.clearance + LONG_STUB_M:
                        notes['long_stub'].add((oid, sid, round(stub, 3)))
    return 'ok', result, None


def _explain(model, params, a, b):
    """Re-run with single constraints relaxed to say which one blocks the route."""
    applicable = []
    if params.closed:
        applicable.append('closures')
    if params.step_free:
        applicable.append('step_free')
    if params.forklift:
        applicable.append('forklift')
    if params.production_rule:
        applicable.append('production_transit')
    if params.clearance > 0:
        applicable.append('wall_clearance')
    reasons = []
    for name in applicable:
        status, res, _ = _solve_leg(model, params.relaxed({name}), a, b, _new_notes())
        if status != 'ok':
            continue
        if name == 'closures':
            used = set(res['space_path']) | set(res['element_path'])
            hit = sorted(c for c in params.closed if c in used) or sorted(params.closed)
            reasons.append('reachable if closures are lifted: ' + ', '.join(hit))
        elif name == 'step_free':
            reasons.append('reachable without the step-free restriction')
        elif name == 'forklift':
            reasons.append('reachable without the forklift restrictions')
        elif name == 'production_transit':
            hit = [s for s in res['space_path'][1:-1]
                   if s in model.spaces and model.spaces[s].get('room_type') in PRODUCTION_ROOM_TYPES]
            reasons.append('reachable only through production areas (transit not allowed): '
                           + ', '.join(dict.fromkeys(hit)))
        else:
            reasons.append(f'reachable with wall clearance 0 m (a passage is too narrow for {params.clearance} m)')
    if reasons:
        return '; '.join(reasons)
    for size in range(2, len(applicable) + 1):
        found = [combo for combo in itertools.combinations(applicable, size)
                 if _solve_leg(model, params.relaxed(set(combo)), a, b, _new_notes())[0] == 'ok']
        if found:
            return ('reachable only if several constraints are relaxed together: '
                    + ' or '.join('(' + ' + '.join(combo) + ')' for combo in found))
    return 'no connection in building data'


def _new_notes():
    return {'unknown_width': set(), 'bad_opening': set(), 'bad_connector': set(), 'missing_elevation': set(),
            'stop_anchor': False, 'unusable_polygon': set(), 'repaired_polygon': set(), 'long_stub': set()}


def _public_endpoint(model, end):
    out = {'kind': end['kind'], 'id': end['id'], 'space_id': end['space_id'],
           'floor_id': model.spaces[end['space_id']].get('floor_id'),
           'position': list(end['position']) if end['position'] is not None else None,
           'resolution': end['resolution']}
    return out


def _challenge_route(building, request, relax=frozenset()):
    """Internal entry; `relax` is a private test hook (e.g. {'production_transit'})."""
    model = _Model(building)
    params, endpoints = _parse_request(model, request)
    if relax:
        unknown = set(relax) - set(RELAXABLE)
        if unknown:
            raise ModelError('invalid_input', 'Unknown relaxation')
        params = params.relaxed(set(relax))
    notes = _new_notes()
    rules = {'wall_clearance_m': params.clearance, 'stairs_m_per_m': STAIRS_M_PER_M,
             'lift_wait_m': LIFT_WAIT_M, 'lift_m_per_m': LIFT_M_PER_M,
             'production_room_types': list(PRODUCTION_ROOM_TYPES),
             'production_transit': 'forbidden' if params.production_rule else 'allowed',
             'forklift': 'partial' if params.forklift else 'off',
             'forklift_min_width_m': FORKLIFT_MIN_WIDTH_M,
             'step_free': params.step_free, 'allow_outside': params.allow_outside,
             'closed': sorted(params.closed)}
    assumptions = [
        'Walkable area inside a space is the room polygon shrunk by wall_clearance_m (round corners); '
        'furniture, machines and all other assets are ignored.',
        'Inside a space the exact shortest path in the shrunk polygon is used (visibility graph over its reflex vertices).',
        "Each opening is crossed exactly at its 'position', linked to each side by a straight stub to the nearest "
        'walkable point (perpendicular to the wall for a door on a straight wall); the stub is exempt from the clearance.',
        'Openings narrower than 2 x wall_clearance_m are unusable.',
        'Stairs cost 2 m per metre of elevation_m difference between consecutive stops; a closed stop space interrupts the stair.',
        'A lift ride between any two stops costs 15 m + 1 m per metre of elevation_m difference.',
        'Production room types ' + ', '.join(PRODUCTION_ROOM_TYPES) + ' may be start, via or target but never transit '
        '(assumed set; official mapping unconfirmed).',
    ]
    if any(e['kind'] == 'space' for e in endpoints):
        assumptions.append('Space endpoints use the shapely representative_point of the room polygon '
                           "(starter-kit anchor); the evaluator's anchor convention is unconfirmed.")
    if any(e['kind'] == 'asset' for e in endpoints):
        assumptions.append('Asset endpoints use the asset position, linked to the nearest walkable point if needed.')
    if params.allow_outside:
        assumptions.append("allow_outside: all entrances are joined through one zero-cost 'outside' node "
                           '(starter-kit convention, not confirmed by the organizers).')
    warnings = [FORKLIFT_WARNING] if params.forklift else []
    for end in endpoints:
        if end['position'] is None:
            raise ModelError('invalid_input', f"Space {end['space_id']} has no usable polygon")
        poly, _ = model.polygon(end['space_id'])
        if end['kind'] == 'asset' and poly is not None and poly.distance(Point(end['position'])) > LONG_STUB_M:
            warnings.append(f"Asset {end['id']} lies {poly.distance(Point(end['position'])):.2f} m outside its space polygon.")

    legs, status, reason = [], 'ok', None
    for index, (a, b) in enumerate(zip(endpoints, endpoints[1:])):
        leg_status, res, leg_reason = _solve_leg(model, params, a, b, notes)
        if leg_status != 'ok':
            status = leg_status
            if leg_status == 'unreachable':
                if notes['missing_elevation']:
                    status = 'unsupported'
                    leg_reason = ('floor elevation_m is missing for connector(s) '
                                  + ', '.join(sorted(notes['missing_elevation'])) + '; vertical cost undefined')
                else:
                    leg_reason = _explain(model, params, a, b)
            if len(endpoints) > 2:
                leg_reason = f'leg {index + 1} ({a["space_id"]} -> {b["space_id"]}): {leg_reason}'
            reason = leg_reason
            break
        legs.append({'index': index, 'from': _public_endpoint(model, a), 'to': _public_endpoint(model, b), **res})

    if notes['stop_anchor']:
        assumptions.append('Connector stops have no position in the data; the stop is the representative_point '
                           'of the stop space.')
    if notes['repaired_polygon']:
        warnings.append('Invalid polygons repaired with make_valid: ' + ', '.join(sorted(notes['repaired_polygon'])))
    if notes['unusable_polygon']:
        warnings.append('Spaces skipped (unusable polygon): ' + ', '.join(sorted(notes['unusable_polygon'])))
    if notes['missing_elevation'] and status != 'unsupported':
        warnings.append('Connectors skipped at floors without elevation_m: ' + ', '.join(sorted(notes['missing_elevation'])))
    if notes['bad_opening']:
        warnings.append('Openings skipped (unknown space or invalid position): ' + ', '.join(sorted(notes['bad_opening'])))
    if notes['bad_connector']:
        warnings.append('Connectors skipped (malformed stops): ' + ', '.join(sorted(notes['bad_connector'])))
    if notes['unknown_width']:
        warnings.append(f"{len(notes['unknown_width'])} opening(s) without width_m treated as usable.")
    for oid, sid, stub in sorted(notes['long_stub']):
        warnings.append(f'Opening {oid} position is {stub} m from the walkable area of {sid} (data offset).')

    result = {'model': 'challenge', 'status': status, 'reachable': status == 'ok',
              'space_path': [], 'element_path': [], 'cost_m': None, 'walk_m': 0.0, 'vertical_cost_m': 0.0,
              'legs': [], 'steps': [], 'segments': [], 'transitions': [],
              'start': _public_endpoint(model, endpoints[0]), 'target': _public_endpoint(model, endpoints[-1]),
              'via': [_public_endpoint(model, e) for e in endpoints[1:-1]],
              'rules': rules, 'unreachable_reason': reason, 'assumptions': assumptions, 'warnings': warnings}
    if status != 'ok':
        return result
    space_path, element_path, steps, segments, transitions = [], [], [], [], []
    for leg in legs:
        space_path += leg['space_path'] if not space_path else leg['space_path'][1:]
        element_path += leg['element_path']
        steps += leg['steps']
        segments += leg['segments']
        transitions += leg['transitions']
    walk_m = sum(leg['walk_m'] for leg in legs)
    vertical_m = sum(leg['vertical_cost_m'] for leg in legs)
    public_legs = [{k: leg[k] for k in ('index', 'from', 'to', 'space_path', 'element_path',
                                        'cost_m', 'walk_m', 'vertical_cost_m')} for leg in legs]
    result.update({'space_path': space_path, 'element_path': element_path, 'cost_m': walk_m + vertical_m,
                   'walk_m': walk_m, 'vertical_cost_m': vertical_m, 'legs': public_legs, 'steps': steps,
                   'segments': segments, 'transitions': transitions})
    return result


def challenge_route(building: dict, request: dict) -> dict:
    """Official Challenge Routing (see module docstring). Raises ModelError('invalid_input', ...)."""
    return _challenge_route(building, request)
