/**
 * Persistence boundary for the graph.
 *
 * The runtime must work with no third-party dependency, so persistence is
 * split in two:
 *
 *   GraphPersistenceAdapter   the boundary. It knows how to save, load, report
 *                             the schema version and run migrations.
 *   a backend                 where the bytes go. Three ship with the runtime:
 *                             an in-memory backend (always available), an
 *                             IndexedDB backend (only when the environment
 *                             provides `indexedDB`), and a registry that lets
 *                             another backend be plugged in.
 *
 * A backend is any object with this shape:
 *
 *   {
 *     name: string,
 *     schemaVersion: string,
 *     open(): Promise<void>,
 *     close(): Promise<void>,
 *     saveModel(model, meta): Promise<object>,
 *     loadModel(meta): Promise<object|null>,
 *     clear(): Promise<void>,
 *     storedVersion(meta): Promise<string|null>,
 *   }
 */
import { GemArcError } from '../core/errors.js';

/** The schema version the runtime writes today. */
export const PERSISTENCE_SCHEMA_VERSION = '1.0.0';

function cloneModel(value) {
  if (value === null || value === undefined) return value;
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(value);
    } catch {
      // Fall through to the JSON clone below.
    }
  }
  return JSON.parse(JSON.stringify(value));
}

function requestToPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new GemArcError('GEMARC_GRAPH_002', 'IndexedDB 操作失败'));
  });
}

function transactionToPromise(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(new GemArcError('GEMARC_GRAPH_002', 'IndexedDB 事务失败'));
    transaction.onabort = () => reject(new GemArcError('GEMARC_GRAPH_002', 'IndexedDB 事务被中止'));
  });
}

/**
 * A persistence backend that keeps the graph in memory. It is always
 * available, needs no environment feature, and is the reference
 * implementation of the backend contract.
 */
export class InMemoryPersistenceAdapter {
  /**
   * @param {{schemaVersion?: string, name?: string}} [options]
   */
  constructor(options = {}) {
    this.schemaVersion = options.schemaVersion || PERSISTENCE_SCHEMA_VERSION;
    this._name = options.name || 'memory';
    this._records = new Map();
    this._opened = false;
  }

  /** @returns {string} the backend name. */
  get name() {
    return this._name;
  }

  /** Open the backend. */
  async open() {
    this._opened = true;
    return this;
  }

  /** Close the backend. */
  async close() {
    this._opened = false;
    return this;
  }

  /**
   * Store a model.
   * @param {object} model the canonical model
   * @param {{key?: string}} [meta] where to store it
   */
  async saveModel(model, meta = {}) {
    const key = meta.key || 'default';
    this._records.set(key, {
      key,
      schemaVersion: this.schemaVersion,
      savedAt: Date.now(),
      model: cloneModel(model),
    });
    return { saved: true, key, schemaVersion: this.schemaVersion };
  }

  /**
   * Read a model back.
   * @param {{key?: string}} [meta] where to read it from
   * @returns {Promise<object|null>} the model, or null when nothing is stored
   */
  async loadModel(meta = {}) {
    const key = meta.key || 'default';
    const record = this._records.get(key);
    return record ? cloneModel(record.model) : null;
  }

  /** Remove every stored model. */
  async clear() {
    this._records.clear();
    return { cleared: true };
  }

  /**
   * @param {{key?: string}} [meta] where to look
   * @returns {Promise<string|null>} the stored schema version
   */
  async storedVersion(meta = {}) {
    const record = this._records.get(meta.key || 'default');
    return record ? record.schemaVersion : null;
  }
}

/**
 * A persistence backend on top of IndexedDB. It activates only when the
 * environment provides `indexedDB`; Dexie is optional and only used when it
 * is handed in.
 */
export class IndexedDbPersistenceAdapter {
  /**
   * @param {{indexedDB?: object, Dexie?: object, dbName?: string, storeName?: string,
   *   schemaVersion?: string}} [options]
   */
  constructor(options = {}) {
    const indexedDB = options.indexedDB || (typeof globalThis !== 'undefined' ? globalThis.indexedDB : null);
    const Dexie = options.Dexie || null;
    if (!indexedDB && !Dexie) {
      throw new GemArcError('GEMARC_GRAPH_002', '当前环境没有 IndexedDB，无法启用 IndexedDB 持久化');
    }
    this._indexedDB = indexedDB;
    this._Dexie = Dexie;
    this.dbName = options.dbName || 'gemarc-graph';
    this.storeName = options.storeName || 'graph';
    this.schemaVersion = options.schemaVersion || PERSISTENCE_SCHEMA_VERSION;
    this._db = null;
  }

  /** @returns {string} the backend name. */
  get name() {
    return this._Dexie ? 'indexeddb-dexie' : 'indexeddb';
  }

