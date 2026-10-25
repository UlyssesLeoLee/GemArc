/**
 * 4D timeline simulator - browser mirror of `gemarc_mcp.simulation`.
 *
 * This file is the JavaScript authority for the bounded, deterministic
 * rule-driven timeline. It must stay *structurally identical* to the Python
 * implementation in `src/gemarc_mcp/simulation.py`, because the golden cases
 * (`src/gemarc_mcp/scaffold/cases/simulation-golden.json`) are compared event
 * by event between the two ends. Every deviation below is a bug, not a style
 * choice:
 *
 *   - the priority queue is keyed by the tuple `(timeMs, insertionCounter)`,
 *     so two events with the same `timeMs` are processed in insertion order
 *     (Python uses `heapq` + `itertools.count()`);
 *   - the `MAX_EVENTS` guard is evaluated at the TOP of the loop, before the
 *     pop, exactly like Python;
 *   - the `MAX_TIME_MS` guard is evaluated AFTER the pop, so an event that
 *     crosses the horizon is never written to the trace;
 *   - a blocked event is appended to the trace but does NOT fire rules;
 *   - key insertion order of every emitted object matches the Python dict
 *     order, so `JSON.stringify` on both sides produces the same text.
 *
 * The model handed to this module is the RAW model, not the normalised one.
 * Python reads `event["module"] / event["event"] / event["timeMs"]` and
 * `rule["source"] / rule["whenEvent"] / rule["target"] / rule["emit"]`
 * directly, so this module reads the raw shape as well.
 */
import { GemArcError, diagnostic } from '../core/errors.js';
import { normalizeModel } from '../core/model.js';
import {
  CANONICAL_EVIDENCE_LEVELS,
  CASE_CATEGORY_IDS,
  DIAGRAM_TYPE_IDS,
  EDGE_TYPE_IDS,
  ENTITY_KIND_IDS,
  WORKFLOW_EDGE_KIND_IDS,
  WORKFLOW_NODE_KIND_IDS,
  canonicalizeEvidenceLevel,
  diagramTypeSpec,
} from '../core/vocab.js';

/** Hard ceiling on the number of events written to one trace. */
export const MAX_EVENTS = 512;

/** Hard ceiling on the simulated horizon, in milliseconds. */
export const MAX_TIME_MS = 3600000;

/**
 * `scenario.evidenceClass` values accepted by the Python validator. These are
 * the scenario level classes, not the evidence vocabulary of the nodes.
 */
const SCENARIO_EVIDENCE_CLASSES = new Set(['observed', 'model_derived', 'hypothetical']);

/**
 * Python's `ID_RE` (`^[A-Za-z0-9][\w.:-]{0,127}$`, `re.UNICODE`) where `\w`
 * is Unicode aware: it matches every character for which `str.isalnum()` is
 * true plus the underscore. The plain JS `\w` is ASCII only, so the Unicode
 * aware spelling is used here to keep both validators in step.
 */
const ID_PATTERN = /^[A-Za-z0-9][\p{L}\p{N}_.:-]{0,127}$/u;

/** Python's `str()` for the values that can reach a diagnostic entity id. */
function pythonStr(value) {
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return String(value);
}

/** Python `isinstance(x, dict)`. */
function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Compare two strings by Unicode code point, which is what Python's `sorted`
 * does. `Array.prototype.sort` compares UTF-16 code units, which orders
 * astral characters differently, so the comparison is done explicitly.
 */
export function compareByCodePoint(a, b) {
  if (a === b) return 0;
  const left = Array.from(a);
  const right = Array.from(b);
  const shared = Math.min(left.length, right.length);
  for (let i = 0; i < shared; i += 1) {
    const x = left[i].codePointAt(0);
    const y = right[i].codePointAt(0);
    if (x !== y) return x < y ? -1 : 1;
  }
  return left.length - right.length;
}

