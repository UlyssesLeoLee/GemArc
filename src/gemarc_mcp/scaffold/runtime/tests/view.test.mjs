/**
 * Tests for the cross-view shared state and the view router.
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/view.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { ViewState } from '../view/view-state.js';
import { ViewRouter } from '../view/view-router.js';

/**
 * A GraphStore-compatible adapter.
 *
 * The runtime store lives in `graph/graph-store.js` and is owned by a sibling
 * worker. That module imports `normalizeNode` / `normalizeEdge` from
 * `core/model.js`, which does not export them yet, so importing it can fail
 * while that work is still in flight. The real store is used as soon as it
 * loads; this adapter keeps the view layer under test in the meantime and
 * implements exactly the store API the runtime relies on.
 */
class StoreAdapter {
  constructor(model = null) {
    this.nodes = new Map();
    this.edges = new Map();
    this.outgoing = new Map();
    this.incoming = new Map();
    this.revision = 0;
    if (model) this.load(model);
  }

  load(model) {
    this.nodes.clear();
    this.edges.clear();
    this.outgoing.clear();
    this.incoming.clear();
    for (const node of model.nodes || []) this.addNode(node, { silent: true });
    for (const edge of model.edges || []) this.addEdge(edge, { silent: true });
    this.revision += 1;
    return this;
  }

  addNode(rawNode, { silent = false } = {}) {
    const node = {
      ...rawNode,
      properties: rawNode.properties || {},
      attributes: rawNode.attributes || [],
      methods: rawNode.methods || [],
      evidenceIds: rawNode.evidenceIds || [],
      verification: rawNode.verification || 'Unknown',
    };
    if (!node.id) throw new Error('node without id');
    if (this.nodes.has(node.id)) throw new Error('duplicate node');
    this.nodes.set(node.id, node);
    this.outgoing.set(node.id, new Set());
    this.incoming.set(node.id, new Set());
    if (!silent) this.revision += 1;
    return node;
  }

  addEdge(rawEdge, { silent = false } = {}) {
    const edge = {
      ...rawEdge,
      properties: rawEdge.properties || {},
      evidenceIds: rawEdge.evidenceIds || [],
      verification: rawEdge.verification || 'Unknown',
      directed: rawEdge.directed === undefined ? true : Boolean(rawEdge.directed),
    };
    if (!edge.id) throw new Error('edge without id');
    if (this.edges.has(edge.id)) throw new Error('duplicate edge');
    if (!this.nodes.has(edge.sourceId) || !this.nodes.has(edge.targetId)) {
      throw new Error('edge endpoint missing');
    }
    this.edges.set(edge.id, edge);
    this.outgoing.get(edge.sourceId).add(edge.id);
    this.incoming.get(edge.targetId).add(edge.id);
    if (!edge.directed) {
      this.outgoing.get(edge.targetId).add(edge.id);
      this.incoming.get(edge.sourceId).add(edge.id);
    }
    if (!silent) this.revision += 1;
    return edge;
  }

  getNode(id) {
    return this.nodes.get(id) || null;
  }

  getEdge(id) {
    return this.edges.get(id) || null;
  }

  hasNode(id) {
    return this.nodes.has(id);
  }

  hasEdge(id) {
    return this.edges.has(id);
  }

  get nodeCount() {
    return this.nodes.size;
  }

  get edgeCount() {
    return this.edges.size;
  }

  incidentEdges(nodeId) {
    const ids = new Set([
      ...(this.outgoing.get(nodeId) || []),
      ...(this.incoming.get(nodeId) || []),
    ]);
    return [...ids].map((id) => this.edges.get(id)).filter(Boolean);
  }

  outgoingEdges(nodeId) {
    return [...(this.outgoing.get(nodeId) || [])].map((id) => this.edges.get(id)).filter(Boolean);
  }

  incomingEdges(nodeId) {
    return [...(this.incoming.get(nodeId) || [])].map((id) => this.edges.get(id)).filter(Boolean);
  }

  toModel() {
    return { nodes: [...this.nodes.values()], edges: [...this.edges.values()] };
  }
}

/** Load the real store when it is available, the adapter otherwise. */
async function loadGraphStore() {
  try {
    const module = await import('../graph/graph-store.js');
    if (typeof module.GraphStore === 'function') return module.GraphStore;
  } catch {
    // The sibling module cannot be loaded yet.
  }
  return StoreAdapter;
}

const GraphStore = await loadGraphStore();

/**
 * A small but complete model: two classes, a service, a module and three
 * relations of different types.
 */
