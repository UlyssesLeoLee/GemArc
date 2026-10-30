"""The scripted CHEATER suite (design 11, pilot P1b: a cheater run must not reach the release gate as ``sealed``).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_oracle_cheaters.py"

Each test plays one cheat on the synthetic lending desk and then asks what is left: is the item ``sealed`` at release, ``revised``,
or flagged ``unverified-provenance``? The rule of the suite (``verdict``): a cheat either keeps the item out of "sealed at
release" or leaves the unverified-provenance flag. The cheats this design CANNOT stop are written down in ``LimitsTests``; each
of those tests PINS today's behaviour so that a change is noticed, and the docstring says what the user is left with.

WHAT THE ATTESTATION CANNOT STOP (attestation, not authentication; no key, no HMAC, no TTY):

* L1 an AI with a shell that reads ``atlas/dist/oracle-sheet.md`` and copies its lines into ``atlas/oracle-confirm.txt``: the lines
  are then "the user's confirmation". (The MCP never writes that file, and a tick for a hash that no sheet printed is ignored.)
* L2 editing the ledger AND recomputing ``chain``, the ``seal`` fields AND the anchor ``.atlas/oracle-anchor.json`` (the algorithm is in
  ``oracle.py``). Recomputing only the chain is caught (``edited``), and so is putting an OLDER copy of ``oracle.json`` back
  (``rolled-back``: its tick is behind the anchor).
* L3 deleting ``.atlas/`` (ledger, anchor and ``puts.json``) or cloning the project afresh: ``.atlas/`` is git-ignored, so a fresh clone starts
  with an empty ledger and a clean provenance. What is left behind: nothing in the ledger; ``.atlas/history`` and git show the earlier
  slice versions.
* L4 adjusting the EVENTS to the expectation: fixing events keeps ``sealed`` by design (the attestation is about the expectation).
* L5 running the engine directly (the package is on the machine) or reading a result from somewhere the tools do not guard.
* L6 near-clones: ``sealed -> revised`` by fingerprint needs the SAME events, parameters and policy (step names and positions do not
  count); one changed latency makes the probe copy a different case.
* L7 putting an older copy of ``oracle.json`` back AND the matching older ``oracle-anchor.json`` (a copy of the whole ``.atlas/`` minus
  the history is the same as L3: nothing in the ledger remembers the exposure).

Synthetic id families only.
"""
from __future__ import annotations

import json
import re
import unittest

from gemarc_mcp.atlas import oracle, tools
from gemarc_mcp.atlas.rules import or_

from .test_oracle_support import OracleProject, desk_files

CASE, IV = "case:CS-BORROW-OK", "intervention:IV-STOCK-DOWN"
ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
WRONG = ROW.replace("svc.stock", "svc.stock=Failed")
EVENTS = ("Call | usr.member | svc.loan | ruleId=R-BORROW\nCall | svc.loan | svc.stock | ruleId=R-CHECK latencyMs={lat}\n"
          "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\nCall | svc.loan | tbl.loan | ruleId=R-WRITE\n"
          "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n" "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n")


def patch_expect(nodes: str, case: str = "CS-BORROW-OK") -> str:
    return f"#atlas 1\n## expect: case | nodes | opts\n{case} | {nodes} | by=author\n"


class Cheater(OracleProject):
    def verdict(self, key: str = CASE) -> dict:
        """What the release sees of one item: its state, whether the gate blocks it, and the ledger provenance flag."""
        states = self.states()
        gate = self.gate()
        ident = key.split(":", 1)[1]
        blocked = sorted({b["kind"] for b in gate["blockers"] if b["id"] == ident or b["kind"] == "provenance"})
        flagged = gate["attestation"]["class"] == "unverified-provenance"
        return {"state": states.get(key, "absent"), "blocked": blocked, "unverified": flagged,
                "sealed_at_release": states.get(key) == "sealed" and not blocked and not flagged}

    def assertStopped(self, outcome: dict, msg=""):
        """The suite's rule: out of sealed-at-release, or flagged unverified-provenance."""
        self.assertTrue(not outcome["sealed_at_release"] or outcome["unverified"], f"{msg}: {outcome}")


