"""The Content-Security-Policy of the built page (contract-requests item 40; build check BD-004, policy part).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate_csp.py"

* ``PolicyTextTests`` (no browser): the template carries exactly one policy ``meta`` as the first element of ``head`` (before the
  self-test script), the policy is the pinned text and only allows the page's own inline script and style plus ``data:`` images,
  nothing the policy forbids is used by the runtime, the style sheet or the self-test, and BD-004 fails closed when the policy is
  missing, moved, doubled, weakened or accompanied by another ``http-equiv`` meta.
* ``RealBrowserCspTests`` (needs Edge or Chrome; SKIPS loudly without one, a skip is not a pass): a negative control proves that a
  violation shows up on the raw browser stderr, then two real built pages (the synthetic small and reference presets, sealed, with
  4D/5D runs) are loaded at 1024, 1366 and 1600 px in gate mode: zero CSP lines on the raw stderr, zero findings of the gate, six
  tabs, the WebGL tabs (3D, 4D, 5D) drawn on a canvas.

Synthetic id families only.
"""
from __future__ import annotations

import dataclasses
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
from .test_oracle_support import consistent, slice_texts

POLICY = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'"
META = f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
BROWSER = smoke.find_browser()
NEEDS_BROWSER = unittest.skipUnless(BROWSER, "NO BROWSER (Edge or Chrome) FOUND: the CSP check in a real browser was NOT run (a skip is not a pass)")
WIDTHS = (1024, 1366, 1600)
#: What Chromium writes on stderr when a policy blocks something ("Refused to load/connect/execute ... violates the following Content
#: Security Policy directive ...").
CSP_LINE = re.compile(r"Refused to|Content[ -]Security[ -]Policy|violates the following", re.IGNORECASE)


def setUpModule():
    if BROWSER is None:
        print("\n" + "=" * 100 + "\nSKIPPED, NOT PASSED: no Edge or Chrome found; the real-browser tests of test_gate_csp.py did not run.\n" + "=" * 100, file=sys.stderr)


def directives(policy: str) -> dict:
    out = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


class PolicyTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viewer = H.load_viewer()
        cls.model = synth.generate("tiny")
        cls.bundle = build.make_bundle(cls.model, viewer=cls.viewer)
        cls.page = H.fill_page(cls.viewer, cls.bundle)

    def rules(self, page, viewer=None):
        return [(i["rule"], i["message"]) for i in H.check_page(page, viewer or self.viewer, self.bundle) if i["severity"] == "ERROR"]

    def test_the_constant_the_template_and_the_built_page_agree_on_the_pinned_policy(self):
        self.assertEqual(H.CSP_POLICY, POLICY)
        self.assertEqual(self.viewer.template.count("Content-Security-Policy"), 1)
        self.assertIn("<head>\n" + META + "\n", self.viewer.template, "the policy is the first element of head")
        self.assertLess(self.page.index("Content-Security-Policy"), self.page.index("<script>"), "and it comes before the self-test script")
        self.assertLess(self.page.index("Content-Security-Policy"), self.page.index('<meta charset="utf-8">'))
        self.assertLess(self.page.index('<meta charset="utf-8">'), 1024, "the charset declaration stays inside the first 1024 bytes")
        self.assertEqual(self.rules(self.page), [])

    def test_the_policy_allows_only_inline_script_and_style_and_data_images_and_has_no_form_and_no_base(self):
        self.assertEqual(directives(POLICY), {"default-src": ["'none'"], "script-src": ["'unsafe-inline'"], "style-src": ["'unsafe-inline'"], "img-src": ["data:"],
                                                  "form-action": ["'none'"], "base-uri": ["'none'"]})
        for word in ("'unsafe-eval'", "*", "http:", "https:", "blob:", "'self'", "connect-src", "frame-src", "worker-src", "font-src"):
            self.assertNotIn(word, POLICY, word)

    def test_nothing_the_policy_forbids_is_used_by_the_code_parts(self):
        needs = {"Worker": r"\bWorker\s*\(", "SharedWorker": r"SharedWorker", "createObjectURL": r"createObjectURL", "font-face": r"@font-face",
                 "blob url": r"blob:", "image element": r"<img\b|new\s+Image\b", "link element": r"<link\b", "frame": r"<iframe\b|<frame\b|<object\b|<embed\b",
                 "audio or video": r"<audio\b|<video\b|new\s+Audio\b", "data url in css": r"url\(\s*['\"]?data:", "toDataURL": r"toDataURL",
                 "network": r"\bfetch\s*\(|XMLHttpRequest|sendBeacon|WebSocket|EventSource|importScripts", "string eval": r"\beval\s*\(|new\s+Function\b",
                 "form": r"<form\b|\.submit\s*\(|\brequestSubmit\b", "base element": r"<base\b",
                 "navigation": r"\blocation\.(href|assign|replace)\b|window\.open\s*\(|top\.location|http-equiv=.refresh"}
        for part, text in H.code_parts(self.viewer).items():
            body = text.replace("<!--ATLAS_", "") if part == "template" else text
            for name, pattern in needs.items():
                self.assertIsNone(re.search(pattern, body), f"the {part} uses {name}, which the policy blocks")

    def test_bd004_fails_closed_on_a_missing_moved_doubled_or_weakened_policy(self):
        page = self.page
        cases = {
            "missing": page.replace(META + "\n", "", 1),
            "moved after the charset": page.replace(META + "\n", "", 1).replace('<meta charset="utf-8">\n', '<meta charset="utf-8">\n' + META + "\n", 1),
            "moved after the self-test": page.replace(META + "\n", "", 1).replace("<title>", META + "\n<title>", 1),
            "doubled": page.replace(META + "\n", META + "\n" + META + "\n", 1),
            "weaker default-src": page.replace("default-src 'none'", "default-src *", 1),
            "unsafe-eval": page.replace("style-src 'unsafe-inline'", "style-src 'unsafe-inline' 'unsafe-eval'", 1),
            "connect-src added": page.replace("img-src data:", "img-src data:; connect-src *", 1),
            "an extra attribute": page.replace('<meta http-equiv="Content-Security-Policy"', '<meta nonce="x" http-equiv="Content-Security-Policy"', 1),
            "report-only instead": page.replace("Content-Security-Policy", "Content-Security-Policy-Report-Only", 1),
            "a refresh meta next to it": page.replace(META + "\n", META + '\n<meta http-equiv="refresh" content="5">\n', 1),
            "a second policy after it": page.replace(META + "\n", META + "\n" + META.replace("'none'", "*", 1) + "\n", 1),
        }
        for label, text in cases.items():
            self.assertNotEqual(text, page, label)
            with self.subTest(label=label):
                found = [rule for rule, _message in self.rules(text)]
                self.assertIn("BD-004", found)

    def test_a_template_without_the_policy_gives_a_page_that_fails_the_check(self):
        bare = dataclasses.replace(self.viewer, template=self.viewer.template.replace(META + "\n", "", 1))
        page = H.fill_page(bare, self.bundle)
        self.assertNotIn("Content-Security-Policy", page)
        self.assertIn("BD-004", [rule for rule, _message in self.rules(page, bare)])

    def test_a_page_of_every_synthetic_preset_passes_the_check_with_the_policy(self):
        for model in (synth.generate("tiny"), synth.generate("small", 2), synth.generate("reference", 2)):
            result = build.render(model, viewer=self.viewer)
            self.assertEqual([i for i in result.issues if i["severity"] == "ERROR"], [])
            self.assertTrue(result.html.startswith("<!doctype html>\n<html lang=\"en-US\">\n<head>\n" + META + "\n"), result.html[:200])


class Probe:
    """One headless launch in gate mode with the raw stderr kept (the gate's own runner discards it after filtering)."""

    def __init__(self, page: Path, width: int, tmp: Path, query: str = "gate=1"):
        calibration = smoke.load_calibration()
        offset = smoke.width_offset(calibration, BROWSER.family, width)
        self.nonce = os.urandom(8).hex()
        profile = tmp / f"profile-{width}"
        profile.mkdir()
        url = smoke.page_url(page, smoke.with_nonce(query, self.nonce))
        launch = smoke._launch(smoke.build_args(BROWSER.path, str(profile), (width + offset, 800), url, 20000), 90, env=smoke._browser_env(tmp / f"browser-tmp-{width}"))
        smoke._rmtree_retry(profile)
        self.launch, self.width, self.calibration = launch, width, calibration
        self.stderr = launch.stderr.decode("utf-8", "replace")
        self.csp_lines = [line for line in self.stderr.splitlines() if CSP_LINE.search(line)]
        self.dom = launch.stdout.decode("utf-8", "replace")
        self.origin = page.resolve().parent.as_uri() + "/"

    def findings(self):
        parsed, bad = smoke.extract_result(self.launch.stdout)
        if bad is not None:
            return parsed, [bad]
        console = smoke.console_errors_from_origin(smoke.parse_console_lines(self.launch.stderr), self.origin, self.calibration)
        return parsed, smoke.evaluate(parsed, self.width, self.calibration, console_errors=console, expected_nonce=self.nonce)


