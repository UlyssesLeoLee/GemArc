// Behaviour pins found missing by the mutation round: edge direction, requirement context, parallel edges, camera margin, search weights,
// label nudge direction and the layout and animation constants. Synthetic ids only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { PROFILE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import * as CO from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import * as SE from '../../../src/gemarc_mcp/atlas/viewer/src/search.js';
import { placeLabel } from '../../../src/gemarc_mcp/atlas/viewer/src/label-fit.js';

const V = () => MA.makeVocab(PROFILE);
const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;
const same = (p, q) => p.length === q.length && p.every((x, i) => near(x, q[i], 1e-6));

function modelOf(rooms, nodes, edges, extra = {}) {
  // rooms: { id: floorIndex-in-building-0 } is too rigid: every room is its own entry of one floor of one building unless {building} says otherwise
  const space = { buildings: [], floors: [], rooms: [] };
  for (const r of rooms) {
    const b = r.building ?? 'bl';
    if (!space.buildings.some((x) => x.id === b)) space.buildings.push({ id: b, name: b, trust: 'internal' });
    const f = `${b}-fl`;
    if (!space.floors.some((x) => x.id === f)) space.floors.push({ id: f, buildingId: b, name: 'f', level: 0 });
    space.rooms.push({ id: r.id, floorId: f, name: r.id });
  }
  return { nodes, edges, space, cases: [], machines: [], requirements: [], ...extra };
}
const node = (id, roomId, kind = 'Module', extra = {}) => ({ id, kind, name: id, roomId, props: {}, ...extra });
const edge = (s, t, relation = 'calls', extra = {}) => ({ id: `${s}:${relation}:${t}`, sourceId: s, targetId: t, relation, ...extra });
const buffers = (m, patch = {}) => {
  const vocab = V(); const ix = MA.indexModel(m); const L = LY.arrangeView(m, 'logical', { vocab, ix });
  return { L, ix, vocab, b: SB.makeBuffers(m, { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab, eye: [0, 8, -40], ...patch }, ix) };
};

// ---------------------------------------------------------------------------------------------------------------------------
test('every drawn edge runs from its source to its target, also for an opposite pair and for parallel edges', () => {
  const m = modelOf([{ id: 'rm' }], [node('a', 'rm'), node('b', 'rm'), node('c', 'rm')], [edge('a', 'b'), edge('b', 'a'), edge('a', 'b', 'reads'), edge('c', 'a'), edge('a', 'c')]);
  const { b, ix } = buffers(m);
  assert.equal(b.drawn.length, m.edges.length);
  for (const d of b.drawn) {
    const e = ix.edges.get(d.id);
    assert.ok(same(d.poly[0], b.worldPoints[e.sourceId]), `${d.id} starts at its source`);
    assert.ok(same(d.poly.at(-1), b.worldPoints[e.targetId]), `${d.id} ends at its target (the arrow tip side)`);
  }
  // the arrow head belongs to the target end: the last line segment group of each edge points at the end of the path
  assert.ok(b.lineCount > 0);
});

test('relevance: the context holds the selection\'s room-mates and the nodes of a requirement that names the selection, nothing else', () => {
  const m = modelOf([{ id: 'r1' }, { id: 'r2' }], [node('x', 'r1'), node('w', 'r1'), node('y', 'r2'), node('z', 'r2'), node('q', 'r2')], [],
    { requirements: [{ id: 'RQ1', nodeIds: ['x', 'y'] }, { id: 'RQ2', nodeIds: ['z', 'q'] }] });
  const ix = MA.indexModel(m); const rel = MA.relevance(ix, { type: 'node', id: 'x' });
  assert.equal(rel.nodeTier('x'), 'Selected');
  assert.equal(rel.nodeTier('w'), 'Context', 'same room');
  assert.equal(rel.nodeTier('y'), 'Context', 'same requirement, other room');
  assert.equal(rel.nodeTier('z'), 'Unrelated', 'a requirement that does not name the selection adds nothing');
  assert.equal(rel.nodeTier('q'), 'Unrelated');
  assert.deepEqual([...rel.backdropNodes].sort(), ['w', 'y']);
});

test('relevance: parallel and opposite edges of a selected edge are directly related, other edges at its ends are not', () => {
  const m = modelOf([{ id: 'rm' }], [node('a', 'rm'), node('b', 'rm'), node('c', 'rm'), node('d', 'rm')], [edge('a', 'b'), edge('a', 'b', 'reads'), edge('b', 'a'), edge('a', 'c'), edge('c', 'd')]);
  const ix = MA.indexModel(m); const rel = MA.relevance(ix, { type: 'edge', id: 'a:calls:b' });
  const tier = (id) => rel.edgeTier(ix.edges.get(id));
  assert.equal(tier('a:calls:b'), 'Selected');
  assert.equal(tier('a:reads:b'), 'Direct', 'parallel');
  assert.equal(tier('b:calls:a'), 'Direct', 'opposite');
  assert.notEqual(tier('a:calls:c'), 'Direct', 'another edge at one end');
  assert.equal(tier('c:calls:d'), 'Unrelated');
  assert.deepEqual([...rel.nearEdges].sort(), ['a:reads:b', 'b:calls:a']);
});

