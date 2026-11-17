/**
 * A stand-in for the parts of three.js the space viewer uses.
 *
 * The rule that governs this file: **the stub must work the way the real
 * client works**, otherwise a passing test proves nothing. A stub that is
 * "close enough" would happily accept a layout where the node z coordinate
 * collapsed to zero, and the real browser would show the bug.
 *
 * So the stub keeps the geometry of three:
 *
 *   - `Vector3` / `Quaternion` implement the same operations, including
 *     `setFromUnitVectors`, which decides the orientation of an edge tube.
 *   - `Object3D` keeps the parent chain, and the world position is the sum of
 *     the chain. The hierarchy the viewer builds only translates, so this is
 *     exact.
 *   - `Raycaster` intersects **exactly**: a slab test for a box, and an
 *     infinite-cylinder test with the y range for an open cylinder. The edge
 *     pick tube uses an open cylinder, so a ray that only crosses the flat end
 *     must not hit it - the stub keeps that behaviour.
 *   - `CanvasTexture` and the sprite scale carry the measured text width, so
 *     a label that loses its aspect ratio is visible to the test.
 */

/* -------------------------------------------------------------- vectors */

export class Vector2 {
  constructor(x = 0, y = 0) {
    this.x = x;
    this.y = y;
  }

  set(x, y) {
    this.x = x;
    this.y = y;
    return this;
  }
}

export class Vector3 {
  constructor(x = 0, y = 0, z = 0) {
    this.x = x;
    this.y = y;
    this.z = z;
  }

  set(x, y, z) {
    this.x = x;
    this.y = y;
    this.z = z;
    return this;
  }

  copy(v) {
    this.x = v.x;
    this.y = v.y;
    this.z = v.z;
    return this;
  }

  clone() {
    return new Vector3(this.x, this.y, this.z);
  }

  setScalar(s) {
    this.x = s;
    this.y = s;
    this.z = s;
    return this;
  }

  /** Scale every component by the same factor. */
  multiplyScalar(s) {
    this.x *= s;
    this.y *= s;
    this.z *= s;
    return this;
  }

  add(v) {
    this.x += v.x;
    this.y += v.y;
    this.z += v.z;
    return this;
  }

  sub(v) {
    this.x -= v.x;
    this.y -= v.y;
    this.z -= v.z;
    return this;
  }

  dot(v) {
    return this.x * v.x + this.y * v.y + this.z * v.z;
  }

  cross(v) {
    return new Vector3(
      this.y * v.z - this.z * v.y,
      this.z * v.x - this.x * v.z,
      this.x * v.y - this.y * v.x,
    );
  }

  length() {
    return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z);
  }

  /** The distance to another point, the way the client reads it back. */
  distanceTo(v) {
    const dx = this.x - v.x;
    const dy = this.y - v.y;
    const dz = this.z - v.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  normalize() {
    const l = this.length() || 1;
    return this.set(this.x / l, this.y / l, this.z / l);
  }

  applyQuaternion(q) {
    const { x, y, z } = this;
    const ix = q.w * x + q.y * z - q.z * y;
    const iy = q.w * y + q.z * x - q.x * z;
    const iz = q.w * z + q.x * y - q.y * x;
    const iw = -q.x * x - q.y * y - q.z * z;
    return this.set(
      ix * q.w + iw * -q.x + iy * -q.z - iz * -q.y,
      iy * q.w + iw * -q.y + iz * -q.x - ix * -q.z,
      iz * q.w + iw * -q.z + ix * -q.y - iy * -q.x,
    );
  }

  /**
   * Project the point into the normalised device coordinates of a camera.
   *
   * This is the camera's own basis, not a helper of the view: a three camera
   * looks down its -Z axis, so the world direction of a pixel is
   * `cameraX * ndcX * tan * aspect + cameraY * ndcY * tan - cameraZ`.
   *
   * @param {object} camera the camera
   * @returns {Vector3} this vector
   */
  project(camera) {
    const target = camera.lookAtTarget || { x: 0, y: 0, z: 0 };
    const backward = new Vector3(
      camera.position.x - target.x,
      camera.position.y - target.y,
      camera.position.z - target.z,
    ).normalize();
    const right = new Vector3(backward.z, 0, -backward.x).normalize();
    const up = backward.clone().cross(right);
    const tanHalf = Math.tan((camera.fov * Math.PI) / 360);
    const offset = new Vector3(
      this.x - camera.position.x,
      this.y - camera.position.y,
      this.z - camera.position.z,
    );
    const depth = offset.dot(new Vector3(-backward.x, -backward.y, -backward.z));
    if (depth <= 0) {
      // Behind the camera: three mirrors the point, and the depth test leaves
      // it outside the frustum.
      this.x = 0;
      this.y = 0;
      this.z = 2;
      return this;
    }
    this.x = offset.dot(right) / (depth * tanHalf * camera.aspect);
    this.y = offset.dot(up) / (depth * tanHalf);
    this.z = depth > camera.far ? 2 : 0;
    return this;
  }
}

