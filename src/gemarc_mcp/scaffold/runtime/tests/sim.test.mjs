/**
 * Simulation engine tests.
 *
 * The engine is the browser twin of `gemarc_mcp.simulation`, so the cases
 * below assert the exact shapes the Python side emits: trace ids, times,
 * triggered-by chaining, truncation reasons, error codes and key order.
 *
 * Run with: node --test runtime/tests/sim.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_EVENTS,
  MAX_TIME_MS,
  COMPARISON_NOTICE,
  CausalEngine,
  EventEngine,
  compare,
  simulate,
  validateSimulationModel,
} from '../sim/index.js';

/**
 * A valid raw model: `meta.system` is set, every node has an id and a name,
 * every edge endpoint exists, and the scenario is well formed. The Python
 * validator accepts it, so the Python simulator accepts it too.
 */
function linearModel() {
  return {
    meta: {
      system: 'gemarc-test',
      schemaVersion: '0.1.0',
      version: '0.1.0',
      language: 'zh-CN',
      evidenceState: 'illustrative',
    },
    nodes: [
      { id: 'sensor', name: '事件源', kind: 'adapter', evidence: '示例' },
      { id: 'queue', name: '消息队列', kind: 'service', evidence: '示例' },
      { id: 'rule', name: '规则引擎', kind: 'service', evidence: '示例' },
      { id: 'notify', name: '通知服务', kind: 'service', evidence: '示例' },
    ],
    edges: [
      { id: 'e1', source: 'sensor', target: 'queue', evidence: '示例' },
      { id: 'e2', source: 'queue', target: 'rule', evidence: '示例' },
      { id: 'e3', source: 'rule', target: 'notify', evidence: '示例' },
    ],
    views: [
      {
        id: 'logical',
        title: '逻辑架构',
        concerns: ['事件依赖'],
        nodeIds: ['sensor', 'queue', 'rule', 'notify'],
      },
    ],
    scenarios: [
      {
        id: 'normal',
        name: '示例：正常事件传播',
        evidenceClass: 'hypothetical',
        events: [{ module: 'sensor', event: 'emit', timeMs: 0 }],
        rules: [
          { source: 'sensor', whenEvent: 'emit', target: 'queue', emit: 'ingest', delayMs: 10 },
          { source: 'queue', whenEvent: 'ingest', target: 'rule', emit: 'evaluate', delayMs: 20 },
          { source: 'rule', whenEvent: 'evaluate', target: 'notify', emit: 'send', delayMs: 10 },
        ],
      },
    ],
  };
}

/** Two events share one `timeMs`, so only the insertion counter can order them. */
function tieModel() {
  return {
    meta: { system: 'gemarc-test', evidenceState: 'illustrative' },
    nodes: [
      { id: 'a', name: 'A', evidence: '示例' },
      { id: 'b', name: 'B', evidence: '示例' },
      { id: 'c', name: 'C', evidence: '示例' },
    ],
    edges: [],
    views: [],
    scenarios: [
      {
        id: 'tie',
        events: [
          { module: 'a', event: 'x', timeMs: 100 },
          { module: 'b', event: 'y', timeMs: 100 },
        ],
        rules: [{ source: 'a', whenEvent: 'x', target: 'c', emit: 'z', delayMs: 0 }],
      },
    ],
  };
}

/** A self feeding rule: the timeline never ends on its own. */
function runawayModel() {
  return {
    meta: { system: 'gemarc-test', evidenceState: 'illustrative' },
    nodes: [{ id: 'loop', name: '循环', evidence: '示例' }],
    edges: [],
    views: [],
    scenarios: [
      {
        id: 'runaway',
        events: [{ module: 'loop', event: 'tick', timeMs: 0 }],
        rules: [{ source: 'loop', whenEvent: 'tick', target: 'loop', emit: 'tick', delayMs: 1 }],
      },
    ],
  };
}

/** A rule that pushes one event beyond the horizon. */
function horizonModel() {
  return {
    meta: { system: 'gemarc-test', evidenceState: 'illustrative' },
    nodes: [
      { id: 'a', name: 'A', evidence: '示例' },
      { id: 'b', name: 'B', evidence: '示例' },
    ],
    edges: [{ id: 'e1', source: 'a', target: 'b', evidence: '示例' }],
    views: [],
    scenarios: [
      {
        id: 'horizon',
        events: [{ module: 'a', event: 'start', timeMs: MAX_TIME_MS }],
        rules: [{ source: 'a', whenEvent: 'start', target: 'b', emit: 'late', delayMs: 1 }],
      },
    ],
  };
}

test('linear scenario delivers four events with the expected trace', () => {
  const result = simulate(linearModel(), 'normal');

  assert.equal(result.status, 'ok');
  assert.equal(result.scenarioId, 'normal');
  assert.equal(result.evidenceClass, 'hypothetical');
  assert.deepEqual(result.disabledNodes, []);
  assert.equal(result.truncation, null);
  assert.deepEqual(result.summary, { delivered: 4, blocked: 0 });
  assert.equal(result.trace.length, 4);

  assert.deepEqual(
    result.trace.map((entry) => [entry.traceId, entry.module, entry.event, entry.timeMs, entry.triggeredBy, entry.status]),
    [
      ['trace-0000', 'sensor', 'emit', 0, null, 'delivered'],
      ['trace-0001', 'queue', 'ingest', 10, 'trace-0000', 'delivered'],
      ['trace-0002', 'rule', 'evaluate', 30, 'trace-0001', 'delivered'],
      ['trace-0003', 'notify', 'send', 40, 'trace-0002', 'delivered'],
    ],
  );
});

