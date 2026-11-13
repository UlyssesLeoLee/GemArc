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
 *   3. **The body is opaque.** A crystal on paper is a solid shape: paper
 *      does not let the light through, and a see-through body is the drawing
 *      of a window pane (BD-010 section 4.2 and 4.6).
 *   4. **No third-party import.** The three-dimensional library is injected
 *      by the caller (an import map for the static profile, node_modules for
 *      the vite profile, a CDN for the console). The only standard pieces
 *      this module needs are a toon material and a data texture.
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
 * Mix one colour into another.
 *
 * The retreat (the focus mode) used to make a crystal see-through. On paper a
 * body is solid, so the retreat now draws a crystal in a colour that is
 * closer to the paper - the way a drawing puts an unimportant shape in a
 * lighter ink instead of making it see-through.
 *
 * @param {number} color the colour to start from
 * @param {number} target the colour to mix into
 * @param {number} amount how much of the target to mix in, 0 to 1
 * @returns {number} the mixed colour
 */
export function mixToward(color, target, amount) {
  const t = Math.min(1, Math.max(0, amount));
  const mix = (shift) => {
    const from = (color >> shift) & 0xff;
    const to = (target >> shift) & 0xff;
    return Math.round(from + (to - from) * t);
  };
  return (mix(16) << 16) | (mix(8) << 8) | mix(0);
}

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
 * The self-light (`emissive`) is the one thing that may change over time: the
 * tool surface breathes with it. It adds no texture and no shader - the three
 * bands of the light stay the three bands (section 13.3).
 *
 * @param {object} THREE the three-dimensional library
 * @param {object} [options] the material options
 * @param {number} [options.color] the body colour
 * @param {number} [options.emissive] the self-light colour
 * @param {number} [options.emissiveIntensity] how strong the self-light is
 * @param {object} [options.gradient] the gradient map from {@link createToonGradient}
 * @returns {object} the material (dispose it with the scene)
 */
export function createToonMaterial(THREE, options = {}) {
  const material = new THREE.MeshToonMaterial({
    color: options.color ?? 0xffffff,
    // The body is solid: a crystal is a shape that was cut, not a lump of
    // translucent material (BD-010 section 4.2).
    transparent: false,
    opacity: 1,
    gradientMap: options.gradient ?? createToonGradient(THREE),
    emissive: options.emissive ?? 0x000000,
    emissiveIntensity: options.emissiveIntensity ?? 0,
  });
  return material;
}
