/**
 * WorkflowValidator - semantic validation of a canonical workflow.
 *
 * This is *not* JSON Schema shape validation (the contract file owns that).
 * It answers the questions the requirement document asks about machine
 * readable workflow semantics:
 *   - do all references resolve (edges, ports, compensation nodes, subprocesses)?
 *   - is the flow executable (a start node, an end node, reachable nodes)?
 *   - are conditions machine judgeable rather than prose?
 *   - are execution constraints complete?
 *
 * Presentation and execution stay separated: **no rule in this file reads
 * `canvas`**. Moving a node on the canvas cannot change a single diagnostic.
 *
 * Diagnostic codes:
 *   GEMARC_WORKFLOW_001 dangling reference (edge endpoint, port, compensation
 *                          node, subprocess target)
 *   GEMARC_WORKFLOW_002 semantic violation (missing start/end, decision
 *                          without a conditional branch, conditional edge
 *                          without a condition, prose condition, incomplete
 *                          timeout / retry / compensation constraint)
 *   GEMARC_WORKFLOW_003 duplicate id
 *   GEMARC_WORKFLOW_004 unreachable node (warning)
 *   GEMARC_WORKFLOW_005 invalid workflow shape
 *   GEMARC_WORKFLOW_006 id that is not registered in the vocabulary
 */
import { GemArcError, diagnostic } from '../core/errors.js';
import { isKnownNodeKind, isKnownEdgeKind, isKnownConstraintKind, isKnownPortKind } from './workflow-model.js';

const ERROR = 'ERROR';
const WARNING = 'WARNING';

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * A condition must be judgeable by a program, not written as prose.
 * Accepted: an expression containing an operator, a call, or a bare
 * identifier / property path. Everything else is prose.
 */
export function isMachineJudgableCondition(condition) {
  const value = text(condition);
  if (!value) return false;
  if (/[=!<>]|\b(?:and|or|not|in|matches|between|contains|startsWith|endsWith)\b|&&|\|\||\btrue\b|\bfalse\b/i.test(value)) {
    return true;
  }
  if (/^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$/.test(value)) return true;
  if (/^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*\s*\(.*\)$/.test(value)) return true;
  return false;
}

/**
 * WorkflowValidator - collects semantic diagnostics for a canonical workflow.
 */
export class WorkflowValidator {
  /**
   * @param {{processes?: Iterable<string>, strict?: boolean}} [options]
   *   `processes` lists the process ids a `subprocess` node may call.
   */
  constructor(options = {}) {
    /** @type {Set<string>} known callable process ids */
    this.processes = new Set(options.processes || []);
    /** @type {boolean} when true, warnings are reported as errors */
    this.strict = Boolean(options.strict);
  }

  /**
   * Validate a canonical workflow.
   * @param {object} workflow
   * @returns {{ok: boolean, diagnostics: object[], errors: object[], warnings: object[]}}
   */
  validate(workflow) {
    const diagnostics = [];
    const push = (code, message, entityId, severity = ERROR) => {
      diagnostics.push(diagnostic(code, message, entityId, severity));
    };

    if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) {
      push('GEMARC_WORKFLOW_005', '工作流必须是对象');
      return this._result(diagnostics);
    }

    if (!text(workflow.id)) {
      push('GEMARC_WORKFLOW_005', '工作流缺少 ID');
    }
    if (!text(workflow.name)) {
      push('GEMARC_WORKFLOW_005', '工作流缺少名称');
    }
    if (workflow.nodes !== undefined && !Array.isArray(workflow.nodes)) {
      push('GEMARC_WORKFLOW_005', '工作流 nodes 必须是数组');
    }
    if (workflow.edges !== undefined && !Array.isArray(workflow.edges)) {
      push('GEMARC_WORKFLOW_005', '工作流 edges 必须是数组');
    }

    const nodes = asArray(workflow.nodes);
    const edges = asArray(workflow.edges);

    const nodeById = new Map();
    const duplicateNodeIds = new Set();
    for (const raw of nodes) {
      const node = asObject(raw);
      const id = text(node.id);
      if (!id) {
        push('GEMARC_WORKFLOW_005', '节点缺少 ID');
        continue;
      }
      if (nodeById.has(id)) {
        duplicateNodeIds.add(id);
        push('GEMARC_WORKFLOW_003', '节点 ID 重复', id);
        continue;
      }
      nodeById.set(id, node);
    }

    const edgeById = new Map();
    for (const raw of edges) {
      const edge = asObject(raw);
      const id = text(edge.id);
      if (!id) {
        push('GEMARC_WORKFLOW_005', '边缺少 ID');
        continue;
      }
      if (edgeById.has(id)) {
        push('GEMARC_WORKFLOW_003', '边 ID 重复', id);
        continue;
      }
      edgeById.set(id, edge);
    }

