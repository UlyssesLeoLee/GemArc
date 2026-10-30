"""``simulate`` and the anti-vacuity run at reference scale (design M5 (b), (d), (e)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_simulate.py"

* (d) fixtures: a lazy one-node assertion fails OR-003, a set of non-participant Idle nodes fails OR-004, an all-Completed
  concurrency case passes with 0 false positives (static rules and the mutant run).
* (e) timings on the reference-scale synthetic atlas (169 nodes, 23 cases, 322 events, 21 interventions): a verdict run (all
  baselines and all pairs) and the build-style run (verdicts, up to 12 mutants per case, guard scan). The measured numbers are
  printed; the limits are 2 s and 10 s.
* (b) the guard scan finds no mismatch between the engine and its table oracle on the valid models and the mutant run reports
  its own counts.

Synthetic id families only. The expectations of the reference-scale project are computed from engine output BEFORE the project
exists (a fixture), see ``test_oracle_support.consistent``.
"""
from __future__ import annotations

import json
import time
import unittest

from gemarc_mcp.atlas import engine, oracle, slicecheck, tools
from gemarc_mcp.atlas.rules import or_

from . import synth
from .test_oracle_support import OracleProject, consistent, desk_files, slice_texts

CASE_ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
IDLE_NODES = ("svc.idle-a | Idle one | Never used by a case. | room=rm.desk kind=Module ev=EVD-1\n"
              "svc.idle-b | Idle two | Never used by a case. | room=rm.desk kind=Module ev=EVD-1\n")


class ReferenceScale(OracleProject):
    """One reference-scale synthetic project with engine-consistent expectations, built once per test class."""

    @classmethod
    def build(cls, seed: int = 3):
        return consistent(synth.generate("reference", seed))

    def make_project(self, model: dict) -> None:
        self.init(name="atlas", repos=model["meta"]["repos"])
        for name, text in slice_texts(model).items():
            reply = self.put(name, text)
            self.assertEqual(reply["status"], "committed", (name, reply["diagnostics"][:300]))
            self.assertLessEqual(tools.reply_bytes(reply), tools.DEFAULT_CAP, "put replies stay within their cap, sealing note included")


