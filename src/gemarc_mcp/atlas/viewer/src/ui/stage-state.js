// Stage state (pure): what the 4D and 5D tabs play and how the player moves. The runs are precomputed and embedded by the build (the browser
// never simulates), and only SEALED items are in the bundle: a case with a baseline run is sealed, a pair with an embedded run is sealed. This
// module lists exactly those, so a case without a sealed result has no entry, no trace and no count of events anywhere in the page.
//
// Every function is total: a damaged or missing part of the bundle gives an empty list or a `reason`, never a throw.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { baselineFor, pairRuns, causalStepStops, clampIndex, stepIndex, traceOf } from '../playback.js';
import { asList, isRecord } from '../model-access.js';

export const STAGE_TABS = ['3D', '4D', '5D'];
export const PLAY_TICK_MS = 900; // autoplay: one step per tick
export const PLAY_ROWS_MAX = 120; // event rows listed at once (a window around the current step)

/** The first stage state: nothing chosen yet (the first sealed case is taken when the tab is drawn). */
export const playInitial = () => ({ caseId: null, changeId: null, idx: -1, playing: false, viewId: 'logical', frames: true, legend: false, diagram: 'sequence' });

/** Sealed cases of the model, in model order: [{caseId, title, category, length, status}]. */
export function playCases(ctx) {
  const out = [];
  for (const c of asList(ctx.cases)) {
    const run = baselineFor(ctx.runs, c.caseId);
    if (!run) continue;
    out.push({ caseId: c.caseId, title: typeof c.title === 'string' && c.title ? c.title : c.caseId, category: typeof c.category === 'string' ? c.category : '', length: traceOf(run).length, status: typeof run.status === 'string' ? run.status : 'OK' });
  }
  return out;
}

/** A pair whose comparison shows a difference (the engine's outcome class is not NO_DIFFERENCE and the pair ran). */
const playDiffers = (p) => p.status === 'OK' && p.outcomeClass !== '' && p.outcomeClass !== 'NO_DIFFERENCE';

/** How many cases of the model have no sealed result and are therefore not offered. */
export const playHiddenCount = (ctx) => Math.max(0, asList(ctx.cases).length - playCases(ctx).length);

/** The authored and sealed (change, case) pairs of a case: [{changeId, type, description, status, outcomeClass}]. */
export function playPairs(ctx, caseId) {
  const byId = new Map(asList(ctx.interventions).filter((iv) => isRecord(iv)).map((iv) => [iv.interventionId, iv]));
  return asList(ctx.pairs).filter((p) => p.caseId === caseId && typeof p.interventionId === 'string').map((p) => {
    const iv = byId.get(p.interventionId);
    return {
      changeId: p.interventionId, type: iv && typeof iv.type === 'string' ? iv.type : '', description: iv && typeof iv.description === 'string' && iv.description ? iv.description : p.interventionId,
      status: typeof p.status === 'string' ? p.status : 'OK', outcomeClass: isRecord(p.diff) && typeof p.diff.outcomeClass === 'string' ? p.diff.outcomeClass : '',
    };
  });
}

/**
 * What a stage tab can play now. tab: '4D' | '5D'; st: the stage state. Returns
 * { tab, cases, caseId, changeId, pairs, run, chain, A, length, stops, reason, errors }.
 * reason is null when there is a run to play; otherwise one of: 'no-cases' (the model has none), 'nothing-sealed', 'no-interventions',
 * 'no-pairs' (no sealed pair for this case), 'pair-status' (the pair was rejected or is a draft: its reason is shown, nothing plays),
 * 'run-status' (the engine stopped the run: LIMIT_EXCEEDED, DRAFT, REJECTED), 'no-run'.
 */
