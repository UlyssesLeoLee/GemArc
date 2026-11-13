/**
 * The crystal scene of the console's tool surface (BD-010 section 12.4).
 *
 * The console is a tuning panel for the server, so its scene carries a
 * runtime fact and nothing else: **one crystal per registered tool**. The
 * read tools are aqua, the write tools are topaz, and the layout is a golden
 * angle spiral, so the same tool list always produces the same picture.
 *
 * The scene borrows what it must not reinvent:
 *
 *   - the toon (cel) material comes from `toon-material.js`, the one module
 *     of the product (section 13.4);
 *   - the gestures come from `unity-controls.js`, so the console reacts
 *     exactly like the two public scenes (section 9.1).
 *
 * @module console-space
 */

import { UnityControls } from './unity-controls.js';
import { createToonGradient, createToonMaterial, PALETTE } from './toon-material.js';

/**
 * The colours of the tool surface. They are the palette of the policy:
 * a read tool is blue, a write tool is persimmon (section 12.4).
 * @type {Readonly<Record<string, number>>}
 */
export const CONSOLE_SPACE_COLORS = Object.freeze({
  read: PALETTE.ai,
  write: PALETTE.kaki,
  background: PALETTE.bg,
});

/**
 * The numbers of the scene.
 * @type {Readonly<Record<string, number>>}
 */
export const CONSOLE_SPACE_CONSTANTS = Object.freeze({
  /** The angle of the spiral: 137.5 degrees. */
  goldenAngle: Math.PI * (3 - Math.sqrt(5)),
  /** The radius of the disc the crystals fill. */
  radius: 24,
  /** The radius of one crystal. */
  crystalSize: 4.4,
  /** How high the middle of the disc sits above the rim. */
  lift: 6,
  /** The distance of the camera. */
  distance: 62,
  /** Seconds for one full turn of a crystal. */
  spinPeriod: 60,
  /** Seconds for one breath: dark, bright, dark. */
  breathPeriod: 6,
  /** The self-light of a crystal at the end and at the peak of a breath. */
  breathLow: 0.12,
  breathHigh: 0.34,
});

/**
 * Whether the reader asked for less motion.
 *
 * The system preference wins: when it says "reduce", the scene stays still
 * and only the reader's own actions change it (section 12.4).
 *
 * @returns {boolean} true when the motion must stay off
 */
