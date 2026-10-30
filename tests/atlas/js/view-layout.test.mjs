import test from 'node:test';
import assert from 'node:assert/strict';
import { makeModel, PROFILE, allFinite, FIXTURE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import { vec } from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';

const V = () => MA.makeVocab(PROFILE);
const inside = (p, b, eps = 1e-9) => p.every((x, k) => x >= b.min[k] - eps && x <= b.max[k] + eps);
const contains = (outer, inner) => inner.min.every((x, k) => x >= outer.min[k] - 1e-9) && inner.max.every((x, k) => x <= outer.max[k] + 1e-9);
const overlap1D = (a0, a1, b0, b1) => a0 < b1 - 1e-9 && b0 < a1 - 1e-9;

test('every view is deterministic, finite, and positions only depend on the model', () => {
  const m = makeModel({ seed: 3, perRoom: 5 }); const v = V();
  for (const view of LY.VIEW_IDS) {
    const a = LY.arrangeView(m, view, { vocab: v }); const b = LY.arrangeView(structuredClone(m), view, { vocab: V() });
    assert.deepEqual(a.pos, b.pos, view);
    assert.ok(allFinite(a.pos) && allFinite(a.boxes), view);
    for (const id of a.shownNodeIds) assert.ok(a.pos[id], `${view}: ${id} is visible but has no position`);
    for (const id of a.shownEdgeIds) { const e = MA.indexModel(m).edges.get(id); assert.ok(a.shownNodeIds.has(e.sourceId) && a.shownNodeIds.has(e.targetId)); }
  }
});

test('building order is array order: reversing space.buildings mirrors the buildings along X', () => {
  const m = makeModel({ seed: 11, buildings: 3, perRoom: 3 }); const v = V();
  const first = LY.arrangeView(m, 'logical', { vocab: v });
  const rev = structuredClone(m); rev.space.buildings.reverse();
  const second = LY.arrangeView(rev, 'logical', { vocab: v });
  const cx = (L, id) => (L.boxes.buildings[id].min[0] + L.boxes.buildings[id].max[0]) / 2;
  const ids = m.space.buildings.map((b) => b.id);
  assert.ok(cx(first, ids[0]) < cx(first, ids[1]) && cx(first, ids[1]) < cx(first, ids[2]));
  assert.ok(cx(second, ids[0]) > cx(second, ids[1]) && cx(second, ids[1]) > cx(second, ids[2]));
  // the scene stays centred on X = 0
  const xs = Object.values(first.boxes.buildings); const lo = Math.min(...xs.map((b) => b.min[0])); const hi = Math.max(...xs.map((b) => b.max[0]));
  assert.ok(Math.abs(lo + hi) < 1e-9);
});

test('floor height follows `level`, not array order; repeated or invalid levels still give distinct finite heights', () => {
  const m = makeModel({ seed: 12, buildings: 1, floors: 3, rooms: 1, perRoom: 2, levels: [2, 0, 1] });
  const L = LY.arrangeView(m, 'logical', { vocab: V() });
  const y = (f) => L.boxes.floors[f.id].min[1];
  const fl = m.space.floors;
  assert.ok(y(fl[1]) < y(fl[2]) && y(fl[2]) < y(fl[0]));
  const dup = makeModel({ seed: 12, buildings: 1, floors: 3, rooms: 1, perRoom: 2, levels: [0, 0, 'x'] });
  const v = V(); const D = LY.arrangeView(dup, 'logical', { vocab: v });
  const ys = dup.space.floors.map((f) => D.boxes.floors[f.id].min[1]);
  assert.equal(new Set(ys).size, 3);
  assert.ok(allFinite(D.pos));
  assert.equal(v.warnings.items.filter((w) => w.code === MA.VIEW_WARNING.FLOOR_LEVEL).length, 2);
  assert.equal(LY.floorLevels(dup, V()).get(dup.space.floors[0].id), 0); // the first claim keeps its level
});

test('boxes nest (rooms in floors in buildings) and every node lies inside its room box', () => {
  const m = makeModel({ seed: 13, perRoom: 7 }); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'logical', { vocab: V(), ix });
  for (const n of m.nodes) assert.ok(inside(L.pos[n.id], L.boxes.rooms[n.roomId]), n.id);
  for (const r of m.space.rooms) { const f = ix.floors.get(r.floorId); assert.ok(contains(L.boxes.floors[f.id], L.boxes.rooms[r.id])); assert.ok(contains(L.boxes.buildings[f.buildingId], L.boxes.floors[f.id])); }
});