    // --- node level rules ---------------------------------------------------
    const portIdsByNode = new Map();
    for (const [id, node] of nodeById) {
      const kind = text(node.kind);
      if (kind && !isKnownNodeKind(kind)) {
        push('GEMARC_WORKFLOW_006', '未登记的节点类型', id);
      }
      if (!text(node.name)) {
        push('GEMARC_WORKFLOW_002', '节点缺少名称', id);
      }
      const portIds = new Set();
      for (const rawPort of asArray(node.ports)) {
        const port = asObject(rawPort);
        const portId = text(port.id);
        if (!portId) {
          push('GEMARC_WORKFLOW_005', '端口缺少 ID', id);
          continue;
        }
        if (portIds.has(portId)) {
          push('GEMARC_WORKFLOW_003', '节点端口 ID 重复', `${id}#${portId}`);
          continue;
        }
        portIds.add(portId);
        const portKind = text(port.kind);
        if (portKind && !isKnownPortKind(portKind)) {
          push('GEMARC_WORKFLOW_006', '未登记的端口类型', `${id}#${portId}`);
        }
      }
      portIdsByNode.set(id, portIds);

      for (const rawConstraint of asArray(node.constraints)) {
        this._checkConstraint(push, id, asObject(rawConstraint));
      }

      const exceptionHandling = node.exceptionHandling ? asObject(node.exceptionHandling) : null;
      if (exceptionHandling) {
        const errorPortId = text(exceptionHandling.errorPortId);
        if (errorPortId && !portIds.has(errorPortId)) {
          push('GEMARC_WORKFLOW_001', '异常处理引用的端口不存在', `${id}#${errorPortId}`);
        }
      }

      if (kind === 'subprocess' && !this._isEmbeddedSubprocess(node)) {
        const target = this._subprocessTarget(node);
        if (!target) {
          push('GEMARC_WORKFLOW_002', '子流程节点必须声明被调用的流程 ID', id);
        } else if (this.processes.size > 0 && !this.processes.has(target)) {
          push('GEMARC_WORKFLOW_001', '子流程引用的流程不存在', `${id}->${target}`);
        }
      }

      if (kind === 'event') {
        const trigger = text(node.dataContract?.trigger) || text(node.properties?.trigger);
        if (!trigger) {
          push('GEMARC_WORKFLOW_002', '事件节点必须声明触发条件', id);
        }
      }
    }

    // --- edge level rules ---------------------------------------------------
    const outgoingByNode = new Map();
    const incomingByNode = new Map();
    for (const [id, edge] of edgeById) {
      const sourceId = text(edge.sourceId);
      const targetId = text(edge.targetId);
      const kind = text(edge.kind);

      if (kind && !isKnownEdgeKind(kind)) {
        push('GEMARC_WORKFLOW_006', '未登记的关系类型', id);
      }
      if (!sourceId || !nodeById.has(sourceId)) {
        push('GEMARC_WORKFLOW_001', '边的源节点不存在', id);
      }
      if (!targetId || !nodeById.has(targetId)) {
        push('GEMARC_WORKFLOW_001', '边的目标节点不存在', id);
      }

      if (sourceId && nodeById.has(sourceId)) {
        const sourcePorts = portIdsByNode.get(sourceId) || new Set();
        const sourcePortId = text(edge.sourcePortId);
        if (sourcePortId && !sourcePorts.has(sourcePortId)) {
          push('GEMARC_WORKFLOW_001', '边引用的源端口不存在', `${id}#${sourcePortId}`);
        }
      }
      if (targetId && nodeById.has(targetId)) {
        const targetPorts = portIdsByNode.get(targetId) || new Set();
        const targetPortId = text(edge.targetPortId);
        if (targetPortId && !targetPorts.has(targetPortId)) {
          push('GEMARC_WORKFLOW_001', '边引用的目标端口不存在', `${id}#${targetPortId}`);
        }
      }

      const condition = edge.condition === '' ? null : edge.condition;
      if (kind === 'conditional') {
        if (!text(condition)) {
          push('GEMARC_WORKFLOW_002', '条件流必须给出 condition 表达式', id);
        } else if (!isMachineJudgableCondition(condition)) {
          push('GEMARC_WORKFLOW_002', 'condition 必须是可被程序判断的表达式，不得只写自然语言', id);
        }
      }

      if (sourceId && nodeById.has(sourceId)) {
        if (!outgoingByNode.has(sourceId)) outgoingByNode.set(sourceId, []);
        outgoingByNode.get(sourceId).push(edge);
      }
      if (targetId && nodeById.has(targetId)) {
        if (!incomingByNode.has(targetId)) incomingByNode.set(targetId, []);
        incomingByNode.get(targetId).push(edge);
      }
    }

