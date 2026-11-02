"""Evidence verifier tests: ported self-tests, SKIP semantics, redaction, drift, stamp, EV-012, roots from env only.

Synthetic files only (temporary directories); the repository's own ``src/gemarc_mcp`` serves as a real-text corpus.
Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_evidence*.py"
Experiments: PYTHONPATH=src python tests/atlas/test_evidence.py drift [--seeds N] [--mix X]   (insertion-drift table)
             PYTHONPATH=src python tests/atlas/test_evidence.py repo                            (verifier on src/gemarc_mcp)
"""
from __future__ import annotations

import copy
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import outline as ol
from gemarc_mcp.atlas import roots

REPO = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO / "src" / "gemarc_mcp"
NO_REV = ev.VerifyOptions(compute_root_rev=False)


def text(n: int, at: dict | None = None) -> str:
    at = at or {}
    return "\n".join(at.get(i + 1, f"line {i + 1}") for i in range(n))


def E(**kw) -> dict:
    return {"evidenceId": "E-T", "repository": "r", "filePath": "a.txt", "lineRange": [3, 5], "anchor": "needle",
            "topic": "t", "sourceKind": "source-code", **kw}


class Workspace:
    """A temporary repository root (alias ``r``) plus a project directory."""

    def __init__(self, files: dict[str, str | bytes] | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.project = self.base / "project"
        self.root.mkdir()
        self.project.mkdir()
        for rel, content in (files or {}).items():
            self.write(rel, content)

    def write(self, rel: str, content: str | bytes, *, where: Path | None = None) -> Path:
        path = (where or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def close(self) -> None:
        self._tmp.cleanup()

    def verify(self, rows, **kw) -> dict:
        kw.setdefault("project_dir", self.project)
        kw.setdefault("options", NO_REV)
        return ev.verify_evidence(rows, {"r": self.root}, **kw)


def run(files, rows, **kw) -> list[dict]:
    ws = Workspace(files)
    try:
        return ws.verify(rows, **kw)["results"]
    finally:
        ws.close()


def reference_check(file_text: str, line_range, anchor: str) -> bool:
    """The reference's own anchor rule (split lines, range end within the file, literal substring of the slice)."""
    lines = re.split(r"\r?\n", file_text)
    if not line_range[1] <= len(lines):
        return False
    return anchor in "\n".join(lines[line_range[0] - 1:line_range[1]])


def names(result: dict) -> list[str]:
    return [w["name"] for w in result.get("warnings", ())]


# ---------------------------------------------------------------------------------------------------------
# The 29 self-tests of the analysis prototype, in their original order.
# P01-P23 verifier, P24-P29 claim ceiling. "as-is" = same assertion, "adapted" = the same case under the anchor-first
# semantics or without a deferred feature (fuzzy matching, file-move search), "pinned" = the deferral itself is asserted.
# ---------------------------------------------------------------------------------------------------------
class PortedSelfTests(unittest.TestCase):
    def test_p01_exact_pass_as_is(self):
        [r] = run({"a.txt": text(20, {4: "has needle here"})}, [E()])
        self.assertEqual((r["status"], r["name"]), ("PASS", "OK"))
        self.assertEqual(r["resolved"]["lineRange"], [4, 4])

    def test_p02_shifted_unique_anchor_is_resolved_adapted(self):
        [r] = run({"a.txt": text(200, {144: "has needle here"})}, [E(lineRange=[3, 9])])
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["resolved"]["lineRange"], [144, 144])
        self.assertEqual(r["resolved"]["movedFrom"], [3, 9])
        self.assertIn("ANCHOR_MOVED", names(r))

    def test_p02b_shifted_ambiguous_anchor_is_a_drift_failure_with_set_range(self):
        [r] = run({"a.txt": text(200, {144: "has needle here", 150: "needle again"})}, [E(lineRange=[3, 9])])
        self.assertEqual(r["name"], "ANCHOR_DRIFT")
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(r["repair"]["action"], "SET_RANGE")
        self.assertEqual(r["repair"]["anchorLines"][0], 144)
        self.assertFalse(r["repair"]["applied"])

    def test_p03_reworded_adapted_no_fuzzy_repair(self):
        [r] = run({"a.txt": text(20, {4: "has needlee here"})}, [E(anchor="needle x")])
        self.assertEqual(r["name"], "ANCHOR_NOT_FOUND")
        self.assertEqual(r["repair"]["action"], "RE_EVIDENCE")
        self.assertEqual(r["repair"]["confidence"], 0.0)

    def test_p04_whitespace_only_difference(self):
        [r] = run({"a.txt": text(20, {4: "foo   bar   baz"})}, [E(anchor="foo bar baz")])
        self.assertEqual(r["name"], "ANCHOR_WS_MISMATCH")
        self.assertEqual(r["repair"]["action"], "REPLACE_ANCHOR")
        self.assertEqual(r["repair"]["suggested"]["anchor"], "foo   bar   baz")

    def test_p05_whitespace_difference_accepted_when_normalized_match_is_on(self):
        [r] = run({"a.txt": text(20, {4: "foo   bar   baz"})}, [E(anchor="foo bar baz")], options=replace(NO_REV, normalized_match=True))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("MATCH_NORMALIZED", names(r))
        self.assertFalse(r["stampable"], "a normalised match never refreshes a stamp by itself")

    def test_p06_empty_anchor(self):
        for anchor in ("", "   ", "\n"):
            [r] = run({"a.txt": text(20)}, [E(anchor=anchor)])
            self.assertEqual(r["name"], "ANCHOR_EMPTY")

    def test_p07_the_reference_rule_passes_an_empty_anchor_vacuously(self):
        self.assertTrue(reference_check(text(20), [3, 5], ""), "documented gap of the reference rule")

    def test_p08_from_greater_than_to(self):
        [r] = run({"a.txt": text(20, {4: "needle"})}, [E(lineRange=[5, 3])])
        self.assertEqual(r["name"], "RANGE_INVALID")

    def test_p09_from_zero(self):
        [r] = run({"a.txt": text(20, {4: "needle"})}, [E(lineRange=[0, 5])])
        self.assertEqual(r["name"], "RANGE_INVALID")

    def test_p10_range_beyond_eof_adapted(self):
        [r] = run({"a.txt": text(20, {4: "needle"})}, [E(lineRange=[18, 40])])
        self.assertEqual(r["status"], "PASS", "lineRange is output: the unique anchor is resolved at its real line")
        self.assertEqual(r["resolved"]["lineRange"], [4, 4])

    def test_p11_file_gone_adapted(self):
        [r] = run({"dir/b.txt": text(20, {7: "needle"})}, [E()])
        self.assertEqual(r["name"], "FILE_NOT_FOUND")
        self.assertNotIn("repair", r)

    def test_p12_file_moved_pinned_no_move_search(self):
        [r] = run({"dir/a.txt": text(20, {7: "needle"})}, [E()])
        self.assertEqual(r["name"], "FILE_NOT_FOUND")
        self.assertNotIn("MOVE_FILE", json.dumps(r), "file-move search is deferred (design 8.1)")

    def test_p13_unmapped_repository_is_skip(self):
        [r] = run({}, [E(repository="zzz")])
        self.assertEqual((r["status"], r["name"]), ("SKIP", "REPO_UNMAPPED"))

    def test_p14_dotdot_traversal(self):
        [r] = run({"a.txt": "x"}, [E(filePath="../secret.txt")])
        self.assertEqual(r["name"], "PATH_REJECTED")

    def test_p15_absolute_path(self):
        for path in ("C:/Windows/win.ini", "/etc/passwd", "\\\\host\\share\\x"):
            [r] = run({"a.txt": "x"}, [E(filePath=path)])
            self.assertEqual(r["name"], "PATH_REJECTED", path)

    def test_p16_bom_and_crlf(self):
        raw = b"\xef\xbb\xbf" + text(20, {4: "needle"}).replace("\n", "\r\n").encode()
        [r] = run({"a.txt": raw}, [E()])
        self.assertEqual(r["status"], "PASS")
        self.assertEqual((r["observed"]["eol"], r["observed"]["bom"]), ("crlf", True))
        self.assertEqual(r["resolved"]["lineRange"], [4, 4])

    def test_p17_ambiguous_anchor_still_passes_when_pinned_but_warns(self):
        [r] = run({"a.txt": text(20, {4: "needle", 9: "needle", 15: "needle"})}, [E(lineRange=[3, 5])])
        self.assertEqual(r["status"], "PASS")
        self.assertIn("ANCHOR_PINNED_AMBIGUOUS", names(r))

    def test_p18_ambiguous_anchor_harden_suggestion_is_unique_in_file(self):
        file_text = text(20, {4: "a needle here", 9: "b needle there", 15: "c needle again"})
        [r] = run({"a.txt": file_text}, [E(lineRange=[3, 5])])
        self.assertEqual(r["repair"]["action"], "HARDEN_ANCHOR")
        hardened = r["repair"]["suggested"]["anchor"]
        self.assertIn("needle", hardened)
        self.assertGreater(len(hardened), 6)
        self.assertEqual(file_text.count(hardened), 1)

    def test_p19_anchor_first_resolves(self):
        row = {k: v for k, v in E().items() if k != "lineRange"}
        [r] = run({"a.txt": text(20, {4: "needle"})}, [row])
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["resolved"], {"lineRange": [4, 4], "occurrences": 1, "unique": True})

    def test_p20_multi_line_anchor(self):
        [r] = run({"a.txt": text(20, {4: "needle", 5: "x"})}, [E(anchor="needle\nx", lineRange=[4, 5])])
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["resolved"]["lineRange"], [4, 5])

    def test_p21_phantom_line_after_the_final_newline(self):
        [r] = run({"a.txt": text(5) + "\n"}, [E(anchor="zzzzzz", lineRange=[3, 6])])
        self.assertEqual(r["status"], "FAIL")

    def test_p22_sensitive_file_never_echoes_adapted(self):
        files = {".env.test": text(20, {4: "DB_PASSWORD=hunter2-very-secret"})}
        [r] = run(files, [E(filePath=".env.test", anchor="DB_PASSWORD=hunterX-very-secret", lineRange=[3, 5])])
        self.assertEqual(r["name"], "SECRET_IN_ANCHOR")
        dump = json.dumps(r)
        self.assertNotIn("hunter", dump, "neither the file value nor the credential-looking anchor is echoed")
        # an innocent ambiguous anchor in a .env file: the drift hint names the line but withholds its text
        files = {".env.test": text(20, {4: "WEB_PORT=1012", 15: "WEB_PORT=1013"})}
        [r] = run(files, [E(filePath=".env.test", anchor="WEB_PORT=", lineRange=[10, 12])])
        self.assertEqual(r["name"], "ANCHOR_DRIFT")
        self.assertEqual(r["repair"]["anchorLines"][0], 15)
        self.assertEqual(r["repair"]["lineText"], "[withheld: sensitive file]")
        self.assertNotIn("1012", json.dumps(r))
        self.assertNotIn("1013", json.dumps(r))

    def test_p23_sensitive_line_drift_hint_withholds_text_adapted(self):
        files = {"conf.txt": text(20, {4: "password = hunter2", 15: "password reset"})}
        [r] = run(files, [E(filePath="conf.txt", anchor="password", lineRange=[2, 3])])
        self.assertEqual(r["name"], "ANCHOR_DRIFT")
        self.assertEqual(r["repair"]["anchorLines"][0], 4)
        self.assertTrue(r["repair"]["lineText"].startswith("[withheld"))
        self.assertNotIn("hunter2", json.dumps(r))

    # ---- claim ceiling (GemArc rank: Implemented above Observed) ----
    EVIDENCE = {"E1": {"sourceKind": "document"}, "E2": {"sourceKind": "source-code"}, "E3": {"sourceKind": "document"}}
    RESULTS = {"E1": {"status": "PASS"}, "E2": {"status": "FAIL"}, "E3": {"status": "SKIP"}}

    def claim(self, level, ids):
        return ev.check_claim({"id": "n", "verification": level, "evidenceIds": ids}, self.EVIDENCE, self.RESULTS)

    def test_p24_implemented_with_only_document_pass_exceeds_evidence(self):
        out = self.claim("Implemented", ["E1", "E2"])
        self.assertEqual(out["name"], "CLAIM_EXCEEDS_EVIDENCE")
        self.assertEqual(out["ceiling"], "Designed")

    def test_p25_designed_with_document_pass_is_fine(self):
        self.assertNotEqual(self.claim("Designed", ["E1"])["status"], "FAIL")

    def test_p26_inferred_without_evidence(self):
        self.assertEqual(self.claim("Inferred", [])["name"], "CLAIM_WITHOUT_EVIDENCE")

    def test_p27_unknown_without_evidence_is_fine(self):
        self.assertEqual(self.claim("Unknown", [])["status"], "PASS")

    def test_p28_implemented_with_only_unverified_evidence_exceeds(self):
        out = self.claim("Implemented", ["E2", "E3"])
        self.assertEqual(out["name"], "CLAIM_EXCEEDS_EVIDENCE")
        self.assertEqual(out["ceiling"], "Unknown")

    def test_p29_ceiling_of_code_and_document_is_implemented(self):
        self.assertEqual(ev.ceiling_of(["source-code", "document"]), "Implemented")

    # ---- claim layer beyond the port ----
    def test_gemarc_order_puts_implemented_above_observed(self):
        out = ev.check_claim({"id": "n", "verification": "Observed", "evidenceIds": ["E2"]}, self.EVIDENCE, {"E2": {"status": "PASS"}})
        self.assertEqual(out["status"], "PASS", "Observed is below Implemented in GemArc rank, so source-code evidence supports it")
        self.assertEqual(self.claim("Bogus", ["E1"])["name"], "LEVEL_UNKNOWN")
        self.assertEqual(self.claim("Designed", ["E1", "E3"])["name"], "CLAIM_HAS_UNVERIFIED_EVIDENCE")
        self.assertEqual(ev.ceiling_of([]), "Unknown")
        self.assertEqual(ev.ceiling_of(["user-requirement"]), "Designed")
        self.assertEqual(ev.ceiling_of(["bogus"]), "Unknown")


