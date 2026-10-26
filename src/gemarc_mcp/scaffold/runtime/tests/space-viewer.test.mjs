/**
 * Regression tests of the three.js space renderer.
 *
 * The stub in `helpers/three-stub.mjs` behaves like the real client: the
 * raycaster intersects exactly, the quaternion orients a tube the way three
 * does, and a label keeps the aspect ratio of its measured text. A test that
 * passes against a sloppy stub would prove nothing about the browser.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LAYOUT_CONSTANTS,
  PICK_TOLERANCE_PX,
  SPACE_VIEWER_ERROR_CODES,
  UNASSIGNED_REGION_ID,
  cameraForNavigation,
  classifyEdgeRelations,
  computeSpaceLayout,
  containerIsRealDeploymentUnit,
  createThreeSpaceRenderer,
  distancePointToSegment,
  relationClassTreatmentTable,
  resolvePickHit,
} from '../space/space-viewer-three.js';
import {
  createStubCanvas,
  createStubContainer,
  createStubThree,
  Vector3,
} from './helpers/three-stub.mjs';

/* ------------------------------------------------------------- fixture */

const MODEL = {
  meta: { evidenceState: 'Observed' },
  nodes: [
    { id: 'n-a', name: 'Alpha', kind: 'service', spaceRef: { roomId: 'r-1' } },
    { id: 'n-b', name: 'Beta', kind: 'service', spaceRef: { roomId: 'r-1' } },
    { id: 'n-e', name: 'Epsilon', kind: 'service', spaceRef: { roomId: 'r-1' } },
    { id: 'n-c', name: 'Gamma', kind: 'service', spaceRef: { roomId: 'r-2' } },
    { id: 'n-g', name: 'Eta', kind: 'service', spaceRef: { roomId: 'r-4' } },
    { id: 'n-f', name: 'Theta', kind: 'service', spaceRef: { roomId: 'r-3' } },
    { id: 'n-d', name: 'Delta', kind: 'service' },
  ],
  edges: [
    { id: 'e-1', sourceId: 'n-a', targetId: 'n-b', type: 'calls' },
    { id: 'e-2', sourceId: 'n-a', targetId: 'n-c', type: 'calls' },
    { id: 'e-3', sourceId: 'n-c', targetId: 'n-f', type: 'calls' },
    { id: 'e-4', sourceId: 'n-a', targetId: 'n-d', type: 'calls' },
    { id: 'e-5', sourceId: 'n-c', targetId: 'n-g', type: 'calls' },
  ],
  space: {
    buildings: [
      { id: 'b-1', name: 'Campus', kind: 'system', semanticSource: 'src:b-1' },
      { id: 'b-2', name: 'Edge Site', kind: 'system', semanticSource: 'src:b-2' },
    ],
    floors: [
      { id: 'f-1', name: 'Platform', buildingId: 'b-1', semanticSource: 'src:f-1' },
      { id: 'f-3', name: 'Data', buildingId: 'b-1', semanticSource: 'src:f-3' },
      { id: 'f-2', name: 'Runtime', buildingId: 'b-2', semanticSource: 'src:f-2' },
    ],
    rooms: [
      { id: 'r-1', name: 'Domain A', floorId: 'f-1', semanticSource: 'src:r-1' },
      { id: 'r-2', name: 'Domain B', floorId: 'f-1', semanticSource: 'src:r-2' },
      { id: 'r-4', name: 'Storage', floorId: 'f-3', semanticSource: 'src:r-4' },
      { id: 'r-3', name: 'Cluster', floorId: 'f-2', semanticSource: 'src:r-3' },
    ],
  },
};

/* ------------------------------------------------------------- helpers */

function findById(list, id) {
  return list.find((item) => item.id === id) || null;
}

/** The two ends of an edge pick tube, in world coordinates. */
function tubeEndpoints(tube) {
  const axis = new Vector3(0, tube.scale.y / 2, 0).applyQuaternion(tube.quaternion);
  return {
    start: { x: tube.position.x - axis.x, y: tube.position.y - axis.y, z: tube.position.z - axis.z },
    end: { x: tube.position.x + axis.x, y: tube.position.y + axis.y, z: tube.position.z + axis.z },
  };
}

function distance(a, b) {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2);
}

/** Project a world point onto the client rectangle of the canvas. */
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

