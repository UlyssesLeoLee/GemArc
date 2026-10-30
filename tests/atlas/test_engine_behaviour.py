"""Behaviour tests of the Atlas engine: hand-computed expectations, invariants and the recorded deviations.

Unlike ``test_engine_golden.py`` (parity with frozen reference values) the expectations here were worked out by hand
from the written semantics (clock arithmetic, routing, guards ...). They run on the synthetic fixtures of
``engine_fixtures/`` and on synthetic generator models; no reference data is read.

Run only this file::

    PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_behaviour*.py"
"""
from __future__ import annotations

import ast
import builtins
import copy
import io
import json
import random
import re
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import vocab
from gemarc_mcp.atlas.engine import causal as engine_causal
from gemarc_mcp.atlas.engine import elements as engine_elements
from gemarc_mcp.atlas.engine import types as engine_types

from . import synth
from .test_engine_golden import (DEVIATIONS_FILE, FIXTURE_DIR, execute, fixture_paths, load_fixture, load_golden, resolve_run,
                                 shuffle_model, strip_for_shared)

ENGINE_DIR = Path(E.__file__).resolve().parent
CORE = load_fixture(FIXTURE_DIR / "core.json")
ELEMENTS = load_fixture(FIXTURE_DIR / "elements.json")


class Fx:
    """Convenience over one model fixture."""

    def __init__(self, fixture: dict):
        self.model = fixture["model"]
        self.ix = E.index_model(self.model)

    def case(self, suffix: str) -> dict:
        return next(c for c in self.model["cases"] if c["caseId"].endswith(f"-{suffix}"))

    def node(self, short: str) -> str:
        return next(n["id"] for n in self.model["nodes"] if n["id"].endswith(f".{short}"))

    def iv(self, suffix: str) -> dict:
        return next(i for i in self.model["interventions"] if i["interventionId"].endswith(f"-{suffix}"))

    def sim(self, suffix: str, deltas=None, **opts) -> dict:
        return E.simulate(self.model, self.case(suffix), deltas or [], {**opts, "index": self.ix})

    def entry(self, run: dict, case_suffix: str, seq: int, tail: str = "") -> dict:
        wanted = f"{self.case(case_suffix)['caseId']}#{seq}{tail}"
        return next(e for e in run["trace"] if e["eventId"] == wanted)

    def causal(self, suffix: str, iv_suffix: str, **opts) -> tuple[dict, dict]:
        iv = self.iv(iv_suffix)
        result = E.run_causal(self.model, self.case(suffix)["caseId"], [iv], {**opts, "index": self.ix})
        return result, E.causal_chain(self.model, result, iv, self.ix)


def code_of(entry: dict):
    return (entry["cause"] or {}).get("code")


class ClockAndRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fx(CORE)

    def test_timeout_is_strict_latency_equal_to_the_timeout_succeeds(self):
        run = self.fx.sim("STRICT")
        ok = self.fx.entry(run, "STRICT", 1)
        self.assertEqual((ok["outcome"], ok["eventType"], ok["logicalTime"], ok["attempt"]), ("OK", "Call", 100, 1))
        slow = self.fx.entry(run, "STRICT", 2, "@1")
        self.assertEqual((slow["outcome"], slow["eventType"], slow["logicalTime"], slow["nextState"]), ("Timeout", "Timeout", 200, "Waiting"))
        self.assertEqual(slow["cause"], {"code": "latency_over_timeout", "args": {"latencyMs": 101, "timeoutMs": 100}})
        failed = self.fx.entry(run, "STRICT", 2, "!")
        self.assertEqual((failed["outcome"], failed["eventType"], failed["attempt"], code_of(failed)), ("Failed", "Failure", 1, "retries_used_up"))
        under = self.fx.entry(run, "STRICT", 3)
        self.assertEqual((under["outcome"], under["logicalTime"]), ("OK", 299))

    def test_retry_with_a_latency_array_and_a_constant_backoff_clock(self):
        run = self.fx.sim("RETRY")
        times = [(e["eventId"].split("#", 1)[1], e["eventType"], e["outcome"], e["logicalTime"], e["attempt"]) for e in run["trace"]]
        self.assertEqual(times, [
            ("1@1", "Timeout", "Timeout", 100, 1),   # 300 > 100, clock 0 + 100
            ("1@2", "Timeout", "Timeout", 225, 2),   # + backoff 25, 150 > 100
            ("1@3", "Retry", "OK", 290, 3),          # + backoff 25, latency 40 fits
            ("2@1", "Timeout", "Timeout", 390, 1),   # clock 290
            ("2@2", "Timeout", "Timeout", 515, 2),
            ("2@3", "Timeout", "Timeout", 640, 3),   # no backoff after the last attempt
            ("2!", "Failure", "Failed", 640, 3),
            ("3", "Wait", "OK", 645, None),
            ("4", "Call", "OK", 735, 1),             # the array's last entry is reused: 90 fits
        ])
        self.assertEqual(run["finalState"][self.fx.node("s1")], "Failed")

    def test_the_retry_entry_is_synthetic_only_when_the_delivery_is(self):
        run = self.fx.sim("RETRY")
        # the reference derives `synthetic` from the delivery suffix, not from the per-attempt suffix: pinned
        self.assertFalse(any(e["synthetic"] for e in run["trace"]))

    def test_parallel_group_members_start_together_and_the_clock_is_the_latest_end(self):
        run = self.fx.sim("GROUP")
        by = {e["eventId"].split("#", 1)[1]: e for e in run["trace"]}
        self.assertEqual([by[k]["logicalTime"] - lat for k, lat in (("2", 40), ("3", 90), ("4", 10))], [15, 15, 15])
        self.assertEqual(by["5"]["logicalTime"], 110)    # starts at the group's end, 105
        self.assertEqual(by["6"]["logicalTime"], 117)    # a new group starts at the clock
        self.assertEqual(by["7"]["logicalTime"], 110)    # a Return takes no time and starts with its group

    def test_starved_events_are_derived_from_state(self):
        run = self.fx.sim("STARVE")
        s = lambda n, tail="": self.fx.entry(run, "STARVE", n, tail)  # noqa: E731
        self.assertEqual((s(1)["outcome"], run["entryNodeId"]), ("OK", self.fx.node("u1")))
        self.assertEqual((s(2)["outcome"], s(2)["cause"]), ("Starved", {"code": "source_state", "args": {"state": "Idle"}}))  # never reached
        self.assertEqual((s(3)["outcome"], code_of(s(3))), ("Starved", "no_open_request"))                                     # no request
        self.assertEqual((s(4)["outcome"], s(4)["errorCode"], s(4)["httpStatus"], code_of(s(4))), ("Failed", "E_BOOM", 500, "declared_fault"))
        self.assertEqual((s(5)["outcome"], code_of(s(5))), ("Starved", "no_open_request"))     # gw never took a request that s1 could answer
        self.assertEqual((s(6)["outcome"], s(6)["cause"]), ("Starved", {"code": "source_state", "args": {"state": "Failed"}}))
        self.assertEqual([s(n)["outcome"] for n in (7, 8, 9)], ["OK", "OK", "OK"])             # an unrelated branch still runs
        self.assertEqual(run["finalState"][self.fx.node("s1")], "Failed")
        self.assertEqual(run["finalState"][self.fx.node("s3")], "Completed")

    def test_dedup_reject_and_ignore(self):
        run = self.fx.sim("DEDUP")
        d = lambda n: self.fx.entry(run, "DEDUP", n)  # noqa: E731
        self.assertEqual(d(2)["outcome"], "OK")
        self.assertEqual((d(3)["outcome"], d(3)["errorCode"], d(3)["httpStatus"], code_of(d(3))), ("Rejected", "DUP_REJECT", 409, "duplicate"))
        self.assertEqual(d(4)["outcome"], "OK")                                            # another key
        self.assertEqual(d(5)["outcome"], "OK")
        self.assertEqual((d(6)["outcome"], d(6)["errorCode"], d(6)["httpStatus"]), ("Ignored", None, None))
        self.assertEqual([d(7)["outcome"], d(8)["outcome"]], ["OK", "OK"])               # no dedup property on the target

    def test_aggregation_degrades_when_a_section_is_lost_and_stays_complete_otherwise(self):
        run = self.fx.sim("AGG")
        agg, gw = self.fx.node("agg"), self.fx.node("gw")
        ret = self.fx.entry(run, "AGG", 8)
        self.assertEqual((ret["nextState"], ret["outcome"], run["finalState"][agg]), ("Degraded", "OK", "Degraded"))
        self.assertEqual(ret["cause"], {"code": "sections_missing", "args": {"sections": [self.fx.node("p2")]}})
        self.assertEqual(run["aggregatorFailures"], {agg: [self.fx.node("p2")]})
        self.assertEqual(run["finalState"][gw], "Completed")
        ok = self.fx.sim("AGGOK")
        self.assertEqual(ok["finalState"][agg], "Completed")
        self.assertEqual(ok["aggregatorFailures"], {})

    def test_transition_guards_accept_and_reject(self):
        run = self.fx.sim("MACH")
        m = lambda n: self.fx.entry(run, "MACH", n)  # noqa: E731
        m1 = self.fx.node("m1")
        self.assertEqual([m(n)["outcome"] for n in range(1, 9)], ["OK", "Rejected", "OK", "Rejected", "Rejected", "OK", "OK", "OK"])
        tid = am.transition_id(m1, "REVIEW", "DONE")
        self.assertEqual((m(2)["cause"], m(2)["errorCode"], m(2)["httpStatus"]), ({"code": "transition_guard", "args": {"transitionId": tid}}, "ERR_ROLE", 422))
        self.assertEqual((code_of(m(4)), m(4)["errorCode"]), ("no_such_transition", "ERR_MACHINE"))  # the machine's own code
        self.assertEqual((code_of(m(5)), m(5)["errorCode"]), ("transition_guard", "ERR_MACHINE"))    # the transition has none: machine's
        self.assertEqual(run["finalState"][m1], "LOST")
        self.assertEqual(self.fx.sim("MACHINIT")["finalState"][m1], "DONE")                          # starts in REVIEW

    def test_change_guard_replaces_and_removes_a_guard(self):
        relaxed = self.fx.sim("MACH", [self.fx.iv("GRD-IN")])
        self.assertEqual(self.fx.entry(relaxed, "MACH", 2)["outcome"], "OK")                        # role "user" is now allowed
        unguarded = self.fx.sim("MACH", [self.fx.iv("GRD-OFF")])
        self.assertEqual(self.fx.entry(unguarded, "MACH", 5)["outcome"], "OK")                      # the owner guard is gone

    def test_unbound_events_and_static_edges(self):
        run = self.fx.sim("UNBOUND")
        self.assertEqual([(e["outcome"], code_of(e)) for e in run["trace"][1:3]], [("Unbound", "no_runtime_edge")] * 2)
        self.assertEqual(run["trace"][1]["edgeId"], None)
        starve = self.fx.sim("STARVE")
        # an explicit edge may be a static edge; routing by direction never picks one
        self.assertTrue(next(e for e in self.fx.model["edges"] if e["id"] == starve["trace"][1]["edgeId"])["static"])

    def test_wait_recovery_compensation_and_failure_events(self):
        run = self.fx.sim("MISC")
        e = lambda n: self.fx.entry(run, "MISC", n)  # noqa: E731
        gw, s1, s2, s3 = (self.fx.node(x) for x in ("gw", "s1", "s2", "s3"))
        self.assertEqual((e(2)["outcome"], e(2)["errorCode"], e(2)["httpStatus"]), ("Failed", "E_X", 500))
        self.assertEqual((e(3)["outcome"], e(3)["errorCode"], e(3)["httpStatus"], code_of(e(3))), ("Rejected", "E_G", 422, "guard"))
        self.assertEqual([e(n)["outcome"] for n in (4, 5, 6)], ["OK", "OK", "OK"])           # present / $ctx eq / when node is Failed
        self.assertEqual((e(7)["outcome"], code_of(e(7))), ("Skipped", "condition_false"))   # when node not in [Failed]
        self.assertEqual(e(8)["outcome"], "OK")                                               # all + not
        self.assertEqual((e(9)["outcome"], e(9)["httpStatus"], e(9)["errorCode"]), ("Denied", 403, "E_DENY"))
        self.assertEqual((e(10)["eventType"], e(10)["logicalTime"], e(10)["nextState"]), ("Wait", 40, "Waiting"))
        self.assertEqual((e(11)["outcome"], e(11)["role"], e(11)["nextState"]), ("OK", "recovery", "Recovered"))
        self.assertEqual((e(12)["eventType"], e(12)["outcome"], code_of(e(12))), ("Failure", "OK", "failure_reported"))
        self.assertEqual(e(13)["outcome"], "Starved")                                         # s3 failed, it cannot call
        self.assertEqual(e(14)["outcome"], "Skipped")                                         # any [...] is false
        self.assertEqual(e(15)["outcome"], "OK")                                              # any + not
        self.assertEqual(e(16)["outcome"], "Failed")                                          # Compensation with a fault: both fail
        self.assertEqual({k: run["finalState"][k] for k in (gw, s1, s2, s3)}, {gw: "Failed", s1: "Failed", s2: "Failed", s3: "Failed"})

    def test_the_event_limit_stops_the_run_explicitly(self):
        short = self.fx.sim("LONG", maxEvents=7)
        self.assertEqual((short["status"], len(short["trace"])), ("LIMIT_EXCEEDED", 7))
        full = self.fx.sim("LONG")
        self.assertEqual((full["status"], len(full["trace"])), ("OK", 25))
        self.assertEqual(self.fx.sim("OK", maxEvents=0)["status"], "LIMIT_EXCEEDED")

    def test_an_empty_case_runs_to_an_idle_state(self):
        run = self.fx.sim("EMPTY")
        self.assertEqual((run["status"], run["trace"], run["entryNodeId"]), ("OK", [], None))
        self.assertTrue(all(v == "Idle" for k, v in run["finalState"].items() if k in self.fx.ix.nodes and k not in self.fx.ix.machines))

    def test_intervention_time_applies_the_delta_only_from_t_star(self):
        s2 = self.fx.node("s2")
        run = self.fx.sim("OK", [self.fx.iv("DIS-S2")], interventionTime=25)
        self.assertEqual(self.fx.entry(run, "OK", 3)["outcome"], "OK")                 # starts at 20: before t*
        self.assertEqual(self.fx.entry(run, "OK", 5)["outcome"], "Blocked")            # starts at 30: after t*
        self.assertEqual(code_of(self.fx.entry(run, "OK", 5)), "module_off")
        self.assertEqual(self.fx.entry(run, "OK", 5)["cause"]["args"], {"nodeId": s2})
        whole = self.fx.sim("OK", [self.fx.iv("DIS-S2")])
        self.assertEqual(self.fx.entry(whole, "OK", 3)["outcome"], "Blocked")
        at_start = self.fx.sim("OK", [self.fx.iv("TO")], interventionTime=20)           # >= t*: the event that starts at 20 is changed
        self.assertEqual(self.fx.entry(at_start, "OK", 3)["outcome"], "OK")

    def test_every_simple_intervention_type_does_what_it_says(self):
        base = self.fx.sim("OK")
        self.assertTrue(all(e["outcome"] == "OK" for e in base["trace"]))
        s1, db = self.fx.node("s1"), self.fx.node("db")
        denied = self.fx.sim("OK", [self.fx.iv("IDN")])
        self.assertEqual((denied["trace"][1]["outcome"], denied["trace"][1]["httpStatus"], denied["trace"][1]["errorCode"]), ("Denied", 403, "IDENTITY_FAILURE"))
        self.assertEqual(denied["finalState"][self.fx.node("gw")], "Failed")             # a denial fails the SOURCE
        broken = self.fx.sim("OK", [self.fx.iv("RES")])
        entry = self.fx.entry(broken, "OK", 4)
        self.assertEqual((entry["outcome"], entry["httpStatus"], entry["errorCode"], broken["finalState"][db]), ("Failed", 503, "RESOURCE_EXHAUSTED", "Failed"))
        delayed = self.fx.sim("OK", [self.fx.iv("DLY-EDGE")])
        self.assertEqual({e["eventType"] for e in delayed["trace"]} & {"Timeout", "Failure"}, {"Timeout", "Failure"})
        cut = self.fx.sim("OK", [{"type": "disable_edge", "targetEdgeId": next(e["id"] for e in self.fx.model["edges"] if e["sourceId"] == s1 and e["targetId"] == self.fx.node("s2"))}])
        self.assertEqual((cut["trace"][2]["outcome"], code_of(cut["trace"][2])), ("Blocked", "edge_off"))
        faster = self.fx.sim("OK", [self.fx.iv("COMB")])
        self.assertIn("module_off", {code_of(e) for e in faster["trace"]})

    def test_no_difference_is_computed_not_decided_by_an_id_check(self):
        result, chain = self.fx.causal("OK", "DIS-FZ")
        self.assertEqual(result["diff"]["outcomeClass"], "NO_DIFFERENCE")
        self.assertEqual((chain["steps"], chain["effects"]), ([], []))
        self.assertTrue(chain["prediction"]["ok"])


class ElementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fx(ELEMENTS)

    def run_iv(self, case: str, iv: str):
        return self.fx.causal(case, iv)

    def test_a_validator_is_routed_through_hop_by_hop(self):
        result, chain = self.run_iv("ONE", "VAL")
        ids = [e["eventId"].split("#", 1)[1] for e in result["B"]["trace"]]
        self.assertEqual(ids[:5], ["1", "2#h1", "2#h2", "3#h1", "3#h2"])      # the Return travels the chain back, hop by hop
        self.assertTrue(result["B"]["trace"][1]["synthetic"] and result["B"]["trace"][2]["synthetic"])
        self.assertEqual(result["B"]["trace"][1]["elementState"], "IDLE")               # the machine state of the element, not the lifecycle
        self.assertEqual(result["diff"]["outcomeClass"], "CONVERGED")
        self.assertEqual(chain["root"]["element"]["between"], [self.fx.node("b"), self.fx.node("c")])

    def test_a_cache_answers_the_second_request_and_the_callee_is_not_invoked_again(self):
        result, _ = self.run_iv("TWO", "CACHE")
        causes = [(e["eventId"].split("#", 1)[1], code_of(e)) for e in result["B"]["trace"] if e["cause"]]
        self.assertIn(("4#h2", "element_answered"), causes)
        self.assertIn(("5#h1", "already_answered"), causes)   # the callee's scripted Return never happens
        self.assertEqual(result["B"]["finalState"][self.fx.node("a")], "Completed")

    def test_drop_and_fail_never_invoke_the_original_target(self):
        for iv, cause, lifecycle in (("DROP", "element_dropped", "Completed"), ("FAIL", "element_failed", "Failed")):
            result, _ = self.run_iv("ONE", iv)
            self.assertIn(cause, {code_of(e) for e in result["B"]["trace"]})
            el = next(iter(result["B"]["elementStates"]))
            self.assertEqual(result["B"]["finalState"][el], lifecycle)
            self.assertEqual(result["B"]["finalState"][self.fx.node("c")], "Idle")

    def test_a_retry_controller_overrides_the_retry_count_of_the_second_hop(self):
        result, _ = self.run_iv("SLOW", "RETRY")
        self.assertEqual(result["A"]["finalState"][self.fx.node("c")], "Failed")       # baseline: one attempt only
        self.assertEqual(result["B"]["finalState"][self.fx.node("c")], "Completed")
        self.assertIn("Retry", [e["eventType"] for e in result["B"]["trace"]])

    def test_keep_leaves_the_original_path_and_disable_blocks_it(self):
        keep, _ = self.run_iv("ONE", "KEEP")
        self.assertEqual(keep["diff"]["outcomeClass"], "NO_DIFFERENCE")
        off, _ = self.run_iv("ONE", "DISABLE")
        self.assertEqual(off["diff"]["outcomeClass"], "DIVERGED")
        self.assertIn(("Blocked", "edge_off"), {(e["outcome"], code_of(e)) for e in off["B"]["trace"]})

    def test_one_element_can_replace_two_edges(self):
        result, _ = self.run_iv("CHAIN", "TWOEDGES")
        ids = [e["eventId"].split("#", 1)[1] for e in result["B"]["trace"]]
        self.assertIn("2#h1", ids)
        self.assertIn("3#h1", ids)

    def test_a_draft_element_never_enters_the_computation(self):
        for iv in ("DRAFT", "SPARSE"):
            result, chain = self.run_iv("ONE", iv)
            self.assertEqual((result["status"], result["diff"], result["explanation"]), ("DRAFT", None, None))
            self.assertEqual((result["B"]["status"], result["B"]["trace"], chain["steps"], chain["rows"]), ("DRAFT", [], [], []))
            self.assertTrue(chain["prediction"]["ok"])                                    # expect.status DRAFT matches

    def test_incomplete_elements_report_what_is_missing(self):
        sparse = E.compile_deltas(self.fx.ix, [self.fx.iv("SPARSE")])
        self.assertEqual(sparse.status, "DRAFT")
        self.assertTrue(all(e.startswith("element ") for e in sparse.errors))
        self.assertIn(f"element {self.fx.iv('SPARSE')['element']['elementId']}: missing outputPorts", sparse.errors)

    def test_rewrite_checks(self):
        by_id = {r["id"]: r for r in ELEMENTS["runs"]}
        for run_id, snippet in (("compile-own-edge", "ownership edge"), ("compile-unknown-edge", "unknown"),
                                ("compile-bad-disposition", "needs disposition"), ("compile-one-edge-chain", "chain must have 2 edges"),
                                ("compile-gap-chain", "not contiguous"), ("compile-wrong-element", "does not pass through"),
                                ("compile-wrong-port-dir", "is not an out port"), ("compile-no-element-port", "element has no input port"),
                                ("compile-type-mismatch", "port type mismatch Message -> Record"),
                                ("compile-type-mismatch-out", "port type mismatch Record -> Message"),
                                ("compile-self-cycle", "cycle"), ("compile-no-rewrite", "not connected to any formal edge"),
                                ("compile-duplicate-element", "duplicate element"), ("compile-rewritten-twice", "rewritten twice"),
                                ("compile-node-collision", "collides with a model node"), ("compile-bad-kind", "not an allowed element kind"),
                                ("compile-bad-handler", "handler action explode not allowed")):
            out = execute(ELEMENTS["model"], by_id[run_id])
            # an incomplete or inconsistent element BODY is a draft (it is not computable but nothing else is wrong)
            # (an element with a complete body and no rewrite is a draft too: spec 4.9, deviation C8)
            body_problem = run_id in ("compile-node-collision", "compile-bad-kind", "compile-bad-handler", "compile-no-rewrite")
            self.assertEqual(out["status"], "DRAFT" if body_problem else "INVALID", run_id)
            self.assertTrue(any(snippet in e for e in out["errors"]), f"{run_id}: {out['errors']}")
        draft_and_error = execute(ELEMENTS["model"], by_id["compile-draft-plus-error"])
        self.assertEqual(draft_and_error["status"], "INVALID")                           # a real error beats the draft

    def test_the_snapshot_quirk_of_an_element_that_changes_a_snapshotted_entry_is_pinned(self):
        # An element that drops or fails a request changes its own state AFTER the hop entry was recorded; when that entry
        # closes a snapshot interval, seek() misses that change. The reference does this too (a candidate fifth fix for the
        # lead); the port keeps it so that the goldens stay equal.
        for iv, el_state in (("DROP", "Completed"), ("FAIL", "Failed")):
            fixture_run = next(r for r in ELEMENTS["runs"] if r["id"] == f"sim-quirk-{iv.lower()}")
            out = execute(ELEMENTS["model"], fixture_run)
            bad = [r["index"] for r in out["replay"] if r["seek"] != r["fold"]]
            self.assertEqual(len(out["run"]["trace"]), 6)
            self.assertEqual(bad, [3, 4, 5], iv)                      # stale from the snapshot at index 3 to the end of the trace
            el = next(iter(out["run"]["elementStates"]))
            at = {r["index"]: r for r in out["replay"]}
            self.assertNotEqual(at[3]["seek"][el], at[3]["fold"][el])
            self.assertEqual(at[3]["fold"][el], el_state)
            self.assertEqual(at[2]["seek"], at[2]["fold"])