function buildModel() {
  return {
    meta: { schemaVersion: '0.2.0', language: 'zh-CN', evidenceState: 'Designed' },
    nodes: [
      {
        id: 'n-order',
        kind: 'Class',
        name: '订单',
        properties: { domain: '订单域' },
        attributes: [{ name: 'id', type: 'string', visibility: 'private' }],
        methods: [{ name: 'submit', visibility: 'public' }],
        verification: 'Designed',
        domainId: 'dom-order',
        role: 'Processor',
        spaceRef: { buildingId: 'b-1', floorId: 'f-1', roomId: 'r-1' },
      },
      {
        id: 'n-line',
        kind: 'Class',
        name: '订单行',
        attributes: [],
        methods: [],
        verification: 'Inferred',
        domainId: 'dom-order',
        role: 'Store',
      },
      {
        id: 'n-service',
        kind: 'Service',
        name: '订单服务',
        verification: 'Designed',
        domainId: 'dom-order',
      },
      {
        id: 'n-module',
        kind: 'Module',
        name: '订单模块',
        verification: 'Unknown',
      },
    ],
    edges: [
      { id: 'e-1', type: 'dependsOn', sourceId: 'n-order', targetId: 'n-line', verification: 'Designed' },
      { id: 'e-2', type: 'calls', sourceId: 'n-service', targetId: 'n-order', verification: 'Inferred' },
      { id: 'e-3', type: 'contains', sourceId: 'n-module', targetId: 'n-order', verification: 'Designed' },
    ],
  };
}

/** A renderer only reads the shared state; it never filters on its own. */
class MiniRenderer {
  constructor(state) {
    this.state = state;
  }

  render() {
    const view = this.state.filteredView();
    return { nodeIds: [...view.nodeIds], edgeIds: [...view.edgeIds] };
  }
}

test('ViewState notifies subscribers when a shared field changes', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const seen = [];
  const unsubscribe = state.subscribe((change) => seen.push(change));

  state.selectedNodeIds = ['n-order'];
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].fields, ['selectedNodeIds']);
  assert.deepEqual(seen[0].state.selectedNodeIds, ['n-order']);

  state.selectedEdgeIds = ['e-1'];
  assert.equal(seen.length, 2);
  assert.deepEqual(seen[1].fields, ['selectedEdgeIds']);

  unsubscribe();
  state.scenarioId = 'sc-1';
  assert.equal(seen.length, 2);
});

test('a batched change notifies once', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const seen = [];
  state.subscribe((change) => seen.push(change));

  state.batch((shared) => {
    shared.selectedNodeIds = ['n-order'];
    shared.selectedEdgeIds = ['e-1'];
    shared.scenarioId = 'sc-1';
  });

  assert.equal(seen.length, 1);
  assert.deepEqual([...seen[0].fields].sort(), ['scenarioId', 'selectedEdgeIds', 'selectedNodeIds']);
});

test('graphRevision follows the store revision', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  assert.equal(state.graphRevision, store.revision);

  const before = state.graphRevision;
  store.addNode({ id: 'n-extra', kind: 'Class', name: '附加类', verification: 'Designed' });
  assert.equal(state.graphRevision, store.revision);
  assert.notEqual(state.graphRevision, before);

  const seen = [];
  state.subscribe((change) => seen.push(change));
  state.syncGraphRevision();
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].fields, ['graphRevision']);
});

test('two renderers applying the shared filter produce identical id sets', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  state.applyFilter((target) => (target.type === 'node'
    ? target.node.kind === 'Class'
    : target.edge.type === 'dependsOn'));

  const renderer2d = new MiniRenderer(state);
  const renderer3d = new MiniRenderer(state);
  const first = renderer2d.render();
  const second = renderer3d.render();

  assert.deepEqual(first.nodeIds, second.nodeIds);
  assert.deepEqual(first.edgeIds, second.edgeIds);
  assert.deepEqual(first.nodeIds, ['n-order', 'n-line']);
  assert.deepEqual(first.edgeIds, ['e-1']);
});

test('a declarative filter in the shared state filters every renderer alike', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  state.setFilters({ kinds: ['Service'] });

  const rendered = new MiniRenderer(state).render();
  assert.deepEqual(rendered.nodeIds, ['n-service']);
  assert.deepEqual(rendered.edgeIds, []);
});

