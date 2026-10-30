"""The REQ / BD / DD document chain: chain invariants DC-001..006, markers, hygiene, robustness, determinism.

The chain invariants (internal self-checks, never shown to the AI) are checked on the lending-desk fixture, a model with
every feature (the deletion-matrix base) and the reference-scale synthetic, in all three languages and at all three levels:

* DC-001 chapters: chapters 1..12 once, in order, with the skeleton titles, none blank (also for the empty selection);
* DC-002 id superset: the ids named in code spans of REQ are a subset of those of BD, and BD of DD (same selection);
* DC-003 id chain: ``RD-`` / ``BD-`` / ``DD-`` numbers, and each document names the numbers of its upper and lower document;
* DC-004 inherited lines: the lines of chapters 2, 9, 10, 11 and 12 of an upper document appear verbatim in the lower one;
* DC-005 depth separation: tables that belong to a deeper level (ports, columns, events, transitions, guards) are absent from
  upper documents and present in DD;
* DC-006 diagram links: every ``dg:`` link resolves to a unit, sits in chapter 4 and illustrates a node (or, for the
  building overview that covers no node, an edge) listed there.

Plus: zero literal defects, explicit markers, every TBD rendered, chapter 9, policy scoping, no engine output, text hygiene
(a forged marker or a stray pipe cannot break a table), the deletion matrix of docs-d1 (imported, not edited) against the
fragment builder, every synthetic edge case, and byte-identical output under different hash seeds.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_docs*.py"
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import unittest
from functools import lru_cache
from pathlib import Path

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.derive import assemble as A
from gemarc_mcp.atlas.derive import diagrams as D
from gemarc_mcp.atlas.derive import fragments as F
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import lint as L
from gemarc_mcp.atlas.derive import policy as P

from . import synth
from .test_derive_deletion_matrix import ROWS, apply_row, build_base

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lending_desk.model.json"
LANGS = T.LANGS
LEVELS = T.LEVELS
INTERNAL = {"DC-D-099", "DC-L-099", "DG-099"}
CODE_SPAN = re.compile(r"`([^`\n]+)`")
NUMBER = re.compile(r"^(?:RD|BD|DD)-[0-9a-f]{8}$")
HEX8 = re.compile(r"^[0-9a-f]{8}$")


# --------------------------------------------------------------------------------------------- shared helpers

@lru_cache(maxsize=None)
def fixture_model():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def base_model():
    return build_base()


@lru_cache(maxsize=None)
def reference_model(seed: int = 7):
    return synth.generate("reference", seed)


def code_tokens(text: str) -> set:
    """Ids named in code spans (document numbers and hex stamps are not elements)."""
    tokens = {t.replace("\\|", "|") for t in CODE_SPAN.findall(text)}
    return {t for t in tokens if not NUMBER.match(t) and not HEX8.match(t)}


def cells_in(line: str) -> int:
    """Number of cells of a table row (an escaped character never ends a cell)."""
    body = line.strip()[1:-1]
    count, i = 1, 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == "|":
            count += 1
        i += 1
    return count


def table_problems(text: str) -> list:
    """Rows whose cell count differs from the header's (a stray pipe or a lost cell breaks a table)."""
    problems, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\|(?:---\|)+", lines[i + 1]):
            width, j = cells_in(lines[i]), i + 2
            while j < len(lines) and lines[j].startswith("|"):
                if cells_in(lines[j]) != width:
                    problems.append((j + 1, lines[j][:80]))
                j += 1
            i = j
        else:
            i += 1
    return problems


def chapter_lines(text: str, number: int) -> list:
    for c in L.parse_chapters(text):
        if c["number"] == number:
            return [x for x in c["body"] if x.strip() and not x.startswith(">")]
    return []


def header_line(lang: str, key: str) -> str:
    return T.md_table(T.tr_list(lang, "skeleton.th." + key), [])[0]


class Built:
    """A model with its docsets and units per language (built once)."""

    def __init__(self, model):
        self.model = model
        self.ix = P.Index(model)
        self.langs = {}
        for lang in LANGS:
            units = D.build_units(model, lang)["units"]
            self.langs[lang] = (F.build_docset(model, lang, units), units)

    def text(self, lang, level, ids=None):
        ds = self.langs[lang][0]
        return A.assemble(ds, level, ds["fragments"]["elements"] if ids is None else ids)


@lru_cache(maxsize=None)
def built(name: str) -> Built:
    return Built({"fixture": fixture_model, "base": base_model, "reference": reference_model}[name]())


