"""Independent portal-graph search over validated local visibility paths.

This does not refine a reference room-anchor route. Explicitly supported portals
form the search graph; unsafe/underspecified openings are never silently clipped.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import time

import networkx as nx
from shapely.geometry import LineString, Point

from errors import ModelError
from geometry import (EPS, build_space, parameters, prepare_assets, point_xy,
                      access_candidates, door_side, effective_opening)



def paired_sides(first, second, opening, step=.5):
    """Door approaches on both sides; wide open apertures may be crossed off-centre.

    The centre is tried first. Only for openings at least 2.5 m wide, a blocked centre is retried at
    positions shifted along the same boundary, each with a reduced nominal interval that stays inside the
    original one, so door_side re-validates walls, obstacles and clearance exactly as for the centre.
    """
    try:
        return opening, [door_side(first, opening), door_side(second, opening)]
    except ModelError as error:
        width = opening.get('width_m')
        if error.code != 'portal_blocked' or not isinstance(width, (int, float)) or width < 2.5:
            raise
        centre = Point(point_xy(opening['position']))
        shared = first.room.boundary.intersection(second.room.boundary)
        if shared.is_empty or shared.distance(centre) > 1e-6 or shared.geom_type != 'LineString':
            raise
        t = shared.project(centre)
        reach = width / 2 - max(first.clearance, second.clearance) - .05
        offsets = sorted({k * step * sign for k in range(1, int(reach / step) + 1) for sign in (1, -1)}, key=abs)
        for offset in offsets:
            if not 0 <= t + offset <= shared.length:
                continue
            moved = shared.interpolate(t + offset)
            candidate = {**opening, 'position': [moved.x, moved.y], 'width_m': width - 2 * abs(offset),
                         '_crossing_offset_m': round(offset, 3)}
            try:
                return candidate, [door_side(first, candidate), door_side(second, candidate)]
            except ModelError as retry:
                if retry.code != 'portal_blocked':
                    raise
        raise

def path_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def navigate(building: dict, request: dict) -> dict:
    started = time.perf_counter()
    if not isinstance(building, dict) or not isinstance(request, dict):
        raise ModelError('invalid_input', 'Navigation request must be an object')
    profile = request.get('profile', 'stationary')
    width, safety, clearance = parameters(profile, request.get('width_m', .6), request.get('safety_m', .05))
    step_free, schematic = request.get('step_free', False), request.get('allow_schematic_vertical', False)
    if not isinstance(step_free, bool) or not isinstance(schematic, bool):
        raise ModelError('invalid_input', 'step_free and allow_schematic_vertical must be booleans')
    seconds = request.get('max_seconds', 20)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not .05 <= seconds <= 60:
        raise ModelError('invalid_input', 'max_seconds must be .05..60')
    deadline = started + seconds
    spaces = {s['id']: s for s in building.get('spaces', [])}
    assets = {a['id']: a for a in building.get('assets', [])}
    openings = {o['id']: o for o in building.get('openings', [])}
    connectors = {c['id']: c for c in building.get('vertical_connectors', [])}
    floors = {f['id']: f for f in building.get('floors', [])}
    raw_closed = request.get('closed', [])
    if not isinstance(raw_closed, list) or not all(isinstance(s, str) for s in raw_closed):
        raise ModelError('invalid_input', 'closed must be a list of room/door/connector IDs')
    snap_limit = request.get('snap_m', 0)
    if isinstance(snap_limit, bool) or not isinstance(snap_limit, (int, float)) or not 0 <= snap_limit <= 2:
        raise ModelError('invalid_input', 'snap_m must be 0..2 m')
    snaps = []
    shifted = []
    closed = set(raw_closed)
    if not closed <= spaces.keys() | openings.keys() | connectors.keys():
        raise ModelError('invalid_input', 'Unknown closure ID')
    start_req, target_req = request.get('start'), request.get('target')
    if not isinstance(start_req, dict) or not isinstance(target_req, dict):
        raise ModelError('invalid_input', 'Select structured start and target')
    for spec in (start_req, target_req):
        if spec.get('kind') in ('space', 'asset', 'door') and not isinstance(spec.get('id'), str):
            raise ModelError('invalid_input', 'Endpoint IDs must be strings')
    forced = {spec['id'] for spec in (start_req, target_req) if spec.get('kind') == 'asset'}
    prepared = prepare_assets(building, profile, forced)
    contexts, unsupported, assumptions = {}, [], []

    def context(sid):
        if not isinstance(sid, str) or sid not in spaces:
            raise ModelError('invalid_input', 'Unknown endpoint/portal space', {'space_id': sid})
        if sid in closed:
            raise ModelError('blocked_by_closure', 'Room is closed', {'space_id': sid})
        if sid not in contexts:
            contexts[sid] = build_space(building, sid, profile, width, safety, prepared=prepared, deadline=deadline)
        return contexts[sid]

    def resolve(spec, is_target=False, origin=None):
        kind = spec.get('kind')
        if kind == 'point':
            sid, positions = spec.get('space_id'), [point_xy(spec.get('position'))]
            ctx = context(sid)
            note = 'Exact user point; no projection.'
            try:
                ctx.component(positions[0])
            except ModelError:
                snapped = ctx.snap(positions[0], snap_limit) if snap_limit > 0 else None
                if snapped is None:
                    raise
                positions = [snapped[0]]
                note = (f'Point moved {snapped[1]:.2f} m to the nearest admissible position in the same room '
                        f'(limit {snap_limit:.2f} m; no wall or other obstacle crossed).')
                snaps.append({'note': note, 'from': list(point_xy(spec.get('position'))), 'to': list(snapped[0]),
                              'distance_m': round(snapped[1], 3), 'role': 'target' if is_target else 'start'})
        elif kind == 'space':
            sid = spec.get('id')
            ctx = context(sid)
            positions = [ctx.automatic_point()]
            note = 'Automatically selected safe representative point of largest free component; not user localisation.'
            assumptions.append(note)
        elif kind == 'asset':
            asset = assets.get(spec.get('id'))
            if asset is None:
                raise ModelError('invalid_input', 'Unknown endpoint asset')
            sid = asset.get('space_id')
            ctx = context(sid)
            if is_target:
                positions = access_candidates(ctx, asset, origin)
                note = 'Reachable geometric access candidate; service/front side and continuous optimum are unknown.'
            else:
                automatic = ctx.automatic_point()
                candidates = access_candidates(ctx, asset, automatic)
                component = ctx.component(automatic)
                candidates = [p for p in candidates if ctx.component(p) == component]
                if not candidates:
                    raise ModelError('model_blocked', 'No asset start access in the automatic start component', {'asset_id': asset['id'], 'space_id': sid})
                positions = [min(candidates, key=lambda p: (math.dist(p, automatic), p))]
                note = 'Asset start uses one validated access candidate nearest the safe automatic room point; not its centre or a verified service side.'
            assumptions.append(note)
        elif kind == 'door':
            opening = openings.get(spec.get('id'))
            sid = spec.get('space_id')
            if opening is None or sid not in opening['space_ids']:
                raise ModelError('invalid_input', 'Choose a door and its actual room side')
            if opening['id'] in closed:
                raise ModelError('blocked_by_closure', 'Selected door is closed')
            if step_free and not opening.get('step_free', False):
                raise ModelError('blocked_by_step_free', 'Selected door is not marked step-free')
            ctx = context(sid)
            side = door_side(ctx, effective_opening(building, opening))
            positions = [tuple(side['inner'])]
            note = 'Door selection resolves to its safe inner approach point; crossing is a separate validated transition.'
            assumptions.append(note)
        else:
            raise ModelError('invalid_input', 'Endpoint kind must be point, space, door, or asset')
        return {'kind': kind, 'id': spec.get('id'), 'space_id': sid,
                'floor_id': ctx.space['floor_id'], 'positions': positions, 'resolution': note}

    start = resolve(start_req)
    target = resolve(target_req, True, start['positions'][0])
    if start['floor_id'] != target['floor_id'] and not schematic:
        raise ModelError('geometry_unsupported', 'Different floors need explicitly allowed schematic vertical transitions',
                         {'reason': 'No precise stair/lift entry and travel geometry in this schema'})
    allowed_floors = set(floors) if schematic else {start['floor_id']}
    graph = nx.Graph()
    room_nodes = defaultdict(list)
    portal_records = []

    def add_node(key, sid, position):
        xy = point_xy(position)
        graph.add_node(key, space_id=sid, position=xy, floor_id=spaces[sid]['floor_id'])
        room_nodes[sid].append(key)

    def record_failure(element, exc):
        if exc.code in {'computation_limit', 'numerical_failure'}:
            raise exc
        unsupported.append({'id': element, **exc.as_dict()})

    for opening in openings.values():
        if opening.get('floor_id') not in allowed_floors or opening['id'] in closed:
            continue
        if step_free and not opening.get('step_free', False):
            continue
        side_ids = opening.get('space_ids', [])
        if 'outside' in side_ids:
            unsupported.append({'id': opening['id'], 'code': 'outside_not_physical', 'message': 'No zero-cost outside jump in precise navigation'})
            continue
        if len(side_ids) != 2 or any(sid in closed for sid in side_ids):
            continue
        try:
            first, second = (context(sid) for sid in side_ids)
            if first.space['floor_id'] != second.space['floor_id']:
                raise ModelError('geometry_unsupported', 'Opening joins different floors')
            opening = effective_opening(building, opening)
            opening, sides = paired_sides(first, second, opening)
            normal_dot = sum(a*b for a, b in zip(sides[0]['normal'], sides[1]['normal']))
            shared = first.room.boundary.intersection(second.room.boundary)
            if (normal_dot > -.99999 or first.room.intersection(second.room).area > 1e-7
                    or shared.length < opening['width_m']-1e-6
                    or shared.distance(Point(opening['position'])) > 1e-6):
                raise ModelError('geometry_unsupported', 'Portal sides do not form opposite sides of one boundary')
            points = [sides[0]['inner'], sides[0]['centre'], sides[1]['inner']]
            if not first.room.union(second.room).covers(LineString(points)):
                raise ModelError('geometry_unsupported', 'Paired portal has a geometry gap; no teleportation')
            a, b = [('door', opening['id'], sid) for sid in side_ids]
            add_node(a, side_ids[0], points[0])
            add_node(b, side_ids[1], points[-1])
            graph.add_edge(a, b, weight=path_length(points), kind='door', from_node=a,
                           opening_id=opening['id'], sides=sides, points=points, physical_opening=opening)
            if opening.get('_crossing_offset_m'):
                shifted.append(f"Opening {opening['id']} is crossed {abs(opening['_crossing_offset_m']):.1f} m from its centre "
                               'because the centre approach is blocked; walls, obstacles and clearance re-checked there.')
            portal_records.append({'id': opening['id'], 'kind': 'door', 'floor_id': first.space['floor_id'],
                                   'position': list(opening['position']), 'sides': sides, 'points': points,
                                   'aperture': opening.get('_precision_aperture')})
        except ModelError as exc:
            record_failure(opening['id'], exc)

    if schematic:
        for connector in connectors.values():
            if connector['id'] in closed or (step_free and not connector.get('step_free', False)):
                continue
            if connector.get('kind') not in {'stairs', 'lift'}:
                unsupported.append({'id': connector['id'], 'code': 'geometry_unsupported', 'message': 'Unknown vertical connector kind'})
                continue
            try:
                stops = sorted(zip(connector['floor_ids'], connector['space_ids'], strict=True), key=lambda stop: floors[stop[0]]['level'])
            except (KeyError, ValueError, TypeError) as exc:
                raise ModelError('geometry_unsupported', 'Malformed vertical connector') from exc
            for (fa, sa), (fb, sb) in zip(stops, stops[1:]):
                if sa in closed or sb in closed:
                    continue
                try:
                    ca, cb = context(sa), context(sb)
                    if ca.space['floor_id'] != fa or cb.space['floor_id'] != fb:
                        raise ModelError('geometry_unsupported', 'Vertical stop space/floor mismatch')
                    pa, pb = ca.automatic_point(), cb.automatic_point()
                    a, b = ('vertical', connector['id'], sa), ('vertical', connector['id'], sb)
                    if a not in graph:
                        add_node(a, sa, pa)
                    if b not in graph:
                        add_node(b, sb, pb)
                    span = abs(floors[fb]['level']-floors[fa]['level'])
                    cost = 15+6*span if connector['kind'] == 'lift' else 12*span
                    graph.add_edge(a, b, weight=cost, kind='schematic_vertical', from_node=a,
                                   connector_id=connector['id'], connector_kind=connector['kind'], reference_cost_m=cost)
                except ModelError as exc:
                    record_failure(connector['id'], exc)

    source_node = ('start', 0)
    add_node(source_node, start['space_id'], start['positions'][0])
    terminal = '__target__'
    for i, p in enumerate(target['positions']):
        key = ('target', i)
        add_node(key, target['space_id'], p)
        graph.add_edge(key, terminal, weight=0, kind='terminal', from_node=key)

    # Add all supported within-room routes, not just those on a reference path.
    local_started = time.perf_counter()
    for sid, keys in room_nodes.items():
        ctx = context(sid)
        if len(keys) < 2:
            continue
        points = [graph.nodes[key]['position'] for key in keys]
        vis = ctx.graph_for(points)
        for i, a in enumerate(keys):
            ctx.check_budget()
            origin = graph.nodes[a]['position']
            distances, paths = nx.single_source_dijkstra(vis, origin, weight='weight')
            for b in keys[i+1:]:
                destination = graph.nodes[b]['position']
                if destination not in distances:
                    continue
                route_points = paths[destination]
                ctx.validate_path(route_points)
                graph.add_edge(a, b, weight=distances[destination], kind='local', from_node=a,
                               space_id=sid, points=route_points)
    local_ms = (time.perf_counter()-local_started)*1000
    try:
        search_cost, sequence = nx.single_source_dijkstra(graph, source_node, terminal, weight='weight')
    except nx.NetworkXNoPath as exc:
        code = 'geometry_unsupported' if unsupported else 'model_blocked'
        raise ModelError(code, 'No route in the supported portal graph; this is not proof of real-building unreachability',
                         {'unsupported_transition_counts': dict(Counter(x['code'] for x in unsupported)),
                          'unsupported_transitions': unsupported[:30], 'closed': sorted(closed)}) from exc

    segments, transitions, used_contexts, vertical_cost = [], [], set(), 0.0
    obstacle_clearances,wall_clearances=[],[]
    for a, b in zip(sequence, sequence[1:]):
        edge = graph[a][b]
        if edge['kind'] == 'terminal':
            continue
        forward = edge['from_node'] == a
        if edge['kind'] == 'local':
            sid = edge['space_id']
            points = edge['points'] if forward else list(reversed(edge['points']))
            context(sid).validate_path(points)
            ctx=context(sid)
            checked_line=LineString(points) if len(points)>1 else Point(points[0])
            wall_distance=checked_line.distance(ctx.room.boundary)
            wall_clearances.append(wall_distance)
            obstacle_distance=None if ctx.obstacles.is_empty else checked_line.distance(ctx.obstacles)
            if obstacle_distance is not None:obstacle_clearances.append(obstacle_distance)
            segments.append({'floor_id': spaces[sid]['floor_id'], 'space_id': sid,
                             'kind': 'local_visibility', 'points': [list(p) for p in points],
                             'length_m': path_length(points), 'validation': 'original_geometry_and_free_area',
                             'minimum_wall_clearance_m':wall_distance,'minimum_obstacle_clearance_m':obstacle_distance})
            used_contexts.add(sid)
        elif edge['kind'] == 'door':
            sides = edge['sides'] if forward else list(reversed(edge['sides']))
            opening = edge['physical_opening']
            # Recheck both original-side corridors at final assembly.
            for side in sides:
                checked_side=door_side(context(side['space_id']), opening)
                for field,values in [('minimum_obstacle_clearance_m',obstacle_clearances),('minimum_remaining_wall_clearance_m',wall_clearances)]:
                    if checked_side[field] is not None:values.append(checked_side[field])
            points = [sides[0]['inner'], sides[0]['centre'], sides[1]['inner']]
            transitions.append({'kind': 'door', 'id': opening['id'], 'points': points,
                                'from_space_id': sides[0]['space_id'], 'to_space_id': sides[1]['space_id'],
                                'floor_id': sides[0]['floor_id'], 'length_xy_m': path_length(points),
                                'width_m': opening['width_m'], 'aperture': opening.get('_precision_aperture'),
                                'status': 'paired_corridors_validated'})
            for side, stub in [(sides[0], points[:2]), (sides[1], points[1:])]:
                segments.append({'floor_id': side['floor_id'], 'space_id': side['space_id'],
                                 'kind': 'validated_door_approach', 'opening_id': opening['id'],
                                 'points': stub, 'length_m': path_length(stub),
                                 'aperture': opening.get('_precision_aperture'),
                                 'validation': 'aperture_exception_only; remaining_walls_and_obstacles_checked'})
                used_contexts.add(side['space_id'])
        else:
            vertical_cost += edge['reference_cost_m']
            transitions.append({'kind': edge['connector_kind'], 'id': edge['connector_id'],
                                'status': 'schematic_only', 'from_floor_id': graph.nodes[a]['floor_id'],
                                'to_floor_id': graph.nodes[b]['floor_id'],
                                'from_position': list(graph.nodes[a]['position']),
                                'to_position': list(graph.nodes[b]['position']),
                                'reference_cost_m': edge['reference_cost_m'], 'length_xy_m': 0,
                                'warning': 'Entry locations and vertical trajectory are not supplied; no interpolated 3D/XY route'})
            used_contexts.update([graph.nodes[a]['space_id'], graph.nodes[b]['space_id']])

    target_node = sequence[-2]
    selected_target = graph.nodes[target_node]['position']
    warnings = ['Only a static disc model in declared planning polygons; not certified real-world collision freedom.',
                'Stationarity and asset service sides are not verified.']
    warnings += [entry['note'] for entry in snaps]
    used_openings = {t.get('id') for t in transitions if t.get('kind') == 'door'}
    warnings += [note for note in shifted if note.split()[1] in used_openings]
    if profile == 'none':
        warnings.append('Furniture and machines are ignored (walls and doors only); only endpoint assets are obstacles.')
    if prepared[1]['unknown_asset_types']:
        warnings.append('Unknown asset types were excluded explicitly; inspect obstacle profile assumptions.')
    if unsupported:
        warnings.append('Some transitions are unsupported; optimality is only in the constructed supported portal graph.')
    has_schematic = any(t.get('status') == 'schematic_only' for t in transitions)
    if has_schematic:
        warnings.append('Route includes schematic vertical transitions; the complete route is not fully geometrically validated.')
    length = sum(s['length_m'] for s in segments)
    geometries = [context(sid).export() for sid in sorted(used_contexts | {start['space_id'], target['space_id']})]
    return {'model': 'precise', 'status': 'schematic_vertical' if has_schematic else 'validated_2d_model',
            'fully_geometrically_checked': not has_schematic, 'fully_checked': not has_schematic, 'length_m': length,
            'snapped_endpoints': snaps,
            'search_cost_m_equivalent': search_cost, 'schematic_transition_cost_m': vertical_cost,
            'profile': profile, 'width_m': width, 'safety_m': safety, 'clearance_m': clearance,
            'validation_summary':{'predicate_tolerance_m':EPS,'minimum_selected_obstacle_clearance_m':min(obstacle_clearances) if obstacle_clearances else None,
                                  'minimum_non_aperture_wall_clearance_m':min(wall_clearances) if wall_clearances else None,
                                  'fully_checked_scope':'XY paths and supported door apertures; schematic vertical legs excluded'},
            'start': {**start, 'positions': [list(p) for p in start['positions']]},
            'target': {**target, 'positions': [list(p) for p in target['positions']],
                       'selected_position': list(selected_target)},
            'segments': segments, 'transitions': transitions,
            'geometry': geometries, 'geometries': geometries,
            'portals': portal_records, 'unsupported_transitions': unsupported,
            'assumptions': ['Factory/campus centre/CCW footprint convention; source verification is required by the service.',
                            'Space boundaries are conservative planning limits, not evidence of physical walls.',
                            'Door centres are discrete portal samples; optimality is in this constructed graph only.',
                            'No reference anchor route or zero-cost outside link is used.'] + assumptions,
            'warnings': warnings, 'metrics': {'elapsed_ms': (time.perf_counter()-started)*1000,
                                            'local_graph_ms': local_ms, 'spaces_built': len(contexts),
                                            'global_nodes': graph.number_of_nodes(), 'global_edges': graph.number_of_edges(),
                                            'visibility_edges_checked': sum(c.checked_edges for c in contexts.values()),
                                            'unsupported_transition_counts': dict(Counter(x['code'] for x in unsupported))}}
