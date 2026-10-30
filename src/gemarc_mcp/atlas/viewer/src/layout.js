// Spatial projection of the formal graph into Building / Floor / Room space, per architecture view, plus the 2D card layout and the
// gem geometry. Pure: only reads the model and the vocabulary; a view changes positions and visibility, never the facts.
//
// Vocabulary-driven: shapes and scales come from the kind table, view membership from `relations[].views`, node filters from `views[].kinds`,
// process lanes from `kinds[].lane`, security zones from the order of the trust table. Nothing here tests an id, a prefix or a room name.
// Building order is array order. A node that does not resolve to a building goes into the unassigned region (id UNASSIGNED_ID).
// Unknown values give neutral defaults and a warning in the shared sink, never an exception.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { vec, boundsOfPoints, boxCorners } from './coords.js';
import { indexModel, nodesOf, edgesOf, casesOf, spaceOf, homeOf, makeVocab, kindInfo, relationInfo, trustOf, nodeDomain, domainColor, own, dict, UNASSIGNED_ID, VIEW_WARNING } from './model-access.js';

export const FLOOR_H = 3.4;
export const SPACING = 1.7;
export const ROOM_GAP = 1.6;
export const BUILDING_GAP = 5;
export const ZONE_GAP = 8;
export const UNKNOWN_ZONE = '~unknown';
export const MAX_FLOOR_LEVEL = 1e6; // a floor level beyond this magnitude is treated as invalid (a Float32 scene would overflow)

const KNOWN_SHAPES = ['hex', 'tri', 'box', 'octa', 'diamond', 'round', 'crown'];
export const gemShapes = () => KNOWN_SHAPES.slice();
export const gemShapeOf = (vocab, kind) => kindInfo(vocab, kind).shape;

