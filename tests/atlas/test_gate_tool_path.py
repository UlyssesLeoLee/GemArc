"""The browser gate on the tool path: discovery without the Program Files variables, the SKIP text, bounded browser pipes and the
wall-clock budget across widths (contract-requests items 28, 35, 36 and 42; debt-b4, item C).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_gate_tool_path.py"

No browser is needed except for the one real discovery check (it SKIPS without an installed browser in a default directory). Real child
processes (the Python interpreter) stand in for the browser where the pipes are concerned; the budget tests inject the clock and the
launcher, so nothing waits. Synthetic id families only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build
from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.gate import smoke

from .test_tools_b3_gates import Flow

PINNED = "D:/secret-operator/private/edge.exe"
EDGE_X86 = os.path.join("C:\\Program Files (x86)", "Microsoft", "Edge", "Application", "msedge.exe")
EDGE_PF = os.path.join("C:\\Program Files", "Microsoft", "Edge", "Application", "msedge.exe")
CHROME_PF = os.path.join("C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe")
# What the MCP SDK hands a stdio child on Windows (get_default_environment): LOCALAPPDATA but none of the ProgramFiles variables.
SDK_ENV = {"APPDATA": "C:\\U\\AppData\\Roaming", "HOMEDRIVE": "C:", "HOMEPATH": "\\U", "LOCALAPPDATA": "C:\\U\\AppData\\Local", "PATH": "C:\\Windows\\System32",
           "PROCESSOR_ARCHITECTURE": "AMD64", "SYSTEMDRIVE": "C:", "SYSTEMROOT": "C:\\Windows", "TEMP": "C:\\U\\Temp", "USERNAME": "u", "USERPROFILE": "C:\\U"}


def no_which(_name):
    return None


class DefaultInstallFallbackTests(unittest.TestCase):
    def test_an_environment_without_the_program_files_variables_still_lists_the_default_install_directories(self):
        paths = smoke.candidate_paths(SDK_ENV, which=no_which, platform="win32", fallback_environ={})
        self.assertIn(EDGE_X86, paths)
        self.assertIn(EDGE_PF, paths)
        self.assertIn(CHROME_PF, paths)
        self.assertIn(os.path.join("C:\\U\\AppData\\Local", "Microsoft", "Edge", "Application", "msedge.exe"), paths)
        self.assertLess(paths.index(EDGE_X86), paths.index(CHROME_PF), "Edge before Chrome")

    def test_the_process_environment_comes_before_the_literal_defaults_and_the_given_environment_before_both(self):
        paths = smoke.candidate_paths({"LOCALAPPDATA": "C:/LA"}, which=no_which, platform="win32", fallback_environ={"ProgramFiles(x86)": "D:/PF86", "ProgramFiles": "D:/PF"})
        edge = [p for p in paths if p.endswith("msedge.exe")]
        self.assertEqual(edge[:3], [os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe") for root in ("D:/PF86", "D:/PF", "C:/LA")])
        given = smoke.candidate_paths({"ProgramFiles(x86)": "E:/X86"}, which=no_which, platform="win32", fallback_environ={"ProgramFiles(x86)": "D:/PF86"})
        self.assertTrue(given[0].startswith("E:/X86"), given[:2])
        self.assertNotIn(os.path.join("D:/PF86", "Microsoft", "Edge", "Application", "msedge.exe"), given, "the fallback fills only what is missing")

    def test_a_complete_environment_adds_no_directory_of_its_own(self):
        env = {"ProgramFiles(x86)": "C:/X86", "ProgramFiles": "C:/PF", "LOCALAPPDATA": "C:/LA"}
        paths = smoke.candidate_paths(env, which=no_which, platform="win32", fallback_environ={})
        self.assertEqual([p for p in paths if p.endswith("msedge.exe")], [os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe") for r in ("C:/X86", "C:/PF", "C:/LA")])

    def test_find_browser_finds_edge_in_its_default_directory_when_only_localappdata_is_set(self):
        found = smoke.find_browser(SDK_ENV, which=no_which, isfile=lambda p: p == EDGE_X86, platform="win32", fallback_environ={})
        self.assertEqual((found.path, found.family), (EDGE_X86, "edge"))

    def test_a_pinned_browser_still_wins_and_is_still_never_replaced(self):
        env = dict(SDK_ENV, **{smoke.BROWSER_ENV: PINNED})
        self.assertIsNone(smoke.find_browser(env, which=no_which, isfile=lambda p: p == EDGE_X86, platform="win32", fallback_environ={}))

    def test_other_platforms_are_untouched_by_the_windows_defaults(self):
        paths = smoke.candidate_paths({}, which=no_which, platform="linux", fallback_environ={})
        self.assertEqual(paths, [])

    @unittest.skipUnless(sys.platform.startswith("win"), "the default install directories are a Windows matter")
    def test_a_real_installed_browser_is_found_with_the_sdk_style_environment(self):
        default = [p for p in (EDGE_X86, EDGE_PF) if os.path.isfile(p)]
        if not default:
            self.skipTest("no Edge in a default install directory on this machine")
        with tempfile.TemporaryDirectory() as empty:
            filtered = {k: v for k, v in os.environ.items() if k not in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", smoke.BROWSER_ENV)}
            filtered["PATH"] = empty                      # nothing on PATH: only the default directories can find it
            with mock.patch.dict(os.environ, filtered, clear=True):
                found = smoke.find_browser()
        self.assertIsNotNone(found, "Edge is installed in a default directory but the gate found no browser")
        self.assertEqual(found.family, "edge")
        self.assertIn(found.path, default)

    def test_the_tools_no_longer_edit_the_process_environment(self):
        self.assertFalse(hasattr(tools, "ensure_browser_env"), "the fallback lives in the gate; a tool call must not change os.environ")


class SkipMessageTests(unittest.TestCase):
    def test_the_message_says_the_pinned_browser_is_not_a_file_without_naming_it(self):
        text = smoke.no_browser_message({smoke.BROWSER_ENV: PINNED})
        for needle in ("SKIPPED, NOT PASSED", "UNVERIFIED", "the pinned browser", smoke.BROWSER_ENV, "is not a file", "does not fall back"):
            self.assertIn(needle, text)
        self.assertNotIn("secret-operator", text)
        self.assertNotIn(PINNED, text)
        self.assertNotIn("secret-operator", smoke.no_browser_message({}, pinned=PINNED))

    def test_the_command_line_may_name_the_path_its_reader_typed(self):
        text = smoke.no_browser_message({}, pinned=PINNED, show_path=True)
        self.assertIn(PINNED, text)

    def test_a_missing_pinned_browser_leaks_no_path_into_the_raw_verdict_or_the_run(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {smoke.BROWSER_ENV: PINNED}):
            page = Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            verdict = smoke.verify_gate(page, (1024, 1366))
            single = smoke.run_gate(page, 1366, tmp_root=tmp)
        self.assertEqual(verdict["status"], "SKIP")
        self.assertNotIn("secret-operator", json.dumps(verdict), "the verdict, before any scrubbing, has no host path")
        self.assertNotIn("secret-operator", json.dumps(single.as_dict()))
        self.assertIn("the pinned browser", verdict["message"])


class BoundedPipeTests(unittest.TestCase):
    """Real child processes: a browser that floods a pipe cannot make the gate hold the flood in memory."""

    @staticmethod
    def flood(stream: str, megabytes: int) -> list:
        code = f"import sys; sys.{stream}.buffer.write(b'x' * {megabytes} * 1024 * 1024); sys.{stream}.flush()"
        return [sys.executable, "-c", code]

    def test_an_output_under_the_cap_is_returned_whole(self):
        launch = smoke._launch(self.flood("stdout", 1), timeout=30, stdout_cap=4 * 1024 * 1024)
        self.assertEqual((launch.returncode, len(launch.stdout), launch.overflow), (0, 1024 * 1024, None))

    def test_stdout_past_its_cap_is_cut_and_the_launch_is_a_process_failure(self):
        launch = smoke._launch(self.flood("stdout", 30), timeout=60, stdout_cap=1024 * 1024)
        self.assertEqual(launch.returncode, 0, "the child was not blocked: the rest of the pipe was read and thrown away")
        self.assertEqual(len(launch.stdout), 1024 * 1024)
        self.assertIn("stdout", launch.overflow)
        self.assertIn(str(30 * 1024 * 1024), launch.overflow, "the flood size is reported")
        reason = smoke.process_failure_reason(launch)
        self.assertIn("truncated", reason)
        self.assertIn("cap", reason)

    def test_stderr_past_its_cap_is_cut_and_the_launch_is_a_process_failure(self):
        launch = smoke._launch(self.flood("stderr", 12), timeout=60, stderr_cap=512 * 1024)
        self.assertEqual((launch.returncode, len(launch.stderr)), (0, 512 * 1024))
        self.assertIn("stderr", launch.overflow)
        self.assertNotIn("stdout", launch.overflow)
        self.assertIn("truncated", smoke.process_failure_reason(launch))

    def test_both_pipes_flooding_at_once_do_not_deadlock(self):
        code = ("import sys\nfor _ in range(64):\n    sys.stdout.buffer.write(b'o' * 262144)\n    sys.stderr.buffer.write(b'e' * 262144)\n"
                "sys.stdout.flush(); sys.stderr.flush()")
        launch = smoke._launch([sys.executable, "-c", code], timeout=60, stdout_cap=1024 * 1024, stderr_cap=1024 * 1024)
        self.assertEqual((launch.returncode, launch.timed_out, len(launch.stdout), len(launch.stderr)), (0, False, 1024 * 1024, 1024 * 1024))
        self.assertIn("stdout", launch.overflow)
        self.assertIn("stderr", launch.overflow)

    def test_the_memory_held_is_about_the_cap_not_the_flood(self):
        tracemalloc.start()
        try:
            smoke._launch(self.flood("stdout", 48), timeout=90, stdout_cap=1024 * 1024)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * 1024 * 1024, f"peak {peak} bytes for a 48 MiB flood with a 1 MiB cap")

    def test_the_default_caps_are_sized_from_the_measured_dumps(self):
        # measured (Edge 152, built reference-scale pages in gate mode): stdout 40 to 82 KB, 2.1 MB with the scripts still in the DOM; stderr 2 to 121 KB
        self.assertGreaterEqual(smoke.STDOUT_CAP_BYTES, 2 * H.FAIL_BYTES, "no page the build accepts can overflow the stdout cap")
        self.assertGreaterEqual(smoke.STDOUT_CAP_BYTES, 100 * 82 * 1024)
        self.assertGreaterEqual(smoke.STDERR_CAP_BYTES, 30 * 121 * 1024)
        self.assertLessEqual(smoke.STDERR_CAP_BYTES, 16 * 1024 * 1024)

    def test_a_cut_dump_is_never_judged_it_is_a_process_failure_in_run_gate(self):
        cut = smoke._Launch(0, b"<html><body>fine so far</body></html>", b"", False, 1.0, overflow="stdout 99999999 bytes over the 16777216 byte cap")
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            with mock.patch.object(smoke, "_launch", return_value=cut):
                res = smoke.run_gate(page, 1366, browser=smoke.Browser("edge", "edge", "1"), tmp_root=tmp, retries=0)
        self.assertEqual((res.status, res.codes), ("FAIL", ["PROCESS"]))
        self.assertIn("truncated", res.message)


def timed_out(width: int, seconds: float) -> "smoke.GateResult":
    return smoke.GateResult("FAIL", "p", width, process_failures=[f"timeout after {seconds:.1f}s (process tree killed)"], findings=[smoke.Finding("PROCESS", "x")],
                            total_seconds=seconds, attempts=1)


def good(width: int, seconds: float = 2.0) -> "smoke.GateResult":
    return smoke.GateResult("PASS", "p", width, total_seconds=seconds, attempts=1)


class Clock:
    """A time source that moves only when a (mocked) launch says how long it took."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class BudgetTests(unittest.TestCase):
    BROWSER = smoke.Browser("e", "edge", "1")

    def run_verify(self, plan, widths=(1024, 1366, 1600), **kw):
        """``plan``: one ``(seconds the launch takes, result)`` per launch; returns ``(verdict, [kwargs of each launch])``."""
        clock, seen, it = Clock(), [], iter(plan)

        def fake(page, width, **k):
            seen.append(dict(k, width=width))
            seconds, result = next(it)
            clock.now += seconds
            return result(width, seconds) if callable(result) else result

        with mock.patch.object(smoke, "run_gate", side_effect=fake):
            verdict = smoke.verify_gate(Path("p.html"), widths, browser=self.BROWSER, clock=clock, **kw)
        return verdict, seen

    def test_after_the_first_inconclusive_width_no_further_width_is_launched_and_the_verdict_says_so(self):
        verdict, seen = self.run_verify([(90.0, timed_out)], stop_after_inconclusive=True, budget_seconds=smoke.VERIFY_BUDGET_SECONDS)
        self.assertEqual(len(seen), 1, "one launch only")
        self.assertEqual(verdict["status"], "INCONCLUSIVE")
        self.assertEqual([r["status"] for r in verdict["widths"]], ["INCONCLUSIVE"] * 3)
        self.assertEqual([bool(r.get("notRun")) for r in verdict["widths"]], [False, True, True])
        self.assertEqual([r["width"] for r in verdict["widths"]], [1024, 1366, 1600])
        for row in verdict["widths"][1:]:
            self.assertEqual((row["attempts"], row["seconds"], row["codes"], row["findings"]), (0, 0.0, ["NOT_RUN"], []))
            self.assertTrue(row["message"].startswith("NOT RUN:"), row["message"])
        self.assertIn("NOT launched", verdict["message"])
        self.assertIn("1366, 1600", verdict["message"])
        self.assertTrue(verdict["lines"][1].startswith("INCONCLUSIVE 1366: NOT RUN"), verdict["lines"])
        self.assertIn("NOT verified", verdict["lines"][2])
        self.assertEqual(verdict["budget"]["notRun"], [1366, 1600])

    def test_without_the_two_arguments_every_width_is_launched_whatever_the_others_did(self):
        verdict, seen = self.run_verify([(90.0, timed_out), (1.0, good), (1.0, good)])
        self.assertEqual(len(seen), 3)
        self.assertEqual(verdict["status"], "INCONCLUSIVE")
        self.assertNotIn("budget", verdict)
        self.assertEqual(verdict["message"], "")
        self.assertFalse(any(r.get("notRun") for r in verdict["widths"]))

    def test_a_fail_ranks_above_an_inconclusive_width(self):
        bad = smoke.GateResult("FAIL", "p", 1024, findings=[smoke.Finding("HEARTBEAT", "x")])
        verdict, seen = self.run_verify([(2.0, bad), (90.0, timed_out)], stop_after_inconclusive=True, budget_seconds=150)
        self.assertEqual(len(seen), 2)
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual([r["status"] for r in verdict["widths"]], ["FAIL", "INCONCLUSIVE", "INCONCLUSIVE"])

    def test_the_budget_caps_the_wait_of_each_launch_and_stops_when_it_is_spent(self):
        # 90 s limit per launch, 150 s for all: 100 s used, 50 s left for the second launch, nothing left for the third
        verdict, seen = self.run_verify([(100.0, good), (50.0, timed_out)], budget_seconds=150)
        self.assertEqual([k["timeout"] for k in seen], [90.0, 50.0])
        self.assertEqual(len(seen), 2)
        self.assertEqual([bool(r.get("notRun")) for r in verdict["widths"]], [False, False, True])
        self.assertIn("150 s wall-clock budget", verdict["widths"][2]["message"])
        self.assertEqual(verdict["status"], "INCONCLUSIVE")
        self.assertEqual(verdict["budget"]["usedSeconds"], 150.0)

    def test_no_launch_is_started_with_less_than_the_minimum_left(self):
        verdict, seen = self.run_verify([(145.0, good)], budget_seconds=150)
        self.assertEqual(len(seen), 1, "5 s left is below the minimum launch time")
        self.assertEqual(verdict["status"], "INCONCLUSIVE", "a width that was not judged is never a pass")
        self.assertEqual([bool(r.get("notRun")) for r in verdict["widths"]], [False, True, True])

    def test_a_good_run_uses_the_whole_default_timeout_for_every_width_and_passes(self):
        verdict, seen = self.run_verify([(3.0, good), (3.0, good), (3.0, good)], budget_seconds=smoke.VERIFY_BUDGET_SECONDS, stop_after_inconclusive=True)
        self.assertEqual(verdict["status"], "PASS")
        self.assertEqual([k["timeout"] for k in seen], [smoke.VERIFY_TIMEOUT_SECONDS] * 3)
        self.assertEqual(verdict["budget"], {"seconds": 150.0, "usedSeconds": 9.0, "notRun": []})
        self.assertEqual(verdict["message"], "")

    def test_the_first_width_is_always_launched(self):
        verdict, seen = self.run_verify([(1.0, good)], widths=(1366,), budget_seconds=5)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["timeout"], smoke.VERIFY_MIN_LAUNCH_SECONDS, "the least a launch gets")
        self.assertEqual(verdict["status"], "PASS")

    def test_the_budget_is_about_two_and_a_half_minutes_and_a_limit_the_caller_gave_is_kept(self):
        self.assertEqual(smoke.VERIFY_BUDGET_SECONDS, 150.0)
        self.assertGreater(smoke.VERIFY_BUDGET_SECONDS, smoke.VERIFY_TIMEOUT_SECONDS)
        verdict, seen = self.run_verify([(2.0, good)], widths=(1366,), budget_seconds=150, timeout=12)
        self.assertEqual(seen[0]["timeout"], 12.0)

    def test_the_tool_path_passes_the_budget_and_the_stop_rule_the_command_line_does_not(self):
        captured = {}

        def fake(page, widths, **k):
            captured.update(k)
            return {"status": "PASS", "widths": [], "lines": [], "browser": None, "message": ""}

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(smoke, "verify_gate", side_effect=fake):
            out = Path(tmp)
            build.run_gate(out / "index.html", (1024, 1366, 1600), out)
        self.assertEqual((captured["budget_seconds"], captured["stop_after_inconclusive"]), (smoke.VERIFY_BUDGET_SECONDS, True))
        import inspect
        self.assertNotIn("verify_gate", inspect.getsource(smoke.main), "the command line launches every width through run_gate")

    def test_a_not_run_row_is_not_a_pass_for_the_step_check_and_the_summary(self):
        verdict, _ = self.run_verify([(90.0, timed_out)], stop_after_inconclusive=True, budget_seconds=150)
        checked = build.require_steps(json.loads(json.dumps(verdict)))
        self.assertEqual(checked["status"], "INCONCLUSIVE")
        summary = build.gate_summary(checked)
        self.assertEqual([(w["width"], w["status"], w["codes"]) for w in summary["widths"]],
                         [(1024, "INCONCLUSIVE", ["PROCESS"]), (1366, "INCONCLUSIVE", ["NOT_RUN"]), (1600, "INCONCLUSIVE", ["NOT_RUN"])])


