"""The 3D gate assertions and the real stage tabs in a browser (design 9.3 and M6 acceptance (a) to (d) and (f)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate_3d*.py"

Browser-free part (always runs): ``SceneVerdictTests`` judges synthetic scene records through ``smoke.evaluate`` (frames and nodes inside the canvas,
the occupancy floor and its 2x margin on both sides, the flags a hook may set), and checks the matrix rows of the two calibration fixtures.
Browser part (needs Edge or Chrome, otherwise a loud SKIP, never a pass): the fixtures clipped-scene and sparse-scene FAIL by name; the real 3D view of
the synthetic reference-scale atlas and of the 400-node probe PASS at three widths with the scene recorded; a mutant of the real page that pulls the camera
in FAILs with FRAME_CLIPPED; a page without WebGL shows DEGRADED and passes without a canvas; a project with one sealed and one unsealed case shows only the
sealed one in 4D and 5D. 20-run statistics are measured by ``tests/atlas/gate_html_matrix.py --fixtures`` (not part of the suite). Synthetic ids only.
"""
from __future__ import annotations

import html as htmllib
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, tools
from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas.gate import smoke

from . import synth
from .test_gate_html import dump_tab, describe, gate
from .test_oracle import expect_patch
from .test_oracle_support import OracleProject, consistent, slice_texts

FIXTURES = Path(__file__).resolve().parent / "gate_fixtures"
CALIBRATION = smoke.load_calibration()
BROWSER = smoke.find_browser()
NEEDS_BROWSER = unittest.skipUnless(BROWSER, "NO BROWSER (Edge or Chrome) FOUND: the 3D gate was NOT run in a browser (a skip is not a pass)")
WIDTHS = (1024, 1366, 1600)
FLOOR = float(CALIBRATION["thresholds"]["sceneOccupancyMin"])
#: Occupancy measured on the real synthetic views at the calibration run (3D tab, three widths): the lowest and the fixture's own value.
REAL_LOWEST = 0.603
SPARSE_FIXTURE = (8 * 4) / (310 * 190)


def setUpModule():
    if BROWSER is None:
        print("\n" + "=" * 100 + "\nSKIPPED, NOT PASSED: no Edge or Chrome found; the 3D gate tests of test_gate_3d.py did not run in a browser.\n" + "=" * 100, file=sys.stderr)


def tab_record(**scene) -> dict:
    """A tab record the way the self-test writes it, with the measures the verdicts do not look at set to values that pass."""
    base = {
        "tab": "3D", "threw": None, "frames": [1, 2, 3], "frameReads": [3, 3], "header": {"roots": 1, "n": 4, "maxOverlapPx": 0, "outside": []}, "scrollWidth": 1000, "clientWidth": 1000,
        "fit": {"n": 0, "clipped": []}, "textBad": [], "canvases": [], "draws": 0, "state": "3D",
    }
    base.update(scene)
    return base


def result_of(tab: dict, width: int = 1366) -> dict:
    return {"v": 2, "nonce": None, "innerWidth": width, "errors": [], "tabs": [tab], "steps": [], "missingHooks": [], "tabStateChecked": False}


def findings(tab: dict) -> list:
    return sorted({f.name for f in smoke.evaluate(result_of(tab), 1366, CALIBRATION, require_header=False)})


GOOD_SCENE = {
    "frames": [{"id": "building", "kind": "buildings", "x": 20, "y": 20, "w": 600, "h": 360}, {"id": "room", "kind": "rooms", "x": 40, "y": 40, "w": 300, "h": 200}],
    "nodes": [{"id": "a", "x": 60, "y": 60}, {"id": "b", "x": 300, "y": 300}, {"id": "c", "x": 520, "y": 120}],
    "occupancy": True,
}
BOX = {"w": 640, "h": 400}