def sample_selections(b: Built, lang: str) -> list:
    """Whole model, empty, one element of every kind, a few requirements."""
    ds = b.langs[lang][0]
    els = ds["fragments"]["elements"]
    ix = b.ix
    picks = [None, [], list(ix.node)[:1], list(ix.edge)[:1], list(ix.case)[:1], list(ix.requirement)[:1], list(ix.case)[:3] + list(ix.node)[:4]]
    return [p for p in picks if p is None or isinstance(p, list)] if els else [None, []]


# --------------------------------------------------------------------------------------------- the chain invariants

class ChainInvariantTests(unittest.TestCase):
    def each(self):
        for name in ("fixture", "base", "reference"):
            b = built(name)
            for lang in LANGS:
                for ids in sample_selections(b, lang):
                    yield name, b, lang, ids

    def texts(self, b, lang, ids):
        return {level: b.text(lang, level, ids) for level in LEVELS}

    def test_dc001_chapters(self):
        for name, b, lang, ids in self.each():
            for level, text in self.texts(b, lang, ids).items():
                with self.subTest(model=name, lang=lang, level=level, sel=str(ids)[:30]):
                    self.assertEqual(L.lint_chapters(text, lang, level), [])
                    self.assertEqual([c["number"] for c in L.parse_chapters(text)], list(range(1, 13)))

    def test_zero_literal_defects(self):
        for name, b, lang, ids in self.each():
            units = b.langs[lang][1]
            for level, text in self.texts(b, lang, ids).items():
                with self.subTest(model=name, lang=lang, level=level):
                    self.assertEqual(T.JUNK_RE.findall(text), [])
                    self.assertEqual(L.lint_junk(text), [])
                    self.assertEqual(L.lint_links(text, units, b.ix), [])
                    self.assertEqual(table_problems(text), [])
                    self.assertNotIn("\x01", text)
                    self.assertTrue(text.endswith("\n") and "\r" not in text)

    def test_dc002_id_superset(self):
        for name, b, lang, ids in self.each():
            t = self.texts(b, lang, ids)
            with self.subTest(model=name, lang=lang, sel=str(ids)[:30]):
                self.assertLessEqual(code_tokens(t["REQ"]), code_tokens(t["BD"]))
                self.assertLessEqual(code_tokens(t["BD"]), code_tokens(t["DD"]))

    def test_dc003_id_chain(self):
        for name, b, lang, ids in self.each():
            t = self.texts(b, lang, ids)
            numbers = {}
            for level in LEVELS:
                m = re.match(r"# .* ((?:RD|BD|DD)-[0-9a-f]{8})\n", t[level])
                self.assertIsNotNone(m, (name, lang, level))
                numbers[level] = m.group(1)
            self.assertEqual([numbers[x][:2] for x in LEVELS], ["RD", "BD", "DD"])
            self.assertEqual(len(set(numbers.values())), 3)
            self.assertIn(f"`{numbers['REQ']}`", t["BD"].split("## 2.")[0])
            self.assertIn(f"`{numbers['BD']}`", t["REQ"].split("## 2.")[0])
            self.assertIn(f"`{numbers['BD']}`", t["DD"].split("## 2.")[0])
            self.assertIn(f"`{numbers['DD']}`", t["BD"].split("## 2.")[0])
            self.assertEqual(t["REQ"].split("## 2.")[0].count("[N/A]"), 1)
            self.assertEqual(t["DD"].split("## 2.")[0].count("[N/A]"), 1)

    def test_dc003_numbers_follow_the_selection_and_the_model(self):
        b = built("base")
        ds = b.langs["en-US"][0]
        a = A.assemble(ds, "BD", [list(b.ix.node)[0]]).split("\n", 1)[0]
        c = A.assemble(ds, "BD", [list(b.ix.node)[1]]).split("\n", 1)[0]
        again = A.assemble(ds, "BD", [list(b.ix.node)[0]]).split("\n", 1)[0]
        self.assertNotEqual(a, c)
        self.assertEqual(a, again)
        changed = copy.deepcopy(base_model())
        changed["nodes"][0]["name"] = "Another name"
        other = F.build_docset(changed, "en-US")
        self.assertNotEqual(A.assemble(other, "BD", [list(b.ix.node)[0]]).split("\n", 1)[0], a)

    def test_dc004_inherited_lines(self):
        for name, b, lang, ids in self.each():
            t = self.texts(b, lang, ids)
            for upper, lower in (("REQ", "BD"), ("BD", "DD")):
                lines = set(t[lower].split("\n"))
                for chapter in (2, 9, 10, 11, 12):
                    for line in chapter_lines(t[upper], chapter):
                        if chapter == 2 and not line.startswith("| `"):
                            continue  # the one-sentence purpose of chapter 2 is level specific by design
                        if line.startswith("[NONE-"):
                            continue  # an explicit empty state is replaced when the deeper level has content
                        with self.subTest(model=name, lang=lang, pair=upper + lower, chapter=chapter):
                            self.assertIn(line, lines)
            lines = set(t["BD"].split("\n"))
            for line in chapter_lines(t["REQ"], 5):
                if line.startswith("| `"):
                    self.assertIn(line, lines)

    def test_dc005_depth_separation(self):
        deeper_than_req = ("port", "column", "event", "transition", "edge_dd", "exception", "crossmap", "coverage", "node_bd", "edge_bd")
        deeper_than_bd = ("column", "event", "edge_dd", "exception", "crossmap")
        for lang in LANGS:
            for name in ("base", "reference"):
                t = self.texts(built(name), lang, None)
                req, bd, dd = (set(t[x].split("\n")) for x in LEVELS)
                for key in deeper_than_req:
                    self.assertNotIn(header_line(lang, key), req, (name, lang, key))
                for key in deeper_than_bd:
                    self.assertNotIn(header_line(lang, key), bd, (name, lang, key))
                for key in ("edge_dd", "event", "crossmap", "coverage") + (("edge_min",) if name == "reference" else ()):
                    self.assertIn(header_line(lang, key), dd, (name, lang, key))
                self.assertIn(header_line(lang, "coverage"), bd)
            base_dd = set(self.texts(built("base"), lang, None)["DD"].split("\n"))
            for key in ("column", "transition", "exception"):
                self.assertIn(header_line(lang, key), base_dd, (lang, key))
            ref = self.texts(built("reference"), lang, None)
            for level in ("BD", "DD"):
                self.assertIn(header_line(lang, "port"), set(ref[level].split("\n")), (lang, level))
                self.assertIn(header_line(lang, "transition"), set(ref[level].split("\n")), (lang, level))
            self.assertNotIn(header_line(lang, "port"), set(ref["REQ"].split("\n")))

    def test_dc006_dg_links(self):
        for name, b, lang, ids in self.each():
            units = {u["id"]: u for u in b.langs[lang][1]}
            for level, text in self.texts(b, lang, ids).items():
                links = re.findall(r"\]\(dg:([^)\s]*)\)", text)
                with self.subTest(model=name, lang=lang, level=level):
                    if level == "REQ":
                        self.assertEqual(links, [])
                        continue
                    chapter4 = "\n".join(next(c for c in L.parse_chapters(text) if c["number"] == 4)["body"])
                    outside = text.replace(chapter4, "")
                    self.assertEqual(re.findall(r"\]\(dg:([^)\s]*)\)", outside), [])
                    for link in links:
                        self.assertIn(link, units)
                        cover = units[link]["covers"]
                        ids = cover["nodeIds"] or cover["edgeIds"]  # the building overview covers no node: it illustrates edges
                        covered = [n for n in ids if f"`{n}`" in chapter4]
                        self.assertTrue(covered, f"{link} illustrates no node or edge listed in chapter 4")