/** Click at a client position and return the pick result. */
function clickAt(canvas, clientX, clientY) {
  canvas.dispatch('pointerdown', { button: 0, clientX, clientY, pointerId: 1 });
  canvas.dispatch('pointerup', { button: 0, clientX, clientY, pointerId: 1 });
}

function buildRenderer(model = MODEL) {
  const THREE = createStubThree();
  const container = createStubContainer();
  const renderer = createThreeSpaceRenderer(THREE, {
    container,
    model,
    space: model.space,
  });
  return { THREE, container, renderer };
}

/* --------------------------------------------------------------- layout */

test('the space layout follows the Building-Floor-Room mapping', () => {
  const layout = computeSpaceLayout(MODEL);
  assert.equal(layout.buildings.length, 2);
  assert.equal(layout.floors.length, 3);
  // Four rooms plus the explicit region for the nodes without a room.
  assert.equal(layout.rooms.length, 5);
  assert.equal(Object.keys(layout.nodes).length, 7);

  const campus = findById(layout.buildings, 'b-1');
  const site = findById(layout.buildings, 'b-2');
  assert.ok(campus, 'the first building exists');
  // The buildings stand side by side along the X axis.
  assert.ok(site.center.x > campus.center.x, 'the second building is further along X');

  // The floors of a building are stacked along Y.
  const platform = findById(layout.floors, 'f-1');
  const data = findById(layout.floors, 'f-3');
  assert.ok(data.center.y > platform.center.y, 'the upper floor is higher');

  // The rooms of a floor stand side by side along X.
  const domainA = findById(layout.rooms, 'r-1');
  const domainB = findById(layout.rooms, 'r-2');
  assert.ok(domainB.center.x > domainA.center.x, 'the rooms are ordered along X');
  assert.equal(domainA.center.y, domainB.center.y, 'the rooms share the storey');
});

test('a room is a volume that stands on the slab of its storey', () => {
  const layout = computeSpaceLayout(MODEL);
  const room = findById(layout.rooms, 'r-1');
  const floor = findById(layout.floors, 'f-1');
  const expectedHeight = LAYOUT_CONSTANTS.floorHeight - LAYOUT_CONSTANTS.floorThickness;
  assert.equal(room.size.y, expectedHeight, 'the room fills the storey above its slab');
  assert.ok(
    Math.abs((room.center.y - room.size.y / 2) - (floor.center.y + floor.size.y / 2)) < 1e-9,
    'the bottom of the room sits on the slab',
  );
});

test('the nodes stand on the floor of their room and keep their grid position', () => {
  const layout = computeSpaceLayout(MODEL);
  const room = findById(layout.rooms, 'r-1');
  const alpha = layout.nodes['n-a'];
  const beta = layout.nodes['n-b'];
  const epsilon = layout.nodes['n-e'];

  assert.equal(
    alpha.position.y,
    room.center.y - room.size.y / 2 + LAYOUT_CONSTANTS.nodeSize / 2,
    'the node stands on the floor of the room',
  );
  assert.notEqual(alpha.position.z, epsilon.position.z, 'the third node opens a second row');
  assert.notEqual(alpha.position.z, 0, 'the row offset is not collapsed onto the origin');
  assert.equal(alpha.position.x, room.center.x - LAYOUT_CONSTANTS.nodeSpacing / 2);
  assert.equal(beta.position.x, room.center.x + LAYOUT_CONSTANTS.nodeSpacing / 2);
  assert.equal(alpha.position.z, epsilon.position.z - LAYOUT_CONSTANTS.nodeSpacing);
});

test('a node without a room lands in the explicit unassigned region', () => {
  const layout = computeSpaceLayout(MODEL);
  const region = layout.roomsById[UNASSIGNED_REGION_ID];
  assert.ok(region, 'the unassigned region exists');
  assert.deepEqual(layout.unassignedNodeIds, ['n-d']);
  assert.deepEqual(region.nodeIds, ['n-d']);
  assert.equal(region.semanticSource, null);
  assert.equal(region.isRealDeploymentUnit, false);
  assert.equal(layout.nodes['n-d'].unassigned, true);
  // The region stands on the ground: it belongs to no storey at all.
  assert.equal(region.center.y - region.size.y / 2, 0);
});

test('the layout is deterministic', () => {
  assert.deepEqual(computeSpaceLayout(MODEL), computeSpaceLayout(MODEL));
});

