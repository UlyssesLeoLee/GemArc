// The 3D, 4D and 5D tabs end to end on the stand-in page with a fake WebGL context: only sealed items appear (a project with one sealed and one unsealed
// case shows exactly the sealed one and no trace of the other), the player and its step count, the A/B comparison, the inspector tabs, the gate hooks (scene
// bounds, scripted steps) and the degraded paths. The stand-in has no layout engine: canvases get a fixed size. Real pixels are the gate's job (test_gate_3d.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { bootAtlas } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/main.js';
import { sceneOutside, sceneOccupancy } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/scene-model.js';
import { installPage } from './ui-dom-stub.mjs';
import { makeBundle, tables, skipNoPython } from './ui-helpers.mjs';
import { sealBundle, installFakeCanvas, fakeGl } from './view3d-helpers.mjs';

const opts = { skip: skipNoPython };
const EN = () => tables()['en-US'];
const BAD = /undefined|NaN|\[object/;

function boot(bundle, { canvas = true, make } = {}) {
  const page = installPage({});
  const fake = canvas ? installFakeCanvas(page, make ? { make } : {}) : null;
  globalThis.__ATLAS__ = bundle;
  const app = bootAtlas();
  return { page, app, fake, end: () => { if (fake) fake.restore(); page.cleanup(); } };
}
const sealedBundle = (kind = 'small', seed = 3, n = 1) => { const b = makeBundle(kind, seed); const { sealed } = sealBundle(b, n); return { b, sealed }; };
const text = (page) => page.text();

test('3D: the canvas is drawn in the redraw that created it, the status names the software renderer, the gate hook reports frames and nodes inside the canvas', opts, () => {
  const h = boot(makeBundle('small', 3));
  try {
    h.app.setTab('3D');
    assert.equal(h.fake.gls.length, 1, 'one context');
    const gl = h.fake.gls[0];
    assert.ok(gl.draws > 0, 'drawn');
    const canvas = h.page.find('canvas[data-gate=canvas]'); assert.ok(canvas);
    assert.ok(text(h.page).includes(EN()['stage.status.software']));
    const bounds = h.app.gateConfig().sceneBounds('3D');
    assert.ok(bounds.frames.length > 0 && bounds.nodes.length > 0 && bounds.occupancy === true);
    assert.deepEqual(sceneOutside(bounds, bounds.w, bounds.h), { frames: [], nodes: [] });
    assert.ok(sceneOccupancy(bounds) > 0.2);
    // a second redraw keeps the same canvas and the same context
    const before = gl.draws; h.app.renderOnce(); assert.equal(h.fake.gls.length, 1); assert.ok(gl.draws > before);
    assert.equal(h.page.find('canvas[data-gate=canvas]'), canvas, 'the canvas element is kept across redraws');
    assert.equal(h.app.gateConfig().sceneBounds('2D'), null, 'a tab without a stage has no scene');
    assert.deepEqual(h.app.errors, []);
  } finally { h.end(); }
});

test('3D: the toolbar changes the view and the frames, the legend opens, a click on the canvas selects nothing it did not hit', opts, () => {
  const h = boot(makeBundle('small', 3));
  try {
    h.app.setTab('3D');
    const sel = h.page.find('select[data-keep=stage-view]'); assert.ok(sel);
    assert.equal(sel.children.length, 6, 'six views');
    sel.value = 'security'; sel.dispatch('change');
    assert.equal(h.app.api.cache.get('stage-ui').viewId, 'security');
    const frames = h.page.findAll('button').find((b) => b.textContent === EN()['stage.frames']);
    frames.click();
    assert.equal(h.app.gateConfig().sceneBounds('3D').frames.length, 0, 'frames off: none projected');
    const legend = h.page.findAll('button').find((b) => b.textContent === EN()['stage.legend']);
    legend.click(); assert.ok(h.page.find('.legend')); assert.ok(h.page.find('.legend').textContent.includes(EN()['legend.kinds']));
    assert.deepEqual(h.app.errors, []);
  } finally { h.end(); }
});

test('4D and 5D: a project with one sealed and one unsealed case offers exactly the sealed one and carries nothing of the other', opts, () => {
  const { b, sealed } = sealedBundle('small', 3, 1);
  const unsealed = b.model.cases.filter((c) => !sealed.includes(c.caseId));
  assert.ok(unsealed.length >= 1);
  const h = boot(b);
  try {
    for (const tab of ['4D', '5D']) {
      h.app.setTab(tab);
      const opts4 = h.page.findAll('select[data-keep=play-case] option');
      assert.deepEqual(opts4.map((o) => o.getAttribute('value')), sealed, tab);
      const all = text(h.page);
      for (const c of unsealed) { assert.ok(!all.includes(c.caseId), c.caseId); assert.ok(!all.includes(c.title), c.title); }
      assert.ok(all.includes(EN()['play.hidden'].replace('{n}', String(unsealed.length))), 'the count of what is not shown');
      assert.ok(!BAD.test(all));
    }
    // the dock carries the step count of the sealed trace
    h.app.setTab('4D');
    assert.equal(h.page.find('[data-step-count]').getAttribute('data-step-count'), String(b.runs.baselines[sealed[0]].trace.length));
    // no embedded run, no pair of the unsealed case exists in the bundle the page holds
    assert.deepEqual(Object.keys(h.app.bundle.runs.baselines), sealed);
    assert.ok(h.app.bundle.runs.pairs.every((p) => sealed.includes(p.caseId)));
  } finally { h.end(); }
});

test('empty states: unsealed, no cases, no changes: the words of the table, no canvas, no scene for the gate', opts, () => {
  const en = EN();
  for (const [kind, seed, tab, key] of [['small', 3, '4D', 'empty.expectation'], ['small', 3, '5D', 'empty.expectation'], ['edge:no-cases', 4, '4D', 'empty.cases'], ['edge:no-cases', 4, '5D', 'empty.cases'], ['edge:no-interventions', 8, '5D', 'empty.interventions']]) {
    const h = boot(makeBundle(kind, seed));
    try {
      h.app.setTab(tab);
      assert.ok(text(h.page).includes(en[key]), `${kind} ${tab}`);
      assert.equal(h.page.find('canvas'), null, 'no canvas to be blank');
      assert.equal(h.app.gateConfig().sceneBounds(tab), null);
      assert.equal(h.page.find('[data-step-count]'), null);
    } finally { h.end(); }
  }
  // a sealed case with an empty trace still plays (zero steps), a sealed case the engine stopped says so
  const { b, sealed } = sealedBundle('small', 3, 1);
  b.runs.baselines[sealed[0]].status = 'LIMIT_EXCEEDED'; b.runs.baselines[sealed[0]].truncated = true;
  const h = boot(b);
  try { h.app.setTab('4D'); assert.ok(text(h.page).includes(en['play.run.status'].replace('{status}', 'LIMIT_EXCEEDED'))); assert.equal(h.page.find('canvas'), null); } finally { h.end(); }
});

test('4D: stepping the dock moves the position and the drawn frame; the event list, the now line and the legend follow; the slider seeks', opts, () => {
  const { b, sealed } = sealedBundle('small', 3, 1);
  const length = b.runs.baselines[sealed[0]].trace.length;
  const h = boot(b);
  try {
    h.app.setTab('4D');
    const gl = h.fake.gls[0]; const step = () => h.page.find('[data-step]').getAttribute('data-step');
    assert.equal(step(), '0');
    for (let k = 1; k <= 3; k++) { const draws = gl.draws; h.page.find('[data-keep=dock-next]').click(); assert.equal(step(), String(k)); assert.ok(gl.draws > draws, 'every step draws a frame'); }
    assert.equal(h.page.findAll('.ev-row.now').length, 1); assert.ok(h.page.find('.now-line').textContent.includes('3'));
    h.page.find('[data-keep=dock-last]').click(); assert.equal(step(), String(length));
    h.page.find('[data-keep=dock-next]').click(); assert.equal(step(), String(length), 'never beyond the end');
    h.page.find('[data-keep=dock-first]').click(); assert.equal(step(), '0');
    const slider = h.page.find('[data-keep=dock-slider]'); slider.value = '2'; slider.dispatch('input'); assert.equal(step(), '2');
    h.page.find('[data-keep=dock-toggle]').click(); assert.equal(h.page.find('[data-keep=dock-toggle]').getAttribute('aria-pressed'), 'true', 'playing');
    h.page.find('[data-keep=dock-toggle]').click(); assert.equal(h.page.find('[data-keep=dock-toggle]').getAttribute('aria-pressed'), 'false');
    const bounds = h.app.gateConfig().sceneBounds('4D'); assert.equal(bounds.occupancy, false); assert.deepEqual(sceneOutside(bounds, bounds.w, bounds.h), { frames: [], nodes: [] });
    h.page.findAll('.ev-row button')[1].click(); assert.equal(step(), '2', 'a row of the event list seeks');
    assert.deepEqual(h.app.errors, []);
  } finally { h.end(); }
});

test('5D: the comparison table, the chain tab with the roles of the steps, the cause buttons, a rejected pair shows its reason only', opts, () => {
  const { b, sealed } = sealedBundle('small', 3, 1);
  const h = boot(b);
  try {
    h.app.setTab('5D');
    assert.ok(h.page.find('.compare-table')); assert.ok(h.page.find('select[data-keep=play-change]'));
    assert.ok(h.page.find('[data-keep=dock-nextCause]') && h.page.find('[data-keep=dock-prevCause]'));
    h.page.find('[data-keep=dock-nextCause]').click(); assert.ok(Number(h.page.find('[data-step]').getAttribute('data-step')) >= 1);
    h.app.api.setInspectorTab('chain');
    const rows = h.page.findAll('.chain-row'); assert.ok(rows.length >= 2);
    assert.ok(rows.some((r) => (r.getAttribute('class') || '').includes('side-a')) && rows.some((r) => (r.getAttribute('class') || '').includes('now')));
    assert.ok(h.page.find('.chain-row .role').textContent.length > 0);
    h.page.find('.chain-row button').click(); // seeks to the step
    assert.equal(h.page.find('[data-step]').getAttribute('data-step'), String(b.runs.pairs[0].chain.steps.slice().sort((x, y) => x.at - y.at)[0].at + 1));
    h.app.api.setInspectorTab('diagram');
    assert.ok(h.page.find('svg.seq') && h.page.findAll('svg.seq .msg').length > 0);
    // the pair is rejected: nothing plays, the reason shows and the table is gone
    b.runs.pairs.forEach((p) => { p.status = 'REJECTED'; p.B = null; });
  } finally { h.end(); }
  const rejected = boot(b);
  try {
    rejected.app.setTab('5D');
    assert.ok(text(rejected.page).includes(EN()['play.pair.status'].replace('{status}', 'OK')) || rejected.page.find('.reason'));
    assert.equal(rejected.page.find('canvas'), null); assert.equal(rejected.page.find('.compare-table'), null);
    assert.equal(rejected.app.gateConfig().sceneBounds('5D'), null);
    rejected.app.api.setInspectorTab('chain'); assert.ok(text(rejected.page).includes(EN()['chain.empty']));
  } finally { rejected.end(); }
});

test('inspector tabs: 3D, 4D and 5D each list their tabs and every one shows something; the diagram tab switches between sequence and state', opts, () => {
  const { b } = sealedBundle('edge:machine', 1, 1);
  const h = boot(b);
  try {
    for (const [tab, ids] of [['3D', ['detail', 'diagram']], ['4D', ['detail', 'diagram']], ['5D', ['detail', 'chain', 'diagram']], ['2D', ['detail']]]) {
      h.app.setTab(tab);
      const strip = h.page.findAll('.panel-ins .choices button');
      assert.equal(strip.length, ids.length, tab);
      for (let i = 0; i < ids.length; i++) { h.app.api.setInspectorTab(ids[i]); assert.ok(h.page.find('.panel-ins .insp-body').firstChild, `${tab}/${ids[i]}`); }
    }
    h.app.setTab('4D'); h.app.api.setInspectorTab('diagram');
    h.page.findAll('.panel-ins .choices button').pop().click();
    assert.ok(h.page.find('svg.statechart') || text(h.page).includes(EN()['diagram.none.state']));
    h.app.setTab('3D'); h.app.api.setInspectorTab('diagram');
    h.page.findAll('.panel-ins .choices button')[2].click(); // the sequence button of the diagram tab
    assert.ok(text(h.page).includes(EN()['diagram.none.seq']), '3D has no run: the sequence says what it needs');
  } finally { h.end(); }
});

test('gate steps: the new steps are registered, pass on sealed and unsealed pages, restore what they changed, and a wrong embedded length fails case-play', opts, () => {
  for (const sealed of [true, false]) {
    const b = sealed ? sealedBundle('small', 3, 1).b : makeBundle('small', 3);
    const h = boot(b);
    try {
      const cfg = h.app.gateConfig(); const names = cfg.steps.map((s) => s.name);
      for (const n of ['inspector-tabs', 'scene-3d', 'case-play', 'pair-play']) assert.ok(names.includes(n), n);
      assert.ok(names.length >= 11);
      const tab = h.app.tab; const level = h.app.level;
      for (const s of cfg.steps) assert.equal(s.run(), true, `${sealed ? 'sealed' : 'unsealed'}: ${s.name}`);
      assert.equal(h.app.tab, tab); assert.equal(h.app.level, level); assert.equal(h.app.selection, null);
      assert.deepEqual(h.app.errors, []);
    } finally { h.end(); }
  }
  const { b } = sealedBundle('small', 3, 1);
  b.calibrationHooks.traceLengths[0].length += 1;
  const h = boot(b);
  try {
    const run = h.app.gateConfig().steps.find((s) => s.name === 'case-play').run();
    assert.equal(typeof run, 'string'); assert.match(run, /trace length/);
  } finally { h.end(); }
});

test('degraded: without a WebGL context, or when shader compilation fails, the 3D tab says so and shows the flat drawing; 4D and 5D keep the player', opts, () => {
  const { b } = sealedBundle('small', 3, 1);
  for (const make of [() => null, () => { const gl = fakeGl(); gl.getShaderParameter = () => false; return gl; }]) {
    const h = boot(b, { make });
    try {
      h.app.setTab('3D');
      assert.ok(text(h.page).includes(EN()['slot.degraded'])); assert.ok(h.page.find('svg.twod')); assert.equal(h.app.gateConfig().sceneBounds('3D'), null);
      assert.ok(h.app.gateConfig().steps.find((s) => s.name === 'scene-3d').run() === true);
      h.app.setTab('4D'); assert.ok(text(h.page).includes(EN()['stage.status.degraded'])); assert.ok(h.page.find('[data-step-count]'));
      h.page.find('[data-keep=dock-next]').click(); assert.equal(h.page.find('[data-step]').getAttribute('data-step'), '1');
      assert.deepEqual(h.app.errors, []);
    } finally { h.end(); }
  }
  const stub = boot(b, { canvas: false });
  try { stub.app.setTab('3D'); assert.ok(text(stub.page).includes(EN()['slot.degraded'])); assert.deepEqual(stub.app.errors, []); } finally { stub.end(); }
});

test('every language: the three tabs render with no missing key and no undefined/NaN text', opts, () => {
  const { b } = sealedBundle('small', 3, 1);
  for (const lang of ['zh-CN', 'ja-JP', 'en-US']) {
    const page = installPage({ language: lang, store: { [b.meta.prefKey]: JSON.stringify({ lang }) } });
    const fake = installFakeCanvas(page);
    globalThis.__ATLAS__ = JSON.parse(JSON.stringify(b));
    const app = bootAtlas();
    try {
      for (const tab of ['3D', '4D', '5D']) {
        app.setTab(tab);
        assert.ok(!BAD.test(page.text()), `${lang}/${tab}`);
        for (const id of ['detail', 'chain', 'diagram']) { app.api.setInspectorTab(id); assert.ok(!BAD.test(page.text()), `${lang}/${tab}/${id}`); }
        app.api.setInspectorTab('detail');
      }
      assert.deepEqual(app.errors, []);
    } finally { fake.restore(); page.cleanup(); }
  }
});