class ReferenceParityTests(unittest.TestCase):
    """Against the reference rule: it passes => we pass; we fail => it fails; the gap is unique anchors that moved."""

    def test_random_rows(self):
        rnd = random.Random(20260101)
        words = ["alpha", "beta", "gamma", "delta", "omega", "sigma"]
        agree_pass = ours_only = fail_both = 0
        for trial in range(300):
            lines = [f"{rnd.choice(words)} {rnd.choice(words)} {rnd.randint(0, 9)}" for _ in range(rnd.randint(20, 60))]
            file_text = "\n".join(lines) + ("\n" if rnd.random() < 0.5 else "")
            source = rnd.choice(lines)
            anchor = source[rnd.randint(0, 3):]
            if len(anchor.replace(" ", "")) < 6:
                anchor = source
            first = rnd.randint(1, len(lines))
            span = rnd.randint(0, 8)
            cited = [first, first + span]
            ref = reference_check(file_text, cited, anchor)
            [r] = run({"a.txt": file_text}, [E(anchor=anchor, lineRange=cited)])
            if ref:
                self.assertEqual(r["status"], "PASS", (trial, anchor, cited))
                agree_pass += 1
            elif r["status"] == "PASS":
                # the reference also rejects a cited range that ends past the last line even when the anchor is inside it;
                # lineRange is output here, so that alone is not a failure
                beyond_eof = cited[1] > len(re.split(r"\r?\n", file_text))
                self.assertTrue("ANCHOR_MOVED" in names(r) or beyond_eof, (trial, anchor, cited))
                ours_only += 1
            else:
                fail_both += 1
        self.assertGreater(agree_pass, 20)
        self.assertGreater(fail_both, 20)
        self.assertGreater(ours_only, 0)