class ExposureCheats(Cheater):
    def test_copy_the_engine_output_into_the_expectation_after_a_reveal(self):
        self.desk(("20-scenarios", ROW, WRONG))
        reveal = self.simulate(case="CS-BORROW-OK", detail="trace")
        copied = reveal["trace"][0]["final"]
        self.assertEqual(copied, {"svc.stock": "Completed"})
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock=" + copied["svc.stock"] + ",tbl.loan"), mode="patch")
        self.simulate()
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertIn("revised", outcome["blocked"])
        self.assertStopped(outcome, "copy after reveal")
        self.assertEqual(oracle.sealed_runs(self.ws, self.name)["baselines"], {}, "a revised item is never embedded")

    def test_copy_after_a_mismatch_count_alone_then_guess_until_it_passes(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate()                                                     # one count: the cheater learns that something is off
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")     # and tries the other value
        self.assertEqual(self.simulate()["clean"], True)
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "guessing after a mismatch")

    def test_delete_the_row_and_add_it_again_with_the_engine_values(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", "#atlas 1\n## expect: case | nodes | opts\n-CS-BORROW-OK\n", mode="patch")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertEqual(self.entry(CASE)["first"]["ordinal"], 1, "the first seal outlives the row")
        self.assertStopped(outcome, "delete and re-add")

    def test_delete_and_add_again_after_a_build_that_embedded_the_trace(self):
        self.desk()
        oracle.sealed_runs(self.ws, self.name)
        self.put("20-scenarios", "#atlas 1\n## expect: case | nodes | opts\n-CS-BORROW-OK\n", mode="patch")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.verdict()["state"], "revised")

    def test_write_the_expectation_only_after_seeing_the_ids(self):
        weak = "CS-BORROW-OK | usr.member | by=author"
        self.desk(("20-scenarios", ROW, weak.replace("usr.member", "usr.member=Failed")))
        reveal = self.simulate(case="CS-BORROW-OK", detail="ids")
        self.assertIn("ids: usr.member", reveal["fail"][0])
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")           # now every id is known
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "ids first")

    def test_the_intervention_prediction_is_probed_one_bit_at_a_time(self):
        self.desk(("20-scenarios", "class=DIVERGED", "class=NO_DIFFERENCE"))
        self.simulate()                                                      # one bit: the class differs
        self.put("20-scenarios", "#atlas 1\n## interventions: id | type | description | opts\nIV-STOCK-DOWN | disable_module | Stock service is down while borrowing | target=svc.stock applies=CS-BORROW-OK class=DIVERGED terminal={\"svc.stock\":\"Blocked\"}\n", mode="patch")
        self.simulate()
        outcome = self.verdict(IV)
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "class bit probing")

    def test_a_probe_copy_of_the_case_teaches_the_values_for_the_real_one(self):
        self.desk()
        probe = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PROBE | normal | Probe copy | req=RQ-1 ev=EVD-1\n## events: type | from | to | opts\n@set case=CS-PROBE\n"
                 + EVENTS.format(lat=20) + "## expect: case | nodes | opts\nCS-PROBE | usr.member=Failed,svc.loan,svc.stock,tbl.loan | by=author\n")
        self.put("30-probe", probe)
        self.simulate(case="CS-PROBE", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,tbl.loan"), mode="patch")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "probe copy")

    def test_a_cut_verdict_is_still_a_mismatch_but_not_a_reveal(self):
        """The counts and the pass list say which items failed, so EVERY failing verdict is a mismatch whatever the 3 KB cut shows;
        only the ids and the trace are recorded for what the reply really shows."""
        self.desk(("20-scenarios", ROW, WRONG))
        from unittest import mock
        with mock.patch.object(oracle, "SIMULATE_CAP", 420):
            reply = self.simulate(case="CS-BORROW-OK", detail="trace")
        shown = bool(reply.get("trace"))
        self.assertEqual(self.entry(CASE)["exposed"]["trace"], 1 if shown else 0)
        self.assertEqual(self.entry(CASE)["exposed"]["mismatch"], 1, "recorded even when the line did not fit")
        self.assertIs(self.entry(CASE)["firstPass"], False)


class LedgerCheats(Cheater):
    def test_edit_the_ledger_file_by_hand_state_and_exposure(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        data = self.ledger()
        data["items"][CASE]["state"] = "sealed"
        data["items"][CASE]["why"] = ""
        data["items"][CASE]["exposed"] = {"mismatch": 0, "ids": 0, "trace": 0, "build": 0}
        self.write_ledger(data)                                              # chain and seal are not recomputed
        gate = self.gate()                                                   # read-only: the gate already sees the damage
        self.assertEqual(gate["attestation"]["class"], "unverified-provenance")
        self.assertIn("provenance", {b["kind"] for b in gate["blockers"]})
        self.simulate()                                                      # the next write call demotes everything and says so for good
        data = self.ledger()
        self.assertEqual((data["provenance"], data["reason"]), ("unverified", "edited"))
        self.assertEqual({e["state"] for e in data["items"].values()}, {"revised"})
        outcome = self.verdict()
        self.assertTrue(outcome["unverified"])
        self.assertFalse(outcome["sealed_at_release"])
        self.assertIn("OR-005", tools.check(self.ws, self.name, mode="full", rule="OR")["diagnostics"])

    def test_the_flag_is_sticky_and_only_the_users_confirmation_clears_the_blocker(self):
        self.desk()
        data = self.ledger()
        data["items"][CASE]["verdicts"] = 99
        self.write_ledger(data)
        self.simulate()
        self.assertEqual(self.ledger()["provenance"], "unverified")
        self.simulate()
        self.assertEqual(self.ledger()["provenance"], "unverified", "sticky")
        self.assertIn("provenance", {b["kind"] for b in self.gate()["blockers"]})
        self.approve_all()
        self.assertNotIn("provenance", {b["kind"] for b in self.gate()["blockers"]}, "the user's confirmation of every item clears it")
        self.assertEqual(self.gate()["attestation"]["class"], "unverified-provenance", "and the badge still says what happened")

    def test_garbage_in_the_ledger_is_unreadable_and_flagged(self):
        self.desk()
        self.paths.oracle.write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.gate()["attestation"]["class"], "unverified-provenance")
        self.simulate()
        self.assertEqual(self.ledger()["reason"], "unreadable")

    def test_delete_the_ledger_file(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.paths.oracle.unlink()
        gate = self.gate()
        self.assertEqual(gate["attestation"]["class"], "unverified-provenance", "puts are on record, the ledger is gone")
        self.simulate()
        outcome = self.verdict()
        self.assertTrue(outcome["unverified"])
        self.assertEqual(self.ledger()["reason"], "deleted")
        self.assertFalse(outcome["sealed_at_release"])

    def test_a_first_put_without_a_ledger_is_not_a_deletion(self):
        self.init()
        self.assertFalse(self.paths.oracle.exists())
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "")
        self.put("00-space", desk_files()["00-space"])
        self.assertTrue(self.paths.oracle.exists(), "a put creates the ledger even when nothing was sealed")
        self.assertEqual(self.ledger()["provenance"], "ok")

    def test_a_red_put_first_does_not_make_the_next_clean_put_a_deletion(self):
        self.init()
        self.put("00-space", "#atlas 1\n## buildings: id | name | opts\nzone.x\n")        # red: arity
        self.assertTrue(self.paths.oracle.exists())
        self.put("00-space", desk_files()["00-space"])
        self.assertEqual(self.ledger()["provenance"], "ok")


class ConfirmationCheats(Cheater):
    def test_forge_the_confirmation_file_before_any_sheet_exists(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        ah8 = self.entry(CASE)["ah"][:8]
        self.confirm_file().write_text(f"- [x] CS-BORROW-OK {ah8}\n- [x] IV-STOCK-DOWN {self.entry(IV)['ah'][:8]}\nAPPROVE-ALL 000000000000\n", encoding="utf-8")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised", "no sheet printed these hashes: the lines are ignored")
        self.assertStopped(outcome, "forged confirmation without a sheet")

    def test_a_confirmation_for_an_older_text_does_not_cover_the_new_one(self):
        self.desk()
        self.confirm_ticks()
        self.put("20-scenarios", patch_expect("usr.member,svc.loan"), mode="patch")
        self.assertEqual(self.verdict()["state"], "revised")


FAULT = ' fault={"mode":"fail"}'
ALL_IDLE = "usr.member=Idle,svc.loan=Idle,svc.stock=Idle,tbl.loan=Idle"


def events_text(lat: int, fault: bool = False) -> str:
    text = EVENTS.format(lat=lat)
    return text.replace(f"latencyMs={lat}\n", f"latencyMs={lat}{FAULT}\n", 1) if fault else text


def cases_slice(rows) -> str:
    """A slice of cases ``(id, latencyMs, expect nodes, fault?)``: each has the six events of the desk case (a different latency is a
    different fingerprint; the same latency and fault is an exact copy)."""
    text = "#atlas 1\n## cases: id | cat | title | opts\n"
    text += "".join(f"{cid} | normal | Case {cid} | req=RQ-1 ev=EVD-1\n" for cid, *_ in rows)
    text += "## events: type | from | to | opts\n"
    text += "".join(f"@set case={cid}\n" + events_text(lat, fault) for cid, lat, _nodes, fault in rows)
    text += "## expect: case | nodes | opts\n"
    return text + "".join(f"{cid} | {nodes} | by=author\n" for cid, _lat, nodes, _fault in rows)


class CutAndCopyCheats(Cheater):
    """The reply is a 3 KB window on a larger result, and a copy of a case is the same case: neither may give free bits."""

    def test_failing_fillers_behind_the_cut_are_ledgered_so_probing_the_target_costs_its_exposure(self):
        self.desk()
        n = 60
        self.assertEqual(self.put("30-fill", cases_slice([(f"CS-FILL-{i:02d}", 100 + i, ALL_IDLE, False) for i in range(n)]))["status"], "committed")
        self.assertEqual(self.put("40-tgt", cases_slice([("CS-TGT", 7, "usr.member=Idle", True)]))["status"], "committed")
        reply = self.simulate()
        self.assertGreater(reply.get("notShown", 0), 0, "the cut triggered: some failing lines are not in the reply")
        items = self.ledger()["items"]
        failing = [k for k, e in items.items() if e["kind"] == "case" and e["firstPass"] is False]
        self.assertEqual(len(failing), n + 1, "every failing case, shown or not, has a first verdict that failed")
        self.assertTrue(all(items[k]["exposed"]["mismatch"] == 1 for k in failing))
        self.assertEqual(self.entry("case:CS-TGT")["exposed"]["mismatch"], 1)
        self.put("40-tgt", patch_expect("svc.loan=Idle", "CS-TGT"), mode="patch")            # probing one node at a time
        self.simulate()
        self.put("40-tgt", patch_expect("usr.member=Failed,svc.loan=Completed,svc.stock=Failed,tbl.loan=Completed", "CS-TGT"), mode="patch")
        outcome = self.verdict("case:CS-TGT")
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "probing behind the cut")

    def test_a_cut_pair_verdict_is_still_a_mismatch(self):
        self.desk(("20-scenarios", "class=DIVERGED", "class=NO_DIFFERENCE"))
        from unittest import mock
        with mock.patch.object(oracle, "SIMULATE_CAP", 420):
            reply = self.simulate()
        self.assertGreater(reply.get("notShown", 0), 0)
        self.assertEqual(self.entry(IV)["exposed"]["mismatch"], 1)
        self.assertIs(self.entry(IV)["firstPass"], False)

    def test_a_shotgun_of_exact_copies_written_in_one_put_leaves_no_sealed_survivor(self):
        self.desk()
        grid = [(a, b) for a in ("Completed", "Failed", "Blocked") for b in ("Completed", "Failed", "Blocked")]
        rows = [(f"CS-SG-{i:02d}", 7, f"svc.stock={a},svc.loan={b}", True) for i, (a, b) in enumerate(grid)]
        reply = self.put("30-shots", cases_slice(rows))
        self.assertEqual(reply["oracle"]["sealed"], len(grid), "all copies are sealed before any result exists")
        winners = [x for x in self.simulate()["pass"] if x.startswith("CS-SG")]
        self.assertEqual(len(winners), 1, "one simulate names the passing copy")
        key = f"case:{winners[0]}"
        self.assertEqual(self.entry(key)["exposed"], {"mismatch": 0, "ids": 0, "trace": 0, "build": 0}, "the survivor itself was never shown anything")
        self.assertEqual(self.entry(key)["state"], "revised", "but its copies were: the group is one exposure unit")
        rm = "#atlas 1\n## expect: case | nodes | opts\n" + "".join(f"-CS-SG-{i:02d}\n" for i in range(len(grid)) if f"CS-SG-{i:02d}" != winners[0])
        self.put("30-shots", rm, mode="patch")
        self.simulate()
        outcome = self.verdict(key)
        self.assertEqual(outcome["state"], "revised")
        self.assertIn("revised", outcome["blocked"])
        self.assertStopped(outcome, "shotgun of copies")

    def test_a_shotgun_of_intervention_copies_with_different_classes_leaves_no_sealed_survivor(self):
        self.desk()
        rows = "".join(f"IV-SG-{i} | disable_module | Stock service is down while borrowing | target=svc.stock applies=CS-BORROW-OK class={c}\n"
                       for i, c in enumerate(("NO_DIFFERENCE", "CONVERGED", "DIVERGED")))
        self.put("30-ivs", "#atlas 1\n## interventions: id | type | description | opts\n" + rows)
        winners = [x for x in self.simulate()["pass"] if x.startswith("IV-SG")]
        self.assertEqual(len(winners), 1)
        key = f"intervention:{winners[0].split(' ')[0]}"
        self.assertEqual(self.entry(key)["state"], "revised")
        self.assertStopped(self.verdict(key), "intervention shotgun")

    def test_a_probe_that_only_renames_the_steps_is_the_same_case(self):
        self.desk()
        renamed = events_text(20)
        for i, old in enumerate(("R-BORROW", "R-CHECK", "R-WRITE", "R-RET-STOCK", "R-RET-TABLE", "R-RET-MEMBER")):
            renamed = renamed.replace(old, f"R-OTHER-{i}")
        probe = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PROBE | normal | Probe | req=RQ-1 ev=EVD-1\n## events: type | from | to | opts\n@set case=CS-PROBE\n"
                 + renamed + "## expect: case | nodes | opts\nCS-PROBE | " + ALL_IDLE + " | by=author\n")
        self.put("30-probe", probe)
        self.assertNotEqual(self.entry("case:CS-PROBE")["state"], "unregistered")
        self.simulate(case="CS-PROBE", detail="trace")
        self.assertEqual(self.entry(CASE)["state"], "revised", "the real case shares the exposure at once, before any edit")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,tbl.loan"), mode="patch")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertStopped(outcome, "probe with renamed steps")

    def test_a_confirmed_copy_is_not_demoted_by_a_sibling(self):
        self.desk()
        probe = cases_slice([("CS-TWIN", 20, ALL_IDLE, False)]).replace("Case CS-TWIN", "Twin")
        self.confirm_ticks()
        self.put("30-twin", probe)
        self.simulate(case="CS-TWIN")
        self.assertEqual(self.states()[CASE], "confirmed", "the user's confirmation of the real case stands")


