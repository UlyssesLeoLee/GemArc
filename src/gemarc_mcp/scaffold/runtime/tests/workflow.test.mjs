/**
 * Workflow runtime tests (v5.0 §2.7).
 *
 * The requirement this file guards is the separation of the presentation model
 * and the execution model: a workflow must carry machine readable semantics
 * (nodes, ports, relations, conditions, exceptions, execution constraints),
 * and canvas coordinates, colours and animations must never decide what the
 * workflow means.
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/workflow.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { GemArcError } from '../core/errors.js';
import {
  WorkflowModel,
  WorkflowValidator,
  validateWorkflow,
  isMachineJudgableCondition,
  CanvasController,
} from '../workflow/index.js';

/**
 * A valid workflow: a start node, a decision with two conditional branches,
 * a task that carries ports / constraints / a data contract / exception
 * handling, a compensation task and an end node.
 */
function baseWorkflow() {
  return {
    id: 'wf-order',
    name: '订单处理',
    kind: 'process',
    nodes: [
      {
        id: 'start',
        kind: 'start',
        name: '开始',
        ports: [{ id: 'p_out', kind: 'output', name: '订单' }],
        canvas: { x: 0, y: 0, width: 160, height: 72 },
      },
      {
        id: 'decide',
        kind: 'decision',
        name: '判定',
        ports: [
          { id: 'p_ok', kind: 'output', name: '大额' },
          { id: 'p_small', kind: 'output', name: '小额' },
        ],
        canvas: { x: 200, y: 0, width: 160, height: 72 },
      },
      {
        id: 'charge',
        kind: 'task',
        name: '扣款',
        canonicalNodeId: 'svc-charge',
        ports: [
          { id: 'in', kind: 'input', name: '订单', schemaRef: 'schema://order' },
          { id: 'out', kind: 'output', name: '收据' },
          { id: 'err', kind: 'error', name: '失败' },
        ],
        constraints: [
          { kind: 'order', value: 1 },
          { kind: 'timeout', value: 5000, onTimeout: 'compensate' },
        ],
        dataContract: { input: 'Order', output: 'Receipt' },
        exceptionHandling: { onError: 'compensate', errorPortId: 'err' },
        canvas: { x: 400, y: 0, width: 160, height: 72 },
      },
      {
        id: 'compensate',
        kind: 'task',
        name: '补偿',
        canvas: { x: 400, y: 160, width: 160, height: 72 },
      },
      {
        id: 'end',
        kind: 'end',
        name: '结束',
        ports: [{ id: 'p_in', kind: 'input' }],
        canvas: { x: 600, y: 0, width: 160, height: 72 },
      },
    ],
    edges: [
      { id: 'e1', sourceId: 'start', targetId: 'decide', sourcePortId: 'p_out', kind: 'sequence' },
      { id: 'e2', sourceId: 'decide', targetId: 'charge', sourcePortId: 'p_ok', kind: 'conditional', condition: 'order.amount > 100' },
      { id: 'e3', sourceId: 'decide', targetId: 'end', sourcePortId: 'p_small', kind: 'conditional', condition: 'order.amount <= 100' },
      { id: 'e4', sourceId: 'charge', targetId: 'end', sourcePortId: 'out', kind: 'sequence' },
      { id: 'e5', sourceId: 'charge', targetId: 'compensate', sourcePortId: 'err', kind: 'sequence' },
    ],
    variables: [{ name: 'order', type: 'Order' }],
  };
}

/** Every execution field of the model, with all presentation data removed. */
function executionSnapshot(model) {
  return {
    nodes: model.nodeList.map((node) => ({
      id: node.id,
      kind: node.kind,
      name: node.name,
      canonicalNodeId: node.canonicalNodeId,
      ports: node.ports.map((port) => ({
        id: port.id,
        kind: port.kind,
        name: port.name,
        schemaRef: port.schemaRef,
      })),
      constraints: node.constraints.map((constraint) => ({
        kind: constraint.kind,
        value: constraint.value,
        backoffMs: constraint.backoffMs,
        onTimeout: constraint.onTimeout,
        compensationNodeId: constraint.compensationNodeId,
      })),
      dataContract: node.dataContract,
      exceptionHandling: node.exceptionHandling,
    })),
    edges: model.edgeList.map((edge) => ({
      id: edge.id,
      sourceId: edge.sourceId,
      targetId: edge.targetId,
      sourcePortId: edge.sourcePortId,
      targetPortId: edge.targetPortId,
      kind: edge.kind,
      directed: edge.directed,
      condition: edge.condition,
      dataContract: edge.dataContract,
    })),
  };
}

