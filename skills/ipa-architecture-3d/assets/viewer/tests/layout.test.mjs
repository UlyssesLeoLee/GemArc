import test from 'node:test';
import assert from 'node:assert/strict';
import {dimensionPlan,crossingReport} from '../src/layout.js';
import {model} from '../tools/load-model.mjs';

test('12 architecture views encode three independently discriminative dimensions',()=>{
  assert.equal(model.views.length,12);
  for(const view of model.views){
    const p=dimensionPlan(model,view);
    assert.equal(p.depth,3,`view ${view.id} effective dimensions`);
    assert.ok(p.incremental.x>0 && p.incremental.y>0 && p.incremental.z>0,view.id);
    assert.equal(new Set(Object.keys(p.positions)).size,view.nodeIds.length,view.id);
    assert.equal(new Set(['x','y','z'].map(a=>p.axes[a].field)).size,3,view.id);
  }
});
test('coordinates are deterministic and factual categories',()=>{
  for(const view of model.views){
    const a=dimensionPlan(model,view),b=dimensionPlan(model,view);
    assert.deepEqual(a.positions,b.positions,view.id);
    for(const id of view.nodeIds){
      const node=model.nodes.find(n=>n.id===id);
      assert.ok(node,`node ${id}`);
      for(const axis of ['x','y','z']){
        assert.equal(a.positions[id].category[axis],node[a.axes[axis].field]||'—',`${view.id} ${axis} ${id}`);
      }
    }
  }
});
test('cross-domain edges never refer to nodes outside view',()=>{
  for(const view of model.views){
    const report=crossingReport(model,view); const visible=new Set(view.nodeIds);
    for(const edge of report.edges){
      assert.ok(visible.has(edge.source));assert.ok(visible.has(edge.target));
      assert.ok(view.edgeKinds.includes(edge.kind));
    }
    for(const edge of report.boundaryEdges){
      const a=model.nodes.find(n=>n.id===edge.source),b=model.nodes.find(n=>n.id===edge.target);
      assert.notEqual(a.trustZone,b.trustZone);
    }
  }
});
test('unused Z semantic dimension is collapsed and visibly reported',()=>{
 const view={nodeIds:['a','b'],dimensions:{x:{field:'environment'},y:{field:'layer'},z:{field:'runtime'}}};
 const sample={meta:{language:'zh-CN'},nodes:[
  {id:'a',environment:'dev',layer:'service',runtime:'node'},
  {id:'b',environment:'prod',layer:'service',runtime:'node'}]};
 const p=dimensionPlan(sample,view);
 assert.equal(p.depth,1);
 assert.equal(p.effective.y,false);assert.equal(p.effective.z,false);
 assert.equal(p.positions.a.y,0);assert.equal(p.positions.b.y,0);
 assert.equal(p.positions.a.z,0);assert.equal(p.positions.b.z,0);
 assert.ok(p.warnings.some(w=>w.axis==='z'));
});
test('duplicate axis definition is a hard error',()=>{
 const view=model.views[0];
 const axes=structuredClone(view.dimensions);axes.y.field=axes.x.field;
 assert.throws(()=>dimensionPlan(model,view,axes),/DIMENSIONS_DUPLICATE/);
});
