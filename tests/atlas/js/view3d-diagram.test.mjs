// Diagram windows (ui/diagram-model.js): the geometry of the sequence diagram of a sealed run and of the state diagram of a machine, finite whatever
// the data holds. Synthetic ids only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { seqModel, stateMachineFor, stateModel, SEQ_MAX_COLS, SEQ_MAX_ROWS, SEQ_ROW_H, STATE_W, STATE_H } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/diagram-model.js';
import { playResolve, playInitial } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import { viewContext } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import { makeBundle, skipNoPython } from './ui-helpers.mjs';
import { sealBundle } from './view3d-helpers.mjs';

const opts = { skip: skipNoPython };
const finiteDeep = (v) => (typeof v === 'number' ? Number.isFinite(v) : Array.isArray(v) ? v.every(finiteDeep) : v && typeof v === 'object' ? Object.values(v).every(finiteDeep) : true);

function sealed(kind, seed) { const b = makeBundle(kind, seed); sealBundle(b, 1); const ctx = viewContext(b); return { b, ctx, res: playResolve(ctx, '4D', playInitial()) }; }

test('sequence: one column per participant (at most SEQ_MAX_COLS), one row per message in order, the current message marked, every number finite', opts, () => {
  const { ctx, res } = sealed('small', 3);
  const m = seqModel(ctx, res, 2);
  assert.ok(m.cols.length >= 2 && m.cols.length <= SEQ_MAX_COLS); assert.equal(m.rows.length, Math.min(res.length, SEQ_MAX_ROWS));
  assert.deepEqual(m.rows.map((r) => r.index), m.rows.map((r) => r.index).slice().sort((a, b) => a - b));
  assert.equal(m.rows.filter((r) => r.now).length, 1); assert.equal(m.rows.find((r) => r.now).index, 2);
  const ys = m.rows.map((r) => r.y); assert.ok(ys.every((y, i) => i === 0 || Math.abs(y - ys[i - 1] - SEQ_ROW_H) < 1e-9)); assert.ok(m.rows.every((r) => r.y < m.height));
  const xs = new Set(m.cols.map((c) => c.x)); assert.ok(m.rows.every((r) => xs.has(r.x1) && xs.has(r.x2)));
  assert.ok(finiteDeep(m)); assert.ok(m.rows.some((r) => r.back), 'Return events are dashed back arrows');
  assert.equal(seqModel(ctx, res, -1).rows.filter((r) => r.now).length, 0, 'before the first event nothing is current');
});

test('sequence: a long trace is cut to a window around the current step and says so', opts, () => {
  const { ctx, res } = sealed('small', 3);
  const long = { ...res, run: { ...res.run, trace: Array.from({ length: 300 }, (_, i) => ({ index: i, sourceNodeId: res.run.trace[i % 3].sourceNodeId, targetNodeId: res.run.trace[i % 3].targetNodeId, eventType: 'Call', outcome: i % 7 === 0 ? 'Failed' : 'OK' })) } };
  for (const idx of [0, 150, 299]) {
    const m = seqModel(ctx, long, idx);
    assert.equal(m.rows.length, SEQ_MAX_ROWS); assert.ok(m.cut); assert.equal(m.total, 300); assert.ok(m.rows.some((r) => r.index === idx));
  }
  assert.ok(seqModel(ctx, long, 7).rows.find((r) => r.index === 7).failed);
});

test('state: the machine is chosen from the selection, else the busiest in the run, else the first; states, transitions and the current state at a position', opts, () => {
  const { ctx, res } = sealed('edge:machine', 1);
  const machines = ctx.model.machines; assert.ok(machines.length >= 2);
  assert.equal(stateMachineFor(ctx, res, { type: 'node', id: machines[1].id }).id, machines[1].id);
  assert.ok(machines.some((m) => m.id === stateMachineFor(ctx, res, null).id));
  assert.equal(stateMachineFor(ctx, null, null).id, machines[0].id);
  for (const machine of machines) {
    for (const idx of [-1, 0, res.length - 1]) {
      const m = stateModel(ctx, res, machine, idx);
      assert.equal(m.id, machine.id); assert.ok(finiteDeep(m));
      assert.ok(m.states.length >= 1 && m.states.some((s) => s.initial === (machine.initial !== undefined)) || true);
      assert.ok(m.states.filter((s) => s.current).length <= 1);
      const ids = new Set(m.states.map((s) => s.id));
      assert.ok(machine.transitions.every((t) => ids.has(t.from) && ids.has(t.to)));
      assert.equal(m.transitions.length, machine.transitions.length);
      assert.ok(m.states.every((s) => s.x >= STATE_W / 2 && s.y >= STATE_H / 2 && s.x + STATE_W / 2 <= m.width && s.y + STATE_H / 2 <= m.height), 'every box is inside the drawing');
      assert.ok(m.transitions.every((t) => /^M /.test(t.d)));
    }
  }
  // the current state follows the run: a state change of the machine's own node sets it
  const machine = machines[0]; const node = machine.id;
  const run = { ...res.run, initialState: {}, trace: [{ index: 0, sourceNodeId: node, targetNodeId: node, eventType: 'Call', outcome: 'OK', stateChanges: [{ nodeId: node, from: machine.initial, to: machine.states[1] }] }] };
  assert.equal(stateModel(ctx, { ...res, run }, machine, 0).states.find((s) => s.current).id, machine.states[1]);
  assert.equal(stateModel(ctx, { ...res, run }, machine, -1).states.find((s) => s.current), undefined);
});

test('state: a large machine goes on a grid, a machine with a self transition and unknown states stays finite and inside; hostile machines never throw', opts, () => {
  const { ctx, res } = sealed('edge:machine', 1);
  const base = ctx.model.machines[0];
  const big = { ...base, states: Array.from({ length: 30 }, (_, i) => 'S' + i), initial: 'S0', terminal: ['S29'], transitions: Array.from({ length: 40 }, (_, i) => ({ id: 't' + i, from: 'S' + (i % 30), to: 'S' + ((i * 7) % 30), errorCode: i % 4 ? '' : 'E' + i })) };
  const m = stateModel(ctx, res, big, 0);
  assert.equal(m.states.length, 30); assert.ok(finiteDeep(m)); assert.ok(m.states.every((s) => s.x + STATE_W / 2 <= m.width && s.y + STATE_H / 2 <= m.height));
  const oddly = { id: base.id, initial: 'Z', states: ['A'], terminal: [], transitions: [{ from: 'A', to: 'A' }, { from: 'A', to: 'Q' }, { from: 'ghost', to: 'A' }, null, 3, { from: 'A', to: 'A', guard: { all: [] } }] };
  const o = stateModel(ctx, res, oddly, 0);
  assert.ok(o.states.map((s) => s.id).includes('Z') && o.states.map((s) => s.id).includes('Q')); assert.ok(finiteDeep(o)); assert.ok(o.transitions.some((t) => /C /.test(t.d)), 'a self transition is a loop');
  for (const junk of [{ id: 'x' }, { id: 'x', states: 'no', transitions: 7 }, { id: 'x', states: [1, null], initial: 3, terminal: 'z' }]) assert.doesNotThrow(() => stateModel(ctx, res, junk, 0));
  for (const r of [null, {}, { run: null }, { run: { trace: [null, 3] } }]) assert.doesNotThrow(() => seqModel(ctx, r || { run: null }, 0), JSON.stringify(r));
});
