/**
 * Vocabulary accessors.
 *
 * The vocabulary is generated from `vocab/*.json` (see `sync.py`) and is the
 * same data the Python validator reads. Nothing in the runtime may hard-code
 * an evidence level, an entity kind or an edge type: it must come from here,
 * so the prompt, the validator and the browser cannot drift apart.
 */
import { VOCAB, VOCAB_DIGEST } from './vocab-data.js';

export { VOCAB, VOCAB_DIGEST };

const CANONICAL_EVIDENCE = VOCAB['evidence-levels'].canonical.map((item) => item.id);
const LEGACY_EVIDENCE = VOCAB['evidence-levels'].legacyAliases;
const EVIDENCE_RANK = new Map(
  VOCAB['evidence-levels'].canonical.map((item) => [item.id, item.rank]),
);
const ENTITY_KINDS = VOCAB['entity-kinds'].kinds.map((item) => item.id);
const EDGE_TYPES = VOCAB['edge-types'].types.map((item) => item.id);
const DIAGRAM_TYPES = VOCAB['diagram-types'].types.map((item) => item.id);
const RELATION_CLASSES = VOCAB['relation-classes'].classes.map((item) => item.id);
const CASE_CATEGORIES = VOCAB['case-categories'].categories.map((item) => item.id);
const WORKFLOW_NODE_KINDS = VOCAB['workflow-kinds'].nodeKinds.map((item) => item.id);
const WORKFLOW_EDGE_KINDS = VOCAB['workflow-kinds'].edgeKinds.map((item) => item.id);
const WORKFLOW_CONSTRAINTS = VOCAB['workflow-kinds'].executionConstraints.map((item) => item.id);
const SPACE_LEVELS = VOCAB['space-levels'].levels.map((item) => item.id);

export const CANONICAL_EVIDENCE_LEVELS = Object.freeze(CANONICAL_EVIDENCE);
export const ENTITY_KIND_IDS = Object.freeze(ENTITY_KINDS);
export const EDGE_TYPE_IDS = Object.freeze(EDGE_TYPES);
export const DIAGRAM_TYPE_IDS = Object.freeze(DIAGRAM_TYPES);
export const RELATION_CLASS_IDS = Object.freeze(RELATION_CLASSES);
export const CASE_CATEGORY_IDS = Object.freeze(CASE_CATEGORIES);
export const WORKFLOW_NODE_KIND_IDS = Object.freeze(WORKFLOW_NODE_KINDS);
export const WORKFLOW_EDGE_KIND_IDS = Object.freeze(WORKFLOW_EDGE_KINDS);
export const WORKFLOW_CONSTRAINT_IDS = Object.freeze(WORKFLOW_CONSTRAINTS);
export const SPACE_LEVEL_IDS = Object.freeze(SPACE_LEVELS);

export function isCanonicalEvidenceLevel(value) {
  return CANONICAL_EVIDENCE.includes(value);
}

export function isEntityKind(value) {
  return ENTITY_KINDS.includes(value);
}

export function isEdgeType(value) {
  return EDGE_TYPES.includes(value);
}

export function isDiagramType(value) {
  return DIAGRAM_TYPES.includes(value);
}

export function isCaseCategory(value) {
  return CASE_CATEGORIES.includes(value);
}

/**
 * Map a legacy or canonical evidence spelling onto the canonical level.
 * Unknown values are returned untouched so the validator can report them.
 * The comparison is case insensitive: a language model filling the model may
 * write "Illustrative" or "illustrative" and both mean the same level.
 */
export function canonicalizeEvidenceLevel(value) {
  if (typeof value !== 'string') return value;
  if (CANONICAL_EVIDENCE.includes(value)) return value;
  const lowered = value.toLowerCase();
  const canonical = CANONICAL_EVIDENCE.find((id) => id.toLowerCase() === lowered);
  if (canonical) return canonical;
  return LEGACY_EVIDENCE[lowered] || value;
}

/**
 * Evidence strength, 1 (Unknown) to 6 (Implemented). Unknown values score 0 so
 * that a strict check can reject them without special casing.
 */
export function evidenceRank(value) {
  return EVIDENCE_RANK.get(canonicalizeEvidenceLevel(value)) || 0;
}

export function edgeTypeSpec(type) {
  return VOCAB['edge-types'].types.find((item) => item.id === type) || null;
}

export function diagramTypeSpec(type) {
  return VOCAB['diagram-types'].types.find((item) => item.id === type) || null;
}

export function relationClassSpec(id) {
  return VOCAB['relation-classes'].classes.find((item) => item.id === id) || null;
}

export function entityKindSpec(kind) {
  return VOCAB['entity-kinds'].kinds.find((item) => item.id === kind) || null;
}
