"""Wave-1 rules (design M2 (a), zero false positives).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_rules.py"

* The registry holds exactly the 31 rules of design 5.1 wave 1.
* ``MUTATIONS`` is the data table of acceptance (a): one mutation of a clean base per rule; the findings that are not
  derived must be exactly ``{rule}``. Three bases: the lending desk, the desk plus a clean machine, and the desk in to-be mode.
* Every base is clean (no diagnostic of any severity) in slice and full mode.
* Valid synthetic atlases (30 random families, small and reference scale) give no ERROR, and every WARN and INFO equals what
  the generator wrote (computed independently of the rules).

Synthetic id families only.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
import copy

from gemarc_mcp.atlas import encode, expand, slicecheck
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.rules import completeness
from gemarc_mcp.atlas.rules import registry as reg

from . import synth

DESK = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
META = json.loads((DESK / "meta.json").read_text(encoding="utf-8"))

WAVE1 = (
    "ST-001 ST-002 ST-003 ST-004 ST-005 ST-006 ST-007 ST-008 ST-009 DV-001 SZ-001 RF-001 RF-002 RF-003 RF-004 RF-005 "
    "EV-001 EV-002 EV-006 EV-007 EV-008 EV-009 EV-010 EV-011 SM-001 SM-002 SM-003 SM-005 CS-004 RQ-001 RQ-002"
).split()

MACHINE = (
    "#atlas 1\n"
    "## nodes: id | name | sub | opts\n"
    "st.loan | Loan state | Open, returned or lost. | room=rm.desk kind=State ev=EVD-1\n"
    "## machines: id | initial | opts\n"
    "st.loan | Open | owner=svc.loan\n"
    "## transitions: from | to | opts\n"
    "Open | Returned | machine=st.loan\n"
    "Open | Lost | machine=st.loan\n"
)


def desk(extra: "dict[str, str] | None" = None, edits=(), tobe: bool = False) -> dict:
    """The lending desk sources (``name -> text``), optionally with more files, text edits ``(file, old, new)`` and to-be mode."""
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(DESK.glob("*.atl"))}
    files.update(extra or {})
    if tobe:
        files["00-space.atl"] = files["00-space.atl"].replace("#atlas 1\n", "#atlas 1 mode=tobe\n", 1)
    for name, old, new in edits:
        if old not in files[name]:
            raise AssertionError(f"edit target not found in {name}: {old!r}")
        files[name] = files[name].replace(old, new, 1)
    return files


BASES = {
    "desk": lambda edits=(), extra=None, tobe=False: desk(extra, edits, tobe),
    "machine": lambda edits=(), extra=None, tobe=False: desk({"15-machine.atl": MACHINE, **(extra or {})}, edits, tobe),
}

S, M = "20-scenarios.atl", "10-structure.atl"
IV = "IV-STOCK-DOWN | disable_module | Stock service is down while borrowing | "
PAD = "// padding line to make the slice larger\n" * 260

# rule -> (base, mode, edits, extra files, options). The non-derived rule ids found must be exactly {rule}.
MUTATIONS = [
    ("ST-001", "desk", "full", [(S, "#atlas 1\n", "")], {}, {}),
    ("ST-002", "desk", "full", [(M, "svc.stock | Stock service |", "svc.loan | Twin | A repeated id.\nsvc.stock | Stock service |")], {}, {}),
    ("ST-003", "desk", "full", [(M, "kind=Module", "kind=Modul")], {}, {}),
    ("ST-004", "desk", "full", [(S, "target=svc.stock applies", "foo=1 target=svc.stock applies")], {}, {}),
    ("ST-005", "desk", "full", [(S, IV + "target=svc.stock applies=CS-BORROW-OK class=DIVERGED terminal={\"svc.stock\":\"Blocked\"}",
                                  "IV-STOCK-DOWN | disable_module")], {}, {}),
    ("ST-006", "desk", "full", [(S, "Stock service is down while borrowing |", "Stock service is down while borrowing why=none |")], {}, {}),
    ("ST-007", "desk", "full", [(S, "terminal={\"svc.stock\":\"Blocked\"}", "terminal={\"svc.stock\":")], {}, {}),
    ("ST-008", "desk", "full", [], {"90-notes.txt": "plain text is not a slice\n"}, {}),
    ("ST-009", "desk", "full", [], {}, {"red_drafts": ["20-scenarios.atl"]}),
    ("DV-001", "desk", "full", [(M, "write loan row | ev=EVD-1", "write loan row | ev=EVD-1 id=twin")], {}, {}),
    ("SZ-001", "desk", "full", [(S, "#atlas 1\n", "#atlas 1\n" + PAD)], {}, {}),
    ("RF-001", "desk", "full", [(M, "@set room=rm.desk kind=Module", "@set room=rm.desx kind=Module")], {}, {}),
    ("RF-002", "desk", "full", [(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |")], {}, {}),
    ("RF-003", "desk", "full", [(M, "borrow a copy | ev=EVD-3", "borrow a copy | ev=EVD-9")], {}, {}),
    ("RF-004", "desk", "full", [(S, "Call | usr.member | svc.loan |", "Call | usr.membre | svc.loan |")], {}, {}),
    ("RF-005", "desk", "full", [(S, "tests=TS-1", "tests=TS-9")], {}, {}),
    ("EV-001", "desk", "full", [(M, "ev=EVD-3 verif=Inferred", "ev=EVD-3 verif=Implemented")], {}, {}),
    ("EV-002", "desk", "full", [(M, "shelf. | ev=EVD-2", "shelf. | ev=EVD-2 verif=Designed")], {}, {}),
    ("EV-006", "desk", "full", [(M, "EVD-1 | src/loan.py | def create_loan( |", "EVD-1 | src/loan.py | api_key=AbCdEf0123456789xYz |")], {}, {}),
    ("EV-007", "desk", "full", [], {}, {"meta": {"repos": []}}),
    ("EV-008", "desk", "full", [(M, "EVD-3 | docs/lending.md | borrower opens the page | the borrower actor",
                                  "EVD-3 | docs/lending.md | borrower opens the page | the borrower actor\nEVD-4 | src/spare.py | def spare( | nothing cites it")], {}, {}),
    ("EV-009", "desk", "full", [(M, "shelf. | ev=EVD-2", "shelf. |")], {}, {}),
    ("EV-010", "desk", "full", [(M, "shelf. | ev=EVD-2", "shelf. | ev=EVD-2 verif=Implemented")], {}, {"tobe": True}),
    ("EV-011", "desk", "full", [(M, "check copy status | ev=EVD-2", "check copy status | ev=^")], {}, {}),
    ("SM-001", "machine", "full", [("15-machine.atl", "Open | Returned | machine=st.loan", "Open | Returned | machine=st.loam")], {}, {}),
    ("SM-002", "machine", "full", [("15-machine.atl", "Open | Lost | machine=st.loan\n", "Open | Lost | machine=st.loan\nArchived | Returned | machine=st.loan\n")], {}, {}),
    ("SM-003", "machine", "full", [("15-machine.atl", "st.loan | Open | owner=svc.loan", "st.loan | Open")], {}, {}),
    ("SM-005", "machine", "full", [("15-machine.atl", "Open | Lost | machine=st.loan\n", "Open | Lost | machine=st.loan\nReturned | Open | machine=st.loan\nLost | Open | machine=st.loan\n")], {}, {}),
    ("CS-004", "desk", "slice", [(S, "req=RQ-1 ev=EVD-1", "ev=EVD-1")], {}, {}),
    ("RQ-001", "desk", "full", [(S, "interview 1 |", "interview 1 | tests=TS-1\nRQ-2 | A second requirement | interview 2 |"),
                                (S, "req=RQ-1 ev", "req=RQ-1,RQ-2 ev")], {}, {}),
    ("RQ-002", "desk", "full", [(S, "tests=TS-1", "tests=TS-1\nRQ-2 | A second requirement | interview 2 | nodes=svc.loan")], {}, {}),
]


def run(base: str, edits, extra, mode: str, opts) -> slicecheck.CheckResult:
    opts = dict(opts)
    meta = dict(META, **opts.pop("meta", {}))
    files = BASES[base](edits, extra, tobe=opts.pop("tobe", False))
    return slicecheck.check(files, meta, mode=mode, **opts)


class RegistryTests(unittest.TestCase):
    def test_wave_one_is_exactly_the_31_rules_of_the_design(self):
        self.assertEqual(sorted(reg.wave_ids(1)), sorted(WAVE1))
        self.assertEqual(len(WAVE1), 31)

    def test_every_rule_has_wave_severity_hint_and_summary(self):
        for r in reg.rules():
            self.assertIn(r.severity, dg.SEVERITIES, r.id)
            self.assertIn(r.wave, (1, 2, 3), r.id)
            self.assertTrue(r.hint and r.summary, r.id)
            self.assertLessEqual(len(r.hint), dg.HINT_LIMIT, r.id)
            self.assertEqual(r.id.split("-")[0] in reg.PHASE_ORDER, True, r.id)

    def test_default_severity_comes_from_the_registry(self):
        self.assertEqual(dg.diag("EV-009", "p", "f:1", 1, "x", "h").severity, dg.WARN)
        self.assertEqual(dg.diag("RF-002", "p", "f:1", 1, "x", "h").severity, dg.ERROR)
        self.assertEqual(dg.diag("EV-008", "p", "f:1", 1, "x", "h").severity, dg.INFO)

    def test_every_check_function_reports_registered_rules(self):
        for _, _, phase, ids, _ in reg.CHECKS:
            for rule_id in ids:
                self.assertIn(rule_id, reg.REGISTRY)
                self.assertEqual(rule_id.split("-")[0], phase if phase != "RF" else "RF")

    def test_phases_run_root_cause_first(self):
        order = [c[2] for c in reg.CHECKS]
        self.assertEqual(order, sorted(order, key=reg.PHASE_ORDER.index))
        self.assertLess(order.index("RF"), order.index("EV"))
        self.assertLess(order.index("EV"), order.index("SM"))

    def test_hints_that_need_values_name_them(self):
        self.assertIn("rm.x", reg.hint_of("RF-001", detail="rm.x"))
        self.assertIn("{count}", reg.hint_of("EV-011"))  # an unfilled placeholder stays visible, never raises


class BaseCleanTests(unittest.TestCase):
    def test_every_base_is_clean_in_both_modes(self):
        for base in BASES:
            for mode in ("slice", "full"):
                result = run(base, (), {}, mode, {})
                self.assertEqual([(d.rule, d.at) for d in result.diagnostics], [], f"{base}/{mode}")
                self.assertEqual((result.pending, result.pending_bind), ({}, 0), f"{base}/{mode}")

    def test_the_to_be_base_is_clean(self):
        for mode in ("slice", "full"):
            self.assertEqual(run("desk", (), {}, mode, {"tobe": True}).diagnostics, [])


class MutationTests(unittest.TestCase):
    def test_table_covers_every_wave_one_rule_once(self):
        self.assertEqual(sorted(m[0] for m in MUTATIONS), sorted(WAVE1))

    def test_each_mutation_triggers_exactly_its_rule(self):
        failures = []
        for rule_id, base, mode, edits, extra, opts in MUTATIONS:
            result = run(base, edits, extra, mode, opts)
            found = result.rule_ids()
            if found != {rule_id}:
                failures.append(f"{rule_id}: got {sorted(found)}: " + "; ".join(f"{d.rule} {d.path} {d.got}" for d in result.diagnostics[:4]))
        self.assertEqual(failures, [])

    def test_each_mutation_has_the_registered_severity(self):
        for rule_id, base, mode, edits, extra, opts in MUTATIONS:
            result = run(base, edits, extra, mode, opts)
            self.assertEqual({d.severity for d in result.diagnostics if d.rule == rule_id and not d.derived},
                             {reg.rule(rule_id).severity}, rule_id)

    def test_every_diagnostic_has_a_location_inside_the_sources(self):
        for rule_id, base, mode, edits, extra, opts in MUTATIONS:
            result = run(base, edits, extra, mode, opts)
            for d in result.diagnostics:
                file, _, line = d.at.rpartition(":")
                self.assertTrue(line.isdigit() and file, (rule_id, d.at))

    def test_two_defects_print_at_most_three_lines(self):
        files = desk(edits=[(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |"),
                            (M, "borrow a copy | ev=EVD-3", "borrow a copy | ev=EVD-9")])
        result = slicecheck.check(files, META, mode="full")
        text = result.render()
        self.assertLessEqual(len(text.splitlines()), 3, text)
        self.assertLessEqual(len(text.encode()) // 3, 1000)


class RuleBoundaryTests(unittest.TestCase):
    """Edges of the rules that a one-defect fixture cannot show (found by a mutation check of this lane)."""

    def found(self, files, mode="full", meta=META):
        return slicecheck.check(files, meta, mode=mode)

    def test_to_be_implemented_without_code_evidence_is_ev010_only(self):
        files = desk(edits=[(M, "ev=EVD-3 verif=Inferred", "ev=EVD-3 verif=Implemented")], tobe=True)
        self.assertEqual(self.found(files).rule_ids(), {"EV-010"})
        self.assertEqual(self.found(desk(edits=[(M, "ev=EVD-3 verif=Inferred", "ev=EVD-3 verif=Implemented")])).rule_ids(), {"EV-001"})

    def test_a_stated_hypothetical_node_without_evidence_is_not_warned_about(self):
        files = desk(edits=[(M, "shelf. | ev=EVD-2", "shelf. | verif=Hypothetical")])
        self.assertEqual(self.found(files).diagnostics, [])
        self.assertEqual(self.found(desk(edits=[(M, "shelf. | ev=EVD-2", "shelf. |")])).rule_ids(), {"EV-009"})

    def test_a_machine_cannot_own_another_machine(self):
        aux = "st.aux | Aux state | Helper machine. | room=rm.desk kind=State ev=EVD-1\n"
        files = desk()
        files["15-machine.atl"] = (MACHINE.replace("st.loan | Loan state", aux + "st.loan | Loan state")
                                   .replace("st.loan | Open | owner=svc.loan", "st.aux | Open | owner=svc.loan\nst.loan | Open | owner=st.aux")
                                   .replace("Open | Returned | machine=st.loan", "Open | Returned | machine=st.loan\nOpen | Done | machine=st.aux"))
        result = self.found(files)
        self.assertEqual(result.rule_ids(), {"SM-003"})
        (d,) = result.errors
        self.assertEqual((d.path, d.got), ("machines[st.loan].owner", "st.aux"))

    def test_two_floors_of_one_building_may_not_share_a_level(self):
        files = desk(edits=[("00-space.atl", "lvl.out | zone.ext", "lvl.core2 | zone.lib | Annex | 1\nlvl.out | zone.ext")])
        result = self.found(files)
        self.assertEqual(result.rule_ids(), {"RF-001"})
        self.assertIn("same level as lvl.core", result.errors[0].hint)
        # the same level in another building is fine
        self.assertEqual(self.found(desk(edits=[("00-space.atl", "lvl.out | zone.ext | Visitors | 0", "lvl.out | zone.ext | Visitors | 1")])).diagnostics, [])


def ids_of(model: dict, collection: str, key: str = "id") -> set:
    return {r[key] for r in model[collection]}


class SyntheticAtlasTests(unittest.TestCase):
    """Valid synthetic atlases: no ERROR, and every WARN / INFO is one the generator wrote on purpose."""

    SEEDS = range(1, 31)

    def expected(self, model: dict, files: dict) -> dict:
        """Counts of the findings that the generated data implies, computed from the model without the rules."""
        kinds = {e["evidenceId"]: e["sourceKind"] for e in model["evidences"]}
        cited = set()
        for coll in ("nodes", "edges", "cases", "machines", "tbds", "requirements"):
            for r in model[coll]:
                cited.update(r["evidenceIds"])
        code = lambda r: any(kinds[x] == "source-code" for x in r["evidenceIds"])
        by_id = {n["id"]: n for n in model["nodes"]}
        owned = {m["id"] for m in model["machines"]}
        # an edge the encoder writes as ``ev=^``: it carries evidence and exactly the union of its endpoints' (owns edges are generated)
        inherited = [e for e in model["edges"] if e["relation"] != "owns" and e["evidenceIds"]
                     and list(e["evidenceIds"]) == am.inherit_edge_evidence(by_id[e["sourceId"]]["evidenceIds"], by_id[e["targetId"]]["evidenceIds"])]
        inherited_ids = {e["id"] for e in inherited}
        named = {x for r in model["requirements"] for x in r["nodeIds"]}
        traced = {x for c in model["cases"] for x in c["requirementIds"]}
        return {
            "EV-009": sum(1 for n in model["nodes"] if not n["evidenceIds"] and n["verification"] == "Hypothetical"),
            "EV-008": len(set(kinds) - cited),
            "SM-005": sum(1 for m in model["machines"] if not m["terminal"] and m["transitions"]),
            "SZ-001": sum(1 for t in files.values() if len(t.encode()) > 8192),
            "EV-002": sum(1 for r in model["nodes"] + model["edges"] if r["verification"] == "Designed" and code(r) and r.get("id") not in inherited_ids),
            "EV-011": len(inherited),
            "RQ-002": int(any(r["id"] not in traced for r in model["requirements"]))
                      + int(bool(model["requirements"]) and any(n["id"] not in named and n["id"] not in owned for n in model["nodes"])),
        }

    def check_one(self, preset: str, seed: int) -> None:
        model = synth.generate(preset, seed)
        files = encode.encode_atl(model)
        meta = encode.meta_of(model)
        want = self.expected(model, files)
        for mode in ("slice", "full"):
            result = slicecheck.check(files, meta, mode=mode)
            where = f"{preset}/{seed}/{mode}"
            self.assertEqual(result.errors, [], where)
            by_rule: dict[str, int] = {}
            for d in result.diagnostics:
                by_rule[d.rule] = by_rule.get(d.rule, 0) + 1
            self.assertEqual(result.pending, {}, where)
            self.assertEqual(result.derived_count, 0, where)
            allowed = {"EV-002", "EV-008", "EV-009", "EV-011", "SM-005", "SZ-001", "RQ-002"}
            self.assertLessEqual(set(by_rule), allowed, f"{where}: unexpected rules {sorted(set(by_rule) - allowed)}")
            # every count equals what the generated data implies, computed from the model without the rules
            for rule_id in ("EV-009", "EV-008", "SM-005", "SZ-001", "EV-002"):
                self.assertEqual(by_rule.get(rule_id, 0), want[rule_id], f"{where} {rule_id}")
            inherited = sum(int(d.got.split()[0]) for d in result.diagnostics if d.rule == "EV-011")  # one finding per file: "N edges"
            self.assertEqual(inherited, want["EV-011"], f"{where} EV-011")
            self.assertEqual(by_rule.get("RQ-002", 0), want["RQ-002"] if mode == "full" else 0, f"{where} RQ-002")

    def check_completeness(self, preset: str, seed: int) -> None:
        """Which slots block on a valid synthetic atlas: only what the generator leaves out of the documents."""
        model = synth.generate(preset, seed)
        ex = expand.expand_sources(encode.encode_atl(model), encode.meta_of(model))
        report = completeness.completeness_report(ex.model, ex.provenance)
        want = {("node.columns", n["id"]) for n in model["nodes"]
                if n["kind"] in ("Entity", "Table") and not (isinstance(n["props"].get("columns"), list) and n["props"]["columns"])}
        if not any(p["kind"] == "nfr" for p in model["policies"]):
            want.add(("ch9.nfr", ""))
        self.assertEqual({(e.slot, e.ref) for e in report.blockers()}, want, f"{preset}/{seed}")

    def test_small_atlases_30_seeds(self):
        for seed in self.SEEDS:
            self.check_one("small", seed)
            self.check_completeness("small", seed)

    def test_reference_scale_atlases_30_seeds(self):
        for seed in self.SEEDS:
            self.check_one("reference", seed)
            self.check_completeness("reference", seed)

    def test_edge_case_presets_have_no_error_except_the_vocabulary_probe(self):
        for name in synth.EDGE_CASES:
            if name == "empty-strings":
                continue  # an empty positional text cannot be written in .atl (EncodeError by design)
            model = synth.edge_case(name, 1)
            result = slicecheck.check(encode.encode_atl(model), encode.meta_of(model), mode="full")
            if name == "unknown-values":
                self.assertEqual({d.rule for d in result.errors}, {"ST-003"}, name)
                self.assertEqual(len(result.errors), 7, name)  # the 7 E_ENUM issues of the contract checker
            else:
                self.assertEqual(result.errors, [], name)


class HostileInputTests(unittest.TestCase):
    """Damaged text never raises: every mutation is checked in both modes and rendered as text and json."""

    @staticmethod
    def lcg(seed: int):
        state = seed
        while True:
            state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
            yield state >> 33

    def mutate(self, text: str, draw) -> str:
        lines = text.split("\n")
        kind = next(draw) % 6
        if kind == 0 and text:
            at = next(draw) % len(text)
            return text[:at] + text[at + 1:]
        if kind == 1:
            at = next(draw) % len(lines)
            return "\n".join(lines[:at] + [lines[at]] + lines[at:])
        if kind == 2 and len(lines) > 2:
            a, b = next(draw) % len(lines), next(draw) % len(lines)
            lines[a], lines[b] = lines[b], lines[a]
            return "\n".join(lines)
        if kind == 3:
            return text[: next(draw) % (len(text) + 1)]
        if kind == 4 and text:
            at = next(draw) % len(text)
            return text[:at] + "|" + text[at:]
        at = next(draw) % len(lines)
        return "\n".join(lines[:at] + lines[at + 1:])

    def test_300_damaged_inputs_do_not_raise_and_stay_in_budget(self):
        draw = self.lcg(20261030)
        bases = [BASES["desk"](), BASES["machine"](), {k: v for k, v in encode.encode_atl(synth.generate("small", 2)).items()}]
        for index in range(300):
            files = dict(bases[index % len(bases)])
            name = sorted(files)[next(draw) % len(files)]
            files[name] = self.mutate(files[name], draw)
            meta = META if index % len(bases) < 2 else encode.meta_of(synth.generate("small", 2))
            for mode in ("slice", "full"):
                result = slicecheck.check(files, meta, mode=mode)
                text, data = result.render(), json.loads(result.render("json"))
                self.assertLessEqual(len(text.encode()), dg.TEXT_BUDGET, (index, mode))
                self.assertLessEqual(len(data["diagnostics"]), 24)
                for d in result.diagnostics:
                    self.assertIn(d.severity, dg.SEVERITIES)
                    self.assertTrue(d.rule in reg.REGISTRY or d.rule in dg.STRUCTURAL_RULES, d.rule)
            completeness.completeness_report(slicecheck.check(files, meta, mode="full").expansion.model)


class CompletenessTests(unittest.TestCase):
    NFR = [(S, "tests=TS-1", "tests=TS-1\n## policies: id | text | opts\nPOL-1 | Answers stay quick. | src=RQ-1 kind=nfr by=user")]

    def report(self, files, meta=META):
        ex = expand.expand_sources(files, meta)
        return ex, completeness.completeness_report(ex.model, ex.provenance)

    def test_the_desk_blocks_only_on_the_missing_nfr_chapter(self):
        _, r = self.report(desk())
        self.assertEqual([(e.slot, e.status, e.severity) for e in r.blockers()], [("ch9.nfr", "MISSING", "BLOCK")])
        self.assertFalse(r.ready)

    def test_an_nfr_policy_clears_the_block(self):
        _, r = self.report(desk(edits=self.NFR))
        self.assertTrue(r.ready, r.render())
        self.assertEqual(r.tally["ch9.nfr"], {"OK": 1})

    def test_statuses_and_defaults(self):
        ex, r = self.report(desk(edits=self.NFR))
        self.assertEqual(r.tally["node.verification"], {"DEFAULTED": 3, "OK": 1})  # usr.member states verif=Inferred
        self.assertEqual(r.tally["ch8.machines"], {"N/A": 1})
        self.assertEqual({(e.slot, e.status) for e in r.entries if e.severity == "GAP"}, set())
        self.assertTrue(all(e.status in completeness.STATUSES and e.severity in completeness.SEVERITIES for e in r.entries))
        located = [e for e in r.entries if e.slot == "node.verification"]
        self.assertTrue(all(e.at.startswith("10-structure.atl:") for e in located))

    def test_unresolved_room_and_blank_text_block(self):
        ex, _ = self.report(desk(edits=self.NFR + [(M, "@set room=rm.desk kind=Module", "@set room=rm.desx kind=Module")]))
        r = completeness.completeness_report(ex.model, ex.provenance)
        self.assertEqual({(e.slot, e.status) for e in r.blockers()}, {("node.room", "UNRESOLVED_REF")})
        self.assertEqual(r.by_level()["BD"]["BLOCK"], 3)
        self.assertEqual(r.by_level()["REQ"]["BLOCK"], 0)  # the room is printed from the building-design level on
        model = copy.deepcopy(ex.model)
        model["nodes"][0]["sub"] = "  "
        blank = completeness.completeness_report(model)
        self.assertIn(("node.sub", "MISSING"), {(e.slot, e.status) for e in blank.blockers()})
        self.assertIn("[NOT-AUTHORED:node.sub@" + model["nodes"][0]["id"] + "]", [e.note for e in blank.blockers()])

    def test_a_data_table_without_columns_is_not_authored(self):
        ex, _ = self.report(desk(edits=self.NFR + [(M, "@set kind=Storage", "@set kind=Table")]))
        r = completeness.completeness_report(ex.model)
        self.assertEqual([(e.slot, e.ref) for e in r.blockers()], [("node.columns", "tbl.loan")])
        self.assertEqual(r.tally["ch6.data"], {"OK": 1})

    def test_the_report_is_pure_and_deterministic(self):
        ex, first = self.report(desk())
        before = json.dumps(ex.model, sort_keys=True)
        again = completeness.completeness_report(ex.model, ex.provenance)
        self.assertEqual(json.dumps(ex.model, sort_keys=True), before)
        self.assertEqual(first.to_dict(), again.to_dict())
        json.dumps(first.to_dict())

    def test_synthetic_atlas_has_no_unresolved_reference_and_counts_every_record(self):
        for seed in (1, 2, 3):
            model = synth.generate("small", seed)
            r = completeness.completeness_report(model)
            self.assertNotIn("UNRESOLVED_REF", {e.status for e in r.entries}, seed)
            self.assertEqual(sum(r.tally["node.sub"].values()), len(model["nodes"]))
            self.assertEqual(sum(r.tally["edge.endpoints"].values()), len(model["edges"]))
            self.assertEqual(sum(r.tally["case.expect"].values()), len(model["cases"]))

    def test_render_is_short_and_lists_blockers_first(self):
        _, r = self.report(desk())
        lines = r.render().splitlines()
        self.assertLessEqual(len(lines), 9)
        self.assertTrue(lines[1].startswith("BLOCK ch9.nfr"))


if __name__ == "__main__":
    unittest.main()
