/**
 * Secondary indexes over a `GraphStore`.
 *
 * The store answers "give me this node" in O(1) but has no answer for "give
 * me every node of kind X" or "which nodes live in room Y". `GraphIndexManager`
 * builds those answers once and keeps them cheap.
 *
 * The index is a cache, never a source of truth. It remembers the store
 * revision it was built from; when the store moves on, `isStale()` reports it
 * and the caller decides whether to rebuild (`ensureFresh`) or to fail
 * (`assertFresh`).
 */
import { GemArcError } from '../core/errors.js';

function emptyIndexes() {
  return {
    byKind: new Map(),
    byEdgeType: new Map(),
    byDomain: new Map(),
    byRole: new Map(),
    byBuilding: new Map(),
    byFloor: new Map(),
    byRoom: new Map(),
    byVerification: new Map(),
    byEdgeVerification: new Map(),
    byDegree: new Map(),
    degree: new Map(),
  };
}

function push(map, key, value) {
  if (key === null || key === undefined || key === '') return;
  const bucket = map.get(key);
  if (bucket) bucket.push(value);
  else map.set(key, [value]);
}

/**
 * The secondary indexes over a graph store.
 */
export class GraphIndexManager {
  /**
   * @param {import('./graph-store.js').GraphStore} store the graph to index
   * @param {{autoRebuild?: boolean}} [options] rebuild automatically on a stale lookup
   */
  constructor(store, options = {}) {
    if (!store) throw new GemArcError('GEMARC_GRAPH_001', '索引需要一个 GraphStore 实例');
    this.store = store;
    this.autoRebuild = options.autoRebuild !== false;
    this.revision = -1;
    this.indexes = emptyIndexes();
    this.rebuild();
  }

  /** Rebuild every index from the store. */
  rebuild() {
    const indexes = emptyIndexes();
    for (const node of this.store.nodes.values()) {
      push(indexes.byKind, node.kind, node.id);
      push(indexes.byDomain, node.domainId, node.id);
      push(indexes.byRole, node.role, node.id);
      push(indexes.byVerification, node.verification, node.id);
      const space = node.spaceRef || null;
      if (space && typeof space === 'object') {
        push(indexes.byBuilding, space.buildingId, node.id);
        push(indexes.byFloor, space.floorId, node.id);
        push(indexes.byRoom, space.roomId, node.id);
      }
    }
    for (const edge of this.store.edges.values()) {
      push(indexes.byEdgeType, edge.type, edge.id);
      push(indexes.byEdgeVerification, edge.verification, edge.id);
    }
    for (const nodeId of this.store.nodes.keys()) {
      const degree = this.store.incidentEdges(nodeId).length;
      indexes.degree.set(nodeId, degree);
      push(indexes.byDegree, degree, nodeId);
    }
    this.indexes = indexes;
    this.revision = this.store.revision;
    return this;
  }

  /** True when the store changed since the index was built. */
  isStale() {
    return this.store.revision !== this.revision;
  }

  /**
   * Rebuild when the store changed.
   * @returns {boolean} true when a rebuild happened
   */
  ensureFresh() {
    if (!this.isStale()) return false;
    this.rebuild();
    return true;
  }

  /**
   * Fail loudly when the index no longer matches the store.
   * @throws {GemArcError} GEMARC_GRAPH_001 when the index is out of date
   */
  assertFresh() {
    if (this.isStale()) {
      throw new GemArcError('GEMARC_GRAPH_001', '索引已过期，需要重建', String(this.revision));
    }
    return true;
  }

  // --- lookups -------------------------------------------------------------

  /** @returns {string[]} the ids of every node of the given kind. */
  nodesByKind(kind) {
    this._guard();
    return [...(this.indexes.byKind.get(kind) || [])];
  }

  /** @returns {string[]} the ids of every edge of the given type. */
  edgesByType(type) {
    this._guard();
    return [...(this.indexes.byEdgeType.get(type) || [])];
  }