// --------- structure helpers ---------
/** Effective level per floor (a Map floorId -> number), finite and unique within a building. Duplicates, non-numbers and levels beyond MAX_FLOOR_LEVEL are moved above the building and warned. */
export function floorLevels(model, vocab = makeVocab(null)) {
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

// Splits the included nodes into rooms that resolve to a building and the unassigned rest (model order kept).
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

// Logical view: building (X) x floor (Y by level) x room (X within the floor) x local grid (X, Z). `shownIds` is the set of nodes laid out;
// rooms, floors and buildings without a member take no space, so a level filter yields a compact scene.
export function placeBuildings(ix, shownIds, vocab = makeVocab(null)) {
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

// --------- views ---------
export const VIEW_IDS = ['logical', 'development', 'dependency', 'process', 'data', 'security'];
const PROFILE_VIEW = { logical: 'logical-view', development: 'development-view', dependency: 'dependency-diagram', process: 'process-view', data: 'data-view', security: 'security-view' };

/** { key, profileId }: key picks the layout algorithm; any other view the profile defines is the logical layout filtered by its kinds and relations. */
export function resolveView(viewId, vocab = makeVocab(null)) {
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
// A relation belongs to a view when its profile record says so. Without any relation vocabulary there is nothing to filter on: all relations belong.
const edgeInView = (vocab, relation, profileId) => (!vocab.relations.size ? true : relationInfo(vocab, relation).views.includes(profileId));

function stackByDepth(edges, nodeIds) {
  const out = new Map([...nodeIds].map((id) => [id, []]));
  for (const e of edges) out.get(e.sourceId).push(e.targetId);
  const layer = new Map(); const state = new Map();
  // iterative depth-first walk: longest path to a sink; a back edge counts as depth 0 so cycles stay cycle-free
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

/**
 * Layout of one view. opts: { vocab, vis: {nodeIds, edgeIds} (level filter, optional) }. Returns
 * { viewId, pos, shownNodeIds, shownEdgeIds, boxes, unassigned } and never throws: an unknown view id falls back to the logical layout with a warning.
 */
export function arrangeView(model, viewId = 'logical', opts = {}) {
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

// Data view: one band per kind of the view (in the view's kind order) plus a band for the nodes that touch them through the view's relations.
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

// Security view: one X zone per trust value (profile order first, then values seen in the model); nodes without any trust value form the last zone.
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

// Interruptible transition: always blends from the *current* rendered positions.
export function blendPositions(from, to, t) {
  const out = dict();
  for (const id of Object.keys(to)) { const a = own(from, id); out[id] = a ? vec.lerp(a, to[id], t) : to[id]; }
  return out;
}

/** Every node position and every frame corner of a layout: the point set the camera has to frame. */
export function layoutPoints(layout) {
  const pts = Object.values(layout.pos || {}).slice();
  for (const group of Object.values(layout.boxes || {})) for (const b of Object.values(group)) pts.push(...boxCorners(b));
  return pts;
}
export const layoutBounds = (layout) => boundsOfPoints(layoutPoints(layout));

// --------- gem geometry ---------
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

// Triangles with a consistent outward orientation: cross(b-a, c-a) points away from the mean point.
function orient(faces) {
  return faces.map(([a, b, c]) => {
    const n = vec.cross(vec.sub(b, a), vec.sub(c, a));
    const m = vec.scale(vec.add(vec.add(a, b), c), 1 / 3);
    return vec.dot(n, m) < 0 ? [a, c, b] : [a, b, c];
  });
}
/** Closed, outward-oriented triangle list of a gem shape; an unknown shape is a plain box. */
export function gemFaces(shape) {
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
export function faceNormal([a, b, c]) { return vec.norm(vec.cross(vec.sub(b, a), vec.sub(c, a))); }

// --------- 2D card layout ---------
export const CARD = { w: 150, h: 44, colGap: 14, rowGap: 14, cols: 3 };
const MAX_ROW = 620; // rooms wrap onto a new line inside a floor beyond this width
const CARD_PAD = 16; const HEAD = 24; const ROOM_GAP_2D = 18; const FLOOR_GAP_2D = 22; const BUILDING_GAP_2D = 40;

// vis: { nodeIds, edgeIds } of the chosen level (omit for everything). Hidden nodes take no space, so each level is laid out tightly.
// opts: { vocab }. Buildings run left to right in array order; nodes that resolve to no building form the last, unassigned building.
export function cardLayout(model, vis = null, opts = {}) {
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

// 2D focus: the selection and its direct relations stay; context cards dim in place; unrelated cards leave their building through the top or
// bottom edge (the side facing away from the focus) and fan out beyond it. Cards start in rounds (nearer first, `delays` in seconds).
// Rooms / floors / buildings left without a related card dim. Context cards stay in place, so every edge still drawn keeps both ends attached.
export const DISPERSE_2D_OUT = 70;
export const DISPERSE_2D_FAN = 140;
export const DISPERSE_2D_RIPPLE = 1.2;
export const DISPERSE_2D_MIN = 300; // every card travels at least this far, even one next to its building's edge
export function cardFocus(L, rel) {
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

// Gem outline for the 2D card badge: polygon points inside a (2r x 2r) box centred on (0, 0). An unknown shape is a diamond.
export function gemOutline(shape, r = 9) {
  const ring = (n, rot = 0, sy = 1) => Array.from({ length: n }, (_, i) => [Math.cos(rot + (i / n) * Math.PI * 2) * r, Math.sin(rot + (i / n) * Math.PI * 2) * r * sy]);
  const pts = {
    hex: ring(6), tri: ring(3, -Math.PI / 2), box: [[-r, -r * 0.7], [r, -r * 0.7], [r, r * 0.7], [-r, r * 0.7]], octa: ring(4, Math.PI / 4),
    diamond: [[0, -r], [r * 0.7, 0], [0, r], [-r * 0.7, 0]], round: ring(8, Math.PI / 8),
    crown: [[-r, r * 0.8], [-r, -r * 0.4], [-r / 2, r * 0.1], [0, -r], [r / 2, r * 0.1], [r, -r * 0.4], [r, r * 0.8]],
  }[shape] || ring(4, Math.PI / 4);
  return pts.map(([x, y]) => `${+x.toFixed(2)},${+y.toFixed(2)}`).join(' ');
}
