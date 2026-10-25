"""End to end: scaffold, fill the slots, build a site, prove the site can load.

The point of the scaffold is that a language model only fills slots. This
test walks that path exactly as a caller would, and then checks the thing
that actually matters: the built site resolves and loads its runtime, not
just that the files exist.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp import service
from gemarc_mcp.paths import Workspace

NODE = shutil.which("node")


def _filled_model(system: str = "demo") -> dict:
    return {
        "meta": {
            "system": system,
            "schemaVersion": "0.2.0",
            "language": "zh-CN",
            "evidenceState": "Illustrative",
            "description": "端到端测试模型",
            "source": {"repository": None, "branch": None, "commit": None,
                       "localPath": None, "analyzedAt": None},
        },
        "nodes": [
            {"id": "sensor", "name": "事件源", "kind": "Component",
             "verification": "illustrative", "evidence": "示例"},
            {"id": "queue", "name": "消息队列", "kind": "Service",
             "verification": "illustrative", "evidence": "示例"},
        ],
        "edges": [
            {"id": "e1", "sourceId": "sensor", "targetId": "queue", "type": "sends",
             "verification": "illustrative", "evidence": "示例"},
        ],
        "evidence": [
            {"id": "ev1", "kind": "code", "level": "illustrative",
             "source": {"locator": "src/demo.py:1"}},
        ],
        "views": [
            {"id": "logical", "title": "逻辑架构", "diagramType": "logical-view",
             "concerns": ["事件依赖"], "nodeIds": ["sensor", "queue"], "edgeKinds": ["sends"]},
        ],
        "scenarios": [
            {"id": "normal", "name": "正常", "evidenceClass": "hypothetical",
             "events": [{"module": "sensor", "event": "emit", "timeMs": 0}],
             "rules": [{"source": "sensor", "whenEvent": "emit", "target": "queue",
                        "emit": "ingest", "delayMs": 10}]},
        ],
    }


class ScaffoldFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemarc-flow-")
        self.ws = Workspace(self.tmp)
        os.environ["GEMARC_ALLOW_WRITE"] = "1"

    def tearDown(self):
        os.environ["GEMARC_ALLOW_WRITE"] = "0"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scaffold_then_fill_then_build(self):
        scaffold = service.scaffold_project(self.ws, "demo", "static")
        self.assertEqual(scaffold["status"], "ok")
        scaffold_dir = Path(scaffold["scaffold"])
        self.assertTrue((scaffold_dir / "scaffold-manifest.json").is_file())

        # The project model is the slot the caller fills.
        model_path = Path(scaffold["model"])
        self.assertTrue(model_path.is_file())
        model_path.write_text(json.dumps(_filled_model(), ensure_ascii=False, indent=2),
                              encoding="utf-8")

        built = service.build(self.ws, "demo", None, "static")
        self.assertEqual(built["status"], "ok")
        self.assertEqual(built["profile"], "static")
        site = Path(built["site"])
        self.assertTrue((site / "index.html").is_file())
        self.assertTrue((site / "app.js").is_file())
        self.assertTrue((site / "runtime" / "core" / "model.js").is_file())
        self.assertTrue((site / "data" / "architecture.json").is_file())

        checked = service.verify(self.ws, "demo")
        self.assertEqual(checked["status"], "ok")
        self.assertEqual(checked["profile"], "static")

    def test_scaffold_refuses_to_overwrite(self):
        service.scaffold_project(self.ws, "demo", "static")
        with self.assertRaises(Exception):
            service.scaffold_project(self.ws, "demo", "static")

    def test_built_site_data_is_canonical(self):
        service.init_project(self.ws, "demo")
        target = Path(self.tmp) / "demo" / "model" / "architecture.json"
        target.write_text(json.dumps(_filled_model(), ensure_ascii=False), encoding="utf-8")
        service.build(self.ws, "demo", None, "static")
        site = Path(self.tmp) / "demo" / "site"
        data = json.loads((site / "data" / "architecture.json").read_text(encoding="utf-8"))
        self.assertEqual(data["edges"][0]["sourceId"], "sensor")
        self.assertNotIn("source", data["edges"][0])
        self.assertEqual(data["nodes"][0]["verification"], "Hypothetical")

    def test_vite_profile_ships_the_npm_project(self):
        service.init_project(self.ws, "demo")
        target = Path(self.tmp) / "demo" / "model" / "architecture.json"
        target.write_text(json.dumps(_filled_model(), ensure_ascii=False), encoding="utf-8")
        built = service.build(self.ws, "demo", None, "vite")
        site = Path(built["site"])
        self.assertTrue((site / "package.json").is_file())
        self.assertTrue((site / "vite.config.js").is_file())
        self.assertTrue((site / "src" / "main.js").is_file())
        self.assertEqual(service.verify(self.ws, "demo")["status"], "ok")

    def test_mvp_profile_still_works(self):
        service.init_project(self.ws, "demo")
        target = Path(self.tmp) / "demo" / "model" / "architecture.json"
        target.write_text(json.dumps(_filled_model(), ensure_ascii=False), encoding="utf-8")
        built = service.build(self.ws, "demo", None, "mvp")
        self.assertEqual(built["profile"], "mvp")
        self.assertEqual(service.verify(self.ws, "demo")["status"], "ok")

    def test_unknown_profile_is_refused(self):
        service.init_project(self.ws, "demo")
        with self.assertRaises(Exception):
            service.build(self.ws, "demo", None, "webpack")

    @unittest.skipUnless(NODE, "node is not available")
    def test_built_runtime_actually_imports(self):
        """The shipped runtime must load as-is, not merely exist on disk."""
        service.init_project(self.ws, "demo")
        target = Path(self.tmp) / "demo" / "model" / "architecture.json"
        target.write_text(json.dumps(_filled_model(), ensure_ascii=False), encoding="utf-8")
        service.build(self.ws, "demo", None, "static")
        site = Path(self.tmp) / "demo" / "site"
        script = (
            "const base = process.argv[1];"
            "const m = await import('file:///' + base + '/runtime/core/model.js');"
            "const s = await import('file:///' + base + '/runtime/graph/graph-store.js');"
            "const sim = await import('file:///' + base + '/runtime/sim/index.js');"
            "const model = m.normalizeModel(JSON.parse(process.argv[2]));"
            "const store = new s.GraphStore(model);"
            "const report = sim.simulate(model, 'normal', []);"
            "process.stdout.write(JSON.stringify({nodes: store.nodeCount, edges: store.edgeCount,"
            " delivered: report.summary.delivered, level: model.nodes[0].verification}));"
        )
        result = subprocess.run(
            [NODE, "--input-type=module", "-e", script, str(site).replace("\\", "/"),
             json.dumps(_filled_model(), ensure_ascii=False)],
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stderr[:2000])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["nodes"], 2)
        self.assertEqual(payload["edges"], 1)
        self.assertEqual(payload["delivered"], 2)
        self.assertEqual(payload["level"], "Hypothetical")

    def test_contracts_tool_lists_the_slots(self):
        result = service.contracts()
        self.assertEqual(result["status"], "ok")
        self.assertIn("canonical-model.schema.json", result["contracts"])
        self.assertIn("evidence-levels.json", result["vocabularies"])
        self.assertIn("static", result["profiles"])
        self.assertTrue(result["slots"])

    def test_contracts_tool_can_return_one_document(self):
        result = service.contracts("evidence-levels.json")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["document"]["canonical"]), 6)


if __name__ == "__main__":
    unittest.main()
