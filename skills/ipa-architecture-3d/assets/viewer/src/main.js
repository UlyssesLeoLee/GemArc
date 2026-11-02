import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import model from '../architecture.json';
import config from '../viewer-config.json';
import {DIM_FIELDS,AXIS_COLORS,dimensionPlan,crossingReport,NO_VALUE} from './layout.js';
import {plan2d} from './flat.js';
import {drawFlat} from './flat-view.js';
import {getTranslation} from './i18n.js';
import {RIGS,RIG_IDS,QUESTION_TEXT,rigAssessment,cameraPlacement,rigMatrix} from './rigs.js';
import {blastRadius,shortestPath,trustChain} from './graph.js';
import {AxisGizmo} from './gizmo.js';
import './style.css';

const lang=config.language||'zh-CN';
const tr=getTranslation(lang);
const $=id=>document.getElementById(id);
const text=(k)=>tr[k]||k;
const qtext=k=>(QUESTION_TEXT[k]||{})[lang]||k;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodeMap=new Map(model.nodes.map(n=>[n.id,n]));
const state={view:0,axes:null,selected:null,showEdges:true,showLayers:true,projection:'iso',mode:'3d',search:'',filters:{x:null,y:null,z:null},renderReady:false,animationStart:0,animateMs:860,impact:null};
const classColors={actor:0xf2c17e,external:0xf2c17e,system:0x76cfe3,gateway:0x76cfe3,service:0x76cfe3,module:0x76cfe3,process:0x9baff4,runtime:0x9baff4,plugin:0x9baff4,database:0x70dbb9,storage:0x70dbb9,data:0x70dbb9,security:0xf2c17e,operations:0x70dbb9};
const sceneContainer=$('scene');
let renderer,scene,perspCamera,orthoCamera,activeCamera,controls,raycaster,canvasEl;
let nodesGroup,edgesGroup,axesGroup,layerGroup;
let layout,report,animation=0,flatPlan=null;
let gizmo=null;
const nodeObjs=new Map(),edgeObjs=new Map();
const disposables=[];
function uiInit(){
 document.documentElement.lang=lang;
 for(const el of document.querySelectorAll('[data-t]')){const key=el.dataset.t;el.textContent=text(key)}
 $('language-pill').textContent=({'zh-CN':'简体中文','ja-JP':'日本語','en-US':'English'})[lang]||lang;
 $('system-title').textContent=model.meta.system;
 $('model-tag').textContent=text(model.meta.evidenceState);
 $('model-tag').dataset.state=model.meta.evidenceState||'';
 // Design intent lives in the model but has no place in space; IPA review needs it
 // stated, so it gets its own surface rather than being left in unused JSON fields.
 const list=v=>Array.isArray(v)?v.filter(Boolean).join(' · '):(v||text('unknown'));
 $('intent-scope').textContent=model.meta.scope||text('unknown');
 $('intent-constraints').textContent=list(model.meta.constraints);
 $('intent-decisions').textContent=list(model.meta.decisions);
 $('intent-quality').textContent=list(model.meta.qualityAttributes);
 $('intent-sources').textContent=list(model.meta.sources);
 $('search').placeholder=text('search');
 $('view-count').textContent=`${model.views.length} Views`;
 const nav=$('view-nav');
 model.views.forEach((view,i)=>{
  const b=document.createElement('button');b.type='button';b.className='nav-btn';b.dataset.view=String(i);
  const num=document.createElement('span');num.className='nav-no';num.textContent=String(i+1).padStart(2,'0');
  const name=document.createElement('span');name.className='nav-label';name.textContent=view.title;
  b.append(num,name);b.onclick=()=>setView(i);nav.append(b);
 });
 for(const axis of ['x','y','z']){
  const el=$(`axis-${axis}`);
  for(const field of DIM_FIELDS){const opt=document.createElement('option');opt.value=field;opt.textContent=`${tr.fieldLabel[field]||field} · ${field}`;el.add(opt)}
  el.onchange=()=>{
   const attempt={...state.axes,[axis]:{field:el.value}};
   try{dimensionPlan(model,model.views[state.view],attempt);state.axes=attempt;state.filters={x:null,y:null,z:null};activateLayout(false)}
   catch(error){window.alert(error.message==='DIMENSIONS_DUPLICATE'?text('duplicateAxis'):text('invalidAxis'));el.value=state.axes[axis].field;}
  };
 }
 $('reset-axes').onclick=()=>{state.axes=structuredClone(model.views[state.view].dimensions);state.filters={x:null,y:null,z:null};activateLayout(false)};
 $('edge-toggle').onclick=()=>{state.showEdges=!state.showEdges;syncToggles()};
 $('layers-toggle').onclick=()=>{state.showLayers=!state.showLayers;syncToggles();layerGroup.visible=state.showLayers};
 $('fit').onclick=()=>fitCamera();
 $('search').oninput=e=>{state.search=e.target.value;renderList()};
 // The inspector's markup is re-rendered on selection, so delegate its actions.
 $('detail').addEventListener('click',e=>{
  const btn=e.target.closest('[data-impact]');
  if(!btn)return;
  if(state.impact){clearImpact();return;}
  focusImpact(btn.dataset.impact);
 });
 for(const axis of ['x','y','z'])$(`axis-${axis}-filter`).onchange=e=>{state.filters[axis]=e.target.value||null;applyFocus()};
 document.querySelectorAll('[data-mode]').forEach(btn=>btn.onclick=()=>setMode(btn.dataset.mode));
 syncToggles();syncCameraButtons();
}
function syncToggles(){
 for(const [id,enabled,key] of [['edge-toggle',state.showEdges,'edges'],['layers-toggle',state.showLayers,'layers']]){
  const el=$(id);el.setAttribute('aria-pressed',String(enabled));el.textContent=`${text(key)}：${text(enabled?'show':'hide')}`;
 }
}
function labelTexture(label,{color='#e1f3ff',background='rgba(13,31,48,0.94)',font=55}={}){
 const c=document.createElement('canvas');c.width=850;c.height=154;const ctx=c.getContext('2d');
 ctx.clearRect(0,0,850,154);ctx.fillStyle=background;ctx.fillRect(0,12,850,129);
 ctx.strokeStyle='#52718b';ctx.lineWidth=3;ctx.strokeRect(1.5,13.5,847,126);
 ctx.font=`600 ${font}px system-ui, "Noto Sans SC", "Yu Gothic",sans-serif`;
 let line=String(label||'');while(line.length>2&&ctx.measureText(line).width>800)line=line.slice(0,-2)+'…';
 ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle=color;ctx.fillText(line,425,77);
 const tex=new THREE.CanvasTexture(c);tex.colorSpace=THREE.SRGBColorSpace;
 return tex;
}
function makeText(label,scale=3.0,opts={}){
 const texture=labelTexture(label,opts);const material=new THREE.SpriteMaterial({map:texture,transparent:true,depthWrite:false});
 const sprite=new THREE.Sprite(material);sprite.scale.set(scale,scale*154/850,1);
 sprite.userData.dispose=()=>{texture.dispose();material.dispose()};return sprite;
}
function buildScene(){
 scene=new THREE.Scene();scene.background=new THREE.Color(0x091929);scene.fog=new THREE.FogExp2(0x091929,.0022);
 const width=Math.max(1,sceneContainer.clientWidth),height=Math.max(1,sceneContainer.clientHeight);
 renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
 renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.setSize(width,height);
 renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;
 renderer.toneMappingExposure=1.16;sceneContainer.append(renderer.domElement);canvasEl=renderer.domElement;
 perspCamera=new THREE.PerspectiveCamera(40,width/height,.2,2000);
 orthoCamera=new THREE.OrthographicCamera(-16*width/height,16*width/height,16,-16,.1,2000);
 activeCamera=perspCamera;controls=new OrbitControls(activeCamera,renderer.domElement);
 controls.enableDamping=true;controls.dampingFactor=.09;controls.minDistance=5;controls.maxDistance=700;
 raycaster=new THREE.Raycaster();scene.add(new THREE.HemisphereLight(0xbad9ed,0x112038,2.5));
 const key=new THREE.DirectionalLight(0xedfaff,2.5);key.position.set(21,44,25);scene.add(key);
 const fill=new THREE.DirectionalLight(0x77b3d8,1);fill.position.set(-30,13,-14);scene.add(fill);
 nodesGroup=new THREE.Group();edgesGroup=new THREE.Group();axesGroup=new THREE.Group();layerGroup=new THREE.Group();scene.add(axesGroup,layerGroup,edgesGroup,nodesGroup);
 for(const node of model.nodes)makeNode(node);
 for(const edge of model.edges)makeEdge(edge);
 renderer.domElement.addEventListener('pointerdown',e=>{renderer.domElement.dataset.pointer=`${e.clientX},${e.clientY}`});
 renderer.domElement.addEventListener('pointerup',onPick);
 window.addEventListener('resize',resize);
 // The gizmo lives in its own scene with its own camera, so it never clips against the
 // main scene's near/far planes and keeps a constant on-screen size at any zoom.
 gizmo=new AxisGizmo({host:$('gizmo-host'),size:112,lang,
  onPick:(axis,positive)=>alignCameraToAxis(axis,positive)});
 state.renderReady=true;requestAnimationFrame(frame);
}
/** Clicking an axis ball swings the view onto that axis -- the gizmo is a navigation
 *  control, not decoration. It lands on a DECLARED rig whose viewing ray is that axis,
 *  so the reader never ends up in an unnamed angle. If no rig hides this axis, the
 *  oblique camera is aimed straight down it instead. */
