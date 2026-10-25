/**
 * Focus–Context relevance evaluation (Universal 5D GemArc Web v5.0 §7.2).
 *
 * The evaluator answers exactly one question: for the currently focused
 * object, which relevance class does every other object belong to?
 *
 *   Selected              the focused object itself
 *   Directly Related      direct edge, path membership, same-room membership,
 *                         or directly reached by an event / causal rule
 *   Contextually Related  reachable within a bounded hop budget, or inside the
 *                         scope of the current case / scenario
 *   Unrelated             none of the above
 *
 * Two rules from the specification are hard guarantees here:
 *
 *   1. Business relevance is NEVER inferred from 3D spatial distance. A node
 *      that merely sits in a neighbouring room stays `Unrelated` unless a real
 *      relation (edge, path membership, room membership, event rule) exists.
 *   2. The visual treatment is not decided here. The evaluator only reports the
 *      relation class; `retreat-animation.js` reads the class back out of the
 *      vocabulary so opacity / outline / label numbers live in exactly one
 *      place (`vocab/relation-classes.json`).
 */
import { GemArcError } from '../core/errors.js';
import { RELATION_CLASS_IDS, relationClassSpec } from '../core/vocab.js';

/**
 * Every object kind that may be focused, in the spelling used by the spec.
 * @type {readonly string[]}
 */
export const FOCUS_TARGET_TYPES = Object.freeze([
  'Building',
  'Floor',
  'Room',
  'Node',
  'Edge',
  'Path',
  'Event',
  'BranchPoint',
  'WorkflowNode',
  'WorkflowEdge',
  'WorkflowCase',
]);

/**
 * The four relevance classes, straight from the vocabulary.
 * @type {readonly string[]}
 */
export const RELEVANCE_CLASS_IDS = RELATION_CLASS_IDS;

const CLASS_RANK = new Map(
  RELATION_CLASS_IDS.map((id) => [id, relationClassSpec(id)?.rank ?? 0]),
);

const SELECTED = 'Selected';
const DIRECT = 'Directly Related';
const CONTEXTUAL = 'Contextually Related';
const UNRELATED = 'Unrelated';

function rankOf(classId) {
  return classId ? (CLASS_RANK.get(classId) ?? 0) : 0;
}

/**
 * Split an entity key such as `node:n1` into its kind and id.
 * @param {string} key
 * @returns {{kind: string, id: string}}
 */
function splitKey(key) {
  const separator = key.indexOf(':');
  if (separator < 0) return { kind: key, id: key };
  return { kind: key.slice(0, separator), id: key.slice(separator + 1) };
}

/**
 * Computes the relevance class of every object for a resolved focus target.
 *
 * The evaluator is deliberately read-only: it never mutates the graph store,
 * the model or the workflow semantics.
 */
export class RelevanceGraphEvaluator {
  /**
   * @param {{store: object, query?: object|null, space?: object|null, options?: object}} config
   */
  constructor({ store, query = null, space = null, options = {} } = {}) {
    if (!store) {
      throw new GemArcError('GEMARC_FOCUS_004', '相关性评估需要图存储');
    }
    this.store = store;
    this.query = query;
    this.space = space;
    this.options = {
      /** Hop budget for contextual relevance. */
      contextDepth: 2,
      /** Edge traversal direction used for relevance (`out` | `in` | `both`). */
      direction: 'both',
      /** Whether same-room membership counts as a direct relation. */
      useSpaceMembership: true,
      ...options,
    };
  }

