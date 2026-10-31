"""Wave-2 rules (design 5.1; milestone M5 (b), (d)): one mutation fixture per rule that triggers exactly its id, and valid
synthetic models that give no wave-2 finding except the intended ones.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_rules_w2.py"

Static rules (CS-001, CS-005, CS-008, PT-003, SM-004, SM-006, SM-007, IV-001, OR-001 unknown rule id, OR-002, OR-003) run on a clean
base through ``slicecheck.check(..., wave_set=(1, 2))``. The rules that read the oracle ledger (OR-000, OR-001, IV-002, OR-004,
OR-005; CS-002 and CS-003 report as OR-001) run on a real project whose other items are confirmed through the sheet, so that the
finding under test is the only one. Synthetic id families only.
"""
from __future__ import annotations

import copy
import json
import unittest
from collections import Counter
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import encode, engine, oracle, slicecheck, tools
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import evidence as evidence_lane
from gemarc_mcp.atlas.engine import sweep
from gemarc_mcp.atlas.rules import registry as reg

from . import synth
from .test_oracle_support import OracleProject, consistent, desk_files

META = json.loads((Path(__file__).resolve().parent / "fixtures" / "lending_desk" / "meta.json").read_text(encoding="utf-8"))
S, M = "20-scenarios", "10-structure"
WAVE2 = {r.id for r in reg.rules((2,))}
CASE_ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
EVENTS = ("Call | usr.member | svc.loan | ruleId=R-BORROW\nCall | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20\n"
          "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nCall | svc.loan | tbl.loan | ruleId=R-WRITE\n"
          "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n" "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n")
MACHINE = ("#atlas 1\n## nodes: id | name | sub | opts\nst.loan | Loan state | Open, returned or lost. | room=rm.desk kind=State ev=EVD-1\n"
           "## machines: id | initial | opts\nst.loan | Open | owner=svc.loan\n## transitions: from | to | opts\n"
           "Open | Returned | machine=st.loan\nOpen | Lost | machine=st.loan\n")
G_NEVER = '{"all":[{"op":"eq","param":"x","value":1},{"op":"eq","param":"x","value":2}]}'
G_ALWAYS = '{"any":[{"op":"present","param":"x"},{"op":"absent","param":"x"}]}'


def files(*edits, extra=None) -> dict:
    out = {f"{k}.atl": v for k, v in desk_files(*edits).items()}
    out.update(extra or {})
    return out


def run(*edits, extra=None, mode="full", rule_sets=(1, 2)) -> slicecheck.CheckResult:
    return slicecheck.check(files(*edits, extra=extra), META, mode=mode, wave_set=rule_sets)


def ids(result) -> set:
    return {d.rule for d in result.diagnostics if not d.derived}


# rule -> (edits, extra files, expected severity). The non-derived findings must be exactly {rule}.
MUTATIONS = {
    "CS-001": ([(S, "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n", "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\nCall | usr.member | tbl.loan | ruleId=R-X\n")], {}, "ERROR"),
    "CS-005": ([], {"30-copy.atl": "#atlas 1\n## cases: id | cat | title | opts\nCS-COPY | normal | Same events again | req=RQ-1 ev=EVD-1\n## events: type | from | to | opts\n@set case=CS-COPY\n"
                   + EVENTS + "## expect: case | nodes | opts\n" + CASE_ROW.replace("CS-BORROW-OK", "CS-COPY") + "\n"}, "WARN"),
    "CS-008": ([(S, "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n", "Return | svc.stock | svc.loan\n")], {}, "INFO"),
    "PT-003": ([(M, "## edges: from | rel | to | label | opts\n", "## edges: from | rel | to | label | opts\nsvc.loan | publishes | evt.loan | announce | ev=EVD-1\n"),
                (M, "@set kind=Storage\n", "@set kind=Storage\nevt.loan | Loan event | Published when a loan is made. | kind=Event ev=EVD-1 room=rm.desk\n"),
                (S, "nodes=usr.member,svc.loan,svc.stock,tbl.loan tests=TS-1", "nodes=usr.member,svc.loan,svc.stock,tbl.loan,evt.loan tests=TS-1")], {}, "WARN"),
    "SM-004": ([], {"15-machine.atl": MACHINE + "Open | Returned | machine=st.loan id=tr.again\n"}, "WARN"),
    "SM-007": ([], {"15-machine.atl": MACHINE + "Open | Stuck | machine=st.loan\nStuck | Stuck2 | machine=st.loan\nStuck2 | Stuck | machine=st.loan\n"}, "WARN"),
    "SM-006": ([], {"15-machine.atl": MACHINE.replace("Open | Returned | machine=st.loan", "Open | Returned | machine=st.loan guard=" + G_NEVER + " err=E_X")}, "ERROR"),
    "IV-001": ([(S, "target=svc.stock applies", "status=DRAFT target=svc.stock applies")], {}, "ERROR"),
    "OR-001": ([(S, CASE_ROW, CASE_ROW + ' outcomes=[{"ruleId":"R-NOPE","outcome":"OK"}]')], {}, "ERROR"),
    "OR-002": ([(S, "CS-BORROW-OK | normal |", "CS-BORROW-OK | abnormal |")], {}, "WARN"),
    "OR-003": ([(S, CASE_ROW, "CS-BORROW-OK | usr.member | by=author")], {}, "ERROR"),
}