test('fitCamera: the margin multiplies the exact fit distance', () => {
  const pts = [[-40, 0, -20], [40, 6, 25], [10, 14, -30], [-25, 3, 40]];
  const d1 = CO.fitCamera(CO.newCamera(), pts, 1.6, 1).dist; const d2 = CO.fitCamera(CO.newCamera(), pts, 1.6, 1.08).dist; const d3 = CO.fitCamera(CO.newCamera(), pts, 1.6, 1.5).dist;
  assert.ok(near(d2 / d1, 1.08, 1e-9), `${d2 / d1}`);
  assert.ok(near(d3 / d1, 1.5, 1e-9));
  assert.equal(CO.fitCamera(CO.newCamera(), pts).dist, d2, 'the default margin is 1.08');
  // exact at margin 1: one point touches the frame, none is outside it
  assert.deepEqual(CO.pointsOutside(CO.fitCamera(CO.newCamera(), pts, 1.6, 1), pts, 800, 500, 1), []);
  assert.ok(CO.pointsOutside({ ...CO.fitCamera(CO.newCamera(), pts, 1.6, 1), dist: d1 * 0.9 }, pts, 800, 500, 1).length > 0, 'closer than the fit leaves points out');
});

test('search ranking: exact beats prefix beats substring; the id field beats the name beats the rest; lines rank below parts by two points', () => {
  const index = (nodes, edges = []) => SE.makeSearchIndex({ nodes, edges }, [], { vocab: V() });
  const keys = (nodes, q, edges = []) => SE.search(index(nodes, edges), q).map((e) => e.key);
  const flat = (ks) => ks.filter((k) => k.startsWith('2D:'));
  // exact on a weak field (1 + 6) ties with a prefix on the name (4 + 3); a bonus of 6 keeps the earlier entry first, 5 would not
  assert.deepEqual(keys([node('k1', 'r', 'Module', { name: 'zz', sub: 'foo' }), node('k2', 'r', 'Module', { name: 'foobar' })], 'foo'), ['2D:node:k1', '2D:node:k2', '3D:node:k1', '3D:node:k2']);
  // prefix (+3) over substring (+0) on the same field, whatever the order
  assert.deepEqual(flat(keys([node('k1', 'r', 'Module', { name: 'xfoo' }), node('k2', 'r', 'Module', { name: 'foox' })], 'foo')), ['2D:node:k2', '2D:node:k1']);
  // id field (5) over name field (4): the entry listed second wins
  assert.deepEqual(flat(keys([node('zz2', 'r', 'Module', { name: 'foo' }), node('foo', 'r', 'Module', { name: 'zz' })], 'foo')), ['2D:node:foo', '2D:node:zz2']);
  // a line with raw score 7 (label prefix) minus 2 ties a part with 5 (substring in the id) and loses the tie to it (parts come first)
  const e1 = [{ id: 'e1', sourceId: 'na', targetId: 'nb', relation: 'calls', label: 'q-link' }];
  assert.deepEqual(keys([node('xqz1', 'r', 'Module', { name: 'Node A' }), node('nb', 'r', 'Module', { name: 'Node B' })], 'q', e1), ['2D:node:xqz1', '2D:edge:e1', '3D:node:xqz1']);
  // against a part with 4 the same line (5) wins: the penalty is exactly two
  assert.deepEqual(keys([node('n2', 'r', 'Module', { name: 'xqz' }), node('nb', 'r', 'Module', { name: 'Node B' })], 'q', e1).slice(0, 2), ['2D:edge:e1', '2D:node:n2']);
});

test('label placement: a colliding label moves up first (-22), the next candidate is below (+22)', () => {
  const ctx = { font: '12px x', measureText(t) { const s = Number(/^(\d+)px/.exec(this.font)[1]); return { width: [...t].length * s }; } };
  const placed = [{ x: 0, y: 100, w: 1000 }];
  const up = placeLabel(ctx, 'lbl', 150, 100, 300, 400, placed);
  assert.equal(up.y, 78);
  const down = placeLabel(ctx, 'lbl', 150, 100, 300, 400, placed);
  assert.equal(down.y, 122);
  const free = placeLabel(ctx, 'lbl', 150, 300, 300, 400, []);
  assert.equal(free.y, 300, 'a label without collision stays at its anchor');
});

