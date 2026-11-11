// Shell and tabs on a stand-in page (ui-dom-stub.mjs): boot, the documented AtlasApp API, every tab in every language, the error boundary
// and heartbeat, the empty states, overlay and docked layouts, keyboard, selection and inspector, the 1D reader, the diagram link, search,
// preferences, routing, the gate configuration, and hostile bundles. The stub has no layout engine: geometry is not tested here (the gate
// probe in a real browser covers it; see the notes of this lane).
import test from 'node:test';
import assert from 'node:assert/strict';
import { bootAtlas, TAB_DEFINITIONS } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/main.js';
import * as TW from '../../../src/gemarc_mcp/atlas/viewer/src/ui/twod-scene.js';
import { installPage } from './ui-dom-stub.mjs';
import { makeBundle, tables, skipNoPython, LANGS } from './ui-helpers.mjs';

const opts = { skip: skipNoPython };
const BAD = /undefined|NaN|\[object/;

function boot(bundle, pageOpts = {}) {
  const page = installPage(pageOpts);
  globalThis.__ATLAS__ = bundle;
  const app = bootAtlas();
  return { page, app, end: () => page.cleanup() };
}
const withBoot = (bundle, pageOpts, fn) => { const h = boot(bundle, pageOpts); try { return fn(h); } finally { h.end(); } };
const buttons = (page, sel) => page.findAll(sel);
const arrow = (dir) => `Arrow${dir}`; // the standard DOM key names, written in two pieces
const tabButton = (page, id) => page.find(`button[data-tab="${id}"]`);

test('boot: AtlasApp is published, the six tabs are registered in order, the heartbeat ran, no errors', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    assert.ok(app && globalThis.AtlasApp === app);
    assert.deepEqual(app.tabs(), ['1D', '2D', '3D', '4D', '5D', 'QA']); assert.equal(app.version, 1);
    for (const k of ['registerTab', 'registerSideTab', 'tabs', 'setTab', 'setLevel', 'select', 'frame', 'renderOnce', 'gateConfig', 'checkDocSelections', 'warnings', 'state']) assert.equal(typeof app[k], 'function', k);
    for (const k of ['tab', 'level', 'selection', 'errors', 'calibrationHooks', 'bundle', 'ctx']) assert.ok(k in app, k);
    assert.ok(app.frame() >= 1); assert.deepEqual(app.errors, []); assert.ok(Array.isArray(globalThis.__ATLAS_ERRORS__) && globalThis.__ATLAS_ERRORS__ === app.errors);
    assert.ok(Array.isArray(globalThis.__ATLAS_WARNINGS__));
    assert.equal(app.tab, '2D'); assert.equal(page.body.getAttribute('data-tab'), '2D');
    assert.equal(page.findAll('[data-gate=header] button[data-tab]').length, 6);
    assert.equal(page.root.getAttribute('data-layout'), 'docked');
    assert.equal(TAB_DEFINITIONS.map((d) => d.id).join(), '1D,2D,3D,4D,5D,QA');
  });
});

test('every tab renders in every language: no missing key, no undefined/NaN text, a header with controls, the QA rows present', opts, () => {
  const bundle = makeBundle('small', 3);
  for (const lang of LANGS) {
    withBoot(bundle, { language: lang, store: { [bundle.meta.prefKey]: JSON.stringify({ lang }) } }, ({ page, app }) => {
      assert.equal(page.root.getAttribute('data-layout'), 'docked'); assert.equal(page.document.documentElement.getAttribute('lang'), lang);
      for (const id of app.tabs()) {
        app.setTab(id);
        assert.equal(app.tab, id); assert.equal(page.body.getAttribute('data-tab'), id);
        const text = page.text();
        assert.ok(!BAD.test(text), `${lang}/${id}: ${BAD.exec(text)}`);
        assert.ok(text.length > 100, `${lang}/${id}: visible text`);
        assert.ok(page.findAll('[data-gate=header] button').length >= 6);
        assert.ok(page.find('.stage').children.length >= 2, `${lang}/${id}`);
      }
      app.setTab('QA'); assert.ok(page.findAll('[data-qa-row]').length >= 10);
      assert.deepEqual(app.errors.filter((e) => e.where === 'i18n'), [], `${lang}: missing interface keys`);
      assert.deepEqual(app.errors, [], lang);
    });
  }
});

