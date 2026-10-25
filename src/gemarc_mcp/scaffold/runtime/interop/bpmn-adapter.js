/**
 * BPMN 2.0 adapter - canonical workflow data <-> BPMN XML.
 *
 * Canonical subset covered (workflow.schema.json):
 *   - workflow id / name / kind  <-> `<bpmn:process>`
 *   - node kind start | end | task | decision | gateway | subprocess | event |
 *     data  <-> startEvent, endEvent, task, exclusiveGateway, parallelGateway,
 *     subProcess / callActivity, intermediateCatchEvent, dataObjectReference
 *   - edge kind sequence | conditional | message | association  <-> sequenceFlow
 *     (with `<bpmn:conditionExpression>`), messageFlow, association
 *   - canvas geometry  <-> BPMN DI (`BPMNShape` bounds, `BPMNEdge` waypoints);
 *     it stays presentation data and never influences execution semantics
 *
 * Known lossy parts (BPMN cannot express the canonical model):
 *   - ports (`ports[]`) have no BPMN counterpart - they are dropped
 *   - execution constraints (order / exclusive / parallel / timeout / retry /
 *     compensation) have no BPMN counterpart - they are dropped
 *   - data contracts and exception handling are dropped
 *   - lane sets, participant pools, message and error events with their
 *     definitions are reduced to plain nodes
 *
 * This module also hosts the shared, dependency free XML scanner used by the
 * other XML adapters (`uml-xmi-adapter.js`, `drawio-adapter.js`). The scanner
 * handles attributes, self closing tags, nested elements, comments, CDATA and
 * entities. It never throws on a broken document: problems land in
 * `warnings`.
 */
import { GemArcError } from '../core/errors.js';

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };

/**
 * Decode the XML entities that may appear in attribute values and text.
 * @param {string} value
 */
export function decodeEntities(value) {
  if (typeof value !== 'string') return '';
  return value.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (match, body) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X'
        ? Number.parseInt(body.slice(2), 16)
        : Number.parseInt(body.slice(1), 10);
      if (Number.isFinite(code) && code > 0) {
        try {
          return String.fromCodePoint(code);
        } catch {
          return match;
        }
      }
      return match;
    }
    const mapped = ENTITIES[body.toLowerCase()];
    return mapped === undefined ? match : mapped;
  });
}

/**
 * Escape a string for use in XML text or attribute values.
 * @param {string} value
 */
export function escapeXml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function localOf(name) {
  const index = name.indexOf(':');
  return index === -1 ? name : name.slice(index + 1);
}

function prefixOf(name) {
  const index = name.indexOf(':');
  return index === -1 ? '' : name.slice(0, index);
}

function appendText(node, chunk) {
  if (!chunk) return;
  node.text += decodeEntities(chunk);
}

function parseStartTag(source, start) {
  const length = source.length;
  let index = start + 1;
  let name = '';
  while (index < length && !/[\s/>]/.test(source[index])) {
    name += source[index];
    index += 1;
  }
  const attrs = {};
  let selfClosing = false;
  while (index < length) {
    while (index < length && /\s/.test(source[index])) index += 1;
    if (index >= length) break;
    const char = source[index];
    if (char === '/') {
      selfClosing = true;
      index += 1;
      continue;
    }
    if (char === '>') {
      index += 1;
      break;
    }
    let attrName = '';
    while (index < length && !/[\s=/>]/.test(source[index])) {
      attrName += source[index];
      index += 1;
    }
    if (!attrName) {
      index += 1;
      continue;
    }
    while (index < length && /\s/.test(source[index])) index += 1;
    let value = '';
    if (source[index] === '=') {
      index += 1;
      while (index < length && /\s/.test(source[index])) index += 1;
      const quote = source[index];
      if (quote === '"' || quote === "'") {
        index += 1;
        const close = source.indexOf(quote, index);
        if (close === -1) {
          value = source.slice(index);
          index = length;
        } else {
          value = source.slice(index, close);
          index = close + 1;
        }
      } else {
        while (index < length && !/[\s>]/.test(source[index])) {
          value += source[index];
          index += 1;
        }
      }
    }
    attrs[attrName] = decodeEntities(value);
  }
  return { name, attrs, selfClosing, end: index };
}

function skipDeclaration(source, start) {
  let index = start + 2;
  let depth = 0;
  while (index < source.length) {
    const char = source[index];
    if (char === '[') depth += 1;
    else if (char === ']') depth -= 1;
    else if (char === '>' && depth <= 0) return index + 1;
    index += 1;
  }
  return -1;
}