/**
 * The canonical copy of a model, exactly as `gemarc_mcp.model.normalized_model`
 * produces it. The runtime normaliser (`core/model.js`) is the shared
 * implementation and already rewrites the 0.1.x spellings (`source`/`target`
 * into `sourceId`/`targetId`, `kind` into `type`) and canonicalises the
 * evidence levels.
 *
 * One difference is corrected here: Python keeps a `None` evidence state as
 * `None` (and then rejects it), while the runtime normaliser turns it into
 * `Unknown`. The raw value is therefore read back from the caller's model.
 *
 * @param {unknown} model raw model
 */
function canonicalModel(model) {
  const canonical = normalizeModel(isPlainObject(model) ? model : {});
  const rawMeta = isPlainObject(model) ? model.meta : undefined;
  const rawState = isPlainObject(rawMeta)
    ? (rawMeta.evidenceState === undefined ? 'Unknown' : rawMeta.evidenceState)
    : 'Unknown';
  canonical.meta.evidenceState = canonicalizeEvidenceLevel(rawState);
  return canonical;
}

/**
 * Mirror of `gemarc_mcp.model.validate(model, strict=False)`.
 *
 * The simulator refuses to run on an invalid model (`GEMARC_MODEL_020`), so
 * this mirror has to accept and reject exactly the same models as the Python
 * side. It reads the same vocabulary through `core/vocab.js`, which is
 * generated from the same `scaffold/vocab/*.json` files the Python validator
 * reads, so neither end can drift from the vocabulary.
 *
 * @param {unknown} model raw model
 * @returns {{status: string, errors: object[], warnings: object[], summary: object}}
 */
export function validateSimulationModel(model) {
  const issues = [];
  const err = (code, message, entity = '') => issues.push(diagnostic(code, message, entity, 'ERROR'));
  const warn = (code, message, entity = '') => issues.push(diagnostic(code, message, entity, 'WARNING'));

  if (!isPlainObject(model)) {
    err('GEMARC_MODEL_001', '模型必须是对象');
    return result(issues);
  }

  const canonical = canonicalModel(model);
  const meta = canonical.meta;
  if (typeof meta.system !== 'string' || !meta.system) {
    err('GEMARC_MODEL_003', 'meta.system 为必填字符串');
  }
  const evidenceState = meta.evidenceState === undefined ? 'Unknown' : meta.evidenceState;
  if (!CANONICAL_EVIDENCE_LEVELS.includes(evidenceState)) {
    err('GEMARC_MODEL_004', '无效的 meta.evidenceState');
  }

  const evidenceIds = new Set();
  for (const record of canonical.evidence) {
    const ident = record.id;
    if (typeof ident !== 'string' || !ID_PATTERN.test(ident)) {
      err('GEMARC_MODEL_030', '证据 ID 无效', pythonStr(ident));
      continue;
    }
    if (evidenceIds.has(ident)) err('GEMARC_MODEL_031', '证据 ID 重复', ident);
    evidenceIds.add(ident);
    if (!CANONICAL_EVIDENCE_LEVELS.includes(record.level)) {
      err('GEMARC_MODEL_021', '未登记的证据等级', ident);
    }
    const source = record.source || {};
    if (!isPlainObject(source) || !source.locator) {
      warn('GEMARC_EVIDENCE_006', '证据缺少可定位的来源', ident);
    }
  }

  const nodeIds = new Set();
  for (const node of canonical.nodes) {
    const ident = node.id;
    if (typeof ident !== 'string' || !ID_PATTERN.test(ident)) {
      err('GEMARC_MODEL_007', '节点 ID 无效', pythonStr(ident));
      continue;
    }
    if (nodeIds.has(ident)) err('GEMARC_MODEL_008', '节点 ID 重复', ident);
    nodeIds.add(ident);
    if (typeof node.name !== 'string' || !node.name) {
      err('GEMARC_MODEL_009', '节点缺少名称', ident);
    }
    if (!ENTITY_KIND_IDS.includes(node.kind)) {
      warn('GEMARC_MODEL_022', '未登记的实体类型', ident);
    }
    checkEvidenceRefs(node, evidenceIds, err);
    if (!node.evidence) {
      warn('GEMARC_EVIDENCE_003', '节点无证据，必须在页面标注', ident);
    }
  }

  const edgeIds = new Set();
  for (const edge of canonical.edges) {
    const eid = edge.id;
    if (typeof eid !== 'string' || !ID_PATTERN.test(eid) || edgeIds.has(eid)) {
      err('GEMARC_MODEL_011', '边 ID 无效或重复', pythonStr(eid));
    } else {
      edgeIds.add(eid);
    }
    if (!EDGE_TYPE_IDS.includes(edge.type)) {
      warn('GEMARC_MODEL_023', '未登记的关系类型', pythonStr(eid));
    }
    for (const key of ['sourceId', 'targetId']) {
      if (typeof edge[key] !== 'string' || !nodeIds.has(edge[key])) {
        err('GEMARC_MODEL_012', `${key} 节点不存在`, pythonStr(eid));
      }
    }
    checkEvidenceRefs(edge, evidenceIds, err);
    if (!edge.evidence) {
      warn('GEMARC_EVIDENCE_005', '关系无证据', pythonStr(eid));
    }
  }

  const viewIds = new Set();
  for (const view of canonical.views) {
    if (!isPlainObject(view) || typeof view.id !== 'string') {
      err('GEMARC_VIEW_001', '视图必须拥有 ID');
      continue;
    }
    const vid = view.id;
    if (viewIds.has(vid)) err('GEMARC_VIEW_002', '重复 View ID', vid);
    viewIds.add(vid);
    validateView(view, nodeIds, err, warn);
  }

  validateSpace(canonical.space, err);
  validateCases(canonical.cases, canonical.scenarios, nodeIds, err);
  validateWorkflows(canonical.workflows, nodeIds, err);
  validateScenarios(canonical.scenarios, nodeIds, err);

  return result(issues);
}

