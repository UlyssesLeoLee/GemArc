// Local preferences and responsive layout rules (pure). The preference record is stored per project and model under the key
// gemarc-atlas:<projectId>:<modelSha8>, so two atlases opened from the same place never share panel sizes or levels. Reading accepts
// anything (a missing, damaged or foreign record gives the defaults) and always returns a complete, valid record.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const PREF_VERSION = 1;
export const LAYOUT_OVERLAY_BELOW = 1100; // below this width the side areas are overlay panes (one open at a time)
export const HEADER_WRAP_BELOW = 1500; // below this width the header may use several rows
export const PANEL_LIMITS = { navWidth: [200, 520, 280], inspectorWidth: [280, 960, 380] }; // [min, max, default]
const PREFS_MAIN_MIN_WIDTH = 360; // the stage keeps at least this width while both side areas are docked
export const THEMES = ['auto', 'light', 'dark'];

/** The storage key of a bundle's preferences. */
export function prefKeyOf(meta) {
  if (meta && typeof meta.prefKey === 'string' && meta.prefKey) return meta.prefKey;
  const id = meta && typeof meta.projectId === 'string' ? meta.projectId : 'project';
  const sha = meta && typeof meta.modelSha8 === 'string' ? meta.modelSha8 : '00000000';
  return 'gemarc-atlas:' + id + ':' + sha;
}

/** Clamp a panel width; the upper bound also leaves room for the stage. */
export function prefsPanelWidth(name, value, viewportWidth) {
  const lim = PANEL_LIMITS[name];
  if (!lim) return 0;
  const n = typeof value === 'number' && Number.isFinite(value) ? value : lim[2];
  const cap = Math.max(lim[0], Math.min(lim[1], (Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 1920) * (name === 'navWidth' ? 0.34 : 0.62)));
  return Math.round(Math.min(cap, Math.max(lim[0], n)));
}

export const layoutKindFor = (viewportWidth) => (viewportWidth < LAYOUT_OVERLAY_BELOW ? 'overlay' : 'docked');
export const headerWraps = (viewportWidth) => viewportWidth < HEADER_WRAP_BELOW;

/** Docked column widths (0 when closed or in overlay mode); the inspector gives way first so the stage keeps its minimum. */
export function layoutColumns(prefs, viewportWidth) {
  if (layoutKindFor(viewportWidth) === 'overlay') return { nav: 0, inspector: 0 };
  let nav = prefs.navOpen ? prefsPanelWidth('navWidth', prefs.navWidth, viewportWidth) : 0;
  let inspector = prefs.inspectorOpen ? prefsPanelWidth('inspectorWidth', prefs.inspectorWidth, viewportWidth) : 0;
  const over = nav + inspector + PREFS_MAIN_MIN_WIDTH + 40 - viewportWidth;
  if (over > 0 && inspector) inspector -= Math.min(over, inspector - PANEL_LIMITS.inspectorWidth[0]);
  const overRest = nav + inspector + PREFS_MAIN_MIN_WIDTH + 40 - viewportWidth;
  if (overRest > 0 && nav) nav -= Math.min(overRest, nav - PANEL_LIMITS.navWidth[0]);
  return { nav, inspector };
}

/** The default record for a vocabulary level list (the middle level, or the only one). */
export function prefsDefaults(levels) {
  const ls = Array.isArray(levels) && levels.length ? levels : ['REQ', 'BD', 'DD'];
  return { v: PREF_VERSION, lang: null, theme: 'auto', level: ls[Math.min(1, ls.length - 1)], navWidth: PANEL_LIMITS.navWidth[2], inspectorWidth: PANEL_LIMITS.inspectorWidth[2], navOpen: true, inspectorOpen: true, sub: {} };
}

/** Parse the stored string (or any value) into a complete record. `ctx` = { levels, languages, viewportWidth }. */
export function prefsParse(raw, ctx) {
  const c = ctx || {};
  let o = null;
  if (typeof raw === 'string') { try { o = JSON.parse(raw); } catch (e) { o = null; } } else o = raw;
  if (!o || typeof o !== 'object' || Array.isArray(o)) o = {};
  const p = prefsDefaults(c.levels);
  const levels = Array.isArray(c.levels) && c.levels.length ? c.levels : ['REQ', 'BD', 'DD'];
  if (typeof o.level === 'string' && levels.includes(o.level)) p.level = o.level;
  if (typeof o.lang === 'string' && Array.isArray(c.languages) && c.languages.includes(o.lang)) p.lang = o.lang;
  if (THEMES.includes(o.theme)) p.theme = o.theme;
  p.navWidth = prefsPanelWidth('navWidth', o.navWidth, c.viewportWidth);
  p.inspectorWidth = prefsPanelWidth('inspectorWidth', o.inspectorWidth, c.viewportWidth);
  if (typeof o.navOpen === 'boolean') p.navOpen = o.navOpen;
  if (typeof o.inspectorOpen === 'boolean') p.inspectorOpen = o.inspectorOpen;
  if (o.sub && typeof o.sub === 'object' && !Array.isArray(o.sub)) {
    for (const k of Object.keys(o.sub)) if (typeof o.sub[k] === 'string' && k.length <= 40) p.sub[k] = o.sub[k].slice(0, 40);
  }
  return p;
}

export const prefsSerialize = (p) => JSON.stringify(p);
