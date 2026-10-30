// Stage state (ui/stage-state.js): only SEALED items are offered, every reason for an empty or non-playable stage, the player transitions never leave
// the trace, the rows the tabs list, and hostile bundles. Synthetic ids only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  playInitial, playCases, playHiddenCount, playPairs, playResolve, playPickCase, playPickPair, playMove, playToggle, playTick, playEventRows, playChainRows, playEffectRows,
  playExpectedLength, PLAY_ROWS_MAX,
} from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import { viewContext } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import { makeBundle, skipNoPython } from './ui-helpers.mjs';
import { sealBundle, fakeRun } from './view3d-helpers.mjs';

const opts = { skip: skipNoPython };
const ctxOf = (bundle) => viewContext(bundle);
const sealedOne = () => { const b = makeBundle('small', 3); const { sealed } = sealBundle(b, 1); return { b, sealed }; };

test('only sealed cases are offered: the case without a baseline has no entry, no title and no count anywhere in the list', opts, () => {
  const { b, sealed } = sealedOne();
  const ctx = ctxOf(b);
  const cases = playCases(ctx);
  assert.deepEqual(cases.map((c) => c.caseId), sealed);
  assert.equal(cases[0].length, b.runs.baselines[sealed[0]].trace.length);
  assert.equal(playHiddenCount(ctx), b.model.cases.length - 1);
  const hidden = b.model.cases.filter((c) => !sealed.includes(c.caseId));
  const text = JSON.stringify(cases);
  for (const h of hidden) { assert.ok(!text.includes(h.caseId), h.caseId); assert.ok(!text.includes(h.title), h.title); }
  // a stale baseline for an id that is not a case of the model is ignored
  b.runs.baselines['no-such-case'] = fakeRun(b.model, sealed[0]);
  assert.deepEqual(playCases(ctxOf(b)).map((c) => c.caseId), sealed);
});

test('resolve: every reason for a stage that cannot play, in the order of the empty states', opts, () => {
  const none = makeBundle('edge:no-cases', 4);
  assert.equal(playResolve(ctxOf(none), '4D', playInitial()).reason, 'no-cases');
  assert.equal(playResolve(ctxOf(none), '5D', playInitial()).reason, 'no-cases');
  const noIv = makeBundle('edge:no-interventions', 8);
  assert.equal(playResolve(ctxOf(noIv), '4D', playInitial()).reason, 'nothing-sealed');
  assert.equal(playResolve(ctxOf(noIv), '5D', playInitial()).reason, 'no-interventions', 'no change authored beats nothing sealed (the old empty-state order)');
  const small = makeBundle('small', 3);
  assert.equal(playResolve(ctxOf(small), '4D', playInitial()).reason, 'nothing-sealed');
  assert.equal(playResolve(ctxOf(small), '5D', playInitial()).reason, 'nothing-sealed');
  const { b, sealed } = sealedOne();
  const ctx = ctxOf(b);
  const r4 = playResolve(ctx, '4D', playInitial());
  assert.equal(r4.reason, null); assert.equal(r4.caseId, sealed[0]); assert.ok(r4.run && r4.length > 0 && r4.stops.length === 0);
  const r5 = playResolve(ctx, '5D', playInitial());
  assert.equal(r5.reason, null); assert.ok(r5.chain && r5.pairs.length >= 1 && r5.stops.length >= 1); assert.equal(r5.A, b.runs.baselines[sealed[0]]);
  // a sealed case with no sealed pair
  b.runs.pairs = [];
  assert.equal(playResolve(ctxOf(b), '5D', playInitial()).reason, 'no-pairs');
});

test('resolve: a rejected pair, a draft chain and a run the engine stopped show their reason and nothing plays', opts, () => {
  const { b, sealed } = sealedOne();
  const pairs = b.runs.pairs.filter((p) => p.caseId === sealed[0]);
  assert.ok(pairs.length >= 1);
  const keep = pairs.map((p) => ({ B: p.B, status: p.status }));
  pairs.forEach((p) => { p.status = 'REJECTED'; p.B = null; });
  const r = playResolve(ctxOf(b), '5D', { ...playInitial(), caseId: sealed[0] });
  assert.equal(r.reason, 'pair-status'); assert.equal(r.run, null);
  pairs.forEach((p, i) => { p.status = keep[i].status; p.B = fakeRun(b.model, sealed[0]); p.chain.status = 'DRAFT'; });
  assert.equal(playResolve(ctxOf(b), '5D', { ...playInitial(), caseId: sealed[0] }).reason, 'pair-status');
  b.runs.baselines[sealed[0]].status = 'LIMIT_EXCEEDED'; b.runs.baselines[sealed[0]].truncated = true;
  const r4 = playResolve(ctxOf(b), '4D', { ...playInitial(), caseId: sealed[0] });
  assert.equal(r4.reason, 'run-status'); assert.equal(r4.runStatus, 'LIMIT_EXCEEDED'); assert.equal(r4.truncated, true); assert.equal(r4.run, null);
  b.runs.baselines[sealed[0]].trace = 'not a list';
  assert.equal(playResolve(ctxOf(b), '4D', { ...playInitial(), caseId: sealed[0] }).reason, 'no-run');
});

