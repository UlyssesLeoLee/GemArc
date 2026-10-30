// Turns the formal graph + layout + focus + 4D/5D overlay into flat vertex arrays. No GL, no DOM: the renderer only uploads what this
// module returns, so scene content is unit-testable. The overlay protocol (nodeStates, edgePaints, pulses, marks, overlayNodes, overlayEdges)
// is the seam between playback and the renderer; anything that emits it can be drawn.
//
// Vocabulary-driven: gem shape and scale come from the kind table, the base colour from the node's domain (profile data), idle edge colour
// from the relation table, trust-boundary relations are drawn dashed. Unknown values give neutral defaults and a warning, never a throw.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { indexModel, nodesOf, edgesOf, RETREAT_TARGET, stepEase, disperseEase, makeVocab, kindInfo, relationInfo, nodeDomain, domainColor, own, dict, VIEW_WARNING, NEUTRAL_COLOR } from './model-access.js';
import { gemFaces, faceNormal } from './layout.js';
import { edgePath, arrowTip, vec } from './coords.js';

export const HALO_ALPHA = { Running: 0.6, Waiting: 0.35, Degraded: 0.4, Recovered: 0.4, Completed: 0.28, Failed: 0.45, Blocked: 0.45, Skipped: 0.15 };
export const HALO_SIZE = { Running: 1.7, Waiting: 1.45, Degraded: 1.5, Recovered: 1.5, Completed: 1.35, Failed: 1.5, Blocked: 1.5, Skipped: 1.2 };
// colour of the energy moving along an edge: distinct from every node-state hue
export const ENERGY_COLOR = '#ffb02e';
// Default state colours, keyed by the engine's node-state ids; a profile nodeStates[].color overrides them.
export const STATE_COLOR = {
  Running: '#5ac7c3', Waiting: '#f0ba63', Completed: '#74ca9d', Failed: '#f08886', Blocked: '#f08886', Degraded: '#f0ba63', Recovered: '#82adfa', Skipped: '#6c7d97',
};
export const NEUTRAL_STATE_COLOR = '#6c7d97';
export const EDGE_COLOR = { Selected: '#5ac7c3', Direct: '#82adfa', Context: '#40536a', Unrelated: '#40536a', Idle: '#5b7090' };
export const OVERLAY_COLOR = { A: '#82adfa', B: '#f0ba63', forks: '#f08886', blocked: '#f08886', rewritten: '#a78bfa', shared: '#74ca9d' };
export const MARK_COLOR = { cause: '#ff5ad1', effect: '#ffa3e8' };
export const IDLE_EDGE_ALPHA = 0.55;
export const STATIC_EDGE_ALPHA = 0.4; // static relations (no runtime meaning) are drawn weaker when nothing is selected

/** '#rrggbb' or '#rgb' to [r, g, b] in 0..1; anything else gives the neutral colour (never NaN). */
export function colorToRgb(h) {
  let s = typeof h === 'string' ? h.replace('#', '') : '';
  if (/^[0-9a-fA-F]{3}$/.test(s)) s = [...s].map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) s = NEUTRAL_COLOR.replace('#', '');
  return [parseInt(s.slice(0, 2), 16) / 255, parseInt(s.slice(2, 4), 16) / 255, parseInt(s.slice(4, 6), 16) / 255];
}
const mix = (a, b, t) => [a[0] * (1 - t) + b[0] * t, a[1] * (1 - t) + b[1] * t, a[2] * (1 - t) + b[2] * t];

/**
 * Colour of a node state (profile first, then the default table); null for Idle (no ring); an unknown state is neutral with a warning.
 * `ownStates` (optional Set) holds the states of the node's own state machine: a state that is neither a profile state nor a default
 * state but belongs to the machine is a known state without a ring (null, no warning), because the viewer has no colour for it.
 */