  /** @returns {string[]} the ids of every node in the given business domain. */
  nodesByDomain(domainId) {
    this._guard();
    return [...(this.indexes.byDomain.get(domainId) || [])];
  }

  /** @returns {string[]} the ids of every node with the given role. */
  nodesByRole(role) {
    this._guard();
    return [...(this.indexes.byRole.get(role) || [])];
  }

  /** @returns {string[]} the ids of every node in the given building. */
  nodesByBuilding(buildingId) {
    this._guard();
    return [...(this.indexes.byBuilding.get(buildingId) || [])];
  }

  /** @returns {string[]} the ids of every node on the given floor. */
  nodesByFloor(floorId) {
    this._guard();
    return [...(this.indexes.byFloor.get(floorId) || [])];
  }

  /** @returns {string[]} the ids of every node in the given room. */
  nodesByRoom(roomId) {
    this._guard();
    return [...(this.indexes.byRoom.get(roomId) || [])];
  }

  /**
   * @param {{buildingId?: string, floorId?: string, roomId?: string}} space
   * @returns {string[]} the ids of every node inside the given space slice
   */
  nodesBySpace(space = {}) {
    this._guard();
    const ids = new Set();
    for (const key of ['buildingId', 'floorId', 'roomId']) {
      const value = space[key];
      if (value === null || value === undefined || value === '') continue;
      for (const id of this.indexes[
        key === 'buildingId' ? 'byBuilding' : key === 'floorId' ? 'byFloor' : 'byRoom'
      ].get(value) || []) {
        ids.add(id);
      }
    }
    return [...ids];
  }

  /** @returns {string[]} the ids of every node with the given evidence level. */
  nodesByVerification(level) {
    this._guard();
    return [...(this.indexes.byVerification.get(level) || [])];
  }

  /** @returns {string[]} the ids of every edge with the given evidence level. */
  edgesByVerification(level) {
    this._guard();
    return [...(this.indexes.byEdgeVerification.get(level) || [])];
  }

  /** @returns {number} the number of edges incident to the node. */
  degreeOf(nodeId) {
    this._guard();
    return this.indexes.degree.get(nodeId) || 0;
  }

  /**
   * @param {number} degree the exact degree to look for
   * @returns {string[]} the ids of every node with that degree
   */
  nodesByDegree(degree) {
    this._guard();
    return [...(this.indexes.byDegree.get(degree) || [])];
  }

  /**
   * @param {number} minDegree the lowest degree to accept
   * @returns {string[]} the ids of every node at or above that degree
   */
  nodesWithDegreeAtLeast(minDegree) {
    this._guard();
    const result = [];
    for (const [nodeId, degree] of this.indexes.degree) {
      if (degree >= minDegree) result.push(nodeId);
    }
    return result;
  }

  /** @returns {Map<number, string[]>} the degree buckets of the graph. */
  degreeBuckets() {
    this._guard();
    const buckets = new Map();
    for (const [degree, ids] of this.indexes.byDegree) buckets.set(degree, [...ids]);
    return buckets;
  }

  /** @returns {object} a summary of the index, useful for diagnostics. */
  stats() {
    this._guard();
    return {
      revision: this.revision,
      storeRevision: this.store.revision,
      stale: this.isStale(),
      nodeCount: this.store.nodeCount,
      edgeCount: this.store.edgeCount,
      kinds: this.indexes.byKind.size,
      edgeTypes: this.indexes.byEdgeType.size,
      domains: this.indexes.byDomain.size,
      roles: this.indexes.byRole.size,
      spaces: this.indexes.byBuilding.size + this.indexes.byFloor.size + this.indexes.byRoom.size,
      verificationLevels: this.indexes.byVerification.size,
      degreeBuckets: this.indexes.byDegree.size,
    };
  }

  _guard() {
    if (!this.isStale()) return;
    if (this.autoRebuild) {
      this.rebuild();
      return;
    }
    this.assertFresh();
  }
}
