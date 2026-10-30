"""Fixes of the gate on built pages (fix-view-b3): the tab and step names the verdict must carry, the text scan that lets a model say
"undefined", a timeout that is not a verdict on the page, nested controls, a step that returns a promise.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate_html_fixes*.py"

The browser tests SKIP loudly without Edge or Chrome (a skipped gate is never a pass); the verdict-shape tests need no browser.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build
from gemarc_mcp.atlas.gate import smoke

from . import synth
from .test_gate_smoke import dom_for, good_result

BROWSER = smoke.find_browser()
NEEDS_BROWSER = unittest.skipUnless(BROWSER, "NO BROWSER (Edge or Chrome) FOUND: the browser gate was NOT run (a skip is not a pass)")


def setUpModule():
    if BROWSER is None:
        print("\n" + "=" * 100 + "\nSKIPPED, NOT PASSED: no Edge or Chrome found; the real-browser tests of test_gate_html_fixes.py did not run.\n" + "=" * 100, file=sys.stderr)


def page_of(body: str, script: str) -> str:
    return ('<!doctype html><html><head><meta charset="utf-8"><title>t</title>' + smoke.SELFTEST_MARKER + "</head><body>"
            '<header data-gate="header"><button type="button">A</button><button type="button">B</button></header>' + body + "<script>" + script + "</script></body></html>")


RUN = "var f=0;AtlasGate.run(%s);"


class BrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="atlas-gate-fix-")
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_text(self, name: str, text: str, width: int = 1366, **kw):
        page = self.tmp / name
        smoke.write_text_lf(page, text)
        return smoke.run_gate(page, width, browser=BROWSER, tmp_root=self.tmp, retries=1, inline_selftest=True, **kw)


@NEEDS_BROWSER
class WalkerFixTests(BrowserCase):
    HOOKS = "{tabs:['m'],renderOnce:function(){f++;},frame:function(){return f;}%s}"

    def test_a_control_that_contains_another_control_is_not_an_overlap(self):
        body = '<div data-gate="header" style="padding:4px"><div role="tab" tabindex="0" style="display:inline-block;padding:10px">Tab A <button type="button">x</button></div></div>'
        res = self.run_text("nested.html", page_of(body, RUN % (self.HOOKS % "")))
        self.assertEqual((res.status, res.codes), ("PASS", []), res.message)

    def test_two_separate_controls_that_collide_still_fail(self):
        body = '<div data-gate="header"><button type="button" style="position:absolute;left:10px;top:60px;width:90px;height:30px">P</button><button type="button" style="position:absolute;left:40px;top:60px;width:90px;height:30px">Q</button></div>'
        res = self.run_text("collide.html", page_of(body, RUN % (self.HOOKS % "")))
        self.assertEqual(res.codes, ["HEADER_OVERLAP"], res.message)

    def test_a_step_that_returns_a_promise_is_a_failed_step(self):
        hooks = self.HOOKS % ",steps:[{name:'late',run:function(){return Promise.resolve('bad');}},{name:'fine',run:function(){return true;}}]"
        res = self.run_text("promise.html", page_of("", RUN % hooks))
        self.assertEqual(res.codes, ["STEP_FAILED"], res.message)
        steps = {s["name"]: s for s in res.result["steps"]}
        self.assertFalse(steps["late"]["ok"])
        self.assertIn("promise", steps["late"]["msg"])
        self.assertTrue(steps["fine"]["ok"])

    def test_the_text_scan_exempts_only_what_the_page_lists_and_the_flag_turns_it_off(self):
        body = "<main><p>The call returns undefined when the queue is empty.</p><p>Total: undefined</p></main>"
        hooks = self.HOOKS % ",textAllow:function(){return ['The call returns undefined when the queue'];}"
        res = self.run_text("allow.html", page_of(body, RUN % hooks))
        self.assertEqual(res.codes, ["TEXT_BAD"], res.message)
        self.assertEqual(len(res.result["tabs"][0]["textBad"]), 1, res.result["tabs"][0]["textBad"])
        self.assertIn("Total", res.result["tabs"][0]["textBad"][0])
        only_prose = page_of("<main><p>The call returns undefined when the queue is empty.</p></main>", RUN % hooks)
        self.assertEqual(self.run_text("allow2.html", only_prose).codes, [])
        self.assertEqual(self.run_text("noallow.html", page_of(body, RUN % (self.HOOKS % ""))).codes, ["TEXT_BAD"], "without the hook nothing is exempt")
        off = self.run_text("off.html", page_of(body, RUN % (self.HOOKS % "")), scan_text=False)
        self.assertEqual((off.status, off.codes), ("PASS", []), off.message)

    def test_markup_around_a_listed_word_does_not_break_the_exemption(self):
        body = "<main><p>The call returns <b>undefined</b> when empty.</p></main>"
        hooks = self.HOOKS % ",textAllow:function(){return ['The call returns **undefined** when empty.'];}"
        self.assertEqual(self.run_text("md.html", page_of(body, RUN % hooks)).codes, [])


class VerdictShapeTests(unittest.TestCase):
    """No browser: the timeout classification, the cumulative seconds and the INCONCLUSIVE status."""

    def run_with(self, launches, **kw):
        calls = iter(launches)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            seen = []

            def launcher(args, timeout, env=None):
                seen.append(timeout)
                return next(calls)

            with mock.patch.object(smoke, "_launch", launcher):
                return smoke.run_gate(page, 1366, browser=smoke.Browser("edge", "edge", "1"), tmp_root=tmp, **kw), seen

    def test_a_timeout_is_final_when_asked_and_the_seconds_add_up(self):
        res, seen = self.run_with([smoke._Launch(None, b"", b"", True, 31.0), smoke._Launch(None, b"", b"", True, 33.0)], retries=1, retry_on_timeout=False)
        self.assertEqual((res.attempts, res.codes), (1, ["PROCESS"]))
        res, seen = self.run_with([smoke._Launch(None, b"", b"", True, 31.0), smoke._Launch(None, b"", b"", True, 33.0)], retries=1)
        self.assertEqual(res.attempts, 2)
        self.assertEqual((res.seconds, round(res.total_seconds, 1)), (33.0, 64.0), "seconds is the last attempt, total_seconds is what was waited")
        self.assertEqual(res.as_dict()["totalSeconds"], 64.0)

    def test_only_timeouts_make_a_width_inconclusive_and_never_a_pass(self):
        res, _ = self.run_with([smoke._Launch(None, b"", b"", True, 91.0)], retries=0)
        self.assertTrue(smoke.is_inconclusive(res))
        row = smoke.summarize_result(res)
        self.assertEqual((row["status"], row["codes"]), ("INCONCLUSIVE", ["PROCESS"]))
        self.assertIn("not a page defect", row["message"])
        crash, _ = self.run_with([smoke._Launch(1, b"", b"", False, 1.0)], retries=0)
        self.assertFalse(smoke.is_inconclusive(crash))
        self.assertEqual(smoke.summarize_result(crash)["status"], "FAIL")
        mixed, _ = self.run_with([smoke._Launch(None, b"", b"", True, 30.0), smoke._Launch(1, b"", b"", False, 1.0)], retries=1)
        self.assertFalse(smoke.is_inconclusive(mixed), "a crash among the failures is a failure")

    def test_the_verdict_status_ranks_fail_above_inconclusive_above_pass(self):
        def verdict(*results):
            seq = iter(results)
            with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("e", "edge", "1")), \
                    mock.patch.object(smoke, "run_gate", side_effect=lambda *a, **k: next(seq)):
                return smoke.verify_gate(Path("p.html"), tuple(1000 + 100 * i for i in range(len(results))))

        ok = smoke.GateResult("PASS", "p", 1000)
        slow = smoke.GateResult("FAIL", "p", 1100, process_failures=["timeout after 90.0s (process tree killed)"], findings=[smoke.Finding("PROCESS", "x")])
        bad = smoke.GateResult("FAIL", "p", 1200, findings=[smoke.Finding("HEARTBEAT", "x")])
        self.assertEqual(verdict(ok, slow)["status"], "INCONCLUSIVE")
        self.assertEqual(verdict(ok, slow, bad)["status"], "FAIL")
        self.assertEqual(verdict(ok, ok)["status"], "PASS")
        self.assertTrue(verdict(ok, slow)["lines"][1].startswith("INCONCLUSIVE"))

    def test_verify_gate_defaults_to_a_long_wall_limit_without_retrying_a_timeout(self):
        captured = {}
        with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("e", "edge", "1")), \
                mock.patch.object(smoke, "run_gate", side_effect=lambda page, w, **k: captured.update(k) or smoke.GateResult("PASS", "p", w)):
            smoke.verify_gate(Path("p.html"), (1366,))
        self.assertEqual((captured["timeout"], captured["retry_on_timeout"]), (smoke.VERIFY_TIMEOUT_SECONDS, False))
        self.assertGreater(smoke.VERIFY_TIMEOUT_SECONDS, 30)
        captured.clear()
        with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("e", "edge", "1")), \
                mock.patch.object(smoke, "run_gate", side_effect=lambda page, w, **k: captured.update(k) or smoke.GateResult("PASS", "p", w)):
            smoke.verify_gate(Path("p.html"), (1366,), timeout=12)
        self.assertEqual(captured["timeout"], 12)

    def test_the_default_run_gate_limit_is_unchanged(self):
        self.assertEqual(smoke.load_calibration()["limits"]["timeoutSeconds"], 30)

    def test_summarize_names_the_steps_it_saw(self):
        res = smoke.GateResult("PASS", "p", 1366, result=good_result())
        row = smoke.summarize_result(res)
        self.assertEqual(row["stepNames"], [s["name"] for s in good_result()["steps"]])
        self.assertEqual(dom_for(good_result()).count(b"atlas-gate") >= 1, True)


class BuiltPageTabTests(unittest.TestCase):
    """The page as built keeps its six tabs and its eleven step names in the runtime (what ``require_steps`` compares a verdict with)."""

    def test_the_names_are_in_the_bundled_runtime(self):
        from gemarc_mcp.atlas import htmlassemble as H

        runtime = H.load_viewer().runtime
        for name in build.GATE_STEP_NAMES:
            self.assertIn(f"name: '{name}'", runtime, name)
        for tab in build.GATE_TABS:
            self.assertIn(f"id: '{tab}'", runtime, tab)
        self.assertEqual(build.GATE_STEPS, len(build.GATE_STEP_NAMES))


@NEEDS_BROWSER
class DroppedTabMutantTests(BrowserCase):
    """A page that lost a tab (and the step that went with it) used to pass; the verdict now names what is missing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        model = synth.generate("small", 3)
        cls.html = build.render(model).html

    def verdict(self, text: str, name: str):
        page = self.tmp / name
        smoke.write_text_lf(page, text)
        return build.require_steps(smoke.verify_gate(page, (1366,), tmp_root=self.tmp))

    def test_the_unmodified_page_passes_with_six_tabs_and_eleven_steps(self):
        v = self.verdict(self.html, "whole.html")
        self.assertEqual(v["status"], "PASS", v["lines"])
        row = v["widths"][0]
        self.assertEqual((row["tabs"], row["steps"]["total"]), (list(build.GATE_TABS), 11))

    def test_a_page_without_the_qa_tab_fails_closed(self):
        old = "STAGE_TAB_5D, QA_TAB]"
        self.assertEqual(self.html.count(old), 1)
        v = self.verdict(self.html.replace(old, "STAGE_TAB_5D]"), "no-qa.html")
        self.assertEqual(v["status"], "FAIL", v["lines"])
        self.assertTrue(any(c in v["widths"][0]["codes"] for c in ("TABS_MISSING", "STEP_FAILED")), v["widths"][0]["codes"])

    def test_a_page_without_the_2d_tab_fails_closed(self):
        old = "DOC_TAB, TWOD_TAB, STAGE"
        self.assertEqual(self.html.count(old), 1)
        v = self.verdict(self.html.replace(old, "DOC_TAB, STAGE"), "no-2d.html")
        self.assertEqual(v["status"], "FAIL", v["lines"])
        self.assertTrue(any(c in v["widths"][0]["codes"] for c in ("TABS_MISSING", "STEP_FAILED")), v["widths"][0]["codes"])


