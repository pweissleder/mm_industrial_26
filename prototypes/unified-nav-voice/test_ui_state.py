"""Regression for a failed calculation retaining a previous clearance claim."""
import pathlib
import subprocess
import unittest


class UiStateTests(unittest.TestCase):
    def run_browser_script(self, checks):
        """Actual application handlers, synthetic DOM only; no mirrored handlers."""
        setup = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('app.js', 'utf8');
const nodes = new Map();
const node = id => {
 if(!nodes.has(id)) nodes.set(id, {value:'',textContent:'',checked:false,disabled:false,open:false,
   children:[],options:[],listeners:{},classList:{add(){},remove(){},toggle(){}},
   append(...children){this.children.push(...children);this.options=this.children;},replaceChildren(...children){this.children=children;this.options=children;},
   setAttribute(){},toggleAttribute(){},
   addEventListener(type,handler){(this.listeners[type] ||= []).push(handler);}});
 return nodes.get(id);
};
const context = vm.createContext({console,URLSearchParams,Map,Set,JSON,Number,Math,Array,
 document:{getElementById:node,querySelectorAll:()=>[],createElement:()=>node('generated-'+nodes.size)},
 window:{addEventListener(){}},setTimeout,clearTimeout});
const bootstrap = source.lastIndexOf('Promise.all(');
assert.ok(bootstrap>0);
vm.runInContext(source.slice(0,bootstrap),context);
vm.runInContext(`draw=()=>{globalThis.drawCount=(globalThis.drawCount||0)+1;};
  setOriginal=()=>{globalThis.originalCount=(globalThis.originalCount||0)+1;};
  fitFloor=()=>{globalThis.fitCount=(globalThis.fitCount||0)+1;};`,context);
"""
        outcome = subprocess.run(
            ["node", "-e", setup + checks], cwd=pathlib.Path(__file__).parent,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)

    def test_navigation_edits_clear_old_route_but_floor_only_changes_view(self):
        self.run_browser_script(r"""
for(const id of ['model','profile','width','safety','step-free','schematic','start-kind','start-space','start-door','start-x','start-y','target-kind','target-space','target-asset','target-door','target-x','target-y']){
 vm.runInContext('result={length_m:12};analysis={old:true};',context);
 node('model-note').textContent='previous clearance 0.35';
 for(const handler of node(id).listeners.change) {
  // Other handlers update dependent controls; the invalidation handler itself
  // is the last actual registered handler on all navigation controls.
  if(handler===node(id).listeners.change.at(-1)) handler();
 }
 assert.equal(vm.runInContext('result',context),null,id);
 assert.equal(vm.runInContext('analysis',context),null,id);
 assert.match(node('model-note').textContent,/discarded/,id);
}
for(const id of ['width','safety','start-x','start-y','target-x','target-y']){
 vm.runInContext('result={length_m:12};',context);
 node(id).listeners.input[0]();
 assert.equal(vm.runInContext('result',context),null,id);
}
node('start-space').value='F0-room';node('target-space').value='F1-room';
vm.runInContext('result={length_m:12};',context);
node('floor').value='F1';node('floor').listeners.change[0]();
assert.equal(node('start-space').value,'F0-room');
assert.equal(node('target-space').value,'F1-room');
assert.equal(vm.runInContext('result.length_m',context),12);
assert.equal(context.originalCount,1);assert.equal(context.fitCount,1);
""")

    def test_reference_note_has_no_precise_clearance_claim_and_info_opens(self):
        self.run_browser_script(r"""
node('model-note').textContent='measured obstacle clearance >= 0.35 m';
vm.runInContext('renderResult({model:"reference",benchmark_cost_m:12});',context);
assert.match(node('model-note').textContent,/no obstacle/);
assert.doesNotMatch(node('model-note').textContent,/measured|separate checked/);
vm.runInContext('selectInfo({id:"asset",name:"Machine"});',context);
assert.equal(node('selection-details').open,true);
assert.equal(JSON.parse(node('selected').textContent).id,'asset');
node('selection-details').open=false;
vm.runInContext('selectInfo({id:"building"},false);',context);
assert.equal(node('selection-details').open,false);
""")

    def test_reference_overlay_is_opt_in_and_does_not_swallow_geometry_clicks(self):
        folder = pathlib.Path(__file__).parent
        html = (folder / "index.html").read_text(encoding="utf-8")
        css = (folder / "style.css").read_text(encoding="utf-8")
        import re
        from html.parser import HTMLParser
        class Inputs(HTMLParser):
            attributes = None
            def handle_starttag(self, tag, attrs):
                if tag == 'input' and dict(attrs).get('id') == 'reference-layer':
                    self.attributes = dict(attrs)
        parser = Inputs();parser.feed(html)
        self.assertIsNotNone(parser.attributes)
        self.assertNotIn('checked', parser.attributes)
        for selector in ('.route-line','.reference-line','.portal','.endpoint','.start-mark','.goal-mark'):
            clean_css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
            self.assertTrue(any(selector in [s.strip() for s in selectors.split(',')] and re.search(r'pointer-events\s*:\s*none', declarations)
                                for selectors,declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', clean_css)), selector)

    def test_map_actions_preserve_asset_ids_and_room_selection(self):
        self.run_browser_script(r"""
