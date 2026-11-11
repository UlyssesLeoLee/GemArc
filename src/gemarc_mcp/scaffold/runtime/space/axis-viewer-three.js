/**
 * Real 3D renderer for the semantic-axis architecture view.
 *
 * The space renderer (`space-viewer-three.js`) draws where things are
 * deployed: buildings, floors, rooms. This renderer draws what things
 * *are*: three categorical axes taken from the model itself - layer, trust
 * zone, environment, and so on - with every entity placed in the cell its
 * own values put it in. It is what turns a canonical model into a Three.js
 * architecture document without the language model writing a line of
 * rendering code.
 *
 * Hard rules this module obeys:
 *
 *   1. **No third-party import.** `three` is injected by the caller (an
 *      import map for the static profile, `node_modules` for the vite
 *      profile, a CDN for the preview profile). The only import is the
 *      sibling module that holds the shared gestures, so a profile that
 *      ships the fewest files has to ship that one as well.
 *   2. **No silent degradation.** When the WebGL context cannot be created the
 *      renderer throws `GEMARC_AXIS_010`. It never falls back to a picture.
 *   3. **The axes come from the model.** A value the model does not carry is
 *      never invented: the entity is parked in the explicit *unset* slot of
 *      that axis and the fact is reported in the layout.
 *   4. **Deterministic layout.** The same dataset always produces the same
 *      scene. There is no `Math.random()` anywhere in this file.
 *   5. **Entities and relations are independently pickable.** Clicking a
 *      relation never selects its source entity, and the two id spaces stay
 *      apart.
 *   6. **An axis that carries no information is visible as such.** An axis
 *      whose entities all share one value is drawn, but the layout reports it
 *      as not independent instead of pretending it separates anything.
 *   7. **The gestures are Unity's.** The camera and the selection follow the
 *      Unity 2022.3 scene view: Alt + left drag orbits, the middle drag pans,
 *      the right drag flies, the wheel dollies, the left click selects,
 *      Ctrl + left click adds, a drag on empty space marquees, Esc clears and
 *      F frames. The mapping lives in `unity-controls.js`, so the two scenes
 *      cannot disagree about it.
 *
 * @module axis-viewer-three
 */

import { UnityControls, boundsOfPoints } from './unity-controls.js';
import { createToonGradient, createToonMaterial } from './toon-material.js';

/**
 * The diagnostic codes this module raises.
 * @type {Readonly<Record<string, string>>}
 */
export const AXIS_VIEWER_ERROR_CODES = Object.freeze({
  NO_WEBGL: 'GEMARC_AXIS_010',
  INVALID_ARGUMENT: 'GEMARC_AXIS_011',
  NO_AXES: 'GEMARC_AXIS_012',
});

/**
 * The layout constants. They are fixed so that the same dataset always
 * yields the same scene.
 * @type {Readonly<Record<string, number>>}
 */
export const AXIS_LAYOUT_CONSTANTS = Object.freeze({
  slotSpacing: 24,
  cellSpread: 4.2,
  nodeSize: 3.4,
  edgeRadius: 0.18,
  axisThickness: 0.22,
  tickLength: 1.8,
  axisMargin: 9,
  unsetOffset: 13,
  labelHeight: 3.2,
  valueLabelHeight: 2.6,
});

/**
 * The three axes, in the order they are reported.
 * @type {ReadonlyArray<string>}
 */
export const AXIS_KEYS = Object.freeze(['x', 'y', 'z']);

/**
 * The direction each axis runs in.
 * @type {Readonly<Record<string, Readonly<{x: number, y: number, z: number}>>>}
 */
export const AXIS_DIRECTIONS = Object.freeze({
  x: Object.freeze({ x: 1, y: 0, z: 0 }),
  y: Object.freeze({ x: 0, y: 1, z: 0 }),
  z: Object.freeze({ x: 0, y: 0, z: 1 }),
});

/**
 * One colour per axis, so a reader can tell the three directions apart.
 * @type {Readonly<Record<string, number>>}
 */
export const AXIS_COLORS = Object.freeze({
  x: 0x6fe3ff,
  y: 0xb78cff,
  z: 0xffc46b,
});

/**
 * One colour per entity kind of the Three.js architecture contract.
 * @type {Readonly<Record<string, number>>}
 */
