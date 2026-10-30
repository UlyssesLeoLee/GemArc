// UI logic on REAL bundles (the Python build supplies fragments, closures, diagrams, profile and tables; see ui-helpers.mjs): the 1D document
// text equals the Python one (twin check over the 20 canonical selections), the selection state machine, the QA record, the 2D scene, the
// navigator tree and the inspector records on the lending-desk fixture, reference-scale synthetic models, random id families and the
// synthetic edge cases (no cases, unknown values, 400 nodes, odd ids, CJK, empty strings). Nothing here reads reference data.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as MV from '../../../src/gemarc_mcp/atlas/viewer/src/ui/model-view.js';
import * as DOC from '../../../src/gemarc_mcp/atlas/viewer/src/ui/doc-state.js';
import * as QA from '../../../src/gemarc_mcp/atlas/viewer/src/ui/qa.js';
import * as TW from '../../../src/gemarc_mcp/atlas/viewer/src/ui/twod-scene.js';
import * as NV from '../../../src/gemarc_mcp/atlas/viewer/src/ui/navigator.js';
import * as IN from '../../../src/gemarc_mcp/atlas/viewer/src/ui/inspector.js';
import * as AS from '../../../src/gemarc_mcp/atlas/viewer/src/assemble.js';
import * as MA from '../../../src/gemarc_mcp/atlas/viewer/src/model-access.js';
import { makeBundle, skipNoPython } from './ui-helpers.mjs';

const opts = { skip: skipNoPython };
const SCENARIOS = [['lending', 0], ['small', 3], ['reference', 7], ['edge:no-cases', 4], ['edge:unknown-values', 5], ['edge:cjk', 3], ['edge:odd-ids', 6], ['edge:empty-strings', 9], ['edge:rich-fields', 11], ['edge:no-interventions', 8], ['edge:big400', 2]];
const allFinite = (v) => { const seen = new Set(); const walk = (x) => { if (typeof x === 'number') return Number.isFinite(x); if (x && typeof x === 'object') { if (seen.has(x)) return true; seen.add(x); return Object.values(x).every(walk); } return true; }; return walk(v); };

test('1D twin: the page assembles the same text as Python for every calibration selection (20 per model, the hash of each recorded by the build)', opts, () => {
  let total = 0;
  for (const [kind, seed] of SCENARIOS) {
    const ctx = MV.viewContext(makeBundle(kind, seed));
    const rows = DOC.docCheckAll(ctx);
    assert.ok(rows.length >= 8, `${kind}: ${rows.length} selections`);
    for (const r of rows) assert.ok(r.ok, `${kind}: ${r.level} (${r.count} ids) page ${r.actual} build ${r.expected}`);
    total += rows.length;
  }
  assert.ok(total >= 150, `checked ${total} selections`);
});

test('1D twin: a tampered hash, a missing dataset and a damaged closure are reported (never silently equal) and never throw', opts, () => {
  const b = makeBundle('lending');
  b.calibrationHooks.docSelections[0].hash32 = 'deadbeef';
  const rows = DOC.docCheckAll(MV.viewContext(b));
  assert.equal(rows[0].ok, false); assert.ok(rows.slice(1).every((r) => r.ok));
  const gone = makeBundle('lending'); delete gone.fragments; assert.ok(DOC.docCheckAll(MV.viewContext(gone)).every((r) => !r.ok));
  const bad = makeBundle('lending'); const id = bad.fragments.elements[0]; bad.closures[id] = 'junk';
  assert.doesNotThrow(() => DOC.docCheckAll(MV.viewContext(bad)));
  assert.ok(!DOC.docKnownIds(MV.viewContext(bad)).has(id));
});

