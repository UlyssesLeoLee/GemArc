/**
 * Tests for the graph indexes, the persistence boundary, the interaction
 * state machine and the dependency-free source scan.
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/graph.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { builtinModules } from 'node:module';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { GraphIndexManager } from '../graph/graph-index.js';
import {
  GraphPersistenceAdapter,
  createMemoryPersistenceAdapter,
  createPersistenceAdapter,
  listPersistenceAdapters,
  registerPersistenceAdapter,
} from '../graph/graph-persistence.js';
import { GraphInteractionController } from '../graph/graph-interaction.js';
import { createLayoutEngine, layeredLayout } from '../graph/graph-adapters.js';
import { ViewState } from '../view/view-state.js';

/**
 * A GraphStore-compatible adapter.
 *
 * The runtime store lives in `graph/graph-store.js` and is owned by a sibling
 * worker. That module imports `normalizeNode` / `normalizeEdge` from
 * `core/model.js`, which does not export them yet, so importing it can fail
 * while that work is still in flight. The real store is used as soon as it
 * loads; this adapter keeps the graph layer under test in the meantime and
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
 * The same fixture the view tests use: two classes, a service, a module and
 * three relations of different types.
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

test('GraphIndexManager indexes by kind, edge type, space and degree', () => {
  const store = new GraphStore(buildModel());
  const index = new GraphIndexManager(store);

  assert.deepEqual(index.nodesByKind('Class'), ['n-order', 'n-line']);
  assert.deepEqual(index.nodesByKind('Service'), ['n-service']);
  assert.deepEqual(index.edgesByType('dependsOn'), ['e-1']);
  assert.deepEqual(index.edgesByType('contains'), ['e-3']);
  assert.deepEqual(index.nodesByRoom('r-1'), ['n-order']);
  assert.deepEqual(index.nodesByFloor('f-1'), ['n-order']);
  assert.deepEqual(index.nodesByBuilding('b-1'), ['n-order']);
  assert.deepEqual(index.nodesByDomain('dom-order'), ['n-order', 'n-line', 'n-service']);
  assert.deepEqual(index.nodesByRole('Processor'), ['n-order']);
  assert.deepEqual(index.nodesByVerification('Inferred'), ['n-line']);
  assert.equal(index.degreeOf('n-order'), 3);
  assert.equal(index.degreeOf('n-line'), 1);
  assert.deepEqual(index.nodesByDegree(3), ['n-order']);
  assert.deepEqual(index.nodesWithDegreeAtLeast(3), ['n-order']);
});

test('GraphIndexManager reports that the store moved on', () => {
  const store = new GraphStore(buildModel());
  const index = new GraphIndexManager(store);
  assert.equal(index.isStale(), false);

  store.addNode({ id: 'n-extra', kind: 'Class', name: '附加类', verification: 'Designed' });
  assert.equal(index.isStale(), true);
  assert.throws(() => index.assertFresh(), /GEMARC_GRAPH_001/);

  assert.equal(index.ensureFresh(), true);
  assert.equal(index.isStale(), false);
  assert.deepEqual(index.nodesByKind('Class'), ['n-order', 'n-line', 'n-extra']);
  assert.equal(index.ensureFresh(), false);
});

test('the in-memory persistence adapter round trips every field', async () => {
  const model = buildModel();
  const adapter = new GraphPersistenceAdapter(createMemoryPersistenceAdapter());
  await adapter.open();
  await adapter.save(model);

  const loaded = await adapter.load();
  assert.deepEqual(loaded, model);
  assert.equal(loaded.nodes.length, 4);
  assert.equal(loaded.edges.length, 3);
  assert.deepEqual(loaded.nodes[0].attributes, [{ name: 'id', type: 'string', visibility: 'private' }]);
  assert.deepEqual(loaded.nodes[0].methods, [{ name: 'submit', visibility: 'public' }]);
  assert.deepEqual(loaded.nodes[0].spaceRef, { buildingId: 'b-1', floorId: 'f-1', roomId: 'r-1' });
  assert.deepEqual(loaded.nodes[0].properties, { domain: '订单域' });
  assert.equal(loaded.edges[0].sourceId, 'n-order');
  assert.equal(loaded.edges[0].targetId, 'n-line');
  assert.equal(loaded.edges[2].type, 'contains');
});

test('a persistence adapter reports the schema version it needs', async () => {
  const adapter = new GraphPersistenceAdapter(createMemoryPersistenceAdapter());
  await adapter.open();

  const empty = await adapter.info();
  assert.equal(empty.backend, 'memory');
  assert.equal(empty.schemaVersion, '1.0.0');
  assert.equal(empty.storedVersion, null);
  assert.equal(empty.needsMigration, false);
  assert.equal(empty.empty, true);

  await adapter.save(buildModel());
  const stored = await adapter.info();
  assert.equal(stored.storedVersion, '1.0.0');
  assert.equal(stored.needsMigration, false);
  assert.equal(stored.empty, false);
});

test('an older stored version is reported and can be migrated', async () => {
  class OldBackend {
    constructor() {
      this.name = 'old';
      this.schemaVersion = '0.9.0';
      this.record = null;
    }

    async open() {}

    async close() {}

    async saveModel(model) {
      this.record = { schemaVersion: this.schemaVersion, model };
      return { saved: true };
    }

    async loadModel() {
      return this.record ? this.record.model : null;
    }

    async clear() {
      this.record = null;
    }

    async storedVersion() {
      return this.record ? this.record.schemaVersion : null;
    }
  }

  const backend = new OldBackend();
  await backend.saveModel(buildModel());

  const adapter = new GraphPersistenceAdapter(backend, { schemaVersion: '1.0.0' });
  const before = await adapter.info();
  assert.equal(before.storedVersion, '0.9.0');
  assert.equal(before.needsMigration, true);

  adapter.registerMigration('0.9.0', '1.0.0', (model) => ({
    model: { ...model, meta: { ...model.meta, schemaVersion: '1.0.0' } },
    version: '1.0.0',
  }));

  const result = await adapter.migrate();
  assert.equal(result.migrated, true);
  assert.equal(result.from, '0.9.0');
  assert.equal(result.to, '1.0.0');
  assert.deepEqual(result.applied, ['0.9.0->1.0.0']);
  assert.equal((await adapter.load()).meta.schemaVersion, '1.0.0');
});

test('another persistence backend can be plugged in by name', () => {
  assert.ok(listPersistenceAdapters().includes('memory'));
  assert.ok(listPersistenceAdapters().includes('indexeddb'));

  class CustomBackend {
    constructor() {
      this.name = 'custom';
      this.schemaVersion = '1.0.0';
    }

    async saveModel() {}

    async loadModel() {
      return null;
    }
  }

  registerPersistenceAdapter('custom', () => new CustomBackend());
  const adapter = createPersistenceAdapter('custom');
  assert.ok(adapter instanceof CustomBackend);
  assert.ok(listPersistenceAdapters().includes('custom'));
  assert.throws(() => createPersistenceAdapter('not-registered'), /GEMARC_GRAPH_005/);
});

test('clicking an edge selects the edge, not its source node', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const controller = new GraphInteractionController({ store, state });

  controller.click({ type: 'edge', id: 'e-1' });

  assert.deepEqual(controller.selectedEdgeIds, ['e-1']);
  assert.deepEqual(controller.selectedNodeIds, []);
  assert.deepEqual(state.selectedEdgeIds, ['e-1']);
  assert.deepEqual(state.selectedNodeIds, []);
});

test('the controller supports multi-select and keeps the selection ordered', () => {
  const store = new GraphStore(buildModel());
  const state = new ViewState({ store });
  const controller = new GraphInteractionController({ store, state });

  controller.click({ type: 'node', id: 'n-order' });
  controller.click({ type: 'edge', id: 'e-1' }, { additive: true });

  assert.deepEqual(controller.selectedNodeIds, ['n-order']);
  assert.deepEqual(controller.selectedEdgeIds, ['e-1']);
  assert.deepEqual(state.selectedNodeIds, ['n-order']);
  assert.deepEqual(state.selectedEdgeIds, ['e-1']);
  assert.deepEqual(controller.selectionOrder(), [
    { type: 'node', id: 'n-order' },
    { type: 'edge', id: 'e-1' },
  ]);

  controller.toggle({ type: 'node', id: 'n-order' });
  assert.deepEqual(controller.selectedNodeIds, []);
  assert.deepEqual(controller.selectedEdgeIds, ['e-1']);
});

test('the controller keeps a hover state and emits events', () => {
  const store = new GraphStore(buildModel());
  const controller = new GraphInteractionController({ store });
  const events = [];
  controller.on('hover', (event) => events.push(event));

  controller.hover({ type: 'node', id: 'n-order' });
  assert.equal(controller.hoverTarget.type, 'node');
  assert.equal(controller.hoverTarget.id, 'n-order');

  controller.hover({ type: 'edge', id: 'e-1' });
  assert.equal(controller.hoverTarget.type, 'edge');

  controller.clearHover();
  assert.equal(controller.hoverTarget, null);
  assert.equal(events.length, 3);
});

test('the keyboard cursor walks through the selection', () => {
  const store = new GraphStore(buildModel());
  const controller = new GraphInteractionController({ store });

  controller.click({ type: 'node', id: 'n-order' });
  controller.click({ type: 'edge', id: 'e-1' }, { additive: true });

  assert.deepEqual(controller.cursorTarget(), { type: 'node', id: 'n-order' });
  assert.deepEqual(controller.moveCursor(1), { type: 'edge', id: 'e-1' });
  assert.deepEqual(controller.moveCursor(-1), { type: 'node', id: 'n-order' });
});

test('the built-in layered layout is deterministic and needs no dependency', () => {
  const store = new GraphStore(buildModel());
  const nodes = [...store.nodes.values()];
  const edges = [...store.edges.values()];

  const first = layeredLayout(nodes, edges);
  const second = layeredLayout(nodes, edges);
  assert.deepEqual([...first.positions.entries()], [...second.positions.entries()]);
  assert.equal(first.positions.get('n-order').x, 0);
  assert.ok(first.positions.get('n-line').y > first.positions.get('n-order').y);

  const engine = createLayoutEngine();
  assert.equal(engine.name, 'layered');
  assert.deepEqual([...engine.layout(nodes, edges).positions.keys()], [...first.positions.keys()]);
});

/**
 * The import patterns used by the source scan.
 *
 * A static import or re-export is matched with the clause anchored to the
 * start of a line, which is how real import statements look. The dynamic
 * form may sit anywhere on a line, so it is matched on its own and is then
 * checked against the comment rules below.
 */
