"""Tests of the generic guard scan (``engine/scan.py``, rule SM-006).

Hand vectors for the synthesis, the 611 guard vectors of the engine fixtures, the engine probe over fixture and generator
models, planted always-true and never-satisfiable guards, and the guard that the probe must catch when the synthesis lies.
All ids are synthetic.

Run only this file with ``unittest discover -s tests/atlas -t tests`` and a ``-p`` pattern for it (see tests/atlas/README.md).
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas.engine import sweep as scan

from . import synth

FIXTURES = Path(__file__).resolve().parent / "engine_fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def holds(guard, found) -> bool:
    return E.eval_guard(guard, {"params": found["params"], "context": found["context"]})


EQ = lambda p, v: {"op": "eq", "param": p, "value": v}  # noqa: E731
PRESENT = lambda p: {"op": "present", "param": p}  # noqa: E731


class SynthesisTests(unittest.TestCase):
    """Satisfying and falsifying values, worked out by hand."""

    def test_eq_gives_the_literal(self):
        found = scan.synthesise(EQ("role", "admin"))
        self.assertEqual(found, {"params": {"role": "admin"}, "context": {}})

    def test_eq_with_a_number_and_a_boolean_keeps_the_type(self):
        self.assertEqual(scan.synthesise(EQ("n", 5))["params"], {"n": 5})
        self.assertIs(scan.synthesise(EQ("b", True))["params"]["b"], True)
        self.assertEqual(scan.synthesise(EQ("z", 0))["params"], {"z": 0})

    def test_in_gives_the_first_member(self):
        self.assertEqual(scan.synthesise({"op": "in", "param": "k", "value": ["x", "y", "z"]})["params"], {"k": "x"})

    def test_present_gives_a_non_empty_value_and_absent_gives_none(self):
        value = scan.synthesise(PRESENT("p"))["params"]["p"]
        self.assertIsInstance(value, str)
        self.assertTrue(value)
        found = scan.synthesise({"op": "absent", "param": "p"})
        self.assertTrue(holds({"op": "absent", "param": "p"}, found))

    def test_not_gives_the_failing_value_of_the_child(self):
        guard = {"not": EQ("role", "admin")}
        found = scan.synthesise(guard)
        self.assertNotEqual(found["params"].get("role"), "admin")
        self.assertTrue(holds(guard, found))
        fail = scan.synthesise(guard, satisfy=False)
        self.assertEqual(fail["params"], {"role": "admin"})

    def test_neq_gives_a_value_that_differs(self):
        guard = {"op": "neq", "param": "a", "value": "x"}
        found = scan.synthesise(guard)
        self.assertTrue(holds(guard, found))
        self.assertNotEqual(found["params"].get("a"), "x")

    def test_all_and_any_recurse(self):
        guard = {"all": [EQ("a", "1"), {"any": [EQ("b", "2"), {"not": PRESENT("c")}]}]}
        found = scan.synthesise(guard)
        self.assertTrue(holds(guard, found))
        self.assertEqual(found["params"]["a"], "1")
        fail = scan.synthesise(guard, satisfy=False)
        self.assertFalse(holds(guard, fail))

    def test_a_fresh_value_never_collides_with_a_literal(self):
        guard = {"all": [{"op": "neq", "param": "a", "value": "sample1"}, PRESENT("a")]}
        found = scan.synthesise(guard)
        self.assertTrue(holds(guard, found))
        self.assertNotEqual(found["params"]["a"], "sample1")

    def test_ctx_reference_with_a_fixed_context_reads_the_context(self):
        guard = EQ("owner", "$ctx.owner")
        found = scan.synthesise(guard, {"owner": "t-1"})
        self.assertEqual(found["params"], {"owner": "t-1"})
        self.assertTrue(holds(guard, found))

    def test_ctx_reference_to_a_missing_key_is_satisfied_by_a_missing_parameter(self):
        """``param === undefined`` holds when the parameter is absent (the interpreter's own semantics)."""
        guard = EQ("owner", "$ctx.nobody")
        found = scan.synthesise(guard, {})
        self.assertTrue(holds(guard, found))
        self.assertNotIn("owner", found["params"])

    def test_ctx_reference_with_a_free_context_is_synthesised_too(self):
        guard = EQ("mode", "$ctx.mode")
        found = scan.synthesise(guard)
        self.assertTrue(holds(guard, found))
        fail = scan.synthesise(guard, satisfy=False)
        self.assertFalse(holds(guard, fail))

    def test_in_with_a_ctx_list(self):
        guard = {"op": "in", "param": "role", "value": "$ctx.roles"}
        found = scan.synthesise(guard)
        self.assertTrue(holds(guard, found))
        self.assertIsInstance(found["context"]["roles"], list)
        found = scan.synthesise(guard, {"roles": ["a", "b"]})
        self.assertEqual(found["params"], {"role": "a"})

    def test_fixed_parameters_are_respected(self):
        guard = {"any": [EQ("target", "X"), PRESENT("why")]}
        found = scan.synthesise(guard, fixed_params={"target": "Y"})
        self.assertEqual(found["params"]["target"], "Y")
        self.assertTrue(holds(guard, found))
        self.assertTrue(found["params"]["why"])

    def test_synthesis_is_deterministic(self):
        guard = {"all": [{"op": "in", "param": "k", "value": ["x", "y"]}, {"not": EQ("m", 3)}]}
        self.assertEqual(json.dumps(scan.synthesise(guard)), json.dumps(scan.synthesise(guard)))

    def test_the_guard_is_never_modified(self):
        guard = {"all": [EQ("a", "1"), {"not": PRESENT("b")}]}
        before = copy.deepcopy(guard)
        scan.classify_guard(guard)
        self.assertEqual(guard, before)


class ClassificationTests(unittest.TestCase):
    def verdict(self, guard, context=None):
        return scan.classify_guard(guard, context)["verdict"]

    def test_ordinary_guards_are_contingent(self):
        for guard in (PRESENT("p"), EQ("a", "x"), {"not": PRESENT("p")}, {"any": [EQ("a", "x"), EQ("a", "y")]},
                      {"all": [EQ("a", "x"), PRESENT("b")]}):
            self.assertEqual(self.verdict(guard), "contingent", guard)

    def test_never_satisfiable_guards(self):
        cases = [
            {"all": [EQ("a", "x"), {"op": "neq", "param": "a", "value": "x"}]},
            {"all": [EQ("a", "x"), EQ("a", "y")]},
            {"all": [PRESENT("a"), {"op": "absent", "param": "a"}]},
            {"op": "in", "param": "a", "value": []},
            {"all": [EQ("a", "x"), {"op": "in", "param": "a", "value": ["y", "z"]}]},
            {"op": "bogus", "param": "a"},
            {"any": [{"op": "in", "param": "a", "value": []}, {"op": "bogus", "param": "a"}]},
        ]
        for guard in cases:
            self.assertEqual(self.verdict(guard), "never", guard)

    def test_always_true_guards(self):
        cases = [
            {"any": [PRESENT("a"), {"op": "absent", "param": "a"}]},
            {"any": [EQ("a", "x"), {"not": EQ("a", "x")}]},
            {"not": {"op": "bogus", "param": "a"}},
            {"not": {"op": "in", "param": "a", "value": []}},
            {"any": [{"op": "neq", "param": "a", "value": "x"}, EQ("a", "x")]},
        ]
        for guard in cases:
            self.assertEqual(self.verdict(guard), "always", guard)

    def test_a_missing_guard_is_always_true(self):
        self.assertEqual(self.verdict(None), "always")

    def test_never_with_a_fixed_context(self):
        guard = {"all": [EQ("a", "$ctx.k"), {"op": "neq", "param": "a", "value": "$ctx.k"}]}
        self.assertEqual(self.verdict(guard, {"k": "v"}), "never")
        self.assertEqual(self.verdict(guard), "never")

    def test_too_many_variables_is_undecided_and_not_reported(self):
        guard = {"all": [PRESENT(f"p{i}") for i in range(9)]}
        self.assertEqual(scan.classify_guard(guard, max_combos=100)["verdict"], "undecided")
        self.assertIsNone(scan.synthesise(guard, max_combos=100))
        found = scan.guard_findings(guard, {"x": 1}, max_combos=100)
        self.assertEqual([f["kind"] for f in found], ["undecided"])
        self.assertEqual(found[0]["severity"], "INFO")
        small = {"all": [PRESENT(f"p{i}") for i in range(4)]}
        self.assertEqual(self.verdict(small), "contingent")
        self.assertEqual(self.verdict(guard), "undecided")  # 5 candidates ** 9 variables is above the default cap


class FindingTests(unittest.TestCase):
    def test_root_findings(self):
        never = scan.guard_findings({"all": [EQ("a", "x"), EQ("a", "y")]}, {"id": "t"}, never_severity="ERROR")
        self.assertEqual([(f["kind"], f["path"], f["severity"], f["code"]) for f in never],
                         [("never_satisfiable", "$", "ERROR", "SM-006")])
        always = scan.guard_findings({"any": [PRESENT("a"), {"op": "absent", "param": "a"}]}, {"id": "t"})
        self.assertEqual([(f["kind"], f["path"], f["severity"]) for f in always], [("always_true", "$", "WARN")])

    def test_a_dead_branch_inside_a_live_guard_is_located(self):
        guard = {"any": [EQ("a", "x"), {"all": [EQ("b", "1"), EQ("b", "2")]}]}
        found = scan.guard_findings(guard, {"id": "t"})
        self.assertEqual([(f["kind"], f["path"]) for f in found], [("never_satisfiable", "$.any[1]")])

    def test_a_redundant_conjunct_is_located(self):
        guard = {"all": [EQ("a", "x"), {"any": [PRESENT("c"), {"op": "absent", "param": "c"}]}]}
        found = scan.guard_findings(guard, {"id": "t"})
        self.assertEqual([(f["kind"], f["path"]) for f in found], [("always_true", "$.all[1]")])

    def test_a_branch_inside_a_reported_subtree_is_not_reported_again(self):
        guard = {"all": [EQ("a", "x"), EQ("a", "y"), {"any": [EQ("b", "1"), {"op": "in", "param": "b", "value": []}]}]}
        self.assertEqual([f["path"] for f in scan.guard_findings(guard, {"id": "t"})], ["$"])

    def test_not_wrapping_a_dead_child_is_reported_at_the_not(self):
        found = scan.guard_findings({"not": {"op": "in", "param": "a", "value": []}}, {"id": "t"})
        self.assertEqual([(f["kind"], f["path"]) for f in found], [("always_true", "$")])

    def test_a_clean_guard_has_no_findings_and_none_has_no_findings(self):
        self.assertEqual(scan.guard_findings({"all": [EQ("a", "x"), {"not": PRESENT("b")}]}, {"id": "t"}), [])
        self.assertEqual(scan.guard_findings(None, {"id": "t"}), [])


class GuardVectorTests(unittest.TestCase):
    """The 611 vectors of the engine fixtures: the scan never contradicts the interpreter."""

    @classmethod
    def setUpClass(cls):
        runs = load("guard-vectors.json")["runs"]
        cls.vectors = [r for r in runs if r.get("op") == "guard"]
        cls.guards = {}
        for r in cls.vectors:
            cls.guards.setdefault(json.dumps(r["guard"], sort_keys=True), r["guard"])

    def test_vectors_exist(self):
        self.assertGreaterEqual(len(self.vectors), 100)
        self.assertGreaterEqual(len(self.guards), 20)

    def test_no_vector_contradicts_the_verdict(self):
        """A vector that passes rules out ``never``; one that fails rules out ``always``: 0 mismatches."""
        mismatches = []
        verdicts: dict[str, str] = {}
        for r in self.vectors:
            key = json.dumps(r["guard"], sort_keys=True)
            ctx = r.get("context")
            verdict = scan.classify_guard(r["guard"], ctx)["verdict"]
            verdicts[key] = verdict
            passed = E.eval_guard(r["guard"], {"params": r.get("params"), "context": ctx})
            if (passed and verdict == "never") or ((not passed) and verdict == "always"):
                mismatches.append((r["id"], verdict, passed))
        self.assertEqual(mismatches, [])
        self.assertGreater(len(set(verdicts.values())), 1)

    def test_every_witness_evaluates_as_claimed(self):
        bad = []
        for guard in self.guards.values():
            for ctx in (None, {"k": "a", "m": ["a", 1]}):
                out = scan.classify_guard(guard, ctx)
                if out["satisfying"] is not None and not holds(guard, out["satisfying"]):
                    bad.append(("sat", guard))
                if out["falsifying"] is not None and holds(guard, out["falsifying"]):
                    bad.append(("fal", guard))
        self.assertEqual(bad, [])


class ProbeTests(unittest.TestCase):
    """The engine probe: accepted and rejected transitions agree with the synthesis on fixtures and generator models."""

    def test_fixture_models_have_zero_mismatches(self):
        total = 0
        for name in ("core.json", "elements.json"):
            model = load(name)["model"]
            report = scan.scan_model(model)
            self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [], name)
            total += report["stats"]["probes"]
        self.assertGreaterEqual(total, 6)

    def test_core_fixture_probes_every_guarded_transition_twice(self):
        model = load("core.json")["model"]
        guarded = [t for m in model["machines"] for t in m["transitions"] if t.get("guard")]
        self.assertEqual(len(guarded), 3)
        report = scan.scan_model(model)
        self.assertEqual(report["stats"]["probes"], 2 * len(guarded))
        self.assertEqual(report["stats"]["transitionGuards"], 3)
        self.assertEqual(report["stats"]["eventGuards"], 4)

    def test_generator_models_have_zero_mismatches(self):
        checked = 0
        models = [synth.generate("small", 2), synth.generate("reference", 3), synth.generate("reference", 11),
                  synth.edge_case("machine", 1), synth.edge_case("rich-fields", 4), synth.edge_case("rich-fields", 9)]
        for model in models:
            report = scan.scan_model(model)
            self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [])
            checked += report["stats"]["probes"]
        self.assertGreater(checked, 20)

    def test_planted_always_true_and_never_satisfiable_transition_guards_are_caught(self):
        model = copy.deepcopy(load("core.json")["model"])
        machine = model["machines"][0]
        machine["transitions"].append({"id": "t-always", "from": "NEW", "to": "DONE",
                                       "guard": {"any": [PRESENT("q"), {"op": "absent", "param": "q"}]}})
        machine["transitions"].append({"id": "t-never", "from": "LOST", "to": "NEW",
                                       "guard": {"all": [EQ("r", "a"), {"op": "neq", "param": "r", "value": "a"}]}})
        report = scan.scan_model(model)
        by_tr = {f["where"].get("transitionId"): f for f in report["findings"]}
        self.assertEqual(by_tr["t-always"]["kind"], "always_true")
        self.assertEqual(by_tr["t-never"]["kind"], "never_satisfiable")
        self.assertEqual(by_tr["t-never"]["severity"], "ERROR")
        self.assertEqual(by_tr["t-always"]["severity"], "WARN")
        self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [])
        self.assertEqual(len(report["findings"]), 2)

    def test_planted_guards_in_a_change_guard_intervention_and_an_event_are_caught(self):
        model = copy.deepcopy(load("core.json")["model"])
        machine = model["machines"][0]
        dead = {"all": [EQ("z", "1"), EQ("z", "2")]}
        model["interventions"].append({"interventionId": "IX-PLANT", "type": "change_guard", "machineId": machine["id"],
                                       "transitionId": machine["transitions"][1]["id"], "guard": dead,
                                       "description": "planted", "appliesTo": [model["cases"][0]["caseId"]],
                                       "expect": {}, "note": ""})
        model["cases"][0]["events"][0]["guard"] = {"not": {"op": "bogus", "param": "x"}}
        report = scan.scan_model(model)
        found = {(f["where"].get("interventionId") or f["where"].get("caseId"), f["kind"]) for f in report["findings"]}
        self.assertIn(("IX-PLANT", "never_satisfiable"), found)
        self.assertIn((model["cases"][0]["caseId"], "always_true"), found)

    def test_a_shadowed_transition_is_skipped_not_reported_as_mismatch(self):
        model = copy.deepcopy(load("core.json")["model"])
        machine = model["machines"][0]
        machine["transitions"].append({"id": "t-shadow", "from": "REVIEW", "to": "DONE", "guard": PRESENT("s")})
        report = scan.scan_model(model)
        self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [])
        self.assertGreaterEqual(report["stats"]["probesSkipped"], 1)

    def test_the_probe_catches_a_synthesis_that_disagrees_with_the_engine(self):
        """If the synthesis were inverted the probe must report every guarded transition (mutation check of the check)."""
        model = load("core.json")["model"]
        real = scan.eval_guard
        with mock.patch.object(scan, "eval_guard", lambda g, c: not real(g, c)):
            report = scan.scan_model(model)
        mismatches = [f for f in report["findings"] if f["kind"] == "probe_mismatch"]
        self.assertEqual(len(mismatches), 6)
        self.assertTrue(all(f["severity"] == "ERROR" for f in mismatches))

    def test_the_probe_does_not_touch_the_model(self):
        model = load("core.json")["model"]
        before = json.dumps(model, sort_keys=True)
        scan.scan_model(model)
        self.assertEqual(json.dumps(model, sort_keys=True), before)

    def test_the_scan_is_deterministic(self):
        model = synth.generate("reference", 3)
        a = json.dumps(scan.scan_model(model), sort_keys=True)
        b = json.dumps(scan.scan_model(copy.deepcopy(model)), sort_keys=True)
        self.assertEqual(a, b)

    def test_models_without_machines_or_guards_give_an_empty_report(self):
        from gemarc_mcp.atlas import model as am
        report = scan.scan_model(am.empty_model())
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["stats"]["probes"], 0)


class HygieneTests(unittest.TestCase):
    def test_no_dynamic_code_and_no_domain_table(self):
        text = (Path(scan.__file__)).read_text(encoding="utf-8")
        for banned in ("eval(", "exec(", "compile(", "__import__"):
            self.assertNotIn(banned, text.replace("eval_guard(", ""))


if __name__ == "__main__":
    unittest.main()
