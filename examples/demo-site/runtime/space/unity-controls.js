/**
 * Unity-consistent navigation and selection for the three-dimensional scenes.
 *
 * GemArc draws a model in three dimensions. A reader who has worked in Unity
 * must not have to learn a second set of gestures: the same mouse buttons
 * must move the same way, and the same keys must select the same way. So the
 * gestures live here, in one module, and both renderers - the semantic-axis
 * scene and the engineering-space scene - use it. The two scenes cannot drift
 * apart, because there is only one implementation of the mapping.
 *
 * The mapping follows the Unity 2022.3 scene view:
 *
 *   Alt + left drag        orbit the pivot
 *   middle drag            pan the pivot
 *   right drag             fly the camera (W/A/S/D and Q/E)
 *   wheel                  dolly towards or away from the pivot
 *   left click             select the thing under the pointer
 *   Ctrl + left click      add the thing to the selection, or take it out
 *   left drag on empty     marquee: select everything inside the rectangle
 *   left click on empty    clear the selection
 *   Esc                    clear the selection
 *   F                      frame the selection
 *
 * Hard rules this module obeys:
 *
 *   1. **No third-party import.** The three namespace is injected by the
 *      caller. The module imports nothing at all, so it can travel to a
 *      profile that ships a single file.
 *   2. **The selection lives outside.** The controller never keeps a
 *      selection of its own: it reports what the reader did, and the host
 *      decides what is selected. Two scenes then share one selection.
 *   3. **A press is not a drag.** A press that moves less than the threshold
 *      is a click; a press that moves more is a drag. The movement alone
 *      decides, never the button: this is what keeps a click working while
 *      the camera sits under the pointer.
 *   4. **No silent gesture.** Every gesture is reported through `onGesture`,
 *      so the shell can tell the reader what the pointer is doing.
 *
 * @module unity-controls
 */

/**
 * The gesture table. It is the single source of truth: the style policy
 * (BD-010) quotes the same rows, and a test compares the two.
 * @type {ReadonlyArray<Readonly<{id: string, action: string, shortcut: string,
 *   effect: string, unity: string}>>}
 */
export const UNITY_CONTROL_MAP = Object.freeze([
  Object.freeze({
    id: 'orbit',
    action: '环绕',
    shortcut: 'Alt + 左键拖动',
    effect: '绕枢轴旋转视角',
    unity: 'Alt + Left Mouse Drag',
  }),
  Object.freeze({
    id: 'pan',
    action: '平移',
    shortcut: '中键拖动',
    effect: '枢轴随视线平行移动',
    unity: 'Middle Mouse Drag',
  }),
  Object.freeze({
    id: 'fly',
    action: '飞行',
    shortcut: '右键拖动',
    effect: '相机自由飞行，用 W/A/S/D 与 Q/E 移动',
    unity: 'Right Mouse Drag',
  }),
  Object.freeze({
    id: 'dolly',
    action: '推拉',
    shortcut: '滚轮',
    effect: '镜头沿视线推近或拉远',
    unity: 'Mouse Wheel',
  }),
  Object.freeze({
    id: 'select',
    action: '选择',
    shortcut: '左键点击',
    effect: '选中指针下的实体或关系',
    unity: 'Left Mouse Click',
  }),
  Object.freeze({
    id: 'add',
    action: '加选',
    shortcut: 'Ctrl + 左键点击',
    effect: '把目标加入选择，或从选择中移除',
    unity: 'Ctrl + Left Mouse Click',
  }),
  Object.freeze({
    id: 'marquee',
    action: '框选',
    shortcut: '左键在空白处拖动',
    effect: '选中矩形内的全部目标',
    unity: 'Left Mouse Drag on empty space',
  }),
  Object.freeze({
    id: 'clear',
    action: '清空',
    shortcut: '左键点击空白处或 Esc',
    effect: '清空当前选择',
    unity: 'Left Mouse Click on empty space / Esc',
  }),
  Object.freeze({
    id: 'frame',
    action: '聚焦',
    shortcut: 'F',
    effect: '把视角对准当前选择',
    unity: 'F (Frame Selected)',
  }),
]);

