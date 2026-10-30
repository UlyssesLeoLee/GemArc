"""Golden parity of the Atlas engine against frozen reference values.

The goldens in ``golden/engine.json`` were produced ONCE by a maintainer harness that ran a copy of the reference
engine on the synthetic models of ``engine_fixtures/`` (random id families). Neither the harness nor the reference
code is in this repository; ``golden/GENERATION.md`` records the sha256 of every input and output and of the archived
generator. This file holds the parity test and the helpers the harness shares with it (``execute``, ``shuffle_model``).

Two layers
----------
``shared``    the port must equal the reference value for value. Strings the port renders in a language
              (``why``, ``headline`` ...) and the run ``hash`` are not part of it (they differ by recorded divergence
              2 and, for the hash, by the structured ``cause`` it covers); a ``cause`` is compared in its structured
              ``{code, args}`` form, obtained by a lossless mapping of the reference string.
``diverged``  hand-reviewed values for what the four recorded fixes change: alignment rows of suffixed event ids
              (1), run hashes over structured causes (2), see ``golden/deviations.json`` for all of them.

Run only this file::

    PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_golden*.py"
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any, Mapping

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas import model as am

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "engine_fixtures"
GOLDEN_DIR = HERE / "golden"
GOLDEN_FILE = GOLDEN_DIR / "engine.json"
DEVIATIONS_FILE = GOLDEN_DIR / "deviations.json"
GENERATION_FILE = GOLDEN_DIR / "GENERATION.md"

#: Fixture categories the design requires (design 9.2); every one must be exercised by at least one fixture run.
REQUIRED_TAGS = (
    "determinism", "order-by-seq", "strict-timeout", "retry-latency-array", "backoff-clock", "parallel-group",
    "starved-propagation", "insert-element", "draft-element", "dedup-reject", "dedup-ignore", "aggregation-degraded",
    "transition-guard", "intervention-time", "limit-exceeded", "lcs-alignment", "suffixed-ids", "snapshot-seek",
    "type:disable_module", "type:disable_edge", "type:resource_failure", "type:identity_failure", "type:change_timeout",
    "type:change_retry", "type:delay_message", "type:change_guard", "type:insert_element", "type:combined_intervention",
)


# --------------------------------------------------------------------------------------------------------
# Fixtures and runs
# --------------------------------------------------------------------------------------------------------


def fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def shuffle_model(model: Mapping, mode: str) -> dict:
    """Copy of ``model`` whose case events are in another array order and whose event keys are in reverse order.

    The engine orders by ``seq`` and must not depend on array order or key order. Nodes and edges are NOT shuffled
    (array order is semantic for them: the first matching edge routes).
    """
    out = copy.deepcopy(dict(model))
    for case in out.get("cases", []):
        events = list(case["events"])
        if mode == "reverse":
            events.reverse()
        elif mode == "rotate":
            k = len(events) // 3 + 1
            events = events[k:] + events[:k]
        else:
            raise ValueError(f"unknown shuffle mode {mode!r}")
        case["events"] = [dict(reversed(list(e.items()))) for e in events]
    return out


def resolve_run(model: Mapping | None, run: Mapping) -> tuple[list, Mapping | None]:
    """``(deltas, intervention record)`` of a run: a named or inline ``intervention``, or explicit ``deltas``."""
    iv = run.get("intervention")
    if isinstance(iv, str):
        iv = next(i for i in model["interventions"] if i["interventionId"] == iv)
    deltas = run["deltas"] if "deltas" in run else ([iv] if iv else [])
    return deltas, iv


def run_model(model: Mapping | None, run: Mapping) -> Mapping | None:
    return shuffle_model(model, run["shuffle"]) if model is not None and run.get("shuffle") else model


def compile_view(compiled: E.Compiled) -> dict:
    """The part of a compile result that is compared with the reference (bodies are inputs, not results)."""
    return {
        "status": compiled.status, "errors": list(compiled.errors), "disabledNodes": sorted(compiled.disabled_nodes),
        "disabledEdges": sorted(compiled.disabled_edges), "resourceNodes": sorted(compiled.resource_nodes),
        "identityNodes": sorted(compiled.identity_nodes), "policy": dict(compiled.policy),
        "guardOverrides": dict(compiled.guard_overrides), "delays": list(compiled.delays),
        "flatTypes": [d.get("type") for d in compiled.flat], "elementIds": sorted(compiled.elements),
        "replaced": {k: {"disposition": v["disposition"], "elementId": v["elementId"]} for k, v in compiled.replaced.items()},
    }


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))


def execute(model: Mapping | None, run: Mapping) -> dict:
    """One run through the engine, in the shape the goldens store (JSON-safe)."""
    op = run["op"]
    model = run_model(model, run)
    if op == "guard":
        return {"value": E.eval_guard(run.get("guard"), {"params": run.get("params"), "context": run.get("context")})}
    if op == "when":
        ctx = {"state": run.get("state") or {}, "params": run.get("params"), "context": run.get("context")}
        if "elementState" in run:
            ctx["elementState"] = run["elementState"]
        return {"value": E.eval_when(run.get("when"), ctx)}
    if op == "fnv":
        return {"value": E.fnv32(run["text"])}
    if op == "runHash":
        return {"value": E.trace_hash({"trace": run["trace"]})}
    if op == "align":
        return jsonable({"rows": E.align_traces({"trace": run["A"]}, {"trace": run["B"]})})
    ix = E.index_model(model)
    deltas, iv = resolve_run(model, run)
    opts = dict(run.get("opts") or {})
    if op == "compile":
        return jsonable(compile_view(E.compile_deltas(ix, deltas)))
    if op == "simulate":
        result = E.simulate(model, ix.cases[run["caseId"]], deltas, {**opts, "index": ix})
        out: dict = {"run": result}
        if run.get("replay") and result["snapshots"]:
            out["replay"] = [{"index": i, "seek": E.seek(result, i), "fold": E.fold_state(result, i) if i >= 0 else dict(result["initialState"])}
                             for i in range(-1, len(result["trace"]))]
        return jsonable(out)
    if op == "causal":
        try:
            result = E.run_causal(model, run["caseId"], deltas, {**opts, "index": ix})
        except E.AtlasEngineError as ex:
            return {"thrown": ex.message}
        return jsonable({"result": result, "chain": E.causal_chain(model, result, iv, ix)})
    raise ValueError(f"unknown op {op!r}")


def strip_for_shared(out: dict) -> dict:
    """Remove what is not compared against the reference (run hashes, rendered sentences)."""
    out = copy.deepcopy(out)

    def drop_hash(run):
        if isinstance(run, dict):
            run.pop("hash", None)

    if "run" in out:
        drop_hash(out["run"])
    result = out.get("result")
    if isinstance(result, dict):
        if result.get("status") == "LIMIT_EXCEEDED":  # deviation H1: the reference reported OK for a truncated pair
            result["status"] = "OK"
            for part in (result.get("diff"), result.get("explanation")):
                if isinstance(part, dict):
                    part.pop("truncated", None)
        drop_hash(result.get("A"))
        drop_hash(result.get("B"))
        if isinstance(result.get("explanation"), dict):
            result["explanation"].pop("summary", None)
            result["explanation"].pop("assumptions", None)
    chain = out.get("chain")
    if isinstance(chain, dict):
        if chain.pop("truncated", None):  # deviation H1
            chain["status"] = "OK"
        chain.pop("question", None)
        chain.pop("headline", None)
        for step in chain.get("steps", []):
            step.pop("why", None)
    return out


def hashes_of(out: dict) -> dict:
    if "run" in out:
        return {"run": out["run"]["hash"]}
    if "result" in out:
        return {"A": out["result"]["A"]["hash"], "B": out["result"]["B"]["hash"]}
    return {}


def golden_key(fixture: str, run_id: str) -> str:
    return f"{fixture}::{run_id}"


def load_golden() -> dict:
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_types(model: Mapping | None, run: Mapping) -> set[str]:
    deltas, _ = resolve_run(model, run) if run.get("op") in ("simulate", "causal", "compile") else ([], None)
    flat = E.flatten_deltas(deltas)
    return {f"type:{d.get('type')}" for d in [*deltas, *flat] if isinstance(d, Mapping)}


def run_tags(model: Mapping | None, run: Mapping) -> set[str]:
    tags = set(run.get("tags", [])) | run_types(model, run)
    if run.get("replay"):
        tags.add("snapshot-seek")
    return tags


def first_difference(actual, expected, path: str = "$") -> str | None:
    """Path of the first difference between two JSON values (``None`` when equal)."""
    if type(actual) is not type(expected) and not (isinstance(actual, (int, float)) and isinstance(expected, (int, float))
                                                    and not isinstance(actual, bool) and not isinstance(expected, bool)):
        return f"{path}: type {type(actual).__name__} != {type(expected).__name__} ({str(actual)[:60]!r} vs {str(expected)[:60]!r})"
    if isinstance(actual, dict):
        for key in sorted(set(actual) | set(expected)):
            if key not in actual:
                return f"{path}.{key}: missing in the port output"
            if key not in expected:
                return f"{path}.{key}: not in the golden"
            found = first_difference(actual[key], expected[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(actual, list):
        if len(actual) != len(expected):
            return f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, b) in enumerate(zip(actual, expected)):
            found = first_difference(a, b, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if actual == expected else f"{path}: {str(actual)[:80]!r} != {str(expected)[:80]!r}"


# --------------------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def test_every_model_fixture_passes_the_contract(self):
        names = []
        for path in fixture_paths():
            fixture = load_fixture(path)
            if fixture["model"] is not None:
                self.assertEqual(am.contract_issues(fixture["model"]), [], path.name)
                names.append(path.name)
        self.assertGreaterEqual(len(names), 2)

    def test_fixtures_are_canonical_files(self):
        for path in fixture_paths():
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertNotIn(b"\r", raw, path.name)
            self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"), path.name)
            self.assertEqual(raw, (json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"), path.name)

    def test_run_ids_are_unique_and_every_run_has_a_golden_entry(self):
        golden = load_golden()
        for path in fixture_paths():
            fixture = load_fixture(path)
            ids = [r["id"] for r in fixture["runs"]]
            self.assertEqual(len(ids), len(set(ids)), path.name)
            for run_id in ids:
                self.assertIn(golden_key(fixture["name"], run_id), golden["shared"], f"{fixture['name']}::{run_id}")
        known = {golden_key(load_fixture(p)["name"], r["id"]) for p in fixture_paths() for r in load_fixture(p)["runs"]}
        self.assertEqual(set(golden["shared"]) - known, set(), "golden entries without a fixture run")


class SharedLayerTests(unittest.TestCase):
    """The port equals the reference value for value on every run of every fixture."""

    @classmethod
    def setUpClass(cls):
        cls.golden = load_golden()

    def _check_fixture(self, path: Path) -> None:
        fixture = load_fixture(path)
        model = fixture["model"]
        bad: list[str] = []
        for run in fixture["runs"]:
            key = golden_key(fixture["name"], run["id"])
            expected = dict(self.golden["shared"][key])
            actual = strip_for_shared(execute(model, run))
            for part in expected.pop("withheld", []):
                actual.pop(part, None)
                self.assertIn(part, self.golden["diverged"][key], f"{key}: {part} withheld from shared but absent from diverged")
            found = first_difference(actual, expected)
            if found:
                bad.append(f"{key} {found}")
        self.assertEqual(bad, [], f"{len(bad)} of {len(fixture['runs'])} runs differ from the reference; first: {bad[:3]}")

    def test_core_fixture(self):
        self._check_fixture(FIXTURE_DIR / "core.json")

    def test_elements_fixture(self):
        self._check_fixture(FIXTURE_DIR / "elements.json")

    def test_guard_vectors(self):
        self._check_fixture(FIXTURE_DIR / "guard-vectors.json")

    def test_align_vectors(self):
        self._check_fixture(FIXTURE_DIR / "align-vectors.json")

    def test_no_fixture_file_is_unchecked(self):
        self.assertEqual({p.name for p in fixture_paths()}, {"core.json", "elements.json", "guard-vectors.json", "align-vectors.json"})


class DivergedLayerTests(unittest.TestCase):
    """The reviewed values for what the recorded fixes change."""

    @classmethod
    def setUpClass(cls):
        cls.golden = load_golden()
        cls.fixtures = {load_fixture(p)["name"]: load_fixture(p) for p in fixture_paths()}

    def _runs(self):
        for fixture in self.fixtures.values():
            for run in fixture["runs"]:
                yield fixture, run

    def test_run_hashes_are_the_reference_hash_of_the_structured_trace(self):
        checked = 0
        for fixture, run in self._runs():
            entry = self.golden["diverged"].get(golden_key(fixture["name"], run["id"]), {})
            if "hashes" not in entry:
                continue
            self.assertEqual(hashes_of(execute(fixture["model"], run)), entry["hashes"], golden_key(fixture["name"], run["id"]))
            checked += 1
        self.assertGreater(checked, 60)

    def test_alignment_of_suffixed_event_ids_equals_the_reviewed_rows(self):
        checked = 0
        for fixture, run in self._runs():
            entry = self.golden["diverged"].get(golden_key(fixture["name"], run["id"]), {})
            if "chain" in entry:
                out = execute(fixture["model"], run)
                actual = strip_for_shared(out)["chain"]
                self.assertEqual(first_difference(actual, entry["chain"]), None, golden_key(fixture["name"], run["id"]))
                checked += 1
            if "rows" in entry:
                self.assertEqual(execute(fixture["model"], run)["rows"], entry["rows"], golden_key(fixture["name"], run["id"]))
                checked += 1
        self.assertGreater(checked, 4)

    def test_every_diverged_entry_belongs_to_a_fixture_run(self):
        known = {golden_key(f["name"], r["id"]) for f, r in self._runs()}
        self.assertEqual(set(self.golden["diverged"]) - known, set())


class CoverageTests(unittest.TestCase):
    def test_every_required_fixture_category_is_exercised(self):
        tags: dict[str, int] = {}
        for path in fixture_paths():
            fixture = load_fixture(path)
            for run in fixture["runs"]:
                for tag in run_tags(fixture["model"], run):
                    tags[tag] = tags.get(tag, 0) + 1
        missing = [t for t in REQUIRED_TAGS if t not in tags]
        self.assertEqual(missing, [], f"fixture categories without a run: {missing}")


class GenerationRecordTests(unittest.TestCase):
    """GENERATION.md pins the sha256 of every committed input and output."""

    def _recorded(self) -> dict[str, str]:
        found = {}
        for line in GENERATION_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\|\s*`?([\w./-]+)`?\s*\|\s*([0-9a-f]{64})\s*\|", line)
            if m:
                found[m.group(1)] = m.group(2)
        return found

    def test_the_committed_files_match_the_recorded_digests(self):
        recorded = self._recorded()
        committed = [p for p in fixture_paths()] + [GOLDEN_FILE, DEVIATIONS_FILE]
        for path in committed:
            rel = path.relative_to(HERE).as_posix()
            self.assertIn(rel, recorded, f"{rel} is not recorded in GENERATION.md")
            self.assertEqual(sha256_file(path), recorded[rel], f"{rel} changed since it was recorded")

    def test_the_archived_generator_is_recorded(self):
        text = GENERATION_FILE.read_text(encoding="utf-8")
        self.assertRegex(text, r"generator[^\n]*\b[0-9a-f]{64}\b")


if __name__ == "__main__":
    unittest.main()
