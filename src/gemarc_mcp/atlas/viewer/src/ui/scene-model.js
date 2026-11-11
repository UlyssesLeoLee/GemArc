// Scene model (pure): turns the view context plus the stage state into what the renderer draws: the layout of the chosen view, the shown
// nodes and edges, the frames that are drawn, the relevance (selection focus or playback stage), the overlay of node states and causal marks,
// the points the camera must frame, the retreat targets, the projected bounds the gate measures and the label placement. No page, no GL.
//
// The 3D tab shows the level-filtered model with the selection in focus. The 4D and 5D tabs show the stage of one case (or one causal chain)
// at its own playback position: they always use the deepest level (every part of the stage is shown) and draw only the room frames that hold
// a shown part, so the camera can frame everything it draws.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { shownAtLevel, keepFocus, relevance, own, dict, asList, RETREAT_TARGET, UNASSIGNED_ID, kindInfo, relationInfo, nodeDomain, domainColor } from '../model-access.js';
import { arrangeView, gemOutline } from '../layout.js';
import { STATE_COLOR, NEUTRAL_STATE_COLOR, MARK_COLOR } from '../scene-buffers.js';
import { replayView, stageSubset } from '../playback.js';
import { boxCorners, project, viewProj, vec, fitCamera } from '../coords.js';
import { estimateTextWidth, truncateText } from '../label-fit.js';
import { twodRelevanceSelection } from './twod-scene.js';

export const SCENE_CLIP_PX = 2; // slack of the gate's inside-the-canvas test
export const SCENE_LABEL_MAX_W = 220;
export const SCENE_LABEL_SIZE = 12;
export const SCENE_LABEL_H = 20;

/** The click that the playback stage understands: a node or an edge; a case selection is not a focus inside the stage. */
export const sceneStageClick = (sel) => (sel && (sel.type === 'node' || sel.type === 'edge') && typeof sel.id === 'string' ? { type: sel.type, id: sel.id } : null);

const framesOf = (boxes) => {
  const out = [];
  for (const kind of ['buildings', 'floors', 'rooms']) for (const id of Object.keys(boxes[kind] || {})) out.push({ id: kind + ':' + id, kind, box: boxes[kind][id] });
  return out;
};

/**
 * Compose the scene. o: { mode: '3D' | '4D' | '5D', level, viewId, sel, res (playResolve result), idx, frames (draw the frames) }.
 * Returns null when a stage tab has nothing to play. Otherwise
 * { mode, level, viewId, layout, shown: {nodeIds, edgeIds}, boxes, drawBoxes, rel, overlay, play, scopeSig, fitPoints }.
 * `boxes` holds the frames of the layout that are drawn (empty groups when frames are off).
 */
