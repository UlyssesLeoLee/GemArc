// Side panel views: the navigator tree (sidebar) and the inspector (details of the selection, with slots for the tabs the 3D, 4D and 5D
// modules register). Text nodes only; ids and author text are data, every label is an interface key or a vocabulary label.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl } from './dom.js';
import { navigatorTree } from './navigator.js';
import { inspectSelection } from './inspector.js';

const PANEL_FIELD_KEYS = {
  id: 'insp.field.id', kind: 'insp.field.kind', summary: 'insp.field.summary', grade: 'insp.field.grade', placement: 'insp.field.placement', trust: 'insp.field.trust',
  relation: 'insp.field.relation', label: 'insp.field.label', guard: 'insp.field.guard', category: 'insp.field.category', events: 'insp.field.events', sealed: 'insp.field.sealed',
  source: 'insp.field.source', tests: 'insp.field.tests',
};
const PANEL_TYPE_KEYS = { node: 'insp.type.node', edge: 'insp.type.edge', case: 'insp.type.case', requirement: 'insp.type.requirement' };

/** Navigator: filter box plus the building > floor > room > node tree of the current level. */
export function renderNavigator(host, api) {
  const { t, ctx } = api; const st = api.state;
  const tree = navigatorTree(ctx, st.level, st.navFilter, st.sel);
  const filter = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'nav-filter', value: st.navFilter, placeholder: t('nav.filter.placeholder'), 'aria-label': t('nav.filter.label'), oninput: (ev) => api.setNavFilter(ev.target.value) });
  const nodeBtn = (n) => {
    const on = st.sel && st.sel.type === 'node' && st.sel.id === n.id;
    return domEl('button', { type: 'button', class: 'nav-node' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', 'data-node': n.id, onclick: () => api.select({ type: 'node', id: n.id }) }, domEl('span', { class: 'nav-name' }, n.name), ' ', domEl('span', { class: 'muted' }, api.label('kinds', n.kind)));
  };
  const groups = tree.groups.map((b) => domEl('details', { open: 'open', class: 'nav-building' },
    domEl('summary', null, b.unassigned ? t('nav.unassigned') : b.name || b.id),
    b.floors.map((f) => domEl('div', { class: 'nav-floor' }, b.unassigned ? null : domEl('div', { class: 'nav-label' }, f.name || f.id),
      f.rooms.map((r) => domEl('div', { class: 'nav-room' }, b.unassigned ? null : domEl('div', { class: 'nav-label muted' }, r.name || r.id), r.nodes.map(nodeBtn)))))));
  host.appendChild(domEl('div', { class: 'nav' }, filter, domEl('p', { class: 'muted' }, t('nav.count', { n: tree.count })), tree.count ? groups : domEl('p', { class: 'empty' }, t('empty.nodes'))));
}

function panelLinkButton(api, link) {
  return domEl('button', { type: 'button', class: 'chip', onclick: () => api.select({ type: link.type, id: link.id }) }, link.title);
}

function panelFieldValue(api, f) {
  if (typeof f.flag === 'boolean') return f.flag ? api.t('flag.yes') : api.t('flag.no');
  if (f.vocab && f.value) return api.label(f.vocab, f.value);
  return f.value;
}

/** The detail view of the selection. */
export function renderInspectorDetail(host, api) {
  const { t } = api;
  const d = inspectSelection(api.ctx, api.state.sel);
  if (!d) { host.appendChild(domEl('p', { class: 'empty', role: 'status' }, t('insp.none'))); return; }
  const dl = domEl('dl', { class: 'fields' }, d.fields.filter((f) => f.value !== '' || typeof f.flag === 'boolean').map((f) => [domEl('dt', null, t(PANEL_FIELD_KEYS[f.key])), domEl('dd', null, f.key === 'id' ? domEl('code', null, panelFieldValue(api, f)) : panelFieldValue(api, f))]));
  const links = d.links.map((l) => domEl('li', null, l.relation !== undefined ? domEl('span', { class: 'muted' }, (l.dir === 'out' ? '\u2192 ' : '\u2190 ') + (l.relation ? api.label('relations', l.relation) : '') + ' ') : null, panelLinkButton(api, l.other || l)));
  const evid = d.evidences.map((e) => domEl('li', null,
    domEl('code', null, e.id), ' ', e.known ? [domEl('span', null, e.file + (e.range ? ':' + e.range : '')), e.anchor ? [' ', domEl('code', null, e.anchor)] : null, e.topic ? [' \u2013 ', e.topic] : null, e.grade ? [' ', domEl('span', { class: 'muted' }, api.label('verification', e.grade))] : null] : domEl('span', { class: 'bad' }, t('insp.evidence.unknown'))));
  host.appendChild(domEl('div', { class: 'insp-detail' },
    domEl('h3', null, d.title), domEl('p', { class: 'muted' }, t(PANEL_TYPE_KEYS[d.type])), dl,
    d.props.length ? [domEl('h4', null, t('insp.props')), domEl('dl', { class: 'fields' }, d.props.map((p) => [domEl('dt', null, p.key), domEl('dd', null, p.value)]))] : null,
    d.evidences.length ? [domEl('h4', null, t('insp.evidence')), domEl('ul', null, evid)] : null,
    d.links.length ? [domEl('h4', null, t('insp.links')), domEl('ul', null, links)] : null,
    d.cases.length ? [domEl('h4', null, t('insp.cases')), domEl('div', { class: 'chips' }, d.cases.map((c) => panelLinkButton(api, c)))] : null,
    d.requirements.length ? [domEl('h4', null, t('insp.requirements')), domEl('div', { class: 'chips' }, d.requirements.map((c) => panelLinkButton(api, c)))] : null));
}

/** Inspector: tab strip (detail plus the registered slots of the current tab) and the active view. */
export function renderInspector(host, api, slots) {
  const { t } = api; const st = api.state;
  const tabs = [{ id: 'detail', labelKey: 'insp.tab.detail' }, ...slots];
  const active = tabs.some((x) => x.id === st.inspectorTab) ? st.inspectorTab : 'detail';
  host.appendChild(domEl('div', { class: 'choices', role: 'group', 'aria-label': t('insp.title') }, tabs.map((x) => domEl('button', { type: 'button', 'aria-pressed': active === x.id ? 'true' : 'false', class: 'seg' + (active === x.id ? ' on' : ''), onclick: () => api.setInspectorTab(x.id) }, t(x.labelKey)))));
  const body = domEl('div', { class: 'insp-body' });
  if (active === 'detail') renderInspectorDetail(body, api);
  else { const def = slots.find((x) => x.id === active); if (def) def.render(body, api); }
  host.appendChild(body);
}
