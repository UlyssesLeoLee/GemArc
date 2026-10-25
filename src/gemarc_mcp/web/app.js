/* GemArc static 5D viewer MVP. Model content is rendered using textContent, not innerHTML. */
'use strict';
const state = {model:null,capabilities:null,scenarioData:null,selected:null,tab:'1d',step:0,scenario:null,disabled:new Set(),prototype:0};
const $ = s => document.querySelector(s);
const svgNS = 'http://www.w3.org/2000/svg';
function node(tag, cls, parent, value){const el=document.createElement(tag);if(cls)el.className=cls;if(value!==undefined)el.textContent=String(value);if(parent)parent.appendChild(el);return el;}
function svg(tag,parent,props={},value){const el=document.createElementNS(svgNS,tag);Object.entries(props).forEach(([k,v])=>el.setAttribute(k,String(v)));if(value!==undefined)el.textContent=String(value);parent.appendChild(el);return el;}
function clear(el){el.replaceChildren();return el;}
function panel(parent,title){const p=node('section','panel',parent);node('h2','',p,title);return p;}
function hint(parent,msg){node('p','hint',parent,msg);}
function text(parent,label,value){const r=node('p','small',parent);node('b','',r,label+'：');node('span','muted',r,value==null?'未提供':String(value));return r;}
function button(parent,txt,act){const b=node('button','action',parent,txt);b.type='button';b.addEventListener('click',act);return b;}
function head(title,kicker){$('#headline').textContent=title;$('#kicker').textContent=kicker;}
function showTab(tab){state.tab=tab;document.querySelectorAll('#nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
 const el=clear($('#content'));clear($('#view-actions'));
 if(tab==='1d')render1D(el);if(tab==='2d')render2D(el);if(tab==='3d')render3D(el);
 if(tab==='4d')render4D(el);if(tab==='5d')render5D(el);if(tab==='prototype')renderPrototype(el);
 renderInspector();location.hash=tab;
}
function current(){return state.model;}
function renderInspector(){const area=clear($('#detail'));let n=current().nodes.find(n=>n.id===state.selected);
 if(!n){node('p','muted small',area,'在 2D 图中单击实体以查看其属性和关系。');return;}
 node('h2','',area,n.name);const dl=node('dl','kv',area);
 for(const [k,v] of [['ID',n.id],['类型',n.kind||'未知'],['层级',n.layer||'未定义'],['核验',n.verification||'unknown'],['证据',n.evidence||'未提供']]){node('dt','',dl,k);node('dd','',dl,v)}
 const neighbors=current().edges.filter(e=>e.source===n.id||e.target===n.id);
 node('p','section-title',area,'RELATIONS');for(const e of neighbors)text(area,e.id,(e.source+' → '+e.target));
}
function render1D(el){head(current().meta.system,'1D / LINEAR FACTS');const about=panel(el,'系统资料');text(about,'系统名称',current().meta.system);text(about,'说明',current().meta.description);text(about,'事实状态',current().meta.evidenceState);
 const cards=node('div','facts',el);current().nodes.forEach(n=>{const div=node('div','fact',cards);node('h3','',div,n.name);node('small','',div,n.id+' · '+(n.kind||'未知类型'));text(div,'证据',n.evidence||'未提供');button(div,'定位关系',()=>{state.selected=n.id;showTab('2d')})});
 const views=panel(el,'架构 View / 关注点');current().views.forEach(v=>{text(views,v.title||v.id,(v.concerns||[]).join('、')||'尚未定义')});
}
function graphLayout(nodes,edges){const ranks=new Map(nodes.map(n=>[n.id,0]));const edgeset=edges.filter(e=>ranks.has(e.source)&&ranks.has(e.target));
 // Fixed number of relaxation passes; cyclic graphs remain safe and bounded.
 for(let i=0;i<nodes.length;i++){let changed=false;for(const e of edgeset){let d=Math.min(nodes.length-1,(ranks.get(e.source)||0)+1);if(d>(ranks.get(e.target)||0)){ranks.set(e.target,d);changed=true;}}if(!changed)break;}
 const buckets=new Map();nodes.forEach(n=>{let rank=ranks.get(n.id)||0;if(!buckets.has(rank))buckets.set(rank,[]);buckets.get(rank).push(n)});
 const pos={};[...buckets].sort((a,b)=>a[0]-b[0]).forEach(([rank,ns])=>ns.forEach((n,i)=>pos[n.id]={x:125+(rank*195),y:90+i*112}));
 return {pos,width:Math.max(740,230+Math.max(0,...ranks.values())*195),height:Math.max(320,...[...buckets.values()].map(v=>v.length*112+50))};
}
function render2D(el){head('节点与依赖关系','2D / GRAPH VIEW');const wrapper=panel(el,'统一 Node–Edge 结构图');
 const action=node('div','control',wrapper);node('span','muted small',action,'选择 View：');const select=node('select','',action);
 const views=current().views.length?current().views:[{id:'all',title:'全部节点',nodeIds:current().nodes.map(n=>n.id)}];
 views.forEach(v=>{let o=node('option','',select,v.title||v.id);o.value=v.id});const holder=node('div','',wrapper);
 function draw(){clear(holder);const view=views.find(v=>v.id===select.value)||views[0];const nodes=current().nodes.filter(n=>!view.nodeIds||view.nodeIds.includes(n.id));const edges=current().edges.filter(e=>nodes.some(n=>n.id===e.source)&&nodes.some(n=>n.id===e.target)&&(!view.edgeKinds||view.edgeKinds.includes(e.kind)));
 const {pos,width,height}=graphLayout(nodes,edges);const root=svg('svg',holder,{viewBox:`0 0 ${width} ${height}`,class:'chart',role:'img','aria-label':'架构节点依赖图'});
 const defs=svg('defs',root);const marker=svg('marker',defs,{id:'arr',markerWidth:10,markerHeight:8,refX:10,refY:4,orient:'auto'});svg('path',marker,{d:'M0,0 L10,4 L0,8 Z',fill:'#5b9bb8'});
 edges.forEach(e=>{const a=pos[e.source],b=pos[e.target];if(!a||!b)return;const focus=state.selected&&(e.source===state.selected||e.target===state.selected);const route=a.x===b.x?`M${a.x+46} ${a.y+15} L${b.x+46} ${b.y-18}`:`M${a.x+74} ${a.y} C${a.x+115} ${a.y} ${b.x-115} ${b.y} ${b.x-74} ${b.y}`;
 svg('path',root,{d:route,class:'edge'+(focus?' focus':''),'marker-end':'url(#arr)'});
 let x=(a.x+b.x)/2,y=(a.y+b.y)/2-10;svg('text',root,{x,y,class:'edge-label','text-anchor':'middle'},e.description||e.kind||e.id);
 });
 nodes.forEach(n=>{const p=pos[n.id],g=svg('g',root,{class:'node'+(state.selected===n.id?' selected':''),tabindex:0,role:'button','aria-label':n.name});svg('rect',g,{x:p.x-74,y:p.y-28,width:148,height:57,rx:10});svg('text',g,{x:p.x,y:p.y-1,'text-anchor':'middle'},n.name.length>12?n.name.slice(0,11)+'…':n.name);svg('text',g,{x:p.x,y:p.y+17,'text-anchor':'middle',fill:'#9baec9','font-size':10},n.id.length>20?n.id.slice(0,19)+'…':n.id);
 const selectNode=()=>{state.selected=n.id;draw();renderInspector()};g.addEventListener('click',selectNode);g.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();selectNode()}})});
 node('p','muted small',holder,`节点 ${nodes.length} · 关系 ${edges.length} · 单击实体可在不同维度保持选中 ID`);
 }
 select.addEventListener('change',draw);draw();
}
function render3D(el){head('3D 空间与分类','3D / WEBGL');if(state.capabilities.viewer3D==='not_available'){
 const p=panel(el,'未绑定现有 Three.js 构建产物');hint(p,'此 MVP 不使用 CSS 伪 3D。请先从 GemArc 原仓库构建真正 Three.js Viewer，再在 gemarc.build 指定 --legacy-dist，输出即可接入 3D。');
 text(p,'Viewer 状态','未安装，页面明确降级');return;}
 const p=panel(el,'已接入项目现有 3D Viewer');hint(p,'下方是单独构建的现有 GemArc Three.js Viewer；当前 MVP 未完成新旧 View 之间的选中状态双向同步。');
 const frame=node('iframe','',p);frame.title='GemArc 3D Legacy Viewer';frame.src='./legacy-3d/index.html';frame.setAttribute('sandbox','allow-scripts allow-same-origin');
}
function scenarioPicker(parent,onChange){const sel=node('select','',parent);for(const scenario of current().scenarios){const opt=node('option','',sel,scenario.name||scenario.id);opt.value=scenario.id}sel.value=state.scenario||current().scenarios[0]?.id||'';sel.addEventListener('change',()=>{state.scenario=sel.value;state.step=0;onChange()});return sel}
function render4D(el){clear(el);head('动态流程与时间轴','4D / EVENT TIME');if(!current().scenarios.length){hint(el,'缺少状态或事件模型，不能生成真实 4D 行为。');return;}
 const controls=panel(el,'选择模拟 Case');const r=node('div','control',controls);scenarioPicker(r,()=>render4D(el));
 const scenarioId=state.scenario||current().scenarios[0].id;state.scenario=scenarioId;const trace=state.scenarioData[scenarioId].normal.trace;
 button(r,'上一步',()=>{state.step=Math.max(0,state.step-1);render4D(el)});button(r,'下一事件',()=>{state.step=Math.min(trace.length,state.step+1);render4D(el)});
 button(r,'显示全部',()=>{state.step=trace.length;render4D(el)});node('span','chip',r,`${state.step} / ${trace.length} events`);
 const p=panel(el,'事件传播');trace.slice(0,state.step).forEach(e=>{const item=node('div','timeline-item'+(e.status==='blocked'?' blocked':''),p);node('b','',item,`${e.timeMs}ms  ${e.module} · ${e.event}`);text(item,'类型',e.status);if(e.triggeredBy)text(item,'来源',e.triggeredBy)});
 if(!state.step)node('p','muted',p,'点击「下一事件」开始按确定性模型回放。');
 hint(el,'时间仅来自模型 timeMs/delayMs，示例数据不代表真实执行延迟。');
}
function simulateWeb(sc,disabled){const todo=(sc.events||[]).map((e,i)=>({module:e.module,event:e.event,timeMs:e.timeMs,order:i,triggeredBy:null}));let counter=todo.length;
 let trace=[];while(todo.length && trace.length<512){todo.sort((a,b)=>a.timeMs-b.timeMs||a.order-b.order);const evt=todo.shift();if(evt.timeMs>3600000)break;evt.status=disabled.has(evt.module)?'blocked':'delivered';evt.traceId=`trace-${String(trace.length).padStart(4,'0')}`;trace.push(evt);if(evt.status==='blocked')continue;
 (sc.rules||[]).forEach(rule=>{if(rule.source===evt.module&&rule.whenEvent===evt.event)todo.push({module:rule.target,event:rule.emit,timeMs:evt.timeMs+(rule.delayMs||0),order:counter++,triggeredBy:evt.traceId})});}
 return trace;
}
function render5D(el){clear(el);head('模块组合与因果干预','5D / CAUSAL SYSTEM');if(!current().scenarios.length){hint(el,'没有显式因果规则，系统不会编造模拟结果。');return;}
 const controls=panel(el,'反事实干预 / disable(module)');const row=node('div','control',controls);scenarioPicker(row,()=>render5D(el));
 const toggles=node('div','',controls);current().nodes.forEach(n=>{const label=node('label','selectnode',toggles);const input=node('input','',label);input.type='checkbox';input.checked=state.disabled.has(n.id);input.addEventListener('change',()=>{input.checked?state.disabled.add(n.id):state.disabled.delete(n.id);render5D(el)});node('span','',label,`${n.name} 关闭`)});
 const sid=state.scenario||current().scenarios[0].id;state.scenario=sid;const sc=current().scenarios.find(s=>s.id===sid);const base=simulateWeb(sc,new Set()),changed=simulateWeb(sc,state.disabled);
 const delivered=arr=>new Set(arr.filter(e=>e.status==='delivered').map(e=>e.module+'::'+e.event));const a=delivered(base),b=delivered(changed);const lost=[...a].filter(x=>!b.has(x));const gained=[...b].filter(x=>!a.has(x));
 const split=node('div','split',el);const left=panel(split,'基准路径');const right=panel(split,'干预路径');for(const [target,trace] of [[left,base],[right,changed]]){trace.forEach(e=>{let t=node('div','timeline-item'+(e.status==='blocked'?' blocked':''),target);node('span','',t,`${e.timeMs}ms · ${e.module} / ${e.event} · ${e.status}`)})}
 const result=panel(el,'模型结果差异');text(result,'未达事件',lost.join('、')||'无');text(result,'新增事件',gained.join('、')||'无');text(result,'证据类型',sc.evidenceClass||'hypothetical');
 hint(el,'反事实输出只来自显式事件规则和所选干预，不能推断真实世界因果强度或未建模的反馈。');
}
function renderPrototype(el){clear(el);head('交互式页面原型','UI / MULTI-TAB PROTOTYPE');const pages=current().prototypes||[];
 if(!pages.length){hint(el,'模型未定义 prototypes；默认不编造业务界面。');return;}
 const p=panel(el,'页面与操作');const tabs=node('div','tabs',p);pages.forEach((pg,i)=>{const b=button(tabs,pg.title||pg.id,()=>{state.prototype=i;renderPrototype(el)});b.classList.toggle('active',state.prototype===i)});
 const pg=pages[state.prototype]||pages[0];const content=node('div','protopage',p);node('h2','',content,pg.title);node('p','muted',content,pg.description||'暂无描述');
 const feedback=node('p','small',content,'可选择下列交互热点，当前为轻量原型组件，不包含 Konva 可视化编辑器。');
 for(const action of pg.actions||[])button(content,action,()=>{feedback.textContent=`已触发原型动作：${action}（演示，不提交实际业务数据）`});
}
async function main(){try{
 const [m,c,s]=await Promise.all(['architecture.json','capabilities.json','scenarios.json'].map(x=>fetch('./data/'+x,{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(x);return r.json()})));
 state.model=m;state.capabilities=c;state.scenarioData=s;
 const evidence=(m.meta||{}).evidenceState||'unknown';$('#evidence').textContent='EVIDENCE · '+evidence.toUpperCase();
 $('#footer').textContent=`GemArc MVP | 模型事实等级: ${evidence} | 3D: ${c.viewer3D} | 4D: ${c.dynamic4D} | 5D: ${c.causal5D}`;
 document.querySelectorAll('#nav button').forEach(btn=>btn.addEventListener('click',()=>showTab(btn.dataset.tab)));
 const requested=location.hash.substring(1);showTab(['1d','2d','3d','4d','5d','prototype'].includes(requested)?requested:'1d');
 }catch(err){$('#content').textContent='加载失败：请使用 python -m http.server 8000 启动本地 HTTP 服务，再访问 http://127.0.0.1:8000/ 。请检查 data/*.json 是否存在。';$('#evidence').textContent='LOAD ERROR';}}
main();
