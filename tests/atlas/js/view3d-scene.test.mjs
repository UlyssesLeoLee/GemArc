// Scene model (ui/scene-model.js): what the 3D, 4D and 5D stages draw, whether the camera frames it (the frame-bounds assertion of the gate, here as
// arithmetic), the occupancy the gate calibrates, the legend, the retreat targets and the label placement. Synthetic ids only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  sceneCompose, sceneTargets, sceneProjected, sceneOutside, sceneOccupancy, sceneLabels, sceneLegend, sceneStageClick, SCENE_CLIP_PX, SCENE_LABEL_MAX_W, SCENE_LABEL_H,
} from '../../../src/gemarc_mcp/atlas/viewer/src/ui/scene-model.js';
import { playResolve, playInitial } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import { viewContext } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import { newCamera, fitCamera } from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import { makeBuffers } from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import { makeBundle, skipNoPython } from './ui-helpers.mjs';
import { sealBundle } from './view3d-helpers.mjs';

const opts = { skip: skipNoPython };
const ASPECTS = [[500, 500], [800, 500], [990, 498], [672, 462], [1100, 360], [320, 600]];

/** Compose, fit a fresh camera to the fit points for w x h, draw the buffers and project: the record the gate would read. */
function frame(ctx, composed, w, h) {
  const cam = fitCamera(newCamera(), composed.fitPoints, w / h);
  const buffers = makeBuffers(ctx.model, { positions: composed.layout.pos, shownNodeIds: composed.shown.nodeIds, shownEdgeIds: composed.shown.edgeIds, boxes: composed.boxes, showFrames: true, relevance: composed.rel, retreat: {}, overlay: composed.overlay || {}, vocab: ctx.vocab, eye: [0, 0, 0] }, ctx.ix);
  return { cam, buffers, projected: sceneProjected(cam, w, h, composed, buffers) };
}

test('3D: every frame and every node of the fitted camera lies inside the canvas at six canvas shapes, for all models of the probes', opts, () => {
  for (const [kind, seed] of [['small', 3], ['reference', 3], ['tiny', 1], ['edge:big400', 1], ['edge:cjk', 2], ['edge:odd-ids', 2], ['edge:unknown-values', 1], ['edge:machine', 1]]) {
    const ctx = viewContext(makeBundle(kind, seed));
    for (const level of ctx.levels) {
      const composed = sceneCompose(ctx, { mode: '3D', level, viewId: 'logical', sel: null, frames: true });
      if (!composed.shown.nodeIds.size) continue;
      for (const [w, h] of ASPECTS) {
        const { projected } = frame(ctx, composed, w, h);
        const out = sceneOutside(projected, w, h);
        assert.deepEqual(out, { frames: [], nodes: [] }, `${kind}/${level} at ${w}x${h}`);
        assert.ok(projected.frames.length > 0 && projected.nodes.length > 0, `${kind}/${level}`);
      }
    }
  }
});

test('3D: the gate mutation: a camera pulled in by half cuts a frame, and the test sees it (so the assertion has teeth)', opts, () => {
  const ctx = viewContext(makeBundle('reference', 3));
  const composed = sceneCompose(ctx, { mode: '3D', level: 'BD', viewId: 'logical', sel: null, frames: true });
  const { cam, buffers } = frame(ctx, composed, 800, 500);
  const near = { ...cam, dist: cam.dist * 0.45 };
  const out = sceneOutside(sceneProjected(near, 800, 500, composed, buffers), 800, 500);
  assert.ok(out.frames.length > 0, 'frames are clipped');
  assert.ok(SCENE_CLIP_PX >= 0);
});