const IMPORT_PATTERNS = [
  /^[ \t]*import\s+[^'";]*?from\s*['"]([^'"]+)['"]/gm,
  /^[ \t]*export\s+[^'";]*?from\s*['"]([^'"]+)['"]/gm,
  /^[ \t]*import\s+['"]([^'"]+)['"]/gm,
  /(?:^|[^.\w$])import\s*\(\s*['"]([^'"]+)['"]\s*\)/gm,
];

const NODE_BUILTINS = new Set([...builtinModules, ...builtinModules.map((name) => `node:${name}`)]);

/**
 * The regions of the source that matter for the scan: the block comments and
 * the string literals.
 *
 * Both are collected in one pass, so a `/*` inside a string literal is not
 * mistaken for a comment and a quote inside a comment is not mistaken for a
 * string. A quote that follows the slash of a regular expression literal
 * (`/"/g`) is not a string delimiter either, otherwise the scan would lose
 * the rest of the file.
 * @param {string} source the source to scan
 * @returns {{comments: Array<[number, number]>, strings: Array<[number, number]>}}
 */
function sourceRegions(source) {
  const comments = [];
  const strings = [];
  let index = 0;
  while (index < source.length) {
    const char = source[index];
    if (char === '/' && source[index + 1] === '/') {
      const end = source.indexOf('\n', index);
      const stop = end < 0 ? source.length : end;
      comments.push([index, stop]);
      index = stop;
      continue;
    }
    if (char === '/' && source[index + 1] === '*') {
      const end = source.indexOf('*/', index + 2);
      const stop = end < 0 ? source.length : end + 2;
      comments.push([index, stop]);
      index = stop;
      continue;
    }
    if (char === '"' || char === "'" || char === '`') {
      const previous = index > 0 ? source[index - 1] : '';
      const beforePrevious = index > 1 ? source[index - 2] : '';
      if (previous === '/' && beforePrevious !== '/' && beforePrevious !== '*') {
        index += 1;
        continue;
      }
      let cursor = index + 1;
      while (cursor < source.length) {
        if (source[cursor] === '\\') {
          cursor += 2;
          continue;
        }
        if (source[cursor] === char) break;
        cursor += 1;
      }
      strings.push([index, Math.min(cursor + 1, source.length)]);
      index = cursor + 1;
      continue;
    }
    index += 1;
  }
  return { comments, strings };
}