function checkEvidenceRefs(entity, evidenceIds, err) {
  const ident = entity.id === undefined ? '' : entity.id;
  for (const ref of Array.isArray(entity.evidenceIds) ? entity.evidenceIds : []) {
    if (typeof ref !== 'string') {
      err('GEMARC_EVIDENCE_007', '证据引用必须是字符串', pythonStr(ident));
    } else if (!evidenceIds.has(ref)) {
      err('GEMARC_EVIDENCE_008', '证据引用不存在', pythonStr(ident));
    }
  }
}

function validateView(view, nodeIds, err, warn) {
  const vid = view.id === undefined ? '' : view.id;
  const diagramType = view.diagramType;
  const spec = diagramTypeSpec(diagramType);
  if (!spec) {
    if (diagramType === undefined || diagramType === null) {
      // 0.1.x views did not carry a diagram type. Not an error yet.
      warn('GEMARC_VIEW_006', '视图未声明 diagramType', pythonStr(vid));
    } else {
      err('GEMARC_VIEW_006', '未登记的 diagramType', pythonStr(vid));
    }
  }
  let viewNodes = view.nodeIds === undefined ? [] : view.nodeIds;
  if (!Array.isArray(viewNodes)) {
    err('GEMARC_VIEW_005', '视图 nodeIds 必须是数组', pythonStr(vid));
    viewNodes = [];
  }
  for (const nodeId of viewNodes) {
    if (typeof nodeId !== 'string' || !nodeIds.has(nodeId)) {
      err('GEMARC_VIEW_003', '视图引用不存在节点', pythonStr(vid));
    }
  }
  if (!view.concerns) {
    warn('GEMARC_VIEW_004', '缺少架构关注点', pythonStr(vid));
  }
  if (!spec) return;
  const allowedEdges = new Set(spec.edgeKinds || []);
  for (const edgeKind of Array.isArray(view.edgeKinds) ? view.edgeKinds : []) {
    if (!allowedEdges.has(edgeKind) && !EDGE_TYPE_IDS.includes(edgeKind)) {
      err('GEMARC_VIEW_008', '视图引用了未登记的关系类型', pythonStr(vid));
    }
  }
}

