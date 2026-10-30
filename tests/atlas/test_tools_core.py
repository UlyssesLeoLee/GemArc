"""The pure tool functions (design 4.1, 5.4, addendum A1 D14): init, put, check, status, index, brief and the size caps.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_tools_core.py"

Scripted end-to-end sessions on the synthetic lending desk and on a 40-node synthetic atlas run through the pure functions
(the same sessions run through the command line in ``test_cli.py``). Synthetic id families only.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import brief as brief_mod
from gemarc_mcp.atlas import encode
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import slices as sl
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.format import rows as rw
from gemarc_mcp.paths import GemArcError

from . import synth

DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
DESK = {name: (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8") for name in ("00-space", "10-structure", "20-scenarios")}
POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"


def sha(project_paths_, config):
    return st.load_snapshot(project_paths_, config).sha8


class Workspace(unittest.TestCase):
    """A temporary workspace with writes allowed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ws = pj.get_workspace(self.root)

    def init(self, name="desk", mode="asis", repos=("main",), languages=("en-US",)):
        reply = tools.init(self.ws, name, "Library lending desk", mode, list(languages), repos=list(repos))
        self.assertEqual(reply["status"], "created", reply)
        self.paths = pj.project_paths(self.ws, name)
        self.name = name
        return reply

    def put(self, slice_, text, **kw):
        return tools.put(self.ws, self.name, slice_, text, **kw)

    def desk(self):
        self.init()
        for name, text in DESK.items():
            self.assertEqual(self.put(name, text)["status"], "committed")

    def snap(self):
        return st.load_snapshot(self.paths, pj.load_config(self.paths))

    def tree(self):
        return {str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(self.root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------------------------------------------
# fit_result
# ---------------------------------------------------------------------------------------------------------------


class FitResultTests(unittest.TestCase):
    def test_a_small_reply_is_returned_unchanged(self):
        r = {"status": "ok", "authority": "local", "lines": ["a", "b"]}
        self.assertEqual(tools.fit_result(r), r)

    def test_the_largest_list_is_cut_and_says_where_to_continue(self):
        r = {"status": "ok", "authority": "local", "lines": [f"line {i} " + "x" * 80 for i in range(400)], "note": "short"}
        out = tools.fit_result(r, 2000, offset=10, items_key="lines")
        self.assertLessEqual(tools.reply_bytes(out), 2000)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["more"], f"offset={10 + len(out['lines'])}")
        self.assertEqual(out["lines"], r["lines"][: len(out["lines"])])
        self.assertEqual(out["note"], "short")

    def test_a_long_text_is_cut_at_a_line_boundary(self):
        text = "\n".join(f"row {i} " + "y" * 60 for i in range(300))
        out = tools.fit_result({"status": "ok", "authority": "local", "text": text}, 1500, items_key="text")
        self.assertLessEqual(tools.reply_bytes(out), 1500)
        self.assertTrue(text.startswith(out["text"]))
        self.assertTrue(out["text"].endswith(tuple("y")))
        self.assertEqual(out["more"], f"offset={len(out['text'].splitlines())}")

    def test_the_hard_cap_wins_over_a_larger_ask_and_multibyte_text_is_counted_in_bytes(self):
        r = {"status": "ok", "authority": "local", "text": "界" * 30000}
        out = tools.fit_result(r, 10 ** 9)
        self.assertLessEqual(tools.reply_bytes(out), tools.HARD_CAP)
        self.assertTrue(out["truncated"])
        self.assertGreater(tools.reply_bytes({"t": "界" * 100}), 300)

    def test_nested_diagnostics_shrink_and_the_envelope_survives(self):
        r = {"status": "ok", "authority": "local", "diagnostics": {"counts": {"error": 1}, "diagnostics": [{"x": "z" * 100}] * 100}}
        out = tools.fit_result(r, 1200)
        self.assertLessEqual(tools.reply_bytes(out), 1200)
        self.assertEqual(out["status"], "ok")
        hopeless = {"status": "ok", "authority": "local", "weird": {"k": 5}, "text": "t"}
        self.assertLessEqual(tools.reply_bytes(tools.fit_result(hopeless, 10)), 10 + 200)


# ---------------------------------------------------------------------------------------------------------------
# init, atlas.toml, write gate
# ---------------------------------------------------------------------------------------------------------------


class InitTests(Workspace):
    def test_init_creates_the_layout_and_refuses_to_overwrite(self):
        reply = self.init()
        self.assertTrue(Path(reply["path"]).is_absolute())
        atlas = self.root / "desk" / "atlas"
        for part in ("atlas.toml", ".gitignore", "slices", ".atlas/red", ".atlas/history"):
            self.assertTrue((atlas / part).exists(), part)
        self.assertEqual((atlas / ".gitignore").read_text(), ".atlas/\ndist/\nout/\n")
        again = tools.init(self.ws, "desk", "x", "asis")
        self.assertEqual(again["status"], "invalid")
        self.assertEqual(again["code"], pj.E_EXISTS)
        self.assertLess(tools.reply_bytes(reply), 1024)

    def test_atlas_toml_round_trips_through_tomllib_including_unicode_and_quotes(self):
        tools.init(self.ws, "uni", 'Title "with" quotes 中文 \\ back', "tobe", ["zh-CN", "en-US"], repos=["main", "web"])
        paths = pj.project_paths(self.ws, "uni")
        cfg = pj.load_config(paths)
        self.assertEqual((cfg.title, cfg.mode, cfg.languages, cfg.repos), ('Title "with" quotes 中文 \\ back', "tobe", ("zh-CN", "en-US"), ("main", "web")))
        self.assertEqual(cfg.meta()["repos"], ["main", "web"])
        text = (paths.config).read_bytes()
        self.assertNotIn(b"\r", text)
        self.assertTrue(text.endswith(b"\n"))
        self.assertNotIn("roots", pj.Config("p", "t", "asis", ("en-US",), "software", ()).meta())   # no aliases: EV-007 stays off

    def test_bad_arguments_are_refused_one_by_one(self):
        cases = [dict(project="../x"), dict(project="a b"), dict(project="con"), dict(project=".."), dict(project="ok", mode="both"),
                 dict(project="ok", languages=["xx-XX"]), dict(project="ok", languages=[]), dict(project="ok", profile="nope"),
                 dict(project="ok", repos=["../etc"]), dict(project="ok", repos=["a", "a"]), dict(project="ok", title="two\nlines")]
        for kw in cases:
            with self.subTest(kw=kw):
                reply = tools.init(self.ws, kw.pop("project"), kw.pop("title", "T"), kw.pop("mode", "asis"), kw.pop("languages", ["en-US"]),
                                   kw.pop("profile", "software"), kw.pop("repos", []))
                self.assertEqual(reply["status"], "invalid", reply)
        self.assertEqual([p.name for p in self.root.iterdir()], [])

    def test_writes_need_allow_write_but_reads_do_not(self):
        self.desk()
        before = self.tree()
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMARC_ALLOW_WRITE")
            for reply in (tools.init(self.ws, "other", "T"), self.put("00-space", DESK["00-space"]), tools.emit_model(self.ws, "desk")):
                self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_002"))
            for reply in (tools.check(self.ws, "desk"), tools.check(self.ws, "desk", mode="full"), tools.status(self.ws, "desk"),
                          tools.index(self.ws, "desk"), tools.brief("format")):
                self.assertEqual(reply["status"], "ok")
        self.assertEqual(self.tree(), before)

    def test_an_uninitialised_project_and_unsafe_names_are_refused(self):
        for reply in (tools.status(self.ws, "nothing"), tools.put(self.ws, "nothing", "a", "#atlas 1\n"), tools.check(self.ws, "../x")):
            self.assertEqual(reply["status"], "invalid")
        self.assertEqual(tools.status(self.ws, "nothing")["code"], pj.E_NOT_FOUND)

    def test_workspace_root_follows_the_environment(self):
        with mock.patch.dict(os.environ, {"GEMARC_WORKSPACE_ROOT": str(self.root)}):
            self.assertEqual(pj.get_workspace().root, self.root.resolve())
        with self.assertRaises(GemArcError):
            pj.get_workspace(self.root / "does-not-exist")


# ---------------------------------------------------------------------------------------------------------------
# put and check replies
# ---------------------------------------------------------------------------------------------------------------


class PutCheckReplyTests(Workspace):
    def test_put_reply_shapes(self):
        self.init()
        ok = self.put("00-space", DESK["00-space"])
        self.assertEqual((ok["status"], ok["authority"], ok["unchanged"]), ("committed", "local", False))
        self.assertEqual(ok["counts"], {"buildings": 2, "floors": 2, "rooms": 2})
        self.assertRegex(ok["sha8"], r"^[0-9a-f]{8}$")
        self.assertIn("next", ok)
        same = self.put("00-space", DESK["00-space"])
        self.assertEqual((same["unchanged"], same["sha8"]), (True, ok["sha8"]))
        bad = self.put("10-structure", DESK["10-structure"].replace("kind=Module", "kind=Modul"))
        self.assertEqual(bad["status"], "invalid")
        self.assertTrue(bad["red"])
        self.assertIn("ST-003", bad["diagnostics"])
        self.assertLessEqual(len(bad["diagnostics"].encode()), 2800)
        self.assertLessEqual(len(bad["next"]), 4)
        self.assertLessEqual(tools.reply_bytes(bad), tools.DEFAULT_CAP)
        fixed = self.put("10-structure", "## nodes\n@set room=rm.desk kind=Module\nsvc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2\n"
                         "svc.stock | Stock service | Knows which copy is on the shelf. | ev=EVD-2\n", mode="patch")
        self.assertEqual(fixed["status"], "committed", fixed)
        self.assertEqual(fixed["patch"]["replaced"], 2)
        self.assertTrue(fixed["red_draft_cleared"])
        conflict = self.put("00-space", DESK["00-space"] + "// c\n", base="deadbeef")
        self.assertEqual((conflict["status"], conflict["code"], conflict["conflict"]), ("invalid", sl.E_CONFLICT, True))
        self.assertEqual(self.put("Bad Name", "#atlas 1\n")["code"], sl.E_NAME)

    def test_check_takes_exactly_one_of_project_and_text(self):
        self.desk()
        for kw in (dict(), dict(project="desk", text="#atlas 1\n")):
            self.assertEqual(tools.check(self.ws, **kw)["status"], "invalid")
        self.assertEqual(tools.check(self.ws, "desk", mode="weird")["status"], "invalid")
        self.assertEqual(tools.check(self.ws, "desk", format="xml")["status"], "invalid")

    def test_inline_text_is_advisory_and_needs_no_project(self):
        reply = tools.check(None, text="#atlas 1\n## nodes: id | name | sub | opts\nsvc.a | A | Alpha. | room=rm.x kind=Modul\n")
        self.assertEqual((reply["status"], reply["authority"], reply["scope"], reply["clean"]), ("ok", "advisory", "grammar+intra-slice", False))
        self.assertIn("ST-003", reply["diagnostics"])
        jl = tools.check(None, text='{"_atlas":1}\n{"_section":"tests","id":"TS-1","title":"One"}\n', format="json")
        self.assertTrue(jl["clean"])
        self.assertIsInstance(jl["diagnostics"], dict)
        self.assertEqual(jl["diagnostics"]["counts"]["error"], 0)

    def test_check_of_a_slice_reads_the_red_draft_and_pages_with_offset(self):
        self.desk()
        rows = "".join(f"gen.a{i} | Gen {i} | Text. | room=rm.desk kind=Modul\n" for i in range(60))
        bad = self.put("30-bad", "#atlas 1\n## nodes: id | name | sub | opts\n" + rows)
        self.assertEqual(bad["status"], "invalid")
        self.assertIn("more (offset=", bad["diagnostics"])
        first = tools.check(self.ws, "desk", slice="30-bad")
        self.assertEqual((first["source"], first["clean"]), ("red draft", False))
        self.assertEqual(first["diagnostics"], bad["diagnostics"])           # the same view as the put showed
        offset = int(re.search(r"offset=(\d+)", first["diagnostics"]).group(1))
        second = tools.check(self.ws, "desk", slice="30-bad", offset=offset)
        self.assertIn("E ST-003 30-bad.atl:", second["diagnostics"])
        self.assertNotEqual(second["diagnostics"], first["diagnostics"])
        self.assertEqual(tools.check(self.ws, "desk", slice="nope")["status"], "invalid")
        self.assertEqual(tools.check(self.ws, "desk", slice="30-bad", format="json")["diagnostics"]["counts"]["error"], 60)

    def test_rule_filter_and_full_mode_with_a_red_draft(self):
        self.desk()
        self.put("30-bad", "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n")
        full = tools.check(self.ws, "desk", mode="full")
        self.assertFalse(full["clean"])
        self.assertIn("ST-009", full["diagnostics"])
        self.assertEqual(full["red_drafts"], ["30-bad.atl"])
        only = tools.check(self.ws, "desk", mode="full", rule="ST-009")
        self.assertEqual(only["rule"], "ST-009")
        self.assertNotIn("ST-003", only["diagnostics"])
        none = tools.check(self.ws, "desk", mode="full", rule="RF")
        self.assertTrue(none["clean"])

    def test_check_never_writes(self):
        self.desk()
        self.put("30-bad", "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n")
        before = self.tree()
        for _ in range(2):
            tools.check(self.ws, "desk")
            tools.check(self.ws, "desk", mode="full")
            tools.check(self.ws, "desk", slice="30-bad")
            tools.check(None, text="#atlas 1\n")
            tools.status(self.ws, "desk")
            tools.status(self.ws, "desk", view="report")
            tools.index(self.ws, "desk")
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.paths.lock.exists())


