// Coordinate convention: the single place where the left-handed convention lives.
//   World: X = +Right, Y = +Up, Z = +Forward (away from the default camera).
//   Matrices are column-major Float32Array(16) (WebGL layout): m[col * 4 + row].
// Pure math; shared by the WebGL renderer, the picker and the tests. No DOM, no WebGL.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };

export const vec = {
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  len: (a) => Math.hypot(a[0], a[1], a[2]),
  norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
  lerp: (a, b, t) => [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t],
};

export function identity() { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }

// out = a * b  (apply b first, then a)
export function multiply(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    o[c * 4 + r] = s;
  }
  return o;
}

export function translation(x, y, z) { const m = identity(); m[12] = x; m[13] = y; m[14] = z; return m; }

export function transformPoint(m, p) {
  const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
  const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
  const z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
  const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
  return [x, y, z, w];
}

// Left-handed perspective, depth mapped to [-1, 1] (WebGL clip space). w_clip = +z_view (z grows away from the eye).
export function perspectiveMatrixLH(vFov, aspect, near, far) {
  const f = 1 / Math.tan(vFov / 2);
  const m = new Float32Array(16);
  m[0] = f / aspect; m[5] = f;
  m[10] = (far + near) / (far - near); m[11] = 1;
  m[14] = (-2 * far * near) / (far - near);
  return m;
}

// View basis in the left-handed convention: right = up x forward, up' = forward x right.
export function cameraFrame(eye, target, up = [0, 1, 0]) {
  const f = vec.norm(vec.sub(target, eye));
  let r = vec.cross(up, f);
  if (vec.len(r) < 1e-6) r = [1, 0, 0];
  r = vec.norm(r);
  const u = vec.cross(f, r);
  return { f, r, u };
}

export function aimMatrixLH(eye, target, up = [0, 1, 0]) {
  const { f, r, u } = cameraFrame(eye, target, up);
  const m = identity();
  m[0] = r[0]; m[4] = r[1]; m[8] = r[2]; m[12] = -vec.dot(r, eye);
  m[1] = u[0]; m[5] = u[1]; m[9] = u[2]; m[13] = -vec.dot(u, eye);
  m[2] = f[0]; m[6] = f[1]; m[10] = f[2]; m[14] = -vec.dot(f, eye);
  return m;
}

// Orbit camera. yaw rotates about +Y; pitch > 0 raises the camera (looks down).
// yaw = 0, pitch = 0: camera at -Z of the target looking toward +Z, so +X is on the right of the screen.
// minDist / maxDist bound the zoom; fitCamera widens them to the scene, so a wide scene can always be framed.
export function newCamera(o = {}) {
  return { yaw: 0.45, pitch: 0.35, dist: 34, target: [0, 3, 0], vFov: Math.PI / 4, near: 0.1, far: 400, minDist: 4, maxDist: 120, ...o };
}
export function cameraAhead(cam) {
  return [Math.sin(cam.yaw) * Math.cos(cam.pitch), -Math.sin(cam.pitch), Math.cos(cam.yaw) * Math.cos(cam.pitch)];
}
export function cameraPosition(cam) { return vec.sub(cam.target, vec.scale(cameraAhead(cam), cam.dist)); }
export function worldToView(cam) { return aimMatrixLH(cameraPosition(cam), cam.target); }
export function viewProj(cam, aspect) { return multiply(perspectiveMatrixLH(cam.vFov, aspect, cam.near, cam.far), worldToView(cam)); }

export function orbit(cam, yawDelta, pitchDelta) {
  return { ...cam, yaw: cam.yaw + yawDelta, pitch: Math.max(-1.45, Math.min(1.45, cam.pitch + pitchDelta)) };
}
export function pan(cam, dx, dy) {
  const { r, u } = cameraFrame(cameraPosition(cam), cam.target);
  const t = vec.add(cam.target, vec.add(vec.scale(r, dx), vec.scale(u, dy)));
  return { ...cam, target: t };
}
export function zoom(cam, factor) { return { ...cam, dist: Math.max(cam.minDist ?? 4, Math.min(cam.maxDist ?? 120, cam.dist * factor)) }; }

// --------- camera fit from scene bounds ---------
/** The camera with every field that is not a finite number (or not a 3-vector for `target`) replaced by the default; a sane camera is returned equal. */
export function saneCamera(cam) {
  const d = newCamera();
  const out = { ...d, ...(cam && typeof cam === 'object' ? cam : {}) };
  for (const k of Object.keys(d)) {
    if (k === 'target') { if (!finite3(out.target)) out.target = d.target.slice(); } else if (typeof out[k] !== 'number' || !Number.isFinite(out[k])) out[k] = d[k];
  }
  if (!(out.vFov > 0 && out.vFov < Math.PI)) out.vFov = d.vFov;
  return out;
}
const finite3 = (p) => Array.isArray(p) && p.length >= 3 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Number.isFinite(p[2]);