class AllFailuresTests(unittest.TestCase):
    def test_every_broken_row_is_reported_not_just_the_first(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        rows = [E(evidenceId=f"E{i}", anchor=f"missing anchor {i}") for i in range(43)] + [
            E(evidenceId="OK1", anchor="present anchor text")]
        report = ws.verify(rows)
        self.assertEqual(report["summary"]["status"], {"PASS": 1, "WARN": 0, "FAIL": 43, "SKIP": 0})
        self.assertEqual(len(report["results"]), 44)
        self.assertEqual(sum(1 for r in report["results"] if r["name"] == "ANCHOR_NOT_FOUND"), 43)

    def test_one_line_per_problem_and_one_line_when_green(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        green = ev.compact_report(ws.verify([E(anchor="present anchor text")]))
        self.assertEqual(green.count("\n"), 0)
        self.assertIn("PASS 1", green)
        mixed = ws.verify([E(evidenceId="A", anchor="nowhere text"), E(evidenceId="B", repository="zz"), E(evidenceId="C", anchor="")])
        lines = ev.compact_report(mixed).split("\n")
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[1].startswith("FAIL A "))
        self.assertTrue(lines[2].startswith("SKIP B "))
        self.assertTrue(lines[3].startswith("FAIL C "))

    def test_compact_report_respects_the_byte_budget(self):
        ws = Workspace({"a.txt": text(30)})
        self.addCleanup(ws.close)
        rows = [E(evidenceId=f"E{i}", anchor=f"missing anchor number {i}") for i in range(200)]
        out = ev.compact_report(ws.verify(rows), max_bytes=1024)
        self.assertLessEqual(len(out.encode("utf-8")), 1024)
        self.assertIn("more lines cut", out)

    def test_schema_problems_are_failures_not_crashes(self):
        ws = Workspace({"a.txt": text(5)})
        self.addCleanup(ws.close)
        rows = [None, "x", {}, {"evidenceId": "E", "filePath": "a.txt"}, {"evidenceId": "E", "filePath": 5, "anchor": "x"},
                {"evidenceId": "E", "filePath": "a.txt", "anchor": "line 1", "repository": 3}]
        report = ws.verify(rows)
        self.assertEqual([r["name"] for r in report["results"]], ["SCHEMA"] * 6)


class SkipSemanticsTests(unittest.TestCase):
    def test_unmapped_alias_and_missing_root_are_skip_never_pass(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="U", repository="nowhere", anchor="present anchor text"),
                E(evidenceId="N", repository="", anchor="present anchor text"),
                E(evidenceId="M", repository="gone", anchor="present anchor text")]
        report = ev.verify_evidence(rows, {"r": ws.root, "gone": ws.base / "no-such-dir"}, options=NO_REV)
        self.assertEqual([r["status"] for r in report["results"]], ["SKIP"] * 3)
        self.assertEqual([r["name"] for r in report["results"]], ["REPO_UNMAPPED", "REPO_UNMAPPED", "ROOT_MISSING"])
        self.assertEqual(report["summary"]["status"]["PASS"], 0)
        self.assertEqual(ev.update_stamp(ev.empty_stamp(), report)["entries"], {}, "a SKIP row is never stamped")
        self.assertIn("SKIP U", ev.compact_report(report))
        self.assertEqual(ev.ceiling_of([]), "Unknown")
        claim = ev.check_claim({"id": "n", "verification": "Implemented", "evidenceIds": ["U"]},
                               {"U": {"sourceKind": "source-code"}}, {"U": report["results"][0]})
        self.assertEqual(claim["name"], "CLAIM_EXCEEDS_EVIDENCE", "a SKIP does not count towards the ceiling")

    def test_roots_come_only_from_the_argument_never_from_the_environment(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        row = E(anchor="present anchor text")
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": f"r={ws.root}"}):
            [skipped] = ev.verify_evidence([row], {}, options=NO_REV)["results"]
            [ok] = ev.verify_evidence([row], roots.roots_from_env(), options=NO_REV)["results"]
        self.assertEqual(skipped["status"], "SKIP")
        self.assertEqual(ok["status"], "PASS")

    def test_a_path_in_the_repository_field_is_not_a_root(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        for value in (str(ws.root), str(ws.root).replace("\\", "/"), "../repo", "D:/anything"):
            [r] = ws.verify([E(repository=value, anchor="present anchor text")])["results"]
            self.assertEqual((r["status"], r["name"]), ("SKIP", "REPO_UNMAPPED"), value)

    def test_path_escape_through_a_junction_or_symlink_is_a_failure(self):
        ws = Workspace({"a.txt": "x"})
        self.addCleanup(ws.close)
        outside = ws.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside secret line\n", encoding="utf-8")
        mechanism = make_link(ws.root / "link", outside, directory=True)
        if mechanism is None:
            print("SKIPPED: no symlink or junction could be created; verifier-level escape NOT tested", file=sys.stderr)
            self.skipTest("cannot create a symlink or junction on this machine")
        [r] = ws.verify([E(filePath="link/secret.txt", anchor="outside secret")])["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "PATH_ESCAPE"))
        self.assertNotIn("outside secret line", json.dumps(r))

    def test_case_mismatch_is_a_warning_not_a_failure(self):
        ws = Workspace({"Docs/Guide.TXT": text(30, {7: "present anchor text"})})
        self.addCleanup(ws.close)
        [r] = ws.verify([E(filePath="docs/guide.txt", anchor="present anchor text")])["results"]
        self.assertEqual(r["status"], "PASS")
        self.assertIn("PATH_CASE_MISMATCH", names(r))
        self.assertIn("Docs/Guide.TXT", [w for w in r["warnings"] if w["name"] == "PATH_CASE_MISMATCH"][0]["message"])
        self.assertEqual(r["stamp"]["filePath"], "docs/guide.txt")


def make_link(link: Path, target: Path, *, directory: bool) -> str | None:
    try:
        os.symlink(target, link, target_is_directory=directory)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        pass
    if directory and sys.platform == "win32":
        try:
            import _winapi  # type: ignore[import-not-found]

            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        except (OSError, ImportError, AttributeError):
            pass
    return None


class FileHandlingTests(unittest.TestCase):
    def test_binary_and_invalid_utf8_and_oversize_are_not_text(self):
        ws = Workspace({"bin.dat": b"\x00\x01\x02 needle \x00", "latin.txt": "caf\xe9 needle here\n".encode("latin-1"),
                        "big.txt": b"needle " * 20})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="B", filePath="bin.dat"), E(evidenceId="L", filePath="latin.txt"), E(evidenceId="G", filePath="big.txt")]
        report = ws.verify(rows, options=replace(NO_REV, max_bytes=100))
        self.assertEqual([r["name"] for r in report["results"]], ["NOT_TEXT", "NOT_TEXT", "NOT_TEXT"])
        self.assertIn("UTF-8", report["results"][1]["message"])

    def test_directory_and_missing_directory_are_file_not_found(self):
        ws = Workspace({"sub/x.txt": "x"})
        self.addCleanup(ws.close)
        for path in ("sub", "nodir/x.txt"):
            [r] = ws.verify([E(filePath=path)])["results"]
            self.assertEqual(r["name"], "FILE_NOT_FOUND", path)

    def test_device_and_stream_names_are_rejected_not_opened(self):
        ws = Workspace({"a.txt": "x"})
        self.addCleanup(ws.close)
        for path in ("NUL", "con.txt", "a.txt:hidden", "a.txt."):
            [r] = ws.verify([E(filePath=path)])["results"]
            self.assertEqual(r["name"], "PATH_REJECTED", path)

    def test_crlf_and_lf_files_resolve_to_the_same_lines(self):
        body = text(30, {9: "def find_copy(self):"})
        ws = Workspace({"lf.py": body, "crlf.py": body.replace("\n", "\r\n"), "bom.py": b"\xef\xbb\xbf" + body.encode()})
        self.addCleanup(ws.close)
        rows = [E(evidenceId=n, filePath=n, anchor="def find_copy(self):", lineRange=None) for n in ("lf.py", "crlf.py", "bom.py")]
        report = ws.verify(rows)
        self.assertEqual({tuple(r["resolved"]["lineRange"]) for r in report["results"]}, {(9, 9)})
        self.assertEqual([r["observed"]["eol"] for r in report["results"]], ["lf", "crlf", "lf"])
        self.assertEqual(len({r["stamp"]["rangeSha256"] for r in report["results"]}), 1, "fingerprints ignore the line ending")

    def test_mixed_line_endings_warn(self):
        ws = Workspace({"m.txt": b"one\r\ntwo\nthree needle line\r\nfour\n"})
        self.addCleanup(ws.close)
        [r] = ws.verify([E(filePath="m.txt", lineRange=None, anchor="three needle")])["results"]
        self.assertEqual(r["resolved"]["lineRange"], [3, 3])
        self.assertIn("FILE_MIXED_EOL", names(r))

    def test_short_and_wide_warnings(self):
        ws = Workspace({"a.txt": text(100, {5: "ab12", 50: "wide anchor text"})})
        self.addCleanup(ws.close)
        short, wide = ws.verify([E(evidenceId="S", anchor="ab12", lineRange=None),
                                 E(evidenceId="W", anchor="wide anchor text", lineRange=[1, 60])])["results"]
        self.assertEqual((short["status"], short["name"]), ("WARN", "ANCHOR_SHORT"), "a short anchor is never a PASS")
        self.assertFalse(short["stampable"])
        self.assertNotIn("stamp", short)
        self.assertEqual(short["resolved"]["lineRange"], [5, 5], "what was found is still reported")
        self.assertIn("RANGE_WIDE", names(wide))

    def test_anchor_must_have_six_non_blank_characters_to_avoid_the_warning(self):
        ws = Workspace({"a.txt": text(10, {4: "a b c d e f g"})})
        self.addCleanup(ws.close)
        [five, six] = ws.verify([E(evidenceId="F", anchor="a b c d e", lineRange=None), E(evidenceId="X", anchor="a b c d e f", lineRange=None)])["results"]
        self.assertEqual((five["status"], five["name"]), ("WARN", "ANCHOR_SHORT"))
        self.assertEqual(six["status"], "PASS")
        self.assertNotIn("ANCHOR_SHORT", names(six))


class AmbiguityTests(unittest.TestCase):
    def test_ambiguous_anchor_without_a_pin_is_an_error_with_hardening_candidates(self):
        file_text = text(40, {5: "a needle here", 15: "b needle there", 25: "c needle again"})
        [r] = run({"a.txt": file_text}, [E(lineRange=None)])
        self.assertEqual((r["status"], r["name"]), ("FAIL", "ANCHOR_AMBIGUOUS"))
        self.assertEqual(r["repair"]["action"], "HARDEN_ANCHOR")
        self.assertEqual([c["line"] for c in r["repair"]["candidates"]], [5, 15, 25])
        for candidate in r["repair"]["candidates"]:
            self.assertEqual(file_text.count(candidate["anchor"]), 1)
        self.assertNotIn("stamp", r, "an ambiguous anchor is never stamped on a guess")

    def test_a_stamp_pins_the_ambiguous_anchor_and_later_drift_is_a_suggestion(self):
        ws = Workspace({"a.txt": text(60, {10: "needle", 30: "needle", 50: "needle"})})
        self.addCleanup(ws.close)
        pinned = ws.verify([E(lineRange=[30, 30])])
        stamp = ev.update_stamp(ev.empty_stamp(), pinned)
        self.assertEqual(stamp["entries"]["E-T"]["lineRange"], [30, 30])
        again = ws.verify([E(lineRange=None)], stamp=stamp)
        self.assertEqual(again["results"][0]["status"], "PASS")
        self.assertEqual(again["results"][0]["resolved"]["lineRange"], [30, 30])
        ws.write("a.txt", "# one\n# two\n# three\n" + text(60, {10: "needle", 30: "needle", 50: "needle"}))
        moved = ws.verify([E(lineRange=None)], stamp=stamp)["results"][0]
        self.assertEqual((moved["status"], moved["name"]), ("FAIL", "ANCHOR_DRIFT"))
        self.assertEqual(moved["repair"]["anchorLines"], [33])
        self.assertEqual(moved["repair"]["method"], "fingerprint")
        self.assertGreaterEqual(moved["repair"]["confidence"], 0.99)

    def test_the_stale_pass_is_caught_by_the_fingerprint(self):
        original = {4: "needle", 14: "needle", 24: "needle"}
        ws = Workspace({"a.txt": text(40, original)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[14, 14])]))
        shifted = "\n".join(["# a", "# b", "# c", "# d", "# e", "# f", "# g", "# h", "# i", "# j"]) + "\n" + text(40, original)
        # ten lines above everything: the old line 4 now sits at 14, the stamped place
        ws.write("a.txt", shifted)
        row = E(lineRange=None)
        blind = ws.verify([row], stamp=stamp, options=replace(NO_REV, use_fingerprint=False, use_consensus=False))["results"][0]
        self.assertEqual((blind["status"], blind["resolved"]["lineRange"]), ("PASS", [14, 14]), "without the fingerprint: a stale PASS")
        careful = ws.verify([row], stamp=stamp)["results"][0]
        self.assertEqual((careful["status"], careful["name"]), ("FAIL", "ANCHOR_DRIFT"))
        self.assertEqual(careful["repair"]["anchorLines"], [24])
        self.assertFalse(careful["repair"]["applied"])

    def test_deleting_the_pinned_occurrence_never_moves_the_stamp_onto_the_survivor(self):
        base = {10: "needle", 30: "needle"}
        ws = Workspace({"a.txt": text(60, base)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        self.assertEqual(stamp["entries"]["E-T"]["occurrences"], 2)
        lines = text(60, base).split("\n")
        del lines[29]
        ws.write("a.txt", "\n".join(lines))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "ANCHOR_DRIFT"), "a unique survivor that is not the pinned one is not a pass")
        self.assertEqual(r["repair"]["anchorLines"], [10])
        self.assertLessEqual(r["repair"]["confidence"], 0.4)
        self.assertTrue(r["repair"]["needsReview"])
        self.assertEqual(ev.update_stamp(stamp, {"results": [r]})["entries"]["E-T"]["lineRange"], [30, 30], "the stamp did not move")

    def test_deleting_the_other_occurrence_keeps_the_pin(self):
        base = {10: "needle", 30: "needle"}
        ws = Workspace({"a.txt": text(60, base)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        lines = text(60, base).split("\n")
        del lines[9]
        ws.write("a.txt", "\n".join(lines))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["resolved"]["lineRange"], r["stampable"]), ("PASS", [29, 29], True))
        self.assertEqual(ev.update_stamp(stamp, {"results": [r]})["entries"]["E-T"]["lineRange"], [29, 29])

    def test_adjacent_identical_lines_deleting_the_pinned_one(self):
        base = {30: "needle", 31: "needle"}
        ws = Workspace({"a.txt": text(60, base)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        lines = text(60, base).split("\n")
        del lines[29]
        ws.write("a.txt", "\n".join(lines))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "ANCHOR_DRIFT"), "the survivor slid into the stamped place")

    def test_stamped_line_text_catches_an_insertion_inside_the_context(self):
        original = text(40, {10: "needle alpha", 11: "needle beta"})
        ws = Workspace({"a.txt": original})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[11, 11])]))
        # one line inserted between the two occurrences: both are equally near the stamped place 11, and the stamped
        # context (which now includes the new line) matches nowhere, so only the stamped line text can tell
        lines = original.split("\n")
        lines.insert(10, "# inserted")
        ws.write("a.txt", "\n".join(lines))
        row = E(lineRange=None)
        blind = ws.verify([row], stamp=stamp, options=replace(NO_REV, use_fingerprint=False))["results"][0]
        self.assertEqual((blind["name"], blind["repair"]["anchorLines"]), ("ANCHOR_DRIFT", [10]), "nearest-only: a tie, resolved to the wrong occurrence")
        found = ws.verify([row], stamp=stamp)["results"][0]
        self.assertEqual((found["status"], found["name"]), ("FAIL", "ANCHOR_DRIFT"))
        self.assertEqual(found["repair"]["anchorLines"], [12])
        self.assertEqual(found["repair"]["method"], "line")
        self.assertGreaterEqual(found["repair"]["confidence"], 0.8)

    def test_shift_consensus_beats_nearest_only(self):
        base = text(80, {20: "needle", 28: "needle", 25: "unique anchor alpha", 40: "unique anchor beta"})
        ws = Workspace({"a.txt": base})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="AMB", lineRange=[28, 28]), E(evidenceId="UA", anchor="unique anchor alpha", lineRange=[25, 25]),
                E(evidenceId="UB", anchor="unique anchor beta", lineRange=[40, 40])]
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify(rows))
        ws.write("a.txt", "\n".join(f"# insert {i}" for i in range(10)) + "\n" + base)
        free = [{k: v for k, v in r.items() if k != "lineRange"} for r in rows]
        nearest = ws.verify(free, stamp=stamp, options=replace(NO_REV, use_fingerprint=False, use_consensus=False))["results"][0]
        voted = ws.verify(free, stamp=stamp, options=replace(NO_REV, use_fingerprint=False))["results"][0]
        self.assertEqual(nearest["repair"]["anchorLines"], [30], "nearest to the stamped line is the wrong occurrence")
        self.assertEqual(voted["repair"]["anchorLines"], [38])
        self.assertEqual(voted["repair"]["method"], "consensus")
        self.assertGreaterEqual(voted["repair"]["confidence"], 0.8)

    def test_one_sided_shift_consensus_is_never_confident(self):
        base = text(80, {20: "needle", 28: "needle", 25: "unique anchor alpha"})
        ws = Workspace({"a.txt": base})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="AMB", lineRange=[28, 28]), E(evidenceId="UA", anchor="unique anchor alpha", lineRange=[25, 25])]
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify(rows))
        ws.write("a.txt", "\n".join(f"# insert {i}" for i in range(10)) + "\n" + base)
        free = [{k: v for k, v in r.items() if k != "lineRange"} for r in rows]
        voted = ws.verify(free, stamp=stamp, options=replace(NO_REV, use_fingerprint=False))["results"][0]
        self.assertEqual((voted["repair"]["method"], voted["repair"]["anchorLines"]), ("consensus", [38]))
        self.assertLess(voted["repair"]["confidence"], 0.8, "one neighbouring row on one side is weak evidence")
        self.assertTrue(voted["repair"]["needsReview"])

    def test_anchor_edited_in_place_is_not_found_and_carries_redacted_context(self):
        ws = Workspace({"a.txt": text(30, {10: "needle in a haystack"})})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(anchor="needle in a haystack", lineRange=[10, 10])]))
        ws.write("a.txt", text(30, {10: "needle in the haystack"}))
        [r] = ws.verify([E(anchor="needle in a haystack", lineRange=None)], stamp=stamp)["results"]
        self.assertEqual(r["name"], "ANCHOR_NOT_FOUND")
        self.assertEqual(r["repair"]["context"], ["needle in the haystack"], "exactly the stamped line, nothing around it")

    def test_a_stamp_for_a_different_anchor_is_ignored_with_a_warning(self):
        ws = Workspace({"a.txt": text(30, {10: "first anchor text", 20: "second anchor text"})})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(anchor="first anchor text", lineRange=[10, 10])]))
        [r] = ws.verify([E(anchor="second anchor text", lineRange=None)], stamp=stamp)["results"]
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["resolved"]["lineRange"], [20, 20])
        self.assertIn("STAMP_IGNORED", names(r))

    def test_content_change_around_a_pinned_anchor_warns_and_does_not_refresh(self):
        file_a = text(40, {10: "needle", 20: "needle"})
        ws = Workspace({"a.txt": file_a})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[20, 20])]))
        ws.write("a.txt", file_a.replace("line 19", "line nineteen, edited"))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual(r["status"], "PASS")
        self.assertIn("RANGE_CONTENT_CHANGED", names(r))
        self.assertFalse(r["stampable"])
        kept = ev.update_stamp(stamp, {"results": [r]})
        self.assertEqual(kept["entries"]["E-T"], stamp["entries"]["E-T"])
        accepted = ev.update_stamp(stamp, {"results": [r]}, accept_changes=True)
        self.assertNotEqual(accepted["entries"]["E-T"]["contextSha256"], stamp["entries"]["E-T"]["contextSha256"])