class InvariantTests(unittest.TestCase):
    def all_runs(self):
        for path in fixture_paths():
            fixture = load_fixture(path)
            if fixture["model"] is None:
                continue
            for run in fixture["runs"]:
                if run["op"] in ("simulate", "causal"):
                    yield fixture, run

    def test_determinism_two_executions_are_identical(self):
        for fixture, run in self.all_runs():
            self.assertEqual(execute(fixture["model"], run), execute(fixture["model"], run), run["id"])

    def test_the_model_and_the_deltas_are_never_modified(self):
        for fixture in (CORE, ELEMENTS):
            before = copy.deepcopy(fixture["model"])
            for run in fixture["runs"]:
                if run["op"] in ("simulate", "causal", "compile"):
                    deltas, iv = resolve_run(fixture["model"], run)
                    d0, i0 = copy.deepcopy(deltas), copy.deepcopy(iv)
                    execute(fixture["model"], run)
                    self.assertEqual((deltas, iv), (d0, i0), run["id"])
            self.assertEqual(fixture["model"], before)

    def test_event_order_in_the_input_does_not_matter(self):
        for fixture in (CORE, ELEMENTS):
            for mode in ("reverse", "rotate"):
                shuffled = shuffle_model(fixture["model"], mode)
                for run in fixture["runs"]:
                    if run["op"] not in ("simulate", "causal") or run.get("shuffle"):
                        continue
                    plain, mixed = execute(fixture["model"], run), execute(shuffled, run)
                    for out in (plain, mixed):
                        for part in (out.get("run"), (out.get("result") or {}).get("A"), (out.get("result") or {}).get("B")):
                            if part:
                                part.pop("modelVersion", None)
                                part.pop("hash", None)
                        for part in (out.get("result") or {}, out.get("chain") or {}):
                            (part.get("explanation") or {}).get("baselineConditions", {}).pop("modelVersion", None)
                    self.assertEqual(plain, mixed, f"{fixture['name']}::{run['id']} {mode}")

    def test_the_run_version_is_the_model_stamp_and_changes_with_the_model(self):
        fx = Fx(CORE)
        run = fx.sim("OK")
        self.assertEqual(run["modelVersion"], am.model_sha8(fx.model))
        other = copy.deepcopy(fx.model)
        other["nodes"][0]["name"] += "!"
        self.assertNotEqual(E.simulate(other, other["cases"][0], [])["modelVersion"], run["modelVersion"])

    def test_snapshot_seek_equals_the_fold_at_every_index(self):
        checked = 0
        for fixture, run in self.all_runs():
            if run["op"] != "simulate" or not run.get("replay") or "seek-quirk" in run.get("tags", []):
                continue
            out = execute(fixture["model"], run)
            for r in out.get("replay", []):
                self.assertEqual(r["seek"], r["fold"], f"{run['id']} at {r['index']}")
                checked += 1
        self.assertGreater(checked, 100)

    def test_seek_rejects_an_index_out_of_range(self):
        run = Fx(CORE).sim("OK")
        for bad in (-2, len(run["trace"])):
            with self.assertRaises(IndexError):
                E.seek(run, bad)

    def test_every_trace_entry_is_well_formed_and_the_whole_result_is_strict_json(self):
        vocab_profile = vocab.default_profile()
        outcomes = set(vocab_profile.ids("eventOutcomes"))
        types = set(vocab_profile.ids("eventTypes")) | {"Timeout", "Retry"}
        keys = None
        for fixture, run in self.all_runs():
            out = execute(fixture["model"], run)
            json.dumps(out, allow_nan=False)
            traces = [out["run"]["trace"]] if "run" in out else [out["result"]["A"]["trace"], out["result"]["B"]["trace"]] if "result" in out else []
            for trace in traces:
                for i, e in enumerate(trace):
                    self.assertEqual((e["index"], e["sequence"]), (i, i + 1))
                    self.assertIn(e["outcome"], outcomes)
                    self.assertIn(e["eventType"], types)
                    keys = keys or set(e)
                    self.assertEqual(set(e) - {"elementState"}, keys - {"elementState"})
                    self.assertTrue(e["cause"] is None or set(e["cause"]) == {"code", "args"})
        self.assertEqual(len(keys), 21)

    def test_unbound_never_occurs_in_a_case_that_binds_every_event(self):
        run = Fx(CORE).sim("OK")
        self.assertNotIn("Unbound", {e["outcome"] for e in run["trace"]})

    def test_engine_constants_agree_with_the_profile(self):
        p = vocab.default_profile()
        self.assertEqual(engine_types.ACTIVE_STATES, p.active_states)
        self.assertEqual(engine_types.DELIVERY, p.delivery_types)
        self.assertEqual(engine_types.HANDLER_ACTIONS, p.enums["handlerActions"])
        self.assertEqual(set(engine_causal.OUTCOME_CLASSES) - {"DRAFT", "REJECTED"}, set(p.ids("outcomeClasses")))
        self.assertEqual(set(engine_causal.LOCALES), set(p.locales))

    def test_the_engine_reads_no_clock_no_randomness_and_does_no_io(self):
        fx = Fx(CORE)
        boom = mock.Mock(side_effect=AssertionError("forbidden call"))
        with mock.patch.object(time, "time", boom), mock.patch.object(time, "monotonic", boom), mock.patch.object(time, "perf_counter", boom), \
                mock.patch.object(random, "random", boom), mock.patch.object(builtins, "open", boom):
            fx.sim("OK")
            fx.sim("RETRY")
            E.run_causal(fx.model, fx.case("OK")["caseId"], [fx.iv("COMB")], {"index": fx.ix})
        self.assertFalse(boom.called)

    def test_engine_sources_use_only_the_standard_library_and_no_dynamic_code(self):
        allowed = {"__future__", "json", "math", "re", "dataclasses", "typing", "collections", "hashlib", "copy", "itertools", "pathlib",
                   "functools"}
        for path in sorted(ENGINE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed, f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    self.assertIn((node.module or "").split(".")[0], allowed, f"{path.name}: from {node.module}")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "compile", "__import__", "open"}, path.name)
            self.assertNotIn("\r", path.read_text(encoding="utf-8", errors="replace").replace("\r\n", ""), path.name)


