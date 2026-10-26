/**
 * Pure view models for the GemArc site.
 *
 * These functions turn the canonical model into plain data - a tree of
 * `{tag, class, text, attrs, children, action}` nodes. They touch no DOM, so
 * they can be unit tested and snapshotted, and the presentation model stays
 * separate from the execution model: nothing here decides what the system
 * does, it only decides how the facts are shown.
 *
 * `app.js` is the thin DOM layer that renders these trees.
 */
import { simulate, compare } from './runtime/sim/index.js';
import { validateWorkflow } from './runtime/workflow/workflow-validator.js';

/* ------------------------------------------------------------- node shapes */

export function element(tag, className, children) {
  return { tag, class: className, children: children || [] };
}

export function textNode(tag, className, value) {
  return { tag, class: className, text: value === undefined || value === null ? '' : String(value) };
}

export function panel(title, children) {
  return element('section', 'panel', [textNode('h2', '', title), ...(children || [])]);
}

export function field(label, value) {
  const shown = value === null || value === undefined || value === '' ? '未提供' : String(value);
  return element('p', 'small', [
    textNode('b', '', `${label}：`),
    textNode('span', 'muted', shown),
  ]);
}

export function hint(message) {
  return textNode('p', 'hint', message);
}

export function action(label, payload) {
  return { ...textNode('button', 'action', label), action: payload };
}

/* ------------------------------------------------------------------- 1D */

export function buildFactsView(model, store) {
  const source = model.meta.source
    ? `${model.meta.source.repository || ''} ${model.meta.source.branch || ''} ${model.meta.source.commit || ''}`.trim() || null
    : null;

  const about = panel('系统资料', [
    field('系统名称', model.meta.system),
    field('说明', model.meta.description),
    field('事实等级', model.meta.evidenceState),
    field('来源', source),
    field('节点 / 关系', `${store.nodeCount} / ${store.edgeCount}`),
  ]);

  const cards = (model.nodes || []).map((node) => element('div', 'fact', [
    textNode('h3', '', node.name),
    textNode('small', '', `${node.id} · ${node.kind || '未登记类型'}`),
    field('事实等级', node.verification),
    field('证据', node.evidence),
    action('定位关系', { type: 'locate-node', id: node.id }),
  ]));

  return {
    headline: model.meta.system,
    kicker: '1D / LINEAR FACTS',
    body: [about, panel('实体清单', cards)],
  };
}

/* ------------------------------------------------------------------- 2D */

/** Deterministic layered layout. No randomness: the same model always looks the same. */
export function layeredLayout(nodes, edges) {
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
      positions.set(node.id, { x: 90 + rank * 240, y: 70 + index * 92 });
    });
  }
  return positions;
}

/** The node box is centred on its position; the edges leave from its sides. */
export const NODE_BOX = { width: 148, height: 36, halfWidth: 74, halfHeight: 18 };

