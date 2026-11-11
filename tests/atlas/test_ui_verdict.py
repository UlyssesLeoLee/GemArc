"""Frontend check verdicts (``protoui/verdict.py``, design A3 4.8 and 6.3 ``UV-001`` .. ``UV-008``).

The prototype is the six-screen synthetic one of ``frontend_synth`` (164 check points), the frontend is its generated tree written to a real
directory (one with the seeded wrong-text defect, so that a ``differs`` verdict has something true to quote). Every rule has mutants: a clean verdict
gives no finding at all, each mutant fires exactly its rule. Also: the stamp ``sha`` of a screen, the hash of the verdict rows apart from the prototype,
the put and check paths of the store (a bad verdict is a red draft, nothing is committed), the evidence reads (sandbox, sensitive files, no source line
that looks like a credential in any answer) and the rule that only public names of ``evidence.py`` are used.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_verdict.py"
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import roots as rt
from gemarc_mcp.atlas.protoui import atl, expand as ex, model as um, rules, store
from gemarc_mcp.atlas.protoui import build as bld
from gemarc_mcp.atlas.protoui import verdict as vd

from . import frontend_synth as fs
from .test_ui_store import NAME, UiProject

MODEL = fs.prototype_model()
STAMPS = um.screen_hashes(MODEL)
POINTS = {sid: [c for c in um.checkpoints(MODEL) if c.screen == sid] for sid in STAMPS}
N = len(POINTS["SCR-LOGIN"])
TOTAL = sum(len(v) for v in POINTS.values())
WRONG_TEXT = next(d for d in fs.DEFECTS if d.key == "wrong-text-1")
FILES = fs.render(MODEL, defect=WRONG_TEXT)
FILE = "ui-verdict-fe.atl"
_tmp = tempfile.TemporaryDirectory()
ROOT = Path(_tmp.name)


def setUpModule():
    fs.write(FILES, ROOT)


def tearDownModule():
    _tmp.cleanup()


def line_of(rel: str, needle: str) -> int:
    for number, text in enumerate(FILES[rel].split("\n"), start=1):
        if needle in text:
            return number
    raise AssertionError(needle)


MAIN = {sid: line_of(f"pages/{sid.lower()}.html", f'data-screen="{sid}"') for sid in STAMPS}
BUTTON = line_of("pages/scr-login.html", "登入")


def basis(screen: str = "SCR-LOGIN", line: "int | None" = None) -> str:
    return f"fe:pages/{screen.lower()}.html:{line or MAIN[screen]}"


def conform(screen: str = "SCR-LOGIN", verdict: str = "conforms", *, sha: "str | None" = None, checked: "int | None" = None, base: "str | None" = None,
            extra: str = "") -> str:
    n = len(POINTS[screen])
    sha = STAMPS[screen] if sha is None else sha
    opts = ([f"sha={sha}"] if sha else []) + ([f"checked={n if checked is None else checked}"] if checked != -1 else []) + ["repo=fe"]
    return f"{screen} | {verdict} | {base or basis(screen)} | " + " ".join(opts) + (" " + extra if extra else "")


def slice_text(conforms=(), diffs=()) -> str:
    out = ["#atlas-ui 1"]
    if conforms:
        out += ["## conform: screen | verdict | basis | opts", *conforms]
    if diffs:
        out += ["## diffs: ref | kind | note | opts", *diffs]
    return "\n".join(out) + "\n"


def differing_label() -> str:
    return f"SCR-LOGIN.submit@label | differs | the button says another word | expected=登录 found=登入 basis={basis('SCR-LOGIN', BUTTON)}"


def run(text: str, *, roots="default", scans=None, mode: str = "slice", incoming="default") -> "vd.VerdictCheck":
    roots = {"fe": ROOT} if roots == "default" else roots
    incoming = [FILE] if incoming == "default" and mode == "slice" else (None if incoming == "default" else incoming)
    return vd.check(MODEL, [(FILE, text)], roots, scans=scans, mode=mode, incoming=incoming)


def rules_of(result) -> set:
    return {d.rule for d in result.diagnostics}


GOOD = slice_text([conform()])
GOOD_DIFFERS = slice_text([conform("SCR-LOGIN", "differs", checked=len(POINTS["SCR-LOGIN"]))], [differing_label()])


class BaselineTests(unittest.TestCase):
    def test_the_fixture_is_what_the_tests_assume(self):
        self.assertEqual(sorted(STAMPS), sorted(fs.SCREENS))
        self.assertEqual((N, TOTAL), (23, 164))
        self.assertIn("登入", FILES["pages/scr-login.html"])
        self.assertEqual(rules.check_sources({"ui-10-synthetic.atl": fs.prototype_text()}, mode="full").errors, [])

    def test_a_conforming_verdict_and_a_differing_one_are_clean(self):
        for text in (GOOD, GOOD_DIFFERS):
            result = run(text)
            self.assertEqual(result.diagnostics, [], result.render())
            self.assertTrue(result.ok)

    def test_the_findings_are_in_the_shape_of_every_other_check(self):
        result = run(slice_text([conform(sha="0a1b2c3d")]))
        d = result.diagnostics[0]
        self.assertEqual(sorted(d.as_dict()), sorted(dg.FIELDS))
        line = dg.render_line(d)
        self.assertLessEqual(len(line.encode("utf-8")), 460)
        self.assertTrue(line.startswith("E UV-007 ui-verdict-fe.atl:3 "))


class MutantTests(unittest.TestCase):
    """One mutant per way of being wrong; each fires exactly the named rule."""

    def check(self, rule: str, text: str, *, severity: str = "ERROR", **kw) -> "vd.VerdictCheck":
        result = run(text, **kw)
        self.assertEqual(rules_of(result), {rule}, result.render())
        self.assertEqual({d.severity for d in result.diagnostics}, {severity}, result.render())
        return result

    # -- UV-001: a ref that is a check point -------------------------------------------------------------------------
    def test_uv001_a_ref_that_is_not_a_check_point(self):
        self.check("UV-001", slice_text([conform("SCR-LOGIN", "differs")], ["SCR-LOGIN.email@emph | differs | no such cell | basis=" + basis()]))

    def test_uv001_a_misspelt_ref_names_the_closest_one(self):
        result = self.check("UV-001", slice_text([conform("SCR-LOGIN", "differs")], ["SCR-LOGIN.emial@label | differs | typo | basis=" + basis()]))
        self.assertIn("did you mean SCR-LOGIN.email@label", result.diagnostics[0].hint)

    def test_uv001_a_widget_without_the_cell_says_so(self):
        result = self.check("UV-001", slice_text(diffs=["SCR-LOGIN.email@api | missing | nothing to check | basis=" + basis()]))
        self.assertIn("no api cell", result.diagnostics[0].hint)

    def test_uv001_a_conform_row_for_a_screen_that_does_not_exist(self):
        self.check("UV-001", slice_text([conform("SCR-LOGIN").replace("SCR-LOGIN |", "SCR-LOGON |", 1)]))

    def test_uv001_an_extra_row_names_a_screen_or_its_route_only(self):
        ok = slice_text([conform("SCR-LOGIN", "differs")], [f"SCR-LOGIN@route | extra | another route | found=/login basis={basis('SCR-LOGIN', 1 + 0)}"])
        self.assertNotIn("UV-001", rules_of(run(ok)))
        self.check("UV-001", slice_text([conform("SCR-LOGIN", "differs")], ["SCR-LOGIN.email@label | extra | a widget is not an extra | basis=" + basis()]))

    # -- UV-002: once ------------------------------------------------------------------------------------------------
    def test_uv002_a_ref_twice(self):
        row = differing_label()
        self.check("UV-002", slice_text([conform("SCR-LOGIN", "differs")], [row, row]))

    def test_uv002_a_screen_with_two_conform_rows(self):
        self.check("UV-002", slice_text([conform(), conform()]))

    # -- UV-003: expected is the prototype's value ----------------------------------------------------------------------
    def test_uv003_expected_is_not_what_the_prototype_has(self):
        result = self.check("UV-003", slice_text([conform("SCR-LOGIN", "differs")], [differing_label().replace("expected=登录", "expected=登陆")]))
        self.assertIn("the prototype has 登录", result.diagnostics[0].hint)

    def test_uv003_text_copied_from_a_card_is_accepted(self):
        text = fs.prototype_text().replace("save | 保存 | kind=button", "save | 保存 * | kind=button")
        model = ex.expand_ui([atl.parse_atl(text, "ui-10-synthetic.atl")]).model
        stamp, n = um.screen_hashes(model)["SCR-BOOK"], sum(1 for c in um.checkpoints(model) if c.screen == "SCR-BOOK")
        card_form = "保存 \u2217"
        for expected, fires in (("保存 *", False), (card_form, False), ("保存", True)):
            row = f"SCR-BOOK.save@label | differs | the text | expected={expected} found=保存 basis=fe:pages/scr-book.html:{MAIN['SCR-BOOK']}"
            slice_ = slice_text([f"SCR-BOOK | differs | fe:pages/scr-book.html:{MAIN['SCR-BOOK']} | sha={stamp} checked={n} repo=fe"], [row])
            result = vd.check(model, [(FILE, slice_)], {}, mode="slice", incoming=[FILE])
            self.assertEqual("UV-003" in rules_of(result), fires, expected)

    # -- UV-004: the basis is real ---------------------------------------------------------------------------------------
    def test_uv004_a_file_that_does_not_exist(self):
        self.check("UV-004", slice_text([conform(base="fe:pages/nope.html:1")]))

    def test_uv004_a_line_past_the_end(self):
        result = self.check("UV-004", slice_text([conform(base="fe:pages/scr-login.html:999")]))
        self.assertIn("lines", result.diagnostics[0].hint)

    def test_uv004_found_is_not_near_the_line(self):
        far = differing_label().replace(f"scr-login.html:{BUTTON}", "scr-login.html:1")
        result = self.check("UV-004", slice_text([conform("SCR-LOGIN", "differs")], [far]))
        self.assertIn(f"line {BUTTON}", result.diagnostics[0].hint)       # the repair names the line where the text is

    def test_uv004_found_that_is_nowhere(self):
        result = self.check("UV-004", slice_text([conform("SCR-LOGIN", "differs")], [differing_label().replace("found=登入", "found=nothing-like-this")]))
        self.assertIn("nowhere", result.diagnostics[0].hint)

    def test_uv004_found_within_three_lines_is_enough_four_is_not(self):
        for delta, fires in ((3, False), (-3, False), (4, True)):
            row = differing_label().replace(f"scr-login.html:{BUTTON}", f"scr-login.html:{BUTTON + delta}")
            result = run(slice_text([conform("SCR-LOGIN", "differs")], [row]))
            self.assertEqual("UV-004" in rules_of(result), fires, delta)

    def test_uv004_found_without_a_basis_cannot_be_checked(self):
        row = "SCR-LOGIN.submit@label | differs | the button says another word | expected=登录 found=登入"
        self.check("UV-004", slice_text([conform("SCR-LOGIN", "differs")], [row]))

    def test_uv004_a_basis_that_is_not_alias_path_line(self):
        for bad in ("pages/scr-login.html", "fe:pages/scr-login.html", "fe:pages/scr-login.html:0", "see-the-login-page"):
            row = differing_label().replace(f"basis={basis('SCR-LOGIN', BUTTON)}", f"basis={bad}")
            self.assertIn("UV-004", rules_of(run(slice_text([conform("SCR-LOGIN", "differs")], [row]))), bad)

    def test_uv004_a_path_that_leaves_the_root_or_is_not_relative(self):
        for bad in ("../outside.html", "/etc/hosts", "C:/windows/win.ini", "pages/../../x.html"):
            result = run(slice_text([conform(base=f"fe:{bad}:1")]))
            self.assertIn("UV-004", rules_of(result), bad)
            self.assertNotIn(str(ROOT), result.render(), bad)

    def test_uv004_a_sensitive_file_is_refused_and_never_read(self):
        (ROOT / ".env").write_text("TOKEN_FOR_TESTS=placeholder\n", encoding="utf-8")
        result = run(slice_text([conform(base="fe:.env:1")]))
        self.assertEqual(rules_of(result), {"UV-004"})
        self.assertIn("sensitive", result.diagnostics[0].hint)

    def test_uv004_without_a_root_it_is_one_info_line_per_alias_and_never_a_pass(self):
        text = slice_text([conform("SCR-LOGIN", "differs"), conform("SCR-HOME").replace("SCR-LOGIN", "SCR-HOME")], [differing_label()])
        result = run(text, roots={})
        self.assertEqual(rules_of(result) - {"UV-005"}, {"UV-004"})      # (the second conform row has no diffs row: UV-005 is a different matter)
        infos = [d for d in result.diagnostics if d.rule == "UV-004"]
        self.assertEqual([d.severity for d in infos], ["INFO"])
        self.assertIn("not a pass", infos[0].hint)
        self.assertEqual(result.skipped_aliases, ("fe",))

    def test_uv004_a_root_that_does_not_exist_is_a_skip_too(self):
        result = run(GOOD, roots={"fe": ROOT / "missing-directory"})
        self.assertEqual([d.severity for d in result.diagnostics], ["INFO"])

    def test_uv004_unchecked_rows_need_no_basis_in_the_grammar(self):
        row = conform("SCR-HOME", "unchecked", sha="", checked=-1).replace("fe:pages/scr-home.html:%d" % MAIN["SCR-HOME"], "not looked at")
        self.assertEqual(rules_of(run(slice_text([row]))), set())

    # -- UV-005: every check point is accounted for -------------------------------------------------------------------------
    def test_uv005_checked_does_not_add_up(self):
        result = self.check("UV-005", slice_text([conform(checked=N - 1)]))
        self.assertIn(f"checked={N}", result.diagnostics[0].hint)

    def test_uv005_checked_is_missing(self):
        self.check("UV-005", slice_text([conform(checked=-1)]))

    def test_uv005_unverifiable_rows_make_up_the_difference(self):
        rows = [f"{c.ref} | unverifiable | the style is in a style sheet | basis={basis()}" for c in POINTS["SCR-LOGIN"] if c.ref.endswith("@emph")]
        self.assertEqual(len(rows), 2)
        text = slice_text([conform(checked=N - len(rows))], rows)
        result = run(text)
        self.assertEqual(result.diagnostics, [], result.render())
        self.assertEqual(vd.coverage_of(MODEL, [{"screen": "SCR-LOGIN", "verdict": "conforms", "sha": STAMPS["SCR-LOGIN"], "checked": N - 2}],
                                        [{"ref": r.split(" | ")[0], "kind": "unverifiable"} for r in rows]).screens[0].label, "conforms+u2")
        self.check("UV-005", slice_text([conform(checked=N)], rows))       # the same rows with checked left at N: N + 2 claims for N points

    def test_uv005_conforms_without_a_basis_and_differs_without_one(self):
        for verdict in ("conforms", "differs"):
            self.check("UV-005", slice_text([conform("SCR-LOGIN", verdict, base="the login page")], [differing_label()] if verdict == "differs" else []))

    def test_uv005_conforms_with_a_differing_row(self):
        result = self.check("UV-005", slice_text([conform()], [differing_label()]))
        self.assertIn("verdict=differs", result.diagnostics[0].hint)

    def test_uv005_differs_without_a_differing_row(self):
        self.check("UV-005", slice_text([conform("SCR-LOGIN", "differs")]))

    # -- UV-006: against the last scan ------------------------------------------------------------------------------------
    def scan_of(self, screen="SCR-LOGIN", absent=("SCR-LOGIN.submit@label",), sha=None, repo="fe") -> dict:
        return {screen: {"screenSha8": sha or STAMPS[screen], "repo": repo,
                         "literals": {**{c.ref: {"state": "FOUND"} for c in POINTS[screen] if c.ref.endswith("@label")}, **{r: {"state": "ABSENT"} for r in absent}}}}

    def test_uv006_conforms_against_an_absent_literal(self):
        result = self.check("UV-006", GOOD, scans=self.scan_of())
        self.assertIn("SCR-LOGIN.submit@label", result.diagnostics[0].hint)

    def test_uv006_an_unverifiable_row_for_it_is_the_way_out(self):
        rows = [f"SCR-LOGIN.submit@label | unverifiable | the text comes from a translation table | basis={basis()}"]
        result = run(slice_text([conform(checked=N - 1)], rows), scans=self.scan_of())
        self.assertEqual(result.diagnostics, [], result.render())

    def test_uv006_a_differing_row_is_the_other_way_and_not_this_rule(self):
        row = f"SCR-LOGIN.submit@label | differs | another word | expected=登录 found=登入 basis={basis('SCR-LOGIN', BUTTON)}"
        result = run(slice_text([conform()], [row]), scans=self.scan_of())
        self.assertEqual(rules_of(result), {"UV-005"})

    def test_uv006_a_scan_of_another_stamp_or_another_repo_is_not_used(self):
        self.assertEqual(run(GOOD, scans=self.scan_of(sha="0a1b2c3d")).diagnostics, [])
        self.assertEqual(run(GOOD, scans=self.scan_of(repo="web")).diagnostics, [])
        self.assertEqual(run(GOOD, scans={}).diagnostics, [])           # no scan file: the rule does not apply

    def test_uv006_only_conforms_is_contradicted(self):
        self.assertEqual(run(GOOD_DIFFERS, scans=self.scan_of()).diagnostics, [])

    # -- UV-007: the stamp -------------------------------------------------------------------------------------------------
    def test_uv007_a_new_conforms_with_an_old_stamp_is_an_error(self):
        result = self.check("UV-007", slice_text([conform(sha="0a1b2c3d")]))
        self.assertIn(f"sha={STAMPS['SCR-LOGIN']}", result.diagnostics[0].hint)

    def test_uv007_a_missing_stamp_is_the_same(self):
        self.check("UV-007", slice_text([conform(sha="")]))

    def test_uv007_a_stored_verdict_that_went_stale_is_a_warning(self):
        result = run(slice_text([conform(sha="0a1b2c3d")]), mode="full", incoming=None)
        self.assertEqual({d.rule: d.severity for d in result.diagnostics if d.rule == "UV-007"}, {"UV-007": "WARN"})
        self.assertTrue(result.ok)

    def test_uv007_a_stale_differs_is_a_warning_even_when_new(self):
        result = self.check("UV-007", slice_text([conform("SCR-LOGIN", "differs", sha="0a1b2c3d")], [differing_label()]), severity="WARN")
        self.assertTrue(result.ok)

    def test_uv007_an_unchecked_row_needs_no_stamp(self):
        row = conform("SCR-HOME", "unchecked", sha="", checked=-1)
        self.assertEqual(rules_of(run(slice_text([row]))), set())

    def test_uv007_a_stale_verdict_is_not_counted(self):
        stale = [{"screen": "SCR-LOGIN", "verdict": "conforms", "sha": "0a1b2c3d", "checked": 25}]
        cov = vd.coverage_of(MODEL, stale, [])
        self.assertEqual((cov.summary()["judged"], cov.summary()["stale"]), (0, 1))
        self.assertEqual(cov.screens[0].label, "conforms(stale)")

    # -- UV-008: coverage ----------------------------------------------------------------------------------------------------
    def test_uv008_the_full_check_reports_the_coverage_and_a_put_does_not(self):
        full = run(GOOD, mode="full", incoming=None)
        self.assertEqual({(d.rule, d.severity) for d in full.diagnostics}, {("UV-008", "INFO")})
        self.assertIn("judged 1/6", full.diagnostics[0].hint)
        self.assertIn(f"check points looked at {N} of {TOTAL}", full.diagnostics[0].hint)
        self.assertEqual(rules_of(run(GOOD, mode="slice")), set())


class StoredVerdictTests(unittest.TestCase):
    """A verdict that was put and then outlived its prototype is a WARN, never an ERROR (design 4.8); a new row is checked in full."""

    def smaller_model(self) -> dict:
        text = fs.prototype_text().replace("err | 邮箱或密码不正确 | kind=text parent=form in=error emph=danger\n", "")
        self.assertNotEqual(text, fs.prototype_text())
        return ex.expand_ui([atl.parse_atl(text, "ui-10-synthetic.atl")]).model

    def old_verdict(self) -> str:
        """A differs verdict of the prototype as it was: it names the removed widget in a missing row and counts the old check points."""
        row = f"SCR-LOGIN.err@label | missing | the error text is not in the page | expected=邮箱或密码不正确 basis={basis()}"
        return slice_text([conform("SCR-LOGIN", "differs")], [row])

    def test_the_rows_of_a_stale_stored_verdict_are_not_held_against_the_new_prototype(self):
        model = self.smaller_model()
        self.assertLess(len(um.checkpoints(model)), len(um.checkpoints(MODEL)))
        stored = vd.check(model, [(FILE, self.old_verdict())], {"fe": ROOT}, mode="full", incoming=None)
        self.assertEqual({(d.rule, d.severity) for d in stored.diagnostics}, {("UV-007", "WARN"), ("UV-008", "INFO")}, stored.render())
        self.assertTrue(stored.ok)

    def test_the_same_rows_being_put_are_checked_against_the_prototype_as_it_is(self):
        model = self.smaller_model()
        fresh = vd.check(model, [(FILE, self.old_verdict())], {"fe": ROOT}, mode="slice", incoming=[FILE])
        self.assertIn("UV-001", rules_of(fresh))
        self.assertFalse(fresh.ok)

    def test_a_new_diffs_row_of_a_screen_with_a_stale_stored_conform_is_still_checked(self):
        model = self.smaller_model()
        stored = FILE
        new_file = "ui-verdict-more.atl"
        bad = slice_text(diffs=["SCR-LOGIN.err@label | missing | gone | basis=" + basis()])
        result = vd.check(model, [(stored, slice_text([conform("SCR-LOGIN", "differs")], [differing_label()])), (new_file, bad)], {"fe": ROOT}, mode="slice", incoming=[new_file])
        self.assertEqual(rules_of(result), {"UV-001"})


class CoverageTests(unittest.TestCase):
    def test_the_labels_and_the_counts(self):
        rows = [{"screen": "SCR-LOGIN", "verdict": "conforms", "sha": STAMPS["SCR-LOGIN"], "checked": 25},
                {"screen": "SCR-HOME", "verdict": "differs", "sha": STAMPS["SCR-HOME"], "checked": 12},
                {"screen": "SCR-BOOK", "verdict": "unchecked", "checked": 0},
                {"screen": "SCR-RETURN", "verdict": "conforms", "sha": "0a1b2c3d", "checked": 9}]
        diffs = [{"ref": "SCR-HOME.find@label", "kind": "differs"}, {"ref": "SCR-HOME@route", "kind": "unverifiable"}]
        cov = vd.coverage_of(MODEL, rows, diffs)
        labels = {s.screen: s.label for s in cov.screens}
        self.assertEqual(labels, {"SCR-LOGIN": "conforms", "SCR-HOME": "differs", "SCR-CATALOG": "none", "SCR-BOOK": "unchecked", "SCR-RETURN": "conforms(stale)",
                                  "SCR-PROFILE": "none"})
        summary = cov.summary()
        self.assertEqual({k: summary[k] for k in ("total", "judged", "conforms", "differs", "stale", "unchecked", "none")},
                         {"total": 6, "judged": 2, "conforms": 1, "differs": 1, "stale": 1, "unchecked": 1, "none": 2})
        self.assertEqual(cov.by_screen()["SCR-HOME"].unverifiable, 1)
        self.assertEqual(cov.by_screen()["SCR-HOME"].diffs, 1)


class StampTests(unittest.TestCase):
    def test_a_stamp_moves_only_with_a_claim_of_its_own_screen(self):
        def stamps(text: str) -> dict:
            return um.screen_hashes(ex.expand_ui([atl.parse_atl(text, "ui-10-synthetic.atl")]).model)

        base = fs.prototype_text()
        self.assertEqual(stamps(base), STAMPS)
        for old, new, moves in (
                ("SCR-LOGIN | 登录 | 会员用邮箱和密码进入借阅台。", "SCR-LOGIN | 登录页 | 一句不同的用途。", set()),        # title and purpose are not claims
                ("email | 邮箱 |", "email | 电子邮箱 |", {"SCR-LOGIN"}),
                ("kind=input parent=form bind=password", "kind=input parent=form bind=passwd", {"SCR-LOGIN"}),
                ("route=/catalog", "route=/books", {"SCR-CATALOG"})):
            changed = {sid for sid, sha in stamps(base.replace(old, new, 1)).items() if sha != STAMPS[sid]}
            self.assertEqual(changed, moves, (old, new))

    def test_the_stamp_does_not_depend_on_the_hash_of_the_whole_prototype(self):
        self.assertEqual(len(set(STAMPS.values())), 6)
        self.assertRegex(next(iter(STAMPS.values())), r"^[0-9a-f]{8}$")
        self.assertEqual(um.screen_sha8(MODEL, "SCR-LOGIN"), STAMPS["SCR-LOGIN"])
        self.assertEqual(um.screen_sha8(MODEL, "SCR-NONE"), "")

    def test_the_card_shows_the_stamp_a_verdict_quotes(self):
        built = bld.build_screens(MODEL)
        card = built.files["screens/SCR-LOGIN.card.md"].decode("utf-8")
        self.assertIn(f"sha={STAMPS['SCR-LOGIN']}", card)
        for sid in STAMPS:
            self.assertIn(f"sha={STAMPS[sid]}", built.files[f"screens/{sid}.card.md"].decode("utf-8"))
        self.assertEqual(built.status, "ok")

    def test_a_verdict_never_moves_the_prototype_or_its_screens(self):
        before = bld.build_screens(MODEL)
        exp = ex.expand_ui([atl.parse_atl(fs.prototype_text(), "ui-10-synthetic.atl"), atl.parse_atl(GOOD_DIFFERS, FILE)])
        self.assertEqual(exp.model, MODEL)
        self.assertEqual(um.ui_sha8(exp.model), um.ui_sha8(MODEL))
        self.assertEqual(bld.build_screens(exp.model).files, before.files)
        self.assertEqual(um.verdict_sha8(exp.verdict), um.verdict_sha8(exp.verdict))
        self.assertNotEqual(um.verdict_sha8(exp.verdict), um.verdict_sha8({"conform": [], "diffs": []}))


class StoreTests(UiProject):
    """``put`` and ``check`` of a verdict slice go through the verdict rules."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.put("ui-10-synthetic", fs.prototype_text()).status, "committed")

    def test_a_good_verdict_commits_and_a_bad_one_is_a_red_draft_that_commits_nothing(self):
        good = self.put("ui-verdict-fe", GOOD_DIFFERS, verdict_roots={"fe": ROOT})
        self.assertEqual((good.status, good.red), ("committed", False), good.check.render())
        before = store.load_state(self.up, self.cfg.meta())
        bad = self.put("ui-verdict-fe", GOOD_DIFFERS.replace("expected=登录", "expected=登陆"), verdict_roots={"fe": ROOT})
        self.assertEqual((bad.status, bad.red), ("invalid", True))
        self.assertEqual({d.rule for d in bad.check.errors}, {"UV-003"})
        after = store.load_state(self.up, self.cfg.meta())
        self.assertEqual(after.verdict_sha8, before.verdict_sha8)          # the committed verdict did not change
        self.assertEqual(after.ui_sha8, before.ui_sha8)
        self.assertEqual(store.read_counters(self.up)["red"], {"ui-verdict-fe": 1})

    def test_the_roots_come_from_the_environment_unless_given(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {rt.ENV_NAME: f"fe={ROOT}"}):
            out = store.put_slice(self.up, self.cfg.meta(), "ui-verdict-fe", slice_text([conform("SCR-LOGIN", "differs")],
                                  [differing_label().replace("found=登入", "found=nothing-like-this")]), five=None, root=self.root)
        self.assertEqual({d.rule for d in out.check.errors}, {"UV-004"})
        with mock.patch.dict("os.environ", {rt.ENV_NAME: "not a valid specification"}):
            self.assertEqual(store.verdict_roots_of(None), {})

    def test_a_put_reports_only_its_own_rows_and_a_full_check_reports_the_stored_ones(self):
        self.assertEqual(self.put("ui-verdict-fe", GOOD).status, "committed")
        self.put("ui-20-more", fs.prototype_text().replace("SCR-", "SCZ-").replace("route=/", "route=/z"))
        full = store.check_store(self.up, self.cfg.meta(), mode="full", verdict_roots={"fe": ROOT})
        self.assertEqual({d.rule for d in full.diagnostics if d.rule.startswith("UV")}, {"UV-008"})
        # the prototype changes under the verdict: the stored verdict goes stale (WARN, not ERROR), the full check still passes
        changed = fs.prototype_text().replace("email | 邮箱 |", "email | 电子邮箱 |")
        self.assertEqual(self.put("ui-10-synthetic", changed).status, "committed")
        again = store.check_store(self.up, self.cfg.meta(), mode="full", verdict_roots={"fe": ROOT})
        stale = [d for d in again.diagnostics if d.rule == "UV-007"]
        self.assertEqual([d.severity for d in stale], ["WARN"])
        self.assertTrue(again.ok)
        # slice mode of a prototype slice does not report the verdict at all
        slice_ = store.check_store(self.up, self.cfg.meta(), mode="slice", incoming=["ui-10-synthetic.atl"], verdict_roots={"fe": ROOT})
        self.assertFalse({d.rule for d in slice_.diagnostics if d.rule.startswith("UV")})

    def test_a_scan_file_is_read_by_the_rule_and_only_a_scan_of_the_current_stamp_counts(self):
        stamp = um.screen_hashes(store.load_state(self.up, self.cfg.meta()).model)["SCR-LOGIN"]
        doc = {"schema": store.SCAN_SCHEMA, "screen": "SCR-LOGIN", "screenSha8": stamp, "repo": "fe",
               "literals": {"SCR-LOGIN.submit@label": {"state": "ABSENT"}}}
        store.write_scan(self.up, "SCR-LOGIN", (json.dumps(doc) + "\n").encode("utf-8"), root=self.root)
        self.assertEqual(sorted(store.read_scans(self.up)), ["SCR-LOGIN"])
        out = self.put("ui-verdict-fe", GOOD, verdict_roots={"fe": ROOT})
        self.assertEqual({d.rule for d in out.check.errors}, {"UV-006"})
        doc["screenSha8"] = "0a1b2c3d"
        store.write_scan(self.up, "SCR-LOGIN", (json.dumps(doc) + "\n").encode("utf-8"), root=self.root)
        self.assertEqual(self.put("ui-verdict-fe", GOOD, verdict_roots={"fe": ROOT}).status, "committed")

    def test_a_stored_verdict_can_be_an_error_in_the_full_check_when_the_evidence_moves(self):
        """The fact behind request U4-3: UV-004 and UV-006 are ERRORs also for rows that were committed earlier (a scan written after the verdict, a frontend
        that was edited), so a gate that reads ``check full`` of the interface would be blocked by a verdict. Only a prototype change is a mere WARN."""
        self.assertEqual(self.put("ui-verdict-fe", GOOD, verdict_roots={"fe": ROOT}).status, "committed")
        clean = store.check_store(self.up, self.cfg.meta(), mode="full", verdict_roots={"fe": ROOT})
        self.assertTrue(clean.ok, clean.render())
        stamp = um.screen_hashes(store.load_state(self.up, self.cfg.meta()).model)["SCR-LOGIN"]
        doc = {"schema": store.SCAN_SCHEMA, "screen": "SCR-LOGIN", "screenSha8": stamp, "repo": "fe", "literals": {"SCR-LOGIN.submit@label": {"state": "ABSENT"}}}
        store.write_scan(self.up, "SCR-LOGIN", (json.dumps(doc) + "\n").encode("utf-8"), root=self.root)
        later = store.check_store(self.up, self.cfg.meta(), mode="full", verdict_roots={"fe": ROOT})
        self.assertFalse(later.ok)
        self.assertEqual({d.rule for d in later.errors}, {"UV-006"})
        shorter = fs.prototype_text().replace("err | 邮箱或密码不正确 | kind=text parent=form in=error emph=danger\n", "")
        self.assertEqual(self.put("ui-10-synthetic", shorter).status, "committed")
        stale = store.check_store(self.up, self.cfg.meta(), mode="full", verdict_roots={"fe": ROOT})
        self.assertTrue(stale.ok, stale.render())          # the prototype moved: the verdict is stale, a WARN, and no other rule speaks for it
        self.assertEqual({d.rule for d in stale.diagnostics if d.rule.startswith("UV")}, {"UV-007", "UV-008"})

    def test_scan_files_that_are_not_scans_are_ignored(self):
        store.write_scan(self.up, "SCR-X", b"not json\n", root=self.root)
        store.write_scan(self.up, "SCR-Y", b'{"schema":"other","screen":"SCR-Y"}\n', root=self.root)
        self.assertEqual(store.read_scans(self.up), {})
        with self.assertRaises(store.UiError):
            store.scan_path(self.up, "../escape")


