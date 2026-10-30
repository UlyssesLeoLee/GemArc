// Model access for the Atlas viewer: the only module that knows the field names of the expanded model (gemarc-atlas/1) and the only
// one that reads the vocabulary profile. Everything else asks this module. Pure: no DOM, no WebGL, no throw on unknown values.
//
// Source convention (the release bundler relies on it): single-line `import { a, b } from './x.js';`, and only
// `export const|function|class` declarations (no export lists, no default export).
//
// Vocabulary-driven: kind -> shape/scale/level/lane, relation -> views/static/colour/trust boundary, trust -> domain -> colour,
// all read from the profile that the bundle carries. An unknown value never throws: it yields a neutral default and one warning
// in the shared warnings sink (codes GEMARC_ATLAS_VIEW_0NN).

export const DEFAULT_LEVELS = ['REQ', 'BD', 'DD'];
export const UNASSIGNED_ID = '~unassigned'; // cannot collide with an authored id (those start with an ASCII letter or digit)
export const NEUTRAL_COLOR = '#5b7090';
export const NEUTRAL_SHAPE = 'box';
export const NEUTRAL_SCALE = 1;
export const NEUTRAL_LANE = 1;
export const NEUTRAL_LEVEL = 'DD';
// Views that show every relation; the other views list their relations explicitly in the profile.
export const OPEN_VIEWS = ['logical-view', 'process-view', 'security-view'];