export function buildGraphView(model, viewState) {
  const view = viewState.filteredView();
  const nodes = (model.nodes || []).filter((node) => view.nodeIds.includes(node.id));
  const edges = (model.edges || []).filter((edge) =>
    view.edgeIds.includes(edge.id)
    || (view.nodeIds.includes(edge.sourceId) && view.nodeIds.includes(edge.targetId)));

  const wrapper = panel('统一 Node–Edge 结构图', []);
  if (!nodes.length) {
    wrapper.children.push(hint('当前筛选条件下没有可显示的节点。'));
    return { headline: '节点与依赖关系', kicker: '2D / GRAPH VIEW', body: [wrapper] };
  }

  const positions = layeredLayout(nodes, edges);
  const width = Math.max(640, ...[...positions.values()].map((p) => p.x + 200));
  const height = Math.max(360, ...[...positions.values()].map((p) => p.y + 90));

  const shapes = [];
  for (const edge of edges) {
    const from = positions.get(edge.sourceId);
    const to = positions.get(edge.targetId);
    if (!from || !to) continue;
    const curve = `M${from.x + NODE_BOX.halfWidth} ${from.y} `
      + `C${from.x + NODE_BOX.halfWidth + 60} ${from.y} `
      + `${to.x - NODE_BOX.halfWidth - 60} ${to.y} `
      + `${to.x - NODE_BOX.halfWidth} ${to.y}`;
    const visible = {
      tag: 'path',
      attrs: {
        d: curve,
        class: `edge${edge.trustBoundary ? ' edge-trust' : ''}`,
        'data-edge': edge.id,
      },
    };
    // An edge needs an independently pickable region. A 1.4px stroke is a
    // target nobody can hit, so a transparent thick stroke rides on top of
    // the visible one and carries the click.
    const hit = {
      tag: 'path',
      attrs: { d: curve, class: 'edge-hit', 'data-edge-hit': edge.id },
    };
    shapes.push({
      tag: 'g',
      attrs: { class: 'edge-group', 'data-edge': edge.id },
      // The edge is a first-class pickable target, not just decoration.
      action: { type: 'select-edge', id: edge.id },
      children: [visible, hit],
    });
  }
  for (const node of nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    shapes.push({
      tag: 'g',
      attrs: { class: 'node', 'data-node': node.id, tabindex: '0' },
      action: { type: 'select-node', id: node.id },
      children: [
        {
          tag: 'rect',
          attrs: {
            x: position.x - NODE_BOX.halfWidth,
            y: position.y - NODE_BOX.halfHeight,
            width: NODE_BOX.width,
            height: NODE_BOX.height,
            rx: 6,
          },
        },
        {
          tag: 'text',
          attrs: { x: position.x - NODE_BOX.halfWidth + 10, y: position.y + 4, class: 'node-label' },
          text: node.name,
        },
      ],
    });
  }

  const canvas = { tag: 'svg', attrs: { viewBox: `0 0 ${width} ${height}`, class: 'canvas' }, children: shapes };
  wrapper.children.push(element('div', 'graph', [canvas]));

  return {
    headline: '节点与依赖关系',
    kicker: '2D / GRAPH VIEW',
    body: [wrapper],
    graph: { nodeIds: nodes.map((n) => n.id), edgeIds: edges.map((e) => e.id) },
  };
}

/* ------------------------------------------------------------------- 3D */

export function buildSpaceView(model, spaceModel) {
  const body = [];
  const space = model.space;
  if (!space || !space.buildings || !space.buildings.length) {
    body.push(panel('三维工程空间', [
      hint('模型未提供 Building–Floor–Room 空间结构。虚拟空间必须声明语义来源，'
        + '因此这里不生成一个没有来源的空间。'),
    ]));
    return { headline: '3D 空间与分类', kicker: '3D / BUILDING · FLOOR · ROOM', body, mount: 'space3d' };
  }

  const buildings = (space.buildings || []).map((building) => {
    const floors = (space.floors || [])
      .filter((floor) => floor.buildingId === building.id)
      .map((floor) => {
        const rooms = (space.rooms || [])
          .filter((room) => room.floorId === floor.id)
          .map((room) => element('div', 'space-room', [
            textNode('span', '', room.name),
            field('语义来源', room.semanticSource),
          ]));
        return element('div', 'space-floor', [
          textNode('h4', '', floor.name),
          field('语义来源', floor.semanticSource),
          ...rooms,
        ]);
      });
    return element('div', 'space-building', [
      textNode('h3', '', building.name),
      field('语义类别', building.kind),
      field('语义来源', building.semanticSource),
      field('信任边界', building.trustBoundary === true ? '是' : '否'),
      ...floors,
    ]);
  });

  body.push(panel('三维工程空间', buildings));
  // The live 3D scene is mounted here when a WebGL renderer is available.
  body.push(element('div', 'space-3d', []));
  const counts = spaceModel && typeof spaceModel.validate === 'function' ? spaceModel.validate() : null;
  if (counts && counts.status && counts.status !== 'ok') {
    body.push(hint('空间结构存在不一致，详情见 validate 输出。'));
  }
  return { headline: '3D 空间与分类', kicker: '3D / BUILDING · FLOOR · ROOM', body, mount: 'space3d' };
}

