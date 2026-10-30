// Diagram windows (pure): the geometry of a sequence diagram (the messages of a sealed run) and of a state diagram (one state machine of
// the model, with the state a playback position is in). The windows are drawn natively as SVG from the embedded runs and the model, so no
// diagram library is needed and the drawing always matches the playback position. Numbers are finite whatever the data holds.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { asList, isRecord, own } from '../model-access.js';
import { seekState, changesOf, traceOf } from '../playback.js';
import { truncateText } from '../label-fit.js';

export const SEQ_COL_W = 120;
export const SEQ_ROW_H = 22;
export const SEQ_HEAD_H = 34;
export const SEQ_MAX_COLS = 10;
export const SEQ_MAX_ROWS = 40;
export const STATE_W = 112;
export const STATE_H = 30;
export const STATE_PAD = 24;
const SEQ_FAILED = ['Blocked', 'Denied', 'Failed', 'Timeout'];

const diagramNodeName = (ctx, res, id) => {
  const n = ctx.ix.nodes.get(id);
  if (n && typeof n.name === 'string' && n.name) return n.name;
  const el = res && res.chain && res.chain.root ? res.chain.root.element : null;
  if (el && el.id === id && typeof el.name === 'string' && el.name) return el.name;
  return typeof id === 'string' ? id : '';
};

/**
 * Sequence diagram of the run: { cols: [{id, name, x}], rows: [{index, x1, x2, y, type, outcome, label (raw ids), failed, now, self, back}], width, height, from, total, cut }.
 * At most SEQ_MAX_COLS participants (the busiest first-seen ones) and SEQ_MAX_ROWS messages around the current step; `cut` says the trace was shortened.
 */
export function seqModel(ctx, res, idx) {
  const trace = traceOf(res && res.run).map((e) => (isRecord(e) ? e : {}));
  const first = trace.length > SEQ_MAX_ROWS ? Math.max(0, Math.min(trace.length - SEQ_MAX_ROWS, idx - Math.floor(SEQ_MAX_ROWS / 3))) : 0;
  const part = trace.slice(first, first + SEQ_MAX_ROWS);
  const ids = [];
  for (const ev of part) for (const id of [ev.sourceNodeId, ev.targetNodeId]) if (typeof id === 'string' && !ids.includes(id)) ids.push(id);
  const shown = ids.slice(0, SEQ_MAX_COLS);
  const cols = shown.map((id, i) => ({ id, name: diagramNodeName(ctx, res, id), x: SEQ_COL_W / 2 + i * SEQ_COL_W }));
  const at = new Map(cols.map((c) => [c.id, c.x]));
  const rows = [];
  part.forEach((ev, k) => {
    const x1 = at.get(ev.sourceNodeId); const x2 = at.get(ev.targetNodeId);
    if (x1 === undefined || x2 === undefined) return;
    rows.push({
      index: first + k, x1, x2, y: SEQ_HEAD_H + SEQ_ROW_H * (rows.length + 0.7), type: typeof ev.eventType === 'string' ? ev.eventType : '', outcome: typeof ev.outcome === 'string' ? ev.outcome : '', label: (typeof ev.eventType === 'string' ? ev.eventType : '') + (typeof ev.outcome === 'string' && ev.outcome ? ' ' + ev.outcome : ''),
      failed: SEQ_FAILED.includes(ev.outcome), now: first + k === idx, self: x1 === x2, back: ev.eventType === 'Return', // diag: engine event type id, not shown text
    });
  });
  return { cols, rows, width: Math.max(SEQ_COL_W, cols.length * SEQ_COL_W), height: SEQ_HEAD_H + SEQ_ROW_H * (rows.length + 1), from: first, total: trace.length, cut: part.length < trace.length || ids.length > shown.length };
}

/** The machine a state window shows: the selected machine node, else the busiest machine in the run, else the first machine of the model. */
export function stateMachineFor(ctx, res, sel) {
  const machines = asList(ctx.model.machines).filter((m) => isRecord(m) && typeof m.id === 'string');
  if (!machines.length) return null;
  if (sel && sel.type === 'node') { const m = machines.find((x) => x.id === sel.id); if (m) return m; }
  const count = new Map();
  for (const ev of traceOf(res && res.run)) for (const c of changesOf(ev)) count.set(c.nodeId, (count.get(c.nodeId) || 0) + 1);
  let best = null;
  for (const m of machines) if (count.has(m.id) && (!best || count.get(m.id) > count.get(best.id))) best = m;
  return best || machines[0];
}

const diaFinite = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);

