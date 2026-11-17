/**
 * The arrows of the architecture (BD-010 section 4.9).
 *
 * A scene of cut stones says what the stones are. It does not say **which way
 * the work flows** - and an architecture diagram without a direction is a pile
 * of parts. So the three-dimensional scenes draw arrows:
 *
 *   - a **shaft**: a thin tube in the tea ink of the palette, the same weight
 *     as a relation line;
 *   - a **head**: a small cone in the ink of the palette, so the direction is
 *     legible at a glance - the paper the scene is drawn on is the paper the
 *     cone would be, so a paper-white cone would be invisible;
 *   - a **pulse**: a solid bead that walks the shaft, once every couple of
 *     seconds. It is *geometry*, not a light of its own: no emissive, no
 *
 * The pulse is the moving part, and it only moves where the policy allows
 * motion: the console's tool surface (section 12.4). The public scenes draw
 * the same arrow but stand still (section 6).
 *
 * The rules this module keeps:
 *
 *   1. **One implementation.** The console, the two public scenes and the
 *      artefacts a user generates all read the arrows from here, so an arrow
 *      cannot look like two different arrows (section 13.4).
 *   2. **The parameters are constants.** They do not change with the entity,
 *      the selection or the frame, so the same model renders the same pixels
 *      twice (section 11.5, section 13.7).
 *   3. **No third-party import.** The three-dimensional library is injected by
 *      the caller; the only things this module needs are the geometry classes
 *      and the palette of the product.
 *   4. **An arrow is never invented.** The module draws what it is given: the
 *      caller decides which links are real (section 2.1).
 *
 * @module flow-arrows
 */

import { PALETTE } from './gem-material.js';

/**
 * The numbers of the arrows (BD-010 section 13.7).
 *
 * They are constants, not options: the shaft is as thin as a relation line,
 * the head is a small paper-white cone, and the bead walks once every 2.6
 * seconds.
 * @type {Readonly<Record<string, number>>}
 */
export const FLOW_ARROW_CONSTANTS = Object.freeze({
  /** The radius of the shaft. */
  shaftRadius: 0.2,
  /** The radius of the cone at the head. */
  headRadius: 0.8,
  /** The length of the cone at the head. */
  headLength: 2.2,
  /** The radius of the bead that walks the shaft. */
  pulseRadius: 0.5,
  /** Seconds for one walk of the bead. */
  pulsePeriod: 2.6,
  /** How high the shaft arches between its two ends. */
  bow: 3.0,
  /** The segments along the shaft. */
  tubularSegments: 40,
  /** The segments around the shaft. */
  radialSegments: 8,
});

/**
 * The middle of an arrow.
 *
 * The two ends are lifted by the bow, so the shaft arches **over** the stones
 * instead of running through them: an arrow that passes through a stone
 * reads as a mistake, not as a flow.
 *
 * @param {{x: number, y: number, z: number}} from where the arrow starts
 * @param {{x: number, y: number, z: number}} to where the arrow ends
 * @param {number} bow how high the shaft arches
 * @returns {{x: number, y: number, z: number}} the middle point
 */
export function arrowMidpoint(from, to, bow) {
  return {
    x: (from.x + to.x) / 2,
    y: (from.y + to.y) / 2 + bow,
    z: (from.z + to.z) / 2,
  };
}

/**
 * Build one arrow.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} options the options
 * @param {{x: number, y: number, z: number}} options.from where it starts
 * @param {{x: number, y: number, z: number}} options.to where it ends
 * @param {number} [options.color] the colour of the shaft (the pale ink)
 * @param {number} [options.headColor] the colour of the cone (the ink)
 * @param {number} [options.pulseColor] the colour of the bead (the ink)
 * @param {number} [options.opacity] how opaque the shaft is
 * @param {number} [options.phase] where the bead starts, in turns
 * @param {number} [options.period] how long one walk takes
 * @param {boolean} [options.animate] false draws the arrow standing still
 * @returns {object} the arrow (dispose it with the scene)
 */
