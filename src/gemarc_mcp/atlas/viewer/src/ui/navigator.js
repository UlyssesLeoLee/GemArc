// Navigator tree (pure): building > floor > room > node for the chosen detail level, optionally narrowed by a filter text. Buildings follow
// the array order of the model, floors run from the highest level down, nodes that resolve to no building form the last, unassigned group.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { shownAtLevel, keepFocus, relevance, UNASSIGNED_ID, asList } from '../model-access.js';
import { floorLevels } from '../layout.js';
import { twodRelevanceSelection } from './twod-scene.js';

const navFold = (s) => String(s === undefined || s === null ? '' : s).normalize('NFKC').toLowerCase();

/**
 * navigatorTree(ctx, level, filter, selection) -> { groups: [{id, name, unassigned, floors: [{id, name, rooms: [{id, name, nodes: [{id, name, kind}]}]}]}], count }
 * `count` is the number of nodes listed. A building, floor or room with no listed node is left out.
 */
export function navigatorTree(ctx, level, filter, selection) {
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
