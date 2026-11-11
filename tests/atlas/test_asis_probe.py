"""``tools/pilot/asis_probe.py`` (milestone M8): the as-is probe, tested on a generated repository (the same 25-module repository as the as-is pipeline), so the
numbers it prints are known in advance, and for what it must never do: change the tree it reads or leave bytecode behind.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_asis_probe*.py"
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import pipeline_flows as F

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tools" / "pilot" / "asis_probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("asis_probe_under_test", PROBE)
    module = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True       # importing the tool must not leave bytecode under tools/pilot
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


def tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\1")
    return digest.hexdigest()


class PureTests(unittest.TestCase):
    def test_percentiles_are_nearest_rank(self):
        probe = load_probe()
        self.assertIsNone(probe.percentile([], 0.5))
        self.assertEqual(probe.percentile([5], 0.9), 5)
        data = list(range(1, 11))
        self.assertEqual((probe.percentile(data, 0.5), probe.percentile(data, 0.9), probe.percentile(data, 1.0)), (5, 9, 10))
        self.assertEqual(probe.stats([2, 4, 6])["sum"], 12)

    def test_the_line_format_of_the_outline_is_parsed_with_and_without_an_anchor(self):
        probe = load_probe()
        a = probe.LINE.match('L29 const SITE_PROFILES | "SITE_PROFILES ="')
        b = probe.LINE.match("L246 def LazySite.__init__ | - (duplicate-line)")
        self.assertEqual((a.group(3), b.group(3), b.group(4)), ("SITE_PROFILES =", None, "duplicate-line"))
        self.assertEqual(probe.HEADER.match("# api/index.py [python] 747 lines, 61 entries, 5 without a unique single-line anchor").groups(), ("747", "61", "5"))


class ProbeRunTests(unittest.TestCase):
    def test_the_probe_on_the_generated_repository_reports_the_known_numbers_and_changes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="atlas-probe-") as tmp:
            root = Path(tmp) / "repo"
            F.make_repository(root)
            before = tree(root)
            pycache_before = sorted((REPO / "tools" / "pilot").rglob("__pycache__"))
            done = subprocess.run([sys.executable, str(PROBE), "--subdir", "pkg", "--exclude", "", "--json"], capture_output=True, text=True, timeout=240, encoding="utf-8",
                                  errors="replace", env={**{k: v for k, v in os.environ.items() if not k.startswith("GEMARC_")}, "GEMARC_ATLAS_ROOTS": f"main={root}"})
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            summary = json.loads(done.stdout)
            self.assertEqual(tree(root), before, "the tree that was probed is byte-identical afterwards")
            self.assertEqual(sorted((REPO / "tools" / "pilot").rglob("__pycache__")), pycache_before, "the probe leaves no bytecode behind")
        self.assertEqual((summary["files"], summary["modules"], summary["packages"]), (26, 25, 1))
        self.assertEqual(summary["filesWithError"], [])
        self.assertTrue(summary["repoUnchanged"])
        self.assertEqual(summary["uniqueAnchorRate"], 1.0)
        self.assertEqual(summary["ev003"]["stampable"], summary["anchors"], "every listed anchor is EV-003 stampable on first stamp")
        self.assertEqual(summary["ev003"]["byStatus"], {"PASS": summary["anchors"]})
        self.assertGreater(summary["anchors"], 25 * 5)
        self.assertGreater(summary["entriesWithoutAnchor"], 0, "the generated repository has duplicated constructor lines: no unique anchor there, and the tool says so")
        self.assertEqual(summary["outlineReplyBytesPerFile"]["max"] < 4500, True)
        self.assertEqual(set(summary["seconds"]), {"outline", "verify", "outlineMsPerFile"})
        self.assertEqual(sorted(summary["outlineReplyBytesPerFile"]), ["max", "mean", "p50", "p90", "sum"])

    def test_the_default_run_on_this_repository_skips_the_atlas_package_and_includes_the_binding(self):
        probe = load_probe()
        files = probe.source_files(REPO / "src" / "gemarc_mcp", ("atlas",))
        names = {p.relative_to(REPO / "src" / "gemarc_mcp").as_posix() for p in files}
        self.assertIn("atlas_tools.py", names, "the MCP binding lives outside atlas/ and is part of the subject")
        self.assertIn("server.py", names)
        self.assertFalse([n for n in names if n.startswith("atlas/")])
        self.assertTrue(any(n.startswith("accounts/") for n in names))

    def test_a_missing_alias_is_a_clear_refusal(self):
        done = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=120,
                              env={**{k: v for k, v in os.environ.items() if not k.startswith("GEMARC_")}, "GEMARC_ATLAS_ROOTS": "other=C:/nowhere"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("alias 'main'", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