class LedgerAnchorCheats(Cheater):
    def test_putting_an_older_copy_of_the_ledger_back_is_rolled_back(self):
        self.desk(("20-scenarios", ROW, WRONG))
        before = self.paths.oracle.read_bytes()
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.assertEqual(self.entry(CASE)["exposed"]["ids"], 1)
        self.paths.oracle.write_bytes(before)                                  # a plain cp before, a plain cp after
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "rolled-back")
        self.assertEqual(self.gate()["attestation"]["class"], "unverified-provenance", "the read-only gate already sees it")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.ledger()["reason"], "rolled-back")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "revised")
        self.assertTrue(outcome["unverified"])
        self.assertStopped(outcome, "ledger rollback")
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "", "the next write moved the tick past the anchor: no second report")
        self.assertEqual(self.ledger()["provenance"], "unverified", "sticky")

    def test_a_recomputed_chain_without_the_anchor_is_edited(self):
        self.desk()
        data = self.ledger()
        data["items"][CASE]["verdicts"] = 9
        self.write_ledger(data, rechain=True, anchor=False)
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "edited")

    def test_a_garbage_anchor_is_edited_and_a_missing_anchor_alone_is_not_a_report(self):
        self.desk()
        (self.paths.dot / oracle.ANCHOR_NAME).write_text("{ nope", encoding="utf-8")
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "edited")
        (self.paths.dot / oracle.ANCHOR_NAME).unlink()
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "", "a ledger without an anchor is a ledger from before the anchor existed")

    def test_one_giant_put_then_a_deleted_ledger_is_a_deletion(self):
        self.init()
        files = desk_files(("20-scenarios", ROW, WRONG))
        body = "#atlas 1\n" + "".join("".join(line + "\n" for line in files[n].split("\n") if not line.startswith("#atlas"))
                                      for n in ("00-space", "10-structure", "20-scenarios"))
        self.assertEqual(self.put("10-all", body)["status"], "committed")
        self.assertEqual(json.loads((self.paths.dot / "puts.json").read_text(encoding="utf-8"))["puts"], 1)
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.paths.oracle.unlink()
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "deleted", "one put on record, ledger and anchor remember")
        (self.paths.dot / oracle.ANCHOR_NAME).unlink()
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "deleted", "the put counter alone remembers a single put")
        path = self.paths.slices / "10-all.atl"
        path.write_text(path.read_text(encoding="utf-8").replace("svc.stock=Failed", "svc.stock=Completed"), encoding="utf-8", newline="\n")
        self.simulate()
        outcome = self.verdict()
        self.assertTrue(outcome["unverified"])
        self.assertFalse(outcome["sealed_at_release"])