/**
 * Parse an XML document into a small tree.
 * @param {string} input
 * @returns {{root: object, warnings: string[]}}
 */
export function parseXmlDocument(input) {
  if (typeof input !== 'string') {
    throw new GemArcError('GEMARC_INTEROP_001', 'XML 输入必须是字符串');
  }
  const warnings = [];
  const source = input.replace(/^\uFEFF/, '');
  const root = { name: '#document', rawName: '#document', prefix: '', attrs: {}, children: [], text: '' };
  const stack = [root];
  let index = 0;

  while (index < source.length) {
    const lt = source.indexOf('<', index);
    if (lt === -1) {
      appendText(stack[stack.length - 1], source.slice(index));
      break;
    }
    if (lt > index) appendText(stack[stack.length - 1], source.slice(index, lt));

    if (source.startsWith('<!--', lt)) {
      const end = source.indexOf('-->', lt + 4);
      if (end === -1) {
        warnings.push('XML 注释未闭合');
        break;
      }
      index = end + 3;
      continue;
    }
    if (source.startsWith('<![CDATA[', lt)) {
      const end = source.indexOf(']]>', lt + 9);
      if (end === -1) {
        warnings.push('CDATA 未闭合');
        appendText(stack[stack.length - 1], source.slice(lt + 9));
        break;
      }
      appendText(stack[stack.length - 1], source.slice(lt + 9, end));
      index = end + 3;
      continue;
    }
    if (source.startsWith('<?', lt)) {
      const end = source.indexOf('?>', lt + 2);
      if (end === -1) {
        warnings.push('XML 声明未闭合');
        break;
      }
      index = end + 2;
      continue;
    }
    if (source.startsWith('<!', lt)) {
      const end = skipDeclaration(source, lt);
      if (end === -1) {
        warnings.push('XML 声明未闭合');
        break;
      }
      index = end;
      continue;
    }
    if (source.startsWith('</', lt)) {
      const end = source.indexOf('>', lt);
      if (end === -1) {
        warnings.push('结束标签未闭合');
        break;
      }
      const name = source.slice(lt + 2, end).trim();
      const current = stack[stack.length - 1];
      if (stack.length > 1 && current.name === localOf(name)) stack.pop();
      else warnings.push(`结束标签不匹配: ${name}`);
      index = end + 1;
      continue;
    }

    const parsed = parseStartTag(source, lt);
    if (!parsed.name) {
      warnings.push('标签无法解析');
      index = lt + 1;
      continue;
    }
    const node = {
      name: localOf(parsed.name),
      rawName: parsed.name,
      prefix: prefixOf(parsed.name),
      attrs: parsed.attrs,
      children: [],
      text: '',
    };
    stack[stack.length - 1].children.push(node);
    if (!parsed.selfClosing) stack.push(node);
    index = parsed.end;
  }

  if (stack.length > 1) warnings.push('存在未闭合的元素');
  return { root, warnings };
}

/**
 * Direct children with the given local name.
 * @param {object} node
 * @param {string} name
 */
export function childrenNamed(node, name) {
  if (!node || !Array.isArray(node.children)) return [];
  return node.children.filter((child) => child.name === name);
}

/**
 * Every descendant with the given local name (depth first).
 * @param {object} node
 * @param {string} name
 */
export function findAll(node, name) {
  const result = [];
  const walk = (current) => {
    if (!current || !Array.isArray(current.children)) return;
    for (const child of current.children) {
      if (child.name === name) result.push(child);
      walk(child);
    }
  };
  walk(node);
  return result;
}

/**
 * The first descendant with the given local name.
 * @param {object} node
 * @param {string} name
 */
export function findFirst(node, name) {
  const found = findAll(node, name);
  return found.length ? found[0] : null;
}

/**
 * Attribute lookup by local name (an exact match wins).
 * @param {object} node
 * @param {string} name
 * @param {*} [fallback]
 */
export function attr(node, name, fallback = null) {
  if (!node || !node.attrs) return fallback;
  if (Object.prototype.hasOwnProperty.call(node.attrs, name)) return node.attrs[name];
  for (const [key, value] of Object.entries(node.attrs)) {
    if (localOf(key) === name) return value;
  }
  return fallback;
}

/**
 * Attribute lookup by its exact (prefixed) name.
 * @param {object} node
 * @param {string} name
 * @param {*} [fallback]
 */
