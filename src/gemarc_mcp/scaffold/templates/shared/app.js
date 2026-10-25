/**
 * GemArc 5D site application - shared by both delivery profiles.
 *
 * The profile decides how the runtime is resolved, not this file:
 *   static : an import map maps "@gemarc/runtime/" to "./runtime/"
 *   vite   : a Vite alias maps "@gemarc/runtime" to the runtime directory
 *
 * The optional third-party libraries are never imported directly here. They are
 * activated by the runtime's adapters only when the profile actually provides
 * them, so the site degrades honestly instead of pretending.
 */
import { normalizeModel } from '@gemarc/runtime/core/model.js';
import { GraphStore } from '@gemarc/runtime/graph/graph-store.js';
import { GraphQueryEngine } from '@gemarc/runtime/graph/graph-query.js';
import { ViewState } from '@gemarc/runtime/view/view-state.js';
import { ViewRouter } from '@gemarc/runtime/view/view-router.js';
import { simulate, compare } from '@gemarc/runtime/sim/index.js';
import { validateWorkflow } from '@gemarc/runtime/workflow/workflow-validator.js';

const state = {
  model: null,
  store: null,
  query: null,
  viewState: null,
  router: null,
  tab: '1d',
  selectedNode: null,
  selectedEdge: null,
  scenario: null,
  disabled: new Set(),
  step: 0,
};

const $ = (selector) => document.querySelector(selector);
const svgNS = 'http://www.w3.org/2000/svg';

/* ---------------------------------------------------------------- dom utils */

function el(tag, className, parent, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = String(value);
  if (parent) parent.appendChild(node);
  return node;
}

function svgEl(tag, parent, props = {}, value) {
  const node = document.createElementNS(svgNS, tag);
  Object.entries(props).forEach(([key, val]) => node.setAttribute(key, String(val)));
  if (value !== undefined) node.textContent = String(value);
  if (parent) parent.appendChild(node);
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

function panel(parent, title) {
  const section = el('section', 'panel', parent);
  el('h2', '', section, title);
  return section;
}

function hint(parent, message) {
  el('p', 'hint', parent, message);
}

function field(parent, label, value) {
  const row = el('p', 'small', parent);
  el('b', '', row, `${label}：`);
  el('span', 'muted', row, value === null || value === undefined || value === '' ? '未提供' : String(value));
  return row;
}

function action(parent, label, handler) {
  const button = el('button', 'action', parent, label);
  button.type = 'button';
  button.addEventListener('click', handler);
  return button;
}

function head(title, kicker) {
  $('#headline').textContent = title;
  $('#kicker').textContent = kicker;
}

/* -------------------------------------------------------------------- data */

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
  const scenarios = state.model.scenarios || [];
  state.scenario = scenarios.length ? scenarios[0].id : null;
}

/* ------------------------------------------------------------------- views */

function render1D(root) {
  head(state.model.meta.system, '1D / LINEAR FACTS');
  const about = panel(root, '系统资料');
  field(about, '系统名称', state.model.meta.system);
  field(about, '说明', state.model.meta.description);
  field(about, '事实等级', state.model.meta.evidenceState);
  field(about, '来源', state.model.meta.source
    ? `${state.model.meta.source.repository || ''} ${state.model.meta.source.branch || ''} ${state.model.meta.source.commit || ''}`.trim() || null
    : null);
  field(about, '节点 / 关系', `${state.store.nodeCount} / ${state.store.edgeCount}`);

  const facts = panel(root, '实体清单');
  for (const node of state.model.nodes) {
    const card = el('div', 'fact', facts);
    el('h3', '', card, node.name);
    el('small', '', card, `${node.id} · ${node.kind || '未登记类型'}`);
    field(card, '事实等级', node.verification);
    field(card, '证据', node.evidence);
    action(card, '定位关系', () => {
      state.selectedNode = node.id;
      state.viewState.setSelection([node.id], []);
      showTab('2d');
    });
  }
}

