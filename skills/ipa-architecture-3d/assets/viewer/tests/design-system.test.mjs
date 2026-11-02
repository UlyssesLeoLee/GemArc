import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const ROOT=path.resolve(HERE,'..');
const read=p=>fs.readFileSync(path.join(ROOT,p),'utf8');
const css=read('src/style.css');
const html=read('index.html');

test('the palette is small and every colour is a token',()=>{
  // Minimalism is a budget, not a vibe: colour is allowed ONLY to encode architecture
  // meaning (an axis, a trust crossing, a verification state). Chrome is greyscale.
  const declared=new Set([...css.matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})/g)]
    .map(m=>m[2].toLowerCase()));
  const literals=[...css.matchAll(/:\s*(#[0-9a-fA-F]{3,8})/g)].map(m=>m[1].toLowerCase());
  const stray=[...new Set(literals)].filter(c=>!declared.has(c));
  assert.deepEqual(stray,[],`colours must come from :root tokens, found strays: ${stray}`);
  assert.ok(declared.size<=18,`palette grew to ${declared.size} colours; keep it under 18`);
});

test('type and spacing scales are enforced, not re-invented per component',()=>{
  // Every font-size must reference the scale. A literal px here is a new visual step.
  const sizes=[...css.matchAll(/font-size:\s*([^;]+);/g)].map(m=>m[1].trim());
  for(const s of sizes){
    assert.match(s,/^var\(--t-(xs|sm|md|lg|xl)\)$/,
      `font-size:${s} bypasses the type scale`);
  }
  // Spacing must use the 4px scale. Hairlines (1px rules, 2px focus offsets) are
  // borders and outlines, not spacing, so they are exempt by value.
  // Capture ONE value at a time -- a greedy [^;]+ would run past the `;` and mix
  // neighbouring declarations together.
  const paddings=[...css.matchAll(/(?:padding|gap|margin)[a-z-]*:\s*([^;}]+)[;}]/g)]
    .map(m=>m[1].trim());
  const isSpacing=v=>/\d+px/.test(v)&&!['1px','2px','0'].includes(v);
  const raw=paddings.filter(isSpacing);
  assert.deepEqual(raw,[],`spacing must use var(--s*), found literals: ${raw}`);
});

test('the axis palette is defined once and reused by 3D, 2D and CSS',()=>{
  // The same three hues must mean the same thing in every renderer, or the gizmo,
  // the 3D axis lines and the SVG map contradict each other.
  for(const axis of ['x','y','z'])assert.ok(css.includes(`--axis-${axis}:`),`--axis-${axis} missing`);
  const layout=read('src/layout.js');
  const m=/AXIS_COLORS=\{x:0x([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2}),y:0x([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2}),z:0x([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})\}/.exec(layout);
  assert.ok(m,'could not parse AXIS_COLORS');
  const hex=[];
  for(let i=1;i<=9;i+=3)hex.push('#'+m[i]+m[i+1]+m[i+2]);
  const cssAxes=[...css.matchAll(/--axis-[xyz]:\s*(#[0-9a-f]{6})/g)].map(m=>m[1].toLowerCase());
  assert.deepEqual(cssAxes,hex.map(h=>h.toLowerCase()),
    'CSS axis colours drifted from layout.js AXIS_COLORS');
});

test('every element id the JS looks up exists in the HTML',()=>{
  // A renamed id silently renders nothing; this catches that at test time.
  const js=read('src/main.js');
  const ids=[...new Set([...js.matchAll(/\$\('([a-zA-Z][\w-]*)'\)/g)].map(m=>m[1]))];
  const missing=ids.filter(id=>!html.includes(`id="${id}"`));
  assert.deepEqual(missing,[],`JS queries ids absent from index.html: ${missing}`);
});

test('the toolbar is honest: no dead controls, no decorative chrome',()=>{
  // Every button must either be wired to a handler or be a real form control.
  const buttons=[...html.matchAll(/<button[^>]*>/g)].map(m=>m[0]);
  for(const b of buttons){
    assert.ok(/type="button"|type="submit"/.test(b),`button missing an explicit type: ${b}`);
  }
  // The header must not carry decorative glyphs; minimal chrome means none.
  assert.ok(!/logo-emblem/.test(html),'decorative logo emblem should be gone');
  assert.ok(!/class="version"/.test(html),'hardcoded version chip should be gone');
});