function validateSpace(space, err) {
  if (!space) return;
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const buildings = new Set(
    asArray(space.buildings).filter(isPlainObject).map((item) => item.id),
  );
  const floors = new Set(
    asArray(space.floors).filter(isPlainObject).map((item) => item.id),
  );
  const rooms = new Set(
    asArray(space.rooms).filter(isPlainObject).map((item) => item.id),
  );
  for (const item of asArray(space.buildings)) {
    if (!isPlainObject(item)) {
      err('GEMARC_SPACE_003', '楼宇必须是对象');
      continue;
    }
    if (!item.semanticSource) {
      err('GEMARC_SPACE_002', '空间容器必须保存语义来源', pythonStr(item.id === undefined ? '' : item.id));
    }
  }
  for (const item of asArray(space.floors)) {
    if (!isPlainObject(item)) {
      err('GEMARC_SPACE_003', '楼层必须是对象');
      continue;
    }
    if (!buildings.has(item.buildingId)) {
      err('GEMARC_SPACE_001', '楼层引用的楼宇不存在', pythonStr(item.id === undefined ? '' : item.id));
    }
    if (!item.semanticSource) {
      err('GEMARC_SPACE_002', '空间容器必须保存语义来源', pythonStr(item.id === undefined ? '' : item.id));
    }
  }
  for (const item of asArray(space.rooms)) {
    if (!isPlainObject(item)) {
      err('GEMARC_SPACE_003', '房间必须是对象');
      continue;
    }
    if (!floors.has(item.floorId)) {
      err('GEMARC_SPACE_001', '房间引用的楼层不存在', pythonStr(item.id === undefined ? '' : item.id));
    }
    if (!item.semanticSource) {
      err('GEMARC_SPACE_002', '空间容器必须保存语义来源', pythonStr(item.id === undefined ? '' : item.id));
    }
  }
}

function validateCases(cases, scenarios, nodeIds, err) {
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const scenarioIds = new Set(
    asArray(scenarios).filter(isPlainObject).map((item) => item.id),
  );
  const seen = new Set();
  for (const item of asArray(cases)) {
    if (!isPlainObject(item)) {
      err('GEMARC_CASE_004', 'Case 必须是对象');
      continue;
    }
    const cid = item.id;
    if (typeof cid !== 'string' || !ID_PATTERN.test(cid) || seen.has(cid)) {
      err('GEMARC_CASE_005', 'Case ID 无效或重复', pythonStr(cid));
    } else {
      seen.add(cid);
    }
    if (!scenarioIds.has(item.scenarioId)) {
      err('GEMARC_CASE_001', 'Case 引用的场景不存在', pythonStr(cid));
    }
    if (!CASE_CATEGORY_IDS.includes(item.category)) {
      err('GEMARC_CASE_002', '无效的 Case 分类', pythonStr(cid));
    }
    for (const nodeId of asArray(item.disabledNodeIds)) {
      if (!nodeIds.has(nodeId)) {
        err('GEMARC_CASE_003', 'Case 屏蔽的节点不存在', pythonStr(cid));
      }
    }
  }
}

