// Text assembly for the 1D tab: the JavaScript twin of derive/assemble.py. The assembled text is the union of the closures of the selected
// element ids plus the always-present fragments, sorted by (chapter, order), bucketed by group and joined. Output must be byte-equal to
// the Python version (tests/atlas/js/assemble.test.mjs). Pure: no DOM, no clock, no network.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const LEVELS = ['REQ', 'BD', 'DD'];
const PREFIX = ['RD', 'BD', 'DD'];
const SHIFT = 3;
const TARGET_LIMIT = 6;
const PH = ['\u0001DOCNO\u0001', '\u0001UPPER\u0001', '\u0001LOWER\u0001', '\u0001TARGET\u0001'];

/** FNV-1a (32 bit) of the UTF-8 bytes, 8 lowercase hex digits. */
export function hash32Hex(text) {
  let h = 2166136261;
  for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 16777619) >>> 0;
  return h.toString(16).padStart(8, '0');
}

function fragmentIds(ds, picked, k) {
  const items = ds.fragments.items; const bit = 1 << k; const out = new Set();
  for (const x of ds.fragments.always) if (x & bit) out.add(x >> SHIFT);
  for (const id of picked) for (const x of ds.closures[id]) if (x & bit) out.add(x >> SHIFT);
  return [...out].sort((a, b) => items[a].chapter - items[b].chapter || items[a].order - items[b].order);
}

/** The Markdown text of `level` for the selected `ids` (unknown ids are ignored, order and repeats do not matter). */
export function assemble(ds, level, ids) {
  const k = LEVELS.indexOf(level);
  if (k < 0) throw new Error('unknown level ' + level);
  const fs = ds.fragments; const items = fs.items; const frame = fs.frame[level];
  const pos = new Map(fs.elements.map((id, n) => [id, n]));
  const picked = [...new Set((ids || []).filter((id) => pos.has(id)))].sort((a, b) => pos.get(a) - pos.get(b));
  const sets = [0, 1, 2].map((n) => fragmentIds(ds, picked, n));
  const nos = sets.map((s, n) => PREFIX[n] + '-' + hash32Hex(LEVELS[n] + '\n' + fs.modelSha8 + '\n' + s.join(',')));
  const by = new Map();
  for (const f of sets[k]) { if (!by.has(items[f].grp)) by.set(items[f].grp, []); by.get(items[f].grp).push(items[f].md); }
  const parts = [frame.head];
  for (const ch of frame.chapters) {
    parts.push(ch.h);
    if (ch.note) parts.push(ch.note);
    const body = [];
    for (const gid of ch.groups) {
      const g = fs.groups[gid]; const members = by.get(gid);
      if (members) body.push((g.head ? [g.head, ...members] : members).join(g.join));
      else if (g.empty) body.push(g.empty);
    }
    parts.push(body.length ? body.join('\n\n') : ch.empty);
  }
  let target = fs.unselected;
  if (picked.length) {
    const shown = picked.slice(0, TARGET_LIMIT).map((id) => '`' + id.split('`').join("'").split('|').join('\\|') + '`').join(', ');
    target = shown + (picked.length > TARGET_LIMIT ? ' +' + (picked.length - TARGET_LIMIT) : '');
  }
  let text = parts.join('\n\n') + '\n';
  const values = [nos[k], k > 0 ? nos[k - 1] : '', k < 2 ? nos[k + 1] : '', target];
  PH.forEach((p, n) => { text = text.split(p).join(values[n]); });
  return text;
}