vm.runInContext(`data={floors:[{id:'F0',level:0}],spaces:[{id:'R',floor_id:'F0',name:'Room'}],
  assets:[{id:'M',floor_id:'F0',space_id:'R',name:'Machine',type:'cnc_mill'}],openings:[],vertical_connectors:[]};
  payload={closed:[],reference_anchors:{R:[2,2]}};`,context);
node('floor').value='F0';node('view-mode').value='2d';
vm.runInContext(`setChoices('start-space',[['R','Room']]);setChoices('target-space',[['R','Room']]);
 setChoices('start-asset',[['M','Machine']]);setChoices('target-asset',[['M','Machine']]);
 showCard([{kind:'asset',record:data.assets[0]}]);assignEndpoint('target');`,context);
let actual=JSON.parse(vm.runInContext(`JSON.stringify(endpoint('target'))`,context));
assert.deepEqual(actual,{kind:'asset',id:'M'});
vm.runInContext(`assignEndpoint('start');`,context);
actual=JSON.parse(vm.runInContext(`JSON.stringify(endpoint('start'))`,context));
assert.deepEqual(actual,{kind:'asset',id:'M'});
vm.runInContext(`showCard([{kind:'space',record:data.spaces[0]}]);assignEndpoint('start');`,context);
actual=JSON.parse(vm.runInContext(`JSON.stringify(endpoint('start'))`,context));
assert.deepEqual(actual,{kind:'space',id:'R'});
assert.equal(node('target-asset').value,'M');
""")

    def test_connectors_use_actual_stop_room_anchor_and_no_invented_height(self):
        self.run_browser_script(r"""
vm.runInContext(`data={floors:[{id:'F0',level:0},{id:'F1',level:1}],
 spaces:[{id:'L0',floor_id:'F0'},{id:'L1',floor_id:'F1'}]};
 payload={reference_anchors:{L0:[2,3],L1:[2,4]}};`,context);
const actual=JSON.parse(vm.runInContext(`JSON.stringify(connectorPoints({floor_ids:['F0','F1'],space_ids:['L0','L1']}))`,context));
assert.equal(actual.length,2);assert.deepEqual(actual[1].position,[2,4]);
assert.equal(actual[1].schematic_marker,true);
assert.equal(vm.runInContext(`elevation({level:3})`,context),null);
assert.equal(vm.runInContext(`elevation({level:3,elevation_m:9.5})`,context),9.5);
assert.equal(vm.runInContext(`Object.keys(DEMOS).length`,context),5);
""")

    def test_challenge_mode_result_arguments_and_controls(self):
        self.run_browser_script(r"""
vm.runInContext(`data={building:{id:'B'},floors:[{id:'F0',level:0}],spaces:[{id:'R1',floor_id:'F0',name:'Hall'},{id:'R2',floor_id:'F0',name:'Lab'}],assets:[],openings:[],vertical_connectors:[]};`,context);
node('start-kind').value='space';node('start-space').value='R1';node('target-kind').value='space';node('target-space').value='R2';
node('forklift').checked=true;node('step-free').checked=true;
node('model').value='precise';
let args=JSON.parse(vm.runInContext('JSON.stringify(routeArguments())',context));
assert.equal('forklift' in args,false);assert.equal(args.profile!==undefined,true);
node('model').value='reference';
args=JSON.parse(vm.runInContext('JSON.stringify(routeArguments())',context));
assert.equal('forklift' in args,false);
node('model').value='challenge';
args=JSON.parse(vm.runInContext('JSON.stringify(routeArguments())',context));
assert.deepEqual(args,{model:'challenge',start:{kind:'space',id:'R1'},target:{kind:'space',id:'R2'},step_free:true,forklift:true});
vm.runInContext('modeVisibility()',context);
assert.equal(node('profile-wrap').hidden,true);assert.equal(node('forklift-wrap').hidden,false);
node('model').value='precise';vm.runInContext('modeVisibility()',context);
assert.equal(node('profile-wrap').hidden,false);assert.equal(node('forklift-wrap').hidden,true);
vm.runInContext(`renderResult({model:'challenge',status:'ok',reachable:true,cost_m:42.123,space_path:['R1','R2'],
 transitions:[{kind:'door',cost_m:1.5}],warnings:['w1'],assumptions:['a1'],
 segments:[{floor_id:'F0',space_id:'R1',points:[[0,0],[1,1]]},{floor_id:'F0',space_id:'R2',points:[[1,1],[2,2]]}]});`,context);
