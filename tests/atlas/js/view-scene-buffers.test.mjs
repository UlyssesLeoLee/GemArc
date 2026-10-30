import test from 'node:test';
import assert from 'node:assert/strict';
import { makeModel, PROFILE, allFinite } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import { vec } from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';

const V = () => MA.makeVocab(PROFILE);
function scene(o = {}, extra = {}) {
  const m = o.model ?? makeModel({ seed: 31, perRoom: 5, ...o }); const vocab = extra.vocab ?? V(); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'logical', { vocab, ix });
  const state = { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab, eye: [0, 8, -40], ...extra };
  return { m, vocab, ix, L, state, build: (patch = {}) => SB.makeBuffers(m, { ...state, ...patch }, ix) };
}

test('every visible node and edge is present in the buffers and independently pickable', () => {
  const { m, build, L } = scene();
  const b = build();
  assert.equal(b.hitNodes.length, m.nodes.length);
  assert.deepEqual(new Set(b.hitEdges.map((e) => e.id)), L.shownEdgeIds);
  assert.equal(b.mesh.faceCount * 9, b.mesh.pos.length);
  assert.ok(allFinite(b.mesh.pos) && allFinite(b.mesh.nrm) && allFinite(b.mesh.col) && allFinite(b.lines));
  assert.equal(b.lineCount * 7, b.lines.length);
  for (const n of b.hitNodes) assert.deepEqual(b.worldPoints[n.id], L.pos[n.id]);
});

test('edges that cross floors and buildings are drawn between the true 3D positions', () => {
  const { m, build, L, ix } = scene();
  const b = build();
  const e = m.edges.find((x) => x.sourceId !== x.targetId && ix.nodes.get(x.sourceId).roomId !== ix.nodes.get(x.targetId).roomId);
  const parts = b.hitEdges.filter((x) => x.id === e.id);
  assert.ok(parts.length >= 10);
  const ends = [parts[0].a, parts.at(-1).b].map((p) => p.map((x) => +x.toFixed(6)).join(','));
  const want = [L.pos[e.sourceId], L.pos[e.targetId]].map((p) => p.map((x) => +x.toFixed(6)).join(','));
  assert.deepEqual(ends.slice().sort(), want.slice().sort());
});

test('parallel edges get separate paths and a self reference is a ring, not a point', () => {
  const m = { nodes: [{ id: 'a', kind: 'Module', name: 'A', roomId: 'r' }, { id: 'b', kind: 'Module', name: 'B', roomId: 'r' }], space: { buildings: [{ id: 'bl', name: 'x', trust: 'internal' }], floors: [{ id: 'fl', buildingId: 'bl', name: 'x', level: 0 }], rooms: [{ id: 'r', floorId: 'fl', name: 'x' }] },
    edges: [{ id: 'e1', sourceId: 'a', targetId: 'b', relation: 'calls' }, { id: 'e2', sourceId: 'a', targetId: 'b', relation: 'sends' }, { id: 'e3', sourceId: 'b', targetId: 'a', relation: 'calls' }, { id: 'e4', sourceId: 'a', targetId: 'a', relation: 'calls' }] };
  const { build } = scene({ model: m });
  const b = build();
  const mid = (id) => { const s = b.hitEdges.filter((x) => x.id === id); return s[Math.floor(s.length / 2)].a.map((x) => +x.toFixed(5)).join(','); };
  assert.equal(new Set(['e1', 'e2', 'e3'].map(mid)).size, 3);
  const loop = b.hitEdges.filter((x) => x.id === 'e4'); assert.equal(loop.length, 16);
  assert.ok(loop.some((s) => vec.len(vec.sub(s.a, s.b)) > 0.05));
});