/**
 * The numbers the gestures use. They are fixed so that the same gesture
 * always moves the scene by the same amount.
 * @type {Readonly<Record<string, number>>}
 */
export const UNITY_CONTROL_CONSTANTS = Object.freeze({
  /** A press that moves less than this many pixels is a click, not a drag. */
  dragThresholdPx: 5,
  /** Radians of rotation per pixel of drag. */
  orbitSensitivity: 0.006,
  /** Pivot movement per pixel of drag, relative to the distance. */
  panSensitivity: 0.0016,
  /** Camera movement per pixel of drag, relative to the distance. */
  flySensitivity: 0.0016,
  /** World units per second of a held fly key, relative to the distance. */
  flySpeed: 0.6,
  /** The factor of the wheel notches. */
  dollySensitivity: 0.0012,
  /** The scene fills the frame without touching its edges. */
  framePadding: 1.85,
  /** The nearest and farthest the camera may come. */
  minDistance: 4,
  maxDistance: 4000,
});

/**
 * The keys that fly the camera, and the direction each one moves it.
 * @type {Readonly<Record<string, string>>}
 */
export const UNITY_FLY_KEYS = Object.freeze({
  w: 'forward',
  s: 'backward',
  a: 'left',
  d: 'right',
  q: 'down',
  e: 'up',
});

/**
 * What a press means, before anything is known about the pointer.
 *
 * The function is pure, so a test can check the mapping without a browser.
 * @param {object} event the pointer event
 * @returns {{mode: string, additive: boolean}} the gesture
 */
export function resolveUnityGesture(event) {
  const button = event && typeof event.button === 'number' ? event.button : 0;
  const alt = Boolean(event && event.altKey);
  const ctrl = Boolean(event && (event.ctrlKey || event.metaKey));
  if (button === 1) return { mode: 'pan', additive: false };
  if (button === 2) return { mode: 'fly', additive: false };
  if (alt) return { mode: 'orbit', additive: false };
  return { mode: 'select', additive: ctrl };
}

/**
 * The bounds of a set of points: the centre and the radius that enclose
 * them. The controller frames a selection with it.
 * @param {Array<{x: number, y: number, z: number}>} points the points
 * @returns {{center: {x: number, y: number, z: number}, radius: number}|null}
 */
export function boundsOfPoints(points) {
  if (!Array.isArray(points) || !points.length) return null;
  const min = { x: Infinity, y: Infinity, z: Infinity };
  const max = { x: -Infinity, y: -Infinity, z: -Infinity };
  for (const point of points) {
    if (!point) continue;
    if (point.x < min.x) min.x = point.x;
    if (point.y < min.y) min.y = point.y;
    if (point.z < min.z) min.z = point.z;
    if (point.x > max.x) max.x = point.x;
    if (point.y > max.y) max.y = point.y;
    if (point.z > max.z) max.z = point.z;
  }
  const center = {
    x: (min.x + max.x) / 2,
    y: (min.y + max.y) / 2,
    z: (min.z + max.z) / 2,
  };
  const radius = Math.max(
    1,
    Math.hypot(max.x - min.x, max.y - min.y, max.z - min.z) / 2,
  );
  return { center, radius };
}

/**
 * The navigation and selection of one three-dimensional scene.
 *
 * The controller owns the camera and the gesture reading. It owns no
 * selection: it reports what the reader did and lets the host decide.
 */
