"""Browser gate tests.

Most tests need no browser: result parsing, fail-closed behaviour, retry policy, console scraping,
argument construction and the no-browser SKIP are exercised with canned data and a patched launcher.
The integration tests at the bottom launch the real Edge or Chrome and SKIP, loudly, when none exists
(a skipped gate is never a passing gate).

Run only these:
    PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate*.py"
"""
from __future__ import annotations

import html
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock

from gemarc_mcp.atlas.gate import smoke

FIXTURES = pathlib.Path(__file__).resolve().parent / "gate_fixtures"
GATE_DIR = pathlib.Path(smoke.__file__).resolve().parent
CAL = smoke.load_calibration()
EDGE = smoke.Browser(r"C:\fake\msedge.exe", "edge", "0")
CHROME = smoke.Browser(r"C:\fake\chrome.exe", "chrome", "0")

def load_spike(name):
    """Import tests/atlas/gate_spike.py by path (it is a CLI script, not part of a package)."""
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name("gate_spike.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses with postponed annotations look the module up
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


NONCE_PLACEHOLDER = "__NONCE__"  # run_with() swaps in the nonce the runner really put in the URL


def good_canvas(**over):
    canvas = {
        "el": "canvas:scene",
        "declared": False,
        "visible": True,
        "isCanvas": True,
        "kind": "webgl",
        "width": 320,
        "height": 200,
        "nonBgRatio": 0.125,
        "renderer": "ANGLE (Google, Vulkan, SwiftShader Device)",
        "glErrors": [],
        "contextLost": False,
    }
    canvas.update(over)
    return canvas


def good_tab(**over):
    tab = {
        "tab": "main",
        "threw": None,
        "frames": [0, 1, 2],
        "frameReads": [0, 0],
        "header": {"roots": 1, "n": 2, "maxOverlapPx": 0, "pair": None, "outside": []},
        "scrollWidth": 1366,
        "clientWidth": 1366,
        "fit": {"n": 0, "clipped": []},
        "textBad": [],
        "draws": 3,
        "canvases": [good_canvas()],
        "render": {"nonBgRatio": 0.125, "renderer": "page-reported, never used"},
    }
    tab.update(over)
    return tab


def good_result(width=1366, **over):
    result = {
        "v": 2,
        "nonce": NONCE_PLACEHOLDER,
        "innerWidth": width,
        "innerHeight": 708,
        "errors": [],
        "missingHooks": [],
        "steps": [],
        "tabStateChecked": False,
        "tabs": [good_tab(scrollWidth=width, clientWidth=width)],
    }
    result.update(over)
    return result


def nonce_of(args):
    """The per-launch nonce the runner appended to the page URL (last argument)."""
    match = re.search(r"[?&]gn=([0-9a-f]{16})$", args[-1])
    assert match, args[-1]
    return match.group(1)


def with_real_nonce(launch, args):
    """Replace the placeholder in a canned launch with the nonce of the URL this launch was given."""
    return smoke._Launch(
        launch.returncode,
        launch.stdout.replace(NONCE_PLACEHOLDER.encode(), nonce_of(args).encode()),
        launch.stderr,
        launch.timed_out,
        launch.seconds,
        launch.spawn_seconds,
        launch.error,
    )


def dom_for(result, attrs='id="atlas-gate" data-done="1"', tag="pre"):
    body = html.escape(json.dumps(result), quote=False)
    return f"<!DOCTYPE html><html><head><title>t</title></head><body><{tag} {attrs}>{body}</{tag}></body></html>".encode()


def names(findings):
    return sorted({f.name for f in findings})


class ExtractResultTests(unittest.TestCase):
    def test_valid_result_roundtrips(self):
        result, bad = smoke.extract_result(dom_for(good_result()))
        self.assertIsNone(bad)
        self.assertEqual(result["innerWidth"], 1366)

    def test_html_escaped_text_is_unescaped_before_json(self):
        payload = good_result(errors=["boom <b> & [object Object]"])
        dom = dom_for(payload)
        self.assertIn(b"&lt;b&gt; &amp;", dom)  # the DOM dumper escapes text nodes
        result, bad = smoke.extract_result(dom)
        self.assertIsNone(bad)
        self.assertEqual(result["errors"], ["boom <b> & [object Object]"])

    def test_attribute_order_and_quoting_do_not_matter(self):
        dom = dom_for(good_result(), attrs="data-done='1' class=x id=atlas-gate")
        result, bad = smoke.extract_result(dom)
        self.assertIsNone(bad)
        self.assertIsNotNone(result)

    def test_missing_element_is_no_result(self):
        result, bad = smoke.extract_result(b"<html><body><p>hello</p></body></html>")
        self.assertIsNone(result)
        self.assertEqual(bad.name, "NO_RESULT")

    def test_empty_dump_is_no_result(self):
        result, bad = smoke.extract_result(b"")
        self.assertIsNone(result)
        self.assertEqual(bad.name, "NO_RESULT")

    def test_two_elements_is_bad_result(self):
        one = dom_for(good_result()).decode()
        two = one.replace("</body>", one[one.index("<pre"): one.index("</pre>") + 6] + "</body>")
        result, bad = smoke.extract_result(two.encode())
        self.assertIsNone(result)
        self.assertEqual(bad.name, "BAD_RESULT")
        self.assertIn("2 elements", bad.detail)

    def test_missing_done_flag_is_bad_result(self):
        result, bad = smoke.extract_result(dom_for(good_result(), attrs='id="atlas-gate"'))
        self.assertIsNone(result)
        self.assertEqual(bad.name, "BAD_RESULT")
        self.assertIn("data-done", bad.detail)

    def test_wrong_done_value_is_bad_result(self):
        result, bad = smoke.extract_result(dom_for(good_result(), attrs='id="atlas-gate" data-done="0"'))
        self.assertEqual(bad.name, "BAD_RESULT")

    def test_garbled_json_is_bad_result(self):
        dom = b'<html><body><pre id="atlas-gate" data-done="1">{"v":1,"tabs":[</pre></body></html>'
        result, bad = smoke.extract_result(dom)
        self.assertIsNone(result)
        self.assertEqual(bad.name, "BAD_RESULT")
        self.assertIn("not valid JSON", bad.detail)

    def test_json_that_is_not_an_object_is_bad_result(self):
        result, bad = smoke.extract_result(b'<html><body><pre id="atlas-gate" data-done="1">[1,2]</pre></body></html>')
        self.assertEqual(bad.name, "BAD_RESULT")

    def test_wrong_schema_version_is_bad_result(self):
        for version in (1, 3, None):  # v1 is the old self-test: it has no nonce, clientWidth or canvas reads
            result, bad = smoke.extract_result(dom_for({"v": version, "tabs": []}))
            self.assertIsNone(result, version)
            self.assertEqual(bad.name, "BAD_RESULT", version)
            self.assertIn("schema", bad.detail)

    def test_late_errors_attribute_is_merged_and_the_body_cannot_forge_it(self):
        late = html.escape(json.dumps({"count": 2, "errors": ["console.error: x"]}), quote=True)
        dom = dom_for(good_result(lateErrors={"count": 0, "errors": []}), attrs=f'id="atlas-gate" data-done="1" data-late-errors="{late}"')
        result, bad = smoke.extract_result(dom)
        self.assertIsNone(bad)
        self.assertEqual(result["lateErrors"], {"count": 2, "errors": ["console.error: x"]})
        plain, bad = smoke.extract_result(dom_for(good_result(lateErrors={"count": 5, "errors": ["forged"]})))
        self.assertIsNone(bad)
        self.assertNotIn("lateErrors", plain, "a same-named key in the JSON body must not be trusted")

    def test_garbled_late_errors_attribute_is_bad_result(self):
        for raw in ("{oops", '"a string"', '{"count": "2", "errors": []}', '{"count": 1}', '{"count": true, "errors": []}'):
            dom = dom_for(good_result(), attrs=f"id=\"atlas-gate\" data-done=\"1\" data-late-errors='{raw}'")
            result, bad = smoke.extract_result(dom)
            self.assertIsNone(result, raw)
            self.assertEqual(bad.name, "BAD_RESULT", raw)

    def test_not_utf8_is_bad_result(self):
        dom = b'<html><body><pre id="atlas-gate" data-done="1">{"v":1,"errors":["\xff\xfe"]}</pre></body></html>'
        result, bad = smoke.extract_result(dom)
        self.assertIsNone(result)
        self.assertEqual(bad.name, "BAD_RESULT")
        self.assertIn("UTF-8", bad.detail)

    def test_result_on_the_wrong_tag_is_bad_result(self):
        result, bad = smoke.extract_result(dom_for(good_result(), tag="div"))
        self.assertEqual(bad.name, "BAD_RESULT")

    def test_text_that_only_mentions_the_id_is_not_a_result(self):
        dom = b'<html><body><p>id="atlas-gate" data-done="1"</p><script>var s=\'<pre id="atlas-gate" data-done="1">{}</pre>\';</script></body></html>'
        result, bad = smoke.extract_result(dom)
        self.assertEqual(bad.name, "NO_RESULT")


class EvaluateTests(unittest.TestCase):
    def check(self, result, width=1366, **kwargs):
        return names(smoke.evaluate(result, width, CAL, **kwargs))

    def test_clean_result_has_no_findings(self):
        self.assertEqual(self.check(good_result()), [])

    def test_inner_width_mismatch_fails(self):
        self.assertEqual(self.check(good_result(1342), width=1366), ["WIDTH"])

    def test_inner_width_missing_fails(self):
        result = good_result()
        del result["innerWidth"]
        self.assertIn("WIDTH", self.check(result))

    def test_in_page_errors_fail(self):
        self.assertEqual(self.check(good_result(errors=["console.error: x"])), ["PAGE_ERRORS"])

    def test_missing_errors_list_is_bad_result(self):
        result = good_result()
        del result["errors"]
        self.assertEqual(self.check(result), ["BAD_RESULT"])

    def test_stderr_console_errors_fail(self):
        findings = smoke.evaluate(good_result(), 1366, CAL, console_errors=[{"level": "INFO", "message": "Uncaught X"}])
        self.assertEqual(names(findings), ["CONSOLE_STDERR"])

    def test_no_tabs_fails(self):
        self.assertEqual(self.check(good_result(tabs=[])), ["NO_TABS"])

    def test_missing_hooks_fail(self):
        self.assertIn("RENDER", self.check(good_result(missingHooks=["frame"])))

    def test_header_overlap_boundary_is_two_pixels(self):
        edge = good_tab(header={"roots": 1, "n": 2, "maxOverlapPx": 2, "pair": None, "outside": []})
        over = good_tab(header={"roots": 1, "n": 2, "maxOverlapPx": 2.01, "pair": ["a", "b"], "outside": []})
        self.assertEqual(self.check(good_result(tabs=[edge])), [])
        self.assertEqual(self.check(good_result(tabs=[over])), ["HEADER_OVERLAP"])

    def test_header_outside_viewport_fails(self):
        tab = good_tab(header={"roots": 1, "n": 1, "maxOverlapPx": 0, "pair": None, "outside": ["button:x"]})
        self.assertEqual(self.check(good_result(tabs=[tab])), ["HEADER_OUTSIDE"])

    def test_scroll_width_must_not_exceed_client_width(self):
        self.assertEqual(self.check(good_result(tabs=[good_tab(scrollWidth=1366, clientWidth=1366)])), [])
        self.assertEqual(self.check(good_result(tabs=[good_tab(scrollWidth=1367, clientWidth=1366)])), ["OVERFLOW_X"])

    def test_overflow_is_judged_against_the_width_without_the_scrollbar(self):
        # classic 15 px vertical scrollbar: clientWidth 1351, innerWidth 1366. The old check compared with
        # innerWidth and let up to 15 px of horizontal overflow through (including width:100vw).
        clean = good_tab(scrollWidth=1351, clientWidth=1351)
        self.assertEqual(self.check(good_result(tabs=[clean])), [])
        for scroll in (1352, 1359, 1366):
            tab = good_tab(scrollWidth=scroll, clientWidth=1351)
            self.assertEqual(self.check(good_result(tabs=[tab])), ["OVERFLOW_X"], scroll)

    def test_overflow_check_fails_closed_without_client_width(self):
        tab = good_tab()
        del tab["clientWidth"]
        self.assertEqual(self.check(good_result(tabs=[tab])), ["BAD_RESULT"])

    def test_header_must_exist_on_every_tab(self):
        for header in (
            {"roots": 0, "n": 0, "maxOverlapPx": 0, "pair": None, "outside": []},  # nothing marked
            {"roots": 1, "n": 0, "maxOverlapPx": 0, "pair": None, "outside": []},  # marked but empty
        ):
            tab = good_tab(header=header)
            self.assertEqual(self.check(good_result(tabs=[tab])), ["HEADER_MISSING"], header)
        second = good_result(tabs=[good_tab(), good_tab(tab="b", header={"roots": 0, "n": 0, "maxOverlapPx": 0, "pair": None, "outside": []})])
        findings = smoke.evaluate(second, 1366, CAL)
        self.assertEqual([(f.name, f.tab) for f in findings], [("HEADER_MISSING", "b")])

    def test_header_opt_out_is_explicit_and_keeps_the_other_header_checks(self):
        absent = {"roots": 0, "n": 0, "maxOverlapPx": 0, "pair": None, "outside": []}
        self.assertEqual(self.check(good_result(tabs=[good_tab(header=absent)]), require_header=False), [])
        overlapping = {"roots": 1, "n": 2, "maxOverlapPx": 9, "pair": ["a", "b"], "outside": []}
        self.assertEqual(self.check(good_result(tabs=[good_tab(header=overlapping)]), require_header=False), ["HEADER_OVERLAP"])

    def test_nonce_mismatch_rejects_the_whole_result(self):
        result = good_result(nonce="aaaa")
        self.assertEqual(self.check(result, expected_nonce="aaaa"), [])
        self.assertEqual(self.check(result, expected_nonce="bbbb"), ["BAD_RESULT"])
        self.assertEqual(self.check(good_result(nonce=None), expected_nonce="bbbb"), ["BAD_RESULT"])
        broken = good_result(nonce="aaaa", errors=["x"], tabs=[])
        self.assertEqual(self.check(broken, expected_nonce="bbbb"), ["BAD_RESULT"], "an untrusted result reports nothing else")

    def test_late_errors_fail_even_when_the_early_error_list_is_empty(self):
        late = {"count": 1, "errors": ["console.error: late"]}
        findings = smoke.evaluate(good_result(lateErrors=late), 1366, CAL)
        self.assertEqual(names(findings), ["PAGE_ERRORS"])
        self.assertIn("after the walker finished", findings[0].detail)
        self.assertIn("late", findings[0].detail)
        both = smoke.evaluate(good_result(errors=["onerror: early"], lateErrors=late), 1366, CAL)
        self.assertEqual(len(both), 1)
        self.assertIn("early", both[0].detail)
        self.assertIn("after the walker finished", both[0].detail)
        # the count is what counts: the error list is capped in the page
        self.assertEqual(names(smoke.evaluate(good_result(lateErrors={"count": 99, "errors": []}), 1366, CAL)), ["PAGE_ERRORS"])
        self.assertEqual(smoke.evaluate(good_result(lateErrors={"count": 0, "errors": []}), 1366, CAL), [])

    def test_clipped_fit_elements_fail(self):
        tab = good_tab(fit={"n": 1, "clipped": ["p:x"]})
        self.assertEqual(self.check(good_result(tabs=[tab])), ["FIT_CLIPPED"])

    def test_bad_text_fails(self):
        self.assertEqual(self.check(good_result(tabs=[good_tab(textBad=["Total: undefined"])])), ["TEXT_BAD"])

    def test_heartbeat_requires_the_counter_to_advance(self):
        self.assertEqual(self.check(good_result(tabs=[good_tab(frames=[3, 4, 4])])), ["HEARTBEAT"])
        self.assertEqual(self.check(good_result(tabs=[good_tab(frames=[None, None, None], frameReads=[None, None])])), ["HEARTBEAT"])
        self.assertEqual(self.check(good_result(tabs=[good_tab(frames=[1, 2])])), ["HEARTBEAT"])
        self.assertEqual(self.check(good_result(tabs=[good_tab(frames=[0, 1, 2])])), [])

    def test_a_frame_counter_that_counts_its_own_reads_is_not_a_heartbeat(self):
        # frames advance, but two consecutive reads with no render in between already differ
        fake = good_tab(frames=[1, 3, 5], frameReads=[0, 1])
        findings = smoke.evaluate(good_result(tabs=[fake]), 1366, CAL)
        self.assertEqual(names(findings), ["HEARTBEAT"])
        self.assertIn("counts its own calls", findings[0].detail)
        missing = good_tab()
        del missing["frameReads"]
        self.assertEqual(self.check(good_result(tabs=[missing])), ["BAD_RESULT"])

    def test_tab_state_is_only_checked_when_the_page_provides_a_hook(self):
        same = [good_tab(tab="a", state="x"), good_tab(tab="b", state="x"), good_tab(tab="c", state="y")]
        self.assertEqual(self.check(good_result(tabs=same)), [], "no hook, no check")
        findings = smoke.evaluate(good_result(tabs=same, tabStateChecked=True), 1366, CAL)
        self.assertEqual([(f.name, f.tab) for f in findings], [("TAB_STATE", "b")])
        moving = [good_tab(tab="a", state="x"), good_tab(tab="b", state="y"), good_tab(tab="c", state="z")]
        self.assertEqual(self.check(good_result(tabs=moving, tabStateChecked=True)), [])
        unreadable = [good_tab(tab="a", state=None)]
        self.assertEqual(self.check(good_result(tabs=unreadable, tabStateChecked=True)), ["TAB_STATE"])

    def test_render_throw_fails(self):
        self.assertEqual(self.check(good_result(tabs=[good_tab(threw="Error: x")])), ["RENDER"])

    def test_failed_step_fails(self):
        self.assertEqual(self.check(good_result(steps=[{"name": "s", "ok": False, "msg": "no"}])), ["STEP_FAILED"])

    def test_blank_canvas_fails_by_default_without_any_flag(self):
        blank = good_result(tabs=[good_tab(canvases=[good_canvas(nonBgRatio=0.0)])])
        self.assertEqual(self.check(blank), ["NON_BG"])
        tiny = good_result(tabs=[good_tab(canvases=[good_canvas(nonBgRatio=0.0004)])])
        self.assertEqual(self.check(tiny), ["NON_BG"], "below the calibrated floor")
        ok = good_result(tabs=[good_tab(canvases=[good_canvas(nonBgRatio=0.0006)])])
        self.assertEqual(self.check(ok), [])

    def test_the_floor_comes_from_calibration_and_can_be_overridden(self):
        self.assertEqual(CAL["thresholds"]["nonBgFloor"], 0.0005)
        low = good_result(tabs=[good_tab(canvases=[good_canvas(nonBgRatio=0.001)])])
        self.assertEqual(self.check(low), [])
        self.assertEqual(self.check(low, non_bg_floor=0.01), ["NON_BG"])
        custom = json.loads(json.dumps(CAL))
        custom["thresholds"]["nonBgFloor"] = 0.5
        self.assertEqual(names(smoke.evaluate(low, 1366, custom)), ["NON_BG"])
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[good_canvas(nonBgRatio=0.0)])]), non_bg_floor=-1.0), [])

    def test_the_page_reported_ratio_is_never_used(self):
        liar = good_tab(canvases=[good_canvas(nonBgRatio=0.0)], render={"nonBgRatio": 0.9, "renderer": "SwiftShader"})
        self.assertEqual(self.check(good_result(tabs=[liar])), ["NON_BG"])
        no_canvas = good_tab(canvases=[], render={"nonBgRatio": 0.0})
        self.assertEqual(self.check(good_result(tabs=[no_canvas])), [], "a page without a canvas has nothing to read")

    def test_canvas_that_cannot_be_read_back_fails_closed(self):
        unreadable = good_canvas(readError="SecurityError: tainted")
        unreadable.pop("nonBgRatio")
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[unreadable])])), ["NON_BG"])
        missing = good_canvas()
        del missing["nonBgRatio"]
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[missing])])), ["NON_BG"])

    def test_declared_canvas_must_be_visible_and_must_be_a_canvas(self):
        hidden = good_canvas(declared=True, visible=False, kind=None)
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[hidden], draws=0)])), ["NON_BG"])
        undeclared_hidden = good_canvas(declared=False, visible=False, kind=None)
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[undeclared_hidden], draws=0)])), [])
        wrong = {"el": "div:x", "declared": True, "visible": True, "isCanvas": False, "kind": None}
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[wrong], draws=0)])), ["NON_BG"])

    def test_a_visible_canvas_needs_draw_calls_during_render_once(self):
        for draws in (0, None):
            self.assertEqual(self.check(good_result(tabs=[good_tab(draws=draws)])), ["NO_DRAW"], draws)
        no_context = good_canvas(kind=None, nonBgRatio=0.4)
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[no_context], draws=0)])), [], "no context: nothing to count")
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[], draws=0)])), [], "no canvas: nothing to count")

    def test_context_kinds_whose_draws_cannot_be_counted_are_judged_by_pixels_only(self):
        for kind in ("webgpu", "bitmaprenderer"):
            painted = good_canvas(kind=kind, nonBgRatio=0.3, renderer=None)
            self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[painted], draws=0)])), [], kind)
            blank = good_canvas(kind=kind, nonBgRatio=0.0, renderer=None)
            self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[blank], draws=0)])), ["NON_BG"], kind)
        for kind in ("2d", "webgl", "webgl2", "experimental-webgl"):
            counted = good_canvas(kind=kind, renderer="SwiftShader" if kind != "2d" else None)
            self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[counted], draws=0)])), ["NO_DRAW"], kind)

    def test_webgl_errors_and_lost_context_fail(self):
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[good_canvas(glErrors=["INVALID_ENUM"])])])), ["GL_ERROR"])
        self.assertEqual(self.check(good_result(tabs=[good_tab(canvases=[good_canvas(contextLost=True)])])), ["GL_ERROR"])

    def test_renderer_is_required_automatically_for_webgl_canvases_only(self):
        self.assertEqual(CAL["gl"]["rendererSubstring"], "SwiftShader")
        intel = good_result(tabs=[good_tab(canvases=[good_canvas(renderer="ANGLE (Intel UHD)")])])
        self.assertEqual(self.check(intel), ["RENDERER"])
        self.assertEqual(self.check(intel, require_renderer=""), [], "explicit off")
        self.assertEqual(self.check(intel, require_renderer="intel"), [], "explicit override")
        flat = good_result(tabs=[good_tab(canvases=[good_canvas(kind="2d", renderer=None)])])
        self.assertEqual(self.check(flat), [], "a 2d canvas has no GL renderer")
        self.assertEqual(self.check(good_result(), require_renderer="swiftshader"), [])

    def test_findings_carry_distinct_codes(self):
        codes = {smoke.CODES[name] for name in smoke.CODES}
        self.assertEqual(len(codes), len(smoke.CODES))
        self.assertTrue(all(re.fullmatch(r"GEMARC_GATE_\d{3}", c) for c in codes))


