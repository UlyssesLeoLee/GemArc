"""v6.0 规格覆盖断言：MCP 的模型层必须能表达 Universal_6D_GemArc_Web_v6.0 要求的内容。

规格把该有的字段和枚举逐条列了出来（§3 canonical_model、§9 supported_diagrams、
§13 case_types、§14 animation_primitives、§17 GWF-IR 6D 扩展、§25 执行图 IR、
quality_gates_6d）。这里把那些列表原样抄下来，钉在词表与 JSON Schema 上：规格变了
而 MCP 没跟上，这些断言就红。

覆盖不了的东西不许假装能：每个断言要么检查“能表达”，要么检查“缺了会被标成缺失”。

这一版同时钉住了 2026-11-03 对抗式审核发现的缺陷：别名归一（caseId / direction）、
两侧保真判定同源、证据反向索引覆盖工作流与 Case、6D 质量门、入口点回退。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import model as model_lib, stateless  # noqa: E402
from gemarc_mcp.scaffold import derive  # noqa: E402

VOCAB_DIR = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "vocab"
CONTRACTS_DIR = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "contracts"
RUNTIME_DIR = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "runtime"
PARITY_HARNESS = REPO_ROOT / "tests" / "js" / "parity-harness.mjs"
NODE = shutil.which("node")


def _vocab(name):
    return json.loads((VOCAB_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _schema(name):
    # The contract files are named "<object>.schema.json".
    return json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


# --- the specification, quoted -----------------------------------------------

#: v6.0 §3 canonical_model.required_entity_capabilities
SPEC_ENTITY_KINDS = [
    "System", "Subsystem", "Module", "Component", "Class", "Interface", "Method",
    "Entity", "Table", "Attribute", "Actor", "Event", "State", "Relationship",
    "Constraint", "Evidence", "Scenario", "CausalRule",
]

#: v6.0 §3 canonical_model.verification_levels
SPEC_EVIDENCE_LEVELS = [
    "Implemented", "Observed", "Designed", "Inferred", "Hypothetical", "Unknown",
]

#: v6.0 §3 canonical_model.node_fields
SPEC_NODE_FIELDS = [
    "id", "type", "label", "qualifiedName", "properties", "role", "domainId",
    "parentId", "sourceLocation", "evidenceIds", "verification", "version",
]

#: v6.0 §3 canonical_model.edge_fields
SPEC_EDGE_FIELDS = [
    "id", "sourceId", "targetId", "type", "directed", "properties", "weight",
    "condition", "evidenceIds", "verification", "version",
]

#: v6.0 §3 canonical_model.provenance_fields
SPEC_PROVENANCE_FIELDS = ["repository", "commit", "path", "startLine", "endLine", "evidenceId"]

#: v6.0 §3 canonical_model.output_files, as this repository ships them.
#: The specification's name for the diagram catalogue is on the repository's
#: hashed denylist (it belongs to a reference system), so it is not repeated
#: here; the MCP ships the same content as ``data/diagram-catalog.json``.
SPEC_ARCHITECTURE_OUTPUT_FILES = ["architecture.json", "evidence-index.json", "diagram-catalog.json"]

#: v6.0 §9 supported_diagrams
SPEC_DIAGRAM_FAMILIES = {
    "UML": ["Class", "Component", "Package", "Sequence", "StateMachine", "Activity",
            "Deployment", "UseCase"],
    "Data": ["ER", "DataFlow", "DataDependency"],
    "Architecture": ["Logical", "Development", "Process", "Deployment", "Data", "Security",
                     "Operations", "Dependency", "EventFlow", "Causal"],
}

#: v6.0 §13 case_types
SPEC_CASE_TYPES = [
    "Normal", "Boundary", "Error", "Timeout", "Retry", "Recovery", "Compensation",
    "Concurrency", "RaceCondition", "ResourceContention", "PartialFailure",
    "CascadingFailure", "Security", "Combined",
]

#: v6.0 §13 case_contract
SPEC_CASE_CONTRACT = [
    "caseId", "name", "description", "category", "initialState", "inputEvents",
    "preconditions", "executionRules", "expectedOutcomes", "terminationConditions",
    "assertions", "evidenceIds", "assumptions",
]

#: v6.0 §13 case reality marking
SPEC_CASE_REALITY = ["Observed", "ImplementedModel", "DesignedModel", "Hypothetical"]

#: v6.0 §17 workflow_fields (GWF-IR 6D 扩展)
SPEC_WORKFLOW_FIELDS = [
    "workflowId", "name", "version", "entryPoints", "nodes", "edges", "dataSchemas",
    "executionPolicy", "errorPolicy", "permissions", "evidenceIds", "metadata",
    "executionGraph", "stateSchemaVersion", "checkpointPolicy", "approvalPolicies",
    "executionCapabilities", "runSecurityPolicy",
]

#: v6.0 §17 node_fields
SPEC_WORKFLOW_NODE_FIELDS = [
    "nodeId", "nodeType", "name", "description", "inputs", "outputs", "ports",
    "preconditions", "postconditions", "stateTransitions", "timeoutPolicy",
    "retryPolicy", "errorHandlers", "sideEffects", "idempotency",
    "implementationRef", "evidenceIds", "verification", "executorKind", "authorityScope",
    "executionMode", "inputStateSchema", "outputStateSchema", "stateWritePolicy",
    "checkpointBefore", "checkpointAfter", "budgetPolicy", "iterationLimit",
    "approvalPolicyRef", "toolContractRef",
]

#: v6.0 §17 edge_fields
SPEC_WORKFLOW_EDGE_FIELDS = [
    "edgeId", "sourceNodeId", "sourcePortId", "targetNodeId", "targetPortId",
    "edgeType", "direction", "guard", "dataMapping", "priority", "errorRoute",
    "verification", "evidenceIds", "stateSchemaRef", "stateProjection",
    "stateMergePolicy", "routingExpression", "deliverySemantics", "resumeBehavior",
    "policyRef",
]

#: v6.0 §17 port_fields
SPEC_PORT_FIELDS = ["portId", "direction", "schema", "cardinality", "required", "acceptedTypes"]

#: v6.0 §17 edge_types
SPEC_WORKFLOW_EDGE_TYPES = [
    "ControlFlow", "DataFlow", "EventFlow", "ErrorFlow", "ConditionalFlow",
    "CompensationFlow", "Dependency", "StateTransition", "ApprovalTransition",
    "ToolResult", "RetryTransition", "TimeoutTransition", "ResumeTransition",
]

#: v6.0 §17 node_types
SPEC_WORKFLOW_NODE_TYPES = [
    "Start", "End", "Task", "Input", "Output", "Decision", "ParallelFork",
    "ParallelJoin", "Event", "Timer", "Wait", "Agent", "HumanApproval", "APICall",
    "DataTransform", "Subflow", "ErrorHandler", "Retry", "Compensation",
    "DeterministicProgram", "AgentLoop", "ToolCall", "PolicyGate", "Checkpoint",
    "Resume", "ManualReview", "Escalation", "AuditEvent",
]

#: v6.0 §14 animation_primitives
SPEC_ANIMATION_PRIMITIVES = [
    "Propagation", "Branching", "Parallel", "Waiting", "Timeout", "Retry", "Blocking",
    "FailurePropagation", "Recovery", "Compensation", "FeedbackLoop", "Convergence",
    "StateTransition", "DataTransformation", "Escalation", "Cancellation", "Rollback",
    "OutcomeComparison",
]

#: v6.0 §14 composition_modes
SPEC_COMPOSITION_MODES = [
    "Sequential", "Parallel", "Conditional", "Nested", "Repeated", "Interrupted",
    "ReversibleVisualReplay", "Synchronized", "Branched", "Converged",
]

#: v6.0 §25 node_kinds (the 6D execution graph)
SPEC_EXECUTION_KINDS = [
    "AgentLoop", "DeterministicProgram", "HumanApproval", "ToolCall", "PolicyGate",
    "Checkpoint", "JoinFork", "RetryTimeout", "Compensation", "End",
]

#: v6.0 §25 execution_node_required_fields
SPEC_EXECUTION_NODE_FIELDS = [
    "nodeId", "kind", "executorRef", "inputSchema", "outputSchema", "stateReadSet",
    "stateWriteSet", "timeout", "retry", "idempotencyKeyStrategy", "authorityPolicyRef",
    "evidenceIds", "verification",
]

#: v6.0 §25 execution_edge_required_fields
SPEC_EXECUTION_EDGE_FIELDS = [
    "edgeId", "sourceNodeId", "sourcePortId", "targetNodeId", "targetPortId", "kind",
    "guardExpression", "dataMapping", "stateSchemaRef", "priority", "onError",
    "onTimeout", "deliverySemantics", "checkpointBoundary", "evidenceIds",
]

#: v6.0 §25 run_required_fields
SPEC_RUN_FIELDS = [
    "runId", "workflowId", "workflowVersion", "stateSchemaVersion", "mode", "status",
    "logicalTime", "currentNodeIds", "pendingEdgeIds", "checkpointId", "actorId",
    "correlationId", "traceId",
]

#: v6.0 quality_gates_6d.reject_on, plus the four WBS gates of ADR-015 (6D = WBS × execution graph)
SPEC_QUALITY_GATES = [
    "unbounded_agent_loop", "human_approval_bypass", "unvalidated_state_schema",
    "missing_route_guard", "unsafe_side_effect_replay", "checkpoint_without_state_integrity",
    "fake_production_execution", "untraceable_decision",
    "wbs_orphan_node", "wbs_broken_hierarchy", "wbs_missing_deliverable", "wbs_unbound_package",
]

#: v6.0 §32 the deliverable files of the target project, as this repository
#: ships them (see SPEC_ARCHITECTURE_OUTPUT_FILES for the one renamed file).
SPEC_DELIVERABLE_FILES = [
    "architecture.json", "evidence-index.json", "diagram-catalog.json", "spatial-layout.json",
    "workflow-schema.json", "workflow-ir.json", "case-library.json",
    "execution-graph.schema.json", "execution-graph.example.json",
]


class VocabularyCoverageTests(unittest.TestCase):
    """The vocabulary must carry every type the specification names."""

    def test_entity_kinds_cover_the_specification(self):
        kinds = {item["id"] for item in _vocab("entity-kinds")["kinds"]}
        self.assertLessEqual(set(SPEC_ENTITY_KINDS), kinds)

    def test_evidence_levels_are_exactly_the_specification(self):
        levels = [item["id"] for item in _vocab("evidence-levels")["canonical"]]
        self.assertEqual(levels, SPEC_EVIDENCE_LEVELS)

    def test_diagram_families_cover_the_specification(self):
        families = _vocab("diagram-types")["specFamilies"]
        self.assertEqual(sorted(families), sorted(SPEC_DIAGRAM_FAMILIES))
        for family, expected in SPEC_DIAGRAM_FAMILIES.items():
            self.assertEqual(len(families[family]), len(expected), family)
            self.assertTrue(set(families[family]) <= {t["id"] for t in _vocab("diagram-types")["types"]},
                            family)

    def test_every_diagram_type_belongs_to_exactly_one_family(self):
        types = _vocab("diagram-types")["types"]
        families = _vocab("diagram-types")["specFamilies"]
        assigned = [item for ids in families.values() for item in ids]
        self.assertEqual(len(assigned), len(set(assigned)), "a type is in two families")
        self.assertEqual(set(assigned), {item["id"] for item in types})

    def test_case_types_are_exactly_the_specification(self):
        types = _vocab("case-types")["types"]
        self.assertEqual([item["id"] for item in types], SPEC_CASE_TYPES)

    def test_case_reality_values_are_exactly_the_specification(self):
        self.assertEqual(_vocab("case-types")["reality"]["values"], SPEC_CASE_REALITY)

    def test_the_composition_modes_are_published(self):
        self.assertEqual(_vocab("case-types")["compositionModes"]["ids"], SPEC_COMPOSITION_MODES)

    def test_the_illustrative_compositions_are_recorded(self):
        entries = _vocab("case-types")["illustrativeCompositions"]["entries"]
        recorded = {item["caseType"]: item["sequence"] for item in entries}
        self.assertEqual(set(recorded), {"Normal", "Timeout", "Recovery",
                                         "CascadingFailure", "Compensation"})
        for case_type, sequence in recorded.items():
            self.assertTrue(sequence, case_type)

    def test_workflow_node_types_cover_the_specification(self):
        kinds = _vocab("workflow-kinds")["nodeKinds"]
        mapped = {item.get("specType") for item in kinds if item.get("specType")}
        self.assertEqual(mapped, set(SPEC_WORKFLOW_NODE_TYPES))

    def test_workflow_edge_types_cover_the_specification(self):
        kinds = _vocab("workflow-kinds")["edgeKinds"]
        mapped = {item.get("specType") for item in kinds if item.get("specType")}
        self.assertEqual(mapped, set(SPEC_WORKFLOW_EDGE_TYPES))

    def test_the_execution_kinds_are_published(self):
        kinds = _vocab("workflow-kinds")["executionKinds"]
        self.assertEqual(len(kinds), len(SPEC_EXECUTION_KINDS))
        self.assertTrue(all(item.get("note") for item in kinds))

    def test_the_execution_field_catalogues_are_published(self):
        execution = _vocab("workflow-kinds")["executionGraph"]
        self.assertEqual(execution["nodeFields"], SPEC_EXECUTION_NODE_FIELDS)
        self.assertEqual(execution["edgeFields"], SPEC_EXECUTION_EDGE_FIELDS)
        self.assertEqual(execution["runFields"], SPEC_RUN_FIELDS)
        self.assertTrue(execution["stateContract"])
        self.assertTrue(execution["compilerGates"])

    def test_the_quality_gates_are_published(self):
        self.assertEqual(_vocab("workflow-kinds")["qualityGates"]["rejectOn"], SPEC_QUALITY_GATES)

    def test_the_animation_primitives_are_published(self):
        primitives = _vocab("case-types")["animationPrimitives"]["ids"]
        self.assertEqual(primitives, SPEC_ANIMATION_PRIMITIVES)

    def test_every_case_type_declares_only_real_primitives(self):
        known = set(SPEC_ANIMATION_PRIMITIVES)
        for item in _vocab("case-types")["types"]:
            for primitive in item.get("primitives", []):
                self.assertIn(primitive, known, item["id"])

    def test_every_diagram_fidelity_token_has_a_probe(self):
        """A fidelity element without a probe would always read as missing."""
        for item in _vocab("diagram-types")["types"]:
            for token in item.get("fidelity", []):
                self.assertIn(token, derive._FIDELITY_RULES, f"{item['id']}:{token}")

    def test_every_diagram_fidelity_token_has_a_runtime_rule(self):
        """The runtime must know the token too, or the site reports it missing forever.

        The runtime is the authority: the derived catalogue only predicts it.
        A token the runtime does not know falls back to reading a path named
        after the token, never finds it, and reports the element as missing for
        every model.
        """
        source = (RUNTIME_DIR / "view" / "view-router.js").read_text(encoding="utf-8")
        block = source.split("const FIDELITY_RULES = {", 1)[1].split("\n};", 1)[0]
        declared = set(re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):\s*\{", block, re.M))
        for item in _vocab("diagram-types")["types"]:
            for token in item.get("fidelity", []):
                self.assertIn(token, declared, f"{item['id']}:{token}")

    def test_the_python_and_runtime_fidelity_tables_agree(self):
        """Same tokens on both sides: a token one side knows and the other does not
        makes the catalogue contradict the site."""
        source = (RUNTIME_DIR / "view" / "view-router.js").read_text(encoding="utf-8")
        block = source.split("const FIDELITY_RULES = {", 1)[1].split("\n};", 1)[0]
        declared = set(re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):\s*\{", block, re.M))
        self.assertEqual(declared, set(derive._FIDELITY_RULES))


class SchemaFieldTests(unittest.TestCase):
    """The JSON Schemas must declare the fields the specification names."""

    def test_node_schema_declares_the_specification_fields(self):
        schema = _schema("node")
        for field in SPEC_NODE_FIELDS:
            self.assertIn(field, schema["properties"], field)
        self.assertNotIn("sourceLocation", schema["required"])

    def test_edge_schema_declares_the_specification_fields(self):
        schema = _schema("edge")
        for field in SPEC_EDGE_FIELDS:
            self.assertIn(field, schema["properties"], field)
        self.assertNotIn("version", schema["required"])

    def test_provenance_fields_are_declared(self):
        node = _schema("node")["properties"]["sourceLocation"]["properties"]
        for field in SPEC_PROVENANCE_FIELDS:
            if field == "evidenceId":
                continue  # the evidence id is the record's own id
            self.assertIn(field, node, field)
        evidence = _schema("evidence")["properties"]["source"]["properties"]
        for field in ("path", "startLine", "endLine"):
            self.assertIn(field, evidence, field)

    def test_workflow_schema_declares_the_gwf_ir_fields(self):
        schema = _schema("workflow")
        for field in SPEC_WORKFLOW_FIELDS:
            self.assertIn(field, schema["properties"], field)
        node = schema["properties"]["nodes"]["items"]["properties"]
        for field in SPEC_WORKFLOW_NODE_FIELDS:
            self.assertIn(field, node, field)
        edge = schema["properties"]["edges"]["items"]["properties"]
        for field in SPEC_WORKFLOW_EDGE_FIELDS:
            self.assertIn(field, edge, field)
        port = node["ports"]["items"]["properties"]
        for field in SPEC_PORT_FIELDS:
            self.assertIn(field, port, field)

    def test_workflow_enums_carry_the_specification_types(self):
        schema = _schema("workflow")
        node_kinds = schema["properties"]["nodes"]["items"]["properties"]["kind"]["enum"]
        edge_kinds = schema["properties"]["edges"]["items"]["properties"]["kind"]["enum"]
        vocab_kinds = {item["id"] for item in _vocab("workflow-kinds")["nodeKinds"]}
        vocab_edges = {item["id"] for item in _vocab("workflow-kinds")["edgeKinds"]}
        self.assertEqual(set(node_kinds), vocab_kinds)
        self.assertEqual(set(edge_kinds), vocab_edges)

    def test_case_schema_declares_the_case_contract(self):
        schema = _schema("scenario")
        case = schema["properties"]["cases"]["items"]["properties"]
        for field in SPEC_CASE_CONTRACT:
            self.assertIn(field, case, field)
        self.assertEqual(set(case["caseType"]["enum"]) - {None}, set(SPEC_CASE_TYPES))
        self.assertEqual(set(case["reality"]["enum"]) - {None}, set(SPEC_CASE_REALITY))

    def test_view_schema_lists_every_registered_diagram_type(self):
        schema = _schema("view")
        registered = {item["id"] for item in _vocab("diagram-types")["types"]}
        self.assertEqual(set(schema["properties"]["diagramType"]["enum"]), registered)

    def test_the_execution_graph_schema_declares_the_specification_fields(self):
        schema = _schema("execution-graph")
        node = schema["$defs"]["executionNode"]["properties"]
        for field in SPEC_EXECUTION_NODE_FIELDS:
            self.assertIn(field, node, field)
        edge = schema["$defs"]["executionEdge"]["properties"]
        for field in SPEC_EXECUTION_EDGE_FIELDS:
            self.assertIn(field, edge, field)
        run = schema["$defs"]["run"]["properties"]
        for field in SPEC_RUN_FIELDS:
            self.assertIn(field, run, field)

    def test_the_execution_graph_example_matches_its_schema(self):
        schema = _schema("execution-graph")
        example = derive.execution_graph_example()
        kinds = set(schema["$defs"]["executionNode"]["properties"]["kind"]["enum"])
        for node in example["nodes"]:
            self.assertIn(node["kind"], kinds, node.get("nodeId"))
            for field in SPEC_EXECUTION_NODE_FIELDS:
                self.assertIn(field, node, node.get("nodeId"))
        for edge in example["edges"]:
            for field in SPEC_EXECUTION_EDGE_FIELDS:
                self.assertIn(field, edge, edge.get("edgeId"))
        for run in example["runs"]:
            for field in SPEC_RUN_FIELDS:
                self.assertIn(field, run, run.get("runId"))

    def test_the_example_is_marked_as_example_data(self):
        """A fixture must never look like a fact about a target project."""
        example = derive.execution_graph_example()
        self.assertIn("示例", example.get("$comment", ""))
        for node in example["nodes"]:
            self.assertEqual(node.get("verification"), "Hypothetical")
            self.assertEqual(node.get("evidenceIds"), [])
        self.assertEqual(example.get("executionMode"), "simulation")

    def test_the_new_fields_are_optional(self):
        """A new field must not be required: old models keep working."""
        for name in ("node", "edge", "workflow", "scenario", "evidence", "view"):
            schema = _schema(name)
            required = set(schema.get("required") or [])
            for field in ("qualifiedName", "sourceLocation", "version", "caseType",
                          "reality", "executionMode", "stateSchemaVersion",
                          "checkpointPolicy", "approvalPolicies", "executionCapabilities",
                          "runSecurityPolicy", "executionGraph", "iterationLimit",
                          "toolContractRef", "authorityScope", "policyRef"):
                self.assertNotIn(field, required, f"{name}.{field}")


class AliasTests(unittest.TestCase):
    """The specification's spellings must be accepted and normalised."""

    def test_the_specification_spelling_validates(self):
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [{"id": "a", "type": "Service", "label": "A", "verification": "Designed"}],
            "edges": [],
            "workflows": [{"workflowId": "w", "name": "W", "version": "1",
                            "nodes": [{"nodeId": "n1", "nodeType": "Task", "name": "N"},
                                      {"nodeId": "n2", "nodeType": "Task", "name": "M"}],
                            "edges": [{"edgeId": "e1", "sourceNodeId": "n1", "targetNodeId": "n2",
                                       "edgeType": "ControlFlow"}]}],
            "cases": [{"caseId": "c1", "scenarioId": "s", "category": "normal",
                       "title": "C"}],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
        }
        result = model_lib.validate(model, strict=False)
        self.assertEqual(result["status"], "ok", result)
        canonical = model_lib.normalized_model(model)
        self.assertEqual(canonical["nodes"][0]["kind"], "Service")
        self.assertEqual(canonical["nodes"][0]["name"], "A")
        self.assertEqual(canonical["workflows"][0]["id"], "w")
        self.assertEqual(canonical["workflows"][0]["nodes"][0]["id"], "n1")
        self.assertEqual(canonical["workflows"][0]["nodes"][0]["kind"], "task")
        self.assertEqual(canonical["workflows"][0]["edges"][0]["id"], "e1")
        self.assertEqual(canonical["workflows"][0]["edges"][0]["kind"], "control-flow")
        self.assertEqual(canonical["workflows"][0]["edges"][0]["sourceId"], "n1")

    def test_the_case_id_alias_is_normalised(self):
        """§13 spells the case id "caseId"; the canonical spelling is "id"."""
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
            "cases": [{"caseId": "c1", "scenarioId": "s", "category": "normal",
                       "title": "C"}],
        }
        canonical = model_lib.normalized_model(model)
        self.assertEqual(canonical["cases"][0]["id"], "c1")
        result = model_lib.validate(model, strict=False)
        self.assertEqual(result["status"], "ok", result)

    def test_the_derived_case_library_can_be_fed_back(self):
        """The MCP must accept the model it produces itself."""
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
            "cases": [{"id": "c1", "scenarioId": "s", "category": "normal",
                       "title": "C", "caseType": "Normal", "reality": "DesignedModel"}],
        }
        library = derive.case_library(model_lib.normalized_model(model))
        derived = library["cases"][0]
        self.assertEqual(derived["caseId"], "c1")
        self.assertNotIn("id", derived)

    def test_a_bidirectional_workflow_edge_keeps_its_direction(self):
        """§17 direction "both" must not be rewritten to "source-to-target"."""
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "workflows": [{"id": "w", "name": "W", "version": "1",
                            "nodes": [{"id": "n1", "kind": "task", "name": "N"},
                                      {"id": "n2", "kind": "task", "name": "M"}],
                            "edges": [{"id": "e1", "sourceId": "n1", "targetId": "n2",
                                       "kind": "control-flow", "direction": "both"}]}],
        }
        canonical = model_lib.normalized_model(model)
        edge = canonical["workflows"][0]["edges"][0]
        self.assertIs(edge["directed"], False)
        ir = derive.workflow_ir(canonical)["workflows"][0]
        self.assertEqual(ir["edges"][0]["direction"], "both")

    def test_an_unregistered_case_type_is_rejected(self):
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
            "cases": [{"id": "c1", "scenarioId": "s", "category": "normal",
                       "title": "C", "caseType": "Nope"}],
        }
        result = model_lib.validate(model, strict=False)
        codes = {item["id"] for item in result["errors"]}
        self.assertIn("GEMARC_CASE_006", codes)

    def test_an_unregistered_reality_is_rejected(self):
        model = {
            "meta": {"system": "Alias", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
            "cases": [{"id": "c1", "scenarioId": "s", "category": "normal",
                       "title": "C", "reality": "Probably"}],
        }
        result = model_lib.validate(model, strict=False)
        codes = {item["id"] for item in result["errors"]}
        self.assertIn("GEMARC_CASE_007", codes)


class DerivedFileTests(unittest.TestCase):
    """The v6.0 deliverables must be derived from the model, and agree with it."""

    def _model(self):
        return {
            "meta": {"system": "Spec", "evidenceState": "Designed"},
            "nodes": [
                {"id": "svc", "kind": "Class", "name": "Service", "verification": "Designed",
                 "evidenceIds": ["ev"], "spaceRef": {"buildingId": "b1", "floorId": "f1",
                                                     "roomId": "r1"}},
            ],
            "edges": [],
            "evidence": [{"id": "ev", "kind": "code", "level": "Designed",
                          "source": {"locator": "a.py:3", "path": "a.py", "startLine": 3}}],
            "space": {"buildings": [{"id": "b1", "name": "B", "semanticSource": "docs"}],
                      "floors": [{"id": "f1", "buildingId": "b1", "name": "F",
                                  "semanticSource": "docs"}],
                      "rooms": [{"id": "r1", "floorId": "f1", "name": "R",
                                 "semanticSource": "docs"}]},
            "views": [{"id": "v1", "title": "V", "diagramType": "uml-class",
                       "nodeIds": ["svc"], "concerns": ["c"]}],
            "scenarios": [{"id": "s", "name": "S", "events": [], "rules": [],
                           "evidenceClass": "hypothetical"}],
            "cases": [{"id": "c1", "scenarioId": "s", "category": "abnormal",
                       "caseType": "Timeout", "reality": "DesignedModel", "title": "C",
                       "disabledNodeIds": ["svc"]}],
            "workflows": [
                {"id": "w1", "name": "W", "version": "1.0.0",
                 "nodes": [{"id": "n1", "kind": "task", "name": "Do"}],
                 "edges": []},
            ],
        }

    def test_render_returns_the_v5_deliverables(self):
        out = stateless.render(self._model())
        self.assertEqual(out["status"], "ok", out)
        names = [item["path"].rsplit("/", 1)[-1] for item in out["files"]]
        for wanted in SPEC_DELIVERABLE_FILES:
            if wanted in ("workflow-schema.json", "execution-graph.schema.json",
                          "execution-graph.example.json"):
                continue  # fixed parts: they travel inside the kit
            self.assertIn(wanted, names)

    def test_the_kit_carries_the_fixed_contracts(self):
        paths = [item["path"] for item in stateless.kit_listing("static")]
        self.assertIn("data/workflow-schema.json", paths)
        self.assertIn("data/execution-graph.schema.json", paths)
        self.assertIn("data/execution-graph.example.json", paths)

    def test_the_evidence_index_agrees_with_the_model(self):
        index = derive.evidence_index(self._model())
        self.assertEqual(index["count"], 1)
        self.assertEqual(index["items"][0]["id"], "ev")
        self.assertEqual(index["items"][0]["citedBy"], ["svc"])
        self.assertEqual(index["groupedByLevel"], {"Designed": ["ev"]})

    def test_the_evidence_index_counts_workflow_and_case_citations(self):
        """Evidence used by a workflow or a case is not "uncited"."""
        model = self._model()
        model["evidence"].append({"id": "ev2", "kind": "code", "level": "Designed",
                                  "source": {"locator": "w.py:1"}})
        model["workflows"][0]["nodes"][0]["evidenceIds"] = ["ev2"]
        model["cases"][0]["evidenceIds"] = ["ev2"]
        index = derive.evidence_index(model)
        entry = next(item for item in index["items"] if item["id"] == "ev2")
        self.assertEqual(entry["citedBy"], ["c1", "n1"])
        self.assertNotIn("ev2", index["uncited"])

    def test_the_diagram_catalogue_reports_what_it_cannot_show(self):
        catalogue = derive.diagrams(self._model())
        entry = catalogue["catalog"][0]
        self.assertEqual(entry["diagramType"], "uml-class")
        # The model has no attributes, methods or multiplicity: they must be
        # reported as missing instead of being drawn out of thin air.
        self.assertIn("attributes", entry["missing"])
        self.assertIn("methods", entry["missing"])
        self.assertIn("attributes", entry["fidelity"])

    def test_the_diagram_catalogue_selects_the_edges_the_runtime_selects(self):
        """The catalogue must predict the site: same rules, same selection."""
        model = self._model()
        model["nodes"].append({"id": "other", "kind": "Class", "name": "Other",
                                "verification": "Designed"})
        model["edges"].append({"id": "e1", "sourceId": "svc", "targetId": "other",
                               "type": "inherits", "directed": True, "verification": "Designed",
                               "evidenceIds": []})
        catalogue = derive.diagrams(model)
        entry = catalogue["catalog"][0]
        # "other" is not in the view, so the edge leaves the view: the runtime
        # reports it and leaves it out.
        self.assertEqual(entry["edgeIds"], [])
        self.assertEqual(entry["nodeIds"], ["svc"])

    def test_the_spatial_layout_places_the_node(self):
        layout = derive.spatial_layout(self._model())
        self.assertTrue(layout["present"])
        self.assertEqual(layout["placements"], [{"nodeId": "svc", "buildingId": "b1",
                                                "floorId": "f1", "roomId": "r1"}])
        self.assertEqual(layout["missing"], [])

    def test_a_model_without_space_says_so(self):
        model = self._model()
        model["space"] = None
        layout = derive.spatial_layout(model)
        self.assertFalse(layout["present"])
        self.assertEqual(layout["missing"], ["building", "floor", "room"])

    def test_the_spatial_layout_lists_the_unplaced_nodes(self):
        model = self._model()
        model["nodes"].append({"id": "loose", "kind": "Class", "name": "Loose",
                                "verification": "Designed"})
        layout = derive.spatial_layout(model)
        self.assertEqual(layout["unplacedNodeIds"], ["loose"])

    def test_the_workflow_ir_uses_the_specification_names(self):
        ir = derive.workflow_ir(self._model())
        workflow = ir["workflows"][0]
        self.assertEqual(workflow["workflowId"], "w1")
        self.assertEqual(workflow["nodes"][0]["nodeType"], "Task")
        self.assertEqual(workflow["nodes"][0]["executable"], False)  # no implementationRef
        self.assertTrue(workflow["missing"])  # no entryPoints/dataSchemas
        self.assertFalse(workflow["gwfIrValid"])

    def test_the_entry_point_falls_back_to_the_start_node(self):
        model = self._model()
        model["workflows"][0]["nodes"] = [{"id": "n0", "kind": "start", "name": "Start"},
                                          {"id": "n1", "kind": "task", "name": "Do"}]
        workflow = derive.workflow_ir(model)["workflows"][0]
        self.assertEqual(workflow["entryPoints"], ["n0"])
        self.assertNotIn("entryPoints", workflow["missing"])

    def test_a_workflow_without_a_start_node_reports_the_missing_entry_point(self):
        model = self._model()
        workflow = derive.workflow_ir(model)["workflows"][0]
        self.assertEqual(workflow["entryPoints"], [])
        self.assertIn("entryPoints", workflow["missing"])

    def test_the_legacy_subprocess_kind_maps_to_subflow(self):
        model = self._model()
        model["workflows"][0]["nodes"] = [{"id": "n1", "kind": "subprocess", "name": "Sub"}]
        workflow = derive.workflow_ir(model)["workflows"][0]
        self.assertEqual(workflow["nodes"][0]["nodeType"], "Subflow")
        self.assertFalse(workflow["issues"])

    def test_the_design_only_nodes_are_marked(self):
        model = self._model()
        model["workflows"][0]["nodes"] = [{"id": "n1", "kind": "agent-loop", "name": "Agent"},
                                          {"id": "n2", "kind": "deterministic-program",
                                           "name": "Program"}]
        workflow = derive.workflow_ir(model)["workflows"][0]
        by_id = {node["nodeId"]: node for node in workflow["nodes"]}
        self.assertTrue(by_id["n1"]["designOnly"])   # no real executor in this delivery
        self.assertFalse(by_id["n2"]["designOnly"])  # a pure program has one
        self.assertEqual(workflow["designOnlyNodeIds"], ["n1"])

    def test_the_case_library_carries_the_case_contract(self):
        library = derive.case_library(self._model())
        case = library["cases"][0]
        self.assertEqual(case["caseId"], "c1")
        self.assertEqual(case["caseType"], "Timeout")
        self.assertEqual(case["reality"], "DesignedModel")
        self.assertEqual(case["category"], "abnormal")
        self.assertIn("Timeout", [item["id"] for item in library["caseTypes"]])
        self.assertEqual(library["coverage"]["byScenario"], {"s": 1})

    def test_the_case_library_publishes_the_reality_values(self):
        library = derive.case_library(self._model())
        self.assertEqual(library["realityValues"], SPEC_CASE_REALITY)

    def test_the_case_library_lists_the_cases_that_need_an_intervention(self):
        """A case whose category demands an intervention must declare one."""
        model = self._model()
        model["cases"].append({"id": "c3", "scenarioId": "s", "category": "abnormal",
                                "title": "C3", "caseType": "Timeout",
                                "reality": "DesignedModel", "disabledNodeIds": []})
        model["cases"].append({"id": "c2", "scenarioId": "s", "category": "normal",
                                "title": "C2", "caseType": "Normal",
                                "reality": "Hypothetical", "disabledNodeIds": []})
        library = derive.case_library(model)
        # c3 is "abnormal" without an intervention: it must be listed. c1 is
        # "abnormal" with one, c2 is "normal" and needs none: neither is.
        self.assertIn("c3", library["coverage"]["missingInterventions"])
        self.assertNotIn("c1", library["coverage"]["missingInterventions"])
        self.assertNotIn("c2", library["coverage"]["missingInterventions"])

    def test_a_case_with_an_intervention_is_no_longer_missing_it(self):
        model = self._model()
        model["cases"][0]["disabledNodeIds"] = ["svc"]
        library = derive.case_library(model)
        self.assertNotIn("c1", library["coverage"]["missingInterventions"])

    def test_a_legacy_workflow_kind_is_reported_not_guessed(self):
        model = self._model()
        model["workflows"][0]["nodes"][0]["kind"] = "gateway"
        ir = derive.workflow_ir(model)
        node = ir["workflows"][0]["nodes"][0]
        self.assertIsNone(node["nodeType"])
        self.assertEqual(node["legacyKind"], "gateway")
        self.assertTrue(ir["workflows"][0]["issues"])

    def test_a_view_without_a_diagram_type_is_not_called_unregistered(self):
        model = self._model()
        model["views"].append({"id": "v2", "title": "No type", "concerns": ["c"]})
        catalogue = derive.diagrams(model)
        self.assertIn("v2", catalogue["undeclared"])
        self.assertNotIn("v2", catalogue["unregistered"])

    def test_an_unregistered_diagram_type_is_reported(self):
        model = self._model()
        model["views"].append({"id": "v3", "title": "Unknown", "diagramType": "nope",
                                "concerns": ["c"]})
        catalogue = derive.diagrams(model)
        self.assertIn("v3", catalogue["unregistered"])


class ExecutionGateTests(unittest.TestCase):
    """The v6.0 6D quality gates: an execution graph that trips one must not pass."""

    def _workflow(self, **overrides):
        workflow = {
            "id": "w", "name": "W", "version": "1",
            "executionGraph": {"executionGraphId": "g"},
            "stateSchemaVersion": "state-v1",
            "nodes": [{"id": "n1", "kind": "agent-loop", "name": "Agent",
                       "iterationLimit": 3, "authorityScope": "read",
                       "implementationRef": "x"}],
            "edges": [],
        }
        workflow.update(overrides)
        return workflow

    def _codes(self, workflow):
        result = model_lib.validate({
            "meta": {"system": "Gate", "evidenceState": "Designed"},
            "nodes": [], "edges": [],
            "workflows": [workflow],
        }, strict=False)
        return {item["id"] for item in result["errors"]}

    def test_a_workflow_that_is_not_an_execution_graph_is_not_gated(self):
        """A design canvas must keep working: the 6D rules are an increment."""
        workflow = {"id": "w", "name": "W", "version": "1",
                    "nodes": [{"id": "n1", "kind": "task", "name": "Do"},
                              {"id": "n2", "kind": "decision", "name": "Split"}],
                    "edges": [{"id": "e1", "sourceId": "n1", "targetId": "n2",
                               "kind": "control-flow"}]}
        self.assertEqual(self._codes(workflow), set())

    def test_a_6d_node_kind_opts_the_workflow_into_the_gates(self):
        """Using a 6D node type means the workflow claims to be executable."""
        workflow = {"id": "w", "name": "W", "version": "1",
                    "nodes": [{"id": "n1", "kind": "agent-loop", "name": "Agent"}],
                    "edges": []}
        self.assertIn("GEMARC_EXEC_001", self._codes(workflow))

    def test_an_unbounded_agent_loop_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"][0].pop("iterationLimit")
        self.assertIn("GEMARC_EXEC_001", self._codes(workflow))

    def test_an_approval_without_a_policy_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"] = [{"id": "n1", "kind": "human-approval", "name": "Approve",
                              "approvalPolicyRef": "p"}]
        self.assertNotIn("GEMARC_EXEC_002", self._codes(workflow))
        workflow["nodes"][0].pop("approvalPolicyRef")
        self.assertIn("GEMARC_EXEC_002", self._codes(workflow))

    def test_state_without_a_schema_version_is_rejected(self):
        workflow = self._workflow()
        workflow.pop("stateSchemaVersion")
        workflow["nodes"][0]["inputStateSchema"] = "state-v1"
        self.assertIn("GEMARC_EXEC_003", self._codes(workflow))

    def test_a_policy_edge_without_a_route_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"].append({"id": "n2", "kind": "task", "name": "T"})
        workflow["edges"] = [{"id": "e1", "sourceId": "n1", "targetId": "n2",
                              "kind": "control-flow", "policyRef": "p"}]
        self.assertIn("GEMARC_EXEC_004", self._codes(workflow))
        workflow["edges"][0]["routingExpression"] = "state.ok"
        self.assertNotIn("GEMARC_EXEC_004", self._codes(workflow))

    def test_a_side_effect_without_idempotency_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"][0]["sideEffects"] = ["charge"]
        self.assertIn("GEMARC_EXEC_005", self._codes(workflow))
        workflow["nodes"][0]["idempotency"] = "key"
        self.assertNotIn("GEMARC_EXEC_005", self._codes(workflow))

    def test_a_checkpoint_without_state_integrity_is_rejected(self):
        workflow = self._workflow()
        workflow.pop("stateSchemaVersion")
        workflow["nodes"].append({"id": "n2", "kind": "checkpoint", "name": "CP"})
        self.assertIn("GEMARC_EXEC_006", self._codes(workflow))

    def test_a_fake_production_execution_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"][0]["executionMode"] = "real"
        workflow["nodes"][0].pop("authorityScope")
        self.assertIn("GEMARC_EXEC_007", self._codes(workflow))
        workflow["nodes"][0]["authorityScope"] = "read"
        self.assertNotIn("GEMARC_EXEC_007", self._codes(workflow))

    def test_an_untraceable_decision_is_rejected(self):
        workflow = self._workflow()
        workflow["nodes"] = [{"id": "n1", "kind": "policy-gate", "name": "Gate"}]
        self.assertIn("GEMARC_EXEC_008", self._codes(workflow))
        workflow["nodes"][0]["policyRef"] = "p"
        self.assertNotIn("GEMARC_EXEC_008", self._codes(workflow))

    def test_the_gate_verdict_is_in_the_derived_workflow_ir(self):
        workflow = self._workflow()
        workflow["nodes"][0].pop("iterationLimit")
        ir = derive.workflow_ir({"workflows": [workflow]})
        self.assertEqual(ir["workflows"][0]["gates"][0]["gate"], "unbounded_agent_loop")
        self.assertEqual(ir["gates"]["rejectedWorkflowIds"], ["w"])


