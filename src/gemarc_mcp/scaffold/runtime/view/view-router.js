/**
 * View resolution.
 *
 * A view definition is a projection request: "show these nodes, these edge
 * kinds, in this diagram type". `ViewRouter` turns that request into the
 * concrete selection a renderer must draw, and reports everything the model
 * cannot satisfy instead of inventing it.
 *
 * Three rules shape the file:
 *
 *  1. The diagram type is a contract. Only the node kinds and edge kinds the
 *     vocabulary allows may appear. A violation is reported, never silently
 *     accepted, and the offending entity is left out of the selection.
 *  2. Missing fidelity is reported. A `uml-class` view whose nodes carry no
 *     `attributes` produces a `missing` entry so the UI can show "缺失"
 *     rather than draw a fake compartment.
 *  3. The dimension never changes the facts. `resolve(view, {dimension: '2D'})`
 *     and `resolve(view, {dimension: '3D'})` return the same node and edge id
 *     sets; the dimension only decides the projection and the metadata.
 */
import { GemArcError, diagnostic } from '../core/errors.js';
import { diagramTypeSpec } from '../core/vocab.js';
import { readModelPath } from './view-state.js';

/**
 * Fidelity requirements of the vocabulary, and how to check them against the
 * model. A rule reads a plain path on the node or the edge, or uses a
 * predicate for the cases that need to look inside a collection.
 */
const FIDELITY_RULES = {
  attributes: { scope: 'node', paths: ['attributes'] },
  attribute: { scope: 'node', paths: ['attributes'] },
  methods: { scope: 'node', paths: ['methods'] },
  visibility: {
    scope: 'node',
    check: (node) => [...(node.attributes || []), ...(node.methods || [])]
      .some((item) => item && item.visibility),
  },
  multiplicity: { scope: 'edge', paths: ['multiplicity', 'properties.multiplicity'] },
  cardinality: { scope: 'edge', paths: ['cardinality', 'properties.cardinality'] },
  primaryKey: {
    scope: 'node',
    check: (node) => (node.attributes || []).some((item) => item && item.primaryKey),
  },
  foreignKey: {
    scope: 'node',
    check: (node) => (node.attributes || []).some((item) => item && item.foreignKey),
  },
  ports: { scope: 'node', paths: ['ports', 'properties.ports'] },
  interfaces: { scope: 'node', paths: ['interfaces', 'properties.interfaces'] },
  nesting: { scope: 'node', check: (node, context) => context.nodeIds.includes(node.parentId) },
  lifeline: { scope: 'node', paths: ['lifeline', 'properties.lifeline'] },
  message: { scope: 'edge', paths: ['message', 'properties.message'] },
  activation: { scope: 'edge', paths: ['activation', 'properties.activation'] },
  guard: { scope: 'edge', paths: ['guard', 'properties.guard'], check: (edge) => Boolean(edge.condition) },
  initial: { scope: 'node', paths: ['initial', 'properties.initial'] },
  final: { scope: 'node', paths: ['final', 'properties.final'] },
  state: { scope: 'node', paths: ['state', 'properties.state'] },
  decision: { scope: 'node', paths: ['decision', 'properties.decision'] },
  merge: { scope: 'node', paths: ['merge', 'properties.merge'] },
  fork: { scope: 'node', paths: ['fork', 'properties.fork'] },
  join: { scope: 'node', paths: ['join', 'properties.join'] },
  systemBoundary: { scope: 'node', paths: ['systemBoundary', 'properties.systemBoundary'] },
  include: { scope: 'edge', paths: ['include', 'properties.include'] },
  extend: { scope: 'edge', paths: ['extend', 'properties.extend'] },
  externalEntity: { scope: 'node', paths: ['externalEntity', 'properties.externalEntity'] },
  dataStore: { scope: 'node', paths: ['dataStore', 'properties.dataStore'] },
  process: { scope: 'node', paths: ['process', 'properties.process'] },
  node: { scope: 'node', paths: ['node', 'properties.node'] },
  executionEnvironment: { scope: 'node', paths: ['executionEnvironment', 'properties.executionEnvironment'] },
  artifact: { scope: 'node', paths: ['artifact', 'properties.artifact'] },
  building: { scope: 'node', paths: ['spaceRef.buildingId'] },
  floor: { scope: 'node', paths: ['spaceRef.floorId'] },
  room: { scope: 'node', paths: ['spaceRef.roomId'] },
  trustBoundary: {
    scope: 'edge',
    check: (edge) => edge.trustBoundary !== null && edge.trustBoundary !== undefined,
  },
  principal: { scope: 'node', paths: ['principal', 'properties.principal'] },
  slo: { scope: 'node', paths: ['slo', 'properties.slo'] },
  healthCheck: { scope: 'node', paths: ['healthCheck', 'properties.healthCheck'] },
  counterfactual: { scope: 'node', paths: ['counterfactual', 'properties.counterfactual'] },
};

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

