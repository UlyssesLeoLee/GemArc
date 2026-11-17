/**
 * The one gem material of the product (BD-010 section 13).
 *
 * The style policy asks for a **cut stone** in real three dimensions, and
 * since v3.7 it asks for it to be a *stone*: the light refracts through it,
 * it splits the light into colour at its edges, and every cut face reflects
 * its own little piece of the room. Three scenes draw stones - the semantic-axis
 * scene, the building scene and the console's tool-surface scene - and they
 * must not drift apart, so the material lives here once and everybody imports
 * it.
 *
 * The rules this module keeps:
 *
 *   1. **A real gem, not a painted one.** The body is a physical material
 *      with transmission, an index of refraction and dispersion (BD-010
 *      section 13.1). There is no alpha blending and no fake highlight layer:
 *      a stone shows what is behind it because the light bends on the way in.
 *   2. **The cut says what the thing is.** Every entity kind has its own cut
 *      work (section 4.3): the colour says which kind it is, the silhouette
 *      says which role it plays. The table lives here, so the three scenes
 *      cannot give the same kind two different shapes (section 13.5).
 *   3. **The ink is a hairline around the outside.** The inside edges of a
 *      cut stone are already visible in the refraction, so they are not
 *      drawn a second time; only the outer contour carries ink (section 4.8).
 *   4. **The parameters are constants.** They do not change with the entity,
 *      the selection or the frame, so the same model renders the same pixels
 *      twice (section 11.5).
 *   5. **No third-party import.** The three-dimensional library is injected
 *      by the caller (an import map for the static profile, node_modules for
 *      the vite profile, a CDN for the console). The only standard pieces
 *      this module needs are the physical material, a handful of geometry
 *      classes and the pre-filtered environment baker.
 *
 * @module gem-material
 */

/**
 * The palette of the product, as the scenes see it (BD-010 section 3 and
 * 16.4). The site, the console and the three scenes share one set of colours;
 * this module holds it so that the scenes cannot drift into a second skin.
 * @type {Readonly<Record<string, number>>}
 */
export const PALETTE = Object.freeze({
  void: 0xd6c9ae,
  bg: 0xebe1ce,
  panel: 0xf4eddf,
  facet: 0xe6dac6,
  rim: 0xa89a7c,
  ink: 0x2e2a24,
  muted: 0x6e6354,
  ai: 0x3d5a72,
  fuji: 0x5f5573,
  kaki: 0x96501f,
  koke: 0x56663a,
  sabi: 0x8a3a2c,
});

/**
 * The colour of an entity kind (BD-010 section 4.3). The table is the one of
 * the policy: the same pigments as the rest of the product, in a light and a
 * dark step of each.
 * @type {Readonly<Record<string, number>>}
 */
export const KIND_COLORS = Object.freeze({
  actor: 0xb4654a,
  system: 0x3d5a72,
  service: 0x96501f,
  process: 0x5f5573,
  module: 0x56663a,
  data: 0x5a7566,
  database: 0x4c6e7a,
  storage: 0x6b5f7e,
  gateway: 0xa9762f,
  plugin: 0x6e7b45,
  security: 0x8a3a2c,
  operations: 0x7e8b93,
  runtime: 0x8c8271,
  external: 0x8f8577,
});

/**
 * The colour of an entity whose kind is not registered. It is deliberately
 * muted: an unregistered kind is not the same thing as a known one.
 * @type {number}
 */
export const UNKNOWN_KIND_COLOR = 0x9c927f;

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
 * The cut work of every entity kind (BD-010 section 4.3). The cut says which
 * role a thing plays, the colour says which kind it is; together they carry
 * the fact, so neither of them has to carry it alone.
 * @type {Readonly<Record<string, string>>}
 */
