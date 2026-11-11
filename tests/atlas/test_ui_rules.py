"""The author-side rules of the interface family (``UX-*``), AC-05 (the mutant matrix), AC-11 (cost), AC-16 (links) and the two modes.

Each mutant breaks the two-screen worked example (``ui_fixtures/ui-10-login.atl``) in one way and must fire exactly its rule: the set of
non-derived rule ids of a full check, against the synthetic 5D fixture ``lending_desk``, equals ``{rule}``. The clean example gives 0
findings of any severity. Slice mode gets its own cases (pending / probable typo, binding rules waiting for ``@final nav``).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_rules.py"
"""
from __future__ import annotations

import copy
import json
import statistics
import time
import unittest
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import expand as ex5
from gemarc_mcp.atlas.protoui import expand as ex
from gemarc_mcp.atlas.protoui import rules

from . import ui_synth

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
NAME = "ui-10-login.atl"


def lending_desk_model() -> dict:
    sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(FIXTURE.glob("*.atl"))}
    return ex5.expand_sources(sources, json.loads((FIXTURE / "meta.json").read_text(encoding="utf-8"))).model


FIVE = lending_desk_model()
BASE = ui_synth.login_text()


def sub(old: str, new: str, text: str = BASE, count: int = 1) -> str:
    assert old in text, old
    return text.replace(old, new, count)


def full(text: str, five=FIVE, name: str = NAME) -> "rules.UiCheckResult":
    return rules.check_sources({name: text}, five=five, mode="full")


def slice_mode(text: str, five=FIVE, name: str = NAME) -> "rules.UiCheckResult":
    return rules.check_sources({name: text}, five=five, mode="slice", incoming=[name])


def widgets_added(count: int) -> str:
    """``count`` extra text widgets under the screen SCR-LOGIN (one fact: the screen is too large)."""
    extra = "\n".join(f"x{i:02d} | 文字{i} | kind=text" for i in range(count))
    return sub("@set screen=SCR-HOME", extra + "\n@set screen=SCR-HOME")


def variants_added(count: int) -> str:
    extra = "\n".join(f"SCR-HOME | v{i:03d} | 状态说明 | kind=other" for i in range(count))
    return sub("## widgets:", extra + "\n## widgets:")


FIVE_WITH_SECOND_REQUIREMENT = copy.deepcopy(FIVE)
FIVE_WITH_SECOND_REQUIREMENT["requirements"].append({**FIVE["requirements"][0], "id": "RQ-2"})