test('3D occupancy of the real views (the numbers the gate floor was calibrated on) and of a sparse scene', opts, () => {
  const seen = {};
  for (const [kind, seed] of [['small', 3], ['reference', 3], ['edge:big400', 1]]) {
    const ctx = viewContext(makeBundle(kind, seed));
    const composed = sceneCompose(ctx, { mode: '3D', level: 'BD', viewId: 'logical', sel: null, frames: true });
    const occ = sceneOccupancy(frame(ctx, composed, 900, 500).projected);
    seen[kind] = occ;
    assert.ok(occ > 0.5 && occ <= 1.0001, `${kind}: ${occ}`);
  }
  assert.ok(Math.min(...Object.values(seen)) >= 0.33, 'at least twice the floor 0.1 of calibration.json, with room to spare');
  // nodes in huge frames: two nodes next to each other in a frame the size of the canvas
  const sparse = { frames: [{ id: 'building', x: 4, y: 4, w: 790, h: 490 }], nodes: [{ id: 'a', x: 400, y: 250 }, { id: 'b', x: 408, y: 254 }] };
  assert.ok(sceneOccupancy(sparse) < 0.001);
  assert.equal(sceneOccupancy({ frames: [], nodes: [{ x: 1, y: 1 }] }), null); assert.equal(sceneOccupancy({ frames: [{ x: 0, y: 0, w: 5, h: 5 }], nodes: [] }), null);
  assert.equal(sceneOccupancy({ frames: [{ x: 0, y: 0, w: null, h: null }], nodes: [{ x: 1, y: 1 }] }), null, 'a frame behind the camera has no box');
});

test('3D occupancy of small scenes: a part is not a point (one, two and three parts, and every probe model at every level, stay clear of the gate floor)', opts, () => {
  const FLOOR = 0.1;
  const occOf = (ctx, level) => {
    const composed = sceneCompose(ctx, { mode: '3D', level, viewId: 'logical', sel: null, frames: true });
    if (!composed.shown.nodeIds.size) return null;
    const p = frame(ctx, composed, 900, 500).projected;
    assert.ok(p.nodes.every((n) => n.r > 0 && Number.isFinite(n.r)), 'every drawn part reports a projected radius');
    return sceneOccupancy(p);
  };
  for (const n of [1, 2, 3]) {
    const b = makeBundle('small', 3); b.model.nodes = b.model.nodes.slice(0, n);
    const ids = new Set(b.model.nodes.map((x) => x.id)); b.model.edges = b.model.edges.filter((e) => ids.has(e.sourceId) && ids.has(e.targetId));
    const ctx = viewContext(b);
    const occ = occOf(ctx, ctx.levels[2]);
    assert.ok(occ >= (n === 1 ? 0.12 : 0.3), `${n} part(s): ${occ}`);
  }
  let lowest = 1;
  for (const [kind, seed] of [['tiny', 1], ['small', 3], ['reference', 3], ['edge:big400', 1], ['edge:cjk', 2], ['edge:odd-ids', 2], ['edge:unknown-values', 1], ['edge:machine', 1], ['edge:no-cases', 4], ['lending', 0]]) {
    const ctx = viewContext(makeBundle(kind, seed));
    for (const level of ctx.levels) { const occ = occOf(ctx, level); if (occ !== null) { lowest = Math.min(lowest, occ); assert.ok(occ >= 2 * FLOOR, `${kind}/${level}: ${occ}`); } }
  }
  assert.ok(lowest >= 0.25, `the lowest of all probe models and levels is ${lowest}`);
});

test('3D: the level reduces what is drawn, a selection always stays, frames are drawn only where a drawn part sits', opts, () => {
  const ctx = viewContext(makeBundle('reference', 3));
  const sizes = ctx.levels.map((level) => sceneCompose(ctx, { mode: '3D', level, viewId: 'logical', sel: null, frames: true }).shown.nodeIds.size);
  assert.deepEqual(sizes, sizes.slice().sort((a, b) => a - b), 'each level only adds');
  assert.ok(sizes[0] < sizes[sizes.length - 1]);
  const deep = ctx.levels[ctx.levels.length - 1]; const shallow = ctx.levels[0];
  const hidden = ctx.nodes.find((n) => !sceneCompose(ctx, { mode: '3D', level: shallow, viewId: 'logical', sel: null, frames: true }).shown.nodeIds.has(n.id));
  assert.ok(hidden, 'a node that is hidden at the shallowest level exists');
  const withSel = sceneCompose(ctx, { mode: '3D', level: shallow, viewId: 'logical', sel: { type: 'node', id: hidden.id }, frames: true });
  assert.ok(withSel.shown.nodeIds.has(hidden.id), 'the selection wins over the level');
  assert.ok(withSel.layout.pos[hidden.id], 'and it has a position');
  const noFrames = sceneCompose(ctx, { mode: '3D', level: deep, viewId: 'logical', sel: null, frames: false });
  assert.deepEqual([Object.keys(noFrames.boxes.buildings).length, Object.keys(noFrames.boxes.rooms).length], [0, 0]);
  const some = sceneCompose(ctx, { mode: '3D', level: shallow, viewId: 'logical', sel: null, frames: true });
  const roomsOfShown = new Set([...some.shown.nodeIds].map((id) => ctx.ix.nodes.get(id).roomId));
  for (const rid of Object.keys(some.boxes.rooms)) assert.ok(roomsOfShown.has(rid) || rid === '~unassigned', rid);
});