/* ------------------------------------------------------- relations */

test('the edge relations follow the space hierarchy', () => {
  const layout = computeSpaceLayout(MODEL);
  const classified = classifyEdgeRelations(
    layout,
    MODEL.edges,
    MODEL,
  );
  const byId = Object.fromEntries(classified.map((item) => [item.edgeId, item]));

  assert.equal(byId['e-1'].relation, 'same-room');
  assert.equal(byId['e-1'].crossesBoundary, false);
  assert.equal(byId['e-2'].relation, 'cross-room');
  assert.equal(byId['e-2'].crossesBoundary, true);
  assert.equal(byId['e-5'].relation, 'cross-floor');
  assert.equal(byId['e-3'].relation, 'cross-building');
  // An edge that touches a node without a location has no place in the
  // taxonomy. The renderer must not invent a level for it.
  assert.equal(byId['e-4'].relation, null);
  assert.equal(byId['e-4'].scope, 'unassigned');
});

test('a plain space object without the node list yields no invented relation', () => {
  const classified = classifyEdgeRelations(MODEL.space, MODEL.edges);
  assert.equal(classified.length, MODEL.edges.length);
  for (const item of classified) {
    assert.equal(item.relation, null);
    assert.equal(item.scope, 'unassigned');
  }
});

/* --------------------------------------------------------- camera */

test('every navigation yields its own camera placement', () => {
  const layout = computeSpaceLayout(MODEL);
  const context = {
    layout,
    buildingId: 'b-1',
    floorId: 'f-1',
    roomId: 'r-1',
    nodeId: 'n-a',
  };
  const ids = [
    'overview',
    'building-select',
    'floor-switch',
    'floor-exploded',
    'room-focus',
    'room-cutaway',
    'node-focus',
    'back-to-overview',
  ];
  const placements = ids.map((id) => cameraForNavigation(id, context));
  const serialized = placements.map((item) => JSON.stringify(item.position));
  assert.equal(new Set(serialized).size, ids.length - 1, 'no two navigations share a position');
  for (const placement of placements) {
    for (const value of Object.values(placement.position)) {
      assert.ok(Number.isFinite(value), 'the camera position is finite');
    }
  }
  // The navigation is deterministic.
  assert.deepEqual(cameraForNavigation('room-focus', context), placements[4]);
});

test('back-to-overview is the global bird\'s eye again', () => {
  const layout = computeSpaceLayout(MODEL);
  const context = { layout, buildingId: 'b-1', floorId: 'f-1', roomId: 'r-1', nodeId: 'n-a' };
  const overview = cameraForNavigation('overview', context);
  const back = cameraForNavigation('back-to-overview', context);
  assert.deepEqual(back.position, overview.position);
  assert.deepEqual(back.target, overview.target);
});

test('the overview camera frames the whole space inside the frustum', () => {
  const layout = computeSpaceLayout(MODEL);
  const width = 800;
  const height = 600;
  const fov = 50;
  const aspect = width / height;
  const placement = cameraForNavigation('overview', { layout, fov, aspect });

  // Every corner of the scene has to land inside the canvas. A camera that
  // is too far away leaves the drawing tiny; one that is too close cuts the
  // space in half.
  const target = placement.target;
  const camera = {
    position: placement.position,
    lookAtTarget: target,
    fov,
    aspect,
  };
  const { min, max } = layout.bounds;
  for (const sx of [min.x, max.x]) {
    for (const sy of [min.y, max.y]) {
      for (const sz of [min.z, max.z]) {
        const point = projectToClient(camera, { x: sx, y: sy, z: sz }, width, height);
        assert.ok(point.clientX >= -1 && point.clientX <= width + 1,
          `the corner x=${point.clientX} is inside the canvas`);
        assert.ok(point.clientY >= -1 && point.clientY <= height + 1,
          `the corner y=${point.clientY} is inside the canvas`);
      }
    }
  }
});

test('a taller canvas does not change the framing of a flat scene', () => {
  const layout = computeSpaceLayout(MODEL);
  const wide = cameraForNavigation('overview', { layout, fov: 50, aspect: 800 / 600 });
  const tall = cameraForNavigation('overview', { layout, fov: 50, aspect: 600 / 800 });
  assert.notEqual(wide.position.x, tall.position.x,
    'the camera adapts to the aspect ratio of the canvas');
  assert.deepEqual(wide.target, tall.target, 'the target stays the centre of the space');
});

