// 1D tab: the document reader. The text is assembled in the page from the embedded fragments and closures (assemble.js, the twin of the
// Python builder) for the chosen selection and level; the reader shows it rendered (tables, links to diagrams, markers) or raw, with a chapter
// outline and a copy button. The sidebar holds the selection UI: one requirement, one element, or any combination (free selection).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl, domCopy, domScrollToId, domViewport } from './dom.js';
import { layoutKindFor } from './prefs.js';
import { docKnownList, docText, docOutline, docFilterRows, docSetRequirement, docSetElement, docSetMode, docSetFilter, docToggle, docSetAll, docClear, docKnownIds } from './doc-state.js';
import { mdParse, mdCountMarkers } from './markdown.js';
import { renderBlocks } from './render-md.js';

const DOC_ROW_LIMIT = 150; // rows rendered per picker group; the filter narrows the rest
const DOC_MODE_KEYS = { requirement: 'doc.mode.requirement', element: 'doc.mode.element', free: 'doc.mode.free' };
const DOC_GROUP_KEYS = { requirements: 'doc.group.requirements', cases: 'doc.group.cases', nodes: 'doc.group.nodes', edges: 'doc.group.edges' };
const DOC_GROUP_TYPES = { requirements: 'requirement', cases: 'case', nodes: 'node', edges: 'edge' };

function docPickerRows(api, rows, group, st, onPick, free) {
  const shown = rows.slice(0, DOC_ROW_LIMIT);
  const list = shown.map((r) => {
    const on = free ? st.ids.includes(r.id) : (st.mode === 'requirement' ? st.reqId === r.id : st.elementId === r.id);
    const label = domEl('span', { class: 'pick-text' }, domEl('code', null, r.id), ' ', r.title === r.id ? '' : r.title);
    if (free) return domEl('label', { class: 'pick' + (on ? ' on' : '') }, domEl('input', { type: 'checkbox', checked: on ? 'checked' : null, onchange: () => onPick(r.id) }), label);
    return domEl('button', { type: 'button', class: 'pick' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', onclick: () => onPick(r.id) }, label);
  });
  return domEl('div', { class: 'pick-group' }, domEl('h4', null, api.t(DOC_GROUP_KEYS[group]), ' ', domEl('span', { class: 'muted' }, String(rows.length))), list,
    rows.length > shown.length ? domEl('p', { class: 'muted' }, api.t('doc.list.more', { shown: shown.length, total: rows.length })) : null);
}

function docControls(host, api) {
  const { t, ctx } = api; const st = api.state.doc;
  const known = docKnownList(ctx, docKnownIds(ctx));
  const set = (next) => api.setDoc(next);
  const modes = Object.keys(DOC_MODE_KEYS).map((m) => domEl('button', { type: 'button', 'aria-pressed': st.mode === m ? 'true' : 'false', class: 'seg' + (st.mode === m ? ' on' : ''), onclick: () => set(docSetMode(st, m)) }, t(DOC_MODE_KEYS[m])));
  const filter = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'doc-filter', value: st.filter, placeholder: t('doc.filter.placeholder'), 'aria-label': t('doc.filter.label'), oninput: (ev) => set(docSetFilter(st, ev.target.value)) });
  const groups = st.mode === 'requirement' ? ['requirements'] : ['requirements', 'cases', 'nodes', 'edges'];
  const free = st.mode === 'free';
  const pick = (group) => (id) => {
    if (free) set(docToggle(st, id));
    else if (group === 'requirements') set(docSetRequirement(st, id));
    else set(docSetElement(st, id));
  };
  const body = groups.map((g) => {
    if (st.mode === 'element' && g === 'requirements') return null;
    return docPickerRows(api, docFilterRows(known[g], st.filter), g, st, pick(g), free);
  });
  const allRows = ['requirements', 'cases', 'nodes', 'edges'].flatMap((g) => docFilterRows(known[g], st.filter)).map((r) => r.id);
  host.appendChild(domEl('div', { class: 'doc-controls' },
    domEl('h3', null, t('doc.controls.title')),
    domEl('div', { class: 'choices', role: 'group', 'aria-label': t('doc.mode.label') }, modes),
    filter,
    free ? domEl('div', { class: 'row-actions' },
      domEl('button', { type: 'button', onclick: () => set(docSetAll(st, allRows)) }, t('doc.free.all')),
      domEl('button', { type: 'button', onclick: () => set(docClear(st)) }, t('doc.free.clear')),
      domEl('span', { class: 'muted' }, t('doc.free.count', { n: st.ids.length }))) : null,
    body));
}

function docReader(host, api) {
  const { t, ctx } = api; const st = api.state.doc;
  const res = docText(ctx, { ...st, level: api.state.level });
  const head = domEl('div', { class: 'doc-head' });
  if (!res.ok) {
    host.appendChild(domEl('div', { class: 'doc' }, head, domEl('p', { class: 'empty', role: 'status' }, t('doc.unavailable'))));
    return;
  }
  const outline = docOutline(res.text);
  const markers = mdCountMarkers(res.text);
  head.appendChild(domEl('div', { class: 'doc-stats', 'data-gate': 'fit' }, t('doc.stats', { level: api.label('levels', api.state.level), bytes: res.bytes, lines: res.lines, targets: res.ids.length })));
  head.appendChild(domEl('div', { class: 'row-actions' },
    domEl('button', { type: 'button', onclick: () => domCopy(res.text) }, t('doc.copy')),
    domEl('button', { type: 'button', 'aria-pressed': api.state.docRaw ? 'true' : 'false', class: api.state.docRaw ? 'on' : null, onclick: () => api.setRaw(!api.state.docRaw) }, t('doc.raw'))));
  if (markers) head.appendChild(domEl('p', { class: 'bad', role: 'status' }, t('doc.markers', { n: markers })));
  if (ctx.primaryLanguage !== api.state.lang) head.appendChild(domEl('p', { class: 'muted' }, t('doc.language', { lang: ctx.primaryLanguage })));
  if (!res.ids.length) head.appendChild(domEl('p', { class: 'muted' }, t('doc.nothing')));
  // at a narrow width the selection controls live in the overlay panel: say where (nothing in this pane points to it otherwise)
  if (layoutKindFor(domViewport().w) === 'overlay') head.appendChild(domEl('p', { class: 'muted note' }, t('doc.panel.hint', { panel: t('hdr.panel.nav') })));
  const nav = domEl('nav', { class: 'outline', 'aria-label': t('doc.outline') }, outline.map((c) => domEl('button', { type: 'button', class: 'chip', onclick: () => domScrollToId('doc-ch-' + c.n) }, c.n + '. ' + c.title)));
  let body;
  if (api.state.docRaw) body = domEl('pre', { class: 'raw', tabindex: '0' }, res.text);
  else {
    // the rendered reader of the last text and language (the titles inside it are interface text): a re-render with both equal reuses it
    const key = api.state.lang + '|' + res.text;
    const cached = api.cache.get('doc');
    if (!cached || cached.key !== key) api.cache.set('doc', { key, node: renderBlocks(mdParse(res.text), { t, onDiagram: (id) => api.openUnit(id) }) });
    body = api.cache.get('doc').node;
  }
  host.appendChild(domEl('div', { class: 'doc' }, head, outline.length && !api.state.docRaw ? nav : null, body));
}

/** The tab definition: the sidebar controls and the reader. */
export const DOC_TAB = { id: '1D', labelKey: 'tab.1d', questionKey: 'ask.1d', inspector: false, controls: docControls, render: docReader };
