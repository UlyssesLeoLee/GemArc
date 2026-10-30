// Global search: every hit is one link to one page, tagged with the dimension (1D..5D / QA) that page belongs to. Pure functions; the UI only
// renders hits and follows `target`. Hits carry a `kind` key (node, edge, ...); the UI translates it, there is no display text here.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { indexModel, nodesOf, edgesOf, casesOf, makeVocab, levelOfNode, levelOfRelation, reachesLevel, asList } from './model-access.js';

/** Display order of the tags (and the tie-break between equally good hits). */
export const SEARCH_TABS = ['1D', '2D', '3D', '4D', '5D', 'QA'];
export const SEARCH_KINDS = ['node', 'edge', 'diagram', 'case', 'intervention', 'requirement', 'tbd'];
export const SEARCH_LIMIT = 40;

// width / case / kana-insensitive: a full-width letter equals its ASCII form
const fold = (s) => String(s ?? '').normalize('NFKC').toLowerCase();

function entry(kind, tab, id, title, sub, fields, target) {
  return { kind, tab, id, title, sub, target, f: fields.map(fold), key: `${tab}:${kind}:${id}` };
}

/**
 * One entry per link: a node has a 2D and a 3D page, a case a 4D page, an intervention a 5D page, a requirement a 1D page, a TBD the QA page.
 * opts: { vocab } (levels and kind table, neutral when omitted). A model without cases, interventions or requirements simply has no such entries.
 */
export function makeSearchIndex(model, diagrams = [], opts = {}) {
  const vocab = opts.vocab || makeVocab(null);
  const ix = opts.ix || indexModel(model);
  const levels = vocab.levels;
  const lowestLevel = (pred) => levels.find(pred) ?? levels[levels.length - 1];
  const out = []; const nodes = nodesOf(model); const cases = casesOf(model);
  const name = (id) => ix.nodes.get(id)?.name ?? id;
  for (const n of nodes) {
    const f = [n.id, n.name, n.sub, n.kind, n.group];
    const level = levelOfNode(n, ix, vocab);
    for (const tab of ['2D', '3D']) out.push(entry('node', tab, n.id, n.name, `${n.kind} \u00b7 ${n.id}${n.sub ? ` \u00b7 ${n.sub}` : ''}`, f, { tab, node: n.id, level }));
  }
  for (const e of edgesOf(model)) {
    const a = name(e.sourceId); const b = name(e.targetId);
    const need = [levelOfRelation(vocab, e.relation), levelOfNode(ix.nodes.get(e.sourceId), ix, vocab), levelOfNode(ix.nodes.get(e.targetId), ix, vocab)];
    const level = lowestLevel((l) => need.every((x) => reachesLevel(vocab, l, x)));
    out.push(entry('edge', '2D', e.id, e.label || e.id, `${a} \u2192 ${b} \u00b7 ${e.relation ?? e.type} \u00b7 ${e.id}`, [e.id, e.label, e.relation, a, b], { tab: '2D', edge: e.id, level }));
  }
  for (const d of asList(diagrams)) out.push(entry('diagram', '2D', d.id, d.title, `${d.type} \u00b7 ${d.id}`, [d.id, d.title, d.type], { tab: '2D', diagram: d.id }));
  for (const c of cases) out.push(entry('case', '4D', c.caseId, c.title, `${c.category} \u00b7 ${c.caseId}`, [c.caseId, c.title, c.category, ...asList(c.requirementIds)], { tab: '4D', caseId: c.caseId }));
  for (const iv of asList(model?.interventions)) {
    const caseId = asList(iv.appliesTo)[0] ?? cases[0]?.caseId ?? null;
    out.push(entry('intervention', '5D', iv.interventionId, iv.description ?? iv.interventionId, `${iv.type} \u00b7 ${iv.interventionId} \u00b7 ${caseId ?? ''}`, [iv.interventionId, iv.description, iv.type, iv.targetNodeId, iv.note], { tab: '5D', caseId, interventionKey: iv.interventionId }));
  }
  for (const r of asList(model?.requirements)) out.push(entry('requirement', '1D', r.id, r.title, `${r.id} \u00b7 ${r.source}`, [r.id, r.title, r.source, ...asList(r.tstIds), ...asList(r.designIds)], { tab: '1D', level: levels[0], node: asList(r.nodeIds)[0] ?? null }));
  for (const t of asList(model?.tbds)) out.push(entry('tbd', 'QA', t.id, t.title, `${t.id} \u00b7 ${t.decision}`, [t.id, t.title, t.decision], { tab: 'QA' }));
  return out;
}

// 0 = no match. Every whitespace-separated word must hit one field (AND); the weakest word decides, the id / title fields count most.
function rankWord(e, w) {
  let best = 0;
  e.f.forEach((v, i) => {
    if (!v.includes(w)) return;
    const s = (i === 0 ? 5 : i === 1 ? 4 : 1) + (v === w ? 6 : v.startsWith(w) ? 3 : 0);
    if (s > best) best = s;
  });
  return best;
}

/** Hits for `query`, best first (then 1D -> QA, then model order); an empty query finds nothing. */
export function search(index, query, limit = SEARCH_LIMIT) {
  const words = fold(query).split(/\s+/).filter(Boolean); if (!words.length) return [];
  const hits = [];
  index.forEach((e, order) => {
    let total = 0;
    for (const w of words) { const s = rankWord(e, w); if (!s) return; total += s; }
    hits.push({ e, total: e.kind === 'edge' ? total - 2 : total, order }); // lines are numerous and repeat their labels: parts and documents list first
  });
  hits.sort((a, b) => b.total - a.total || SEARCH_TABS.indexOf(a.e.tab) - SEARCH_TABS.indexOf(b.e.tab) || a.order - b.order);
  return hits.slice(0, limit).map((h) => h.e);
}
