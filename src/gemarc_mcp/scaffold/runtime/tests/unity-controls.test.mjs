/**
 * Regression tests of the Unity-consistent navigation and selection.
 *
 * The rule that governs this file: the gestures are Unity's, so the tests
 * check Unity's behaviour and not the implementation. A controller that
 * orbits on a plain left drag would pass a test that only checks "something
 * happened"; it must fail here.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  UNITY_CONTROL_MAP,
  UNITY_CONTROL_CONSTANTS,
  UnityControls,
  boundsOfPoints,
  resolveUnityGesture,
} from '../space/unity-controls.js';

/* ------------------------------------------------------------- harness */

/** A camera that records what the controller does to it. */
function makeCamera() {
  return {
    position: {
      x: 0, y: 0, z: 0,
      set(x, y, z) { this.x = x; this.y = y; this.z = z; },
    },
    lookAt() {},
    updateProjectionMatrix() {},
    updateMatrixWorld() {},
  };
}

/** An element that takes pointer and keyboard events. */
function createElement() {
  const listeners = new Map();
  return {
    style: {},
    tabIndex: -1,
    parentNode: null,
    ownerDocument: {
      createElement() { return { style: {}, parentNode: null }; },
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      const list = listeners.get(type) || [];
      const index = list.indexOf(handler);
      if (index >= 0) list.splice(index, 1);
    },
    dispatch(type, event = {}) {
      for (const handler of [...(listeners.get(type) || [])]) handler(event);
    },
    getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 600 }; },
    setPointerCapture() {},
  };
}

/**
 * Build a controller with a scene of two targets.
 * @param {object} [options] the hooks
 * @returns {object} the controller and the record of what it did
 */
function build(options = {}) {
  const camera = makeCamera();
  const element = createElement();
  const selected = [];
  const cleared = [];
  const gestures = [];
  const hit = options.hit || (() => null);
  const controls = new UnityControls(camera, element, {
    distance: 100,
    pick: (event) => hit(event),
    pickInRect: options.pickInRect || (() => []),
    onSelect: (target, mode) => selected.push({ target, mode: mode.mode }),
    onClear: () => cleared.push(true),
    onGesture: (mode) => gestures.push(mode),
    getBounds: () => ({ center: { x: 0, y: 0, z: 0 }, radius: 50 }),
    getSelectionBounds: options.getSelectionBounds || (() => null),
  });
  return { camera, element, controls, selected, cleared, gestures };
}

/** A press and a release at the same place. */
function click(element, x, y, extra = {}) {
  element.dispatch('pointerdown', { button: 0, clientX: x, clientY: y, pointerId: 1, ...extra });
  element.dispatch('pointerup', { button: 0, clientX: x, clientY: y, pointerId: 1, ...extra });
}

/** A press, a drag of the given length, and a release. */
function drag(element, fromX, fromY, toX, toY, extra = {}) {
  element.dispatch('pointerdown', { button: 0, clientX: fromX, clientY: fromY, pointerId: 1, ...extra });
  element.dispatch('pointermove', { clientX: toX, clientY: toY, ...extra });
  element.dispatch('pointerup', { button: 0, clientX: toX, clientY: toY, pointerId: 1, ...extra });
}

/* --------------------------------------------------------------- table */

test('the gesture table carries the nine Unity gestures, in order', () => {
  assert.deepEqual(UNITY_CONTROL_MAP.map((row) => row.id), [
    'orbit', 'pan', 'fly', 'dolly', 'select', 'add', 'marquee', 'clear', 'frame',
  ]);
});

test('every row of the table names the Unity shortcut it stands for', () => {
  for (const row of UNITY_CONTROL_MAP) {
    assert.ok(row.action, `${row.id} has no action`);
    assert.ok(row.shortcut, `${row.id} has no shortcut`);
    assert.ok(row.effect, `${row.id} has no effect`);
    assert.ok(row.unity, `${row.id} names no Unity shortcut`);
  }
});

test('the table is frozen, so a scene cannot change the mapping', () => {
  assert.throws(() => {
    UNITY_CONTROL_MAP[0].action = '别的';
  });
});

/* ------------------------------------------------------------ gestures */

test('a plain left press is a selection', () => {
  assert.deepEqual(resolveUnityGesture({ button: 0 }), { mode: 'select', additive: false });
});