/** The rotation of an object: three keeps it as an Euler with three angles. */
export class Euler {
  constructor(x = 0, y = 0, z = 0) {
    this.x = x;
    this.y = y;
    this.z = z;
  }

  set(x, y, z) {
    this.x = x;
    this.y = y;
    this.z = z;
    return this;
  }
}

export class Quaternion {
  constructor(x = 0, y = 0, z = 0, w = 1) {
    this.x = x;
    this.y = y;
    this.z = z;
    this.w = w;
  }

  setFromUnitVectors(from, to) {
    let r = from.dot(to) + 1;
    if (r < 1e-8) {
      r = 0;
      if (Math.abs(from.x) > Math.abs(from.z)) {
        this.x = -from.y;
        this.y = from.x;
        this.z = 0;
        this.w = r;
      } else {
        this.x = 0;
        this.y = -from.z;
        this.z = from.y;
        this.w = r;
      }
    } else {
      this.x = from.y * to.z - from.z * to.y;
      this.y = from.z * to.x - from.x * to.z;
      this.z = from.x * to.y - from.y * to.x;
      this.w = r;
    }
    return this.normalize();
  }

  normalize() {
    const l = Math.sqrt(
      this.x * this.x + this.y * this.y + this.z * this.z + this.w * this.w,
    );
    if (l === 0) {
      this.x = 0;
      this.y = 0;
      this.z = 0;
      this.w = 1;
      return this;
    }
    const inv = 1 / l;
    this.x *= inv;
    this.y *= inv;
    this.z *= inv;
    this.w *= inv;
    return this;
  }

  /** The inverse rotation: the conjugate of a unit quaternion. */
  conjugate() {
    return new Quaternion(-this.x, -this.y, -this.z, this.w);
  }
}

export class Color {
  constructor(value = 0) {
    // The client accepts a colour or a number, and copies the colour when it
    // is given one: `new Color(otherColor)` is the same colour.
    this.hex = value && typeof value === 'object' && typeof value.getHex === 'function'
      ? value.getHex()
      : value;
  }

  setHex(value) {
    this.hex = value;
    return this;
  }

  /** The colour as a hex number, the way the client reads it back. */
  getHex() {
    return this.hex;
  }
}

/* ---------------------------------------------------------------- scene */

export class Object3D {
  constructor() {
    this.position = new Vector3();
    this.scale = new Vector3(1, 1, 1);
    this.rotation = new Euler();
    this.quaternion = new Quaternion();
    this.children = [];
    this.parent = null;
    this.userData = {};
    this.visible = true;
    this.renderOrder = 0;
  }

  add(...objects) {
    for (const object of objects) {
      if (!object) continue;
      object.parent = this;
      this.children.push(object);
    }
    return this;
  }

  clear() {
    for (const child of this.children) child.parent = null;
    this.children.length = 0;
    return this;
  }

  /** Detach a child, the way three does. */
  remove(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parent = null;
    }
    return this;
  }

  traverse(visit) {
    visit(this);
    for (const child of this.children) child.traverse(visit);
  }

  updateMatrixWorld() {
    // The stub computes world transforms from the parent chain on demand.
    return this;
  }

  /**
   * The world position of this object, written into the given vector.
   * @param {object} target the vector to write into
   * @returns {object} the target
   */
  getWorldPosition(target) {
    const point = this.worldPosition();
    if (target && typeof target.set === 'function') target.set(point.x, point.y, point.z);
    return target;
  }

  lookAt(x, y, z) {
    this.lookAtTarget = { x, y, z };
    return this;
  }

  /** The world position of this object. */
  worldPosition() {
    // The scale of an object applies to its geometry, never to its own
    // position. Only the ancestors transform the local position: a child sits
    // at `parentWorld + parentWorldScale * localPosition`.
    let x = 0;
    let y = 0;
    let z = 0;
    let scaleX = 1;
    let scaleY = 1;
    let scaleZ = 1;
    const chain = [];
    let node = this;
    while (node) {
      chain.unshift(node);
      node = node.parent;
    }
    for (const item of chain) {
      x += item.position.x * scaleX;
      y += item.position.y * scaleY;
      z += item.position.z * scaleZ;
      scaleX *= item.scale.x;
      scaleY *= item.scale.y;
      scaleZ *= item.scale.z;
    }
    return { x, y, z };
  }
}

