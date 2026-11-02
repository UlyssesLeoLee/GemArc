/** Impact analysis over the architecture graph.
 *
 *  Pure, dependency-free, and deliberately small. A graph library earns its keep only
 *  once traversal is needed in more than one place; until then these functions are the
 *  whole requirement and cost nothing to ship.
 *
 *  Every traversal respects the SAME filters the renderer does -- an edge whose kind is
 *  not in the current view's `edgeKinds`, or whose endpoints are not both visible, must
 *  not appear in an impact path. Otherwise "what breaks if I change this?" would answer
 *  with links the reader cannot even see, and the two views would disagree.
 */

export function adjacency(model, view){
  const visible=new Set(view.nodeIds);
  const kinds=new Set(view.edgeKinds);
  const byId=new Map(model.nodes.map(n=>[n.id,n]));
  const out=new Map(), incoming=new Map();
  for(const id of visible){out.set(id,[]);incoming.set(id,[]);}
  const edges=model.edges.filter(e=>
    visible.has(e.source)&&visible.has(e.target)&&kinds.has(e.kind));
  for(const e of edges){
    out.get(e.source).push({to:e.target,edge:e});
    incoming.get(e.target).push({to:e.source,edge:e});
  }
  return {visible,byId,edges,out,incoming};
}

/** Every node reachable from `start` by following `direction` up to `maxDepth` hops.
 *
 *  Depth is counted in EDGES, and the start node is at depth 0. The result records the
 *  depth and the path that reached each node, because "impacted" without "how far" is not
 *  actionable during a review. */
export function blastRadius(model,view,start,options={}){
  const {direction='out',maxDepth=Infinity,edgeKinds=null}=options;
  const graph=adjacency(model,view);
  if(!graph.visible.has(start))return {start,levels:new Map(),edges:new Set(),truncated:false};

  // edgeKinds narrows further than the view whitelist, never widens it.
  const allowed=edgeKinds?new Set(edgeKinds):null;
  const link=id=>{
    const list=direction==='out'?graph.out.get(id):graph.incoming.get(id);
    if(!list)return[];
    return allowed?list.filter(r=>allowed.has(r.edge.kind)):list;
  };

  const levels=new Map([[start,0]]);
  const usedEdges=new Set();
  const paths=new Map([[start,null]]);
  let frontier=[start];
  let depth=0;
  let truncated=false;
  while(frontier.length){
    if(depth>=maxDepth){truncated=frontier.length>0;break;}
    depth++;
    const next=[];
    for(const id of frontier){
      for(const {to,edge} of link(id)){
        usedEdges.add(edge.id);
        if(levels.has(to))continue;
        levels.set(to,depth);
        paths.set(to,{from:id,edgeId:edge.id,kind:edge.kind});
        next.push(to);
      }
    }
    frontier=next;
  }
  return {start,direction,levels,paths,edges:usedEdges,truncated,maxDepth};
}

/** Shortest path between two nodes, over the same filtered graph.
 *
 *  Breadth-first, so the first time a node is seen it is at its minimum depth. Ties are
 *  broken by the model's edge order, which keeps the result deterministic -- a layout
 *  that reshuffles on every render cannot be reviewed. */
export function shortestPath(model,view,from,to,options={}){
  const {direction='out',edgeKinds=null}=options;
  const graph=adjacency(model,view);
  if(!graph.visible.has(from)||!graph.visible.has(to))return null;
  if(from===to)return {nodes:[from],edges:[],depth:0};
  const allowed=edgeKinds?new Set(edgeKinds):null;
  const link=id=>{
    const list=direction==='out'?graph.out.get(id):graph.incoming.get(id);
    return allowed?list.filter(r=>allowed.has(r.edge.kind)):list;
  };
  const cameFrom=new Map([[from,null]]);
  const queue=[from];
  let head=0;
  while(head<queue.length){
    const id=queue[head++];
    for(const {to:next,edge} of link(id)){
      if(cameFrom.has(next))continue;
      cameFrom.set(next,{from:id,edgeId:edge.id,kind:edge.kind});
      if(next===to){
        const nodes=[],edges=[];
        let cur=next;
        while(cur!==from){
          nodes.push(cur);
          const step=cameFrom.get(cur);
          edges.push(step.edgeId);
          cur=step.from;
        }
        nodes.push(from);
        nodes.reverse();edges.reverse();
        return {nodes,edges,depth:nodes.length-1};
      }
      queue.push(next);
    }
  }
  return null;
}

/** Chain of trust boundaries between two nodes.
 *
 *  A path crossing trust zones is the single most common review finding, so it is
 *  computed once and reused by both the 3D scene and the 2D map. Returns the ordered
 *  transitions plus whether every crossing has a confirmed control. */
export function trustChain(model,view,from,to,options={}){
  const path=shortestPath(model,view,from,to,options);
  if(!path)return null;
  const byId=new Map(model.nodes.map(n=>[n.id,n]));
  const edgeById=new Map(model.edges.map(e=>[e.id,e]));
  const crossings=[];
  for(let i=0;i<path.nodes.length-1;i++){
    const a=byId.get(path.nodes[i]),b=byId.get(path.nodes[i+1]);
    const edge=edgeById.get(path.edges[i]);
    if(!a||!b||a.trustZone===b.trustZone)continue;
    const control=edge?.control||'';
    const undocumented=/待确认|要確認|未确认|to be confirmed|unverified|unknown/i.test(control);
    crossings.push({from:a.trustZone,to:b.trustZone,edgeId:edge.id,
      verified:edge?.verification==='confirmed'&&!undocumented,control});
  }
  return {...path,crossings,
    crossesTrust:crossings.length>0,
    allVerified:crossings.every(c=>c.verified)};
}

/** The nodes a reviewer would most plausibly ask about next: those that bridge the
 *  trust boundary, ranked by how much they sit between the two sides. */
export function boundaryBridges(model,view){
  const graph=adjacency(model,view);
  const edges=graph.edges;
  const degree=new Map();
  const crossingDegree=new Map();
  for(const e of edges){
    degree.set(e.source,(degree.get(e.source)||0)+1);
    degree.set(e.target,(degree.get(e.target)||0)+1);
    const a=graph.byId.get(e.source),b=graph.byId.get(e.target);
    if(a?.trustZone&&b?.trustZone&&a.trustZone!==b.trustZone){
      crossingDegree.set(e.source,(crossingDegree.get(e.source)||0)+1);
      crossingDegree.set(e.target,(crossingDegree.get(e.target)||0)+1);
    }
  }
  return [...graph.visible]
    .map(id=>({id,degree:degree.get(id)||0,crossing:crossingDegree.get(id)||0,
      name:graph.byId.get(id)?.name||id}))
    // A node that never touches the boundary cannot be a bridge; rank by that first.
    .sort((a,b)=>b.crossing-a.crossing||b.degree-a.degree||a.id.localeCompare(b.id))
    .filter(n=>n.crossing>0);
}

/** Drop nodes outside the blast radius so the 3D scene can dim the irrelevant ones.
 *  Returns the same view shape, so callers can hand it straight back to the renderer. */
export function focusView(model,view,start,options={}){
  const impact=blastRadius(model,view,start,options);
  const keep=new Set(impact.levels.keys());
  return {...view,nodeIds:view.nodeIds.filter(id=>keep.has(id))};
}