function findError(result, code, entityId = null) {
  return result.errors.find((item) => (
    item.id === code && (entityId === null || item.entityId === entityId)
  ));
}

test('a complete workflow validates without a single diagnostic', () => {
  const result = validateWorkflow(baseWorkflow());

  assert.equal(result.ok, true);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.warnings, []);
  assert.deepEqual(result.diagnostics, []);
});

test('a dangling edge endpoint is reported', () => {
  const workflow = baseWorkflow();
  workflow.edges.push({ id: 'e6', sourceId: 'charge', targetId: 'ghost', kind: 'sequence' });

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  const error = findError(result, 'GEMARC_WORKFLOW_001', 'e6');
  assert.ok(error, 'the dangling target must be reported');
  assert.equal(error.severity, 'ERROR');
});

test('a dangling edge source is reported', () => {
  const workflow = baseWorkflow();
  workflow.edges.push({ id: 'e7', sourceId: 'ghost', targetId: 'charge', kind: 'sequence' });

  const result = validateWorkflow(workflow);

  assert.ok(findError(result, 'GEMARC_WORKFLOW_001', 'e7'));
});

test('a decision node without a conditional branch is reported', () => {
  const workflow = baseWorkflow();
  workflow.edges.find((edge) => edge.id === 'e2').kind = 'sequence';
  workflow.edges.find((edge) => edge.id === 'e3').kind = 'sequence';

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  const error = findError(result, 'GEMARC_WORKFLOW_002', 'decide');
  assert.ok(error, 'a decision without a conditional branch must be reported');
});

test('a conditional edge without a condition is reported', () => {
  const workflow = baseWorkflow();
  delete workflow.edges.find((edge) => edge.id === 'e2').condition;

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_002', 'e2'));
});

test('a missing start node is reported', () => {
  const workflow = baseWorkflow();
  workflow.nodes = workflow.nodes.filter((node) => node.kind !== 'start');
  workflow.edges = workflow.edges.filter((edge) => edge.sourceId !== 'start');

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(
    result.errors.some((item) => item.id === 'GEMARC_WORKFLOW_002' && item.message.includes('开始节点')),
    'the missing start node must be reported as an error',
  );
});

test('a duplicate node id is reported', () => {
  const workflow = baseWorkflow();
  workflow.nodes.push({ id: 'charge', kind: 'task', name: '重复扣款' });

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_003', 'charge'));
});

test('an edge that references a port the node does not have is reported', () => {
  const workflow = baseWorkflow();
  workflow.edges.find((edge) => edge.id === 'e2').sourcePortId = 'no_such_port';

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_001', 'e2#no_such_port'));
});

test('an edge that references a missing target port is reported', () => {
  const workflow = baseWorkflow();
  workflow.edges.find((edge) => edge.id === 'e4').targetPortId = 'no_such_port';

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_001', 'e4#no_such_port'));
});

test('a timeout constraint without a value is reported', () => {
  const workflow = baseWorkflow();
  workflow.nodes.find((node) => node.id === 'charge').constraints.push({
    kind: 'timeout',
    onTimeout: 'abort',
  });

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  const error = findError(result, 'GEMARC_WORKFLOW_002', 'charge');
  assert.ok(error);
  assert.ok(error.message.includes('timeout'));
});

test('a compensation constraint without a compensationNodeId is reported', () => {
  const workflow = baseWorkflow();
  workflow.nodes.find((node) => node.id === 'charge').constraints.push({ kind: 'compensation' });

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  const error = findError(result, 'GEMARC_WORKFLOW_002', 'charge');
  assert.ok(error);
  assert.ok(error.message.includes('compensationNodeId'));
});

test('a prose condition is rejected, an expression is accepted', () => {
  assert.equal(isMachineJudgableCondition('order.amount > 100'), true);
  assert.equal(isMachineJudgableCondition('isValid(order)'), true);
  assert.equal(isMachineJudgableCondition('status'), true);
  assert.equal(isMachineJudgableCondition('如果金额超过 100 就通过'), false);
  assert.equal(isMachineJudgableCondition(''), false);

  const workflow = baseWorkflow();
  workflow.edges.find((edge) => edge.id === 'e2').condition = '如果金额超过 100 就通过';

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, false);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_002', 'e2'));
});