# (rule that must fire, what is wrong, mutated text, five model or the default)
MUTANTS = [
    ("ST-001", "the header line is missing", sub("#atlas-ui 1\n", ""), None),
    ("ST-001", "a widget row without its required kind", sub("kind=text parent=form in=error", "parent=form in=error"), None),
    ("ST-002", "a duplicate widget id", sub("logout | 退出 | kind=link parent=bar", "logout | 退出 | kind=link parent=bar\nlogout | 退出 | kind=link parent=bar"), None),
    ("ST-003", "an unknown widget kind", sub("kind=input parent=form bind=email", "kind=inputt parent=form bind=email"), None),
    ("ST-003", "a screen id with a dot (the refused screen takes its widgets and nav rows with it)", sub("SCR-HOME | 我的借阅", "SCR.HOME | 我的借阅"), None),
    ("ST-003", "an authored variant named default", sub("## widgets:", "SCR-LOGIN | default | 默认状态由工具派生。 | kind=other" + chr(10) + "## widgets:"), None),
    ("ST-003", "a bad size grammar", sub("kind=table w=fill", "kind=table w=wide"), None),
    ("ST-003", "a route that does not start with a slash", sub("route=/home", "route=home"), None),
    ("ST-004", "an unknown option key", sub("kind=table w=fill", "kind=table w=fill colour=red"), None),
    ("ST-004", "an unknown directive", sub("## nav:", "@bogus x\n## nav:"), None),
    ("ST-004", "an option on the header line", sub("#atlas-ui 1", "#atlas-ui 1 mode=tobe"), None),
    ("ST-005", "a widget row with one cell only", sub("email | 邮箱 | kind=input parent=form bind=email", "email"), None),
    ("ST-006", "the label cell is missing, the options move into it", sub("email | 邮箱 | kind=input parent=form bind=email", "email | kind=input parent=form bind=email"), None),
    ("ST-006", "an option-looking token inside a label", sub("loans | 借出的书 |", "loans | 借出的书 w=3 |"), None),
    ("ST-007", "an unbalanced quote in an option", sub("bind=email", 'bind="email'), None),
    ("ST-008", "a file larger than 64 KiB", BASE + "// " + "x" * 66000 + "\n", None),
    ("DV-001", "an authored derived field", sub("kind=link parent=bar", "kind=link parent=bar order=2"), None),
    ("UX-001", "an unknown parent", sub("kind=input parent=form bind=password", "kind=input parent=frm bind=password"), None),
    ("UX-001", "in= names an undeclared variant", sub("in=error emph=danger", "in=errr emph=danger"), None),
    ("UX-001", "a nav row to an undefined screen", sub("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LOGON |"), None),
    ("UX-002", "a leaf kind as parent", sub("kind=text parent=form in=error", "kind=text parent=email in=error"), None),
    ("UX-002", "a tab outside a tabs group", sub("@set screen=SCR-HOME", "t1 | 标签 | kind=tab\n@set screen=SCR-HOME"), None),
    ("UX-002", "a loop in the parent chain", sub("form | 登录表单 | kind=form", "form | 登录表单 | kind=form parent=submit"), None),
    ("UX-003", "a nav row from a text widget", sub("## nav: from | to | label | opts\n", "## nav: from | to | label | opts\nSCR-LOGIN.err | SCR-HOME | 误点 | on=tap\n"), None),
    ("UX-005", "a nav alias equal to a 5D id", sub("on=submit guard=凭证正确", "on=submit alias=usr.member guard=凭证正确"), None),
    ("UX-006", "two screens share a route", sub("route=/home", "route=/login"), None),
    ("UX-007", "a screen with more than 40 widgets", widgets_added(36), None),
    ("UX-008", "two screen ids that differ only in case", sub("## variants:", "scr-home | 副本 | 只差大小写 | start=true\n## variants:"), None),
    ("UX-009", "a screen id that names a build artifact", sub("## variants:", "Nav | 导航 | 产物名 | start=true\n## variants:"), None),
    ("UX-010", "a widget named root", sub("@set screen=SCR-HOME", "root | 根 | kind=text\n@set screen=SCR-HOME"), None),
    ("UX-010", "a widget named state (the id of the state band of the SVG)", sub("@set screen=SCR-HOME", "state | 状态 | kind=text\n@set screen=SCR-HOME"), None),
    ("UX-011", "a title longer than 40 characters", sub("SCR-HOME | 我的借阅", "SCR-HOME | " + "长" * 45), None),
    ("UX-201", "an unreachable screen", sub("SCR-LOGIN.submit | SCR-HOME |", "SCR-LOGIN.submit | SCR-LOGIN |"), None),
    ("UX-202", "a dead end (no way out of the home screen)",
     sub("SCR-HOME.logout | SCR-LOGIN | 退出登录 | on=tap\n", "").replace("kind=link parent=bar", "kind=link parent=bar req=RQ-1"), None),
    ("UX-203", "a form screen without an error variant",
     sub("SCR-LOGIN | error | 邮箱或密码不正确。 | kind=error\n", "").replace(" in=error", ""), None),
    ("UX-204", "a form with inputs and no button", sub("submit | 登录 | kind=button", "submit | 登录 | kind=link"), None),
    ("UX-205", "a button that leads nowhere", sub("logout | 退出 | kind=link parent=bar", "logout | 退出 | kind=link parent=bar\nhelp | 帮助 | kind=button parent=bar"), None),
    ("UX-206", "a requirement that the 5D model does not have", sub("req=RQ-1\nSCR-HOME", "req=RQ-9\nSCR-HOME"), None),
    ("UX-206", "an actor that is not an Actor node", sub("actor=usr.member", "actor=svc.loan"), None),
    ("UX-206", "a rule that is not a step of the case", sub("rule=R-BORROW", "rule=R-NOPE"), None),
    ("UX-206", "a rule without a case", sub("case=CS-BORROW-OK rule=R-BORROW", "rule=R-BORROW"), None),
    ("UX-207", "a requirement that no screen cites", BASE, FIVE_WITH_SECOND_REQUIREMENT),
    ("SZ-001", "a slice larger than 6 KiB", variants_added(190), None),
]


