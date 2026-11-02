import test from 'node:test';
import assert from 'node:assert/strict';
import {RIGS,RIG_IDS,rigAssessment,cameraPlacement,screenAxesFor,rigMatrix} from '../src/rigs.js';
import {dimensionPlan} from '../src/layout.js';
import {model} from '../tools/load-model.mjs';

test('every rig declares its review question and maps all three axes exactly once',()=>{
  for(const id of RIG_IDS){
    const r=RIGS[id];
    assert.ok(r.question,`${id} has no review question`);
    assert.ok(r.hint,`${id} has no explanation`);
    assert.ok(Array.isArray(r.dir)&&r.dir.length===3,`${id} direction`);
    if(r.kind==='iso')continue;
    const {x,y,hidden}=r.roles;
    assert.equal(new Set([x,y,hidden]).size,3,`${id} must use each axis exactly once`);
    for(const axis of [x,y,hidden])assert.ok('xyz'.includes(axis),`${id} bad axis ${axis}`);
  }
});

test('all 12 real views can offer every rig honestly (they all have full depth)',()=>{
  for(const v of model.views){
    const p=dimensionPlan(model,v);
    assert.equal(p.depth,3,`${v.id} should have full depth`);
    for(const id of RIG_IDS){
      const a=rigAssessment(RIGS[id],p);
      assert.equal(a.valid,true,`${v.id}/${id} should be valid, reasons=${JSON.stringify(a.reasons)}`);
    }
  }
});

test('a rig is refused when a screen axis carries no information',()=>{
  // Y is redundant (X already determines it) but Z still discriminates INSIDE each X
  // group, so the scene has genuine depth and only the Y rig is unusable.
  const nodes=[
    {id:'a',layer:'core',trustZone:'int',environment:'prod'},
    {id:'b',layer:'core',trustZone:'int',environment:'dev'},
    {id:'c',layer:'edge',trustZone:'ext',environment:'prod'},
    {id:'d',layer:'edge',trustZone:'ext',environment:'dev'},
  ];
  const m={meta:{language:'zh-CN'},nodes};
  const view={nodeIds:['a','b','c','d'],groupBy:'layer',
    dimensions:{x:{field:'layer'},y:{field:'trustZone'},z:{field:'environment'}}};
  const p=dimensionPlan(m,view);
  assert.equal(p.effective.y,false,'precondition: Y is redundant');
  assert.equal(p.effective.z,true,'precondition: Z still discriminates');
  assert.equal(p.depth,2);
  // plan reads X x Z and hides the flat Y -> still an honest angle.
  const plan=rigAssessment(RIGS.plan,p);
  assert.equal(plan.valid,true,'hiding the flat axis is acceptable');
  // elevation and section both put the flat Y on the screen -> refused.
  assert.equal(rigAssessment(RIGS.elevation,p).valid,false,'elevation shows flat Y');
  assert.equal(rigAssessment(RIGS.section,p).valid,false,'section shows flat Y');
  assert.ok(rigAssessment(RIGS.elevation,p).reasons.flatScreenAxes.some(f=>f.axis==='y'));
});

test('a rig is refused when it would hide the only axis that carries information',()=>{
  // Z is the ONLY useful axis -> any camera projecting along Z would erase the data.
  const nodes=[
    {id:'a',layer:'core',runtime:'r1',environment:'prod'},
    {id:'b',layer:'core',runtime:'r1',environment:'dev'},
  ];
  const m={meta:{language:'zh-CN'},nodes};
  const view={nodeIds:['a','b'],groupBy:'layer',
    dimensions:{x:{field:'layer'},y:{field:'runtime'},z:{field:'environment'}}};
  const p=dimensionPlan(m,view);
  assert.equal(p.effective.z,true,'precondition: Z discriminates');
  const ele=rigAssessment(RIGS.elevation,p); // elevation hides Z
  assert.equal(ele.valid,false,'elevation must refuse when it hides the only useful axis');
  assert.equal(ele.reasons.hiddenAxisIsOnlyUseful,true);
  assert.equal(ele.reasons.hiddenAxisField,'environment');
});

test('the iso rig refuses a flat scene instead of faking a third dimension',()=>{
  // Three distinct fields, but runtime and environment are constant across the nodes,
  // so only X discriminates: the scene is genuinely 2D and an oblique camera would lie.
  const m={meta:{language:'zh-CN'},nodes:[
    {id:'a',layer:'core',runtime:'r1',environment:'prod'},
    {id:'b',layer:'edge',runtime:'r1',environment:'prod'}]};
  const p=dimensionPlan(m,{nodeIds:['a','b'],groupBy:'layer',
    dimensions:{x:{field:'layer'},y:{field:'runtime'},z:{field:'environment'}}});
  assert.equal(p.depth,1,'precondition: only X discriminates');
  const iso=rigAssessment(RIGS.iso,p);
  assert.equal(iso.valid,false,'iso must refuse a flat scene');
  assert.equal(iso.reasons.tooFlat,true);
  assert.equal(iso.reasons.reason,'spatial-need-2d');
});

