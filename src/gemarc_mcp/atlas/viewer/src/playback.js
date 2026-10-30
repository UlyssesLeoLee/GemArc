// 4D / 5D playback over PRECOMPUTED runs: the browser never simulates. A run is the engine's result for one case (baselines) or for one
// (intervention, case) pair, embedded by the build; this module folds the `changes` of its trace events into node states, derives which
// elements a case (4D) or a causal chain (5D) is about, and says how each playback position highlights them.
//
// The stage is fixed for the whole run, so stepping through events never moves anything: the current event is bright, what playback has
// passed stays medium, the rest of the stage is a weak trace, and everything outside the stage is not drawn at all.
// 5D is limited to the causal chain: events that A and B share carry no animation, and autoplay jumps between the causal steps.
// Pure functions: no DOM, no simulation.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { incident, keepFocus, asList, isRecord, own, dict, VIEW_WARNING } from './model-access.js';
import { ENERGY_COLOR } from './scene-buffers.js';

const FAILED = ['Blocked', 'Denied', 'Failed', 'Timeout'];

// Role of a causal step -> colour. Role ids are the engine's closed set; the labels live in the i18n tables, not here.
export const ROLE_COLOR = {
  cause: '#ff5ad1', block: '#f08886', deny: '#f08886', fail: '#f08886', timeout: '#f0ba63', reject: '#f0ba63', starve: '#c9a25e', degrade: '#f0ba63',
  retry: '#82adfa', recovery: '#82adfa', compensation: '#82adfa', bypass: '#a78bfa', gain: '#a78bfa', lost: '#7f8fa8', effect: '#ffa3e8',
};
export const NEUTRAL_ROLE_COLOR = '#7f8fa8';
export const roleColor = (role) => own(ROLE_COLOR, role) ?? NEUTRAL_ROLE_COLOR;

// --------- runs: lookup, state folding ---------
/** The baseline run of a case, or null (the case is not sealed, or no run was embedded). `runs` is the bundle's runs object. */
export const baselineFor = (runs, caseId) => own(runs?.baselines, caseId) ?? null;
/** { A, B, chain, diff, status, errors } of an authored (intervention, case) pair, or null. A is the baseline of the same case. */
export function pairRuns(runs, caseId, interventionId) {
  const p = asList(runs?.pairs).find((x) => x && x.caseId === caseId && x.interventionId === interventionId);
  if (!p) return null;
  return { A: baselineFor(runs, caseId), B: p.B ?? null, chain: p.chain ?? null, diff: p.diff ?? null, status: p.status ?? null, errors: asList(p.errors) };
}

/** The state changes of a trace event, [{nodeId, from, to}]: the engine emits `stateChanges`; the shorter `changes` is accepted as an alias. */
export const changesOf = (ev) => asList(ev?.stateChanges ?? ev?.changes);
export const traceOf = (run) => asList(run?.trace);

/**
 * The one place a playback index is normalised: a finite number is truncated and clamped into -1..length-1 (-1 = before the first event);
 * anything else (NaN, a string, null, undefined) is -1. `ok` is false when the input was not already an integer inside that range,
 * and the callers then warn (GEMARC_ATLAS_VIEW_008). seek, fold, clamp and the player all go through it, so they always agree.
 */
export function normIndex(idx, length) {
  const n = Math.max(0, Math.trunc(Number(length)) || 0);
  const t = typeof idx === 'number' && Number.isFinite(idx) ? Math.trunc(idx) : -1;
  const i = Math.max(-1, Math.min(n - 1, t));
  return { i, ok: i === idx };
}
const warnIndex = (warnings, idx) => warnings?.add(VIEW_WARNING.BAD_INDEX, `${idx}`, 'playback index is not an integer inside the trace: clamped');

/** Node states after applying the first idx + 1 events (idx = -1: the initial state), folded from the initial state. */
export function stateByFold(run, idx, warnings = null) {
  const trace = traceOf(run);
  const st = dict(isRecord(run?.initialState) ? run.initialState : null);
  const { i: last, ok } = normIndex(idx, trace.length);
  if (!ok) warnIndex(warnings, idx);
  for (let i = 0; i <= last; i++) for (const ch of changesOf(trace[i])) st[ch.nodeId] = ch.to;
  return st;
}
/**
 * Same result as stateByFold, starting from the nearest snapshot at or before idx when the run has snapshots ({index, state}). An index outside
 * -1..length-1 is clamped (and warned when a warnings sink is given): playback never throws.
 */