# ---------------------------------------------------------------------------------------------------------------
# status: next, priority, the completion contract
# ---------------------------------------------------------------------------------------------------------------

BIG_BROKEN = {
    "15-extra": "#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.desk kind=Module\nsvc.extra | Extra | An extra service. | ev=EVD-1\n"
                "fsm.job | Job state | State of a job. | kind=State ev=EVD-1\nsvc.idle | Idle one | Nobody calls it. | ev=EVD-1\n"
                "## edges: from | rel | to | label | opts\nsvc.extra | calls | svc.ghost | call ghost | ev=EVD-1\n@final edges\n"
                "## machines: id | initial | opts\nfsm.job | A | owner=svc.extra\n"
                "## transitions: from | to | opts\n@set machine=fsm.job\nA | B\nC | B\n",
    "25-more": "#atlas 1\n## cases: id | cat | title | opts\nCS-NOEXPECT | normal | A case with no expectation | req=RQ-1\n"
               "## requirements: id | title | source | opts\nRQ-9 | An orphan requirement | nowhere\n",
}


class StatusTests(Workspace):
    def broken_desk(self):
        self.desk()
        # SM-002 is an ERROR in slice mode, so a put would refuse it: the unreachable state arrives as a direct file edit
        (self.paths.slices / "15-extra.atl").write_text(BIG_BROKEN["15-extra"], encoding="utf-8")
        self.assertEqual(self.put("25-more", BIG_BROKEN["25-more"])["status"], "committed")
        bad = self.put("30-bad", "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n")
        self.assertEqual(bad["status"], "invalid")

    def test_next_lists_at_most_ten_lines_in_the_priority_order_of_design_5_4(self):
        self.broken_desk()
        reply = tools.status(self.ws, "desk")
        lines = reply["next"]
        self.assertLessEqual(len(lines), 10)
        self.assertTrue(lines[-1].startswith("dims: "), lines[-1])
        digits = [int(x[0]) for x in lines if x[0].isdigit()]
        self.assertEqual(digits, sorted(digits), lines)
        self.assertEqual(set(digits), {1, 2, 3, 4, 5, 6, 7}, lines)
        self.assertEqual(reply["phase"], "repair")
        self.assertTrue(lines[0].startswith("1 red draft 30-bad"))
        self.assertTrue(any("RQ-001" in x for x in lines if x.startswith("1 ")), lines)
        self.assertLessEqual(max(len(x.encode()) for x in lines), 118)
        self.assertLessEqual(tools.reply_bytes(reply), tools.DEFAULT_CAP)
        self.assertLess(tools.reply_bytes(reply), 2000)

    def test_unresolved_references_alone_are_author_not_repair(self):
        self.desk()
        self.put("15-extra", "#atlas 1\n## edges: from | rel | to | label | opts\nsvc.loan | calls | svc.ghost | call ghost | ev=EVD-1\n")
        reply = tools.status(self.ws, "desk")
        self.assertEqual(reply["phase"], "author")
        self.assertTrue(any(x.startswith("3 undefined node svc.ghost") for x in reply["next"]), reply["next"])

    def test_an_empty_project_is_collect_and_a_to_be_project_follows_the_elicitation_order(self):
        self.init("fresh")
        reply = tools.status(self.ws, "fresh")
        self.assertEqual((reply["phase"], reply["done"]), ("collect", False))
        self.assertTrue(any("00-space" in x for x in reply["next"]))
        self.init("greenfield", mode="tobe", repos=())
        reply = tools.status(self.ws, "greenfield")
        self.assertEqual(reply["phase"], "collect")
        self.assertTrue(any(x.startswith("tobe step 1/7") for x in reply["next"]), reply["next"])
        self.assertTrue(any("requirement facts" in x for x in reply["needsUser"]), reply["needsUser"])
        (self.paths.user).mkdir(parents=True, exist_ok=True)
        (self.paths.user / "req.txt").write_text("The member can borrow a copy.", encoding="utf-8")
        self.assertEqual(tools.status(self.ws, "greenfield")["needsUser"], [])
        self.put("00-space", DESK["00-space"].replace("#atlas 1", "#atlas 1 mode=tobe"))
        self.assertTrue(any(x.startswith("tobe step 2/7") for x in tools.status(self.ws, "greenfield")["next"]))
        self.put("20-req", "#atlas 1 mode=tobe\n## tests: id | title\nTS-1 | A member can borrow\n## requirements: id | title | source | opts\n"
                 'RQ-1 | Borrow | "The member can borrow a copy." | tests=TS-1\n')
        self.assertTrue(any(x.startswith("tobe step 4/7") for x in tools.status(self.ws, "greenfield")["next"]))

    def test_done_requires_every_condition_of_the_contract(self):
        self.desk()
        reply = tools.status(self.ws, "desk")
        self.assertEqual((reply["phase"], reply["done"]), ("author", False))
        self.assertIn("completeness-block:1", reply["blockers"])
        self.assertTrue(any("ch9.nfr" in x for x in reply["next"]))
        self.assertEqual(self.put("30-policy", POLICY)["status"], "committed")
        reply = tools.status(self.ws, "desk")
        self.assertEqual((reply["phase"], reply["done"], reply["blockers"]), ("review", False, ["review:missing"]))
        self.assertTrue(reply["next"][0].startswith("review: "))
        cfg = pj.load_config(self.paths)
        self.paths.review.write_text(json.dumps({"modelSha": "00000000", "findings": [], "fixed": []}), encoding="utf-8")
        stale = tools.status(self.ws, "desk")
        self.assertEqual((stale["phase"], stale["blockers"]), ("review", ["review:stale"]))
        self.paths.review.write_text(json.dumps({"modelSha": sha(self.paths, cfg), "findings": [], "fixed": [], "notes": "ok"}), encoding="utf-8")
        done = tools.status(self.ws, "desk")
        self.assertEqual((done["phase"], done["done"], done["blockers"], done["needsUser"]), ("done", True, [], []))
        self.assertTrue(done["next"][0].startswith("done"))
        self.put("31-more", "#atlas 1\n## tests: id | title\nTS-2 | Another\n")            # the model changed: the review no longer counts
        again = tools.status(self.ws, "desk")
        self.assertEqual((again["phase"], again["done"]), ("review", False))

    def test_the_review_record_may_carry_the_full_sha(self):
        self.desk()
        self.put("30-policy", POLICY)
        snap = self.snap()
        self.paths.review.write_text(json.dumps({"modelSha": am.model_sha(snap.model)}), encoding="utf-8")
        self.assertTrue(tools.status(self.ws, "desk")["done"])
        self.paths.review.write_text("not json", encoding="utf-8")
        self.assertEqual(tools.status(self.ws, "desk")["phase"], "review")
        self.paths.review.write_text(json.dumps({"modelSha": "abc"}), encoding="utf-8")
        self.assertFalse(tools.status(self.ws, "desk")["done"])

    def reviewed(self):
        self.desk()
        self.put("30-policy", POLICY)
        self.paths.review.write_text(json.dumps({"modelSha": self.snap().sha8}), encoding="utf-8")
        self.assertTrue(tools.status(self.ws, "desk")["done"])

    def test_a_red_draft_or_an_error_or_a_block_each_stop_done(self):
        self.reviewed()
        self.put("40-bad", "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n")
        reply = tools.status(self.ws, "desk")
        self.assertEqual((reply["done"], reply["phase"]), (False, "repair"))
        self.assertIn("red-drafts:1", reply["blockers"])
        (self.paths.red / "40-bad.atl").unlink()
        self.put("40-bad", "#atlas 1\n## requirements: id | title | source | opts\nRQ-X | Nothing | nowhere\n")
        reply = tools.status(self.ws, "desk")
        self.assertFalse(reply["done"])
        self.assertTrue(any(b.startswith("check-full") for b in reply["blockers"]), reply["blockers"])

    def test_unresolved_tbds_are_needs_user_until_the_user_decides(self):
        self.reviewed()
        self.put("32-tbd", "#atlas 1\n## tbds: id | title | decision | opts\nTBD-1 | Late fee rule | Open: who pays late fees? |\n")
        reply = tools.status(self.ws, "desk")
        self.assertEqual((reply["phase"], reply["done"]), ("needs-user", False))
        self.assertTrue(reply["needsUser"][0].startswith("decide TBD TBD-1: Late fee rule"), reply["needsUser"])
        self.assertTrue(reply["next"][0].startswith("ask the user: decide TBD TBD-1"))
        self.put("33-decision", "#atlas 1\n## policies: id | text | opts\nPL-D-1 | The member pays late fees. | src=TBD-1 by=user kind=constraint\n")
        reply = tools.status(self.ws, "desk")
        self.assertEqual(reply["phase"], "needs-user", "a by=user policy that quotes nothing the user said decides nothing")
        self.paths.user.mkdir(parents=True, exist_ok=True)
        (self.paths.user / "answer.txt").write_text("Asked about late fees the user said: the member pays late fees.\n", encoding="utf-8")
        self.put("33-decision", "#atlas 1\n## policies: id | text | opts\n"
                 "PL-D-1 | The user decided: 'the member pays late fees'. | src=TBD-1 by=user kind=constraint\n")
        reply = tools.status(self.ws, "desk")
        self.assertEqual(reply["needsUser"], [])
        self.assertEqual(reply["phase"], "review")                                    # the model changed since the review record
        self.paths.review.write_text(json.dumps({"modelSha": self.snap().sha8}), encoding="utf-8")
        self.assertTrue(tools.status(self.ws, "desk")["done"])
        self.put("34-ai-guess", "#atlas 1\n## tbds: id | title | decision | opts\nTBD-2 | Fee cap | Open: what is the cap? |\n"
                 "## policies: id | text | opts\nPL-D-2 | Cap is ten. | src=TBD-2 by=ai kind=constraint\n")
        self.assertEqual(tools.status(self.ws, "desk")["phase"], "needs-user")        # an AI-written policy decides nothing

    def test_pluggable_gates_default_to_not_applicable_and_block_when_red(self):
        self.reviewed()
        dims = tools.status(self.ws, "desk", view="dims")
        self.assertEqual(dims["required"], ["1D", "2D", "3D"])
        self.assertEqual({g["applicable"] for g in dims["gates"].values()}, {False})
        self.assertFalse(dims["dimensions"]["4D"]["ready"])
        with mock.patch.dict(st.GATES, clear=False):
            st.register_gate("build", lambda snap: {"applicable": True, "ok": False, "detail": "dist is missing"})
            reply = tools.status(self.ws, "desk")
            self.assertEqual((reply["phase"], reply["done"]), ("build", False))
            self.assertIn("build: dist is missing", reply["next"][0])
            st.register_gate("build", lambda snap: {"applicable": True, "ok": True, "detail": ""})
            self.assertTrue(tools.status(self.ws, "desk")["done"])
            st.register_gate("browser", lambda snap: 1 / 0)
            self.assertFalse(tools.status(self.ws, "desk")["done"])
        self.assertTrue(tools.status(self.ws, "desk")["done"])
        with self.assertRaises(ValueError):
            st.register_gate("nonsense", lambda snap: {})

    def test_sealed_expectations_make_4d_and_5d_required_once_the_oracle_gate_is_applicable(self):
        self.reviewed()
        seals = {"seals": {"c1": {"kind": "case", "state": "sealed"}, "i1": {"kind": "intervention", "state": "sealed"},
                           "c2": {"kind": "case", "state": "revised"}}}
        self.paths.oracle.write_text(json.dumps(seals), encoding="utf-8")
        reply = tools.status(self.ws, "desk", view="oracle")
        self.assertEqual((reply["cases"], reply["interventions"], reply["revised"], reply["unconfirmed"]), (1, 1, 1, 3))
        dims = tools.status(self.ws, "desk", view="dims")
        self.assertTrue(dims["dimensions"]["4D"]["ready"] and dims["dimensions"]["5D"]["ready"])
        self.paths.oracle.write_text("{}", encoding="utf-8")
        with mock.patch.dict(st.GATES):
            st.register_gate("oracle", lambda snap: {"applicable": True, "ok": True, "detail": ""})
            reply = tools.status(self.ws, "desk")
            self.assertEqual(tools.status(self.ws, "desk", view="dims")["required"], ["1D", "2D", "3D", "4D", "5D"])
            self.assertFalse(reply["done"])
            self.assertIn("dims:4D,5D", reply["blockers"])
            self.assertEqual(reply["phase"], "author")

    def test_report_dims_oracle_and_evidence_views(self):
        self.desk()
        report = tools.status(self.ws, "desk", view="report")
        self.assertEqual(report["counts"]["BLOCK"], 1)
        self.assertFalse(report["ready"])
        self.assertTrue(any(x.startswith("BLOCK ch9.nfr") for x in report["slots"]))
        self.assertTrue(all(x.startswith("BLOCK ch9") or "ch9" in x for x in tools.status(self.ws, "desk", view="report", scope="ch9")["slots"]))
        oracle = tools.status(self.ws, "desk", view="oracle")
        self.assertEqual((oracle["present"], oracle["total"]), (False, 0))
        evidence = tools.status(self.ws, "desk", view="evidence")
        self.assertEqual((evidence["evidences"], evidence["sourceCode"], evidence["stamped"]), (3, 2, 0))
        self.assertEqual(sorted(evidence["unstamped"]), ["EVD-1", "EVD-2"])
        self.assertEqual(tools.status(self.ws, "desk", view="weird")["status"], "invalid")


