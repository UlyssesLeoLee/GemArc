(function () {
'use strict';
// ---- assemble.js
const __mod_assemble = (function () {
'use strict';
const LEVELS = ['REQ', 'BD', 'DD'];
const PREFIX = ['RD', 'BD', 'DD'];
const SHIFT = 3;
const TARGET_LIMIT = 6;
const PH = ['\u0001DOCNO\u0001', '\u0001UPPER\u0001', '\u0001LOWER\u0001', '\u0001TARGET\u0001'];
function hash32Hex(text) {
let h = 2166136261;
for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 16777619) >>> 0;
return h.toString(16).padStart(8, '0');
}
function fragmentIds(ds, picked, k) {
const items = ds.fragments.items; const bit = 1 << k; const out = new Set();
for (const x of ds.fragments.always) if (x & bit) out.add(x >> SHIFT);
for (const id of picked) for (const x of ds.closures[id]) if (x & bit) out.add(x >> SHIFT);
return [...out].sort((a, b) => items[a].chapter - items[b].chapter || items[a].order - items[b].order);
}
function assemble(ds, level, ids) {
const k = LEVELS.indexOf(level);
if (k < 0) throw new Error('unknown level ' + level);
const fs = ds.fragments; const items = fs.items; const frame = fs.frame[level];
const pos = new Map(fs.elements.map((id, n) => [id, n]));
const picked = [...new Set((ids || []).filter((id) => pos.has(id)))].sort((a, b) => pos.get(a) - pos.get(b));
const sets = [0, 1, 2].map((n) => fragmentIds(ds, picked, n));
const nos = sets.map((s, n) => PREFIX[n] + '-' + hash32Hex(LEVELS[n] + '\n' + fs.modelSha8 + '\n' + s.join(',')));
const by = new Map();
for (const f of sets[k]) { if (!by.has(items[f].grp)) by.set(items[f].grp, []); by.get(items[f].grp).push(items[f].md); }
const parts = [frame.head];
for (const ch of frame.chapters) {
parts.push(ch.h);
if (ch.note) parts.push(ch.note);
const body = [];
for (const gid of ch.groups) {
const g = fs.groups[gid]; const members = by.get(gid);
if (members) body.push((g.head ? [g.head, ...members] : members).join(g.join));
else if (g.empty) body.push(g.empty);
}
parts.push(body.length ? body.join('\n\n') : ch.empty);
}
let target = fs.unselected;
if (picked.length) {
const shown = picked.slice(0, TARGET_LIMIT).map((id) => '`' + id.split('`').join("'").split('|').join('\\|') + '`').join(', ');
target = shown + (picked.length > TARGET_LIMIT ? ' +' + (picked.length - TARGET_LIMIT) : '');
}
let text = parts.join('\n\n') + '\n';
const values = [nos[k], k > 0 ? nos[k - 1] : '', k < 2 ? nos[k + 1] : '', target];
PH.forEach((p, n) => { text = text.split(p).join(values[n]); });
return text;
}
return { LEVELS, hash32Hex, assemble };
})();
// ---- coords.js
const __mod_coords = (function () {
'use strict';
const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const vec = {
add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
len: (a) => Math.hypot(a[0], a[1], a[2]),
norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
lerp: (a, b, t) => [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t],
};
function identity() { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }
function multiply(a, b) {
const o = new Float32Array(16);
for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
let s = 0;
for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
o[c * 4 + r] = s;
}
return o;
}
function translation(x, y, z) { const m = identity(); m[12] = x; m[13] = y; m[14] = z; return m; }
function transformPoint(m, p) {
const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
const z = m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14];
const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
return [x, y, z, w];
}
function perspectiveMatrixLH(vFov, aspect, near, far) {
const f = 1 / Math.tan(vFov / 2);
const m = new Float32Array(16);
m[0] = f / aspect; m[5] = f;
m[10] = (far + near) / (far - near); m[11] = 1;
m[14] = (-2 * far * near) / (far - near);
return m;
}
function cameraFrame(eye, target, up = [0, 1, 0]) {
const f = vec.norm(vec.sub(target, eye));
let r = vec.cross(up, f);
if (vec.len(r) < 1e-6) r = [1, 0, 0];
r = vec.norm(r);
const u = vec.cross(f, r);
return { f, r, u };
}
function aimMatrixLH(eye, target, up = [0, 1, 0]) {
const { f, r, u } = cameraFrame(eye, target, up);
const m = identity();
m[0] = r[0]; m[4] = r[1]; m[8] = r[2]; m[12] = -vec.dot(r, eye);
m[1] = u[0]; m[5] = u[1]; m[9] = u[2]; m[13] = -vec.dot(u, eye);
m[2] = f[0]; m[6] = f[1]; m[10] = f[2]; m[14] = -vec.dot(f, eye);
return m;
}
function newCamera(o = {}) {
return { yaw: 0.45, pitch: 0.35, dist: 34, target: [0, 3, 0], vFov: Math.PI / 4, near: 0.1, far: 400, minDist: 4, maxDist: 120, ...o };
}
function cameraAhead(cam) {
return [Math.sin(cam.yaw) * Math.cos(cam.pitch), -Math.sin(cam.pitch), Math.cos(cam.yaw) * Math.cos(cam.pitch)];
}
function cameraPosition(cam) { return vec.sub(cam.target, vec.scale(cameraAhead(cam), cam.dist)); }
function worldToView(cam) { return aimMatrixLH(cameraPosition(cam), cam.target); }
function viewProj(cam, aspect) { return multiply(perspectiveMatrixLH(cam.vFov, aspect, cam.near, cam.far), worldToView(cam)); }
function orbit(cam, yawDelta, pitchDelta) {
return { ...cam, yaw: cam.yaw + yawDelta, pitch: Math.max(-1.45, Math.min(1.45, cam.pitch + pitchDelta)) };
}
function pan(cam, dx, dy) {
const { r, u } = cameraFrame(cameraPosition(cam), cam.target);
const t = vec.add(cam.target, vec.add(vec.scale(r, dx), vec.scale(u, dy)));
return { ...cam, target: t };
}
function zoom(cam, factor) { return { ...cam, dist: Math.max(cam.minDist ?? 4, Math.min(cam.maxDist ?? 120, cam.dist * factor)) }; }
function saneCamera(cam) {
const d = newCamera();
const out = { ...d, ...(cam && typeof cam === 'object' ? cam : {}) };
for (const k of Object.keys(d)) {
if (k === 'target') { if (!finite3(out.target)) out.target = d.target.slice(); } else if (typeof out[k] !== 'number' || !Number.isFinite(out[k])) out[k] = d[k];
}
if (!(out.vFov > 0 && out.vFov < Math.PI)) out.vFov = d.vFov;
return out;
}
const finite3 = (p) => Array.isArray(p) && p.length >= 3 && Number.isFinite(p[0]) && Number.isFinite(p[1]) && Number.isFinite(p[2]);
function boxCorners(b) {
const [x0, y0, z0] = b.min; const [x1, y1, z1] = b.max;
return [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
}
function boundsOfPoints(points) {
const pts = (points || []).filter(finite3);
if (!pts.length) return null;
const min = [Infinity, Infinity, Infinity]; const max = [-Infinity, -Infinity, -Infinity];
for (const p of pts) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], p[k]); max[k] = Math.max(max[k], p[k]); }
const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
let radius = 0;
for (const p of pts) radius = Math.max(radius, vec.len(vec.sub(p, center)));
return { min, max, center, radius };
}
function fitCamera(camIn, points, aspect = 1.6, margin = 1.08) {
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
function pointsOutside(cam, points, w, h, tol = 2) {
const vp = viewProj(cam, w / h);
const out = [];
for (const p of points || []) {
if (!finite3(p)) continue;
const s = project(vp, p, w, h);
if (!s.visible || !(s.x >= -tol && s.x <= w + tol && s.y >= -tol && s.y <= h + tol)) out.push(p);
}
return out;
}
function project(vp, p, w, h) {
const [x, y, z, cw] = transformPoint(vp, p);
if (cw <= 1e-6) return { x: NaN, y: NaN, depth: cw, visible: false };
const nx = x / cw; const ny = y / cw; const nz = z / cw;
return { x: (nx * 0.5 + 0.5) * w, y: (0.5 - ny * 0.5) * h, depth: cw, clipZ: nz, visible: nz >= -1 && nz <= 1 };
}
function chiralityOfView(vm) {
const m = (r, c) => vm[c * 4 + r];
const det = m(0, 0) * (m(1, 1) * m(2, 2) - m(1, 2) * m(2, 1)) - m(0, 1) * (m(1, 0) * m(2, 2) - m(1, 2) * m(2, 0)) + m(0, 2) * (m(1, 0) * m(2, 1) - m(1, 1) * m(2, 0));
return { chirality: det > 0 ? 'Left' : 'Right', det };
}
function conventionProbe(cam, w = 800, h = 600) {
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
function screenRay(cam, x, y, w, h) {
const eye = cameraPosition(cam);
const { f, r, u } = cameraFrame(eye, cam.target);
const t = Math.tan(cam.vFov / 2);
const nx = (x / w) * 2 - 1; const ny = 1 - (y / h) * 2;
const dir = vec.norm(vec.add(f, vec.add(vec.scale(r, nx * t * (w / h)), vec.scale(u, ny * t))));
return { origin: eye, dir };
}
function rayDistance(p, ray) {
const v = vec.sub(p, ray.origin);
const t = Math.max(0, vec.dot(v, ray.dir));
return { dist: vec.len(vec.sub(v, vec.scale(ray.dir, t))), t };
}
function pointSegmentDistance(px, py, ax, ay, bx, by) {
const vx = bx - ax; const vy = by - ay;
const l2 = vx * vx + vy * vy;
let t = l2 === 0 ? 0 : ((px - ax) * vx + (py - ay) * vy) / l2;
t = Math.max(0, Math.min(1, t));
return Math.hypot(px - (ax + vx * t), py - (ay + vy * t));
}
function pick(cam, items, x, y, w, h, edgeTol = 6) {
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
function edgePath(a, b, index = 0, count = 1, segments = 10) {
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
function arrowTip(p0, p1, size = 0.35) {
const dir = vec.norm(vec.sub(p1, p0));
let side = vec.cross(dir, [0, 1, 0]);
if (vec.len(side) < 1e-4) side = [1, 0, 0];
side = vec.norm(side);
const base = vec.sub(p1, vec.scale(dir, size));
return [vec.add(base, vec.scale(side, size * 0.45)), p1, vec.sub(base, vec.scale(side, size * 0.45))];
}
return { AXES, vec, identity, multiply, translation, transformPoint, perspectiveMatrixLH, cameraFrame, aimMatrixLH, newCamera, cameraAhead, cameraPosition, worldToView, viewProj, orbit, pan, zoom, saneCamera, boxCorners, boundsOfPoints, fitCamera, pointsOutside, project, chiralityOfView, conventionProbe, screenRay, rayDistance, pointSegmentDistance, pick, edgePath, arrowTip };
})();
// ---- label-fit.js
const __mod_label_fit = (function () {
'use strict';
const FONT_FAMILY = 'system-ui, "Noto Sans", "Noto Sans CJK SC", "Noto Sans CJK JP", "PingFang SC", "Microsoft YaHei", sans-serif';
const WIDE_FROM = 11904; // first code point of the CJK radicals block: everything from here on is drawn one em wide
const ELLIPSIS = '\u2026';
const LABEL_FONT = (size, family = FONT_FAMILY) => `${size}px ${family}`;
const LABEL_PAD = 12; // 6px each side
const prefix = (chars, n) => chars.slice(0, n).join('');
function shrinkText(ctx, text, maxWidth, sizes = [12, 11, 10, 9], family = FONT_FAMILY) {
const str = String(text ?? '');
let size = sizes[0];
for (const s of sizes) { size = s; ctx.font = LABEL_FONT(s, family); if (ctx.measureText(str).width + LABEL_PAD <= maxWidth) return { text: str, size, width: ctx.measureText(str).width + LABEL_PAD }; }
const chars = [...str];
let lo = 0; let hi = chars.length;
while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (ctx.measureText(`${prefix(chars, mid)}${ELLIPSIS}`).width + LABEL_PAD <= maxWidth) lo = mid; else hi = mid - 1; }
const out = `${prefix(chars, lo)}${ELLIPSIS}`;
return { text: out, size, width: Math.min(maxWidth, ctx.measureText(out).width + LABEL_PAD) };
}
const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y - 10 < b.y + 10 && b.y - 10 < a.y + 10;
function placeLabel(ctx, text, cx, y0, w, h, placed, labelMax = 260, family = FONT_FAMILY) {
const fit = shrinkText(ctx, text, Math.max(40, Math.min(w - 8, labelMax)), [12, 11, 10, 9], family);
const x = Math.max(4, Math.min(w - fit.width - 4, cx - fit.width / 2));
let y = Math.max(10, Math.min(h - 10, y0));
for (let i = 0; i < 8; i++) {
const box = { x, y, w: fit.width };
if (!placed.some((q) => hit(box, q))) break;
const step = 22 * (Math.floor(i / 2) + 1) * (i % 2 ? 1 : -1);
y = Math.max(10, Math.min(h - 10, y0 + step));
}
const box = { x, y, w: fit.width, size: fit.size, text: fit.text };
placed.push(box);
return box;
}
const estimateTextWidth = (t, size) => [...String(t ?? '')].reduce((w, c) => w + (c.codePointAt(0) > WIDE_FROM ? size : size * 0.56), 0);
function truncateText(text, limitPx, size) {
const t = String(text ?? '');
if (estimateTextWidth(t, size) <= limitPx) return t;
let out = '';
for (const c of t) { if (estimateTextWidth(`${out}${c}${ELLIPSIS}`, size) > limitPx) break; out += c; }
return `${out}${ELLIPSIS}`;
}
return { FONT_FAMILY, ELLIPSIS, LABEL_FONT, shrinkText, placeLabel, estimateTextWidth, truncateText };
})();
// ---- model-access.js
const __mod_model_access = (function () {
'use strict';
const DEFAULT_LEVELS = ['REQ', 'BD', 'DD'];
const UNASSIGNED_ID = '~unassigned'; // cannot collide with an authored id (those start with an ASCII letter or digit)
const NEUTRAL_COLOR = '#5b7090';
const NEUTRAL_SHAPE = 'box';
const NEUTRAL_SCALE = 1;
const NEUTRAL_LANE = 1;
const NEUTRAL_LEVEL = 'DD';
const OPEN_VIEWS = ['logical-view', 'process-view', 'security-view'];
const VIEW_WARNING = {
UNKNOWN_KIND: 'GEMARC_ATLAS_VIEW_001',
UNKNOWN_RELATION: 'GEMARC_ATLAS_VIEW_002',
UNKNOWN_TRUST: 'GEMARC_ATLAS_VIEW_003',
UNKNOWN_VIEW: 'GEMARC_ATLAS_VIEW_004',
UNPLACED_NODE: 'GEMARC_ATLAS_VIEW_005',
FLOOR_LEVEL: 'GEMARC_ATLAS_VIEW_006',
UNKNOWN_STATE: 'GEMARC_ATLAS_VIEW_007',
BAD_INDEX: 'GEMARC_ATLAS_VIEW_008',
UNKNOWN_LEVEL: 'GEMARC_ATLAS_VIEW_009',
UNKNOWN_DOMAIN: 'GEMARC_ATLAS_VIEW_010',
};
const WARNING_CAP = 25; // distinct subjects kept per code; the rest are only counted
const asList = (v) => (Array.isArray(v) ? v : []);
const isRecord = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const own = (o, k) => (o !== null && typeof o === 'object' && Object.hasOwn(o, k) ? o[k] : undefined);
const dict = (src = null) => Object.assign(Object.create(null), src);
function makeWarnings() {
const items = []; const seen = new Set(); const perCode = {}; const suppressed = {};
return {
items, suppressed,
add(code, subject, detail = '') {
const key = `${code}|${subject}`;
if (seen.has(key)) return;
seen.add(key);
perCode[code] = (perCode[code] || 0) + 1;
if (perCode[code] > WARNING_CAP) { suppressed[code] = (suppressed[code] || 0) + 1; return; }
items.push({ code, subject: String(subject), detail });
},
count() { return items.length + Object.values(suppressed).reduce((a, b) => a + b, 0); },
};
}
const byId = (arr) => new Map(asList(arr).filter((x) => isRecord(x) && typeof x.id === 'string').map((x) => [x.id, x]));
function makeVocab(profile = null, warnings = makeWarnings()) {
const p = isRecord(profile) ? profile : {};
const levelList = asList(p.levels).filter((x) => isRecord(x) && typeof x.id === 'string');
const levels = levelList.length ? levelList.slice().sort((a, b) => (Number(a.rank) || 0) - (Number(b.rank) || 0)).map((x) => x.id) : DEFAULT_LEVELS.slice();
const trustList = asList(p.trust).filter((x) => isRecord(x) && typeof x.id === 'string');
return {
profile: p, warnings, levels,
kinds: byId(p.kinds), relations: byId(p.relations), domains: byId(p.domains), trust: byId(p.trust), trustOrder: trustList.map((x) => x.id),
views: byId(p.views), layers: byId(p.layers), nodeStates: byId(p.nodeStates), defaults: isRecord(p.defaults) ? p.defaults : {},
};
}
function kindInfo(vocab, kind) {
const k = vocab?.kinds?.get(kind);
if (!k) {
if (vocab && vocab.kinds.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_KIND, kind, 'unknown node kind: neutral shape');
return { id: kind, known: false, shape: NEUTRAL_SHAPE, scale: NEUTRAL_SCALE, level: NEUTRAL_LEVEL, domain: null, lane: NEUTRAL_LANE, record: false };
}
const num = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
return {
id: k.id, known: true, shape: typeof k.shape === 'string' ? k.shape : NEUTRAL_SHAPE, scale: num(k.scale, NEUTRAL_SCALE),
level: typeof k.level === 'string' ? k.level : NEUTRAL_LEVEL, domain: typeof k.domain === 'string' && k.domain ? k.domain : null,
lane: num(k.lane, NEUTRAL_LANE), record: k.record === true,
};
}
const shapeOf = (vocab, kind) => kindInfo(vocab, kind).shape;
const scaleOf = (vocab, kind) => kindInfo(vocab, kind).scale;
function relationInfo(vocab, relation) {
const r = vocab?.relations?.get(relation);
if (!r) {
if (vocab && vocab.relations.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_RELATION, relation, 'unknown relation: neutral colour, open views only');
return { id: relation, known: false, level: NEUTRAL_LEVEL, runtime: true, static: false, color: NEUTRAL_COLOR, trustBoundary: false, views: OPEN_VIEWS };
}
return {
id: r.id, known: true, level: typeof r.level === 'string' ? r.level : NEUTRAL_LEVEL, runtime: r.runtime !== false, static: r.static === true,
color: typeof r.color === 'string' ? r.color : NEUTRAL_COLOR, trustBoundary: r.trustBoundary === true, views: asList(r.views),
};
}
const relationColor = (vocab, relation) => relationInfo(vocab, relation).color;
const relationInView = (vocab, relation, viewId) => relationInfo(vocab, relation).views.includes(viewId);
function rankOfLevel(vocab, level) {
const i = (vocab?.levels || DEFAULT_LEVELS).indexOf(level);
return Math.max(0, i);
}
const reachesLevel = (vocab, shown, from) => rankOfLevel(vocab, shown) >= rankOfLevel(vocab, from);
const knownLevel = (vocab, v) => (vocab?.levels || DEFAULT_LEVELS).includes(v);
function indexModel(model) {
const m = isRecord(model) ? model : {};
const space = isRecord(m.space) ? m.space : {};
const nodes = new Map(asList(m.nodes).map((n) => [n.id, n]));
const edges = new Map(asList(m.edges).map((e) => [e.id, e]));
const out = new Map(); const inc = new Map();
for (const n of asList(m.nodes)) { out.set(n.id, []); inc.set(n.id, []); }
for (const e of asList(m.edges)) {
if (out.has(e.sourceId)) out.get(e.sourceId).push(e);
if (inc.has(e.targetId)) inc.get(e.targetId).push(e);
}
const rooms = new Map(asList(space.rooms).map((r) => [r.id, r]));
const floors = new Map(asList(space.floors).map((f) => [f.id, f]));
const buildings = new Map(asList(space.buildings).map((b) => [b.id, b]));
const ports = new Map(asList(m.ports).map((p) => [p.id, p]));
const evidences = new Map(asList(m.evidences).map((e) => [e.evidenceId, e]));
const machines = new Map(asList(m.machines).map((x) => [x.id, x]));
const cases = new Map(asList(m.cases).map((c) => [c.caseId, c]));
return { model: m, nodes, edges, out, inc, rooms, floors, buildings, ports, evidences, machines, cases };
}
const nodesOf = (model) => asList(model?.nodes);
const edgesOf = (model) => asList(model?.edges);
const casesOf = (model) => asList(model?.cases);
const spaceOf = (model) => {
const s = isRecord(model?.space) ? model.space : {};
return { buildings: asList(s.buildings), floors: asList(s.floors), rooms: asList(s.rooms) };
};
function incident(ix, nodeId) { return [...(ix.out.get(nodeId) || []), ...(ix.inc.get(nodeId) || [])]; }
function neighbours(ix, nodeId) {
const s = new Set();
for (const e of incident(ix, nodeId)) { s.add(e.sourceId); s.add(e.targetId); }
s.delete(nodeId);
return s;
}
function roomIdOf(ix, nodeId) { return ix.nodes.get(nodeId)?.roomId ?? null; }
function floorIdOf(ix, nodeId) { return ix.rooms.get(roomIdOf(ix, nodeId))?.floorId ?? null; }
function buildingIdOf(ix, nodeId) { return ix.floors.get(floorIdOf(ix, nodeId))?.buildingId ?? null; }
function homeOf(ix, nodeId) {
const n = ix.nodes.get(nodeId); const r = n ? ix.rooms.get(n.roomId) : null; const f = r ? ix.floors.get(r.floorId) : null; const b = f ? ix.buildings.get(f.buildingId) : null;
return b ? { roomId: r.id, floorId: f.id, buildingId: b.id } : null;
}
function membersIn(ix, type, id) {
const all = nodesOf(ix.model);
if (type === 'room') return all.filter((n) => n.roomId === id).map((n) => n.id);
if (type === 'floor') return all.filter((n) => floorIdOf(ix, n.id) === id).map((n) => n.id);
if (type === 'building') return all.filter((n) => buildingIdOf(ix, n.id) === id).map((n) => n.id);
return [];
}
function casePlayers(ix, caseId) {
const c = ix.cases.get(caseId);
const s = new Set();
if (!c) return s;
for (const e of asList(c.events)) { s.add(e.from); s.add(e.to); }
return s;
}
function fewestHopsPath(ix, fromId, toId) {
if (!ix.nodes.has(fromId) || !ix.nodes.has(toId)) return null;
const prev = new Map([[fromId, null]]);
const q = [fromId];
for (let h = 0; h < q.length; h++) {
const cur = q[h];
if (cur === toId) break;
for (const e of incident(ix, cur)) {
const nx = e.sourceId === cur ? e.targetId : e.sourceId;
if (!prev.has(nx)) { prev.set(nx, { id: cur, edge: e.id }); q.push(nx); }
}
}
if (!prev.has(toId)) return null;
const nodes = []; const edges = [];
for (let cur = toId; cur;) { nodes.unshift(cur); const p = prev.get(cur); if (p) { edges.unshift(p.edge); cur = p.id; } else cur = null; }
return { nodes, edges };
}
function levelOfNode(node, ix, vocab) {
if (!node) return NEUTRAL_LEVEL;
if (typeof node.level === 'string') {
if (knownLevel(vocab, node.level)) return node.level;
vocab?.warnings?.add(VIEW_WARNING.UNKNOWN_LEVEL, node.level, 'unknown node level: taken from the room layer or the kind');
}
const room = ix?.rooms?.get(node.roomId);
const layer = room && room.layer ? vocab?.layers?.get(room.layer) : null;
if (layer && asList(layer.levelKinds).includes(node.kind) && typeof layer.level === 'string') return layer.level;
return kindInfo(vocab, node.kind).level;
}
const levelOfRelation = (vocab, relation) => relationInfo(vocab, relation).level;
function shownAtLevel(model, level, vocab, ix = indexModel(model)) {
const nodeIds = new Set(nodesOf(model).filter((n) => reachesLevel(vocab, level, levelOfNode(n, ix, vocab))).map((n) => n.id));
const edgeIds = new Set(edgesOf(model).filter((e) => reachesLevel(vocab, level, levelOfRelation(vocab, e.relation)) && nodeIds.has(e.sourceId) && nodeIds.has(e.targetId)).map((e) => e.id));
return { nodeIds, edgeIds };
}
const nodeShownAt = (node, level, ix, vocab) => reachesLevel(vocab, level, levelOfNode(node, ix, vocab));
function keepFocus(vis, rel) {
if (!rel?.active) return vis;
return { nodeIds: new Set([...vis.nodeIds, ...rel.chosenNodes]), edgeIds: new Set([...vis.edgeIds, ...rel.chosenEdges]) };
}
function trustOf(node, ix) {
const own = node?.props?.trust;
const viaRoom = node ? ix.buildings.get(ix.floors.get(ix.rooms.get(node.roomId)?.floorId)?.buildingId)?.trust : undefined;
if (typeof own === 'string' && own) return own;
return typeof viaRoom === 'string' && viaRoom ? viaRoom : null;
}
function nodeDomain(node, ix, vocab) {
const fromKind = kindInfo(vocab, node?.kind).domain;
if (fromKind) return fromKind;
const room = ix?.rooms?.get(node?.roomId);
const layer = room && room.layer ? vocab?.layers?.get(room.layer) : null;
if (layer && typeof layer.domain === 'string') return layer.domain;
const t = trustOf(node, ix);
const fallback = typeof vocab?.defaults?.trust === 'string' ? vocab.defaults.trust : null;
if (t !== null) {
const z = vocab?.trust?.get(t);
if (z) return typeof z.domain === 'string' ? z.domain : t;
if (vocab && vocab.trust.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_TRUST, t, 'unknown trust value: default domain');
}
const d = fallback ? vocab.trust.get(fallback) : null;
return d && typeof d.domain === 'string' ? d.domain : fallback;
}
function domainColor(vocab, domain) {
const d = domain === null || domain === undefined ? null : vocab?.domains?.get(domain);
if (d && typeof d.color === 'string') return d.color;
if (domain !== null && domain !== undefined && vocab && vocab.domains.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_DOMAIN, domain, 'unknown domain: neutral colour');
return NEUTRAL_COLOR;
}
const nodeColor = (node, ix, vocab) => domainColor(vocab, nodeDomain(node, ix, vocab));
function relevance(ix, selection) {
const model = ix.model;
const pickedNodes = new Set(); const pickedEdges = new Set();
if (selection) {
const { type } = selection;
if (type === 'node') pickedNodes.add(selection.id);
else if (type === 'edge') { const e = ix.edges.get(selection.id); if (e) pickedEdges.add(e.id); }
else if (type === 'room' || type === 'floor' || type === 'building') membersIn(ix, type, selection.id).forEach((i) => pickedNodes.add(i));
else if (type === 'path') asList(selection.ids).forEach((i) => pickedNodes.add(i));
else if (type === 'case') casePlayers(ix, selection.id).forEach((i) => pickedNodes.add(i));
}
const direct = new Set(); const nearEdges = new Set();
for (const id of pickedNodes) for (const e of incident(ix, id)) { nearEdges.add(e.id); direct.add(e.sourceId); direct.add(e.targetId); }
for (const eid of pickedEdges) {
const e = ix.edges.get(eid);
if (!e) continue;
direct.add(e.sourceId); direct.add(e.targetId);
for (const o of edgesOf(model)) {
if (o.id !== eid && ((o.sourceId === e.sourceId && o.targetId === e.targetId) || (o.sourceId === e.targetId && o.targetId === e.sourceId))) nearEdges.add(o.id);
}
}
for (const id of pickedNodes) direct.delete(id);
const anchor = new Set([...pickedNodes, ...direct]);
const context = new Set();
const pickedRooms = new Set([...pickedNodes].map((s) => roomIdOf(ix, s)));
const reqNodes = new Set();
for (const r of asList(model.requirements)) {
const ids = asList(r.nodeIds);
if (ids.some((id) => pickedNodes.has(id))) for (const id of ids) reqNodes.add(id);
}
for (const n of nodesOf(model)) {
if (anchor.has(n.id)) continue;
if (pickedRooms.has(n.roomId) || reqNodes.has(n.id)) context.add(n.id);
}
const everything = new Set([...anchor, ...context]);
const nodeTier = (id) => (pickedNodes.has(id) ? 'Selected' : direct.has(id) ? 'Direct' : context.has(id) ? 'Context' : 'Unrelated');
const edgeTier = (e) => {
if (pickedEdges.has(e.id)) return 'Selected';
if (nearEdges.has(e.id)) return 'Direct';
if (everything.has(e.sourceId) && everything.has(e.targetId) && (pickedNodes.size || pickedEdges.size)) return 'Context';
return 'Unrelated';
};
const empty = pickedNodes.size === 0 && pickedEdges.size === 0;
return {
active: !empty, chosenNodes: pickedNodes, chosenEdges: pickedEdges, nearNodes: direct, nearEdges, backdropNodes: context,
nodeTier: (id) => (empty ? 'Selected' : nodeTier(id)), edgeTier: (e) => (empty ? 'Selected' : edgeTier(e)),
};
}
const RETREAT_TARGET = {
Selected: { alpha: 1, push: 0 }, Direct: { alpha: 1, push: 0 }, Context: { alpha: 0.2, push: 0 }, Unrelated: { alpha: 0.04, push: 1 },
Reached: { alpha: 0.65, push: 0 }, Stage: { alpha: 0.35, push: 0 }, Outside: { alpha: 0, push: 0 },
};
const DISPERSE_SEC = 3.2; // one full disperse (or return)
const FADE_SEC = 2.4; // full dim 1 -> 0
const disperseEase = (u) => (1 - Math.cos(Math.PI * Math.min(1, Math.max(0, u)))) / 2;
function stepEase(current = {}, target = RETREAT_TARGET.Context, dt = 0, delay = 0) {
let { alpha = 1, push = 0, wait = 0, goal } = current ?? {};
target = target ?? RETREAT_TARGET.Context;
const g = `${target.alpha}|${target.push}`;
if (goal !== g) { wait = target.push > push ? delay : 0; goal = g; } // the delay only delays a disperse; returning starts at once
let t = Math.max(0, Number(dt) || 0);
if (wait > 0) { const w = Math.min(wait, t); wait -= w; t -= w; }
const toward = (v, to, rate) => (v < to ? Math.min(to, v + rate * t) : Math.max(to, v - rate * t));
push = toward(push, target.push, 1 / DISPERSE_SEC);
alpha = toward(alpha, target.alpha, 1 / FADE_SEC);
return { alpha, push, wait, goal, done: wait <= 0 && alpha === target.alpha && push === target.push };
}
return { DEFAULT_LEVELS, UNASSIGNED_ID, NEUTRAL_COLOR, NEUTRAL_SHAPE, NEUTRAL_SCALE, NEUTRAL_LANE, NEUTRAL_LEVEL, OPEN_VIEWS, VIEW_WARNING, asList, isRecord, own, dict, makeWarnings, makeVocab, kindInfo, shapeOf, scaleOf, relationInfo, relationColor, relationInView, rankOfLevel, reachesLevel, knownLevel, indexModel, nodesOf, edgesOf, casesOf, spaceOf, incident, neighbours, roomIdOf, floorIdOf, buildingIdOf, homeOf, membersIn, casePlayers, fewestHopsPath, levelOfNode, levelOfRelation, shownAtLevel, nodeShownAt, keepFocus, trustOf, nodeDomain, domainColor, nodeColor, relevance, RETREAT_TARGET, DISPERSE_SEC, FADE_SEC, disperseEase, stepEase };
})();
// ---- layout.js
const __mod_layout = (function () {
'use strict';
const { vec, boundsOfPoints, boxCorners } = __mod_coords;
const { indexModel, nodesOf, edgesOf, casesOf, spaceOf, homeOf, makeVocab, kindInfo, relationInfo, trustOf, nodeDomain, domainColor, own, dict, UNASSIGNED_ID, VIEW_WARNING } = __mod_model_access;
const FLOOR_H = 3.4;
const SPACING = 1.7;
const ROOM_GAP = 1.6;
const BUILDING_GAP = 5;
const ZONE_GAP = 8;
const UNKNOWN_ZONE = '~unknown';
const MAX_FLOOR_LEVEL = 1e6; // a floor level beyond this magnitude is treated as invalid (a Float32 scene would overflow)
const KNOWN_SHAPES = ['hex', 'tri', 'box', 'octa', 'diamond', 'round', 'crown'];
const gemShapes = () => KNOWN_SHAPES.slice();
const gemShapeOf = (vocab, kind) => kindInfo(vocab, kind).shape;
function floorLevels(model, vocab = makeVocab(null)) {
const { floors } = spaceOf(model);
const byBuilding = new Map();
for (const f of floors) { if (!byBuilding.has(f.buildingId)) byBuilding.set(f.buildingId, []); byBuilding.get(f.buildingId).push(f); }
const out = new Map();
for (const fs of byBuilding.values()) {
const used = new Set(); const later = [];
for (const f of fs) {
if (typeof f.level === 'number' && Number.isFinite(f.level) && Math.abs(f.level) <= MAX_FLOOR_LEVEL && !used.has(f.level)) { used.add(f.level); out.set(f.id, f.level); } else later.push(f);
}
for (const f of later) {
let top = -Infinity;
for (const u of used) if (u > top) top = u;
const lv = (used.size ? top : -1) + 1;
used.add(lv); out.set(f.id, lv);
vocab.warnings.add(VIEW_WARNING.FLOOR_LEVEL, f.id, 'floor level repeated, not a number or out of range: floor moved above the building');
}
}
return out;
}
function localGrid(count) {
const cols = Math.max(1, Math.ceil(Math.sqrt(count)));
const rows = Math.ceil(count / cols);
const pts = [];
for (let i = 0; i < count; i++) {
const c = i % cols; const r = Math.floor(i / cols);
pts.push([(c - (cols - 1) / 2) * SPACING, 0, (r - (rows - 1) / 2) * SPACING]);
}
return { pts, w: cols * SPACING, d: rows * SPACING };
}
function groupByRoom(ix, shownIds, vocab) {
const byRoom = new Map(); const unassigned = [];
for (const n of nodesOf(ix.model)) {
if (!shownIds.has(n.id)) continue;
const home = homeOf(ix, n.id);
if (!home) { unassigned.push(n); vocab.warnings.add(VIEW_WARNING.UNPLACED_NODE, n.id, 'node does not resolve to a room, floor and building: unassigned region'); continue; }
if (!byRoom.has(home.roomId)) byRoom.set(home.roomId, []);
byRoom.get(home.roomId).push(n);
}
return { byRoom, unassigned };
}
const newBoxes = () => ({ buildings: dict(), floors: dict(), rooms: dict() });
const emptyBox = (y0, y1) => ({ min: [Infinity, y0, Infinity], max: [-Infinity, y1, -Infinity] });
const grow = (a, b) => { for (let k = 0; k < 3; k++) { a.min[k] = Math.min(a.min[k], b.min[k]); a.max[k] = Math.max(a.max[k], b.max[k]); } };
function placeBuildings(ix, shownIds, vocab = makeVocab(null)) {
const space = spaceOf(ix.model);
const levels = floorLevels(ix.model, vocab);
const { byRoom, unassigned } = groupByRoom(ix, shownIds, vocab);
const roomGrids = new Map(); const roomsByFloor = new Map();
for (const r of space.rooms) {
const ms = byRoom.get(r.id);
if (!ms || roomGrids.has(r.id)) continue;
roomGrids.set(r.id, { ms, ...localGrid(ms.length) });
if (!roomsByFloor.has(r.floorId)) roomsByFloor.set(r.floorId, []);
roomsByFloor.get(r.floorId).push(r.id);
}
const floorFit = new Map();
for (const [fid, roomIds] of roomsByFloor) {
const widths = roomIds.map((rid) => roomGrids.get(rid).w);
floorFit.set(fid, { roomIds, widths, total: widths.reduce((a, b) => a + b, 0) + ROOM_GAP * (roomIds.length - 1), depth: Math.max(...roomIds.map((rid) => roomGrids.get(rid).d)) });
}
const widthOfBuilding = new Map();
for (const f of space.floors) {
const fi = floorFit.get(f.id);
if (fi) widthOfBuilding.set(f.buildingId, Math.max(widthOfBuilding.get(f.buildingId) || 0, fi.total));
}
const order = [...new Set(space.buildings.map((b) => b.id))].filter((b) => widthOfBuilding.has(b));
const slots = order.map((id) => ({ id, width: widthOfBuilding.get(id) }));
const ug = unassigned.length ? localGrid(unassigned.length) : null;
if (ug) slots.push({ id: UNASSIGNED_ID, width: ug.w });
const spanW = slots.reduce((a, s) => a + s.width, 0) + BUILDING_GAP * Math.max(0, slots.length - 1);
const pos = dict(); const boxes = newBoxes();
let cursor = -spanW / 2;
for (const slot of slots) {
const bw = slot.width; const bx = cursor + bw / 2;
cursor += bw + BUILDING_GAP;
if (slot.id === UNASSIGNED_ID) {
const fy = 0; const box = { min: [bx - ug.w / 2, fy - 0.9, -ug.d / 2], max: [bx + ug.w / 2, fy + 1.1, ug.d / 2] };
unassigned.forEach((n, j) => { pos[n.id] = [bx + ug.pts[j][0], fy, ug.pts[j][2]]; });
boxes.rooms[UNASSIGNED_ID] = box;
boxes.floors[UNASSIGNED_ID] = { min: [box.min[0], fy - 1, box.min[2]], max: [box.max[0], fy + 1.2, box.max[2]] };
boxes.buildings[UNASSIGNED_ID] = { min: [...boxes.floors[UNASSIGNED_ID].min], max: [...boxes.floors[UNASSIGNED_ID].max] };
continue;
}
const buildingBox = emptyBox(Infinity, -Infinity);
for (const f of space.floors.filter((x) => x.buildingId === slot.id)) {
const fi = floorFit.get(f.id);
if (!fi) continue;
const fy = levels.get(f.id) * FLOOR_H;
let rx = bx - fi.total / 2;
const floorBox = emptyBox(fy - 1, fy + 1.2);
fi.roomIds.forEach((rid, i) => {
const rl = roomGrids.get(rid);
const cx = rx + fi.widths[i] / 2;
rx += fi.widths[i] + ROOM_GAP;
const roomBox = { min: [cx - rl.w / 2, fy - 0.9, -rl.d / 2], max: [cx + rl.w / 2, fy + 1.1, rl.d / 2] };
rl.ms.forEach((n, j) => { pos[n.id] = [cx + rl.pts[j][0], fy, rl.pts[j][2]]; });
boxes.rooms[rid] = roomBox;
grow(floorBox, roomBox);
});
boxes.floors[f.id] = floorBox;
grow(buildingBox, floorBox);
}
boxes.buildings[slot.id] = buildingBox;
}
return { pos, boxes, unassigned: unassigned.map((n) => n.id) };
}
const VIEW_IDS = ['logical', 'development', 'dependency', 'process', 'data', 'security'];
const PROFILE_VIEW = { logical: 'logical-view', development: 'development-view', dependency: 'dependency-diagram', process: 'process-view', data: 'data-view', security: 'security-view' };
function resolveView(viewId, vocab = makeVocab(null)) {
if (VIEW_IDS.includes(viewId)) return { key: viewId, profileId: PROFILE_VIEW[viewId] };
const short = VIEW_IDS.find((k) => PROFILE_VIEW[k] === viewId);
if (short) return { key: short, profileId: viewId };
if (vocab.views.has(viewId)) return { key: 'filtered', profileId: viewId };
vocab.warnings.add(VIEW_WARNING.UNKNOWN_VIEW, viewId, 'unknown view: logical layout');
return { key: 'logical', profileId: PROFILE_VIEW.logical };
}
const viewKinds = (vocab, profileId) => {
const k = vocab.views.get(profileId)?.kinds;
return Array.isArray(k) ? new Set(k) : null;
};
const edgeInView = (vocab, relation, profileId) => (!vocab.relations.size ? true : relationInfo(vocab, relation).views.includes(profileId));
function stackByDepth(edges, nodeIds) {
const out = new Map([...nodeIds].map((id) => [id, []]));
for (const e of edges) out.get(e.sourceId).push(e.targetId);
const layer = new Map(); const state = new Map();
for (const root of [...nodeIds].sort()) {
if (state.get(root) === 2) continue;
state.set(root, 1);
const stack = [{ id: root, i: 0, l: 0 }];
while (stack.length) {
const fr = stack[stack.length - 1]; const outs = out.get(fr.id);
if (fr.i < outs.length) {
const t = outs[fr.i++]; const st = state.get(t);
if (st === 2) fr.l = Math.max(fr.l, layer.get(t) + 1);
else if (st === 1) fr.l = Math.max(fr.l, 1);
else { state.set(t, 1); stack.push({ id: t, i: 0, l: 0 }); }
} else {
state.set(fr.id, 2); layer.set(fr.id, fr.l); stack.pop();
if (stack.length) { const parent = stack[stack.length - 1]; parent.l = Math.max(parent.l, fr.l + 1); }
}
}
}
return layer;
}
function placeGrid(ids, pos, y, zOrigin = 0, cols = null, xBase = 0) {
const n = ids.length;
const c = cols || Math.max(1, Math.ceil(Math.sqrt(n * 1.6)));
ids.forEach((id, i) => { pos[id] = [xBase + ((i % c) - (c - 1) / 2) * SPACING, y, zOrigin + (Math.floor(i / c) - (Math.ceil(n / c) - 1) / 2) * SPACING]; });
return c;
}
function arrangeView(model, viewId = 'logical', opts = {}) {
const vocab = opts.vocab || makeVocab(null);
const ix = opts.ix || indexModel(model);
const { key, profileId } = resolveView(viewId, vocab);
const nodes = nodesOf(model);
const allowedNode = (id) => (opts.vis ? opts.vis.nodeIds.has(id) : true);
const allowedEdge = (id) => (opts.vis ? opts.vis.edgeIds.has(id) : true);
const all = new Set(nodes.filter((n) => allowedNode(n.id)).map((n) => n.id));
let pos = dict(); let visible = all; let boxes = newBoxes(); let unassigned = [];
let keepEdge = (e) => edgeInView(vocab, e.relation, profileId);
const useLogical = (ids) => { ({ pos, boxes, unassigned } = placeBuildings(ix, ids, vocab)); };
if (key === 'logical') {
useLogical(visible);
} else if (key === 'development' || key === 'filtered') {
const kinds = viewKinds(vocab, profileId);
visible = new Set(nodes.filter((n) => allowedNode(n.id) && (!kinds || kinds.has(n.kind))).map((n) => n.id));
useLogical(visible);
} else if (key === 'dependency') {
const kinds = viewKinds(vocab, profileId);
const ok = (id) => ix.nodes.has(id) && allowedNode(id) && (!kinds || kinds.has(ix.nodes.get(id).kind));
const depEdges = edgesOf(model).filter((e) => edgeInView(vocab, e.relation, profileId) && ok(e.sourceId) && ok(e.targetId));
visible = new Set();
for (const e of depEdges) { visible.add(e.sourceId); visible.add(e.targetId); }
const layer = stackByDepth(depEdges.filter((e) => e.sourceId !== e.targetId), visible);
const byLayer = new Map();
for (const id of [...visible].sort()) { const l = layer.get(id); if (!byLayer.has(l)) byLayer.set(l, []); byLayer.get(l).push(id); }
for (const [l, ids] of byLayer) placeGrid(ids, pos, l * FLOOR_H, 0);
} else if (key === 'process') {
visible = new Set(); const caseOfNode = new Map(); const cases = casesOf(model);
cases.forEach((c, ci) => { for (const e of Array.isArray(c.events) ? c.events : []) for (const id of [e.from, e.to]) if (ix.nodes.has(id) && allowedNode(id) && !caseOfNode.has(id)) { caseOfNode.set(id, ci); visible.add(id); } });
const lanes = new Map();
for (const [id, ci] of caseOfNode) { if (!lanes.has(ci)) lanes.set(ci, []); lanes.get(ci).push(id); }
for (const [ci, ids] of lanes) {
ids.forEach((id, j) => { pos[id] = [(j - (ids.length - 1) / 2) * SPACING, kindInfo(vocab, ix.nodes.get(id).kind).lane * 1.6, (ci - (cases.length - 1) / 2) * 2.4]; });
}
const base = keepEdge; keepEdge = (e) => visible.has(e.sourceId) && visible.has(e.targetId) && base(e);
} else if (key === 'data') {
({ pos, visible } = layoutData(ix, vocab, profileId, allowedNode));
} else {
({ pos, visible, boxes } = layoutSecurity(ix, vocab, allowedNode));
keepEdge = (e) => edgeInView(vocab, e.relation, profileId);
}
const shownEdgeIds = new Set(edgesOf(model).filter((e) => visible.has(e.sourceId) && visible.has(e.targetId) && keepEdge(e) && allowedEdge(e.id)).map((e) => e.id));
return { viewId, pos, shownNodeIds: visible, shownEdgeIds, boxes, unassigned };
}
function layoutData(ix, vocab, profileId, allowedNode) {
const kinds = viewKinds(vocab, profileId);
const nodes = nodesOf(ix.model).filter((n) => allowedNode(n.id));
const core = nodes.filter((n) => !kinds || kinds.has(n.kind));
const coreIds = new Set(core.map((n) => n.id));
const owners = new Set();
for (const e of edgesOf(ix.model)) {
if (!edgeInView(vocab, e.relation, profileId) || !ix.nodes.has(e.sourceId) || !ix.nodes.has(e.targetId)) continue;
if (!allowedNode(e.sourceId) || !allowedNode(e.targetId)) continue;
if (coreIds.has(e.targetId) && !coreIds.has(e.sourceId)) owners.add(e.sourceId);
if (coreIds.has(e.sourceId) && !coreIds.has(e.targetId)) owners.add(e.targetId);
}
const kindOrder = kinds ? [...kinds] : [];
for (const n of core) if (!kindOrder.includes(n.kind)) kindOrder.push(n.kind);
const bands = kindOrder.map((k) => core.filter((n) => n.kind === k).map((n) => n.id)).filter((ids) => ids.length);
const cols = bands.map((ids) => Math.max(1, Math.ceil(Math.sqrt(ids.length * 1.6))));
const widths = cols.map((c) => c * SPACING);
const total = widths.reduce((a, b) => a + b, 0) + ZONE_GAP * Math.max(0, bands.length - 1);
const pos = dict(); let cursor = -total / 2;
bands.forEach((ids, i) => { placeGrid(ids, pos, i === 0 ? 0 : FLOOR_H, 0, cols[i], cursor + widths[i] / 2); cursor += widths[i] + ZONE_GAP; });
const ownerIds = nodes.filter((n) => owners.has(n.id)).map((n) => n.id);
placeGrid(ownerIds, pos, 2 * FLOOR_H, 0, 7, 0);
return { pos, visible: new Set([...coreIds, ...ownerIds]) };
}
function layoutSecurity(ix, vocab, allowedNode) {
const nodes = nodesOf(ix.model).filter((n) => allowedNode(n.id));
const fallback = typeof vocab.defaults.trust === 'string' ? vocab.defaults.trust : UNKNOWN_ZONE;
const zoneOf = (n) => trustOf(n, ix) ?? fallback;
const order = vocab.trustOrder.slice();
for (const n of nodes) { const z = zoneOf(n); if (!order.includes(z)) { order.push(z); if (vocab.trust.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_TRUST, z, 'trust value not in the profile: own security zone'); } }
const idsByZone = new Map(order.map((z) => [z, []]));
for (const n of nodes) idsByZone.get(zoneOf(n)).push(n.id);
const zones = order.filter((z) => idsByZone.get(z).length).map((z) => ({ z, ids: idsByZone.get(z), g: localGrid(idsByZone.get(z).length) }));
const total = zones.reduce((a, s) => a + s.g.w + 2, 0) + ZONE_GAP * Math.max(0, zones.length - 1);
const pos = dict(); const boxes = newBoxes();
let cursor = -total / 2;
for (const { z, ids, g } of zones) {
const cx = cursor + (g.w + 2) / 2; cursor += g.w + 2 + ZONE_GAP;
ids.forEach((id, i) => { pos[id] = [cx + g.pts[i][0], 0, g.pts[i][2]]; });
boxes.buildings[`~trust.${z}`] = { min: [cx - g.w / 2 - 1, -1, -g.d / 2 - 1], max: [cx + g.w / 2 + 1, 1, g.d / 2 + 1] };
}
return { pos, visible: new Set(nodes.map((n) => n.id)), boxes };
}
function blendPositions(from, to, t) {
const out = dict();
for (const id of Object.keys(to)) { const a = own(from, id); out[id] = a ? vec.lerp(a, to[id], t) : to[id]; }
return out;
}
function layoutPoints(layout) {
const pts = Object.values(layout.pos || {}).slice();
for (const group of Object.values(layout.boxes || {})) for (const b of Object.values(group)) pts.push(...boxCorners(b));
return pts;
}
const layoutBounds = (layout) => boundsOfPoints(layoutPoints(layout));
function extrudePolygon(n, r, h, rot = 0) {
const top = []; const bot = [];
for (let i = 0; i < n; i++) { const a = rot + (i / n) * Math.PI * 2; top.push([Math.cos(a) * r, h / 2, Math.sin(a) * r]); bot.push([Math.cos(a) * r, -h / 2, Math.sin(a) * r]); }
const faces = [];
for (let i = 1; i < n - 1; i++) { faces.push([top[0], top[i], top[i + 1]]); faces.push([bot[0], bot[i + 1], bot[i]]); }
for (let i = 0; i < n; i++) { const j = (i + 1) % n; faces.push([top[i], bot[i], bot[j]]); faces.push([top[i], bot[j], top[j]]); }
return faces;
}
function doubleCone(n, r, depthAbove, depthBelow) {
const faces = [];
for (let i = 0; i < n; i++) {
const a0 = (i / n) * Math.PI * 2; const a1 = ((i + 1) / n) * Math.PI * 2;
const p0 = [Math.cos(a0) * r, 0, Math.sin(a0) * r]; const p1 = [Math.cos(a1) * r, 0, Math.sin(a1) * r];
faces.push([[0, depthAbove, 0], p0, p1]); faces.push([[0, -depthBelow, 0], p1, p0]);
}
return faces;
}
function box(w, h, d) {
const x = w / 2; const y = h / 2; const z = d / 2;
const v = [[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z], [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]];
const f = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [3, 7, 6, 2], [0, 4, 7, 3], [1, 2, 6, 5]];
const faces = [];
for (const q of f) { faces.push([v[q[0]], v[q[1]], v[q[2]]]); faces.push([v[q[0]], v[q[2]], v[q[3]]]); }
return faces;
}
function crown(n, r, h) {
const ring = (y) => Array.from({ length: n }, (_, i) => { const a = (i / n) * Math.PI * 2; return [Math.cos(a) * r, y, Math.sin(a) * r]; });
const bot = ring(-h / 2); const top = ring(0.0); const tip = [0, h / 2, 0];
const faces = [];
for (let i = 1; i < n - 1; i++) faces.push([bot[0], bot[i + 1], bot[i]]);
for (let i = 0; i < n; i++) { const j = (i + 1) % n; faces.push([top[i], bot[i], bot[j]]); faces.push([top[i], bot[j], top[j]]); faces.push([tip, top[i], top[j]]); }
return faces;
}
function orient(faces) {
return faces.map(([a, b, c]) => {
const n = vec.cross(vec.sub(b, a), vec.sub(c, a));
const m = vec.scale(vec.add(vec.add(a, b), c), 1 / 3);
return vec.dot(n, m) < 0 ? [a, c, b] : [a, b, c];
});
}
function gemFaces(shape) {
switch (shape) {
case 'hex': return orient(extrudePolygon(6, 0.5, 0.62, Math.PI / 6));
case 'tri': return orient(extrudePolygon(3, 0.58, 0.6, Math.PI / 2));
case 'box': return orient(box(0.95, 0.55, 0.7));
case 'octa': return orient(doubleCone(4, 0.55, 0.62, 0.62));
case 'diamond': return orient(doubleCone(6, 0.6, 0.3, 0.3));
case 'round': return orient(doubleCone(8, 0.5, 0.34, 0.5));
case 'crown': return orient(crown(6, 0.55, 0.9));
default: return orient(box(0.6, 0.6, 0.6));
}
}
function faceNormal([a, b, c]) { return vec.norm(vec.cross(vec.sub(b, a), vec.sub(c, a))); }
const CARD = { w: 150, h: 44, colGap: 14, rowGap: 14, cols: 3 };
const MAX_ROW = 620; // rooms wrap onto a new line inside a floor beyond this width
const CARD_PAD = 16; const HEAD = 24; const ROOM_GAP_2D = 18; const FLOOR_GAP_2D = 22; const BUILDING_GAP_2D = 40;
function cardLayout(model, vis = null, opts = {}) {
const vocab = opts.vocab || makeVocab(null);
const ix = opts.ix || indexModel(model);
const space = spaceOf(model);
const levels = floorLevels(model, vocab);
const nodes = []; const rooms = []; const floors = []; const buildings = [];
const include = new Set(nodesOf(model).filter((n) => !vis || vis.nodeIds.has(n.id)).map((n) => n.id));
const { byRoom, unassigned } = groupByRoom(ix, include, vocab);
const bs = [...new Map(space.buildings.map((b) => [b.id, b])).values()];
const slots = bs.map((b) => ({ b, fs: space.floors.filter((f) => f.buildingId === b.id).sort((a, c) => levels.get(c.id) - levels.get(a.id)).map((f) => ({ f, rooms: space.rooms.filter((r) => r.floorId === f.id && byRoom.has(r.id)).map((r) => ({ r, ms: byRoom.get(r.id) })) })) }));
if (unassigned.length) {
const pseudo = { id: UNASSIGNED_ID, name: '', trust: null, unassigned: true };
slots.push({ b: pseudo, fs: [{ f: { id: UNASSIGNED_ID, name: '' }, rooms: [{ r: { id: UNASSIGNED_ID, name: '' }, ms: unassigned }] }] });
}
let x0 = 0; let tallest = 0;
for (const { b, fs } of slots) {
const measured = fs.map(({ f, rooms: rs0 }) => {
const rs = rs0.map(({ r, ms }) => {
const cols = Math.min(CARD.cols, ms.length); const rows = Math.ceil(ms.length / cols);
return { r, ms, cols, w: CARD_PAD * 2 + cols * CARD.w + (cols - 1) * CARD.colGap, h: HEAD + CARD_PAD * 2 + rows * CARD.h + (rows - 1) * CARD.rowGap };
});
const lines = [[]]; let lw = 0;
for (const m of rs) {
if (lines.at(-1).length && lw + ROOM_GAP_2D + m.w > MAX_ROW) { lines.push([]); lw = 0; }
lines.at(-1).push(m); lw += (lines.at(-1).length > 1 ? ROOM_GAP_2D : 0) + m.w;
}
const rowWidths = lines.map((l) => l.reduce((a, m) => a + m.w, 0) + (l.length - 1) * ROOM_GAP_2D);
const rowHeights = lines.map((l) => Math.max(...l.map((m) => m.h)));
return { f, rs, lines, w: Math.max(...rowWidths), h: rowHeights.reduce((a, v) => a + v, 0) + (lines.length - 1) * ROOM_GAP_2D, rowHeights };
}).filter((m) => m.rs.length);
if (!measured.length) continue; // a building with nothing to show at this level takes no space
const bw = Math.max(0, ...measured.map((m) => m.w)) + CARD_PAD * 2;
let y = HEAD + CARD_PAD;
for (const m of measured) {
floors.push({ id: m.f.id, buildingId: b.id, name: m.f.name, x: x0 + CARD_PAD / 2, y: y - 6, w: bw - CARD_PAD, h: m.h + 12 });
let ly = y;
m.lines.forEach((line, li) => {
let x = x0 + CARD_PAD;
for (const rm of line) {
rooms.push({ id: rm.r.id, floorId: m.f.id, buildingId: b.id, name: rm.r.name, x, y: ly, w: rm.w, h: rm.h });
rm.ms.forEach((n, i) => {
const c = i % rm.cols; const rw = Math.floor(i / rm.cols);
const domain = nodeDomain(n, ix, vocab);
nodes.push({ id: n.id, roomId: rm.r.id, kind: n.kind, name: n.name, shape: kindInfo(vocab, n.kind).shape, domain, color: domainColor(vocab, domain),
x: x + CARD_PAD + c * (CARD.w + CARD.colGap), y: ly + HEAD + CARD_PAD + rw * (CARD.h + CARD.rowGap), w: CARD.w, h: CARD.h });
});
x += rm.w + ROOM_GAP_2D;
}
ly += m.rowHeights[li] + ROOM_GAP_2D;
});
y += m.h + FLOOR_GAP_2D;
}
const bh = y - FLOOR_GAP_2D + CARD_PAD;
buildings.push({ id: b.id, name: b.name, trust: b.trust, x: x0, y: 0, w: bw, h: bh, ...(b.unassigned ? { unassigned: true } : {}) });
tallest = Math.max(tallest, bh); x0 += bw + BUILDING_GAP_2D;
}
const at = new Map(nodes.map((n) => [n.id, n]));
const edges = []; const seen = new Map();
for (const e of edgesOf(model)) {
if (vis && !vis.edgeIds.has(e.id)) continue;
const a = at.get(e.sourceId); const b = at.get(e.targetId);
if (!a || !b) continue;
const k = [e.sourceId, e.targetId].sort().join('|'); const nth = seen.get(k) ?? 0; seen.set(k, nth + 1);
const ax = a.x + a.w / 2; const ay = a.y + a.h / 2; const bx = b.x + b.w / 2; const by = b.y + b.h / 2;
let d;
if (a === b) d = `M${a.x + a.w} ${ay} C${a.x + a.w + 40} ${ay - 30},${a.x + a.w + 40} ${ay + 30},${a.x + a.w} ${ay + 8}`;
else {
const mx = (ax + bx) / 2; const my = (ay + by) / 2; const dx = bx - ax; const dy = by - ay; const len = Math.hypot(dx, dy) || 1;
const bow = (nth % 2 ? -1 : 1) * Math.ceil(nth / 2) * 22 + Math.min(40, len * 0.08);
d = `M${ax} ${ay} Q${mx - (dy / len) * bow} ${my + (dx / len) * bow} ${bx} ${by}`;
}
const info = relationInfo(vocab, e.relation);
edges.push({ id: e.id, sourceId: e.sourceId, targetId: e.targetId, relation: e.relation, d, color: info.color, dashed: info.trustBoundary, static: info.static });
}
return { nodes, rooms, floors, buildings, edges, unassigned: unassigned.map((n) => n.id), bounds: { x: -20, y: -20, w: Math.max(0, x0 - BUILDING_GAP_2D) + 40, h: tallest + 40 } };
}
const DISPERSE_2D_OUT = 70;
const DISPERSE_2D_FAN = 140;
const DISPERSE_2D_RIPPLE = 1.2;
const DISPERSE_2D_MIN = 300; // every card travels at least this far, even one next to its building's edge
function cardFocus(L, rel) {
const offsets = new Map(); const delays = new Map(); const away = { rooms: new Set(), floors: new Set(), buildings: new Set() };
if (!rel?.active) return { offsets, delays, away };
const mid = (n) => [n.x + n.w / 2, n.y + n.h / 2];
const cls = (n) => rel.nodeTier(n.id);
let core = L.nodes.filter((n) => cls(n) === 'Selected'); if (!core.length) core = L.nodes.filter((n) => cls(n) === 'Direct');
const c = core.length ? core.map(mid).reduce((a, p) => [a[0] + p[0] / core.length, a[1] + p[1] / core.length], [0, 0]) : null;
const kept = new Set(L.nodes.filter((n) => cls(n) !== 'Unrelated').map((n) => n.roomId));
const roomBuilding = new Map(L.rooms.map((r) => [r.id, r.buildingId])); const buildingBox = new Map(L.buildings.map((b) => [b.id, b]));
let far = 1;
for (const n of L.nodes) far = Math.max(far, c ? Math.hypot(mid(n)[0] - c[0], mid(n)[1] - c[1]) : 0);
for (const n of L.nodes) {
if (cls(n) !== 'Unrelated') continue;
const [x, y] = mid(n); const b = buildingBox.get(roomBuilding.get(n.roomId));
const ref = c ?? (b && b.h !== undefined ? [b.x + b.w / 2, b.y + b.h / 2] : [x, y]);
const d = Math.hypot(x - ref[0], y - ref[1]); const t = Math.min(1, d / far);
const down = y - ref[1] >= 0;
const fan = DISPERSE_2D_OUT + DISPERSE_2D_FAN * t;
let dx = (x - ref[0]) * 0.15;
let ty;
if (b && b.h !== undefined) {
dx = Math.min(b.x + b.w - (n.x + n.w), Math.max(b.x - n.x, dx)); // keep the card within its building's width
ty = down ? b.y + b.h + fan : b.y - n.h - fan;
} else ty = n.y + (down ? fan : -fan);
const dy = (down ? 1 : -1) * Math.max(DISPERSE_2D_MIN, Math.abs(ty - n.y));
offsets.set(n.id, [dx, dy]);
delays.set(n.id, DISPERSE_2D_RIPPLE * t);
}
const floorsKept = new Set(L.rooms.filter((r) => kept.has(r.id)).map((r) => r.floorId));
const buildingsKept = new Set(L.rooms.filter((r) => kept.has(r.id)).map((r) => r.buildingId));
for (const r of L.rooms) if (!kept.has(r.id)) away.rooms.add(r.id);
for (const f of L.floors) if (!floorsKept.has(f.id)) away.floors.add(f.id);
for (const b of L.buildings) if (!buildingsKept.has(b.id)) away.buildings.add(b.id);
return { offsets, delays, away };
}
function gemOutline(shape, r = 9) {
const ring = (n, rot = 0, sy = 1) => Array.from({ length: n }, (_, i) => [Math.cos(rot + (i / n) * Math.PI * 2) * r, Math.sin(rot + (i / n) * Math.PI * 2) * r * sy]);
const pts = {
hex: ring(6), tri: ring(3, -Math.PI / 2), box: [[-r, -r * 0.7], [r, -r * 0.7], [r, r * 0.7], [-r, r * 0.7]], octa: ring(4, Math.PI / 4),
diamond: [[0, -r], [r * 0.7, 0], [0, r], [-r * 0.7, 0]], round: ring(8, Math.PI / 8),
crown: [[-r, r * 0.8], [-r, -r * 0.4], [-r / 2, r * 0.1], [0, -r], [r / 2, r * 0.1], [r, -r * 0.4], [r, r * 0.8]],
}[shape] || ring(4, Math.PI / 4);
return pts.map(([x, y]) => `${+x.toFixed(2)},${+y.toFixed(2)}`).join(' ');
}
return { FLOOR_H, SPACING, ROOM_GAP, BUILDING_GAP, ZONE_GAP, UNKNOWN_ZONE, MAX_FLOOR_LEVEL, gemShapes, gemShapeOf, floorLevels, placeBuildings, VIEW_IDS, resolveView, arrangeView, blendPositions, layoutPoints, layoutBounds, gemFaces, faceNormal, CARD, cardLayout, DISPERSE_2D_OUT, DISPERSE_2D_FAN, DISPERSE_2D_RIPPLE, DISPERSE_2D_MIN, cardFocus, gemOutline };
})();
// ---- scene-buffers.js
const __mod_scene_buffers = (function () {
'use strict';
const { indexModel, nodesOf, edgesOf, RETREAT_TARGET, stepEase, disperseEase, makeVocab, kindInfo, relationInfo, nodeDomain, domainColor, own, dict, VIEW_WARNING, NEUTRAL_COLOR } = __mod_model_access;
const { gemFaces, faceNormal } = __mod_layout;
const { edgePath, arrowTip, vec } = __mod_coords;
const HALO_ALPHA = { Running: 0.6, Waiting: 0.35, Degraded: 0.4, Recovered: 0.4, Completed: 0.28, Failed: 0.45, Blocked: 0.45, Skipped: 0.15 };
const HALO_SIZE = { Running: 1.7, Waiting: 1.45, Degraded: 1.5, Recovered: 1.5, Completed: 1.35, Failed: 1.5, Blocked: 1.5, Skipped: 1.2 };
const ENERGY_COLOR = '#ffb02e';
const STATE_COLOR = {
Running: '#5ac7c3', Waiting: '#f0ba63', Completed: '#74ca9d', Failed: '#f08886', Blocked: '#f08886', Degraded: '#f0ba63', Recovered: '#82adfa', Skipped: '#6c7d97',
};
const NEUTRAL_STATE_COLOR = '#6c7d97';
const EDGE_COLOR = { Selected: '#5ac7c3', Direct: '#82adfa', Context: '#40536a', Unrelated: '#40536a', Idle: '#5b7090' };
const OVERLAY_COLOR = { A: '#82adfa', B: '#f0ba63', forks: '#f08886', blocked: '#f08886', rewritten: '#a78bfa', shared: '#74ca9d' };
const MARK_COLOR = { cause: '#ff5ad1', effect: '#ffa3e8' };
const IDLE_EDGE_ALPHA = 0.55;
const STATIC_EDGE_ALPHA = 0.4; // static relations (no runtime meaning) are drawn weaker when nothing is selected
function colorToRgb(h) {
let s = typeof h === 'string' ? h.replace('#', '') : '';
if (/^[0-9a-fA-F]{3}$/.test(s)) s = [...s].map((c) => c + c).join('');
if (!/^[0-9a-fA-F]{6}$/.test(s)) s = NEUTRAL_COLOR.replace('#', '');
return [parseInt(s.slice(0, 2), 16) / 255, parseInt(s.slice(2, 4), 16) / 255, parseInt(s.slice(4, 6), 16) / 255];
}
const mix = (a, b, t) => [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t];
function stateColor(vocab, st, ownStates = null) {
if (!st || st === 'Idle') return null;
const fromProfile = vocab?.nodeStates?.get(st)?.color;
if (typeof fromProfile === 'string') return fromProfile;
const fallback = own(STATE_COLOR, st);
if (fallback) return fallback;
if (ownStates && ownStates.has(st)) return null;
if (vocab) vocab.warnings.add(VIEW_WARNING.UNKNOWN_STATE, st, 'unknown node state: neutral ring');
return NEUTRAL_STATE_COLOR;
}
const faceCache = new Map();
function faces(shape) {
if (!faceCache.has(shape)) faceCache.set(shape, gemFaces(shape).map((t) => ({ t, n: faceNormal(t) })));
return faceCache.get(shape);
}
function stepEaseAll(retreat, classOf, ids, dt, startDelay = null) {
let moving = false;
for (const id of ids) {
const cur = own(retreat, id) || { alpha: 1, push: 0 };
const next = stepEase(cur, own(RETREAT_TARGET, classOf(id)), dt, startDelay ? startDelay(id) : 0);
retreat[id] = { alpha: next.alpha, push: next.push, wait: next.wait, goal: next.goal };
if (!next.done) moving = true;
}
return moving;
}
const DISPERSE_OUT = 10;
const DISPERSE_FAN = 10;
const DISPERSE_MIN = 16;
function boxOfPoint(p, boxes) {
const all = Object.values(boxes?.buildings || {});
const inside = all.find((b) => p[0] >= b.min[0] && p[0] <= b.max[0] && p[1] >= b.min[1] && p[1] <= b.max[1] && p[2] >= b.min[2] && p[2] <= b.max[2]);
if (inside) return inside;
let best = null; let bd = Infinity;
for (const b of all) { const d = Math.max(b.min[0] - p[0], 0, p[0] - b.max[0]) + Math.max(b.min[2] - p[2], 0, p[2] - b.max[2]); if (d < bd) { bd = d; best = b; } }
return best;
}
function disperseTarget(p, center, box) {
const ref = center ?? (box ? vec.scale(vec.add(box.min, box.max), 0.5) : p);
let side = p[2] - ref[2];
if (Math.abs(side) < 1e-6) side = (p[0] - ref[0]) || 1;
const sign = side > 0 ? 1 : -1;
const fan = DISPERSE_OUT + DISPERSE_FAN * Math.min(1, Math.hypot(p[0] - ref[0], p[2] - ref[2]) / 24);
if (!box) return [p[0], p[1], p[2] + sign * Math.max(DISPERSE_MIN, fan)];
const x = Math.min(box.max[0], Math.max(box.min[0], p[0] + (p[0] - ref[0]) * 0.2));
const z = (sign > 0 ? box.max[2] : box.min[2]) + sign * fan;
return [x, p[1], p[2] + sign * Math.max(DISPERSE_MIN, Math.abs(z - p[2]))];
}
function disperseOffset(p, center, box, push) {
if (!push) return [0, 0, 0];
return vec.scale(vec.sub(disperseTarget(p, center, box), p), disperseEase(push));
}
const DISPERSE_RIPPLE = 1.2;
function disperseDelay(p, center) {
if (!p || !center) return 0;
return DISPERSE_RIPPLE * Math.min(1, vec.len(vec.sub(p, center)) / 40);
}
const HIDDEN_BELOW = 0.01;
const STAGE_LABEL_ALPHA = 0.3;
const STAGE_LABEL_MAX = 40;
const EDGE_KEEP_ALPHA = RETREAT_TARGET.Context.alpha;
function stageEdgeStyle(cls, styled) {
if (cls === 'Selected') return styled ?? { color: EDGE_COLOR.Selected, alpha: 1 };
if (cls === 'Direct') return styled ? { color: styled.color, alpha: styled.alpha * 0.85 } : { color: EDGE_COLOR.Direct, alpha: 0.9 };
if (cls === 'Reached') return styled ? { color: styled.color, alpha: styled.alpha * 0.6 } : { color: EDGE_COLOR.Direct, alpha: 0.45 };
return { color: EDGE_COLOR.Context, alpha: cls === 'Stage' ? 0.16 : 0 };
}
function focusMiddle(rel, positions) {
if (!rel?.active) return null;
const pick = (ids) => [...(ids || [])].map((id) => own(positions, id)).filter(Boolean);
const ps = pick(rel.chosenNodes).length ? pick(rel.chosenNodes) : pick(rel.nearNodes);
return ps.length ? vec.scale(ps.reduce((a, p) => vec.add(a, p), [0, 0, 0]), 1 / ps.length) : null;
}
function frameLines(b, rgba, out) {
const [x0, y0, z0] = b.min; const [x1, y1, z1] = b.max;
const c = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
for (const [i, j] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]) out.push(...c[i], ...rgba, ...c[j], ...rgba);
}
function makeBuffers(model, state, ix = indexModel(model)) {
const vocab = state.vocab || makeVocab(null);
const rel = state.relevance;
const overlay = state.overlay || {};
const retreat = state.retreat || {};
const markPrefix = state.markPrefix || {};
const entities = nodesOf(model);
const visible = state.shownNodeIds || new Set(entities.map((n) => n.id));
const shownEdges = state.shownEdgeIds || new Set(edgesOf(model).map((e) => e.id));
const mesh = { pos: [], nrm: [], col: [], faceCount: 0 };
const lines = []; const points = []; const halos = []; const labels = []; const instances = [];
const hitNodes = []; const hitEdges = [];
const worldPoints = dict();
const pos0 = dict(state.positions);
const extras = [];
for (const x of overlay.overlayNodes || []) {
const p = x.pos ?? (x.between && pos0[x.between[0]] && pos0[x.between[1]] ? vec.add(vec.lerp(pos0[x.between[0]], pos0[x.between[1]], 0.5), [0, 1.6, 0]) : null);
if (p) { pos0[x.id] = p; extras.push(x); }
}
const placed = (n) => { if (pos0[n.id]) return true; vocab.warnings.add(VIEW_WARNING.UNPLACED_NODE, n.id, 'visible node has no position: not drawn'); return false; };
const nodeList = [...entities.filter((n) => visible.has(n.id) && placed(n)),...extras.map((x) => ({ id: x.id, kind: 'Element', name: x.name || x.id, roomId: x.roomId ?? null, props: {}, extra: x }))];
const eye = state.eye || [0, 0, 0];
const center = focusMiddle(rel, pos0);
const placedNodes = nodeList.map((n) => {
const fade = own(retreat, n.id) || { alpha: 1, push: 0 };
const p = vec.add(pos0[n.id], disperseOffset(pos0[n.id], center, boxOfPoint(pos0[n.id], state.boxes), fade.push));
return { n, p, fade };
}).filter((x) => x.fade.alpha >= HIDDEN_BELOW).sort((a, b) => vec.len(vec.sub(b.p, eye)) - vec.len(vec.sub(a.p, eye)));
for (const { n, p } of placedNodes) worldPoints[n.id] = p;
for (const { n, p, fade } of placedNodes) {
const ki = kindInfo(vocab, n.kind);
const shape = ki.shape; const scale = ki.scale;
let rgb = colorToRgb(domainColor(vocab, nodeDomain(n, ix, vocab)));
const st = own(overlay.nodeStates, n.id);
const machine = ix.machines.get(n.id);
const ringHex = stateColor(vocab, st, machine && Array.isArray(machine.states) ? new Set(machine.states) : null);
const ringRgb = ringHex ? colorToRgb(ringHex) : null;
if (ringRgb) rgb = mix(rgb, ringRgb, 0.45);
const cls = rel ? rel.nodeTier(n.id) : 'Selected';
const hovered = state.hover === n.id;
if (cls === 'Selected' && rel?.active) rgb = mix(rgb, [1, 1, 1], 0.35);
if (hovered) rgb = mix(rgb, [1, 1, 1], 0.25);
const a = fade.alpha;
for (const { t, n: nrm } of faces(shape)) {
for (const v of t) { mesh.pos.push(p[0] + v[0] * scale, p[1] + v[1] * scale, p[2] + v[2] * scale); mesh.nrm.push(nrm[0], nrm[1], nrm[2]); mesh.col.push(rgb[0], rgb[1], rgb[2], a); }
mesh.faceCount++;
}
instances.push({ id: n.id, shape, scale, pos: p, color: [rgb[0], rgb[1], rgb[2], a] });
hitNodes.push({ id: n.id, pos: p, radius: 0.62 * scale, alpha: a });
const settled = fade.push < 0.02;
if (ringRgb && a > 0.25 && settled && st !== 'Idle') {
const rc = [...ringRgb, a];
if (a > 0.5 && (!rel?.stage || cls === 'Selected')) halos.push(p[0], p[1], p[2], ...ringRgb, (own(HALO_ALPHA, st) ?? 0.4) * a, (own(HALO_SIZE, st) ?? 2) * scale, halos.length / 9 * 1.7);
const R = 0.95 * scale; const seg = 20;
const dash = st === 'Waiting'; const half = st === 'Degraded';
for (let i = 0; i < seg; i++) {
if (dash && i % 2) continue; if (half && i >= seg / 2) continue;
const a0 = (i / seg) * Math.PI * 2; const a1 = ((i + 1) / seg) * Math.PI * 2;
lines.push(p[0] + Math.cos(a0) * R, p[1] - 0.45 * scale, p[2] + Math.sin(a0) * R, ...rc, p[0] + Math.cos(a1) * R, p[1] - 0.45 * scale, p[2] + Math.sin(a1) * R, ...rc);
}
if (st === 'Completed') { const R2 = R * 0.72; for (let i = 0; i < seg; i++) { const a0 = (i / seg) * Math.PI * 2; const a1 = ((i + 1) / seg) * Math.PI * 2; lines.push(p[0] + Math.cos(a0) * R2, p[1] - 0.45 * scale, p[2] + Math.sin(a0) * R2, ...rc, p[0] + Math.cos(a1) * R2, p[1] - 0.45 * scale, p[2] + Math.sin(a1) * R2, ...rc); } }
if (st === 'Failed' || st === 'Blocked') { const h = p[1] + 0.95 * scale; const d = 0.3 * scale; lines.push(p[0] - d, h - d, p[2], ...rc, p[0] + d, h + d, p[2], ...rc, p[0] - d, h + d, p[2], ...rc, p[0] + d, h - d, p[2], ...rc); }
}
const mark = own(overlay.marks, n.id);
const markHex = own(MARK_COLOR, mark);
if (markHex && a > 0.3 && settled) {
const mc = [...colorToRgb(markHex), a];
const top = p[1] + 0.85 * scale; const seg = 18;
const radii = mark === 'cause' ? [0.42 * scale] : [0.42 * scale, 0.6 * scale];
for (const R of radii) for (let i = 0; i < seg; i++) {
const a0 = (i / seg) * Math.PI * 2; const a1 = ((i + 1) / seg) * Math.PI * 2;
lines.push(p[0] + Math.cos(a0) * R, top, p[2] + Math.sin(a0) * R, ...mc, p[0] + Math.cos(a1) * R, top, p[2] + Math.sin(a1) * R, ...mc);
}
if (mark === 'cause') lines.push(p[0], top, p[2], ...mc, p[0], top + 1.4 * scale, p[2], ...mc);
labels.push({ id: n.id, pos: [p[0], p[1] + (mark === 'cause' ? 2.4 : 1.2) * scale, p[2]], text: `${own(markPrefix, mark) ?? ''}${n.name}`, cls: 'Selected', state: st || null, mark });
}
const stageLabelled = rel?.stage && a >= STAGE_LABEL_ALPHA;
const named = hovered || stageLabelled || (rel?.active && (cls === 'Selected' || cls === 'Direct') && a > 0.5 && settled);
if (named && !(markHex && a > 0.3 && settled && !hovered)) labels.push({ id: n.id, pos: [p[0], p[1] + 0.9 * scale, p[2]], text: n.name, cls: hovered ? 'Hover' : cls, state: st || null });
}
const PRI = { Hover: 0, Selected: 1, Direct: 2, Reached: 3, Stage: 4 };
labels.sort((x, y) => (PRI[x.cls] ?? 5) - (PRI[y.cls] ?? 5)); labels.length = Math.min(labels.length, rel?.stage ? STAGE_LABEL_MAX : 28);
mesh.pos = new Float32Array(mesh.pos); mesh.nrm = new Float32Array(mesh.nrm); mesh.col = new Float32Array(mesh.col);
const fadeOf = (id) => own(retreat, id)?.alpha ?? 1;
const edgeList = [...edgesOf(model).filter((e) => shownEdges.has(e.id) && worldPoints[e.sourceId] && worldPoints[e.targetId]), ...(overlay.overlayEdges || []).filter((e) => worldPoints[e.sourceId] && worldPoints[e.targetId])];
const groups = new Map();
for (const e of edgeList) { const k = [e.sourceId, e.targetId].sort().join('|'); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
const drawn = [];
for (const g of groups.values()) {
g.sort((a, b) => (a.id < b.id ? -1 : 1));
g.forEach((e, i) => {
const overlayStyle = own(overlay.edgePaints, e.id);
const cls = rel ? rel.edgeTier(e) : 'Selected';
const info = e.relation ? relationInfo(vocab, e.relation) : null;
let color; let alpha;
const styled = overlayStyle ? { color: overlayStyle.color ?? own(OVERLAY_COLOR, overlayStyle.kind) ?? EDGE_COLOR.Direct, alpha: overlayStyle.alpha ?? 1 } : null;
if (rel?.stage) ({ color, alpha } = stageEdgeStyle(cls, styled));
else if (styled) ({ color, alpha } = styled);
else if (rel?.active) { color = EDGE_COLOR[cls]; alpha = { Selected: 1, Direct: 0.95, Context: 0.2, Unrelated: 0.03 }[cls]; }
else { color = info && state.edgeStyle !== 'class' ? info.color : EDGE_COLOR.Idle; alpha = info?.static ? STATIC_EDGE_ALPHA : IDLE_EDGE_ALPHA; }
alpha *= Math.min(1, Math.min(fadeOf(e.sourceId), fadeOf(e.targetId)) / EDGE_KEEP_ALPHA);
if (alpha < HIDDEN_BELOW) return;
const rgba = [...colorToRgb(color), alpha];
const [c0, c1] = [e.sourceId, e.targetId].sort();
let poly = edgePath(worldPoints[c0], worldPoints[c1], i, g.length);
if (e.sourceId !== c0) poly = poly.slice().reverse();
const dashed = info?.trustBoundary === true;
for (let k = 0; k < poly.length - 1; k++) {
if (!(dashed && k % 2)) lines.push(...poly[k], ...rgba, ...poly[k + 1], ...rgba);
hitEdges.push({ id: e.id, a: poly[k], b: poly[k + 1] });
}
if (e.sourceId !== e.targetId) {
const tip = poly.at(-1); const prev = poly[poly.length - 3] ?? poly[0];
const [w1, tp, w2] = arrowTip(prev, vec.lerp(tip, prev, 0.12), 0.38);
lines.push(...w1, ...rgba, ...tp, ...rgba, ...tp, ...rgba, ...w2, ...rgba);
}
drawn.push({ id: e.id, poly, alpha, relation: e.relation ?? null, dashed });
if (rel?.active && cls === 'Selected' && alpha > 0.5 && e.sourceId !== e.targetId && labels.length < (rel.stage ? STAGE_LABEL_MAX + 12 : 40)) {
const mid = poly[Math.floor(poly.length / 2)];
labels.push({ id: e.id, pos: [mid[0], mid[1] + 0.5, mid[2]], text: e.label || e.relation || e.id, cls: 'Selected', state: null, edge: true });
}
});
}
if (state.showFrames && state.boxes) {
for (const b of Object.values(state.boxes.buildings || {})) frameLines(b, [0.51, 0.68, 0.98, 0.35], lines);
for (const b of Object.values(state.boxes.rooms || {})) frameLines(b, [0.35, 0.45, 0.56, 0.22], lines);
}
for (const m of overlay.pulses || []) {
const d = drawn.find((x) => x.id === m.edgeId);
if (!d || d.alpha < 0.5) continue; // a pulse only travels on an edge that is clearly shown
const idx = Math.min(d.poly.length - 1, Math.max(0, Math.round(m.progress * (d.poly.length - 1))));
points.push(...d.poly[idx], ...colorToRgb(m.color ?? '#ffffff'), d.alpha);
}
return {
mesh, lines: new Float32Array(lines), lineCount: lines.length / 7, points: new Float32Array(points), dotCount: points.length / 7,
halos: new Float32Array(halos), haloCount: halos.length / 9, hitNodes, hitEdges, labels, worldPoints, paintedEdgeIds: drawn.map((d) => d.id), drawn, instances,
warnings: vocab.warnings,
};
}
function templateGeometry(shape) {
const t = faces(shape); const pos = []; const nrm = [];
for (const { t: tri, n } of t) for (const v of tri) { pos.push(v[0], v[1], v[2]); nrm.push(n[0], n[1], n[2]); }
return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), faceCount: t.length };
}
function toInstanceBuffers(instances) {
const groups = new Map();
for (const it of instances) { if (!groups.has(it.shape)) groups.set(it.shape, []); groups.get(it.shape).push(it); }
return [...groups.entries()].map(([shape, list]) => {
const offset = new Float32Array(list.length * 3); const scale = new Float32Array(list.length); const color = new Float32Array(list.length * 4);
list.forEach((it, i) => { offset.set(it.pos, i * 3); scale[i] = it.scale; color.set(it.color, i * 4); });
return { shape, count: list.length, template: templateGeometry(shape), offset, scale, color, ids: list.map((it) => it.id) };
});
}
return { HALO_ALPHA, HALO_SIZE, ENERGY_COLOR, STATE_COLOR, NEUTRAL_STATE_COLOR, EDGE_COLOR, OVERLAY_COLOR, MARK_COLOR, IDLE_EDGE_ALPHA, STATIC_EDGE_ALPHA, colorToRgb, stateColor, stepEaseAll, DISPERSE_OUT, DISPERSE_FAN, DISPERSE_MIN, boxOfPoint, disperseTarget, disperseOffset, DISPERSE_RIPPLE, disperseDelay, HIDDEN_BELOW, STAGE_LABEL_ALPHA, STAGE_LABEL_MAX, EDGE_KEEP_ALPHA, stageEdgeStyle, focusMiddle, makeBuffers, templateGeometry, toInstanceBuffers };
})();
// ---- playback.js
const __mod_playback = (function () {
'use strict';
const { incident, keepFocus, asList, isRecord, own, dict, VIEW_WARNING } = __mod_model_access;
const { ENERGY_COLOR } = __mod_scene_buffers;
const FAILED = ['Blocked', 'Denied', 'Failed', 'Timeout'];
const ROLE_COLOR = {
cause: '#ff5ad1', block: '#f08886', deny: '#f08886', fail: '#f08886', timeout: '#f0ba63', reject: '#f0ba63', starve: '#c9a25e', degrade: '#f0ba63',
retry: '#82adfa', recovery: '#82adfa', compensation: '#82adfa', bypass: '#a78bfa', gain: '#a78bfa', lost: '#7f8fa8', effect: '#ffa3e8',
};
const NEUTRAL_ROLE_COLOR = '#7f8fa8';
const roleColor = (role) => own(ROLE_COLOR, role) ?? NEUTRAL_ROLE_COLOR;
const baselineFor = (runs, caseId) => own(runs?.baselines, caseId) ?? null;
function pairRuns(runs, caseId, interventionId) {
const p = asList(runs?.pairs).find((x) => x && x.caseId === caseId && x.interventionId === interventionId);
if (!p) return null;
return { A: baselineFor(runs, caseId), B: p.B ?? null, chain: p.chain ?? null, diff: p.diff ?? null, status: p.status ?? null, errors: asList(p.errors) };
}
const changesOf = (ev) => asList(ev?.stateChanges ?? ev?.changes);
const traceOf = (run) => asList(run?.trace);
function normIndex(idx, length) {
const n = Math.max(0, Math.trunc(Number(length)) || 0);
const t = typeof idx === 'number' && Number.isFinite(idx) ? Math.trunc(idx) : -1;
const i = Math.max(-1, Math.min(n - 1, t));
return { i, ok: i === idx };
}
const warnIndex = (warnings, idx) => warnings?.add(VIEW_WARNING.BAD_INDEX, `${idx}`, 'playback index is not an integer inside the trace: clamped');
function stateByFold(run, idx, warnings = null) {
const trace = traceOf(run);
const st = dict(isRecord(run?.initialState) ? run.initialState : null);
const { i: last, ok } = normIndex(idx, trace.length);
if (!ok) warnIndex(warnings, idx);
for (let i = 0; i <= last; i++) for (const ch of changesOf(trace[i])) st[ch.nodeId] = ch.to;
return st;
}
function seekState(run, idx, warnings = null) {
const trace = traceOf(run);
const { i, ok } = normIndex(idx, trace.length);
if (!ok) warnIndex(warnings, idx);
let snap = null;
for (const s of asList(run?.snapshots)) if (isRecord(s) && Number.isInteger(s.index) && s.index >= -1 && s.index <= i && isRecord(s.state) && (!snap || s.index >= snap.index)) snap = s;
const st = dict(snap ? snap.state : (isRecord(run?.initialState) ? run.initialState : null));
for (let k = (snap ? snap.index : -1) + 1; k <= i; k++) for (const ch of changesOf(trace[k])) st[ch.nodeId] = ch.to;
return st;
}
function clampIndex(idx, length) { return normIndex(idx, length).i; }
function stepIndex(idx, length, dir, stops = null) {
const cur = clampIndex(idx, length);
if (stops) return dir > 0 ? (followingStop(stops, cur) ?? cur) : precedingStop(stops, cur);
return clampIndex(cur + (dir > 0 ? 1 : -1), length);
}
function closeOverEdges(ix, nodes, edges) {
for (const id of edges) { const e = ix.edges.get(id); if (e) { nodes.add(e.sourceId); nodes.add(e.targetId); } }
return { nodes, edges };
}
function caseStage(ix, run) {
const nodes = new Set(); const edges = new Set();
for (const ev of traceOf(run)) { nodes.add(ev.sourceNodeId); nodes.add(ev.targetNodeId); if (ev.edgeId) edges.add(ev.edgeId); }
return closeOverEdges(ix, nodes, edges);
}
function chainStage(ix, chain) {
const nodes = new Set(); const edges = new Set();
if (!chain?.root) return { nodes, edges };
const el = chain.root.element;
for (const id of asList(chain.root.nodeIds)) nodes.add(id);
for (const id of asList(chain.root.edgeIds)) edges.add(id);
for (const id of asList(el?.between)) nodes.add(id);
for (const x of asList(el?.edges)) { edges.add(x.id); nodes.add(x.sourceId); nodes.add(x.targetId); }
for (const s of asList(chain.steps)) { nodes.add(s.sourceNodeId); nodes.add(s.targetNodeId); if (s.edgeId) edges.add(s.edgeId); }
for (const e of asList(chain.effects)) nodes.add(e.nodeId);
return closeOverEdges(ix, nodes, edges);
}
function causalStepStops(chain, length) {
if (!length) return [];
const last = length - 1;
const at = asList(chain?.steps).map((st) => Math.min(last, st.at)).filter((i) => i >= 0);
return [...new Set([...at, last])].sort((a, b) => a - b);
}
const followingStop = (stops, idx) => stops.find((i) => i > idx) ?? null;
const precedingStop = (stops, idx) => [...stops].reverse().find((i) => i < idx) ?? -1;
const stepsPlayingAt = (chain, idx) => asList(chain?.steps).filter((s) => s.side === 'B' && s.bIndex === idx);
const shownSteps = (chain, idx) => asList(chain?.steps).filter((st) => st.at <= idx);
function causalLayer(chain, idx) {
const edgePaints = dict(); const marks = dict();
if (!chain || chain.status !== 'OK' || !chain.root) return { edgePaints, marks, overlayNodes: [], overlayEdges: [] };
const el = chain.root.element;
for (const id of asList(chain.root.edgeIds)) edgePaints[id] = { color: ROLE_COLOR.cause, alpha: 1 };
if (el?.replacedEdgeId) edgePaints[el.replacedEdgeId] = { color: ROLE_COLOR.lost, alpha: 0.35 };
for (const s of shownSteps(chain, idx)) {
if (!s.edgeId) continue;
const prev = edgePaints[s.edgeId];
if (prev && prev.color === ROLE_COLOR.cause && !(el && asList(el.edges).some((x) => x.id === s.edgeId))) continue; // the cause stays marked
edgePaints[s.edgeId] = { color: roleColor(s.role), alpha: s.role === 'lost' ? 0.4 : 1 };
}
for (const n of asList(chain.root.nodeIds)) marks[n] = 'cause';
if (idx >= asList(chain.steps).reduce((m, st) => Math.max(m, st.at), -1)) for (const e of asList(chain.effects)) if (!Object.hasOwn(marks, e.nodeId)) marks[e.nodeId] = 'effect';
const overlayNodes = el?.between ? [{ id: el.id, name: el.name, between: el.between, roomId: el.roomId ?? null }] : [];
const overlayEdges = el ? asList(el.edges) : [];
return { edgePaints, marks, overlayNodes, overlayEdges };
}
function stageFocus(ix, scope, focus = {}) {
const sel = new Set(focus.nodes || []); const pickedE = new Set(focus.edges || []);
const dir = new Set(focus.nearNodes || []); const directE = new Set(focus.nearEdges || []);
const rch = new Set(focus.passedNodes || []); const reachedE = new Set(focus.passedEdges || []);
const nodes = new Set([...scope.nodes, ...sel]); const edges = new Set([...scope.edges, ...pickedE]);
for (const id of sel) dir.delete(id);
const nodeTier = (id) => (sel.has(id) ? 'Selected' : dir.has(id) ? 'Direct' : rch.has(id) ? 'Reached' : nodes.has(id) ? 'Stage' : 'Outside');
const edgeTier = (e) => (pickedE.has(e.id) ? 'Selected' : directE.has(e.id) ? 'Direct' : reachedE.has(e.id) ? 'Reached' : edges.has(e.id) ? 'Stage' : 'Outside');
return {
active: true, stage: { nodes, edges }, key: [...nodes].sort().join(',') + '|' + [...edges].sort().join(','),
chosenNodes: sel, chosenEdges: pickedE, nearNodes: dir, nearEdges: directE, backdropNodes: new Set(),
nodeTier, edgeTier,
};
}
function focusOfClick(ix, scope, sel, element) {
if (sel.type === 'element' && element) {
return { nodes: [element.id, ...asList(element.between)], edges: asList(element.edges).map((x) => x.id) };
}
if (sel.type === 'edge') {
const e = ix.edges.get(sel.id);
return { nodes: [], edges: [sel.id], nearNodes: e ? [e.sourceId, e.targetId] : [] };
}
const near = incident(ix, sel.id).filter((e) => scope.edges.has(e.id));
return { nodes: [sel.id], nearEdges: near.map((e) => e.id), nearNodes: near.flatMap((e) => [e.sourceId, e.targetId]) };
}
function replayView({ ix, tab, run, chain, idx, sel = null, warnings = null }) {
if (!run || !Array.isArray(run.trace)) return null;
const chainTab = tab === '5D';
if (chainTab && (!chain?.root || chain.status !== 'OK')) return null; // no chain, or the pair was rejected: nothing to play
const scope = chainTab ? chainStage(ix, chain) : caseStage(ix, run);
const { i, ok } = normIndex(idx, run.trace.length);
if (!ok) warnIndex(warnings, idx);
const ev = i >= 0 ? run.trace[i] ?? null : null;
const nodeStates = dict();
if (run.trace.length) for (const [k, v] of Object.entries(seekState(run, i))) if (v !== 'Idle' && scope.nodes.has(k)) nodeStates[k] = v;
const overlay = { nodeStates, edgePaints: dict(), pulses: [] };
let focus; let live = []; // live: edges that carry the event animation at this position
if (chainTab) {
const co = causalLayer(chain, i);
for (const [k, v] of Object.entries(co.edgePaints)) if (scope.edges.has(k)) overlay.edgePaints[k] = v;
Object.assign(overlay, { marks: co.marks, overlayNodes: co.overlayNodes, overlayEdges: co.overlayEdges });
const now = stepsPlayingAt(chain, i);
live = now.filter((s) => s.edgeId).map((s) => s.edgeId);
const past = shownSteps(chain, i).filter((s) => !now.includes(s));
focus = {
nodes: [...asList(chain.root.nodeIds), ...Object.keys(co.marks), ...co.overlayNodes.map((x) => x.id), ...now.flatMap((s) => [s.sourceNodeId, s.targetNodeId])],
edges: [...asList(chain.root.edgeIds), ...co.overlayEdges.map((x) => x.id), ...live],
passedNodes: past.flatMap((s) => [s.sourceNodeId, s.targetNodeId]), passedEdges: past.map((s) => s.edgeId).filter(Boolean),
};
} else {
live = ev?.edgeId ? [ev.edgeId] : [];
const past = run.trace.slice(0, Math.max(0, i));
focus = {
nodes: ev ? [ev.sourceNodeId, ev.targetNodeId] : [], edges: live,
passedNodes: past.flatMap((e) => [e.sourceNodeId, e.targetNodeId]), passedEdges: past.map((e) => e.edgeId).filter(Boolean),
};
}
for (const id of live) {
const outcome = chainTab ? stepsPlayingAt(chain, i).find((s) => s.edgeId === id)?.outcome : ev?.outcome;
if (!chainTab || !overlay.edgePaints[id]) overlay.edgePaints[id] = FAILED.includes(outcome) ? { kind: 'blocked' } : { kind: 'shared', color: ENERGY_COLOR };
overlay.pulses.push({ edgeId: id, progress: 0.5, color: ENERGY_COLOR });
}
if (sel) {
const cf = focusOfClick(ix, scope, sel, chain?.root?.element);
focus = { ...cf, passedNodes: [...focus.passedNodes, ...focus.nodes], passedEdges: [...focus.passedEdges, ...focus.edges] };
}
return { scope, rel: stageFocus(ix, scope, focus), overlay, causal: chainTab ? asList(chain?.steps).some((st) => st.at === i) : true };
}
function stageSubset(ix, layout, detail, rel) {
const f = detail ? keepFocus(detail, rel) : { nodeIds: layout.shownNodeIds, edgeIds: layout.shownEdgeIds };
return {
nodeIds: new Set([...f.nodeIds].filter((id) => layout.shownNodeIds.has(id) && rel.nodeTier(id) !== 'Outside')),
edgeIds: new Set([...f.edgeIds].filter((id) => (layout.shownEdgeIds.has(id) || rel.chosenEdges.has(id)) && ix.edges.has(id) && rel.edgeTier(ix.edges.get(id)) !== 'Outside')),
};
}
return { ROLE_COLOR, NEUTRAL_ROLE_COLOR, roleColor, baselineFor, pairRuns, changesOf, traceOf, normIndex, stateByFold, seekState, clampIndex, stepIndex, caseStage, chainStage, causalStepStops, followingStop, precedingStop, stepsPlayingAt, shownSteps, causalLayer, stageFocus, replayView, stageSubset };
})();
// ---- search.js
const __mod_search = (function () {
'use strict';
const { indexModel, nodesOf, edgesOf, casesOf, makeVocab, levelOfNode, levelOfRelation, reachesLevel, asList } = __mod_model_access;
const SEARCH_TABS = ['1D', '2D', '3D', '4D', '5D', 'QA'];
const SEARCH_KINDS = ['node', 'edge', 'diagram', 'case', 'intervention', 'requirement', 'tbd'];
const SEARCH_LIMIT = 40;
const fold = (s) => String(s ?? '').normalize('NFKC').toLowerCase();
function entry(kind, tab, id, title, sub, fields, target) {
return { kind, tab, id, title, sub, target, f: fields.map(fold), key: `${tab}:${kind}:${id}` };
}
function makeSearchIndex(model, diagrams = [], opts = {}) {
const vocab = opts.vocab || makeVocab(null);
const ix = opts.ix || indexModel(model);
const levels = vocab.levels;
const lowestLevel = (pred) => levels.find(pred) ?? levels[levels.length - 1];
const out = []; const nodes = nodesOf(model); const cases = casesOf(model);
const name = (id) => ix.nodes.get(id)?.name ?? id;
for (const n of nodes) {
const f = [n.id, n.name, n.sub, n.kind, n.group];
const level = levelOfNode(n, ix, vocab);
for (const tab of ['2D', '3D']) out.push(entry('node', tab, n.id, n.name, `${n.kind} \u00b7 ${n.id}${n.sub ? ` \u00b7 ${n.sub}` : ''}`, f, { tab, node: n.id, level }));
}
for (const e of edgesOf(model)) {
const a = name(e.sourceId); const b = name(e.targetId);
const need = [levelOfRelation(vocab, e.relation), levelOfNode(ix.nodes.get(e.sourceId), ix, vocab), levelOfNode(ix.nodes.get(e.targetId), ix, vocab)];
const level = lowestLevel((l) => need.every((x) => reachesLevel(vocab, l, x)));
out.push(entry('edge', '2D', e.id, e.label || e.id, `${a} \u2192 ${b} \u00b7 ${e.relation ?? e.type} \u00b7 ${e.id}`, [e.id, e.label, e.relation, a, b], { tab: '2D', edge: e.id, level }));
}
for (const d of asList(diagrams)) out.push(entry('diagram', '2D', d.id, d.title, `${d.type} \u00b7 ${d.id}`, [d.id, d.title, d.type], { tab: '2D', diagram: d.id }));
for (const c of cases) out.push(entry('case', '4D', c.caseId, c.title, `${c.category} \u00b7 ${c.caseId}`, [c.caseId, c.title, c.category, ...asList(c.requirementIds)], { tab: '4D', caseId: c.caseId }));
for (const iv of asList(model?.interventions)) {
const caseId = asList(iv.appliesTo)[0] ?? cases[0]?.caseId ?? null;
out.push(entry('intervention', '5D', iv.interventionId, iv.description ?? iv.interventionId, `${iv.type} \u00b7 ${iv.interventionId} \u00b7 ${caseId ?? ''}`, [iv.interventionId, iv.description, iv.type, iv.targetNodeId, iv.note], { tab: '5D', caseId, interventionKey: iv.interventionId }));
}
for (const r of asList(model?.requirements)) out.push(entry('requirement', '1D', r.id, r.title, `${r.id} \u00b7 ${r.source}`, [r.id, r.title, r.source, ...asList(r.tstIds), ...asList(r.designIds)], { tab: '1D', level: levels[0], node: asList(r.nodeIds)[0] ?? null }));
for (const t of asList(model?.tbds)) out.push(entry('tbd', 'QA', t.id, t.title, `${t.id} \u00b7 ${t.decision}`, [t.id, t.title, t.decision], { tab: 'QA' }));
return out;
}
function rankWord(e, w) {
let best = 0;
e.f.forEach((v, i) => {
if (!v.includes(w)) return;
const s = (i === 0 ? 5 : i === 1 ? 4 : 1) + (v === w ? 6 : v.startsWith(w) ? 3 : 0);
if (s > best) best = s;
});
return best;
}
function search(index, query, limit = SEARCH_LIMIT) {
const words = fold(query).split(/\s+/).filter(Boolean); if (!words.length) return [];
const hits = [];
index.forEach((e, order) => {
let total = 0;
for (const w of words) { const s = rankWord(e, w); if (!s) return; total += s; }
hits.push({ e, total: e.kind === 'edge' ? total - 2 : total, order }); // lines are numerous and repeat their labels: parts and documents list first
});
hits.sort((a, b) => b.total - a.total || SEARCH_TABS.indexOf(a.e.tab) - SEARCH_TABS.indexOf(b.e.tab) || a.order - b.order);
return hits.slice(0, limit).map((h) => h.e);
}
return { SEARCH_TABS, SEARCH_KINDS, SEARCH_LIMIT, makeSearchIndex, search };
})();
// ---- ui/boundary.js
const __mod_ui_boundary = (function () {
'use strict';
const BOUNDARY_MESSAGE_LIMIT = 300;
function boundaryRecord(where, err) {
let message = '';
try { message = String(err && err.message !== undefined ? err.message : err); } catch (e) { message = 'unprintable'; } // diag: author-facing
const rec = { where: String(where), message: message.length > BOUNDARY_MESSAGE_LIMIT ? message.slice(0, BOUNDARY_MESSAGE_LIMIT) : message };
if (err && typeof err.stack === 'string') rec.stack = err.stack.split('\n').slice(0, 4).join('\n');
return rec;
}
function makeBoundary(sink) {
let n = 0;
const report = (where, err) => {
n += 1;
const rec = boundaryRecord(where, err);
sink.push(rec);
return rec;
};
return {
report,
guardRun(where, fn, fallback) {
try { return fn(); } catch (e) { report(where, e); return fallback; }
},
frameRun(counter, where, fn) {
let ok = true; let error = null;
try { fn(); } catch (e) { ok = false; error = report(where, e); } finally { counter.n += 1; }
return { ok, error };
},
count: () => n,
};
}
return { boundaryRecord, makeBoundary };
})();
// ---- ui/diagram-model.js
const __mod_ui_diagram_model = (function () {
'use strict';
const { asList, isRecord, own } = __mod_model_access;
const { seekState, changesOf, traceOf } = __mod_playback;
const { truncateText } = __mod_label_fit;
const SEQ_COL_W = 120;
const SEQ_ROW_H = 22;
const SEQ_HEAD_H = 34;
const SEQ_MAX_COLS = 10;
const SEQ_MAX_ROWS = 40;
const STATE_W = 112;
const STATE_H = 30;
const STATE_PAD = 24;
const SEQ_FAILED = ['Blocked', 'Denied', 'Failed', 'Timeout'];
const diagramNodeName = (ctx, res, id) => {
const n = ctx.ix.nodes.get(id);
if (n && typeof n.name === 'string' && n.name) return n.name;
const el = res && res.chain && res.chain.root ? res.chain.root.element : null;
if (el && el.id === id && typeof el.name === 'string' && el.name) return el.name;
return typeof id === 'string' ? id : '';
};
function seqModel(ctx, res, idx) {
const trace = traceOf(res && res.run).map((e) => (isRecord(e) ? e : {}));
const first = trace.length > SEQ_MAX_ROWS ? Math.max(0, Math.min(trace.length - SEQ_MAX_ROWS, idx - Math.floor(SEQ_MAX_ROWS / 3))) : 0;
const part = trace.slice(first, first + SEQ_MAX_ROWS);
const ids = [];
for (const ev of part) for (const id of [ev.sourceNodeId, ev.targetNodeId]) if (typeof id === 'string' && !ids.includes(id)) ids.push(id);
const shown = ids.slice(0, SEQ_MAX_COLS);
const cols = shown.map((id, i) => ({ id, name: diagramNodeName(ctx, res, id), x: SEQ_COL_W / 2 + i * SEQ_COL_W }));
const at = new Map(cols.map((c) => [c.id, c.x]));
const rows = [];
part.forEach((ev, k) => {
const x1 = at.get(ev.sourceNodeId); const x2 = at.get(ev.targetNodeId);
if (x1 === undefined || x2 === undefined) return;
rows.push({
index: first + k, x1, x2, y: SEQ_HEAD_H + SEQ_ROW_H * (rows.length + 0.7), type: typeof ev.eventType === 'string' ? ev.eventType : '', outcome: typeof ev.outcome === 'string' ? ev.outcome : '', label: (typeof ev.eventType === 'string' ? ev.eventType : '') + (typeof ev.outcome === 'string' && ev.outcome ? ' ' + ev.outcome : ''),
failed: SEQ_FAILED.includes(ev.outcome), now: first + k === idx, self: x1 === x2, back: ev.eventType === 'Return', // diag: engine event type id, not shown text
});
});
return { cols, rows, width: Math.max(SEQ_COL_W, cols.length * SEQ_COL_W), height: SEQ_HEAD_H + SEQ_ROW_H * (rows.length + 1), from: first, total: trace.length, cut: part.length < trace.length || ids.length > shown.length };
}
function stateMachineFor(ctx, res, sel) {
const machines = asList(ctx.model.machines).filter((m) => isRecord(m) && typeof m.id === 'string');
if (!machines.length) return null;
if (sel && sel.type === 'node') { const m = machines.find((x) => x.id === sel.id); if (m) return m; }
const count = new Map();
for (const ev of traceOf(res && res.run)) for (const c of changesOf(ev)) count.set(c.nodeId, (count.get(c.nodeId) || 0) + 1);
let best = null;
for (const m of machines) if (count.has(m.id) && (!best || count.get(m.id) > count.get(best.id))) best = m;
return best || machines[0];
}
const diaFinite = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
function transitionPath(a, b, swing) {
if (a === b) return { d: 'M ' + (a.x - 14) + ' ' + a.y + ' C ' + (a.x - 30) + ' ' + (a.y - 46) + ' ' + (a.x + 30) + ' ' + (a.y - 46) + ' ' + (a.x + 14) + ' ' + a.y, lx: a.x, ly: a.y - 38 };
const dx = b.x - a.x; const dy = b.y - a.y; const len = Math.hypot(dx, dy) || 1;
const edge = (p, ux, uy) => { const k = Math.min(ux ? (STATE_W / 2) / Math.abs(ux) : Infinity, uy ? (STATE_H / 2) / Math.abs(uy) : Infinity); return { x: p.x + ux * k, y: p.y + uy * k }; };
const ux = dx / len; const uy = dy / len;
const s = edge(a, ux, uy); const e = edge(b, -ux, -uy);
const mx = (s.x + e.x) / 2 + (-uy) * swing; const my = (s.y + e.y) / 2 + ux * swing;
return { d: 'M ' + s.x + ' ' + s.y + ' Q ' + mx + ' ' + my + ' ' + e.x + ' ' + e.y, lx: (s.x + 2 * mx + e.x) / 4, ly: (s.y + 2 * my + e.y) / 4 };
}
function stateModel(ctx, res, machine, idx) {
const ids = [...new Set([...asList(machine.states), machine.initial, ...asList(machine.terminal), ...asList(machine.transitions).flatMap((t) => (isRecord(t) ? [t.from, t.to] : []))].filter((s) => typeof s === 'string' && s))];
const n = ids.length;
const current = res && res.run ? own(seekState(res.run, idx), machine.id) ?? null : null;
const pos = new Map();
if (n <= 8) {
const r = Math.max(70, n * 22);
ids.forEach((id, i) => { const th = n === 1 ? 0 : (i / n) * Math.PI * 2 - Math.PI / 2; pos.set(id, { x: STATE_PAD + STATE_W / 2 + r * 1.4 + Math.cos(th) * r * 1.4, y: STATE_PAD + STATE_H / 2 + 46 + r + Math.sin(th) * r }); });
} else {
const cols = Math.ceil(Math.sqrt(n * 1.4));
ids.forEach((id, i) => pos.set(id, { x: STATE_PAD + STATE_W / 2 + (i % cols) * (STATE_W + 36), y: STATE_PAD + STATE_H / 2 + 20 + Math.floor(i / cols) * (STATE_H + 50) }));
}
const terminal = new Set(asList(machine.terminal));
const states = ids.map((id) => { const p = pos.get(id); return { id, x: p.x, y: p.y, w: STATE_W, h: STATE_H, initial: id === machine.initial, terminal: terminal.has(id), current: current === id }; });
const seen = new Map();
const transitions = [];
asList(machine.transitions).filter(isRecord).slice(0, 80).forEach((t, k) => {
const a = pos.get(t.from); const b = pos.get(t.to);
if (!a || !b) return;
const key = [t.from, t.to].sort().join('|'); const c = seen.get(key) || 0; seen.set(key, c + 1);
const p = transitionPath(a, b, a === b ? 0 : 18 * (c % 2 ? -1 : 1) * Math.ceil(c / 2) + (t.from > t.to ? 10 : -10));
const label = typeof t.label === 'string' && t.label ? t.label : typeof t.guard === 'string' ? t.guard : typeof t.errorCode === 'string' ? t.errorCode : '';
transitions.push({ id: typeof t.id === 'string' ? t.id : 'transition-' + k, d: p.d, lx: diaFinite(p.lx, 0), ly: diaFinite(p.ly, 0), label: truncateText(label, 90, 10) });
});
const width = Math.max(240, ...states.map((s) => s.x + STATE_W / 2 + STATE_PAD)); const height = Math.max(120, ...states.map((s) => s.y + STATE_H / 2 + STATE_PAD + 24));
return { id: machine.id, name: diagramNodeName(ctx, res, machine.id), states, transitions, width, height };
}
return { SEQ_COL_W, SEQ_ROW_H, SEQ_HEAD_H, SEQ_MAX_COLS, SEQ_MAX_ROWS, STATE_W, STATE_H, STATE_PAD, seqModel, stateMachineFor, stateModel };
})();
// ---- ui/doc-state.js
const __mod_ui_doc_state = (function () {
'use strict';
const { assemble, hash32Hex } = __mod_assemble;
const { asList, isRecord } = __mod_model_access;
const DOC_MODES = ['requirement', 'element', 'free'];
const DOC_LEVELS = ['REQ', 'BD', 'DD'];
const DOC_CHAPTER_HEAD = /^## (\d+)\. (.*)$/;
function docDataset(ctx) {
const f = ctx.bundle.fragments; const c = ctx.bundle.closures;
if (!isRecord(f) || !isRecord(c) || !Array.isArray(f.items) || !Array.isArray(f.elements) || !Array.isArray(f.always) || !isRecord(f.frame) || !isRecord(f.groups)) return null;
return { fragments: f, closures: c };
}
function docKnownIds(ctx) {
const ds = docDataset(ctx);
return ds ? new Set(ds.fragments.elements.filter((id) => Array.isArray(ds.closures[id]))) : new Set();
}
function docInitial(ctx, level, params) {
const known = docKnownIds(ctx);
const lv = DOC_LEVELS.includes(level) ? level : (ctx.levels.includes(level) ? level : DOC_LEVELS[1]);
const reqs = ctx.requirements.filter((r) => known.has(r.id));
const st = { mode: 'requirement', level: DOC_LEVELS.includes(lv) ? lv : DOC_LEVELS[1], reqId: reqs.length ? reqs[0].id : null, elementId: null, ids: [], filter: '' };
const p = isRecord(params) ? params : {};
if (typeof p.req === 'string' && known.has(p.req)) { st.reqId = p.req; st.mode = 'requirement'; }
if (!reqs.length) {
st.mode = 'element';
const first = docKnownList(ctx, known).nodes[0];
st.elementId = first ? first.id : null;
}
if (typeof p.mode === 'string' && DOC_MODES.includes(p.mode)) st.mode = p.mode;
return st;
}
function docKnownList(ctx, knownIds) {
const known = knownIds || docKnownIds(ctx);
const row = (id, title) => ({ id, title: typeof title === 'string' && title ? title : id });
return {
requirements: ctx.requirements.filter((r) => known.has(r.id)).map((r) => row(r.id, r.title)),
cases: ctx.cases.filter((c) => known.has(c.caseId)).map((c) => row(c.caseId, c.title)),
nodes: ctx.nodes.filter((n) => known.has(n.id)).map((n) => row(n.id, n.name)),
edges: ctx.edges.filter((e) => known.has(e.id)).map((e) => row(e.id, e.label)),
};
}
function docIds(state, ctx) {
const known = docKnownIds(ctx);
if (state.mode === 'requirement') return state.reqId && known.has(state.reqId) ? [state.reqId] : [];
if (state.mode === 'element') return state.elementId && known.has(state.elementId) ? [state.elementId] : [];
return asList(state.ids).filter((id) => typeof id === 'string' && known.has(id));
}
function docText(ctx, state) {
const ds = docDataset(ctx);
const ids = docIds(state, ctx);
if (!ds) return { ok: false, text: '', ids, level: state.level, bytes: 0, lines: 0, error: 'no fragments' }; // diag: author-facing
try {
const text = assemble(ds, state.level, ids);
return { ok: true, text, ids, level: state.level, bytes: new TextEncoder().encode(text).length, lines: text.split('\n').length - 1, error: null };
} catch (e) {
return { ok: false, text: '', ids, level: state.level, bytes: 0, lines: 0, error: String(e && e.message ? e.message : e) };
}
}
function docCheckAll(ctx) {
const ds = docDataset(ctx);
const hooks = isRecord(ctx.bundle.calibrationHooks) ? ctx.bundle.calibrationHooks : {};
return asList(hooks.docSelections).filter(isRecord).map((s) => {
const ids = asList(s.elementIds).filter((x) => typeof x === 'string');
let actual = null;
if (ds && DOC_LEVELS.includes(s.level)) {
try { actual = hash32Hex(assemble(ds, s.level, ids)); } catch (e) { actual = null; }
}
return { level: String(s.level), count: ids.length, expected: typeof s.hash32 === 'string' ? s.hash32 : null, actual, ok: actual !== null && actual === s.hash32 };
});
}
function docOutline(text) {
const out = []; const lines = String(text).split('\n');
for (let i = 0; i < lines.length; i++) {
const m = DOC_CHAPTER_HEAD.exec(lines[i]);
if (m) out.push({ n: Number(m[1]), title: m[2], line: i });
}
return out;
}
const docSetLevel = (st, level) => (DOC_LEVELS.includes(level) ? { ...st, level } : st);
const docSetMode = (st, mode) => (DOC_MODES.includes(mode) ? { ...st, mode } : st);
const docSetRequirement = (st, reqId) => ({ ...st, mode: 'requirement', reqId });
const docSetElement = (st, elementId) => ({ ...st, mode: 'element', elementId });
const docSetFilter = (st, filter) => ({ ...st, filter: String(filter).slice(0, 200) });
function docToggle(st, id) {
const has = st.ids.includes(id);
return { ...st, mode: 'free', ids: has ? st.ids.filter((x) => x !== id) : [...st.ids, id] };
}
const docSetAll = (st, ids) => ({ ...st, mode: 'free', ids: [...ids] });
const docClear = (st) => ({ ...st, ids: [] });
function docFilterRows(rows, filter) {
const words = String(filter || '').normalize('NFKC').toLowerCase().split(/\s+/).filter(Boolean);
if (!words.length) return rows;
return rows.filter((r) => { const hay = (r.id + ' ' + r.title).normalize('NFKC').toLowerCase(); return words.every((w) => hay.includes(w)); });
}
return { DOC_MODES, DOC_LEVELS, docDataset, docKnownIds, docInitial, docKnownList, docIds, docText, docCheckAll, docOutline, docSetLevel, docSetMode, docSetRequirement, docSetElement, docSetFilter, docToggle, docSetAll, docClear, docFilterRows };
})();
// ---- ui/dom.js
const __mod_ui_dom = (function () {
'use strict';
const DOM_SVG_NS = 'http://www.w3.org/2000/svg';
const domRoot = () => globalThis;
const domDoc = () => domRoot().document;
function domApplyAttrs(el, attrs) {
if (!attrs) return el;
for (const key of Object.keys(attrs)) {
const v = attrs[key];
if (v === undefined || v === null || v === false) continue;
if (key === 'class') el.setAttribute('class', String(v));
else if (key.startsWith('on') && typeof v === 'function') el.addEventListener(key.slice(2), v);
else if (key === 'text') el.appendChild(domDoc().createTextNode(String(v)));
else el.setAttribute(key, v === true ? '' : String(v));
}
return el;
}
function domAppend(el, kids) {
for (const k of kids.flat(Infinity)) {
if (k === undefined || k === null || k === false) continue;
el.appendChild(typeof k === 'object' ? k : domDoc().createTextNode(String(k)));
}
return el;
}
function domEl(tag, attrs, ...kids) { return domAppend(domApplyAttrs(domDoc().createElement(tag), attrs), kids); }
function domSvg(tag, attrs, ...kids) { return domAppend(domApplyAttrs(domDoc().createElementNS(DOM_SVG_NS, tag), attrs), kids); }
const domText = (s) => domDoc().createTextNode(String(s));
function domClear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
const domById = (id) => domDoc().getElementById(id);
const domQuery = (sel, from) => (from || domDoc()).querySelector(sel);
const domQueryAll = (sel, from) => Array.from((from || domDoc()).querySelectorAll(sel));
const domBody = () => domDoc().body;
function domOn(el, type, fn, opts) { el.addEventListener(type, fn, opts); return () => el.removeEventListener(type, fn, opts); }
const domSet = (el, name, value) => { if (value === null || value === undefined || value === false) el.removeAttribute(name); else el.setAttribute(name, value === true ? '' : String(value)); return el; };
const domGet = (el, name) => el.getAttribute(name);
const domClassOn = (el, name, on) => { el.classList.toggle(name, !!on); return el; };
const domHasClass = (el, name) => !!(el && el.classList && el.classList.contains(name));
const domFocus = (el) => { if (el && el.focus) el.focus(); };
const domScrollTop = (el, y) => { if (el) el.scrollTop = y; };
function domViewport() {
const w = domRoot();
return { w: Math.round(w.innerWidth || 0), h: Math.round(w.innerHeight || 0) };
}
const domPrefersDark = () => !!(domRoot().matchMedia && domRoot().matchMedia('(prefers-color-scheme: dark)').matches);
const domNavigatorLanguage = () => (domRoot().navigator && domRoot().navigator.language) || '';
function domStoreGet(key) {
try { return domRoot().localStorage.getItem(key); } catch (e) { return null; }
}
function domStoreSet(key, value) {
try { domRoot().localStorage.setItem(key, value); return true; } catch (e) { return false; }
}
const domHash = () => (domRoot().location && domRoot().location.hash) || '';
const domQueryString = () => (domRoot().location && domRoot().location.search) || '';
function domSetHash(hash) {
const w = domRoot();
try {
if (w.history && w.history.replaceState) w.history.replaceState(null, '', hash);
else w.location.hash = hash;
} catch (e) { /* a sandboxed page may refuse; the hash is only a convenience */ }
}
function domOnHash(fn) { domRoot().addEventListener('hashchange', fn); return () => domRoot().removeEventListener('hashchange', fn); }
const domOnKey = (fn) => domOn(domDoc(), 'keydown', fn);
const domOnResize = (fn) => domOn(domRoot(), 'resize', fn);
function domCopy(text) {
const w = domRoot();
try {
if (w.navigator && w.navigator.clipboard && w.navigator.clipboard.writeText) { w.navigator.clipboard.writeText(text).catch(() => {}); return true; }
} catch (e) { /* fall through to the textarea path */ }
try {
const ta = domEl('textarea', { 'aria-hidden': 'true', style: 'position:fixed;left:-9999px;top:0' });
ta.value = text; domDoc().body.appendChild(ta); ta.select();
const ok = domDoc().execCommand && domDoc().execCommand('copy');
domDoc().body.removeChild(ta);
return !!ok;
} catch (e) { return false; }
}
const domLater = (fn, ms) => domRoot().setTimeout(fn, ms);
function domSlotArray(name) {
const w = domRoot();
if (!Array.isArray(w[name])) w[name] = [];
return w[name];
}
function domExpose(name, value) { domRoot()[name] = value; return value; }
const domReadSlot = (name) => domRoot()[name];
function domCatchErrors(sink) {
const w = domRoot();
w.addEventListener('error', (ev) => sink({ where: 'window', message: String((ev && ev.message) || 'error') }));
w.addEventListener('unhandledrejection', (ev) => sink({ where: 'promise', message: String((ev && ev.reason && ev.reason.message) || (ev && ev.reason) || 'rejection') }));
}
function domFocusState() {
const a = domDoc().activeElement;
if (!a || !a.getAttribute || !a.getAttribute('data-keep')) return null;
const s = { keep: a.getAttribute('data-keep'), start: null, end: null };
try { s.start = a.selectionStart; s.end = a.selectionEnd; } catch (e) { /* not a text control */ }
return s;
}
function domRestoreFocus(state) {
if (!state) return;
const el = domDoc().querySelector('[data-keep="' + String(state.keep).replace(/"/g, '') + '"]');
if (!el) return;
el.focus();
try { if (state.start !== null && state.start !== undefined) el.setSelectionRange(state.start, state.end); } catch (e) { /* not a text control */ }
}
function domRootAttrs(map) {
const el = domDoc().documentElement;
for (const k of Object.keys(map)) domSet(el, k, map[k]);
}
function domSetTitle(text) { domDoc().title = String(text); }
function domRect(el) {
const r = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
return r ? { left: r.left || 0, top: r.top || 0, width: r.width || 0, height: r.height || 0 } : { left: 0, top: 0, width: 0, height: 0 };
}
function domBoxes(selector) {
return domQueryAll(selector).map((el) => {
const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
return r && r.width > 0 && r.height > 0 ? { text: String(el.textContent || ''), left: r.left, top: r.top, right: r.right, bottom: r.bottom } : null;
}).filter(Boolean);
}
function domAtScale(svg, scale, fn) {
const r = domRect(svg); const vb = svg.getAttribute('viewBox'); const low = domHasClass(svg, 'lod-low');
const p = String(vb || '').split(/\s+/).map(Number);
try {
if (r.width > 0 && r.height > 0 && scale > 0 && p.length === 4 && p.every(Number.isFinite)) svg.setAttribute('viewBox', [p[0], p[1], r.width / scale, r.height / scale].join(' '));
domClassOn(svg, 'lod-low', false);
return fn();
} finally {
if (vb === null) svg.removeAttribute('viewBox'); else svg.setAttribute('viewBox', vb);
domClassOn(svg, 'lod-low', low);
}
}
function domBrokenTokens(selectors, maxLen) {
const out = [];
for (const el of domQueryAll(selectors.join(','))) {
const text = String(el.textContent || '').trim();
if (!text || text.length > maxLen || /\s/.test(text)) continue;
try {
const range = domDoc().createRange(); range.selectNodeContents(el);
const tops = new Set(Array.from(range.getClientRects()).filter((r) => r.width > 0).map((r) => Math.round(r.top)));
if (tops.size > 1) out.push(text);
} catch (e) { /* a stand-in without ranges has no layout to check */ }
}
return out;
}
function domScrollToId(id) {
const el = domDoc().getElementById(id);
if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
}
function domCapture(el, pointerId) { try { el.setPointerCapture(pointerId); } catch (e) { /* not supported */ } }
function domRelease(el, pointerId) { try { el.releasePointerCapture(pointerId); } catch (e) { /* not captured */ } }
function domFrame(fn) {
const w = domRoot();
return typeof w.requestAnimationFrame === 'function' ? w.requestAnimationFrame(fn) : 0;
}
function domCancelFrame(id) {
const w = domRoot();
if (id && typeof w.cancelAnimationFrame === 'function') w.cancelAnimationFrame(id);
}
const domNow = () => { const p = domRoot().performance; return p && typeof p.now === 'function' ? p.now() : 0; };
const domPixelRatio = () => Math.max(1, Math.min(2, Number(domRoot().devicePixelRatio) || 1));
function domStillMode() {
const w = domRoot();
try { if (w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)').matches) return true; } catch (e) { /* no media queries */ }
const gate = w.AtlasGate;
return !!(gate && gate.active);
}
function domGlContext(canvas) {
const attrs = { antialias: true, alpha: false, depth: true, preserveDrawingBuffer: false };
for (const kind of ['webgl2', 'webgl']) {
try { const gl = canvas.getContext(kind, attrs); if (gl) return gl; } catch (e) { /* try the next kind */ }
}
return null;
}
function domCanvasPixels(canvas, w, h) {
if (canvas.width === w && canvas.height === h) return false;
canvas.width = w; canvas.height = h;
return true;
}
function domStyleText(el, text) { el.setAttribute('style', String(text)); return el; }
return { domEl, domSvg, domText, domClear, domById, domQuery, domQueryAll, domBody, domOn, domSet, domGet, domClassOn, domHasClass, domFocus, domScrollTop, domViewport, domPrefersDark, domNavigatorLanguage, domStoreGet, domStoreSet, domHash, domQueryString, domSetHash, domOnHash, domOnKey, domOnResize, domCopy, domLater, domSlotArray, domExpose, domReadSlot, domCatchErrors, domFocusState, domRestoreFocus, domRootAttrs, domSetTitle, domRect, domBoxes, domAtScale, domBrokenTokens, domScrollToId, domCapture, domRelease, domFrame, domCancelFrame, domNow, domPixelRatio, domStillMode, domGlContext, domCanvasPixels, domStyleText };
})();
// ---- ui/gate-checks.js
const __mod_ui_gate_checks = (function () {
'use strict';
const GATE_TOKEN_MAX = 40; // a table cell this short with no space is one word: it must stay on one line
const GATE_OVERLAP_RATIO = 0.1; // overlap depth, as a share of the smaller box height, beyond which two text boxes collide
const GATE_ALLOW_CONTEXT_MIN = 4; // characters of context a listed snippet needs besides the token itself
const GATE_BAD_TEXT = /undefined|NaN|\[object/g; // the tokens the browser gate scans the page text for (selftest.js)
function gateTextAllow(data, limit = 120, visitLimit = 400000) {
const out = new Set(); let seen = 0;
const visit = (v) => {
if (out.size >= limit * 4 || seen++ > visitLimit) return;
if (typeof v === 'string') {
const re = new RegExp(GATE_BAD_TEXT.source, 'g'); let m = re.exec(v);
while (m && out.size < limit * 4) {
const at = m.index; const end = at + m[0].length;
for (const [a, b] of [[12, 12], [12, 0], [0, 12], [4, 4]]) {
const snip = v.slice(Math.max(0, at - a), end + b);
if (snip.length >= m[0].length + GATE_ALLOW_CONTEXT_MIN) out.add(snip); // a bare token would exempt every use of the word on the page
}
m = re.exec(v);
}
} else if (Array.isArray(v)) { for (const x of v) visit(x); }
else if (v && typeof v === 'object') { for (const k of Object.keys(v)) { visit(k); visit(v[k]); } }
};
if (data && typeof data === 'object' && !Array.isArray(data)) { for (const k of Object.keys(data)) { if (k !== 'i18n') visit(data[k]); } }
return Array.from(out);
}
function gateOverlaps(boxes, ratio = GATE_OVERLAP_RATIO) {
const hits = [];
for (let i = 0; i < boxes.length; i++) {
for (let j = i + 1; j < boxes.length; j++) {
const a = boxes[i]; const b = boxes[j];
const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
const min = Math.min(a.bottom - a.top, b.bottom - b.top);
if (w > 0.2 && h > Math.max(0.2, ratio * min)) hits.push([a, b]);
}
}
return hits;
}
return { GATE_TOKEN_MAX, GATE_OVERLAP_RATIO, GATE_ALLOW_CONTEXT_MIN, GATE_BAD_TEXT, gateTextAllow, gateOverlaps };
})();
// ---- ui/gl-renderer.js
const __mod_ui_gl_renderer = (function () {
'use strict';
const GL_THEMES = { dark: { bg: [0.035, 0.05, 0.11] }, light: { bg: [0.96, 0.96, 0.95] } };
const GL_LIGHT = [-0.4, 0.9, -0.5];
const GL_SOFTWARE = /swiftshader|software|microsoft basic/i;
const GL_VS_MESH = 'attribute vec3 aPos;attribute vec3 aNormal;attribute vec3 aOff;attribute float aScl;attribute vec4 aTint;uniform mat4 uVP;varying vec3 vN;varying vec4 vC;varying vec3 vW;void main(){vec3 w=aPos*aScl+aOff;gl_Position=uVP*vec4(w,1.0);vN=aNormal;vC=aTint;vW=w;}'; // diag: shader source
const GL_FS_MESH = 'precision mediump float;uniform vec3 uL;uniform vec3 uE;varying vec3 vN;varying vec4 vC;varying vec3 vW;void main(){vec3 n=normalize(vN);vec3 v=normalize(uE-vW);if(dot(n,v)<0.0)n=-n;float d=max(dot(n,normalize(uL)),0.0);float f=pow(1.0-max(dot(n,v),0.0),2.0);float s=pow(max(dot(reflect(-normalize(uL),n),v),0.0),24.0);vec3 c=vC.rgb*(0.5+0.5*d)+vec3(0.55,0.75,1.0)*f*0.45+vec3(1.0)*s*0.6;gl_FragColor=vec4(c,vC.a*(0.42+0.5*f+0.2*d));}'; // diag: shader source
const GL_VS_LINE = 'attribute vec3 aPos;attribute vec4 aTint;uniform mat4 uVP;varying vec4 vC;void main(){gl_Position=uVP*vec4(aPos,1.0);vC=aTint;}'; // diag: shader source
const GL_FS_LINE = 'precision mediump float;varying vec4 vC;void main(){gl_FragColor=vC;}'; // diag: shader source
const GL_VS_DOT = 'attribute vec3 aPos;attribute vec4 aTint;attribute vec2 aCorner;uniform mat4 uVP;uniform float uPS;uniform vec2 uView;varying vec4 vC;varying vec2 vQ;void main(){vec4 p=uVP*vec4(aPos,1.0);p.xy+=aCorner*uPS/uView*p.w;gl_Position=p;vC=aTint;vQ=aCorner;}'; // diag: shader source
const GL_FS_DOT = 'precision mediump float;varying vec4 vC;varying vec2 vQ;void main(){float r=length(vQ);if(r>1.0)discard;float c=pow(1.0-r,1.6);gl_FragColor=vec4(mix(vC.rgb,vec3(1.0),c*0.35),clamp(c*1.2,0.0,1.0)*vC.a);}'; // diag: shader source
const GL_VS_HALO = 'attribute vec3 aPos;attribute vec4 aTint;attribute vec2 aSz;attribute vec2 aCorner;uniform mat4 uVP;uniform float uK;uniform float uT;uniform vec2 uView;varying vec4 vC;varying float vP;varying vec2 vQ;void main(){vec4 p=uVP*vec4(aPos,1.0);float br=0.88+0.12*sin(uT*3.0+aSz.y);float px=max(2.0,aSz.x*br*uK/max(p.w,0.1));p.xy+=aCorner*px/uView*p.w;gl_Position=p;vC=aTint;vP=uT*1.4+aSz.y*0.37;vQ=aCorner;}'; // diag: shader source
const GL_FS_HALO = 'precision mediump float;varying vec4 vC;varying float vP;varying vec2 vQ;void main(){float r=length(vQ);if(r>1.0)discard;float core=pow(1.0-r,2.2);float ph=vP-floor(vP);float ring=exp(-pow((r-ph)*7.0,2.0))*(1.0-ph);float a=clamp((core*0.75+ring*0.6)*vC.a,0.0,1.0);gl_FragColor=vec4(mix(vC.rgb,vec3(1.0),core*0.15),a);}'; // diag: shader source
function glCompile(gl, type, src) {
const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('shader: ' + gl.getShaderInfoLog(s));
return s;
}
function glProgram(gl, vs, fs) {
const p = gl.createProgram(); gl.attachShader(p, glCompile(gl, gl.VERTEX_SHADER, vs)); gl.attachShader(p, glCompile(gl, gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('link: ' + gl.getProgramInfoLog(p));
return p;
}
function glInit(gl) {
const pm = glProgram(gl, GL_VS_MESH, GL_FS_MESH); const pl = glProgram(gl, GL_VS_LINE, GL_FS_LINE); const pd = glProgram(gl, GL_VS_DOT, GL_FS_DOT); const ph = glProgram(gl, GL_VS_HALO, GL_FS_HALO);
const at = (p, n) => gl.getAttribLocation(p, n); const un = (p, n) => gl.getUniformLocation(p, n);
const isTwo = typeof gl.drawArraysInstanced === 'function'; // only a WebGL 2 context has instancing built in
const ext = isTwo ? null : gl.getExtension('ANGLE_instanced_arrays');
const instancing = isTwo ? 'webgl2' : ext ? 'extension' : 'none';
const dbg = gl.getExtension('WEBGL_debug_renderer_info');
const renderer = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
const names = ['pos', 'nrm', 'off', 'scl', 'col', 'line', 'pt'];
const buf = {}; for (const n of names) buf[n] = gl.createBuffer();
return {
gl, pm, pl, pd, ph, buf, instancing,
divisor: isTwo ? (loc, d) => gl.vertexAttribDivisor(loc, d) : ext ? (loc, d) => ext.vertexAttribDivisorANGLE(loc, d) : () => {},
drawInstanced: isTwo ? (mode, n, count) => gl.drawArraysInstanced(mode, 0, n, count) : ext ? (mode, n, count) => ext.drawArraysInstancedANGLE(mode, 0, n, count) : null,
loc: {
mesh: { aPos: at(pm, 'aPos'), aNormal: at(pm, 'aNormal'), aOff: at(pm, 'aOff'), aScl: at(pm, 'aScl'), aTint: at(pm, 'aTint'), uVP: un(pm, 'uVP'), uL: un(pm, 'uL'), uE: un(pm, 'uE') },
line: { aPos: at(pl, 'aPos'), aTint: at(pl, 'aTint'), uVP: un(pl, 'uVP') },
dot: { aPos: at(pd, 'aPos'), aTint: at(pd, 'aTint'), aCorner: at(pd, 'aCorner'), uVP: un(pd, 'uVP'), uPS: un(pd, 'uPS'), uView: un(pd, 'uView') },
halo: { aPos: at(ph, 'aPos'), aTint: at(ph, 'aTint'), aSz: at(ph, 'aSz'), aCorner: at(ph, 'aCorner'), uVP: un(ph, 'uVP'), uK: un(ph, 'uK'), uT: un(ph, 'uT'), uView: un(ph, 'uView') },
},
info: { renderer, version: String(gl.getParameter(gl.VERSION)), software: GL_SOFTWARE.test(renderer), instancing },
};
}
const GL_CORNERS = [[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]];
function glHaloQuads(h, count) {
const out = new Float32Array(count * 6 * 11); let o = 0;
for (let i = 0; i < count; i++) {
const k = i * 9;
for (const c of GL_CORNERS) { for (let m = 0; m < 9; m++) out[o++] = h[k + m]; out[o++] = c[0]; out[o++] = c[1]; }
}
return out;
}
function glDotQuads(p, count) {
const out = new Float32Array(count * 6 * 9); let o = 0;
for (let i = 0; i < count; i++) {
const k = i * 7;
for (const c of GL_CORNERS) { for (let m = 0; m < 7; m++) out[o++] = p[k + m]; out[o++] = c[0]; out[o++] = c[1]; }
}
return out;
}
const glUpload = (rs, buffer, data, loc, size, stride = 0, offset = 0) => {
const gl = rs.gl;
gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset);
};
function glReset(rs) {
const gl = rs.gl;
for (let i = 0; i < 8; i++) { gl.disableVertexAttribArray(i); rs.divisor(i, 0); }
}
function glDraw(rs, f) {
const gl = rs.gl; const L = rs.loc; const b = f.buffers; const th = GL_THEMES[f.theme] || GL_THEMES.dark;
let calls = 0;
gl.viewport(0, 0, f.width, f.height);
gl.clearColor(th.bg[0], th.bg[1], th.bg[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE);
gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
glReset(rs);
gl.useProgram(rs.pm); gl.depthMask(false);
gl.uniformMatrix4fv(L.mesh.uVP, false, f.vp); gl.uniform3f(L.mesh.uL, GL_LIGHT[0], GL_LIGHT[1], GL_LIGHT[2]); gl.uniform3fv(L.mesh.uE, f.eye);
if (rs.drawInstanced && f.groups) {
for (const g of f.groups) {
if (!g.count) continue;
glReset(rs);
glUpload(rs, rs.buf.pos, g.template.pos, L.mesh.aPos, 3); glUpload(rs, rs.buf.nrm, g.template.nrm, L.mesh.aNormal, 3);
glUpload(rs, rs.buf.off, g.offset, L.mesh.aOff, 3); rs.divisor(L.mesh.aOff, 1);
glUpload(rs, rs.buf.scl, g.scale, L.mesh.aScl, 1); rs.divisor(L.mesh.aScl, 1);
glUpload(rs, rs.buf.col, g.color, L.mesh.aTint, 4); rs.divisor(L.mesh.aTint, 1);
rs.drawInstanced(gl.TRIANGLES, g.template.faceCount * 3, g.count); calls++;
}
} else if (b.mesh.faceCount) {
glReset(rs);
glUpload(rs, rs.buf.pos, b.mesh.pos, L.mesh.aPos, 3); glUpload(rs, rs.buf.nrm, b.mesh.nrm, L.mesh.aNormal, 3); glUpload(rs, rs.buf.col, b.mesh.col, L.mesh.aTint, 4);
gl.vertexAttrib3f(L.mesh.aOff, 0, 0, 0); gl.vertexAttrib1f(L.mesh.aScl, 1);
gl.drawArrays(gl.TRIANGLES, 0, b.mesh.faceCount * 3); calls++;
}
glReset(rs);
gl.useProgram(rs.pl);
gl.uniformMatrix4fv(L.line.uVP, false, f.vp);
const stride = 7 * 4;
if (b.lineCount) {
glUpload(rs, rs.buf.line, b.lines, L.line.aPos, 3, stride, 0);
gl.enableVertexAttribArray(L.line.aTint); gl.vertexAttribPointer(L.line.aTint, 4, gl.FLOAT, false, stride, 12);
gl.drawArrays(gl.LINES, 0, b.lineCount); calls++;
}
if (b.haloCount) {
glReset(rs);
gl.useProgram(rs.ph); gl.blendFunc(gl.SRC_ALPHA, gl.ONE); gl.disable(gl.DEPTH_TEST);
gl.uniformMatrix4fv(L.halo.uVP, false, f.vp); gl.uniform1f(L.halo.uK, f.vp[5] * f.height * 0.5); gl.uniform1f(L.halo.uT, f.clock); gl.uniform2f(L.halo.uView, f.width, f.height);
const hs = 11 * 4;
glUpload(rs, rs.buf.pt, glHaloQuads(b.halos, b.haloCount), L.halo.aPos, 3, hs, 0);
gl.enableVertexAttribArray(L.halo.aTint); gl.vertexAttribPointer(L.halo.aTint, 4, gl.FLOAT, false, hs, 12);
gl.enableVertexAttribArray(L.halo.aSz); gl.vertexAttribPointer(L.halo.aSz, 2, gl.FLOAT, false, hs, 28);
gl.enableVertexAttribArray(L.halo.aCorner); gl.vertexAttribPointer(L.halo.aCorner, 2, gl.FLOAT, false, hs, 36);
gl.drawArrays(gl.TRIANGLES, 0, b.haloCount * 6); calls++;
gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.enable(gl.DEPTH_TEST);
}
if (b.dotCount) {
glReset(rs);
gl.useProgram(rs.pd); gl.disable(gl.DEPTH_TEST); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
gl.uniformMatrix4fv(L.dot.uVP, false, f.vp); gl.uniform1f(L.dot.uPS, 13 + 3 * Math.sin(f.clock * 8)); gl.uniform2f(L.dot.uView, f.width, f.height);
const ds = 9 * 4;
glUpload(rs, rs.buf.line, glDotQuads(b.points, b.dotCount), L.dot.aPos, 3, ds, 0);
gl.enableVertexAttribArray(L.dot.aTint); gl.vertexAttribPointer(L.dot.aTint, 4, gl.FLOAT, false, ds, 12);
gl.enableVertexAttribArray(L.dot.aCorner); gl.vertexAttribPointer(L.dot.aCorner, 2, gl.FLOAT, false, ds, 28);
gl.drawArrays(gl.TRIANGLES, 0, b.dotCount * 6); calls++;
gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.enable(gl.DEPTH_TEST);
}
gl.depthMask(true);
glReset(rs);
return calls;
}
return { GL_THEMES, GL_LIGHT, glInit, glHaloQuads, glDotQuads, glDraw };
})();
// ---- ui/i18n.js
const __mod_ui_i18n = (function () {
'use strict';
const UI_FALLBACK_LANG = 'en-US';
function i18nFill(template, vars) {
const text = typeof template === 'string' ? template : '';
if (!vars) return text;
return text.replace(/\{([A-Za-z0-9_]+)\}/g, (m, name) => (Object.hasOwn(vars, name) && vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : m));
}
function i18nPickLang(languages, wanted) {
const have = Array.isArray(languages) ? languages.filter((l) => typeof l === 'string') : [];
for (const w of wanted) {
if (typeof w !== 'string' || !w) continue;
if (have.includes(w)) return w;
const base = w.split('-')[0].toLowerCase();
const near = have.find((l) => l.split('-')[0].toLowerCase() === base);
if (near) return near;
}
return have.includes(UI_FALLBACK_LANG) ? UI_FALLBACK_LANG : (have[0] || UI_FALLBACK_LANG);
}
function i18nLabel(entry, lang, fallbackId) {
const label = entry && typeof entry.label === 'object' && entry.label ? entry.label : null;
const pick = (l) => (label && typeof label[l] === 'string' && label[l] ? label[l] : null);
return pick(lang) || pick(UI_FALLBACK_LANG) || (entry && typeof entry.id === 'string' ? entry.id : String(fallbackId === undefined ? '' : fallbackId));
}
function makeI18n(tables, lang, sink) {
const own = (l) => (tables && typeof tables === 'object' && tables[l] && typeof tables[l] === 'object' ? tables[l] : {});
const active = own(lang); const fallback = own(UI_FALLBACK_LANG); const missing = new Set();
const read = (key) => (Object.hasOwn(active, key) && typeof active[key] === 'string' ? active[key] : Object.hasOwn(fallback, key) && typeof fallback[key] === 'string' ? fallback[key] : null);
return {
lang,
t(key, vars) {
if (typeof key !== 'string') return '';
const v = read(key);
if (v === null) {
if (!missing.has(key)) { missing.add(key); if (sink) sink({ where: 'i18n', message: 'missing key ' + key }); } // diag: author-facing
return String(key);
}
return i18nFill(v, vars);
},
has: (key) => read(key) !== null,
label: (entry, fallbackId) => i18nLabel(entry, lang, fallbackId),
keys: () => Object.keys(active),
};
}
return { UI_FALLBACK_LANG, i18nFill, i18nPickLang, i18nLabel, makeI18n };
})();
// ---- ui/model-view.js
const __mod_ui_model_view = (function () {
'use strict';
const { makeVocab, makeWarnings, indexModel, asList, isRecord, own, nodesOf, edgesOf, casesOf } = __mod_model_access;
const viewRecords = (v, idField) => asList(v).filter((x) => isRecord(x) && (idField === null || typeof x[idField] === 'string'));
function viewCleanModel(model) {
const m = isRecord(model) ? model : {};
const space = isRecord(m.space) ? m.space : {};
return {
...m, nodes: viewRecords(m.nodes, 'id'), edges: viewRecords(m.edges, 'id'), cases: viewRecords(m.cases, 'caseId'), requirements: viewRecords(m.requirements, 'id'),
interventions: viewRecords(m.interventions, null), tbds: viewRecords(m.tbds, null), evidences: viewRecords(m.evidences, 'evidenceId'), machines: viewRecords(m.machines, 'id'),
ports: viewRecords(m.ports, 'id'), space: { ...space, buildings: viewRecords(space.buildings, 'id'), floors: viewRecords(space.floors, 'id'), rooms: viewRecords(space.rooms, 'id') },
};
}
function viewContext(bundle) {
const b = isRecord(bundle) ? bundle : {};
const meta = isRecord(b.meta) ? b.meta : {};
const model = viewCleanModel(b.model);
const warnings = makeWarnings();
const vocab = makeVocab(isRecord(b.profile) ? b.profile : null, warnings);
const ix = indexModel(model);
const runs = isRecord(b.runs) ? b.runs : {};
const baselines = isRecord(runs.baselines) ? runs.baselines : {};
const languages = asList(meta.languages).filter((l) => typeof l === 'string');
const requirements = model.requirements;
const interventions = model.interventions;
const cases = casesOf(model);
const diagrams = asList(b.diagrams).filter(isRecord);
return {
bundle: b, meta, model, vocab, ix, warnings, runs, diagrams,
languages, primaryLanguage: typeof meta.primaryLanguage === 'string' ? meta.primaryLanguage : (languages[0] || 'en-US'),
levels: vocab.levels,
nodes: nodesOf(model), edges: edgesOf(model), cases, requirements, interventions,
tbds: model.tbds,
sealedCaseIds: Object.keys(baselines),
pairs: asList(runs.pairs).filter(isRecord),
hasCases: cases.length > 0,
hasInterventions: interventions.length > 0,
hasSealed: Object.keys(baselines).length > 0,
};
}
function viewElementKind(ctx, id) {
if (ctx.ix.nodes.has(id)) return 'node';
if (ctx.ix.edges.has(id)) return 'edge';
if (ctx.ix.cases.has(id)) return 'case';
if (ctx.requirements.some((r) => r.id === id)) return 'requirement';
return null;
}
function viewElementTitle(ctx, id) {
const n = ctx.ix.nodes.get(id);
if (n) return typeof n.name === 'string' && n.name ? n.name : id;
const e = ctx.ix.edges.get(id);
if (e) {
if (typeof e.label === 'string' && e.label) return e.label;
const a = ctx.ix.nodes.get(e.sourceId); const z = ctx.ix.nodes.get(e.targetId);
return (a ? a.name : e.sourceId) + ' > ' + (z ? z.name : e.targetId);
}
const c = ctx.ix.cases.get(id);
if (c) return typeof c.title === 'string' && c.title ? c.title : id;
const r = ctx.requirements.find((x) => x.id === id);
if (r) return typeof r.title === 'string' && r.title ? r.title : id;
return String(id);
}
function viewVocabLabel(ctx, collection, id, lang) {
const entry = asList(ctx.vocab.profile[collection]).find((e) => isRecord(e) && e.id === id);
const label = entry && isRecord(entry.label) ? entry.label : null;
const pick = (l) => (label && typeof own(label, l) === 'string' && own(label, l) ? own(label, l) : null);
return pick(lang) || pick('en-US') || String(id);
}
function viewCountBy(list, field) {
const m = new Map();
for (const x of list) {
const v = isRecord(x) && typeof x[field] === 'string' && x[field] ? x[field] : '';
m.set(v, (m.get(v) || 0) + 1);
}
return [...m.entries()].map(([id, count]) => ({ id, count }));
}
function viewHasSelection(ctx, sel) {
if (!isRecord(sel) || typeof sel.id !== 'string') return false;
if (sel.type === 'node') return ctx.ix.nodes.has(sel.id);
if (sel.type === 'edge') return ctx.ix.edges.has(sel.id);
if (sel.type === 'case') return ctx.ix.cases.has(sel.id);
if (sel.type === 'requirement') return ctx.requirements.some((r) => r.id === sel.id);
return false;
}
return { viewCleanModel, viewContext, viewElementKind, viewElementTitle, viewVocabLabel, viewCountBy, viewHasSelection };
})();
// ---- ui/inspector.js
const __mod_ui_inspector = (function () {
'use strict';
const { asList, isRecord, casePlayers, incident, homeOf } = __mod_model_access;
const { viewElementTitle } = __mod_ui_model_view;
const INSP_MAX_ROWS = 60;
const inspStr = (v) => (typeof v === 'string' ? v : v === undefined || v === null ? '' : typeof v === 'object' ? '' : String(v));
function inspEvidenceRows(ctx, ids) {
return asList(ids).slice(0, INSP_MAX_ROWS).map((id) => {
const e = ctx.ix.evidences.get(id);
if (!e) return { id: String(id), file: '', anchor: '', range: '', topic: '', grade: '', known: false };
const r = Array.isArray(e.lineRange) && e.lineRange.length === 2 ? e.lineRange[0] + '-' + e.lineRange[1] : '';
return { id: String(id), file: inspStr(e.filePath), anchor: inspStr(e.anchor), range: r, topic: inspStr(e.topic), grade: inspStr(e.verification), known: true };
});
}
const inspNodeLink = (ctx, id) => ({ type: 'node', id, title: viewElementTitle(ctx, id) });
const inspPropRows = (props) => (isRecord(props) ? Object.keys(props).filter((k) => ['string', 'number', 'boolean'].includes(typeof props[k])).slice(0, INSP_MAX_ROWS).map((k) => ({ key: k, value: String(props[k]) })) : []);
function inspNode(ctx, n) {
const home = homeOf(ctx.ix, n.id);
const named = (map, id) => { const x = map.get(id); return x ? inspStr(x.name) || inspStr(x.id) : ''; };
const place = home ? [named(ctx.ix.buildings, home.buildingId), named(ctx.ix.floors, home.floorId), named(ctx.ix.rooms, home.roomId)].filter(Boolean).join(' / ') : '';
const links = incident(ctx.ix, n.id).slice(0, INSP_MAX_ROWS).map((e) => {
const out = e.sourceId === n.id; const other = out ? e.targetId : e.sourceId;
return { type: 'edge', id: e.id, title: viewElementTitle(ctx, e.id), dir: out ? 'out' : 'in', relation: inspStr(e.relation), other: inspNodeLink(ctx, other) };
});
const cases = ctx.cases.filter((c) => asList(c.events).some((ev) => ev && (ev.from === n.id || ev.to === n.id))).slice(0, INSP_MAX_ROWS).map((c) => ({ type: 'case', id: c.caseId, title: viewElementTitle(ctx, c.caseId) }));
const reqs = ctx.requirements.filter((r) => asList(r.nodeIds).includes(n.id)).slice(0, INSP_MAX_ROWS).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
return {
type: 'node', id: n.id, title: inspStr(n.name) || n.id,
fields: [{ key: 'id', value: n.id }, { key: 'kind', value: inspStr(n.kind), vocab: 'kinds' }, { key: 'summary', value: inspStr(n.sub) }, { key: 'grade', value: inspStr(n.verification), vocab: 'verification' }, { key: 'placement', value: place }, { key: 'trust', value: inspStr(n.props && n.props.trust), vocab: 'trust' }],
props: inspPropRows(n.props), evidences: inspEvidenceRows(ctx, n.evidenceIds), links, cases, requirements: reqs,
};
}
function inspEdge(ctx, e) {
const reqs = ctx.requirements.filter((r) => asList(r.edgeIds).includes(e.id)).slice(0, INSP_MAX_ROWS).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
return {
type: 'edge', id: e.id, title: viewElementTitle(ctx, e.id),
fields: [{ key: 'id', value: e.id }, { key: 'relation', value: inspStr(e.relation), vocab: 'relations' }, { key: 'label', value: inspStr(e.label) }, { key: 'grade', value: inspStr(e.verification), vocab: 'verification' }, { key: 'guard', value: inspStr(e.guardId) }],
props: [], evidences: inspEvidenceRows(ctx, e.evidenceIds), cases: [], requirements: reqs,
links: [{ type: 'node', id: e.sourceId, title: viewElementTitle(ctx, e.sourceId), dir: 'from' }, { type: 'node', id: e.targetId, title: viewElementTitle(ctx, e.targetId), dir: 'to' }],
};
}
function inspCase(ctx, c) {
const players = [...casePlayers(ctx.ix, c.caseId)];
const reqs = ctx.requirements.filter((r) => asList(c.requirementIds).includes(r.id) || asList(r.caseIds).includes(c.caseId)).map((r) => ({ type: 'requirement', id: r.id, title: viewElementTitle(ctx, r.id) }));
return {
type: 'case', id: c.caseId, title: inspStr(c.title) || c.caseId,
fields: [{ key: 'id', value: c.caseId }, { key: 'category', value: inspStr(c.category) }, { key: 'events', value: String(asList(c.events).length) }, { key: 'sealed', value: '', flag: ctx.sealedCaseIds.includes(c.caseId) }],
props: [], evidences: inspEvidenceRows(ctx, c.evidenceIds), links: players.slice(0, INSP_MAX_ROWS).map((id) => ({ ...inspNodeLink(ctx, id), dir: 'player' })), cases: [], requirements: reqs,
};
}
function inspRequirement(ctx, r) {
return {
type: 'requirement', id: r.id, title: inspStr(r.title) || r.id,
fields: [{ key: 'id', value: r.id }, { key: 'source', value: inspStr(r.source) }, { key: 'tests', value: asList(r.tstIds).map(inspStr).join(', ') }],
props: [], evidences: inspEvidenceRows(ctx, r.evidenceIds),
links: [...asList(r.nodeIds).slice(0, INSP_MAX_ROWS).map((id) => ({ ...inspNodeLink(ctx, id), dir: 'node' })), ...asList(r.edgeIds).slice(0, INSP_MAX_ROWS).map((id) => ({ type: 'edge', id, title: viewElementTitle(ctx, id), dir: 'edge' }))],
cases: ctx.cases.filter((c) => asList(c.requirementIds).includes(r.id)).map((c) => ({ type: 'case', id: c.caseId, title: viewElementTitle(ctx, c.caseId) })), requirements: [],
};
}
function inspectSelection(ctx, sel) {
if (!isRecord(sel) || typeof sel.id !== 'string') return null;
if (sel.type === 'node') { const n = ctx.ix.nodes.get(sel.id); return n ? inspNode(ctx, n) : null; }
if (sel.type === 'edge') { const e = ctx.ix.edges.get(sel.id); return e ? inspEdge(ctx, e) : null; }
if (sel.type === 'case') { const c = ctx.ix.cases.get(sel.id); return c ? inspCase(ctx, c) : null; }
if (sel.type === 'requirement') { const r = ctx.requirements.find((x) => x.id === sel.id); return r ? inspRequirement(ctx, r) : null; }
return null;
}
return { inspectSelection };
})();
// ---- ui/twod-scene.js
const __mod_ui_twod_scene = (function () {
'use strict';
const { shownAtLevel, keepFocus, relevance, asList, UNASSIGNED_ID } = __mod_model_access;
const { cardLayout, gemOutline } = __mod_layout;
const { estimateTextWidth, truncateText } = __mod_label_fit;
const TWOD_ZOOM_MIN = 0.05;
const TWOD_ZOOM_MAX = 6;
const twodFin = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
function twodRelevanceSelection(ctx, sel) {
if (!sel || typeof sel.type !== 'string') return null;
if (sel.type === 'requirement') {
const r = ctx.requirements.find((x) => x.id === sel.id);
return r ? { type: 'path', ids: asList(r.nodeIds).filter((id) => ctx.ix.nodes.has(id)) } : null;
}
return sel;
}
const twodFold = (s) => String(s === undefined || s === null ? '' : s).normalize('NFKC').toLowerCase();
function twodScene(ctx, opts) {
const o = opts || {};
const level = ctx.levels.includes(o.level) ? o.level : ctx.levels[ctx.levels.length - 1];
const rel = relevance(ctx.ix, twodRelevanceSelection(ctx, o.selection));
const vis = keepFocus(shownAtLevel(ctx.model, level, ctx.vocab, ctx.ix), rel);
const L = cardLayout(ctx.model, vis, { vocab: ctx.vocab, ix: ctx.ix });
const words = twodFold(o.query).split(/\s+/).filter(Boolean);
const matches = (n) => { const hay = twodFold(n.id + ' ' + n.name + ' ' + n.kind); return words.every((w) => hay.includes(w)); };
const cards = L.nodes.map((n) => ({
id: n.id, x: twodFin(n.x, 0), y: twodFin(n.y, 0), w: twodFin(n.w, 0), h: twodFin(n.h, 0), name: typeof n.name === 'string' ? n.name : String(n.id), kind: String(n.kind), color: n.color,
outline: gemOutline(n.shape, 8), tier: rel.nodeTier(n.id), match: words.length === 0 || matches(n),
}));
const edgeRec = new Map(ctx.edges.map((e) => [e.id, e]));
const edges = L.edges.map((e) => ({ id: e.id, d: e.d, color: e.color, dashed: !!e.dashed, static: !!e.static, tier: rel.edgeTier(edgeRec.get(e.id) || { id: e.id, sourceId: e.sourceId, targetId: e.targetId }), sourceId: e.sourceId, targetId: e.targetId, relation: String(e.relation) }));
const b = L.bounds || { x: 0, y: 0, w: 100, h: 100 };
const frame = (r) => ({ id: r.id, name: typeof r.name === 'string' ? r.name : '', x: twodFin(r.x, 0), y: twodFin(r.y, 0), w: twodFin(r.w, 0), h: twodFin(r.h, 0) });
return {
viewBox: { x: twodFin(b.x, 0), y: twodFin(b.y, 0), w: Math.max(1, twodFin(b.w, 100)), h: Math.max(1, twodFin(b.h, 100)) },
buildings: L.buildings.map((r) => ({ ...frame(r), unassigned: !!r.unassigned })), floors: L.floors.map((r) => ({ ...frame(r), buildingId: r.buildingId })), rooms: L.rooms.map((r) => ({ ...frame(r), floorId: r.floorId })),
cards, edges, unassigned: L.unassigned.slice(), active: rel.active,
counts: { cards: cards.length, edges: edges.length, buildings: L.buildings.length, floors: L.floors.length, rooms: L.rooms.length, unassigned: L.unassigned.length, hidden: ctx.nodes.length - cards.length },
};
}
const TWOD_LABEL_SIZE = 11;
const TWOD_LABEL_ASCENT = 9; // box above the baseline, scene units
const TWOD_LABEL_DESCENT = 3;
const TWOD_BUILDING_BASE = 16; const TWOD_FLOOR_BASE = 11; const TWOD_ROOM_BASE = 28;
const TWOD_LABEL_PAD = 8; const TWOD_LABEL_MIN_W = 30;
function twodFrameLabels(scene, unassignedText) {
const out = [];
const add = (kind, id, raw, x, base, maxW) => {
const text = truncateText(raw, Math.max(TWOD_LABEL_MIN_W, maxW), TWOD_LABEL_SIZE);
if (!text) return;
out.push({ kind, id, text, x, y: base, box: { x, y: base - TWOD_LABEL_ASCENT, w: estimateTextWidth(text, TWOD_LABEL_SIZE), h: TWOD_LABEL_ASCENT + TWOD_LABEL_DESCENT } });
};
for (const b of scene.buildings) add('building', b.id, b.unassigned ? unassignedText : b.name, b.x + TWOD_LABEL_PAD, b.y + TWOD_BUILDING_BASE, b.w - 2 * TWOD_LABEL_PAD);
const first = new Map();
for (const r of scene.rooms) {
const cur = first.get(r.floorId);
if (!cur || r.y < cur.y || (r.y === cur.y && r.x < cur.x)) first.set(r.floorId, r);
}
for (const f of scene.floors) {
const r = first.get(f.id);
if (f.id !== UNASSIGNED_ID && r) add('floor', f.id, f.name, r.x + TWOD_LABEL_PAD, r.y + TWOD_FLOOR_BASE, r.w - 2 * TWOD_LABEL_PAD);
}
for (const r of scene.rooms) { if (r.id !== UNASSIGNED_ID) add('room', r.id, r.name, r.x + TWOD_LABEL_PAD, r.y + TWOD_ROOM_BASE, r.w - 2 * TWOD_LABEL_PAD); }
return out;
}
function twodLabelOverlaps(labels, tol = 0.5) {
const hits = [];
for (let i = 0; i < labels.length; i++) {
for (let j = i + 1; j < labels.length; j++) {
const a = labels[i].box; const b = labels[j].box;
if (Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > tol && Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > tol) hits.push([labels[i], labels[j]]);
}
}
return hits;
}
const TWOD_LOD_SCALE = 0.5;
function twodScale(view, paneW, paneH) {
const w = twodFin(paneW, 0); const h = twodFin(paneH, 0);
return w > 0 && h > 0 && view.w > 0 && view.h > 0 ? Math.min(w / view.w, h / view.h) : null;
}
const twodTextHidden = (scale) => typeof scale === 'number' && scale < TWOD_LOD_SCALE;
function twodFit(scene, paneW, paneH) {
const b = scene.viewBox; const aspect = twodFin(paneW, 1) > 0 && twodFin(paneH, 1) > 0 ? paneW / paneH : 1.6;
let w = b.w; let h = b.h;
if (w / h < aspect) w = h * aspect; else h = w / aspect;
return { x: b.x - (w - b.w) / 2, y: b.y - (h - b.h) / 2, w, h };
}
function twodZoom(view, fit, factor, fx, fy) {
const f = twodFin(factor, 1) > 0 ? twodFin(factor, 1) : 1;
const px0 = Math.min(1, Math.max(0, twodFin(fx, 0.5))); const py0 = Math.min(1, Math.max(0, twodFin(fy, 0.5)));
const scale = Math.min(TWOD_ZOOM_MAX, Math.max(TWOD_ZOOM_MIN, (fit.w / view.w) * f));
const w = fit.w / scale; const h = fit.h / scale;
const px = view.x + view.w * px0; const py = view.y + view.h * py0;
return { x: px - w * px0, y: py - h * py0, w, h };
}
function twodPan(view, dxPx, dyPx, paneW, paneH) {
const sx = view.w / Math.max(1, twodFin(paneW, 1)); const sy = view.h / Math.max(1, twodFin(paneH, 1));
return { x: view.x - twodFin(dxPx, 0) * sx, y: view.y - twodFin(dyPx, 0) * sy, w: view.w, h: view.h };
}
function twodCenterOn(view, card) {
return { x: card.x + card.w / 2 - view.w / 2, y: card.y + card.h / 2 - view.h / 2, w: view.w, h: view.h };
}
const twodViewBoxText = (v) => [v.x, v.y, v.w, v.h].map((n) => String(Math.round(n * 100) / 100)).join(' ');
return { TWOD_ZOOM_MIN, TWOD_ZOOM_MAX, twodRelevanceSelection, twodScene, TWOD_LABEL_SIZE, TWOD_LABEL_ASCENT, TWOD_LABEL_DESCENT, twodFrameLabels, twodLabelOverlaps, TWOD_LOD_SCALE, twodScale, twodTextHidden, twodFit, twodZoom, twodPan, twodCenterOn, twodViewBoxText };
})();
// ---- ui/prefs.js
const __mod_ui_prefs = (function () {
'use strict';
const PREF_VERSION = 1;
const LAYOUT_OVERLAY_BELOW = 1100; // below this width the side areas are overlay panes (one open at a time)
const HEADER_WRAP_BELOW = 1500; // below this width the header may use several rows
const PANEL_LIMITS = { navWidth: [200, 520, 280], inspectorWidth: [280, 960, 380] }; // [min, max, default]
const PREFS_MAIN_MIN_WIDTH = 360; // the stage keeps at least this width while both side areas are docked
const THEMES = ['auto', 'light', 'dark'];
function prefKeyOf(meta) {
if (meta && typeof meta.prefKey === 'string' && meta.prefKey) return meta.prefKey;
const id = meta && typeof meta.projectId === 'string' ? meta.projectId : 'project';
const sha = meta && typeof meta.modelSha8 === 'string' ? meta.modelSha8 : '00000000';
return 'gemarc-atlas:' + id + ':' + sha;
}
function prefsPanelWidth(name, value, viewportWidth) {
const lim = PANEL_LIMITS[name];
if (!lim) return 0;
const n = typeof value === 'number' && Number.isFinite(value) ? value : lim[2];
const cap = Math.max(lim[0], Math.min(lim[1], (Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 1920) * (name === 'navWidth' ? 0.34 : 0.62)));
return Math.round(Math.min(cap, Math.max(lim[0], n)));
}
const layoutKindFor = (viewportWidth) => (viewportWidth < LAYOUT_OVERLAY_BELOW ? 'overlay' : 'docked');
const headerWraps = (viewportWidth) => viewportWidth < HEADER_WRAP_BELOW;
function layoutColumns(prefs, viewportWidth) {
if (layoutKindFor(viewportWidth) === 'overlay') return { nav: 0, inspector: 0 };
let nav = prefs.navOpen ? prefsPanelWidth('navWidth', prefs.navWidth, viewportWidth) : 0;
let inspector = prefs.inspectorOpen ? prefsPanelWidth('inspectorWidth', prefs.inspectorWidth, viewportWidth) : 0;
const over = nav + inspector + PREFS_MAIN_MIN_WIDTH + 40 - viewportWidth;
if (over > 0 && inspector) inspector -= Math.min(over, inspector - PANEL_LIMITS.inspectorWidth[0]);
const overRest = nav + inspector + PREFS_MAIN_MIN_WIDTH + 40 - viewportWidth;
if (overRest > 0 && nav) nav -= Math.min(overRest, nav - PANEL_LIMITS.navWidth[0]);
return { nav, inspector };
}
function prefsDefaults(levels) {
const ls = Array.isArray(levels) && levels.length ? levels : ['REQ', 'BD', 'DD'];
return { v: PREF_VERSION, lang: null, theme: 'auto', level: ls[Math.min(1, ls.length - 1)], navWidth: PANEL_LIMITS.navWidth[2], inspectorWidth: PANEL_LIMITS.inspectorWidth[2], navOpen: true, inspectorOpen: true, sub: {} };
}
function prefsParse(raw, ctx) {
const c = ctx || {};
let o = null;
if (typeof raw === 'string') { try { o = JSON.parse(raw); } catch (e) { o = null; } } else o = raw;
if (!o || typeof o !== 'object' || Array.isArray(o)) o = {};
const p = prefsDefaults(c.levels);
const levels = Array.isArray(c.levels) && c.levels.length ? c.levels : ['REQ', 'BD', 'DD'];
if (typeof o.level === 'string' && levels.includes(o.level)) p.level = o.level;
if (typeof o.lang === 'string' && Array.isArray(c.languages) && c.languages.includes(o.lang)) p.lang = o.lang;
if (THEMES.includes(o.theme)) p.theme = o.theme;
p.navWidth = prefsPanelWidth('navWidth', o.navWidth, c.viewportWidth);
p.inspectorWidth = prefsPanelWidth('inspectorWidth', o.inspectorWidth, c.viewportWidth);
if (typeof o.navOpen === 'boolean') p.navOpen = o.navOpen;
if (typeof o.inspectorOpen === 'boolean') p.inspectorOpen = o.inspectorOpen;
if (o.sub && typeof o.sub === 'object' && !Array.isArray(o.sub)) {
for (const k of Object.keys(o.sub)) if (typeof o.sub[k] === 'string' && k.length <= 40) p.sub[k] = o.sub[k].slice(0, 40);
}
return p;
}
const prefsSerialize = (p) => JSON.stringify(p);
return { PREF_VERSION, LAYOUT_OVERLAY_BELOW, HEADER_WRAP_BELOW, PANEL_LIMITS, THEMES, prefKeyOf, prefsPanelWidth, layoutKindFor, headerWraps, layoutColumns, prefsDefaults, prefsParse, prefsSerialize };
})();
// ---- ui/nav.js
const __mod_ui_nav = (function () {
'use strict';
const TAB_ORDER = ['1D', '2D', '3D', '4D', '5D', 'QA'];
const NAV_ROUTE_KEYS = ['level', 'sel', 'req', 'mode'];
function navParseRoute(hash, knownTabs) {
const tabs = Array.isArray(knownTabs) ? knownTabs : TAB_ORDER;
const text = typeof hash === 'string' ? hash.replace(/^#\/?/, '') : '';
const cut = text.indexOf('?');
const head = cut < 0 ? text : text.slice(0, cut);
const params = {};
if (cut >= 0) {
for (const pair of text.slice(cut + 1).split('&')) {
const eq = pair.indexOf('=');
if (eq <= 0) continue;
const key = pair.slice(0, eq);
if (!NAV_ROUTE_KEYS.includes(key)) continue;
let val = pair.slice(eq + 1);
try { val = decodeURIComponent(val); } catch (e) { continue; }
if (val.length <= 400) params[key] = val;
}
}
return { tab: tabs.includes(head) ? head : null, params };
}
function navBuildRoute(tab, params) {
const parts = [];
for (const key of NAV_ROUTE_KEYS) {
const v = params ? params[key] : undefined;
if (typeof v === 'string' && v) parts.push(key + '=' + encodeURIComponent(v));
}
return '#/' + tab + (parts.length ? '?' + parts.join('&') : '');
}
const navSelText = (sel) => (sel && typeof sel.type === 'string' && typeof sel.id === 'string' ? sel.type + ':' + sel.id : '');
function navParseSel(text) {
if (typeof text !== 'string') return null;
const i = text.indexOf(':');
if (i <= 0 || i === text.length - 1) return null;
return { type: text.slice(0, i), id: text.slice(i + 1) };
}
const navTabOrder = (def) => (Number.isFinite(def.order) ? def.order : TAB_ORDER.indexOf(def.id) >= 0 ? TAB_ORDER.indexOf(def.id) : 100);
function navMakeRegistry() {
const tabs = new Map(); const inspector = new Map();
const check = (def, needs) => {
if (!def || typeof def !== 'object') return 'definition missing'; // diag: author-facing
if (typeof def.id !== 'string' || !def.id) return 'id missing'; // diag: author-facing
for (const k of needs) if (typeof def[k] !== (k === 'render' || k === 'controls' ? 'function' : 'string')) return k + ' missing';
return null;
};
return {
registerTab(def) {
const bad = check(def, ['labelKey', 'render']);
if (bad) return { ok: false, reason: bad }; // diag: author-facing
tabs.set(def.id, { ...def });
return { ok: true };
},
registerSideTab(def) {
const bad = check(def, ['labelKey', 'render']);
if (bad) return { ok: false, reason: bad }; // diag: author-facing
inspector.set(def.id, { ...def });
return { ok: true };
},
hasTab: (id) => tabs.has(id),
getTab: (id) => tabs.get(id) || null,
tabIds: () => [...tabs.values()].sort((a, b) => navTabOrder(a) - navTabOrder(b)).map((d) => d.id),
sideTabs: (tabId) => [...inspector.values()].filter((d) => !Array.isArray(d.tabs) || d.tabs.includes(tabId)).sort((a, b) => navTabOrder(a) - navTabOrder(b)),
getSideTab: (id) => inspector.get(id) || null,
};
}
return { TAB_ORDER, navParseRoute, navBuildRoute, navSelText, navParseSel, navMakeRegistry };
})();
// ---- ui/shell-state.js
const __mod_ui_shell_state = (function () {
'use strict';
const { navParseSel, navSelText } = __mod_ui_nav;
const { docInitial } = __mod_ui_doc_state;
const { viewHasSelection } = __mod_ui_model_view;
const { isRecord, asList } = __mod_model_access;
const SHELL_SIDES = ['nav', 'inspector'];
function shellInitial(ctx, prefs, route, tabs, lang) {
const params = route && route.params ? route.params : {};
const level = typeof params.level === 'string' && ctx.levels.includes(params.level) ? params.level : (ctx.levels.includes(prefs.level) ? prefs.level : ctx.levels[0]);
const sel = navParseSel(params.sel);
const tab = route && route.tab && tabs.includes(route.tab) ? route.tab : (tabs.includes('2D') ? '2D' : tabs[0]);
return {
tab, level, lang, theme: prefs.theme,
sel: sel && viewHasSelection(ctx, sel) ? sel : null,
overlay: null, searchOpen: false, query: '', inspectorTab: 'detail', navSub: prefs.sub && typeof prefs.sub.nav === 'string' ? prefs.sub.nav : 'ctl',
navFilter: '', navOpen: prefs.navOpen, inspectorOpen: prefs.inspectorOpen,
doc: docInitial(ctx, level, params), docRaw: false,
twod: { view: null, query: '', board: 'canvas', unitId: null },
};
}
const shellSetTab = (st, tab) => ({ ...st, tab, overlay: null, inspectorTab: 'detail' });
const shellSetLevel = (st, level) => ({ ...st, level, doc: { ...st.doc, level: st.doc.level === level ? st.doc.level : level } });
const shellSelect = (st, sel) => ({ ...st, sel: sel && typeof sel.type === 'string' && typeof sel.id === 'string' ? { type: sel.type, id: sel.id } : null });
const shellSetLang = (st, lang) => ({ ...st, lang });
const shellSetTheme = (st, theme) => ({ ...st, theme });
function shellTogglePanel(st, name, layoutKind) {
if (!SHELL_SIDES.includes(name)) return st;
if (layoutKind === 'overlay') return { ...st, overlay: st.overlay === name ? null : name };
return name === 'nav' ? { ...st, navOpen: !st.navOpen } : { ...st, inspectorOpen: !st.inspectorOpen };
}
function shellEscape(st) {
if (st.searchOpen) return { ...st, searchOpen: false, query: '' };
if (st.overlay) return { ...st, overlay: null };
if (st.sel) return { ...st, sel: null };
return st;
}
function shellRouteParams(st) {
const p = { level: st.level };
const s = navSelText(st.sel);
if (s) p.sel = s;
if (st.tab === '1D' && st.doc.mode === 'requirement' && st.doc.reqId) p.req = st.doc.reqId;
return p;
}
function shellKeyAction(key, mods, typing, tabIds) {
const m = mods || {};
if (key === 'Escape') return { type: 'escape' };
if ((key === 'k' || key === 'K') && m.ctrl) return { type: 'search' };
if (typing || m.ctrl || m.alt || m.meta) return null;
if (key === '/') return { type: 'search' };
if (key === '[') return { type: 'panel', name: 'nav' };
if (key === ']') return { type: 'panel', name: 'inspector' };
if (/^[1-9]$/.test(key)) { const id = asList(tabIds)[Number(key) - 1]; return id ? { type: 'tab', id } : null; }
return null;
}
function shellMergeHits(hits) {
const seen = new Map(); const out = [];
for (const h of asList(hits)) {
if (!isRecord(h)) continue;
const key = String(h.kind) + '\u0000' + String(h.id);
const first = seen.get(key);
if (first) { if (typeof h.tab === 'string' && !first.tabs.includes(h.tab)) first.tabs.push(h.tab); continue; }
const row = { ...h, tabs: typeof h.tab === 'string' ? [h.tab] : [] };
seen.set(key, row); out.push(row);
}
return out;
}
function shellHitFrom(hit, current) {
return isRecord(hit) && Array.isArray(hit.tabs) && hit.tabs.includes(current) && hit.tab !== current
? { ...hit, tab: current, target: { ...(isRecord(hit.target) ? hit.target : {}), tab: current } } : hit;
}
function shellApplyHit(ctx, hit, tabIds) {
const h = isRecord(hit) ? hit : {}; const t = isRecord(h.target) ? h.target : {};
const out = {};
const wanted = typeof t.tab === 'string' ? t.tab : h.tab;
if (typeof wanted === 'string') out.tab = tabIds.includes(wanted) ? wanted : (tabIds.includes('2D') ? '2D' : tabIds[0]);
if (typeof t.level === 'string' && ctx.levels.includes(t.level)) out.level = t.level;
if (h.kind === 'requirement' && typeof h.id === 'string') out.requirementId = h.id;
else if (typeof t.node === 'string') out.sel = { type: 'node', id: t.node };
else if (typeof t.edge === 'string') out.sel = { type: 'edge', id: t.edge };
else if (typeof t.caseId === 'string') out.sel = { type: 'case', id: t.caseId };
if (typeof t.diagram === 'string') out.unitId = t.diagram;
return out;
}
return { SHELL_SIDES, shellInitial, shellSetTab, shellSetLevel, shellSelect, shellSetLang, shellSetTheme, shellTogglePanel, shellEscape, shellRouteParams, shellKeyAction, shellMergeHits, shellHitFrom, shellApplyHit };
})();
// ---- ui/navigator.js
const __mod_ui_navigator = (function () {
'use strict';
const { shownAtLevel, keepFocus, relevance, UNASSIGNED_ID, asList } = __mod_model_access;
const { floorLevels } = __mod_layout;
const { twodRelevanceSelection } = __mod_ui_twod_scene;
const navFold = (s) => String(s === undefined || s === null ? '' : s).normalize('NFKC').toLowerCase();
function navigatorTree(ctx, level, filter, selection) {
const lv = ctx.levels.includes(level) ? level : ctx.levels[ctx.levels.length - 1];
const vis = keepFocus(shownAtLevel(ctx.model, lv, ctx.vocab, ctx.ix), relevance(ctx.ix, twodRelevanceSelection(ctx, selection)));
const words = navFold(filter).split(/\s+/).filter(Boolean);
const keep = (n) => vis.nodeIds.has(n.id) && (words.length === 0 || words.every((w) => navFold(n.id + ' ' + n.name + ' ' + n.kind).includes(w)));
const levels = floorLevels(ctx.model, ctx.vocab);
const space = ctx.model.space && typeof ctx.model.space === 'object' ? ctx.model.space : {};
const byRoom = new Map(); const loose = [];
for (const n of ctx.nodes) {
if (!keep(n)) continue;
const room = ctx.ix.rooms.get(n.roomId); const floor = room ? ctx.ix.floors.get(room.floorId) : null;
if (room && floor && ctx.ix.buildings.has(floor.buildingId)) {
if (!byRoom.has(room.id)) byRoom.set(room.id, []);
byRoom.get(room.id).push({ id: n.id, name: typeof n.name === 'string' && n.name ? n.name : n.id, kind: String(n.kind) });
} else loose.push({ id: n.id, name: typeof n.name === 'string' && n.name ? n.name : n.id, kind: String(n.kind) });
}
let count = 0; const groups = [];
for (const b of asList(space.buildings)) {
if (!b || typeof b.id !== 'string') continue;
const floors = asList(space.floors).filter((f) => f && f.buildingId === b.id).slice()
.sort((x, y) => (levels.get(y.id) ?? 0) - (levels.get(x.id) ?? 0))
.map((f) => ({ id: f.id, name: typeof f.name === 'string' ? f.name : '', rooms: asList(space.rooms).filter((r) => r && r.floorId === f.id && byRoom.has(r.id)).map((r) => ({ id: r.id, name: typeof r.name === 'string' ? r.name : '', nodes: byRoom.get(r.id) })) }))
.filter((f) => f.rooms.length);
if (floors.length) { floors.forEach((f) => f.rooms.forEach((r) => { count += r.nodes.length; })); groups.push({ id: b.id, name: typeof b.name === 'string' ? b.name : '', unassigned: false, floors }); }
}
if (loose.length) { count += loose.length; groups.push({ id: UNASSIGNED_ID, name: '', unassigned: true, floors: [{ id: UNASSIGNED_ID, name: '', rooms: [{ id: UNASSIGNED_ID, name: '', nodes: loose }] }] }); }
return { groups, count };
}
return { navigatorTree };
})();
// ---- ui/panel-views.js
const __mod_ui_panel_views = (function () {
'use strict';
const { domEl } = __mod_ui_dom;
const { navigatorTree } = __mod_ui_navigator;
const { inspectSelection } = __mod_ui_inspector;
const PANEL_FIELD_KEYS = {
id: 'insp.field.id', kind: 'insp.field.kind', summary: 'insp.field.summary', grade: 'insp.field.grade', placement: 'insp.field.placement', trust: 'insp.field.trust',
relation: 'insp.field.relation', label: 'insp.field.label', guard: 'insp.field.guard', category: 'insp.field.category', events: 'insp.field.events', sealed: 'insp.field.sealed',
source: 'insp.field.source', tests: 'insp.field.tests',
};
const PANEL_TYPE_KEYS = { node: 'insp.type.node', edge: 'insp.type.edge', case: 'insp.type.case', requirement: 'insp.type.requirement' };
function renderNavigator(host, api) {
const { t, ctx } = api; const st = api.state;
const tree = navigatorTree(ctx, st.level, st.navFilter, st.sel);
const filter = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'nav-filter', value: st.navFilter, placeholder: t('nav.filter.placeholder'), 'aria-label': t('nav.filter.label'), oninput: (ev) => api.setNavFilter(ev.target.value) });
const nodeBtn = (n) => {
const on = st.sel && st.sel.type === 'node' && st.sel.id === n.id;
return domEl('button', { type: 'button', class: 'nav-node' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', 'data-node': n.id, onclick: () => api.select({ type: 'node', id: n.id }) }, domEl('span', { class: 'nav-name' }, n.name), ' ', domEl('span', { class: 'muted' }, api.label('kinds', n.kind)));
};
const groups = tree.groups.map((b) => domEl('details', { open: 'open', class: 'nav-building' },
domEl('summary', null, b.unassigned ? t('nav.unassigned') : b.name || b.id),
b.floors.map((f) => domEl('div', { class: 'nav-floor' }, b.unassigned ? null : domEl('div', { class: 'nav-label' }, f.name || f.id),
f.rooms.map((r) => domEl('div', { class: 'nav-room' }, b.unassigned ? null : domEl('div', { class: 'nav-label muted' }, r.name || r.id), r.nodes.map(nodeBtn)))))));
host.appendChild(domEl('div', { class: 'nav' }, filter, domEl('p', { class: 'muted' }, t('nav.count', { n: tree.count })), tree.count ? groups : domEl('p', { class: 'empty' }, t('empty.nodes'))));
}
function panelLinkButton(api, link) {
return domEl('button', { type: 'button', class: 'chip', onclick: () => api.select({ type: link.type, id: link.id }) }, link.title);
}
function panelFieldValue(api, f) {
if (typeof f.flag === 'boolean') return f.flag ? api.t('flag.yes') : api.t('flag.no');
if (f.vocab && f.value) return api.label(f.vocab, f.value);
return f.value;
}
function renderInspectorDetail(host, api) {
const { t } = api;
const d = inspectSelection(api.ctx, api.state.sel);
if (!d) { host.appendChild(domEl('p', { class: 'empty', role: 'status' }, t('insp.none'))); return; }
const dl = domEl('dl', { class: 'fields' }, d.fields.filter((f) => f.value !== '' || typeof f.flag === 'boolean').map((f) => [domEl('dt', null, t(PANEL_FIELD_KEYS[f.key])), domEl('dd', null, f.key === 'id' ? domEl('code', null, panelFieldValue(api, f)) : panelFieldValue(api, f))]));
const links = d.links.map((l) => domEl('li', null, l.relation !== undefined ? domEl('span', { class: 'muted' }, (l.dir === 'out' ? '\u2192 ' : '\u2190 ') + (l.relation ? api.label('relations', l.relation) : '') + ' ') : null, panelLinkButton(api, l.other || l)));
const evid = d.evidences.map((e) => domEl('li', null,
domEl('code', null, e.id), ' ', e.known ? [domEl('span', null, e.file + (e.range ? ':' + e.range : '')), e.anchor ? [' ', domEl('code', null, e.anchor)] : null, e.topic ? [' \u2013 ', e.topic] : null, e.grade ? [' ', domEl('span', { class: 'muted' }, api.label('verification', e.grade))] : null] : domEl('span', { class: 'bad' }, t('insp.evidence.unknown'))));
host.appendChild(domEl('div', { class: 'insp-detail' },
domEl('h3', null, d.title), domEl('p', { class: 'muted' }, t(PANEL_TYPE_KEYS[d.type])), dl,
d.props.length ? [domEl('h4', null, t('insp.props')), domEl('dl', { class: 'fields' }, d.props.map((p) => [domEl('dt', null, p.key), domEl('dd', null, p.value)]))] : null,
d.evidences.length ? [domEl('h4', null, t('insp.evidence')), domEl('ul', null, evid)] : null,
d.links.length ? [domEl('h4', null, t('insp.links')), domEl('ul', null, links)] : null,
d.cases.length ? [domEl('h4', null, t('insp.cases')), domEl('div', { class: 'chips' }, d.cases.map((c) => panelLinkButton(api, c)))] : null,
d.requirements.length ? [domEl('h4', null, t('insp.requirements')), domEl('div', { class: 'chips' }, d.requirements.map((c) => panelLinkButton(api, c)))] : null));
}
function renderInspector(host, api, slots) {
const { t } = api; const st = api.state;
const tabs = [{ id: 'detail', labelKey: 'insp.tab.detail' }, ...slots];
const active = tabs.some((x) => x.id === st.inspectorTab) ? st.inspectorTab : 'detail';
host.appendChild(domEl('div', { class: 'choices', role: 'group', 'aria-label': t('insp.title') }, tabs.map((x) => domEl('button', { type: 'button', 'aria-pressed': active === x.id ? 'true' : 'false', class: 'seg' + (active === x.id ? ' on' : ''), onclick: () => api.setInspectorTab(x.id) }, t(x.labelKey)))));
const body = domEl('div', { class: 'insp-body' });
if (active === 'detail') renderInspectorDetail(body, api);
else { const def = slots.find((x) => x.id === active); if (def) def.render(body, api); }
host.appendChild(body);
}
return { renderNavigator, renderInspectorDetail, renderInspector };
})();
// ---- ui/shell.js
const __mod_ui_shell = (function () {
'use strict';
const { domEl, domById, domClear, domOnKey, domOnHash, domOnResize, domHash, domSetHash, domStoreGet, domStoreSet, domViewport, domBody, domSet, domGet, domRootAttrs, domSetTitle, domSlotArray, domExpose, domCatchErrors, domFocusState, domRestoreFocus, domQuery, domLater, domNavigatorLanguage, domScrollTop, domQueryAll, domBoxes, domBrokenTokens, domAtScale, domClassOn } = __mod_ui_dom;
const { TWOD_LOD_SCALE } = __mod_ui_twod_scene;
const { gateOverlaps, gateTextAllow, GATE_TOKEN_MAX } = __mod_ui_gate_checks;
const { makeI18n, i18nPickLang } = __mod_ui_i18n;
const { makeBoundary } = __mod_ui_boundary;
const { prefKeyOf, prefsParse, prefsSerialize, layoutKindFor, layoutColumns, headerWraps, THEMES } = __mod_ui_prefs;
const { navMakeRegistry, navParseRoute, navBuildRoute } = __mod_ui_nav;
const { viewContext, viewElementTitle, viewVocabLabel } = __mod_ui_model_view;
const { shellInitial, shellSetTab, shellSetLevel, shellSelect, shellSetLang, shellSetTheme, shellTogglePanel, shellEscape, shellRouteParams, shellKeyAction, shellApplyHit, shellMergeHits, shellHitFrom } = __mod_ui_shell_state;
const { docSetRequirement, docCheckAll } = __mod_ui_doc_state;
const { renderNavigator, renderInspector } = __mod_ui_panel_views;
const { makeSearchIndex, search } = __mod_search;
const SHELL_API_VERSION = 1;
const SHELL_SCHEMA = 'gemarc-atlas-bundle/1';
const SHELL_TAB_KEYS = { '1D': 'tab.1d', '2D': 'tab.2d', '3D': 'tab.3d', '4D': 'tab.4d', '5D': 'tab.5d', QA: 'tab.qa' };
const SHELL_SEARCH_KIND_KEYS = { node: 'kind.node', edge: 'kind.edge', diagram: 'kind.diagram', case: 'kind.case', intervention: 'kind.intervention', requirement: 'kind.requirement', tbd: 'kind.tbd' };
const ARROW_KEY = /^Arrow(Left|Right|Up|Down)$/;
const SHELL_THEME_KEYS = { auto: 'hdr.theme.auto', light: 'hdr.theme.light', dark: 'hdr.theme.dark' };
function createApp(bundle, opts) {
const options = opts || {};
const sink = domSlotArray('__ATLAS_ERRORS__');
const boundary = makeBoundary(sink);
domCatchErrors((rec) => sink.push(rec));
if (!bundle || bundle.schema !== SHELL_SCHEMA) boundary.report('bundle', 'unexpected bundle schema'); // diag: author-facing
const ctx = viewContext(bundle);
domExpose('__ATLAS_WARNINGS__', ctx.warnings.items);
const tables = bundle && bundle.i18n && bundle.i18n.tables ? bundle.i18n.tables : {};
const tableLangs = Object.keys(tables);
const prefKey = prefKeyOf(ctx.meta);
const vp0 = domViewport();
const prefs = prefsParse(domStoreGet(prefKey), { levels: ctx.levels, languages: tableLangs, viewportWidth: vp0.w });
const lang = i18nPickLang(tableLangs, [prefs.lang, ctx.primaryLanguage, domNavigatorLanguage()]);
const i18nCache = new Map();
const i18nFor = (l) => { if (!i18nCache.has(l)) i18nCache.set(l, makeI18n(tables, l, (rec) => sink.push(rec))); return i18nCache.get(l); };
const registry = navMakeRegistry();
for (const def of options.tabs || []) { const r = registry.registerTab(def); if (!r.ok) boundary.report('registerTab', r.reason); }
for (const def of options.sideTabs || []) registry.registerSideTab(def);
const counter = { n: 0 };
const mountQueue = []; // callbacks of the tabs, run once per redraw right after the tree is in the page (the canvases of the stage draw there)
let state = shellInitial(ctx, prefs, navParseRoute(domHash(), registry.tabIds()), registry.tabIds(), lang);
let t = i18nFor(lang).t; let lookupIndex = null; let dragFlag = false; let statusExtra = ''; let rendering = false; let pending = false;
const root = () => domById('atlas-root');
const label = (collection, id) => viewVocabLabel(ctx, collection, id, state.lang);
const tabOf = () => registry.getTab(state.tab);
const api = {
ctx, boundary, registry, label,
cache: new Map(), // per application scratch space for tab modules (rendered parts that are expensive to rebuild)
t: (key, vars) => t(key, vars),
get state() { return state; },
title: (id) => viewElementTitle(ctx, id),
select: (sel) => commit(shellSelect(state, sel)),
setLevel: (level) => commit(shellSetLevel(state, ctx.levels.includes(level) ? level : state.level)),
setTab: (id) => (registry.hasTab(id) ? (commit(shellSetTab(state, id)), true) : false),
setDoc: (doc) => commit({ ...state, doc }),
setRaw: (on) => commit({ ...state, docRaw: !!on }),
setTwod: (patch) => commit({ ...state, twod: { ...state.twod, ...patch } }),
setTwodView: (view) => { state = { ...state, twod: { ...state.twod, view } }; },
setNavFilter: (text) => commit({ ...state, navFilter: String(text).slice(0, 200) }),
setInspectorTab: (id) => commit({ ...state, inspectorTab: id }),
openUnit: (id) => commit({ ...shellSetTab(state, registry.hasTab('2D') ? '2D' : state.tab), twod: { ...state.twod, board: 'diagrams', unitId: id } }),
dragged: () => dragFlag,
markDragged: () => { dragFlag = true; domLater(() => { dragFlag = false; }, 0); },
redraw: () => app.renderOnce(),
setStatus: (text) => { statusExtra = String(text); },
mounted: (fn) => { if (typeof fn === 'function') mountQueue.push(fn); },
};
function commit(next) { state = next; app.renderOnce(); return true; }
function ensureIndex() {
if (!lookupIndex) lookupIndex = boundary.guardRun('search', () => makeSearchIndex(ctx.model, ctx.diagrams, { vocab: ctx.vocab, ix: ctx.ix }), []);
return lookupIndex;
}
function applyHit(hit) {
const ids = registry.tabIds();
const go = shellApplyHit(ctx, shellHitFrom(hit, state.tab), ids);
let next = { ...state, searchOpen: false, query: '' };
if (go.level) next = shellSetLevel(next, go.level);
if (go.tab) next = { ...next, tab: go.tab, inspectorTab: 'detail', overlay: null };
if (go.sel) next = shellSelect(next, go.sel);
if (go.requirementId) next = { ...next, doc: docSetRequirement(next.doc, go.requirementId) };
if (go.unitId) next = { ...next, twod: { ...next.twod, board: 'diagrams', unitId: go.unitId } };
commit(next);
}
function renderFinder() {
const hits = state.query.trim() ? shellMergeHits(search(ensureIndex(), state.query)) : [];
const input = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'search-input', value: state.query, placeholder: t('search.placeholder'), 'aria-label': t('search.label'),
oninput: (ev) => commit({ ...state, query: ev.target.value }), onkeydown: (ev) => { if (ev.key === 'Enter' && hits.length) { ev.preventDefault(); applyHit(hits[0]); } } });
const list = hits.map((h) => domEl('li', null, domEl('button', { type: 'button', class: 'hit', onclick: () => applyHit(h) },
domEl('span', { class: 'hit-kind muted' }, SHELL_SEARCH_KIND_KEYS[h.kind] ? t(SHELL_SEARCH_KIND_KEYS[h.kind]) : h.kind, ' \u00b7 ', (h.tabs && h.tabs.length ? h.tabs : [h.tab]).map((x) => t(SHELL_TAB_KEYS[x] || 'tab.2d')).join(' / ')), ' ', domEl('span', { class: 'hit-title' }, String(h.title)), ' ', domEl('span', { class: 'muted' }, String(h.sub)))));
return domEl('div', { class: 'scrim', onclick: (ev) => { if (ev.target === ev.currentTarget) commit({ ...state, searchOpen: false, query: '' }); } },
domEl('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('search.title') }, input,
domEl('ul', { class: 'hits', role: 'listbox' }, list),
state.query.trim() && !hits.length ? domEl('p', { class: 'empty' }, t('search.none')) : null));
}
function renderHeader(kind) {
const tabs = registry.tabIds().map((id) => domEl('button', { type: 'button', class: 'tab' + (state.tab === id ? ' on' : ''), 'aria-current': state.tab === id ? 'page' : null, 'data-tab': id, 'data-keep': 'tab-' + id, onclick: () => api.setTab(id), onkeydown: (ev) => shellTabKey(ev, id) }, t(SHELL_TAB_KEYS[id] || registry.getTab(id).labelKey)));
const levels = ctx.levels.map((lv) => domEl('button', { type: 'button', class: 'seg' + (state.level === lv ? ' on' : ''), 'aria-pressed': state.level === lv ? 'true' : 'false', 'data-level': lv, 'data-keep': 'level-' + lv, onclick: () => api.setLevel(lv) }, label('levels', lv)));
const langs = domEl('select', { 'aria-label': t('hdr.lang'), 'data-keep': 'lang', onchange: (ev) => commit(shellSetLang(state, ev.target.value)) }, tableLangs.map((l) => domEl('option', { value: l, selected: l === state.lang ? 'selected' : null }, t(l === 'zh-CN' ? 'hdr.lang.zh' : l === 'ja-JP' ? 'hdr.lang.ja' : 'hdr.lang.en'))));
const nextTheme = THEMES[(THEMES.indexOf(state.theme) + 1) % THEMES.length];
const def = tabOf();
const status = t('status.ready', { nodes: ctx.nodes.length, edges: ctx.edges.length }) + (statusExtra ? ' \u00b7 ' + statusExtra : '');
const title = typeof ctx.meta.title === 'string' && ctx.meta.title ? ctx.meta.title : String(ctx.meta.projectId || t('app.untitled'));
return domEl('header', { class: 'hdr', 'data-gate': 'header' },
domEl('div', { class: 'brand', 'data-gate': 'fit' }, domEl('strong', null, title), ' ', domEl('span', { class: 'muted' }, String(ctx.meta.modelSha8 || ''))),
domEl('nav', { class: 'tabs', 'aria-label': t('hdr.tabs') }, tabs),
domEl('div', { class: 'choices', role: 'group', 'aria-label': t('hdr.level') }, levels),
langs,
domEl('button', { type: 'button', 'data-keep': 'theme', onclick: () => commit(shellSetTheme(state, nextTheme)), title: t('hdr.theme.title') }, t(SHELL_THEME_KEYS[state.theme] || 'hdr.theme.auto')),
domEl('button', { type: 'button', 'data-keep': 'panel-nav', 'aria-pressed': (kind === 'overlay' ? state.overlay === 'nav' : state.navOpen) ? 'true' : 'false', onclick: () => commit(shellTogglePanel(state, 'nav', kind)) }, t('hdr.panel.nav')),
def && def.inspector ? domEl('button', { type: 'button', 'data-keep': 'panel-inspector', 'aria-pressed': (kind === 'overlay' ? state.overlay === 'inspector' : state.inspectorOpen) ? 'true' : 'false', onclick: () => commit(shellTogglePanel(state, 'inspector', kind)) }, t('hdr.panel.inspector')) : null,
domEl('button', { type: 'button', 'data-keep': 'search-open', onclick: () => commit({ ...state, searchOpen: true, query: '' }) }, t('hdr.search')),
domEl('span', { class: 'status', role: 'status', 'data-gate': 'fit' }, status));
}
function shellTabKey(ev, id) {
const ids = registry.tabIds(); const i = ids.indexOf(id);
const dir = ARROW_KEY.exec(ev.key);
const to = dir && dir[1] === 'Right' ? ids[(i + 1) % ids.length] : dir && dir[1] === 'Left' ? ids[(i + ids.length - 1) % ids.length] : ev.key === 'Home' ? ids[0] : ev.key === 'End' ? ids[ids.length - 1] : null;
if (to) { ev.preventDefault(); api.setTab(to); }
}
function renderSide(sideApi, def) {
const subs = def && def.controls ? ['ctl', 'nav'] : ['nav'];
const sub = subs.includes(state.navSub) ? state.navSub : subs[0];
const strip = subs.length > 1 ? domEl('div', { class: 'choices', role: 'group', 'aria-label': t('panel.nav.label') }, subs.map((s) => domEl('button', { type: 'button', 'aria-pressed': sub === s ? 'true' : 'false', class: 'seg' + (sub === s ? ' on' : ''), onclick: () => commit({ ...state, navSub: s }) }, t(s === 'ctl' ? 'panel.controls' : 'panel.navigator')))) : null;
const body = domEl('div', { class: 'panel-body' });
if (sub === 'ctl') def.controls(body, sideApi); else renderNavigator(body, sideApi);
return domEl('aside', { class: 'panel panel-nav', 'aria-label': t('panel.nav.label'), 'data-open': sideShown('nav') ? '1' : '0' }, strip, body);
}
function sideShown(name) {
const kind = layoutKindFor(domViewport().w);
if (kind === 'overlay') return state.overlay === name;
return name === 'nav' ? state.navOpen : state.inspectorOpen;
}
const app = {
version: SHELL_API_VERSION, bundle: ctx.bundle, ctx, api,
registerTab: (def) => { const r = registry.registerTab(def); if (r.ok) app.renderOnce(); return r; },
registerSideTab: (def) => { const r = registry.registerSideTab(def); if (r.ok) app.renderOnce(); return r; },
tabs: () => registry.tabIds(),
get tab() { return state.tab; },
get level() { return state.level; },
get selection() { return state.sel; },
state: () => state,
setTab: (id) => api.setTab(id),
setLevel: (level) => api.setLevel(level),
select: (sel) => api.select(sel),
frame: () => counter.n,
errors: sink,
warnings: () => ctx.warnings.items,
calibrationHooks: ctx.bundle.calibrationHooks || {},
checkDocSelections: () => docCheckAll(ctx),
renderOnce(tab) {
if (typeof tab === 'string' && tab !== state.tab && registry.hasTab(tab)) state = shellSetTab(state, tab);
if (rendering) { pending = true; return { tab: state.tab, frame: counter.n, ok: true, error: null }; }
rendering = true;
let res;
try {
do {
pending = false;
res = boundary.frameRun(counter, 'render', draw);
} while (pending);
} finally { rendering = false; }
return { tab: state.tab, frame: counter.n, ok: res.ok, error: res.error };
},
gateConfig() {
return {
tabs: registry.tabIds(),
setTab: (id) => { app.setTab(id); },
tabState: () => domGet(domBody(), 'data-tab'),
renderOnce: (tab) => { const r = app.renderOnce(tab); return { tab: r.tab, ok: r.ok }; },
frame: () => counter.n,
headerSelector: '[data-gate~=header]',
sceneBounds: (tab) => { const d = registry.getTab(typeof tab === 'string' ? tab : state.tab); return d && typeof d.sceneBounds === 'function' ? d.sceneBounds(api) : null; },
textAllow: () => gateTextAllow(ctx.bundle),
steps: gateSteps(),
};
},
};
function gateWithTab(id, fn) {
if (!registry.hasTab(id)) return 'the tab ' + id + ' is not in the page'; // diag: author-facing
const before = state.tab;
if (before !== id) api.setTab(id);
try { return fn(); } finally { if (state.tab !== before) api.setTab(before); }
}
function gateSteps() {
return [
{ name: 'doc-twin', run: () => {
const rows = docCheckAll(ctx); const bad = rows.filter((r) => !r.ok);
for (const b of bad) boundary.report('doc-twin', 'document hash differs at ' + b.level + ' (' + b.count + ' ids)'); // diag: author-facing
return bad.length === 0 || 'document twin mismatch'; // diag: author-facing
} },
{ name: 'levels', run: () => {
const before = state.level;
for (const lv of ctx.levels) { api.setLevel(lv); if (domGet(domBody(), 'data-level') !== lv) return 'level did not change'; } // diag: author-facing
api.setLevel(before);
return true;
} },
{ name: 'inspector', run: () => {
const first = ctx.nodes[0];
if (!first) return true;
api.select({ type: 'node', id: first.id });
const ok = !!domQuery('.insp-detail') || !sideShown('inspector') || !(tabOf() && tabOf().inspector);
api.select(null);
return ok || 'inspector shows no detail'; // diag: author-facing
} },
{ name: 'twod-labels', run: () => gateWithTab('2D', () => {
const board = state.twod.board;
if (board !== 'canvas') api.setTwod({ board: 'canvas' });
const svg = domQuery('svg.twod');
let out = true;
if (svg) {
const hits = domAtScale(svg, TWOD_LOD_SCALE, () => gateOverlaps(domBoxes('text.frame-label')));
if (hits.length) out = 'frame labels overlap: ' + hits.length + ' pair(s), first ' + String(hits[0][0].text).slice(0, 24) + ' / ' + String(hits[0][1].text).slice(0, 24); // diag: author-facing
}
if (board !== 'canvas') api.setTwod({ board });
return out;
}) },
{ name: 'qa-rows', run: () => gateWithTab('QA', () => domQueryAll('[data-qa-row]').length > 0 || 'QA tab shows no rows') }, // diag: author-facing
{ name: 'table-words', run: () => {
for (const id of ['1D', 'QA']) {
const broken = gateWithTab(id, () => domBrokenTokens(['th', 'td'], GATE_TOKEN_MAX));
if (typeof broken === 'string') return broken;
if (broken.length) return 'a table word is cut across lines on ' + id + ': ' + String(broken[0]).slice(0, 24); // diag: author-facing
}
return true;
} },
{ name: 'search', run: () => {
commit({ ...state, searchOpen: true, query: '' });
const opened = !!domQuery('[role=dialog]');
commit({ ...state, searchOpen: false, query: '' });
return (opened && !domQuery('[role=dialog]')) || 'search dialog did not open and close'; // diag: author-facing
} },
{ name: 'inspector-tabs', run: () => {
const before = state.tab; const beforeInspector = state.inspectorTab; let out = true;
for (const id of registry.tabIds()) {
const def = registry.getTab(id);
if (!def || !def.inspector) continue;
if (state.tab !== id) api.setTab(id);
if (!domQuery('.panel-ins')) continue; // the visitor closed the inspector: there is nothing to open
for (const slot of ['detail', ...registry.sideTabs(id).map((x) => x.id)]) {
api.setInspectorTab(slot);
const body = domQuery('.panel-ins .insp-body');
if (!body || !body.firstChild) { out = 'the inspector tab ' + slot + ' of ' + id + ' shows nothing'; break; } // diag: author-facing
}
if (out !== true) break;
}
if (state.tab !== before) api.setTab(before);
if (state.inspectorTab !== beforeInspector) api.setInspectorTab(beforeInspector);
return out;
} },
...registry.tabIds().flatMap((id) => { const d = registry.getTab(id); return d && typeof d.gateSteps === 'function' ? d.gateSteps(api) : []; }),
];
}
function draw() {
const el = root();
if (!el) throw new Error('atlas-root missing'); // diag: author-facing
t = i18nFor(state.lang).t;
const vp = domViewport(); const kind = layoutKindFor(vp.w); const def = tabOf();
const focus = domFocusState();
const cols = layoutColumns({ navOpen: state.navOpen, inspectorOpen: state.inspectorOpen, navWidth: prefs.navWidth, inspectorWidth: prefs.inspectorWidth }, vp.w);
domRootAttrs({ lang: state.lang, 'data-scheme': state.theme === 'auto' ? null : state.theme });
domSetTitle(typeof ctx.meta.title === 'string' && ctx.meta.title ? ctx.meta.title : t('app.title'));
domSet(domBody(), 'data-tab', state.tab);
domSet(domBody(), 'data-level', state.level);
domSet(el, 'data-layout', kind); domSet(el, 'data-wrap', headerWraps(vp.w) ? '1' : '0');
domSet(el, 'style', '--nav-w:' + cols.nav + 'px;--ins-w:' + cols.inspector + 'px');
mountQueue.length = 0;
const stageBefore = domQuery('.stage');
const scroll = stageBefore && domGet(stageBefore, 'data-tab') === state.tab ? stageBefore.scrollTop : 0;
domClear(el);
el.appendChild(renderHeader(kind));
const shellEl = domEl('div', { class: 'shell' });
const stage = domEl('main', { class: 'stage', 'data-tab': state.tab, tabindex: '-1', 'aria-label': def ? t(def.labelKey) : '' });
if (!def) stage.appendChild(domEl('p', { class: 'empty' }, t('app.notab')));
else {
stage.appendChild(domEl('p', { class: 'question muted' }, def.questionKey ? t(def.questionKey) : ''));
const host = domEl('div', { class: 'tab-host' });
boundary.guardRun('tab ' + def.id, () => def.render(host, api), null);
stage.appendChild(host);
}
const showNav = kind === 'overlay' || cols.nav > 0;
if (showNav) shellEl.appendChild(boundary.guardRun('sidebar', () => renderSide(api, def), domEl('aside', { class: 'panel panel-nav' })));
shellEl.appendChild(stage);
const wantsInspector = !!(def && def.inspector);
if (wantsInspector && (kind === 'overlay' || cols.inspector > 0)) {
const ins = domEl('aside', { class: 'panel panel-ins', 'aria-label': t('insp.title'), 'data-open': sideShown('inspector') ? '1' : '0' });
boundary.guardRun('inspector', () => renderInspector(ins, api, registry.sideTabs(state.tab)), null);
shellEl.appendChild(ins);
}
if (kind === 'overlay' && state.overlay) shellEl.appendChild(domEl('div', { class: 'side-scrim', onclick: () => commit({ ...state, overlay: null }) }));
el.appendChild(shellEl);
for (const fn of mountQueue.splice(0)) boundary.guardRun('mount', fn, null);
if (state.searchOpen) el.appendChild(boundary.guardRun('search', renderFinder, domEl('div')));
domRestoreFocus(focus);
domScrollTop(domQuery('.stage'), scroll);
persist();
const hash = navBuildRoute(state.tab, shellRouteParams(state));
if (hash !== domHash()) domSetHash(hash);
}
function persist() {
const next = { ...prefs, lang: state.lang, theme: state.theme, level: state.level, navOpen: state.navOpen, inspectorOpen: state.inspectorOpen, sub: { ...prefs.sub, nav: state.navSub } };
domStoreSet(prefKey, prefsSerialize(next));
}
function onKey(ev) {
const target = ev.target; const tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || !!(target && target.isContentEditable);
const act = shellKeyAction(ev.key, { ctrl: ev.ctrlKey, alt: ev.altKey, meta: ev.metaKey }, typing, registry.tabIds());
if (!act) return;
if (act.type === 'tab') { ev.preventDefault(); api.setTab(act.id); }
else if (act.type === 'search') { ev.preventDefault(); commit({ ...state, searchOpen: true, query: '' }); }
else if (act.type === 'panel') { ev.preventDefault(); commit(shellTogglePanel(state, act.name, layoutKindFor(domViewport().w))); }
else if (act.type === 'escape') { const next = shellEscape(state); if (next !== state) { ev.preventDefault(); commit(next); } }
}
function onRoute() {
const r = navParseRoute(domHash(), registry.tabIds());
let next = state;
if (r.tab && r.tab !== state.tab) next = shellSetTab(next, r.tab);
if (typeof r.params.level === 'string' && ctx.levels.includes(r.params.level) && r.params.level !== state.level) next = shellSetLevel(next, r.params.level);
if (next !== state) commit(next);
}
domOnResize(() => app.renderOnce());
domOnKey(onKey);
domOnHash(onRoute);
return app;
}
return { SHELL_API_VERSION, createApp };
})();
// ---- ui/markdown.js
const __mod_ui_markdown = (function () {
'use strict';
const MD_MARKER_BLOCK = 'NOT-AUTHORED';
const MD_MARKER = /^\[((?:NOT-AUTHORED:[^\]\n]*)|N\/A|NONE-IN-SCOPE|NONE-DECLARED)\]/;
const MD_ENTITIES = [['&lt;', '<'], ['&gt;', '>'], ['&amp;', '&']];
const MD_MAX_DEPTH = 2;
const MD_MAX_INLINE = 20000;
const MD_MAX_LINK = 2000;
const MD_LINK = /^\[((?:[^\]\\]|\\.)*)\]\(([^)\s]*)\)/;
const mdUnEntity = (s) => { let out = s; for (const [a, b] of MD_ENTITIES) out = out.split(a).join(b); return out; };
function mdSpans(src, depth = 0) {
const out = []; let buf = ''; let i = 0; const s = String(src);
if (s.length > MD_MAX_INLINE) return [{ k: 't', s: mdUnEntity(s) }]; // a pathological cell is shown as plain text, not scanned quadratically
const flush = () => { if (buf) { out.push({ k: 't', s: mdUnEntity(buf) }); buf = ''; } };
while (i < s.length) {
const ch = s[i];
if (ch === '\\' && i + 1 < s.length && /[!-/:-@[-`{-~]/.test(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
if (ch === '`') {
let n = 1; while (s[i + n] === '`') n++;
const fence = '`'.repeat(n); const end = s.indexOf(fence, i + n);
if (end > i + n - 1 && end >= 0) { flush(); out.push({ k: 'c', s: mdUnEntity(s.slice(i + n, end).split('\\|').join('|').trim()) }); i = end + n; continue; }
buf += fence; i += n; continue;
}
if (ch === '[') {
const link = depth < MD_MAX_DEPTH ? MD_LINK.exec(s.slice(i, i + MD_MAX_LINK)) : null;
if (link) { flush(); out.push({ k: 'a', s: mdSpans(link[1], depth + 1), href: link[2] }); i += link[0].length; continue; }
const m = MD_MARKER.exec(s.slice(i, i + MD_MAX_LINK));
if (m) { flush(); out.push({ k: 'm', s: m[1], block: m[1].startsWith(MD_MARKER_BLOCK) }); i += m[0].length; continue; }
}
buf += ch; i += 1;
}
flush();
return out;
}
function mdCells(line) {
const cells = []; let cur = ''; const s = String(line).trim();
for (let i = 0; i < s.length; i++) {
const ch = s[i];
if (ch === '\\' && i + 1 < s.length) { cur += ch + s[i + 1]; i++; continue; }
if (ch === '|') { cells.push(cur); cur = ''; continue; }
cur += ch;
}
cells.push(cur);
if (s.startsWith('|')) cells.shift();
if (s.endsWith('|') && !s.endsWith('\\|') && cells.length) cells.pop();
return cells.map((c) => c.trim());
}
const mdIsRule = (line) => /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);
function mdParse(text) {
const lines = String(text).replace(/\r\n?/g, '\n').split('\n'); const blocks = []; let para = null; let list = null;
const endPara = () => { para = null; };
const endList = () => { list = null; };
for (let i = 0; i < lines.length; i++) {
const line = lines[i];
if (!line.trim()) { endPara(); endList(); continue; }
const fence = /^(`{3,})(.*)$/.exec(line);
if (fence) {
endPara(); endList();
const body = []; i += 1;
while (i < lines.length && !lines[i].startsWith(fence[1])) { body.push(lines[i]); i += 1; }
blocks.push({ t: 'code', text: body.join('\n') });
continue;
}
const h = /^(#{1,6})\s+(.*)$/.exec(line);
if (h) { endPara(); endList(); blocks.push({ t: 'h', n: h[1].length, inl: mdSpans(h[2].trim()) }); continue; }
if (line.trim().startsWith('|') && i + 1 < lines.length && mdIsRule(lines[i + 1].trim())) {
endPara(); endList();
const head = mdCells(line).map((c) => mdSpans(c)); const rows = []; i += 2;
while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(mdCells(lines[i]).map((c) => mdSpans(c))); i += 1; }
i -= 1;
blocks.push({ t: 'table', head, rows });
continue;
}
const q = /^>\s?(.*)$/.exec(line);
if (q) { endPara(); endList(); blocks.push({ t: 'quote', inl: mdSpans(q[1]) }); continue; }
const li = /^[-*]\s+(.*)$/.exec(line);
if (li) {
endPara();
if (!list) { list = { t: 'ul', items: [] }; blocks.push(list); }
list.items.push(mdSpans(li[1]));
continue;
}
endList();
if (!para) { para = { t: 'p', lines: [] }; blocks.push(para); }
para.lines.push(mdSpans(line.trim()));
}
return blocks;
}
function mdPlain(inl) {
return inl.map((x) => (x.k === 'a' ? mdPlain(x.s) : x.k === 'm' ? '[' + x.s + ']' : x.s)).join('');
}
const mdDiagramId = (href) => (typeof href === 'string' && href.startsWith('dg:') && href.length > 3 ? href.slice(3) : null);
function mdCountMarkers(text) {
const m = String(text).match(/\[NOT-AUTHORED:[^\]\n]*\]/g);
return m ? m.length : 0;
}
return { MD_MARKER_BLOCK, mdSpans, mdCells, mdParse, mdPlain, mdDiagramId, mdCountMarkers };
})();
// ---- ui/render-md.js
const __mod_ui_render_md = (function () {
'use strict';
const { domEl } = __mod_ui_dom;
const { mdDiagramId } = __mod_ui_markdown;
function mdInlineNodes(spans, opts) {
return spans.map((x) => {
if (x.k === 'c') return domEl('code', null, x.s);
if (x.k === 'm') return domEl('span', { class: x.block ? 'mark mark-block' : 'mark', title: opts.t(x.block ? 'doc.marker.block' : 'doc.marker.note') }, x.s);
if (x.k === 'a') {
const dg = mdDiagramId(x.href);
const kids = mdInlineNodes(x.s, opts);
if (dg === null) return domEl('span', null, kids);
return domEl('button', { type: 'button', class: 'link', 'data-diagram': dg, title: opts.t('doc.link.diagram'), onclick: () => opts.onDiagram(dg) }, kids);
}
return x.s;
});
}
const MD_TOKEN_MAX = 40;
function mdSpanText(spans) { return spans.map((x) => (typeof x.s === 'string' ? x.s : Array.isArray(x.s) ? mdSpanText(x.s) : '')).join(''); }
function mdCellAttrs(spans, extra) {
const txt = mdSpanText(spans);
return { ...(extra || {}), class: txt.length > 0 && txt.length <= MD_TOKEN_MAX && !/\s/.test(txt) ? 'tok' : null };
}
function renderBlocks(blocks, opts) {
const root = domEl('div', { class: 'md' });
for (const b of blocks) {
if (b.t === 'h') {
const attrs = { class: 'h' + b.n };
const m = b.n === 2 ? /^(\d+)\./.exec(b.inl.length && b.inl[0].k === 't' ? b.inl[0].s : '') : null;
if (m) attrs.id = 'doc-ch-' + m[1];
root.appendChild(domEl('h' + Math.min(6, b.n + 1), attrs, mdInlineNodes(b.inl, opts)));
} else if (b.t === 'p') {
root.appendChild(domEl('p', null, b.lines.map((l, i) => [i ? domEl('br') : null, mdInlineNodes(l, opts)])));
} else if (b.t === 'quote') {
root.appendChild(domEl('blockquote', null, mdInlineNodes(b.inl, opts)));
} else if (b.t === 'ul') {
root.appendChild(domEl('ul', null, b.items.map((it) => domEl('li', null, mdInlineNodes(it, opts)))));
} else if (b.t === 'code') {
root.appendChild(domEl('pre', null, domEl('code', null, b.text)));
} else if (b.t === 'table') {
const head = domEl('tr', null, b.head.map((c) => domEl('th', mdCellAttrs(c, { scope: 'col' }), mdInlineNodes(c, opts))));
const rows = b.rows.map((r) => domEl('tr', null, b.head.map((_, i) => domEl('td', mdCellAttrs(r[i] || []), mdInlineNodes(r[i] || [], opts)))));
root.appendChild(domEl('div', { class: 'tbl-wrap' }, domEl('table', null, domEl('thead', null, head), domEl('tbody', null, rows))));
}
}
return root;
}
return { renderBlocks };
})();
// ---- ui/tab-doc.js
const __mod_ui_tab_doc = (function () {
'use strict';
const { domEl, domCopy, domScrollToId, domViewport } = __mod_ui_dom;
const { layoutKindFor } = __mod_ui_prefs;
const { docKnownList, docText, docOutline, docFilterRows, docSetRequirement, docSetElement, docSetMode, docSetFilter, docToggle, docSetAll, docClear, docKnownIds } = __mod_ui_doc_state;
const { mdParse, mdCountMarkers } = __mod_ui_markdown;
const { renderBlocks } = __mod_ui_render_md;
const DOC_ROW_LIMIT = 150; // rows rendered per picker group; the filter narrows the rest
const DOC_MODE_KEYS = { requirement: 'doc.mode.requirement', element: 'doc.mode.element', free: 'doc.mode.free' };
const DOC_GROUP_KEYS = { requirements: 'doc.group.requirements', cases: 'doc.group.cases', nodes: 'doc.group.nodes', edges: 'doc.group.edges' };
const DOC_GROUP_TYPES = { requirements: 'requirement', cases: 'case', nodes: 'node', edges: 'edge' };
function docPickerRows(api, rows, group, st, onPick, free) {
const shown = rows.slice(0, DOC_ROW_LIMIT);
const list = shown.map((r) => {
const on = free ? st.ids.includes(r.id) : (st.mode === 'requirement' ? st.reqId === r.id : st.elementId === r.id);
const label = domEl('span', { class: 'pick-text' }, domEl('code', null, r.id), ' ', r.title === r.id ? '' : r.title);
if (free) return domEl('label', { class: 'pick' + (on ? ' on' : '') }, domEl('input', { type: 'checkbox', checked: on ? 'checked' : null, onchange: () => onPick(r.id) }), label);
return domEl('button', { type: 'button', class: 'pick' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', onclick: () => onPick(r.id) }, label);
});
return domEl('div', { class: 'pick-group' }, domEl('h4', null, api.t(DOC_GROUP_KEYS[group]), ' ', domEl('span', { class: 'muted' }, String(rows.length))), list,
rows.length > shown.length ? domEl('p', { class: 'muted' }, api.t('doc.list.more', { shown: shown.length, total: rows.length })) : null);
}
function docControls(host, api) {
const { t, ctx } = api; const st = api.state.doc;
const known = docKnownList(ctx, docKnownIds(ctx));
const set = (next) => api.setDoc(next);
const modes = Object.keys(DOC_MODE_KEYS).map((m) => domEl('button', { type: 'button', 'aria-pressed': st.mode === m ? 'true' : 'false', class: 'seg' + (st.mode === m ? ' on' : ''), onclick: () => set(docSetMode(st, m)) }, t(DOC_MODE_KEYS[m])));
const filter = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'doc-filter', value: st.filter, placeholder: t('doc.filter.placeholder'), 'aria-label': t('doc.filter.label'), oninput: (ev) => set(docSetFilter(st, ev.target.value)) });
const groups = st.mode === 'requirement' ? ['requirements'] : ['requirements', 'cases', 'nodes', 'edges'];
const free = st.mode === 'free';
const pick = (group) => (id) => {
if (free) set(docToggle(st, id));
else if (group === 'requirements') set(docSetRequirement(st, id));
else set(docSetElement(st, id));
};
const body = groups.map((g) => {
if (st.mode === 'element' && g === 'requirements') return null;
return docPickerRows(api, docFilterRows(known[g], st.filter), g, st, pick(g), free);
});
const allRows = ['requirements', 'cases', 'nodes', 'edges'].flatMap((g) => docFilterRows(known[g], st.filter)).map((r) => r.id);
host.appendChild(domEl('div', { class: 'doc-controls' },
domEl('h3', null, t('doc.controls.title')),
domEl('div', { class: 'choices', role: 'group', 'aria-label': t('doc.mode.label') }, modes),
filter,
free ? domEl('div', { class: 'row-actions' },
domEl('button', { type: 'button', onclick: () => set(docSetAll(st, allRows)) }, t('doc.free.all')),
domEl('button', { type: 'button', onclick: () => set(docClear(st)) }, t('doc.free.clear')),
domEl('span', { class: 'muted' }, t('doc.free.count', { n: st.ids.length }))) : null,
body));
}
function docReader(host, api) {
const { t, ctx } = api; const st = api.state.doc;
const res = docText(ctx, { ...st, level: api.state.level });
const head = domEl('div', { class: 'doc-head' });
if (!res.ok) {
host.appendChild(domEl('div', { class: 'doc' }, head, domEl('p', { class: 'empty', role: 'status' }, t('doc.unavailable'))));
return;
}
const outline = docOutline(res.text);
const markers = mdCountMarkers(res.text);
head.appendChild(domEl('div', { class: 'doc-stats', 'data-gate': 'fit' }, t('doc.stats', { level: api.label('levels', api.state.level), bytes: res.bytes, lines: res.lines, targets: res.ids.length })));
head.appendChild(domEl('div', { class: 'row-actions' },
domEl('button', { type: 'button', onclick: () => domCopy(res.text) }, t('doc.copy')),
domEl('button', { type: 'button', 'aria-pressed': api.state.docRaw ? 'true' : 'false', class: api.state.docRaw ? 'on' : null, onclick: () => api.setRaw(!api.state.docRaw) }, t('doc.raw'))));
if (markers) head.appendChild(domEl('p', { class: 'bad', role: 'status' }, t('doc.markers', { n: markers })));
if (ctx.primaryLanguage !== api.state.lang) head.appendChild(domEl('p', { class: 'muted' }, t('doc.language', { lang: ctx.primaryLanguage })));
if (!res.ids.length) head.appendChild(domEl('p', { class: 'muted' }, t('doc.nothing')));
if (layoutKindFor(domViewport().w) === 'overlay') head.appendChild(domEl('p', { class: 'muted note' }, t('doc.panel.hint', { panel: t('hdr.panel.nav') })));
const nav = domEl('nav', { class: 'outline', 'aria-label': t('doc.outline') }, outline.map((c) => domEl('button', { type: 'button', class: 'chip', onclick: () => domScrollToId('doc-ch-' + c.n) }, c.n + '. ' + c.title)));
let body;
if (api.state.docRaw) body = domEl('pre', { class: 'raw', tabindex: '0' }, res.text);
else {
const key = api.state.lang + '|' + res.text;
const cached = api.cache.get('doc');
if (!cached || cached.key !== key) api.cache.set('doc', { key, node: renderBlocks(mdParse(res.text), { t, onDiagram: (id) => api.openUnit(id) }) });
body = api.cache.get('doc').node;
}
host.appendChild(domEl('div', { class: 'doc' }, head, outline.length && !api.state.docRaw ? nav : null, body));
}
const DOC_TAB = { id: '1D', labelKey: 'tab.1d', questionKey: 'ask.1d', inspector: false, controls: docControls, render: docReader };
return { DOC_TAB };
})();
// ---- ui/tab-twod.js
const __mod_ui_tab_twod = (function () {
'use strict';
const { domEl, domSvg, domOn, domSet, domRect, domCapture, domRelease, domClassOn, domLater } = __mod_ui_dom;
const { twodScene, twodFit, twodZoom, twodPan, twodCenterOn, twodViewBoxText, twodFrameLabels, twodScale, twodTextHidden } = __mod_ui_twod_scene;
const { truncateText } = __mod_label_fit;
const { UNASSIGNED_ID } = __mod_model_access;
const TWOD_DEFAULT_PANE = { w: 1000, h: 600 };
const TWOD_ARROW = /^Arrow(Left|Right|Up|Down)$/;
const TWOD_ZOOM_STEP = 1.25;
const TWOD_FOCUS_ZOOM = 3.5; // zoom factor (against the fit view) of "zoom to selection"
const TWOD_DRAG_PX = 3;
const TWOD_EDGE_HIT_LIMIT = 700; // above this many edges the wide invisible hit paths are skipped (the visible curve still takes clicks)
const TWOD_BOARD_KEYS = { canvas: 'twod.board.canvas', diagrams: 'twod.board.diagrams' };
const TWOD_DIAGRAM_TYPE_KEYS = { component: 'twod.type.component', class: 'twod.type.class', state: 'twod.type.state', er: 'twod.type.er', sequence: 'twod.type.sequence' };
function twodFrames(api, scene) {
const g = domSvg('g', { class: 'frames' });
for (const b of scene.buildings) g.appendChild(domSvg('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 10, class: 'frame frame-building' }));
for (const f of scene.floors) { if (f.id !== UNASSIGNED_ID) g.appendChild(domSvg('rect', { x: f.x, y: f.y, width: f.w, height: f.h, rx: 7, class: 'frame frame-floor' })); }
for (const r of scene.rooms) { if (r.id !== UNASSIGNED_ID) g.appendChild(domSvg('rect', { x: r.x, y: r.y, width: r.w, height: r.h, rx: 6, class: 'frame frame-room' })); }
for (const l of twodFrameLabels(scene, api.t('twod.unassigned'))) g.appendChild(domSvg('text', { x: l.x, y: l.y, class: 'frame-label label-' + l.kind, 'data-label': l.kind }, l.text));
return g;
}
function twodEdges(api, scene) {
const g = domSvg('g', { class: 'edges' });
const hit = scene.edges.length <= TWOD_EDGE_HIT_LIMIT;
const pick = (id) => (ev) => { if (api.dragged()) return; ev.stopPropagation(); api.select({ type: 'edge', id }); };
const sel = api.state.sel;
for (const e of scene.edges) {
const cls = 'edge tier-' + e.tier + (e.static ? ' static' : '') + (e.dashed ? ' dashed' : '') + (sel && sel.type === 'edge' && sel.id === e.id ? ' picked' : '');
g.appendChild(domSvg('path', { d: e.d, class: cls, stroke: e.color, fill: 'none', 'data-edge': e.id, onclick: pick(e.id) }));
if (hit) g.appendChild(domSvg('path', { d: e.d, class: 'edge-hit', fill: 'none', onclick: pick(e.id) }));
}
return g;
}
function twodCards(api, scene) {
const g = domSvg('g', { class: 'cards' });
const sel = api.state.sel;
for (const c of scene.cards) {
const on = sel && sel.type === 'node' && sel.id === c.id;
const choose = (ev) => { if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return; if (ev.type === 'click' && api.dragged()) return; ev.stopPropagation(); ev.preventDefault(); api.select({ type: 'node', id: c.id }); };
const kindName = api.label('kinds', c.kind);
g.appendChild(domSvg('g', { class: 'card tier-' + c.tier + (c.match ? '' : ' nomatch') + (on ? ' picked' : ''), tabindex: '0', role: 'button', 'aria-label': c.name + ', ' + kindName, 'aria-pressed': on ? 'true' : 'false', 'data-node': c.id, onclick: choose, onkeydown: choose },
domSvg('rect', { x: c.x, y: c.y, width: c.w, height: c.h, rx: 6, class: 'card-box', stroke: c.color, fill: c.color }),
domSvg('polygon', { points: c.outline, transform: 'translate(' + (c.x + 14) + ' ' + (c.y + c.h / 2) + ')', class: 'badge', fill: c.color }),
domSvg('text', { x: c.x + 28, y: c.y + 19, class: 'card-name' }, truncateText(c.name, c.w - 34, 12)),
domSvg('text', { x: c.x + 28, y: c.y + 34, class: 'card-kind' }, truncateText(kindName, c.w - 34, 10))));
}
return g;
}
function twodCanvas(host, api) {
const { t, ctx } = api; const st = api.state; const tw = st.twod;
const scene = twodScene(ctx, { level: st.level, selection: st.sel, query: tw.query });
const picked = st.sel && st.sel.type === 'node' ? scene.cards.find((c) => c.id === st.sel.id) : null;
let view = tw.view || null;
const lodMemo = api.cache.get('twod-lod');
const svg = domSvg('svg', { class: 'twod' + (lodMemo === true ? ' lod-low' : ''), viewBox: twodViewBoxText(view || scene.viewBox), preserveAspectRatio: 'xMidYMid meet', role: 'group', tabindex: '0', 'aria-label': t('twod.aria') },
twodFrames(api, scene), twodEdges(api, scene), twodCards(api, scene));
const hint = domEl('p', { class: 'lod-hint', role: 'status' }, t('twod.lod.hint'));
const paneEl = domEl('div', { class: 'twod-pane' + (lodMemo === true ? ' lod-low' : '') }, svg, hint);
const paneNow = () => { const r = domRect(svg); return { w: r.width > 50 ? r.width : TWOD_DEFAULT_PANE.w, h: r.height > 50 ? r.height : TWOD_DEFAULT_PANE.h }; };
const fitNow = () => { const p = paneNow(); return twodFit(scene, p.w, p.h); };
const cur = () => view || fitNow();
const lod = () => {
const r = domRect(svg); const sc = twodScale(view || scene.viewBox, r.width, r.height);
if (sc === null) return;
const low = twodTextHidden(sc); api.cache.set('twod-lod', low);
domClassOn(svg, 'lod-low', low); domClassOn(paneEl, 'lod-low', low);
};
const apply = (v) => { view = v; domSet(svg, 'viewBox', twodViewBoxText(v || scene.viewBox)); api.setTwodView(v); lod(); };
const zoomBy = (factor, fx, fy) => apply(twodZoom(cur(), fitNow(), factor, fx, fy));
let drag = null;
domOn(svg, 'wheel', (ev) => { ev.preventDefault(); const r = domRect(svg); zoomBy(ev.deltaY < 0 ? TWOD_ZOOM_STEP : 1 / TWOD_ZOOM_STEP, r.width ? (ev.clientX - r.left) / r.width : 0.5, r.height ? (ev.clientY - r.top) / r.height : 0.5); }, { passive: false });
domOn(svg, 'pointerdown', (ev) => { if (ev.button !== undefined && ev.button !== 0) return; drag = { x: ev.clientX, y: ev.clientY, view: cur(), moved: false, id: ev.pointerId }; });
domOn(svg, 'pointermove', (ev) => {
if (!drag) return;
const dx = ev.clientX - drag.x; const dy = ev.clientY - drag.y;
if (!drag.moved && Math.hypot(dx, dy) < TWOD_DRAG_PX) return;
if (!drag.moved) { drag.moved = true; domCapture(svg, drag.id); }
const p = paneNow();
apply(twodPan(drag.view, dx, dy, p.w, p.h));
});
const end = (ev) => { if (drag) { if (drag.moved) { domRelease(svg, drag.id); api.markDragged(); } drag = null; } if (ev && ev.type === 'click') { /* a click on the empty pane clears nothing: Esc does */ } };
domOn(svg, 'pointerup', end); domOn(svg, 'pointercancel', end);
domOn(svg, 'keydown', (ev) => {
if (ev.target !== svg) return;
const v0 = cur(); const step = v0.w * 0.1;
const dir = TWOD_ARROW.exec(ev.key);
const moves = { Left: [-step, 0], Right: [step, 0], Up: [0, -step], Down: [0, step] };
if (dir) { ev.preventDefault(); apply({ ...v0, x: v0.x + moves[dir[1]][0], y: v0.y + moves[dir[1]][1] }); }
else if (ev.key === '+' || ev.key === '=') zoomBy(TWOD_ZOOM_STEP, 0.5, 0.5);
else if (ev.key === '-') zoomBy(1 / TWOD_ZOOM_STEP, 0.5, 0.5);
else if (ev.key === '0') apply(null);
});
const query = domEl('input', { type: 'search', class: 'filter', 'data-keep': 'twod-query', value: tw.query, placeholder: t('twod.search.placeholder'), 'aria-label': t('twod.search.label'), oninput: (ev) => api.setTwod({ query: ev.target.value }) });
const bar = domEl('div', { class: 'twod-bar' },
domEl('span', { class: 'badge-mode', 'data-gate': 'fit' }, t('twod.mode')),
domEl('div', { class: 'row-actions' },
domEl('button', { type: 'button', 'aria-label': t('twod.zoom.in'), onclick: () => zoomBy(TWOD_ZOOM_STEP, 0.5, 0.5) }, '+'),
domEl('button', { type: 'button', 'aria-label': t('twod.zoom.out'), onclick: () => zoomBy(1 / TWOD_ZOOM_STEP, 0.5, 0.5) }, '\u2212'),
domEl('button', { type: 'button', onclick: () => apply(null) }, t('twod.zoom.fit')),
domEl('button', { type: 'button', disabled: picked ? null : 'disabled', onclick: () => { if (picked) { const f = fitNow(); apply(twodCenterOn(twodZoom(f, f, TWOD_FOCUS_ZOOM, 0.5, 0.5), picked)); } } }, t('twod.zoom.selection'))),
query,
domEl('span', { class: 'muted' }, t('twod.counts', { cards: scene.counts.cards, edges: scene.counts.edges })));
host.appendChild(domEl('div', { class: 'twod-wrap' }, bar,
domEl('p', { class: 'muted note' }, t('twod.note')),
scene.counts.hidden > 0 ? domEl('p', { class: 'muted note' }, t('twod.hidden', { n: scene.counts.hidden, level: api.label('levels', st.level) })) : null,
scene.counts.cards === 0 ? domEl('p', { class: 'empty', role: 'status' }, t('empty.nodes')) : null,
paneEl));
domLater(lod, 0); // the pane has a size once the page holds it: settle the level of detail
}
function twodCoverCounts(covers) {
const n = (v) => new Set(Array.isArray(v) ? v : []).size;
return { nodes: n(covers.nodeIds), edges: n(covers.edgeIds), cases: n(covers.caseIds) };
}
function twodDiagrams(host, api) {
const { t, ctx } = api; const tw = api.state.twod;
const list = ctx.diagrams;
const cur = list.find((d) => d.id === tw.unitId) || null;
const items = list.map((d) => domEl('button', { type: 'button', class: 'pick' + (cur && cur.id === d.id ? ' on' : ''), 'aria-pressed': cur && cur.id === d.id ? 'true' : 'false', onclick: () => api.setTwod({ unitId: d.id }) },
domEl('span', { class: 'pick-text' }, domEl('code', null, String(d.id)), ' ', typeof d.title === 'string' ? d.title : '')));
const covers = cur && cur.covers && typeof cur.covers === 'object' ? cur.covers : {};
const nodeIds = (Array.isArray(covers.nodeIds) ? covers.nodeIds : []).filter((id) => ctx.ix.nodes.has(id)).slice(0, 30);
const detail = cur ? domEl('div', { class: 'diagram' },
domEl('h3', null, typeof cur.title === 'string' ? cur.title : String(cur.id)),
domEl('p', { class: 'muted' }, TWOD_DIAGRAM_TYPE_KEYS[cur.type] ? t(TWOD_DIAGRAM_TYPE_KEYS[cur.type]) : t('twod.type.other'), ' \u00b7 ', t(twodCoverCounts(covers).nodes ? 'twod.diagram.covers' : 'twod.diagram.covers.noparts', twodCoverCounts(covers))),
nodeIds.length ? domEl('div', { class: 'chips' }, nodeIds.map((id) => domEl('button', { type: 'button', class: 'chip', onclick: () => { api.select({ type: 'node', id }); api.setTwod({ board: 'canvas' }); } }, api.title(id)))) : null,
domEl('h4', null, t('twod.diagram.source')),
domEl('p', { class: 'muted note' }, t('twod.diagram.note')),
domEl('pre', { class: 'raw', tabindex: '0' }, typeof cur.mermaid === 'string' ? cur.mermaid : '')) : domEl('p', { class: 'muted' }, t('twod.diagram.pick'));
host.appendChild(domEl('div', { class: 'twod-wrap' },
domEl('span', { class: 'badge-mode', 'data-gate': 'fit' }, t('twod.mode.diagrams')),
list.length ? domEl('div', { class: 'diagram-board' }, domEl('div', { class: 'pick-group' }, items), detail) : domEl('p', { class: 'empty', role: 'status' }, t('twod.diagram.none'))));
}
function twodRender(host, api) {
const tw = api.state.twod;
const tabs = domEl('div', { class: 'choices', role: 'group', 'aria-label': api.t('twod.board.label') }, Object.keys(TWOD_BOARD_KEYS).map((b) => domEl('button', { type: 'button', 'aria-pressed': tw.board === b ? 'true' : 'false', class: 'seg' + (tw.board === b ? ' on' : ''), onclick: () => api.setTwod({ board: b }) }, api.t(TWOD_BOARD_KEYS[b]))));
host.appendChild(tabs);
if (tw.board === 'diagrams') twodDiagrams(host, api); else twodCanvas(host, api);
}
const TWOD_TAB = { id: '2D', labelKey: 'tab.2d', questionKey: 'ask.2d', inspector: true, render: twodRender };
return { TWOD_TAB };
})();
// ---- ui/qa.js
const __mod_ui_qa = (function () {
'use strict';
const { asList, isRecord, own } = __mod_model_access;
const { viewCountBy } = __mod_ui_model_view;
const QA_COUNT_KEYS = ['nodes', 'edges', 'cases', 'interventions', 'requirements', 'tbds', 'sealedCases', 'sealedPairs'];
const QA_ORACLE_CLASSES = ['sealed', 'revised', 'confirmed', 'pending'];
const QA_RULE_LEVELS = ['block', 'error', 'warn', 'info'];
const qaInt = (v) => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);
function qaActualCounts(ctx) {
return { nodes: ctx.nodes.length, edges: ctx.edges.length, cases: ctx.cases.length, interventions: ctx.interventions.length, requirements: ctx.requirements.length, tbds: ctx.tbds.length, sealedCases: ctx.sealedCaseIds.length, sealedPairs: ctx.pairs.length };
}
function qaPickInts(src, keys) {
if (!isRecord(src)) return null;
const out = {}; let any = false;
for (const k of keys) { const v = qaInt(own(src, k)); out[k] = v; if (v !== null) any = true; }
return any ? out : null;
}
function qaReport(ctx, docCheck) {
const actual = qaActualCounts(ctx);
const expected = isRecord(ctx.meta.counts) ? ctx.meta.counts : {};
const counts = QA_COUNT_KEYS.map((key) => {
const want = qaInt(own(expected, key));
return { key, actual: actual[key], expected: want, ok: want === null ? null : want === actual[key] };
});
const qa = isRecord(ctx.meta.qa) ? ctx.meta.qa : {};
const rules = qaPickInts(qa.rules, QA_RULE_LEVELS);
const oracle = qaPickInts(qa.oracle, QA_ORACLE_CLASSES);
const sheet = isRecord(qa.oracle) && typeof qa.oracle.sheet === 'string' ? qa.oracle.sheet : null;
const gate = isRecord(qa.gate) ? { release: typeof qa.gate.release === 'boolean' ? qa.gate.release : null, blockers: qaInt(own(qa.gate, 'blockers')) } : null;
const seen = new Set(); const notes = [];
const take = (list, source) => {
for (const w of asList(list)) {
if (!isRecord(w) || typeof w.code !== 'string') continue;
const key = w.code + '|' + String(w.subject);
if (seen.has(key)) continue;
seen.add(key); notes.push({ code: w.code, subject: String(w.subject === undefined ? '' : w.subject), detail: typeof w.detail === 'string' ? w.detail : '', source });
}
};
take(ctx.meta.warnings, 'build'); take(qa.notes, 'build'); take(ctx.warnings.items, 'view');
const scene = isRecord(ctx.bundle.calibrationHooks) && isRecord(ctx.bundle.calibrationHooks.scene) ? ctx.bundle.calibrationHooks.scene : null;
const sceneRow = scene ? { nodeCount: qaInt(scene.nodeCount), unassigned: qaInt(scene.unassignedCount), ok: qaInt(scene.nodeCount) === ctx.nodes.length && qaInt(scene.unassignedCount) === 0 } : null;
const grades = asList(ctx.vocab.profile.verification).filter(isRecord).slice().sort((a, b) => (Number(b.rank) || 0) - (Number(a.rank) || 0)).map((g) => g.id);
const byGrade = new Map(viewCountBy([...ctx.nodes, ...ctx.edges], 'verification').map((g) => [g.id, g.count]));
const evidence = [...grades.filter((g) => byGrade.has(g)), ...[...byGrade.keys()].filter((g) => !grades.includes(g))].map((id) => ({ id, count: byGrade.get(id) }));
const docs = isRecord(ctx.bundle.fragments) ? ctx.bundle.fragments : {};
return {
counts, countsOk: counts.every((c) => c.ok !== false), mismatches: counts.filter((c) => c.ok === false).length,
evidence, kinds: viewCountBy(ctx.nodes, 'kind'), relations: viewCountBy(ctx.edges, 'relation'),
rules, gate, oracle, sheet, notes, scene: sceneRow,
oracleEmpty: !ctx.hasSealed,
tbds: ctx.tbds.map((t) => ({ id: String(t.id), title: typeof t.title === 'string' ? t.title : '' })),
docs: { fragments: asList(docs.items).length, elements: asList(docs.elements).length },
docCheck: Array.isArray(docCheck) ? { total: docCheck.length, equal: docCheck.filter((d) => d.ok).length, rows: docCheck } : null,
};
}
return { QA_COUNT_KEYS, QA_ORACLE_CLASSES, QA_RULE_LEVELS, qaReport };
})();
// ---- ui/tab-qa.js
const __mod_ui_tab_qa = (function () {
'use strict';
const { domEl } = __mod_ui_dom;
const { qaReport } = __mod_ui_qa;
const { docCheckAll } = __mod_ui_doc_state;
const QA_COUNT_KEYS_I18N = {
nodes: 'qa.count.nodes', edges: 'qa.count.edges', cases: 'qa.count.cases', interventions: 'qa.count.interventions', requirements: 'qa.count.requirements',
tbds: 'qa.count.tbds', sealedCases: 'qa.count.sealedCases', sealedPairs: 'qa.count.sealedPairs',
};
const QA_RULE_KEYS_I18N = { block: 'qa.rules.block', error: 'qa.rules.error', warn: 'qa.rules.warn', info: 'qa.rules.info' };
const QA_ORACLE_KEYS_I18N = { sealed: 'qa.oracle.sealed', revised: 'qa.oracle.revised', confirmed: 'qa.oracle.confirmed', pending: 'qa.oracle.pending' };
const QA_WARN_KEYS_I18N = {
GEMARC_ATLAS_VIEW_001: 'warn.view001', GEMARC_ATLAS_VIEW_002: 'warn.view002', GEMARC_ATLAS_VIEW_003: 'warn.view003', GEMARC_ATLAS_VIEW_004: 'warn.view004',
GEMARC_ATLAS_VIEW_005: 'warn.view005', GEMARC_ATLAS_VIEW_006: 'warn.view006', GEMARC_ATLAS_VIEW_007: 'warn.view007', GEMARC_ATLAS_VIEW_008: 'warn.view008',
GEMARC_ATLAS_VIEW_009: 'warn.view009', GEMARC_ATLAS_VIEW_010: 'warn.view010', 'LB-001': 'warn.lb001', 'RC-001': 'warn.rc001', 'RC-002': 'warn.rc002',
};
const qaSection = (title, ...kids) => domEl('section', { class: 'qa-sec' }, domEl('h3', null, title), kids);
const qaTable = (head, rows) => domEl('div', { class: 'tbl-wrap' }, domEl('table', null, domEl('thead', null, domEl('tr', null, head.map((h) => domEl('th', { scope: 'col' }, h)))), domEl('tbody', null, rows)));
const qaRow = (cells, extra) => domEl('tr', { 'data-qa-row': '1', class: extra || null }, cells.map((c) => domEl('td', null, c)));
const QA_TAB = {
id: 'QA', labelKey: 'tab.qa', questionKey: 'ask.qa', inspector: false,
render(host, api) {
const { t, ctx } = api;
const r = qaReport(ctx, docCheckAll(ctx));
host.appendChild(domEl('div', { class: 'qa' },
qaSection(t('qa.counts.title'),
qaTable([t('qa.col.item'), t('qa.col.model'), t('qa.col.build'), t('qa.col.result')], r.counts.map((c) => qaRow([t(QA_COUNT_KEYS_I18N[c.key]), String(c.actual), c.expected === null ? t('qa.na') : String(c.expected), c.ok === null ? t('qa.na') : c.ok ? t('qa.ok') : t('qa.mismatch')], c.ok === false ? 'bad' : null))),
domEl('p', { class: 'muted note', 'data-qa-row': '1' }, t('qa.counts.note')),
r.mismatches ? domEl('p', { class: 'bad', role: 'status' }, t('qa.counts.mismatch', { n: r.mismatches })) : null),
qaSection(t('qa.rules.title'),
r.rules ? qaTable([t('qa.col.item'), t('qa.col.count')], Object.keys(QA_RULE_KEYS_I18N).filter((k) => r.rules[k] !== null).map((k) => qaRow([t(QA_RULE_KEYS_I18N[k]), String(r.rules[k])]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.rules.none')),
r.gate ? domEl('p', { 'data-qa-row': '1' }, r.gate.release === null ? t('qa.gate.unknown') : r.gate.release ? t('qa.gate.release.ok') : t('qa.gate.release.blocked', { n: r.gate.blockers === null ? 0 : r.gate.blockers })) : null),
qaSection(t('qa.oracle.title'),
r.oracleEmpty ? domEl('p', { class: 'empty', 'data-qa-row': '1' }, t('empty.expectation')) : null,
r.oracle ? qaTable([t('qa.col.item'), t('qa.col.count')], Object.keys(QA_ORACLE_KEYS_I18N).filter((k) => r.oracle[k] !== null).map((k) => qaRow([t(QA_ORACLE_KEYS_I18N[k]), String(r.oracle[k])]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.oracle.none')),
r.sheet ? domEl('p', { 'data-qa-row': '1' }, t('qa.oracle.sheet'), ' ', domEl('code', null, r.sheet)) : null),
qaSection(t('qa.warn.title'),
r.notes.length ? qaTable([t('qa.col.code'), t('qa.col.what'), t('qa.col.subject')], r.notes.map((n) => qaRow([domEl('code', null, n.code), QA_WARN_KEYS_I18N[n.code] ? t(QA_WARN_KEYS_I18N[n.code]) : t('warn.other'), n.subject]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.warn.none')),
r.scene && !r.scene.ok ? domEl('p', { class: 'bad', role: 'status', 'data-qa-row': '1' }, t('qa.scene.unassigned', { n: r.scene.unassigned === null ? 0 : r.scene.unassigned })) : null),
qaSection(t('qa.evidence.title'),
r.evidence.length ? qaTable([t('qa.col.grade'), t('qa.col.count')], r.evidence.map((g) => qaRow([g.id ? api.label('verification', g.id) : t('qa.na'), String(g.count)]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.none'))),
qaSection(t('qa.vocab.title'),
qaTable([t('qa.col.kind'), t('qa.col.count')], r.kinds.map((k) => qaRow([api.label('kinds', k.id), String(k.count)]))),
qaTable([t('qa.col.relation'), t('qa.col.count')], r.relations.map((k) => qaRow([api.label('relations', k.id), String(k.count)])))),
qaSection(t('qa.docs.title'),
domEl('p', { 'data-qa-row': '1' }, t('qa.docs.summary', { fragments: r.docs.fragments, elements: r.docs.elements })),
r.docCheck ? domEl('p', { class: r.docCheck.equal === r.docCheck.total ? null : 'bad', role: 'status', 'data-qa-row': '1' }, t('qa.docs.twin', { equal: r.docCheck.equal, total: r.docCheck.total })) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.docs.twin.none'))),
qaSection(t('qa.tbd.title'),
r.tbds.length ? qaTable([t('qa.col.id'), t('qa.col.title')], r.tbds.map((d) => qaRow([domEl('code', null, d.id), d.title]))) : domEl('p', { class: 'muted', 'data-qa-row': '1' }, t('qa.none')))));
},
};
return { QA_TAB };
})();
// ---- ui/scene-model.js
const __mod_ui_scene_model = (function () {
'use strict';
const { shownAtLevel, keepFocus, relevance, own, dict, asList, RETREAT_TARGET, UNASSIGNED_ID, kindInfo, relationInfo, nodeDomain, domainColor } = __mod_model_access;
const { arrangeView, gemOutline } = __mod_layout;
const { STATE_COLOR, NEUTRAL_STATE_COLOR, MARK_COLOR } = __mod_scene_buffers;
const { replayView, stageSubset } = __mod_playback;
const { boxCorners, project, viewProj, vec, fitCamera } = __mod_coords;
const { estimateTextWidth, truncateText } = __mod_label_fit;
const { twodRelevanceSelection } = __mod_ui_twod_scene;
const SCENE_CLIP_PX = 2; // slack of the gate's inside-the-canvas test
const SCENE_LABEL_MAX_W = 220;
const SCENE_LABEL_SIZE = 12;
const SCENE_LABEL_H = 20;
const sceneStageClick = (sel) => (sel && (sel.type === 'node' || sel.type === 'edge') && typeof sel.id === 'string' ? { type: sel.type, id: sel.id } : null);
const framesOf = (boxes) => {
const out = [];
for (const kind of ['buildings', 'floors', 'rooms']) for (const id of Object.keys(boxes[kind] || {})) out.push({ id: kind + ':' + id, kind, box: boxes[kind][id] });
return out;
};
function sceneCompose(ctx, o) {
const stage = o.mode === '4D' || o.mode === '5D';
let rel; let overlay = null; let play = null; let level = o.level;
if (stage) {
if (!o.res || !o.res.run) return null;
level = ctx.levels[ctx.levels.length - 1];
play = replayView({ ix: ctx.ix, tab: o.mode, run: o.res.run, chain: o.res.chain, idx: o.idx, sel: sceneStageClick(o.sel), warnings: ctx.warnings });
if (!play) return null;
rel = play.rel; overlay = play.overlay;
} else {
rel = relevance(ctx.ix, twodRelevanceSelection(ctx, o.sel));
}
const base = shownAtLevel(ctx.model, level, ctx.vocab, ctx.ix);
const vis = keepFocus(base, rel);
const viewId = stage ? 'logical' : (typeof o.viewId === 'string' ? o.viewId : 'logical');
const layout = arrangeView(ctx.model, viewId, { vocab: ctx.vocab, ix: ctx.ix, vis });
let shown;
if (stage) shown = stageSubset(ctx.ix, layout, base, rel);
else shown = { nodeIds: new Set([...vis.nodeIds].filter((id) => layout.shownNodeIds.has(id))), edgeIds: new Set([...vis.edgeIds].filter((id) => layout.shownEdgeIds.has(id) || rel.chosenEdges.has(id))) };
const boxes = { buildings: dict(), floors: dict(), rooms: dict() };
if (o.frames !== false) {
if (stage) {
const rooms = new Set();
for (const id of shown.nodeIds) {
const n = ctx.ix.nodes.get(id);
if (n && typeof n.roomId === 'string' && own(layout.boxes.rooms, n.roomId)) rooms.add(n.roomId);
else if (own(layout.pos, id) && layout.unassigned.includes(id)) rooms.add(UNASSIGNED_ID);
}
for (const id of rooms) boxes.rooms[id] = layout.boxes.rooms[id];
} else {
const rooms = new Set(); const buildings = new Set();
for (const id of shown.nodeIds) {
const n = ctx.ix.nodes.get(id);
const room = n && typeof n.roomId === 'string' ? n.roomId : null;
if (room && own(layout.boxes.rooms, room)) { rooms.add(room); const f = ctx.ix.rooms.get(room); const b = f ? ctx.ix.floors.get(f.floorId) : null; if (b && own(layout.boxes.buildings, b.buildingId)) buildings.add(b.buildingId); }
else if (layout.unassigned.includes(id)) { rooms.add(UNASSIGNED_ID); buildings.add(UNASSIGNED_ID); }
}
for (const [k, b] of Object.entries(layout.boxes.rooms)) if (rooms.has(k) || !ctx.ix.rooms.has(k) && k !== UNASSIGNED_ID) boxes.rooms[k] = b;
for (const [k, b] of Object.entries(layout.boxes.buildings)) if (buildings.has(k) || !ctx.ix.buildings.has(k) && k !== UNASSIGNED_ID) boxes.buildings[k] = b;
for (const [k, b] of Object.entries(layout.boxes.floors)) if (!ctx.ix.floors.has(k) && k !== UNASSIGNED_ID) boxes.floors[k] = b;
}
}
const fitPoints = [];
for (const id of shown.nodeIds) { const p = own(layout.pos, id); if (p) fitPoints.push(p); }
for (const f of framesOf(boxes)) fitPoints.push(...boxCorners(f.box));
if (overlay) for (const x of asList(overlay.overlayNodes)) { const a = own(layout.pos, x.between && x.between[0]); const b = own(layout.pos, x.between && x.between[1]); if (a && b) fitPoints.push(vec.add(vec.lerp(a, b, 0.5), [0, 1.6, 0])); }
const scopeSig = stage ? rel.key : null;
return { mode: o.mode, level, viewId, layout, shown, boxes, rel, overlay, play, scopeSig, fitPoints };
}
function sceneTargets(ctx, composed) {
const out = dict();
for (const n of ctx.nodes) out[n.id] = own(RETREAT_TARGET, composed.rel.nodeTier(n.id)) || RETREAT_TARGET.Context;
return out;
}
const finite = (v) => typeof v === 'number' && Number.isFinite(v);
const SCENE_FIT_PITCHES = [0.5, 0.75, 1.0];
const SCENE_FIT_GAIN = 1.12;
function sceneCoverage(cam, points, aspect) {
const W = 1000 * aspect; const H = 1000; const vp = viewProj(cam, aspect);
const box = sceneBoxOf(points.map((p) => project(vp, p, W, H)));
return box ? (box.w * box.h) / (W * H) : 0;
}
function sceneBestFit(cam, points, aspect, pitches = SCENE_FIT_PITCHES) {
let best = null; let bestCover = -1;
pitches.forEach((pitch, i) => {
const c = fitCamera({ ...cam, pitch }, points, aspect);
const cover = sceneCoverage(c, points, aspect);
if (i === 0 || cover > bestCover * SCENE_FIT_GAIN) { best = c; bestCover = cover; }
});
return best || fitCamera(cam, points, aspect);
}
function sceneBoxOf(points) {
if (!points.length) return null;
let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
for (const p of points) {
if (!p.visible || !finite(p.x) || !finite(p.y)) return null;
x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
}
return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
function sceneProjected(cam, w, h, composed, buffers) {
const vp = viewProj(cam, w / h);
const frames = framesOf(composed.boxes).map((f) => {
const box = sceneBoxOf(boxCorners(f.box).map((c) => project(vp, c, w, h)));
return box ? { id: f.id, kind: f.kind, ...box } : { id: f.id, kind: f.kind, x: 0, y: 0, w: null, h: null };
});
const nodes = [];
const focal = h / 2 / Math.tan(cam.vFov / 2);
for (const n of asList(buffers && buffers.hitNodes)) {
if (!(n.alpha >= 0.3)) continue;
const p = project(vp, n.pos, w, h);
const r = p.visible && p.depth > 0 && finite(n.radius) ? (n.radius * focal) / p.depth : 0;
nodes.push({ id: n.id, x: p.visible ? p.x : null, y: p.visible ? p.y : null, r: finite(r) ? r : 0 });
}
return { frames, nodes };
}
function sceneOccupancy(projected) {
const pts = projected.nodes.filter((n) => finite(n.x) && finite(n.y));
const fr = projected.frames.filter((f) => finite(f.w) && finite(f.h));
if (!pts.length || !fr.length) return null;
const rad = (p) => (finite(p.r) && p.r > 0 ? p.r : 0);
const fx0 = Math.min(...fr.map((f) => f.x)); const fy0 = Math.min(...fr.map((f) => f.y)); const fx1 = Math.max(...fr.map((f) => f.x + f.w)); const fy1 = Math.max(...fr.map((f) => f.y + f.h));
const nx0 = Math.min(...pts.map((p) => p.x - rad(p))); const ny0 = Math.min(...pts.map((p) => p.y - rad(p))); const nx1 = Math.max(...pts.map((p) => p.x + rad(p))); const ny1 = Math.max(...pts.map((p) => p.y + rad(p)));
const fa = (fx1 - fx0) * (fy1 - fy0);
return fa > 0 ? ((nx1 - nx0) * (ny1 - ny0)) / fa : null;
}
function sceneOutside(projected, w, h, tol = SCENE_CLIP_PX) {
const out = (x, y) => !(x >= -tol && x <= w + tol && y >= -tol && y <= h + tol);
return {
frames: projected.frames.filter((f) => !finite(f.w) || out(f.x, f.y) || out(f.x + f.w, f.y + f.h)).map((f) => f.id),
nodes: projected.nodes.filter((n) => !finite(n.x) || out(n.x, n.y)).map((n) => n.id),
};
}
const labelRank = { Hover: 0, Selected: 1, Direct: 2, Reached: 3, Stage: 4 };
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
function sceneLabels(labels, cam, w, h, stateText = (s) => s) {
const vp = viewProj(cam, w / h); const placed = []; const out = [];
const ordered = asList(labels).filter((l) => l && typeof l === 'object' && Array.isArray(l.pos)).sort((p, q) => (labelRank[p.cls] ?? 5) - (labelRank[q.cls] ?? 5) || (p.edge ? 1 : 0) - (q.edge ? 1 : 0));
for (const l of ordered) {
const p = project(vp, l.pos, w, h);
if (!p.visible) continue;
const raw = l.state ? l.text + ' \u00b7 ' + stateText(l.state) : l.text;
const text = truncateText(raw, SCENE_LABEL_MAX_W - 14, SCENE_LABEL_SIZE);
const bw = Math.min(SCENE_LABEL_MAX_W, estimateTextWidth(text, SCENE_LABEL_SIZE) + 14);
const x = Math.max(2, Math.min(w - bw - 2, p.x - bw / 2));
let ok = null;
for (let i = 0; i < 6 && !ok; i++) {
const dy = i === 0 ? 0 : (i % 2 ? 1 : -1) * SCENE_LABEL_H * Math.ceil(i / 2);
const y = Math.max(2, Math.min(h - SCENE_LABEL_H - 2, p.y - 16 + dy));
const box = { x, y, w: bw, h: SCENE_LABEL_H };
if (!placed.some((q) => overlaps(box, q))) ok = box;
}
if (!ok) continue;
placed.push(ok);
out.push({ id: l.id, text, x: ok.x, y: ok.y, w: ok.w, h: ok.h, cls: l.cls, state: l.state || null, mark: l.mark || null, edge: !!l.edge });
}
return out;
}
function sceneLegend(ctx, composed) {
const kinds = new Map(); const relations = new Map();
for (const id of composed.shown.nodeIds) {
const n = ctx.ix.nodes.get(id);
if (!n || kinds.has(n.kind)) continue;
const ki = kindInfo(ctx.vocab, n.kind);
kinds.set(n.kind, { id: n.kind, outline: gemOutline(ki.shape, 8), color: domainColor(ctx.vocab, nodeDomain(n, ctx.ix, ctx.vocab)) });
}
for (const id of composed.shown.edgeIds) {
const e = ctx.ix.edges.get(id);
if (!e || relations.has(e.relation)) continue;
const ri = relationInfo(ctx.vocab, e.relation);
relations.set(e.relation, { id: e.relation, color: ri.color, dashed: ri.trustBoundary, weak: ri.static });
}
const states = new Map(); const marks = new Map();
if (composed.overlay) {
for (const st of Object.values(composed.overlay.nodeStates || {})) if (!states.has(st)) states.set(st, { id: st, color: ctx.vocab.nodeStates.get(st)?.color || own(STATE_COLOR, st) || NEUTRAL_STATE_COLOR });
for (const m of Object.values(composed.overlay.marks || {})) if (!marks.has(m)) marks.set(m, { id: m, color: own(MARK_COLOR, m) || NEUTRAL_STATE_COLOR });
}
return { kinds: [...kinds.values()], relations: [...relations.values()], states: [...states.values()], marks: [...marks.values()] };
}
return { SCENE_CLIP_PX, SCENE_LABEL_MAX_W, SCENE_LABEL_SIZE, SCENE_LABEL_H, sceneStageClick, sceneCompose, sceneTargets, SCENE_FIT_PITCHES, SCENE_FIT_GAIN, sceneCoverage, sceneBestFit, sceneBoxOf, sceneProjected, sceneOccupancy, sceneOutside, sceneLabels, sceneLegend };
})();
// ---- ui/stage-state.js
const __mod_ui_stage_state = (function () {
'use strict';
const { baselineFor, pairRuns, causalStepStops, clampIndex, stepIndex, traceOf } = __mod_playback;
const { asList, isRecord } = __mod_model_access;
const STAGE_TABS = ['3D', '4D', '5D'];
const PLAY_TICK_MS = 900; // autoplay: one step per tick
const PLAY_ROWS_MAX = 120; // event rows listed at once (a window around the current step)
const playInitial = () => ({ caseId: null, changeId: null, idx: -1, playing: false, viewId: 'logical', frames: true, legend: false, diagram: 'sequence' });
function playCases(ctx) {
const out = [];
for (const c of asList(ctx.cases)) {
const run = baselineFor(ctx.runs, c.caseId);
if (!run) continue;
out.push({ caseId: c.caseId, title: typeof c.title === 'string' && c.title ? c.title : c.caseId, category: typeof c.category === 'string' ? c.category : '', length: traceOf(run).length, status: typeof run.status === 'string' ? run.status : 'OK' });
}
return out;
}
const playDiffers = (p) => p.status === 'OK' && p.outcomeClass !== '' && p.outcomeClass !== 'NO_DIFFERENCE';
const playHiddenCount = (ctx) => Math.max(0, asList(ctx.cases).length - playCases(ctx).length);
function playPairs(ctx, caseId) {
const byId = new Map(asList(ctx.interventions).filter((iv) => isRecord(iv)).map((iv) => [iv.interventionId, iv]));
return asList(ctx.pairs).filter((p) => p.caseId === caseId && typeof p.interventionId === 'string').map((p) => {
const iv = byId.get(p.interventionId);
return {
changeId: p.interventionId, type: iv && typeof iv.type === 'string' ? iv.type : '', description: iv && typeof iv.description === 'string' && iv.description ? iv.description : p.interventionId,
status: typeof p.status === 'string' ? p.status : 'OK', outcomeClass: isRecord(p.diff) && typeof p.diff.outcomeClass === 'string' ? p.diff.outcomeClass : '',
};
});
}
function playResolve(ctx, tab, st) {
const cases = playCases(ctx);
const res = { tab, cases, caseId: null, changeId: null, pairs: [], run: null, chain: null, A: null, length: 0, stops: [], reason: null, errors: [], runStatus: null, truncated: false };
if (!ctx.hasCases) { res.reason = 'no-cases'; return res; }
if (tab === '5D' && !ctx.hasInterventions) { res.reason = 'no-interventions'; return res; }
if (!cases.length) { res.reason = 'nothing-sealed'; return res; }
const want = st && typeof st.caseId === 'string' ? st.caseId : null;
const openCase = tab === '5D' ? (cases.find((c) => playPairs(ctx, c.caseId).some(playDiffers)) || cases[0]) : cases[0];
res.caseId = cases.some((c) => c.caseId === want) ? want : openCase.caseId;
let run = null;
if (tab === '5D') {
res.pairs = playPairs(ctx, res.caseId);
if (!res.pairs.length) { res.reason = 'no-pairs'; return res; }
const wantIv = st && typeof st.changeId === 'string' ? st.changeId : null;
res.changeId = res.pairs.some((p) => p.changeId === wantIv) ? wantIv : (res.pairs.find(playDiffers) || res.pairs[0]).changeId;
const pair = pairRuns(ctx.runs, res.caseId, res.changeId);
res.A = pair ? pair.A : baselineFor(ctx.runs, res.caseId);
res.chain = pair ? pair.chain : null;
res.errors = pair ? pair.errors : [];
if (!pair || pair.status !== 'OK' || !pair.B || !res.chain || res.chain.status !== 'OK') { res.reason = 'pair-status'; return res; }
run = pair.B;
} else {
run = baselineFor(ctx.runs, res.caseId);
res.A = run;
}
if (!run || !Array.isArray(run.trace) || !run.trace.every(isRecord)) { res.reason = 'no-run'; return res; } // a trace with a hole in it is not a run the build wrote
res.runStatus = typeof run.status === 'string' ? run.status : 'OK';
res.truncated = run.truncated === true;
res.errors = res.errors.length ? res.errors : asList(run.errors);
if (res.runStatus !== 'OK') { res.reason = 'run-status'; return res; }
res.run = run;
res.length = run.trace.length;
res.stops = tab === '5D' ? causalStepStops(res.chain, res.length) : [];
return res;
}
const playPickCase = (st, caseId) => ({ ...st, caseId, changeId: null, idx: -1, playing: false });
const playPickPair = (st, changeId) => ({ ...st, changeId, idx: -1, playing: false });
const playStop = (st) => (st.playing ? { ...st, playing: false } : st);
function playMove(st, res, action) {
const n = res.length;
let idx = clampIndex(st.idx, n);
if (typeof action === 'number') idx = clampIndex(action, n);
else if (action === 'first') idx = -1;
else if (action === 'last') idx = n - 1;
else if (action === 'prev') idx = stepIndex(idx, n, -1);
else if (action === 'next') idx = stepIndex(idx, n, 1);
else if (action === 'prevCause') idx = stepIndex(idx, n, -1, res.stops);
else if (action === 'nextCause') idx = stepIndex(idx, n, 1, res.stops);
return { ...st, idx, playing: false };
}
function playToggle(st, res) {
if (st.playing) return { ...st, playing: false };
if (!res.length) return st;
return { ...st, playing: true, idx: clampIndex(st.idx, res.length) >= res.length - 1 ? -1 : st.idx };
}
function playTick(st, res) {
if (!st.playing || !res.length) return { ...st, playing: false };
const idx = clampIndex(st.idx, res.length);
const next = stepIndex(idx, res.length, 1, res.tab === '5D' ? res.stops : null);
return { ...st, idx: next, playing: next < res.length - 1 && next !== idx };
}
const nodeName = (ctx, res, id) => {
const n = ctx.ix.nodes.get(id);
if (n && typeof n.name === 'string' && n.name) return n.name;
const el = res.chain && res.chain.root ? res.chain.root.element : null;
if (el && el.id === id && typeof el.name === 'string' && el.name) return el.name;
return typeof id === 'string' ? id : '';
};
function playEventRows(ctx, res, idx) {
const trace = res.run ? res.run.trace : [];
const first = trace.length > PLAY_ROWS_MAX ? Math.max(0, Math.min(trace.length - PLAY_ROWS_MAX, idx - Math.floor(PLAY_ROWS_MAX / 3))) : 0;
return trace.slice(first, first + PLAY_ROWS_MAX).map((ev, k) => ({
index: first + k, from: nodeName(ctx, res, ev.sourceNodeId), to: nodeName(ctx, res, ev.targetNodeId), type: typeof ev.eventType === 'string' ? ev.eventType : '',
outcome: typeof ev.outcome === 'string' ? ev.outcome : '', now: first + k === idx,
}));
}
function playChainRows(ctx, res, idx) {
const steps = asList(res.chain && res.chain.steps).filter(isRecord);
return steps.map((st, k) => ({
key: typeof st.id === 'string' ? st.id : 'step-' + k, side: st.side === 'A' ? 'A' : 'B', role: typeof st.role === 'string' ? st.role : '', at: Number.isFinite(st.at) ? st.at : 0,
from: nodeName(ctx, res, st.sourceNodeId), to: nodeName(ctx, res, st.targetNodeId), outcome: typeof st.outcome === 'string' ? st.outcome : '', why: typeof st.why === 'string' ? st.why : '',
shown: Number.isFinite(st.at) && st.at <= idx, now: st.side === 'B' && st.bIndex === idx,
})).sort((a, b) => a.at - b.at);
}
function playEffectRows(ctx, res) {
return asList(res.chain && res.chain.effects).filter(isRecord).map((e) => ({ nodeId: e.nodeId, name: typeof e.name === 'string' && e.name ? e.name : nodeName(ctx, res, e.nodeId), a: typeof e.a === 'string' ? e.a : '', b: typeof e.b === 'string' ? e.b : '' }));
}
function playChangeSummary(rows, max = 4) {
const list = asList(rows);
return { n: list.length, shown: list.slice(0, max).map((r) => ({ name: r.name, a: r.a || 'Idle', b: r.b || 'Idle' })), more: Math.max(0, list.length - max) };
}
function playExpectedLength(ctx, tab, caseId, changeId) {
const rows = asList(ctx.bundle && ctx.bundle.calibrationHooks && ctx.bundle.calibrationHooks.traceLengths).filter(isRecord);
const hit = rows.find((r) => r.caseId === caseId && (tab === '5D' ? r.interventionId === changeId : r.interventionId === null || r.interventionId === undefined));
return hit && Number.isFinite(hit.length) ? hit.length : null;
}
return { STAGE_TABS, PLAY_TICK_MS, PLAY_ROWS_MAX, playInitial, playCases, playHiddenCount, playPairs, playResolve, playPickCase, playPickPair, playStop, playMove, playToggle, playTick, playEventRows, playChainRows, playEffectRows, playChangeSummary, playExpectedLength };
})();
// ---- ui/tab-slots.js
const __mod_ui_tab_slots = (function () {
'use strict';
const { domEl, domOn, domRect, domCapture, domRelease, domFrame, domCancelFrame, domNow, domPixelRatio, domStillMode, domGlContext, domCanvasPixels, domClear, domPrefersDark, domSet, domSvg, domLater, domQuery } = __mod_ui_dom;
const { newCamera, orbit, pan, zoom, pick, viewProj, cameraPosition, boundsOfPoints } = __mod_coords;
const { makeBuffers, toInstanceBuffers, stepEaseAll } = __mod_scene_buffers;
const { VIEW_IDS, resolveView } = __mod_layout;
const { RETREAT_TARGET, own, dict } = __mod_model_access;
const { roleColor, clampIndex } = __mod_playback;
const { truncateText } = __mod_label_fit;
const { glInit, glDraw } = __mod_ui_gl_renderer;
const { sceneCompose, sceneTargets, sceneProjected, sceneLabels, sceneLegend, sceneOutside, sceneBestFit } = __mod_ui_scene_model;
const { playInitial, playResolve, playPickCase, playPickPair, playMove, playToggle, playTick, playEventRows, playChainRows, playEffectRows, playChangeSummary, playHiddenCount, playExpectedLength, playCases, PLAY_TICK_MS } = __mod_ui_stage_state;
const { seqModel, stateMachineFor, stateModel, SEQ_ROW_H, SEQ_HEAD_H, SEQ_COL_W, STATE_W, STATE_H } = __mod_ui_diagram_model;
const { TWOD_TAB } = __mod_ui_tab_twod;
const STAGE_CAMERA = { yaw: 0.18, pitch: 0.5 }; // the first view: nearly frontal and from above, so a row of buildings fills the width of the canvas
const STAGE_ORBIT = 0.006;
const STAGE_PAN = 0.0016;
const STAGE_CLICK_PX = 5;
const STAGE_ARROW = /^Arrow(Left|Right|Up|Down)$/;
const STAGE_MARK_KEYS = { cause: 'mark.cause', effect: 'mark.effect' };
const STAGE_FIT_ASPECT_STEP = 0.01;
function stageState(api) {
let st = api.cache.get('stage-ui');
if (!st) { st = playInitial(); api.cache.set('stage-ui', st); }
return st;
}
function stageSet(api, patch) {
api.cache.set('stage-ui', { ...stageState(api), ...patch });
api.redraw();
}
const stageTheme = (api) => (api.state.theme === 'light' ? 'light' : api.state.theme === 'dark' ? 'dark' : domPrefersDark() ? 'dark' : 'light');
function stageNew(id) {
const canvas = domEl('canvas', { class: 'stage-canvas', 'data-gate': 'canvas', tabindex: '0', role: 'img' });
const labels = domEl('div', { class: 'stage-labels', 'aria-hidden': 'true' });
const wrap = domEl('div', { class: 'stage-view' }, canvas, labels);
return {
id, canvas, labels, wrap, gl: null, rs: null, tried: false, degraded: false, cam: newCamera(STAGE_CAMERA), fitKey: null, aspect: 0, userMoved: false, retreat: dict(), retreatReady: false, scopeSig: null,
composed: null, last: null, hover: null, raf: 0, frames: 0, animating: false, still: true, prevStamp: 0, pointers: new Map(), api: null, wired: false, groups: null,
};
}
function stageGet(api, id) {
const key = 'stage:' + id;
let sc = api.cache.get(key);
if (!sc) { sc = stageNew(id); api.cache.set(key, sc); }
sc.api = api;
return sc;
}
function stageOpenGl(sc) {
if (sc.tried) return;
sc.tried = true;
try {
sc.gl = domGlContext(sc.canvas);
if (!sc.gl) throw new Error('no context'); // diag: caught below, the tab shows the fallback
sc.rs = glInit(sc.gl);
} catch (e) { sc.degraded = true; sc.gl = null; sc.rs = null; }
}
function stageStatusKey(sc) {
if (!sc || sc.degraded) return 'stage.status.degraded';
if (!sc.rs) return 'stage.status.starting';
return sc.rs.info.software ? 'stage.status.software' : 'stage.status.gpu';
}
function stageFit(sc, force) {
const c = sc.composed; const r = domRect(sc.canvas);
if (!c || !(r.width >= 2 && r.height >= 2)) return;
const b = boundsOfPoints(c.fitPoints);
const key = c.mode + '|' + (b ? [...b.min, ...b.max].map((v) => v.toFixed(2)).join(',') : 'none');
const aspect = r.width / r.height;
const aspectChanged = Math.abs(aspect - sc.aspect) > sc.aspect * STAGE_FIT_ASPECT_STEP;
if (force || sc.fitKey !== key || (aspectChanged && !sc.userMoved)) {
sc.cam = sceneBestFit(sc.cam, c.fitPoints, aspect);
sc.fitKey = key; sc.aspect = aspect; sc.userMoved = false;
}
}
function stageSnap(api, sc) {
const c = sc.composed; const ctx = api.ctx;
const targets = sceneTargets(ctx, c);
const changedStage = sc.scopeSig !== c.scopeSig;
if (!sc.retreatReady || sc.still || changedStage) {
for (const n of ctx.nodes) { const t = own(targets, n.id) || RETREAT_TARGET.Context; sc.retreat[n.id] = { alpha: t.alpha, push: t.push }; }
sc.retreatReady = true; sc.animating = false;
} else sc.animating = true;
sc.scopeSig = c.scopeSig; sc.targets = targets;
}
function stageDraw(sc) {
const api = sc.api; const ctx = api.ctx; const c = sc.composed;
if (!c || !sc.rs) return null;
const r = domRect(sc.canvas); const w = Math.floor(r.width); const h = Math.floor(r.height);
if (!(w >= 2 && h >= 2)) return null;
const dpr = domPixelRatio();
domCanvasPixels(sc.canvas, Math.floor(w * dpr), Math.floor(h * dpr));
const eye = cameraPosition(sc.cam);
const buffers = makeBuffers(ctx.model, {
positions: c.layout.pos, shownNodeIds: c.shown.nodeIds, shownEdgeIds: c.shown.edgeIds, boxes: c.boxes, showFrames: true, relevance: c.rel, retreat: sc.retreat, overlay: c.overlay || {},
eye, hover: sc.hover, vocab: ctx.vocab, markPrefix: { cause: api.t('mark.cause') + ' ', effect: api.t('mark.effect') + ' ' }, edgeStyle: 'relation',
}, ctx.ix);
sc.frames += 1;
const clock = sc.animating || !sc.still ? domNow() / 1000 : sc.frames / 60;
const calls = glDraw(sc.rs, { buffers, groups: toInstanceBuffers(buffers.instances), vp: viewProj(sc.cam, w / h), eye, width: sc.canvas.width, height: sc.canvas.height, theme: stageTheme(api), clock });
sc.last = { buffers, w, h, calls };
stageLabelLayer(sc, w, h);
return sc.last;
}
function stageLabelLayer(sc, w, h) {
domClear(sc.labels);
const placed = sceneLabels(sc.last.buffers.labels, sc.cam, w, h, (s) => sc.api.label('nodeStates', s));
for (const l of placed) {
sc.labels.appendChild(domEl('div', { class: 'slabel cls-' + l.cls + (l.edge ? ' edge' : '') + (l.mark ? ' mark-' + l.mark : ''), style: '--x:' + Math.round(l.x) + 'px;--y:' + Math.round(l.y) + 'px;--w:' + Math.round(l.w) + 'px' }, l.text));
}
}
function stageLoop(sc) {
if (sc.raf) return;
sc.raf = domFrame((ts) => {
sc.raf = 0;
const dt = sc.prevStamp ? Math.min(0.05, (ts - sc.prevStamp) / 1000) : 0.016;
sc.prevStamp = ts;
let moving = false;
if (sc.animating && sc.composed) {
const ids = sc.api.ctx.nodes.map((n) => n.id);
moving = stepEaseAll(sc.retreat, (id) => sc.composed.rel.nodeTier(id), ids, dt);
if (!moving) sc.animating = false;
}
try { stageDraw(sc); } catch (e) { sc.api.boundary.report('stage', e); }
if (moving) stageLoop(sc); else sc.prevStamp = 0;
});
}
function stageRequest(sc) { stageLoop(sc); }
function stageMount(api, sc, composed) {
sc.composed = composed; sc.still = domStillMode();
domSet(sc.canvas, 'aria-label', api.t('stage.canvas.aria'));
stageWire(sc);
stageOpenGl(sc);
if (!sc.rs) return;
stageFit(sc, false);
stageSnap(api, sc);
stageDraw(sc);
if (sc.animating) stageLoop(sc);
}
function stageHit(sc, x, y) {
const b = sc.last && sc.last.buffers;
if (!b) return null;
return pick(sc.cam, { nodes: b.hitNodes.filter((n) => n.alpha > 0.3), edges: b.hitEdges }, x, y, sc.last.w, sc.last.h, 6);
}
const stageLocal = (sc, ev) => { const r = domRect(sc.canvas); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };
function stageWire(sc) {
if (sc.wired) return;
sc.wired = true;
const c = sc.canvas;
const move = (cam) => { sc.cam = cam; sc.userMoved = true; stageRequest(sc); };
domOn(c, 'contextmenu', (ev) => ev.preventDefault());
domOn(c, 'pointerdown', (ev) => {
domCapture(c, ev.pointerId);
sc.pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY, button: ev.button || 0, shift: !!ev.shiftKey });
});
domOn(c, 'pointermove', (ev) => {
const p = sc.pointers.get(ev.pointerId);
if (!p) {
const q = stageLocal(sc, ev); const hit = stageHit(sc, q.x, q.y); const id = hit && hit.type === 'node' ? hit.id : null;
if (id !== sc.hover) { sc.hover = id; stageRequest(sc); }
return;
}
const dx = ev.clientX - p.x; const dy = ev.clientY - p.y; p.x = ev.clientX; p.y = ev.clientY;
if (p.button === 2 || p.shift) move(pan(sc.cam, -dx * sc.cam.dist * STAGE_PAN, dy * sc.cam.dist * STAGE_PAN));
else move(orbit(sc.cam, dx * STAGE_ORBIT, dy * STAGE_ORBIT));
});
const up = (ev) => {
const p = sc.pointers.get(ev.pointerId);
sc.pointers.delete(ev.pointerId); domRelease(c, ev.pointerId);
if (!p || p.button !== 0 || Math.hypot(ev.clientX - p.sx, ev.clientY - p.sy) >= STAGE_CLICK_PX) return;
const q = stageLocal(sc, ev); const hit = stageHit(sc, q.x, q.y);
if (hit) sc.api.select({ type: hit.type, id: hit.id });
};
domOn(c, 'pointerup', up);
domOn(c, 'pointercancel', (ev) => { sc.pointers.delete(ev.pointerId); });
domOn(c, 'wheel', (ev) => { ev.preventDefault(); move(zoom(sc.cam, ev.deltaY > 0 ? 1.1 : 0.9)); }, { passive: false });
domOn(c, 'dblclick', (ev) => {
const q = stageLocal(sc, ev); const hit = stageHit(sc, q.x, q.y);
const p = hit && hit.type === 'node' && sc.last ? own(sc.last.buffers.worldPoints, hit.id) : null;
if (p) move({ ...sc.cam, target: p, dist: Math.max(10, sc.cam.dist * 0.6) });
});
domOn(c, 'keydown', (ev) => {
const k = ev.key; const dir = STAGE_ARROW.exec(k);
if (dir) move(orbit(sc.cam, dir[1] === 'Left' ? -0.1 : dir[1] === 'Right' ? 0.1 : 0, dir[1] === 'Up' ? -0.08 : dir[1] === 'Down' ? 0.08 : 0));
else if (k === '+' || k === '=') move(zoom(sc.cam, 0.88));
else if (k === '-') move(zoom(sc.cam, 1.14));
else if (k === '0' || k === 'Home') { stageFit(sc, true); stageRequest(sc); } else return;
ev.preventDefault();
});
}
function stageRelease(api, id) {
const sc = api.cache.get('stage:' + id);
if (sc) { domCancelFrame(sc.raf); sc.raf = 0; }
}
function stageForget(api, id) {
const sc = api.cache.get('stage:' + id);
if (sc) { sc.last = null; sc.composed = null; }
}
function stageBounds(api, id) {
const sc = api.cache.get('stage:' + id);
if (!sc || !sc.last || !sc.composed) return null;
const p = sceneProjected(sc.cam, sc.last.w, sc.last.h, sc.composed, sc.last.buffers);
return { frames: p.frames, nodes: p.nodes, w: sc.last.w, h: sc.last.h, occupancy: id === '3D' };
}
function stagePane(api, id, composed) {
const sc = stageGet(api, id);
sc.composed = composed;
api.mounted(() => stageMount(api, sc, composed));
return sc.wrap;
}
function stageIsDegraded(api, id) {
const sc = stageGet(api, id);
stageOpenGl(sc);
return sc.degraded;
}
function stageStatus(api, id) {
const sc = stageGet(api, id);
stageOpenGl(sc);
return api.t(stageStatusKey(sc));
}
function stageLegendEl(api, composed) {
const lg = sceneLegend(api.ctx, composed);
const row = (swatch, text) => domEl('li', null, swatch, ' ', text);
const shape = (k) => domEl('span', { class: 'lg-shape' }, api.label('kinds', k.id), ' ', domEl('span', { class: 'lg-sw', style: 'background:' + k.color }, ''));
const line = (r) => domEl('span', { class: 'lg-line' + (r.dashed ? ' dashed' : '') + (r.weak ? ' weak' : ''), style: 'border-color:' + r.color }, '');
return domEl('section', { class: 'legend', 'aria-label': api.t('legend.title') },
domEl('h4', null, api.t('legend.title')),
domEl('div', { class: 'legend-cols' },
domEl('ul', null, domEl('li', { class: 'muted' }, api.t('legend.kinds')), lg.kinds.map((k) => domEl('li', null, shape(k)))),
domEl('ul', null, domEl('li', { class: 'muted' }, api.t('legend.relations')), lg.relations.map((r) => row(line(r), api.label('relations', r.id))),
lg.relations.some((r) => r.dashed) ? domEl('li', { class: 'muted' }, api.t('legend.dashed')) : null, lg.relations.some((r) => r.weak) ? domEl('li', { class: 'muted' }, api.t('legend.weak')) : null),
lg.states.length || lg.marks.length ? domEl('ul', null, domEl('li', { class: 'muted' }, api.t('legend.states')),
lg.states.map((s) => row(domEl('span', { class: 'lg-sw', style: 'background:' + s.color }, ''), s.id)),
lg.marks.map((m) => row(domEl('span', { class: 'lg-sw', style: 'background:' + m.color }, ''), (STAGE_MARK_KEYS[m.id] ? api.t(STAGE_MARK_KEYS[m.id]) : m.id)))) : null));
}
function stage3dRender(host, api) {
const ctx = api.ctx; const t = api.t; const st = stageState(api);
stageForget(api, '3D');
if (!ctx.nodes.length) { host.appendChild(domEl('div', { class: 'slot' }, domEl('p', { class: 'empty', role: 'status' }, t('empty.nodes')))); return; }
const composed = sceneCompose(ctx, { mode: '3D', level: api.state.level, viewId: st.viewId, sel: api.state.sel, frames: st.frames });
const sc = stageGet(api, '3D');
stageOpenGl(sc);
if (sc.degraded) {
host.appendChild(domEl('div', { class: 'slot' }, domEl('p', { class: 'degraded', role: 'status', 'data-gate': 'fit' }, t('slot.degraded'))));
TWOD_TAB.render(host, api);
return;
}
const views = VIEW_IDS.map((v) => ({ id: v, profileId: resolveView(v, ctx.vocab).profileId }));
const viewSelect = domEl('select', { 'aria-label': t('stage.view.label'), 'data-keep': 'stage-view', onchange: (ev) => stageSet(api, { viewId: ev.target.value }) },
views.map((v) => domEl('option', { value: v.id, selected: v.id === st.viewId ? 'selected' : null }, api.label('views', v.profileId))));
const toggle = (on, key, patch) => domEl('button', { type: 'button', 'aria-pressed': on ? 'true' : 'false', class: on ? 'on' : null, onclick: () => stageSet(api, patch) }, t(key));
const bar = domEl('div', { class: 'stage-bar' },
domEl('span', { class: 'badge-mode', 'data-gate': 'fit' }, stageStatus(api, '3D')),
viewSelect,
toggle(st.frames, 'stage.frames', { frames: !st.frames }),
toggle(st.legend, 'stage.legend', { legend: !st.legend }),
domEl('button', { type: 'button', onclick: () => { const s = stageGet(api, '3D'); stageFit(s, true); stageRequest(s); } }, t('stage.fit')),
domEl('span', { class: 'muted' }, t('stage.counts', { nodes: composed.shown.nodeIds.size, edges: composed.shown.edgeIds.size })));
host.appendChild(domEl('div', { class: 'stage-wrap' }, bar, domEl('p', { class: 'muted note' }, t('stage.note.3d')), stagePane(api, '3D', composed), st.legend ? stageLegendEl(api, composed) : null));
}
const STAGE_TAB_3D = { id: '3D', labelKey: 'tab.3d', questionKey: 'ask.3d', inspector: true, render: stage3dRender, sceneBounds: (api) => stageBounds(api, '3D'), gateSteps: (api) => stage3dSteps(api) };
function stage3dSteps(api) {
return [{
name: 'scene-3d',
run: () => {
if (!api.ctx.nodes.length) return true;
const before = api.state.tab;
if (before !== '3D') api.setTab('3D');
try {
const sc = api.cache.get('stage:3D');
if (!sc) return 'the 3D stage was not created'; // diag: author-facing
if (sc.degraded) return true; // a missing context is shown by its own DEGRADED line and is not a failure of the scene
if (!sc.last || sc.last.calls < 1) return 'the 3D scene issued no draw call'; // diag: author-facing
const status = stageStatus(api, '3D');
return status.length > 0 || 'the 3D status line is empty'; // diag: author-facing
} finally { if (api.state.tab !== before) api.setTab(before); }
},
}];
}
const PLAY_EMPTY_KEYS = { 'no-cases': 'empty.cases', 'nothing-sealed': 'empty.expectation', 'no-interventions': 'empty.interventions', 'no-pairs': 'empty.expectation', 'no-run': 'empty.expectation' };
const PLAY_ROLE_KEYS = {
cause: 'role.cause', block: 'role.block', deny: 'role.deny', fail: 'role.fail', timeout: 'role.timeout', reject: 'role.reject', starve: 'role.starve', degrade: 'role.degrade',
retry: 'role.retry', recovery: 'role.recovery', compensation: 'role.compensation', bypass: 'role.bypass', gain: 'role.gain', lost: 'role.lost', effect: 'role.effect',
};
const PLAY_ARROW = ' \u2192 ';
function playAttach(api, tab) {
let st = stageState(api);
if (st.playTab !== tab) { st = { ...st, playTab: tab, idx: -1, playing: false }; api.cache.set('stage-ui', st); }
const sel = api.state.sel;
if (sel && sel.type === 'case' && sel.id !== st.seenCase && sel.id !== st.caseId) { st = { ...playPickCase(st, sel.id), seenCase: sel.id, playTab: tab }; api.cache.set('stage-ui', st); }
return st;
}
const playEvents = (api, res, st, idx) => playEventRows(api.ctx, res, idx).map((r) => domEl('li', { class: 'ev-row' + (r.now ? ' now' : '') },
domEl('button', { type: 'button', 'aria-pressed': r.now ? 'true' : 'false', onclick: () => stageSet(api, playMove(st, res, r.index)) },
domEl('span', { class: 'ev-n' }, String(r.index + 1)), ' ', r.from, PLAY_ARROW, r.to, ' ', domEl('span', { class: 'muted' }, [r.type ? api.label('eventTypes', r.type) : '', r.outcome ? api.label('eventOutcomes', r.outcome) : ''].filter(Boolean).join(' ')))));
function playPickCaseTo(api, id) {
api.cache.set('stage-ui', { ...playPickCase(stageState(api), id), seenCase: id });
api.select({ type: 'case', id });
}
function playSelects(api, res, st, tab) {
const t = api.t;
const caseSel = domEl('select', { 'aria-label': t('play.case.label'), 'data-keep': 'play-case', onchange: (ev) => playPickCaseTo(api, ev.target.value) },
res.cases.map((c) => domEl('option', { value: c.caseId, selected: c.caseId === res.caseId ? 'selected' : null }, c.title + ' (' + c.length + ')')));
const pairSel = tab === '5D' ? domEl('select', { 'aria-label': t('play.change.label'), 'data-keep': 'play-change', onchange: (ev) => stageSet(api, playPickPair(st, ev.target.value)) },
res.pairs.map((p) => domEl('option', { value: p.changeId, selected: p.changeId === res.changeId ? 'selected' : null }, p.description))) : null;
return [caseSel, pairSel];
}
function playDock(api, res, st, idx) {
const t = api.t; const go = (a) => () => stageSet(api, playMove(st, res, a));
const btn = (key, action) => domEl('button', { type: 'button', 'data-keep': 'dock-' + action, onclick: go(action), disabled: res.length ? null : 'disabled' }, t(key));
const play = domEl('button', { type: 'button', 'data-keep': 'dock-toggle', 'aria-pressed': st.playing ? 'true' : 'false', onclick: () => stageSet(api, playToggle(st, res)) }, st.playing ? t('play.pause') : t('play.play'));
const slider = domEl('input', { type: 'range', min: 0, max: res.length, step: 1, value: idx + 1, class: 'dock-slider', 'aria-label': t('play.slider'), 'data-keep': 'dock-slider', oninput: (ev) => stageSet(api, playMove(st, res, Number(ev.target.value) - 1)) });
return domEl('div', { class: 'dock', role: 'group', 'aria-label': t('play.dock') },
domEl('div', { class: 'row-actions' }, btn('play.first', 'first'), btn('play.prev', 'prev'), play, btn('play.next', 'next'), btn('play.last', 'last'),
res.tab === '5D' ? [btn('play.prevCause', 'prevCause'), btn('play.nextCause', 'nextCause')] : null,
domEl('span', { class: 'dock-step', 'data-gate': 'fit', 'data-step': String(idx + 1), 'data-step-count': String(res.length) }, t('play.step', { n: idx + 1, total: res.length }))),
slider);
}
function playNowLine(api, res, idx) {
const t = api.t;
if (idx < 0) return domEl('p', { class: 'now-line muted' }, t('play.start'));
const ev = res.run.trace[idx]; const rows = playEventRows(api.ctx, res, idx).find((r) => r.index === idx);
return domEl('p', { class: 'now-line' }, t('play.event', { n: idx + 1, from: rows ? rows.from : '', to: rows ? rows.to : '', kind: typeof ev.eventType === 'string' && ev.eventType ? api.label('eventTypes', ev.eventType) : '', outcome: typeof ev.outcome === 'string' && ev.outcome ? api.label('eventOutcomes', ev.outcome) : '' }));
}
function playHeadline(api, rows) {
const { t } = api; const sum = playChangeSummary(rows);
const items = sum.shown.map((r) => t('play.change.item', { name: r.name, a: api.label('nodeStates', r.a), b: api.label('nodeStates', r.b) }));
return t('play.headline', { n: sum.n, list: items.join('; ') + (sum.more ? '; ' + t('play.headline.more', { n: sum.more }) : '') });
}
function playCompare(api, res) {
const t = api.t; const rows = playEffectRows(api.ctx, res); const chain = res.chain || {};
const aLen = res.A && Array.isArray(res.A.trace) ? res.A.trace.length : 0;
return domEl('section', { class: 'compare', 'aria-label': t('play.compare') },
domEl('h4', null, t('play.compare')),
rows.length ? domEl('p', { class: 'headline' }, playHeadline(api, rows)) : null,
domEl('p', { class: 'muted' }, t('play.lengths', { a: aLen, b: res.length }), typeof chain.outcomeClass === 'string' && chain.outcomeClass ? ' \u00b7 ' + t('play.outcome', { value: api.label('outcomeClasses', chain.outcomeClass) }) : ''),
rows.length ? domEl('div', { class: 'tbl-wrap' }, domEl('table', { class: 'compare-table' },
domEl('thead', null, domEl('tr', null, domEl('th', null, t('play.compare.node')), domEl('th', null, t('play.compare.a')), domEl('th', null, t('play.compare.b')))),
domEl('tbody', null, rows.map((r) => domEl('tr', null, domEl('td', null, r.name), domEl('td', null, api.label('nodeStates', r.a || 'Idle')), domEl('td', null, api.label('nodeStates', r.b || 'Idle'))))))) : domEl('p', { class: 'muted' }, t('play.compare.none')));
}
function playReason(api, res) {
const t = api.t;
const first = res.reason === 'pair-status' ? t('play.pair.status', { status: res.chain && typeof res.chain.status === 'string' ? res.chain.status : 'DRAFT' }) : t('play.run.status', { status: res.runStatus || 'DRAFT' });
return domEl('div', { class: 'reason', role: 'status' }, domEl('p', { class: 'empty' }, first),
res.chain && typeof res.chain.headline === 'string' && res.chain.headline ? domEl('p', { class: 'headline' }, res.chain.headline) : null,
res.errors.length ? domEl('ul', { class: 'errs' }, res.errors.slice(0, 8).map((e) => domEl('li', null, String(e)))) : null);
}
function playRender(tab) {
return (host, api) => {
const ctx = api.ctx; const t = api.t;
const st = playAttach(api, tab);
stageForget(api, tab);
const res = playResolve(ctx, tab, st);
if (res.reason && PLAY_EMPTY_KEYS[res.reason]) { host.appendChild(domEl('div', { class: 'slot' }, domEl('p', { class: 'empty', role: 'status' }, t(PLAY_EMPTY_KEYS[res.reason])))); return; }
const [caseSel, pairSel] = playSelects(api, res, st, tab);
const hidden = playHiddenCount(ctx);
const head = [caseSel, pairSel, hidden > 0 ? domEl('span', { class: 'muted' }, t('play.hidden', { n: hidden })) : null];
if (res.reason) { host.appendChild(domEl('div', { class: 'stage-wrap' }, domEl('div', { class: 'stage-bar' }, head), playReason(api, res))); return; }
const idx = clampIndex(st.idx, res.length);
const composed = sceneCompose(ctx, { mode: tab, sel: api.state.sel, res, idx, frames: st.frames });
if (!composed) { host.appendChild(domEl('div', { class: 'slot' }, domEl('p', { class: 'empty', role: 'status' }, t('empty.expectation')))); return; }
const bar = domEl('div', { class: 'stage-bar' }, domEl('span', { class: 'badge-mode', 'data-gate': 'fit' }, stageStatus(api, tab)), head,
domEl('button', { type: 'button', 'aria-pressed': st.legend ? 'true' : 'false', class: st.legend ? 'on' : null, onclick: () => stageSet(api, { legend: !st.legend }) }, t('stage.legend')));
const drawn = composed.shown.nodeIds.size > 0 && !stageIsDegraded(api, tab);
host.appendChild(domEl('div', { class: 'stage-wrap' }, bar,
res.truncated ? domEl('p', { class: 'bad', role: 'status' }, t('play.run.truncated')) : null,
drawn ? stagePane(api, tab, composed) : stageIsDegraded(api, tab) ? domEl('p', { class: 'degraded', role: 'status', 'data-gate': 'fit' }, t('play.degraded')) : domEl('p', { class: 'empty', role: 'status' }, t('play.nostage')),
playDock(api, res, st, idx), playNowLine(api, res, idx),
st.legend && drawn ? stageLegendEl(api, composed) : null,
domEl('div', { class: 'play-lower' }, domEl('section', { class: 'events', 'aria-label': t('play.events') }, domEl('h4', null, t('play.events')), domEl('ol', null, playEvents(api, res, st, idx))), tab === '5D' ? playCompare(api, res) : null)));
if (st.playing) api.mounted(() => playSchedule(api, tab));
};
}
function playSchedule(api, tab) {
const gen = (api.cache.get('play-gen') || 0) + 1;
api.cache.set('play-gen', gen);
domLater(() => {
if (api.cache.get('play-gen') !== gen || api.state.tab !== tab) return;
const st = stageState(api);
if (!st.playing) return;
stageSet(api, playTick(st, playResolve(api.ctx, tab, st)));
}, PLAY_TICK_MS);
}
function seqSvg(api, model, res, st) {
const t = api.t;
const kids = [];
for (const c of model.cols) {
kids.push(domSvg('line', { x1: c.x, y1: SEQ_HEAD_H - 4, x2: c.x, y2: model.height, class: 'life' }));
kids.push(domSvg('rect', { x: c.x - SEQ_COL_W / 2 + 6, y: 4, width: SEQ_COL_W - 12, height: SEQ_HEAD_H - 10, rx: 5, class: 'part' }));
kids.push(domSvg('text', { x: c.x, y: 22, 'text-anchor': 'middle', class: 'part-name' }, truncateText(c.name, SEQ_COL_W - 20, 11)));
}
for (const r of model.rows) {
const cls = 'msg' + (r.now ? ' now' : '') + (r.failed ? ' failed' : '') + (r.back ? ' back' : '');
const x1 = r.x1; const x2 = r.self ? r.x1 + 26 : r.x2; const dir = x2 >= x1 ? 1 : -1;
kids.push(domSvg('g', { class: cls, 'data-event': String(r.index), onclick: () => stageSet(api, playMove(st, res, r.index)) },
domSvg('rect', { x: Math.min(x1, x2) - 4, y: r.y - SEQ_ROW_H / 2, width: Math.abs(x2 - x1) + 8, height: SEQ_ROW_H, class: 'hit' }),
domSvg('line', { x1, y1: r.y, x2: r.self ? x1 + 26 : x2, y2: r.y }),
domSvg('polygon', { points: x2 + ',' + r.y + ' ' + (x2 - dir * 7) + ',' + (r.y - 3.5) + ' ' + (x2 - dir * 7) + ',' + (r.y + 3.5) }),
domSvg('text', { x: (x1 + x2) / 2 + (r.self ? 14 : 0), y: r.y - 4, 'text-anchor': 'middle' }, truncateText(String(r.index + 1) + ' ' + [r.type ? api.label('eventTypes', r.type) : '', r.outcome ? api.label('eventOutcomes', r.outcome) : ''].filter(Boolean).join(' '), Math.max(60, Math.abs(x2 - x1) - 8), 10))));
}
return domEl('div', { class: 'diagram-box' }, domEl('div', { class: 'diagram-win' }, domSvg('svg', { class: 'seq', width: model.width, height: model.height, viewBox: '0 0 ' + model.width + ' ' + model.height, role: 'img', 'aria-label': t('diagram.sequence') }, kids)), model.width > 300 ? domEl('p', { class: 'muted note' }, t('diagram.scroll')) : null);
}
function stateSvg(api, m) {
const kids = [domSvg('defs', null, domSvg('marker', { id: 'st-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, domSvg('path', { d: 'M 0 0 L 10 5 L 0 10 z', class: 'st-tip' })))];
for (const tr of m.transitions) {
kids.push(domSvg('path', { d: tr.d, class: 'tr', fill: 'none', 'marker-end': 'url(#st-arrow)' }));
if (tr.label) kids.push(domSvg('text', { x: tr.lx, y: tr.ly, 'text-anchor': 'middle', class: 'tr-label' }, tr.label));
}
for (const s of m.states) {
kids.push(domSvg('g', { class: 'st' + (s.current ? ' cur' : '') + (s.terminal ? ' term' : '') + (s.initial ? ' init' : '') },
domSvg('rect', { x: s.x - s.w / 2, y: s.y - s.h / 2, width: s.w, height: s.h, rx: 8 }),
s.initial ? domSvg('circle', { cx: s.x - s.w / 2 - 8, cy: s.y, r: 4 }) : null,
domSvg('text', { x: s.x, y: s.y + 4, 'text-anchor': 'middle' }, truncateText(api.label('nodeStates', s.id), STATE_W - 12, 11))));
}
return domEl('div', { class: 'diagram-win' }, domSvg('svg', { class: 'statechart', width: m.width, height: m.height, viewBox: '0 0 ' + m.width + ' ' + m.height, role: 'img', 'aria-label': api.t('diagram.state') }, kids));
}
function diagramSide(host, api) {
const t = api.t; const st = stageState(api); const ctx = api.ctx; const tab = api.state.tab;
const play = tab === '4D' || tab === '5D' ? playResolve(ctx, tab, st) : null;
const res = play && play.run ? play : null;
const idx = res ? clampIndex(st.idx, res.length) : -1;
const which = st.diagram === 'state' ? 'state' : 'sequence';
const strip = domEl('div', { class: 'choices', role: 'group', 'aria-label': t('diagram.tab') },
['sequence', 'state'].map((k) => domEl('button', { type: 'button', class: 'seg' + (which === k ? ' on' : ''), 'aria-pressed': which === k ? 'true' : 'false', onclick: () => stageSet(api, { diagram: k }) }, t(k === 'sequence' ? 'diagram.sequence' : 'diagram.state'))));
let body;
if (which === 'sequence') {
if (!res) body = domEl('p', { class: 'empty', role: 'status' }, t('diagram.none.seq'));
else {
const m = seqModel(ctx, res, idx);
body = domEl('div', null, m.rows.length ? seqSvg(api, m, res, st) : domEl('p', { class: 'empty' }, t('diagram.none.seq')), m.cut ? domEl('p', { class: 'muted' }, t('diagram.cut', { shown: m.rows.length, total: m.total, first: m.from + 1 })) : null);
}
} else {
const machine = stateMachineFor(ctx, res, api.state.sel);
body = machine ? stateSvg(api, stateModel(ctx, res, machine, idx)) : domEl('p', { class: 'empty', role: 'status' }, t('diagram.none.state'));
}
host.appendChild(domEl('div', { class: 'diagram-pane' }, strip, body));
}
function chainQuestion(api, res) {
const pair = res.pairs.find((p) => p.changeId === res.changeId); const kase = res.cases.find((c) => c.caseId === res.caseId);
return api.t('chain.question', { change: pair ? pair.description : String(res.changeId), case: kase ? kase.title : String(res.caseId) });
}
function chainTab(host, api) {
const t = api.t; const st = stageState(api);
const res = playResolve(api.ctx, '5D', st);
if (!res.run) { host.appendChild(domEl('p', { class: 'empty', role: 'status' }, t('chain.empty'))); return; }
const idx = clampIndex(st.idx, res.length);
const rows = playChainRows(api.ctx, res, idx);
host.appendChild(domEl('div', { class: 'chain-pane' },
domEl('p', { class: 'muted' }, chainQuestion(api, res)),
rows.length ? domEl('ol', { class: 'chain' }, rows.map((r) => domEl('li', { class: 'chain-row' + (r.shown ? ' shown' : '') + (r.now ? ' now' : '') + (r.side === 'A' ? ' side-a' : '') },
domEl('button', { type: 'button', onclick: () => stageSet(api, playMove(st, res, r.at)) },
domEl('span', { class: 'role', style: 'border-color:' + roleColor(r.role) }, PLAY_ROLE_KEYS[r.role] ? t(PLAY_ROLE_KEYS[r.role]) : r.role), ' ',
domEl('span', { class: 'muted' }, r.side === 'A' ? t('chain.side.a') : t('chain.side.b')), ' ', r.from, PLAY_ARROW, r.to,
r.outcome && api.label('eventOutcomes', r.outcome) !== (PLAY_ROLE_KEYS[r.role] ? t(PLAY_ROLE_KEYS[r.role]) : r.role) ? [' ', domEl('span', { class: 'muted' }, api.label('eventOutcomes', r.outcome))] : null, r.why ? domEl('div', { class: 'why muted' }, r.why) : null)))) : domEl('p', { class: 'muted' }, t('chain.empty')),
rows.some((r) => r.why) ? domEl('p', { class: 'muted note' }, t('chain.why.note')) : null));
}
const STAGE_SIDE_TABS = [
{ id: 'chain', labelKey: 'chain.tab', order: 1, tabs: ['5D'], render: chainTab },
{ id: 'diagram', labelKey: 'diagram.tab', order: 2, tabs: ['3D', '4D', '5D'], render: diagramSide },
];
function playWithTab(api, id, fn) {
if (!api.registry.hasTab(id)) return true;
const before = api.state.tab; const saved = stageState(api); const sel = api.state.sel;
if (before !== id) api.setTab(id);
try { return fn(); } finally {
api.cache.set('stage-ui', saved);
if (api.state.tab !== before) api.setTab(before); else api.redraw();
if (sel !== api.state.sel) api.select(sel);
}
}
function playStepCheck(api, tab, length, label) {
const dock = domQuery('[data-step-count]');
if (!dock) return 'the ' + label + ' tab shows no player'; // diag: author-facing
if (dock.getAttribute('data-step-count') !== String(length)) return 'the step count of the ' + label + ' player differs from the trace length'; // diag: author-facing
const want = Math.min(3, length);
for (let k = 1; k <= want; k++) {
stageSet(api, playMove(stageState(api), playResolve(api.ctx, tab, stageState(api)), 'next'));
const at = domQuery('[data-step]');
if (!at || at.getAttribute('data-step') !== String(k)) return 'stepping the ' + label + ' player did not advance to step ' + k; // diag: author-facing
}
return true;
}
function playSteps4d(api) {
return [{
name: 'case-play',
run: () => {
const cases = playCases(api.ctx);
if (!cases.length) return true;
return playWithTab(api, '4D', () => {
const first = cases[0];
api.cache.set('stage-ui', { ...playPickCase(stageState(api), first.caseId), playTab: '4D', seenCase: first.caseId });
api.select({ type: 'case', id: first.caseId });
const expected = playExpectedLength(api.ctx, '4D', first.caseId, null);
if (expected !== null && expected !== first.length) return 'the embedded run and the recorded trace length differ'; // diag: author-facing
if (first.status !== 'OK') return true;
return playStepCheck(api, '4D', first.length, '4D');
});
},
}];
}
function playSteps5d(api) {
return [{
name: 'pair-play',
run: () => {
const ctx = api.ctx;
const pairs = playCases(ctx).flatMap((c) => playPairsOf(ctx, c.caseId)).filter((p) => p.status === 'OK');
const staged = (p) => {
const r = playResolve(ctx, '5D', { ...playInitial(), caseId: p.caseId, changeId: p.changeId });
const c = r.run ? sceneCompose(ctx, { mode: '5D', sel: null, res: r, idx: -1, frames: true }) : null;
return !!c && c.shown.nodeIds.size > 0;
};
const pair = pairs.find(staged) || pairs[0];
if (!pair) return true;
const wantStage = staged(pair);
return playWithTab(api, '5D', () => {
api.cache.set('stage-ui', { ...playPickPair(playPickCase(stageState(api), pair.caseId), pair.changeId), playTab: '5D', seenCase: pair.caseId });
api.select({ type: 'case', id: pair.caseId });
const res = playResolve(ctx, '5D', stageState(api));
if (!res.run) return 'the sealed comparison has no playable run'; // diag: author-facing
const expected = playExpectedLength(ctx, '5D', pair.caseId, pair.changeId);
if (expected !== null && expected !== res.length) return 'the embedded run and the recorded trace length differ'; // diag: author-facing
if (!domQuery('.compare-table') && playEffectRows(ctx, res).length) return 'the comparison table is missing'; // diag: author-facing
const sc = api.cache.get('stage:5D');
if (wantStage && sc && !sc.degraded) {
if (!sc.last || sc.last.calls < 1) return 'the 5D stage issued no draw call'; // diag: author-facing
const b = stageBounds(api, '5D'); const out = b ? sceneOutside(b, b.w, b.h) : { frames: [], nodes: [] };
if (out.frames.length || out.nodes.length) return 'the 5D stage leaves its canvas'; // diag: author-facing
}
return playStepCheck(api, '5D', res.length, '5D');
});
},
}];
}
const playPairsOf = (ctx, caseId) => ctx.pairs.filter((p) => p.caseId === caseId).map((p) => ({ caseId, changeId: p.interventionId, status: p.status || 'OK' }));
const STAGE_TAB_4D = { id: '4D', labelKey: 'tab.4d', questionKey: 'ask.4d', inspector: true, render: playRender('4D'), sceneBounds: (api) => stageBounds(api, '4D'), gateSteps: playSteps4d };
const STAGE_TAB_5D = { id: '5D', labelKey: 'tab.5d', questionKey: 'ask.5d', inspector: true, render: playRender('5D'), sceneBounds: (api) => stageBounds(api, '5D'), gateSteps: playSteps5d };
return { stageState, stageSet, stageStatusKey, stageRelease, stageForget, stageBounds, stagePane, stageIsDegraded, stageStatus, stageLegendEl, STAGE_TAB_3D, STAGE_SIDE_TABS, STAGE_TAB_4D, STAGE_TAB_5D };
})();
// ---- ui/main.js
const __mod_ui_main = (function () {
'use strict';
const { createApp } = __mod_ui_shell;
const { domReadSlot, domExpose, domSlotArray } = __mod_ui_dom;
const { boundaryRecord } = __mod_ui_boundary;
const { DOC_TAB } = __mod_ui_tab_doc;
const { TWOD_TAB } = __mod_ui_tab_twod;
const { QA_TAB } = __mod_ui_tab_qa;
const { STAGE_TAB_3D, STAGE_TAB_4D, STAGE_TAB_5D, STAGE_SIDE_TABS } = __mod_ui_tab_slots;
const TAB_DEFINITIONS = [DOC_TAB, TWOD_TAB, STAGE_TAB_3D, STAGE_TAB_4D, STAGE_TAB_5D, QA_TAB];
const SIDE_TAB_DEFINITIONS = [...STAGE_SIDE_TABS];
function bootAtlas() {
try {
const app = createApp(domReadSlot('__ATLAS__'), { tabs: TAB_DEFINITIONS, sideTabs: SIDE_TAB_DEFINITIONS });
domExpose('AtlasApp', app);
app.renderOnce();
const gate = domReadSlot('AtlasGate');
if (gate && gate.active && typeof gate.run === 'function') gate.run(app.gateConfig());
return app;
} catch (e) {
domSlotArray('__ATLAS_ERRORS__').push(boundaryRecord('boot', e));
return null;
}
}
return { TAB_DEFINITIONS, SIDE_TAB_DEFINITIONS, bootAtlas };
})();
__mod_ui_main.bootAtlas();
})();