export function seekState(run, idx, warnings = null) {
  const trace = traceOf(run);
  const { i, ok } = normIndex(idx, trace.length);
  if (!ok) warnIndex(warnings, idx);
  let snap = null;
  for (const s of asList(run?.snapshots)) if (isRecord(s) && Number.isInteger(s.index) && s.index >= -1 && s.index <= i && isRecord(s.state) && (!snap || s.index >= snap.index)) snap = s;
  const st = dict(snap ? snap.state : (isRecord(run?.initialState) ? run.initialState : null));
  for (let k = (snap ? snap.index : -1) + 1; k <= i; k++) for (const ch of changesOf(trace[k])) st[ch.nodeId] = ch.to;
  return st;
}

// --------- player position (pure state transitions the UI edges to its controls) ---------
/** idx within -1..length-1 (-1 = before the first event); a finite number is truncated, anything else is -1 (see normIndex). */
export function clampIndex(idx, length) { return normIndex(idx, length).i; }
/** One step forward (dir > 0) or back (dir < 0); with `stops` (5D autoplay) it jumps between them. Never leaves -1..length-1. */
export function stepIndex(idx, length, dir, stops = null) {
  const cur = clampIndex(idx, length);
  if (stops) return dir > 0 ? (followingStop(stops, cur) ?? cur) : precedingStop(stops, cur);
  return clampIndex(cur + (dir > 0 ? 1 : -1), length);
}

// --------- stage scope ---------
function closeOverEdges(ix, nodes, edges) {
  for (const id of edges) { const e = ix.edges.get(id); if (e) { nodes.add(e.sourceId); nodes.add(e.targetId); } }
  return { nodes, edges };
}

// 4D stage: every node and edge the run touches (the trace, so retries and compensation steps are included).
export function caseStage(ix, run) {
  const nodes = new Set(); const edges = new Set();
  for (const ev of traceOf(run)) { nodes.add(ev.sourceNodeId); nodes.add(ev.targetNodeId); if (ev.edgeId) edges.add(ev.edgeId); }
  return closeOverEdges(ix, nodes, edges);
}

// 5D stage: the cause (intervention target, inserted element and its edges), every causal step (A-only ones included) and the effects.
export function chainStage(ix, chain) {
  const nodes = new Set(); const edges = new Set();
  if (!chain?.root) return { nodes, edges };
  const el = chain.root.element;
  for (const id of asList(chain.root.nodeIds)) nodes.add(id);
  for (const id of asList(chain.root.edgeIds)) edges.add(id);
  for (const id of asList(el?.between)) nodes.add(id);
  for (const x of asList(el?.edges)) { edges.add(x.id); nodes.add(x.sourceId); nodes.add(x.targetId); }
  for (const s of asList(chain.steps)) { nodes.add(s.sourceNodeId); nodes.add(s.targetNodeId); if (s.edgeId) edges.add(s.edgeId); }
  for (const e of asList(chain.effects)) nodes.add(e.nodeId);
  return closeOverEdges(ix, nodes, edges);
}

// Playback positions that carry a causal step, plus the last position (where the effects are revealed). Autoplay visits only these.
export function causalStepStops(chain, length) {
  if (!length) return [];
  const last = length - 1;
  const at = asList(chain?.steps).map((st) => Math.min(last, st.at)).filter((i) => i >= 0);
  return [...new Set([...at, last])].sort((a, b) => a - b);
}
export const followingStop = (stops, idx) => stops.find((i) => i > idx) ?? null;
export const precedingStop = (stops, idx) => [...stops].reverse().find((i) => i < idx) ?? -1;
// B-side causal steps playing at position idx (A-only steps no longer happen, so they are colored but never animated).
export const stepsPlayingAt = (chain, idx) => asList(chain?.steps).filter((s) => s.side === 'B' && s.bIndex === idx);
// steps revealed by B-playback position `idx` (A-only steps appear once playback passes where they used to happen)
export const shownSteps = (chain, idx) => asList(chain?.steps).filter((st) => st.at <= idx);

