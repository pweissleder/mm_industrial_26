"""Single process-local scenario store. Benchmark and physical contracts stay separate."""
import copy
import csv
import networkx as nx
from io import StringIO
from threading import RLock
from time import perf_counter
from data import load_building, reference_map, read_member, building_index, source_hash, REFERENCE_COMMIT
from errors import ModelError
from functools import lru_cache


@lru_cache(maxsize=2)
def _examples(split):
    if split not in {'train', 'dev'}:
        raise ModelError('invalid_input', 'Only Train/Dev examples are available')
    from data import examples
    return tuple(examples(split))

class Service:
    def __init__(self):
        self.lock=RLock();self.scenarios={};self.revision=0

    def model(self,building_id):
        if building_id not in self.scenarios:self.scenarios[building_id]=reference_map(load_building(building_id))
        return self.scenarios[building_id]

    def catalog(self,include_train=True):
        # Train buildings are listed too so explicitly chosen Train examples can be loaded (never Test).
        splits={'dev','train'} if include_train else {'dev'}
        rows=csv.DictReader(StringIO(read_member('data/manifest.csv').decode('utf-8-sig')))
        return [{'id':r['building_id'],'name':r['name'],'source':r['source'],'split':r['split'],'floors':int(r['floors'])}
                for r in rows if r['split'] in splits and r['building_id'] in building_index()]

    def building(self,building_id):
        model=self.model(building_id)
        return {'data':copy.deepcopy(model.data),'closed':sorted(model.closed),'revision':self.revision,
                'reference_anchors':{sid:model.anchor(sid) for sid in model.spaces},
                'source':{'commit':REFERENCE_COMMIT,'sha256':source_hash(building_id),'original_read_only':True,
                          'footprints':'center/CCW verified per supported procedural building against original SVG',
                          'convention_scope':'Other source families are available to benchmark tools; precise routing requires validation'}}

    def change(self,building_id,arguments):
        action=arguments.get('action')
        if action=='reset':self.scenarios.clear();self.revision+=1;return self.building(building_id)
        model=copy.deepcopy(self.model(building_id))
        if action in {'close','open'}:
            element=arguments.get('element')
            if not isinstance(element,str) or element not in model.spaces|model.openings|model.connectors:
                raise ModelError('invalid_input','Choose a room, opening or connector')
            model.set_closed(element,action=='close')
        elif action=='add_door':
            first,second=arguments.get('first'),arguments.get('second')
            if first==second or first not in model.spaces or second not in model.spaces:
                raise ModelError('invalid_input','Select two different rooms')
            try:model.add_door(first,second)
            except ValueError as error:raise ModelError('invalid_input',str(error)) from error
        else:raise ModelError('invalid_input','Unknown scenario action')
        self.scenarios[building_id]=model;self.revision+=1
        return self.building(building_id)

    def reference_route(self,building_id,arguments):
        """'reference' = the official kit 1.1.0 room graph drawn as straight lines between its nodes
        (space anchors and door positions); the cost is the official rule-based route length."""
        result=self.challenge_route(building_id,{k:v for k,v in arguments.items() if k in {'start','target','via','step_free','closed'}})
        if not result['reachable']:
            raise ModelError('blocked',result.get('unreachable_reason') or 'No official route',{'status':result['status']})
        nodes=result['nodes'];segments=[]
        for a,b in zip(nodes,nodes[1:]):
            if a['floor_id']==b['floor_id']:segments.append({'floor_id':a['floor_id'],'points':[a['position'],b['position']]})
        return {'model':'reference','benchmark_cost_m':result['cost_m'],'segments':segments,'nodes':nodes,
                'space_path':result['space_path'],'element_path':result['element_path'],'steps':result['directions'],
                'directions':result['directions'],'elapsed_ms':result.get('elapsed_ms'),
                'status':'abstract_reference','collision_checked':False,'kit':result['kit'],
                'assumptions':['Official kit 1.1.0 route model (rule-based cost); nodes are space anchors and door positions',
                               'Straight node-to-node lines; the official walk geometry is in challenge mode',
                               'Input XY is resolved to its room, not used as a physical endpoint']}

    def challenge_route(self,building_id,arguments):
        from challenge import official_route
        closed=arguments.get('closed',[])
        if not isinstance(closed,list) or not all(isinstance(value,str) for value in closed):
            raise ModelError('invalid_input','closed must be a list of element IDs')
        model=self.model(building_id)
        request=copy.deepcopy(arguments);request.pop('model',None);request['closed']=sorted(set(model.closed)|set(closed))
        began=perf_counter()
        result=official_route(model,request)
        result['elapsed_ms']=(perf_counter()-began)*1000
        result['revision']=self.revision
        return result

    def navigate(self,building_id,arguments):
        if arguments.get('model')=='reference':return self.reference_route(building_id,arguments)
        if arguments.get('model')=='challenge':return self.challenge_route(building_id,arguments)
        try:
            return self._navigate(building_id,arguments)
        except ModelError as error:
            from diagnostics import annotate_error
            try:
                model=self.model(building_id)
            except ModelError:
                raise error
            explained=copy.deepcopy(arguments)
            closed=explained.get('closed',[])
            if isinstance(closed,list) and all(isinstance(value,str) for value in closed):
                explained['closed']=sorted(set(model.closed)|set(closed))
            try:
                diagnosed=annotate_error(model.data,explained,error)
            except Exception:
                error.details['diagnosis']={'category':'unresolved','summary':'The original rejection stands; a reliable diagnosis could not be produced.',
                                             'elements':[],'diagnostic_failure':True}
                raise error
            raise diagnosed

    def _navigate(self,building_id,arguments):
        if arguments.get('model')=='reference':return self.reference_route(building_id,arguments)
        from navigation import navigate
        from validation import validate_footprints
        proof=validate_footprints(building_id)
        if not proof.get('verified'):raise ModelError('geometry_undetermined','Footprint convention not verified for this building',proof)
        closed=arguments.get('closed',[])
        if not isinstance(closed,list) or not all(isinstance(value,str) for value in closed):
            raise ModelError('invalid_input','closed must be a list of element IDs')
        model=self.model(building_id)
        request=copy.deepcopy(arguments);request['closed']=sorted(set(model.closed)|set(closed))
        result=navigate(copy.deepcopy(model.data),request)
        result['revision']=self.revision;result['source_validation']=proof
        # Compare contracts explicitly; this number never labels the physical polyline.
        try:result['reference_comparison']=self.reference_route(building_id,arguments)
        except (ModelError,ValueError,KeyError,TypeError,nx.NetworkXException):result['reference_comparison']={'status':'not_comparable','assumptions':['No comparable reference room route for these endpoints; validated physical result is retained']}
        a,b=arguments.get('start',{}),arguments.get('target',{})
        if a.get('kind')=='door' and b.get('kind')=='door' and a.get('space_id')==b.get('space_id'):
            if a.get('id') in model.openings and b.get('id') in model.openings and a.get('space_id') in model.spaces:
                import math
                from shapely.geometry import LineString,shape
                original_points=[model.openings[a['id']]['position'],model.anchor(a['space_id']),model.openings[b['id']]['position']]
                points=[result['start']['positions'][0],model.anchor(a['space_id']),result['target']['selected_position']]
                compare_line=LineString(points)
                geometries=result.get('geometries',result.get('geometry',[]))
                local=next((g for g in geometries if g['space_id']==a['space_id']),None)
                intersections=[]
                if local:
                    for obstacle in local.get('obstacles',[]):
                        hit=compare_line.intersection(shape(obstacle['geometry']))
                        if not hit.is_empty:intersections.append({'asset_id':obstacle['id'],'intersection_length_m':hit.length})
                result['reference_comparison']={'model':'anchor_comparison','status':'invalid_reference_geometry' if intersections else 'not_clearance_validated',
                    'same_endpoints':True,'anchor_comparison_length_m':sum(math.dist(x,y) for x,y in zip(points,points[1:])),
                    'original_door_center_anchor_length_m':sum(math.dist(x,y) for x,y in zip(original_points,original_points[1:])),
                    'obstacle_intersections':intersections,
                    'segments':[{'floor_id':model.spaces[a['space_id']]['floor_id'],'space_id':a['space_id'],'points':points}],
                    'assumptions':['Comparison via official anchor with IDENTICAL physical inner endpoints; not an official benchmark answer',
                                   'The separate raw-door-centre cost has different endpoints and does not label this polyline']}
        return result

    def analyze(self,building_id,arguments):
        from geometry import analyze_space
        from validation import validate_footprints
        proof=validate_footprints(building_id)
        if not proof.get('verified'):raise ModelError('geometry_undetermined','Unverified footprint convention',proof)
        return analyze_space(copy.deepcopy(self.model(building_id).data),arguments.get('space_id'),
                             arguments.get('profile','stationary'),arguments.get('width_m',.6),arguments.get('safety_m',.05))

    def operation(self,building_id,arguments):
        from operations import compute,format_answer
        from challenge import prime_map_cache
        model=self.model(building_id);prime_map_cache(model)
        inputs=copy.deepcopy(arguments.get('arguments',{}));inputs['_scenario_closed']=sorted(model.closed)
        result=compute(copy.deepcopy(model.data),arguments.get('operation'),inputs)
        result['formatted_answer']=format_answer(arguments.get('operation'),result)
        result['model']='benchmark';result['revision']=self.revision
        result['scenario_note']='RAM closures and doors apply to graph operations; physical obstacles never alter benchmark costs'
        return result

    def examples(self,split='dev'):
        """Explicit test-lab inputs and reference expectations, never Test data."""
        if not isinstance(split,str) or split not in {'train','dev'}:raise ModelError('invalid_input','Only Train/Dev examples are available')
        return copy.deepcopy(list(_examples(split)))

    def example(self,building_id,arguments):
        split=arguments.get('split','dev')
        if not isinstance(split,str) or split not in {'train','dev'}:raise ModelError('invalid_input','Only Train/Dev examples are available')
        if not isinstance(arguments.get('id'),str):
            raise ModelError('invalid_input','Choose a Train/Dev example ID')
        selected=next((q for q in _examples(split) if q['id']==arguments['id']),None)
        if selected is None or selected['building_id']!=building_id:
            raise ModelError('invalid_input','Example does not belong to the selected building/split')
        # The calculation receives only the task's declared inputs, never its answer.
        result=self.operation(building_id,{'operation':selected['kind'],'arguments':copy.deepcopy(selected['inputs'])})
        from data import reference_module
        matched=reference_module('score').score_reply(selected['answer'],result['formatted_answer'])
        result['example']={'id':selected['id'],'split':split,'building_id':building_id,
                           'question':selected['question'],'expected':copy.deepcopy(selected['answer'])}
        result['comparison']={'matches_reference':bool(matched),
            'scope':'Structured Train/Dev comparison with the kit 1.1.0 answers (official scorer); live RAM scenario applies; no language benchmark'}
        return result