function alignCameraToAxis(axis,positive){
 const match=RIG_IDS.map(id=>RIGS[id]).find(r=>r.roles&&r.roles.hidden===axis);
 if(match){setProjection(match.id);return;}
 const sign=positive?1:-1;
 const oblique={id:'axis-align',kind:'iso',orthographic:false,dir:[.9,.9,.9],up:[0,1,0]};
 oblique.dir[axisAxisIndex(axis)]=sign*1.6;
 const aspect=sceneContainer.clientWidth/Math.max(1,sceneContainer.clientHeight);
 const place=cameraPlacement(oblique,layout,layout.bounds,aspect);
 state.projection=oblique.id;
 activeCamera=perspCamera;controls.object=perspCamera;
 perspCamera.position.set(...place.position);perspCamera.up.set(...place.up);
 perspCamera.lookAt(...place.center);
 perspCamera.near=.1;perspCamera.far=Math.max(1500,place.distance*4);perspCamera.updateProjectionMatrix();
 controls.target.set(...place.center);controls.update();
}
function axisAxisIndex(axis){return axis==='x'?0:axis==='y'?1:2}
function makeNode(node){
 const tint=classColors[node.kind]||0xa1bdde;
 const group=new THREE.Group();
 const body=new THREE.Mesh(new THREE.BoxGeometry(2.65,1.05,1.18),new THREE.MeshStandardMaterial({color:0x29475d,metalness:.08,roughness:.75,emissive:0x0b2438,emissiveIntensity:.35}));
 body.userData.nodeId=node.id;group.add(body);
 const led=new THREE.Mesh(new THREE.BoxGeometry(2.68,.11,1.2),new THREE.MeshBasicMaterial({color:tint}));led.position.y=.53;group.add(led);
 const name=makeText(node.name,3.22,{color:'#ecfaff'});name.position.y=1.03;group.add(name);
 const sub=makeText(node.id,1.95,{font:46,color:'#9cc1da',background:'rgba(13,31,48,.72)'});sub.position.y=-.83;group.add(sub);
 group.position.set(0,-30,0);group.scale.setScalar(.001);group.visible=false;
 nodesGroup.add(group);nodeObjs.set(node.id,{group,body,node,start:new THREE.Vector3(),goal:new THREE.Vector3(),fromScale:0,toScale:0,name,sub});
}
function makeEdge(edge){
 const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3()]),new THREE.LineBasicMaterial({color:0x65accf,transparent:true,opacity:.33,depthWrite:false}));
 const cone=new THREE.Mesh(new THREE.ConeGeometry(.15,.37,8),new THREE.MeshBasicMaterial({color:0x65accf,transparent:true,opacity:.64,depthWrite:false}));
 line.frustumCulled=false;edgesGroup.add(line,cone);edgeObjs.set(edge.id,{edge,line,cone,active:false});
}
function clearVisual(group){
 while(group.children.length){const child=group.children[0];group.remove(child);child.traverse(obj=>{
  if(obj.userData.dispose)obj.userData.dispose();if(obj.geometry)obj.geometry.dispose();
  if(obj.material){for(const m of Array.isArray(obj.material)?obj.material:[obj.material])m.dispose()}
 })}
}
function axisCoordinates(axis){
 const a=layout.axes[axis];if(!layout.effective[axis])return [{category:NO_VALUE,coordinate:0}];return a.values.map((category,i)=>({category,coordinate:(i-(a.size-1)/2)*layout.stride[axis]}));
}
function rebuildScaffold(){
 clearVisual(axesGroup);clearVisual(layerGroup);
 const b=layout.bounds;
 const xAxis=axisCoordinates('x'),yAxis=axisCoordinates('y'),zAxis=axisCoordinates('z');
 const minY=layout.axes.y.size===1?0:0;
 const floor=new THREE.Mesh(new THREE.PlaneGeometry(b.maxX-b.minX+1,b.maxZ-b.minZ+1),new THREE.MeshBasicMaterial({color:0x14344b,transparent:true,opacity:.25,side:THREE.DoubleSide,depthWrite:false}));floor.rotation.x=-Math.PI/2;floor.position.set((b.minX+b.maxX)/2,minY-.75,(b.minZ+b.maxZ)/2);axesGroup.add(floor);
 const grid=new THREE.GridHelper(Math.max(b.maxX-b.minX,b.maxZ-b.minZ)+9,Math.min(36,Math.max(8,layout.axes.x.size*layout.axes.z.size*2)),0x2b7388,0x224258);grid.position.y=minY-.72;grid.material.transparent=true;grid.material.opacity=.34;axesGroup.add(grid);
 const addLine=(from,to,color,opacity=.52)=>{
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...from),new THREE.Vector3(...to)]),new THREE.LineBasicMaterial({color,transparent:true,opacity,depthWrite:false}));axesGroup.add(line);
 };
 for(const tick of xAxis){
  const l=makeText(tick.category,Math.max(2.9,Math.min(5.2,layout.stride.x*.79)),{color:'#79dcea'});l.position.set(tick.coordinate,-1.2,b.minZ-1.4);axesGroup.add(l);
  addLine([tick.coordinate,-.68,b.minZ],[tick.coordinate,-.68,b.maxZ],AXIS_COLORS.x,.22);
 }
 for(const tick of zAxis){
  const l=makeText(tick.category,Math.max(2.7,Math.min(4.2,layout.stride.z*.66)),{color:'#c6b6ff'});l.position.set(b.minX-2,-1.1,tick.coordinate);axesGroup.add(l);
  addLine([b.minX,-.68,tick.coordinate],[b.maxX,-.68,tick.coordinate],AXIS_COLORS.z,.22);
 }
 for(const tick of yAxis){
  const y=layout.axes.y.size===1?0:yAxis.indexOf(tick)*layout.stride.y;
  const label=makeText(tick.category,3.4,{color:'#ffd398'});label.position.set(b.minX-2.5,y+.15,b.minZ-.5);axesGroup.add(label);
  const plane=new THREE.Mesh(new THREE.PlaneGeometry(b.maxX-b.minX,b.maxZ-b.minZ),new THREE.MeshBasicMaterial({color:AXIS_COLORS.y,transparent:true,opacity:.045,depthWrite:false,side:THREE.DoubleSide}));plane.rotation.x=-Math.PI/2;plane.position.set((b.minX+b.maxX)/2,y-.62,(b.minZ+b.maxZ)/2);layerGroup.add(plane);
  addLine([b.minX,y-.62,b.minZ],[b.maxX,y-.62,b.minZ],AXIS_COLORS.y,.4);
 }
 const names=[['x',tr.fieldLabel[layout.axes.x.field]||layout.axes.x.field],['y',tr.fieldLabel[layout.axes.y.field]||layout.axes.y.field],['z',tr.fieldLabel[layout.axes.z.field]||layout.axes.z.field]];
 for(const [axis,title] of names){
  const l=makeText(`${axis.toUpperCase()} · ${title}`,4.4,{color:'#ffffff',background:'rgba(9,24,40,.88)',font:50});
  if(axis==='x')l.position.set((b.minX+b.maxX)/2,-2.9,b.minZ-3.2);
  if(axis==='y')l.position.set(b.minX-2.7,b.maxY+1.4,b.minZ-.5);
  if(axis==='z')l.position.set(b.minX-3,-2.9,(b.minZ+b.maxZ)/2);
  axesGroup.add(l);
 }
 layerGroup.visible=state.showLayers;
}
function setView(index){
 // A blast radius belongs to ONE view's graph; carrying it across would dim nodes the
 // new view never had.
 state.impact=null;
 state.view=index;state.axes=structuredClone(model.views[index].dimensions);state.filters={x:null,y:null,z:null};
 for(const o of nodeObjs.values())o.inImpact=true;
 document.querySelectorAll('.nav-btn').forEach(btn=>btn.classList.toggle('active',Number(btn.dataset.view)===index));
 activateLayout(true);
}
function activateLayout(resetCamera){
 const view=model.views[state.view];
 layout=dimensionPlan(model,view,state.axes);report=crossingReport(model,view);const inView=new Set(view.nodeIds);
 $('view-index').textContent=`${String(state.view+1).padStart(2,'0')} / ${model.views.length} · VIEW`;
 $('view-title').textContent=view.title;$('view-desc').textContent=view.description;
 $('stakeholders').textContent=view.stakeholders.join(' · ');
 $('concerns').textContent=view.concerns.join(' · ');
 $('questions').textContent=view.questions.join(' / ');
 $('depth-metric').textContent=`${layout.depth} / 3`;
 $('node-stat').textContent=view.nodeIds.length;$('edge-stat').textContent=report.edges.length;$('cross-stat').textContent=report.boundaryEdges.length;
 $('scene-summary').textContent=`${view.nodeIds.length} ${text('elements')} · ${report.edges.length} ${text('relations')} · ${layout.depth}/3 ${text('depth')}`;
 for(const axis of ['x','y','z']){
  $(`axis-${axis}`).value=state.axes[axis].field;
  const {size,complete,total}=layout.axes[axis];
  $(`axis-${axis}-count`).textContent=`${size} ${text('categories')} · ${complete}/${total} ${text('complete')}${layout.effective[axis]?'':' · '+text('collapsed')}`;
  const filter=$(`axis-${axis}-filter`);filter.replaceChildren();
  for(const name of ['',...layout.axes[axis].values]){
   const opt=document.createElement('option');opt.value=name;opt.textContent=name||text('allCategories');filter.add(opt);
  }filter.value=state.filters[axis]||'';
 }
 const warning=layout.warnings.length?`${text('dimensionWarn')} ${layout.warnings.map(w=>`${w.axis.toUpperCase()}: ${text(w.issue==='variance'?'noVariance':w.issue==='redundant'?'redundantAxis':'missingValues')}${w.issue==='missing'?' ('+w.count+')':''}`).join(' / ')}`:'';
 const axisSentence=['x','y','z'].map(a=>`${a.toUpperCase()}=${tr.fieldLabel[layout.axes[a].field]||layout.axes[a].field}`).join(' · ');
 $('axis-note').textContent=`${text('axisUsed')}：${axisSentence} · ${text('incremental')} X:${layout.incremental.x} / Y:+${layout.incremental.y} / Z:+${layout.incremental.z} — ${view.reviewIntent||view.description}${warning?' · '+warning:''}`;
 const needs=report.undocumented.length;
 $('review-warning').textContent=needs?`${text('boundaryControls')}：${needs} / ${report.boundaryEdges.length}。`:`${text('boundaries')}：${report.boundaryEdges.length} · ${text('needsReview')}：0`;
 state.animationStart=performance.now();
 for(const [id,item] of nodeObjs){
  const p=layout.positions[id];item.start.copy(item.group.position);item.goal.copy(p?new THREE.Vector3(p.x,p.y,p.z):new THREE.Vector3(item.group.position.x,-30,item.group.position.z));
  item.fromScale=item.group.scale.x;item.toScale=p?1:0;if(p)item.group.visible=true;
 }
 for(const e of edgeObjs.values())e.active=report.edges.some(r=>r.id===e.edge.id);
 if(state.renderReady){rebuildScaffold();if(resetCamera)fitCamera();}
 syncCameraButtons();renderRigNote();
 // The 2D map is planned from the same axes, so it can never claim different fields.
 flatPlan=plan2d(model,view,state.axes);
 applyFocus();renderDetail();
}
/** Tell the reader what the CURRENT angle is for, and which axis it cannot speak about.
 *  This is the difference between four "camera angles" and four review viewpoints. */