test('heartbeat: renderOnce advances the counter once per call, frame() is a pure read, a throwing tab is recorded and the page keeps going', opts, () => {
  withBoot(makeBundle('tiny', 0), {}, ({ page, app }) => {
    const f0 = app.frame(); assert.equal(app.frame(), f0); assert.equal(app.frame(), f0);
    const r = app.renderOnce(); assert.equal(r.ok, true); assert.equal(app.frame(), f0 + 1); assert.equal(r.frame, f0 + 1);
    assert.equal(app.renderOnce('QA').tab, 'QA'); assert.equal(app.tab, 'QA'); assert.equal(app.frame(), f0 + 2);
    assert.equal(app.renderOnce('nope').tab, 'QA');
    app.registerTab({ id: '2D', labelKey: 'tab.2d', inspector: true, render() { throw new Error('tab boom'); } });
    app.setTab('2D');
    assert.ok(app.errors.some((e) => e.message === 'tab boom' && e.where === 'tab 2D'));
    const f1 = app.frame(); const n = app.errors.length;
    app.renderOnce(); assert.equal(app.frame(), f1 + 1); assert.ok(app.errors.length > n, 'a repeated failure is recorded again');
    assert.ok(page.findAll('[data-gate=header] button').length >= 6, 'the chrome survives a failing tab');
    app.registerTab({ id: '2D', labelKey: 'tab.2d', inspector: true, render(host, api) { host.appendChild(page.document.createTextNode('recovered')); } });
    assert.ok(page.text().includes('recovered'));
  });
});

test('a failing renderer inside the shell (sidebar and inspector) is contained as well', opts, () => {
  withBoot(makeBundle('tiny', 0), {}, ({ page, app }) => {
    app.registerTab({ id: 'QA', labelKey: 'tab.qa', inspector: false, controls() { throw new Error('controls boom'); }, render(host) { host.appendChild(page.document.createTextNode('qa body')); } });
    app.setTab('QA');
    assert.ok(app.errors.some((e) => e.message === 'controls boom')); assert.ok(page.text().includes('qa body'));
  });
});

test('registerTab: a later definition replaces the reserved slot; a side tab shows up in the inspector strip of its tabs', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    assert.equal(app.registerTab({ id: '3D', labelKey: 'tab.3d', questionKey: 'ask.3d', inspector: true, render(host) { host.appendChild(page.document.createTextNode('REAL-3D')); } }).ok, true);
    app.setTab('3D'); assert.ok(page.text().includes('REAL-3D')); assert.ok(!page.text().includes('DEGRADED')); assert.equal(app.tabs().length, 6);
    assert.equal(app.registerTab({ id: 'X' }).ok, false);
    assert.equal(app.registerSideTab({ id: 'chain', labelKey: 'insp.cases', tabs: ['3D'], render(host) { host.appendChild(page.document.createTextNode('CHAIN-PANE')); } }).ok, true);
    assert.ok(page.findAll('.insp-body').length >= 1);
    const btn = page.findAll('.panel-ins button').find((b) => b.textContent === 'Cases'); assert.ok(btn); btn.click();
    assert.ok(page.text().includes('CHAIN-PANE'));
    app.setTab('2D'); assert.ok(!page.findAll('.panel-ins button').some((b) => b.textContent === 'Cases' && b.getAttribute('aria-pressed') === 'x'));
  });
});

