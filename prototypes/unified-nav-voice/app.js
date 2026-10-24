"use strict";
const $=id=>document.getElementById(id), svg=$('map'), iso=$('iso'), NS='http://www.w3.org/2000/svg';
let data,payload,specs=[],result,analysis,selected,view,fitBox,loading=false,generation=0,examples=[],exampleGen=0,cardHits=[],closedSet=new Set(),drag=null,dragMoved=false;
const opInputs=new Map(),choices=new Map(),diag=new Map(),marks=new Set();
const DEMO_BUILDING='proc-factory-dev-003',F=suffix=>DEMO_BUILDING+'-'+suffix;
const CAT_LABEL={closed:'Closed',too_narrow:'Too narrow',unsupported:'Not supported',unreachable_in_model:'Not reachable in model',unresolved:'Unresolved'};
const KIND_LABEL={space:'Room',asset:'Asset',opening:'Door',connector:'Lift / stairs'};
const KINDS={space:()=>data.spaces,opening:()=>data.openings,asset:()=>data.assets,connector:()=>data.vertical_connectors};
const EXPLORE={rooms:'space',doors:'opening',assets:'asset',connectors:'connector'};
const DEMOS={
 direct:{profile:'stationary',floor:'F0',start:{kind:'door',space:'F0-S019',id:'F0-O0023'},target:{kind:'door',space:'F0-S019',id:'F0-O0024'}},
 desk:{profile:'all',floor:'F0',start:{kind:'door',space:'F0-S019',id:'F0-O0023'},target:{kind:'door',space:'F0-S019',id:'F0-O0024'}},
 cnc:{profile:'stationary',floor:'F0',start:{kind:'point',space:'F0-S001',xy:[-12,2]},target:{kind:'asset',id:'A00001'}},
 rooms:{profile:'stationary',floor:'F0',start:{kind:'space',space:'F0-S020'},target:{kind:'space',space:'F0-S021'}},
 floors:{profile:'stationary',floor:'F0',schematic:true,stepFree:true,start:{kind:'space',space:'F0-S020'},target:{kind:'space',space:'F1-S004'}}
};
function option(value,label){const n=document.createElement('option');n.value=value;n.textContent=label;return n;}
function shape(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function showError(error){const d=error?.details?.diagnosis;$('error').hidden=!error;$('error').textContent=error?(d?.summary||d?.summary_de||`${error.code||'error'} · ${error.message||error}`):'';}
async function request(path,args){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),65000);
 try{const response=await fetch(path,args===undefined?{signal:controller.signal}:{method:'POST',headers:{'Content-Type':'application/json','X-Prototype-Action':'local'},body:JSON.stringify({building_id:data.building.id,arguments:args}),signal:controller.signal});const value=await response.json();if(!response.ok)throw value.error||new Error(`HTTP ${response.status}`);return value;}finally{clearTimeout(timer);}
}
async function busy(task){
 if(loading)return;loading=true;document.body.classList.add('busy');const controls=Array.from(document.querySelectorAll('button,select,input')).map(n=>[n,n.disabled]);controls.forEach(([n])=>n.disabled=true);showError(null);
 try{await task();}catch(e){showError(e);$('result-summary').textContent='No valid new route · '+(e?.code||'error');$('model-note').textContent='No new route calculated; previous route and clearance values are discarded.';$('result').textContent=JSON.stringify(e instanceof Error?{code:'client_error',message:e.message}:e,null,2);result=null;analysis=null;marks.clear();clearChips();setDiagnosis(e);draw();}finally{loading=false;document.body.classList.remove('busy');controls.forEach(([n,disabled])=>n.disabled=disabled);}
}
const floorName=id=>data.floors.find(f=>f.id===id)?.level??'?';
const spaceLabel=s=>`L${floorName(s.floor_id)} · ${s.name||s.ref||s.id}`;
const short=s=>{s=String(s||'');return s.length>80?s.slice(0,77)+'…':s;};
const fmt=v=>v===undefined||v===null?'–':Number(v).toFixed(3)+' m';
const shown=v=>v===undefined||v===null?'see result':typeof v==='object'?JSON.stringify(v):String(v);
function floor(){return data?.floors.find(f=>f.id===$('floor').value);}
function chip(text,cls){$('chips').append(el('span',text,'chip '+(cls||'')));}
function clearChips(){for(const id of ['chips','facts','warnings'])$(id).replaceChildren();$('facts').hidden=$('warnings').hidden=true;}
function clearCalculation(message='Inputs changed · recalculate route.'){
 result=null;analysis=null;clearChips();diag.clear();marks.clear();$('diagnosis').hidden=true;
 $('result-summary').textContent=message;$('result').textContent='No current calculation for these inputs.';$('model-note').textContent='Previous route and clearance values are discarded. Blue appears only after a new calculation.';
}
function invalidateNavigation(){clearCalculation();showError(null);draw();}
function selectInfo(record,reveal=true){selected=record;$('selected').textContent=JSON.stringify(record,null,2);if(reveal)$('selection-details').open=true;draw();}
function diagnose(e){
 const d=e?.details?.diagnosis;
 if(d&&typeof d==='object')return {category:Object.hasOwn(CAT_LABEL,d.category)?d.category:'unresolved',summary:String(d.summary||d.summary_de||'No summary provided.'),elements:Array.isArray(d.elements)?d.elements:[],clearance:d.clearance_m};
 const code=String(e?.code||'').toLowerCase();
 const table=[['closed','closed','The code suggests a closure; affected elements are not named.'],['narrow','too_narrow','The code suggests insufficient width; affected elements are not named.'],['clearance','too_narrow','The code suggests insufficient clearance; affected elements are not named.'],['not_supported','unsupported','The request is not supported by the model.'],['unsupported','unsupported','The request is not supported by the model.'],['unreachable','unresolved','No route reported; unreachability is not established.'],['no_route','unresolved','No route reported; unreachability is not established.'],['no_path','unresolved','No route reported; unreachability is not established.']];
 const hit=table.find(([k])=>code.includes(k));
 return {category:hit?hit[1]:'unresolved',summary:hit?hit[2]:`Cause unclear (${code||'no code'}); check the raw details.`,elements:[],clearance:undefined};
}
function setDiagnosis(e){
 diag.clear();const d=diagnose(e);
 for(const x of d.elements)if(x&&x.id!==undefined&&x.id!==null)diag.set(String(x.id),{reason:String(x.reason||x.reason_de||''),category:String(x.category||d.category)});
 $('diagnosis').hidden=false;
 const c=Number(d.clearance);
 $('diag-summary').textContent=`${CAT_LABEL[d.category]}: ${d.summary}${d.clearance!==undefined&&d.clearance!==null&&Number.isFinite(c)?' · required centre clearance c = '+c.toFixed(3)+' m':''}`;
 $('diag-list').replaceChildren(...Array.from(diag,([id,v])=>{const li=el('li'),found=findRecord(id),f=found?recordFloor(found.record):null,b=el('button',`${id}${f&&data?' · L'+floorName(f):''} · ${v.reason||CAT_LABEL[v.category]||v.category}`,'link');b.addEventListener('click',()=>{if(found)focusRecord(found.kind,found.record);});li.append(b);return li;}));
}
function recordLabel(kind,r){
 if(kind==='space')return spaceLabel(r);
 if(kind==='asset')return `L${floorName(r.floor_id)} · ${r.name} (${r.type})`;
 if(kind==='opening')return `L${floorName(r.floor_id)} · ${r.kind} ${String(r.id).split('-').at(-1)} · ${r.width_m} m`;
 return r.name||r.id;
}
function recordFloor(r){return r.floor_id||(r.floor_ids?.includes($('floor').value)?$('floor').value:r.floor_ids?.[0])||r.stops?.[0]?.floor_id;}
function findRecord(id){for(const kind of Object.keys(KINDS)){const r=KINDS[kind]().find(x=>x.id===id);if(r)return {kind,record:r};}return null;}
function connectorPoints(c){
 const out=[];for(const s of c.stops||[])if(s&&s.floor_id&&Array.isArray(s.position))out.push(s);
 if(!out.length&&c.floor_id&&Array.isArray(c.position))out.push({floor_id:c.floor_id,position:c.position});
 if(!out.length)for(let i=0;i<(c.space_ids||[]).length;i++){
  const room=data.spaces.find(s=>s.id===c.space_ids[i]),position=payload?.reference_anchors?.[c.space_ids[i]];
  if(room&&Array.isArray(position)&&position.length===2)out.push({floor_id:room.floor_id,position,space_id:room.id,schematic_marker:true});
 }
 return out;
}
function connectorRoom(c){const ids=(c.stops||[]).map(s=>s.space_id).concat(c.space_ids||[]).filter(id=>data.spaces.some(s=>s.id===id));return ids.find(id=>data.spaces.find(s=>s.id===id).floor_id===floor().id)||ids[0];}
function recordPoints(kind,r){
 if(kind==='space')return allPoints(r.polygon);
 if(kind==='asset'&&r.position)return [...(footprint(r)||[]),[r.position[0]-4,r.position[1]-4],[r.position[0]+4,r.position[1]+4]];
 if(kind==='opening'&&r.position)return [[r.position[0]-4,r.position[1]-4],[r.position[0]+4,r.position[1]+4]];
 if(kind==='connector')return connectorPoints(r).filter(p=>p.floor_id===recordFloor(r)).flatMap(p=>[[p.position[0]-4,p.position[1]-4],[p.position[0]+4,p.position[1]+4]]);
 return [];
}
function focusRecord(kind,r){
 const f=recordFloor(r);if(f&&data.floors.some(x=>x.id===f)){$('floor').value=f;setOriginal();}
 const pts=recordPoints(kind,r);if(pts.length)fitPoints(pts);else fitFloor();
}
function exploreList(){
 if(!data)return;const kind=EXPLORE[$('explore-type').value],q=$('explore-search').value.trim().toLowerCase();
 const rows=KINDS[kind]().map(r=>[r.id,recordLabel(kind,r)]).filter(([id,t])=>!q||t.toLowerCase().includes(q)||String(id).toLowerCase().includes(q));
 $('explore-list').replaceChildren(...rows.slice(0,400).map(([id,t])=>option(id,t)));
 $('explore-count').textContent=`${rows.length} hits${rows.length>400?' · first 400 shown':''}`;
}
function exploreSelect(){
 const kind=EXPLORE[$('explore-type').value],r=KINDS[kind]().find(x=>x.id===$('explore-list').value);
 if(!r)return;focusRecord(kind,r);showCard([{kind,record:r}]);selectInfo(r);
}
function setChoices(id,items){choices.set(id,items);renderChoices(id);}
function renderChoices(id,query=''){
 const s=$(id),old=s.value,q=query.trim().toLowerCase();
 const rows=(choices.get(id)||[]).filter(([v,t])=>v===old||!q||t.toLowerCase().includes(q));
 s.replaceChildren(...rows.map(([v,t])=>option(v,t)));if(rows.some(([v])=>v===old))s.value=old;
}
function applyFilter(prefix){for(const k of ['space','asset'])renderChoices(prefix+'-'+k,$(prefix+'-search').value);}
function setSelect(id,value){
 const s=$(id);
 if(!Array.from(s.options).some(o=>o.value===value)){const prefix=id.split('-')[0];$(prefix+'-search').value='';applyFilter(prefix);}
 s.value=value;
}
function roomsFor(select,spaces){$(select).replaceChildren(...spaces.map(s=>option(s.id,spaceLabel(s))));}
function fillDoors(prefix){
 const id=$(prefix+'-space').value,old=$(prefix+'-door').value;
 const doors=data.openings.filter(o=>o.space_ids.includes(id));
 $(prefix+'-door').replaceChildren(...doors.map(o=>option(o.id,`${o.kind} · ${o.id.split('-').at(-1)} · ${o.width_m} m`)));
 if(doors.some(d=>d.id===old))$(prefix+'-door').value=old;
}
function endpointVisibility(){
 for(const p of ['start','target']){const k=$(p+'-kind').value;$(p+'-xy').hidden=k!=='point';$(p+'-door-wrap').hidden=k!=='door';$(p+'-asset-wrap').hidden=k!=='asset';$(p+'-space-wrap').hidden=k==='asset';}
 if($('start-kind').value==='point'||$('target-kind').value==='point')$('advanced').open=true;
}
function modeVisibility(){
 const challenge=$('model').value==='challenge';
 for(const id of ['profile-wrap','size-wrap','schematic-wrap','precision-note','analyze'])$(id).hidden=challenge;
 $('forklift-wrap').hidden=$('challenge-note').hidden=!challenge;
}
function endpoint(prefix){
 const kind=$(prefix+'-kind').value;
 if(kind==='point')return {kind,space_id:$(prefix+'-space').value,position:[Number($(prefix+'-x').value),Number($(prefix+'-y').value)]};
 if(kind==='door')return {kind,id:$(prefix+'-door').value,space_id:$(prefix+'-space').value};
 return {kind,id:$(prefix+(kind==='asset'?'-asset':'-space')).value};
}
function parameters(){return {profile:$('profile').value,width_m:Number($('width').value),safety_m:Number($('safety').value),step_free:$('step-free').checked,allow_schematic_vertical:$('schematic').checked};}
function routeArguments(){
 const model=$('model').value,base={model,start:endpoint('start'),target:endpoint('target')};
 // Challenge rules take only step-free/forklift; forklift is never sent to the precise/reference contracts.
 return model==='challenge'?{...base,step_free:$('step-free').checked,forklift:$('forklift').checked}:{...base,...parameters()};
}
function spaceName(entry){
 if(entry&&typeof entry==='object')return entry.name||entry.ref||entry.id||JSON.stringify(entry);
 const s=data?.spaces.find(x=>x.id===entry);return s?(s.name||s.ref||s.id):String(entry);
}
function renderChallenge(value,ms){
 const reachable=value.reachable!==false,cost=Number(value.cost_m),path=Array.isArray(value.space_path)?value.space_path:[];
 const costText=value.cost_m!==undefined&&value.cost_m!==null&&Number.isFinite(cost)?`${cost.toFixed(1)} m cost`:'cost n/a';
 $('result-summary').textContent=reachable?`Challenge route · ${value.status||'ok'} · ${costText} · ${path.length} spaces${ms}`:`Challenge route · ${value.status||'unreachable'} · not reachable${ms}`;
 chip('Challenge rules','info');
 chip(`Status: ${value.status??'–'}`,reachable?'ok':'bad');
 chip(reachable?'Reachable':'Not reachable',reachable?'ok':'bad');
 if(value.forklift===true||value.request?.forklift===true)chip('Forklift · partial rules','warn');
 if(!reachable&&value.unreachable_reason)$('warnings').append(el('li',`Unreachable: ${typeof value.unreachable_reason==='string'?value.unreachable_reason:JSON.stringify(value.unreachable_reason)}`));
 if(path.length)$('facts').append(el('li',`Spaces: ${path.map(spaceName).join(' → ')}`));
 for(const t of Array.isArray(value.transitions)?value.transitions:[]){
  const c=Number(t?.cost_m),label=[t?.kind||'transition',t?.id].filter(Boolean).join(' ');
  $('facts').append(el('li',`${label}: ${t?.cost_m!==undefined&&t?.cost_m!==null&&Number.isFinite(c)?c.toFixed(2)+' m cost':'cost n/a'}`));
 }
 for(const a of Array.isArray(value.assumptions)?value.assumptions:[])$('facts').append(el('li',`Assumption: ${typeof a==='string'?a:JSON.stringify(a)}`));
 for(const w of Array.isArray(value.warnings)?value.warnings:[])$('warnings').append(el('li',typeof w==='string'?w:JSON.stringify(w)));
 $('facts').hidden=!$('facts').children.length;$('warnings').hidden=!$('warnings').children.length;
 $('model-note').textContent='Organizer challenge rules on the room graph. Cost is rule-based, not measured walking length; the drawn line follows the returned segments and is not clearance-checked.';
 draw();
}
function renderResult(value){
 result=value;analysis=null;marks.clear();clearChips();$('result').textContent=JSON.stringify(value,null,2);
 const elapsed=value.elapsed_ms??value.metrics?.elapsed_ms??value.metrics?.total_ms,ms=elapsed!==undefined?' · '+Number(elapsed).toFixed(1)+' ms':'';
 const c=Number($('width').value)/2+Number($('safety').value);
 if(value.model==='challenge'){renderChallenge(value,ms);return;}
 if(value.model==='reference'){
  $('result-summary').textContent=`Reference cost: ${Number(value.benchmark_cost_m).toFixed(3)} m · NOT collision-checked${ms}`;
  chip('Reference · unchecked','warn');chip('red line only shown when enabled','');
  $('model-note').textContent='Abstract room-anchor reference route: no obstacle, wall-clearance or door-width check. Person width and safety margin do not validate this line.';draw();return;
 }
 const length=value.length_m??value.total_length_m??value.xy_length_m;
 const transitions=value.transitions||[];
 const schematic=transitions.some(t=>t.schematic||t.kind==='stairs'||t.kind==='lift');
 $('result-summary').textContent=`Model XY route: ${length===undefined?'see details':Number(length).toFixed(3)+' m'}${ms} · ${value.status||(schematic?'with schematic floor change':'see check status')}`;
 const proof=value.validation_summary,comparison=value.reference_comparison,min=proof?.minimum_selected_obstacle_clearance_m;
 chip('Precision model','info');
 chip(value.fully_checked===true?'Segments checked in model':'not fully checked',value.fully_checked===true?'ok':'warn');
 chip(`Target clearance c = ${c.toFixed(3)} m`,'');
 if(value.clearance_m!==undefined&&value.clearance_m!==null)chip(`Output clearance_m = ${Number(value.clearance_m).toFixed(3)} m`,'');
 if(min!==undefined&&min!==null)chip(`measured obstacle clearance ≥ ${Number(min).toFixed(6)} m`,'ok');
 if($('profile').value==='stationary')chip('Profile A: excluded objects not checked','warn');
 if(schematic)chip('Floor change schematic','warn');
 for(const p of transitions){const aperture=p.aperture||p;if(aperture.effective_width_m===undefined&&aperture.nominal_width_m===undefined)continue;$('facts').append(el('li',`${p.id||p.kind||'Portal'}: usable shared opening ${fmt(aperture.effective_width_m)} · nominal ${fmt(aperture.nominal_width_m)}`));}
 for(const t of value.unsupported_transitions||[])$('warnings').append(el('li',`Not modelled: ${typeof t==='string'?t:(t.summary||t.reason||t.summary_de||t.reason_de||t.message||t.id||JSON.stringify(t))}`));
 $('facts').hidden=!$('facts').children.length;$('warnings').hidden=!$('warnings').children.length;
 let note=`c = ${c.toFixed(3)} m`;
 if(comparison?.same_endpoints)note+=` · anchor comparison, same endpoints: ${Number(comparison.anchor_comparison_length_m).toFixed(3)} m (${comparison.obstacle_intersections?.length?'crosses an obstacle':'no clearance validation'})`;
 if(value.schematic_transition_cost_m)note+=` · extra abstract floor cost: ${value.schematic_transition_cost_m} reference metres, not a geometric length`;
 $('model-note').textContent=note+' · Door choice = inner approach. Full passages are separate checked segments. Floor changes are not drawn as a line. Reference cost does not describe the blue route.';
 draw();
}
async function route(){await busy(async()=>{clearCalculation('Calculating …');draw();const value=await request('/api/navigation',routeArguments());renderResult(value);fitRoute();});}
function analysisSpace(){if($('start-kind').value==='asset'){const a=data.assets.find(x=>x.id===$('start-asset').value);if(a?.space_id)return a.space_id;}return $('start-space').value;}
async function analyze(){await busy(async()=>{clearCalculation('Calculating free area …');const id=analysisSpace();analysis=await request('/api/analyze',{space_id:id,...parameters()});$('result').textContent=JSON.stringify(analysis,null,2);$('result-summary').textContent=`Free area · c=${Number(analysis.geometry.clearance_m).toFixed(3)} m · ${analysis.geometry.components.length} components`;chip('Free area','info');chip(`c = ${Number(analysis.geometry.clearance_m).toFixed(3)} m`,'');$('model-note').textContent='Green: permitted disc centres · orange: obstacles with margin · the polygon edge is a conservative planning boundary, not a surveyed wall.';fitRoom(id);});}
function showTab(name){for(const n of ['navigate','inspect','benchmark'])$('tab-'+n).hidden=n!==name;for(const button of document.querySelectorAll('nav button'))button.classList.toggle('active',button.dataset.tab===name);}
function operationFields(){
 $('operation-fields').replaceChildren();opInputs.clear();const spec=specs.find(s=>s.name===$('operation').value);if(!spec||!data)return;
 for(const field of spec.fields){
  const label=document.createElement('label');label.textContent=field.label;
  const input=document.createElement(['number','boolean'].includes(field.type)?'input':'select');input.id='op-'+field.name;input.setAttribute('aria-label',field.label);
  if(field.type==='boolean'){input.type='checkbox';input.checked=field.default===true;label.className='check';}
  else if(field.type==='number'){input.type='number';input.step='any';input.value=field.default??0;}
  else{
   let values=[];
   if(field.type==='floor')values=data.floors.map(f=>[f.id,`${f.level} · ${f.name}`]);
   if(field.type==='space')values=data.spaces.map(s=>[s.id,spaceLabel(s)]);
   if(field.type==='connector')values=[['','No extra closure'],...data.vertical_connectors.map(c=>[c.id,c.name])];
   if(field.type==='room_type')values=[...new Set(data.spaces.map(s=>s.room_type))].sort().map(x=>[x,x]);
   if(field.type==='asset_type')values=[...new Set(data.assets.map(a=>a.type))].sort().map(x=>[x,x]);
   if(field.type==='direction')values=[['above','Directly above'],['below','Directly below']];
   input.replaceChildren(...values.map(([v,t])=>option(v,t)));if(field.default!==undefined)input.value=field.default;
   else if(field.type==='floor')input.value=$('floor').value;
  }
  label.append(input);$('operation-fields').append(label);opInputs.set(field.name,{input,field});
 }
}
function showOperation(value){
 clearChips();marks.clear();
 $('result').textContent=JSON.stringify(value,null,2);
 $('result-summary').textContent=`Reference tool ${value.operation}: ${shown(value.value)} ${value.unit||''}`;
 chip('Benchmark reference tool','info');chip('no obstacle route cost','warn');
 $('model-note').textContent='Structured reference tool; not a precise obstacle route and not a language benchmark.';
 for(const id of Array.isArray(value.ids)?value.ids:[])marks.add(String(id));
 if(marks.size)chip(`${marks.size} elements marked yellow (current map floor)`,'ok');
 draw();
}
async function compute(){await busy(async()=>{clearCalculation('Running reference tool …');draw();const inputs={};for(const[name,{input,field}]of opInputs){if(field.type==='boolean')inputs[name]=input.checked;else if(field.type==='number')inputs[name]=Number(input.value);else if(input.value||field.required)inputs[name]=input.value;}const value=await request('/api/operation',{operation:$('operation').value,arguments:inputs});showOperation(value);});}
function splitValue(){return $('example-split').value==='train'?'train':'dev';}
function currentExample(){return examples.find(e=>e.id===$('example-list').value);}
async function loadExamples(){
 const token=++exampleGen;let rows=[];
 try{rows=await request('/examples?split='+splitValue());$('example-status').textContent='';}catch(e){$('example-status').textContent='Examples unavailable: '+(e?.message||e?.code||'error');}
 if(token!==exampleGen)return;
 examples=Array.isArray(rows)?rows:[];renderExamples();
}
function renderExamples(){
 const op=$('example-op').value,q=$('example-search').value.trim().toLowerCase(),old=$('example-list').value;
 const rows=examples.filter(e=>(!op||e.kind===op)&&(!q||`${e.id} ${e.question}`.toLowerCase().includes(q)));
 $('example-list').replaceChildren(...rows.slice(0,300).map(e=>option(e.id,`${e.id} · ${e.kind} · ${short(e.question)}`)));
 if(rows.some(e=>e.id===old))$('example-list').value=old;else $('example-list').selectedIndex=-1;
 $('example-count').textContent=`${rows.length} examples (${splitValue()})${rows.length>300?' · first 300 shown':''}`;
}
function setField({input,field},value){
 if(field.type==='boolean')input.checked=value===true;
 else if(field.type==='number')input.value=String(value);
 else{const v=String(value??'');if(!Array.from(input.options).some(o=>o.value===v))input.append(option(v,v));input.value=v;}
}
async function loadExample(){
 const ex=currentExample();if(!ex)return;
 try{
  if(!data||data.building.id!==ex.building_id){
   if(!Array.from($('building').options).some(o=>o.value===ex.building_id)){showError({code:'unknown_building',message:`Building ${ex.building_id} is not in the catalog.`});return;}
   $('building').value=ex.building_id;await loadBuilding();if(!data||data.building.id!==ex.building_id)return;
  }
  if(!specs.some(s=>s.name===ex.kind)){showError({code:'unknown_operation',message:`Operation ${ex.kind} is unknown.`});return;}
  $('operation').value=ex.kind;operationFields();
  const missing=[];
  for(const[name,value]of Object.entries(ex.inputs||{})){const slot=opInputs.get(name);if(!slot){missing.push(name);continue;}setField(slot,value);}
  $('example-question').textContent=ex.question;$('example-expected').textContent=JSON.stringify(ex.answer,null,2);
  $('example-status').textContent=`Example ${ex.id} loaded · inputs applied${missing.length?' · not mappable: '+missing.join(', '):''}`;
  clearCalculation('Example loaded · “Run operation” computes the form values, “Check example” compares with the reference.');draw();showError(null);
 }catch(e){showError(e);}
}
async function checkExample(){
 const ex=currentExample();if(!ex){showError({code:'invalid_input',message:'Select an example first.'});return;}
 if(!data||data.building.id!==ex.building_id){await loadExample();if(!data||data.building.id!==ex.building_id)return;}
 await busy(async()=>{
  clearCalculation('Checking example …');draw();
  const split=splitValue(),value=await request('/api/example',{split,id:ex.id});
  showOperation(value);
  const m=value.comparison||{},expected=value.example?.expected??ex.answer;
  chip(m.matches_reference===true?'Matches reference':'Differs from reference or unknown',m.matches_reference===true?'ok':'bad');
  $('example-expected').textContent=JSON.stringify(expected,null,2);
  $('result-summary').textContent=`Example ${ex.id} (${split}): ${m.matches_reference===true?'matches the reference answer':'no match or not assessable'} · value ${shown(value.value)} ${value.unit||''}`;
  $('model-note').textContent=`Expected answer comes from the unchanged public ${split} dataset and is not a product oracle. Scope: ${m.scope||'not stated'}. Live in-memory state: ${payload.closed.length} closed (revision ${payload.revision}); this can cause differences from the reference.`;
 });
}
async function scenario(action){await busy(async()=>{const args={action,element:$('scenario-element').value,first:$('new-first').value,second:$('new-second').value};const value=await request('/api/scenario',args);applyBuilding(value,true);$('result-summary').textContent=`In-memory revision ${value.revision} · old routes discarded`;result=null;analysis=null;draw();});}
function applyBuilding(value,preserve=false){
 const previous=preserve?{floor:$('floor').value,start:$('start-space').value,target:$('target-space').value,asset:$('target-asset').value,startAsset:$('start-asset').value}:null;
 payload=value;data=value.data;cardHits=[];clearCalculation('Building / scenario loaded · recalculate route.');
 $('floor').replaceChildren(...[...data.floors].sort((a,b)=>a.level-b.level).map(f=>option(f.id,`${f.level} · ${f.name}`)));if(previous?.floor)$('floor').value=previous.floor;
 const spaces=[...data.spaces].sort((a,b)=>floorName(a.floor_id)-floorName(b.floor_id)||(a.name||a.id).localeCompare(b.name||b.id));
 $('start-search').value=$('target-search').value='';
 setChoices('start-space',spaces.map(s=>[s.id,spaceLabel(s)]));setChoices('target-space',spaces.map(s=>[s.id,spaceLabel(s)]));
 if(previous?.start)$('start-space').value=previous.start;if(previous?.target)$('target-space').value=previous.target;
 const assets=[...data.assets].sort((a,b)=>Number(b.category==='machine')-Number(a.category==='machine')||a.name.localeCompare(b.name)).map(a=>[a.id,`L${floorName(a.floor_id)} · ${a.name} (${a.type})`]);
 setChoices('start-asset',assets);setChoices('target-asset',assets);
 if(previous?.asset)$('target-asset').value=previous.asset;if(previous?.startAsset)$('start-asset').value=previous.startAsset;
 const closed=new Set(value.closed);
 $('scenario-element').replaceChildren(...[...data.spaces,...data.openings,...data.vertical_connectors].map(x=>option(x.id,`${closed.has(x.id)?'⛔ ':''}${x.name||x.kind+' '+x.id.split('-').at(-1)}`)));
 roomsFor('new-first',spaces);roomsFor('new-second',spaces);$('scenario-status').textContent=JSON.stringify({revision:value.revision,closed:value.closed},null,2);
 $('source-status').textContent=`${data.building.source} · ${data.spaces.length} spaces · ${data.assets.length} assets · source data unchanged · memory rev. ${value.revision}`;
 $('precision-support').textContent=data.building.source==='procedural'?'Precision routing: procedural building; room/asset footprints are checked against the original SVG before each route.':'Precision routing is not supported for this data source yet. Source data, map and benchmark tools remain available.';
 $('demo-note').textContent=`Demos use the current in-memory scenario (${value.closed.length} closed elements); nothing is reset.`;
 fillDoors('start');fillDoors('target');endpointVisibility();operationFields();selectInfo(data.building,false);setOriginal();fitFloor();exploreList();
}
function loadBuilding(){const token=++generation;return busy(async()=>{const value=await request('/building?id='+encodeURIComponent($('building').value));if(token!==generation)return;applyBuilding(value);$('result').textContent=JSON.stringify(value.source,null,2);$('result-summary').textContent='Building loaded · choose mode and endpoints.';});}
// Hook for assistant.js: resolves with `data` once the building is loaded; rejects with {code,message} otherwise.
async function selectBuilding(id){
 if(!Array.from($('building').options).some(o=>o.value===id))throw {code:'unknown_building',message:`Building ${id} is not in the catalog.`};
 if(data?.building.id===id&&$('building').value===id)return data;
 if(loading)throw {code:'busy',message:'Another calculation is still running.'};
 $('building').value=id;await loadBuilding();
 if(data?.building.id!==id)throw {code:'building_not_loaded',message:`Building ${id} could not be loaded.`};
 return data;
}
function buildingOptions(catalog){
 const groups=new Map([['dev',[]],['train',[]]]);
 const sorted=[...catalog].sort((a,b)=>Number(b.id===DEMO_BUILDING)-Number(a.id===DEMO_BUILDING)||String(a.name).localeCompare(String(b.name)));
 for(const b of sorted){const key=String(b.split||'dev').toLowerCase();if(!groups.has(key))groups.set(key,[]);groups.get(key).push(option(b.id,`${b.name} · ${b.source}`));}
 const out=[];
 for(const[key,items]of groups){if(!items.length)continue;const g=document.createElement('optgroup');g.label=key.charAt(0).toUpperCase()+key.slice(1);g.append(...items);out.push(g);}
 $('building').replaceChildren(...out);
}
function setOriginal(){$('original').src='/map?'+new URLSearchParams({id:data.building.id,level:floor().level,variant:'png'});}
function allPoints(raw){if(!raw)return[];if(raw.type==='Feature')return allPoints(raw.geometry);if(raw.type==='GeometryCollection')return raw.geometries.flatMap(allPoints);if(raw.type)return allPoints(raw.coordinates);if(Array.isArray(raw)&&raw.length===2&&typeof raw[0]==='number')return[raw];return Array.isArray(raw)?raw.flatMap(allPoints):[];}
function fitPoints(points){if(!points.length)return;const xs=points.map(p=>p[0]),ys=points.map(p=>-p[1]),x=Math.min(...xs),y=Math.min(...ys),w=Math.max(...xs)-x,h=Math.max(...ys)-y,pad=Math.max(w,h)*.07||1;view=fitBox=[x-pad,y-pad,w+2*pad,h+2*pad];draw();}
function fitFloor(){fitPoints(data.spaces.filter(s=>s.floor_id===floor().id).flatMap(s=>allPoints(s.polygon)));}
function fitRoom(id){const space=data.spaces.find(s=>s.id===id);if(space){$('floor').value=space.floor_id;setOriginal();fitPoints(allPoints(space.polygon));}}
function fitRoute(){
 const segments=result?.segments||[];
 if(segments.length&&!segments.some(s=>s.floor_id===floor().id)&&data.floors.some(f=>f.id===segments[0].floor_id)){$('floor').value=segments[0].floor_id;setOriginal();}
 const current=segments.filter(s=>s.floor_id===floor().id);const ids=new Set(current.map(s=>s.space_id).filter(Boolean));
 if(ids.size)fitPoints(data.spaces.filter(s=>ids.has(s.id)).flatMap(s=>allPoints(s.polygon)));else if(current.length)fitPoints(current.flatMap(s=>s.points||s.polyline||[]));else draw();
}
function geometryNode(geometry,classes){
 if(!geometry)return null;if(geometry.type==='Feature')return geometryNode(geometry.geometry,classes);
 if(geometry.type==='GeometryCollection'){const group=shape('g');for(const g of geometry.geometries){const n=geometryNode(g,classes);if(n)group.append(n);}return group;}
 if(geometry.type==='MultiPolygon'){const group=shape('g');for(const p of geometry.coordinates){const n=geometryNode({type:'Polygon',coordinates:p},classes);if(n)group.append(n);}return group;}
 if(geometry.type==='Polygon'){const path=geometry.coordinates.map(r=>r.map(([x,y],i)=>`${i?'L':'M'}${x},${-y}`).join(' ')+' Z').join(' ');return shape('path',{d:path,class:classes,'fill-rule':'evenodd'});}
 return null;
}
function polygonGeometry(raw){if(raw?.type)return raw;if(raw?.exterior)return {type:'Polygon',coordinates:[raw.exterior,...(raw.holes||[])]};return {type:'Polygon',coordinates:Array.isArray(raw?.[0]?.[0])?raw:[raw]};}
function polys(g){if(!g)return[];if(g.type==='Polygon')return[g.coordinates];if(g.type==='MultiPolygon')return g.coordinates;if(g.type==='Feature')return polys(g.geometry);if(g.type==='GeometryCollection')return g.geometries.flatMap(polys);return[];}
function inGeometry(xy,g){return polys(g).some(p=>inRing(xy,p[0])&&!p.slice(1).some(h=>inRing(xy,h)));}
function footprint(asset){const f=asset.footprint;if(!f||!asset.position||!Number.isFinite(f.width_m)||!Number.isFinite(f.depth_m))return null;const w=f.width_m/2,d=f.depth_m/2,theta=(f.rotation_deg||0)*Math.PI/180;return [[-w,-d],[w,-d],[w,d],[-w,d]].map(([x,y])=>[asset.position[0]+x*Math.cos(theta)-y*Math.sin(theta),asset.position[1]+x*Math.sin(theta)+y*Math.cos(theta)]);}
function line(points,classes){return shape('polyline',{points:points.map(([x,y])=>`${x},${-y}`).join(' '),class:classes});}
function cls(id,base){return base+(closedSet.has(id)?' closed':'')+($('diag-layer').checked&&diag.has(id)?' diag':'')+(marks.has(id)?' mark':'')+(selected?.id===id?' sel':'');}
function unitSize(){return Math.max(view[2]/Math.max(svg.clientWidth,1),view[3]/Math.max(svg.clientHeight,1));}
function marker(p,classes,text,unit){const g=shape('g');g.append(shape('circle',{cx:p[0],cy:-p[1],r:unit*6,class:classes}));g.append(shape('text',{x:p[0],y:-p[1]+unit*3.5,'text-anchor':'middle','font-size':unit*10,class:'mark-text'},text));return g;}
// Precise and challenge results both carry route segments [{floor_id,space_id,points}]; reference lines are drawn separately.
function routeEnds(){const segs=result&&result.model!=='reference'&&Array.isArray(result.segments)?result.segments:[];return {segs,first:segs.find(s=>s.points?.length),last:[...segs].reverse().find(s=>s.points?.length)};}
function routedAssets(){const out=new Set();if(result)for(const p of ['start','target'])if($(p+'-kind').value==='asset')out.add($(p+'-'+'asset').value);return out;}
function draw(){
 if(!data||!payload)return;
 const isoMode=$('view-mode').value==='iso';
 svg.toggleAttribute('hidden',isoMode);iso.toggleAttribute('hidden',!isoMode);$('action-card').hidden=isoMode||!cardHits.length;
 closedSet=new Set(payload.closed);
 if(isoMode){drawIso();return;}
 if(!view)return;
 svg.replaceChildren();svg.setAttribute('viewBox',view.join(' '));
 const unit=unitSize(),current=floor().id,showDiag=$('diag-layer').checked,routed=routedAssets();
 if($('rooms-layer').checked)for(const space of data.spaces.filter(s=>s.floor_id===current)){const n=geometryNode(polygonGeometry(space.polygon),cls(space.id,'room'+(['hall','corridor'].includes(space.type)?' hall':'')));if(!n)continue;n.dataset.space=space.id;n.append(shape('title',{},space.name));svg.append(n);}
 const geometries=analysis?[analysis.geometry]:(result?.geometries||result?.geometry||[]);const layers=Array.isArray(geometries)?geometries:[geometries];
 if($('free-layer').checked)for(const g of layers.filter(g=>g&&(!g.floor_id||g.floor_id===current))){for(const[geometry,classes]of [[g.free,'free'],[g.expanded_obstacles,'buffer']]){const n=geometryNode(geometry,classes);if(n){n.setAttribute('pointer-events','none');svg.append(n);}}}
 if($('assets-layer').checked)for(const asset of data.assets.filter(a=>a.floor_id===current)){const polygon=footprint(asset);if(!polygon)continue;const n=geometryNode(polygonGeometry(polygon),cls(asset.id,'asset'+(routed.has(asset.id)?' routed':'')));if(!n)continue;n.dataset.space=asset.space_id;n.append(shape('title',{},asset.name));svg.append(n);}
 if($('doors-layer').checked)for(const opening of data.openings.filter(o=>o.floor_id===current)){const n=shape('circle',{cx:opening.position[0],cy:-opening.position[1],r:unit*2.7,class:cls(opening.id,'door')});n.append(shape('title',{},`${opening.id} · ${opening.width_m} m`));svg.append(n);}
 if($('connectors-layer').checked)for(const c of data.vertical_connectors)for(const p of connectorPoints(c))if(p.floor_id===current){const n=shape('rect',{x:p.position[0]-unit*4,y:-p.position[1]-unit*4,width:unit*8,height:unit*8,class:cls(c.id,'conn')});n.append(shape('title',{},c.name||c.id));svg.append(n);}
 if(showDiag)for(const id of diag.keys()){const found=findRecord(id),p=found&&(found.kind==='opening'||found.kind==='asset')?found.record.position:null;if(p&&found.record.floor_id===current)svg.append(shape('circle',{cx:p[0],cy:-p[1],r:unit*7,class:'diag-ring'}));}
 if($('reference-layer').checked){const reference=result?.model==='reference'?result:result?.reference_comparison;for(const segment of reference?.segments||[])if(segment.floor_id===current)svg.append(line(segment.points,'reference-line'));}
 const {segs,first,last}=routeEnds();
 for(const segment of segs)if(segment.floor_id===current&&segment.points?.length)svg.append(line(segment.points,'route-line'));
 for(const portal of result?.portals||[]){if(portal.floor_id!==current)continue;const p=portal.inner||portal.position;if(p)svg.append(shape('circle',{cx:p[0],cy:-p[1],r:unit*3.5,class:'portal'}));}
 if(first&&first.floor_id===current)svg.append(marker(first.points[0],'start-mark','S',unit));
 if(last&&last.floor_id===current)svg.append(marker(last.points.at(-1),'goal-mark','T',unit));
}
const elevation=f=>Number.isFinite(f.elevation_m)?f.elevation_m:null;
function project(x,y,z){return [(x-y)*.866,(x+y)*.5-z];}
function isoPath(g,z){return polys(g).map(p=>p.map(r=>r.map(([x,y],i)=>{const q=project(x,y,z);return (i?'L':'M')+q[0].toFixed(2)+','+q[1].toFixed(2);}).join(' ')+' Z').join(' ')).join(' ');}
function isoLine(points,z,classes){return shape('polyline',{points:points.map(([x,y])=>project(x,y,z).map(v=>v.toFixed(2)).join(',')).join(' '),class:classes});}
function drawIso(){
 iso.replaceChildren();
 if(data.floors.some(f=>elevation(f)===null)){iso.setAttribute('viewBox','0 0 700 100');iso.append(shape('text',{x:15,y:35,'font-size':15},'2.5D unavailable: floor elevation missing. Use 2D.'));return;}
 const floors=[...data.floors].sort((a,b)=>elevation(a)-elevation(b)),pts=data.spaces.flatMap(s=>allPoints(s.polygon));
 if(!pts.length||!floors.length)return;
 const x0=Math.min(...pts.map(p=>p[0])),x1=Math.max(...pts.map(p=>p[0])),y0=Math.min(...pts.map(p=>p[1])),y1=Math.max(...pts.map(p=>p[1])),span=Math.max(x1-x0,y1-y0,1);
 const zs=floors.map(elevation),gaps=zs.slice(1).map((z,i)=>z-zs[i]).filter(g=>g>.01),scale=gaps.length?Math.max(1,span*.9/Math.min(...gaps)):1;
 const corners=floors.flatMap(f=>[[x0,y0],[x1,y0],[x1,y1],[x0,y1]].map(([x,y])=>project(x,y,elevation(f)*scale)));
 const bx=Math.min(...corners.map(p=>p[0])),by=Math.min(...corners.map(p=>p[1])),bw=Math.max(...corners.map(p=>p[0]))-bx,bh=Math.max(...corners.map(p=>p[1]))-by,pad=Math.max(bw,bh)*.05,fs=Math.max(bw,bh)/45;
 iso.setAttribute('viewBox',[bx-pad,by-pad-fs*2,bw+2*pad,bh+2*pad+fs*2].join(' '));
 iso.append(shape('text',{x:bx,y:by-fs*.8,'font-size':fs,class:'label'},`View only · floor spacing exaggerated ×${scale.toFixed(1)} · no lift shafts · routing unchanged`));
 const routed=routedAssets(),{segs,first,last}=routeEnds();
 for(const f of floors){
  const z=elevation(f)*scale,g=shape('g'),lp=project(x0,y1,z);
  g.append(shape('text',{x:lp[0],y:lp[1]+fs,'font-size':fs,class:'label'},`Floor ${f.level} · ${f.name} · ${elevation(f)} m`));
  if($('rooms-layer').checked)for(const s of data.spaces.filter(s=>s.floor_id===f.id)){const n=shape('path',{d:isoPath(polygonGeometry(s.polygon),z),class:cls(s.id,'room'+(['hall','corridor'].includes(s.type)?' hall':'')),'fill-rule':'evenodd'});n.append(shape('title',{},s.name));g.append(n);}
  if($('assets-layer').checked)for(const a of data.assets.filter(a=>a.floor_id===f.id)){const fp=footprint(a);if(!fp)continue;const n=shape('path',{d:isoPath(polygonGeometry(fp),z),class:cls(a.id,'asset'+(routed.has(a.id)?' routed':''))});n.append(shape('title',{},a.name));g.append(n);}
  if($('reference-layer').checked){const reference=result?.model==='reference'?result:result?.reference_comparison;for(const segment of reference?.segments||[])if(segment.floor_id===f.id&&segment.points?.length)g.append(isoLine(segment.points,z,'reference-line'));}
  for(const segment of segs)if(segment.floor_id===f.id&&segment.points?.length)g.append(isoLine(segment.points,z,'route-line'));
  for(const [seg,idx,classes,text] of [[first,0,'start-mark','S'],[last,-1,'goal-mark','T']]){if(!seg||seg.floor_id!==f.id)continue;const p=seg.points.at(idx),q=project(p[0],p[1],z);g.append(shape('circle',{cx:q[0],cy:q[1],r:fs*.6,class:classes}));g.append(shape('text',{x:q[0],y:q[1]+fs*.35,'text-anchor':'middle','font-size':fs*.9,class:'mark-text'},text));}
  iso.append(g);
 }
}
function point(event){return new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());}
function inRing(point,ring){let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if((a[1]>point[1])!==(b[1]>point[1])&&point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0])inside=!inside;}return inside;}
function hitsAt(xy,unit,current){
 const out=[],push=(kind,record)=>out.push({kind,record});
 if($('assets-layer').checked)for(const a of data.assets)if(a.floor_id===current){const f=footprint(a);if(f&&inRing(xy,f))push('asset',a);}
 if($('doors-layer').checked)for(const o of data.openings)if(o.floor_id===current&&Math.hypot(o.position[0]-xy[0],o.position[1]-xy[1])<=unit*4)push('opening',o);
 if($('connectors-layer').checked)for(const c of data.vertical_connectors)for(const p of connectorPoints(c))if(p.floor_id===current&&Math.hypot(p.position[0]-xy[0],p.position[1]-xy[1])<=unit*5){push('connector',c);break;}
 if($('rooms-layer').checked)for(const s of data.spaces)if(s.floor_id===current&&inGeometry(xy,polygonGeometry(s.polygon)))push('space',s);
 return out;
}
function currentHit(){return cardHits[Number($('card-hits').value)||0];}
function showCard(list){
 cardHits=list;const pick=$('card-hits');
 pick.replaceChildren(...list.map((h,i)=>option(String(i),`${KIND_LABEL[h.kind]} · ${recordLabel(h.kind,h.record)}`)));pick.value='0';pick.hidden=list.length<2;
 renderCard();
}
function endpointTarget(h){
 const r=h.record;
 if(h.kind==='asset')return {kind:'asset',id:r.id,text:'Machine / asset · access candidate, chosen heuristically'};
 if(h.kind==='space')return {kind:'space',id:r.id,text:'Room · checked interior point'};
 if(h.kind==='opening'){
  const sides=(r.space_ids||[]).map(id=>data.spaces.find(s=>s.id===id)).filter(Boolean);
  const side=sides.find(s=>cardHits.some(x=>x.kind==='space'&&x.record.id===s.id))||sides.find(s=>s.floor_id===floor().id)||sides[0];
  return side?{kind:'door',id:r.id,space:side.id,text:`Door · room side ${spaceLabel(side)}`}:null;
 }
 if(h.kind==='connector'){const room=connectorRoom(r);return room?{kind:'space',id:room,text:'Stop room of the lift / stairs · no route through the shaft'}:null;}
 return null;
}
function renderCard(){
 const h=currentHit();$('action-card').hidden=!h||$('view-mode').value!=='2d';if(!h)return;
 const r=h.record,f=recordFloor(r),t=endpointTarget(h);
 $('card-title').textContent=recordLabel(h.kind,r);
 $('card-meta').textContent=`${KIND_LABEL[h.kind]} · ${r.type||r.room_type||r.kind||'–'} · floor ${f?floorName(f):'?'} · ${r.id}`;
 $('card-start').disabled=$('card-target').disabled=!t;
 $('card-note').textContent=t?t.text:'Inspect only: no endpoint can be derived for this entry. Choose the stop room via the room list.';
}
function assignEndpoint(prefix){
 const h=currentHit(),t=h&&endpointTarget(h);if(!t)return;
 $(prefix+'-kind').value=t.kind;
 if(t.kind==='asset')setSelect(prefix+'-asset',t.id);
 else if(t.kind==='door'){setSelect(prefix+'-space',t.space);fillDoors(prefix);$(prefix+'-door').value=t.id;}
 else{setSelect(prefix+'-space',t.id);fillDoors(prefix);}
 endpointVisibility();showTab('navigate');invalidateNavigation();
 $('result-summary').textContent=`${prefix==='start'?'Start':'Target'} set: ${recordLabel(h.kind,h.record)} · ${t.text} · recalculate route.`;
}
function inspectHit(){
 const h=currentHit();if(!h)return;
 $('explore-type').value=Object.keys(EXPLORE).find(k=>EXPLORE[k]===h.kind);$('explore-search').value='';exploreList();$('explore-list').value=h.record.id;
 selectInfo(h.record,true);showTab('inspect');
}
svg.addEventListener('click',event=>{
 if(dragMoved||!data||!view)return;
 const p=point(event),xy=[p.x,-p.y],mode=$('click-mode').value,current=floor().id;
 if(mode==='info'){
  const hits=hitsAt(xy,unitSize(),current);
  if(!hits.length){cardHits=[];$('action-card').hidden=true;return;}
  showCard(hits);selectInfo(hits[0].record,false);
  if(!result&&!analysis)$('result-summary').textContent=`Selected: ${recordLabel(hits[0].kind,hits[0].record)}${hits.length>1?' · '+hits.length+' hits, choose in the map card':''}`;
  return;
 }
 const space=data.spaces.find(s=>s.floor_id===current&&inGeometry(xy,polygonGeometry(s.polygon)));
 if(!space){showError({code:'invalid_input',message:'Map point is not inside a room on this floor.'});return;}
 showError(null);
 const prefix=mode;$(prefix+'-kind').value='point';setSelect(prefix+'-space',space.id);fillDoors(prefix);$(prefix+'-x').value=String(xy[0]);$(prefix+'-y').value=String(xy[1]);endpointVisibility();invalidateNavigation();
 $('result-summary').textContent=`${prefix==='start'?'Start':'Target'} set: ${xy.map(v=>v.toFixed(3)).join(', ')} m · recalculate route; validity is checked then.`;
});
svg.addEventListener('wheel',event=>{if(!view)return;event.preventDefault();const p=point(event),factor=event.deltaY>0?1.15:1/1.15;if(view[2]*factor<fitBox[2]/150||view[2]*factor>fitBox[2]*10)return;view=[p.x+(view[0]-p.x)*factor,p.y+(view[1]-p.y)*factor,view[2]*factor,view[3]*factor];draw();},{passive:false});
svg.addEventListener('pointerdown',event=>{if(event.button!==0||!view)return;dragMoved=false;drag={x:event.clientX,y:event.clientY,matrix:svg.getScreenCTM().inverse(),view:[...view]};});
window.addEventListener('pointermove',event=>{if(!drag)return;if(!dragMoved&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<4)return;dragMoved=true;svg.classList.add('dragging');const a=new DOMPoint(drag.x,drag.y).matrixTransform(drag.matrix),b=new DOMPoint(event.clientX,event.clientY).matrixTransform(drag.matrix);view=[drag.view[0]+a.x-b.x,drag.view[1]+a.y-b.y,...drag.view.slice(2)];draw();});
window.addEventListener('pointerup',()=>{drag=null;svg.classList.remove('dragging');setTimeout(()=>dragMoved=false,0);});
function setEndpointFields(prefix,spec){
 $(prefix+'-kind').value=spec.kind;
 if(spec.kind==='asset')setSelect(prefix+'-asset',F(spec.id));
 else{setSelect(prefix+'-space',F(spec.space));fillDoors(prefix);}
 if(spec.kind==='door')$(prefix+'-door').value=F(spec.id);
 if(spec.kind==='point'){$(prefix+'-x').value=spec.xy[0];$(prefix+'-y').value=spec.xy[1];}
}
async function demo(kind){
 if(loading)return;const d=DEMOS[kind];if(!d)return;
 if(data.building.id!==DEMO_BUILDING){$('building').value=DEMO_BUILDING;await loadBuilding();if(data.building.id!==DEMO_BUILDING)return;}
 showTab('navigate');$('model').value='precise';modeVisibility();$('click-mode').value='info';
 $('profile').value=d.profile;$('width').value=.6;$('safety').value=.05;$('step-free').checked=!!d.stepFree;$('schematic').checked=!!d.schematic;
 $('start-search').value=$('target-search').value='';applyFilter('start');applyFilter('target');
 $('floor').value=F(d.floor);
 setEndpointFields('start',d.start);setEndpointFields('target',d.target);
 endpointVisibility();setOriginal();await route();
}
for(const button of document.querySelectorAll('nav button'))button.addEventListener('click',()=>showTab(button.dataset.tab));
$('building').addEventListener('change',loadBuilding);
$('floor').addEventListener('change',()=>{setOriginal();fitFloor();});
for(const prefix of ['start','target']){$(prefix+'-kind').addEventListener('change',endpointVisibility);$(prefix+'-space').addEventListener('change',()=>fillDoors(prefix));}
for(const prefix of ['start','target'])$(prefix+'-search').addEventListener('input',()=>applyFilter(prefix));
$('model').addEventListener('change',modeVisibility);
for(const id of ['model','profile','width','safety','step-free','schematic','forklift','start-kind','start-space','start-door','start-x','start-y','target-kind','target-space','target-asset','target-door','target-x','target-y'])$(id).addEventListener('change',invalidateNavigation);
$('start-asset').addEventListener('change',invalidateNavigation);
for(const id of ['width','safety','start-x','start-y','target-x','target-y'])$(id).addEventListener('input',invalidateNavigation);
$('route').addEventListener('click',route);$('analyze').addEventListener('click',analyze);$('compute').addEventListener('click',compute);$('operation').addEventListener('change',operationFields);
$('close').addEventListener('click',()=>scenario('close'));$('open').addEventListener('click',()=>scenario('open'));$('add-door').addEventListener('click',()=>scenario('add_door'));$('reset').addEventListener('click',()=>scenario('reset'));
for(const layer of ['rooms-layer','assets-layer','doors-layer','connectors-layer','free-layer','reference-layer','diag-layer','view-mode'])$(layer).addEventListener('change',draw);
$('fit').addEventListener('click',()=>result?fitRoute():fitFloor());window.addEventListener('resize',draw);
$('explore-type').addEventListener('change',exploreList);$('explore-search').addEventListener('input',exploreList);$('explore-list').addEventListener('change',exploreSelect);
$('card-hits').addEventListener('change',()=>{const h=currentHit();if(h)selectInfo(h.record,false);renderCard();});
$('card-start').addEventListener('click',()=>assignEndpoint('start'));$('card-target').addEventListener('click',()=>assignEndpoint('target'));$('card-inspect').addEventListener('click',inspectHit);
$('card-close').addEventListener('click',()=>{cardHits=[];$('action-card').hidden=true;});
$('example-split').addEventListener('change',loadExamples);$('example-op').addEventListener('change',renderExamples);$('example-search').addEventListener('input',renderExamples);
$('example-list').addEventListener('change',loadExample);$('example-load').addEventListener('click',loadExample);$('example-check').addEventListener('click',checkExample);
for(const[kind,id]of [['direct','demo-direct'],['desk','demo-desk'],['cnc','demo-cnc'],['rooms','demo-rooms'],['floors','demo-floors']])$(id).addEventListener('click',()=>demo(kind));
modeVisibility();
Promise.all([request('/catalog'),request('/specs')]).then(async([catalog,operations])=>{specs=operations;buildingOptions(catalog);$('operation').replaceChildren(...specs.map(s=>option(s.name,s.label)));$('example-op').replaceChildren(option('','All operations'),...specs.map(s=>option(s.name,s.label)));await loadBuilding();loadExamples();}).catch(showError);