def render_all(result) -> list:
    return [dg.render_line(d) for d in result.diagnostics]


class CleanExampleTests(unittest.TestCase):
    def test_the_worked_example_has_no_finding_of_any_severity_in_either_mode(self):
        for check in (full, slice_mode):
            self.assertEqual(render_all(check(BASE)), [], check.__name__)

    def test_without_a_5d_model_the_links_are_counted_not_verified(self):
        result = full(BASE, five=None)
        self.assertEqual(sorted(result.rule_ids()), ["UX-206"])
        self.assertEqual([d.severity for d in result.diagnostics], ["INFO"])
        self.assertIn("6 links", render_all(result)[0])
        self.assertTrue(result.ok)

    def test_an_empty_5d_model_is_treated_like_no_5d_model(self):
        empty = {"nodes": [], "edges": [], "requirements": [], "cases": [], "machines": []}
        self.assertEqual([d.severity for d in full(BASE, five=empty).diagnostics], ["INFO"])


class MutantMatrixTests(unittest.TestCase):
    def test_there_are_at_least_25_mutants_and_each_rule_of_the_matrix_is_named(self):
        self.assertGreaterEqual(len(MUTANTS), 25)
        named = {m[0] for m in MUTANTS}
        for rule_id in ("ST-001", "ST-002", "ST-003", "ST-004", "ST-005", "ST-006", "ST-007", "ST-008", "DV-001", "SZ-001",
                        "UX-001", "UX-002", "UX-003", "UX-005", "UX-006", "UX-007", "UX-008", "UX-009", "UX-010", "UX-011",
                        "UX-201", "UX-202", "UX-203", "UX-204", "UX-205", "UX-206", "UX-207"):
            self.assertIn(rule_id, named)

    def test_every_mutant_fires_exactly_its_rule_in_full_mode(self):
        for want, what, text, five in MUTANTS:
            with self.subTest(rule=want, mutant=what):
                result = full(text, FIVE if five is None else five)
                self.assertEqual(sorted(result.rule_ids()), [want], render_all(result))

    def test_the_mutants_that_break_the_syntax_are_all_errors_and_stay_red_drafts(self):
        for want, what, text, five in MUTANTS:
            if want.startswith(("ST-", "DV-")):
                with self.subTest(rule=want, mutant=what):
                    self.assertFalse(full(text).ok)

    def test_every_other_error_rule_blocks_and_every_warning_does_not(self):
        for want, what, text, five in MUTANTS:
            if want.startswith(("ST-", "DV-")):
                continue
            result = full(text, FIVE if five is None else five)
            severity = rules.rule(want).severity
            with self.subTest(rule=want, mutant=what):
                self.assertEqual({d.severity for d in result.diagnostics if d.rule == want} - {"INFO"} or {severity}, {severity})
                self.assertEqual(result.ok, severity != "ERROR")

    def test_a_mutant_in_slice_mode_still_shows_its_rule_when_the_rule_runs_there(self):
        waiting = {"UX-201", "UX-202", "UX-203", "UX-204", "UX-205", "UX-207", "UX-005"}   # binding or full-only
        links_and_refs = {"UX-001"}                                                         # pending or probable typo, see the mode tests
        for want, what, text, five in MUTANTS:
            if want in waiting or want in links_and_refs or want == "UX-206":
                continue
            with self.subTest(rule=want, mutant=what):
                result = slice_mode(text, FIVE if five is None else five)
                self.assertIn(want, result.rule_ids(), render_all(result))

    def test_a_diagnostic_line_stays_within_220_bytes(self):
        longest = 0
        for want, what, text, five in MUTANTS:
            for d in full(text, FIVE if five is None else five).diagnostics:
                longest = max(longest, len(dg.render_line(d).encode("utf-8")))
        self.assertLessEqual(longest, 220)


