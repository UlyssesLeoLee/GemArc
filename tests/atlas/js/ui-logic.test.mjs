// Pure-logic tests of the UI modules that need no page and no Python: interface text lookup, preferences and responsive rules, routing and
// the tab registry, shell state transitions, the error boundary and the Markdown reader. Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as I18N from '../../../src/gemarc_mcp/atlas/viewer/src/ui/i18n.js';
import * as PREFS from '../../../src/gemarc_mcp/atlas/viewer/src/ui/prefs.js';
import * as NAV from '../../../src/gemarc_mcp/atlas/viewer/src/ui/nav.js';
import * as SS from '../../../src/gemarc_mcp/atlas/viewer/src/ui/shell-state.js';
import * as BND from '../../../src/gemarc_mcp/atlas/viewer/src/ui/boundary.js';
import * as MD from '../../../src/gemarc_mcp/atlas/viewer/src/ui/markdown.js';
import * as MV from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import * as GC from '../../../src/gemarc_mcp/atlas/viewer/src/ui/gate-checks.js';
import { makeModel, PROFILE } from './view-helpers.mjs';

// ---------------------------------------------------------------- i18n runtime
test('i18n: placeholders are filled, unknown names stay as written, values are converted', () => {
  assert.equal(I18N.i18nFill('a {n} b {m}', { n: 3 }), 'a 3 b {m}');
  assert.equal(I18N.i18nFill('x', null), 'x');
  assert.equal(I18N.i18nFill(undefined, { n: 1 }), '');
  assert.equal(I18N.i18nFill('{n}', { n: 0 }), '0');
  assert.equal(I18N.i18nFill('{__proto__} {toString}', {}), '{__proto__} {toString}');
});

test('i18n: a missing key returns the key, is reported once, never yields undefined, and falls back to the fallback language first', () => {
  const sink = [];
  const i = I18N.makeI18n({ 'ja-JP': { a: 'JA' }, 'en-US': { a: 'EN', b: 'only en {n}' } }, 'ja-JP', (r) => sink.push(r));
  assert.equal(i.t('a'), 'JA');
  assert.equal(i.t('b', { n: 2 }), 'only en 2');
  assert.equal(i.t('c.d'), 'c.d'); assert.equal(i.t('c.d'), 'c.d');
  assert.equal(i.t(undefined), ''); assert.equal(i.t(null), ''); assert.equal(i.t(5), '');
  assert.equal(sink.length, 1); assert.equal(sink[0].where, 'i18n');
  assert.equal(I18N.makeI18n(null, 'xx').t('k'), 'k');
  assert.equal(i.t('constructor'), 'constructor');
});

test('i18n: language choice (stored, model, browser, then fallback) and vocabulary labels with fallbacks', () => {
  const have = ['zh-CN', 'ja-JP', 'en-US'];
  assert.equal(I18N.i18nPickLang(have, ['ja-JP', 'zh-CN']), 'ja-JP');
  assert.equal(I18N.i18nPickLang(have, [null, 'zh']), 'zh-CN');
  assert.equal(I18N.i18nPickLang(have, ['de-DE']), 'en-US');
  assert.equal(I18N.i18nPickLang(['fr-FR'], []), 'fr-FR');
  assert.equal(I18N.i18nPickLang(null, []), 'en-US');
  const e = { id: 'K', label: { 'en-US': 'Kind', 'ja-JP': '' } };
  assert.equal(I18N.i18nLabel(e, 'zh-CN'), 'Kind'); assert.equal(I18N.i18nLabel(e, 'ja-JP'), 'Kind');
  assert.equal(I18N.i18nLabel({ id: 'Z' }, 'en-US'), 'Z'); assert.equal(I18N.i18nLabel(null, 'en-US', 'fallback'), 'fallback');
});