class ReferenceScaleTests(ReferenceScale):
    def test_a_verdict_run_and_the_build_style_run_stay_inside_their_budgets(self):
        model = self.build()
        self.make_project(model)
        items = or_.collect_items(model)
        self.assertEqual(len(model["cases"]), 23)
        self.assertEqual(len(model["interventions"]), 21)
        self.assertEqual({s for _i, s, _w in oracle.effective_states(self.paths, self.snap().model).values()} - {"sealed"}, set(), "all sealed by the puts")
        t = time.perf_counter()
        reply = self.simulate()
        verdict_s = time.perf_counter() - t
        pairs = reply["summary"]["pairs"]["run"]
        self.assertEqual(reply["summary"]["baselines"]["run"], len(model["cases"]))
        self.assertEqual(reply["summary"]["baselines"]["fail"] + reply["summary"]["pairs"]["fail"], 0, reply.get("fail"))
        self.assertGreaterEqual(pairs, 20)
        self.assertLessEqual(tools.reply_bytes(reply), tools.SIMULATE_CAP)
        t = time.perf_counter()
        bite = tools.oracle_bite(self.ws, self.name)
        build_s = time.perf_counter() - t
        self.assertEqual(bite["status"], "ok")
        mutants = bite["bite"]["mutants"]
        print(f"\n[oracle timings, reference scale: {len(items)} assertions, {pairs} pairs] verdict run {verdict_s:.2f} s "
              f"(limit 2 s); build-style run {build_s:.2f} s (limit 10 s) with {bite['bite']['cases']} cases, {mutants} mutants, "
              f"kill rate {bite['bite']['rate']}, {bite['guards']['stats']['guards']} guards, partial={bite['partial']}")
        self.assertLessEqual(verdict_s, 2.0)
        self.assertLessEqual(build_s, 10.0)
        self.assertFalse(bite["partial"])
        self.assertGreater(mutants, 100, "the mutant run did work: it is not vacuous")
        self.assertEqual(bite["skipped"], [])
        self.assertEqual(bite["guards"]["findings"], 0, "valid synthetic guards: nothing to report")

    def test_the_reply_stays_within_three_kilobytes_with_failures_present(self):
        model = self.build()
        for case in model["cases"][:15]:                       # fifteen cases whose expectation is wrong on one node
            node = next(iter(case["expectedTerminalState"]))
            case["expectedTerminalState"][node] = "Failed" if case["expectedTerminalState"][node] != "Failed" else "Completed"
        self.make_project(model)
        reply = self.simulate()
        self.assertLessEqual(tools.reply_bytes(reply), tools.SIMULATE_CAP)
        self.assertEqual(reply["summary"]["baselines"]["fail"], 15)
        self.assertGreaterEqual(len(reply["fail"]) + reply.get("notShown", 0), 15)
        text = json.dumps(reply)
        for case in model["cases"][:15]:
            for node in case["expectedTerminalState"]:
                self.assertNotIn(f'"{node}"', text)

    def test_every_ledgered_reveal_appears_in_the_reply(self):
        model = self.build()
        for case in model["cases"][:6]:
            node = next(iter(case["expectedTerminalState"]))
            case["expectedTerminalState"][node] = "Failed" if case["expectedTerminalState"][node] != "Failed" else "Completed"
        self.make_project(model)
        wrong = [c["caseId"] for c in model["cases"][:6]]
        for cid in wrong:
            reply = self.simulate(case=cid, detail="trace")
            self.assertEqual(len(reply["trace"]), 1)
            self.assertEqual(self.entry(f"case:{cid}")["exposed"]["trace"], 1)
            self.assertLessEqual(tools.reply_bytes(reply), tools.SIMULATE_CAP)
        other = model["cases"][10]["caseId"]
        self.assertEqual(self.entry(f"case:{other}")["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0}, "nothing was shown for it")

    def test_a_mutated_event_set_is_what_the_assertions_are_judged_on(self):
        model = self.build()
        self.make_project(model)
        bite = tools.oracle_bite(self.ws, self.name)["bite"]
        self.assertEqual(bite["zeroKilled"], [])
        self.assertGreater(bite["rate"], 0.25, "assertions that restate every participant bite")

    def test_the_guard_scan_finds_no_mismatch_on_the_reference_scale_models(self):
        for seed in (3, 7):
            model = synth.generate("reference", seed)
            report = engine.sweep.scan_model(model)
            self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [], seed)


class AntiVacuityFixtureTests(OracleProject):
    """Design M5 (d)."""

    def desk_with(self, *edits):
        self.init()
        self.load(desk_files(("10-structure", "@set kind=Storage\n", "@set kind=Storage\n"), *edits))

    def test_a_lazy_one_node_assertion_fails_OR_003(self):
        self.desk_with(("20-scenarios", CASE_ROW, "CS-BORROW-OK | usr.member | by=author"))
        found = [d for d in tools.check(self.ws, self.name, mode="full", rule="OR", format="json")["diagnostics"]["diagnostics"] if d["rule"] == "OR-003"]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["severity"], found[0]["got"]), ("ERROR", "1 asserted"))
        self.assertIn("2 of the 4 participants", found[0]["hint"])
        self.assertFalse(tools.check(self.ws, self.name, mode="full", rule="OR")["clean"])

    def test_a_set_of_non_participant_Idle_nodes_fails_OR_004(self):
        self.init()
        files = desk_files()
        files["10-structure"] = files["10-structure"].replace("@set kind=Storage\n", "@set kind=Module\n" + IDLE_NODES + "@set kind=Storage\n")
        files["20-scenarios"] = files["20-scenarios"].replace(CASE_ROW, "CS-BORROW-OK | svc.idle-a=Idle,svc.idle-b=Idle | by=author")
        self.load(files)
        self.assertEqual(self.simulate()["clean"], True, "the lazy assertions hold: that is the point")
        bite = tools.oracle_bite(self.ws, self.name)["bite"]
        self.assertEqual(bite["zeroKilled"], ["CS-BORROW-OK"])
        found = [d for d in tools.check(self.ws, self.name, mode="full", rule="OR", format="json")["diagnostics"]["diagnostics"] if d["rule"] == "OR-004"]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["severity"], found[0]["got"]), ("ERROR", "0 of 8 mutants killed"))
        self.assertEqual(self.entry("case:CS-BORROW-OK")["bite"]["killed"], 0)

    def test_an_all_Completed_concurrency_case_passes_with_no_false_positive(self):
        events = ("## events: type | from | to | opts\n@set case=CS-PAR\nCall | usr.member | svc.loan | ruleId=R-P0\n"
                  "Call | svc.loan | svc.stock | group=g1 ruleId=R-P1 latencyMs=10\nCall | svc.loan | tbl.loan | group=g1 ruleId=R-P2 latencyMs=12\n"
                  "Return | svc.stock | svc.loan | ruleId=R-P3\n" "Return | tbl.loan | svc.loan | ruleId=R-P4\n" "Return | svc.loan | usr.member | ruleId=R-P5\n")
        scenario = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PAR | concurrency | Stock and table are asked at the same time | req=RQ-1 ev=EVD-1\n" + events
                    + "## expect: case | nodes | opts\nCS-PAR | usr.member,svc.loan,svc.stock,tbl.loan | by=author\n")
        self.init()
        self.load(desk_files())
        self.assertEqual(self.put("30-parallel", scenario)["status"], "committed")
        reply = self.simulate(case="CS-PAR")
        self.assertTrue(reply["clean"], reply)
        bite = tools.oracle_bite(self.ws, self.name)
        self.assertEqual(bite["bite"]["zeroKilled"], [])
        diags = tools.check(self.ws, self.name, mode="full", rule="", format="json")
        self.assertTrue(diags["clean"])
        full = tools.check(self.ws, self.name, mode="full", rule="OR", format="json")["diagnostics"]["diagnostics"]
        self.assertEqual([d for d in full if "PAR" in d["path"]], [], "0 false positives: no OR-002, OR-003 or OR-004 for the case")
        static = slicecheck.check({f"{k}.atl": v for k, v in desk_files().items()} | {"30-parallel.atl": scenario},
                                  self.cfg().meta(), mode="full", wave_set=(1, 2))
        self.assertEqual({d.rule for d in static.diagnostics}, set())

    def test_the_weak_bite_warning_needs_a_normal_case_with_five_participants(self):
        self.desk()
        data = self.ledger()
        entry = data["items"]["case:CS-BORROW-OK"]
        sha = self.snap().sha8
        entry["bite"] = {"sha": sha, "total": 12, "killed": 2, "holds": True}
        self.write_ledger(data, rechain=True)
        found = tools.check(self.ws, self.name, mode="full", rule="OR", format="json")["diagnostics"]["diagnostics"]
        self.assertEqual([d for d in found if d["rule"] == "OR-004"], [], "four participants: the weak-bite warning does not apply")
        self.assertEqual(or_.BITE_WARN_PARTICIPANTS, 5)

    def test_a_mismatching_baseline_gets_no_mutant_run(self):
        self.desk(("20-scenarios", CASE_ROW, CASE_ROW.replace("svc.stock", "svc.stock=Failed")))
        reply = tools.oracle_bite(self.ws, self.name)
        self.assertEqual(reply["skipped"], ["CS-BORROW-OK mismatch"])
        self.assertEqual(reply["bite"]["cases"], 0)
        self.assertIsNone(self.entry("case:CS-BORROW-OK")["bite"])
        self.assertEqual(self.entry("case:CS-BORROW-OK")["exposed"]["mismatch"], 1, "the failed verdict is ledgered like any other")

    def test_the_mutant_run_is_time_boxed(self):
        self.desk()
        reply = oracle.bite_run(self.ws, self.name, budget=-1.0)
        self.assertTrue(reply["partial"])
        self.assertEqual(reply["skipped"], ["CS-BORROW-OK time"])
        self.assertEqual(reply["bite"]["mutants"], 0)

    def test_the_mutant_set_is_deterministic(self):
        self.desk()
        first = tools.oracle_bite(self.ws, self.name)
        second = tools.oracle_bite(self.ws, self.name)
        for key in ("bite", "guards", "skipped"):
            self.assertEqual(first[key], second[key])