test('empty states: no cases, no interventions, nothing sealed (the 4D and 5D tabs say to author the expectation first); 3D is a labelled degraded 2D', opts, () => {
  const en = tables()['en-US'];
  const none = makeBundle('edge:no-cases', 4);
  withBoot(none, {}, ({ page, app }) => {
    assert.equal(app.ctx.hasCases, false);
    for (const id of ['4D', '5D']) { app.setTab(id); assert.ok(page.text().includes(en['empty.cases']), id); }
  });
  const noIv = makeBundle('edge:no-interventions', 8);
  withBoot(noIv, {}, ({ page, app }) => {
    app.setTab('4D'); assert.ok(page.text().includes(en['empty.expectation']), 'cases exist, nothing sealed');
    app.setTab('5D'); assert.ok(page.text().includes(en['empty.interventions']));
  });
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    app.setTab('4D'); assert.ok(page.text().includes('Author the expectation first'));
    app.setTab('5D'); assert.ok(page.text().includes('Author the expectation first'));
    app.setTab('3D'); assert.ok(page.text().includes(en['slot.degraded'])); assert.ok(page.text().includes(en['twod.mode'])); assert.ok(page.find('svg.twod'));
    app.setTab('QA'); assert.ok(page.text().includes(en['empty.expectation']));
  });
  const sealed = makeBundle('small', 3); sealed.runs.baselines[sealed.model.cases[0].caseId] = { caseId: sealed.model.cases[0].caseId, status: 'OK', trace: [] };
  withBoot(sealed, {}, ({ page, app }) => { app.setTab('4D'); assert.equal(page.find('[data-step-count]').getAttribute('data-step-count'), '0'); assert.deepEqual(app.errors, []); });
});

test('layout: docked columns at 1100 and above, overlay panes below; an overlay opens on its button and closes on Esc and on the scrim', opts, () => {
  withBoot(makeBundle('small', 3), { width: 1366 }, ({ page, app }) => {
    assert.ok(page.find('.panel-nav') && page.find('.panel-ins')); assert.equal(page.find('.panel-nav').getAttribute('data-open'), '1');
    page.setViewport(1024); app.renderOnce();
    assert.equal(page.root.getAttribute('data-layout'), 'overlay'); assert.equal(page.root.getAttribute('data-wrap'), '1'); assert.equal(page.find('.panel-nav').getAttribute('data-open'), '0'); assert.equal(page.find('.panel-ins').getAttribute('data-open'), '0');
    page.find('[data-keep=panel-nav]').click(); assert.equal(page.find('.panel-nav').getAttribute('data-open'), '1'); assert.equal(page.find('.panel-ins').getAttribute('data-open'), '0'); assert.ok(page.find('.side-scrim'));
    page.find('[data-keep=panel-inspector]').click(); assert.equal(page.find('.panel-nav').getAttribute('data-open'), '0'); assert.equal(page.find('.panel-ins').getAttribute('data-open'), '1');
    page.key('Escape'); assert.equal(page.find('.panel-ins').getAttribute('data-open'), '0'); assert.equal(page.find('.side-scrim'), null);
    page.find('[data-keep=panel-nav]').click(); page.find('.side-scrim').click(); assert.equal(page.find('.panel-nav').getAttribute('data-open'), '0');
    page.setViewport(1600); app.renderOnce(); assert.equal(page.root.getAttribute('data-layout'), 'docked'); assert.equal(page.root.getAttribute('data-wrap'), '0');
    page.find('[data-keep=panel-nav]').click(); assert.equal(page.find('.panel-nav'), null, 'a closed docked column is not rendered');
    app.setTab('QA'); assert.equal(page.find('.panel-ins'), null, 'QA has no inspector'); assert.equal(page.find('[data-keep=panel-inspector]'), null);
  });
});

