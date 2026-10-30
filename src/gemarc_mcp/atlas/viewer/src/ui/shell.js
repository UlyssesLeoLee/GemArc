// The shell: header (brand, tabs, level switch, language, theme, panel buttons, search, status), the navigator and inspector side areas (docked
// columns, or overlay panes below the narrow width), the stage of the active tab, the search dialog, keyboard handling, the preference
// store, the error boundary and the documented window API (AtlasApp, see docs/atlas/spec/bundle-contract.md section 9). It builds elements only
// through dom.js. A full render is synchronous (renderOnce): the gate calls it and reads the heartbeat counter.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl, domById, domClear, domOnKey, domOnHash, domOnResize, domHash, domSetHash, domStoreGet, domStoreSet, domViewport, domBody, domSet, domGet, domRootAttrs, domSetTitle, domSlotArray, domExpose, domCatchErrors, domFocusState, domRestoreFocus, domQuery, domLater, domNavigatorLanguage, domScrollTop, domQueryAll, domBoxes, domBrokenTokens, domAtScale, domClassOn } from './dom.js';
import { TWOD_LOD_SCALE } from './twod-scene.js';
import { gateOverlaps, gateTextAllow, GATE_TOKEN_MAX } from './gate-checks.js';
import { makeI18n, i18nPickLang } from './i18n.js';
import { makeBoundary } from './boundary.js';
import { prefKeyOf, prefsParse, prefsSerialize, layoutKindFor, layoutColumns, headerWraps, THEMES } from './prefs.js';
import { navMakeRegistry, navParseRoute, navBuildRoute } from './nav.js';
import { viewContext, viewElementTitle, viewVocabLabel } from './model-view.js';
import { shellInitial, shellSetTab, shellSetLevel, shellSelect, shellSetLang, shellSetTheme, shellTogglePanel, shellEscape, shellRouteParams, shellKeyAction, shellApplyHit, shellMergeHits, shellHitFrom } from './shell-state.js';
import { docSetRequirement, docCheckAll } from './doc-state.js';
import { renderNavigator, renderInspector } from './panel-views.js';
import { makeSearchIndex, search } from '../search.js';

export const SHELL_API_VERSION = 1;
const SHELL_SCHEMA = 'gemarc-atlas-bundle/1';
const SHELL_TAB_KEYS = { '1D': 'tab.1d', '2D': 'tab.2d', '3D': 'tab.3d', '4D': 'tab.4d', '5D': 'tab.5d', QA: 'tab.qa' };
const SHELL_SEARCH_KIND_KEYS = { node: 'kind.node', edge: 'kind.edge', diagram: 'kind.diagram', case: 'kind.case', intervention: 'kind.intervention', requirement: 'kind.requirement', tbd: 'kind.tbd' };
const ARROW_KEY = /^Arrow(Left|Right|Up|Down)$/;
const SHELL_THEME_KEYS = { auto: 'hdr.theme.auto', light: 'hdr.theme.light', dark: 'hdr.theme.dark' };

/**
 * Create the application for a bundle and render it once. `opts.tabs` is the list of tab definitions to register in order (a later definition
 * with the same id replaces an earlier one). Returns the AtlasApp object (the caller exposes it as window.AtlasApp).
 */
