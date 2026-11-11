"""``png.render``: the flow of the PNG step with a launcher double (``protoui/png.py``; design A3 4.6, 5.5, D-14; task U3).

The browser is never started here: the launcher is injected, and the double of ``png_support.FakeBrowser`` records every call and writes the stand-in
picture of the SVG it is asked to open (or misbehaves the way a browser can). What is pinned: the command line, the states that are rendered, the files and
``png.json`` (no duration, no path, same bytes for the same SVGs), the cache and its key, every way a launch or a picture can go wrong (and that a bad
picture is never kept), the statuses ``skipped:*``, the budget, the concurrency, links, leftovers, and the reading side (``state_of``, ``check_files``).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_png_render
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas.protoui import png, pngread

from . import png_support as ps
from .test_tools_b3_fixes import make_link

HERE = Path(__file__).resolve().parent
NOT_FOUND = (HERE / "ui_fixtures" / "png" / "not-found.png").read_bytes()
LOGIN = "SCR-LOGIN.svg"


class Flow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dist = ps.make_dist(self.root)

    def cfg(self, **kw) -> png.PngConfig:
        kw.setdefault("browser", "fake-browser")
        return png.PngConfig(**kw)

    def render(self, browser=None, dist=None, **kw):
        browser = browser if browser is not None else ps.FakeBrowser()
        return png.render(dist or self.dist, self.cfg(**kw), browser), browser

    def one(self, name: str = "one") -> Path:
        """A dist with the SVGs of the login screen only (every state)."""
        return ps.make_dist(self.root / name, only={LOGIN, "SCR-LOGIN~error.svg", "SCR-LOGIN~loading.svg"})

    @property
    def out(self) -> Path:
        return self.dist / ".verify" / "screens"

    def listing(self, dist=None) -> list:
        folder = (dist or self.dist) / ".verify" / "screens"
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

    def work_is_empty(self, dist=None) -> bool:
        work = (dist or self.dist) / ".verify" / "work"
        return not work.exists() or not any(work.iterdir())


class WhatIsRenderedTests(Flow):
    def test_only_the_default_state_of_each_screen_is_rendered(self):
        r, b = self.render()
        self.assertEqual(r.status, "ok")
        self.assertEqual(len(b.calls), 12)
        self.assertTrue(all("~" not in name for name in b.pages), b.pages)
        self.assertEqual(len(list((self.dist / "screens").glob("*.svg"))), 35)
        self.assertEqual(len([e for e in r.entries if e.status == "ok"]), 12)
        self.assertEqual({e.variant for e in r.entries}, {"default"})

    def test_mode_all_renders_every_state_under_the_name_of_its_file(self):
        dist = ps.make_dist(self.root / "all")
        r, b = self.render(dist=dist, mode="all")
        self.assertEqual((r.status, len(b.calls)), ("ok", 35))
        names = self.listing(dist)
        self.assertIn("SCR-LOGIN~error.png", names)
        self.assertEqual(len([n for n in names if n.endswith(".png")]), 35)

    def test_mode_none_does_nothing_and_writes_nothing(self):
        r, b = self.render(mode="none")
        self.assertEqual((r.status, b.calls, r.entries), ("skipped:none", [], []))
        self.assertFalse((self.dist / ".verify").exists())
        self.assertIn("switched off", r.message)

    def test_no_screens_is_absent_and_a_wrong_mode_is_a_programming_error(self):
        empty = self.root / "empty"
        (empty / "screens").mkdir(parents=True)
        for dist in (empty, self.root / "nothing"):
            r, b = self.render(dist=dist)
            self.assertEqual((r.status, b.calls, r.entries), ("absent", [], []), dist)
            self.assertFalse((dist / ".verify").exists())
        with self.assertRaises(ValueError):
            self.render(mode="sometimes")

    def test_a_directory_or_another_file_in_screens_is_not_a_screen(self):
        (self.dist / "screens" / "folder.svg").mkdir()
        (self.dist / "screens" / "SCR-LOGIN.card.md").write_text("x", encoding="utf-8")
        r, b = self.render()
        self.assertEqual((r.status, len(b.calls)), ("ok", 12))

    def test_the_command_line_the_launcher_gets(self):
        r, b = self.render(jobs=1, timeout=17.0)
        args, timeout, scratch = b.calls[0]
        parsed = ps.parse_args(args)
        first = self.dist / "screens" / "SCR-BOOK.svg"
        self.assertEqual(args[0], "fake-browser")
        self.assertEqual(args[1:3], ["--headless=new", "--hide-scrollbars"])
        self.assertEqual((parsed["window"], parsed["scale"]), ("1280,828", "1"))
        self.assertEqual(Path(parsed["page"]).resolve(), first.resolve())
        self.assertEqual(args[-1], first.resolve().as_uri())
        self.assertEqual(parsed["shot"], str(scratch / "shot.png"))
        self.assertFalse([a for a in args if a.startswith(("--user-data-dir", "--disable-gpu"))])
        self.assertEqual(timeout, 17.0)
        self.assertTrue(all(b.scratch_seen), "the scratch directory exists when the launcher is called")
        self.assertEqual(scratch.parent.parent, self.dist / ".verify" / "work")
        self.assertTrue(scratch.parent.name.startswith("atlas-png-") and scratch.name.startswith("job-"), scratch)
        self.assertEqual(len({c[2] for c in b.calls}), 12, "one private directory per launch")

    def test_a_screen_wider_or_taller_than_1600_is_rendered_with_a_scale_factor_and_checked_at_the_scaled_size(self):
        dist = self.root / "tall" / "dist"
        (dist / "screens").mkdir(parents=True)
        groups = [ps.box("a", 24, 24, 200, 40, n=1), ps.box("b", 24, 80, 200, 40, cls="k", n=2, kind="button"), ps.box("c", 24, 2300, 200, 40, n=3)]
        (dist / "screens" / "S.svg").write_text(ps.mini_svg(1280, 2372, groups), encoding="utf-8", newline="\n")
        r, b = self.render(dist=dist)
        self.assertEqual(r.status, "ok", r.summary())
        parsed = ps.parse_args(b.calls[0][0])
        self.assertEqual((parsed["window"], parsed["scale"]), ("1280,2400", "0.666"))
        entry = r.entries[0]
        self.assertEqual((entry.width, entry.height, entry.scale), (852, 1598, 0.666))
        self.assertEqual(pngread.size_of((dist / ".verify" / "screens" / "S.png").read_bytes()), (852, 1598))


class FilesTests(Flow):
    def test_the_files_written_and_the_content_of_png_json(self):
        r, _ = self.render(label="fake 1.0")
        names = self.listing()
        self.assertEqual(len([n for n in names if n.endswith(".png")]), 12)
        self.assertEqual(sorted(set(names) - {n for n in names if n.endswith(".png")}), ["png.json"])
        raw = (self.out / "png.json").read_bytes()
        self.assertEqual(raw, r.index)
        self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)
        doc = json.loads(raw)
        self.assertEqual((doc["schema"], doc["status"], doc["mode"], doc["renderer"], doc["counts"]), ("gemarc-atlas-png/1", "ok", "default", "fake 1.0", {"ok": 12}))
        self.assertEqual([e["svg"] for e in doc["entries"]], sorted(e["svg"] for e in doc["entries"]))
        first = doc["entries"][0]
        self.assertEqual(sorted(first), ["file", "height", "probes", "scale", "screen", "status", "svg", "svgSha8", "uiSha8", "variant", "width"])
        self.assertEqual((first["svg"], first["file"], first["screen"], first["variant"], first["status"]), ("screens/SCR-BOOK.svg", "SCR-BOOK.png", "SCR-BOOK", "default", "ok"))
        self.assertEqual((first["width"], first["height"], first["scale"]), (1280, 828, 1.0))
        self.assertEqual(first["svgSha8"], png.svg_key((self.dist / first["svg"]).read_bytes()))
        self.assertNotIn("renderer", json.loads(self.render(dist=ps.make_dist(self.root / "plain"))[0].index))

    def test_every_picture_kept_passes_the_probe_of_its_own_svg_and_has_the_size_of_it(self):
        self.render()
        checked = 0
        for entry in json.loads((self.out / "png.json").read_text(encoding="utf-8"))["entries"]:
            probes = png.probes_for((self.dist / entry["svg"]).read_bytes())
            data = (self.out / entry["file"]).read_bytes()
            self.assertTrue(png.probe(data, probes.probes), entry["svg"])
            self.assertEqual(pngread.size_of(data), (entry["width"], entry["height"]))
            checked += 1
        self.assertEqual(checked, 12)

    def test_png_json_has_no_duration_no_path_and_no_machine_name(self):
        r, _ = self.render()
        text = r.index.decode("utf-8")
        for word in ("seconds", "attempts", "detail", "timeout", "scratch", "atlas-png-", "job-", "\\", ":/", str(self.root), "fake-browser"):
            self.assertNotIn(word, text, word)

    def test_the_same_svgs_give_the_same_png_json_whatever_the_scheduling_the_timing_or_the_directory(self):
        a, _ = self.render(browser=ps.FakeBrowser(delay=lambda n, p: 0.05 * (13 - n) / 12), jobs=4)
        b, _ = self.render(dist=ps.make_dist(self.root / "second"), jobs=1)
        c, _ = self.render(dist=ps.make_dist(self.root / "third"), jobs=3, retries=0, timeout=3.0, budget_seconds=500.0)
        self.assertEqual(a.index, b.index)
        self.assertEqual(a.index, c.index)

    def test_the_whole_tree_is_the_same_bytes_when_the_picture_is_the_same(self):
        self.render()
        first = {p.name: p.read_bytes() for p in self.out.iterdir()}
        r, b = self.render(refresh=True)
        self.assertEqual(len(b.calls), 12)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, first)

    def test_a_picture_that_is_not_in_the_index_is_removed_and_a_file_of_another_kind_is_not(self):
        self.out.mkdir(parents=True)
        (self.out / "old.png").write_bytes(b"x")
        (self.out / "junk.tmp").write_bytes(b"x")
        (self.out / "notes.txt").write_text("keep", encoding="utf-8")
        (self.out / "keepdir").mkdir()
        (self.out / "keepdir" / "x.png").write_bytes(b"x")
        self.render()
        names = self.listing()
        self.assertNotIn("old.png", names)
        self.assertNotIn("junk.tmp", names)
        self.assertIn("notes.txt", names)
        self.assertEqual((self.out / "keepdir" / "x.png").read_bytes(), b"x")

    def test_the_private_directories_are_removed_and_so_are_old_ones_of_the_same_kind_only(self):
        work = self.dist / ".verify" / "work"
        old, fresh, other = work / "atlas-png-OLD", work / "atlas-png-FRESH", work / "atlas-gate-x"
        for d in (old, fresh, other):
            d.mkdir(parents=True)
            (d / "f").write_text("x", encoding="utf-8")
        hour_ago = time.time() - 3600
        os.utime(old, (hour_ago, hour_ago))
        self.render()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists() and other.exists())
        self.assertEqual(sorted(p.name for p in work.iterdir()), ["atlas-gate-x", "atlas-png-FRESH"])

    def test_a_custom_scratch_root_is_used_and_left_empty(self):
        root = self.root / "custom-scratch"
        r, b = self.render(scratch_root=root)
        self.assertEqual(r.status, "ok")
        self.assertTrue(all(c[2].is_relative_to(root) for c in b.calls))
        self.assertEqual(list(root.iterdir()), [])
        self.assertFalse((self.dist / ".verify" / "work").exists())

    def test_a_scratch_directory_on_another_volume_is_handled_by_a_copy_beside_the_target(self):
        dist = self.one()
        real = os.replace
        calls = []

        def refuse_first_move(source, target):
            if str(source).endswith("shot.png") and not calls:
                calls.append(source)
                raise OSError(18, "cross-device link")
            return real(source, target)

        with mock.patch.object(os, "replace", side_effect=refuse_first_move):
            r, _ = self.render(dist=dist, jobs=1)
        self.assertEqual((r.status, len(calls)), ("ok", 1), r.summary())
        self.assertEqual(self.listing(dist), ["SCR-LOGIN.png", "png.json"])
        self.assertTrue(png.probe((dist / ".verify" / "screens" / "SCR-LOGIN.png").read_bytes(), png.probes_for((dist / "screens" / "SCR-LOGIN.svg").read_bytes()).probes))

    def test_a_picture_that_cannot_be_put_in_place_is_a_failed_picture_not_a_crash(self):
        dist = self.one()
        with mock.patch.object(png, "_put_in_place", side_effect=PermissionError("in use")):
            r, b = self.render(dist=dist, jobs=1)
        self.assertEqual((r.status, len(b.calls)), ("FAIL", 1))
        self.assertEqual(r.entries[0].reason, "write: the picture could not be put in place")
        self.assertEqual(self.listing(dist), ["png.json"])

    def test_a_scratch_directory_that_cannot_be_removed_is_reported_by_name_and_the_pictures_stay_good(self):
        dist = self.one()
        with mock.patch.object(png, "_remove_tree", return_value=False):
            r, _ = self.render(dist=dist, jobs=1)
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.leftovers and all(n.startswith(("job-", "atlas-png-")) for n in r.leftovers), r.leftovers)


class CacheTests(Flow):
    def setUp(self):
        super().setUp()
        self.first, self.first_calls = self.render()
        self.assertEqual((self.first.status, len(self.first_calls.calls)), ("ok", 12))

    def test_a_second_call_renders_nothing_and_writes_the_same_index(self):
        r, b = self.render()
        self.assertEqual((r.status, b.calls, r.rendered, r.cached), ("ok", [], 0, 12))
        self.assertEqual(r.index, self.first.index)

    def test_a_change_of_one_svg_renders_that_one_only(self):
        path = self.dist / "screens" / "SCR-HOME.svg"
        path.write_bytes(path.read_bytes() + b"<!-- changed -->\n")
        r, b = self.render()
        self.assertEqual((r.status, b.pages, r.rendered, r.cached), ("ok", ["SCR-HOME.svg"], 1, 11))
        self.assertNotEqual(r.index, self.first.index)

    def test_a_change_of_the_model_stamp_alone_redraws_nothing(self):
        path = self.dist / "screens" / "SCR-HOME.svg"
        text = path.read_bytes()
        path.write_bytes(text.replace(b' role="group"', b' data-model-sha8="abcdef12" role="group"', 1))
        self.assertNotEqual(path.read_bytes(), text)
        r, b = self.render()
        self.assertEqual((r.status, b.calls, r.cached), ("ok", [], 12))
        self.assertEqual(r.index, self.first.index, "the key ignores the stamp, so the index does too")

    def test_refresh_ignores_the_cache(self):
        r, b = self.render(refresh=True)
        self.assertEqual((r.status, len(b.calls), r.rendered, r.cached), ("ok", 12, 12, 0))

    def test_a_cached_picture_that_no_longer_passes_is_drawn_again(self):
        flat = ps.encode_png(1280, 828, [b"\xff\xff\xff" * 1280] * 828)
        (self.out / "SCR-HOME.png").write_bytes(flat)
        r, b = self.render()
        self.assertEqual((r.status, b.pages, r.rendered), ("ok", ["SCR-HOME.svg"], 1))
        self.assertTrue(png.probe((self.out / "SCR-HOME.png").read_bytes(), png.probes_for((self.dist / "screens" / "SCR-HOME.svg").read_bytes()).probes))

    def test_a_picture_that_went_missing_or_is_not_a_picture_is_drawn_again(self):
        (self.out / "SCR-HOME.png").unlink()
        (self.out / "SCR-LOGIN.png").write_bytes(b"not a picture")
        r, b = self.render()
        self.assertEqual((r.status, b.pages, r.rendered, r.cached), ("ok", ["SCR-HOME.svg", "SCR-LOGIN.svg"], 2, 10))

    def test_a_missing_or_unreadable_or_foreign_index_means_everything_is_drawn_again(self):
        for name, content in (("missing", None), ("garbage", b"{not json"), ("other schema", b'{"schema":"other/1","entries":[]}'), ("not a mapping", b"[1,2]")):
            dist = ps.make_dist(self.root / name.replace(" ", "-"))
            self.render(dist=dist)
            index = dist / ".verify" / "screens" / "png.json"
            index.unlink() if content is None else index.write_bytes(content)
            r, b = self.render(dist=dist)
            self.assertEqual((r.status, len(b.calls), r.cached), ("ok", 12, 0), name)

    def test_a_failed_redraw_removes_the_old_picture_so_that_the_directory_never_holds_a_wrong_one(self):
        path = self.dist / "screens" / "SCR-HOME.svg"
        path.write_bytes(path.read_bytes() + b"<!-- changed -->\n")
        r, b = self.render(browser=ps.FakeBrowser("blank"))
        self.assertEqual(r.status, "FAIL")
        self.assertNotIn("SCR-HOME.png", self.listing())
        self.assertEqual(r.cached, 11)
        self.assertEqual([e.screen for e in r.failed], ["SCR-HOME"])


class FailureTests(Flow):
    CASES = (
        ("start-error", "FAIL", "launch: the browser could not be started", 1),
        ("timeout", "FAIL", "launch: timeout (process tree killed)", 1),
        ("overflow", "FAIL", "launch: browser output cut at its cap", 2),
        ("exit1", "FAIL", "launch: browser exit code 1", 2),
        ("nofile", "FAIL", "launch: the browser wrote no picture", 2),
        ("raise", "FAIL", "launch: the launcher failed (RuntimeError)", 1),
        ("blank", "FAIL", "probe: ", 2),
        ("page:f6f6f6", "FAIL", "probe: ", 2),
        ("page:f6f7f9", "FAIL", "probe: ", 2),
        ("page:e9ebee", "FAIL", "probe: ", 2),
        ("wrongsize", "FAIL", "size: expected 1280x828, got 1279x828", 2),
        ("corrupt", "FAIL", "png: not a PNG file", 2),
        ("damaged", "FAIL", "png: ", 2),
        ("bytes:not_found", "FAIL", "probe: ", 2),
        ("interlaced", "skipped:probe", "probe: the picture is of a kind the reader does not decode", 1),
    )

    def test_every_way_a_launch_or_a_picture_can_fail_gives_that_status_and_keeps_nothing(self):
        for mode, status, reason, calls in self.CASES:
            with self.subTest(mode):
                dist = self.one(mode.replace(":", "-"))
                browser = ps.FakeBrowser(mode)
                browser.not_found = NOT_FOUND
                r, _ = self.render(browser=browser, dist=dist)
                self.assertEqual(r.status, status, r.summary())
                entry = r.entries[0] if len(r.entries) == 1 else None
                self.assertIsNotNone(entry, [e.svg for e in r.entries])
                self.assertEqual(len(browser.calls), calls)
                self.assertTrue(entry.reason.startswith(reason), entry.reason)
                self.assertEqual(self.listing(dist), ["png.json"], "no picture is kept, no temporary file stays")
                self.assertTrue(self.work_is_empty(dist))
                self.assertEqual(r.leftovers, [])
                rule = r.findings[0]
                self.assertEqual((rule.rule, rule.severity), ("SV-208", "ERROR" if status == "FAIL" else "INFO"))
                doc = json.loads(r.index)
                self.assertEqual(doc["entries"][0]["status"], status)
                self.assertNotIn("file", doc["entries"][0])
                self.assertTrue(doc["entries"][0]["reason"].startswith(reason))

    def test_retries_zero_means_one_launch_and_a_failed_try_is_never_the_result_of_the_next(self):
        dist = self.one("zero")
        browser = ps.FakeBrowser("exit1")
        r, _ = self.render(browser=browser, dist=dist, retries=0)
        self.assertEqual((r.status, len(browser.calls)), ("FAIL", 1))
        # a picture written by a launch that exited with code 1 must not be taken by the next try, which writes nothing
        dist = self.one("sequence")
        browser = ps.FakeBrowser(lambda n, a: "exit1" if n == 1 else "nofile")
        r, _ = self.render(browser=browser, dist=dist)
        self.assertEqual((r.status, len(browser.calls)), ("FAIL", 2))
        self.assertEqual(r.entries[0].reason, "launch: the browser wrote no picture")
        self.assertEqual(self.listing(dist), ["png.json"])

    def test_a_launch_that_fails_once_and_then_works_is_a_good_picture(self):
        dist = self.one("transient")
        browser = ps.FakeBrowser(lambda n, a: "exit1" if n == 1 else "ok")
        r, _ = self.render(browser=browser, dist=dist)
        self.assertEqual((r.status, len(browser.calls), r.entries[0].attempts), ("ok", 2, 2))
        self.assertEqual(self.listing(dist), ["SCR-LOGIN.png", "png.json"])

    def test_one_failure_among_many_fails_the_run_and_the_others_are_kept(self):
        browser = ps.FakeBrowser(lambda n, a: "blank" if a["page"].name == "SCR-HOME.svg" else "ok")
        r, _ = self.render(browser=browser, retries=0)
        self.assertEqual(r.status, "FAIL")
        self.assertEqual([e.screen for e in r.failed], ["SCR-HOME"])
        self.assertEqual(len([n for n in self.listing() if n.endswith(".png")]), 11)
        summary = r.summary()
        self.assertEqual((summary["png"], summary["rendered"], summary["failed"]), ("FAIL", 11, [{"screen": "SCR-HOME", "variant": "default", "reason": r.failed[0].reason}]))
        self.assertIn("run verify again", summary["next"])

    def test_the_summary_names_at_most_five_failures_and_stays_small(self):
        r, _ = self.render(browser=ps.FakeBrowser("blank"), retries=0)
        summary = r.summary()
        self.assertEqual((r.status, len(r.failed), len(summary["failed"])), ("FAIL", 12, 5))
        self.assertLess(len(json.dumps(summary)), 900)
        self.assertEqual(len(r.findings), 12)

    def test_what_the_launcher_double_writes_for_a_good_run_never_leaves_a_stray_temporary_file(self):
        self.render()
        self.assertEqual([n for n in self.listing() if not n.endswith(".png")], ["png.json"])
        self.assertTrue(self.work_is_empty())


class SkipTests(Flow):
    def test_no_launcher_renders_nothing_and_says_so_loudly(self):
        r = png.render(self.dist, self.cfg(), None)
        self.assertEqual(r.status, "skipped:no-launcher")
        self.assertEqual({e.status for e in r.entries}, {"skipped:no-launcher"})
        self.assertEqual(len(r.entries), 12)
        self.assertEqual(self.listing(), ["png.json"])
        self.assertIn("NOT PASSED", r.message)
        self.assertEqual(r.summary()["skipped"], 12)
        self.assertEqual({f.severity for f in r.findings}, {"INFO"})

    def test_no_browser_path_renders_nothing_and_never_calls_the_launcher(self):
        browser = ps.FakeBrowser()
        r = png.render(self.dist, png.PngConfig(), browser)
        self.assertEqual((r.status, browser.calls), ("skipped:no-browser", []))
        self.assertIn("NOT PASSED", r.message)
        self.assertIn("GEMARC_ATLAS_BROWSER", r.message)

    def test_a_cached_picture_needs_neither_a_launcher_nor_a_browser(self):
        self.render()
        r = png.render(self.dist, png.PngConfig(), None)
        self.assertEqual((r.status, r.cached), ("ok", 12))

    def test_a_changed_svg_without_a_launcher_loses_its_old_picture_and_the_run_is_not_ok(self):
        self.render()
        path = self.dist / "screens" / "SCR-HOME.svg"
        path.write_bytes(path.read_bytes() + b"<!-- changed -->\n")
        r = png.render(self.dist, self.cfg(), None)
        self.assertEqual((r.status, r.cached), ("skipped:no-launcher", 11))
        self.assertNotIn("SCR-HOME.png", self.listing())
        statuses = {e.screen: e.status for e in r.entries}
        self.assertEqual(statuses["SCR-HOME"], "skipped:no-launcher")
        self.assertEqual(statuses["SCR-LOGIN"], "ok")

    def test_an_svg_that_gives_no_sound_probe_set_is_skipped_and_never_launched(self):
        dist = self.root / "odd" / "dist"
        (dist / "screens").mkdir(parents=True)
        (dist / "screens" / "S.svg").write_text(ps.mini_svg(300, 200, []).replace('data-kind="state-label"', 'data-kind="x"'), encoding="utf-8", newline="\n")
        (dist / "screens" / "T.svg").write_text("<svg", encoding="utf-8", newline="\n")
        (dist / "screens" / "U.svg").write_text(ps.mini_svg(300, 200, [], screen="U", variant="error"), encoding="utf-8", newline="\n")
        r, b = self.render(dist=dist)
        self.assertEqual((r.status, b.calls), ("skipped:probe", []))
        self.assertEqual({e.svg: e.status for e in r.entries}, {"screens/S.svg": "skipped:probe", "screens/T.svg": "skipped:probe"},
                         "T cannot be read, so it is reported; U is an error state, so it is not rendered and not reported")
        (dist / "screens" / "V.svg").write_text('<!DOCTYPE svg [<!ENTITY a "aaaa">]>\n' + ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30)], screen="V"), encoding="utf-8", newline="\n")
        r, b = self.render(dist=dist)
        self.assertEqual(({e.svg: e.status for e in r.entries}["screens/V.svg"], b.calls), ("skipped:probe", []), "a document type declaration is never parsed")

    def test_a_budget_that_runs_out_skips_the_rest_with_the_number_of_seconds_left_as_the_timeout(self):
        clock = ps.FakeClock()
        browser = ps.FakeBrowser(clock=clock, step=4.0)
        r = png.render(self.dist, png.PngConfig(browser="fake-browser", jobs=1, budget_seconds=10.0, min_launch_seconds=3.0, clock=clock), browser)
        self.assertEqual([c[1] for c in browser.calls], [10.0, 6.0])
        self.assertEqual([e.status for e in r.entries[:2]], ["ok", "ok"])
        self.assertEqual({e.status for e in r.entries[2:]}, {"skipped:budget"})
        self.assertEqual(r.status, "skipped:budget")
        self.assertEqual(len(self.listing()), 3)
        self.assertIn("run verify again", r.message)
        self.assertEqual(r.summary()["skipped"], 10)

    def test_a_retry_is_not_started_without_the_minimum_budget(self):
        clock = ps.FakeClock()
        browser = ps.FakeBrowser("exit1", clock=clock, step=9.0)
        r = png.render(self.one(), png.PngConfig(browser="fake-browser", jobs=1, budget_seconds=10.0, min_launch_seconds=3.0, clock=clock, retries=1), browser)
        self.assertEqual((r.status, len(browser.calls)), ("FAIL", 1))
        self.assertTrue(r.entries[0].reason.startswith("launch: browser exit code 1"))

    def test_the_launcher_never_gets_more_than_the_timeout_of_the_configuration(self):
        r, b = self.render(timeout=2.5, budget_seconds=1000.0)
        self.assertEqual({c[1] for c in b.calls}, {2.5})


class ParallelTests(Flow):
    def test_jobs_is_the_number_of_launches_at_a_time(self):
        for jobs in (1, 2, 4):
            dist = ps.make_dist(self.root / f"jobs{jobs}")
            browser = ps.FakeBrowser(delay=0.08)
            r, _ = self.render(browser=browser, dist=dist, jobs=jobs)
            self.assertEqual((r.status, browser.peak), ("ok", jobs), jobs)

    def test_entries_come_back_sorted_whatever_order_the_launches_finish_in(self):
        r, _ = self.render(browser=ps.FakeBrowser(delay=lambda n, a: 0.2 if a["page"].name < "SCR-H" else 0.0), jobs=4)
        self.assertEqual([e.svg for e in r.entries], sorted(e.svg for e in r.entries))

    def test_each_launch_gets_its_own_directory_even_in_parallel(self):
        r, b = self.render(browser=ps.FakeBrowser(delay=0.05), jobs=4)
        self.assertEqual(len({c[2] for c in b.calls}), 12)


class LinkTests(Flow):
    def test_a_junction_where_the_verify_directory_should_be_is_not_followed(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "sentinel.txt").write_text("keep", encoding="utf-8")
        link = self.dist / ".verify"
        if not make_link(link, target):
            self.skipTest("no directory link could be made here")
        try:
            r, b = self.render()
            self.assertEqual((r.status, b.calls, r.entries), ("skipped:link", [], []))
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["sentinel.txt"])
            self.assertIn("not followed", r.message)
        finally:
            os.rmdir(link)

    def test_a_junction_where_the_screens_directory_should_be_is_not_followed(self):
        target = self.root / "elsewhere2"
        target.mkdir()
        shutil_dist = self.root / "linked" / "dist"
        shutil_dist.mkdir(parents=True)
        link = shutil_dist / "screens"
        if not make_link(link, self.dist / "screens"):
            self.skipTest("no directory link could be made here")
        try:
            r, b = self.render(dist=shutil_dist)
            self.assertEqual((r.status, b.calls), ("skipped:link", []))
            self.assertFalse((shutil_dist / ".verify").exists())
        finally:
            os.rmdir(link)


class ReadingSideTests(Flow):
    def setUp(self):
        super().setUp()
        self.render()
        self.index = png.read_index(self.dist)
        self.home = (self.dist / "screens" / "SCR-HOME.svg").read_bytes()

    def test_read_index_gives_the_document_or_none(self):
        self.assertEqual(self.index["schema"], "gemarc-atlas-png/1")
        self.assertIsNone(png.read_index(self.root / "nothing"))
        (self.out / "png.json").write_bytes(b"{")
        self.assertIsNone(png.read_index(self.dist))

    def test_the_state_of_a_picture_is_ok_stale_absent_failed_or_skipped(self):
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home), "ok")
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home + b"<!-- x -->"), "stale")
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home.replace(b' role="group"', b' data-model-sha8="abcdef12" role="group"', 1)), "ok")
        self.assertEqual(png.state_of(self.index, "screens/SCR-NONE.svg", self.home), "absent")
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME~error.svg", self.home), "absent", "states other than the default are not rendered")
        self.assertEqual(png.state_of(None, "screens/SCR-HOME.svg", self.home), "absent")
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home, self.dist), "ok")
        (self.out / "SCR-HOME.png").unlink()
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home, self.dist), "absent", "the entry says ok but the file is gone")
        self.assertEqual(png.state_of(self.index, "screens/SCR-HOME.svg", self.home), "ok", "without a dist the index alone is believed")
        (self.out / "SCR-HOME.png").write_bytes(ps.paint(self.home.decode("utf-8")))
        failed, _ = self.render(browser=ps.FakeBrowser("blank"), refresh=True, retries=0)
        self.assertEqual(png.state_of(png.read_index(self.dist), "screens/SCR-HOME.svg", self.home), "FAIL")
        png.render(self.dist, self.cfg(refresh=True), None)
        self.assertEqual(png.state_of(png.read_index(self.dist), "screens/SCR-HOME.svg", self.home), "skipped:no-launcher")
        self.assertEqual(failed.status, "FAIL")

    def test_check_files_is_clean_for_good_pictures_and_needs_no_browser(self):
        self.assertEqual(png.check_files(self.dist), [])
        self.assertEqual(png.check_files(self.root / "nothing"), [])

    def test_check_files_finds_a_replaced_picture_a_missing_one_a_stale_one_and_a_foreign_file(self):
        (self.out / "SCR-HOME.png").write_bytes(ps.encode_png(1280, 828, [b"\xff\xff\xff" * 1280] * 828))
        (self.out / "SCR-LOGIN.png").unlink()
        (self.out / "SCR-BOOK.png").write_bytes(b"GIF89a")
        stale = self.dist / "screens" / "SCR-FINES.svg"
        stale.write_bytes(stale.read_bytes() + b"<!-- changed -->\n")
        found = {f.path: (f.rule, f.severity) for f in png.check_files(self.dist)}
        self.assertEqual(found, {"screens/SCR-HOME.svg": ("SV-208", "ERROR"), "screens/SCR-LOGIN.svg": ("SV-208", "ERROR"), "screens/SCR-BOOK.svg": ("SV-208", "ERROR"),
                                 "screens/SCR-FINES.svg": ("SV-208", "WARN")})

    def test_check_files_ignores_entries_that_are_not_ok(self):
        png.render(self.dist, self.cfg(refresh=True), ps.FakeBrowser("blank"))
        self.assertEqual(png.check_files(self.dist), [])

    def test_check_files_knows_the_scale_of_a_scaled_picture(self):
        dist = self.root / "scaled" / "dist"
        (dist / "screens").mkdir(parents=True)
        groups = [ps.box("a", 24, 24, 200, 40, n=1), ps.box("b", 24, 80, 200, 40, cls="k", n=2, kind="button")]
        (dist / "screens" / "S.svg").write_text(ps.mini_svg(1280, 2372, groups), encoding="utf-8", newline="\n")
        self.render(dist=dist)
        self.assertEqual(png.check_files(dist), [])


if __name__ == "__main__":
    unittest.main()