# --------------------------------------------------------------------------------------------- content

class ContentTests(unittest.TestCase):
    def test_chapter_9_marker_and_policies(self):
        for lang in LANGS:
            for level in LEVELS:
                text = built("fixture").text(lang, level)
                self.assertIn("[NOT-AUTHORED:ch9.nfr]", "\n".join(chapter_lines(text, 9)))
                issues = L.lint_document(text, lang=lang, level=level, ix=built("fixture").ix, units=built("fixture").langs[lang][1])
                self.assertEqual([i["got"] for i in L.release_blockers(issues)], ["ch9.nfr"])
                filled = built("base").text(lang, level)
                self.assertNotIn("[NOT-AUTHORED:ch9.nfr]", filled)
                self.assertIn("`RQ-1`", "\n".join(chapter_lines(filled, 9)))  # the src requirement of the nfr policy

    def test_every_tbd_is_rendered_even_for_an_unrelated_selection(self):
        b = built("base")
        for lang in LANGS:
            for level in LEVELS:
                for ids in ([], [list(b.ix.edge)[0]]):
                    text = b.text(lang, level, ids)
                    ch10 = "\n".join(chapter_lines(text, 10))
                    self.assertIn("`TBD-1`", ch10)
                    self.assertIn("`TBD-2`", ch10)
        big = built("reference")
        for tbd in big.ix.tbds:
            self.assertIn(f"`{tbd['id']}`", big.text("en-US", "REQ", []))

    def test_explicit_empty_states_instead_of_blanks(self):
        fixture = built("fixture")
        text = fixture.text("en-US", "DD", [])
        for n in (3, 4, 5, 6, 7, 8, 11):
            body = "\n".join(chapter_lines(text, n))
            self.assertTrue("[NONE-IN-SCOPE]" in body or n == 3, (n, body[:80]))
        self.assertIn("[NONE-DECLARED]", "\n".join(chapter_lines(text, 10)))
        self.assertIn(T.tr("en-US", "skeleton.doc_info.unselected"), text)
        self.assertIn("[N/A]", built("base").text("en-US", "DD"))

    def test_policy_scope_and_level(self):
        b = built("base")
        ds = b.langs["en-US"][0]
        table = [n for n, r in b.ix.node.items() if r["kind"] == "Table"][0]
        module = [n for n, r in b.ix.node.items() if r["kind"] == "Module"][0]
        # PL-D: kind:Table, level DD, data -> chapter 6, DD documents of a selection that holds a table
        self.assertIn("Member rows keep an audit trail.", A.assemble(ds, "DD", [table]))
        self.assertNotIn("Member rows keep an audit trail.", A.assemble(ds, "DD", [module]))
        self.assertNotIn("Member rows keep an audit trail.", A.assemble(ds, "BD", [table]))
        # PL-C: scope all, level BD -> BD and DD, never REQ
        for level, shown in (("REQ", False), ("BD", True), ("DD", True)):
            self.assertEqual("Only members may borrow." in A.assemble(ds, level, []), shown, level)
        # PL-N: nfr, level REQ
        self.assertIn("A borrow answers within two seconds.", A.assemble(ds, "REQ", []))

    def test_data_policy_chapter_defaults_and_odd_chapters(self):
        model = copy.deepcopy(base_model())
        model["policies"].append(am.build_policy("PL-X", "Odd chapter policy.", "RQ-1", kind="constraint", chapter=15))
        model["policies"].append(am.build_policy("PL-Y", "Early chapter policy.", "RQ-1", kind="constraint", chapter=2))
        ds = F.build_docset(model, "en-US")
        self.assertEqual([i for i in ds["issues"] if i["rule"] in INTERNAL], [])
        text = A.assemble(ds, "DD", [])
        self.assertIn("Odd chapter policy.", "\n".join(chapter_lines(text, 10)))
        self.assertIn("Early chapter policy.", "\n".join(chapter_lines(text, 2)))
        bad = copy.deepcopy(base_model())
        bad["policies"][0]["scope"] = "node:ghost"
        ds = F.build_docset(bad, "en-US")
        self.assertNotIn("A borrow answers within two seconds.", A.assemble(ds, "DD", ds["fragments"]["elements"]))
        pid = bad["policies"][0]["id"]
        # a policy no document can reach is reported by id (never a silent drop): unknown node, unknown kind, no valid scope
        for scope in ("node:ghost", "req:ghost", "kind:NoSuchKind", "nonsense"):
            bad["policies"][0]["scope"] = scope
            ds = F.build_docset(bad, "en-US")
            self.assertEqual(ds["coverage"]["unreached"].get("policies"), [pid], scope)
            self.assertNotIn("A borrow answers within two seconds.", A.assemble(ds, "DD", ds["fragments"]["elements"]), scope)
        self.assertTrue([i for i in ds["issues"] if i["rule"] == "DC-D-002"])
        del bad["policies"][0]["id"]
        bad["policies"][0]["scope"] = "node:ghost"
        self.assertEqual(F.build_docset(bad, "en-US")["coverage"]["unreached"].get("policies"), ["policies[0]"])

    def test_structure_only_no_engine_output(self):
        b = built("base")
        text = b.text("en-US", "DD")
        for iv in b.ix.interventions:
            self.assertNotIn(iv["interventionId"], text)
        for word in ("trace", "verdict", "outcomeClass", "DIVERGED", "baseline"):
            self.assertNotIn(word, text, word)
        # authored events and expectations are shown
        case = list(b.ix.case.values())[0]
        for event in case["events"]:
            self.assertIn(f"`{event['from']}`", text)
        self.assertIn("Expected end state", text)

    def test_sections_of_the_dd_chain(self):
        text = built("base").text("en-US", "DD")
        self.assertIn("`tbl.member`", "\n".join(chapter_lines(text, 6)))
        self.assertIn("PK", "\n".join(chapter_lines(text, 6)))
        self.assertIn("personal data", "\n".join(chapter_lines(text, 6)))
        self.assertIn("close()", "\n".join(chapter_lines(text, 4)))
        self.assertIn("E_LATE", "\n".join(chapter_lines(text, 8)))
        self.assertIn("late present", "\n".join(chapter_lines(text, 8)).replace("\\", ""))
        self.assertIn("`st.loan:Open->Due`" if False else "`st.loan", "\n".join(chapter_lines(text, 8)))

    def test_text_hygiene(self):
        model = copy.deepcopy(base_model())
        hostile = "x | y\n`z` [NOT-AUTHORED:forged] [N/A] <b>bold</b> [link](https://e.example) ```fence"
        model["nodes"][0]["name"] = hostile
        model["nodes"][0]["sub"] = hostile
        model["requirements"][0]["title"] = hostile
        model["tbds"][0]["decision"] = hostile
        model["policies"][0]["text"] = hostile
        model["cases"][0]["title"] = hostile
        model["edges"][0]["label"] = hostile
        for lang in LANGS:
            ds = F.build_docset(model, lang)
            units = D.build_units(model, lang)["units"]
            ix = P.Index(model)
            for level in LEVELS:
                text = A.assemble(ds, level, ds["fragments"]["elements"])
                self.assertNotIn("forged", [m for m in T.NOT_AUTHORED_RE.findall(text)])
                self.assertEqual(table_problems(text), [], (lang, level))
                self.assertEqual(L.lint_links(text, units, ix), [])
                self.assertEqual(L.lint_chapters(text, lang, level), [])
                self.assertNotIn("<b>", text)
                self.assertNotIn("```", text.replace("```mermaid", ""))

    def test_multi_language(self):
        titles = set()
        for lang in LANGS:
            text = built("base").text(lang, "BD")
            titles.add(text.split("\n", 1)[0].rsplit(" ", 1)[0])
            self.assertIn(T.tr(lang, "skeleton.label"), text)
        self.assertEqual(len(titles), 3)

    def test_markers_for_missing_facts_are_listed_by_slot(self):
        model = copy.deepcopy(base_model())
        del model["nodes"][0]["sub"]
        del model["requirements"][0]["source"]
        model["tbds"][0]["decision"] = ""
        ds = F.build_docset(model, "en-US")
        slots = set()
        for level in LEVELS:
            slots |= set(T.NOT_AUTHORED_RE.findall(A.assemble(ds, level, ds["fragments"]["elements"])))
        self.assertIn(f"node.sub@{model['nodes'][0]['id']}", slots)
        self.assertIn("requirement.source@RQ-1", slots)
        self.assertIn("tbd.decision@TBD-1", slots)


