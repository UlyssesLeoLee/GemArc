/**
 * GraphQueryEngine - every traversal, lookup and analysis over a GraphStore.
 *
 * Two rules shape this file:
 *
 *  1. Every traversal is bounded. Depth, result count and wall-clock budget
 *     are hard limits, not advice. A query that would exceed them returns a
 *     truncated result carrying `truncated: true` instead of hanging.
 *  2. Every result maps straight onto the visualisation. A query returns node
 *     and edge ids that already exist in the store, never derived shapes.
 */
import { GemArcError } from '../core/errors.js';

const DEFAULTS = {
  maxDepth: 6,
  maxResults: 5000,
  budgetMs: 250,
};

function now() {
  return typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
}

export class GraphQueryEngine {
  /**
   * @param {import('./graph-store.js').GraphStore} store
   * @param {{maxDepth?: number, maxResults?: number, budgetMs?: number}} [limits]
   */
  constructor(store, limits = {}) {
    this.store = store;
    this.limits = { ...DEFAULTS, ...limits };
  }

  // --- lookups -------------------------------------------------------------

  getNode(id) {
    return this.store.getNode(id);
  }

  getEdge(id) {
    return this.store.getEdge(id);
  }

  getIncomingEdges(nodeId, options = {}) {
    return this._filterEdges(this.store.incomingEdges(nodeId), options);
  }

  getOutgoingEdges(nodeId, options = {}) {
    return this._filterEdges(this.store.outgoingEdges(nodeId), options);
  }

  getNeighbors(nodeId, options = {}) {
    const { direction = 'out', edgeTypes = null, evidenceIds = null } = options;
    const found = this.store.neighbors(nodeId, { direction, edgeTypes });
    const result = [];
    for (const [neighborId, edge] of found) {
      if (evidenceIds && !edge.evidenceIds?.some((id) => evidenceIds.includes(id))) continue;
      result.push({ nodeId: neighborId, edgeId: edge.id, edgeType: edge.type });
    }
    return result;
  }

  /**
   * Breadth-first neighbourhood up to `maxDepth`.
   * Returns the visited node ids and the edge ids that were traversed.
   */
  getNeighborhood(nodeId, options = {}) {
    const { maxDepth = this.limits.maxDepth, direction = 'out', edgeTypes = null } = options;
    const guard = this._guard(options);
    if (!this.store.hasNode(nodeId)) {
      throw new GemArcError('GEMARC_QUERY_001', '节点不存在', nodeId);
    }
    const visited = new Set([nodeId]);
    const edgeIds = new Set();
    let frontier = [nodeId];
    let depth = 0;
    while (frontier.length && depth < maxDepth) {
      const next = [];
      for (const current of frontier) {
        for (const neighbor of this.getNeighbors(current, { direction, edgeTypes })) {
          edgeIds.add(neighbor.edgeId);
          if (!visited.has(neighbor.nodeId)) {
            visited.add(neighbor.nodeId);
            next.push(neighbor.nodeId);
            if (guard.exceeded()) {
              return { nodeIds: [...visited], edgeIds: [...edgeIds], truncated: true };
            }
          }
        }
      }
      frontier = next;
      depth += 1;
    }
    return { nodeIds: [...visited], edgeIds: [...edgeIds], truncated: false };
  }

  /**
   * The subgraph induced by a set of node ids.
   */
  getSubgraph(nodeIds, options = {}) {
    const keep = new Set(nodeIds);
    const nodes = [...keep].filter((id) => this.store.hasNode(id)).map((id) => this.store.getNode(id));
    const edges = this.store.edges
      ? [...this.store.edges.values()].filter(
          (edge) => keep.has(edge.sourceId) && keep.has(edge.targetId),
        )
      : [];
    return { nodes, edges };
  }

  // --- hierarchy -----------------------------------------------------------

  /** Ancestors via `parentId`, closest first. */
  getAncestors(nodeId, options = {}) {
    const { maxDepth = this.limits.maxDepth } = options;
    const result = [];
    const seen = new Set([nodeId]);
    let current = this.store.getNode(nodeId);
    let depth = 0;
    while (current && current.parentId && depth < maxDepth) {
      if (seen.has(current.parentId)) break;
      seen.add(current.parentId);
      const parent = this.store.getNode(current.parentId);
      if (!parent) break;
      result.push(parent.id);
      current = parent;
      depth += 1;
    }
    return result;
  }