export const KIND_COLORS = Object.freeze({
  actor: 0xff9a8b,
  system: 0x6fb3ff,
  service: 0xffc46b,
  process: 0xd59bff,
  module: 0x9ad36f,
  runtime: 0xc9d1d9,
  database: 0x7fb0ff,
  external: 0x8a94a6,
  gateway: 0xffd166,
  plugin: 0xc8f06a,
  security: 0xff7b9c,
  operations: 0x7fd1ff,
  data: 0x7fe3d4,
  storage: 0xc0a6ff,
});

/**
 * The colour of an entity whose kind is not registered. It is deliberately
 * muted: an unregistered kind is not the same thing as a known one.
 * @type {number}
 */
export const UNKNOWN_KIND_COLOR = 0x8d97a3;

/**
 * The golden angle. Entities that share a cell are spread along a spiral
 * instead of being stacked on top of each other. It is a constant, not a
 * random number: the same dataset always produces the same scene.
 * @type {number}
 */
const GOLDEN_ANGLE = 2.399963229728653;

/**
 * The error this module raises.
 */
export class AxisViewerError extends Error {
  /**
   * @param {string} code the diagnostic code
   * @param {string} message the message
   */
  constructor(code, message) {
    super(message);
    this.name = 'AxisViewerError';
    this.code = code;
  }
}

/**
 * Read the ordered values of one axis and the coordinate of every slot.
 * @param {object} plan the axis plan
 * @param {number} spacing the distance between two slots
 * @returns {object} the plan with its slots
 */
function planSlots(plan, spacing) {
  const count = plan.values.length;
  const first = -((count - 1) / 2) * spacing;
  plan.slots = plan.values.map((value, index) => ({
    value,
    index,
    coordinate: first + index * spacing,
  }));
  plan.first = count ? first : 0;
  plan.last = count ? first + (count - 1) * spacing : 0;
  plan.unsetCoordinate = plan.first - spacing;
  return plan;
}

/**
 * The deterministic position of an entity inside its cell.
 *
 * Entities that share the same three values would sit on the same point, so
 * they are spread along a spiral. The spread never reaches the next slot:
 * an entity must stay inside the cell the model put it in.
 * @param {number} index the index of the entity inside its cell
 * @param {number} spread the maximum spread
 * @param {number} limit the distance that keeps the entity inside its cell
 * @returns {{x: number, y: number, z: number}}
 */
function spiralOffset(index, spread, limit) {
  if (index <= 0) return { x: 0, y: 0, z: 0 };
  const angle = index * GOLDEN_ANGLE;
  const radius = Math.min(spread * Math.sqrt(index), limit);
  return {
    x: Math.cos(angle) * radius,
    y: Math.sin(angle) * radius * 0.6,
    z: Math.cos(angle * 0.5) * radius * 0.45,
  };
}

/**
 * Compute the deterministic layout of one semantic-axis view.
 *
 * The function is pure: it takes the derived dataset and returns where every
 * entity sits. It never touches the DOM, so a test can check the geometry
 * without a browser.
 * @param {object} view one view of the derived 3D dataset
 * @param {{constants?: object}} [options]
 * @returns {object} the layout
 */