class GuardTests(unittest.TestCase):
    def test_the_interpreter_is_closed_and_follows_strict_equality(self):
        g = E.eval_guard
        self.assertTrue(g(None))
        self.assertFalse(g({"param": "p", "op": "unknown", "value": 1}, {"params": {"p": 1}}))
        self.assertFalse(g({"param": "p", "op": "eq", "value": 1}, {"params": {"p": True}}))      # true !== 1
        self.assertFalse(g({"param": "p", "op": "eq", "value": 1}, {"params": {"p": "1"}}))
        self.assertTrue(g({"param": "p", "op": "neq", "value": 1}, {"params": {"p": True}}))
        self.assertFalse(g({"param": "p", "op": "in", "value": [1]}, {"params": {"p": True}}))
        self.assertTrue(g({"param": "p", "op": "in", "value": ["a", "b"]}, {"params": {"p": "b"}}))
        self.assertFalse(g({"param": "p", "op": "in", "value": "ab"}, {"params": {"p": "a"}}))   # only an array holds members

    def test_present_and_absent_use_the_string_form(self):
        present = {"param": "p", "op": "present"}
        for value, expected in (("x", True), ("", False), (0, True), (False, True), ([], False), ([0], True), (None, False)):
            self.assertEqual(E.eval_guard(present, {"params": {"p": value}}), expected, repr(value))
        self.assertFalse(E.eval_guard(present, {"params": {}}))
        self.assertTrue(E.eval_guard({"param": "p", "op": "absent"}, {"params": {}}))

    def test_ctx_references_and_combinators(self):
        eq = {"param": "p", "op": "eq", "value": "$ctx.k"}
        self.assertTrue(E.eval_guard(eq, {"params": {"p": "v"}, "context": {"k": "v"}}))
        self.assertFalse(E.eval_guard(eq, {"params": {"p": "v"}, "context": {}}))
        self.assertTrue(E.eval_guard({"param": "p", "op": "eq"}, {"params": {}}))                  # undefined === undefined
        self.assertTrue(E.eval_guard({"all": []}))                                                  # an empty array is truthy
        self.assertTrue(E.eval_guard({"any": []}) is False)
        self.assertTrue(E.eval_guard({"not": {"param": "p", "op": "present"}}, {"params": {}}))

    def test_when_forms(self):
        state = {"a": "Running", "b": "Idle"}
        w = lambda form, **ctx: E.eval_when(form, {"state": state, **ctx})  # noqa: E731
        self.assertTrue(w({"node": "a", "is": ["Running"]}))
        self.assertTrue(w({"node": "b", "not_in": ["Running"]}))
        self.assertTrue(w({"node": "a"}))
        self.assertFalse(w({"node": "zz", "is": ["Idle"]}))
        self.assertTrue(w({"elementState": "BUSY"}, elementState="BUSY"))
        self.assertFalse(w({"elementState": "BUSY"}))
        self.assertTrue(w({"param": "p", "op": "eq", "value": 1}, params={"p": 1}))
        self.assertFalse(w({}))
        self.assertEqual(E.normalize_when({"node": "a", "not": ["Idle"]}), {"node": "a", "not_in": ["Idle"]})
        self.assertEqual(E.normalize_when({"not": {"node": "a", "not": ["Idle"]}}), {"not": {"node": "a", "not_in": ["Idle"]}})
        source = {"all": [{"node": "a", "not": ["Idle"]}]}
        E.normalize_when(source)
        self.assertEqual(source, {"all": [{"node": "a", "not": ["Idle"]}]})                        # the input is not modified

    def test_fnv32_known_values_and_utf16_units(self):
        self.assertEqual(E.fnv32(""), "811c9dc5")
        self.assertEqual(E.fnv32("a"), "e40c292c")
        self.assertEqual(E.fnv32("foobar"), "bf9cf968")
        self.assertNotEqual(E.fnv32("\U0001F600"), E.fnv32("�"))
        self.assertEqual(E.canonical({"b": [1, None], "a": 2.0}), '{"a":2,"b":[1,null]}')


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fx(CORE)

    def test_errors_use_the_documented_messages_and_status(self):
        ix = self.fx.ix
        cases = [
            ([{"type": "disable_module", "targetNodeId": "q"}], "disable_module: unknown node q"),
            ([{"type": "identity_failure"}], "identity_failure: unknown node undefined"),
            ([{"type": "disable_edge", "targetEdgeId": "e"}], "disable_edge: unknown edge e"),
            ([{"type": "change_timeout", "timeoutMs": 0}], "change_timeout: timeoutMs must be > 0"),
            ([{"type": "change_timeout", "timeoutMs": "5"}], "change_timeout: timeoutMs must be > 0"),
            ([{"type": "change_retry", "retryMax": -1}], "change_retry: retryMax must be >= 0"),
            ([{"type": "delay_message", "addLatencyMs": 5}], "delay_message: needs addLatencyMs and a target"),
            ([{"type": "delay_message", "addLatencyMs": 5, "targetEdgeId": "e"}], "delay_message: unknown edge e"),
            ([{"type": "change_guard", "machineId": "m", "transitionId": "t"}], "change_guard: unknown transition t"),
            ([{"type": "nope"}], "unknown intervention type nope"),
            (["text"], "unknown intervention type undefined"),
        ]
        for deltas, message in cases:
            c = E.compile_deltas(ix, deltas)
            self.assertEqual((c.status, c.errors), ("INVALID", [message]), message)
            run = E.simulate(self.fx.model, self.fx.case("OK"), deltas, {"index": ix})
            self.assertEqual((run["status"], run["errors"], run["trace"], run["hash"]), ("REJECTED", [message], [], None))

    def test_compiled_effects_and_combined_flattening(self):
        c = E.compile_deltas(self.fx.ix, [self.fx.iv("COMB"), {"type": "change_retry", "retryMax": 2}])
        self.assertEqual(c.status, "OK")
        self.assertEqual([d["type"] for d in c.flat], ["disable_module", "change_timeout", "delay_message", "change_retry"])
        self.assertEqual(c.policy, {"timeoutMs": 5, "retryMax": 2})
        self.assertEqual(c.disabled_nodes, {self.fx.node("cache")})
        self.assertEqual(c.to_dict()["disabledNodes"], [self.fx.node("cache")])
        json.dumps(c.to_dict())

    def test_a_combined_record_the_contract_rejects_is_invalid_not_silently_empty(self):
        for record in ({"type": "combined_intervention", "parts": []}, {"type": "combined_intervention"}, {"type": "combined_intervention", "parts": ["x"]}):
            c = E.compile_deltas(self.fx.ix, [record])
            self.assertEqual(c.status, "INVALID")
            self.assertTrue(c.errors[0].startswith("combined_intervention: "), c.errors)
        deep = {"type": "disable_module", "targetNodeId": self.fx.node("cache")}
        for _ in range(am.MAX_PART_DEPTH + 1):
            deep = {"type": "combined_intervention", "parts": [deep]}
        self.assertEqual(E.compile_deltas(self.fx.ix, [deep]).status, "INVALID")

    def test_run_causal_raises_for_an_unknown_case(self):
        with self.assertRaises(E.AtlasEngineError) as caught:
            E.run_causal(self.fx.model, "NO-SUCH", [])
        self.assertEqual((caught.exception.code, caught.exception.message), ("GEMARC_ATLAS_ENGINE_001", "unknown case NO-SUCH"))


class CompareAndChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fx(CORE)

    def test_identical_runs_are_no_difference_and_unequal_lengths_find_the_divergence(self):
        a = self.fx.sim("OK")
        self.assertEqual(E.compare_runs(a, a)["outcomeClass"], "NO_DIFFERENCE")
        shorter = copy.deepcopy(a)
        shorter["trace"] = shorter["trace"][:3]
        diff = E.compare_runs(a, shorter)
        self.assertEqual(diff["firstDivergence"]["index"], 3)
        self.assertIsNone(diff["firstDivergence"]["b"])

    def test_outcome_classes_and_evidence_lists(self):
        result, chain = self.fx.causal("OK", "DIS-S2")
        diff = result["diff"]
        self.assertEqual(diff["outcomeClass"], "DIVERGED")
        self.assertEqual(diff["terminalDiff"], [{"nodeId": self.fx.node("s2"), "a": "Completed", "b": "Blocked"},
                                                {"nodeId": self.fx.node("db"), "a": "Completed", "b": "Idle"}])
        self.assertEqual(diff["firstDivergence"]["index"], 2)
        # events 3..6 touch the disabled module: the target of each entry is s2, the store, s2 and s1 (a Return into s1)
        self.assertEqual([x["nodeId"] for x in diff["blocking"]], [self.fx.node("s2"), self.fx.node("db"), self.fx.node("s2"), self.fx.node("s1")])
        self.assertEqual(diff["epistemic"], "model-defined")
        self.assertEqual(chain["steps"][0]["role"], "block")
        self.assertEqual([p for s in chain["steps"] for p in s["parents"]][:1], ["root"])
        self.assertEqual(chain["confidence"]["weakest"], "Implemented")

    def test_retries_and_recovery_are_reported(self):
        result, _ = self.fx.causal("OK", "RETRY")
        self.assertEqual(result["diff"]["outcomeClass"], "NO_DIFFERENCE")
        result, chain = self.fx.causal("OK", "TO")
        self.assertEqual(result["diff"]["outcomeClass"], "DIVERGED")
        self.assertIn("timeout", chain["counts"])
        self.assertIn("fail", chain["counts"])

    def test_the_prediction_check_reports_every_mismatch(self):
        result, chain = self.fx.causal("OK", "WRONG")
        p = chain["prediction"]
        self.assertFalse(p["ok"])
        self.assertEqual([m["code"] for m in p["mismatches"]], ["status", "outcomeClass", "terminal"])
        self.assertEqual(p["mismatches"][0]["args"], {"expected": "DRAFT", "got": "OK"})
        self.assertEqual(p["mismatches"][2]["args"], {"nodeId": self.fx.node("s2"), "expected": "Completed", "got": "Blocked"})
        self.assertIsNone(E.check_prediction({"type": "x"}, result))
        self.assertIsNone(E.check_prediction(None, result))
        unknown = E.check_prediction({"expect": {"terminal": {"zz": "Idle"}}}, result)
        self.assertEqual(unknown["mismatches"][0]["args"]["got"], None)

    def test_rejected_and_draft_chains_have_no_steps_and_an_honest_headline(self):
        bad = {"type": "disable_module", "targetNodeId": "zz"}
        result = E.run_causal(self.fx.model, self.fx.case("OK")["caseId"], [bad], {"index": self.fx.ix})
        self.assertEqual((result["status"], result["diff"], result["B"]["status"]), ("REJECTED", None, "REJECTED"))
        chain = E.causal_chain(self.fx.model, result, {"description": "x", "expect": {"status": "REJECTED"}}, self.fx.ix)
        self.assertEqual((chain["status"], chain["outcomeClass"], chain["steps"], chain["confidence"]), ("REJECTED", "REJECTED", [], None))
        self.assertIn("disable_module: unknown node zz", chain["headline"])
        self.assertTrue(chain["prediction"]["ok"])

    def test_chain_steps_link_back_to_the_cause_through_earlier_steps(self):
        for run in CORE["runs"]:
            if run["op"] != "causal" or "intervention" not in run:
                continue
            out = execute(CORE["model"], run)
            if out.get("thrown") or out["chain"]["status"] != "OK":
                continue
            chain = out["chain"]
            seen = {"root"}
            for step in chain["steps"]:
                self.assertTrue(step["parents"], run["id"])
                self.assertEqual(len(step["parents"]) <= len(step["via"]), True)
                self.assertTrue(set(step["parents"]) <= seen | {step["id"]}, f"{run['id']} {step['id']} {step['parents']}")
                seen.add(step["id"])
            for effect in chain["effects"]:
                self.assertTrue(set(effect["parents"]) <= seen, run["id"])

    def test_alignment_covers_both_traces_exactly_once(self):
        for run in CORE["runs"]:
            if run["op"] != "causal" or "intervention" not in run:
                continue
            out = execute(CORE["model"], run)
            if out.get("thrown") or out["chain"]["status"] != "OK":
                continue
            rows, a, b = out["chain"]["rows"], out["result"]["A"]["trace"], out["result"]["B"]["trace"]
            self.assertEqual(sorted(r["a"] for r in rows if r["a"] is not None), list(range(len(a))), run["id"])
            self.assertEqual(sorted(r["b"] for r in rows if r["b"] is not None), list(range(len(b))), run["id"])

    def test_inserted_elements_are_located_between_the_replaced_edge_ends(self):
        fx = Fx(ELEMENTS)
        result, chain = fx.causal("ONE", "VAL")
        element = chain["root"]["element"]
        self.assertEqual(element["kind"], "Validator")
        self.assertEqual([e["id"].rsplit(":", 1)[1] for e in element["edges"]], ["1", "2"])
        self.assertEqual(chain["root"]["facts"][0]["code"], "element-inserted")
        for effect in chain["effects"]:
            if effect["nodeId"] == element["id"]:
                self.assertEqual(effect["name"], element["name"])


