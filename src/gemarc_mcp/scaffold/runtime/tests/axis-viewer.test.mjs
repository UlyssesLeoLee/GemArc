/**
 * Regression tests of the semantic-axis three.js renderer.
 *
 * The stub in `helpers/three-stub.mjs` behaves like the real client: the
 * raycaster intersects exactly and the projection mirrors three. A test that
 * passes against a sloppy stub would prove nothing about the browser.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AXIS_LAYOUT_CONSTANTS,
  AXIS_VIEWER_ERROR_CODES,
  UNKNOWN_KIND_COLOR,
  computeAxisLayout,
  createThreeAxisRenderer,
  kindColor,
  screenDistanceToSegment,
} from '../space/axis-viewer-three.js';
import { createStubContainer, createStubThree, Vector3 } from './helpers/three-stub.mjs';

/* ------------------------------------------------------------- fixture */

const VIEW = {
  viewId: 'v-3d',
  axes: {
    x: { field: 'properties.layer', label: '部署层', values: ['应用层', '数据层'] },
    y: { field: 'properties.trustZone', label: '信任域', values: ['内部域', '外部域'] },
    z: { field: 'properties.environment', label: '环境', values: ['生产环境'] },
  },
  independent: { x: true, y: true, z: false },
  nodes: [
    {
      id: 'n-a',
      name: 'Alpha',
      threeDKind: 'service',
      values: { x: '应用层', y: '内部域', z: '生产环境' },
    },
    {
      id: 'n-b',
      name: 'Beta',
      threeDKind: 'module',
      values: { x: '数据层', y: '内部域', z: '生产环境' },
    },
    {
      id: 'n-c',
      name: 'Gamma',
      threeDKind: 'service',
      values: { x: '应用层', y: null, z: '生产环境' },
    },
  ],
  edges: [
    { id: 'e-1', sourceId: 'n-a', targetId: 'n-b', threeDType: 'calls' },
  ],
};

/* ------------------------------------------------------------- helpers */

function findById(list, id) {
  return list.find((item) => item.id === id) || null;
}

/**
 * Project a world point onto the client rectangle of the canvas.
 *
 * The convention is the one three uses: `right = forward x worldUp` and
 * `up = right x forward`. Getting the second sign wrong mirrors the scene
 * vertically, and the click then lands on a different entity than the one
 * the test meant.
 */
function projectToClient(camera, point, width = 800, height = 600) {
  const target = camera.lookAtTarget || { x: 0, y: 0, z: 0 };
  const forward = new Vector3(
    target.x - camera.position.x,
    target.y - camera.position.y,
    target.z - camera.position.z,
  ).normalize();
  const right = forward.clone().cross(new Vector3(0, 1, 0)).normalize();
  const up = right.clone().cross(forward).normalize();
  const v = new Vector3(
    point.x - camera.position.x,
    point.y - camera.position.y,
    point.z - camera.position.z,
  );
  const depth = v.dot(forward);
  const tanHalfFov = Math.tan((camera.fov * Math.PI) / 360);
  const ndcX = v.dot(right) / (depth * tanHalfFov * camera.aspect);
  const ndcY = v.dot(up) / (depth * tanHalfFov);
  return {
    clientX: ((ndcX + 1) / 2) * width,
    clientY: ((1 - ndcY) / 2) * height,
  };
}

/** Click at a client position. */
function clickAt(canvas, clientX, clientY) {
  canvas.dispatch('pointerdown', { button: 0, clientX, clientY, pointerId: 1 });
  canvas.dispatch('pointerup', { button: 0, clientX, clientY, pointerId: 1 });
}

function buildRenderer(view = VIEW) {
  const THREE = createStubThree();
  const container = createStubContainer();
  const picked = { nodes: [], edges: [] };
  const renderer = createThreeAxisRenderer(THREE, {
    container,
    view,
    onSelectNode: (id) => picked.nodes.push(id),
    onSelectEdge: (id) => picked.edges.push(id),
  });
  return { THREE, container, renderer, picked };
}

/* --------------------------------------------------------------- layout */

test('the layout places every entity in the cell its own values name', () => {
  const layout = computeAxisLayout(VIEW);
  assert.equal(layout.nodes.length, 3);
  assert.equal(layout.edges.length, 1);

  const alpha = findById(layout.nodes, 'n-a');
  const beta = findById(layout.nodes, 'n-b');
  assert.ok(alpha.position.x < beta.position.x, 'the first X value stands left of the second');
  assert.equal(alpha.position.y, beta.position.y, 'they share the Y value');
});

test('an entity without a value is parked in the unset slot, not invented', () => {
  const layout = computeAxisLayout(VIEW);
  const gamma = findById(layout.nodes, 'n-c');
  const yAxis = layout.axes.find((axis) => axis.key === 'y');
  assert.deepEqual(gamma.unmappedAxes, ['y']);
  assert.equal(gamma.position.y, yAxis.unsetCoordinate);
  assert.notEqual(gamma.position.y, findById(layout.nodes, 'n-a').position.y);
});

test('the same dataset always produces the same scene', () => {
  const first = computeAxisLayout(VIEW);
  const second = computeAxisLayout(VIEW);
  assert.deepEqual(first, second);
});