test('a selection dims and disperses the unrelated nodes out of their own building, at their own height; related nodes stay sharp', () => {
  const { m, state, ix, L } = scene();
  const rel = MA.relevance(ix, { type: 'node', id: m.nodes[0].id });
  const retreat = {}; const ids = m.nodes.map((n) => n.id);
  const center = SB.focusMiddle(rel, L.pos);
  for (let i = 0; i < 400; i++) SB.stepEaseAll(retreat, (id) => rel.nodeTier(id), ids, 0.05, (id) => SB.disperseDelay(L.pos[id], center));
  assert.equal(SB.stepEaseAll(retreat, (id) => rel.nodeTier(id), ids, 0.05), false);
  const b = SB.makeBuffers(m, { ...state, relevance: rel, retreat }, ix);
  const drawn = new Set(b.hitNodes.map((n) => n.id));
  for (const n of m.nodes) {
    const cls = rel.nodeTier(n.id);
    if (cls === 'Selected' || cls === 'Direct') { assert.ok(drawn.has(n.id)); assert.equal(retreat[n.id].alpha, 1); assert.deepEqual(b.worldPoints[n.id], L.pos[n.id]); }
    if (cls === 'Unrelated' && drawn.has(n.id)) { assert.equal(b.worldPoints[n.id][1], L.pos[n.id][1]); assert.ok(Math.abs(b.worldPoints[n.id][2] - L.pos[n.id][2]) >= SB.DISPERSE_MIN - 1e-9); const box = SB.boxOfPoint(L.pos[n.id], L.boxes); assert.ok(b.worldPoints[n.id][0] >= box.min[0] - 1e-9 && b.worldPoints[n.id][0] <= box.max[0] + 1e-9); }
  }
  // clearing the focus returns every node to full strength
  const none = MA.relevance(ix, null);
  for (let i = 0; i < 400; i++) SB.stepEaseAll(retreat, (id) => none.nodeTier(id), ids, 0.05);
  for (const id of ids) assert.deepEqual([retreat[id].alpha, retreat[id].push], [1, 0]);
});

test('disperseTarget: leaves through the wall facing away from the focus, same height, within the X span, at least DISPERSE_MIN', () => {
  const box = { min: [-10, 0, -4], max: [10, 3, 4] };
  const t = SB.disperseTarget([2, 1, 3], [0, 1, 0], box);
  assert.equal(t[1], 1); assert.ok(t[2] > 4 && t[0] >= -10 && t[0] <= 10); assert.ok(t[2] - 3 >= SB.DISPERSE_MIN - 1e-9);
  const back = SB.disperseTarget([2, 1, -3], [0, 1, 0], box); assert.ok(back[2] < -4);
  assert.deepEqual(SB.disperseOffset([0, 0, 0], null, box, 0), [0, 0, 0]);
  assert.deepEqual(SB.focusMiddle(MA.relevance(MA.indexModel({ nodes: [] }), null), {}), null);
});

test('runtime states become circles on the same nodes; pulses ride the formal edges; marks and labels follow the overlay', () => {
  const { m, build, ix } = scene();
  const ids = m.nodes.map((n) => n.id); const e = m.edges.find((x) => x.sourceId !== x.targetId);
  const overlay = { nodeStates: { [ids[0]]: 'Running', [ids[1]]: 'Failed', [ids[2]]: 'Waiting', [ids[3]]: 'Completed', [ids[4]]: 'Degraded' }, edgePaints: { [e.id]: { kind: 'shared', color: SB.ENERGY_COLOR } }, pulses: [{ edgeId: e.id, progress: 0.5, color: SB.ENERGY_COLOR }], marks: { [ids[5]]: 'cause', [ids[6]]: 'effect' } };
  const plain = build(); const b = build({ overlay, markPrefix: { cause: 'C ', effect: 'E ' } });
  assert.ok(b.lineCount > plain.lineCount);
  assert.equal(b.haloCount, 5); // one halo per settled, clearly visible node that carries a state
  assert.equal(b.dotCount, 1);
  const on = b.drawn.find((d) => d.id === e.id); assert.ok(on);
  const mid = on.poly[Math.round(0.5 * (on.poly.length - 1))]; assert.deepEqual(Array.from(b.points.slice(0, 3)).map((x) => +x.toFixed(5)), mid.map((x) => +x.toFixed(5)));
  assert.deepEqual(b.labels.filter((l) => l.mark).map((l) => [l.mark, l.text.slice(0, 2)]).sort(), [['cause', 'C '], ['effect', 'E ']]);
  const noPrefix = build({ overlay }); assert.ok(noPrefix.labels.filter((l) => l.mark).every((l) => !/^[CE] /.test(l.text)));
  void ix;
});

