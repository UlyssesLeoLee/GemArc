/**
 * GemArc site application - shared by both delivery profiles.
 *
 * The application always lives at the project root, so the shared runtime is
 * reached by a relative path in both profiles. That keeps the same source
 * usable in the browser, in the Vite build and in plain Node - which is what
 * makes the view models testable.
 */
import { normalizeModel } from './runtime/core/model.js';
import { GraphStore } from './runtime/graph/graph-store.js';
import { GraphQueryEngine } from './runtime/graph/graph-query.js';
import { ViewState } from './runtime/view/view-state.js';
import { ViewRouter } from './runtime/view/view-router.js';
import { SpaceModel } from './runtime/space/space-model.js';
import {
  buildAxisView,
  buildCausalView,
  buildFactsView,
  buildGraphView,
  buildInspector,
  buildSpaceView,
  buildTimelineView,
  buildViewsView,
  buildWorkflowView,
} from './view-model.js';

const state = {
  model: null,
  store: null,
  query: null,
  viewState: null,
  router: null,
  spaceModel: null,
  threeD: null,
  tab: '1d',
  scenario: null,
  disabled: [],
  step: 0,
  spaceRenderer: null,
  axisRenderer: null,
};

const $ = (selector) => document.querySelector(selector);
const SVG_TAGS = new Set(['svg', 'g', 'path', 'rect', 'text', 'line', 'circle',
  'polygon', 'polyline', 'ellipse', 'defs', 'marker', 'linearGradient', 'stop']);

/* ---------------------------------------------------------------- dom utils */

