import test from 'node:test';
import assert from 'node:assert/strict';
import { makeModel, PROFILE, FIXTURE } from './view-helpers.mjs';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import * as SE from '../../../src/gemarc_mcp/atlas/viewer/src/search.js';

const V = () => MA.makeVocab(PROFILE);
function modelWith(extra = {}) {
  const m = makeModel({ seed: 41, perRoom: 3 });
  m.interventions = [{ interventionId: 'IV1', type: 'disable_module', targetNodeId: m.nodes[0].id, description: 'Turn the first part off', appliesTo: [m.cases[0].caseId], expect: {}, note: 'a note' }];
  m.tbds = [{ id: 'TB1', title: 'Open question', decision: 'pending', evidenceIds: [] }];
  return { ...m, ...extra };
}

test('one link per page: node 2D+3D, edge and diagram 2D, case 4D, intervention 5D, requirement 1D, TBD QA', () => {
  const m = modelWith(); const idx = SE.makeSearchIndex(m, [{ id: 'dg1', title: 'First diagram', type: 'Component' }], { vocab: V() });
  const count = (kind) => idx.filter((e) => e.kind === kind).length;
  assert.equal(count('node'), 2 * m.nodes.length); assert.equal(count('edge'), m.edges.length); assert.equal(count('diagram'), 1);
  assert.equal(count('case'), m.cases.length); assert.equal(count('intervention'), 1); assert.equal(count('requirement'), m.requirements.length); assert.equal(count('tbd'), 1);
  assert.deepEqual([...new Set(idx.filter((e) => e.kind === 'node').map((e) => e.tab))].sort(), ['2D', '3D']);
  assert.equal(new Set(idx.map((e) => e.key)).size, idx.length);
  for (const e of idx) assert.ok(SE.SEARCH_KINDS.includes(e.kind) && SE.SEARCH_TABS.includes(e.tab));
});

test('hits carry kind keys only: the module has no display text of its own', async () => {
  const src = await import('node:fs').then((fs) => fs.readFileSync(new URL('../../../src/gemarc_mcp/atlas/viewer/src/search.js', import.meta.url), 'utf8'));
  assert.ok(!/[^\x00-\x7f]/.test(src.replace(/\\u[0-9a-f]{4}/g, '')), 'non-ASCII literal in search.js');
  assert.equal(SE.SEARCH_KINDS.length, 7);
});

test('targets point at things that exist and are shown at the level they ask for', () => {
  const m = modelWith(); const vocab = V(); const ix = MA.indexModel(m); const idx = SE.makeSearchIndex(m, [], { vocab, ix });
  for (const e of idx) {
    const t = e.target; if (t.node) assert.ok(ix.nodes.has(t.node), e.key);
    if (t.edge) assert.ok(ix.edges.has(t.edge)); if (t.caseId) assert.ok(ix.cases.has(t.caseId));
    if (t.node && t.level && e.kind === 'node') assert.ok(MA.shownAtLevel(m, t.level, vocab, ix).nodeIds.has(t.node), e.key);
    if (t.edge) assert.ok(MA.shownAtLevel(m, t.level, vocab, ix).edgeIds.has(t.edge), e.key);
  }
});

test('every intervention opens on a case it applies to; without any case the link has no case, never a throw', () => {
  const m = modelWith(); const idx = SE.makeSearchIndex(m, [], { vocab: V() });
  assert.equal(idx.find((e) => e.kind === 'intervention').target.caseId, m.cases[0].caseId);
  const bare = modelWith({ cases: [] }); bare.interventions[0].appliesTo = [];
  assert.doesNotThrow(() => SE.makeSearchIndex(bare, [], { vocab: V() }));
  assert.equal(SE.makeSearchIndex(bare, [], { vocab: V() }).find((e) => e.kind === 'intervention').target.caseId, null);
});