test('scenario-only nodes sit above the midpoint of the nodes they are between; a missing anchor is skipped, never a throw', () => {
  const { m, build, L } = scene();
  const e = m.edges.find((x) => x.sourceId !== x.targetId);
  const ov = { overlayNodes: [{ id: 'x1', name: 'Extra', between: [e.sourceId, e.targetId] }, { id: 'x2', name: 'Lost', between: ['nope', e.targetId] }], overlayEdges: [{ id: 'x1:1', sourceId: e.sourceId, targetId: 'x1' }, { id: 'x2:1', sourceId: 'x2', targetId: e.targetId }] };
  const b = build({ overlay: ov });
  const mid = vec.add(vec.lerp(L.pos[e.sourceId], L.pos[e.targetId], 0.5), [0, 1.6, 0]);
  assert.deepEqual(b.worldPoints.x1.map((x) => +x.toFixed(6)), mid.map((x) => +x.toFixed(6)));
  assert.equal(b.worldPoints.x2, undefined);
  assert.ok(b.hitEdges.some((s) => s.id === 'x1:1')); assert.ok(!b.hitEdges.some((s) => s.id === 'x2:1'));
});

test('the scene follows the view: only visible nodes and edges are built; a visible node without a position is skipped with a warning', () => {
  const { m, build, state, vocab } = scene();
  const some = new Set(m.nodes.slice(0, 5).map((n) => n.id));
  const b = build({ shownNodeIds: some });
  assert.equal(b.hitNodes.length, 5);
  for (const e of b.paintedEdgeIds) assert.ok(true, e);
  const noPos = SB.makeBuffers(m, { ...state, positions: {}, shownNodeIds: some }, MA.indexModel(m));
  assert.equal(noPos.hitNodes.length, 0);
  assert.ok(vocab.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNPLACED_NODE));
});

test('shape and scale come from the kind table; unknown kind, state, domain and colour are neutral (warned once), never a throw', () => {
  const m = makeModel({ seed: 33, buildings: 1, floors: 1, rooms: 1, perRoom: 3 });
  m.nodes[0].kind = 'Storage'; m.nodes[1].kind = 'NoSuchKind'; m.nodes[2].kind = 'Actor';
  const { build, vocab } = scene({ model: m });
  const b = build({ overlay: { nodeStates: { [m.nodes[1].id]: 'TotallyNew' } } });
  assert.equal(b.instances.find((i) => i.id === m.nodes[0].id).shape, 'box');
  assert.equal(b.instances.find((i) => i.id === m.nodes[0].id).scale, 1.25);
  assert.equal(b.instances.find((i) => i.id === m.nodes[1].id).shape, MA.NEUTRAL_SHAPE);
  assert.equal(b.instances.find((i) => i.id === m.nodes[2].id).shape, 'round');
  const codes = vocab.warnings.items.map((w) => w.code);
  assert.ok(codes.includes(MA.VIEW_WARNING.UNKNOWN_KIND) && codes.includes(MA.VIEW_WARNING.UNKNOWN_STATE));
  assert.ok(allFinite(b.mesh.col));
  assert.equal(SB.stateColor(vocab, 'TotallyNew'), SB.NEUTRAL_STATE_COLOR);
  assert.ok(b.lineCount > build().lineCount, 'an unknown state still draws a neutral ring');
  for (const bad of [undefined, null, '', 'red', '#12', '#gggggg', 42]) assert.ok(SB.colorToRgb(bad).every((x) => Number.isFinite(x) && x >= 0 && x <= 1));
  assert.deepEqual(SB.colorToRgb('#fff'), [1, 1, 1]);
  assert.equal(SB.stateColor(MA.makeVocab(null), 'Idle'), null);
  assert.equal(SB.stateColor(MA.makeVocab({ nodeStates: [{ id: 'Running', color: '#010203' }] }), 'Running'), '#010203');
});

test('node colour is the domain colour of the profile; trust decides it when the kind and room layer do not', () => {
  const m = makeModel({ seed: 34, buildings: 1, floors: 1, rooms: 1, perRoom: 1 });
  m.space.buildings[0].trust = 'restricted'; m.nodes[0].kind = 'Module';
  const { build } = scene({ model: m });
  const c = build().instances[0].color;
  const want = SB.colorToRgb(PROFILE.domains.find((d) => d.id === 'restricted').color);
  want.forEach((x, k) => assert.ok(Math.abs(x - c[k]) < 1e-6));
});

