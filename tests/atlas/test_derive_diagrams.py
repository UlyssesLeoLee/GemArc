"""Skeleton tables, text hygiene, Mermaid builders and diagram units.

Covers: the three i18n tables (identical keys, size 3-6 KB for the skeleton, exact label, no junk literal), Markdown and
Mermaid injection hygiene, every builder on garbage input (never raises), the unit builder on the lending-desk fixture,
reference-scale synthetic models and the edge-case models in three languages (zero ``undefined|NaN|None|[object``),
stable link-safe unit ids, determinism, and the extension point for the sequence kind.

The Mermaid text is checked with a conservative structural validator written here; it is NOT the Mermaid parser (no
Mermaid library is available offline), so "syntactically valid for Mermaid" is verified structurally only.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_diagrams*.py"
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.derive import diagrams as D
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import mermaid as M
from gemarc_mcp.atlas.derive import policy as P

from . import synth

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lending_desk.model.json"
LANGS = T.LANGS
FIRST_LINES = {"flowchart LR", "classDiagram", "stateDiagram-v2", "erDiagram"}
ENTITY = re.compile(r"#(?:quot|lt|gt|amp|\d+);")
BAD_IN_LABEL = re.compile(r"[<>&{}|\\`%;~\"]")
LINK_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def mermaid_problems(text: str) -> list:
    """Structural problems of Mermaid text (conservative; see the module docstring)."""
    problems = []
    lines = text.split("\n")
    if text.endswith("\n") is False:
        problems.append("no trailing newline")
    body = [l for l in lines if l.strip()]
    if not body or body[0] not in FIRST_LINES:
        problems.append(f"first line {body[:1]}")
        return problems
    depth = 0
    for n, line in enumerate(lines, 1):
        if any(ord(ch) < 32 and ch != "\t" for ch in line):
            problems.append(f"{n}: control character")
        if "%%" in line or "`" in line:
            problems.append(f"{n}: comment marker or backtick")
        if line.count('"') % 2:
            problems.append(f"{n}: unbalanced quotes")
        for q in re.findall(r'"([^"]*)"', line):
            stripped = ENTITY.sub("", q)
            stripped = re.sub(r"\[NOT-AUTHORED:[A-Za-z0-9._@#:-]*\]", "", stripped)
            if BAD_IN_LABEL.search(stripped):
                problems.append(f"{n}: unescaped character in label")
        if re.match(r"^\s*subgraph ", line):
            depth += 1
        if line.strip() == "end":
            depth -= 1
        if depth < 0:
            problems.append(f"{n}: end without subgraph")
    if depth != 0:
        problems.append("subgraph without end")
    if body[0] in ("classDiagram", "erDiagram"):
        opens = sum(1 for l in lines if l.rstrip().endswith("{"))
        closes = sum(1 for l in lines if l.strip() == "}")
        if opens != closes:
            problems.append("unbalanced braces")
    if body[0] == "flowchart LR":
        declared = set(re.findall(r"^\s*(n\d+)(?:\(|\{|\[)", text, re.M))
        used = set(re.findall(r"\b(n\d+)\b", re.sub(r'"[^"]*"', "", text)))
        if used - declared:
            problems.append(f"undeclared nodes {sorted(used - declared)}")
    return problems


def junk(text: str) -> list:
    return T.JUNK_RE.findall(text)


class TableTests(unittest.TestCase):
    def tables(self):
        return {lang: json.loads((T.I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}

    @staticmethod
    def keys(value, prefix=""):
        if isinstance(value, dict):
            out = set()
            for k, v in value.items():
                out |= TableTests.keys(v, f"{prefix}{k}.")
            return out
        if isinstance(value, list):
            return {f"{prefix}[{len(value)}]"}
        return {prefix.rstrip(".")}

    def test_three_languages_have_identical_keys_and_list_lengths(self):
        t = self.tables()
        base = self.keys(t["en-US"])
        for lang in LANGS:
            self.assertEqual(self.keys(t[lang]), base, lang)

    def test_every_value_is_a_non_empty_string_and_free_of_junk(self):
        def walk(value, path):
            if isinstance(value, dict):
                for k, v in value.items():
                    yield from walk(v, f"{path}.{k}")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    yield from walk(v, f"{path}[{i}]")
            else:
                yield path, value

        for lang, table in self.tables().items():
            for path, value in walk(table, lang):
                self.assertIsInstance(value, str, path)
                self.assertTrue(value.strip(), path)
                self.assertEqual(junk(value), [], path)

    def test_placeholders_are_the_same_in_every_language(self):
        t = self.tables()

        def holders(value):
            return sorted(re.findall(r"\{([a-z_]+)\}", json.dumps(value, ensure_ascii=False)))

        for section in ("prompts", "diagrams"):
            for key in t["en-US"][section]:
                base = holders(t["en-US"][section][key])
                for lang in LANGS:
                    self.assertEqual(holders(t[lang][section][key]), base, f"{lang}.{section}.{key}")
        for lang in LANGS:
            self.assertEqual(holders(t[lang]["skeleton"]["upper_note"]), ["doc", "n"])

    def test_skeleton_size_is_three_to_six_kilobytes_per_language(self):
        for lang, table in self.tables().items():
            size = len(json.dumps(table["skeleton"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.assertTrue(3 * 1024 <= size <= 6 * 1024, (lang, size))

    def test_the_structure_label_is_the_required_text(self):
        self.assertEqual(T.skeleton("en-US")["label"], "IPA-inspired structure (not an official IPA template)")
        self.assertEqual(T.skeleton("zh-CN")["label"], "参考 IPA 的结构(非官方模板)")
        for lang in LANGS:
            self.assertNotIn("standard", T.skeleton(lang)["label"].lower())

    def test_twelve_chapters_per_level_and_stable_numbering(self):
        for lang in LANGS:
            for level in T.LEVELS:
                titles = [T.chapter_title(lang, level, n) for n in range(1, 13)]
                self.assertEqual(len(set(titles)), 12, (lang, level))
                self.assertTrue(all(not t.startswith("[NOT-AUTHORED") for t in titles))
                self.assertEqual(T.chapter_heading(lang, level, 3), f"## 3. {titles[2]}")
        for lang in LANGS:  # chapters 1, 2, 9, 10, 12 read the same at every level
            for n in (1, 2, 9, 10, 12):
                self.assertEqual({T.chapter_title(lang, lv, n) for lv in T.LEVELS}.__len__(), 1)

    def test_every_table_header_set_exists_and_has_the_documented_width(self):
        widths = {"evidence": 4, "requirement": 4, "coverage": 6, "node_req": 5, "node_bd": 7, "edge_min": 5, "edge_bd": 6, "edge_dd": 10,
                  "case": 5, "event": 6, "entity": 5, "column": 4, "port": 4, "transition": 5, "tbd": 4, "policy": 3, "exception": 4,
                  "testplan": 2, "crossmap": 4, "signoff": 4}
        for lang in LANGS:
            for key, width in widths.items():
                self.assertEqual(len(T.tr_list(lang, f"skeleton.th.{key}")), width, (lang, key))

    def test_unknown_key_and_unknown_language_never_raise(self):
        self.assertTrue(T.NOT_AUTHORED_RE.search(T.tr("en-US", "skeleton.nope")))
        self.assertEqual(T.tr_list("en-US", "skeleton.nope"), [])
        self.assertEqual(T.normalize_lang("xx"), "zh-CN")
        self.assertEqual(T.normalize_lang(None), "zh-CN")
        self.assertEqual(T.chapter_title("klingon", "DD", 1), T.chapter_title("zh-CN", "DD", 1))
        self.assertEqual(T.fill("a {x} b", x=None), f"a {T.not_authored('x')} b")
        self.assertEqual(T.fill(None), "")

    def test_the_tables_are_read_only(self):
        with self.assertRaises(Exception):
            T.skeleton("en-US")["label"] = "x"


class HygieneTests(unittest.TestCase):
    NASTY = ["a|b", "line1\nline2\r\nline3\tx", "`code`", "```fence```", "~~~fence~~~", "[x](javascript:alert(1))", "![img](x)",
             "<script>alert(1)</script>", "# heading", "- bullet", "1. item", "> quote", "[NOT-AUTHORED:fake]", "\x00\x1b[31mred\x07",
             "\u202eRTL", "back\\slash", "*star* _under_"]

    def test_md_text_neutralises_structure(self):
        for raw in self.NASTY:
            out = T.md_text(raw)
            self.assertNotIn("\n", out)
            self.assertNotIn("\r", out)
            self.assertNotRegex(out, r"(?<!\\)\|", raw)
            self.assertNotRegex(out, r"(?<!\\)`", raw)
            self.assertNotRegex(out, r"(?<!\\)~", raw)
            self.assertNotIn("<", out)
            self.assertNotIn(">", out)
            self.assertIsNone(T.NOT_AUTHORED_RE.search(out), raw)
            self.assertNotRegex(out, r"(?<!\\)\]\(", raw)
            self.assertNotRegex(out, r"^(?:#|>|-|\+|\d+[.)])", raw)
            self.assertNotRegex(out, "[\x00-\x1f\x7f\u202e]")

    def test_md_text_is_total(self):
        for value in (None, 0, 3.5, float("nan"), float("inf"), True, [], {}, [1, "a", None], {"a": None}, object, b"x"):
            out = T.md_text(value)
            self.assertIsInstance(out, str)
            self.assertEqual(junk(out), [], repr(value))

    def test_scalar_never_prints_a_bare_none(self):
        self.assertEqual(T.scalar(None), "")
        self.assertEqual(T.scalar({"a": None}), '{"a": null}')
        self.assertEqual(T.scalar(float("nan")), T.NON_FINITE)  # a marker, not an empty cell and not the text NaN

    def test_a_table_cell_cannot_break_out(self):
        cell = T.md_cell("x | y\n| z", "slot")
        lines = T.md_table(["a", "b"], [[cell, "c"]])
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", lines[2])), 3)

    def test_empty_cell_with_slot_is_a_marker_and_without_slot_stays_empty(self):
        self.assertEqual(T.md_cell("", "node.sub@x"), "[NOT-AUTHORED:node.sub@x]")
        self.assertEqual(T.md_cell("   ", None), "")
        self.assertEqual(T.md_cell(None, "a b"), "[NOT-AUTHORED:a_b]")

    def test_slot_names_are_confined(self):
        self.assertTrue(T.NOT_AUTHORED_RE.fullmatch(T.not_authored("x]y [z:\n" + "q" * 200)))
        self.assertLessEqual(len(T.slot_name("a" * 500)), 60)

    def test_code_span_cannot_close_early(self):
        self.assertEqual(T.code("a`b|c"), "`a'b\\|c`")
        self.assertEqual(T.code(""), "")

    def test_dg_link_ids_are_link_safe(self):
        link = T.dg_link("a [b]", "x y)z")
        self.assertRegex(link, r"^\[a \\\[b\\\]\]\(dg:x_y_z\)$")

    def test_issue_got_is_clipped_and_control_stripped(self):
        item = T.issue("X-1", "WARN", "p", "h", got="a\nb" + "z" * 200)
        self.assertLessEqual(len(item["got"]), 80)
        self.assertNotIn("\n", item["got"])
        self.assertEqual(T.issue("X-1", "nonsense", "p", "h")["severity"], "ERROR")


class MermaidBuilderTests(unittest.TestCase):
    EVIL = 'a"b<c>&d#e%%f;g{h}[i]|j\\k`l~m\nn\r\n'

    def test_labels_never_carry_structure(self):
        label = M.label(self.EVIL)
        self.assertEqual(BAD_IN_LABEL.sub("", ENTITY.sub("", label)), ENTITY.sub("", label))
        self.assertNotIn("\n", label)
        self.assertIn("#quot;", label)

    def test_authored_text_cannot_produce_a_marker_in_a_label(self):
        label = M.label("[NOT-AUTHORED:fake]")
        self.assertIsNone(T.NOT_AUTHORED_RE.search(label))
        self.assertIsNotNone(T.NOT_AUTHORED_RE.search(M.marker("real.slot")))

    def test_raw_text_uses_a_safe_alphabet(self):
        out = M.raw(self.EVIL + "ok(1, 2)")
        self.assertRegex(out, r"^[\w .,()/+=@'-]*$")

    def test_id_map_is_deterministic_and_collision_free(self):
        ids = M.IdMap("n")
        a, b, c = ids("svc.loan"), ids("a:b"), ids("svc.loan")
        self.assertEqual((a, b, c), ("n1", "n2", "n1"))
        self.assertEqual(len(ids), 2)

    def test_component_with_hostile_ids_and_labels(self):
        data = {"groups": [{"id": "g:1", "label": self.EVIL, "nodeIds": ["a.b", "c:d"]}],
                "nodes": [{"id": "a.b", "label": self.EVIL, "shape": "hex"}, {"id": "c:d", "label": "", "shape": "weird"},
                          {"id": "e", "label": "plain", "shape": "round"}],
                "edges": [{"id": "x", "from": "a.b", "to": "c:d", "label": self.EVIL, "static": True},
                          {"id": "y", "from": "a.b", "to": "zzz", "label": "dangling"}, {"id": "z", "from": "e", "to": "a.b"}]}
        text, issues = M.build_component(data)
        self.assertEqual(mermaid_problems(text), [], text)
        self.assertIn("-.->", text)
        self.assertNotIn("zzz", text)
        self.assertEqual(sorted({i["rule"] for i in issues}), ["DG-011"])
        self.assertTrue(any("c:d" in i.get("got", "") or "c_d" in i["path"] for i in issues))
        self.assertIn("[NOT-AUTHORED:node.name@c:d]", text)
        self.assertIn("[NOT-AUTHORED:edge.label@z]", text)

    def test_class_members_and_stereotypes(self):
        data = {"classes": [{"id": "k.one", "label": "One", "annotation": "interface", "members": ["+save(a: int)", "<<evil>>", "x\ny", ""],
                             "missing": ["props.methods"]},
                            {"id": "k.two", "label": "Two", "annotation": "bad annotation!", "members": []}],
                "relations": [{"from": "k.one", "to": "k.two", "label": "uses {x}"}, {"from": "k.one", "to": "nope"}]}
        text, issues = M.build_class(data)
        self.assertEqual(mermaid_problems(text), [], text)
        self.assertIn("<<interface>> c1", text)
        self.assertNotIn("bad annotation", text)
        self.assertNotIn("<<evil>>", text)
        self.assertIn("[NOT-AUTHORED:props.methods@k.one]", text)
        self.assertTrue(any(i["rule"] == "DG-011" for i in issues))

    def test_state_diagram_variants(self):
        ok, issues = M.build_state({"initial": "A", "states": ["A", "B", "C"], "terminal": ["C"],
                                    "transitions": [{"from": "A", "to": "B", "label": "x present"}, {"from": "B", "to": "C", "label": ""}]})
        self.assertEqual((mermaid_problems(ok), issues), ([], []), ok)
        self.assertIn("[*] --> s1", ok)
        self.assertIn("s3 --> [*]", ok)
        for data, slot in (({"states": ["A"]}, "machine.initial"), ({}, "machine.states"), (None, "machine.states"), ({"initial": "A"}, None)):
            text, found = M.build_state(data)
            self.assertEqual(mermaid_problems(text), [], text)
            self.assertTrue(found or slot is None, data)
            if slot:
                self.assertIn(f"[NOT-AUTHORED:{slot}]", text)

    def test_state_transition_to_an_undeclared_state_still_draws(self):
        text, issues = M.build_state({"initial": "A", "states": ["A"], "transitions": [{"from": "A", "to": "Z"}, {"from": "", "to": "A"}]})
        self.assertIn("Z", text)
        self.assertEqual(len(issues), 1)

    def test_er_cardinalities_types_and_flags(self):
        self.assertEqual(M.cardinality_symbols("N:1"), ("}o", "||", True))
        self.assertEqual(M.cardinality_symbols("1:0..1"), ("||", "o|", True))
        self.assertEqual(M.cardinality_symbols("1..N:N"), ("}|", "o{", True))
        for bad in (None, "", "N", "N:1:1", "x:y", 3):
            self.assertEqual(M.cardinality_symbols(bad), ("}o", "o{", False))
        data = {"entities": [{"id": "t.a", "label": "A", "columns": [{"name": "id", "pk": True, "type": "int"}, {"name": "col-x y", "type": "bad type!"},
                                                                       {"name": "id"}, {"name": ""}, {"name": 5}]},
                             {"id": "t.b", "label": "B", "columns": []}],
                "relations": [{"id": "r", "from": "t.a", "to": "t.b", "label": "a to b", "cardinality": "N:1"},
                              {"id": "s", "from": "t.a", "to": "t.b"}, {"id": "q", "from": "t.a", "to": "none"}]}
        text, issues = M.build_er(data)
        self.assertEqual(mermaid_problems(text), [], text)
        self.assertIn("int id PK", text)
        self.assertIn("unspecified col_x_y", text)
        self.assertIn("unspecified columns", text)
        self.assertIn("t1 }o--|| t2", text)
        self.assertIn("unspecified", text)
        self.assertEqual(sum(1 for i in issues if i["rule"] == "DG-011"), 4)

    def test_every_builder_survives_garbage(self):
        garbage = [None, 0, "x", [], [1, None], {}, {"nodes": None}, {"nodes": [1, "a", None]}, {"classes": "x"}, {"entities": [None]},
                   {"states": "abc", "transitions": [3]}, {"nodes": [{"id": 5}], "edges": [{"from": [], "to": {}}]}]
        for kind, builder in M.BUILDERS.items():
            for data in garbage:
                text, issues = builder(data)
                self.assertEqual(mermaid_problems(text), [], (kind, data, text))
                self.assertIsInstance(issues, list)
                self.assertEqual(junk(text), [], (kind, data))

    def test_registry_and_extension_point(self):
        text, issues = M.build("sequence", {})
        self.assertEqual([i["rule"] for i in issues], ["DG-008"])
        M.register_builder("sequence-test", lambda data: ("sequenceDiagram\n", []))
        try:
            self.assertEqual(M.build("sequence-test", {})[0], "sequenceDiagram\n")
            with self.assertRaises(ValueError):
                M.register_builder("sequence-test", lambda data: ("", []))
            with self.assertRaises(ValueError):
                M.register_builder("Bad Name", lambda data: ("", []))
        finally:
            M.BUILDERS.pop("sequence-test", None)
        self.assertEqual(sorted(M.BUILDERS), ["class", "component", "er", "state"])

    def test_builder_exception_is_reported_with_its_own_code(self):
        M.BUILDERS["boom-test"] = lambda data: 1 / 0
        try:
            text, issues = M.build("boom-test", {})
        finally:
            M.BUILDERS.pop("boom-test", None)
        self.assertEqual([i["rule"] for i in issues], ["DG-099"])

    def test_oversized_diagram_is_flagged(self):
        nodes = [{"id": f"n{i}", "label": "x" * 70} for i in range(900)]
        text, issues = M.build_component({"nodes": nodes})
        self.assertIn("DG-009", [i["rule"] for i in issues])


class UnitTests(unittest.TestCase):
    def check_units(self, model, result, label):
        ids = [u["id"] for u in result["units"]]
        self.assertEqual(len(ids), len(set(ids)), label)
        node_ids = {n["id"] for n in model["nodes"]}
        edge_ids = {e["id"] for e in model["edges"]}
        case_ids = {c["caseId"] for c in model["cases"]}
        for unit in result["units"]:
            self.assertRegex(unit["id"], LINK_SAFE, label)
            self.assertEqual(sorted(unit), sorted(D.UNIT_FIELDS))
            self.assertLessEqual(set(unit["covers"]["nodeIds"]), node_ids, (label, unit["id"]))
            self.assertLessEqual(set(unit["covers"]["edgeIds"]), edge_ids, (label, unit["id"]))
            self.assertLessEqual(set(unit["covers"]["caseIds"]), case_ids, (label, unit["id"]))
            self.assertEqual(mermaid_problems(unit["mermaid"]), [], (label, unit["id"], unit["mermaid"][:400]))
            self.assertEqual(junk(unit["mermaid"]), [], (label, unit["id"]))
            self.assertEqual(junk(json.dumps(unit, ensure_ascii=False)), [], (label, unit["id"]))
            self.assertLessEqual(len(unit["mermaid"]), M.MAX_TEXT, (label, unit["id"]))
            self.assertTrue(unit["title"].strip())
        for item in result["issues"]:
            self.assertNotIn(item["rule"], ("DG-099", "DG-008"), (label, item))

    def test_fixture_in_three_languages(self):
        model = load_fixture()
        for lang in LANGS:
            result = D.build_units(model, lang)
            self.check_units(model, result, lang)
            self.assertEqual([u["id"] for u in result["units"]], ["component-zone.lib"])
            self.assertEqual(result["issues"], [])
        self.assertIn("Loan service", D.build_units(model, "en-US")["units"][0]["mermaid"])

    def test_reference_scale_synthetic_models_in_three_languages(self):
        for seed in (1, 7, 21):
            model = synth.generate("reference", seed)
            for lang in LANGS:
                result = D.build_units(model, lang)
                self.check_units(model, result, f"reference/{seed}/{lang}")
                kinds = {u["type"] for u in result["units"]}
                self.assertLessEqual({"component", "class", "state", "er"}, kinds)

    def test_small_and_edge_case_models(self):
        models = [("small", synth.generate("small", 4))] + [(name, synth.edge_case(name, 4)) for name in synth.EDGE_CASES]
        for name, model in models:
            for lang in LANGS:
                self.check_units(model, D.build_units(model, lang), f"{name}/{lang}")

    def test_titles_follow_the_language(self):
        model = load_fixture()
        titles = {lang: D.build_units(model, lang)["units"][0]["title"] for lang in LANGS}
        self.assertEqual(len(set(titles.values())), 3)
        self.assertIn("Library system", titles["en-US"])

    def test_two_builds_are_identical(self):
        model = synth.generate("reference", 5)
        a = json.dumps(D.build_units(model, "ja-JP"), ensure_ascii=False, sort_keys=True)
        b = json.dumps(D.build_units(model, "ja-JP"), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)

    def test_unit_ids_are_stable_under_an_unrelated_change(self):
        model = synth.generate("small", 2)
        before = [u["id"] for u in D.build_units(model, "en-US")["units"]]
        model["tbds"].append(am.build_tbd("TBD-NEW", "title", "decision"))
        self.assertEqual([u["id"] for u in D.build_units(model, "en-US")["units"]], before)

    def test_unit_kinds_and_ids(self):
        model = synth.edge_case("machine", 3)
        units = D.build_units(model, "en-US")["units"]
        by = {}
        for u in units:
            by.setdefault(u["type"], []).append(u["id"])
        self.assertTrue(all(i.startswith("component-") for i in by["component"]))
        self.assertTrue(all(i.startswith("class-") for i in by["class"]))
        self.assertEqual(sorted(by["state"]), sorted("state-" + m["id"] for m in model["machines"]))
        self.assertEqual(by["er"], ["er-all"])

    def test_kinds_filter_and_unknown_kind(self):
        model = load_fixture()
        self.assertEqual(D.build_units(model, "en-US", kinds=["er"])["units"], [])
        result = D.build_units(model, "en-US", kinds=["sequence"])
        self.assertEqual([i["rule"] for i in result["issues"]], ["DG-008"])

    def test_unit_builder_extension_point(self):
        def sequence_units(ix, lang):
            data = {}
            return [{"id": "sequence-demo", "type": "sequence", "title": "Demo", "covers": {"nodeIds": [], "edgeIds": [], "caseIds": []},
                     "requirementIds": [], "evidenceLevel": "Unknown", "evidenceIds": [], "data": data, "mermaid": "", "issues": []}], []

        D.register_unit_builder("sequence", sequence_units)
        try:
            ids = [u["id"] for u in D.build_units(load_fixture(), "en-US")["units"]]
            self.assertIn("sequence-demo", ids)
            with self.assertRaises(ValueError):
                D.register_unit_builder("sequence", lambda ix, lang: ([], []))
        finally:
            D.UNIT_BUILDERS.pop("sequence", None)
        self.assertEqual(sorted(D.UNIT_BUILDERS), sorted(D.KINDS))

    def test_links_and_files(self):
        model = synth.generate("small", 3)
        result = D.build_units(model, "en-US")
        units = result["units"]
        line = D.links_line(units, [units[0]["covers"]["nodeIds"][0]], "en-US")
        self.assertIn("(dg:", line)
        self.assertEqual(D.links_line(units, ["nope"], "en-US"), "")
        files = D.diagram_files(units)
        self.assertEqual(sorted(files), sorted(f"{u['id']}.mmd" for u in units))
        ix = P.Index(model)
        table = next(n["id"] for n in model["nodes"] if n["kind"] in ("Entity", "Table"))
        self.assertEqual(D.best_unit_for_node(ix, units, table)["type"], "er")

    def test_hostile_text_in_the_model_stays_inside_labels(self):
        model = load_fixture()
        evil = 'x"]\n  n9 --> n1\n%% hi `\n<script>'
        model["nodes"][0]["name"] = evil
        model["space"]["floors"][0]["name"] = evil
        model["edges"][1]["label"] = evil
        for lang in LANGS:
            for unit in D.build_units(model, lang)["units"]:
                self.assertEqual(mermaid_problems(unit["mermaid"]), [], unit["mermaid"])
                self.assertNotIn("<script>", unit["mermaid"])
                self.assertNotIn("%% hi", unit["mermaid"])

    def test_very_large_component_is_split_by_floor(self):
        model = synth.edge_case("big400", 2)
        # force a tiny limit so the split path runs on a model that is otherwise small
        old = D.SPLIT_LIMIT
        D.SPLIT_LIMIT = 300
        try:
            result = D.build_units(model, "en-US", kinds=["component"])
        finally:
            D.SPLIT_LIMIT = old
        self.assertTrue(any(i["rule"] == "DG-010" for i in result["issues"]))
        ids = [u["id"] for u in result["units"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(re.match(r"component-.+-.+", i) for i in ids))


if __name__ == "__main__":
    unittest.main()
