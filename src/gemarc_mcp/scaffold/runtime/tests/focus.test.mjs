/**
 * Focus–Context engine tests (v5.0 §7).
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/focus.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { relationClassSpec } from '../core/vocab.js';
import { normalizeModel } from '../core/model.js';
import {
  FocusContextEngine,
  RelevanceGraphEvaluator,
  RetreatAnimationController,
  ContextRestorationManager,
  createManualScheduler,
  resolveFocusTarget,
} from '../focus/index.js';
import { SpaceModel } from '../space/space-model.js';

/**
 * A GraphStore-compatible adapter.
 *
 * The runtime store lives in `graph/graph-store.js` and is owned by a sibling
 * worker. That module currently imports `normalizeNode` / `normalizeEdge`
 * from `core/model.js`, which does not export them, so importing it can fail
 * while the sibling work is still in flight. The real store is used as soon
 * as it loads; this adapter keeps the focus engine under test in the
 * meantime. It implements exactly the API the focus engine relies on.
 */
class StoreAdapter {
  constructor(model) {
    const normalized = normalizeModel(model);
    this.nodes = new Map(normalized.nodes.map((node) => [node.id, node]));
    this.edges = new Map(normalized.edges.map((edge) => [edge.id, edge]));
    this.outgoing = new Map();
    this.incoming = new Map();
    for (const node of normalized.nodes) {
      this.outgoing.set(node.id, new Set());
      this.incoming.set(node.id, new Set());
    }
    for (const edge of normalized.edges) {
      this.outgoing.get(edge.sourceId)?.add(edge.id);
      this.incoming.get(edge.targetId)?.add(edge.id);
      if (!edge.directed) {
        this.outgoing.get(edge.targetId)?.add(edge.id);
        this.incoming.get(edge.sourceId)?.add(edge.id);
      }
    }
  }

  getNode(id) {
    return this.nodes.get(id) || null;
  }

  getEdge(id) {
    return this.edges.get(id) || null;
  }

  neighbors(nodeId, { direction = 'out' } = {}) {
    const result = new Map();
    if (direction === 'out' || direction === 'both') {
      for (const id of this.outgoing.get(nodeId) || []) {
        const edge = this.edges.get(id);
        result.set(edge.sourceId === nodeId ? edge.targetId : edge.sourceId, edge);
      }
    }
    if (direction === 'in' || direction === 'both') {
      for (const id of this.incoming.get(nodeId) || []) {
        const edge = this.edges.get(id);
        result.set(edge.targetId === nodeId ? edge.sourceId : edge.targetId, edge);
      }
    }
    return result;
  }

  toModel() {
    return { nodes: [...this.nodes.values()], edges: [...this.edges.values()] };
  }
}

const { GraphStore } = await import('../graph/graph-store.js').catch(() => ({
  GraphStore: StoreAdapter,
}));

/**
 * A small graph plus a three-level space.
 *
 *   n1 --calls--> n2 --dependsOn--> n3        n1, n2, n6 share room r1
 *   n4 isolated, no room                      n5 sits in the neighbouring room r2
 *
 * n5 is spatially close to n1 (same floor, next room) but has no relation at
 * all, so it must stay `Unrelated`.
 */
function buildFixture() {
  const model = {
    meta: { schemaVersion: '0.2.0', language: 'zh-CN', evidenceState: 'Observed' },
    nodes: [
      { id: 'n1', kind: 'Service', name: 'Gateway', verification: 'Implemented' },
      { id: 'n2', kind: 'Module', name: 'Auth', verification: 'Implemented' },
      { id: 'n3', kind: 'Module', name: 'Session', verification: 'Designed' },
      { id: 'n4', kind: 'Module', name: 'Isolated', verification: 'Unknown' },
      { id: 'n5', kind: 'Service', name: 'OtherRoom', verification: 'Designed' },
      { id: 'n6', kind: 'Module', name: 'Peer', verification: 'Observed' },
    ],
    edges: [
      { id: 'e1', sourceId: 'n1', targetId: 'n2', type: 'calls' },
      { id: 'e2', sourceId: 'n2', targetId: 'n3', type: 'dependsOn' },
    ],
    space: {
      buildings: [
        {
          id: 'b1',
          name: 'Building A',
          kind: '系统',
          semanticSource: '架构层定义',
          nodeIds: [],
        },
      ],
      floors: [
        {
          id: 'f1',
          buildingId: 'b1',
          name: 'Floor 1',
          kind: '应用层',
          semanticSource: '架构层定义',
          nodeIds: [],
        },
      ],
      rooms: [
        { id: 'r1', floorId: 'f1', name: 'Room 1', kind: '模块组', semanticSource: '包清单', nodeIds: ['n1', 'n2', 'n6'] },
        { id: 'r2', floorId: 'f1', name: 'Room 2', kind: '模块组', semanticSource: '包清单', nodeIds: ['n5'] },
        { id: 'r3', floorId: 'f1', name: 'Room 3', kind: '模块组', semanticSource: '包清单', nodeIds: ['n3'] },
      ],
    },
  };
  const store = new GraphStore(model);
  const space = new SpaceModel(model);
  return { model, store, space };
}

