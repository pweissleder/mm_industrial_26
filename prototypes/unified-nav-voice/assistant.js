'use strict';
// Chat + push-to-talk panel. Uses the same backend Service as the map controls; every route,
// search highlight or change shown here comes from deterministic tool results.
(()=>{
const root=document.getElementById('assistant');if(!root)return;
const A=id=>document.getElementById(id);
const EXAMPLES=[
 ['Where is the CNC mill?','Ambiguous machine name → follow-up question'],
 ["I'm in Meeting room 0.20.",'Remembers your position'],
 ['How do I get to the quality lab?','Route from remembered start'],
 ['Is there a step-free way to Office 1.04?','Floor change, step-free'],
 ['What if the Office corridor 0.16 were closed?','Preview, nothing changes'],
 ['Please close the Office corridor 0.16.','Change → needs your confirmation'],
 ['Which is the largest office on floor 1?','Benchmark tool'],
];
const DEMO_BUILDING='proc-factory-dev-003';
root.innerHTML=`<div class='as-head'><strong>Assistant</strong><span id='as-mode' class='note'>connecting…</span>
<label class='check'><input id='as-speak' type='checkbox'>Speak replies</label><button id='as-reset' type='button'>New conversation</button></div>
<div id='as-log' role='log' aria-live='polite'></div>
<details id='as-examples-wrap'><summary>Example questions (Factory 003)</summary><div id='as-examples'></div></details>
<form id='as-form'><input id='as-input' autocomplete='off' maxlength='2000' aria-label='Message to the assistant' placeholder='Ask in English, e.g. “Where is the CNC mill?”'>
<button id='as-send' class='primary'>Send</button><button id='as-talk' type='button' title='Hold to talk (or hold Space while focused)'>🎤 Hold to talk</button></form>
<div id='as-status' class='note' aria-live='polite'>Ready.</div>`;
let session={live:false,voice:false},busyNow=false,context=null,worklet=false,recording=null,held=false,applying=false;
const touched={start:false,target:false};
function status(text){A('as-status').textContent=text;}
function bubble(cls,text,meta){const n=document.createElement('div');n.className='as-msg '+cls;const p=document.createElement('div');p.textContent=text;n.append(p);if(meta){const m=document.createElement('div');m.className='as-meta';m.textContent=meta;n.append(m);}A('as-log').append(n);A('as-log').scrollTop=A('as-log').scrollHeight;return n;}
function setBusy(value){busyNow=value;for(const id of ['as-send','as-input','as-reset'])A(id).disabled=value;A('as-talk').disabled=value&&!recording||!session.voice;}
async function post(path,body){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Prototype-Action':'local'},body:JSON.stringify(body)});
 let value;try{value=await response.json();}catch{throw new Error(`Server answered HTTP ${response.status}.`);}
 if(!response.ok)throw new Error(value?.error?.message||`Request failed (HTTP ${response.status}).`);return value;
}
// ----- shared UI state -----
function endpointSpec(prefix){
 const kind=A(prefix+'-kind')?.value;
 if(kind==='asset')return {kind:'asset',id:A(prefix+'-asset').value};
 const space=A(prefix+'-space')?.value;return space?{kind:'space',id:space}:null;
}
function uiState(){
 if(typeof data==='undefined'||!data)return {};
 const mode=A('model')?.value==='challenge'?'challenge':'precision';
 return {building_id:data.building.id,start:touched.start?endpointSpec('start'):null,destination:touched.target?endpointSpec('target'):null,
  mode,profile:A('profile').value,width_m:Number(A('width').value),safety_m:Number(A('safety').value),step_free:A('step-free').checked,
  forklift:!!A('forklift')?.checked,allow_schematic_vertical:A('schematic').checked};
}
for(const prefix of ['start','target'])for(const suffix of ['kind','space','asset','door','search'])A(prefix+'-'+suffix)?.addEventListener('change',()=>{if(!applying)touched[prefix]=true;});
A('card-start')?.addEventListener('click',()=>{touched.start=true;});A('card-target')?.addEventListener('click',()=>{touched.target=true;});
A('building')?.addEventListener('change',()=>{if(!applying){touched.start=touched.target=false;}});
for(const button of document.querySelectorAll('.demo-row button'))button.addEventListener('click',()=>{touched.start=touched.target=true;});
function setEndpoint(prefix,spec){
 if(!spec)return;A(prefix+'-kind').value=spec.kind;
 if(A(prefix+'-search'))A(prefix+'-search').value='';if(typeof applyFilter==='function')applyFilter(prefix);
 setSelect(prefix+'-'+spec.kind,spec.id);
 if(spec.kind==='space'&&typeof fillDoors==='function')fillDoors(prefix);
 touched[prefix]=true;
}
async function applyUi(ui){
 applying=true;
 try{
  if(ui.building_id&&data?.building?.id!==ui.building_id){if(typeof selectBuilding==='function')await selectBuilding(ui.building_id);else{A('building').value=ui.building_id;await loadBuilding();}touched.start=touched.target=false;}
  const s=ui.settings;
  if(s){A('model').value=s.mode==='challenge'?'challenge':'precise';A('model').dispatchEvent(new Event('change'));
   A('profile').value=s.profile;A('width').value=s.width_m;A('safety').value=s.safety_m;A('step-free').checked=s.step_free;A('schematic').checked=s.allow_schematic_vertical;if(A('forklift'))A('forklift').checked=s.forklift;}
  if(ui.endpoints){setEndpoint('start',ui.endpoints.start);setEndpoint('target',ui.endpoints.destination);if(typeof endpointVisibility==='function')endpointVisibility();}
  if(ui.route){showTab('navigate');renderResult(ui.route.result);fitRoute();}
  else if(ui.route_error){showTab('navigate');const e=ui.route_error.error;result=null;clearChips();showError(e);if(typeof setDiagnosis==='function')setDiagnosis(e);A('result-summary').textContent='No route · '+(e.message||e.code);A('result').textContent=JSON.stringify(e,null,2);draw();}
  if(ui.operation){showTab('benchmark');showOperation(ui.operation);}
  if(ui.highlight&&typeof marks!=='undefined'&&!ui.route){marks.clear();for(const id of ui.highlight)marks.add(id);draw();}
  if(ui.focus&&!ui.route){const found=findRecord(ui.focus.id);if(found)focusRecord(found.kind,found.record);}
 }finally{applying=false;}
}
function toolLine(t){
 const args=Object.entries(t.input||{}).filter(([,v])=>v!==null&&v!==false&&!(Array.isArray(v)&&!v.length)).map(([k,v])=>`${k}=${typeof v==='object'?JSON.stringify(v):v}`).join(', ');
 const o=t.output||{};let outcome=t.status==='error'?`error: ${o.message||o.error}`:(o.status||'ok');
 if(o.length_m!=null)outcome+=` · ${Number(o.length_m).toFixed(1)} m`;if(o.cost_m!=null)outcome+=` · cost ${Number(o.cost_m).toFixed(1)} m`;
 if(o.shown!=null)outcome+=` · ${o.shown} match${o.shown===1?'':'es'}`;if(o.formatted_answer)outcome+=` · ${o.formatted_answer.replace(/\s+/g,' ').slice(0,80)}`;
 return `🔧 ${t.tool}(${args}) → ${outcome} · ${Math.round(t.elapsed_ms)} ms`;
}
function proposalCard(p){
 const n=bubble('as-proposal',`Confirm change: ${p.action==='close'?'close':'reopen'} ${p.element_kind} “${p.element_name}”?`);
 const row=document.createElement('div');const ok=document.createElement('button');ok.textContent='Confirm';ok.className='primary';const no=document.createElement('button');no.textContent='Cancel';row.append(ok,no);n.append(row);
 const act=async approved=>{ok.disabled=no.disabled=true;try{const value=await post('/api/assistant/confirm',{proposal_id:p.proposal_id,approved});bubble('as-bot',value.message);
   if(value.building){applyBuilding(value.building,true);showError(null);A('result-summary').textContent=value.message+' · recompute routes.';draw();}}
  catch(error){bubble('as-error',error.message);}};
 ok.addEventListener('click',()=>act(true));no.addEventListener('click',()=>act(false));
}
async function audioContext(){if(!context)context=new AudioContext();if(context.state==='suspended')await context.resume();return context;}
async function play(encoded,rate){
 const ctx=await audioContext(),raw=atob(encoded),buffer=ctx.createBuffer(1,raw.length/2,rate),out=buffer.getChannelData(0);
 for(let i=0;i<out.length;i++){let v=raw.charCodeAt(i*2)|(raw.charCodeAt(i*2+1)<<8);if(v>=32768)v-=65536;out[i]=v/32768;}
 const source=ctx.createBufferSource();source.buffer=buffer;source.connect(ctx.destination);source.start();
 status('Speaking…');await new Promise(resolve=>{source.onended=resolve;});
}
async function send(text,{speak=A('as-speak').checked,voiceMeta=null}={}){
 if(busyNow||!text.trim())return;setBusy(true);
 if(!voiceMeta)bubble('as-user',text);
 status('Thinking… (Claude is choosing tools)');const began=performance.now();
 try{
  const value=await post('/api/assistant/turn',{text,speak,voice:!!voiceMeta,ui:uiState()});
  for(const t of value.tools||[])bubble('as-tool',toolLine(t));
  await applyUi(value.ui||{});
  const tm=value.timing_ms||{},u=value.usage||{};
  const meta=[`Claude ${(tm.claude_ms/1000).toFixed(1)} s`,`tools ${Math.round(tm.tools_ms)} ms`,voiceMeta,tm.tts_ms!=null?`TTS ${Math.round(tm.tts_ms)} ms`:null,`total ${((performance.now()-began)/1000).toFixed(1)} s`,`${u.input_tokens||0}/${u.output_tokens||0} tokens ≈ $${(u.estimated_usd||0).toFixed(4)}`].filter(Boolean).join(' · ');
  bubble('as-bot',value.reply,meta);
  if(value.ui?.proposal)proposalCard(value.ui.proposal);
  if(value.voice_error)bubble('as-error','Voice output failed: '+value.voice_error+' The text answer above is complete.');
  if(value.audio_pcm16)await play(value.audio_pcm16,value.sample_rate);
  status('Ready.');
 }catch(error){bubble('as-error',error.message||'Request failed.');status('Ready.');}
 finally{setBusy(false);A('as-input').focus();}
}
// ----- push to talk (AudioWorklet → 16 kHz mono WAV, as in the voice POC) -----
function encodeWav(chunks,rate){
 const size=chunks.reduce((s,c)=>s+c.length,0),samples=new Float32Array(size);let offset=0;for(const c of chunks){samples.set(c,offset);offset+=c.length;}
 const ratio=rate/16000,count=Math.min(160000,Math.floor(size/ratio)),bytes=new ArrayBuffer(44+count*2),view=new DataView(bytes);
 const text=(at,v)=>{for(let i=0;i<v.length;i++)view.setUint8(at+i,v.charCodeAt(i));};
 text(0,'RIFF');view.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,count*2,true);
 for(let i=0;i<count;i++){const a=Math.floor(i*ratio),b=Math.max(a+1,Math.floor((i+1)*ratio));let sum=0;for(let j=a;j<b&&j<size;j++)sum+=samples[j];const v=Math.max(-1,Math.min(1,sum/(b-a)));view.setInt16(44+i*2,v<0?v*32768:v*32767,true);}
 const u8=new Uint8Array(bytes);let binary='';for(let i=0;i<u8.length;i+=16384)binary+=String.fromCharCode(...u8.subarray(i,i+16384));return btoa(binary);
}
async function startRecording(){
 if(busyNow||recording||!session.voice)return;held=true;let stream;
 try{
  const ctx=await audioContext();stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
  if(!held){stream.getTracks().forEach(t=>t.stop());status('Microphone ready. Hold the button to talk.');return;}
  if(!worklet){await ctx.audioWorklet.addModule('/recorder.js');worklet=true;}
  const source=ctx.createMediaStreamSource(stream),node=new AudioWorkletNode(ctx,'synera-recorder'),gain=ctx.createGain();gain.gain.value=0;
  const rec={stream,source,node,gain,chunks:[],frames:0,rate:ctx.sampleRate,timer:null};recording=rec;
  node.port.onmessage=e=>{if(recording!==rec)return;rec.chunks.push(e.data);rec.frames+=e.data.length;};
  source.connect(node);node.connect(gain);gain.connect(ctx.destination);rec.timer=setTimeout(stopRecording,10000);
  A('as-talk').classList.add('recording');A('as-talk').textContent='● Release to send';status('Listening… (max 10 s)');
 }catch{stream?.getTracks().forEach(t=>t.stop());held=false;bubble('as-error','Microphone access failed. Allow the microphone for this localhost page and try again.');}
}
async function stopRecording(){
 held=false;if(!recording)return;const rec=recording;recording=null;clearTimeout(rec.timer);
 rec.source.disconnect();rec.node.disconnect();rec.node.port.close();rec.gain.disconnect();rec.stream.getTracks().forEach(t=>t.stop());
 A('as-talk').classList.remove('recording');A('as-talk').textContent='🎤 Hold to talk';
 if(rec.frames<rec.rate*.3){status('Recording too short — hold the button while you speak.');return;}
 setBusy(true);status('Transcribing (ElevenLabs)…');
 try{const t=await post('/api/assistant/transcribe',{audio:encodeWav(rec.chunks,rec.rate)});bubble('as-user','🎤 '+t.text,`speech-to-text ${Math.round(t.stt_ms)} ms`);setBusy(false);await send(t.text,{speak:true,voiceMeta:`STT ${Math.round(t.stt_ms)} ms`});}
 catch(error){bubble('as-error',error.message);status('Ready.');setBusy(false);}
}
const talk=A('as-talk');
talk.addEventListener('pointerdown',e=>{e.preventDefault();talk.setPointerCapture(e.pointerId);startRecording();});
talk.addEventListener('pointerup',stopRecording);talk.addEventListener('pointercancel',stopRecording);
talk.addEventListener('keydown',e=>{if(e.code==='Space'&&!e.repeat){e.preventDefault();startRecording();}});
talk.addEventListener('keyup',e=>{if(e.code==='Space'){e.preventDefault();stopRecording();}});
A('as-form').addEventListener('submit',e=>{e.preventDefault();const text=A('as-input').value.trim();if(!text)return;A('as-input').value='';send(text);});
A('as-reset').addEventListener('click',async()=>{try{await post('/api/assistant/reset',{});A('as-log').replaceChildren();bubble('as-bot','New conversation. Start and destination were forgotten; building changes you confirmed stay in place.');}catch(error){bubble('as-error',error.message);}});
for(const [text,hint] of EXAMPLES){const b=document.createElement('button');b.type='button';b.textContent=text;b.title=hint;
 b.addEventListener('click',async()=>{if(busyNow)return;if(data?.building?.id!==DEMO_BUILDING){applying=true;try{if(typeof selectBuilding==='function')await selectBuilding(DEMO_BUILDING);else{A('building').value=DEMO_BUILDING;await loadBuilding();}}finally{applying=false;}touched.start=touched.target=false;}send(text);});A('as-examples').append(b);}
(async()=>{
 try{const r=await fetch('/api/assistant/session');session=await r.json();}catch{session={live:false,voice:false};}
 A('as-mode').textContent=session.live?`live · ${session.model} · effort ${session.effort}${session.voice?' · voice on':' · voice off'}`:'offline · no Claude key (map tools still work)';
 A('as-speak').checked=!!session.voice;A('as-speak').disabled=!session.voice;A('as-talk').disabled=!session.voice;
 bubble('as-bot',session.live?'Hi! Ask me where something is, how to get somewhere, or what changes if a door or lift is closed. You can also click rooms on the map.':'The assistant is offline (no API key in this server). All map and benchmark tools still work.');
})();
})();
