/**
 * Parity harness: run the browser runtime's own normalisation and fidelity
 * checks over a corpus, so the Python side can be compared against them.
 *
 * Two modes:
 *   {"mode": "fidelity", "cases": [{token, nodes, edges, nodeIds}]}
 *       -> [{"token": ..., "satisfied": bool}]
 *   {"mode": "aliases", "models": [ ...canonical models... ]}
 *       -> [{nodes: [{kind, name}], edges: [{type, directed, condition}],
 *           workflows: [{nodes: [{id, kind}], edges: [{id, kind, sourceId, targetId, condition}]}]}]
 *
 * The harness only reads; it never writes into the repository.
 */
import { readFileSync } from 'node:fs';
import { normalizeModel } from '../../src/gemarc_mcp/scaffold/runtime/core/model.js';
import { normalizeWorkflow } from '../../src/gemarc_mcp/scaffold/runtime/workflow/workflow-model.js';
import { checkFidelity } from '../../src/gemarc_mcp/scaffold/runtime/view/view-router.js';

const corpusPath = process.argv[2];
const corpus = JSON.parse(readFileSync(corpusPath, 'utf8'));

/** An absent value and an empty value mean the same thing here. */
function effective(value) {
  if (value === undefined || value === null || value === '') return null;
  return value;
}

if (corpus.mode === 'fidelity') {
  const out = corpus.cases.map((item) => ({
    token: item.token,
    satisfied: Boolean(checkFidelity(item.token, item.nodes || [], item.edges || [], item.nodeIds || [])),
  }));
  process.stdout.write(JSON.stringify(out));
} else if (corpus.mode === 'aliases') {
  const out = corpus.models.map((raw) => {
    const model = normalizeModel(raw);
    const workflows = (raw.workflows || []).map((workflow) => {
      const normalized = normalizeWorkflow(workflow);
      return {
        id: effective(normalized.id),
        nodes: normalized.nodes.map((node) => ({
          id: effective(node.id),
          kind: effective(node.kind),
          name: effective(node.name),
        })),
        edges: normalized.edges.map((edge) => ({
          id: effective(edge.id),
          kind: effective(edge.kind),
          sourceId: effective(edge.sourceId),
          targetId: effective(edge.targetId),
          condition: effective(edge.condition),
          directed: effective(edge.directed),
        })),
      };
    });
    return {
      nodes: model.nodes.map((node) => ({
        id: effective(node.id),
        kind: effective(node.kind),
        name: effective(node.name),
      })),
      edges: model.edges.map((edge) => ({
        id: effective(edge.id),
        type: effective(edge.type),
        directed: effective(edge.directed),
        condition: effective(edge.condition),
        sourceId: effective(edge.sourceId),
        targetId: effective(edge.targetId),
      })),
      workflows,
    };
  });
  process.stdout.write(JSON.stringify(out));
} else {
  process.stderr.write(`unknown mode: ${corpus.mode}\n`);
  process.exit(2);
}