class SemanticsDetailTests(unittest.TestCase):
    """Hand-derived details that the fixtures alone do not pin (found by a mutation check of the port)."""

    def setUp(self):
        self.core = Fx(CORE)
        self.elements = Fx(ELEMENTS)

    @staticmethod
    def with_events(case: dict, events: list, **policy) -> dict:
        out = copy.deepcopy(case)
        out["events"] = [{"seq": i, **e} for i, e in enumerate(events, start=1)]
        out["policy"] = {**out["policy"], **policy}
        return out

    def test_success_on_the_second_attempt_is_a_retry_entry_with_the_backed_off_clock(self):
        fx = self.core
        a, gw, s1 = fx.node("u1"), fx.node("gw"), fx.node("s1")
        case = self.with_events(fx.case("OK"), [
            {"type": "Call", "from": a, "to": gw},
            {"type": "Call", "from": gw, "to": s1, "latencyMs": [300, 20]},
        ], timeoutMs=100, retryMax=1, retryBackoffMs=10)
        run = E.simulate(fx.model, case, [], {"index": fx.ix})
        rows = [(e["eventId"].split("#", 1)[1], e["eventType"], e["outcome"], e["logicalTime"], e["attempt"]) for e in run["trace"]]
        self.assertEqual(rows, [("1", "Call", "OK", 0, 1), ("2@1", "Timeout", "Timeout", 100, 1), ("2@2", "Retry", "OK", 130, 2)])

    def test_the_last_latency_of_an_array_is_reused_for_later_attempts(self):
        fx = self.core
        a, gw = fx.node("u1"), fx.node("gw")
        case = self.with_events(fx.case("OK"), [{"type": "Call", "from": a, "to": gw, "latencyMs": [300, 400]}], timeoutMs=100, retryMax=2, retryBackoffMs=0)
        run = E.simulate(fx.model, case, [], {"index": fx.ix})
        seen = [e["cause"]["args"]["latencyMs"] for e in run["trace"] if e["eventType"] == "Timeout"]
        self.assertEqual(seen, [300, 400, 400])
        self.assertEqual(run["trace"][-1]["eventType"], "Failure")

    def test_a_starved_section_return_still_marks_the_section_as_lost(self):
        fx = self.core
        a, gw, agg, p1, p2 = (fx.node(x) for x in ("u1", "gw", "agg", "p1", "p2"))
        case = self.with_events(fx.case("AGGOK"), [
            {"type": "Call", "from": a, "to": gw}, {"type": "Call", "from": gw, "to": agg},
            {"type": "Call", "from": agg, "to": p1, "section": True}, {"type": "Return", "from": p1, "to": agg, "section": True},
            {"type": "Return", "from": p2, "to": agg, "section": True}, {"type": "Return", "from": agg, "to": gw},
        ])
        run = E.simulate(fx.model, case, [], {"index": fx.ix})
        self.assertEqual((run["trace"][4]["outcome"], code_of(run["trace"][4])), ("Starved", "no_open_request"))
        self.assertEqual(run["trace"][5]["cause"], {"code": "sections_missing", "args": {"sections": [p2]}})
        self.assertEqual(run["finalState"][agg], "Degraded")

    def test_a_return_travels_back_along_the_edge_of_its_request(self):
        fx = self.core
        model = copy.deepcopy(fx.model)
        a, gw = fx.node("u1"), fx.node("gw")
        extra = am.build_edge(gw, "calls", a, "opposite direction")
        model["edges"].append(extra)
        case = next(c for c in model["cases"] if c["caseId"].endswith("-OK"))
        run = E.simulate(model, case, [])
        request, ret = run["trace"][0], run["trace"][-1]
        self.assertEqual(request["edgeId"], f"{a}:calls:{gw}")
        self.assertEqual((ret["eventType"], ret["edgeId"]), ("Return", request["edgeId"]))      # not the edge gw -> user
        self.assertNotEqual(ret["edgeId"], extra["id"])

    def test_an_element_answers_each_request_once_and_the_counter_drops(self):
        fx = self.elements
        two = copy.deepcopy(fx.case("TWO"))
        b, c = fx.node("b"), fx.node("c")
        two["events"].append({"seq": 7, "type": "Return", "from": c, "to": b})
        result = E.simulate(fx.model, two, [fx.iv("CACHE")], {"index": fx.ix})
        by_id = {e["eventId"].split("#", 1)[1]: e for e in result["trace"]}
        self.assertEqual(code_of(by_id["5#h1"]), "already_answered")           # the answered request's own Return is skipped once
        self.assertEqual((by_id["7#h1"]["outcome"], code_of(by_id["7#h1"])), ("Starved", "no_open_request"))   # ... and only once

    def test_change_retry_carries_its_backoff_into_the_policy_and_the_clock(self):
        fx = self.core
        compiled = E.compile_deltas(fx.ix, [{"type": "change_retry", "retryMax": 2, "retryBackoffMs": 50}])
        self.assertEqual(compiled.policy, {"retryMax": 2, "retryBackoffMs": 50})
        slow = [e for e in fx.sim("RETRY", [{"type": "change_retry", "retryMax": 2, "retryBackoffMs": 50}])["trace"] if e["eventType"] == "Timeout"]
        self.assertEqual([e["logicalTime"] for e in slow][:2], [100, 250])     # 100, +50 backoff, +100

    def test_only_successful_recovery_entries_count_as_recovery(self):
        def entry(index, outcome, role):
            return {"index": index, "eventId": f"C#{index + 1}", "sourceNodeId": "n", "targetNodeId": "n", "edgeId": None, "eventType": "Recovery",
                    "outcome": outcome, "nextState": "Recovered", "errorCode": None, "cause": None, "attempt": None, "role": role, "logicalTime": 0,
                    "stateChanges": []}
        base = entry(0, "OK", None)
        A = {"trace": [base], "finalState": {"n": "Idle"}}
        B = {"trace": [base, entry(1, "Starved", "recovery"), entry(2, "OK", "recovery")], "finalState": {"n": "Completed"}}
        diff = E.compare_runs(A, B)
        self.assertEqual((diff["recovery"], diff["recovered"]), ([2], True))