# ---------------------------------------------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------------------------------------------


class IndexTests(Workspace):
    def test_scopes_on_the_lending_desk(self):
        self.desk()
        nodes = tools.index(self.ws, "desk")
        self.assertEqual(nodes["total"], 4)
        self.assertIn("svc.loan Module Loan service", nodes["text"])
        self.assertEqual(tools.index(self.ws, "desk", ids=["svc.stock"])["text"], "svc.stock Module Stock service")
        self.assertEqual(tools.index(self.ws, "desk", ids="svc.stock,usr.member")["total"], 2)
        edges = tools.index(self.ws, "desk", with_edges=True)
        self.assertIn("usr.member calls svc.loan borrow a copy", edges["text"])
        self.assertEqual(edges["total"], 7)
        room = tools.index(self.ws, "desk", scope="room:rm.vis")["text"].splitlines()
        self.assertEqual(room, ["room rm.vis Members floor=lvl.out", "usr.member Actor Member"])
        req = tools.index(self.ws, "desk", scope="req:RQ-1")["text"]
        self.assertIn("cases: CS-BORROW-OK", req)
        self.assertIn("svc.stock Module Stock service", req)
        case = tools.index(self.ws, "desk", scope="case:CS-BORROW-OK")["text"]
        self.assertIn("2 Call svc.loan -> svc.stock R-CHECK", case)
        self.assertIn("svc.loan=Completed", case)
        ids = tools.index(self.ws, "desk", scope="ids:svc.")["text"].splitlines()
        self.assertIn("node svc.loan", ids)
        self.assertIn("edge svc.loan:calls:svc.stock", ids)
        self.assertNotIn("node usr.member", ids)
        for bad in ("room:nope", "req:nope", "case:nope", "weird", "room:", "ids"):
            with self.subTest(scope=bad):
                self.assertEqual(tools.index(self.ws, "desk", scope=bad)["status"], "invalid")
        self.assertEqual(tools.index(self.ws, "desk", ids=["nope"])["status"], "invalid")

    def test_expanded_detail_carries_the_file_and_line_that_define_each_record(self):
        self.desk()
        rec = [json.loads(x) for x in tools.index(self.ws, "desk", detail="expanded")["text"].splitlines()]
        self.assertEqual(rec[0]["at"], "10-structure.atl:" + str(DESK["10-structure"].split("\n").index("svc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2") + 1))
        self.assertEqual((rec[0]["id"], rec[0]["roomId"]), ("svc.loan", "rm.desk"))
        case = tools.index(self.ws, "desk", scope="case:CS-BORROW-OK", detail="expanded")["text"].splitlines()
        first_event = DESK["20-scenarios"].split("\n").index("Call | usr.member | svc.loan | ruleId=R-BORROW") + 1
        self.assertEqual(json.loads(case[1])["at"], f"20-scenarios.atl:{first_event}")
        self.assertEqual(tools.index(self.ws, "desk", detail="odd")["status"], "invalid")

    def test_paging_covers_every_node_once_within_the_cap(self):
        self.init("big", repos=())
        model = synth.generate("reference", 5)
        for name, text in encode.encode_atl(model, max_bytes=60 * 1024).items():      # a machine and its transitions share a slice
            self.assertEqual(self.put(name.rsplit(".", 1)[0], text)["status"], "committed", name)
        seen, offset, pages = [], 0, 0
        while True:
            page = tools.index(self.ws, "big", offset=offset)
            self.assertLessEqual(tools.reply_bytes(page), tools.INDEX_CAP)
            seen.extend(page["text"].splitlines())
            pages += 1
            if "more" not in page:
                break
            offset = int(page["more"].split("=")[1])
            self.assertEqual(offset, len(seen))
        self.assertEqual(len(seen), len(model["nodes"]))
        self.assertGreater(pages, 1)
        self.assertEqual({x.split()[0] for x in seen}, {n["id"] for n in model["nodes"]})


