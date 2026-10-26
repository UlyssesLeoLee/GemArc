/**
 * Real 3D renderer for the Building–Floor–Room engineering space
 * (Universal 5D GemArc Web v5.0 §6).
 *
 * The dimension used to be a text list because no renderer existed. This module
 * renders the space as an actual WebGL scene and makes the engineering space
 * pickable.
 *
 * Hard rules this module obeys:
 *
 *   1. **No third-party import.** `three` is injected by the profile (an import
 *      map for the static profile, `node_modules` for the vite profile). The
 *      runtime has a hard rule against bare module specifiers, and `three/addons`
 *      is not portable either, so the camera controller is implemented here.
 *   2. **No silent degradation.** When the WebGL context cannot be created the
 *      renderer throws `GEMARC_SPACE_010`. It never falls back to a picture.
 *   3. **Reality boundary.** A container without `semanticSource` is a virtual
 *      reading organisation. It is flagged `isRealDeploymentUnit: false` and is
 *      rendered in the muted "virtual" style, never as a deployment facility.
 *   4. **No meaningless decoration.** No doors, windows or furniture. A
 *      relationship crossing a room boundary is a line, never a drawn device.
 *   5. **Nodes and edges are independently pickable.** Clicking an edge never
 *      selects its source node: edges own an invisible pick tube that is trimmed
 *      away from the node bodies, and node ids and edge ids stay in separate
 *      id spaces.
 *   6. **Deterministic layout.** The same model always produces the same scene.
 *      There is no `Math.random()` anywhere in this file.
 *   7. **The retreat never moves real coordinates.** A relation class only
 *      changes opacity, outline, labels and line weight of the rendering.
 */
import { GemArcError } from '../core/errors.js';
import { RELATION_CLASS_IDS, relationClassSpec } from '../core/vocab.js';
import { treatmentForRelationClass } from '../focus/retreat-animation.js';
import { SpaceModel } from './space-model.js';

/**
 * The diagnostic codes this module raises.
 * @type {Readonly<Record<string, string>>}
 */
export const SPACE_VIEWER_ERROR_CODES = Object.freeze({
  NO_WEBGL: 'GEMARC_SPACE_010',
  INVALID_ARGUMENT: 'GEMARC_SPACE_011',
});

/**
 * The id of the explicit region that holds every node without a space
 * assignment. A node without a room must still be visible somewhere.
 * @type {string}
 */
export const UNASSIGNED_REGION_ID = '__unassigned__';

/**
 * The layout constants. They are fixed so that the same model always yields
 * the same scene.
 * @type {Readonly<Record<string, number>>}
 */
export const LAYOUT_CONSTANTS = Object.freeze({
  buildingGap: 20,
  floorHeight: 26,
  floorThickness: 1.2,
  roomWidth: 26,
  roomDepth: 18,
  roomGap: 6,
  roomThickness: 0.9,
  nodeSpacing: 6.5,
  nodeSize: 3.4,
  unassignedWidth: 32,
  unassignedDepth: 18,
  explodeStep: 9,
});

/**
 * The colours of the relationship lines. A trust boundary edge is always
 * visually distinct from an ordinary edge.
 */
const EDGE_COLORS = Object.freeze({
  'same-room': 0x5a7288,
  'cross-room': 0x6bd68a,
  'cross-floor': 0x4cc2ff,
  'cross-building': 0xb48ead,
  'unassigned': 0x8fa3b8,
  'trust-boundary': 0xffb454,
});

const NODE_COLOR = 0x4cc2ff;
const NODE_COLOR_HIGHLIGHT = 0xffb454;
const CONTAINER_COLOR = 0x22303f;
const CONTAINER_COLOR_VIRTUAL = 0x3a4353;
const OUTLINE_STRENGTH = Object.freeze({
  strong: { opacity: 1, scale: 1.42 },
  normal: { opacity: 0.8, scale: 1.22 },
  weak: { opacity: 0.35, scale: 1.1 },
  none: { opacity: 0, scale: 1 },
});

const LINE_WEIGHT_SCALE = Object.freeze({
  normal: 1,
  thin: 0.55,
  hairline: 0.28,
});

/**
 * The display-only displacement of the vocabulary `zOffset`. It is applied to
 * the rendering only; the layout data never changes.
 */
const Z_OFFSET_DISPLAY = Object.freeze({
  front: 4,
  none: 0,
  back: -4,
});

/**
 * The camera placement of every navigation. The direction and the base
 * distance differ per navigation, so two different navigations never produce
 * the same camera position - with one deliberate exception: `back-to-overview`
 * is the global bird's eye again, so it shares the position of `overview`.
 * That is the meaning of the navigation, not a shortcut.
 */
const NAVIGATION_CAMERA = Object.freeze({
  overview: { direction: [0.55, 0.72, 0.85], base: 60, factor: 2.4 },
  'building-select': { direction: [0.6, 0.55, 0.8], base: 45, factor: 2 },
  'floor-switch': { direction: [0.5, 0.5, 0.9], base: 35, factor: 1.7 },
  'floor-exploded': { direction: [0.45, 0.78, 0.85], base: 40, factor: 2.2 },
  'room-focus': { direction: [0.45, 0.6, 0.85], base: 25, factor: 1.5 },
  'room-cutaway': { direction: [0.4, 0.28, 0.95], base: 20, factor: 1.3 },
  'node-focus': { direction: [0.5, 0.45, 0.9], base: 12, factor: 0.9 },
  'back-to-overview': { direction: [0.55, 0.72, 0.85], base: 60, factor: 2.4 },
});

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function distance3(a, b) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const dz = a.z - b.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

function radiusOf(size) {
  return 0.5 * Math.sqrt(size.x * size.x + size.y * size.y + size.z * size.z);
}

/**
 * The dot product of two vectors.
 * @param {{x: number, y: number, z: number}} a
 * @param {{x: number, y: number, z: number}} b
 * @returns {number}
 */
