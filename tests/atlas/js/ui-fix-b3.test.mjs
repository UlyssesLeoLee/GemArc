// Tests of the fixes of fix-view-b3 that need no page: the gate's allow-list of the model's own words, merged search rows, the state-change
// summary, the best-pitch camera fit, the sequence rows that carry ids for the interface language, and that every vocabulary collection the
// UI asks a label of exists in the profile (the class of bug behind the English outcome words). Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import * as GC from '../../../src/gemarc_mcp/atlas/viewer/src/ui/gate-checks.js';
import * as SS from '../../../src/gemarc_mcp/atlas/viewer/src/ui/shell-state.js';
import * as ST from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import * as SM from '../../../src/gemarc_mcp/atlas/viewer/src/ui/scene-model.js';
import * as DM from '../../../src/gemarc_mcp/atlas/viewer/src/ui/diagram-model.js';
import { newCamera, boundsOfPoints } from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import { playResolve, playInitial } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/stage-state.js';
import { viewContext } from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import { ROOT, tables, makeBundle, skipNoPython } from './ui-helpers.mjs';
import { sealBundle } from './view3d-helpers.mjs';

const UI = join(ROOT, 'src', 'gemarc_mcp', 'atlas', 'viewer', 'src', 'ui');

// ---------------------------------------------------------------- the gate's text allow-list
test('gate text allow: the tokens of the model prose are listed with context, the interface tables are not, a bare token is dropped', () => {
  const data = {
    model: { nodes: [{ name: 'Pool returns undefined when empty (NaN inputs rejected)' }, { name: 'undefined' }, { name: 'NaN' }] },
    i18n: { 'en-US': { x: 'Total: undefined' } },
    fragments: ['see [object Object] here'],
  };
  const list = GC.gateTextAllow(data);
  assert.ok(list.some((s) => s.includes('returns undefined when')));
  assert.ok(list.some((s) => s.includes('(NaN')));
  assert.ok(list.some((s) => s.includes('[object Object]')));
  assert.ok(!list.some((s) => s.includes('Total')), 'the interface tables are chrome: never exempt');
  assert.ok(!list.includes('undefined') && !list.includes('NaN'), 'a bare token would exempt every use of the word');
  assert.ok(list.every((s) => s.length >= 4 + 3));
});

test('gate text allow: total on odd input and bounded', () => {
  assert.deepEqual(GC.gateTextAllow(null), []);
  assert.deepEqual(GC.gateTextAllow([1, 2]), []);
  assert.deepEqual(GC.gateTextAllow({ a: 5, b: null, c: [undefined, {}] }), []);
  const many = { rows: Array.from({ length: 5000 }, (_, i) => 'row ' + i + ' says undefined here') };
  assert.ok(GC.gateTextAllow(many, 50).length <= 50 * 4 + 3);
  const repeated = { rows: [...Array.from({ length: 500 }, () => 'the same phrase says undefined again'), 'a different phrase says NaN here'] };
  assert.ok(GC.gateTextAllow(repeated).some((x) => x.includes('says NaN')), 'repeats of one phrase do not use up the budget');
});

// ---------------------------------------------------------------- search rows
test('search: a part found as 2D and 3D is one row that knows both pages and opens the page the visitor is on', () => {
  const hit = (tab) => ({ kind: 'node', tab, id: 'n1', title: 'T', sub: 's', target: { tab, node: 'n1', level: 'BD' } });
  const rows = SS.shellMergeHits([hit('2D'), hit('3D'), { kind: 'edge', tab: '2D', id: 'e1', title: 'E', sub: '', target: { tab: '2D', edge: 'e1' } }, hit('3D')]);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].tabs, ['2D', '3D']);
  assert.deepEqual(rows[1].tabs, ['2D']);
  assert.equal(SS.shellHitFrom(rows[0], '3D').target.tab, '3D');
  assert.equal(SS.shellHitFrom(rows[0], '3D').tab, '3D');
  assert.equal(SS.shellHitFrom(rows[0], '4D').tab, '2D', 'on another page the first page wins');
  assert.equal(SS.shellHitFrom(rows[1], '3D').tab, '2D', 'a one-page row never moves');
  assert.deepEqual(SS.shellMergeHits(null), []);
  assert.deepEqual(SS.shellMergeHits([null, 5, {}]).length, 1);
});

// ---------------------------------------------------------------- comparison summary
test('change summary: counts, shows at most four with the engine ids of the states, says how many more', () => {
  const rows = Array.from({ length: 6 }, (_, i) => ({ name: 'P' + i, a: i % 2 ? 'Completed' : '', b: 'Failed' }));
  const s = ST.playChangeSummary(rows);
  assert.equal(s.n, 6); assert.equal(s.shown.length, 4); assert.equal(s.more, 2);
  assert.deepEqual(s.shown[0], { name: 'P0', a: 'Idle', b: 'Failed' });
  assert.deepEqual(ST.playChangeSummary(null), { n: 0, shown: [], more: 0 });
});

