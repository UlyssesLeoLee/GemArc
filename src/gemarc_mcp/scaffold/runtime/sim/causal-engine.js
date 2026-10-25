/**
 * 5D intervention comparison - browser mirror of `gemarc_mcp.simulation.compare`.
 *
 * The comparison is derived from the two real execution traces, never from a
 * visual guess: the baseline run has no module disabled, the counterfactual
 * run has the disabled modules applied, and the difference of the *delivered*
 * `(module, event)` pairs is reported.
 *
 * The emitted object mirrors the Python dict field for field and key for key:
 *
 *   {status, scenarioId, baseline, intervention, lostEvents, gainedEvents, notice}
 */
import { EventEngine, compareByCodePoint, simulate } from './event-engine.js';

/** The notice is part of the contract, word for word. */
export const COMPARISON_NOTICE = '仅代表指定模型规则的干预计算结果；不是现实因果识别证明';

/**
 * The set of delivered `(module, event)` pairs of one trace, de-duplicated and
 * ordered the way Python's `sorted({...})` orders the tuples: by module
 * first, then by event.
 */
function observedPairs(trace) {
  const seen = new Set();
  const pairs = [];
  for (const entry of Array.isArray(trace) ? trace : []) {
    if (!entry || entry.status !== 'delivered') continue;
    const key = JSON.stringify([entry.module, entry.event]);
    if (seen.has(key)) continue;
    seen.add(key);
    pairs.push([entry.module, entry.event]);
  }
  return pairs;
}

function comparePairs(left, right) {
  if (left[0] !== right[0]) return compareByCodePoint(left[0], right[0]);
  return compareByCodePoint(left[1], right[1]);
}

/**
 * The 5D intervention engine.
 *
 * ```js
 * const engine = new CausalEngine(model);
 * engine.compare('normal', ['queue']);
 * ```
 */
export class CausalEngine {
  /**
   * @param {object} model raw model (or an `indexModel()` result)
   * @param {{validate?: boolean, maxEvents?: number, maxTimeMs?: number}} [options]
   */
  constructor(model, options = {}) {
    this.model = model;
    this.options = { ...options };
    this.events = new EventEngine(model, options);
  }

  /**
   * Run one scenario without any module disabled.
   *
   * @param {string} scenarioId
   * @param {string[]|Set<string>} [disabledNodes]
   */
  simulate(scenarioId, disabledNodes) {
    return this.events.simulate(scenarioId, disabledNodes);
  }

  /**
   * Compare a clean run against an intervention run.
   *
   * @param {string} scenarioId
   * @param {string[]|Set<string>} [disabledNodes]
   * @returns {object} the comparison report
   */
  compare(scenarioId, disabledNodes) {
    const baseline = this.events.simulate(scenarioId);
    const intervention = this.events.simulate(scenarioId, disabledNodes);
    return buildComparison(scenarioId, baseline, intervention);
  }
}

/**
 * Build the comparison report from two traces.
 *
 * @param {string} scenarioId
 * @param {object} baseline simulation report without disabled modules
 * @param {object} intervention simulation report with disabled modules
 */
export function buildComparison(scenarioId, baseline, intervention) {
  const before = observedPairs(baseline.trace);
  const after = observedPairs(intervention.trace);
  const afterKeys = new Set(after.map(([module, event]) => JSON.stringify([module, event])));

  const lost = before
    .filter(([module, event]) => !afterKeys.has(JSON.stringify([module, event])))
    .sort(comparePairs);
  const beforeKeys = new Set(before.map(([module, event]) => JSON.stringify([module, event])));
  const gained = after
    .filter(([module, event]) => !beforeKeys.has(JSON.stringify([module, event])))
    .sort(comparePairs);

  return {
    status: 'ok',
    scenarioId,
    baseline,
    intervention,
    lostEvents: lost.map(([module, event]) => ({ module, event })),
    gainedEvents: gained.map(([module, event]) => ({ module, event })),
    notice: COMPARISON_NOTICE,
  };
}

/**
 * Standalone twin of the Python `simulation.compare`.
 *
 * @param {object} model raw model
 * @param {string} scenarioId
 * @param {string[]|Set<string>} [disabledNodes]
 */
export function compare(model, scenarioId, disabledNodes) {
  const baseline = simulate(model, scenarioId);
  const counterfactual = simulate(model, scenarioId, disabledNodes);
  return buildComparison(scenarioId, baseline, counterfactual);
}