class ConsoleScrapeTests(unittest.TestCase):
    ORIGIN = "file:///C:/work/%E6%97%A5%E6%9C%AC/page%20dir/"
    PAGE = ORIGIN + "page.html?gate=1"

    def lines(self, *items):
        return "\n".join(items) + "\n"

    def test_parses_both_line_forms_and_multiline_messages(self):
        text = self.lines(
            f'[1:2:1030/113311.463:INFO:CONSOLE:4] "a log", source: {self.PAGE} (4)',
            f'[1:2:1030/113311.463:INFO:CONSOLE(7)] "second form", source: {self.PAGE} (7)',
            '[1:2:1030/113311.464:INFO:CONSOLE:4] "first line',
            f'second line", source: {self.PAGE} (9)',
        )
        records = smoke.parse_console_lines(text)
        self.assertEqual([r["message"] for r in records], ["a log", "second form", "first line\nsecond line"])
        self.assertEqual(records[0]["line"], 4)

    def test_never_raises_on_noise(self):
        self.assertEqual(smoke.parse_console_lines(b"\xff\xfe garbage \n[not a log line\n"), [])

    def test_uncaught_and_webgl_errors_from_the_page_origin_count(self):
        text = self.lines(
            f'[1:2:1030/1.1:INFO:CONSOLE:8] "Uncaught SyntaxError: Unexpected token \';\'", source: {self.PAGE} (8)',
            f'[1:2:1030/1.1:INFO:CONSOLE:5] "Uncaught (in promise) Error: x", source: {self.PAGE} (5)',
            f'[1:2:1030/1.1:INFO:CONSOLE:6] "WebGL: INVALID_OPERATION: drawArrays: no valid shader program in use", source: {self.PAGE} (6)',
        )
        errors = smoke.console_errors_from_origin(smoke.parse_console_lines(text), self.ORIGIN, CAL)
        self.assertEqual(len(errors), 3)

    def test_info_level_noise_from_the_page_origin_does_not_count(self):
        text = self.lines(
            f'[1:2:1030/1.1:INFO:CONSOLE:4] "a log", source: {self.PAGE} (4)',
            f'[1:2:1030/1.1:INFO:CONSOLE:0] "[.WebGL-0x1]GL Driver Message (OpenGL, Performance, GL_CLOSE_PATH_NV, High): GPU stall due to ReadPixels", source: {self.PAGE} (0)',
        )
        self.assertEqual(smoke.console_errors_from_origin(smoke.parse_console_lines(text), self.ORIGIN, CAL), [])

    def test_other_origins_are_ignored_even_at_error_level(self):
        text = self.lines(
            '[1:2:1030/1.1:ERROR:CONSOLE:5518] "Uncaught boom", source: chrome-extension://abc/background.js (5518)',
            '[1:2:1030/1.1:ERROR:CONSOLE:1] "Uncaught boom", source: file:///C:/elsewhere/other.html (1)',
        )
        self.assertEqual(smoke.console_errors_from_origin(smoke.parse_console_lines(text), self.ORIGIN, CAL), [])

    def test_error_level_from_the_page_origin_counts_whatever_the_message(self):
        text = self.lines(f'[1:2:1030/1.1:ERROR:CONSOLE:3] "plain message", source: {self.PAGE} (3)')
        self.assertEqual(len(smoke.console_errors_from_origin(smoke.parse_console_lines(text), self.ORIGIN, CAL)), 1)

    def test_origin_match_survives_percent_encoding_differences(self):
        decoded_origin = "file:///C:/work/日本/page dir/"
        text = self.lines(f'[1:2:1030/1.1:INFO:CONSOLE:8] "Uncaught x", source: {self.PAGE} (8)')
        self.assertEqual(len(smoke.console_errors_from_origin(smoke.parse_console_lines(text), decoded_origin, CAL)), 1)