// ---------------------------------------------------------------- preferences and responsive layout
test('prefs: the storage key is per project and model; a damaged or foreign record gives complete valid defaults', () => {
  assert.equal(PREFS.prefKeyOf({ projectId: 'p1', modelSha8: 'abcd1234' }), 'gemarc-atlas:p1:abcd1234');
  assert.equal(PREFS.prefKeyOf({ prefKey: 'gemarc-atlas:x:y' }), 'gemarc-atlas:x:y');
  assert.equal(PREFS.prefKeyOf(null), 'gemarc-atlas:project:00000000');
  const ctx = { levels: ['REQ', 'BD', 'DD'], languages: ['en-US', 'ja-JP'], viewportWidth: 1400 };
  const d = PREFS.prefsParse(null, ctx);
  assert.equal(d.level, 'BD'); assert.equal(d.theme, 'auto'); assert.equal(d.navWidth, 280); assert.equal(d.inspectorWidth, 380); assert.equal(d.lang, null);
  for (const raw of ['', '{', 'null', '[]', '"x"', '12', 'true', '{"level":5,"theme":"neon","navWidth":"wide","navOpen":"yes","sub":[1],"lang":"de-DE"}']) assert.deepEqual(PREFS.prefsParse(raw, ctx), d, raw);
  const ok = PREFS.prefsParse(JSON.stringify({ level: 'DD', theme: 'dark', lang: 'ja-JP', navWidth: 9999, inspectorWidth: 10, navOpen: false, inspectorOpen: false, sub: { nav: 'nav', big: 'x'.repeat(100), n: 3 } }), ctx);
  assert.equal(ok.level, 'DD'); assert.equal(ok.theme, 'dark'); assert.equal(ok.lang, 'ja-JP'); assert.equal(ok.navOpen, false);
  assert.equal(ok.navWidth, Math.round(1400 * 0.34)); assert.equal(ok.inspectorWidth, 280);
  assert.deepEqual(ok.sub.nav, 'nav'); assert.equal(ok.sub.big.length, 40); assert.ok(!('n' in ok.sub));
  assert.deepEqual(PREFS.prefsParse(PREFS.prefsSerialize(ok), ctx), ok);
});

test('prefs: overlay mode below 1100, a wrapping header below 1500, columns leave the stage its minimum', () => {
  assert.equal(PREFS.layoutKindFor(1099), 'overlay'); assert.equal(PREFS.layoutKindFor(1100), 'docked'); assert.equal(PREFS.layoutKindFor(1024), 'overlay');
  assert.equal(PREFS.headerWraps(1499), true); assert.equal(PREFS.headerWraps(1500), false);
  const open = { navOpen: true, inspectorOpen: true, navWidth: 280, inspectorWidth: 380 };
  assert.deepEqual(PREFS.layoutColumns(open, 1024), { nav: 0, inspector: 0 });
  const c = PREFS.layoutColumns(open, 1280);
  assert.ok(c.nav >= 200 && c.inspector >= 280 && c.nav + c.inspector + 360 + 40 <= 1280 + 280, JSON.stringify(c));
  assert.deepEqual(PREFS.layoutColumns({ ...open, navOpen: false, inspectorOpen: false }, 1600), { nav: 0, inspector: 0 });
  assert.equal(PREFS.prefsPanelWidth('navWidth', NaN, 1600), 280); assert.equal(PREFS.prefsPanelWidth('nope', 5, 1600), 0);
});

// ---------------------------------------------------------------- routing and the registry
test('nav: routes round-trip, unknown parts are dropped, hostile values never throw', () => {
  const r = NAV.navParseRoute('#/2D?level=BD&sel=node%3Aa.b%3Ac&evil=1&x', ['1D', '2D']);
  assert.deepEqual(r, { tab: '2D', params: { level: 'BD', sel: 'node:a.b:c' } });
  assert.equal(NAV.navParseRoute('#/9D', ['1D']).tab, null); assert.equal(NAV.navParseRoute(undefined).tab, null);
  assert.deepEqual(NAV.navParseRoute('#/1D?level=%E0%A4%A', ['1D']).params, {});
  assert.equal(NAV.navBuildRoute('1D', { level: 'REQ', sel: 'edge:x:y', req: '', evil: 'z' }), '#/1D?level=REQ&sel=edge%3Ax%3Ay');
  assert.deepEqual(NAV.navParseSel('node:a:b'), { type: 'node', id: 'a:b' }); assert.equal(NAV.navParseSel('node:'), null); assert.equal(NAV.navParseSel(':x'), null); assert.equal(NAV.navParseSel(5), null);
  assert.equal(NAV.navSelText({ type: 'case', id: 'c1' }), 'case:c1'); assert.equal(NAV.navSelText(null), '');
});