function classOf(report, key) {
  return report.classes.get(key);
}

test('relevance classification: selected, directly related, contextual, unrelated', () => {
  const { store, space } = buildFixture();
  const evaluator = new RelevanceGraphEvaluator({ store, space });
  const resolved = resolveFocusTarget({ type: 'Node', id: 'n1' }, { store, space });

  const report = evaluator.evaluate(resolved);

  assert.equal(classOf(report, 'node:n1'), 'Selected');
  assert.equal(classOf(report, 'node:n2'), 'Directly Related');
  assert.equal(classOf(report, 'node:n3'), 'Contextually Related');
  assert.equal(classOf(report, 'node:n4'), 'Unrelated');
  assert.equal(report.counts.Selected, 1);
  assert.ok(report.counts['Contextually Related'] >= 1);
});

test('spatial distance alone never creates relevance', () => {
  const { store, space } = buildFixture();
  const evaluator = new RelevanceGraphEvaluator({ store, space });
  const resolved = resolveFocusTarget({ type: 'Node', id: 'n1' }, { store, space });

  const report = evaluator.evaluate(resolved);

  // n5 lives in the neighbouring room of the same floor and is unconnected.
  assert.equal(space.roomOfNode('n5'), 'r2');
  assert.equal(space.floorOfNode('n5'), space.floorOfNode('n1'));
  assert.notEqual(space.roomOfNode('n5'), space.roomOfNode('n1'));
  assert.equal(classOf(report, 'node:n5'), 'Unrelated');
  // A node that really shares the room is directly related.
  assert.equal(classOf(report, 'node:n6'), 'Directly Related');
});

test('retreat produces four distinct treatments and mutates nothing', () => {
  const { store, space } = buildFixture();
  const evaluator = new RelevanceGraphEvaluator({ store, space });
  const resolved = resolveFocusTarget({ type: 'Node', id: 'n1' }, { store, space });
  const report = evaluator.evaluate(resolved);

  const before = JSON.stringify(store.toModel());
  const applied = [];
  const controller = new RetreatAnimationController({
    applyTransform: (payload) => applied.push(payload),
    durationMs: 0,
  });
  controller.apply(report);

  const byKey = new Map(applied.map((payload) => [payload.key, payload.treatment]));
  const selected = byKey.get('node:n1');
  const direct = byKey.get('node:n2');
  const contextual = byKey.get('node:n3');
  const unrelated = byKey.get('node:n4');

  for (const [treatment, classId] of [
    [selected, 'Selected'],
    [direct, 'Directly Related'],
    [contextual, 'Contextually Related'],
    [unrelated, 'Unrelated'],
  ]) {
    const visual = relationClassSpec(classId).visual;
    assert.equal(treatment.opacity, visual.opacity);
    assert.equal(treatment.outline, visual.outline);
    assert.equal(treatment.labels, visual.labels);
    assert.equal(treatment.lineWeight, visual.lineWeight);
    assert.equal(treatment.zOffset, visual.zOffset);
  }

  // Four distinct treatments.
  const signatures = new Set(
    [selected, direct, contextual, unrelated].map((t) => JSON.stringify(t)),
  );
  assert.equal(signatures.size, 4);
  assert.equal(selected.collapsed, false);
  assert.equal(direct.collapsed, false);
  assert.equal(contextual.collapsed, true);
  assert.equal(unrelated.collapsed, true);

  // The retreat is a display transform only: the model is untouched.
  assert.equal(JSON.stringify(store.toModel()), before);
});

test('restore returns the original treatment exactly', () => {
  const { store, space } = buildFixture();
  const evaluator = new RelevanceGraphEvaluator({ store, space });
  const resolved = resolveFocusTarget({ type: 'Node', id: 'n1' }, { store, space });
  const report = evaluator.evaluate(resolved);

  const original = {
    opacity: 0.9,
    outline: 'normal',
    labels: 'full',
    lineWeight: 'normal',
    zOffset: 'none',
    collapsed: false,
    position: null,
  };
  const applied = [];
  const controller = new RetreatAnimationController({
    applyTransform: (payload) => applied.push(payload),
    readTreatment: (target) => (target.key === 'node:n4' ? { ...original } : null),
    durationMs: 0,
  });

  controller.apply(report);
  assert.ok(controller.getTreatment('node:n1'));
  controller.restore();

  // No leftover treatment.
  assert.equal(controller.getTreatment('node:n1'), null);
  assert.equal(controller.getTreatment('node:n4'), null);

  // The entity that had a treatment before the retreat gets exactly it back.
  const restored = new Map(applied.slice(-report.entries.length).map((p) => [p.key, p.treatment]));
  assert.deepEqual(restored.get('node:n4'), original);
  assert.equal(restored.get('node:n1'), null);
  assert.deepEqual(controller.getOriginal('node:n4'), original);
});

