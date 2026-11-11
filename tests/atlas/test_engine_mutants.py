"""Tests of the deterministic mutants (``engine/mutate.py``, rule OR-004 input).

Hand-computed mutant lists on the synthetic lending-desk fixture, caps, determinism, the kill semantics with the three kinds
of expectation set, and the vacuous-expectation case (a lazy set that nothing can kill).

Run only this file::

    PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_mutants*.py"
"""
from __future__ import annotations

import copy
import json
import random
import unittest
from pathlib import Path

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas.engine import mutate

from . import synth

DESK = json.loads((Path(__file__).resolve().parent / "fixtures" / "lending_desk.model.json").read_text(encoding="utf-8"))


def desk_copy() -> dict:
    """The desk model plus one node that no case touches (the target of lazy expectations)."""
    model = copy.deepcopy(DESK)
    model["nodes"].append({**copy.deepcopy(model["nodes"][0]), "id": "zz.idle", "name": "Unused"})
    return model


def desk_case() -> dict:
    return next(c for c in DESK["cases"] if c["caseId"] == "CS-BORROW-OK")


def chain_case(n_nodes: int, n_events: int) -> dict:
    """A synthetic case over ``n_nodes`` nodes: event i goes node (i mod n) -> node ((i + 1) mod n)."""
    events = [{"seq": i + 1, "type": "Call", "from": f"nd{i % n_nodes:02d}", "to": f"nd{(i + 1) % n_nodes:02d}"} for i in range(n_events)]
    return {"caseId": "CS-X", "category": "normal", "events": events}


class ListingTests(unittest.TestCase):
    def test_participants_in_first_appearance_order(self):
        self.assertEqual(mutate.participants(desk_case()), ["usr.member", "svc.loan", "svc.stock", "tbl.loan"])

    def test_degree_counts_touching_events(self):
        self.assertEqual(mutate.degrees(desk_case()), {"usr.member": 2, "svc.loan": 6, "svc.stock": 2, "tbl.loan": 2})

    def test_a_self_event_counts_once(self):
        case = {"caseId": "C", "events": [{"seq": 1, "type": "Wait", "from": "a", "to": "a"}]}
        self.assertEqual(mutate.degrees(case), {"a": 1})

    def test_desk_mutants_by_hand(self):
        """4 participants by degree then id, then 4 spread deletions out of 6 events: seq 1, 3, 4, 6."""
        mutants = mutate.generate_mutants(desk_case())
        self.assertEqual([m["id"] for m in mutants], [
            "CS-BORROW-OK:disable:svc.loan", "CS-BORROW-OK:disable:svc.stock", "CS-BORROW-OK:disable:tbl.loan",
            "CS-BORROW-OK:disable:usr.member", "CS-BORROW-OK:delete:1", "CS-BORROW-OK:delete:3", "CS-BORROW-OK:delete:4",
            "CS-BORROW-OK:delete:6"])
        self.assertEqual(mutants[0]["deltas"], [{"type": "disable_module", "targetNodeId": "svc.loan"}])

    def test_caps_and_split(self):
        case = chain_case(20, 40)
        for cap, disables, deletes in ((12, 8, 4), (6, 4, 2), (3, 2, 1), (2, 2, 0), (1, 1, 0), (0, 0, 0), (50, 8, 4)):
            kinds = [m["kind"] for m in mutate.generate_mutants(case, cap)]
            self.assertEqual((kinds.count("disable"), kinds.count("delete")), (disables, deletes), cap)
            self.assertLessEqual(len(kinds), max(0, min(cap, 12)))

    def test_deleted_events_are_spread_with_first_and_last(self):
        case = chain_case(5, 40)
        seqs = [m["seq"] for m in mutate.generate_mutants(case) if m["kind"] == "delete"]
        self.assertEqual(seqs, [1, 14, 27, 40])

    def test_short_cases_have_fewer_mutants(self):
        one = {"caseId": "C", "events": [{"seq": 1, "type": "Call", "from": "a", "to": "b"}]}
        self.assertEqual([m["id"] for m in mutate.generate_mutants(one)], ["C:disable:a", "C:disable:b", "C:delete:1"])
        self.assertEqual(mutate.generate_mutants({"caseId": "C", "events": []}), [])

    def test_the_highest_degree_participants_are_chosen(self):
        case = chain_case(20, 40)
        case["events"].extend({"seq": 41 + i, "type": "Call", "from": "nd19", "to": "nd07"} for i in range(5))
        chosen = [m["target"] for m in mutate.generate_mutants(case) if m["kind"] == "disable"]
        self.assertEqual(chosen[:2], ["nd07", "nd19"])
        self.assertEqual(len(chosen), 8)

    def test_order_does_not_depend_on_the_event_list_order(self):
        case = chain_case(12, 30)
        shuffled = copy.deepcopy(case)
        random.Random(5).shuffle(shuffled["events"])
        self.assertEqual(mutate.generate_mutants(case), mutate.generate_mutants(shuffled))

    def test_every_case_of_a_reference_scale_model_stays_within_the_cap(self):
        model = synth.generate("reference", 3)
        counts = [len(mutate.generate_mutants(c)) for c in model["cases"]]
        self.assertEqual(len(counts), 23)
        self.assertTrue(all(0 < n <= 12 for n in counts), counts)
        self.assertEqual(counts, [len(mutate.generate_mutants(c)) for c in copy.deepcopy(model["cases"])])