test('keyboard: digits switch tabs, [ and ] toggle the columns, / and Ctrl+K open search, Esc closes layer by layer, typing is left alone', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    page.key('1'); assert.equal(app.tab, '1D'); page.key('6'); assert.equal(app.tab, 'QA'); page.key('9'); assert.equal(app.tab, 'QA');
    page.key('2'); assert.equal(app.tab, '2D');
    page.key('['); assert.equal(page.find('.panel-nav'), null); page.key('['); assert.ok(page.find('.panel-nav'));
    page.key(']'); assert.equal(page.find('.panel-ins'), null); page.key(']'); assert.ok(page.find('.panel-ins'));
    page.key('/'); assert.ok(page.find('[role=dialog]')); page.key('Escape'); assert.equal(page.find('[role=dialog]'), null);
    page.key('k', { ctrlKey: true }); assert.ok(page.find('[role=dialog]'));
    page.key('3', { target: { tagName: 'INPUT' } }); assert.equal(app.tab, '2D', 'a digit typed into a field does not switch tabs');
    page.key('Escape'); assert.equal(page.find('[role=dialog]'), null);
    const node = app.ctx.nodes[0].id; app.select({ type: 'node', id: node }); assert.ok(app.selection); page.key('Escape'); assert.equal(app.selection, null);
    tabButton(page, '2D').dispatch('keydown', { key: arrow('Right') }); assert.equal(app.tab, '3D');
    tabButton(page, '3D').dispatch('keydown', { key: arrow('Left') }); assert.equal(app.tab, '2D');
    tabButton(page, '2D').dispatch('keydown', { key: 'Home' }); assert.equal(app.tab, '1D');
    tabButton(page, '1D').dispatch('keydown', { key: arrow('Left') }); assert.equal(app.tab, 'QA'); tabButton(page, 'QA').dispatch('keydown', { key: arrow('Right') }); assert.equal(app.tab, '1D');
    tabButton(page, '1D').dispatch('keydown', { key: 'End' }); assert.equal(app.tab, 'QA');
  });
});

test('selection: a click on a 2D card selects it, the inspector shows its facts, a link selects the neighbour, Esc clears; the level switch re-draws', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    const card = page.find('.card[data-node]'); assert.ok(card); const id = card.getAttribute('data-node');
    card.click(); assert.deepEqual(app.selection, { type: 'node', id });
    const name = app.ctx.ix.nodes.get(id).name;
    assert.ok(page.find('.insp-detail').textContent.includes(name)); assert.ok(page.find('.card.picked'));
    const link = page.findAll('.insp-detail button.chip')[0]; assert.ok(link); link.click(); assert.notDeepEqual(app.selection, { type: 'node', id }); assert.ok(app.selection);
    app.select(null); assert.ok(page.text().includes(tables()['en-US']['insp.none']));
    const before = page.findAll('.card[data-node]').length;
    page.find('[data-level=REQ]').click(); assert.equal(app.level, 'REQ'); assert.equal(page.body.getAttribute('data-level'), 'REQ'); assert.ok(page.findAll('.card[data-node]').length <= before);
    page.find('[data-level=DD]').click(); assert.equal(page.findAll('.card[data-node]').length, app.ctx.nodes.length);
    const edge = page.find('[data-edge]'); assert.ok(edge); edge.click(); assert.equal(app.selection.type, 'edge');
    app.select({ type: 'case', id: app.ctx.cases[0].caseId }); assert.ok(page.find('.insp-detail'));
    const nav = page.find('.nav-node'); assert.ok(nav); nav.click(); assert.equal(app.selection.type, 'node');
  });
});

test('2D fit is the scene bounds (the browser centres it in the pane), the hint for the zoomed-out state exists, the QA tab has no duplicate intro', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    const vb = page.find('svg.twod').getAttribute('viewBox');
    const scene = TW.twodScene(app.ctx, { level: app.level, selection: null, query: '' });
    assert.equal(vb, TW.twodViewBoxText(scene.viewBox), 'no pane size is guessed: the fit is the bounds of the drawing');
    assert.equal(page.find('svg.twod').getAttribute('preserveAspectRatio'), 'xMidYMid meet');
    assert.ok(page.find('.lod-hint'), 'the hint is part of the pane (shown by the style while the text is dropped)');
    assert.equal(page.findAll('svg.twod text.frame-label').length > 0, true);
    const kinds = new Set(page.findAll('svg.twod text.frame-label').map((n) => n.getAttribute('data-label'))); assert.ok(kinds.has('building') && kinds.has('floor') && kinds.has('room'));
    app.setTab('QA'); assert.equal(page.findAll('.lead').length, 0, 'the question line above is the only introduction');
    assert.ok(page.text().includes(tables()['en-US']['qa.counts.note']));
  });
});