function dot3(a, b) {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

/**
 * The cross product of two vectors.
 * @param {{x: number, y: number, z: number}} a
 * @param {{x: number, y: number, z: number}} b
 * @returns {{x: number, y: number, z: number}}
 */
function cross3(a, b) {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

/**
 * A unit vector in the same direction.
 * @param {{x: number, y: number, z: number}} v
 * @returns {{x: number, y: number, z: number}}
 */
function normalize3(v) {
  const length = Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  if (!length) return { x: 0, y: 0, z: 1 };
  return { x: v.x / length, y: v.y / length, z: v.z / length };
}

/**
 * True when a container may be presented as a real deployment unit. A
 * container without a semantic source is a virtual reading organisation and
 * must never masquerade as a real code module or a physical facility.
 *
 * @param {object|null} container a building, floor or room record
 * @returns {boolean}
 */
export function containerIsRealDeploymentUnit(container) {
  if (!container) return false;
  return Boolean(container.semanticSource);
}

/**
 * Accept either a `SpaceModel` instance or a plain space object and return a
 * `SpaceModel`. The plain object is the `model.space` of a canonical model.
 *
 * @param {object|null} space a `SpaceModel` or a plain space object
 * @param {object|null} [model] the canonical model
 * @returns {SpaceModel}
 */
function resolveSpaceModel(space, model) {
  if (space && typeof space.building === 'function' && space.buildings instanceof Map) return space;
  const nodes = model && model.nodes
    ? (model.nodes instanceof Map ? [...model.nodes.values()] : asArray(model.nodes))
    : [];
  if (space && typeof space === 'object') return new SpaceModel({ space, nodes });
  return new SpaceModel(model || {});
}

/**
 * Compute the position of every building, floor, room and node of a model.
 *
 * The layout is pure data: the renderer turns it into meshes, but the layout
 * itself carries no rendering concern, so it can be checked in plain Node.
 *
 * Mapping:
 *   - buildings are placed side by side along the X axis
 *   - the floors of a building are stacked along Y
 *   - the rooms of a floor are placed side by side along X
 *   - the nodes of a room are placed in a centred grid
 *   - a node without a room goes into the explicit `__unassigned__` region,
 *     which is a visible region of its own, never "nowhere"
 *
 * @param {object} [model] the canonical model
 * @param {object} [space] the space: a `SpaceModel` or `model.space`
 * @returns {object} the layout
 */
export function computeSpaceLayout(model = {}, space = null) {
  const spaceModel = resolveSpaceModel(space, model);
  const C = LAYOUT_CONSTANTS;

  const buildingList = [...spaceModel.buildings.values()];
  const floorList = [...spaceModel.floors.values()];
  const roomList = [...spaceModel.rooms.values()];

  // Which room claims which node. `spaceRef` wins over the container
  // membership; the SpaceModel already applies that rule.
  const nodeIdsByRoom = new Map();
  const unassignedNodeIds = [];
  for (const node of spaceModel.nodes.values()) {
    const roomId = spaceModel.roomOfNode(node.id);
    if (roomId) {
      if (!nodeIdsByRoom.has(roomId)) nodeIdsByRoom.set(roomId, []);
      nodeIdsByRoom.get(roomId).push(node.id);
    } else {
      unassignedNodeIds.push(node.id);
    }
  }

  // Pass 1 - measure every building.
  const plans = buildingList.map((building) => {
    const floors = floorList.filter((floor) => floor.buildingId === building.id);
    const roomCounts = floors.map(
      (floor) => roomList.filter((room) => room.floorId === floor.id).length,
    );
    const roomTotal = Math.max(1, ...roomCounts);
    const width = roomTotal * C.roomWidth + Math.max(0, roomTotal - 1) * C.roomGap;
    const height = Math.max(1, floors.length) * C.floorHeight;
    return { building, floors, width, height, depth: C.roomDepth, centerX: 0 };
  });

  // Pass 2 - place the buildings along the X axis.
  let cursor = 0;
  for (const plan of plans) {
    plan.centerX = cursor + plan.width / 2;
    cursor += plan.width + C.buildingGap;
  }
  const unassignedCenterX = cursor + C.unassignedWidth / 2;

  const buildings = [];
  const floors = [];
  const rooms = [];
  const nodes = {};

  const addNode = (node, position, placement) => {
    nodes[node.id] = {
      id: node.id,
      name: node.name || node.id,
      kind: node.kind || null,
      level: 'Node',
      buildingId: placement.buildingId,
      floorId: placement.floorId,
      roomId: placement.roomId,
      unassigned: placement.unassigned,
      position: { x: position.x, y: position.y, z: position.z },
    };
  };

  // Pass 3 - build the records.
  for (const plan of plans) {
    const buildingRecord = {
      id: plan.building.id,
      name: plan.building.name || plan.building.id,
      kind: plan.building.kind || null,
      level: 'Building',
      semanticSource: plan.building.semanticSource || null,
      hasSemanticSource: Boolean(plan.building.semanticSource),
      isRealDeploymentUnit: Boolean(plan.building.semanticSource),
      virtual: !plan.building.semanticSource,
      center: { x: plan.centerX, y: plan.height / 2, z: 0 },
      size: { x: plan.width, y: plan.height, z: plan.depth },
      floorIds: [],
    };
    buildingRecord.radius = radiusOf(buildingRecord.size);
    buildings.push(buildingRecord);

    plan.floors.forEach((floor, floorIndex) => {
      const floorY = floorIndex * C.floorHeight + C.floorThickness / 2;
      const floorRecord = {
        id: floor.id,
        name: floor.name || floor.id,
        kind: floor.kind || null,
        level: 'Floor',
        buildingId: plan.building.id,
        semanticSource: floor.semanticSource || null,
        hasSemanticSource: Boolean(floor.semanticSource),
        isRealDeploymentUnit: Boolean(floor.semanticSource),
        virtual: !floor.semanticSource,
        center: { x: plan.centerX, y: floorY, z: 0 },
        size: { x: plan.width, y: C.floorThickness, z: plan.depth },
        roomIds: [],
      };
      floorRecord.radius = radiusOf(floorRecord.size);
      floors.push(floorRecord);
      buildingRecord.floorIds.push(floor.id);

      const roomsOfFloor = roomList.filter((room) => room.floorId === floor.id);
      roomsOfFloor.forEach((room, roomIndex) => {
        const roomX = plan.centerX - plan.width / 2 + C.roomWidth / 2
          + roomIndex * (C.roomWidth + C.roomGap);
        // A room is a volume that fills the storey above its slab. A room with
        // no vertical extent would read as a line on a floor plan instead of
        // the space the hierarchy claims it is.
        const roomHeight = C.floorHeight - C.floorThickness;
        const roomY = floorY + C.floorThickness / 2 + roomHeight / 2;
        const roomRecord = {
          id: room.id,
          name: room.name || room.id,
          kind: room.kind || null,
          level: 'Room',
          buildingId: plan.building.id,
          floorId: floor.id,
          semanticSource: room.semanticSource || null,
          hasSemanticSource: Boolean(room.semanticSource),
          isRealDeploymentUnit: Boolean(room.semanticSource),
          virtual: !room.semanticSource,
          center: { x: roomX, y: roomY, z: 0 },
          size: { x: C.roomWidth, y: roomHeight, z: C.roomDepth },
          nodeIds: [],
        };
        roomRecord.radius = radiusOf(roomRecord.size);
        rooms.push(roomRecord);
        floorRecord.roomIds.push(room.id);

        const nodeIds = nodeIdsByRoom.get(room.id) || [];
        const columns = Math.max(1, Math.ceil(Math.sqrt(nodeIds.length)));
        const rows = Math.max(1, Math.ceil(nodeIds.length / columns));
        nodeIds.forEach((nodeId, index) => {
          const node = spaceModel.nodes.get(nodeId);
          if (!node) return;
          const column = index % columns;
          const row = Math.floor(index / columns);
          const position = {
            x: roomX + (column - (columns - 1) / 2) * C.nodeSpacing,
            // The nodes stand on the floor of their room.
            y: roomY - roomHeight / 2 + C.nodeSize / 2,
            z: (row - (rows - 1) / 2) * C.nodeSpacing,
          };
          roomRecord.nodeIds.push(nodeId);
          addNode(node, position, {
            buildingId: plan.building.id,
            floorId: floor.id,
            roomId: room.id,
            unassigned: false,
          });
        });
      });
    });
  }

  // The explicit unassigned region. A node without a space assignment is still
  // visible: it sits in this region instead of silently disappearing. The
  // region stands on the ground, because it belongs to no storey at all.
  const unassignedHeight = C.floorHeight - C.floorThickness;
  const unassignedRecord = {
    id: UNASSIGNED_REGION_ID,
    name: '未分配节点',
    kind: 'unassigned',
    level: 'Room',
    buildingId: null,
    floorId: null,
    semanticSource: null,
    hasSemanticSource: false,
    isRealDeploymentUnit: false,
    virtual: true,
    center: {
      x: unassignedCenterX,
      y: unassignedHeight / 2,
      z: 0,
    },
    size: { x: C.unassignedWidth, y: unassignedHeight, z: C.unassignedDepth },
    nodeIds: [],
  };
  unassignedRecord.radius = radiusOf(unassignedRecord.size);
  rooms.push(unassignedRecord);

  const unassignedColumns = Math.max(1, Math.ceil(Math.sqrt(unassignedNodeIds.length)));
  const unassignedRows = Math.max(1, Math.ceil(unassignedNodeIds.length / unassignedColumns));
  unassignedNodeIds.forEach((nodeId, index) => {
    const node = spaceModel.nodes.get(nodeId);
    if (!node) return;
    const column = index % unassignedColumns;
    const row = Math.floor(index / unassignedColumns);
    const position = {
      x: unassignedCenterX + (column - (unassignedColumns - 1) / 2) * C.nodeSpacing,
      y: unassignedRecord.center.y - unassignedHeight / 2 + C.nodeSize / 2,
      z: (row - (unassignedRows - 1) / 2) * C.nodeSpacing,
    };
    unassignedRecord.nodeIds.push(nodeId);
    addNode(node, position, {
      buildingId: null,
      floorId: null,
      roomId: null,
      unassigned: true,
    });
  });

  // The bounds of the whole scene.
  const min = { x: Infinity, y: Infinity, z: Infinity };
  const max = { x: -Infinity, y: -Infinity, z: -Infinity };
  const grow = (point) => {
    min.x = Math.min(min.x, point.x);
    min.y = Math.min(min.y, point.y);
    min.z = Math.min(min.z, point.z);
    max.x = Math.max(max.x, point.x);
    max.y = Math.max(max.y, point.y);
    max.z = Math.max(max.z, point.z);
  };
  for (const building of buildings) {
    grow({ x: building.center.x - building.size.x / 2, y: 0, z: building.center.z - building.size.z / 2 });
    grow({ x: building.center.x + building.size.x / 2, y: building.size.y, z: building.center.z + building.size.z / 2 });
  }
  for (const room of rooms) {
    grow({ x: room.center.x - room.size.x / 2, y: room.center.y - room.size.y / 2, z: room.center.z - room.size.z / 2 });
    grow({ x: room.center.x + room.size.x / 2, y: room.center.y + room.size.y / 2, z: room.center.z + room.size.z / 2 });
  }
  for (const node of Object.values(nodes)) grow(node.position);
  if (!Number.isFinite(min.x)) {
    min.x = 0; min.y = 0; min.z = 0;
    max.x = 0; max.y = 0; max.z = 0;
  }

  const bounds = {
    min: { ...min },
    max: { ...max },
    center: {
      x: (min.x + max.x) / 2,
      y: (min.y + max.y) / 2,
      z: (min.z + max.z) / 2,
    },
  };
  bounds.radius = 0.5 * distance3(bounds.min, bounds.max);

  const buildingsById = {};
  for (const building of buildings) buildingsById[building.id] = building;
  const floorsById = {};
  for (const floor of floors) floorsById[floor.id] = floor;
  const roomsById = {};
  for (const room of rooms) roomsById[room.id] = room;

  return {
    buildings,
    floors,
    rooms,
    nodes,
    buildingsById,
    floorsById,
    roomsById,
    nodesById: nodes,
    unassignedRegionId: UNASSIGNED_REGION_ID,
    unassignedNodeIds,
    bounds,
    constants: { ...C },
  };
}

/**
 * Classify every edge of the space.
 *
 * The four cross-space relations come from the vocabulary: `same-room`,
 * `cross-room`, `cross-floor` and `cross-building`. An edge that touches the
 * unassigned region has no place in that taxonomy, so its `relation` is null
 * and its `scope` is `unassigned`. The renderer never invents a level for it.
 *
 * @param {object} space a `SpaceModel` or a plain space object
 * @param {Array<object>|Map<string, object>} edges the edges
 * @param {object} [model] the canonical model. It is required when `space` is
 *   a plain object: without the node list every relation would come out as
 *   null, because a node without a location has no place in the taxonomy.
 * @returns {Array<object>} one record per edge
 */
export function classifyEdgeRelations(space, edges = [], model = null) {
  const spaceModel = resolveSpaceModel(space, model);
  const list = Array.isArray(edges)
    ? edges
    : (edges && typeof edges.values === 'function' ? [...edges.values()] : []);
  return list.map((edge) => {
    const sourceId = edge.sourceId ?? edge.source ?? null;
    const targetId = edge.targetId ?? edge.target ?? null;
    const relation = sourceId && targetId
      ? spaceModel.classifyRelation(sourceId, targetId)
      : null;
    const scope = relation ? 'assigned' : 'unassigned';
    return {
      edgeId: edge.id,
      sourceId,
      targetId,
      relation,
      scope,
      crossesBoundary: relation !== null && relation !== 'same-room',
      trustBoundary: edge.trustBoundary === true,
    };
  });
}

/**
 * Turn raycast hits into a pick result.
 *
 * The rule is one line and it is the whole point of the two pass pick: a node
 * hit always wins over an edge hit. The edge hit is only reported when no node
 * was under the pointer.
 *
 * @param {Array<{object: {userData?: object}}>} nodeHits the hits of the node meshes
 * @param {Array<{object: {userData?: object}}>} edgeHits the hits of the edge pick tubes
 * @returns {{kind: string, id: string}|null} the pick result
 */
export function resolvePickHit(nodeHits, edgeHits) {
  const node = nodeHits && nodeHits.length ? nodeHits[0] : null;
  const edge = edgeHits && edgeHits.length ? edgeHits[0] : null;
  const chosen = node || edge;
  if (!chosen) return null;
  const data = chosen.object?.userData || {};
  if (!data.pickKind || !data.pickId) return null;
  return { kind: data.pickKind, id: data.pickId };
}

/**
 * The visual treatment of every relation class, straight from the vocabulary.
 *
 * @param {string[]} [classIds] the relation classes to include
 * @returns {Record<string, object>} class id -> treatment
 */
export function relationClassTreatmentTable(classIds = RELATION_CLASS_IDS) {
  const table = {};
  for (const id of classIds) {
    const spec = relationClassSpec(id);
    if (!spec) continue;
    table[id] = { ...treatmentForRelationClass(id), rank: spec.rank };
  }
  return table;
}

/**
 * Turn a node id -> relation class map into node id -> treatment.
 *
 * @param {Map<string, string>|Record<string, string>|null} classes
 * @returns {Record<string, object>} entity id -> treatment
 */
export function relationClassTreatments(classes) {
  const table = relationClassTreatmentTable();
  const result = {};
  if (!classes) return result;
  const entries = classes instanceof Map ? [...classes.entries()] : Object.entries(classes);
  for (const [entityId, classId] of entries) {
    const treatment = table[classId] || treatmentForRelationClass(classId);
    if (treatment) result[entityId] = treatment;
  }
  return result;
}

/**
 * The distance at which a box exactly fills the frustum.
 *
 * A bounding *sphere* is a poor fit for an engineering space: the scene is
 * usually much wider than it is tall, so a sphere leaves half of the canvas
 * empty. This computes the distance from the box corners in the camera's own
 * axes, which is the classic "fit the box" computation.
 *
 * @param {{x: number, y: number, z: number}} center the centre of the box
 * @param {{x: number, y: number, z: number}} size the full size of the box
 * @param {number[]} direction the vector from the target to the camera
 * @param {number} [fov] the vertical field of view in degrees
 * @param {number} [aspect] the width / height of the canvas
 * @param {number} [margin] the extra room left around the box
 * @returns {number} the distance from the target to the camera
 */
export function fitDistanceToBox(center, size, direction, fov = 50, aspect = 16 / 9, margin = 1.08) {
  const dir = normalize3(direction);
  // The camera looks at the target, so its local +Z points back at the camera.
  const backward = dir;
  const right = normalize3(cross3({ x: 0, y: 1, z: 0 }, backward));
  const up = cross3(backward, right);
  const tanHalf = Math.tan((fov * Math.PI) / 360);
  const half = { x: size.x / 2, y: size.y / 2, z: size.z / 2 };

  let distance = 0;
  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const rel = { x: sx * half.x, y: sy * half.y, z: sz * half.z };
        const x = dot3(rel, right);
        const y = dot3(rel, up);
        const z = dot3(rel, backward);
        // The depth of the corner from the camera is `distance - z`.
        distance = Math.max(
          distance,
          Math.abs(x) / (tanHalf * aspect) + z,
          Math.abs(y) / tanHalf + z,
        );
      }
    }
  }
  return Math.max(distance * margin, 1);
}