export const VIEW_WARNING = {
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

/** Array or empty array; record test (plain object, not an array). Shared by the other modules so no private helper repeats. */
export const asList = (v) => (Array.isArray(v) ? v : []);
export const isRecord = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
/** Own-property read: an id such as 'constructor' or 'toString' never reaches an inherited member of a plain object. */
export const own = (o, k) => (o !== null && typeof o === 'object' && Object.hasOwn(o, k) ? o[k] : undefined);
/** A fresh object without a prototype: safe to key by any id. */
export const dict = (src = null) => Object.assign(Object.create(null), src);

/** Shared warnings sink. `items` keeps at most WARNING_CAP distinct subjects per code, `suppressed[code]` counts the rest. */
export function makeWarnings() {
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

/** Viewer-side view of the vocabulary profile. `profile` may be null (everything is neutral). Never throws. */
export function makeVocab(profile = null, warnings = makeWarnings()) {
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

// --------- kinds ---------
/** Kind record with neutral fallbacks: { id, known, shape, scale, level, domain, lane, record }. */
export function kindInfo(vocab, kind) {
  const k = vocab?.kinds?.get(kind);
  if (!k) {
    // an unknown kind is reported once; a missing vocabulary (no kinds at all) is a neutral scene, not a defect per node
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
export const shapeOf = (vocab, kind) => kindInfo(vocab, kind).shape;
export const scaleOf = (vocab, kind) => kindInfo(vocab, kind).scale;

// --------- relations ---------
/** Relation record with neutral fallbacks: { id, known, level, runtime, static, color, trustBoundary, views }. */
export function relationInfo(vocab, relation) {
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
export const relationColor = (vocab, relation) => relationInfo(vocab, relation).color;
/** True when `relation` belongs to the view `viewId` (profile data, `views` of the relation). */
export const relationInView = (vocab, relation, viewId) => relationInfo(vocab, relation).views.includes(viewId);

// --------- levels (REQ -> BD -> DD, each level only adds) ---------
export function rankOfLevel(vocab, level) {
  const i = (vocab?.levels || DEFAULT_LEVELS).indexOf(level);
  return Math.max(0, i);
}
export const reachesLevel = (vocab, shown, from) => rankOfLevel(vocab, shown) >= rankOfLevel(vocab, from);
export const knownLevel = (vocab, v) => (vocab?.levels || DEFAULT_LEVELS).includes(v);

// --------- model index ---------
/** Lookup tables over a gemarc-atlas/1 model. Tolerates missing collections. */
export function indexModel(model) {
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
/** The model's collections as arrays (never undefined). */
export const nodesOf = (model) => asList(model?.nodes);
export const edgesOf = (model) => asList(model?.edges);
export const casesOf = (model) => asList(model?.cases);
export const spaceOf = (model) => {
  const s = isRecord(model?.space) ? model.space : {};
  return { buildings: asList(s.buildings), floors: asList(s.floors), rooms: asList(s.rooms) };
};

export function incident(ix, nodeId) { return [...(ix.out.get(nodeId) || []), ...(ix.inc.get(nodeId) || [])]; }
export function neighbours(ix, nodeId) {
  const s = new Set();
  for (const e of incident(ix, nodeId)) { s.add(e.sourceId); s.add(e.targetId); }
  s.delete(nodeId);
  return s;
}
export function roomIdOf(ix, nodeId) { return ix.nodes.get(nodeId)?.roomId ?? null; }
export function floorIdOf(ix, nodeId) { return ix.rooms.get(roomIdOf(ix, nodeId))?.floorId ?? null; }
export function buildingIdOf(ix, nodeId) { return ix.floors.get(floorIdOf(ix, nodeId))?.buildingId ?? null; }
/** { roomId, floorId, buildingId } when the whole chain node -> room -> floor -> building resolves, else null (the node is unplaced). */
export function homeOf(ix, nodeId) {
  const n = ix.nodes.get(nodeId); const r = n ? ix.rooms.get(n.roomId) : null; const f = r ? ix.floors.get(r.floorId) : null; const b = f ? ix.buildings.get(f.buildingId) : null;
  return b ? { roomId: r.id, floorId: f.id, buildingId: b.id } : null;
}

export function membersIn(ix, type, id) {
  const all = nodesOf(ix.model);
  if (type === 'room') return all.filter((n) => n.roomId === id).map((n) => n.id);
  if (type === 'floor') return all.filter((n) => floorIdOf(ix, n.id) === id).map((n) => n.id);
  if (type === 'building') return all.filter((n) => buildingIdOf(ix, n.id) === id).map((n) => n.id);
  return [];
}

/** Which nodes appear in a case ("contextually related"). */
export function casePlayers(ix, caseId) {
  const c = ix.cases.get(caseId);
  const s = new Set();
  if (!c) return s;
  for (const e of asList(c.events)) { s.add(e.from); s.add(e.to); }
  return s;
}

export function fewestHopsPath(ix, fromId, toId) {
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

// --------- node level and domain (model-schema 5.9) ---------
/** The shallowest level at which a node is shown: node.level, else its room layer, else the kind's level (unknown kind: DD). */
export function levelOfNode(node, ix, vocab) {
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
/** The shallowest level at which a relation is shown (unknown relation: DD). */
export const levelOfRelation = (vocab, relation) => relationInfo(vocab, relation).level;

/** Ids of the nodes / edges visible at `level`. A relation appears only when both of its ends do. */
export function shownAtLevel(model, level, vocab, ix = indexModel(model)) {
  const nodeIds = new Set(nodesOf(model).filter((n) => reachesLevel(vocab, level, levelOfNode(n, ix, vocab))).map((n) => n.id));
  const edgeIds = new Set(edgesOf(model).filter((e) => reachesLevel(vocab, level, levelOfRelation(vocab, e.relation)) && nodeIds.has(e.sourceId) && nodeIds.has(e.targetId)).map((e) => e.id));
  return { nodeIds, edgeIds };
}
export const nodeShownAt = (node, level, ix, vocab) => reachesLevel(vocab, level, levelOfNode(node, ix, vocab));

/** A selection / playback focus always wins over the level filter: what the user picked or what is playing stays on screen. */
export function keepFocus(vis, rel) {
  if (!rel?.active) return vis;
  return { nodeIds: new Set([...vis.nodeIds, ...rel.chosenNodes]), edgeIds: new Set([...vis.edgeIds, ...rel.chosenEdges]) };
}

/** Trust value of a node: its own props.trust, else its building's, else the profile default; null when nothing is known. */
export function trustOf(node, ix) {
  const own = node?.props?.trust;
  const viaRoom = node ? ix.buildings.get(ix.floors.get(ix.rooms.get(node.roomId)?.floorId)?.buildingId)?.trust : undefined;
  if (typeof own === 'string' && own) return own;
  return typeof viaRoom === 'string' && viaRoom ? viaRoom : null;
}
/** Domain id of a node: kind domain, else room layer domain, else trust zone domain, else the default trust's domain (never throws). */
export function nodeDomain(node, ix, vocab) {
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
/** Colour of a domain id; unknown or missing domain: neutral colour and one warning. */
export function domainColor(vocab, domain) {
  const d = domain === null || domain === undefined ? null : vocab?.domains?.get(domain);
  if (d && typeof d.color === 'string') return d.color;
  if (domain !== null && domain !== undefined && vocab && vocab.domains.size) vocab.warnings.add(VIEW_WARNING.UNKNOWN_DOMAIN, domain, 'unknown domain: neutral colour');
  return NEUTRAL_COLOR;
}
export const nodeColor = (node, ix, vocab) => domainColor(vocab, nodeDomain(node, ix, vocab));

// --------- focus / context relevance and retreat (pure) ---------
// selection = {type:'node'|'edge'|'room'|'floor'|'building'|'path'|'case', id|ids}
export function relevance(ix, selection) {
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
    // parallel edges between the same endpoints are directly related as well
    for (const o of edgesOf(model)) {
      if (o.id !== eid && ((o.sourceId === e.sourceId && o.targetId === e.targetId) || (o.sourceId === e.targetId && o.targetId === e.sourceId))) nearEdges.add(o.id);
    }
  }
  for (const id of pickedNodes) direct.delete(id);
  const anchor = new Set([...pickedNodes, ...direct]);
  const context = new Set();
  // context stays narrow: the selection's own room and the requirements that name the selection itself (not its neighbours)
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

// Retreat animation: a pure step function so cancel/restart cannot leave leftovers. Only the selection and its direct relations stay sharp;
// context dims in place; unrelated objects dim and disperse. Playback classes (Reached / Stage / Outside) only dim, never move.
export const RETREAT_TARGET = {
  Selected: { alpha: 1, push: 0 }, Direct: { alpha: 1, push: 0 }, Context: { alpha: 0.2, push: 0 }, Unrelated: { alpha: 0.04, push: 1 },
  Reached: { alpha: 0.65, push: 0 }, Stage: { alpha: 0.35, push: 0 }, Outside: { alpha: 0, push: 0 },
};
export const DISPERSE_SEC = 3.2; // one full disperse (or return)
export const FADE_SEC = 2.4; // full dim 1 -> 0
export const disperseEase = (u) => (1 - Math.cos(Math.PI * Math.min(1, Math.max(0, u)))) / 2;
export function stepEase(current = {}, target = RETREAT_TARGET.Context, dt = 0, delay = 0) {
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
