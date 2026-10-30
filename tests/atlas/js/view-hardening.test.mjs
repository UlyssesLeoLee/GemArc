// Regression tests of the verification round: ids named like Object.prototype members, machine states, data view membership,
// huge floor levels, playback index handling, rejected pairs, argument defaults and the bundle scope. Synthetic ids only.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { makeModel, makeRun, makeChain, PROFILE, allFinite } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import * as CO from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import * as PB from '../../../src/gemarc_mcp/atlas/viewer/src/playback.js';

const V = () => MA.makeVocab(PROFILE);
const SPACE = () => ({ buildings: [{ id: 'bl', name: 'b', trust: 'internal' }], floors: [{ id: 'fl', buildingId: 'bl', name: 'f', level: 0 }], rooms: [{ id: 'rm', floorId: 'fl', name: 'r' }] });
const node = (id, kind = 'Module', extra = {}) => ({ id, kind, name: id, roomId: 'rm', props: {}, ...extra });
const edge = (s, t, relation = 'calls') => ({ id: `${s}:${relation}:${t}`, sourceId: s, targetId: t, relation });
const smallModel = (nodes, edges, extra = {}) => ({ nodes, edges, space: SPACE(), cases: [], machines: [], requirements: [], ...extra });
const sceneOf = (m, extra = {}) => {
  const vocab = extra.vocab ?? V(); const ix = MA.indexModel(m); const L = LY.arrangeView(m, 'logical', { vocab, ix });
  const state = { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab, eye: [0, 8, -40], ...extra };
  return { m, vocab, ix, L, state, build: (patch = {}) => SB.makeBuffers(m, { ...state, ...patch }, ix) };
};
const codes = (w) => w.items.map((x) => x.code);

// ---------------------------------------------------------------------------------------------------------------------------
test('own() and dict(): inherited members are never read, a dict has no prototype', () => {
  assert.equal(MA.own({}, 'constructor'), undefined);
  assert.equal(MA.own({ constructor: 3 }, 'constructor'), 3);
  assert.equal(MA.own(null, 'x'), undefined);
  assert.equal(MA.own(undefined, 'x'), undefined);
  const d = MA.dict({ a: 1 });
  assert.equal(Object.getPrototypeOf(d), null);
  assert.equal(d.toString, undefined);
  assert.equal(JSON.stringify(d), '{"a":1}');
});

const HOSTILE = ['constructor', 'toString', 'valueOf', 'hasOwnProperty', 'isPrototypeOf', 'propertyIsEnumerable', 'toLocaleString', 'a4'];