class DeviationTests(unittest.TestCase):
    """Every recorded deviation from the reference is exercised by a test."""

    @classmethod
    def setUpClass(cls):
        cls.deviations = json.loads(DEVIATIONS_FILE.read_text(encoding="utf-8"))
        cls.golden = load_golden()

    def test_the_deviation_list_is_well_formed_and_every_entry_names_its_test_and_its_evidence(self):
        ids = [d["id"] for d in self.deviations["deviations"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([d["id"] for d in self.deviations["deviations"] if d["category"] == "recorded"], ["D1", "D2", "D3", "D4"])
        self.assertEqual({d["category"] for d in self.deviations["deviations"]}, {"recorded", "contract", "preserved", "hardening"})
        for d in self.deviations["deviations"]:
            self.assertTrue(d["description"] and d["reference"] and d["port"], d["id"])
            cls_name, _, method = d["test"].partition(".")
            self.assertTrue(callable(getattr(globals().get(cls_name), method, None)), f"{d['id']}: no test {d['test']}")
            for key in d.get("evidence", []):
                self.assertTrue(key in self.golden["diverged"] or key in self.golden["shared"], f"{d['id']}: {key} is not a golden entry")

    def test_d1_suffixed_event_ids_pair_by_step_and_attempt(self):
        entry = self.golden["diverged"]["align-vectors::suffix-timeout-to-ok"]
        self.assertEqual(entry["fix"], [1])
        rows = entry["rows"]
        kinds = [r["kind"] for r in rows]
        # A has #2@1 #2@2 #2! (attempts 1, 2, 3) and B one OK #2 (attempt 1): the same-attempt entry pairs as `changed`
        self.assertEqual(kinds, ["same", "changed", "aOnly", "aOnly", "changed"])
        self.assertEqual(rows[1], {"kind": "changed", "a": 1, "b": 1})
        self.assertEqual(sorted(r["a"] for r in rows if r["a"] is not None), [0, 1, 2, 3, 4])
        # reviewed by hand: the Starved #3 of A faces the OK #3 of B and pairs with it too, after the two A-only attempts
        self.assertEqual(rows[-1], {"kind": "changed", "a": 4, "b": 2})
        self.assertEqual([r["a"] for r in rows[2:4]], [2, 3])
        # the sort key of every row is a number (the reference produced NaN for these ids)
        for fixture_run in load_fixture(FIXTURE_DIR / "align-vectors.json")["runs"]:
            for row in execute(None, fixture_run)["rows"]:
                self.assertIn(row["kind"], ("same", "changed", "aOnly", "bOnly"))
        self.assertEqual(engine_causal.event_seq({"eventId": "C#12@3"}), 12)
        self.assertEqual(engine_causal.event_seq({"eventId": "C#6!"}), 6)
        self.assertEqual(engine_causal.event_seq({"eventId": "C#6#h1@2"}), 6)

    def test_d1_attempt_preference_and_unsuffixed_ids_behave_like_the_reference(self):
        def rec(eid, outcome, attempt=None):
            return {"eventId": eid, "sourceNodeId": "a", "targetNodeId": "b", "edgeId": "e", "eventType": "Call", "outcome": outcome,
                    "nextState": "Running", "errorCode": None, "attempt": attempt}
        A = {"trace": [rec("C#1@1", "Timeout", 1), rec("C#1@2", "Timeout", 2), rec("C#1@3", "Timeout", 3)]}
        B = {"trace": [rec("C#1@2", "OK", 2)]}
        rows = E.align_traces(A, B)
        # the B entry pairs with the A entry of the same attempt (index 1), not with the first one; A-only rows follow it
        self.assertEqual([(r["kind"], r["a"], r["b"]) for r in rows], [("changed", 1, 0), ("aOnly", 0, None), ("aOnly", 2, None)])
        plain_a = {"trace": [rec("C#1", "OK", 1), rec("C#2", "Blocked")]}
        plain_b = {"trace": [rec("C#1", "OK", 1), rec("C#2", "OK", 1)]}
        self.assertEqual([r["kind"] for r in E.align_traces(plain_a, plain_b)], ["same", "changed"])

    def test_d2_cause_is_structured_and_every_code_has_text_in_every_locale(self):
        codes = engine_types.CAUSE_CODES
        self.assertEqual(len(codes), len(set(codes)))
        for locale in engine_causal.LOCALES:
            table = engine_causal._TEXT[locale]["cause"]
            self.assertEqual(set(table), set(codes), locale)
            for code in codes:
                args = {"nodeId": "n", "state": "Idle", "transitionId": "t", "latencyMs": 5, "timeoutMs": 2, "sections": ["x", "y"], "eventType": "Call"}
                text = E.cause_text({"code": code, "args": args}, locale)
                self.assertTrue(text and text != code, f"{locale}/{code}")
                self.assertNotIn("{", text, f"{locale}/{code}")
        self.assertEqual(E.cause_text({"code": "unknown_code", "args": {}}), "unknown_code")        # unknown codes pass through
        self.assertEqual(E.cause_text(None), "")
        self.assertEqual(E.cause_text({"code": "module_off", "args": {"nodeId": "svc"}}, "xx-XX"), "svc is unavailable")  # fallback: en-US
        fx = Fx(CORE)
        result = E.run_causal(fx.model, fx.case("OK")["caseId"], [fx.iv("DIS-S2")], {"index": fx.ix})
        for entry in result["B"]["trace"]:
            self.assertTrue(entry["cause"] is None or isinstance(entry["cause"]["args"], dict))
            self.assertNotIsInstance(entry["cause"], str)

    def test_d2_the_text_tables_have_the_same_shape_in_every_locale(self):
        tables = engine_causal._TEXT
        base = tables["en-US"]

        def shape(value):
            return {k: shape(v) for k, v in value.items()} if isinstance(value, dict) else type(value).__name__

        for locale, table in tables.items():
            self.assertEqual(shape(table), shape(base), locale)
        for code in engine_causal.ROLE:
            for locale in tables:
                self.assertTrue(tables[locale]["role"][code])
        fx = Fx(CORE)
        result, chain = fx.causal("OK", "DIS-S2")
        for locale in tables:
            lines = E.chain_lines(E.causal_chain(fx.model, result, fx.iv("DIS-S2"), fx.ix, locale), locale)
            self.assertGreaterEqual(len(lines), 5)
            self.assertTrue(all(isinstance(x, str) and x for x in lines))
        zh = E.chain_lines(E.causal_chain(fx.model, result, fx.iv("DIS-S2"), fx.ix, "zh-CN"), "zh-CN")
        self.assertTrue(any(re.search(r"[一-鿿]", line) for line in zh))
        self.assertIn(chain["steps"][0]["why"], E.chain_lines(chain)[3])

    def test_d3_no_seed_anywhere(self):
        fx = Fx(CORE)
        result = E.run_causal(fx.model, fx.case("OK")["caseId"], [fx.iv("DIS-S2")], {"index": fx.ix})

        def has_seed(value):
            if isinstance(value, dict):
                return "seed" in value or any(has_seed(v) for v in value.values())
            if isinstance(value, list):
                return any(has_seed(v) for v in value)
            return False

        self.assertFalse(has_seed(result))
        self.assertFalse(has_seed(fx.sim("OK")))
        self.assertNotIn("seed", E.simulate.__code__.co_varnames)

    def test_d4_one_default_policy_constant(self):
        self.assertIs(engine_types.DEFAULT_POLICY, am.DEFAULT_POLICY)
        for path in sorted(ENGINE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"\b2000\b", source), [], f"{path.name}: a second copy of the timeout default")
            self.assertNotIn("timeoutMs\": 2000", source)
        fx = Fx(CORE)
        case = copy.deepcopy(fx.case("OK"))
        del case["policy"]
        case["events"][1]["latencyMs"] = 2001
        run = E.simulate(fx.model, case, [], {"index": fx.ix})
        self.assertEqual(run["trace"][1]["cause"]["args"], {"latencyMs": 2001, "timeoutMs": 2000})
        result, chain = fx.causal("OK", "TO")
        self.assertEqual(chain["root"]["facts"], [{"code": "timeout-change", "args": {"from": 2000, "to": 15}}])

    def test_c1_keep_and_disable_carry_no_chain_and_only_replace_is_chain_checked(self):
        fx = Fx(ELEMENTS)
        keep = fx.iv("KEEP")
        self.assertEqual(keep["rewrite"][0]["chain"], [])
        self.assertEqual(E.compile_deltas(fx.ix, [keep]).status, "OK")
        # a keep entry whose element ports could never fit the edge is still fine: the chain is never checked
        el = copy.deepcopy(keep["element"])
        el["inputPorts"][0]["type"] = "Record"
        mismatched = {**keep, "element": el}
        self.assertEqual(E.compile_deltas(fx.ix, [mismatched]).status, "OK")
        replace = copy.deepcopy(fx.iv("VAL"))
        replace["element"]["inputPorts"][0]["type"] = "Record"
        self.assertEqual(E.compile_deltas(fx.ix, [replace]).status, "INVALID")
        # ... but the edge must exist and must not be an owns edge
        own = next(e for e in fx.model["edges"] if e["relation"] == "owns")
        bad = {**keep, "rewrite": [{"replaceEdgeId": own["id"], "disposition": "keep", "chain": []}]}
        self.assertTrue(any("ownership" in e for e in E.compile_deltas(fx.ix, [bad]).errors))
        # keep entries still count as a rewrite of that edge
        both = [keep, fx.iv("VAL")]
        self.assertIn("rewritten twice", " ".join(E.compile_deltas(fx.ix, both).errors))

    def test_c2_element_kinds_are_the_profile_ids(self):
        fx = Fx(ELEMENTS)
        spaced = copy.deepcopy(fx.iv("VAL"))
        spaced["element"]["kind"] = "Validator"
        self.assertEqual(E.compile_deltas(fx.ix, [spaced]).status, "OK")
        for wrong in ("New Service", "validator", "Teleporter", None):
            spaced["element"]["kind"] = wrong
            c = E.compile_deltas(fx.ix, [spaced])
            self.assertEqual(c.status, "DRAFT")
            self.assertIn(f"kind {wrong if wrong else 'null'} is not an allowed element kind", " ".join(c.errors))

    def test_c3_the_static_flag_of_an_edge_decides_routing(self):
        fx = Fx(CORE)
        model = copy.deepcopy(fx.model)
        case = next(c for c in model["cases"] if c["caseId"].endswith("-UNBOUND"))
        gw, db = fx.node("gw"), fx.node("db")
        outcome = lambda: E.simulate(model, case, [])["trace"][1]["outcome"]  # noqa: E731
        self.assertEqual(outcome(), "Unbound")                                                  # no edge between gw and the store
        model["edges"].append(am.build_edge(gw, "calls", db, "helper edge"))
        self.assertNotEqual(outcome(), "Unbound")                                               # a runtime edge routes the event
        model["edges"][-1]["static"] = True
        self.assertEqual(outcome(), "Unbound")                                                  # static: never carries events
        model["edges"][-1].update({"static": False, "sourceId": db, "targetId": gw})
        self.assertNotEqual(outcome(), "Unbound")                                               # the reverse direction routes too

    def test_c4_grades_are_compared_by_the_contract_rank(self):
        self.assertEqual(engine_causal.weakest(["Implemented", "Observed"]), "Observed")
        self.assertEqual(engine_causal.weakest(["Observed", "Designed"]), "Designed")
        self.assertEqual(engine_causal.weakest([]), "Unknown")
        runs = {r["id"]: r for r in CORE["runs"]}
        for key in ("core::iv-idn", "core::iv-retry-heals", "core::iv-dis-gw-starve"):
            entry = self.golden["diverged"][key]["confidenceWeakest"]
            self.assertEqual(entry, {"reviewed": "Observed", "reference": "Implemented"}, key)
            out = execute(CORE["model"], runs[key.split("::", 1)[1]])
            self.assertEqual(out["chain"]["confidence"]["weakest"], "Observed", key)       # the engine itself, not only the file

    def test_c5_a_malformed_combined_record_is_an_error(self):
        ix = Fx(CORE).ix
        for record in ({"type": "combined_intervention", "parts": []}, {"type": "combined_intervention"}):
            compiled = E.compile_deltas(ix, [record])
            self.assertEqual(compiled.status, "INVALID")
            self.assertTrue(compiled.errors[0].startswith("combined_intervention: "))

    def test_c7_the_version_stamp_is_the_model_hash_and_there_is_no_revision_field(self):
        fx = Fx(CORE)
        run = fx.sim("OK")
        self.assertEqual(run["modelVersion"], am.model_sha8(fx.model))
        self.assertNotIn("scenario" + "Revision", run)
        result = E.run_causal(fx.model, fx.case("OK")["caseId"], [fx.iv("DIS-S2")], {"index": fx.ix})
        self.assertEqual(result["explanation"]["baselineConditions"]["modelVersion"], run["modelVersion"])

    def test_c6_text_rendering_constants(self):
        self.assertEqual(engine_causal.EPISTEMIC, "model-defined")
        fx = Fx(ELEMENTS)
        _, chain = fx.causal("ONE", "VAL")
        self.assertEqual(chain["root"]["element"]["name"], f"Validator ({fx.iv('VAL')['element']['role']})")


    def test_c8_an_unconnected_complete_element_is_a_draft(self):
        fx = Fx(ELEMENTS)
        ix = fx.ix
        draft = {"type": "insert_element", "element": {"elementId": "fp4.x", "kind": "Cache"}, "rewrite": []}
        done = engine_elements.complete_deltas(fx.model, [draft])
        self.assertEqual(done[0]["element"]["kind"], "Cache")
        compiled = E.compile_deltas(ix, done)
        self.assertEqual((compiled.status, compiled.errors), ("DRAFT", ["element fp4.x is not connected to any formal edge"]))
        runs = {r["id"]: r for r in ELEMENTS["runs"]}
        for key in ("elements::compile-no-rewrite", "elements::compile-missing-rewrite-key"):
            self.assertEqual(self.golden["diverged"][key], {"status": "DRAFT"}, key)
            self.assertEqual(execute(ELEMENTS["model"], runs[key.split("::", 1)[1]])["status"], "DRAFT", key)
        # an element that is not connected cannot run: simulate reports DRAFT with an empty trace
        run = E.simulate(fx.model, fx.case("ONE"), done, {"index": ix})
        self.assertEqual((run["status"], run["trace"]), ("DRAFT", []))
        # a real error beats the draft
        mixed = E.compile_deltas(ix, [*done, {"type": "disable_module", "targetNodeId": "nope"}])
        self.assertEqual(mixed.status, "INVALID")

    def test_h1_a_truncated_pair_is_limit_exceeded(self):
        fx = Fx(CORE)
        iv = fx.iv("DIS-S2")
        case_id = fx.case("OK")["caseId"]
        full = E.run_causal(fx.model, case_id, [iv], {"index": fx.ix})
        self.assertEqual(full["status"], "OK")
        for part in (full["diff"], full["explanation"], E.causal_chain(fx.model, full, iv, fx.ix)):
            self.assertNotIn("truncated", part)          # the key exists only for a truncated pair
        cut = E.run_causal(fx.model, case_id, [iv], {"index": fx.ix, "maxEvents": 2})
        self.assertEqual((cut["A"]["status"], cut["B"]["status"]), ("LIMIT_EXCEEDED", "LIMIT_EXCEEDED"))
        self.assertEqual(cut["status"], "LIMIT_EXCEEDED")
        self.assertIs(cut["diff"]["truncated"], True)
        self.assertIs(cut["explanation"]["truncated"], True)
        chain = E.causal_chain(fx.model, cut, iv, fx.ix)
        self.assertEqual((chain["status"], chain["truncated"]), ("LIMIT_EXCEEDED", True))
        plain = E.causal_chain(fx.model, full, iv, fx.ix)
        for locale in E.LOCALES:
            localised = E.causal_chain(fx.model, cut, iv, fx.ix, locale)["headline"]
            self.assertIn(" 2", localised.replace("（", " ").replace("(", " "), locale)   # the entry count is in the sentence
            self.assertNotEqual(localised, E.causal_chain(fx.model, full, iv, fx.ix, locale)["headline"], locale)
        self.assertNotIn("truncated", plain)
        entry = self.golden["diverged"]["core::causal-limit"]["truncation"]
        self.assertEqual(entry, {"status": "LIMIT_EXCEEDED", "diffTruncated": True, "chainTruncated": True})
        runs = {r["id"]: r for r in CORE["runs"]}
        out = execute(CORE["model"], runs["causal-limit"])
        self.assertEqual((out["result"]["status"], out["result"]["diff"]["truncated"], out["chain"]["truncated"]),
                         ("LIMIT_EXCEEDED", True, True))
        # a prediction of status OK is a mismatch for a truncated pair
        self.assertFalse(E.check_prediction({"expect": {"status": "OK"}}, cut)["ok"])

    def test_h2_the_retry_loop_is_bounded_and_hostile_numbers_are_errors(self):
        entry = next(d for d in self.deviations["deviations"] if d["id"] == "H2")
        self.assertEqual(entry["category"], "hardening")
        for name in ("test_retry_loop_stops_at_max_events", "test_change_retry_above_the_bound_is_invalid",
                     "test_options_of_the_wrong_type_are_coded_errors", "test_element_latency_and_rewrite_edge_must_be_well_typed"):
            self.assertTrue(callable(getattr(HardeningTests, name)), name)


class HardeningTests(unittest.TestCase):
    """Defences of the fix pass (bounded retry loops, hostile numbers, coded errors, aliasing) and two mutation-killers."""

    @classmethod
    def setUpClass(cls):
        cls.core = Fx(CORE)
        cls.elements = Fx(ELEMENTS)

    def slow_case(self, retry_max, latency=10 ** 6, timeout=1):
        fx = self.core
        a, gw = fx.node("u1"), fx.node("gw")
        case = copy.deepcopy(fx.case("OK"))
        case["events"] = [{"seq": 1, "type": "Call", "from": a, "to": gw, "latencyMs": latency}]
        case["policy"] = {"timeoutMs": timeout, "retryMax": retry_max, "retryBackoffMs": 0}
        return case

    def test_retry_loop_stops_at_max_events(self):
        """A huge retryMax (case policy or intervention) ends at maxEvents with LIMIT_EXCEEDED, not after 10**9 attempts."""
        fx = self.core
        started = time.perf_counter()
        run = E.simulate(fx.model, self.slow_case(10 ** 9), [], {"index": fx.ix, "maxEvents": 50})
        self.assertEqual(run["status"], "LIMIT_EXCEEDED")
        self.assertLessEqual(len(run["trace"]), 51)
        self.assertTrue(all(e["eventType"] == "Timeout" for e in run["trace"]))
        # through an intervention (the largest value the engine compiles)
        case = self.slow_case(0)
        iv = {"interventionId": "X", "type": "change_retry", "appliesTo": [case["caseId"]], "retryMax": 1000}
        run = E.simulate(fx.model, case, [iv], {"index": fx.ix, "maxEvents": 20})
        self.assertEqual(run["status"], "LIMIT_EXCEEDED")
        self.assertLessEqual(len(run["trace"]), 21)
        self.assertLess(time.perf_counter() - started, 5.0)
        # a run that finishes inside the bound is unchanged: all attempts are recorded and the last one fails
        # an infinite limit is refused, so a huge case-policy retryMax cannot spin
        with self.assertRaises(E.AtlasEngineError):
            E.simulate(fx.model, self.slow_case(10 ** 9), [], {"index": fx.ix, "maxEvents": float("inf")})
        done = E.simulate(fx.model, self.slow_case(3), [], {"index": fx.ix})
        self.assertEqual((done["status"], len(done["trace"])), ("OK", 5))
        self.assertEqual(done["trace"][-1]["eventType"], "Failure")

    def test_change_retry_above_the_bound_is_invalid(self):
        ix = self.core.ix
        for retry in (engine_types.MAX_RETRY + 1, 10 ** 9):
            compiled = E.compile_deltas(ix, [{"type": "change_retry", "retryMax": retry}])
            self.assertEqual(compiled.status, "INVALID", retry)
            self.assertEqual(compiled.errors, [f"change_retry: retryMax must be <= {engine_types.MAX_RETRY}"])
        self.assertEqual(E.compile_deltas(ix, [{"type": "change_retry", "retryMax": engine_types.MAX_RETRY}]).status, "OK")

    def test_a_pair_whose_runs_hit_the_limit_inside_a_retry_loop_is_truncated(self):
        fx = self.core
        case = self.slow_case(10 ** 9)
        model = {**fx.model, "cases": [*fx.model["cases"], case]}
        iv = {"interventionId": "X", "type": "change_timeout", "appliesTo": [case["caseId"]], "timeoutMs": 2}
        result = E.run_causal(model, case["caseId"], [iv], {"maxEvents": 30})
        self.assertEqual(result["status"], "LIMIT_EXCEEDED")
        self.assertTrue(result["diff"]["truncated"])

    def test_element_latency_and_rewrite_edge_must_be_well_typed(self):
        fx = self.elements
        base = copy.deepcopy(fx.iv("VAL"))
        for latency in ("zz", float("nan"), float("inf"), -1, {"a": 1}, [1], True):
            iv = copy.deepcopy(base)
            iv["element"]["latencyMs"] = latency
            compiled = E.compile_deltas(fx.ix, [iv])
            self.assertEqual(compiled.status, "INVALID", repr(latency))
            self.assertTrue(compiled.errors[0].startswith("insert_element "), compiled.errors)
        # the contract gate rejects the same values (additive check in model._check_element)
        bad_model = copy.deepcopy(fx.model)
        next(i for i in bad_model["interventions"] if i["interventionId"] == base["interventionId"])["element"]["latencyMs"] = "zz"
        self.assertTrue(any(i["path"].endswith("element.latencyMs") for i in am.contract_issues(bad_model)))
        ok = copy.deepcopy(base)
        ok["element"]["latencyMs"] = 5
        self.assertEqual(E.compile_deltas(fx.ix, [ok]).status, "OK")
        for edge in ({"a": 1}, ["x"], 7, None):
            iv = copy.deepcopy(base)
            iv["rewrite"][0]["replaceEdgeId"] = edge
            compiled = E.compile_deltas(fx.ix, [iv])
            self.assertEqual(compiled.status, "INVALID", repr(edge))
            run = E.simulate(fx.model, fx.case("ONE"), [iv], {"index": fx.ix})
            self.assertEqual(run["status"], "REJECTED")
            result = E.run_causal(fx.model, fx.case("ONE")["caseId"], [iv], {"index": fx.ix})
            self.assertEqual(result["status"], "REJECTED")

    def test_a_lone_surrogate_does_not_crash_a_run(self):
        model = synth.generate("small", 2)
        model["nodes"][0]["name"] = "\ud800"
        self.assertEqual(am.contract_issues(model), [])
        run = E.simulate(model, model["cases"][0], [], {})
        self.assertEqual(run["status"], "OK")
        self.assertIsNone(run["modelVersion"])
        self.assertEqual(E.run_causal(model, model["cases"][0]["caseId"], [], {})["status"], "OK")

    def test_options_of_the_wrong_type_are_coded_errors(self):
        fx = self.core
        case = fx.case("OK")
        for opts in ({"interventionTime": "5"}, {"interventionTime": float("nan")}, {"interventionTime": True},
                     {"interventionTime": [1]}, {"maxEvents": "9"}, {"maxEvents": -1}, {"maxEvents": float("nan")},
                     {"maxEvents": False}, {"maxEvents": float("inf")}):
            with self.assertRaises(E.AtlasEngineError, msg=str(opts)) as ctx:
                E.simulate(fx.model, case, [], {**opts, "index": fx.ix})
            self.assertEqual(ctx.exception.code, engine_types.ENGINE_ERROR_BAD_ARGUMENT, str(opts))
            with self.assertRaises(E.AtlasEngineError):
                E.run_causal(fx.model, case["caseId"], [], {**opts, "index": fx.ix})
        self.assertEqual(E.simulate(fx.model, case, [], {"index": fx.ix, "interventionTime": None, "maxEvents": None})["status"], "OK")
        self.assertEqual(E.simulate(fx.model, case, [], {"index": fx.ix, "interventionTime": 2.5, "maxEvents": 500.0})["status"], "OK")

    def test_input_that_breaks_the_contract_raises_a_coded_error_not_a_bare_type_error(self):
        fx = self.core
        for mutate in (lambda c: c["events"][1].__setitem__("latencyMs", []), lambda c: c["events"][0].__setitem__("seq", "x"),
                       lambda c: c["events"][0].pop("from")):
            model = copy.deepcopy(fx.model)
            case = next(c for c in model["cases"] if c["caseId"].endswith("-OK"))
            mutate(case)
            self.assertNotEqual(am.contract_issues(model), [])             # the contract does reject it
            with self.assertRaises(E.AtlasEngineError) as ctx:
                E.simulate(model, case, [], {})
            self.assertEqual(ctx.exception.code, engine_types.ENGINE_ERROR_CONTRACT)
        # a deeply nested guard (the contract allows at most 16 levels) does not crash the interpreter with a RecursionError
        deep = {"param": "x", "eq": 1}
        for _ in range(3000):
            deep = {"not": deep}
        model = copy.deepcopy(fx.model)
        case = next(c for c in model["cases"] if c["caseId"].endswith("-OK"))
        case["events"][0]["guard"] = deep
        with self.assertRaises(E.AtlasEngineError) as ctx:
            E.simulate(model, case, [], {})
        self.assertEqual(ctx.exception.code, engine_types.ENGINE_ERROR_CONTRACT)
        # an unknown case keeps its own code
        with self.assertRaises(E.AtlasEngineError) as ctx:
            E.run_causal(fx.model, "no-such-case", [])
        self.assertEqual(ctx.exception.code, engine_types.ENGINE_ERROR_UNKNOWN_CASE)

    def test_a_run_result_does_not_alias_the_policy_context_or_expectation_of_its_inputs(self):
        fx = self.core
        model = copy.deepcopy(fx.model)
        case = next(c for c in model["cases"] if c["caseId"].endswith("-RETRY"))
        case["context"] = {"k": {"deep": 1}}
        iv = {"interventionId": "X", "type": "disable_module", "appliesTo": [case["caseId"]], "targetNodeId": fx.node("s1"),
              "expect": {"status": "OK", "terminal": {fx.node("s1"): "Blocked"}}}
        before = copy.deepcopy((model, iv))
        result = E.run_causal(model, case["caseId"], [iv], {})
        conditions = result["explanation"]["baselineConditions"]
        conditions["policy"]["timeoutMs"] = 1
        conditions["context"]["k"]["deep"] = 2
        chain = E.causal_chain(model, result, iv)
        chain["prediction"]["expected"]["terminal"]["x"] = "y"
        self.assertEqual((model, iv), before)

    def test_every_pending_element_answer_is_counted_down_one_by_one(self):
        """Two requests answered by an element are in flight; each scripted Return is skipped exactly once (mutation: the
        counter was reset to zero instead of decremented)."""
        fx = self.elements
        a, b, c = fx.node("a"), fx.node("b"), fx.node("c")
        iv = copy.deepcopy(fx.iv("CACHE"))
        iv["element"]["eventHandlers"] = [{"on": "receive", "do": "respond"}]
        case = copy.deepcopy(fx.case("TWO"))
        case["events"] = [{"seq": 1, "type": "Call", "from": a, "to": b}, {"seq": 2, "type": "Call", "from": b, "to": c},
                          {"seq": 3, "type": "Call", "from": b, "to": c}, {"seq": 4, "type": "Return", "from": c, "to": b},
                          {"seq": 5, "type": "Return", "from": c, "to": b}, {"seq": 6, "type": "Return", "from": c, "to": b}]
        run = E.simulate(fx.model, case, [iv], {"index": fx.ix})
        self.assertEqual(run["status"], "OK")
        by_id = {e["eventId"].split("#", 1)[1]: e for e in run["trace"]}
        self.assertEqual([code_of(by_id[f"{n}#h1"]) for n in (4, 5)], ["already_answered", "already_answered"])
        self.assertNotEqual(code_of(by_id["6#h1"]), "already_answered")     # a third Return is nobody's answer

    def test_a_shared_step_resets_the_provenance_of_the_nodes_it_changes(self):
        """A node changed by a differing step and then again by a step both runs share is no longer explained by the first
        step: a later differing step that starts at that node is explained by the intervention (mutation: the provenance
        entry was kept)."""
        fx = self.core
        p, q, r = fx.node("u1"), fx.node("gw"), fx.node("s1")

        def entry(index, seq, src, dst, outcome, changes, cause=None, next_state="Running"):
            return {"index": index, "eventId": f"PB8-OK#{seq}", "sourceNodeId": src, "targetNodeId": dst, "edgeId": None,
                    "eventType": "Call", "outcome": outcome, "nextState": next_state, "errorCode": None, "cause": cause,
                    "attempt": None, "role": None, "logicalTime": index, "previousState": None, "synthetic": False,
                    "stateChanges": changes}

        def ch(node, before, after):
            return [{"nodeId": node, "from": before, "to": after}]
        a0 = entry(0, 1, p, q, "OK", [])
        b0 = entry(0, 1, p, q, "Failed", ch(q, "Idle", "Failed"), cause=E.cause("declared_fault"), next_state="Failed")
        same = entry(1, 2, q, r, "OK", ch(q, "Failed", "Recovered"), next_state="Recovered")
        a2 = entry(2, 3, q, r, "OK", ch(r, "Idle", "Running"))
        b2 = entry(2, 3, q, r, "Starved", [], cause=E.cause("source_state", state="Failed"), next_state="Failed")
        A = {"trace": [a0, same, a2], "finalState": {p: "Running", q: "Recovered", r: "Running"}, "status": "OK"}
        B = {"trace": [b0, same, b2], "finalState": {p: "Running", q: "Recovered", r: "Idle"}, "status": "OK"}
        diff = {"outcomeClass": "DIVERGED", "firstDivergence": {"index": 0, "logicalTime": 0, "a": "x", "b": "y"},
                "terminalDiff": [{"nodeId": r, "a": "Running", "b": "Idle"}], "addedNodes": []}
        result = {"caseId": "PB8-OK", "status": "OK", "errors": [], "A": A, "B": B, "diff": diff}
        chain = E.causal_chain(fx.model, result, None, fx.ix)
        starved = next(s for s in chain["steps"] if s["role"] == "starve")
        self.assertEqual(starved["parents"], ["root"], starved["via"])      # not "B0": node q was changed again by a shared step


class RobustnessTests(unittest.TestCase):
    def test_a_generated_atlas_scale_model_runs_deterministically_and_stays_json_safe(self):
        model = synth.generate("reference", 3)
        ix = E.index_model(model)
        started = time.perf_counter()
        runs = [E.simulate(model, case, [], {"index": ix}) for case in model["cases"]]
        elapsed = time.perf_counter() - started
        self.assertEqual(len(runs), len(model["cases"]))
        self.assertTrue(all(r["status"] in ("OK", "LIMIT_EXCEEDED") for r in runs))
        again = [E.simulate(model, case, [], {"index": ix}) for case in model["cases"]]
        self.assertEqual(runs, again)
        json.dumps(runs, allow_nan=False)
        self.assertLess(elapsed, 20.0, f"{len(runs)} baselines took {elapsed:.1f}s")

    def test_every_generated_intervention_computes_or_is_reported_never_crashes(self):
        model = synth.generate("small", 5)
        ix = E.index_model(model)
        statuses = set()
        for iv in model["interventions"]:
            for case_id in iv["appliesTo"][:2]:
                result = E.run_causal(model, case_id, [iv], {"index": ix})
                chain = E.causal_chain(model, result, iv, ix)
                json.dumps([result, chain], allow_nan=False)
                statuses.add(result["status"])
        self.assertTrue(statuses <= {"OK", "DRAFT", "REJECTED", "LIMIT_EXCEEDED"})
        self.assertIn("OK", statuses)

    def test_edge_case_models_run(self):
        for name in ("machine", "insert-element", "no-interventions", "odd-ids", "cjk"):
            model = synth.edge_case(name, 2)
            ix = E.index_model(model)
            for case in model["cases"][:3]:
                run = E.simulate(model, case, [], {"index": ix})
                self.assertIn(run["status"], ("OK", "LIMIT_EXCEEDED"), name)


if __name__ == "__main__":
    unittest.main()
