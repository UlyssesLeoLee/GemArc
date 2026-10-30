// Shell state and its transitions (pure). One plain record holds everything the shell draws from: tab, level, selection, language, theme,
// the open overlay, the search box, the 1D selection and the 2D view. Transitions return new records; the shell stores the result and renders.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { navParseSel, navSelText } from './nav.js';
import { docInitial } from './doc-state.js';
import { viewHasSelection } from './model-view.js';
import { isRecord, asList } from '../model-access.js';

export const SHELL_SIDES = ['nav', 'inspector'];

/** The first state: the route wins over the stored preferences, which win over the defaults. `tabs` is the list of registered tab ids. */
export function shellInitial(ctx, prefs, route, tabs, lang) {
  const params = route && route.params ? route.params : {};
  const level = typeof params.level === 'string' && ctx.levels.includes(params.level) ? params.level : (ctx.levels.includes(prefs.level) ? prefs.level : ctx.levels[0]);
  const sel = navParseSel(params.sel);
  const tab = route && route.tab && tabs.includes(route.tab) ? route.tab : (tabs.includes('2D') ? '2D' : tabs[0]);
  return {
    tab, level, lang, theme: prefs.theme,
    sel: sel && viewHasSelection(ctx, sel) ? sel : null,
    overlay: null, searchOpen: false, query: '', inspectorTab: 'detail', navSub: prefs.sub && typeof prefs.sub.nav === 'string' ? prefs.sub.nav : 'ctl',
    navFilter: '', navOpen: prefs.navOpen, inspectorOpen: prefs.inspectorOpen,
    doc: docInitial(ctx, level, params), docRaw: false,
    twod: { view: null, query: '', board: 'canvas', unitId: null },
  };
}

export const shellSetTab = (st, tab) => ({ ...st, tab, overlay: null, inspectorTab: 'detail' });
export const shellSetLevel = (st, level) => ({ ...st, level, doc: { ...st.doc, level: st.doc.level === level ? st.doc.level : level } });
export const shellSelect = (st, sel) => ({ ...st, sel: sel && typeof sel.type === 'string' && typeof sel.id === 'string' ? { type: sel.type, id: sel.id } : null });
export const shellSetLang = (st, lang) => ({ ...st, lang });
export const shellSetTheme = (st, theme) => ({ ...st, theme });

/** Open or close a side panel: docked layouts toggle the column, overlay layouts keep at most one overlay open. */
export function shellTogglePanel(st, name, layoutKind) {
  if (!SHELL_SIDES.includes(name)) return st;
  if (layoutKind === 'overlay') return { ...st, overlay: st.overlay === name ? null : name };
  return name === 'nav' ? { ...st, navOpen: !st.navOpen } : { ...st, inspectorOpen: !st.inspectorOpen };
}

/** Esc closes one thing per press, innermost first: search, overlay, selection. */
export function shellEscape(st) {
  if (st.searchOpen) return { ...st, searchOpen: false, query: '' };
  if (st.overlay) return { ...st, overlay: null };
  if (st.sel) return { ...st, sel: null };
  return st;
}

/** The route text for the state (written with replaceState). */
export function shellRouteParams(st) {
  const p = { level: st.level };
  const s = navSelText(st.sel);
  if (s) p.sel = s;
  if (st.tab === '1D' && st.doc.mode === 'requirement' && st.doc.reqId) p.req = st.doc.reqId;
  return p;
}

/**
 * Keyboard shortcuts as data: digits 1..N switch tabs, [ and ] toggle the side areas, / and Ctrl+K open search, Escape closes.
 * Nothing fires while the user types in a field (except Escape) or while a modifier other than Ctrl+K is held.
 */
export function shellKeyAction(key, mods, typing, tabIds) {
  const m = mods || {};
  if (key === 'Escape') return { type: 'escape' };
  if ((key === 'k' || key === 'K') && m.ctrl) return { type: 'search' };
  if (typing || m.ctrl || m.alt || m.meta) return null;
  if (key === '/') return { type: 'search' };
  if (key === '[') return { type: 'panel', name: 'nav' };
  if (key === ']') return { type: 'panel', name: 'inspector' };
  if (/^[1-9]$/.test(key)) { const id = asList(tabIds)[Number(key) - 1]; return id ? { type: 'tab', id } : null; }
  return null;
}

/**
 * One row per thing: a part has a 2D and a 3D page, so the search lists it twice. The merged hit keeps the first (best, 2D first) and lists every
 * page in `tabs`; the shell opens the page the visitor is on when it is one of them, else the first.
 */
export function shellMergeHits(hits) {
  const seen = new Map(); const out = [];
  for (const h of asList(hits)) {
    if (!isRecord(h)) continue;
    const key = String(h.kind) + '\u0000' + String(h.id);
    const first = seen.get(key);
    if (first) { if (typeof h.tab === 'string' && !first.tabs.includes(h.tab)) first.tabs.push(h.tab); continue; }
    const row = { ...h, tabs: typeof h.tab === 'string' ? [h.tab] : [] };
    seen.set(key, row); out.push(row);
  }
  return out;
}

/** The hit as it will be applied from the page `current`: a merged hit that has that page opens it. */
export function shellHitFrom(hit, current) {
  return isRecord(hit) && Array.isArray(hit.tabs) && hit.tabs.includes(current) && hit.tab !== current
    ? { ...hit, tab: current, target: { ...(isRecord(hit.target) ? hit.target : {}), tab: current } } : hit;
}

/** Where a search hit leads: { tab, sel, level, requirementId, unitId } (every field optional). The shell applies it. */
export function shellApplyHit(ctx, hit, tabIds) {
  const h = isRecord(hit) ? hit : {}; const t = isRecord(h.target) ? h.target : {};
  const out = {};
  const wanted = typeof t.tab === 'string' ? t.tab : h.tab;
  if (typeof wanted === 'string') out.tab = tabIds.includes(wanted) ? wanted : (tabIds.includes('2D') ? '2D' : tabIds[0]);
  if (typeof t.level === 'string' && ctx.levels.includes(t.level)) out.level = t.level;
  if (h.kind === 'requirement' && typeof h.id === 'string') out.requirementId = h.id;
  else if (typeof t.node === 'string') out.sel = { type: 'node', id: t.node };
  else if (typeof t.edge === 'string') out.sel = { type: 'edge', id: t.edge };
  else if (typeof t.caseId === 'string') out.sel = { type: 'case', id: t.caseId };
  if (typeof t.diagram === 'string') out.unitId = t.diagram;
  return out;
}