# ---------------------------------------------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------------------------------------------


class BriefTests(unittest.TestCase):
    def test_the_work_order_and_every_section_fit_their_caps_in_both_languages(self):
        for lang in brief_mod.LANGUAGES:
            work = tools.brief("", lang)
            self.assertEqual((work["status"], work["section"], work["lang"]), ("ok", "all", lang))
            self.assertLessEqual(work["bytes"], brief_mod.ALL_CAP, lang)
            self.assertLessEqual(tools.reply_bytes(work), tools.BRIEF_CAP, lang)
            self.assertNotIn("truncated", work)
            for section in brief_mod.SECTIONS:
                one = tools.brief(section, lang)
                self.assertLessEqual(one["bytes"], brief_mod.SECTION_CAP, (section, lang, one["bytes"]))
                self.assertLessEqual(tools.reply_bytes(one), tools.DEFAULT_CAP, (section, lang))
                self.assertNotIn("truncated", one)
                self.assertNotIn("{{", one["text"])

    def test_the_work_order_tells_the_ai_the_four_things_it_must_know(self):
        for lang in brief_mod.LANGUAGES:
            text = tools.brief("all", lang)["text"]
            self.assertIn("11", text)                                       # nodes per slice
            self.assertRegex(text, r"about 11 nodes|约 11 个节点")
            self.assertIn("DV-001", text)                                    # derived: never write
            self.assertIn("mode=patch", text)                                # repair by patch
            self.assertIn("status view=next", text)                          # how to read next
            self.assertIn("needs-user", text)
            for word in ("done", "phase", "next", "blockers", "needsUser"):
                self.assertIn(word, text)

    def test_generated_parts_follow_the_code(self):
        fmt = tools.brief("format", "en-US")["text"]
        for spec in rw.SECTIONS.values():
            self.assertIn(spec.header, fmt)
            for option in spec.option_names:
                self.assertIn(option, fmt)
        derived = tools.brief("derived", "zh-CN")["text"]
        for name, keys in rw.DERIVED.items():
            for key in keys:
                self.assertIn(key, derived)
        vocab_text = tools.brief("vocab", "en-US")["text"]
        from gemarc_mcp.atlas import vocab

        for group in ("kinds", "relations", "verification", "eventTypes", "interventionTypes"):
            for ident in vocab.default_profile().ids(group):
                self.assertIn(ident, vocab_text)

    def test_unknown_section_and_language_fallback(self):
        bad = tools.brief("nonsense")
        self.assertEqual((bad["status"], bad["code"]), ("invalid", tools.E_ARG))
        ja = tools.brief("format", "ja-JP")
        self.assertEqual(ja["lang"], "en-US")
        self.assertIn("note", ja)
        self.assertEqual(tools.brief("ALL", "zh-CN")["section"], "all")

    def test_every_brief_file_exists_in_both_languages(self):
        names = {p.name for p in brief_mod.BRIEF_DIR.glob("*.md")}
        self.assertEqual(names, {f"{s}.{l}.md" for s in ("all",) + brief_mod.SECTIONS for l in brief_mod.LANGUAGES})
        for p in brief_mod.BRIEF_DIR.glob("*.md"):
            data = p.read_bytes()
            self.assertNotIn(b"\r", data, p.name)
            self.assertTrue(data.endswith(b"\n"), p.name)


