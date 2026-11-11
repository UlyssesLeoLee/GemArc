// Test helper (not a test): a very small stand-in for the browser page, enough for dom.js and the shell/tab modules. It supports the element
// tree (createElement, createElementNS, text nodes, append/remove, attributes, class list, events), the lookups the adapter uses (by id and
// a few selector forms), storage, location and a viewport size. It is not a browser: no layout (every rectangle is zero unless set by the
// test), no CSS. Synthetic: no model data.

class StubNode {
  constructor(tag, ns) { this.tagName = tag.toUpperCase(); this.localName = tag; this.ns = ns || null; this.children = []; this.parent = null; this.attrs = new Map(); this.listeners = new Map(); this.value = ''; this.scrollTop = 0; this.rect = { left: 0, top: 0, width: 0, height: 0 }; }
  get firstChild() { return this.children[0] || null; }
  get nodeType() { return 1; }
  appendChild(n) { if (n.parent) n.parent.removeChild(n); n.parent = this; this.children.push(n); return n; }
  removeChild(n) { const i = this.children.indexOf(n); if (i >= 0) { this.children.splice(i, 1); n.parent = null; } return n; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); if (k === 'value') this.value = String(v); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  hasAttribute(k) { return this.attrs.has(k); }
  addEventListener(t, fn) { if (!this.listeners.has(t)) this.listeners.set(t, []); this.listeners.get(t).push(fn); }
  removeEventListener(t, fn) { const a = this.listeners.get(t) || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); }
  dispatch(t, ev = {}) { const e = { type: t, target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...ev }; for (const fn of this.listeners.get(t) || []) fn(e); return e; }
  click() { return this.dispatch('click'); }
  focus() { this.ownerDocument.activeElement = this; }
  select() {}
  setSelectionRange() {}
  getBoundingClientRect() { return { ...this.rect, right: this.rect.left + this.rect.width, bottom: this.rect.top + this.rect.height }; }
  get classList() {
    const self = this;
    const names = () => (self.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    return { toggle(n, on) { const s = new Set(names()); if (on === undefined ? !s.has(n) : on) s.add(n); else s.delete(n); self.setAttribute('class', [...s].join(' ')); }, contains: (n) => names().includes(n) };
  }
  get id() { return this.getAttribute('id') || ''; }
  get textContent() { return this.children.map((c) => (c.nodeType === 3 ? c.data : c.textContent)).join(''); }
  walk(fn) { fn(this); for (const c of this.children) if (c.nodeType === 1) c.walk(fn); }
}
class StubText { constructor(data) { this.data = String(data); this.nodeType = 3; this.parent = null; } }

const hasClass = (n, c) => (n.getAttribute('class') || '').split(/\s+/).includes(c);
/** Selector subset: comma lists of descendant chains of compound selectors made of tag, #id, .class, [attr], [attr=value], [attr~=value]. */
export function matches(n, selector) {
  return selector.split(',').some((part) => {
    const chain = part.trim().split(/\s+/);
    if (chain.length > 1) { // descendant combinator: the last compound matches n, earlier ones some ancestor, in order
      if (!matches(n, chain[chain.length - 1])) return false;
      let at = n.parent; let k = chain.length - 2;
      while (at && k >= 0) { if (at.nodeType === 1 && matches(at, chain[k])) k--; at = at.parent; }
      return k < 0;
    }
    const sel = chain[0]; const re = /(#[\w-]+)|(\.[\w-]+)|(\[[^\]]+\])|([a-zA-Z][\w-]*)/g; let m; let ok = true; let any = false;
    while ((m = re.exec(sel))) {
      any = true;
      if (m[1]) ok = ok && n.id === m[1].slice(1);
      else if (m[2]) ok = ok && hasClass(n, m[2].slice(1));
      else if (m[3]) {
        const q = /^\[([\w:-]+)(?:(~?=)"?([^"\]]*)"?)?\]$/.exec(m[3]);
        if (!q) { ok = false; continue; }
        const v = n.getAttribute(q[1]);
        if (!q[2]) ok = ok && v !== null;
        else if (q[2] === '=') ok = ok && v === q[3];
        else ok = ok && v !== null && v.split(/\s+/).includes(q[3]);
      } else ok = ok && n.localName === m[4].toLowerCase();
    }
    return any && ok;
  });
}

