"""Milestone M8, the deterministic part: SCRIPTED pipelines through the REAL tools (no model in the loop), end to end.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_pipeline_m8*.py"

* ``FixturePipelineTests``: the three synthetic fixtures (tiny, small, reference): init, every slice by ``put``, the answers and saved texts
  that only people can supply, ``simulate``, ``evidence`` against a synthetic evidence root the test generates, ``build``, ``doc``, the
  browser gate (``verify``), the one review (``report`` and ``record``), ``status`` says ``done``;
* ``GreenfieldPipelineTests``: a synthetic requirement brief in the elicitation order of design 8.3 (to-be): the text is saved under
  ``atlas/user/`` first, requirements, tests, TBDs, the stop at ``needs-user``, the user's answers as ``by=user`` policies that quote
  them, no invented requirement; plus the negative control (an invented requirement source is a BLOCK at review);
* ``AsIsPipelineTests``: a generated repository of 25 modules, anchors taken from the ``outline`` tool, every Implemented claim stamped
  (EV-003), the same flow over REAL stdio in ``StdioPipelineTests``;
* ``BudgetTests``: sizes (``dist/index.html`` at most 2 MiB at reference scale), byte-identical rebuilds, time budgets (design 5.5, with the
  1.5x stop/go margin), reply caps (design 4.1), and that nothing is written outside the project directory (the workspace, the evidence
  root, the repository tree).

The real browser gate runs in every pipeline (Edge or Chrome); without one the driver waives it the way the user would and the test says
so. Synthetic data only. Engine-consistent expectations of the fixtures are test FIXTURES (see ``pipeline.py``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import status as status_mod

from . import pipeline as P
from . import pipeline_flows as F
from .test_atlas_mcp_e2e import REPLY_CAPS, _sdk_available
from .test_build_project import dist_tree

REPO = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024
#: Design 5.5 limits times the 1.5x of the stop/go rule of M5 (a measured value above them is a finding, not noise).
BUDGET = {"check-slice": 0.3 * 1.5, "check-full": 1.0 * 1.5, "simulate": 2.0 * 1.5, "build": 10.0 * 1.5}
PUT_SECONDS = 4.0

_TMP: "tempfile.TemporaryDirectory | None" = None
_CACHE: dict = {}


def setUpModule() -> None:
    global _TMP
    _TMP = tempfile.TemporaryDirectory(prefix="atlas-m8-")


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()
    _CACHE.clear()


def work_dir(name: str) -> "tuple[Path, Path]":
    """A fresh ``(workspace, scratch)`` pair for one run, both inside the temporary directory of this module."""
    base = Path(_TMP.name) / name
    (base / "ws").mkdir(parents=True)
    (base / "scratch").mkdir()
    return base / "ws", base / "scratch"


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0" + path.read_bytes() + b"\1")
    return digest.hexdigest()


def repo_fingerprint() -> "str | None":
    """Hash of every file git lists as modified or untracked in this repository (None when git is not available): a scripted run must not
    change a byte of the tree (the atlas reads the repository only through evidence roots, and writes only into its workspace)."""
    try:
        listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-m", "-o", "--exclude-standard", "-z"], capture_output=True, timeout=60, check=True,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.decode("utf-8", "replace").split("\0")
    except (OSError, subprocess.SubprocessError):
        return None
    digest = hashlib.sha256()
    for rel in sorted(x for x in listed if x):
        target = REPO / rel
        digest.update(rel.encode("utf-8") + b"\0")
        if target.is_file():
            digest.update(target.read_bytes())
    return digest.hexdigest()


def fixture_run(preset: str, seed: int) -> dict:
    """One scripted fixture run, made once per process (the real browser gate is in it) with the confinement witnesses."""
    key = ("fixture", preset, seed)
    if key not in _CACHE:
        ws, scratch = work_dir(f"{preset}-{seed}")
        repo_before = repo_fingerprint()
        started = time.perf_counter()
        run = P.run_fixture(preset, seed, ws, scratch)
        run.update(seconds=time.perf_counter() - started, ws=ws, scratch=scratch, repo_before=repo_before, repo_after=repo_fingerprint(),
                   roots_hash=tree_hash(scratch / "roots"))
        _CACHE[key] = run
    return _CACHE[key]


def greenfield_run() -> dict:
    if "greenfield" not in _CACHE:
        ws, scratch = work_dir("greenfield")
        repo_before = repo_fingerprint()
        with P.Session(ws, {}) as s:
            out = F.run_greenfield(s)
        _CACHE["greenfield"] = {"session": s, "out": out, "ws": ws, "scratch": scratch, "repo_before": repo_before, "repo_after": repo_fingerprint()}
    return _CACHE["greenfield"]


def asis_run() -> dict:
    if "asis" not in _CACHE:
        ws, scratch = work_dir("asis")
        repo = F.make_repository(scratch / "repo")
        before = tree_hash(scratch / "repo")
        repo_before = repo_fingerprint()
        with P.Session(ws, {"main": scratch / "repo"}) as s:
            out = F.run_asis(s, repo)
        _CACHE["asis"] = {"session": s, "out": out, "ws": ws, "scratch": scratch, "repo": repo, "repo_hash_before": before,
                          "repo_hash_after": tree_hash(scratch / "repo"), "repo_before": repo_before, "repo_after": repo_fingerprint()}
    return _CACHE["asis"]


def waived(run_steps: dict) -> bool:
    return run_steps.get("browser") == "WAIVED"


class FixturePipelineTests(unittest.TestCase):
    def test_all_three_fixtures_reach_done_with_one_review(self):
        for preset, seed in P.FIXTURES:
            with self.subTest(preset=preset):
                run = fixture_run(preset, seed)
                final, steps = run["final"], run["steps"]
                self.assertEqual((final["done"], final["phase"], final["blockers"], final["needsUser"]), (True, "done", [], []), final)
                self.assertEqual(steps["record"]["status"], "recorded", steps["record"])
                self.assertEqual((steps["record"]["done"], steps["record"]["counts"]["BLOCK"]), (True, 0))
                self.assertEqual(steps["simulate"]["status"], "ok")
                summary = steps["simulate"]["summary"]
                self.assertEqual((summary["baselines"]["fail"], summary["pairs"]["fail"], summary["baselines"]["undecided"]), (0, 0, 0), steps["simulate"])
                # an intervention of the generator that states neither a class nor a terminal state asserts nothing: there is nothing to seal, and that is all "refused" means here
                for line in steps["simulate"].get("refused", []):
                    if not re.fullmatch(r"\+\d+ more", line):          # the list is cut after four lines
                        self.assertIn("the intervention has no expectation to seal", line)
                self.assertEqual((steps["evidence"]["fail"], steps["evidence"]["skip"], steps["evidence"]["claims"]["unverified"]), (0, 0, 0), steps["evidence"])
                self.assertEqual(steps["build"]["status"], "ok", steps["build"])
                self.assertEqual(steps["build"]["checks"], [])
                self.assertEqual(steps["doc"]["status"], "ok")
                self.assertEqual(steps["doc"]["markers"], 0)
                if waived(steps):
                    print(f"\n[{preset}] NO BROWSER on this machine: the gate was waived by the user's file, the page was NOT verified")
                    self.assertEqual(final["waived"], ["browser"])
                else:
                    self.assertEqual(steps["verify"]["gate"], "PASS", steps["verify"])
                    self.assertEqual([w["width"] for w in steps["verify"]["widths"]], [1024, 1366, 1600])
                    self.assertIn("gates green: oracle, evidence, docs, build, browser", final["next"][0])

    def test_the_things_only_people_can_do_were_scripted_as_people_do_them(self):
        small = fixture_run("small", 3)
        self.assertEqual(small["steps"]["after-puts"]["phase"], "needs-user", "the open TBDs are the user's to decide before anything else")
        self.assertTrue(any(line.startswith("ask the user: decide TBD") for line in small["steps"]["after-puts"]["next"]), small["steps"]["after-puts"]["next"])
        user_dir = small["ws"] / P.PROJECT / "atlas" / "user"
        self.assertTrue((user_dir / "answers.txt").is_file())
        self.assertGreaterEqual(len(list(user_dir.glob("*.txt"))), 2, "one saved text per user: source and one for the answers")
        self.assertFalse((small["ws"] / P.PROJECT / "atlas" / "oracle-confirm.txt").exists(), "the user's confirmation file is never written by the script")

    def test_the_oracle_sheet_is_handed_over_not_confirmed(self):
        for preset, seed in P.FIXTURES:
            run = fixture_run(preset, seed)
            sheet = run["ws"] / P.PROJECT / "atlas" / "dist" / "oracle-sheet.md"
            self.assertTrue(sheet.is_file(), preset)
            self.assertIn("APPROVE-ALL", sheet.read_text(encoding="utf-8"))
            self.assertTrue(any("await the user" in line for line in run["final"]["next"]), run["final"]["next"])

    def test_documents_diagrams_and_prompts_are_written_for_every_fixture(self):
        for preset, seed in P.FIXTURES:
            dist = fixture_run(preset, seed)["ws"] / P.PROJECT / "atlas" / "dist"
            for rel in ("index.html", "build-info.json", "docs/en-US/REQ.md", "docs/en-US/BD.md", "docs/en-US/DD.md"):
                self.assertTrue((dist / rel).is_file(), f"{preset}: {rel}")
            self.assertTrue(list((dist / "diagrams").glob("*.mmd")), preset)
            self.assertTrue(list((dist / "prompts").glob("*")), preset)
            for md in (dist / "docs" / "en-US").glob("*.md"):
                text = md.read_text(encoding="utf-8")
                self.assertNotIn("[NOT-AUTHORED", text, f"{preset}: {md.name}")
                for junk in ("undefined", "NaN", "[object"):
                    self.assertNotIn(junk, text, f"{preset}: {md.name}")


class GreenfieldPipelineTests(unittest.TestCase):
    def test_the_elicitation_order_ends_at_done(self):
        out = greenfield_run()["out"]
        self.assertEqual(out["status-empty"]["phase"], "collect")
        self.assertTrue(out["status-empty"]["next"][0].startswith("tobe step 1/7"), out["status-empty"]["next"])
        self.assertTrue(out["status-saved"]["next"][0].startswith("tobe step 2/7"), "after the text is saved, the requirements come next")
        self.assertEqual(out["status-needs-user"]["phase"], "needs-user")
        self.assertEqual(len([x for x in out["status-needs-user"]["next"] if x.startswith("ask the user: decide TBD")]), 2, "two lines and a '+1 more'")
        self.assertTrue(any("+1 more" in x for x in out["status-needs-user"]["needsUser"]), out["status-needs-user"]["needsUser"])
        final = out["final"]
        self.assertEqual((final["done"], final["phase"], final["blockers"], final["needsUser"]), (True, "done", [], []), final)
        self.assertEqual(out["record"]["counts"]["BLOCK"], 0)

    def test_the_requirement_text_is_saved_before_any_fact(self):
        records = greenfield_run()["session"].records
        run = greenfield_run()
        saved = run["ws"] / P.PROJECT / "atlas" / "user" / "brief.txt"
        self.assertEqual(saved.read_bytes().decode("utf-8"), F.BRIEF)
        # status said "step 1/7" before the text existed and "step 2/7" after it: the script saved it between those two calls, and the first put came later
        names = [r["tool"] for r in records]
        self.assertLess(names.index("gemarc_atlas_status"), names.index("gemarc_atlas_put"))
        self.assertEqual(names[:3], ["gemarc_atlas_brief", "gemarc_atlas_init", "gemarc_atlas_status"])

    def test_no_requirement_is_invented(self):
        run = greenfield_run()
        paths = pj.project_paths(pj.get_workspace(run["ws"], create=False), P.PROJECT)
        snap = status_mod.load_snapshot(paths, pj.load_config(paths))
        model = snap.model
        sources = [r["source"] for r in model["requirements"]]
        self.assertEqual(sources, list(F.REQUIREMENTS), "exactly the numbered sentences of the brief, in order")
        saved = " ".join(F.BRIEF.split())
        for sentence in sources:
            self.assertIn(" ".join(sentence.split()), saved)
        quotes = [e["anchor"] for e in model["evidences"] if str(e["filePath"]).startswith("user:")]
        self.assertEqual(len(quotes), 6)
        for quote in quotes:
            self.assertIn(" ".join(quote.split()), saved)
        out = run["out"]
        self.assertEqual((out["evidence"]["pass"], out["evidence"]["warn"], out["evidence"]["fail"], out["evidence"]["skip"]), (6, 0, 0, 0), "EV-012: every quote is in the saved text")
        self.assertEqual(out["report"]["stats"]["userQuotes"], 6)
        self.assertEqual([f for f in out["report"]["findings"] if f["sev"] == "BLOCK"], [])
        self.assertEqual(snap.mode, "tobe")

    def test_the_users_answers_are_policies_by_the_user_that_quote_the_saved_text(self):
        run = greenfield_run()
        paths = pj.project_paths(pj.get_workspace(run["ws"], create=False), P.PROJECT)
        snap = status_mod.load_snapshot(paths, pj.load_config(paths))
        decided = status_mod.decided_tbds(snap.model, status_mod.saved_text(paths))
        self.assertEqual(decided, {"TBD-1", "TBD-2", "TBD-3"})
        self.assertEqual([p["by"] for p in snap.model["policies"] if p["id"].startswith("PL-DEC")], ["user"] * 3)
        self.assertEqual([p["by"] for p in snap.model["policies"] if p["id"] == "PL-NFR-1"], ["ai"], "the non-functional policy was written by the AI: the review asks the user to confirm it")

    def test_a_decision_without_the_users_words_does_not_resolve_a_tbd(self):
        """Negative control: the answer policy exists but the user said nothing saved (a different project without the answers file)."""
        ws, scratch = work_dir("greenfield-no-answers")
        with P.Session(ws, {}) as s:
            out = F.run_greenfield(s, answers=False, finish=False)
            self.assertEqual(out["status-structure"]["phase"], "needs-user")
            invented = F.decisions_slice()
            reply = s.put("60-decisions", invented)           # the quotes are not in anything the user saved
            self.assertEqual(reply["status"], "committed")
            state = s.status("author")
        self.assertEqual(state["phase"], "needs-user", "a by=user policy that quotes nothing the user said does not decide the TBD")
        self.assertEqual(len([x for x in state["needsUser"] if "decide TBD" in x]), 2)

    def test_an_invented_requirement_is_a_block_at_review(self):
        """Negative control: copy the finished project, replace one requirement's source with a sentence the brief does not contain."""
        run = greenfield_run()
        ws, _scratch = work_dir("greenfield-invented")
        shutil.rmtree(ws)
        shutil.copytree(run["ws"], ws)
        invented = "#atlas 1 mode=tobe\n## requirements: id | title | source | opts\nRQ-3 | Release unplanted plots | Members may park a car on any free plot at night. | nodes=svc.booking,svc.waitlist,tbl.plot tests=TS-3\n"
        with P.Session(ws, {}) as s:
            self.assertEqual(s.put("10-requirements", invented, mode="patch")["status"], "committed")
            report = s.call("review", "review", action="report")
            state = s.status("final")
        blocks = [f for f in report["findings"] if f["sev"] == "BLOCK"]
        self.assertEqual([f["id"] for f in blocks], ["RV-H5"], report["findings"])
        self.assertEqual(blocks[0]["ids"], ["RQ-3"])
        self.assertFalse(state["done"], "an invented requirement keeps the project from done (the review record is stale)")