/** The eight corners of an axis-aligned box { min, max }. */
export function boxCorners(b) {
  const [x0, y0, z0] = b.min; const [x1, y1, z1] = b.max;
  return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
}
/** Bounds of the finite points: { min, max, center, radius } (radius of the enclosing sphere about the centre), or null when there are none. */
export function boundsOfPoints(points) {
  const pts = (points || []).filter(finite3);
  if (!pts.length) return null;
  const min = [Infinity, Infinity, Infinity]; const max = [-Infinity, -Infinity, -Infinity];
  for (const p of pts) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], p[k]); max[k] = Math.max(max[k], p[k]); }
  const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  let radius = 0;
  for (const p of pts) radius = Math.max(radius, vec.len(vec.sub(p, center)));
  return { min, max, center, radius };
}

// Frames `points` for the camera's current yaw / pitch: the target is the centre of their bounds and the distance is the smallest one at
// which every point is inside the frustum (exact for the given points, times `margin`). The zoom limits and the far plane follow the scene,
// so a scene wider than the default clamp can still be framed. Points that are not finite are ignored; with none, the camera is unchanged.
export function fitCamera(camIn, points, aspect = 1.6, margin = 1.08) {
  const cam = saneCamera(camIn);
  const b = boundsOfPoints(points);
  if (!b) return { ...cam };
  const asp = Number.isFinite(aspect) && aspect > 0 ? aspect : 1.6;
  const tanV = Math.tan(cam.vFov / 2); const tanH = tanV * asp;
  const target = b.center;
  const { f, r, u } = cameraFrame(vec.sub(target, vec.scale(cameraAhead(cam), 1)), target);
  let need = 0; let zMin = Infinity; let zMax = -Infinity;
  for (const p of points.filter(finite3)) {
    const d = vec.sub(p, target); const x = vec.dot(d, r); const y = vec.dot(d, u); const z = vec.dot(d, f);
    need = Math.max(need, Math.abs(x) / tanH - z, Math.abs(y) / tanV - z);
    zMin = Math.min(zMin, z); zMax = Math.max(zMax, z);
  }
  const dist = Math.max(need * margin, -zMin + 4 * cam.near, 2);
  return {
    ...cam, target, dist, far: Math.max(cam.far, (dist + zMax) * 1.5), minDist: Math.min(cam.minDist ?? 4, dist * 0.1), maxDist: Math.max(cam.maxDist ?? 120, dist * 3),
  };
}

/** Which of `points` project outside the w x h canvas (with `tol` px of slack) or behind the camera. Empty array = all inside. */
export function pointsOutside(cam, points, w, h, tol = 2) {
  const vp = viewProj(cam, w / h);
  const out = [];
  for (const p of points || []) {
    if (!finite3(p)) continue;
    const s = project(vp, p, w, h);
    if (!s.visible || !(s.x >= -tol && s.x <= w + tol && s.y >= -tol && s.y <= h + tol)) out.push(p);
  }
  return out;
}

// project to CSS pixels (y down). visible = in front of the camera and inside the near/far range.
export function project(vp, p, w, h) {
  const [x, y, z, cw] = transformPoint(vp, p);
  if (cw <= 1e-6) return { x: NaN, y: NaN, depth: cw, visible: false };
  const nx = x / cw; const ny = y / cw; const nz = z / cw;
  return { x: (nx * 0.5 + 0.5) * w, y: (0.5 - ny * 0.5) * h, depth: cw, clipZ: nz, visible: nz >= -1 && nz <= 1 };
}

// sign test of the upper 3x3 of a view matrix: +1 = world axes are left-handed relative to the left-handed view basis
export function chiralityOfView(vm) {
  const m = (r, c) => vm[c * 4 + r];
  const det = m(0, 0) * (m(1, 1) * m(2, 2) - m(1, 2) * m(2, 1)) - m(0, 1) * (m(1, 0) * m(2, 2) - m(1, 2) * m(2, 0)) + m(0, 2) * (m(1, 0) * m(2, 1) - m(1, 1) * m(2, 0));
  return { chirality: det > 0 ? 'Left' : 'Right', det };
}

