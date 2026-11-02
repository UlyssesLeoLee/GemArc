"""The planner: modes (one page, folded dimensions, split), the real envelope reservation, continuation parts, the measured bands (design A3 2.3, 2.4, T-R1, T-R2).

The bands below are what ``reading.build`` measured on the synthetic presets in zh-CN (seeds 1-4 and 6-8; seed 5 cannot be put through the
generator) when R1 was written; they are pinned at about 15 percent around the measurement and may only change with a table that the maintainer
script ``tools/pilot/read_matrix.py`` prints (design 2.3: never to make a threshold pass).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_plan
"""
from __future__ import annotations

import statistics
import unittest

from gemarc_mcp import atlas  # noqa: F401  (package import for the relative paths below)
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.reading import api
from gemarc_mcp.atlas.reading import names as N
from gemarc_mcp.atlas.reading import parse as P

from . import read_support as RS

LANG = "zh-CN"
CAP = {"card": 6000, "page": 9000}
#: (low, high) bounds measured at R1 +-15 percent: S pages, all Markdown pages under read/, per preset (median of seven seeds in the comment)
BANDS = {
    "small": {"S": (6, 9), "md": (17, 24)},            # S 7-8, md 20-21
    "reference": {"S": (29, 39), "md": (54, 72)},      # S 33-35 (median 34), md 61-64 (median 63)
    "refcjk": {"S": (40, 54), "md": (74, 100)},        # S 44-47 (median 47), md 84-89 (median 87)
}


def read_pages(b: RS.Built) -> dict:
    return {p: P.parse_page(LANG, p, d.decode("utf-8")) for p, d in b.result.files.items() if p.startswith("read/") and p.endswith(".md")}


class ModeTests(unittest.TestCase):
    def test_a_tiny_project_is_one_page_and_the_glossary(self):
        b = RS.built("tiny")
        self.assertEqual(b.result.plan["mode"], "single")
        self.assertEqual(sorted(p for p in b.result.files if p.startswith("read/")), ["read/A01.md", "read/G01.md"])
        self.assertLessEqual(len(b.result.files["read/A01.md"]), CAP["page"])
        self.assertNotIn("INDEX", " ".join(b.result.files))
        start = b.text("START.md")
        self.assertIn("(read/A01.md)", start)

    def test_a_middle_sized_project_folds_dimensions_into_few_pages(self):
        from . import synth
        import dataclasses

        base = synth.PRESETS["small"]
        scale = dataclasses.replace(base, nodes=10, edges=22, evidences=12, cases=1, events=15, interventions=2, requirements=2, tbds=2, tests=2, policies=2,
                                    rooms=4, floors=2, buildings=2)
        b = RS.make(synth.generate(scale, 1))
        self.assertEqual(b.result.plan["mode"], "folded")
        self.assertTrue(b.result.plan["folded"])
        for pid in b.result.plan["folded"]:
            self.assertIn(pid, ("P1D", "P2D", "P3Q"))
            self.assertTrue(N.is_page_id(pid))
        self.assertEqual(RS.rules_of(b.result.findings), set())
        self.assertLessEqual(len([p for p in b.result.files if p.startswith("read/") and "G01" not in p and "INDEX" not in p]), 6)

    def test_a_small_project_has_one_page_family_per_kind_and_no_wide_page(self):
        b = RS.built("small")
        self.assertEqual(b.result.plan["mode"], "split")
        for path, page in read_pages(b).items():
            self.assertLessEqual(len(b.result.files[path]), CAP[page.cls], path)
        self.assertEqual(set(b.result.plan["pages"]), {"S", "D", "M", "W", "R", "T", "E", "C", "V", "Q"})

    def test_the_hash_of_html_does_not_change_a_markdown_page(self):
        b = RS.built("small")
        again = R.build(b.bundle, RS.meta_for(b.bundle), R.ReadConfig(html=True), RS.UI)
        self.assertEqual({p: d for p, d in again.files.items() if not p.startswith("site/")}, dict(b.result.files))
        twins = {p for p in again.files if p.startswith("site/")}
        self.assertTrue(twins)
        self.assertEqual(twins, {N.twin_path(p) for p in again.files if p.endswith(".md") and N.twin_path(p)})
        self.assertEqual(again.status, "ok")

    def test_the_build_is_a_pure_function_of_the_bundle(self):
        import copy

        b = RS.built("small")
        before = copy.deepcopy(b.bundle)
        R.build(b.bundle, RS.meta_for(b.bundle), R.ReadConfig(), RS.UI)
        self.assertEqual(b.bundle, before)
        self.assertEqual({p: d for p, d in R.build(b.bundle, RS.meta_for(b.bundle), R.ReadConfig(), RS.UI).files.items()}, dict(b.result.files))


class EnvelopeTests(unittest.TestCase):
    def test_the_envelope_is_reserved_by_the_real_renderer_so_no_page_exceeds_its_class(self):
        for preset in ("small", "reference"):
            b = RS.built(preset)
            for path, page in read_pages(b).items():
                with self.subTest(path=path):
                    info = N.parse_page_id(page.pid)
                    cont = page.continuation is not None
                    fc = f"{info.family}:{page.cls}" if info.family in N.FAMILIES else None
                    if fc and info.family in N.FAMILIES:
                        try:
                            reserved = api.envelope_size(fc, LANG, cont)
                        except ValueError:
                            continue
                        self.assertLessEqual(page.envelope_bytes, reserved)
                        self.assertLessEqual(len(b.result.files[path]), CAP[page.cls])

    def test_the_envelope_share_is_far_below_the_limit_of_the_design(self):
        b = RS.built("reference")
        env = sum(p.envelope_bytes for path, p in read_pages(b).items() if "INDEX" not in path and "G01" not in path)
        total = sum(len(b.result.files[path]) for path in read_pages(b) if "INDEX" not in path and "G01" not in path)
        self.assertLess(env / total, 0.45)
        self.assertLess(env / total, 0.15)       # measured 0.105 at R1


