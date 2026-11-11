// 2D tab data (pure): the card layout of the chosen detail level turned into a plain scene description, plus the viewport arithmetic
// (fit, zoom about a point, pan). The tab renders the scene as SVG. This is a flat drawing of the same facts, its own mode: it has no depth
// and no playback; frames show building, floor and room, cards show nodes (shape badge from the kind, colour from the domain).
// Every number in the scene is finite (a damaged bundle gives an empty scene, not NaN).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { shownAtLevel, keepFocus, relevance, asList, UNASSIGNED_ID } from '../model-access.js';
import { cardLayout, gemOutline } from '../layout.js';
import { estimateTextWidth, truncateText } from '../label-fit.js';

export const TWOD_ZOOM_MIN = 0.05;
export const TWOD_ZOOM_MAX = 6;
const twodFin = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);

/** Selection record of the UI -> the selection the relevance function understands (a requirement selects its nodes as a path). */
export function twodRelevanceSelection(ctx, sel) {
  if (!sel || typeof sel.type !== 'string') return null;
  if (sel.type === 'requirement') {
    const r = ctx.requirements.find((x) => x.id === sel.id);
    return r ? { type: 'path', ids: asList(r.nodeIds).filter((id) => ctx.ix.nodes.has(id)) } : null;
  }
  return sel;
}

const twodFold = (s) => String(s === undefined || s === null ? '' : s).normalize('NFKC').toLowerCase();

/**
 * The scene of the 2D tab. opts: { level, selection, query }. Returns
 * { viewBox: {x, y, w, h}, buildings, floors, rooms, cards, edges, unassigned, counts, active }. A room also names its floor (floorId).
 * Card: { id, x, y, w, h, name, kind, color, outline (polygon points of the shape badge), tier, match }.
 * Edge: { id, d, color, dashed, static, tier, sourceId, targetId, relation }.
 */
export function twodScene(ctx, opts) {
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

// ---------- frame labels ----------
export const TWOD_LABEL_SIZE = 11;
export const TWOD_LABEL_ASCENT = 9; // box above the baseline, scene units
export const TWOD_LABEL_DESCENT = 3;
// Baselines, relative to the top of the frame the label names. The card layout leaves 40 units above the first card row of a room: the
// floor name takes the first line inside the first room of the floor, the room name the second line, the cards start below both.
const TWOD_BUILDING_BASE = 16; const TWOD_FLOOR_BASE = 11; const TWOD_ROOM_BASE = 28;
const TWOD_LABEL_PAD = 8; const TWOD_LABEL_MIN_W = 30;

/**
 * Where the names of the frames are written. Returns [{ kind, id, text, x, y, box: {x, y, w, h} }]: x, y are the text start and baseline,
 * `box` is the estimated ink box (the text is already cut to its frame). A building is named at its top edge, a floor in the first line
 * of its first room, a room in the second line of its own header, so no two names share a line. The pseudo floor and room of the
 * unassigned region carry no name. `unassignedText` names the unassigned building.
 */
export function twodFrameLabels(scene, unassignedText) {
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

/** Pairs of labels whose ink boxes overlap by more than `tol` scene units in both directions (the pure twin of the gate's label step). */
export function twodLabelOverlaps(labels, tol = 0.5) {
  const hits = [];
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      const a = labels[i].box; const b = labels[j].box;
      if (Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > tol && Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > tol) hits.push([labels[i], labels[j]]);
    }
  }
  return hits;
}

// ---------- level of detail ----------
/** Below this scale (screen px per scene unit) card names are under 6 px: the text is not drawn, a hint says to zoom in. */
export const TWOD_LOD_SCALE = 0.5;
/** Screen px per scene unit of a view box shown in a pane (the drawing is centred and fitted: the smaller ratio wins). */
export function twodScale(view, paneW, paneH) {
  const w = twodFin(paneW, 0); const h = twodFin(paneH, 0);
  return w > 0 && h > 0 && view.w > 0 && view.h > 0 ? Math.min(w / view.w, h / view.h) : null;
}
export const twodTextHidden = (scale) => typeof scale === 'number' && scale < TWOD_LOD_SCALE;

// ---------- viewport (a view box {x, y, w, h} in scene units) ----------
/** The view box that shows the whole scene, padded, with the aspect ratio of the pane. */
export function twodFit(scene, paneW, paneH) {
  const b = scene.viewBox; const aspect = twodFin(paneW, 1) > 0 && twodFin(paneH, 1) > 0 ? paneW / paneH : 1.6;
  let w = b.w; let h = b.h;
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  return { x: b.x - (w - b.w) / 2, y: b.y - (h - b.h) / 2, w, h };
}

/** Zoom about a point of the scene (fx, fy are fractions 0..1 of the pane); factor > 1 zooms in. Bounded by the limits relative to `fit`. */
export function twodZoom(view, fit, factor, fx, fy) {
  const f = twodFin(factor, 1) > 0 ? twodFin(factor, 1) : 1;
  const px0 = Math.min(1, Math.max(0, twodFin(fx, 0.5))); const py0 = Math.min(1, Math.max(0, twodFin(fy, 0.5)));
  const scale = Math.min(TWOD_ZOOM_MAX, Math.max(TWOD_ZOOM_MIN, (fit.w / view.w) * f));
  const w = fit.w / scale; const h = fit.h / scale;
  const px = view.x + view.w * px0; const py = view.y + view.h * py0;
  return { x: px - w * px0, y: py - h * py0, w, h };
}

/** Pan by a pane-pixel delta. */
export function twodPan(view, dxPx, dyPx, paneW, paneH) {
  const sx = view.w / Math.max(1, twodFin(paneW, 1)); const sy = view.h / Math.max(1, twodFin(paneH, 1));
  return { x: view.x - twodFin(dxPx, 0) * sx, y: view.y - twodFin(dyPx, 0) * sy, w: view.w, h: view.h };
}

/** Centre the view on a card (keeps the zoom). */
export function twodCenterOn(view, card) {
  return { x: card.x + card.w / 2 - view.w / 2, y: card.y + card.h / 2 - view.h / 2, w: view.w, h: view.h };
}

export const twodViewBoxText = (v) => [v.x, v.y, v.w, v.h].map((n) => String(Math.round(n * 100) / 100)).join(' ');
