/**
 * Retreat animation (Universal 5D GemArc Web v5.0 §7.3).
 *
 * The retreat turns a relevance classification into a *display* treatment:
 * the selected object keeps the foreground, direct relations stay clear,
 * contextual relations lose visual weight, unrelated objects retreat.
 *
 * Hard guarantees:
 *
 *   1. The retreat never touches real architecture coordinates, factual
 *      relations or workflow execution semantics. It only calls an injected
 *      `applyTransform` callback with a treatment record; the model, the graph
 *      store and the workflow definitions are read-only here.
 *   2. The treatment is reversible. The controller remembers the treatment
 *      every entity had before the first change and puts it back verbatim on
 *      restore, so no stale opacity or position survives.
 *   3. Rapid successive clicks cancel a transition that has not finished yet.
 *      A superseded transition never applies its treatments and never calls
 *      its completion callback.
 *
 * The opacity / outline / label numbers come from the vocabulary
 * (`relationClassSpec(id).visual`), never from hard-coded constants.
 */
import { relationClassSpec } from '../core/vocab.js';

/**
 * The visual fields a treatment carries. They mirror the vocabulary
 * `visual` block of a relation class.
 * @type {readonly string[]}
 */
export const TREATMENT_FIELDS = Object.freeze([
  'opacity',
  'outline',
  'labels',
  'lineWeight',
  'zOffset',
]);

/**
 * Build the display treatment for a relevance class.
 *
 * @param {string} relationClassId one of the four relation classes
 * @returns {object|null} treatment record, or null for an unknown class
 */
export function treatmentForRelationClass(relationClassId) {
  const spec = relationClassSpec(relationClassId);
  if (!spec) return null;
  const visual = spec.visual || {};
  return {
    relationClass: spec.id,
    opacity: visual.opacity ?? null,
    outline: visual.outline ?? null,
    labels: visual.labels ?? null,
    lineWeight: visual.lineWeight ?? null,
    zOffset: visual.zOffset ?? null,
    // Secondary labels are collapsed / hidden for everything but the two
    // foreground classes. The vocabulary invariant forbids collapsing the
    // labels of `Selected` and `Directly Related`.
    collapsed: visual.labels === 'collapsed' || visual.labels === 'hidden',
    // Display-only displacement. Always null: the retreat must never move a
    // real architecture coordinate.
    position: null,
  };
}

/**
 * Scheduler backed by `setTimeout` / `clearTimeout`.
 * @returns {{schedule: Function, cancel: Function}}
 */
export function createTimerScheduler() {
  return {
    schedule(callback, delayMs) {
      return setTimeout(callback, delayMs);
    },
    cancel(handle) {
      clearTimeout(handle);
    },
  };
}

/**
 * Scheduler that never fires on its own. Tests use it to drive transitions
 * by hand and to prove that a superseded transition stays dead.
 * @returns {{schedule: Function, cancel: Function, flush: Function, size: number}}
 */
export function createManualScheduler() {
  const pending = new Map();
  let sequence = 0;
  return {
    schedule(callback) {
      sequence += 1;
      pending.set(sequence, callback);
      return sequence;
    },
    cancel(handle) {
      pending.delete(handle);
    },
    flush() {
      const entries = [...pending.entries()];
      pending.clear();
      for (const [, callback] of entries) callback();
      return entries.length;
    },
    get size() {
      return pending.size;
    },
  };
}

/**
 * DOM-friendly default adapter. It writes CSS custom properties on the
 * element that carries `data-gemarc-id="<id>"`.
 *
 * The adapter is optional: the controller core stays testable in plain Node.
 * Outside a browser it degrades to a no-op instead of throwing.
 *
 * @param {{root?: object|null, selectorPrefix?: string}} [options]
 * @returns {(payload: object) => void}
 */
export function createDomTransformAdapter({ root = null, selectorPrefix = 'data-gemarc-id' } = {}) {
  return function applyTransform(payload) {
    if (typeof document === 'undefined') return;
    const scope = root || (typeof document !== 'undefined' ? document : null);
    if (!scope || typeof scope.querySelector !== 'function') return;
    const element = scope.querySelector(`[${selectorPrefix}="${payload.id}"]`);
    if (!element) return;
    const treatment = payload.treatment;
    if (!treatment) {
      element.style.opacity = '';
      for (const field of TREATMENT_FIELDS) element.style.removeProperty(`--gemarc-${field}`);
      element.style.removeProperty('--gemarc-collapsed');
      element.style.removeProperty('--gemarc-position');
      return;
    }
    element.style.opacity = String(treatment.opacity ?? 1);
    for (const field of TREATMENT_FIELDS) {
      element.style.setProperty(`--gemarc-${field}`, String(treatment[field] ?? ''));
    }
    element.style.setProperty('--gemarc-collapsed', treatment.collapsed ? '1' : '0');
  };
}

/**
 * Applies and reverses the retreat treatment.
 */