test('nav: the registry replaces a definition with the same id, orders by the tab order, validates without throwing', () => {
  const reg = NAV.navMakeRegistry();
  const def = (id, extra = {}) => ({ id, labelKey: 'tab.' + id, render() {}, ...extra });
  assert.deepEqual(reg.registerTab(def('QA')), { ok: true }); reg.registerTab(def('3D')); reg.registerTab(def('1D')); reg.registerTab(def('zz', { order: 50 }));
  assert.deepEqual(reg.tabIds(), ['1D', '3D', 'QA', 'zz']);
  const first = reg.getTab('3D'); reg.registerTab(def('3D', { questionKey: 'ask.3d' }));
  assert.notEqual(reg.getTab('3D'), first); assert.equal(reg.getTab('3D').questionKey, 'ask.3d'); assert.equal(reg.tabIds().length, 4);
  for (const bad of [null, {}, { id: 'x' }, { id: 'x', labelKey: 'k' }, { id: '', labelKey: 'k', render() {} }, 'str']) assert.equal(reg.registerTab(bad).ok, false);
  reg.registerSideTab({ id: 'chain', labelKey: 'k', order: 3, tabs: ['5D'], render() {} }); reg.registerSideTab({ id: 'all', labelKey: 'k', order: 1, render() {} });
  assert.deepEqual(reg.sideTabs('5D').map((x) => x.id), ['all', 'chain']); assert.deepEqual(reg.sideTabs('2D').map((x) => x.id), ['all']);
  assert.equal(reg.registerSideTab({}).ok, false);
});

// ---------------------------------------------------------------- shell state
test('shell state: route over preferences over defaults, stale selections dropped, side areas and Esc behave by layout', () => {
  const model = makeModel({ seed: 3, perRoom: 2 });
  const ctx = MV.viewContext({ model, profile: PROFILE, meta: { languages: ['en-US'] } });
  const prefs = PREFS.prefsParse(null, { levels: ctx.levels, languages: ['en-US'], viewportWidth: 1400 });
  const tabs = ['1D', '2D', 'QA'];
  const node = model.nodes[0].id;
  const st = SS.shellInitial(ctx, prefs, NAV.navParseRoute(`#/QA?level=REQ&sel=node:${node}`, tabs), tabs, 'en-US');
  assert.equal(st.tab, 'QA'); assert.equal(st.level, 'REQ'); assert.deepEqual(st.sel, { type: 'node', id: node }); assert.equal(st.doc.level, 'REQ');
  const stale = SS.shellInitial(ctx, prefs, NAV.navParseRoute('#/1D?sel=node:no-such', tabs), tabs, 'en-US');
  assert.equal(stale.sel, null); assert.equal(stale.tab, '1D'); assert.equal(stale.level, prefs.level);
  assert.equal(SS.shellInitial(ctx, prefs, { tab: null, params: {} }, tabs, 'en-US').tab, '2D');
  assert.equal(SS.shellInitial(ctx, prefs, null, ['QA'], 'en-US').tab, 'QA');
  // side areas
  assert.equal(SS.shellTogglePanel(st, 'nav', 'docked').navOpen, !st.navOpen);
  const ov = SS.shellTogglePanel(st, 'inspector', 'overlay'); assert.equal(ov.overlay, 'inspector'); assert.equal(SS.shellTogglePanel(ov, 'nav', 'overlay').overlay, 'nav'); assert.equal(SS.shellTogglePanel(ov, 'inspector', 'overlay').overlay, null);
  assert.equal(SS.shellTogglePanel(st, 'x', 'docked'), st);
  // Esc closes one layer per press: search, then the overlay, then the selection
  let s = { ...st, searchOpen: true, query: 'q', overlay: 'nav' };
  s = SS.shellEscape(s); assert.equal(s.searchOpen, false); assert.equal(s.overlay, 'nav'); s = SS.shellEscape(s); assert.equal(s.overlay, null); assert.ok(s.sel);
  s = SS.shellEscape(s); assert.equal(s.sel, null); assert.equal(SS.shellEscape(s), s);
  // level and tab transitions
  assert.equal(SS.shellSetLevel(st, 'DD').doc.level, 'DD'); assert.equal(SS.shellSetTab({ ...st, overlay: 'nav' }, '1D').overlay, null);
  assert.deepEqual(SS.shellRouteParams({ ...st, tab: '1D', doc: { ...st.doc, mode: 'requirement', reqId: 'R-1' } }), { level: 'REQ', sel: `node:${node}`, req: 'R-1' });
});

