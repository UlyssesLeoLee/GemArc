"""The core of ``gemarc_atlas_doc`` (outline plus lint, chapter pages) and the deterministic document build.

Acceptance (design M3 (f)): the default reply is at most 1 KiB of compact JSON on five sample units in every language and at
every level; a chapter page is at most 20 KiB; nothing raises. ``build_docs`` writes ``docs/<lang>/*.md``, ``diagrams/*.mmd``
and ``prompts/*.md`` and two builds are byte-identical.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_doc_outline*.py"
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.atlas.derive import assemble as A
from gemarc_mcp.atlas.derive import docbuild as B
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import lint as L

from .test_derive_docs import base_model, built, fixture_model, table_problems

LEVELS = T.LEVELS
REPLY_LIMIT = 1024
PAGE_LIMIT = 20 * 1024


def size(reply) -> int:
    return len(B._compact(reply).encode("utf-8"))


def sample_units(name: str) -> list:
    """Five units: a node, an edge, a case, a requirement, and the whole model (plus a diagram unit as a bonus)."""
    b = built(name)
    nodes, edges, cases, reqs = list(b.ix.node), list(b.ix.edge), list(b.ix.case), list(b.ix.requirement)
    return [nodes[len(nodes) // 2], edges[len(edges) // 2], cases[0], reqs[0], None]


class OutlineReplyTests(unittest.TestCase):
    def test_default_reply_is_at_most_one_kib_on_five_sample_units(self):
        worst = 0
        for name in ("fixture", "base", "reference"):
            model = built(name).model
            for lang in T.LANGS:
                for unit in sample_units(name):
                    for level in LEVELS:
                        reply = B.doc_outline(model, unit, level, lang)
                        with self.subTest(model=name, lang=lang, unit=str(unit)[:30], level=level):
                            self.assertTrue(reply["ok"], reply)
                            self.assertLessEqual(size(reply), REPLY_LIMIT)
                            self.assertEqual(reply["total"], 12)
                            self.assertEqual(len(reply["ch"]), 12)
                            self.assertEqual(reply["filled"], sum(1 for x in reply["ch"] if x != "0"))
                            self.assertEqual(reply["junk"], 0)
                            self.assertEqual(reply["links"], 0)
                            self.assertEqual(reply["lang"], lang)
                            self.assertRegex(reply["no"], r"^(RD|BD|DD)-[0-9a-f]{8}$")
                            worst = max(worst, size(reply))
        self.assertLessEqual(worst, REPLY_LIMIT)
        print(f"\nlargest outline reply: {worst} bytes", file=__import__("sys").stderr)

    def test_the_reply_is_json_and_deterministic(self):
        model = built("reference").model
        one = B.doc_outline_json(model, None, "DD", "zh-CN")
        self.assertEqual(one, B.doc_outline_json(copy.deepcopy(model), None, "DD", "zh-CN"))
        self.assertEqual(json.loads(one)["unit"], "all")

    def test_counts_markers_unresolved_links_and_chapters(self):
        fixture = B.doc_outline(fixture_model(), None, "DD", "en-US")
        self.assertEqual((fixture["markers"], fixture["blocks"]), (1, 1))            # chapter 9 is not authored
        self.assertEqual(fixture["ch"][8], "0")                                        # and counts as not filled
        self.assertEqual(fixture["filled"], 12 - fixture["ch"].count("0"))
        full = B.doc_outline(base_model(), None, "DD", "en-US")
        self.assertEqual(full["markers"], 0)
        self.assertEqual(full["ch"][8], "1")
        model = copy.deepcopy(base_model())
        model["requirements"][0]["nodeIds"].append(model["nodes"][0]["id"].split(".")[0] + ".ghost")
        dangling = B.doc_outline(model, None, "DD", "en-US")
        self.assertGreaterEqual(dangling["links"], 1)
        marked = copy.deepcopy(base_model())
        del marked["nodes"][0]["sub"]
        self.assertEqual(B.doc_outline(marked, None, "DD", "en-US")["ch"][3], "2")    # content that still carries a marker

    def test_units_resolve_to_elements_diagram_units_and_lists(self):
        b = built("base")
        for unit in ("svc.loan", "CS-BORROW-OK", "RQ-1", "svc.loan:calls:svc.stock", "all", None, "component-zone.lib", ["svc.loan", "CS-LATE"]):
            self.assertTrue(B.doc_outline(b.model, unit, "BD", "en-US")["ok"], unit)
        one = B.doc_outline(b.model, "svc.loan", "DD", "en-US")
        whole = B.doc_outline(b.model, None, "DD", "en-US")
        self.assertLess(one["bytes"], whole["bytes"])
        self.assertNotEqual(one["no"], whole["no"])

    def test_errors_are_diagnostics_not_exceptions(self):
        model = built("base").model
        cases = [
            (B.doc_outline(model, "no-such-id", "BD"), "GEMARC_ATLAS_DOC_001"),
            (B.doc_outline(model, 5, "BD"), "GEMARC_ATLAS_DOC_001"),
            (B.doc_outline(model, [], "BD"), "GEMARC_ATLAS_DOC_001"),
            (B.doc_outline(model, None, "XX"), "GEMARC_ATLAS_DOC_002"),
            (B.doc_outline(model, None, None), "GEMARC_ATLAS_DOC_002"),
            (B.doc_outline(model, None, "BD", "en-US", 13), "GEMARC_ATLAS_DOC_003"),
            (B.doc_outline(model, None, "BD", "en-US", "4"), "GEMARC_ATLAS_DOC_003"),
            (B.doc_outline(model, None, "BD", "en-US", True), "GEMARC_ATLAS_DOC_003"),
            (B.doc_outline(model, None, "BD", "en-US", 4, 0), "GEMARC_ATLAS_DOC_003"),
            (B.doc_outline(model, None, "BD", "en-US", 4, 99), "GEMARC_ATLAS_DOC_003"),
            (B.doc_outline(model, None, "BD", "en-US", 4, "1"), "GEMARC_ATLAS_DOC_003"),
        ]
        for reply, code in cases:
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["code"], code)
            self.assertLessEqual(size(reply), REPLY_LIMIT)
        for garbage in (None, {}, [], "x", {"nodes": 5}, {"cases": [None]}):
            for level in LEVELS:
                reply = B.doc_outline(garbage, None, level, "ja-JP")
                self.assertIn("ok", reply)
                self.assertNotEqual(reply.get("code"), "GEMARC_ATLAS_DOC_099", garbage)

    def test_an_unknown_language_falls_back_to_the_primary_language(self):
        self.assertEqual(B.doc_outline(built("base").model, None, "BD", "xx-XX")["lang"], T.PRIMARY_LANG)


class ChapterPageTests(unittest.TestCase):
    def test_a_chapter_page_is_at_most_20_kib_and_pages_add_up(self):
        model = built("reference").model
        for lang in ("zh-CN", "en-US"):
            first = B.doc_outline(model, None, "DD", lang, 4, 1)
            self.assertTrue(first["ok"])
            self.assertGreater(first["pages"], 3)
            text = A.assemble(B.get_bundle(model, lang).docset, "DD", B.get_bundle(model, lang).docset["fragments"]["elements"])
            title, chapter = B.chapter_text(text, 4)
            self.assertEqual(first["title"], title)
            joined = []
            for page in range(1, first["pages"] + 1):
                reply = B.doc_outline(model, None, "DD", lang, 4, page)
                self.assertTrue(reply["ok"])
                self.assertEqual((reply["chapter"], reply["page"], reply["pages"]), (4, page, first["pages"]))
                self.assertLessEqual(len(reply["text"].encode("utf-8")), PAGE_LIMIT)
                joined.append(reply["text"])
            self.assertEqual(B.doc_outline(model, None, "DD", lang, 4, first["pages"] + 1)["code"], "GEMARC_ATLAS_DOC_003")
            # a table that continues repeats its two header lines: drop them where the previous page ended inside a table
            rebuilt = []
            for n, page in enumerate(joined):
                lines = page.rstrip("\n").split("\n")
                if n > 0 and rebuilt[-1].startswith("|") and lines[0].startswith("|") and len(lines) > 1 and set(lines[1]) <= set("|-:"):
                    lines = lines[2:]
                rebuilt.extend(lines)
            self.assertEqual(rebuilt, chapter.rstrip("\n").split("\n"))

    def test_small_chapters_are_one_page_and_show_the_heading(self):
        reply = B.doc_outline(built("base").model, None, "BD", "en-US", 12, 1)
        self.assertEqual(reply["pages"], 1)
        self.assertTrue(reply["text"].startswith("## 12. "))
        self.assertIn("Review and sign-off", reply["text"])

    def test_every_chapter_of_every_level_pages_within_the_limit(self):
        model = built("reference").model
        for level in LEVELS:
            for n in range(1, 13):
                reply = B.doc_outline(model, None, level, "ja-JP", n, 1)
                self.assertTrue(reply["ok"], (level, n))
                self.assertLessEqual(len(reply["text"].encode("utf-8")), PAGE_LIMIT)

    def test_paginate_clips_a_single_oversized_line_and_repeats_table_headers(self):
        head = ["| a | b |", "|---|---|"]
        rows = [f"| {i} | {'x' * 200} |" for i in range(400)]
        pages = B.paginate("\n".join(head + rows) + "\n", 4096)
        self.assertGreater(len(pages), 5)
        for page in pages:
            self.assertLessEqual(len(page.encode("utf-8")), 4096)
            self.assertEqual(page.split("\n")[:2], head)
        self.assertEqual(sum(p.count("| x") + p.count("xxxx |") for p in pages) >= 400, True)
        giant = B.paginate("a" * 50000 + "\nnext\n", 4096)
        self.assertLessEqual(max(len(p.encode("utf-8")) for p in giant), 4096)
        self.assertEqual([p.strip() for p in B.paginate("", 100)], [""])

    def test_chapter_states(self):
        states = B.chapter_states(A.assemble(built("fixture").langs["en-US"][0], "DD", []))
        self.assertEqual(len(states), 12)
        self.assertEqual(states[0], "1")
        self.assertEqual(states[8], "0")   # only the not-authored marker
        self.assertEqual(states[11], "1")


class BuildDocsTests(unittest.TestCase):
    def build(self, model, langs, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        report = B.build_docs(model, tmp.name, langs, **kwargs)
        return Path(tmp.name), report

    def digest(self, root: Path) -> dict:
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}

    def test_writes_documents_diagrams_and_prompts(self):
        root, report = self.build(built("base").model, list(T.LANGS))
        for lang in T.LANGS:
            for level in LEVELS:
                path = root / "docs" / lang / f"{level}.md"
                self.assertTrue(path.is_file(), path)
                data = path.read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn(b"\r", data)
                self.assertTrue(data.endswith(b"\n"))
                self.assertEqual(L.lint_chapters(data.decode("utf-8"), lang, level), [])
                self.assertEqual(table_problems(data.decode("utf-8")), [])
        diagrams = sorted(p.name for p in (root / "diagrams").glob("*.mmd"))
        self.assertIn("er-all.mmd", diagrams)
        self.assertTrue(any(n.startswith("component-") for n in diagrams))
        prompts = sorted((root / "prompts").glob("PR-*.md"))
        self.assertEqual(len(prompts), 3)
        self.assertEqual(sorted(f["path"] for f in report["files"]), sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()))
        self.assertEqual(report["primary"], "zh-CN")

    def test_two_builds_are_byte_identical(self):
        model = built("reference").model
        first, _ = self.build(model, ["zh-CN", "en-US"])
        second, _ = self.build(copy.deepcopy(model), ["zh-CN", "en-US"])
        a, b = self.digest(first), self.digest(second)
        self.assertEqual(a, b)
        self.assertGreater(len(a), 20)

    def test_report_has_no_timestamp_and_is_deterministic(self):
        model = built("base").model
        _, one = self.build(model, ["en-US"])
        _, two = self.build(model, ["en-US"])
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))
        text = json.dumps(one)
        for needle in ("T00:", "2026", "tmp", "Temp"):
            self.assertNotIn(needle, text)
        doc = one["langs"]["en-US"]["docs"]["DD"]
        self.assertEqual(doc["lint"]["junk"], 0)
        self.assertEqual(doc["lint"]["chapters"], {"filled": 12, "total": 12})

    def test_default_languages_follow_the_model_and_per_requirement_is_optional(self):
        model = built("fixture").model
        self.assertEqual(B.default_langs(model), ["en-US"])
        self.assertEqual(B.default_langs({}), ["zh-CN"])
        self.assertEqual(B.default_langs({"meta": {"languages": ["xx", "ja-JP", "ja-JP"]}}), ["ja-JP"])
        root, _ = self.build(model, None)
        self.assertTrue((root / "docs" / "en-US" / "DD.md").is_file())
        self.assertFalse((root / "docs" / "en-US" / "by-requirement").exists())
        root, _ = self.build(model, ["en-US"], per_requirement=True)
        self.assertTrue((root / "docs" / "en-US" / "by-requirement" / "RQ-1-DD.md").is_file())

    def test_requirement_ids_that_are_not_file_names_are_sanitised(self):
        model = copy.deepcopy(base_model())
        model["requirements"][0]["id"] = "RQ:1"
        model["cases"][0]["requirementIds"] = ["RQ:1"]
        model["cases"][1]["requirementIds"] = ["RQ:1"]
        model["policies"] = []
        model["tbds"] = []
        root, _ = self.build(model, ["en-US"], per_requirement=True)
        names = sorted(p.name for p in (root / "docs" / "en-US" / "by-requirement").glob("*.md"))
        self.assertEqual(names, ["RQ_1-BD.md", "RQ_1-DD.md", "RQ_1-REQ.md"])


if __name__ == "__main__":
    unittest.main()