/**
 * True when the offset sits inside one of the given regions.
 * @param {number} offset the offset in the source
 * @param {Array<[number, number]>} regions the regions to test
 */
function isInside(offset, regions) {
  return regions.some(([start, end]) => offset >= start && offset < end);
}

/**
 * Find the bare module specifiers of one source text.
 * @param {string} source the source to scan
 * @param {string[]} [allowed] specifiers that are allowed anyway
 * @returns {string[]} the bare specifiers, in the order they appear
 */
function findBareModules(source, allowed = []) {
  const { comments, strings } = sourceRegions(source);
  const found = [];
  for (const pattern of IMPORT_PATTERNS) {
    pattern.lastIndex = 0;
    let match = pattern.exec(source);
    while (match !== null) {
      const keyword = match[0].indexOf('import');
      const offset = keyword >= 0 ? match.index + keyword : match.index;
      const specifier = match[1];
      const isRelative = specifier.startsWith('.') || specifier.startsWith('/');
      const isBuiltin = NODE_BUILTINS.has(specifier) || specifier.startsWith('node:');
      const commented = isInside(offset, comments);
      const quoted = isInside(offset, strings);
      if (!commented && !quoted && !isRelative && !isBuiltin && !allowed.includes(specifier)) {
        found.push(specifier);
      }
      match = pattern.exec(source);
    }
  }
  return found;
}