// ---------------------------------------------------------------- camera fit
test('best fit: a long thin scene is shown from higher up, a compact one keeps the first pitch, and the fit always frames every point', () => {
  const strip = []; for (let i = 0; i < 12; i++) for (const z of [-3, 3]) strip.push([i * 8, 0, z]);
  const cam = { yaw: 0.18, pitch: 0.5 };
  const best = SM.sceneBestFit(cam, strip, 1.4);
  assert.ok(best.pitch >= 0.5);
  assert.ok(SM.sceneCoverage(best, strip, 1.4) >= SM.sceneCoverage(SM.sceneBestFit(cam, strip, 1.4, [0.5]), strip, 1.4) - 1e-9);
  const square = []; for (const x of [-10, 10]) for (const z of [-10, 10]) for (const y of [0, 4]) square.push([x, y, z]);
  assert.equal(SM.sceneBestFit(cam, square, 1.4).pitch, 0.5, 'the first pitch stays unless another one covers clearly more');
  assert.deepEqual(SM.sceneBestFit(cam, [], 1.4).target.length, 3, 'no points: a camera comes back, not a throw');
  assert.equal(SM.sceneCoverage(newCamera(cam), [[0, 0, 0]], 1.4), 0, 'a single point has no area');
});

// ---------------------------------------------------------------- sequence rows
test('sequence rows carry the event type and outcome ids so the page can write them in the interface language', () => {
  const ix = { nodes: new Map([['a', { name: 'A' }], ['b', { name: 'B' }]]) };
  const ctx = { ix };
  const res = { run: { trace: [{ sourceNodeId: 'a', targetNodeId: 'b', eventType: 'Call', outcome: 'Starved' }, { sourceNodeId: 'b', targetNodeId: 'a', eventType: 'Return', outcome: 'OK' }] }, chain: null };
  const m = DM.seqModel(ctx, res, 0);
  assert.equal(m.rows.length, 2);
  assert.deepEqual([m.rows[0].type, m.rows[0].outcome], ['Call', 'Starved']);
  assert.equal(m.rows[1].back, true);
});

// ---------------------------------------------------------------- vocabulary collections
test('every collection name the UI passes to api.label exists in the vocabulary profile', () => {
  const profile = JSON.parse(readFileSync(join(ROOT, 'src', 'gemarc_mcp', 'atlas', 'profiles', 'software.json'), 'utf8'));
  const seen = new Set();
  for (const f of readdirSync(UI).filter((x) => x.endsWith('.js'))) {
    const text = readFileSync(join(UI, f), 'utf8');
    for (const m of text.matchAll(/\b(?:api\.)?label\(\s*'([A-Za-z]+)'/g)) seen.add(m[1] + '@' + f);
  }
  assert.ok(seen.size >= 8, 'the scan finds the calls');
  for (const key of seen) {
    const [name, file] = key.split('@');
    assert.ok(Array.isArray(profile[name]) && profile[name].length > 0, `${file} asks a label of the collection '${name}' that the profile does not have`);
    assert.ok(profile[name].every((e) => e && typeof e === 'object' && e.label && e.label['en-US']), `${name} entries carry labels`);
  }
  // the three collections of the 4D and 5D words, by name
  for (const name of ['eventTypes', 'eventOutcomes', 'outcomeClasses', 'nodeStates']) assert.ok([...seen].some((k) => k.startsWith(name + '@')), `the UI labels ${name}`);
});

test('the new interface keys exist in all three tables and the English count line has no plural form to get wrong', () => {
  const t = tables();
  for (const key of ['twod.diagram.covers.noparts', 'twod.diagram.note', 'play.headline', 'play.change.item', 'play.headline.more', 'chain.question', 'chain.why.note', 'doc.panel.hint', 'diagram.scroll']) {
    for (const lang of Object.keys(t)) assert.equal(typeof t[lang][key], 'string', `${lang} ${key}`);
  }
  assert.ok(!/cases have no sealed/.test(t['en-US']['play.hidden']));
  assert.ok(!t['ja-JP']['play.step'].includes('手目'), 'the move counter of a board game is not a step counter');
});

// ---------------------------------------------------------------- the camera does not move while a run plays
test('the bounds the camera is fitted to do not change from step to step of a run (the best-pitch fit never flips during playback)', { skip: skipNoPython }, () => {
  const bundle = makeBundle('reference', 3); sealBundle(bundle, 2);
  const ctx = viewContext(bundle);
  for (const tab of ['4D', '5D']) {
    const res = playResolve(ctx, tab, playInitial());
    assert.ok(res.length > 2, tab);
    const keys = new Set();
    for (let idx = -1; idx < res.length; idx++) {
      const c = SM.sceneCompose(ctx, { mode: tab, level: ctx.levels[ctx.levels.length - 1], viewId: 'logical', sel: null, res, idx, frames: true });
      const b = boundsOfPoints(c.fitPoints);
      keys.add([...b.min, ...b.max].map((v) => v.toFixed(2)).join(','));
    }
    assert.equal(keys.size, 1, tab + ': the fit key changes between steps');
  }
});