test('rooms of one floor and nodes of one room never overlap; buildings are separated', () => {
  const m = makeModel({ seed: 14, perRoom: 9 }); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'logical', { vocab: V(), ix });
  const byFloor = new Map();
  for (const r of m.space.rooms) { if (!byFloor.has(r.floorId)) byFloor.set(r.floorId, []); byFloor.get(r.floorId).push(L.boxes.rooms[r.id]); }
  for (const rs of byFloor.values()) for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) assert.ok(!overlap1D(rs[i].min[0], rs[i].max[0], rs[j].min[0], rs[j].max[0]));
  const pts = Object.values(L.pos); const seen = new Set(pts.map((p) => p.map((x) => x.toFixed(6)).join(',')));
  assert.equal(seen.size, pts.length);
  const bs = Object.values(L.boxes.buildings).sort((a, b) => a.min[0] - b.min[0]);
  for (let i = 1; i < bs.length; i++) assert.ok(bs[i].min[0] >= bs[i - 1].max[0] + LY.BUILDING_GAP - 1e-9);
});

test('a room grows with its node count (no fixed room box)', () => {
  const small = LY.arrangeView(makeModel({ seed: 15, buildings: 1, floors: 1, rooms: 1, perRoom: 4 }), 'logical', { vocab: V() });
  const big = LY.arrangeView(makeModel({ seed: 15, buildings: 1, floors: 1, rooms: 1, perRoom: 100 }), 'logical', { vocab: V() });
  const w = (L) => Object.values(L.boxes.rooms)[0].max[0] - Object.values(L.boxes.rooms)[0].min[0];
  assert.ok(w(big) > 4 * w(small));
  for (const L of [small, big]) for (const p of Object.values(L.pos)) assert.ok(inside(p, Object.values(L.boxes.rooms)[0]));
});

test('nodes that resolve to no building go to the unassigned region, never dropped, never a throw', () => {
  const m = makeModel({ seed: 16, unknownBuilding: true, orphans: 3, perRoom: 3 }); const v = V();
  const L = LY.arrangeView(m, 'logical', { vocab: v });
  assert.ok(L.unassigned.length >= 3);
  for (const n of m.nodes) assert.ok(L.pos[n.id], `${n.id} was dropped`);
  const box = L.boxes.rooms[MA.UNASSIGNED_ID];
  for (const id of L.unassigned) assert.ok(inside(L.pos[id], box));
  assert.ok(L.boxes.buildings[MA.UNASSIGNED_ID] && L.boxes.floors[MA.UNASSIGNED_ID]);
  // the region is its own key, so a real building called "unassigned" cannot collide with it
  assert.ok(MA.UNASSIGNED_ID.startsWith('~'));
  const real = Object.entries(L.boxes.buildings).filter(([k]) => k !== MA.UNASSIGNED_ID);
  for (const [, b] of real) assert.ok(b.max[0] <= box.min[0] + 1e-9);
  assert.ok(v.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNPLACED_NODE));
  // a model with no space at all is one unassigned region
  const flat = LY.arrangeView({ nodes: [{ id: 'a1', kind: 'X', name: 'A' }, { id: 'a2', kind: 'X', name: 'B' }], edges: [] }, 'logical', { vocab: V() });
  assert.deepEqual(flat.unassigned, ['a1', 'a2']);
});

