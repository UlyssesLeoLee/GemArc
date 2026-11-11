import test from 'node:test';
import assert from 'node:assert/strict';
import { makeModel, makeRun, makeChain, rng, PROFILE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import * as PB from '../../../src/gemarc_mcp/atlas/viewer/src/playback.js';

// state maps are prototype-less (any id may be a key); compare their content as plain objects
const sameMap = (a, b, msg) => assert.deepEqual({ ...a }, { ...b }, msg);

const V = () => MA.makeVocab(PROFILE);
function setup(seed = 51) {
  const m = makeModel({ seed, perRoom: 5, cases: 3 }); const ix = MA.indexModel(m); const vocab = V();
  const L = LY.arrangeView(m, 'logical', { vocab, ix });
  const c = m.cases.find((x) => x.events.length >= 5) ?? m.cases[0];
  const run = makeRun(m, c);
  return { m, ix, vocab, L, c, run, chain: makeChain(m, run) };
}

test('seek equals fold for every index, with snapshots, without, and with the alias key name (changes)', () => {
  const { m, run } = setup();
  const bare = { ...run, snapshots: [] }; const alias = { ...run, trace: run.trace.map(({ stateChanges, ...rest }) => ({ ...rest, changes: stateChanges })) };
  for (let i = -1; i < run.trace.length; i++) {
    const f = PB.stateByFold(run, i);
    sameMap(PB.seekState(run, i), f, `snapshots @${i}`);
    sameMap(PB.seekState(bare, i), f, `no snapshots @${i}`);
    sameMap(PB.seekState(alias, i), f, `alias @${i}`);
  }
  sameMap(PB.stateByFold(run, run.trace.length - 1), run.finalState);
  sameMap(PB.stateByFold(run, -1), run.initialState);
  sameMap(PB.seekState({ ...run, snapshots: [...run.snapshots].reverse() }, 6), PB.stateByFold(run, 6));
  void m;
});

test('a snapshot with a wrong state would be visible: seek really starts from the nearest snapshot at or before the index', () => {
  const { run } = setup();
  const lie = { ...run, snapshots: [...run.snapshots, { index: 3, state: { ...run.snapshots[0].state, marker: 'from-snapshot' } }] };
  assert.equal(PB.seekState(lie, 5).marker, 'from-snapshot');
  assert.equal(PB.seekState(lie, 2).marker, undefined);
});

test('an index out of range is clamped with a warning, never a throw; junk runs fold to an empty state', () => {
  const { run } = setup(); const w = MA.makeWarnings();
  sameMap(PB.seekState(run, 999, w), PB.stateByFold(run, run.trace.length - 1));
  sameMap(PB.seekState(run, -5, w), run.initialState);
  sameMap(PB.seekState(run, NaN, w), run.initialState);
  assert.ok(w.items.some((x) => x.code === MA.VIEW_WARNING.BAD_INDEX));
  for (const junk of [null, undefined, {}, { trace: 5 }, { trace: [null, {}], initialState: 7 }]) assert.doesNotThrow(() => PB.seekState(junk, 3));
  sameMap(PB.seekState({ trace: [null, {}] }, 1), {});
});

test('clampIndex and stepIndex stay inside -1..length-1; with stops they jump between them', () => {
  assert.equal(PB.clampIndex(5, 3), 2); assert.equal(PB.clampIndex(-9, 3), -1); assert.equal(PB.clampIndex(NaN, 3), -1); assert.equal(PB.clampIndex(0, 0), -1);
  assert.equal(PB.stepIndex(-1, 3, 1), 0); assert.equal(PB.stepIndex(2, 3, 1), 2); assert.equal(PB.stepIndex(0, 3, -1), -1); assert.equal(PB.stepIndex(-1, 3, -1), -1);
  const stops = [1, 4, 6];
  assert.equal(PB.stepIndex(-1, 7, 1, stops), 1); assert.equal(PB.stepIndex(1, 7, 1, stops), 4); assert.equal(PB.stepIndex(6, 7, 1, stops), 6);
  assert.equal(PB.stepIndex(4, 7, -1, stops), 1); assert.equal(PB.stepIndex(1, 7, -1, stops), -1);
  assert.equal(PB.followingStop(stops, 6), null); assert.equal(PB.precedingStop(stops, 1), -1);
});

test('lookup of embedded runs: baselines by case, pairs by (case, intervention); a missing item is null', () => {
  const { run } = setup(); const B = { ...run, status: 'OK' };
  const runs = { baselines: { [run.caseId]: run }, pairs: [{ caseId: run.caseId, interventionId: 'iv1', status: 'OK', B, chain: { x: 1 }, diff: { outcomeClass: 'DIVERGED' } }] };
  assert.equal(PB.baselineFor(runs, run.caseId), run); assert.equal(PB.baselineFor(runs, 'nope'), null); assert.equal(PB.baselineFor(null, 'x'), null);
  const p = PB.pairRuns(runs, run.caseId, 'iv1'); assert.equal(p.A, run); assert.equal(p.B, B); assert.deepEqual(p.chain, { x: 1 });
  assert.equal(PB.pairRuns(runs, run.caseId, 'iv2'), null); assert.equal(PB.pairRuns({}, 'a', 'b'), null);
});

test('caseStage covers everything the run touches, closed over the ends of its edges; chainStage covers cause, steps and effects', () => {
  const { ix, run, chain } = setup();
  const s = PB.caseStage(ix, run);
  for (const e of run.trace) { assert.ok(s.nodes.has(e.sourceNodeId) && s.nodes.has(e.targetNodeId)); if (e.edgeId) assert.ok(s.edges.has(e.edgeId)); }
  const s5 = PB.chainStage(ix, chain);
  for (const id of chain.root.nodeIds) assert.ok(s5.nodes.has(id));
  for (const st of chain.steps) assert.ok(s5.nodes.has(st.sourceNodeId));
  for (const ef of chain.effects) assert.ok(s5.nodes.has(ef.nodeId));
  assert.deepEqual(PB.chainStage(ix, null), { nodes: new Set(), edges: new Set() });
  assert.equal(PB.chainStage(ix, { root: null }).nodes.size, 0);
});

test('4D: at every step only the run is drawn, nothing moves, only the current event halos and pulses', () => {
  const { m, ix, vocab, L, run } = setup();
  const ids = m.nodes.map((n) => n.id);
  const retreat = {}; let key = null;
  for (let idx = -1; idx < run.trace.length; idx++) {
    const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx });
    if (key === null) { key = pv.rel.key; for (const id of ids) retreat[id] = { ...MA.RETREAT_TARGET[pv.rel.nodeTier(id)] }; }
    assert.equal(pv.rel.key, key, 'the stage never changes during playback');
    for (let i = 0; i < 100 && SB.stepEaseAll(retreat, (id) => pv.rel.nodeTier(id), ids, 0.05); i++);
    const shown = PB.stageSubset(ix, L, null, pv.rel);
    const b = SB.makeBuffers(m, { positions: L.pos, shownNodeIds: shown.nodeIds, shownEdgeIds: shown.edgeIds, boxes: L.boxes, relevance: pv.rel, retreat, overlay: pv.overlay, vocab, eye: [0, 5, -30] }, ix);
    for (const n of b.hitNodes) { assert.ok(pv.rel.stage.nodes.has(n.id), `${n.id} off stage but drawn`); assert.deepEqual(b.worldPoints[n.id], L.pos[n.id], `${n.id} moved`); }
    for (const e of b.paintedEdgeIds) assert.ok(pv.rel.stage.edges.has(e));
    for (const l of b.labels) assert.ok(pv.rel.stage.nodes.has(l.id) || pv.rel.stage.edges.has(l.id));
    const selected = b.hitNodes.filter((n) => pv.rel.nodeTier(n.id) === 'Selected').length;
    assert.ok(b.haloCount <= selected);
    assert.ok(b.dotCount <= pv.overlay.pulses.length);
    if (idx >= 0) { const ev = run.trace[idx]; assert.equal(pv.rel.nodeTier(ev.sourceNodeId), 'Selected'); assert.equal(pv.overlay.pulses.length, ev.edgeId ? 1 : 0); }
  }
});