class ArgumentConstructionTests(unittest.TestCase):
    def test_design_flags_are_present_and_complete(self):
        args = smoke.build_args(EDGE.path, "C:/tmp/prof", (1390, 800), "file:///x.html?gate=1", 20000)
        self.assertEqual(args[0], EDGE.path)
        for flag in (
            "--headless=new",
            "--disable-gpu",
            "--enable-unsafe-swiftshader",
            "--use-angle=swiftshader",
            "--user-data-dir=C:/tmp/prof",
            "--window-size=1390,800",
            "--virtual-time-budget=20000",
            "--enable-logging=stderr",
            "--v=0",
            "--dump-dom",
        ):
            self.assertIn(flag, args)
        self.assertEqual(args[-1], "file:///x.html?gate=1")
        self.assertEqual(len(args), 12)

    def test_window_width_is_target_plus_family_offset(self):
        self.assertEqual(smoke.width_offset(CAL, "edge", 1366), 24)
        self.assertEqual(smoke.width_offset(CAL, "chrome", 1366), 16)
        self.assertEqual(smoke.width_offset(CAL, "unknown-family", 1366), 16)  # unknown families borrow chrome
        self.assertEqual(smoke.width_offset(CAL, "edge", 1111), 24)  # unmeasured width: family constant

    def test_page_url_is_percent_encoded_ascii(self):
        page = pathlib.Path(tempfile.gettempdir()) / "日本語 dir" / "x.html"
        url = smoke.page_url(page)
        self.assertTrue(url.isascii(), url)
        self.assertTrue(url.startswith("file:///"), url)
        self.assertTrue(url.endswith("%E6%97%A5%E6%9C%AC%E8%AA%9E%20dir/x.html?gate=1"), url)
        self.assertEqual(smoke.page_url(page, ""), url[: -len("?gate=1")])

    def test_run_gate_passes_the_expected_command_to_the_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = pathlib.Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            seen = []

            def launcher(args, timeout, env=None):
                seen.append((args, timeout, env))
                return smoke._Launch(0, dom_for(good_result(nonce=nonce_of(args))), b"", False, 0.1)

            for browser, expected_window in ((EDGE, "1390,800"), (CHROME, "1382,800")):
                with mock.patch.object(smoke, "_launch", launcher):
                    res = smoke.run_gate(page, 1366, browser=browser, tmp_root=tmp, retries=0)
                self.assertEqual(res.status, "PASS", res.message)
                args, timeout, env = seen[-1]
                self.assertIn(f"--window-size={expected_window}", args)
                self.assertRegex(args[-1], r"/page\.html\?gate=1&gn=[0-9a-f]{16}$")
                profile = [a for a in args if a.startswith("--user-data-dir=")][0].split("=", 1)[1]
                self.assertTrue(profile.startswith(str(pathlib.Path(tmp))), profile)
                self.assertEqual(timeout, 30.0)
                self.assertIn("--virtual-time-budget=20000", args)
                # the browser's own temp directory is inside the gate's work directory (see _browser_env)
                for key in ("TEMP", "TMP", "TMPDIR"):
                    self.assertTrue(env[key].startswith(str(pathlib.Path(tmp))), (key, env[key]))
                    self.assertNotEqual(env[key], str(pathlib.Path(tmp)))
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [page], "work directory (and the browser temp inside it) removed")

    def test_with_nonce_appends_to_the_query(self):
        self.assertEqual(smoke.with_nonce("gate=1", "ab12"), "gate=1&gn=ab12")
        self.assertEqual(smoke.with_nonce("", "ab12"), "gn=ab12")

    def test_assemble_page_inlines_and_escapes(self):
        page = "<html><head>" + smoke.SELFTEST_MARKER + "</head></html>"
        out = smoke.assemble_page(page, "var s = '</script>'; var t = '\\1';")
        self.assertNotIn(smoke.SELFTEST_MARKER, out)
        self.assertIn("<\\/script>", out)
        self.assertIn("'\\1'", out)  # backslashes in JS survive (no re.sub)
        self.assertEqual(out.count("</script>"), 1)
        with self.assertRaises(ValueError):
            smoke.assemble_page("<html></html>", "x")