test('shell state: keys are data; nothing fires while typing; hits resolve to a tab, a selection or a requirement', () => {
  const ids = ['1D', '2D', '3D'];
  assert.deepEqual(SS.shellKeyAction('2', {}, false, ids), { type: 'tab', id: '2D' }); assert.equal(SS.shellKeyAction('9', {}, false, ids), null); assert.equal(SS.shellKeyAction('2', {}, true, ids), null);
  assert.deepEqual(SS.shellKeyAction('Escape', {}, true, ids), { type: 'escape' }); assert.deepEqual(SS.shellKeyAction('k', { ctrl: true }, true, ids), { type: 'search' });
  assert.deepEqual(SS.shellKeyAction('/', {}, false, ids), { type: 'search' }); assert.deepEqual(SS.shellKeyAction('[', {}, false, ids), { type: 'panel', name: 'nav' }); assert.deepEqual(SS.shellKeyAction(']', {}, false, ids), { type: 'panel', name: 'inspector' });
  assert.equal(SS.shellKeyAction('a', {}, false, ids), null); assert.equal(SS.shellKeyAction('2', { alt: true }, false, ids), null); assert.equal(SS.shellKeyAction('1', {}, false, null), null);
  const ctx = { levels: ['REQ', 'BD', 'DD'] };
  assert.deepEqual(SS.shellApplyHit(ctx, { kind: 'requirement', id: 'R-1', tab: '1D', target: { tab: '1D', level: 'REQ', node: 'n1' } }, ids), { tab: '1D', level: 'REQ', requirementId: 'R-1' });
  assert.deepEqual(SS.shellApplyHit(ctx, { kind: 'node', id: 'n1', tab: '3D', target: { tab: '3D', node: 'n1', level: 'zz' } }, ['1D', '2D']), { tab: '2D', sel: { type: 'node', id: 'n1' } });
  assert.deepEqual(SS.shellApplyHit(ctx, { kind: 'diagram', target: { tab: '2D', diagram: 'd1' } }, ids), { tab: '2D', unitId: 'd1' });
  assert.deepEqual(SS.shellApplyHit(ctx, null, ids), {});
});

// ---------------------------------------------------------------- error boundary
test('boundary: errors are recorded not thrown, the frame counter advances in a finally, the sink is only pushed to', () => {
  const sink = []; const b = BND.makeBoundary(sink);
  assert.equal(b.guardRun('w', () => 5, 0), 5); assert.equal(b.guardRun('w', () => { throw new Error('x'); }, 'fb'), 'fb');
  assert.equal(sink.length, 1); assert.equal(sink[0].where, 'w'); assert.equal(sink[0].message, 'x'); assert.ok(sink[0].stack.length > 0);
  const counter = { n: 0 };
  assert.deepEqual(b.frameRun(counter, 'f', () => {}), { ok: true, error: null }); assert.equal(counter.n, 1);
  const r = b.frameRun(counter, 'f', () => { throw new RangeError('boom'); }); assert.equal(r.ok, false); assert.equal(counter.n, 2); assert.equal(r.error.message, 'boom');
  b.frameRun(counter, 'f', () => { throw 'a string'; }); b.frameRun(counter, 'f', () => { throw { get message() { throw new Error('hostile'); } }; });
  assert.equal(counter.n, 4); assert.equal(sink.length, 4); assert.equal(sink[3].message, 'unprintable'); assert.equal(b.count(), 4);
  assert.equal(BND.boundaryRecord('w', { message: 'y'.repeat(900) }).message.length, 300);
  assert.equal(BND.boundaryRecord('w', null).message, 'null');
});

