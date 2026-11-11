"""Page rendering: the frame, the sentinel, folding of long lines (exactly invertible), legends and the ruler, inbound tables, languages
(design A3 2.4 rule 6, 2.5, AM-02, AM-06, AM-14).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_render
"""
from __future__ import annotations

import copy
import random
import re
import unittest

from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.reading import blocks as B
from gemarc_mcp.atlas.reading import cards
from gemarc_mcp.atlas.reading import envelope as E
from gemarc_mcp.atlas.reading import parse as P

from . import read_support as RS

LANG = "zh-CN"


def pages_of(b: RS.Built, lang: str = LANG) -> dict:
    return {p: P.parse_page(lang, p, d.decode("utf-8")) for p, d in b.result.files.items() if p.endswith(".md") and (p.startswith("read/") or p == "START.md")}


class FrameTests(unittest.TestCase):
    def test_every_page_has_the_frame_and_a_sentinel_that_verifies(self):
        for preset in ("tiny", "small"):
            b = RS.built(preset)
            for path, page in pages_of(b).items():
                with self.subTest(preset=preset, path=path):
                    self.assertIsNotNone(page, "frame cannot be read back")
                    self.assertEqual(E.verify_sentinel(b.text(path)), [])
                    self.assertEqual(page.stamps.get("m"), b.src.stamp)
                    self.assertEqual(page.total_lines, len(page.lines))
                    self.assertTrue(page.covers and page.excludes and page.howto)

    def test_no_line_is_longer_than_500_characters(self):
        for preset in ("small", "reference"):
            b = RS.built(preset)
            for path, data in b.result.files.items():
                if path.endswith((".md", ".tsv")):
                    limit = 400 if path.endswith(".tsv") else 500
                    self.assertLessEqual(max(len(x) for x in data.decode("utf-8").split("\n")), limit, path)

    def test_page_ids_are_written_with_the_section_sign_everywhere_in_text(self):
        b = RS.built("small")
        for path, page in pages_of(b).items():
            text = "\n".join(page.body)
            self.assertNotRegex(re.sub(r"`[^`]*`", "", text), r"(?<!§)\bS\d{2}\b(?!\.md)", path)

    def test_english_and_japanese_use_the_english_frame(self):
        en = RS.make(RS.model_of("small", 1), "en-US")
        self.assertEqual(RS.rules_of(en.result.findings), set())
        self.assertTrue(en.text("read/2D/S01.md").startswith("Page §S01 · "))
        ja = RS.make(RS.model_of("small", 1), "ja-JP")
        self.assertEqual(RS.rules_of(ja.result.findings), set())
        self.assertTrue(ja.text("read/2D/S01.md").startswith("Page §S01 · "))
        self.assertIn("ja-JP", ja.text("read/2D/S01.md").split("\n")[1])
        for b in (en, ja):
            for path, page in pages_of(b, b.lang).items():
                self.assertLessEqual(page.envelope_bytes, 1430, path)


class FoldingTests(unittest.TestCase):
    def test_fold_text_is_exactly_invertible(self):
        rng = random.Random(7)
        alphabet = ["a", "b", "x", " ", " ", "中", "文", "é", "`", "|", "-"]
        mark = "（续）"
        for _ in range(300):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 2500))).strip() or "z"
            lines = B.fold_text(text, mark)
            self.assertTrue(all(len(x) <= 480 for x in lines))
            self.assertEqual(B.unfold_text(lines, mark), [text])

    def test_a_long_table_cell_goes_to_a_note_and_comes_back_unchanged(self):
        model = copy.deepcopy(RS.model_of("small", 1))
        model["requirements"][0]["title"] = "word " * 140          # 700 characters in one cell
        model["nodes"][2]["sub"] = "long description " * 60        # a bullet line above the wrap limit
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set())
        self.assertIn("RS-132", b.result.degraded)
        path = next(p for p in sorted(b.result.files) if p.startswith("read/1D/R") and "`" + model["requirements"][0]["id"] + "`" in b.text(p))
        page = P.parse_page(LANG, path, b.text(path))
        self.assertTrue(any("见注 [1]" in line for line in page.body))
        self.assertTrue(any(line.startswith("[1] ") for line in page.body))
        unfolded = P.unfold_lines(LANG, page.body)
        row = next(h.md for h in b.src.by_group("req") if h.key == model["requirements"][0]["id"])
        self.assertIn(row, unfolded)
        spath = next(p for p in sorted(b.result.files) if p.startswith("read/2D/S") and ("\n`" + model["nodes"][2]["id"] + "` · ") in b.text(p))
        sbody = P.unfold_lines(LANG, P.parse_page(LANG, spath, b.text(spath)).body)
        bullet = next(line for line in b.src.by_group("n.DD")[2].lines if line.startswith("- ") and len(line) > 480)
        self.assertIn(bullet, sbody)
        self.assertGreater(next(r["fit"] for r in b.result.records if r["p"] == spath), 0)
        for data in b.result.files.values():
            self.assertLessEqual(max(len(x) for x in data.decode("utf-8").split("\n")), 500)

    def test_a_folded_page_keeps_every_other_check_green(self):
        model = copy.deepcopy(RS.model_of("small", 3))
        for tbd in model["tbds"][:2]:
            tbd["decision"] = "decision text " * 70
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set())