export class RetreatAnimationController {
  /**
   * @param {{applyTransform?: Function, scheduler?: object|null,
   *          readTreatment?: Function, durationMs?: number}} [options]
   */
  constructor({ applyTransform = null, scheduler = null, readTreatment = null, durationMs = 0 } = {}) {
    /** @type {(payload: object) => void} */
    this.applyTransform = applyTransform || (() => {});
    this.scheduler = scheduler || createTimerScheduler();
    /** Reads the treatment an entity had before the first change. */
    this.readTreatment = readTreatment || (() => null);
    this.durationMs = Number.isFinite(durationMs) ? durationMs : 0;
    /** @type {Map<string, object>} key -> payload currently applied */
    this.state = new Map();
    /** @type {Map<string, object|null>} key -> treatment before the first change */
    this.originals = new Map();
    /** @type {Map<number, object>} transitionId -> token */
    this.inFlight = new Map();
    this.sequence = 0;
  }

  /**
   * Apply the retreat for a relevance report.
   *
   * @param {object} report report produced by `RelevanceGraphEvaluator.evaluate`
   * @param {{durationMs?: number, onComplete?: Function}} [options]
   * @returns {{transitionId: number, applied: boolean, cancelled: boolean,
   *            targets: object[]}} transition handle
   */
  apply(report, { durationMs = this.durationMs, onComplete = null } = {}) {
    const transitionId = this.sequence + 1;
    this.sequence = transitionId;
    // A new transition supersedes every transition that is still running.
    this.cancelAll();

    const targets = (report?.entries || []).map((entry) => ({
      key: entry.key,
      id: entry.id,
      type: entry.type,
      relationClass: entry.relationClass,
      treatment: treatmentForRelationClass(entry.relationClass),
    }));

    for (const target of targets) {
      if (!this.originals.has(target.key)) {
        this.originals.set(target.key, this.readTreatment(target) ?? null);
      }
    }

    const token = { id: transitionId, cancelled: false, handle: null, targets, onComplete };
    this.inFlight.set(transitionId, token);
    if (durationMs > 0) {
      token.handle = this.scheduler.schedule(() => this._settle(token), durationMs);
      return { transitionId, applied: false, cancelled: false, targets };
    }
    this._settle(token);
    return { transitionId, applied: true, cancelled: false, targets };
  }

  /**
   * Restore every entity to the treatment it had before the first change.
   * Entities that never had a treatment are cleared, so no leftover opacity
   * or position can survive a restore.
   *
   * @returns {{restored: Array<{key: string, treatment: object|null}>}}
   */
  restore() {
    this.cancelAll();
    const restored = [];
    for (const [key, payload] of this.state) {
      const original = this.originals.has(key) ? this.originals.get(key) : null;
      this.applyTransform({
        key,
        id: payload.id,
        type: payload.type,
        relationClass: null,
        treatment: original ?? null,
      });
      restored.push({ key, treatment: original ?? null });
    }
    this.state.clear();
    return { restored };
  }

  /**
   * The treatment currently applied to an entity key.
   * @param {string} key entity key, e.g. `node:n1`
   * @returns {object|null}
   */
  getTreatment(key) {
    return this.state.get(key)?.treatment ?? null;
  }

  /**
   * The treatment an entity had before the first change.
   * @param {string} key entity key, e.g. `node:n1`
   * @returns {object|null}
   */
  getOriginal(key) {
    return this.originals.has(key) ? this.originals.get(key) : null;
  }

  /**
   * Cancel one running transition.
   * @param {number} transitionId
   * @returns {boolean} true when a running transition was cancelled
   */
  cancel(transitionId) {
    const token = this.inFlight.get(transitionId);
    if (!token) return false;
    token.cancelled = true;
    if (token.handle !== null) this.scheduler.cancel(token.handle);
    this.inFlight.delete(transitionId);
    return true;
  }

  /**
   * Cancel every running transition. Their completion callbacks are not called.
   * @returns {number} number of cancelled transitions
   */
  cancelAll() {
    const count = this.inFlight.size;
    for (const token of this.inFlight.values()) {
      token.cancelled = true;
      if (token.handle !== null) this.scheduler.cancel(token.handle);
    }
    this.inFlight.clear();
    return count;
  }

  /**
   * True while a transition is still running.
   * @returns {boolean}
   */
  get isTransitioning() {
    return this.inFlight.size > 0;
  }

  /**
   * A snapshot of the applied and remembered treatments.
   * @returns {{applied: object, original: object}}
   */
  snapshot() {
    const applied = {};
    for (const [key, payload] of this.state) applied[key] = payload.treatment;
    const original = {};
    for (const [key, treatment] of this.originals) original[key] = treatment;
    return { applied, original };
  }

  _settle(token) {
    if (token.cancelled) return false;
    if (this.inFlight.get(token.id) !== token) return false;
    for (const target of token.targets) {
      const payload = {
        key: target.key,
        id: target.id,
        type: target.type,
        relationClass: target.relationClass,
        treatment: target.treatment,
      };
      this.applyTransform(payload);
      this.state.set(target.key, payload);
    }
    this.inFlight.delete(token.id);
    if (token.onComplete) {
      token.onComplete({ transitionId: token.id, applied: true, cancelled: false });
    }
    return true;
  }
}
