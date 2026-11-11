import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { makeModel, makeRun, PROFILE, FIXTURE, allFinite } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as LY from '../../../src/gemarc_mcp/atlas/viewer/src/layout.js';
import * as SB from '../../../src/gemarc_mcp/atlas/viewer/src/scene-buffers.js';
import * as CO from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';
import * as SE from '../../../src/gemarc_mcp/atlas/viewer/src/search.js';
import * as PB from '../../../src/gemarc_mcp/atlas/viewer/src/playback.js';

const SRC = new URL('../../../src/gemarc_mcp/atlas/viewer/src/', import.meta.url);
const files = readdirSync(SRC).filter((f) => f.endsWith('.js')).sort();
const text = (f) => readFileSync(new URL(f, SRC), 'utf8');

// seven view modules plus assemble.js (the 1D text assembly of the document lane); every scan below applies to all eight
test('the viewer source set is exactly the seven pure modules and the document assembly module', () => {
  assert.deepEqual(files, ['assemble.js', 'coords.js', 'label-fit.js', 'layout.js', 'model-access.js', 'playback.js', 'scene-buffers.js', 'search.js']);
});

test('source convention for the release bundler: ASCII, LF, single-line relative imports, only export const|function|class, no cycles', () => {
  const deps = new Map();
  for (const f of files) {
    const s = text(f);
    assert.ok(!/[^\x00-\x7f]/.test(s), `${f}: non-ASCII character`);
    assert.ok(!s.includes('\r'), `${f}: CR`); assert.ok(!s.startsWith('﻿'), `${f}: BOM`);
    const imports = s.split('\n').filter((l) => /^\s*import\b/.test(l));
    for (const l of imports) assert.match(l, /^import \{ [A-Za-z0-9_, ]+ \} from '\.\/[a-z-]+\.js';$/, `${f}: ${l}`);
    deps.set(f, imports.map((l) => /'\.\/([a-z-]+\.js)'/.exec(l)[1]));
    for (const l of s.split('\n').filter((x) => /^export\b/.test(x))) assert.match(l, /^export (const|function|class) /, `${f}: ${l}`);
    assert.ok(!/^export default|^export \{|^export \*/m.test(s), `${f}: re-export or default export`);
    assert.ok(!/\bimport\s*\(/.test(s), `${f}: dynamic import`);
  }
  const visit = (f, stack) => { assert.ok(!stack.includes(f), `cycle: ${[...stack, f].join(' -> ')}`); for (const d of deps.get(f)) visit(d, [...stack, f]); };
  for (const f of files) visit(f, []);
});

test('no network, no DOM, no clock, no randomness, no eval in the pure modules (BD-001 strings included, comments too)', () => {
  const forbidden = [/\bfetch\s*\(/, /\bimport\s*\(/, /url\(\s*http/i, /@import/, /XMLHttpRequest/, /\beval\s*\(/, /new Function/, /Math\.random/, /\bDate\b/, /\bdocument\b/, /\bwindow\b/, /\blocalStorage\b/, /\bsetTimeout\b/, /requestAnimationFrame/, /\bconsole\./, /https?:\/\//];
  for (const f of files) for (const re of forbidden) assert.ok(!re.test(text(f)), `${f}: ${re}`);
});

test('no business literals: no id prefix tests, no room or building names, no id-shaped literals in the pure modules', () => {
  for (const f of files) {
    const s = text(f).split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
    assert.ok(!/\.startsWith\(\s*['"`]/.test(s) && !/\.endsWith\(\s*['"`]/.test(s) && !/\.indexOf\(\s*['"`]/.test(s), `${f}: string prefix test`);
    assert.ok(!/['"`][a-z]{1,4}\.[a-z][\w-]*['"`]/.test(s.replace(/'[0-9]D'/g, '')), `${f}: id-shaped literal`);
    const hardCoded = new RegExp(['BUILDING_ORDER', 'DOMAIN_COLOR', 'GEM_SHAPE_BY_KIND', 'KIND_SCALE', ['dom', 'ainOf'].join('')].join('|'));
    assert.ok(!hardCoded.test(s), `${f}: a vocabulary table that is hard-coded`);
  }
});

function* variants() {
  for (let seed = 1; seed <= 5; seed++) {
    yield [`s${seed} clean 400`, makeModel({ seed, buildings: 4, floors: 4, rooms: 5, perRoom: 5 })];
    yield [`s${seed} unknown 400`, makeModel({ seed, buildings: 4, floors: 4, rooms: 5, perRoom: 5, unknownBuilding: true, unknownKind: true, unknownTrust: true, unknownRelation: true, orphans: 7, noCases: true, levels: [0, 0, 1] })];
  }
  yield ['fixture', FIXTURE];
  yield ['empty', { nodes: [], edges: [], cases: [] }];
  yield ['nodes only', { nodes: [{ id: 'a1', kind: 'X', name: 'A', roomId: 'nope' }, { id: 'a2', kind: 'X', name: 'B' }], edges: [{ id: 'a1:r:a1', sourceId: 'a1', targetId: 'a1', relation: 'r' }, { id: 'a1:r:a2', sourceId: 'a1', targetId: 'a2', relation: 'r' }] }];
  yield ['hostile', { nodes: [{ id: 'q', kind: 5, name: null, roomId: {}, props: 'x' }, null, 'junk'].filter(Boolean), edges: [{ id: 'e', sourceId: 'q', targetId: 'ghost', relation: 7 }], space: { buildings: 'no', floors: [{ id: 'f', buildingId: 1 }], rooms: null }, cases: [{ caseId: 'c', events: 3 }] }];
}

test('genericity probe: random id families, 400 nodes, unknown building / kind / trust / relation, orphans, repeated levels, no cases -> finite, no NaN, no throw', () => {
  let checks = 0;
  for (const [name, model] of variants()) {
    for (const vocab of [MA.makeVocab(PROFILE), MA.makeVocab(null)]) {
      const ix = MA.indexModel(model);
      for (const view of [...LY.VIEW_IDS, 'no-such-view']) for (const vis of [undefined, MA.shownAtLevel(model, 'REQ', vocab, ix)]) {
        const L = LY.arrangeView(model, view, { vocab, ix, vis });
        assert.ok(allFinite(L.pos) && allFinite(L.boxes), `${name}/${view}: non-finite layout`);
        for (const id of L.shownNodeIds) assert.ok(L.pos[id], `${name}/${view}: ${id} visible without a position`);
        for (const rel of [MA.relevance(ix, null), MA.relevance(ix, model.nodes?.[0] ? { type: 'node', id: model.nodes[0].id } : null)]) {
          const b = SB.makeBuffers(model, { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, showFrames: true, relevance: rel, vocab, eye: [0, 5, -30] }, ix);
          assert.ok(allFinite(b.mesh.pos) && allFinite(b.mesh.col) && allFinite(b.lines) && allFinite(b.hitNodes), `${name}/${view}: non-finite buffers`);
        }
        const pts = LY.layoutPoints(L); const cam = CO.fitCamera(CO.newCamera(), pts, 1.6);
        assert.ok(allFinite(cam), `${name}/${view}: camera`); assert.deepEqual(CO.pointsOutside(cam, pts, 900, 560, 1), [], `${name}/${view}: frame`);
        checks++;
      }
      for (const vis of [null, MA.shownAtLevel(model, 'BD', vocab, ix)]) { const L2 = LY.cardLayout(model, vis, { vocab, ix }); assert.ok(allFinite(L2.nodes) && allFinite(L2.bounds), `${name}: 2D`); }
      SE.search(SE.makeSearchIndex(model, [], { vocab, ix }), 'a');
      const c0 = model.cases?.[0];
      if (c0 && Array.isArray(c0.events) && model.nodes.length > 1 && model.edges?.length) assert.ok(PB.replayView({ ix, tab: '4D', run: makeRun(model, c0), chain: null, idx: 1 }));
      else assert.equal(PB.replayView({ ix, tab: '4D', run: null, chain: null, idx: 0 }), null);
    }
  }
  assert.ok(checks > 100);
});

test('the 400 node unknown-values model: every node is placed, the unplaced ones are in the unassigned region, and warnings are bounded', () => {
  const m = makeModel({ seed: 9, buildings: 4, floors: 4, rooms: 5, perRoom: 5, unknownBuilding: true, unknownKind: true, unknownTrust: true, orphans: 7, noCases: true });
  assert.equal(m.nodes.length, 407);
  const vocab = MA.makeVocab(PROFILE); const L = LY.arrangeView(m, 'logical', { vocab });
  assert.equal(Object.keys(L.pos).length, 407);
  assert.ok(L.unassigned.length >= 7 && L.unassigned.every((id) => L.pos[id]));
  assert.ok(vocab.warnings.items.length <= 25 * 11);
  assert.ok(vocab.warnings.count() >= vocab.warnings.items.length);
});

test('layouts and buffers are deterministic: two runs on a clone are identical', () => {
  const m = makeModel({ seed: 12, buildings: 3, perRoom: 6 });
  const run = () => { const c = structuredClone(m); const v = MA.makeVocab(PROFILE); const ix = MA.indexModel(c); const L = LY.arrangeView(c, 'logical', { vocab: v, ix }); const b = SB.makeBuffers(c, { positions: L.pos, shownNodeIds: L.shownNodeIds, shownEdgeIds: L.shownEdgeIds, boxes: L.boxes, vocab: v, eye: [0, 5, -30] }, ix); return JSON.stringify([L.pos, L.boxes, Array.from(b.mesh.pos), Array.from(b.lines), b.labels, b.paintedEdgeIds]); };
  assert.equal(run(), run());
});

test('a node-less or space-less model never crashes any consumer, including the camera on an empty scene', () => {
  const L = LY.arrangeView({}, 'logical', { vocab: MA.makeVocab(PROFILE) });
  assert.deepEqual({ ...L.pos }, {}); assert.equal(LY.layoutBounds(L), null);
  const cam = CO.fitCamera(CO.newCamera(), LY.layoutPoints(L), 1.6);
  assert.deepEqual(cam, CO.newCamera());
  assert.equal(SB.makeBuffers({}, { positions: {} }).hitNodes.length, 0);
});