class DiscoveryTests(unittest.TestCase):
    NO_ENV = {"ProgramFiles(x86)": "C:/X86", "ProgramFiles": "C:/PF", "LOCALAPPDATA": "C:/LA"}

    def test_edge_candidates_precede_chrome_and_env_override_comes_first(self):
        env = dict(self.NO_ENV, **{smoke.BROWSER_ENV: "D:/mine/browser.exe"})
        paths = smoke.candidate_paths(env, which=lambda name: None, platform="win32")
        self.assertEqual(paths[0], "D:/mine/browser.exe")
        edge = [i for i, p in enumerate(paths) if p.endswith("msedge.exe")]
        chrome = [i for i, p in enumerate(paths) if p.endswith("chrome.exe")]
        self.assertTrue(edge and chrome and max(edge) < min(chrome))

    def test_path_lookup_comes_before_default_install_paths(self):
        paths = smoke.candidate_paths(self.NO_ENV, which=lambda n: "/usr/bin/msedge" if n == "msedge" else None, platform="linux")
        self.assertEqual(paths[0], "/usr/bin/msedge")

    def test_find_browser_picks_the_first_existing_file_and_classifies_it(self):
        found = smoke.find_browser(self.NO_ENV, which=lambda n: None, isfile=lambda p: p.endswith("chrome.exe"), platform="win32")
        self.assertEqual(found.family, "chrome")
        found = smoke.find_browser(self.NO_ENV, which=lambda n: None, isfile=lambda p: "Edge" in p, platform="win32")
        self.assertEqual(found.family, "edge")

    def test_no_browser_returns_none(self):
        self.assertIsNone(smoke.find_browser(self.NO_ENV, which=lambda n: None, isfile=lambda p: False, platform="win32"))

    def test_a_pinned_browser_that_is_not_a_file_is_never_replaced_by_another_browser(self):
        env = dict(self.NO_ENV, **{smoke.BROWSER_ENV: "D:/nope/browser.exe"})
        found = smoke.find_browser(
            env, which=lambda n: "/usr/bin/msedge", isfile=lambda p: p != "D:/nope/browser.exe", platform="win32"
        )
        self.assertIsNone(found, "the gate silently ran on a browser the user did not pin")

    def test_a_pinned_browser_that_exists_wins_over_edge(self):
        env = dict(self.NO_ENV, **{smoke.BROWSER_ENV: "D:/mine/chrome.exe"})
        found = smoke.find_browser(env, which=lambda n: "/usr/bin/msedge", isfile=lambda p: True, platform="win32")
        self.assertEqual((found.path, found.family), ("D:/mine/chrome.exe", "chrome"))

    def test_the_skip_message_names_the_pinned_browser_and_the_generic_case(self):
        pinned = smoke.no_browser_message({smoke.BROWSER_ENV: "D:/nope.exe"})
        for needle in ("SKIPPED, NOT PASSED", "UNVERIFIED", "D:/nope.exe", "does not fall back"):
            self.assertIn(needle, pinned)
        generic = smoke.no_browser_message({})
        self.assertIn("no Edge or Chrome found", generic)
        self.assertNotIn("pinned", generic)
        self.assertIn("SKIPPED, NOT PASSED", smoke.no_browser_message({}, pinned="Z:/x.exe"))

    def test_run_gate_with_a_missing_pinned_browser_skips(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {smoke.BROWSER_ENV: str(pathlib.Path(tmp) / "nope.exe")}):
            res = smoke.run_gate(FIXTURES / "ok-stub.html", 1366, inline_selftest=True, tmp_root=tmp)
        self.assertEqual((res.status, res.codes), ("SKIP", ["NO_BROWSER"]))
        self.assertIn("nope.exe", res.message)

    def test_cli_browser_flag_that_is_not_a_file_skips_with_exit_3(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(buf):
            code = smoke.main([str(FIXTURES / "ok-stub.html"), "--browser", str(pathlib.Path(tmp) / "nope.exe"), "--width", "1366"])
        self.assertEqual(code, 3)
        self.assertIn("SKIPPED, NOT PASSED", buf.getvalue())
        self.assertIn("nope.exe", buf.getvalue())

    def test_no_browser_is_a_loud_skip_never_a_pass(self):
        with mock.patch.object(smoke, "find_browser", return_value=None):
            res = smoke.run_gate(FIXTURES / "ok-stub.html", 1366, inline_selftest=True)
        self.assertEqual(res.status, "SKIP")
        self.assertNotEqual(res.status, "PASS")
        self.assertIn("SKIPPED, NOT PASSED", res.message)
        self.assertIn("UNVERIFIED", res.message)
        self.assertEqual(res.codes, ["NO_BROWSER"])

    def test_cli_exit_code_is_3_without_a_browser_and_says_so_on_stderr(self):
        buf = io.StringIO()
        with mock.patch.object(smoke, "find_browser", return_value=None), redirect_stderr(buf):
            code = smoke.main([str(FIXTURES / "ok-stub.html"), "--inline-selftest", "--width", "1366"])
        self.assertEqual(code, 3)
        self.assertIn("SKIPPED, NOT PASSED", buf.getvalue())


class FailClosedAndRetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.page = self.tmp / "page.html"
        self.page.write_text("<html></html>", encoding="utf-8")

    def run_with(self, launches, retries=1, width=1366):
        queue = list(launches)
        calls = []

        def launcher(args, timeout, env=None):
            calls.append(args)
            launch = queue.pop(0) if len(queue) > 1 else queue[0]
            return with_real_nonce(launch, args)

        with mock.patch.object(smoke, "_launch", launcher):
            res = smoke.run_gate(self.page, width, browser=EDGE, tmp_root=self.tmp, retries=retries)
        return res, calls

    def ok(self, dom, stderr=b""):
        return smoke._Launch(0, dom, stderr, False, 0.2)

    def test_complete_dom_without_result_fails_and_is_not_retried(self):
        res, calls = self.run_with([self.ok(b"<html><body></body></html>")], retries=3)
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.codes, ["NO_RESULT"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.attempts, 1)

    def test_assertion_failure_is_never_retried(self):
        res, calls = self.run_with([self.ok(dom_for(good_result(errors=["boom"])))], retries=3)
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.codes, ["PAGE_ERRORS"])
        self.assertEqual(len(calls), 1)

    def test_garbled_result_fails_and_is_not_retried(self):
        res, calls = self.run_with([self.ok(b'<html><body><pre id="atlas-gate" data-done="1">{oops</pre></body></html>')], retries=3)
        self.assertEqual(res.codes, ["BAD_RESULT"])
        self.assertEqual(len(calls), 1)

    def test_reported_width_mismatch_fails(self):
        res, _ = self.run_with([self.ok(dom_for(good_result(width=1342)))], retries=0, width=1366)
        self.assertEqual(res.status, "FAIL")
        self.assertIn("WIDTH", res.codes)

    def test_timeout_is_retried_then_passes(self):
        res, calls = self.run_with(
            [smoke._Launch(None, b"", b"", True, 30.0), self.ok(dom_for(good_result()))], retries=1
        )
        self.assertEqual(res.status, "PASS")
        self.assertEqual(res.attempts, 2)
        self.assertEqual(len(res.process_failures), 1)
        self.assertIn("timeout", res.process_failures[0])

    def test_truncated_output_and_crash_are_process_level_failures(self):
        res, calls = self.run_with(
            [smoke._Launch(0, b"<html><body>cut off", b"", False, 1.0), smoke._Launch(3221225477, b"", b"", False, 0.5)],
            retries=1,
        )
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.codes, ["PROCESS"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(res.process_failures), 2)

    def test_retries_zero_means_one_launch(self):
        res, calls = self.run_with([smoke._Launch(None, b"", b"", True, 30.0)], retries=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res.codes, ["PROCESS"])

    def test_stderr_uncaught_error_from_the_page_origin_fails_an_otherwise_clean_page(self):
        origin = self.page.resolve().parent.as_uri() + "/"
        stderr = f'[1:2:1030/1.1:INFO:CONSOLE:8] "Uncaught ReferenceError: x is not defined", source: {origin}page.html?gate=1 (8)\n'.encode()
        res, _ = self.run_with([self.ok(dom_for(good_result()), stderr)], retries=0)
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.codes, ["CONSOLE_STDERR"])

    def test_extension_console_noise_does_not_fail_a_clean_page(self):
        stderr = b'[1:2:1030/1.1:ERROR:CONSOLE:5518] "Uncaught boom", source: chrome-extension://abc/background.js (5518)\n'
        res, _ = self.run_with([self.ok(dom_for(good_result()), stderr)], retries=0)
        self.assertEqual(res.status, "PASS", res.message)

    def test_every_attempt_has_a_fresh_nonce_and_a_stale_dom_is_rejected(self):
        seen = []

        def launcher(args, timeout, env=None):
            seen.append(nonce_of(args))
            if len(seen) == 1:
                return smoke._Launch(None, b"", b"", True, 30.0)  # attempt 1 times out
            # attempt 2 hands back a DOM that carries attempt 1's nonce: it must not be accepted
            return smoke._Launch(0, dom_for(good_result(nonce=seen[0])), b"", False, 0.2)

        with mock.patch.object(smoke, "_launch", launcher):
            res = smoke.run_gate(self.page, 1366, browser=EDGE, tmp_root=self.tmp, retries=1)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual((res.status, res.codes), ("FAIL", ["BAD_RESULT"]))
        self.assertIn("nonce", res.message)

    def test_hard_coded_result_without_the_nonce_fails(self):
        for nonce in (None, "0123456789abcdef"):
            launch = smoke._Launch(0, dom_for(good_result(nonce=nonce)), b"", False, 0.2)
            res, _ = self.run_with([launch], retries=0)
            self.assertEqual((res.status, res.codes), ("FAIL", ["BAD_RESULT"]), nonce)

    def test_late_errors_attribute_fails_an_otherwise_clean_page(self):
        late = html.escape(json.dumps({"count": 1, "errors": ["console.error: late"]}), quote=True)
        dom = dom_for(good_result(), attrs=f'id="atlas-gate" data-done="1" data-late-errors="{late}"')
        res, _ = self.run_with([self.ok(dom)], retries=0)
        self.assertEqual((res.status, res.codes), ("FAIL", ["PAGE_ERRORS"]))
        self.assertIn("after the walker finished", res.message)

    def test_a_browser_that_cannot_be_started_is_a_coded_failure_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            textfile = pathlib.Path(tmp) / "not-a-browser.exe"
            textfile.write_text("this is not an executable", encoding="utf-8")
            for path in (str(pathlib.Path(tmp) / "missing" / "edge.exe"), str(textfile)):
                res = smoke.run_gate(self.page, 1366, browser=smoke.Browser(path, "edge", None), tmp_root=self.tmp, retries=3)
                self.assertEqual((res.status, res.codes), ("FAIL", ["PROCESS"]), path)
                self.assertEqual(res.attempts, 1, "a browser that cannot start is not retried")
                self.assertIn("could not start the browser", res.message)
                self.assertEqual(res.leftovers, [])

    def test_missing_page_fails(self):
        with mock.patch.object(smoke, "_launch", side_effect=AssertionError("must not launch")):
            res = smoke.run_gate(self.tmp / "nope.html", 1366, browser=EDGE, tmp_root=self.tmp)
        self.assertEqual(res.status, "FAIL")

    def test_profile_directories_are_cleaned_up(self):
        res, _ = self.run_with([self.ok(dom_for(good_result()))], retries=0)
        self.assertEqual(res.leftovers, [])
        leftovers = [p.name for p in self.tmp.iterdir() if p.name != "page.html"]
        self.assertEqual(leftovers, [])

    def test_process_failure_reason_helper(self):
        self.assertIsNone(smoke.process_failure_reason(self.ok(b"<html></html>")))
        self.assertIn("exited", smoke.process_failure_reason(smoke._Launch(1, b"<html></html>", b"", False, 1)))
        self.assertIn("truncated", smoke.process_failure_reason(smoke._Launch(0, b"", b"", False, 1)))


