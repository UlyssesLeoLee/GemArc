"""6D = WBS × 执行图: the work breakdown layer of the execution graph.

The execution graph says how a run is wired; the work breakdown says what work
that run performs, what each level delivers and when it counts as done. These
tests cover the three layers that have to agree:

- the vocabulary and the published contract (what a package may declare),
- the model layer (aliases, and the quality gates that reject a broken
  breakdown),
- the derivation and the site (what the reader actually sees).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp import model as gemarc_model
from gemarc_mcp import service
from gemarc_mcp import stateless
from gemarc_mcp.paths import Workspace
from gemarc_mcp.scaffold import derive

SCAFFOLD = Path(__file__).resolve().parent.parent / "src" / "gemarc_mcp" / "scaffold"
CONTRACT = SCAFFOLD / "contracts" / "execution-graph.schema.json"
EXAMPLE = SCAFFOLD / "contracts" / "execution-graph.example.json"
EXAMPLE_MODEL = Path(__file__).resolve().parent.parent / "examples" / "demo-site" / "data" / "architecture.json"
DUMP_TOOL = SCAFFOLD / "runtime" / "tools" / "dump-view-model.mjs"
NODE = shutil.which("node")

PACKAGE_KINDS = ["phase", "work-package", "task"]
NEW_GATES = [
    "wbs_orphan_node",
    "wbs_broken_hierarchy",
    "wbs_missing_deliverable",
    "wbs_unbound_package",
]
GATE_CODES = {
    "wbs_orphan_node": "GEMARC_EXEC_009",
    "wbs_broken_hierarchy": "GEMARC_EXEC_010",
    "wbs_missing_deliverable": "GEMARC_EXEC_011",
    "wbs_unbound_package": "GEMARC_EXEC_012",
}


def _vocab() -> dict:
    return json.loads((SCAFFOLD / "vocab" / "workflow-kinds.json").read_text(encoding="utf-8"))


def execution_workflow(packages, node_ids=("n-agent", "n-program", "n-approve"),
                       extra_nodes=()) -> dict:
    """A workflow that declares an execution graph with the given work packages.

    The workflow carries a start and an end node, because the site's own
    workflow validation requires them: a showcase model that produces
    diagnostics is not a model to build a site from.
    """
    nodes = [
        {"id": "n-start", "kind": "start", "name": "开始"},
        {"id": "n-agent", "kind": "agent-loop", "name": "收集", "iterationLimit": 3,
         "executionMode": "simulation", "authorityScope": "read-only"},
        {"id": "n-program", "kind": "deterministic-program", "name": "校验",
         "executorKind": "python", "executionMode": "simulation"},
        {"id": "n-approve", "kind": "human-approval", "name": "审批",
         "approvalPolicyRef": "approval.default", "executionMode": "simulation"},
        {"id": "n-end", "kind": "end", "name": "结束"},
    ]
    selected = [node for node in nodes
                if node["id"] in node_ids or node["id"] in ("n-start", "n-end")]
    edges = []
    for index in range(len(selected) - 1):
        edges.append({"id": f"e{index + 1}", "kind": "control-flow",
                      "sourceId": selected[index]["id"], "targetId": selected[index + 1]["id"]})
    return {
        "id": "wf-6d",
        "name": "交付流程",
        "version": "1.0",
        "nodes": selected + list(extra_nodes),
        "edges": edges,
        "stateSchemaVersion": "state-v1",
        "executionGraph": {"executionGraphId": "eg-1", "workPackages": packages},
    }


def rendered_fields(node) -> list[tuple]:
    """Every (label, value) pair the view model renders as a field.

    A field is a paragraph whose children are a bold label and a muted value.
    """
    found = []
    if isinstance(node, dict):
        children = node.get("children") or []
        if node.get("tag") == "p":
            label = next((child.get("text") for child in children
                          if isinstance(child, dict) and child.get("tag") == "b"), None)
            value = next((child.get("text") for child in children
                          if isinstance(child, dict) and child.get("tag") == "span"), None)
            if label is not None and value is not None:
                found.append((label, value))
        for value in node.values():
            if isinstance(value, (list, dict)):
                found.extend(rendered_fields(value))
    elif isinstance(node, list):
        for child in node:
            found.extend(rendered_fields(child))
    return found

def good_packages() -> list[dict]:
    return [
        {"packageId": "wp-root", "parentPackageId": None, "kind": "phase", "title": "交付阶段",
         "deliverable": "一次可审计的交付", "completionCriteria": "运行完成",
         "nodeIds": [], "dependsOn": []},
        {"packageId": "wp-collect", "parentPackageId": "wp-root", "kind": "work-package",
         "title": "收集与校验", "deliverable": "结构化发现",
         "completionCriteria": "findings 已产出并通过校验", "nodeIds": ["n-agent", "n-program"],
         "dependsOn": []},
        {"packageId": "wp-approve", "parentPackageId": "wp-root", "kind": "task", "title": "审批",
         "deliverable": "审批结论", "completionCriteria": "决定已记录", "nodeIds": ["n-approve"],
         "dependsOn": ["wp-collect"]},
    ]


class VocabularyTests(unittest.TestCase):
    """The vocabulary publishes what a work package may declare."""

    def test_the_three_wbs_levels_are_published(self):
        self.assertEqual(_vocab()["executionGraph"]["workPackageKinds"], PACKAGE_KINDS)

    def test_the_work_package_field_catalogue_is_published(self):
        fields = _vocab()["executionGraph"]["workPackageFields"]
        for name in ("packageId", "parentPackageId", "kind", "title", "deliverable",
                     "completionCriteria", "nodeIds", "dependsOn"):
            self.assertIn(name, fields)

    def test_the_wbs_contract_is_written_down(self):
        contract = _vocab()["executionGraph"]["wbsContract"]
        self.assertTrue(contract)
        joined = " ".join(contract)
        self.assertIn("WBS", joined)
        self.assertIn("deliverable", joined)
        self.assertIn("completionCriteria", joined)

    def test_the_wbs_gates_are_published_as_reject_on(self):
        reject_on = _vocab()["qualityGates"]["rejectOn"]
        for gate in NEW_GATES:
            self.assertIn(gate, reject_on)

    def test_the_contract_schema_declares_work_packages(self):
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertIn("workPackages", schema["properties"])
        definition = schema["$defs"]["workPackage"]
        self.assertEqual(definition["properties"]["kind"]["enum"], PACKAGE_KINDS)
        for name in ("packageId", "parentPackageId", "deliverable", "completionCriteria", "nodeIds"):
            self.assertIn(name, definition["properties"])

    def test_the_published_example_carries_a_work_breakdown(self):
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        packages = example["workPackages"]
        self.assertTrue(packages)
        roots = [item for item in packages if not item.get("parentPackageId")]
        self.assertEqual(len(roots), 1)
        for item in packages:
            self.assertTrue(item["deliverable"], item["packageId"])
            self.assertTrue(item["completionCriteria"], item["packageId"])

    def test_the_example_validates_against_the_contract(self):
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - the dependency is optional
            self.skipTest("jsonschema is not installed")
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        jsonschema.validate(example, schema)


class NormalisationTests(unittest.TestCase):
    """The model layer accepts both spellings of a work package."""

    def test_the_short_spellings_are_rewritten(self):
        workflow = execution_workflow([{
            "id": "wp-root", "parentId": None, "kind": "phase", "title": "阶段",
            "deliverable": "d", "completionCriteria": "c", "nodes": ["n-agent"],
        }])
        normalised = gemarc_model.normalized_model({"workflows": [workflow]})
        package = normalised["workflows"][0]["executionGraph"]["workPackages"][0]
        self.assertEqual(package["packageId"], "wp-root")
        self.assertIsNone(package["parentPackageId"])
        self.assertEqual(package["nodeIds"], ["n-agent"])

    def test_the_canonical_spellings_survive(self):
        workflow = execution_workflow(good_packages())
        normalised = gemarc_model.normalized_model({"workflows": [workflow]})
        package = normalised["workflows"][0]["executionGraph"]["workPackages"][1]
        self.assertEqual(package["packageId"], "wp-collect")
        self.assertEqual(package["parentPackageId"], "wp-root")


class DerivationTests(unittest.TestCase):
    """The derivation publishes the tree, the rollup and the coverage."""

    def setUp(self):
        self.entry = derive.workflow_ir({"workflows": [execution_workflow(good_packages())]})["workflows"][0]

    def test_the_packages_are_derived(self):
        rows = {row["packageId"]: row for row in self.entry["workPackages"]}
        self.assertEqual(set(rows), {"wp-root", "wp-collect", "wp-approve"})
        self.assertEqual(rows["wp-root"]["childPackageIds"], ["wp-approve", "wp-collect"])
        self.assertEqual(rows["wp-collect"]["nodeCount"], 2)
        self.assertEqual(rows["wp-approve"]["dependsOn"], ["wp-collect"])
        self.assertEqual(rows["wp-collect"]["deliverable"], "结构化发现")

    def test_the_summary_names_the_root_and_the_coverage(self):
        summary = self.entry["wbs"]
        self.assertTrue(summary["declared"])
        self.assertEqual(summary["rootPackageId"], "wp-root")
        self.assertEqual(summary["packageCount"], 3)
        self.assertEqual(summary["nodeCount"], 3)
        self.assertEqual(summary["assignedNodeCount"], 3)
        self.assertEqual(summary["coverageRatio"], 1.0)
        self.assertEqual(summary["kinds"], {"phase": 1, "work-package": 1, "task": 1})

    def test_a_workflow_without_a_breakdown_declares_none(self):
        """The layer is an increment: a model that does not use it pays nothing."""
        entry = derive.workflow_ir({"workflows": [execution_workflow([])]})["workflows"][0]
        self.assertNotIn("workPackages", entry)
        self.assertNotIn("wbs", entry)
        self.assertEqual(entry["gates"], [])

    def test_the_generated_ir_schema_declares_the_layer(self):
        schema = derive.workflow_schema()["jsonSchema"]
        self.assertIn("workPackages", schema["properties"])
        self.assertIn("workPackage", schema["$defs"])
        self.assertEqual(schema["$defs"]["workPackage"]["properties"]["kind"]["enum"], PACKAGE_KINDS)


class GateTests(unittest.TestCase):
    """The four WBS gates reject a broken breakdown and nothing else."""

    def _gates(self, packages, node_ids=("n-agent", "n-program", "n-approve")) -> dict:
        workflow = execution_workflow(packages, node_ids)
        hits = derive._execution_gates(workflow)
        return {hit["gate"]: hit for hit in hits}

    def test_a_sound_breakdown_passes(self):
        self.assertEqual(self._gates(good_packages()), {})

    def test_an_unbound_node_is_rejected(self):
        """A node that no work package accounts for is outside the breakdown."""
        workflow = execution_workflow(good_packages(), extra_nodes=[
            {"id": "n-extra", "kind": "deterministic-program", "name": "多余的执行节点",
             "executorKind": "python", "executionMode": "simulation"},
        ])
        hits = {hit["gate"]: hit for hit in derive._execution_gates(workflow)}
        self.assertIn("wbs_orphan_node", hits)
        self.assertEqual(hits["wbs_orphan_node"]["targets"], ["n-extra"])

    def test_a_package_pointing_at_a_removed_node_is_a_dangling_binding(self):
        gates = self._gates(good_packages(), node_ids=("n-agent", "n-program"))
        self.assertIn("wbs_broken_hierarchy", gates)

    def test_two_roots_break_the_hierarchy(self):
        packages = good_packages()
        packages.append({"packageId": "wp-other", "parentPackageId": None, "kind": "phase",
                         "title": "另一个阶段", "deliverable": "d", "completionCriteria": "c",
                         "nodeIds": []})
        self.assertIn("wbs_broken_hierarchy", self._gates(packages))

    def test_a_missing_parent_breaks_the_hierarchy(self):
        packages = good_packages()
        packages[1]["parentPackageId"] = "wp-ghost"
        self.assertIn("wbs_broken_hierarchy", self._gates(packages))

    def test_a_cycle_breaks_the_hierarchy(self):
        packages = good_packages()
        packages[0]["parentPackageId"] = "wp-collect"
        self.assertIn("wbs_broken_hierarchy", self._gates(packages))

    def test_a_package_that_points_at_a_missing_node_breaks_the_binding(self):
        packages = good_packages()
        packages[1]["nodeIds"] = ["n-agent", "n-ghost"]
        self.assertIn("wbs_broken_hierarchy", self._gates(packages))

    def test_a_package_without_a_deliverable_is_rejected(self):
        packages = good_packages()
        packages[1]["deliverable"] = ""
        gates = self._gates(packages)
        self.assertIn("wbs_missing_deliverable", gates)
        self.assertEqual(gates["wbs_missing_deliverable"]["targets"], ["wp-collect"])

    def test_a_package_without_a_done_criterion_is_rejected(self):
        packages = good_packages()
        packages[1]["completionCriteria"] = ""
        self.assertIn("wbs_missing_deliverable", self._gates(packages))

    def test_a_leaf_package_without_a_node_is_rejected(self):
        packages = good_packages()
        packages[2]["nodeIds"] = []
        gates = self._gates(packages)
        self.assertIn("wbs_unbound_package", gates)
        self.assertEqual(gates["wbs_unbound_package"]["targets"], ["wp-approve"])

    def test_a_level_that_only_aggregates_is_not_rejected(self):
        """A phase owns children, not nodes: that is what a level is for."""
        gates = self._gates(good_packages())
        self.assertNotIn("wbs_unbound_package", gates)

    def test_the_gate_codes_are_published(self):
        for gate, code in GATE_CODES.items():
            self.assertEqual(gemarc_model.EXECUTION_GATE_CODES.get(gate), code)

    def test_the_validation_reports_the_gate_codes(self):
        workflow = execution_workflow(good_packages(), extra_nodes=[
            {"id": "n-extra", "kind": "deterministic-program", "name": "多余的执行节点",
             "executorKind": "python", "executionMode": "simulation"},
        ])
        result = gemarc_model.validate({"workflows": [workflow]}, strict=False)
        codes = {issue["id"] for issue in result["errors"]}
        self.assertIn("GEMARC_EXEC_009", codes)


class SiteRenderingTests(unittest.TestCase):
    """The site shows the breakdown: levels, deliverable, done criterion."""

    @classmethod
    def setUpClass(cls):
        if not NODE:
            return
        cls.tmp = tempfile.mkdtemp(prefix="gemarc-wbs-site-")
        os.environ["GEMARC_ALLOW_WRITE"] = "1"
        workspace = Workspace(Path(cls.tmp))
        project = workspace.project("wbs")
        project.mkdir(parents=True, exist_ok=True)
        (project / "model").mkdir(parents=True, exist_ok=True)
        model = {
            "meta": {"system": "WBS 演示模型", "schemaVersion": "0.2.0", "language": "zh-CN",
                     "evidenceState": "Hypothetical"},
            "nodes": [{"id": "sensor", "kind": "Component", "name": "事件源"}],
            "edges": [],
            "evidence": [],
            "workflows": [execution_workflow(good_packages())],
        }
        (project / "model" / "architecture.json").write_text(
            json.dumps(model, ensure_ascii=False), encoding="utf-8")
        built = service.build(workspace, "wbs", None, "static")
        cls.site = Path(built["site"])

    @classmethod
    def tearDownClass(cls):
        os.environ["GEMARC_ALLOW_WRITE"] = "0"
        if getattr(cls, "tmp", None):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_workflow_view_shows_the_breakdown(self):
        result = subprocess.run(
            ["node", str(DUMP_TOOL), str(self.site)],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, (result.stderr or "")[:2000])
        body = json.dumps(json.loads(result.stdout)["workflow"], ensure_ascii=False)
        for expected in ("阶段", "工作包", "任务", "交付物", "完成条件", "归属执行节点"):
            self.assertIn(expected, body)
        self.assertIn("wp-collect", body)
        self.assertIn("结构化发现", body)

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_site_coverage_uses_the_derivation_count(self):
        """One denominator only.

        The card on the site and the derived artifact must count the same
        execution nodes. Two rules would make "coverage 1" mean two different
        things depending on where the reader looks.
        """
        result = subprocess.run(
            ["node", str(DUMP_TOOL), str(self.site)],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, (result.stderr or "")[:2000])
        body = json.dumps(json.loads(result.stdout)["workflow"], ensure_ascii=False)
        ir = json.loads((self.site / "data" / "workflow-ir.json").read_text(encoding="utf-8"))
        wbs = ir["workflows"][0]["wbs"]
        self.assertEqual(wbs["nodeCount"], 3)
        self.assertEqual(wbs["assignedNodeCount"], 3)
        pairs = {label.rstrip("：:"): value
                 for label, value in rendered_fields(json.loads(result.stdout)["workflow"])}
        self.assertEqual(pairs.get("执行节点归属"),
                         f"{wbs['assignedNodeCount']} / {wbs['nodeCount']}")

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_site_sheet_carries_the_breakdown_styles(self):
        sheet = (self.site / "style.css").read_text(encoding="utf-8")
        for name in (".wbs", ".wbs-item", ".wbs-detail"):
            self.assertIn(name, sheet)


class PublishedExampleTests(unittest.TestCase):
    """The published example carries the 6D layer - and still fits inline.

    The example is what every new reader sees first, so it has to show the
    work breakdown. It is also the model the MCP tool is called with, so it
    has to stay inside the inline budget: every file must arrive with its
    content, otherwise the site cannot be assembled from the answer.
    """

    @classmethod
    def setUpClass(cls):
        cls.model = json.loads(EXAMPLE_MODEL.read_text(encoding="utf-8"))

    def test_the_example_declares_a_three_level_breakdown(self):
        workflows = self.model.get("workflows") or []
        self.assertEqual(len(workflows), 1)
        packages = workflows[0]["executionGraph"]["workPackages"]
        self.assertEqual([package["kind"] for package in packages],
                         ["phase", "work-package", "task"])
        for package in packages:
            self.assertTrue(package["deliverable"])
            self.assertTrue(package["completionCriteria"])

    def test_the_example_passes_the_quality_gates(self):
        result = gemarc_model.validate(self.model, strict=False)
        self.assertEqual(result["status"], "ok", result.get("errors"))

    def test_the_example_breakdown_binds_every_execution_node(self):
        entry = derive.workflow_ir({"workflows": self.model["workflows"]})["workflows"][0]
        self.assertEqual(entry["gates"], [])
        self.assertEqual(entry["wbs"]["coverageRatio"], 1.0)
        self.assertEqual(entry["wbs"]["assignedNodeCount"], entry["wbs"]["nodeCount"])

    def test_the_example_stays_fully_inline(self):
        out = stateless.render(self.model)
        self.assertTrue(out["inline"], out.get("notice"))
        for item in out["files"]:
            self.assertIn("content", item, f"{item['path']} arrived without content")
        self.assertEqual(len(out["files"]), 9)


if __name__ == "__main__":
    unittest.main()
