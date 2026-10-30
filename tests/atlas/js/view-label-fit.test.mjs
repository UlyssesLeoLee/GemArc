import test from 'node:test';
import assert from 'node:assert/strict';
import { shrinkText, placeLabel, truncateText, estimateTextWidth, LABEL_FONT, FONT_FAMILY, ELLIPSIS } from '../../../src/gemarc_mcp/atlas/viewer/src/label-fit.js';

// one character = font size px, so widths are exact
const mkCtx = () => ({ font: '12px x', measureText(t) { const s = Number(/^(\d+)px/.exec(this.font)[1]); return { width: [...t].length * s }; } });

test('a string that fits stays as it is; otherwise the font shrinks first, then the text is ellipsized', () => {
  const ctx = mkCtx();
  assert.deepEqual({ ...shrinkText(ctx, 'abcd', 200) }, { text: 'abcd', size: 12, width: 60 });
  const shrunk = shrinkText(ctx, 'a'.repeat(15), 175); assert.equal(shrunk.text, 'a'.repeat(15)); assert.ok(shrunk.size < 12);
  const cut = shrinkText(ctx, 'a'.repeat(40), 100); assert.ok(cut.text.endsWith(ELLIPSIS) && cut.width <= 100 && cut.size === 9);
});

test('the font stack is a parameter', () => {
  const ctx = mkCtx(); const seen = [];
  const spy = { get font() { return this._f; }, set font(v) { this._f = v; seen.push(v); }, measureText: (t) => ({ width: t.length * 5 }) };
  shrinkText(spy, 'abc', 500, [12], 'serif');
  assert.equal(seen[0], '12px serif');
  assert.equal(LABEL_FONT(10), `10px ${FONT_FAMILY}`);
  void ctx;
});

test('astral characters are never cut in half', () => {
  const ctx = { font: '12px x', measureText(t) { return { width: t.length * Number(/^(\d+)px/.exec(this.font)[1]) }; } }; // one unit of width per UTF-16 unit
  const text = '\u{1F600}'.repeat(30);
  for (let w = 40; w <= 130; w += 7) {
    const r = shrinkText(ctx, text, w);
    assert.ok(r.text.endsWith(ELLIPSIS));
    assert.ok(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(r.text) && !/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(r.text), `width ${w}`);
  }
  assert.ok(!/[\uD800-\uDBFF]$/.test(truncateText(text, 30, 12).slice(0, -1)));
});

test('labels stay inside their box and overlapping ones are nudged vertically', () => {
  const ctx = mkCtx(); const placed = [];
  const a = placeLabel(ctx, 'lbl-a', 5, 30, 300, 200, placed);
  const b = placeLabel(ctx, 'lbl-b', 5, 30, 300, 200, placed);
  assert.ok(a.x >= 4 && a.x + a.w <= 296);
  assert.notEqual(a.y, b.y); assert.equal(placed.length, 2);
  const wide = placeLabel(ctx, 'x'.repeat(100), 150, 100, 120, 200, []); assert.ok(wide.x >= 4 && wide.x + wide.w <= 116);
});

test('SVG text is clipped by estimated width: full-width glyphs count one em, ASCII about 0.56 em', () => {
  assert.equal(truncateText('abc', 100, 12), 'abc');
  const cut = truncateText('x'.repeat(50), 60, 12); assert.ok(cut.endsWith(ELLIPSIS) && estimateTextWidth(cut, 12) <= 60);
  const wide = truncateText('中'.repeat(20), 60, 12); assert.ok(wide.endsWith(ELLIPSIS) && estimateTextWidth(wide, 12) <= 60);
  assert.equal(truncateText('', 10, 12), ''); assert.equal(truncateText(undefined, 10, 12), ''); assert.equal(estimateTextWidth(null, 12), 0);
  assert.equal(estimateTextWidth('中', 10), 10); assert.ok(Math.abs(estimateTextWidth('a', 10) - 5.6) < 1e-9);
});