export class UnityControls {
  /**
   * @param {object} camera the camera
   * @param {object} element the element that receives the pointer events
   * @param {object} [options] the hooks and the numbers
   */
  constructor(camera, element, options = {}) {
    const constants = { ...UNITY_CONTROL_CONSTANTS, ...(options.constants || {}) };
    this.camera = camera;
    this.element = element;
    this.constants = constants;
    this.minDistance = options.minDistance ?? constants.minDistance;
    this.maxDistance = options.maxDistance ?? constants.maxDistance;
    this.target = { x: 0, y: 0, z: 0 };
    this.distance = options.distance ?? 120;
    this.theta = options.theta ?? Math.PI * 0.25;
    this.phi = options.phi ?? Math.PI * 0.32;
    /** @type {object|null} the press in progress */
    this.dragging = null;
    /** @type {number} how far the press in progress moved */
    this.moved = 0;
    /** @type {Set<string>} the fly keys that are held */
    this.keys = new Set();
    /** @type {object|null} the rectangle of the marquee in progress */
    this.marquee = null;
    /** @type {string|null} the gesture of the press in progress */
    this.gesture = null;
    /** @type {string|null} the last gesture the controller recognised */
    this.lastGesture = null;
    this.listeners = [];
    this.pick = options.pick || null;
    this.pickInRect = options.pickInRect || null;
    this.onSelect = options.onSelect || null;
    this.onClear = options.onClear || null;
    this.onGesture = options.onGesture || null;
    this.onFrame = options.onFrame || null;
    this.getBounds = options.getBounds || null;
    this.getSelectionBounds = options.getSelectionBounds || null;
    this._marqueeElement = null;
    this._bind();
  }

  /* ------------------------------------------------------------- binding */

  _bind() {
    const element = this.element;
    if (!element || typeof element.addEventListener !== 'function') return;
    const on = (type, handler, options) => {
      element.addEventListener(type, handler, options);
      this.listeners.push([type, handler, options]);
    };
    on('pointerdown', (event) => this._onDown(event));
    on('pointermove', (event) => this._onMove(event));
    on('pointerup', (event) => this._onUp(event));
    on('pointercancel', () => this._endDrag());
    on('wheel', (event) => this._onWheel(event), { passive: false });
    on('contextmenu', (event) => event.preventDefault());
    on('keydown', (event) => this._onKeyDown(event));
    on('keyup', (event) => this._onKeyUp(event));
    on('blur', () => this.keys.clear());
    // The scene has to be focusable, otherwise the keyboard gestures never
    // reach it: a canvas is not focusable by default.
    if (typeof element.tabIndex !== 'number' || element.tabIndex < 0) {
      element.tabIndex = 0;
    }
  }

  /**
   * Report a gesture to the host.
   * @param {string} mode the gesture
   */
  _reportGesture(mode) {
    this.lastGesture = mode;
    if (typeof this.onGesture === 'function') this.onGesture(mode);
  }

  /* ------------------------------------------------------------- pointers */

