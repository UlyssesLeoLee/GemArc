"""The interface expander and model (``protoui/expand.py``, ``protoui/model.py``): AC-03, AC-06, AC-08, AC-12, AC-18 and the round trip.

The two-screen worked example expands to a pinned model (``ui_fixtures/login.model.json``, ``login.checkpoints.tsv``); the 12-screen sample
(``ui_fixtures/sample12``) is pinned by counts and ``ui_sha8``; 50 seeded random synthetic models make the round trip
``spec -> .atl -> model -> spec -> .atl -> model`` a fixed point. Synthetic ids only.

Rewrite the goldens after a deliberate change of the model:  PYTHONPATH=src python -m tests.atlas.test_ui_expand --write-golden

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_expand.py"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.protoui import atl, expand as ex, model as um, rules, vocab

from . import synth, ui_synth

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
UI_FIXTURES = HERE / "ui_fixtures"
SRC = HERE.parents[1] / "src"

#: The pinned numbers of the 12-screen sample (re-measured and pinned by U1).
SAMPLE12 = {"screens": 12, "variants": 23, "widgets": 79, "nav": 30, "bytes": 8833, "checkpoints": 316, "sha8": "e1b61479"}
LOGIN_SHA8 = "9da9bdd5"


def expand_text(text: str, name: str = "ui-10-login.atl", five=None) -> "ex.UiExpansion":
    return ex.expand_ui([atl.parse_atl(text, name)], five)


def login_expansion() -> "ex.UiExpansion":
    return expand_text(ui_synth.login_text())


def golden_files() -> dict:
    exp = login_expansion()
    cps = "".join(f"{c.ref}\t{c.value}\n" for c in um.checkpoints(exp.model))
    return {UI_FIXTURES / "login.model.json": um.canonical_bytes(exp.model, indent=1), UI_FIXTURES / "login.checkpoints.tsv": cps.encode("utf-8")}


class WorkedExampleTests(unittest.TestCase):
    """AC-03: the example expands to the pinned model without a single diagnostic."""

    def test_no_diagnostic_and_the_model_is_the_pinned_golden_byte_for_byte(self):
        exp = login_expansion()
        self.assertEqual(exp.diagnostics, [])
        golden = (UI_FIXTURES / "login.model.json").read_bytes()
        self.assertEqual(um.canonical_bytes(exp.model, indent=1), golden)
        self.assertEqual(json.loads(golden), exp.model)
        self.assertEqual(um.ui_sha8(exp.model), LOGIN_SHA8)

    def test_the_check_points_are_the_pinned_golden(self):
        cps = "".join(f"{c.ref}\t{c.value}\n" for c in um.checkpoints(login_expansion().model))
        self.assertEqual(cps.encode("utf-8"), (UI_FIXTURES / "login.checkpoints.tsv").read_bytes())
        self.assertEqual(cps.count("\n"), 36)

    def test_the_golden_files_are_lf_only_and_end_with_one_newline(self):
        for name in ("login.model.json", "login.checkpoints.tsv", "ui-10-login.atl"):
            data = (UI_FIXTURES / name).read_bytes()
            self.assertNotIn(b"\r", data, name)
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), name)

    def test_the_model_has_the_contract_shape_and_the_schema_is_versioned(self):
        model = login_expansion().model
        self.assertEqual(model["schema"], "gemarc-atlas-ui/1")
        self.assertEqual(um.contract_issues(model), [])
        self.assertEqual(sorted(model), ["meta", "nav", "schema", "screens", "variants", "widgets"])
        self.assertEqual(model["meta"], {"projectId": "project", "title": "", "languages": ["zh-CN"], "profile": "ui"})

    def test_the_example_is_the_size_the_design_gives(self):
        self.assertEqual(len(ui_synth.login_text().encode("utf-8")), 1261)

    def test_the_header_version_must_be_1(self):
        text = ui_synth.login_text().replace("#atlas-ui 1", "#atlas-ui 2", 1)
        self.assertIn("ST-004", {d.rule for d in expand_text(text).diagnostics})


class DerivedFieldTests(unittest.TestCase):
    def setUp(self):
        self.model = login_expansion().model
        self.screens = {s["id"]: s for s in self.model["screens"]}
        self.widgets = {w["id"]: w for w in self.model["widgets"]}

    def test_the_defaults_are_filled_in(self):
        text = "#atlas-ui 1\n## screens: id | title | purpose | opts\nSCR-A | A | a.\n## widgets: local | label | opts\n@set screen=SCR-A\nx | X | kind=text\n"
        model = expand_text(text).model
        self.assertEqual({k: model["screens"][0][k] for k in ("kind", "frame", "start")}, {"kind": "page", "frame": "desktop", "start": False})
        self.assertEqual(model["screens"][0]["variantNames"], ["default"])
        self.assertEqual(model["widgets"][0]["inVariants"], None)
        text += "## variants: screen | name | note | opts\nSCR-A | gone | g.\n## nav: from | to | label | opts\nSCR-A.x | SCR-A#gone | go\n"
        model = expand_text(text).model
        self.assertEqual(model["variants"][0]["kind"], "other")
        self.assertEqual(model["nav"][0]["on"], "tap")

    def test_the_default_variant_comes_first_and_is_never_a_record(self):
        self.assertEqual(self.screens["SCR-LOGIN"]["variantNames"], ["default", "loading", "error"])
        self.assertNotIn("SCR-LOGIN#default", {v["id"] for v in self.model["variants"]})

    def test_the_tree_fields(self):
        form, email, err = self.widgets["SCR-LOGIN.form"], self.widgets["SCR-LOGIN.email"], self.widgets["SCR-LOGIN.err"]
        self.assertEqual((form["parentId"], form["order"], form["depth"]), (None, 0, 0))
        self.assertEqual(form["childIds"], ["SCR-LOGIN.email", "SCR-LOGIN.pwd", "SCR-LOGIN.err", "SCR-LOGIN.submit"])
        self.assertEqual((email["parentId"], email["order"], email["depth"], email["childIds"]), ("SCR-LOGIN.form", 0, 1, []))
        self.assertEqual((err["order"], err["inVariants"]), (2, ["error"]))

    def test_widget_ids_are_the_pre_order_of_the_tree(self):
        self.assertEqual(self.screens["SCR-LOGIN"]["widgetIds"],
                         ["SCR-LOGIN.form", "SCR-LOGIN.email", "SCR-LOGIN.pwd", "SCR-LOGIN.err", "SCR-LOGIN.submit"])
        self.assertEqual(self.screens["SCR-HOME"]["widgetIds"], ["SCR-HOME.bar", "SCR-HOME.logout", "SCR-HOME.loans"])

    def test_the_nav_ids_and_the_in_and_out_lists(self):
        ids = [n["id"] for n in self.model["nav"]]
        self.assertEqual(ids, ["SCR-LOGIN.submit:go:SCR-HOME", "SCR-HOME.logout:go:SCR-LOGIN"])
        self.assertEqual(self.screens["SCR-LOGIN"]["navOutIds"], [ids[0]])
        self.assertEqual(self.screens["SCR-LOGIN"]["navInIds"], [ids[1]])
        nav = self.model["nav"][0]
        self.assertEqual((nav["fromScreenId"], nav["toScreenId"], nav["on"], nav["ruleId"], nav["caseId"]), ("SCR-LOGIN", "SCR-HOME", "submit", "R-BORROW", "CS-BORROW-OK"))

    def test_a_nav_to_a_variant_counts_for_the_screen_of_the_variant(self):
        text = (ui_synth.login_text().replace("SCR-HOME.logout | SCR-LOGIN |", "SCR-HOME.logout | SCR-LOGIN#error |")
                .replace("SCR-LOGIN | loading", "SCR-LOGIN | loading"))
        model = expand_text(text).model
        self.assertEqual(model["nav"][1]["toScreenId"], "SCR-LOGIN")
        self.assertEqual(model["nav"][1]["id"], "SCR-HOME.logout:go:SCR-LOGIN#error")
        self.assertEqual(len(model["screens"][0]["navInIds"]), 1)

    def test_an_authored_derived_key_is_dv001_and_the_row_is_dropped(self):
        text = ui_synth.login_text().replace("kind=link parent=bar", "kind=link parent=bar order=2")
        exp = expand_text(text)
        self.assertEqual([d.rule for d in exp.diagnostics], ["DV-001"])
        self.assertNotIn("SCR-HOME.logout", {w["id"] for w in exp.model["widgets"]})

    def test_expansion_never_changes_with_the_5d_model_it_is_given(self):
        five = ex.five_index(json.loads((HERE / "fixtures" / "lending_desk.model.json").read_text(encoding="utf-8")))
        self.assertEqual(expand_text(ui_synth.login_text(), five=five).model, self.model)
        self.assertEqual(expand_text(ui_synth.login_text(), five=five).diagnostics, [])

    def test_the_5d_index_knows_what_a_link_can_point_at(self):
        index = ex.five_index(json.loads((HERE / "fixtures" / "lending_desk.model.json").read_text(encoding="utf-8")))
        self.assertEqual(index.nodes["usr.member"], "Actor")
        self.assertIn("RQ-1", index.requirements)
        self.assertIn("R-BORROW", index.cases["CS-BORROW-OK"])
        self.assertFalse(index.empty)
        self.assertTrue(ex.FiveIndex().empty)
        self.assertIsNone(ex.five_index(None))


class IdTests(unittest.TestCase):
    """AC-08: ids are injective, also at their longest."""

    def longest(self) -> dict:
        screen_a, screen_b = "A" + "b" * 39, "C" + "d" * 39
        local_a, local_b = "w" + "x" * 31, "v" + "y" * 31
        rows = ["#atlas-ui 1", "## screens: id | title | purpose | opts", f"{screen_a} | T | p | start=true", f"{screen_b} | T | p",
                "## variants: screen | name | note | opts", f"{screen_b} | " + "n" * 24 + " | n",
                "## widgets: local | label | opts", f"@set screen={screen_a}", f"{local_a} | L | kind=button",
                f"@set screen={screen_b}", f"{local_b} | L | kind=button",
                "## nav: from | to | label | opts", f"{screen_a}.{local_a} | {screen_b}#{'n' * 24} | go",
                f"{screen_b}.{local_b} | {screen_a} | go"]
        return {"text": "\n".join(rows) + "\n", "a": screen_a, "b": screen_b, "la": local_a, "lb": local_b}

    def test_the_longest_ids_expand_without_a_diagnostic_and_the_long_nav_id_is_hashed(self):
        data = self.longest()
        exp = expand_text(data["text"])
        self.assertEqual(exp.diagnostics, [])
        ids = [n["id"] for n in exp.model["nav"]]
        self.assertEqual(len(ids[0]) > um.NAV_ID_LIMIT, False)
        self.assertTrue(ids[0].startswith("h:") and len(ids[0]) == 34, ids[0])
        self.assertEqual(ids[1], f"{data['b']}.{data['lb']}:go:{data['a']}")
        self.assertLessEqual(len(ids[1]), um.NAV_ID_LIMIT)
        self.assertEqual(len(f"{data['a']}.{data['la']}"), 40 + 1 + 32)

    def test_every_derived_id_of_every_kind_is_distinct_and_a_variant_id_is_not_a_5d_id(self):
        for model in (login_expansion().model, expand_text(self.longest()["text"]).model, rules.check_sources(ui_synth.sample12_sources()).model):
            ids = ([s["id"] for s in model["screens"]] + [v["id"] for v in model["variants"]] + [w["id"] for w in model["widgets"]]
                   + [n["id"] for n in model["nav"]])
            self.assertEqual(len(ids), len(set(ids)))
            for v in model["variants"]:
                self.assertIsNone(am.ID_RE.fullmatch(v["id"]), v["id"])
            for s in model["screens"]:
                self.assertNotRegex(s["id"], r"[.:#~]")

    def test_the_hash_form_depends_on_the_pair_only(self):
        a = um.nav_id("S1.x", "S2#" + "n" * 100)
        self.assertEqual(a, um.nav_id("S1.x", "S2#" + "n" * 100))
        self.assertNotEqual(a, um.nav_id("S1.y", "S2#" + "n" * 100))
        self.assertEqual(um.nav_id("S1.x", "S2", alias="back"), "back")

    def test_an_alias_that_equals_a_screen_or_widget_id_is_st002(self):
        for alias in ("SCR-HOME", "SCR-LOGIN.email"):
            text = ui_synth.login_text().replace("on=submit guard", f"on=submit alias={alias} guard")
            self.assertIn("ST-002", {d.rule for d in expand_text(text).diagnostics}, alias)

    def test_a_second_nav_row_for_the_same_pair_needs_an_alias(self):
        row = "SCR-HOME.logout | SCR-LOGIN | 另一条 | on=tap\n"
        exp = expand_text(ui_synth.login_text() + row)
        self.assertEqual([d.rule for d in exp.diagnostics], ["ST-002"])
        self.assertIn("alias=", exp.diagnostics[0].hint)
        self.assertEqual(expand_text(ui_synth.login_text() + row.replace("on=tap", "on=tap alias=back-again")).diagnostics, [])

    def test_the_split_and_split_helpers(self):
        self.assertEqual(um.split_widget("SCR-A.x"), ("SCR-A", "x"))
        self.assertIsNone(um.split_widget("SCR-A"))
        self.assertEqual(um.split_variant("SCR-A#empty"), ("SCR-A", "empty"))
        self.assertEqual(um.root_id("SCR-A"), "SCR-A.root")


class DeterminismTests(unittest.TestCase):
    """AC-06."""

    def test_the_same_slices_give_the_same_bytes_and_hash_twice(self):
        a, b = rules.check_sources(ui_synth.sample12_sources()).model, rules.check_sources(ui_synth.sample12_sources()).model
        self.assertEqual(um.canonical_bytes(a), um.canonical_bytes(b))
        self.assertEqual(um.ui_sha8(a), um.ui_sha8(b))

    def test_the_model_holds_no_timestamp_and_no_path(self):
        text = um.canonical_bytes(rules.check_sources(ui_synth.sample12_sources()).model).decode("utf-8")
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text))
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]|\\\\|/Users/|/home/|/tmp/", text))

    def test_the_hash_does_not_depend_on_the_hash_seed(self):
        code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
                "from tests.atlas import ui_synth\nfrom gemarc_mcp.atlas.protoui import rules, model as um\n"
                "print(um.ui_sha8(rules.check_sources(ui_synth.sample12_sources()).model))\n"
                "spec = ui_synth.generate(9, 4, 'zh')\nprint(um.ui_sha8(rules.check_sources(ui_synth.render(spec)).model))\n") % (str(SRC), str(HERE.parents[1]))
        outputs = set()
        for seed in ("0", "1", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
            done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, env=env, check=True, cwd=str(HERE.parents[1]))
            outputs.add(done.stdout.decode("utf-8"))
        self.assertEqual(len(outputs), 1, outputs)
        first = outputs.pop().split()
        self.assertEqual(first[0], SAMPLE12["sha8"])

    def test_the_prototype_hash_does_not_depend_on_a_verdict_slice(self):
        verdict = "#atlas-ui 1\n## conform: screen | verdict | basis | opts\nSCR-LOGIN | unchecked | fe:src/a.tsx:1 | checked=0\n"
        sources = {"ui-10-login.atl": ui_synth.login_text(), "ui-verdict-fe.atl": verdict}
        with_verdict = rules.check_sources(sources)
        self.assertEqual(with_verdict.diagnostics and [d for d in with_verdict.diagnostics if d.severity == "ERROR"], [])
        self.assertEqual(um.ui_sha8(with_verdict.model), LOGIN_SHA8)
        self.assertEqual(um.verdict_sha8(with_verdict.expansion.verdict), um.verdict_sha8({"conform": [{"screen": "SCR-LOGIN", "verdict": "unchecked", "basis": "fe:src/a.tsx:1", "checked": 0}], "diffs": []}))
        self.assertNotEqual(um.verdict_sha8(with_verdict.expansion.verdict), um.verdict_sha8({}))


class Sample12Tests(unittest.TestCase):
    """AC-12 and the pinned numbers of the 12-screen sample."""

    def setUp(self):
        self.sources = ui_synth.sample12_sources()
        self.result = rules.check_sources(self.sources, five=json.loads((HERE / "fixtures" / "lending_desk.model.json").read_text(encoding="utf-8")), mode="full")

    def test_the_sample_checks_clean_against_the_5d_fixture(self):
        self.assertEqual([dg.render_line(d) for d in self.result.diagnostics], [])

    def test_the_counts_the_bytes_the_hash_and_the_check_points_are_pinned(self):
        model = self.result.model
        self.assertEqual({k: len(model[k]) for k in ("screens", "variants", "widgets", "nav")},
                         {k: SAMPLE12[k] for k in ("screens", "variants", "widgets", "nav")})
        self.assertEqual(sum(len(t.encode("utf-8")) for t in self.sources.values()), SAMPLE12["bytes"])
        self.assertEqual(um.ui_sha8(model), SAMPLE12["sha8"])
        self.assertEqual(len(um.checkpoints(model)), SAMPLE12["checkpoints"])

    def test_the_author_cost_is_inside_the_acceptance_bounds(self):
        self.assertLessEqual(sum(len(t.encode("utf-8")) for t in self.sources.values()), 12 * 1024)
        widget_rows = [line for text in self.sources.values() for line in text.split("\n")
                       if re.match(r"^[a-z][a-z0-9_-]* \| ", line) and "kind=" in line and not line.startswith("SCR")]
        self.assertEqual(len(widget_rows), SAMPLE12["widgets"])
        self.assertLessEqual(sum(len(r.encode("utf-8")) for r in widget_rows) / len(widget_rows), 100)

    def test_the_expansion_is_about_3_5_times_the_authored_text(self):
        expanded = len(um.canonical_bytes(self.result.model))
        self.assertGreater(expanded / SAMPLE12["bytes"], 3.0)
        self.assertLess(expanded / SAMPLE12["bytes"], 4.0)


class RoundTripTests(unittest.TestCase):
    """The round trip of U1: spec -> .atl -> model -> spec -> .atl -> model is a fixed point, for 50 seeded random synthetic models."""

    def test_fifty_random_models_check_clean_and_survive_the_round_trip_with_two_different_slicings(self):
        for seed in range(50):
            screens = 2 + (seed * 7) % 19
            spec = ui_synth.generate(screens, seed, lang="zh" if seed % 2 else "en", family="fixed" if seed % 3 == 0 else "random")
            first = rules.check_sources(ui_synth.render(spec, per_slice=1 + seed % 5), mode="full")
            with self.subTest(seed=seed, screens=screens):
                self.assertEqual([dg.render_line(d) for d in first.diagnostics], [])
                self.assertEqual(len(first.model["screens"]), screens)
                self.assertEqual(len(first.model["widgets"]), len(spec["widgets"]))
                self.assertEqual(len(first.model["nav"]), len(spec["nav"]))
                second = rules.check_sources(ui_synth.render(ui_synth.encode_model(first.model), per_slice=1 + (seed + 2) % 5), mode="full")
                self.assertEqual(second.diagnostics, [])
                self.assertEqual(second.model, first.model)
                self.assertEqual(um.ui_sha8(second.model), um.ui_sha8(first.model))

    def test_a_slice_put_in_slice_mode_shows_no_error_whatever_the_slicing(self):
        spec = ui_synth.generate(10, 5, "en")
        sources = ui_synth.render(spec, per_slice=3)
        for name in sources:
            result = rules.check_sources(sources, mode="slice", incoming=[name])
            self.assertTrue(result.ok, (name, [dg.render_line(d) for d in result.errors]))

    def test_the_generated_links_resolve_against_a_synthetic_5d_model(self):
        five = synth.generate("small", seed=2)
        for seed in range(5):
            spec = ui_synth.generate(8, seed, "en", five=five)
            result = rules.check_sources(ui_synth.render(spec), five=five, mode="full")
            self.assertEqual([dg.render_line(d) for d in result.diagnostics if d.rule != "UX-207"], [], seed)   # the coverage INFO is expected

    def test_the_generator_is_deterministic_and_the_screens_are_distinct(self):
        self.assertEqual(ui_synth.generate(6, 3, "zh"), ui_synth.generate(6, 3, "zh"))
        self.assertNotEqual(ui_synth.generate(6, 3, "zh"), ui_synth.generate(6, 4, "zh"))
        ids = [s["id"] for s in ui_synth.generate(30, 1)["screens"]]
        self.assertEqual(len(ids), len(set(ids)))


class VocabularyTests(unittest.TestCase):
    def test_55_labelled_ids_and_the_group_sizes(self):
        self.assertEqual(len(vocab.WIDGET_KINDS), 31)
        self.assertEqual([len(vocab.ENUMS[g]) for g in vocab.LABELLED_GROUPS], [31, 4, 3, 6, 4, 7])
        self.assertEqual(len(vocab.label_ids()), 55)

    def test_the_flags(self):
        self.assertEqual(sorted(vocab.CONTAINERS), ["card", "footer", "form", "grid", "header", "list", "row", "sidebar", "stack", "tabs", "toolbar"])
        self.assertEqual(sorted(vocab.NAV_SOURCES), sorted(["button", "card", "icon", "image", "link", "list", "menu", "table", "tab"]))
        self.assertEqual({"input", "textarea", "select", "checkbox", "radio", "switch", "button", "link", "tab", "menu", "pagination"}, set(vocab.INTERACTIVE))

    def test_the_option_names_come_from_the_section_table(self):
        self.assertEqual(vocab.OPTION_NAMES["widgets"], ("screen", "kind", "parent", "w", "h", "cols", "in", "bind", "emph", "data", "api", "req", "align"))
        self.assertEqual(vocab.OPTION_NAMES["screens"], ("kind", "frame", "route", "start", "actor", "req", "group", "align"))
        self.assertEqual(vocab.OPTION_NAMES["variants"], ("kind", "machine", "state", "text"))
        self.assertEqual(vocab.OPTION_NAMES["nav"], ("on", "guard", "api", "case", "rule", "req", "alias"))


if __name__ == "__main__":
    if "--write-golden" in sys.argv:
        for path, data in golden_files().items():
            path.write_bytes(data)
            print("wrote", path)
    else:
        unittest.main()
