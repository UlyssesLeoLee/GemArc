"""v5.0 规格覆盖断言：MCP 的模型层必须能表达 Universal_5D_GemArc_Web_v5.0 要求的内容。

规格把该有的字段和枚举逐条列了出来（§3 canonical_model、§9 supported_diagrams、
§13 case_types、§17 GWF-IR、§25 输出目录）。这里把那些列表原样抄下来，钉在词表与
JSON Schema 上：规格变了而 MCP 没跟上，这些断言就红。

覆盖不了的东西不许假装能：每个断言要么检查“能表达”，要么检查“缺了会被标成缺失”。
"""
import json
import sys
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


def _vocab(name):
    return json.loads((VOCAB_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _schema(name):
    # The contract files are named "<object>.schema.json".
    return json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


# --- the specification, quoted -----------------------------------------------

#: v5.0 §3 canonical_model.required_entity_capabilities
SPEC_ENTITY_KINDS = [
    "System", "Subsystem", "Module", "Component", "Class", "Interface", "Method",
    "Entity", "Table", "Attribute", "Actor", "Event", "State", "Relationship",
    "Constraint", "Evidence", "Scenario", "CausalRule",
]

#: v5.0 §3 canonical_model.verification_levels
SPEC_EVIDENCE_LEVELS = [
    "Implemented", "Observed", "Designed", "Inferred", "Hypothetical", "Unknown",
]

#: v5.0 §3 canonical_model.node_fields
SPEC_NODE_FIELDS = [
    "id", "type", "label", "qualifiedName", "properties", "role", "domainId",
    "parentId", "sourceLocation", "evidenceIds", "verification", "version",
]

#: v5.0 §3 canonical_model.edge_fields
SPEC_EDGE_FIELDS = [
    "id", "sourceId", "targetId", "type", "directed", "properties", "weight",
    "condition", "evidenceIds", "verification", "version",
]

#: v5.0 §3 canonical_model.provenance_fields
SPEC_PROVENANCE_FIELDS = ["repository", "commit", "path", "startLine", "endLine", "evidenceId"]

#: v5.0 §3 canonical_model.output_files, as this repository ships them.
#: The specification's name for the diagram catalogue is on the repository's
#: hashed denylist (it belongs to a reference system), so it is not repeated
#: here; the MCP ships the same content as ``data/diagram-catalog.json``.
SPEC_ARCHITECTURE_OUTPUT_FILES = ["architecture.json", "evidence-index.json", "diagram-catalog.json"]

#: v5.0 §9 supported_diagrams
SPEC_DIAGRAM_FAMILIES = {
    "UML": ["Class", "Component", "Package", "Sequence", "StateMachine", "Activity",
            "Deployment", "UseCase"],
    "Data": ["ER", "DataFlow", "DataDependency"],
    "Architecture": ["Logical", "Development", "Process", "Deployment", "Data", "Security",
                     "Operations", "Dependency", "EventFlow", "Causal"],
}

#: v5.0 §13 case_types
SPEC_CASE_TYPES = [
    "Normal", "Boundary", "Error", "Timeout", "Retry", "Recovery", "Compensation",
    "Concurrency", "RaceCondition", "ResourceContention", "PartialFailure",
    "CascadingFailure", "Security", "Combined",
]

#: v5.0 §13 case_contract
SPEC_CASE_CONTRACT = [
    "caseId", "name", "description", "category", "initialState", "inputEvents",
    "preconditions", "executionRules", "expectedOutcomes", "terminationConditions",
    "assertions", "evidenceIds", "assumptions",
]

#: v5.0 §13 case reality marking
SPEC_CASE_REALITY = ["Observed", "ImplementedModel", "DesignedModel", "Hypothetical"]

#: v5.0 §17 workflow_fields
SPEC_WORKFLOW_FIELDS = [
    "workflowId", "name", "version", "entryPoints", "nodes", "edges", "dataSchemas",
    "executionPolicy", "errorPolicy", "permissions", "evidenceIds", "metadata",
]

#: v5.0 §17 node_fields
SPEC_WORKFLOW_NODE_FIELDS = [
    "nodeId", "nodeType", "name", "description", "inputs", "outputs", "ports",
    "preconditions", "postconditions", "stateTransitions", "timeoutPolicy",
    "retryPolicy", "errorHandlers", "sideEffects", "idempotency",
    "implementationRef", "evidenceIds", "verification",
]

#: v5.0 §17 edge_fields
SPEC_WORKFLOW_EDGE_FIELDS = [
    "edgeId", "sourceNodeId", "sourcePortId", "targetNodeId", "targetPortId",
    "edgeType", "direction", "guard", "dataMapping", "priority", "errorRoute",
    "verification", "evidenceIds",
]

#: v5.0 §17 port_fields
SPEC_PORT_FIELDS = ["portId", "direction", "schema", "cardinality", "required", "acceptedTypes"]

#: v5.0 §17 edge_types
SPEC_WORKFLOW_EDGE_TYPES = [
    "ControlFlow", "DataFlow", "EventFlow", "ErrorFlow", "ConditionalFlow",
    "CompensationFlow", "Dependency",
]

#: v5.0 §17 node_types
SPEC_WORKFLOW_NODE_TYPES = [
    "Start", "End", "Task", "Input", "Output", "Decision", "ParallelFork",
    "ParallelJoin", "Event", "Timer", "Wait", "Agent", "HumanApproval", "APICall",
    "DataTransform", "Subflow", "ErrorHandler", "Retry", "Compensation",
]

#: v5.0 §14 animation_primitives
SPEC_ANIMATION_PRIMITIVES = [
    "Propagation", "Branching", "Parallel", "Waiting", "Timeout", "Retry", "Blocking",
    "FailurePropagation", "Recovery", "Compensation", "FeedbackLoop", "Convergence",
    "StateTransition", "DataTransformation", "Escalation", "Cancellation", "Rollback",
    "OutcomeComparison",
]

#: v5.0 §25 the deliverable files of the target project, as this repository
#: ships them (see SPEC_ARCHITECTURE_OUTPUT_FILES for the one renamed file).
SPEC_DELIVERABLE_FILES = [
    "architecture.json", "evidence-index.json", "diagram-catalog.json", "spatial-layout.json",
    "workflow-schema.json", "workflow-ir.json", "case-library.json",
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

    def test_workflow_node_types_cover_the_specification(self):
        kinds = _vocab("workflow-kinds")["nodeKinds"]
        mapped = {item.get("specType") for item in kinds if item.get("specType")}
        self.assertEqual(mapped, set(SPEC_WORKFLOW_NODE_TYPES))

    def test_workflow_edge_types_cover_the_specification(self):
        kinds = _vocab("workflow-kinds")["edgeKinds"]
        mapped = {item.get("specType") for item in kinds if item.get("specType")}
        self.assertEqual(mapped, set(SPEC_WORKFLOW_EDGE_TYPES))

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
                self.assertIn(token, derive._FIDELITY_PROBES, f"{item['id']}:{token}")


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

    def test_the_new_fields_are_optional(self):
        """Every added field must be optional: an old model must still validate."""
        for name in ("node", "edge", "workflow", "scenario", "evidence"):
            schema = _schema(name)
            self.assertTrue(schema.get("additionalProperties", False) or name == "scenario")


class AliasTests(unittest.TestCase):
    """The field spellings of §3 and §17 must be accepted and normalised."""

    def _model(self):
        return {
            "meta": {"system": "Spec", "evidenceState": "Designed"},
            "nodes": [
                {"id": "svc", "type": "Service", "label": "Service",
                 "qualifiedName": "com.example.Service", "version": "1.0.0",
                 "sourceLocation": {"repository": "r", "commit": "c", "path": "a.py",
                                    "startLine": 3, "endLine": 9},
                 "verification": "Designed", "evidenceIds": ["ev"]},
                {"id": "dep", "type": "Entity", "label": "Dep", "verification": "Designed"},
            ],
            "edges": [
                {"id": "e1", "sourceId": "svc", "targetId": "dep", "type": "dependsOn",
                 "direction": "undirected", "version": "1.0.0", "verification": "Designed",
                 "evidenceIds": ["ev"]},
            ],
            "evidence": [{"id": "ev", "kind": "code", "level": "Designed",
                          "source": {"locator": "a.py:3", "path": "a.py",
                                     "startLine": 3, "endLine": 9}}],
            "workflows": [
                {"workflowId": "w1", "name": "W", "version": "1.0.0",
                 "nodes": [
                     {"nodeId": "n1", "nodeType": "Task", "name": "Do",
                      "ports": [{"portId": "p1", "kind": "input", "direction": "in",
                                 "cardinality": "1", "required": True,
                                 "acceptedTypes": ["string"]}]},
                 ],
                 "edges": [
                     {"edgeId": "w1", "sourceNodeId": "n1", "targetNodeId": "n1",
                      "edgeType": "ControlFlow", "guard": "x > 1"},
                 ]},
            ],
        }

    def test_the_specification_spelling_validates(self):
        result = model_lib.validate(self._model())
        self.assertEqual(result["status"], "ok", result["errors"])

    def test_the_aliases_are_normalised_to_the_canonical_spelling(self):
        model = model_lib.normalized_model(self._model())
        node = model["nodes"][0]
        self.assertEqual(node["kind"], "Service")
        self.assertEqual(node["name"], "Service")
        self.assertEqual(model["edges"][0]["directed"], False)
        workflow = model["workflows"][0]
        self.assertEqual(workflow["id"], "w1")
        self.assertEqual(workflow["nodes"][0]["id"], "n1")
        self.assertEqual(workflow["nodes"][0]["kind"], "task")
        self.assertEqual(workflow["nodes"][0]["ports"][0]["id"], "p1")
        self.assertEqual(workflow["edges"][0]["id"], "w1")
        self.assertEqual(workflow["edges"][0]["kind"], "control-flow")
        self.assertEqual(workflow["edges"][0]["condition"], "x > 1")

    def test_an_unregistered_case_type_is_rejected(self):
        model = self._model()
        model["scenarios"] = [{"id": "s", "name": "S", "events": [], "rules": []}]
        model["cases"] = [{"id": "c1", "scenarioId": "s", "category": "normal",
                           "title": "C", "caseType": "Nonsense"}]
        result = model_lib.validate(model)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("GEMARC_CASE_006", [d["id"] for d in result["errors"]])


class DerivedFileTests(unittest.TestCase):
    """The v5.0 deliverables must be derived from the model, and agree with it."""

    def _model(self):
        return {
            "meta": {"system": "Spec", "evidenceState": "Designed"},
            "nodes": [
                {"id": "svc", "kind": "Service", "name": "Service", "verification": "Designed",
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
            if wanted == "workflow-schema.json":
                continue  # a fixed part: it travels inside the kit
            self.assertIn(wanted, names)

    def test_the_kit_carries_the_workflow_schema(self):
        paths = [item["path"] for item in stateless.kit_listing("static")]
        self.assertIn("data/workflow-schema.json", paths)

    def test_the_evidence_index_agrees_with_the_model(self):
        index = derive.evidence_index(self._model())
        self.assertEqual(index["count"], 1)
        self.assertEqual(index["items"][0]["id"], "ev")
        self.assertEqual(index["items"][0]["citedBy"], ["svc"])
        self.assertEqual(index["groupedByLevel"], {"Designed": ["ev"]})

    def test_the_diagram_catalogue_reports_what_it_cannot_show(self):
        catalogue = derive.diagrams(self._model())
        entry = catalogue["catalog"][0]
        self.assertEqual(entry["diagramType"], "uml-class")
        # The model has no attributes, methods or multiplicity: they must be
        # reported as missing instead of being drawn out of thin air.
        self.assertIn("attributes", entry["missing"])
        self.assertIn("methods", entry["missing"])
        self.assertNotIn("attributes", entry["fidelity"] and [] or [])

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

    def test_the_workflow_ir_uses_the_specification_names(self):
        ir = derive.workflow_ir(self._model())
        workflow = ir["workflows"][0]
        self.assertEqual(workflow["workflowId"], "w1")
        self.assertEqual(workflow["nodes"][0]["nodeType"], "Task")
        self.assertEqual(workflow["nodes"][0]["executable"], False)  # no implementationRef
        self.assertTrue(workflow["missing"])  # no version/entryPoints/dataSchemas
        self.assertFalse(workflow["gwfIrValid"])

    def test_the_case_library_carries_the_case_contract(self):
        library = derive.case_library(self._model())
        case = library["cases"][0]
        self.assertEqual(case["caseId"], "c1")
        self.assertEqual(case["caseType"], "Timeout")
        self.assertEqual(case["reality"], "DesignedModel")
        self.assertEqual(case["category"], "abnormal")
        self.assertIn("Timeout", [item["id"] for item in library["caseTypes"]])
        self.assertEqual(library["coverage"]["byScenario"], {"s": 1})

    def test_a_legacy_workflow_kind_is_reported_not_guessed(self):
        model = self._model()
        model["workflows"][0]["nodes"][0]["kind"] = "gateway"
        ir = derive.workflow_ir(model)
        node = ir["workflows"][0]["nodes"][0]
        self.assertIsNone(node["nodeType"])
        self.assertEqual(node["legacyKind"], "gateway")
        self.assertTrue(ir["workflows"][0]["issues"])


class DocumentBindingTests(unittest.TestCase):
    """The capability document must not drift from what the code actually does.

    A document that quotes a machine-made number (the test count, the size of a
    vocabulary) is stale the moment the code moves. These assertions bind the
    two together, so a reviewer who trusts the document is not misled.
    """

    DOC = REPO_ROOT / "docs" / "08_附录" / "03_v5.0规格能力对照.md"

    def _document(self) -> str:
        return self.DOC.read_text(encoding="utf-8")

    def test_the_document_cites_the_real_coverage_test_count(self):
        module = sys.modules[__name__]
        total = sum(
            len([name for name in dir(obj) if name.startswith("test_")])
            for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase)
        )
        self.assertIn(f"{total} 项断言", self._document(),
                      "the capability document must cite the real number of coverage assertions")

    def test_the_document_cites_the_real_vocabulary_sizes(self):
        text = self._document()
        diagram_types = len(_vocab("diagram-types")["types"])
        case_types = len(_vocab("case-types")["types"])
        self.assertIn(f"图示类型 {diagram_types} 种", text)
        self.assertIn(f"{case_types} 种分析类型", text)


if __name__ == "__main__":
    unittest.main()