/**
 * The camera placement of a navigation.
 *
 * @param {string} navigationId one of the eight navigations
 * @param {{layout?: object, buildingId?: string|null, floorId?: string|null,
 *          roomId?: string|null, nodeId?: string|null, edgeId?: string|null,
 *          fov?: number, aspect?: number}} [context]
 * @returns {{navigation: string, position: {x: number, y: number, z: number},
 *            target: {x: number, y: number, z: number}}}
 */
export function cameraForNavigation(navigationId, context = {}) {
  const spec = NAVIGATION_CAMERA[navigationId];
  if (!spec) {
    throw new GemArcError(SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '未知的空间导航', String(navigationId));
  }
  const layout = context.layout || null;
  const fov = context.fov ?? 50;
  const aspect = context.aspect ?? 16 / 9;
  const bounds = layout?.bounds || {
    center: { x: 0, y: 0, z: 0 },
    radius: 0,
  };

  let target = { ...bounds.center };
  let radius = bounds.radius || 0;
  // The box the camera has to frame. `null` means the navigation frames a
  // point instead of a volume and keeps the sphere based distance.
  let fitBox = layout?.bounds
    ? {
      center: { ...bounds.center },
      size: {
        x: Math.max(0.001, bounds.max.x - bounds.min.x),
        y: Math.max(0.001, bounds.max.y - bounds.min.y),
        z: Math.max(0.001, bounds.max.z - bounds.min.z),
      },
    }
    : null;

  const building = context.buildingId ? layout?.buildingsById?.[context.buildingId] : null;
  const floor = context.floorId ? layout?.floorsById?.[context.floorId] : null;
  const room = context.roomId ? layout?.roomsById?.[context.roomId] : null;
  const node = context.nodeId ? layout?.nodesById?.[context.nodeId] : null;

  if (navigationId === 'building-select' && building) {
    target = { ...building.center };
    radius = building.radius;
    fitBox = { center: { ...building.center }, size: { ...building.size } };
  } else if (navigationId === 'floor-switch' && floor) {
    target = { ...floor.center };
    radius = floor.radius;
    fitBox = { center: { ...floor.center }, size: { ...floor.size } };
  } else if (navigationId === 'floor-exploded' && floor) {
    target = { ...floor.center };
    radius = floor.radius;
    fitBox = { center: { ...floor.center }, size: { ...floor.size } };
  } else if ((navigationId === 'room-focus' || navigationId === 'room-cutaway') && room) {
    target = { ...room.center };
    radius = room.radius;
    fitBox = { center: { ...room.center }, size: { ...room.size } };
  } else if (navigationId === 'node-focus' && node) {
    target = { ...node.position };
    radius = LAYOUT_CONSTANTS.nodeSize;
    // A node is a point, not a volume: the sphere based distance stays.
    fitBox = null;
  } else if (navigationId === 'building-select' && layout?.buildings?.length) {
    target = { ...layout.buildings[0].center };
    radius = layout.buildings[0].radius;
    fitBox = { center: { ...layout.buildings[0].center }, size: { ...layout.buildings[0].size } };
  } else if ((navigationId === 'floor-switch' || navigationId === 'floor-exploded')
    && layout?.floors?.length) {
    target = { ...layout.floors[0].center };
    radius = layout.floors[0].radius;
    fitBox = { center: { ...layout.floors[0].center }, size: { ...layout.floors[0].size } };
  } else if ((navigationId === 'room-focus' || navigationId === 'room-cutaway')
    && layout?.rooms?.length) {
    target = { ...layout.rooms[0].center };
    radius = layout.rooms[0].radius;
    fitBox = { center: { ...layout.rooms[0].center }, size: { ...layout.rooms[0].size } };
  }

  const [dx, dy, dz] = spec.direction;
  const length = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
  const unit = { x: dx / length, y: dy / length, z: dz / length };
  const distance = fitBox
    ? fitDistanceToBox(fitBox.center, fitBox.size, unit, fov, aspect)
    : spec.base + radius * spec.factor;
  return {
    navigation: navigationId,
    target: { x: target.x, y: target.y, z: target.z },
    position: {
      x: target.x + unit.x * distance,
      y: target.y + unit.y * distance,
      z: target.z + unit.z * distance,
    },
  };
}

