// 2D tab: a flat SVG drawing of the same facts as the 3D scene, shown as its own mode (the toolbar says so): building, floor and room frames,
// one card per node (shape badge from the kind, colour from the domain, label cut to the card), one curve per relation (relation colour,
// dashed across a trust boundary, weaker when static). Pan by dragging, zoom with the wheel or the buttons, search dims the cards that do
// not match, a click selects (the inspector shows the details). A second board lists the diagram units; documents link to them (dg: links).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl, domSvg, domOn, domSet, domRect, domCapture, domRelease, domClassOn, domLater } from './dom.js';
import { twodScene, twodFit, twodZoom, twodPan, twodCenterOn, twodViewBoxText, twodFrameLabels, twodScale, twodTextHidden } from './twod-scene.js';
import { truncateText } from '../label-fit.js';
import { UNASSIGNED_ID } from '../model-access.js';

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
  // the names live in their own group, written after the rectangles; where each goes is decided by twodFrameLabels (no two share a line)
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
  // The fit is the scene bounds: the browser fits and centres them in the pane (preserveAspectRatio meet), so no pane size is needed
  // while the tab is drawn (the host is not in the page yet). A pane size is read only when the user acts, from the live element.
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

/** How many distinct parts, connections and cases a diagram unit covers (a repeated id counts once; a missing list counts 0). */
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

/** The tab definition. */
export const TWOD_TAB = { id: '2D', labelKey: 'tab.2d', questionKey: 'ask.2d', inspector: true, render: twodRender };
