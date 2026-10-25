/**
 * CanvasController - interaction layer of the infinite workflow canvas.
 *
 * What this file covers:
 *   - pan, zoom (anchored and fit-to-content), node dragging, selection and
 *     edge routing, all expressed in canvas coordinates
 *   - a coordinate bridge between screen space and world space
 *
 * What it cannot express - and must never do:
 *   - it MUST NOT change execution semantics. The controller writes into
 *     `canvas` fields only (node position / size, edge points). Ports,
 *     constraints, conditions, edge kinds and node kinds are read-only for
 *     it. Moving a node on the canvas cannot change what the workflow means.
 *
 * The controller is framework agnostic: it never touches the DOM. Rendering
 * is delegated to injected callbacks (`onTransform`, `onNodeMove`,
 * `onEdgeRoute`, `onSelectionChange`, `onChange`), so the whole interaction
 * model is testable in plain Node.
 */
import { GemArcError } from '../core/errors.js';
import { WorkflowModel } from './workflow-model.js';

const DEFAULT_MIN_SCALE = 0.1;
const DEFAULT_MAX_SCALE = 8;
const DEFAULT_PADDING = 48;
const DEFAULT_NODE_WIDTH = 160;
const DEFAULT_NODE_HEIGHT = 72;

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function finite(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
}

/**
 * CanvasController - pan / zoom / drag / select on top of a workflow model.
 */
export class CanvasController {
  /**
   * @param {import('./workflow-model.js').WorkflowModel|object} model
   * @param {{
   *   onTransform?: (transform: {x: number, y: number, scale: number}) => void,
   *   onNodeMove?: (payload: {nodeId: string, canvas: object}) => void,
   *   onEdgeRoute?: (payload: {edgeId: string, points: object[]}) => void,
   *   onSelectionChange?: (nodeIds: string[]) => void,
   *   onChange?: (record: object) => void,
   *   minScale?: number, maxScale?: number
   * }} [options]
   */
  constructor(model, options = {}) {
    /** @type {WorkflowModel} */
    this.model = model instanceof WorkflowModel ? model : new WorkflowModel(model);
    this.onTransform = options.onTransform || null;
    this.onNodeMove = options.onNodeMove || null;
    this.onEdgeRoute = options.onEdgeRoute || null;
    this.onSelectionChange = options.onSelectionChange || null;
    this.onChange = options.onChange || null;
    this.minScale = options.minScale ?? DEFAULT_MIN_SCALE;
    this.maxScale = options.maxScale ?? DEFAULT_MAX_SCALE;
    /** @type {{x: number, y: number, scale: number}} */
    this.transform = { x: 0, y: 0, scale: 1 };
    /** @type {Set<string>} */
    this.selected = new Set();
    /** @type {{nodeId: string, originX: number, originY: number, pointer: object}|null} */
    this.drag = null;
  }

  /** Current pan / zoom transform (a copy; the controller owns the state). */
  getTransform() {
    return { ...this.transform };
  }

  /**
   * Replace the transform wholesale. The scale is clamped to the allowed range.
   * @param {{x?: number, y?: number, scale?: number}} next
   */
  setTransform(next = {}) {
    const scale = clamp(
      finite(next.scale, this.transform.scale),
      this.minScale,
      this.maxScale,
    );
    this.transform = {
      x: finite(next.x, this.transform.x),
      y: finite(next.y, this.transform.y),
      scale,
    };
    this._emitTransform();
    return this.getTransform();
  }

  /**
   * Pan the viewport by a screen space delta.
   * @param {number} dx
   * @param {number} dy
   */
  pan(dx, dy) {
    this.transform = {
      ...this.transform,
      x: this.transform.x + finite(dx, 0),
      y: this.transform.y + finite(dy, 0),
    };
    this._emitTransform();
    return this.getTransform();
  }

  /**
   * Zoom to an absolute scale, keeping the world point under `anchor` fixed.
   * @param {number} scale
   * @param {{x: number, y: number}|null} [anchor] screen space anchor
   */
  zoomTo(scale, anchor = null) {
    const next = clamp(finite(scale, this.transform.scale), this.minScale, this.maxScale);
    this._zoomAround(next, anchor);
    return this.getTransform();
  }

  /**
   * Zoom by a factor, keeping the world point under `anchor` fixed.
   * @param {number} factor
   * @param {{x: number, y: number}|null} [anchor] screen space anchor
   */
  zoomBy(factor, anchor = null) {
    const next = clamp(
      this.transform.scale * finite(factor, 1),
      this.minScale,
      this.maxScale,
    );
    this._zoomAround(next, anchor);
    return this.getTransform();
  }