test('4D: node states in the overlay are the fold up to the position, limited to the stage and without Idle', () => {
  const { ix, run } = setup();
  for (let idx = -1; idx < run.trace.length; idx++) {
    const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx });
    const want = Object.fromEntries(Object.entries(PB.stateByFold(run, idx)).filter(([k, v]) => v !== 'Idle' && pv.scope.nodes.has(k)));
    sameMap(pv.overlay.nodeStates, want, `@${idx}`);
  }
});

test('4D: passed events are Reached, the rest of the stage is Stage; outcome Failed gets the blocked style', () => {
  const { ix, run } = setup();
  const idx = 3; const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx });
  for (const e of run.trace.slice(0, idx)) if (!new Set([run.trace[idx].sourceNodeId, run.trace[idx].targetNodeId]).has(e.sourceNodeId)) assert.ok(['Reached', 'Selected', 'Direct'].includes(pv.rel.nodeTier(e.sourceNodeId)));
  const failing = run.trace.findIndex((e) => e.outcome === 'Failed' && e.edgeId);
  if (failing >= 0) assert.deepEqual(PB.replayView({ ix, tab: '4D', run, chain: null, idx: failing }).overlay.edgePaints[run.trace[failing].edgeId], { kind: 'blocked' });
});

test('5D: the stage is the causal chain, pulses ride only causal B-steps, cause and effect marks appear', () => {
  const { ix, run, chain } = setup();
  const stops = PB.causalStepStops(chain, run.trace.length);
  assert.equal(stops.at(-1), run.trace.length - 1); assert.deepEqual(stops, [...stops].sort((a, b) => a - b)); assert.equal(new Set(stops).size, stops.length);
  let key = null; let sawEffect = false;
  for (let idx = -1; idx < run.trace.length; idx++) {
    const pv = PB.replayView({ ix, tab: '5D', run, chain, idx });
    if (key === null) key = pv.rel.key; assert.equal(pv.rel.key, key);
    const causalEdges = new Set(PB.stepsPlayingAt(chain, idx).map((s) => s.edgeId));
    for (const p of pv.overlay.pulses) assert.ok(causalEdges.has(p.edgeId));
    for (const id of chain.root.nodeIds) assert.equal(pv.overlay.marks[id], 'cause');
    if (Object.values(pv.overlay.marks).includes('effect')) sawEffect = true;
    assert.equal(pv.causal, chain.steps.some((st) => st.at === idx));
  }
  assert.ok(sawEffect);
  const last = PB.replayView({ ix, tab: '5D', run, chain, idx: run.trace.length - 1 });
  for (const e of chain.effects) assert.ok(['cause', 'effect'].includes(last.overlay.marks[e.nodeId]));
});