export class Group extends Object3D {}
export class Scene extends Object3D {}
export class Mesh extends Object3D {
  constructor(geometry = null, material = null) {
    super();
    this.geometry = geometry;
    this.material = material;
    this.isMesh = true;
  }
}
export class LineSegments extends Object3D {
  constructor(geometry = null, material = null) {
    super();
    this.geometry = geometry;
    this.material = material;
  }
}
export class Sprite extends Object3D {
  constructor(material = null) {
    super();
    this.material = material;
    this.isSprite = true;
  }
}
export class AmbientLight extends Object3D {
  constructor(color = 0xffffff, intensity = 1) {
    super();
    this.color = new Color(color);
    this.intensity = intensity;
  }
}
export class DirectionalLight extends Object3D {
  constructor(color = 0xffffff, intensity = 1) {
    super();
    this.color = new Color(color);
    this.intensity = intensity;
    this.position = new Vector3();
  }
}
export class PerspectiveCamera extends Object3D {
  constructor(fov = 50, aspect = 1, near = 0.1, far = 2000) {
    super();
    this.fov = fov;
    this.aspect = aspect;
    this.near = near;
    this.far = far;
    this.isCamera = true;
  }

  updateProjectionMatrix() {
    this.projectionUpdated = true;
    return this;
  }
}

/* ------------------------------------------------------------ geometries */

/**
 * The shared behaviour of the geometries: the client can scale them and turn
 * them around, and the stub only has to remember that it was told to.
 */
class Geometry {
  scale(x = 1, y = 1, z = 1) {
    this.scaled = true;
    this.scaleFactor = { x, y, z };
    return this;
  }

  rotateY() {
    this.rotated = true;
    return this;
  }

  translate(x = 0, y = 0, z = 0) {
    this.translated = true;
    this.translation = { x, y, z };
    return this;
  }
}

class BoxGeometry extends Geometry {
  constructor(width = 1, height = 1, depth = 1) {
    super();
    this.type = 'BoxGeometry';
    this.parameters = { width, height, depth };
  }
}
class EdgesGeometry extends Geometry {
  constructor(source = null) {
    super();
    this.type = 'EdgesGeometry';
    this.parameters = source?.parameters || { width: 1, height: 1, depth: 1 };
  }
}
class CylinderGeometry extends Geometry {
  constructor(radiusTop = 1, radiusBottom = 1, height = 1, radialSegments = 8) {
    super();
    this.type = 'CylinderGeometry';
    this.parameters = {
      radiusTop,
      radiusBottom,
      height,
      radialSegments,
      openEnded: true,
    };
  }
}
class ConeGeometry extends Geometry {
  constructor(radius = 1, height = 1, radialSegments = 8) {
    super();
    this.type = 'ConeGeometry';
    this.parameters = { radius, height, radialSegments };
  }
}
class OctahedronGeometry extends Geometry {
  constructor(radius = 1, detail = 0) {
    super();
    this.type = 'OctahedronGeometry';
    this.parameters = { radius, detail };
  }
}
class PlaneGeometry extends Geometry {
  constructor(width = 1, height = 1) {
    super();
    this.type = 'PlaneGeometry';
    this.parameters = { width, height };
  }
}
class SphereGeometry extends Geometry {
  constructor(radius = 1, widthSegments = 8, heightSegments = 6) {
    super();
    this.type = 'SphereGeometry';
    this.parameters = { radius, widthSegments, heightSegments };
  }
}
class TubeGeometry extends Geometry {
  constructor(path = null, tubularSegments = 64, radius = 1, radialSegments = 8, closed = false) {
    super();
    this.type = 'TubeGeometry';
    this.parameters = { path, tubularSegments, radius, radialSegments, closed };
  }
}

/**
 * The quadratic curve an arrow shaft is bent along.
 *
 * The stub computes the same points the library does, so a test can say
 * where the bead of an arrow is.
 */