class LoudSkipTests(unittest.TestCase):
    def test_module_setup_prints_the_no_browser_banner_at_any_verbosity(self):
        buf = io.StringIO()
        with mock.patch.dict(globals(), {"BROWSER": None}), redirect_stderr(buf):
            setUpModule()
        self.assertIn("NO BROWSER", buf.getvalue())
        self.assertIn("not a pass", buf.getvalue())
        quiet = io.StringIO()
        with mock.patch.dict(globals(), {"BROWSER": EDGE}), redirect_stderr(quiet):
            setUpModule()
        self.assertEqual(quiet.getvalue(), "")

    def test_real_cli_process_skips_with_exit_3_when_the_environment_has_no_browser(self):
        src = str(pathlib.Path(smoke.__file__).resolve().parents[3])
        nowhere = "Z:\\none" if os.name == "nt" else "/nonexistent-gate-test"
        env = {
            "PYTHONPATH": src,
            "PATH": nowhere,
            "ProgramFiles": nowhere,
            "ProgramFiles(x86)": nowhere,
            "LOCALAPPDATA": nowhere,
        }
        for key in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        with tempfile.TemporaryDirectory() as cwd:  # neither the repo nor anything with a browser
            proc = subprocess.run(
                [sys.executable, "-m", "gemarc_mcp.atlas.gate.smoke", str(FIXTURES / "ok-stub.html"), "--inline-selftest", "--width", "1366"],
                env=env,
                cwd=cwd,
                capture_output=True,
                timeout=60,
            )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn(b"SKIPPED, NOT PASSED", proc.stderr)
        self.assertNotIn(b"PASS width", proc.stdout)


