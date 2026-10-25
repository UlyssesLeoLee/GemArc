/**
 * Draw.io adapter - canonical model nodes/edges <-> `mxGraphModel` XML.
 *
 * Canonical subset covered (node.schema.json / edge.schema.json):
 *   - node id / name / kind   <-> `<mxCell vertex="1" value="...">`
 *   - node geometry            <-> `<mxGeometry x y width height>` (canvas)
 *   - edge id / endpoints      <-> `<mxCell edge="1" source= target=>`
 *   - edge type                <-> the `gemarcType=` style key
 *   - node kind                <-> the `gemarcKind=` style key
 *
 * Known lossy parts (the draw.io form cannot express the canonical model):
 *   - the draw.io shape style (colours, shadows, custom shapes) is reduced to
 *     the two GemArc style keys and is otherwise lost
 *   - the `parent` attribute addresses a draw.io layer, not a canonical node:
 *     canonical `parentId` stays empty
 *   - labels keep their text but lose inline HTML formatting
 *   - only one page is written; a multi page document is flattened
 *
 * The XML scanner is shared with `bpmn-adapter.js` (dependency free, no
 * DOMParser).
 */
import { GemArcError } from '../core/errors.js';
import {
  parseXmlDocument,
  findFirst,
  childrenNamed,
  attr,
  escapeXml,
} from './bpmn-adapter.js';

/** Format identifier of this adapter. */
export const DRAWIO_FORMAT = 'drawio';

const DEFAULT_NODE_KIND = 'Component';
const DEFAULT_EDGE_TYPE = 'dependsOn';

/**
 * Split a draw.io style string into its key/value pairs.
 * @param {string} style
 */
export function parseStyle(style) {
  const result = {};
  if (typeof style !== 'string' || !style) return result;
  for (const part of style.split(';')) {
    const index = part.indexOf('=');
    if (index === -1) continue;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (key) result[key] = value;
  }
  return result;
}

/**
 * Build a draw.io style string from key/value pairs.
 * @param {Record<string, string|number>} pairs
 */