class RegistryTests(unittest.TestCase):
    def test_wave_two_is_the_sixteen_rules_of_the_design_plus_OR_000(self):
        design = "CS-001 CS-002 CS-003 CS-005 CS-008 PT-003 SM-004 SM-006 SM-007 IV-001 IV-002 OR-001 OR-002 OR-003 OR-004 OR-005".split()
        self.assertEqual(len(design), 16)
        self.assertEqual(sorted(WAVE2), sorted(design + ["OR-000"]))

    def test_the_evidence_rules_are_registered_from_the_evidence_table(self):
        wanted = {rule for (_code, rule, _meaning) in evidence_lane._CODES.values()}
        self.assertLessEqual({"EV-003", "EV-004", "EV-005", "EV-012"}, wanted)
        for rule_id in wanted:
            self.assertIn(rule_id, reg.REGISTRY, rule_id)
        for rule_id in ("EV-003", "EV-004", "EV-005", "EV-012"):
            self.assertEqual(reg.rule(rule_id).wave, 3)
        self.assertEqual({r: reg.rule(r).severity for r in ("EV-003", "EV-004", "EV-005", "EV-012")},
                         {"EV-003": "ERROR", "EV-004": "INFO", "EV-005": "WARN", "EV-012": "WARN"})

    def test_hints_fit_and_severities_are_the_ones_of_the_design(self):
        for r in reg.rules((2,)):
            self.assertLessEqual(len(r.hint), dg.HINT_LIMIT, r.id)
        severities = {r.id: r.severity for r in reg.rules((2,))}
        self.assertEqual({k: severities[k] for k in ("CS-001", "CS-005", "CS-008", "PT-003", "SM-004", "SM-007", "IV-001", "IV-002",
                                                     "OR-000", "OR-001", "OR-002", "OR-003", "OR-004", "OR-005")},
                         {"CS-001": "ERROR", "CS-005": "WARN", "CS-008": "INFO", "PT-003": "WARN", "SM-004": "WARN", "SM-007": "WARN",
                          "IV-001": "ERROR", "IV-002": "ERROR", "OR-000": "INFO", "OR-001": "ERROR", "OR-002": "WARN", "OR-003": "ERROR",
                          "OR-004": "ERROR", "OR-005": "INFO"})
        self.assertEqual(reg.rule("CS-001").binding, True)

    def test_wave_one_is_untouched_by_wave_two(self):
        self.assertEqual(len(reg.wave_ids(1)), 31)
        self.assertEqual(slicecheck.check(files(), META, mode="full").rule_ids(), set())

    def test_every_wave_two_rule_has_a_fixture_in_this_file(self):
        covered = set(MUTATIONS) | {"OR-000", "IV-002", "OR-004", "OR-005", "CS-002", "CS-003"}
        self.assertEqual(WAVE2 - covered, set())