test('resolve: an unknown case or change in the state falls back to the first sealed one', opts, () => {
  const { b, sealed } = sealedOne();
  const ctx = ctxOf(b);
  assert.equal(playResolve(ctx, '4D', { ...playInitial(), caseId: 'nope' }).caseId, sealed[0]);
  const r = playResolve(ctx, '5D', { ...playInitial(), caseId: sealed[0], changeId: 'nope' });
  assert.equal(r.changeId, r.pairs[0].changeId);
  assert.deepEqual(playPairs(ctx, 'nope'), []);
});

test('player: moves stay inside -1..length-1 whatever comes in; play from the end restarts; a tick stops at the end', opts, () => {
  const { b } = sealedOne();
  const ctx = ctxOf(b); const res = playResolve(ctx, '4D', playInitial()); const n = res.length;
  let st = { ...playInitial(), idx: -1 };
  st = playMove(st, res, 'prev'); assert.equal(st.idx, -1);
  st = playMove(st, res, 'next'); assert.equal(st.idx, 0);
  st = playMove(st, res, 'last'); assert.equal(st.idx, n - 1);
  st = playMove(st, res, 'next'); assert.equal(st.idx, n - 1);
  st = playMove(st, res, 'first'); assert.equal(st.idx, -1);
  for (const bad of [NaN, 'x', null, undefined, Infinity, -Infinity, 1e9, -1e9, 2.7]) {
    const s = playMove(st, res, bad); assert.ok(Number.isInteger(s.idx) && s.idx >= -1 && s.idx <= n - 1, String(bad));
  }
  assert.equal(playMove({ ...st, idx: 'junk' }, res, 'next').idx, 0, 'a damaged position counts as before the first event');
  let play = playToggle({ ...playInitial(), idx: n - 1 }, res);
  assert.equal(play.playing, true); assert.equal(play.idx, -1, 'play from the end starts again');
  let ticks = 0;
  while (play.playing && ticks < n + 5) { play = playTick(play, res); ticks++; }
  assert.equal(play.idx, n - 1); assert.equal(play.playing, false); assert.ok(ticks <= n);
  assert.equal(playTick({ ...playInitial(), playing: false }, res).playing, false);
  assert.equal(playToggle({ ...playInitial(), playing: true }, res).playing, false);
  assert.equal(playToggle(playInitial(), { ...res, length: 0 }).playing, false, 'nothing to play');
  assert.equal(playPickCase({ ...play, idx: 4 }, 'x').idx, -1); assert.equal(playPickPair({ ...play, idx: 4, playing: true }, 'y').playing, false);
});

test('player (5D): autoplay and the cause buttons jump between the causal steps, the plain buttons step every event', opts, () => {
  const { b } = sealedOne();
  const ctx = ctxOf(b); const res = playResolve(ctx, '5D', playInitial());
  assert.ok(res.stops.length >= 1 && res.stops[res.stops.length - 1] === res.length - 1, 'the last position is always a stop');
  let st = playMove({ ...playInitial(), idx: -1 }, res, 'nextCause');
  assert.equal(st.idx, res.stops[0]);
  st = playMove(st, res, 'prevCause'); assert.equal(st.idx, -1);
  const plain = playMove({ ...playInitial(), idx: -1 }, res, 'next'); assert.equal(plain.idx, 0);
  const seen = []; let play = playToggle(playInitial(), res);
  for (let i = 0; i < res.length + 3 && play.playing; i++) { play = playTick(play, res); seen.push(play.idx); }
  assert.deepEqual(seen.filter((x, i) => seen.indexOf(x) === i), seen.filter((x, i) => seen.indexOf(x) === i).sort((x, y) => x - y));
  assert.ok(seen.every((i) => res.stops.includes(i)), 'autoplay only visits the stops');
});