class KillTests(unittest.TestCase):
    def setUp(self):
        self.model = desk_copy()
        self.case = next(c for c in self.model["cases"] if c["caseId"] == "CS-BORROW-OK")
        self.ix = E.index_model(self.model)

    def run_case(self, **kwargs):
        return mutate.run_mutants(self.model, self.case, index=self.ix, **kwargs)

    def test_the_fixture_expectations_hold_on_the_unmodified_case(self):
        report = self.run_case()
        self.assertTrue(report["baselineHolds"])
        self.assertEqual(report["baselineViolations"], 0)
        self.assertEqual(report["total"], 8)

    def test_full_terminal_expectations_kill_every_mutant(self):
        report = self.run_case()
        self.assertEqual(report["killed"], 8)
        self.assertEqual(report["rate"], 1.0)
        self.assertFalse(report["zeroKilled"])
        self.assertTrue(all(m["killed"] and m["violations"] >= 1 for m in report["mutants"]))

    def test_a_lazy_non_participant_idle_set_kills_nothing(self):
        """The vacuous expectation: it holds on the case and on every mutant (OR-004: zero killed)."""
        self.assertNotIn("zz.idle", mutate.participants(self.case))
        report = self.run_case(assertions={"terminal": {"zz.idle": "Idle"}})
        self.assertTrue(report["baselineHolds"])
        self.assertEqual((report["total"], report["killed"], report["rate"]), (8, 0, 0.0))
        self.assertTrue(report["zeroKilled"])

    def test_a_one_node_expectation_kills_only_what_reaches_that_node(self):
        report = self.run_case(assertions={"terminal": {"tbl.loan": "Completed"}})
        killed = {m["id"] for m in report["mutants"] if m["killed"]}
        self.assertIn("CS-BORROW-OK:disable:tbl.loan", killed)
        self.assertIn("CS-BORROW-OK:disable:svc.loan", killed)
        self.assertNotIn("CS-BORROW-OK:disable:svc.stock", killed)
        self.assertNotIn("CS-BORROW-OK:delete:6", killed)
        self.assertEqual(report["killed"], len(killed))

    def test_expected_outcomes_are_killed_by_deleting_the_event(self):
        spec = {"terminal": {}, "outcomes": [{"ruleId": "R-BORROW", "outcome": "OK"}]}
        report = self.run_case(assertions=spec)
        killed = {m["id"] for m in report["mutants"] if m["killed"]}
        self.assertIn("CS-BORROW-OK:delete:1", killed)
        self.assertNotIn("CS-BORROW-OK:delete:3", killed)
        self.assertNotIn("CS-BORROW-OK:disable:tbl.loan", killed)
        self.assertIn("CS-BORROW-OK:disable:svc.loan", killed)  # the entry event is Blocked, no longer OK

    def test_an_error_code_in_an_expected_outcome_must_match(self):
        spec = {"terminal": {}, "outcomes": [{"ruleId": "R-BORROW", "outcome": "OK", "errorCode": "NOPE"}]}
        report = self.run_case(assertions=spec)
        self.assertFalse(report["baselineHolds"])

    def test_the_case_carries_its_own_outcomes(self):
        self.case["expectedOutcomes"] = [{"ruleId": "R-CHECK", "outcome": "OK"}]
        self.case["expectedTerminalState"] = {}
        report = mutate.run_mutants(self.model, self.case, index=E.index_model(self.model))
        self.assertTrue(report["baselineHolds"])
        killed = {m["id"] for m in report["mutants"] if m["killed"]}
        self.assertIn("CS-BORROW-OK:disable:svc.stock", killed)
        self.assertIn("CS-BORROW-OK:disable:svc.loan", killed)

    def test_wrong_expectations_are_flagged_not_hidden(self):
        report = self.run_case(assertions={"terminal": {"tbl.loan": "Failed"}})
        self.assertFalse(report["baselineHolds"])
        self.assertEqual(report["baselineViolations"], 1)

    def test_callable_assertions(self):
        never_violated = self.run_case(assertions=lambda run: [])
        self.assertEqual(never_violated["killed"], 0)
        always_violated = self.run_case(assertions=lambda run: ["x"])
        self.assertEqual((always_violated["killed"], always_violated["baselineHolds"]), (8, False))

        def final_is_clean(run):
            return [n for n, s in run["finalState"].items() if s in ("Blocked", "Failed")]

        report = self.run_case(assertions=final_is_clean)
        self.assertTrue(report["baselineHolds"])
        self.assertTrue(report["mutants"][0]["killed"])

    def test_deleting_an_event_keeps_the_other_sequence_numbers(self):
        mutant = next(m for m in mutate.generate_mutants(self.case) if m["id"].endswith("delete:3"))
        run = mutate._mutant_run(self.model, self.case, mutant, self.ix)
        seen = {e["eventId"] for e in run["trace"]}
        self.assertNotIn("CS-BORROW-OK#3", seen)
        self.assertIn("CS-BORROW-OK#4", seen)

    def test_mutation_never_modifies_the_model(self):
        before = json.dumps(self.model, sort_keys=True)
        self.run_case()
        mutate.mutation_report(self.model)
        self.assertEqual(json.dumps(self.model, sort_keys=True), before)

    def test_reports_are_deterministic(self):
        a = json.dumps(self.run_case(), sort_keys=True)
        b = json.dumps(mutate.run_mutants(copy.deepcopy(self.model), copy.deepcopy(self.case)), sort_keys=True)
        self.assertEqual(a, b)

    def test_a_smaller_cap_runs_fewer_mutants(self):
        self.assertEqual(self.run_case(cap=6)["total"], 6)
        self.assertEqual(self.run_case(cap=0)["total"], 0)
        self.assertIsNone(self.run_case(cap=0)["rate"])


