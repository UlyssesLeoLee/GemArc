/**
 * Interaction state machine for the graph views.
 *
 * The controller owns what the pointer and the keyboard did: what is hovered,
 * what is selected, what has focus and which path is being walked. It is
 * framework agnostic - it emits events and the renderer decides what to draw.
 *
 * Two rules shape the file:
 *
 *  1. A node and an edge are different first-class targets. Clicking an edge
 *     selects that edge; it never collapses into selecting its source node.
 *  2. The selection stays keyboard reachable. The selection keeps its order,
 *     a cursor walks through it, and the cursor target can be activated.
 *
 * When a `ViewState` is supplied, every change is written through to it, so
 * the 2D view and the 3D view always show the same selection.
 */
import { GemArcError } from '../core/errors.js';

function normalizeTarget(target) {
  if (!target) return null;
  if (typeof target === 'string') return { type: 'node', id: target };
  if (typeof target !== 'object') return null;
  const type = target.type === 'edge' ? 'edge' : 'node';
  if (typeof target.id !== 'string' || target.id === '') return null;
  return { type, id: target.id };
}

function sameTarget(left, right) {
  return Boolean(left) && Boolean(right) && left.type === right.type && left.id === right.id;
}

/**
 * The interaction state machine: hover, selection, focus and path.
 */
export class GraphInteractionController {
  /**
   * @param {{store?: object, state?: object}} [options] the graph store and the
   *   shared view state the controller writes through to
   */
  constructor(options = {}) {
    this.store = options.store || null;
    this.state = options.state || null;
    this._hover = null;
    this._selection = [];
    this._focus = null;
    this._path = { nodeIds: [], edgeIds: [] };
    this._cursor = 0;
    this._handlers = new Map();
  }

  // --- observed state ------------------------------------------------------

  /** @returns {{type: 'node'|'edge', id: string}|null} the hovered target. */
  get hoverTarget() {
    return this._hover ? { ...this._hover } : null;
  }

  /** @returns {{nodeIds: string[], edgeIds: string[]}} the current selection. */
  get selection() {
    return {
      nodeIds: this._selection.filter((item) => item.type === 'node').map((item) => item.id),
      edgeIds: this._selection.filter((item) => item.type === 'edge').map((item) => item.id),
    };
  }

  /** @returns {string[]} the selected node ids. */
  get selectedNodeIds() {
    return this.selection.nodeIds;
  }

  /** @returns {string[]} the selected edge ids. */
  get selectedEdgeIds() {
    return this.selection.edgeIds;
  }

  /** @returns {{type: 'node'|'edge', id: string, context: unknown}|null} the focus. */
  get focusTarget() {
    return this._focus ? { ...this._focus } : null;
  }

  /** @returns {unknown} the opaque focus context owned by the focus engine. */
  get focusContext() {
    return this._focus ? this._focus.context : null;
  }

  /** @returns {{nodeIds: string[], edgeIds: string[]}} the path under inspection. */
  get activePath() {
    return { nodeIds: [...this._path.nodeIds], edgeIds: [...this._path.edgeIds] };
  }

  /** @returns {Array<{type: string, id: string}>} the selection in order. */
  selectionOrder() {
    return this._selection.map((item) => ({ ...item }));
  }

  // --- events --------------------------------------------------------------

  /**
   * Subscribe to an interaction event.
   * @param {string} type the event name, or '*' for every event
   * @param {(event: object) => void} handler the handler
   * @returns {() => void} an unsubscribe function
   */
  on(type, handler) {
    if (typeof type !== 'string' || type === '') {
      throw new GemArcError('GEMARC_GRAPH_006', '事件名称无效');
    }
    if (typeof handler !== 'function') {
      throw new GemArcError('GEMARC_GRAPH_006', '事件处理函数无效', type);
    }
    const handlers = this._handlers.get(type) || new Set();
    handlers.add(handler);
    this._handlers.set(type, handlers);
    return () => this.off(type, handler);
  }

  /**
   * Unsubscribe from an interaction event.
   * @param {string} type the event name
   * @param {(event: object) => void} handler the handler
   */
  off(type, handler) {
    const handlers = this._handlers.get(type);
    if (!handlers) return false;
    const removed = handlers.delete(handler);
    if (handlers.size === 0) this._handlers.delete(type);
    return removed;
  }

  // --- hover ---------------------------------------------------------------

  /**
   * Record the hovered target.
   * @param {{type: 'node'|'edge', id: string}|string|null} target
   */
  hover(target) {
    const normalized = target ? normalizeTarget(target) : null;
    if (target && !normalized) {
      throw new GemArcError('GEMARC_GRAPH_006', '悬停目标无效');
    }
    if (normalized && this.store && !this._exists(normalized)) {
      this._hover = null;
      this._emit('hover', { target: null });
      return null;
    }
    this._hover = normalized;
    this._emit('hover', { target: this._hover ? { ...this._hover } : null });
    return this._hover;
  }

  /** Clear the hover state. */
  clearHover() {
    this._hover = null;
    this._emit('hover', { target: null });
    return this;
  }

  // --- selection -----------------------------------------------------------

