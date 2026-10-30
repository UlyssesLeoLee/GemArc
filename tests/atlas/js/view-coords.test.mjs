import test from 'node:test';
import assert from 'node:assert/strict';
import { rng } from './view-helpers.mjs';
import * as C from '../../../src/gemarc_mcp/atlas/viewer/src/coords.js';

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} vs ${b}`);
const cam0 = (o) => C.newCamera({ yaw: 0, pitch: 0, dist: 20, target: [0, 0, 0], ...o });

test('default orientation: +X is right, +Y is up, +Z goes away from the viewer (left-handed)', () => {
  const r = C.conventionProbe(cam0());
  assert.equal(r.chirality, 'Left');
  assert.ok(r.axes.x.dx > 0 && Math.abs(r.axes.x.dy) < 1e-6);
  assert.ok(r.axes.y.dy < 0); // screen y grows lower
  assert.ok(r.axes.z.depthDelta > 0);
});

test('a mirrored view is detected as Right, so the probe is not a constant', () => {
  const vm = C.worldToView(cam0()); const mirrored = vm.slice(); mirrored[0] = -mirrored[0]; mirrored[4] = -mirrored[4]; mirrored[8] = -mirrored[8];
  assert.equal(C.chiralityOfView(mirrored).chirality, 'Right');
});

test('orbit clamps pitch, pan moves along the screen plane, zoom stays inside the camera limits', () => {
  const c = C.orbit(cam0(), 0.5, 99); assert.ok(c.pitch <= 1.45); near(c.yaw, 0.5);
  assert.ok(C.orbit(cam0(), 0, -99).pitch >= -1.45);
  const p = C.pan(cam0(), 3, 0); near(p.target[0], 3); near(p.target[1], 0);
  assert.equal(C.zoom(cam0(), 1e-6).dist, 4); assert.equal(C.zoom(cam0(), 1e6).dist, 120);
  const wide = cam0({ minDist: 1, maxDist: 5000 });
  assert.equal(C.zoom(wide, 1e6).dist, 5000); assert.equal(C.zoom(wide, 1e-9).dist, 1);
});

test('matrices multiply right to left; translations compose by addition', () => {
  const m = C.multiply(C.translation(1, 2, 3), C.translation(10, 20, 30));
  assert.deepEqual(C.transformPoint(m, [0, 0, 0]).slice(0, 3), [11, 22, 33]);
  assert.deepEqual(Array.from(C.multiply(C.identity(), m)), Array.from(m));
});

test('perspectiveMatrixLH maps near to -1 and far to +1; aimMatrixLH keeps the target on the forward axis', () => {
  const P = C.perspectiveMatrixLH(Math.PI / 4, 1.5, 0.1, 400);
  const z = (d) => { const [, , zz, w] = C.transformPoint(P, [0, 0, d]); return zz / w; };
  near(z(0.1), -1, 1e-4); near(z(400), 1, 1e-4);
  const v = C.aimMatrixLH([0, 0, -10], [0, 0, 0]); const [x, y, zz] = C.transformPoint(v, [0, 0, 0]);
  near(x, 0); near(y, 0); near(zz, 10);
});

test('picking: a projected node centre selects exactly that node, nearest first, empty space selects nothing', () => {
  const cam = cam0(); const vp = C.viewProj(cam, 1.6);
  const items = { nodes: [{ id: 'near', pos: [0, 0, -2], radius: 0.7 }, { id: 'far', pos: [0, 0, 3], radius: 0.7 }, { id: 'side', pos: [6, 1, 0], radius: 0.7 }], edges: [] };
  const s = C.project(vp, items.nodes[2].pos, 800, 500);
  assert.equal(C.pick(cam, items, s.x, s.y, 800, 500).id, 'side');
  const o = C.project(vp, [0, 0, 0], 800, 500);
  const hit = C.pick(cam, { nodes: [items.nodes[0], items.nodes[1]], edges: [] }, o.x, o.y, 800, 500);
  assert.equal(hit.id, 'near');
  assert.equal(C.pick(cam, items, 5, 5, 800, 500), null);
});

test('edges are pickable, nodes win over edges, endpoints behind the camera are never picked', () => {
  const cam = cam0(); const vp = C.viewProj(cam, 1.6);
  const e = { id: 'e1', a: [-4, 0, 0], b: [4, 0, 0] };
  const mid = C.project(vp, [0, 0, 0], 800, 500);
  assert.deepEqual(C.pick(cam, { nodes: [], edges: [e] }, mid.x, mid.y + 2, 800, 500).id, 'e1');
  assert.equal(C.pick(cam, { nodes: [{ id: 'n', pos: [0, 0, 0], radius: 1 }], edges: [e] }, mid.x, mid.y, 800, 500).type, 'node');
  const behind = { id: 'b', a: [0, 0, -40], b: [3, 0, -40] };
  assert.equal(C.pick(cam, { nodes: [], edges: [behind] }, 400, 250, 800, 500), null);
});

test('parallel edges and self loops keep their endpoints and stay distinct', () => {
  const a = [0, 0, 0]; const b = [6, 0, 0];
  const p0 = C.edgePath(a, b, 0, 3); const p1 = C.edgePath(a, b, 1, 3); const p2 = C.edgePath(a, b, 2, 3);
  for (const p of [p0, p1, p2]) { p[0].forEach((x, k) => near(x, a[k])); p.at(-1).forEach((x, k) => near(x, b[k])); }
  assert.notDeepEqual(p0[5], p1[5]); assert.notDeepEqual(p1[5], p2[5]);
  const loop = C.edgePath(a, a, 0, 1); assert.equal(loop.length, 17); assert.ok(loop.every((q) => q.every(Number.isFinite)));
  const head = C.arrowTip([0, 0, 0], [1, 0, 0]); assert.deepEqual(head[1], [1, 0, 0]);
});

test('fitCamera: every point of scenes of any size lands inside the canvas', () => {
  const r = rng(77);
  for (const [w, h, d] of [[10, 5, 5], [104, 17, 9], [300, 20, 20], [2000, 60, 40], [1, 1, 1], [0, 0, 0], [50, 400, 3]]) {
    const pts = Array.from({ length: 40 }, () => [(r.next() - 0.5) * w, r.next() * h, (r.next() - 0.5) * d]);
    for (const [aw, ah] of [[800, 500], [1600, 700], [400, 800], [320, 240]]) {
      const cam = C.fitCamera(C.newCamera(), pts, aw / ah);
      assert.deepEqual(C.pointsOutside(cam, pts, aw, ah, 1), [], `${w}x${h}x${d} @ ${aw}x${ah}`);
      assert.ok(cam.far > cam.dist && cam.near < cam.dist);
    }
  }
});

test('fitCamera: a scene wider than the default zoom clamp can still be framed and zoomed', () => {
  const pts = [[-500, 0, 0], [500, 20, 10], [0, 40, -10]];
  const cam = C.fitCamera(C.newCamera({ yaw: 0, pitch: 0.2 }), pts, 1.6);
  assert.ok(cam.dist > 120 && cam.maxDist >= cam.dist * 3 && cam.minDist <= 4);
  assert.deepEqual(C.pointsOutside(cam, pts, 900, 560, 1), []);
  assert.equal(C.zoom(cam, 0.5).dist, cam.dist * 0.5);
  // the default clamp could not: pointsOutside reports the points when the camera is held at 120
  const clamped = { ...cam, dist: 120 };
  assert.ok(C.pointsOutside(clamped, pts, 900, 560, 1).length > 0);
});

test('fitCamera ignores non-finite points and leaves the camera alone when there is nothing to frame', () => {
  const base = C.newCamera();
  assert.deepEqual(C.fitCamera(base, [], 1.6), base);
  assert.deepEqual(C.fitCamera(base, [[NaN, 0, 0], [Infinity, 1, 1]], 1.6), base);
  const cam = C.fitCamera(base, [[0, 0, 0], [NaN, 1, 1], [4, 2, 1]], 1.6);
  assert.ok(Object.values(cam).flat().every((x) => typeof x !== 'number' || Number.isFinite(x)));
  assert.ok(Number.isFinite(C.fitCamera(base, [[1, 1, 1]], NaN).dist));
});

test('boundsOfPoints and boxCorners', () => {
  const b = C.boundsOfPoints([[0, 0, 0], [2, 4, 6]]); assert.deepEqual(b.center, [1, 2, 3]); near(b.radius, Math.hypot(1, 2, 3));
  assert.equal(C.boundsOfPoints([]), null);
  assert.equal(C.boxCorners({ min: [0, 0, 0], max: [1, 1, 1] }).length, 8);
});

test('project reports points behind the camera as not visible', () => {
  const vp = C.viewProj(cam0(), 1.6);
  const s = C.project(vp, [0, 0, -50], 800, 500); assert.equal(s.visible, false); assert.ok(Number.isNaN(s.x));
});
