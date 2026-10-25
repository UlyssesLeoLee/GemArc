/**
 * Cross-view shared state.
 *
 * Every view in the runtime (2D diagram, 3D space, workflow canvas,
 * simulation, interop panel) reads one `ViewState` instance. The state owns
 * the facts that must not drift between views:
 *
 *   selectedNodeIds / selectedEdgeIds  the current selection
 *   activePath                         the ordered path under inspection
 *   filters                            the one filter every renderer applies
 *   graphRevision                      the graph revision the state reflects
 *   focusContext                       an opaque value owned by the focus engine
 *   scenarioId                         the scenario (4D/5D) currently in play
 *
 * Two rules shape the file:
 *
 *  1. A renderer never filters on its own. It asks `filteredView()` for the
 *     id sets. Two renderers asking the same state therefore always see the
 *     same facts - that is what makes "switching between 2D and 3D must not
 *     change the graph data facts" hold.
 *  2. One user action is one notification. `batch()` collects every field
 *     change of that action and flushes a single change event at the end.
 *
 * The state never imports a renderer, a DOM API or a third-party package.
 */
import { GemArcError } from '../core/errors.js';
import { canonicalizeEvidenceLevel, evidenceRank } from '../core/vocab.js';

/** The shared field names a change event may report. */
export const VIEW_STATE_FIELDS = Object.freeze([
  'selectedNodeIds',
  'selectedEdgeIds',
  'activePath',
  'filters',
  'graphRevision',
  'focusContext',
  'scenarioId',
]);

/** Read a dotted path from a plain object; returns undefined when absent. */
export function readModelPath(object, path) {
  if (object === null || object === undefined) return undefined;
  if (!path.includes('.')) return object[path];
  let cursor = object;
  for (const part of path.split('.')) {
    if (cursor === null || cursor === undefined) return undefined;
    cursor = cursor[part];
  }
  return cursor;
}

function toIdArray(value) {
  if (!Array.isArray(value)) return [];
  const result = [];
  const seen = new Set();
  for (const item of value) {
    if (typeof item !== 'string' || item === '') continue;
    if (seen.has(item)) continue;
    seen.add(item);
    result.push(item);
  }
  return result;
}