test('a level filter makes the 3D layout compact: hidden nodes take no space, empty frames disappear', () => {
  const m = makeModel({ seed: 17, perRoom: 6 }); const v = V(); const ix = MA.indexModel(m);
  const full = LY.arrangeView(m, 'logical', { vocab: v, ix });
  const vis = MA.shownAtLevel(m, 'REQ', v, ix);
  const cut = LY.arrangeView(m, 'logical', { vocab: v, ix, vis });
  assert.equal(cut.shownNodeIds.size, vis.nodeIds.size);
  assert.ok(cut.shownNodeIds.size < full.shownNodeIds.size);
  assert.deepEqual(Object.keys(cut.pos).sort(), [...vis.nodeIds].sort());
  assert.ok(Object.keys(cut.boxes.rooms).length <= Object.keys(full.boxes.rooms).length);
  const width = (L) => Math.max(...Object.values(L.boxes.buildings).map((b) => b.max[0])) - Math.min(...Object.values(L.boxes.buildings).map((b) => b.min[0]));
  assert.ok(width(cut) < width(full));
  for (const id of cut.shownEdgeIds) assert.ok(vis.edgeIds.has(id));
});

test('views take node filters and relation membership from the profile', () => {
  const m = makeModel({ seed: 18, perRoom: 6 }); const v = V(); const ix = MA.indexModel(m);
  const dev = LY.arrangeView(m, 'development', { vocab: v, ix });
  const devKinds = new Set(PROFILE.views.find((x) => x.id === 'development-view').kinds);
  for (const id of dev.shownNodeIds) assert.ok(devKinds.has(ix.nodes.get(id).kind));
  for (const id of dev.shownEdgeIds) assert.ok(MA.relationInfo(v, ix.edges.get(id).relation).views.includes('development-view'));
  const data = LY.arrangeView(m, 'data', { vocab: v, ix });
  const dataKinds = new Set(PROFILE.views.find((x) => x.id === 'data-view').kinds);
  const core = [...data.shownNodeIds].filter((id) => dataKinds.has(ix.nodes.get(id).kind));
  assert.ok(core.length > 0);
  // any view the profile defines works, also by its profile id
  const byProfileId = LY.arrangeView(m, 'development-view', { vocab: v, ix });
  assert.deepEqual(byProfileId.pos, dev.pos);
  const custom = structuredClone(PROFILE); custom.views.push({ id: 'my-view', kinds: ['Actor'], label: { 'en-US': 'x' } });
  const cv = MA.makeVocab(custom); const L = LY.arrangeView(m, 'my-view', { vocab: cv, ix });
  for (const id of L.shownNodeIds) assert.equal(ix.nodes.get(id).kind, 'Actor');
});

test('an unknown view id falls back to the logical layout with a warning', () => {
  const m = makeModel({ seed: 19 }); const v = V();
  const L = LY.arrangeView(m, 'no-such-view', { vocab: v });
  assert.deepEqual(L.pos, LY.arrangeView(m, 'logical', { vocab: V() }).pos);
  assert.ok(v.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNKNOWN_VIEW));
});

test('process view: one lane (Z) per case, rows by the kind lane of the profile', () => {
  const m = makeModel({ seed: 20, cases: 4 }); const v = V(); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'process', { vocab: v, ix });
  const first = new Map();
  m.cases.forEach((c, ci) => { for (const e of c.events) for (const id of [e.from, e.to]) if (!first.has(id)) first.set(id, ci); });
  assert.deepEqual([...L.shownNodeIds].sort(), [...first.keys()].sort());
  for (const [id, ci] of first) {
    assert.ok(Math.abs(L.pos[id][2] - (ci - (m.cases.length - 1) / 2) * 2.4) < 1e-9);
    assert.ok(Math.abs(L.pos[id][1] - MA.kindInfo(v, ix.nodes.get(id).kind).lane * 1.6) < 1e-9);
  }
  const none = LY.arrangeView(makeModel({ seed: 20, noCases: true }), 'process', { vocab: V() });
  assert.equal(none.shownNodeIds.size, 0);
});

