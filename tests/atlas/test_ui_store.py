"""The interface store (``protoui/store.py``): put (replace and patch), red drafts, history, counters, routing, the write fence (AC-17).

Real files in a temporary workspace; the 5D side is the synthetic lending desk. Every test that needs a directory link makes a REAL one (a
junction on Windows) and SKIPS cleanly where none can be made. Synthetic ids only.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_store.py"
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import expand as ex5
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import slices as sl
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.protoui import atl, expand as ex, model as um
from gemarc_mcp.atlas.protoui import store
from gemarc_mcp.paths import GemArcError

from . import ui_synth
from .test_tools_b3_fixes import make_link
from .test_ui_expand import LOGIN_SHA8

DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
NAME = "ui-10-login"
LOGIN = ui_synth.login_text()
BAD = LOGIN.replace("kind=input parent=form bind=email", "kind=inputt parent=form bind=email")


def verdict_for_model(model: dict, screen: str = "SCR-LOGIN") -> str:
    """A verdict slice that the verdict rules (UV-001 .. UV-008) accept for ``screen`` of ``model``: one differing label, every other check point
    looked at, the current stamp. Its basis points at an alias that has no root, so the basis cells are skipped (INFO), not verified."""
    stamp = um.screen_hashes(model)[screen]
    points = [c for c in um.checkpoints(model) if c.screen == screen]
    target = next(c for c in points if c.ref.endswith("@label"))
    return ("#atlas-ui 1\n## conform: screen | verdict | basis | opts\n"
            f"{screen} | differs | fe:src/login.tsx:1 | sha={stamp} checked={len(points)} repo=fe\n"
            "## diffs: ref | kind | note | opts\n"
            f"{target.ref} | differs | the text differs | expected={target.value} found=Sign-in basis=fe:src/login.tsx:34\n")


VERDICT = verdict_for_model(ex.expand_ui([atl.parse_atl(LOGIN, NAME + ".atl")]).model)


class UiProject(unittest.TestCase):
    """A temporary workspace with writes allowed, one project, optionally the 5D desk."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ws = pj.get_workspace(self.root)
        self.assertEqual(tools.init(self.ws, "desk", "Library lending desk", "asis", ["zh-CN"], repos=["main"])["status"], "created")
        self.paths = pj.project_paths(self.ws, "desk")
        self.cfg = pj.load_config(self.paths)
        self.up = store.UiPaths.of(self.paths)

    def desk(self) -> None:
        for name in ("00-space", "10-structure", "20-scenarios"):
            self.assertEqual(tools.put(self.ws, "desk", name, (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8"))["status"], "committed")

    def five(self):
        files = sl.list_slices(self.paths)
        return ex5.expand_sources({f.fname: f.text() for f in files}, self.cfg.meta()).model if files else None

    def put(self, name: str, text: str, **kw) -> "store.UiPutOutcome":
        kw.setdefault("five", self.five())
        kw.setdefault("root", self.root)
        kw.setdefault("verdict_roots", {})      # a verdict's basis cells are not read from a root of the machine that runs the test
        return store.put_slice(self.up, self.cfg.meta(), name, text, **kw)

    def files(self) -> set:
        return {str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*") if p.is_file()}

    def dirs(self) -> set:
        return {str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*") if p.is_dir()}


class PutTests(UiProject):
    def test_a_clean_slice_commits_to_atlas_ui_with_its_hashes_and_counts(self):
        self.desk()
        out = self.put(NAME, LOGIN)
        self.assertEqual((out.status, out.slice, out.red, out.unchanged, out.previous, out.family), ("committed", NAME, False, False, "", "ui"))
        self.assertEqual(out.sha8, sl.sha8(LOGIN))
        self.assertEqual(out.ui_sha8, um.ui_sha8(ex.expand_ui([atl.parse_atl(LOGIN, "ui-10-login.atl")], None, self.cfg.meta()).model))
        self.assertNotEqual(out.ui_sha8, LOGIN_SHA8, "the model carries the project meta")
        self.assertEqual(out.counts, {"screens": 2, "variants": 3, "widgets": 8, "nav": 2})
        self.assertTrue(out.check.ok)
        self.assertEqual((self.paths.atlas / "ui" / f"{NAME}.atl").read_bytes(), sl.normalise(LOGIN).encode("utf-8"))
        self.assertEqual(store.list_slices(self.up)[0].name, NAME)
        self.assertEqual(store.read_counters(self.up), {"puts": 1, "verdictPuts": 0, "red": {}})

    def test_the_same_text_is_unchanged_and_makes_no_history(self):
        self.put(NAME, LOGIN)
        again = self.put(NAME, LOGIN + "\n\n")
        self.assertEqual((again.status, again.unchanged, again.previous), ("committed", True, sl.sha8(LOGIN)))
        self.assertEqual(store.history_of(self.up, NAME), [])

    def test_a_change_archives_the_previous_version_and_ten_are_kept(self):
        self.put(NAME, LOGIN)
        for n in range(12):
            self.put(NAME, LOGIN + f"// edit {n}\n")
        history = store.history_of(self.up, NAME)
        self.assertEqual(len(history), store.HISTORY_CAP)
        self.assertTrue(all(p.name.startswith(f"{NAME}.") and p.suffix == ".atl" for p in history))
        self.assertEqual(sl.sha8(history[-1].read_text(encoding="utf-8")), history[-1].name.split(".")[1])

    def test_an_error_commits_nothing_and_keeps_the_whole_text_as_the_red_draft(self):
        out = self.put(NAME, BAD)
        self.assertEqual((out.status, out.red, out.committed), ("invalid", True, False))
        self.assertEqual([d.rule for d in out.check.errors], ["ST-003"])
        self.assertFalse((self.paths.atlas / "ui" / f"{NAME}.atl").exists())
        self.assertEqual(store.find_red(self.up, NAME).path.read_bytes(), sl.normalise(BAD).encode("utf-8"))
        self.assertEqual(store.read_counters(self.up)["red"], {NAME: 1})
        self.assertTrue(store.ui_present(self.up))

    def test_a_good_put_clears_the_red_draft_and_the_counter(self):
        self.put(NAME, BAD)
        out = self.put(NAME, LOGIN)
        self.assertEqual((out.status, out.cleared_red), ("committed", True))
        self.assertIsNone(store.find_red(self.up, NAME))
        self.assertEqual(store.read_counters(self.up), {"puts": 2, "verdictPuts": 0, "red": {}})

    def test_a_red_put_leaves_the_committed_version_alone(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, BAD)
        self.assertTrue(out.red)
        self.assertEqual((self.paths.atlas / "ui" / f"{NAME}.atl").read_bytes(), sl.normalise(LOGIN).encode("utf-8"))
        self.assertEqual(out.previous, sl.sha8(LOGIN))

    def test_the_same_red_text_twice_writes_the_draft_once_and_counts_two_rounds(self):
        self.put(NAME, BAD)
        stamp = store.find_red(self.up, NAME).path.stat().st_mtime_ns
        self.put(NAME, BAD)
        self.assertEqual(store.find_red(self.up, NAME).path.stat().st_mtime_ns, stamp)
        self.assertEqual(store.read_counters(self.up)["red"], {NAME: 2})

    def test_a_warning_commits(self):
        out = self.put(NAME, LOGIN.replace("route=/home", "route=/login"))
        self.assertEqual(out.status, "committed")
        self.assertEqual([d.rule for d in out.check.warnings], ["UX-006"])

    def test_the_put_in_slice_mode_checks_against_the_committed_rest(self):
        first = LOGIN.split("## widgets:")[0]
        self.assertEqual(self.put("ui-10-a", first).status, "committed")
        second = "#atlas-ui 1\n## widgets: local | label | opts\n@set screen=SCR-HOME\nbar | 借阅台 | kind=header\nnote | 说明 | kind=text parent=cardx\n"
        out = self.put("ui-20-b", second)
        self.assertEqual(out.status, "committed")
        self.assertEqual(sorted(out.check.pending), ["widget of this screen"])
        self.assertNotIn("UX-006", {d.rule for d in out.check.diagnostics})

    def test_the_links_are_checked_when_a_5d_model_exists(self):
        self.desk()
        out = self.put(NAME, LOGIN.replace("req=RQ-1\nSCR-HOME", "req=RQ-1\nSCR-HOME"))
        self.assertEqual([d.rule for d in out.check.diagnostics], [])
        out = self.put("ui-20-b", "#atlas-ui 1\n## screens: id | title | purpose | opts\nSCR-X | X | x. | actor=svc.loan route=/x\n")
        self.assertEqual([(d.rule, d.severity) for d in out.check.diagnostics if d.rule == "UX-206"], [("UX-206", "ERROR")])
        self.assertTrue(out.red)

    def test_without_a_5d_model_the_links_are_one_info_and_the_put_commits(self):
        out = self.put(NAME, LOGIN)
        self.assertEqual([(d.rule, d.severity) for d in out.check.diagnostics], [("UX-206", "INFO")])
        self.assertEqual(out.status, "committed")

    def test_the_diagnostics_render_through_the_shared_renderer(self):
        text = self.put(NAME, BAD).check.render("text")
        self.assertIn("E ST-003", text)
        self.assertLess(len(text.encode("utf-8")), 2800)


class PatchTests(UiProject):
    def test_a_patch_repairs_the_red_draft_with_the_changed_rows_only(self):
        self.put(NAME, BAD)
        out = self.put(NAME, "## widgets: local | label | opts\n@set screen=SCR-LOGIN\nemail | 邮箱 | kind=input parent=form bind=email\n", mode="patch")
        self.assertEqual((out.status, out.patch.replaced, out.patch.added), ("committed", 1, 0))
        self.assertNotIn("kind=inputt", out.text)
        self.assertIsNone(store.find_red(self.up, NAME))

    def test_a_row_with_the_identity_of_a_base_row_replaces_it_and_keeps_its_set_lines(self):
        self.put(NAME, LOGIN)
        with self.assertRaises(store.UiError) as ctx:
            self.put(NAME, "pwd | 口令 | kind=input parent=form bind=password\n", mode="patch")
        self.assertEqual(ctx.exception.code, store.E_PATCH)   # a patch row needs its section header
        out = self.put(NAME, "## widgets: local | label | opts\n@set screen=SCR-LOGIN\npwd | 口令 | kind=input parent=form bind=password\n", mode="patch")
        self.assertEqual((out.status, out.patch.replaced, out.patch.added), ("committed", 1, 0))
        self.assertIn("口令", out.text)
        self.assertNotIn("| 密码 |", out.text)
        self.assertEqual(out.text.count("pwd |"), 1)

    def test_a_delete_line_removes_a_row_by_its_identity(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, "## widgets: local | label | opts\n@set screen=SCR-LOGIN\n-err\n", mode="patch")
        self.assertEqual((out.patch.deleted, out.patch.missed), (1, []))
        self.assertNotIn("err |", out.text)
        self.assertNotIn("SCR-LOGIN.err", json.dumps(out.check.expansion.model))

    def test_deletes_by_the_identities_of_the_other_sections(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, "## variants: screen | name | note | opts\n-SCR-HOME | empty\n## nav: from | to | label | opts\n-SCR-HOME.logout | SCR-LOGIN\n", mode="patch")
        self.assertEqual(out.patch.deleted, 2)
        self.assertNotIn("| empty |", out.text)
        self.assertNotIn("退出登录", out.text)

    def test_a_delete_that_matches_nothing_is_reported(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, "## screens: id | title | purpose | opts\n-SCR-GONE\n", mode="patch")
        self.assertEqual(out.patch.missed, ["SCR-GONE"])
        self.assertTrue(out.unchanged)

    def test_a_screen_row_and_a_nav_row_are_replaced_by_identity(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, "## screens: id | title | purpose | opts\nSCR-HOME | 我的借阅 | 新的用途。 | kind=page frame=desktop route=/home req=RQ-1\n"
                             "## nav: from | to | label | opts\nSCR-HOME.logout | SCR-LOGIN | 退出 | on=tap\n", mode="patch")
        self.assertEqual((out.patch.replaced, out.patch.added), (2, 0))
        self.assertIn("新的用途", out.text)
        self.assertEqual(out.text.count("SCR-HOME.logout |"), 1)

    def test_an_empty_patch_rechecks_the_red_draft(self):
        self.put("ui-20-b", "#atlas-ui 1\n## nav: from | to | label | opts\nSCR-LOGIN.submit | SCR-HOME | go\n")
        self.assertEqual(store.read_counters(self.up)["red"], {})
        self.put(NAME, BAD)
        again = self.put(NAME, "#atlas-ui 1\n", mode="patch")
        self.assertTrue(again.red)
        self.assertEqual(store.read_counters(self.up)["red"], {NAME: 2})

    def test_a_patch_without_a_slice_or_a_draft_and_an_unreadable_patch_are_refused_with_nothing_written(self):
        with self.assertRaises(store.UiError) as ctx:
            self.put(NAME, "-x\n", mode="patch")
        self.assertEqual(ctx.exception.code, store.E_NOTHING)
        self.put(NAME, LOGIN)
        before = self.files()
        for text, code in (("just words\n", store.E_PATCH), ("## widgets: local | label | opts\nx | X | kind=text\n", store.E_PATCH),
                           ("// nothing but a comment\n", store.E_PATCH)):
            with self.assertRaises(store.UiError) as ctx:
                self.put(NAME, text, mode="patch")
            self.assertEqual(ctx.exception.code, code, text)
        self.assertEqual(self.files(), before)

    def test_final_nav_and_pending_are_added_once(self):
        self.put(NAME, LOGIN)
        out = self.put(NAME, "## nav: from | to | label | opts\n@final nav\n@pending SCR-LATER\n", mode="patch")
        self.assertTrue(out.text.endswith("@final nav\n@pending SCR-LATER\n"))
        again = self.put(NAME, "## nav: from | to | label | opts\n@final nav\n", mode="patch")
        self.assertEqual(again.text.count("@final nav"), 1)

    def test_the_same_patch_twice_gives_the_same_text(self):
        self.put(NAME, LOGIN)
        patch = "## widgets: local | label | opts\n@set screen=SCR-LOGIN\npwd | 口令 | kind=input parent=form bind=password\n"
        self.assertEqual(self.put(NAME, patch, mode="patch").text, self.put(NAME, patch, mode="patch").text)


class RefusalTests(UiProject):
    def test_a_name_must_be_ui_dash_and_safe(self):
        for name in ("login", "10-login", "ui", "ui-", "UI-10", "ui-10 login", "ui-../x", "ui-" + "a" * 38, "ui-a.b", "", None):
            with self.assertRaises(store.UiError, msg=repr(name)) as ctx:
                self.put(name, LOGIN)
            self.assertEqual(ctx.exception.code, store.E_NAME)
        for name in ("ui-1", "ui-" + "a" * 37, "ui-verdict-fe", "ui-con"):
            self.assertEqual(store.check_name(name), name)

    def test_the_first_line_decides_the_family_and_the_prefix_is_the_second_check(self):
        with self.assertRaises(store.UiError) as ctx:
            self.put("ui-layer", "#atlas 1\n## nodes: id | name | sub | opts\nsvc.a | A | a. | room=rm.a kind=Module\n")
        self.assertEqual(ctx.exception.code, store.E_ROUTE_FIVE)
        self.assertIn("5D", ctx.exception.message)
        with self.assertRaises(store.UiError) as ctx:
            self.put("10-structure", LOGIN)
        self.assertEqual(ctx.exception.code, store.E_NAME)       # not a ui name at all
        self.assertEqual(self.dirs() & {"desk/atlas/ui"}, set())

    def test_route_returns_family_and_code(self):
        five_text = "// c\n#atlas 1\n"
        table = [
            ("ui-10-x", LOGIN, ("ui", "")),
            ("ui-10-x", five_text, ("five", store.E_ROUTE_FIVE)),
            ("10-structure", five_text, ("five", "")),
            ("10-structure", LOGIN, ("ui", store.E_ROUTE_UI)),
            ("ui-layer", "#atlas 1 mode=tobe\n", ("five", store.E_ROUTE_FIVE)),
            ("ui-10-x", "no header at all\n", ("ui", "")),
            ("10-structure", "no header at all\n", ("none", "")),
            ("ui-10-x", "﻿#atlas-ui 1\n", ("ui", "")),
            ("ui-10-x", "", ("ui", "")),
        ]
        for name, text, (family, code) in table:
            got = store.route(name, text)
            self.assertEqual((got.family, got.code), (family, code), (name, text[:20]))
            self.assertEqual(got.ok, not code)

    def test_a_text_too_large_is_refused_without_being_stored_and_a_large_one_is_a_red_draft(self):
        with self.assertRaises(store.UiError) as ctx:
            self.put(NAME, LOGIN + "// " + "x" * (store.HARD_TEXT_BYTES + 10))
        self.assertEqual(ctx.exception.code, store.E_LIMIT)
        self.assertFalse(self.dirs() & {"desk/atlas/ui"})
        out = self.put(NAME, LOGIN + "// " + "x" * (store.MAX_SLICE_BYTES + 10))
        self.assertEqual([d.rule for d in out.check.errors], ["ST-008"])
        self.assertTrue(out.red)

    def test_an_empty_text_a_bad_mode_and_a_bad_base_are_refused(self):
        for kw, code in (({"text": ""}, store.E_ARG), ({"text": LOGIN, "mode": "append"}, store.E_ARG), ({"text": LOGIN, "base": "xyz"}, store.E_ARG)):
            with self.assertRaises(store.UiError) as ctx:
                self.put(NAME, kw.pop("text"), **kw)
            self.assertEqual(ctx.exception.code, code)

    def test_base_is_optimistic_concurrency_and_nothing_is_written_on_a_mismatch(self):
        first = self.put(NAME, LOGIN)
        before = (self.paths.atlas / "ui" / f"{NAME}.atl").read_bytes()
        with self.assertRaises(store.UiError) as ctx:
            self.put(NAME, LOGIN + "// x\n", base="deadbeef")
        self.assertEqual(ctx.exception.code, store.E_CONFLICT)
        self.assertEqual(ctx.exception.extra["current"], first.sha8)
        self.assertEqual((self.paths.atlas / "ui" / f"{NAME}.atl").read_bytes(), before)
        self.assertEqual(self.put(NAME, LOGIN + "// x\n", base=first.sha8).status, "committed")

    def test_the_total_size_of_the_interface_is_bounded(self):
        with mock.patch.object(store, "MAX_UI_BYTES", 2000):
            self.put("ui-10-a", LOGIN)
            with self.assertRaises(store.UiError) as ctx:
                self.put("ui-20-b", LOGIN.replace("SCR-", "SCR-B"))
            self.assertEqual(ctx.exception.code, store.E_LIMIT)

    def test_a_ui_error_is_also_a_slice_error(self):
        self.assertTrue(issubclass(store.UiError, sl.SliceError))
        with self.assertRaises(sl.SliceError):
            self.put("nope", LOGIN)


class VerdictSliceTests(UiProject):
    VERDICT = VERDICT

    def test_a_verdict_never_moves_the_prototype_hash_and_counts_against_its_own_budget(self):
        self.put(NAME, LOGIN)
        state = store.load_state(self.up, self.cfg.meta())
        out = self.put("ui-verdict-fe", self.VERDICT)
        self.assertEqual((out.status, out.verdict), ("committed", True))
        self.assertEqual(out.ui_sha8, state.ui_sha8)
        self.assertNotEqual(out.verdict_sha8, state.verdict_sha8)
        after = store.load_state(self.up, self.cfg.meta())
        self.assertEqual((after.ui_sha8, after.verdict_sha8), (state.ui_sha8, out.verdict_sha8))
        self.assertEqual(store.read_counters(self.up), {"puts": 1, "verdictPuts": 1, "red": {}})
        self.assertEqual(out.counts, {"conform": 1, "diffs": 1})

    def test_a_verdict_slice_holds_nothing_else_and_a_prototype_slice_holds_no_verdict(self):
        out = self.put("ui-verdict-fe", LOGIN)
        self.assertTrue(out.red)
        self.assertEqual({d.rule for d in out.check.errors}, {"ST-004"})
        out = self.put("ui-10-other", self.VERDICT)
        self.assertTrue(out.red)
        self.assertEqual({d.rule for d in out.check.errors}, {"ST-004"})

    def test_the_budgets(self):
        self.assertEqual([store.put_budget(n) for n in (0, 1, 4, 5, 12)], [4, 6, 6, 8, 10])
        self.assertEqual([store.verdict_budget(n) for n in (0, 1, 2, 12)], [2, 3, 3, 8])


class CommitHookTests(UiProject):
    def test_the_interface_hooks_run_after_a_commit_and_the_5d_hooks_never_do(self):
        seen, five_seen = [], []
        hook = lambda paths, outcome: seen.append((paths.ui.name, outcome.slice, outcome.status))
        five_hook = lambda paths, outcome: five_seen.append(outcome)
        store.register_commit_hook(hook)
        sl.register_commit_hook(five_hook)
        self.addCleanup(lambda: store.UI_COMMIT_HOOKS.remove(hook))
        self.addCleanup(lambda: sl.COMMIT_HOOKS.remove(five_hook))
        self.put(NAME, LOGIN)
        self.put("ui-20-bad", BAD)
        self.assertEqual(seen, [("ui", NAME, "committed")])
        self.assertEqual(five_seen, [])
        tools.put(self.ws, "desk", "00-space", (DESK_DIR / "00-space.atl").read_text(encoding="utf-8"))
        self.assertEqual(len(five_seen), 1)
        self.assertEqual(len(seen), 1)

    def test_a_failing_hook_does_not_undo_the_commit(self):
        def broken(paths, outcome):
            raise RuntimeError("boom")

        store.register_commit_hook(broken)
        self.addCleanup(lambda: store.UI_COMMIT_HOOKS.remove(broken))
        out = self.put(NAME, LOGIN)
        self.assertEqual(out.status, "committed")
        self.assertEqual(out.hook_errors, ["RuntimeError: boom"])
        self.assertTrue((self.paths.atlas / "ui" / f"{NAME}.atl").is_file())


class LocationTests(UiProject):
    def test_ui_present_looks_only_at_the_interface_directories(self):
        self.desk()
        self.assertFalse(store.ui_present(self.up))
        tools.put(self.ws, "desk", "ui-layer", "#atlas 1\n## tests: id | title\nTS-9 | a layer test\n")
        self.assertTrue((self.paths.slices / "ui-layer.atl").is_file())
        self.assertFalse(store.ui_present(self.up), "a 5D slice named ui-layer is not an interface")
        self.put(NAME, BAD)
        self.assertTrue(store.ui_present(self.up), "a red draft counts")

    def test_the_reads_create_nothing_in_a_project_without_an_interface(self):
        self.desk()
        files, dirs = self.files(), self.dirs()
        store.load_state(self.up, self.cfg.meta())
        store.check_store(self.up, self.cfg.meta(), five=self.five())
        store.check_store(self.up, self.cfg.meta(), mode="slice")
        store.ui_present(self.up)
        store.read_counters(self.up)
        store.list_slices(self.up), store.list_red(self.up), store.history_of(self.up, NAME)
        self.assertEqual((self.files(), self.dirs()), (files, dirs))

    def test_a_put_creates_files_only_under_atlas_ui_and_the_hidden_state_directory(self):
        self.desk()
        before = self.files()
        self.put(NAME, LOGIN)
        self.put(NAME, BAD)
        self.put(NAME, "## widgets: local | label | opts\n@set screen=SCR-LOGIN\n-err\n", mode="patch")
        new = self.files() - before
        self.assertTrue(new)
        for path in new:
            self.assertTrue(path.startswith(("desk/atlas/ui/", "desk/atlas/.atlas/ui/")), path)
        self.assertEqual(self.files() - new, before)
        self.assertTrue((self.paths.dot / "ui" / "lock").exists() is False, "the lock is released")

    def test_the_lock_is_the_interface_lock_not_the_5d_lock(self):
        seen = []
        hook = lambda paths, outcome: seen.append((paths.lock.is_file(), (self.paths.dot / "lock").exists()))
        store.register_commit_hook(hook)
        self.addCleanup(lambda: store.UI_COMMIT_HOOKS.remove(hook))
        self.put(NAME, LOGIN)
        self.assertEqual(seen, [(True, False)])

    def test_a_temp_file_of_a_crashed_write_is_swept_in_the_interface_directories_only(self):
        self.desk()
        self.put(NAME, LOGIN)
        ours, theirs = self.paths.atlas / "ui" / ".tmp-1-aa-x.atl", self.paths.slices / ".tmp-1-aa-x.atl"
        ours.write_bytes(b"x")
        theirs.write_bytes(b"x")
        self.put(NAME, LOGIN + "// again\n")
        self.assertFalse(ours.exists())
        self.assertTrue(theirs.exists())

    def test_a_dot_file_and_other_files_in_the_interface_directory_are_not_slices(self):
        self.put(NAME, LOGIN)
        (self.paths.atlas / "ui" / ".hidden.atl").write_bytes(b"x")
        (self.paths.atlas / "ui" / "notes.txt").write_bytes(b"x")
        (self.paths.atlas / "ui" / "login.atl").write_bytes(b"x")
        self.assertEqual([s.name for s in store.list_slices(self.up)], [NAME])

    def test_check_store_in_full_mode_lists_the_red_drafts_as_st009(self):
        self.put(NAME, LOGIN)
        self.put("ui-20-bad", BAD)
        full = store.check_store(self.up, self.cfg.meta())
        self.assertEqual({d.rule for d in full.errors}, {"ST-009"})
        self.assertFalse(full.ok)
        self.assertIn("ST-009", store.check_store(self.up, self.cfg.meta(), five=self.five()).rule_ids())
        self.assertNotIn("ST-009", store.check_store(self.up, self.cfg.meta(), mode="slice").rule_ids())

    def test_load_state_gives_the_model_the_hash_and_the_red_drafts(self):
        self.put(NAME, LOGIN)
        self.put("ui-20-bad", BAD)
        state = store.load_state(self.up, self.cfg.meta())
        self.assertEqual((state.ui_sha8, state.slices, state.red, state.screens, state.present), (um.ui_sha8(state.model), [NAME], ["ui-20-bad"], 2, True))
        self.assertEqual(state.model["meta"]["projectId"], "desk")
        empty = store.load_state(self.up, self.cfg.meta())
        self.assertEqual(empty.slices, [NAME])

    def test_an_oversized_committed_file_is_read_as_a_stub_and_never_loaded(self):
        (self.paths.atlas / "ui").mkdir(parents=True)
        (self.paths.atlas / "ui" / "ui-10-big.atl").write_bytes(b"#atlas-ui 1\n// " + b"x" * (store.HARD_TEXT_BYTES + 100))
        result = store.check_store(self.up, self.cfg.meta())
        self.assertEqual({d.rule for d in result.errors}, {"ST-008"})
        with self.assertRaises(store.UiError) as ctx:       # a put over it is a refusal, not a crash
            self.put("ui-10-big", LOGIN)
        self.assertEqual(ctx.exception.code, store.E_LIMIT)
        self.assertEqual(self.put("ui-20-other", LOGIN).status, "committed")   # the other slices still work


class FenceTests(UiProject):
    """AC-17: a directory of the interface store that is a link, a junction or outside the workspace refuses the write."""

    def refused(self, link: Path, target: Path) -> None:
        sentinel = target / "sentinel.txt"
        sentinel.write_text("keep me", encoding="utf-8")
        link.parent.mkdir(parents=True, exist_ok=True)
        if not make_link(link, target):
            self.skipTest("no directory link (junction or symlink) could be made here")
        try:
            with self.assertRaises(GemArcError) as ctx:
                self.put(NAME, LOGIN)
            self.assertEqual(ctx.exception.code if hasattr(ctx.exception, "code") else "", "GEMARC_AUTH_001")
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["sentinel.txt"], "nothing was written through the link")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep me")
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(link)

    def outside(self) -> Path:
        out = Path(tempfile.mkdtemp(prefix="gemarc-ui-outside-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(out, True))
        return out

    def test_a_junction_at_atlas_ui_is_refused(self):
        self.refused(self.paths.atlas / "ui", self.outside())

    def test_a_junction_at_the_hidden_state_directory_is_refused(self):
        self.refused(self.paths.dot / "ui", self.outside())

    def test_a_junction_at_red_or_history_is_refused(self):
        (self.paths.dot / "ui").mkdir(parents=True)
        self.refused(self.paths.dot / "ui" / "red", self.outside())

    def test_a_junction_at_history_is_refused(self):
        (self.paths.dot / "ui").mkdir(parents=True)
        self.refused(self.paths.dot / "ui" / "history", self.outside())

    def test_check_confined_refuses_a_directory_that_resolves_outside_the_root(self):
        smaller_root = self.root / "elsewhere"
        smaller_root.mkdir()
        with self.assertRaises(GemArcError):
            store.check_confined(smaller_root, self.up)
        store.check_confined(self.root, self.up)
        store.check_confined(None, self.up)

    def test_a_plain_project_is_not_refused(self):
        self.assertEqual(self.put(NAME, LOGIN).status, "committed")


if __name__ == "__main__":
    unittest.main()