/* ------------------------------------------------------------------- 4D */

export function buildTimelineView(model, scenarioId, disabled, step) {
  const scenarios = model.scenarios || [];
  if (!scenarios.length) {
    return {
      headline: '动态流程与时间轴',
      kicker: '4D / EVENT TIME',
      body: [panel('事件时间轴', [hint('缺少事件或规则模型，不能生成真实 4D 行为。')])],
    };
  }

  const current = scenarios.find((item) => item.id === scenarioId) || scenarios[0];
  const report = simulate(model, current.id, disabled || []);
  const trace = report.trace || [];

  const picker = {
    tag: 'select',
    children: scenarios.map((scenario) => ({
      ...textNode('option', '', scenario.name || scenario.id),
      attrs: { value: scenario.id },
    })),
    attrs: { value: current.id },
    action: { type: 'scenario', id: current.id },
  };

  const items = trace.map((event, index) => ({
    tag: 'li',
    class: `event event-${event.status}${index === step ? ' current' : ''}`,
    children: [
      textNode('span', 'time', `${event.timeMs}ms`),
      textNode('span', 'module', event.module),
      textNode('span', 'name', event.event),
      textNode('span', 'state', event.status),
      textNode('small', 'muted', event.traceId + (event.triggeredBy ? ` ← ${event.triggeredBy}` : '')),
    ],
  }));

  const children = [
    picker,
    hint(`证据等级：${current.evidenceClass || 'unknown'}`),
  ];
  if (!trace.length) {
    children.push(hint('该场景没有产生任何事件。'));
  } else {
    children.push(element('ol', 'timeline', items));
  }
  if (report.truncation) {
    children.push(hint(`结果被截断（${report.truncation}）：事件或时间超出仿真上限。`));
  }
  children.push(element('div', 'controls', [
    action('下一步', { type: 'step', delta: 1 }),
    action('重置', { type: 'step', delta: 0 }),
  ]));
  children.push(field('送达 / 阻断', `${report.summary.delivered} / ${report.summary.blocked}`));

  return {
    headline: '动态流程与时间轴',
    kicker: '4D / EVENT TIME',
    body: [panel('事件时间轴', children)],
    trace: trace.length,
  };
}

/* ------------------------------------------------------------------- 5D */

export function buildCausalView(model, scenarioId, disabled) {
  const scenarios = model.scenarios || [];
  if (!scenarios.length) {
    return {
      headline: '模块组合与因果干预',
      kicker: '5D / CAUSAL SYSTEM',
      body: [panel('因果干预对照', [hint('没有显式因果规则，系统不会编造模拟结果。')])],
    };
  }

  const current = scenarios.find((item) => item.id === scenarioId) || scenarios[0];
  const chips = (model.nodes || []).map((node) => ({
    ...textNode('button', `chip${(disabled || []).includes(node.id) ? ' on' : ''}`, node.name),
    attrs: { type: 'button' },
    action: { type: 'toggle-disable', id: node.id },
  }));

  const delta = compare(model, current.id, disabled || []);
  const lost = (delta.lostEvents || []).map((event) => textNode('li', '', `${event.module} · ${event.event}`));
  const gained = (delta.gainedEvents || []).map((event) => textNode('li', '', `${event.module} · ${event.event}`));

  const children = [
    {
      tag: 'select',
      children: scenarios.map((scenario) => ({
        ...textNode('option', '', scenario.name || scenario.id),
        attrs: { value: scenario.id },
      })),
      attrs: { value: current.id },
      action: { type: 'scenario', id: current.id },
    },
    element('div', 'chips', [
      hint('选择要屏蔽的模块，然后重新计算反事实结果：'),
      ...chips,
    ]),
    element('ul', '', [textNode('h3', '', '丢失事件'), ...(lost.length ? lost : [textNode('li', 'muted', '无')])]),
    element('ul', '', [textNode('h3', '', '新增事件'), ...(gained.length ? gained : [textNode('li', 'muted', '无')])]),
    hint(delta.notice),
  ];

  return {
    headline: '模块组合与因果干预',
    kicker: '5D / CAUSAL SYSTEM',
    body: [panel('因果干预对照', children)],
    lostEvents: (delta.lostEvents || []).length,
  };
}

