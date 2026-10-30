// The stage tabs: 3D, 4D and 5D (viewer-B). This file replaces the reserved slots of viewer-A (the name is historical: the structure test of the shell lists
// this path as a module that may build elements, and it is the only such path a new tab can take without editing that test).
//
// Part 1, the stage: one WebGL canvas per spatial tab with an orbit camera, picking, hover, a label layer, the legend and the 3D tab itself. The canvas and
// its GL state live in api.cache and are re-attached on every redraw (the shell rebuilds its tree each time; a new canvas would be a new WebGL context each
// time). The shell calls the mount callback right after the tree is in the page, so the draw happens in the same task as renderOnce: the gate reads the pixels
// and the draw-call counter of that very frame. Motion is optional and never decides a result: under the gate and for visitors who ask for less motion the fade
// and disperse targets are applied at once and the shader clock is the frame count. Without WebGL the tab says so and shows the flat drawing instead.
//
// Part 2, 4D and 5D: playback of SEALED results only, plus the two inspector tabs the stage adds (the causal chain and the diagram windows). 4D plays one sealed
// case event by event; 5D plays the run with one authored change (B) next to the case without it (A) and lists what ended differently. The runs are embedded
// by the build; the browser never simulates. A case, or a pair, that is not in runs.baselines / runs.pairs is not offered, so an unsealed item has no entry, no
// trace and no count here (it shows the empty state).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { domEl, domOn, domRect, domCapture, domRelease, domFrame, domCancelFrame, domNow, domPixelRatio, domStillMode, domGlContext, domCanvasPixels, domClear, domPrefersDark, domSet, domSvg, domLater, domQuery } from './dom.js';
import { newCamera, orbit, pan, zoom, pick, viewProj, cameraPosition, boundsOfPoints } from '../coords.js';
import { makeBuffers, toInstanceBuffers, stepEaseAll } from '../scene-buffers.js';
import { VIEW_IDS, resolveView } from '../layout.js';
import { RETREAT_TARGET, own, dict } from '../model-access.js';
import { roleColor, clampIndex } from '../playback.js';
import { truncateText } from '../label-fit.js';
import { glInit, glDraw } from './gl-renderer.js';
import { sceneCompose, sceneTargets, sceneProjected, sceneLabels, sceneLegend, sceneOutside, sceneBestFit } from './scene-model.js';
import { playInitial, playResolve, playPickCase, playPickPair, playMove, playToggle, playTick, playEventRows, playChainRows, playEffectRows, playChangeSummary, playHiddenCount, playExpectedLength, playCases, PLAY_TICK_MS } from './stage-state.js';
import { seqModel, stateMachineFor, stateModel, SEQ_ROW_H, SEQ_HEAD_H, SEQ_COL_W, STATE_W, STATE_H } from './diagram-model.js';
import { TWOD_TAB } from './tab-twod.js';

// ================ part 1: the stage ================

const STAGE_CAMERA = { yaw: 0.18, pitch: 0.5 }; // the first view: nearly frontal and from above, so a row of buildings fills the width of the canvas
const STAGE_ORBIT = 0.006;
const STAGE_PAN = 0.0016;
const STAGE_CLICK_PX = 5;
const STAGE_ARROW = /^Arrow(Left|Right|Up|Down)$/;
const STAGE_MARK_KEYS = { cause: 'mark.cause', effect: 'mark.effect' };
const STAGE_FIT_ASPECT_STEP = 0.01;