/** Install a page on globalThis. Returns { document, window, text(), find(sel), findAll(sel), setViewport(w, h), cleanup() }. */
export function installPage({ width = 1366, height = 800, hash = '', search = '', language = 'en-US', store = {} } = {}) {
  const saved = {}; const keys = ['document', 'innerWidth', 'innerHeight', 'location', 'history', 'localStorage', 'navigator', 'matchMedia', 'addEventListener', 'removeEventListener', 'AtlasApp', '__ATLAS_ERRORS__', '__ATLAS_WARNINGS__', '__ATLAS__', 'AtlasGate'];
  for (const k of keys) saved[k] = Object.getOwnPropertyDescriptor(globalThis, k);
  const winListeners = new Map();
  let doc = null;
  const mk = (tag, ns) => { const n = new StubNode(tag, ns); n.ownerDocument = doc; return n; };
  const html = mk('html'); const body = mk('body'); html.appendChild(body);
  const root = mk('div'); root.setAttribute('id', 'atlas-root'); body.appendChild(root);
  doc = {
    documentElement: html, body, title: '', activeElement: null,
    createElement: (t) => mk(t), createElementNS: (ns, t) => mk(t, ns), createTextNode: (d) => new StubText(d),
    getElementById: (id) => { let hit = null; html.walk((n) => { if (!hit && n.id === id) hit = n; }); return hit; },
    querySelector: (s) => { let hit = null; html.walk((n) => { if (!hit && n !== html && matches(n, s)) hit = n; }); return hit; },
    querySelectorAll: (s) => { const out = []; html.walk((n) => { if (n !== html && matches(n, s)) out.push(n); }); return out; },
    addEventListener(t, fn) { if (!winListeners.has('doc:' + t)) winListeners.set('doc:' + t, []); winListeners.get('doc:' + t).push(fn); },
    removeEventListener() {},
  };
  for (const n of [html, body, root]) n.ownerDocument = doc;
  const mem = { ...store };
  const loc = { hash, search };
  const set = (k, v) => Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
  set('document', doc); set('innerWidth', width); set('innerHeight', height); set('location', loc);
  set('history', { replaceState(_s, _t, url) { const i = String(url).indexOf('#'); loc.hash = i >= 0 ? String(url).slice(i) : ''; } });
  set('localStorage', { getItem: (k) => (Object.hasOwn(mem, k) ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, mem });
  set('navigator', { language });
  set('matchMedia', () => ({ matches: false }));
  set('addEventListener', (t, fn) => { if (!winListeners.has(t)) winListeners.set(t, []); winListeners.get(t).push(fn); });
  set('removeEventListener', () => {});
  delete globalThis.AtlasApp; delete globalThis.__ATLAS_ERRORS__; delete globalThis.__ATLAS_WARNINGS__; delete globalThis.AtlasGate;
  const page = {
    document: doc, root, body, store: mem, location: loc,
    text: () => root.textContent,
    find: (s) => doc.querySelector(s), findAll: (s) => doc.querySelectorAll(s),
    setViewport(w, h) { set('innerWidth', w); if (h) set('innerHeight', h); },
    fire(type, ev) { for (const fn of winListeners.get(type) || []) fn({ type, ...ev }); },
    key(key, ev = {}) { for (const fn of winListeners.get('doc:keydown') || []) fn({ type: 'keydown', key, target: doc.body, preventDefault() {}, ...ev }); },
    cleanup() { for (const k of keys) { if (saved[k]) Object.defineProperty(globalThis, k, saved[k]); else delete globalThis[k]; } },
  };
  return page;
}
