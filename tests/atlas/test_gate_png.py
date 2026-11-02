"""The PNG step in a real browser (``protoui/png.py``; design A3 4.6, D-14; check SV-208; task U3, acceptance T-S4): the twelve default-state pictures of the
sample pass, a wrong URL turns the result red, a hung browser is killed with every process it started and leaves no file behind, a tall screen is rendered
scaled.

This test starts Edge, so it only runs when ``GEMARC_PNG_EDGE=1`` is set, and whoever runs it must hold the edge lock of the machine (the lock directory
``locks/edge`` of the shared scratch area; several agents share one browser and timing suffers under load). Without the variable it is skipped with a
message that says so; with the variable but without a browser it is skipped loudly (a skip is not a pass).

The launcher below is the thin one of this test: it adds ``--disable-gpu`` and a profile directory inside ``scratch`` and calls the hardened launch of
``gate/smoke.py`` (process tree kill, redirected temporary directory, output caps), which is what ``launch_capture`` will be.

Run: GEMARC_PNG_EDGE=1 PYTHONPATH=src python -m unittest tests.atlas.test_gate_png
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gemarc_mcp.atlas.gate import smoke
from gemarc_mcp.atlas.protoui import png, pngread

from . import png_support as ps

ENABLED = os.environ.get("GEMARC_PNG_EDGE") == "1"
EDGE_LIKE = re.compile(r"cv_debug|Importer|msedge|^edge|^BIT.*\.tmp$", re.I)


def edge_launcher(args, timeout, scratch):
    """``launch_capture(args, timeout, scratch)`` as this test needs it: the profile and the temporary directory of the browser live inside ``scratch``."""
    scratch = Path(scratch)
    full = [args[0], "--disable-gpu", f"--user-data-dir={scratch / 'profile'}", *args[1:]]
    return smoke._launch(full, timeout, env=smoke._browser_env(scratch / "tmp"))


def processes_with(marker: str) -> list:
    """The command lines of the browser processes (and crash handlers) that mention ``marker``."""
    if os.name == "nt":
        command = "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | ForEach-Object { $_.CommandLine }"
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=90).stdout
    else:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=30).stdout
    return [line for line in out.splitlines() if marker in line]


def wait_for(predicate, seconds: float, step: float = 0.5):
    end = time.monotonic() + seconds
    value = predicate()
    while not value and time.monotonic() < end:
        time.sleep(step)
        value = predicate()
    return value


@unittest.skipUnless(ENABLED, "starts Edge: set GEMARC_PNG_EDGE=1 and hold the edge lock (see the module doc)")
class PngInRealEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = smoke.find_browser()
        if cls.browser is None:
            raise unittest.SkipTest(smoke.no_browser_message())

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def cfg(self, **kw) -> png.PngConfig:
        kw.setdefault("browser", self.browser.path)
        kw.setdefault("label", f"{self.browser.family} {self.browser.version}")
        return png.PngConfig(**kw)

    def test_the_twelve_default_state_pictures_of_the_sample_pass_and_the_cache_then_holds(self):
        dist = ps.make_dist(self.root)
        launches = []

        def counted(args, timeout, scratch):
            launches.append(args)
            return edge_launcher(args, timeout, scratch)

        r = png.render(dist, self.cfg(retries=0, budget_seconds=120.0), counted)
        self.assertEqual((r.status, r.rendered, r.cached, len(r.entries), len(launches)), ("ok", 12, 0, 12, 12), r.summary())
        self.assertEqual({e.attempts for e in r.entries}, {1})
        self.assertEqual({e.variant for e in r.entries}, {"default"})
        out = dist / ".verify" / "screens"
        self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([f"{e.screen}.png" for e in r.entries] + ["png.json"]))
        for e in r.entries:
            data = (out / e.file).read_bytes()
            probes = png.probes_for((dist / e.svg).read_bytes())
            self.assertEqual(pngread.size_of(data), (e.width, e.height), e.svg)
            self.assertEqual((e.width, e.height), probes.size, e.svg)
            self.assertTrue(png.probe(data, probes.probes), e.svg)
            self.assertGreaterEqual(e.probes, png.MIN_PROBES)
        self.assertEqual(png.check_files(dist), [])
        again = png.render(dist, self.cfg(), counted)
        self.assertEqual((again.status, again.cached, len(launches), again.index), ("ok", 12, 12, r.index))
        self.assertEqual(list((dist / ".verify" / "work").iterdir()), [])

    def test_a_wrong_url_is_red_in_every_try_and_keeps_no_picture(self):
        dist = ps.make_dist(self.root, only={"SCR-LOGIN.svg", "SCR-HOME.svg", "SCR-STOCK.svg"})

        def wrong(args, timeout, scratch):
            args = list(args)
            args[-1] = "file:///atlas-fixture/missing/" + Path(ps.parse_args(args)["page"]).name
            return edge_launcher(args, timeout, scratch)

        r = png.render(dist, self.cfg(retries=1), wrong)
        self.assertEqual(r.status, "FAIL", r.summary())
        self.assertEqual(len(r.failed), 3)
        for e in r.failed:
            self.assertTrue(e.reason.startswith("probe: "), e.reason)
            self.assertEqual(e.attempts, 2)
        self.assertEqual(sorted(p.name for p in (dist / ".verify" / "screens").iterdir()), ["png.json"])
        self.assertEqual(r.findings[0].severity, "ERROR")

    def test_the_picture_of_a_wrong_url_has_the_right_size_and_exit_code_zero_and_only_its_pixels_reject_it(self):
        dist = ps.make_dist(self.root, only={"SCR-LOGIN.svg"})
        svg = dist / "screens" / "SCR-LOGIN.svg"
        probes = png.probes_for(svg.read_bytes())
        scratch = self.root / "direct"
        scratch.mkdir()
        args = png.screenshot_args(self.browser.path, svg, scratch / "shot.png", 1280, 828, 1.0)
        args[-1] = "file:///atlas-fixture/missing/SCR-LOGIN.svg"
        launch = edge_launcher(args, 30.0, scratch)
        self.assertEqual((launch.returncode, launch.timed_out), (0, False))
        data = (scratch / "shot.png").read_bytes()
        self.assertEqual(pngread.size_of(data), probes.size)
        self.assertFalse(png.probe(data, probes.probes))
        report = png.probe_report(data, probes.probes, probes.size)
        self.assertEqual(report.error, "")
        right = scratch / "right"
        right.mkdir()
        good = png.screenshot_args(self.browser.path, svg, right / "shot.png", 1280, 828, 1.0)
        self.assertEqual(edge_launcher(good, 30.0, right).returncode, 0)
        self.assertTrue(png.probe((right / "shot.png").read_bytes(), probes.probes))

    def test_a_live_browser_is_seen_by_the_process_listing_and_the_tree_kill_removes_it(self):
        """The control of the timeout test: the listing is only worth something if it sees a browser that is running."""
        scratch = self.root / "control-marker"
        scratch.mkdir()
        marker = scratch.name
        args = [self.browser.path, "--headless=new", "--disable-gpu", f"--user-data-dir={scratch / 'profile'}", "about:blank"]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=smoke._browser_env(scratch / "tmp"),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            seen = wait_for(lambda: processes_with(marker), 30.0)
            self.assertTrue(seen, "the listing must see the browser that is running, or its silence after a kill proves nothing")
            self.assertIsNone(process.poll(), "headless Edge without an action keeps running until it is killed")
        finally:
            smoke._kill_tree(process)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (process.stdout, process.stderr):
                pipe.close()
        self.assertEqual(wait_for(lambda: not processes_with(marker), 20.0), True, processes_with(marker))

    def test_a_hung_browser_is_killed_with_every_process_it_started_and_nothing_stays_behind(self):
        dist = ps.make_dist(self.root, only={"SCR-LOGIN.svg"})
        scratch_root = self.root / "scratch-root"
        seen_dirs = []
        temp_before = set(os.listdir(tempfile.gettempdir()))

        def hang(args, timeout, scratch):
            seen_dirs.append(Path(scratch))
            return edge_launcher([a for a in args if not a.startswith("--screenshot=")], timeout, scratch)   # no screenshot: the browser stays up

        started = time.monotonic()
        r = png.render(dist, self.cfg(timeout=4.0, retries=1, scratch_root=scratch_root), hang)
        waited = time.monotonic() - started
        self.assertEqual(r.status, "FAIL", r.summary())
        self.assertEqual(r.entries[0].reason, "launch: timeout (process tree killed)")
        self.assertEqual((len(seen_dirs), r.entries[0].attempts), (1, 1), "a timeout is not retried")
        self.assertLess(waited, 40.0)
        marker = seen_dirs[0].parent.name
        self.assertTrue(marker.startswith("atlas-png-"), marker)
        left = wait_for(lambda: not processes_with(marker), 20.0)
        self.assertTrue(left, f"browser processes still alive after the kill: {processes_with(marker)[:2]}")
        self.assertEqual(list(scratch_root.iterdir()), [], "the render scratch directory is empty")
        self.assertEqual(r.leftovers, [])
        self.assertEqual(sorted(p.name for p in (dist / ".verify" / "screens").iterdir()), ["png.json"])
        new = [n for n in set(os.listdir(tempfile.gettempdir())) - temp_before if EDGE_LIKE.search(n)]
        self.assertEqual(new, [], "the browser's own temporary files went into the scratch directory, not into the real temporary directory")

    def test_a_tall_screen_and_a_narrow_very_tall_one_are_rendered_scaled_and_checked_at_that_size(self):
        for width, canvas, expected_scale in ((1280, 2372, 0.666), (390, 5000, 0.5)):
            dist = self.root / f"tall{width}" / "dist"
            (dist / "screens").mkdir(parents=True)
            groups = [ps.box("a", 24, 24, 200, 40, n=1, mark=True), ps.box("b", 24, 80, 200, 40, cls="k", n=2, kind="button"),
                      ps.box("c", 24, canvas - 80, 200, 40, n=3), ps.box("d", 150, canvas // 2, 200, 80, cls="s", n=4, kind="button")]
            (dist / "screens" / "S.svg").write_text(ps.mini_svg(width, canvas, groups), encoding="utf-8", newline="\n")
            r = png.render(dist, self.cfg(retries=0), edge_launcher)
            self.assertEqual(r.status, "ok", (width, r.summary(), r.entries[0].reason))
            e = r.entries[0]
            self.assertEqual(e.scale, expected_scale)
            self.assertEqual((e.width, e.height), png.expected_size(width, canvas + 28, expected_scale))
            data = (dist / ".verify" / "screens" / "S.png").read_bytes()
            self.assertEqual(pngread.size_of(data), (e.width, e.height))
            self.assertEqual(png.check_files(dist), [])

    def test_four_launches_at_a_time_do_not_share_state(self):
        dist = ps.make_dist(self.root, only={"SCR-BOOK.svg", "SCR-BORROW.svg", "SCR-FINES.svg", "SCR-HOME.svg", "SCR-LOGIN.svg", "SCR-NOTICE.svg", "SCR-RENEW.svg", "SCR-RESET.svg"})
        active, peak, lock = [0], [0], threading.Lock()

        def watched(args, timeout, scratch):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                return edge_launcher(args, timeout, scratch)
            finally:
                with lock:
                    active[0] -= 1

        r = png.render(dist, self.cfg(jobs=4, retries=0, budget_seconds=120.0), watched)
        self.assertEqual((r.status, len(r.entries)), ("ok", 8), r.summary())
        self.assertGreaterEqual(peak[0], 2, "the launches overlapped")
        self.assertLessEqual(peak[0], 4)


if __name__ == "__main__":
    unittest.main()
