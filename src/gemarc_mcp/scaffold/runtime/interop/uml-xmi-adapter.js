/**
 * UML XMI adapter - canonical model nodes/edges <-> UML XMI 2.x.
 *
 * Canonical subset covered (node.schema.json / edge.schema.json):
 *   - node kind Module  <-> `uml:Package`
 *   - node kind Class   <-> `uml:Class`     (attributes, methods, visibility)
 *   - node kind Interface <-> `uml:Interface`
 *   - edge type `inherits`   <-> `uml:Generalization` (specific -> general)
 *   - edge type `implements` <-> `uml:Realization`
 *   - edge type `dependsOn`  <-> `uml:Association`
 *   - `parentId` <-> the nesting of `packagedElement`
 *
 * Known lossy parts (XMI cannot express the canonical model):
 *   - the `uml:Model` element is the document container, not a canonical node:
 *     a model level package therefore has no parent after a round trip
 *   - stereotypes, constraints, multiplicities and tagged values are dropped
 *   - a canonical kind outside Module / Class / Interface (Entity, Table, ...)
 *     is carried in a `gemarc:kind` attribute; an XMI consumer that ignores
 *     that attribute sees a plain `uml:Class`
 *   - operation parameters survive, but their direction and default values do
 *
 * The XML scanner is shared with `bpmn-adapter.js` (dependency free, no
 * DOMParser).
 */
import { GemArcError } from '../core/errors.js';
import {
  parseXmlDocument,
  findAll,
  findFirst,
  childrenNamed,
  attr,
  attrRaw,
  escapeXml,
} from './bpmn-adapter.js';

/** Format identifier of this adapter. */
export const XMI_FORMAT = 'xmi';

const UML_TO_CANONICAL = {
  package: 'Module',
  class: 'Class',
  interface: 'Interface',
  enumeration: 'Class',
  datatype: 'Class',
};

const CANONICAL_TO_UML = {
  Module: 'Package',
  Class: 'Class',
  Interface: 'Interface',
};

const CANONICAL_KINDS = new Set(Object.keys(CANONICAL_TO_UML));

/**
 * Elements that belong to a node but are not nodes themselves. They are
 * read into `attributes[]` / `methods[]` by the owner, so walking into them
 * must neither create a node nor report them as unsupported.
 */
const MEMBER_ELEMENTS = new Set([
  'property',
  'operation',
  'parameter',
  'ownedmember',
  'memberend',
  'ownedattribute',
  'ownedoperation',
  'ownedparameter',
]);

function localOf(name) {
  const index = name.indexOf(':');
  return index === -1 ? name : name.slice(index + 1);
}

function prefixOf(name) {
  const index = name.indexOf(':');
  return index === -1 ? '' : name.slice(0, index);
}

function plainAttr(node, name) {
  if (!node || !node.attrs) return null;
  return Object.prototype.hasOwnProperty.call(node.attrs, name) ? node.attrs[name] : null;
}

/** The `xmi:type` of an element, reduced to its local name. */
function xmiTypeOf(node) {
  for (const [key, value] of Object.entries(node.attrs || {})) {
    if (localOf(key) === 'type' && prefixOf(key)) return localOf(value);
  }
  return node.name;
}

/** The GemArc kind carried by a `gemarc:kind` attribute, if any. */
function gemarcKindOf(node) {
  for (const [key, value] of Object.entries(node.attrs || {})) {
    if (localOf(key) === 'kind' && prefixOf(key) === 'gemarc') return value;
  }
  return null;
}

function idOf(node, fallback) {
  const id = attr(node, 'id') || plainAttr(node, 'xmi:id');
  return id || fallback;
}

function visibilityOf(node) {
  const visibility = attr(node, 'visibility');
  return visibility || null;
}

/**
 * Read a UML XMI document into canonical nodes and edges.
 * @param {string} input XMI XML
 * @returns {{nodes: object[], edges: object[], warnings: string[]}}
 */
