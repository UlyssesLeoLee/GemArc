"""The oracle ledger and its services (design section 7, milestone M5 (c)): sealing, ORACLE_FIRST, blind verdicts, reveals, the
``revised`` rule, the confirmation lane, the release gate. The scripted cheaters are in ``test_oracle_cheaters.py``.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_oracle.py"

Everything runs on the synthetic lending desk (a ruleId on every event). Synthetic id families only.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import cli, oracle, slicecheck, tools
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas.derive import diagrams
from gemarc_mcp.atlas.rules import or_

from .test_oracle_support import OracleProject, desk_files, tree_digest

CASE, IV = "case:CS-BORROW-OK", "intervention:IV-STOCK-DOWN"
ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"


def expect_patch(nodes: str, case: str = "CS-BORROW-OK") -> str:
    return f"#atlas 1\n## expect: case | nodes | opts\n{case} | {nodes} | by=author\n"


def delete_expect(case: str = "CS-BORROW-OK") -> str:
    return f"#atlas 1\n## expect: case | nodes | opts\n-{case}\n"


class SealingTests(OracleProject):
    def test_a_clean_commit_seals_every_expectation_in_order(self):
        self.desk()
        data = self.ledger()
        self.assertEqual(data["schema"], "gemarc-atlas-oracle/1")
        self.assertEqual(sorted(data["items"]), [CASE, IV])
        self.assertEqual([data["items"][k]["ordinal"] for k in (CASE, IV)], [1, 2])
        for key, entry in data["items"].items():
            self.assertEqual(entry["state"], "sealed", key)
            self.assertEqual(entry["seal"], oracle._seal_of(entry["ah"], entry["ordinal"]))
            self.assertEqual(entry["first"], {"ordinal": entry["ordinal"], "ah8": entry["ah"][:8]})
            self.assertEqual(entry["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})
        self.assertEqual(self.states(), {CASE: "sealed", IV: "sealed"})

    def test_the_put_reply_says_what_was_sealed_and_stays_small(self):
        self.init()
        files = desk_files()
        self.put("00-space", files["00-space"])
        self.put("10-structure", files["10-structure"])
        reply = self.put("20-scenarios", files["20-scenarios"])
        self.assertEqual(reply["oracle"], {"sealed": 2})
        self.assertLess(tools.reply_bytes(reply), tools.DEFAULT_CAP)

    def test_a_typo_in_an_expectation_node_is_not_sealed_and_simulate_refuses_with_OR_000(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        data = self.ledger()
        self.assertNotIn(CASE, data["items"])
        self.assertIn(IV, data["items"])
        reply = self.simulate(case="CS-BORROW-OK")
        self.assertEqual((reply["status"], reply["code"]), ("invalid", oracle.E_UNREGISTERED))
        self.assertTrue(reply["message"].startswith("OR-000"), reply["message"])
        self.assertNotIn("terminal", json.dumps(reply), "no verdict of any kind")
        self.assertIn(CASE, self.states())
        self.assertEqual(self.states()[CASE], "unregistered")
        self.assertEqual(self.ledger()["items"].get(CASE), None)

    def test_only_a_clean_commit_seals(self):
        self.init()
        files = desk_files()
        self.put("00-space", files["00-space"])
        self.put("10-structure", files["10-structure"])
        red = self.put("20-scenarios", files["20-scenarios"].replace("class=DIVERGED", "class=SIDEWAYS"))     # ST-003: not committed
        self.assertEqual(red["status"], "invalid")
        self.assertNotIn("oracle", red)
        self.assertEqual(self.ledger()["items"], {}, "a red draft seals nothing: the ledger exists, empty")
        self.assertEqual(self.states(), {})
        fixed = self.put("20-scenarios", files["20-scenarios"])
        self.assertEqual(fixed["oracle"], {"sealed": 2})

    def test_a_put_that_waits_says_why_and_the_repair_seals_it_before_any_verdict(self):
        self.init()
        files = desk_files(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        self.put("00-space", files["00-space"])
        self.put("10-structure", files["10-structure"])
        reply = self.put("20-scenarios", files["20-scenarios"])
        self.assertEqual(reply["oracle"]["waiting"], 1)
        self.assertIn("svc.stok", reply["oracle"]["why"])
        fixed = self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(fixed["oracle"], {"sealed": 1})
        self.assertEqual(self.entry(CASE)["state"], "sealed")
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})

    def test_a_pending_node_waits_until_the_write_call_at_which_the_row_is_clean(self):
        self.init()
        files = desk_files()
        self.put("00-space", files["00-space"])
        waiting = self.put("20-scenarios", files["20-scenarios"])
        self.assertEqual(waiting["status"], "committed", "a forward reference is pending, not an error")
        self.assertNotIn(CASE, self.ledger()["items"])
        later = self.put("10-structure", files["10-structure"])
        self.assertEqual(later["oracle"], {"sealed": 2}, "sealed by the first write call that sees the nodes")
        self.assertEqual(self.states(), {CASE: "sealed", IV: "sealed"})

    def test_an_outcome_rule_id_that_no_event_carries_is_not_sealed(self):
        self.desk(("20-scenarios", "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author",
                   'CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author outcomes=[{"ruleId":"R-NOPE","outcome":"OK"}]'))
        self.assertNotIn(CASE, self.ledger()["items"])
        self.assertEqual(self.states()[CASE], "unregistered")
        reason = oracle.effective_states(self.paths, self.snap().model)[CASE][2]
        self.assertIn("R-NOPE", reason)
        files = {f"{k}.atl": v for k, v in desk_files(("20-scenarios", ROW, ROW + ' outcomes=[{"ruleId":"R-NOPE","outcome":"OK"}]')).items()}
        full = slicecheck.check(files, self.cfg().meta(), mode="full", wave_set=(1, 2))
        self.assertIn("OR-001", full.rule_ids())

    def test_an_edit_before_any_verdict_reseals_in_place_and_stays_sealed(self):
        self.desk()
        before = self.entry(CASE)
        reply = self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock"), mode="patch")
        self.assertEqual(reply["oracle"], {"sealed": 1})
        after = self.entry(CASE)
        self.assertEqual((after["state"], after["ordinal"], after["revisions"]), ("sealed", before["ordinal"], 0))
        self.assertNotEqual(after["ah"], before["ah"])
        self.assertEqual(after["first"], before["first"], "the first seal is remembered")
        self.assertEqual(after["seal"], oracle._seal_of(after["ah"], after["ordinal"]))

    def test_an_edit_that_names_an_unknown_node_leaves_the_item_unregistered_until_repaired(self):
        self.desk()
        original = self.entry(CASE)["ah"]
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stok,tbl.loan"), mode="patch")
        self.assertEqual(self.states()[CASE], "unregistered")
        self.assertEqual(self.simulate(case="CS-BORROW-OK")["code"], oracle.E_UNREGISTERED)
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual((self.entry(CASE)["state"], self.entry(CASE)["ah"]), ("sealed", original))
        self.assertEqual(self.entry(CASE)["revisions"], 0)

    def test_fixing_the_events_keeps_the_expectation_sealed(self):
        self.desk()
        self.simulate()
        patch = "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\nCall | usr.member | svc.loan | ruleId=R-BORROW\nCall | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=30\n" "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nCall | svc.loan | tbl.loan | ruleId=R-WRITE\n" "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n" "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n"
        reply = self.put("20-scenarios", patch, mode="patch")
        self.assertEqual(reply["status"], "committed", reply)
        self.assertEqual(self.entry(CASE)["state"], "sealed")
        self.assertEqual(self.entry(CASE)["revisions"], 0)

    def test_sealing_needs_the_write_gate(self):
        self.desk()
        with mock.patch.dict("os.environ", {"GEMARC_ALLOW_WRITE": "0"}):
            before = tree_digest(self.root)
            reply = self.simulate()
            self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_002"))
            self.assertEqual(tree_digest(self.root), before)
            self.assertEqual(self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")["code"], "GEMARC_AUTH_002")


class VerdictTests(OracleProject):
    def test_the_default_verdict_is_count_only(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        reply = self.simulate()
        self.assertTrue(reply["summary"]["baselines"]["fail"] == 1 and not reply["clean"])
        line = reply["fail"][0]
        self.assertIn("terminal 1/4", line)
        text = json.dumps(reply)
        for node in ("svc.stock", "svc.loan", "usr.member", "tbl.loan", "Failed", "Completed"):
            self.assertNotIn(node, text, f"count-only: {node} must not appear")
        self.assertNotIn("trace", reply)
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 1, "ids": 0, "trace": 0, "build": 0})
        self.assertEqual(self.entry(CASE)["last"]["pass"], False)

    def test_a_pass_is_a_pass_and_no_exposure(self):
        self.desk()
        reply = self.simulate()
        self.assertTrue(reply["clean"])
        self.assertEqual(reply["summary"], {"baselines": {"run": 1, "pass": 1, "fail": 0, "undecided": 0},
                                            "pairs": {"run": 1, "pass": 1, "fail": 0, "undecided": 0}})
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})
        self.assertEqual(self.entry(CASE)["verdicts"], 1)

    def test_ids_name_the_nodes_and_are_ledgered(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        reply = self.simulate(case="CS-BORROW-OK", detail="ids")
        self.assertIn("ids: svc.stock", reply["fail"][0])
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 1, "ids": 1, "trace": 0, "build": 0})

    def test_trace_is_bounded_ledgered_and_shows_the_final_states_of_the_disagreeing_nodes(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        reply = self.simulate(case="CS-BORROW-OK", detail="trace")
        block = reply["trace"][0]
        self.assertEqual(block["case"], "CS-BORROW-OK")
        self.assertLessEqual(len(block["events"]), oracle.TRACE_LINES)
        self.assertEqual(block["final"], {"svc.stock": "Completed"})
        self.assertEqual(self.entry(CASE)["exposed"]["trace"], 1)
        self.assertLessEqual(tools.reply_bytes(reply), tools.SIMULATE_CAP)

    def test_the_pair_trace_reveals_the_case_baseline_too(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", intervention="IV-STOCK-DOWN", detail="trace")
        self.assertEqual(self.entry(IV)["exposed"]["trace"], 1)
        self.assertEqual(self.entry(CASE)["exposed"]["trace"], 1)

    def test_a_project_wide_reveal_is_refused(self):
        self.desk()
        for detail in ("ids", "trace"):
            reply = self.simulate(detail=detail)
            self.assertEqual((reply["status"], reply["code"]), ("invalid", oracle.E_ARG))
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})

    def test_the_ledger_records_a_reveal_only_for_what_the_reply_shows(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        with mock.patch.object(oracle, "SIMULATE_CAP", 1000):
            reply = self.simulate(intervention="IV-STOCK-DOWN", detail="trace")
        self.assertLessEqual(tools.reply_bytes(reply), 1000)
        shown = {b["pair"] for b in reply.get("trace", ())}
        self.assertEqual("IV-STOCK-DOWN x CS-BORROW-OK" in shown, self.entry(IV)["exposed"]["trace"] == 1)
        before = self.entry(CASE)["exposed"]["trace"]
        reply2 = self.simulate(case="CS-BORROW-OK", detail="trace")
        self.assertEqual(reply2["trace"][0]["case"], "CS-BORROW-OK")
        self.assertEqual(self.entry(CASE)["exposed"]["trace"], before + 1)

    def test_a_failing_pair_reports_the_class_bit_and_the_terminal_count(self):
        iv_row = 'terminal={"svc.stock":"Blocked"}'
        self.desk(("20-scenarios", "class=DIVERGED " + iv_row, "class=NO_DIFFERENCE terminal={\"svc.stock\":\"Completed\"}"))
        reply = self.simulate()
        line = [x for x in reply["fail"] if "IV-002" in x][0]
        self.assertIn("class differs", line)
        self.assertIn("terminal 1/1", line)
        self.assertNotIn("DIVERGED", json.dumps(reply))
        self.assertEqual(self.entry(IV)["exposed"]["mismatch"], 1)
        self.assertEqual(self.entry(IV)["last"]["klass"], True)

    def test_an_unknown_case_or_a_mismatched_pair_is_refused(self):
        self.desk()
        self.assertEqual(self.simulate(case="CS-NOPE")["code"], oracle.E_ARG)
        self.assertEqual(self.simulate(intervention="IV-NOPE")["code"], oracle.E_ARG)
        self.assertEqual(self.simulate(detail="loud")["code"], oracle.E_ARG)

    def test_a_case_without_an_expectation_is_refused_when_named_and_silent_otherwise(self):
        self.desk(("20-scenarios", "## expect: case | nodes | opts\n" + ROW + "\n", ""))
        self.assertNotIn(CASE, self.ledger()["items"])
        named = self.simulate(case="CS-BORROW-OK")
        self.assertEqual(named["code"], oracle.E_UNREGISTERED)
        everything = self.simulate()
        self.assertEqual(everything["summary"]["baselines"]["run"], 0)
        self.assertIn("refused", everything)          # the pair needs the case
        self.assertTrue(all(x.startswith("OR-000") for x in everything["refused"]))

    def test_the_verdict_sees_a_committed_model_only(self):
        self.desk()
        bad = self.put("20-scenarios", "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\nCall | usr.member | svc.nope\n", mode="patch")
        self.assertIn(bad["status"], ("committed", "invalid"))
        reply = self.simulate()
        if bad["status"] == "committed":
            self.assertIn(reply["status"], ("ok", "invalid"))
        else:
            self.assertEqual(reply["status"], "ok", "a red draft is not the committed model")


class ReplyShapeTests(OracleProject):
    """What the reply says when there is nothing to say, too much to say, or something is wrong with the request."""

    def test_a_refusal_never_grows_with_the_caller_input(self):
        self.desk()
        for case in ("x" * 100000, "CS-BORROW-OK\n## injected", "y" * 3000):
            reply = self.simulate(case=case)
            self.assertEqual((reply["status"], reply["code"]), ("invalid", oracle.E_ARG))
            self.assertLessEqual(tools.reply_bytes(reply), 3000)
            self.assertNotIn("\n", reply["message"])
        reply = self.simulate(intervention="z" * 100000)
        self.assertLessEqual(tools.reply_bytes(reply), 3000)
        reply = self.simulate(case="c" * 100000, intervention="IV-STOCK-DOWN")
        self.assertLessEqual(tools.reply_bytes(reply), 3000)
        out, code = cli.run(["--root", str(self.root), "simulate", self.name, "--case", "a" * 3000])
        self.assertEqual(code, 1)
        self.assertLessEqual(tools.reply_bytes(out), 3000)

    def test_a_project_without_a_registered_expectation_is_not_clean(self):
        self.init()
        self.put("00-space", desk_files()["00-space"])
        reply = self.simulate()
        self.assertEqual(reply["status"], "ok")
        self.assertIsNone(reply["clean"], "nothing was verified: neither true nor false")
        self.assertIn("nothing to verify", " ".join(reply["next"]))
        self.assertNotIn("all verdicts agree", " ".join(reply["next"]))
        out, code = cli.run(["--root", str(self.root), "simulate", self.name])
        self.assertEqual(code, 1, "the command line does not call that a success")

    def test_a_refused_item_makes_the_run_not_clean(self):
        self.desk(("20-scenarios", ROW, ROW + ' outcomes=[{"ruleId":"R-NOPE","outcome":"OK"}]'))
        reply = self.simulate()
        self.assertTrue(reply["refused"])
        self.assertIs(reply["clean"], False, "an item that got no verdict is not verified")

    def test_a_verdict_for_a_revised_item_says_so(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        reply = self.simulate(case="CS-BORROW-OK")
        self.assertTrue(reply["clean"])
        self.assertIn("revised", " ".join(reply["next"]))
        self.assertNotIn("all verdicts agree", " ".join(reply["next"]))

    def test_an_ids_line_is_cut_inside_the_line_so_one_failing_item_is_always_shown(self):
        v = {"terminal": [300, 300], "outcomes": [0, 0], "badNodes": [f"node.family-{i:03d}" for i in range(300)], "badRules": [],
             "events": 9, "unbound": 0, "blocked": 0}
        line = oracle._fail_line("OR-001", "CS-X", v, "ids")
        self.assertLess(len(line), 700)
        self.assertIn("... +", line)
        self.assertIn("node.family-000", line)
        self.assertEqual(oracle._final_of({f"n{i}": "Failed" for i in range(40)}, [f"n{i}" for i in range(40)])["finalMore"], 28)

    def test_an_ids_call_on_a_passing_item_records_nothing_because_nothing_is_shown(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.assertEqual(self.entry(CASE)["exposed"]["trace"], 1, "a trace is shown for a pass too")


class StateTests(OracleProject):
    def revised(self, key=CASE):
        entry = self.entry(key)
        return entry["state"], entry["revisions"], entry["why"]

    def test_editing_after_a_mismatch_report_gives_revised(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate(case="CS-BORROW-OK")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        state, revisions, why = self.revised()
        self.assertEqual((state, revisions), ("revised", 1))
        self.assertIn("mismatch", why)
        self.assertEqual(self.states()[CASE], "revised")

    def test_editing_after_an_ids_reveal_gives_revised(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.revised()[0], "revised")

    def test_editing_after_a_passing_trace_gives_revised(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")
        self.assertEqual(self.revised()[0], "revised")

    def test_deleting_and_adding_again_after_a_build_that_embedded_the_trace_gives_revised(self):
        self.desk()
        runs = oracle.sealed_runs(self.ws, self.name)
        self.assertIn("CS-BORROW-OK", runs["baselines"])
        self.assertEqual(self.entry(CASE)["exposed"]["build"], 1)
        self.put("20-scenarios", delete_expect(), mode="patch")
        self.assertTrue(self.entry(CASE)["withdrawn"])
        self.assertEqual(self.entry(CASE)["first"]["ordinal"], 1, "the ledger remembers the first seal after the row is deleted")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        entry = self.entry(CASE)
        self.assertEqual((entry["state"], entry["withdrawn"], entry["ordinal"]), ("revised", False, 1))

    def test_deleting_and_adding_again_before_any_result_keeps_sealed_and_the_ordinal(self):
        self.desk()
        self.put("20-scenarios", delete_expect(), mode="patch")
        withdrawn = self.entry(CASE)
        self.assertTrue(withdrawn["withdrawn"])
        self.assertEqual(self.states().get(CASE), None, "no assertion in the model now")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        entry = self.entry(CASE)
        self.assertEqual((entry["state"], entry["ordinal"], entry["withdrawn"]), ("sealed", 1, False))

    def test_a_deleted_row_stays_in_the_ledger_with_its_first_seal(self):
        self.desk()
        first = self.entry(CASE)["first"]
        self.put("20-scenarios", delete_expect(), mode="patch")
        self.put("20-scenarios", "#atlas 1\n## notes: id\n", mode="patch") if False else None
        entry = self.entry(CASE)
        self.assertEqual((entry["first"], entry["withdrawn"]), (first, True))
        self.assertEqual(oracle.ledger_summary(self.ledger())["total"], 1, "a withdrawn entry is not counted")

    def test_a_copy_of_an_exposed_case_inherits_the_exposure(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        copy_events = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PROBE | normal | Probe copy | req=RQ-1 ev=EVD-1\n"
                       "## events: type | from | to | opts\n@set case=CS-PROBE\nCall | usr.member | svc.loan | ruleId=R-BORROW\n"
                       "Call | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20\n" "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n"
                       "Call | svc.loan | tbl.loan | ruleId=R-WRITE\n" "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n"
                       "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n"
                       "## expect: case | nodes | opts\nCS-PROBE | usr.member,svc.loan,svc.stock=Failed,tbl.loan | by=author\n")
        self.simulate(case="CS-BORROW-OK", detail="ids")           # the original is shown a result
        self.put("30-probe", copy_events)
        entry = self.entry("case:CS-PROBE")
        self.assertEqual(entry["state"], "revised")
        self.assertIn("copy of case:CS-BORROW-OK", entry["why"])

    def test_the_probe_copy_cheat_is_caught_at_the_edit_of_the_real_case(self):
        self.desk()
        probe = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PROBE | normal | Probe copy | req=RQ-1 ev=EVD-1\n"
                 "## events: type | from | to | opts\n@set case=CS-PROBE\nCall | usr.member | svc.loan | ruleId=R-BORROW\n"
                 "Call | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20\n" "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n"
                 "Call | svc.loan | tbl.loan | ruleId=R-WRITE\n" "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n"
                 "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n"
                 "## expect: case | nodes | opts\nCS-PROBE | usr.member,svc.loan,svc.stock=Failed,tbl.loan | by=author\n")
        self.put("30-probe", probe)
        self.simulate(case="CS-PROBE", detail="ids")                # the probe is exposed, the real case is not
        self.assertEqual(self.entry(CASE)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0})
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,tbl.loan"), mode="patch")   # the learned values go into the real one
        self.assertEqual(self.entry(CASE)["state"], "revised")
        self.assertIn("copy of it", self.entry(CASE)["why"])

    def test_sealed_runs_gives_sealed_or_confirmed_items_only(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate(case="CS-BORROW-OK")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")   # revised
        runs = oracle.sealed_runs(self.ws, self.name)
        self.assertEqual(runs["baselines"], {})
        self.assertEqual(runs["pairs"], [])
        self.assertEqual(runs["withheld"], {"cases": ["CS-BORROW-OK"], "interventions": ["IV-STOCK-DOWN"]})
        self.assertEqual([u for u in runs["diagrams"]], [])
        self.confirm_ticks()
        runs = oracle.sealed_runs(self.ws, self.name)
        self.assertIn("CS-BORROW-OK", runs["baselines"])
        self.assertEqual(runs["baselines"]["CS-BORROW-OK"]["state"], "confirmed")

    def test_an_unsealed_case_gets_no_run_no_pair_and_no_diagram(self):
        bad_outcome = ROW + ' outcomes=[{"ruleId":"R-NOPE","outcome":"OK"}]'      # valid model, never sealable
        self.desk(("20-scenarios", ROW, bad_outcome))
        runs = oracle.sealed_runs(self.ws, self.name)
        self.assertEqual(runs["baselines"], {})
        self.assertEqual(runs["pairs"], [], "the pair needs the case baseline: nothing for an unsealed case")
        self.assertEqual(runs["diagrams"], [])
        self.assertEqual(runs["withheld"], {"cases": ["CS-BORROW-OK"], "interventions": ["IV-STOCK-DOWN"]})
        ids = {u["id"] for u in diagrams.build_units(self.snap().model)["units"]}
        self.assertFalse([i for i in ids if i.startswith(("sequence", "staterun"))], "structure-only units never draw a run")

    def test_an_unsealed_typo_gives_no_provider_result_at_all(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        with self.assertRaises(oracle.OracleError) as raised:
            oracle.sealed_runs(self.ws, self.name)
        self.assertEqual(raised.exception.code, oracle.E_MODEL)

    def test_sealed_items_get_a_sequence_and_a_state_diagram_built_from_the_run(self):
        self.desk()
        runs = oracle.sealed_runs(self.ws, self.name)
        by_id = {u["id"]: u for u in runs["diagrams"]}
        self.assertIn("sequence-CS-BORROW-OK", by_id)
        self.assertIn("staterun-CS-BORROW-OK", by_id)
        self.assertIn("sequence-IV-STOCK-DOWN-CS-BORROW-OK", by_id)
        text = by_id["sequence-CS-BORROW-OK"]["mermaid"]
        self.assertTrue(text.startswith("sequenceDiagram"))
        self.assertIn("Loan service", text)
        self.assertEqual(by_id["staterun-CS-BORROW-OK"]["type"], "staterun")
        self.assertTrue(by_id["staterun-CS-BORROW-OK"]["mermaid"].startswith("stateDiagram-v2"))
        self.assertIn("-x", by_id["sequence-IV-STOCK-DOWN-CS-BORROW-OK"]["mermaid"], "the blocked call is drawn as a failed arrow")
        for unit in runs["diagrams"]:
            self.assertEqual(unit["issues"], [])


class ConfirmationTests(OracleProject):
    def test_the_sheet_lists_every_unconfirmed_assertion_and_the_approve_all_line(self):
        self.desk()
        text = self.sheet()
        lines = [x for x in text.splitlines() if x.startswith("- [ ] ")]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(f" {self.entry(CASE)['ah'][:8]} sealed | " in x for x in lines))
        self.assertIn("Case CS-BORROW-OK \"Member borrows an available copy\"", text)
        self.assertIn("If Stock service is down while borrowing", text)
        self.assertRegex(text, r"(?m)^APPROVE-ALL [0-9a-f]{12}$")
        self.assertIn("This is an attestation, not authentication", text)
        self.assertFalse(self.confirm_file().exists(), "the MCP never writes the confirmation file")
        self.assertTrue((self.paths.atlas / "dist" / "oracle-sheet.md").is_file())

    def test_the_sheet_is_written_in_the_project_language(self):
        self.init(languages=("zh-CN",))
        self.load(desk_files())
        text = self.sheet()
        self.assertTrue(text.startswith("# 期望确认单"), text[:30])
        self.assertIn("用例 CS-BORROW-OK", text)
        self.init(name="desk2", languages=("ja-JP",))
        self.load(desk_files())
        self.assertTrue(self.sheet().startswith("# 期待値確認シート"))

    def test_the_sheet_is_deterministic_and_has_no_timestamp_or_path(self):
        self.desk()
        first = self.sheet()
        self.assertEqual(first, self.sheet())
        self.assertNotIn(str(self.root), first)
        self.assertNotRegex(first, r"\b20\d\d-\d\d-\d\d\b")

    def test_ticked_lines_confirm_exactly_those_items(self):
        self.desk()
        self.assertEqual(self.confirm_ticks(only={"CS-BORROW-OK"}), 1)
        self.assertEqual(self.states(), {CASE: "confirmed", IV: "sealed"})
        gate = self.gate()
        self.assertEqual(gate["counts"]["confirmed"], 1)
        summary = tools.status(self.ws, self.name, "oracle")
        self.assertEqual((summary["confirmed"], summary["sealed"], summary["unconfirmed"]), (1, 1, 1))

    def test_approve_all_confirms_everything_and_lapses_when_anything_changes(self):
        self.desk()
        self.approve_all()
        self.assertEqual(set(self.states().values()), {"confirmed"})
        self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")        # a later edit
        self.assertEqual(self.states(), {CASE: "revised", IV: "sealed"}, "the edited item lapsed, and the digest no longer matches")
        self.assertEqual(self.entry(CASE)["why"], "edited after the user confirmed it")

    def test_a_tick_for_a_hash_the_sheet_never_printed_is_ignored(self):
        self.desk()
        self.confirm_file().write_text(f"- [x] CS-BORROW-OK {self.entry(CASE)['ah'][:8]}\nAPPROVE-ALL 0123456789ab\n", encoding="utf-8")
        self.assertEqual(set(self.states().values()), {"sealed"})
        self.assertEqual(self.gate()["counts"]["confirmed"], 0)
        confirmed, ignored = oracle.resolve_confirmations(self.paths, self.ledger())
        self.assertEqual(confirmed, {})
        self.assertEqual(len(ignored), 2)

    def test_an_unticked_line_and_a_changed_hash_confirm_nothing(self):
        self.desk()
        sheet = self.sheet()
        unticked = [x.split(" | ")[0] for x in sheet.splitlines() if x.startswith("- [ ] ")]
        wrong = [re.sub(r" [0-9a-f]{8} ", " deadbeef ", re.sub(r"^- \[ \]", "- [x]", x)) for x in unticked]
        self.confirm_file().write_text("\n".join(unticked + wrong) + "\n", encoding="utf-8")
        self.assertEqual(set(self.states().values()), {"sealed"})

    def test_by_user_counts_only_with_a_matching_confirmation(self):
        self.desk(("20-scenarios", "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author",
                   "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=user"))
        counts = self.gate()["counts"]
        self.assertEqual((counts["claimed"], counts["honoured"]), (1, 0))
        self.confirm_ticks(only={"CS-BORROW-OK"})
        counts = self.gate()["counts"]
        self.assertEqual((counts["claimed"], counts["honoured"]), (1, 1))

    def test_nothing_the_mcp_does_writes_the_confirmation_file(self):
        self.desk()
        self.simulate()
        self.simulate(case="CS-BORROW-OK", detail="trace")
        oracle.sealed_runs(self.ws, self.name)
        tools.oracle_bite(self.ws, self.name)
        self.sheet()
        self.gate()
        tools.check(self.ws, self.name, mode="full")
        self.assertFalse(self.confirm_file().exists())

    def test_the_attestation_wording(self):
        self.desk()
        badge = self.gate()["attestation"]
        self.assertEqual(badge["class"], "attested")
        self.assertIn("2 of 2 expectations were sealed before any result was shown", badge["badge"])
        self.assertIn("attestation, not authentication", badge["badge"])


class GateTests(OracleProject):
    def kinds(self):
        return sorted({b["kind"] for b in self.gate()["blockers"]})

    def test_nothing_blocks_a_sealed_project_with_passing_verdicts(self):
        self.desk()
        self.assertEqual(self.kinds(), ["no-bite", "no-verdict"], "the gate does not run the engine")
        self.simulate()
        self.assertEqual(self.kinds(), ["no-bite"], "a verdict is not a mutant run: OR-004 needs one for this model")
        self.bite()
        gate = self.gate()
        self.assertEqual((gate["ok"], gate["blockers"]), (True, []))

    def test_each_blocker_kind(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        self.assertIn("unsealed", self.kinds())
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock=Failed,tbl.loan"), mode="patch")
        self.simulate()
        self.assertEqual(self.kinds(), ["mismatch"], self.gate())
        blocker = [b for b in self.gate()["blockers"] if b["kind"] == "mismatch"][0]
        self.assertEqual((blocker["rule"], blocker["id"]), ("OR-001", "CS-BORROW-OK"))
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")   # revised now
        self.simulate()
        self.bite()
        self.assertEqual(self.kinds(), ["revised"])
        self.confirm_ticks(only={"CS-BORROW-OK"})
        self.assertEqual(self.kinds(), [])

    def test_a_changed_model_needs_a_new_verdict(self):
        self.desk()
        self.simulate()
        self.put("20-scenarios", "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\nCall | usr.member | svc.loan | ruleId=R-BORROW\nCall | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=45\n" "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nCall | svc.loan | tbl.loan | ruleId=R-WRITE\n" "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n" "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n", mode="patch")
        self.assertEqual(self.kinds(), ["no-bite", "no-verdict"])

    def test_the_gate_is_structured_and_never_writes(self):
        self.desk()
        before = tree_digest(self.root)
        gate = self.gate()
        self.assertEqual(set(gate), {"ok", "blockers", "counts", "attestation", "modelSha8"})
        self.assertEqual(tree_digest(self.root), before)


class VacuousAndSummaryTests(OracleProject):
    def test_a_sealed_passing_one_node_assertion_does_not_clear_the_release_gate(self):
        self.desk(("20-scenarios", ROW, "CS-BORROW-OK | usr.member | by=author"))
        self.assertTrue(self.simulate()["clean"])
        gate = self.gate()
        self.assertFalse(gate["ok"])
        blocker = [b for b in gate["blockers"] if b["kind"] == "vacuous"]
        self.assertEqual([(b["id"], b["rule"]) for b in blocker], [("CS-BORROW-OK", "OR-003")])

    def test_a_mutant_run_that_killed_nothing_blocks_the_gate(self):
        self.desk()
        data = self.ledger()
        data["items"][CASE]["bite"] = {"sha": self.snap().sha8, "total": 8, "killed": 0, "holds": True}
        self.write_ledger(data, rechain=True)
        self.simulate()
        self.assertIn(("CS-BORROW-OK", "OR-004"), [(b["id"], b["rule"]) for b in self.gate()["blockers"] if b["kind"] == "vacuous"])

    def test_the_default_full_check_reports_OR_003_as_an_error_and_cs_001(self):
        self.desk(("20-scenarios", ROW, "CS-BORROW-OK | usr.member | by=author"))
        reply = tools.check(self.ws, self.name, mode="full")
        self.assertFalse(reply["clean"])
        self.assertIn("OR-003", reply["diagnostics"])

    def test_slice_mode_check_prints_OR_000_for_the_slice_it_names(self):
        self.desk()
        path = self.paths.slices / "20-scenarios.atl"
        path.write_text(path.read_text(encoding="utf-8").replace(ROW, "CS-BORROW-OK | usr.member,svc.loan | by=author"), encoding="utf-8", newline=chr(10))
        self.assertIn("OR-000", tools.check(self.ws, self.name, slice="20-scenarios")["diagnostics"])
        self.assertNotIn("OR-000", tools.check(self.ws, self.name, slice="10-structure")["diagnostics"])

    def test_the_ledger_summary_has_the_blind_mismatch_rate_and_the_bite(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate()
        summary = self.ledger()["summary"]
        self.assertEqual((summary["verdicted"], summary["blindMismatch"]), (2, 0.5))
        self.assertEqual(self.entry(CASE)["firstPass"], False)
        self.put("20-scenarios", expect_patch("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.simulate()
        self.assertEqual(self.entry(CASE)["firstPass"], False, "only the first verdict is the blind one")
        self.desk_bite = tools.oracle_bite(self.ws, self.name)
        self.assertIn("bite", self.ledger()["summary"])
        review = __import__("gemarc_mcp.atlas.review", fromlist=["x"]).run(self.ws, self.name, "report", "")
        self.assertEqual(review["oracle"]["blindMismatch"], 0.5)


class LedgerTests(OracleProject):
    def test_the_ledger_is_canonical_bounded_and_free_of_timestamps_and_paths(self):
        self.desk()
        self.simulate()
        raw = self.paths.oracle.read_bytes()
        self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)
        self.assertLess(len(raw), oracle.LEDGER_MAX_BYTES)
        data = json.loads(raw)
        self.assertEqual(data["chain"], oracle._chain_of(data))
        self.assertNotIn(str(self.root).encode("utf-8"), raw)
        self.assertIsNone(re.search(rb"\b20\d\d-\d\d-\d\d", raw))
        self.assertEqual(data["provenance"], "ok")

    def test_the_same_operations_give_the_same_bytes(self):
        out = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                ws = pj.get_workspace(Path(tmp))
                tools.init(ws, "desk", "Library lending desk", "asis", ["en-US"], repos=["main"])
                for name, text in desk_files().items():
                    tools.put(ws, "desk", name, text)
                tools.simulate(ws, "desk")
                out.append(pj.project_paths(ws, "desk").oracle.read_bytes())
        self.assertEqual(out[0], out[1])

    def test_writes_leave_no_temporary_file_and_no_lock(self):
        self.desk()
        self.simulate()
        leftovers = [p.name for p in self.paths.dot.iterdir() if p.name.startswith(".tmp") or p.name == "lock"]
        self.assertEqual(leftovers, [])

    def test_the_ledger_is_size_bounded(self):
        self.desk()
        with mock.patch.object(oracle, "MAX_ITEMS", 2):
            reply = self.put("30-more", "#atlas 1\n## cases: id | cat | title | opts\nCS-EXTRA | normal | Extra | req=RQ-1\n"
                             "## events: type | from | to | opts\n@set case=CS-EXTRA\nCall | usr.member | svc.loan | ruleId=R-X\nCall | svc.loan | svc.stock | ruleId=R-Y\n"
                             "## expect: case | nodes | opts\nCS-EXTRA | usr.member,svc.loan | by=author\n")
        self.assertEqual(reply["status"], "committed", "the put itself is never failed by the ledger")
        self.assertEqual(reply["oracle"], {"error": oracle.E_LIMIT})
        self.assertNotIn("case:CS-EXTRA", self.ledger()["items"])
        with mock.patch.object(oracle, "LEDGER_MAX_BYTES", 400):
            reply = self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")
            self.assertEqual(reply["oracle"], {"error": oracle.E_LIMIT})

    def test_a_ledger_written_by_the_provisional_shape_is_still_read_by_status(self):
        self.desk()
        self.paths.oracle.write_text(json.dumps({"seals": {"c1": {"kind": "case", "state": "sealed"}}}), encoding="utf-8")
        summary = tools.status(self.ws, self.name, "oracle")
        self.assertEqual((summary["cases"], summary["total"]), (1, 1))

    def test_status_reads_the_real_schema(self):
        self.desk()
        summary = tools.status(self.ws, self.name, "oracle")
        self.assertEqual((summary["present"], summary["total"], summary["sealed"], summary["revised"], summary["cases"], summary["interventions"]),
                         (True, 2, 2, 0, 1, 1))
        dims = tools.status(self.ws, self.name, "dims")["dimensions"]
        self.assertTrue(dims["4D"]["ready"] and dims["5D"]["ready"])
        self.assertEqual(oracle.ledger_summary(self.ledger())["cases"], 1)


class ReadOnlyTests(OracleProject):
    """``check`` and every other read changes no file (the whole project tree is hashed before and after)."""

    def test_check_status_index_and_the_gate_change_no_file(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        self.simulate()
        before = tree_digest(self.root)
        for call in (lambda: tools.check(self.ws, self.name, mode="full"), lambda: tools.check(self.ws, self.name, mode="full", rule="OR"),
                     lambda: tools.check(self.ws, self.name, mode="full", rule="IV"), lambda: tools.check(self.ws, self.name, slice="20-scenarios"),
                     lambda: tools.status(self.ws, self.name, "next"), lambda: tools.status(self.ws, self.name, "oracle"),
                     lambda: tools.index(self.ws, self.name, "case:CS-BORROW-OK"), lambda: oracle.release_gate(self.ws, self.name),
                     lambda: tools.oracle_gate(self.ws, self.name)):
            call()
            self.assertEqual(tree_digest(self.root), before)

    def test_check_on_an_unsealed_assertion_prints_OR_000_and_runs_no_verdict(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        reply = tools.check(self.ws, self.name, mode="full")
        self.assertIn("OR-000", reply["diagnostics"])
        self.assertIn("not registered", reply["diagnostics"])
        self.assertNotIn("OR-001", reply["diagnostics"])
        self.assertIsNone(self.entry(IV)["last"], "no verdict was run")

    def test_check_never_seals(self):
        self.init()
        files = desk_files()
        for name, text in files.items():
            (self.paths.slices / f"{name}.atl").write_text(text, encoding="utf-8", newline="\n")     # direct file edits are supported
        self.assertFalse(self.paths.oracle.exists())
        reply = tools.check(self.ws, self.name, mode="full")
        self.assertIn("OR-000", reply["diagnostics"])
        self.assertFalse(self.paths.oracle.exists(), "check wrote nothing: sealing happens at put, simulate or build")
        self.simulate()
        self.assertEqual(self.states(), {CASE: "sealed", IV: "sealed"})

    def test_wave_two_findings_from_the_ledger_show_without_naming_nodes(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        self.simulate()
        reply = tools.check(self.ws, self.name, mode="full", rule="OR")
        self.assertIn("OR-001", reply["diagnostics"])
        self.assertIn("terminal 1 of 4 asserted nodes differ", reply["diagnostics"])
        self.assertNotIn("Failed", reply["diagnostics"])

    def test_the_default_full_check_is_wave_one_plus_OR_000_only(self):
        self.desk()
        rules = slicecheck.check(desk_files(), self.cfg().meta(), mode="full", wave_set=(1,)).rule_ids()
        self.assertEqual(set(self.snap().check.rule_ids()) - rules, set())


class CliTests(OracleProject):
    def run_cli(self, *argv):
        return cli.run(["--root", str(self.root), *argv])

    def test_simulate_through_the_command_line(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stock=Failed")))
        reply, code = self.run_cli("simulate", self.name)
        self.assertEqual((reply["status"], code), ("ok", 1), "exit code 1: a mismatch is an ERROR finding, as for check")
        self.assertIn("terminal 1/4", reply["fail"][0])
        self.assertNotIn("svc.stock", json.dumps(reply))
        reply, code = self.run_cli("simulate", self.name, "--case", "CS-BORROW-OK", "--detail", "ids")
        self.assertIn("ids: svc.stock", reply["fail"][0])
        gate, code = self.run_cli("simulate", self.name, "--gate")
        self.assertEqual(gate["ok"], False)
        sheet, code = self.run_cli("simulate", self.name, "--sheet")
        self.assertEqual(sheet["status"], "ok")

    def test_an_unregistered_case_through_the_command_line_has_no_verdict_and_exit_one(self):
        self.desk(("20-scenarios", ROW, ROW.replace("svc.stock", "svc.stok")))
        reply, code = self.run_cli("simulate", self.name, "--case", "CS-BORROW-OK")
        self.assertEqual((reply["status"], code), ("invalid", 1))
        self.assertTrue(reply["message"].startswith("OR-000"))

    def test_the_gate_is_read_only_and_needs_no_write_flag(self):
        self.desk()
        self.simulate()
        self.bite()
        with mock.patch.dict("os.environ", {"GEMARC_ALLOW_WRITE": "0"}):
            gate, code = self.run_cli("simulate", self.name, "--gate")
            self.assertEqual((gate["status"], code), ("ok", 0))
            reply, code = self.run_cli("simulate", self.name)
            self.assertEqual((reply["status"], code), ("invalid", 1))


if __name__ == "__main__":
    unittest.main()