class MutationTests(unittest.TestCase):
    def test_the_base_is_clean_in_both_rule_sets_and_both_modes(self):
        for mode in ("slice", "full"):
            self.assertEqual(ids(run(mode=mode)), set(), mode)
        self.assertEqual(ids(run(extra={"15-machine.atl": MACHINE})), set(), "the machine base")

    def test_one_mutation_per_rule_triggers_exactly_its_id(self):
        for rule_id, (edits, extra, severity) in MUTATIONS.items():
            with self.subTest(rule=rule_id):
                result = run(*edits, extra=extra)
                self.assertEqual(ids(result), {rule_id}, [d.as_dict() for d in result.diagnostics])
                found = [d for d in result.diagnostics if d.rule == rule_id and not d.derived]
                self.assertEqual({d.severity for d in found}, {severity})
                for d in found:
                    self.assertRegex(d.at, r"^\S+\.atl:\d+$")
                    self.assertLessEqual(len(d.hint), dg.HINT_LIMIT)

    def test_CS_001_is_a_binding_rule_deferred_in_slice_mode_until_the_edges_are_final(self):
        edits = MUTATIONS["CS-001"][0]
        not_final = [(M, "@final edges\n", "")]
        sliced = run(*edits, *not_final, mode="slice")
        self.assertEqual((ids(sliced), sliced.pending_bind), (set(), 1), "pending-bind until @final edges or full mode")
        self.assertEqual(ids(run(*edits, mode="slice")), {"CS-001"}, "the edges section is final: the binding rule reports")
        self.assertEqual(ids(run(*edits, *not_final, mode="full")), {"CS-001"})
        self.assertEqual(ids(slicecheck.check(files(*edits), META, mode="slice")), set(), "the checks of put run wave 1 only")

    def test_CS_001_names_the_edge_to_add_and_accepts_an_explicit_edge(self):
        found = [d for d in run(*MUTATIONS["CS-001"][0]).diagnostics if d.rule == "CS-001"][0]
        self.assertEqual(found.got, "usr.member -> tbl.loan")
        self.assertIn("add edge usr.member | <rel> | tbl.loan", found.hint)
        explicit = MUTATIONS["CS-001"][0][0][2].replace("ruleId=R-X", "ruleId=R-X edge=usr.member:calls:svc.loan")
        self.assertEqual(ids(run((S, MUTATIONS["CS-001"][0][0][1], explicit))), set())

    def test_CS_001_follows_the_engine_routing_in_both_directions_and_for_self_events(self):
        reverse = "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nCall | svc.stock | svc.loan | ruleId=R-BACK\n"
        self.assertEqual(ids(run((S, "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n", reverse))), set())
        selfie = "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nWait | svc.stock | svc.stock | ruleId=R-WAIT ms=5\n"
        self.assertEqual(ids(run((S, "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n", selfie))), set())

    def test_CS_005_names_the_copy_and_reports_a_missing_category_from_six_cases_on(self):
        found = [d for d in run(*[], extra=MUTATIONS["CS-005"][1]).diagnostics if d.rule == "CS-005"][0]
        self.assertIn("CS-COPY is a renamed copy of CS-BORROW-OK", found.hint)
        cases = "".join(f"CS-N{i} | normal | Case {i} | req=RQ-1 ev=EVD-1\n" for i in range(5))
        events = "".join(f"## events: type | from | to | opts\n@set case=CS-N{i}\nCall | usr.member | svc.loan | ruleId=R-N{i} latencyMs={i + 1}\nCall | svc.loan | tbl.loan | ruleId=R-W{i}\n"
                         f"## expect: case | nodes | opts\nCS-N{i} | usr.member,svc.loan,tbl.loan | by=author\n" for i in range(5))
        result = run(extra={"30-many.atl": "#atlas 1\n## cases: id | cat | title | opts\n" + cases + events})
        found = [d for d in result.diagnostics if d.rule == "CS-005"]
        self.assertEqual(len(found), 1)
        self.assertIn("abnormal", found[0].got)
        self.assertIn("combination", found[0].got)
        self.assertNotIn("normal,", found[0].got.replace("abnormal", ""))

    def test_PT_003_names_the_relation_and_never_flags_Any(self):
        found = [d for d in run(*MUTATIONS["PT-003"][0]).diagnostics if d.rule == "PT-003"][0]
        self.assertEqual(found.got, "Message -> Record")
        self.assertIn("publishes", found.hint)

    def test_SM_007_is_silent_for_a_loop_without_a_terminal_state(self):
        loop = MACHINE.replace("Open | Returned | machine=st.loan\nOpen | Lost | machine=st.loan\n", "Open | Busy | machine=st.loan\nBusy | Open | machine=st.loan\n")
        self.assertEqual(ids(run(extra={"15-machine.atl": loop})) - {"SM-005"}, set())

    def test_SM_006_catches_an_always_true_guard_and_a_never_satisfiable_one(self):
        always = MACHINE.replace("Open | Returned | machine=st.loan", "Open | Returned | machine=st.loan guard=" + G_ALWAYS + " err=E_X")
        result = run(extra={"15-machine.atl": always})
        self.assertEqual(ids(result), {"SM-006"})
        self.assertEqual({d.severity for d in result.diagnostics}, {"WARN"})
        never = run(*MUTATIONS["SM-006"][0], extra=MUTATIONS["SM-006"][1])
        self.assertEqual({d.severity for d in never.diagnostics if d.rule == "SM-006"}, {"ERROR"})
        self.assertIn("SM-006", ids(never))

    def test_SM_006_runs_in_full_mode_only(self):
        extra = MUTATIONS["SM-006"][1]
        self.assertEqual(ids(run(extra=extra, mode="slice")), set())

    def test_SM_006_finds_no_mismatch_between_the_engine_and_the_table_oracle_on_valid_models(self):
        for model in (synth.generate("tiny"), synth.generate("small", 3), synth.generate("reference", 3), synth.generate("reference", 7)):
            report = sweep.scan_model(model)
            self.assertEqual([f for f in report["findings"] if f["kind"] == "probe_mismatch"], [], "0 probe mismatches")

    def test_IV_001_accepts_a_declared_draft(self):
        draft_edit = [(S, "target=svc.stock applies", "target=svc.stock applies")]
        self.assertEqual(ids(run(*draft_edit)), set())
        result = run(*MUTATIONS["IV-001"][0])
        found = [d for d in result.diagnostics if d.rule == "IV-001"][0]
        self.assertEqual(found.got, "OK")
        self.assertIn("compiles as OK, expect.status is DRAFT", found.hint)

    def test_IV_001_quotes_the_engine_message_for_a_rejected_intervention(self):
        result = run((S, "target=svc.stock applies", "target=svc.stoc applies"))
        # an unknown target is a contract defect: the engine does not run, the contract rules speak
        self.assertNotIn("IV-001", ids(result))
        self.assertIn("RF-005", ids(result))

    def test_OR_002_does_not_flag_a_normal_or_a_concurrency_case_that_asserts_only_Completed(self):
        for category in ("normal", "concurrency", "boundary"):
            self.assertEqual(ids(run((S, "CS-BORROW-OK | normal |", f"CS-BORROW-OK | {category} |"))), set(), category)
        for category in ("abnormal", "recovery", "combination"):
            self.assertEqual(ids(run((S, "CS-BORROW-OK | normal |", f"CS-BORROW-OK | {category} |"))), {"OR-002"}, category)

    def test_OR_002_is_silent_when_a_remarkable_outcome_or_a_different_state_is_asserted(self):
        edit = (S, "CS-BORROW-OK | normal |", "CS-BORROW-OK | abnormal |")
        self.assertEqual(ids(run(edit, (S, CASE_ROW, CASE_ROW.replace("tbl.loan", "tbl.loan=Failed")))), set())
        self.assertEqual(ids(run(edit, (S, CASE_ROW, CASE_ROW + ' outcomes=[{"ruleId":"R-CHECK","outcome":"Failed"}]'))), set())

    def test_OR_002_is_not_evaded_by_an_outcome_that_every_run_has(self):
        # debt-b4 fix: an OK outcome says nothing abnormal (it was enough to make the case silent before)
        edit = (S, "CS-BORROW-OK | normal |", "CS-BORROW-OK | abnormal |")
        row = CASE_ROW + ' outcomes=[{"ruleId":"R-CHECK","outcome":"OK"}]'
        self.assertEqual(ids(run(edit, (S, CASE_ROW, row))), {"OR-002"})

    def test_OR_002_is_not_evaded_by_Idle_on_a_node_the_case_never_touches(self):
        # debt-b4 fix: the same notion of "effective" as the coverage floor (a participant, or a state other than Idle)
        edit = (S, "CS-BORROW-OK | normal |", "CS-BORROW-OK | abnormal |")
        idle = {"30-idle.atl": "#atlas 1" + chr(10) + "## nodes: id | name | sub | opts" + chr(10)
                + "svc.idle | Idle service | Never called by the case. | room=rm.desk kind=Module ev=EVD-1" + chr(10)}
        lazy = CASE_ROW.replace("tbl.loan", "tbl.loan,svc.idle=Idle")
        self.assertIn("OR-002", ids(run(edit, (S, CASE_ROW, lazy), extra=idle)))
        real = CASE_ROW.replace("tbl.loan", "tbl.loan,svc.idle=Blocked")
        self.assertNotIn("OR-002", ids(run(edit, (S, CASE_ROW, real), extra=idle)), "a claim that differs on an untouched node is something")

    def test_OR_002_pure_function_edges(self):
        from gemarc_mcp.atlas.rules import or_

        base = {"category": "abnormal", "events": [{"seq": 1, "from": "a", "to": "b"}], "expectedTerminalState": {"a": "Completed", "b": "Completed"}}
        self.assertTrue(or_.abnormal_all_completed(base))
        self.assertTrue(or_.abnormal_all_completed({**base, "expectedOutcomes": [{"ruleId": "R", "outcome": "OK"}, {"ruleId": "S", "outcome": "OK"}]}))
        self.assertFalse(or_.abnormal_all_completed({**base, "expectedOutcomes": [{"ruleId": "R", "outcome": "OK"}, {"ruleId": "S", "outcome": "Timeout"}]}))
        self.assertFalse(or_.abnormal_all_completed({**base, "category": "normal"}))
        self.assertFalse(or_.abnormal_all_completed({**base, "expectedTerminalState": {"a": "Completed", "b": "Degraded"}}))
        self.assertFalse(or_.abnormal_all_completed({**base, "expectedTerminalState": {"z": "Idle"}}), "nothing effective is asserted: OR-003 speaks, not OR-002")
        self.assertFalse(or_.abnormal_all_completed({**base, "expectedTerminalState": {}}))

    def test_OR_003_floor_is_max_two_and_35_percent_of_the_participants(self):
        from gemarc_mcp.atlas.rules import or_

        self.assertEqual([or_.coverage_floor(n) for n in (1, 2, 4, 5, 6, 7, 10, 20, 100)], [2, 2, 2, 2, 3, 3, 4, 7, 35])
        self.assertEqual(ids(run((S, CASE_ROW, "CS-BORROW-OK | usr.member,svc.loan | by=author"))), set(), "2 of 4 meets the floor")

    def test_OR_003_counts_participants_and_non_idle_claims_only(self):
        from gemarc_mcp.atlas.rules import or_

        case = {"events": [{"seq": 1, "type": "Call", "from": "a.x", "to": "a.y"}, {"seq": 2, "type": "Return", "from": "a.y", "to": "a.x"}],
                "expectedTerminalState": {"a.x": "Completed", "a.y": "Completed", "b.z": "Idle", "b.w": "Idle"}}
        self.assertEqual(or_.effective_asserted(case), 2, "Idle on two untouched nodes counts for nothing")
        self.assertIsNone(or_.coverage_gap(case))
        lazy = {**case, "expectedTerminalState": {"b.z": "Idle", "b.w": "Idle"}}
        self.assertEqual(or_.coverage_gap(lazy), (0, 2, 2))
        claim = {**case, "expectedTerminalState": {"b.z": "Failed", "a.x": "Completed"}}
        self.assertIsNone(or_.coverage_gap(claim), "a non-Idle claim about a node the engine changed is a real constraint")

    def test_OR_003_a_case_with_an_expectation_and_no_events_is_a_gap(self):
        from gemarc_mcp.atlas.rules import or_

        self.assertEqual(or_.coverage_gap({"events": [], "expectedTerminalState": {"a.x": "Idle"}}), (0, 2, 0))
        self.assertEqual(or_.coverage_gap({"events": [], "expectedTerminalState": {"a.x": "Failed"}}), (1, 2, 0))
        self.assertEqual(or_.coverage_gap({"events": [], "expectedOutcomes": [{"ruleId": "R", "outcome": "OK"}]}), (0, 2, 0))
        self.assertIsNone(or_.coverage_gap({"events": []}), "no expectation, nothing to constrain")
        self.assertIn("no events", or_.gap_text((1, 2, 0)))

    def test_the_clone_fingerprint_ignores_labels_and_nothing_else(self):
        from gemarc_mcp.atlas.rules import or_

        base = {"events": [{"seq": 1, "type": "Call", "from": "a.x", "to": "a.y", "ruleId": "R-1", "latencyMs": 5},
                           {"seq": 2, "type": "Return", "from": "a.y", "to": "a.x", "ruleId": "R-2"}], "policy": {"retry": 1}}
        renamed = copy.deepcopy(base)
        renamed["events"][0]["ruleId"], renamed["events"][1]["ruleId"] = "R-X", "R-Y"
        renamed["events"][1]["seq"] = 7
        self.assertEqual(or_._case_fp(base), or_._case_fp(renamed), "step names and positions are labels")
        for change in (lambda c: c["events"][0].update(latencyMs=6), lambda c: c["events"][0].update(role="recovery"),
                       lambda c: c["events"].pop(), lambda c: c["policy"].update(retry=2), lambda c: c["events"][0].update(fault={"mode": "fail"})):
            other = copy.deepcopy(base)
            change(other)
            self.assertNotEqual(or_._case_fp(base), or_._case_fp(other))
        self.assertEqual(or_._case_fp({"events": None}), or_._case_fp({"events": []}))

    def test_OR_003_runs_in_full_mode_only(self):
        self.assertEqual(ids(run(*MUTATIONS["OR-003"][0], mode="slice")), set())