// ---------------------------------------------------------------- markdown reader
test('markdown: headings, tables with escaped pipes, links, markers, code spans, entities, escapes, lists, quotes', () => {
  const text = ['## 3. Title', '', '> Elaborates section 3', '', 'Line one', 'Line two', '', '- item `a|b`', '- item 2', '', '| Id | Name |', '|---|---|', '| `x\\|y` | A \\| B &amp; [NOT-AUTHORED:slot] |', '| [go](dg:comp-1) | [N/A] |', '', '```', 'raw | text', '```', 'plain \\[escaped\\] 1\\. x &lt;b&gt;'].join('\n');
  const b = MD.mdParse(text);
  assert.deepEqual(b.map((x) => x.t), ['h', 'quote', 'p', 'ul', 'table', 'code', 'p']);
  assert.equal(b[0].n, 2); assert.equal(MD.mdPlain(b[0].inl), '3. Title');
  assert.equal(b[2].lines.length, 2); assert.equal(b[3].items.length, 2); assert.deepEqual(b[3].items[0][1], { k: 'c', s: 'a|b' });
  assert.equal(b[4].head.length, 2); assert.equal(b[4].rows.length, 2);
  assert.deepEqual(b[4].rows[0][0], [{ k: 'c', s: 'x|y' }]);
  assert.equal(MD.mdPlain(b[4].rows[0][1]), 'A | B & [NOT-AUTHORED:slot]');
  assert.equal(b[4].rows[0][1].find((x) => x.k === 'm').block, true); assert.equal(b[4].rows[1][1][0].block, false);
  assert.equal(MD.mdDiagramId(b[4].rows[1][0][0].href), 'comp-1'); assert.equal(MD.mdDiagramId('http://x'), null); assert.equal(MD.mdDiagramId('dg:'), null);
  assert.equal(b[5].text, 'raw | text');
  assert.equal(MD.mdPlain(b[6].lines[0]), 'plain [escaped] 1. x <b>');
  assert.equal(MD.mdCountMarkers('[NOT-AUTHORED:a] x [NOT-AUTHORED:b] [N/A]'), 2);
});

test('markdown: author text never becomes structure (raw angle brackets stay text), and hostile input is total and fast', () => {
  const b = MD.mdParse('<script>alert(1)</script> [x](javascript:alert(1)) [[y]](dg:a)');
  const kinds = b[0].lines[0].map((x) => x.k);
  assert.ok(!JSON.stringify(b).includes('"k":"script"')); assert.ok(kinds.includes('a')); // a link record exists; the renderer shows only dg: targets as buttons
  assert.equal(MD.mdDiagramId(b[0].lines[0].find((x) => x.k === 'a').href), null);
  const seeds = ['`', '``', '[', '[[', '](', '|', '\\', '| a |\n|---|', '#', '>', '- ', '```', '\u0000', '\ud800', 'x'.repeat(30000), '['.repeat(30000), '|'.repeat(5000)];
  const t0 = Date.now();
  for (const s of seeds) assert.doesNotThrow(() => MD.mdParse(s), JSON.stringify(s.slice(0, 20)));
  let a = 12345; const r = () => { a = (a * 1103515245 + 12345) & 0x7fffffff; return a; };
  const alphabet = ['`', '[', ']', '(', ')', '|', '\\', '\n', '#', '>', '-', ' ', 'a', ':', '&', '*', '~'];
  for (let i = 0; i < 400; i++) { let s = ''; for (let k = 0; k < 120; k++) s += alphabet[r() % alphabet.length]; assert.doesNotThrow(() => MD.mdParse(s)); }
  assert.ok(Date.now() - t0 < 5000, 'hostile input must not be quadratic');
  assert.deepEqual(MD.mdParse(''), []); assert.deepEqual(MD.mdParse(null), [{ t: 'p', lines: [[{ k: 't', s: 'null' }]] }]);
});

test('gate checks: text boxes that share a band are reported, stacked boxes and a hairline touch are not', () => {
  const box = (left, top, right, bottom, text = 'x') => ({ left, top, right, bottom, text });
  assert.equal(GC.gateOverlaps([box(0, 0, 80, 12), box(10, 9, 90, 21)]).length, 1, 'the old floor / room placement: 3 of 12 px deep');
  assert.equal(GC.gateOverlaps([box(0, 0, 80, 12), box(10, 17, 90, 29)]).length, 0, 'one line apart');
  assert.equal(GC.gateOverlaps([box(0, 0, 80, 12), box(80, 0, 120, 12)]).length, 0, 'side by side');
  assert.equal(GC.gateOverlaps([box(0, 0, 80, 12), box(10, 11.9, 90, 24)]).length, 0, 'a shared hairline');
  assert.equal(GC.gateOverlaps([]).length, 0);
  assert.equal(GC.gateOverlaps([box(0, 0, 10, 10), box(2, 2, 8, 8), box(3, 3, 5, 5)]).length, 3);
});