class CommandLineProcessTests(unittest.TestCase):
    """The CLI as a real child process; no Edge/Chrome needed."""

    @staticmethod
    def env(**extra):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(pathlib.Path(smoke.__file__).resolve().parents[3])
        env.update(extra)
        return env

    def test_a_pinned_browser_that_is_missing_skips_even_though_edge_exists(self):
        with tempfile.TemporaryDirectory() as cwd:
            env = self.env(**{smoke.BROWSER_ENV: str(pathlib.Path(cwd) / "nope" / "edge.exe")})
            proc = subprocess.run(
                [sys.executable, "-m", "gemarc_mcp.atlas.gate.smoke", str(FIXTURES / "ok-stub.html"), "--inline-selftest", "--width", "1366"],
                env=env, cwd=cwd, capture_output=True, timeout=60,
            )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn(b"SKIPPED, NOT PASSED", proc.stderr)
        self.assertIn(b"nope", proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertNotIn(b"PASS width", proc.stdout)

    def test_a_console_that_cannot_encode_the_page_path_still_prints_the_verdict(self):
        # gbk cannot encode an emoji. The page does not exist, so the runner reports "page not found: <path>"
        # (the exact text that used to raise UnicodeEncodeError after the browser had run).
        with tempfile.TemporaryDirectory() as cwd:
            page = pathlib.Path(cwd) / "\U0001F600 page.html"
            proc = subprocess.run(
                [sys.executable, "-m", "gemarc_mcp.atlas.gate.smoke", str(page), "--browser", sys.executable, "--width", "1366"],
                env=self.env(PYTHONIOENCODING="gbk"), cwd=cwd, capture_output=True, timeout=60,
            )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertIn(b"FAIL width=1366", proc.stdout)
        self.assertIn(b"\\U0001f600", proc.stdout)


class RealProcessTests(unittest.TestCase):
    """Process handling against real (non-browser) child processes; no Edge/Chrome needed."""

    def test_launch_reports_a_start_failure_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            textfile = pathlib.Path(tmp) / "not-a-browser.exe"
            textfile.write_text("this is not an executable", encoding="utf-8")
            for path in (str(pathlib.Path(tmp) / "nope.exe"), str(textfile)):
                launch = smoke._launch([path, "--dump-dom"], timeout=5)
                self.assertIsNone(launch.returncode, path)
                self.assertFalse(launch.timed_out, path)
                self.assertTrue(launch.error and ("Error" in launch.error), path)
                self.assertIn("could not start the browser", smoke.process_failure_reason(launch))

    def test_the_child_gets_its_temp_directory_inside_the_scratch_directory(self):
        # Edge drops an updater log, an importer folder and .tmp files into the temp directory; the
        # gate points the child's temp variables into its own work directory so nothing stays behind.
        code = "import os, sys; sys.stdout.write(os.environ['TEMP'] + '|' + os.environ['TMP'] + '|' + os.environ['TMPDIR'])"
        with tempfile.TemporaryDirectory() as tmp:
            scratch = pathlib.Path(tmp) / "browser-tmp"
            launch = smoke._launch([sys.executable, "-c", code], timeout=20, env=smoke._browser_env(scratch))
            self.assertEqual(launch.returncode, 0, launch.stderr)
            self.assertTrue(scratch.is_dir())
            self.assertEqual(launch.stdout.decode().split("|"), [str(scratch)] * 3)
            plain = smoke._launch([sys.executable, "-c", "import os; print(os.environ['TEMP'])"], timeout=20)
            self.assertNotEqual(plain.stdout.decode().strip(), str(scratch), "without env the child inherits ours")

    def test_timeout_kills_the_whole_process_tree_promptly(self):
        code = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); time.sleep(120)"
        )
        started = time.perf_counter()
        launch = smoke._launch([sys.executable, "-c", code], timeout=1.5)
        elapsed = time.perf_counter() - started
        self.assertTrue(launch.timed_out)
        self.assertLess(elapsed, 12, "the grandchild kept the pipes open: the tree was not killed")
        self.assertIn("timeout", smoke.process_failure_reason(launch))

    def test_a_browser_that_exits_non_zero_is_retried_then_reported_as_process_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = pathlib.Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            fake = smoke.Browser(sys.executable, "chrome", None)  # python rejects --headless=new and exits 2
            res = smoke.run_gate(page, 1366, browser=fake, tmp_root=tmp, retries=1)
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(res.codes, ["PROCESS"])
        self.assertEqual(res.attempts, 2)
        self.assertEqual(res.leftovers, [])