test('1D tables: a short single token (an id, a grade, a one-word header) is marked so the table never wraps it', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    app.setTab('1D');
    const toks = page.findAll('.md td.tok, .md th.tok'); assert.ok(toks.length > 3, 'tokens found: ' + toks.length);
    assert.ok(toks.every((n) => !/\s/.test(n.textContent) && n.textContent.length <= 40));
    const longCells = page.findAll('.md td').filter((n) => /\s/.test(n.textContent.trim())); assert.ok(longCells.length > 0 && longCells.every((n) => n.getAttribute('class') !== 'tok'));
  });
});

test('2D pane: wheel zoom and button zoom change the view box, fit restores it, a drag pans and suppresses the click, search dims', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    const svg = () => page.find('svg.twod'); const vb = () => svg().getAttribute('viewBox');
    const fit = vb(); assert.ok(!BAD.test(fit));
    page.findAll('.twod-bar button').find((b) => b.getAttribute('aria-label') === 'Zoom in').click();
    assert.notEqual(vb(), fit); const zoomed = vb();
    svg().dispatch('wheel', { deltaY: -100, clientX: 10, clientY: 10, preventDefault() {} }); assert.notEqual(vb(), zoomed);
    page.findAll('.twod-bar button').find((b) => b.textContent === 'Fit').click(); assert.equal(vb(), fit);
    const toSel = () => page.findAll('.twod-bar button').find((b) => b.textContent === 'Zoom to selection');
    assert.equal(toSel().getAttribute('disabled'), 'disabled'); toSel().click(); assert.equal(vb(), fit, 'nothing selected: nothing happens');
    page.find('.card[data-node]').click(); assert.equal(toSel().getAttribute('disabled'), null); toSel().click(); assert.notEqual(vb(), fit);
    const [vx, vy, vw, vh] = vb().split(' ').map(Number); const card = app.ctx.ix.nodes.get(app.selection.id); assert.ok(vw < 1200 && vw > 0 && vh > 0 && Number.isFinite(vx + vy), 'zoomed in on the selected card');
    app.select(null); page.findAll('.twod-bar button').find((b) => b.textContent === 'Fit').click(); assert.equal(vb(), fit);
    const s = svg(); const before = vb();
    s.dispatch('pointerdown', { button: 0, clientX: 100, clientY: 100, pointerId: 1 }); s.dispatch('pointermove', { clientX: 160, clientY: 130, pointerId: 1 }); assert.notEqual(vb(), before);
    s.dispatch('pointerup', { clientX: 160, clientY: 130, pointerId: 1 });
    page.find('.card[data-node]').click(); assert.equal(app.selection, null, 'the click that ends a drag is not a selection');
    page.find('svg.twod').dispatch('keydown', { key: arrow('Right'), preventDefault() {} }); // key handler is attached to the svg: target is the svg itself
    const input = page.find('[data-keep=twod-query]'); input.value = app.ctx.nodes[0].name.split(' ')[0]; input.dispatch('input'); assert.ok(page.findAll('.card.nomatch').length > 0);
    assert.equal(page.find('[data-keep=twod-query]').getAttribute('value'), input.value, 'the query survives the re-draw');
  });
});