class LegendTests(unittest.TestCase):
    def test_a_page_with_na_or_grades_explains_them_in_its_reading_line(self):
        b = RS.built("small")
        ruler = b.src.grade_ruler()
        seen = {"na": 0, "grade": 0, "plain": 0}
        for path, page in pages_of(b).items():
            if not path.startswith("read/") or "INDEX" in path or "G01" in path:
                continue
            body = "\n".join(page.body)
            if T.NA in body:
                seen["na"] += 1
                self.assertIn("[N/A]", page.howto, path)
            if any(w in body for w in ruler):
                seen["grade"] += 1
                positions = [page.howto.find(w) for w in ruler]
                self.assertTrue(all(p >= 0 for p in positions) and positions == sorted(positions), path)
            if T.NA not in body and not any(w in body for w in ruler):
                seen["plain"] += 1
        self.assertGreater(seen["na"], 0)
        self.assertGreater(seen["grade"], 0)

    def test_the_ruler_has_the_ranks_of_the_profile_in_descending_order(self):
        b = RS.built("small")
        self.assertEqual(b.src.grade_ruler(), ["Implemented", "Observed", "Designed", "Inferred", "Hypothetical", "Unknown"])
        en = RS.make(RS.model_of("small", 1), "en-US")
        self.assertEqual(en.src.grade_ruler(), ["Implemented", "Observed", "Designed", "Inferred", "Hypothetical", "Unknown"])

    def test_the_markers_are_detected_without_matching_the_legend_text(self):
        b = RS.built("small")
        self.assertEqual(cards.page_markers(b.src, ["| `a` | [N/A] |"]), {"na": True, "auth": False, "grade": False, "verdict": False})
        self.assertTrue(cards.page_markers(b.src, ["x [NOT-AUTHORED:node.name:a] y"])["auth"])
        self.assertTrue(cards.page_markers(b.src, ["`n` · 模块 · Designed"])["grade"])
        self.assertFalse(cards.page_markers(b.src, ["`n` · 模块 · Designedness"])["grade"])


class InboundTests(unittest.TestCase):
    def test_every_edge_is_listed_inbound_on_the_page_of_its_target_with_the_page_of_its_source(self):
        b = RS.built("small")
        ix = b.src.ix
        node_page = {}
        for path in sorted(b.result.files):
            if path.startswith("read/2D/S"):
                pid = path.rsplit("/", 1)[1][:-3]
                for line in b.text(path).split("\n"):
                    m = re.match(r"^`([^`]+)` · ", line)
                    if m and m.group(1) in ix.node:
                        node_page.setdefault(m.group(1), pid)
        seen = {}
        for path in sorted(b.result.files):
            if path.startswith("read/2D/S"):
                pid = path.rsplit("/", 1)[1][:-3]
                for line in b.text(path).split("\n"):
                    cells = B.split_cells(line) if line.startswith("| `") else []
                    if len(cells) == 5 and cells[3] != "" and cells[4].startswith("§"):
                        seen[cells[1].strip("`")] = (pid, cells[0].strip("`"), cells[2].strip("`"), cells[4])
        wanted = {e["id"]: e for e in ix.edges if e["targetId"] in ix.node}
        self.assertEqual(set(seen), set(wanted))
        for eid, (pid, target, source, where) in seen.items():
            self.assertEqual(target, wanted[eid]["targetId"])
            self.assertEqual(source, wanted[eid]["sourceId"])
            self.assertEqual(pid, node_page[target])
            self.assertEqual(where, "§" + node_page[source])

    def test_the_count_line_of_a_node_block_equals_its_inbound_edges(self):
        b = RS.built("small")
        ix = b.src.ix
        counts = {}
        for e in ix.edges:
            counts[e["targetId"]] = counts.get(e["targetId"], 0) + 1
        for path in sorted(b.result.files):
            if not path.startswith("read/2D/S"):
                continue
            lines = b.text(path).split("\n")
            for n, line in enumerate(lines):
                m = re.match(r"^- 入向连接: (\d+)（", line)
                if m:
                    k = n
                    while not lines[k - 1].startswith("### "):
                        k -= 1
                    nid = re.match(r"^`([^`]+)`", lines[k]).group(1)
                    self.assertEqual(int(m.group(1)), counts.get(nid, 0), nid)


if __name__ == "__main__":
    unittest.main()