export function createApp(bundle, opts) {
  const options = opts || {};
  const sink = domSlotArray('__ATLAS_ERRORS__');
  const boundary = makeBoundary(sink);
  domCatchErrors((rec) => sink.push(rec));
  if (!bundle || bundle.schema !== SHELL_SCHEMA) boundary.report('bundle', 'unexpected bundle schema'); // diag: author-facing
  const ctx = viewContext(bundle);
  domExpose('__ATLAS_WARNINGS__', ctx.warnings.items);

  const tables = bundle && bundle.i18n && bundle.i18n.tables ? bundle.i18n.tables : {};
  const tableLangs = Object.keys(tables);
  const prefKey = prefKeyOf(ctx.meta);
  const vp0 = domViewport();
  const prefs = prefsParse(domStoreGet(prefKey), { levels: ctx.levels, languages: tableLangs, viewportWidth: vp0.w });
  const lang = i18nPickLang(tableLangs, [prefs.lang, ctx.primaryLanguage, domNavigatorLanguage()]);
  const i18nCache = new Map();
  const i18nFor = (l) => { if (!i18nCache.has(l)) i18nCache.set(l, makeI18n(tables, l, (rec) => sink.push(rec))); return i18nCache.get(l); };

  const registry = navMakeRegistry();
  for (const def of options.tabs || []) { const r = registry.registerTab(def); if (!r.ok) boundary.report('registerTab', r.reason); }
  for (const def of options.sideTabs || []) registry.registerSideTab(def);

  const counter = { n: 0 };
  const mountQueue = []; // callbacks of the tabs, run once per redraw right after the tree is in the page (the canvases of the stage draw there)
  let state = shellInitial(ctx, prefs, navParseRoute(domHash(), registry.tabIds()), registry.tabIds(), lang);
  let t = i18nFor(lang).t; let lookupIndex = null; let dragFlag = false; let statusExtra = ''; let rendering = false; let pending = false;

  const root = () => domById('atlas-root');
  const label = (collection, id) => viewVocabLabel(ctx, collection, id, state.lang);
  const tabOf = () => registry.getTab(state.tab);

  const api = {
    ctx, boundary, registry, label,
    cache: new Map(), // per application scratch space for tab modules (rendered parts that are expensive to rebuild)
    t: (key, vars) => t(key, vars),
    get state() { return state; },
    title: (id) => viewElementTitle(ctx, id),
    select: (sel) => commit(shellSelect(state, sel)),
    setLevel: (level) => commit(shellSetLevel(state, ctx.levels.includes(level) ? level : state.level)),
    setTab: (id) => (registry.hasTab(id) ? (commit(shellSetTab(state, id)), true) : false),
    setDoc: (doc) => commit({ ...state, doc }),
    setRaw: (on) => commit({ ...state, docRaw: !!on }),
    setTwod: (patch) => commit({ ...state, twod: { ...state.twod, ...patch } }),
    setTwodView: (view) => { state = { ...state, twod: { ...state.twod, view } }; },
    setNavFilter: (text) => commit({ ...state, navFilter: String(text).slice(0, 200) }),
    setInspectorTab: (id) => commit({ ...state, inspectorTab: id }),
    openUnit: (id) => commit({ ...shellSetTab(state, registry.hasTab('2D') ? '2D' : state.tab), twod: { ...state.twod, board: 'diagrams', unitId: id } }),
    dragged: () => dragFlag,
    markDragged: () => { dragFlag = true; domLater(() => { dragFlag = false; }, 0); },
    redraw: () => app.renderOnce(),
    setStatus: (text) => { statusExtra = String(text); },
    mounted: (fn) => { if (typeof fn === 'function') mountQueue.push(fn); },
  };

  function commit(next) { state = next; app.renderOnce(); return true; }

  // ---------- search ----------
  function ensureIndex() {
    if (!lookupIndex) lookupIndex = boundary.guardRun('search', () => makeSearchIndex(ctx.model, ctx.diagrams, { vocab: ctx.vocab, ix: ctx.ix }), []);
    return lookupIndex;
  }
  function applyHit(hit) {
    const ids = registry.tabIds();
    const go = shellApplyHit(ctx, shellHitFrom(hit, state.tab), ids);
    let next = { ...state, searchOpen: false, query: '' };
    if (go.level) next = shellSetLevel(next, go.level);
    if (go.tab) next = { ...next, tab: go.tab, inspectorTab: 'detail', overlay: null };
    if (go.sel) next = shellSelect(next, go.sel);
    if (go.requirementId) next = { ...next, doc: docSetRequirement(next.doc, go.requirementId) };
    if (go.unitId) next = { ...next, twod: { ...next.twod, board: 'diagrams', unitId: go.unitId } };
    commit(next);
  }

  function renderFinder() {
    const hits = state.query.trim() ? shellMergeHits(search(ensureIndex(), state.query)) : [];
    const input = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'search-input', value: state.query, placeholder: t('search.placeholder'), 'aria-label': t('search.label'),
      oninput: (ev) => commit({ ...state, query: ev.target.value }), onkeydown: (ev) => { if (ev.key === 'Enter' && hits.length) { ev.preventDefault(); applyHit(hits[0]); } } });
    const list = hits.map((h) => domEl('li', null, domEl('button', { type: 'button', class: 'hit', onclick: () => applyHit(h) },
      domEl('span', { class: 'hit-kind muted' }, SHELL_SEARCH_KIND_KEYS[h.kind] ? t(SHELL_SEARCH_KIND_KEYS[h.kind]) : h.kind, ' \u00b7 ', (h.tabs && h.tabs.length ? h.tabs : [h.tab]).map((x) => t(SHELL_TAB_KEYS[x] || 'tab.2d')).join(' / ')), ' ', domEl('span', { class: 'hit-title' }, String(h.title)), ' ', domEl('span', { class: 'muted' }, String(h.sub)))));
    return domEl('div', { class: 'scrim', onclick: (ev) => { if (ev.target === ev.currentTarget) commit({ ...state, searchOpen: false, query: '' }); } },
      domEl('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('search.title') }, input,
        domEl('ul', { class: 'hits', role: 'listbox' }, list),
        state.query.trim() && !hits.length ? domEl('p', { class: 'empty' }, t('search.none')) : null));
  }

  // ---------- chrome ----------
  function renderHeader(kind) {
    const tabs = registry.tabIds().map((id) => domEl('button', { type: 'button', class: 'tab' + (state.tab === id ? ' on' : ''), 'aria-current': state.tab === id ? 'page' : null, 'data-tab': id, 'data-keep': 'tab-' + id, onclick: () => api.setTab(id), onkeydown: (ev) => shellTabKey(ev, id) }, t(SHELL_TAB_KEYS[id] || registry.getTab(id).labelKey)));
    const levels = ctx.levels.map((lv) => domEl('button', { type: 'button', class: 'seg' + (state.level === lv ? ' on' : ''), 'aria-pressed': state.level === lv ? 'true' : 'false', 'data-level': lv, 'data-keep': 'level-' + lv, onclick: () => api.setLevel(lv) }, label('levels', lv)));
    const langs = domEl('select', { 'aria-label': t('hdr.lang'), 'data-keep': 'lang', onchange: (ev) => commit(shellSetLang(state, ev.target.value)) }, tableLangs.map((l) => domEl('option', { value: l, selected: l === state.lang ? 'selected' : null }, t(l === 'zh-CN' ? 'hdr.lang.zh' : l === 'ja-JP' ? 'hdr.lang.ja' : 'hdr.lang.en'))));
    const nextTheme = THEMES[(THEMES.indexOf(state.theme) + 1) % THEMES.length];
    const def = tabOf();
    const status = t('status.ready', { nodes: ctx.nodes.length, edges: ctx.edges.length }) + (statusExtra ? ' \u00b7 ' + statusExtra : '');
    const title = typeof ctx.meta.title === 'string' && ctx.meta.title ? ctx.meta.title : String(ctx.meta.projectId || t('app.untitled'));
    return domEl('header', { class: 'hdr', 'data-gate': 'header' },
      domEl('div', { class: 'brand', 'data-gate': 'fit' }, domEl('strong', null, title), ' ', domEl('span', { class: 'muted' }, String(ctx.meta.modelSha8 || ''))),
      domEl('nav', { class: 'tabs', 'aria-label': t('hdr.tabs') }, tabs),
      domEl('div', { class: 'choices', role: 'group', 'aria-label': t('hdr.level') }, levels),
      langs,
      domEl('button', { type: 'button', 'data-keep': 'theme', onclick: () => commit(shellSetTheme(state, nextTheme)), title: t('hdr.theme.title') }, t(SHELL_THEME_KEYS[state.theme] || 'hdr.theme.auto')),
      domEl('button', { type: 'button', 'data-keep': 'panel-nav', 'aria-pressed': (kind === 'overlay' ? state.overlay === 'nav' : state.navOpen) ? 'true' : 'false', onclick: () => commit(shellTogglePanel(state, 'nav', kind)) }, t('hdr.panel.nav')),
      def && def.inspector ? domEl('button', { type: 'button', 'data-keep': 'panel-inspector', 'aria-pressed': (kind === 'overlay' ? state.overlay === 'inspector' : state.inspectorOpen) ? 'true' : 'false', onclick: () => commit(shellTogglePanel(state, 'inspector', kind)) }, t('hdr.panel.inspector')) : null,
      domEl('button', { type: 'button', 'data-keep': 'search-open', onclick: () => commit({ ...state, searchOpen: true, query: '' }) }, t('hdr.search')),
      domEl('span', { class: 'status', role: 'status', 'data-gate': 'fit' }, status));
  }

  function shellTabKey(ev, id) {
    const ids = registry.tabIds(); const i = ids.indexOf(id);
    const dir = ARROW_KEY.exec(ev.key);
    const to = dir && dir[1] === 'Right' ? ids[(i + 1) % ids.length] : dir && dir[1] === 'Left' ? ids[(i + ids.length - 1) % ids.length] : ev.key === 'Home' ? ids[0] : ev.key === 'End' ? ids[ids.length - 1] : null;
    if (to) { ev.preventDefault(); api.setTab(to); }
  }

  function renderSide(sideApi, def) {
    const subs = def && def.controls ? ['ctl', 'nav'] : ['nav'];
    const sub = subs.includes(state.navSub) ? state.navSub : subs[0];
    const strip = subs.length > 1 ? domEl('div', { class: 'choices', role: 'group', 'aria-label': t('panel.nav.label') }, subs.map((s) => domEl('button', { type: 'button', 'aria-pressed': sub === s ? 'true' : 'false', class: 'seg' + (sub === s ? ' on' : ''), onclick: () => commit({ ...state, navSub: s }) }, t(s === 'ctl' ? 'panel.controls' : 'panel.navigator')))) : null;
    const body = domEl('div', { class: 'panel-body' });
    if (sub === 'ctl') def.controls(body, sideApi); else renderNavigator(body, sideApi);
    return domEl('aside', { class: 'panel panel-nav', 'aria-label': t('panel.nav.label'), 'data-open': sideShown('nav') ? '1' : '0' }, strip, body);
  }

  function sideShown(name) {
    const kind = layoutKindFor(domViewport().w);
    if (kind === 'overlay') return state.overlay === name;
    return name === 'nav' ? state.navOpen : state.inspectorOpen;
  }

  // ---------- the application object ----------
  const app = {
    version: SHELL_API_VERSION, bundle: ctx.bundle, ctx, api,
    registerTab: (def) => { const r = registry.registerTab(def); if (r.ok) app.renderOnce(); return r; },
    registerSideTab: (def) => { const r = registry.registerSideTab(def); if (r.ok) app.renderOnce(); return r; },
    tabs: () => registry.tabIds(),
    get tab() { return state.tab; },
    get level() { return state.level; },
    get selection() { return state.sel; },
    state: () => state,
    setTab: (id) => api.setTab(id),
    setLevel: (level) => api.setLevel(level),
    select: (sel) => api.select(sel),
    frame: () => counter.n,
    errors: sink,
    warnings: () => ctx.warnings.items,
    calibrationHooks: ctx.bundle.calibrationHooks || {},
    checkDocSelections: () => docCheckAll(ctx),

    /** Synchronous full render of the active tab (chrome, side areas and stage). The heartbeat advances in a finally, whatever happens. */
    renderOnce(tab) {
      if (typeof tab === 'string' && tab !== state.tab && registry.hasTab(tab)) state = shellSetTab(state, tab);
      if (rendering) { pending = true; return { tab: state.tab, frame: counter.n, ok: true, error: null }; }
      rendering = true;
      let res;
      try {
        do {
          pending = false;
          res = boundary.frameRun(counter, 'render', draw);
        } while (pending);
      } finally { rendering = false; }
      return { tab: state.tab, frame: counter.n, ok: res.ok, error: res.error };
    },

    /** The configuration object for AtlasGate.run (gate mode only; the walker itself lives in the gate self-test script). */
    gateConfig() {
      return {
        tabs: registry.tabIds(),
        setTab: (id) => { app.setTab(id); },
        tabState: () => domGet(domBody(), 'data-tab'),
        renderOnce: (tab) => { const r = app.renderOnce(tab); return { tab: r.tab, ok: r.ok }; },
        frame: () => counter.n,
        headerSelector: '[data-gate~=header]',
        // projected frames and nodes of the stage that was just drawn (3D, 4D, 5D), or null for a tab without a canvas
        sceneBounds: (tab) => { const d = registry.getTab(typeof tab === 'string' ? tab : state.tab); return d && typeof d.sceneBounds === 'function' ? d.sceneBounds(api) : null; },
        // data strings that legitimately say undefined / NaN / [object (prose of the model): the gate does not count them as a rendering fault
        textAllow: () => gateTextAllow(ctx.bundle),
        steps: gateSteps(),
      };
    },
  };

  /** Run fn with another tab shown, then put the tab back. A tab the page does not have is a failure of the step (a page that lost a tab must not pass). */
  function gateWithTab(id, fn) {
    if (!registry.hasTab(id)) return 'the tab ' + id + ' is not in the page'; // diag: author-facing
    const before = state.tab;
    if (before !== id) api.setTab(id);
    try { return fn(); } finally { if (state.tab !== before) api.setTab(before); }
  }

  function gateSteps() {
    return [
      { name: 'doc-twin', run: () => {
        const rows = docCheckAll(ctx); const bad = rows.filter((r) => !r.ok);
        for (const b of bad) boundary.report('doc-twin', 'document hash differs at ' + b.level + ' (' + b.count + ' ids)'); // diag: author-facing
        return bad.length === 0 || 'document twin mismatch'; // diag: author-facing
      } },
      { name: 'levels', run: () => {
        const before = state.level;
        for (const lv of ctx.levels) { api.setLevel(lv); if (domGet(domBody(), 'data-level') !== lv) return 'level did not change'; } // diag: author-facing
        api.setLevel(before);
        return true;
      } },
      { name: 'inspector', run: () => {
        const first = ctx.nodes[0];
        if (!first) return true;
        api.select({ type: 'node', id: first.id });
        const ok = !!domQuery('.insp-detail') || !sideShown('inspector') || !(tabOf() && tabOf().inspector);
        api.select(null);
        return ok || 'inspector shows no detail'; // diag: author-facing
      } },
      { name: 'twod-labels', run: () => gateWithTab('2D', () => {
        const board = state.twod.board;
        if (board !== 'canvas') api.setTwod({ board: 'canvas' });
        const svg = domQuery('svg.twod');
        let out = true;
        if (svg) {
          // Measure the names at the smallest scale at which they are drawn at all (TWOD_LOD_SCALE), whatever the fit scale of this atlas is today:
          // below it the names are hidden, and a box of 2 or 3 px is judged by glyph-metric rounding, not by the placement.
          const hits = domAtScale(svg, TWOD_LOD_SCALE, () => gateOverlaps(domBoxes('text.frame-label')));
          if (hits.length) out = 'frame labels overlap: ' + hits.length + ' pair(s), first ' + String(hits[0][0].text).slice(0, 24) + ' / ' + String(hits[0][1].text).slice(0, 24); // diag: author-facing
        }
        if (board !== 'canvas') api.setTwod({ board });
        return out;
      }) },
      { name: 'qa-rows', run: () => gateWithTab('QA', () => domQueryAll('[data-qa-row]').length > 0 || 'QA tab shows no rows') }, // diag: author-facing
      { name: 'table-words', run: () => {
        for (const id of ['1D', 'QA']) {
          const broken = gateWithTab(id, () => domBrokenTokens(['th', 'td'], GATE_TOKEN_MAX));
          if (typeof broken === 'string') return broken;
          if (broken.length) return 'a table word is cut across lines on ' + id + ': ' + String(broken[0]).slice(0, 24); // diag: author-facing
        }
        return true;
      } },
      { name: 'search', run: () => {
        commit({ ...state, searchOpen: true, query: '' });
        const opened = !!domQuery('[role=dialog]');
        commit({ ...state, searchOpen: false, query: '' });
        return (opened && !domQuery('[role=dialog]')) || 'search dialog did not open and close'; // diag: author-facing
      } },
      { name: 'inspector-tabs', run: () => {
        const before = state.tab; const beforeInspector = state.inspectorTab; let out = true;
        for (const id of registry.tabIds()) {
          const def = registry.getTab(id);
          if (!def || !def.inspector) continue;
          if (state.tab !== id) api.setTab(id);
          if (!domQuery('.panel-ins')) continue; // the visitor closed the inspector: there is nothing to open
          for (const slot of ['detail', ...registry.sideTabs(id).map((x) => x.id)]) {
            api.setInspectorTab(slot);
            const body = domQuery('.panel-ins .insp-body');
            if (!body || !body.firstChild) { out = 'the inspector tab ' + slot + ' of ' + id + ' shows nothing'; break; } // diag: author-facing
          }
          if (out !== true) break;
        }
        if (state.tab !== before) api.setTab(before);
        if (state.inspectorTab !== beforeInspector) api.setInspectorTab(beforeInspector);
        return out;
      } },
      ...registry.tabIds().flatMap((id) => { const d = registry.getTab(id); return d && typeof d.gateSteps === 'function' ? d.gateSteps(api) : []; }),
    ];
  }

  // ---------- drawing ----------
  function draw() {
    const el = root();
    if (!el) throw new Error('atlas-root missing'); // diag: author-facing
    t = i18nFor(state.lang).t;
    const vp = domViewport(); const kind = layoutKindFor(vp.w); const def = tabOf();
    const focus = domFocusState();
    const cols = layoutColumns({ navOpen: state.navOpen, inspectorOpen: state.inspectorOpen, navWidth: prefs.navWidth, inspectorWidth: prefs.inspectorWidth }, vp.w);
    domRootAttrs({ lang: state.lang, 'data-scheme': state.theme === 'auto' ? null : state.theme });
    domSetTitle(typeof ctx.meta.title === 'string' && ctx.meta.title ? ctx.meta.title : t('app.title'));
    domSet(domBody(), 'data-tab', state.tab);
    domSet(domBody(), 'data-level', state.level);
    domSet(el, 'data-layout', kind); domSet(el, 'data-wrap', headerWraps(vp.w) ? '1' : '0');
    domSet(el, 'style', '--nav-w:' + cols.nav + 'px;--ins-w:' + cols.inspector + 'px');
    mountQueue.length = 0;
    const stageBefore = domQuery('.stage');
    const scroll = stageBefore && domGet(stageBefore, 'data-tab') === state.tab ? stageBefore.scrollTop : 0;
    domClear(el);
    el.appendChild(renderHeader(kind));

    const shellEl = domEl('div', { class: 'shell' });
    const stage = domEl('main', { class: 'stage', 'data-tab': state.tab, tabindex: '-1', 'aria-label': def ? t(def.labelKey) : '' });
    if (!def) stage.appendChild(domEl('p', { class: 'empty' }, t('app.notab')));
    else {
      stage.appendChild(domEl('p', { class: 'question muted' }, def.questionKey ? t(def.questionKey) : ''));
      const host = domEl('div', { class: 'tab-host' });
      boundary.guardRun('tab ' + def.id, () => def.render(host, api), null);
      stage.appendChild(host);
    }
    const showNav = kind === 'overlay' || cols.nav > 0;
    if (showNav) shellEl.appendChild(boundary.guardRun('sidebar', () => renderSide(api, def), domEl('aside', { class: 'panel panel-nav' })));
    shellEl.appendChild(stage);
    const wantsInspector = !!(def && def.inspector);
    if (wantsInspector && (kind === 'overlay' || cols.inspector > 0)) {
      const ins = domEl('aside', { class: 'panel panel-ins', 'aria-label': t('insp.title'), 'data-open': sideShown('inspector') ? '1' : '0' });
      boundary.guardRun('inspector', () => renderInspector(ins, api, registry.sideTabs(state.tab)), null);
      shellEl.appendChild(ins);
    }
    if (kind === 'overlay' && state.overlay) shellEl.appendChild(domEl('div', { class: 'side-scrim', onclick: () => commit({ ...state, overlay: null }) }));
    el.appendChild(shellEl);
    for (const fn of mountQueue.splice(0)) boundary.guardRun('mount', fn, null);
    if (state.searchOpen) el.appendChild(boundary.guardRun('search', renderFinder, domEl('div')));
    domRestoreFocus(focus);
    domScrollTop(domQuery('.stage'), scroll);
    persist();
    const hash = navBuildRoute(state.tab, shellRouteParams(state));
    if (hash !== domHash()) domSetHash(hash);
  }

  function persist() {
    const next = { ...prefs, lang: state.lang, theme: state.theme, level: state.level, navOpen: state.navOpen, inspectorOpen: state.inspectorOpen, sub: { ...prefs.sub, nav: state.navSub } };
    domStoreSet(prefKey, prefsSerialize(next));
  }

  // ---------- events ----------
  function onKey(ev) {
    const target = ev.target; const tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || !!(target && target.isContentEditable);
    const act = shellKeyAction(ev.key, { ctrl: ev.ctrlKey, alt: ev.altKey, meta: ev.metaKey }, typing, registry.tabIds());
    if (!act) return;
    if (act.type === 'tab') { ev.preventDefault(); api.setTab(act.id); }
    else if (act.type === 'search') { ev.preventDefault(); commit({ ...state, searchOpen: true, query: '' }); }
    else if (act.type === 'panel') { ev.preventDefault(); commit(shellTogglePanel(state, act.name, layoutKindFor(domViewport().w))); }
    else if (act.type === 'escape') { const next = shellEscape(state); if (next !== state) { ev.preventDefault(); commit(next); } }
  }
  function onRoute() {
    const r = navParseRoute(domHash(), registry.tabIds());
    let next = state;
    if (r.tab && r.tab !== state.tab) next = shellSetTab(next, r.tab);
    if (typeof r.params.level === 'string' && ctx.levels.includes(r.params.level) && r.params.level !== state.level) next = shellSetLevel(next, r.params.level);
    if (next !== state) commit(next);
  }
  domOnResize(() => app.renderOnce());
  domOnKey(onKey);
  domOnHash(onRoute);
  return app;
}