test('rapid successive focus changes cancel the in-flight transition', () => {
  const { store, space } = buildFixture();
  const evaluator = new RelevanceGraphEvaluator({ store, space });
  const first = evaluator.evaluate(resolveFocusTarget({ type: 'Node', id: 'n1' }, { store, space }));
  const second = evaluator.evaluate(resolveFocusTarget({ type: 'Node', id: 'n2' }, { store, space }));

  const scheduler = createManualScheduler();
  const applied = [];
  const completed = [];
  const controller = new RetreatAnimationController({
    applyTransform: (payload) => applied.push(payload),
    scheduler,
    durationMs: 120,
  });

  controller.apply(first, { onComplete: () => completed.push('first') });
  controller.apply(second, { onComplete: () => completed.push('second') });
  scheduler.flush();

  // The superseded transition never completes and never applies.
  assert.deepEqual(completed, ['second']);
  assert.equal(applied.length, second.entries.length);
  assert.ok(applied.every((payload) => payload.relationClass !== 'Selected' || payload.key !== 'node:n1'));
  assert.ok(applied.some((payload) => payload.key === 'node:n2'));
});

test('the engine cancels a pending transition when the focus changes again', () => {
  const { store, space } = buildFixture();
  const scheduler = createManualScheduler();
  const completed = [];
  const engine = new FocusContextEngine({
    store,
    space,
    animation: new RetreatAnimationController({ scheduler, durationMs: 150 }),
    options: { durationMs: 150 },
  });

  engine.focus({ type: 'Node', id: 'n1' }, { onComplete: () => completed.push('n1') });
  engine.focus({ type: 'Node', id: 'n2' }, { onComplete: () => completed.push('n2') });
  scheduler.flush();

  assert.deepEqual(completed, ['n2']);
  assert.equal(engine.current.target.id, 'n2');
});

test('the engine focuses objects that are not graph nodes through resolvers', () => {
  const { store, space } = buildFixture();
  const engine = new FocusContextEngine({
    store,
    space,
    resolvers: {
      WorkflowCase: (id) => ({ nodeIds: ['n2', 'n3'], scope: 'case' }),
      BranchPoint: (id) => ({ nodeIds: ['n1'], edgeIds: ['e1'] }),
    },
  });

  const session = engine.focus({ type: 'WorkflowCase', id: 'case-1' });
  assert.equal(session.target.type, 'WorkflowCase');
  assert.equal(session.report.classes.get('node:n2'), 'Contextually Related');
  assert.equal(session.report.classes.get('node:n1'), 'Unrelated');

  const branch = engine.focus({ type: 'BranchPoint', id: 'bp-1' });
  assert.equal(branch.report.classes.get('node:n1'), 'Directly Related');
  assert.equal(branch.report.classes.get('edge:e1'), 'Directly Related');
});

test('unknown focus targets and invalid focus types are reported with stable codes', () => {
  const { store, space } = buildFixture();
  const engine = new FocusContextEngine({ store, space });

  assert.throws(
    () => engine.focus({ type: 'Node', id: 'missing' }),
    (error) => error.code === 'GEMARC_FOCUS_001' && !String(error.message).includes('\\'),
  );
  assert.throws(
    () => engine.focus({ type: 'Satellite', id: 'n1' }),
    (error) => error.code === 'GEMARC_FOCUS_002',
  );
});

test('back, breadcrumb, lock and restore overview work', () => {
  const { store, space } = buildFixture();
  const engine = new FocusContextEngine({ store, space });

  engine.focus({ type: 'Node', id: 'n1' });
  engine.focus({ type: 'Node', id: 'n2' });
  assert.deepEqual(
    engine.breadcrumb().map((item) => item.targetId),
    ['n1', 'n2'],
  );

  const result = engine.back();
  assert.equal(result.restored.target.id, 'n1');
  assert.equal(engine.current.target.id, 'n1');

  engine.lockFocus(true);
  assert.throws(
    () => engine.focus({ type: 'Node', id: 'n3' }),
    (error) => error.code === 'GEMARC_FOCUS_003',
  );
  engine.lockFocus(false);

  engine.restoreOverview();
  assert.equal(engine.current, null);
  assert.equal(engine.breadcrumb().length, 1);

  engine.clearFocus();
  assert.equal(engine.breadcrumb().length, 0);
});

test('compare focus reports the class differences of two targets', () => {
  const { store, space } = buildFixture();
  const manager = new ContextRestorationManager({
    evaluator: new RelevanceGraphEvaluator({ store, space }),
    resolve: (target) => resolveFocusTarget(target, { store, space }),
  });

  const comparison = manager.compareFocus({ type: 'Node', id: 'n1' }, { type: 'Node', id: 'n2' });
  assert.ok(comparison.changed.length > 0);
  assert.ok(comparison.shared.length > 0);
  // Both reports cover the whole graph, so nothing is exclusive to one side.
  assert.equal(comparison.onlyA.length, 0);
  assert.equal(comparison.onlyB.length, 0);
  const n1 = comparison.changed.find((item) => item.key === 'node:n1');
  assert.equal(n1.from, 'Selected');
  assert.equal(n1.to, 'Directly Related');
});