test('models without cases, interventions, requirements or tbds still index', () => {
  const m = makeModel({ seed: 42, noCases: true }); delete m.interventions; delete m.requirements; delete m.tbds;
  const idx = SE.makeSearchIndex(m, undefined, { vocab: V() });
  assert.deepEqual([...new Set(idx.map((e) => e.kind))].sort(), ['edge', 'node']);
  assert.deepEqual(SE.makeSearchIndex({}), []);
});

test('search: empty, blank and unknown queries find nothing; width and case are ignored; every word must match', () => {
  const m = modelWith(); const idx = SE.makeSearchIndex(m, [], { vocab: V() });
  assert.deepEqual(SE.search(idx, ''), []); assert.deepEqual(SE.search(idx, '   '), []); assert.deepEqual(SE.search(idx, 'qqqq-no-such-thing'), []);
  const n = m.nodes[3];
  const exact = SE.search(idx, n.id.toUpperCase()); assert.equal(exact[0].id, n.id);
  const wide = SE.search(idx, n.id.replace(/[a-z0-9]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0xfee0))); assert.equal(wide[0].id, n.id);
  assert.ok(SE.search(idx, `node ${n.name.split(' ')[1]}`).some((e) => e.id === n.id));
  assert.deepEqual(SE.search(idx, `${n.id} qqqq-no-such-thing`), []);
});

test('search: a node name returns both its 2D and 3D link, ordered 2D before 3D; a part ranks above a line with the same label', () => {
  const m = modelWith(); const n = m.nodes[0]; m.edges[0].label = n.name;
  const idx = SE.makeSearchIndex(m, [], { vocab: V() });
  const hits = SE.search(idx, n.name).filter((e) => e.id === n.id);
  assert.deepEqual(hits.map((e) => e.tab), ['2D', '3D']);
  const all = SE.search(idx, n.name); assert.ok(all.findIndex((e) => e.kind === 'node') < all.findIndex((e) => e.kind === 'edge'));
});

test('search: every dimension is reachable; limit applies; results are deterministic', () => {
  const m = modelWith(); const idx = SE.makeSearchIndex(m, [{ id: 'dg1', title: 'First diagram', type: 'Component' }], { vocab: V() });
  assert.equal(SE.search(idx, m.requirements[0].id)[0].tab, '1D'); assert.equal(SE.search(idx, m.cases[0].caseId)[0].tab, '4D');
  assert.equal(SE.search(idx, 'IV1')[0].tab, '5D'); assert.equal(SE.search(idx, 'TB1')[0].tab, 'QA'); assert.equal(SE.search(idx, 'dg1')[0].tab, '2D');
  assert.equal(SE.search(idx, 'node', 5).length, 5);
  assert.deepEqual(SE.search(idx, 'node').map((e) => e.key), SE.search(idx, 'node').map((e) => e.key));
});

test('ranking: an exact id ranks above an exact name; equal scores list 1D before 2D before 3D', () => {
  const mk = (nodes, reqs = []) => ({ nodes: nodes.map(([id, name]) => ({ id, kind: 'Module', name, roomId: 'r' })), edges: [], cases: [], requirements: reqs, space: { buildings: [], floors: [], rooms: [] } });
  const a = SE.makeSearchIndex(mk([['byname', 'foo'], ['foo', 'other']]), [], { vocab: V() });
  assert.deepEqual(SE.search(a, 'foo').filter((e) => e.tab === '2D').map((e) => e.id), ['foo', 'byname']);
  const b = SE.makeSearchIndex(mk([['n1', 'qqq']], [{ id: 'RQ', title: 'qqq', source: 's', nodeIds: [] }]), [], { vocab: V() });
  assert.deepEqual(SE.search(b, 'qqq').map((e) => e.tab), ['1D', '2D', '3D']);
});

test('the fixture indexes with its own field names', () => {
  const idx = SE.makeSearchIndex(FIXTURE, [], { vocab: V() });
  assert.ok(idx.length > FIXTURE.nodes.length);
  assert.ok(SE.search(idx, FIXTURE.nodes[0].id).length > 0);
});