# --------------------------------------------------------------------------------------------- robustness

class RobustnessTests(unittest.TestCase):
    def check_model(self, model, label, langs=("en-US",)):
        for lang in langs:
            ds = F.build_docset(model, lang)
            self.assertFalse({i["rule"] for i in ds["issues"]} & INTERNAL, (label, lang, [i for i in ds["issues"] if i["rule"] in INTERNAL][:2]))
            for level in LEVELS:
                for ids in (None, []):
                    text = A.assemble(ds, level, ds["fragments"]["elements"] if ids is None else ids)
                    self.assertEqual(T.JUNK_RE.findall(text), [], (label, lang, level))
                    self.assertEqual(L.lint_chapters(text, lang, level), [], (label, lang, level))
                    self.assertEqual(table_problems(text), [], (label, lang, level))
        return ds

    def test_deletion_matrix_rows_never_break_the_document_builder(self):
        base = base_model()
        rows = 0
        for name, spec, _rules, _slot, _extra in ROWS:
            self.check_model(apply_row(base, spec), name, LANGS)
            rows += 1
        self.assertGreaterEqual(rows, 60)

    def test_garbage_models(self):
        for model in ({}, {"nodes": "x"}, {"nodes": [None, 1, "a"], "edges": 3, "cases": [{}], "requirements": [{"nodeIds": 5}]},
                       {"policies": [{"scope": "all", "kind": "nfr", "level": "DD"}], "tbds": [{}]}):
            self.check_model(model, str(model)[:30], LANGS)
        ds = F.build_docset(None, "zh-CN")
        self.assertEqual(L.lint_chapters(A.assemble(ds, "DD", []), "zh-CN", "DD"), [])

    def test_synthetic_edge_cases(self):
        for name in synth.EDGE_CASES:
            model = synth.edge_case(name, 3)
            ds = self.check_model(model, name, ("en-US", "zh-CN") if name != "big400" else ("en-US",))
            ix = P.Index(model)
            units = D.build_units(model, "en-US")["units"]
            if name != "empty-strings":
                text = A.assemble(F.build_docset(model, "en-US", units), "DD", ds["fragments"]["elements"])
                self.assertEqual(L.lint_links(text, units, ix), [], name)

    def test_empty_strings_become_markers_not_blanks(self):
        model = synth.edge_case("empty-strings", 1)
        ds = F.build_docset(model, "en-US")
        text = A.assemble(ds, "DD", ds["fragments"]["elements"])
        self.assertTrue(T.NOT_AUTHORED_RE.search(text))

    def test_two_builds_are_identical(self):
        model = reference_model(7)
        first = json.dumps(F.build_docset(model, "zh-CN"), sort_keys=True, ensure_ascii=False)
        second = json.dumps(F.build_docset(copy.deepcopy(model), "zh-CN"), sort_keys=True, ensure_ascii=False)
        self.assertEqual(first, second)

    def test_hash_seed_independent(self):
        code = (
            "import sys, hashlib, json\n"
            "sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')\n"
            "from atlas import synth\n"
            "from gemarc_mcp.atlas.derive import fragments as F, assemble as A\n"
            "m = synth.generate('small', 5)\n"
            "ds = F.build_docset(m, 'ja-JP')\n"
            "h = hashlib.sha256()\n"
            "for lv in ('REQ', 'BD', 'DD'):\n"
            "    h.update(A.assemble(ds, lv, ds['fragments']['elements']).encode('utf-8'))\n"
            "h.update(json.dumps(ds['closures'], sort_keys=True).encode('utf-8'))\n"
            "print(h.hexdigest())\n"
        )
        root = Path(__file__).resolve().parents[2]
        digests = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(root / "src"))
            out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1, digests)