class AsIsPipelineTests(unittest.TestCase):
    def test_a_synthetic_repository_ends_at_done_with_every_claim_stamped(self):
        run = asis_run()
        out = run["out"]
        self.assertEqual(len(run["repo"]["modules"]), 25)
        final = out["final"]
        self.assertEqual((final["done"], final["phase"], final["blockers"], final["needsUser"]), (True, "done", [], []), final)
        evidence = out["evidence"]
        self.assertEqual((evidence["rows"], evidence["pass"], evidence["warn"], evidence["fail"], evidence["skip"]), (86, 86, 0, 0, 0), evidence)
        self.assertEqual(evidence["claims"], {"implemented": 60, "unverified": 0}, "every Implemented claim is EV-003 stamped")

    def test_every_implemented_claim_has_a_stamp_for_its_own_file_and_anchor_that_resolves_to_one_line(self):
        run = asis_run()
        paths = pj.project_paths(pj.get_workspace(run["ws"], create=False), P.PROJECT)
        snap = status_mod.load_snapshot(paths, pj.load_config(paths))
        from gemarc_mcp.atlas import evidence as ev

        entries = ev.load_stamp(paths.atlas)["entries"]
        total, bad = status_mod.claims_unverified(snap.model, entries)
        self.assertEqual((total, bad), (60, []))
        code_rows = [e for e in snap.model["evidences"] if e["sourceKind"] == "source-code"]
        self.assertEqual(len(code_rows), 85)
        for row in code_rows:
            entry = entries[row["evidenceId"]]
            self.assertEqual((entry["occurrences"], entry["filePath"]), (1, row["filePath"]), "unique anchors by construction")
            self.assertEqual(entry["lineRange"][0], entry["lineRange"][1])
        implemented = [n for n in snap.model["nodes"] if n["verification"] == "Implemented"] + [e for e in snap.model["edges"] if e["verification"] == "Implemented"]
        self.assertEqual(len(implemented), 60)

    def test_the_anchors_of_the_nodes_came_from_the_outline_tool(self):
        records = asis_run()["session"].records
        outline_calls = [r for r in records if r["tool"] == "gemarc_atlas_outline"]
        self.assertGreaterEqual(len(outline_calls), 25, "one outline per module at least")
        self.assertEqual({r["phase"] for r in outline_calls}, {"references"})
        for r in outline_calls:
            self.assertLessEqual(r["reply"], REPLY_CAPS["gemarc_atlas_outline"])

    def test_the_repository_was_only_read(self):
        run = asis_run()
        self.assertEqual(run["repo_hash_before"], run["repo_hash_after"], "no byte of the analysed repository changed")

    def test_the_page_lists_the_modules_and_the_sealed_runs(self):
        run = asis_run()
        from gemarc_mcp.atlas import htmlassemble as H

        page = (run["ws"] / P.PROJECT / "atlas" / "dist" / "index.html").read_text(encoding="utf-8")
        data = H.extract_data(page)
        self.assertEqual(len(data["model"]["nodes"]), 25)
        self.assertEqual(sorted(data["runs"]["baselines"]), ["CS-FLOW-1", "CS-FLOW-2", "CS-FLOW-3"])


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioPipelineTests(unittest.TestCase):
    """The as-is flow once more, every call a ``tools/call`` over a real ``python -m gemarc_mcp`` child process."""

    def test_the_asis_flow_over_real_stdio_ends_at_done(self):
        ws, scratch = work_dir("asis-stdio")
        repo = F.make_repository(scratch / "repo")
        result, stderr = P.run_over_stdio(ws, {"main": scratch / "repo"}, lambda s: (F.run_asis(s, repo), s))
        out, session = result
        self.assertEqual(session.protocol_errors, [], "every reply: one compact JSON text, no structuredContent, isError equals status invalid or error")
        self.assertFalse([line for line in stderr if "Traceback" in line], stderr[:5])
        final = out["final"]
        self.assertEqual((final["done"], final["phase"], final["blockers"], final["needsUser"]), (True, "done", [], []), final)
        self.assertEqual(out["evidence"]["claims"], {"implemented": 60, "unverified": 0})
        for r in session.records:
            self.assertLessEqual(r["reply"], REPLY_CAPS[r["tool"]], f"{r['tool']}: {r['reply']} B over the wire")
            self.assertLessEqual(r["reply"], 20 * 1024)
        status_replies = [r["reply"] for r in session.records if r["tool"] == "gemarc_atlas_status"]
        self.assertLessEqual(max(status_replies), 1200, "status view=next is at most 1.2 KB")
        # the same project built in-process gives the same page bytes: transport does not change what the build writes
        local = asis_run()
        self.assertEqual(out["build"]["page"]["sha256"], local["out"]["build"]["page"]["sha256"], "the page of the same project is byte-identical in-process and over stdio")


