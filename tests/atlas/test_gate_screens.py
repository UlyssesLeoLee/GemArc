"""The screens in a real browser (design A3 U2: "open every SVG of the 12-screen sample in headless Edge"): no text leaves its box, no mark covers another
widget, every jump is a click target, every SVG loads as an image, no console error. Measurements and numbers: ``screens_edge.py``.

This test starts Edge, so it only runs when ``GEMARC_SCREENS_EDGE=1`` is set, and whoever runs it must hold the edge lock of the machine (the lock directory
``locks/edge`` of the shared scratch area; several agents share one browser and the timing of the gate tests suffers under load). Without the variable it is
skipped, with a message that says so, so that a full suite run does not start a browser by the side door.

Run: GEMARC_SCREENS_EDGE=1 PYTHONPATH=src python -m unittest tests.atlas.test_gate_screens
"""
from __future__ import annotations

import os
import unittest

from . import screens_edge, screens_fixtures as fx


def svgs_of(*names: str) -> tuple:
    texts, pages = {}, {}
    for name in names:
        built = fx.built(name)
        for path, data in built.files.items():
            if path.endswith(".svg"):
                texts[f"{name}:{path[len('screens/'):-4]}"] = data.decode("utf-8")
            elif path.startswith("site/screens/") and path.endswith(".html"):
                pages[f"{name}:{path}"] = data.decode("utf-8")
    return texts, pages


@unittest.skipUnless(os.environ.get("GEMARC_SCREENS_EDGE") == "1", "starts Edge: set GEMARC_SCREENS_EDGE=1 and hold the edge lock (see the module doc)")
class ScreensInEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svgs, cls.pages = svgs_of("sample12", "showcase")
        cls.probe = screens_edge.run(cls.svgs, cls.pages)
        if cls.probe["status"] == "SKIP":
            raise unittest.SkipTest(cls.probe["reason"])

    def test_the_browser_ran_and_measured_every_state_and_every_gallery(self):
        self.assertEqual(self.probe["status"], "OK", self.probe.get("reason"))
        a = screens_edge.analyse(self.probe["result"])
        states = len(self.svgs)
        galleries_states = sum(f.count("<figure ") for f in self.pages.values())
        self.assertEqual(a["svgs"], states + galleries_states, "every SVG in the page: the files and the inlined states of the galleries")
        self.assertEqual(a["images"], states, "every SVG file is loaded as an image")
        self.assertGreater(a["texts"], 400)
        self.assertGreater(a["marks"], 300)
        self.assertGreater(a["targets"], 100)

    def test_no_text_leaves_the_box_of_its_group_and_the_width_model_holds(self):
        a = screens_edge.analyse(self.probe["result"])
        self.assertEqual(a["overflow"], [], a["overflow"][:3])
        self.assertLessEqual(a["ratio_max"], 1.2, "a line is at most 12 percent wider than the model (short runs of capitals), the boxes carry the slack")
        self.assertGreater(a["ratio_min"], 0.6)

    def test_no_mark_covers_the_box_or_the_text_of_another_widget(self):
        a = screens_edge.analyse(self.probe["result"])
        self.assertEqual((a["marks_over_box"], a["marks_over_text"]), ([], []))

    def test_every_widget_with_a_jump_is_the_top_element_at_its_centre(self):
        a = screens_edge.analyse(self.probe["result"])
        self.assertEqual(a["targets_missed"], [], a["targets_missed"][:3])

    def test_every_svg_file_is_well_formed_and_there_is_no_console_error(self):
        a = screens_edge.analyse(self.probe["result"])
        self.assertEqual(a["images_failed"], [])
        self.assertEqual(a["errors"], [])
        self.assertEqual(self.probe["console_errors"], [])

    def test_the_hostile_screen_is_measured_too(self):
        from gemarc_mcp.atlas.protoui import build as bld
        hostile = bld.build_screens(fx.model_of(fx.hostile_text()), None, fx.CFG)
        svgs = {f"hostile:{p}": d.decode("utf-8") for p, d in hostile.files.items() if p.endswith(".svg")}
        pages = {f"hostile:{p}": d.decode("utf-8") for p, d in hostile.files.items() if p.startswith("site/")}
        run = screens_edge.run(svgs, pages)
        self.assertEqual(run["status"], "OK", run.get("reason"))
        a = screens_edge.analyse(run["result"])
        self.assertEqual((a["overflow"], a["targets_missed"], a["images_failed"], a["errors"], run["console_errors"]), ([], [], [], [], []))


if __name__ == "__main__":
    unittest.main()
