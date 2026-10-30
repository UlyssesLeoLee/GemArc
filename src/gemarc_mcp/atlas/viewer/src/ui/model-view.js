// The view context: everything the tabs derive once from the embedded bundle (vocabulary, model index, lists, flags for the empty states).
// Pure and total: a missing or malformed part of the bundle gives empty lists, never a throw. The bundle itself is only read.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { makeVocab, makeWarnings, indexModel, asList, isRecord, own, nodesOf, edgesOf, casesOf } from '../model-access.js';

const viewRecords = (v, idField) => asList(v).filter((x) => isRecord(x) && (idField === null || typeof x[idField] === 'string'));

/** A shallow copy of the model whose collections hold only records with a usable id, so the index and the layout never meet a null. */
export function viewCleanModel(model) {
  const m = isRecord(model) ? model : {};
  const space = isRecord(m.space) ? m.space : {};
  return {
    ...m, nodes: viewRecords(m.nodes, 'id'), edges: viewRecords(m.edges, 'id'), cases: viewRecords(m.cases, 'caseId'), requirements: viewRecords(m.requirements, 'id'),
    interventions: viewRecords(m.interventions, null), tbds: viewRecords(m.tbds, null), evidences: viewRecords(m.evidences, 'evidenceId'), machines: viewRecords(m.machines, 'id'),
    ports: viewRecords(m.ports, 'id'), space: { ...space, buildings: viewRecords(space.buildings, 'id'), floors: viewRecords(space.floors, 'id'), rooms: viewRecords(space.rooms, 'id') },
  };
}

/** Build the context of a bundle. */
export function viewContext(bundle) {
  const b = isRecord(bundle) ? bundle : {};
  const meta = isRecord(b.meta) ? b.meta : {};
  const model = viewCleanModel(b.model);
  const warnings = makeWarnings();
  const vocab = makeVocab(isRecord(b.profile) ? b.profile : null, warnings);
  const ix = indexModel(model);
  const runs = isRecord(b.runs) ? b.runs : {};
  const baselines = isRecord(runs.baselines) ? runs.baselines : {};
  const languages = asList(meta.languages).filter((l) => typeof l === 'string');
  const requirements = model.requirements;
  const interventions = model.interventions;
  const cases = casesOf(model);
  const diagrams = asList(b.diagrams).filter(isRecord);
  return {
    bundle: b, meta, model, vocab, ix, warnings, runs, diagrams,
    languages, primaryLanguage: typeof meta.primaryLanguage === 'string' ? meta.primaryLanguage : (languages[0] || 'en-US'),
    levels: vocab.levels,
    nodes: nodesOf(model), edges: edgesOf(model), cases, requirements, interventions,
    tbds: model.tbds,
    sealedCaseIds: Object.keys(baselines),
    pairs: asList(runs.pairs).filter(isRecord),
    hasCases: cases.length > 0,
    hasInterventions: interventions.length > 0,
    hasSealed: Object.keys(baselines).length > 0,
  };
}

/** The kind of an element id: 'node' | 'edge' | 'case' | 'requirement' | null (the id spaces never overlap in a valid model). */
export function viewElementKind(ctx, id) {
  if (ctx.ix.nodes.has(id)) return 'node';
  if (ctx.ix.edges.has(id)) return 'edge';
  if (ctx.ix.cases.has(id)) return 'case';
  if (ctx.requirements.some((r) => r.id === id)) return 'requirement';
  return null;
}

/** A readable title for an element id (the model name or title; the id itself when there is none). */
export function viewElementTitle(ctx, id) {
  const n = ctx.ix.nodes.get(id);
  if (n) return typeof n.name === 'string' && n.name ? n.name : id;
  const e = ctx.ix.edges.get(id);
  if (e) {
    if (typeof e.label === 'string' && e.label) return e.label;
    const a = ctx.ix.nodes.get(e.sourceId); const z = ctx.ix.nodes.get(e.targetId);
    return (a ? a.name : e.sourceId) + ' > ' + (z ? z.name : e.targetId);
  }
  const c = ctx.ix.cases.get(id);
  if (c) return typeof c.title === 'string' && c.title ? c.title : id;
  const r = ctx.requirements.find((x) => x.id === id);
  if (r) return typeof r.title === 'string' && r.title ? r.title : id;
  return String(id);
}

/** The label of an entry of a profile collection ('kinds', 'relations', 'domains', 'trust', 'levels', 'verification', ...) in `lang`. */
export function viewVocabLabel(ctx, collection, id, lang) {
  const entry = asList(ctx.vocab.profile[collection]).find((e) => isRecord(e) && e.id === id);
  const label = entry && isRecord(entry.label) ? entry.label : null;
  const pick = (l) => (label && typeof own(label, l) === 'string' && own(label, l) ? own(label, l) : null);
  return pick(lang) || pick('en-US') || String(id);
}

/** Counts by a string field of a list: [{id, count}] in first-seen order. */
export function viewCountBy(list, field) {
  const m = new Map();
  for (const x of list) {
    const v = isRecord(x) && typeof x[field] === 'string' && x[field] ? x[field] : '';
    m.set(v, (m.get(v) || 0) + 1);
  }
  return [...m.entries()].map(([id, count]) => ({ id, count }));
}

/** Everything a selection can point at, as {type, id} -> exists? Used to drop stale selections from a route or after a model swap. */
export function viewHasSelection(ctx, sel) {
  if (!isRecord(sel) || typeof sel.id !== 'string') return false;
  if (sel.type === 'node') return ctx.ix.nodes.has(sel.id);
  if (sel.type === 'edge') return ctx.ix.edges.has(sel.id);
  if (sel.type === 'case') return ctx.ix.cases.has(sel.id);
  if (sel.type === 'requirement') return ctx.requirements.some((r) => r.id === sel.id);
  return false;
}