function renderRigNote(){
 const el=$('rig-note');if(!el||!layout)return;
 const rig=RIGS[state.projection];
 if(!rig){el.hidden=true;return;}
 // NOTE: `rig.hint` is developer-facing English and deliberately NOT shown to the reader;
 // printing it would mix languages in the UI, which this Skill treats as a P0 defect.
 // The reader-facing explanation is the localized question label plus rigHides/rigCells.
 const parts=[qtext(rig.question)];
 if(rig.roles){
  const h=rig.roles.hidden;
  parts.push(text('rigHides').replace('{axis}',`${h.toUpperCase()}=${tr.fieldLabel[layout.axes[h].field]||layout.axes[h].field}`));
  const mtx=rigMatrix(rig,layout);
  if(mtx&&mtx.cells)parts.push(`${mtx.columns.size}×${mtx.rows.size} ${text('rigCells')}`);
 }
 el.textContent=parts.join(' · ');el.hidden=false;
}
function resize(){if(!renderer)return;const w=Math.max(1,sceneContainer.clientWidth),h=Math.max(1,sceneContainer.clientHeight);
 renderer.setSize(w,h);perspCamera.aspect=w/h;perspCamera.updateProjectionMatrix();orthoCamera.left=-16*w/h;orthoCamera.right=16*w/h;orthoCamera.top=16;orthoCamera.bottom=-16;orthoCamera.updateProjectionMatrix();fitCamera();}