function validateWorkflows(workflows, nodeIds, err) {
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const seen = new Set();
  for (const workflow of asArray(workflows)) {
    if (!isPlainObject(workflow)) {
      err('GEMARC_WORKFLOW_004', '工作流必须是对象');
      continue;
    }
    const wid = workflow.id;
    if (typeof wid !== 'string' || !ID_PATTERN.test(wid) || seen.has(wid)) {
      err('GEMARC_WORKFLOW_005', '工作流 ID 无效或重复', pythonStr(wid));
    } else {
      seen.add(wid);
    }
    if (workflow.kind && !WORKFLOW_NODE_KIND_IDS.includes(workflow.kind)) {
      err('GEMARC_WORKFLOW_006', '未登记的工作流类型', pythonStr(wid));
    }
    const localIds = new Set(
      asArray(workflow.nodes).filter(isPlainObject).map((item) => item.id),
    );
    for (const node of asArray(workflow.nodes)) {
      if (!isPlainObject(node)) {
        err('GEMARC_WORKFLOW_007', '工作流节点必须是对象', pythonStr(wid));
        continue;
      }
      if (!WORKFLOW_NODE_KIND_IDS.includes(node.kind)) {
        err('GEMARC_WORKFLOW_008', '未登记的工作流节点类型', pythonStr(node.id === undefined ? '' : node.id));
      }
      const bound = node.canonicalNodeId;
      if (bound !== undefined && bound !== null && !nodeIds.has(bound)) {
        err('GEMARC_WORKFLOW_001', '工作流节点绑定的架构节点不存在', pythonStr(node.id === undefined ? '' : node.id));
      }
    }
    for (const edge of asArray(workflow.edges)) {
      if (!isPlainObject(edge)) {
        err('GEMARC_WORKFLOW_009', '工作流边必须是对象', pythonStr(wid));
        continue;
      }
      if (!WORKFLOW_EDGE_KIND_IDS.includes(edge.kind)) {
        err('GEMARC_WORKFLOW_010', '未登记的工作流边类型', pythonStr(edge.id === undefined ? '' : edge.id));
      }
      for (const key of ['sourceId', 'targetId']) {
        if (!localIds.has(edge[key])) {
          err('GEMARC_WORKFLOW_002', '工作流边引用不存在的节点', pythonStr(edge.id === undefined ? '' : edge.id));
        }
      }
      if (edge.kind === 'conditional' && !edge.condition) {
        err('GEMARC_WORKFLOW_003', '条件流必须声明可判断的条件', pythonStr(edge.id === undefined ? '' : edge.id));
      }
    }
  }
}

function validateScenarios(scenarios, nodeIds, err) {
  const asArray = (value) => (Array.isArray(value) ? value : []);
  const scenarioIds = new Set();
  for (const scenario of asArray(scenarios)) {
    if (!isPlainObject(scenario)) {
      err('GEMARC_DYNAMIC_002', '场景必须是对象');
      continue;
    }
    const sid = scenario.id;
    if (typeof sid !== 'string' || !ID_PATTERN.test(sid) || scenarioIds.has(sid)) {
      err('GEMARC_DYNAMIC_003', '场景 ID 无效或重复', pythonStr(sid));
    } else {
      scenarioIds.add(sid);
    }
    const events = scenario.events === undefined ? [] : scenario.events;
    const rules = scenario.rules === undefined ? [] : scenario.rules;
    if (!Array.isArray(events) || !Array.isArray(rules)) {
      err('GEMARC_DYNAMIC_008', '场景 events/rules 必须为数组', pythonStr(sid));
      continue;
    }
    for (const evt of events) {
      if (!isPlainObject(evt)) {
        err('GEMARC_DYNAMIC_004', '事件必须是对象', pythonStr(sid));
        continue;
      }
      if (typeof evt.module !== 'string' || !nodeIds.has(evt.module)) {
        err('GEMARC_DYNAMIC_004', '事件引用不存在的模块', pythonStr(sid));
      }
      const timeMs = evt.timeMs;
      if (
        typeof evt.event !== 'string'
        || !evt.event
        || typeof timeMs !== 'number'
        || !Number.isFinite(timeMs)
        || timeMs < 0
        || timeMs > MAX_TIME_MS
      ) {
        err('GEMARC_DYNAMIC_005', '事件必须具有 event 和 0～3600000 的 timeMs', pythonStr(sid));
      }
    }
    for (const rule of rules) {
      if (!isPlainObject(rule)) {
        err('GEMARC_CAUSAL_001', '规则必须是对象', pythonStr(sid));
        continue;
      }
      if (
        typeof rule.source !== 'string'
        || !nodeIds.has(rule.source)
        || typeof rule.target !== 'string'
        || !nodeIds.has(rule.target)
      ) {
        err('GEMARC_CAUSAL_001', '因果规则的模块端点不存在', pythonStr(sid));
      }
      if (typeof rule.whenEvent !== 'string' || typeof rule.emit !== 'string') {
        err('GEMARC_CAUSAL_002', '因果规则缺少 whenEvent 或 emit', pythonStr(sid));
      }
      const delay = rule.delayMs === undefined ? 0 : rule.delayMs;
      if (typeof delay !== 'number' || !Number.isFinite(delay) || delay < 0 || delay > MAX_TIME_MS) {
        err('GEMARC_CAUSAL_004', '因果规则 delayMs 必须在 0～3600000', pythonStr(sid));
      }
    }
    const evidenceClass = scenario.evidenceClass === undefined ? 'hypothetical' : scenario.evidenceClass;
    if (!SCENARIO_EVIDENCE_CLASSES.has(evidenceClass)) {
      err('GEMARC_CAUSAL_005', '无效的证据等级', pythonStr(sid));
    }
  }
}

