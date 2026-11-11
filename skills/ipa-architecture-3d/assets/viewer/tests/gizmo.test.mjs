import test from 'node:test';
import assert from 'node:assert/strict';
import {gizmoLabels,axisScreenAngles,AXIS_KEYS} from '../src/gizmo-core.js';
import {getTranslation} from '../src/i18n.js';

// Any UI string built with a .replace('{axis}', ...) placeholder must actually carry
// that placeholder in EVERY language. A missing token renders as a dangling sentence
// like "This angle hides" with nothing after it -- silent, and only visible in a browser.
const PLACEHOLDER_KEYS=['rigHides','rigHidesOnlyAxis','rigFlatAxis'];

test('gizmo labels are localized and declare handedness',()=>{
  // Every shipped language must name the frame, otherwise a left-handed export target
  // would be invisible rather than an explicit choice.
  assert.equal(gizmoLabels('zh-CN').hand,'右手系');
  assert.equal(gizmoLabels('ja-JP').hand,'右手系');
  assert.equal(gizmoLabels('en-US').hand,'Right-handed');
  for(const lang of ['zh-CN','ja-JP','en-US']){
    const l=gizmoLabels(lang);
    for(const k of AXIS_KEYS)assert.ok(l[k],`${lang} missing axis label ${k}`);
    assert.ok(l.handNote,`${lang} missing handNote`);
  }
  assert.notEqual(gizmoLabels('zh-CN').x,gizmoLabels('en-US').x,'languages must actually differ');
  assert.deepEqual(Object.keys(gizmoLabels('zh-CN')).sort(),Object.keys(gizmoLabels('en-US')).sort(),
    'all languages must expose the same keys');
  // An unknown locale falls back rather than throwing mid-render.
  assert.deepEqual(gizmoLabels('fr-FR'),gizmoLabels('zh-CN'));
});

test('every placeholder string keeps its {axis} token in all languages',()=>{
  for(const lang of ['zh-CN','ja-JP','en-US']){
   const tr=getTranslation(lang);
   for(const key of PLACEHOLDER_KEYS){
    assert.ok(typeof tr[key]==='string'&&tr[key].length>0,`${lang}.${key} missing`);
    assert.ok(tr[key].includes('{axis}'),`${lang}.${key} lost its {axis} placeholder: ${tr[key]}`);
   }
  }
});

// The camera basis is the rows of its world inverse rotation; identity means the
// camera looks down -Z, which is the default orientation.
const IDENTITY=[[1,0,0],[0,1,0],[0,0,1]];

test('identity camera puts +X right, +Y up, +Z toward the viewer',()=>{
  const a=axisScreenAngles(IDENTITY,'en-US');
  assert.equal(a.x.x,1);assert.ok(Math.abs(a.x.y)<1e-9);
  assert.ok(Math.abs(a.x.angle)<1e-9,'X points right (0 deg)');
  assert.ok(Math.abs(a.y.angle-90)<1e-9,'Y points up (90 deg)');
  assert.ok(Math.abs(a.z.angle)<1e-9,'Z points right in screen space');
  assert.equal(a.z.towardViewer,true,'+Z is the near side for a default camera');
  assert.equal(a.x.towardViewer,false);
});

test('axis angles are derived, never hard-coded',()=>{
  // Swap the X and Y columns: the reported angles must follow the basis.
  const swapped=[[0,1,0],[1,0,0],[0,0,1]];
  const a=axisScreenAngles(swapped,'en-US');
  assert.ok(Math.abs(a.x.angle-90)<1e-9,'X now points up');
  assert.ok(Math.abs(a.y.angle-0)<1e-9,'Y now points right');
});

test('an axis pointing away from the viewer is not marked as toward-viewer',()=>{
  // Flip Z: the -Z direction is now the near side.
  const flipped=[[1,0,0],[0,1,0],[0,0,-1]];
  const a=axisScreenAngles(flipped);
  assert.equal(a.z.towardViewer,false,'flipped basis points Z away');
});

test('every axis always carries a label and a finite angle',()=>{
  for(const basis of [IDENTITY,[[0,0,1],[1,0,0],[0,1,0]],[[-1,0,0],[0,-1,0],[0,0,-1]]]){
    const a=axisScreenAngles(basis,'ja-JP');
    for(const k of AXIS_KEYS){
      assert.ok(a[k].label,`${k} label`);
      assert.ok(Number.isFinite(a[k].angle),`${k} angle must be finite`);
      assert.ok(a[k].angle>=-180.001&&a[k].angle<=180.001,`${k} angle out of range: ${a[k].angle}`);
    }
  }
});
