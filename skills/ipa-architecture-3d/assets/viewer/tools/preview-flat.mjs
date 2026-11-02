// Dev harness: render the flat map to a standalone SVG/HTML without a browser or WebGL.
// Uses a minimal DOM shim so flat-view.js runs unmodified.
import fs from 'node:fs';
import path from 'node:path';
import {plan2d} from '../src/flat.js';
import {crossingReport} from '../src/layout.js';
import {model} from './load-model.mjs';

class N{
 constructor(name){this.tag=name;this.attrs={};this.children=[];this.text=null;this.listeners={};}
 setAttribute(k,v){this.attrs[k]=String(v)}
 appendChild(c){this.children.push(c);return c}
 append(...nodes){for(const c of nodes)this.children.push(c);return this}
 insertBefore(c,ref){const i=this.children.indexOf(ref);this.children.splice(i<0?0:i,0,c);return c}
 removeChild(c){const i=this.children.indexOf(c);if(i>=0)this.children.splice(i,1);return c}
 addEventListener(t,fn){(this.listeners[t] ||= []).push(fn)}
 get firstChild(){return this.children[0]||null}
 set textContent(v){this.text=v}
 get textContent(){return this.text??''}
 toString(){
  const a=Object.entries(this.attrs).map(([k,v])=>` ${k}="${String(v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}"`).join('');
  const inner=this.text!==null?this.text.replace(/&/g,'&amp;').replace(/</g,'&lt;'):'';
  return `<${this.tag}${a}>${inner}${this.children.map(String).join('')}</${this.tag}>`;
 }
}
globalThis.document={createElementNS:(_ns,name)=>new N(name)};
const {drawFlat}=await import('../src/flat-view.js');

const {getTranslation}=await import('../src/i18n.js');
const tr=getTranslation('zh-CN');
const text=k=>tr[k]||k;
const classColors={actor:0xf2c17e,external:0xf2c17e,system:0x76cfe3,gateway:0x76cfe3,service:0x76cfe3,module:0x76cfe3,process:0x9baff4,runtime:0x9baff4,plugin:0x9baff4,database:0x70dbb9,storage:0x70dbb9,data:0x70dbb9,security:0xf2c17e,operations:0x70dbb9};

const outDir=process.argv[2]||'.';
fs.mkdirSync(outDir,{recursive:true});
const index=Number(process.argv[3]??0);
const view=model.views[index];
const plan=plan2d(model,view);
const report=crossingReport(model,view);
const svg=new N('svg');
const state={selected:null,showEdges:true,filters:{x:null,y:null,z:null}};
drawFlat(svg,{plan,model,report,state,classColors,tr,text,onSelect:()=>{}});
const markup=svg.toString().replace('<svg ','<svg xmlns="http://www.w3.org/2000/svg" width="100%" ');
const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>2D flat map preview</title>
<style>body{margin:0;background:#07121d;font-family:'Noto Sans SC',system-ui,sans-serif;color:#dce9f5}
.wrap{padding:18px}.note{font-size:12px;line-height:1.8;color:#9dc0d3;margin:0 0 12px}
svg{display:block;width:100%;height:auto;border:1px solid #23354a;border-radius:12px;background:#0a1826}</style></head>
<body><div class="wrap"><p class="note"></p><div id="host"></div></div>
<script type="module">const svg = document.createElement('div'); svg.innerHTML = ${JSON.stringify(markup)}; document.getElementById('host').appendChild(svg.firstElementChild);</script>
</body></html>`;
const file=path.join(outDir,`flat-view-${index}.html`);
fs.writeFileSync(file,html,'utf8');
console.log(`view: ${view.id} — ${view.title}`);
console.log(`X=${plan.axes.x.field} (${plan.columns.length}) Y=${plan.axes.y.field} (${plan.rows.length}) Z=${plan.axes.z.field} (${plan.zValues.length}) demoted=${plan.zDemoted}`);
console.log(`canvas ${Math.round(plan.width)}x${Math.round(plan.height)}  nodes=${Object.keys(plan.nodes).length}  groups=${[...plan.cells.values()].reduce((n,c)=>n+c.groups.length,0)}`);
console.log(`wrote ${file}`);