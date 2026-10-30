// 1D tab logic (pure): the selection state, the picker lists and the document text. The text is produced by assemble() of assemble.js, the
// JavaScript twin of the Python document builder, over the embedded fragments and closures; this module only chooses the arguments and keeps
// assemble from throwing on a damaged bundle. The twin check recomputes every calibrationHooks.docSelections hash in the page.
//
// Selection modes: 'requirement' = one requirement and its closure, 'element' = one node, edge or case and its closure, 'free' = any
// combination of ids (checkboxes across kinds). The level is separate (REQ, BD, DD from the vocabulary).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { assemble, hash32Hex } from '../assemble.js';
import { asList, isRecord } from '../model-access.js';

export const DOC_MODES = ['requirement', 'element', 'free'];
export const DOC_LEVELS = ['REQ', 'BD', 'DD'];
const DOC_CHAPTER_HEAD = /^## (\d+)\. (.*)$/;

/** The dataset assemble() needs, or null when the bundle carries no usable fragments. */
export function docDataset(ctx) {
  const f = ctx.bundle.fragments; const c = ctx.bundle.closures;
  if (!isRecord(f) || !isRecord(c) || !Array.isArray(f.items) || !Array.isArray(f.elements) || !Array.isArray(f.always) || !isRecord(f.frame) || !isRecord(f.groups)) return null;
  return { fragments: f, closures: c };
}

/** Element ids that have a closure entry: the only ids the document can be built for. */
export function docKnownIds(ctx) {
  const ds = docDataset(ctx);
  return ds ? new Set(ds.fragments.elements.filter((id) => Array.isArray(ds.closures[id]))) : new Set();
}

/** The initial state: the route (req / level / mode) wins, else the first requirement, else the first element. */
export function docInitial(ctx, level, params) {
  const known = docKnownIds(ctx);
  const lv = DOC_LEVELS.includes(level) ? level : (ctx.levels.includes(level) ? level : DOC_LEVELS[1]);
  const reqs = ctx.requirements.filter((r) => known.has(r.id));
  const st = { mode: 'requirement', level: DOC_LEVELS.includes(lv) ? lv : DOC_LEVELS[1], reqId: reqs.length ? reqs[0].id : null, elementId: null, ids: [], filter: '' };
  const p = isRecord(params) ? params : {};
  if (typeof p.req === 'string' && known.has(p.req)) { st.reqId = p.req; st.mode = 'requirement'; }
  if (!reqs.length) {
    st.mode = 'element';
    const first = docKnownList(ctx, known).nodes[0];
    st.elementId = first ? first.id : null;
  }
  if (typeof p.mode === 'string' && DOC_MODES.includes(p.mode)) st.mode = p.mode;
  return st;
}

/** Picker lists restricted to the ids the document can be built for: {requirements, cases, nodes, edges} of {id, title}. */
export function docKnownList(ctx, knownIds) {
  const known = knownIds || docKnownIds(ctx);
  const row = (id, title) => ({ id, title: typeof title === 'string' && title ? title : id });
  return {
    requirements: ctx.requirements.filter((r) => known.has(r.id)).map((r) => row(r.id, r.title)),
    cases: ctx.cases.filter((c) => known.has(c.caseId)).map((c) => row(c.caseId, c.title)),
    nodes: ctx.nodes.filter((n) => known.has(n.id)).map((n) => row(n.id, n.name)),
    edges: ctx.edges.filter((e) => known.has(e.id)).map((e) => row(e.id, e.label)),
  };
}

/** The element ids the state selects (a subset of the known ids, in the order they were chosen). */
export function docIds(state, ctx) {
  const known = docKnownIds(ctx);
  if (state.mode === 'requirement') return state.reqId && known.has(state.reqId) ? [state.reqId] : [];
  if (state.mode === 'element') return state.elementId && known.has(state.elementId) ? [state.elementId] : [];
  return asList(state.ids).filter((id) => typeof id === 'string' && known.has(id));
}

/** The document text for a state: { ok, text, ids, level, bytes, lines, error }. Never throws. */
export function docText(ctx, state) {
  const ds = docDataset(ctx);
  const ids = docIds(state, ctx);
  if (!ds) return { ok: false, text: '', ids, level: state.level, bytes: 0, lines: 0, error: 'no fragments' }; // diag: author-facing
  try {
    const text = assemble(ds, state.level, ids);
    return { ok: true, text, ids, level: state.level, bytes: new TextEncoder().encode(text).length, lines: text.split('\n').length - 1, error: null };
  } catch (e) {
    return { ok: false, text: '', ids, level: state.level, bytes: 0, lines: 0, error: String(e && e.message ? e.message : e) };
  }
}

/** Twin check: assemble every calibration selection in the page and compare the 32-bit hash with the one the build computed in Python. */
export function docCheckAll(ctx) {
  const ds = docDataset(ctx);
  const hooks = isRecord(ctx.bundle.calibrationHooks) ? ctx.bundle.calibrationHooks : {};
  return asList(hooks.docSelections).filter(isRecord).map((s) => {
    const ids = asList(s.elementIds).filter((x) => typeof x === 'string');
    let actual = null;
    if (ds && DOC_LEVELS.includes(s.level)) {
      try { actual = hash32Hex(assemble(ds, s.level, ids)); } catch (e) { actual = null; }
    }
    return { level: String(s.level), count: ids.length, expected: typeof s.hash32 === 'string' ? s.hash32 : null, actual, ok: actual !== null && actual === s.hash32 };
  });
}

/** Chapter outline of a document text: [{n, title, line}] from the `## n. title` headings (the same headings the Python lint checks). */
export function docOutline(text) {
  const out = []; const lines = String(text).split('\n');
  for (let i = 0; i < lines.length; i++) {
    const m = DOC_CHAPTER_HEAD.exec(lines[i]);
    if (m) out.push({ n: Number(m[1]), title: m[2], line: i });
  }
  return out;
}

// ---------- reducers (state is never mutated) ----------
export const docSetLevel = (st, level) => (DOC_LEVELS.includes(level) ? { ...st, level } : st);
export const docSetMode = (st, mode) => (DOC_MODES.includes(mode) ? { ...st, mode } : st);
export const docSetRequirement = (st, reqId) => ({ ...st, mode: 'requirement', reqId });
export const docSetElement = (st, elementId) => ({ ...st, mode: 'element', elementId });
export const docSetFilter = (st, filter) => ({ ...st, filter: String(filter).slice(0, 200) });
export function docToggle(st, id) {
  const has = st.ids.includes(id);
  return { ...st, mode: 'free', ids: has ? st.ids.filter((x) => x !== id) : [...st.ids, id] };
}
export const docSetAll = (st, ids) => ({ ...st, mode: 'free', ids: [...ids] });
export const docClear = (st) => ({ ...st, ids: [] });

/** Picker rows filtered by the text box (case and width insensitive, every word must match id or title). */
export function docFilterRows(rows, filter) {
  const words = String(filter || '').normalize('NFKC').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return rows;
  return rows.filter((r) => { const hay = (r.id + ' ' + r.title).normalize('NFKC').toLowerCase(); return words.every((w) => hay.includes(w)); });
}
