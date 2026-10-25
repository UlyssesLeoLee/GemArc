/**
 * Workflow runtime - barrel for the workflow semantics modules.
 *
 * The three modules are kept apart on purpose:
 *   - `workflow-model.js`     the execution model (identity, ports, traversal)
 *   - `workflow-validator.js` semantic validation (never reads canvas data)
 *   - `canvas-controller.js`  presentation interaction (writes canvas data only)
 *
 * Importing this barrel gives the whole workflow runtime; importing a single
 * module keeps a consumer free of the others.
 */
export {
  WorkflowModel,
  normalizeWorkflow,
  isExecutionEdge,
  isKnownNodeKind,
  isKnownEdgeKind,
  isKnownConstraintKind,
  isKnownPortKind,
  WORKFLOW_PORT_KIND_IDS,
  WORKFLOW_EXECUTION_EDGE_KINDS,
} from './workflow-model.js';

export {
  WorkflowValidator,
  validateWorkflow,
  isMachineJudgableCondition,
} from './workflow-validator.js';

export { CanvasController } from './canvas-controller.js';