export const KIND_CUT_FORMS = Object.freeze({
  system: 'brilliant',
  runtime: 'brilliant',
  service: 'column',
  gateway: 'column',
  data: 'wedge',
  database: 'wedge',
  storage: 'wedge',
  module: 'step',
  plugin: 'step',
  process: 'pyramid',
  operations: 'pyramid',
  actor: 'marquise',
  external: 'marquise',
  security: 'shield',
});

/**
 * The cut of an entity kind that is not registered: the plain stone. It has no
 * meaning to carry, so it gets the shape that means nothing.
 * @type {string}
 */
export const UNKNOWN_CUT_FORM = 'plain';

/**
 * The cut work of an entity kind.
 * @param {string|null} kind the kind
 * @returns {string} the cut work id
 */
export function cutFormForKind(kind) {
  if (typeof kind !== 'string') return UNKNOWN_CUT_FORM;
  return Object.prototype.hasOwnProperty.call(KIND_CUT_FORMS, kind)
    ? KIND_CUT_FORMS[kind]
    : UNKNOWN_CUT_FORM;
}

/**
 * How rough a cut face is. A polished stone is smooth; the number is small
 * because the reflection has to stay a hard edge (BD-010 section 13.1).
 * @type {number}
 */
export const GEM_ROUGHNESS = 0.03;

/**
 * How much of the light goes through the stone. The stone is not a pane of
 * clear stuff: a real gem is mostly brilliant - the light that comes back
 * out of it - and only partly a window onto what is behind. At full
 * transmission the stone turns into a flat tinted pane and loses its cut; at
 * 0.9 the cut faces keep their reflection and the body still carries the
 * type colour (BD-010 section 4.7).
 * @type {number}
 */
export const GEM_TRANSMISSION = 0.9;

/**
 * The index of refraction of the stone. Diamonds are 2.42, sapphires 1.77;
 * the stone of this product is cut for fire, not for cheapness.
 * @type {number}
 */
export const GEM_IOR = 2.2;

/**
 * How much the white light is split into colours inside the stone. This is
 * the rainbow at the edge of a real stone; it is a by-product of the
 * refraction, not a texture.
 * @type {number}
 */
export const GEM_DISPERSION = 1.5;

/**
 * How thick the stone is for the refraction, as a share of its radius.
 * @type {number}
 */
export const GEM_THICKNESS_RATIO = 0.85;

/**
 * How far the light travels before the type colour has soaked into it, as a
 * share of the radius. A bigger number means a paler stone.
 * @type {number}
 */
export const GEM_ATTENUATION_RATIO = 2.2;

/**
 * How strongly the cut faces reflect the room.
 * @type {number}
 */
export const GEM_ENV_INTENSITY = 4.5;

/**
 * The polished coat on the stone. A cut stone is polished: the coat is what
 * puts the hard white glint on the edge of a facet. It is a real layer of
 * the material, not a painted highlight.
 * @type {number}
 */
export const GEM_CLEARCOAT = 1;

/**
 * How rough that coat is. Polished, so the glint stays a hard point.
 * @type {number}
 */
export const GEM_CLEARCOAT_ROUGHNESS = 0.03;

/**
 * How bright the room is allowed to be. The room is the room: the exposure
 * does not change from scene to scene.
 * @type {number}
 */
export const GEM_TONE_MAPPING_EXPOSURE = 1.6;

/**
 * How strong the three scene lights are.
 *
 * A stone is lit by the **studio**, not by a lamp in the room: the cut faces
 * are turned towards the bright bars, and that is what makes them glint. The
 * three lights of the scene (ambient, key, warm fill) are only a fill - a
 * little light on the paper-white parts of the stone. Turned up, they wash
 * the type colour out of the body and the stone turns chalky.
 * @type {number}
 */
export const GEM_LIGHT_SCALE = 0.55;

/**
 * The width of the ink line around a stone, in pixels on the screen. It is a
 * hairline: the stone is the subject, the line only says where it ends.
 * @type {number}
 */
export const GEM_OUTLINE_PIXELS = 1;

/**
 * The width of the ink line around the selected stone.
 * @type {number}
 */