function setProjection(id){
 // Refuse an angle that cannot answer anything for THIS layout, and say why, instead
 // of drawing a confident-looking picture that encodes nothing.
 const rig=RIGS[id];if(!rig||!layout)return;
 const verdict=rigAssessment(rig,layout);
 if(!verdict.valid){
  const r=verdict.reasons;
  const why=r.hiddenAxisIsOnlyUseful
   ? text('rigHidesOnlyAxis').replace('{axis}',r.hiddenAxisField||'')
   : text('rigFlatAxis').replace('{axis}',(r.flatScreenAxes||[]).map(f=>f.axis.toUpperCase()).join('/'));
  showRigNotice(why);return;
 }
 showRigNotice('');
 state.projection=id;
 document.querySelectorAll('[data-camera]').forEach(b=>b.classList.toggle('selected',b.dataset.camera===id));
 controls.object=RIGS[id].orthographic?orthoCamera:perspCamera;
 fitCamera();
 renderRigNote();
}
function showRigNotice(message){
 const el=$('rig-note');if(!el)return;
 el.textContent=message||'';el.hidden=!message;
}
/** Rewrite the camera buttons from the rig table, so a new review angle is added in
 *  one place (rigs.js) and automatically gets a label, a question and an honest
 *  disabled state for the current layout. */
