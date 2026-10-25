/**
 * The view package: the shared state every view reads and the router that
 * turns a view definition into a renderable selection.
 *
 * Importing this barrel pulls in no third-party package.
 */

/** The shared state every view reads. */
export {
  ViewState,
  VIEW_STATE_FIELDS,
  readModelPath,
} from './view-state.js';

/** The router that turns a view definition into a renderable selection. */
export {
  ViewRouter,
  assertViewDefinition,
  checkFidelity,
  hasCompleteAxes,
} from './view-router.js';
