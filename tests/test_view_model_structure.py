"""Structure regression for the site's presentation layer.

The view models decide what the site shows. They are pure, so they can be
dumped as JSON and compared against a committed baseline. The comparison runs
against a BUILT site rather than the sources, because the application only
reaches the runtime by a relative path once the scaffold has been materialised
- testing the delivered artefact is the point.

Regenerate the baseline after an intentional presentation change:

    python -m gemarc_mcp.scaffold.viewmodel_baseline --write
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp import service
from gemarc_mcp.paths import Workspace

SCAFFOLD = Path(__file__).resolve().parent.parent / "src" / "gemarc_mcp" / "scaffold"
FIXTURE = SCAFFOLD / "cases" / "view-model-fixture.json"
BASELINE = SCAFFOLD / "cases" / "view-model-baseline.json"
DUMP_TOOL = SCAFFOLD / "runtime" / "tools" / "dump-view-model.mjs"

NODE = shutil.which("node")


def build_site(root: Path) -> Path:
    """Materialise a static site from the fixed fixture model."""
    os.environ["GEMARC_ALLOW_WRITE"] = "1"
    workspace = Workspace(root)
    project = workspace.project("fixture")
    project.mkdir(parents=True, exist_ok=True)
    (project / "model").mkdir(parents=True, exist_ok=True)
    (project / "model" / "architecture.json").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    built = service.build(workspace, "fixture", None, "static")
    return Path(built["site"])


def dump(site: Path) -> dict:
    result = subprocess.run(
        ["node", str(DUMP_TOOL), str(site)],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise AssertionError(f"dump failed: {(result.stderr or '').strip()[:2000]}")
    return json.loads(result.stdout)


def current_baseline(site: Path) -> dict:
    return dump(site)


class ViewModelStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gemarc-viewmodel-")
        cls.site = build_site(Path(cls.tmp))

    @classmethod
    def tearDownClass(cls):
        os.environ["GEMARC_ALLOW_WRITE"] = "0"
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @unittest.skipUnless(NODE, "node is not available")
    def test_structure_matches_the_committed_baseline(self):
        expected = json.loads(BASELINE.read_text(encoding="utf-8"))
        actual = current_baseline(self.site)
        self.assertEqual(
            json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2),
            json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2),
            "The site structure changed. If that is intended, regenerate the baseline with: "
            "python -m gemarc_mcp.scaffold.viewmodel_baseline --write")

    @unittest.skipUnless(NODE, "node is not available")
    def test_every_dimension_is_present(self):
        actual = current_baseline(self.site)
        for key in ("facts", "graph", "space", "timeline", "causal", "views", "workflow"):
            self.assertIn(key, actual)
            self.assertTrue(actual[key].get("headline"), key)
            self.assertTrue(actual[key].get("body"), key)
        # The inspector is a side panel, not a tab, so it has no headline.
        self.assertIn("inspector", actual)
        self.assertTrue(actual["inspector"].get("body"))

    @unittest.skipUnless(NODE, "node is not available")
    def test_timeline_reports_the_computed_events(self):
        actual = current_baseline(self.site)
        timeline = actual["timeline"]
        self.assertEqual(timeline["headline"], "动态流程与时间轴")
        self.assertEqual(timeline["trace"], 4)
        body = json.dumps(timeline, ensure_ascii=False)
        for expected in ("trace-0000", "trace-0001", "trace-0002", "trace-0003"):
            self.assertIn(expected, body)

    @unittest.skipUnless(NODE, "node is not available")
    def test_graph_view_lists_the_selected_nodes_and_edges(self):
        actual = current_baseline(self.site)
        self.assertEqual(actual["graph"]["graph"]["nodeIds"],
                         ["sensor", "queue", "rule", "notify"])
        self.assertEqual(actual["graph"]["graph"]["edgeIds"], ["e1", "e2", "e3"])

    @unittest.skipUnless(NODE, "node is not available")
    def test_space_view_requires_a_semantic_source(self):
        actual = current_baseline(self.site)
        body = json.dumps(actual["space"], ensure_ascii=False)
        self.assertIn("来自架构层定义", body)
        self.assertEqual(actual["space"]["mount"], "space3d")


if __name__ == "__main__":
    unittest.main()