class RetryBudgetTests(unittest.TestCase):
    """A retry of a failed launch is charged against the wall-clock budget of the tool path (debt-b4 fix): before, every attempt got the
    whole per-launch limit, so one width could use two long attempts (about 160 s measured against the 150 s budget)."""

    BROWSER = smoke.Browser("e", "edge", "1")

    def run_verify(self, plan, widths=(1366,), budget=150):
        """``plan``: one ``(seconds the launch takes, return code)`` per launch of the (mocked) browser."""
        clock, timeouts, it = Clock(), [], iter(plan)

        def fake_launch(args, timeout, env=None, **kw):
            timeouts.append(timeout)
            seconds, code = next(it)
            clock.now += seconds
            return smoke._Launch(code, b"", b"", False, seconds)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(smoke, "_launch", side_effect=fake_launch):
            page = Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            verdict = smoke.verify_gate(page, widths, browser=self.BROWSER, clock=clock, budget_seconds=budget, tmp_root=tmp)
        return verdict, timeouts

    def test_the_retry_gets_what_is_left_of_the_budget_not_the_whole_limit(self):
        verdict, timeouts = self.run_verify([(80.0, 1), (5.0, 1)])
        self.assertEqual(timeouts, [90.0, 70.0])
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["widths"][0]["attempts"], 2)

    def test_no_retry_is_started_with_less_than_the_minimum_left(self):
        verdict, timeouts = self.run_verify([(88.0, 1)], budget=95)
        self.assertEqual(timeouts, [90.0], "7 s are left: the failed launch is not repeated")
        row = verdict["widths"][0]
        self.assertEqual((row["status"], row["attempts"]), ("FAIL", 1))
        self.assertIn("no retry: the wall-clock budget of the gate is spent", row["findings"][0]["detail"])
        self.assertIn("browser exited with code 1", row["findings"][0]["detail"])

    def test_the_worst_case_of_the_old_code_now_stays_inside_the_budget(self):
        # the verifier's case: a browser that exits 3 after 80 s, three widths. Before: width 1 used 2 x 80 s = 160 s.
        verdict, timeouts = self.run_verify([(80.0, 3), (5.0, 3)] + [(1.0, 3)] * 4, widths=(1024, 1366, 1600))
        self.assertLessEqual(verdict["budget"]["usedSeconds"], 150.0, verdict["budget"])
        self.assertTrue(all(t <= smoke.VERIFY_TIMEOUT_SECONDS for t in timeouts), timeouts)

    def test_without_a_budget_the_retry_keeps_the_full_limit(self):
        timeouts, it = [], iter([1, 1])

        def fake_launch(args, timeout, env=None, **kw):
            timeouts.append(timeout)
            return smoke._Launch(next(it), b"", b"", False, 1.0)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(smoke, "_launch", side_effect=fake_launch):
            page = Path(tmp) / "page.html"
            page.write_text("<html></html>", encoding="utf-8")
            smoke.run_gate(page, 1366, browser=self.BROWSER, tmp_root=tmp, timeout=40.0)
        self.assertEqual(timeouts, [40.0, 40.0])