  /**
   * Click a target. A node click selects the node, an edge click selects the
   * edge - the edge never collapses into its source node.
   * @param {{type: 'node'|'edge', id: string}|string} target
   * @param {{additive?: boolean}} [options] keep the current selection
   */
  click(target, options = {}) {
    const normalized = normalizeTarget(target);
    if (!normalized) {
      throw new GemArcError('GEMARC_GRAPH_006', '点击目标无效');
    }
    if (this.store && !this._exists(normalized)) {
      return null;
    }
    const additive = Boolean(options.additive);
    if (!additive) {
      this._selection = [];
      this._cursor = 0;
    }
    if (!this._selection.some((item) => sameTarget(item, normalized))) {
      this._selection.push(normalized);
    }
    this._syncSelection();
    this._emit('select', { target: { ...normalized }, selection: this.selection });
    return { ...normalized };
  }

  /**
   * Select a node.
   * @param {string} id the node id
   * @param {{additive?: boolean}} [options] keep the current selection
   */
  selectNode(id, options = {}) {
    return this.click({ type: 'node', id }, options);
  }

  /**
   * Select an edge. The edge is a target of its own; its endpoints are not
   * dragged into the selection.
   * @param {string} id the edge id
   * @param {{additive?: boolean}} [options] keep the current selection
   */
  selectEdge(id, options = {}) {
    return this.click({ type: 'edge', id }, options);
  }

  /**
   * Add the target to the selection, or remove it when it is already there.
   * @param {{type: 'node'|'edge', id: string}} target
   */
  toggle(target) {
    const normalized = normalizeTarget(target);
    if (!normalized) {
      throw new GemArcError('GEMARC_GRAPH_006', '切换目标无效');
    }
    const index = this._selection.findIndex((item) => sameTarget(item, normalized));
    if (index >= 0) {
      this._selection.splice(index, 1);
      this._cursor = Math.min(this._cursor, Math.max(0, this._selection.length - 1));
      this._syncSelection();
      this._emit('select', { target: { ...normalized }, removed: true, selection: this.selection });
      return null;
    }
    return this.click(normalized, { additive: true });
  }

  /** Clear the selection. */
  clearSelection() {
    this._selection = [];
    this._cursor = 0;
    this._syncSelection();
    this._emit('select', { target: null, selection: this.selection });
    return this;
  }

  // --- keyboard ------------------------------------------------------------

  /**
   * Move the keyboard cursor through the selection.
   * @param {number} delta -1 for the previous target, +1 for the next one
   * @returns {{type: string, id: string}|null} the target under the cursor
   */
  moveCursor(delta) {
    if (this._selection.length === 0) {
      this._cursor = 0;
      return null;
    }
    const step = Number.isFinite(delta) ? Math.sign(delta) : 0;
    const next = this._cursor + step;
    this._cursor = Math.max(0, Math.min(this._selection.length - 1, next));
    return this.cursorTarget();
  }

  /** @returns {{type: string, id: string}|null} the target under the cursor. */
  cursorTarget() {
    if (this._selection.length === 0) return null;
    const item = this._selection[Math.min(this._cursor, this._selection.length - 1)];
    return item ? { ...item } : null;
  }

  /** Select the target under the keyboard cursor. */
  activateCursor() {
    const target = this.cursorTarget();
    if (!target) return null;
    return this.click(target);
  }

  // --- focus ---------------------------------------------------------------

  /**
   * Move the focus to a target.
   * @param {{type: 'node'|'edge', id: string}} target
   * @param {{context?: unknown}} [options] the opaque context of the focus engine
   */
  focus(target, options = {}) {
    const normalized = normalizeTarget(target);
    if (!normalized) {
      throw new GemArcError('GEMARC_GRAPH_006', '聚焦目标无效');
    }
    if (this.store && !this._exists(normalized)) return null;
    const context = options.context === undefined ? { ...normalized } : options.context;
    this._focus = { ...normalized, context };
    if (this.state) this.state.focusContext = context;
    this._emit('focus', { target: { ...normalized }, context });
    return { ...normalized };
  }

  /** Clear the focus. */
  clearFocus() {
    this._focus = null;
    if (this.state) this.state.focusContext = null;
    this._emit('focus', { target: null, context: null });
    return this;
  }

  // --- path ----------------------------------------------------------------

  /**
   * Set the path under inspection.
   * @param {string[]} nodeIds the ordered node ids of the path
   * @param {string[]} edgeIds the edge ids between them
   */
  setPath(nodeIds = [], edgeIds = []) {
    this._path = { nodeIds: [...nodeIds], edgeIds: [...edgeIds] };
    if (this.state) this.state.activePath = this.activePath;
    this._emit('path', { path: this.activePath });
    return this;
  }

  /** Clear the path under inspection. */
  clearPath() {
    return this.setPath([], []);
  }

  // --- internals -----------------------------------------------------------

  _exists(target) {
    if (!this.store) return true;
    return target.type === 'edge'
      ? this.store.hasEdge(target.id)
      : this.store.hasNode(target.id);
  }

  _syncSelection() {
    if (!this.state) return;
    const selection = this.selection;
    this.state.selectedNodeIds = selection.nodeIds;
    this.state.selectedEdgeIds = selection.edgeIds;
  }

  _emit(type, payload) {
    const event = { type, ...payload };
    const handlers = this._handlers.get(type);
    if (handlers) {
      for (const handler of [...handlers]) handler(event);
    }
    const wildcard = this._handlers.get('*');
    if (wildcard) {
      for (const handler of [...wildcard]) handler(event);
    }
  }
}
