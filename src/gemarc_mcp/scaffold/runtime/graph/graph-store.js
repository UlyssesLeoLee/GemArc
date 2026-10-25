/**
 * GraphStore - the in-memory property graph behind every view.
 *
 * The store is the single owner of node and edge identity. Two dimensions, one
 * model: 2D, 3D, the workflow canvas, the simulation and the interop adapters
 * all read from this store, so a node id means the same thing everywhere.
 *
 * The store has no required third-party dependency. When Graphology is
 * present the adapter in `graph-adapters.js` wraps it; otherwise this
 * implementation is used directly.
 */
import { GemArcError } from '../core/errors.js';
import { isEdgeType, edgeTypeSpec } from '../core/vocab.js';
import { normalizeEdge, normalizeNode } from '../core/model.js';

export class GraphStore {
  constructor(model = null) {
    /** @type {Map<string, object>} */
    this.nodes = new Map();
    /** @type {Map<string, object>} */
    this.edges = new Map();
    /** @type {Map<string, Set<string>>} */
    this.outgoing = new Map();
    /** @type {Map<string, Set<string>>} */
    this.incoming = new Map();
    /** @type {number} */
    this.revision = 0;
    if (model) this.load(model);
  }

  /** Replace the whole graph. */
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
    const node = normalizeNode(rawNode);
    if (!node.id) throw new GemArcError('GEMARC_MODEL_007', '节点缺少 ID');
    if (this.nodes.has(node.id)) {
      throw new GemArcError('GEMARC_MODEL_008', '节点 ID 重复', node.id);
    }
    this.nodes.set(node.id, node);
    this.outgoing.set(node.id, new Set());
    this.incoming.set(node.id, new Set());
    if (!silent) this.revision += 1;
    return node;
  }

  addEdge(rawEdge, { silent = false } = {}) {
    const edge = normalizeEdge(rawEdge);
    if (!edge.id) throw new GemArcError('GEMARC_MODEL_011', '边缺少 ID');
    if (this.edges.has(edge.id)) {
      throw new GemArcError('GEMARC_MODEL_011', '边 ID 重复', edge.id);
    }
    if (!isEdgeType(edge.type)) {
      throw new GemArcError('GEMARC_MODEL_013', '未登记的关系类型', edge.id);
    }
    if (!this.nodes.has(edge.sourceId)) {
      throw new GemArcError('GEMARC_MODEL_012', 'sourceId 节点不存在', edge.id);
    }
    if (!this.nodes.has(edge.targetId)) {
      throw new GemArcError('GEMARC_MODEL_012', 'targetId 节点不存在', edge.id);
    }
    this.edges.set(edge.id, edge);
    this.outgoing.get(edge.sourceId).add(edge.id);
    this.incoming.get(edge.targetId).add(edge.id);
    if (!edge.directed) {
      // An undirected edge is traversable from both ends.
      this.outgoing.get(edge.targetId).add(edge.id);
      this.incoming.get(edge.sourceId).add(edge.id);
    }
    if (!silent) this.revision += 1;
    return edge;
  }

  removeEdge(id) {
    const edge = this.edges.get(id);
    if (!edge) return false;
    this.outgoing.get(edge.sourceId)?.delete(id);
    this.incoming.get(edge.targetId)?.delete(id);
    this.outgoing.get(edge.targetId)?.delete(id);
    this.incoming.get(edge.sourceId)?.delete(id);
    this.edges.delete(id);
    this.revision += 1;
    return true;
  }

  removeNode(id) {
    if (!this.nodes.has(id)) return false;
    for (const edgeId of [...this.incidentEdges(id)]) this.removeEdge(edgeId);
    this.nodes.delete(id);
    this.outgoing.delete(id);
    this.incoming.delete(id);
    this.revision += 1;
    return true;
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

  /**
   * Direct neighbours. With `directed: false` an undirected edge yields both
   * endpoints; with `directed: true` only the target is a neighbour.
   */
  neighbors(nodeId, { direction = 'out', edgeTypes = null } = {}) {
    const result = new Map();
    const push = (edge, other) => {
      if (edgeTypes && !edgeTypes.includes(edge.type)) return;
      result.set(other, edge);
    };
    if (direction === 'out' || direction === 'both') {
      for (const edge of this.outgoingEdges(nodeId)) {
        push(edge, edge.sourceId === nodeId ? edge.targetId : edge.sourceId);
      }
    }
    if (direction === 'in' || direction === 'both') {
      for (const edge of this.incomingEdges(nodeId)) {
        push(edge, edge.targetId === nodeId ? edge.sourceId : edge.targetId);
      }
    }
    return result;
  }

  /** Every edge that crosses a trust boundary. */
  trustBoundaryEdges() {
    return [...this.edges.values()].filter(
      (edge) => edge.trustBoundary === true || edgeTypeSpec(edge.type)?.trustBoundary === true,
    );
  }

  /** Convert the store back into a plain model object. */
  toModel() {
    return {
      nodes: [...this.nodes.values()],
      edges: [...this.edges.values()],
    };
  }

  /**
   * A cheap structural fingerprint. Used to prove that two renderers are
   * showing the same facts, and to detect accidental mutation.
   */
  fingerprint() {
    const nodeIds = [...this.nodes.keys()].sort().join(',');
    const edgeIds = [...this.edges.keys()].sort().join(',');
    return `${this.nodes.size}:${nodeIds}|${this.edges.size}:${edgeIds}`;
  }
}