class SceneVerdictTests(unittest.TestCase):
    def test_a_scene_inside_its_canvas_and_filling_its_frames_passes(self):
        self.assertEqual(findings(tab_record(scene=GOOD_SCENE, sceneCanvas=BOX)), [])

    def test_a_tab_without_a_scene_is_not_judged_by_it(self):
        self.assertEqual(findings(tab_record(scene=None, sceneCanvas=None)), [])
        self.assertEqual(findings(tab_record()), [], "a page without the hook has no scene key at all")

    def test_a_frame_that_leaves_the_canvas_fails_by_name_with_two_pixels_of_slack(self):
        for dx, dy, dw, dh, bad in [(-23, 0, 0, 0, True), (-22, 0, 0, 0, False), (0, -22.5, 0, 0, True), (0, 0, 23, 0, True), (0, 0, 22, 0, False), (0, 0, 0, 22, False), (0, 0, 0, 23, True)]:
            scene = json.loads(json.dumps(GOOD_SCENE))
            scene["frames"][0]["x"] += dx; scene["frames"][0]["y"] += dy; scene["frames"][0]["w"] += dw; scene["frames"][0]["h"] += dh
            # the box is 640 x 400 and the building frame spans 20..620 x 20..380: the edges are 20, 20, 20 and 20 px inside, so a move of 22 is exactly the slack
            got = findings(tab_record(scene=scene, sceneCanvas=BOX))
            self.assertEqual(got, ["FRAME_CLIPPED"] if bad else [], (dx, dy, dw, dh, got))

    def test_a_frame_behind_the_camera_has_no_box_and_counts_as_clipped(self):
        scene = json.loads(json.dumps(GOOD_SCENE)); scene["frames"][1] = {"id": "room", "kind": "rooms", "x": 0, "y": 0, "w": None, "h": None}
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas=BOX)), ["FRAME_CLIPPED"])

    def test_a_node_that_leaves_the_canvas_fails_by_its_own_name_so_node_bounds_and_frame_bounds_are_told_apart(self):
        scene = json.loads(json.dumps(GOOD_SCENE)); scene["nodes"].append({"id": "d", "x": 700, "y": 10})
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas=BOX)), ["NODE_CLIPPED"])
        scene["nodes"][-1] = {"id": "d", "x": None, "y": None}
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas=BOX)), ["NODE_CLIPPED"])

    def test_node_bounds_alone_would_pass_the_clipped_view_which_is_why_frames_count(self):
        scene = {"frames": [{"id": "building", "x": -60, "y": -40, "w": 300, "h": 190}], "nodes": [{"id": "n1", "x": 40, "y": 50}, {"id": "n2", "x": 120, "y": 90}]}
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas={"w": 320, "h": 200})), ["FRAME_CLIPPED"])
        self.assertEqual(findings(tab_record(scene={"frames": [], "nodes": scene["nodes"]}, sceneCanvas={"w": 320, "h": 200})), [], "without frames only the nodes are judged")

    def test_nodes_in_huge_frames_fail_the_occupancy_floor(self):
        scene = {"frames": [{"id": "building", "x": 4, "y": 4, "w": 310, "h": 190}], "nodes": [{"id": "n1", "x": 150, "y": 90}, {"id": "n2", "x": 158, "y": 94}]}
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas={"w": 320, "h": 200})), ["SCENE_SPARSE"])
        scene["nodes"] = []
        self.assertEqual(findings(tab_record(scene=scene, sceneCanvas={"w": 320, "h": 200})), ["SCENE_SPARSE"], "frames without any projected node")

    def test_a_node_is_not_a_point_one_or_two_parts_in_their_own_frame_pass_and_the_same_dots_without_a_radius_do_not(self):
        frame = {"id": "room", "x": 250, "y": 150, "w": 140, "h": 100}
        one = {"frames": [frame], "nodes": [{"id": "a", "x": 320, "y": 200, "r": 22}]}
        self.assertEqual(findings(tab_record(scene=one, sceneCanvas=BOX)), [], "a single part of radius 22 px in a 140 x 100 frame: 0.31")
        two = {"frames": [frame], "nodes": [{"id": "a", "x": 300, "y": 200, "r": 20}, {"id": "b", "x": 340, "y": 200, "r": 20}]}
        self.assertEqual(findings(tab_record(scene=two, sceneCanvas=BOX)), [])
        dots = {"frames": [frame], "nodes": [{"id": "a", "x": 300, "y": 200}, {"id": "b", "x": 340, "y": 200}]}
        self.assertEqual(findings(tab_record(scene=dots, sceneCanvas=BOX)), ["SCENE_SPARSE"], "without a radius the same two parts are a 40 x 0 box")
        tiny = {"frames": [frame], "nodes": [{"id": "a", "x": 320, "y": 200, "r": 2}]}
        self.assertEqual(findings(tab_record(scene=tiny, sceneCanvas=BOX)), ["SCENE_SPARSE"], "a speck in a frame is still sparse")

    def test_a_hook_may_skip_the_occupancy_but_never_the_inside_the_canvas_tests(self):
        sparse = {"frames": [{"id": "r", "x": 4, "y": 4, "w": 600, "h": 380}], "nodes": [{"id": "a", "x": 300, "y": 200}, {"id": "b", "x": 310, "y": 204}], "occupancy": False}
        self.assertEqual(findings(tab_record(scene=sparse, sceneCanvas=BOX)), [])
        sparse["frames"][0]["x"] = -30
        self.assertEqual(findings(tab_record(scene=sparse, sceneCanvas=BOX)), ["FRAME_CLIPPED"])

    def test_a_scene_without_a_measurable_canvas_a_throwing_hook_and_garbage_are_failures_not_passes(self):
        self.assertEqual(findings(tab_record(scene=GOOD_SCENE, sceneCanvas=None)), ["FRAME_CLIPPED"])
        self.assertEqual(findings(tab_record(scene=GOOD_SCENE, sceneCanvas={"w": 0, "h": 0})), ["FRAME_CLIPPED"])
        self.assertEqual(findings(tab_record(scene=None, sceneError="boom", sceneCanvas=None)), [], "a null scene is never judged ...")
        self.assertIn("RENDER", findings(tab_record(scene={"frames": [], "nodes": []}, sceneError="boom", sceneCanvas=BOX)), "... but an error from the hook is")
        self.assertEqual(findings(tab_record(scene={"frames": "no"}, sceneCanvas=BOX)), ["BAD_RESULT"])
        self.assertEqual(findings(tab_record(scene=[1, 2], sceneCanvas=BOX)), ["BAD_RESULT"])

    def test_the_occupancy_floor_has_a_two_times_margin_on_both_sides(self):
        self.assertGreaterEqual(FLOOR, 2 * SPARSE_FIXTURE, "the sparse fixture is at least 2x below the floor")
        self.assertLessEqual(FLOOR, REAL_LOWEST / 2, "the lowest real view is at least 2x above the floor")
        note = CALIBRATION["provisional"]["thresholds.sceneOccupancyMin"]
        self.assertIn("0.603", note); self.assertIn("2x margin", note)
        self.assertEqual(CALIBRATION["thresholds"]["sceneClipPx"], 2)

    def test_the_codes_exist_and_the_matrix_rows_of_the_two_fixtures_are_the_real_expectations(self):
        for name, code in (("FRAME_CLIPPED", "GEMARC_GATE_023"), ("NODE_CLIPPED", "GEMARC_GATE_024"), ("SCENE_SPARSE", "GEMARC_GATE_025")):
            self.assertEqual(smoke.CODES[name], code)
        self.assertEqual(len(set(smoke.CODES.values())), len(smoke.CODES))
        matrix = json.loads((FIXTURES / "MATRIX.json").read_text(encoding="utf-8"))
        rows = {f["name"]: f for f in matrix["fixtures"]}
        self.assertEqual(rows["clipped-scene"]["expect"], {"1366": {"status": "FAIL", "codes": ["FRAME_CLIPPED"]}})
        self.assertEqual(rows["sparse-scene"]["expect"], {"1366": {"status": "FAIL", "codes": ["SCENE_SPARSE"]}})
        for name in ("clipped-scene", "sparse-scene"):
            self.assertNotIn("pending", rows[name]); self.assertNotIn("current", rows[name])
            page = FIXTURES / rows[name]["file"]
            raw = page.read_bytes()
            self.assertTrue(raw.isascii() and b"\r" not in raw and raw.count(smoke.SELFTEST_MARKER.encode()) == 1, page.name)
            self.assertNotIn(b"PLACEHOLDER", raw)

    def test_the_selftest_only_measures_the_scene_it_never_judges_it(self):
        text = (smoke.GATE_DIR / "selftest.js").read_text(encoding="utf-8")
        self.assertIn("sceneBounds", text); self.assertIn("rec.sceneCanvas", text)
        self.assertNotIn("FRAME_CLIPPED", text); self.assertNotIn("SCENE_SPARSE", text)


