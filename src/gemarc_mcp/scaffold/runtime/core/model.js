/**
 * Model normalisation and accessors.
 *
 * These rules are mirrored by `gemarc_mcp.model.normalized_model` on the
 * Python side. The two implementations must stay in step, because the golden
 * simulation cases are compared between them. Keep the rule list short and
 * mechanical so the mirror is easy to maintain.
 *
 * Normalisation rules:
 *   1. edge.source / edge.target are accepted and rewritten to sourceId / targetId
 *   2. evidence levels are canonicalised (confirmed -> Implemented, ...)
 *   3. every collection gets a default empty array
 *   4. meta gets schemaVersion / language / evidenceState defaults
 */
import { canonicalizeEvidenceLevel, isCanonicalEvidenceLevel } from './vocab.js';

export const SCHEMA_VERSION = '0.2.0';

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

/**
 * Return a new model with the canonical shape. The input is not mutated.
 */
export function normalizeModel(model) {
  const source = asObject(model);
  const normalized = { ...source };

  normalized.meta = { ...asObject(source.meta) };
  normalized.meta.schemaVersion = normalized.meta.schemaVersion || SCHEMA_VERSION;
  normalized.meta.language = normalized.meta.language || 'zh-CN';
  normalized.meta.evidenceState = canonicalizeEvidenceLevel(
    normalized.meta.evidenceState || 'Unknown',
  );

  normalized.nodes = asArray(source.nodes).map(normalizeNode);
  normalized.edges = asArray(source.edges).map(normalizeEdge);
  normalized.evidence = asArray(source.evidence).map(normalizeEvidence);
  normalized.views = asArray(source.views);
  normalized.scenarios = asArray(source.scenarios);
  normalized.cases = asArray(source.cases);
  normalized.workflows = asArray(source.workflows);
  normalized.prototypes = asArray(source.prototypes);
  normalized.space = source.space ? asObject(source.space) : null;

  return normalized;
}

export function normalizeNode(node) {
  const source = asObject(node);
  return {
    ...source,
    id: source.id,
    kind: source.kind,
    name: source.name,
    properties: asObject(source.properties),
    parentId: source.parentId ?? null,
    role: source.role ?? null,
    domainId: source.domainId ?? null,
    evidenceIds: asArray(source.evidenceIds),
    verification: canonicalizeEvidenceLevel(source.verification || 'Unknown'),
    spaceRef: source.spaceRef ?? null,
    attributes: asArray(source.attributes),
    methods: asArray(source.methods),
  };
}

export function normalizeEdge(edge) {
  // Copy first: normalisation must never mutate the caller's model.
  const source = { ...asObject(edge) };
  const sourceId = source.sourceId ?? source.source ?? null;
  const targetId = source.targetId ?? source.target ?? null;
  // Canonical output carries only sourceId / targetId. Consumers that still
  // read the legacy keys must accept both; see the runtime's graph store.
  delete source.source;
  delete source.target;
  // 0.1.x models called the relation type "kind"; 0.2.0 calls it "type".
  const edgeType = source.type ?? source.kind ?? null;
  delete source.kind;
  return {
    ...source,
    id: source.id,
    sourceId,
    targetId,
    type: edgeType,
    directed: source.directed === undefined ? true : Boolean(source.directed),
    condition: source.condition === '' ? null : (source.condition ?? null),
    weight: source.weight ?? null,
    properties: asObject(source.properties),
    evidenceIds: asArray(source.evidenceIds),
    verification: canonicalizeEvidenceLevel(source.verification || 'Unknown'),
    trustBoundary: source.trustBoundary ?? null,
    spaceSpan: source.spaceSpan ?? null,
  };
}

function normalizeEvidence(item) {
  const source = asObject(item);
  const level = canonicalizeEvidenceLevel(source.level || source.verification || 'Unknown');
  return {
    ...source,
    id: source.id,
    kind: source.kind,
    level,
    verification: level,
    source: asObject(source.source),
    notes: source.notes ?? null,
  };
}

/**
 * Index a normalised model for O(1) lookups.
 */
export function indexModel(model) {
  const normalized = model && model.nodes && model.edges ? model : normalizeModel(model);
  const nodes = new Map();
  const edges = new Map();
  const evidence = new Map();
  for (const node of normalized.nodes) nodes.set(node.id, node);
  for (const edge of normalized.edges) edges.set(edge.id, edge);
  for (const item of normalized.evidence) evidence.set(item.id, item);
  return { model: normalized, nodes, edges, evidence };
}

export function nodeById(index, id) {
  return index.nodes.get(id) || null;
}

export function edgeById(index, id) {
  return index.edges.get(id) || null;
}

export function evidenceFor(index, ids) {
  const result = [];
  for (const id of asArray(ids)) {
    const item = index.evidence.get(id);
    if (item) result.push(item);
  }
  return result;
}

export function nodesOfKind(index, kind) {
  return index.model.nodes.filter((node) => node.kind === kind);
}

export function edgesOfType(index, type) {
  return index.model.edges.filter((edge) => edge.type === type);
}

/**
 * Children of a node, in declaration order.
 */
export function childrenOf(index, parentId) {
  return index.model.nodes.filter((node) => node.parentId === parentId);
}

/**
 * The chain of ancestors, closest first.
 */
export function ancestorsOf(index, nodeId) {
  const chain = [];
  let current = nodeById(index, nodeId);
  const seen = new Set();
  while (current && current.parentId && !seen.has(current.parentId)) {
    seen.add(current.parentId);
    const parent = nodeById(index, current.parentId);
    if (!parent) break;
    chain.push(parent);
    current = parent;
  }
  return chain;
}

/**
 * True when the model uses only canonical evidence levels.
 */
export function hasCanonicalEvidence(model) {
  const levels = [
    model.meta?.evidenceState,
    ...asArray(model.nodes).map((node) => node.verification),
    ...asArray(model.edges).map((edge) => edge.verification),
  ].filter(Boolean);
  return levels.every((level) => isCanonicalEvidenceLevel(level));
}
