/**
 * Simulation runtime entry point.
 *
 * `simulate(model, scenarioId, disabledNodes)` and
 * `compare(model, scenarioId, disabledNodes)` are drop-in twins of the Python
 * functions of the same name in `gemarc_mcp.simulation`: same arguments, same
 * output object, same key order, same error codes. They are the primary API
 * and must stay that way; the `EventEngine` / `CausalEngine` classes are the
 * stateful wrappers used by the views.
 *
 * ```js
 * import { simulate, compare } from './sim/index.js';
 *
 * const report = simulate(model, 'normal', ['queue']);
 * const delta = compare(model, 'normal', ['queue']);
 * ```
 */
export {
  MAX_EVENTS,
  MAX_TIME_MS,
  compareByCodePoint,
  validateSimulationModel,
  EventEngine,
  simulate,
} from './event-engine.js';

export {
  COMPARISON_NOTICE,
  CausalEngine,
  buildComparison,
  compare,
} from './causal-engine.js';