function sameIds(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

function normalizePath(value) {
  if (!value) return { nodeIds: [], edgeIds: [] };
  if (Array.isArray(value)) return { nodeIds: toIdArray(value), edgeIds: [] };
  return { nodeIds: toIdArray(value.nodeIds), edgeIds: toIdArray(value.edgeIds) };
}

/**
 * Declarative filter keys. A renderer may read them for display, but the
 * selection itself is always computed here, never in the renderer.
 */
const DECLARATIVE_FILTERS = {
  /** Keep nodes and edges whose evidence level equals the given level. */
  verification: (value) => (target) => {
    const wanted = canonicalizeEvidenceLevel(value);
    const actual = target.type === 'node' ? target.node.verification : target.edge.verification;
    return canonicalizeEvidenceLevel(actual) === wanted;
  },
  /** Keep entities whose evidence rank is at least the given rank. */
  minVerificationRank: (value) => (target) => {
    const actual = target.type === 'node' ? target.node.verification : target.edge.verification;
    return evidenceRank(actual) >= Number(value);
  },
  /** Keep nodes whose kind is listed. Edges are not affected. */
  kinds: (value) => (target) => target.type !== 'node' || value.includes(target.node.kind),
  /** Keep nodes whose domain id is listed. Edges are not affected. */
  domains: (value) => (target) => target.type !== 'node' || value.includes(target.node.domainId),
  /** Keep nodes whose role is listed. Edges are not affected. */
  roles: (value) => (target) => target.type !== 'node' || value.includes(target.node.role),
  /** Keep edges whose type is listed. Nodes are not affected. */
  edgeTypes: (value) => (target) => target.type !== 'edge' || value.includes(target.edge.type),
};

function passesAll(predicates, target) {
  for (const predicate of predicates) {
    if (!predicate(target)) return false;
  }
  return true;
}

/**
 * The shared state every view reads. One instance is owned by the
 * application; the views only read it.
 */
export class ViewState {
  /**
   * @param {{store?: object, selectedNodeIds?: string[], selectedEdgeIds?: string[],
   *   activePath?: {nodeIds?: string[], edgeIds?: string[]}, filters?: object,
   *   focusContext?: unknown, scenarioId?: string|null}} [options]
   */
  constructor(options = {}) {
    this.store = options.store || null;
    this._listeners = new Set();
    this._predicates = [];
    this._batchDepth = 0;
    this._pending = new Set();
    this._version = 0;

    this._selectedNodeIds = toIdArray(options.selectedNodeIds);
    this._selectedEdgeIds = toIdArray(options.selectedEdgeIds);
    this._activePath = normalizePath(options.activePath);
    this._filters = { ...(options.filters || {}) };
    this._focusContext = options.focusContext ?? null;
    this._scenarioId = options.scenarioId ?? null;
    this._graphRevision = this.store ? this.store.revision : 0;
  }

  // --- shared fields -------------------------------------------------------

  /** @returns {string[]} the selected node ids, in selection order. */
  get selectedNodeIds() {
    return this._selectedNodeIds;
  }

  set selectedNodeIds(value) {
    const next = toIdArray(value);
    if (sameIds(next, this._selectedNodeIds)) return;
    this._selectedNodeIds = next;
    this._touch('selectedNodeIds');
  }

  /** @returns {string[]} the selected edge ids, in selection order. */
  get selectedEdgeIds() {
    return this._selectedEdgeIds;
  }

  set selectedEdgeIds(value) {
    const next = toIdArray(value);
    if (sameIds(next, this._selectedEdgeIds)) return;
    this._selectedEdgeIds = next;
    this._touch('selectedEdgeIds');
  }

  /** @returns {{nodeIds: string[], edgeIds: string[]}} the ordered path under inspection. */
  get activePath() {
    return { nodeIds: [...this._activePath.nodeIds], edgeIds: [...this._activePath.edgeIds] };
  }

  set activePath(value) {
    const next = normalizePath(value);
    if (sameIds(next.nodeIds, this._activePath.nodeIds) && sameIds(next.edgeIds, this._activePath.edgeIds)) {
      return;
    }
    this._activePath = next;
    this._touch('activePath');
  }

  /** @returns {object} the shared filter settings. Treat as read-only. */
  get filters() {
    return { ...this._filters };
  }

  set filters(value) {
    this._filters = { ...(value || {}) };
    this._touch('filters');
  }

  /** @returns {unknown} the opaque focus context owned by the focus engine. */
  get focusContext() {
    return this._focusContext;
  }

  set focusContext(value) {
    if (this._focusContext === value) return;
    this._focusContext = value;
    this._touch('focusContext');
  }

  /** @returns {string|null} the scenario currently in play. */
  get scenarioId() {
    return this._scenarioId;
  }

  set scenarioId(value) {
    const next = value === undefined || value === null ? null : String(value);
    if (this._scenarioId === next) return;
    this._scenarioId = next;
    this._touch('scenarioId');
  }

  /**
   * The revision of the underlying graph. When a store is attached the value
   * tracks `store.revision` live, so every view knows which facts it shows.
   */
  get graphRevision() {
    return this.store ? this.store.revision : this._graphRevision;
  }

  set graphRevision(value) {
    const next = Number.isFinite(value) ? Number(value) : 0;
    if (next === this._graphRevision) return;
    this._graphRevision = next;
    this._touch('graphRevision');
  }

  // --- subscriptions -------------------------------------------------------

  /**
   * Subscribe to state changes.
   * @param {(change: {fields: string[], version: number, state: object}) => void} listener
   * @param {{fields?: string[]}} [options] restrict the listener to some fields
   * @returns {() => void} an unsubscribe function
   */
  subscribe(listener, options = {}) {
    if (typeof listener !== 'function') {
      throw new GemArcError('GEMARC_VIEW_013', '订阅者必须是函数');
    }
    const fields = options.fields ? new Set(options.fields) : null;
    const entry = { listener, fields };
    this._listeners.add(entry);
    return () => {
      this._listeners.delete(entry);
    };
  }

  /**
   * Run `fn` and emit a single change event for every field it touched.
   * Nested batches emit once, when the outermost batch ends.
   */
  batch(fn) {
    if (typeof fn !== 'function') {
      throw new GemArcError('GEMARC_VIEW_013', '批量操作必须传入函数');
    }
    this._batchDepth += 1;
    try {
      return fn(this);
    } finally {
      this._batchDepth -= 1;
      if (this._batchDepth === 0) this._flush();
    }
  }

  /** Attach a graph store; the state then follows the store revision. */
  attachStore(store) {
    this.store = store || null;
    this._graphRevision = this.store ? this.store.revision : 0;
    this._touch('graphRevision');
    return this;
  }

  /**
   * Refresh the cached graph revision and notify when the graph changed.
   * Call it after a mutation that bypassed the store.
   */
  syncGraphRevision() {
    if (!this.store) return this.graphRevision;
    const previous = this._graphRevision;
    this._graphRevision = this.store.revision;
    if (previous !== this._graphRevision) this._touch('graphRevision');
    return this._graphRevision;
  }

  /** A plain copy of the shared state, safe to hand to a renderer. */
  snapshot() {
    return {
      selectedNodeIds: [...this._selectedNodeIds],
      selectedEdgeIds: [...this._selectedEdgeIds],
      activePath: { nodeIds: [...this._activePath.nodeIds], edgeIds: [...this._activePath.edgeIds] },
      filters: { ...this._filters },
      graphRevision: this.graphRevision,
      focusContext: this._focusContext,
      scenarioId: this._scenarioId,
    };
  }

  // --- mutation helpers ----------------------------------------------------

  /** Replace the whole selection in one action. */
  setSelection(nodeIds = [], edgeIds = []) {
    this.batch(() => {
      this.selectedNodeIds = nodeIds;
      this.selectedEdgeIds = edgeIds;
    });
    return this;
  }

  /** Replace the active path in one action. */
  setActivePath(nodeIds = [], edgeIds = []) {
    this.batch(() => {
      this.activePath = { nodeIds, edgeIds };
    });
    return this;
  }

  /** Clear the active path. */
  clearActivePath() {
    this.activePath = { nodeIds: [], edgeIds: [] };
    return this;
  }

  /** Replace the shared filter settings. */
  setFilters(filters) {
    this.filters = filters;
    return this;
  }

  /** Set one shared filter setting. */
  setFilter(name, value) {
    this.filters = { ...this._filters, [name]: value };
    return this;
  }

  /** Clear the shared filter settings; registered predicates survive. */
  clearFilters() {
    this.filters = {};
    return this;
  }

  /**
   * Register a filter predicate. Every renderer that asks `filteredView()`
   * then sees the same ids.
   * @param {(target: {type: 'node'|'edge', id: string, node?: object, edge?: object}) => boolean} predicate
   * @returns {() => void} a function that removes the predicate again
   */
  applyFilter(predicate) {
    if (typeof predicate !== 'function') {
      throw new GemArcError('GEMARC_VIEW_013', '过滤器必须是函数');
    }
    this._predicates.push(predicate);
    this._touch('filters');
    return () => this.removeFilter(predicate);
  }

  /** Remove a previously registered filter predicate. */
  removeFilter(predicate) {
    const index = this._predicates.indexOf(predicate);
    if (index < 0) return false;
    this._predicates.splice(index, 1);
    this._touch('filters');
    return true;
  }

  /** The predicate chain currently in force. */
  activeFilters() {
    const predicates = [];
    for (const [key, value] of Object.entries(this._filters)) {
      if (typeof value === 'function') {
        predicates.push(value);
        continue;
      }
      const factory = DECLARATIVE_FILTERS[key];
      if (factory) predicates.push(factory(value));
    }
    return predicates.concat(this._predicates);
  }

  // --- the one filter every renderer applies -------------------------------

  /**
   * The filtered id sets. A renderer must use this method and never filter on
   * its own, otherwise two views can show different facts.
   * @param {{base?: {nodeIds?: string[], edgeIds?: string[]}}} [options]
   * @returns {{nodeIds: string[], edgeIds: string[], nodes: object[], edges: object[]}}
   */
  filteredView(options = {}) {
    const store = this.store;
    if (!store) return { nodeIds: [], edgeIds: [], nodes: [], edges: [] };

    const predicates = this.activeFilters();
    const base = options.base || null;
    const baseNodeIds = base && base.nodeIds ? base.nodeIds : [...store.nodes.keys()];
    const baseEdgeIds = base && base.edgeIds ? base.edgeIds : [...store.edges.keys()];

    const nodeIds = [];
    for (const id of baseNodeIds) {
      const node = store.getNode(id);
      if (!node) continue;
      if (!passesAll(predicates, { type: 'node', id, node, state: this })) continue;
      nodeIds.push(id);
    }

    const kept = new Set(nodeIds);
    const edgeIds = [];
    for (const id of baseEdgeIds) {
      const edge = store.getEdge(id);
      if (!edge) continue;
      if (!kept.has(edge.sourceId) || !kept.has(edge.targetId)) continue;
      if (!passesAll(predicates, { type: 'edge', id, edge, state: this })) continue;
      edgeIds.push(id);
    }

    return {
      nodeIds,
      edgeIds,
      nodes: nodeIds.map((id) => store.getNode(id)),
      edges: edgeIds.map((id) => store.getEdge(id)),
    };
  }

  // --- internals -----------------------------------------------------------

  _touch(field) {
    this._pending.add(field);
    if (this._batchDepth > 0) return;
    this._flush();
  }

  _flush() {
    if (this._pending.size === 0) return;
    const fields = [...this._pending];
    this._pending.clear();
    this._version += 1;
    const change = { fields, version: this._version, state: this.snapshot() };
    let firstError = null;
    for (const entry of [...this._listeners]) {
      if (entry.fields && !fields.some((field) => entry.fields.has(field))) continue;
      try {
        entry.listener(change);
      } catch (error) {
        // A broken subscriber must not stop the other subscribers, but the
        // failure is still reported to the caller.
        if (!firstError) firstError = error;
      }
    }
    if (firstError) throw firstError;
  }
}