class FakeStream:
    """A pipe whose reads are scripted: bytes are returned, an exception is raised."""

    def __init__(self, *script):
        self.script = list(script)
        self.closed = False

    def read1(self, _size):
        item = self.script.pop(0) if self.script else b""
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class PipeReadErrorTests(unittest.TestCase):
    """A read that fails is not an end of file: the old ``communicate`` raised, the reader threads of debt-b4 swallowed it."""

    def test_the_reader_keeps_the_error_and_what_it_had_read(self):
        pipe = smoke._Pipe(FakeStream(b"first line\n", OSError("the pipe broke"), b"never read"), 1024)
        pipe.run()
        self.assertEqual(bytes(pipe.data), b"first line\n")
        self.assertIn("OSError: the pipe broke", pipe.error)

    def test_a_clean_end_of_file_has_no_error(self):
        pipe = smoke._Pipe(FakeStream(b"abc", b""), 1024)
        pipe.run()
        self.assertEqual((bytes(pipe.data), pipe.error), (b"abc", None))

    def fake_popen(self, stdout, stderr):
        proc = mock.Mock()
        proc.stdout, proc.stderr = stdout, stderr
        proc.returncode = 0
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        return proc

    def test_a_launch_whose_stderr_was_cut_by_a_read_error_is_a_process_failure_not_a_clean_one(self):
        proc = self.fake_popen(FakeStream(b"<html></html>"), FakeStream(b"console line 1\n", OSError("read failed"), b"console line 2: ERROR\n"))
        with mock.patch.object(smoke.subprocess, "Popen", return_value=proc):
            launch = smoke._launch(["browser"], 5)
        self.assertEqual((launch.returncode, launch.timed_out, launch.overflow), (0, False, None))
        self.assertIn("stderr OSError: read failed", launch.read_error)
        reason = smoke.process_failure_reason(launch)
        self.assertIn("could not be read to its end", reason)

    def test_a_complete_launch_has_no_read_error_and_is_not_a_failure(self):
        proc = self.fake_popen(FakeStream(b"<html></html>"), FakeStream(b"log\n"))
        with mock.patch.object(smoke.subprocess, "Popen", return_value=proc):
            launch = smoke._launch(["browser"], 5)
        self.assertIsNone(launch.read_error)
        self.assertIsNone(smoke.process_failure_reason(launch))

    def test_a_timeout_stays_a_timeout_first(self):
        launch = smoke._Launch(None, b"", b"", True, 5.0, read_error="stdout OSError: x")
        self.assertTrue(smoke.process_failure_reason(launch).startswith("timeout"))