function layout(nodes, edges) {
  const ranks = new Map(nodes.map((node) => [node.id, 0]));
  const usable = edges.filter((edge) => ranks.has(edge.sourceId) && ranks.has(edge.targetId));
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const edge of usable) {
      const next = Math.min(nodes.length - 1, (ranks.get(edge.sourceId) || 0) + 1);
      if (next > (ranks.get(edge.targetId) || 0)) {
        ranks.set(edge.targetId, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const columns = new Map();
  for (const node of nodes) {
    const rank = ranks.get(node.id) || 0;
    if (!columns.has(rank)) columns.set(rank, []);
    columns.get(rank).push(node);
  }
  const positions = new Map();
  for (const [rank, column] of columns) {
    column.forEach((node, index) => {
      positions.set(node.id, { x: 90 + rank * 190, y: 70 + index * 92 });
    });
  }
  return positions;
}

function render2D(root) {
  head('节点与依赖关系', '2D / GRAPH VIEW');
  const wrapper = panel(root, '统一 Node–Edge 结构图');
  const holder = el('div', 'graph', wrapper);
  const view = state.viewState.filteredView();
  const nodes = state.model.nodes.filter((node) => view.nodeIds.includes(node.id));
  const edges = state.model.edges.filter((edge) =>
    view.edgeIds.includes(edge.id)
    || (view.nodeIds.includes(edge.sourceId) && view.nodeIds.includes(edge.targetId)));

  if (!nodes.length) {
    hint(wrapper, '当前筛选条件下没有可显示的节点。');
    return;
  }
  const positions = layout(nodes, edges);
  const width = Math.max(640, ...[...positions.values()].map((p) => p.x + 200));
  const height = Math.max(360, ...[...positions.values()].map((p) => p.y + 90));
  const svg = svgEl('svg', holder, { viewBox: `0 0 ${width} ${height}`, class: 'canvas' });

  for (const edge of edges) {
    const from = positions.get(edge.sourceId);
    const to = positions.get(edge.targetId);
    if (!from || !to) continue;
    const path = svgEl('path', svg, {
      d: `M${from.x + 74} ${from.y} C${from.x + 120} ${from.y} ${to.x - 120} ${to.y} ${to.x - 74} ${to.y}`,
      class: `edge${edge.trustBoundary ? ' edge-trust' : ''}`,
      'data-edge': edge.id,
    });
    // The edge is a first-class pickable target, not just decoration.
    path.addEventListener('click', (event) => {
      event.stopPropagation();
      state.selectedEdge = edge.id;
      state.selectedNode = null;
      state.viewState.setSelection([], [edge.id]);
      renderInspector();
    });
  }
  for (const node of nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    const group = svgEl('g', svg, { class: 'node', 'data-node': node.id, tabindex: '0' });
    svgEl('rect', group, { x: position.x, y: position.y - 18, width: 148, height: 36, rx: 6 });
    svgEl('text', group, { x: position.x + 10, y: position.y + 4, class: 'node-label' }, node.name);
    group.addEventListener('click', () => {
      state.selectedNode = node.id;
      state.selectedEdge = null;
      state.viewState.setSelection([node.id], []);
      renderInspector();
    });
  }
}

function render3D(root) {
  head('3D 空间与分类', '3D / BUILDING · FLOOR · ROOM');
  const section = panel(root, '三维工程空间');
  const space = state.model.space;
  if (!space || !space.buildings || !space.buildings.length) {
    hint(section, '模型未提供 Building–Floor–Room 空间结构。虚拟空间必须声明语义来源，'
    + '因此这里不生成一个没有来源的空间。');
    return;
  }
  for (const building of space.buildings) {
    const block = el('div', 'space-building', section);
    el('h3', '', block, building.name);
    field(block, '语义类别', building.kind);
    field(block, '语义来源', building.semanticSource);
    field(block, '信任边界', building.trustBoundary === true ? '是' : '否');
    for (const floor of (space.floors || []).filter((item) => item.buildingId === building.id)) {
      const floorBlock = el('div', 'space-floor', block);
      el('h4', '', floorBlock, floor.name);
      field(floorBlock, '语义来源', floor.semanticSource);
      for (const room of (space.rooms || []).filter((item) => item.floorId === floor.id)) {
        const roomBlock = el('div', 'space-room', floorBlock);
        el('span', '', roomBlock, room.name);
        field(roomBlock, '语义来源', room.semanticSource);
      }
    }
  }
  hint(section, '三维拾取需要构建 legacy Viewer 后用 --legacy-dist 注入；'
  + '在拿到该产物之前，这里只如实呈现空间语义与归属。');
}

function render4D(root) {
  head('动态流程与时间轴', '4D / EVENT TIME');
  const section = panel(root, '事件时间轴');
  const scenarios = state.model.scenarios || [];
  if (!scenarios.length) {
    hint(section, '缺少事件或规则模型，不能生成真实 4D 行为。');
    return;
  }
  const picker = el('select', '', section);
  for (const scenario of scenarios) {
    const option = el('option', '', picker, scenario.name || scenario.id);
    option.value = scenario.id;
  }
  picker.value = state.scenario;
  picker.addEventListener('change', () => {
    state.scenario = picker.value;
    state.step = 0;
    render4D(clear($('#content')));
  });
  const current = scenarios.find((item) => item.id === state.scenario);
  el('p', 'hint', section, `证据等级：${current ? current.evidenceClass || 'unknown' : 'unknown'}`);

  const report = simulate(state.model, state.scenario, [...state.disabled]);
  const trace = report.trace || [];
  if (!trace.length) {
    hint(section, '该场景没有产生任何事件。');
    return;
  }
  const list = el('ol', 'timeline', section);
  trace.forEach((event, index) => {
    const item = el('li', `event event-${event.status}`, list);
    el('span', 'time', item, `${event.timeMs}ms`);
    el('span', 'module', item, event.module);
    el('span', 'name', item, event.event);
    el('span', 'state', item, event.status);
    el('small', 'muted', item, event.traceId + (event.triggeredBy ? ` ← ${event.triggeredBy}` : ''));
    if (index === state.step) item.classList.add('current');
  });
  if (report.truncation) {
    hint(section, `结果被截断（${report.truncation}）：事件或时间超出仿真上限。`);
  }
  const controls = el('div', 'controls', section);
  action(controls, '下一步', () => {
    state.step = Math.min(state.step + 1, trace.length - 1);
    render4D(clear($('#content')));
  });
  action(controls, '重置', () => {
    state.step = 0;
    render4D(clear($('#content')));
  });
  field(section, '送达 / 阻断', `${report.summary.delivered} / ${report.summary.blocked}`);
}

function render5D(root) {
  head('模块组合与因果干预', '5D / CAUSAL SYSTEM');
  const section = panel(root, '因果干预对照');
  if (!state.model.scenarios || !state.model.scenarios.length) {
    hint(section, '没有显式因果规则，系统不会编造模拟结果。');
    return;
  }
  const picker = el('select', '', section);
  for (const scenario of state.model.scenarios) {
    const option = el('option', '', picker, scenario.name || scenario.id);
    option.value = scenario.id;
  }
  picker.value = state.scenario;
  picker.addEventListener('change', () => {
    state.scenario = picker.value;
    render5D(clear($('#content')));
  });

  const modules = el('div', 'chips', section);
  el('p', 'hint', modules, '选择要屏蔽的模块，然后重新计算反事实结果：');
  for (const node of state.model.nodes) {
    const chip = el('button', 'chip', modules, node.name);
    chip.type = 'button';
    if (state.disabled.has(node.id)) chip.classList.add('on');
    chip.addEventListener('click', () => {
      if (state.disabled.has(node.id)) state.disabled.delete(node.id);
      else state.disabled.add(node.id);
      render5D(clear($('#content')));
    });
  }

  const delta = compare(state.model, state.scenario, [...state.disabled]);
  const lost = el('ul', '', section);
  el('h3', '', lost, '丢失事件');
  if (!delta.lostEvents.length) el('li', 'muted', lost, '无');
  for (const event of delta.lostEvents) el('li', '', lost, `${event.module} · ${event.event}`);
  const gained = el('ul', '', section);
  el('h3', '', gained, '新增事件');
  if (!delta.gainedEvents.length) el('li', 'muted', gained, '无');
  for (const event of delta.gainedEvents) el('li', '', gained, `${event.module} · ${event.event}`);
  hint(section, delta.notice);
}

function renderViews(root) {
  head('图示定义', 'VIEWS / DIAGRAM TYPES');
  const section = panel(root, '模型中登记的图示');
  const views = state.model.views || [];
  if (!views.length) {
    hint(section, '模型没有登记任何图示。');
    return;
  }
  for (const view of views) {
    const card = el('div', 'fact', section);
    el('h3', '', card, view.title || view.id);
    field(card, '图示类型', view.diagramType);
    field(card, '维度', view.dimension);
    field(card, '关注点', (view.concerns || []).join('、') || null);
    field(card, '节点数', (view.nodeIds || []).length);
    field(card, '关系类型', (view.edgeKinds || []).join('、') || null);
    if (view.missing && view.missing.length) field(card, '保真缺失', view.missing.join('、'));
    action(card, '在 2D 中查看', () => {
      state.viewState.setFilters({ viewId: view.id });
      showTab('2d');
    });
  }
}

function renderWorkflow(root) {
  head('工作流语义', 'WORKFLOW / MACHINE READABLE');
  const section = panel(root, '工作流');
  const workflows = state.model.workflows || [];
  if (!workflows.length) {
    hint(section, '模型没有登记工作流。');
    return;
  }
  for (const workflow of workflows) {
    const card = el('div', 'fact', section);
    el('h3', '', card, workflow.name || workflow.id);
    field(card, '类型', workflow.kind);
    field(card, '节点数', (workflow.nodes || []).length);
    field(card, '边数', (workflow.edges || []).length);
    const report = validateWorkflow(workflow);
    const issues = report.issues || report.errors || [];
    if (issues.length) {
      const list = el('ul', '', card);
      for (const issue of issues) {
        el('li', '', list, `${issue.id || ''} ${issue.message || ''} ${issue.entityId || ''}`.trim());
      }
    } else {
      field(card, '语义校验', '通过');
    }
  }
}

/* ---------------------------------------------------------------- inspector */

function renderInspector() {
  const area = clear($('#detail'));
  if (state.selectedEdge) {
    const edge = state.store.getEdge(state.selectedEdge);
    if (!edge) return;
    el('p', 'section-title', area, 'RELATION');
    field(area, 'Edge ID', edge.id);
    field(area, 'Source', edge.sourceId);
    field(area, 'Target', edge.targetId);
    field(area, '关系类型', edge.type);
    field(area, '方向', edge.directed ? '有向' : '无向');
    field(area, '条件', edge.condition);
    field(area, '权重', edge.weight);
    field(area, '事实等级', edge.verification);
    field(area, '证据', edge.evidence);
    return;
  }
  if (!state.selectedNode) {
    el('p', 'hint', area, '选择节点或关系查看关联信息');
    return;
  }
  const node = state.store.getNode(state.selectedNode);
  if (!node) return;
  el('p', 'section-title', area, 'ENTITY');
  field(area, '名称', node.name);
  field(area, '稳定 ID', node.id);
  field(area, '类型', node.kind);
  field(area, '事实等级', node.verification);
  field(area, '证据', node.evidence);
  const neighbours = state.query.getNeighbors(node.id, { direction: 'both' });
  field(area, '邻居数', neighbours.length);
  const list = el('ul', '', area);
  for (const neighbour of neighbours.slice(0, 30)) {
    el('li', '', list, `${neighbour.nodeId} ← ${neighbour.edgeType}`);
  }
}

/* -------------------------------------------------------------------- shell */

function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll('#nav button').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tab);
  });
  const root = clear($('#content'));
  if (tab === '1d') render1D(root);
  else if (tab === '2d') render2D(root);
  else if (tab === '3d') render3D(root);
  else if (tab === '4d') render4D(root);
  else if (tab === '5d') render5D(root);
  else if (tab === 'views') renderViews(root);
  else if (tab === 'workflow') renderWorkflow(root);
  renderInspector();
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
    el('p', 'hint', root, `载入失败：${error.message}`);
    $('#evidence').textContent = '载入失败';
  }
}

main();