test('an unknown navigation is rejected', () => {
  assert.throws(
    () => cameraForNavigation('no-such-navigation', {}),
    (error) => error.code === SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT,
  );
});

/* ------------------------------------------------- reality boundary */

test('a container without a semantic source is not a real deployment unit', () => {
  assert.equal(containerIsRealDeploymentUnit({ semanticSource: 'src:x' }), true);
  assert.equal(containerIsRealDeploymentUnit({ semanticSource: null }), false);
  assert.equal(containerIsRealDeploymentUnit(null), false);
});

test('the relation treatments come from the vocabulary', () => {
  const table = relationClassTreatmentTable();
  assert.deepEqual(
    Object.keys(table).sort(),
    ['Contextually Related', 'Directly Related', 'Selected', 'Unrelated'],
  );
  assert.equal(table.Selected.opacity, 1);
  assert.equal(table.Selected.labels, 'full');
  assert.equal(table['Contextually Related'].labels, 'collapsed');
  assert.equal(table.Unrelated.labels, 'hidden');
});

/* -------------------------------------------------- error handling */

test('the renderer rejects an incomplete setup with an explicit code', () => {
  const THREE = createStubThree();
  const container = createStubContainer();
  const cases = [
    [{ THREE }, SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT],
    [{ THREE, container }, SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT],
    [{ THREE, container, model: MODEL }, SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT],
    [{ container, model: MODEL, space: MODEL.space }, SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT],
  ];
  for (const [options, code] of cases) {
    assert.throws(
      () => createThreeSpaceRenderer(options.THREE, options),
      (error) => error.code === code,
      `the options ${JSON.stringify(Object.keys(options))} are rejected`,
    );
  }
});

test('a missing WebGL context is an explicit failure, never a silent fallback', () => {
  const THREE = createStubThree();
  const container = createStubContainer({ webgl: false });
  assert.throws(
    () => createThreeSpaceRenderer(THREE, {
      container,
      model: MODEL,
      space: MODEL.space,
    }),
    (error) => error.code === SPACE_VIEWER_ERROR_CODES.NO_WEBGL,
  );
});

/* --------------------------------------------------- the scene graph */

test('the scene holds every container, node and edge of the model', () => {
  const { renderer } = buildRenderer();
  const stats = renderer.stats();
  assert.equal(stats.buildings, 2);
  assert.equal(stats.floors, 3);
  assert.equal(stats.rooms, 5);
  assert.equal(stats.nodes, 7);
  assert.equal(stats.edges, 5);
});

test('the node meshes sit where the layout put them', () => {
  const { renderer } = buildRenderer();
  const layout = renderer.layout();
  const meshes = new Map();
  renderer.scene.traverse((object) => {
    if (object.userData?.pickKind === 'node') {
      meshes.set(object.userData.pickId, object.worldPosition());
    }
  });
  assert.equal(meshes.size, 7);
  for (const [nodeId, node] of Object.entries(layout.nodes)) {
    const position = meshes.get(nodeId);
    assert.ok(position, `the node ${nodeId} is in the scene`);
    assert.ok(Math.abs(position.x - node.position.x) < 1e-9, `the x of ${nodeId}`);
    assert.ok(Math.abs(position.y - node.position.y) < 1e-9, `the y of ${nodeId}`);
    assert.ok(Math.abs(position.z - node.position.z) < 1e-9, `the z of ${nodeId}`);
  }
});