export function stateColor(vocab, st, ownStates = null) {
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

// Progress every node's retreat towards the target of its current relevance class. Cancels leftovers by construction:
// the target is recomputed from the *current* class on every call. `startDelay(id)` delays the start of a disperse.
export function stepEaseAll(retreat, classOf, ids, dt, startDelay = null) {
  let moving = false;
  for (const id of ids) {
    const cur = own(retreat, id) || { alpha: 1, push: 0 };
    const next = stepEase(cur, own(RETREAT_TARGET, classOf(id)), dt, startDelay ? startDelay(id) : 0);
    retreat[id] = { alpha: next.alpha, push: next.push, wait: next.wait, goal: next.goal };
    if (!next.done) moving = true;
  }
  return moving;
}

// Where a dispersing node ends up: out of its own building at its own floor height, so it never passes into another floor or building.
// The node leaves through the front or back wall (+-Z, the side facing away from the focus), stays within its building's X span, and widens out.
export const DISPERSE_OUT = 10;
export const DISPERSE_FAN = 10;
export const DISPERSE_MIN = 16;
export function boxOfPoint(p, boxes) {
  const all = Object.values(boxes?.buildings || {});
  const inside = all.find((b) => p[0] >= b.min[0] && p[0] <= b.max[0] && p[1] >= b.min[1] && p[1] <= b.max[1] && p[2] >= b.min[2] && p[2] <= b.max[2]);
  if (inside) return inside;
  let best = null; let bd = Infinity;
  for (const b of all) { const d = Math.max(b.min[0] - p[0], 0, p[0] - b.max[0]) + Math.max(b.min[2] - p[2], 0, p[2] - b.max[2]); if (d < bd) { bd = d; best = b; } }
  return best;
}
export function disperseTarget(p, center, box) {
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
// Offset at disperse progress `push` (0..1), smoothed with a cosine curve so the burst starts and lands gently.
export function disperseOffset(p, center, box, push) {
  if (!push) return [0, 0, 0];
  return vec.scale(vec.sub(disperseTarget(p, center, box), p), disperseEase(push));
}
// Start delay of a disperse: nearer nodes leave first, so the burst widens out outward from the focus in rounds.
export const DISPERSE_RIPPLE = 1.2;
export function disperseDelay(p, center) {
  if (!p || !center) return 0;
  return DISPERSE_RIPPLE * Math.min(1, vec.len(vec.sub(p, center)) / 40);
}
// A node below this alpha is not drawn, picked or connected (e.g. Outside during playback).
export const HIDDEN_BELOW = 0.01;
// Playback names every stage element that is at least this visible (the weakest stage class is 0.35), up to STAGE_LABEL_MAX of them.
export const STAGE_LABEL_ALPHA = 0.3;
export const STAGE_LABEL_MAX = 40;
// Edges keep full strength while both ends are at least as visible as the context backdrop.
export const EDGE_KEEP_ALPHA = RETREAT_TARGET.Context.alpha;
// Playback edges: the current event is bright, steps already passed keep their (causal) colour at reduced strength, the rest of the case is a weak trace.
export function stageEdgeStyle(cls, styled) {
  if (cls === 'Selected') return styled ?? { color: EDGE_COLOR.Selected, alpha: 1 };
  if (cls === 'Direct') return styled ? { color: styled.color, alpha: styled.alpha * 0.85 } : { color: EDGE_COLOR.Direct, alpha: 0.9 };
  if (cls === 'Reached') return styled ? { color: styled.color, alpha: styled.alpha * 0.6 } : { color: EDGE_COLOR.Direct, alpha: 0.45 };
  return { color: EDGE_COLOR.Context, alpha: cls === 'Stage' ? 0.16 : 0 };
}
// Focus centre = mean position of the selected nodes, or of the direct ones when only an edge is selected.
export function focusMiddle(rel, positions) {
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

/**
 * state: { positions, relevance, retreat, overlay, hover, showFrames, boxes, eye, shownNodeIds, shownEdgeIds,
 *          vocab (makeVocab result; neutral when omitted), markPrefix: {cause, effect} (localised label prefixes; empty when omitted),
 *          edgeStyle: 'relation' (idle edges take the relation colour, default) | 'class' (one idle colour) }
 * Returns the flat arrays plus `instances` (one entry per drawn node, for the instanced path, see toInstanceBuffers).
 */
export function makeBuffers(model, state, ix = indexModel(model)) {
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
  // scenario-only nodes: an explicit `pos`, or lifted above the midpoint of the two nodes it sits `between` (skipped if they are not laid out)
  const pos0 = dict(state.positions);
  const extras = [];
  for (const x of overlay.overlayNodes || []) {
    const p = x.pos ?? (x.between && pos0[x.between[0]] && pos0[x.between[1]] ? vec.add(vec.lerp(pos0[x.between[0]], pos0[x.between[1]], 0.5), [0, 1.6, 0]) : null);
    if (p) { pos0[x.id] = p; extras.push(x); }
  }
  const placed = (n) => { if (pos0[n.id]) return true; vocab.warnings.add(VIEW_WARNING.UNPLACED_NODE, n.id, 'visible node has no position: not drawn'); return false; };
  const nodeList = [...entities.filter((n) => visible.has(n.id) && placed(n)),...extras.map((x) => ({ id: x.id, kind: 'Element', name: x.name || x.id, roomId: x.roomId ?? null, props: {}, extra: x }))];
  // back-to-front's order: far to near so translucent gem shapes blend sensibly
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
    // overlay effects ride only on nodes that stand in place and are clearly visible, so a dimming or dispersing node never carries an animation
    const settled = fade.push < 0.02;
    if (ringRgb && a > 0.25 && settled && st !== 'Idle') {
      const rc = [...ringRgb, a];
      // energy halo: soft additive sprite wrapped around the node; size/phase are animated in the shader (playback: the current event only)
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
    // 5D marks: the intervention target carries a line + ring (cause); nodes whose final state differs carry a double ring (effect)
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
    // Playback: every element of the stage is named (past and still-to-come ones included, at their own strength), so a story never contains an anonymous gem.
    const stageLabelled = rel?.stage && a >= STAGE_LABEL_ALPHA;
    const named = hovered || stageLabelled || (rel?.active && (cls === 'Selected' || cls === 'Direct') && a > 0.5 && settled);
    if (named && !(markHex && a > 0.3 && settled && !hovered)) labels.push({ id: n.id, pos: [p[0], p[1] + 0.9 * scale, p[2]], text: n.name, cls: hovered ? 'Hover' : cls, state: st || null });
  }
  // keep the most informative labels when there are too many: the hovered / selected / marked ones first, then direct neighbours
  const PRI = { Hover: 0, Selected: 1, Direct: 2, Reached: 3, Stage: 4 };
  labels.sort((x, y) => (PRI[x.cls] ?? 5) - (PRI[y.cls] ?? 5)); labels.length = Math.min(labels.length, rel?.stage ? STAGE_LABEL_MAX : 28);
  mesh.pos = new Float32Array(mesh.pos); mesh.nrm = new Float32Array(mesh.nrm); mesh.col = new Float32Array(mesh.col);

  // edges (parallel groups are offset sideways; self loops become circles; trust-boundary relations are dashed)
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
      // an edge is never stronger than its weaker end: it dims out with a receding node instead of pointing at an empty spot
      alpha *= Math.min(1, Math.min(fadeOf(e.sourceId), fadeOf(e.targetId)) / EDGE_KEEP_ALPHA);
      if (alpha < HIDDEN_BELOW) return;
      const rgba = [...colorToRgb(color), alpha];
      // offsets are computed on a canonical direction so opposite-direction edges separate instead of overlapping
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
      // the edge being played (or selected) is named at its midpoint
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
  // moving markers (4D event pulses)
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

// --------- instanced path ---------
/** Unit-size triangle template of a shape (positions and flat normal vectors), shared by every instance of that shape. */
export function templateGeometry(shape) {
  const t = faces(shape); const pos = []; const nrm = [];
  for (const { t: tri, n } of t) for (const v of tri) { pos.push(v[0], v[1], v[2]); nrm.push(n[0], n[1], n[2]); }
  return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), faceCount: t.length };
}
/**
 * Groups makeBuffers().instances by shape for instanced drawing: one entry per shape with a shared template and per-instance
 * offset (3), scale (1) and colour (4) arrays. Order inside a shape is far to near; across shapes the groups come in order of first
 * appearance, so translucent blending between different shapes is an approximation of the back-to-front order of the flat mesh.
 */
export function toInstanceBuffers(instances) {
  const groups = new Map();
  for (const it of instances) { if (!groups.has(it.shape)) groups.set(it.shape, []); groups.get(it.shape).push(it); }
  return [...groups.entries()].map(([shape, list]) => {
    const offset = new Float32Array(list.length * 3); const scale = new Float32Array(list.length); const color = new Float32Array(list.length * 4);
    list.forEach((it, i) => { offset.set(it.pos, i * 3); scale[i] = it.scale; color.set(it.color, i * 4); });
    return { shape, count: list.length, template: templateGeometry(shape), offset, scale, color, ids: list.map((it) => it.id) };
  });
}