  /**
   * Zoom and pan so that every node with canvas data is visible.
   * @param {{padding?: number}} [options]
   */
  zoomToFit(options = {}) {
    const padding = finite(options.padding, DEFAULT_PADDING);
    const bounds = this.contentBounds();
    if (!bounds) {
      this.transform = { x: 0, y: 0, scale: 1 };
      this._emitTransform();
      return this.getTransform();
    }
    const width = Math.max(bounds.width, 1);
    const height = Math.max(bounds.height, 1);
    const scale = clamp(
      Math.min((width + padding * 2) / width, (height + padding * 2) / height),
      this.minScale,
      this.maxScale,
    );
    this.transform = {
      scale,
      x: padding - bounds.x * scale,
      y: padding - bounds.y * scale,
    };
    this._emitTransform();
    return this.getTransform();
  }

  /**
   * Convert a screen point into world coordinates.
   * @param {{x: number, y: number}} point
   */
  screenToWorld(point) {
    const source = point || { x: 0, y: 0 };
    const scale = this.transform.scale || 1;
    return {
      x: (finite(source.x, 0) - this.transform.x) / scale,
      y: (finite(source.y, 0) - this.transform.y) / scale,
    };
  }

  /**
   * Convert a world point into screen coordinates.
   * @param {{x: number, y: number}} point
   */
  worldToScreen(point) {
    const source = point || { x: 0, y: 0 };
    const scale = this.transform.scale || 1;
    return {
      x: finite(source.x, 0) * scale + this.transform.x,
      y: finite(source.y, 0) * scale + this.transform.y,
    };
  }

  /**
   * Move a node to an absolute world position.
   * @param {string} nodeId
   * @param {{x: number, y: number}} position
   */
  moveNode(nodeId, position) {
    const node = this._requireNode(nodeId);
    const canvas = { ...(node.canvas || {}) };
    canvas.x = finite(position?.x, canvas.x ?? 0);
    canvas.y = finite(position?.y, canvas.y ?? 0);
    node.canvas = canvas;
    this._emitNodeMove(nodeId, canvas);
    return { ...canvas };
  }

  /**
   * Move a node by a world space delta.
   * @param {string} nodeId
   * @param {number} dx
   * @param {number} dy
   */
  moveNodeBy(nodeId, dx, dy) {
    const node = this._requireNode(nodeId);
    const canvas = { ...(node.canvas || {}) };
    canvas.x = finite(canvas.x, 0) + finite(dx, 0);
    canvas.y = finite(canvas.y, 0) + finite(dy, 0);
    node.canvas = canvas;
    this._emitNodeMove(nodeId, canvas);
    return { ...canvas };
  }

  /**
   * Resize a node. Size is presentation data like everything else in canvas.
   * @param {string} nodeId
   * @param {{width?: number, height?: number}} size
   */
  setNodeSize(nodeId, size) {
    const node = this._requireNode(nodeId);
    const canvas = { ...(node.canvas || {}) };
    if (Number.isFinite(size?.width)) canvas.width = size.width;
    if (Number.isFinite(size?.height)) canvas.height = size.height;
    node.canvas = canvas;
    this._emitNodeMove(nodeId, canvas);
    return { ...canvas };
  }

  /**
   * Start a node drag. The pointer position is given in screen coordinates.
   * @param {string} nodeId
   * @param {{x: number, y: number}} pointer
   */
  beginDrag(nodeId, pointer) {
    const node = this._requireNode(nodeId);
    const canvas = node.canvas || {};
    this.drag = {
      nodeId,
      originX: finite(canvas.x, 0),
      originY: finite(canvas.y, 0),
      pointer: { x: finite(pointer?.x, 0), y: finite(pointer?.y, 0) },
    };
    return this.drag;
  }

  /**
   * Continue a node drag. The pointer position is given in screen coordinates.
   * @param {{x: number, y: number}} pointer
   */
  dragTo(pointer) {
    if (!this.drag) return null;
    const world = this.screenToWorld(pointer);
    const startWorld = this.screenToWorld(this.drag.pointer);
    const dx = world.x - startWorld.x;
    const dy = world.y - startWorld.y;
    return this.moveNode(this.drag.nodeId, { x: this.drag.originX + dx, y: this.drag.originY + dy });
  }

  /** Finish the current node drag. */
  endDrag() {
    this.drag = null;
    return true;
  }

  /**
   * Replace the routing points of an edge.
   * @param {string} edgeId
   * @param {{x: number, y: number}[]} points
   */
  setEdgePoints(edgeId, points) {
    const edge = this._requireEdge(edgeId);
    const list = (Array.isArray(points) ? points : []).map((point) => ({
      x: finite(point?.x, 0),
      y: finite(point?.y, 0),
    }));
    edge.canvas = { ...(edge.canvas || {}), points: list };
    this._emitEdgeRoute(edgeId, list);
    return list;
  }