function syncCameraButtons(){
 const host=$('camera-tabs');if(!host)return;
 host.replaceChildren();
 for(const id of RIG_IDS){
  const rig=RIGS[id];
  const b=document.createElement('button');
  b.type='button';b.dataset.camera=id;b.className='ctl ctl-rig'+(id===state.projection?' selected':'');
  b.textContent=qtext(rig.question);
  // The dot colour tells the reader, before clicking, WHICH axis this angle drops.
  if(rig.roles)b.dataset.hiddenAxis=rig.roles.hidden;
  b.title=`${rig.hint} — ${text('rigHides').replace('{axis}',rig.roles?tr.fieldLabel[rig.roles.hidden]||rig.roles.hidden:'')}`;
  if(layout&&!rigAssessment(rig,layout).valid)b.disabled=true;
  b.onclick=()=>setProjection(id);
  host.append(b);
 }
}
/** Reader-facing choice between the real 3D scene and the real 2D map.
 * 2D is an explicit selection, never a silent downgrade of a WebGL failure. */
function setMode(mode){
 state.mode=mode;
 const flat=mode==='2d';
 document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.mode===mode));
 $('flat').hidden=!flat;
 if(canvasEl)canvasEl.hidden=flat;
 $('flat-note').hidden=!flat;
 document.querySelectorAll('[data-camera]').forEach(b=>b.disabled=flat||!state.renderReady);
 $('layers-toggle').disabled=flat;$('fit').disabled=flat;
 if(flat&&state.axes){flatPlan=plan2d(model,model.views[state.view],state.axes)}
 renderFlat();
}
function renderFlat(){
 if(state.mode!=='2d'||!flatPlan||!report)return;
 drawFlat($('flat'),{plan:flatPlan,model,report,state,classColors,tr,text,onSelect:selectNode});
 // Spell out what each axis actually contributed instead of just naming it.
 const roles={column:text('axisRoleColumn'),row:text('axisRoleRow'),group:text('axisRoleGroup'),none:text('flatZNone')};
 const sentence=flatPlan.axisMeta.map(m=>{
  const label=tr.fieldLabel[m.field]||m.field;
  const gain=m.axis==='x'?`${text('incremental')} ${m.incremental}`:`${text('incremental')} +${m.incremental}`;
  return `${m.axis.toUpperCase()}=${label}（${roles[m.role]||m.role}${m.effective?'':' · '+text('collapsed')} · ${m.size} ${text('categories')} · ${gain}）`;
 }).join(' · ');
 $('flat-note').textContent=`${text('flatNote')}：${sentence}`;
}
function fitCamera(){if(!layout||!state.renderReady)return;
 // The rig owns the framing: it decides direction, and guarantees that every
 // orthographic angle frames the SAME box so switching angles changes only what is
 // occluded, never the scale.
 const rig=RIGS[state.projection]||RIGS.iso;
 const aspect=sceneContainer.clientWidth/Math.max(1,sceneContainer.clientHeight);
 const place=cameraPlacement(rig,layout,layout.bounds,aspect);
 const center=new THREE.Vector3(...place.center);
 if(rig.orthographic){
  activeCamera=orthoCamera;
  orthoCamera.position.set(...place.position);
  orthoCamera.up.set(...place.up);
  orthoCamera.lookAt(center);
  orthoCamera.left=-place.halfWidth;orthoCamera.right=place.halfWidth;
  orthoCamera.top=place.halfHeight;orthoCamera.bottom=-place.halfHeight;
  orthoCamera.near=.1;orthoCamera.far=Math.max(1500,place.distance*4);
  orthoCamera.updateProjectionMatrix();
 }else{
  activeCamera=perspCamera;
  perspCamera.position.set(...place.position);
  perspCamera.up.set(...place.up);
  perspCamera.lookAt(center);
  perspCamera.near=.1;perspCamera.far=Math.max(1500,place.distance*4);
  perspCamera.updateProjectionMatrix();
 }
 controls.target.copy(center);controls.update();
}
function onPick(event){if(!layout||!renderer)return;
 const down=(renderer.domElement.dataset.pointer||'').split(',').map(Number);
 if(down.length===2&&Math.hypot(event.clientX-down[0],event.clientY-down[1])>5)return;
 const rect=renderer.domElement.getBoundingClientRect();const mouse=new THREE.Vector2(2*(event.clientX-rect.left)/rect.width-1,1-2*(event.clientY-rect.top)/rect.height);
 raycaster.setFromCamera(mouse,activeCamera);const candidates=[...nodeObjs.values()].filter(o=>o.toScale>0).map(o=>o.body);
 const pick=raycaster.intersectObjects(candidates,false).find(hit=>hit.object.userData.nodeId);
 if(pick)selectNode(pick.object.userData.nodeId);
}
function selectNode(id){state.selected=id;renderDetail();renderList();renderFlat();}
function matchesFocus(id){
 const p=layout?.positions[id];if(!p)return false;
 if(state.impact&&!state.impact.levels.has(id))return false;
 return ['x','y','z'].every(a=>!state.filters[a]||p.category[a]===state.filters[a]);
}
function applyFocus(){
 for(const [id,o] of nodeObjs){const on=matchesFocus(id);o.inFocus=on;}
 renderList();
 const count=model.views[state.view].nodeIds.filter(matchesFocus).length;
 $('scene-summary').textContent=`${count}/${model.views[state.view].nodeIds.length} ${text('elements')} · ${report.edges.length} ${text('relations')} · ${layout.depth}/3 ${text('depth')}`;
 renderFlat();
}
function renderList(){
 const view=model.views[state.view],root=$('nodes');root.replaceChildren();let count=0;
 for(const id of view.nodeIds){const n=nodeMap.get(id);if(!n||!matchesFocus(id))continue;if(state.search&&!`${n.id} ${n.name} ${n.description}`.toLocaleLowerCase().includes(state.search.toLocaleLowerCase()))continue;
  count++;const b=document.createElement('button');b.type='button';b.className='node-button'+(state.selected===id?' active':'');b.onclick=()=>selectNode(id);
  const dot=document.createElement('i');dot.className='kind-dot';dot.style.background='#'+new THREE.Color(classColors[n.kind]||0x9dd1df).getHexString();
  const span=document.createElement('span');span.textContent=n.name;const small=document.createElement('small');small.textContent=n.id;b.append(dot,span,small);root.append(b);
 }
 if(!count){const p=document.createElement('p');p.className='empty-hint';p.textContent=text('noneFound');root.append(p)}
}
function renderDetail(){
 const el=$('detail'),n=state.selected&&nodeMap.get(state.selected);
 if(!n){el.innerHTML=`<div class="empty-hint">◇ ${escape(text('selectedHint'))}</div>`;return}
 const view=model.views[state.view];const active=view.nodeIds.includes(n.id);
 const rows=[[text('type'),n.kind+' · '+n.id],[text('group'),n[state.axes.x.field]||text('unknown')],[text('trust'),n.trustZone||text('unknown')],[text('environment'),n.environment||text('unknown')],[text('verified'),text(n.verification||'unverified')],[text('source'),n.evidence||text('unknown')]];
 const links=report.edges.filter(e=>e.source===n.id||e.target===n.id).map(e=>{
  const other=nodeMap.get(e.source===n.id?e.target:e.source);return `${e.source===n.id?'→':'←'} ${other?.name||'?'} [${e.kind}]`;
 });
 rows.push([text('links'),links.join(' / ')||text('noLinks')]);
 el.innerHTML=`<h3>${escape(n.name)}</h3><p class="detail-description">${escape(n.description)}</p>${active?'':`<p class="detail-description">${escape(text('notInView'))}</p>`}${rows.map(([a,b])=>`<div class="kv"><b>${escape(a)}</b><span>${escape(b)}</span></div>`).join('')}${renderImpact(n,active)}`;
 renderImpactBar();
}
/** Blast radius + trust chain for the selected node, computed over the SAME filtered
 *  graph the renderer draws, so "what breaks if this changes?" can never cite a link
 *  the reader cannot see. */
