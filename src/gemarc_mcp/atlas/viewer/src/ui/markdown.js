// A small Markdown reader for the generated documents (pure). It understands exactly the constructs the document builder writes: headings,
// pipe tables, block quotes, bullet lists, paragraphs, triple-backtick code blocks, inline code spans, links (the dg: scheme points at a diagram unit),
// backslash escapes, the three XML entities and the marker tokens ([NOT-AUTHORED:slot], [N/A], [NONE-IN-SCOPE], [NONE-DECLARED]).
// It returns plain data; the renderer turns it into text nodes, so author text is never interpreted as markup.
//
// Block:  {t:'h', n, inl} | {t:'p', lines:[inl]} | {t:'quote', inl} | {t:'ul', items:[inl]} | {t:'table', head:[inl], rows:[[inl]]} | {t:'code', text}
// Inline: {k:'t', s} text | {k:'c', s} code | {k:'a', s: [inl], href} link | {k:'m', s, block} marker
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const MD_MARKER_BLOCK = 'NOT-AUTHORED';
const MD_MARKER = /^\[((?:NOT-AUTHORED:[^\]\n]*)|N\/A|NONE-IN-SCOPE|NONE-DECLARED)\]/;
const MD_ENTITIES = [['&lt;', '<'], ['&gt;', '>'], ['&amp;', '&']];
const MD_MAX_DEPTH = 2;
const MD_MAX_INLINE = 20000;
const MD_MAX_LINK = 2000;
const MD_LINK = /^\[((?:[^\]\\]|\\.)*)\]\(([^)\s]*)\)/;

const mdUnEntity = (s) => { let out = s; for (const [a, b] of MD_ENTITIES) out = out.split(a).join(b); return out; };

/** Inline spans of one line or cell. */
export function mdSpans(src, depth = 0) {
  const out = []; let buf = ''; let i = 0; const s = String(src);
  if (s.length > MD_MAX_INLINE) return [{ k: 't', s: mdUnEntity(s) }]; // a pathological cell is shown as plain text, not scanned quadratically
  const flush = () => { if (buf) { out.push({ k: 't', s: mdUnEntity(buf) }); buf = ''; } };
  while (i < s.length) {
    const ch = s[i];
    if (ch === '\\' && i + 1 < s.length && /[!-/:-@[-`{-~]/.test(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
    if (ch === '`') {
      let n = 1; while (s[i + n] === '`') n++;
      const fence = '`'.repeat(n); const end = s.indexOf(fence, i + n);
      if (end > i + n - 1 && end >= 0) { flush(); out.push({ k: 'c', s: mdUnEntity(s.slice(i + n, end).split('\\|').join('|').trim()) }); i = end + n; continue; }
      buf += fence; i += n; continue;
    }
    if (ch === '[') {
      const link = depth < MD_MAX_DEPTH ? MD_LINK.exec(s.slice(i, i + MD_MAX_LINK)) : null;
      if (link) { flush(); out.push({ k: 'a', s: mdSpans(link[1], depth + 1), href: link[2] }); i += link[0].length; continue; }
      const m = MD_MARKER.exec(s.slice(i, i + MD_MAX_LINK));
      if (m) { flush(); out.push({ k: 'm', s: m[1], block: m[1].startsWith(MD_MARKER_BLOCK) }); i += m[0].length; continue; }
    }
    buf += ch; i += 1;
  }
  flush();
  return out;
}

/** Split a table row on unescaped pipes; the outer pipes are optional. */
export function mdCells(line) {
  const cells = []; let cur = ''; const s = String(line).trim();
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '\\' && i + 1 < s.length) { cur += ch + s[i + 1]; i++; continue; }
    if (ch === '|') { cells.push(cur); cur = ''; continue; }
    cur += ch;
  }
  cells.push(cur);
  if (s.startsWith('|')) cells.shift();
  if (s.endsWith('|') && !s.endsWith('\\|') && cells.length) cells.pop();
  return cells.map((c) => c.trim());
}

const mdIsRule = (line) => /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

/** Parse a whole document into blocks. Total: any string gives a (possibly empty) block list. */
export function mdParse(text) {
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n'); const blocks = []; let para = null; let list = null;
  const endPara = () => { para = null; };
  const endList = () => { list = null; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) { endPara(); endList(); continue; }
    const fence = /^(`{3,})(.*)$/.exec(line);
    if (fence) {
      endPara(); endList();
      const body = []; i += 1;
      while (i < lines.length && !lines[i].startsWith(fence[1])) { body.push(lines[i]); i += 1; }
      blocks.push({ t: 'code', text: body.join('\n') });
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) { endPara(); endList(); blocks.push({ t: 'h', n: h[1].length, inl: mdSpans(h[2].trim()) }); continue; }
    if (line.trim().startsWith('|') && i + 1 < lines.length && mdIsRule(lines[i + 1].trim())) {
      endPara(); endList();
      const head = mdCells(line).map((c) => mdSpans(c)); const rows = []; i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(mdCells(lines[i]).map((c) => mdSpans(c))); i += 1; }
      i -= 1;
      blocks.push({ t: 'table', head, rows });
      continue;
    }
    const q = /^>\s?(.*)$/.exec(line);
    if (q) { endPara(); endList(); blocks.push({ t: 'quote', inl: mdSpans(q[1]) }); continue; }
    const li = /^[-*]\s+(.*)$/.exec(line);
    if (li) {
      endPara();
      if (!list) { list = { t: 'ul', items: [] }; blocks.push(list); }
      list.items.push(mdSpans(li[1]));
      continue;
    }
    endList();
    if (!para) { para = { t: 'p', lines: [] }; blocks.push(para); }
    para.lines.push(mdSpans(line.trim()));
  }
  return blocks;
}

/** The plain text of inline spans (used for titles and aria labels). */
export function mdPlain(inl) {
  return inl.map((x) => (x.k === 'a' ? mdPlain(x.s) : x.k === 'm' ? '[' + x.s + ']' : x.s)).join('');
}

/** The diagram unit id of a dg: link target, or null. */
export const mdDiagramId = (href) => (typeof href === 'string' && href.startsWith('dg:') && href.length > 3 ? href.slice(3) : null);

/** Number of NOT-AUTHORED markers in a text (release blockers, shown in the document header). */
export function mdCountMarkers(text) {
  const m = String(text).match(/\[NOT-AUTHORED:[^\]\n]*\]/g);
  return m ? m.length : 0;
}