export const GEM_OUTLINE_PIXELS_SELECTED = 2;

/**
 * Cut a stone out of a profile.
 *
 * The profile is the half cross-section of the cut, from the bottom of the
 * pavilion up to the table on top: a list of `[radius, height]` pairs. The
 * stone is turned around that profile, and the number of segments is the
 * number of sides the cut has - that is what tells a brilliant from a
 * column from a step.
 *
 * The stone is **centred** on its own middle, so a cut whose pavilion is
 * deeper than its crown still hangs on the node it belongs to.
 *
 * @param {object} THREE the three-dimensional library
 * @param {ReadonlyArray<[number, number]>} profile the half cross-section
 * @param {number} segments the number of sides of the cut
 * @returns {{geometry: object, half: {x: number, y: number, z: number}}} the stone
 */
function latheCut(THREE, profile, segments) {
  const points = profile.map(([radius, height]) => new THREE.Vector2(radius, height));
  const geometry = new THREE.LatheGeometry(points, segments);
  let maxRadius = 0;
  let minHeight = Infinity;
  let maxHeight = -Infinity;
  for (const [radius, height] of profile) {
    if (radius > maxRadius) maxRadius = radius;
    if (height < minHeight) minHeight = height;
    if (height > maxHeight) maxHeight = height;
  }
  // The turned stone reaches its widest point where the profile points at the
  // side of the turn, so the reach is the radius times the widest sine (or
  // cosine) the number of sides allows.
  let reach = 0;
  let depth = 0;
  for (let step = 0; step <= segments; step += 1) {
    const angle = (Math.PI * 2 * step) / segments;
    if (Math.abs(Math.sin(angle)) > reach) reach = Math.abs(Math.sin(angle));
    if (Math.abs(Math.cos(angle)) > depth) depth = Math.abs(Math.cos(angle));
  }
  const centre = (minHeight + maxHeight) / 2;
  geometry.translate(0, -centre, 0);
  return {
    geometry,
    half: {
      x: maxRadius * reach,
      y: (maxHeight - minHeight) / 2,
      z: maxRadius * depth,
    },
  };
}

/**
 * The unit shapes of the cut works. Every cut is a real cut: a table on top,
 * a girdle in the middle and a pavilion below, so each of them reads as a
 * stone and not as a vessel. The profiles are written in the unit size, and
 * the stone is scaled to the size that is asked for.
 * @type {Readonly<Record<string, {build: function(object): {geometry: object, half: object}}>>}
 */
