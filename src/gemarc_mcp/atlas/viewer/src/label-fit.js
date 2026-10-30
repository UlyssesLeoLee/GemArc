// Canvas label sizing (pure apart from ctx.measureText / ctx.font): shrink the font, then shorten with an ellipsis, so a label never leaves its box,
// and nudge it vertically so two labels do not sit on top of each other. No DOM.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const FONT_FAMILY = 'system-ui, "Noto Sans", "Noto Sans CJK SC", "Noto Sans CJK JP", "PingFang SC", "Microsoft YaHei", sans-serif';
const WIDE_FROM = 11904; // first code point of the CJK radicals block: everything from here on is drawn one em wide
export const ELLIPSIS = '\u2026';
export const LABEL_FONT = (size, family = FONT_FAMILY) => `${size}px ${family}`;
const LABEL_PAD = 12; // 6px each side

// Code-point slice, so a label is never cut inside a surrogate pair.
const prefix = (chars, n) => chars.slice(0, n).join('');

/** Fits `text` into `maxWidth`: the largest of `sizes` that fits, else the smallest size with an ellipsis. `family` is the CSS font stack. */
export function shrinkText(ctx, text, maxWidth, sizes = [12, 11, 10, 9], family = FONT_FAMILY) {
  const str = String(text ?? '');
  let size = sizes[0];
  for (const s of sizes) { size = s; ctx.font = LABEL_FONT(s, family); if (ctx.measureText(str).width + LABEL_PAD <= maxWidth) return { text: str, size, width: ctx.measureText(str).width + LABEL_PAD }; }
  const chars = [...str];
  let lo = 0; let hi = chars.length;
  while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (ctx.measureText(`${prefix(chars, mid)}${ELLIPSIS}`).width + LABEL_PAD <= maxWidth) lo = mid; else hi = mid - 1; }
  const out = `${prefix(chars, lo)}${ELLIPSIS}`;
  return { text: out, size, width: Math.min(maxWidth, ctx.measureText(out).width + LABEL_PAD) };
}

const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y - 10 < b.y + 10 && b.y - 10 < a.y + 10;

// placed: rectangles already drawn this frame ({x, y, w}); the chosen one is appended.
export function placeLabel(ctx, text, cx, y0, w, h, placed, labelMax = 260, family = FONT_FAMILY) {
  const fit = shrinkText(ctx, text, Math.max(40, Math.min(w - 8, labelMax)), [12, 11, 10, 9], family);
  const x = Math.max(4, Math.min(w - fit.width - 4, cx - fit.width / 2));
  let y = Math.max(10, Math.min(h - 10, y0));
  for (let i = 0; i < 8; i++) {
    const box = { x, y, w: fit.width };
    if (!placed.some((q) => hit(box, q))) break;
    const step = 22 * (Math.floor(i / 2) + 1) * (i % 2 ? 1 : -1);
    y = Math.max(10, Math.min(h - 10, y0 + step));
  }
  const box = { x, y, w: fit.width, size: fit.size, text: fit.text };
  placed.push(box);
  return box;
}

// SVG text has no canvas context to measure with: estimate the advance (full-width glyphs = 1em, ASCII about 0.56 of the size) and shorten with an ellipsis to limitPx.
export const estimateTextWidth = (t, size) => [...String(t ?? '')].reduce((w, c) => w + (c.codePointAt(0) > WIDE_FROM ? size : size * 0.56), 0);
export function truncateText(text, limitPx, size) {
  const t = String(text ?? '');
  if (estimateTextWidth(t, size) <= limitPx) return t;
  let out = '';
  for (const c of t) { if (estimateTextWidth(`${out}${c}${ELLIPSIS}`, size) > limitPx) break; out += c; }
  return `${out}${ELLIPSIS}`;
}
