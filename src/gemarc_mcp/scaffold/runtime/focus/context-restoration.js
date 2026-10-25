/**
 * Context restoration (Universal 5D GemArc Web v5.0 §7.4).
 *
 * The manager owns the focus history and every operation that moves the user
 * out of a focus again:
 *
 *   Back             step to the previous focus in the trail
 *   Clear Focus      drop the focus and every display override
 *   Restore Overview drop the focus but keep the trail as a breadcrumb
 *   Lock Focus       freeze the current focus against further switching
 *   Focus Breadcrumb the trail the user walked through
 *   Compare Focus    two focus targets side by side
 *
 * Restoring cancels a transition that is still running, so a fast sequence of
 * clicks can never leave a half-applied opacity or position behind.
 */
import { GemArcError } from '../core/errors.js';

/**
 * Keeps the focus trail and performs the restoration operations.
 */
export class ContextRestorationManager {
  /**
   * @param {{animation?: object|null, evaluator?: object|null,
   *          resolve?: Function|null, maxHistory?: number}} [options]
   */
  constructor({ animation = null, evaluator = null, resolve = null, maxHistory = 32 } = {}) {
    this.animation = animation;
    this.evaluator = evaluator;
    this.resolve = resolve;
    this.maxHistory = maxHistory;
    /** @type {object[]} */
    this.trail = [];
    /** @type {object|null} */
    this.current = null;
    this.locked = false;
  }

  /**
   * Push a focus session onto the trail.
   * @param {object} session session produced by `FocusContextEngine.focus`
   * @returns {object} the recorded session
   */
  record(session) {
    if (!session) return null;
    this.trail.push(session);
    while (this.trail.length > this.maxHistory) this.trail.shift();
    this.current = session;
    return session;
  }

  /**
   * The focus trail, oldest first.
   * @returns {object[]}
   */
  get history() {
    return [...this.trail];
  }

  /**
   * The breadcrumb of the focus trail.
   * @returns {Array<{id: string, type: string, targetId: string, label: string}>}
   */
  breadcrumb() {
    return this.trail.map((session) => ({
      id: session.id,
      type: session.target?.type || '',
      targetId: session.target?.id || '',
      label: session.target?.label || session.target?.id || '',
    }));
  }

  /**
   * Step back to the previous focus. When no previous focus is left the
   * overview is restored instead.
   *
   * @param {{durationMs?: number}} [options]
   * @returns {{restored: object|null, transition: object|null, overview: boolean}}
   */
  back({ durationMs } = {}) {
    if (this.current) this.trail.pop();
    const previous = this.trail.length > 0 ? this.trail[this.trail.length - 1] : null;
    if (!previous) {
      this.animation?.cancelAll?.();
      this.animation?.restore?.();
      this.current = null;
      return { restored: null, transition: null, overview: true };
    }
    let transition = null;
    if (this.animation) {
      transition = this.animation.apply(previous.report, {
        durationMs: durationMs ?? this.animation.durationMs,
      });
    }
    this.current = previous;
    return { restored: previous, transition, overview: false };
  }

  /**
   * Drop the focus and every display override, and empty the trail.
   * @returns {{cleared: boolean}}
   */
  clearFocus() {
    this.animation?.cancelAll?.();
    this.animation?.restore?.();
    this.trail = [];
    this.current = null;
    return { cleared: true };
  }

  /**
   * Drop the focus and every display override but keep the trail, so the user
   * can still see where they came from and step back.
   * @returns {{overview: boolean}}
   */
  restoreOverview() {
    this.animation?.cancelAll?.();
    this.animation?.restore?.();
    this.current = null;
    return { overview: true };
  }

  /**
   * Lock or unlock the current focus.
   * @param {boolean} [locked]
   * @returns {boolean} the new lock state
   */
  setLocked(locked = true) {
    this.locked = Boolean(locked);
    return this.locked;
  }

  /**
   * True when the focus is locked against further switching.
   * @returns {boolean}
   */
  isLocked() {
    return this.locked;
  }

  /**
   * Compare two focus targets. Both targets are classified with the same
   * evaluator, so the comparison is a class-by-class diff.
   *
   * @param {object} targetA
   * @param {object} targetB
   * @returns {{a: object, b: object, shared: object[], onlyA: object[],
   *            onlyB: object[], changed: object[]}}
   */
  compareFocus(targetA, targetB) {
    if (!this.evaluator || typeof this.resolve !== 'function') {
      throw new GemArcError('GEMARC_FOCUS_004', '对比聚焦需要相关性评估器与目标解析器');
    }
    const reportA = this.evaluator.evaluate(this.resolve(targetA));
    const reportB = this.evaluator.evaluate(this.resolve(targetB));
    const classesB = new Map(reportB.entries.map((entry) => [entry.key, entry.relationClass]));
    const classesA = new Map(reportA.entries.map((entry) => [entry.key, entry.relationClass]));
    const shared = [];
    const onlyA = [];
    const changed = [];
    for (const entry of reportA.entries) {
      const other = classesB.get(entry.key);
      if (other === undefined) {
        onlyA.push(entry);
        continue;
      }
      shared.push({ key: entry.key, a: entry.relationClass, b: other });
      if (other !== entry.relationClass) {
        changed.push({ key: entry.key, from: entry.relationClass, to: other });
      }
    }
    const onlyB = reportB.entries.filter((entry) => !classesA.has(entry.key));
    return { a: reportA, b: reportB, shared, onlyA, onlyB, changed };
  }
}
