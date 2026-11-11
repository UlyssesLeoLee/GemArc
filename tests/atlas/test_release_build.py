"""``build release=true`` (design 7.6, milestone M8): what a release needs, what refuses it, and that a refusal never touches ``dist/``.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_release_build*.py"

A release is assembled in ``atlas/.atlas/release-stage`` and judged there (oracle bite run, oracle release gate, evidence re-verified against
the sources, documents, the BD/RC/LB build checks, the browser gate); only a release that passed is swapped into ``dist/``. Every refusal
below is checked three ways: the reply (code ``GEMARC_ATLAS_BUILD_020`` and the blocker kind), the tree of ``dist/`` (byte for byte what it was,
``oracle-sheet.md`` included, which the refusal rewrites with the same content) and a POSITIVE CONTROL (the cause removed, the same project releases).

Most tests stand in a passing browser verdict (``smoke.verify_gate`` is patched; the gate itself has real-browser tests in ``test_gate_html.py`` and
``test_gate_tool_path.py``); ``RealBrowserRelease`` runs the real gate once, and skips loudly where there is no browser. Synthetic data only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, oracle
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import status as status_mod
from gemarc_mcp.atlas.gate import smoke

from . import pipeline as P

GOOD_VERDICT = {"status": "PASS", "browser": None, "message": "", "lines": [f"PASS {w}: 6 tabs, 11/11 steps, 0.1 s" for w in (1024, 1366, 1600)],
                "widths": [{"width": w, "status": "PASS", "codes": [], "findings": [], "tabs": list(build.GATE_TABS), "steps": {"ok": len(build.GATE_STEP_NAMES), "total": len(build.GATE_STEP_NAMES)},
                            "stepNames": list(build.GATE_STEP_NAMES), "message": "", "seconds": 0.1} for w in (1024, 1366, 1600)]}
RED_VERDICT = {"status": "FAIL", "browser": None, "message": "", "lines": ["FAIL 1024: GEMARC_GATE_013 HEARTBEAT"],
               "widths": [{"width": 1024, "status": "FAIL", "codes": ["HEARTBEAT"], "tabs": ["1D"], "steps": {"ok": 0, "total": 4}, "seconds": 0.1,
                           "findings": [{"code": "GEMARC_GATE_013", "name": "HEARTBEAT", "detail": "frame counter did not advance", "tab": "2D"}]}]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root: Path) -> dict:
    """``{relative path: sha256}`` of EVERY file under ``root`` (``.verify``, ``oracle-sheet.md`` and all)."""
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}


_BASE: dict = {}


def setUpModule() -> None:
    tmp = tempfile.TemporaryDirectory(prefix="atlas-release-")
    _BASE["tmp"] = tmp
    base = Path(tmp.name)
    (base / "ws").mkdir()
    (base / "scratch").mkdir()
    # a finished tiny project (done, no browser needed to make it) and its evidence root: every test works on a copy
    run = P.run_fixture("tiny", 1, base / "ws", base / "scratch", browser=False)
    assert run["final"]["done"], run["final"]
    _BASE.update(base=base, ws=base / "ws", roots=run["roots"], counter=0)


def tearDownModule() -> None:
    _BASE["tmp"].cleanup()


class ReleaseCase(unittest.TestCase):
    """A fresh copy of the finished project for every test, with a ``Session`` on it."""

    maxDiff = None

    def setUp(self):
        _BASE["counter"] += 1
        n = _BASE["counter"]
        self.ws = _BASE["base"] / f"copy{n}" / "ws"
        shutil.copytree(_BASE["ws"], self.ws)
        self.roots = {}
        for alias, path in _BASE["roots"].items():
            self.roots[alias] = _BASE["base"] / f"copy{n}" / "roots" / alias
            shutil.copytree(path, self.roots[alias])
        self.s = P.Session(self.ws, self.roots)
        self.s.__enter__()
        self.addCleanup(self.s.__exit__, None, None, None)
        self.dist = self.s.dist
        self.paths = pj.project_paths(pj.get_workspace(self.ws, create=False), P.PROJECT)

    # -- helpers ------------------------------------------------------------------------------------------------

    def release(self, verdict=None, **kw):
        """A release build with the browser verdict stood in (``verdict`` None = the real gate)."""
        args = {"release": True, "gate": "smoke", **kw}
        if verdict is None:
            return self.s.call("build", "gates", **args)
        with mock.patch.object(smoke, "verify_gate", return_value=copy.deepcopy(verdict)):
            return self.s.call("build", "gates", **args)

    def stage(self) -> Path:
        return build.stage_dir(self.paths)

    def assert_refused(self, reply, kinds, *, before: dict):
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_RELEASE), reply)
        self.assertNotIn("page", reply, "a refused release names no page: none was written")
        self.assertEqual(reply["release"]["ok"], False)
        self.assertTrue(set(kinds) <= {b.split()[1] for b in reply["blockers"]} or reply["release"]["blockerCount"] > len(reply["blockers"]), (kinds, reply["blockers"]))
        after = tree(self.dist)
        self.assertEqual({k: v for k, v in after.items() if k != "oracle-sheet.md"}, {k: v for k, v in before.items() if k != "oracle-sheet.md"},
                         "dist/ is exactly what it was: the page, the info, the documents, the diagrams, the prompts, .verify")
        self.assertFalse(self.stage().exists(), "the stage directory is removed after a refusal")
        self.assertLessEqual(len(json.dumps(reply, ensure_ascii=False).encode("utf-8")), 2000)

    def assert_released(self, reply):
        self.assertEqual(reply["status"], "ok", reply)
        self.assertEqual(reply["release"], {"requested": True, "ok": True})
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual((info["release"]["requested"], info["release"]["ok"], info["release"]["blockers"]), (True, True, []))
        self.assertEqual(info["gate"]["status"], "PASS")
        self.assertEqual(info["page"]["sha256"], sha(self.dist / "index.html"))
        self.assertFalse(self.stage().exists())

    def exposures(self) -> dict:
        """``{ledger key: how many times a build embedded its trace}``: what a later edit of the expectation is judged against."""
        items = json.loads(self.paths.oracle.read_text(encoding="utf-8"))["items"]
        return {key: entry["exposed"]["build"] for key, entry in items.items()}

    def confirm_all(self):
        """What the user does: keep the APPROVE-ALL line of the sheet in ``atlas/oracle-confirm.txt``."""
        sheet = (self.dist / "oracle-sheet.md").read_text(encoding="utf-8")
        digest = re.search(r"^APPROVE-ALL ([0-9a-f]{12})$", sheet, re.M).group(1)
        (self.paths.atlas / "oracle-confirm.txt").write_text(f"APPROVE-ALL {digest}\n", encoding="utf-8")

    def reveal_and_edit_the_first_expectation(self):
        """Seal, look at the trace of the case (a reveal), then edit the expectation: the item is ``revised`` and waits for the user."""
        self.s.call("simulate", "gates")
        case = json.loads(self.paths.oracle.read_text(encoding="utf-8"))["items"]
        case_id = next(k for k in case if k.startswith("case:"))[5:]
        self.s.call("simulate", "gates", case=case_id, detail="trace")
        slices = self.paths.slices
        name = next(p.stem for p in sorted(slices.glob("*.atl")) if "## expect:" in p.read_text(encoding="utf-8"))
        text = (slices / f"{name}.atl").read_text(encoding="utf-8")
        expect_rows = text.split("## expect:", 1)[1].splitlines()[1:]
        row = next(line for line in expect_rows if line.startswith(f"{case_id} |"))
        nodes = row.split(" | ")[1].split(",")
        edited = f"{case_id} | {','.join(nodes[:-1])} | by=author"      # one participant fewer: a different assertion, written after a reveal
        reply = self.s.put(name, f"#atlas 1\n## expect: case | nodes | opts\n{edited}\n", mode="patch")
        self.assertEqual(reply["status"], "committed", reply)
        return case_id


class ReleaseRefusalTests(ReleaseCase):
    def test_a_clean_finished_project_releases_and_swaps_dist(self):
        before = tree(self.dist)
        reply = self.release(GOOD_VERDICT)
        self.assert_released(reply)
        after = tree(self.dist)
        self.assertNotEqual(after["index.html"], before["index.html"], "the release page differs from the earlier plain build (its QA badge says release)")
        self.assertEqual(after["oracle-sheet.md"], before["oracle-sheet.md"], "the sheet is not part of the swap")
        self.assertTrue(set(before) <= set(after), "nothing was lost")
        self.assertEqual(sorted(set(after) - set(before)), [".verify/gate-report.json", ".verify/gate-stamp.json"], "only the gate's report and stamp are new; nothing is left over from the stage")
        self.assertGreaterEqual(json.loads((self.paths.oracle).read_text(encoding="utf-8"))["items"].__len__(), 1)
        entry = next(v for k, v in json.loads(self.paths.oracle.read_text(encoding="utf-8"))["items"].items() if k.startswith("case:"))
        self.assertIn("bite", entry, "a release build runs the mutants first (OR-004)")

    def test_a_release_without_the_browser_gate_is_refused_and_nothing_is_written(self):
        before = tree(self.dist)
        reply = self.release(None, gate="none")
        self.assert_refused(reply, ["gate"], before=before)
        self.assertEqual(reply["gate"], "NOT-RUN")
        self.assertTrue(any("gate was not run" in b for b in reply["blockers"]), reply["blockers"])
        self.assertIn("browser gate", " ".join(reply["next"]))
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok", "positive control: with the gate it releases")

    def test_a_release_without_the_documents_is_refused_and_the_documents_survive(self):
        """``docs=false`` used to be accepted as a release: the swap then deleted the previous documents, diagrams and prompts and ``status`` said "not done"."""
        before = tree(self.dist)
        derived = sorted(name for name in before if name.startswith(("docs/", "diagrams/", "prompts/")))
        self.assertTrue(derived, "the finished project has documents, diagrams and prompts in dist/")
        reply = self.release(GOOD_VERDICT, docs=False)
        self.assert_refused(reply, ["docs"], before=before)
        self.assertEqual(reply["gate"], "NOT-RUN", "refused before anything was staged or gated")
        self.assertTrue(any("docs=true" in b for b in reply["blockers"]), reply["blockers"])
        self.assertIn("documents", " ".join(reply["next"]))
        both = self.release(GOOD_VERDICT, docs=False, gate="none")
        self.assertEqual({b.split()[1] for b in both["blockers"]}, {"gate", "docs"}, "every blocker is listed at once")
        self.assert_released(self.release(GOOD_VERDICT))            # positive control: with the documents it releases
        self.assertEqual(sorted(name for name in tree(self.dist) if name.startswith(("docs/", "diagrams/", "prompts/"))), derived)

    def test_the_command_line_refuses_a_release_without_the_documents_too(self):
        from gemarc_mcp.atlas import cli

        before = tree(self.dist)
        reply, code = cli.run(["--root", str(self.ws), "build", P.PROJECT, "--release", "--no-docs", "--gate", "smoke"])
        self.assertEqual((reply["status"], reply["code"], code), ("invalid", build.E_RELEASE, 1), reply)
        self.assertTrue(any("docs=true" in b for b in reply["blockers"]), reply["blockers"])
        self.assertEqual({k: v for k, v in tree(self.dist).items() if k != "oracle-sheet.md"}, {k: v for k, v in before.items() if k != "oracle-sheet.md"})
        self.assertFalse(self.stage().exists())

    def test_the_refusal_says_that_only_the_sheet_is_refreshed(self):
        reply = self.release(None, gate="none")
        self.assertIn("no page or document of dist/ was changed", reply["message"])
        self.assertIn("oracle-sheet.md is refreshed", reply["message"])
        self.assertNotIn("dist/ is unchanged", reply["message"], "dist/oracle-sheet.md is rewritten by a refusal, so 'unchanged' was not true")

    def test_a_refused_release_records_no_build_exposure_and_a_release_records_one_per_embedded_trace(self):
        """A refusal shows nobody a trace, so it must not make a later honest edit of the expectation ``revised``."""
        before = self.exposures()
        self.assertTrue(before and all(v >= 1 for k, v in before.items() if k.startswith("case:")), "the finished project was built once")
        stamp = self.paths.stamp.read_bytes()
        self.release(None, gate="none")                                    # no browser gate
        self.release(GOOD_VERDICT, docs=False)                             # no documents
        self.release(RED_VERDICT)                                          # the staged page was gated and failed
        self.paths.stamp.unlink()
        self.release(GOOD_VERDICT)                                         # stale evidence
        self.paths.stamp.write_bytes(stamp)
        self.assertEqual(self.exposures(), before, "four refusals, not one exposure recorded")
        self.assert_released(self.release(GOOD_VERDICT))
        after = self.exposures()
        grown = {k: after[k] - before[k] for k in after}
        self.assertTrue(set(grown.values()) <= {0, 1}, grown)
        self.assertGreaterEqual(sum(grown.values()), 1, "the released page embeds traces: their exposure is recorded once")

    def test_the_oracle_gate_is_judged_again_after_the_exposure_and_a_closed_gate_refuses(self):
        """One fingerprint is one exposure unit: the exposure can revise a sibling. The release then judges the ledger as it stands NOW (as it did before the exposure was deferred)."""
        before = tree(self.dist)
        real, seen = oracle.release_gate, []

        def closes_after_the_exposure(ws, project):
            out = real(ws, project)
            seen.append(1)
            if len(seen) < 2:
                return out
            return {**out, "ok": False, "blockers": [{"kind": "revised", "rule": "OR-005", "id": "x", "message": "a sibling became revised"}]}
        with mock.patch.object(oracle, "release_gate", side_effect=closes_after_the_exposure):
            reply = self.release(GOOD_VERDICT)
        self.assertEqual(len(seen), 2, "once before the exposure, once after it")
        self.assert_refused(reply, ["revised"], before=before)
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok", "positive control")

    def test_a_refused_release_on_a_project_without_a_dist_creates_no_page(self):
        shutil.rmtree(self.dist)
        reply = self.release(RED_VERDICT)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_RELEASE))
        self.assertFalse((self.dist / "index.html").exists())
        self.assertFalse((self.dist / "build-info.json").exists())
        self.assertEqual(sorted(p.name for p in self.dist.iterdir()), ["oracle-sheet.md"], "only the user's hand-over sheet is written by a refusal")
        self.assertFalse(self.stage().exists())

    def test_a_red_browser_gate_refuses_and_a_previous_dist_survives(self):
        before = tree(self.dist)
        reply = self.release(RED_VERDICT)
        self.assert_refused(reply, ["gate"], before=before)
        self.assertEqual(reply["gate"], "FAIL")
        self.assertTrue(any("browser gate is FAIL" in b for b in reply["blockers"]))
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok")

    def test_a_skipped_gate_is_not_a_pass_for_a_release(self):
        before = tree(self.dist)
        with mock.patch.object(smoke, "find_browser", return_value=None):
            reply = self.release(None)
        self.assert_refused(reply, ["gate"], before=before)
        self.assertEqual(reply["gate"], "SKIP")
        self.assertTrue(any("SKIP" in b for b in reply["blockers"]))

    def test_an_inconclusive_gate_is_not_a_pass_for_a_release(self):
        before = tree(self.dist)
        verdict = copy.deepcopy(GOOD_VERDICT)
        verdict["status"] = "INCONCLUSIVE"
        verdict["widths"][0]["status"] = "INCONCLUSIVE"
        reply = self.release(verdict)
        self.assert_refused(reply, ["gate"], before=before)
        self.assertEqual(reply["gate"], "INCONCLUSIVE")

    def test_a_revised_expectation_refuses_until_the_user_confirms_it(self):
        self.s.call("build", "gates")                                  # a plain build first: the previous good dist
        case_id = self.reveal_and_edit_the_first_expectation()
        self.s.call("simulate", "gates")
        self.s.call("build", "gates")
        before = tree(self.dist)
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["revised"], before=before)
        self.assertEqual(reply["oracle"]["revised"], 1, reply["oracle"])
        self.assertIn("never write that file", " ".join(reply["next"]))
        self.assertIn(case_id, " ".join(reply["blockers"]))
        self.assertTrue((self.dist / "oracle-sheet.md").is_file(), "the refusal wrote the sheet the user needs")
        self.assertFalse((self.paths.atlas / "oracle-confirm.txt").exists(), "the MCP never writes the user's confirmation")
        self.confirm_all()                                              # positive control: the user confirms, the same project releases
        self.assert_released(self.release(GOOD_VERDICT))

    def test_an_unsealed_expectation_refuses(self):
        """A typo in an expect row: the expectation never seals (OR-000) and the model has an ERROR: nothing is built, nothing is released."""
        before = tree(self.dist)
        slices = self.paths.slices
        name = next(p.stem for p in sorted(slices.glob("*.atl")) if "## expect:" in p.read_text(encoding="utf-8"))
        text = (slices / f"{name}.atl").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if "## expect" not in line and re.match(r"^[A-Z0-9-]+ \| [^|]+ \| by=author$", line))
        head, nodes, tail = row.split(" | ")
        broken = f"{head} | {nodes.split(',')[0]}typo,{','.join(nodes.split(',')[1:])} | {tail}"
        self.assertEqual(self.s.put(name, f"#atlas 1\n## expect: case | nodes | opts\n{broken}\n", mode="patch")["status"], "committed")
        reply = self.release(GOOD_VERDICT)
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertIn(reply["code"], (build.E_REFUSED, build.E_RELEASE), "ERROR findings refuse every build; the release gate also lists the item as unsealed")
        self.assertEqual({k: v for k, v in tree(self.dist).items() if k != "oracle-sheet.md"}, {k: v for k, v in before.items() if k != "oracle-sheet.md"})
        self.assertFalse(self.stage().exists())
        gate = oracle.release_gate(pj.get_workspace(self.ws, create=False), P.PROJECT)
        self.assertIn("unsealed", {b["kind"] for b in gate["blockers"]})
        self.assertEqual(self.s.put(name, f"#atlas 1\n## expect: case | nodes | opts\n{row}\n", mode="patch")["status"], "committed")
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok", "positive control: the typo repaired, it releases")

    def test_a_tampered_ledger_is_unverified_provenance_until_the_user_confirms_everything(self):
        self.s.call("simulate", "gates")
        data = json.loads(self.paths.oracle.read_text(encoding="utf-8"))
        data["tick"] = int(data.get("tick", 0)) + 7                     # edited by hand, the chain is not recomputed
        self.paths.oracle.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        before = tree(self.dist)
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["provenance"], before=before)
        self.confirm_all()
        self.assert_released(self.release(GOOD_VERDICT))

    def test_unverified_implemented_claims_refuse(self):
        before = tree(self.dist)
        stamp = self.paths.stamp
        saved = stamp.read_bytes()
        stamp.unlink()
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertTrue(any("EV-003" in b and "verified" in b for b in reply["blockers"]), reply["blockers"])
        self.assertEqual(self.s.call("evidence", "gates")["claims"]["unverified"], 0)
        self.assert_released(self.release(GOOD_VERDICT))
        self.assertTrue(stamp.is_file() and saved)

    def test_a_stamp_of_another_anchor_does_not_count_as_verified(self):
        """The stamp holds an entry for the id, but the row cites another anchor now (edited after the stamp): not verified."""
        before = tree(self.dist)
        slices = self.paths.slices
        name = next(p.stem for p in sorted(slices.glob("*.atl")) if "## evidences:" in p.read_text(encoding="utf-8"))
        text = (slices / f"{name}.atl").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if re.match(r"^[A-Z0-9-]+ \| src/.+\.py \| ", line))
        ident, path, anchor, topic = row.split(" | ")[:4]
        shorter = anchor.rstrip("(")
        self.assertNotEqual(shorter, anchor)
        patch = f"#atlas 1\n## evidences: id | file | anchor | topic | opts\n@set repo=main\n{ident} | {path} | {shorter} | {topic}\n"
        self.assertEqual(self.s.put(name, patch, mode="patch")["status"], "committed")
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertEqual(self.s.call("evidence", "gates")["claims"]["unverified"], 0)
        self.assert_released(self.release(GOOD_VERDICT))

    def source_file(self) -> Path:
        return next(self.roots["main"].rglob("*.py"))

    def test_the_evidence_gates_key_on_the_claims_not_on_the_configured_mode(self):
        """``snap.mode`` is ``meta.mode or config.mode``: a model that carries an Implemented claim is held to the evidence rules whatever the mode says."""
        import dataclasses

        cfg = pj.load_config(self.paths)
        snap = status_mod.load_snapshot(self.paths, cfg)
        self.assertEqual(snap.mode, "asis")
        self.assertTrue([i for k in ("nodes", "edges") for i in snap.model[k] if i.get("verification") == "Implemented"], "the finished project makes Implemented claims")
        self.paths.stamp.unlink()
        as_is = [b["rule"] for b in build._evidence_blockers(snap)]       # noqa: SLF001
        self.assertIn("EV-003", as_is)
        flipped = dataclasses.replace(snap, config=dataclasses.replace(cfg, mode="tobe"), model={**snap.model, "meta": {**snap.model["meta"], "mode": "tobe"}})
        self.assertEqual(flipped.mode, "tobe")
        self.assertEqual([b["rule"] for b in build._evidence_blockers(flipped)], as_is, "the release gate no longer returns early for a to-be snapshot")   # noqa: SLF001
        gate = status_mod._gate_evidence(flipped)                          # noqa: SLF001
        self.assertEqual((gate["applicable"], gate["ok"]), (True, False))
        clean = dataclasses.replace(flipped, model={**flipped.model, "nodes": [], "edges": []})
        self.assertEqual(build._evidence_blockers(clean), [], "a model without a claim has nothing to verify")   # noqa: SLF001
        self.assertEqual(status_mod._gate_evidence(clean)["applicable"], False)   # noqa: SLF001

    def test_flipping_the_config_to_tobe_leaves_no_implemented_claim_to_release(self):
        """What the flip really does (it needs a hand edit of ``atlas.toml``, the same trust boundary as the oracle limits): the derived ``verification`` of
        every claim becomes ``Designed``, so there is nothing Implemented that skipped the evidence gate; the release is a to-be release of a to-be model."""
        toml = self.paths.atlas / "atlas.toml"
        toml.write_text(re.sub(r'mode\s*=\s*"asis"', 'mode = "tobe"', toml.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        snap = status_mod.load_snapshot(self.paths, pj.load_config(self.paths))
        self.assertEqual(snap.mode, "tobe")
        levels = {i.get("verification") for k in ("nodes", "edges") for i in snap.model[k]}
        self.assertNotIn("Implemented", levels, levels)
        self.assertEqual(self.s.call("check", "gates", mode="full")["counts"]["error"], 0)

    def test_stale_evidence_a_source_that_moved_refuses_until_evidence_runs_again(self):
        before = tree(self.dist)
        target = self.source_file()
        target.write_bytes(b"# a line inserted above the anchors\n" + target.read_bytes())
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertTrue(any("EV-005" in b and "stale" in b for b in reply["blockers"]), reply["blockers"])
        self.assertIn("gemarc_atlas_evidence", " ".join(reply["next"]))
        self.assertEqual(self.s.call("evidence", "gates")["fail"], 0, "the anchors moved by one line: evidence re-stamps them")
        self.assert_released(self.release(GOOD_VERDICT))

    def test_stale_evidence_an_edited_block_needs_the_row_to_be_cited_again(self):
        """The anchor is still where it was but the lines of the stamped block changed: ``evidence`` never accepts that on its own."""
        before = tree(self.dist)
        target = self.source_file()
        text = target.read_bytes().decode("utf-8")
        target.write_bytes(text.replace("    pass", "    return 1", 1).encode("utf-8"))
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertTrue(any("RANGE_CONTENT_CHANGED" in b for b in reply["blockers"]), reply["blockers"])
        replied = self.s.call("evidence", "gates")
        self.assertGreaterEqual(replied.get("stale", 0), 1, "the evidence reply says that an edited block was not re-stamped")
        self.assertNotIn("all evidence rows verified", " ".join(replied["next"]))
        self.assertIn("EV-005", " ".join(replied["next"]))
        self.assertIn("different anchor", " ".join(replied["next"]))
        again = self.release(GOOD_VERDICT)
        self.assertEqual(again["code"], build.E_RELEASE, "running evidence again does not accept an edited block (never applied on its own)")
        # the remedy the hint names: read the code again and cite the rows with the anchor as it reads now (here a prefix of the same line)
        rel = target.relative_to(self.roots["main"]).as_posix()
        name = next(p.stem for p in sorted(self.paths.slices.glob("*.atl")) if "## evidences:" in p.read_text(encoding="utf-8"))
        rows = [line.split(" | ") for line in (self.paths.slices / f"{name}.atl").read_text(encoding="utf-8").split("## evidences:", 1)[1].splitlines()[1:] if line.count(" | ") >= 3]
        cited = [(ident, path, anchor, topic) for ident, path, anchor, topic, *_ in rows if path == rel]
        self.assertTrue(cited, "the edited file backs at least one evidence row")
        patch = "#atlas 1\n## evidences: id | file | anchor | topic | opts\n@set repo=main\n" + "".join(f"{i} | {p} | {a[:-1]} | {t}\n" for i, p, a, t in cited)
        self.assertEqual(self.s.put(name, patch, mode="patch")["status"], "committed")
        cited_again = self.s.call("evidence", "gates")
        self.assertEqual(cited_again["fail"], 0, "a row cited again is verified from its anchor alone")
        self.assertNotIn("stale", cited_again)
        self.assert_released(self.release(GOOD_VERDICT))

    def test_stale_evidence_an_anchor_that_is_gone_refuses(self):
        before = tree(self.dist)
        target = self.source_file()
        lines = target.read_bytes().decode("utf-8").split("\n")
        target.write_bytes("\n".join(line for line in lines if not line.startswith(("def ", "class ", "function "))).encode("utf-8"))
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertTrue(any("ANCHOR_NOT_FOUND" in b or "FILE_NOT_FOUND" in b for b in reply["blockers"]), reply["blockers"])

    def test_no_evidence_root_means_the_release_cannot_re_verify(self):
        before = tree(self.dist)
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMARC_ATLAS_ROOTS", None)
            reply = self.s.call("build", "gates", release=True, gate="smoke")
        self.assert_refused(reply, ["evidence"], before=before)
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok", "positive control: with the operator's roots it releases")

    def test_a_page_that_fails_a_build_check_is_refused_before_anything_is_staged(self):
        """BD-005 (the page is larger than the limit) is an ERROR of the build checks: the release is refused with the page code, nothing is staged or gated, dist is untouched."""
        from gemarc_mcp.atlas import htmlassemble

        before = tree(self.dist)
        with mock.patch.object(htmlassemble, "FAIL_BYTES", 1000):
            reply = self.release(GOOD_VERDICT)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_PAGE), reply)
        self.assertIn("BD-005", reply["message"])
        self.assertEqual(tree(self.dist), before)
        self.assertFalse(self.stage().exists())
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok", "positive control: with the real limit it releases")

    def test_a_not_authored_slot_in_the_documents_refuses(self):
        before = tree(self.dist)
        name = next(p.stem for p in sorted(self.paths.slices.glob("*.atl")) if "PL-NFR-M8" in p.read_text(encoding="utf-8"))
        row = next(line for line in (self.paths.slices / f"{name}.atl").read_text(encoding="utf-8").splitlines() if line.startswith("PL-NFR-M8 |"))
        self.assertEqual(self.s.put(name, "#atlas 1\n## policies: id | text | opts\n-PL-NFR-M8\n", mode="patch")["status"], "committed")
        reply = self.release(GOOD_VERDICT)
        self.assert_refused(reply, ["markers"], before=before)
        self.assertTrue(any("DC-L-004" in b for b in reply["blockers"]), reply["blockers"])
        self.assertEqual(self.s.put(name, f"#atlas 1\n## policies: id | text | opts\n{row}\n", mode="patch")["status"], "committed")
        self.assertEqual(self.release(GOOD_VERDICT)["status"], "ok")

    def test_every_blocker_is_listed_at_once_not_one_per_attempt(self):
        before = tree(self.dist)
        self.paths.stamp.unlink()
        name = next(p.stem for p in sorted(self.paths.slices.glob("*.atl")) if "PL-NFR-M8" in p.read_text(encoding="utf-8"))
        self.s.put(name, "#atlas 1\n## policies: id | text | opts\n-PL-NFR-M8\n", mode="patch")
        reply = self.release(RED_VERDICT)
        self.assertGreaterEqual(reply["release"]["blockerCount"], 3, reply)
        kinds = {b.split()[1] for b in reply["blockers"]}
        self.assertEqual(kinds, {"evidence", "markers", "gate"}, reply["blockers"])
        self.assertEqual({k: v for k, v in tree(self.dist).items() if k != "oracle-sheet.md"}, {k: v for k, v in before.items() if k != "oracle-sheet.md"})

    def test_the_refusal_reply_stays_small_and_names_no_host_path(self):
        self.paths.stamp.unlink()
        reply = self.release(RED_VERDICT)
        text = json.dumps(reply, ensure_ascii=False)
        self.assertLessEqual(len(text.encode("utf-8")), 2000)
        self.assertEqual(reply["dist"], "atlas/dist")
        self.assertNotIn(str(self.ws), text)
        self.assertNotIn(self.ws.parent.name, text)
        self.assertNotIn(str(self.ws).replace("\\", "\\\\"), text)

    def test_status_does_not_change_when_a_release_is_refused(self):
        before = self.s.status("final")
        self.paths.stamp.unlink()
        self.release(GOOD_VERDICT)
        self.s.call("evidence", "gates")
        after = self.s.status("final")
        self.assertEqual((before["done"], before["phase"]), (after["done"], after["phase"]))


class StageTests(ReleaseCase):
    def test_a_stage_left_by_a_crashed_build_is_removed_by_the_next_release(self):
        stage = self.stage()
        (stage / "docs").mkdir(parents=True)
        (stage / "docs" / "leftover.md").write_text("left behind\n", encoding="utf-8")
        (stage / "index.html").write_text("<html>old stage</html>", encoding="utf-8")
        reply = self.release(GOOD_VERDICT)
        self.assert_released(reply)
        self.assertNotIn("leftover.md", " ".join(tree(self.dist)))
        self.assertNotIn("old stage", (self.dist / "index.html").read_text(encoding="utf-8"))

    def test_an_exception_while_judging_leaves_no_stage_and_no_change(self):
        before = tree(self.dist)
        with mock.patch.object(smoke, "verify_gate", side_effect=OSError("the runner died")):
            reply = self.s.call("build", "gates", release=True, gate="smoke")
        self.assertEqual(reply["status"], "error", reply)
        self.assertEqual({k: v for k, v in tree(self.dist).items()}, before)
        self.assertFalse(self.stage().exists())

    def test_the_model_changing_during_the_build_is_refused_before_the_swap(self):
        before = tree(self.dist)
        calls = []

        def run_gate_then_edit(page, widths, out):
            verdict = copy.deepcopy(GOOD_VERDICT)
            name = next(p.stem for p in sorted(self.paths.slices.glob("*.atl")) if "## tests:" in p.read_text(encoding="utf-8"))
            calls.append(self.s.put(name, "#atlas 1\n## tests: id | title\nTS-EXTRA | A test added while the release was being built\n", mode="patch"))
            return verdict
        with mock.patch.object(build, "run_gate", side_effect=run_gate_then_edit):
            reply = self.s.call("build", "gates", release=True, gate="smoke")
        self.assertEqual(calls[0]["status"], "committed", calls)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_ORACLE), reply)
        self.assertEqual(tree(self.dist), before, "the page of the older model never replaces the page of the older model, and no half-swap happened")
        self.assertFalse(self.stage().exists())

    def test_a_link_at_the_stage_is_refused_and_nothing_outside_is_touched(self):
        if os.name != "nt":
            self.skipTest("junctions are a Windows reparse point; the symlink variant needs privilege on this machine")
        outside = _BASE["base"] / "outside-the-workspace"
        outside.mkdir()
        (outside / "victim.txt").write_text("keep me\n", encoding="utf-8")
        stage = self.stage()
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(stage), str(outside)], capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot create a junction here")
        self.addCleanup(lambda: os.rmdir(stage) if stage.exists() else None)
        before = tree(self.dist)
        reply = self.release(GOOD_VERDICT)
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertEqual(reply["code"], "GEMARC_AUTH_001")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["victim.txt"], "nothing was written through the junction")
        self.assertEqual((outside / "victim.txt").read_text(encoding="utf-8"), "keep me\n")
        self.assertEqual(tree(self.dist), before)

    def test_a_failed_swap_rolls_back_every_part(self):
        out, stage = _BASE["base"] / "swap" / "dist", _BASE["base"] / "swap" / "stage"
        for root, tag in ((out, "old"), (stage, "new")):
            for part in ("docs", "diagrams", ".verify"):
                (root / part).mkdir(parents=True)
                (root / part / "f.txt").write_text(f"{tag} {part}\n", encoding="utf-8")
            (root / "index.html").write_text(f"{tag} page\n", encoding="utf-8")
            (root / "build-info.json").write_text(f"{tag} info\n", encoding="utf-8")
        (out / "oracle-sheet.md").write_text("the user's sheet\n", encoding="utf-8")
        before = tree(out)
        real, seen = os.replace, []

        def flaky(src, dst, *a, **k):
            seen.append(Path(dst).name)
            if len(seen) == 7:                         # in the middle: after docs, diagrams and part of .verify were replaced
                raise OSError("disk went away")
            return real(src, dst, *a, **k)
        with mock.patch.object(os, "replace", side_effect=flaky):
            with self.assertRaises(OSError):
                build._swap_into(stage, out)           # noqa: SLF001
        self.assertEqual(tree(out), before, "every replaced part is back, the user's sheet was never touched")
        self.assertEqual((stage / "docs" / "f.txt").read_text(encoding="utf-8"), "new docs\n", "the staged parts are back in the stage")

    def test_a_permission_error_in_the_swap_is_a_coded_refusal_that_rolls_everything_back(self):
        out, stage = _BASE["base"] / "swap3" / "dist", _BASE["base"] / "swap3" / "stage"
        for root, tag in ((out, "old"), (stage, "new")):
            for part in ("docs", "diagrams", ".verify"):
                (root / part).mkdir(parents=True)
                (root / part / "f.txt").write_text(f"{tag} {part}\n", encoding="utf-8")
            (root / "index.html").write_text(f"{tag} page\n", encoding="utf-8")
        before = tree(out)
        real, seen = os.replace, []

        def locked(src, dst, *a, **k):
            seen.append(Path(dst).name)
            if len(seen) == 8:                         # after docs, diagrams and .verify were replaced: the page is the part another program holds open
                raise PermissionError(13, "The process cannot access the file because it is being used by another process")
            return real(src, dst, *a, **k)
        with mock.patch.object(os, "replace", side_effect=locked):
            with self.assertRaises(build.BuildRefused) as caught:
                build._swap_into(stage, out)           # noqa: SLF001
        self.assertEqual(caught.exception.code, build.E_SWAP)
        self.assertIn("Close what has dist/ open", caught.exception.message)
        self.assertEqual(caught.exception.extra["part"], "index.html")
        self.assertEqual(tree(out), before, "every part that was already replaced is back")
        self.assertEqual((stage / "docs" / "f.txt").read_text(encoding="utf-8"), "new docs\n")

    @unittest.skipUnless(os.name == "nt", "Windows refuses to rename a file that another program holds open; POSIX does not")
    def test_an_open_file_in_dist_makes_the_release_a_coded_refusal_and_a_retry_after_closing_it_releases(self):
        for victim in ("docs/en-US/BD.md", "index.html"):       # the first part to move, and the next to last (earlier parts must be rolled back)
            held = open(self.dist / victim, "rb")
            try:
                before = tree(self.dist)
                reply = self.release(GOOD_VERDICT)
            finally:
                held.close()
            self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_SWAP), (victim, reply))
            self.assertIn("Close what has dist/ open", reply["message"])
            self.assertEqual(tree(self.dist), before, f"{victim}: dist/ is byte for byte what it was")
            self.assertFalse(self.stage().exists())
        self.assert_released(self.release(GOOD_VERDICT))

    def test_a_successful_swap_replaces_every_owned_part_and_keeps_the_sheet(self):
        out, stage = _BASE["base"] / "swap2" / "dist", _BASE["base"] / "swap2" / "stage"
        (out / "docs").mkdir(parents=True)
        (out / "docs" / "old.md").write_text("old\n", encoding="utf-8")
        (out / "oracle-sheet.md").write_text("sheet\n", encoding="utf-8")
        (out / "index.html").write_text("old page\n", encoding="utf-8")
        (stage / "docs").mkdir(parents=True)
        (stage / "docs" / "new.md").write_text("new\n", encoding="utf-8")
        (stage / "index.html").write_text("new page\n", encoding="utf-8")
        build._swap_into(stage, out)               # noqa: SLF001
        self.assertEqual(sorted(tree(out)), ["docs/new.md", "index.html", "oracle-sheet.md"], "an owned part that the stage does not have is removed with its old files")
        self.assertEqual((out / "index.html").read_text(encoding="utf-8"), "new page\n")


@unittest.skipUnless(smoke.find_browser(dict(os.environ)) is not None, "no Edge or Chrome on this machine: the real gate cannot run")
class RealBrowserRelease(ReleaseCase):
    def test_the_real_browser_gate_releases_a_clean_project_and_status_stays_done(self):
        reply = self.release(None)
        self.assert_released(reply)
        self.assertEqual(reply["gate"], "PASS")
        self.assertEqual([line.split(":")[0] for line in reply["lines"]], ["PASS 1024", "PASS 1366", "PASS 1600"])
        state = self.s.status("final")
        self.assertEqual((state["done"], state["phase"]), (True, "done"))
        self.assertIn("browser", state["next"][0], "the stamp the release wrote binds the verdict to the page now in dist")
        stamp = status_mod.read_gate_stamp(self.paths)
        self.assertEqual((stamp["status"], stamp["pageSha256"]), ("PASS", sha(self.dist / "index.html")))
        self.assertEqual(list((self.dist / ".verify").glob("work*")), [], "the browser's scratch directory is gone")


if __name__ == "__main__":
    unittest.main()