class ContinuationTests(unittest.TestCase):
    def test_a_hub_node_and_a_long_case_continue_on_pages_of_their_own(self):
        b = RS.make(RS.hub_model(160, 400))
        pages = read_pages(b)
        parts = {path: p.continuation for path, p in pages.items() if p.continuation}
        units = {c["unit"] for c in parts.values()}
        self.assertEqual(len(units), 2)
        for unit in units:
            numbers = sorted(c["part"] for c in parts.values() if c["unit"] == unit)
            self.assertEqual(numbers, list(range(1, max(numbers) + 1)))
        for path in parts:
            self.assertLessEqual(len(b.result.files[path]), CAP["page"])
            self.assertLessEqual(pages[path].envelope_bytes, api.envelope_size("S:page" if "/S" in path else "C:page", LANG, True))
        self.assertEqual(RS.rules_of(b.result.findings), set())

    def test_a_unit_of_more_than_three_pages_is_an_info_finding_not_a_rule(self):
        b = RS.make(RS.hub_model(160, 400))
        notes = [f for f in b.result.findings if f.rule == "INFO-SPLIT"]
        self.assertEqual({(f.klass, f.severity) for f in notes}, {("INFO", "INFO")})
        self.assertEqual(len(notes), 1)                                     # the long case has five parts, the hub three
        self.assertIn("LZ3-LONG-1", notes[0].message)
        self.assertEqual(b.result.status, "ok")

    def test_the_rows_of_a_split_table_are_all_there_in_order(self):
        b = RS.make(RS.hub_model(160, 0))
        hub = b.model["nodes"][0]["id"]
        rows = []
        for path in sorted(b.result.files):
            if path.startswith("read/2D/S"):
                rows += [line for line in b.text(path).split("\n") if line.startswith("| `" + hub + "` | `")]
        wanted = sorted(e["id"] for e in b.model["edges"] if e["targetId"] == hub)
        self.assertEqual(sorted(line.split("`")[3] for line in rows), wanted)

    def test_the_identity_lines_and_the_headers_are_repeated(self):
        b = RS.make(RS.hub_model(160, 0))
        later = [p for p, page in read_pages(b).items() if page.continuation and page.continuation["part"] > 1]
        self.assertTrue(later)
        for path in later:
            body = P.parse_page(LANG, path, b.text(path)).body
            self.assertTrue(body[0].startswith("### "))
            self.assertIn("| 终点 | 边 | 起点 | 起点名 | 起点所在页 |", body)


class BandTests(unittest.TestCase):
    def test_page_counts_stay_inside_the_measured_bands(self):
        for preset, bands in BANDS.items():
            for seed in (1, 4, 8):
                b = RS.built(preset, LANG, seed)
                md = [p for p in b.result.files if p.startswith("read/") and p.endswith(".md")]
                with self.subTest(preset=preset, seed=seed):
                    low, high = bands["S"]
                    self.assertTrue(low <= b.result.plan["pages"]["S"] <= high, b.result.plan["pages"]["S"])
                    low, high = bands["md"]
                    self.assertTrue(low <= len(md) <= high, len(md))

    def test_t_r2_distribution_at_reference_scale(self):
        for preset in ("reference", "refcjk"):
            b = RS.built(preset)
            rows = [r for r in b.result.records if r["k"] == "page"]
            sizes = [r["b"] for r in rows]
            with self.subTest(preset=preset):
                self.assertTrue(all(r["b"] <= CAP[r["cls"]] for r in rows))
                self.assertGreaterEqual(statistics.median(r["b"] for r in rows if r["cls"] == "page"), 5000)
                self.assertLessEqual(len([s for s in sizes if s < 1500]) / len(sizes), 0.10)
                ai = [r for r in b.result.records if r.get("ai") and r["cls"] in ("card", "page", "svg")]
                self.assertLessEqual(len(ai), 130)
                if preset == "reference":
                    self.assertLessEqual(sum(r["b"] for r in ai), 650_000)
                for row in b.result.records:
                    self.assertLessEqual(len(R.manifest.dumps(row)), 500)

    def test_the_planner_is_deterministic(self):
        a = RS.make(RS.model_of("small", 2))
        c = RS.make(RS.model_of("small", 2))
        self.assertEqual(dict(a.result.files), dict(c.result.files))
        self.assertEqual(a.result.records, c.result.records)

    def test_t_r1_no_integrity_error_on_any_preset_and_seed(self):
        for preset in RS.PRESETS:
            for seed in RS.SEEDS:
                b = RS.built(preset, LANG, seed)
                with self.subTest(preset=preset, seed=seed):
                    self.assertEqual(b.result.status, "ok")
                    self.assertEqual(RS.rules_of(b.result.findings), set(), [f.message for f in b.result.errors][:2])


if __name__ == "__main__":
    unittest.main()