const CUT_SHAPES = Object.freeze({
  brilliant: {
    // A round brilliant: the table on top, the girdle in the middle, the
    // pavilion that ends in a point. Sixteen sides, because a brilliant is
    // the cut that is meant to throw light.
    build(THREE) {
      return latheCut(THREE, [
        [0.0001, -1.00],
        [0.55, -0.44],
        [0.92, -0.10],
        [1.00, 0.00],
        [1.00, 0.07],
        [0.74, 0.32],
        [0.44, 0.48],
        [0.0001, 0.48],
      ], 16);
    },
  },
  column: {
    // A hexagonal column: straight sides, a small table, a point at the
    // bottom. Something that carries and stands up.
    build(THREE) {
      return latheCut(THREE, [
        [0.0001, -0.95],
        [0.68, -0.30],
        [0.80, 0.00],
        [0.80, 0.58],
        [0.52, 0.80],
        [0.0001, 0.84],
      ], 6);
    },
  },
  wedge: {
    // An octagonal emerald cut: the stepped shoulders of a boundary, a
    // broad table and a shallow point below.
    build(THREE) {
      return latheCut(THREE, [
        [0.0001, -0.86],
        [0.60, -0.34],
        [0.84, -0.02],
        [0.84, 0.50],
        [0.62, 0.68],
        [0.40, 0.78],
        [0.0001, 0.80],
      ], 8);
    },
  },
  step: {
    // A square step cut: the wide base, the shoulders of a part that can be
    // taken off and put back.
    build(THREE) {
      const cut = latheCut(THREE, [
        [0.0001, -0.78],
        [0.52, -0.38],
        [0.86, -0.12],
        [1.00, 0.00],
        [1.00, 0.08],
        [0.84, 0.30],
        [0.56, 0.46],
        [0.0001, 0.50],
      ], 4);
      // A square cut turned by half a step shows its faces, not its corners.
      cut.geometry.rotateY(Math.PI / 4);
      return cut;
    },
  },
  pyramid: {
    // A triangular cut: three sides, a point above and a point below.
    build(THREE) {
      return latheCut(THREE, [
        [0.0001, -0.72],
        [0.62, -0.16],
        [1.00, 0.06],
        [0.62, 0.28],
        [0.0001, 0.72],
      ], 3);
    },
  },
  marquise: {
    // A long double pyramid: an actor, a proxy, a thing that points.
    build(THREE) {
      const geometry = new THREE.OctahedronGeometry(0.62, 0);
      geometry.scale(0.80, 1.60, 0.80);
      return { geometry, half: { x: 0.496, y: 0.992, z: 0.496 } };
    },
  },
  shield: {
    // A pentagon with the shoulders of a guard: wide in the middle, a point
    // above and a point below.
    build(THREE) {
      return latheCut(THREE, [
        [0.0001, -0.70],
        [0.66, -0.26],
        [1.00, 0.16],
        [0.74, 0.54],
        [0.0001, 0.80],
      ], 5);
    },
  },
  plain: {
    // The plain stone: an octahedron, the shape that means nothing.
    build(THREE) {
      const geometry = new THREE.OctahedronGeometry(1.00, 0);
      return { geometry, half: { x: 1.00, y: 1.00, z: 1.00 } };
    },
  },
});

/**
 * The cut works this module can cut.
 * @type {ReadonlyArray<string>}
 */
export const CUT_FORM_IDS = Object.freeze(Object.keys(CUT_SHAPES));

/**
 * Cut a stone.
 *
 * The shape is built once per call and scaled to the size that was asked
 * for: `size` is the radius of the stone, so every cut ends up in the same
 * box and the layout of the scene does not depend on the cut.
 *
 * @param {object} THREE the three-dimensional library
 * @param {string} form the cut work id
 * @param {number} size the radius of the stone
 * @returns {{geometry: object, half: {x: number, y: number, z: number}}} the stone
 */
export function createCrystalGeometry(THREE, form, size) {
  const shape = Object.prototype.hasOwnProperty.call(CUT_SHAPES, form) ? CUT_SHAPES[form] : CUT_SHAPES.plain;
  const cut = shape.build(THREE);
  const half = cut.half;
  const longest = Math.max(half.x, half.y, half.z) || 1;
  const scale = size / longest;
  cut.geometry.scale(scale, scale, scale);
  return {
    geometry: cut.geometry,
    half: { x: half.x * scale, y: half.y * scale, z: half.z * scale },
  };
}

/**
 * Build the material of a stone.
 *
 * The material is the whole point of the module (BD-010 section 13.1):
 *
 *   - **transmission**: the stone shows what is behind it, bent by the
 *     refraction, and keeps the rest of its light for the reflection;
 *   - **ior**: the light bends on the way in, so the background inside the
 *     stone is not where it was;
 *   - **dispersion**: the light is split into colours, so the edges carry a
 *     rainbow;
 *   - **attenuation colour**: the type colour soaks into the stone instead
 *     of being painted on it;
 *   - **clearcoat**: the polished coat that puts a hard white glint on the
 *     edge of a cut face;
 *   - **flat shading**: one normal per cut face, so the reflection is a hard
 *     edge and not a soft patch;
 *   - **double side**: the inner cut faces are seen through the stone.
 *
 * The surface itself carries no colour of its own: a stone is not painted.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} [options] the material options
 * @param {number} [options.color] the type colour of the stone
 * @param {number} [options.size] the radius of the stone (the refraction
 *   volume and the depth of the colour follow it)
 * @param {number} [options.emissive] the self-light colour (the breathing of
 *   the console's tool surface)
 * @param {number} [options.emissiveIntensity] how strong the self-light is
 * @returns {object} the material (dispose it with the scene)
 */
