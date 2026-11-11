// QA tab data (pure). Gathers what the page can know without running anything: the counts of the model against the counts the build wrote
// into meta.counts, evidence grades, vocabulary usage, the optional build summary meta.qa (rule counts, release gate, oracle classes, the oracle
// sheet reference), build and runtime warnings (vocabulary, label-fit, room-contract), open decisions and the scene check. The tab only
// renders this record; every display text comes from i18n keys, nothing here is shown as is.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { asList, isRecord, own } from '../model-access.js';
import { viewCountBy } from './model-view.js';

export const QA_COUNT_KEYS = ['nodes', 'edges', 'cases', 'interventions', 'requirements', 'tbds', 'sealedCases', 'sealedPairs'];
export const QA_ORACLE_CLASSES = ['sealed', 'revised', 'confirmed', 'pending'];
export const QA_RULE_LEVELS = ['block', 'error', 'warn', 'info'];

const qaInt = (v) => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);

function qaActualCounts(ctx) {
  return { nodes: ctx.nodes.length, edges: ctx.edges.length, cases: ctx.cases.length, interventions: ctx.interventions.length, requirements: ctx.requirements.length, tbds: ctx.tbds.length, sealedCases: ctx.sealedCaseIds.length, sealedPairs: ctx.pairs.length };
}

function qaPickInts(src, keys) {
  if (!isRecord(src)) return null;
  const out = {}; let any = false;
  for (const k of keys) { const v = qaInt(own(src, k)); out[k] = v; if (v !== null) any = true; }
  return any ? out : null;
}

/** The QA record of a context. `docCheck` (optional) is the list of document twin results from doc-state.docCheckAll. */
export function qaReport(ctx, docCheck) {
  const actual = qaActualCounts(ctx);
  const expected = isRecord(ctx.meta.counts) ? ctx.meta.counts : {};
  const counts = QA_COUNT_KEYS.map((key) => {
    const want = qaInt(own(expected, key));
    return { key, actual: actual[key], expected: want, ok: want === null ? null : want === actual[key] };
  });
  const qa = isRecord(ctx.meta.qa) ? ctx.meta.qa : {};
  const rules = qaPickInts(qa.rules, QA_RULE_LEVELS);
  const oracle = qaPickInts(qa.oracle, QA_ORACLE_CLASSES);
  const sheet = isRecord(qa.oracle) && typeof qa.oracle.sheet === 'string' ? qa.oracle.sheet : null;
  const gate = isRecord(qa.gate) ? { release: typeof qa.gate.release === 'boolean' ? qa.gate.release : null, blockers: qaInt(own(qa.gate, 'blockers')) } : null;
  const seen = new Set(); const notes = [];
  const take = (list, source) => {
    for (const w of asList(list)) {
      if (!isRecord(w) || typeof w.code !== 'string') continue;
      const key = w.code + '|' + String(w.subject);
      if (seen.has(key)) continue;
      seen.add(key); notes.push({ code: w.code, subject: String(w.subject === undefined ? '' : w.subject), detail: typeof w.detail === 'string' ? w.detail : '', source });
    }
  };
  take(ctx.meta.warnings, 'build'); take(qa.notes, 'build'); take(ctx.warnings.items, 'view');
  const scene = isRecord(ctx.bundle.calibrationHooks) && isRecord(ctx.bundle.calibrationHooks.scene) ? ctx.bundle.calibrationHooks.scene : null;
  const sceneRow = scene ? { nodeCount: qaInt(scene.nodeCount), unassigned: qaInt(scene.unassignedCount), ok: qaInt(scene.nodeCount) === ctx.nodes.length && qaInt(scene.unassignedCount) === 0 } : null;
  const grades = asList(ctx.vocab.profile.verification).filter(isRecord).slice().sort((a, b) => (Number(b.rank) || 0) - (Number(a.rank) || 0)).map((g) => g.id);
  const byGrade = new Map(viewCountBy([...ctx.nodes, ...ctx.edges], 'verification').map((g) => [g.id, g.count]));
  const evidence = [...grades.filter((g) => byGrade.has(g)), ...[...byGrade.keys()].filter((g) => !grades.includes(g))].map((id) => ({ id, count: byGrade.get(id) }));
  const docs = isRecord(ctx.bundle.fragments) ? ctx.bundle.fragments : {};
  return {
    counts, countsOk: counts.every((c) => c.ok !== false), mismatches: counts.filter((c) => c.ok === false).length,
    evidence, kinds: viewCountBy(ctx.nodes, 'kind'), relations: viewCountBy(ctx.edges, 'relation'),
    rules, gate, oracle, sheet, notes, scene: sceneRow,
    // nothing sealed (or nothing authored to seal): the oracle part and the 4D / 5D tabs show the same empty state
    oracleEmpty: !ctx.hasSealed,
    tbds: ctx.tbds.map((t) => ({ id: String(t.id), title: typeof t.title === 'string' ? t.title : '' })),
    docs: { fragments: asList(docs.items).length, elements: asList(docs.elements).length },
    docCheck: Array.isArray(docCheck) ? { total: docCheck.length, equal: docCheck.filter((d) => d.ok).length, rows: docCheck } : null,
  };
}