test('an unreachable node is a warning, not an error', () => {
  const workflow = baseWorkflow();
  workflow.nodes.push({ id: 'orphan', kind: 'task', name: '孤立任务' });

  const result = validateWorkflow(workflow);

  assert.equal(result.ok, true);
  assert.ok(findError(result, 'GEMARC_WORKFLOW_004', 'orphan') === undefined);
  assert.ok(result.warnings.some((item) => item.id === 'GEMARC_WORKFLOW_004' && item.entityId === 'orphan'));
});

test('assertValid throws a GemArcError carrying the first diagnostic code', () => {
  const validator = new WorkflowValidator();
  const workflow = baseWorkflow();
  workflow.edges.push({ id: 'e6', sourceId: 'charge', targetId: 'ghost', kind: 'sequence' });

  assert.throws(() => validator.assertValid(workflow), (error) => {
    assert.ok(error instanceof GemArcError);
    assert.equal(error.name, 'GemArcError');
    assert.equal(error.code, 'GEMARC_WORKFLOW_001');
    assert.equal(error.entityId, 'e6');
    return true;
  });
  assert.doesNotThrow(() => validator.assertValid(baseWorkflow()));
});

test('the key invariant: moving a node does not change the validation result', () => {
  const workflow = baseWorkflow();
  const before = validateWorkflow(workflow);

  const model = new WorkflowModel(workflow);
  const controller = new CanvasController(model);
  controller.moveNode('charge', { x: 999, y: -42 });
  controller.moveNode('decide', { x: 12, y: 34 });
  controller.moveNode('end', { x: 5000, y: 5000 });
  controller.pan(320, -180);
  controller.zoomTo(2.5);

  const after = validateWorkflow(model.toJSON());

  assert.deepEqual(after.diagnostics, before.diagnostics);
  assert.equal(after.ok, before.ok);
  // The move really happened: the canvas data is different.
  assert.notEqual(model.node('charge').canvas.x, 400);
});

test('the key invariant holds for a workflow that already has a defect', () => {
  const workflow = baseWorkflow();
  workflow.edges.push({ id: 'e6', sourceId: 'charge', targetId: 'ghost', kind: 'sequence' });
  const before = validateWorkflow(workflow);

  const model = new WorkflowModel(workflow);
  const controller = new CanvasController(model);
  controller.moveNode('charge', { x: -900, y: 77 });
  controller.zoomBy(0.25);

  const after = validateWorkflow(model.toJSON());

  assert.equal(before.ok, false);
  assert.deepEqual(after.diagnostics, before.diagnostics);
});

test('pan, zoom and node drag only touch the canvas fields', () => {
  const workflow = baseWorkflow();
  const model = new WorkflowModel(workflow);
  const controller = new CanvasController(model);
  const before = executionSnapshot(model);

  controller.pan(240, -90);
  controller.zoomTo(1.75, { x: 200, y: 100 });
  controller.beginDrag('charge', { x: 0, y: 0 });
  controller.dragTo({ x: 180, y: 60 });
  controller.endDrag();
  controller.setNodeSize('charge', { width: 220, height: 90 });
  controller.routeEdge('e1');
  controller.select(['charge', 'decide']);

  const after = executionSnapshot(model);

  assert.deepEqual(after, before);
  // The presentation did change.
  assert.notDeepEqual(controller.getTransform(), { x: 0, y: 0, scale: 1 });
  assert.notEqual(model.node('charge').canvas.x, 400);
  assert.notDeepEqual(model.edge('e1').canvas.points, []);
});

test('a node move is reflected in the model but not in the execution semantics', () => {
  const workflow = baseWorkflow();
  const model = new WorkflowModel(workflow);
  const controller = new CanvasController(model);
  const before = validateWorkflow(model.toJSON());

  const canvas = controller.moveNode('charge', { x: 800, y: 450 });

  assert.deepEqual(canvas, { x: 800, y: 450, width: 160, height: 72 });
  assert.deepEqual(model.node('charge').canvas, { x: 800, y: 450, width: 160, height: 72 });
  assert.equal(model.toJSON().nodes.find((node) => node.id === 'charge').canvas.x, 800);

  const after = validateWorkflow(model.toJSON());
  assert.deepEqual(after.diagnostics, before.diagnostics);
});