/** The path of a transition between two state boxes (a quadratic curve, a loop above the box for a self transition). */
function transitionPath(a, b, swing) {
  if (a === b) return { d: 'M ' + (a.x - 14) + ' ' + a.y + ' C ' + (a.x - 30) + ' ' + (a.y - 46) + ' ' + (a.x + 30) + ' ' + (a.y - 46) + ' ' + (a.x + 14) + ' ' + a.y, lx: a.x, ly: a.y - 38 };
  const dx = b.x - a.x; const dy = b.y - a.y; const len = Math.hypot(dx, dy) || 1;
  // leave and enter at the box edge towards the other box
  const edge = (p, ux, uy) => { const k = Math.min(ux ? (STATE_W / 2) / Math.abs(ux) : Infinity, uy ? (STATE_H / 2) / Math.abs(uy) : Infinity); return { x: p.x + ux * k, y: p.y + uy * k }; };
  const ux = dx / len; const uy = dy / len;
  const s = edge(a, ux, uy); const e = edge(b, -ux, -uy);
  const mx = (s.x + e.x) / 2 + (-uy) * swing; const my = (s.y + e.y) / 2 + ux * swing;
  return { d: 'M ' + s.x + ' ' + s.y + ' Q ' + mx + ' ' + my + ' ' + e.x + ' ' + e.y, lx: (s.x + 2 * mx + e.x) / 4, ly: (s.y + 2 * my + e.y) / 4 };
}

/**
 * State diagram of a machine at playback position idx: { id, name, states: [{id, x, y, w, h, initial, terminal, current}], transitions: [{id, d, lx, ly, label}],
 * width, height }. States sit on a ring (up to 8) or a grid; `current` marks the state the machine node is in at idx (null when the run never touches it).
 */
export function stateModel(ctx, res, machine, idx) {
  const ids = [...new Set([...asList(machine.states), machine.initial, ...asList(machine.terminal), ...asList(machine.transitions).flatMap((t) => (isRecord(t) ? [t.from, t.to] : []))].filter((s) => typeof s === 'string' && s))];
  const n = ids.length;
  const current = res && res.run ? own(seekState(res.run, idx), machine.id) ?? null : null;
  const pos = new Map();
  if (n <= 8) {
    const r = Math.max(70, n * 22);
    ids.forEach((id, i) => { const th = n === 1 ? 0 : (i / n) * Math.PI * 2 - Math.PI / 2; pos.set(id, { x: STATE_PAD + STATE_W / 2 + r * 1.4 + Math.cos(th) * r * 1.4, y: STATE_PAD + STATE_H / 2 + 46 + r + Math.sin(th) * r }); });
  } else {
    const cols = Math.ceil(Math.sqrt(n * 1.4));
    ids.forEach((id, i) => pos.set(id, { x: STATE_PAD + STATE_W / 2 + (i % cols) * (STATE_W + 36), y: STATE_PAD + STATE_H / 2 + 20 + Math.floor(i / cols) * (STATE_H + 50) }));
  }
  const terminal = new Set(asList(machine.terminal));
  const states = ids.map((id) => { const p = pos.get(id); return { id, x: p.x, y: p.y, w: STATE_W, h: STATE_H, initial: id === machine.initial, terminal: terminal.has(id), current: current === id }; });
  const seen = new Map();
  const transitions = [];
  asList(machine.transitions).filter(isRecord).slice(0, 80).forEach((t, k) => {
    const a = pos.get(t.from); const b = pos.get(t.to);
    if (!a || !b) return;
    const key = [t.from, t.to].sort().join('|'); const c = seen.get(key) || 0; seen.set(key, c + 1);
    const p = transitionPath(a, b, a === b ? 0 : 18 * (c % 2 ? -1 : 1) * Math.ceil(c / 2) + (t.from > t.to ? 10 : -10));
    const label = typeof t.label === 'string' && t.label ? t.label : typeof t.guard === 'string' ? t.guard : typeof t.errorCode === 'string' ? t.errorCode : '';
    transitions.push({ id: typeof t.id === 'string' ? t.id : 'transition-' + k, d: p.d, lx: diaFinite(p.lx, 0), ly: diaFinite(p.ly, 0), label: truncateText(label, 90, 10) });
  });
  const width = Math.max(240, ...states.map((s) => s.x + STATE_W / 2 + STATE_PAD)); const height = Math.max(120, ...states.map((s) => s.y + STATE_H / 2 + STATE_PAD + 24));
  return { id: machine.id, name: diagramNodeName(ctx, res, machine.id), states, transitions, width, height };
}