/**
 * The pick tolerance, in **pixels**.
 *
 * A world-space radius is not a target size: the same edge is one pixel wide
 * when the camera is close and ten pixels wide when it is far, and a thin
 * tube at an angle is nearly unhittable. The 2D view solved this with a
 * transparent band around the line; the 3D view measures the same thing in
 * screen space.
 */
export const PICK_TOLERANCE_PX = Object.freeze({
  node: 10,
  edge: 12,
});

/**
 * The distance from a point to a segment, in pixels.
 * @param {number} px the pointer x
 * @param {number} py the pointer y
 * @param {number} ax the start x of the segment
 * @param {number} ay the start y of the segment
 * @param {number} bx the end x of the segment
 * @param {number} by the end y of the segment
 * @returns {number}
 */
export function distancePointToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  let t = lengthSquared > 0
    ? ((px - ax) * dx + (py - ay) * dy) / lengthSquared
    : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/* ------------------------------------------------------------------ */
/* The renderer                                                        */
/* ------------------------------------------------------------------ */

/**
 * Resolve the canvas the renderer draws into.
 * @param {object} container the container element or a canvas
 * @returns {object} the canvas
 */
function resolveCanvas(container) {
  if (typeof container.getContext === 'function') return container;
  const doc = container.ownerDocument
    || (typeof document !== 'undefined' ? document : null);
  if (!doc || typeof doc.createElement !== 'function') {
    throw new GemArcError(
      SPACE_VIEWER_ERROR_CODES.NO_WEBGL,
      '无法为三维空间创建画布',
    );
  }
  const canvas = doc.createElement('canvas');
  canvas.className = 'gemarc-space-canvas';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.display = 'block';
  container.appendChild(canvas);
  return canvas;
}

/**
 * Create the WebGL context. A failure is explicit: the renderer throws
 * instead of degrading into a picture.
 * @param {object} canvas
 * @returns {object} the context
 */
function createWebGLContext(canvas) {
  const attributes = { antialias: true, alpha: false, powerPreference: 'high-performance' };
  let context = null;
  try {
    context = canvas.getContext('webgl2', attributes) || canvas.getContext('webgl', attributes) || null;
  } catch {
    context = null;
  }
  if (!context) {
    throw new GemArcError(
      SPACE_VIEWER_ERROR_CODES.NO_WEBGL,
      '无法创建 WebGL 上下文，三维空间无法渲染',
    );
  }
  return context;
}

/**
 * A small, dependency-free orbit camera.
 *
 * Mouse drag rotates, the wheel zooms, a right drag or a shift drag pans.
 * `three/addons` is not portable across the two profiles, so the controller
 * lives here.
 */
class SpaceCameraController {
  constructor(camera, element) {
    this.camera = camera;
    this.element = element;
    this.target = { x: 0, y: 0, z: 0 };
    this.distance = 80;
    this.theta = Math.PI * 0.25;
    this.phi = Math.PI * 0.32;
    this.minDistance = 4;
    this.maxDistance = 4000;
    this.dragging = null;
    this.moved = 0;
    this.listeners = [];
    this._bind();
  }

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
  }

  _onDown(event) {
    if (!event || typeof event.button !== 'number') return;
    const pan = event.button === 2 || event.button === 1 || event.shiftKey === true;
    this.dragging = {
      mode: pan ? 'pan' : 'rotate',
      x: event.clientX ?? 0,
      y: event.clientY ?? 0,
    };
    this.moved = 0;
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
    this.moved += Math.abs(dx) + Math.abs(dy);
    if (this.dragging.mode === 'rotate') {
      this.theta -= dx * 0.006;
      this.phi = Math.max(0.05, Math.min(Math.PI - 0.05, this.phi - dy * 0.006));
    } else {
      this._pan(dx, dy);
    }
    this.apply();
  }

  _onWheel(event) {
    if (!event) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    const delta = event.deltaY ?? 0;
    const factor = Math.exp(delta * 0.0012);
    this.distance = Math.max(
      this.minDistance,
      Math.min(this.maxDistance, this.distance * factor),
    );
    this.apply();
  }

  _endDrag() {
    this.dragging = null;
  }

  _onUp(event) {
    const wasDragging = Boolean(this.dragging);
    const moved = this.moved;
    this._endDrag();
    if (wasDragging && moved < 5 && typeof this.onClick === 'function') {
      this.onClick(event);
    }
  }

  _pan(dx, dy) {
    const scale = this.distance * 0.0016;
    const cos = Math.cos(this.theta);
    const sin = Math.sin(this.theta);
    // The camera right vector lies in the XZ plane.
    this.target.x -= (dx * cos) * scale;
    this.target.z += (dx * sin) * scale;
    this.target.y += dy * scale;
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
    this.camera.updateMatrixWorld();
  }

  dispose() {
    for (const [type, handler, options] of this.listeners) {
      if (this.element && typeof this.element.removeEventListener === 'function') {
        this.element.removeEventListener(type, handler, options);
      }
    }
    this.listeners = [];
  }
}