class ReportTests(unittest.TestCase):
    def test_report_sums_add_up_and_filter_by_case(self):
        model = copy.deepcopy(DESK)
        report = mutate.mutation_report(model)
        self.assertEqual(report["total"], sum(c["total"] for c in report["cases"]))
        self.assertEqual(report["killed"], sum(c["killed"] for c in report["cases"]))
        one = mutate.mutation_report(model, case_ids=["CS-BORROW-OK"])
        self.assertEqual([c["caseId"] for c in one["cases"]], ["CS-BORROW-OK"])

    def test_assertions_by_case_and_by_callable(self):
        model = desk_copy()
        lazy = {"CS-BORROW-OK": {"terminal": {"zz.idle": "Idle"}}}
        self.assertEqual(mutate.mutation_report(model, lazy)["killed"], 0)
        self.assertEqual(mutate.mutation_report(model, lambda case: {"terminal": {}})["killed"], 0)

    def test_reference_scale_synthetic_with_engine_derived_expectations(self):
        """Expectations fixed to the engine's own final states (a test device only): every case is checked and capped."""
        model = synth.generate("reference", 3)
        ix = E.index_model(model)
        spec = {}
        for case in model["cases"]:
            run = E.simulate(model, case, [], {"index": ix})
            spec[case["caseId"]] = {"terminal": {n: run["finalState"][n] for n in mutate.participants(case)}}
        report = mutate.mutation_report(model, spec, index=ix)
        self.assertEqual(len(report["cases"]), 23)
        self.assertTrue(all(c["baselineHolds"] for c in report["cases"]))
        self.assertTrue(all(c["total"] <= 12 for c in report["cases"]))
        self.assertGreater(report["rate"], 0.5)
        self.assertEqual(json.dumps(report, sort_keys=True), json.dumps(mutate.mutation_report(model, spec), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