  _onDown(event) {
    if (!event || typeof event.button !== 'number') return;
    // The middle button would otherwise start the autoscroll of the page, and
    // the right button would raise the context menu over the scene.
    if ((event.button === 1 || event.button === 2)
        && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    const gesture = resolveUnityGesture(event);
    // A left press on empty space may become a marquee; a left press on a
    // target may only become a click. Unity draws the difference here.
    const hit = gesture.mode === 'select' && this.pick ? this.pick(event) : null;
    this.dragging = {
      mode: gesture.mode,
      additive: gesture.additive,
      x: event.clientX ?? 0,
      y: event.clientY ?? 0,
      startX: event.clientX ?? 0,
      startY: event.clientY ?? 0,
      moved: 0,
      onEmpty: gesture.mode === 'select' && !hit,
      suppressed: false,
    };
    this.moved = 0;
    this.gesture = gesture;
    this._reportGesture(gesture.mode);
    if (typeof this.element.setPointerCapture === 'function' && event.pointerId !== undefined) {
      try {
        this.element.setPointerCapture(event.pointerId);
      } catch {
        // A stub element may not support pointer capture.
      }
    }
  }

  _onMove(event) {
    if (!this.dragging) return;
    const dx = (event.clientX ?? 0) - this.dragging.x;
    const dy = (event.clientY ?? 0) - this.dragging.y;
    this.dragging.x = event.clientX ?? 0;
    this.dragging.y = event.clientY ?? 0;
    this.dragging.moved += Math.abs(dx) + Math.abs(dy);
    this.moved = this.dragging.moved;
    const mode = this.dragging.mode;
    if (mode === 'orbit') {
      this.theta -= dx * this.constants.orbitSensitivity;
      this.phi = Math.max(0.05, Math.min(Math.PI - 0.05, this.phi - dy * this.constants.orbitSensitivity));
    } else if (mode === 'pan') {
      this._pan(dx, dy);
    } else if (mode === 'fly') {
      this._fly(dx, dy);
    } else if (mode === 'select') {
      if (this.dragging.moved >= this.constants.dragThresholdPx) {
        if (this.dragging.onEmpty) {
          // Unity turns a drag that started on empty space into a selection
          // rectangle. A drag that started on a target does nothing.
          this.dragging.mode = 'marquee';
          this._updateMarquee(event);
        } else {
          this.dragging.suppressed = true;
        }
      }
    }
    this.apply();
  }

  _onUp(event) {
    const drag = this.dragging;
    if (!drag) return;
    this._endDrag();
    if (drag.mode === 'marquee') {
      this._applyMarquee(drag);
      return;
    }
    if (drag.mode !== 'select') return;
    // A press that travelled is a drag, not a click. The click is dropped
    // rather than fired at whatever happens to sit under the pointer now.
    if (drag.suppressed || drag.moved >= this.constants.dragThresholdPx) return;
    const hit = this.pick ? this.pick(event) : null;
    if (!hit) {
      if (this.onClear) this.onClear();
      return;
    }
    if (this.onSelect) this.onSelect(hit, { mode: drag.additive ? 'toggle' : 'replace' });
  }

  _onWheel(event) {
    if (!event) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    const delta = event.deltaY ?? 0;
    const factor = Math.exp(delta * this.constants.dollySensitivity);
    this.distance = Math.max(
      this.minDistance,
      Math.min(this.maxDistance, this.distance * factor),
    );
    this.apply();
    this._reportGesture('dolly');
  }

  _endDrag() {
    this.dragging = null;
    this.gesture = null;
    this._hideMarquee();
  }

  /* ------------------------------------------------------------- keyboard */

  _onKeyDown(event) {
    if (!event) return;
    const key = typeof event.key === 'string' ? event.key.toLowerCase() : '';
    if (key === 'f') {
      if (this.frameSelection() && typeof event.preventDefault === 'function') {
        event.preventDefault();
      }
      return;
    }
    if (key === 'escape') {
      if (this.onClear) this.onClear();
      if (typeof event.preventDefault === 'function') event.preventDefault();
      return;
    }
    if (Object.prototype.hasOwnProperty.call(UNITY_FLY_KEYS, key)) {
      this.keys.add(key);
      if (typeof event.preventDefault === 'function') event.preventDefault();
    }
  }

  _onKeyUp(event) {
    if (!event) return;
    const key = typeof event.key === 'string' ? event.key.toLowerCase() : '';
    this.keys.delete(key);
  }

  /* ------------------------------------------------------------- movement */

  /**
   * The unit vector the camera looks along.
   * @returns {{x: number, y: number, z: number}}
   */
  forwardVector() {
    const sinPhi = Math.sin(this.phi);
    return {
      x: -sinPhi * Math.sin(this.theta),
      y: -Math.cos(this.phi),
      z: -sinPhi * Math.cos(this.theta),
    };
  }

  /**
   * The unit vector to the right of the camera. This is the convention three
   * uses: the right vector is the forward vector crossed with the world up.
   * @returns {{x: number, y: number, z: number}}
   */
  rightVector() {
    const forward = this.forwardVector();
    const length = Math.hypot(forward.x, forward.z) || 1;
    return { x: -forward.z / length, y: 0, z: forward.x / length };
  }

  /**
   * The unit vector above the camera.
   * @returns {{x: number, y: number, z: number}}
   */
  upVector() {
    const right = this.rightVector();
    const forward = this.forwardVector();
    return {
      x: right.y * forward.z - right.z * forward.y,
      y: right.z * forward.x - right.x * forward.z,
      z: right.x * forward.y - right.y * forward.x,
    };
  }

  /**
   * Move the pivot in the plane of the screen.
   * @param {number} dx the horizontal movement in pixels
   * @param {number} dy the vertical movement in pixels
   */
  _pan(dx, dy) {
    const scale = this.distance * this.constants.panSensitivity;
    const right = this.rightVector();
    const up = this.upVector();
    this.target.x += (-right.x * dx + up.x * dy) * scale;
    this.target.y += (-right.y * dx + up.y * dy) * scale;
    this.target.z += (-right.z * dx + up.z * dy) * scale;
  }

  /**
   * Move the camera along its own axes. The pivot travels with it, so the
   * direction of the view does not change: this is Unity's fly.
   * @param {number} dx the horizontal movement in pixels
   * @param {number} dy the vertical movement in pixels
   */
  _fly(dx, dy) {
    const scale = this.distance * this.constants.flySensitivity;
    const forward = this.forwardVector();
    const right = this.rightVector();
    const along = -dy * scale;
    const side = dx * scale;
    this.target.x += forward.x * along + right.x * side;
    this.target.y += forward.y * along + right.y * side;
    this.target.z += forward.z * along + right.z * side;
  }

  /**
   * Advance the fly keys. The renderer calls this once per frame; a scene
   * that is not being flown does not move.
   * @param {number} [seconds] the time since the last frame
   * @returns {boolean} true when the camera moved
   */
  tick(seconds = 0.016) {
    if (!this.dragging || this.dragging.mode !== 'fly' || !this.keys.size) return false;
    const speed = this.distance * this.constants.flySpeed * seconds;
    const forward = this.forwardVector();
    const right = this.rightVector();
    let move = { x: 0, y: 0, z: 0 };
    for (const key of this.keys) {
      const direction = UNITY_FLY_KEYS[key];
      if (direction === 'forward') move = add(move, forward, speed);
      else if (direction === 'backward') move = add(move, forward, -speed);
      else if (direction === 'right') move = add(move, right, speed);
      else if (direction === 'left') move = add(move, right, -speed);
      else if (direction === 'up') move = { x: move.x, y: move.y + speed, z: move.z };
      else if (direction === 'down') move = { x: move.x, y: move.y - speed, z: move.z };
    }
    this.target.x += move.x;
    this.target.y += move.y;
    this.target.z += move.z;
    this.apply();
    return true;
  }

  /* -------------------------------------------------------------- marquee */

  /**
   * The rectangle of the marquee in progress, in client coordinates.
   * @param {object} event the pointer event
   */
  _updateMarquee(event) {
    const startX = this.dragging.startX;
    const startY = this.dragging.startY;
    const x = event.clientX ?? 0;
    const y = event.clientY ?? 0;
    this.marquee = {
      left: Math.min(startX, x),
      top: Math.min(startY, y),
      right: Math.max(startX, x),
      bottom: Math.max(startY, y),
      width: Math.abs(x - startX),
      height: Math.abs(y - startY),
    };
    this._showMarquee(this.marquee);
  }

  /**
   * Select everything inside the rectangle.
   * @param {object} drag the press that ended
   */
  _applyMarquee(drag) {
    const rect = this.marquee;
    this.marquee = null;
    this._hideMarquee();
    if (!rect) return;
    const hits = this.pickInRect ? (this.pickInRect(rect) || []) : [];
    if (!drag.additive && this.onClear) this.onClear();
    if (!hits.length) return;
    for (const hit of hits) {
      if (this.onSelect) this.onSelect(hit, { mode: 'add' });
    }
  }

  /**
   * Draw the selection rectangle. The rectangle is part of the scene, so it
   * is drawn over the canvas; a scene that cannot draw it still selects.
   * @param {object} rect the rectangle in client coordinates
   */
  _showMarquee(rect) {
    const element = this.element;
    if (!element || !element.parentNode) return;
    const doc = element.ownerDocument
      || (typeof document !== 'undefined' ? document : null);
    if (!doc || typeof doc.createElement !== 'function') return;
    if (!this._marqueeElement) {
      const box = doc.createElement('div');
      // A host that cannot draw the rectangle still selects: the rectangle is
      // a picture of the gesture, not the gesture itself.
      if (!box || !box.style) return;
      box.className = 'gemarc-marquee';
      box.style.position = 'absolute';
      box.style.pointerEvents = 'none';
      box.style.border = '1px solid var(--gem-ai, #3d5a72)';
      box.style.background = 'rgba(61, 90, 114, 0.08)';
      box.style.zIndex = '2';
      element.parentNode.appendChild(box);
      this._marqueeElement = box;
      if (element.parentNode.style
          && element.parentNode.style.position !== 'relative'
          && element.parentNode.style.position !== 'absolute'
          && element.parentNode.style.position !== 'fixed') {
        element.parentNode.style.position = 'relative';
      }
    }
    const parent = element.parentNode;
    const base = parent.getBoundingClientRect
      ? parent.getBoundingClientRect()
      : { left: 0, top: 0 };
    this._marqueeElement.style.left = `${rect.left - base.left}px`;
    this._marqueeElement.style.top = `${rect.top - base.top}px`;
    this._marqueeElement.style.width = `${rect.width}px`;
    this._marqueeElement.style.height = `${rect.height}px`;
  }

  _hideMarquee() {
    if (this._marqueeElement && this._marqueeElement.parentNode) {
      this._marqueeElement.parentNode.removeChild(this._marqueeElement);
    }
    this._marqueeElement = null;
  }

  /* ---------------------------------------------------------------- camera */

  /**
   * Point the camera at the centre of the scene from a readable angle.
   * @param {{center: object, radius: number}} bounds the bounds of the scene
   */
  frame(bounds) {
    if (!bounds || !bounds.center) return;
    this.target = { ...bounds.center };
    // The scene has to fill the frame without touching its edges: twice the
    // radius would leave the crystals in the middle of a dark room.
    this.distance = Math.max(this.minDistance, bounds.radius * this.constants.framePadding);
    this.apply();
  }

  /**
   * Frame the selection, or the whole scene when nothing is selected. This
   * is Unity's F.
   * @returns {boolean} true when the camera moved
   */
  frameSelection() {
    const selected = this.getSelectionBounds ? this.getSelectionBounds() : null;
    const bounds = selected || (this.getBounds ? this.getBounds() : null);
    if (!bounds) return false;
    this.frame(bounds);
    if (this.onFrame) this.onFrame(bounds);
    return true;
  }

  /**
   * Move the camera to an explicit position and target.
   * @param {{position: object, target: object}} placement
   */
  set(placement) {
    const target = placement.target;
    const position = placement.position;
    this.target = { x: target.x, y: target.y, z: target.z };
    const dx = position.x - target.x;
    const dy = position.y - target.y;
    const dz = position.z - target.z;
    this.distance = Math.max(this.minDistance, Math.sqrt(dx * dx + dy * dy + dz * dz));
    if (this.distance > 0) {
      this.theta = Math.atan2(dx, dz);
      this.phi = Math.max(0.05, Math.min(Math.PI - 0.05, Math.acos(dy / this.distance)));
    }
    this.apply();
  }

  apply() {
    const sinPhi = Math.sin(this.phi);
    this.camera.position.set(
      this.target.x + this.distance * sinPhi * Math.sin(this.theta),
      this.target.y + this.distance * Math.cos(this.phi),
      this.target.z + this.distance * sinPhi * Math.cos(this.theta),
    );
    this.camera.lookAt(this.target.x, this.target.y, this.target.z);
    if (typeof this.camera.updateProjectionMatrix === 'function') {
      this.camera.updateProjectionMatrix();
    }
    if (typeof this.camera.updateMatrixWorld === 'function') {
      this.camera.updateMatrixWorld();
    }
  }

  dispose() {
    for (const [type, handler, options] of this.listeners) {
      if (this.element && typeof this.element.removeEventListener === 'function') {
        this.element.removeEventListener(type, handler, options);
      }
    }
    this.listeners = [];
    this._hideMarquee();
    this.keys.clear();
  }
}

/**
 * Add a scaled vector to another.
 * @param {{x: number, y: number, z: number}} base the vector
 * @param {{x: number, y: number, z: number}} direction the direction
 * @param {number} scale how far
 * @returns {{x: number, y: number, z: number}} the sum
 */
function add(base, direction, scale) {
  return {
    x: base.x + direction.x * scale,
    y: base.y + direction.y * scale,
    z: base.z + direction.z * scale,
  };
}