class NeverAutoAppliedTests(unittest.TestCase):
    def drifted(self):
        ws = Workspace({"a.txt": text(60, {10: "needle", 30: "needle", 50: "needle"}), "b.txt": text(20)})
        self.addCleanup(ws.close)
        row = E(lineRange=[30, 30])
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([row]))
        ws.write("a.txt", "# x\n# y\n" + text(60, {10: "needle", 30: "needle", 50: "needle"}))
        return ws, [E(lineRange=None)], stamp

    def test_verification_changes_neither_rows_nor_stamp_nor_files(self):
        ws, rows, stamp = self.drifted()
        rows_before, stamp_before = copy.deepcopy(rows), copy.deepcopy(stamp)
        files_before = sorted((p.relative_to(ws.base).as_posix(), p.read_bytes()) for p in ws.base.rglob("*") if p.is_file())
        report = ws.verify(rows, stamp=stamp)
        self.assertEqual(report["results"][0]["name"], "ANCHOR_DRIFT")
        self.assertEqual(rows, rows_before)
        self.assertEqual(stamp, stamp_before)
        self.assertEqual(files_before, sorted((p.relative_to(ws.base).as_posix(), p.read_bytes()) for p in ws.base.rglob("*") if p.is_file()))
        self.assertEqual(report["stamp"]["entries"]["E-T"], stamp_before["entries"]["E-T"], "the proposed stamp keeps the old place")

    def test_a_suggestion_is_applied_only_for_the_named_ids(self):
        ws, rows, stamp = self.drifted()
        report = ws.verify(rows, stamp=stamp)
        untouched = ev.update_stamp(stamp, report)
        self.assertEqual(untouched["entries"]["E-T"]["lineRange"], [30, 30])
        applied = ev.update_stamp(stamp, report, accept_repairs=["E-T"])
        self.assertEqual(applied["entries"]["E-T"]["lineRange"], [32, 32])
        self.assertEqual(stamp["entries"]["E-T"]["lineRange"], [30, 30], "the input stamp is not modified")
        with self.assertRaises(ev.EvidenceError):
            ev.update_stamp(stamp, report, accept_repairs=["nope"])
        ok_report = ws.verify([E(evidenceId="OK", anchor="line 2 ", lineRange=None)])
        with self.assertRaises(ev.EvidenceError):
            ev.update_stamp(stamp, ok_report, accept_repairs=["OK"])

    def test_after_accepting_the_row_passes(self):
        ws, rows, stamp = self.drifted()
        applied = ev.update_stamp(stamp, ws.verify(rows, stamp=stamp), accept_repairs=["E-T"])
        [r] = ws.verify(rows, stamp=applied)["results"]
        self.assertEqual((r["status"], r["resolved"]["lineRange"]), ("PASS", [32, 32]))

    def test_unique_anchor_that_moved_is_resolved_and_restamped(self):
        ws = Workspace({"a.txt": text(30, {10: "unique thing here"})})
        self.addCleanup(ws.close)
        row = E(anchor="unique thing here", lineRange=None)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([row]))
        ws.write("a.txt", "# new\n# new\n" + text(30, {10: "unique thing here"}))
        report = ws.verify([row], stamp=stamp)
        self.assertEqual(report["results"][0]["resolved"]["lineRange"], [12, 12])
        self.assertEqual(ev.update_stamp(stamp, report)["entries"]["E-T"]["lineRange"], [12, 12])

    def test_prune_drops_entries_of_rows_that_are_gone(self):
        stamp = {"schema": ev.STAMP_SCHEMA, "entries": {"OLD": {"lineRange": [1, 1]}}}
        self.assertIn("OLD", ev.update_stamp(stamp, {"results": []})["entries"])
        self.assertNotIn("OLD", ev.update_stamp(stamp, {"results": []}, prune=True)["entries"])


