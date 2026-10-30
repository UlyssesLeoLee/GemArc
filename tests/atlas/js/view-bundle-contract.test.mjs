import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeModel, makeRun, makeChain, PROFILE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import * as CO from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import * as LF from '../../../src/gemarc_mcp/atlas/viewer/src/label-fit.js';
import * as SE from '../../../src/gemarc_mcp/atlas/viewer/src/search.js';
import * as PB from '../../../src/gemarc_mcp/atlas/viewer/src/playback.js';

const DOC = readFileSync(new URL('../../../docs/atlas/spec/bundle-contract.md', import.meta.url), 'utf8');
const MODULES = { 'model-access.js': MA, 'coords.js': CO, 'layout.js': LY, 'scene-buffers.js': SB, 'label-fit.js': LF, 'search.js': SE, 'playback.js': PB };

test('the contract lists the ten top-level keys of window.__ATLAS__ in its code block', () => {
  const block = /window\.__ATLAS__ = \{([\s\S]*?)\n\}/.exec(DOC)[1];
  const keys = [...block.matchAll(/^\s{2}"(\w+)":/gm)].map((m) => m[1]);
  assert.deepEqual(keys, ['schema', 'meta', 'profile', 'model', 'i18n', 'fragments', 'closures', 'runs', 'diagrams', 'calibrationHooks']);
  for (const k of ['baselines', 'pairs']) assert.ok(block.includes(`"${k}"`));
});

test('every warning code of the documentation exists in the code and vice versa', () => {
  const doc = new Set(DOC.match(/GEMARC_ATLAS_VIEW_\d{3}/g));
  assert.deepEqual([...doc].sort(), Object.values(MA.VIEW_WARNING).sort());
  assert.equal(new Set(Object.values(MA.VIEW_WARNING)).size, Object.values(MA.VIEW_WARNING).length);
});

test('every export named in the module table exists', () => {
  for (const [file, mod] of Object.entries(MODULES)) {
    const row = DOC.split('\n').find((l) => l.startsWith(`| \`${file}\``));
    assert.ok(row, `${file} is missing from the module table`);
    const names = [...row.split('|')[2].matchAll(/`([A-Za-z0-9_]+)`/g)].map((m) => m[1]);
    assert.ok(names.length >= 3, file);
    for (const n of names) assert.ok(n in mod, `${file}: documented export ${n} does not exist`);
  }
});

test('the documented warning table matches what the modules emit (spot checks)', () => {
  const v = MA.makeVocab(PROFILE);
  MA.kindInfo(v, 'x1'); MA.relationInfo(v, 'x2'); SB.stateColor(v, 'x3'); MA.domainColor(v, 'x4');
  const m = { nodes: [{ id: 'n', kind: 'Module', name: 'n', level: 'bogus' }], edges: [] };
  MA.levelOfNode(m.nodes[0], MA.indexModel(m), v); LY.arrangeView(m, 'no-such-view', { vocab: v });
  PB.seekState({ trace: [] }, 9, v.warnings);
  assert.deepEqual([...new Set(v.warnings.items.map((w) => w.code))].sort(), ['GEMARC_ATLAS_VIEW_001', 'GEMARC_ATLAS_VIEW_002', 'GEMARC_ATLAS_VIEW_004', 'GEMARC_ATLAS_VIEW_005', 'GEMARC_ATLAS_VIEW_007', 'GEMARC_ATLAS_VIEW_008', 'GEMARC_ATLAS_VIEW_009', 'GEMARC_ATLAS_VIEW_010']);
});

test('a bundle built to the documented shapes drives every module end to end', () => {
  const model = makeModel({ seed: 61, perRoom: 4, cases: 3 });
  const c = model.cases.find((x) => x.events.length >= 4); const A = makeRun(model, c); const chain = makeChain(model, A);
  const bundle = { schema: 'gemarc-atlas-bundle/1', meta: { projectId: model.meta.projectId, languages: ['en-US'], primaryLanguage: 'en-US', counts: {} }, profile: PROFILE, model,
    i18n: { languages: ['en-US'], tables: { 'en-US': {} } }, fragments: { language: 'en-US', modelSha8: '00000000', unselected: '', items: [], frame: { REQ: { head: '', chapters: [] }, BD: { head: '', chapters: [] }, DD: { head: '', chapters: [] } }, groups: {}, always: [], elements: [] }, closures: {},
    runs: { baselines: { [c.caseId]: A }, pairs: [{ caseId: c.caseId, interventionId: 'iv-test', status: 'OK', errors: [], B: A, diff: { outcomeClass: 'DIVERGED' }, chain }] },
    diagrams: [{ id: 'component-x', type: 'component', title: 'Component x', covers: { nodeIds: [], edgeIds: [], caseIds: [] }, requirementIds: [], evidenceLevel: 'Designed', evidenceIds: [], data: {}, mermaid: '', issues: [] }],
    calibrationHooks: { docSelections: [], traceLengths: [{ caseId: c.caseId, interventionId: null, length: A.trace.length }], scene: { nodeCount: model.nodes.length, buildingIds: model.space.buildings.map((b) => b.id), unassignedCount: 0 } } };
  const json = JSON.parse(JSON.stringify(bundle)); // what the page sees after parsing the embedded JSON
  const vocab = MA.makeVocab(json.profile); const ix = MA.indexModel(json.model);
  const L = LY.arrangeView(json.model, 'logical', { vocab, ix });
  assert.equal(L.unassigned.length, json.calibrationHooks.scene.unassignedCount);
  assert.equal(Object.keys(L.pos).length, json.calibrationHooks.scene.nodeCount);
  const pr = PB.pairRuns(json.runs, c.caseId, 'iv-test');
  assert.equal(pr.A.trace.length, json.calibrationHooks.traceLengths[0].length);
  const pv4 = PB.replayView({ ix, tab: '4D', run: PB.baselineFor(json.runs, c.caseId), chain: null, idx: 2 });
  const pv5 = PB.replayView({ ix, tab: '5D', run: pr.B, chain: pr.chain, idx: 2 });
  assert.ok(pv4 && pv5);
  assert.ok(SE.search(SE.makeSearchIndex(json.model, json.diagrams, { vocab, ix }), 'component').some((e) => e.kind === 'diagram'));
  assert.equal(vocab.warnings.items.filter((w) => w.code !== MA.VIEW_WARNING.UNKNOWN_STATE).length, 0);
});
