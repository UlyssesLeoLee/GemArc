"""A source or user text over the size limit is refused from its size, never read (contract-requests item 38; debt-b4, item E).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_size_before_read.py"

``outline.outline_file`` and the evidence verifier (repository files and the saved user texts under ``atlas/user/``) call
``evidence.read_text_file``: ``os.stat`` first, then at most ``max_bytes + 1`` bytes. The oversized file here is one byte over the limit, or
far over it; the tests mock the readers and assert they are NEVER called for it, measure the memory, and prove the boundary (exactly the
limit is read, one byte more is refused). Synthetic content only.
"""
from __future__ import annotations

import builtins
import os
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import outline as ol
from gemarc_mcp.atlas import review as rv
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools

from .test_oracle_support import OracleProject, desk_files

OVER = ev.MAX_FILE_BYTES + 1
PYTHON = "def create_loan(member, copy):\n    return True\n"


def make_big(path: Path, size: int) -> None:
    """A file of ``size`` bytes made by seeking (sparse where the file system supports it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\n")


class Reads:
    """Context manager: every attempt to read ``watched`` through ``Path.read_bytes``, ``open`` or ``io.open`` is recorded (and still works)."""

    def __init__(self, watched: Path):
        self.watched = os.path.normcase(str(watched))
        self.calls: list = []

    def _note(self, label, path):
        try:
            if os.path.normcase(str(path)) == self.watched:
                self.calls.append(label)
        except (TypeError, ValueError):
            pass

    def __enter__(self):
        real_read_bytes, real_open = Path.read_bytes, builtins.open
        outer = self

        def read_bytes(path_self):
            outer._note("Path.read_bytes", path_self)
            return real_read_bytes(path_self)

        def opening(file, *args, **kwargs):
            outer._note("open", file)
            return real_open(file, *args, **kwargs)

        self._patches = [mock.patch.object(Path, "read_bytes", read_bytes), mock.patch.object(builtins, "open", opening)]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()


class ReadTextFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_file_over_the_limit_raises_before_any_read(self):
        big = self.dir / "big.txt"
        make_big(big, OVER)
        with Reads(big) as reads:
            with self.assertRaises(ev.TextError) as caught:
                ev.read_text_file(big)
        self.assertEqual(reads.calls, [], "neither Path.read_bytes nor open was called")
        self.assertIn(f"larger than {ev.MAX_FILE_BYTES} bytes", str(caught.exception))

    def test_the_limit_is_exact(self):
        exact = self.dir / "exact.txt"
        exact.write_bytes(b"a" * 1000)
        self.assertEqual(len(ev.read_text_file(exact, max_bytes=1000).text), 1000)
        with self.assertRaises(ev.TextError):
            ev.read_text_file(exact, max_bytes=999)

    def test_a_file_that_became_larger_since_the_stat_is_cut_and_refused(self):
        grown = self.dir / "grown.txt"
        grown.write_bytes(b"x" * 5000)
        real_stat = os.stat
        small = mock.Mock(st_size=10)
        with mock.patch.object(os, "stat", side_effect=lambda p, *a, **k: small if os.path.normcase(str(p)) == os.path.normcase(str(grown)) else real_stat(p, *a, **k)):
            with self.assertRaises(ev.TextError):
                ev.read_text_file(grown, max_bytes=1000)

    def test_binary_and_bad_utf8_and_missing_files_keep_their_errors(self):
        binary = self.dir / "b.bin"
        binary.write_bytes(b"\x00\x01")
        with self.assertRaisesRegex(ev.TextError, "binary"):
            ev.read_text_file(binary)
        bad = self.dir / "bad.txt"
        bad.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ev.TextError, "UTF-8"):
            ev.read_text_file(bad)
        with self.assertRaises(OSError):
            ev.read_text_file(self.dir / "absent.txt")

    def test_the_memory_is_not_the_size_of_the_file(self):
        big = self.dir / "huge.txt"
        make_big(big, 64 * 1024 * 1024)
        tracemalloc.start()
        try:
            with self.assertRaises(ev.TextError):
                ev.read_text_file(big)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024, f"peak {peak} bytes for a 64 MiB file")


class OutlineFileSizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "m.py").write_text(PYTHON, encoding="utf-8")
        self.roots = {"r": self.root}

    def test_an_oversized_source_is_refused_with_not_text_and_is_never_read(self):
        big = self.root / "src" / "big.py"
        make_big(big, OVER)
        with Reads(big) as reads:
            out = ol.outline_file(self.roots, "r", "src/big.py")
        self.assertEqual(reads.calls, [])
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], ev._CODES["NOT_TEXT"][0])
        self.assertIn("larger than", out["message"])

    def test_the_same_refusal_for_a_sparse_file_far_over_the_limit_with_a_small_memory_peak(self):
        big = self.root / "src" / "far.py"
        make_big(big, 64 * 1024 * 1024)
        tracemalloc.start()
        try:
            out = ol.outline_file(self.roots, "r", "src/far.py")
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual((out["ok"], out["code"]), (False, ev._CODES["NOT_TEXT"][0]))
        self.assertLess(peak, 2 * 1024 * 1024, f"peak {peak} bytes")

    def test_an_ordinary_file_is_still_outlined_and_is_read_once(self):
        target = self.root / "src" / "m.py"
        with Reads(target) as reads:
            out = ol.outline_file(self.roots, "r", "src/m.py")
        self.assertTrue(out["ok"], out)
        self.assertIn("create_loan", out["text"])
        self.assertEqual(reads.calls, ["open"], "one bounded read through open")


class EvidenceSizeTests(unittest.TestCase):
    """The verifier: repository files and the user texts under atlas/user/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.root = base / "repo"
        (self.project / "atlas" / "user").mkdir(parents=True)
        (self.root / "src").mkdir(parents=True)
        (self.project / "atlas" / "user" / "brief.txt").write_text("The borrower opens the page and sees the list of copies.\n", encoding="utf-8")
        (self.root / "src" / "loan.py").write_text(PYTHON, encoding="utf-8")

    def user_row(self, name="brief", anchor="The borrower opens the page"):
        return {"evidenceId": "U-1", "repository": "", "filePath": f"user:{name}", "anchor": anchor, "topic": "claim", "sourceKind": "user-requirement"}

    def verify(self, rows):
        options = ev.VerifyOptions(compute_root_rev=False)
        return ev.verify_evidence(rows, {"main": self.root}, project_dir=self.project, options=options)["results"]

    def test_a_user_text_over_the_limit_is_unavailable_and_never_read(self):
        big = self.project / "atlas" / "user" / "huge.txt"
        make_big(big, OVER)
        with Reads(big) as reads:
            [result] = self.verify([self.user_row("huge")])
        self.assertEqual(reads.calls, [], "the user text was not read")
        self.assertEqual((result["status"], result["name"]), ("SKIP", "USER_TEXT_MISSING"), result)

    def test_a_user_text_under_the_limit_is_still_verified(self):
        [result] = self.verify([self.user_row()])
        self.assertEqual((result["status"], result["rule"]), ("PASS", "EV-012"), result)

    def test_a_user_text_far_over_the_limit_costs_no_memory(self):
        big = self.project / "atlas" / "user" / "far.txt"
        make_big(big, 64 * 1024 * 1024)
        tracemalloc.start()
        try:
            [result] = self.verify([self.user_row("far")])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(result["name"], "USER_TEXT_MISSING")
        self.assertLess(peak, 2 * 1024 * 1024, f"peak {peak} bytes")

    def test_a_repository_file_over_the_limit_is_refused_and_never_read(self):
        big = self.root / "src" / "big.py"
        make_big(big, OVER)
        row = {"evidenceId": "E-1", "repository": "main", "filePath": "src/big.py", "anchor": "def create_loan(", "topic": "claim", "sourceKind": "source-code"}
        with Reads(big) as reads:
            [result] = self.verify([row])
        self.assertEqual(reads.calls, [])
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["name"], "NOT_TEXT", result)

    def test_a_smaller_limit_in_the_options_is_honoured_for_user_texts(self):
        options = ev.VerifyOptions(compute_root_rev=False, max_bytes=10)
        [result] = ev.verify_evidence([self.user_row()], {"main": self.root}, project_dir=self.project, options=options)["results"]
        self.assertEqual(result["name"], "USER_TEXT_MISSING")