@NEEDS_BROWSER
class ModelProseTests(BrowserCase):
    """A model may say undefined, NaN or [object in its own words; the viewer's own faults are still found."""

    PROSE = " returns undefined when empty (NaN inputs rejected, no [object Object])"

    def page(self, name: str, mutate=None) -> Path:
        model = synth.generate("small", 2)
        model["nodes"][0]["name"] += self.PROSE
        text = build.render(model).html
        if mutate:
            text = mutate(text)
        path = self.tmp / name
        smoke.write_text_lf(path, text)
        return path

    def test_a_node_name_that_says_undefined_passes_at_three_widths(self):
        verdict = build.require_steps(smoke.verify_gate(self.page("prose.html"), (1024, 1366, 1600), tmp_root=self.tmp))
        self.assertEqual(verdict["status"], "PASS", verdict["lines"])

    def test_the_interface_saying_undefined_still_fails_with_the_same_prose_in_the_model(self):
        def damage(text):
            old = '"hdr.search":"Search"'
            self.assertEqual(text.count(old), 1)
            return text.replace(old, '"hdr.search":"Search NaN"')

        res = smoke.run_gate(self.page("chrome-nan.html", damage), 1366, browser=BROWSER, tmp_root=self.tmp)
        self.assertEqual(res.codes, ["TEXT_BAD"], res.message)