export function createGemMaterial(THREE, options = {}) {
  const size = options.size ?? 1;
  return new THREE.MeshPhysicalMaterial({
    // No colour on the surface: the stone is not painted (section 4.7).
    color: 0xffffff,
    metalness: 0,
    roughness: GEM_ROUGHNESS,
    transmission: GEM_TRANSMISSION,
    thickness: size * GEM_THICKNESS_RATIO,
    ior: GEM_IOR,
    dispersion: GEM_DISPERSION,
    attenuationColor: new THREE.Color(options.color ?? 0xffffff),
    attenuationDistance: size * GEM_ATTENUATION_RATIO,
    envMapIntensity: GEM_ENV_INTENSITY,
    // The polished coat of a cut stone: it is what glints.
    clearcoat: GEM_CLEARCOAT,
    clearcoatRoughness: GEM_CLEARCOAT_ROUGHNESS,
    // One normal per cut face: the edge between two faces stays hard.
    flatShading: true,
    // The inner cut faces are seen through the stone.
    side: THREE.DoubleSide,
    emissive: options.emissive ?? 0x000000,
    emissiveIntensity: options.emissiveIntensity ?? 0,
  });
}

/**
 * Build the ink line around a stone.
 *
 * The line is a hairline around the **outside** only (BD-010 section 4.8):
 * the inside edges of a cut stone are already visible in the refraction, so
 * drawing them again would turn the stone into a wire frame.
 *
 * It is drawn with a copy of the same geometry, grown by one line width and
 * seen from the back: the shell sticks out of the silhouette by exactly the
 * width of the ink and nowhere else.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} geometry the geometry of the stone
 * @param {{x: number, y: number, z: number}} half the half size of the stone
 * @param {object} [options] the material options
 * @param {number} [options.color] the colour of the ink
 * @returns {object} the shell (dispose it with the scene)
 */
export function createSilhouetteOutline(THREE, geometry, half, options = {}) {
  const material = new THREE.MeshBasicMaterial({
    color: options.color ?? PALETTE.ink,
    // Only the back of the shell is drawn: the front would cover the stone.
    side: THREE.BackSide,
    transparent: true,
    opacity: 1,
    // The line never hides what is behind the stone.
    depthWrite: false,
    // The ink is ink: it is not lit and it is not tone mapped.
    toneMapped: false,
  });
  const shell = new THREE.Mesh(geometry, material);
  shell.userData.silhouetteHalf = half;
  // The shell is a hairline, not a body: it never takes part in picking, so
  // clicking next to a stone is still clicking on nothing.
  shell.raycast = () => {};
  return shell;
}

/**
 * Give the ink line its width.
 *
 * The width is counted in **pixels on the screen**, not in world units: a
 * stone far away and a stone close by get the same hairline, and zooming in
 * does not turn the line into a border. The world width of one pixel grows
 * with the distance to the camera, so the shell is widened by that much on
 * every side of the stone.
 *
 * @param {object} outline the shell from {@link createSilhouetteOutline}
 * @param {object} camera the camera the scene is seen through
 * @param {object} target the world position of the stone
 * @param {number} viewportHeight the height of the canvas in pixels
 * @param {number} pixelWidth the width of the line in pixels
 */
export function updateSilhouetteWidth(outline, camera, target, viewportHeight, pixelWidth) {
  const half = outline?.userData?.silhouetteHalf;
  if (!half || !camera || !target) return;
  const distance = camera.position.distanceTo(target);
  if (!Number.isFinite(distance) || distance <= 0) return;
  const halfHeight = distance * Math.tan((camera.fov * Math.PI) / 360);
  const worldPerPixel = (2 * halfHeight) / Math.max(viewportHeight, 1);
  const width = worldPerPixel * pixelWidth;
  outline.scale.set(
    1 + width / half.x,
    1 + width / half.y,
    1 + width / half.z,
  );
}