test('causalStepStops: causal positions plus the last one; nothing for an empty run', () => {
  assert.deepEqual(PB.causalStepStops({ steps: [{ at: 1 }, { at: 1 }, { at: 3 }] }, 6), [1, 3, 5]);
  assert.deepEqual(PB.causalStepStops({ steps: [{ at: 99 }] }, 5), [4]);
  assert.deepEqual(PB.causalStepStops(null, 3), [2]);
  assert.deepEqual(PB.causalStepStops({ steps: [{ at: 0 }] }, 0), []);
});

test('5D overlay colours edges by causal role; the cause stays marked; lost steps are weak', () => {
  const { chain } = setup();
  const o = PB.causalLayer(chain, 99);
  for (const id of chain.root.edgeIds) assert.ok(o.edgePaints[id]);
  const lost = chain.steps.find((s) => s.role === 'lost');
  if (lost && lost.edgeId && !chain.root.edgeIds.includes(lost.edgeId)) assert.deepEqual(o.edgePaints[lost.edgeId], { color: PB.ROLE_COLOR.lost, alpha: 0.4 });
  assert.equal(PB.roleColor('no-such-role'), PB.NEUTRAL_ROLE_COLOR);
  assert.deepEqual(JSON.parse(JSON.stringify(PB.causalLayer(null, 0))), { edgePaints: {}, marks: {}, overlayNodes: [], overlayEdges: [] });
  assert.deepEqual(PB.causalLayer({ status: 'DRAFT', root: {} }, 0).overlayNodes, []);
});

test('5D: an inserted element is on stage with the nodes it sits between, and a click on it highlights its chain', () => {
  const { m, ix, run, chain } = setup();
  const e0 = run.trace.find((e) => e.edgeId);
  const withEl = { ...chain, root: { ...chain.root, element: { id: 'el-1', kind: 'Cache', name: 'El', roomId: null, between: [e0.sourceNodeId, e0.targetNodeId], replacedEdgeId: e0.edgeId, edges: [{ id: 'el-1:1', sourceId: e0.sourceNodeId, targetId: 'el-1' }, { id: 'el-1:2', sourceId: 'el-1', targetId: e0.targetNodeId }] } } };
  const pv = PB.replayView({ ix, tab: '5D', run, chain: withEl, idx: 1 });
  assert.ok(pv.rel.stage.nodes.has('el-1')); assert.ok(pv.rel.stage.edges.has('el-1:1'));
  assert.deepEqual(pv.overlay.overlayNodes.map((x) => x.id), ['el-1']);
  const click = PB.replayView({ ix, tab: '5D', run, chain: withEl, idx: 1, sel: { type: 'element' } });
  assert.equal(click.rel.nodeTier('el-1'), 'Selected'); assert.equal(click.rel.nodeTier(e0.sourceNodeId), 'Selected'); assert.equal(click.rel.edgeTier({ id: 'el-1:1' }), 'Selected');
  void m;
});