class AssetConsistencyTests(unittest.TestCase):
    def test_calibration_is_self_consistent(self):
        for family, entry in CAL["browsers"].items():
            self.assertTrue(entry["measured"], family)
            for width, offset in entry["measured"].items():
                self.assertEqual(offset, entry["widthOffset"], f"{family} {width}")
        self.assertEqual(CAL["thresholds"]["headerOverlapPx"], 2)
        self.assertLessEqual(CAL["limits"]["launchSecondsMax"], 8)
        self.assertGreater(CAL["thresholds"]["nonBgFloor"], 0, "a blank canvas must fail with default settings")
        self.assertEqual(CAL["gl"]["rendererSubstring"], "SwiftShader")
        for key in ("thresholds.nonBgFloor", "gl.rendererSubstring"):
            self.assertIn(key, CAL["provisional"], "unmeasured numbers must be labelled provisional")

    def test_selftest_is_timer_driven_never_raf(self):
        source = (GATE_DIR / "selftest.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"requestAnimationFrame\s*\(", source))
        self.assertIn("setTimeout", source)
        self.assertIn("data-done", source)
        self.assertIn("unhandledrejection", source)
        self.assertIn("console.error", source)
        # the independent evidence: the self-test reads canvases and counts draws itself
        for needle in (
            "gn=", "data-late-errors", "HTMLCanvasElement.prototype.getContext", "drawArrays", "getError", "readPixels",
            "clientWidth", "getExtension", "drawArraysInstancedANGLE",
        ):
            self.assertIn(needle, source)
        self.assertEqual(smoke.RESULT_SCHEMA, 2)
        self.assertIn("RESULT_VERSION = 2", source)

    def test_fixtures_are_synthetic_ascii_lf_and_carry_the_placeholder(self):
        files = sorted(FIXTURES.glob("*.html"))
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            raw = path.read_bytes()
            self.assertTrue(raw.isascii(), path.name)
            self.assertNotIn(b"\r", raw, path.name)
            self.assertEqual(raw.count(smoke.SELFTEST_MARKER.encode()), 1, path.name)

    def test_every_fixture_is_exercised_by_a_test_or_the_spike_matrix(self):
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        spike = load_spike("gate_spike_orphans")
        self.addCleanup(sys.modules.pop, "gate_spike_orphans", None)
        used = {case.fixture for case in spike.CASES}
        for path in sorted(FIXTURES.glob("*.html")):
            self.assertTrue(path.stem in used or f'"{path.stem}"' in source, f"{path.name} is not used by any test or spike row")

    def test_every_fixture_used_by_the_spike_exists(self):
        spike = load_spike("gate_spike_under_test")
        self.addCleanup(sys.modules.pop, "gate_spike_under_test", None)
        for case in spike.CASES:
            self.assertTrue((FIXTURES / f"{case.fixture}.html").is_file(), case.fixture)
            self.assertTrue(set(case.codes) <= set(smoke.CODES), case)
        self.assertEqual(spike.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95), 10)
        self.assertEqual(spike.percentile([5.0], 0.95), 5.0)

    def test_spike_exits_3_and_never_reports_go_without_a_browser(self):
        spike = load_spike("gate_spike_nobrowser")
        self.addCleanup(sys.modules.pop, "gate_spike_nobrowser", None)
        buf = io.StringIO()
        with mock.patch.object(spike.smoke, "find_browser", return_value=None), redirect_stderr(buf):
            code = spike.main(["--runs", "1"])
        self.assertEqual(code, 3)
        self.assertIn("not a pass", buf.getvalue())


# ----------------------------------------------------------------------------------------------
# Integration: real browser. Loud SKIP when none is installed.
# ----------------------------------------------------------------------------------------------
BROWSER = smoke.find_browser()
NO_BROWSER_REASON = (
    "NO BROWSER (Edge/Chrome) FOUND: the gate integration tests did NOT run. This is a SKIP, not a pass; "
    "the rendered-page gate is unverified on this machine."
)


def setUpModule():
    # skipIf reasons are only shown with -v; a missing browser must be visible at any verbosity.
    if BROWSER is None:
        print("\n*** " + NO_BROWSER_REASON + "\n", file=sys.stderr, flush=True)


def minimal_page(extra_head="", extra_body=""):
    """A tiny self-contained page (placeholder for the self-test, one header control, a working heartbeat)."""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>t</title>' + smoke.SELFTEST_MARKER + extra_head + "</head><body>"
        '<header data-gate="header"><button type="button">A</button></header>' + extra_body +
        "<script>var f=0;AtlasGate.run({tabs:['m'],renderOnce:function(){f++;},frame:function(){return f;}});</script>"
        "</body></html>"
    )