export function buildStyle(pairs) {
  return Object.entries(pairs)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${key}=${String(value).replace(/[;="]/g, '')}`)
    .join(';');
}

/**
 * Strip inline HTML from a draw.io label.
 * @param {string} value
 */
export function plainLabel(value) {
  if (typeof value !== 'string') return '';
  return value.replace(/<[^>]*>/g, '').trim();
}

function geometryOf(cell) {
  const geometry = childrenNamed(cell, 'mxGeometry')[0];
  return geometry || null;
}

function numberAttr(node, name) {
  const value = Number(attr(node, name, NaN));
  return Number.isFinite(value) ? value : null;
}

/**
 * Read a draw.io / diagrams.net document into canonical nodes and edges.
 * @param {string} input mxGraphModel XML
 * @returns {{nodes: object[], edges: object[], warnings: string[]}}
 */
export function parseDrawio(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new GemArcError('GEMARC_INTEROP_001', 'Draw.io 输入必须是 XML 字符串');
  }
  const { root, warnings } = parseXmlDocument(input);
  const graphModel = findFirst(root, 'mxGraphModel');
  if (!graphModel) {
    throw new GemArcError('GEMARC_INTEROP_001', '不是 Draw.io 文档：缺少 mxGraphModel 元素');
  }
  const graphRoot = childrenNamed(graphModel, 'root')[0];
  if (!graphRoot) {
    warnings.push('Draw.io 文档中没有 root 元素');
    return { nodes: [], edges: [], warnings };
  }

  const nodes = [];
  const edges = [];
  const nodeIds = new Set();

  for (const cell of childrenNamed(graphRoot, 'mxCell')) {
    const id = attr(cell, 'id');
    if (!id) {
      warnings.push('Draw.io 单元格缺少 id');
      continue;
    }
    const style = parseStyle(attr(cell, 'style', ''));
    const isEdge = attr(cell, 'edge') === '1' || style.edge === '1';
    const isVertex = attr(cell, 'vertex') === '1' || style.vertex === '1';

    if (isEdge) {
      const sourceId = attr(cell, 'source');
      const targetId = attr(cell, 'target');
      if (!sourceId || !targetId) {
        warnings.push(`Draw.io 连线缺少端点: ${id}`);
        continue;
      }
      edges.push({
        id,
        sourceId,
        targetId,
        type: style.gemarcType || DEFAULT_EDGE_TYPE,
        directed: style.gemarcDirected !== '0',
        condition: null,
        properties: { drawio: 'edge' },
      });
      continue;
    }
    if (!isVertex) continue;

    nodeIds.add(id);
    const geometry = geometryOf(cell);
    let nodeCanvas = null;
    if (geometry) {
      const x = numberAttr(geometry, 'x');
      const y = numberAttr(geometry, 'y');
      const width = numberAttr(geometry, 'width');
      const height = numberAttr(geometry, 'height');
      if (x !== null || y !== null) {
        nodeCanvas = {
          ...(x !== null ? { x } : {}),
          ...(y !== null ? { y } : {}),
          ...(width !== null ? { width } : {}),
          ...(height !== null ? { height } : {}),
        };
      }
    }
    nodes.push({
      id,
      kind: style.gemarcKind || DEFAULT_NODE_KIND,
      name: plainLabel(attr(cell, 'value', '')) || id,
      parentId: null,
      attributes: [],
      methods: [],
      properties: { drawio: 'vertex' },
      canvas: nodeCanvas,
    });
  }

  for (const edge of edges) {
    if (!nodeIds.has(edge.sourceId)) warnings.push(`Draw.io 连线引用了不存在的节点: ${edge.sourceId}`);
    if (!nodeIds.has(edge.targetId)) warnings.push(`Draw.io 连线引用了不存在的节点: ${edge.targetId}`);
  }

  return { nodes, edges, warnings };
}

function canonicalNodesOf(canonical) {
  if (canonical && Array.isArray(canonical.nodes)) return canonical.nodes;
  if (canonical && canonical.model && Array.isArray(canonical.model.nodes)) return canonical.model.nodes;
  return [];
}

function canonicalEdgesOf(canonical) {
  if (canonical && Array.isArray(canonical.edges)) return canonical.edges;
  if (canonical && canonical.model && Array.isArray(canonical.model.edges)) return canonical.model.edges;
  return [];
}

/**
 * Write canonical nodes and edges as a draw.io `mxGraphModel` document.
 * @param {object} canonical canonical nodes and edges
 * @returns {string} draw.io XML
 */
export function serializeDrawio(canonical) {
  const nodes = canonicalNodesOf(canonical);
  const edges = canonicalEdgesOf(canonical);
  const name = text(canonical?.name || canonical?.workflow?.name || 'GemArc');

  const lines = [];
  lines.push('<?xml version="1.0" encoding="UTF-8"?>');
  lines.push('<mxfile host="app.diagrams.net" agent="gemarc" type="device">');
  lines.push('  <diagram id="gemarc-diagram" name="Page-1">');
  lines.push(
    '    <mxGraphModel dx="1000" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1"'
      + ' connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="1100"'
      + ' math="0" shadow="0">',
  );
  lines.push('      <root>');
  lines.push('        <mxCell id="0" />');
  lines.push('        <mxCell id="1" parent="0" />');

  for (const node of nodes) {
    const style = buildStyle({
      rounded: '0',
      whiteSpace: 'wrap',
      html: '1',
      gemarcKind: node.kind || DEFAULT_NODE_KIND,
    });
    const canvas = node.canvas || {};
    const x = Number.isFinite(canvas.x) ? canvas.x : 0;
    const y = Number.isFinite(canvas.y) ? canvas.y : 0;
    const width = Number.isFinite(canvas.width) ? canvas.width : 160;
    const height = Number.isFinite(canvas.height) ? canvas.height : 60;
    lines.push(
      `        <mxCell id="${escapeXml(node.id)}" value="${escapeXml(node.name || node.id)}"`
        + ` style="${escapeXml(style)}" vertex="1" parent="1">`,
    );
    lines.push(
      `          <mxGeometry x="${x}" y="${y}" width="${width}" height="${height}" as="geometry" />`,
    );
    lines.push('        </mxCell>');
  }

  for (const edge of edges) {
    const style = buildStyle({
      edgeStyle: 'orthogonalEdgeStyle',
      html: '1',
      gemarcType: edge.type || DEFAULT_EDGE_TYPE,
      gemarcDirected: edge.directed === false ? '0' : '1',
    });
    lines.push(
      `        <mxCell id="${escapeXml(edge.id)}" style="${escapeXml(style)}" edge="1" parent="1"`
        + ` source="${escapeXml(edge.sourceId)}" target="${escapeXml(edge.targetId)}">`,
    );
    lines.push('          <mxGeometry relative="1" as="geometry" />');
    lines.push('        </mxCell>');
  }

  lines.push('      </root>');
  lines.push('    </mxGraphModel>');
  lines.push('  </diagram>');
  lines.push('</mxfile>');
  return `${lines.join('\n')}\n`;
}

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

/** The Draw.io adapter: a consistent parse / serialize pair. */
export const drawioAdapter = {
  format: DRAWIO_FORMAT,
  supports: DRAWIO_FORMAT,
  parse: parseDrawio,
  serialize: serializeDrawio,
};
