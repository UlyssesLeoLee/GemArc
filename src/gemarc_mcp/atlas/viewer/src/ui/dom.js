// The one DOM adapter of the viewer: every access to the page (elements, events, storage, location, clipboard, timers, the global
// slots) goes through this module. All other runtime modules are DOM-free (tests/atlas/js/ui-structure.test.mjs scans them). Tab and
// shell modules build elements with domEl / domSvg, which only ever create text nodes: author text is never parsed as markup.
//
// The adapter reads the browser globals lazily, at call time, so a test can install a small stand-in before the first call.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

const DOM_SVG_NS = 'http://www.w3.org/2000/svg';

const domRoot = () => globalThis;
const domDoc = () => domRoot().document;

function domApplyAttrs(el, attrs) {
  if (!attrs) return el;
  for (const key of Object.keys(attrs)) {
    const v = attrs[key];
    if (v === undefined || v === null || v === false) continue;
    if (key === 'class') el.setAttribute('class', String(v));
    else if (key.startsWith('on') && typeof v === 'function') el.addEventListener(key.slice(2), v);
    else if (key === 'text') el.appendChild(domDoc().createTextNode(String(v)));
    else el.setAttribute(key, v === true ? '' : String(v));
  }
  return el;
}

function domAppend(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === undefined || k === null || k === false) continue;
    el.appendChild(typeof k === 'object' ? k : domDoc().createTextNode(String(k)));
  }
  return el;
}

/** Create an HTML element: domEl('div', {class: 'x', onclick: fn}, child, 'text', [more]). Strings become text nodes. */
export function domEl(tag, attrs, ...kids) { return domAppend(domApplyAttrs(domDoc().createElement(tag), attrs), kids); }
/** Create an SVG element (same conventions as domEl). */
export function domSvg(tag, attrs, ...kids) { return domAppend(domApplyAttrs(domDoc().createElementNS(DOM_SVG_NS, tag), attrs), kids); }
export const domText = (s) => domDoc().createTextNode(String(s));
export function domClear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
export const domById = (id) => domDoc().getElementById(id);
export const domQuery = (sel, from) => (from || domDoc()).querySelector(sel);
export const domQueryAll = (sel, from) => Array.from((from || domDoc()).querySelectorAll(sel));
export const domBody = () => domDoc().body;
export function domOn(el, type, fn, opts) { el.addEventListener(type, fn, opts); return () => el.removeEventListener(type, fn, opts); }
export const domSet = (el, name, value) => { if (value === null || value === undefined || value === false) el.removeAttribute(name); else el.setAttribute(name, value === true ? '' : String(value)); return el; };
export const domGet = (el, name) => el.getAttribute(name);
export const domClassOn = (el, name, on) => { el.classList.toggle(name, !!on); return el; };
export const domHasClass = (el, name) => !!(el && el.classList && el.classList.contains(name));
export const domFocus = (el) => { if (el && el.focus) el.focus(); };
export const domScrollTop = (el, y) => { if (el) el.scrollTop = y; };

/** Viewport size in CSS px. */
export function domViewport() {
  const w = domRoot();
  return { w: Math.round(w.innerWidth || 0), h: Math.round(w.innerHeight || 0) };
}
export const domPrefersDark = () => !!(domRoot().matchMedia && domRoot().matchMedia('(prefers-color-scheme: dark)').matches);
export const domNavigatorLanguage = () => (domRoot().navigator && domRoot().navigator.language) || '';

// ---------- storage: every read and write is guarded; a blocked store simply remembers nothing ----------
export function domStoreGet(key) {
  try { return domRoot().localStorage.getItem(key); } catch (e) { return null; }
}
export function domStoreSet(key, value) {
  try { domRoot().localStorage.setItem(key, value); return true; } catch (e) { return false; }
}