test('security view: one X zone per trust value in profile order, unknown trust gets its own zone', () => {
  const m = makeModel({ seed: 21, buildings: 4, unknownTrust: true, perRoom: 3 }); const v = V(); const ix = MA.indexModel(m);
  for (const n of m.nodes) n.props.trust = MA.trustOf(n, ix);
  const L = LY.arrangeView(m, 'security', { vocab: v, ix });
  const zones = Object.keys(L.boxes.buildings).map((k) => k.replace('~trust.', ''));
  assert.ok(zones.includes('no-such-zone'));
  const profileZones = zones.filter((z) => PROFILE.trust.some((t) => t.id === z));
  assert.deepEqual(profileZones, PROFILE.trust.map((t) => t.id).filter((z) => zones.includes(z)));
  const cx = (z) => { const b = L.boxes.buildings[`~trust.${z}`]; return (b.min[0] + b.max[0]) / 2; };
  for (let i = 1; i < zones.length; i++) assert.ok(cx(zones[i]) > cx(zones[i - 1]));
  for (const n of m.nodes) assert.ok(inside(L.pos[n.id], L.boxes.buildings[`~trust.${n.props.trust}`]), n.id);
  assert.ok(v.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNKNOWN_TRUST));
});

test('data view: bands do not overlap each other', () => {
  const m = makeModel({ seed: 22, perRoom: 8 }); const v = V(); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'data', { vocab: v, ix });
  const byRow = new Map();
  for (const id of L.shownNodeIds) { const p = L.pos[id]; const key = `${p[1]}`; if (!byRow.has(key)) byRow.set(key, []); byRow.get(key).push(p); }
  const all = [...L.shownNodeIds].map((id) => L.pos[id].map((x) => x.toFixed(6)).join(','));
  assert.equal(new Set(all).size, all.length);
  assert.ok(byRow.size >= 2);
});

test('dependency view: a node sits at least one layer above everything it points to (cycles are ignored, not fatal)', () => {
  const m = makeModel({ seed: 23, perRoom: 5 }); const v = V(); const ix = MA.indexModel(m);
  const L = LY.arrangeView(m, 'dependency', { vocab: v, ix });
  assert.ok(L.shownNodeIds.size > 0);
  const y = (id) => Math.round(L.pos[id][1] / LY.FLOOR_H);
  let forward = 0; let backEdges = 0;
  for (const id of L.shownEdgeIds) { const e = ix.edges.get(id); if (e.sourceId === e.targetId) continue; if (y(e.sourceId) > y(e.targetId)) forward++; else backEdges++; }
  assert.ok(forward > backEdges, `${forward} lower vs ${backEdges} other`);
  const layered = { nodes: ['a', 'b', 'c', 'd'].map((id) => ({ id, kind: 'Module', name: id })), edges: [['a', 'b'], ['b', 'c'], ['a', 'c'], ['c', 'd']].map(([s, t]) => ({ id: `${s}:dependsOn:${t}`, sourceId: s, targetId: t, relation: 'dependsOn' })) };
  const A = LY.arrangeView(layered, 'dependency', { vocab: V() });
  assert.deepEqual(['a', 'b', 'c', 'd'].map((id) => Math.round(A.pos[id][1] / LY.FLOOR_H)), [3, 2, 1, 0]);
  const cyc = { nodes: ['a', 'b', 'c'].map((id) => ({ id, kind: 'Module', name: id })), edges: [['a', 'b'], ['b', 'a'], ['a', 'c']].map(([s, t]) => ({ id: `${s}:dependsOn:${t}`, sourceId: s, targetId: t, relation: 'dependsOn' })) };
  const C = LY.arrangeView(cyc, 'dependency', { vocab: V() });
  assert.deepEqual(['a', 'b', 'c'].map((id) => Math.round(C.pos[id][1] / LY.FLOOR_H)), [2, 1, 0]); // the back edge b -> a counts as one level, so the cycle still stacks
  const deep = { nodes: Array.from({ length: 6000 }, (_, i) => ({ id: `d${i}`, kind: 'Module', name: `${i}` })), edges: Array.from({ length: 5999 }, (_, i) => ({ id: `d${i}:dependsOn:d${i + 1}`, sourceId: `d${i}`, targetId: `d${i + 1}`, relation: 'dependsOn' })) };
  assert.doesNotThrow(() => LY.arrangeView(deep, 'dependency', { vocab: V() })); // iterative walk, no stack overflow
});

