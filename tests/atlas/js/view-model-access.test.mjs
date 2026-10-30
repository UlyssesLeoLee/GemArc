import test from 'node:test';
import assert from 'node:assert/strict';
import { makeModel, PROFILE, FIXTURE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';

const V = () => MA.makeVocab(PROFILE);

test('makeVocab: tolerates anything and exposes profile lookups', () => {
  for (const junk of [null, undefined, 0, 'x', [], {}, { kinds: 5, relations: 'x', levels: [1, 2] }]) {
    const v = MA.makeVocab(junk);
    assert.deepEqual(v.levels, ['REQ', 'BD', 'DD']);
    assert.equal(v.kinds.size, 0);
  }
  const v = V();
  assert.equal(v.kinds.get('Module').shape, 'hex');
  assert.deepEqual(v.levels, ['REQ', 'BD', 'DD']);
  assert.deepEqual(v.trustOrder, PROFILE.trust.map((t) => t.id));
});

test('kindInfo / relationInfo: known values come from the profile, unknown ones are neutral with exactly one warning', () => {
  const v = V();
  const k = MA.kindInfo(v, 'Storage');
  assert.deepEqual([k.known, k.shape, k.scale, k.lane], [true, 'box', 1.25, 0]);
  const u = MA.kindInfo(v, 'NoSuchKind'); MA.kindInfo(v, 'NoSuchKind');
  assert.deepEqual([u.known, u.shape, u.scale, u.level, u.lane], [false, MA.NEUTRAL_SHAPE, 1, 'DD', 1]);
  assert.equal(v.warnings.items.filter((w) => w.code === MA.VIEW_WARNING.UNKNOWN_KIND).length, 1);
  const r = MA.relationInfo(v, 'calls');
  assert.equal(r.known, true); assert.equal(r.trustBoundary, false); assert.ok(r.views.includes('logical-view'));
  assert.equal(MA.relationInfo(v, 'sends').trustBoundary, true);
  const ur = MA.relationInfo(v, 'nope');
  assert.deepEqual([ur.known, ur.color, ur.static], [false, MA.NEUTRAL_COLOR, false]);
  assert.deepEqual(ur.views, MA.OPEN_VIEWS);
  assert.equal(v.warnings.items.filter((w) => w.code === MA.VIEW_WARNING.UNKNOWN_RELATION).length, 1);
});

test('a missing vocabulary is a neutral scene, not a warning per node', () => {
  const v = MA.makeVocab(null);
  MA.kindInfo(v, 'Whatever'); MA.relationInfo(v, 'whatever');
  assert.equal(v.warnings.count(), 0);
  assert.equal(MA.relationInView(v, 'whatever', 'logical-view'), true);
});

test('warnings: capped per code, the remainder is counted, duplicates are ignored', () => {
  const w = MA.makeWarnings();
  for (let i = 0; i < 100; i++) w.add('C1', `s${i}`); w.add('C1', 's0'); w.add('C2', 'x');
  assert.equal(w.items.filter((x) => x.code === 'C1').length, 25);
  assert.equal(w.suppressed.C1, 75);
  assert.equal(w.count(), 25 + 75 + 1);
});

test('indexModel: field names of gemarc-atlas/1, tolerant of missing collections', () => {
  const ix = MA.indexModel(FIXTURE);
  assert.equal(ix.nodes.size, FIXTURE.nodes.length);
  assert.equal(ix.edges.size, FIXTURE.edges.length);
  assert.ok(ix.rooms.size > 0 && ix.floors.size > 0 && ix.buildings.size > 0);
  for (const n of FIXTURE.nodes) assert.ok(MA.homeOf(ix, n.id), n.id);
  const empty = MA.indexModel({}); assert.equal(empty.nodes.size, 0);
  assert.doesNotThrow(() => MA.indexModel(null));
  assert.equal(MA.homeOf(empty, 'x'), null);
});

test('homeOf is null whenever any link of node -> room -> floor -> building is broken', () => {
  const m = makeModel({ seed: 4, unknownBuilding: true, orphans: 2 });
  const ix = MA.indexModel(m);
  const unplaced = m.nodes.filter((n) => !MA.homeOf(ix, n.id));
  assert.ok(unplaced.length > 2);
  for (const n of m.nodes.filter((x) => x.id.includes('o') && x.name.startsWith('Orphan'))) assert.equal(MA.homeOf(ix, n.id), null);
});

test('levelOfNode follows model-schema 5.9: node.level, then room layer, then kind, unknown kind is DD', () => {
  const v = V(); const m = makeModel({ seed: 2, buildings: 1, floors: 1, rooms: 1, perRoom: 1 });
  m.space.rooms[0].layer = 'business';
  m.nodes[0].kind = 'Module'; // BD by kind, REQ by the business layer
  let ix = MA.indexModel(m);
  assert.equal(MA.levelOfNode(m.nodes[0], ix, v), 'REQ');
  m.nodes[0].level = 'DD'; assert.equal(MA.levelOfNode(m.nodes[0], ix, v), 'DD');
  m.nodes[0].level = 'bogus'; assert.equal(MA.levelOfNode(m.nodes[0], ix, v), 'REQ');
  assert.ok(v.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNKNOWN_LEVEL));
  delete m.nodes[0].level; delete m.space.rooms[0].layer; ix = MA.indexModel(m);
  assert.equal(MA.levelOfNode(m.nodes[0], ix, v), 'BD');
  m.nodes[0].kind = 'NoSuchKind'; assert.equal(MA.levelOfNode(m.nodes[0], ix, v), 'DD');
  assert.equal(MA.levelOfNode(undefined, ix, v), 'DD');
});