test('3D: all six views compose and fit, an unknown view falls back to the logical layout with a warning, never a throw', opts, () => {
  const ctx = viewContext(makeBundle('small', 3));
  for (const viewId of ['logical', 'development', 'dependency', 'process', 'data', 'security', 'logical-view']) {
    const composed = sceneCompose(ctx, { mode: '3D', level: ctx.levels[2], viewId, sel: null, frames: true });
    if (!composed.shown.nodeIds.size) continue;
    const { projected } = frame(ctx, composed, 800, 500);
    assert.deepEqual(sceneOutside(projected, 800, 500), { frames: [], nodes: [] }, viewId);
  }
  assert.doesNotThrow(() => sceneCompose(ctx, { mode: '3D', level: 'zz', viewId: 'no-such-view', sel: { type: 'node', id: 'zz' }, frames: true }));
  assert.ok(ctx.warnings.items.some((w) => w.code === 'GEMARC_ATLAS_VIEW_004'));
});

function sealedCtx(kind = 'small', seed = 3) { const b = makeBundle(kind, seed); sealBundle(b, 2); return viewContext(b); }

test('4D and 5D: the stage draws only what the run touches, at the deepest level, with the room frames of the drawn parts, and the camera frames all of it', opts, () => {
  const ctx = sealedCtx('reference', 3);
  for (const tab of ['4D', '5D']) {
    const res = playResolve(ctx, tab, playInitial());
    assert.equal(res.reason, null, tab);
    for (const idx of [-1, 0, 2, res.length - 1]) {
      const composed = sceneCompose(ctx, { mode: tab, sel: null, res, idx, frames: true });
      assert.ok(composed, tab);
      assert.equal(composed.level, ctx.levels[ctx.levels.length - 1]);
      assert.equal(Object.keys(composed.boxes.buildings).length, 0, 'no building frames on a stage');
      const stageNodes = composed.rel.stage.nodes;
      for (const id of composed.shown.nodeIds) assert.ok(stageNodes.has(id) || composed.rel.chosenNodes.has(id), id);
      if (!composed.shown.nodeIds.size) continue;
      const { projected, buffers } = frame(ctx, composed, 900, 420);
      assert.deepEqual(sceneOutside(projected, 900, 420), { frames: [], nodes: [] }, `${tab} at ${idx}`);
      assert.ok(buffers.instances.length > 0);
    }
  }
});

test('4D: a click inside the stage is a focus, a case selection is not; a stage without a run composes to null', opts, () => {
  const ctx = sealedCtx('small', 3);
  const res = playResolve(ctx, '4D', playInitial());
  const node = res.run.trace[0].sourceNodeId;
  assert.deepEqual(sceneStageClick({ type: 'node', id: node }), { type: 'node', id: node });
  assert.equal(sceneStageClick({ type: 'case', id: 'x' }), null); assert.equal(sceneStageClick(null), null);
  const c = sceneCompose(ctx, { mode: '4D', sel: { type: 'node', id: node }, res, idx: 1, frames: true });
  assert.ok(c.rel.chosenNodes.has(node));
  assert.equal(sceneCompose(ctx, { mode: '4D', sel: null, res: { run: null }, idx: 0, frames: true }), null);
  assert.equal(sceneCompose(ctx, { mode: '5D', sel: null, res: null, idx: 0, frames: true }), null);
});

