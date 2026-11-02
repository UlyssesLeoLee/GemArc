/** Pure geometry: every spatial coordinate encodes a declared architecture attribute. */
export const DIM_FIELDS=['boundary','layer','repo','runtime','environment','dataZone','trustZone','opsLayer','integrationLayer','extensionLayer','failureDomain','domain','owner'];
export const AXIS_COLORS={x:0x56c8dd,y:0xffcc7e,z:0xbca6fc};
export const NO_VALUE='—';
function value(node, field){const v=node[field]; return v===undefined||v===null||String(v).trim()===''?NO_VALUE:String(v)}
/** A missing attribute is an absence of fact, not a category of its own.
 *  It is drawn as a placeholder band so the reader sees the gap, but it must never
 *  count as a distinct class when measuring how much an axis discriminates --
 *  otherwise "everyone is prod, one record just forgot" would look like a real
 *  second Z group and the viewer would claim depth it does not have. */
function discriminates(node, field){const v=node[field]; return !(v===undefined||v===null||String(v).trim()==='')}
export function dimensionPlan(model,view,requested){
 const dimensions=requested||view.dimensions;
 if(!dimensions||!['x','y','z'].every(a=>DIM_FIELDS.includes(dimensions[a]?.field)))throw new Error('DIMENSIONS_INVALID');
 const fields=['x','y','z'].map(a=>dimensions[a].field);
 if(new Set(fields).size!==3)throw new Error('DIMENSIONS_DUPLICATE');
 const nodes=view.nodeIds.map(id=>model.nodes.find(n=>n.id===id)).filter(Boolean);
 const axes={};
 for(const axis of ['x','y','z']){
  const field=dimensions[axis].field; const values=nodes.map(n=>value(n,field));
  const distinct=[...new Set(values)];
  // Stable, documented X classification order is prioritized when it matches groupBy.
  const order=axis==='x'&&field===view.groupBy&&Array.isArray(view.orderBy)?view.orderBy:[];
  distinct.sort((a,b)=>{
   const ia=order.indexOf(a),ib=order.indexOf(b);
   if(ia>=0||ib>=0)return ia<0?1:ib<0?-1:ia-ib;
   return a.localeCompare(b,model.meta.language||'zh-CN');
  });
  axes[axis]={field,values:distinct,size:distinct.length,complete:values.filter(s=>s!==NO_VALUE).length,total:values.length};
 }
 // Independent information gain: an axis is useful only if it further partitions entities.
 // Placeholder (`—`) rows are excluded here; see `discriminates`.
 const splitCount=(fields)=>new Set(nodes.map(n=>fields.map(f=>discriminates(n,f)?value(n,f):'').join('\u0001'))).size;
 const s1=splitCount([axes.x.field]),s2=splitCount([axes.x.field,axes.y.field]),s3=splitCount([axes.x.field,axes.y.field,axes.z.field]);
 const incremental={x:Math.max(0,s1-1),y:s2-s1,z:s3-s2};
 const effective={x:incremental.x>0,y:incremental.y>0,z:incremental.z>0};
 const counts=new Map();
 for(const n of nodes){const key=['x','y','z'].map(a=>value(n,axes[a].field)).join('\u0001');counts.set(key,(counts.get(key)||0)+1)}
 const maxOccupancy=Math.max(1,...counts.values());
 const cols=Math.ceil(Math.sqrt(maxOccupancy)),rows=Math.ceil(maxOccupancy/cols);
 const stride={x:Math.max(5.6,3.3*cols+1.8),y:3.8,z:Math.max(5,2.45*rows+2.6)};
 const positions={},bins=new Map();
 for(const n of nodes){
  const category={x:value(n,axes.x.field),y:value(n,axes.y.field),z:value(n,axes.z.field)};
  const index={x:axes.x.values.indexOf(category.x),y:axes.y.values.indexOf(category.y),z:axes.z.values.indexOf(category.z)};
  const key=[category.x,category.y,category.z].join('\u0001');const rank=bins.get(key)||0;bins.set(key,rank+1);
  const groupCount=counts.get(key)||1;
  const groupCols=Math.ceil(Math.sqrt(groupCount)),groupRows=Math.ceil(groupCount/groupCols);
  const offsetX=(rank%groupCols-(groupCols-1)/2)*3.3;
  const offsetZ=(Math.floor(rank/groupCols)-(groupRows-1)/2)*2.45;
  positions[n.id]={x:(effective.x?(index.x-(axes.x.size-1)/2)*stride.x:0)+offsetX,y:effective.y?index.y*stride.y:0,z:(effective.z?(index.z-(axes.z.size-1)/2)*stride.z:0)+offsetZ,category,index};
 }
 const depth=['x','y','z'].filter(a=>effective[a]).length;
 // The UI must acknowledge where meaningful dimensionality is missing.
 const warnings=['x','y','z'].filter(a=>axes[a].size<2).map(a=>({axis:a,issue:'variance',size:axes[a].size}));
 for(const a of ['y','z'])if(axes[a].size>1&&!effective[a])warnings.push({axis:a,issue:'redundant'});
 for(const axis of ['x','y','z'])if(axes[axis].complete<axes[axis].total)warnings.push({axis,issue:'missing',count:axes[axis].total-axes[axis].complete});
 const coords=Object.values(positions);
 const bounds={minX:Math.min(0,...coords.map(p=>p.x))-2.8,maxX:Math.max(0,...coords.map(p=>p.x))+2.8,minY:Math.min(0,...coords.map(p=>p.y))-.9,maxY:Math.max(0,...coords.map(p=>p.y))+2,minZ:Math.min(0,...coords.map(p=>p.z))-2.4,maxZ:Math.max(0,...coords.map(p=>p.z))+2.4};
 return {axes,positions,bounds,stride,depth,warnings,maxOccupancy,incremental,effective,splitCounts:[s1,s2,s3]};
}
export function crossingReport(model,view){
 const visible=new Set(view.nodeIds);const allowed=new Set(view.edgeKinds);const nodes=new Map(model.nodes.map(n=>[n.id,n]));
 const edges=model.edges.filter(e=>visible.has(e.source)&&visible.has(e.target)&&allowed.has(e.kind));
 const boundaryEdges=edges.filter(e=>nodes.get(e.source)?.trustZone&&nodes.get(e.target)?.trustZone&&nodes.get(e.source).trustZone!==nodes.get(e.target).trustZone);
 const undocumented=boundaryEdges.filter(e=>e.verification!=='confirmed'||!e.control||/待确认|要確認|unknown|未确认|to be confirmed|unverified/i.test(e.control));
 return {edges,boundaryEdges,undocumented,missingEvidence:edges.filter(e=>!e.evidence)};
}