test('shownAtLevel: each level only adds (REQ in BD in DD), edges need both ends', () => {
  const v = V(); const m = makeModel({ seed: 5, perRoom: 6 }); const ix = MA.indexModel(m);
  const sets = ['REQ', 'BD', 'DD'].map((l) => MA.shownAtLevel(m, l, v, ix));
  for (let i = 0; i < 2; i++) {
    for (const id of sets[i].nodeIds) assert.ok(sets[i + 1].nodeIds.has(id));
    for (const id of sets[i].edgeIds) assert.ok(sets[i + 1].edgeIds.has(id));
  }
  assert.ok(sets[0].nodeIds.size < sets[2].nodeIds.size);
  for (const s of sets) for (const id of s.edgeIds) { const e = ix.edges.get(id); assert.ok(s.nodeIds.has(e.sourceId) && s.nodeIds.has(e.targetId)); }
  assert.equal(sets[2].nodeIds.size, m.nodes.length);
});

test('keepFocus keeps what is selected on screen whatever the level says', () => {
  const v = V(); const m = makeModel({ seed: 5 }); const ix = MA.indexModel(m);
  const vis = MA.shownAtLevel(m, 'REQ', v, ix); const hidden = m.nodes.find((n) => !vis.nodeIds.has(n.id));
  const rel = MA.relevance(ix, { type: 'node', id: hidden.id });
  assert.ok(MA.keepFocus(vis, rel).nodeIds.has(hidden.id));
  assert.equal(MA.keepFocus(vis, MA.relevance(ix, null)), vis);
});

test('nodeDomain: kind domain, then room layer, then trust zone, then the default; unknown values never throw', () => {
  const v = V(); const m = makeModel({ seed: 7, buildings: 1, floors: 1, rooms: 1, perRoom: 2 });
  m.space.buildings[0].trust = 'external';
  m.nodes[0].kind = 'Module'; m.nodes[1].kind = 'State';
  const ix = MA.indexModel(m);
  assert.equal(MA.nodeDomain(m.nodes[0], ix, v), 'external');
  assert.equal(MA.nodeDomain(m.nodes[1], ix, v), 'business'); // kind domain
  m.space.rooms[0].layer = 'business';
  assert.equal(MA.nodeDomain(m.nodes[0], ix, v), 'business'); // room layer beats trust
  m.space.buildings[0].trust = 'no-such-zone'; delete m.space.rooms[0].layer;
  assert.equal(MA.nodeDomain(m.nodes[0], ix, v), PROFILE.defaults.trust);
  assert.ok(v.warnings.items.some((w) => w.code === MA.VIEW_WARNING.UNKNOWN_TRUST));
  assert.equal(MA.nodeDomain(m.nodes[0], ix, MA.makeVocab(null)), null);
  assert.equal(MA.domainColor(v, 'external'), PROFILE.domains.find((d) => d.id === 'external').color);
  assert.equal(MA.domainColor(v, 'no-such-domain'), MA.NEUTRAL_COLOR);
  assert.equal(MA.domainColor(MA.makeVocab(null), null), MA.NEUTRAL_COLOR);
});

