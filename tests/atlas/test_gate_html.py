"""The browser gate on REAL built pages (design 9.3, M4 acceptance (b), (c), (e) and the browser side of (g)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate_html*.py"

Needs Edge or Chrome. Without one every browser test SKIPS and ``setUpModule`` prints a loud banner: a skipped gate is never a pass
(the browser-free tests of the SKIP path still run). One launch per width here; the 20-run statistics are measured by
``tests/atlas/gate_html_matrix.py`` (not part of the suite). Synthetic id families only.

* ``BuiltPageGateTests``: a sealed reference-scale project, built with ``gate=smoke`` and verified with ``verify``: QA, 1D, 2D (and the
  three reserved tabs) at 1024, 1366 and 1600 with zero errors; the in-page document twin check (e) fails when a hash or a fragment drifts.
* ``HostileAndGenericPageTests``: a page whose every free-text field is hostile markup renders without injected script; the genericity probe
  (random id family, 400 nodes, unknown building / kind / trust, no cases) renders with zero errors and a live heartbeat.
* ``CalibrationMatrixTests``: the fixtures of ``gate_fixtures/MATRIX.json`` FAIL and PASS exactly as design 9.3 says.
* ``SkipAndFailClosedTests``: no browser means SKIP (never PASS) all the way up to the tool replies; a crashing runner is a FAIL.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, tools
from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas.gate import smoke

from . import synth
from .test_build_page import hostile_model
from .test_oracle import expect_patch
from .test_oracle_support import OracleProject, consistent, slice_texts

FIXTURES = Path(__file__).resolve().parent / "gate_fixtures"
PROFILE = json.loads((H.PACKAGE / "profiles" / "software.json").read_text(encoding="utf-8"))
BROWSER = smoke.find_browser()
NEEDS_BROWSER = unittest.skipUnless(BROWSER, "NO BROWSER (Edge or Chrome) FOUND: the browser gate was NOT run (a skip is not a pass)")
WIDTHS = (1024, 1366, 1600)


def setUpModule():
    if BROWSER is None:
        print("\n" + "=" * 100 + "\nSKIPPED, NOT PASSED: no Edge or Chrome found; the real-browser tests of test_gate_html.py did not run.\n" + "=" * 100, file=sys.stderr)


def gate(page: Path, width: int, tmp: Path, **kw):
    return smoke.run_gate(page, width, browser=BROWSER, tmp_root=tmp, **kw)


def describe(result) -> str:
    return f"{result.status} {result.codes}: {result.message}"[:600]


def dump_tab(page: Path, tab: str, tmp: Path, width: int = 1366) -> str:
    """The DOM text of one tab of a page, from a headless launch without gate mode (``--dump-dom`` at the end of the virtual time)."""
    calibration = smoke.load_calibration()
    offset = smoke.width_offset(calibration, BROWSER.family, width)
    profile = tmp / "profile"
    profile.mkdir(exist_ok=True)
    url = smoke.page_url(page, "view=1") + f"#/{tab}"
    launch = smoke._launch(smoke.build_args(BROWSER.path, str(profile), (width + offset, 800), url, 20000), 60, env=smoke._browser_env(tmp / "browser-tmp"))
    smoke._rmtree_retry(profile)
    return rendered_root(launch.stdout.decode("utf-8", "replace"))


def rendered_root(dom: str) -> str:
    """What the viewer has drawn: the content of ``#atlas-root`` (the dump also holds the scripts, whose data carry every message text)."""
    head, sep, tail = dom.partition('id="atlas-root"')
    assert sep, "the dump has no #atlas-root"
    return tail.split("</script>", 1)[0].split("<script", 1)[0]


class WorkDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write_page(self, html_text: str, name: str = "page.html") -> Path:
        path = self.tmp / name
        path.write_text(html_text, encoding="utf-8", newline="\n")
        return path