test('rows: events are a window around the current step, chain rows say what is shown and what is playing, effects are listed', opts, () => {
  const { b, sealed } = sealedOne();
  const ctx = ctxOf(b); const res = playResolve(ctx, '5D', playInitial());
  const rows = playEventRows(ctx, res, 2);
  assert.equal(rows.length, res.length); assert.equal(rows.filter((r) => r.now).length, 1); assert.equal(rows.find((r) => r.now).index, 2);
  assert.ok(rows.every((r) => typeof r.from === 'string' && typeof r.to === 'string'));
  const chain = playChainRows(ctx, res, 1);
  assert.ok(chain.length >= 2); assert.deepEqual(chain.map((c) => c.at), chain.map((c) => c.at).slice().sort((x, y) => x - y));
  assert.ok(chain.filter((c) => c.shown).every((c) => c.at <= 1)); assert.ok(chain.some((c) => c.now && c.side === 'B')); assert.ok(chain.some((c) => c.side === 'A'));
  assert.equal(playEffectRows(ctx, res).length, 1);
  assert.equal(playExpectedLength(ctx, '4D', sealed[0], null), b.runs.baselines[sealed[0]].trace.length);
  assert.equal(playExpectedLength(ctx, '5D', sealed[0], res.changeId), res.length);
  assert.equal(playExpectedLength(ctx, '4D', 'nope', null), null);
  // a long trace lists a window of PLAY_ROWS_MAX rows that contains the current step
  const long = { ...res, run: { ...res.run, trace: Array.from({ length: 500 }, (_, i) => ({ index: i, sourceNodeId: res.run.trace[0].sourceNodeId, targetNodeId: res.run.trace[0].targetNodeId, eventType: 'Call', outcome: 'OK' })) } };
  for (const at of [-1, 0, 250, 499]) { const w = playEventRows(ctx, long, at); assert.equal(w.length, PLAY_ROWS_MAX); if (at >= 0) assert.ok(w.some((r) => r.index === at), String(at)); }
});

test('hostile data: damaged runs, pairs and chains never throw and give empty lists or a reason', opts, () => {
  const { b } = sealedOne();
  const junk = [null, 3, 'x', [], {}, { trace: null }, { trace: [null, 3, { sourceNodeId: 7 }] }];
  for (const j of junk) {
    const bad = JSON.parse(JSON.stringify(b));
    bad.runs.baselines[Object.keys(bad.runs.baselines)[0]] = j;
    bad.runs.pairs = [j, { caseId: bad.model.cases[0].caseId, interventionId: 'q', status: 'OK', B: j, chain: j }];
    const ctx = ctxOf(bad);
    for (const tab of ['4D', '5D']) {
      let r; assert.doesNotThrow(() => { r = playResolve(ctx, tab, playInitial()); }, tab);
      assert.ok(r.reason !== undefined);
      if (r.run) { assert.doesNotThrow(() => { playEventRows(ctx, r, 0); playChainRows(ctx, r, 0); playEffectRows(ctx, r); }); }
    }
  }
  const chainless = JSON.parse(JSON.stringify(b)); chainless.runs.pairs.forEach((p) => { p.chain = { status: 'OK', steps: 'no', effects: 7, root: {} }; });
  const r = playResolve(ctxOf(chainless), '5D', playInitial());
  assert.doesNotThrow(() => { playChainRows(ctxOf(chainless), r, 0); playEffectRows(ctxOf(chainless), r); });
});

test('5D opens on a comparison that shows a difference when there is one (most comparisons of an atlas show none), 4D on the first sealed case', opts, () => {
  const { b, sealed } = sealedOne();
  const ctx0 = ctxOf(b);
  const own = playPairs(ctx0, sealed[0]);
  assert.ok(own.length >= 1);
  b.runs.pairs.forEach((p) => { p.diff = { outcomeClass: 'NO_DIFFERENCE' }; });
  assert.equal(playResolve(ctxOf(b), '5D', playInitial()).changeId, own[0].changeId, 'all alike: the first');
  const target = b.runs.pairs[b.runs.pairs.length - 1];
  target.diff = { outcomeClass: 'DIVERGED' };
  assert.equal(playResolve(ctxOf(b), '5D', playInitial()).changeId, target.interventionId, 'the one with a difference');
  assert.equal(playResolve(ctxOf(b), '4D', playInitial()).caseId, sealed[0]);
  // a rejected pair is never the default when another ran
  target.status = 'REJECTED';
  assert.notEqual(playResolve(ctxOf(b), '5D', playInitial()).changeId, target.interventionId);
});