class CascadeTests(unittest.TestCase):
    """A reference to an id whose row was refused is a consequence (derived), a real typo is not."""

    def test_the_references_to_a_refused_screen_are_counted_as_derived(self):
        result = full(sub("SCR-HOME | 我的借阅", "SCR.HOME | 我的借阅"))
        self.assertEqual(sorted(result.rule_ids()), ["ST-003"])
        self.assertGreaterEqual(result.derived_count, 4)
        self.assertEqual(sorted(result.rule_ids(derived=True)), ["ST-003", "UX-001"])

    def test_a_genuine_typo_next_to_a_refused_id_still_surfaces(self):
        text = sub("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LOGON |")
        text = sub("SCR-HOME | 我的借阅", "SCR.HOME | 我的借阅", text)
        result = full(text)
        self.assertEqual(sorted(result.rule_ids()), ["ST-003", "UX-001"])
        shown = [d for d in result.diagnostics if d.rule == "UX-001" and not d.derived]
        self.assertEqual([d.got for d in shown], ["SCR-LOGON"])

    def test_a_refused_widget_takes_the_nav_rows_that_start_from_it_as_derived(self):
        result = full(sub("logout | 退出 | kind=link", "logout | 退出 | kind=lnk"))
        self.assertEqual(sorted(result.rule_ids()), ["ST-003"])
        self.assertIn("UX-001", result.rule_ids(derived=True))

    def test_a_refused_variant_takes_the_widgets_that_name_it_as_derived(self):
        text = sub("bind=email", "bind=email in=loading")
        text = sub("SCR-LOGIN | loading | 提交后等待服务器响应。 | kind=loading", "SCR-LOGIN | loading | 提交后等待服务器响应。 | kind=loadin", text)
        result = full(text)
        self.assertEqual(sorted(result.rule_ids()), ["ST-003"])
        self.assertIn("UX-001", result.rule_ids(derived=True))


class ModeTests(unittest.TestCase):
    def test_an_unknown_reference_with_no_look_alike_is_pending_not_a_finding_in_slice_mode(self):
        result = slice_mode(sub("kind=input parent=form bind=password", "kind=input parent=cardx bind=password"))
        self.assertEqual(render_all(result), [])
        self.assertEqual(result.pending, {"widget of this screen": ["cardx"]})
        self.assertTrue(result.ok)

    def test_an_unknown_reference_one_edit_from_a_defined_id_is_a_warning_in_slice_mode(self):
        for text, got in ((sub("kind=input parent=form bind=password", "kind=input parent=frm bind=password"), "frm"),
                          (sub("in=error emph=danger", "in=errr emph=danger"), "errr"),
                          (sub("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LOGON |"), "SCR-LOGON")):
            result = slice_mode(text)
            shown = [d for d in result.diagnostics if d.rule == "UX-001"]
            self.assertEqual([(d.severity, d.got) for d in shown], [("WARN", got)])
            self.assertIn("one edit from", shown[0].hint)
            self.assertTrue(result.ok)

    def test_pending_silences_a_deliberate_forward_reference(self):
        text = sub("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LATER |") + "@pending SCR-LATER\n"
        result = slice_mode(text)
        self.assertEqual(render_all(result), [])
        self.assertEqual(result.pending, {})

    def test_the_same_reference_is_an_error_in_full_mode_with_the_nearest_id(self):
        shown = [d for d in full(sub("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LOGON |")).diagnostics if d.rule == "UX-001"]
        self.assertEqual([d.severity for d in shown], ["ERROR"])
        self.assertIn("nearest: SCR-LOGIN", shown[0].hint)
        parent = [d for d in full(sub("kind=input parent=form bind=password", "kind=input parent=frm bind=password")).diagnostics if d.rule == "UX-001"]
        self.assertIn("fix parent=", parent[0].hint)

    def test_a_forward_reference_to_a_later_slice_resolves_when_that_slice_is_committed(self):
        first = BASE.split("## widgets:")[0]
        rest = "#atlas-ui 1\n## widgets: local | label | opts\n@set screen=SCR-HOME\nbar | 借阅台 | kind=header\n"
        both = rules.check_sources({"ui-10-a.atl": first, "ui-20-b.atl": rest}, mode="full")
        self.assertNotIn("UX-001", both.rule_ids())

    def test_the_binding_rules_wait_for_final_nav_in_slice_mode(self):
        text = sub("SCR-LOGIN.submit | SCR-HOME |", "SCR-LOGIN.submit | SCR-LOGIN |")
        waiting = slice_mode(text)
        self.assertNotIn("UX-201", waiting.rule_ids())
        self.assertEqual(waiting.pending_bind, 1)
        released = slice_mode(text + "@final nav\n")
        self.assertIn("UX-201", released.rule_ids())
        self.assertEqual(released.pending_bind, 0)

    def test_full_mode_runs_the_binding_rules_without_a_final_declaration(self):
        self.assertIn("UX-201", full(sub("SCR-LOGIN.submit | SCR-HOME |", "SCR-LOGIN.submit | SCR-LOGIN |")).rule_ids())

    def test_only_the_incoming_slice_is_reported_in_slice_mode(self):
        bad = sub("route=/home", "route=/login")
        other = "#atlas-ui 1\n## screens: id | title | purpose | opts\nSCR-OTHER | 其它 | 另一个切片。 | route=/other start=true\n"
        result = rules.check_sources({"ui-10-login.atl": bad, "ui-20-other.atl": other}, five=FIVE, mode="slice", incoming=["ui-20-other.atl"])
        self.assertEqual(render_all(result), [])
        result = rules.check_sources({"ui-10-login.atl": bad, "ui-20-other.atl": other}, five=FIVE, mode="slice", incoming=["ui-10-login.atl"])
        self.assertIn("UX-006", result.rule_ids())

    def test_a_red_draft_is_st009_in_full_mode_only(self):
        full_result = rules.check_sources({NAME: BASE}, mode="full", red_drafts=["ui-30-draft"])
        self.assertIn("ST-009", full_result.rule_ids())
        self.assertFalse(full_result.ok)
        self.assertNotIn("ST-009", rules.check_sources({NAME: BASE}, mode="slice", red_drafts=["ui-30-draft"]).rule_ids())

    def test_a_warning_only_slice_commits(self):
        for want in ("UX-006", "UX-007", "UX-011", "SZ-001"):
            text = next(t for rule, _w, t, _f in MUTANTS if rule == want)
            self.assertTrue(slice_mode(text).ok, want)

    def test_the_severity_is_the_registry_severity_not_the_5d_default(self):
        result = full([t for r, _w, t, _f in MUTANTS if r == "UX-011"][0])
        self.assertEqual([d.severity for d in result.diagnostics], ["WARN"])
        self.assertTrue(result.ok)