@NEEDS_BROWSER
class BuiltPageGateTests(unittest.TestCase):
    """One sealed reference-scale project, built once with the gate switched on."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.ws = pj.get_workspace(Path(cls._tmp.name))
        model = consistent(synth.generate("reference", 3))
        assert tools.init(cls.ws, "atlas", "Reference scale", "asis", ["en-US"], repos=model["meta"]["repos"])["status"] == "created"
        for name, text in slice_texts(model).items():
            assert tools.put(cls.ws, "atlas", name, text)["status"] == "committed", name
        cls.paths = pj.project_paths(cls.ws, "atlas")
        cls.dist = build.dist_dir(cls.paths)
        cls.reply = tools.build(cls.ws, "atlas", gate="smoke")

    def test_build_with_the_gate_passes_at_three_widths_on_all_tabs(self):
        self.assertEqual(self.reply["status"], "ok", self.reply)
        self.assertEqual(self.reply["gate"], "PASS", self.reply)
        report = json.loads((self.dist / ".verify" / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual([w["width"] for w in report["widths"]], list(WIDTHS))
        for row in report["widths"]:
            self.assertEqual(row["status"], "PASS", row)
            self.assertEqual(row["innerWidth"], row["width"], "the page reported the width that was asked for")
            self.assertEqual(row["codes"], [])
            self.assertEqual(row["tabs"], ["1D", "2D", "3D", "4D", "5D", "QA"])
            self.assertEqual(row["steps"], {"ok": build.GATE_STEPS, "total": build.GATE_STEPS})
        self.assertFalse((self.dist / ".verify" / "work").exists(), "the scratch directory of the gate is removed")
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info["gate"], {"status": "PASS", "widths": [{"width": w, "status": "PASS", "codes": []} for w in WIDTHS]})

    def test_verify_passes_and_writes_only_below_dist_verify(self):
        before = {p.relative_to(self.dist).as_posix() for p in self.dist.rglob("*") if p.is_file() and ".verify" not in p.parts}
        reply = tools.verify(self.ws, "atlas")
        self.assertEqual((reply["status"], reply["gate"]), ("ok", "PASS"), reply)
        self.assertLessEqual(tools.reply_bytes(reply), tools.VERIFY_CAP)
        self.assertEqual([w["width"] for w in reply["widths"]], list(WIDTHS))
        self.assertEqual(reply["shots"], [])
        after = {p.relative_to(self.dist).as_posix() for p in self.dist.rglob("*") if p.is_file() and ".verify" not in p.parts}
        self.assertEqual(before, after)
        self.assertTrue(Path(reply["report"]).is_file())

    def test_verify_at_a_single_other_width(self):
        reply = tools.verify(self.ws, "atlas", [1256])
        self.assertEqual((reply["status"], reply["gate"]), ("ok", "PASS"), reply)
        self.assertEqual([w["width"] for w in reply["widths"]], [1256])

    def page_text(self) -> str:
        return (self.dist / "index.html").read_text(encoding="utf-8")

    def run_tampered(self, text: str, tmp: Path):
        page = tmp / "tampered.html"
        page.write_text(text, encoding="utf-8", newline="\n")
        return gate(page, 1366, tmp)

    def test_a_drifting_document_hash_fails_the_gate_with_the_twin_step(self):                       # acceptance (e)
        text = self.page_text()
        match = re.search(r'"hash32":"([0-9a-f]{8})"', text)
        self.assertIsNotNone(match)
        wrong = "00000000" if match.group(1) != "00000000" else "11111111"
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_tampered(text[:match.start(1)] + wrong + text[match.end(1):], Path(tmp))
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertIn("PAGE_ERRORS", result.codes)
        self.assertIn("STEP_FAILED", result.codes)
        self.assertTrue(any("doc-twin" in f.detail for f in result.findings), describe(result))

    def test_a_drifting_fragment_fails_the_gate_with_the_twin_step(self):                            # acceptance (e)
        data = H.extract_data(self.page_text())
        data["fragments"]["items"][3]["md"] += " drift"
        page = H.fill_page(H.load_viewer(), data, lang="en-US", title="drift")
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_tampered(page, Path(tmp))
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertIn("STEP_FAILED", result.codes)

    # -- calibration of the checks the first checkpoint found missing: each mutant undoes one fix in the page text and must fail by name

    def run_mutant(self, old: str, new: str, tmp: Path):
        text = self.page_text()
        self.assertEqual(text.count(old), 1, f"the mutation target must be unique in the page: {old!r}")
        return self.run_tampered(text.replace(old, new), tmp)

    def failed_steps(self, result) -> set:
        return {s["name"] for s in (result.result or {}).get("steps", []) if not s.get("ok")}

    def test_a_floor_name_on_the_room_name_fails_the_label_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_mutant("const TWOD_ROOM_BASE = 28;", "const TWOD_ROOM_BASE = 15;", Path(tmp))
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertIn("STEP_FAILED", result.codes)
        self.assertEqual(self.failed_steps(result), {"twod-labels"}, describe(result))

    def test_words_cut_in_table_cells_fail_the_table_step(self):
        css_old = "td.tok, th.tok, td code, th code { white-space: nowrap; overflow-wrap: normal; }"
        text = self.page_text()
        self.assertEqual(text.count(css_old), 1)
        broken = text.replace(css_old, "td.tok, th.tok { }").replace("overflow-wrap: break-word; word-break: normal; hyphens: manual;", "overflow-wrap: anywhere;")
        self.assertNotEqual(broken, text)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_tampered(broken, Path(tmp))
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertEqual(self.failed_steps(result), {"table-words"}, describe(result))

    def test_a_qa_tab_without_rows_fails_the_row_step(self):
        text = self.page_text()
        self.assertGreater(text.count("'data-qa-row': '1'"), 1)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_tampered(text.replace("'data-qa-row': '1'", "'data-qa-rox': '1'"), Path(tmp))
        self.assertEqual(result.status, "FAIL", describe(result))
        self.assertEqual(self.failed_steps(result), {"qa-rows"}, describe(result))

    def test_the_label_step_runs_on_the_page_as_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_tampered(self.page_text(), Path(tmp))
        self.assertEqual(result.status, "PASS", describe(result))
        names = [s["name"] for s in result.result["steps"]]
        self.assertEqual(names, list(build.GATE_STEP_NAMES))
        self.assertEqual(result.result["tabs"] and [t["tab"] for t in result.result["tabs"]], list(build.GATE_TABS))

    def test_the_twin_check_covers_twenty_selections_at_every_level(self):
        data = H.extract_data(self.page_text())
        selections = data["calibrationHooks"]["docSelections"]
        self.assertEqual(len(selections), 20)
        self.assertEqual({s["level"] for s in selections}, {"REQ", "BD", "DD"})
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{8}", s["hash32"]) for s in selections))

    def test_the_four_d_tab_shows_no_empty_state_when_runs_are_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed = dump_tab(self.dist / "index.html", "4D", Path(tmp))
        self.assertNotIn("Author the expectation first", sealed)


@NEEDS_BROWSER
class OracleFirstPageTests(OracleProject, WorkDir):
    def setUp(self):
        OracleProject.setUp(self)
        WorkDir.setUp(self)

    def test_a_revised_expectation_shows_the_empty_state_on_4d_and_5d_in_the_browser(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")
        reply = tools.build(self.ws, self.name, gate="smoke")
        self.assertEqual(reply["status"], "ok", reply)
        page = build.dist_dir(self.paths) / "index.html"
        for tab in ("4D", "5D"):
            text = dump_tab(page, tab, self.tmp)
            self.assertIn("Author the expectation first", text, tab)
            self.assertNotIn("CS-BORROW-OK#", text, "no event of the case in the rendered tab")

    def test_a_sealed_desk_does_not_show_the_empty_state(self):
        self.desk()
        reply = tools.build(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        text = dump_tab(build.dist_dir(self.paths) / "index.html", "4D", self.tmp)
        self.assertNotIn("Author the expectation first", text)


@NEEDS_BROWSER
class HostileAndGenericPageTests(WorkDir):
    def render_page(self, model, name="page.html") -> Path:
        rendered = build.render(model)
        self.assertEqual([i for i in rendered.issues if i["severity"] == "ERROR"], [])
        return self.write_page(rendered.html, name)

    def test_hostile_text_in_every_free_text_field_does_not_run_and_does_not_break_the_page(self):
        page = self.render_page(hostile_model(synth.generate("small", 2)))
        for width in (1024, 1600):
            result = gate(page, width, self.tmp)
            self.assertEqual(result.status, "PASS", describe(result))      # an injected script would throw and show up as PAGE_ERRORS

    def test_the_genericity_probe_renders_with_zero_errors_and_a_live_heartbeat(self):             # acceptance (c)
        for seed in (1, 2):
            model = synth.edge_case("big400", seed)
            synth._inject_unknown_values(model)
            model["nodes"][0]["roomId"] = "no-such-room"
            model["cases"], model["interventions"] = [], []
            self.assertEqual(len(model["nodes"]), 400)
            page = self.render_page(model, f"probe-{seed}.html")
            for width in (WIDTHS if seed == 1 else (1366,)):
                result = gate(page, width, self.tmp)
                self.assertEqual(result.status, "PASS", f"seed {seed} width {width}: {describe(result)}")
                self.assertEqual(result.result["innerWidth"], width)
                self.assertTrue(all(t["frames"][2] > t["frames"][1] for t in result.result["tabs"]), "the heartbeat advanced on every tab")
                self.assertEqual(result.result["errors"], [])
                self.assertEqual(result.console, [])

    def test_every_interface_language_renders(self):
        for lang in ("zh-CN", "ja-JP", "en-US"):
            model = synth.generate("small", 3)
            model["meta"]["languages"] = [lang]
            result = gate(self.render_page(model, f"lang-{lang}.html"), 1366, self.tmp)
            self.assertEqual(result.status, "PASS", f"{lang}: {describe(result)}")


class ProbeModelTests(unittest.TestCase):
    def test_the_probe_models_cover_what_the_acceptance_names(self):
        model = synth.edge_case("big400", 1)
        synth._inject_unknown_values(model)
        self.assertEqual(len(model["nodes"]), 400)
        self.assertGreater(len({n["id"].split(".")[0] for n in model["nodes"]}), 3, "several random id prefixes")
        known = {k["id"] for k in PROFILE["kinds"]}
        self.assertTrue(any(n["kind"] not in known for n in model["nodes"]), "unknown node kinds")
        self.assertTrue(any(n.get("props", {}).get("trust") not in {t["id"] for t in PROFILE["trust"]} for n in model["nodes"] if n.get("props", {}).get("trust")) or
                        any(b.get("trust") not in {t["id"] for t in PROFILE["trust"]} for b in model["space"]["buildings"]), "unknown trust values")


@NEEDS_BROWSER
class CalibrationMatrixTests(WorkDir):
    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads((FIXTURES / "MATRIX.json").read_text(encoding="utf-8"))

    def unknown_building_page(self) -> Path:
        model = synth.generate("small", 4)
        model["nodes"][0]["roomId"] = "no-such-room"
        rendered = build.render(model)
        self.assertGreaterEqual(rendered.bundle["calibrationHooks"]["scene"]["unassignedCount"], 1)
        return self.write_page(rendered.html, "unknown-building.html")

    def test_the_fixtures_fail_and_pass_exactly_as_the_design_says(self):
        seen = []
        for fixture in self.matrix["fixtures"]:
            if fixture.get("pending"):
                continue
            if fixture.get("built") == "unknown-building":
                page, inline = self.unknown_building_page(), False
            else:
                page, inline = FIXTURES / fixture["file"], True
            for width, want in fixture["expect"].items():
                result = gate(page, int(width), self.tmp, inline_selftest=inline)
                seen.append((fixture["name"], int(width), result.status, result.codes))
                self.assertEqual((result.status, result.codes), (want["status"], sorted(want["codes"])), f"{fixture['name']} @ {width}: {describe(result)}")
        self.assertEqual({name for name, *_ in seen}, {"bad-header-overlap", "frozen-scene", "missing-result", "unknown-building", "clipped-scene", "sparse-scene"})
        print("\n[calibration matrix] " + "; ".join(f"{n}@{w}={s}{c or ''}" for n, w, s, c in seen))

    def test_unknown_building_keeps_the_heartbeat_and_the_unassigned_region(self):
        result = gate(self.unknown_building_page(), 1366, self.tmp)
        self.assertEqual(result.status, "PASS", describe(result))
        self.assertTrue(all(t["frames"][2] > t["frames"][1] for t in result.result["tabs"]))


class MatrixFileTests(unittest.TestCase):
    def test_the_matrix_names_the_six_fixtures_of_the_design_and_every_file_exists(self):
        matrix = json.loads((FIXTURES / "MATRIX.json").read_text(encoding="utf-8"))
        self.assertEqual([f["name"] for f in matrix["fixtures"]],
                         ["bad-header-overlap", "frozen-scene", "missing-result", "unknown-building", "clipped-scene", "sparse-scene"])
        for fixture in matrix["fixtures"]:
            if "file" in fixture:
                folder = FIXTURES
                raw = (folder / fixture["file"]).read_bytes()
                self.assertTrue(raw.isascii())
                self.assertNotIn(b"\r", raw)
                self.assertEqual(raw.count(smoke.SELFTEST_MARKER.encode()), 1)
            for width, want in fixture["expect"].items():
                self.assertTrue(width.isdigit())
                self.assertIn(want["status"], ("PASS", "FAIL"))
        first = matrix["fixtures"][0]["expect"]
        self.assertEqual((first["1256"]["status"], first["1366"]["status"], first["1576"]["status"]), ("FAIL", "FAIL", "PASS"))

    def test_the_scene_fixtures_stay_out_of_the_directory_that_the_other_gate_tests_enumerate(self):
        self.assertTrue((FIXTURES / "scene" / "clipped-scene.html").is_file())
        self.assertFalse((FIXTURES / "clipped-scene.html").exists())


class SkipAndFailClosedTests(OracleProject):
    """No browser, no verdict: a SKIP is loud and never a pass; a runner that crashes fails the gate."""

    def test_verify_gate_without_a_browser_is_skip_for_every_width(self):
        with mock.patch.object(smoke, "find_browser", return_value=None):
            verdict = smoke.verify_gate(Path("nowhere.html"), WIDTHS)
        self.assertEqual(verdict["status"], "SKIP")
        self.assertEqual([w["status"] for w in verdict["widths"]], ["SKIP"] * 3)
        self.assertTrue(all("NOT run" in line and "not a pass" in line for line in verdict["lines"]))
        self.assertIn("skipped, not passed", verdict["message"].lower())
        self.assertIn("no edge or chrome", verdict["message"].lower())

    def test_tools_report_the_skip_loudly_and_never_as_ok(self):
        self.desk()
        with mock.patch.object(smoke, "find_browser", return_value=None):
            built = tools.build(self.ws, self.name, gate="smoke")
            self.assertEqual(built["status"], "ok", "the missing browser does not undo the build")
            self.assertEqual(built["gate"], "SKIP")
            self.assertIn("NOT verified", " ".join(built["next"]))
            verified = tools.verify(self.ws, self.name)
            released = tools.build(self.ws, self.name, release=True, gate="smoke")
        self.assertEqual((verified["status"], verified["code"]), ("invalid", build.E_GATE_SKIP))
        self.assertIn("SKIPPED, NOT PASSED", verified["message"])
        self.assertEqual((released["status"], released["code"]), ("invalid", build.E_RELEASE))
        self.assertTrue(any("SKIP" in b for b in released["blockers"]))

    FAILED = {"status": "FAIL", "browser": None, "message": "", "lines": ["FAIL 1024: GEMARC_GATE_013 HEARTBEAT"],
              "widths": [{"width": 1024, "status": "FAIL", "codes": ["HEARTBEAT"], "tabs": ["1D"], "steps": {"ok": 0, "total": 4}, "seconds": 0.1,
                          "findings": [{"code": "GEMARC_GATE_013", "name": "HEARTBEAT", "detail": "frame counter did not advance", "tab": "2D"}]}]}

    def test_a_failing_gate_makes_build_and_verify_invalid_but_the_page_stays(self):
        self.desk()
        with mock.patch.object(smoke, "verify_gate", return_value=self.FAILED):
            built = tools.build(self.ws, self.name, gate="smoke")
            self.assertEqual((built["status"], built["code"]), ("invalid", build.E_GATE_FAIL), built)
            self.assertEqual(built["gate"], "FAIL")
            self.assertTrue((build.dist_dir(self.paths) / "index.html").is_file())
            verified = tools.verify(self.ws, self.name)
        self.assertEqual((verified["status"], verified["code"]), ("invalid", build.E_GATE_FAIL), verified)
        self.assertIn("HEARTBEAT [2D]", verified["findings"][0])
        self.assertLessEqual(tools.reply_bytes(verified), tools.VERIFY_CAP)
        report = json.loads((build.dist_dir(self.paths) / ".verify" / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "FAIL")
        with mock.patch.object(smoke, "verify_gate", return_value=self.FAILED):
            released = tools.build(self.ws, self.name, release=True, gate="smoke")
        self.assertEqual(released["code"], build.E_RELEASE, "a release build reports its own code first")

    def test_a_page_with_fewer_scripted_steps_than_the_viewer_defines_fails_closed(self):
        verdict = {"status": "PASS", "widths": [{"width": w, "status": "PASS", "codes": [], "findings": [], "tabs": list(build.GATE_TABS), "steps": {"ok": 4, "total": 4},
                                                 "stepNames": list(build.GATE_STEP_NAMES[:4]), "message": ""} for w in WIDTHS],
                   "lines": [f"PASS {w}: 6 tabs, 4/4 steps, 1 s" for w in WIDTHS], "browser": None, "message": ""}
        out = build.require_steps(verdict)
        self.assertEqual(out["status"], "FAIL")
        self.assertTrue(all("STEPS_MISSING" in r["codes"] and r["status"] == "FAIL" for r in out["widths"]))
        self.assertTrue(all(line.startswith("FAIL") for line in out["lines"]))
        self.assertEqual(build.require_steps(self.good_verdict())["status"], "PASS")

    @staticmethod
    def good_verdict(tabs=None, names=None, widths=(1024,)):
        tabs = list(build.GATE_TABS if tabs is None else tabs)
        names = list(build.GATE_STEP_NAMES if names is None else names)
        rows = [{"width": w, "status": "PASS", "codes": [], "findings": [], "tabs": tabs, "steps": {"ok": len(names), "total": len(names)}, "stepNames": names, "message": ""} for w in widths]
        return {"status": "PASS", "widths": rows, "lines": [f"PASS {w}" for w in widths], "browser": None, "message": ""}

    def test_a_page_that_lost_a_tab_or_a_step_name_fails_closed_even_with_enough_steps(self):
        for what, verdict, code in (
            ("no QA tab", self.good_verdict(tabs=[t for t in build.GATE_TABS if t != "QA"]), "TABS_MISSING"),
            ("no 2D tab", self.good_verdict(tabs=[t for t in build.GATE_TABS if t != "2D"]), "TABS_MISSING"),
            ("tabs in another order", self.good_verdict(tabs=list(reversed(build.GATE_TABS))), "TABS_MISSING"),
            ("a step renamed, the count kept", self.good_verdict(names=["x" if n == "qa-rows" else n for n in build.GATE_STEP_NAMES]), "STEPS_MISSING"),
            ("no step names in the result", {**self.good_verdict(), "widths": [{k: v for k, v in self.good_verdict()["widths"][0].items() if k != "stepNames"}]}, "STEPS_MISSING"),
        ):
            out = build.require_steps(verdict)
            self.assertEqual(out["status"], "FAIL", what)
            self.assertIn(code, out["widths"][0]["codes"], what)
            self.assertTrue(out["lines"][0].startswith("FAIL"), what)
        extra = self.good_verdict(names=list(build.GATE_STEP_NAMES) + ["more"])
        self.assertEqual(build.require_steps(extra)["status"], "PASS", "an extra step is allowed")

    def test_a_crashing_runner_is_a_failed_gate_not_a_pass(self):
        with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("x", "edge", "1")), \
                mock.patch.object(smoke, "run_gate", side_effect=RuntimeError("boom")):
            verdict = smoke.verify_gate(Path("page.html"), (1024,))
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["widths"][0]["codes"], ["PROCESS"])

    def test_a_missing_page_is_a_failed_gate(self):
        verdict = smoke.verify_gate(Path("definitely-missing.html"), (1024,), browser=smoke.Browser("x", "edge", "1"))
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["widths"][0]["codes"], ["NO_RESULT"])

    def test_one_failing_width_fails_the_whole_verdict(self):
        results = iter([smoke.GateResult("PASS", "p", 1024), smoke.GateResult("FAIL", "p", 1366, findings=[smoke.Finding("HEARTBEAT", "x")]),
                        smoke.GateResult("PASS", "p", 1600)])
        with mock.patch.object(smoke, "run_gate", side_effect=lambda *a, **k: next(results)):
            verdict = smoke.verify_gate(Path("p.html"), WIDTHS, browser=smoke.Browser("x", "edge", "1"))
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual([w["status"] for w in verdict["widths"]], ["PASS", "FAIL", "PASS"])
        self.assertIn("GEMARC_GATE_013 HEARTBEAT", verdict["lines"][1])

    def test_verify_needs_at_least_one_width(self):
        with self.assertRaises(ValueError):
            smoke.verify_gate(Path("p.html"), (), browser=smoke.Browser("x", "edge", "1"))


if __name__ == "__main__":
    unittest.main()