#: Pointer, wheel and click on the real canvas of the 3D tab (the stage listens for the events a visitor causes; the stand-in page tests only the wiring).
POINTER_SNIPPET = (
    'AtlasApp.setTab("3D");var api=AtlasApp.api,sc=api.cache.get("stage:3D"),c=sc.canvas,r=c.getBoundingClientRect();'
    'var out={rect:[r.width,r.height],cam0:JSON.stringify(sc.cam),dist0:sc.cam.dist};'
    'function ptr(t,x,y){c.dispatchEvent(new PointerEvent(t,{clientX:r.left+x,clientY:r.top+y,pointerId:7,button:0,bubbles:true}))}'
    'ptr("pointerdown",300,200);ptr("pointermove",340,215);ptr("pointermove",380,230);ptr("pointerup",380,230);'
    'out.camAfterDrag=JSON.stringify(sc.cam);out.moved=sc.userMoved;'
    'c.dispatchEvent(new WheelEvent("wheel",{deltaY:100,clientX:r.left+300,clientY:r.top+200,bubbles:true,cancelable:true}));out.distAfterWheel=sc.cam.dist;'
    'var found=null;for(var y=10;y<r.height&&!found;y+=6)for(var x=10;x<r.width&&!found;x+=6){ptr("pointermove",x,y);if(sc.hover)found={x:x,y:y,id:sc.hover}}'
    'out.found=found;if(found){ptr("pointerdown",found.x,found.y);ptr("pointerup",found.x,found.y);out.sel=api.state.sel;out.inspector=!!document.querySelector(".insp-detail")}'
    'window.__out=out'
)


