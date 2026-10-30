// Routing and the tab registry (pure). The route lives in the location hash as #/<TAB>?level=<id>&sel=<kind>:<id>, written with
// replaceState and read on load and on hashchange, so a link reproduces the tab, the detail level and the selection. The registry is the
// extension seam of the shell: the 3D, 4D and 5D modules call registerTab / registerSideTab; nothing else in the shell changes.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const TAB_ORDER = ['1D', '2D', '3D', '4D', '5D', 'QA'];
const NAV_ROUTE_KEYS = ['level', 'sel', 'req', 'mode'];

/** Parse a location hash. Unknown tabs give tab = null (the caller picks its default); parameter values are decoded safely. */
export function navParseRoute(hash, knownTabs) {
  const tabs = Array.isArray(knownTabs) ? knownTabs : TAB_ORDER;
  const text = typeof hash === 'string' ? hash.replace(/^#\/?/, '') : '';
  const cut = text.indexOf('?');
  const head = cut < 0 ? text : text.slice(0, cut);
  const params = {};
  if (cut >= 0) {
    for (const pair of text.slice(cut + 1).split('&')) {
      const eq = pair.indexOf('=');
      if (eq <= 0) continue;
      const key = pair.slice(0, eq);
      if (!NAV_ROUTE_KEYS.includes(key)) continue;
      let val = pair.slice(eq + 1);
      try { val = decodeURIComponent(val); } catch (e) { continue; }
      if (val.length <= 400) params[key] = val;
    }
  }
  return { tab: tabs.includes(head) ? head : null, params };
}

/** Build the hash text for a tab and parameters (only the known keys, only non-empty values). */
export function navBuildRoute(tab, params) {
  const parts = [];
  for (const key of NAV_ROUTE_KEYS) {
    const v = params ? params[key] : undefined;
    if (typeof v === 'string' && v) parts.push(key + '=' + encodeURIComponent(v));
  }
  return '#/' + tab + (parts.length ? '?' + parts.join('&') : '');
}

/** A selection as route text and back: 'node:<id>', 'edge:<id>', ... ids may contain ':' (the first colon splits). */
export const navSelText = (sel) => (sel && typeof sel.type === 'string' && typeof sel.id === 'string' ? sel.type + ':' + sel.id : '');
export function navParseSel(text) {
  if (typeof text !== 'string') return null;
  const i = text.indexOf(':');
  if (i <= 0 || i === text.length - 1) return null;
  return { type: text.slice(0, i), id: text.slice(i + 1) };
}

const navTabOrder = (def) => (Number.isFinite(def.order) ? def.order : TAB_ORDER.indexOf(def.id) >= 0 ? TAB_ORDER.indexOf(def.id) : 100);

/**
 * The registry. A tab definition: { id, labelKey, questionKey?, order?, inspector?: boolean, available?(ctx) -> {ok, emptyKey?},
 * controls?(host, api), render(host, api) }. Registering an id that exists replaces it (this is how a later module takes over a reserved
 * slot). An inspector tab definition: { id, labelKey, order?, tabs?: [ids it belongs to], render(host, api) }.
 * register* never throws: it returns { ok, reason? }.
 */
export function navMakeRegistry() {
  const tabs = new Map(); const inspector = new Map();
  const check = (def, needs) => {
    if (!def || typeof def !== 'object') return 'definition missing'; // diag: author-facing
    if (typeof def.id !== 'string' || !def.id) return 'id missing'; // diag: author-facing
    for (const k of needs) if (typeof def[k] !== (k === 'render' || k === 'controls' ? 'function' : 'string')) return k + ' missing';
    return null;
  };
  return {
    registerTab(def) {
      const bad = check(def, ['labelKey', 'render']);
      if (bad) return { ok: false, reason: bad }; // diag: author-facing
      tabs.set(def.id, { ...def });
      return { ok: true };
    },
    registerSideTab(def) {
      const bad = check(def, ['labelKey', 'render']);
      if (bad) return { ok: false, reason: bad }; // diag: author-facing
      inspector.set(def.id, { ...def });
      return { ok: true };
    },
    hasTab: (id) => tabs.has(id),
    getTab: (id) => tabs.get(id) || null,
    tabIds: () => [...tabs.values()].sort((a, b) => navTabOrder(a) - navTabOrder(b)).map((d) => d.id),
    sideTabs: (tabId) => [...inspector.values()].filter((d) => !Array.isArray(d.tabs) || d.tabs.includes(tabId)).sort((a, b) => navTabOrder(a) - navTabOrder(b)),
    getSideTab: (id) => inspector.get(id) || null,
  };
}