# ---------------------------------------------------------------------------------------------------------------
# Size caps on a 5,000-defect input
# ---------------------------------------------------------------------------------------------------------------

DEFECT_ROWS = (
    ("nodes", "gen.a{i} | Gen | Text. | room=rm.desk kind=Modul"),                       # ST-003
    ("nodes", "svc.loan | Twin {i} | Text. | room=rm.desk kind=Module"),                  # ST-002
    ("nodes", "gen.c{i} | Only two"),                                                     # ST-005
    ("nodes", "gen.d{i} | Gen | Text ev=EVD-1 | room=rm.desk kind=Module"),               # ST-006
    ("edges", "svc.loan | calls | gen.nowhere{i} | go | ev=EVD-1"),                       # RF-002
    ("nodes", "gen.g{i} | Gen | Text. | room=rm.desk kind=Module ev=XVD-{i}"),            # RF-003
    ("requirements", "RQ-G{i} | Gen | source | nodes=gen.missing{i}"),                   # RF-005
    ("nodes", "gen.n{i} | Gen | Text. | room=rm.desk kind=Module portIds=[]"),            # DV-001
)
HEADERS = {"nodes": "## nodes: id | name | sub | opts", "edges": "## edges: from | rel | to | label | opts",
           "requirements": "## requirements: id | title | source | opts"}


