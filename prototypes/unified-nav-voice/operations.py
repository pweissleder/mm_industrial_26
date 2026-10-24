"""Nine deterministic building operations. No language parser, examples or answer lookups."""
from __future__ import annotations

import copy
import csv
import io
import math

import networkx as nx
from shapely.geometry import Polygon, shape

from data import reference_map
from errors import ModelError

_INFRA = {'corridor', 'stair', 'lift', 'outdoor'}


def operation_specs():
    def field(name, label, kind, default=None, required=True):
        result = {'name': name, 'label': label, 'type': kind, 'required': required}
        if default is not None: result['default'] = default
        return result
    floor = field('floor_id', 'Floor', 'floor')
    room_type = field('room_type', 'Room type', 'room_type')
    start = field('start', 'Start room', 'space')
    pair = [field('first', 'First room', 'space'), field('second', 'Second room', 'space')]
    return [
        {'name':'largest_room', 'label':'Largest room', 'fields':[floor, room_type]},
        {'name':'floor_area_total', 'label':'Total room area', 'fields':[floor, room_type]},
        {'name':'errand', 'label':'Pick up and deliver to largest target room', 'fields':[start, field('asset_type','Pickup asset type','asset_type'), floor, room_type, field('step_free','Step-free','boolean',False,False)]},
        {'name':'wide_delivery_count', 'label':'Rooms reachable for wide delivery', 'fields':[start, floor, field('min_width','Minimum door width (m)','number',1.1)]},
        {'name':'step_free_reachable_count', 'label':'Step-free reachable rooms with closure', 'fields':[start, floor, field('closed','Closed connector','connector',required=False)]},
        {'name':'room_stacked', 'label':'Room above / below', 'fields':[start,field('direction','Direction','direction','above'),field('assume_aligned','Explicitly assume unchecked floor XY alignment','boolean',False,False)]},
        {'name':'shared_walls_count', 'label':'Count rooms sharing a wall', 'fields':[start,field('min_length','Minimum length (m)','number',1.0,False),field('wall_tolerance','Boundary tolerance (m)','number',.35,False)]},
        {'name':'straight_line_3d', 'label':'3D straight line between room centroids', 'fields':pair+[field('assume_aligned','Explicitly assume unchecked floor XY alignment','boolean',False,False)]},
        {'name':'what_if_door', 'label':'New door: shorter routes to exits', 'fields':pair+[floor,field('improvement_m','Improvement strictly greater than (m)','number',1.0,False)]},
    ]


def _number(arguments, key, default=None, minimum=0):
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < minimum:
        raise ModelError('invalid_input', f'{key} must be a finite number >= {minimum}')
    return float(value)


def _boolean(arguments,key,default=False):
    value=arguments.get(key,default)
    if not isinstance(value,bool): raise ModelError('invalid_input',f'{key} must be boolean')
    return value


def _polygon(space):
    raw=space.get('polygon')
    try:
        if isinstance(raw,dict): result=shape(raw)
        elif raw and isinstance(raw[0][0],(list,tuple)): result=Polygon(raw[0],raw[1:])
        else: result=Polygon(raw)
    except (TypeError,ValueError,KeyError) as error:
        raise ModelError('geometry_underdetermined','Invalid room polygon',{'space_id':space.get('id')}) from error
    if result.is_empty or not result.is_valid or result.geom_type not in {'Polygon','MultiPolygon'} or not math.isfinite(result.area):
        raise ModelError('geometry_underdetermined','Empty, invalid or non-polygon room geometry',{'space_id':space.get('id')})
    return result


def _space(building,value):
    if not isinstance(value,str): raise ModelError('invalid_input','Select a room ID')
    spaces={s['id']:s for s in building['spaces']}
    if value in spaces: return spaces[value]
    assets={a['id']:a for a in building.get('assets',[])}
    if value in assets and assets[value]['space_id'] in spaces: return spaces[assets[value]['space_id']]
    raise ModelError('invalid_input','Unknown room or asset ID',{'id':value})


def _floor(building,value):
    floor=next((f for f in building['floors'] if f['id']==value),None)
    if floor is None: raise ModelError('invalid_input','Select a valid floor ID')
    return floor


