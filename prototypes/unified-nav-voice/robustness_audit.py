"""Bounded fixed-seed audit; original ZIP input, independent segment predicates.

No router geometry helpers are used by the validation oracle. Taxonomy is copied
as the declared A/B product contract, not inferred stationary truth. Reachability
and validity are reported separately. Run --label baseline or --label corrected.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import time

from shapely.geometry import Polygon, Point, LineString, shape
from shapely.ops import unary_union
from data import load_building, source_hash
from errors import ModelError
from service import Service

SEED = 24102026
TOL = 2e-6
A_TYPES = set('cnc_mill drill_press conveyor industrial_3d_printer 3d_printer curing_oven injection_moulding_machine waterjet_cutter compressor coordinate_measuring_machine pallet_rack server_rack bookshelf'.split())
B_TYPES = A_TYPES | set('desk reception_desk workbench assembly_station inspection_bench packing_station bench chair office_chair meeting_table dining_table filing_cabinet tool_cabinet locker shoe_cabinet coat_rack plant kitchen_counter sink stove fridge dishwasher toilet vending_machine welding_station forklift pallet_jack forklift_charging_station ups_unit'.split())


def polygon(record):
    raw = record['polygon']
    if isinstance(raw, dict):
        return shape(raw) if 'type' in raw else Polygon(raw['exterior'], raw.get('holes', []))
    if isinstance(raw[0][0], (list, tuple)):
        return Polygon(raw[0], raw[1:])
    return Polygon(raw, record.get('holes', []))


def footprint(asset):
    """Direct world-coordinate corner calculation, separate from router helpers."""
    f, (x, y) = asset['footprint'], asset['position']
    w, d = f['width_m']/2, f['depth_m']/2
    theta = math.radians(f.get('rotation_deg', 0))
    c, s = math.cos(theta), math.sin(theta)
    return Polygon([(x+u*c-v*s, y+u*s+v*c) for u,v in [(-w,-d),(w,-d),(w,d),(-w,d)]])


def obstacles(building, request):
    kinds = A_TYPES if request['profile']=='stationary' else B_TYPES
    forced = {spec.get('id') for spec in (request.get('start',{}),request.get('target',{})) if spec.get('kind')=='asset'}
    return [(a, footprint(a)) for a in building['assets'] if a['type'] in kinds or a['id'] in forced]


def independent_free(building, space, request):
    c = request['width_m']/2 + request['safety_m']
    room = polygon(space)
    polys = [p for a,p in obstacles(building, request) if a['floor_id']==space['floor_id']]
    return room.buffer(-(c+1e-5),join_style=2).difference(unary_union(polys).buffer(c+1e-5,join_style=2))


def components(raw):
    if raw.is_empty:return []
    if raw.geom_type=='Polygon':return [raw]
    if hasattr(raw, 'geoms'):return [p for g in raw.geoms for p in components(g)]
    return []


def point_pair(building, space, request, rng):
    parts=sorted(components(independent_free(building,space,request)),key=lambda p:-p.area)
    if not parts:return None
    region=parts[0];bounds=region.bounds;points=[]
    for _ in range(80):
        p=(rng.uniform(bounds[0],bounds[2]),rng.uniform(bounds[1],bounds[3]))
        if region.contains(Point(p)):points.append(p)
        if len(points)==2:return points
    return [tuple(region.representative_point().coords[0])]*2


def walls_except_aperture(room, opening):
    centre=Point(opening['position']);width=opening['width_m']
    walls=[];matched=0
    for part in components(room):
        for ring in [part.exterior,*part.interiors]:
            coords=list(ring.coords)
            for a,b in zip(coords,coords[1:]):
                edge=LineString([a,b])
                if edge.distance(centre)>1e-6:
                    walls.append(edge);continue
                matched+=1;t=edge.project(centre)
                if t-width/2 < -1e-6 or t+width/2 > edge.length+1e-6:
                    raise AssertionError('successful portal exceeds supporting wall segment')
                low=max(0,t-width/2);high=min(edge.length,t+width/2)
                if low>1e-8:walls.append(LineString([a,edge.interpolate(low)]))
                if high<edge.length-1e-8:walls.append(LineString([edge.interpolate(high),b]))
    if matched!=1:raise AssertionError('successful portal has ambiguous boundary')
    return unary_union(walls)


def independent_aperture(building, opening):
    """Derive the new declared open-zone aperture from original polygons only.

    No trust in router aperture metadata and no clipping of physical doors.
    """
    info=building['building'];sides=opening.get('space_ids',[])
    if (info.get('source')!='procedural' or info.get('source_ref',{}).get('generator')!='factory'
            or opening.get('kind')!='opening' or opening.get('synthetic') or len(sides)!=2):return opening
    spaces={s['id']:s for s in building['spaces']}
    if any(sid not in spaces or spaces[sid].get('type') not in {'hall','corridor'} for sid in sides):return opening
    a,b=[spaces[sid] for sid in sides]
    if a['floor_id']!=b['floor_id']:return opening
    first,second=polygon(a),polygon(b);shared=first.boundary.intersection(second.boundary)
    if first.intersection(second).area>1e-7 or shared.geom_type!='LineString' or shared.length<=1e-7:return opening
    if (Point(opening['position']).distance(shared.interpolate(.5,normalized=True))>1e-6
            or opening['width_m']<shared.length-1e-6
            or abs(math.dist(shared.coords[0],shared.coords[-1])-shared.length)>1e-6):return opening
    for domain in (first,second):
        edges=[LineString([p,q]) for ring in [domain.exterior,*domain.interiors] for p,q in zip(ring.coords,list(ring.coords)[1:])]
        if not any(edge.hausdorff_distance(shared)<=1e-6 for edge in edges):return opening
    return {**opening,'width_m':min(opening['width_m'],shared.length),'audit_shared':shared}


def independent_check(building,request,result):
    """All path segments against JSON domains, footprints and aperture walls."""
    spaces={s['id']:s for s in building['spaces']}
    doors={o['id']:o for o in building['openings']}
    obs=obstacles(building,request)
    c=request['width_m']/2+request['safety_m'];length=0;previous=None
    count=0;minimum_obstacle=None;minimum_wall=None
    for seg in result['segments']:
        sid=seg['space_id'];record=spaces[sid];room=polygon(record);points=seg['points']
        line=LineString(points) if len(points)>1 else Point(points[0])
        if not room.buffer(1e-8).covers(line):raise AssertionError('segment leaves original room')
        walls=room.boundary
        if seg['kind']=='validated_door_approach':
            original=doors[seg['opening_id']]
            door=independent_aperture(building,original)
            if 'audit_shared' in door:
                aperture=seg.get('aperture')
                if (not aperture or abs(aperture['effective_width_m']-door['width_m'])>TOL
                        or abs(aperture['nominal_width_m']-original['width_m'])>TOL
                        or LineString(aperture['segment']).hausdorff_distance(door['audit_shared'])>TOL):
                    raise AssertionError('open-zone aperture metadata disagrees with independently derived shared edge')
            if door['width_m']+TOL < 2*c:raise AssertionError('door too narrow')
            if sid not in door['space_ids']:raise AssertionError('wrong door room side')
            if door['id'] in request.get('closed',[]):raise AssertionError('closed door used')
            # The crossing must lie on this room's boundary inside the opening interval, leaving the full
            # clearance to both interval ends (off-centre crossings of wide openings are allowed).
            crossing=min(points,key=lambda p:room.boundary.distance(Point(p)))
            if room.boundary.distance(Point(crossing))>TOL:raise AssertionError('door stub does not reach the wall')
            if math.dist(crossing,door['position'])>door['width_m']/2-c+TOL:raise AssertionError('door crossing outside the usable opening interval')
            walls=walls_except_aperture(room,door)
        wall_distance=line.distance(walls)
        if wall_distance < c-TOL:raise AssertionError(f'wall clearance {wall_distance} < {c}')
        minimum_wall=wall_distance if minimum_wall is None else min(minimum_wall,wall_distance)
        for asset,poly in obs:
            if asset['floor_id']!=record['floor_id']:continue
            distance=line.distance(poly)
            if distance < c-TOL:raise AssertionError(f"obstacle clearance {asset['id']} {distance} < {c}")
            minimum_obstacle=distance if minimum_obstacle is None else min(minimum_obstacle,distance)
        if sid in request.get('closed',[]):raise AssertionError('closed space used')
        if previous and previous['floor_id']==seg['floor_id'] and math.dist(previous['points'][-1],points[0])>TOL:
            raise AssertionError('same-floor segments discontinuous')
        previous=seg
        measured=sum(math.dist(a,b) for a,b in zip(points,points[1:]));length+=measured;count+=1
        if abs(measured-seg['length_m'])>TOL:raise AssertionError('segment length mismatch')
    if abs(length-result['length_m'])>TOL:raise AssertionError('total length mismatch')
    if result['segments']:
        if math.dist(result['segments'][0]['points'][0],result['start']['positions'][0])>TOL:raise AssertionError('wrong start')
        if math.dist(result['segments'][-1]['points'][-1],result['target']['selected_position'])>TOL:raise AssertionError('wrong goal')
    if request['target']['kind']=='asset':
        target=next(a for a in building['assets'] if a['id']==request['target']['id'])
        if Point(result['target']['selected_position']).distance(footprint(target))<c-TOL:raise AssertionError('target access in footprint')
    return {'segments_checked':count,'minimum_wall_m':minimum_wall,'minimum_obstacle_m':minimum_obstacle,'length_m':length}


def select_buildings(service):
    catalog=service.catalog()
    factory=sorted([r for r in catalog if r['id'].startswith('proc-factory')],key=lambda r:r['id'])
    chosen=factory+sorted([r for r in catalog if r['id'].startswith('proc-campus')],key=lambda r:r['id'])[:2]
    for source in ['osm_indoor','resplan','swiss_dwellings']:
        chosen+=sorted([r for r in catalog if r['source']==source],key=lambda r:r['id'])[:2]
    return chosen


def make_cases(building,rng):
    rooms=building['spaces'];spaces={s['id']:s for s in rooms}
    # Stable random selection avoids success-only room choice. Every local pair
    # is independently sampled from one known free component, when one exists.
    sample=rng.sample(rooms,min(4,len(rooms)))
    settings=[('stationary',.6,.05),('all',.6,.05),('all',.6,.3),('all',1.2,.5)]
    cases=[]
    for idx,(profile,width,safety) in enumerate(settings):
        base={'model':'precise','profile':profile,'width_m':width,'safety_m':safety,'max_seconds':8}
        room=sample[idx%len(sample)];pair=point_pair(building,room,base,rng)
        if pair:
            start={'kind':'point','space_id':room['id'],'position':pair[0]};target={'kind':'point','space_id':room['id'],'position':pair[1]}
        else:start=target={'kind':'space','id':room['id']}
        cases.append(('local',f'local-{idx}',{**base,'start':start,'target':target},bool(pair)))
    edges=[o for o in building['openings'] if len(o.get('space_ids',[]))==2 and all(s in spaces for s in o['space_ids'])]
    for idx,opening in enumerate(rng.sample(edges,min(4,len(edges)))):
        profile,width,safety=settings[idx];base={'model':'precise','profile':profile,'width_m':width,'safety_m':safety,'max_seconds':8}
        request={**base,'start':{'kind':'space','id':opening['space_ids'][0]},'target':{'kind':'space','id':opening['space_ids'][1]}}
        cases.append(('adjacent',f'adjacent-{idx}',request,False))
        if idx<2:cases.append(('closure',f'close-{idx}',{**request,'closed':[opening['id']]},False))
    assets=[a for a in building['assets'] if a.get('space_id') in spaces]
    machines=[a for a in assets if a.get('category')=='machine']
    selected=(rng.sample(machines,min(2,len(machines)))+rng.sample(assets,min(2,len(assets))))
    for idx,asset in enumerate(selected):
        profile,width,safety=settings[idx%4]
        base={'model':'precise','profile':profile,'width_m':width,'safety_m':safety,'max_seconds':8}
        cases.append(('asset',f'asset-{idx}',{**base,'start':{'kind':'space','id':asset['space_id']},'target':{'kind':'asset','id':asset['id']}},False))
    floor=rooms[0]['floor_id'];same=[s for s in rooms if s['floor_id']==floor]
    if len(same)>=2:
        first,last=rng.sample(same,2)
        for idx,(profile,width,safety) in enumerate(settings[:2]):
            cases.append(('multispace',f'multi-{idx}',{'model':'precise','profile':profile,'width_m':width,'safety_m':safety,'max_seconds':8,'start':{'kind':'space','id':first['id']},'target':{'kind':'space','id':last['id']}},False))
    return cases


def classify_error(error,building,request):
    code=error.code
    if code in {'invalid_input'}:return 'invalid_input'
    if code in {'geometry_unsupported','geometry_undetermined','computation_limit','source_drift'}:return 'unsupported'
    if code=='numerical_failure':return 'program_error'
    if code in {'blocked_endpoint','disconnected','model_blocked','blocked_by_closure','portal_blocked','door_too_narrow','blocked_by_step_free'}:
        # An API error alone is insufficient proof of building unreachability.
        # Local point cases can be certified by this independent free-set oracle.
        a,b=request['start'],request['target']
        if a['kind']==b['kind']=='point' and a['space_id']==b['space_id']:
            space=next(s for s in building['spaces'] if s['id']==a['space_id'])
            free=independent_free(building,space,request)
            if not any(p.covers(Point(a['position'])) and p.covers(Point(b['position'])) for p in components(free)):
                return 'physical_unreachable_in_model'
        return 'blocked_or_unreachable_unproven'
    return 'program_error'


def run(label):
    service=Service();rng=random.Random(SEED);rows=[];started=time.perf_counter()
    chosen=select_buildings(service)
    output=Path(__file__).parent/'robustness';output.mkdir(exist_ok=True)
    for item in chosen:
        bid=item['id'];building=load_building(bid)
        family=building['building'].get('source_ref',{}).get('generator') or item['source']
        for function,name,request,known_reachable in make_cases(building,rng):
            row={'building':bid,'family':family,'function':function,'name':name,'request':request,'known_local_reachable':known_reachable}
            began=time.perf_counter()
            try:
                result=service.navigate(bid,request)
                try:
                    row['validation']=independent_check(building,request,result)
                    row['outcome']='success';row['status']=result['status']
                except Exception as error:
                    row['outcome']='unsafe_success';row['validation_error']=repr(error)
                row['length_m']=result['length_m'];row['transitions']=[t['id'] for t in result['transitions']]
                row['unsupported_counts']=result.get('metrics',{}).get('unsupported_transition_counts',{})
            except ModelError as error:
                row['outcome']=classify_error(error,building,request);row['error']=error.as_dict()
                if known_reachable:row['expected_reachable_failure']=True
            except Exception as error:
                row['outcome']='program_error';row['error']=repr(error)
            row['elapsed_ms']=1000*(time.perf_counter()-began);rows.append(row)
        print(bid,dict(Counter(r['outcome'] for r in rows if r['building']==bid)),flush=True)
        (output/f'{label}-cases.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    summary={'seed':SEED,'label':label,'cases':len(rows),'buildings':len(chosen),'elapsed_seconds':time.perf_counter()-started,'outcomes':dict(Counter(r['outcome'] for r in rows)),'source_hashes':{r['id']:source_hash(r['id']) for r in chosen}}
    for group in ['family','function']:
        summary['by_'+group]={value:dict(Counter(r['outcome'] for r in rows if r[group]==value)) for value in sorted({r[group] for r in rows})}
    summary['known_reachable_failures']=[{'building':r['building'],'name':r['name'],'error':r.get('error')} for r in rows if r.get('expected_reachable_failure')]
    summary['source_code_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    (output/f'{label}-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


def run_reproductions(input_path,label):
    """Replay curated counterexamples through the same independent validator."""
    service=Service();rows=[]
    for case in json.loads(Path(input_path).read_text(encoding='utf-8-sig')):
        req=case['request'];building=load_building(case['building'])
        row={'building':case['building'],'asset':case['asset'],'request':req,
             'previous_result':case.get('actual_result'),'witness_reachable':case.get('witness_reachable')}
        try:
            result=service.navigate(case['building'],req)
            row['independent_check']=independent_check(building,req,result)
            row['outcome']='success'
        except ModelError as error:
            row['outcome']=classify_error(error,building,req);row['error']=error.as_dict()
        except Exception as error:
            row['outcome']='unsafe_success_or_program_error';row['error']=repr(error)
        rows.append(row)
    output=Path(__file__).parent/'robustness';output.mkdir(exist_ok=True)
    (output/f'{label}-reproductions.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(rows),'outcomes':dict(Counter(r['outcome'] for r in rows)),
                      'reachable_cases':dict(Counter(r['outcome'] for r in rows if r['witness_reachable']))},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='baseline')
    parser.add_argument('--reproductions')
    args=parser.parse_args()
    if args.reproductions:run_reproductions(args.reproductions,args.label)
    else:run(args.label)
