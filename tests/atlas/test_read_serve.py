"""``reading.serve``: the reader of the written pages for the doc and index tools (design A3 5.2, 5.3, 2.9, D-8, D-33).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_serve
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.atlas.reading import serve

from . import read_support as RS


class ServeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = RS.built("small")
        cls.dist = RS.TempDist(cls.b)
        cls.dist.__enter__()
        cls.stamp = cls.b.src.stamp

    @classmethod
    def tearDownClass(cls):
        cls.dist.__exit__(None, None, None)

    def test_a_page_is_served_with_its_stamp_neighbours_and_text(self):
        reply = serve.load_page(self.dist.tmp, "§S02", self.stamp)
        self.assertEqual(reply["status"], "ok")
        self.assertEqual((reply["id"], reply["family"], reply["dim"], reply["cls"], reply["path"]), ("§S02", "S", "2D", "page", "read/2D/S02.md"))
        self.assertEqual(reply["stamp"], {"built": self.stamp, "current": self.stamp, "stale": False})
        self.assertEqual((reply["prev"], reply["next"]), ("§S01", "§S03"))
        self.assertEqual(reply["text"], self.b.text("read/2D/S02.md"))
        self.assertEqual(reply["bytes"], len(self.b.result.files["read/2D/S02.md"]))
        self.assertLessEqual(reply["bytes"], 9000)
        self.assertEqual(serve.load_page(self.dist.tmp, "S02", self.stamp)["id"], "§S02")

    def test_a_stale_page_gives_no_text(self):
        reply = serve.load_page(self.dist.tmp, "§S02", "00000000")
        self.assertEqual((reply["status"], reply["code"]), ("invalid", serve.E_STALE))
        self.assertNotIn("text", reply)
        self.assertEqual(reply["stamp"], {"built": self.stamp, "current": "00000000", "stale": True})

    def test_a_build_that_does_not_exist_and_a_page_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(serve.load_page(Path(empty), "§S01", self.stamp)["code"], serve.E_NO_BUILD)
        reply = serve.load_page(self.dist.tmp, "§S77", self.stamp)
        self.assertEqual(reply["code"], serve.E_NO_PAGE)
        self.assertEqual(len(reply["closest"]), 3)
        self.assertTrue(all(c.startswith("§") for c in reply["closest"]))
        self.assertEqual(serve.load_page(self.dist.tmp, "tq1.something", self.stamp)["code"], serve.E_NO_PAGE)

    def test_result_pages_are_files_and_never_served(self):
        reply = serve.load_page(self.dist.tmp, "§C01", self.stamp)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", serve.E_RESULT))
        self.assertEqual(reply["stamp"]["built"], self.stamp)
        self.assertEqual(reply["path"], "read/4D/C01.md")
        self.assertNotIn("text", reply)

    def test_a_folded_page_that_holds_the_cases_is_a_file_too(self):
        from . import synth
        import dataclasses

        scale = dataclasses.replace(synth.PRESETS["small"], nodes=6, edges=12, evidences=8, cases=1, events=10, interventions=1, requirements=1, tbds=1, tests=1,
                                    policies=1, rooms=2, floors=2, buildings=2)
        b = RS.make(synth.generate(scale, 1))
        self.assertIn("P3Q", b.result.plan["folded"])
        with RS.TempDist(b) as d:
            for pid in ("§P3Q",):
                reply = serve.load_page(d.tmp, pid, b.src.stamp)
                self.assertEqual((reply["status"], reply["code"]), ("invalid", serve.E_RESULT))
            self.assertEqual(serve.load_page(d.tmp, "§P1D", b.src.stamp)["status"], "ok")
            row = next(r for r in d.manifest.rows if r["p"] == "read/P3Q.md")
            self.assertIn("C", row["fams"])

    def test_the_stamp_line_is_one_short_line_and_changes_nothing(self):
        before = (self.dist.tmp / "manifest.jsonl").read_bytes()
        self.assertEqual(serve.stamp_line(self.dist.tmp, self.stamp), f"built={self.stamp} current={self.stamp} stale=false")
        self.assertEqual(serve.stamp_line(self.dist.tmp, "11111111"), f"built={self.stamp} current=11111111 stale=true")
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(serve.stamp_line(Path(empty), self.stamp), f"built=none current={self.stamp} stale=false")
        self.assertLessEqual(len(serve.stamp_line(self.dist.tmp, self.stamp)), 200)
        self.assertEqual((self.dist.tmp / "manifest.jsonl").read_bytes(), before)

    def test_the_page_list_echoes_the_manifest(self):
        lines = serve.page_list(self.dist.tmp, "2D")
        self.assertTrue(lines and all(x.startswith("§") for x in lines))
        self.assertTrue(any(x.startswith("§S01 page 2D ") for x in lines))
        lines5 = serve.page_list(self.dist.tmp, "5D")
        self.assertTrue(lines5 and all(x.startswith("§V") for x in lines5), "the intervention pages are pages of the manifest (R3)")
        self.assertEqual(serve.page_list(self.dist.tmp, "6D"), [])

    def test_a_manifest_that_is_not_json_is_a_missing_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "manifest.jsonl").write_text("not json\n", encoding="utf-8")
            self.assertEqual(serve.load_page(Path(tmp), "§S01", self.stamp)["code"], serve.E_NO_BUILD)


if __name__ == "__main__":
    unittest.main()
