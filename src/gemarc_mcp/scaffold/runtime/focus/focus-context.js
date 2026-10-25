/**
 * Focus–Context dynamic retreat engine (Universal 5D GemArc Web v5.0 §7).
 *
 * `FocusContextEngine` is the coordinator. It
 *
 *   1. resolves the focused object (a Building / Floor / Room / Node / Edge /
 *      Path / Event / BranchPoint / WorkflowNode / WorkflowEdge / WorkflowCase),
 *   2. asks `RelevanceGraphEvaluator` to classify every object,
 *   3. asks `RetreatAnimationController` to apply the visual retreat,
 *   4. records the session in `ContextRestorationManager` so Back, Clear
 *      Focus, Restore Overview, Lock Focus, Breadcrumb and Compare Focus work.
 *
 * The engine never mutates the model, the graph store or the workflow
 * semantics. Objects that are not graph nodes (a workflow case, an event, a
 * branch point) are resolved through injected resolvers, so the engine does
 * not depend on the graph store alone.
 */
import { GemArcError } from '../core/errors.js';
import { ContextRestorationManager } from './context-restoration.js';
import { FOCUS_TARGET_TYPES, RelevanceGraphEvaluator } from './relevance-evaluator.js';
import { RetreatAnimationController } from './retreat-animation.js';

export { FOCUS_TARGET_TYPES };

/**
 * Normalise the many shapes a caller may use to address a focus target.
 * @param {object|string} target
 * @returns {{type: string|null, id: string}}
 */
function normalizeTargetInput(target) {
  if (typeof target === 'string') {
    if (!target) throw new GemArcError('GEMARC_FOCUS_001', '焦点对象缺少 ID');
    return { type: null, id: target };
  }
  if (!target || typeof target !== 'object') {
    throw new GemArcError('GEMARC_FOCUS_001', '焦点对象格式无效');
  }
  const id = target.id ?? target.entityId ?? target.nodeId ?? target.targetId;
  if (typeof id !== 'string' || !id) {
    throw new GemArcError('GEMARC_FOCUS_001', '焦点对象缺少 ID');
  }
  const rawType = target.type ?? target.kind ?? target.targetType ?? null;
  return { type: rawType ? String(rawType) : null, id };
}

/**
 * Find the canonical spelling of a focus target type.
 * @param {string} type
 * @returns {string} canonical type, e.g. `WorkflowCase`
 */
function canonicalType(type) {
  const wanted = String(type).toLowerCase();
  const found = FOCUS_TARGET_TYPES.find((item) => item.toLowerCase() === wanted);
  if (!found) {
    throw new GemArcError('GEMARC_FOCUS_002', `未知的焦点对象类型: ${type}`, String(type));
  }
  return found;
}

/**
 * Call an injected resolver for a focus target type.
 * @param {object} resolvers
 * @param {string} type canonical focus target type
 * @param {string} id
 * @param {object} context
 * @returns {{nodeIds: string[], edgeIds: string[], scope: string|null}|null}
 */
function callResolver(resolvers, type, id, context) {
  const resolver = resolvers?.[type] ?? resolvers?.[type.toLowerCase()] ?? null;
  if (typeof resolver !== 'function') return null;
  const raw = resolver(id, context);
  if (!raw) return null;
  if (Array.isArray(raw)) return { nodeIds: raw.slice(), edgeIds: [], scope: null };
  return {
    nodeIds: Array.isArray(raw.nodeIds) ? raw.nodeIds.slice() : [],
    edgeIds: Array.isArray(raw.edgeIds) ? raw.edgeIds.slice() : [],
    scope: raw.scope ?? null,
  };
}

/**
 * Resolve a focus target into the descriptor the evaluator consumes.
 *
 * @param {object|string} target `{type, id}` or a bare id
 * @param {{store: object, space?: object|null, resolvers?: object}} context
 * @returns {{type: string, id: string, target: object, entityKey: string,
 *            focusNodeIds: string[], focusEdgeIds: string[],
 *            relatedNodeIds: string[], relatedEdgeIds: string[],
 *            scopeNodeIds: string[]}}
 */
