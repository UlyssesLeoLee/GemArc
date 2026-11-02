"""T-R12 (design A3 6.0): input limits degrade, they never fail a build. The fixtures that exist before the integration lane and the screen lane: names that
differ only in case, cells and lines above 500 characters, a hub, a long case, a diagram above its cap, a path that is too long. What needs the tools (``status``
never ``needs-user``, the ``repair`` line of the reply) or the screen lane (30 controls x 5 states, 60 controls, more than 40 screens) is not here.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_degrade
"""
from __future__ import annotations

import copy
import unittest

from gemarc_mcp.atlas.reading import check as C

from . import read_support as RS
from . import synth


class DegradationFixtures(unittest.TestCase):
    def test_nodes_that_differ_only_in_case_build_and_keep_two_homes(self):
        model = copy.deepcopy(synth.generate("small", 1))
        first = model["nodes"][0]
        twin = copy.deepcopy(first)
        twin["id"] = first["id"].upper() if first["id"].upper() != first["id"] else first["id"].lower()
        twin["portIds"] = []
        model["nodes"].append(twin)
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok")
        rows = [x.split("\t") for x in b.text("ids.tsv").split("\n") if x and not x.startswith("#")]
        self.assertEqual(len([r for r in rows if r[1] == "node" and r[2] == "home" and r[0] in (first["id"], twin["id"])]), 2)
        self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set())

    def test_a_cell_and_a_line_above_500_characters_are_folded_and_recorded(self):
        model = copy.deepcopy(synth.generate("small", 2))
        model["requirements"][0]["title"] = "long " * 150
        model["tbds"][0]["decision"] = "decision " * 90
        model["nodes"][1]["sub"] = "sentence " * 70
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set())
        degraded = [f for f in b.result.findings if f.rule == "RS-132"]
        self.assertTrue(degraded)
        self.assertTrue(all(f.klass == "L" and f.severity == "WARN" and f.hint for f in degraded))
        self.assertEqual(b.result.degraded, ["RS-132"])
        self.assertTrue(any(r["fit"] for r in b.result.records if r.get("fit")))
        for data in b.result.files.values():
            self.assertLessEqual(max(len(x) for x in data.decode("utf-8").split("\n")), 500)

    def test_a_hub_and_a_long_case_continue_and_the_build_is_ok(self):
        b = RS.make(RS.hub_model(150, 300))
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set())
        parts = [r for r in b.result.records if r["p"].startswith("read/") and r.get("fam") in ("S", "C")]
        self.assertGreater(len(parts), 8)

    def test_a_diagram_above_its_cap_is_a_warning_of_the_class_L(self):
        b = RS.built("small")
        s = RS.copy_surface(b.surface())
        s.files["diagrams/big.mmd"] = b"x" * 7000
        s.extra_paths.add("diagrams/big.mmd")
        findings = RS.check(b, s, ["RS-132"])
        big = [f for f in findings if f.path == "diagrams/big.mmd"]
        self.assertEqual({(f.klass, f.severity) for f in big}, {("L", "WARN")})
        self.assertEqual(RS.rules_of(findings), set())

    def test_a_path_that_is_too_long_or_collides_is_a_warning_not_an_error(self):
        b = RS.built("small")
        s = RS.copy_surface(b.surface())
        s.extra_paths |= {"diagrams/" + "a" * 120 + ".mmd", "diagrams/Pump.mmd", "diagrams/pump.mmd"}
        findings = RS.check(b, s, ["RS-117"])
        self.assertEqual(RS.rules_of(findings), set())
        warn = [f for f in findings if f.rule == "RS-117"]
        self.assertGreaterEqual(len(warn), 2)
        self.assertEqual({(f.klass, f.severity) for f in warn}, {("L", "WARN")})
        self.assertTrue(all(f.hint for f in warn))

    def test_the_ruler_that_does_not_fit_the_reading_line_degrades_to_a_pointer(self):
        model = copy.deepcopy(synth.generate("small", 1))
        b = RS.make(model)
        bundle = copy.deepcopy(b.bundle)
        for n in range(8):                                   # eight more grades: the ordered ruler no longer fits the reading line
            bundle["profile"]["verification"].append({"id": f"ExtraGradeWithALongName{n}", "rank": 0, "label": {"en-US": "x", "zh-CN": "x", "ja-JP": "x"}})
        from gemarc_mcp.atlas import reading as R

        result = R.build(bundle, RS.meta_for(bundle), R.ReadConfig(), RS.UI)
        self.assertEqual(result.status, "ok")
        self.assertIn("RS-132", result.degraded)


if __name__ == "__main__":
    unittest.main()
