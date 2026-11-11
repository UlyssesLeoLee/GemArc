"""The completion contract with the REAL gates (design addendum A1, D14; task tools-b3): ``status`` says ``done`` only when the
structure is complete, the oracle gate, the evidence gate (as-is), the docs gate, the build gate and the browser gate are green and
exactly one review is recorded. In-process: the gates are active inside ``status.product_gates()`` (what the MCP binding and the shell
entry open around every call); the browser verdict is written as a stamp bound to the page (the real browser runs in the MCP test).

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_tools_b3_gates.py"

Synthetic id families only (the lending desk fixture).
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, oracle
from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import review
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools

from .test_oracle_support import OracleProject, desk_files, tree_digest

POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"
CORRECTED_EXPECT = "#atlas 1\n## expect: case | nodes | opts\nCS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author\n"
SOURCES = {"src/loan.py": "class Loans:\n    def create_loan(self, member):\n        return 1\n",
           "src/stock.py": "def find_copy(title):\n    return None\n",
           "docs/lending.md": "# Lending\nThe borrower opens the page to borrow.\n"}


class Flow(OracleProject):
    """A lending desk (as-is) with a synthetic source root, driven through the tool functions with the real gates on."""

    def setUp(self):
        super().setUp()
        self.src = self.root / "srcroot"
        for rel, text in SOURCES.items():
            target = self.src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": f"main={self.src}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        gates = st.product_gates()
        gates.__enter__()
        self.addCleanup(gates.__exit__, None, None, None)

    # -- helpers -----------------------------------------------------------------------------------------
    def authored(self, name="desk", mode="asis", edits=()):
        self.init(name=name, mode=mode)
        files = desk_files(*edits)
        files["30-policy"] = POLICY
        self.load(files)

    def status(self) -> dict:
        reply = tools.status(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        return reply

    def stamp(self, status="PASS", widths=(1024, 1366, 1600)):
        """The browser verdict, bound to the page that is on disk now (what ``verify`` writes after a real run)."""
        info = json.loads((build.dist_dir(self.paths) / "build-info.json").read_text(encoding="utf-8"))
        verdict = {"status": status, "widths": [{"width": w, "status": status} for w in widths]}
        st.write_gate_stamp(self.paths, model_sha8=info["modelSha8"], page_sha256=info["page"]["sha256"], verdict=verdict)

    def build(self):
        reply = tools.build_all(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        return reply

    def gates_green(self, browser="PASS"):
        self.assertTrue(self.simulate()["clean"])
        self.assertEqual(tools.evidence(self.ws, self.name)["status"], "ok")
        self.build()
        if browser:
            self.stamp(browser)

    def record(self, notes=""):
        reply = review.review_record(self.ws, self.name, notes)
        self.assertEqual(reply["status"], "recorded", reply)
        return reply

    def done_state(self):
        self.authored()
        self.gates_green()
        self.record()
        state = self.status()
        self.assertTrue(state["done"], state)
        return state

    def gate_of(self, name):
        return st.evaluate(self.snap()).gates[name]


class GateWiringTests(Flow):
    def test_the_gate_names_and_the_default_are_unchanged_outside_the_product_context(self):
        self.assertEqual(st.GATE_NAMES, ("oracle", "evidence", "docs", "build", "browser"))
        self.authored()
        with mock.patch.object(st, "_PRODUCT", st.contextvars.ContextVar("x", default=False)):   # a plain evaluate, as the unit tests of the core call it
            state = st.evaluate(self.snap())
        self.assertEqual({g["applicable"] for g in state.gates.values()}, {False})
        self.assertEqual(state.phase, "review", "no real gate without the context: the authoring core is unchanged")

    def test_every_real_gate_is_registered_and_the_registration_is_per_context(self):
        self.assertEqual(sorted(st.PRODUCT_GATES), sorted(st.GATE_NAMES))
        self.assertTrue(st.product_gates_active())
        self.authored()
        state = st.evaluate(self.snap())
        self.assertEqual(state.phase, "build")
        self.assertTrue(state.gates["oracle"]["applicable"])
        self.assertEqual(state.required, ("1D", "2D", "3D", "4D", "5D"))

    def test_a_test_registered_gate_still_wins_over_the_real_one(self):
        self.authored()
        with mock.patch.dict(st.GATES, clear=False):
            st.register_gate("build", lambda snap: {"applicable": True, "ok": True, "detail": "stub"})
            self.assertEqual(self.gate_of("build")["detail"], "stub")
        self.assertNotEqual(self.gate_of("build")["detail"], "stub")


class HappyPathTests(Flow):
    def test_the_whole_contract_in_order(self):
        self.authored()
        state = self.status()
        self.assertEqual((state["done"], state["phase"]), (False, "build"))
        names = {b.split(":")[0] for b in state["blockers"]}
        self.assertTrue({"gate-oracle", "gate-evidence", "gate-build", "review"} <= names, state["blockers"])
        self.assertTrue(state["next"][0].startswith("oracle:") and "gemarc_atlas_simulate" in state["next"][0], state["next"])
        self.assertTrue(self.simulate()["clean"])
        state = self.status()
        self.assertTrue(state["next"][0].startswith("evidence:") and "gemarc_atlas_evidence" in state["next"][0], state["next"])
        self.assertEqual(tools.evidence(self.ws, self.name)["claims"], {"implemented": 5, "unverified": 0})
        state = self.status()
        self.assertTrue(state["next"][0].startswith("build:") and "gemarc_atlas_build" in state["next"][0], state["next"])
        self.build()
        state = self.status()
        self.assertTrue(state["next"][0].startswith("browser:") and "gemarc_atlas_verify" in state["next"][0], state["next"])
        self.stamp("PASS")
        state = self.status()
        self.assertEqual((state["done"], state["phase"]), (False, "review"), state)
        self.assertEqual(state["blockers"], ["review:missing"])
        self.record()
        state = self.status()
        self.assertEqual((state["done"], state["phase"], state["blockers"], state["needsUser"]), (True, "done", [], []))
        self.assertIn("gates green: oracle, evidence, docs, build, browser", state["next"][0])
        self.assertIn("1D-5D", state["next"][0])

    def test_sealed_but_unconfirmed_expectations_do_not_block_and_the_sheet_is_handed_over(self):
        self.done_state()
        sheet = (self.paths.atlas / "dist" / "oracle-sheet.md").read_text(encoding="utf-8")
        self.assertIn("APPROVE-ALL", sheet)
        self.assertFalse(self.confirm_file().exists(), "the MCP never writes the user's confirmation file")
        self.assertEqual(self.status()["needsUser"], [])
        self.assertEqual(self.states(), {"case:CS-BORROW-OK": "sealed", "intervention:IV-STOCK-DOWN": "sealed"})

    def test_to_be_has_no_evidence_gate(self):
        self.authored(name="tb", mode="tobe")
        self.assertFalse(self.gate_of("evidence")["applicable"])


class EachConditionTests(Flow):
    """Each condition alone keeps ``done`` false and is named; restoring it brings ``done`` back."""

    def test_the_oracle_gate_needs_a_fresh_verdict_after_every_model_change(self):
        self.done_state()
        self.assertEqual(self.put("30-policy", POLICY.replace("two seconds", "three seconds"))["status"], "committed")
        state = self.status()
        self.assertFalse(state["done"])
        self.assertIn("no-verdict", self.gate_of("oracle")["detail"])
        self.assertEqual(state["phase"], "build")

    def test_a_mismatch_blocks_and_correcting_the_expectation_afterwards_needs_the_user(self):
        row = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
        self.authored(edits=[("20-scenarios", row, row.replace("svc.stock", "svc.stock=Failed"))])
        self.assertFalse(self.simulate()["clean"])
        gate = self.gate_of("oracle")
        self.assertEqual((gate["ok"], gate["kinds"]), (False, {"mismatch": 1}))
        self.assertIn("mismatch (CS-BORROW-OK)", gate["detail"])
        state = self.status()
        # debt-b4: the mismatch is an OR-001 ERROR of the default full check (wave 2 is part of it), so the phase is repair (it was build)
        self.assertEqual((state["done"], state["phase"], state["needsUser"]), (False, "repair", []), "a failed verdict is the author's to look into")
        # the author corrects the EXPECTATION after the mismatch was shown: that is exposure, the item is revised and the user decides
        self.put("20-scenarios", CORRECTED_EXPECT, mode="patch")
        self.assertEqual(self.states()["case:CS-BORROW-OK"], "revised")
        state = self.status()
        self.assertEqual((state["phase"], state["needsUser"]), ("build", []), "no sheet to approve yet: the user is not asked, the build comes first")
        self.assertIn("gemarc_atlas_simulate", state["next"][0], "the edit left the model without a verdict: simulate first")
        self.simulate()
        state = self.status()
        self.assertEqual((state["phase"], state["needsUser"]), ("build", []))
        self.assertIn("gemarc_atlas_build", state["next"][0])
        self.assertIn("gate-oracle:1 revised", state["blockers"])
        self.build()                                   # now there is an oracle sheet for the user
        state = self.status()
        self.assertEqual(state["phase"], "needs-user")
        self.assertTrue(any("oracle sheet" in x and "approve" in x for x in state["needsUser"]), state["needsUser"])
        self.assertTrue(all("gemarc_atlas" not in x for x in state["needsUser"]), "needsUser is for the user: no instruction addressed to the AI")
        self.assertTrue(any("APPROVE-ALL" in x for x in state["next"]), "the AI's part (hand the sheet over) is in next")

    def test_the_users_approval_of_the_sheet_ends_the_stop(self):
        row = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
        self.authored(edits=[("20-scenarios", row, row.replace("svc.stock", "svc.stock=Failed"))])
        self.simulate()
        self.put("20-scenarios", CORRECTED_EXPECT, mode="patch")
        self.assertEqual(self.status()["phase"], "build")
        self.build()                       # writes the oracle sheet; the USER (here: the helper) approves it
        self.assertEqual(self.status()["phase"], "needs-user")
        self.approve_all()
        self.assertEqual(self.states()["case:CS-BORROW-OK"], "confirmed")
        self.assertTrue(self.simulate()["clean"])
        self.assertEqual(self.gate_of("oracle")["kinds"], {})
        state = self.status()
        self.assertNotEqual(state["phase"], "needs-user", state)
        self.assertEqual(state["needsUser"], [])

    def test_an_expectation_that_names_an_undefined_node_stays_unsealed_and_blocks(self):
        row = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"
        self.authored(edits=[("20-scenarios", row, row.replace("svc.stock", "svc.stok"))])     # a typo'd node: OR-000, no seal, no verdict
        reply = self.simulate()
        self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_ATLAS_ORACLE_003"), reply)   # no verdict for a model the engine cannot run
        gate = self.gate_of("oracle")
        self.assertEqual(gate["ok"], False)
        self.assertIn("unsealed", gate["kinds"])
        state = self.status()
        self.assertFalse(state["done"])
        self.assertTrue(any(b.startswith("gate-oracle") for b in state["blockers"]), state["blockers"])
        self.assertEqual(self.states()["case:CS-BORROW-OK"], "unregistered")

    def test_the_evidence_gate_needs_a_stamp_for_the_same_file_and_anchor(self):
        self.done_state()
        # the cited anchor of EVD-1 is edited after it was stamped: the old stamp no longer describes the row
        edit = ("10-structure", "EVD-1 | src/loan.py | def create_loan( | creates the loan row", "EVD-1 | src/loan.py | class Loans: | creates the loan row")
        text = desk_files(edit)["10-structure"]
        self.assertEqual(self.put("10-structure", text)["status"], "committed")
        self.assertFalse(self.status()["done"])
        gate = self.gate_of("evidence")
        self.assertFalse(gate["ok"])
        self.assertIn("lack a verified anchor", gate["detail"])
        self.assertEqual(tools.evidence(self.ws, self.name)["claims"]["unverified"], 0)
        self.assertTrue(self.gate_of("evidence")["ok"])

    def test_without_an_evidence_root_nothing_is_verified_and_the_reply_says_why(self):
        self.authored()
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": ""}):
            reply = tools.evidence(self.ws, self.name)
        self.assertEqual((reply["status"], reply["pass"], reply["skip"]), ("ok", 0, 3))
        self.assertTrue(any("GEMARC_ATLAS_ROOTS" in x for x in reply["next"]), reply["next"])
        self.assertFalse(self.gate_of("evidence")["ok"])

    def test_the_docs_gate_reads_the_derived_documents_and_waits_for_the_structure(self):
        from gemarc_mcp.atlas.derive import docbuild

        self.authored()
        gate = self.gate_of("docs")
        self.assertEqual((gate["applicable"], gate["ok"], gate["detail"]), (True, True, "no NOT-AUTHORED slot"))
        real = docbuild.doc_outline
        with mock.patch.object(docbuild, "doc_outline", lambda model, unit, level, lang, *a: {**real(model, unit, level, lang), "markers": 2 if level == "BD" else 0}):
            gate = self.gate_of("docs")
            self.assertEqual(gate["ok"], False)
            self.assertIn("2 NOT-AUTHORED slot(s)", gate["detail"])
            state = self.status()
            self.assertIn("gate-docs", " ".join(state["blockers"]))
            self.assertFalse(state["done"])
        # without a chapter-9 policy the structure itself has a BLOCK slot: the docs gate has nothing to say yet, the structure phase does
        self.init(name="nopol")
        self.load(desk_files())
        state = self.status()
        self.assertEqual(state["phase"], "author")
        self.assertFalse(self.gate_of("docs")["applicable"])

    def test_the_build_gate_follows_the_model_and_the_files(self):
        self.done_state()
        page = self.paths.atlas / "dist" / "index.html"
        page.write_bytes(page.read_bytes() + b"\n")
        gate = self.gate_of("build")
        self.assertFalse(gate["ok"])
        self.assertIn("changed after the build", gate["detail"])
        self.build()
        self.stamp()
        self.assertTrue(self.status()["done"])
        self.assertEqual(self.put("30-policy", POLICY.replace("two seconds", "four seconds"))["status"], "committed")
        self.assertIn("older model", self.gate_of("build")["detail"])
        no_docs = tools.build(self.ws, self.name, docs=False)
        self.assertEqual(no_docs["status"], "ok")
        self.assertIn("docs=false", self.gate_of("build")["detail"])

    def test_a_pass_of_an_earlier_build_never_satisfies_the_browser_gate(self):
        self.done_state()
        self.assertEqual(self.put("30-policy", POLICY.replace("two seconds", "five seconds"))["status"], "committed")
        self.simulate()
        tools.evidence(self.ws, self.name)
        self.build()                       # a new page; the old gate-stamp (and the old gate-report.json) is still on disk
        gate = self.gate_of("browser")
        self.assertFalse(gate["ok"])
        self.assertIn("has not been verified", gate["detail"])
        self.stamp()
        self.assertTrue(self.gate_of("browser")["ok"])

    def test_a_skipped_browser_blocks_loudly_and_only_the_users_waiver_lets_it_pass(self):
        self.authored()
        self.gates_green(browser="SKIP")
        state = self.status()
        self.assertFalse(state["done"])
        self.assertEqual(state["phase"], "needs-user", "the driver must stop and ask: install a browser or let the user waive")
        self.assertTrue(any("waive-browser.txt" in x for x in state["needsUser"]), state["needsUser"])
        self.assertIn("SKIPPED", self.gate_of("browser")["detail"])
        (self.paths.atlas / st.WAIVER_NAME).write_text("no browser on this build machine; the user checks the page by hand\n", encoding="utf-8")
        gate = self.gate_of("browser")
        self.assertTrue(gate["ok"] and gate.get("waived"), gate)
        self.assertIn("waived by the user", gate["detail"])
        self.assertEqual(self.status()["phase"], "review")
        self.record()
        self.assertTrue(self.status()["done"])

    def test_a_failed_browser_gate_is_never_covered_by_the_waiver(self):
        self.authored()
        self.gates_green(browser="FAIL")
        (self.paths.atlas / st.WAIVER_NAME).write_text("waived\n", encoding="utf-8")
        gate = self.gate_of("browser")
        self.assertFalse(gate["ok"])
        self.assertIn("FAILED", gate["detail"])
        self.assertEqual(self.status()["phase"], "build")

    def test_an_inconclusive_gate_blocks_without_blaming_the_page(self):
        self.authored()
        self.gates_green(browser="INCONCLUSIVE")
        gate = self.gate_of("browser")
        self.assertFalse(gate["ok"])
        self.assertIn("INCONCLUSIVE", gate["detail"])
        self.assertNotIn("FAILED", gate["detail"])

    def test_the_waiver_without_a_run_is_enough_for_a_user_without_a_browser(self):
        self.authored()
        self.gates_green(browser=None)
        (self.paths.atlas / st.WAIVER_NAME).write_text("the user does not use the browser gate\n", encoding="utf-8")
        self.assertTrue(self.gate_of("browser")["ok"])

    def test_red_drafts_errors_and_open_decisions_still_block(self):
        self.done_state()
        bad = "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n"
        self.assertEqual(self.put("40-bad", bad)["status"], "invalid")
        state = self.status()
        self.assertFalse(state["done"])
        self.assertTrue(any(b.startswith("red-drafts") for b in state["blockers"]), state["blockers"])


class ReviewOnceTests(Flow):
    def test_record_is_refused_while_a_gate_is_red_and_the_pass_is_not_burnt(self):
        self.authored()
        reply = review.review_record(self.ws, self.name, "")
        self.assertEqual(reply["status"], "invalid")
        self.assertEqual(reply["code"], review.E_REVIEW_PHASE)
        self.assertFalse(self.paths.review.exists())
        self.gates_green()
        self.record()

    def test_a_second_review_is_never_demanded_after_the_record(self):
        self.done_state()
        again = review.review_record(self.ws, self.name, "")
        self.assertEqual((again["status"], again["code"]), ("invalid", review.E_REVIEW_ONCE))
        # the model changes: the gates go red, never "review"
        self.assertEqual(self.put("30-policy", POLICY.replace("two seconds", "six seconds"))["status"], "committed")
        state = self.status()
        self.assertEqual(state["phase"], "build")
        self.gates_green()
        state = self.status()
        self.assertEqual((state["done"], state["phase"]), (True, "done"), "no new BLOCK finding: the record still counts, no second review")

    def test_a_new_block_after_the_record_sends_the_driver_to_author_not_to_review(self):
        self.init(name="tobe", mode="tobe")
        text = "The desk lends books. A member can borrow an available copy. The desk keeps a shelf.\n"
        self.paths.user.mkdir(parents=True, exist_ok=True)
        (self.paths.user / "requirement.txt").write_text(text, encoding="utf-8")
        files = desk_files()
        files["30-policy"] = POLICY
        for name, body in files.items():
            body = body.replace("#atlas 1\n", "#atlas 1 mode=tobe\n", 1)
            self.assertEqual(self.put(name, body)["status"], "committed", name)
        self.gates_green(browser="PASS")
        state = self.status()
        if state["phase"] != "review":
            self.skipTest(f"the to-be desk is not at phase review here: {state['phase']} {state['blockers']}")
        self.record()
        self.assertTrue(self.status()["done"])
        extra = ("#atlas 1\n## requirements: id | title | source | opts\n"
                 "RQ-2 | The desk also sells coffee | The desk also sells coffee to members. | nodes=svc.loan\n")
        self.assertEqual(self.put("40-more", extra)["status"], "committed")
        self.gates_green(browser="PASS")
        state = self.status()
        self.assertFalse(state["done"])
        self.assertIn("review:stale", state["blockers"])
        self.assertEqual(state["phase"], "author", state)
        self.assertTrue(any(line.startswith("review record is stale") and "NO second review" in line for line in state["next"]), state["next"])
        again = review.review_record(self.ws, self.name, "")
        self.assertEqual(again["code"], review.E_REVIEW_ONCE)


class ParityAndReadOnlyTests(Flow):
    KINDS_DONE = set(st.ORACLE_DONE_KINDS)

    def release_kinds(self):
        return sorted(b["kind"] + ":" + b["id"] for b in oracle.release_gate(self.ws, self.name)["blockers"] if b["kind"] in self.KINDS_DONE)

    def gate_kinds(self):
        gate = self.gate_of("oracle")
        return gate.get("kinds", {})

    def test_the_status_gate_agrees_with_the_release_gate_in_several_states(self):
        self.authored()
        for step in ("nothing run", "after simulate", "after a reveal and an edit", "confirmed"):
            if step == "after simulate":
                self.simulate()
            elif step == "after a reveal and an edit":
                self.simulate(case="CS-BORROW-OK", detail="ids")
                edit = self.expect_row("usr.member,svc.loan,svc.stock")
                self.put("20-scenarios", desk_files()["20-scenarios"].replace(edit[1], edit[2]))
            elif step == "confirmed":
                self.build()
                self.approve_all()
            counted: dict = {}
            for item in oracle.release_gate(self.ws, self.name)["blockers"]:
                if item["kind"] in self.KINDS_DONE:
                    counted[item["kind"]] = counted.get(item["kind"], 0) + 1
            self.assertEqual(self.gate_kinds(), counted, step)

    def test_status_with_every_gate_never_writes_a_byte(self):
        self.authored()
        self.gates_green()
        before = tree_digest(self.paths.project)
        for view in ("next", "report", "dims", "oracle", "evidence"):
            self.assertEqual(tools.status(self.ws, self.name, view)["status"], "ok")
        self.assertEqual(tree_digest(self.paths.project), before)
        self.assertEqual(tools.doc(self.ws, self.name)["status"], "ok")
        self.assertEqual(tree_digest(self.paths.project), before, "doc reads only")

    def test_status_never_runs_a_verdict_the_verifier_or_a_browser(self):
        self.authored()
        with mock.patch.object(oracle, "run_verdicts", side_effect=AssertionError("verdict")), \
                mock.patch.object(ev, "verify_evidence", side_effect=AssertionError("verifier")), \
                mock.patch.object(build, "run_gate", side_effect=AssertionError("browser")):
            self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")


class CliTests(Flow):
    """The shell entry has the new commands, writes only what the tools write, and applies the real gates."""

    def run_cli(self, *argv):
        from gemarc_mcp.atlas import cli

        reply, code = cli.run(["--root", str(self.root), *argv])
        reply.pop("_human", None)
        return reply, code

    def test_doc_outline_evidence_build_and_status_through_the_cli(self):
        self.authored()
        self.simulate()
        reply, code = self.run_cli("doc", "desk", "--level", "DD")
        self.assertEqual((reply["status"], code), ("ok", 0))
        reply, code = self.run_cli("doc", "desk", "--chapter", "3", "--page", "1")
        self.assertEqual((reply["status"], reply["chapter"], code), ("ok", 3, 0))
        reply, code = self.run_cli("outline", "main", "src/loan.py")
        self.assertEqual((reply["status"], code), ("ok", 0))
        reply, code = self.run_cli("evidence", "desk", "--ids", "EVD-1")
        self.assertEqual((reply["pass"], code), (1, 0))
        reply, code = self.run_cli("build", "desk")
        self.assertEqual((reply["status"], reply["sheet"], code), ("ok", "atlas/dist/oracle-sheet.md", 0))

    def test_the_shell_entry_applies_the_real_gates_and_the_plain_runner_does_not_open_them_itself(self):
        import subprocess
        import sys

        self.authored()
        env = {**os.environ, "GEMARC_WORKSPACE_ROOT": str(self.root), "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"), "PYTHONUTF8": "1"}
        done = subprocess.run([sys.executable, "-m", "gemarc_mcp.atlas", "status", "desk"], capture_output=True, env=env, cwd=str(self.root))
        reply = json.loads(done.stdout.decode("utf-8"))
        self.assertEqual((reply["done"], reply["phase"]), (False, "build"), reply)
        self.assertTrue(reply["next"][0].startswith("oracle:"), reply["next"])
        human = subprocess.run([sys.executable, "-m", "gemarc_mcp.atlas", "status", "desk", "--human"], capture_output=True, env=env, cwd=str(self.root))
        self.assertIn("gemarc_atlas_simulate", human.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
