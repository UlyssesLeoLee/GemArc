"""Performance budgets of the engine on the reference-scale synthetic model (design 5.5 and M5 (e)).

Budgets (this machine, Python standard library only):

* oracle-verdict-style run: every baseline plus every authored (intervention, case) pair (cap 200 pairs): at most 2 s;
* build-style run: the verdict run plus the guard scan plus up to 12 mutants per case: at most 10 s;
* stop/go: a timing above 1.5x its budget would cut the mutant cap from 12 to 6; baselines alone above 3 s mean profile or cache.

The synthetic model has 23 cases, 322 events and 21 interventions (``synth.generate("reference", seed)``). Each test takes the
median of ``REPEATS`` runs so that one slow scheduler hiccup does not decide the verdict. The measured numbers are printed
when the module is run with ``--report``::

    PYTHONPATH=src:tests ./.venv/Scripts/python.exe -m atlas.test_engine_perf --report

and the budgets are asserted when it runs as tests::

    PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_perf*.py"
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import unittest

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas.engine import mutate
from gemarc_mcp.atlas.engine import sweep as scan

from . import synth

REPEATS = 3
PAIR_CAP = 200
VERDICT_BUDGET = 2.0
BUILD_BUDGET = 10.0
BASELINE_LIMIT = 3.0
SEEDS = (3, 7)


def pairs_of(model: dict, cap: int = PAIR_CAP) -> list[tuple[dict, str]]:
    """Authored (intervention, case id) pairs, in model order, at most ``cap``."""
    out = []
    for iv in model["interventions"]:
        for case_id in iv["appliesTo"]:
            if len(out) < cap:
                out.append((iv, case_id))
    return out


def baselines(model: dict, ix) -> list[dict]:
    return [E.simulate(model, case, [], {"index": ix}) for case in model["cases"]]


def verdict_run(model: dict, ix) -> dict:
    """Oracle-verdict-style: every baseline and every pair as a full baseline / intervention / comparison run."""
    base = baselines(model, ix)
    results = []
    for iv, case_id in pairs_of(model):
        result = E.run_causal(model, case_id, [iv], {"index": ix})
        if result["status"] == "OK":  # build embeds the causal chain of every computed pair
            E.causal_chain(model, result, iv, ix)
        results.append(result)
    return {"baselines": len(base), "pairs": len(results), "ok": sum(1 for r in results if r["status"] == "OK")}


def build_run(model: dict, ix, cap: int = mutate.DEFAULT_CAP) -> dict:
    """Build-style: the verdict run, the guard scan and the mutants of every case (expectations fixed to the engine's own)."""
    out = verdict_run(model, ix)
    scanned = scan.scan_model(model, index=ix)
    spec = {}
    for case in model["cases"]:
        run = E.simulate(model, case, [], {"index": ix})
        spec[case["caseId"]] = {"terminal": {n: run["finalState"][n] for n in mutate.participants(case)}}
    report = mutate.mutation_report(model, spec, cap=cap, index=ix)
    return {**out, "guards": scanned["stats"]["guards"], "probes": scanned["stats"]["probes"], "mutants": report["total"],
            "killed": report["killed"]}


def timed(fn, *args, repeats: int = REPEATS) -> tuple[float, object]:
    """Median wall time of ``fn(*args)`` over ``repeats`` runs, and the last result."""
    samples, result = [], None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn(*args)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), result


def measure(seed: int) -> dict:
    model = synth.generate("reference", seed)
    start = time.perf_counter()
    ix = E.index_model(model)
    index_time = time.perf_counter() - start
    base_time, _ = timed(baselines, model, ix)
    verdict_time, verdict = timed(verdict_run, model, ix)
    build_time, build = timed(build_run, model, ix)
    half_time, _ = timed(build_run, model, ix, 6)
    scan_time, _ = timed(scan.scan_model, model)
    scale = {"cases": len(model["cases"]), "events": sum(len(c["events"]) for c in model["cases"]),
             "interventions": len(model["interventions"]), "nodes": len(model["nodes"])}
    return {"seed": seed, "scale": scale, "index": index_time, "baselines": base_time, "verdict": verdict_time,
            "build": build_time, "buildCap6": half_time, "scan": scan_time, "verdictResult": verdict, "buildResult": build}


class ScaleTests(unittest.TestCase):
    def test_the_synthetic_model_has_the_reference_scale(self):
        for seed in SEEDS:
            model = synth.generate("reference", seed)
            self.assertEqual(len(model["cases"]), 23)
            self.assertEqual(sum(len(c["events"]) for c in model["cases"]), 322)
            self.assertEqual(len(model["interventions"]), 21)
            self.assertGreaterEqual(len(pairs_of(model)), 21)
            self.assertLessEqual(len(pairs_of(model)), PAIR_CAP)

    def test_the_pair_cap_applies(self):
        model = synth.generate("reference", 3)
        many = {**model, "interventions": [{**model["interventions"][0], "interventionId": f"IV{i}"} for i in range(400)]}
        self.assertEqual(len(pairs_of(many)), PAIR_CAP)


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [measure(seed) for seed in SEEDS]

    def test_baselines_alone_stay_under_the_profile_threshold(self):
        for row in self.rows:
            self.assertLess(row["baselines"], BASELINE_LIMIT, row)

    def test_oracle_verdict_style_run_is_within_2_seconds(self):
        for row in self.rows:
            self.assertLessEqual(row["verdict"], VERDICT_BUDGET, row)
            self.assertEqual(row["verdictResult"]["baselines"], 23)
            self.assertGreaterEqual(row["verdictResult"]["pairs"], 21)

    def test_build_style_run_with_scan_and_mutants_is_within_10_seconds(self):
        for row in self.rows:
            self.assertLessEqual(row["build"], BUILD_BUDGET, row)
            self.assertGreater(row["buildResult"]["mutants"], 100)
            self.assertLessEqual(row["buildResult"]["mutants"], 23 * mutate.DEFAULT_CAP)

    def test_no_timing_needs_the_cap_cut(self):
        """Stop/go: a timing above 1.5x budget would cut the mutant cap from 12 to 6."""
        for row in self.rows:
            self.assertLess(row["verdict"], 1.5 * VERDICT_BUDGET)
            self.assertLess(row["build"], 1.5 * BUILD_BUDGET)

    def test_the_cap_of_six_is_cheaper_than_the_cap_of_twelve(self):
        for row in self.rows:
            self.assertLess(row["buildCap6"], row["build"] * 1.2 + 0.05)


def main() -> None:
    rows = [measure(seed) for seed in SEEDS]
    for row in rows:
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}, sort_keys=True))


if __name__ == "__main__":
    if "--report" in sys.argv:
        main()
    else:
        unittest.main()