test('ids that equal Object.prototype members are ordinary ids in every view, buffer, retreat and overlay (no dropped node, no NaN)', () => {
  const nodes = HOSTILE.map((id, i) => node(id, i % 2 ? 'Service' : 'Module'));
  const edges = HOSTILE.map((id, i) => edge(id, HOSTILE[(i + 1) % HOSTILE.length]));
  const m = smallModel(nodes, edges);
  const { build, L, ix, vocab, state } = sceneOf(m);
  assert.equal(Object.keys(L.pos).length, HOSTILE.length);
  for (const id of HOSTILE) assert.ok(Array.isArray(L.pos[id]) && allFinite(L.pos[id]), `${id} placed`);
  const plain = build();
  assert.equal(plain.hitNodes.length, HOSTILE.length);
  assert.equal(plain.paintedEdgeIds.length, edges.length);
  assert.ok(allFinite(plain.lines) && allFinite(plain.mesh.pos));
  // focus: retreat driven to its end for every node, then the build
  const rel = MA.relevance(ix, { type: 'node', id: 'constructor' });
  const retreat = {};
  SB.stepEaseAll(retreat, rel.nodeTier, HOSTILE, 10);
  const b = SB.makeBuffers(m, { ...state, relevance: rel, retreat }, ix);
  assert.ok(allFinite(b.lines) && allFinite(b.mesh.pos) && allFinite(b.mesh.col));
  assert.ok(b.hitNodes.some((x) => x.id === 'constructor'));
  for (const id of b.hitNodes.map((x) => x.id)) assert.ok(Array.isArray(b.worldPoints[id]));
  // overlay maps keyed by such ids: states, marks, edge paints
  const o = build({ overlay: { nodeStates: { valueOf: 'Running', toString: 'Failed' }, marks: { hasOwnProperty: 'cause', constructor: 'effect' }, edgePaints: { [edges[0].id]: { color: '#ffffff', alpha: 1 } } } });
  assert.ok(o.haloCount >= 1 && allFinite(o.lines));
  assert.ok(o.labels.some((l) => l.id === 'hasOwnProperty' && l.mark === 'cause'));
  assert.ok(o.labels.some((l) => l.id === 'constructor' && l.mark === 'effect'));
  // a state, a mark and a retreat entry that are NOT set must not resolve to inherited members either
  const clean = build({ overlay: { nodeStates: {}, marks: {} }, retreat: {} });
  assert.equal(clean.haloCount, 0);
  assert.equal(vocab.warnings.items.length, 0, JSON.stringify(vocab.warnings.items));
  // blendPositions from an empty map: every id takes its target, never an inherited function
  const blended = LY.blendPositions({}, L.pos, 0.5);
  for (const id of HOSTILE) assert.deepEqual(blended[id], L.pos[id]);
  // the other views and the 2D layout
  for (const v of LY.VIEW_IDS) { const l = LY.arrangeView(m, v, { vocab: V() }); assert.ok(allFinite(l.pos), v); }
  assert.equal(LY.cardLayout(m, null, { vocab: V() }).nodes.length, HOSTILE.length);
});

test('playback over ids named like Object.prototype members: states, stage and the 5D marks keep every node', () => {
  const nodes = HOSTILE.map((id) => node(id));
  const edges = HOSTILE.map((id, i) => edge(id, HOSTILE[(i + 1) % HOSTILE.length]));
  const caseItem = { caseId: 'KC1', events: [{ seq: 1, type: 'Call', from: 'constructor', to: 'toString', edge: edges[0].id }, { seq: 2, type: 'Call', from: 'toString', to: 'valueOf', edge: edges[1].id }] };
  const m = smallModel(nodes, edges, { cases: [caseItem] });
  const ix = MA.indexModel(m); const run = makeRun(m, caseItem); const chain = makeChain(m, run);
  const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx: 0 });
  assert.equal(pv.overlay.nodeStates.constructor, 'Running');
  assert.equal(pv.overlay.nodeStates.valueOf, undefined);
  assert.equal(PB.seekState(run, 1).toString, 'Running');
  assert.equal(PB.stateByFold(run, 1).toString, 'Running');
  const p5 = PB.replayView({ ix, tab: '5D', run, chain, idx: run.trace.length - 1 });
  assert.ok(p5 && Object.keys(p5.overlay.marks).length >= 1);
  const edgeNames = Object.keys(p5.overlay.edgePaints);
  assert.ok(edgeNames.every((k) => k.includes(':')));
  assert.equal(PB.baselineFor({ baselines: {} }, 'constructor'), null);
  assert.equal(PB.roleColor('constructor'), PB.NEUTRAL_ROLE_COLOR);
});