function result(issues) {
  const errors = issues.filter((item) => item.severity === 'ERROR');
  const warnings = issues.filter((item) => item.severity === 'WARNING');
  return {
    status: errors.length > 0 ? 'invalid' : 'ok',
    errors,
    warnings,
    summary: { errors: errors.length, warnings: warnings.length },
  };
}

/**
 * Binary min heap over the `(timeMs, counter)` key. The counter makes the key
 * a strict total order, so the pop sequence is fully determined and does not
 * depend on the heap implementation.
 */
class EventQueue {
  constructor() {
    /** @type {{timeMs: number, counter: number, payload: object}[]} */
    this.items = [];
  }

  get size() {
    return this.items.length;
  }

  push(timeMs, counter, payload) {
    const items = this.items;
    items.push({ timeMs, counter, payload });
    let index = items.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (!before(items[index], items[parent])) break;
      const swap = items[index];
      items[index] = items[parent];
      items[parent] = swap;
      index = parent;
    }
  }

  pop() {
    const items = this.items;
    if (items.length === 0) return null;
    const top = items[0];
    const last = items.pop();
    if (items.length > 0) {
      items[0] = last;
      let index = 0;
      for (;;) {
        const left = index * 2 + 1;
        const right = left + 1;
        let smallest = index;
        if (left < items.length && before(items[left], items[smallest])) smallest = left;
        if (right < items.length && before(items[right], items[smallest])) smallest = right;
        if (smallest === index) break;
        const swap = items[index];
        items[index] = items[smallest];
        items[smallest] = swap;
        index = smallest;
      }
    }
    return top;
  }
}

function before(a, b) {
  if (a.timeMs < b.timeMs) return true;
  if (a.timeMs > b.timeMs) return false;
  return a.counter < b.counter;
}

/**
 * Python `set(disabled_nodes or [])`. A string is treated as an iterable of
 * characters, exactly like `set("abc")` does in Python.
 */
function toDisabledSet(value) {
  if (value === undefined || value === null) return new Set();
  if (typeof value === 'string') return new Set(value);
  if (Array.isArray(value)) return new Set(value);
  if (value instanceof Set) return new Set(value);
  if (typeof value[Symbol.iterator] === 'function') return new Set(value);
  return new Set();
}

/**
 * Accept either a raw model or an `indexModel()` result, so the engine can be
 * wired onto a store backed view later without changing the model API.
 */
function resolveModel(candidate) {
  if (isPlainObject(candidate) && isPlainObject(candidate.model) && candidate.nodes instanceof Map) {
    return candidate.model;
  }
  return candidate;
}

/**
 * The 4D timeline engine.
 *
 * ```js
 * const engine = new EventEngine(model);
 * engine.simulate('normal', ['queue']);
 * ```
 */
export class EventEngine {
  /**
   * @param {object} model raw model (or an `indexModel()` result)
   * @param {{validate?: boolean, maxEvents?: number, maxTimeMs?: number}} [options]
   */
  constructor(model, options = {}) {
    this.model = model;
    this.options = {
      validate: options.validate !== false,
      maxEvents: typeof options.maxEvents === 'number' ? options.maxEvents : MAX_EVENTS,
      maxTimeMs: typeof options.maxTimeMs === 'number' ? options.maxTimeMs : MAX_TIME_MS,
    };
  }