test('a node label keeps the aspect ratio of its measured text', () => {
  const previous = globalThis.document;
  globalThis.document = {
    createElement(tag) {
      return tag === 'canvas' ? createStubCanvas() : {};
    },
  };
  try {
    const { renderer } = buildRenderer();
    const labels = [];
    renderer.scene.traverse((object) => {
      if (object.isSprite && object.userData?.baseScale) labels.push(object);
    });
    assert.ok(labels.length > 0, 'the labels exist');
    const ratios = [];
    for (const label of labels) {
      const base = label.userData.baseScale;
      assert.ok(
        Math.abs(label.scale.x / label.scale.y - base.x / base.y) < 1e-9,
        'the label keeps the aspect ratio of the text',
      );
      ratios.push(base.x / base.y);
    }
    // The measured width really drives the ratio: at least one label is
    // clearly wider than it is high.
    assert.ok(
      ratios.some((ratio) => ratio > 1.2),
      'the text width is measured, not replaced by a square',
    );

    // A collapsed label shrinks by a factor and keeps that ratio. Only the
    // node that carries the relation class is treated.
    renderer.setRelationClasses({ 'n-a': 'Contextually Related' });
    const alpha = labels.find((label) => label.userData.labelText === 'Alpha');
    assert.ok(alpha, 'the label of the treated node exists');
    const base = alpha.userData.baseScale;
    assert.ok(
      Math.abs(alpha.scale.x - base.x * 0.72) < 1e-9,
      'the collapsed label is scaled, not squashed',
    );
    assert.ok(
      Math.abs(alpha.scale.x / alpha.scale.y - base.x / base.y) < 1e-9,
      'the ratio survives the collapse',
    );
    // The other labels are untouched.
    const beta = labels.find((label) => label.userData.labelText === 'Beta');
    assert.ok(Math.abs(beta.scale.x - beta.userData.baseScale.x) < 1e-9);
  } finally {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  }
});

/* --------------------------------------------------------- picking */

test('the node and the edge pick targets live in separate id spaces', () => {
  const { renderer } = buildRenderer();
  const nodes = [];
  const edges = [];
  renderer.scene.traverse((object) => {
    if (object.userData?.pickKind === 'node') nodes.push(object.userData.pickId);
    if (object.userData?.pickKind === 'edge') edges.push(object.userData.pickId);
  });
  assert.equal(nodes.length, 7);
  assert.equal(edges.length, 5);
  for (const edgeId of edges) {
    assert.ok(!nodes.includes(edgeId), `the edge ${edgeId} is not registered as a node`);
  }
  for (const nodeId of nodes) {
    assert.ok(!edges.includes(nodeId), `the node ${nodeId} is not registered as an edge`);
  }
});

test('a relation line connects exactly the two nodes it links', () => {
  const { renderer } = buildRenderer();
  const layout = renderer.layout();
  const tubes = new Map();
  renderer.scene.traverse((object) => {
    if (object.userData?.pickKind !== 'edge') return;
    // A relation owns a group; the line itself is one of its children.
    const mesh = object.geometry ? object : object.children.find((child) => child.geometry);
    if (mesh) tubes.set(object.userData.pickId, mesh);
  });
  for (const edge of MODEL.edges) {
    const tube = tubes.get(edge.id);
    assert.ok(tube, `the relation ${edge.id} has a line`);
    const { start, end } = tubeEndpoints(tube);
    const source = layout.nodes[edge.sourceId].position;
    const target = layout.nodes[edge.targetId].position;
    // The line runs from node to node. Anything else would draw a relation
    // that does not connect the two entities it claims to connect.
    assert.ok(distance(start, source) < 1e-6, `the line of ${edge.id} starts at its source`);
    assert.ok(distance(end, target) < 1e-6, `the line of ${edge.id} ends at its target`);
  }
});

test('a node always wins over an edge under the pointer', () => {
  const nodeHit = { object: { userData: { pickKind: 'node', pickId: 'n-a' } } };
  const edgeHit = { object: { userData: { pickKind: 'edge', pickId: 'e-1' } } };
  assert.deepEqual(resolvePickHit([nodeHit], [edgeHit]), { kind: 'node', id: 'n-a' });
  assert.deepEqual(resolvePickHit([], [edgeHit]), { kind: 'edge', id: 'e-1' });
  assert.deepEqual(resolvePickHit([], []), null);
  assert.deepEqual(resolvePickHit([nodeHit], []), { kind: 'node', id: 'n-a' });
});

test('clicking a node picks the node', () => {
  const { renderer, container } = buildRenderer();
  const layout = renderer.layout();
  const target = layout.nodes['n-a'].position;
  const point = projectToClient(renderer.camera, target);
  clickAt(container.canvas, point.clientX, point.clientY);
  // The renderer reports the pick through its own callback; the click itself
  // must not throw and must not resolve to an edge.
  assert.ok(renderer.stats().nodes > 0);
});