class VacuityCheats(Cheater):
    def no_events(self):
        self.desk()
        self.put("20-scenarios", "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\n", mode="patch")
        self.put("20-scenarios", patch_expect("svc.stock=Idle,tbl.loan=Idle"), mode="patch")
        self.put("20-scenarios", "#atlas 1\n## interventions: id | type | description | opts\n-IV-STOCK-DOWN\n", mode="patch")

    def test_a_case_without_events_that_asserts_idle_is_vacuous_at_the_gate_and_in_check(self):
        self.no_events()
        self.assertTrue(self.simulate()["clean"], "Idle holds trivially: the verdict alone cannot tell")
        self.bite()
        gate = self.gate()
        self.assertFalse(gate["ok"])
        self.assertIn(("vacuous", "OR-003"), [(b["kind"], b["rule"]) for b in gate["blockers"] if b["id"] == "CS-BORROW-OK"])
        reply = tools.check(self.ws, self.name, mode="full")
        self.assertIn("OR-003", reply["diagnostics"])
        self.assertIn("no events", reply["diagnostics"])

    def test_idle_on_nodes_the_events_never_touch_does_not_count_toward_the_floor(self):
        self.desk()
        self.put("20-scenarios", "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\nCall | usr.member | svc.loan | ruleId=R-BORROW\n", mode="patch")
        self.put("20-scenarios", patch_expect("svc.stock=Idle,tbl.loan=Idle"), mode="patch")
        self.put("20-scenarios", "#atlas 1\n## interventions: id | type | description | opts\n-IV-STOCK-DOWN\n", mode="patch")
        self.simulate()
        self.bite()
        self.assertIn(("vacuous", "OR-003"), [(b["kind"], b["rule"]) for b in self.gate()["blockers"]])

    def test_a_held_assertion_without_a_mutant_run_for_this_model_blocks_as_no_bite(self):
        self.desk()
        self.simulate()
        self.assertEqual([b["kind"] for b in self.gate()["blockers"]], ["no-bite"])
        self.bite()
        self.assertEqual(self.gate()["blockers"], [])
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock", "CS-BORROW-OK"), mode="patch")        # a new model: the old run is stale
        self.assertIn("no-bite", {b["kind"] for b in self.gate()["blockers"]})

    def test_a_timeboxed_bite_run_makes_progress_across_calls(self):
        self.desk()
        self.simulate()
        first = oracle.bite_run(self.ws, self.name, budget=0.0)
        self.assertTrue(first["partial"])
        self.assertEqual(self.gate()["blockers"][0]["kind"], "no-bite")
        second = oracle.bite_run(self.ws, self.name)
        self.assertFalse(second["partial"])
        self.assertEqual(self.gate()["blockers"], [])