/**
 * Draw a text label as a sprite. The label states what the model states: a
 * container without a semantic source is marked as such, so the scene never
 * implies more than the model does.
 * @param {object} THREE the injected three namespace
 * @param {string} text the label text
 * @param {{color?: string, background?: string, height?: number}} [options]
 * @returns {object|null} the sprite
 */
function createLabel(THREE, text, options = {}) {
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  const color = options.color || '#dbe6f0';
  const background = options.background || 'rgba(11,15,20,0.82)';
  const fontSize = 44;
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return null;
  context.font = `${fontSize}px "Segoe UI", "Noto Sans CJK SC", system-ui, sans-serif`;
  const width = Math.max(8, Math.ceil(context.measureText(text).width) + 28);
  const height = fontSize + 20;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.font = `${fontSize}px "Segoe UI", "Noto Sans CJK SC", system-ui, sans-serif`;
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 14, height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const sprite = new THREE.Sprite(material);
  const worldHeight = options.height ?? 3.4;
  // The base scale keeps the aspect ratio of the measured text. A label is
  // later shrunk by a factor, and `setScalar` would destroy that ratio.
  const baseScale = { x: (width / height) * worldHeight, y: worldHeight };
  sprite.scale.set(baseScale.x, baseScale.y, 1);
  sprite.renderOrder = 10;
  sprite.userData.labelText = text;
  sprite.userData.labelCanvas = canvas;
  sprite.userData.baseScale = baseScale;
  return sprite;
}

/**
 * Orient and place a cylinder so that it spans from `a` to `b`.
 * @param {object} THREE the injected three namespace
 * @param {object} mesh the cylinder mesh
 * @param {object} a the start point
 * @param {object} b the end point
 */