test('the emitted object keeps the key order of the Python dict', () => {
  const result = simulate(linearModel(), 'normal');
  assert.deepEqual(Object.keys(result), [
    'status',
    'scenarioId',
    'evidenceClass',
    'disabledNodes',
    'trace',
    'truncation',
    'summary',
  ]);
  assert.deepEqual(Object.keys(result.summary), ['delivered', 'blocked']);
  assert.deepEqual(Object.keys(result.trace[0]), [
    'module',
    'event',
    'timeMs',
    'triggeredBy',
    'status',
    'traceId',
  ]);
});

test('disabling a module blocks the downstream events', () => {
  const result = simulate(linearModel(), 'normal', ['queue']);

  assert.deepEqual(result.disabledNodes, ['queue']);
  assert.deepEqual(result.summary, { delivered: 1, blocked: 1 });
  assert.equal(result.trace.length, 2);
  assert.equal(result.trace[0].status, 'delivered');
  assert.equal(result.trace[1].status, 'blocked');
  assert.equal(result.trace[1].module, 'queue');
  // A blocked event is recorded but fires no rule, so nothing follows it.
  assert.equal(result.trace[1].triggeredBy, 'trace-0000');
});

test('compare reports the lost events of an intervention', () => {
  const report = compare(linearModel(), 'normal', ['queue']);

  assert.equal(report.status, 'ok');
  assert.equal(report.scenarioId, 'normal');
  assert.deepEqual(report.lostEvents, [
    { module: 'notify', event: 'send' },
    { module: 'queue', event: 'ingest' },
    { module: 'rule', event: 'evaluate' },
  ]);
  assert.deepEqual(report.gainedEvents, []);
  assert.equal(report.notice, COMPARISON_NOTICE);
  assert.equal(report.baseline.summary.delivered, 4);
  assert.equal(report.intervention.summary.delivered, 1);
});

test('compare with no disabled module loses nothing', () => {
  const report = compare(linearModel(), 'normal', []);

  assert.deepEqual(report.lostEvents, []);
  assert.deepEqual(report.gainedEvents, []);
  assert.deepEqual(report.baseline.trace, report.intervention.trace);
});

test('events with the same timeMs are processed in insertion order', () => {
  const result = simulate(tieModel(), 'tie');

  assert.deepEqual(
    result.trace.map((entry) => [entry.traceId, entry.module, entry.event, entry.timeMs]),
    [
      ['trace-0000', 'a', 'x', 100],
      ['trace-0001', 'b', 'y', 100],
      ['trace-0002', 'c', 'z', 100],
    ],
  );
});

test('the trace is truncated at MAX_EVENTS', () => {
  const result = simulate(runawayModel(), 'runaway');

  assert.equal(result.status, 'truncated');
  assert.equal(result.truncation, 'MAX_EVENTS');
  assert.equal(result.trace.length, MAX_EVENTS);
  assert.deepEqual(result.summary, { delivered: MAX_EVENTS, blocked: 0 });
  assert.equal(result.trace[MAX_EVENTS - 1].traceId, 'trace-0511');
});

test('the timeline is truncated at MAX_TIME_MS', () => {
  const result = simulate(horizonModel(), 'horizon');

  assert.equal(result.status, 'truncated');
  assert.equal(result.truncation, 'MAX_TIME_MS');
  // The event that crossed the horizon is never written to the trace.
  assert.equal(result.trace.length, 1);
  assert.equal(result.trace[0].timeMs, MAX_TIME_MS);
  assert.deepEqual(result.summary, { delivered: 1, blocked: 0 });
});

test('an unknown scenario id is rejected', () => {
  assert.throws(() => simulate(linearModel(), 'missing'), (error) => {
    assert.equal(error.name, 'GemArcError');
    assert.equal(error.code, 'GEMARC_DYNAMIC_006');
    assert.equal(error.safeMessage, '场景不存在');
    return true;
  });
});

test('an unknown disabled node id is rejected', () => {
  assert.throws(() => simulate(linearModel(), 'normal', ['ghost']), (error) => {
    assert.equal(error.name, 'GemArcError');
    assert.equal(error.code, 'GEMARC_DYNAMIC_007');
    assert.equal(error.safeMessage, '屏蔽的模块不存在');
    return true;
  });
});

test('an invalid model is rejected before anything is simulated', () => {
  assert.throws(() => simulate({ nodes: [] }, 'normal'), (error) => {
    assert.equal(error.code, 'GEMARC_MODEL_020');
    return true;
  });
  assert.equal(validateSimulationModel(linearModel()).status, 'ok');
  assert.equal(validateSimulationModel({ nodes: [] }).status, 'invalid');
});

test('the engine classes agree with the standalone functions', () => {
  const model = linearModel();
  const engine = new EventEngine(model);
  const causal = new CausalEngine(model);

  assert.deepEqual(engine.simulate('normal'), simulate(model, 'normal'));
  assert.deepEqual(engine.simulate('normal', ['queue']), simulate(model, 'normal', ['queue']));
  assert.deepEqual(causal.compare('normal', ['queue']), compare(model, 'normal', ['queue']));
});

test('two runs of the same model produce byte identical output', () => {
  const model = linearModel();
  const first = JSON.stringify(simulate(model, 'normal'));
  const second = JSON.stringify(simulate(model, 'normal'));
  assert.equal(first, second);
});
