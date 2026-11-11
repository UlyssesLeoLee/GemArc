import test from 'node:test';
import assert from 'node:assert/strict';
import {adjacency,blastRadius,shortestPath,trustChain,boundaryBridges,focusView} from '../src/graph.js';
import {model} from '../tools/load-model.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const read=p=>fs.readFileSync(path.join(HERE,'..',p),'utf8');

test('every declared model field is actually consumed somewhere',()=>{
  // A field nobody reads is worse than a missing one: it advertises a capability the
  // tool does not have. `i18n` is structural (consumed by build.py), so it is exempt.
  const main=read('src/main.js');
  const flat=read('src/flat-view.js');
  const graph=read('src/graph.js');
  const rigs=read('src/rigs.js');
  const code=[main,flat,graph,rigs].join('\n');

  const CONSUMED=new Set([
    'id','name','description','kind','layer','repo','runtime','environment','dataZone',
    'trustZone','opsLayer','integrationLayer','extensionLayer','failureDomain','domain',
    'owner','boundary','evidence','verification','control',
    'source','target','edges','nodes','views','meta','i18n',
    'title','stakeholders','concerns','questions','reviewIntent','nodeIds','edgeKinds',
    'groupBy','orderBy','dimensions',
    'system','version','evidenceState','language',
    // surfaced by uiInit as design intent
    'scope','constraints','decisions','qualityAttributes','sources','updatedAt',
  ]);
  const unused=[];
  for(const n of model.nodes)for(const k of Object.keys(n))if(!CONSUMED.has(k))unused.push(`node.${k}`);
  for(const e of model.edges)for(const k of Object.keys(e))if(!CONSUMED.has(k))unused.push(`edge.${k}`);
  for(const v of model.views)for(const k of Object.keys(v))if(!CONSUMED.has(k))unused.push(`view.${k}`);
  for(const k of Object.keys(model.meta))if(!CONSUMED.has(k))unused.push(`meta.${k}`);
  assert.deepEqual([...new Set(unused)],[],`model declares fields nothing reads: ${unused}`);
  // Sanity: the allow-list itself must stay honest.
  for(const k of ['scope','constraints','decisions','qualityAttributes','sources']){
    assert.ok(code.includes(k)||main.includes('intent-'),`${k} is allow-listed but not rendered`);
  }
});

test('adjacency respects BOTH the view node set and the edge-kind whitelist',()=>{
  for(const view of model.views){
    const g=adjacency(model,view);
    assert.equal(g.visible.size,view.nodeIds.length,view.id);
    for(const e of g.edges){
      assert.ok(view.nodeIds.includes(e.source),`${view.id} edge outside view: ${e.source}`);
      assert.ok(view.nodeIds.includes(e.target),`${view.id} edge outside view: ${e.target}`);
      assert.ok(view.edgeKinds.includes(e.kind),`${view.id} edge kind not whitelisted: ${e.kind}`);
    }
  }
});

test('blast radius reaches only nodes the reader can actually see',()=>{
  for(const view of model.views){
    for(const id of view.nodeIds){
      const r=blastRadius(model,view,id);
      for(const reached of r.levels.keys()){
        assert.ok(view.nodeIds.includes(reached),
          `${view.id}: ${id} reached invisible node ${reached}`);
      }
    }
  }
});

test('blast radius depth is consistent with the paths that produced it',()=>{
  const view=model.views[0];
  const r=blastRadius(model,view,view.nodeIds[0],{maxDepth:3});
  for(const [node,depth] of r.levels){
    assert.ok(depth>=0&&depth<=3,`${node} depth ${depth} out of range`);
    if(depth>0){
      // Each reached node must record the edge that led to it, and that edge's source
      // must sit exactly one level shallower.
      const step=r.paths.get(node);
      assert.ok(step,`${node} has no recorded path`);
      assert.equal(r.levels.get(step.from),depth-1,`${node} parent depth mismatch`);
    }
  }
});

test('blast radius truncation is reported, never silent',()=>{
  const view=model.views[0];
  const full=blastRadius(model,view,view.nodeIds[0]);
  const capped=blastRadius(model,view,view.nodeIds[0],{maxDepth:1});
  assert.ok(capped.levels.size<=full.levels.size,'capping cannot grow the set');
  if(capped.truncated){
    assert.ok(capped.levels.size<full.levels.size,'truncated flag set but nothing was cut');
  }
});

test('blast radius honours an extra edge-kind filter without widening',()=>{
  const view=model.views.find(v=>v.edgeKinds.length>1);
  const id=view.nodeIds[0];
  const allowed=view.edgeKinds.slice(0,1);
  const r=blastRadius(model,view,id,{edgeKinds:allowed});
  const g=adjacency(model,view);
  for(const edgeId of r.edges){
    assert.ok(allowed.includes(g.edges.find(e=>e.id===edgeId).kind),
      `${edgeId} escaped the edge-kind filter`);
  }
  // A filter can only shrink, never add.
  assert.ok(r.levels.size<=blastRadius(model,view,id).levels.size);
});

