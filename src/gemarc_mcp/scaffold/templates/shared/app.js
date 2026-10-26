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
  tab: '1d',
  scenario: null,
  disabled: [],
  step: 0,
  spaceRenderer: null,
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

function dispatch(payload) {
  switch (payload.type) {
    case 'select-node':
      state.viewState.setSelection([payload.id], []);
      renderInspector();
      syncSpaceHighlight();
      break;
    case 'select-edge':
      state.viewState.setSelection([], [payload.id]);
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
    case '3d': return buildSpaceView(state.model, state.spaceModel);
    case '4d': return buildTimelineView(state.model, state.scenario, state.disabled, state.step);
    case '5d': return buildCausalView(state.model, state.scenario, state.disabled);
    case 'views': return buildViewsView(state.model);
    case 'workflow': return buildWorkflowView(state.model);
    default: return buildFactsView(state.model, state.store);
  }
}

function showTab(tab) {
  // Leaving the 3D tab releases the WebGL renderer. Without this the render
  // loop of a removed canvas would keep running.
  if (state.tab === '3d' && tab !== '3d' && state.spaceRenderer) {
    state.spaceRenderer.dispose();
    state.spaceRenderer = null;
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
  if (view.mount === 'space3d') mountSpace(root);
  renderInspector();
}

/* ------------------------------------------------------------------- 3D */

/**
 * Mirror the current selection into the 3D scene. The selection lives in one
 * place - the view state - so the dimensions never disagree about what is
 * selected.
 */
function syncSpaceHighlight() {
  if (!state.spaceRenderer) return;
  const snapshot = state.viewState.snapshot();
  state.spaceRenderer.setHighlight({
    nodeIds: snapshot.selectedNodeIds,
    edgeIds: snapshot.selectedEdgeIds,
  });
}

async function mountSpace(root) {
  const holder = root.querySelector('.space-3d');
  if (!holder) return;
  try {
    const THREE = await import('three');
    const { createThreeSpaceRenderer } = await import('./runtime/space/space-viewer-three.js');
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
    state.spaceRenderer = createThreeSpaceRenderer(THREE, {
      container: holder,
      model: state.model,
      space: state.model.space,
      store: state.store,
      onSelectNode: (id) => dispatch({ type: 'select-node', id }),
      onSelectEdge: (id) => dispatch({ type: 'select-edge', id }),
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