test('the canvas controller rejects unknown nodes and edges with a stable code', () => {
  const controller = new CanvasController(new WorkflowModel(baseWorkflow()));

  assert.throws(() => controller.moveNode('ghost', { x: 1, y: 1 }), (error) => {
    assert.ok(error instanceof GemArcError);
    assert.equal(error.code, 'GEMARC_WORKFLOW_001');
    return true;
  });
  assert.throws(() => controller.setEdgePoints('ghost', []), (error) => {
    assert.equal(error.code, 'GEMARC_WORKFLOW_001');
    return true;
  });
});

test('WorkflowModel answers what runs after a node', () => {
  const model = new WorkflowModel(baseWorkflow());

  const successors = model.successorsOf('decide');

  assert.deepEqual(successors.map((item) => item.node.id).sort(), ['charge', 'end']);
  assert.deepEqual(successors.map((item) => item.edge.id).sort(), ['e2', 'e3']);
  assert.deepEqual(model.successorsOf('end'), []);
  // An association-free, port aware narrowing works as well.
  assert.deepEqual(
    model.successorsOf('decide', { kind: 'conditional' }).map((item) => item.node.id).sort(),
    ['charge', 'end'],
  );
  assert.deepEqual(
    model.successorsOf('decide', { portId: 'p_ok' }).map((item) => item.node.id),
    ['charge'],
  );
});

test('WorkflowModel answers what a node requires', () => {
  const model = new WorkflowModel(baseWorkflow());

  const requirements = model.requirementsOf('charge');

  assert.equal(requirements.nodeId, 'charge');
  assert.deepEqual(requirements.ports.input.map((port) => port.id), ['in']);
  assert.deepEqual(requirements.ports.output.map((port) => port.id), ['out']);
  assert.deepEqual(requirements.ports.control, []);
  assert.deepEqual(requirements.ports.error.map((port) => port.id), ['err']);
  assert.deepEqual(requirements.inbound.map((item) => item.edgeId), ['e2']);
  assert.deepEqual(requirements.inbound.map((item) => item.sourceId), ['decide']);
  assert.deepEqual(requirements.inbound.map((item) => item.condition), ['order.amount > 100']);
  assert.deepEqual(requirements.dataContract, { input: 'Order', output: 'Receipt' });
  assert.deepEqual(requirements.exceptionHandling, { onError: 'compensate', errorPortId: 'err' });
  assert.deepEqual(model.incomingPorts('charge').map((port) => port.id).sort(), ['err', 'in']);
  assert.deepEqual(model.outgoingPorts('charge').map((port) => port.id).sort(), ['out']);
});

test('WorkflowModel answers which constraints apply', () => {
  const model = new WorkflowModel(baseWorkflow());

  assert.deepEqual(
    model.constraintsOf('charge').map((constraint) => constraint.kind),
    ['order', 'timeout'],
  );
  assert.deepEqual(
    model.constraintsOf('charge', 'timeout').map((constraint) => constraint.value),
    [5000],
  );
  assert.deepEqual(model.constraintsOf('decide'), []);
});

test('WorkflowModel refuses to describe an unknown node', () => {
  const model = new WorkflowModel(baseWorkflow());

  assert.throws(() => model.requirementsOf('ghost'), (error) => {
    assert.ok(error instanceof GemArcError);
    assert.equal(error.code, 'GEMARC_WORKFLOW_001');
    return true;
  });
});

test('WorkflowModel normalises the input without mutating it', () => {
  const workflow = baseWorkflow();
  const snapshot = JSON.stringify(workflow);

  const model = new WorkflowModel(workflow);

  assert.equal(JSON.stringify(workflow), snapshot);
  assert.equal(model.id, 'wf-order');
  assert.equal(model.name, '订单处理');
  assert.equal(model.kind, 'process');
  assert.deepEqual(model.startNodes().map((node) => node.id), ['start']);
  assert.deepEqual(model.endNodes().map((node) => node.id), ['end']);
  assert.deepEqual([...model.reachableNodeIds()].sort(), ['charge', 'compensate', 'decide', 'end', 'start']);
  assert.deepEqual(model.duplicateNodeIds, []);
});