export function sceneCompose(ctx, o) {
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
      // a frame is drawn only where a shown part sits, so a shallow level is not surrounded by empty boxes
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

/** Retreat target (alpha, push) of every node of the model for the current relevance classes, as a prototype-less dictionary. */
export function sceneTargets(ctx, composed) {
  const out = dict();
  for (const n of ctx.nodes) out[n.id] = own(RETREAT_TARGET, composed.rel.nodeTier(n.id)) || RETREAT_TARGET.Context;
  return out;
}

const finite = (v) => typeof v === 'number' && Number.isFinite(v);

/** Camera pitches the fit may choose from (radians, from the first view's pitch up towards a view from above). */
export const SCENE_FIT_PITCHES = [0.5, 0.75, 1.0];
/** A steeper view must show at least this much more of the canvas (area of the framed bounds) than the first one to be chosen. */
export const SCENE_FIT_GAIN = 1.12;

/** Share of a w x h canvas that the projected bounds of `points` cover under `cam` (0 when a point is behind the camera). */
export function sceneCoverage(cam, points, aspect) {
  const W = 1000 * aspect; const H = 1000; const vp = viewProj(cam, aspect);
  const box = sceneBoxOf(points.map((p) => project(vp, p, W, H)));
  return box ? (box.w * box.h) / (W * H) : 0;
}

/**
 * The camera that frames `points` best: the fit of fitCamera for each pitch of `pitches`, and the one that covers most of the canvas (a long thin
 * scene is shown from higher up, so it fills more of the canvas). The first pitch wins unless another covers SCENE_FIT_GAIN times as much. Pure.
 */
export function sceneBestFit(cam, points, aspect, pitches = SCENE_FIT_PITCHES) {
  let best = null; let bestCover = -1;
  pitches.forEach((pitch, i) => {
    const c = fitCamera({ ...cam, pitch }, points, aspect);
    const cover = sceneCoverage(c, points, aspect);
    if (i === 0 || cover > bestCover * SCENE_FIT_GAIN) { best = c; bestCover = cover; }
  });
  return best || fitCamera(cam, points, aspect);
}

/** The 2D bounds of a set of projected points: {x, y, w, h} or null when one is behind the camera or not finite. */
export function sceneBoxOf(points) {
  if (!points.length) return null;
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
  for (const p of points) {
    if (!p.visible || !finite(p.x) || !finite(p.y)) return null;
    x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/**
 * The projected geometry the gate measures, in CSS pixels from the canvas corner, with the camera and aspect that were drawn:
 * { frames: [{id, kind, x, y, w, h}], nodes: [{id, x, y}] }. A frame with a corner behind the camera has no box (`w` and `h` are null, which
 * the gate counts as clipped). Nodes are the drawn ones that are clearly visible (alpha >= 0.3), at their drawn position, with `r` = the projected
 * radius of the shape in pixels (a part is not a point: the occupancy of a scene with one or two parts is the size of the parts over the frames).
 */
export function sceneProjected(cam, w, h, composed, buffers) {
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

/**
 * Area of the node bounds over the area of the frame bounds (the scene occupancy), or null when either set is empty or degenerate. A node counts with its
 * projected radius `r` (missing: 0), so one or two parts have a box of their own size.
 */
export function sceneOccupancy(projected) {
  const pts = projected.nodes.filter((n) => finite(n.x) && finite(n.y));
  const fr = projected.frames.filter((f) => finite(f.w) && finite(f.h));
  if (!pts.length || !fr.length) return null;
  const rad = (p) => (finite(p.r) && p.r > 0 ? p.r : 0);
  const fx0 = Math.min(...fr.map((f) => f.x)); const fy0 = Math.min(...fr.map((f) => f.y)); const fx1 = Math.max(...fr.map((f) => f.x + f.w)); const fy1 = Math.max(...fr.map((f) => f.y + f.h));
  const nx0 = Math.min(...pts.map((p) => p.x - rad(p))); const ny0 = Math.min(...pts.map((p) => p.y - rad(p))); const nx1 = Math.max(...pts.map((p) => p.x + rad(p))); const ny1 = Math.max(...pts.map((p) => p.y + rad(p)));
  const fa = (fx1 - fx0) * (fy1 - fy0);
  return fa > 0 ? ((nx1 - nx0) * (ny1 - ny0)) / fa : null;
}

/** Frames and nodes that lie outside the w x h canvas by more than `tol` px: { frames: [ids], nodes: [ids] }. */
export function sceneOutside(projected, w, h, tol = SCENE_CLIP_PX) {
  const out = (x, y) => !(x >= -tol && x <= w + tol && y >= -tol && y <= h + tol);
  return {
    frames: projected.frames.filter((f) => !finite(f.w) || out(f.x, f.y) || out(f.x + f.w, f.y + f.h)).map((f) => f.id),
    nodes: projected.nodes.filter((n) => !finite(n.x) || out(n.x, n.y)).map((n) => n.id),
  };
}

// ---------- labels ----------
const labelRank = { Hover: 0, Selected: 1, Direct: 2, Reached: 3, Stage: 4 };
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * Place the labels the scene buffers ask for on the canvas: [{id, text, x, y, w, h, cls, state, mark, edge}] in CSS px (x, y = top-left of the pill).
 * Each label is cut to SCENE_LABEL_MAX_W, kept inside the canvas and moved up or down until it clears the labels placed before it; a label
 * that cannot be placed without overlap is dropped (the more important ones are placed first). Estimated text widths, so no measuring context.
 */
export function sceneLabels(labels, cam, w, h, stateText = (s) => s) {
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

// ---------- legend ----------
/**
 * What the legend explains for the scene that is drawn (only what is on screen): kinds with shape outline and colour, relations with colour and
 * line style, and, on the stage tabs, the node states and the causal marks that occur. All from the profile; unknown values are neutral.
 * Returns { kinds: [{id, outline, color}], relations: [{id, color, dashed, weak}], states: [{id, color}], marks: [{id, color}] }.
 */
export function sceneLegend(ctx, composed) {
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