export function playResolve(ctx, tab, st) {
  const cases = playCases(ctx);
  const res = { tab, cases, caseId: null, changeId: null, pairs: [], run: null, chain: null, A: null, length: 0, stops: [], reason: null, errors: [], runStatus: null, truncated: false };
  if (!ctx.hasCases) { res.reason = 'no-cases'; return res; }
  if (tab === '5D' && !ctx.hasInterventions) { res.reason = 'no-interventions'; return res; }
  if (!cases.length) { res.reason = 'nothing-sealed'; return res; }
  const want = st && typeof st.caseId === 'string' ? st.caseId : null;
  // 5D opens on the first case that has a comparison with a difference (most comparisons of a real atlas show none: that would be an empty first impression)
  const openCase = tab === '5D' ? (cases.find((c) => playPairs(ctx, c.caseId).some(playDiffers)) || cases[0]) : cases[0];
  res.caseId = cases.some((c) => c.caseId === want) ? want : openCase.caseId;
  let run = null;
  if (tab === '5D') {
    res.pairs = playPairs(ctx, res.caseId);
    if (!res.pairs.length) { res.reason = 'no-pairs'; return res; }
    const wantIv = st && typeof st.changeId === 'string' ? st.changeId : null;
    res.changeId = res.pairs.some((p) => p.changeId === wantIv) ? wantIv : (res.pairs.find(playDiffers) || res.pairs[0]).changeId;
    const pair = pairRuns(ctx.runs, res.caseId, res.changeId);
    res.A = pair ? pair.A : baselineFor(ctx.runs, res.caseId);
    res.chain = pair ? pair.chain : null;
    res.errors = pair ? pair.errors : [];
    if (!pair || pair.status !== 'OK' || !pair.B || !res.chain || res.chain.status !== 'OK') { res.reason = 'pair-status'; return res; }
    run = pair.B;
  } else {
    run = baselineFor(ctx.runs, res.caseId);
    res.A = run;
  }
  if (!run || !Array.isArray(run.trace) || !run.trace.every(isRecord)) { res.reason = 'no-run'; return res; } // a trace with a hole in it is not a run the build wrote
  res.runStatus = typeof run.status === 'string' ? run.status : 'OK';
  res.truncated = run.truncated === true;
  res.errors = res.errors.length ? res.errors : asList(run.errors);
  if (res.runStatus !== 'OK') { res.reason = 'run-status'; return res; }
  res.run = run;
  res.length = run.trace.length;
  res.stops = tab === '5D' ? causalStepStops(res.chain, res.length) : [];
  return res;
}

// ---------- transitions ----------
export const playPickCase = (st, caseId) => ({ ...st, caseId, changeId: null, idx: -1, playing: false });
export const playPickPair = (st, changeId) => ({ ...st, changeId, idx: -1, playing: false });
export const playStop = (st) => (st.playing ? { ...st, playing: false } : st);

/** One move of the player: first | prev | next | last | prevCause | nextCause, or a number (seek). The index stays within -1..length-1. */
export function playMove(st, res, action) {
  const n = res.length;
  let idx = clampIndex(st.idx, n);
  if (typeof action === 'number') idx = clampIndex(action, n);
  else if (action === 'first') idx = -1;
  else if (action === 'last') idx = n - 1;
  else if (action === 'prev') idx = stepIndex(idx, n, -1);
  else if (action === 'next') idx = stepIndex(idx, n, 1);
  else if (action === 'prevCause') idx = stepIndex(idx, n, -1, res.stops);
  else if (action === 'nextCause') idx = stepIndex(idx, n, 1, res.stops);
  return { ...st, idx, playing: false };
}

/** Play or pause. Playing from the end starts again at the beginning. */
export function playToggle(st, res) {
  if (st.playing) return { ...st, playing: false };
  if (!res.length) return st;
  return { ...st, playing: true, idx: clampIndex(st.idx, res.length) >= res.length - 1 ? -1 : st.idx };
}

/** One autoplay tick: 4D walks every event, 5D jumps between the causal steps (the shared events carry no story). Stops at the end. */
export function playTick(st, res) {
  if (!st.playing || !res.length) return { ...st, playing: false };
  const idx = clampIndex(st.idx, res.length);
  const next = stepIndex(idx, res.length, 1, res.tab === '5D' ? res.stops : null);
  return { ...st, idx: next, playing: next < res.length - 1 && next !== idx };
}