    // --- flow level rules ---------------------------------------------------
    const startNodes = [...nodeById.values()].filter((node) => text(node.kind) === 'start');
    const endNodes = [...nodeById.values()].filter((node) => text(node.kind) === 'end');
    if (nodeById.size > 0 && startNodes.length === 0) {
      push('GEMARC_WORKFLOW_002', '工作流缺少开始节点');
    }
    if (nodeById.size > 0 && endNodes.length === 0) {
      push('GEMARC_WORKFLOW_002', '工作流缺少结束节点');
    }

    for (const node of nodeById.values()) {
      if (text(node.kind) !== 'decision') continue;
      const outgoing = outgoingByNode.get(text(node.id)) || [];
      const hasConditional = outgoing.some((edge) => text(edge.kind) === 'conditional');
      if (!hasConditional) {
        push('GEMARC_WORKFLOW_002', '判定节点必须至少有一条条件流', text(node.id));
      }
    }

    const reachable = this._reachable(nodeById, outgoingByNode, startNodes.map((node) => text(node.id)));
    if (startNodes.length > 0) {
      for (const id of nodeById.keys()) {
        if (!reachable.has(id)) {
          push('GEMARC_WORKFLOW_004', '节点无法从开始节点到达', id, this.strict ? ERROR : WARNING);
        }
      }
    }

    return this._result(diagnostics);
  }

  /**
   * Validate and throw when the workflow is not semantically sound.
   * @param {object} workflow
   * @returns {object} the validation result
   */
  assertValid(workflow) {
    const result = this.validate(workflow);
    if (!result.ok) {
      const first = result.errors[0];
      throw new GemArcError(first.id, first.message, first.entityId);
    }
    return result;
  }

  // --- internals -----------------------------------------------------------

  _result(diagnostics) {
    const errors = diagnostics.filter((item) => item.severity === ERROR);
    const warnings = diagnostics.filter((item) => item.severity !== ERROR);
    return { ok: errors.length === 0, diagnostics, errors, warnings };
  }

  _checkConstraint(push, nodeId, constraint) {
    const kind = text(constraint.kind);
    if (!kind) {
      push('GEMARC_WORKFLOW_005', '执行约束缺少 kind', nodeId);
      return;
    }
    if (!isKnownConstraintKind(kind)) {
      push('GEMARC_WORKFLOW_006', '未登记的执行约束', `${nodeId}#${kind}`);
      return;
    }
    const hasValue = constraint.value !== undefined && constraint.value !== null && constraint.value !== '';
    if ((kind === 'timeout' || kind === 'retry') && !hasValue) {
      push('GEMARC_WORKFLOW_002', `${kind} 约束必须声明 value`, nodeId);
    }
    if (kind === 'timeout' && !text(constraint.onTimeout)) {
      push('GEMARC_WORKFLOW_002', 'timeout 约束必须声明超时后的处理', nodeId);
    }
    if (kind === 'compensation') {
      const compensationNodeId = text(constraint.compensationNodeId);
      if (!compensationNodeId) {
        push('GEMARC_WORKFLOW_002', 'compensation 约束必须声明 compensationNodeId', nodeId);
      }
    }
  }

  _subprocessTarget(node) {
    const candidates = [
      node.targetProcessId,
      node.dataContract?.processId,
      node.properties?.processId,
    ];
    for (const candidate of candidates) {
      const value = text(candidate);
      if (value) return value;
    }
    return '';
  }

  /**
   * An embedded sub process (BPMN `<subProcess>`) carries its own body and
   * therefore calls no other process; a call activity does and must name it.
   */
  _isEmbeddedSubprocess(node) {
    return node.properties?.embedded === true;
  }

  _reachable(nodeById, outgoingByNode, startIds) {
    const seen = new Set(startIds);
    const queue = [...startIds];
    while (queue.length) {
      const current = queue.shift();
      for (const edge of outgoingByNode.get(current) || []) {
        const kind = text(edge.kind);
        if (kind === 'association') continue;
        const targetId = text(edge.targetId);
        if (!targetId || !nodeById.has(targetId) || seen.has(targetId)) continue;
        seen.add(targetId);
        queue.push(targetId);
      }
    }
    return seen;
  }
}

/**
 * Convenience wrapper around `WorkflowValidator.validate`.
 * @param {object} workflow
 * @param {{processes?: Iterable<string>, strict?: boolean}} [options]
 */
export function validateWorkflow(workflow, options = {}) {
  return new WorkflowValidator(options).validate(workflow);
}