export function attrRaw(node, name, fallback = null) {
  if (!node || !node.attrs) return fallback;
  return Object.prototype.hasOwnProperty.call(node.attrs, name) ? node.attrs[name] : fallback;
}

/** Format identifier of this adapter. */
export const BPMN_FORMAT = 'bpmn';

/** Fallback size of a BPMN shape when the canonical canvas has no size. */
const DEFAULT_DI_WIDTH = 100;
const DEFAULT_DI_HEIGHT = 80;

const BPMN_TO_CANONICAL = {
  startEvent: 'start',
  endEvent: 'end',
  intermediateCatchEvent: 'event',
  intermediateThrowEvent: 'event',
  boundaryEvent: 'event',
  exclusiveGateway: 'gateway',
  parallelGateway: 'gateway',
  inclusiveGateway: 'gateway',
  complexGateway: 'gateway',
  eventBasedGateway: 'gateway',
  subProcess: 'subprocess',
  callActivity: 'subprocess',
  task: 'task',
  userTask: 'task',
  serviceTask: 'task',
  scriptTask: 'task',
  sendTask: 'task',
  receiveTask: 'task',
  manualTask: 'task',
  businessRuleTask: 'task',
  transaction: 'task',
  dataObjectReference: 'data',
  dataObject: 'data',
  dataStoreReference: 'data',
  dataInput: 'data',
  dataOutput: 'data',
};

const CANONICAL_TO_BPMN = {
  start: 'startEvent',
  end: 'endEvent',
  task: 'task',
  decision: 'exclusiveGateway',
  gateway: 'parallelGateway',
  subprocess: 'subProcess',
  event: 'intermediateCatchEvent',
  data: 'dataObjectReference',
};

const BPMN_EDGE_TO_CANONICAL = {
  sequenceFlow: 'sequence',
  messageFlow: 'message',
  association: 'association',
};

const CANONICAL_EDGE_TO_BPMN = {
  sequence: 'sequenceFlow',
  conditional: 'sequenceFlow',
  message: 'messageFlow',
  association: 'association',
};

const IGNORED_ELEMENTS = new Set(['textAnnotation', 'group', 'laneSet', 'lane', 'documentation', 'extensionElements']);

function localName(text) {
  return typeof text === 'string' ? text.trim() : '';
}

function conditionOf(element) {
  const expression = childrenNamed(element, 'conditionExpression')[0];
  if (!expression) return null;
  const value = expression.text.trim();
  return value ? value : null;
}

function triggerOf(element) {
  const definitions = element.children.filter((child) => /EventDefinition$/.test(child.name));
  if (definitions.length) {
    const name = attr(definitions[0], 'name');
    if (name) return name;
    return definitions[0].name;
  }
  return attr(element, 'name') || element.name;
}

/**
 * Read a BPMN `<definitions>` document into canonical workflow data.
 * @param {string} input BPMN XML
 * @returns {{nodes: object[], edges: object[], warnings: string[], workflow: object}}
 */