test('relevance: node, edge, room, case selections and the empty selection', () => {
  const m = makeModel({ seed: 9 }); const ix = MA.indexModel(m);
  const n0 = m.nodes[0].id;
  const rel = MA.relevance(ix, { type: 'node', id: n0 });
  assert.equal(rel.active, true); assert.equal(rel.nodeTier(n0), 'Selected');
  for (const e of MA.incident(ix, n0)) { const other = e.sourceId === n0 ? e.targetId : e.sourceId; if (other !== n0) assert.equal(rel.nodeTier(other), 'Direct'); assert.equal(rel.edgeTier(e), 'Direct'); }
  const classes = new Set(m.nodes.map((n) => rel.nodeTier(n.id)));
  assert.ok(classes.has('Unrelated'));
  const er = MA.relevance(ix, { type: 'edge', id: m.edges[0].id });
  assert.equal(er.edgeTier(m.edges[0]), 'Selected');
  const rr = MA.relevance(ix, { type: 'room', id: m.nodes[0].roomId });
  assert.ok(rr.chosenNodes.size > 1);
  const cr = MA.relevance(ix, { type: 'case', id: m.cases[0].caseId });
  assert.ok(cr.chosenNodes.size > 0);
  const none = MA.relevance(ix, null);
  assert.equal(none.active, false); assert.equal(none.nodeTier(n0), 'Selected'); assert.equal(none.edgeTier(m.edges[0]), 'Selected');
  assert.doesNotThrow(() => MA.relevance(ix, { type: 'edge', id: 'missing' }));
  assert.doesNotThrow(() => MA.relevance(ix, { type: 'bogus' }));
});

test('the retreat stepper always settles to the latest target and returns to full strength', () => {
  const T = MA.RETREAT_TARGET;
  let s = { alpha: 1, push: 0 };
  const run = (target, secs, delay = 0) => { for (let i = 0; i < secs * 20; i++) { const next = MA.stepEase(s, target, 0.05, delay); s = { alpha: next.alpha, push: next.push, wait: next.wait, goal: next.goal }; } };
  run(T.Unrelated, 1, 0.5); run(T.Selected, 0.3); run(T.Unrelated, 6); assert.deepEqual([s.alpha, s.push], [T.Unrelated.alpha, 1]);
  run(T.Selected, 10); assert.deepEqual([s.alpha, s.push], [1, 0]);
  const r = MA.stepEase({ alpha: 1, push: 0 }, T.Unrelated, 0.1, 1);
  assert.equal(r.push, 0); assert.ok(r.wait > 0.8); // the delay only delays a disperse
  assert.equal(MA.disperseEase(0), 0); assert.equal(MA.disperseEase(1), 1); assert.equal(MA.disperseEase(5), 1);
});

test('fewestHopsPath, membersIn and neighbours on the fixture', () => {
  const ix = MA.indexModel(FIXTURE);
  const a = FIXTURE.nodes[0].id;
  assert.deepEqual(MA.fewestHopsPath(ix, a, a), { nodes: [a], edges: [] });
  assert.equal(MA.fewestHopsPath(ix, a, 'missing'), null);
  assert.ok(MA.membersIn(ix, 'building', FIXTURE.space.buildings[0].id).length > 0);
  assert.ok(MA.neighbours(ix, a) instanceof Set);
});
