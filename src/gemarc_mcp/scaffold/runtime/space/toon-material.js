/**
 * The one toon (cel) material of the product (BD-010 section 13).
 *
 * The style policy asks for an ink drawing of a crystal in real three
 * dimensions: the light is quantised into a small number of hard bands on the
 * cut faces of a crystal, and the ink line is the crystal's own edge.
 * Three scenes draw crystals - the semantic-axis scene, the building scene
 * and the console's tool-surface scene - and they must not drift apart, so
 * the material lives here once and everybody imports it.
 *
 * The rules this module keeps:
 *
 *   1. **The light is banded, not graded.** A three-step gradient map turns
 *      the shading into three flat steps; there is no continuous ramp
 *      anywhere, which is what makes it read as cel shading rather than as
 *      polished stone.
 *   2. **The gradient is a constant.** It never changes with the entity, the
 *      selection or the frame, so the same model renders the same pixels
 *      twice (BD-010 section 11.5).
 *   3. **The body is translucent.** A crystal is a stone that was cut, and a
 *      cut stone is transparent: you see through it, and you see its own
 *      inner cut faces. The body therefore does not write depth and is drawn
 *      on both sides (BD-010 section 4.2). The ink line stays solid, so the
 *      silhouette is still a drawn one.
 *   4. **The stone has a shine.** Transparency alone reads as a sheet of
 *      colored paper; what makes a cut stone a stone is the one reflection
 *      on its cut faces. The shine is a second layer over the same geometry
 *      (BD-010 section 4.7): it carries the specular term only, it is added
 *      on top of the body, and it never reaches past the paper's own white.
 *   5. **No third-party import.** The three-dimensional library is injected
 *      by the caller (an import map for the static profile, node_modules for
 *      the vite profile, a CDN for the console). The only standard pieces
 *      this module needs are a toon material, a phong material and a data
 *      texture.
 *
 * @module toon-material
 */

/**
 * How many bands the light is quantised into.
 * @type {number}
 */
export const TOON_BAND_COUNT = 3;

/**
 * The brightness of each band, from the darkest to the lightest. The values
 * scale the colour of the crystal, so the darkest band is still the same
 * hue - a band never turns into a different colour.
 *
 * The darkest band is 55% and never darker: on paper the dark band means
 * "this face is turned away from the light", not "this is a hole"
 * (BD-010 section 13.1).
 * @type {ReadonlyArray<number>}
 */
export const TOON_BAND_STEPS = Object.freeze([0x8c, 0xc7, 0xff]);

/**
 * How transparent the body of a crystal is.
 *
 * A cut stone is transparent, and the policy puts a number on it: the body
 * sits between 0.45 and 0.62 (BD-010 section 4.2). Below 0.45 the stone turns
 * into a stain of colour on the paper; above 0.62 it stops being transparent
 * at all and the inner cut faces stop reading.
 *
 * @type {number}
 */
export const CRYSTAL_BODY_OPACITY = 0.52;

/**
 * The lowest opacity the body may have.
 * @type {number}
 */
export const CRYSTAL_BODY_OPACITY_MIN = 0.45;

/**
 * The highest opacity the body may have.
 * @type {number}
 */
export const CRYSTAL_BODY_OPACITY_MAX = 0.62;

/**
 * How sharp the shine on a cut face is.
 *
 * A cut face has one normal, so the whole face carries one brightness and
 * the border between two faces stays the crystal's own edge (BD-010 section
 * 4.7). A high number keeps the reflection on the face that really looks
 * into the light, so the glint sits on one cut instead of spreading over the
 * whole stone.
 *
 * @type {number}
 */
export const CRYSTAL_HIGHLIGHT_SHININESS = 18;

/**
 * How strong the shine on a cut face is.
 *
 * The shine is added on top of the paper, so it can never be brighter than
 * the paper itself: the ceiling is the paper's white, not a pure white
 * (section 4.7).
 *
 * @type {number}
 */
export const CRYSTAL_HIGHLIGHT_OPACITY = 0.45;

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
 * Build the gradient map: one row of pixels, one pixel per band.
 *
 * The texture uses the red channel only (the library samples it as a single
 * value) and nearest filtering, so the step boundaries stay hard.
 *
 * @param {object} THREE the three-dimensional library
 * @returns {object} the gradient texture (dispose it with the scene)
 */