class StampFileTests(unittest.TestCase):
    def test_round_trip_bytes_and_shape(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        report = ev.verify_evidence([E(anchor="present anchor text", lineRange=None)], {"r": ws.root})
        stamp = ev.update_stamp(ev.empty_stamp(), report)
        path = ev.save_stamp(ws.project, stamp)
        self.assertEqual(path, ws.project / ".atlas" / "evidence-stamp.json")
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
        self.assertEqual(ev.load_stamp(ws.project), stamp)
        entry = stamp["entries"]["E-T"]
        for key in ("rangeSha256", "occurrences", "fileSha256", "rootRev", "lineRange", "contextSha256", "anchorSha256"):
            self.assertIn(key, entry)
        self.assertRegex(entry["rootRev"], r"^(git|manifest):[0-9a-f]+$")
        self.assertNotRegex(json.dumps(stamp), r"\d{4}-\d{2}-\d{2}T", "no timestamps")
        self.assertEqual(ev.stamp_text(stamp).encode(), raw)
        self.assertEqual(ev.stamp_text(ev.load_stamp(ws.project)).encode(), raw, "deterministic")

    def test_missing_and_broken_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ev.load_stamp(d), ev.empty_stamp())
            path = Path(d) / ".atlas" / "evidence-stamp.json"
            path.parent.mkdir()
            for bad in (b"{not json", b'{"schema": "other", "entries": {}}', b'{"schema": "gemarc-atlas-evidence-stamp/1", "entries": []}'):
                path.write_bytes(bad)
                with self.assertRaises(ev.EvidenceError):
                    ev.load_stamp(d)

    def test_merge_line_ranges_fills_only_matching_rows(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        row = {"evidenceId": "E-T", "repository": "r", "filePath": "a.txt", "anchor": "present anchor text", "topic": "t", "lineRange": None}
        stamp = ev.update_stamp(ev.empty_stamp(), ev.verify_evidence([row], {"r": ws.root}, options=NO_REV))
        merged = ev.merge_line_ranges([row, {**row, "evidenceId": "E-OTHER"}, {**row, "anchor": "changed anchor text"}], stamp)
        self.assertEqual(merged[0]["lineRange"], [5, 5])
        self.assertIsNone(merged[1]["lineRange"])
        stamp["entries"]["E-OTHER"] = stamp["entries"]["E-T"]
        self.assertEqual(ev.merge_line_ranges([{**row, "evidenceId": "E-OTHER", "anchor": "changed anchor text"}], stamp)[0]["lineRange"], None)
        self.assertIsNone(row["lineRange"], "input rows are not modified")


class RootRevisionTests(unittest.TestCase):
    def test_manifest_is_content_addressed_and_stable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.txt").write_text("one\n")
            files = {"a.txt": "h1", "b.txt": "h2"}
            first = ev.root_revision(root, files)
            self.assertRegex(first, r"^manifest:[0-9a-f]{16}$")
            self.assertEqual(first, ev.root_revision(root, dict(reversed(list(files.items())))))
            self.assertNotEqual(first, ev.root_revision(root, {"a.txt": "h1", "b.txt": "h3"}))

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_subtree_hash_only_for_a_clean_tracked_tree(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d)

            def git(*args):
                return subprocess.run(["git", "-C", str(top), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                       *args], capture_output=True, text=True, check=True).stdout.strip()

            git("init", "-q")
            (top / "sub").mkdir()
            (top / "sub" / "a.txt").write_text("one\n")
            (top / "b.txt").write_text("two\n")
            git("add", ".")
            git("commit", "-q", "-m", "init")
            whole = ev.root_revision(top, {"b.txt": "x", "sub/a.txt": "y"})
            sub = ev.root_revision(top / "sub", {"a.txt": "y"})
            self.assertEqual(whole, "git:" + git("rev-parse", "HEAD^{tree}")[:12])
            self.assertEqual(sub, "git:" + git("rev-parse", "HEAD:sub")[:12])
            self.assertNotEqual(whole, sub, "a subtree has its own hash; plain HEAD would be identical")
            self.assertNotEqual(whole, "git:" + git("rev-parse", "HEAD")[:12])
            (top / "b.txt").write_text("changed but not committed\n")
            self.assertRegex(ev.root_revision(top, {"b.txt": "x"}), r"^manifest:", "a dirty tree never claims a commit hash")
            git("add", ".")
            git("commit", "-q", "-m", "second")
            sub_after = ev.root_revision(top / "sub", {"a.txt": "y"})
            self.assertEqual(sub_after, "git:" + git("rev-parse", "HEAD:sub")[:12])
            (top / "sub" / "untracked.txt").write_text("u\n")
            self.assertEqual(ev.root_revision(top / "sub", {"a.txt": "y"}), sub_after, "an unrelated untracked file changes nothing")
            (top / "sub" / "a.txt").write_text("edited, not committed\n")
            self.assertRegex(ev.root_revision(top / "sub", {"a.txt": "y"}), r"^manifest:", "a cited file that is modified")
            git("checkout", "--", "sub/a.txt")
            (top / "sub" / "untracked.txt").unlink()
            self.assertRegex(ev.root_revision(top, {"missing.txt": "z"}), r"^manifest:", "a cited file that git does not track")

    def test_ev004_note_when_the_stamped_revision_differs(self):
        ws = Workspace({"a.txt": text(30, {5: "present anchor text"})})
        self.addCleanup(ws.close)
        row = E(anchor="present anchor text", lineRange=None)
        report = ev.verify_evidence([row], {"r": ws.root})
        stamp = ev.update_stamp(ev.empty_stamp(), report)
        self.assertEqual(report["summary"]["notes"], [])
        ws.write("a.txt", text(30, {5: "present anchor text", 6: "extra"}))
        later = ev.verify_evidence([row], {"r": ws.root}, stamp=stamp)
        self.assertEqual([n["rule"] for n in later["summary"]["notes"]], ["EV-004"])
        self.assertEqual(later["summary"]["notes"][0]["repository"], "r")
        self.assertIn("NOTE EV-004", ev.compact_report(later))


class SecretTests(unittest.TestCase):
    POSITIVE = [
        "AKIAABCDEFGHIJKLMNOP", "ghp_" + "a1" * 20, "-----BEGIN RSA PRIVATE KEY-----", "postgres://user:pass1234@db/app",
        'password = "hunter2"', "DB_PASSWORD=hunter2-very-secret", "api_key: 'abcd1234efgh'", "token = abc123def456",
        "$2a$10$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz0123", "xoxb-1234567890-abcdef", "sk-" + "A1b2C3d4" * 3,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    ]
    NEGATIVE = [
        "def check_password(user, password):", "token = self._next_token(", "password: str", "PASSWORD_MIN = 8",
        "api_key = os.environ['KEY']", "secret = None", "token = tokens[0]", "def create_loan(", "password = request.form",
        "DB_PASSWORD=", "WEB_PORT=1012", "password = ${DB_PASSWORD}", "token = <your-token>", "",
    ]

    def test_patterns(self):
        for line in self.POSITIVE:
            with self.subTest(line=line[:20]):
                self.assertTrue(ev.secret_findings(line), line)
        for line in self.NEGATIVE:
            with self.subTest(line=line):
                self.assertEqual(ev.secret_findings(line), [], line)
        self.assertEqual(ev.secret_findings(None), [])
        self.assertEqual(ev.secret_findings(5), [])

    def test_findings_never_contain_the_matched_text(self):
        for line in self.POSITIVE:
            for name in ev.secret_findings(line):
                self.assertRegex(name, r"^[a-z-]+$")

    def test_a_credential_anchor_or_topic_fails_and_is_never_echoed(self):
        ws = Workspace({"a.txt": text(20, {4: 'password = "hunter2"'})})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="A", anchor='password = "hunter2"', lineRange=None), E(evidenceId="T", anchor="line 4", topic="password = 'topsecret99'", lineRange=None),
                E(evidenceId="U", repository="zz", anchor="AKIAABCDEFGHIJKLMNOP")]
        report = ws.verify(rows)
        self.assertEqual([r["name"] for r in report["results"]], ["SECRET_IN_ANCHOR"] * 3, "the lint runs before roots are consulted")
        self.assertEqual({r["rule"] for r in report["results"]}, {"EV-006"})
        blob = json.dumps(report) + ev.compact_report(report, include_warnings=True)
        for secret in ("hunter2", "topsecret99", "AKIAABCDEFGHIJKLMNOP"):
            self.assertNotIn(secret, blob)

    def test_key_names_in_a_dotenv_file_are_fine_but_a_value_after_a_credential_key_is_not(self):
        ws = Workspace({".env": "WEB_PORT=1012\nDB_PASSWORD=hunter2-very-secret\nAPI_TOKEN=abcd1234secret\n"})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="K", filePath=".env", anchor="WEB_PORT=", lineRange=None),
                E(evidenceId="V", filePath=".env", anchor="API_TOKEN=abcd", lineRange=None),
                E(evidenceId="N", filePath=".env", anchor="DB_PASSWORD=", lineRange=None)]
        report = ws.verify(rows)
        self.assertEqual([r["status"] for r in report["results"]], ["PASS", "FAIL", "PASS"])
        self.assertEqual(report["results"][1]["name"], "SECRET_IN_ANCHOR")
        self.assertNotIn("abcd1234", json.dumps(report))

    def test_no_failure_path_echoes_a_secret_from_a_sensitive_file(self):
        secret_file = "# header\nAPI_TOKEN=abcd1234secret\nDB_PASSWORD=hunter2-very-secret\nWEB_PORT=1012\nWEB_PORT=1013\nlast line\n"
        ws = Workspace({".env.prod": secret_file, "credentials.json": '{"password": "hunter2-very-secret"}\n'})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="a", filePath=".env.prod", anchor="WEB_PORT=", lineRange=None),
                E(evidenceId="b", filePath=".env.prod", anchor="WEB_PORT=", lineRange=[1, 1]),
                E(evidenceId="c", filePath=".env.prod", anchor="WEB_POR=", lineRange=[2, 3]),
                E(evidenceId="d", filePath=".env.prod", anchor="web_port=  ", lineRange=None),
                E(evidenceId="e", filePath="credentials.json", anchor='"password"', lineRange=[3, 4]),
                E(evidenceId="f", filePath="credentials.json", anchor='"passw0rd"', lineRange=None)]
        report = ws.verify(rows)
        blob = json.dumps(report, ensure_ascii=False) + ev.compact_report(report, include_warnings=True, max_bytes=100000)
        for secret in ("abcd1234", "hunter2", "1012", "1013"):
            self.assertNotIn(secret, blob)

    def test_echo_line(self):
        self.assertEqual(ev.echo_line(".env", "A=1"), "[withheld: sensitive file]")
        self.assertEqual(ev.echo_line("src/a.py", "password = hunter2"), "[withheld: line looks sensitive]")
        self.assertEqual(ev.echo_line("src/a.py", "api_key = os.environ['K']"), "[withheld: line looks sensitive]")
        self.assertEqual(ev.echo_line("src/a.py", "   def f(x):   "), "def f(x):")
        self.assertEqual(len(ev.echo_line("src/a.py", "x" * 500)), 100)

    def test_sensitive_paths(self):
        for path in (".env", ".env.local", "deploy/.env.prod", "a/server.pem", "x/id_rsa", "id_ed25519.pub", "config/secrets.yml",
                     "credentials.json", "x/.npmrc", "ca.key", "store.jks"):
            self.assertTrue(ev.is_sensitive_path(path), path)
        for path in ("src/a.py", "environment.md", "README.md", "env.py", "keyboard.js"):
            self.assertFalse(ev.is_sensitive_path(path), path)

    def test_model_lint_covers_anchors_topics_and_props(self):
        model = {"evidences": [{"anchor": "ok anchor", "topic": "fine"}, {"anchor": 'token = "abcd1234efgh"', "topic": "t"},
                               {"anchor": "fine anchor", "topic": "uses password = 'swordfish77'"}],
                 "nodes": [{"props": {"owner": "team", "conn": "postgres://u:pw123456@h/db", "deep": {"x": ["AKIAABCDEFGHIJKLMNOP"]}}}],
                 "edges": [{"props": {"note": "fine"}}], "space": {"rooms": [{"props": {"k": "sk-" + "Zz9" * 10}}]}}
        issues = ev.secret_lint_model(model)
        paths = sorted(i["path"] for i in issues)
        self.assertEqual(paths, ["evidences[1].anchor", "evidences[2].topic", "nodes[0].props.conn",
                                 "nodes[0].props.deep.x[0]", "space.rooms[0].props.k"])
        self.assertEqual({i["rule"] for i in issues}, {"EV-006"})
        self.assertNotIn("swordfish77", json.dumps(issues))


class UserEvidenceTests(unittest.TestCase):
    BRIEF = "The borrower opens the page\nand sees the list of   available copies.\n\nA member may hold at most 3 copies."

    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.close)
        self.ws.write("atlas/user/brief.txt", self.BRIEF, where=self.ws.project)

    def user(self, anchor, **kw):
        return {"evidenceId": "U-1", "repository": "", "filePath": "user:brief", "anchor": anchor, "topic": "claim", "sourceKind": "user-requirement", **kw}

    def test_quote_found_with_whitespace_collapsed(self):
        [r] = self.ws.verify([self.user("sees the list of available copies.")])["results"]
        self.assertEqual((r["status"], r["rule"]), ("PASS", "EV-012"))
        self.assertEqual(r["resolved"]["lineRange"], [2, 2])
        multi = self.ws.verify([self.user("opens the page and sees the list")])["results"][0]
        self.assertEqual(multi["resolved"]["lineRange"], [1, 2])
        self.assertEqual(multi["stamp"]["rootRev"], ev.USER_ROOT_REV)

    def test_quote_not_found_is_a_warning(self):
        [r] = self.ws.verify([self.user("A member may hold at most 5 copies.")])["results"]
        self.assertEqual((r["status"], r["name"], r["rule"]), ("WARN", "USER_QUOTE_NOT_FOUND", "EV-012"))
        report = self.ws.verify([self.user("A member may hold at most 5 copies.")])
        self.assertEqual(report["summary"]["status"]["WARN"], 1)
        self.assertEqual(ev.update_stamp(ev.empty_stamp(), report)["entries"], {})

    def test_missing_text_is_skip(self):
        [r] = self.ws.verify([self.user("whatever quote text", filePath="user:other")])["results"]
        self.assertEqual((r["status"], r["name"]), ("SKIP", "USER_TEXT_MISSING"))
        [r] = ev.verify_evidence([self.user("whatever quote text")], {}, options=NO_REV)["results"]
        self.assertEqual((r["status"], r["name"]), ("SKIP", "USER_TEXT_MISSING"), "no project directory")

    def test_names_that_are_paths_are_failures(self):
        for name in ("../brief", "a/b", "a\\b", "x:y", "", ".hidden/x", "con"):
            [r] = self.ws.verify([self.user("quote text here", filePath=f"user:{name}")])["results"]
            self.assertIn(r["name"], ("USER_NAME_INVALID", "USER_TEXT_MISSING"), name)
            if name != "con":
                self.assertEqual(r["name"], "USER_NAME_INVALID", name)

    def test_name_with_extension_and_a_text_outside_the_project_is_not_read(self):
        self.ws.write("atlas/user/notes.md", "A quote inside the notes file.", where=self.ws.project)
        [r] = self.ws.verify([self.user("quote inside the notes", filePath="user:notes.md")])["results"]
        self.assertEqual(r["status"], "PASS")
        self.ws.write("brief.txt", "outside quote text", where=self.ws.project)
        [r] = self.ws.verify([self.user("outside quote text", filePath="user:../brief")])["results"]
        self.assertEqual(r["name"], "USER_NAME_INVALID")

    def test_user_evidence_needs_the_user_prefix_and_a_secret_quote_still_fails(self):
        [r] = self.ws.verify([self.user("the quote text", filePath="src/a.py")])["results"]
        self.assertEqual(r["name"], "USER_NAME_INVALID")
        [r] = self.ws.verify([self.user("password = 'hunter2abc'")])["results"]
        self.assertEqual(r["name"], "SECRET_IN_ANCHOR")

    def test_user_rows_mix_with_source_rows(self):
        self.ws.write("a.txt", text(30, {5: "present anchor text"}))
        report = self.ws.verify([self.user("at most 3 copies"), E(evidenceId="S", anchor="present anchor text", lineRange=None)])
        self.assertEqual([r["status"] for r in report["results"]], ["PASS", "PASS"])