def defect_files(count: int, limit: int = 56 * 1024) -> dict:
    files, current, size, number = {}, {}, 0, 0
    for i in range(count):
        section, row = DEFECT_ROWS[i % len(DEFECT_ROWS)]
        text = row.format(i=i)
        if size + len(text) + 1 > limit:
            files[f"4{number:02d}-defects"] = current
            current, size, number = {}, 0, number + 1
        current.setdefault(section, []).append(text)
        size += len(text) + 1
    files[f"4{number:02d}-defects"] = current
    out = {}
    for name, sections in files.items():
        out[name] = "#atlas 1\n" + "".join(HEADERS[s] + "\n" + "\n".join(rows) + "\n" for s, rows in sections.items())
    return out


class CapTests(Workspace):
    def test_every_reply_respects_its_cap_on_a_5000_defect_input(self):
        self.desk()
        files = defect_files(5000)
        for name, text in files.items():
            self.assertLess(len(text.encode()), sl.MAX_SLICE_BYTES, name)
            (self.paths.slices / f"{name}.atl").write_bytes(text.encode())       # direct file edits are supported
        full = tools.check(self.ws, "desk", mode="full")
        self.assertFalse(full["clean"])
        self.assertLessEqual(tools.reply_bytes(full), tools.DEFAULT_CAP)
        self.assertGreaterEqual(full["counts"]["error"], 5000)
        as_json = tools.check(self.ws, "desk", mode="full", format="json")
        self.assertLessEqual(tools.reply_bytes(as_json), tools.DEFAULT_CAP)
        for offset in (0, 7, 2500, 4999, 10 ** 6):
            page = tools.check(self.ws, "desk", mode="full", offset=offset)
            self.assertLessEqual(tools.reply_bytes(page), tools.DEFAULT_CAP, offset)
        reply = tools.status(self.ws, "desk")
        self.assertLessEqual(tools.reply_bytes(reply), tools.DEFAULT_CAP)
        self.assertLessEqual(len(reply["next"]), 10)
        for view in ("report", "dims", "oracle", "evidence"):
            self.assertLessEqual(tools.reply_bytes(tools.status(self.ws, "desk", view=view)), tools.DEFAULT_CAP, view)
        text = next(iter(files.values()))
        put = self.put("50-new", text)
        self.assertEqual(put["status"], "invalid")
        self.assertLessEqual(tools.reply_bytes(put), tools.DEFAULT_CAP)
        self.assertLessEqual(tools.reply_bytes(tools.index(self.ws, "desk", with_edges=True)), tools.INDEX_CAP)
        self.assertLessEqual(tools.reply_bytes(tools.index(self.ws, "desk", detail="expanded", with_edges=True)), tools.EXPANDED_CAP + 400)
        print(f"\n[5,000 defects] check full {tools.reply_bytes(full)} B, json {tools.reply_bytes(as_json)} B, status next {tools.reply_bytes(reply)} B, put {tools.reply_bytes(put)} B")