function createNode(spec) {
  const isSvg = SVG_TAGS.has(spec.tag);
  const node = isSvg
    ? document.createElementNS('http://www.w3.org/2000/svg', spec.tag)
    : document.createElement(spec.tag);
  if (spec.class) node.setAttribute('class', spec.class);
  if (spec.text !== undefined && spec.text !== '') node.textContent = String(spec.text);
  for (const [key, value] of Object.entries(spec.attrs || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

/** Render a view-model tree into the DOM. */
function renderTree(spec, parent) {
  const node = createNode(spec);
  for (const child of spec.children || []) renderTree(child, node);
  if (spec.action) {
    node.addEventListener('click', (event) => {
      if (spec.tag === 'select') return;
      event.stopPropagation();
      dispatch(spec.action);
    });
    if (spec.tag === 'select') {
      node.addEventListener('change', () => dispatch({ ...spec.action, id: node.value }));
    }
  }
  parent.appendChild(node);
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

/* ------------------------------------------------------------- interactions */

/**
 * Apply the selection mode of a three-dimensional click.
 *
 * The modes are the ones Unity uses: a plain click replaces the selection,
 * a click with the modifier adds to it or takes it out again, and a marquee
 * adds the whole rectangle.
 * @param {string[]} current the ids that are selected now
 * @param {string} id the id the reader pointed at
 * @param {string} [mode] one of replace, add, toggle
 * @returns {string[]} the ids that are selected afterwards
 */
function applySelectionMode(current, id, mode) {
  if (mode === 'toggle') {
    return current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id];
  }
  if (mode === 'add') return current.includes(id) ? current : [...current, id];
  return [id];
}

function dispatch(payload) {
  switch (payload.type) {
    case 'select-node': {
      const snapshot = state.viewState.snapshot();
      const nodes = applySelectionMode(snapshot.selectedNodeIds, payload.id, payload.mode);
      // A plain click replaces the whole selection, the way a click in a
      // three-dimensional scene replaces it. An additive click only touches
      // the entities.
      const edges = payload.mode === 'replace' ? [] : snapshot.selectedEdgeIds;
      state.viewState.setSelection(nodes, edges);
      renderInspector();
      syncSpaceHighlight();
      break;
    }
    case 'select-edge': {
      const snapshot = state.viewState.snapshot();
      const edges = applySelectionMode(snapshot.selectedEdgeIds, payload.id, payload.mode);
      const nodes = payload.mode === 'replace' ? [] : snapshot.selectedNodeIds;
      state.viewState.setSelection(nodes, edges);
      renderInspector();
      syncSpaceHighlight();
      break;
    }
    case 'clear-selection':
      state.viewState.setSelection([], []);
      renderInspector();
      syncSpaceHighlight();
      break;
    case 'locate-node':
      state.viewState.setSelection([payload.id], []);
      showTab('2d');
      break;
    case 'toggle-disable': {
      const set = new Set(state.disabled);
      if (set.has(payload.id)) set.delete(payload.id);
      else set.add(payload.id);
      state.disabled = [...set];
      showTab(state.tab);
      break;
    }
    case 'scenario':
      state.scenario = payload.id;
      state.step = 0;
      showTab(state.tab);
      break;
    case 'step':
      state.step = payload.delta === 0 ? 0 : state.step + payload.delta;
      showTab(state.tab);
      break;
    case 'view-filter':
      state.viewState.setFilters({ viewId: payload.id });
      showTab('2d');
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------------- views */

function currentView() {
  switch (state.tab) {
    case '1d': return buildFactsView(state.model, state.store);
    case '2d': return buildGraphView(state.model, state.viewState);
    case '3d': return buildThreeDView();
    case '4d': return buildTimelineView(state.model, state.scenario, state.disabled, state.step);
    case '5d': return buildCausalView(state.model, state.scenario, state.disabled);
    case 'views': return buildViewsView(state.model);
    case 'workflow': return buildWorkflowView(state.model);
    default: return buildFactsView(state.model, state.store);
  }
}

/**
 * The 3D tab: the semantic axes of the model, and the engineering space when
 * the model declares one. Both are optional and both say so when they are
 * missing.
 * @returns {object} the view
 */
function buildThreeDView() {
  const axis = buildAxisView(state.threeD);
  const space = buildSpaceView(state.model, state.spaceModel);
  const body = [...axis.body, ...space.body];
  return {
    headline: '3D 语义与空间',
    kicker: '3D / AXES · BUILDING · FLOOR · ROOM',
    body,
    mount: 'semantic3d',
  };
}

function showTab(tab) {
  // Leaving the 3D tab releases the WebGL renderers. Without this the render
  // loop of a removed canvas would keep running.
  if (state.tab === '3d' && tab !== '3d') {
    if (state.spaceRenderer) {
      state.spaceRenderer.dispose();
      state.spaceRenderer = null;
    }
    if (state.axisRenderer) {
      state.axisRenderer.dispose();
      state.axisRenderer = null;
    }
  }
  state.tab = tab;
  document.querySelectorAll('#nav button').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tab);
  });
  const root = clear($('#content'));
  const view = currentView();
  $('#headline').textContent = view.headline;
  $('#kicker').textContent = view.kicker;
  for (const spec of view.body) renderTree(spec, root);
  if (view.mount === 'semantic3d') mountScenes(root);
  renderInspector();
}

/* ------------------------------------------------------------------- 3D */

/**
 * The line that tells the reader how the three-dimensional scene is driven.
 *
 * The list comes from the shared module, so the page and the code cannot
 * disagree about the gestures.
 * @param {Array<{action: string, shortcut: string}>} map the gesture table
 * @returns {object} the line
 */
function unityGestureHint(map) {
  const line = document.createElement('p');
  line.className = 'hint unity-hint';
  const rows = map.map((row) => `${row.action} ${row.shortcut}`);
  line.textContent = `三维操作与 Unity 一致：${rows.join('，')}。`;
  return line;
}

/**
 * Mirror the current selection into the 3D scenes. The selection lives in one
 * place - the view state - so the dimensions never disagree about what is
 * selected.
 */
function syncSpaceHighlight() {
  const snapshot = state.viewState.snapshot();
  if (state.spaceRenderer) {
    state.spaceRenderer.setHighlight({
      nodeIds: snapshot.selectedNodeIds,
      edgeIds: snapshot.selectedEdgeIds,
    });
  }
  if (state.axisRenderer) {
    state.axisRenderer.setHighlight({
      nodeIds: snapshot.selectedNodeIds,
      edgeIds: snapshot.selectedEdgeIds,
    });
  }
}

/**
 * Mount both 3D scenes of the tab: the semantic axes of the model and the
 * engineering space it declares.
 * @param {object} root the content root
 */
async function mountScenes(root) {
  await mountAxis(root);
  await mountSpace(root);
}

/**
 * Mount the semantic-axis scene. The dataset is derived from the model, so
 * the scene never invents an axis.
 * @param {object} root the content root
 */
async function mountAxis(root) {
  const holder = root.querySelector('.axis-3d');
  if (!holder) return;
  const dataset = state.threeD;
  const views = dataset && dataset.present && Array.isArray(dataset.views) ? dataset.views : [];
  if (!views.length) return;
  try {
    const THREE = await import('three');
    const { createThreeAxisRenderer } = await import('./runtime/space/axis-viewer-three.js');
    const { UNITY_CONTROL_MAP } = await import('./runtime/space/unity-controls.js');
    if (state.axisRenderer) {
      state.axisRenderer.dispose();
      state.axisRenderer = null;
    }
    const status = document.createElement('p');
    status.className = 'hint axis-3d-status';
    status.textContent = '将指针移到实体或关系上可查看拾取目标。';
    holder.after(status);
    holder.after(unityGestureHint(UNITY_CONTROL_MAP));
    state.axisRenderer = createThreeAxisRenderer(THREE, {
      container: holder,
      view: views[0],
      onSelectNode: (id, selection) => dispatch({
        type: 'select-node', id, mode: selection ? selection.mode : 'replace',
      }),
      onSelectEdge: (id, selection) => dispatch({
        type: 'select-edge', id, mode: selection ? selection.mode : 'replace',
      }),
      onClearSelection: () => dispatch({ type: 'clear-selection' }),
      onHover: (hit) => {
        status.textContent = describeAxisHit(hit);
      },
    });
    state.axisRenderer.render();
    syncSpaceHighlight();
  } catch (error) {
    const reason = error && error.code === 'GEMARC_AXIS_010'
      ? '浏览器没有可用的 WebGL 上下文'
      : (error && error.message ? error.message : '未知原因');
    const notice = document.createElement('p');
    notice.className = 'hint';
    notice.textContent = `三维语义渲染未能启动：${reason}`
      + '。三维拾取需要真实的 WebGL 渲染，此处不提供任何替代视图；'
      + '坐标轴语义仍如实列在左侧。';
    holder.appendChild(notice);
  }
}

/**
 * Describe what the semantic scene reports under the pointer.
 * @param {{kind: string, id: string}|null} hit the pick result
 * @returns {string}
 */
function describeAxisHit(hit) {
  if (!hit) return '指针下没有可拾取的实体或关系。';
  const node = hit.kind === 'node' ? state.store.getNode(hit.id) : null;
  if (node) {
    const name = node && node.name ? node.name : hit.id;
    return `拾取目标：实体 ${name}（${hit.id}）`;
  }
  const edge = state.store.getEdge(hit.id);
  const name = edge && edge.description ? edge.description : hit.id;
  return `拾取目标：关系 ${name}（${hit.id}）`;
}

async function mountSpace(root) {
  const holder = root.querySelector('.space-3d');
  if (!holder) return;
  try {
    const THREE = await import('three');
    const { createThreeSpaceRenderer } = await import('./runtime/space/space-viewer-three.js');
    const { UNITY_CONTROL_MAP } = await import('./runtime/space/unity-controls.js');
    if (state.spaceRenderer) {
      state.spaceRenderer.dispose();
      state.spaceRenderer = null;
    }
    // The status line tells the user what is under the pointer. A scene that
    // cannot be picked silently is indistinguishable from a broken one. It
    // sits next to the canvas, not inside it: the canvas fills its box.
    const status = document.createElement('p');
    status.className = 'hint space-3d-status';
    status.textContent = '将指针移到节点或关系上可查看拾取目标。';
    holder.after(status);
    holder.after(unityGestureHint(UNITY_CONTROL_MAP));
    state.spaceRenderer = createThreeSpaceRenderer(THREE, {
      container: holder,
      model: state.model,
      space: state.model.space,
      store: state.store,
      onSelectNode: (id, selection) => dispatch({
        type: 'select-node', id, mode: selection ? selection.mode : 'replace',
      }),
      onSelectEdge: (id, selection) => dispatch({
        type: 'select-edge', id, mode: selection ? selection.mode : 'replace',
      }),
      onClearSelection: () => dispatch({ type: 'clear-selection' }),
      onHover: (hit) => {
        status.textContent = describeSpaceHit(hit);
      },
    });
    state.spaceRenderer.render();
    syncSpaceHighlight();
  } catch (error) {
    // WebGL 失败必须显式说明，不能用静态图或 2D 伪装。
    const reason = error && error.code === 'GEMARC_SPACE_010'
      ? '浏览器没有可用的 WebGL 上下文'
      : (error && error.message ? error.message : '未知原因');
    const notice = document.createElement('p');
    notice.className = 'hint';
    notice.textContent = `三维渲染未能启动：${reason}`
      + '。三维拾取需要真实的 WebGL 渲染，此处不提供任何替代视图；'
      + '空间语义与归属仍如实列在左侧。';
    holder.appendChild(notice);
  }
}

/**
 * Describe what the 3D scene reports under the pointer.
 * @param {{kind: string, id: string}|null} hit the pick result
 * @returns {string}
 */
function describeSpaceHit(hit) {
  if (!hit) return '指针下没有可拾取的节点或关系。';
  if (hit.kind === 'node') {
    const node = state.store.getNode(hit.id);
    const name = node && node.name ? node.name : hit.id;
    return `拾取目标：节点 ${name}（${hit.id}）`;
  }
  const edge = state.store.getEdge(hit.id);
  const name = edge && edge.description ? edge.description : hit.id;
  return `拾取目标：关系 ${name}（${hit.id}）`;
}

/* ---------------------------------------------------------------- inspector */

function renderInspector() {
  const snapshot = state.viewState.snapshot();
  const area = clear($('#detail'));
  const spec = buildInspector(state.model, state.store, state.query, {
    nodeIds: snapshot.selectedNodeIds,
    edgeIds: snapshot.selectedEdgeIds,
  });
  for (const child of spec.body) renderTree(child, area);
}

/* ------------------------------------------------------------------ layout */

/**
 * Pane geometry: the side panes are resizable, the main column keeps a floor
 * so the primary content is never squeezed into an unusable strip, and the
 * reader can drop the side panes entirely to give the content the whole
 * window.
 *
 * The sizes live in CSS custom properties, so the layout is a stylesheet
 * decision and the behaviour here only moves the numbers.
 */
const LAYOUT_KEY = 'gemarc.layout.v1';
const LAYOUT_DEFAULTS = { sidebar: 232, inspector: 300, focus: false };
const PANE_LIMITS = {
  sidebar: { min: 160, max: 520 },
  inspector: { min: 200, max: 560 },
};
const MAIN_MIN = 480;
const GUTTER = 8;
const KEY_STEP = 16;

function readLayout() {
  try {
    const raw = window.localStorage.getItem(LAYOUT_KEY);
    if (!raw) return { ...LAYOUT_DEFAULTS };
    const parsed = JSON.parse(raw);
    return {
      sidebar: Number.isFinite(parsed.sidebar) ? parsed.sidebar : LAYOUT_DEFAULTS.sidebar,
      inspector: Number.isFinite(parsed.inspector) ? parsed.inspector : LAYOUT_DEFAULTS.inspector,
      focus: Boolean(parsed.focus),
    };
  } catch (error) {
    return { ...LAYOUT_DEFAULTS };
  }
}

function writeLayout(layout) {
  try {
    window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch (error) {
    // A browser that refuses storage (private mode) still gets a working page.
  }
}

// The layout in flight lives here, not in localStorage: reading the store on
// every pointer move is wasteful, and the store can lag behind what is
// painted. One in-memory state keeps the splitter, the focus toggle and the
// resize handler talking about the same numbers.
let layoutState = readLayout();

function paintLayout(layout) {
  const root = document.documentElement;
  root.style.setProperty('--sidebar-w', `${layout.sidebar}px`);
  root.style.setProperty('--inspector-w', `${layout.inspector}px`);
  const main = document.querySelector('main');
  if (main) main.classList.toggle('focus', Boolean(layout.focus));
  const toggle = document.querySelector('#focus-toggle');
  if (toggle) {
    toggle.setAttribute('aria-pressed', layout.focus ? 'true' : 'false');
    toggle.textContent = layout.focus ? '退出专注' : '专注';
  }
  document.querySelectorAll('.splitter').forEach((splitter) => {
    const pane = splitter.dataset.pane;
    const width = layout[pane];
    if (Number.isFinite(width)) {
      splitter.setAttribute('aria-label', `${splitter.dataset.label || '调整窗格宽度'}（当前 ${width} 像素）`);
    }
  });
}

/** Keep the main column above its floor: the side panes give up space first. */
function clampPane(pane, value, layout, mainWidth) {
  const limits = PANE_LIMITS[pane];
  const other = pane === 'sidebar' ? layout.inspector : layout.sidebar;
  const roomForMain = mainWidth - MAIN_MIN - other - GUTTER * 2;
  const max = Math.max(limits.min, Math.min(limits.max, roomForMain));
  return Math.round(Math.min(Math.max(value, limits.min), max));
}

function mainWidth() {
  const main = document.querySelector('main');
  return main ? main.getBoundingClientRect().width : 0;
}

function setPaneWidth(pane, value, layout) {
  const width = clampPane(pane, value, layout, mainWidth());
  const next = { ...layout, [pane]: width };
  paintLayout(next);
  return next;
}

function wireSplitter(splitter, pane) {
  let dragging = false;

  splitter.addEventListener('pointerdown', (event) => {
    dragging = true;
    splitter.classList.add('dragging');
    splitter.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  splitter.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const rect = document.querySelector('main').getBoundingClientRect();
    const raw = pane === 'sidebar'
      ? event.clientX - rect.left - GUTTER / 2
      : rect.right - event.clientX - GUTTER / 2;
    layoutState = setPaneWidth(pane, raw, layoutState);
    // Persist on every move, not only on pointerup: a drag that ends without
    // a pointerup (the pointer leaves the window, the gesture is cancelled)
    // must not lose the width the reader just chose. The write is synchronous
    // on purpose: the stored state must never lag behind the painted one.
    writeLayout(layoutState);
  });
  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove('dragging');
    try { splitter.releasePointerCapture(event.pointerId); } catch (error) { /* already released */ }
    writeLayout(layoutState);
  };
  splitter.addEventListener('pointerup', stop);
  splitter.addEventListener('pointercancel', stop);
  splitter.addEventListener('lostpointercapture', stop);

  splitter.addEventListener('dblclick', () => {
    layoutState = { ...layoutState, [pane]: LAYOUT_DEFAULTS[pane] };
    paintLayout(layoutState);
    writeLayout(layoutState);
  });

  splitter.addEventListener('keydown', (event) => {
    const step = event.shiftKey ? KEY_STEP * 3 : KEY_STEP;
    const current = layoutState[pane];
    let next = null;
    if (event.key.startsWith('Arrow') && event.key.endsWith('Left')) next = current - step;
    else if (event.key.startsWith('Arrow') && event.key.endsWith('Right')) next = current + step;
    else if (event.key === 'Home') next = PANE_LIMITS[pane].min;
    else if (event.key === 'End') next = PANE_LIMITS[pane].max;
    else if (event.key === 'Enter' || event.key === ' ') next = LAYOUT_DEFAULTS[pane];
    if (next === null) return;
    event.preventDefault();
    layoutState = setPaneWidth(pane, next, layoutState);
    writeLayout(layoutState);
  });
}

function setupLayout() {
  layoutState = readLayout();
  paintLayout(layoutState);

  document.querySelectorAll('.splitter').forEach((splitter) => {
    wireSplitter(splitter, splitter.dataset.pane);
  });

  const toggle = document.querySelector('#focus-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      layoutState = { ...layoutState, focus: !layoutState.focus };
      paintLayout(layoutState);
      writeLayout(layoutState);
    });
  }

  // A narrower window cannot hold the same three panes: give the room back
  // to the main column instead of letting the layout overflow.
  window.addEventListener('resize', () => {
    const current = layoutState;
    const width = mainWidth();
    if (!width) return;
    let sidebar = current.sidebar;
    let inspector = current.inspector;
    const room = width - MAIN_MIN - GUTTER * 2;
    if (sidebar + inspector > room) {
      const total = sidebar + inspector;
      const share = total ? room / total : 0.5;
      sidebar = Math.round(sidebar * share);
      inspector = Math.round(inspector * share);
    }
    const next = {
      ...current,
      sidebar: clampPane('sidebar', sidebar, { ...current, inspector }, width),
      inspector: clampPane('inspector', inspector, { ...current, sidebar }, width),
    };
    // The in-flight state is updated too, so a resize that happens during a
    // drag is not overwritten by the next pointer move.
    layoutState = next;
    paintLayout(layoutState);
    writeLayout(layoutState);
  });
}