function placeCylinder(THREE, mesh, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dz = b.z - a.z;
  const length = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.0001;
  mesh.position.set((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
  mesh.scale.set(1, length, 1);
  const direction = new THREE.Vector3(dx / length, dy / length, dz / length);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
}

/**
 * Build the renderer.
 *
 * @param {object} THREE the injected three namespace
 * @param {{container: object, model: object, space: object, store?: object|null,
 *          onSelectNode?: Function|null, onSelectEdge?: Function|null,
 *          onHover?: Function|null, onNavigate?: Function|null}} options
 * @returns {object} the renderer api
 */
export function createThreeSpaceRenderer(THREE, options = {}) {
  const {
    container,
    model,
    space,
    store = null,
    onSelectNode = null,
    onSelectEdge = null,
    onHover = null,
    onNavigate = null,
  } = options || {};

  if (!container) {
    throw new GemArcError(SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要容器元素');
  }
  if (!model || typeof model !== 'object') {
    throw new GemArcError(SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要规范化模型');
  }
  if (!space || typeof space !== 'object') {
    throw new GemArcError(SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要空间结构');
  }
  if (!THREE || typeof THREE !== 'object') {
    throw new GemArcError(SPACE_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要注入 THREE');
  }

  const canvas = resolveCanvas(container);
  const context = createWebGLContext(canvas);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, context, antialias: true, alpha: false });
  } catch {
    throw new GemArcError(
      SPACE_VIEWER_ERROR_CODES.NO_WEBGL,
      '无法初始化 WebGL 渲染器，三维空间无法渲染',
    );
  }
  renderer.setPixelRatio(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0f14);

  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 6000);
  const controls = new SpaceCameraController(camera, canvas);

  const ambient = new THREE.AmbientLight(0xffffff, 0.62);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 0.78);
  key.position.set(1, 2.4, 1.4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x4cc2ff, 0.28);
  fill.position.set(-1.4, 0.8, -1.1);
  scene.add(fill);

  const root = new THREE.Group();
  scene.add(root);
  const containerGroup = new THREE.Group();
  const edgeGroup = new THREE.Group();
  root.add(containerGroup, edgeGroup);

  const disposables = [];
  const track = (item) => {
    if (item) disposables.push(item);
    return item;
  };

  /** @type {Map<string, object>} node id -> mesh record */
  const nodeRecords = new Map();
  /** @type {Map<string, object>} edge id -> mesh record */
  const edgeRecords = new Map();
  /** @type {Map<string, object>} room id -> group */
  const roomGroups = new Map();
  /** @type {Map<string, object>} container id -> group */
  const containerRecords = new Map();
  /** @type {object[]} the node meshes the raycaster tests */
  const nodePickTargets = [];
  /** @type {Map<string, object>} node id -> display position */
  const displayPositions = new Map();

  let layout = null;
  let relationClasses = new Map();
  let highlight = { nodeIds: new Set(), edgeIds: new Set() };
  let navigation = 'overview';
  let contextState = { buildingId: null, floorId: null, roomId: null, nodeId: null, edgeId: null };
  let explodedFloorId = null;
  let hovered = null;
  let disposed = false;

  const spaceModel = resolveSpaceModel(space, model);

  /* --------------------------------------------------------------- layout */

  const getLayout = () => {
    if (!layout) layout = computeSpaceLayout(model, space);
    return layout;
  };

  const nodeDisplayPosition = (nodeId) => {
    const record = displayPositions.get(nodeId);
    if (record) return record;
    const node = getLayout().nodesById[nodeId];
    return node ? node.position : { x: 0, y: 0, z: 0 };
  };

  /* ---------------------------------------------------------------- build */

  const clearScene = () => {
    for (const item of disposables) {
      if (typeof item.dispose === 'function') {
        try {
          item.dispose();
        } catch {
          // A disposed material must never break the rebuild.
        }
      }
    }
    disposables.length = 0;
    containerGroup.clear();
    edgeGroup.clear();
    nodeRecords.clear();
    edgeRecords.clear();
    roomGroups.clear();
    containerRecords.clear();
    nodePickTargets.length = 0;
    displayPositions.clear();
  };

  const makeFrame = (THREE, size, color, opacity) => {
    const geometry = new THREE.BoxGeometry(size.x, size.y, size.z);
    track(geometry);
    const edges = new THREE.EdgesGeometry(geometry);
    track(edges);
    const material = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity,
    });
    track(material);
    const frame = new THREE.LineSegments(edges, material);
    return frame;
  };

  const makePlate = (THREE, size, color, opacity) => {
    const geometry = new THREE.BoxGeometry(size.x, size.y, size.z);
    track(geometry);
    const material = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity,
    });
    track(material);
    return new THREE.Mesh(geometry, material);
  };

  const buildContainer = (record, parent) => {
    const virtual = record.virtual === true;
    // The container is placed relative to its parent, so a room that lives
    // inside a room group does not end up at twice its own centre.
    const local = {
      x: record.center.x - parent.position.x,
      y: record.center.y - parent.position.y,
      z: record.center.z - parent.position.z,
    };
    const frame = makeFrame(
      THREE,
      record.size,
      virtual ? CONTAINER_COLOR_VIRTUAL : CONTAINER_COLOR,
      virtual ? 0.55 : 0.85,
    );
    frame.position.set(local.x, local.y, local.z);
    parent.add(frame);

    // Only a floor gets a filled slab: it is the ground of that storey. A
    // building or a room would otherwise become a translucent box that hides
    // everything inside it - the very thing the hierarchy is meant to show.
    let plate = null;
    if (record.level === 'Floor') {
      plate = makePlate(
        THREE,
        { x: record.size.x, y: Math.max(0.2, record.size.y), z: record.size.z },
        virtual ? 0x182030 : 0x1b2532,
        0.75,
      );
      plate.position.copy(frame.position);
      parent.add(plate);
    }

    // The label states the semantic source. A container without one is marked
    // explicitly, so a virtual organisation never masquerades as a real one.
    const suffix = virtual ? '（未声明语义来源）' : '';
    const label = createLabel(THREE, `${record.name}${suffix}`, {
      color: virtual ? '#8fa3b8' : '#dbe6f0',
      height: record.level === 'Room' ? 3.0 : 4.2,
    });
    if (label) {
      if (record.level === 'Room') {
        // The room signage sits in front of the room volume, so it never
        // collides with the slab of the storey above.
        label.position.set(local.x, local.y, local.z + record.size.z / 2 + 3.2);
      } else {
        label.position.set(local.x, local.y + record.size.y / 2 + 3.4, local.z);
      }
      parent.add(label);
    }

    containerRecords.set(record.id, { record, frame, plate, label });
    return containerRecords.get(record.id);
  };

  const buildRoom = (record) => {
    const group = new THREE.Group();
    group.position.set(record.center.x, record.center.y, record.center.z);
    group.userData = { pickKind: 'room', pickId: record.id };
    containerGroup.add(group);
    roomGroups.set(record.id, group);
    buildContainer(record, group);
    return group;
  };

  const buildNode = (node, parent, localPosition) => {
    const geometry = new THREE.BoxGeometry(
      LAYOUT_CONSTANTS.nodeSize,
      LAYOUT_CONSTANTS.nodeSize,
      LAYOUT_CONSTANTS.nodeSize,
    );
    track(geometry);
    const material = new THREE.MeshStandardMaterial({
      color: NODE_COLOR,
      transparent: true,
      opacity: 1,
      roughness: 0.55,
      metalness: 0.15,
    });
    track(material);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(localPosition.x, localPosition.y, localPosition.z);

    const outlineGeometry = new THREE.EdgesGeometry(geometry);
    track(outlineGeometry);
    const outlineMaterial = new THREE.LineBasicMaterial({
      color: 0xdbe6f0,
      transparent: true,
      opacity: 0,
    });
    track(outlineMaterial);
    const outline = new THREE.LineSegments(outlineGeometry, outlineMaterial);
    mesh.add(outline);

    const label = createLabel(THREE, node.name, { height: 2.4 });
    if (label) {
      // The label is a child of the node mesh, so its position is relative to
      // the node. Using the room-relative coordinates here would place the
      // label at twice the distance from the room.
      label.position.set(0, LAYOUT_CONSTANTS.nodeSize / 2 + 1.9, 0);
      mesh.add(label);
    }

    mesh.userData = { pickKind: 'node', pickId: node.id };
    parent.add(mesh);
    nodePickTargets.push(mesh);

    nodeRecords.set(node.id, {
      node,
      mesh,
      material,
      outline,
      outlineMaterial,
      label,
      group: parent,
      local: { x: localPosition.x, y: localPosition.y, z: localPosition.z },
      displayOffset: 0,
    });
    return mesh;
  };

  const buildEdgeVisual = (record) => {
    const color = record.trustBoundary
      ? EDGE_COLORS['trust-boundary']
      : (EDGE_COLORS[record.relation] || EDGE_COLORS.unassigned);

    // Every relation owns a group. The group carries the pick identity, so a
    // dashed relation and a solid one are the same pick target.
    const group = new THREE.Group();
    group.userData = { pickKind: 'edge', pickId: record.edgeId };
    edgeGroup.add(group);
    record.group = group;

    if (!record.trustBoundary) {
      const material = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.95,
      });
      track(material);
      const geometry = new THREE.CylinderGeometry(0.12, 0.12, 1, 8, 1, true);
      track(geometry);
      const tube = new THREE.Mesh(geometry, material);
      group.add(tube);
      record.tube = tube;
      record.material = material;
      record.dashes = [];
    } else {
      // A trust boundary edge is dashed: it is built from short tube segments
      // with gaps, so the dashes are real geometry, not a screen effect.
      record.tube = null;
      record.material = null;
      record.dashes = [];
      const dashMaterial = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.95,
      });
      track(dashMaterial);
      record.dashMaterial = dashMaterial;
      const dashGeometry = new THREE.CylinderGeometry(0.16, 0.16, 1, 8, 1, true);
      track(dashGeometry);
      record.dashGeometry = dashGeometry;
    }
  };

  const updateEdgeGeometry = () => {
    for (const record of edgeRecords.values()) {
      const a = nodeDisplayPosition(record.sourceId);
      const b = nodeDisplayPosition(record.targetId);
      const direction = { x: b.x - a.x, y: b.y - a.y, z: b.z - a.z };
      const length = Math.sqrt(
        direction.x * direction.x + direction.y * direction.y + direction.z * direction.z,
      );
      const unit = length > 0
        ? { x: direction.x / length, y: direction.y / length, z: direction.z / length }
        : { x: 0, y: 1, z: 0 };

      const radius = record.radius ?? 0.12;
      if (record.tube) {
        record.tube.visible = true;
        placeCylinder(THREE, record.tube, a, b);
        record.tube.scale.x = radius / 0.12;
        record.tube.scale.z = radius / 0.12;
      }
      if (record.dashes) {
        const count = Math.max(2, Math.min(24, Math.round(length / 3.2)));
        const step = length / count;
        const fill = step * 0.58;
        while (record.dashes.length < count) {
          const dash = new THREE.Mesh(record.dashGeometry, record.dashMaterial);
          record.group.add(dash);
          record.dashes.push(dash);
        }
        record.dashes.forEach((dash, index) => {
          const start = {
            x: a.x + unit.x * (step * index),
            y: a.y + unit.y * (step * index),
            z: a.z + unit.z * (step * index),
          };
          const end = {
            x: start.x + unit.x * fill,
            y: start.y + unit.y * fill,
            z: start.z + unit.z * fill,
          };
          placeCylinder(THREE, dash, start, end);
          dash.scale.x = radius / 0.16;
          dash.scale.z = radius / 0.16;
        });
        for (let index = count; index < record.dashes.length; index += 1) {
          record.dashes[index].visible = false;
        }
        for (let index = 0; index < count; index += 1) record.dashes[index].visible = true;
      }
    }
  };

  const build = () => {
    clearScene();
    const current = getLayout();

    for (const building of current.buildings) {
      buildContainer(building, containerGroup);
    }
    for (const floor of current.floors) {
      buildContainer(floor, containerGroup);
    }
    for (const room of current.rooms) {
      buildRoom(room);
    }

    for (const node of Object.values(current.nodes)) {
      const room = node.roomId ? roomGroups.get(node.roomId) : null;
      const parent = room || roomGroups.get(current.unassignedRegionId);
      if (!parent) continue;
      const local = {
        x: node.position.x - parent.position.x,
        y: node.position.y - parent.position.y,
        z: node.position.z - parent.position.z,
      };
      buildNode(node, parent, local);
    }

    const edges = store && typeof store.edges?.values === 'function'
      ? [...store.edges.values()]
      : asArray(model.edges);
    const classified = classifyEdgeRelations(spaceModel, edges);
    for (const record of classified) {
      const edge = store && typeof store.getEdge === 'function'
        ? store.getEdge(record.edgeId)
        : asArray(model.edges).find((item) => item.id === record.edgeId);
      const trustBoundary = record.trustBoundary
        || (edge && edge.trustBoundary === true);
      const entry = { ...record, trustBoundary, radius: 0.12 };
      edgeRecords.set(record.edgeId, entry);
      buildEdgeVisual(entry);
    }

    applyExplosion();
    updateEdgeGeometry();
    refreshVisuals();
  };

  /* -------------------------------------------------------------- visuals */

  /**
   * Place the room groups and recompute the display position of every node.
   *
   * The layout data is never touched. The exploded view and the display-only
   * z offset live in the scene graph, so both are reversible.
   */
  const applyExplosion = () => {
    const current = getLayout();
    for (const [roomId, group] of roomGroups) {
      const record = current.roomsById[roomId];
      if (!record) continue;
      const lift = group.userData.lift || 0;
      group.position.set(record.center.x, record.center.y + lift, record.center.z);
    }
    displayPositions.clear();
    for (const [nodeId, record] of nodeRecords) {
      const group = record.group;
      displayPositions.set(nodeId, {
        x: group.position.x + record.local.x,
        y: group.position.y + record.local.y,
        z: group.position.z + record.local.z + (record.displayOffset || 0),
      });
    }
  };

  const setExplodedFloor = (floorId) => {
    const current = getLayout();
    explodedFloorId = floorId && current.floorsById[floorId] ? floorId : null;

    // Every floor slab is restored first, so leaving the exploded view always
    // returns the scene to its normal state.
    for (const floor of current.floors) {
      const record = containerRecords.get(floor.id);
      if (record && record.plate) record.plate.visible = true;
    }
    for (const group of roomGroups.values()) group.userData.lift = 0;

    if (explodedFloorId) {
      const rooms = current.rooms.filter((room) => room.floorId === explodedFloorId);
      rooms.forEach((room, index) => {
        const group = roomGroups.get(room.id);
        if (!group) return;
        // The rooms of the exploded floor rise out of their slab.
        group.userData.lift = (index + 1) * LAYOUT_CONSTANTS.explodeStep;
      });
      const floorRecord = containerRecords.get(explodedFloorId);
      if (floorRecord && floorRecord.plate) floorRecord.plate.visible = false;
    }
    applyExplosion();
    updateEdgeGeometry();
  };

  const refreshVisuals = () => {
    const treatments = relationClassTreatments(relationClasses);

    for (const [nodeId, record] of nodeRecords) {
      const treatment = treatments[nodeId] || null;
      const isHighlighted = highlight.nodeIds.has(nodeId);
      const opacity = treatment?.opacity ?? 1;
      record.material.opacity = opacity;
      record.material.transparent = opacity < 1;
      record.material.color.setHex(isHighlighted ? NODE_COLOR_HIGHLIGHT : NODE_COLOR);

      const outlineName = treatment?.outline
        || (isHighlighted ? 'strong' : 'none');
      const strength = OUTLINE_STRENGTH[outlineName] || OUTLINE_STRENGTH.none;
      record.outlineMaterial.opacity = strength.opacity;
      record.outline.scale.setScalar(strength.scale);

      if (record.label) {
        const mode = treatment?.labels ?? 'full';
        record.label.visible = mode !== 'hidden';
        // The base scale carries the aspect ratio of the measured text. A
        // factor shrinks the label; `setScalar` would turn it into a square.
        const factor = mode === 'collapsed' ? 0.72 : 1;
        const base = record.label.userData.baseScale;
        if (base) record.label.scale.set(base.x * factor, base.y * factor, 1);
      }

      // The z offset is a display-only displacement along the world Z axis.
      // The layout data stays untouched, so the retreat is fully reversible.
      const offset = Z_OFFSET_DISPLAY[treatment?.zOffset ?? 'none'] ?? 0;
      record.displayOffset = offset;
      record.mesh.position.set(
        record.local.x,
        record.local.y,
        record.local.z + offset,
      );
    }

    for (const [edgeId, record] of edgeRecords) {
      const sourceClass = treatments[record.sourceId];
      const targetClass = treatments[record.targetId];
      let treatment = null;
      if (sourceClass || targetClass) {
        const candidates = [sourceClass, targetClass].filter(Boolean);
        candidates.sort((a, b) => a.rank - b.rank);
        treatment = candidates[0];
      }
      const isHighlighted = highlight.edgeIds.has(edgeId);
      const opacity = treatment?.opacity ?? 1;
      const weight = treatment?.lineWeight || 'normal';
      const radius = 0.12 * (LINE_WEIGHT_SCALE[weight] ?? 1) * (isHighlighted ? 1.6 : 1);
      record.radius = radius;
      record.opacity = opacity;
      if (record.material) {
        record.material.opacity = opacity;
        record.material.transparent = true;
      }
      if (record.dashMaterial) {
        record.dashMaterial.opacity = opacity;
        record.dashMaterial.transparent = true;
      }
    }

    // The relation lines follow the nodes they connect.
    applyExplosion();
    updateEdgeGeometry();
  };

  /* ---------------------------------------------------------------- picking */

  const raycaster = new THREE.Raycaster();
  const pointer = { x: 0, y: 0 };

  const toPointer = (event) => {
    const rect = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : null;
    const left = rect ? rect.left : 0;
    const top = rect ? rect.top : 0;
    const width = rect && rect.width ? rect.width : (canvas.clientWidth || 1);
    const height = rect && rect.height ? rect.height : (canvas.clientHeight || 1);
    const clientX = event.clientX ?? 0;
    const clientY = event.clientY ?? 0;
    pointer.x = ((clientX - left) / width) * 2 - 1;
    pointer.y = -((clientY - top) / height) * 2 + 1;
    return pointer;
  };

  /**
   * Project a world point onto the client rectangle of the canvas.
   * @param {{x: number, y: number, z: number}} point the world point
   * @returns {{x: number, y: number, onScreen: boolean}}
   */
  const projectToClient = (point) => {
    const vector = new THREE.Vector3(point.x, point.y, point.z).project(camera);
    const rect = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : null;
    const left = rect ? rect.left : 0;
    const top = rect ? rect.top : 0;
    const width = rect && rect.width ? rect.width : (canvas.clientWidth || 1);
    const height = rect && rect.height ? rect.height : (canvas.clientHeight || 1);
    return {
      x: left + (vector.x * 0.5 + 0.5) * width,
      y: top + (-vector.y * 0.5 + 0.5) * height,
      // A point behind the camera projects into a mirrored position.
      onScreen: vector.z >= -1 && vector.z <= 1,
    };
  };

  /**
   * Pick the object under the pointer.
   *
   * The measurement happens in **screen space**, because that is where the
   * pointer is. A node is found by its projected position and a relation by
   * the distance to its projected line; both use a tolerance in pixels, so
   * the target keeps its size at every camera distance and every angle.
   *
   * The node wins wherever the two overlap. That is the same rule the 2D view
   * follows: the node body is drawn on top of the lines.
   *
   * Node ids and edge ids live in separate id spaces: the kind travels with
   * the hit, so an edge hit can never be reported as a node.
   *
   * @param {MouseEvent} event the pointer event
   * @returns {{kind: string, id: string}|null}
   */
  const pick = (event) => {
    if (!nodePickTargets.length && !edgeRecords.size) return null;
    const rect = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : null;
    const px = event.clientX ?? 0;
    const py = event.clientY ?? 0;

    // The nodes and the edges are also tested with the raycaster: it knows
    // which object is in front, and it tells a real hit from a projection
    // that only looks close.
    const ndc = toPointer(event);
    scene.updateMatrixWorld(true);
    raycaster.setFromCamera({ x: ndc.x, y: ndc.y }, camera);
    const nodeHits = raycaster.intersectObjects(nodePickTargets, false);
    const raycastNode = nodeHits.length ? nodeHits[0] : null;

    let nodeCandidate = null;
    let nodeDistance = Infinity;
    for (const [nodeId] of nodeRecords) {
      const projected = projectToClient(nodeDisplayPosition(nodeId));
      if (!projected.onScreen) continue;
      const distance = Math.hypot(px - projected.x, py - projected.y);
      if (distance > PICK_TOLERANCE_PX.node) continue;
      // The raycast hit is the node in front, so it wins over a node that is
      // merely close on the screen.
      if (raycastNode && raycastNode.object.userData?.pickId === nodeId) {
        nodeCandidate = nodeId;
        nodeDistance = distance;
        break;
      }
      if (distance < nodeDistance) {
        nodeCandidate = nodeId;
        nodeDistance = distance;
      }
    }

    let edgeCandidate = null;
    let edgeDistance = Infinity;
    for (const record of edgeRecords.values()) {
      const source = projectToClient(nodeDisplayPosition(record.sourceId));
      const target = projectToClient(nodeDisplayPosition(record.targetId));
      if (!source.onScreen || !target.onScreen) continue;
      const distance = distancePointToSegment(px, py, source.x, source.y, target.x, target.y);
      if (distance > PICK_TOLERANCE_PX.edge) continue;
      if (distance < edgeDistance) {
        edgeCandidate = record.edgeId;
        edgeDistance = distance;
      }
    }

    // The node wins wherever the two overlap. That is the same rule the 2D
    // view follows: the node body is drawn on top of the lines.
    return resolvePickHit(
      nodeCandidate
        ? [{ object: { userData: { pickKind: 'node', pickId: nodeCandidate } } }]
        : [],
      edgeCandidate
        ? [{ object: { userData: { pickKind: 'edge', pickId: edgeCandidate } } }]
        : [],
    );
  };
  controls.onClick = (event) => {
    const hit = pick(event);
    if (!hit) {
      if (onNavigate) onNavigate('back-to-overview');
      return;
    }
    if (hit.kind === 'node') {
      if (onSelectNode) onSelectNode(hit.id);
    } else if (hit.kind === 'edge') {
      // An edge click selects the edge, never its source node.
      if (onSelectEdge) onSelectEdge(hit.id);
    }
  };

  const onPointerMove = (event) => {
    if (controls.dragging) return;
    const hit = pick(event);
    const key = hit ? `${hit.kind}:${hit.id}` : null;
    if (key === hovered) return;
    hovered = key;
    if (onHover) onHover(hit ? { kind: hit.kind, id: hit.id } : null);
  };

  const onDoubleClick = (event) => {
    const hit = pick(event);
    if (!hit) return;
    if (hit.kind === 'node') {
      const node = getLayout().nodesById[hit.id];
      if (node) {
        contextState = {
          buildingId: node.buildingId,
          floorId: node.floorId,
          roomId: node.roomId,
          nodeId: node.id,
          edgeId: null,
        };
      }
      if (onNavigate) onNavigate('node-focus');
    } else if (hit.kind === 'edge') {
      contextState = { ...contextState, edgeId: hit.id };
      if (onNavigate) onNavigate('node-focus');
    }
  };

  const onResize = () => {
    const width = container.clientWidth || canvas.clientWidth || 1;
    const height = container.clientHeight || canvas.clientHeight || 1;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  if (canvas && typeof canvas.addEventListener === 'function') {
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('dblclick', onDoubleClick);
  }
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', onResize);
  }

  /* --------------------------------------------------------------- the loop */

  let animationHandle = null;
  const animate = () => {
    if (disposed) return;
    // The loop only draws. Every position change belongs to `refreshVisuals`,
    // which owns the display-only z offset. A per-frame position write would
    // fight the layout and could silently move a node off its room floor.
    renderer.render(scene, camera);
    animationHandle = requestAnimationFrame(animate);
  };

  const startLoop = () => {
    if (typeof requestAnimationFrame === 'function') animationHandle = requestAnimationFrame(animate);
  };

  /* ------------------------------------------------------------------- api */

  const api = {
    /**
     * Rebuild the scene from the model.
     */
    render() {
      layout = computeSpaceLayout(model, space);
      build();
      onResize();
      // The canvas may have been laid out only now, so the framing is
      // recomputed against the real aspect ratio.
      controls.set(cameraForNavigation(navigation, {
        layout: getLayout(),
        ...contextState,
        fov: camera.fov,
        aspect: camera.aspect,
      }));
      if (!animationHandle && typeof requestAnimationFrame === 'function') startLoop();
      return api;
    },

    /**
     * Move the camera to a node and highlight it.
     * @param {string} nodeId
     * @returns {boolean} true when the node exists
     */
    focus(nodeId) {
      const current = getLayout();
      const node = current.nodesById[nodeId];
      if (!node) return false;
      contextState = {
        buildingId: node.buildingId,
        floorId: node.floorId,
        roomId: node.roomId,
        nodeId: node.id,
        edgeId: null,
      };
      const incident = new Set();
      for (const [edgeId, record] of edgeRecords) {
        if (record.sourceId === nodeId || record.targetId === nodeId) incident.add(edgeId);
      }
      highlight = { nodeIds: new Set([nodeId]), edgeIds: incident };
      refreshVisuals();
      const placement = cameraForNavigation('node-focus', {
        layout: current,
        ...contextState,
        fov: camera.fov,
        aspect: camera.aspect,
      });
      controls.set(placement);
      return true;
    },

    /**
     * Set the highlighted nodes and edges.
     * @param {{nodeIds?: string[], edgeIds?: string[]}} selection
     */
    setHighlight(selection = {}) {
      highlight = {
        nodeIds: new Set(asArray(selection.nodeIds)),
        edgeIds: new Set(asArray(selection.edgeIds)),
      };
      refreshVisuals();
      return api;
    },

    /**
     * Apply the vocabulary relation classes. Only the rendering changes: the
     * layout, the coordinates and the model are untouched.
     * @param {Map<string, string>|Record<string, string>} classes
     */
    setRelationClasses(classes) {
      relationClasses = classes instanceof Map
        ? new Map(classes)
        : new Map(Object.entries(classes || {}));
      refreshVisuals();
      return api;
    },

    /**
     * Move the camera the way the matching navigation does.
     * @param {string} navigationId one of the eight navigations
     * @param {object} [payload] optional ids of the target
     */
    navigate(navigationId, payload = {}) {
      const current = getLayout();
      const next = { ...contextState };
      if (payload.buildingId) next.buildingId = payload.buildingId;
      if (payload.floorId) next.floorId = payload.floorId;
      if (payload.roomId) next.roomId = payload.roomId;
      if (payload.nodeId) next.nodeId = payload.nodeId;
      if (payload.edgeId) next.edgeId = payload.edgeId;
      if (next.floorId && !next.buildingId) {
        const floor = current.floorsById[next.floorId];
        if (floor) next.buildingId = floor.buildingId;
      }
      if (next.roomId && !next.floorId) {
        const room = current.roomsById[next.roomId];
        if (room) next.floorId = room.floorId;
      }
      contextState = next;
      navigation = navigationId;
      setExplodedFloor(navigationId === 'floor-exploded' ? next.floorId : null);
      const placement = cameraForNavigation(navigationId, {
        layout: current,
        ...next,
        fov: camera.fov,
        aspect: camera.aspect,
      });
      controls.set(placement);
      return { navigation: navigationId, ...next, placement };
    },

    /**
     * Dispose every geometry, material and the renderer itself.
     */
    dispose() {
      disposed = true;
      if (animationHandle !== null && typeof cancelAnimationFrame === 'function') {
        cancelAnimationFrame(animationHandle);
        animationHandle = null;
      }
      if (canvas && typeof canvas.removeEventListener === 'function') {
        canvas.removeEventListener('pointermove', onPointerMove);
        canvas.removeEventListener('dblclick', onDoubleClick);
      }
      if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
        window.removeEventListener('resize', onResize);
      }
      controls.dispose();
      clearScene();
      if (typeof renderer.dispose === 'function') renderer.dispose();
      if (canvas && canvas.parentNode && typeof canvas.parentNode.removeChild === 'function') {
        canvas.parentNode.removeChild(canvas);
      }
      return api;
    },

    /**
     * The counts of the rendered space.
     * @returns {{buildings: number, floors: number, rooms: number, nodes: number, edges: number}}
     */
    stats() {
      const current = getLayout();
      return {
        buildings: current.buildings.length,
        floors: current.floors.length,
        rooms: current.rooms.length,
        nodes: Object.keys(current.nodes).length,
        edges: edgeRecords.size,
      };
    },

    /**
     * The layout the scene was built from.
     * @returns {object}
     */
    layout() {
      return getLayout();
    },

    /**
     * The current navigation id.
     * @returns {string}
     */
    currentNavigation() {
      return navigation;
    },

    /**
     * The camera placement of the current navigation.
     * @returns {object}
     */
    cameraState() {
      return {
        position: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
        target: { ...controls.target },
      };
    },

    canvas,
    context,
    renderer,
    scene,
    camera,
  };

  build();
  onResize();
  const initial = cameraForNavigation('overview', {
    layout: getLayout(),
    fov: camera.fov,
    aspect: camera.aspect,
  });
  controls.set(initial);
  if (typeof requestAnimationFrame === 'function') startLoop();

  return api;
}