export function parseXmi(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new GemArcError('GEMARC_INTEROP_001', 'XMI 输入必须是 XML 字符串');
  }
  const { root, warnings } = parseXmlDocument(input);
  const xmiRoot = findFirst(root, 'XMI');
  if (!xmiRoot) {
    throw new GemArcError('GEMARC_INTEROP_001', '不是 XMI 文档：缺少 XMI 根元素');
  }
  const model = findFirst(xmiRoot, 'Model');
  if (!model) {
    warnings.push('XMI 文档中没有 Model 元素');
    return { nodes: [], edges: [], warnings };
  }

  const nodes = [];
  const edges = [];
  const nodeIds = new Set();
  const usedIds = new Set();

  const uniqueId = (base) => {
    if (!usedIds.has(base)) {
      usedIds.add(base);
      return base;
    }
    let index = 2;
    while (usedIds.has(`${base}-${index}`)) index += 1;
    usedIds.add(`${base}-${index}`);
    return `${base}-${index}`;
  };

  const readAttributes = (owner) => {
    const attributes = [];
    for (const element of childrenNamed(owner, 'ownedAttribute')) {
      const name = attr(element, 'name');
      if (!name) continue;
      attributes.push({
        name,
        type: plainAttr(element, 'type') || attr(element, 'type') || null,
        visibility: visibilityOf(element),
        primaryKey: false,
        foreignKey: null,
        nullable: true,
      });
    }
    return attributes;
  };

  const readOperations = (owner) => {
    const methods = [];
    for (const element of childrenNamed(owner, 'ownedOperation')) {
      const name = attr(element, 'name');
      if (!name) continue;
      const parameters = childrenNamed(element, 'ownedParameter').map((parameter) => ({
        name: attr(parameter, 'name') || '',
        type: plainAttr(parameter, 'type') || null,
      }));
      methods.push({
        name,
        returnType: plainAttr(element, 'returnType') || null,
        visibility: visibilityOf(element),
        parameters,
      });
    }
    return methods;
  };

  const walk = (parentElement, parentId) => {
    for (const element of parentElement.children || []) {
      const type = xmiTypeOf(element);
      const typeKey = type.toLowerCase();
      const nameKey = element.name.toLowerCase();
      if (MEMBER_ELEMENTS.has(typeKey) || MEMBER_ELEMENTS.has(nameKey)) continue;

      if (['generalization', 'realization', 'association'].includes(typeKey)) {
        const id = uniqueId(idOf(element, `${typeKey}-${edges.length + 1}`));
        const general = attr(element, 'general') || attr(element, 'generalization');
        const specific = attr(element, 'specific') || attr(element, 'client');
        let sourceId = specific;
        let targetId = general;
        if (typeKey === 'association') {
          const members = childrenNamed(element, 'ownedMember')
            .map((member) => plainAttr(member, 'type'))
            .filter(Boolean);
          if (members.length >= 2) {
            sourceId = members[0];
            targetId = members[1];
          } else {
            const ends = childrenNamed(element, 'memberEnd');
            if (ends.length >= 2) {
              sourceId = attr(ends[0], 'type') || null;
              targetId = attr(ends[1], 'type') || null;
            }
          }
        }
        if (!sourceId || !targetId) {
          warnings.push(`XMI 关系缺少端点: ${id}`);
          continue;
        }
        edges.push({
          id,
          sourceId,
          targetId,
          type: typeKey === 'generalization' ? 'inherits' : (typeKey === 'realization' ? 'implements' : 'dependsOn'),
          directed: true,
          condition: null,
          properties: { uml: type },
        });
        continue;
      }

      const kind = gemarcKindOf(element) || UML_TO_CANONICAL[typeKey];
      if (!kind) {
        warnings.push(`忽略不支持的 UML 元素: ${type}`);
        continue;
      }
      const rawId = idOf(element, attr(element, 'name') || `${type.toLowerCase()}-${nodes.length + 1}`);
      const id = uniqueId(rawId);
      if (id !== rawId) warnings.push(`XMI 元素 id 重复，已改名: ${rawId} -> ${id}`);
      nodeIds.add(id);
      nodes.push({
        id,
        kind,
        name: attr(element, 'name') || id,
        parentId: parentId || null,
        attributes: readAttributes(element),
        methods: readOperations(element),
        properties: { uml: type },
      });
      walk(element, id);
    }
  };

  walk(model, null);

  for (const edge of edges) {
    if (!nodeIds.has(edge.sourceId)) warnings.push(`XMI 关系引用了不存在的节点: ${edge.sourceId}`);
    if (!nodeIds.has(edge.targetId)) warnings.push(`XMI 关系引用了不存在的节点: ${edge.targetId}`);
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
 * Write canonical class-like data as a UML XMI document.
 * @param {object} canonical canonical nodes and edges
 * @returns {string} XMI XML
 */
export function serializeXmi(canonical) {
  const nodes = canonicalNodesOf(canonical);
  const edges = canonicalEdgesOf(canonical);

  const childrenOf = (parentId) => nodes.filter((node) => (node.parentId || null) === parentId);
  const roots = childrenOf(null);

  const kindAttribute = (kind) => (
    CANONICAL_KINDS.has(kind) ? '' : ` gemarc:kind="${escapeXml(kind)}"`
  );

  const renderNode = (node, depth) => {
    const pad = '  '.repeat(depth);
    const type = CANONICAL_TO_UML[node.kind] || 'Class';
    const attributes = [
      `xmi:id="${escapeXml(node.id)}"`,
      `name="${escapeXml(node.name || node.id)}"`,
    ];
    const lines = [];
    const children = childrenOf(node.id);
    const owned = [];
    for (const attribute of node.attributes || []) {
      owned.push(
        `      <ownedAttribute xmi:id="${escapeXml(`${node.id}.${attribute.name}`)}"`
          + ` name="${escapeXml(attribute.name)}"`
          + ` type="${escapeXml(attribute.type || 'String')}"`
          + ` visibility="${escapeXml(attribute.visibility || 'public')}" />`,
      );
    }
    for (const method of node.methods || []) {
      const parameters = (Array.isArray(method.parameters) ? method.parameters : [])
        .filter((parameter) => parameter && parameter.name);
      const head = `      <ownedOperation xmi:id="${escapeXml(`${node.id}.${method.name}`)}"`
        + ` name="${escapeXml(method.name)}"`
        + ` returnType="${escapeXml(method.returnType || 'void')}"`
        + ` visibility="${escapeXml(method.visibility || 'public')}"`;
      if (!parameters.length) {
        owned.push(`${head} />`);
        continue;
      }
      owned.push(`${head}>`);
      for (const parameter of parameters) {
        owned.push(
          `        <ownedParameter xmi:id="${escapeXml(`${node.id}.${method.name}.${parameter.name}`)}"`
            + ` name="${escapeXml(parameter.name)}"`
            + ` type="${escapeXml(parameter.type || 'String')}" />`,
        );
      }
      owned.push('      </ownedOperation>');
    }
    if (!owned.length && !children.length) {
      lines.push(`${pad}<packagedElement xmi:type="uml:${type}" ${attributes.join(' ')}${kindAttribute(node.kind)} />`);
      return lines;
    }
    lines.push(`${pad}<packagedElement xmi:type="uml:${type}" ${attributes.join(' ')}${kindAttribute(node.kind)}>`);
    for (const line of owned) lines.push(line);
    for (const child of children) lines.push(...renderNode(child, depth + 1));
    lines.push(`${pad}</packagedElement>`);
    return lines;
  };

  const lines = [];
  lines.push('<?xml version="1.0" encoding="UTF-8"?>');
  lines.push(
    '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI"'
      + ' xmlns:uml="http://www.omg.org/spec/UML/20131001"'
      + ' xmlns:gemarc="urn:gemarc:uml">',
  );
  lines.push('  <uml:Model xmi:id="model_gemarc" name="GemArc">');
  for (const root of roots) lines.push(...renderNode(root, 2));

  for (const edge of edges) {
    const id = escapeXml(edge.id || '');
    if (edge.type === 'inherits') {
      lines.push(`    <generalization xmi:id="${id}" general="${escapeXml(edge.targetId)}" specific="${escapeXml(edge.sourceId)}" />`);
    } else if (edge.type === 'implements') {
      lines.push(`    <realization xmi:id="${id}" general="${escapeXml(edge.targetId)}" specific="${escapeXml(edge.sourceId)}" />`);
    } else {
      lines.push(`    <association xmi:id="${id}" name="">`);
      lines.push(`      <ownedMember xmi:type="uml:Property" type="${escapeXml(edge.sourceId)}" />`);
      lines.push(`      <ownedMember xmi:type="uml:Property" type="${escapeXml(edge.targetId)}" />`);
      lines.push('    </association>');
    }
  }

  lines.push('  </uml:Model>');
  lines.push('</xmi:XMI>');
  return `${lines.join('\n')}\n`;
}

/** The XMI adapter: a consistent parse / serialize pair. */
export const xmiAdapter = {
  format: XMI_FORMAT,
  supports: XMI_FORMAT,
  parse: parseXmi,
  serialize: serializeXmi,
};