/* -------------------------------------------------------------------- boot */

async function load() {
  const response = await fetch('./data/architecture.json', { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`无法载入 data/architecture.json (HTTP ${response.status})`);
  }
  state.model = normalizeModel(await response.json());
  state.store = new GraphStore(state.model);
  state.query = new GraphQueryEngine(state.store);
  state.viewState = new ViewState();
  state.viewState.attachStore(state.store);
  state.router = new ViewRouter({ store: state.store, query: state.query, viewState: state.viewState });
  state.spaceModel = new SpaceModel(state.model);
  // The derived 3D dataset is optional: a site without it still runs, it
  // simply has no semantic axes to show.
  try {
    const derived = await fetch('./data/three-d.json', { cache: 'no-store' });
    state.threeD = derived.ok ? await derived.json() : null;
  } catch {
    state.threeD = null;
  }
  const scenarios = state.model.scenarios || [];
  state.scenario = scenarios.length ? scenarios[0].id : null;
}

async function main() {
  try {
    await load();
    $('#evidence').textContent = `${state.model.meta.evidenceState} · ${state.store.nodeCount} 节点 / ${state.store.edgeCount} 关系`;
    document.querySelectorAll('#nav button').forEach((button) => {
      button.addEventListener('click', () => showTab(button.dataset.tab));
    });
    setupLayout();
    showTab('1d');
  } catch (error) {
    const root = clear($('#content'));
    const notice = document.createElement('p');
    notice.className = 'hint';
    notice.textContent = `载入失败：${error.message}`;
    root.appendChild(notice);
    $('#evidence').textContent = '载入失败';
  }
}

main();
