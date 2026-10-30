// Interface text lookup. Every string a user reads comes from a table keyed by a flat dotted key (viewer/i18n/<lang>.json, embedded in the
// bundle as i18n.tables) or from a vocabulary entry label (profile.<collection>[].label[<lang>]). Pure: no DOM.
//
// A missing key never yields undefined: the lookup returns the key itself (so a gap is visible and greppable) and records one entry in the
// optional sink. Placeholders are written {name} and filled from the vars argument.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const UI_FALLBACK_LANG = 'en-US';

/** Fill {name} placeholders; an unknown name is left as written, a non-string value is converted. */
export function i18nFill(template, vars) {
  const text = typeof template === 'string' ? template : '';
  if (!vars) return text;
  return text.replace(/\{([A-Za-z0-9_]+)\}/g, (m, name) => (Object.hasOwn(vars, name) && vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : m));
}

/** Pick the interface language: the stored choice, else the model primary language, else the browser language, else the first table. */
export function i18nPickLang(languages, wanted) {
  const have = Array.isArray(languages) ? languages.filter((l) => typeof l === 'string') : [];
  for (const w of wanted) {
    if (typeof w !== 'string' || !w) continue;
    if (have.includes(w)) return w;
    const base = w.split('-')[0].toLowerCase();
    const near = have.find((l) => l.split('-')[0].toLowerCase() === base);
    if (near) return near;
  }
  return have.includes(UI_FALLBACK_LANG) ? UI_FALLBACK_LANG : (have[0] || UI_FALLBACK_LANG);
}

/** The label of a vocabulary entry in `lang` (then the fallback language, then the entry id). */
export function i18nLabel(entry, lang, fallbackId) {
  const label = entry && typeof entry.label === 'object' && entry.label ? entry.label : null;
  const pick = (l) => (label && typeof label[l] === 'string' && label[l] ? label[l] : null);
  return pick(lang) || pick(UI_FALLBACK_LANG) || (entry && typeof entry.id === 'string' ? entry.id : String(fallbackId === undefined ? '' : fallbackId));
}

/**
 * makeI18n(tables, lang, sink) -> { lang, t(key, vars), has(key), label(entry, fallbackId), keys() }.
 * `tables` is {lang: {key: string}}; a key absent from the active table is read from the fallback table, then returned as the key.
 */
export function makeI18n(tables, lang, sink) {
  const own = (l) => (tables && typeof tables === 'object' && tables[l] && typeof tables[l] === 'object' ? tables[l] : {});
  const active = own(lang); const fallback = own(UI_FALLBACK_LANG); const missing = new Set();
  const read = (key) => (Object.hasOwn(active, key) && typeof active[key] === 'string' ? active[key] : Object.hasOwn(fallback, key) && typeof fallback[key] === 'string' ? fallback[key] : null);
  return {
    lang,
    t(key, vars) {
      if (typeof key !== 'string') return '';
      const v = read(key);
      if (v === null) {
        if (!missing.has(key)) { missing.add(key); if (sink) sink({ where: 'i18n', message: 'missing key ' + key }); } // diag: author-facing
        return String(key);
      }
      return i18nFill(v, vars);
    },
    has: (key) => read(key) !== null,
    label: (entry, fallbackId) => i18nLabel(entry, lang, fallbackId),
    keys: () => Object.keys(active),
  };
}