class LinkTests(unittest.TestCase):
    """AC-16: links to 5D facts are validated only when a 5D model is present."""

    REQ_TYPO = sub("req=RQ-1\nSCR-HOME", "req=RQ-9\nSCR-HOME")

    def test_with_a_5d_model_a_dangling_requirement_is_an_error_in_full_and_pending_in_slice_mode(self):
        full_result = full(self.REQ_TYPO)
        self.assertEqual([(d.rule, d.severity) for d in full_result.diagnostics], [("UX-206", "ERROR")])
        slice_result = slice_mode(self.REQ_TYPO)
        self.assertEqual(render_all(slice_result), [])
        self.assertEqual(slice_result.pending, {"requirement": ["RQ-9"]})

    def test_a_requirement_one_edit_from_a_defined_one_is_a_typo_warning_in_slice_mode(self):
        result = slice_mode(sub("req=RQ-1\nSCR-HOME", "req=RQ-11\nSCR-HOME"))
        self.assertEqual([(d.rule, d.severity) for d in result.diagnostics], [])  # shorter than 5 characters: never a look-alike
        result = slice_mode(sub("api=svc.loan", "api=svc.loon"))
        self.assertEqual([(d.rule, d.severity) for d in result.diagnostics], [("UX-206", "WARN")])

    def test_with_no_5d_model_a_dangling_requirement_is_one_info_and_never_blocks(self):
        for check in (full, slice_mode):
            result = check(self.REQ_TYPO, five=None)
            self.assertEqual([(d.rule, d.severity) for d in result.diagnostics], [("UX-206", "INFO")])
            self.assertTrue(result.ok)

    def test_a_link_to_something_of_the_wrong_kind_is_an_error_even_in_slice_mode(self):
        result = slice_mode(sub("actor=usr.member", "actor=svc.loan"))
        self.assertEqual([(d.rule, d.severity) for d in result.diagnostics], [("UX-206", "ERROR")])

    def test_a_state_needs_a_machine_and_belongs_to_it(self):
        text = sub("SCR-LOGIN | error | 邮箱或密码不正确。 | kind=error", "SCR-LOGIN | error | 邮箱或密码不正确。 | kind=error state=Idle")
        self.assertEqual([d.rule for d in full(text).diagnostics], ["UX-206"])

    def test_the_links_of_a_clean_example_resolve_against_the_5d_fixture(self):
        self.assertEqual(render_all(full(BASE)), [])