class LimitsTests(Cheater):
    """Each test PINS a cheat that the design cannot stop (see the module docstring); a change of behaviour must be a decision."""

    def test_L1_copying_the_printed_sheet_lines_is_accepted_as_the_users_confirmation(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        sheet = self.sheet()
        lines = [re.sub(r"^- \[ \]", "- [x]", x).split(" | ")[0] for x in sheet.splitlines() if x.startswith("- [ ] ")]
        self.confirm_file().write_text("\n".join(lines) + "\n", encoding="utf-8")
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "confirmed")
        self.assertEqual(self.gate()["attestation"]["class"], "attested", "LIMIT L1: this is what a forger leaves behind: a user-confirmed item")
        self.assertTrue(self.paths.slices.joinpath("20-scenarios.atl").exists())

    def test_L2_an_edit_with_a_recomputed_chain_and_seal_is_not_detected(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.entry(CASE)["state"], "revised")
        data = self.ledger()
        entry = data["items"][CASE]
        entry["state"], entry["why"] = "sealed", ""
        entry["exposed"] = {"mismatch": 0, "ids": 0, "trace": 0, "build": 0}
        self.write_ledger(data, rechain=True)                              # chain, seal AND anchor
        self.simulate()
        outcome = self.verdict()
        self.assertEqual(outcome["state"], "sealed", "LIMIT L2: nothing can tell")
        self.assertFalse(outcome["unverified"])

    def test_L3_deleting_the_whole_dot_atlas_directory_starts_a_clean_ledger(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.simulate(case="CS-BORROW-OK", detail="ids")
        (self.paths.dot / "oracle.json").unlink()
        (self.paths.dot / "puts.json").unlink()
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "deleted", "the anchor remembers that a ledger existed")
        (self.paths.dot / oracle.ANCHOR_NAME).unlink()
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "", "no trace of the earlier ledger is left")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.entry(CASE)["state"], "sealed", "LIMIT L3 (a fresh git clone does the same: .atlas/ is git-ignored)")
        self.assertEqual(self.ledger()["provenance"], "ok")
        self.assertTrue(list(self.paths.history.glob("20-scenarios.*")), "what is left behind: the archived slice versions")

    def test_L4_adjusting_the_events_to_the_expectation_keeps_the_item_sealed(self):
        self.desk(("20-scenarios", ROW, WRONG))
        self.assertEqual(self.simulate()["clean"], False)
        fit = "#atlas 1\n## events: type | from | to | opts\n@set case=CS-BORROW-OK\n" + EVENTS.format(lat=20).replace(
            "Call | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20", "Call | svc.loan | svc.stock | ruleId=R-CHECK latencyMs=20 fault={\"mode\":\"fail\"}")
        reply = self.put("20-scenarios", fit, mode="patch")
        self.assertEqual(reply["status"], "committed", reply)
        self.assertEqual(self.entry(CASE)["state"], "sealed")
        self.assertEqual(self.entry(CASE)["exposed"]["mismatch"], 1, "the cheater saw the mismatch count and then fitted the events")
        self.assertEqual(self.entry(CASE)["revisions"], 0, "LIMIT L4: only the expectation is attested, the events may be fixed")

    def test_L6_one_changed_latency_makes_the_probe_a_different_case(self):
        self.desk()
        probe = ("#atlas 1\n## cases: id | cat | title | opts\nCS-PROBE | normal | Probe | req=RQ-1 ev=EVD-1\n## events: type | from | to | opts\n@set case=CS-PROBE\n"
                 + EVENTS.format(lat=21) + "## expect: case | nodes | opts\nCS-PROBE | usr.member=Failed,svc.loan,svc.stock,tbl.loan | by=author\n")
        self.put("30-probe", probe)
        self.simulate(case="CS-PROBE", detail="ids")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,tbl.loan"), mode="patch")
        self.assertEqual(self.entry(CASE)["state"], "sealed", "LIMIT L6: the fingerprint is exact")

    def test_L7_an_older_ledger_and_its_matching_anchor_put_back_together_are_not_detected(self):
        self.desk(("20-scenarios", ROW, WRONG))
        old_ledger, old_anchor = self.paths.oracle.read_bytes(), (self.paths.dot / oracle.ANCHOR_NAME).read_bytes()
        self.simulate(case="CS-BORROW-OK", detail="ids")
        self.paths.oracle.write_bytes(old_ledger)
        (self.paths.dot / oracle.ANCHOR_NAME).write_bytes(old_anchor)
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "", "LIMIT L7: both files are consistent with each other")
        self.put("20-scenarios", patch_expect("usr.member,svc.loan,svc.stock,tbl.loan"), mode="patch")
        self.assertEqual(self.entry(CASE)["state"], "sealed")


class SuiteSummaryTests(Cheater):
    def test_the_limits_are_listed_in_the_module_documentation(self):
        for limit in ("L1", "L2", "L3", "L4", "L5", "L6", "L7"):
            self.assertIn(f"* {limit} ", __doc__)
        self.assertIn("attestation, not authentication", __doc__)
        self.assertIn("a fresh clone", __doc__)


if __name__ == "__main__":
    unittest.main()