// ---------------------------------------------------------------------------------------------------------------------------
test('machine states: a state of the node\'s own machine is known (no warning, no ring); a profile state still gets its ring; others warn', () => {
  const m = smallModel([node('mq', 'State'), node('wk', 'Service')], [edge('wk', 'mq', 'owns')], {
    machines: [{ id: 'mq', nodeId: 'mq', initial: 'Queued', states: ['Queued', 'Running', 'Done'], terminal: ['Done'], transitions: [] }],
  });
  const none = sceneOf(m).build({ overlay: { nodeStates: {} } });
  const s1 = sceneOf(m); const queued = s1.build({ overlay: { nodeStates: { mq: 'Queued' } } });
  assert.equal(codes(s1.vocab.warnings).includes('GEMARC_ATLAS_VIEW_007'), false);
  assert.equal(queued.haloCount, 0);
  assert.equal(queued.lineCount, none.lineCount, 'a machine-only state draws no ring');
  const s2 = sceneOf(m); const running = s2.build({ overlay: { nodeStates: { mq: 'Running' } } });
  assert.equal(running.haloCount, 1);
  assert.ok(running.lineCount > none.lineCount, 'Running is a profile state: it is drawn with a ring even on a machine node');
  // a node that is not a machine does not get the exemption
  const s3 = sceneOf(m); const stray = s3.build({ overlay: { nodeStates: { wk: 'Queued' } } });
  assert.deepEqual(codes(s3.vocab.warnings), ['GEMARC_ATLAS_VIEW_007']);
  assert.equal(stray.haloCount, 1, 'an unknown state still draws the neutral ring');
  // stateColor: the third argument is the exemption, nothing else changes
  const v = V();
  assert.equal(SB.stateColor(v, 'Queued', new Set(['Queued'])), null);
  assert.equal(codes(v.warnings).length, 0);
  assert.equal(SB.stateColor(v, 'Queued'), SB.NEUTRAL_STATE_COLOR);
  assert.equal(codes(v.warnings).length, 1);
  assert.equal(SB.stateColor(v, 'constructor'), SB.NEUTRAL_STATE_COLOR);
});

test('4D playback over a machine node: its own states give no warning in the whole run', () => {
  const m = smallModel([node('mq', 'State'), node('wk', 'Service')], [edge('wk', 'mq', 'owns')], {
    machines: [{ id: 'mq', nodeId: 'mq', initial: 'Queued', states: ['Queued', 'Open', 'Closed'], terminal: ['Closed'], transitions: [] }],
  });
  const ix = MA.indexModel(m); const vocab = V(); const L = LY.arrangeView(m, 'logical', { vocab, ix });
  const trace = [
    { index: 0, sourceNodeId: 'wk', targetNodeId: 'mq', edgeId: 'wk:owns:mq', outcome: 'OK', stateChanges: [{ nodeId: 'mq', from: 'Queued', to: 'Open' }] },
    { index: 1, sourceNodeId: 'wk', targetNodeId: 'mq', edgeId: 'wk:owns:mq', outcome: 'OK', stateChanges: [{ nodeId: 'mq', from: 'Open', to: 'Closed' }] },
  ];
  const run = { caseId: 'c', status: 'OK', trace, initialState: { mq: 'Queued', wk: 'Idle' }, snapshots: [] };
  for (let i = -1; i < 2; i++) {
    const pv = PB.replayView({ ix, tab: '4D', run, idx: i });
    const b = SB.makeBuffers(m, { positions: L.pos, vocab, relevance: pv.rel, overlay: pv.overlay, shownNodeIds: PB.stageSubset(ix, L, null, pv.rel).nodeIds, shownEdgeIds: L.shownEdgeIds }, ix);
    assert.ok(b.hitNodes.length >= 1);
  }
  assert.equal(vocab.warnings.items.length, 0, JSON.stringify(vocab.warnings.items));
});

// ---------------------------------------------------------------------------------------------------------------------------
test('data view membership: nodes outside the view kinds join through ANY relation of the data view, in either direction (documented rule)', () => {
  // data-view relations in the profile: reads writes publishes subscribes triggers belongsTo references; `calls` is not one of them
  const m = smallModel(
    [node('t1', 'Table'), node('svc', 'Service'), node('own', 'Component'), node('far', 'Module'), node('sub', 'Module')],
    [edge('svc', 't1', 'writes'), edge('t1', 'own', 'belongsTo'), edge('t1', 'sub', 'subscribes'), edge('far', 'svc', 'calls')],
  );
  const L = LY.arrangeView(m, 'data', { vocab: V() });
  assert.deepEqual([...L.shownNodeIds].sort(), ['own', 'sub', 't1', 'svc'].sort());
  assert.ok(!L.shownNodeIds.has('far'));
  assert.ok(L.shownEdgeIds.has('t1:belongsTo:own') && L.shownEdgeIds.has('svc:writes:t1') && !L.shownEdgeIds.has('far:calls:svc'));
});