class BuildGuardTests(unittest.TestCase):
    def test_a_model_that_cannot_be_encoded_is_refused_by_render_not_by_the_encoder(self):
        for value, code in (("bad \ud800 name", "GEMARC_ATLAS_MODEL_012"),):
            model = synth.generate("small", 3)
            model["nodes"][0]["name"] = value
            with self.assertRaises(build.BuildRefused) as caught:
                build.render(model)
            self.assertEqual(caught.exception.code, build.E_CONTRACT)
            self.assertIn(code, caught.exception.message)
        model = synth.generate("small", 3)
        model["nodes"][0]["props"] = {"x": float("nan")}
        with self.assertRaises(build.BuildRefused) as caught:
            build.render(model)
        self.assertIn("GEMARC_ATLAS_MODEL_013", caught.exception.message)

    def test_every_embedded_diagram_unit_covers_each_id_once(self):
        # the building overview of the diagram builder lists a cross-building edge once per building; the page embeds each id once
        unit = {"id": "component-overview", "type": "component", "title": "t", "covers": {"nodeIds": [], "edgeIds": ["e1", "e2", "e1", "e2", "e1"], "caseIds": []},
                "data": {"x": 1}, "evidenceIds": ["v"], "mermaid": "flowchart LR"}
        out = build.compact_unit(unit)
        self.assertEqual(out["covers"]["edgeIds"], ["e1", "e2"])
        self.assertNotIn("data", out)
        self.assertNotIn("evidenceIds", out)
        self.assertEqual(unit["covers"]["edgeIds"], ["e1", "e2", "e1", "e2", "e1"], "the input is not changed")
        model = synth.generate("reference", 3)
        for page_unit in build.make_bundle(model)["diagrams"]:
            for key, ids in page_unit["covers"].items():
                self.assertEqual(len(ids), len(set(ids)), f"{page_unit['id']} covers.{key} repeats an id")

    def test_a_model_with_other_contract_issues_still_renders(self):
        model = synth.generate("small", 3)
        model["nodes"][0]["kind"] = "NoSuchKind"
        self.assertTrue(build.render(model).html.startswith("<!doctype html>") or build.render(model).html.lstrip().lower().startswith("<!doctype"))


if __name__ == "__main__":
    unittest.main()
