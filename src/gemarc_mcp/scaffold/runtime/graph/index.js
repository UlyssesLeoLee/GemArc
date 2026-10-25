/**
 * The graph package: the property graph, the indexes over it, the
 * persistence boundary, the interaction state machine and the optional
 * third-party adapters.
 *
 * Importing this barrel pulls in no third-party package.
 */

/** The secondary indexes over a graph store. */
export { GraphIndexManager } from './graph-index.js';

/** The persistence boundary and the backends that ship with it. */
export {
  GraphPersistenceAdapter,
  InMemoryPersistenceAdapter,
  IndexedDbPersistenceAdapter,
  PERSISTENCE_SCHEMA_VERSION,
  createMemoryPersistenceAdapter,
  createIndexedDbPersistenceAdapter,
  createPersistenceAdapter,
  registerPersistenceAdapter,
  listPersistenceAdapters,
  isIndexedDbAvailable,
} from './graph-persistence.js';

/** The interaction state machine. */
export { GraphInteractionController } from './graph-interaction.js';

/** The optional third-party adapters and the built-in layout fallback. */
export {
  OPTIONAL_ADAPTERS,
  createGraphologyAdapter,
  createDagreLayoutAdapter,
  createDexieAdapter,
  createSigmaRendererAdapter,
  createThreeRendererAdapter,
  createLayoutEngine,
  detectOptionalAdapters,
  layeredLayout,
} from './graph-adapters.js';