class QuadraticBezierCurve3 {
  constructor(v0 = new Vector3(), v1 = new Vector3(), v2 = new Vector3()) {
    this.v0 = v0;
    this.v1 = v1;
    this.v2 = v2;
    this.type = 'QuadraticBezierCurve3';
  }

  getPoint(t = 0, target = new Vector3()) {
    const u = 1 - t;
    target.set(
      u * u * this.v0.x + 2 * u * t * this.v1.x + t * t * this.v2.x,
      u * u * this.v0.y + 2 * u * t * this.v1.y + t * t * this.v2.y,
      u * u * this.v0.z + 2 * u * t * this.v1.z + t * t * this.v2.z,
    );
    return target;
  }

  getTangent(t = 0, target = new Vector3()) {
    const u = 1 - t;
    target.set(
      2 * u * (this.v1.x - this.v0.x) + 2 * t * (this.v2.x - this.v1.x),
      2 * u * (this.v1.y - this.v0.y) + 2 * t * (this.v2.y - this.v1.y),
      2 * u * (this.v1.z - this.v0.z) + 2 * t * (this.v2.z - this.v1.z),
    );
    return target.normalize();
  }
}
class LatheGeometry extends Geometry {
  constructor(points = [], segments = 8) {
    super();
    this.type = 'LatheGeometry';
    this.parameters = { points, segments };
  }

  /**
   * The client scales and moves the vertices of a turned stone, so the stub
   * does the same to the profile it was given: a stone that was moved up or
   * scaled down has to be picked where it really is.
   */
  scale(x = 1, y = 1, z = 1) {
    super.scale(x, y, z);
    for (const point of this.parameters.points) {
      point.x *= x;
      point.y *= y;
    }
    return this;
  }

  translate(x = 0, y = 0, z = 0) {
    super.translate(x, y, z);
    for (const point of this.parameters.points) {
      point.x += x;
      point.y += y;
    }
    return this;
  }
}

/* ------------------------------------------------------------- materials */

class MeshBasicMaterial {
  constructor(parameters = {}) {
    Object.assign(this, parameters);
    this.color = new Color(parameters.color ?? 0xffffff);
  }
}
class MeshStandardMaterial extends MeshBasicMaterial {}
class MeshToonMaterial extends MeshBasicMaterial {}
class MeshPhongMaterial extends MeshBasicMaterial {
  constructor(parameters = {}) {
    super(parameters);
    // The client keeps the specular colour as a colour object, not a number.
    this.specular = new Color(parameters.specular ?? 0x111111);
  }
}
/**
 * The stone material. The client keeps the attenuation colour as a colour
 * object, like every other colour it hands to a shader.
 */
class MeshPhysicalMaterial extends MeshBasicMaterial {
  constructor(parameters = {}) {
    super(parameters);
    this.attenuationColor = new Color(parameters.attenuationColor ?? 0xffffff);
  }
}
class LineBasicMaterial extends MeshBasicMaterial {}
class SpriteMaterial {
  constructor(parameters = {}) {
    Object.assign(this, parameters);
  }
}
class CanvasTexture {
  constructor(image = null) {
    this.image = image;
    this.isCanvasTexture = true;
  }
}
class DataTexture {
  constructor(data = null, width = 1, height = 1, format = null) {
    this.data = data;
    this.width = width;
    this.height = height;
    this.format = format;
    this.isDataTexture = true;
    this.needsUpdate = false;
    this.generateMipmaps = true;
    this.magFilter = null;
    this.minFilter = null;
  }
  dispose() {
    this.disposed = true;
  }
}

/* -------------------------------------------------------------- renderer */

export class WebGLRenderer {
  constructor(parameters = {}) {
    this.parameters = parameters;
    this.domElement = parameters.canvas || null;
    this.context = parameters.context || null;
    this.renderCount = 0;
    this.disposed = false;
    this.pixelRatio = 1;
    this.toneMapping = 0;
    this.toneMappingExposure = 1;
  }

  setPixelRatio(ratio) {
    this.pixelRatio = ratio;
    return this;
  }

  getPixelRatio() {
    return this.pixelRatio;
  }

  setSize(width, height, updateStyle = true) {
    this.width = width;
    this.height = height;
    if (this.domElement) {
      this.domElement.width = width;
      this.domElement.height = height;
    }
    if (updateStyle && this.domElement?.style) {
      this.domElement.style.width = `${width}px`;
      this.domElement.style.height = `${height}px`;
    }
    return this;
  }