export function resolveFocusTarget(target, { store, space = null, resolvers = {} } = {}) {
  const input = normalizeTargetInput(target);
  let type = input.type ? canonicalType(input.type) : null;

  if (!type) {
    if (store.getNode(input.id)) type = 'Node';
    else if (store.getEdge(input.id)) type = 'Edge';
    else if (space?.container) {
      const container = space.container(input.id);
      if (container) type = container.level;
    }
    if (!type) {
      throw new GemArcError('GEMARC_FOCUS_001', '无法定位焦点对象', input.id);
    }
  }

  const base = {
    type,
    id: input.id,
    target: { type, id: input.id },
    entityKey: '',
    focusNodeIds: [],
    focusEdgeIds: [],
    relatedNodeIds: [],
    relatedEdgeIds: [],
    scopeNodeIds: [],
  };

  switch (type) {
    case 'Node': {
      const node = store.getNode(input.id);
      if (!node) throw new GemArcError('GEMARC_FOCUS_001', '焦点节点不存在', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: node.name || input.id },
        entityKey: `node:${input.id}`,
        focusNodeIds: [input.id],
      };
    }
    case 'Edge': {
      const edge = store.getEdge(input.id);
      if (!edge) throw new GemArcError('GEMARC_FOCUS_001', '焦点关系不存在', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: edge.name || edge.id },
        entityKey: `edge:${input.id}`,
        focusEdgeIds: [input.id],
      };
    }
    case 'Building':
    case 'Floor':
    case 'Room': {
      const container = space?.container ? space.container(input.id) : null;
      if (!container || container.level !== type) {
        throw new GemArcError('GEMARC_FOCUS_001', '空间容器不存在', input.id);
      }
      return {
        ...base,
        target: { type, id: input.id, label: container.entity.name || input.id },
        entityKey: `${type.toLowerCase()}:${input.id}`,
        relatedNodeIds: space.nodeIdsOf(input.id),
      };
    }
    case 'Path': {
      const resolved = callResolver(resolvers, 'Path', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '路径对象无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `path:${input.id}`,
        relatedNodeIds: resolved.nodeIds,
        relatedEdgeIds: resolved.edgeIds,
      };
    }
    case 'Event': {
      const node = store.getNode(input.id);
      if (node) {
        return {
          ...base,
          target: { type, id: input.id, label: node.name || input.id },
          entityKey: `node:${input.id}`,
          focusNodeIds: [input.id],
        };
      }
      const resolved = callResolver(resolvers, 'Event', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '事件对象无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `event:${input.id}`,
        relatedNodeIds: resolved.nodeIds,
        relatedEdgeIds: resolved.edgeIds,
      };
    }
    case 'BranchPoint': {
      const resolved = callResolver(resolvers, 'BranchPoint', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '分支点对象无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `branchpoint:${input.id}`,
        relatedNodeIds: resolved.nodeIds,
        relatedEdgeIds: resolved.edgeIds,
      };
    }
    case 'WorkflowNode': {
      const resolved = callResolver(resolvers, 'WorkflowNode', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '工作流节点无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `workflowNode:${input.id}`,
        relatedNodeIds: resolved.nodeIds,
        relatedEdgeIds: resolved.edgeIds,
      };
    }
    case 'WorkflowEdge': {
      const resolved = callResolver(resolvers, 'WorkflowEdge', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '工作流关系无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `workflowEdge:${input.id}`,
        relatedNodeIds: resolved.nodeIds,
        relatedEdgeIds: resolved.edgeIds,
      };
    }
    case 'WorkflowCase': {
      const resolved = callResolver(resolvers, 'WorkflowCase', input.id, { store, space });
      if (!resolved) throw new GemArcError('GEMARC_FOCUS_001', '工作流案例无法解析', input.id);
      return {
        ...base,
        target: { type, id: input.id, label: input.id },
        entityKey: `workflowCase:${input.id}`,
        scopeNodeIds: resolved.nodeIds,
      };
    }
    default:
      throw new GemArcError('GEMARC_FOCUS_002', `未知的焦点对象类型: ${type}`, String(type));
  }
}

