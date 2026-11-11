import test from 'node:test';
import assert from 'node:assert/strict';
import {dimensionPlan} from '../src/layout.js';
import {plan2d,flatEdgePath,flatCounts,FLAT_METRICS} from '../src/flat.js';
import {model} from '../tools/load-model.mjs';

test('every view keeps all nodes in the 2D plan',()=>{
 for(const view of model.views){
  const plan=plan2d(model,view);
  assert.equal(Object.keys(plan.nodes).length,view.nodeIds.length,view.id);
  for(const id of view.nodeIds)assert.ok(plan.nodes[id],`${view.id} missing ${id}`);
 }
});

test('2D plan is deterministic and never invents coordinates',()=>{
 for(const view of model.views){
  const a=plan2d(model,view),b=plan2d(model,view);
  assert.deepEqual(a.nodes,b.nodes,view.id);
  for(const [id,item] of Object.entries(a.nodes)){
   assert.equal(item.category.x,item.node[a.axes.x.field]||'—',`${view.id} x ${id}`);
   assert.equal(item.category.y,item.node[a.axes.y.field]||'—',`${view.id} y ${id}`);
  }
 }
});

test('2D and 3D claim exactly the same axis fields',()=>{
 for(const view of model.views){
  const flat=plan2d(model,view),spatial=dimensionPlan(model,view);
  for(const axis of ['x','y','z'])assert.equal(flat.axes[axis].field,spatial.axes[axis].field,`${view.id} ${axis}`);
  assert.equal(flat.depth,spatial.depth,view.id);
  assert.deepEqual(flat.columns,spatial.effective.x?spatial.axes.x.values:['—'],view.id);
  assert.deepEqual(flat.rows,spatial.effective.y?spatial.axes.y.values:['—'],view.id);
 }
});

test('Z is demoted to a labelled group, never faked as a spatial axis',()=>{
 for(const view of model.views){
  const plan=plan2d(model,view);
  assert.equal(plan.zDemoted,plan.axes.z.size>1,view.id);
  if(plan.zDemoted)assert.ok(plan.zValues.length>1,view.id);
 }
});

test('2D boxes never overlap and stay inside the canvas',()=>{
 for(const view of model.views){
  const plan=plan2d(model,view);
  const boxes=Object.values(plan.nodes);
  for(const box of boxes){
   assert.ok(box.x>=0&&box.y>=0,`${view.id} ${box.id} origin`);
   assert.ok(box.x+box.w<=plan.width+0.001,`${view.id} ${box.id} overflows width`);
   assert.ok(box.y+box.h<=plan.height+0.001,`${view.id} ${box.id} overflows height`);
   assert.equal(box.w,FLAT_METRICS.nodeW);
  }
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
   const a=boxes[i],b=boxes[j];
   const disjoint=a.x+a.w<=b.x||b.x+b.w<=a.x||a.y+a.h<=b.y||b.y+b.h<=a.y;
   assert.ok(disjoint,`${view.id} overlap ${a.id}/${b.id}`);
  }
 }
});

test('collapsed axis collapses to a single column or row instead of empty space',()=>{
 const view={nodeIds:['a','b'],dimensions:{x:{field:'environment'},y:{field:'layer'},z:{field:'runtime'}}};
 const sample={meta:{language:'zh-CN'},nodes:[
  {id:'a',environment:'dev',layer:'service',runtime:'node'},
  {id:'b',environment:'prod',layer:'service',runtime:'node'}]};
 const plan=plan2d(sample,view);
 assert.deepEqual(plan.columns,['dev','prod']);
 assert.deepEqual(plan.rows,['—']);
 assert.equal(plan.zDemoted,false);
 assert.equal(plan.nodes.a.y,plan.nodes.b.y);
});

test('flat edges use orthogonal routing that stays between the two boxes',()=>{
 const from={x:0,y:0,w:172,h:46},to={x:400,y:300,w:172,h:46};
 const p=flatEdgePath(from,to);
 assert.match(p.d,/^M [\d.-]+ [\d.-]+ H [\d.-]+ V [\d.-]+ H [\d.-]+$/,'not orthogonal');
 assert.ok(Number.isFinite(p.mid.x)&&Number.isFinite(p.mid.y));
 assert.ok(p.d.includes(String(from.x+from.w)),'does not leave from the source border');
 assert.ok(p.d.includes(String(to.x-9)),'does not stop at the target border');
});

test('band and lane headers carry real counts',()=>{
 for(const view of model.views){
  const plan=plan2d(model,view);
  const counts=flatCounts(plan);
  assert.equal(counts.columns.reduce((a,b)=>a+b,0),view.nodeIds.length,view.id);
  assert.equal(counts.rows.reduce((a,b)=>a+b,0),view.nodeIds.length,view.id);
  assert.equal(counts.columns.length,plan.columns.length,view.id);
  assert.equal(counts.rows.length,plan.rows.length,view.id);
 }
});

test('Z becomes containment groups that actually hold their nodes',()=>{
 const plan=plan2d(model,model.views[0]);
 assert.ok(plan.zDemoted,'sample view should demote Z');
 let grouped=0;
 for(const cell of plan.cells.values())for(const group of cell.groups){
  if(!group.showHeader)continue;
  grouped++;
  for(const item of group.nodes){
   const box=plan.nodes[item.node.id];
   assert.ok(box.x>=group.x&&box.x+box.w<=group.x+group.w,`${item.node.id} escapes its group`);
   assert.ok(box.y>=group.y&&box.y+box.h<=group.y+group.h,`${item.node.id} escapes its group`);
  }
 }
 assert.ok(grouped>0,'no Z group was drawn');
});

test('axisMeta reports each dimension role and information gain',()=>{
 const plan=plan2d(model,model.views[0]);
 assert.deepEqual(plan.axisMeta.map(m=>m.axis),['x','y','z']);
 assert.equal(plan.axisMeta[0].role,'column');
 assert.equal(plan.axisMeta[1].role,'row');
 for(const m of plan.axisMeta){
  assert.equal(m.field,plan.axes[m.axis].field);
  assert.ok(Number.isFinite(m.incremental));
  assert.ok(m.size>=1&&m.complete<=m.total);
 }
});