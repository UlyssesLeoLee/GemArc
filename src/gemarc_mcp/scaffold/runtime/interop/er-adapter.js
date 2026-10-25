/**
 * ER diagram adapter - canonical model nodes/edges <-> ER description.
 *
 * The ER description is a small, documented interchange form. Two input
 * flavours are accepted:
 *
 * (A) JSON object
 *   {
 *     "entities": [
 *       { "id": "customer", "name": "Customer", "kind": "Entity",
 *         "attributes": [ { "name": "id", "type": "int", "primaryKey": true } ] },
 *       { "id": "order", "name": "Order", "kind": "Table",
 *         "attributes": [ { "name": "id", "type": "int", "primaryKey": true } ] }
 *     ],
 *     "relations": [
 *       { "id": "rel-1", "from": "customer", "to": "order", "kind": "owns",
 *         "cardinality": { "from": "1", "to": "N" } }
 *     ]
 *   }
 *
 * (B) compact line DSL
 *   entity customer Customer
 *     attribute id int pk
 *     attribute name string
 *   table order Order
 *     attribute id int pk
 *   relation customer 1 order N owns
 *   (`attribute` flags: pk, fk=<ref>, notnull; `relation` cardinality may be
 *   1, N, M, 0..1, 1..* ...)
 *
 * Canonical subset covered (node.schema.json / edge.schema.json):
 *   - node kind Entity / Table with their `attributes[]`
 *   - node kind Attribute, bound to its entity through `parentId`
 *   - edge type `owns` / `composes` with the cardinality in
 *     `properties.cardinality` ({from, to})
 *
 * Known lossy parts (the ER form cannot express the canonical model):
 *   - only `owns` and `composes` relations are carried; every other edge type
 *     is dropped on write
 *   - the cardinality of an attribute itself is not modelled (only the
 *     relation cardinality is)
 *   - attribute visibility, foreign key targets and nullability survive only
 *     as far as the documented flags
 *
 * `serializeEr` writes the JSON flavour (A); it is the lossless direction.
 */
import { GemArcError } from '../core/errors.js';

/** Format identifier of this adapter. */
export const ER_FORMAT = 'er';

const RELATION_KINDS = new Set(['owns', 'composes']);

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function parseAttributeFlags(flags, warnings) {
  const attribute = {
    name: '',
    type: null,
    visibility: 'public',
    primaryKey: false,
    foreignKey: null,
    nullable: true,
  };
  for (const flag of flags) {
    const value = text(flag);
    if (!value) continue;
    if (value.toLowerCase() === 'pk') {
      attribute.primaryKey = true;
      attribute.nullable = false;
    } else if (value.toLowerCase() === 'notnull') {
      attribute.nullable = false;
    } else if (value.toLowerCase().startsWith('fk=')) {
      attribute.foreignKey = value.slice(3);
    } else if (value.toLowerCase().startsWith('visibility=')) {
      attribute.visibility = value.slice(11);
    } else {
      warnings.push(`ER: 无法识别的属性标记 "${value}"`);
    }
  }
  return attribute;
}

function parseAttributeObject(raw, warnings) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const attribute = {
    name: text(source.name),
    type: text(source.type) || null,
    visibility: text(source.visibility) || 'public',
    primaryKey: Boolean(source.primaryKey),
    foreignKey: text(source.foreignKey) || null,
    nullable: source.nullable === undefined ? true : Boolean(source.nullable),
  };
  if (!attribute.name) warnings.push('ER: 属性缺少名称');
  return attribute;
}

function cardinalityOf(raw) {
  if (!raw) return null;
  if (typeof raw === 'object') {
    const from = text(raw.from);
    const to = text(raw.to);
    if (!from && !to) return null;
    return { from: from || '1', to: to || 'N' };
  }
  const value = text(raw);
  if (!value) return null;
  const parts = value.split(/[:|]/);
  if (parts.length === 2) return { from: parts[0].trim(), to: parts[1].trim() };
  return { from: '1', to: value };
}

function cardinalityText(cardinality) {
  if (!cardinality) return null;
  return `${cardinality.from}:${cardinality.to}`;
}