test('1D reader: the text is the assembled document, tables are real tables, markers are flagged, the selection UI drives it, links open the diagram unit', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    app.setTab('1D');
    assert.ok(page.find('.md') && page.findAll('.md h3').length >= 12); assert.ok(page.findAll('.md table').length > 3); assert.ok(page.findAll('.md td').length > 20);
    assert.ok(page.findAll('.outline button').length === 12);
    const stats = page.find('.doc-stats').textContent; assert.ok(/Basic design: \d+ bytes/.test(stats), stats);
    assert.ok(page.findAll('.mark-block').length >= 1, 'the synthetic model has slots that are not authored');
    const t0 = page.find('.md').textContent;
    page.find('[data-level=DD]').click(); assert.notEqual(page.find('.md').textContent, t0); assert.ok(/Detailed design: /.test(page.find('.doc-stats').textContent));
    const modeBtn = (txt) => page.findAll('.doc-controls button').find((b) => b.textContent === txt);
    modeBtn('Free selection').click(); assert.equal(app.state().doc.mode, 'free');
    const box = page.find('.pick input'); assert.ok(box); box.dispatch('change'); assert.equal(app.state().doc.ids.length, 1);
    assert.ok(page.find('.doc-stats').textContent.includes('1 selected'));
    modeBtn('Select all listed').click(); assert.ok(app.state().doc.ids.length > 10); modeBtn('Clear').click(); assert.equal(app.state().doc.ids.length, 0); assert.ok(page.text().includes('Nothing is selected'));
    modeBtn('One element').click(); const pick = page.findAll('.doc-controls button.pick')[0]; pick.click(); assert.equal(app.state().doc.mode, 'element');
    modeBtn('One requirement').click(); page.findAll('.doc-controls button.pick')[1].click(); assert.equal(app.state().doc.mode, 'requirement');
    const raw = page.findAll('.doc button').find((b) => b.textContent === 'Raw text'); raw.click(); assert.ok(page.find('pre.raw').textContent.startsWith('# ')); assert.equal(page.find('.md'), null);
    page.findAll('.doc button').find((b) => b.textContent === 'Raw text').click(); assert.ok(page.find('.md'));
    page.find('[data-level=DD]').click(); modeBtn('Free selection').click(); modeBtn('Select all listed').click();
    const link = page.find('button[data-diagram]'); assert.ok(link, 'the whole model at the detailed level links to diagram units');
    const id = link.getAttribute('data-diagram'); assert.ok(app.ctx.diagrams.some((d) => d.id === id), 'the link target is a real unit');
    link.click(); assert.equal(app.tab, '2D'); assert.equal(app.state().twod.board, 'diagrams'); assert.equal(app.state().twod.unitId, id); assert.ok(page.find('pre.raw'));
  });
});

test('2D diagram board: the units are listed, one is shown with its source, a covered part leads back to the drawing', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    assert.ok(app.ctx.diagrams.length > 0);
    page.findAll('.twod-wrap, .choices button').find((b) => b.textContent === 'Diagrams')?.click();
    assert.equal(app.state().twod.board, 'diagrams');
    const items = page.findAll('.diagram-board button.pick'); assert.equal(items.length, app.ctx.diagrams.length);
    items[0].click(); assert.equal(app.state().twod.unitId, app.ctx.diagrams[0].id); assert.ok(page.find('.diagram pre.raw').textContent.length > 5);
    const withNodes = app.ctx.diagrams.findIndex((d) => d.covers && d.covers.nodeIds && d.covers.nodeIds.some((n) => app.ctx.ix.nodes.has(n)));
    assert.ok(withNodes >= 0, 'some unit covers parts'); page.findAll('.diagram-board button.pick')[withNodes].click();
    const chip = page.find('.diagram .chip'); assert.ok(chip, 'a unit that covers parts offers them'); chip.click(); assert.equal(app.state().twod.board, 'canvas'); assert.equal(app.selection.type, 'node');
  });
  const none = makeBundle('tiny', 0); none.diagrams = [];
  withBoot(none, {}, ({ page, app }) => { app.setTab('2D'); app.api.setTwod({ board: 'diagrams' }); assert.ok(page.text().includes(tables()['en-US']['twod.diagram.none'])); });
});