/**
 * Put the renderer into the state the stones need.
 *
 * Two things happen here and nowhere else (BD-010 section 13.1): the light is
 * tone mapped, so a bright reflection rolls off instead of clipping to a
 * white hole, and the transmission buffer is rendered at half size, because
 * the stones are small and the buffer is not.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} renderer the renderer
 */
export function applyGemRendererSettings(THREE, renderer) {
  if (!renderer) return;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = GEM_TONE_MAPPING_EXPOSURE;
  if ('transmissionResolutionScale' in renderer) {
    renderer.transmissionResolutionScale = 0.5;
  }
}

/**
 * Build the little studio the stones reflect.
 *
 * A cut face only shows something if there is something to show. The studio
 * is the way a gem is photographed: a **dark room** with a row of **narrow
 * bright bars** in it. Every bar is a different piece of the room, so every
 * cut face catches a different one - that is where the hard glints come from.
 *
 * The bars are the paper white of the palette, so the brightest thing in a
 * stone is the colour of the paper it is drawn on. The room around them is
 * the ink of the palette: the dark cut faces of a stone are the room, not a
 * painted shadow.
 *
 * The room is made of code, not of files: it is baked into an environment
 * map with the library's own pre-filtering, and it never changes.
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} renderer the renderer (the baker needs it)
 * @returns {{texture: object, dispose: function(): void}} the environment
 */
export function createStudioEnvironment(THREE, renderer) {
  const studio = new THREE.Scene();
  // The room around the bars is the ink of the palette: the dark cut faces
  // of a stone are the room, not a painted shadow.
  studio.background = new THREE.Color(PALETTE.ink);

  const bars = [];
  const bar = (width, height, color, x, y, z, rx, ry) => {
    const geometry = new THREE.PlaneGeometry(width, height);
    const material = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(x, y, z);
    mesh.rotation.set(rx, ry, 0);
    studio.add(mesh);
    bars.push(geometry, material);
  };
  // The bright bars: a row of narrow ones overhead, one on each side, two
  // in front and one across the top. Narrow, so a cut face catches one bar
  // and not the room - that is where the hard glints come from. There are
  // enough of them that every stone catches two or three.
  bar(11.0, 0.7, PALETTE.panel, 0, 5.2, 0, -Math.PI / 2, 0);
  bar(11.0, 0.7, PALETTE.panel, 0, 5.2, -2.4, -Math.PI / 2, 0);
  bar(11.0, 0.7, PALETTE.facet, 0, 5.2, -4.6, -Math.PI / 2, 0);
  bar(0.7, 9.0, PALETTE.panel, -4.6, 1.8, 0, 0, Math.PI / 2);
  bar(0.7, 9.0, PALETTE.panel, 4.6, 1.8, 0, 0, -Math.PI / 2);
  bar(0.7, 9.0, PALETTE.facet, -4.6, 1.8, -4.2, 0, Math.PI / 2);
  // The fill bars: the same room, one step down, so the glints have a
  // softer second light next to them.
  bar(0.7, 8.0, PALETTE.facet, -2.6, 1.2, 4.4, 0, 0);
  bar(0.7, 8.0, PALETTE.facet, 2.6, 1.2, 4.4, 0, 0);
  bar(9.0, 0.7, PALETTE.facet, 0, 4.4, 4.6, -0.9, 0);

  const baker = new THREE.PMREMGenerator(renderer);
  const target = baker.fromScene(studio, 0.02, 0.1, 100);
  baker.dispose();
  for (const item of bars) {
    if (item && typeof item.dispose === 'function') item.dispose();
  }
  return {
    texture: target.texture,
    dispose: () => {
      if (target && typeof target.dispose === 'function') target.dispose();
    },
  };
}