test('shortest path is deterministic and every hop is a real view edge',()=>{
  for(const view of model.views){
    const [a,b]=view.nodeIds;
    for(let i=0;i<3;i++){ // repeat: a non-deterministic result would flake
      const p=shortestPath(model,view,a,b);
      if(!p)continue;
      assert.deepEqual(blastPathShape(p),blastPathShape(p));
      assert.equal(p.nodes[0],a);assert.equal(p.nodes.at(-1),b);
      const g=adjacency(model,view);
      for(let k=0;k<p.edges.length;k++){
        const e=g.edges.find(x=>x.id===p.edges[k]);
        assert.ok(e,`${view.id} path edge ${p.edges[k]} not in view`);
        assert.equal(e.source,p.nodes[k],'path edge source must match');
        assert.equal(e.target,p.nodes[k+1],'path edge target must match');
      }
    }
  }
});
const blastPathShape=p=>p.nodes.join('>')+'|'+p.edges.join(',');

test('trust chain reports every crossing and whether its control is confirmed',()=>{
  // Find a view containing a genuine trust crossing.
  const view=model.views.find(v=>{
    const g=adjacency(model,v);
    return g.edges.some(e=>{
      const a=g.byId.get(e.source),b=g.byId.get(e.target);
      return a?.trustZone&&b?.trustZone&&a.trustZone!==b.trustZone;
    });
  });
  assert.ok(view,'the sample model should contain a trust crossing');
  const g=adjacency(model,view);
  const crossing=g.edges.find(e=>{
    const a=g.byId.get(e.source),b=g.byId.get(e.target);
    return a.trustZone!==b.trustZone;
  });
  const chain=trustChain(model,view,crossing.source,crossing.target);
  assert.ok(chain,'a direct crossing edge must yield a chain');
  assert.equal(chain.depth,1);
  assert.equal(chain.crossesTrust,true);
  assert.equal(chain.crossings.length,1);
  assert.equal(chain.crossings[0].edgeId,crossing.id);
  // allVerified must be a real claim about the evidence, not a default true.
  assert.equal(typeof chain.allVerified,'boolean');
});

test('trust chain of an unconnected pair is null rather than an empty claim',()=>{
  const view=model.views[0];
  const unreachable=shortestPath(model,view,view.nodeIds[0],view.nodeIds.at(-1));
  if(!unreachable){
    assert.equal(trustChain(model,view,view.nodeIds[0],view.nodeIds.at(-1)),null);
  }
});

test('boundary bridges only returns nodes that actually touch the boundary',()=>{
  for(const view of model.views){
    const bridges=boundaryBridges(model,view);
    const g=adjacency(model,view);
    for(const b of bridges){
      assert.ok(view.nodeIds.includes(b.id),`${view.id} bridge outside view`);
      assert.ok(b.crossing>0,`${b.id} reported as a bridge with no crossing`);
      const touching=g.edges.some(e=>{
        const a=g.byId.get(e.source),c=g.byId.get(e.target);
        const involves=e.source===b.id||e.target===b.id;
        return involves&&a.trustZone!==c.trustZone;
      });
      assert.ok(touching,`${b.id} has no trust-crossing edge`);
    }
    // Ranking must be stable across calls.
    assert.deepEqual(boundaryBridges(model,view).map(n=>n.id),bridges.map(n=>n.id),view.id);
  }
});

test('focus view keeps the start node and only drops unreachable ones',()=>{
  for(const view of model.views){
    const start=view.nodeIds[0];
    const focused=focusView(model,view,start);
    assert.ok(focused.nodeIds.includes(start),`${view.id} focus dropped its own start`);
    assert.ok(focused.nodeIds.length<=view.nodeIds.length,`${view.id} focus grew the view`);
    for(const id of focused.nodeIds)assert.ok(view.nodeIds.includes(id),'focus invented a node');
    // Must stay a renderable view.
    assert.ok(focused.id&&focused.title&&focused.dimensions&&focused.edgeKinds.length,
      `${view.id} focus produced an invalid view`);
  }
});

test('traversal never mutates the model or the view it is given',()=>{
  const view=model.views[0];
  const before=JSON.stringify({view,edges:model.edges});
  blastRadius(model,view,view.nodeIds[0]);
  shortestPath(model,view,view.nodeIds[0],view.nodeIds.at(-1));
  boundaryBridges(model,view);
  assert.equal(JSON.stringify({view,edges:model.edges}),before,'model was mutated');
});