export function parseBpmn(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new GemArcError('GEMARC_INTEROP_001', 'BPMN 输入必须是 XML 字符串');
  }
  const { root, warnings } = parseXmlDocument(input);
  const definitions = findFirst(root, 'definitions');
  if (!definitions) {
    throw new GemArcError('GEMARC_INTEROP_001', '不是 BPMN 文档：缺少 definitions 元素');
  }

  const processes = findAll(root, 'process');
  if (!processes.length) {
    warnings.push('BPMN 文档中没有 process 元素');
    return { nodes: [], edges: [], warnings, workflow: { id: '', name: '', kind: 'process', nodes: [], edges: [] } };
  }
  if (processes.length > 1) {
    warnings.push('BPMN 文档包含多个 process，仅解析第一个');
  }
  const process = processes[0];

  const nodes = [];
  const edges = [];
  const nodeById = new Map();
  const flowElements = (process.children || []).filter(
    (child) => !IGNORED_ELEMENTS.has(child.name) && !child.name.endsWith('EventDefinition'),
  );

  // First pass: flow elements become nodes.
  for (const element of flowElements) {
    const kind = BPMN_TO_CANONICAL[element.name];
    if (!kind) {
      // A flow element is handled by the second pass (edges); it is not lost.
      if (!BPMN_EDGE_TO_CANONICAL[element.name]) {
        warnings.push(`忽略不支持的 BPMN 元素: ${element.name}`);
      }
      continue;
    }
    const id = attr(element, 'id') || '';
    if (!id) {
      warnings.push(`BPMN 元素缺少 id: ${element.name}`);
      continue;
    }
    if (nodeById.has(id)) {
      warnings.push(`BPMN 元素 id 重复: ${id}`);
      continue;
    }
    const name = attr(element, 'name') || id;
    const node = {
      id,
      kind,
      name,
      canonicalNodeId: null,
      ports: [],
      constraints: [],
      dataContract: null,
      exceptionHandling: null,
      canvas: null,
      properties: { bpmn: element.name },
    };
    if (kind === 'event') {
      node.dataContract = { trigger: triggerOf(element) };
    }
    if (element.name === 'callActivity') {
      const called = attr(element, 'calledElement');
      node.dataContract = { processId: called || null };
    }
    if (element.name === 'subProcess') {
      node.properties.embedded = true;
    }
    nodeById.set(id, node);
    nodes.push(node);
  }

  // Second pass: flows become edges.
  for (const element of flowElements) {
    if (!BPMN_EDGE_TO_CANONICAL[element.name]) continue;
    const id = attr(element, 'id') || `${attr(element, 'sourceRef')}-${attr(element, 'targetRef')}`;
    const sourceRef = attr(element, 'sourceRef');
    const targetRef = attr(element, 'targetRef');
    if (!sourceRef || !targetRef) {
      warnings.push(`BPMN 流缺少 sourceRef/targetRef: ${id}`);
      continue;
    }
    if (!nodeById.has(sourceRef) || !nodeById.has(targetRef)) {
      warnings.push(`BPMN 流引用了不存在的节点: ${id}`);
    }
    const condition = conditionOf(element);
    let kind = BPMN_EDGE_TO_CANONICAL[element.name];
    if (kind === 'sequence' && condition) kind = 'conditional';
    edges.push({
      id,
      sourceId: sourceRef,
      targetId: targetRef,
      sourcePortId: null,
      targetPortId: null,
      kind,
      directed: true,
      condition,
      dataContract: null,
      canvas: null,
      properties: { bpmn: element.name },
    });
  }

  // An exclusive gateway with conditional branches is a decision.
  const conditionalSources = new Set(
    edges.filter((edge) => edge.kind === 'conditional').map((edge) => edge.sourceId),
  );
  for (const node of nodes) {
    if (node.properties.bpmn === 'exclusiveGateway' && conditionalSources.has(node.id)) {
      node.kind = 'decision';
    }
  }

  applyDiagramInterchange(root, nodeById, edges);

  const workflow = {
    id: attr(process, 'id') || '',
    name: attr(process, 'name') || attr(process, 'id') || '',
    kind: 'process',
    nodes,
    edges,
    variables: [],
  };
  return { nodes, edges, warnings, workflow };
}