test('a click while the stage is up highlights the clicked node and its neighbours within the stage; the stage itself does not change', () => {
  const { ix, run } = setup();
  const base = PB.replayView({ ix, tab: '4D', run, chain: null, idx: 2 });
  const node = run.trace[2].sourceNodeId;
  const click = PB.replayView({ ix, tab: '4D', run, chain: null, idx: 2, sel: { type: 'node', id: node } });
  assert.equal(click.rel.key, base.rel.key); assert.equal(click.rel.nodeTier(node), 'Selected');
  for (const e of MA.incident(ix, node)) if (click.scope.edges.has(e.id)) assert.equal(click.rel.edgeTier(e), 'Direct');
  const edge = run.trace.find((e) => e.edgeId).edgeId;
  const ce = PB.replayView({ ix, tab: '4D', run, chain: null, idx: 2, sel: { type: 'edge', id: edge } });
  assert.equal(ce.rel.edgeTier(ix.edges.get(edge)), 'Selected');
});

test('stage classes: Selected, Direct, Reached, Stage, Outside; Outside is never shown', () => {
  const { ix, L } = setup();
  const scope = { nodes: new Set(['a', 'b', 'c', 'd']), edges: new Set(['e1']) };
  const rel = PB.stageFocus(ix, scope, { nodes: ['a'], nearNodes: ['a', 'b'], passedNodes: ['c'] });
  assert.deepEqual(['a', 'b', 'c', 'd', 'z'].map((id) => rel.nodeTier(id)), ['Selected', 'Direct', 'Reached', 'Stage', 'Outside']);
  assert.equal(rel.edgeTier({ id: 'e1' }), 'Stage'); assert.equal(rel.edgeTier({ id: 'zz' }), 'Outside');
  const shown = PB.stageSubset(ix, L, null, PB.replayView({ ix, tab: '4D', run: { trace: [] }, idx: -1 }).rel);
  assert.equal(shown.nodeIds.size, 0);
});

test('the level still reduces the stage, but the current event always stays drawn', () => {
  const { m, ix, vocab, L, run } = setup();
  const detail = MA.shownAtLevel(m, 'REQ', vocab, ix);
  const hidden = run.trace.map((e) => e.sourceNodeId).find((id) => !detail.nodeIds.has(id));
  if (!hidden) return; // every node of this run is shown at REQ
  const idx = run.trace.findIndex((e) => e.sourceNodeId === hidden);
  const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx });
  const shown = PB.stageSubset(ix, L, detail, pv.rel);
  assert.ok(shown.nodeIds.has(hidden));
  const earlier = PB.replayView({ ix, tab: '4D', run, chain: null, idx: -1 });
  assert.ok(!PB.stageSubset(ix, L, detail, earlier.rel).nodeIds.has(hidden) || detail.nodeIds.has(hidden));
});

test('nothing to play is null, not a crash: no run, a run without a trace, a 5D tab without a chain', () => {
  const { ix, run } = setup();
  assert.equal(PB.replayView({ ix, tab: '4D', run: null, chain: null, idx: 0 }), null);
  assert.equal(PB.replayView({ ix, tab: '4D', run: {}, chain: null, idx: 0 }), null);
  assert.equal(PB.replayView({ ix, tab: '5D', run, chain: null, idx: 0 }), null);
  const w = MA.makeWarnings();
  const pv = PB.replayView({ ix, tab: '4D', run, chain: null, idx: 9999, warnings: w });
  assert.equal(pv.rel.nodeTier(run.trace.at(-1).sourceNodeId) !== undefined, true); assert.ok(w.items.length === 1);
  assert.doesNotThrow(() => PB.replayView({ ix, tab: '4D', run: { trace: [] }, idx: 0 }));
});

test('a long randomised run folds consistently (seek == fold at random positions)', () => {
  const m = makeModel({ seed: 52, perRoom: 6 }); const r = rng(9);
  const big = { caseId: 'BIG', events: Array.from({ length: 300 }, (_, i) => { const e = m.edges[r.int(m.edges.length)]; return { seq: i + 1, type: 'Call', from: e.sourceId, to: e.targetId, edge: e.id }; }) };
  const run = makeRun(m, big);
  for (let k = 0; k < 60; k++) { const i = r.int(301) - 1; sameMap(PB.seekState(run, i), PB.stateByFold(run, i)); }
});