export function createFlowArrow(THREE, options = {}) {
  const from = options.from || { x: 0, y: 0, z: 0 };
  const to = options.to || { x: 0, y: 0, z: 0 };
  const constants = { ...FLOW_ARROW_CONSTANTS, ...(options.constants || {}) };
  const bow = options.bow === undefined ? constants.bow : options.bow;
  const period = options.period === undefined ? constants.pulsePeriod : options.period;
  const phase = options.phase === undefined ? 0 : options.phase;
  const animate = options.animate !== false;

  const middle = arrowMidpoint(from, to, bow);
  const curve = new THREE.QuadraticBezierCurve3(
    new THREE.Vector3(from.x, from.y, from.z),
    new THREE.Vector3(middle.x, middle.y, middle.z),
    new THREE.Vector3(to.x, to.y, to.z),
  );

  const group = new THREE.Group();
  group.name = options.name || 'flow-arrow';

  const shaftGeometry = new THREE.TubeGeometry(
    curve,
    constants.tubularSegments,
    constants.shaftRadius,
    constants.radialSegments,
    false,
  );
  const shaftMaterial = new THREE.MeshBasicMaterial({
    color: options.color === undefined ? PALETTE.muted : options.color,
    transparent: true,
    opacity: options.opacity === undefined ? 0.75 : options.opacity,
    // The shaft is a line of tea ink, not a lit object.
    toneMapped: false,
  });
  const shaft = new THREE.Mesh(shaftGeometry, shaftMaterial);
  group.add(shaft);

  const headGeometry = new THREE.ConeGeometry(
    constants.headRadius,
    constants.headLength,
    constants.radialSegments,
  );
  const headMaterial = new THREE.MeshBasicMaterial({
    color: options.headColor === undefined ? PALETTE.ink : options.headColor,
    // The head is a line, not a body: it is drawn transparent. An opaque
    // object is drawn into the transmission buffer of the scene, and a
    // stone would then refract the head of its own arrow - the gem turns
    // (BD-010 section 4.9).
    transparent: true,
    opacity: 1,
    toneMapped: false,
  });
  const head = new THREE.Mesh(headGeometry, headMaterial);
  const end = curve.getPoint(1);
  const tangent = curve.getTangent(1);
  head.position.set(end.x, end.y, end.z);
  // A cone stands on its own Y axis; the tangent says where the arrow goes.
  head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tangent);
  group.add(head);

  const pulseGeometry = new THREE.SphereGeometry(constants.pulseRadius, constants.radialSegments, 6);
  const pulseMaterial = new THREE.MeshBasicMaterial({
    color: options.pulseColor === undefined ? PALETTE.ink : options.pulseColor,
    // The bead is a line too: transparent, so it never enters the
    // transmission buffer (BD-010 section 4.9).
    transparent: true,
    opacity: 1,
    toneMapped: false,
  });
  const pulse = new THREE.Mesh(pulseGeometry, pulseMaterial);
  group.add(pulse);

  const disposables = [shaftGeometry, shaftMaterial, headGeometry, headMaterial, pulseGeometry, pulseMaterial];

  // Whether the bead walks. A public scene stands still until the reader
  // picks a relation (section 6, section 12.4).
  let animating = animate;

  /**
   * Move the bead.
   *
   * The bead's place is the only thing that changes: the geometry, the
   * colours and the shape of the arrow stay exactly as they were
   * (section 4.9, section 13.3).
   *
   * @param {number} elapsed the seconds since the scene started
   */
  function update(elapsed) {
    const turn = ((elapsed / period) + phase) % 1;
    const point = curve.getPoint(turn);
    pulse.position.set(point.x, point.y, point.z);
    pulse.visible = animating;
  }

  update(0);

  return {
    group,
    curve,
    shaft,
    head,
    pulse,
    /** Whether the bead is walking. */
    get animating() {
      return animating;
    },
    /**
     * Start or stop the walk.
     * @param {boolean} value true when the bead may walk
     */
    setAnimating(value) {
      animating = Boolean(value);
      if (!animating) pulse.visible = false;
    },
    update,
    dispose() {
      for (const item of disposables) {
        if (item && typeof item.dispose === 'function') item.dispose();
      }
    },
  };
}

/**
 * Build a set of arrows.
 *
 * @param {object} THREE the three-dimensional library
 * @param {Array<{from: object, to: object, phase?: number}>} links the links
 * @param {object} [options] the options of every arrow
 * @returns {object[]} the arrows
 */
export function createFlowArrows(THREE, links, options = {}) {
  const list = Array.isArray(links) ? links : [];
  return list.map((link, index) => createFlowArrow(THREE, {
    ...options,
    from: link.from,
    to: link.to,
    phase: link.phase === undefined ? (options.phase === undefined ? index * 0.25 : options.phase) : link.phase,
    name: link.name,
  }));
}

/**
 * Move every bead of a set of arrows.
 *
 * @param {object[]} arrows the arrows
 * @param {number} elapsed the seconds since the scene started
 */
export function updateFlowArrows(arrows, elapsed) {
  const list = Array.isArray(arrows) ? arrows : [];
  for (const arrow of list) {
    if (arrow && typeof arrow.update === 'function') arrow.update(elapsed);
  }
}

/**
 * Release a set of arrows.
 *
 * @param {object[]} arrows the arrows
 */
export function disposeFlowArrows(arrows) {
  const list = Array.isArray(arrows) ? arrows : [];
  for (const arrow of list) {
    if (arrow && typeof arrow.dispose === 'function') arrow.dispose();
  }
}