assert.equal(node('result-summary').textContent,'Challenge route · ok · 42.1 m cost · 2 spaces');
const facts=node('facts').children.map(n=>n.textContent).join('|');
assert.match(facts,/Spaces: Hall → Lab/);assert.match(facts,/door: 1\.50 m cost/);assert.match(facts,/Assumption: a1/);
assert.equal(node('warnings').children[0].textContent,'w1');
const ends=JSON.parse(vm.runInContext('JSON.stringify(routeEnds())',context));
assert.equal(ends.segs.length,2);assert.deepEqual(ends.first.points[0],[0,0]);assert.deepEqual(ends.last.points.at(-1),[2,2]);
vm.runInContext(`renderResult({model:'challenge',status:'unreachable',reachable:false,unreachable_reason:'all doors closed',segments:[]});`,context);
assert.match(node('result-summary').textContent,/not reachable/);
assert.equal(node('warnings').children[0].textContent,'Unreachable: all doors closed');
""")

    def test_picker_respects_polygon_holes(self):
        self.run_browser_script(r"""
assert.equal(vm.runInContext(`inGeometry([5,5],polygonGeometry({exterior:[[0,0],[10,0],[10,10],[0,10]],holes:[[[4,4],[6,4],[6,6],[4,6]]]}))`,context),false);
assert.equal(vm.runInContext(`inGeometry([2,2],polygonGeometry({exterior:[[0,0],[10,0],[10,10],[0,10]],holes:[[[4,4],[6,4],[6,6],[4,6]]]}))`,context),true);
""")

    def test_inspecting_source_geometry_keeps_current_route_summary(self):
        self.run_browser_script(r"""
vm.runInContext(`data={floors:[{id:'F0',level:0}],spaces:[{id:'R',floor_id:'F0',name:'Room',polygon:[[0,0],[10,0],[10,10],[0,10]]}],
 assets:[{id:'M',name:'Machine',type:'cnc_mill',space_id:'R',floor_id:'F0',position:[5,5],footprint:{width_m:2,depth_m:2}}],openings:[],vertical_connectors:[]};
 view=[0,-10,10,10];result={length_m:12.34};point=()=>({x:5,y:-5});`,context);
node('floor').value='F0';node('view-mode').value='2d';node('click-mode').value='info';
node('assets-layer').checked=true;node('rooms-layer').checked=true;
node('map').clientWidth=1000;node('map').clientHeight=800;
node('result-summary').textContent='Model XY route: 12.340 m';
node('map').listeners.click[0]({});
assert.equal(node('result-summary').textContent,'Model XY route: 12.340 m');
assert.match(node('card-title').textContent,/Machine/);
""")

    def test_failed_calculation_discards_previous_clearance_claim(self):
        # Execute the actual UI failure transition without a browser framework.
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app.js', 'utf8');
const start = source.indexOf('async function busy(task)');
const end = source.indexOf('const floorName=', start);
assert.ok(start >= 0 && end > start);
const nodes = Object.fromEntries(['result-summary', 'model-note', 'result'].map(id => [id, {textContent: ''}]));
nodes['model-note'].textContent = 'measured obstacle clearance >= 0.350 m';
const controls = [{disabled: false}, {disabled: true}];
let displayedError, draws = 0;
const context = vm.createContext({
  $: id => nodes[id],
  document: {body: {classList: {add() {}, remove() {}}}, querySelectorAll: () => controls},
  loading: false, result: {oldRoute: true}, analysis: {oldClearance: true},
  marks: new Set(), clearChips: () => {}, setDiagnosis: () => {},
  showError: error => {displayedError = error;}, draw: () => {draws++;}
});
vm.runInContext(source.slice(start, end), context);
(async () => {
  const failure = {code: 'geometry_unsupported', message: 'No supported route'};
  await context.busy(async () => {throw failure;});
  assert.equal(context.result, null);
  assert.equal(context.analysis, null);
  assert.equal(context.loading, false);
  assert.equal(displayedError, failure);
  assert.equal(draws, 1);
  assert.deepEqual(controls.map(c => c.disabled), [false, true]);
  assert.match(nodes['result-summary'].textContent, /geometry_unsupported/);
  assert.match(nodes['model-note'].textContent, /discarded/);
  assert.doesNotMatch(nodes['model-note'].textContent, /0\.350|measured/);
  assert.equal(JSON.parse(nodes.result.textContent).code, 'geometry_unsupported');
  console.log('PASS: failure clears prior route, analysis and clearance claim; controls restored');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        outcome = subprocess.run(
            ["node", "-e", script], cwd=pathlib.Path(__file__).parent,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        self.assertIn("PASS:", outcome.stdout)


if __name__ == "__main__":
    unittest.main()
