"""Fixes of the verifier findings on the tools lane (task fix-tools-b3): status lines that stay whole sentences, the needs-user stops that were
missing (no evidence root, a revised expectation before the sheet exists), the waiver that must not read as a pass, the three-width browser
gate, the review guards, the confinement of ``dist`` against links and junctions, host paths and operator values kept out of replies, the
stamp of a failing evidence row, the size check of ``outline`` before it reads, the escaped oracle sheet, input validation and the brief's
state rules.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_tools_b3_fixes.py"

Synthetic id families only (the lending desk fixture).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp import atlas_tools as at
from gemarc_mcp.atlas import brief as brief_mod
from gemarc_mcp.atlas import build
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.paths import GemArcError

from .test_oracle_support import desk_files
from .test_tools_b3_gates import POLICY, Flow

TBD_ROWS = "#atlas 1\n## tbds: id | title | decision | opts\n"


def make_link(link: Path, target: Path) -> bool:
    """A directory link without admin rights: a junction on Windows, a symlink elsewhere. False when it cannot be made."""
    try:
        if sys.platform.startswith("win"):
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            return done.returncode == 0 and link.exists()
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


class StatusLineTests(Flow):
    """needsUser, blockers and next are whole sentences or tokens; needsUser holds only what the user is asked."""

    def test_a_long_question_is_cut_at_a_word_with_a_mark_and_the_instruction_is_not_in_needs_user(self):
        self.authored()
        title = "Which of the late fee rules applies when a member returns a damaged copy after the grace period, and who pays then? " * 2
        self.put("32-tbd", TBD_ROWS + f"TBD-1 | {title.strip()} | open\n")
        state = self.status()
        self.assertEqual(state["phase"], "needs-user")
        question = state["needsUser"][0]
        self.assertTrue(question.startswith("decide TBD TBD-1: Which of the late fee"), question)
        self.assertTrue(question.endswith(" ..."), "a cut sentence says so")
        self.assertLessEqual(len(question.encode("utf-8")), st.NEEDS_LINE_BYTES)
        self.assertTrue(all("save" not in x and "policies row" not in x for x in state["needsUser"]), state["needsUser"])
        how = [x for x in state["next"] if x.startswith("TBD answered:")]
        self.assertEqual(len(how), 1, state["next"])
        self.assertIn("atlas/user/<name>.txt", how[0])
        self.assertIn("by=user src=<tbd id>", how[0])
        self.assertFalse(how[0].endswith("..."), "the instruction of the AI stays whole")

    def test_every_gate_line_in_the_build_phase_is_whole(self):
        self.authored()
        state = self.status()
        self.assertEqual(state["phase"], "build")
        for line in state["next"]:
            self.assertFalse(line.endswith("..."), line)
        for token in state["blockers"]:
            self.assertRegex(token, r"^(gate-[a-z]+(:[A-Za-z0-9/ -]+)?|review:missing|needs-user:\d+|\+\d+)$", token)
        self.assertTrue(any("gate-evidence:" in b for b in state["blockers"]), state["blockers"])
        self.simulate()
        line = next(x for x in self.status()["next"] if x.startswith("evidence:"))
        self.assertIn("gemarc_atlas_evidence", line)
        self.assertIn("lower verif=", line, "the tail of the instruction survives the cut")

    def test_the_driver_is_not_sent_to_the_user_for_the_requirement_text_it_has_not_saved_yet(self):
        self.init(name="fresh", mode="tobe")
        state = self.status()
        self.assertEqual((state["phase"], state["needsUser"]), ("collect", []))
        self.assertTrue(any(x.startswith("tobe step 1/7") for x in state["next"]), state["next"])
        self.put("00-space", desk_files()["00-space"].replace("#atlas 1", "#atlas 1 mode=tobe"))
        self.assertTrue(any("requirement facts" in x for x in self.status()["needsUser"]), "once something is authored the missing text is a question")

    def test_without_the_product_gates_the_requirement_line_stays_for_the_core(self):
        self.init(name="fresh", mode="tobe")
        with mock.patch.object(st, "_PRODUCT", st.contextvars.ContextVar("x", default=False)):
            state = st.evaluate(self.snap())
        self.assertTrue(any("requirement facts" in x for x in state.needs_user), state.needs_user)


class NoEvidenceRootTests(Flow):
    def test_an_as_is_project_without_any_root_stops_at_needs_user(self):
        self.authored()
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMARC_ATLAS_ROOTS", None)
            state = self.status()
            self.assertEqual((state["done"], state["phase"]), (False, "needs-user"), state)
            self.assertTrue(any("no evidence root for main" in x and "GEMARC_ATLAS_ROOTS main=<dir>" in x and "verif=" in x for x in state["needsUser"]),
                            state["needsUser"])
            self.assertTrue(any("without a root" in x for x in state["next"]), state["next"])
            self.assertIn("gate-evidence:5/5 unverified", state["blockers"])
            reply = tools.evidence(self.ws, self.name)
            self.assertEqual((reply["pass"], reply["skip"]), (0, 3), "evidence itself still reports SKIP, never a pass")
        state = self.status()           # the operator sets the root: the stop is gone, the gate is the driver's work again
        self.assertEqual(state["phase"], "build", state)
        self.assertEqual(state["needsUser"], [])

    def test_a_root_that_exists_for_the_alias_is_never_a_stop(self):
        self.authored()
        self.assertEqual(self.status()["needsUser"], [])

    def test_a_missing_directory_counts_as_no_root(self):
        self.authored()
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": f"main={self.root / 'not-there'}"}):
            self.assertEqual(self.status()["phase"], "needs-user")


class WaiverAndWidthsTests(Flow):
    def waive(self):
        (self.paths.atlas / st.WAIVER_NAME).write_text("this machine has no browser; the user opens the page by hand\n", encoding="utf-8")

    def test_a_waived_browser_is_not_reported_as_green(self):
        self.authored()
        self.gates_green(browser=None)
        self.waive()
        self.record("fixed=; open=")
        state = self.status()
        self.assertTrue(state["done"], state)
        self.assertEqual(state["waived"], ["browser"])
        self.assertIn("gates green: oracle, evidence, docs, build;", state["next"][0])
        self.assertNotIn("browser", state["next"][0].split("gates green:")[1])
        line = next(x for x in state["next"] if "WAIVED" in x)
        self.assertIn("browser gate WAIVED by the user, the page is UNVERIFIED", line)
        self.assertIn("opens the page by hand", line)

    def test_a_verified_page_has_no_waived_field(self):
        self.done_state()
        self.assertNotIn("waived", self.status())

    def test_a_pass_at_one_width_does_not_verify_the_page(self):
        self.authored()
        self.gates_green(browser=None)
        self.stamp("PASS", widths=(1024,))
        gate = self.gate_of("browser")
        self.assertFalse(gate["ok"])
        self.assertIn("1366, 1600", gate["detail"])
        self.assertEqual(self.status()["phase"], "build")
        self.stamp("PASS", widths=(1600, 1366, 1024))
        self.assertTrue(self.gate_of("browser")["ok"])
        self.stamp("PASS", widths=(1024, 1366, 1600, 1920))
        self.assertTrue(self.gate_of("browser")["ok"], "more widths are fine")

    def test_the_required_widths_are_the_default_widths(self):
        self.assertEqual(st.REQUIRED_WIDTHS, build.GATE_WIDTHS)

    def test_a_confirmed_free_done_says_how_many_expectations_wait_for_the_user(self):
        self.done_state()
        state = self.status()
        line = next(x for x in state["next"] if "await the user" in x)
        self.assertIn("2 expectation(s)", line)
        self.assertIn("oracle-sheet.md", line)


class ReviewGuardTests(Flow):
    def at_phase_review(self):
        self.authored()
        self.gates_green()
        self.assertEqual(self.status()["phase"], "review")

    def setUp(self):
        super().setUp()
        tools._REPORTED.clear()
        self.addCleanup(tools._REPORTED.clear)

    def test_record_needs_notes_and_a_report_first(self):
        self.at_phase_review()
        early = tools.review(self.ws, self.name, "record", "fixed=; open=")
        self.assertEqual((early["status"], early["code"]), ("invalid", "GEMARC_ATLAS_REVIEW_001"), early)
        self.assertIn("report", early["message"])
        report = tools.review(self.ws, self.name, "report")
        self.assertEqual(report["status"], "ok")
        for notes in ("", "   ", None):
            blank = tools.review(self.ws, self.name, "record", notes)
            self.assertEqual((blank["status"], blank["code"]), ("invalid", "GEMARC_ATLAS_REVIEW_001"), blank)
            self.assertIn("notes", blank["message"])
        self.assertFalse(self.paths.review.exists(), "a refused record writes nothing")
        done = tools.review(self.ws, self.name, "record", "fixed=; open=RV-O1")
        self.assertEqual((done["status"], done["done"]), ("recorded", True), done)

    def test_a_report_of_an_older_model_does_not_count(self):
        self.at_phase_review()
        tools.review(self.ws, self.name, "report")
        self.assertEqual(self.put("30-policy", POLICY.replace("two seconds", "three seconds"))["status"], "committed")
        self.gates_green()                       # the fix is regated, as the prompt says
        self.assertEqual(self.status()["phase"], "review")
        stale = tools.review(self.ws, self.name, "record", "fixed=; open=")
        self.assertEqual((stale["status"], stale["code"]), ("invalid", "GEMARC_ATLAS_REVIEW_001"), stale)
        self.assertIn("THIS model", stale["message"])
        self.assertFalse(self.paths.review.exists())
        tools.review(self.ws, self.name, "report")
        self.assertEqual(tools.review(self.ws, self.name, "record", "fixed=; open=")["status"], "recorded")

    def test_the_phase_and_once_only_refusals_keep_their_codes(self):
        self.authored()
        self.assertEqual(tools.review(self.ws, self.name, "record")["code"], "GEMARC_ATLAS_REVIEW_003", "not at phase review: the core answers")
        self.gates_green()
        tools.review(self.ws, self.name, "report")
        self.assertEqual(tools.review(self.ws, self.name, "record", "fixed=; open=")["status"], "recorded")
        again = tools.review(self.ws, self.name, "record")
        self.assertEqual(again["code"], "GEMARC_ATLAS_REVIEW_002", "a second record is refused as before, whatever its notes")

    def test_the_shell_entry_needs_notes_but_not_a_process_local_report(self):
        self.at_phase_review()
        refused = tools.review(self.ws, self.name, "record", "", require_report=False)
        self.assertEqual(refused["code"], "GEMARC_ATLAS_REVIEW_001")
        ok = tools.review(self.ws, self.name, "record", "fixed=; open=", require_report=False)
        self.assertEqual(ok["status"], "recorded")

    def test_a_report_after_a_stale_record_does_not_ask_for_another_record(self):
        self.at_phase_review()
        tools.review(self.ws, self.name, "report")
        tools.review(self.ws, self.name, "record", "fixed=; open=")
        stale = {"state": "stale", "newBlock": ["RV-H5"]}
        from gemarc_mcp.atlas import review as review_mod
        real = review_mod.run

        def fake(ws, project, action="report", notes=""):
            out = real(ws, project, action, notes)
            out["record"] = stale
            return out
        with mock.patch.object(review_mod, "run", fake):
            out = tools.review(self.ws, self.name, "report")
        self.assertFalse(any("action=record" in x for x in out["next"]), out["next"])
        self.assertTrue(any("no second review" in x.lower() for x in out["next"]), out["next"])

    def test_the_prompt_tells_the_driver_what_the_guards_demand(self):
        text = at.create_prompt_local(requirement="x", mode="tobe")
        self.assertIn("openBlocks", text)
        self.assertIn("waived", text)
        self.assertIn("notes", text)
        self.assertIn("GEMARC_ATLAS_ROOTS", text)
        self.assertIn("再 report 一次", text)


class ConfinementTests(Flow):
    """A link or junction planted at dist, dist/.verify or dist/docs is refused before the build deletes or writes anything."""

    def setUp(self):
        super().setUp()
        self.authored()
        self.outside = self.root / "outside"
        (self.outside / "docs").mkdir(parents=True)
        self.victim = self.outside / "docs" / "victim.txt"
        self.victim.write_text("keep me", encoding="utf-8")
        self.dist = self.paths.atlas / "dist"

    def assert_untouched(self):
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["docs"])
        self.assertEqual(sorted(p.name for p in (self.outside / "docs").iterdir()), ["victim.txt"])

    def test_dist_as_a_link_is_refused_by_build_verify_and_the_sheet(self):
        if not make_link(self.dist, self.outside):
            self.skipTest("no directory link could be made here")
        for call in (lambda: tools.build_all(self.ws, self.name), lambda: tools.verify(self.ws, self.name), lambda: tools.oracle_sheet(self.ws, self.name)):
            reply = call()
            self.assertEqual(reply["status"], "invalid", reply)
            self.assertEqual(reply["code"], "GEMARC_AUTH_001")
            self.assert_untouched()

    def test_a_link_inside_dist_is_refused(self):
        self.dist.mkdir(parents=True)
        if not make_link(self.dist / "docs", self.outside / "docs"):
            self.skipTest("no directory link could be made here")
        reply = tools.build_all(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_001"), reply)
        self.assert_untouched()

    def test_a_link_at_the_verify_directory_is_refused(self):
        self.assertTrue(self.simulate()["clean"])
        tools.evidence(self.ws, self.name)
        self.build()
        if not make_link(self.dist / ".verify", self.outside):
            self.skipTest("no directory link could be made here")
        reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_001"), reply)
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["docs"])

    def test_is_link_sees_junctions_and_symlinks_and_ignores_plain_directories(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.assertFalse(tools.is_link(plain))
        self.assertFalse(tools.is_link(self.root / "absent"))
        link = self.root / "link"
        if not make_link(link, plain):
            self.skipTest("no directory link could be made here")
        self.assertTrue(tools.is_link(link))

    def test_an_ordinary_project_is_not_refused(self):
        self.assertEqual(tools.build_all(self.ws, self.name)["status"], "ok")


class HostPathTests(Flow):
    def test_the_mcp_replies_of_build_and_verify_name_files_relative_to_the_project(self):
        self.authored()
        self.assertTrue(self.simulate()["clean"])
        with mock.patch.dict(os.environ, {"GEMARC_WORKSPACE_ROOT": str(self.root)}):
            built = at.build(project=self.name)
            self.assertEqual((built["status"], built["dist"]), ("ok", "atlas/dist"), built)
            self.assertNotIn(str(self.root), json.dumps(built))
            self.assertNotIn("\\\\", json.dumps(built).replace("\\\\n", ""), "no Windows path in the reply")
            with mock.patch.dict(os.environ, {"GEMARC_ATLAS_BROWSER": str(self.root / "no-edge.exe")}):
                verified = at.verify(project=self.name)
            self.assertEqual(verified["report"], "atlas/dist/.verify/gate-report.json")
            self.assertNotIn(str(self.root), json.dumps(verified))

    def test_the_helper_leaves_other_replies_alone(self):
        self.assertEqual(at._no_host_paths({"status": "ok", "path": "C:\\x\\atlas"}), {"status": "ok", "path": "C:\\x\\atlas"})
        self.assertEqual(at._no_host_paths({"dist": "C:\\Users\\someone\\ws\\p\\atlas\\dist"})["dist"], "atlas/dist")
        self.assertEqual(at._no_host_paths({"report": "/home/u/ws/p/atlas/dist/.verify/gate-report.json"})["report"], "atlas/dist/.verify/gate-report.json")
        self.assertEqual(at._no_host_paths({"dist": "elsewhere/dist"})["dist"], "dist")


class PinnedBrowserTests(Flow):
    def test_the_operators_pinned_path_is_not_echoed_nor_written_to_the_report(self):
        self.authored()
        self.assertTrue(self.simulate()["clean"])
        self.build()
        secret = str(self.root / "secret-operator" / "private" / "edge.exe")
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_BROWSER": secret}):
            reply = tools.verify(self.ws, self.name)
            built = tools.build_all(self.ws, self.name, gate="smoke")
        self.assertEqual((reply["status"], reply["gate"]), ("invalid", "SKIP"), reply)
        self.assertNotIn("secret-operator", json.dumps(reply))
        self.assertIn("pinned browser", reply["message"])
        self.assertNotIn("secret-operator", json.dumps(built))
        report = (self.paths.atlas / "dist" / ".verify" / "gate-report.json").read_text(encoding="utf-8")
        self.assertNotIn("secret-operator", report)
        self.assertEqual(json.loads(report)["status"], "SKIP")
        stamp = (self.paths.atlas / "dist" / ".verify" / "gate-stamp.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(stamp)["status"], "SKIP")

    @unittest.skipUnless(sys.platform.startswith("win"), "the default install directories are a Windows matter")
    def test_the_program_files_directories_are_filled_in_when_the_sdk_environment_lacks_them(self):
        keys = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
        with mock.patch.dict(os.environ):
            for key in keys:
                os.environ.pop(key, None)
            tools.ensure_browser_env()
            for key in keys:
                folder = os.environ.get(key)
                if folder is None:
                    self.assertFalse(os.path.isdir((os.environ.get("SystemDrive") or "C:") + "\\" + ("Program Files (x86)" if "x86" in key else "Program Files")))
                else:
                    self.assertTrue(os.path.isdir(folder), folder)
        with mock.patch.dict(os.environ, {"ProgramFiles": "Z:\\operator"}):
            tools.ensure_browser_env()
            self.assertEqual(os.environ["ProgramFiles"], "Z:\\operator", "a value the operator set is never replaced")


class EvidenceDriftTests(Flow):
    def test_a_row_that_fails_later_loses_its_stamp(self):
        self.authored()
        first = tools.evidence(self.ws, self.name)
        self.assertEqual(first["claims"], {"implemented": 5, "unverified": 0}, first)
        self.assertTrue(first["stamped"] >= 3)
        (self.src / "src" / "loan.py").write_text("class Loans:\n    def make_loan(self, member):\n        return 1\n", encoding="utf-8")
        second = tools.evidence(self.ws, self.name)
        self.assertEqual(second["fail"], 1, second)
        self.assertGreater(second["claims"]["unverified"], 0, "a failing row no longer verifies its claims")
        self.assertEqual(second["stamped"], first["stamped"] - 1)
        state = self.status()
        self.assertFalse(self.gate_of("evidence")["ok"])
        self.assertTrue(any(b.startswith("gate-evidence") for b in state["blockers"]), state["blockers"])


class OutlineSizeTests(Flow):
    def test_a_file_over_the_limit_is_refused_before_it_is_read(self):
        from gemarc_mcp.atlas import evidence as ev
        big = self.src / "src" / "huge.py"
        with open(big, "wb") as stream:
            stream.truncate(ev.MAX_FILE_BYTES + 1024 * 1024)       # sparse: no disk is used
        tracemalloc.start()
        try:
            reply = tools.outline("main", "src/huge.py")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertIn(reply["code"], (ev._CODES["NOT_TEXT"][0],))
        self.assertLess(peak, 2 * 1024 * 1024, f"the file was read: peak {peak} bytes")

    def test_a_normal_file_is_still_outlined(self):
        reply = tools.outline("main", "src/loan.py")
        self.assertEqual(reply["status"], "ok", reply)
        self.assertIn("create_loan", reply["text"])

    def test_an_unknown_alias_and_a_missing_file_keep_their_answers(self):
        self.assertEqual(tools.outline("nope", "src/loan.py")["status"], "invalid")
        self.assertEqual(tools.outline("main", "src/absent.py")["status"], "invalid")


class OracleSheetTests(Flow):
    def test_raw_html_in_model_text_is_escaped_in_the_sheet(self):
        self.init()
        files = desk_files()
        original = [line for line in files["20-scenarios"].splitlines() if line.startswith("CS-BORROW-OK |") and "usr.member" not in line][0]
        title = original.split(" | ")
        title[2] = "Borrow</script><img onerror>"
        files["20-scenarios"] = files["20-scenarios"].replace(original, " | ".join(title))
        files["30-policy"] = POLICY
        self.load(files)
        reply = tools.oracle_sheet(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        sheet = (self.paths.atlas / "dist" / "oracle-sheet.md").read_text(encoding="utf-8")
        self.assertNotIn("<img", sheet)
        self.assertNotIn("</script>", sheet)
        self.assertIn("&lt;img onerror&gt;", sheet)
        self.assertRegex(sheet, r"(?m)^APPROVE-ALL [0-9a-f]{12}$")
        self.assertIn("CS-BORROW-OK", sheet)


class InputValidationTests(Flow):
    def test_unknown_languages_and_negative_offsets_are_refused(self):
        self.authored()
        for lang in ("fr", "zh", "EN-us", "xx-XX"):
            reply = tools.brief("", lang)
            self.assertEqual((reply["status"], reply["code"]), ("invalid", tools.E_ARG), (lang, reply))
        for lang in ("", "en-US", "zh-CN", "ja-JP"):
            self.assertEqual(tools.brief("", lang)["status"], "ok", lang)
        self.assertEqual(tools.brief("", "ja-JP")["lang"], "en-US")
        self.assertEqual(tools.index(self.ws, self.name, offset=-5)["code"], tools.E_ARG)
        self.assertEqual(tools.check(self.ws, self.name, mode="full", offset=-1)["code"], tools.E_ARG)
        self.assertEqual(tools.check(None, "", "x", offset=-1)["code"], tools.E_ARG)
        self.assertEqual(tools.doc(self.ws, self.name, chapter="2", page=-1)["code"], tools.E_ARG)
        self.assertEqual(tools.outline("main", "src/loan.py", -1)["code"], tools.E_ARG)

    def test_zero_and_positive_values_still_work(self):
        self.authored()
        self.assertEqual(tools.index(self.ws, self.name, offset=0)["status"], "ok")
        self.assertEqual(tools.check(self.ws, self.name, mode="full", offset=0)["status"], "ok")
        self.assertEqual(tools.doc(self.ws, self.name, chapter="2", page=0)["page"], 1)

    def test_the_init_refusal_is_in_english(self):
        self.authored()
        reply = tools.init(self.ws, self.name, "again")
        self.assertEqual(reply["code"], pj.E_EXISTS)
        self.assertTrue(reply["message"].isascii(), reply["message"])


class BriefStateRulesTests(unittest.TestCase):
    def test_the_oracle_section_says_how_final_states_follow_from_events(self):
        for lang in ("en-US", "zh-CN"):
            text = brief_mod.render("oracle", lang)
            for word in ("Idle", "Running", "Completed", "Failed", "Recovered", "Return", "Failure", "Call"):
                self.assertIn(word, text, f"{lang}: {word}")
            self.assertLessEqual(len(text.encode("utf-8")), brief_mod.SECTION_CAP)
            all_text = brief_mod.render("all", lang)
            self.assertIn("Running", all_text)
            self.assertLessEqual(len(all_text.encode("utf-8")), brief_mod.ALL_CAP)

    def test_the_work_order_names_the_stops_and_the_review_notes(self):
        text = brief_mod.render("all", "en-US")
        for needle in ("GEMARC_ATLAS_ROOTS", "openBlocks", "waived", "notes="):
            self.assertIn(needle, text)
        self.assertIn("GEMARC_ATLAS_ROOTS", brief_mod.render("all", "zh-CN"))


if __name__ == "__main__":
    unittest.main()
