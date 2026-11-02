"""T-R11 (design A3 6.0): the three kinds of legal model build a reading surface without an integrity finding.

Only the half of the acceptance that belongs to lane R is evaluated here: ``read.status == "ok"`` and no I-class finding. That ``status`` can reach
``done`` for them is lane I's (the gate and the waiver are not part of this package).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_legit
"""
from __future__ import annotations

import copy
import unittest

from gemarc_mcp.atlas import reading as R

from . import read_support as RS
from . import synth


def assert_ok(case: unittest.TestCase, b: RS.Built, what: str) -> None:
    case.assertEqual(b.result.status, "ok", (what, [f"{f.rule} {f.message[:90]}" for f in b.result.errors][:3]))
    case.assertEqual(RS.rules_of(b.result.findings), set(), what)
    case.assertEqual(RS.rules_of(RS.check(b, b.surface())), set(), what)


class LegalModelTests(unittest.TestCase):
    def test_a_to_be_model_with_a_document_evidence_whose_line_ranges_are_all_null(self):
        for preset in ("small", "reference"):
            model = synth.generate(preset, 2, mode="tobe")
            self.assertEqual(model["meta"]["mode"], "tobe")
            self.assertTrue(all(e["lineRange"] is None for e in model["evidences"]))
            b = RS.make(model)
            assert_ok(self, b, f"tobe {preset}")
            body = "\n".join(b.text(p) for p in b.result.files if p.startswith("read/1D/E"))
            self.assertIn("（未盖章）", body)

    def test_an_as_is_model_with_lowered_verification_and_no_evidence_root(self):
        model = copy.deepcopy(synth.generate("small", 3))
        for node in model["nodes"]:
            node["verification"] = "Hypothetical"
            node["evidenceIds"] = []
        for edge in model["edges"]:
            edge["verification"] = "Unknown"
            edge["evidenceIds"] = []
        for table in ("cases", "requirements", "tbds"):
            for rec in model[table]:
                rec["evidenceIds"] = []
        for machine in model["machines"]:
            machine["evidenceIds"] = []
        model["evidences"] = []
        b = RS.make(model)
        assert_ok(self, b, "as-is without evidence")
        self.assertEqual(b.result.plan["pages"].get("E", 0), 0)
        self.assertIn("模型里没有证据", b.text("START.md"))

    def test_an_as_is_model_with_unstamped_stamped_and_odd_evidence_rows(self):
        model = copy.deepcopy(synth.generate("small", 4))
        evs = model["evidences"]
        evs[0].update({"lineRange": [3, 9], "repository": "synth-repo"})
        evs[1].update({"lineRange": None, "repository": ""})
        evs[2].update({"verification": "Unknown", "anchor": "a" * 300})
        evs[3].update({"anchor": "`code` | pipe [link](x) # not a heading, see §S99 and §Z1"})
        model["nodes"][0]["sub"] = "refers to §R77 and §Q9 in the old text"
        b = RS.make(model)
        assert_ok(self, b, "as-is odd evidence")
        text = "\n".join(b.text(p) for p in b.result.files if p.startswith("read/1D/E"))
        self.assertIn("| 3-9 |", text)
        self.assertIn("`synth-repo`", text)
        self.assertIn("（无）", text)
        self.assertIn("...", text)               # the anchor of 300 characters is cut at 120

    def test_a_model_that_is_nearly_empty_still_builds(self):
        model = copy.deepcopy(synth.lending_desk("fixed", 1))
        for table in ("cases", "interventions", "requirements", "tests", "tbds", "policies", "evidences", "machines"):
            model[table] = []
        for node in model["nodes"]:
            node["evidenceIds"] = []
        for edge in model["edges"]:
            edge["evidenceIds"] = []
        b = RS.make(model)
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set())


class StatusHalfTests(unittest.TestCase):
    def test_the_gate_half_is_not_evaluated_by_this_lane(self):
        """Documented limit: the readiness (status reaches done, needs-user and the waiver file) is lane I's; here it is only the result status."""
        self.assertTrue(hasattr(R.ReadResult, "ok"))
        self.assertEqual(R.ReadResult("ok").status, "ok")
        self.assertTrue(R.ReadResult("ok").ok)
        self.assertFalse(R.ReadResult("invalid").ok)


if __name__ == "__main__":
    unittest.main()