class PageCompactionTests(unittest.TestCase):
    """The page embeds what the stage tabs read of an event and nothing of the keys they never read (the size budget of the largest seed depends on it)."""

    EVENT = {"index": 3, "sequence": 4, "caseId": "C-1", "eventId": "C-1#4", "logicalTime": 7, "sourceNodeId": "a", "targetNodeId": "b", "edgeId": "e", "eventType": "Call", "outcome": "Blocked",
             "stateChanges": [{"nodeId": "b", "from": "Idle", "to": "Blocked"}], "ruleId": "R-1", "evidenceIds": ["EV-1"], "cause": {"code": "X", "args": {}}, "attempt": 2, "errorCode": "E1"}

    def test_the_unread_event_keys_are_gone_and_everything_the_stage_reads_is_kept(self):
        out = build._compact_event(self.EVENT)
        for key in build.EVENT_UNREAD_KEYS:
            self.assertNotIn(key, out)
        self.assertEqual(sorted(build.EVENT_UNREAD_KEYS), ["attempt", "cause", "evidenceIds", "ruleId"])
        for key in ("index", "sourceNodeId", "targetNodeId", "edgeId", "eventType", "outcome", "stateChanges"):
            self.assertEqual(out[key], self.EVENT[key], key)

    def test_no_runtime_module_reads_a_key_that_the_page_drops(self):
        ui = Path(__file__).resolve().parents[2] / "src" / "gemarc_mcp" / "atlas" / "viewer" / "src"
        modules = sorted(ui.glob("*.js")) + sorted((ui / "ui").glob("*.js"))
        self.assertGreater(len(modules), 30)
        for path in modules:
            rel = path.relative_to(ui).as_posix()
            code = re.sub(r"//.*", "", path.read_text(encoding="utf-8"))
            for key in build.EVENT_UNREAD_KEYS:
                self.assertIsNone(re.search(r"\.%s" % key, code), f"{rel} reads .{key}")


class WorkDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_page(self, html_text: str, name: str = "page.html") -> Path:
        path = self.tmp / name
        path.write_text(html_text, encoding="utf-8", newline="\n")
        return path


def scene_of(result, tab="3D") -> dict:
    rec = next(t for t in result.result["tabs"] if t["tab"] == tab)
    return rec


def occupancy(scene: dict) -> float:
    frames = [f for f in scene["frames"] if f["w"] is not None]
    nodes = [n for n in scene["nodes"] if n["x"] is not None]
    frame_area = (max(f["x"] + f["w"] for f in frames) - min(f["x"] for f in frames)) * (max(f["y"] + f["h"] for f in frames) - min(f["y"] for f in frames))
    node_area = (max(n["x"] + n.get("r", 0) for n in nodes) - min(n["x"] - n.get("r", 0) for n in nodes)) * (max(n["y"] + n.get("r", 0) for n in nodes) - min(n["y"] - n.get("r", 0) for n in nodes))
    return node_area / frame_area


@NEEDS_BROWSER
class CalibrationFixtureTests(WorkDir):
    def test_clipped_scene_and_sparse_scene_fail_by_name_and_only_by_that_name(self):                              # acceptance (b)
        for name, code in (("clipped-scene", "FRAME_CLIPPED"), ("sparse-scene", "SCENE_SPARSE")):
            result = gate(FIXTURES / "scene" / f"{name}.html", 1366, self.tmp, inline_selftest=True)
            self.assertEqual((result.status, result.codes), ("FAIL", [code]), describe(result))
            self.assertEqual(result.result["innerWidth"], 1366)

    def test_the_measured_canvas_is_the_one_the_page_declared_not_a_number_the_page_reported(self):
        result = gate(FIXTURES / "scene" / "clipped-scene.html", 1366, self.tmp, inline_selftest=True)
        rec = result.result["tabs"][0]
        self.assertEqual(rec["sceneCanvas"], {"w": 320, "h": 200})
        self.assertEqual(len(rec["scene"]["frames"]), 1)