@NEEDS_BROWSER
class NegativeControlTests(unittest.TestCase):
    """The detection works: a page that breaks the policy on purpose is seen on the raw stderr (otherwise 'zero lines' proves nothing)."""

    def test_a_blocked_image_and_a_blocked_request_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result = build.render(synth.generate("tiny"))
            probe = ('<img src="http://127.0.0.1:9/blocked.png">'
                     '<script>try { new Image().src = "http://127.0.0.1:9/p.png"; } catch (e) {} '
                     'try { fetch("http://127.0.0.1:9/x").catch(function () {}); } catch (e) {}</script>')
            page = tmp / "violating.html"
            page.write_text(result.html.replace("</body>", probe + "</body>"), encoding="utf-8", newline="\n")
            run = Probe(page, 1366, tmp, query="view=1")
            self.assertGreaterEqual(len(run.csp_lines), 2, run.stderr[:1500])
            joined = " ".join(run.csp_lines)
            self.assertIn("img-src", joined)
            self.assertIn("default-src", joined)

    def test_without_the_policy_the_same_page_logs_no_policy_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result = build.render(synth.generate("tiny"))
            page = tmp / "bare.html"
            page.write_text(result.html.replace(META + "\n", "").replace("</body>", '<img src="http://127.0.0.1:9/blocked.png"></body>'), encoding="utf-8", newline="\n")
            run = Probe(page, 1366, tmp, query="view=1")
            self.assertEqual(run.csp_lines, [], "no policy, nothing is blocked by one")


@NEEDS_BROWSER
class RealBrowserCspTests(unittest.TestCase):
    """Two sealed synthetic projects (small and reference), built; every width loaded in gate mode with the raw stderr kept."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.ws = pj.get_workspace(Path(cls._tmp.name))
        cls.pages = {}
        for name, preset, seed in (("small", "small", 3), ("reference", "reference", 2)):  # these seeds draw a WebGL canvas on 3D, 4D and 5D (5D only when a comparison with a stage exists)
            model = consistent(synth.generate(preset, seed))
            assert tools.init(cls.ws, name, f"CSP {name}", "asis", ["en-US"], repos=model["meta"]["repos"])["status"] == "created"
            for slice_name, text in slice_texts(model).items():
                assert tools.put(cls.ws, name, slice_name, text)["status"] == "committed", slice_name
            reply = tools.build(cls.ws, name, docs=False)
            assert reply["status"] == "ok", reply
            cls.pages[name] = build.dist_dir(pj.project_paths(cls.ws, name)) / "index.html"

    def check_page(self, name: str):
        page = self.pages[name]
        text = page.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!doctype html>\n<html lang=\"en-US\">\n<head>\n" + META + "\n"), name)
        tmp = Path(self._tmp.name) / f"probe-{name}"
        tmp.mkdir()
        for width in WIDTHS:
            run = Probe(page, width, tmp)
            self.assertEqual(run.launch.returncode, 0, (name, width, run.stderr[:500]))
            self.assertEqual(run.csp_lines, [], (name, width))
            parsed, findings = run.findings()
            self.assertEqual([str(f) for f in findings], [], (name, width))
            self.assertEqual(parsed["innerWidth"], width, (name, width))
            tabs = [t["tab"] for t in parsed["tabs"]]
            self.assertEqual(tabs, list(build.GATE_TABS), (name, width))
            self.assertEqual(len(parsed["steps"]), build.GATE_STEPS, (name, width))
            self.assertTrue(all(s.get("ok") for s in parsed["steps"]), (name, width))
            for tab in parsed["tabs"]:
                if tab["tab"] in ("3D", "4D", "5D"):
                    self.assertTrue([c for c in tab.get("canvases", []) if c.get("kind") in smoke.GL_KINDS], (name, width, tab["tab"], "a WebGL canvas was drawn"))

    def test_the_small_page_has_zero_policy_violations_and_the_gate_passes_at_three_widths(self):
        self.check_page("small")

    def test_the_reference_page_has_zero_policy_violations_and_the_gate_passes_at_three_widths(self):
        self.check_page("reference")


if __name__ == "__main__":
    unittest.main()