@unittest.skipIf(BROWSER is None, NO_BROWSER_REASON)
class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="atlas-gate-it-")
        cls.tmp = pathlib.Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def gate(self, fixture, width, **kwargs):
        kwargs.setdefault("retries", 1)
        return smoke.run_gate(FIXTURES / f"{fixture}.html", width, browser=BROWSER, tmp_root=self.tmp, inline_selftest=True, **kwargs)

    def expect(self, fixture, status, codes, width=1366, **kwargs):
        res = self.gate(fixture, width, **kwargs)
        self.assertEqual((res.status, res.codes), (status, sorted(codes)), f"{fixture}@{width}: {res.message}")
        self.assertEqual(res.leftovers, [])
        return res

    def run_page(self, name, text, **kwargs):
        page = self.tmp / name
        smoke.write_text_lf(page, text)
        kwargs.setdefault("retries", 1)
        return smoke.run_gate(page, 1366, browser=BROWSER, tmp_root=self.tmp, **kwargs)

    # ---- positive controls: defaults only, no opt-in flag anywhere ---------------------------
    def test_ok_stub_passes_with_the_defaults_and_the_gate_reads_the_canvas_itself(self):
        res = self.expect("ok-stub", "PASS", [])
        tabs = res.result["tabs"]
        self.assertEqual(len(tabs), 2)
        self.assertRegex(res.result["nonce"], r"^[0-9a-f]{16}$")
        for tab in tabs:
            canvas = tab["canvases"][0]
            self.assertAlmostEqual(canvas["nonBgRatio"], 0.125, places=2)  # the gate's own read agrees with the stub
            self.assertIn("SwiftShader", canvas["renderer"])
            self.assertEqual(canvas["glErrors"], [])
            self.assertGreater(tab["draws"], 0)
            self.assertEqual(tab["frameReads"][0], tab["frameReads"][1])
        self.assertEqual(res.result["innerWidth"], 1366)
        self.assertTrue(res.result["tabStateChecked"])
        self.assertEqual([t["state"] for t in tabs], ["overview", "detail"])
        self.assertEqual(res.result["steps"][0]["ok"], True)

    def test_a_2d_canvas_is_read_and_counted_too(self):
        res = self.expect("ok-canvas-2d", "PASS", [])
        canvas = res.result["tabs"][0]["canvases"][0]
        self.assertEqual(canvas["kind"], "2d")
        self.assertGreater(canvas["nonBgRatio"], 0.1)
        self.assertGreater(res.result["tabs"][0]["draws"], 0)

    def test_a_framebuffer_left_bound_by_the_page_does_not_hide_or_break_the_read(self):
        res = self.expect("ok-gl-fbo-left-bound", "PASS", [])
        canvas = res.result["tabs"][0]["canvases"][0]
        self.assertGreater(canvas["nonBgRatio"], 0.05)
        self.assertEqual(canvas["glErrors"], [])

    def test_a_webgl1_page_that_draws_only_through_an_extension_is_not_a_false_no_draw(self):
        # ANGLE_instanced_arrays is not a global in Chromium: its draw methods only exist on the object
        # getExtension() returns, so the self-test has to wrap them when that object is handed out.
        res = self.expect("ok-gl1-instanced", "PASS", [])
        tab = res.result["tabs"][0]
        self.assertEqual(tab["canvases"][0]["kind"], "webgl")
        self.assertGreater(tab["canvases"][0]["nonBgRatio"], 0.01)
        self.assertEqual(tab["draws"], 1, "one extension draw per renderOnce, counted exactly once")

    # ---- header ---------------------------------------------------------------------------
    def test_header_overlap_fails_when_narrow_and_passes_when_wide(self):
        for width in (1256, 1366):
            self.expect("bad-header-overlap", "FAIL", ["HEADER_OVERLAP"], width)
        self.expect("bad-header-overlap", "PASS", [], 1576)

    def test_an_unmarked_header_fails_and_the_opt_out_is_explicit(self):
        res = self.expect("header-unmarked", "FAIL", ["HEADER_MISSING"])
        self.assertEqual(res.result["tabs"][0]["header"]["roots"], 0)
        self.expect("header-unmarked", "PASS", [], require_header=False)

    # ---- overflow -------------------------------------------------------------------------
    def test_horizontal_overflow_hidden_by_the_scrollbar_width_is_caught(self):
        res = self.expect("overflow-vscroll", "FAIL", ["OVERFLOW_X"])
        tab = res.result["tabs"][0]
        # this is the class the old innerWidth comparison missed: scrollWidth equals innerWidth exactly,
        # and only clientWidth (innerWidth minus the classic scrollbar) shows the overflow
        self.assertLess(tab["clientWidth"], res.result["innerWidth"], "no classic vertical scrollbar in this browser build")
        self.assertLessEqual(tab["scrollWidth"], res.result["innerWidth"])
        self.assertGreater(tab["scrollWidth"], tab["clientWidth"])

    def test_wide_overflow_fails(self):
        self.expect("wide-overflow", "FAIL", ["OVERFLOW_X"])

    def test_overflow_hidden_on_the_root_still_counts_but_a_clipping_wrapper_or_fixed_element_does_not(self):
        wide = '<div style="width:3000px;height:10px"></div>'
        hidden = self.run_page("root-hidden.html", minimal_page(extra_head="<style>html{overflow-x:hidden}</style>", extra_body=wide), inline_selftest=True)
        self.assertEqual((hidden.status, hidden.codes), ("FAIL", ["OVERFLOW_X"]), hidden.message)
        wrapped = self.run_page("wrapped.html", minimal_page(extra_body='<div style="overflow:hidden;width:100%">' + wide + "</div>"), inline_selftest=True)
        self.assertEqual((wrapped.status, wrapped.codes), ("PASS", []), wrapped.message)
        fixed = self.run_page("fixed.html", minimal_page(extra_body='<div style="position:fixed;top:0;left:100%;width:300px;height:20px">x</div>'), inline_selftest=True)
        self.assertEqual((fixed.status, fixed.codes), ("PASS", []), fixed.message)

    # ---- errors -----------------------------------------------------------------------------
    def test_frozen_scene_fails_only_on_the_heartbeat(self):
        self.expect("frozen-scene", "FAIL", ["HEARTBEAT"])

    def test_a_fake_frame_counter_that_counts_its_own_reads_fails(self):
        res = self.expect("frame-counts-reads", "FAIL", ["HEARTBEAT"])
        self.assertIn("counts its own calls", res.message)

    def test_page_without_result_fails(self):
        self.expect("no-result", "FAIL", ["NO_RESULT"])

    def test_console_error_fails(self):
        self.expect("console-error", "FAIL", ["PAGE_ERRORS"])

    def test_console_error_after_the_walker_finished_fails(self):
        res = self.expect("console-error-late", "FAIL", ["PAGE_ERRORS"])
        self.assertEqual(res.result["errors"], [], "the walker had already written its result when this fired")
        self.assertEqual(res.result["lateErrors"]["count"], 1)
        self.assertIn("synthetic late console.error", res.message)

    def test_uncaught_throw_after_the_walker_finished_is_seen_by_both_channels(self):
        res = self.expect("late-throw", "FAIL", ["CONSOLE_STDERR", "PAGE_ERRORS"])
        self.assertEqual(res.result["lateErrors"]["count"], 1)

    def test_error_before_the_selftest_is_caught_from_stderr(self):
        self.expect("early-error", "FAIL", ["CONSOLE_STDERR"])

    def test_all_three_in_page_error_channels_fire_and_stderr_agrees(self):
        res = self.expect("page-errors", "FAIL", ["CONSOLE_STDERR", "PAGE_ERRORS"])
        kinds = sorted(e.split(":", 1)[0] for e in res.result["errors"])
        self.assertEqual(kinds, ["onerror", "resource", "unhandledrejection"])

    def test_multi_defect_reports_each_defect_once(self):
        self.expect("multi-defect", "FAIL", ["FIT_CLIPPED", "HEADER_OUTSIDE", "TEXT_BAD"])

    def test_text_defects_are_found_in_title_and_aria_label_too(self):
        res = self.expect("nan-in-attr", "FAIL", ["TEXT_BAD"])
        found = res.result["tabs"][0]["textBad"]
        self.assertTrue(any(item.startswith("@title") for item in found), found)
        self.assertTrue(any(item.startswith("@aria-label") for item in found), found)

    def test_a_tab_switch_that_does_nothing_is_caught_when_the_page_offers_tab_state(self):
        self.expect("settab-noop", "FAIL", ["TAB_STATE"])

    # ---- the result must come from this launch's self-test ------------------------------------
    def test_a_hard_coded_result_element_is_rejected(self):
        res = self.expect("fake-static", "FAIL", ["BAD_RESULT"])
        self.assertIn("nonce", res.message)

    # ---- canvases: judged from what the gate read itself --------------------------------------
    def test_a_blank_webgl_canvas_fails_even_when_the_page_claims_pixels_and_a_renderer(self):
        res = self.expect("canvas-gl-blank-lying", "FAIL", ["NON_BG", "NO_DRAW"])
        tab = res.result["tabs"][0]
        self.assertEqual(tab["render"]["nonBgRatio"], 0.9, "the page's own claim, which the gate ignores")
        self.assertEqual(tab["canvases"][0]["nonBgRatio"], 0.0)
        self.assertEqual(tab["draws"], 0)

    def test_a_blank_2d_canvas_fails(self):
        self.expect("canvas-2d-blank", "FAIL", ["NON_BG", "NO_DRAW"])

    def test_a_canvas_without_any_context_fails(self):
        self.expect("canvas-no-context", "FAIL", ["NON_BG"])

    def test_a_declared_canvas_that_is_hidden_fails(self):
        self.expect("canvas-declared-hidden", "FAIL", ["NON_BG"])

    def test_an_attached_helper_canvas_parked_off_screen_is_judged_like_any_visible_canvas(self):
        helper = (
            '<canvas id="h" width="10" height="10" style="position:absolute;left:-9999px"></canvas>'
            "<script>document.getElementById('h').getContext('2d');</script>"
        )
        res = self.run_page("helper-canvas.html", minimal_page(extra_body=helper), inline_selftest=True)
        self.assertEqual((res.status, res.codes), ("FAIL", ["NON_BG", "NO_DRAW"]), res.message)

    def test_a_webgl_error_left_by_the_page_fails(self):
        res = self.expect("gl-error", "FAIL", ["CONSOLE_STDERR", "GL_ERROR"])
        self.assertEqual(res.result["tabs"][0]["canvases"][0]["glErrors"], ["INVALID_ENUM"])

    def test_a_missing_relative_asset_under_inline_selftest_fails_loudly(self):
        # inline_selftest assembles the page in a temporary directory, so relative assets are not found
        # there. That must surface as a failure, never as a silent pass.
        res = self.run_page("rel.html", minimal_page(extra_body='<img src="assets/logo.png" alt="logo" width="4" height="4">'), inline_selftest=True)
        self.assertEqual((res.status, res.codes), ("FAIL", ["PAGE_ERRORS"]), res.message)
        self.assertTrue(any(e.startswith("resource:") for e in res.result["errors"]), res.result["errors"])

    def test_runs_from_a_non_ascii_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="\u65e5\u672c\u8a9e \u30c6\u30b9\u30c8-") as base:
            nested = pathlib.Path(base) / "\u5b50 dir"
            nested.mkdir()
            page = nested / "ok-stub.html"
            smoke.write_text_lf(page, smoke.assemble_page((FIXTURES / "ok-stub.html").read_text(encoding="utf-8")))
            res = smoke.run_gate(page, 1366, browser=BROWSER, tmp_root=nested)
            self.assertEqual(res.status, "PASS", res.message)
            self.assertEqual(res.leftovers, [])
            self.assertEqual([p.name for p in nested.iterdir()], ["ok-stub.html"])


@unittest.skipIf(BROWSER is None, NO_BROWSER_REASON)
class KnownLimitTests(unittest.TestCase):
    """What the gate does NOT catch. Each test pins a documented limit, so nobody mistakes a PASS for
    more than it is. If one of these starts failing because the gate got better, update the README."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="atlas-gate-limit-")
        cls.tmp = pathlib.Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def gate(self, fixture):
        return smoke.run_gate(
            FIXTURES / f"{fixture}.html", 1366, browser=BROWSER, tmp_root=self.tmp, inline_selftest=True, retries=1
        )

    def test_an_error_after_the_virtual_time_budget_is_never_seen(self):
        res = self.gate("beyond-budget")  # throws at 25000 ms; the budget is 20000 ms
        self.assertEqual((res.status, res.codes), ("PASS", []), res.message)
        self.assertEqual(smoke.load_calibration()["limits"]["virtualTimeBudgetMs"], 20000)

    def test_a_page_without_a_canvas_whose_render_once_only_bumps_a_counter_passes(self):
        res = self.gate("counter-bump-only")
        self.assertEqual((res.status, res.codes), ("PASS", []), res.message)
        self.assertEqual(res.result["tabs"][0]["canvases"], [], "no canvas, so nothing independent was read")

    def test_a_page_that_reads_its_own_url_can_forge_the_whole_result(self):
        forged = good_result(1366)
        forged["nonce"] = None
        text = (
            '<!doctype html><html><head><meta charset="utf-8"><title>forged</title></head><body>'
            "<script>var r = " + json.dumps(forged) + ";"
            "r.nonce = (/[?&]gn=([A-Za-z0-9]+)/.exec(location.search) || [])[1];"
            "var p = document.createElement('pre'); p.id = 'atlas-gate'; p.setAttribute('data-done', '1');"
            "p.textContent = JSON.stringify(r); document.body.appendChild(p);</script></body></html>"
        )
        page = self.tmp / "forged.html"
        smoke.write_text_lf(page, text)
        res = smoke.run_gate(page, 1366, browser=BROWSER, tmp_root=self.tmp, retries=1)
        self.assertEqual((res.status, res.codes), ("PASS", []), res.message)


if __name__ == "__main__":
    unittest.main()