function renderImpact(n,active){
 if(!active)return '';
 const view=model.views[state.view];
 const impact=blastRadius(model,view,n.id,{maxDepth:3});
 const others=impact.levels.size-1;
 // Where does this node sit relative to the nearest trust boundary?
 let chain=null;
 for(const e of report.edges){
  if(e.source!==n.id&&e.target!==n.id)continue;
  const far=e.source===n.id?e.target:e.source;
  const c=trustChain(model,view,n.id,far);
  if(c&&c.crossesTrust){chain=c;break;}
 }
 const blocks=[];
 blocks.push(`<div class="impact"><div class="impact-head"><span>${escape(text('impact'))}</span>
   <button type="button" class="ctl" data-impact="${escape(n.id)}">${escape(text('impactFocus'))}</button></div>
   <div class="impact-body">${others?`${others} ${escape(text('impactReached'))}`
     :escape(text('impactIsolated'))}</div>`);
 if(chain){
  const zone=`${chain.crossings[0].from} → ${chain.crossings[0].to}`;
  blocks.push(`<div class="impact"><div class="impact-head"><span>${escape(text('trustChain'))}</span>
    <span class="impact-flag ${chain.allVerified?'ok':'warn'}">${escape(chain.allVerified?text('controlsVerified'):text('controlsPending'))}</span></div>
    <div class="impact-body">${escape(zone)} · ${chain.depth} ${escape(text('hops'))}</div></div>`);
 }
 blocks.push('</div>');
 return blocks.join('');
}
function renderImpactBar(){
 const el=$('impact-bar');if(!el)return;
 const impact=state.impact;
 if(!impact){el.hidden=true;el.textContent='';return;}
 const view=model.views[state.view];
 el.hidden=false;
 el.textContent=`${text('impactShowing')} ${impact.levels.size}/${view.nodeIds.length} ${text('elements')}`;
}
function clearImpact(){
 if(!state.impact)return;
 state.impact=null;
 for(const o of nodeObjs.values())o.inImpact=true;
 applyFocus();renderDetail();renderImpactBar();
}
/** Dim everything outside the selected node's blast radius. This is a READING aid,
 *  not an edit: the model, the view definition and the evidence are untouched. */