# ---------------------------------------------------------------------------------------------------------------
# End-to-end sessions through the pure functions
# ---------------------------------------------------------------------------------------------------------------



class SessionTests(Workspace):
    def test_lending_desk_session(self):
        self.init("lending-desk")
        trail = []
        for name, text in DESK.items():
            if name == "10-structure":
                bad = tools.put(self.ws, "lending-desk", name, text.replace("kind=Module", "kind=Modul"))
                self.assertEqual(bad["status"], "invalid")
                trail.append(tools.status(self.ws, "lending-desk"))
                self.assertEqual(trail[-1]["phase"], "repair")
                fix = tools.put(self.ws, "lending-desk", name, "## nodes\n@set room=rm.desk kind=Module\nsvc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2\n"
                                "svc.stock | Stock service | Knows which copy is on the shelf. | ev=EVD-2\n@set kind=Storage\n"
                                "tbl.loan | Loan table | One row per open loan. | ev=EVD-1\n@set room=rm.vis kind=Actor\n"
                                "usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Inferred\n", mode="patch")
                self.assertEqual(fix["status"], "committed", fix)
            else:
                self.assertEqual(tools.put(self.ws, "lending-desk", name, text)["status"], "committed")
            trail.append(tools.status(self.ws, "lending-desk"))
        self.assertEqual(trail[-1]["phase"], "author")                     # chapter 9 has no text yet
        policy = tools.put(self.ws, "lending-desk", "30-policy", POLICY)
        self.assertEqual(policy["status"], "committed", policy)
        trail.append(tools.status(self.ws, "lending-desk"))
        self.assertEqual(trail[-1]["phase"], "review")
        again = tools.put(self.ws, "lending-desk", "30-policy", POLICY)    # idempotent: same text, same sha8
        self.assertEqual((again["unchanged"], again["sha8"]), (True, policy["sha8"]))
        edited = tools.put(self.ws, "lending-desk", "30-policy", POLICY.replace("two seconds", "three seconds"), base=policy["sha8"])
        self.assertEqual((edited["status"], edited["previous"]), ("committed", policy["sha8"]))
        stale = tools.put(self.ws, "lending-desk", "30-policy", POLICY, base=policy["sha8"])     # the slice moved on: nothing written
        self.assertEqual((stale["status"], stale["code"]), ("invalid", sl.E_CONFLICT))
        self.assertIn("three seconds", (pj.project_paths(self.ws, "lending-desk").slices / "30-policy.atl").read_text(encoding="utf-8"))
        trail.append(tools.status(self.ws, "lending-desk"))
        self.assertGreaterEqual(len(trail), 6)
        for reply in trail:
            self.assertLessEqual(len(reply["next"]), 10)
            self.assertLessEqual(tools.reply_bytes(reply), tools.DEFAULT_CAP)
        full = tools.check(self.ws, "lending-desk", mode="full")
        self.assertTrue(full["clean"], full["diagnostics"])
        self.assertIn("svc.loan=Completed", tools.index(self.ws, "lending-desk", scope="case:CS-BORROW-OK")["text"])
        self.assertIn("usr.member Actor Member", tools.index(self.ws, "lending-desk", scope="room:rm.vis")["text"])
        self.assertIn("cases: CS-BORROW-OK", tools.index(self.ws, "lending-desk", scope="req:RQ-1")["text"])
        emitted = tools.emit_model(self.ws, "lending-desk")
        self.assertEqual(emitted["status"], "ok", emitted)
        model = json.loads((self.paths.out / "model.json").read_text(encoding="utf-8"))
        self.assertEqual(am.contract_issues(model), [])
        self.assertEqual({n["id"] for n in model["nodes"]}, {"svc.loan", "svc.stock", "tbl.loan", "usr.member"})
        self.assertEqual(emitted["modelSha8"], am.model_sha8(model))

    def run_synthetic(self, name):
        model = synth.generate("small", 3)
        meta = encode.meta_of(model)
        self.assertEqual(len(model["nodes"]), 40)
        self.init(name, repos=meta["repos"], languages=meta["languages"])
        files = encode.encode_atl(model, max_bytes=8 * 1024)
        self.assertGreaterEqual(len(files), 6)
        statuses = []
        failed = repaired = False
        for fname, text in files.items():
            slice_ = fname.rsplit(".", 1)[0]
            if not failed and slice_.endswith("structure") and re.search(r"^\S+ \| calls \| \S+ \|", text, re.M):
                failed = True
                good_row = re.search(r"^(\S+) \| calls \| (\S+) \|.*$", text, re.M)
                broken = text.replace(good_row.group(0), good_row.group(0).replace("| calls |", "| calll |", 1), 1)
                bad = self.put(slice_, broken)
                self.assertEqual(bad["status"], "invalid")
                statuses.append(tools.status(self.ws, name))
                self.assertEqual(statuses[-1]["phase"], "repair")
                self.assertIn("red draft", statuses[-1]["next"][0])
                wrong = f"-{good_row.group(1)} | calll | {good_row.group(2)}"
                fix = self.put(slice_, f"## edges\n{wrong}\n{good_row.group(0)}\n", mode="patch")
                self.assertEqual(fix["status"], "committed", fix)
                self.assertEqual((fix["patch"]["deleted"], fix["patch"]["added"]), (1, 1))
                repaired = True
            else:
                reply = self.put(slice_, text)
                self.assertEqual(reply["status"], "committed", (slice_, reply["diagnostics"]))
            statuses.append(tools.status(self.ws, name))
        self.assertTrue(repaired)
        for reply in statuses:
            self.assertLessEqual(len(reply["next"]), 10)
            self.assertTrue(reply["next"][-1].startswith("dims: "))
            self.assertLessEqual(tools.reply_bytes(reply), tools.DEFAULT_CAP)
        full = tools.check(self.ws, name, mode="full")
        self.assertTrue(full["clean"], full["diagnostics"])
        self.assertEqual(full["counts"]["error"], 0)
        self.assertEqual(tools.index(self.ws, name)["total"], 40)
        self.assertEqual(tools.index(self.ws, name, scope="ids:" + model["nodes"][0]["id"][:4])["status"], "ok")
        emitted = tools.emit_model(self.ws, name)
        self.assertEqual(emitted["status"], "ok", emitted)
        out = json.loads((self.paths.out / "model.json").read_text(encoding="utf-8"))
        self.assertEqual(am.contract_issues(out), [])
        self.assertEqual({n["id"] for n in out["nodes"]}, {n["id"] for n in model["nodes"]})
        self.assertEqual(sorted(e["id"] for e in out["edges"]), sorted(e["id"] for e in model["edges"]))
        self.assertEqual(len(out["cases"]), len(model["cases"]))
        return statuses, (self.paths.out / "model.json").read_bytes()

    def test_forty_node_session_with_a_failing_put_repaired_by_patch(self):
        statuses, data = self.run_synthetic("small")
        self.assertGreaterEqual(len(statuses), 7)

    def test_the_emitted_model_is_deterministic_across_runs(self):
        _, first = self.run_synthetic("small")
        with tempfile.TemporaryDirectory() as other:
            self.ws = pj.get_workspace(Path(other))
            _, second = self.run_synthetic("small")
        self.assertEqual(first, second)

    def test_emit_model_refuses_a_model_with_errors(self):
        self.desk()
        self.put("30-policy", POLICY)
        (self.paths.slices / "40-bad.atl").write_text("#atlas 1\n## requirements: id | title | source | opts\nRQ-X | Nothing | nowhere\n", encoding="utf-8")
        reply = tools.emit_model(self.ws, "desk")
        self.assertEqual((reply["status"], reply["code"]), ("invalid", tools.E_EMIT))
        self.assertFalse((self.paths.out / "model.json").exists())

    def test_reference_scale_project_checks_inside_the_time_budgets(self):
        self.init("big", repos=())
        model = synth.generate("reference", 11)
        puts = 0
        for name, text in encode.encode_atl(model, max_bytes=60 * 1024).items():
            self.assertEqual(self.put(name.rsplit(".", 1)[0], text)["status"], "committed")
            puts += 1
        t0 = time.perf_counter()
        slice_check = tools.check(self.ws, "big", slice=name.rsplit(".", 1)[0])
        t_slice = time.perf_counter() - t0
        t0 = time.perf_counter()
        full = tools.check(self.ws, "big", mode="full")
        t_full = time.perf_counter() - t0
        t0 = time.perf_counter()
        status = tools.status(self.ws, "big")
        t_status = time.perf_counter() - t0
        self.assertTrue(full["clean"], full["diagnostics"])
        self.assertTrue(slice_check["clean"])
        self.assertLess(t_full, 1.5)
        self.assertLess(t_slice, 0.5)
        print(f"\n[reference scale {len(model['nodes'])} nodes, {puts} puts] check slice {t_slice:.3f} s, check full {t_full:.3f} s, status {t_status:.3f} s")


if __name__ == "__main__":
    unittest.main()