class ColumnConsistencyTests(unittest.TestCase):
    def test_every_table_row_has_the_header_width_at_reference_scale(self):
        for lang in LANGS:
            b = built("reference")
            for level in LEVELS:
                self.assertEqual(table_problems(b.text(lang, level)), [], (lang, level))

    def test_reference_scale_documents_lint_clean_apart_from_open_markers(self):
        for lang in LANGS:
            b = built("reference")
            for level in LEVELS:
                text = b.text(lang, level)
                issues = L.lint_document(text, lang=lang, level=level, ix=b.ix, units=b.langs[lang][1])
                self.assertEqual({i["rule"] for i in issues} - {"DC-L-004"}, set(), (lang, level, issues[:3]))
                self.assertTrue(all(i["severity"] == "BLOCK" for i in issues))

    def test_every_model_element_appears_in_a_fragment_or_is_reported(self):
        for name in ("fixture", "base", "reference"):
            b = built(name)
            ds = b.langs["en-US"][0]
            deep = A.assemble(ds, "DD", ds["fragments"]["elements"])
            tokens = code_tokens(deep)
            unreached = ds["coverage"]["unreached"]
            for category, table in (("nodes", b.ix.node), ("edges", b.ix.edge), ("cases", b.ix.case), ("requirements", b.ix.requirement),
                                    ("machines", b.ix.machine), ("tbds", b.ix.tbd), ("policies", b.ix.policy)):
                for key in table:
                    if category == "policies":
                        continue
                    self.assertTrue(key in tokens or key in unreached.get(category, []), (name, category, key))
            self.assertEqual(unreached.get("nodes"), None)
            self.assertEqual(unreached.get("edges"), None)
            self.assertEqual(unreached.get("cases"), None)
            self.assertEqual(unreached.get("requirements"), None)
            self.assertEqual(unreached.get("policies"), None)
            cited = {e for table in (b.ix.nodes, b.ix.edges, b.ix.cases, b.ix.requirements, b.ix.machines) for rec in table for e in P.str_list(rec.get("evidenceIds"))}
            self.assertEqual(sorted(unreached.get("evidences", [])), sorted(set(b.ix.evidence) - cited))


if __name__ == "__main__":
    unittest.main()
