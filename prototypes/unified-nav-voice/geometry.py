"""Conservative 2-D configuration-space geometry, no model clients or file writes.

The supported factory asset convention is centre + CCW rotation. Polygon edges
are planning-domain limits, not certified physical walls. A route is checked
against both the derived free set and the original room/obstacle geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time

import networkx as nx
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, Point, LineString, GeometryCollection, mapping, shape
from shapely.ops import unary_union, nearest_points
from shapely.prepared import prep

from errors import ModelError

EPS = 1e-7
PORTAL_TOL = 1e-6
MAX_VERTICES = 650
STATIONARY = frozenset({
    'cnc_mill', 'drill_press', 'conveyor', 'industrial_3d_printer', '3d_printer',
    'curing_oven', 'injection_moulding_machine', 'waterjet_cutter', 'compressor',
    'coordinate_measuring_machine', 'pallet_rack', 'server_rack', 'bookshelf',
})
SNAPSHOT = STATIONARY | frozenset({
    'desk', 'reception_desk', 'workbench', 'assembly_station', 'inspection_bench',
    'packing_station', 'bench', 'chair', 'office_chair', 'meeting_table', 'dining_table',
    'filing_cabinet', 'tool_cabinet', 'locker', 'shoe_cabinet', 'coat_rack', 'plant',
    'kitchen_counter', 'sink', 'stove', 'fridge', 'dishwasher', 'toilet', 'vending_machine',
    'welding_station', 'forklift', 'pallet_jack', 'forklift_charging_station', 'ups_unit',
})
EXCLUDED = frozenset({'display_screen', 'whiteboard', 'fire_extinguisher', 'hand_dryer',
                      'microwave', 'coffee_machine', 'microscope'})


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelError('invalid_input', f'{name} must be a finite number')
    return float(value)


def parameters(profile='stationary', width_m=.6, safety_m=.05):
    if not isinstance(profile, str) or profile not in {'stationary', 'all'}:
        raise ModelError('invalid_input', 'Obstacle profile must be stationary or all')
    width, safety = finite(width_m, 'width_m'), finite(safety_m, 'safety_m')
    if not .05 <= width <= 5 or not 0 <= safety <= 2:
        raise ModelError('invalid_input', 'Width must be .05..5 m and safety 0..2 m')
    return width, safety, width / 2 + safety


def check_convention(building):
    info = building.get('building', {})
    if info.get('source') != 'procedural' or info.get('source_ref', {}).get('generator') not in {'factory', 'campus'}:
        raise ModelError('geometry_unsupported', 'Precise navigation currently supports verified procedural factory/campus coordinates only',
                         {'reason': 'asset centre/rotation convention not verified for this source'})


def polygon_of(space):
    raw = space.get('polygon')
    try:
        if isinstance(raw, dict) and 'type' in raw:
            poly = shape(raw)
        elif isinstance(raw, dict):
            poly = Polygon(raw['exterior'], raw.get('holes', []))
        elif raw and isinstance(raw[0][0], (list, tuple)):
            poly = Polygon(raw[0], raw[1:])
        else:
            poly = Polygon(raw, space.get('holes', []))
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise ModelError('geometry_unsupported', 'Room polygon is missing or malformed', {'space_id': space.get('id')}) from exc
    if poly.geom_type not in {'Polygon', 'MultiPolygon'} or poly.is_empty or not poly.is_valid or not math.isfinite(poly.area):
        raise ModelError('geometry_unsupported', 'Invalid room polygon; no silent repair is applied', {'space_id': space.get('id')})
    if not all(math.isfinite(v) for v in poly.bounds):
        raise ModelError('geometry_unsupported', 'Non-finite room coordinates')
    return poly


def asset_polygon(asset):
    try:
        pos, footprint = asset['position'], asset['footprint']
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ModelError('geometry_unsupported', 'Asset requires two position coordinates')
        x, y = (finite(v, 'asset position') for v in pos)
        width, depth = finite(footprint['width_m'], 'asset width'), finite(footprint['depth_m'], 'asset depth')
        angle = finite(footprint.get('rotation_deg', 0), 'asset rotation')
        if not 0 < width <= 1000 or not 0 < depth <= 1000:
            raise ModelError('geometry_unsupported', 'Asset footprint dimensions must be positive and bounded')
        return translate(rotate(Polygon([(-width/2, -depth/2), (width/2, -depth/2),
                                        (width/2, depth/2), (-width/2, depth/2)]), angle, origin=(0, 0)), x, y)
    except (KeyError, TypeError) as exc:
        raise ModelError('geometry_unsupported', 'Missing asset footprint/position', {'asset_id': asset.get('id')}) from exc


def prepare_assets(building, profile, force_ids=()):
    allowed, forced = (STATIONARY if profile == 'stationary' else SNAPSHOT), set(force_ids)
    result, excluded, unknown = [], [], []
    for asset in building.get('assets', []):
        kind = asset.get('type')
        if kind in allowed or asset.get('id') in forced:
            result.append((asset, asset_polygon(asset)))
        else:
            excluded.append(asset.get('id'))
            if kind not in SNAPSHOT and kind not in EXCLUDED:
                unknown.append({'id': asset.get('id'), 'type': kind})
    return result, {'excluded_asset_ids': excluded, 'unknown_asset_types': unknown,
                    'stationarity_verified': False, 'moving_objects': 'static snapshot only'}


def polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if geometry.geom_type in {'MultiPolygon', 'GeometryCollection'}:
        return [p for g in geometry.geoms for p in polygons(g)]
    return []


def point_xy(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ModelError('invalid_input', 'Point requires [x, y] in metres')
    return tuple(finite(v, 'coordinate') for v in value)


def ring_vertices(poly):
    result = []
    for part in polygons(poly):
        for ring in [part.exterior, *part.interiors]:
            coords = list(ring.coords)[:-1]
            for i, p in enumerate(coords):
                a, b = coords[i-1], coords[(i+1) % len(coords)]
                cross = (p[0]-a[0])*(b[1]-p[1]) - (p[1]-a[1])*(b[0]-p[0])
                if abs(cross) > 1e-12:
                    result.append(tuple(p))
    return list(dict.fromkeys(result))


@dataclass
class SpaceGeometry:
    space: dict
    room: object
    clearance: float
    obstacle_records: list
    obstacles: object
    expanded: object
    free: object
    profile: str
    facts: dict
    build_ms: float
    deadline: float = float('inf')
    visibility: object = None
    checked_edges: int = 0
    _prepared: object = field(default=None, repr=False)

    @property
    def components(self):
        return sorted(polygons(self.free), key=lambda p: (-p.area, p.bounds))

    def check_budget(self):
        if time.perf_counter() > self.deadline:
            raise ModelError('computation_limit', 'Navigation time budget reached', {'space_id': self.space['id']})

    def component(self, xy):
        point = Point(point_xy(xy))
        for index, part in enumerate(self.components):
            if part.covers(point) and self.segment_valid(xy, xy):
                return index
        raise ModelError('blocked_endpoint', 'Point is outside the admissible free area',
                         {'space_id': self.space['id'], 'position': list(xy), 'clearance_m': self.clearance})

    def segment_valid(self, a, b):
        a, b = point_xy(a), point_xy(b)
        line = Point(a) if a == b else LineString([a, b])
        if self._prepared is None:
            self._prepared = prep(self.free)
        if not self._prepared.covers(line) or not self.room.covers(line):
            return False
        # Independent original-geometry checks, not an expanded tolerance mask.
        if line.distance(self.room.boundary) < self.clearance - EPS:
            return False
        if not self.obstacles.is_empty and line.distance(self.obstacles) < self.clearance - EPS:
            return False
        return True

    def graph_for(self, endpoints=()):
        self.check_budget()
        if self.visibility is None:
            corners = ring_vertices(self.free)
            if len(corners) > MAX_VERTICES:
                raise ModelError('computation_limit', 'Too many free-geometry vertices for this visibility prototype',
                                 {'space_id': self.space['id'], 'vertices': len(corners), 'limit': MAX_VERTICES})
            self.visibility = nx.Graph()
            for p in corners:
                self.visibility.add_node(p)
            for i, p in enumerate(corners):
                if i % 8 == 0:
                    self.check_budget()
                for q in corners[i+1:]:
                    self.checked_edges += 1
                    if self.segment_valid(p, q):
                        self.visibility.add_edge(p, q, weight=math.dist(p, q))
        for raw in endpoints:
            p = point_xy(raw)
            self.component(p)
            if p in self.visibility:
                continue
            for q in list(self.visibility):
                self.checked_edges += 1
                if self.segment_valid(p, q):
                    self.visibility.add_edge(p, q, weight=math.dist(p, q))
            self.visibility.add_node(p)
        self.check_budget()
        return self.visibility

    def path(self, start, target):
        self.check_budget()
        start, target = point_xy(start), point_xy(target)
        if self.component(start) != self.component(target):
            raise ModelError('disconnected', 'Endpoints are in different free-area components', {'space_id': self.space['id']})
        if self.segment_valid(start, target):
            return [list(start), list(target)] if start != target else [list(start)]
        graph = self.graph_for([start, target])
        try:
            points = nx.shortest_path(graph, start, target, weight='weight')
        except nx.NetworkXNoPath as exc:
            raise ModelError('model_blocked', 'No valid visibility path in this planning domain', {'space_id': self.space['id']}) from exc
        self.validate_path(points)
        return [list(p) for p in points]

    def validate_path(self, points):
        if not points or not all(self.segment_valid(a, b) for a, b in zip(points, points[1:])) or not self.segment_valid(points[0], points[0]):
            raise ModelError('numerical_failure', 'Final route failed independent geometric validation', {'space_id': self.space['id']})

    def automatic_point(self):
        for part in self.components:
            p = tuple(part.representative_point().coords[0])
            if self.segment_valid(p, p):
                return p
        raise ModelError('model_blocked', 'No nonempty safe location in this room', {'space_id': self.space['id']})

    def export(self):
        return {'space_id': self.space['id'], 'floor_id': self.space['floor_id'],
                'profile': self.profile, 'clearance_m': self.clearance,
                'room': mapping(self.room), 'free': mapping(self.free),
                'expanded_obstacles': mapping(self.expanded),
                'obstacles': [{'id': a['id'], 'type': a.get('type'), 'source': a.get('source'), 'geometry': mapping(poly)} for a, poly in self.obstacle_records],
                'components': [{'id': i, 'area_m2': part.area, 'geometry': mapping(part)} for i, part in enumerate(self.components)],
                'facts': self.facts, 'build_ms': self.build_ms}


def build_space(building, space_id, profile='stationary', width_m=.6, safety_m=.05,
                prepared=None, force_ids=(), deadline=float('inf')):
    start = time.perf_counter()
    check_convention(building)
    width, safety, clearance = parameters(profile, width_m, safety_m)
    space = next((s for s in building.get('spaces', []) if s.get('id') == space_id), None)
    if space is None:
        raise ModelError('invalid_input', 'Unknown space_id')
    room = polygon_of(space)
    assets, facts = prepared if prepared is not None else prepare_assets(building, profile, force_ids)
    # Select spatially, never only by declared space_id. Include nearby footprints
    # whose clearance expansion reaches into the domain.
    selected = [(a, poly) for a, poly in assets if a.get('floor_id') == space['floor_id'] and poly.distance(room) <= clearance]
    obstacle_union = unary_union([poly for _, poly in selected]) if selected else GeometryCollection()
    effective = clearance + EPS * 4
    expanded = obstacle_union.buffer(effective, join_style='mitre', mitre_limit=1000) if selected else GeometryCollection()
    inset = room.buffer(-effective, join_style='mitre', mitre_limit=1000)
    free = inset.difference(expanded)
    if not free.is_valid:
        raise ModelError('numerical_failure', 'Derived free geometry is invalid; no silent repair')
    return SpaceGeometry(space, room, clearance, selected, obstacle_union, expanded, free,
                         profile, {**facts, 'width_m': width, 'safety_m': safety,
                                   'domain_boundary_is_assumed_wall_limit': True,
                                   'buffer_extra_m': EPS*4,'predicate_tolerance_m':EPS,
                                   'corner_offset_model':'Conservative mitre offsets; corner detours can exceed exact circular-disc optima'}, (time.perf_counter()-start)*1000, deadline)


def analyze_space(building, space_id, profile='stationary', width_m=.6, safety_m=.05):
    ctx = build_space(building, space_id, profile, width_m, safety_m)
    return {'model': 'precise_geometry', 'geometry': ctx.export(),
            'assumptions': ['Factory asset positions are centres; rotation is CCW in world XY.',
                            'Space boundaries are conservative planning limits, not verified physical walls.',
                            'The person is a disc; this is not certified accessibility or vehicle planning.']}


def access_candidates(ctx, asset, origin=None):
    if not any(record.get('id') == asset.get('id') for record, _ in ctx.obstacle_records):
        raise ModelError('geometry_unsupported', 'Target asset is not in this room/floor obstacle model; inconsistent assignment',
                         {'asset_id': asset.get('id'), 'space_id': ctx.space['id']})
    poly = asset_polygon(asset).buffer(ctx.clearance + EPS*8, join_style='mitre', mitre_limit=1000)
    points = []
    for part in polygons(poly):
        coords = list(part.exterior.coords)
        for a, b in zip(coords, coords[1:]):
            points.extend([a, ((a[0]+b[0])/2, (a[1]+b[1])/2)])
            if origin is not None:
                points.append(tuple(nearest_points(LineString([a, b]), Point(origin))[0].coords[0]))
    # Corners/midpoints alone can miss a short accessible part of a side.
    # Clip the access contour to the actual free set before sampling. Keep all
    # components: global routing, not this sampler, decides reachability.
    def sample_access_piece(piece):
        if piece.is_empty:
            return
        if piece.geom_type == 'Point':
            points.append(tuple(piece.coords[0]))
        elif piece.geom_type in {'LineString', 'LinearRing'}:
            points.extend([tuple(piece.coords[0]), tuple(piece.coords[-1]),
                           tuple(piece.interpolate(.5, normalized=True).coords[0])])
            if origin is not None:
                points.append(tuple(nearest_points(piece, Point(origin))[0].coords[0]))
        elif hasattr(piece, 'geoms'):
            for member in piece.geoms:
                sample_access_piece(member)
    sample_access_piece(poly.boundary.intersection(ctx.free))
    valid = []
    for p in dict.fromkeys(tuple(p) for p in points):
        if ctx.segment_valid(p, p):
            valid.append(p)
    if not valid:
        raise ModelError('model_blocked', 'No admissible access candidate around the target asset', {'asset_id': asset['id']})
    return valid


def boundary_segments(room):
    for part in polygons(room):
        for ring in [part.exterior, *part.interiors]:
            coords = list(ring.coords)
            for a, b in zip(coords, coords[1:]):
                if math.dist(a, b) > EPS:
                    yield LineString([a, b])


def effective_opening(building, opening):
    """Conservative declared aperture for verified procedural hall zones only.

    `opening` means no door leaf in the official schema. For a full-width
    hall/hall opening the usable aperture is its intersection with the actual
    shared straight boundary, never the nominal overhang past a room corner.
    Physical door/entrance widths and source records remain untouched.
    """
    info = building.get('building', {})
    ids = opening.get('space_ids', [])
    if (info.get('source') != 'procedural'
            or info.get('source_ref', {}).get('generator') != 'factory'
            or opening.get('kind') != 'opening' or opening.get('synthetic', False)
            or len(ids) != 2):
        return opening
    spaces = {s['id']: s for s in building.get('spaces', [])}
    if any(sid not in spaces or spaces[sid].get('type') not in {'hall', 'corridor'} for sid in ids):
        return opening
    first, second = (polygon_of(spaces[sid]) for sid in ids)
    if (spaces[ids[0]]['floor_id'] != spaces[ids[1]]['floor_id']
            or first.intersection(second).area > EPS):
        return opening
    shared = first.boundary.intersection(second.boundary)
    if shared.geom_type != 'LineString' or shared.length <= EPS:
        return opening
    centre = Point(point_xy(opening.get('position')))
    width = finite(opening.get('width_m'), 'opening width_m')
    # Entire shared boundary must be one straight edge in BOTH domains, and
    # the declared interval must cover it symmetrically. This is not a general
    # repair for malformed portals or arbitrarily offset openings.
    if (centre.distance(shared.interpolate(.5, normalized=True)) > PORTAL_TOL
            or width < shared.length - PORTAL_TOL
            or not all(any(edge.hausdorff_distance(shared) <= PORTAL_TOL
                           for edge in boundary_segments(room)) for room in (first, second))):
        return opening
    coords = list(shared.coords)
    if abs(math.dist(coords[0], coords[-1])-shared.length) > PORTAL_TOL:
        return opening
    aperture = {'kind': 'declared_open_shared_boundary',
                'nominal_width_m': width, 'effective_width_m': min(width, shared.length),
                'segment': [list(coords[0]), list(coords[-1])],
                'derivation': 'Declared no-leaf opening intersects the exact common straight hall boundary; no nominal overhang is usable.'}
    return {**opening, 'width_m': aperture['effective_width_m'], '_precision_aperture': aperture}


def door_side(ctx, opening):
    centre = point_xy(opening.get('position'))
    width = finite(opening.get('width_m'), 'door width_m')
    if width < 2*ctx.clearance - EPS:
        raise ModelError('door_too_narrow', 'Door is narrower than the configured person plus safety margin',
                         {'opening_id': opening['id'], 'width_m': width, 'required_m': 2*ctx.clearance})
    segments = list(boundary_segments(ctx.room))
    matches = [s for s in segments if s.distance(Point(centre)) <= PORTAL_TOL]
    if len(matches) != 1:
        raise ModelError('geometry_unsupported', 'Door centre has no unique straight boundary segment', {'opening_id': opening['id'], 'space_id': ctx.space['id']})
    segment = matches[0]
    t = segment.project(Point(centre))
    if t-width/2 < -PORTAL_TOL or t+width/2 > segment.length+PORTAL_TOL:
        raise ModelError('geometry_unsupported', 'Nominal opening width exceeds its boundary segment; no clipping is applied',
                         {'opening_id': opening['id'], 'width_m': width, 'segment_length_m': segment.length})
    a, b = list(segment.coords)
    tangent = ((b[0]-a[0])/segment.length, (b[1]-a[1])/segment.length)
    normals = [(-tangent[1], tangent[0]), (tangent[1], -tangent[0])]
    inside = [n for n in normals if ctx.room.contains(Point(centre[0]+n[0]*.0001, centre[1]+n[1]*.0001))]
    if len(inside) != 1:
        raise ModelError('geometry_unsupported', 'Door inward normal is ambiguous', {'opening_id': opening['id']})
    normal = inside[0]
    depth = ctx.clearance + EPS*10
    inner = (centre[0]+normal[0]*depth, centre[1]+normal[1]*depth)
    stub = LineString([centre, inner])
    # Remove only the validated aperture from this side's wall boundary. All
    # remaining walls and original obstacles still require full clearance.
    walls = [s for s in segments if s is not segment]
    left, right = segment.interpolate(max(0, t-width/2)), segment.interpolate(min(segment.length, t+width/2))
    if t-width/2 > EPS:
        walls.append(LineString([a, tuple(left.coords[0])]))
    if t+width/2 < segment.length-EPS:
        walls.append(LineString([tuple(right.coords[0]), b]))
    remaining = unary_union(walls)
    if (not ctx.room.covers(stub) or not ctx.segment_valid(inner, inner)
            or (not remaining.is_empty and stub.distance(remaining) < ctx.clearance-EPS)
            or (not ctx.obstacles.is_empty and stub.distance(ctx.obstacles) < ctx.clearance-EPS)):
        raise ModelError('portal_blocked', 'Door approach corridor is blocked or disconnected from the free area',
                         {'opening_id': opening['id'], 'space_id': ctx.space['id']})
    return {'opening_id': opening['id'], 'space_id': ctx.space['id'], 'floor_id': ctx.space['floor_id'],
            'centre': list(centre), 'inner': list(inner), 'normal': list(normal),
            'width_m': width, 'stub_length_m': stub.length, 'component_id': ctx.component(inner),
            'minimum_obstacle_clearance_m':None if ctx.obstacles.is_empty else stub.distance(ctx.obstacles),
            'minimum_remaining_wall_clearance_m':None if remaining.is_empty else stub.distance(remaining),
            'points': [list(centre), list(inner)], 'aperture': opening.get('_precision_aperture'),
            'validation': 'original domain, remaining walls, original obstacles'}