  /**
   * Compute an orthogonal route between the two endpoints of an edge and
   * store it as canvas points.
   * @param {string} edgeId
   */
  routeEdge(edgeId) {
    const edge = this._requireEdge(edgeId);
    const source = this.model.node(edge.sourceId);
    const target = this.model.node(edge.targetId);
    const from = this.nodeRect(source);
    const to = this.nodeRect(target);
    const horizontal = to.x >= from.x + from.width;
    const points = horizontal
      ? [
          { x: from.x + from.width, y: from.y + from.height / 2 },
          { x: to.x, y: to.y + to.height / 2 },
        ]
      : [
          { x: from.x + from.width / 2, y: from.y + from.height },
          { x: to.x + to.width / 2, y: to.y },
        ];
    return this.setEdgePoints(edgeId, points);
  }

  /**
   * Select nodes. The selection is a presentation concern only.
   * @param {string[]|string} ids
   */
  select(ids) {
    const list = Array.isArray(ids) ? ids : ids ? [ids] : [];
    this.selected = new Set(list.filter((id) => typeof id === 'string' && id.length > 0));
    this._emitSelection();
    return this.getSelection();
  }

  /**
   * Add or remove one node from the selection.
   * @param {string} id
   */
  toggleSelection(id) {
    if (this.selected.has(id)) this.selected.delete(id);
    else this.selected.add(id);
    this._emitSelection();
    return this.getSelection();
  }

  /** Clear the selection. */
  clearSelection() {
    this.selected = new Set();
    this._emitSelection();
    return this.getSelection();
  }

  /** The selected node ids. */
  getSelection() {
    return [...this.selected];
  }

  /**
   * The bounding box of every node that carries canvas data.
   * @returns {{x: number, y: number, width: number, height: number}|null}
   */
  contentBounds() {
    const rects = this.model.nodeList
      .filter((node) => node.canvas && Number.isFinite(node.canvas.x) && Number.isFinite(node.canvas.y))
      .map((node) => this.nodeRect(node));
    if (!rects.length) return null;
    const minX = Math.min(...rects.map((rect) => rect.x));
    const minY = Math.min(...rects.map((rect) => rect.y));
    const maxX = Math.max(...rects.map((rect) => rect.x + rect.width));
    const maxY = Math.max(...rects.map((rect) => rect.y + rect.height));
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
  }

  /**
   * The canvas rectangle of a node, falling back to a default size.
   * @param {object|null} node
   */
  nodeRect(node) {
    const canvas = (node && node.canvas) || {};
    return {
      x: finite(canvas.x, 0),
      y: finite(canvas.y, 0),
      width: finite(canvas.width, DEFAULT_NODE_WIDTH),
      height: finite(canvas.height, DEFAULT_NODE_HEIGHT),
    };
  }

  /** Reset pan, zoom, selection and drag state. */
  reset() {
    this.transform = { x: 0, y: 0, scale: 1 };
    this.selected = new Set();
    this.drag = null;
    this._emitTransform();
    this._emitSelection();
    return this.getTransform();
  }

  // --- internals -----------------------------------------------------------

  _requireNode(nodeId) {
    const node = this.model.node(nodeId);
    if (!node) throw new GemArcError('GEMARC_WORKFLOW_001', '节点不存在', String(nodeId));
    return node;
  }

  _requireEdge(edgeId) {
    const edge = this.model.edge(edgeId);
    if (!edge) throw new GemArcError('GEMARC_WORKFLOW_001', '边不存在', String(edgeId));
    return edge;
  }

  _zoomAround(scale, anchor) {
    const next = clamp(scale, this.minScale, this.maxScale);
    const point = anchor || { x: 0, y: 0 };
    const world = this.screenToWorld(point);
    this.transform = {
      scale: next,
      x: finite(point.x, 0) - world.x * next,
      y: finite(point.y, 0) - world.y * next,
    };
    this._emitTransform();
  }

  _emitTransform() {
    const transform = this.getTransform();
    if (this.onTransform) this.onTransform(transform);
    if (this.onChange) this.onChange({ type: 'transform', transform });
  }

  _emitNodeMove(nodeId, canvas) {
    const payload = { nodeId, canvas: { ...canvas } };
    if (this.onNodeMove) this.onNodeMove(payload);
    if (this.onChange) this.onChange({ type: 'node-move', ...payload });
  }

  _emitEdgeRoute(edgeId, points) {
    const payload = { edgeId, points: points.map((point) => ({ ...point })) };
    if (this.onEdgeRoute) this.onEdgeRoute(payload);
    if (this.onChange) this.onChange({ type: 'edge-route', ...payload });
  }

  _emitSelection() {
    const ids = this.getSelection();
    if (this.onSelectionChange) this.onSelectionChange(ids);
    if (this.onChange) this.onChange({ type: 'selection', nodeIds: ids });
  }
}