class WrongLineAndReviewTests(unittest.TestCase):
    """A PASS must not hide that the cited section changed (EV-005) or that the line may be a different occurrence."""

    SOURCE = ["import os", "", "class A:", "    def process_order(self):", "        return 1", "", "    def other(self):", "        return 2"]

    def stamped(self, ws, anchor="def process_order(", lines=None):
        ws.write("a.py", "\n".join(lines or self.SOURCE))
        row = E(filePath="a.py", anchor=anchor, lineRange=None)
        return row, ev.update_stamp(ev.empty_stamp(), ws.verify([row]))

    def test_a_comment_with_the_same_text_next_to_the_real_line_is_not_a_pass(self):
        ws = Workspace()
        self.addCleanup(ws.close)
        row, stamp = self.stamped(ws)
        self.assertEqual(stamp["entries"]["E-T"]["lineRange"], [4, 4])
        ws.write("a.py", "\n".join(self.SOURCE[:3] + ["    # def process_order( was renamed"] + self.SOURCE[3:]))
        report = ws.verify([row], stamp=stamp)
        [r] = report["results"]
        self.assertEqual((r["status"], r["name"], r["rule"]), ("WARN", "RANGE_CONTENT_CHANGED", "EV-005"))
        self.assertFalse(r["stampable"])
        self.assertEqual(r["repair"]["anchorLines"], [5], "the real line is suggested")
        self.assertIn("WARN E-T EV-005", ev.compact_report(report))
        self.assertEqual(ev.update_stamp(stamp, report)["entries"], stamp["entries"], "never refreshed by itself")

    def test_a_third_occurrence_sliding_into_the_stamped_place_is_not_a_pass(self):
        lines = text(60, {10: "needle", 30: "needle", 50: "needle"}).split("\n")
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        del lines[29]                 # the pinned one is deleted; the occurrence at 50 is now at 49, 10 stays
        lines.insert(28, "needle")    # and a new identical line lands next to the stamped place
        ws.write("a.txt", "\n".join(lines))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertNotEqual(r["status"], "PASS", r)

    def test_a_look_alike_that_slid_into_the_stamped_place_is_not_a_pass(self):
        base = {30: "needle", 35: "needle"}
        lines = text(60, base).split("\n")
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        for key in ("beforeSha256", "afterSha256", "lineSha256"):
            self.assertIn(key, stamp["entries"]["E-T"])
        moved = lines[:9] + lines[14:]            # five lines above the stamped one are deleted: it is now at 25, the other at 30
        moved[moved.index("line 29")] = "line 29 edited"   # and its context is touched, so no fingerprint finds it
        ws.write("a.txt", "\n".join(moved))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual(moved.count("needle"), 2, "the number of occurrences did not change")
        self.assertEqual((r["status"], r["name"]), ("WARN", "RANGE_CONTENT_CHANGED"), r)
        self.assertEqual(r["resolved"]["lineRange"], [30, 30])
        self.assertFalse(r["stampable"])

    def test_one_intact_side_of_the_context_keeps_a_pinned_anchor_a_pass(self):
        base = {20: "needle", 30: "needle"}
        lines = text(60, base).split("\n")
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        for edited in ("line 31", "line 28"):         # one side touched: the other still vouches for the place
            changed = [("edited " + v) if v == edited else v for v in lines]
            ws.write("a.txt", "\n".join(changed))
            [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
            self.assertEqual((r["status"], r["resolved"]["lineRange"]), ("PASS", [30, 30]), edited)
            self.assertIn("RANGE_CONTENT_CHANGED", names(r))
        both = [("edited " + v) if v in ("line 28", "line 31") else v for v in lines]
        ws.write("a.txt", "\n".join(both))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual(r["status"], "WARN", "both sides replaced: nothing vouches for the place")

    def test_a_whole_file_reindent_keeps_a_pinned_anchor_a_pass(self):
        lines = text(40, {10: "needle", 20: "needle"}).split("\n")
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[20, 20])]))
        ws.write("a.txt", "\n".join("    " + v for v in lines))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["resolved"]["lineRange"]), ("PASS", [20, 20]))

    def test_compact_report_bounds_the_review_lines(self):
        file_a = text(200, {i: f"def target_{i}():" for i in range(10, 190, 10)})
        ws = Workspace({"a.py": file_a})
        self.addCleanup(ws.close)
        rows = [E(evidenceId=f"R{i}", filePath="a.py", anchor=f"def target_{i}():", lineRange=None) for i in range(10, 190, 10)]
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify(rows))
        ws.write("a.py", "\n".join(("  " + v) for v in file_a.split("\n")))      # every row now "PASS, review"
        report = ws.verify(rows, stamp=stamp)
        out = ev.compact_report(report, max_bytes=100000)
        self.assertEqual(out.count("RANGE_CONTENT_CHANGED (review)"), ev.MAX_REVIEW_LINES)
        self.assertIn(f"NOTE EV-005 {len(rows) - ev.MAX_REVIEW_LINES} more PASS rows", out)

    def test_neighbour_edit_of_a_pinned_ambiguous_anchor_is_listed_for_review(self):
        file_a = text(40, {10: "needle", 20: "needle"})
        ws = Workspace({"a.txt": file_a})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[20, 20])]))
        ws.write("a.txt", file_a.replace("line 19", "line nineteen, edited"))
        report = ws.verify([E(lineRange=None)], stamp=stamp)
        self.assertEqual(report["results"][0]["status"], "PASS")
        self.assertIn("PASS E-T EV-003 RANGE_CONTENT_CHANGED (review)", ev.compact_report(report))
        self.assertIn("NOTE EV-003 1 PASS rows rest on a stamp that pins one of several identical anchors", ev.compact_report(report))

    def test_unique_anchor_with_an_edited_neighbour_raises_ev005(self):
        file_a = text(30, {16: "def target_function():"})
        ws = Workspace({"a.py": file_a})
        self.addCleanup(ws.close)
        row = E(filePath="a.py", anchor="def target_function():", lineRange=None)
        report = ws.verify([row])
        stamp = ev.update_stamp(ev.empty_stamp(), report)
        self.assertEqual(ws.verify([row], stamp=stamp)["results"][0]["warnings"], [], "untouched: silent")
        ws.write("a.py", file_a.replace("line 17", "rewritten 17").replace("line 18", "rewritten 18"))
        report = ws.verify([row], stamp=stamp)
        [r] = report["results"]
        self.assertEqual(r["status"], "PASS", "the anchor still exists exactly once")
        self.assertIn("RANGE_CONTENT_CHANGED", names(r))
        self.assertFalse(r["stampable"])
        self.assertEqual(ev.update_stamp(stamp, report), stamp, "update_stamp does not refresh without accept_changes")
        self.assertIn("RANGE_CONTENT_CHANGED (review)", ev.compact_report(report), "an all-green line would hide it")
        accepted = ev.update_stamp(stamp, report, accept_changes=True)
        self.assertNotEqual(accepted["entries"]["E-T"]["contextSha256"], stamp["entries"]["E-T"]["contextSha256"])
        self.assertEqual(ws.verify([row], stamp=accepted)["results"][0]["warnings"], [])

    def test_unique_anchor_moved_by_an_insertion_far_above_stays_silent_apart_from_the_move(self):
        file_a = text(40, {20: "def target_function():"})
        ws = Workspace({"a.py": file_a})
        self.addCleanup(ws.close)
        row = E(filePath="a.py", anchor="def target_function():", lineRange=None)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([row]))
        ws.write("a.py", "# top\n# top\n" + file_a)
        [r] = ws.verify([row], stamp=stamp)["results"]
        self.assertEqual((r["status"], names(r), r["stampable"]), ("PASS", ["ANCHOR_MOVED"], True))

    def test_the_stamp_records_whether_its_context_block_is_unique(self):
        block = ["a1", "a2", "a3", "needle", "b1", "b2", "b3"]
        lines = [f"f{i}" for i in range(9)] + block + [f"g{i}" for i in range(13)] + block + [f"h{i}" for i in range(9)]
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        pinned = ws.verify([E(lineRange=[33, 33])])["results"][0]
        self.assertEqual(pinned["stamp"]["contextCount"], 2)
        ws.write("a.txt", "\n".join(lines).replace("\nf3\n", "\nunique filler line\n"))
        unique = ws.verify([E(anchor="unique filler line", lineRange=None, evidenceId="U")])["results"][0]
        self.assertEqual(unique["stamp"]["contextCount"], 1)

    def test_an_identical_twin_block_caps_the_confidence_of_a_fingerprint_suggestion(self):
        block = ["a1", "a2", "a3", "needle", "b1", "b2", "b3"]
        lines = [f"f{i}" for i in range(9)] + block + [f"g{i}" for i in range(13)] + block + [f"h{i}" for i in range(9)]
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[33, 33])]))
        # the real block moves down 20 lines and one line of its context is edited; the twin at 13 is untouched
        moved = lines[:29] + [f"m{i}" for i in range(20)] + lines[29:]
        moved[moved.index("b3", 40)] = "b3 edited"
        ws.write("a.txt", "\n".join(moved))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "ANCHOR_DRIFT"))
        self.assertEqual(r["repair"]["anchorLines"], [13], "the twin is what the fingerprint finds")
        self.assertLessEqual(r["repair"]["confidence"], 0.6)
        self.assertTrue(r["repair"]["needsReview"])
        self.assertFalse(r["repair"]["exact"])

    def test_the_line_rung_is_a_guess_when_the_stamped_line_was_repeated(self):
        lines = [f"line {i}" for i in range(1, 70)]
        for number, label in ((10, "A"), (20, "B"), (30, "C"), (40, "D"), (50, "E")):
            lines[number - 1] = f"needle {label}"
        ws = Workspace({"a.txt": "\n".join(lines)})
        self.addCleanup(ws.close)
        stamp = ev.update_stamp(ev.empty_stamp(), ws.verify([E(lineRange=[30, 30])]))
        self.assertEqual(stamp["entries"]["E-T"]["occurrences"], 5)
        shifted = [f"# top {i}" for i in range(8)] + lines
        for index, value in enumerate(shifted):
            if value in ("line 29", "line 31"):
                shifted[index] = value + " edited"          # the stamped context no longer exists anywhere
        ws.write("a.txt", "\n".join(shifted))
        [r] = ws.verify([E(lineRange=None)], stamp=stamp)["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "ANCHOR_DRIFT"))
        self.assertEqual((r["repair"]["method"], r["repair"]["anchorLines"]), ("line", [38]))
        self.assertLess(r["repair"]["confidence"], 0.8)
        self.assertTrue(r["repair"]["needsReview"])


class ShortAnchorTests(unittest.TestCase):
    def test_a_one_character_anchor_is_never_a_pass_and_never_stamped(self):
        ws = Workspace({"a.py": "\n".join(f"alpha beta gamma {i}" for i in range(60)) + "\n}\nab\n"})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="S1", filePath="a.py", anchor="a", lineRange=[1, 3]), E(evidenceId="S2", filePath="a.py", anchor="}", lineRange=[61, 61]),
                E(evidenceId="S3", filePath="a.py", anchor="ab", lineRange=None)]
        report = ws.verify(rows)
        for r in report["results"]:
            self.assertEqual((r["status"], r["name"]), ("WARN", "ANCHOR_SHORT"), r["evidenceId"])
            self.assertFalse(r["stampable"])
        self.assertEqual(ev.update_stamp(ev.empty_stamp(), report)["entries"], {})
        self.assertEqual(report["summary"]["status"]["PASS"], 0)
        claim = ev.check_claim({"id": "n", "verification": "Implemented", "evidenceIds": ["S3"]},
                               {"S3": {"sourceKind": "source-code"}}, {"S3": report["results"][2]})
        self.assertEqual(claim["status"], "FAIL", "the release gate counts only PASS evidence")

    def test_a_short_quote_of_user_text_is_not_a_pass_either(self):
        ws = Workspace()
        self.addCleanup(ws.close)
        ws.write("atlas/user/brief.txt", "A short brief with the words ok in it.\n", where=ws.project)
        [r] = ws.verify([E(filePath="user:brief", sourceKind="user-requirement", anchor="ok", lineRange=None)])["results"]
        self.assertEqual((r["status"], r["name"]), ("WARN", "ANCHOR_SHORT"))