// ---------------------------------------------------------------------------------------------------------------------------
test('floor levels beyond MAX_FLOOR_LEVEL are invalid: warned, moved above the building, scene stays finite and framed', () => {
  assert.equal(LY.MAX_FLOOR_LEVEL, 1e6);
  for (const bad of [1e300, 1e7, -1e7, 1e6 + 1]) {
    const m = makeModel({ seed: 3, buildings: 1, floors: 3, rooms: 1, perRoom: 3, levels: [0, bad, 1] });
    const vocab = V();
    const lv = LY.floorLevels(m, vocab);
    for (const v of lv.values()) assert.ok(Number.isFinite(v) && Math.abs(v) <= 1e6, `level ${v} for ${bad}`);
    assert.equal(new Set(lv.values()).size, lv.size, 'unique levels');
    assert.ok(codes(vocab.warnings).includes('GEMARC_ATLAS_VIEW_006'), `warning for ${bad}`);
    const ix = MA.indexModel(m); const L = LY.arrangeView(m, 'logical', { vocab: V(), ix });
    const b = SB.makeBuffers(m, { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab: V(), eye: [0, 5, -30], showFrames: true }, ix);
    assert.ok(allFinite(b.mesh.pos) && allFinite(b.lines) && allFinite(L.boxes), `finite buffers for ${bad}`);
    const cam = CO.fitCamera(CO.newCamera(), LY.layoutPoints(L), 1.6);
    assert.deepEqual(CO.pointsOutside(cam, LY.layoutPoints(L), 800, 500), []);
  }
  // the boundary value itself is allowed
  const ok = makeModel({ seed: 4, buildings: 1, floors: 2, rooms: 1, perRoom: 2, levels: [0, 1e6] });
  const v2 = V();
  assert.equal(LY.floorLevels(ok, v2).get(ok.space.floors[1].id), 1e6);
  assert.equal(v2.warnings.items.length, 0);
});

// ---------------------------------------------------------------------------------------------------------------------------
test('normIndex: the single place where a playback index is normalised; seek, fold, clamp and replayView agree', () => {
  const m = makeModel({ seed: 21, perRoom: 4 }); const c = m.cases.find((x) => x.events.length >= 4) ?? m.cases[0];
  const run = makeRun(m, c); const n = run.trace.length;
  assert.deepEqual(MA && PB.normIndex(0, n), { i: 0, ok: true });
  assert.deepEqual(PB.normIndex(-1, n), { i: -1, ok: true });
  assert.deepEqual(PB.normIndex(n - 1, n), { i: n - 1, ok: true });
  assert.deepEqual(PB.normIndex(n, n), { i: n - 1, ok: false });
  assert.deepEqual(PB.normIndex(-3, n), { i: -1, ok: false });
  assert.deepEqual(PB.normIndex(1.5, n), { i: 1, ok: false });
  for (const junk of [NaN, '1', undefined, null, Infinity, {}, [1]]) assert.equal(PB.normIndex(junk, n).ok, false, String(junk));
  assert.equal(PB.normIndex(Infinity, n).i, -1);
  assert.equal(PB.normIndex(NaN, n).i, -1);
  assert.deepEqual(PB.normIndex(0, 0), { i: -1, ok: false });
  assert.deepEqual(PB.normIndex(-1, 0), { i: -1, ok: true });
  const same = (a, b) => assert.deepEqual({ ...a }, { ...b });
  for (const idx of [NaN, 1.5, '1', undefined, null, 7, -3, 0.2, 2.9, Infinity, -Infinity]) {
    const w = MA.makeWarnings();
    const i = PB.normIndex(idx, n).i;
    same(PB.seekState(run, idx, w), PB.stateByFold(run, i));
    same(PB.stateByFold(run, idx), PB.stateByFold(run, i));
    assert.equal(PB.clampIndex(idx, n), i);
    assert.equal(w.items.length, 1, `seekState(${String(idx)}) warns exactly once`);
    assert.equal(w.items[0].code, 'GEMARC_ATLAS_VIEW_008');
  }
  for (const idx of [-1, 0, 1, n - 1]) { const w = MA.makeWarnings(); PB.seekState(run, idx, w); assert.equal(w.items.length, 0, `index ${idx} is valid`); }
  // the same through stateByFold and replayView
  const ix = MA.indexModel(m);
  for (const idx of [NaN, 1.5, undefined, 99]) {
    const w1 = MA.makeWarnings(); PB.stateByFold(run, idx, w1); assert.equal(w1.items.length, 1);
    const w2 = MA.makeWarnings(); const pv = PB.replayView({ ix, tab: '4D', run, idx, warnings: w2 }); assert.ok(pv); assert.equal(w2.items.length, 1);
  }
  // a fractional index truncates in every helper alike
  same(PB.seekState(run, 1.5), PB.stateByFold(run, 1)); assert.equal(PB.clampIndex(1.5, n), 1);
});