def _rooms(building,floor_id,room_type=None):
    _floor(building,floor_id)
    return [s for s in building['spaces'] if s['floor_id']==floor_id and (room_type is None or s.get('room_type')==room_type)]


def _largest(building,arguments):
    kind=arguments.get('room_type')
    if not isinstance(kind,str) or not kind: raise ModelError('invalid_input','Select a room type')
    rooms=_rooms(building,arguments.get('floor_id'),kind)
    if not rooms: raise ModelError('invalid_input','No rooms of selected type on this floor')
    # Stable input order resolves equal areas; no building-specific choices.
    return max(rooms,key=lambda s:_polygon(s).area)


def _alignment(building,spaces,arguments):
    if len({s['floor_id'] for s in spaces})<2: return []
    source=building.get('building',{}).get('source')
    metadata=building.get('building',{}).get('source_ref',{})
    uncertain=source=='swiss_dwellings' or metadata.get('floors_aligned') is False
    if uncertain and not _boolean(arguments,'assume_aligned'):
        raise ModelError('geometry_underdetermined','Floor XY alignment of this source is not verified; an explicit assumption is required',{'source':source})
    return ['Floor XY alignment explicitly assumed; not geometrically verified.'] if uncertain else ['Shared building XY frame assumed per kit convention.']


def _graph(building,closed=None,min_width=None,step_free=False,scenario_closed=()):
    model=reference_map(building)
    if not isinstance(scenario_closed,(list,tuple)) or any(not isinstance(x,str) for x in scenario_closed):
        raise ModelError('invalid_input','Scenario closures must be a list of IDs')
    known=set(model.spaces)|set(model.openings)|set(model.connectors)
    for element in [*scenario_closed,*([closed] if closed else [])]:
        if not isinstance(element,str) or element not in known: raise ModelError('invalid_input','Unknown closure ID')
        model.set_closed(element,True)
    graph=model.graph(step_free)
    if min_width is not None:
        graph.remove_nodes_from(['door:'+o['id'] for o in model.openings.values() if o.get('width_m',0)<min_width])
    return graph


def _lengths(graph,start):
    if start not in graph: raise ModelError('blocked','Selected start is not in the active reference graph')
    return nx.single_source_dijkstra_path_length(graph,start,weight='weight')


def _lines(geometry):
    if geometry.geom_type=='LineString': return [geometry]
    if hasattr(geometry,'geoms'): return [line for child in geometry.geoms for line in _lines(child)]
    return []


