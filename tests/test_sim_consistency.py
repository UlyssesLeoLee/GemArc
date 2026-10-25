"""
Cross-language consistency for the 4D/5D simulation kernel.

The simulator exists twice: in Python (the MCP tool authority) and in
JavaScript (so the browser computes the animation instead of replaying a
precomputed trace). Two implementations of the same semantics drift unless
something pins them together.

This test is that pin. For every golden case it asserts:
  1. the Python engine still reproduces the committed expectation
  2. the JavaScript engine reproduces the same expectation, byte for byte

The JavaScript half is skipped when Node or the engine module is unavailable.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.scaffold.golden import load_cases
from gemarc_mcp.simulation import compare, simulate

RUNTIME = Path(__file__).resolve().parent.parent / "src" / "gemarc_mcp" / "scaffold" / "runtime"
RUNNER = RUNTIME / "tools" / "run-simulation.mjs"
CASES_FILE = RUNTIME.parent / "cases" / "simulation-golden.json"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _engine_available() -> bool:
    return (RUNTIME / "sim" / "index.js").is_file()


def _run_js() -> list[dict]:
    result = subprocess.run(
        ["node", str(RUNNER), str(CASES_FILE)],
        capture_output=True, text=True, timeout=120,
        # The runner emits UTF-8; the Windows locale codec would mangle it.
        encoding="utf-8", errors="replace",
        env={**os.environ, "NODE_OPTIONS": "", "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode != 0:
        raise AssertionError(f"node runner failed: {(result.stderr or '').strip()[:2000]}")
    return json.loads(result.stdout)


class SimulationConsistencyTests(unittest.TestCase):
    def test_python_engine_reproduces_every_golden_case(self):
        for case in load_cases():
            with self.subTest(case=case["id"]):
                expected = case["expected"]
                self.assertEqual(
                    simulate(case["model"], case["scenarioId"], []), expected["baseline"])
                self.assertEqual(
                    simulate(case["model"], case["scenarioId"], case["disabledNodeIds"]),
                    expected["intervention"])
                comparison = compare(case["model"], case["scenarioId"], case["disabledNodeIds"])
                self.assertEqual({
                    "status": comparison["status"],
                    "scenarioId": comparison["scenarioId"],
                    "lostEvents": comparison["lostEvents"],
                    "gainedEvents": comparison["gainedEvents"],
                }, expected["comparison"])

    @unittest.skipUnless(_node_available(), "node is not available")
    @unittest.skipUnless(_engine_available(), "javascript simulation engine is not present")
    def test_javascript_engine_matches_python_byte_for_byte(self):
        js_results = {item["id"]: item for item in _run_js()}
        for case in load_cases():
            with self.subTest(case=case["id"]):
                self.assertIn(case["id"], js_results, "the node runner did not return this case")
                actual = js_results[case["id"]]
                expected = case["expected"]
                self.assertEqual(actual["baseline"], expected["baseline"])
                self.assertEqual(actual["intervention"], expected["intervention"])
                self.assertEqual(actual["comparison"], expected["comparison"])

    @unittest.skipUnless(_node_available(), "node is not available")
    @unittest.skipUnless(_engine_available(), "javascript simulation engine is not present")
    def test_golden_case_file_is_current(self):
        """The committed cases must match what the Python engine produces now."""
        payload = json.loads(CASES_FILE.read_text(encoding="utf-8"))
        for case in payload["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    simulate(case["model"], case["scenarioId"], []),
                    case["expected"]["baseline"])


if __name__ == "__main__":
    unittest.main()