// Left-handed sanity probe computed from the live matrices (not asserted by constant).
export function conventionProbe(cam, w = 800, h = 600) {
  const vm = worldToView(cam);
  const vp = viewProj(cam, w / h);
  const origin = cam.target;
  const axes = {};
  for (const [k, a] of Object.entries(AXES)) {
    const tip = vec.add(origin, vec.scale(a, 2));
    const po = project(vp, origin, w, h); const pt = project(vp, tip, w, h);
    const vo = transformPoint(vm, origin); const vt = transformPoint(vm, tip);
    axes[k] = { dx: pt.x - po.x, dy: pt.y - po.y, depthDelta: vt[2] - vo[2] };
  }
  return { ...chiralityOfView(vm), axes };
}

// --------- picking ---------
export function screenRay(cam, x, y, w, h) {
  const eye = cameraPosition(cam);
  const { f, r, u } = cameraFrame(eye, cam.target);
  const t = Math.tan(cam.vFov / 2);
  const nx = (x / w) * 2 - 1; const ny = 1 - (y / h) * 2;
  const dir = vec.norm(vec.add(f, vec.add(vec.scale(r, nx * t * (w / h)), vec.scale(u, ny * t))));
  return { origin: eye, dir };
}
export function rayDistance(p, ray) {
  const v = vec.sub(p, ray.origin);
  const t = Math.max(0, vec.dot(v, ray.dir));
  return { dist: vec.len(vec.sub(v, vec.scale(ray.dir, t))), t };
}
export function pointSegmentDistance(px, py, ax, ay, bx, by) {
  const vx = bx - ax; const vy = by - ay;
  const l2 = vx * vx + vy * vy;
  let t = l2 === 0 ? 0 : ((px - ax) * vx + (py - ay) * vy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + vx * t), py - (ay + vy * t));
}

// items: { nodes:[{id,pos,radius}], edges:[{id, a:[x,y,z], b:[x,y,z]}] }. Nodes win over edges; nearest node along the ray wins.
export function pick(cam, items, x, y, w, h, edgeTol = 6) {
  const nodes = Array.isArray(items?.nodes) ? items.nodes : []; const edges = Array.isArray(items?.edges) ? items.edges : [];
  const ray = screenRay(cam, x, y, w, h);
  let best = null;
  for (const n of nodes) {
    const { dist, t } = rayDistance(n.pos, ray);
    if (dist <= n.radius && (!best || t < best.t)) best = { type: 'node', id: n.id, t };
  }
  if (best) return best;
  const vp = viewProj(cam, w / h);
  let bestEdge = null;
  for (const e of edges) {
    const pa = project(vp, e.a, w, h); const pb = project(vp, e.b, w, h);
    if (!pa.visible || !pb.visible) continue;
    const d = pointSegmentDistance(x, y, pa.x, pa.y, pb.x, pb.y);
    if (d <= edgeTol && (!bestEdge || d < bestEdge.d)) bestEdge = { type: 'edge', id: e.id, d };
  }
  return bestEdge;
}

// --------- edge geometry (parallel edges, self loops) ---------
export function edgePath(a, b, index = 0, count = 1, segments = 10) {
  const d = vec.sub(b, a);
  if (vec.len(d) < 1e-6) {
    const r = 0.7 + index * 0.25; const pts = [];
    for (let i = 0; i <= 16; i++) { const th = (i / 16) * Math.PI * 2; pts.push([a[0] + Math.cos(th) * r, a[1] + 0.6 + Math.sin(th) * r * 0.6, a[2] + Math.sin(th) * r * 0.4]); }
    return pts;
  }
  const dir = vec.norm(d);
  let side = vec.cross(dir, [0, 1, 0]);
  if (vec.len(side) < 1e-4) side = [1, 0, 0];
  side = vec.norm(side);
  const off = (index - (count - 1) / 2) * 0.55;
  const sway = count > 1 ? off : 0;
  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const p = vec.lerp(a, b, t);
    const k = Math.sin(Math.PI * t) * sway;
    pts.push(vec.add(p, vec.scale(side, k)));
  }
  return pts;
}
export function arrowTip(p0, p1, size = 0.35) {
  const dir = vec.norm(vec.sub(p1, p0));
  let side = vec.cross(dir, [0, 1, 0]);
  if (vec.len(side) < 1e-4) side = [1, 0, 0];
  side = vec.norm(side);
  const base = vec.sub(p1, vec.scale(dir, size));
  return [vec.add(base, vec.scale(side, size * 0.45)), p1, vec.sub(base, vec.scale(side, size * 0.45))];
}