/* ----------------------------------------------------------------- views */

export function buildViewsView(model) {
  const views = model.views || [];
  if (!views.length) {
    return {
      headline: '图示定义',
      kicker: 'VIEWS / DIAGRAM TYPES',
      body: [panel('模型中登记的图示', [hint('模型没有登记任何图示。')])],
    };
  }
  const cards = views.map((view) => {
    const children = [
      field('图示类型', view.diagramType),
      field('维度', view.dimension),
      field('关注点', (view.concerns || []).join('、') || null),
      field('节点数', (view.nodeIds || []).length),
      field('关系类型', (view.edgeKinds || []).join('、') || null),
    ];
    if (view.missing && view.missing.length) children.push(field('保真缺失', view.missing.join('、')));
    children.push(action('在 2D 中查看', { type: 'view-filter', id: view.id }));
    return element('div', 'fact', [textNode('h3', '', view.title || view.id), ...children]);
  });
  return {
    headline: '图示定义',
    kicker: 'VIEWS / DIAGRAM TYPES',
    body: [panel('模型中登记的图示', cards)],
  };
}

/* -------------------------------------------------------------- workflow */

export function buildWorkflowView(model) {
  const workflows = model.workflows || [];
  if (!workflows.length) {
    return {
      headline: '工作流语义',
      kicker: 'WORKFLOW / MACHINE READABLE',
      body: [panel('工作流', [hint('模型没有登记工作流。')])],
    };
  }
  const cards = workflows.map((workflow) => {
    const children = [
      field('类型', workflow.kind),
      field('节点数', (workflow.nodes || []).length),
      field('边数', (workflow.edges || []).length),
    ];
    const report = validateWorkflow(workflow);
    const issues = report.issues || report.errors || [];
    if (issues.length) {
      children.push(element('ul', '', issues.map((issue) =>
        textNode('li', '', `${issue.id || ''} ${issue.message || ''} ${issue.entityId || ''}`.trim()))));
    } else {
      children.push(field('语义校验', '通过'));
    }
    return element('div', 'fact', [textNode('h3', '', workflow.name || workflow.id), ...children]);
  });
  return {
    headline: '工作流语义',
    kicker: 'WORKFLOW / MACHINE READABLE',
    body: [panel('工作流', cards)],
  };
}

/* ------------------------------------------------------------- inspector */

export function buildInspector(model, store, query, selection) {
  const selectedEdgeId = selection.edgeIds[0];
  const selectedNodeId = selection.nodeIds[0];

  if (selectedEdgeId) {
    const edge = store.getEdge(selectedEdgeId);
    if (!edge) return { body: [hint('选择节点或关系查看关联信息')] };
    return {
      body: [
        textNode('p', 'section-title', 'RELATION'),
        field('Edge ID', edge.id),
        field('Source', edge.sourceId),
        field('Target', edge.targetId),
        field('关系类型', edge.type),
        field('方向', edge.directed ? '有向' : '无向'),
        field('条件', edge.condition),
        field('权重', edge.weight),
        field('事实等级', edge.verification),
        field('证据', edge.evidence),
      ],
    };
  }

  if (!selectedNodeId) return { body: [hint('选择节点或关系查看关联信息')] };
  const node = store.getNode(selectedNodeId);
  if (!node) return { body: [hint('选择节点或关系查看关联信息')] };
  const neighbours = query.getNeighbors(node.id, { direction: 'both' });
  return {
    body: [
      textNode('p', 'section-title', 'ENTITY'),
      field('名称', node.name),
      field('稳定 ID', node.id),
      field('类型', node.kind),
      field('事实等级', node.verification),
      field('证据', node.evidence),
      field('邻居数', neighbours.length),
      element('ul', '', neighbours.slice(0, 30).map((neighbour) =>
        textNode('li', '', `${neighbour.nodeId} ← ${neighbour.edgeType}`))),
    ],
  };
}