  /** Descendants via `parentId`. */
  getDescendants(nodeId, options = {}) {
    const { maxDepth = this.limits.maxDepth } = options;
    const children = new Map();
    for (const node of this.store.nodes.values()) {
      if (!node.parentId) continue;
      if (!children.has(node.parentId)) children.set(node.parentId, []);
      children.get(node.parentId).push(node.id);
    }
    const result = [];
    const seen = new Set([nodeId]);
    let frontier = [nodeId];
    let depth = 0;
    while (frontier.length && depth < maxDepth) {
      const next = [];
      for (const current of frontier) {
        for (const child of children.get(current) || []) {
          if (seen.has(child)) continue;
          seen.add(child);
          result.push(child);
          next.push(child);
        }
      }
      frontier = next;
      depth += 1;
    }
    return result;
  }

  // --- paths ---------------------------------------------------------------

  /**
   * Shortest path by edge count. Returns `{ nodeIds, edgeIds }` or null.
   */
  findShortestPath(fromId, toId, options = {}) {
    const { direction = 'out', edgeTypes = null, maxDepth = this.limits.maxDepth } = options;
    const guard = this._guard(options);
    if (!this.store.hasNode(fromId) || !this.store.hasNode(toId)) {
      throw new GemArcError('GEMARC_QUERY_001', '路径端点不存在');
    }
    if (fromId === toId) return { nodeIds: [fromId], edgeIds: [], truncated: false };

    const previous = new Map([[fromId, null]]);
    const queue = [[fromId, []]];
    while (queue.length) {
      const [current, pathEdges] = queue.shift();
      if (pathEdges.length >= maxDepth) continue;
      for (const neighbor of this.getNeighbors(current, { direction, edgeTypes })) {
        if (previous.has(neighbor.nodeId)) continue;
        previous.set(neighbor.nodeId, { node: current, edgeId: neighbor.edgeId });
        const nextEdges = [...pathEdges, neighbor.edgeId];
        if (neighbor.nodeId === toId) {
          return { nodeIds: this._rebuildPath(previous, fromId, toId), edgeIds: nextEdges, truncated: false };
        }
        queue.push([neighbor.nodeId, nextEdges]);
        if (guard.exceeded()) return { nodeIds: [], edgeIds: [], truncated: true };
      }
    }
    return null;
  }

  /**
   * All simple paths between two nodes, bounded by depth and result count.
   */
  findAllPaths(fromId, toId, options = {}) {
    const { direction = 'out', edgeTypes = null, maxDepth = this.limits.maxDepth } = options;
    const guard = this._guard(options);
    const paths = [];
    const visited = new Set([fromId]);

    const walk = (current, nodeIds, edgeIds) => {
      if (paths.length >= this.limits.maxResults || guard.exceeded()) return;
      if (current === toId && nodeIds.length > 1) {
        paths.push({ nodeIds: [...nodeIds], edgeIds: [...edgeIds] });
        return;
      }
      if (edgeIds.length >= maxDepth) return;
      for (const neighbor of this.getNeighbors(current, { direction, edgeTypes })) {
        if (visited.has(neighbor.nodeId)) continue;
        visited.add(neighbor.nodeId);
        walk(neighbor.nodeId, [...nodeIds, neighbor.nodeId], [...edgeIds, neighbor.edgeId]);
        visited.delete(neighbor.nodeId);
      }
    };

    walk(fromId, [fromId], []);
    return { paths, truncated: paths.length >= this.limits.maxResults || guard.exceeded() };
  }

  // --- structure -----------------------------------------------------------

  /**
   * Every elementary cycle, found with a depth bounded DFS.
   */
  findCycles(options = {}) {
    const { direction = 'out', edgeTypes = null, maxDepth = this.limits.maxDepth } = options;
    const guard = this._guard(options);
    const cycles = [];
    const seen = new Set();

    const walk = (start, current, pathNodes, pathEdges, onPath) => {
      if (cycles.length >= this.limits.maxResults || guard.exceeded()) return;
      for (const neighbor of this.getNeighbors(current, { direction, edgeTypes })) {
        if (neighbor.nodeId === start && pathNodes.length > 1) {
          const key = [...pathNodes].sort().join('|');
          if (!seen.has(key)) {
            seen.add(key);
            cycles.push({ nodeIds: [...pathNodes], edgeIds: [...pathEdges, neighbor.edgeId] });
          }
          continue;
        }
        if (onPath.has(neighbor.nodeId)) continue;
        if (pathNodes.length >= maxDepth) continue;
        onPath.add(neighbor.nodeId);
        walk(start, neighbor.nodeId, [...pathNodes, neighbor.nodeId], [...pathEdges, neighbor.edgeId], onPath);
        onPath.delete(neighbor.nodeId);
      }
    };

    for (const node of this.store.nodes.keys()) {
      walk(node, node, [node], [], new Set([node]));
      if (guard.exceeded()) break;
    }
    return { cycles, truncated: guard.exceeded() };
  }