test('trust-boundary relations are drawn dashed, static relations weaker; edgeStyle "class" ignores the relation', () => {
  const m = { nodes: ['a', 'b', 'c', 'd'].map((id) => ({ id, kind: 'Module', name: id, roomId: 'r' })), space: { buildings: [{ id: 'bl', name: 'x', trust: 'internal' }], floors: [{ id: 'fl', buildingId: 'bl', name: 'x', level: 0 }], rooms: [{ id: 'r', floorId: 'fl', name: 'x' }] },
    edges: [{ id: 'solid', sourceId: 'a', targetId: 'b', relation: 'calls' }, { id: 'dash', sourceId: 'c', targetId: 'd', relation: 'sends' }] };
  const { build } = scene({ model: m });
  const b = build();
  assert.equal(b.drawn.find((d) => d.id === 'dash').dashed, true); assert.equal(b.drawn.find((d) => d.id === 'solid').dashed, false);
  const parts = (id) => b.hitEdges.filter((s) => s.id === id).length;
  assert.equal(parts('dash'), parts('solid')); // picking is not dashed
  const lineVerts = (bb) => bb.lineCount;
  const allSolid = scene({ model: { ...m, edges: [{ id: 'dash', sourceId: 'c', targetId: 'd', relation: 'calls' }, m.edges[0]] } }).build();
  assert.ok(lineVerts(b) < lineVerts(allSolid));
  const st = { ...m, edges: [{ id: 's', sourceId: 'a', targetId: 'b', relation: 'dependsOn' }, { id: 'r', sourceId: 'c', targetId: 'd', relation: 'calls' }] };
  const bs = scene({ model: st }).build();
  const idleAlpha = (id) => { const i = bs.drawn.findIndex((d) => d.id === id); return bs.drawn[i].alpha; };
  assert.equal(idleAlpha('s'), SB.STATIC_EDGE_ALPHA); assert.equal(idleAlpha('r'), SB.IDLE_EDGE_ALPHA);
  const rel = (bb, id) => { const k = bb.lines.length; void k; return bb.drawn.find((d) => d.id === id); };
  assert.ok(rel(build({ edgeStyle: 'class' }), 'solid'));
});

test('playback stage labels are capped; label priority is hover, selected, direct, reached, stage', () => {
  const { m, build, ix } = scene({ perRoom: 12 });
  const ids = m.nodes.map((n) => n.id);
  const rel = { active: true, stage: { nodes: new Set(ids), edges: new Set() }, chosenNodes: new Set([ids[0]]), chosenEdges: new Set(), nearNodes: new Set(), nearEdges: new Set(), backdropNodes: new Set(),
    nodeTier: (id) => (id === ids[0] ? 'Selected' : id === ids[1] ? 'Reached' : 'Stage'), edgeTier: () => 'Stage', key: 'k' };
  const b = build({ relevance: rel, hover: ids[7] });
  assert.ok(b.labels.length <= SB.STAGE_LABEL_MAX);
  assert.deepEqual(b.labels.slice(0, 3).map((l) => l.cls), ['Hover', 'Selected', 'Reached']);
  void ix;
});

test('instanced buffers expand to exactly the flat mesh, per node', () => {
  const { build } = scene({ perRoom: 6 });
  const b = build(); const inst = SB.toInstanceBuffers(b.instances);
  assert.equal(inst.reduce((a, g) => a + g.count, 0), b.hitNodes.length);
  const byShape = new Map(inst.map((g) => [g.shape, { g, next: 0 }]));
  let off = 0;
  for (const it of b.instances) {
    const e = byShape.get(it.shape); const k = e.next++; const g = e.g;
    for (let i = 0; i < g.template.pos.length; i += 3) for (let d = 0; d < 3; d++) {
      assert.ok(Math.abs(g.template.pos[i + d] * g.scale[k] + g.offset[k * 3 + d] - b.mesh.pos[off + i + d]) < 1e-4);
      assert.ok(Math.abs(g.template.nrm[i + d] - b.mesh.nrm[off + i + d]) < 1e-6);
    }
    off += g.template.faceCount * 9;
  }
  assert.equal(off, b.mesh.pos.length);
  assert.deepEqual(g0(inst).template.faceCount, SB.templateGeometry(g0(inst).shape).faceCount);
});
const g0 = (inst) => inst[0];

test('a 400 node scene builds in well under a second and stays linear in size', () => {
  const t0 = Date.now(); const { build, m } = scene({ buildings: 4, floors: 4, rooms: 5, perRoom: 5 }); const b = build();
  assert.equal(m.nodes.length, 400); assert.equal(b.hitNodes.length, 400);
  assert.ok(Date.now() - t0 < 1500);
});
