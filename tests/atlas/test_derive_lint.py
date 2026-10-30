"""Lint rules DC-L-001..004 and DG-002/003/005/006, each with a mutation fixture.

``render_skeleton_doc`` is a small test-only renderer of the twelve-chapter skeleton built from the shared helpers of
``derive`` (skeleton text, policy sections, diagram links, Markdown hygiene). It is NOT the document builder (that is the
next lane's); it gives the linter a realistic, clean document to mutate. Every rule is shown to fire on its mutant and to
stay silent on the clean text.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_lint*.py"
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.derive import diagrams as D
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import lint as L
from gemarc_mcp.atlas.derive import policy as P

from . import synth

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lending_desk.model.json"
LANGS = T.LANGS
LEVELS = T.LEVELS


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def render_skeleton_doc(model, lang: str, level: str, selection=None, units=None) -> str:
    """A complete skeleton document for ``model`` (test-only; see the module docstring)."""
    ix = P.Index(model)
    scope, _ = P.resolve_selection(ix, selection)
    units = D.build_units(model, lang)["units"] if units is None else units
    sk = lambda key: T.tr(lang, "skeleton." + key)  # noqa: E731
    out = [f"# {T.md_text(sk('docs.' + level))} {T.code('DD-' + 'a1b2c3d4')}", "", T.md_text(sk("label")), ""]

    def chapter(n):
        out.append("")
        out.append(T.chapter_heading(lang, level, n))
        out.append("")

    chapter(1)
    out += [f"- {sk('doc_info.level')}: {T.md_text(level)}", f"- {sk('doc_info.model')}: {T.code(ix.sha8())}", f"- {sk('doc_info.status')}: {sk('doc_info.draft')}"]
    out += [f"- {T.md_text(line)}" for line in T.tr_list(lang, "skeleton.facts_note")]
    chapter(2)
    rows = []
    for eid in sorted({e for n in scope.node_ids for e in P.str_list(ix.node[n].get("evidenceIds"))}):
        ev = ix.evidence.get(eid, {})
        rows.append([T.code(eid), T.md_cell(ev.get("filePath"), T.slot_name("evidence.filePath", eid)),
                     T.md_cell(ev.get("topic"), T.slot_name("evidence.topic", eid)), T.md_cell(ev.get("verification"), T.slot_name("evidence.verification", eid))])
    out += T.md_table(T.tr_list(lang, "skeleton.th.evidence"), rows) if rows else [T.empty_line(lang, "nodes")]
    chapter(3)
    rows = [[T.code(r), T.md_cell(ix.requirement[r].get("title"), T.slot_name("requirement.title", r)), T.md_cell(ix.requirement[r].get("source"), T.slot_name("requirement.source", r)),
             ", ".join(T.code(t) for t in P.str_list(ix.requirement[r].get("tstIds"))) or T.not_authored(T.slot_name("requirement.tstIds", r))] for r in scope.requirement_ids]
    out += T.md_table(T.tr_list(lang, "skeleton.th.requirement"), rows) if rows else [T.empty_line(lang, "requirements")]
    chapter(4)
    rows = [[T.code(n), T.md_cell(ix.node[n].get("name"), T.slot_name("node.name", n)), T.md_cell(ix.node[n].get("kind"), T.slot_name("node.kind", n)),
             T.md_cell(ix.node[n].get("sub"), T.slot_name("node.sub", n)), ""] for n in scope.node_ids]
    out += T.md_table(T.tr_list(lang, "skeleton.th.node_req"), rows) if rows else [T.empty_line(lang, "nodes")]
    line = D.links_line(units, scope.node_ids, lang)
    if line:
        out += ["", line]
    chapter(5)
    rows = [[T.code(c), T.md_cell(ix.case[c].get("title"), T.slot_name("case.title", c)), T.md_cell(ix.case[c].get("category"), T.slot_name("case.category", c)), "", ""] for c in scope.case_ids]
    out += T.md_table(T.tr_list(lang, "skeleton.th.case"), rows) if rows else [T.empty_line(lang, "cases")]
    chapter(6)
    out.append(T.empty_line(lang, "entities"))
    chapter(7)
    out.append(T.empty_line(lang, "interfaces"))
    chapter(8)
    out.append(T.empty_line(lang, "machines"))
    chapter(9)
    out += P.nfr_chapter(ix, lang, level, scope)
    chapter(10)
    out += P.constraints_chapter(ix, lang, level, scope)
    chapter(11)
    out.append(T.empty_line(lang, "acceptance"))
    chapter(12)
    out += T.md_table(T.tr_list(lang, "skeleton.th.signoff"), [[T.md_text(r), "", "", ""] for r in T.tr_list(lang, "skeleton.signoff.roles")])
    out += ["", T.md_text(sk("signoff.judgement"))]
    return "\n".join(out) + "\n"


def with_nfr(model: dict) -> dict:
    """A copy of ``model`` that carries one nfr policy so chapter 9 has content."""
    m = copy.deepcopy(model)
    req = m["requirements"][0]["id"]
    m["policies"].append(am.build_policy("PL-N1", "Borrowing answers within two seconds.", req, kind="nfr", by="user"))
    return m


def codes(issues):
    return sorted({i["rule"] for i in issues})


class CleanDocumentTests(unittest.TestCase):
    def test_clean_document_has_no_defect_for_every_language_and_level(self):
        model = with_nfr(load_fixture())
        self.assertEqual(am.contract_issues(model), [])
        ix = P.Index(model)
        units = D.build_units(model, "en-US")["units"]
        for lang in LANGS:
            for level in LEVELS:
                text = render_skeleton_doc(model, lang, level, units=D.build_units(model, lang)["units"])
                self.assertEqual(L.lint_document(text, lang=lang, level=level, ix=ix, units=D.build_units(model, lang)["units"]), [], (lang, level))
        self.assertTrue(units)

    def test_missing_nfr_is_exactly_one_release_block(self):
        model = load_fixture()  # no policies at all
        ix = P.Index(model)
        for lang in LANGS:
            text = render_skeleton_doc(model, lang, "BD")
            issues = L.lint_document(text, lang=lang, level="BD", ix=ix, units=D.build_units(model, lang)["units"])
            self.assertEqual(codes(issues), ["DC-L-004"], (lang, issues))
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["severity"], "BLOCK")
            self.assertEqual(issues[0]["got"], "ch9.nfr")
            self.assertEqual(L.release_blockers(issues), issues)


class JunkRuleTests(unittest.TestCase):
    """DC-L-001"""

    def setUp(self):
        self.model = with_nfr(load_fixture())
        self.text = render_skeleton_doc(self.model, "en-US", "DD")

    def test_each_literal_is_found_with_its_line(self):
        for junk in ("undefined", "NaN", "None", "[object Object]", "x:undefined"):
            mutant = self.text.replace("Creates a loan and takes returns.", f"Creates a loan {junk} and takes returns.")
            self.assertNotEqual(mutant, self.text)
            found = [i for i in L.lint_junk(mutant) if i["rule"] == "DC-L-001"]
            self.assertTrue(found, junk)
            self.assertIn("line", found[0]["path"])

    def test_words_that_merely_contain_a_literal_are_not_junk(self):
        for fine in ("Nonetheless it works", "Nano", "nonesuch", "undefinedly"):
            self.assertEqual(L.lint_junk(fine), [], fine)

    def test_junk_is_caught_in_mermaid_text_and_in_prompts(self):
        units = D.build_units(self.model, "en-US")["units"]
        bad = dict(units[0], mermaid=units[0]["mermaid"] + "  n9 --> undefined\n")
        self.assertEqual(codes(L.lint_diagram_text([bad])), ["DC-L-001"])
        self.assertEqual(L.lint_diagram_text(units), [])


class LinkRuleTests(unittest.TestCase):
    """DC-L-002"""

    def setUp(self):
        self.model = with_nfr(load_fixture())
        self.ix = P.Index(self.model)
        self.units = D.build_units(self.model, "en-US")["units"]
        self.text = render_skeleton_doc(self.model, "en-US", "BD", units=self.units)
        self.assertIn("(dg:", self.text)

    def lint(self, text):
        return L.lint_document(text, lang="en-US", level="BD", ix=self.ix, units=self.units)

    def test_clean_text_resolves(self):
        self.assertEqual(self.lint(self.text), [])

    def test_dangling_dg_link_is_found(self):
        unit_id = self.units[0]["id"]
        mutant = self.text.replace(f"(dg:{unit_id})", "(dg:component-nowhere)")
        self.assertEqual(codes(self.lint(mutant)), ["DC-L-002"])

    def test_empty_dg_link_and_foreign_scheme_are_found(self):
        mutant = self.text + "\n[x](dg:)\n[y](javascript:alert1)\n[z](https://example.test/a)\n"
        found = [i for i in self.lint(mutant) if i["rule"] == "DC-L-002"]
        self.assertEqual(len(found), 3, found)

    def test_dangling_id_with_a_known_family_prefix_is_found(self):
        mutant = self.text.replace("`svc.loan`", "`svc.lone`")
        self.assertNotEqual(mutant, self.text)
        found = [i for i in self.lint(mutant) if i["rule"] == "DC-L-002"]
        self.assertTrue(found)
        self.assertEqual(found[0]["got"], "svc.lone")

    def test_known_ids_machine_states_and_event_ids_resolve(self):
        text = self.text + "\n`svc.loan:out` `CS-BORROW-OK#3` `R-BORROW` `RQ-1` `TS-1` `EVD-2` `DD-a1b2c3d4` `Completed` `x_y`\n"
        self.assertEqual(self.lint(text), [])

    def test_a_dangling_event_id_is_found(self):
        found = [i for i in self.lint(self.text + "\n`CS-BORROW-OK#99`\n") if i["rule"] == "DC-L-002"]
        self.assertEqual([i["got"] for i in found], ["CS-BORROW-OK#99"])

    def test_model_text_cannot_forge_a_link(self):
        model = with_nfr(load_fixture())
        model["nodes"][0]["name"] = "[go](javascript:alert1) [x](dg:component-nowhere)"
        text = render_skeleton_doc(model, "en-US", "BD")
        self.assertEqual(L.lint_document(text, lang="en-US", level="BD", ix=P.Index(model), units=D.build_units(model, "en-US")["units"]), [])

    def test_code_blocks_are_skipped_for_links(self):
        text = self.text + "\n```\n[x](dg:nowhere) `svc.lone`\n```\n"
        self.assertEqual(self.lint(text), [])


class ChapterRuleTests(unittest.TestCase):
    """DC-L-003"""

    def setUp(self):
        self.model = with_nfr(load_fixture())
        self.text = render_skeleton_doc(self.model, "zh-CN", "DD")

    def lint(self, text, level="DD"):
        return L.lint_chapters(text, "zh-CN", level)

    def test_clean(self):
        self.assertEqual(self.lint(self.text), [])

    def test_missing_chapter(self):
        heading = T.chapter_heading("zh-CN", "DD", 7)
        mutant = self.text.replace(heading, "")
        found = self.lint(mutant)
        self.assertTrue(any(i["rule"] == "DC-L-003" and i["path"] == "chapter 7" for i in found), found)

    def test_blank_chapter(self):
        lines = self.text.split("\n")
        start = lines.index(T.chapter_heading("zh-CN", "DD", 6))
        end = lines.index(T.chapter_heading("zh-CN", "DD", 7))
        mutant = "\n".join(lines[: start + 1] + [""] + lines[end:])
        found = self.lint(mutant)
        self.assertEqual([i["path"] for i in found], ["chapter 6"])

    def test_chapter_with_only_a_subheading_is_blank(self):
        lines = self.text.split("\n")
        start = lines.index(T.chapter_heading("zh-CN", "DD", 6))
        end = lines.index(T.chapter_heading("zh-CN", "DD", 7))
        mutant = "\n".join(lines[: start + 1] + ["", "### x", ""] + lines[end:])
        self.assertEqual([i["path"] for i in self.lint(mutant)], ["chapter 6"])

    def test_explicit_marker_counts_as_filled(self):
        lines = self.text.split("\n")
        start = lines.index(T.chapter_heading("zh-CN", "DD", 6))
        end = lines.index(T.chapter_heading("zh-CN", "DD", 7))
        for marker in (T.not_authored("ch6.x"), T.NA, T.NONE_IN_SCOPE, T.NONE_DECLARED):
            mutant = "\n".join(lines[: start + 1] + ["", marker, ""] + lines[end:])
            self.assertEqual(self.lint(mutant), [], marker)

    def test_wrong_title_and_wrong_level_title(self):
        mutant = self.text.replace(T.chapter_heading("zh-CN", "DD", 4), "## 4. 随便写的标题")
        found = self.lint(mutant)
        self.assertEqual([i["path"] for i in found], ["chapter 4"])
        self.assertTrue(self.lint(self.text, level="REQ"), "DD titles must not pass as REQ titles")

    def test_order_duplicates_and_out_of_range(self):
        h4, h5 = T.chapter_heading("zh-CN", "DD", 4), T.chapter_heading("zh-CN", "DD", 5)
        swapped = self.text.replace(h4, "@@").replace(h5, h4).replace("@@", h5)
        self.assertTrue(any(i["path"] == "chapters" for i in self.lint(swapped)))
        duplicated = self.text + "\n" + h4 + "\n\nx\n"
        self.assertTrue(any("more than once" in i["hint"] for i in self.lint(duplicated)))
        extra = self.text + "\n## 13. 多余\n\nx\n"
        self.assertTrue(any(i["path"].startswith("line") for i in self.lint(extra)))

    def test_headings_inside_fences_do_not_count(self):
        mutant = self.text.replace(T.chapter_heading("zh-CN", "DD", 7), "```\n" + T.chapter_heading("zh-CN", "DD", 7) + "\n```")
        self.assertTrue(any(i["path"] == "chapter 7" for i in self.lint(mutant)))

    def test_empty_text(self):
        found = self.lint("")
        self.assertEqual(len(found), 12)


class MarkerRuleTests(unittest.TestCase):
    """DC-L-004"""

    def test_one_block_per_slot_with_count(self):
        text = "a [NOT-AUTHORED:node.sub@x] b\n[NOT-AUTHORED:node.sub@x] [NOT-AUTHORED:ch9.nfr]\n"
        found = L.lint_markers(text)
        self.assertEqual([i["got"] for i in found], ["node.sub@x", "ch9.nfr"])
        self.assertTrue(all(i["severity"] == "BLOCK" and i["rule"] == "DC-L-004" for i in found))
        self.assertIn("2x", found[0]["hint"])
        self.assertEqual(found[0]["path"], "line 1")

    def test_other_markers_are_not_blocks(self):
        self.assertEqual(L.lint_markers(f"{T.NA} {T.NONE_IN_SCOPE} {T.NONE_DECLARED}"), [])

    def test_model_text_cannot_forge_or_hide_a_marker(self):
        model = with_nfr(load_fixture())
        model["nodes"][0]["sub"] = "[NOT-AUTHORED:fake]"
        text = render_skeleton_doc(model, "en-US", "BD")
        self.assertNotIn("[NOT-AUTHORED:fake]", text)
        self.assertEqual(L.lint_markers(text), [])
        model["nodes"][0]["sub"] = ""
        text = render_skeleton_doc(model, "en-US", "BD")
        self.assertEqual([i["got"] for i in L.lint_markers(text)], ["node.sub@svc.loan"])

    def test_summary_is_compact(self):
        model = load_fixture()
        text = render_skeleton_doc(model, "en-US", "BD")
        issues = L.lint_document(text, lang="en-US", level="BD", ix=P.Index(model), units=D.build_units(model, "en-US")["units"])
        summary = L.summarize(issues, text, "en-US", "BD")
        self.assertEqual(summary["chapters"], {"filled": 12, "total": 12})
        self.assertEqual((summary["junk"], summary["unresolvedLinks"], summary["notAuthored"], summary["blocks"]), (0, 0, 1, 1))
        self.assertLessEqual(len(json.dumps(summary)), 1024)


class ClassEnumRuleTests(unittest.TestCase):
    """DG-002"""

    @staticmethod
    def model_with_class(change=lambda states, index: list(states)):
        m = synth.edge_case("machine", 5)
        owner_edge = next(e for e in m["edges"] if e["relation"] == "owns")
        owner = owner_edge["sourceId"]
        machine_ids = [x["id"] for x in m["machines"]]
        room = m["nodes"][0]["roomId"]
        node = am.build_node("if.status", "Interface", "Status port", room, "Exposes the status.", evidence_ids=[])
        linked = am.linked_machine_ids({"props": {"owner": owner}}, m["edges"], machine_ids)
        by_id = {x["id"]: x for x in m["machines"]}
        node["props"] = {"owner": owner, "methods": ["status()"],
                         "enums": {f"Status{i}": change(by_id[mid]["states"], i) for i, mid in enumerate(linked)}}
        m["nodes"].append(node)
        m["ports"] += am.derive_ports([node])
        return m, linked, node

    def run_rule(self, model, node):
        units = D.build_units(model, "en-US", kinds=["class"])["units"]
        return [i for i in L.lint_diagrams(model, units) if i["rule"] == "DG-002" and node["id"] in i["path"]]

    def test_matching_enums_are_clean(self):
        m, linked, node = self.model_with_class()
        self.assertTrue(linked)
        self.assertEqual(self.run_rule(m, node), [])

    def test_a_different_enum_is_found_for_exactly_that_machine(self):
        m, linked, node = self.model_with_class(lambda states, i: list(states)[:-1] if i == 0 else list(states))
        found = self.run_rule(m, node)
        self.assertEqual([(i["severity"], i["got"]) for i in found], [("WARN", linked[0])])

    def test_no_enum_at_all_is_found_for_every_linked_machine(self):
        m, linked, node = self.model_with_class()
        node["props"]["enums"] = {}
        self.assertEqual(sorted(i["got"] for i in self.run_rule(m, node)), sorted(linked))

    def test_enum_without_linked_machine_is_a_hint(self):
        m, linked, node = self.model_with_class(lambda states, i: ["A", "B"])
        node["props"].pop("owner")
        found = self.run_rule(m, node)
        self.assertEqual([i["severity"] for i in found], ["INFO"])


class LayerRuleTests(unittest.TestCase):
    """DG-003"""

    def build(self, source_level, target_level):
        m = load_fixture()
        m["nodes"].append(am.build_node("svc.api", "Interface", "Api", "rm.vis", "Exposes it.", evidence_ids=["EVD-3"]))
        m["ports"] += am.derive_ports([m["nodes"][-1]])
        m["edges"].append(am.build_edge("svc.loan", "dependsOn", "svc.api", "needs the api", evidence_ids=["EVD-3"]))
        floors = {f["id"]: f for f in m["space"]["floors"]}
        floors["lvl.core"]["level"] = source_level
        floors["lvl.out"]["level"] = target_level
        return m

    def issues(self, model):
        return [i for i in L.lint_diagrams(model, D.build_units(model, "en-US")["units"]) if i["rule"] == "DG-003"]

    def test_dependency_to_a_lower_floor_is_fine(self):
        self.assertEqual(self.issues(self.build(2, 1)), [])
        self.assertEqual(self.issues(self.build(1, 1)), [])

    def test_upward_dependency_is_found(self):
        found = self.issues(self.build(1, 2))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["got"], "svc.loan:dependsOn:svc.api")

    def test_runtime_relations_are_not_layered(self):
        m = self.build(1, 2)
        m["edges"] = [e for e in m["edges"] if e["relation"] != "dependsOn"] + [am.build_edge("svc.loan", "calls", "svc.api", "calls it", evidence_ids=["EVD-3"])]
        self.assertEqual(self.issues(m), [])

    def test_unknown_floor_is_not_a_layer_issue(self):
        m = self.build(1, 2)
        m["space"]["rooms"][1]["floorId"] = "lvl.nowhere"
        self.assertEqual(self.issues(m), [])


class ErForeignKeyRuleTests(unittest.TestCase):
    """DG-005"""

    @staticmethod
    def build(columns_a, cardinality="N:1"):
        m = load_fixture()
        room = "rm.desk"
        for nid, cols in (("tbl.a", columns_a), ("tbl.b", ["id"])):
            node = am.build_node(nid, "Table", nid, room, "A table.", evidence_ids=["EVD-1"])
            node["props"]["columns"] = cols
            m["nodes"].append(node)
            m["ports"] += am.derive_ports([node])
        m["edges"].append(am.build_edge("tbl.a", "references", "tbl.b", "points at b", evidence_ids=["EVD-1"], cardinality=cardinality))
        return m

    def issues(self, model):
        return [i for i in L.lint_diagrams(model, D.build_units(model, "en-US")["units"]) if i["rule"] == "DG-005"]

    def test_fk_column_satisfies_the_rule(self):
        self.assertEqual(self.issues(self.build([{"name": "b_id", "fk": "tbl.b"}])), [])

    def test_missing_fk_is_found(self):
        found = self.issues(self.build([{"name": "b_id"}]))
        self.assertEqual([i["got"] for i in found], ["tbl.a:references:tbl.b"])

    def test_plain_string_columns_cannot_carry_fk(self):
        self.assertEqual(len(self.issues(self.build(["b_id"]))), 1)

    def test_fk_naming_another_table_is_found(self):
        self.assertEqual(len(self.issues(self.build([{"name": "b_id", "fk": "tbl.zzz"}]))), 1)

    def test_er_text_carries_flags_and_cardinality_without_inventing_types(self):
        m = self.build([{"name": "b_id", "fk": "tbl.b", "pk": True}, "note"])
        er = next(u for u in D.build_units(m, "en-US")["units"] if u["type"] == "er")
        self.assertIn("unspecified note", er["mermaid"])
        self.assertIn("PK, FK", er["mermaid"])
        self.assertIn("}o--||", er["mermaid"])


class ErPolicyRuleTests(unittest.TestCase):
    """DG-006"""

    def build(self, scope):
        m = ErForeignKeyRuleTests.build([{"name": "b_id", "fk": "tbl.b", "pii": True}])
        m["policies"].append(am.build_policy("PL-D1", "Rows are kept for seven years.", "RQ-1", scope=scope, kind="data", by="user"))
        return m

    def issues(self, model):
        return [i for i in L.lint_diagrams(model, D.build_units(model, "en-US")["units"]) if i["rule"] == "DG-006"]

    def test_scopes_that_hit_a_table_are_clean(self):
        for scope in ("all", "kind:Table", "node:tbl.a", "req:RQ-1"):
            m = self.build(scope)
            if scope == "req:RQ-1":
                m["requirements"][0]["nodeIds"].append("tbl.a")
            self.assertEqual(self.issues(m), [], scope)

    def test_scopes_that_hit_nothing_are_found(self):
        for scope in ("kind:Module", "node:svc.loan", "req:RQ-1"):
            self.assertEqual(len(self.issues(self.build(scope))), 1, scope)

    def test_er_unit_lists_data_policies_and_pii_flags_only(self):
        m = self.build("all")
        m["policies"].append(am.build_policy("PL-C1", "A constraint.", "RQ-1", kind="constraint"))
        er = next(u for u in D.build_units(m, "en-US")["units"] if u["type"] == "er")
        self.assertEqual([p["id"] for p in er["data"]["policies"]], ["PL-D1"])
        self.assertEqual(er["data"]["piiColumns"], [{"table": "tbl.a", "column": "b_id"}])
        self.assertNotIn("seven years", er["mermaid"])  # policy text is data for documents, never baked into the drawing

    def test_no_policy_is_hard_coded(self):
        m = ErForeignKeyRuleTests.build([{"name": "b_id", "fk": "tbl.b"}])
        er = next(u for u in D.build_units(m, "en-US")["units"] if u["type"] == "er")
        self.assertEqual(er["data"]["policies"], [])
        self.assertEqual(er["data"]["piiColumns"], [])


class ModelLintTests(unittest.TestCase):
    def test_lint_model_on_the_fixture(self):
        model = with_nfr(load_fixture())
        docs = {"BD": ("BD", render_skeleton_doc(model, "en-US", "BD"))}
        result = L.lint_model(model, lang="en-US", documents=docs)
        self.assertEqual([i for i in result["issues"] if i["severity"] in ("BLOCK", "ERROR")], [])
        self.assertEqual(result["summary"]["policiesByAi"], 0)

    def test_policy_row_problems_are_reported(self):
        model = with_nfr(load_fixture())
        model["policies"].append({"id": "PL-X", "text": "", "src": "", "scope": "node:nope", "level": "XX", "chapter": None, "kind": "zzz", "by": "robot"})
        result = L.lint_model(model, lang="en-US")
        self.assertEqual(codes(i for i in result["issues"] if i["rule"].startswith("DC-P")), ["DC-P-001", "DC-P-002", "DC-P-003", "DC-P-004"])

    def test_internal_error_has_its_own_code(self):
        class Boom(dict):
            def get(self, *a, **k):
                raise RuntimeError("boom")

        issues = L.lint_diagrams(Boom(), [{"type": "x"}])
        self.assertEqual([i["rule"] for i in issues], ["DG-099"])  # reported with its own code, never raised

    def test_reference_scale_models_lint_without_raising(self):
        for seed in (1, 2):
            model = synth.generate("reference", seed)
            lang = "ja-JP"
            result = L.lint_model(model, lang=lang, documents={"DD": ("DD", render_skeleton_doc(model, lang, "DD"))})
            self.assertEqual([i for i in result["issues"] if i["rule"] in ("DC-L-001", "DC-L-002", "DC-L-003", "DG-099", "DC-L-099")], [], seed)


if __name__ == "__main__":
    unittest.main()