@unittest.skipUnless(NODE, "node is not available")
class RuntimeParityTests(unittest.TestCase):
    """The Python side and the browser runtime must normalise the same way.

    The two are separate implementations of one contract. Without this test
    the promise "the same rules on both sides" is an assertion in a commit
    message, not a fact.
    """

    def _run(self, corpus):
        with tempfile.TemporaryDirectory(prefix="gemarc-parity-") as tmp:
            path = Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus), encoding="utf-8")
            result = subprocess.run([NODE, str(PARITY_HARNESS), str(path)],
                                    capture_output=True, text=True, timeout=120,
                                    encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise AssertionError(f"harness failed: {(result.stderr or '').strip()[:2000]}")
            return json.loads(result.stdout)

    ALIAS_CORPUS = [
        {"nodes": [{"id": "n1", "kind": "Service", "name": "S"}], "edges": [], "workflows": []},
        {"nodes": [{"id": "n1", "type": "Service", "label": "S"}], "edges": [], "workflows": []},
        {"nodes": [{"id": "n1", "kind": "", "type": "Service", "label": "S"}],
         "edges": [], "workflows": []},
        {"nodes": [{"id": "n1", "kind": "Service", "name": "", "label": "S"}],
         "edges": [], "workflows": []},
        {"nodes": [], "edges": [{"id": "e1", "source": "a", "target": "b", "kind": "sends"}],
         "workflows": []},
        {"nodes": [], "edges": [{"id": "e1", "sourceId": "a", "targetId": "b", "type": "sends",
                                 "direction": "undirected"}], "workflows": []},
        {"nodes": [], "edges": [{"id": "e1", "sourceId": "a", "targetId": "b", "type": "sends",
                                 "directed": None}], "workflows": []},
        {"nodes": [], "edges": [{"id": "e1", "sourceId": "a", "targetId": "b", "type": "sends",
                                 "directed": False}], "workflows": []},
        {"nodes": [], "edges": [], "workflows": [
            {"workflowId": "w1", "name": "W", "version": "1",
             "nodes": [{"nodeId": "n1", "nodeType": "Task", "name": "N"}], "edges": []}]},
        {"nodes": [], "edges": [], "workflows": [
            {"id": "w1", "name": "W", "version": "1",
             "nodes": [{"id": "n1", "kind": "task", "name": "N"},
                       {"id": "n2", "kind": "task", "name": "M"}],
             "edges": [{"id": "e1", "sourceNodeId": "n1", "targetNodeId": "n2",
                        "edgeType": "ControlFlow"}]}]},
        {"nodes": [], "edges": [], "workflows": [
            {"id": "w1", "name": "W", "version": "1",
             "nodes": [{"id": "n1", "kind": "", "nodeType": "Task"}],
             "edges": [{"id": "e1", "kind": "", "condition": "", "guard": "x>0"}]}]},
        {"nodes": [], "edges": [], "workflows": [
            {"id": "w1", "name": "W", "version": "1",
             "nodes": [{"id": "n1", "kind": "task"}],
             "edges": [{"id": "e1", "sourceId": "n1", "targetId": "n2",
                        "kind": "control-flow", "direction": "both"}]}]},
    ]

    def _effective(self, value):
        if value is None or value == "":
            return None
        return value

    def _python_side(self, raw):
        canonical = model_lib.normalized_model(raw)
        workflows = []
        for workflow in raw.get("workflows") or []:
            normalized = model_lib.normalized_model({"workflows": [workflow]})["workflows"][0]
            workflows.append({
                "id": self._effective(normalized.get("id")),
                "nodes": [{"id": self._effective(node.get("id")),
                           "kind": self._effective(node.get("kind")),
                           "name": self._effective(node.get("name"))}
                          for node in normalized.get("nodes") or []],
                "edges": [{"id": self._effective(edge.get("id")),
                           "kind": self._effective(edge.get("kind")),
                           "sourceId": self._effective(edge.get("sourceId")),
                           "targetId": self._effective(edge.get("targetId")),
                           "condition": self._effective(edge.get("condition")),
                           "directed": self._effective(edge.get("directed"))}
                          for edge in normalized.get("edges") or []],
            })
        return {
            "nodes": [{"id": self._effective(node.get("id")),
                       "kind": self._effective(node.get("kind")),
                       "name": self._effective(node.get("name"))}
                      for node in canonical.get("nodes") or []],
            "edges": [{"id": self._effective(edge.get("id")),
                       "type": self._effective(edge.get("type")),
                       "directed": self._effective(edge.get("directed")),
                       "condition": self._effective(edge.get("condition")),
                       "sourceId": self._effective(edge.get("sourceId")),
                       "targetId": self._effective(edge.get("targetId"))}
                      for edge in canonical.get("edges") or []],
            "workflows": workflows,
        }

    def test_the_two_normalisers_agree(self):
        actual = self._run({"mode": "aliases", "models": self.ALIAS_CORPUS})
        for index, raw in enumerate(self.ALIAS_CORPUS):
            expected = self._python_side(raw)
            self.assertEqual(actual[index], expected, f"case {index}: {json.dumps(raw)}")

    FIDELITY_CASES = [
        {"token": "attributes", "nodes": [{"id": "n1", "attributes": [{"name": "a"}]}]},
        {"token": "attributes", "nodes": [{"id": "n1", "attributes": []}]},
        {"token": "primaryKey", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "id", "primaryKey": True}]}]},
        {"token": "primaryKey", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "id", "primaryKey": False}]}]},
        {"token": "column", "nodes": [{"id": "n1", "attributes": [{"name": "id"}]}]},
        {"token": "column", "nodes": [{"id": "n1", "attributes": [{"type": "uuid"}]}]},
        {"token": "dataType", "nodes": [{"id": "n1", "attributes": [{"name": "id", "type": "uuid"}]}]},
        {"token": "multiplicity", "edges": [{"id": "e1", "properties": {"multiplicity": "1..n"}}]},
        {"token": "multiplicity", "edges": [{"id": "e1"}]},
        {"token": "guard", "edges": [{"id": "e1", "condition": "ok"}]},
        {"token": "trustBoundary", "edges": [{"id": "e1", "trustBoundary": False}]},
        {"token": "trustBoundary", "edges": [{"id": "e1"}]},
        {"token": "building", "nodes": [{"id": "n1", "spaceRef": {"buildingId": "b1"}}]},
        {"token": "building", "nodes": [{"id": "n1"}]},
        {"token": "nesting", "nodes": [{"id": "n1", "parentId": "n0"}], "nodeIds": ["n1", "n0"]},
        {"token": "nesting", "nodes": [{"id": "n1", "parentId": "n0"}], "nodeIds": ["n1"]},
        {"token": "counterfactual", "nodes": [{"id": "n1", "properties": {"counterfactual": True}}]},
        {"token": "eventPayload", "edges": [{"id": "e1", "properties": {"payload": {"a": 1}}}]},
        {"token": "ordering", "edges": [{"id": "e1", "properties": {"ordering": "fifo"}}]},
        {"token": "deliveryGuarantee", "edges": [{"id": "e1",
                                                 "properties": {"deliveryGuarantee": "at-least-once"}}]},
        # These three have a predicate of their own. A predicate whose
        # signature did not match the call site raised a TypeError that the
        # call site swallowed, so Python answered "not satisfied" for every
        # node while the runtime drew the element: the corpus has to cover
        # them, or the divergence stays invisible.
        {"token": "visibility", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "a", "visibility": "public"}]}]},
        {"token": "visibility", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "a", "visibility": ""}]}]},
        {"token": "foreignKey", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "a", "foreignKey": True}]}]},
        {"token": "foreignKey", "nodes": [{"id": "n1",
                                           "attributes": [{"name": "a", "foreignKey": False}]}]},
        {"token": "methods", "nodes": [{"id": "n1", "methods": [{"name": "run"}]}]},
    ]

    def test_the_two_fidelity_checks_agree(self):
        actual = self._run({"mode": "fidelity", "cases": self.FIDELITY_CASES})
        for index, case in enumerate(self.FIDELITY_CASES):
            context = {"nodes": case.get("nodes", []), "edges": case.get("edges", []),
                       "nodeIds": case.get("nodeIds", []), "space": {}, "scenarios": []}
            expected = derive._check_fidelity(case["token"], context)
            self.assertEqual(actual[index]["satisfied"], expected,
                             f"case {index}: {case['token']}")