// ---------- location ----------
export const domHash = () => (domRoot().location && domRoot().location.hash) || '';
export const domQueryString = () => (domRoot().location && domRoot().location.search) || '';
export function domSetHash(hash) {
  const w = domRoot();
  try {
    if (w.history && w.history.replaceState) w.history.replaceState(null, '', hash);
    else w.location.hash = hash;
  } catch (e) { /* a sandboxed page may refuse; the hash is only a convenience */ }
}
export function domOnHash(fn) { domRoot().addEventListener('hashchange', fn); return () => domRoot().removeEventListener('hashchange', fn); }
export const domOnKey = (fn) => domOn(domDoc(), 'keydown', fn);
export const domOnResize = (fn) => domOn(domRoot(), 'resize', fn);

// ---------- clipboard ----------
export function domCopy(text) {
  const w = domRoot();
  try {
    if (w.navigator && w.navigator.clipboard && w.navigator.clipboard.writeText) { w.navigator.clipboard.writeText(text).catch(() => {}); return true; }
  } catch (e) { /* fall through to the textarea path */ }
  try {
    const ta = domEl('textarea', { 'aria-hidden': 'true', style: 'position:fixed;left:-9999px;top:0' });
    ta.value = text; domDoc().body.appendChild(ta); ta.select();
    const ok = domDoc().execCommand && domDoc().execCommand('copy');
    domDoc().body.removeChild(ta);
    return !!ok;
  } catch (e) { return false; }
}

// ---------- timers ----------
export const domLater = (fn, ms) => domRoot().setTimeout(fn, ms);

// ---------- global slots (the documented window API) ----------
/** The shared array behind window.__ATLAS_ERRORS__: created once, only ever pushed to (the gate keeps a reference to it). */
export function domSlotArray(name) {
  const w = domRoot();
  if (!Array.isArray(w[name])) w[name] = [];
  return w[name];
}
export function domExpose(name, value) { domRoot()[name] = value; return value; }
export const domReadSlot = (name) => domRoot()[name];
/** Last-resort error capture; the page keeps working after an error, the record goes to the sink. */
export function domCatchErrors(sink) {
  const w = domRoot();
  w.addEventListener('error', (ev) => sink({ where: 'window', message: String((ev && ev.message) || 'error') }));
  w.addEventListener('unhandledrejection', (ev) => sink({ where: 'promise', message: String((ev && ev.reason && ev.reason.message) || (ev && ev.reason) || 'rejection') }));
}