test('cardLayout: every shown node has one card inside its room, rooms inside floors inside buildings, no overlap', () => {
  const m = makeModel({ seed: 24, perRoom: 5, orphans: 2, unknownBuilding: true }); const v = V(); const ix = MA.indexModel(m);
  const L = LY.cardLayout(m, null, { vocab: v, ix });
  assert.equal(L.nodes.length, m.nodes.length);
  const box = (a) => ({ min: [a.x, a.y], max: [a.x + a.w, a.y + a.h] });
  const within = (a, b) => a.x >= b.x - 1e-9 && a.y >= b.y - 1e-9 && a.x + a.w <= b.x + b.w + 1e-9 && a.y + a.h <= b.y + b.h + 1e-9;
  const room = new Map(L.rooms.map((r) => [r.id, r])); const floor = new Map(L.floors.map((f) => [f.id, f])); const bld = new Map(L.buildings.map((b) => [b.id, b]));
  for (const n of L.nodes) assert.ok(within(n, room.get(n.roomId)), n.id);
  for (const r of L.rooms) { assert.ok(within(r, floor.get(r.floorId))); assert.ok(within(floor.get(r.floorId), bld.get(r.buildingId))); }
  for (let i = 0; i < L.nodes.length; i++) for (let j = i + 1; j < L.nodes.length; j++) { const a = box(L.nodes[i]); const b = box(L.nodes[j]); assert.ok(!(overlap1D(a.min[0], a.max[0], b.min[0], b.max[0]) && overlap1D(a.min[1], a.max[1], b.min[1], b.max[1]))); }
  assert.equal(L.buildings.at(-1).id, MA.UNASSIGNED_ID);
  assert.equal(L.buildings.at(-1).unassigned, true);
  assert.ok(allFinite(L.nodes) && allFinite(L.bounds));
  for (const e of L.edges) assert.ok(!/NaN|Infinity/.test(e.d), e.id);
});

test('cardLayout: buildings run left to right in array order and higher floors are drawn above lower ones', () => {
  const m = makeModel({ seed: 25, buildings: 3, floors: 3, levels: [0, 2, 1] }); const L = LY.cardLayout(m, null, { vocab: V() });
  const xs = m.space.buildings.map((b) => L.buildings.find((x) => x.id === b.id).x);
  assert.ok(xs[0] < xs[1] && xs[1] < xs[2]);
  const f = m.space.floors.filter((x) => x.buildingId === m.space.buildings[0].id);
  const yy = (id) => L.floors.find((x) => x.id === id).y;
  assert.ok(yy(f[1].id) < yy(f[2].id) && yy(f[2].id) < yy(f[0].id)); // level 2 on top, then 1, then 0
});

test('cardLayout at a level: hidden nodes take no space', () => {
  const m = makeModel({ seed: 26, perRoom: 6 }); const v = V(); const ix = MA.indexModel(m);
  const full = LY.cardLayout(m, null, { vocab: v, ix }); const vis = MA.shownAtLevel(m, 'REQ', v, ix);
  const cut = LY.cardLayout(m, vis, { vocab: v, ix });
  assert.equal(cut.nodes.length, vis.nodeIds.size);
  assert.ok(cut.bounds.w * cut.bounds.h < full.bounds.w * full.bounds.h);
  for (const e of cut.edges) assert.ok(vis.edgeIds.has(e.id));
});