function parseErDsl(source, warnings) {
  const entities = [];
  const relations = [];
  let current = null;
  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || line.startsWith('//')) continue;
    const tokens = line.split(/\s+/);
    const keyword = tokens[0].toLowerCase();
    if (keyword === 'entity' || keyword === 'table') {
      const id = text(tokens[1]);
      const name = text(tokens.slice(2).join(' ')) || id;
      if (!id) {
        warnings.push('ER DSL: 实体缺少 ID');
        current = null;
        continue;
      }
      current = { id, name, kind: keyword === 'table' ? 'Table' : 'Entity', attributes: [] };
      entities.push(current);
    } else if (keyword === 'attribute' || keyword === 'attr') {
      if (!current) {
        warnings.push('ER DSL: attribute 之前缺少实体');
        continue;
      }
      const attribute = parseAttributeFlags(tokens.slice(3), warnings);
      attribute.name = text(tokens[1]);
      attribute.type = text(tokens[2]) || null;
      if (!attribute.name) warnings.push('ER DSL: 属性缺少名称');
      current.attributes.push(attribute);
    } else if (keyword === 'relation' || keyword === 'rel') {
      const from = text(tokens[1]);
      const to = text(tokens[3]);
      if (!from || !to) {
        warnings.push('ER DSL: 关系缺少端点');
        continue;
      }
      relations.push({
        id: text(tokens[6]) || `rel-${relations.length + 1}`,
        from,
        to,
        kind: text(tokens[5]) || 'owns',
        cardinality: { from: text(tokens[2]) || '1', to: text(tokens[4]) || 'N' },
      });
      current = null;
    } else {
      warnings.push(`ER DSL: 无法识别的行 "${line}"`);
    }
  }
  return { entities, relations };
}

function normalizeEntity(raw, warnings) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const id = text(source.id);
  const name = text(source.name) || id;
  if (!id) {
    warnings.push('ER: 实体缺少 ID');
    return null;
  }
  const kind = text(source.kind) === 'Table' ? 'Table' : 'Entity';
  const attributes = (Array.isArray(source.attributes) ? source.attributes : [])
    .map((attribute) => parseAttributeObject(attribute, warnings))
    .filter((attribute) => attribute.name);
  return { id, name, kind, attributes };
}

function normalizeRelation(raw, warnings, index) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const from = text(source.from) || text(source.sourceId);
  const to = text(source.to) || text(source.targetId);
  if (!from || !to) {
    warnings.push('ER: 关系缺少端点');
    return null;
  }
  const kind = text(source.kind) || text(source.type) || 'owns';
  if (!RELATION_KINDS.has(kind)) {
    warnings.push(`ER: 不支持的关系类型 "${kind}"，按 owns 处理`);
  }
  return {
    id: text(source.id) || `rel-${index + 1}`,
    from,
    to,
    kind: RELATION_KINDS.has(kind) ? kind : 'owns',
    cardinality: cardinalityOf(source.cardinality),
  };
}

/**
 * Read an ER description into canonical nodes and edges.
 * @param {string|object} input ER description (JSON text or DSL text)
 * @returns {{nodes: object[], edges: object[], warnings: string[]}}
 */
