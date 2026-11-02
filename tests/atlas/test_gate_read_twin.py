"""The HTML twins in a real browser (design A3 6.5, milestone R2): five twins of a synthetic project load in headless Edge (or Chrome) with zero
Content-Security-Policy violations and the browser's own DOM carries the very text of the Markdown page.

Run (needs Edge or Chrome, SKIPS loudly without one: a skip is not a pass; take the Edge lock of the lane protocol first):
    PYTHONPATH=src python -m unittest tests.atlas.test_gate_read_twin

The launch is the one of ``test_gate_csp.Probe`` (``smoke._launch`` with the hardened environment, the process tree killed on a timeout); nothing in
this file starts a process of its own. ``Probe`` dumps the DOM after the load (``--dump-dom``) and keeps the raw stderr, where Chromium writes a line for
every request or script a policy refuses. The file is named ``test_gate_*`` so that the pattern of the fast suite (``test_[!g]*.py``) leaves it to the
gate run, which holds the Edge lock.

* ``NegativeControlTests``: a twin that asks for an image of a network address is refused by the policy and the refusal shows on the stderr (without it
  "zero lines" would prove nothing).
* ``RealBrowserTwinTests``: START, a full page of the structure, a case page, the quality page and a dimension INDEX of the ``reference`` preset at the
  desktop width, two of them at a phone width too: exit code 0, zero policy lines, ``visible_html`` of the browser's DOM equals ``visible_md`` of the page
  (the parsed text is compared, not the bytes: Chromium inserts ``<tbody>`` and normalises the serialisation).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.atlas.reading import names as N
from gemarc_mcp.atlas.reading import render_html as RH
from gemarc_mcp.atlas.reading.parse import parse_page

from . import read_support as RS
from .test_gate_csp import NEEDS_BROWSER, Probe, setUpModule  # noqa: F401  (setUpModule: the loud banner of a skipped module)

LANG = "zh-CN"
PAGES = ("START.md", "read/2D/S02.md", "read/4D/C01.md", "read/QA/Q01.md", "read/2D/INDEX.md")
PHONE_PAGES = ("START.md", "read/2D/S02.md")
DESKTOP, PHONE = 1280, 390


@NEEDS_BROWSER
class NegativeControlTests(unittest.TestCase):
    def test_a_twin_that_asks_for_a_network_image_is_refused_and_the_refusal_is_seen(self):
        b = RS.built("small", LANG, 1, html=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            page = tmp / "violating.html"
            text = b.result.files["site/START.html"].decode("utf-8")
            page.write_text(text.replace("</body>", '<img src="http://127.0.0.1:9/blocked.png"></body>'), encoding="utf-8", newline="\n")
            run = Probe(page, DESKTOP, tmp, query="")
            self.assertGreaterEqual(len(run.csp_lines), 1, run.stderr[:1500])
            self.assertIn("img-src", " ".join(run.csp_lines))

    def test_a_clean_twin_logs_no_policy_line_at_all(self):
        b = RS.built("small", LANG, 1, html=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            page = tmp / "clean.html"
            page.write_bytes(b.result.files["site/START.html"])
            run = Probe(page, DESKTOP, tmp, query="")
            self.assertEqual(run.launch.returncode, 0, run.stderr[:600])
            self.assertEqual(run.csp_lines, [], run.stderr[:600])


@NEEDS_BROWSER
class RealBrowserTwinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.root = Path(cls._tmp.name)
        cls.built = RS.built("reference", LANG, 2, html=True)
        RS.write_dist(cls.built, cls.root / "dist", extras=False)

    def check(self, md_path: str, width: int):
        twin = N.twin_path(md_path)
        page = self.root / "dist" / twin
        self.assertTrue(page.is_file(), twin)
        work = self.root / f"probe-{width}-{twin.replace('/', '_')}"
        work.mkdir()
        run = Probe(page, width, work, query="")
        self.assertEqual(run.launch.returncode, 0, (twin, run.stderr[:500]))
        self.assertEqual(run.csp_lines, [], (twin, run.csp_lines[:3]))
        want = RH.visible_md(self.built.result.files[md_path].decode("utf-8"))
        got = RH.visible_html(run.dom)
        self.assertGreater(len(got), 10, (twin, "the browser dumped an empty page"))
        self.assertEqual(got, want, (twin, RH.analyse(run.dom).title))
        meta = {m["name"]: m["content"] for m in RH.analyse(run.dom).metas if "name" in m}
        page_id = parse_page(LANG, md_path, self.built.result.files[md_path].decode("utf-8")).pid
        self.assertEqual((meta["atlas-page"], meta["atlas-schema"]), (page_id, "gemarc-atlas-read/1"))

    def test_five_twins_load_with_zero_policy_violations_and_the_text_of_the_page(self):
        for md_path in PAGES:
            with self.subTest(page=md_path, width=DESKTOP):
                self.check(md_path, DESKTOP)

    def test_two_twins_at_a_phone_width(self):
        for md_path in PHONE_PAGES:
            with self.subTest(page=md_path, width=PHONE):
                self.check(md_path, PHONE)


if __name__ == "__main__":
    unittest.main()
