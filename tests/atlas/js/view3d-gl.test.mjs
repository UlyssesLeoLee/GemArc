// WebGL glue (ui/gl-renderer.js) against a recording stand-in context: the instancing paths, the flat fallback, the sprite quads, the draw-call count, and a
// lint of the shader sources (every attribute and uniform that the JS asks for is used by its program; varyings agree). No real GPU here: the real-browser
// proof (pixels, renderer, getError) is the gate (tests/atlas/test_gate_3d.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { glInit, glDraw, glHaloQuads, glDotQuads, GL_THEMES } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/gl-renderer.js';
import { sceneCompose } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/scene-model.js';
import { playResolve, playInitial } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import { viewContext } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import { makeBuffers, toInstanceBuffers } from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import { newCamera, fitCamera, viewProj, cameraPosition } from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import { makeBundle, skipNoPython } from './ui-helpers.mjs';
import { fakeGl, sealBundle } from './view3d-helpers.mjs';

const opts = { skip: skipNoPython };
const SOURCE = readFileSync(new URL('../../../src/gemarc_mcp/atlas/viewer/src/ui/gl-renderer.js', import.meta.url), 'utf8');

function sceneFrame(kind, mode, idx) {
  const b = makeBundle(kind, 3); sealBundle(b, 1);
  const ctx = viewContext(b);
  const res = mode === '3D' ? null : playResolve(ctx, mode, playInitial());
  const composed = sceneCompose(ctx, { mode, level: ctx.levels[2], viewId: 'logical', sel: null, res, idx, frames: true });
  const cam = fitCamera(newCamera(), composed.fitPoints, 1.6);
  const buffers = makeBuffers(ctx.model, { positions: composed.layout.pos, shownNodeIds: composed.shown.nodeIds, shownEdgeIds: composed.shown.edgeIds, boxes: composed.boxes, showFrames: true, relevance: composed.rel, retreat: {}, overlay: composed.overlay || {}, vocab: ctx.vocab, eye: cameraPosition(cam) }, ctx.ix);
  return { buffers, vp: viewProj(cam, 1.6), eye: cameraPosition(cam), groups: toInstanceBuffers(buffers.instances) };
}
const frameArgs = (f) => ({ ...f, width: 800, height: 500, theme: 'dark', clock: 0.5 });

test('init: the driver is named, the software renderer is recognised, the instancing path follows the context', () => {
  const two = glInit(fakeGl({ renderer: 'ANGLE (Google, Vulkan, SwiftShader Device)' }));
  assert.equal(two.info.instancing, 'webgl2'); assert.equal(two.info.software, true); assert.match(two.info.renderer, /SwiftShader/);
  const hw = glInit(fakeGl({ renderer: 'ANGLE (Vendor, Some Card, D3D11)' })); assert.equal(hw.info.software, false);
  assert.equal(glInit(fakeGl({ webgl2: false })).info.instancing, 'extension');
  assert.equal(glInit(fakeGl({ webgl2: false, instancing: false })).info.instancing, 'none');
  assert.deepEqual(Object.keys(GL_THEMES).sort(), ['dark', 'light']);
  const broken = fakeGl(); broken.getShaderParameter = () => false; broken.getShaderInfoLog = () => 'boom';
  assert.throws(() => glInit(broken), /shader: boom/, 'a compile failure throws (the stage shows the labelled fallback)');
});

test('draw (WebGL 2): one instanced call per shape, one call for the lines, none for an empty pass; the call count is what the gate counts', opts, () => {
  const f = sceneFrame('small', '3D', 0);
  const gl = fakeGl(); const rs = glInit(gl);
  const calls = glDraw(rs, frameArgs(f));
  assert.equal(calls, gl.draws);
  assert.equal(gl.calls('drawArraysInstanced'), f.groups.filter((g) => g.count > 0).length);
  assert.ok(gl.calls('drawArraysInstanced') >= 1 && gl.calls('drawArraysInstanced') <= 7, 'at most one per gem shape');
  assert.equal(gl.calls('drawArrays'), f.buffers.lineCount ? 1 : 0);
  const inst = gl.log.filter((x) => x[0] === 'drawArraysInstanced');
  assert.equal(inst.reduce((n, x) => n + x[4], 0), f.buffers.instances.length, 'every drawn part is one instance');
  assert.ok(gl.calls('vertexAttribDivisor') > 0);
  const tail = gl.log.slice(-8).filter((x) => x[0] === 'vertexAttribDivisor');
  assert.ok(tail.length > 0 && tail.every((x) => x[2] === 0), 'the divisors are back to 0');
  assert.equal(gl.calls('clear'), 1); assert.equal(gl.calls('viewport'), 1);
  const empty = fakeGl();
  const none = glDraw(glInit(empty), frameArgs({ ...f, groups: [], buffers: { ...f.buffers, lineCount: 0, haloCount: 0, dotCount: 0, mesh: { faceCount: 0 } } }));
  assert.equal(none, 0);
});

test('draw (WebGL 1 with the extension, and without any instancing): the extension path divides through the extension, the flat mesh path draws one triangle list', opts, () => {
  const f = sceneFrame('small', '3D', 0);
  const ext = fakeGl({ webgl2: false }); const calls = glDraw(glInit(ext), frameArgs(f));
  assert.equal(calls, ext.draws); assert.ok(ext.calls('drawArraysInstancedANGLE') >= 1); assert.equal(ext.calls('drawArraysInstanced'), 0);
  const flat = fakeGl({ webgl2: false, instancing: false }); const n = glDraw(glInit(flat), frameArgs(f));
  assert.equal(n, flat.draws); assert.equal(flat.calls('drawArraysInstancedANGLE'), 0);
  const tri = flat.log.find((x) => x[0] === 'drawArrays' && x[1] === flat.TRIANGLES);
  assert.ok(tri && tri[3] === f.buffers.mesh.faceCount * 3, 'the flat mesh is one TRIANGLES call over every face');
});