test('ViewRouter resolves the same node and edge ids for 2D and 3D', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const router = new ViewRouter({ store, state });
  const view = {
    id: 'v-class',
    title: '订单类图',
    diagramType: 'uml-class',
    nodeIds: ['n-order', 'n-line', 'n-service'],
    edgeKinds: ['dependsOn', 'calls'],
  };

  const twoD = router.resolve(view, { dimension: '2D' });
  const threeD = router.resolve(view, { dimension: '3D' });

  assert.deepEqual(twoD.nodeIds, threeD.nodeIds);
  assert.deepEqual(twoD.edgeIds, threeD.edgeIds);
  assert.equal(twoD.factFingerprint, threeD.factFingerprint);
  assert.deepEqual(twoD.nodeIds, ['n-order', 'n-line']);
  assert.deepEqual(twoD.edgeIds, ['e-1']);
  assert.equal(twoD.dimension, '2D');
  assert.equal(threeD.dimension, '3D');
});

test('ViewRouter reports an unregistered diagram type instead of throwing', () => {
  const store = new GraphStore(buildModel());
  const router = new ViewRouter({ store, state: new ViewState({ store }) });

  const result = router.resolve({ id: 'v-unknown', title: '未知图示', diagramType: 'no-such-diagram' });
  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].id, 'GEMARC_VIEW_002');
  assert.deepEqual(result.nodeIds, []);
  assert.deepEqual(result.edgeIds, []);
});

test('ViewRouter reports a node kind the diagram type forbids and leaves it out', () => {
  const store = new GraphStore(buildModel());
  const router = new ViewRouter({ store, state: new ViewState({ store }) });

  const result = router.resolve({
    id: 'v-class',
    title: '订单类图',
    diagramType: 'uml-class',
    nodeIds: ['n-order', 'n-service'],
  });

  const violation = result.violations.find((item) => item.id === 'GEMARC_VIEW_005');
  assert.ok(violation, 'expected a node kind violation');
  assert.equal(violation.entityId, 'n-service');
  assert.ok(!result.nodeIds.includes('n-service'));
  assert.deepEqual(result.nodeIds, ['n-order']);
});

test('ViewRouter reports missing fidelity instead of faking it', () => {
  const store = new GraphStore(buildModel());
  const router = new ViewRouter({ store, state: new ViewState({ store }) });

  const result = router.resolve({
    id: 'v-bare',
    title: '空类图',
    diagramType: 'uml-class',
    nodeIds: ['n-line'],
  });

  const missing = result.missing.filter((item) => item.id === 'GEMARC_VIEW_007');
  assert.ok(missing.length >= 1, 'expected a missing fidelity report');
  assert.ok(missing.some((item) => item.message.includes('attributes')));
  assert.ok(missing.some((item) => item.message.includes('methods')));
  assert.deepEqual(result.nodeIds, ['n-line']);
});

test('ViewRouter reports a 3D view without axis sources', () => {
  const store = new GraphStore(buildModel());
  const router = new ViewRouter({ store, state: new ViewState({ store }) });

  const withoutAxes = router.resolve({
    id: 'v-3d',
    title: '空间视图',
    diagramType: 'uml-class',
    nodeIds: ['n-order'],
  }, { dimension: '3D' });
  assert.ok(withoutAxes.missing.some((item) => item.id === 'GEMARC_VIEW_008'));

  const withAxes = router.resolve({
    id: 'v-3d',
    title: '空间视图',
    diagramType: 'uml-class',
    nodeIds: ['n-order'],
    axes: {
      x: { field: 'spaceRef.buildingId' },
      y: { field: 'spaceRef.floorId' },
      z: { field: 'spaceRef.roomId' },
    },
  }, { dimension: '3D' });
  assert.ok(!withAxes.missing.some((item) => item.id === 'GEMARC_VIEW_008'));
  assert.deepEqual(withAxes.nodeIds, withoutAxes.nodeIds);
});

test('ViewRouter rejects a structurally invalid view definition', () => {
  const store = new GraphStore(buildModel());
  const router = new ViewRouter({ store, state: new ViewState({ store }) });

  assert.throws(() => router.resolve({ title: '无 id' }), /GEMARC_VIEW_001/);
  assert.throws(() => router.resolve({ id: 'v-1', title: '无图示类型' }), /GEMARC_VIEW_001/);
  assert.doesNotThrow(() => router.tryResolve({ id: 'v-1', title: '无图示类型' }));
});

test('ViewRouter applies the shared filter to the view selection', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const router = new ViewRouter({ store, state });
  state.setFilters({ kinds: ['Class'] });

  const result = router.resolve({
    id: 'v-class',
    title: '订单类图',
    diagramType: 'uml-class',
    nodeIds: ['n-order', 'n-line', 'n-service'],
  });

  assert.deepEqual(result.nodeIds, ['n-order', 'n-line']);
});