class SimulateReplyTests(OracleProject):
    def test_the_reply_shape(self):
        self.desk()
        reply = self.simulate()
        self.assertEqual(set(reply) - {"registered"}, {"status", "authority", "detail", "modelSha8", "summary", "pass", "next", "clean"})
        self.assertEqual((reply["status"], reply["authority"], reply["detail"]), ("ok", "local", "count"))
        self.assertLess(tools.reply_bytes(reply), 700)

    def test_a_named_pair_runs_only_that_pair(self):
        self.desk()
        reply = self.simulate(case="CS-BORROW-OK", intervention="IV-STOCK-DOWN")
        self.assertEqual(reply["summary"]["baselines"]["run"], 0)
        self.assertEqual(reply["summary"]["pairs"]["run"], 1)
        self.assertEqual(reply["pass"], ["IV-STOCK-DOWN x CS-BORROW-OK"])

    def test_the_intervention_alone_runs_its_pairs_and_the_case_alone_runs_the_baseline(self):
        self.desk()
        self.assertEqual(self.simulate(intervention="IV-STOCK-DOWN")["summary"]["pairs"]["run"], 1)
        self.assertEqual(self.simulate(case="CS-BORROW-OK")["summary"], {"baselines": {"run": 1, "pass": 1, "fail": 0, "undecided": 0},
                                                                          "pairs": {"run": 0, "pass": 0, "fail": 0, "undecided": 0}})

    def test_a_truncated_run_is_undecided_not_a_verdict(self):
        self.desk()
        real = engine.simulate

        def truncated(model, case, deltas=None, opts=None):
            run = real(model, case, deltas, opts)
            return {**run, "status": "LIMIT_EXCEEDED"}

        from unittest import mock
        with mock.patch.object(oracle, "simulate", truncated):
            reply = self.simulate(case="CS-BORROW-OK")
        self.assertEqual(reply["summary"]["baselines"]["undecided"], 1)
        self.assertFalse(reply["clean"])
        self.assertIsNone(self.entry("case:CS-BORROW-OK")["last"], "an undecided run is neither a pass nor a mismatch")

    def test_a_contract_breaking_model_gets_no_verdict_and_the_seal_still_happens(self):
        self.desk()
        path = self.paths.slices / "20-scenarios.atl"
        path.write_text(path.read_text(encoding="utf-8").replace("Call | usr.member | svc.loan |", "Call | usr.member | svc.nope |", 1), encoding="utf-8", newline="\n")
        reply = self.simulate()
        self.assertEqual((reply["status"], reply["code"]), ("invalid", oracle.E_MODEL))
        self.assertIsNone(self.entry("case:CS-BORROW-OK")["last"])


if __name__ == "__main__":
    unittest.main()