test('1D selection: requirement, element and free modes give exactly assemble(level, ids); unknown ids are dropped; every level works', opts, () => {
  const ctx = MV.viewContext(makeBundle('small', 3));
  const ds = DOC.docDataset(ctx); assert.ok(ds);
  const known = DOC.docKnownIds(ctx); const list = DOC.docKnownList(ctx, known);
  assert.ok(list.requirements.length > 0 && list.nodes.length > 0 && list.edges.length > 0 && list.cases.length > 0);
  let st = DOC.docInitial(ctx, 'BD', {});
  assert.equal(st.mode, 'requirement'); assert.equal(st.reqId, list.requirements[0].id);
  for (const level of ['REQ', 'BD', 'DD']) {
    st = DOC.docSetLevel(st, level);
    const r = DOC.docText(ctx, st); assert.ok(r.ok); assert.equal(r.text, AS.assemble(ds, level, [st.reqId])); assert.deepEqual(r.ids, [st.reqId]);
    assert.equal(r.bytes, new TextEncoder().encode(r.text).length); assert.ok(r.text.includes('## 1.'));
    assert.deepEqual(DOC.docOutline(r.text).map((c) => c.n), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  }
  const node = list.nodes[1].id;
  const el = DOC.docSetElement(st, node); assert.equal(el.mode, 'element'); assert.deepEqual(DOC.docText(ctx, el).ids, [node]);
  let free = DOC.docToggle(st, node); free = DOC.docToggle(free, list.edges[0].id); free = DOC.docToggle(free, 'no-such-id'); free = DOC.docToggle(free, list.cases[0].id);
  assert.equal(free.mode, 'free'); assert.deepEqual(DOC.docText(ctx, free).ids, [node, list.edges[0].id, list.cases[0].id]);
  assert.equal(DOC.docText(ctx, free).text, AS.assemble(ds, 'DD', [node, list.edges[0].id, list.cases[0].id]));
  assert.equal(DOC.docToggle(free, node).ids.includes(node), false); assert.deepEqual(DOC.docClear(free).ids, []);
  assert.deepEqual(DOC.docText(ctx, DOC.docClear(free)).ids, []); assert.ok(DOC.docText(ctx, DOC.docClear(free)).ok);
  const all = DOC.docSetAll(st, [...known]); assert.equal(DOC.docIds(all, ctx).length, known.size);
  assert.equal(DOC.docSetLevel(st, 'XX'), st); assert.equal(DOC.docSetMode(st, 'nope'), st);
  assert.deepEqual(DOC.docFilterRows(list.nodes, '').length, list.nodes.length);
  assert.deepEqual(DOC.docFilterRows([{ id: 'ab1.x', title: 'Loan' }, { id: 'cd2.y', title: 'Stock' }], ' LOAN ab1').map((r) => r.id), ['ab1.x']);
  assert.equal(DOC.docFilterRows([{ id: 'a', title: 'b' }], 'zzz').length, 0);
  assert.equal(DOC.docInitial(ctx, 'DD', { req: list.requirements[2].id }).reqId, list.requirements[2].id);
  assert.equal(DOC.docInitial(ctx, 'DD', { req: 'nope' }).reqId, list.requirements[0].id);
  assert.equal(DOC.docInitial(ctx, 'DD', { mode: 'free' }).mode, 'free');
});

test('1D selection: a model without requirements starts in element mode; a bundle without fragments yields an explicit unavailable state', opts, () => {
  const b = makeBundle('lending'); b.model.requirements = [];
  const st = DOC.docInitial(MV.viewContext(b), 'BD', {});
  assert.equal(st.mode, 'element'); assert.ok(st.elementId);
  const none = makeBundle('lending'); delete none.fragments; delete none.closures;
  const r = DOC.docText(MV.viewContext(none), DOC.docInitial(MV.viewContext(none), 'BD', {}));
  assert.equal(r.ok, false); assert.equal(r.text, ''); assert.ok(r.error);
  const empty = DOC.docInitial(MV.viewContext({}), 'BD', {}); assert.equal(empty.mode, 'element'); assert.equal(empty.elementId, null);
  assert.equal(DOC.docText(MV.viewContext({}), empty).ok, false);
});

test('QA record: counts reconcile with meta.counts, a mismatch is flagged, the optional build summary is read defensively, empty states are explicit', opts, () => {
  const b = makeBundle('small', 3); const ctx = MV.viewContext(b);
  const r = QA.qaReport(ctx, DOC.docCheckAll(ctx));
  assert.deepEqual(r.counts.map((c) => c.key), QA.QA_COUNT_KEYS); assert.equal(r.countsOk, true); assert.equal(r.mismatches, 0);
  assert.ok(r.counts.every((c) => c.ok === true)); assert.equal(r.counts[0].actual, b.model.nodes.length);
  assert.equal(r.rules, null); assert.equal(r.oracle, null); assert.equal(r.gate, null); assert.equal(r.sheet, null);
  assert.equal(r.oracleEmpty, true); assert.equal(r.docCheck.equal, r.docCheck.total); assert.ok(r.docCheck.total >= 8); assert.ok(r.evidence.length > 0 && r.kinds.length > 0 && r.relations.length > 0);
  assert.deepEqual(r.scene, { nodeCount: b.model.nodes.length, unassigned: 0, ok: true });
  b.meta.counts.nodes += 1; b.calibrationHooks.scene.unassignedCount = 2;
  b.meta.qa = { rules: { block: 1, error: 2, warn: 'x', info: 4 }, gate: { release: false, blockers: 3 }, oracle: { sealed: 5, revised: 1, confirmed: 0, pending: -2 }, notes: [{ code: 'LB-001', subject: 'n1', detail: 'd' }, { nope: 1 }, null] };
  b.meta.warnings = [{ code: 'RC-002', subject: 's', detail: 'x' }, { code: 'LB-001', subject: 'n1' }];
  const q = QA.qaReport(MV.viewContext(b));
  assert.equal(q.countsOk, false); assert.equal(q.mismatches, 1); assert.equal(q.counts[0].ok, false); assert.equal(q.scene.ok, false);
  assert.deepEqual(q.rules, { block: 1, error: 2, warn: null, info: 4 }); assert.deepEqual(q.gate, { release: false, blockers: 3 }); assert.deepEqual(q.oracle, { sealed: 5, revised: 1, confirmed: 0, pending: null });
  assert.deepEqual(q.notes.map((n) => n.code + '|' + n.subject), ['RC-002|s', 'LB-001|n1']); assert.equal(q.docCheck, null);
  b.runs = { baselines: { [b.model.cases[0].caseId]: { trace: [] } }, pairs: [] };
  assert.equal(QA.qaReport(MV.viewContext(b)).oracleEmpty, false);
  assert.doesNotThrow(() => QA.qaReport(MV.viewContext(null))); assert.doesNotThrow(() => QA.qaReport(MV.viewContext({ meta: { qa: 5, counts: 'x', warnings: 3 }, model: 5, runs: 7 })));
});

test('2D scene: finite numbers, counts consistent with the level filter, tiers from the selection, search dims, on every scenario and level', opts, () => {
  for (const [kind, seed] of SCENARIOS) {
    const ctx = MV.viewContext(makeBundle(kind, seed));
    const first = ctx.nodes[0]; const sel = first ? { type: 'node', id: first.id } : null;
    for (const level of ['REQ', 'BD', 'DD']) {
      for (const selection of [null, sel]) {
        const s = TW.twodScene(ctx, { level, selection, query: '' });
        assert.ok(allFinite(s), `${kind}/${level}: non-finite`);
        const shown = MA.shownAtLevel(ctx.model, level, ctx.vocab, ctx.ix);
        if (!selection) assert.equal(s.cards.length, shown.nodeIds.size, `${kind}/${level}`);
        else assert.ok(s.cards.length >= shown.nodeIds.size, `${kind}/${level}: a selection stays visible`);
        assert.equal(s.counts.cards + s.counts.hidden, ctx.nodes.length);
        assert.ok(s.viewBox.w > 0 && s.viewBox.h > 0);
        const ids = new Set(s.cards.map((c) => c.id)); for (const e of s.edges) assert.ok(ids.has(e.sourceId) && ids.has(e.targetId), `${kind}: edge ${e.id} has both ends drawn`);
        if (selection && s.cards.length) { assert.equal(s.cards.find((c) => c.id === first.id).tier, 'Selected'); assert.equal(s.active, true); } else assert.ok(s.cards.every((c) => c.tier === 'Selected'));
        assert.ok(s.cards.every((c) => typeof c.outline === 'string' && c.outline.length > 0 && !/NaN|undefined/.test(c.outline)));
      }
    }
  }
  const ctx = MV.viewContext(makeBundle('small', 3)); const word = ctx.nodes[0].name.split(' ')[0];
  const s = TW.twodScene(ctx, { level: 'DD', query: word });
  assert.ok(s.cards.some((c) => c.match) && s.cards.some((c) => !c.match)); assert.ok(s.cards.filter((c) => c.match).every((c) => (c.id + ' ' + c.name + ' ' + c.kind).toLowerCase().includes(word.toLowerCase())));
  const req = ctx.requirements[0]; const rs = TW.twodScene(ctx, { level: 'DD', selection: { type: 'requirement', id: req.id } });
  assert.ok(rs.cards.filter((c) => c.tier === 'Selected').length === req.nodeIds.length); assert.equal(TW.twodRelevanceSelection(ctx, { type: 'requirement', id: 'nope' }), null);
  const none = TW.twodScene(MV.viewContext({}), {}); assert.equal(none.cards.length, 0); assert.ok(allFinite(none));
});

test('2D frame labels: no two names overlap, a room name sits above its first card row, the floor name is written, on seeds, levels and edge cases', opts, () => {
  const kinds = [['small', 1], ['small', 2], ['small', 3], ['small', 4], ['small', 5], ['reference', 3], ['edge:cjk', 3], ['edge:odd-ids', 6], ['edge:unknown-values', 5], ['edge:big400', 2], ['edge:empty-strings', 9]];
  let checked = 0; let floors = 0;
  for (const [kind, seed] of kinds) {
    const ctx = MV.viewContext(makeBundle(kind, seed));
    for (const level of ctx.levels) {
      const s = TW.twodScene(ctx, { level });
      const labels = TW.twodFrameLabels(s, 'U');
      assert.deepEqual(TW.twodLabelOverlaps(labels).map(([a, b]) => a.kind + ':' + a.text + ' / ' + b.kind + ':' + b.text), [], `${kind}#${seed}/${level}`);
      assert.ok(allFinite(labels));
      for (const l of labels.filter((x) => x.kind === 'room')) {
        const r = s.rooms.find((x) => x.id === l.id); const inside = s.cards.filter((c) => c.x >= r.x && c.x + c.w <= r.x + r.w && c.y >= r.y && c.y + c.h <= r.y + r.h);
        if (inside.length) assert.ok(l.box.y + l.box.h <= Math.min(...inside.map((c) => c.y)) + 1e-9, `${kind}/${level}: the room name ends above the cards`);
        assert.ok(l.box.y >= r.y && l.x >= r.x && l.box.x + l.box.w <= r.x + r.w + 1e-9, `${kind}/${level}: the room name stays inside its frame`);
      }
      floors += labels.filter((x) => x.kind === 'floor').length; checked += labels.length;
    }
  }
  assert.ok(checked > 400 && floors > 40, `checked ${checked} names, ${floors} floor names`);
  // the detector itself: the old placement (floor and room name in the same band) is reported
  const ctx = MV.viewContext(makeBundle('small', 1)); const s = TW.twodScene(ctx, { level: 'DD' });
  const good = TW.twodFrameLabels(s, 'U');
  const bad = good.map((l) => (l.kind === 'room' ? { ...l, box: { ...l.box, y: l.box.y - 13 } } : l));
  assert.ok(TW.twodLabelOverlaps(bad).length > 0, 'a room name moved into the floor name band is an overlap');
  assert.equal(TW.twodLabelOverlaps(good).length, 0);
  assert.equal(good.find((l) => l.kind === 'building' && l.text === 'U'), undefined, 'the unassigned name only appears when a building is unassigned');
});

test('2D level of detail: card text is dropped below the scale where it is under 7 px, never on a missing measure', () => {
  assert.equal(TW.twodTextHidden(0.3), true); assert.equal(TW.twodTextHidden(TW.TWOD_LOD_SCALE), false); assert.equal(TW.twodTextHidden(1.4), false);
  assert.equal(TW.twodTextHidden(null), false); assert.equal(TW.twodTextHidden(NaN), false);
  const view = { x: 0, y: 0, w: 2000, h: 1000 };
  assert.equal(TW.twodScale(view, 1000, 800), 0.5, 'the smaller ratio wins: width 1000 / 2000');
  assert.equal(TW.twodScale(view, 4000, 400), 0.4); assert.equal(TW.twodScale(view, 0, 0), null); assert.equal(TW.twodScale(view, NaN, 10), null);
});

test('2D viewport: fit takes the pane aspect, zoom keeps the point under the cursor and respects the limits, pan moves by pixels, never NaN', () => {
  const scene = { viewBox: { x: -20, y: -20, w: 1000, h: 500 } };
  const fit = TW.twodFit(scene, 800, 400); assert.deepEqual(fit, { x: -20, y: -20, w: 1000, h: 500 });
  const tall = TW.twodFit(scene, 400, 800); assert.ok(Math.abs(tall.w / tall.h - 0.5) < 1e-9); assert.ok(tall.x <= -20 && tall.x + tall.w >= 980 && tall.y <= -20 && tall.y + tall.h >= 480);
  const z = TW.twodZoom(fit, fit, 2, 0.25, 0.75); assert.equal(z.w, 500); assert.equal(z.h, 250);
  assert.ok(Math.abs((z.x + z.w * 0.25) - (fit.x + fit.w * 0.25)) < 1e-9 && Math.abs((z.y + z.h * 0.75) - (fit.y + fit.h * 0.75)) < 1e-9, 'the point under the cursor stays put');
  let v = fit; for (let i = 0; i < 80; i++) v = TW.twodZoom(v, fit, 2, 0.5, 0.5); assert.ok(fit.w / v.w <= TW.TWOD_ZOOM_MAX + 1e-9);
  v = fit; for (let i = 0; i < 80; i++) v = TW.twodZoom(v, fit, 0.5, 0.5, 0.5); assert.ok(fit.w / v.w >= TW.TWOD_ZOOM_MIN - 1e-9);
  assert.ok(allFinite(TW.twodZoom(fit, fit, NaN, NaN, undefined)) && allFinite(TW.twodZoom(fit, fit, -3, 9, -9)));
  assert.deepEqual(TW.twodPan(fit, 80, 40, 800, 400), { x: -120, y: -70, w: 1000, h: 500 }); assert.ok(allFinite(TW.twodPan(fit, NaN, 'x', 0, 0)));
  const c = TW.twodCenterOn(fit, { x: 100, y: 100, w: 150, h: 44 }); assert.equal(c.x + c.w / 2, 175); assert.equal(c.y + c.h / 2, 122);
  assert.equal(TW.twodViewBoxText({ x: 1, y: 2.345678, w: 3, h: 4 }), '1 2.35 3 4');
});

test('navigator tree: the listed nodes are exactly the nodes shown at the level, grouped by building, unplaced nodes in the last group; the filter narrows', opts, () => {
  for (const [kind, seed] of SCENARIOS) {
    const ctx = MV.viewContext(makeBundle(kind, seed));
    for (const level of ['REQ', 'DD']) {
      const t = NV.navigatorTree(ctx, level, '', null);
      const listed = t.groups.flatMap((g) => g.floors.flatMap((f) => f.rooms.flatMap((r) => r.nodes.map((n) => n.id))));
      assert.equal(new Set(listed).size, listed.length, `${kind}: a node is listed once`);
      assert.equal(listed.length, t.count); assert.equal(t.count, MA.shownAtLevel(ctx.model, level, ctx.vocab, ctx.ix).nodeIds.size, `${kind}/${level}`);
      assert.ok(t.groups.every((g, i) => !g.unassigned || i === t.groups.length - 1));
    }
  }
  const ctx = MV.viewContext(makeBundle('small', 3)); const word = ctx.nodes[2].name.split(' ')[0].toUpperCase();
  const t = NV.navigatorTree(ctx, 'DD', word, null); assert.ok(t.count > 0 && t.count < ctx.nodes.length);
  assert.equal(NV.navigatorTree(ctx, 'DD', 'nonexistent-term-x', null).count, 0);
  const u = MV.viewContext(makeBundle('edge:unknown-values', 5)); assert.ok(NV.navigatorTree(u, 'DD', '', null).count > 0);
});

test('inspector records: node, edge, case and requirement carry their facts, links resolve, nothing is undefined, unknown selections give null', opts, () => {
  const ctx = MV.viewContext(makeBundle('small', 3));
  const n = ctx.nodes.find((x) => ctx.edges.some((e) => e.sourceId === x.id));
  const dn = IN.inspectSelection(ctx, { type: 'node', id: n.id });
  assert.equal(dn.type, 'node'); assert.equal(dn.title, n.name); assert.equal(dn.fields.find((f) => f.key === 'id').value, n.id);
  assert.equal(dn.fields.find((f) => f.key === 'kind').vocab, 'kinds'); assert.ok(dn.links.length > 0); assert.ok(dn.links.every((l) => ctx.ix.edges.has(l.id) && ctx.ix.nodes.has(l.other.id)));
  assert.ok(dn.evidences.every((e) => e.known && e.file)); assert.ok(dn.fields.find((f) => f.key === 'placement').value.includes(' / '));
  const e = ctx.edges[0]; const de = IN.inspectSelection(ctx, { type: 'edge', id: e.id });
  assert.deepEqual(de.links.map((l) => l.id), [e.sourceId, e.targetId]); assert.equal(de.fields.find((f) => f.key === 'relation').value, e.relation);
  const c = ctx.cases[0]; const dc = IN.inspectSelection(ctx, { type: 'case', id: c.caseId });
  assert.equal(dc.fields.find((f) => f.key === 'events').value, String(c.events.length)); assert.equal(dc.fields.find((f) => f.key === 'sealed').flag, false); assert.ok(dc.links.length > 0);
  const r = ctx.requirements[0]; const dr = IN.inspectSelection(ctx, { type: 'requirement', id: r.id });
  assert.equal(dr.links.length, r.nodeIds.length + r.edgeIds.length);
  for (const d of [dn, de, dc, dr]) assert.ok(!/undefined|NaN|\[object/.test(JSON.stringify(d)), d.type);
  for (const bad of [null, {}, { type: 'node', id: 'nope' }, { type: 'room', id: 'x' }, { type: 'node' }, 'x']) assert.equal(IN.inspectSelection(ctx, bad), null);
  const withUnknown = makeBundle('small', 3); withUnknown.model.nodes[0].evidenceIds = ['no-such-evidence']; const du = IN.inspectSelection(MV.viewContext(withUnknown), { type: 'node', id: withUnknown.model.nodes[0].id });
  assert.equal(du.evidences[0].known, false);
});

test('view context: element kinds and titles, vocabulary labels with fallbacks, hostile bundles never throw', opts, () => {
  const ctx = MV.viewContext(makeBundle('lending'));
  const n = ctx.nodes[0]; const e = ctx.edges[0]; const c = ctx.cases[0]; const r = ctx.requirements[0];
  assert.deepEqual([n.id, e.id, c.caseId, r.id].map((id) => MV.viewElementKind(ctx, id)), ['node', 'edge', 'case', 'requirement']); assert.equal(MV.viewElementKind(ctx, 'zz'), null);
  assert.equal(MV.viewElementTitle(ctx, n.id), n.name); assert.equal(MV.viewElementTitle(ctx, e.id), e.label); assert.equal(MV.viewElementTitle(ctx, 'zz'), 'zz');
  assert.equal(MV.viewVocabLabel(ctx, 'kinds', n.kind, 'ja-JP').length > 0, true); assert.equal(MV.viewVocabLabel(ctx, 'kinds', 'NoSuchKind', 'en-US'), 'NoSuchKind'); assert.equal(MV.viewVocabLabel(ctx, 'nope', 'x', 'en-US'), 'x');
  assert.equal(MV.viewVocabLabel(ctx, 'levels', 'BD', 'en-US'), 'Basic design');
  assert.deepEqual(MV.viewCountBy([{ a: 'x' }, { a: 'x' }, { a: 'y' }, {}, null], 'a'), [{ id: 'x', count: 2 }, { id: 'y', count: 1 }, { id: '', count: 2 }]);
  for (const junk of [undefined, null, 0, 'x', [], { model: [] }, { model: { nodes: [null, 5, { id: 1 }], cases: 'x', requirements: [null] }, profile: 5, runs: { baselines: 3, pairs: 'x' }, i18n: 7 }]) assert.doesNotThrow(() => { const x = MV.viewContext(junk); QA.qaReport(x); TW.twodScene(x, {}); NV.navigatorTree(x, 'DD', '', null); DOC.docText(x, DOC.docInitial(x, 'BD', {})); });
  assert.equal(MV.viewHasSelection(ctx, { type: 'node', id: n.id }), true); assert.equal(MV.viewHasSelection(ctx, { type: 'node', id: 'zz' }), false); assert.equal(MV.viewHasSelection(ctx, { type: 'room', id: n.roomId }), false);
});