class RowShapeTests(unittest.TestCase):
    def test_an_unhashable_or_blank_id_is_reported_not_raised(self):
        ws = Workspace({"a.py": text(10, {3: "unique content row 3"})})
        self.addCleanup(ws.close)
        good = E(evidenceId="OK", filePath="a.py", anchor="unique content row 3", lineRange=None)
        rows = [{**good, "evidenceId": ["x"]}, {**good, "evidenceId": {"k": 1}}, {**good, "evidenceId": "  "}, {**good, "evidenceId": None},
                {**good, "evidenceId": 7}, good]
        report = ws.verify(rows)
        self.assertEqual([r["status"] for r in report["results"]], ["FAIL"] * 5 + ["PASS"])
        self.assertTrue(all(r["name"] == "SCHEMA" for r in report["results"][:5]))
        self.assertEqual([r["evidenceId"] for r in report["results"][:2]], ["<row 1>", "<row 2>"])
        self.assertEqual(list(ev.update_stamp(ev.empty_stamp(), report)["entries"]), ["OK"])
        self.assertIn("SCHEMA", ev.compact_report(report))

    def test_a_duplicate_evidence_id_fails_the_second_row_and_the_stamp_keeps_the_first(self):
        ws = Workspace({"a.py": text(50, {3: "unique content row 3"}), "b.py": text(50, {40: "unique content row 40"})})
        self.addCleanup(ws.close)
        rows = [E(evidenceId="D1", filePath="a.py", anchor="unique content row 3", lineRange=None),
                E(evidenceId="D1", filePath="b.py", anchor="unique content row 40", lineRange=None)]
        report = ws.verify(rows)
        self.assertEqual([r["status"] for r in report["results"]], ["PASS", "FAIL"])
        self.assertEqual(report["results"][1]["name"], "SCHEMA")
        self.assertIn("already used", report["results"][1]["message"])
        entries = ev.update_stamp(ev.empty_stamp(), report)["entries"]
        self.assertEqual(entries["D1"]["filePath"], "a.py")

    def test_topic_is_required_because_it_is_the_claim_text(self):
        ws = Workspace({"a.py": text(10, {3: "unique content row 3"})})
        self.addCleanup(ws.close)
        base = E(filePath="a.py", anchor="unique content row 3", lineRange=None)
        missing = {k: v for k, v in base.items() if k != "topic"}
        results = ws.verify([{**missing, "evidenceId": "A"}, {**base, "evidenceId": "B", "topic": ""}, {**base, "evidenceId": "C", "topic": "  "},
                             {**base, "evidenceId": "D", "topic": "what it proves"}])["results"]
        self.assertEqual([r["status"] for r in results], ["FAIL", "FAIL", "FAIL", "PASS"])
        self.assertIn("topic", results[0]["message"])

    def test_nul_byte_after_the_first_8_kib_is_binary(self):
        ws = Workspace({"a.bin.txt": b"x" * 9000 + b"\0 anchorx more\n"})
        self.addCleanup(ws.close)
        [r] = ws.verify([E(filePath="a.bin.txt", anchor="anchorx more", lineRange=None)])["results"]
        self.assertEqual((r["status"], r["name"]), ("FAIL", "NOT_TEXT"))


class CredentialEchoTests(unittest.TestCase):
    """What leaves the verifier is filtered line by line AND block by block; bare tokens are credentials too."""

    PEM = ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
           "MzEfYyjiWA4R4/M2bS1GB4t7NXp98C3SC6dVMvDuictGeurT8jNbvJZHtCSuYEvu\n-----END PRIVATE KEY-----")
    WITHHELD = [
        "Authorization: Bearer abcdef0123456789abcdef", "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIB", "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
        "passphrase = 'open sesame door'", "passphrase: open sesame door", "AC" + "0123456789abcdef" * 2, "sk_" + "live_4eC39HqLyjWDarjtT1zdp7dc",
        "AIzaSyA-1234567890abcdefghijklmnopqrstuv", "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX", "npm_" + "a1B2" * 9,
        "abcdef0123456789abcdef", "password = correcthorsebattery", "auth  =  'Bearer abcdef0123456789abcdef'",
    ]
    ORDINARY = [
        "def process_order_with_retries_v2(self, order_id):", "from gemarc_mcp.atlas.vocab import default_profile",
        "    # see docs/atlas/spec/model-schema.md section 4.9 for details", "return self._build_node_with_ports(node_id, ports)",
        "path = 'src/gemarc_mcp/atlas/engine/sim_v2/fixtures/order_flow_case_01.json'", "basic configuration is required here",
        "class AtlasModelContractIssueFormatterWithDigits2(Base):", "https://example.com/api/v1/orders/get_order_status_by_id_123",
    ]
    #: not credentials for the lint (placeholders, types, calls) although ``echo_line`` withholds any assignment to a credential-like key
    ASSIGNMENTS = ["token: str", "password: Optional[str] = None", "api_key = os.environ['SERVICE_KEY']", "secret = get_secret_value()"]

    def test_every_listed_credential_shape_is_withheld_and_ordinary_code_is_not(self):
        for line in self.WITHHELD:
            self.assertTrue(ev.echo_line("src/a.py", line).startswith("[withheld"), line)
        for line in self.ORDINARY:
            self.assertFalse(ev.echo_line("src/a.py", line).startswith("[withheld"), line)

    def test_the_lint_flags_bare_tokens_and_unquoted_words_after_a_credential_key(self):
        for line in self.WITHHELD:
            self.assertTrue(ev.secret_findings(line) or ev._ASSIGN_RE.search(line), line)
        for line in self.ORDINARY + self.ASSIGNMENTS:
            self.assertEqual(ev.secret_findings(line), [], line)
        self.assertEqual(ev.secret_findings("password = correcthorsebattery"), ["keyword-assignment"])

    def test_auth_scheme_credentials_are_found_even_when_short(self):
        for line in ("Authorization: Basic dXNlcjpwYXNz", "headers = {'x': 'Bearer tok3nValue9876'}", "bearer a1b2c3d4e5f6g7"):
            self.assertIn("auth-header-credential", ev.secret_findings(line), line)
        for line in ("basic configuration is required here", "Bearer tokens are documented elsewhere", "Bearer ${TOKEN_VALUE_HERE}"):
            self.assertNotIn("auth-header-credential", ev.secret_findings(line), line)

    def test_a_private_key_block_inside_a_string_is_withheld_line_by_line(self):
        src = ev.decode_text(("KEY = \"\"\"\n" + self.PEM + "\n\"\"\"\nprint(1)\n").encode())
        self.assertEqual(sorted(src.private_key_lines()), [2, 3, 4, 5])
        for number in (2, 3, 4, 5):
            self.assertTrue(ev.echo_source_line("cert.py", src, number).startswith("[withheld"), number)
        self.assertEqual(ev.echo_source_line("cert.py", src, 1), 'KEY = """')
        self.assertEqual(ev.echo_source_line("cert.py", src, 7), "print(1)")

    def test_a_bogus_anchor_with_a_cited_range_reads_nothing_of_a_key_block_or_its_neighbours(self):
        ws = Workspace({"cert.py": 'KEY = """\n' + self.PEM + '\n"""\nprint(1)\n', "conf.yaml": "a: 1\nb: 2\npassphrase: open sesame door\nc: 3\n"})
        self.addCleanup(ws.close)
        for number in range(1, 8):
            report = ws.verify([E(filePath="cert.py", anchor="wrong anchor text", lineRange=[number, number])])
            blob = json.dumps(report) + ev.compact_report(report, include_warnings=True, max_bytes=100000)
            for secret in ("MIIEvQ", "MzEfYy"):
                self.assertNotIn(secret, blob)
            self.assertEqual(report["results"][0]["repair"]["context"], [], "a row's own lineRange never selects echoed context")
        stamp = {"schema": ev.STAMP_SCHEMA, "entries": {"E-T": {
            "repository": "r", "filePath": "conf.yaml", "lineRange": [3, 3], "anchorSha256": ev._h16("wrong anchor text"),
            "rangeSha256": "x", "context": [1, 1], "contextSha256": "y", "occurrences": 1}}}
        report = ws.verify([E(filePath="conf.yaml", anchor="wrong anchor text", lineRange=None)], stamp=stamp)
        self.assertNotIn("sesame", json.dumps(report))
        self.assertEqual(report["results"][0]["repair"]["context"], [], "config files get no neighbour echo at all")

    def test_context_echo_is_one_line_from_code_and_prose_files_only(self):
        self.assertTrue(all(ev.context_echo_allowed(p) for p in ("a/b.py", "x.TS", "docs/readme.md", "notes.txt")))
        self.assertFalse(any(ev.context_echo_allowed(p) for p in ("a.yaml", "a.json", "a.toml", "a.ini", "a.conf", ".env", "x.pem", "secrets.py", "noext")))

    def test_credential_looking_anchors_fail_before_the_file_is_read(self):
        ws = Workspace({"auth.py": "x = 1\nauth  =  'Bearer abcdef0123456789abcdef'\ny = 2\n"})
        self.addCleanup(ws.close)
        anchors = ["auth  =  'Bearer abcdef0123456789abcdef'", "password = correcthorsebattery", "MIIEvQIBADANBgkqhkiG9w0B",
                   "abcdef0123456789abcdef", "passphrase = 'open sesame door'"]
        results = ws.verify([E(evidenceId=f"S{i}", filePath="auth.py", anchor=a, lineRange=None) for i, a in enumerate(anchors)])["results"]
        self.assertEqual([r["name"] for r in results], ["SECRET_IN_ANCHOR"] * len(anchors))
        blob = json.dumps(results)
        for secret in ("abcdef0123", "correcthorse", "MIIEvQ", "sesame"):
            self.assertNotIn(secret, blob)

    def test_hardened_candidates_never_contain_a_token(self):
        token = "abcdef0123456789abcdef0123"
        ws = Workspace({"a.py": f"x = 1\nuse_id = call(1)  # {token}\nuse_id = call(2)\n"})
        self.addCleanup(ws.close)
        [r] = ws.verify([E(filePath="a.py", anchor="use_id", lineRange=None)])["results"]
        self.assertEqual(r["name"], "ANCHOR_AMBIGUOUS")
        self.assertNotIn(token, json.dumps(r))


class RepoCorpusTests(unittest.TestCase):
    """The verifier and the outline on this repository's own src/gemarc_mcp (real Python, JS, Markdown, JSON, CRLF files)."""

    def test_verifier_passes_its_own_outline_anchors_and_catches_planted_failures(self):
        stats = repo_run()
        self.assertGreater(stats["files"], 50)
        self.assertGreater(stats["rows"], 1000)
        self.assertEqual(stats["valid_rows_not_pass"], 0, stats["examples"])
        self.assertEqual(stats["valid_wrong_line"], 0, stats["examples"])
        self.assertEqual(stats["planted_not_failed"], 0)
        self.assertEqual(stats["planted"], 5 * stats["files_with_rows"])