/** The shared stage state of the page (case, change, step, view, frames, legend): one record in api.cache, changed only through stageSet. */
export function stageState(api) {
  let st = api.cache.get('stage-ui');
  if (!st) { st = playInitial(); api.cache.set('stage-ui', st); }
  return st;
}
export function stageSet(api, patch) {
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

/** The line the status of a stage shows: [kind, text key]: WebGL with the software renderer is named, a missing context is DEGRADED. */
export function stageStatusKey(sc) {
  if (!sc || sc.degraded) return 'stage.status.degraded';
  if (!sc.rs) return 'stage.status.starting';
  return sc.rs.info.software ? 'stage.status.software' : 'stage.status.gpu';
}

// ---------- camera ----------
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

// ---------- drawing ----------
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

/** Called after the tree is in the page: size, fit, snap or ease, draw once in this task. Never throws (the shell guards it, too). */
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

// ---------- input ----------
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

// ---------- pieces the tabs use ----------
/** Forget a stage canvas (a test or a model swap): stop its loop. */
export function stageRelease(api, id) {
  const sc = api.cache.get('stage:' + id);
  if (sc) { domCancelFrame(sc.raf); sc.raf = 0; }
}

/** A tab render that draws no canvas forgets the last frame, so the gate never measures a frame from an earlier visit. */
export function stageForget(api, id) {
  const sc = api.cache.get('stage:' + id);
  if (sc) { sc.last = null; sc.composed = null; }
}

/** The gate record of a stage tab: { frames, nodes, w, h } in canvas CSS px for the frame that was just drawn, or null without a drawn canvas. */
export function stageBounds(api, id) {
  const sc = api.cache.get('stage:' + id);
  if (!sc || !sc.last || !sc.composed) return null;
  const p = sceneProjected(sc.cam, sc.last.w, sc.last.h, sc.composed, sc.last.buffers);
  // the occupancy floor judges the 3D scene; a stage frames the rooms of the case, which may hold many more parts than the case uses
  return { frames: p.frames, nodes: p.nodes, w: sc.last.w, h: sc.last.h, occupancy: id === '3D' };
}

/** The element of a stage (canvas plus label layer) for the composed scene; the draw is scheduled for after the tree is in the page. */
export function stagePane(api, id, composed) {
  const sc = stageGet(api, id);
  sc.composed = composed;
  api.mounted(() => stageMount(api, sc, composed));
  return sc.wrap;
}

/** True when this page cannot open a WebGL context for the stage (the tab then draws no canvas). */
export function stageIsDegraded(api, id) {
  const sc = stageGet(api, id);
  stageOpenGl(sc);
  return sc.degraded;
}

/** Status of the canvas (renderer family) for the toolbar line. */
export function stageStatus(api, id) {
  const sc = stageGet(api, id);
  stageOpenGl(sc);
  return api.t(stageStatusKey(sc));
}

/** The legend block of a scene (kinds, relations, states, marks) as an element; an empty scene has none. */
export function stageLegendEl(api, composed) {
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

// ---------- the 3D tab ----------
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

/** The 3D tab definition; viewer-B's definition replaces the reserved slot of the same id. */
export const STAGE_TAB_3D = { id: '3D', labelKey: 'tab.3d', questionKey: 'ask.3d', inspector: true, render: stage3dRender, sceneBounds: (api) => stageBounds(api, '3D'), gateSteps: (api) => stage3dSteps(api) };

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

// ================ part 2: 4D and 5D ================

const PLAY_EMPTY_KEYS = { 'no-cases': 'empty.cases', 'nothing-sealed': 'empty.expectation', 'no-interventions': 'empty.interventions', 'no-pairs': 'empty.expectation', 'no-run': 'empty.expectation' };
const PLAY_ROLE_KEYS = {
  cause: 'role.cause', block: 'role.block', deny: 'role.deny', fail: 'role.fail', timeout: 'role.timeout', reject: 'role.reject', starve: 'role.starve', degrade: 'role.degrade',
  retry: 'role.retry', recovery: 'role.recovery', compensation: 'role.compensation', bypass: 'role.bypass', gain: 'role.gain', lost: 'role.lost', effect: 'role.effect',
};
const PLAY_ARROW = ' \u2192 ';

/** Attach the player to a tab: a state left by the other playback tab does not carry its position over (the two runs are different). */
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

/** The one-line summary of the state changes, written in the interface language from the effect rows (part names, state words), not the engine's English text. */
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

/** The reason line for a tab that has a case list but nothing to play. */
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
    // a comparison whose change is not on the causal path has nothing to draw, and a page without WebGL cannot draw at all: say so instead of showing an
    // empty canvas (the player, the events and the diagram windows stay)
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

// ---------- inspector tabs ----------
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

/** The question of a comparison, in the interface language: the change and the case are named from the page data. */
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

/** The inspector tabs of the stage: the causal chain (5D) and the diagram windows (3D, 4D, 5D). */
export const STAGE_SIDE_TABS = [
  { id: 'chain', labelKey: 'chain.tab', order: 1, tabs: ['5D'], render: chainTab },
  { id: 'diagram', labelKey: 'diagram.tab', order: 2, tabs: ['3D', '4D', '5D'], render: diagramSide },
];

// ---------- scripted steps of the gate ----------
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
      // prefer a pair with something on its stage: the real 5D drawing is what this step must see
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

export const STAGE_TAB_4D = { id: '4D', labelKey: 'tab.4d', questionKey: 'ask.4d', inspector: true, render: playRender('4D'), sceneBounds: (api) => stageBounds(api, '4D'), gateSteps: playSteps4d };
export const STAGE_TAB_5D = { id: '5D', labelKey: 'tab.5d', questionKey: 'ask.5d', inspector: true, render: playRender('5D'), sceneBounds: (api) => stageBounds(api, '5D'), gateSteps: playSteps5d };
