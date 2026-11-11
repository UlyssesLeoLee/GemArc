"""Genericity (design A3 D-18, 6.4): any id family, 400 nodes, mixed CJK vocabulary, unknown kinds, no cases, no interventions, odd ids, empty strings:
the build has no integrity finding (L-class degradations are recorded and the build is ``ok``). Behaviour fixtures, never a grep for id literals.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_genericity
"""
from __future__ import annotations

import unittest

from . import read_support as RS
from . import synth


class EdgeCaseTests(unittest.TestCase):
    def test_every_synthetic_edge_case_builds_without_an_integrity_finding(self):
        for name in synth.EDGE_CASES:
            with self.subTest(case=name):
                b = RS.make(synth.edge_case(name, 3))
                self.assertEqual(b.result.status, "ok", [f"{f.rule} {f.path} {f.message[:100]}" for f in b.result.errors][:3])
                self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set())

    def test_the_400_node_model_is_split_into_many_pages_and_conserved(self):
        b = RS.make(synth.edge_case("big400", 2))
        self.assertEqual(b.result.status, "ok")
        self.assertGreater(b.result.plan["pages"]["S"], 40)
        self.assertEqual(b.result.plan["mode"], "split")
        for path, data in b.result.files.items():
            if path.startswith("read/") and path.endswith(".md"):
                self.assertLessEqual(len(data), 9000, path)


class IdFamilyTests(unittest.TestCase):
    def test_a_different_random_id_family_every_run_changes_nothing_in_the_structure(self):
        prefixes = synth.all_random_prefixes()
        self.assertGreater(len(prefixes), 3)
        for seed in (11, 12, 13):
            with self.subTest(seed=seed):
                b = RS.make(synth.generate("small", seed, family="random"))
                self.assertEqual(b.result.status, "ok")
                self.assertEqual(RS.rules_of(b.result.findings), set())

    def test_the_fixed_family_and_the_lending_desk_build_too(self):
        for family in ("fixed",):
            b = RS.make(synth.generate("small", 5 if False else 6, family=family))
            self.assertEqual(b.result.status, "ok")
        tiny = RS.make(synth.lending_desk("fixed", 2))
        self.assertEqual(tiny.result.status, "ok")

    def test_ids_that_look_like_page_ids_are_never_confused_with_them(self):
        """A model id may be called ``S07`` or ``R02``: the reading surface writes page ids with the section sign, and the checks accept it."""
        import copy

        model = copy.deepcopy(synth.generate("small", 1))
        renames = {model["nodes"][0]["id"]: "S07", model["nodes"][1]["id"]: "R02", model["nodes"][2]["id"]: "C03"}
        text = __import__("json").dumps(model)
        for old, new in renames.items():
            text = text.replace(f'"{old}"', f'"{new}"').replace(f'"{old}:', f'"{new}:').replace(f":{old}\"", f":{new}\"")
        model = __import__("json").loads(text)
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok", [f.message for f in b.result.errors][:2])
        rows = [x.split("\t") for x in b.text("ids.tsv").split("\n") if x and not x.startswith("#")]
        self.assertTrue([r for r in rows if r[0] == "S07" and r[1] == "node"])
        self.assertNotIn("§S07\t", b.text("ids.tsv"))


class VocabularyTests(unittest.TestCase):
    def test_a_cjk_primary_language_and_unknown_vocabulary_do_not_break_the_build(self):
        b = RS.built("refcjk")
        self.assertEqual(b.result.status, "ok")
        model = synth.edge_case("unknown-values", 2)
        self.assertEqual(RS.make(model).result.status, "ok")

    def test_a_model_without_cases_and_interventions(self):
        for name in ("no-cases", "no-interventions"):
            b = RS.make(synth.edge_case(name, 4))
            self.assertEqual(b.result.status, "ok", name)
            self.assertNotIn("C", [k for k, n in b.result.plan["pages"].items() if n and name == "no-cases"])


if __name__ == "__main__":
    unittest.main()