export function parseEr(input) {
  const warnings = [];
  let entities = [];
  let relations = [];

  if (input && typeof input === 'object' && !Array.isArray(input)) {
    entities = Array.isArray(input.entities) ? input.entities : [];
    relations = Array.isArray(input.relations) ? input.relations : [];
    if (!entities.length && !relations.length) {
      throw new GemArcError('GEMARC_INTEROP_001', '不是 ER 描述：缺少 entities / relations');
    }
  } else if (typeof input === 'string' && input.trim()) {
    const trimmed = input.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      let parsed;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        throw new GemArcError('GEMARC_INTEROP_001', 'ER 描述不是有效的 JSON');
      }
      const source = Array.isArray(parsed) ? { entities: parsed } : parsed;
      if (!source || typeof source !== 'object') {
        throw new GemArcError('GEMARC_INTEROP_001', 'ER 描述不是有效的 JSON 对象');
      }
      if (!Array.isArray(source.entities) && !Array.isArray(source.relations)) {
        throw new GemArcError('GEMARC_INTEROP_001', '不是 ER 描述：缺少 entities / relations');
      }
      entities = Array.isArray(source.entities) ? source.entities : [];
      relations = Array.isArray(source.relations) ? source.relations : [];
    } else {
      const parsed = parseErDsl(trimmed, warnings);
      entities = parsed.entities;
      relations = parsed.relations;
      if (!entities.length && !relations.length) {
        throw new GemArcError('GEMARC_INTEROP_001', '不是 ER 描述：无法识别的格式');
      }
    }
  } else {
    throw new GemArcError('GEMARC_INTEROP_001', 'ER 输入必须是描述文本或对象');
  }

  const nodes = [];
  const edges = [];
  const entityIds = new Set();
  const attributeIds = new Set();

  for (const raw of entities) {
    const entity = normalizeEntity(raw, warnings);
    if (!entity) continue;
    if (entityIds.has(entity.id)) {
      warnings.push(`ER: 实体 ID 重复: ${entity.id}`);
      continue;
    }
    entityIds.add(entity.id);
    nodes.push({
      id: entity.id,
      kind: entity.kind,
      name: entity.name,
      parentId: null,
      attributes: entity.attributes,
      methods: [],
      properties: { er: 'entity' },
    });
    for (const attribute of entity.attributes) {
      let attributeId = `${entity.id}.${attribute.name}`;
      let index = 2;
      while (attributeIds.has(attributeId)) {
        attributeId = `${entity.id}.${attribute.name}-${index}`;
        index += 1;
      }
      attributeIds.add(attributeId);
      nodes.push({
        id: attributeId,
        kind: 'Attribute',
        name: attribute.name,
        parentId: entity.id,
        attributes: [],
        methods: [],
        properties: {
          er: 'attribute',
          type: attribute.type,
          primaryKey: attribute.primaryKey,
          foreignKey: attribute.foreignKey,
          nullable: attribute.nullable,
        },
      });
    }
  }

  relations.forEach((raw, index) => {
    const relation = normalizeRelation(raw, warnings, index);
    if (!relation) return;
    if (!entityIds.has(relation.from)) warnings.push(`ER: 关系引用了不存在的实体: ${relation.from}`);
    if (!entityIds.has(relation.to)) warnings.push(`ER: 关系引用了不存在的实体: ${relation.to}`);
    edges.push({
      id: relation.id,
      sourceId: relation.from,
      targetId: relation.to,
      type: relation.kind,
      directed: true,
      condition: null,
      properties: {
        cardinality: relation.cardinality,
        cardinalityText: cardinalityText(relation.cardinality),
      },
    });
  });

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
 * Write canonical nodes and edges as an ER description (JSON flavour).
 * @param {object} canonical canonical nodes and edges
 * @returns {string} ER description in JSON
 */
export function serializeEr(canonical) {
  const nodes = canonicalNodesOf(canonical);
  const edges = canonicalEdgesOf(canonical);

  const entities = [];

  for (const node of nodes) {
    if (node.kind !== 'Entity' && node.kind !== 'Table') continue;
    const attributes = [];
    const seen = new Set();
    for (const attribute of node.attributes || []) {
      if (!attribute || !attribute.name) continue;
      seen.add(attribute.name);
      attributes.push({
        name: attribute.name,
        type: attribute.type ?? null,
        primaryKey: Boolean(attribute.primaryKey),
        foreignKey: attribute.foreignKey ?? null,
        nullable: attribute.nullable === undefined ? true : Boolean(attribute.nullable),
      });
    }
    for (const child of nodes) {
      if (child.kind !== 'Attribute') continue;
      if ((child.parentId || null) !== node.id) continue;
      if (!child.name || seen.has(child.name)) continue;
      seen.add(child.name);
      attributes.push({
        name: child.name,
        type: child.properties?.type ?? null,
        primaryKey: Boolean(child.properties?.primaryKey),
        foreignKey: child.properties?.foreignKey ?? null,
        nullable: child.properties?.nullable === undefined ? true : Boolean(child.properties.nullable),
      });
    }
    entities.push({
      id: node.id,
      name: node.name || node.id,
      kind: node.kind,
      attributes,
    });
  }

  const relations = [];
  for (const edge of edges) {
    if (!RELATION_KINDS.has(edge.type)) continue;
    const cardinality = edge.properties?.cardinality
      || cardinalityOf(edge.properties?.cardinalityText);
    relations.push({
      id: edge.id,
      from: edge.sourceId,
      to: edge.targetId,
      kind: edge.type,
      cardinality: cardinality || null,
    });
  }

  return `${JSON.stringify({ entities, relations }, null, 2)}\n`;
}

/** The ER adapter: a consistent parse / serialize pair. */
export const erAdapter = {
  format: ER_FORMAT,
  supports: ER_FORMAT,
  parse: parseEr,
  serialize: serializeEr,
};
