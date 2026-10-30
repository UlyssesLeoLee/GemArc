// Inspector data (pure): the facts shown for a selected node, edge, case or requirement, as plain records. The renderer chooses labels
// (i18n keys and vocabulary labels) and draws; values are the author's text and are only ever shown as text nodes.
//
// Result: { type, id, title, fields: [{key, value, vocab?}], evidences: [...], links: [...], cases: [...], requirements: [...] } or null.
// A field with `vocab` names the profile collection whose label should be shown instead of the raw value ('kinds', 'relations', ...);
// a field with a boolean `flag` is shown as yes or no from the interface table.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { asList, isRecord, casePlayers, incident, homeOf } from '../model-access.js';
import { viewElementTitle } from './model-view.js';

const INSP_MAX_ROWS = 60;
const inspStr = (v) => (typeof v === 'string' ? v : v === undefined || v === null ? '' : typeof v === 'object' ? '' : String(v));

function inspEvidenceRows(ctx, ids) {
  return asList(ids).slice(0, INSP_MAX_ROWS).map((id) => {
    const e = ctx.ix.evidences.get(id);
    if (!e) return { id: String(id), file: '', anchor: '', range: '', topic: '', grade: '', known: false };
    const r = Array.isArray(e.lineRange) && e.lineRange.length === 2 ? e.lineRange[0] + '-' + e.lineRange[1] : '';
    return { id: String(id), file: inspStr(e.filePath), anchor: inspStr(e.anchor), range: r, topic: inspStr(e.topic), grade: inspStr(e.verification), known: true };
  });
}

const inspNodeLink = (ctx, id) => ({ type: 'node', id, title: viewElementTitle(ctx, id) });
const inspPropRows = (props) => (isRecord(props) ? Object.keys(props).filter((k) => ['string', 'number', 'boolean'].includes(typeof props[k])).slice(0, INSP_MAX_ROWS).map((k) => ({ key: k, value: String(props[k]) })) : []);

function inspNode(ctx, n) {
  const home = homeOf(ctx.ix, n.id);
  const named = (map, id) => { const x = map.get(id); return x ? inspStr(x.name) || inspStr(x.id) : ''; };
  const place = home ? [named(ctx.ix.buildings, home.buildingId), named(ctx.ix.floors, home.floorId), named(ctx.ix.rooms, home.roomId)].filter(Boolean).join(' / ') : '';
  const links = incident(ctx.ix, n.id).slice(0, INSP_MAX_ROWS).map((e) => {
    const out = e.sourceId === n.id; const other = out ? e.targetId : e.sourceId;
    return { type: 'edge', id: e.id, title: viewElementTitle(ctx, e.id), dir: out ? 'out' : 'in', relation: inspStr(e.relation), other: inspNodeLink(ctx, other) };
  });
  const cases = ctx.cases.filter((c) => asList(c.events).some((ev) => ev && (ev.from === n.id || ev.to === n.id))).slice(0, INSP_MAX_ROWS).map((c) => ({ type: 'case', id: c.caseId, title: viewElementTitle(ctx, c.caseId) }));
  const reqs = ctx.requirements.filter((r) => asList(r.nodeIds).includes(n.id)).slice(0, INSP_MAX_ROWS).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
  return {
    type: 'node', id: n.id, title: inspStr(n.name) || n.id,
    fields: [{ key: 'id', value: n.id }, { key: 'kind', value: inspStr(n.kind), vocab: 'kinds' }, { key: 'summary', value: inspStr(n.sub) }, { key: 'grade', value: inspStr(n.verification), vocab: 'verification' }, { key: 'placement', value: place }, { key: 'trust', value: inspStr(n.props && n.props.trust), vocab: 'trust' }],
    props: inspPropRows(n.props), evidences: inspEvidenceRows(ctx, n.evidenceIds), links, cases, requirements: reqs,
  };
}

function inspEdge(ctx, e) {
  const reqs = ctx.requirements.filter((r) => asList(r.edgeIds).includes(e.id)).slice(0, INSP_MAX_ROWS).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
  return {
    type: 'edge', id: e.id, title: viewElementTitle(ctx, e.id),
    fields: [{ key: 'id', value: e.id }, { key: 'relation', value: inspStr(e.relation), vocab: 'relations' }, { key: 'label', value: inspStr(e.label) }, { key: 'grade', value: inspStr(e.verification), vocab: 'verification' }, { key: 'guard', value: inspStr(e.guardId) }],
    props: [], evidences: inspEvidenceRows(ctx, e.evidenceIds), cases: [], requirements: reqs,
    links: [{ type: 'node', id: e.sourceId, title: viewElementTitle(ctx, e.sourceId), dir: 'from' }, { type: 'node', id: e.targetId, title: viewElementTitle(ctx, e.targetId), dir: 'to' }],
  };
}

function inspCase(ctx, c) {
  const players = [...casePlayers(ctx.ix, c.caseId)];
  const reqs = ctx.requirements.filter((r) => asList(c.requirementIds).includes(r.id) || asList(r.caseIds).includes(c.caseId)).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
  return {
    type: 'case', id: c.caseId, title: inspStr(c.title) || c.caseId,
    fields: [{ key: 'id', value: c.caseId }, { key: 'category', value: inspStr(c.category) }, { key: 'events', value: String(asList(c.events).length) }, { key: 'sealed', value: '', flag: ctx.sealedCaseIds.includes(c.caseId) }],
    props: [], evidences: inspEvidenceRows(ctx, c.evidenceIds), links: players.slice(0, INSP_MAX_ROWS).map((id) => ({ ...inspNodeLink(ctx, id), dir: 'player' })), cases: [], requirements: reqs,
  };
}

function inspRequirement(ctx, r) {
  return {
    type: 'requirement', id: r.id, title: inspStr(r.title) || r.id,
    fields: [{ key: 'id', value: r.id }, { key: 'source', value: inspStr(r.source) }, { key: 'tests', value: asList(r.tstIds).map(inspStr).join(', ') }],
    props: [], evidences: inspEvidenceRows(ctx, r.evidenceIds),
    links: [...asList(r.nodeIds).slice(0, INSP_MAX_ROWS).map((id) => ({ ...inspNodeLink(ctx, id), dir: 'node' })), ...asList(r.edgeIds).slice(0, INSP_MAX_ROWS).map((id) => ({ type: 'edge', id, title: viewElementTitle(ctx, id), dir: 'edge' }))],
    cases: ctx.cases.filter((c) => asList(c.requirementIds).includes(r.id)).map((c) => ({ type: 'case', id: c.caseId, title: viewElementTitle(ctx, c.caseId) })), requirements: [],
  };
}

/** The inspector record for a selection ({type, id}); null when nothing (or something unknown) is selected. */
export function inspectSelection(ctx, sel) {
  if (!isRecord(sel) || typeof sel.id !== 'string') return null;
  if (sel.type === 'node') { const n = ctx.ix.nodes.get(sel.id); return n ? inspNode(ctx, n) : null; }
  if (sel.type === 'edge') { const e = ctx.ix.edges.get(sel.id); return e ? inspEdge(ctx, e) : null; }
  if (sel.type === 'case') { const c = ctx.ix.cases.get(sel.id); return c ? inspCase(ctx, c) : null; }
  if (sel.type === 'requirement') { const r = ctx.requirements.find((x) => x.id === sel.id); return r ? inspRequirement(ctx, r) : null; }
  return null;
}