  render() {
    this.renderCount += 1;
    return this;
  }

  dispose() {
    this.disposed = true;
    return this;
  }
}

/* ------------------------------------------------------------- raycaster */

/**
 * The ray/box intersection of three: a slab test.
 * @returns {number|null} the distance of the entry, or null
 */
function intersectBox(origin, direction, center, half) {
  let tmin = -Infinity;
  let tmax = Infinity;
  for (const axis of ['x', 'y', 'z']) {
    const o = origin[axis];
    const d = direction[axis];
    const lo = center[axis] - half[axis];
    const hi = center[axis] + half[axis];
    if (Math.abs(d) < 1e-12) {
      if (o < lo || o > hi) return null;
      continue;
    }
    let t1 = (lo - o) / d;
    let t2 = (hi - o) / d;
    if (t1 > t2) {
      const swap = t1;
      t1 = t2;
      t2 = swap;
    }
    tmin = Math.max(tmin, t1);
    tmax = Math.min(tmax, t2);
    if (tmin > tmax) return null;
  }
  if (tmax < 0) return null;
  return tmin >= 0 ? tmin : tmax;
}

/**
 * The ray/open cylinder intersection of three: the infinite cylinder around
 * the Y axis, clipped to the height range. The geometry is open ended, so the
 * flat ends are not surfaces and a ray that only crosses them does not hit.
 * @returns {number|null} the distance of the entry, or null
 */
function intersectCylinder(origin, direction, radius, halfHeight) {
  const a = direction.x * direction.x + direction.z * direction.z;
  const b = 2 * (origin.x * direction.x + origin.z * direction.z);
  const c = origin.x * origin.x + origin.z * origin.z - radius * radius;
  let t;
  if (Math.abs(a) < 1e-12) {
    if (c > 0) return null;
    t = 0;
  } else {
    const discriminant = b * b - 4 * a * c;
    if (discriminant < 0) return null;
    const root = Math.sqrt(discriminant);
    const t0 = (-b - root) / (2 * a);
    const t1 = (-b + root) / (2 * a);
    t = t0 >= 0 ? t0 : t1;
    if (t < 0) return null;
  }
  if (Math.abs(direction.y) < 1e-12) {
    if (Math.abs(origin.y) > halfHeight) return null;
  } else {
    const y = origin.y + direction.y * t;
    if (y < -halfHeight || y > halfHeight) return null;
  }
  return t;
}

/**
 * The ray/octahedron intersection of three: the solid |x|+|y|+|z| <= radius
 * is the intersection of eight half spaces, so the ray is clipped against
 * them one by one.
 * @returns {number|null} the distance of the entry, or null
 */
function intersectOctahedron(origin, direction, radius) {
  let tmin = -Infinity;
  let tmax = Infinity;
  for (const sx of [1, -1]) {
    for (const sy of [1, -1]) {
      for (const sz of [1, -1]) {
        const numerator = radius
          - (sx * origin.x + sy * origin.y + sz * origin.z);
        const denominator = sx * direction.x + sy * direction.y + sz * direction.z;
        if (Math.abs(denominator) < 1e-12) {
          // The ray runs parallel to this face: it is either inside it or out.
          if (numerator < 0) return null;
          continue;
        }
        const t = numerator / denominator;
        if (denominator > 0) {
          if (t < tmax) tmax = t;
        } else if (t > tmin) {
          tmin = t;
        }
      }
    }
  }
  if (tmin > tmax) return null;
  if (tmax < 0) return null;
  return tmin >= 0 ? tmin : tmax;
}

/**
 * The ray/sphere intersection of three: the sphere that encloses a turned
 * stone. The client tests the triangles of the stone; the stub tests the
 * sphere around them. The two agree at the middle of the stone, which is
 * where the pick tests aim, and the sphere is the same size the stone is -
 * so a click that lands on nothing in the client is still nothing here.
 * @returns {number|null} the distance to the entry, or null
 */
function intersectSphere(origin, direction, center, radius) {
  const ox = origin.x - center.x;
  const oy = origin.y - center.y;
  const oz = origin.z - center.z;
  const b = 2 * (ox * direction.x + oy * direction.y + oz * direction.z);
  const c = ox * ox + oy * oy + oz * oz - radius * radius;
  const discriminant = b * b - 4 * c;
  if (discriminant < 0) return null;
  const root = Math.sqrt(discriminant);
  let distance = (-b - root) / 2;
  if (distance < 0) distance = (-b + root) / 2;
  if (distance < 0) return null;
  return distance;
}