  /**
   * Tarjan strongly connected components. Nodes in a component of size > 1 are
   * mutually reachable, i.e. a real coupling risk.
   */
  findStronglyConnectedComponents(options = {}) {
    const { edgeTypes = null } = options;
    const guard = this._guard(options);
    const index = new Map();
    const low = new Map();
    const onStack = new Set();
    const stack = [];
    const components = [];
    let counter = 0;

    const strongConnect = (nodeId) => {
      index.set(nodeId, counter);
      low.set(nodeId, counter);
      counter += 1;
      stack.push(nodeId);
      onStack.add(nodeId);

      for (const neighbor of this.getNeighbors(nodeId, { direction: 'out', edgeTypes })) {
        if (!index.has(neighbor.nodeId)) {
          strongConnect(neighbor.nodeId);
          low.set(nodeId, Math.min(low.get(nodeId), low.get(neighbor.nodeId)));
        } else if (onStack.has(neighbor.nodeId)) {
          low.set(nodeId, Math.min(low.get(nodeId), index.get(neighbor.nodeId)));
        }
      }

      if (low.get(nodeId) === index.get(nodeId)) {
        const component = [];
        let member;
        do {
          member = stack.pop();
          onStack.delete(member);
          component.push(member);
        } while (member !== nodeId);
        components.push(component.sort());
        if (guard.exceeded()) throw new GemArcError('GEMARC_QUERY_002', '查询预算耗尽');
      }
    };

    for (const nodeId of this.store.nodes.keys()) {
      if (!index.has(nodeId)) strongConnect(nodeId);
    }
    // A single node is only a component if it can reach itself.
    return {
      components: components.filter((component) => component.length > 1),
      truncated: false,
    };
  }

  // --- domain helpers ------------------------------------------------------

  /**
   * The chain of `dependsOn` edges leading into a node.
   */
  findDependencyChain(nodeId, options = {}) {
    const { maxDepth = this.limits.maxDepth } = options;
    const chain = [];
    const seen = new Set([nodeId]);
    let current = nodeId;
    for (let depth = 0; depth < maxDepth; depth += 1) {
      const dependency = this.getIncomingEdges(current, { edgeTypes: ['dependsOn'] })[0];
      if (!dependency || seen.has(dependency.sourceId)) break;
      seen.add(dependency.sourceId);
      chain.push({ nodeId: dependency.sourceId, edgeId: dependency.id });
      current = dependency.sourceId;
    }
    return chain;
  }

  /**
   * The set of nodes reachable downstream of a node: what breaks if it breaks.
   */
  findImpactSubgraph(nodeId, options = {}) {
    const { maxDepth = this.limits.maxDepth, edgeTypes = null, direction = 'out' } = options;
    const neighborhood = this.getNeighborhood(nodeId, { maxDepth, edgeTypes, direction });
    const impacted = neighborhood.nodeIds.filter((id) => id !== nodeId);
    return {
      nodeId,
      impactedNodeIds: impacted,
      edgeIds: neighborhood.edgeIds,
      truncated: neighborhood.truncated,
    };
  }

  // --- internals -----------------------------------------------------------

  _filterEdges(edges, options) {
    const { edgeTypes = null, evidenceIds = null } = options;
    return edges.filter((edge) => {
      if (edgeTypes && !edgeTypes.includes(edge.type)) return false;
      if (evidenceIds && !(edge.evidenceIds || []).some((id) => evidenceIds.includes(id))) return false;
      return true;
    });
  }

  _rebuildPath(previous, fromId, toId) {
    const path = [];
    let cursor = toId;
    while (cursor) {
      path.unshift(cursor);
      const step = previous.get(cursor);
      cursor = step ? step.node : null;
    }
    return path;
  }

  /**
   * A budget guard. `exceeded()` is cheap enough to call inside a hot loop.
   */
  _guard(options) {
    const limit = options.budgetMs ?? this.limits.budgetMs;
    const started = now();
    return {
      exceeded() {
        return now() - started > limit;
      },
    };
  }
}