  /**
   * Run one scenario.
   *
   * @param {string} scenarioId
   * @param {string[]|Set<string>} [disabledNodes]
   * @returns {object} the simulation report
   */
  simulate(scenarioId, disabledNodes) {
    const model = resolveModel(this.model);
    const { validate, maxEvents, maxTimeMs } = this.options;

    if (validate) {
      const check = validateSimulationModel(model);
      if (check.status !== 'ok') {
        throw new GemArcError('GEMARC_MODEL_020', '模型校验失败，不可模拟');
      }
    }

    const scenarios = new Map();
    for (const scenario of Array.isArray(model?.scenarios) ? model.scenarios : []) {
      if (isPlainObject(scenario)) scenarios.set(scenario.id, scenario);
    }
    if (!scenarios.has(scenarioId)) {
      throw new GemArcError('GEMARC_DYNAMIC_006', '场景不存在');
    }
    const scenario = scenarios.get(scenarioId);

    const nodeIds = new Set();
    for (const node of Array.isArray(model?.nodes) ? model.nodes : []) {
      if (isPlainObject(node)) nodeIds.add(node.id);
    }
    const disabled = toDisabledSet(disabledNodes);
    for (const id of disabled) {
      if (!nodeIds.has(id)) {
        throw new GemArcError('GEMARC_DYNAMIC_007', '屏蔽的模块不存在');
      }
    }

    const rules = Array.isArray(scenario.rules) ? scenario.rules : [];
    const queue = new EventQueue();
    let counter = 0;
    for (const event of Array.isArray(scenario.events) ? scenario.events : []) {
      if (!isPlainObject(event)) continue;
      queue.push(event.timeMs, counter, {
        module: event.module,
        event: event.event,
        timeMs: event.timeMs,
        triggeredBy: null,
      });
      counter += 1;
    }

    const trace = [];
    let truncation = null;
    while (queue.size > 0) {
      // The MAX_EVENTS guard sits at the top of the loop, before the pop.
      if (trace.length >= maxEvents) {
        truncation = 'MAX_EVENTS';
        break;
      }
      const popped = queue.pop();
      const tick = popped.timeMs;
      const event = popped.payload;
      // The horizon guard sits after the pop: an event beyond the horizon is
      // never written to the trace.
      if (tick > maxTimeMs) {
        truncation = 'MAX_TIME_MS';
        break;
      }
      event.status = disabled.has(event.module) ? 'blocked' : 'delivered';
      event.traceId = `trace-${String(trace.length).padStart(4, '0')}`;
      trace.push(event);
      // A blocked event is recorded but fires no rule.
      if (event.status === 'blocked') continue;
      for (const rule of rules) {
        if (!isPlainObject(rule)) continue;
        if (rule.source !== event.module || rule.whenEvent !== event.event) continue;
        const delay = typeof rule.delayMs === 'number' && Number.isFinite(rule.delayMs) ? rule.delayMs : 0;
        const newTime = tick + delay;
        queue.push(newTime, counter, {
          module: rule.target,
          event: rule.emit,
          timeMs: newTime,
          triggeredBy: event.traceId,
        });
        counter += 1;
      }
    }

    let delivered = 0;
    let blocked = 0;
    for (const entry of trace) {
      if (entry.status === 'delivered') delivered += 1;
      else blocked += 1;
    }

    const disabledNodesSorted = [...disabled].sort(compareByCodePoint);
    const evidenceClass = scenario.evidenceClass === undefined ? 'hypothetical' : scenario.evidenceClass;

    return {
      status: truncation === null ? 'ok' : 'truncated',
      scenarioId,
      evidenceClass,
      disabledNodes: disabledNodesSorted,
      trace,
      truncation,
      summary: { delivered, blocked },
    };
  }
}

/**
 * Standalone twin of the Python `simulation.simulate`.
 *
 * @param {object} model raw model
 * @param {string} scenarioId
 * @param {string[]|Set<string>} [disabledNodes]
 */
export function simulate(model, scenarioId, disabledNodes) {
  return new EventEngine(model).simulate(scenarioId, disabledNodes);
}