class DocumentBindingTests(unittest.TestCase):
    """The capability document must not drift from what the code actually does.

    A document that quotes a machine-made number (the test count, the size of a
    vocabulary) is stale the moment the code moves. These assertions bind the
    two together, so a reviewer who trusts the document is not misled.
    """

    DOC = REPO_ROOT / "docs" / "08_附录" / "03_v6.0规格能力对照.md"

    def _document(self) -> str:
        return self.DOC.read_text(encoding="utf-8")

    def test_the_document_cites_the_real_coverage_test_count(self):
        module = sys.modules[__name__]
        total = sum(
            len([name for name in dir(obj) if name.startswith("test_")])
            for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
        )
        self.assertRegex(self._document(), rf"(?<!\d){total} 项覆盖测试",
                         "the capability document must cite the real number of coverage tests")

    def test_the_document_cites_the_real_vocabulary_sizes(self):
        text = self._document()
        diagram_types = len(_vocab("diagram-types")["types"])
        case_types = len(_vocab("case-types")["types"])
        node_types = len(_vocab("workflow-kinds")["gwfIr"]["nodeTypes"])
        edge_types = len(_vocab("workflow-kinds")["gwfIr"]["edgeTypes"])
        self.assertRegex(text, rf"(?<!\d){diagram_types} 种图示类型")
        self.assertRegex(text, rf"(?<!\d){case_types} 种分析类型")
        self.assertRegex(text, rf"(?<!\d){node_types} 种工作流节点类型")
        self.assertRegex(text, rf"(?<!\d){edge_types} 种工作流边类型")


if __name__ == "__main__":
    unittest.main()