export class Raycaster {
  constructor() {
    this.ray = { origin: new Vector3(), direction: new Vector3(0, 0, -1) };
  }

  setFromCamera(ndc, camera) {
    const target = camera.lookAtTarget || { x: 0, y: 0, z: 0 };
    const forward = new Vector3(
      target.x - camera.position.x,
      target.y - camera.position.y,
      target.z - camera.position.z,
    ).normalize();
    const right = forward.clone().cross(new Vector3(0, 1, 0)).normalize();
    const up = right.clone().cross(forward).normalize();
    const tanHalfFov = Math.tan((camera.fov * Math.PI) / 360);
    // A three camera looks down its own -Z axis, so the world direction is
    // `-forward` plus the two screen axes. Getting this sign wrong points the
    // ray away from the scene, and every pick silently misses.
    const direction = forward.clone()
      .add(right.clone().multiplyScalar(ndc.x * tanHalfFov * camera.aspect))
      .add(up.clone().multiplyScalar(ndc.y * tanHalfFov))
      .normalize();
    this.ray.origin = camera.position.clone();
    this.ray.direction = direction;
    return this;
  }

  intersectObjects(objects, recursive = false) {
    const hits = [];
    for (const object of objects) {
      const hit = this.intersectObject(object);
      if (hit) hits.push(hit);
    }
    hits.sort((a, b) => a.distance - b.distance);
    return hits;
  }

  intersectObject(object) {
    const geometry = object.geometry;
    if (!geometry) return null;
    const origin = this.ray.origin;
    const direction = this.ray.direction;

    if (geometry.type === 'BoxGeometry') {
      const center = object.worldPosition();
      const half = {
        x: (geometry.parameters.width / 2) * Math.abs(object.scale.x),
        y: (geometry.parameters.height / 2) * Math.abs(object.scale.y),
        z: (geometry.parameters.depth / 2) * Math.abs(object.scale.z),
      };
      const distance = intersectBox(origin, direction, center, half);
      if (distance === null) return null;
      return { object, distance, point: null };
    }

    if (geometry.type === 'OctahedronGeometry') {
      const center = object.worldPosition();
      const radius = geometry.parameters.radius * Math.max(
        Math.abs(object.scale.x),
        Math.abs(object.scale.y),
        Math.abs(object.scale.z),
      );
      // The solid is centred on the object, so the ray is measured from the
      // centre: the octahedron is |x|+|y|+|z| <= radius around it.
      const localOrigin = {
        x: origin.x - center.x,
        y: origin.y - center.y,
        z: origin.z - center.z,
      };
      const distance = intersectOctahedron(localOrigin, direction, radius);
      if (distance === null) return null;
      return { object, distance, point: null };
    }

    if (geometry.type === 'CylinderGeometry') {
      const center = object.worldPosition();
      // Undo the orientation of the tube: the cylinder stands on its own Y
      // axis, and the mesh carries the rotation that aligns it with the edge.
      const inverse = object.quaternion.conjugate();
      const localOrigin = new Vector3(
        origin.x - center.x,
        origin.y - center.y,
        origin.z - center.z,
      ).applyQuaternion(inverse);
      const localDirection = direction.clone().applyQuaternion(inverse);
      const radius = geometry.parameters.radiusTop * Math.abs(object.scale.x);
      const halfHeight = (geometry.parameters.height / 2) * Math.abs(object.scale.y);
      const distance = intersectCylinder(localOrigin, localDirection, radius, halfHeight);
      if (distance === null) return null;
      return { object, distance, point: null };
    }

    if (geometry.type === 'LatheGeometry' || geometry.type === 'ConeGeometry') {
      const center = object.worldPosition();
      const scale = Math.max(
        Math.abs(object.scale.x),
        Math.abs(object.scale.y),
        Math.abs(object.scale.z),
      );
      let radius = 0;
      if (geometry.type === 'ConeGeometry') {
        radius = Math.hypot(
          geometry.parameters.radius,
          geometry.parameters.height / 2,
        );
      } else {
        for (const point of geometry.parameters.points || []) {
          const reach = Math.hypot(point.x, point.y);
          if (reach > radius) radius = reach;
        }
      }
      const distance = intersectSphere(origin, direction, center, radius * scale);
      if (distance === null) return null;
      return { object, distance, point: null };
    }

    return null;
  }
}