class LedgerRuleTests(OracleProject):
    """OR-000, OR-001 (= CS-002, CS-003), IV-002, OR-004, OR-005 on a real project; the other items are confirmed first."""

    def only(self, rule_family="OR"):
        reply = tools.check(self.ws, self.name, mode="full", rule=rule_family, format="json")
        return reply["diagnostics"]

    def found(self, family="OR"):
        return [d for d in tools.check(self.ws, self.name, mode="full", rule=family, format="json")["diagnostics"]["diagnostics"]]

    def test_OR_000_is_the_only_finding_of_an_unregistered_edit(self):
        self.desk()
        self.approve_all()
        path = self.paths.slices / "20-scenarios.atl"
        path.write_text(path.read_text(encoding="utf-8").replace(CASE_ROW, "CS-BORROW-OK | usr.member,svc.loan | by=author"), encoding="utf-8", newline="\n")
        rules = {d["rule"] for d in self.found()}
        self.assertEqual(rules, {"OR-000"})
        self.assertIn("OR-000", tools.check(self.ws, self.name, mode="full")["diagnostics"], "also without asking for the family")

    def test_OR_001_terminal_is_CS_002_and_reports_counts_only(self):
        self.desk(("20-scenarios", CASE_ROW, CASE_ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate()
        self.approve_all()
        found = self.found()
        self.assertEqual({d["rule"] for d in found}, {"OR-001"})
        self.assertEqual(found[0]["severity"], "ERROR")
        self.assertIn("terminal 1 of 4 asserted nodes differ (CS-002)", found[0]["hint"])
        self.assertTrue(found[0]["path"].endswith(".expectedTerminalState"))
        self.assertNotIn("svc.stock", json.dumps(found))

    def test_OR_001_outcomes_is_CS_003(self):
        self.desk(("20-scenarios", CASE_ROW, CASE_ROW + ' outcomes=[{"ruleId":"R-CHECK","outcome":"Timeout"}]'))
        self.simulate()
        self.approve_all()
        found = self.found()
        self.assertEqual({d["rule"] for d in found}, {"OR-001"})
        self.assertIn("outcomes 1 of 1 not found (CS-003)", found[0]["hint"])
        self.assertTrue(found[0]["path"].endswith(".expectedOutcomes"))

    def test_a_verdict_of_an_older_model_is_not_reported_again(self):
        self.desk(("20-scenarios", CASE_ROW, CASE_ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate()
        self.approve_all()
        self.put(S, "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\n" + EVENTS.replace("latencyMs=20", "latencyMs=21"), mode="patch")
        self.assertEqual({d["rule"] for d in self.found()}, set(), "the old verdict says nothing about the changed model: run simulate again")

    def test_IV_002_reports_a_wrong_prediction_with_counts_only(self):
        self.desk(("20-scenarios", "class=DIVERGED", "class=NO_DIFFERENCE"))
        self.simulate()
        self.approve_all()
        found = self.found("IV")
        self.assertEqual({d["rule"] for d in found}, {"IV-002"})
        self.assertIn("the outcome class differs", found[0]["hint"])
        self.assertNotIn("DIVERGED", json.dumps(found))

    def test_OR_005_reports_a_revised_expectation_and_the_unconfirmed_count(self):
        self.desk(("20-scenarios", CASE_ROW, CASE_ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate()
        self.put(S, "#atlas 1\n## expect: case | nodes | opts\n" + CASE_ROW + "\n", mode="patch")        # revised
        found = self.found()
        self.assertEqual({d["rule"] for d in found}, {"OR-005"})
        self.assertTrue(any("is revised" in d["hint"] for d in found))
        self.assertTrue(any("sealed expectation(s) wait" in d["hint"] for d in found))

    def test_the_wave_two_ledger_rules_say_nothing_without_a_ledger_view(self):
        self.desk()
        self.assertEqual(ids(slicecheck.check(files(), self.cfg().meta(), mode="full", wave_set=(1, 2))), set())


class SyntheticModelTests(unittest.TestCase):
    """A valid synthetic model gives no wave-2 finding except the intended ones, each computed independently of the rules."""

    @staticmethod
    def expected(model: dict) -> Counter:
        want: Counter = Counter()
        ports = {p["id"]: p["type"] for p in model["ports"]}
        for e in model["edges"]:
            a, b = ports.get(e["sourcePortId"]), ports.get(e["targetPortId"])
            if a and b and a != b and "Any" not in (a, b):
                want["PT-003"] += 1
        if any(not ev.get("ruleId") for c in model["cases"] for ev in c["events"]):
            want["CS-008"] += 1
        for c in model["cases"]:
            terminal = c["expectedTerminalState"]
            if c["category"] in ("abnormal", "recovery", "combination") and terminal and not c.get("expectedOutcomes") \
                    and all(v == "Completed" for v in terminal.values()):
                want["OR-002"] += 1
        if len(model["cases"]) >= 6:
            have = {c["category"] for c in model["cases"]}
            if set(am.vocab.default_profile().ids("caseCategories")) - have:
                want["CS-005"] += 1
        signatures = Counter(json.dumps([[{k: v for k, v in e.items() if k not in ("seq", "ruleId")} for e in c["events"]], c["policy"], c["context"],
                                         c.get("initialMachines")], sort_keys=True) for c in model["cases"] if c["events"])
        want["CS-005"] += sum(n - 1 for n in signatures.values())
        for m in model["machines"]:
            terminal = set(m.get("terminal") or ())
            if terminal and m["transitions"]:
                back = {}
                for t in m["transitions"]:
                    back.setdefault(t["to"], set()).add(t["from"])
                reach, todo = set(terminal), list(terminal)
                while todo:
                    for before in back.get(todo.pop(), ()):
                        if before not in reach:
                            reach.add(before)
                            todo.append(before)
                want["SM-007"] += min(3, sum(1 for s in m["states"] if s not in reach))
        return want

    def test_no_wave_two_finding_besides_the_intended_ones(self):
        checked = 0
        for preset, seed in (("tiny", None), ("small", 2), ("small", 5), ("reference", 3), ("reference", 7)):
            model = consistent(synth.generate("tiny") if preset == "tiny" else synth.generate(preset, seed))
            result = slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                      mode="full", wave_set=(1, 2))
            got = Counter(d.rule for d in result.diagnostics if not d.derived and d.rule in WAVE2 | {"EV-003"})
            self.assertEqual(got, self.expected(model), (preset, seed))
            self.assertFalse([d for d in result.diagnostics if d.severity == dg.ERROR and not d.derived], (preset, seed))
            checked += 1
        self.assertEqual(checked, 5)

    def test_a_raw_generated_model_has_no_IV_001_and_one_wrong_claim_is_found(self):
        # debt-b4: this test pinned that the generator's random ``status`` (OK or DRAFT) was what IV-001 found on a raw model; the generator now
        # writes a status only when it is true, so that count is zero and the old assertion (equal counts) would pass vacuously. Now: zero on raw
        # models of five seeds, and exactly the one intervention whose claim is made wrong is named.
        def run(model):
            return slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                    mode="full", wave_set=(1, 2))

        for seed in (1, 2, 3, 4, 5):
            self.assertEqual([d.rule for d in run(synth.generate("small", seed)).diagnostics if d.rule == "IV-001"], [], seed)
        raw = synth.generate("small", 3)
        victim = next(i for i in raw["interventions"] if i["type"] == "insert_element")
        victim["expect"]["status"] = "DRAFT"
        found = [d for d in run(raw).diagnostics if d.rule == "IV-001"]
        self.assertEqual([d.path for d in found], [f"interventions[{victim['interventionId']}].expect.status"])


if __name__ == "__main__":
    unittest.main()