def repo_files() -> list[Path]:
    out = []
    for path in sorted(CORPUS_ROOT.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and ol.language_of(path.name):
            out.append(path)
    return out


def repo_run(per_file: int = 400) -> dict:
    """Synthetic evidence rows over src/gemarc_mcp: every outline anchor (must PASS at its entry line) plus five planted
    failures per file (missing file, missing anchor, ambiguous anchor, blank anchor, credential-looking anchor)."""
    rows, expected, planted = [], {}, []
    files_with_rows = 0
    for path in repo_files():
        rel = path.relative_to(CORPUS_ROOT).as_posix()
        try:
            src = ev.decode_text(path.read_bytes())
        except ev.TextError:
            continue
        result = ol.outline(src, rel)
        entries = [e for e in result["entries"] if e["anchor"] is not None][:per_file]
        if not entries:
            continue
        files_with_rows += 1
        for index, entry in enumerate(entries):
            eid = f"V-{len(rows)}"
            rows.append({"evidenceId": eid, "repository": "src", "filePath": rel, "anchor": entry["anchor"], "topic": "synthetic"})
            expected[eid] = entry["line"]
        base = {"repository": "src", "topic": "synthetic"}
        plants = [
            {**base, "filePath": rel + ".gone", "anchor": entries[0]["anchor"]},
            {**base, "filePath": rel, "anchor": "zzz-not-in-this-file-" + str(files_with_rows)},
            {**base, "filePath": rel, "anchor": ""},
            {**base, "filePath": rel, "anchor": "AKIAABCDEFGHIJKLMNOP"},
            {**base, "filePath": rel, "anchor": "\n"},
        ]
        for index, plant in enumerate(plants):
            plant["evidenceId"] = f"P-{files_with_rows}-{index}"
            rows.append(plant)
            planted.append(plant["evidenceId"])
    report = ev.verify_evidence(rows, {"src": CORPUS_ROOT}, options=NO_REV)
    by_id = {r["evidenceId"]: r for r in report["results"]}
    bad, wrong, examples = 0, 0, []
    for eid, line in expected.items():
        r = by_id[eid]
        if r["status"] != "PASS":
            bad += 1
            examples.append((eid, r["name"]))
        elif r["resolved"]["lineRange"][0] != line:
            wrong += 1
            examples.append((eid, r["resolved"]["lineRange"], line))
    not_failed = [p for p in planted if by_id[p]["status"] != "FAIL"]
    return {"files": len(repo_files()), "files_with_rows": files_with_rows, "rows": len(rows), "valid": len(expected),
            "planted": len(planted), "summary": report["summary"]["status"], "valid_rows_not_pass": bad, "valid_wrong_line": wrong,
            "planted_not_failed": len(not_failed), "examples": examples[:10]}


# ---------------------------------------------------------------------------------------------------------
# Seeded insertion-drift experiment (stop/go of M7: below 97% exact line the verifier ships report-only)
# ---------------------------------------------------------------------------------------------------------


def drift_corpus() -> dict[str, str]:
    """Text of the tracked Python, JS and Markdown files under src/gemarc_mcp (CRLF folded), size below 300 KB."""
    try:
        listed = subprocess.run(["git", "ls-files", "src/gemarc_mcp"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
        paths = [REPO / p for p in listed]
    except (OSError, subprocess.SubprocessError):
        paths = sorted(CORPUS_ROOT.rglob("*"))
    corpus = {}
    for path in paths:
        if path.suffix in (".py", ".js", ".mjs", ".md") and path.is_file() and path.stat().st_size < 300000 and "__pycache__" not in path.parts:
            try:
                corpus[path.relative_to(REPO).as_posix()] = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
            except UnicodeDecodeError:
                continue
    return corpus


def drift_rows(corpus: dict[str, str], seed: int, per_file: int, ambiguous_share: float) -> list[dict]:
    """Anchors a careless author might write: prefixes of random lines, plus (with ``ambiguous_share``) whole lines that
    occur several times in their file (``return None``, ``else:``...). Each row cites its own line."""
    rnd = random.Random(seed)
    rows = []
    for rel, body in sorted(corpus.items()):
        stripped = [line.strip() for line in body.split("\n")]
        counts: dict[str, int] = {}
        for line in stripped:
            counts[line] = counts.get(line, 0) + 1
        usable = [i for i, s in enumerate(stripped) if len(s) >= 10 and not s.startswith("#")]
        repeated = [i for i in usable if counts[stripped[i]] >= 2 and len("".join(stripped[i].split())) >= 6]
        for _ in range(min(per_file, len(usable))):
            if repeated and rnd.random() < ambiguous_share:
                index = rnd.choice(repeated)
                anchor = stripped[index]
            else:
                index = rnd.choice(usable)
                anchor = stripped[index][:rnd.randint(8, 28)].rstrip()
                if len("".join(anchor.split())) < 6:
                    anchor = stripped[index]
            rows.append({"evidenceId": f"E{len(rows)}", "repository": "r", "filePath": rel, "anchor": anchor, "topic": "t",
                         "lineRange": [index + 1, index + 1]})
    return rows


DRIFT_VARIANTS = {
    "nearest-only": ev.VerifyOptions(use_fingerprint=False, use_consensus=False, compute_root_rev=False),
    "+shift consensus": ev.VerifyOptions(use_fingerprint=False, use_consensus=True, compute_root_rev=False),
    "+stored fingerprint": ev.VerifyOptions(compute_root_rev=False),
}


def drift_trial(corpus: dict[str, str], seed: int, k: int, ambiguous_share: float, per_file: int = 12) -> dict:
    """One seed at one k: stamp pristine rows, insert k lines at a seeded position of every file, re-verify."""
    rows = drift_rows(corpus, seed, per_file, ambiguous_share)
    base = Path(tempfile.mkdtemp())
    try:
        for rel, body in corpus.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))
        first = ev.verify_evidence(rows, {"r": base}, options=NO_REV)
        stamp = ev.update_stamp(ev.empty_stamp(), first)
        usable = [r for r, res in zip(rows, first["results"]) if res["status"] == "PASS"]
        occurrences = {res["evidenceId"]: res["resolved"]["occurrences"] for res in first["results"] if res["status"] == "PASS"}
        free = [{key: value for key, value in r.items() if key != "lineRange"} for r in usable]
        rnd = random.Random(seed * 1000 + k)
        position = {}
        for rel, body in corpus.items():
            lines = body.split("\n")
            position[rel] = rnd.randint(1, len(lines))
            inserted = [f"# drift {i}" for i in range(k)]
            (base / rel).write_bytes("\n".join(lines[:position[rel] - 1] + inserted + lines[position[rel] - 1:]).encode("utf-8"))
        out = {}
        for name, options in DRIFT_VARIANTS.items():
            report = ev.verify_evidence(free, {"r": base}, stamp=stamp, options=options)
            stat = dict(affected=0, exact=0, unique=0, unique_exact=0, ambiguous=0, ambiguous_exact=0, silent_wrong=0,
                        confident_wrong=0, control=0, control_wrong=0)
            for row, res in zip(usable, report["results"]):
                line = row["lineRange"][0]
                if position[row["filePath"]] > line:  # insertion below the anchor: nothing moved, must still pass in place
                    stat["control"] += 1
                    stat["control_wrong"] += int(res["status"] != "PASS" or res["resolved"]["lineRange"][0] != line)
                    continue
                expected = line + k
                ambiguous = occurrences[row["evidenceId"]] > 1
                stat["affected"] += 1
                stat["ambiguous" if ambiguous else "unique"] += 1
                if res["status"] == "PASS":
                    got, confidence = res["resolved"]["lineRange"][0], 1.0
                elif res["name"] == "ANCHOR_DRIFT":
                    got, confidence = res["repair"]["anchorLines"][0], res["repair"]["confidence"]
                else:
                    got, confidence = None, 0.0
                if got == expected:
                    stat["exact"] += 1
                    stat["ambiguous_exact" if ambiguous else "unique_exact"] += 1
                elif res["status"] == "PASS":
                    stat["silent_wrong"] += 1
                elif confidence >= 0.8:
                    stat["confident_wrong"] += 1
            out[name] = stat
        return {"variants": out, "rows": len(usable), "ambiguous_rows": sum(1 for v in occurrences.values() if v > 1),
                "unstamped": len(rows) - len(usable)}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def drift_experiment(seeds: int, ks=(1, 3, 10), ambiguous_share: float = 0.5, per_file: int = 12) -> dict:
    corpus = drift_corpus()
    table = {}
    for k in ks:
        merged: dict[str, dict[str, int]] = {}
        rows = ambiguous_rows = unstamped = 0
        for seed in range(seeds):
            trial = drift_trial(corpus, seed, k, ambiguous_share, per_file)
            rows += trial["rows"]
            ambiguous_rows += trial["ambiguous_rows"]
            unstamped += trial["unstamped"]
            for name, stat in trial["variants"].items():
                into = merged.setdefault(name, {})
                for key, value in stat.items():
                    into[key] = into.get(key, 0) + value
        table[k] = {"variants": merged, "rows": rows, "ambiguous_rows": ambiguous_rows, "unstamped": unstamped}
    return {"files": len(corpus), "table": table, "ambiguous_share": ambiguous_share, "seeds": seeds}


def format_drift(result: dict) -> str:
    lines = [f"files {result['files']}, seeds {result['seeds']}, ambiguous-line share {result['ambiguous_share']}"]
    for k, entry in result["table"].items():
        lines.append(f"k={k}: rows stamped {entry['rows']} (ambiguous {entry['ambiguous_rows']}), not stampable {entry['unstamped']}")
        for name, s in entry["variants"].items():
            pct = 100.0 * s["exact"] / max(1, s["affected"])
            lines.append(f"  {name:20s} affected {s['affected']:5d} exact {s['exact']:5d} ({pct:6.2f}%)  unique {s['unique_exact']}/{s['unique']}  "
                         f"ambiguous {s['ambiguous_exact']}/{s['ambiguous']}  silent-wrong {s['silent_wrong']}  confident-wrong {s['confident_wrong']}  "
                         f"| control {s['control']} wrong {s['control_wrong']}")
    return "\n".join(lines)


class DriftExperimentTests(unittest.TestCase):
    """Acceptance (M7): at most 10 inserted lines the full pipeline names the exact moved line in at least 99% of the
    affected trials; the weaker ladders are measured to show what each rung buys."""

    @classmethod
    def setUpClass(cls):
        cls.result = drift_experiment(seeds=2, ks=(1, 3, 10), ambiguous_share=0.5, per_file=10)

    def test_full_pipeline_meets_the_stop_go_threshold(self):
        affected = exact = silent = confident = control_wrong = 0
        for entry in self.result["table"].values():
            stat = entry["variants"]["+stored fingerprint"]
            affected += stat["affected"]
            exact += stat["exact"]
            silent += stat["silent_wrong"]
            confident += stat["confident_wrong"]
            control_wrong += stat["control_wrong"]
        self.assertGreater(affected, 1500, "the experiment must actually exercise drift")
        self.assertGreaterEqual(exact / affected, 0.99, format_drift(self.result))
        self.assertLessEqual(confident, 2, "a confident (>= 0.8) suggestion that is wrong")
        self.assertLessEqual(control_wrong, 2, "rows whose anchor did not move must keep passing in place")

    def test_each_rung_helps_and_unique_anchors_are_always_exact(self):
        for k, entry in self.result["table"].items():
            near, vote, full = (entry["variants"][n] for n in ("nearest-only", "+shift consensus", "+stored fingerprint"))
            self.assertGreaterEqual(vote["exact"], near["exact"], k)
            self.assertGreaterEqual(full["exact"], vote["exact"], k)
            for stat in (near, vote, full):
                self.assertEqual(stat["unique_exact"], stat["unique"], (k, "a unique anchor is resolved, not guessed"))
        self.assertGreater(self.result["table"][10]["ambiguous_rows"], 100, "the corpus must contain many ambiguous anchors")

    def test_without_fingerprint_a_stale_pass_exists_and_with_it_it_does_not_at_ten_lines(self):
        entry = self.result["table"][10]["variants"]
        self.assertGreater(entry["nearest-only"]["silent_wrong"], 0, "the problem the fingerprint solves must be real in the data")
        self.assertLessEqual(entry["+stored fingerprint"]["silent_wrong"], 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "drift":
        arguments = sys.argv[2:]
        seeds = int(arguments[arguments.index("--seeds") + 1]) if "--seeds" in arguments else 8
        mix = float(arguments[arguments.index("--mix") + 1]) if "--mix" in arguments else 0.5
        print(format_drift(drift_experiment(seeds=seeds, ambiguous_share=mix)))
    elif len(sys.argv) > 1 and sys.argv[1] == "repo":
        print(json.dumps(repo_run(), indent=1, default=str))
    else:
        unittest.main()