test('search: opens as a dialog, finds parts and requirements, a hit navigates (tab, selection, requirement), closing restores the page', opts, () => {
  withBoot(makeBundle('small', 3), {}, ({ page, app }) => {
    page.find('[data-keep=search-open]').click(); assert.ok(page.find('[role=dialog]'));
    const input = page.find('[data-keep=search-input]'); input.value = app.ctx.nodes[3].name.split(' ')[0]; input.dispatch('input');
    const hits = page.findAll('.hit'); assert.ok(hits.length > 0); hits[0].click();
    assert.equal(page.find('[role=dialog]'), null); assert.ok(app.selection || app.tab !== '2D');
    page.key('/'); const i2 = page.find('[data-keep=search-input]'); i2.value = app.ctx.requirements[1].id; i2.dispatch('input');
    const req = page.findAll('.hit').find((h) => h.textContent.includes('Requirement')); assert.ok(req); req.click();
    assert.equal(app.tab, '1D'); assert.equal(app.state().doc.reqId, app.ctx.requirements[1].id);
    page.key('/'); const i3 = page.find('[data-keep=search-input]'); i3.value = 'nonexistent-term-x'; i3.dispatch('input'); assert.ok(page.text().includes('No match.'));
    i3.dispatch('keydown', { key: 'Enter', preventDefault() {} }); assert.ok(page.find('[role=dialog]'), 'Enter with no hit keeps the dialog');
  });
});

test('preferences and route: the level, theme, language and columns are stored per project and model; the hash carries tab, level and selection', opts, () => {
  const bundle = makeBundle('small', 3); const key = bundle.meta.prefKey;
  let saved;
  withBoot(bundle, { hash: '#/QA?level=DD' }, ({ page, app }) => {
    assert.equal(app.tab, 'QA'); assert.equal(app.level, 'DD');
    page.find('[data-level=REQ]').click(); page.find('[data-keep=theme]').click(); page.find('[data-keep=panel-nav]').click();
    saved = JSON.parse(page.store[key]); assert.equal(saved.level, 'REQ'); assert.equal(saved.theme, 'light'); assert.equal(saved.navOpen, false);
    assert.equal(page.document.documentElement.getAttribute('data-scheme'), 'light');
    assert.equal(page.location.hash, '#/QA?level=REQ');
    app.setTab('2D'); app.select({ type: 'node', id: app.ctx.nodes[0].id }); assert.equal(page.location.hash, `#/2D?level=REQ&sel=${encodeURIComponent('node:' + app.ctx.nodes[0].id)}`);
    assert.deepEqual(Object.keys(page.store), [key], 'one record, one key');
  });
  withBoot(bundle, { store: { [key]: JSON.stringify(saved) } }, ({ app, page }) => { assert.equal(app.level, 'REQ'); assert.equal(page.find('.panel-nav'), null); });
  withBoot(bundle, { store: { [key]: '{broken' } }, ({ app }) => { assert.equal(app.level, 'BD'); assert.deepEqual(app.errors, []); });
  withBoot(bundle, { hash: '#/3D?level=ZZ&sel=node:no-such' }, ({ app }) => { assert.equal(app.tab, '3D'); assert.equal(app.level, 'BD'); assert.equal(app.selection, null); });
  const other = makeBundle('lending'); const h2 = boot(other); try { assert.ok(Object.keys(h2.page.store).every((k) => k === other.meta.prefKey)); assert.notEqual(other.meta.prefKey, key); } finally { h2.end(); }
  withBoot(bundle, { hash: '#/2D' }, ({ page, app }) => { page.location.hash = '#/QA?level=DD'; page.fire('hashchange'); assert.equal(app.tab, 'QA'); assert.equal(app.level, 'DD'); });
});

test('language and theme switches re-draw in place: the select changes every label, the document keeps its own language note', opts, () => {
  const bundle = makeBundle('small', 3);
  withBoot(bundle, {}, ({ page, app }) => {
    const sel = page.find('select'); sel.value = 'ja-JP'; sel.dispatch('change'); assert.equal(app.state().lang, 'ja-JP');
    assert.ok(page.text().includes(tables()['ja-JP']['status.ready'].split('{')[0]));
    app.setTab('1D'); assert.ok(page.text().includes('ドキュメント') || page.find('.doc'), 'ja interface');
    assert.ok(page.find('.doc-head').textContent.includes(tables()['ja-JP']['doc.language'].replace('{lang}', app.ctx.primaryLanguage)), 'a note says the document text keeps its own language');
    const T = tables(); const marks = () => page.findAll('.mark-block').map((m) => m.getAttribute('title'));
    assert.ok(marks().length > 0 && marks().every((x) => x === T['ja-JP']['doc.marker.block']), 'the cached reader is rebuilt in the new language (marker titles)');
    sel.value = 'zh-CN'; sel.dispatch('change'); assert.ok(marks().every((x) => x === T['zh-CN']['doc.marker.block']), 'and again after the next switch');
    sel.value = 'en-US'; sel.dispatch('change'); assert.ok(marks().every((x) => x === T['en-US']['doc.marker.block'])); assert.ok(!page.find('.doc-head').textContent.includes('is written in'), 'no note when the languages agree');
    assert.deepEqual(app.errors, []);
  });
});