test('a rejected pair has nothing to play in 5D: chain status other than OK is null, 4D is unaffected', () => {
  const m = makeModel({ seed: 22, perRoom: 4 }); const c = m.cases.find((x) => x.events.length >= 4) ?? m.cases[0];
  const ix = MA.indexModel(m); const A = makeRun(m, c); const chain = makeChain(m, A);
  assert.ok(PB.replayView({ ix, tab: '5D', run: A, chain, idx: 0 }));
  for (const status of ['REJECTED', 'ERROR', '', undefined, null]) {
    assert.equal(PB.replayView({ ix, tab: '5D', run: A, chain: { ...chain, status }, idx: 0 }), null, String(status));
  }
  assert.ok(PB.replayView({ ix, tab: '4D', run: A, chain: { ...chain, status: 'REJECTED' }, idx: 0 }));
});

// ---------------------------------------------------------------------------------------------------------------------------
test('argument defaults: pick without items, stepEase without arguments, fitCamera on a broken camera never throw and stay finite', () => {
  const cam = CO.newCamera();
  assert.equal(CO.pick(cam, undefined, 10, 10, 100, 100), null);
  assert.equal(CO.pick(cam, {}, 10, 10, 100, 100), null);
  assert.equal(CO.pick(cam, { nodes: null, edges: 3 }, 10, 10, 100, 100), null);
  const s = MA.stepEase(undefined, undefined, 0.1);
  assert.ok(Number.isFinite(s.alpha) && Number.isFinite(s.push));
  assert.ok(s.alpha < 1 && s.alpha > MA.RETREAT_TARGET.Context.alpha, 'a fresh node starts from full strength and moves towards the context target');
  assert.doesNotThrow(() => MA.stepEase(undefined, undefined, NaN));
  assert.doesNotThrow(() => SB.stepEaseAll({}, () => 'Context', ['x'], 0.2));
  const pts = [[0, 0, 0], [10, 3, 4], [-6, 2, 9]];
  for (const bad of [{ dist: NaN }, { vFov: NaN }, { vFov: 0 }, { near: Infinity }, { target: [NaN, 0, 0] }, { yaw: undefined, pitch: 'x' }, { far: null }]) {
    const fit = CO.fitCamera({ ...cam, ...bad }, pts, 1.6);
    for (const k of ['yaw', 'pitch', 'dist', 'vFov', 'near', 'far', 'minDist', 'maxDist']) assert.ok(Number.isFinite(fit[k]), `${JSON.stringify(bad)} -> ${k}`);
    assert.ok(allFinite(fit.target));
    assert.deepEqual(CO.pointsOutside(fit, pts, 800, 500), [], JSON.stringify(bad));
  }
  assert.doesNotThrow(() => CO.fitCamera(null, pts));
  assert.deepEqual(CO.saneCamera(cam), cam, 'a sane camera is returned equal');
  assert.deepEqual(CO.fitCamera({ ...cam, dist: NaN }, [], 1.6), CO.saneCamera({ ...cam, dist: NaN }));
});