// 3D overlay contribution of the chain at playback position `idx`: edge colours by causal role, root marks, inserted element.
export function causalLayer(chain, idx) {
  const edgePaints = dict(); const marks = dict();
  if (!chain || chain.status !== 'OK' || !chain.root) return { edgePaints, marks, overlayNodes: [], overlayEdges: [] };
  const el = chain.root.element;
  for (const id of asList(chain.root.edgeIds)) edgePaints[id] = { color: ROLE_COLOR.cause, alpha: 1 };
  if (el?.replacedEdgeId) edgePaints[el.replacedEdgeId] = { color: ROLE_COLOR.lost, alpha: 0.35 };
  for (const s of shownSteps(chain, idx)) {
    if (!s.edgeId) continue;
    const prev = edgePaints[s.edgeId];
    if (prev && prev.color === ROLE_COLOR.cause && !(el && asList(el.edges).some((x) => x.id === s.edgeId))) continue; // the cause stays marked
    edgePaints[s.edgeId] = { color: roleColor(s.role), alpha: s.role === 'lost' ? 0.4 : 1 };
  }
  for (const n of asList(chain.root.nodeIds)) marks[n] = 'cause';
  if (idx >= asList(chain.steps).reduce((m, st) => Math.max(m, st.at), -1)) for (const e of asList(chain.effects)) if (!Object.hasOwn(marks, e.nodeId)) marks[e.nodeId] = 'effect';
  const overlayNodes = el?.between ? [{ id: el.id, name: el.name, between: el.between, roomId: el.roomId ?? null }] : [];
  const overlayEdges = el ? asList(el.edges) : [];
  return { edgePaints, marks, overlayNodes, overlayEdges };
}

// --------- relevance over a fixed stage ---------
// Classes: Selected (current) / Direct (neighbours of a clicked node within the stage) / Reached / Stage / Outside.
// Same shape as relevance() of model-access, plus `stage` (the scope) so the renderer can hide what is off stage.
export function stageFocus(ix, scope, focus = {}) {
  const sel = new Set(focus.nodes || []); const pickedE = new Set(focus.edges || []);
  const dir = new Set(focus.nearNodes || []); const directE = new Set(focus.nearEdges || []);
  const rch = new Set(focus.passedNodes || []); const reachedE = new Set(focus.passedEdges || []);
  const nodes = new Set([...scope.nodes, ...sel]); const edges = new Set([...scope.edges, ...pickedE]);
  for (const id of sel) dir.delete(id);
  const nodeTier = (id) => (sel.has(id) ? 'Selected' : dir.has(id) ? 'Direct' : rch.has(id) ? 'Reached' : nodes.has(id) ? 'Stage' : 'Outside');
  const edgeTier = (e) => (pickedE.has(e.id) ? 'Selected' : directE.has(e.id) ? 'Direct' : reachedE.has(e.id) ? 'Reached' : edges.has(e.id) ? 'Stage' : 'Outside');
  return {
    active: true, stage: { nodes, edges }, key: [...nodes].sort().join(',') + '|' + [...edges].sort().join(','),
    chosenNodes: sel, chosenEdges: pickedE, nearNodes: dir, nearEdges: directE, backdropNodes: new Set(),
    nodeTier, edgeTier,
  };
}

// Focus of a click while the stage is up: the clicked node and its neighbours over stage edges, or the clicked edge and its two ends.
function focusOfClick(ix, scope, sel, element) {
  if (sel.type === 'element' && element) {
    return { nodes: [element.id, ...asList(element.between)], edges: asList(element.edges).map((x) => x.id) };
  }
  if (sel.type === 'edge') {
    const e = ix.edges.get(sel.id);
    return { nodes: [], edges: [sel.id], nearNodes: e ? [e.sourceId, e.targetId] : [] };
  }
  const near = incident(ix, sel.id).filter((e) => scope.edges.has(e.id));
  return { nodes: [sel.id], nearEdges: near.map((e) => e.id), nearNodes: near.flatMap((e) => [e.sourceId, e.targetId]) };
}

/**
 * Everything the stage shows at playback position idx: the relevance and the overlay (node states, edge colours, pulse, 5D marks).
 * sel: the user's click, which replaces the playback focus but keeps the stage. Returns null when there is nothing to play
 * (no run, a 5D tab without a causal chain, or a rejected pair whose chain status is not OK): the UI shows its empty state.
 */