@NEEDS_BROWSER
class RealStageTests(WorkDir):
    """The synthetic reference-scale atlas (sealed, built by the project tools) and the 400-node probe."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        root = Path(cls._tmp.name)
        ws = pj.get_workspace(root)
        # seed 5: the 5D tab draws only when the sealed comparisons contain one that shows a difference AND has something on its stage (and three
        # draw calls need a stage with frames, nodes and edges), and the generator (debt-b4: no port-type mismatch edges, so a different graph for every
        # seed) gives that for seed 5 (measured: seeds 1 and 3 have no 5D canvas, seed 2 has one with two draw calls)
        model = consistent(synth.generate("reference", 5))
        assert tools.init(ws, "atlas", "Reference scale", "asis", ["en-US"], repos=model["meta"]["repos"])["status"] == "created"
        for name, text in slice_texts(model).items():
            assert tools.put(ws, "atlas", name, text)["status"] == "committed", name
        reply = tools.build(ws, "atlas", docs=False)
        assert reply["status"] == "ok", reply
        cls.reference = build.dist_dir(pj.project_paths(ws, "atlas")) / "index.html"
        probe = synth.edge_case("big400", 1)
        synth._inject_unknown_values(probe)
        probe["cases"], probe["interventions"] = [], []
        cls.probe = root / "probe400.html"
        cls.probe.write_text(build.render(probe).html, encoding="utf-8", newline="\n")

    def test_the_real_3d_view_of_the_reference_scale_atlas_passes_at_three_widths_inside_the_time_budget(self):    # acceptance (a), (b)
        started = time.monotonic()
        seen = []
        for width in WIDTHS:
            result = gate(self.reference, width, self.tmp)
            self.assertEqual((result.status, result.codes), ("PASS", []), describe(result))
            self.assertEqual(result.result["innerWidth"], width)
            self.assertEqual([t["tab"] for t in result.result["tabs"]], ["1D", "2D", "3D", "4D", "5D", "QA"])
            self.assertEqual(sum(1 for s in result.result["steps"] if s["ok"]), build.GATE_STEPS)
            rec = scene_of(result)
            self.assertGreater(len(rec["scene"]["frames"]), 3); self.assertGreater(len(rec["scene"]["nodes"]), 50)
            self.assertGreater(rec["draws"], 0)
            occ = occupancy(rec["scene"]); seen.append(occ)
            self.assertGreaterEqual(occ, 2 * FLOOR, f"{width}: occupancy {occ:.3f}")
            self.assertGreater(scene_of(result, "4D")["draws"], 0)
        self.assertLess(time.monotonic() - started, 120, "six tabs at three widths in at most 120 s")
        print(f"\n[3D occupancy, reference atlas, widths {WIDTHS}] " + ", ".join(f"{o:.3f}" for o in seen) + f" (floor {FLOOR}, sparse fixture {SPARSE_FIXTURE:.4f})")

    def test_the_400_node_model_passes_at_three_widths_with_its_scene_recorded(self):                                # acceptance (b)
        for width in WIDTHS:
            result = gate(self.probe, width, self.tmp)
            self.assertEqual((result.status, result.codes), ("PASS", []), describe(result))
            rec = scene_of(result)
            self.assertGreater(len(rec["scene"]["nodes"]), 250)
            self.assertGreaterEqual(occupancy(rec["scene"]), 2 * FLOOR)
            self.assertEqual([t["tab"] for t in result.result["tabs"]], ["1D", "2D", "3D", "4D", "5D", "QA"])

    def test_the_gate_reads_pixels_and_draw_calls_of_the_real_scene(self):
        result = gate(self.reference, 1366, self.tmp)
        for tab in ("3D", "4D", "5D"):          # 5D opens on the one comparison of the reference-scale atlas that shows a difference
            rec = scene_of(result, tab)
            canvases = [c for c in rec["canvases"] if c["visible"]]
            self.assertEqual(len(canvases), 1, tab)
            self.assertIn(canvases[0]["kind"], ("webgl", "webgl2")); self.assertGreater(canvases[0]["nonBgRatio"], CALIBRATION["thresholds"]["nonBgFloor"] * 10)
            self.assertEqual(canvases[0]["glErrors"], []); self.assertIn("swiftshader", canvases[0]["renderer"].lower())
            self.assertGreaterEqual(rec["draws"], 3)
        self.assertIs(scene_of(result, "5D")["scene"]["occupancy"], False, "a stage frames whole rooms: no occupancy floor, the inside-the-canvas tests still run")
        self.assertIs(scene_of(result, "3D")["scene"]["occupancy"], True)
        for tab in ("1D", "2D", "QA"):
            self.assertEqual([c for c in scene_of(result, tab)["canvases"] if c["visible"]], [], f"{tab} draws no canvas, and nothing is blank")

    def eval_in_page(self, snippet: str, tab: str = "3D") -> dict:
        """Run a snippet in a copy of the reference page (not in gate mode) after load and return what it put in ``window.__out``."""
        text = self.reference.read_text(encoding="utf-8")
        injected = ('<script>setTimeout(function(){var d=document.createElement("pre");d.id="dbg";try{' + snippet + ';d.textContent=JSON.stringify(window.__out)}'
                    'catch(e){d.textContent="ERR "+e}document.body.appendChild(d)},50);</script></body>')
        page = self.write_page(text.replace("</body>", injected), "eval.html")
        calibration = smoke.load_calibration()
        offset = smoke.width_offset(calibration, BROWSER.family, 1366)
        profile = self.tmp / "profile-eval"
        profile.mkdir(exist_ok=True)
        url = smoke.page_url(page, "view=1") + f"#/{tab}"
        launch = smoke._launch(smoke.build_args(BROWSER.path, str(profile), (1366 + offset, 800), url, 20000), 60, env=smoke._browser_env(self.tmp / "browser-tmp-eval"))
        smoke._rmtree_retry(profile)
        match = re.search(r'<pre id="dbg">(.*?)</pre>', launch.stdout.decode("utf-8", "replace"), re.S)
        self.assertIsNotNone(match, "the snippet wrote no result")
        out = htmllib.unescape(match.group(1))
        self.assertFalse(out.startswith("ERR"), out)
        return json.loads(out)

    def test_pointer_and_wheel_drive_the_real_camera_and_a_click_selects_a_part(self):
        out = self.eval_in_page(POINTER_SNIPPET)
        self.assertGreater(out["rect"][0], 300); self.assertGreater(out["rect"][1], 300)
        self.assertNotEqual(out["cam0"], out["camAfterDrag"], "dragging orbits the camera"); self.assertTrue(out["moved"])
        self.assertGreater(out["distAfterWheel"], out["dist0"], "the wheel zooms out")
        self.assertIsNotNone(out["found"], "hovering over the canvas finds a part")
        self.assertEqual(out["sel"], {"type": "node", "id": out["found"]["id"]}, "a click selects the part under the pointer")
        self.assertTrue(out["inspector"], "and the inspector shows its detail")

    def run_mutant(self, old: str, new: str, width: int = 1366):
        text = self.reference.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"the mutation target must be unique in the page: {old!r}")
        return gate(self.write_page(text.replace(old, new), "mutant.html"), width, self.tmp)

    def test_a_camera_pulled_in_on_the_real_page_fails_with_frame_clipped(self):                                    # the assertion has teeth on the real view
        result = self.run_mutant("aspect = 1.6, margin = 1.08", "aspect = 1.6, margin = 0.45")
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertIn("FRAME_CLIPPED", result.codes)
        self.assertTrue(all(f.tab in ("3D", "4D", "5D") for f in result.findings if f.name == "FRAME_CLIPPED"))

    def test_a_scene_that_does_not_draw_fails_with_no_draw_and_non_bg(self):
        result = self.run_mutant("let calls = 0;", "let calls = 0; return 0;")       # glDraw returns before it touches the context
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertTrue({"NON_BG", "NO_DRAW"} <= set(result.codes), describe(result))
        # drawing the frames and the edges but not one part is NOT caught by the gate: it sees a drawn scene (an honest limit, see the notes of viewer-B)

    def test_a_page_without_webgl_says_degraded_and_passes_without_a_canvas_or_a_scene(self):
        result = self.run_mutant("try { const gl = canvas.getContext(kind, attrs); if (gl) return gl; }", "try { const gl = null; if (gl) return gl; }")
        self.assertEqual((result.status, result.codes), ("PASS", []), describe(result))
        for tab in ("3D", "4D", "5D"):
            rec = scene_of(result, tab)
            self.assertEqual([c for c in rec["canvases"] if c["visible"]], [], tab)
            self.assertIsNone(rec.get("scene"), tab)
        dom = dump_tab(self.write_page(self.reference.read_text(encoding="utf-8").replace("try { const gl = canvas.getContext(kind, attrs); if (gl) return gl; }", "try { const gl = null; if (gl) return gl; }"), "nogl.html"), "3D", self.tmp)
        self.assertIn("DEGRADED", dom)
        self.assertIn("twod", dom)


@NEEDS_BROWSER
class MixedProjectTests(OracleProject):
    """Acceptance (d): a project with sealed cases and one unsealed (revised) case: 4D and 5D list the sealed ones only and nothing of the other, in a real browser."""

    def setUp(self):
        super().setUp()
        model = consistent(synth.generate("small", 3))
        self.first, self.second = model["cases"][0]["caseId"], model["cases"][1]["caseId"]
        self.second_title = model["cases"][1]["title"]
        self.init(name="mixed", repos=model["meta"]["repos"])
        slices = slice_texts(model)
        for name, text in slices.items():
            self.assertEqual(self.put(name, text)["status"], "committed", name)
        expect_slice = next(name for name, text in slices.items() if "## expect:" in text and any(line.startswith(self.second + " |") for line in text.split("## expect:")[-1].splitlines()))
        self.simulate(case=self.second, detail="trace")                     # a reveal of the second case ...
        terminal = model["cases"][1]["expectedTerminalState"]
        machines = {m["id"] for m in model["machines"]}
        victim = next(n for n in terminal if n not in machines)             # a state machine has its own states: only a plain node can end Failed
        row = f"{self.second} | " + ",".join(f"{n}={'Failed' if n == victim else s}" for n, s in terminal.items()) + " | by=author"
        self.assertEqual(self.put(expect_slice, f"#atlas 1\n## expect: case | nodes | opts\n{row}\n", mode="patch")["status"], "committed")   # ... then an edit: revised
        self.assertEqual(self.states()[f"case:{self.second}"], "revised")
        reply = tools.build(self.ws, self.name, docs=False)
        self.assertEqual(reply["status"], "ok", reply)
        self.page = build.dist_dir(self.paths) / "index.html"
        self.data = H.extract_data(self.page.read_text(encoding="utf-8"))

    def test_the_page_embeds_the_sealed_cases_and_not_the_unsealed_one(self):
        self.assertIn(self.first, self.data["runs"]["baselines"])
        self.assertNotIn(self.second, self.data["runs"]["baselines"])
        self.assertTrue(all(p["caseId"] != self.second for p in self.data["runs"]["pairs"]))

    def test_4d_and_5d_list_only_the_sealed_case_and_play_it(self):
        tmp = Path(tempfile.mkdtemp(prefix="gate3d-", dir=self._tmp.name))
        length = len(self.data["runs"]["baselines"][self.first]["trace"])
        for tab in ("4D", "5D"):
            dom = dump_tab(self.page, tab, tmp)
            self.assertIn(f'value="{self.first}"', dom, tab)
            self.assertNotIn(self.second, dom, f"{tab}: no id of the unsealed case in what is drawn")
            self.assertNotIn(self.second_title, dom, tab)
            self.assertIn("Cases without a sealed result are not offered: 1", dom, tab)
        dom4 = dump_tab(self.page, "4D", tmp)
        self.assertRegex(dom4, rf'data-step-count="{length}"')

    def test_the_gate_passes_on_the_mixed_page_and_steps_the_sealed_case(self):
        tmp = Path(tempfile.mkdtemp(prefix="gate3d-", dir=self._tmp.name))
        for width in (1024, 1600):
            result = gate(self.page, width, tmp)
            self.assertEqual((result.status, result.codes), ("PASS", []), describe(result))
            names = {s["name"]: s["ok"] for s in result.result["steps"]}
            self.assertTrue(names["case-play"] and names["pair-play"] and names["scene-3d"] and names["inspector-tabs"], names)


@NEEDS_BROWSER
class UnsealedPageTests(WorkDir):
    def test_an_unsealed_page_shows_the_empty_state_in_4d_and_5d_with_no_canvas_and_passes(self):
        model = consistent(synth.generate("small", 3))
        page = self.write_page(build.render(model).html, "unsealed.html")
        for width in (1024, 1366):
            result = gate(page, width, self.tmp)
            self.assertEqual((result.status, result.codes), ("PASS", []), describe(result))
            for tab in ("4D", "5D"):
                rec = scene_of(result, tab)
                self.assertEqual([c for c in rec["canvases"] if c["visible"]], [], tab)
                self.assertIsNone(rec.get("scene"), tab)
        for tab in ("4D", "5D"):
            self.assertIn("Author the expectation first", dump_tab(page, tab, self.tmp))


if __name__ == "__main__":
    unittest.main()
