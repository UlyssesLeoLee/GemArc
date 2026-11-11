/**
 * The one toon (cel) material of the product (BD-010 section 13).
 *
 * The style policy asks for a comic-book cel look in real three dimensions:
 * the light is quantised into a small number of hard bands on the cut
 * faces of a crystal, and the ink line is the crystal's own bright edge.
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
 *   3. **The body stays translucent.** The opacity range of section 4.2 is
 *      unchanged: a crystal that is opaque is a painted ball.
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
 * @type {ReadonlyArray<number>}
 */
export const TOON_BAND_STEPS = Object.freeze([0x73, 0xc7, 0xff]);

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
 * @param {number} [options.opacity] the body opacity (0.45-0.7, section 4.2)
 * @param {number} [options.emissive] the self-light colour
 * @param {number} [options.emissiveIntensity] how strong the self-light is
 * @param {object} [options.gradient] the gradient map from {@link createToonGradient}
 * @returns {object} the material (dispose it with the scene)
 */
export function createToonMaterial(THREE, options = {}) {
  const material = new THREE.MeshToonMaterial({
    color: options.color ?? 0xffffff,
    transparent: true,
    opacity: options.opacity ?? 0.62,
    gradientMap: options.gradient ?? createToonGradient(THREE),
    emissive: options.emissive ?? 0x000000,
    emissiveIntensity: options.emissiveIntensity ?? 0,
  });
  return material;
}