test('the modifier turns the left button into a toggle', () => {
  assert.deepEqual(resolveUnityGesture({ button: 0, ctrlKey: true }),
    { mode: 'select', additive: true });
});

test('alt turns the left button into an orbit', () => {
  assert.deepEqual(resolveUnityGesture({ button: 0, altKey: true }),
    { mode: 'orbit', additive: false });
});

test('the middle button pans and the right button flies', () => {
  assert.deepEqual(resolveUnityGesture({ button: 1 }), { mode: 'pan', additive: false });
  assert.deepEqual(resolveUnityGesture({ button: 2 }), { mode: 'fly', additive: false });
});

/* ------------------------------------------------------------ selection */

test('a click on a target selects it', () => {
  const { element, selected, cleared } = build({ hit: () => ({ kind: 'node', id: 'n-a' }) });
  click(element, 100, 100);
  assert.deepEqual(selected, [{ target: { kind: 'node', id: 'n-a' }, mode: 'replace' }]);
  assert.equal(cleared.length, 0);
});

test('a click on empty space clears the selection', () => {
  const { element, selected, cleared } = build({ hit: () => null });
  click(element, 100, 100);
  assert.equal(selected.length, 0);
  assert.equal(cleared.length, 1);
});

test('a click with the modifier toggles the target', () => {
  const { element, selected } = build({ hit: () => ({ kind: 'node', id: 'n-a' }) });
  click(element, 100, 100, { ctrlKey: true });
  assert.deepEqual(selected, [{ target: { kind: 'node', id: 'n-a' }, mode: 'toggle' }]);
});

test('a drag on empty space becomes a marquee and selects what it covers', () => {
  const { element, selected, cleared } = build({
    hit: () => null,
    pickInRect: (rect) => {
      assert.ok(rect.width > 0 && rect.height > 0, 'the rectangle has a size');
      return [{ kind: 'node', id: 'n-a' }, { kind: 'node', id: 'n-b' }];
    },
  });
  drag(element, 100, 100, 200, 180);
  assert.equal(cleared.length, 1, 'the marquee replaces the old selection');
  assert.deepEqual(selected, [
    { target: { kind: 'node', id: 'n-a' }, mode: 'add' },
    { target: { kind: 'node', id: 'n-b' }, mode: 'add' },
  ]);
});

test('a drag on a target selects nothing and moves nothing', () => {
  const { element, controls, selected, cleared } = build({
    hit: () => ({ kind: 'node', id: 'n-a' }),
  });
  const before = { ...controls.target, distance: controls.distance, theta: controls.theta };
  drag(element, 100, 100, 200, 180);
  assert.equal(selected.length, 0);
  assert.equal(cleared.length, 0);
  assert.deepEqual(controls.target, { x: before.x, y: before.y, z: before.z });
  assert.equal(controls.distance, before.distance);
  assert.equal(controls.theta, before.theta);
});

test('a press that travels is a drag, not a click', () => {
  const { element, selected } = build({ hit: () => null });
  // One pixel is below the threshold: it is still a click.
  click(element, 100, 100);
  element.dispatch('pointerdown', { button: 0, clientX: 200, clientY: 200, pointerId: 1 });
  element.dispatch('pointermove', { clientX: 210, clientY: 200 });
  element.dispatch('pointerup', { button: 0, clientX: 210, clientY: 200, pointerId: 1 });
  assert.equal(selected.length, 0, 'the drag did not fire a second click');
  assert.equal(UNITY_CONTROL_CONSTANTS.dragThresholdPx, 5);
});

/* --------------------------------------------------------------- camera */

test('alt and drag orbits the pivot without touching the selection', () => {
  const { element, controls, selected } = build({ hit: () => null });
  const before = controls.theta;
  drag(element, 100, 100, 160, 100, { altKey: true });
  assert.notEqual(controls.theta, before, 'the view turned');
  assert.equal(selected.length, 0);
});

test('the middle drag pans the pivot without touching the selection', () => {
  const { element, controls, selected } = build({ hit: () => null });
  const before = { ...controls.target };
  element.dispatch('pointerdown', { button: 1, clientX: 100, clientY: 100, pointerId: 1 });
  element.dispatch('pointermove', { clientX: 160, clientY: 100 });
  element.dispatch('pointerup', { button: 1, clientX: 160, clientY: 100, pointerId: 1 });
  assert.notDeepEqual(controls.target, before, 'the pivot moved');
  assert.equal(selected.length, 0);
});

