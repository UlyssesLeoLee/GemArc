"""Tests of the synthetic model generator ``tests/atlas/synth.py``.

Covers: determinism (also across ``PYTHONHASHSEED``), the lending-desk fixture, the scale presets against the
numbers of design 3.5, internal consistency (references resolve, ports derivable == stored, unique ids, events bind
to runtime edges), random vs fixed id families and the edge-case models. All ids are synthetic.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_synth*.py"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import vocab

from . import synth

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"

#: Design 3.4, verbatim (synthetic lending desk): the three .atl files, 2,175 bytes by the design's own count.
DESIGN_FILES = (
    """#atlas 1
// Library lending desk: a synthetic example, not a real system.
## buildings: id | name | opts
zone.lib | Library system | trust=internal
zone.ext | Outside | trust=external
## floors: id | building | name | level
lvl.core | zone.lib | Core services | 1
lvl.out | zone.ext | Visitors | 0
## rooms: id | floor | name | opts
rm.desk | lvl.core | Lending desk | tier=T2
rm.vis | lvl.out | Members | tier=T1
""",
    """#atlas 1
## nodes: id | name | sub | opts
@set room=rm.desk kind=Module
svc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2
svc.stock | Stock service | Knows which copy is on the shelf. | ev=EVD-2
@set kind=Storage
tbl.loan | Loan table | One row per open loan. | ev=EVD-1
@set room=rm.vis kind=Actor
usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Inferred
## edges: from | rel | to | label | opts
usr.member | calls | svc.loan | borrow a copy | ev=EVD-3
svc.loan | calls | svc.stock | check copy status | ev=EVD-2
svc.loan | writes | tbl.loan | write loan row | ev=EVD-1
@final edges
## evidences: id | file | anchor | topic | opts
@set repo=main
EVD-1 | src/loan.py | def create_loan( | creates the loan row
EVD-2 | src/stock.py | def find_copy( | shelf lookup
EVD-3 | docs/lending.md | borrower opens the page | the borrower actor
""",
    """#atlas 1
## cases: id | cat | title | opts
CS-BORROW-OK | normal | Member borrows an available copy | req=RQ-1 ev=EVD-1
## events: type | from | to | opts
@set case=CS-BORROW-OK
Call | usr.member | svc.loan | ruleId=R-BORROW
Call | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20
Return | svc.stock | svc.loan
Call | svc.loan | tbl.loan | ruleId=R-WRITE
Return | tbl.loan | svc.loan
Return | svc.loan | usr.member
## expect: case | nodes | opts
CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author
## interventions: id | type | description | opts
IV-STOCK-DOWN | disable_module | Stock service is down while borrowing | target=svc.stock applies=CS-BORROW-OK class=DIVERGED terminal={"svc.stock":"Blocked"}
## requirements: id | title | source | opts
RQ-1 | A member can borrow an available copy | interview 1 | nodes=usr.member,svc.loan,svc.stock,tbl.loan tests=TS-1
""",
)
DESIGN_BYTES = 2175
#: Section sizes of design 3.5 (reference scale, bytes of the authored text).
DESIGN_35 = {"spatial": 3074, "nodes": 38586, "edges": 41469, "evidences": 32048, "cases": 29522, "interventions": 9678,
             "requirements": 5500, "tbds": 4385, "machines": 1725}
DESIGN_35_CORE = 165987
FIXED_PREFIXES = {"svc", "usr", "tbl", "rm", "zone", "lvl"}

_PRESET_SEEDS = (0, 1, 2)
_cache: dict[tuple, dict] = {}


def cached(kind: str, name: str, seed: int, family: str) -> dict:
    """Generated models are read-only inside these tests; cache them (generation is cheap but not free)."""
    key = (kind, name, seed, family)
    if key not in _cache:
        _cache[key] = synth.generate(name, seed, family) if kind == "preset" else synth.edge_case(name, seed, family)
    return _cache[key]


def canon(model) -> bytes:
    return am.canonical_bytes(model)


def design_section_bytes() -> dict[str, int]:
    """Bytes per ``## section`` of the design text; the comment, ``@final`` and ``#atlas`` lines (not written by the estimator) are summed apart."""
    sizes: dict[str, int] = {}
    current = None
    extra = 0
    for text in DESIGN_FILES:
        for line in text.split("\n")[:-1]:
            raw = len(line.encode("utf-8")) + 1
            if line.startswith("#atlas"):
                extra += raw
                current = None
                continue
            if line.startswith("//") or line.startswith("@final"):
                extra += raw
                continue
            match = re.match(r"## (\w+):", line)
            if match:
                current = match.group(1)
            sizes[current] = sizes.get(current, 0) + raw
    return {"sections": sizes, "extra": extra}


# --------------------------------------------------------------------------------------------------------
# Independent consistency checks (written against the contract text, not against model.py internals)
# --------------------------------------------------------------------------------------------------------


def all_authored_ids(model) -> list[str]:
    ids = [r["id"] for part in ("buildings", "floors", "rooms") for r in model["space"][part]]
    ids += [n["id"] for n in model["nodes"]] + [e["id"] for e in model["edges"]] + [e["evidenceId"] for e in model["evidences"]]
    ids += [c["caseId"] for c in model["cases"]] + [i["interventionId"] for i in model["interventions"]]
    for key in ("requirements", "tests", "tbds", "policies"):
        ids += [r["id"] for r in model[key]]
    return ids


def reference_problems(model) -> list[str]:
    """Every reference field that does not resolve (empty list = all resolve)."""
    s = model["space"]
    building, floor, room = {b["id"] for b in s["buildings"]}, {f["id"] for f in s["floors"]}, {r["id"] for r in s["rooms"]}
    node, edge = {n["id"] for n in model["nodes"]}, {e["id"] for e in model["edges"]}
    ev, test = {e["evidenceId"] for e in model["evidences"]}, {t["id"] for t in model["tests"]}
    req, tbd = {r["id"] for r in model["requirements"]}, {t["id"] for t in model["tbds"]}
    case, machine = {c["caseId"] for c in model["cases"]}, {m["id"] for m in model["machines"]}
    bad: list[str] = []

    def check(where: str, values, valid) -> None:
        for value in values:
            if value not in valid:
                bad.append(f"{where}: {value!r}")

    check("floor.buildingId", [f["buildingId"] for f in s["floors"]], building)
    check("room.floorId", [r["floorId"] for r in s["rooms"]], floor)
    for n in model["nodes"]:
        check("node.roomId", [n["roomId"]], room)
        check("node.evidenceIds", n["evidenceIds"], ev)
    for e in model["edges"]:
        check("edge.endpoint", [e["sourceId"], e["targetId"]], node)
        check("edge.evidenceIds", e["evidenceIds"], ev)
    check("machine.id", machine, node)
    for m in model["machines"]:
        check("machine.evidenceIds", m["evidenceIds"], ev)
    for c in model["cases"]:
        check("case.requirementIds", c["requirementIds"], req)
        check("case.evidenceIds", c["evidenceIds"], ev)
        check("case.linkedTestIds", c["linkedTestIds"], test)
        check("case.expectedTerminalState", c["expectedTerminalState"], node)
        check("case.initialMachines", c.get("initialMachines", {}), machine)
        for event in c["events"]:
            check("event.endpoint", [event["from"], event["to"]], node)
            if "edge" in event:
                check("event.edge", [event["edge"]], edge)
    for i in model["interventions"]:
        check("intervention.appliesTo", i["appliesTo"], case)
        if "targetNodeId" in i:
            check("intervention.targetNodeId", [i["targetNodeId"]], node)
        if "targetEdgeId" in i:
            check("intervention.targetEdgeId", [i["targetEdgeId"]], edge)
        if "machineId" in i:
            check("intervention.machineId", [i["machineId"]], machine)
            owner = next(m for m in model["machines"] if m["id"] == i["machineId"])
            check("intervention.transitionId", [i["transitionId"]], {t["id"] for t in owner["transitions"]})
        for rewrite in i.get("rewrite", []):
            check("rewrite.replaceEdgeId", [rewrite["replaceEdgeId"]], edge)
    for r in model["requirements"]:
        check("requirement.nodeIds", r["nodeIds"], node)
        check("requirement.edgeIds", r["edgeIds"], edge)
        check("requirement.tstIds", r["tstIds"], test)
        check("requirement.evidenceIds", r["evidenceIds"], ev)
        check("requirement.tbdIds", r["tbdIds"], tbd)
    for t in model["tbds"]:
        check("tbd.evidenceIds", t["evidenceIds"], ev)
    for p in model["policies"]:
        check("policy.src", [p["src"]], req | tbd)
    return bad


def unbound_events(model) -> list[tuple[str, int]]:
    """Non-self events that follow no runtime edge in either direction (``owns`` counts: Transition events use it)."""
    runtime = {(e["sourceId"], e["targetId"]) for e in model["edges"] if not e["static"]}
    runtime |= {(b, a) for a, b in runtime}
    out = []
    for c in model["cases"]:
        for event in c["events"]:
            if event["from"] != event["to"] and (event["from"], event["to"]) not in runtime:
                out.append((c["caseId"], event["seq"]))
    return out


class RngTests(unittest.TestCase):
    def test_stream_is_pinned_so_models_do_not_change_with_the_python_version(self):
        rng = synth.Rng(7, "pin")
        self.assertEqual([rng.below(1000) for _ in range(6)], PINNED_STREAM)

    def test_same_seed_same_stream_and_forks_are_independent(self):
        a, b = synth.Rng(3, "x"), synth.Rng(3, "x")
        self.assertEqual([a.random() for _ in range(5)], [b.random() for _ in range(5)])
        self.assertNotEqual(synth.Rng(3).fork("a").random(), synth.Rng(3).fork("b").random())
        self.assertNotEqual(synth.Rng(3, "x").random(), synth.Rng(4, "x").random())

    def test_helpers_stay_in_range(self):
        rng = synth.Rng(1)
        values = [rng.randint(3, 5) for _ in range(300)]
        self.assertEqual(set(values), {3, 4, 5})
        self.assertTrue(all(0 <= rng.random() < 1 for _ in range(300)))
        items = list(range(10))
        rng.shuffle(items)
        self.assertEqual(sorted(items), list(range(10)))
        self.assertEqual(len(set(rng.sample(range(20), 7))), 7)
        self.assertEqual(rng.weighted((("only", 1),)), "only")
        mean = sum(rng.random() for _ in range(4000)) / 4000
        self.assertAlmostEqual(mean, 0.5, delta=0.03)


PINNED_STREAM = [926, 245, 25, 739, 653, 925]  # Rng(7, 'pin').below(1000): the seeding and SplitMix64 contract


class EstimatorCalibrationTests(unittest.TestCase):
    """The .atl size estimator reproduces the design's own worked example (design 3.4, 2,175 bytes)."""

    def test_the_transcribed_design_text_has_the_stated_size(self):
        total = sum(len(text.encode("utf-8")) for text in DESIGN_FILES)
        self.assertLessEqual(abs(total - DESIGN_BYTES), 4, f"design text is {total} bytes, the design says {DESIGN_BYTES}")

    def test_each_section_matches_the_design_text(self):
        design = design_section_bytes()["sections"]
        estimate = synth.estimate_atl_sections(synth.lending_desk("fixed"))
        mapping = {
            "spatial": ("buildings", "floors", "rooms"), "nodes": ("nodes",), "edges": ("edges",), "evidences": ("evidences",),
            "cases": ("cases", "events", "expect"), "interventions": ("interventions",), "requirements": ("requirements",),
        }
        for name, parts in mapping.items():
            expected = sum(design[p] for p in parts)
            self.assertLessEqual(abs(estimate[name] - expected), 2, f"{name}: estimate {estimate[name]}, design {expected}")

    def test_total_is_within_one_percent_of_the_design_example(self):
        design = design_section_bytes()
        design_core = sum(design["sections"].values()) + 9 * 3  # three `#atlas 1` headers
        estimate = synth.estimate_atl(synth.lending_desk("fixed"))
        written = estimate["core"] + estimate["tests"]  # the design example has no tests registry; the fixture adds one row
        comparable = estimate["core"]
        self.assertLessEqual(abs(comparable - design_core) / design_core, 0.01, (comparable, design_core))
        self.assertGreater(written, comparable)  # the added `tests` registry is counted outside core

    def test_empty_sections_write_nothing(self):
        text = synth.atl_sections_text(synth.generate("tiny", family="fixed"))
        self.assertEqual(text["machines"], "")
        self.assertEqual(text["tbds"], "")
        self.assertEqual(text["policies"], "")


class LendingFixtureTests(unittest.TestCase):
    def test_fixture_file_is_the_canonical_form_of_the_generator_output(self):
        data = synth.LENDING_FIXTURE.read_bytes()
        self.assertEqual(data, canon(synth.lending_desk("fixed")),
                         "fixtures/lending_desk.model.json is stale: run `PYTHONPATH=src python tests/atlas/synth.py write-fixture`")
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"}\n") and not data.endswith(b"\n\n"))
        self.assertEqual(am.canonical_bytes(json.loads(data)), data, "the file must already be in canonical form")

    def test_fixture_is_the_worked_example_of_the_design(self):
        m = json.loads(synth.LENDING_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(am.contract_issues(m), [])
        self.assertEqual([n["id"] for n in m["nodes"]], ["svc.loan", "svc.stock", "tbl.loan", "usr.member"])
        self.assertEqual(len(m["edges"]), 3)
        self.assertEqual(len(m["ports"]), 8)
        self.assertEqual(len(m["evidences"]), 3)
        self.assertEqual(sum(len(c["events"]) for c in m["cases"]), 6)
        self.assertEqual([e["id"] for e in m["edges"]], ["usr.member:calls:svc.loan", "svc.loan:calls:svc.stock", "svc.loan:writes:tbl.loan"])
        node = {n["id"]: n for n in m["nodes"]}
        self.assertEqual(node["svc.loan"]["verification"], "Implemented")
        self.assertEqual(node["usr.member"]["verification"], "Inferred")  # the authored override of the design
        edge = {e["id"]: e for e in m["edges"]}
        self.assertEqual(edge["usr.member:calls:svc.loan"]["verification"], "Designed")  # document evidence only
        self.assertEqual({e["evidenceId"]: e["sourceKind"] for e in m["evidences"]},
                         {"EVD-1": "source-code", "EVD-2": "source-code", "EVD-3": "document"})
        self.assertEqual(m["interventions"][0]["interventionId"], "IV-STOCK-DOWN")
        self.assertEqual(m["requirements"][0]["edgeIds"], [e["id"] for e in m["edges"]])  # derived edge list
        self.assertEqual(m["tests"], [{"id": "TS-1", "title": "A member can borrow a copy"}])

    def test_meta_is_what_an_init_call_would_write(self):
        m = json.loads(synth.LENDING_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(m["meta"], {"projectId": "lending-desk", "title": "Library lending desk", "mode": "asis",
                                     "languages": ["en-US"], "profile": "software", "repos": ["main"]})

    def test_expanded_size_is_close_to_the_design_figure(self):
        # design 3.4 states 4,525 bytes for its expansion; this contract adds meta, a tests registry, event/edge fields.
        size = len(canon(synth.lending_desk("fixed")))
        self.assertTrue(3400 <= size <= 5700, f"{size} bytes vs the design's 4,525")

    def test_tiny_with_a_random_family_has_the_same_shape_with_other_ids(self):
        fixed, other = synth.generate("tiny", 4, "fixed"), synth.generate("tiny", 4, "random")
        self.assertEqual(synth.section_counts(fixed), synth.section_counts(other))
        self.assertEqual(am.contract_issues(other), [])
        self.assertTrue(set(n["id"] for n in fixed["nodes"]).isdisjoint(n["id"] for n in other["nodes"]))
        self.assertEqual([n["name"] for n in fixed["nodes"]], [n["name"] for n in other["nodes"]])


class DeterminismTests(unittest.TestCase):
    CASES = [("preset", "tiny"), ("preset", "small"), ("preset", "reference")] + [("edge", name) for name in synth.EDGE_CASES]

    def build(self, kind, name, seed, family="random"):
        return synth.generate(name, seed, family) if kind == "preset" else synth.edge_case(name, seed, family)

    def test_same_seed_gives_identical_bytes(self):
        for kind, name in self.CASES:
            for family in ("random", "fixed"):
                self.assertEqual(canon(self.build(kind, name, 5, family)), canon(self.build(kind, name, 5, family)), (name, family))

    def test_different_seeds_give_different_models(self):
        for kind, name in self.CASES[1:]:
            self.assertNotEqual(canon(self.build(kind, name, 1)), canon(self.build(kind, name, 2)), name)

    def test_models_do_not_depend_on_the_hash_seed(self):
        code = (
            "import sys; sys.path[:0] = [{src!r}, {tests!r}]\n"
            "import synth\n"
            "from gemarc_mcp.atlas import model as am\n"
            "print(am.model_sha(synth.generate('small', 3)), am.model_sha(synth.edge_case('odd-ids', 3)),"
            " am.model_sha(synth.generate('reference', 3, 'fixed')))\n"
        ).format(src=str(SRC), tests=str(HERE))
        outputs = []
        for hash_seed in ("1", "2", "random"):
            env = dict(os.environ, PYTHONHASHSEED=hash_seed)
            run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            outputs.append(run.stdout.strip())
        self.assertEqual(len(set(outputs)), 1, outputs)
        here = " ".join((am.model_sha(synth.generate("small", 3)), am.model_sha(synth.edge_case("odd-ids", 3)),
                         am.model_sha(synth.generate("reference", 3, "fixed"))))
        self.assertEqual(outputs[0], here)

    def test_generation_does_not_mutate_shared_state(self):
        before = canon(synth.generate("small", 9))
        synth.generate("reference", 9)
        synth.edge_case("cjk", 9)
        self.assertEqual(canon(synth.generate("small", 9)), before)


class ScaleTests(unittest.TestCase):
    def test_tiny_and_small_counts(self):
        tiny = synth.section_counts(cached("preset", "tiny", 0, "fixed"))
        self.assertEqual((tiny["nodes"], tiny["edges"], tiny["ports"], tiny["evidences"], tiny["events"]), (4, 3, 8, 3, 6))
        small = synth.section_counts(cached("preset", "small", 0, "random"))
        self.assertEqual(small["nodes"], 40)
        self.assertTrue(70 <= small["edges"] <= 110, small)

    def test_reference_scale_has_the_design_numbers(self):
        for seed in _PRESET_SEEDS:
            for family in ("random", "fixed"):
                c = synth.section_counts(cached("preset", "reference", seed, family))
                self.assertEqual((c["nodes"], c["edges"], c["evidences"], c["cases"], c["events"], c["interventions"],
                                  c["requirements"], c["tbds"]), (169, 374, 242, 23, 322, 21, 16, 23), (seed, family))
                self.assertGreaterEqual(c["machines"], 1)
                self.assertEqual(c["ports"], 2 * (169 - c["machines"]))

    def test_reference_scale_encoded_size_is_within_ten_percent_of_design_3_5(self):
        for seed in range(6):
            sizes = synth.estimate_atl(cached("preset", "reference", seed, "random"))
            self.assertLessEqual(abs(sizes["core"] - DESIGN_35_CORE) / DESIGN_35_CORE, 0.10, (seed, sizes["core"]))

    def test_reference_scale_sections_track_the_design_proportions(self):
        for seed in range(6):
            sizes = synth.estimate_atl(cached("preset", "reference", seed, "random"))
            for name, target in DESIGN_35.items():
                self.assertLessEqual(abs(sizes[name] - target) / target, 0.20, (seed, name, sizes[name], target))

    def test_reference_scale_mixes_verification_levels_and_evidence_kinds(self):
        m = cached("preset", "reference", 1, "random")
        self.assertGreaterEqual(len({n["verification"] for n in m["nodes"]}), 4)
        self.assertGreaterEqual(len({e["verification"] for e in m["edges"]}), 4)
        kinds = Counter(e["sourceKind"] for e in m["evidences"])
        self.assertEqual(set(kinds), {"source-code", "document", "user-requirement"})
        share = kinds["document"] / len(m["evidences"])
        self.assertTrue(0.18 <= share <= 0.40, share)

    def test_about_fifteen_percent_of_records_carry_an_authored_override(self):
        profile = vocab.default_profile()
        for seed in range(4):
            m = cached("preset", "reference", seed, "random")
            ev = {e["evidenceId"]: e for e in m["evidences"]}
            overridden = 0
            for n in m["nodes"]:
                default = am.default_verification_for_node(am.evidence_kinds_of(n["evidenceIds"], ev), m["meta"]["mode"], profile=profile)
                overridden += n["verification"] != default
                if n["verification"] != default:
                    self.assertIn(n["verification"], ("Inferred", "Designed"))
            share = overridden / len(m["nodes"])
            self.assertTrue(0.08 <= share <= 0.22, (seed, share))

    def test_reference_scale_uses_every_intervention_type_and_case_category(self):
        m = cached("preset", "reference", 1, "random")
        self.assertEqual({i["type"] for i in m["interventions"]}, set(vocab.default_profile().ids("interventionTypes")))
        self.assertGreaterEqual(len({c["category"] for c in m["cases"]}), 5)
        self.assertTrue(any(e["id"] != am.edge_id(e["sourceId"], e["relation"], e["targetId"]) for e in m["edges"]),
                        "some parallel edges carry an alias")

    def test_tobe_mode_caps_derived_levels(self):
        m = synth.generate("small", 2, "random", mode="tobe")
        self.assertEqual(m["meta"]["mode"], "tobe")
        self.assertEqual(am.contract_issues(m), [])
        self.assertNotIn("Implemented", {n["verification"] for n in m["nodes"]} | {e["verification"] for e in m["edges"]})


MATRIX = [("preset", name, seed, family) for name in ("tiny", "small", "reference") for seed in _PRESET_SEEDS for family in ("random", "fixed")]
MATRIX += [("edge", name, seed, "random") for name in synth.EDGE_CASES if name != "unknown-values" for seed in (0, 7)]


class ConsistencyTests(unittest.TestCase):
    def test_every_model_satisfies_the_contract(self):
        for key in MATRIX:
            self.assertEqual(am.contract_issues(cached(*key)), [], key)

    def test_every_reference_resolves(self):
        for key in MATRIX:
            self.assertEqual(reference_problems(cached(*key)), [], key)

    def test_authored_ids_are_globally_unique_and_derived_ids_do_not_collide(self):
        for key in MATRIX:
            m = cached(*key)
            ids = all_authored_ids(m)
            self.assertEqual([i for i, n in Counter(ids).items() if n > 1], [], key)
            self.assertTrue(set(p["id"] for p in m["ports"]).isdisjoint(ids), key)

    def test_ports_derivable_equal_stored(self):
        for key in MATRIX:
            m = cached(*key)
            machine_ids = [x["id"] for x in m["machines"]]
            self.assertEqual(am.derive_ports(m["nodes"], machine_ids), m["ports"], key)
            for n in m["nodes"]:
                self.assertEqual(n["portIds"], am.port_ids(n["id"], n["id"] in machine_ids), (key, n["id"]))
            for e in m["edges"]:
                self.assertEqual((e["sourcePortId"], e["targetPortId"]), am.edge_ports(e["sourceId"], e["targetId"], machine_ids), (key, e["id"]))

    def test_edge_ids_static_flag_and_machine_derivations(self):
        profile = vocab.default_profile()
        for key in MATRIX:
            m = cached(*key)
            for e in m["edges"]:
                derived = am.edge_id(e["sourceId"], e["relation"], e["targetId"])
                self.assertTrue(e["id"] == derived or am.is_authored_id(e["id"]), (key, e["id"]))
                self.assertEqual(e["static"], profile.item("relations", e["relation"])["static"], (key, e["id"]))
            for machine in m["machines"]:
                states = am.machine_states(machine["initial"], machine["transitions"])
                self.assertEqual(machine["states"], states, key)
                self.assertEqual(machine["terminal"], am.machine_terminal(states, machine["transitions"]), key)
                self.assertEqual(machine["nodeId"], machine["id"])
            owns = [e for e in m["edges"] if e["relation"] == "owns"]
            self.assertEqual(len(owns), len(m["machines"]), key)

    def test_event_numbering_and_binding(self):
        for key in MATRIX:
            m = cached(*key)
            for c in m["cases"]:
                self.assertEqual([e["seq"] for e in c["events"]], list(range(1, len(c["events"]) + 1)), (key, c["caseId"]))
                self.assertEqual(c["policy"].keys(), am.DEFAULT_POLICY.keys())
            self.assertEqual(unbound_events(m), [], key)

    def test_canonical_json_round_trips(self):
        for key in MATRIX:
            m = cached(*key)
            self.assertEqual(am.canonical_bytes(json.loads(am.canonical_dumps(m))), canon(m), key)

    def test_requirement_edges_are_the_default_derivation(self):
        for key in MATRIX:
            m = cached(*key)
            for r in m["requirements"]:
                self.assertEqual(r["edgeIds"], am.default_requirement_edges(r["nodeIds"], m["edges"]), (key, r["id"]))
            for c in m["cases"]:
                if c["linkedTestIds"]:
                    self.assertEqual(c["linkedTestIds"], am.default_linked_tests(c["requirementIds"], m["requirements"]), (key, c["caseId"]))

    def test_generator_refuses_to_return_a_model_that_breaks_the_contract(self):
        broken = synth.generate("small", 1)
        broken["nodes"][0]["roomId"] = "rm.missing"
        with self.assertRaises(AssertionError):
            synth._finish(broken)


class FamilyTests(unittest.TestCase):
    NODE_PREFIX = re.compile(r"^([^.:]+)\.")

    def prefixes(self, model) -> set[str]:
        out = {self.NODE_PREFIX.match(n["id"]).group(1) for n in model["nodes"]}
        out |= {self.NODE_PREFIX.match(r["id"]).group(1) for part in ("buildings", "floors", "rooms") for r in model["space"][part]}
        return out

    def test_the_fixed_family_is_the_documented_one(self):
        p = synth.Family.fixed().p
        self.assertEqual({p["service"], p["actor"], p["storage"], p["room"], p["building"], p["floor"]}, FIXED_PREFIXES)
        m = cached("preset", "small", 0, "fixed")
        used = self.prefixes(m)
        self.assertLessEqual(used, set(synth.FIXED_PREFIXES.values()))
        self.assertTrue({"svc", "usr", "tbl", "rm", "zone", "lvl"} <= used)

    def test_random_families_never_use_the_fixed_prefixes(self):
        banned = {v.lower() for v in synth.FIXED_PREFIXES.values()}
        for seed in range(80):
            fam = synth.Family.random(seed)
            values = [v.lower() for v in fam.values()]
            self.assertEqual(len(values), len(set(values)), f"seed {seed}: prefixes repeat inside one family")
            self.assertFalse(set(values) & banned, f"seed {seed}")
            self.assertEqual(synth.Family.random(seed), fam)

    def test_two_seeds_give_different_id_prefixes(self):
        a, b = cached("preset", "small", 1, "random"), cached("preset", "small", 2, "random")
        self.assertNotEqual(synth.Family.random(1).prefixes, synth.Family.random(2).prefixes)
        self.assertTrue(self.prefixes(a).isdisjoint(self.prefixes(b)), (self.prefixes(a), self.prefixes(b)))
        ev_a = {re.split(r"[-:]", e["evidenceId"])[0] for e in a["evidences"]}
        ev_b = {re.split(r"[-:]", e["evidenceId"])[0] for e in b["evidences"]}
        self.assertTrue(ev_a.isdisjoint(ev_b))

    def test_random_generation_uses_no_fixed_prefix_anywhere(self):
        for seed in (1, 2, 3):
            m = cached("preset", "reference", seed, "random")
            self.assertFalse(self.prefixes(m) & set(synth.FIXED_PREFIXES.values()), seed)
            for ident in all_authored_ids(m):
                self.assertFalse(ident.startswith(("svc.", "usr.", "tbl.", "rm.", "zone.", "lvl.", "EVD-", "CS-", "RQ-")), (seed, ident))

    def test_a_family_object_can_be_passed_explicitly(self):
        fam = synth.Family.random(11)
        m = synth.generate("small", 3, fam)
        self.assertTrue(all(n["id"].startswith((fam["actor"] + ".", fam["service"] + ".", fam["storage"] + ".", fam["event"] + ".",
                                                 fam["state"] + ".")) for n in m["nodes"]))
        with self.assertRaises(ValueError):
            synth.Family.resolve("nonsense", 0)

    def test_a_family_is_a_pure_function_of_the_seed_and_reads_no_file(self):
        expected = {seed: synth.Family.random(seed) for seed in range(20)}
        with mock.patch("builtins.open", side_effect=AssertionError("Family.random must not read files")), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError), mock.patch.object(Path, "read_bytes", side_effect=AssertionError):
            self.assertEqual({seed: synth.Family.random(seed) for seed in range(20)}, expected)
        self.assertNotIn("denylist", Path(synth.__file__).read_text(encoding="utf-8").lower(),
                         "a generated model must not depend on the denylist: rebuilding that file would silently change every random model")

    def test_random_prefixes_of_the_first_five_thousand_seeds_are_not_denylisted_and_never_repeat(self):
        """Guard for goldens built from random families: a committed model would trip test_denylist at its first scan.

        The exhaustive proof for every possible prefix is ``RandomPrefixTests``; this scan also pins that a family has
        no repeated prefix. If it fails after a denylist rebuild, change the generator deliberately and regenerate
        whatever was built from the old stream; do not filter on the denylist at run time.
        """
        from .test_denylist import digest, load_denylist

        digests, canary = load_denylist()
        denied = digests | {canary}
        for seed in range(5000):
            values = [v.lower() for v in synth.Family.random(seed).values()]
            self.assertEqual(len(values), len(set(values)), f"seed {seed}: prefixes repeat inside one family")
            for value in values:
                self.assertNotIn(digest(value), denied, f"seed {seed}")


class EdgeCaseTests(unittest.TestCase):
    def test_every_edge_case_name_builds(self):
        for name in synth.EDGE_CASES:
            m = synth.edge_case(name, 3)
            self.assertEqual(m["schema"], am.SCHEMA)
        with self.assertRaises(ValueError):
            synth.edge_case("nonsense")

    def test_big400(self):
        m = cached("edge", "big400", 0, "random")
        self.assertEqual(len(m["nodes"]), 400)
        self.assertGreaterEqual(len(m["edges"]), 800)

    def test_unknown_values_break_only_vocabulary_membership(self):
        m = synth.edge_case("unknown-values", 4)
        issues = am.contract_issues(m)
        self.assertTrue(issues)
        self.assertEqual({i["code"] for i in issues}, {am.E_ENUM}, issues)
        paths = {i["path"] for i in issues}
        self.assertIn(f"$.space.buildings[{len(m['space']['buildings']) - 1}].trust", paths)
        self.assertIn("$.space.rooms[0].layer", paths)
        self.assertIn("$.space.rooms[1].tier", paths)
        self.assertEqual(len([p for p in paths if p.endswith(".kind")]), 3)
        self.assertEqual(len([p for p in paths if p.endswith(".relation")]), 1)
        self.assertEqual(len(paths), 7, paths)
        self.assertEqual(reference_problems(m), [])

    def test_no_cases_and_no_interventions(self):
        m = cached("edge", "no-cases", 0, "random")
        self.assertEqual((m["cases"], m["interventions"]), ([], []))
        self.assertTrue(m["requirements"] and m["nodes"])
        m = cached("edge", "no-interventions", 0, "random")
        self.assertEqual(m["interventions"], [])
        self.assertTrue(m["cases"])

    def test_empty_strings(self):
        m = cached("edge", "empty-strings", 0, "random")
        self.assertEqual(m["meta"]["title"], "")
        self.assertTrue(any(n["sub"] == "" for n in m["nodes"]))
        self.assertTrue(any(not n["evidenceIds"] for n in m["nodes"]))
        self.assertTrue(any(i["note"] == "" for i in m["interventions"]))
        self.assertTrue(any(e["repository"] == "" for e in m["evidences"]))

    def test_cjk_text_with_ascii_ids(self):
        m = cached("edge", "cjk", 0, "random")
        cjk = re.compile(r"[぀-ヿ㐀-鿿]")
        self.assertTrue(all(cjk.search(n["name"]) and cjk.search(n["sub"]) for n in m["nodes"] if n["sub"]))
        self.assertTrue(all(cjk.search(c["title"]) for c in m["cases"]))
        self.assertTrue(all(ident.isascii() for ident in all_authored_ids(m)))
        self.assertEqual(m["meta"]["languages"], ["zh-CN", "ja-JP"])
        self.assertEqual(am.canonical_bytes(json.loads(am.canonical_dumps(m))), canon(m))

    def test_ids_with_dots_colons_and_dashes(self):
        m = cached("edge", "odd-ids", 0, "random")
        ids = all_authored_ids(m)
        self.assertTrue(any(":" in i for i in ids))
        self.assertTrue(all(":" not in n["id"] for n in m["nodes"]), "node ids never contain a colon")
        self.assertTrue(any("." in n["id"] and "-" in n["id"] for n in m["nodes"]))
        self.assertTrue(any("_" in n["id"] for n in m["nodes"]))
        long_nodes = [n for n in m["nodes"] if len(n["id"]) >= 55]
        self.assertGreaterEqual(len(long_nodes), 3)
        self.assertTrue(any(e["id"].startswith(am.HASH_ID_PREFIX) for e in m["edges"]), "an over-long edge id falls back to a hash id")
        self.assertTrue(any(e["id"] != am.edge_id(e["sourceId"], e["relation"], e["targetId"]) and not e["id"].startswith("h:") for e in m["edges"]))

    def test_state_machine_node(self):
        m = cached("edge", "machine", 0, "random")
        self.assertGreaterEqual(len(m["machines"]), 3)
        machine_nodes = [n for n in m["nodes"] if n["kind"] == "State"]
        self.assertEqual({n["id"] for n in machine_nodes}, {x["id"] for x in m["machines"]})
        self.assertTrue(all(n["portIds"] == [] for n in machine_nodes))
        self.assertTrue(any("guard" in t for x in m["machines"] for t in x["transitions"]))
        self.assertTrue(any(i["type"] == "change_guard" for i in m["interventions"]))
        self.assertTrue(any(e["type"] == "Transition" for c in m["cases"] for e in c["events"]))
        self.assertTrue(any(x["terminal"] for x in m["machines"]))
        owns = [e for e in m["edges"] if e["relation"] == "owns"]
        self.assertTrue(all(e["targetPortId"] is None for e in owns))

    def test_insert_element_intervention(self):
        m = cached("edge", "insert-element", 0, "random")
        inserted = [i for i in m["interventions"] if i["type"] == "insert_element"]
        self.assertEqual(len(inserted), 1)
        element, rewrite = inserted[0]["element"], inserted[0]["rewrite"]
        self.assertEqual([p["id"] for p in element["inputPorts"] + element["outputPorts"]], [f"{element['elementId']}:in", f"{element['elementId']}:out"])
        self.assertEqual([r["chain"][0]["id"] for r in rewrite], [f"{element['elementId']}:1"])
        self.assertEqual([c["id"] for c in rewrite[0]["chain"]], [f"{element['elementId']}:1", f"{element['elementId']}:2"])
        self.assertNotIn(element["elementId"], {n["id"] for n in m["nodes"]})

    def test_element_with_typed_ports_and_several_rewrites(self):
        m = cached("edge", "element-ports", 0, "random")
        inserted = [i for i in m["interventions"] if i["type"] == "insert_element"]
        self.assertEqual(len(inserted), 1)
        element, rewrite = inserted[0]["element"], inserted[0]["rewrite"]
        self.assertEqual({p["type"] for p in element["inputPorts"] + element["outputPorts"]}, {"Record", "Any"})
        self.assertGreaterEqual(len(rewrite), 2)
        self.assertEqual([r["disposition"] for r in rewrite][:2], ["replace", "replace"])
        self.assertEqual([c["id"] for r in rewrite[:2] for c in r["chain"]], [f"{element['elementId']}:{n}" for n in (1, 2, 3, 4)])
        if len(rewrite) > 2:
            self.assertEqual((rewrite[2]["disposition"], rewrite[2]["chain"]), ("keep", []))


def optional_features(model: dict) -> set[str]:
    """Names of the optional contract features a model actually uses (events, props, edges, evidence, interventions)."""
    found: set[str] = set()
    for case in model["cases"]:
        for event in case["events"]:
            found |= {f"event.{key}" for key in event if key not in ("seq", "type", "from", "to")}
            found.add(f"event.type.{event['type']}")
    for node in model["nodes"]:
        found |= {f"props.{key}" for key in node["props"]}
    for edge in model["edges"]:
        found |= {f"edge.{key}" for key in ("cardinality", "group") if key in edge}
    for evidence in model["evidences"]:
        if evidence["lineRange"] is not None:
            found.add("evidence.lineRange")
    for intervention in model["interventions"]:
        found.add(f"intervention.{intervention['type']}")
        found |= {f"rewrite.{rewrite['disposition']}" for rewrite in intervention.get("rewrite", [])}
        found |= {f"part.{part['type']}" for part in intervention.get("parts", [])}
        found |= {f"intervention.{key}" for key in ("targetEventId", "targetRuleId", "targetEdgeId") if key in intervention}
    return found


#: What the checker, the engine, the documents and the diagrams read; every one must be exercised by some generated model.
REQUIRED_FEATURES = {
    "event.when", "event.guard", "event.errorCode", "event.type.Compensation", "event.dedupKey", "event.edge", "event.fault",
    "event.latencyMs", "event.ack", "event.group", "event.section", "event.role", "event.params", "event.ms", "event.ruleId",
    "props.dedup", "props.owner", "props.store", "props.enums", "props.columns", "props.methods", "props.aggregator", "props.tier",
    "edge.cardinality", "edge.group", "evidence.lineRange",
    "rewrite.replace", "rewrite.keep", "rewrite.disable", "part.insert_element", "part.change_guard", "part.disable_module",
    "intervention.targetEventId", "intervention.targetRuleId", "intervention.targetEdgeId",
} | {f"intervention.{name}" for name in vocab.default_profile().ids("interventionTypes")}


class FeatureCoverageTests(unittest.TestCase):
    def test_the_union_of_the_presets_and_edge_cases_uses_every_optional_field(self):
        found: set[str] = set()
        for seed in (0, 1, 2):
            for preset in ("small", "reference"):
                found |= optional_features(cached("preset", preset, seed, "random"))
            for name in synth.EDGE_CASES:
                found |= optional_features(cached("edge", name, seed, "random"))
        missing = REQUIRED_FEATURES - found
        self.assertEqual(missing, set(), "no generated model exercises: " + ", ".join(sorted(missing)))

    def test_the_rich_edge_case_alone_exercises_them_for_every_seed(self):
        needed = REQUIRED_FEATURES - {"props.columns", "props.methods", "props.aggregator", "event.group", "event.section", "event.role",
                                      "event.params", "event.fault", "event.ack", "event.ms", "edge.group", "intervention.targetEdgeId"}
        for seed in range(6):
            found = optional_features(synth.edge_case("rich-fields", seed))
            self.assertEqual(needed - found, set(), seed)

    def test_the_rich_edge_case_follows_the_event_and_guard_rules_it_claims(self):
        m = synth.edge_case("rich-fields", 3)
        case_by_id = {c["caseId"]: c for c in m["cases"]}
        named = [iv for iv in m["interventions"] if "targetEventId" in iv]
        self.assertTrue(named)
        for iv in named:
            case_id, seq = am.parse_event_id(iv["targetEventId"])
            self.assertIn(case_id, iv["appliesTo"])
            self.assertLessEqual(seq, len(case_by_id[case_id]["events"]))
        deduplicated = {n["id"] for n in m["nodes"] if "dedup" in n["props"]}
        keyed = [e for c in m["cases"] for e in c["events"] if "dedupKey" in e and e["to"] in deduplicated]
        self.assertTrue(keyed, "events into a node with props.dedup carry a dedupKey")
        repeated = [(c["caseId"], e["to"], e["dedupKey"]) for c in m["cases"] for e in c["events"] if "dedupKey" in e]
        self.assertTrue(len(repeated) != len(set(repeated)), "at least one dedup key is used twice in a case")
        self.assertTrue(any(n["props"].get("dedup", {}).get("key") for n in m["nodes"]))

    def test_every_generated_model_with_cases_names_at_least_one_edge_explicitly(self):
        for seed in (0, 1, 2):
            for name in ("small", "reference"):
                self.assertIn("event.edge", optional_features(cached("preset", name, seed, "random")), (name, seed))
            for name in synth.EDGE_CASES:
                model = cached("edge", name, seed, "random")
                if model["cases"] and name != "unknown-values":
                    self.assertIn("event.edge", optional_features(model), (name, seed))

    def test_element_ports_edge_case_holds_a_replace_a_keep_and_a_disable_entry(self):
        m = synth.edge_case("element-ports", 0)
        dispositions = [r["disposition"] for r in m["interventions"][-1]["rewrite"]]
        self.assertEqual(dispositions[-2:], ["keep", "disable"])
        self.assertIn("replace", dispositions)

    def test_stamped_evidence_exists_only_where_asked_for(self):
        self.assertNotIn("evidence.lineRange", optional_features(cached("preset", "reference", 0, "random")))
        stamped = [e for e in synth.edge_case("rich-fields", 0)["evidences"] if e["lineRange"] is not None]
        self.assertTrue(stamped)
        for e in stamped:
            self.assertTrue(1 <= e["lineRange"][0] <= e["lineRange"][1])


class ReferenceDistributionTests(unittest.TestCase):
    def test_case_lengths_have_a_heavy_tail_between_4_and_48_events(self):
        for seed in _PRESET_SEEDS:
            lengths = sorted(len(c["events"]) for c in cached("preset", "reference", seed, "random")["cases"])
            self.assertEqual((lengths[0], lengths[-1], sum(lengths), len(lengths)), (4, 48, 322, 23), seed)
            self.assertLess(lengths[len(lengths) // 2], 20, "the median case is short")
            self.assertGreaterEqual(len({n for n in lengths}), 8, "the lengths are spread, not constant")

    def test_machines_have_between_3_and_10_transitions_and_both_ends_are_used(self):
        sizes = set()
        for seed in range(6):
            sizes |= {len(m["transitions"]) for m in synth.generate("reference", seed, "random")["machines"]}
        self.assertTrue(sizes <= set(range(3, 11)), sizes)
        self.assertTrue(min(sizes) <= 4 and max(sizes) >= 9, sizes)

    def test_the_reference_preset_carries_guards_whens_and_dedup_at_reference_rates(self):
        events = [e for seed in _PRESET_SEEDS for c in cached("preset", "reference", seed, "random")["cases"] for e in c["events"]]
        counts = Counter(key for e in events for key in e)
        self.assertGreaterEqual(counts["when"], 15)       # the reference has 19 of 322 per model
        self.assertLessEqual(counts["when"], 80)
        self.assertGreaterEqual(counts["dedupKey"], 15)   # about 10 per model
        self.assertGreaterEqual(counts["guard"], 1)
        self.assertLessEqual(counts["guard"], 30)

    def test_the_small_preset_keeps_its_even_case_lengths(self):
        lengths = {len(c["events"]) for c in cached("preset", "small", 0, "random")["cases"]}
        self.assertEqual(lengths, {12, 13})


class SpreadHelperTests(unittest.TestCase):
    def test_lengths_sum_exactly_and_stay_in_range(self):
        for seed in range(30):
            rng = synth.Rng(seed, "spread")
            for count, total in ((23, 322), (9, 160), (5, 62), (2, 52), (7, 7 * 48), (7, 7 * 4)):
                lengths = synth._spread_lengths(rng, count, total)
                self.assertEqual((len(lengths), sum(lengths)), (count, total))
                self.assertTrue(all(4 <= n <= 48 for n in lengths), lengths)
        self.assertEqual(synth._spread_lengths(synth.Rng(0), 0, 10), [])
        self.assertEqual(sum(synth._spread_lengths(synth.Rng(0), 3, 5)), 5)  # infeasible range: falls back to an even split


class RandomPrefixTests(unittest.TestCase):
    def test_every_prefix_has_the_documented_shape(self):
        for seed in range(300):
            for field_name, value in synth.Family.random(seed).prefixes:
                pattern = r"[A-Z]{2}[0-9]" if field_name in synth.UPPER_FIELDS else r"[a-z]{2}[0-9]"
                self.assertRegex(value, "^" + pattern + "$", (seed, field_name))

    def test_no_prefix_the_generator_can_ever_produce_is_a_token_of_the_hashed_list(self):
        """Exhaustive: the prefix space has 6,760 words, so a clash is excluded for every seed, not just the seeds tried.

        If this fails after the token list was rebuilt, shrink the space in ``synth.PREFIX_LETTERS`` /
        ``synth.PREFIX_DIGITS`` (do not filter on the list at run time) and regenerate whatever was built from the old stream.
        """
        from .test_denylist import digest, load_denylist, tokenize

        digests, canary = load_denylist()
        denied = digests | {canary}
        prefixes = synth.all_random_prefixes()
        self.assertEqual(len(prefixes), 26 * 26 * 10)
        for prefix in prefixes:
            for spelling in (prefix, prefix.upper()):
                for text in (spelling, spelling + "-1", spelling + ".x"):
                    for token in tokenize(text):
                        if len(token) >= 2:
                            self.assertNotIn(digest(token), denied, f"prefix #{prefixes.index(prefix)} clashes")


class FixtureHygieneTests(unittest.TestCase):
    def test_no_generated_text_looks_like_a_filesystem_path_or_a_timestamp(self):
        m = cached("preset", "reference", 1, "random")
        text = am.canonical_dumps(m)
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]")
        self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:")

    def test_generator_module_is_stdlib_plus_the_atlas_model_only(self):
        source = (HERE / "synth.py").read_text(encoding="utf-8")
        imports = set(re.findall(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.MULTILINE))
        allowed = {"__future__", "copy", "hashlib", "json", "re", "sys", "dataclasses", "pathlib", "typing", "gemarc_mcp.atlas"}
        self.assertLessEqual(imports, allowed, imports - allowed)


if __name__ == "__main__":
    unittest.main()