  /**
   * Classify every known object against a resolved focus target.
   *
   * @param {object} resolved resolved focus descriptor:
   *   `{ type, id, target, entityKey, focusNodeIds, focusEdgeIds,
   *      relatedNodeIds, relatedEdgeIds, scopeNodeIds }`
   * @param {{contextDepth?: number, direction?: string, useSpaceMembership?: boolean}} [options]
   * @returns {{target: object, anchorIds: string[], classes: Map<string,string>,
   *            entries: object[], byClass: Record<string, object[]>,
   *            counts: Record<string, number>}}
   */
  evaluate(resolved, options = {}) {
    if (!resolved || typeof resolved !== 'object') {
      throw new GemArcError('GEMARC_FOCUS_001', '缺少已解析的焦点对象');
    }
    const config = { ...this.options, ...options };
    const classes = new Map();
    const reasons = new Map();

    const assign = (key, classId, reason) => {
      const current = classes.get(key);
      if (current && rankOf(current) >= rankOf(classId)) return false;
      classes.set(key, classId);
      if (reason) {
        const list = reasons.get(key) || [];
        if (!list.includes(reason)) list.push(reason);
        reasons.set(key, list);
      }
      return true;
    };

    const focusNodes = asIds(resolved.focusNodeIds);
    const focusEdges = asIds(resolved.focusEdgeIds);
    const relatedNodes = asIds(resolved.relatedNodeIds);
    const relatedEdges = asIds(resolved.relatedEdgeIds);
    const scopeNodes = asIds(resolved.scopeNodeIds);

    // 1. The focused object itself.
    for (const id of focusNodes) assign(`node:${id}`, SELECTED, 'focus-target');
    for (const id of focusEdges) assign(`edge:${id}`, SELECTED, 'focus-target');
    if (resolved.entityKey) assign(resolved.entityKey, SELECTED, 'focus-target');

    // 2. Direct relations: edges incident to the focus, focus edge endpoints.
    for (const id of focusNodes) {
      const neighbours = this.store.neighbors(id, { direction: config.direction });
      for (const [neighbourId, edge] of neighbours) {
        assign(`node:${neighbourId}`, DIRECT, `direct-edge:${edge.id}`);
        assign(`edge:${edge.id}`, DIRECT, `incident-to:${id}`);
      }
    }
    for (const id of focusEdges) {
      const edge = this.store.getEdge(id);
      if (!edge) continue;
      assign(`node:${edge.sourceId}`, DIRECT, `edge-endpoint:${id}`);
      assign(`node:${edge.targetId}`, DIRECT, `edge-endpoint:${id}`);
    }

    // 3. Relations declared by the resolver: path members, container members,
    //    event propagation targets, workflow dependencies.
    for (const id of relatedNodes) assign(`node:${id}`, DIRECT, 'declared-relation');
    for (const id of relatedEdges) assign(`edge:${id}`, DIRECT, 'declared-relation');

    // 4. Same-room membership of the focused node. Neighbouring rooms are NOT
    //    touched: spatial distance alone must never create relevance.
    if (config.useSpaceMembership && this.space) {
      for (const id of focusNodes) {
        const roomId = this.space.roomOfNode ? this.space.roomOfNode(id) : null;
        if (!roomId) continue;
        for (const peerId of this.space.nodeIdsOf(roomId)) {
          if (peerId === id) continue;
          assign(`node:${peerId}`, DIRECT, `same-room:${roomId}`);
        }
      }
    }

    // 5. Contextual relevance: bounded reachability + case / scenario scope.
    const anchors = focusNodes.length > 0 ? focusNodes : relatedNodes;
    for (const id of this._reachable(anchors, config.contextDepth, config.direction)) {
      assign(`node:${id}`, CONTEXTUAL, `within-${config.contextDepth}-hops`);
    }
    for (const id of scopeNodes) assign(`node:${id}`, CONTEXTUAL, 'case-scope');

    // 6. Everything without a relation is explicitly unrelated, then every
    //    edge derives its class from the weaker of its two endpoints.
    for (const node of this.store.nodes.values()) {
      const key = `node:${node.id}`;
      if (!classes.has(key)) assign(key, UNRELATED, 'no-relation');
    }
    for (const edge of this.store.edges.values()) {
      const key = `edge:${edge.id}`;
      if (classes.has(key)) continue;
      const rank = Math.min(
        rankOf(classes.get(`node:${edge.sourceId}`)),
        rankOf(classes.get(`node:${edge.targetId}`)),
      );
      if (rank >= rankOf(CONTEXTUAL)) {
        assign(key, CONTEXTUAL, 'derived-from-endpoints');
      } else {
        assign(key, UNRELATED, 'no-relation');
      }
    }

    const entries = [];
    const seen = new Set();
    const push = (key, id, type, name) => {
      if (seen.has(key)) return;
      seen.add(key);
      const relationClass = classes.get(key) || UNRELATED;
      entries.push({
        key,
        id,
        type,
        name: name || '',
        relationClass,
        rank: rankOf(relationClass),
        reasons: reasons.get(key) || [],
        visual: relationClassSpec(relationClass)?.visual || null,
      });
    };

    for (const node of this.store.nodes.values()) {
      push(`node:${node.id}`, node.id, 'node', node.name);
    }
    for (const edge of this.store.edges.values()) {
      push(`edge:${edge.id}`, edge.id, 'edge', edge.name || edge.id);
    }
    for (const [key] of classes) {
      const { kind, id } = splitKey(key);
      push(key, id, kind, '');
    }

    const byClass = {};
    for (const id of RELEVANCE_CLASS_IDS) byClass[id] = [];
    for (const entry of entries) {
      (byClass[entry.relationClass] ||= []).push(entry);
    }
    const counts = {};
    for (const id of RELEVANCE_CLASS_IDS) counts[id] = byClass[id].length;

    return {
      target: resolved.target || { type: resolved.type || '', id: resolved.id || '' },
      anchorIds: anchors,
      classes,
      entries,
      byClass,
      counts,
    };
  }

  /**
   * The relevance class of one entity key inside a report.
   * @param {object} report report produced by `evaluate`
   * @param {string} key entity key, e.g. `node:n1`
   * @returns {string} one of the four relation classes
   */
  classOf(report, key) {
    return report?.classes?.get(key) || UNRELATED;
  }

  /**
   * Breadth-first reachability from a set of anchors.
   * @param {string[]} anchors
   * @param {number} maxHops
   * @param {string} direction
   * @returns {string[]} every node reachable within `maxHops` hops
   */
  _reachable(anchors, maxHops, direction) {
    if (!anchors.length || maxHops <= 0) return [];
    const visited = new Set(anchors);
    const reached = [];
    let frontier = [...anchors];
    for (let hop = 0; hop < maxHops && frontier.length; hop += 1) {
      const next = [];
      for (const id of frontier) {
        for (const neighbourId of this.store.neighbors(id, { direction }).keys()) {
          if (visited.has(neighbourId)) continue;
          visited.add(neighbourId);
          reached.push(neighbourId);
          next.push(neighbourId);
        }
      }
      frontier = next;
    }
    return reached;
  }
}

function asIds(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.filter((id) => typeof id === 'string' && id.length > 0);
  if (value instanceof Set) return [...value];
  return [];
}