export function computeAxisLayout(view, options = {}) {
  if (!view || typeof view !== 'object') {
    throw new AxisViewerError(AXIS_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维布局需要视图数据');
  }
  const constants = { ...AXIS_LAYOUT_CONSTANTS, ...(options.constants || {}) };
  const axes = view.axes && typeof view.axes === 'object' ? view.axes : {};

  const plans = [];
  for (const key of AXIS_KEYS) {
    const entry = axes[key];
    if (!entry || typeof entry !== 'object') continue;
    const values = Array.isArray(entry.values)
      ? entry.values.filter((value) => typeof value === 'string')
      : [];
    if (values.length === 0) continue;
    plans.push(planSlots({
      key,
      field: typeof entry.field === 'string' ? entry.field : key,
      label: typeof entry.label === 'string' && entry.label ? entry.label
        : (typeof entry.field === 'string' ? entry.field : key),
      values,
      independent: !view.independent || view.independent[key] !== false,
    }, constants.slotSpacing));
  }
  if (plans.length === 0) {
    throw new AxisViewerError(
      AXIS_VIEWER_ERROR_CODES.NO_AXES,
      '视图没有声明带取值的坐标轴，无法建立三维语义',
    );
  }

  const nodes = [];
  const positions = new Map();
  const cellCounts = new Map();
  for (const node of Array.isArray(view.nodes) ? view.nodes : []) {
    if (!node || typeof node !== 'object' || typeof node.id !== 'string') continue;
    const position = { x: 0, y: 0, z: 0 };
    const cell = {};
    const unmappedAxes = [];
    for (const plan of plans) {
      const raw = node.values ? node.values[plan.key] : null;
      const slot = typeof raw === 'string'
        ? plan.slots.find((item) => item.value === raw)
        : null;
      cell[plan.key] = slot ? slot.index : -1;
      if (!slot) unmappedAxes.push(plan.key);
      position[plan.key] = slot ? slot.coordinate : plan.unsetCoordinate;
    }
    const signature = plans.map((plan) => String(cell[plan.key])).join('|');
    const index = cellCounts.get(signature) || 0;
    cellCounts.set(signature, index + 1);
    const offset = spiralOffset(index, constants.cellSpread, constants.slotSpacing / 3);
    const placed = {
      x: position.x + offset.x,
      y: position.y + offset.y,
      z: position.z + offset.z,
    };
    positions.set(node.id, placed);
    nodes.push({
      id: node.id,
      name: typeof node.name === 'string' ? node.name : node.id,
      kind: typeof node.threeDKind === 'string' ? node.threeDKind : null,
      mcpKind: typeof node.mcpKind === 'string' ? node.mcpKind : null,
      values: node.values && typeof node.values === 'object' ? node.values : {},
      cell,
      unmappedAxes,
      position: placed,
    });
  }

  const edges = [];
  for (const edge of Array.isArray(view.edges) ? view.edges : []) {
    if (!edge || typeof edge !== 'object') continue;
    const from = positions.get(edge.sourceId);
    const to = positions.get(edge.targetId);
    if (!from || !to) continue;
    edges.push({
      id: edge.id,
      sourceId: edge.sourceId,
      targetId: edge.targetId,
      kind: typeof edge.threeDType === 'string' ? edge.threeDType : null,
      from,
      to,
    });
  }

  const min = { x: Infinity, y: Infinity, z: Infinity };
  const max = { x: -Infinity, y: -Infinity, z: -Infinity };
  const grow = (point) => {
    for (const key of AXIS_KEYS) {
      if (point[key] < min[key]) min[key] = point[key];
      if (point[key] > max[key]) max[key] = point[key];
    }
  };
  for (const node of nodes) grow(node.position);
  for (const plan of plans) {
    grow(plan.first);
    grow(plan.last);
  }
  if (!nodes.length) {
    for (const key of AXIS_KEYS) {
      min[key] = 0;
      max[key] = 0;
    }
  }
  const center = {
    x: (min.x + max.x) / 2,
    y: (min.y + max.y) / 2,
    z: (min.z + max.z) / 2,
  };
  const radius = Math.max(
    12,
    Math.max(
      Math.abs(max.x - min.x),
      Math.abs(max.y - min.y),
      Math.abs(max.z - min.z),
    ) / 2 + constants.axisMargin,
  );

  return {
    axes: plans.map((plan) => ({
      key: plan.key,
      field: plan.field,
      label: plan.label,
      values: plan.values,
      independent: plan.independent,
      first: plan.first,
      last: plan.last,
      unsetCoordinate: plan.unsetCoordinate,
      slots: plan.slots,
    })),
    nodes,
    edges,
    bounds: { min, max, center, radius },
    constants,
  };
}

/**
 * The colour of an entity kind.
 * @param {string|null} kind the kind
 * @returns {number} the colour
 */
export function kindColor(kind) {
  if (typeof kind !== 'string') return UNKNOWN_KIND_COLOR;
  return Object.prototype.hasOwnProperty.call(KIND_COLORS, kind)
    ? KIND_COLORS[kind]
    : UNKNOWN_KIND_COLOR;
}

/**
 * A brighter version of a colour: the edge of a crystal is always brighter
 * than its body.
 * @param {number} color the colour
 * @param {number} amount how much white is mixed in, 0 to 1
 * @returns {number} the brighter colour
 */
export function brighten(color, amount = 0.45) {
  const red = (color >> 16) & 0xff;
  const green = (color >> 8) & 0xff;
  const blue = color & 0xff;
  const mix = (channel) => Math.round(channel + (255 - channel) * amount);
  return (mix(red) << 16) | (mix(green) << 8) | mix(blue);
}

/**
 * Draw a text label as a sprite.
 * @param {object} THREE the injected three namespace
 * @param {string} text the label text
 * @param {{color?: string, background?: string, height?: number}} [options]
 * @returns {object|null} the sprite
 */
function createLabel(THREE, text, options = {}) {
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  const color = options.color || '#e6eef7';
  const background = options.background || 'rgba(6,9,14,0.82)';
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
  const worldHeight = options.height ?? 3.2;
  // The scale keeps the aspect ratio of the measured text.
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
 * The distance from a point to a segment, in screen space.
 * @param {number} px the x of the point
 * @param {number} py the y of the point
 * @param {number} ax the x of the segment start
 * @param {number} ay the y of the segment start
 * @param {number} bx the x of the segment end
 * @param {number} by the y of the segment end
 * @returns {number} the distance
 */
export function screenDistanceToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lengthSquared;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/**
 * Build the renderer.
 * @param {object} THREE the injected three namespace
 * @param {{container: object, view?: object, layout?: object,
 *          onSelectNode?: Function|null, onSelectEdge?: Function|null,
 *          onClearSelection?: Function|null,
 *          onHover?: Function|null}} options
 * @returns {object} the renderer api
 */
export function createThreeAxisRenderer(THREE, options = {}) {
  const {
    container,
    view = null,
    layout: givenLayout = null,
    onSelectNode = null,
    onSelectEdge = null,
    onClearSelection = null,
    onHover = null,
  } = options || {};

  if (!container) {
    throw new AxisViewerError(AXIS_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要容器元素');
  }
  if (!THREE || typeof THREE !== 'object') {
    throw new AxisViewerError(AXIS_VIEWER_ERROR_CODES.INVALID_ARGUMENT, '三维渲染器需要注入 THREE');
  }
  const layout = givenLayout || computeAxisLayout(view);

  const doc = container.ownerDocument
    || (typeof document !== 'undefined' ? document : null);
  if (!doc || typeof doc.createElement !== 'function') {
    throw new AxisViewerError(AXIS_VIEWER_ERROR_CODES.NO_WEBGL, '无法为三维语义创建画布');
  }
  const canvas = doc.createElement('canvas');
  canvas.className = 'gemarc-axis-canvas';
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.display = 'block';
  container.appendChild(canvas);

  const attributes = { antialias: true, alpha: false, powerPreference: 'high-performance' };
  let context = null;
  try {
    context = canvas.getContext('webgl2', attributes) || canvas.getContext('webgl', attributes) || null;
  } catch {
    context = null;
  }
  if (!context) {
    throw new AxisViewerError(
      AXIS_VIEWER_ERROR_CODES.NO_WEBGL,
      '无法创建 WebGL 上下文，三维语义无法渲染',
    );
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, context, antialias: true, alpha: false });
  } catch {
    throw new AxisViewerError(
      AXIS_VIEWER_ERROR_CODES.NO_WEBGL,
      '无法初始化 WebGL 渲染器，三维语义无法渲染',
    );
  }
  renderer.setPixelRatio(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06090e);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 6000);
  // The camera and the selection follow the Unity gestures. The mapping is
  // shared with the space scene, so the two can never disagree.
  const controls = new UnityControls(camera, canvas, {
    distance: 120,
    minDistance: 8,
    pick: (event) => pick(event),
    pickInRect: (rect) => pickInRect(rect),
    onSelect: (hit, selection) => {
      if (hit.kind === 'node' && onSelectNode) onSelectNode(hit.id, selection);
      if (hit.kind === 'edge' && onSelectEdge) onSelectEdge(hit.id, selection);
    },
    onClear: () => {
      if (onClearSelection) onClearSelection();
    },
    getBounds: () => layout.bounds,
    getSelectionBounds: () => selectionBounds(),
  });

  const ambient = new THREE.AmbientLight(0xffffff, 0.66);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 0.8);
  key.position.set(1, 2.4, 1.4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x4cc2ff, 0.3);
  fill.position.set(-1.4, 0.8, -1.1);
  scene.add(fill);

  const root = new THREE.Group();
  scene.add(root);
  const axisGroup = new THREE.Group();
  const nodeGroup = new THREE.Group();
  const edgeGroup = new THREE.Group();
  root.add(axisGroup, nodeGroup, edgeGroup);

  const disposables = [];
  const track = (item) => {
    if (item) disposables.push(item);
    return item;
  };

  const constants = layout.constants || AXIS_LAYOUT_CONSTANTS;

  /* ------------------------------------------------------------ the axes */

  for (const plan of layout.axes) {
    const direction = AXIS_DIRECTIONS[plan.key];
    const color = AXIS_COLORS[plan.key] || 0x9aa5b1;
    const from = plan.first - constants.axisMargin;
    const to = plan.last + constants.axisMargin;
    const length = to - from;
    const mid = (from + to) / 2;

    const geometry = new THREE.BoxGeometry(
      direction.x ? length : constants.axisThickness,
      direction.y ? length : constants.axisThickness,
      direction.z ? length : constants.axisThickness,
    );
    track(geometry);
    const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 });
    track(material);
    const bar = new THREE.Mesh(geometry, material);
    bar.position.set(direction.x * mid, direction.y * mid, direction.z * mid);
    axisGroup.add(bar);

    for (const slot of plan.slots) {
      const tickGeometry = new THREE.BoxGeometry(
        direction.x ? constants.tickLength : constants.axisThickness * 2,
        direction.y ? constants.tickLength : constants.axisThickness * 2,
        direction.z ? constants.tickLength : constants.axisThickness * 2,
      );
      track(tickGeometry);
      const tickMaterial = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 });
      track(tickMaterial);
      const tick = new THREE.Mesh(tickGeometry, tickMaterial);
      tick.position.set(
        direction.x * slot.coordinate,
        direction.y * slot.coordinate,
        direction.z * slot.coordinate,
      );
      axisGroup.add(tick);

      const label = createLabel(THREE, slot.value, {
        height: constants.valueLabelHeight,
        color: '#e6eef7',
      });
      if (label) {
        label.position.set(
          direction.x * slot.coordinate + direction.y * constants.valueLabelHeight * 1.6,
          direction.y * slot.coordinate + direction.z * constants.valueLabelHeight * 1.6,
          direction.z * slot.coordinate + direction.x * constants.valueLabelHeight * 1.6,
        );
        axisGroup.add(label);
      }
    }

    const axisLabel = createLabel(THREE, `${plan.key.toUpperCase()} · ${plan.label}`, {
      height: constants.labelHeight,
      color: `#${color.toString(16).padStart(6, '0')}`,
    });
    if (axisLabel) {
      axisLabel.position.set(
        direction.x * to + direction.y * constants.labelHeight * 1.4,
        direction.y * to + direction.z * constants.labelHeight * 1.4,
        direction.z * to + direction.x * constants.labelHeight * 1.4,
      );
      axisGroup.add(axisLabel);
    }
  }

  /* --------------------------------------------------------- the entities */

  /** @type {Map<string, object>} entity id -> record */
  const nodeRecords = new Map();
  /** @type {Map<string, object>} relation id -> record */
  const edgeRecords = new Map();
  /** @type {object[]} the meshes the raycaster tests */
  const nodePickTargets = [];

  // One gradient map for the whole scene (BD-010 section 13): the light is
  // banded into three steps on the cut faces, and the map never changes, so
  // the same model renders the same pixels twice.
  const toonGradient = track(createToonGradient(THREE));

  for (const node of layout.nodes) {
    const color = kindColor(node.kind);
    // A crystal is a cut polyhedron, not a ball: the cut planes are what
    // catch the light, and the bright edge is what makes it read as a gem.
    const geometry = new THREE.OctahedronGeometry(constants.nodeSize * 0.74, 0);
    track(geometry);
    const material = createToonMaterial(THREE, {
      color,
      opacity: 0.62,
      gradient: toonGradient,
    });
    track(material);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(node.position.x, node.position.y, node.position.z);
    mesh.userData.nodeId = node.id;
    nodeGroup.add(mesh);
    nodePickTargets.push(mesh);

    const outlineGeometry = new THREE.EdgesGeometry(geometry);
    track(outlineGeometry);
    const outlineMaterial = new THREE.LineBasicMaterial({
      color: brighten(color, 0.5),
      transparent: true,
      opacity: 0.8,
    });
    track(outlineMaterial);
    const outline = new THREE.LineSegments(outlineGeometry, outlineMaterial);
    mesh.add(outline);

    const label = createLabel(THREE, node.name, { height: constants.labelHeight * 0.8 });
    if (label) {
      label.position.set(0, constants.nodeSize * 0.75 + constants.labelHeight * 0.5, 0);
      mesh.add(label);
    }

    nodeRecords.set(node.id, { node, mesh, material, outlineMaterial, baseColor: color });
  }

  for (const edge of layout.edges) {
    const geometry = new THREE.CylinderGeometry(
      constants.edgeRadius,
      constants.edgeRadius,
      1,
      8,
      1,
      true,
    );
    track(geometry);
    const material = new THREE.MeshBasicMaterial({
      color: 0x7f8fa6,
      transparent: true,
      opacity: 0.42,
    });
    track(material);
    const mesh = new THREE.Mesh(geometry, material);
    placeCylinder(THREE, mesh, edge.from, edge.to);
    mesh.userData.edgeId = edge.id;
    edgeGroup.add(mesh);
    edgeRecords.set(edge.id, { edge, mesh, material });
  }

  /* --------------------------------------------------------------- picking */

  const raycaster = new THREE.Raycaster();
  const pointer = { x: 0, y: 0 };
  let hover = null;
  let highlight = { nodeIds: new Set(), edgeIds: new Set() };

  /**
   * The pointer position in normalised device coordinates.
   * @param {object} event the pointer event
   * @returns {{x: number, y: number}}
   */
  function pointerNdc(event) {
    const rect = typeof canvas.getBoundingClientRect === 'function'
      ? canvas.getBoundingClientRect()
      : { left: 0, top: 0, width: canvas.clientWidth || 1, height: canvas.clientHeight || 1 };
    const width = rect.width || 1;
    const height = rect.height || 1;
    return {
      x: ((event.clientX ?? 0) - rect.left) / width * 2 - 1,
      y: -(((event.clientY ?? 0) - rect.top) / height) * 2 + 1,
    };
  }

  /**
   * Project a world point onto the canvas, in pixels.
   * @param {object} point the world point
   * @param {object} rect the canvas rectangle
   * @returns {{x: number, y: number}|null} the pixel position, or null when
   *   the point is behind the camera
   */
  function projectToScreen(point, rect) {
    const vector = new THREE.Vector3(point.x, point.y, point.z);
    vector.project(camera);
    if (vector.z > 1) return null;
    return {
      x: rect.left + (vector.x * 0.5 + 0.5) * rect.width,
      y: rect.top + (-vector.y * 0.5 + 0.5) * rect.height,
    };
  }

  /**
   * What is under the pointer: an entity, a relation, or nothing.
   * @param {object} event the pointer event
   * @returns {{kind: string, id: string}|null} the hit
   */
  function pick(event) {
    const ndc = pointerNdc(event);
    pointer.x = ndc.x;
    pointer.y = ndc.y;
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(nodePickTargets, false);
    if (hits && hits.length > 0) {
      const hit = hits[0];
      const id = hit.object && hit.object.userData ? hit.object.userData.nodeId : null;
      if (id) return { kind: 'node', id };
    }
    // A relation is picked in screen space: the reader points at the line
    // between two entities, not at a body.
    const rect = typeof canvas.getBoundingClientRect === 'function'
      ? canvas.getBoundingClientRect()
      : { left: 0, top: 0, width: canvas.clientWidth || 1, height: canvas.clientHeight || 1 };
    const px = event.clientX ?? 0;
    const py = event.clientY ?? 0;
    let best = null;
    let bestDistance = 8;
    for (const record of edgeRecords.values()) {
      const from = projectToScreen(record.edge.from, rect);
      const to = projectToScreen(record.edge.to, rect);
      if (!from || !to) continue;
      const distance = screenDistanceToSegment(px, py, from.x, from.y, to.x, to.y);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = { kind: 'edge', id: record.edge.id };
      }
    }
    return best;
  }

  /**
   * Everything the marquee rectangle covers.
   *
   * An entity is inside the rectangle when its own position is. A relation is
   * inside when both of its ends are: half a relation is not a relation.
   * @param {{left: number, top: number, right: number, bottom: number}} rect
   *   the rectangle in client coordinates
   * @returns {Array<{kind: string, id: string}>} the hits
   */
  function pickInRect(rect) {
    const canvasRect = typeof canvas.getBoundingClientRect === 'function'
      ? canvas.getBoundingClientRect()
      : { left: 0, top: 0, width: canvas.clientWidth || 1, height: canvas.clientHeight || 1 };
    const inside = (point) => point
      && point.x >= rect.left && point.x <= rect.right
      && point.y >= rect.top && point.y <= rect.bottom;
    const hits = [];
    const covered = new Set();
    for (const node of layout.nodes) {
      const point = projectToScreen(node.position, canvasRect);
      if (!inside(point)) continue;
      covered.add(node.id);
      hits.push({ kind: 'node', id: node.id });
    }
    for (const edge of layout.edges) {
      if (!covered.has(edge.sourceId) || !covered.has(edge.targetId)) continue;
      hits.push({ kind: 'edge', id: edge.id });
    }
    return hits;
  }

  /**
   * The bounds of the selected entities, so that F can frame them.
   * @returns {{center: object, radius: number}|null} the bounds
   */
  function selectionBounds() {
    const points = [];
    for (const node of layout.nodes) {
      if (highlight.nodeIds.has(node.id)) points.push(node.position);
    }
    return boundsOfPoints(points);
  }

  const onPointerMove = (event) => {
    const hit = pick(event);
    const changed = (hit && hover ? `${hit.kind}:${hit.id}` : '')
      !== (hover && hover ? `${hover.kind}:${hover.id}` : '');
    hover = hit;
    if (typeof onHover === 'function') onHover(hit);
    if (changed && canvas.style) canvas.style.cursor = hit ? 'pointer' : 'default';
  };

  if (typeof canvas.addEventListener === 'function') {
    canvas.addEventListener('pointermove', onPointerMove);
  }

  /* ------------------------------------------------------------- highlight */

  /**
   * Mark the entities and relations the reader selected.
   * @param {{nodeIds?: string[], edgeIds?: string[]}} selection the selection
   */
  function setHighlight(selection = {}) {
    const nodeIds = new Set(Array.isArray(selection.nodeIds) ? selection.nodeIds : []);
    const edgeIds = new Set(Array.isArray(selection.edgeIds) ? selection.edgeIds : []);
    highlight = { nodeIds, edgeIds };
    for (const [id, record] of nodeRecords) {
      const active = nodeIds.has(id);
      // The body stays transparent; the selection is carried by the edge,
      // which is the brightest line of the crystal.
      record.material.opacity = active ? 0.88 : 0.58;
      record.outlineMaterial.opacity = active ? 1 : 0.75;
    }
    for (const [id, record] of edgeRecords) {
      const active = edgeIds.has(id);
      record.material.opacity = active ? 0.95 : 0.42;
      record.material.color = active ? 0x6fe3ff : 0x7f8fa6;
    }
  }

  /* ------------------------------------------------------------- the frame */

  let frame = null;

  function resize() {
    const width = Math.max(1, container.clientWidth || canvas.clientWidth || 1);
    const height = Math.max(1, container.clientHeight || canvas.clientHeight || 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    if (typeof camera.updateProjectionMatrix === 'function') camera.updateProjectionMatrix();
  }

  function draw() {
    renderer.render(scene, camera);
  }

  function loop() {
    // The fly keys move the camera only while the right button is held.
    controls.tick();
    draw();
    if (typeof requestAnimationFrame === 'function') {
      frame = requestAnimationFrame(loop);
    }
  }

  resize();
  controls.frame(layout.bounds);
  draw();
  if (typeof requestAnimationFrame === 'function') {
    frame = requestAnimationFrame(loop);
  }

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', resize);
  }

  return {
    layout,
    scene,
    camera,
    controls,
    renderer,
    canvas,
    /** Render one frame. */
    render: draw,
    /** Re-measure the canvas. */
    resize,
    /** Mark the selected entities and relations. */
    setHighlight,
    /** Release every resource the renderer created. */
    dispose() {
      if (frame !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame);
      frame = null;
      controls.dispose();
      if (typeof canvas.removeEventListener === 'function') {
        canvas.removeEventListener('pointermove', onPointerMove);
      }
      if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
        window.removeEventListener('resize', resize);
      }
      for (const item of disposables) {
        if (item && typeof item.dispose === 'function') item.dispose();
      }
      if (renderer && typeof renderer.dispose === 'function') renderer.dispose();
      if (typeof container.removeChild === 'function' && canvas.parentNode === container) {
        container.removeChild(canvas);
      }
    },
  };
}