test('5D: the cause and the effects carry marks, the legend lists them, node states come from the playback position', opts, () => {
  const ctx = sealedCtx('small', 3);
  const res = playResolve(ctx, '5D', playInitial());
  const last = sceneCompose(ctx, { mode: '5D', sel: null, res, idx: res.length - 1, frames: true });
  const marks = Object.values(last.overlay.marks);
  assert.ok(marks.includes('cause') && marks.includes('effect'));
  const lg = sceneLegend(ctx, last);
  assert.ok(lg.marks.map((m) => m.id).includes('cause') && lg.kinds.length > 0 && lg.relations.length >= 0);
  assert.ok(lg.kinds.every((k) => /^#[0-9a-f]{3,6}$/i.test(k.color) && k.outline.length > 0));
  const first = sceneCompose(ctx, { mode: '5D', sel: null, res, idx: -1, frames: true });
  assert.deepEqual(Object.keys(first.overlay.nodeStates), [], 'before the first event nothing has a state');
  const mid = sceneCompose(ctx, { mode: '4D', sel: null, res: playResolve(ctx, '4D', playInitial()), idx: 1, frames: true });
  assert.ok(Object.keys(mid.overlay.nodeStates).length > 0);
  assert.ok(sceneLegend(ctx, mid).states.length > 0);
});

test('targets: the retreat target of every node follows its relevance class (selected and direct stay, unrelated dim and leave)', opts, () => {
  const ctx = viewContext(makeBundle('small', 3));
  const none = sceneCompose(ctx, { mode: '3D', level: 'DD', viewId: 'logical', sel: null, frames: true });
  assert.ok(Object.values(sceneTargets(ctx, none)).every((t) => t.alpha === 1 && t.push === 0));
  const pick = ctx.nodes[0];
  const sel = sceneCompose(ctx, { mode: '3D', level: 'DD', viewId: 'logical', sel: { type: 'node', id: pick.id }, frames: true });
  const targets = sceneTargets(ctx, sel);
  assert.deepEqual(targets[pick.id], { alpha: 1, push: 0 });
  assert.ok(Object.values(targets).some((t) => t.push === 1 && t.alpha < 0.1), 'unrelated parts dim and leave');
  assert.equal(Object.getPrototypeOf(targets), null);
});

test('labels: placed inside the canvas, never overlapping, cut to the label width, the important ones first; a hostile list never throws', opts, () => {
  const ctx = viewContext(makeBundle('small', 3));
  const composed = sceneCompose(ctx, { mode: '3D', level: 'DD', viewId: 'logical', sel: { type: 'node', id: ctx.nodes[0].id }, frames: true });
  const w = 700; const h = 440;
  const { cam, buffers } = frame(ctx, composed, w, h);
  assert.ok(buffers.labels.length > 0);
  const placed = sceneLabels(buffers.labels, cam, w, h);
  assert.ok(placed.length > 0 && placed.length <= buffers.labels.length);
  for (const l of placed) { assert.ok(l.x >= 2 - 1e-6 && l.x + l.w <= w - 2 + 1e-6 && l.y >= 2 - 1e-6 && l.y + l.h <= h - 2 + 1e-6, JSON.stringify(l)); assert.ok(l.w <= SCENE_LABEL_MAX_W); assert.equal(l.h, SCENE_LABEL_H); }
  for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++) {
    const a = placed[i]; const b = placed[j];
    assert.ok(!(a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h), `labels ${i} and ${j} overlap`);
  }
  assert.ok(['Selected', 'Hover', 'Direct', 'Stage', 'Reached'].includes(placed[0].cls));
  const long = sceneLabels([{ id: 'x', pos: cam.target, text: 'w'.repeat(300), cls: 'Selected', state: 'Running' }], cam, w, h);
  assert.equal(long.length, 1); assert.ok(long[0].text.length < 60 && long[0].text.endsWith('…'));
  for (const junk of [null, undefined, [null, 3, {}, { pos: [NaN, 0, 0], text: 't' }, { pos: cam.target }], 'x']) assert.doesNotThrow(() => sceneLabels(junk, cam, w, h), String(junk));
});