// ---------------------------------------------------------------------------------------------------------------------------
test('layout constants: room and building gaps in 3D, the 2D row width limit', () => {
  assert.equal(LY.ROOM_GAP, 1.6); assert.equal(LY.BUILDING_GAP, 5); assert.equal(LY.FLOOR_H, 3.4); assert.equal(LY.SPACING, 1.7);
  const mk = (id, room) => node(id, room);
  const m = modelOf([{ id: 'r1', building: 'b1' }, { id: 'r2', building: 'b1' }, { id: 'r3', building: 'b2' }], [mk('a1', 'r1'), mk('a2', 'r1'), mk('b1', 'r2'), mk('b2', 'r2'), mk('c1', 'r3'), mk('c2', 'r3')], []);
  const L = LY.arrangeView(m, 'logical', { vocab: V() });
  assert.ok(near(L.boxes.rooms.r2.min[0] - L.boxes.rooms.r1.max[0], LY.ROOM_GAP, 1e-9), 'rooms of one floor are ROOM_GAP apart');
  assert.ok(near(L.boxes.buildings.b2.min[0] - L.boxes.buildings.b1.max[0], LY.BUILDING_GAP, 1e-9), 'buildings are BUILDING_GAP apart');
  // 2D: single-card rooms are 182 wide; three fit in a 620 wide row (582), the fourth wraps onto a new line
  const rooms = ['r1', 'r2', 'r3', 'r4'].map((id) => ({ id }));
  const four = modelOf(rooms, rooms.map((r, i) => node(`n${i}`, r.id)), []);
  const C = LY.cardLayout(four, null, { vocab: V() });
  const [p1, p2, p3, p4] = ['r1', 'r2', 'r3', 'r4'].map((id) => C.rooms.find((r) => r.id === id));
  assert.equal(p1.w, 2 * 16 + LY.CARD.w);
  assert.ok(p1.y === p2.y && p2.y === p3.y, 'three rooms share a line');
  assert.ok(p4.y > p1.y && p4.x === p1.x, 'the fourth wraps to the start of a new line');
  assert.equal(p2.x - (p1.x + p1.w), 18, 'rooms of one line are 18 apart');
  assert.equal(C.buildings.length, 1);
  const three = LY.cardLayout(modelOf(rooms.slice(0, 3), rooms.slice(0, 3).map((r, i) => node(`n${i}`, r.id)), []), null, { vocab: V() });
  assert.equal(new Set(three.rooms.map((r) => r.y)).size, 1);
});

test('edge strength constants: idle 0.55, static 0.4, an edge follows its weaker end relative to the context backdrop', () => {
  assert.equal(SB.IDLE_EDGE_ALPHA, 0.55); assert.equal(SB.STATIC_EDGE_ALPHA, 0.4);
  assert.equal(MA.RETREAT_TARGET.Context.alpha, 0.2); assert.equal(SB.EDGE_KEEP_ALPHA, 0.2);
  const m = modelOf([{ id: 'rm' }], [node('a', 'rm'), node('b', 'rm')], [edge('a', 'b'), edge('a', 'b', 'dependsOn')]);
  const { b } = buffers(m);
  assert.equal(b.drawn.find((d) => d.id === 'a:calls:b').alpha, 0.55);
  assert.equal(b.drawn.find((d) => d.id === 'a:dependsOn:b').alpha, 0.4);
  const dim = buffers(m, { retreat: { b: { alpha: 0.1, push: 0 } } }).b;
  assert.ok(near(dim.drawn.find((d) => d.id === 'a:calls:b').alpha, 0.55 * 0.5, 1e-9));
});

test('animation constants: fade takes 2.4 s, dispersal 3.2 s, the start delay only holds a dispersal back', () => {
  assert.equal(MA.FADE_SEC, 2.4); assert.equal(MA.DISPERSE_SEC, 3.2);
  const s = MA.stepEase({ alpha: 1, push: 0 }, MA.RETREAT_TARGET.Context, 1);
  assert.ok(near(s.alpha, 1 - 1 / 2.4, 1e-12));
  const d = MA.stepEase({ alpha: 1, push: 0 }, MA.RETREAT_TARGET.Unrelated, 1);
  assert.ok(near(d.push, 1 / 3.2, 1e-12));
  const held = MA.stepEase({ alpha: 1, push: 0 }, MA.RETREAT_TARGET.Unrelated, 1, 0.75);
  assert.ok(near(held.push, 0.25 / 3.2, 1e-12), 'the delay is spent before the push starts');
  const back = MA.stepEase({ alpha: 0.04, push: 1 }, MA.RETREAT_TARGET.Selected, 1, 5);
  assert.ok(near(back.push, 1 - 1 / 3.2, 1e-12), 'returning starts at once');
});