class SavedUserTextTests(OracleProject):
    """The saved requirement texts ``atlas/user/*.txt`` as status, the review report and put read them (debt-b4 fix: they were read
    whole and cut afterwards, so one huge file cost its whole size on every ``status`` call)."""

    QUOTE = "The borrower opens the page and sees the list of copies."

    def setUp(self):
        super().setUp()
        self.desk()
        self.paths.user.mkdir(parents=True, exist_ok=True)

    def big(self, size: int, head: str = "") -> Path:
        path = self.paths.user / "huge.txt"
        make_big(path, size)
        if head:
            with open(path, "r+b") as handle:
                handle.write(head.encode("utf-8"))
        return path

    def peak_of(self, call) -> int:
        tracemalloc.start()
        try:
            call()
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def calls(self):
        scenarios = desk_files()["20-scenarios"]
        return {
            "saved_text": lambda: st.saved_text(self.paths),
            "review user text": lambda: rv._user_text(self.paths),  # noqa: SLF001
            "status": lambda: tools.status(self.ws, self.name),
            "review report": lambda: tools.review(self.ws, self.name, "report"),
            "put": lambda: tools.put(self.ws, self.name, "20-scenarios", scenarios),
        }

    def test_a_huge_saved_text_costs_no_memory_in_any_reader(self):
        baselines = {label: self.peak_of(call) for label, call in self.calls().items()}
        self.big(64 * 1024 * 1024)
        for label, call in self.calls().items():
            with self.subTest(call=label):
                peak = self.peak_of(call)
                self.assertLess(peak - baselines[label], 4 * 1024 * 1024, f"peak {peak} bytes against {baselines[label]} without the file")

    def test_a_huge_saved_text_is_never_loaded_through_read_bytes(self):
        big = self.big(OVER)
        with Reads(big) as reads:
            for call in self.calls().values():
                call()
        self.assertNotIn("Path.read_bytes", reads.calls)

    def test_the_first_part_of_an_oversize_text_still_backs_a_quotation(self):
        self.big(st.REVIEW_MAX_BYTES + 4096, head=self.QUOTE + "\n")
        self.assertIn(st.norm_text(self.QUOTE), st.saved_text(self.paths))
        self.assertIn(st.norm_text(self.QUOTE), rv._user_text(self.paths))  # noqa: SLF001
        self.assertEqual(st.oversize_user_texts(self.paths), ["huge.txt"])

    def test_the_review_report_says_that_a_saved_text_is_cut(self):
        self.big(st.REVIEW_MAX_BYTES + 1)
        warnings = tools.review(self.ws, self.name, "report").get("warnings", [])
        self.assertTrue(any("huge.txt" in w and "first part" in w for w in warnings), warnings)

    def test_a_text_at_the_limit_is_whole_and_not_flagged(self):
        (self.paths.user / "exact.txt").write_bytes(b"x" * st.REVIEW_MAX_BYTES)
        self.assertEqual(st.oversize_user_texts(self.paths), [])
        self.assertEqual(len(st.read_user_text(self.paths.user / "exact.txt")), st.REVIEW_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
