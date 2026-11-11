"""The six tools of batch 3 as functions (design 4.1): ``doc``, ``outline``, ``evidence``, ``build_all`` (build plus the oracle sheet),
``verify`` (gate stamp) and the write gate; reply caps at the reference scale; reads never write.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_tools_b3_tools.py"

Synthetic id families only (the lending desk, the generator's reference preset). The real browser runs once at the end (it SKIPS loudly
without Edge or Chrome: a skip is not a pass).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, oracle
from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import review
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.gate import smoke

from . import synth
from .test_oracle_support import OracleProject, consistent, desk_files, slice_texts, tree_digest
from .test_tools_b3_gates import POLICY, SOURCES, Flow

HARD = tools.HARD_CAP
BROWSER = smoke.find_browser()


def size(reply: dict) -> int:
    return tools.reply_bytes(reply)


class DocToolTests(Flow):
    def setUp(self):
        super().setUp()
        self.authored()

    def test_the_outline_is_within_one_kib_and_says_where_the_slots_are(self):
        reply = tools.doc(self.ws, self.name)
        self.assertEqual(reply["status"], "ok")
        self.assertLessEqual(size(reply), 1024)
        self.assertEqual((reply["level"], reply["lang"], reply["total"]), ("BD", "en-US", 12))
        self.assertEqual(len(reply["ch"]), 12)
        self.assertEqual(reply["markers"], 0)
        self.assertEqual(reply["sha8"], self.snap().sha8)

    def test_units_levels_and_pages_map_to_the_document_core(self):
        base = tools.doc(self.ws, self.name)["no"]
        for unit in ("req:RQ-1", "case:CS-BORROW-OK", "node:svc.loan", "svc.loan"):
            reply = tools.doc(self.ws, self.name, unit=unit, level="DD")
            self.assertEqual(reply["status"], "ok", (unit, reply))
            self.assertNotEqual(reply["no"], base, unit)
        for level in ("REQ", "BD", "DD"):
            self.assertEqual(tools.doc(self.ws, self.name, level=level)["level"], level)
        page = tools.doc(self.ws, self.name, chapter="2")
        self.assertEqual((page["status"], page["chapter"], page["page"]), ("ok", 2, 1))
        self.assertTrue(page["text"].startswith("## 2."), page["text"][:40])
        self.assertEqual(tools.doc(self.ws, self.name, chapter="2", page=0)["page"], 1)

    def test_refusals_are_invalid_and_never_raise(self):
        for kwargs in ({"level": "XX"}, {"unit": "node:nope.such"}, {"chapter": "13"}, {"chapter": "2", "page": 9}):
            reply = tools.doc(self.ws, self.name, **kwargs)
            self.assertEqual(reply["status"], "invalid", kwargs)
            self.assertTrue(reply["code"].startswith("GEMARC_ATLAS_DOC_"), reply)
        self.assertEqual(tools.doc(self.ws, self.name, chapter="two")["code"], tools.E_ARG)
        self.assertEqual(tools.doc(self.ws, "missing")["status"], "invalid")

    def test_the_outline_never_carries_an_engine_result(self):
        text = json.dumps(tools.doc(self.ws, self.name, level="DD"))
        self.assertNotIn("trace", text)
        self.assertNotIn("verdict", text)


class OutlineToolTests(OracleProject):
    def setUp(self):
        super().setUp()
        self.src = self.root / "srcroot"
        (self.src / "app").mkdir(parents=True)
        (self.src / "app" / "big.py").write_text("".join(f'def handler_{i}(request):\n    return "ok {i}"\n\n' for i in range(400)), encoding="utf-8")
        (self.src / "app" / "quotes.py").write_text("".join(f'def f{i}(x):\n    return "a \\"quoted\\" value {i}"\n' for i in range(300)), encoding="utf-8")
        (self.src / ".env").write_text("API_TOKEN=abc123secretvalue\nHOST=example\n", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": f"main={self.src}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_page_is_within_4_kb_and_the_pages_cover_every_entry_once(self):
        seen, offset, pages = [], 0, 0
        while True:
            reply = tools.outline("main", "app/big.py", offset)
            self.assertEqual(reply["status"], "ok", reply)
            self.assertLessEqual(size(reply), 4500)
            self.assertLessEqual(len(reply["text"].encode("utf-8")), 4096)
            seen += [line for line in reply["text"].splitlines() if line.startswith("L")]
            pages += 1
            if "next" not in reply:
                break
            self.assertEqual(reply["more"], f"offset={reply['next']}")
            offset = reply["next"]
        self.assertEqual(len(seen), 400)
        self.assertGreater(pages, 2)
        self.assertEqual(len(set(seen)), 400)

    def test_quotes_in_anchors_do_not_push_a_page_over_the_cap(self):
        offset = 0
        while True:
            reply = tools.outline("main", "app/quotes.py", offset)
            self.assertEqual(reply["status"], "ok")
            self.assertLessEqual(size(reply), 4500)
            self.assertNotIn("truncated", reply)
            if "next" not in reply:
                break
            offset = reply["next"]

    def test_the_root_is_an_alias_never_a_path_and_nothing_escapes(self):
        for root in (str(self.src), "main/..", "C:\\x", "..", ""):
            reply = tools.outline(root, "app/big.py")
            self.assertEqual(reply["status"], "invalid", root)
            self.assertTrue(reply["code"].startswith("GEMARC_ATLAS_ROOT_"), reply)
        self.assertEqual(tools.outline("web", "app/big.py")["status"], "invalid")
        for path in ("../secret.txt", "/etc/passwd", "C:/Windows/win.ini", "app/../../x.py", "nope.py", ""):
            self.assertEqual(tools.outline("main", path)["status"], "invalid", path)
        self.assertEqual(tools.outline("main", "app/big.py", offset=-1)["status"], "invalid")
        self.assertEqual(tools.outline("main", "app/big.py", offset=10 ** 6)["status"], "ok")

    def test_secrets_are_redacted(self):
        reply = tools.outline("main", ".env")
        self.assertEqual(reply["status"], "ok")
        self.assertIn("API_TOKEN", reply["text"])
        self.assertNotIn("abc123secretvalue", json.dumps(reply))

    def test_it_reads_no_project_and_writes_nothing(self):
        before = tree_digest(self.src)
        tools.outline("main", "app/big.py")
        self.assertEqual(tree_digest(self.src), before)


class EvidenceToolTests(Flow):
    def test_it_refuses_without_the_write_gate_and_changes_nothing(self):
        self.authored()
        before = tree_digest(self.paths.project)
        with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": ""}):
            reply = tools.evidence(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_002"))
        self.assertEqual(tree_digest(self.paths.project), before)

    def test_it_stamps_what_passes_and_lists_what_does_not_with_a_hint_never_applied(self):
        edits = [("10-structure", "EVD-2 | src/stock.py | def find_copy( | shelf lookup", "EVD-2 | src/stock.py | def find_copies( | shelf lookup")]
        self.authored(edits=edits)
        reply = tools.evidence(self.ws, self.name)
        self.assertEqual((reply["rows"], reply["pass"], reply["fail"]), (3, 2, 1), reply)
        self.assertLessEqual(size(reply), tools.EVIDENCE_CAP)
        self.assertTrue(any(line.startswith("FAIL EVD-2") for line in reply["lines"]), reply["lines"])
        self.assertTrue(any("fix each FAIL row" in x for x in reply["next"]))
        self.assertEqual(reply["claims"]["unverified"], 3, "svc.stock, its edge and svc.loan's second source row are not verified")
        stamp = ev.load_stamp(self.paths.atlas)
        self.assertEqual(sorted(stamp["entries"]), ["EVD-1", "EVD-3"])
        # the row in the model is untouched and a second run says the same (never applies a repair)
        self.assertIn("def find_copies(", (self.paths.slices / "10-structure.atl").read_text(encoding="utf-8"))
        self.assertEqual(tools.evidence(self.ws, self.name)["fail"], 1)
        self.assertFalse(self.gate_of("evidence")["ok"])

    def test_ids_limit_the_run_and_an_unknown_id_is_refused(self):
        self.authored()
        reply = tools.evidence(self.ws, self.name, ids=["EVD-1"])
        self.assertEqual((reply["rows"], reply["pass"]), (1, 1))
        self.assertEqual(sorted(ev.load_stamp(self.paths.atlas)["entries"]), ["EVD-1"])
        self.assertEqual(tools.evidence(self.ws, self.name, ids=["EVD-9"])["status"], "invalid")

    def test_the_project_names_the_aliases_it_uses_and_no_other_root_is_read(self):
        other = self.root / "other"
        (other / "src").mkdir(parents=True)
        (other / "src" / "loan.py").write_text(SOURCES["src/loan.py"], encoding="utf-8")
        self.authored()
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": f"web={other}"}):
            reply = tools.evidence(self.ws, self.name)
        self.assertEqual((reply["roots"], reply["pass"], reply["skip"]), ([], 0, 3), "alias web is not one of the project's aliases (main)")

    def test_a_malformed_roots_variable_is_one_clear_refusal(self):
        self.authored()
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": "no-equals-sign"}):
            reply = tools.evidence(self.ws, self.name)
        self.assertEqual(reply["status"], "invalid")
        self.assertTrue(reply["code"].startswith("GEMARC_ATLAS_ROOT_"))
        for call in (lambda: tools.outline("main", "x.py"),):
            with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": "no-equals-sign"}):
                self.assertEqual(call()["status"], "invalid")


class BuildAndVerifyTests(Flow):
    def test_build_all_writes_the_sheet_and_stays_within_two_kb(self):
        self.authored()
        self.simulate()
        reply = tools.build_all(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        self.assertLessEqual(size(reply), tools.BUILD_CAP)
        self.assertEqual(reply["sheet"], "atlas/dist/oracle-sheet.md")
        self.assertTrue((self.paths.atlas / "dist" / "oracle-sheet.md").is_file())
        self.assertTrue(any("oracle-sheet.md" in x and "2 expectation(s)" in x for x in reply["next"]), reply["next"])
        self.assertFalse(self.confirm_file().exists(), "the MCP never writes the confirmation file")

    def test_a_refused_build_writes_no_sheet(self):
        self.authored()
        bad = "#atlas 1\n## nodes: id | name | sub | opts\ngen.a | Gen | Text. | room=rm.desk kind=Modul\n"
        self.assertEqual(self.put("40-bad", bad)["status"], "invalid")        # a red draft: the model is not buildable
        reply = tools.build_all(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_REFUSED))
        self.assertNotIn("sheet", reply)
        self.assertFalse((self.paths.atlas / "dist").exists())

    def test_release_without_the_gate_is_refused_and_only_the_users_sheet_is_written(self):
        # M8: a refused release writes no page (see tests/atlas/test_release_build.py), but the user still gets the sheet he needs to act on the oracle blockers
        self.authored()
        reply = tools.build_all(self.ws, self.name, release=True)
        self.assertEqual(reply["status"], "invalid")
        self.assertEqual(reply["code"], build.E_RELEASE)
        self.assertEqual(reply["sheet"], "atlas/dist/oracle-sheet.md")
        self.assertNotIn("page", reply)
        self.assertEqual(sorted(p.name for p in (self.paths.atlas / "dist").iterdir()), ["oracle-sheet.md"])

    def test_a_missing_browser_is_a_loud_skip_that_status_turns_into_a_stop_for_the_user(self):
        self.authored()
        self.simulate()
        tools.evidence(self.ws, self.name)
        self.assertEqual(tools.build_all(self.ws, self.name)["status"], "ok")
        with mock.patch.object(smoke, "find_browser", return_value=None):
            reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"], reply["gate"]), ("invalid", build.E_GATE_SKIP, "SKIP"))
        self.assertIn("SKIPPED, NOT PASSED", reply["message"])
        stamp = st.read_gate_stamp(self.paths)
        self.assertEqual(stamp["status"], "SKIP")
        state = self.status()
        self.assertEqual((state["done"], state["phase"]), (False, "needs-user"))
        self.assertTrue(any("browser SKIPPED" in x for x in state["needsUser"]))

    def test_verify_binds_its_verdict_to_the_page_and_a_rebuild_unbinds_it(self):
        self.authored()
        self.simulate()
        tools.evidence(self.ws, self.name)
        tools.build_all(self.ws, self.name)
        verdict = {"status": "PASS", "widths": [{"width": w, "status": "PASS", "codes": [], "tabs": ["1D"] * 6, "steps": {"ok": 11, "total": 11}, "seconds": 1.0,
                                                 "findings": []} for w in (1024, 1366, 1600)], "lines": ["PASS"], "browser": {"family": "edge", "version": "1"}}
        with mock.patch.object(build, "run_gate", return_value=verdict):
            reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["gate"]), ("ok", "PASS"))
        self.assertTrue(self.gate_of("browser")["ok"])
        self.put("30-policy", POLICY.replace("two seconds", "nine seconds"))
        self.simulate()
        tools.evidence(self.ws, self.name)
        tools.build_all(self.ws, self.name)
        self.assertFalse(self.gate_of("browser")["ok"], "the old stamp belongs to the old page")

    def test_verify_refuses_a_stale_page_before_any_browser_starts(self):
        self.authored()
        self.simulate()
        tools.build_all(self.ws, self.name)
        self.put("30-policy", POLICY.replace("two seconds", "ten seconds"))
        with mock.patch.object(smoke, "find_browser", side_effect=AssertionError("no browser may start")):
            reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_NO_PAGE))
        self.assertIsNone(st.read_gate_stamp(self.paths))


class WriteGateTests(Flow):
    def test_every_new_write_tool_refuses_without_the_flag_and_every_read_works(self):
        self.authored()
        self.simulate()
        tools.evidence(self.ws, self.name)
        tools.build_all(self.ws, self.name)
        before = tree_digest(self.paths.project)
        with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": ""}):
            for label, reply in (("simulate", tools.simulate(self.ws, self.name)), ("evidence", tools.evidence(self.ws, self.name)),
                                 ("build", tools.build_all(self.ws, self.name)), ("verify", tools.verify(self.ws, self.name)),
                                 ("record", review.review_record(self.ws, self.name, ""))):
                self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_AUTH_002"), (label, reply))
            for label, reply in (("doc", tools.doc(self.ws, self.name)), ("outline", tools.outline("main", "src/loan.py")),
                                 ("status", tools.status(self.ws, self.name)), ("check", tools.check(self.ws, self.name, mode="full")),
                                 ("index", tools.index(self.ws, self.name)), ("report", review.review_report(self.ws, self.name))):
                self.assertEqual(reply["status"], "ok", (label, reply))
        self.assertEqual(tree_digest(self.paths.project), before, "refusals and reads change no byte")


class ReferenceScaleTests(unittest.TestCase):
    """The reference-scale synthetic atlas (169 nodes, 374 edges, 23 cases, 242 evidences): every reply within its design cap."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        for key, value in (("GEMARC_ALLOW_WRITE", "1"), ("GEMARC_ATLAS_DEBUG", "1"), ("GEMARC_ATLAS_ROOTS", "")):
            patcher = mock.patch.dict(os.environ, {key: value})
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        gates = st.product_gates()
        gates.__enter__()
        cls.addClassCleanup(gates.__exit__, None, None, None)
        cls.ws = pj.get_workspace(Path(cls._tmp.name))
        cls.model = consistent(synth.generate("reference", 3))
        assert tools.init(cls.ws, "atlas", "Reference scale", "asis", ["en-US"], repos=cls.model["meta"]["repos"])["status"] == "created"
        for name, text in slice_texts(cls.model).items():
            assert tools.put(cls.ws, "atlas", name, text)["status"] == "committed", name
        cls.paths = pj.project_paths(cls.ws, "atlas")
        cls.times = {}

    def timed(self, label, fn):
        start = time.time()
        reply = fn()
        self.times[label] = round(time.time() - start, 3)
        return reply

    def test_every_reply_is_within_its_cap(self):
        ws, name = self.ws, "atlas"
        rows = []

        def check(label, reply, cap):
            n = size(reply)
            rows.append((label, n, cap))
            self.assertLessEqual(n, cap, f"{label}: {n} B > {cap} B")
            self.assertNotEqual(reply.get("status"), "error", reply)
            return reply

        check("status next", self.timed("status", lambda: tools.status(ws, name)), 1200)
        check("status report", tools.status(ws, name, "report"), 4500)
        check("status dims", tools.status(ws, name, "dims"), 4500)
        check("status oracle", tools.status(ws, name, "oracle"), 4500)
        check("status evidence", tools.status(ws, name, "evidence"), 4500)
        check("index", tools.index(ws, name), 4500)
        check("check full", tools.check(ws, name, mode="full"), 4500)
        first_slice = sorted(p.name for p in self.paths.slices.iterdir())[0]
        check("put (unchanged)", tools.put(ws, name, first_slice.rsplit(".", 1)[0], (self.paths.slices / first_slice).read_text(encoding="utf-8")), 4500)
        check("simulate", self.timed("simulate", lambda: tools.simulate(ws, name)), 3000)
        check("simulate ids", tools.simulate(ws, name, detail="ids"), 3000)
        check("doc outline", tools.doc(ws, name), 1024)
        for level in ("REQ", "BD", "DD"):
            check(f"doc outline {level}", tools.doc(ws, name, level=level), 1024)
        for chapter in range(1, 13):
            page = 1
            while True:
                reply = check(f"doc DD chapter {chapter} page {page}", tools.doc(ws, name, level="DD", chapter=str(chapter), page=page), HARD)
                self.assertEqual(reply["status"], "ok")
                self.assertNotIn("truncated", reply, f"chapter {chapter} page {page}: the page text must arrive whole")
                if page >= reply["pages"]:
                    break
                page += 1
        evidence = check("evidence (no root: every row SKIP)", self.timed("evidence", lambda: tools.evidence(ws, name)), 3000)
        self.assertEqual(evidence["skip"], 242)
        build_reply = check("build", self.timed("build", lambda: tools.build_all(ws, name)), 2000)
        self.assertEqual(build_reply["status"], "ok", build_reply)
        verified = check("verify (real browser; SKIP says so loudly)", self.timed("verify", lambda: tools.verify(ws, name)), 3000)
        if BROWSER:
            self.assertEqual((verified["status"], verified["gate"]), ("ok", "PASS"), verified)
        else:
            self.assertEqual(verified["gate"], "SKIP")
        check("review report", review.review_report(ws, name), 4500)
        check("status next after build", tools.status(ws, name), 1200)
        for label, rows_ in (("status", self.times.get("status")),):
            self.assertLess(rows_, 5.0)
        print("\n[reference-scale reply sizes] " + "; ".join(f"{label} {n}/{cap}" for label, n, cap in rows if "chapter" not in label))
        worst = max((n for label, n, cap in rows if "chapter" in label), default=0)
        print(f"[reference-scale doc chapter pages] largest reply {worst} B (hard cap {HARD}); timings {self.times}")

    def test_an_unfinished_structure_keeps_the_later_gates_quiet(self):
        state = tools.status(self.ws, "atlas")
        self.assertFalse(state["done"])
        self.assertEqual(state["phase"], "author", "the synthetic reference atlas has BLOCK slots: authoring comes before the gates")
        self.assertFalse(any(b.startswith(("gate-docs", "gate-build", "gate-browser")) for b in state["blockers"]), state["blockers"])


@unittest.skipUnless(BROWSER, "NO BROWSER (Edge or Chrome) FOUND: the real browser gate was NOT run (a skip is not a pass)")
class RealBrowserTests(Flow):
    def test_verify_runs_the_real_gate_and_status_reaches_done(self):
        self.authored()
        self.simulate()
        tools.evidence(self.ws, self.name)
        self.assertEqual(tools.build_all(self.ws, self.name)["status"], "ok")
        reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["gate"]), ("ok", "PASS"), reply)
        self.assertLessEqual(size(reply), tools.VERIFY_CAP)
        self.assertEqual(self.status()["phase"], "review")
        self.record()
        self.assertTrue(self.status()["done"])


if __name__ == "__main__":
    unittest.main()