// ---------------------------------------------------------------------------------------------------------------------------
// Bundle scope. The release bundler may concatenate the modules into one classic script (imports removed, `export` dropped): the
// top-level names of all modules must then be unique, and the result must compile and behave like the ES modules.
const ORDER = ['coords.js', 'model-access.js', 'layout.js', 'scene-buffers.js', 'playback.js', 'search.js', 'label-fit.js', 'assemble.js'];
const SRC = new URL('../../../src/gemarc_mcp/atlas/viewer/src/', import.meta.url);
const source = (f) => readFileSync(new URL(f, SRC), 'utf8');

test('naive bundle: the top-level names of all eight modules are unique, so strip-imports-and-concatenate compiles', () => {
  const seen = new Map();
  for (const f of ORDER) {
    for (const line of source(f).split('\n')) {
      const m = /^(?:export\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/.exec(line);
      if (!m) continue;
      assert.ok(!seen.has(m[1]), `${m[1]} is declared in ${seen.get(m[1])} and in ${f}`);
      seen.set(m[1], f);
    }
  }
  assert.ok(seen.size > 150);
  const code = ORDER.map((f) => source(f).split('\n').filter((l) => !/^import\b/.test(l)).map((l) => l.replace(/^export\s+(?=const|function|class)/, '')).join('\n')).join('\n');
  assert.doesNotThrow(() => new vm.Script(code, { filename: 'bundle.js' }));
});

test('naive bundle: the concatenated script behaves like the ES modules (layout, buffers, search, playback)', () => {
  const code = ORDER.map((f) => source(f).split('\n').filter((l) => !/^import\b/.test(l)).map((l) => l.replace(/^export\s+(?=const|function|class)/, '')).join('\n')).join('\n');
  const ctx = vm.createContext(Object.create(null));
  const api = new vm.Script(`${code}\n;({ makeVocab, indexModel, arrangeView, makeBuffers, makeSearchIndex, search, cardLayout, replayView, makeWarnings })`).runInContext(ctx);
  const m = makeModel({ seed: 9, perRoom: 4 });
  const profile = JSON.parse(JSON.stringify(PROFILE));
  const run = (A) => {
    const vocab = A.makeVocab(profile); const ix = A.indexModel(m); const L = A.arrangeView(m, 'logical', { vocab, ix });
    const b = A.makeBuffers(m, { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab, eye: [0, 5, -30] }, ix);
    const si = A.makeSearchIndex(m, [], { vocab, ix });
    const c0 = m.cases[0]; const r = makeRun(m, c0); const pv = A.replayView({ ix, tab: '4D', run: r, idx: 2 });
    return JSON.stringify([L.pos, L.boxes, Array.from(b.lines), Array.from(b.mesh.pos), b.labels, A.search(si, 'node 3').map((e) => e.key), A.cardLayout(m, null, { vocab, ix }).nodes.length, pv.overlay.nodeStates, [...pv.rel.stage.nodes]]);
  };
  const esm = { makeVocab: MA.makeVocab, indexModel: MA.indexModel, arrangeView: LY.arrangeView, makeBuffers: SB.makeBuffers, makeSearchIndex: null, search: null, cardLayout: LY.cardLayout, replayView: PB.replayView };
  return import('../../../src/gemarc_mcp/atlas/viewer/src/search.js').then((SE) => {
    esm.makeSearchIndex = SE.makeSearchIndex; esm.search = SE.search;
    assert.equal(run(api), run(esm));
  });
});