class DescendantHoldsPipeTests(unittest.TestCase):
    """A browser that exits while a descendant keeps a pipe open (a crash helper): the old join of 2 x 5 s after the kill added ten seconds."""

    def test_the_wait_ends_close_to_the_limit(self):
        code = ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']); "
                "sys.stdout.write('</html>'); sys.stdout.flush()")
        started = time.perf_counter()
        launch = smoke._launch([sys.executable, "-c", code], timeout=2)
        elapsed = time.perf_counter() - started
        self.assertTrue(launch.timed_out, "the pipe never closed within the limit: that is a timeout, never a pass")
        self.assertIsNotNone(smoke.process_failure_reason(launch))
        self.assertLess(elapsed, 2 + 5, f"{elapsed:.1f} s: the limit plus at most a couple of seconds, not the limit plus ten")


class ScrubHostPathsTests(unittest.TestCase):
    """A page-level FAIL quotes the URL of the page: no machine path may reach a reply or ``gate-report.json`` (debt-b4 fix)."""

    DIRTY = [
        "onerror: Uncaught Error: boom @file:///C:/Users/someone/AppData/Local/Temp/atlas-gate-ab12/dist/index.html:123:7",
        "resource: SCRIPT file:///C:/Users/someone/AppData/Local/Temp/atlas-gate-ab12/app.js, IMG 'file:///C:/Users/someone/My%20Dir/c.png'",
        "first: 'C:\\\\Users\\\\someone\\\\AppData\\\\Local\\\\Temp\\\\gate\\\\index.html:5 (blocked)'",
        "C:\\Users\\someone\\work\\dist\\index.html did not load",
        "file:///home/someone/work/dist/index.html:1:2 and /Users/someone/p/q.js",
    ]

    def test_every_machine_path_becomes_a_file_name(self):
        for text in self.DIRTY:
            clean = smoke.scrub_host_paths(text)
            with self.subTest(text=text):
                for word in ("file://", "someone", "AppData", "C:", "/home", "/Users", "Temp"):
                    self.assertNotIn(word, clean)
        self.assertEqual(smoke.scrub_host_paths(self.DIRTY[0]), "onerror: Uncaught Error: boom @index.html:123:7")
        self.assertIn("app.js", smoke.scrub_host_paths(self.DIRTY[1]))

    def test_text_without_a_path_is_unchanged(self):
        for text in ("PAGE_ERRORS: 2 uncaught errors", "details/summary and a/b and 1:2", "see http://example.invalid/x", "", "NaN in the text of tab 3D"):
            self.assertEqual(smoke.scrub_host_paths(text), text)

    def test_a_page_fail_reaches_the_verdict_without_a_path(self):
        findings = [smoke.Finding("PAGE_ERRORS", self.DIRTY[0]), smoke.Finding("CONSOLE_STDERR", self.DIRTY[2])]
        failed = smoke.GateResult("FAIL", "C:\\Users\\someone\\x\\index.html", 1366, findings=findings, total_seconds=2.0, attempts=1,
                                  message="; ".join(str(f) for f in findings))
        with mock.patch.object(smoke, "run_gate", return_value=failed):
            verdict = smoke.verify_gate(Path("p.html"), (1366,), browser=smoke.Browser("e", "edge", "1"))
        text = json.dumps(verdict)
        for word in ("file://", "someone", "AppData", "C:\\\\", "Temp"):
            self.assertNotIn(word, text)
        self.assertIn("index.html:123:7", text, "the file name and the position stay: that is what helps")
        self.assertEqual(verdict["status"], "FAIL")