test('the right drag flies the camera along the view', () => {
  const { element, controls, selected } = build({ hit: () => null });
  const before = { ...controls.target };
  element.dispatch('pointerdown', { button: 2, clientX: 100, clientY: 100, pointerId: 1 });
  element.dispatch('pointermove', { clientX: 100, clientY: 60 });
  element.dispatch('pointerup', { button: 2, clientX: 100, clientY: 60, pointerId: 1 });
  assert.notDeepEqual(controls.target, before, 'the camera flew');
  assert.equal(selected.length, 0);
});

test('the wheel dollies the camera and leaves the pivot alone', () => {
  const { element, controls } = build();
  const pivot = { ...controls.target };
  const distance = controls.distance;
  element.dispatch('wheel', { deltaY: -240 });
  assert.ok(controls.distance < distance, 'the camera came closer');
  assert.deepEqual(controls.target, pivot, 'the pivot did not move');
});

test('the camera basis follows the three.js convention', () => {
  const { controls } = build();
  const forward = controls.forwardVector();
  const right = controls.rightVector();
  const up = controls.upVector();
  // The right vector is the forward vector crossed with the world up.
  const raw = {
    x: forward.y * 0 - forward.z * 1,
    y: forward.z * 0 - forward.x * 0,
    z: forward.x * 1 - forward.y * 0,
  };
  const length = Math.hypot(raw.x, raw.y, raw.z) || 1;
  const expected = { x: raw.x / length, y: raw.y / length, z: raw.z / length };
  assert.ok(Math.abs(right.x - expected.x) < 1e-9);
  assert.ok(Math.abs(right.y - expected.y) < 1e-9);
  assert.ok(Math.abs(right.z - expected.z) < 1e-9);
  // The three vectors are orthogonal and of unit length.
  for (const vector of [forward, right, up]) {
    assert.ok(Math.abs(Math.hypot(vector.x, vector.y, vector.z) - 1) < 1e-9);
  }
  assert.ok(Math.abs(forward.x * right.x + forward.y * right.y + forward.z * right.z) < 1e-9);
});

/* ------------------------------------------------------------- keyboard */

test('F frames the selection', () => {
  const { element, controls } = build({
    getSelectionBounds: () => ({ center: { x: 10, y: 20, z: 30 }, radius: 4 }),
  });
  element.dispatch('keydown', { key: 'f' });
  assert.deepEqual(controls.target, { x: 10, y: 20, z: 30 });
  assert.equal(controls.distance, 4 * UNITY_CONTROL_CONSTANTS.framePadding);
});

test('F without a selection frames the whole scene', () => {
  const { element, controls } = build({
    getSelectionBounds: () => null,
  });
  element.dispatch('keydown', { key: 'f' });
  assert.deepEqual(controls.target, { x: 0, y: 0, z: 0 });
  assert.equal(controls.distance, 50 * UNITY_CONTROL_CONSTANTS.framePadding);
});

test('Esc clears the selection', () => {
  const { element, cleared } = build();
  element.dispatch('keydown', { key: 'Escape' });
  assert.equal(cleared.length, 1);
});

test('the fly keys only move the camera while the right button is held', () => {
  const { element, controls } = build();
  const before = { ...controls.target };
  element.dispatch('keydown', { key: 'w' });
  assert.equal(controls.tick(0.5), false, 'nothing moves without the right button');
  assert.deepEqual(controls.target, before);
  element.dispatch('pointerdown', { button: 2, clientX: 100, clientY: 100, pointerId: 1 });
  assert.equal(controls.tick(0.5), true, 'the camera flies');
  assert.notDeepEqual(controls.target, before);
});

/* --------------------------------------------------------------- bounds */

test('the bounds of a set of points enclose them', () => {
  const bounds = boundsOfPoints([
    { x: 0, y: 0, z: 0 },
    { x: 10, y: 0, z: 0 },
  ]);
  assert.deepEqual(bounds.center, { x: 5, y: 0, z: 0 });
  assert.ok(Math.abs(bounds.radius - 5) < 1e-9);
});

test('an empty set of points has no bounds', () => {
  assert.equal(boundsOfPoints([]), null);
  assert.equal(boundsOfPoints(null), null);
});