test('entities that share a cell are spread instead of stacked', () => {
  const view = {
    ...VIEW,
    nodes: [
      { id: 'n-1', name: 'One', threeDKind: 'service', values: { x: '应用层', y: '内部域', z: '生产环境' } },
      { id: 'n-2', name: 'Two', threeDKind: 'service', values: { x: '应用层', y: '内部域', z: '生产环境' } },
      { id: 'n-3', name: 'Three', threeDKind: 'service', values: { x: '应用层', y: '内部域', z: '生产环境' } },
    ],
    edges: [],
  };
  const layout = computeAxisLayout(view);
  const positions = layout.nodes.map((node) => `${node.position.x},${node.position.y},${node.position.z}`);
  assert.equal(new Set(positions).size, 3, 'the three entities do not share a point');
  // The spread never reaches the next slot: an entity stays inside its cell.
  const cell = layout.axes.find((axis) => axis.key === 'x').slots[0].coordinate;
  for (const node of layout.nodes) {
    assert.ok(Math.abs(node.position.x - cell) <= AXIS_LAYOUT_CONSTANTS.slotSpacing / 3);
  }
});

test('a view without axes is refused instead of being drawn arbitrarily', () => {
  assert.throws(
    () => computeAxisLayout({ axes: {}, nodes: [] }),
    (error) => error.code === AXIS_VIEWER_ERROR_CODES.NO_AXES,
  );
});

test('an unregistered kind keeps the muted colour', () => {
  assert.equal(kindColor('service'), kindColor('service'));
  assert.notEqual(kindColor('service'), UNKNOWN_KIND_COLOR);
  assert.equal(kindColor('not-a-kind'), UNKNOWN_KIND_COLOR);
  assert.equal(kindColor(null), UNKNOWN_KIND_COLOR);
});

test('the distance from a point to a segment is measured on the line', () => {
  assert.equal(screenDistanceToSegment(5, 0, 0, 0, 10, 0), 0);
  assert.equal(screenDistanceToSegment(5, 3, 0, 0, 10, 0), 3);
  // Beyond the end of the segment the distance is measured to the end point.
  assert.equal(screenDistanceToSegment(13, 0, 0, 0, 10, 0), 3);
});

/* ------------------------------------------------------------- renderer */

test('the renderer refuses to start without a WebGL context', () => {
  const THREE = createStubThree();
  const container = createStubContainer({ webgl: false });
  assert.throws(
    () => createThreeAxisRenderer(THREE, { container, view: VIEW }),
    (error) => error.code === AXIS_VIEWER_ERROR_CODES.NO_WEBGL,
  );
});

test('the renderer draws every entity and every relation', () => {
  const { renderer } = buildRenderer();
  const meshes = [];
  renderer.scene.traverse((object) => {
    if (object.userData && (object.userData.nodeId || object.userData.edgeId)) meshes.push(object);
  });
  assert.equal(meshes.filter((mesh) => mesh.userData.nodeId).length, 3);
  assert.equal(meshes.filter((mesh) => mesh.userData.edgeId).length, 1);
  renderer.dispose();
});

test('clicking an entity selects the entity, never a relation', () => {
  const { renderer, picked } = buildRenderer();
  const alpha = findById(renderer.layout.nodes, 'n-a');
  const point = projectToClient(renderer.camera, alpha.position);
  clickAt(renderer.canvas, point.clientX, point.clientY);
  assert.deepEqual(picked.nodes, ['n-a']);
  assert.deepEqual(picked.edges, [], 'a relation was not selected by accident');
  renderer.dispose();
});

test('clicking a relation selects the relation, never an entity', () => {
  const { renderer, picked } = buildRenderer();
  const edge = renderer.layout.edges[0];
  const middle = {
    x: (edge.from.x + edge.to.x) / 2,
    y: (edge.from.y + edge.to.y) / 2,
    z: (edge.from.z + edge.to.z) / 2,
  };
  const point = projectToClient(renderer.camera, middle);
  clickAt(renderer.canvas, point.clientX, point.clientY);
  assert.deepEqual(picked.edges, ['e-1']);
  assert.deepEqual(picked.nodes, [], 'an entity was not selected by accident');
  renderer.dispose();
});

test('the highlight marks the selected entity', () => {
  const { renderer } = buildRenderer();
  renderer.setHighlight({ nodeIds: ['n-b'], edgeIds: ['e-1'] });
  let alphaOpacity = null;
  let betaOpacity = null;
  renderer.scene.traverse((object) => {
    if (object.userData && object.userData.nodeId === 'n-a') alphaOpacity = object.material.opacity;
    if (object.userData && object.userData.nodeId === 'n-b') betaOpacity = object.material.opacity;
  });
  // The body of a crystal stays translucent in every state; the selection is
  // carried by the edge, not by the opacity of the body.
  assert.equal(alphaOpacity, 0.58, 'the entity that is not selected is dimmed');
  assert.equal(betaOpacity, 0.88, 'the selected entity is drawn at full strength');
  renderer.dispose();
});

test('disposing the renderer releases the canvas', () => {
  const { renderer, container } = buildRenderer();
  assert.ok(container.child, 'the canvas was appended');
  renderer.dispose();
  assert.equal(container.child, null, 'the canvas was removed again');
});
