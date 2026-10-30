// Gate checks (pure): the comparisons the scripted steps of the gate run on boxes the DOM adapter measured. Kept apart from the shell so
// they can be tested with plain numbers. A step asks the adapter for boxes, these functions say what is wrong with them.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const GATE_TOKEN_MAX = 40; // a table cell this short with no space is one word: it must stay on one line
export const GATE_OVERLAP_RATIO = 0.1; // overlap depth, as a share of the smaller box height, beyond which two text boxes collide

export const GATE_ALLOW_CONTEXT_MIN = 4; // characters of context a listed snippet needs besides the token itself
export const GATE_BAD_TEXT = /undefined|NaN|\[object/g; // the tokens the browser gate scans the page text for (selftest.js)

/**
 * Snippets of the bundle's own data (names, prose, labels: everything but the interface tables) that contain a token the gate scans for, so
 * the gate does not call the model's own words a rendering fault. Per occurrence four snippets: the token with 12 characters on both sides, on
 * the left only, on the right only, and with 4 on both sides (a name cut short by an ellipsis or wrapped in markup still matches one of them).
 * A snippet with less than GATE_ALLOW_CONTEXT_MIN characters of context beside the token is dropped.
 */
export function gateTextAllow(data, limit = 120, visitLimit = 400000) {
  const out = new Set(); let seen = 0;
  const visit = (v) => {
    if (out.size >= limit * 4 || seen++ > visitLimit) return;
    if (typeof v === 'string') {
      const re = new RegExp(GATE_BAD_TEXT.source, 'g'); let m = re.exec(v);
      while (m && out.size < limit * 4) {
        const at = m.index; const end = at + m[0].length;
        for (const [a, b] of [[12, 12], [12, 0], [0, 12], [4, 4]]) {
          const snip = v.slice(Math.max(0, at - a), end + b);
          if (snip.length >= m[0].length + GATE_ALLOW_CONTEXT_MIN) out.add(snip); // a bare token would exempt every use of the word on the page
        }
        m = re.exec(v);
      }
    } else if (Array.isArray(v)) { for (const x of v) visit(x); }
    else if (v && typeof v === 'object') { for (const k of Object.keys(v)) { visit(k); visit(v[k]); } }
  };
  if (data && typeof data === 'object' && !Array.isArray(data)) { for (const k of Object.keys(data)) { if (k !== 'i18n') visit(data[k]); } }
  return Array.from(out);
}

/** Pairs of boxes ({left, top, right, bottom}) that overlap in both directions by more than ratio * the smaller height (and 0.2 px). */
export function gateOverlaps(boxes, ratio = GATE_OVERLAP_RATIO) {
  const hits = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]; const b = boxes[j];
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      const min = Math.min(a.bottom - a.top, b.bottom - b.top);
      if (w > 0.2 && h > Math.max(0.2, ratio * min)) hits.push([a, b]);
    }
  }
  return hits;
}