class EvidenceReadTests(unittest.TestCase):
    """What leaves the module: no host path, no line that looks like a credential."""

    SECRET = "sk" + "-" + "Zq9" * 8

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        fs.write(FILES, self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "legacy.js").write_text(f"// the login page\nconst label = 'login';\nconst passwd = \"{self.SECRET}\";\nconst x = 1;\n", encoding="utf-8")
        (self.root / "src" / "plain.js").write_text("// the login page\nconst label = 'login';\nconst title = 'Welcome';\nconst x = 1;\n", encoding="utf-8")

    def verdict(self, rel: str, line: int, found: str = "not-there") -> "vd.VerdictCheck":
        row = f"SCR-LOGIN.submit@label | differs | another word | expected=登录 found={found} basis=fe:{rel}:{line}"
        text = slice_text([conform("SCR-LOGIN", "differs")], [row])
        return vd.check(MODEL, [(FILE, text)], {"fe": self.root}, mode="slice", incoming=[FILE])

    def test_a_line_that_looks_like_a_credential_is_withheld_not_shown(self):
        result = self.verdict("src/legacy.js", 3)
        out = result.render() + json.dumps([d.as_dict() for d in result.diagnostics], ensure_ascii=False)
        self.assertEqual(rules_of(result), {"UV-004"})
        self.assertNotIn(self.SECRET, out)
        self.assertNotIn("Zq9Zq9", out)
        self.assertIn("[withheld", out)

    def test_an_ordinary_line_may_be_shown_to_help_the_repair(self):
        out = self.verdict("src/plain.js", 3).render()
        self.assertIn("line 3 reads: const title = 'Welcome';", out)

    def test_no_answer_carries_the_host_path(self):
        for rel, line in (("src/plain.js", 3), ("src/missing.js", 1), ("../x.js", 1), ("src/plain.js", 99)):
            result = self.verdict(rel, line)
            text = result.render() + json.dumps([d.as_dict() for d in result.diagnostics], ensure_ascii=False)
            self.assertNotIn(self.root.as_posix(), text.replace("\\", "/"))
            self.assertNotIn(str(Path.home()), text)

    def test_only_the_named_public_symbols_of_the_evidence_module_are_used(self):
        public = {"read_text_file", "is_sensitive_path", "context_echo_allowed", "echo_source_line", "CONTEXT_RADIUS", "TextError", "SourceText"}
        here = Path(vd.__file__).parent
        for name in ("verdict.py", "scan.py"):
            used = set(re.findall(r"\bev\.(\w+)", (here / name).read_text(encoding="utf-8")))
            self.assertTrue(used <= public, (name, sorted(used - public)))
            self.assertFalse(any(u.startswith("_") for u in used), name)
            self.assertNotRegex((here / name).read_text(encoding="utf-8"), r"from \.\.evidence import|_verify_source_row|_Run\b")

    def test_the_roots_machinery_is_used_through_its_public_names(self):
        here = Path(vd.__file__).parent
        for name in ("verdict.py", "scan.py"):
            used = set(re.findall(r"\brt\.(\w+)", (here / name).read_text(encoding="utf-8")))
            self.assertTrue(used <= {"RootsError", "resolve_in_root", "is_regular_file", "root_available", "roots_from_env", "require_alias", "E_ABSOLUTE", "E_DRIVE",
                                     "E_DOTDOT", "E_NAME", "E_PATH_TYPE", "E_ESCAPE"}, (name, sorted(used)))


if __name__ == "__main__":
    unittest.main()
