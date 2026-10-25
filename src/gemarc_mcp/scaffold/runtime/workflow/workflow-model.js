/**
 * WorkflowModel - the in-memory execution model of a canonical workflow.
 *
 * What this file covers (canonical workflow subset, see
 * `contracts/workflow.schema.json`):
 *   - workflow identity (id / name / kind / description) and its variables
 *   - nodes: kind, ports, execution constraints, data contract, exception
 *     handling, canonical node binding
 *   - edges: source/target node, source/target port, kind, directedness,
 *     condition, data contract
 *
 * What it cannot express:
 *   - canvas geometry is carried through untouched but never interpreted:
 *     presentation and execution stay separated (the canvas lives in
 *     `canvas-controller.js`)
 *   - runtime scheduling: the model states *what* may run, under which
 *     constraints and over which ports; it never states *how* an engine
 *     schedules it
 *
 * The model is deliberately tolerant: malformed input is normalised into a
 * usable shape and the *validator* reports the problems. Only a non-object
 * input is rejected outright.
 */
import { GemArcError } from '../core/errors.js';
import {
  VOCAB,
  WORKFLOW_NODE_KIND_IDS,
  WORKFLOW_EDGE_KIND_IDS,
  WORKFLOW_CONSTRAINT_IDS,
} from '../core/vocab.js';

/** Port kinds, taken from the generated vocabulary so they cannot drift. */
export const WORKFLOW_PORT_KIND_IDS = Object.freeze(
  (VOCAB['workflow-kinds']?.portKinds || []).map((item) => item.id),
);

const NODE_KIND_SET = new Set(WORKFLOW_NODE_KIND_IDS);
const EDGE_KIND_SET = new Set(WORKFLOW_EDGE_KIND_IDS);
const CONSTRAINT_KIND_SET = new Set(WORKFLOW_CONSTRAINT_IDS);
const PORT_KIND_SET = new Set(WORKFLOW_PORT_KIND_IDS);

