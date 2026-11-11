"""The dimensions of change carry the scene; the work breakdown carries the links.

4D and 5D are dimensions of *change*. A list of events on its own says what
happened but not where it happens: the reader has to imagine the scene. So
both dimensions carry the semantic scene and mark the entities the change acts
on - 4D the module the running event acts on, 5D the modules the intervention
reaches.

6D is the dimension that binds the work breakdown to the execution graph. As
plain text the two halves stand next to each other and the reader cannot follow
the binding, so the binding itself is a link: work package -> execution node ->
entity, plus the way to the other dimensions.

The rules are pinned against a BUILT site, because the delivered artefact is
what matters, and they are checked in both shells: the kit (static and vite
share it) and the mvp preview. A rule that only one shell keeps is a rule the
reader will miss in the other.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp import service
from gemarc_mcp.paths import Workspace

REPO = Path(__file__).resolve().parent.parent
SCAFFOLD = REPO / "src" / "gemarc_mcp" / "scaffold"
FIXTURE = SCAFFOLD / "cases" / "view-model-fixture.json"
DUMP_TOOL = SCAFFOLD / "runtime" / "tools" / "dump-view-model.mjs"
PREVIEW_APP = REPO / "src" / "gemarc_mcp" / "web" / "app.js"

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


def anchors(spec) -> list:
    """Every link of a view-model tree (a view has a body, a node has children)."""
    found = []
    if isinstance(spec, dict):
        if spec.get("tag") == "a":
            found.append(spec)
        for child in spec.get("children") or []:
            found.extend(anchors(child))
        for child in spec.get("body") or []:
            found.extend(anchors(child))
    elif isinstance(spec, list):
        for item in spec:
            found.extend(anchors(item))
    return found


def function_source(text: str, name: str) -> str:
    """The source of one function of a classic script."""
    start = text.find(f"function {name}(")
    if start < 0:
        return ""
    end = text.find("\nfunction ", start + 1)
    return text[start:end if end > 0 else len(text)]


class DimensionContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gemarc-dimensions-")
        cls.site = build_site(Path(cls.tmp))
        cls.dump = dump(cls.site)

    @classmethod
    def tearDownClass(cls):
        os.environ["GEMARC_ALLOW_WRITE"] = "0"
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- 4D -------------------------------------------------------------------

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_fourth_dimension_carries_the_scene(self):
        """4D without the scene is a list of events, not a dimension of change."""
        timeline = self.dump["timeline"]
        self.assertEqual(timeline.get("mount"), "semantic3d")
        body = json.dumps(timeline, ensure_ascii=False)
        self.assertIn("axis-3d", body, "the 4D view has no scene to show")
        self.assertIn("三维语义轴 · 当前事件", body)

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_fourth_dimension_focuses_the_running_event(self):
        """The focus follows the event the reader stepped to."""
        self.assertEqual(self.dump["timeline"].get("highlight"), ["sensor"])

    # -- 5D -------------------------------------------------------------------

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_fifth_dimension_carries_the_scene(self):
        causal = self.dump["causal"]
        self.assertEqual(causal.get("mount"), "semantic3d")
        body = json.dumps(causal, ensure_ascii=False)
        self.assertIn("axis-3d", body, "the 5D view has no scene to show")
        self.assertIn("三维语义轴 · 干预影响", body)

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_fifth_dimension_focus_follows_the_intervention(self):
        """Without an intervention nothing is in focus; with one the focus is
        the module that was switched off plus the modules that lost an event."""
        self.assertEqual(self.dump["causal"].get("highlight"), [])
        focus = self.dump["causalIntervened"].get("highlight") or []
        self.assertIn("queue", focus, "the switched-off module is not in focus")
        self.assertTrue(
            set(focus) - {"queue"},
            "the modules that lost an event are not in focus either")

    # -- 6D -------------------------------------------------------------------

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_work_breakdown_links_to_the_execution_node(self):
        """The binding between a work package and its execution node is a link."""
        links = anchors(self.dump["workflow"])
        self.assertTrue(links, "the work breakdown has no links at all")
        targets = [link.get("attrs", {}).get("href") for link in links]
        self.assertIn("#2d", targets, "no link leads to the entity in 2D")
        located = [link for link in links
                   if (link.get("action") or {}).get("type") == "locate-node"]
        self.assertTrue(located, "no link selects the entity it points at")

    @unittest.skipUnless(NODE, "node is not available")
    def test_the_workflow_links_to_the_other_dimensions(self):
        targets = [link.get("attrs", {}).get("href")
                   for link in anchors(self.dump["workflow"])]
        for target in ("#3d", "#views", "#1d"):
            self.assertIn(target, targets, f"the workflow does not link to {target}")

    # -- the shells -----------------------------------------------------------

    def test_the_preview_shell_carries_the_same_rules(self):
        """The mvp preview is a second shell; a rule only the kit keeps is a
        rule the reader misses in the preview."""
        source = PREVIEW_APP.read_text(encoding="utf-8")
        for renderer in ("render4D", "render5D"):
            body = function_source(source, renderer)
            self.assertTrue(body, f"the preview has no {renderer}")
            self.assertIn("axis-3d", body, f"{renderer} has no scene to show")
            self.assertIn("mountAxisScene", body, f"{renderer} never mounts the scene")
            self.assertIn("applyFocal", body, f"{renderer} never marks what is in focus")
        workflow = function_source(source, "renderWorkflow")
        self.assertIn("link(", workflow, "the preview workflow has no links")
        for target in ("#2d", "#3d"):
            self.assertIn(target, workflow, f"the preview workflow does not link to {target}")

    def test_every_tab_is_reachable_by_its_hash(self):
        """A link points at a hash; a hash the page does not know falls back to
        the first tab, so the link would silently lead nowhere."""
        source = PREVIEW_APP.read_text(encoding="utf-8")
        match = re.search(r"showTab\(\[(?P<tabs>[^\]]+)\]\.includes", source)
        self.assertIsNotNone(match, "the preview has no hash routing")
        tabs = set(re.findall(r"'([a-z0-9]+)'", match.group("tabs")))
        self.assertEqual(tabs, {"1d", "2d", "3d", "4d", "5d", "workflow", "views", "prototype"})

    def test_leaving_a_scene_tab_releases_the_renderer(self):
        """A scene tab owns a WebGL context; leaving it must release the
        context, or the page leaks one per visit."""
        source = PREVIEW_APP.read_text(encoding="utf-8")
        self.assertIn("SCENE_TABS", source)
        show = function_source(source, "showTab")
        self.assertIn("dispose", show, "the preview never disposes the scene renderer")


if __name__ == "__main__":
    unittest.main()