  /** Open the database, creating the object store when needed. */
  async open() {
    if (this._db) return this._db;
    if (this._Dexie) {
      const db = new this._Dexie(this.dbName);
      db.version(1).stores({ [this.storeName]: 'key, savedAt' });
      await db.open();
      this._db = db;
      return this._db;
    }
    const request = this._indexedDB.open(this.dbName, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(this.storeName)) {
        db.createObjectStore(this.storeName, { keyPath: 'key' });
      }
    };
    this._db = await requestToPromise(request);
    return this._db;
  }

  /** Close the database. */
  async close() {
    if (this._db && typeof this._db.close === 'function') this._db.close();
    this._db = null;
    return this;
  }

  /**
   * Store a model.
   * @param {object} model the canonical model
   * @param {{key?: string}} [meta] where to store it
   */
  async saveModel(model, meta = {}) {
    const key = meta.key || 'default';
    const record = {
      key,
      schemaVersion: this.schemaVersion,
      savedAt: Date.now(),
      model: cloneModel(model),
    };
    const db = await this.open();
    if (this._Dexie) {
      await db.table(this.storeName).put(record);
      return { saved: true, key, schemaVersion: this.schemaVersion };
    }
    const transaction = db.transaction(this.storeName, 'readwrite');
    transaction.objectStore(this.storeName).put(record);
    await transactionToPromise(transaction);
    return { saved: true, key, schemaVersion: this.schemaVersion };
  }

  /**
   * Read a model back.
   * @param {{key?: string}} [meta] where to read it from
   * @returns {Promise<object|null>} the model, or null when nothing is stored
   */
  async loadModel(meta = {}) {
    const key = meta.key || 'default';
    const db = await this.open();
    let record = null;
    if (this._Dexie) {
      record = await db.table(this.storeName).get(key);
    } else {
      const transaction = db.transaction(this.storeName, 'readonly');
      const request = transaction.objectStore(this.storeName).get(key);
      record = await requestToPromise(request);
    }
    return record ? cloneModel(record.model) : null;
  }

  /** Remove every stored model. */
  async clear() {
    const db = await this.open();
    if (this._Dexie) {
      await db.table(this.storeName).clear();
      return { cleared: true };
    }
    const transaction = db.transaction(this.storeName, 'readwrite');
    transaction.objectStore(this.storeName).clear();
    await transactionToPromise(transaction);
    return { cleared: true };
  }

  /**
   * @param {{key?: string}} [meta] where to look
   * @returns {Promise<string|null>} the stored schema version
   */
  async storedVersion(meta = {}) {
    const key = meta.key || 'default';
    const db = await this.open();
    let record = null;
    if (this._Dexie) {
      record = await db.table(this.storeName).get(key);
    } else {
      const transaction = db.transaction(this.storeName, 'readonly');
      const request = transaction.objectStore(this.storeName).get(key);
      record = await requestToPromise(request);
    }
    return record ? record.schemaVersion : null;
  }
}

/**
 * The persistence boundary: it saves, loads, reports the schema version and
 * runs the migrations of the backend it wraps.
 */
export class GraphPersistenceAdapter {
  /**
   * @param {object} backend a backend implementing the documented contract
   * @param {{migrations?: Map<string, {migrate: Function, version?: string}>,
   *   schemaVersion?: string}} [options]
   */
  constructor(backend, options = {}) {
    if (!backend || typeof backend.saveModel !== 'function' || typeof backend.loadModel !== 'function') {
      throw new GemArcError('GEMARC_GRAPH_005', '持久化后端没有实现必要的接口');
    }
    this.backend = backend;
    this.migrations = new Map(options.migrations || []);
    this.schemaVersion = options.schemaVersion || backend.schemaVersion || PERSISTENCE_SCHEMA_VERSION;
  }

  /** @returns {string} the name of the backend in use. */
  get name() {
    return this.backend.name || 'unknown';
  }

  /** Open the backend. */
  async open() {
    if (typeof this.backend.open === 'function') await this.backend.open();
    return this;
  }

  /** Close the backend. */
  async close() {
    if (typeof this.backend.close === 'function') await this.backend.close();
    return this;
  }

  /**
   * Save a model.
   * @param {object} model the canonical model
   * @param {{key?: string}} [meta] where to store it
   */
  async save(model, meta = {}) {
    await this.open();
    return this.backend.saveModel(model, meta);
  }

  /**
   * Load a model.
   * @param {{key?: string}} [meta] where to read it from
   * @returns {Promise<object|null>} the model, or null when nothing is stored
   */
  async load(meta = {}) {
    await this.open();
    return this.backend.loadModel(meta);
  }

  /** Remove every stored model. */
  async clear() {
    await this.open();
    if (typeof this.backend.clear === 'function') await this.backend.clear();
    return this;
  }