function focusImpact(id){
 const view=model.views[state.view];
 if(!view.nodeIds.includes(id))return;
 state.impact=blastRadius(model,view,id,{maxDepth:3});
 for(const [nodeId,o] of nodeObjs)o.inImpact=state.impact.levels.has(nodeId);
 applyFocus();renderDetail();renderImpactBar();
}
function frame(now){requestAnimationFrame(frame);
 // While the reader is on the 2D map the WebGL scene is parked; SVG redraws on state change.
 if(state.mode!=='3d'||!renderer)return;
 const fraction=Math.min(1,(now-state.animationStart)/(window.matchMedia('(prefers-reduced-motion: reduce)').matches?1:state.animateMs));
 const ease=1-Math.pow(1-fraction,3);
 for(const [id,o] of nodeObjs){if(!o.group.visible)continue;
  o.group.position.lerpVectors(o.start,o.goal,ease);
  const sz=THREE.MathUtils.lerp(o.fromScale,o.toScale,ease);o.group.scale.setScalar(Math.max(.001,sz));
  const chosen=state.selected===id;o.body.material.emissive.setHex(chosen?0x337489:0x0b2438);
  o.body.material.emissiveIntensity=chosen?.9:.35;
  o.body.material.transparent=true;o.body.material.opacity=o.inFocus?1:.12;
  o.name.material.opacity=o.inFocus?1:0;o.sub.material.opacity=o.inFocus?.9:0;
  if(fraction===1&&o.toScale===0)o.group.visible=false;
 }
 for(const item of edgeObjs.values()){
  const {edge,line,cone,active}=item;
  const show=state.showEdges&&active&&matchesFocus(edge.source)&&matchesFocus(edge.target)&&(!state.selected||edge.source===state.selected||edge.target===state.selected);
  line.visible=cone.visible=show;if(!show)continue;
  const src=nodeObjs.get(edge.source)?.group.position,dst=nodeObjs.get(edge.target)?.group.position;
  if(!src||!dst)continue;
  const delta=dst.clone().sub(src);const unit=delta.length()>.001?delta.normalize():new THREE.Vector3(0,1,0);
  const head=src.clone().addScaledVector(unit,1),tail=dst.clone().addScaledVector(unit,-1);
  const pos=line.geometry.attributes.position;pos.setXYZ(0,head.x,head.y,head.z);pos.setXYZ(1,tail.x,tail.y,tail.z);pos.needsUpdate=true;
  // Dashed confidence is represented in inspector; color here encodes trust-zone crossings.
  const a=nodeMap.get(edge.source),b=nodeMap.get(edge.target);const crossing=a?.trustZone&&b?.trustZone&&a.trustZone!==b.trustZone;
  const color=crossing?0xf6bd76:0x72b7d3;line.material.color.setHex(color);cone.material.color.setHex(color);
  const emphasis=state.selected?.96:.35;line.material.opacity=emphasis;cone.material.opacity=state.selected?.95:.64;
  cone.position.copy(tail);cone.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),unit);
 }
 controls.update();renderer.render(scene,activeCamera);
 // The gizmo mirrors the live camera orientation, so it is only meaningful in 3D.
 if(gizmo){
  gizmo.syncFrom(activeCamera);gizmo.render();
  gizmo.renderer.domElement.style.visibility=state.mode==='2d'?'hidden':'visible';
 }
}
function errorScene(error){
 state.renderReady=false;
 $('webgl-error').hidden=false;
 $('webgl-error').textContent=`${text('errorPrefix')} ${error?.message||''}`;
 console.error('True Three.js 3D unavailable:',error);
 document.querySelectorAll('[data-camera]').forEach(b=>b.disabled=true);
 // Offer the honest alternative, but say plainly that the 3D scene did not start.
 if(state.mode!=='2d')setMode('2d');
 $('webgl-error').textContent=`${text('errorPrefix')} ${error?.message||''} — ${text('flatFallbackNote')}`;
}
uiInit();
try{buildScene();setView(0)}catch(error){errorScene(error);setView(0)}