function toStringArray(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => typeof item === 'string' && item !== '');
}

/**
 * Check one view definition for structural validity.
 * @throws {GemArcError} GEMARC_VIEW_001 when the definition cannot be used
 */
export function assertViewDefinition(view) {
  if (!view || typeof view !== 'object' || Array.isArray(view)) {
    throw new GemArcError('GEMARC_VIEW_001', '图示定义必须是一个对象');
  }
  if (typeof view.id !== 'string' || view.id === '') {
    throw new GemArcError('GEMARC_VIEW_001', '图示定义缺少 id');
  }
  if (typeof view.title !== 'string' || view.title === '') {
    throw new GemArcError('GEMARC_VIEW_001', '图示定义缺少 title', view.id);
  }
  if (typeof view.diagramType !== 'string' || view.diagramType === '') {
    throw new GemArcError('GEMARC_VIEW_001', '图示定义缺少 diagramType', view.id);
  }
  return view;
}

/**
 * Check whether one entity carries the information a fidelity rule asks for.
 */
function satisfiesFidelity(entity, rule) {
  for (const path of rule.paths || []) {
    const value = readModelPath(entity, path);
    if (value === undefined || value === null || value === false) continue;
    if (Array.isArray(value)) {
      if (value.length > 0) return true;
      continue;
    }
    if (typeof value === 'object') {
      if (Object.keys(value).length > 0) return true;
      continue;
    }
    return true;
  }
  return false;
}

/**
 * Check a fidelity requirement against the resolved selection.
 * @returns {boolean} true when the model can satisfy the requirement
 */
export function checkFidelity(fidelity, nodes, edges, nodeIds = []) {
  const rule = FIDELITY_RULES[fidelity] || { scope: 'both', paths: [fidelity] };
  const context = { nodeIds };
  const matches = (entity) => (rule.check ? Boolean(rule.check(entity, context)) : false)
    || satisfiesFidelity(entity, rule);
  if (rule.scope === 'node' || rule.scope === 'both') {
    for (const node of nodes) {
      if (matches(node)) return true;
    }
  }
  if (rule.scope === 'edge' || rule.scope === 'both') {
    for (const edge of edges) {
      if (matches(edge)) return true;
    }
  }
  return false;
}

/**
 * Turns a view definition into a renderable selection and reports everything
 * the model cannot satisfy.
 */
export class ViewRouter {
  /**
   * @param {{store?: object, state?: object}} [options] the graph store and the
   *   shared view state; the state carries the one filter every renderer uses
   */
  constructor(options = {}) {
    this.store = options.store || null;
    this.state = options.state || null;
  }