/**
 * Coordinates relevance evaluation, the visual retreat and the restoration
 * operations.
 */
export class FocusContextEngine {
  /**
   * @param {{store: object, query?: object|null, space?: object|null,
   *          evaluator?: object|null, animation?: object|null,
   *          restoration?: object|null, resolvers?: object, options?: object}} config
   */
  constructor({
    store,
    query = null,
    space = null,
    evaluator = null,
    animation = null,
    restoration = null,
    resolvers = {},
    options = {},
  } = {}) {
    if (!store) throw new GemArcError('GEMARC_FOCUS_004', '焦点引擎需要图存储');
    this.store = store;
    this.query = query;
    this.space = space;
    this.resolvers = resolvers || {};
    this.options = options || {};
    this.evaluator =
      evaluator || new RelevanceGraphEvaluator({ store, query, space, options: options.relevance });
    this.animation =
      animation ||
      new RetreatAnimationController({
        durationMs: options.durationMs ?? 0,
        ...(options.animation || {}),
      });
    this.restoration =
      restoration ||
      new ContextRestorationManager({
        animation: this.animation,
        evaluator: this.evaluator,
        resolve: (target) => this.resolve(target),
        maxHistory: options.maxHistory ?? 32,
      });
    this.sequence = 0;
  }

  /**
   * Resolve a focus target against the current model.
   * @param {object|string} target
   * @returns {object} resolved focus descriptor
   */
  resolve(target) {
    return resolveFocusTarget(target, {
      store: this.store,
      space: this.space,
      resolvers: this.resolvers,
    });
  }

  /**
   * Focus an object: classify everything, then retreat the rest.
   *
   * @param {object|string} target `{type, id}` or a bare id
   * @param {{durationMs?: number, relevance?: object, onComplete?: Function}} [options]
   * @returns {object} the focus session
   */
  focus(target, options = {}) {
    if (this.restoration.isLocked()) {
      throw new GemArcError('GEMARC_FOCUS_003', '焦点已锁定，无法切换焦点');
    }
    const resolved = this.resolve(target);
    const report = this.evaluator.evaluate(resolved, options.relevance);
    const transition = this.animation.apply(report, {
      durationMs: options.durationMs ?? this.animation.durationMs,
      onComplete: options.onComplete || null,
    });
    this.sequence += 1;
    const session = {
      id: `focus-${this.sequence}`,
      target: resolved.target,
      resolved,
      report,
      transition,
      createdAt: Date.now(),
    };
    this.restoration.record(session);
    return session;
  }

  /**
   * Drop the focus and every display override.
   * @returns {{cleared: boolean}}
   */
  clearFocus() {
    return this.restoration.clearFocus();
  }

  /**
   * Drop the focus but keep the trail as a breadcrumb.
   * @returns {{overview: boolean}}
   */
  restoreOverview() {
    return this.restoration.restoreOverview();
  }

  /**
   * Step back to the previous focus.
   * @param {{durationMs?: number}} [options]
   * @returns {{restored: object|null, transition: object|null, overview: boolean}}
   */
  back(options = {}) {
    return this.restoration.back(options);
  }

  /**
   * Lock or unlock the current focus.
   * @param {boolean} [locked]
   * @returns {boolean} the new lock state
   */
  lockFocus(locked = true) {
    return this.restoration.setLocked(locked);
  }

  /**
   * True when the focus is locked.
   * @returns {boolean}
   */
  isLocked() {
    return this.restoration.isLocked();
  }

  /**
   * The focus trail as a breadcrumb.
   * @returns {Array<{id: string, type: string, targetId: string, label: string}>}
   */
  breadcrumb() {
    return this.restoration.breadcrumb();
  }

  /**
   * Compare two focus targets class by class.
   * @param {object} targetA
   * @param {object} targetB
   * @returns {object} comparison result
   */
  compareFocus(targetA, targetB) {
    return this.restoration.compareFocus(targetA, targetB);
  }

  /**
   * The session that is currently focused.
   * @returns {object|null}
   */
  get current() {
    return this.restoration.current;
  }
}