export function prefersLessMotion() {
  try {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return Boolean(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch {
    return false;
  }
}

/**
 * Where every crystal sits.
 *
 * The spiral is deterministic: the n-th tool always lands on the n-th place
 * of the spiral, so the same tool list renders the same picture twice
 * (section 11.5).
 *
 * @param {Array<{name: string, write?: boolean}>} tools the registered tools
 * @param {object} [options] the options
 * @param {object} [options.constants] the numbers to override
 * @returns {Array<{name: string, write: boolean, color: number,
 *   position: {x: number, y: number, z: number}}>} the crystals
 */
export function layoutConsoleCrystals(tools, options = {}) {
  const constants = { ...CONSOLE_SPACE_CONSTANTS, ...(options.constants || {}) };
  const list = Array.isArray(tools) ? tools : [];
  const count = Math.max(1, list.length);
  return list.map((tool, index) => {
    const radius = constants.radius * Math.sqrt((index + 0.5) / count);
    const angle = index * constants.goldenAngle;
    const write = Boolean(tool && tool.write);
    return {
      name: tool.name,
      write,
      color: write ? CONSOLE_SPACE_COLORS.write : CONSOLE_SPACE_COLORS.read,
      position: {
        x: radius * Math.cos(angle),
        y: constants.lift * (1 - radius / constants.radius),
        z: radius * Math.sin(angle),
      },
    };
  });
}

/**
 * Build the scene.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} options the options
 * @param {object} options.container the element that holds the canvas
 * @param {Array<{name: string, write?: boolean}>} [options.tools] the tools
 * @param {function} [options.onSelect] called with the name of a picked tool
 * @param {function} [options.onHover] called with the name under the pointer
 * @returns {object} the scene api
 */
export function createConsoleSpace(THREE, options = {}) {
  const { container, tools = [], onSelect = null, onHover = null } = options;
  if (!container) throw new Error('控制台的晶体场景需要容器元素');
  if (!THREE || typeof THREE !== 'object') throw new Error('控制台的晶体场景需要注入 THREE');

  // The clock of the scene. It can be injected, so a test can move time by hand.
  const clock = typeof options.now === 'function'
    ? options.now
    : () => (typeof performance !== 'undefined' && typeof performance.now === 'function'
      ? performance.now() / 1000
      : Date.now() / 1000);
  // Reduced motion: the scene then stays still (section 12.4).
  const lessMotion = options.lessMotion === undefined
    ? prefersLessMotion()
    : Boolean(options.lessMotion);
  const started = clock();

  const doc = container.ownerDocument
    || (typeof document !== 'undefined' ? document : null);
  if (!doc || typeof doc.createElement !== 'function') {
    throw new Error('无法为控制台创建画布');
  }
  const canvas = doc.createElement('canvas');
  canvas.className = 'gemarc-console-canvas';
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
  if (!context) throw new Error('无法创建 WebGL 上下文');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, context, antialias: true, alpha: false });
  } catch {
    throw new Error('无法初始化 WebGL 渲染器');
  }
  renderer.setPixelRatio(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(CONSOLE_SPACE_COLORS.background);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 6000);

  const disposables = [];
  const track = (item) => {
    if (item) disposables.push(item);
    return item;
  };

  // One gradient map for the whole scene: the light is banded into three
  // steps on the cut faces and the map never changes (section 13.1).
  const gradient = track(createToonGradient(THREE));

  const root = new THREE.Group();
  scene.add(root);

  // The ratios of section 4.5 (0.75 / 0.55 / 0.25) times one scale: the
  // three-dimensional library divides the light by pi, so without the scale a
  // pigment would arrive on the paper at a third of its own colour.
  const ambient = new THREE.AmbientLight(0xfaf2e2, 0.75 * 2.1);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xfff8ee, 0.55 * 2.1);
  key.position.set(1, 2.4, 1.4);
  scene.add(key);
  // The fill is the bounce of the paper: warm, never cold blue (section 4.5).
  const fill = new THREE.DirectionalLight(PALETTE.facet, 0.25 * 2.1);
  fill.position.set(-1.4, 0.8, -1.1);
  scene.add(fill);

  /** @type {Map<string, object>} tool name -> the crystal */
  const crystals = new Map();
  /** @type {object[]} the meshes the raycaster tests */
  const pickTargets = [];
  let selected = null;

  /**
   * Build the crystals of the given tools.
   * @param {Array<{name: string, write?: boolean}>} list the tools
   */
  function setTools(list) {
    for (const record of crystals.values()) {
      root.remove(record.mesh);
      for (const item of [record.geometry, record.material, record.edgeGeometry, record.edgeMaterial]) {
        if (item && typeof item.dispose === 'function') item.dispose();
      }
    }
    crystals.clear();
    pickTargets.length = 0;

    const layout = layoutConsoleCrystals(list);
    layout.forEach((plan, index) => {
      const geometry = new THREE.OctahedronGeometry(CONSOLE_SPACE_CONSTANTS.crystalSize, 0);
      const material = createToonMaterial(THREE, {
        color: plan.color,
        gradient,
        // The self-light of the crystal: this is the only thing that breathes
        // (section 13.3). No new texture, no new shader.
        emissive: plan.color,
        emissiveIntensity: CONSOLE_SPACE_CONSTANTS.breathLow,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(plan.position.x, plan.position.y, plan.position.z);
      mesh.userData.toolName = plan.name;
      root.add(mesh);
      pickTargets.push(mesh);

      // The ink line is the crystal's own edge: no second outline pass. The
      // ink is the ink, never a brighter step of the body colour (section 13.2).
      const edgeGeometry = new THREE.EdgesGeometry(geometry);
      const edgeMaterial = new THREE.LineBasicMaterial({ color: PALETTE.ink });
      const edge = new THREE.LineSegments(edgeGeometry, edgeMaterial);
      mesh.add(edge);

      crystals.set(plan.name, {
        mesh,
        geometry,
        material,
        edgeGeometry,
        edgeMaterial,
        edge,
        color: plan.color,
        // The phase of this crystal in the turn and in the breath. It comes
        // from the place of the crystal, so it never changes between two runs.
        phase: index * 0.37,
      });
    });
    if (selected && !crystals.has(selected)) selected = null;
    markSelection();
    frameAll();
  }

  /**
   * Mark one crystal as the selected one.
   * @param {string|null} name the tool
   */
  function markSelection() {
    for (const [name, record] of crystals) {
      const active = name === selected;
      // The body keeps its colour and stays solid; the selection is carried
      // by the ink line, which grows one step (section 4.2).
      record.material.opacity = 1;
      record.edgeMaterial.opacity = 1;
      if (record.edge) record.edge.scale.setScalar(active ? 1.12 : 1);
    }
  }

  /**
   * Tell the panel which crystal is selected.
   * @param {string|null} name the tool
   */
  function setSelected(name) {
    selected = name && crystals.has(name) ? name : null;
    markSelection();
  }

  /* ------------------------------------------------------------- picking */

  const raycaster = new THREE.Raycaster();
  const pointer = { x: 0, y: 0 };

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
   * The tool under the pointer.
   * @param {object} event the pointer event
   * @returns {{name: string}|null} the hit
   */
  function pick(event) {
    const ndc = pointerNdc(event);
    pointer.x = ndc.x;
    pointer.y = ndc.y;
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(pickTargets, false);
    if (hits && hits.length > 0) {
      const name = hits[0].object?.userData?.toolName;
      if (name) return { name };
    }
    return null;
  }

  /**
   * Every crystal inside the marquee rectangle.
   * @param {object} rect the rectangle in client coordinates
   * @returns {Array<{name: string}>} the hits
   */
  function pickInRect(rect) {
    const canvasRect = typeof canvas.getBoundingClientRect === 'function'
      ? canvas.getBoundingClientRect()
      : { left: 0, top: 0, width: canvas.clientWidth || 1, height: canvas.clientHeight || 1 };
    const hits = [];
    for (const [name, record] of crystals) {
      const point = record.mesh.worldPosition();
      const x = canvasRect.left + (point.x * 0.5 + 0.5) * canvasRect.width;
      const y = canvasRect.top + (-point.y * 0.5 + 0.5) * canvasRect.height;
      if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) continue;
      hits.push({ name });
    }
    return hits;
  }

  const controls = new UnityControls(camera, canvas, {
    distance: CONSOLE_SPACE_CONSTANTS.distance,
    minDistance: 18,
    maxDistance: 200,
    pick: (event) => pick(event),
    pickInRect: (rect) => pickInRect(rect),
    onSelect: (hit) => {
      if (onSelect) onSelect(hit.name);
    },
    onClear: () => {
      selected = null;
      markSelection();
    },
    getBounds: () => ({ center: { x: 0, y: 0, z: 0 }, radius: CONSOLE_SPACE_CONSTANTS.radius }),
  });

  const onPointerMove = (event) => {
    const hit = pick(event);
    if (onHover) onHover(hit ? hit.name : null);
    if (canvas.style) canvas.style.cursor = hit ? 'pointer' : 'default';
  };
  if (typeof canvas.addEventListener === 'function') {
    canvas.addEventListener('pointermove', onPointerMove);
  }

  /* ------------------------------------------------------------- the frame */

  let frame = null;

  function resize() {
    const width = Math.max(1, container.clientWidth || canvas.clientWidth || 1);
    const height = Math.max(1, container.clientHeight || canvas.clientHeight || 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    if (typeof camera.updateProjectionMatrix === 'function') camera.updateProjectionMatrix();
    fitCamera();
  }

  function draw() {
    renderer.render(scene, camera);
  }

  /**
   * One step of the motion: a slow turn and a slow breath.
   *
   * The motion changes the **pose** and the **self-light** only. The
   * geometry, the place of every crystal and its colour stay exactly where
   * the tool list put them (sections 12.4 and 11.8), so the picture of the
   * tool surface still describes the same facts.
   */
  function applyMotion() {
    if (lessMotion) return;
    const elapsed = clock() - started;
    const turn = CONSOLE_SPACE_CONSTANTS.spinPeriod;
    const breath = CONSOLE_SPACE_CONSTANTS.breathPeriod;
    for (const record of crystals.values()) {
      const phase = record.phase || 0;
      record.mesh.rotation.y = (elapsed / turn + phase) * Math.PI * 2;
      const wave = 0.5 + 0.5 * Math.sin(((elapsed / breath) + phase) * Math.PI * 2);
      record.material.emissiveIntensity = CONSOLE_SPACE_CONSTANTS.breathLow
        + (CONSOLE_SPACE_CONSTANTS.breathHigh - CONSOLE_SPACE_CONSTANTS.breathLow) * wave;
    }
  }

  function loop() {
    controls.tick();
    applyMotion();
    draw();
    if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(loop);
  }

  /** Point the camera at the whole disc. */
  function frameAll() {
    fitCamera();
  }

  /**
   * Put the whole disc into the frame.
   *
   * The panel is narrow, so the distance cannot be the one of the wide public
   * scenes: the camera has to step back until the disc fits in the narrower of
   * the two directions, or the outer crystals are cut off. The disc is seen at
   * an angle, so it is shorter on the screen than it is wide.
   */
  function fitCamera() {
    const radius = CONSOLE_SPACE_CONSTANTS.radius + CONSOLE_SPACE_CONSTANTS.crystalSize;
    const halfFov = (camera.fov * Math.PI) / 360;
    const flat = Math.sin(Math.PI / 2 - (controls.phi || 0));
    const vertical = (radius * flat + CONSOLE_SPACE_CONSTANTS.lift) / Math.tan(halfFov);
    const horizontal = radius / (Math.tan(halfFov) * Math.max(0.35, camera.aspect || 1));
    const wanted = Math.max(vertical, horizontal) * 1.15;
    controls.distance = Math.max(controls.minDistance, Math.min(controls.maxDistance, wanted));
    controls.apply();
  }

  resize();
  setTools(tools);
  draw();
  if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(loop);

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', resize);
  }

  return {
    scene,
    camera,
    controls,
    renderer,
    canvas,
    /** The crystals, by tool name. */
    crystals,
    /** Rebuild the crystals of a tool list. */
    setTools,
    /** Mark the selected crystal. */
    setSelected,
    /** Render one frame. */
    render: draw,
    /** One step: the camera, the motion and one picture. */
    tick() {
      controls.tick();
      applyMotion();
      draw();
    },
    /** Whether the motion is off because the reader asked for less of it. */
    lessMotion,
    /** Re-measure the canvas. */
    resize,
    /** Release every resource the scene created. */
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