/**
 * List every source file of a directory tree.
 * @param {string} directory the root of the tree
 * @returns {string[]} the absolute paths of the source files
 */
function listSourceFiles(directory) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...listSourceFiles(full));
    else if (/\.(js|mjs|cjs)$/.test(entry.name)) files.push(full);
  }
  return files;
}

/**
 * Scan a whole runtime tree for bare module imports.
 * @param {string} runtimeRoot the root of the tree
 * @param {string[]} [allowed] specifiers that are allowed anyway
 * @returns {string[]} the violations, as `<file>: <specifier>`
 */
function findRuntimeViolations(runtimeRoot, allowed = []) {
  const violations = [];
  for (const file of listSourceFiles(runtimeRoot)) {
    const source = readFileSync(file, 'utf8');
    for (const specifier of findBareModules(source, allowed)) {
      violations.push(`${path.relative(runtimeRoot, file)}: ${specifier}`);
    }
  }
  return violations;
}

test('the import scanner ignores prose that only looks like an import', () => {
  const source = [
    '/**',
    ' * Example ER document:',
    ' *   { "id": "rel-1", "from": "customer", "to": "order", "kind": "owns",',
    ' *     "cardinality": { "from": "1", "to": "N" } }',
    ' */',
    "import { GemArcError } from '../core/errors.js';",
  ].join('\n');
  assert.deepEqual(findBareModules(source), []);
});

test('the import scanner ignores a commented out import', () => {
  assert.deepEqual(findBareModules("/*\nimport x from 'some-package';\n*/"), []);
  assert.deepEqual(findBareModules("// import x from 'some-package';"), []);
  assert.deepEqual(findBareModules(" * import x from 'some-package';"), []);
  assert.deepEqual(findBareModules("// const m = await import('some-package');"), []);
});

test('the import scanner still catches a real bare module import', () => {
  assert.deepEqual(findBareModules("import x from 'some-package';"), ['some-package']);
  assert.deepEqual(findBareModules("import {\n  a,\n  b,\n} from 'some-package';"), ['some-package']);
  assert.deepEqual(findBareModules("export { x } from 'some-package';"), ['some-package']);
  assert.deepEqual(findBareModules("const m = await import('some-package');"), ['some-package']);
  assert.deepEqual(findBareModules("import 'some-package';"), ['some-package']);
  assert.deepEqual(findBareModules("import x from './local.js';"), []);
  assert.deepEqual(findBareModules("import x from '../core/errors.js';"), []);
  assert.deepEqual(findBareModules("import test from 'node:test';"), []);
});

test('the runtime imports no bare module', () => {
  const runtimeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  // No third-party package is allowed anywhere in the runtime.
  const allowedBareModules = [];
  assert.deepEqual(findRuntimeViolations(runtimeRoot, allowedBareModules), []);
});