/** Edge kinds that carry execution order (association is a plain link). */
export const WORKFLOW_EXECUTION_EDGE_KINDS = Object.freeze(
  WORKFLOW_EDGE_KIND_IDS.filter((kind) => kind !== 'association'),
);

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function optionalString(value) {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function normalizePort(port) {
  const source = asObject(port);
  return {
    ...source,
    id: typeof source.id === 'string' ? source.id : '',
    kind: typeof source.kind === 'string' ? source.kind : 'input',
    name: optionalString(source.name),
    schemaRef: optionalString(source.schemaRef),
  };
}

function normalizeConstraint(constraint) {
  const source = asObject(constraint);
  return {
    ...source,
    kind: typeof source.kind === 'string' ? source.kind : '',
    value: source.value === undefined ? null : source.value,
    backoffMs: typeof source.backoffMs === 'number' ? source.backoffMs : null,
    onTimeout: optionalString(source.onTimeout),
    compensationNodeId: optionalString(source.compensationNodeId),
  };
}

/** Canvas data is presentation only; it is normalised but never interpreted. */
function normalizeCanvas(canvas) {
  if (!canvas || typeof canvas !== 'object' || Array.isArray(canvas)) return null;
  const source = asObject(canvas);
  const out = {};
  if (typeof source.x === 'number') out.x = source.x;
  if (typeof source.y === 'number') out.y = source.y;
  if (typeof source.width === 'number') out.width = source.width;
  if (typeof source.height === 'number') out.height = source.height;
  return out;
}

function normalizeNode(node) {
  const source = asObject(node);
  return {
    ...source,
    id: typeof source.id === 'string' ? source.id : '',
    kind: typeof source.kind === 'string' ? source.kind : 'task',
    name: typeof source.name === 'string' ? source.name : '',
    canonicalNodeId: optionalString(source.canonicalNodeId),
    ports: asArray(source.ports).map(normalizePort),
    constraints: asArray(source.constraints).map(normalizeConstraint),
    dataContract: source.dataContract ? asObject(source.dataContract) : null,
    exceptionHandling: source.exceptionHandling ? asObject(source.exceptionHandling) : null,
    canvas: normalizeCanvas(source.canvas),
    properties: asObject(source.properties),
  };
}

function normalizeEdge(edge) {
  const source = asObject(edge);
  const condition = source.condition === '' ? null : (source.condition ?? null);
  return {
    ...source,
    id: typeof source.id === 'string' ? source.id : '',
    sourceId: typeof source.sourceId === 'string' ? source.sourceId : '',
    targetId: typeof source.targetId === 'string' ? source.targetId : '',
    sourcePortId: optionalString(source.sourcePortId),
    targetPortId: optionalString(source.targetPortId),
    kind: typeof source.kind === 'string' ? source.kind : 'sequence',
    directed: source.directed === undefined ? true : Boolean(source.directed),
    condition: condition === null ? null : String(condition),
    dataContract: source.dataContract ? asObject(source.dataContract) : null,
    canvas: source.canvas ? asObject(source.canvas) : null,
    properties: asObject(source.properties),
  };
}

/**
 * Normalise a canonical workflow into the shape the model works on.
 * The input is never mutated and malformed parts are defaulted, not rejected.
 */
export function normalizeWorkflow(workflow) {
  const source = asObject(workflow);
  return {
    ...source,
    id: typeof source.id === 'string' ? source.id : '',
    name: typeof source.name === 'string' ? source.name : '',
    kind: optionalString(source.kind),
    description: optionalString(source.description),
    nodes: asArray(source.nodes).map(normalizeNode),
    edges: asArray(source.edges).map(normalizeEdge),
    variables: asArray(source.variables).map((item) => ({ ...asObject(item) })),
  };
}

/** True when the edge kind carries execution order rather than a plain link. */
export function isExecutionEdge(edge) {
  if (!edge) return false;
  return WORKFLOW_EXECUTION_EDGE_KINDS.includes(edge.kind);
}

/** True when the id is registered in the generated vocabulary. */
export function isKnownNodeKind(kind) {
  return NODE_KIND_SET.has(kind);
}

/** True when the edge kind is registered in the generated vocabulary. */
export function isKnownEdgeKind(kind) {
  return EDGE_KIND_SET.has(kind);
}

/** True when the constraint kind is registered in the generated vocabulary. */
export function isKnownConstraintKind(kind) {
  return CONSTRAINT_KIND_SET.has(kind);
}

/** True when the port kind is registered in the generated vocabulary. */
export function isKnownPortKind(kind) {
  return PORT_KIND_SET.has(kind);
}

/**
 * WorkflowModel - stable identity, port aware edges and bounded traversal.
 *
 * Node and edge objects are mutable on purpose: the canvas controller writes
 * presentation data into them. Execution data is never rewritten by the
 * model itself.
 */
export class WorkflowModel {
  /**
   * @param {object} workflow canonical workflow data (workflow.schema.json)
   */
  constructor(workflow) {
    if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) {
      throw new GemArcError('GEMARC_WORKFLOW_005', '工作流必须是对象');
    }
    /** @type {object} normalised workflow data */
    this.data = normalizeWorkflow(workflow);
    /** @type {Map<string, object>} */
    this.nodes = new Map();
    /** @type {Map<string, object>} */
    this.edges = new Map();
    /** @type {Map<string, Set<string>>} */
    this.outgoing = new Map();
    /** @type {Map<string, Set<string>>} */
    this.incoming = new Map();
    /** @type {string[]} node ids that appeared more than once */
    this.duplicateNodeIds = [];
    /** @type {string[]} edge ids that appeared more than once */
    this.duplicateEdgeIds = [];

    for (const node of this.data.nodes) {
      if (!node.id) continue;
      if (this.nodes.has(node.id)) {
        this.duplicateNodeIds.push(node.id);
        continue;
      }
      this.nodes.set(node.id, node);
      this.outgoing.set(node.id, new Set());
      this.incoming.set(node.id, new Set());
    }
    for (const edge of this.data.edges) {
      if (!edge.id) continue;
      if (this.edges.has(edge.id)) {
        this.duplicateEdgeIds.push(edge.id);
        continue;
      }
      this.edges.set(edge.id, edge);
      if (this.outgoing.has(edge.sourceId)) this.outgoing.get(edge.sourceId).add(edge.id);
      if (this.incoming.has(edge.targetId)) this.incoming.get(edge.targetId).add(edge.id);
    }
  }

  /** Build a model from canonical JSON text. */
  static fromJSON(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new GemArcError('GEMARC_WORKFLOW_005', '工作流 JSON 无法解析');
    }
    return new WorkflowModel(parsed);
  }

  /** Workflow id. */
  get id() {
    return this.data.id;
  }

  /** Workflow name. */
  get name() {
    return this.data.name;
  }

  /** Workflow kind (process, choreography, ...). */
  get kind() {
    return this.data.kind;
  }

  /** All nodes in declaration order. */
  get nodeList() {
    return [...this.nodes.values()];
  }

  /** All edges in declaration order. */
  get edgeList() {
    return [...this.edges.values()];
  }

  /** Look up a node by id. */
  node(id) {
    return this.nodes.get(id) || null;
  }

  /** Look up an edge by id. */
  edge(id) {
    return this.edges.get(id) || null;
  }

  /** True when the node id is known. */
  hasNode(id) {
    return this.nodes.has(id);
  }

  /** True when the edge id is known. */
  hasEdge(id) {
    return this.edges.has(id);
  }

  /**
   * Edges leaving a node, optionally narrowed by port or edge kind.
   * @param {string} nodeId
   * @param {{portId?: string, portKind?: string, kind?: string}} [options]
   */
  outgoingEdges(nodeId, options = {}) {
    const ids = this.outgoing.get(nodeId) || new Set();
    const result = [];
    for (const id of ids) {
      const edge = this.edges.get(id);
      if (!edge) continue;
      if (options.kind && edge.kind !== options.kind) continue;
      if (options.portId && edge.sourcePortId !== options.portId) continue;
      if (options.portKind) {
        const port = this.port(nodeId, edge.sourcePortId);
        if (!port || port.kind !== options.portKind) continue;
      }
      result.push(edge);
    }
    return result;
  }

  /**
   * Edges entering a node, optionally narrowed by port or edge kind.
   * @param {string} nodeId
   * @param {{portId?: string, portKind?: string, kind?: string}} [options]
   */
  incomingEdges(nodeId, options = {}) {
    const ids = this.incoming.get(nodeId) || new Set();
    const result = [];
    for (const id of ids) {
      const edge = this.edges.get(id);
      if (!edge) continue;
      if (options.kind && edge.kind !== options.kind) continue;
      if (options.portId && edge.targetPortId !== options.portId) continue;
      if (options.portKind) {
        const port = this.port(nodeId, edge.targetPortId);
        if (!port || port.kind !== options.portKind) continue;
      }
      result.push(edge);
    }
    return result;
  }

  /**
   * What may run after a node.
   * @param {string} nodeId
   * @param {{kind?: string, portId?: string}} [options]
   * @returns {{edge: object, node: object|null}[]}
   */
  successorsOf(nodeId, options = {}) {
    return this.outgoingEdges(nodeId, options).map((edge) => ({
      edge,
      node: this.nodes.get(edge.targetId) || null,
    }));
  }

  /**
   * What ran before a node.
   * @param {string} nodeId
   * @param {{kind?: string, portId?: string}} [options]
   * @returns {{edge: object, node: object|null}[]}
   */
  predecessorsOf(nodeId, options = {}) {
    return this.incomingEdges(nodeId, options).map((edge) => ({
      edge,
      node: this.nodes.get(edge.sourceId) || null,
    }));
  }

  /** All ports of a node, optionally filtered by port kind. */
  portsOf(nodeId, { kind = null } = {}) {
    const node = this.nodes.get(nodeId);
    if (!node) return [];
    if (!kind) return [...node.ports];
    return node.ports.filter((port) => port.kind === kind);
  }

  /** Look up one port of a node. */
  port(nodeId, portId) {
    if (!portId) return null;
    const node = this.nodes.get(nodeId);
    if (!node) return null;
    return node.ports.find((port) => port.id === portId) || null;
  }

  /** Ports of a node that carry data in (input / control / error). */
  incomingPorts(nodeId) {
    const node = this.nodes.get(nodeId);
    if (!node) return [];
    return node.ports.filter((port) => port.kind === 'input' || port.kind === 'control' || port.kind === 'error');
  }

  /** Ports of a node that carry data out (output / control). */
  outgoingPorts(nodeId) {
    const node = this.nodes.get(nodeId);
    if (!node) return [];
    return node.ports.filter((port) => port.kind === 'output' || port.kind === 'control');
  }

  /**
   * Everything a node requires before it may run: its ports, the inbound
   * edges feeding those ports, the data contract and the execution
   * constraints that apply.
   */
  requirementsOf(nodeId) {
    const node = this.nodes.get(nodeId);
    if (!node) {
      throw new GemArcError('GEMARC_WORKFLOW_001', '节点不存在', String(nodeId));
    }
    return {
      nodeId,
      ports: {
        input: this.portsOf(nodeId, { kind: 'input' }),
        output: this.portsOf(nodeId, { kind: 'output' }),
        control: this.portsOf(nodeId, { kind: 'control' }),
        error: this.portsOf(nodeId, { kind: 'error' }),
      },
      inbound: this.incomingEdges(nodeId).map((edge) => ({
        edgeId: edge.id,
        sourceId: edge.sourceId,
        sourcePortId: edge.sourcePortId,
        targetPortId: edge.targetPortId,
        kind: edge.kind,
        condition: edge.condition,
        dataContract: edge.dataContract,
      })),
      dataContract: node.dataContract,
      exceptionHandling: node.exceptionHandling,
      constraints: this.constraintsOf(nodeId),
    };
  }

  /**
   * Execution constraints of a node, optionally narrowed by constraint kind.
   * @param {string} nodeId
   * @param {string} [kind]
   */
  constraintsOf(nodeId, kind = null) {
    const node = this.nodes.get(nodeId);
    if (!node) return [];
    if (!kind) return [...node.constraints];
    return node.constraints.filter((constraint) => constraint.kind === kind);
  }

  /** Nodes of kind `start`. */
  startNodes() {
    return this.nodeList.filter((node) => node.kind === 'start');
  }

  /** Nodes of kind `end`. */
  endNodes() {
    return this.nodeList.filter((node) => node.kind === 'end');
  }

  /**
   * Node ids reachable from a set of start nodes over execution edges.
   * @param {string[]} [fromIds]
   * @returns {Set<string>}
   */
  reachableNodeIds(fromIds = null) {
    const starts = fromIds || this.startNodes().map((node) => node.id);
    const seen = new Set(starts);
    const queue = [...starts];
    while (queue.length) {
      const current = queue.shift();
      for (const edge of this.outgoingEdges(current)) {
        if (!isExecutionEdge(edge)) continue;
        if (seen.has(edge.targetId)) continue;
        seen.add(edge.targetId);
        queue.push(edge.targetId);
      }
    }
    return seen;
  }

  /** The canonical workflow data (canvas included, untouched). */
  toJSON() {
    return {
      ...this.data,
      nodes: this.nodeList,
      edges: this.edgeList,
    };
  }
}