class HostPathsInReplyTests(Flow):
    """The same through ``tools.verify``: the reply and ``gate-report.json`` of a page-level FAIL."""

    def test_a_page_level_fail_leaves_no_machine_path_in_the_reply_or_the_report(self):
        self.authored()
        self.gates_green(browser=None)
        findings = [smoke.Finding("PAGE_ERRORS", ScrubHostPathsTests.DIRTY[0])]

        def fake(page, width, **k):
            return smoke.GateResult("FAIL", str(page), width, findings=findings, total_seconds=2.0, attempts=1, message=str(findings[0]))

        with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("e", "edge", "1")), mock.patch.object(smoke, "run_gate", side_effect=fake):
            reply = tools.verify(self.ws, self.name)
        report = (self.paths.atlas / "dist" / ".verify" / "gate-report.json").read_text(encoding="utf-8")
        # the "report" and "dist" values of a direct call are the location of the project on this machine (the MCP layer shows them relative to
        # the workspace; the shell entry prints them in full, see ATLAS_TOOLS.md): what is checked here is the gate's own text
        shown = {key: value for key, value in reply.items() if key not in ("report", "dist")}
        for label, text in (("reply", json.dumps(shown)), ("report", report)):
            for word in ("file://", "someone", "AppData"):
                self.assertNotIn(word, text, label)
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertIn("index.html", json.dumps(reply))