// ---------- focus keeping across a full re-render ----------
/** Remember which element (by data-keep) has focus and the caret; domRestoreFocus puts it back after the tree was rebuilt. */
export function domFocusState() {
  const a = domDoc().activeElement;
  if (!a || !a.getAttribute || !a.getAttribute('data-keep')) return null;
  const s = { keep: a.getAttribute('data-keep'), start: null, end: null };
  try { s.start = a.selectionStart; s.end = a.selectionEnd; } catch (e) { /* not a text control */ }
  return s;
}
export function domRestoreFocus(state) {
  if (!state) return;
  const el = domDoc().querySelector('[data-keep="' + String(state.keep).replace(/"/g, '') + '"]');
  if (!el) return;
  el.focus();
  try { if (state.start !== null && state.start !== undefined) el.setSelectionRange(state.start, state.end); } catch (e) { /* not a text control */ }
}

/** Set the lang attribute and the theme attributes on the root element. */
export function domRootAttrs(map) {
  const el = domDoc().documentElement;
  for (const k of Object.keys(map)) domSet(el, k, map[k]);
}
export function domSetTitle(text) { domDoc().title = String(text); }

/** Size and position of an element in CSS px ({left, top, width, height}); zeros when the element has no layout. */
export function domRect(el) {
  const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  return r ? { left: r.left || 0, top: r.top || 0, width: r.width || 0, height: r.height || 0 } : { left: 0, top: 0, width: 0, height: 0 };
}
/** Boxes (CSS px) and text of the visible elements that match a selector: the gate compares them. Elements without a box are left out. */
export function domBoxes(selector) {
  return domQueryAll(selector).map((el) => {
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    return r && r.width > 0 && r.height > 0 ? { text: String(el.textContent || ''), left: r.left, top: r.top, right: r.right, bottom: r.bottom } : null;
  }).filter(Boolean);
}
/** Run fn with the svg temporarily drawn at `scale` CSS px per scene unit and its text shown (class lod-low off); everything is put back. */
export function domAtScale(svg, scale, fn) {
  const r = domRect(svg); const vb = svg.getAttribute('viewBox'); const low = domHasClass(svg, 'lod-low');
  const p = String(vb || '').split(/\s+/).map(Number);
  try {
    if (r.width > 0 && r.height > 0 && scale > 0 && p.length === 4 && p.every(Number.isFinite)) svg.setAttribute('viewBox', [p[0], p[1], r.width / scale, r.height / scale].join(' '));
    domClassOn(svg, 'lod-low', false);
    return fn();
  } finally {
    if (vb === null) svg.removeAttribute('viewBox'); else svg.setAttribute('viewBox', vb);
    domClassOn(svg, 'lod-low', low);
  }
}
/** Texts (no whitespace, at most maxLen characters) of the elements matching any of the selectors that the browser lays out on more than one line: a word cut in the middle. */
export function domBrokenTokens(selectors, maxLen) {
  const out = [];
  for (const el of domQueryAll(selectors.join(','))) {
    const text = String(el.textContent || '').trim();
    if (!text || text.length > maxLen || /\s/.test(text)) continue;
    try {
      const range = domDoc().createRange(); range.selectNodeContents(el);
      const tops = new Set(Array.from(range.getClientRects()).filter((r) => r.width > 0).map((r) => Math.round(r.top)));
      if (tops.size > 1) out.push(text);
    } catch (e) { /* a stand-in without ranges has no layout to check */ }
  }
  return out;
}
/** Scroll the element with this id into view (the document outline uses it). */
export function domScrollToId(id) {
  const el = domDoc().getElementById(id);
  if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
}
/** Pointer capture helpers for dragging (a stand-in without them simply does not capture). */
export function domCapture(el, pointerId) { try { el.setPointerCapture(pointerId); } catch (e) { /* not supported */ } }
export function domRelease(el, pointerId) { try { el.releasePointerCapture(pointerId); } catch (e) { /* not captured */ } }

// ---------- the stage (3D, 4D, 5D): animation frames, canvas, WebGL context, motion preference ----------
/** Ask for one animation frame (0 when the page has none). The stage only uses it for interaction and easing, never to produce its result. */
export function domFrame(fn) {
  const w = domRoot();
  return typeof w.requestAnimationFrame === 'function' ? w.requestAnimationFrame(fn) : 0;
}
export function domCancelFrame(id) {
  const w = domRoot();
  if (id && typeof w.cancelAnimationFrame === 'function') w.cancelAnimationFrame(id);
}
export const domNow = () => { const p = domRoot().performance; return p && typeof p.now === 'function' ? p.now() : 0; };
export const domPixelRatio = () => Math.max(1, Math.min(2, Number(domRoot().devicePixelRatio) || 1));
/** The visitor asks for less motion, or the page runs under the gate (the gate needs a result that does not depend on time). */
export function domStillMode() {
  const w = domRoot();
  try { if (w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)').matches) return true; } catch (e) { /* no media queries */ }
  const gate = w.AtlasGate;
  return !!(gate && gate.active);
}
/** A WebGL context for the canvas (WebGL 2 first, then WebGL 1), or null: the stage then shows its labelled flat fallback. */
export function domGlContext(canvas) {
  const attrs = { antialias: true, alpha: false, depth: true, preserveDrawingBuffer: false };
  for (const kind of ['webgl2', 'webgl']) {
    try { const gl = canvas.getContext(kind, attrs); if (gl) return gl; } catch (e) { /* try the next kind */ }
  }
  return null;
}
/** Set the pixel size of a canvas (only when it changed: a resize clears the drawing buffer). Returns true when it changed. */
export function domCanvasPixels(canvas, w, h) {
  if (canvas.width === w && canvas.height === h) return false;
  canvas.width = w; canvas.height = h;
  return true;
}
/** Apply a CSS custom-property style string to an element (the stage sets two sizes this way). */
export function domStyleText(el, text) { el.setAttribute('style', String(text)); return el; }