function applyDiagramInterchange(root, nodeById, edges) {
  for (const shape of findAll(root, 'BPMNShape')) {
    const ref = attr(shape, 'bpmnElement');
    const node = ref ? nodeById.get(ref) : null;
    if (!node) continue;
    const bounds = childrenNamed(shape, 'Bounds')[0];
    if (!bounds) continue;
    const x = Number(attr(bounds, 'x', NaN));
    const y = Number(attr(bounds, 'y', NaN));
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const width = Number(attr(bounds, 'width', NaN));
    const height = Number(attr(bounds, 'height', NaN));
    node.canvas = {
      x,
      y,
      ...(Number.isFinite(width) ? { width } : {}),
      ...(Number.isFinite(height) ? { height } : {}),
    };
  }
  for (const shape of findAll(root, 'BPMNEdge')) {
    const ref = attr(shape, 'bpmnElement');
    const edge = edges.find((item) => item.id === ref);
    if (!edge) continue;
    const points = childrenNamed(shape, 'waypoint')
      .map((point) => ({ x: Number(attr(point, 'x', NaN)), y: Number(attr(point, 'y', NaN)) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    if (points.length) edge.canvas = { points };
  }
}

function canonicalNodesOf(canonical) {
  if (canonical && Array.isArray(canonical.nodes)) return canonical.nodes;
  if (canonical && canonical.workflow && Array.isArray(canonical.workflow.nodes)) return canonical.workflow.nodes;
  return [];
}

function canonicalEdgesOf(canonical) {
  if (canonical && Array.isArray(canonical.edges)) return canonical.edges;
  if (canonical && canonical.workflow && Array.isArray(canonical.workflow.edges)) return canonical.workflow.edges;
  return [];
}

/**
 * Write canonical workflow data as a BPMN 2.0 `<definitions>` document.
 * @param {object} canonical canonical workflow data
 * @returns {string} BPMN XML
 */
export function serializeBpmn(canonical) {
  const source = canonical && canonical.workflow ? canonical.workflow : canonical || {};
  const nodes = canonicalNodesOf(source);
  const edges = canonicalEdgesOf(source);
  const processId = localName(source.id) || 'process';
  const processName = localName(source.name) || processId;

  const lines = [];
  lines.push('<?xml version="1.0" encoding="UTF-8"?>');
  lines.push(
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'
      + ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
      + ' id="Definitions_GemArc" targetNamespace="urn:gemarc:workflow">',
  );
  lines.push(`  <bpmn:process id="${escapeXml(processId)}" name="${escapeXml(processName)}" isExecutable="false">`);

  for (const node of nodes) {
    const kind = node.kind || 'task';
    let element = CANONICAL_TO_BPMN[kind] || 'task';
    if (kind === 'subprocess' && node.dataContract && node.dataContract.processId) {
      element = 'callActivity';
    }
    const attributes = [`id="${escapeXml(node.id || '')}"`];
    if (node.name) attributes.push(`name="${escapeXml(node.name)}"`);
    if (element === 'callActivity' && node.dataContract?.processId) {
      attributes.push(`calledElement="${escapeXml(node.dataContract.processId)}"`);
    }
    lines.push(`    <bpmn:${element} ${attributes.join(' ')} />`);
  }

  for (const edge of edges) {
    const kind = edge.kind || 'sequence';
    const element = CANONICAL_EDGE_TO_BPMN[kind] || 'sequenceFlow';
    const attributes = [
      `id="${escapeXml(edge.id || '')}"`,
      `sourceRef="${escapeXml(edge.sourceId || '')}"`,
      `targetRef="${escapeXml(edge.targetId || '')}"`,
    ];
    if (kind === 'conditional' && edge.condition) {
      lines.push(`    <bpmn:${element} ${attributes.join(' ')}>`);
      lines.push(`      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression"><![CDATA[${String(edge.condition).replace(/]]>/g, ']] ]]>')}]]></bpmn:conditionExpression>`);
      lines.push('    </bpmn:sequenceFlow>');
      continue;
    }
    lines.push(`    <bpmn:${element} ${attributes.join(' ')} />`);
  }

  lines.push('  </bpmn:process>');

  const diagram = [];
  for (const node of nodes) {
    const canvas = node.canvas || {};
    if (!Number.isFinite(canvas.x) || !Number.isFinite(canvas.y)) continue;
    const width = Number.isFinite(canvas.width) ? canvas.width : DEFAULT_DI_WIDTH;
    const height = Number.isFinite(canvas.height) ? canvas.height : DEFAULT_DI_HEIGHT;
    diagram.push(`      <bpmn:BPMNShape id="${escapeXml(`${node.id || ''}_di`)}" bpmnElement="${escapeXml(node.id || '')}">`);
    diagram.push(`        <bpmn:Bounds x="${canvas.x}" y="${canvas.y}" width="${width}" height="${height}" />`);
    diagram.push('      </bpmn:BPMNShape>');
  }
  for (const edge of edges) {
    const points = Array.isArray(edge.canvas?.points) ? edge.canvas.points : [];
    const usable = points.filter((point) => Number.isFinite(point?.x) && Number.isFinite(point?.y));
    if (!usable.length) continue;
    diagram.push(`      <bpmn:BPMNEdge id="${escapeXml(`${edge.id || ''}_di`)}" bpmnElement="${escapeXml(edge.id || '')}">`);
    for (const point of usable) {
      diagram.push(`        <bpmn:waypoint x="${point.x}" y="${point.y}" />`);
    }
    diagram.push('      </bpmn:BPMNEdge>');
  }
  if (diagram.length) {
    lines.push('  <bpmn:BPMNDiagram id="BPMNDiagram_GemArc">');
    lines.push(`    <bpmn:BPMNPlane id="BPMNPlane_GemArc" bpmnElement="${escapeXml(processId)}">`);
    lines.push(...diagram);
    lines.push('    </bpmn:BPMNPlane>');
    lines.push('  </bpmn:BPMNDiagram>');
  }

  lines.push('</bpmn:definitions>');
  return `${lines.join('\n')}\n`;
}

/** The BPMN adapter: a consistent parse / serialize pair. */
export const bpmnAdapter = {
  format: BPMN_FORMAT,
  supports: BPMN_FORMAT,
  parse: parseBpmn,
  serialize: serializeBpmn,
};