export function createToonGradient(THREE) {
  const data = new Uint8Array(TOON_BAND_STEPS);
  const texture = new THREE.DataTexture(data, TOON_BAND_STEPS.length, 1, THREE.RedFormat);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

/**
 * Build one crystal body material.
 *
 * The cut faces are the geometry's own: a crystal is a polyhedron, so every
 * face already carries its own direction and the banded light breaks on them.
 * (The toon material has no `flatShading` switch; asking for one only produces
 * a warning.)
 *
 * The body is translucent and does not write depth, and it is drawn on both
 * sides: that is what makes a stone read as a stone instead of a colored
 * pane (BD-010 section 4.2). The ink line is a separate, solid geometry, so
 * the silhouette stays sharp.
 *
 * The self-light (`emissive`) is the one thing that may change over time: the
 * tool surface breathes with it. It adds no texture and no shader - the three
 * bands of the light stay the three bands (section 13.3).
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} [options] the material options
 * @param {number} [options.color] the body colour
 * @param {number} [options.opacity] the body opacity (0.45-0.62, section 4.2)
 * @param {number} [options.emissive] the self-light colour
 * @param {number} [options.emissiveIntensity] how strong the self-light is
 * @param {object} [options.gradient] the gradient map from {@link createToonGradient}
 * @returns {object} the material (dispose it with the scene)
 */
export function createToonMaterial(THREE, options = {}) {
  const material = new THREE.MeshToonMaterial({
    color: options.color ?? 0xffffff,
    // The stone is transparent, and it does not write depth: what is behind
    // it stays visible, and its own cut faces show through.
    transparent: true,
    opacity: options.opacity ?? CRYSTAL_BODY_OPACITY,
    depthWrite: false,
    side: THREE.DoubleSide,
    gradientMap: options.gradient ?? createToonGradient(THREE),
    emissive: options.emissive ?? 0x000000,
    emissiveIntensity: options.emissiveIntensity ?? 0,
  });
  return material;
}

/**
 * Build the material of the shine on a cut face.
 *
 * The body is a toon material: it knows the three bands of the light and
 * nothing else. It has no specular term, so a transparent body reads as a
 * sheet of colored paper. The shine is the second layer that puts the one
 * reflection back (BD-010 section 4.7):
 *
 *   - **no diffuse light**: the colour is black, which means "nothing but
 *     the reflection" and not a colour on the paper;
 *   - **the paper's own white** as the specular colour, so the shine can
 *     never be brighter than the paper;
 *   - **flat shading**: one brightness per cut face, so the border between
 *     two faces stays a hard edge instead of a soft patch;
 *   - **added on top** of the body, so the stone keeps its colour
 *     underneath the reflection;
 *   - **no depth written** and **the outward faces only**: the inner faces
 *     are seen through the stone, they do not catch the light a second time.
 *
 * The material is the same for the three scenes, so the shine cannot drift
 * apart between them (section 13.4).
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} [options] the material options
 * @param {number} [options.specular] the colour of the reflection
 * @param {number} [options.shininess] how sharp the reflection is
 * @param {number} [options.opacity] how strong the reflection is
 * @returns {object} the material (dispose it with the scene)
 */
export function createFacetHighlightMaterial(THREE, options = {}) {
  return new THREE.MeshPhongMaterial({
    // No diffuse light: this layer carries the reflection only.
    color: 0x000000,
    // The reflection is the paper's white, never a pure white.
    specular: options.specular ?? PALETTE.panel,
    shininess: options.shininess ?? CRYSTAL_HIGHLIGHT_SHININESS,
    // One brightness per cut face: the edge between two faces stays hard.
    flatShading: true,
    transparent: true,
    opacity: options.opacity ?? CRYSTAL_HIGHLIGHT_OPACITY,
    // The shine never hides what is behind the stone.
    depthWrite: false,
    // Added on top of the body: the stone keeps its colour underneath.
    blending: THREE.AdditiveBlending,
    // The outward faces only: the inner faces are seen through the stone.
    side: THREE.FrontSide,
  });
}
