// Markdown blocks (from markdown.js) to page elements. Only text nodes are created, so author text can never become markup. Tables sit in a
// scrolling wrapper so a wide table never widens the page. Links with the dg: scheme become buttons that call opts.onDiagram(id); any other
// link is shown as plain text (the viewer is self-contained: nothing is fetched or opened).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl } from './dom.js';
import { mdDiagramId } from './markdown.js';

function mdInlineNodes(spans, opts) {
  return spans.map((x) => {
    if (x.k === 'c') return domEl('code', null, x.s);
    if (x.k === 'm') return domEl('span', { class: x.block ? 'mark mark-block' : 'mark', title: opts.t(x.block ? 'doc.marker.block' : 'doc.marker.note') }, x.s);
    if (x.k === 'a') {
      const dg = mdDiagramId(x.href);
      const kids = mdInlineNodes(x.s, opts);
      if (dg === null) return domEl('span', null, kids);
      return domEl('button', { type: 'button', class: 'link', 'data-diagram': dg, title: opts.t('doc.link.diagram'), onclick: () => opts.onDiagram(dg) }, kids);
    }
    return x.s;
  });
}

const MD_TOKEN_MAX = 40;
function mdSpanText(spans) { return spans.map((x) => (typeof x.s === 'string' ? x.s : Array.isArray(x.s) ? mdSpanText(x.s) : '')).join(''); }
/** Cell attributes: a short text without a space (an id, a grade, a one-word header) is marked as a token so the table never wraps it. */
function mdCellAttrs(spans, extra) {
  const txt = mdSpanText(spans);
  return { ...(extra || {}), class: txt.length > 0 && txt.length <= MD_TOKEN_MAX && !/\s/.test(txt) ? 'tok' : null };
}

/** Render blocks into a container element. opts: { t, onDiagram }. Chapter headings (level 2) get the id doc-ch-<n> for the outline. */
export function renderBlocks(blocks, opts) {
  const root = domEl('div', { class: 'md' });
  for (const b of blocks) {
    if (b.t === 'h') {
      const attrs = { class: 'h' + b.n };
      const m = b.n === 2 ? /^(\d+)\./.exec(b.inl.length && b.inl[0].k === 't' ? b.inl[0].s : '') : null;
      if (m) attrs.id = 'doc-ch-' + m[1];
      root.appendChild(domEl('h' + Math.min(6, b.n + 1), attrs, mdInlineNodes(b.inl, opts)));
    } else if (b.t === 'p') {
      root.appendChild(domEl('p', null, b.lines.map((l, i) => [i ? domEl('br') : null, mdInlineNodes(l, opts)])));
    } else if (b.t === 'quote') {
      root.appendChild(domEl('blockquote', null, mdInlineNodes(b.inl, opts)));
    } else if (b.t === 'ul') {
      root.appendChild(domEl('ul', null, b.items.map((it) => domEl('li', null, mdInlineNodes(it, opts)))));
    } else if (b.t === 'code') {
      root.appendChild(domEl('pre', null, domEl('code', null, b.text)));
    } else if (b.t === 'table') {
      const head = domEl('tr', null, b.head.map((c) => domEl('th', mdCellAttrs(c, { scope: 'col' }), mdInlineNodes(c, opts))));
      const rows = b.rows.map((r) => domEl('tr', null, b.head.map((_, i) => domEl('td', mdCellAttrs(r[i] || []), mdInlineNodes(r[i] || [], opts)))));
      root.appendChild(domEl('div', { class: 'tbl-wrap' }, domEl('table', null, domEl('thead', null, head), domEl('tbody', null, rows))));
    }
  }
  return root;
}