  /**
   * Resolve a view definition into a renderable selection.
   * @param {object} view the view definition
   * @param {{dimension?: string}} [options] the projection dimension
   * @returns {{viewId: string, nodeIds: string[], edgeIds: string[],
   *   missing: object[], violations: object[], errors: object[]}}
   */
  resolve(view, options = {}) {
    assertViewDefinition(view);
    const store = this.store;
    const errors = [];
    const violations = [];
    const missing = [];

    const typeSpec = diagramTypeSpec(view.diagramType);
    if (!typeSpec) {
      errors.push(diagnostic('GEMARC_VIEW_002', `图示类型未登记：${view.diagramType}`, String(view.id)));
      return this._result(view, options, {
        typeSpec: null,
        nodeIds: [],
        edgeIds: [],
        errors,
        violations,
        missing,
      });
    }

    const dimension = options.dimension || view.dimension || typeSpec.dimension || '2D';
    const requestedNodeIds = toIdArray(view.nodeIds);
    const requestedEdgeIds = toIdArray(view.edgeIds);
    const edgeKinds = toStringArray(view.edgeKinds);

    // --- nodes -------------------------------------------------------------
    const nodeIds = [];
    for (const id of requestedNodeIds) {
      const node = store ? store.getNode(id) : null;
      if (!node) {
        violations.push(diagnostic('GEMARC_VIEW_003', '图示引用了不存在的节点', id));
        continue;
      }
      if (!typeSpec.nodeKinds.includes(node.kind)) {
        violations.push(diagnostic(
          'GEMARC_VIEW_005',
          `节点类型 ${node.kind} 不允许出现在图示类型 ${typeSpec.id}`,
          id,
        ));
        continue;
      }
      nodeIds.push(id);
    }
    const nodeSet = new Set(nodeIds);

    // --- edges -------------------------------------------------------------
    const edgeIds = [];
    const edgeCandidates = [];
    for (const id of requestedEdgeIds) {
      const edge = store ? store.getEdge(id) : null;
      if (!edge) {
        violations.push(diagnostic('GEMARC_VIEW_004', '图示引用了不存在的关系', id));
        continue;
      }
      edgeCandidates.push(edge);
    }
    if (requestedEdgeIds.length === 0 && store) {
      for (const edge of store.edges.values()) {
        if (nodeSet.has(edge.sourceId) && nodeSet.has(edge.targetId)) edgeCandidates.push(edge);
      }
    }
    for (const edge of edgeCandidates) {
      if (!typeSpec.edgeKinds.includes(edge.type)) {
        violations.push(diagnostic(
          'GEMARC_VIEW_006',
          `关系类型 ${edge.type} 不允许出现在图示类型 ${typeSpec.id}`,
          edge.id,
        ));
        continue;
      }
      if (edgeKinds.length > 0 && !edgeKinds.includes(edge.type)) continue;
      if (!nodeSet.has(edge.sourceId) || !nodeSet.has(edge.targetId)) {
        violations.push(diagnostic('GEMARC_VIEW_012', '关系的端点不在该图示的节点范围内', edge.id));
        continue;
      }
      edgeIds.push(edge.id);
    }

    // --- the shared filter wins over the view -------------------------------
    const filtered = this._applySharedFilter(nodeIds, edgeIds);
    const finalNodeIds = filtered.nodeIds;
    const finalEdgeIds = filtered.edgeIds;

    // --- what the model cannot satisfy --------------------------------------
    const finalNodes = store ? finalNodeIds.map((id) => store.getNode(id)).filter(Boolean) : [];
    const finalEdges = store ? finalEdgeIds.map((id) => store.getEdge(id)).filter(Boolean) : [];

    if (finalNodeIds.length === 0) {
      missing.push(diagnostic('GEMARC_VIEW_011', '该图示没有选中任何节点', String(view.id), 'WARNING'));
    } else {
      for (const fidelity of typeSpec.fidelity || []) {
        if (checkFidelity(fidelity, finalNodes, finalEdges, finalNodeIds)) continue;
        missing.push(diagnostic(
          'GEMARC_VIEW_007',
          `图示类型 ${typeSpec.id} 要求的保真信息「${fidelity}」在模型中缺失`,
          String(view.id),
          'WARNING',
        ));
      }
    }

    if (dimension === '3D' && !hasCompleteAxes(view.axes)) {
      missing.push(diagnostic('GEMARC_VIEW_008', '3D 图示必须声明 x/y/z 三根空间轴的字段来源', String(view.id), 'WARNING'));
    }
    if ((dimension === '4D' || dimension === '5D') && !view.scenarioId) {
      missing.push(diagnostic('GEMARC_VIEW_009', '4D/5D 图示必须绑定 scenarioId', String(view.id), 'WARNING'));
    }
    if (view.scenarioId && this.state && this.state.scenarioId && this.state.scenarioId !== view.scenarioId) {
      violations.push(diagnostic(
        'GEMARC_VIEW_010',
        '图示绑定的场景与共享状态中的场景不一致，跨视图事实可能不同',
        String(view.id),
        'WARNING',
      ));
    }
    if (Array.isArray(view.reviewQuestions) && view.reviewQuestions.length === 0) {
      missing.push(diagnostic('GEMARC_VIEW_014', '该图示未定义审查问题', String(view.id), 'WARNING'));
    }
    for (const item of toStringArray(view.missing)) {
      missing.push(diagnostic('GEMARC_VIEW_015', item, String(view.id), 'WARNING'));
    }

    return this._result(view, { ...options, dimension }, {
      typeSpec,
      nodeIds: finalNodeIds,
      edgeIds: finalEdgeIds,
      errors,
      violations,
      missing,
    });
  }

