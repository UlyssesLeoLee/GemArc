// QA tab: counts against the build, rule and gate summary, oracle classes and the oracle sheet reference, warnings (vocabulary, label fit,
// room contract), evidence grades, vocabulary usage, the document twin check and the open decisions. Every row carries data-qa-row so the
// gate can assert that the page shows rows. All text comes from interface keys; ids and counts are data.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl } from './dom.js';
import { qaReport } from './qa.js';
import { docCheckAll } from './doc-state.js';

const QA_COUNT_KEYS_I18N = {
  nodes: 'qa.count.nodes', edges: 'qa.count.edges', cases: 'qa.count.cases', interventions: 'qa.count.interventions', requirements: 'qa.count.requirements',
  tbds: 'qa.count.tbds', sealedCases: 'qa.count.sealedCases', sealedPairs: 'qa.count.sealedPairs',
};
const QA_RULE_KEYS_I18N = { block: 'qa.rules.block', error: 'qa.rules.error', warn: 'qa.rules.warn', info: 'qa.rules.info' };
const QA_ORACLE_KEYS_I18N = { sealed: 'qa.oracle.sealed', revised: 'qa.oracle.revised', confirmed: 'qa.oracle.confirmed', pending: 'qa.oracle.pending' };
const QA_WARN_KEYS_I18N = {
  GEMARC_ATLAS_VIEW_001: 'warn.view001', GEMARC_ATLAS_VIEW_002: 'warn.view002', GEMARC_ATLAS_VIEW_003: 'warn.view003', GEMARC_ATLAS_VIEW_004: 'warn.view004',
  GEMARC_ATLAS_VIEW_005: 'warn.view005', GEMARC_ATLAS_VIEW_006: 'warn.view006', GEMARC_ATLAS_VIEW_007: 'warn.view007', GEMARC_ATLAS_VIEW_008: 'warn.view008',
  GEMARC_ATLAS_VIEW_009: 'warn.view009', GEMARC_ATLAS_VIEW_010: 'warn.view010', 'LB-001': 'warn.lb001', 'RC-001': 'warn.rc001', 'RC-002': 'warn.rc002',
};

const qaSection = (title, ...kids) => domEl('section', { class: 'qa-sec' }, domEl('h3', null, title), kids);
const qaTable = (head, rows) => domEl('div', { class: 'tbl-wrap' }, domEl('table', null, domEl('thead', null, domEl('tr', null, head.map((h) => domEl('th', { scope: 'col' }, h)))), domEl('tbody', null, rows)));
const qaRow = (cells, extra) => domEl('tr', { 'data-qa-row': '1', class: extra || null }, cells.map((c) => domEl('td', null, c)));

/** The tab definition. */
export const QA_TAB = {
  id: 'QA', labelKey: 'tab.qa', questionKey: 'ask.qa', inspector: false,
  render(host, api) {
    const { t, ctx } = api;
    const r = qaReport(ctx, docCheckAll(ctx));
    host.appendChild(domEl('div', { class: 'qa' },
      qaSection(t('qa.counts.title'),
        qaTable([t('qa.col.item'), t('qa.col.model'), t('qa.col.build'), t('qa.col.result')], r.counts.map((c) => qaRow([t(QA_COUNT_KEYS_I18N[c.key]), String(c.actual), c.expected === null ? t('qa.na') : String(c.expected), c.ok === null ? t('qa.na') : c.ok ? t('qa.ok') : t('qa.mismatch')], c.ok === false ? 'bad' : null))),
        domEl('p', { class: 'muted note', 'data-qa-row': '1' }, t('qa.counts.note')),
        r.mismatches ? domEl('p', { class: 'bad', role: 'status' }, t('qa.counts.mismatch', { n: r.mismatches })) : null),
      qaSection(t('qa.rules.title'),
        r.rules ? qaTable([t('qa.col.item'), t('qa.col.count')], Object.keys(QA_RULE_KEYS_I18N).filter((k) => r.rules[k] !== null).map((k) => qaRow([t(QA_RULE_KEYS_I18N[k]), String(r.rules[k])]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.rules.none')),
        r.gate ? domEl('p', { 'data-qa-row': '1' }, r.gate.release === null ? t('qa.gate.unknown') : r.gate.release ? t('qa.gate.release.ok') : t('qa.gate.release.blocked', { n: r.gate.blockers === null ? 0 : r.gate.blockers })) : null),
      qaSection(t('qa.oracle.title'),
        r.oracleEmpty ? domEl('p', { class: 'empty', 'data-qa-row': '1' }, t('empty.expectation')) : null,
        r.oracle ? qaTable([t('qa.col.item'), t('qa.col.count')], Object.keys(QA_ORACLE_KEYS_I18N).filter((k) => r.oracle[k] !== null).map((k) => qaRow([t(QA_ORACLE_KEYS_I18N[k]), String(r.oracle[k])]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.oracle.none')),
        r.sheet ? domEl('p', { 'data-qa-row': '1' }, t('qa.oracle.sheet'), ' ', domEl('code', null, r.sheet)) : null),
      qaSection(t('qa.warn.title'),
        r.notes.length ? qaTable([t('qa.col.code'), t('qa.col.what'), t('qa.col.subject')], r.notes.map((n) => qaRow([domEl('code', null, n.code), QA_WARN_KEYS_I18N[n.code] ? t(QA_WARN_KEYS_I18N[n.code]) : t('warn.other'), n.subject]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.warn.none')),
        r.scene && !r.scene.ok ? domEl('p', { class: 'bad', role: 'status', 'data-qa-row': '1' }, t('qa.scene.unassigned', { n: r.scene.unassigned === null ? 0 : r.scene.unassigned })) : null),
      qaSection(t('qa.evidence.title'),
        r.evidence.length ? qaTable([t('qa.col.grade'), t('qa.col.count')], r.evidence.map((g) => qaRow([g.id ? api.label('verification', g.id) : t('qa.na'), String(g.count)]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.none'))),
      qaSection(t('qa.vocab.title'),
        qaTable([t('qa.col.kind'), t('qa.col.count')], r.kinds.map((k) => qaRow([api.label('kinds', k.id), String(k.count)]))),
        qaTable([t('qa.col.relation'), t('qa.col.count')], r.relations.map((k) => qaRow([api.label('relations', k.id), String(k.count)])))),
      qaSection(t('qa.docs.title'),
        domEl('p', { 'data-qa-row': '1' }, t('qa.docs.summary', { fragments: r.docs.fragments, elements: r.docs.elements })),
        r.docCheck ? domEl('p', { class: r.docCheck.equal === r.docCheck.total ? null : 'bad', role: 'status', 'data-qa-row': '1' }, t('qa.docs.twin', { equal: r.docCheck.equal, total: r.docCheck.total })) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.docs.twin.none'))),
      qaSection(t('qa.tbd.title'),
        r.tbds.length ? qaTable([t('qa.col.id'), t('qa.col.title')], r.tbds.map((d) => qaRow([domEl('code', null, d.id), d.title]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.none')))));
  },
};