class CostTests(unittest.TestCase):
    """AC-11: the interface side of a put (parse, expand, rules; the 5D index given) is cheap."""

    def test_a_12_screen_put_is_far_inside_25_ms_and_with_a_reference_size_5d_model_inside_150_ms(self):
        from . import synth

        sources = ui_synth.sample12_sources()
        index = ex.five_index(synth.generate("reference", seed=3))
        for five, limit in ((ex.five_index(FIVE), 25.0), (index, 150.0)):
            times = []
            for _ in range(15):
                started = time.perf_counter()
                result = rules.check_sources(sources, five=five, mode="slice", incoming=list(sources))
                times.append((time.perf_counter() - started) * 1000)
            self.assertLessEqual(statistics.median(times), limit, f"median {statistics.median(times):.1f} ms")
            self.assertTrue(result.ok)


class HashSeedTests(unittest.TestCase):
    """The rendered findings of every mutant do not depend on ``PYTHONHASHSEED`` (sets are never iterated into the output)."""

    def test_the_findings_of_all_mutants_are_the_same_under_three_hash_seeds(self):
        import os
        import subprocess
        import sys

        repo = Path(__file__).resolve().parents[2]
        code = ("import sys, hashlib; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
                "from tests.atlas import test_ui_rules as t\nfrom gemarc_mcp.atlas import diag as dg\n"
                "h = hashlib.sha256()\n"
                "for want, what, text, five in t.MUTANTS:\n"
                "    r = t.full(text, t.FIVE if five is None else five)\n"
                "    h.update(chr(10).join(dg.render_line(d) + str(d.derived) for d in r.diagnostics).encode('utf-8'))\n"
                "    s = t.slice_mode(text, t.FIVE if five is None else five)\n"
                "    h.update(chr(10).join(dg.render_line(d) for d in s.diagnostics).encode('utf-8') + repr(sorted(s.pending.items())).encode('utf-8'))\n"
                "print(h.hexdigest())\n") % (str(repo / "src"), str(repo))
        digests = set()
        for seed in ("0", "7", "31337"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
            done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, env=env, cwd=str(repo))
            self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace")[-500:])
            digests.add(done.stdout.decode("utf-8").strip())
        self.assertEqual(len(digests), 1, digests)


class RegistryTests(unittest.TestCase):
    def test_the_registry_is_independent_of_the_shared_one(self):
        from gemarc_mcp.atlas.rules import registry as shared

        before = set(shared.REGISTRY)
        self.assertFalse({r for r in rules.REGISTRY if r.startswith("UX-")} & before)
        self.assertEqual(set(shared.REGISTRY), before)

    def test_every_registered_rule_has_a_check_that_reports_it(self):
        reported = {i for _order, ids, _fn in rules.CHECKS for i in ids}
        self.assertEqual(reported, set(rules.REGISTRY))

    def test_no_layout_rule_is_registered_without_a_check(self):
        self.assertFalse([r for r in rules.REGISTRY if r.startswith("UX-1")])

    def test_wave_numbers_modes_and_the_binding_flags_follow_the_design(self):
        wave1 = {r.id for r in rules.rules((1,))}
        self.assertEqual(wave1, {"ST-009", "SZ-001", "UX-001", "UX-002", "UX-003", "UX-006", "UX-007", "UX-008", "UX-009", "UX-010", "UX-011"})
        self.assertEqual({r.id for r in rules.rules((3,))}, {"UX-005", "UX-201", "UX-202", "UX-203", "UX-204", "UX-205", "UX-206", "UX-207"})
        self.assertEqual({r.id for r in rules.REGISTRY.values() if r.binding}, {"UX-201", "UX-202", "UX-205"})
        self.assertEqual({r.id for r in rules.REGISTRY.values() if r.modes == ("full",)}, {"ST-009", "UX-005", "UX-203", "UX-204", "UX-207"})
        self.assertNotIn("UX-004", rules.REGISTRY)


if __name__ == "__main__":
    unittest.main()