test('draw: 4D and 5D add the halo and dot sprite passes when a position has an executing part and an event on an edge', opts, () => {
  let sawHalo = false;
  for (const mode of ['4D', '5D']) {
    for (let idx = 0; idx < 6; idx++) {
      const f = sceneFrame('small', mode, idx);
      const gl = fakeGl(); glDraw(glInit(gl), frameArgs(f));
      const faces = gl.log.filter((x) => x[0] === 'drawArrays' && x[1] === gl.TRIANGLES);
      if (f.buffers.haloCount) { sawHalo = true; assert.ok(faces.some((x) => x[3] === f.buffers.haloCount * 6)); }
      if (f.buffers.dotCount) assert.ok(faces.some((x) => x[3] === f.buffers.dotCount * 6));
      assert.equal(gl.draws, gl.log.filter((x) => /^draw/.test(x[0])).length);
    }
  }
  assert.ok(sawHalo, 'some position of the synthetic runs has a halo');
});

test('sprite quads: six vertices per sprite with the corner running -1..1, the sprite data copied unchanged', () => {
  const halos = new Float32Array([1, 2, 3, 0.1, 0.2, 0.3, 0.4, 1.5, 2, 4, 5, 6, 0.5, 0.6, 0.7, 0.8, 1.2, 3]);
  const q = glHaloQuads(halos, 2);
  assert.equal(q.length, 2 * 6 * 11);
  for (let i = 0; i < 12; i++) {
    const from = i < 6 ? 0 : 9;
    assert.deepEqual([...q.slice(i * 11, i * 11 + 9)], [...halos.slice(from, from + 9)]);
    assert.ok(Math.abs(q[i * 11 + 9]) === 1 && Math.abs(q[i * 11 + 10]) === 1);
  }
  const corners = new Set(Array.from({ length: 6 }, (_, i) => q[i * 11 + 9] + ',' + q[i * 11 + 10])); assert.equal(corners.size, 4, 'two triangles cover four corners');
  const dots = glDotQuads(new Float32Array([1, 2, 3, 1, 0, 0, 0.9]), 1);
  assert.equal(dots.length, 6 * 9); assert.deepEqual([...dots.slice(0, 7)], [...new Float32Array([1, 2, 3, 1, 0, 0, 0.9])]);
  assert.equal(glHaloQuads(new Float32Array(0), 0).length, 0);
});

test('shader sources: every attribute and uniform the JS asks for is used by its program, the varyings of a vertex and a fragment shader agree, no extension is needed', () => {
  const lits = Object.fromEntries([...SOURCE.matchAll(/^const (GL_[VF]S_[A-Z]+) = '([^']*)';/gm)].map((m) => [m[1], m[2]]));
  assert.deepEqual(Object.keys(lits).sort(), ['GL_FS_DOT', 'GL_FS_HALO', 'GL_FS_LINE', 'GL_FS_MESH', 'GL_VS_DOT', 'GL_VS_HALO', 'GL_VS_LINE', 'GL_VS_MESH']);
  for (const [name, src] of Object.entries(lits)) {
    assert.equal(src.split('{').length, src.split('}').length, name + ': braces');
    assert.equal(src.split('(').length, src.split(')').length, name + ': parentheses');
    assert.ok(/void main\(\)/.test(src) && !/#extension|texture2D|sampler/.test(src), name);
    if (name.startsWith('GL_FS')) assert.ok(/precision mediump float;/.test(src), name + ': precision');
    for (const a of [...src.matchAll(/attribute \w+ (\w+);/g)].map((m) => m[1])) assert.ok(src.split(a).length > 2, `${name}: ${a} is declared and never used`);
    for (const u of [...src.matchAll(/uniform \w+ (\w+);/g)].map((m) => m[1])) assert.ok(src.split(u).length > 2, `${name}: ${u} is declared and never used`);
  }
  const varyings = (s) => [...s.matchAll(/varying \w+ (\w+);/g)].map((m) => m[1]).sort().join();
  for (const k of ['MESH', 'LINE', 'DOT', 'HALO']) assert.equal(varyings(lits['GL_VS_' + k]), varyings(lits['GL_FS_' + k]), k + ': varyings');
  const program = { pm: 'MESH', pl: 'LINE', pd: 'DOT', ph: 'HALO' };
  const asked = [...SOURCE.matchAll(/at\((p[mldh]), '(\w+)'\)/g)].map((m) => [m[1], m[2]]);
  assert.equal(asked.length, 14, 'five mesh, two line, three dot and four halo attributes');
  for (const [p, name] of asked) assert.ok(new RegExp('attribute \\w+ ' + name + ';').test(lits['GL_VS_' + program[p]]), `${program[p]}: attribute ${name}`);
  for (const [p, name] of [...SOURCE.matchAll(/un\((p[mldh]), '(\w+)'\)/g)].map((m) => [m[1], m[2]])) {
    assert.ok(new RegExp('uniform \\w+ ' + name + ';').test(lits['GL_VS_' + program[p]] + lits['GL_FS_' + program[p]]), `${program[p]}: uniform ${name}`);
  }
});