// ---------- rows the tabs list ----------
const nodeName = (ctx, res, id) => {
  const n = ctx.ix.nodes.get(id);
  if (n && typeof n.name === 'string' && n.name) return n.name;
  const el = res.chain && res.chain.root ? res.chain.root.element : null;
  if (el && el.id === id && typeof el.name === 'string' && el.name) return el.name;
  return typeof id === 'string' ? id : '';
};

/** The event rows around the current step: [{index, from, to, type, outcome, now}] (a window of PLAY_ROWS_MAX when the trace is longer). */
export function playEventRows(ctx, res, idx) {
  const trace = res.run ? res.run.trace : [];
  const first = trace.length > PLAY_ROWS_MAX ? Math.max(0, Math.min(trace.length - PLAY_ROWS_MAX, idx - Math.floor(PLAY_ROWS_MAX / 3))) : 0;
  return trace.slice(first, first + PLAY_ROWS_MAX).map((ev, k) => ({
    index: first + k, from: nodeName(ctx, res, ev.sourceNodeId), to: nodeName(ctx, res, ev.targetNodeId), type: typeof ev.eventType === 'string' ? ev.eventType : '',
    outcome: typeof ev.outcome === 'string' ? ev.outcome : '', now: first + k === idx,
  }));
}

/** The causal steps of a chain with their display flags at position idx: [{key, side, role, at, from, to, outcome, why, shown, now}]. */
export function playChainRows(ctx, res, idx) {
  const steps = asList(res.chain && res.chain.steps).filter(isRecord);
  return steps.map((st, k) => ({
    key: typeof st.id === 'string' ? st.id : 'step-' + k, side: st.side === 'A' ? 'A' : 'B', role: typeof st.role === 'string' ? st.role : '', at: Number.isFinite(st.at) ? st.at : 0,
    from: nodeName(ctx, res, st.sourceNodeId), to: nodeName(ctx, res, st.targetNodeId), outcome: typeof st.outcome === 'string' ? st.outcome : '', why: typeof st.why === 'string' ? st.why : '',
    shown: Number.isFinite(st.at) && st.at <= idx, now: st.side === 'B' && st.bIndex === idx,
  })).sort((a, b) => a.at - b.at);
}

/** The effects of a chain, A against B: [{nodeId, name, a, b}] (states are engine values; the tab shows them as they are). */
export function playEffectRows(ctx, res) {
  return asList(res.chain && res.chain.effects).filter(isRecord).map((e) => ({ nodeId: e.nodeId, name: typeof e.name === 'string' && e.name ? e.name : nodeName(ctx, res, e.nodeId), a: typeof e.a === 'string' ? e.a : '', b: typeof e.b === 'string' ? e.b : '' }));
}

/** The state changes of a comparison for one line of text: { n, shown: [{name, a, b}] (at most `max`), more } (states are engine ids; the tab turns them into words). */
export function playChangeSummary(rows, max = 4) {
  const list = asList(rows);
  return { n: list.length, shown: list.slice(0, max).map((r) => ({ name: r.name, a: r.a || 'Idle', b: r.b || 'Idle' })), more: Math.max(0, list.length - max) };
}

/** The trace length the gate compares with the step count the player shows (the build records them as calibrationHooks.traceLengths). */
export function playExpectedLength(ctx, tab, caseId, changeId) {
  const rows = asList(ctx.bundle && ctx.bundle.calibrationHooks && ctx.bundle.calibrationHooks.traceLengths).filter(isRecord);
  const hit = rows.find((r) => r.caseId === caseId && (tab === '5D' ? r.interventionId === changeId : r.interventionId === null || r.interventionId === undefined));
  return hit && Number.isFinite(hit.length) ? hit.length : null;
}