export function replayView({ ix, tab, run, chain, idx, sel = null, warnings = null }) {
  if (!run || !Array.isArray(run.trace)) return null;
  const chainTab = tab === '5D';
  if (chainTab && (!chain?.root || chain.status !== 'OK')) return null; // no chain, or the pair was rejected: nothing to play
  const scope = chainTab ? chainStage(ix, chain) : caseStage(ix, run);
  const { i, ok } = normIndex(idx, run.trace.length);
  if (!ok) warnIndex(warnings, idx);
  const ev = i >= 0 ? run.trace[i] ?? null : null;
  const nodeStates = dict();
  if (run.trace.length) for (const [k, v] of Object.entries(seekState(run, i))) if (v !== 'Idle' && scope.nodes.has(k)) nodeStates[k] = v;
  const overlay = { nodeStates, edgePaints: dict(), pulses: [] };
  let focus; let live = []; // live: edges that carry the event animation at this position
  if (chainTab) {
    const co = causalLayer(chain, i);
    for (const [k, v] of Object.entries(co.edgePaints)) if (scope.edges.has(k)) overlay.edgePaints[k] = v;
    Object.assign(overlay, { marks: co.marks, overlayNodes: co.overlayNodes, overlayEdges: co.overlayEdges });
    const now = stepsPlayingAt(chain, i);
    live = now.filter((s) => s.edgeId).map((s) => s.edgeId);
    const past = shownSteps(chain, i).filter((s) => !now.includes(s));
    focus = {
      nodes: [...asList(chain.root.nodeIds), ...Object.keys(co.marks), ...co.overlayNodes.map((x) => x.id), ...now.flatMap((s) => [s.sourceNodeId, s.targetNodeId])],
      edges: [...asList(chain.root.edgeIds), ...co.overlayEdges.map((x) => x.id), ...live],
      passedNodes: past.flatMap((s) => [s.sourceNodeId, s.targetNodeId]), passedEdges: past.map((s) => s.edgeId).filter(Boolean),
    };
  } else {
    live = ev?.edgeId ? [ev.edgeId] : [];
    const past = run.trace.slice(0, Math.max(0, i));
    focus = {
      nodes: ev ? [ev.sourceNodeId, ev.targetNodeId] : [], edges: live,
      passedNodes: past.flatMap((e) => [e.sourceNodeId, e.targetNodeId]), passedEdges: past.map((e) => e.edgeId).filter(Boolean),
    };
  }
  for (const id of live) {
    const outcome = chainTab ? stepsPlayingAt(chain, i).find((s) => s.edgeId === id)?.outcome : ev?.outcome;
    if (!chainTab || !overlay.edgePaints[id]) overlay.edgePaints[id] = FAILED.includes(outcome) ? { kind: 'blocked' } : { kind: 'shared', color: ENERGY_COLOR };
    overlay.pulses.push({ edgeId: id, progress: 0.5, color: ENERGY_COLOR });
  }
  if (sel) {
    const cf = focusOfClick(ix, scope, sel, chain?.root?.element);
    focus = { ...cf, passedNodes: [...focus.passedNodes, ...focus.nodes], passedEdges: [...focus.passedEdges, ...focus.edges] };
  }
  return { scope, rel: stageFocus(ix, scope, focus), overlay, causal: chainTab ? asList(chain?.steps).some((st) => st.at === i) : true };
}

// What the scene draws while a stage is up: the stage only (the level still reduces it; the current event and a click always stay).
export function stageSubset(ix, layout, detail, rel) {
  const f = detail ? keepFocus(detail, rel) : { nodeIds: layout.shownNodeIds, edgeIds: layout.shownEdgeIds };
  return {
    nodeIds: new Set([...f.nodeIds].filter((id) => layout.shownNodeIds.has(id) && rel.nodeTier(id) !== 'Outside')),
    edgeIds: new Set([...f.edgeIds].filter((id) => (layout.shownEdgeIds.has(id) || rel.chosenEdges.has(id)) && ix.edges.has(id) && rel.edgeTier(ix.edges.get(id)) !== 'Outside')),
  };
}