class BudgetTests(unittest.TestCase):
    def test_reference_scale_page_is_within_two_mebibytes_and_a_rebuild_is_byte_identical(self):
        run = fixture_run("reference", 3)
        dist = run["ws"] / P.PROJECT / "atlas" / "dist"
        page = dist / "index.html"
        size = page.stat().st_size
        print(f"\n[reference scale] dist/index.html {size} bytes ({size / MIB:.2f} MiB); parts {run['steps']['build']['parts']}")
        self.assertLessEqual(size, 2 * MIB, "design M4 (a) and M8: at most 2 MiB at reference scale")
        self.assertLessEqual(size, 2_000_000, "and within the 2,000,000 byte budget the page tests pin")
        first = dist_tree(dist)
        with P.Session(run["ws"], run["roots"]) as s:
            again = s.call("build", "gates")
            self.assertEqual(again["status"], "ok", again)
            second = dist_tree(dist)
            third = s.call("build", "gates")
            self.assertEqual(third["status"], "ok")
        self.assertEqual(second, first, "two builds of one project are byte-identical (page, build-info, documents, diagrams, prompts)")
        self.assertEqual(dist_tree(dist), first)
        self.assertEqual(again["page"]["sha256"], third["page"]["sha256"])

    def test_every_fixture_page_is_deterministic_and_small(self):
        for preset, seed in P.FIXTURES:
            run = fixture_run(preset, seed)
            dist = run["ws"] / P.PROJECT / "atlas" / "dist"
            before = dist_tree(dist)
            with P.Session(run["ws"], run["roots"]) as s:
                self.assertEqual(s.call("build", "gates")["status"], "ok")
            self.assertEqual(dist_tree(dist), before, preset)
            self.assertLessEqual((dist / "index.html").stat().st_size, 2 * MIB, preset)

    def test_time_budgets_at_reference_scale(self):
        run = fixture_run("reference", 3)
        records = run["session"].records
        puts = [r["seconds"] for r in records if r["tool"] == "gemarc_atlas_put"]
        by = {name: [r["seconds"] for r in records if r["tool"] == f"gemarc_atlas_{name}"] for name in ("simulate", "build", "evidence", "status", "doc", "verify", "review")}
        print(f"\n[reference timing] puts {len(puts)} max {max(puts):.2f}s; simulate {by['simulate']}; build {by['build']}; evidence {by['evidence']}; "
              f"status max {max(by['status']):.2f}s; verify {by['verify']}; whole run {run['seconds']:.1f}s")
        self.assertLessEqual(max(puts), PUT_SECONDS)
        self.assertLessEqual(max(by["simulate"]), BUDGET["simulate"])
        self.assertLessEqual(max(by["build"]), BUDGET["build"])
        self.assertLessEqual(max(by["status"]), 3.0, "the status hot path")
        with P.Session(run["ws"], run["roots"]) as s:
            slice_name = sorted(p.stem for p in (run["ws"] / P.PROJECT / "atlas" / "slices").glob("*.atl"))[3]
            s.call("check", "gates", mode="slice", slice=slice_name)
            full = s.call("check", "gates", mode="full")
        timings = {r["tool"] + ":" + str(i): r["seconds"] for i, r in enumerate(s.records)}
        slice_seconds, full_seconds = [r["seconds"] for r in s.records]
        print(f"[reference timing] check slice {slice_seconds:.3f}s, check full {full_seconds:.3f}s")
        self.assertLessEqual(slice_seconds, BUDGET["check-slice"], timings)
        self.assertLessEqual(full_seconds, BUDGET["check-full"], timings)
        self.assertNotEqual(full["status"], "error")

    def test_reply_caps_of_design_4_1_hold_on_every_recorded_call(self):
        for preset, seed in P.FIXTURES:
            for r in fixture_run(preset, seed)["session"].records:
                self.assertLessEqual(r["reply"], REPLY_CAPS[r["tool"]], f"{preset}: {r['tool']} {r['reply']} B")
                self.assertLessEqual(r["reply"], 20 * 1024)
            states = [r["reply"] for r in fixture_run(preset, seed)["session"].records if r["tool"] == "gemarc_atlas_status"]
            self.assertLessEqual(max(states), 1200, preset)
        for name, run in (("greenfield", greenfield_run()), ("asis", asis_run())):
            for r in run["session"].records:
                self.assertLessEqual(r["reply"], REPLY_CAPS[r["tool"]], f"{name}: {r['tool']} {r['reply']} B")

    def test_nothing_is_written_outside_the_project_directory(self):
        runs = [("fixture-" + preset, fixture_run(preset, seed)) for preset, seed in P.FIXTURES] + [("greenfield", greenfield_run()), ("asis", asis_run())]
        for name, run in runs:
            self.assertEqual(sorted(p.name for p in run["ws"].iterdir()), [P.PROJECT], f"{name}: the workspace holds the project directory and nothing else")
            self.assertEqual(run["repo_before"], run["repo_after"], f"{name}: no file of this repository changed")
        for preset, seed in P.FIXTURES:
            run = fixture_run(preset, seed)
            self.assertEqual(tree_hash(run["scratch"] / "roots"), run["roots_hash"], f"{preset}: the evidence root was only read")
            self.assertEqual(sorted(p.name for p in run["scratch"].iterdir()), ["roots"], "the scratch directory holds only the evidence root the test generated")
        asis = asis_run()
        self.assertEqual(sorted(p.name for p in asis["scratch"].iterdir()), ["repo"])
        self.assertEqual(sorted(p.name for p in (asis["ws"] / P.PROJECT).iterdir()), ["atlas"], "the project directory holds atlas/ only")
        leftovers = list((asis["ws"] / P.PROJECT / "atlas" / ".atlas").glob("release-stage*"))
        self.assertEqual(leftovers, [])
        self.assertEqual(list((asis["ws"] / P.PROJECT / "atlas" / "dist" / ".verify").glob("work*")), [], "the browser's scratch directory is removed")

    def test_the_measured_pipeline_sizes_are_recorded_per_tool(self):
        """The raw material of docs/atlas/TOKEN_LEDGER.md: bytes written (requests) and read (replies) per tool, by the scripted runs."""
        sums = {name: run["session"].tool_sums() for name, run in (("asis", asis_run()), ("greenfield", greenfield_run()))}
        for name, table in sums.items():
            self.assertIn("gemarc_atlas_put", table)
            self.assertGreater(table["gemarc_atlas_put"]["request"], 5000, name)
            print(f"\n[{name}] " + ", ".join(f"{k.replace('gemarc_atlas_', '')} {v['calls']}x req {v['request']} reply {v['reply']}" for k, v in sorted(table.items())))


if __name__ == "__main__":
    unittest.main()