  /**
   * Report the version situation of the stored data.
   * @returns {Promise<{backend: string, schemaVersion: string, storedVersion: string|null,
   *   needsMigration: boolean, empty: boolean}>}
   */
  async info() {
    const storedVersion = typeof this.backend.storedVersion === 'function'
      ? await this.backend.storedVersion()
      : null;
    return {
      backend: this.name,
      schemaVersion: this.schemaVersion,
      storedVersion,
      needsMigration: storedVersion !== null && storedVersion !== this.schemaVersion,
      empty: storedVersion === null,
    };
  }

  /**
   * Register a migration step.
   * @param {string} fromVersion the version the step reads
   * @param {string} toVersion the version the step writes
   * @param {(model: object) => object|{model: object, version: string}} migrate
   */
  registerMigration(fromVersion, toVersion, migrate) {
    this.migrations.set(fromVersion, { toVersion, migrate });
    return this;
  }

  /**
   * Bring stored data up to the current schema version.
   * @returns {Promise<{migrated: boolean, reason?: string, from?: string|null, to?: string, applied?: string[]}>}
   */
  async migrate() {
    const info = await this.info();
    if (info.storedVersion === null) {
      return { migrated: false, reason: 'empty', from: null, to: this.schemaVersion, applied: [] };
    }
    if (!info.needsMigration) {
      return { migrated: false, reason: 'current', from: info.storedVersion, to: this.schemaVersion, applied: [] };
    }
    let model = await this.load();
    let version = info.storedVersion;
    const applied = [];
    while (version !== this.schemaVersion) {
      const step = this.migrations.get(version);
      if (!step) {
        throw new GemArcError('GEMARC_GRAPH_003', '缺少数据迁移路径', String(version));
      }
      const result = await step.migrate(model);
      model = result && result.model !== undefined ? result.model : result;
      const nextVersion = result && result.version ? result.version : step.toVersion || this.schemaVersion;
      if (nextVersion === version) {
        throw new GemArcError('GEMARC_GRAPH_003', '数据迁移没有推进版本', String(version));
      }
      applied.push(`${version}->${nextVersion}`);
      version = nextVersion;
    }
    await this.save(model);
    return { migrated: true, from: info.storedVersion, to: this.schemaVersion, applied };
  }
}

/**
 * True when the environment can host the IndexedDB backend.
 * @param {object} [environment] the object to look at, defaults to globalThis
 */
export function isIndexedDbAvailable(environment = typeof globalThis !== 'undefined' ? globalThis : {}) {
  return Boolean(environment && (environment.indexedDB || environment.Dexie));
}

/**
 * Create the in-memory backend. Always available.
 * @param {{schemaVersion?: string, name?: string}} [options]
 */
export function createMemoryPersistenceAdapter(options = {}) {
  return new InMemoryPersistenceAdapter(options);
}

/**
 * Create the IndexedDB backend, or return null when the environment has no
 * IndexedDB. Dexie is optional and may be injected.
 * @param {{indexedDB?: object, Dexie?: object, dbName?: string, storeName?: string,
 *   schemaVersion?: string}} [options]
 */
export function createIndexedDbPersistenceAdapter(options = {}) {
  const environment = typeof globalThis !== 'undefined' ? globalThis : {};
  const indexedDB = options.indexedDB || environment.indexedDB || null;
  const Dexie = options.Dexie || environment.Dexie || null;
  if (!indexedDB && !Dexie) return null;
  return new IndexedDbPersistenceAdapter({ ...options, indexedDB, Dexie });
}

const ADAPTER_FACTORIES = new Map();

/**
 * Register another backend so the application can choose it by name.
 * @param {string} name the backend name
 * @param {(options?: object) => object} factory the factory function
 */
export function registerPersistenceAdapter(name, factory) {
  if (typeof name !== 'string' || name === '') {
    throw new GemArcError('GEMARC_GRAPH_005', '持久化后端名称无效');
  }
  if (typeof factory !== 'function') {
    throw new GemArcError('GEMARC_GRAPH_005', '持久化后端工厂必须是函数', name);
  }
  ADAPTER_FACTORIES.set(name, factory);
  return name;
}

/**
 * Create a backend by name.
 * @param {string} name the backend name
 * @param {object} [options] options handed to the factory
 * @returns {object|null} the backend, or null when it is not available here
 */
export function createPersistenceAdapter(name, options = {}) {
  const factory = ADAPTER_FACTORIES.get(name);
  if (!factory) {
    throw new GemArcError('GEMARC_GRAPH_005', '未登记的持久化后端', String(name));
  }
  return factory(options);
}

/**
 * @returns {string[]} the names of every registered backend
 */
export function listPersistenceAdapters() {
  return [...ADAPTER_FACTORIES.keys()];
}

registerPersistenceAdapter('memory', (options = {}) => new InMemoryPersistenceAdapter(options));
registerPersistenceAdapter('indexeddb', (options = {}) => createIndexedDbPersistenceAdapter(options));