  /**
   * Resolve a view without throwing; problems land in `errors` instead.
   * @param {object} view the view definition
   * @param {{dimension?: string}} [options] the projection dimension
   */
  tryResolve(view, options = {}) {
    try {
      return this.resolve(view, options);
    } catch (error) {
      return {
        viewId: view && typeof view.id === 'string' ? view.id : '',
        title: view && typeof view.title === 'string' ? view.title : '',
        diagramType: view && typeof view.diagramType === 'string' ? view.diagramType : '',
        dimension: options.dimension || '2D',
        typeSpec: null,
        nodeIds: [],
        edgeIds: [],
        nodes: [],
        edges: [],
        metadata: {},
        missing: [],
        violations: [],
        errors: [error instanceof GemArcError
          ? error.toDiagnostic()
          : diagnostic('GEMARC_INTERNAL_001', '图示解析失败')],
        factFingerprint: '0:|0:',
      };
    }
  }

  /** Resolve a view for the 2D projection. */
  resolve2D(view) {
    return this.resolve(view, { dimension: '2D' });
  }

  /** Resolve a view for the 3D projection. */
  resolve3D(view) {
    return this.resolve(view, { dimension: '3D' });
  }

  /**
   * Intersect the view selection with the shared filter. Every renderer uses
   * the same filter, so no view can show facts another view hides.
   */
  _applySharedFilter(nodeIds, edgeIds) {
    if (!this.state || typeof this.state.filteredView !== 'function') {
      return { nodeIds: [...nodeIds], edgeIds: [...edgeIds] };
    }
    const filtered = this.state.filteredView();
    const keptNodes = new Set(filtered.nodeIds);
    const keptEdges = new Set(filtered.edgeIds);
    return {
      nodeIds: nodeIds.filter((id) => keptNodes.has(id)),
      edgeIds: edgeIds.filter((id) => keptEdges.has(id)),
    };
  }

  _result(view, options, parts) {
    const store = this.store;
    const { typeSpec, nodeIds, edgeIds, errors, violations, missing } = parts;
    const sortedNodes = [...nodeIds].sort();
    const sortedEdges = [...edgeIds].sort();
    return {
      viewId: String(view.id),
      title: view.title,
      diagramType: view.diagramType,
      dimension: options.dimension || view.dimension || (typeSpec ? typeSpec.dimension : '2D'),
      notation: typeSpec ? typeSpec.notation : null,
      typeSpec,
      nodeIds: [...nodeIds],
      edgeIds: [...edgeIds],
      nodes: store ? nodeIds.map((id) => store.getNode(id)).filter(Boolean) : [],
      edges: store ? edgeIds.map((id) => store.getEdge(id)).filter(Boolean) : [],
      metadata: {
        axes: view.axes && typeof view.axes === 'object' ? { ...view.axes } : {},
        scenarioId: view.scenarioId ?? null,
        caseIds: toStringArray(view.caseIds),
        stakeholders: toStringArray(view.stakeholders),
        concerns: toStringArray(view.concerns),
        reviewQuestions: toStringArray(view.reviewQuestions),
        legend: view.legend && typeof view.legend === 'object' ? { ...view.legend } : {},
        selection: this.state
          ? {
            nodeIds: [...this.state.selectedNodeIds],
            edgeIds: [...this.state.selectedEdgeIds],
          }
          : { nodeIds: [], edgeIds: [] },
      },
      missing,
      violations,
      errors,
      factFingerprint: `${sortedNodes.length}:${sortedNodes.join(',')}|${sortedEdges.length}:${sortedEdges.join(',')}`,
    };
  }
}

/**
 * True when a 3D view declares a traceable field for all three axes.
 * @param {object} axes the axis definitions of the view
 */
export function hasCompleteAxes(axes) {
  if (!axes || typeof axes !== 'object') return false;
  for (const axis of ['x', 'y', 'z']) {
    const definition = axes[axis];
    if (!definition || typeof definition !== 'object') return false;
    if (typeof definition.field !== 'string' || definition.field === '') return false;
  }
  return true;
}
