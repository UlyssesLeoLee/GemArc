"""The command line ``python -m gemarc_mcp.atlas`` (design M2): init, put, check, status, index, brief.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_cli.py"

The scripted lending-desk session runs through real child processes (``python -m gemarc_mcp.atlas``); the 40-node synthetic session
runs through ``cli.main`` in this process (same code path, no process start per call). Synthetic id families only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import cli, encode
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import tools

from . import synth

SRC = Path(__file__).resolve().parents[2] / "src"
DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
DESK_NAMES = ("00-space", "10-structure", "20-scenarios")
POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "ws"
        self.root.mkdir()
        patcher = mock.patch.dict(os.environ, {"GEMARC_WORKSPACE_ROOT": str(self.root), "GEMARC_ALLOW_WRITE": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("GEMARC_ATLAS_DEBUG", None)

    def env(self, **extra):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(extra)
        return env

    def proc(self, *argv, stdin=None, env=None):
        """One real child process; ``(parsed JSON reply or raw stdout, exit code, stderr)``."""
        done = subprocess.run([sys.executable, "-m", "gemarc_mcp.atlas", *argv], input=stdin, capture_output=True, env=env or self.env(),
                              cwd=str(self.root.parent), timeout=120)
        out = done.stdout.decode("utf-8")
        try:
            return json.loads(out), done.returncode, done.stderr.decode("utf-8")
        except ValueError:
            return out, done.returncode, done.stderr.decode("utf-8")

    def main(self, *argv, stdin=None):
        """``cli.main`` in this process with captured stdout; ``(reply or text, exit code)``."""
        buffer = io.BytesIO()
        fake = mock.Mock()
        fake.buffer = buffer
        with mock.patch.object(sys, "stdout", fake):
            if stdin is not None:
                with mock.patch.object(sys, "stdin", mock.Mock(buffer=io.BytesIO(stdin.encode("utf-8")))):
                    code = cli.main(list(argv))
            else:
                code = cli.main(list(argv))
        out = buffer.getvalue().decode("utf-8")
        try:
            return json.loads(out), code
        except ValueError:
            return out, code


class CommandLineTests(Base):
    def test_a_bad_command_line_exits_2_and_a_refused_request_exits_1(self):
        out, code, err = self.proc("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)
        out, code, err = self.proc("put", "desk", "10-structure")
        self.assertEqual(code, 2)
        self.assertIn("--file", err)
        reply, code, _ = self.proc("status", "never-initialised")
        self.assertEqual((reply["status"], code), ("invalid", 1))
        self.assertEqual(reply["code"], "GEMARC_ATLAS_PROJ_001")

    def test_root_and_human_flags_work_before_and_after_the_command(self):
        elsewhere = Path(self._tmp.name) / "other"
        elsewhere.mkdir()
        reply, code = self.main("init", "desk", "--root", str(elsewhere))
        self.assertEqual((reply["status"], code), ("created", 0))
        self.assertTrue((elsewhere / "desk" / "atlas" / "atlas.toml").is_file())
        self.assertFalse((self.root / "desk").exists())
        text, code = self.main("--human", "--root", str(elsewhere), "status", "desk")
        self.assertEqual(code, 0)
        self.assertIsInstance(text, str)
        self.assertIn("collect", text)
        text2, _ = self.main("status", "desk", "--root", str(elsewhere), "--human")
        self.assertEqual(text, text2)

    def test_writes_need_allow_write_and_nothing_is_written_without_it(self):
        with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "0"}):
            reply, code, _ = self.proc("init", "desk", env=self.env(GEMARC_ALLOW_WRITE="0"))
        self.assertEqual((reply["status"], code, reply["code"]), ("invalid", 1, "GEMARC_AUTH_002"))
        self.assertFalse((self.root / "desk").exists())
        reply, code, _ = self.proc("init", "desk")
        self.assertEqual((reply["status"], code), ("created", 0))
        before = sorted(p.name for p in (self.root / "desk" / "atlas" / "slices").iterdir())
        reply, code, _ = self.proc("put", "desk", "00-space", "--file", str(DESK_DIR / "00-space.atl"), env=self.env(GEMARC_ALLOW_WRITE="0"))
        self.assertEqual((reply["status"], code), ("invalid", 1))
        self.assertEqual(sorted(p.name for p in (self.root / "desk" / "atlas" / "slices").iterdir()), before)
        for read_only in (("status", "desk"), ("check", "desk", "--mode", "full"), ("index", "desk"), ("brief",)):
            reply, code, _ = self.proc(*read_only, env=self.env(GEMARC_ALLOW_WRITE="0"))
            self.assertEqual(reply["status"], "ok", read_only)
            self.assertEqual(code, 0 if read_only[0] != "check" else 0, read_only)

    def test_put_reads_the_text_from_a_file_from_text_and_from_standard_input(self):
        self.main("init", "desk", "--repos", "main", "--languages", "en-US")
        text = (DESK_DIR / "00-space.atl").read_text(encoding="utf-8")
        reply, code, _ = self.proc("put", "desk", "00-space", "-", stdin=text.encode("utf-8"))
        self.assertEqual((reply["status"], code), ("committed", 0))
        sha = reply["sha8"]
        reply, code = self.main("put", "desk", "00-space", "--text", text)
        self.assertEqual((reply["status"], reply["unchanged"], reply["sha8"], code), ("committed", True, sha, 0))
        reply, code = self.main("put", "desk", "00-space", "--file", str(DESK_DIR / "00-space.atl"))
        self.assertEqual((reply["unchanged"], reply["sha8"]), (True, sha))
        reply, code = self.main("put", "desk", "00-space", "-", stdin=text)
        self.assertEqual((reply["unchanged"], reply["sha8"]), (True, sha))
        reply, code = self.main("put", "desk", "00-space", "--file", str(DESK_DIR / "no-such-file.atl"))
        self.assertEqual(code, 2)

    def test_base_conflict_through_the_command_line_writes_nothing(self):
        self.main("init", "desk", "--repos", "main", "--languages", "en-US")
        self.main("put", "desk", "00-space", "--file", str(DESK_DIR / "00-space.atl"))
        path = self.root / "desk" / "atlas" / "slices" / "00-space.atl"
        before = path.read_bytes()
        reply, code = self.main("put", "desk", "00-space", "--text", (DESK_DIR / "00-space.atl").read_text(encoding="utf-8") + "\n// more\n",
                                "--base", "deadbeef")
        self.assertEqual((reply["status"], reply["code"], code), ("invalid", "GEMARC_ATLAS_SLICE_003", 1))
        self.assertTrue(reply.get("conflict"))
        self.assertEqual(path.read_bytes(), before)

    def test_inline_check_prints_human_text_and_exits_1_on_an_error(self):
        text, code = self.main("check", "--text", "-", "--human", stdin="bad\n")
        self.assertEqual(code, 1)
        self.assertIn("ST-001", text)
        good = "#atlas 1\n## buildings: id | name | opts\nbld.a | Main building\n"
        reply, code = self.main("check", "--text", "-", stdin=good)
        self.assertEqual((reply["authority"], reply["clean"], code), ("advisory", True, 0))
        sample = Path(self._tmp.name) / "inline.atl"
        sample.write_text(good, encoding="utf-8")
        reply, code = self.main("check", "--text", str(sample))
        self.assertEqual((reply["clean"], code), (True, 0))

    def test_brief_prints_both_languages(self):
        reply, code = self.main("brief", "--lang", "zh-CN")
        self.assertEqual((reply["status"], reply["lang"], code), ("ok", "zh-CN", 0))
        self.assertLessEqual(reply["bytes"], 9000)
        self.assertRegex(reply["text"], "[一-鿿]")
        text, code = self.main("brief", "--section", "derived", "--lang", "en-US", "--human")
        self.assertEqual(code, 0)
        self.assertNotRegex(text, "[一-鿿]")
        reply, code = self.main("brief", "--section", "nope")
        self.assertEqual((reply["status"], code), ("invalid", 1))

    def test_output_is_utf8_json_with_the_unicode_kept(self):
        self.main("init", "desk", "--title", "借阅台", "--languages", "zh-CN,en-US")
        reply, code, _ = self.proc("status", "desk")
        self.assertEqual(code, 0)
        raw = subprocess.run([sys.executable, "-m", "gemarc_mcp.atlas", "brief", "--lang", "zh-CN"], capture_output=True, env=self.env()).stdout
        self.assertIn("切片".encode("utf-8"), raw)
        self.assertNotIn(b"\\u5207", raw)


class LendingDeskSessionTests(Base):
    """init, a failing put, repair by patch, status after each put, check full, index scope reads: all in child processes."""

    def test_scripted_session(self):
        reply, code, _ = self.proc("init", "lending-desk", "--title", "Library lending desk", "--repos", "main", "--languages", "en-US")
        self.assertEqual((reply["status"], code), ("created", 0), reply)
        statuses = []
        for name in DESK_NAMES:
            text = (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8")
            if name == "10-structure":
                bad, code, _ = self.proc("put", "lending-desk", name, "-", stdin=text.replace("kind=Module", "kind=Modul").encode("utf-8"))
                self.assertEqual((bad["status"], code, bad["red"]), ("invalid", 1, True))
                after_bad, code, _ = self.proc("status", "lending-desk")
                self.assertEqual((after_bad["phase"], code), ("repair", 0))
                self.assertIn("red draft", after_bad["next"][0])
                statuses.append(after_bad)
                patch = ("## nodes\n@set room=rm.desk kind=Module\nsvc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2\n"
                         "svc.stock | Stock service | Knows which copy is on the shelf. | ev=EVD-2\n@set kind=Storage\n"
                         "tbl.loan | Loan table | One row per open loan. | ev=EVD-1\n@set room=rm.vis kind=Actor\n"
                         "usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Inferred\n")
                reply, code, _ = self.proc("put", "lending-desk", name, "--mode", "patch", "-", stdin=patch.encode("utf-8"))
                self.assertEqual((reply["status"], code), ("committed", 0), reply)
                self.assertTrue(reply["red_draft_cleared"])
            else:
                reply, code, _ = self.proc("put", "lending-desk", name, "--file", str(DESK_DIR / f"{name}.atl"))
                self.assertEqual((reply["status"], code), ("committed", 0), reply)
            status, code, _ = self.proc("status", "lending-desk")
            self.assertEqual(code, 0)
            statuses.append(status)
        for status in statuses:
            self.assertLessEqual(len(status["next"]), 10)
        self.assertEqual(statuses[-1]["phase"], "author")                     # the NFR chapter has no text yet
        reply, code, _ = self.proc("put", "lending-desk", "30-policy", "-", stdin=POLICY.encode("utf-8"))
        self.assertEqual((reply["status"], code), ("committed", 0), reply)
        statuses.append(self.proc("status", "lending-desk")[0])
        self.assertEqual(statuses[-1]["phase"], "review")
        again, code, _ = self.proc("put", "lending-desk", "30-policy", "-", stdin=POLICY.encode("utf-8"))
        self.assertEqual((again["unchanged"], again["sha8"], code), (True, reply["sha8"], 0))
        edited, code, _ = self.proc("put", "lending-desk", "30-policy", "--base", reply["sha8"], "-",
                                    stdin=POLICY.replace("two seconds", "three seconds").encode("utf-8"))
        self.assertEqual((edited["status"], edited["previous"], code), ("committed", reply["sha8"], 0), edited)
        stale, code, _ = self.proc("put", "lending-desk", "30-policy", "--base", reply["sha8"], "-", stdin=POLICY.encode("utf-8"))
        self.assertEqual((stale["code"], code), ("GEMARC_ATLAS_SLICE_003", 1))
        statuses.append(self.proc("status", "lending-desk")[0])
        self.assertGreaterEqual(len(statuses), 6)
        full, code, _ = self.proc("check", "lending-desk", "--mode", "full")
        self.assertEqual((full["clean"], code), (True, 0), full)
        final, code, _ = self.proc("status", "lending-desk")
        self.assertEqual(final["phase"], "review")
        self.assertFalse(final["done"])
        nodes, code, _ = self.proc("index", "lending-desk", "--with-edges")
        self.assertEqual((nodes["total"], code), (7, 0), nodes)   # 4 nodes and their 3 edges
        room, _, _ = self.proc("index", "lending-desk", "--scope", "room:rm.vis")
        self.assertIn("usr.member Actor Member", room["text"])
        req, _, _ = self.proc("index", "lending-desk", "--scope", "req:RQ-1")
        self.assertIn("cases: CS-BORROW-OK", req["text"])
        case, _, _ = self.proc("index", "lending-desk", "--scope", "case:CS-BORROW-OK")
        self.assertIn("svc.loan=Completed", case["text"])
        emitted, code, _ = self.proc("check", "lending-desk", "--emit-model")
        self.assertEqual((emitted["status"], code), ("ok", 0), emitted)
        model = json.loads((self.root / "lending-desk" / "atlas" / "out" / "model.json").read_text(encoding="utf-8"))
        self.assertEqual(am.contract_issues(model), [])
        self.assertEqual(emitted["modelSha8"], am.model_sha8(model))


class SyntheticSessionTests(Base):
    """A 40-node synthetic atlas through ``cli.main``: init, 6+ puts, one failing put repaired by a patch, status after each."""

    def test_forty_node_session(self):
        model = synth.generate("small", 3)
        self.assertEqual(len(model["nodes"]), 40)
        meta = encode.meta_of(model)
        reply, code = self.main("init", "small", "--repos", ",".join(meta["repos"]), "--languages", ",".join(meta["languages"]))
        self.assertEqual((reply["status"], code), ("created", 0), reply)
        files = encode.encode_atl(model, max_bytes=8 * 1024)
        self.assertGreaterEqual(len(files), 6)
        failed = repaired = False
        statuses = []
        for fname, text in files.items():
            slice_ = fname.rsplit(".", 1)[0]
            calls = re.search(r"^(\S+) \| calls \| (\S+) \|.*$", text, re.M)
            if not failed and slice_.endswith("structure") and calls:
                failed = True
                broken = text.replace(calls.group(0), calls.group(0).replace("| calls |", "| calll |", 1), 1)
                bad, code = self.main("put", "small", slice_, "-", stdin=broken)
                self.assertEqual((bad["status"], code), ("invalid", 1))
                state, _ = self.main("status", "small")
                self.assertEqual(state["phase"], "repair")
                statuses.append(state)
                patch = f"## edges\n-{calls.group(1)} | calll | {calls.group(2)}\n{calls.group(0)}\n"
                fix, code = self.main("put", "small", slice_, "--mode", "patch", "-", stdin=patch)
                self.assertEqual((fix["status"], code), ("committed", 0), fix)
                self.assertEqual((fix["patch"]["deleted"], fix["patch"]["added"]), (1, 1))
                repaired = True
            else:
                reply, code = self.main("put", "small", slice_, "-", stdin=text)
                self.assertEqual((reply["status"], code), ("committed", 0), (slice_, reply["diagnostics"]))
            state, _ = self.main("status", "small")
            statuses.append(state)
        self.assertTrue(repaired)
        self.assertGreaterEqual(len(statuses), 7)
        for state in statuses:
            self.assertLessEqual(len(state["next"]), 10)
            self.assertTrue(state["next"][-1].startswith("dims: "))
        full, code = self.main("check", "small", "--mode", "full")
        self.assertEqual((full["clean"], code), (True, 0), full["diagnostics"])
        listing, code = self.main("index", "small")
        self.assertEqual(listing["total"], 40)
        first = model["nodes"][0]["id"]
        ids, _ = self.main("index", "small", "--scope", "ids:" + first[:4])
        self.assertEqual(ids["status"], "ok")
        emitted, code = self.main("check", "small", "--emit-model")
        self.assertEqual((emitted["status"], code), ("ok", 0), emitted)
        via_functions = tools.emit_model(pj.get_workspace(self.root), "small")
        self.assertEqual(via_functions["modelSha8"], emitted["modelSha8"])


if __name__ == "__main__":
    unittest.main()