test('camera placement is deterministic and ortho rigs share one scale',()=>{
  const p=dimensionPlan(model,model.views[0]);
  const b=p.bounds;
  const a1=cameraPlacement(RIGS.plan,p,b,1.6);
  const a2=cameraPlacement(RIGS.plan,p,b,1.6);
  assert.deepEqual(a1,a2,'deterministic');
  const e=cameraPlacement(RIGS.elevation,p,b,1.6);
  const s=cameraPlacement(RIGS.section,p,b,1.6);
  assert.equal(a1.halfHeight,e.halfHeight,'ortho rigs must share vertical scale');
  assert.equal(a1.halfHeight,s.halfHeight,'ortho rigs must share vertical scale');
  // Ortho cameras sit far enough out that the near plane cannot clip the scene.
  for(const c of [a1,e,s]){
    const dist=Math.hypot(...c.position.map((v,i)=>v-c.center[i]));
    assert.ok(dist>1,`ortho camera too close: ${dist}`);
  }
});

test('every rig returns a finite distance so near/far can never become NaN',()=>{
  // Regression: an ortho rig that omits `distance` makes the caller compute
  // Math.max(1500, undefined*4) === NaN. Three.js then stores far=null and renders a
  // COMPLETELY BLANK frame with no GL error -- the failure is invisible to unit tests
  // that only check geometry, and only shows up as a black canvas in a browser.
  for(const view of model.views){
    const p=dimensionPlan(model,view);
    for(const id of RIG_IDS){
      const place=cameraPlacement(RIGS[id],p,p.bounds,1.7);
      assert.equal(typeof place.distance,'number',`${view.id}/${id} has no numeric distance`);
      assert.ok(Number.isFinite(place.distance),`${view.id}/${id} distance not finite`);
      assert.ok(place.distance>0,`${view.id}/${id} distance must be positive`);
      // Exactly what fitCamera() does with it -- this is the value that broke.
      const far=Math.max(1500,place.distance*4);
      assert.ok(Number.isFinite(far)&&far>0,`${view.id}/${id} yields a non-finite far plane`);
      for(const axis of ['halfHeight','halfWidth']){
        if(place[axis]===undefined)continue;
        assert.ok(Number.isFinite(place[axis])&&place[axis]>0,`${view.id}/${id} ${axis} invalid`);
      }
      for(let i=0;i<3;i++){
        assert.ok(Number.isFinite(place.position[i]),`${view.id}/${id} position[${i}] not finite`);
        assert.ok(Number.isFinite(place.center[i]),`${view.id}/${id} center[${i}] not finite`);
      }
    }
  }
});

test('screen axes never contradict the rig definition',()=>{
  for(const id of RIG_IDS){
    const r=RIGS[id];
    const p=dimensionPlan(model,model.views[0]);
    const s=screenAxesFor(r,p);
    if(r.kind==='iso'){assert.deepEqual(s,{x:'x',y:'y',z:'z'});continue;}
    assert.equal(s.x,r.roles.x,id);
    assert.equal(s.y,r.roles.y,id);
    assert.equal(s.z,r.roles.hidden,id);
  }
});

test('rig matrix reports real counts and never invents a cell grid',()=>{
  const p=dimensionPlan(model,model.views[0]);
  const mtx=rigMatrix(RIGS.plan,p);
  assert.equal(mtx.columns.axis,'x');
  assert.equal(mtx.rows.axis,'z');
  assert.ok(mtx.columns.size>=1&&mtx.rows.size>=1);
  assert.equal(mtx.cells,mtx.columns.size*mtx.rows.size);
  // A collapsed screen axis must not claim a cell grid.
  const flat=dimensionPlan({meta:{language:'zh-CN'},nodes:[
      {id:'a',layer:'c',runtime:'r1',environment:'prod'},
      {id:'b',layer:'c',runtime:'r1',environment:'prod'}]},
    {nodeIds:['a','b'],groupBy:'layer',
     dimensions:{x:{field:'layer'},y:{field:'runtime'},z:{field:'environment'}}});
  const none=rigMatrix(RIGS.plan,flat);
  assert.equal(none.cells,0,'no cell grid when an axis does not discriminate');
});