def compute(building,operation,arguments):
    """Compute from a building dict and explicit parameters, never a question/answer."""
    if not isinstance(arguments,dict) or not isinstance(operation,str) or operation not in {s['name'] for s in operation_specs()}:
        raise ModelError('invalid_input','Unknown operation or invalid argument object')
    if not isinstance(building,dict) or not all(key in building for key in ('spaces','floors')):
        raise ModelError('invalid_input','A building with rooms and floors is required')
    scenario_closed=arguments.get('_scenario_closed',[])
    result={'model':'benchmark','operation':operation,'ids':[],'assumptions':[],'trace':{},'cost_model':'official_reference' if operation in {'errand','wide_delivery_count','step_free_reachable_count','what_if_door'} else 'polygon_geometry'}
    if operation in {'largest_room','floor_area_total'}:
        if operation=='largest_room':
            room=_largest(building,arguments)
            result.update(value=_polygon(room).area,unit='m2',ids=[room['id']],space_id=room['id'],space_name=room.get('name',room['id']),space_ref=room.get('ref'))
        else:
            kind=arguments.get('room_type')
            if not isinstance(kind,str) or not kind: raise ModelError('invalid_input','Select a room type')
            rooms=_rooms(building,arguments.get('floor_id'),kind)
            result.update(value=sum(_polygon(s).area for s in rooms),unit='m2',ids=[s['id'] for s in rooms])
    elif operation=='errand':
        start=_space(building,arguments.get('start'))['id'];target=_largest(building,arguments)
        asset_type=arguments.get('asset_type')
        if not isinstance(asset_type,str) or not asset_type: raise ModelError('invalid_input','Select an asset type')
        graph=_graph(building,step_free=_boolean(arguments,'step_free'),scenario_closed=scenario_closed)
        distances=_lengths(graph,start)
        candidates=list(dict.fromkeys(a['space_id'] for a in building.get('assets',[]) if a.get('type')==asset_type and a['space_id'] in distances))
        if not candidates: raise ModelError('blocked','No reachable pickup room containing this asset type')
        via=min(candidates,key=lambda sid:distances[sid])
        after=_lengths(graph,via)
        if target['id'] not in after: raise ModelError('blocked','Largest target room is unreachable after pickup')
        result.update(value=round(distances[via]+after[target['id']],2),unit='m',ids=[start,via,target['id']],via=via,target=target['id'],trace={'pickup_length_m':distances[via],'delivery_length_m':after[target['id']]})
        result['assumptions'].append('Nearest pickup room is chosen first by reference walking cost; not by globally minimizing both legs. Asset access is its room anchor in this benchmark contract.')
    elif operation in {'wide_delivery_count','step_free_reachable_count'}:
        start=_space(building,arguments.get('start'))['id'];floor_id=arguments.get('floor_id');_floor(building,floor_id)
        width=_number(arguments,'min_width',minimum=0) if operation=='wide_delivery_count' else None
        closed=arguments.get('closed') if operation=='step_free_reachable_count' else None
        graph=_graph(building,closed=closed,min_width=width,step_free=True,scenario_closed=scenario_closed)
        reachable=_lengths(graph,start)
        rooms=[s['id'] for s in building['spaces'] if s['floor_id']==floor_id and s['type']=='room' and s['id'] in reachable]
        result.update(value=len(rooms),unit='rooms',ids=rooms,trace={'start':start,'closed':closed,'min_width_m':width,'step_free':True})
        result['assumptions'].append('Count only type=room, not hall/corridor/stair/lift/outdoor; width comparison is inclusive. This is reference reachability, not obstacle clearance.')
    elif operation=='room_stacked':
        start=_space(building,arguments.get('start'));direction=arguments.get('direction')
        if direction not in {'above','below'}: raise ModelError('invalid_input','Direction must be above or below')
        floor=_floor(building,start['floor_id']);sign=1 if direction=='above' else -1
        possible=[f for f in building['floors'] if (f['level']-floor['level'])*sign>0]
        if not possible: raise ModelError('invalid_input','No next floor in requested direction')
        other_floor=min(possible,key=lambda f:abs(f['level']-floor['level']))
        candidates=[s for s in building['spaces'] if s['floor_id']==other_floor['id'] and s['type'] not in _INFRA]
        if not candidates: raise ModelError('geometry_underdetermined','No candidate rooms on next floor')
        result['assumptions']+=_alignment(building,[start,candidates[0]],arguments)
        polygon=_polygon(start);overlaps=[(s,polygon.intersection(_polygon(s)).area) for s in candidates]
        room,area=max(overlaps,key=lambda item:item[1])
        if area<=1e-9: raise ModelError('geometry_underdetermined','No positive-area overlap on next floor')
        result.update(value=room['id'],unit='space',ids=[room['id']],space_id=room['id'],space_name=room.get('name',room['id']),space_ref=room.get('ref'),trace={'overlap_m2':area,'source_space':start['id'],'target_floor':other_floor['id']})
    elif operation=='shared_walls_count':
        start=_space(building,arguments.get('start'));polygon=_polygon(start)
        tolerance=_number(arguments,'wall_tolerance',.35);length=_number(arguments,'min_length',1.0,minimum=.000001)
        matches=[];lengths={}
        boundary=polygon.boundary.buffer(tolerance) if tolerance>0 else polygon.boundary
        for other in building['spaces']:
            if other['id']==start['id'] or other['floor_id']!=start['floor_id'] or other['type'] in _INFRA:continue
            shared=_polygon(other).boundary.intersection(boundary).length
            if shared+1e-9>=length:matches.append(other['id']);lengths[other['id']]=shared
        result.update(value=len(matches),unit='rooms',ids=matches,trace={'boundary_near_length_m':lengths,'tolerance_m':tolerance,'minimum_length_m':length})
        result['assumptions'].append('Benchmark boundary-nearness model: other boundary within 0.35 m of the selected boundary; halls count, corridors/stairs/lifts/outdoor do not. This is not a surveyed wall object.')
    elif operation=='straight_line_3d':
        first=_space(building,arguments.get('first'));second=_space(building,arguments.get('second'))
        result['assumptions']+=_alignment(building,[first,second],arguments)
        a=_polygon(first).centroid;b=_polygon(second).centroid
        z1=_floor(building,first['floor_id']).get('elevation_m');z2=_floor(building,second['floor_id']).get('elevation_m')
        if any(isinstance(z,bool) or not isinstance(z,(int,float)) or not math.isfinite(z) for z in (z1,z2)):
            raise ModelError('geometry_underdetermined','Finite floor elevation_m required')
        result.update(value=math.dist((a.x,a.y,z1),(b.x,b.y,z2)),unit='m',ids=[first['id'],second['id']],trace={'first_centroid':[a.x,a.y,z1],'second_centroid':[b.x,b.y,z2]})
    else:  # what_if_door
        first=_space(building,arguments.get('first'));second=_space(building,arguments.get('second'))
        if first['id']==second['id'] or first['floor_id']!=second['floor_id']:raise ModelError('invalid_input','Choose two different rooms on the same floor')
        floor_id=arguments.get('floor_id');_floor(building,floor_id);threshold=_number(arguments,'improvement_m',1.0)
        polygon=_polygon(first);other=_polygon(second)
        touching=polygon.boundary.intersection(other.boundary)
        if touching.geom_type in {'Point','MultiPoint'} and polygon.intersection(other).area<=1e-9:
            raise ModelError('geometry_underdetermined','Point contact does not define a shared wall')
        pieces=_lines(polygon.boundary.intersection(other.boundary.buffer(.35)))
        if not pieces: raise ModelError('geometry_underdetermined','No shared-wall segment within benchmark tolerance')
        wall=max(pieces,key=lambda line:line.length)
        if wall.length<=.7+1e-9: raise ModelError('geometry_underdetermined','Boundary-near segment is too short to distinguish it from tolerance end caps')
        point=wall.interpolate(.5,normalized=True)
        edited=copy.deepcopy(building)
        opening_ids={o['id'] for o in edited['openings']};door_id='prototype-what-if-door'
        while door_id in opening_ids:door_id+='-new'
        door={'id':door_id,'floor_id':first['floor_id'],'space_ids':[first['id'],second['id']],'kind':'door','position':[point.x,point.y],'width_m':.9,'step_free':True,'synthetic':True}
        edited['openings'].append(door)
        before_graph=_graph(building,scenario_closed=scenario_closed);after_graph=_graph(edited,scenario_closed=scenario_closed)
        if 'outside' not in before_graph:raise ModelError('geometry_underdetermined','No entrance/outside graph node available')
        before=_lengths(before_graph,'outside');after=_lengths(after_graph,'outside')
        changed=[];effects=[]
        for room in building['spaces']:
            if room['floor_id']!=floor_id or room['type']!='room':continue
            old=before.get(room['id']);new=after.get(room['id'])
            improvement=old-new if old is not None and new is not None else None
            if new is not None and (old is None or improvement>threshold+1e-9):
                changed.append(room['id']);effects.append({'space_id':room['id'],'before_m':old,'after_m':new,'improvement_m':improvement})
        result.update(value=len(changed),unit='rooms',ids=changed,trace={'door':door,'shared_wall_length_m':wall.length,'improvement_threshold_m':threshold,'effects':effects})
        result['assumptions'].append('New reference door at midpoint of longest boundary-near shared segment (0.35 m); no original edit. Nearest-entrance distance uses the official zero-cost outside node; not a physical outside path.')
    return result


def format_answer(operation,result):
    if operation!=result.get('operation'):raise ModelError('invalid_input','Result/operation mismatch')
    if operation in {'largest_room','room_stacked'}:
        # OSM names can be duplicated (e.g. several rooms named "Büro").
        label=result.get('space_ref') or result.get('space_name') or result['space_id']
        return f"Answer: {label}, {result['value']:.8g} m2" if operation=='largest_room' else f'Answer: {label}'
    value=result['value']
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ModelError('invalid_input','Answer must be finite numeric')
    return f'Answer: {value:.10g}'


def write_csv(rows):
    """Return UTF-8 CSV bytes from iterable (question_id, full_reply) pairs."""
    buffer=io.StringIO(newline='');writer=csv.writer(buffer);writer.writerow(['question_id','answer']);writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')