class ToolReplyTests(Flow):
    """``verify`` through the tool function: the INCONCLUSIVE reply says which widths were not launched, the stamp stays INCONCLUSIVE."""

    def test_verify_replies_inconclusive_and_names_the_widths_that_were_not_run(self):
        self.authored()
        self.gates_green(browser=None)
        calls = []

        def fake(page, width, **k):
            calls.append((width, k.get("timeout")))
            return timed_out(width, 90.0)

        with mock.patch.object(smoke, "find_browser", return_value=smoke.Browser("e", "edge", "1")), mock.patch.object(smoke, "run_gate", side_effect=fake):
            reply = tools.verify(self.ws, self.name)
        self.assertEqual([w for w, _t in calls], [1024], "the machine timed out once: no other width was launched")
        self.assertEqual((reply["status"], reply["code"], reply["gate"]), ("invalid", build.E_GATE_INCONCLUSIVE, "INCONCLUSIVE"), reply)
        self.assertIn("NOT launched", reply["message"])
        self.assertIn("1366, 1600", reply["message"])
        self.assertEqual([r["status"] for r in reply["widths"]], ["INCONCLUSIVE"] * 3)
        self.assertTrue(any("NOT RUN" in line for line in reply["lines"]), reply["lines"])
        self.assertLessEqual(tools.reply_bytes(reply), tools.VERIFY_CAP)
        stamp = st.read_gate_stamp(self.paths)
        self.assertEqual(stamp["status"], "INCONCLUSIVE")
        report = json.loads((self.paths.atlas / "dist" / ".verify" / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["budget"]["notRun"], [1366, 1600])
        gate = self.gate_of("browser")
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["short"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
