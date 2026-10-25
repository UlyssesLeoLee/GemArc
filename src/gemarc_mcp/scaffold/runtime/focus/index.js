/**
 * Public API of the Focus–Context engine.
 * @module focus
 */
export {
  FOCUS_TARGET_TYPES,
  RELEVANCE_CLASS_IDS,
  RelevanceGraphEvaluator,
} from './relevance-evaluator.js';
export {
  RetreatAnimationController,
  TREATMENT_FIELDS,
  treatmentForRelationClass,
  createTimerScheduler,
  createManualScheduler,
  createDomTransformAdapter,
} from './retreat-animation.js';
export { ContextRestorationManager } from './context-restoration.js';
export { FocusContextEngine, resolveFocusTarget } from './focus-context.js';