/* --------------------------------------------------- the environment baker */

/**
 * The pre-filtered environment baker. The stub hands back a texture it can
 * dispose, the way the client does, and it remembers how often it was asked
 * to bake a room.
 */
export class PMREMGenerator {
  constructor(renderer = null) {
    this.renderer = renderer;
    this.disposed = false;
    this.bakes = 0;
  }

  fromScene(scene = null, sigma = 0, near = 0.1, far = 100) {
    this.bakes += 1;
    return {
      texture: { isTexture: true, source: scene, sigma, near, far },
      dispose() {
        this.disposed = true;
      },
    };
  }

  dispose() {
    this.disposed = true;
  }
}

/* ---------------------------------------------------------------- export */

export const DoubleSide = 2;
export const FrontSide = 0;
export const BackSide = 1;
export const RedFormat = 1023;
export const NearestFilter = 1003;
export const AdditiveBlending = 2;
export const ACESFilmicToneMapping = 4;

/**
 * Build the stub three namespace.
 * @returns {object}
 */
export function createStubThree() {
  return {
    Vector2,
    Vector3,
    Quaternion,
    Color,
    Object3D,
    Group,
    Scene,
    Mesh,
    LineSegments,
    Sprite,
    AmbientLight,
    DirectionalLight,
    PerspectiveCamera,
    BoxGeometry,
    EdgesGeometry,
    CylinderGeometry,
    ConeGeometry,
    OctahedronGeometry,
    PlaneGeometry,
    SphereGeometry,
    TubeGeometry,
    QuadraticBezierCurve3,
    LatheGeometry,
    MeshBasicMaterial,
    MeshStandardMaterial,
    MeshToonMaterial,
    MeshPhongMaterial,
    MeshPhysicalMaterial,
    LineBasicMaterial,
    SpriteMaterial,
    CanvasTexture,
    DataTexture,
    WebGLRenderer,
    PMREMGenerator,
    Raycaster,
    DoubleSide,
    FrontSide,
    BackSide,
    RedFormat,
    NearestFilter,
    AdditiveBlending,
    ACESFilmicToneMapping,
  };
}

/**
 * A canvas stub with a WebGL context and a 2D context that measures text.
 * The measured width is proportional to the text length, so a label that
 * loses its aspect ratio is detectable.
 * @param {{webgl?: boolean}} [options] whether a WebGL context is available
 * @returns {object}
 */
export function createStubCanvas(options = {}) {
  const listeners = new Map();
  const measured = new Map();
  const canvas = {
    width: 300,
    height: 150,
    clientWidth: 800,
    clientHeight: 600,
    style: {},
    className: '',
    getContext(kind) {
      if (kind === '2d') {
        return {
          font: '',
          fillStyle: '',
          textBaseline: '',
          measureText(text) {
            const width = String(text).length * 12;
            measured.set(text, width);
            return { width };
          },
          fillRect() {},
          fillText() {},
        };
      }
      if (options.webgl === false) return null;
      return { canvas, kind };
    },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 800, height: 600 };
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      const list = listeners.get(type) || [];
      const index = list.indexOf(handler);
      if (index >= 0) list.splice(index, 1);
    },
    dispatch(type, event = {}) {
      for (const handler of [...(listeners.get(type) || [])]) handler(event);
    },
    measuredTextWidth(text) {
      return measured.get(text);
    },
  };
  return canvas;
}

/**
 * A container stub that behaves like the holder element of the 3D tab.
 * @param {object} [options]
 * @returns {object}
 */
export function createStubContainer(options = {}) {
  const canvas = options.canvas || createStubCanvas({ webgl: options.webgl });
  const container = {
    clientWidth: 800,
    clientHeight: 600,
    child: null,
    appendChild(child) {
      container.child = child;
      child.parentNode = container;
      return child;
    },
    removeChild(child) {
      if (container.child === child) container.child = null;
      child.parentNode = null;
      return child;
    },
    ownerDocument: {
      createElement(tag) {
        // The viewer creates exactly one canvas and appends it to the
        // container. The stub hands back the very same canvas, so a test can
        // dispatch the pointer events on it.
        if (tag === 'canvas') return canvas;
        return {};
      },
    },
  };
  container.canvas = canvas;
  return container;
}