test('cardLayout edges carry the relation colour, the trust-boundary dash flag and the static flag from the profile', () => {
  const m = makeModel({ seed: 27, perRoom: 4 }); const v = V();
  const L = LY.cardLayout(m, null, { vocab: v });
  for (const e of L.edges) { const info = MA.relationInfo(v, e.relation); assert.equal(e.color, info.color); assert.equal(e.dashed, info.trustBoundary); assert.equal(e.static, info.static); }
  assert.ok(L.edges.some((e) => e.dashed) && L.edges.some((e) => !e.dashed));
});

test('cardFocus: related cards stay, unrelated cards leave through the side away from the selection, rooms without related cards dim', () => {
  const m = makeModel({ seed: 28, perRoom: 5 }); const v = V(); const ix = MA.indexModel(m);
  const L = LY.cardLayout(m, null, { vocab: v, ix });
  const rel = MA.relevance(ix, { type: 'node', id: m.nodes[0].id });
  const F = LY.cardFocus(L, rel);
  for (const n of L.nodes) {
    const cls = rel.nodeTier(n.id);
    if (cls === 'Unrelated') { const [dx, dy] = F.offsets.get(n.id); assert.ok(Math.abs(dy) >= LY.DISPERSE_2D_MIN); assert.ok(Number.isFinite(dx)); assert.ok(F.delays.get(n.id) >= 0); } else assert.ok(!F.offsets.has(n.id));
  }
  assert.ok(F.away.rooms.size > 0);
  const keptRoom = L.nodes.find((n) => n.id === m.nodes[0].id).roomId; assert.ok(!F.away.rooms.has(keptRoom));
  assert.equal(LY.cardFocus(L, MA.relevance(ix, null)).offsets.size, 0);
});

test('gem geometry: closed, outward-oriented triangle lists with unit normal vectors; unknown shape is a box', () => {
  for (const s of LY.gemShapes()) {
    const faces = LY.gemFaces(s); assert.ok(faces.length >= 8, s);
    const edges = new Map(); const key = (pt) => pt.map((x) => Math.round(x * 1e4)).join(',');
    for (const t of faces) for (let i = 0; i < 3; i++) { const a = key(t[i]); const b = key(t[(i + 1) % 3]); edges.set(`${a}>${b}`, (edges.get(`${a}>${b}`) ?? 0) + 1); }
    for (const [k, c] of edges) { const [a, b] = k.split('>'); assert.equal(c, 1, `${s}: ${k}`); assert.equal(edges.get(`${b}>${a}`), 1, `${s}: open edge ${k}`); }
    for (const t of faces) { const n = LY.faceNormal(t); assert.ok(Math.abs(vec.len(n) - 1) < 1e-9); const c = vec.scale(vec.add(vec.add(t[0], t[1]), t[2]), 1 / 3); assert.ok(vec.dot(n, c) > 0, `${s}: inward face`); }
    assert.ok(LY.gemOutline(s).split(' ').length >= 3);
  }
  assert.deepEqual(LY.gemFaces('no-such-shape'), LY.gemFaces('cube'));
  assert.equal(LY.gemOutline('no-such-shape').split(' ').length, 4);
});

test('blendPositions blends from the current position and keeps new nodes at their target', () => {
  const out = LY.blendPositions({ a: [0, 0, 0] }, { a: [10, 0, 0], b: [1, 1, 1] }, 0.25);
  assert.deepEqual(out.a, [2.5, 0, 0]); assert.deepEqual(out.b, [1, 1, 1]);
});

test('layoutBounds frames every node and every box corner', () => {
  const m = makeModel({ seed: 29 }); const L = LY.arrangeView(m, 'logical', { vocab: V() });
  const b = LY.layoutBounds(L);
  for (const p of Object.values(L.pos)) assert.ok(inside(p, b));
  for (const g of Object.values(L.boxes)) for (const bx of Object.values(g)) assert.ok(contains(b, bx));
  assert.equal(LY.layoutBounds({ pos: {}, boxes: {} }), null);
});

test('the fixture model lays out in every view', () => {
  for (const view of LY.VIEW_IDS) { const L = LY.arrangeView(FIXTURE, view, { vocab: V() }); assert.ok(allFinite(L.pos)); }
});