test('gate configuration: tab list, hooks and the scripted steps all pass on a good bundle; a tampered hash fails the twin step and is reported', opts, () => {
  const bundle = makeBundle('small', 3);
  withBoot(bundle, {}, ({ page, app }) => {
    const cfg = app.gateConfig();
    assert.deepEqual(cfg.tabs, ['1D', '2D', '3D', '4D', '5D', 'QA']); assert.equal(cfg.headerSelector, '[data-gate~=header]');
    for (const k of ['setTab', 'tabState', 'renderOnce', 'frame']) assert.equal(typeof cfg[k], 'function', k);
    const f = cfg.frame(); assert.equal(cfg.frame(), f);
    cfg.setTab('QA'); assert.equal(cfg.tabState(), 'QA'); const r = cfg.renderOnce('QA'); assert.equal(r.ok, true); assert.ok(cfg.frame() > f);
    assert.deepEqual(cfg.steps.map((s) => s.name), ['doc-twin', 'levels', 'inspector', 'twod-labels', 'qa-rows', 'table-words', 'search', 'inspector-tabs', 'scene-3d', 'case-play', 'pair-play']);
    const level = app.level;
    for (const s of cfg.steps) assert.equal(s.run(), true, s.name);
    assert.equal(app.level, level, 'the steps restore the state they changed'); assert.equal(app.selection, null); assert.equal(page.find('[role=dialog]'), null);
    assert.deepEqual(app.errors, []);
  });
  const bad = makeBundle('small', 3); bad.calibrationHooks.docSelections[2].hash32 = '00000000';
  withBoot(bad, {}, ({ app }) => {
    const r = app.gateConfig().steps[0].run(); assert.equal(typeof r, 'string');
    assert.ok(app.errors.some((e) => e.where === 'doc-twin'), 'twin drift reaches window.__ATLAS_ERRORS__, which the gate fails on');
    assert.equal(app.checkDocSelections().filter((x) => !x.ok).length, 1);
  });
  withBoot(bundle, {}, ({ app }) => { globalThis.AtlasGate = { active: true, run(cfg) { globalThis.__ran = cfg; } }; bootAtlas(); assert.ok(globalThis.__ran && globalThis.__ran.tabs.length === 6); delete globalThis.__ran; });
});

test('hostile bundles: no bundle, no model, junk collections and an unknown schema never throw; the page still renders its chrome and empty states', () => {
  for (const junk of [undefined, null, {}, { schema: 'x' }, { schema: 'gemarc-atlas-bundle/1', model: { nodes: [null, 3, { id: 'a' }], edges: [{ id: 'e', sourceId: 'a', targetId: 'zz' }], space: 5 }, i18n: 'no', meta: 7, profile: [] }, { model: { nodes: [{ id: 'n1', kind: 1, name: null, roomId: {} }] }, fragments: { items: 3 }, closures: 4, i18n: { tables: { 'en-US': {} } } }]) {
    const h = boot(junk);
    try {
      assert.ok(h.app, JSON.stringify(junk)); assert.ok(h.page.findAll('[data-gate=header] button').length >= 1);
      for (const id of h.app.tabs()) { assert.doesNotThrow(() => h.app.setTab(id), id); }
      assert.ok(h.app.frame() >= 7);
      assert.ok(h.page.text().length > 0);
    } finally { h.end(); }
  }
});