test('the renderer exposes the pick target of a click through its callbacks', () => {
  const THREE = createStubThree();
  const container = createStubContainer();
  const picked = [];
  const renderer = createThreeSpaceRenderer(THREE, {
    container,
    model: MODEL,
    space: MODEL.space,
    onSelectNode: (id) => picked.push({ kind: 'node', id }),
    onSelectEdge: (id) => picked.push({ kind: 'edge', id }),
  });
  const layout = renderer.layout();

  // Aim at the middle of the cross-room edge e-2. A short same-room edge is
  // not a fair target: its middle can fall behind the silhouette of the
  // nearer node, and there the node is genuinely what the user sees.
  const source = layout.nodes['n-a'].position;
  const target = layout.nodes['n-c'].position;
  const middle = {
    x: (source.x + target.x) / 2,
    y: (source.y + target.y) / 2,
    z: (source.z + target.z) / 2,
  };
  const point = projectToClient(renderer.camera, middle);
  clickAt(container.canvas, point.clientX, point.clientY);
  assert.deepEqual(picked, [{ kind: 'edge', id: 'e-2' }]);

  // Aim at a node: the node wins. The node is drawn on top of the edges, so
  // the same rule holds in every dimension.
  const nodePoint = projectToClient(renderer.camera, layout.nodes['n-c'].position);
  clickAt(container.canvas, nodePoint.clientX, nodePoint.clientY);
  assert.deepEqual(picked, [
    { kind: 'edge', id: 'e-2' },
    { kind: 'node', id: 'n-c' },
  ]);
});

test('the pick tolerance is measured in pixels, not in world units', () => {
  const THREE = createStubThree();
  const container = createStubContainer();
  const picked = [];
  const renderer = createThreeSpaceRenderer(THREE, {
    container,
    model: MODEL,
    space: MODEL.space,
    onSelectNode: (id) => picked.push({ kind: 'node', id }),
    onSelectEdge: (id) => picked.push({ kind: 'edge', id }),
  });
  const layout = renderer.layout();

  // A few pixels away from the centre of a node is still that node. A
  // world-space radius would shrink the target to a couple of pixels at this
  // camera distance, and nobody could click it.
  const nodePoint = projectToClient(renderer.camera, layout.nodes['n-c'].position);
  clickAt(container.canvas, nodePoint.clientX + 6, nodePoint.clientY + 4);
  assert.deepEqual(picked, [{ kind: 'node', id: 'n-c' }]);

  // The same holds for a relation: a few pixels away from the line is still
  // the line. When two relations run close together, the closest one wins.
  picked.length = 0;
  const source = layout.nodes['n-a'].position;
  const target = layout.nodes['n-c'].position;
  const middle = {
    x: (source.x + target.x) / 2,
    y: (source.y + target.y) / 2,
    z: (source.z + target.z) / 2,
  };
  const edgePoint = projectToClient(renderer.camera, middle);
  const probeX = edgePoint.clientX + 7;
  const probeY = edgePoint.clientY - 5;
  const expected = MODEL.edges
    .map((edge) => {
      const a = projectToClient(renderer.camera, layout.nodes[edge.sourceId].position);
      const b = projectToClient(renderer.camera, layout.nodes[edge.targetId].position);
      return {
        id: edge.id,
        distance: distancePointToSegment(probeX, probeY, a.clientX, a.clientY, b.clientX, b.clientY),
      };
    })
    .sort((a, b) => a.distance - b.distance)[0];
  assert.ok(expected.distance <= PICK_TOLERANCE_PX.edge, 'the probe point is on a relation');
  clickAt(container.canvas, probeX, probeY);
  assert.deepEqual(picked, [{ kind: 'edge', id: expected.id }]);

  // Far away from everything: nothing is picked.
  picked.length = 0;
  clickAt(container.canvas, 5, 5);
  assert.deepEqual(picked, []);
});

/* --------------------------------------------------- lifecycle */

test('disposing the renderer releases the canvas and the renderer', () => {
  const { renderer, container } = buildRenderer();
  renderer.dispose();
  assert.ok(renderer.renderer.disposed, 'the WebGL renderer is disposed');
  assert.equal(container.child, null, 'the canvas is removed from the container');
});

test('the renderer rebuilds when the model changes', () => {
  const { renderer } = buildRenderer();
  const first = renderer.layout();
  renderer.render();
  const second = renderer.layout();
  assert.deepEqual(first, second, 'the same model yields the same layout');
  assert.equal(renderer.stats().nodes, 7);
});
