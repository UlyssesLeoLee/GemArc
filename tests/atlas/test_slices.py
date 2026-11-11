"""The slice store (design section 6, M2 (c)): put, red drafts, row-level patch, sha8, base conflicts, ownership, history, lock,
crash safety and two processes writing two slices at once.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_slices.py"

Every test works in its own temporary workspace. Synthetic id families only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import expand
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import slices as sl

SRC = str(Path(__file__).resolve().parents[2] / "src")

SPACE = """#atlas 1
## buildings: id | name | opts
zone.a | Zone A
## floors: id | building | name | level
lvl.a | zone.a | Floor A | 1
## rooms: id | floor | name | opts
rm.a | lvl.a | Room A
"""

BASE = """#atlas 1
// sample slice
## nodes: id | name | sub | opts
@set room=rm.a kind=Module
svc.one | One | First. | ev=EVD-1
svc.two | Two | Second. | ev=EVD-1
@set kind=Storage
tbl.one | Table one | Rows. | ev=EVD-1
## edges: from | rel | to | label | opts
svc.one | calls | svc.two | call two | ev=EVD-1
svc.two | writes | tbl.one | write row | ev=EVD-1
@final edges
## evidences: id | file | anchor | topic | opts
@set repo=main
EVD-1 | src/a.py | def run( | runs
"""

EVENTS = """#atlas 1
## cases: id | cat | title | opts
CS-1 | normal | First case
CS-2 | normal | Second case
## events: type | from | to | opts
@set case=CS-1
Call | svc.one | svc.two
Return | svc.two | svc.one
Call | svc.one | tbl.one
@set case=CS-2
Call | svc.two | tbl.one
Return | tbl.one | svc.two
"""

MACHINE = """#atlas 1
## machines: id | initial | opts
svc.two | Idle | owner=svc.one
## transitions: from | to | opts
@set machine=svc.two
Idle | Busy
Busy | Idle
"""


def make_project(root: Path, name: str = "p") -> pj.Paths:
    ws = pj.get_workspace(root)
    paths = pj.project_paths(ws, name)
    pj.create_layout(paths, pj.render_config(name, "Test", "asis", ["en-US"], "software", []))
    return paths


class TempProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = make_project(self.root)
        self.meta = pj.load_config(self.paths).meta()

    def put(self, name, text, **kw):
        return sl.put_slice(self.paths, self.meta, name, text, **kw)

    def slice_text(self, name, fmt="atl"):
        return (self.paths.slices / f"{name}.{fmt}").read_text(encoding="utf-8")

    def red_text(self, name, fmt="atl"):
        return (self.paths.red / f"{name}.{fmt}").read_text(encoding="utf-8")

    def tree(self):
        out = {}
        for p in sorted(self.paths.atlas.rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(self.paths.atlas))] = (st.st_size, st.st_mtime_ns)
        return out


# ---------------------------------------------------------------------------------------------------------------


class NameAndHashTests(unittest.TestCase):
    def test_slice_names_are_a_small_safe_alphabet(self):
        for good in ("a", "10-structure", "x" * 40, "00-space"):
            self.assertEqual(sl.check_name(good), good)
        for bad in ("", "A", "x" * 41, "a.b", "a/b", "a\\b", "..", "../x", "a b", "con", "nul", "com1", "lpt9", "é", "a\n", None, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(sl.SliceError) as cm:
                    sl.check_name(bad)
                self.assertEqual(cm.exception.code, sl.E_NAME)

    def test_sha8_ignores_newline_style_bom_and_trailing_blank_lines(self):
        a = "#atlas 1\n## nodes\nrow\n"
        self.assertEqual(sl.sha8(a), sl.sha8(a.replace("\n", "\r\n")))
        self.assertEqual(sl.sha8(a), sl.sha8("﻿" + a))
        self.assertEqual(sl.sha8(a), sl.sha8(a + "\n\n"))
        self.assertNotEqual(sl.sha8(a), sl.sha8(a + "x\n"))
        self.assertRegex(sl.sha8(a), r"^[0-9a-f]{8}$")

    def test_normalise_gives_one_final_newline(self):
        self.assertEqual(sl.normalise("a\r\nb\r\n\r\n"), "a\nb\n")
        self.assertEqual(sl.normalise(""), "")


class PatchTests(unittest.TestCase):
    def apply(self, patch, base=BASE, fmt="atl"):
        return sl.apply_patch(base, patch, fmt)

    def lines(self, text):
        return text.split("\n")

    def test_a_row_with_the_identity_of_a_base_row_replaces_it_and_every_other_line_is_kept(self):
        r = self.apply("## nodes: id | name | sub | opts\nsvc.two | Two b | Second b. | room=rm.a kind=Module ev=EVD-1\n")
        self.assertEqual((r.replaced, r.added, r.deleted), (1, 0, 0))
        self.assertNotIn("svc.two | Two | Second. | ev=EVD-1", r.text)
        self.assertIn("svc.two | Two b | Second b. | room=rm.a kind=Module ev=EVD-1", r.text)
        kept = [x for x in self.lines(BASE) if x and "svc.two | Two |" not in x]
        for line in kept:
            self.assertIn(line, self.lines(r.text), line)           # comments, @set, @final, other rows: verbatim
        self.assertEqual(self.lines(r.text).count("## nodes: id | name | sub | opts"), 2)

    def test_a_new_identity_is_appended_and_a_delete_row_removes_by_identity(self):
        r = self.apply("## nodes\nsvc.three | Three | Third. | room=rm.a kind=Module\n-tbl.one\n## edges\n-svc.two | writes | tbl.one\n")
        self.assertEqual((r.replaced, r.added, r.deleted), (0, 1, 2))
        self.assertNotIn("tbl.one | Table one", r.text)
        self.assertNotIn("svc.two | writes | tbl.one", r.text)
        self.assertIn("svc.three | Three | Third.", r.text)
        self.assertIn("svc.one | calls | svc.two | call two", r.text)

    def test_a_delete_that_matches_nothing_is_reported_not_silent(self):
        r = self.apply("## nodes\n-svc.nowhere\n## edges\n-svc.one | calls | svc.nowhere\n")
        self.assertEqual(r.deleted, 0)
        self.assertEqual(len(r.missed), 2)
        self.assertEqual(r.as_dict()["nothing_to_delete"], r.missed)

    def test_edges_are_identified_by_from_rel_to_and_alias(self):
        base = BASE + "## edges: from | rel | to | label | opts\nsvc.one | calls | svc.two | again | alias=twice\n"
        r = self.apply("## edges\n-svc.one | calls | svc.two | x | alias=twice\n", base)
        self.assertEqual(r.deleted, 1)
        self.assertIn("svc.one | calls | svc.two | call two", r.text)
        self.assertNotIn("alias=twice", r.text)

    def test_an_events_block_replaces_all_events_of_the_case_and_an_empty_block_deletes_them(self):
        r = self.apply("## events: type | from | to | opts\n@set case=CS-1\nCall | svc.two | tbl.one\n", EVENTS)
        self.assertEqual(r.blocks, ["CS-1"])
        self.assertNotIn("Call | svc.one | svc.two", r.text)
        self.assertNotIn("Call | svc.one | tbl.one", r.text)
        self.assertIn("Call | svc.two | tbl.one", r.text)
        self.assertEqual(r.text.count("Return | tbl.one | svc.two"), 1)           # CS-2 untouched
        gone = self.apply("## events\n@set case=CS-2\n", EVENTS)
        self.assertEqual(gone.blocks, ["CS-2"])
        self.assertNotIn("Return | tbl.one | svc.two", gone.text)
        self.assertIn("Call | svc.one | svc.two", gone.text)                       # CS-1 untouched

    def test_events_rows_may_carry_the_case_as_an_option(self):
        r = self.apply("## events\nCall | svc.two | svc.one | case=CS-2\n", EVENTS)
        self.assertNotIn("Call | svc.two | tbl.one", r.text)
        self.assertIn("Call | svc.two | svc.one | case=CS-2", r.text)
        self.assertIn("Call | svc.one | svc.two", r.text)

    def test_transitions_are_identified_by_machine_from_and_to(self):
        r = self.apply("## transitions\n-Idle | Busy | machine=svc.two\n", MACHINE)
        self.assertEqual(r.deleted, 1)
        self.assertNotIn("Idle | Busy", r.text)
        self.assertIn("Busy | Idle", r.text)
        other = self.apply("## transitions\n-Idle | Busy | machine=svc.other\n", MACHINE)
        self.assertEqual((other.deleted, len(other.missed)), (0, 1))

    def test_rows_that_need_set_defaults_bring_their_own_set_lines_under_a_fresh_header(self):
        patch = "## nodes\n@set room=rm.b kind=Actor\nusr.x | X | Someone. | ev=EVD-1\n"
        r = self.apply(patch)
        tail = self.lines(r.text.rstrip("\n"))[-3:]
        self.assertEqual(tail, ["## nodes: id | name | sub | opts", "@set room=rm.b kind=Actor", "usr.x | X | Someone. | ev=EVD-1"])
        parsed = expand.parse_source("s.atl", r.text)
        self.assertFalse(parsed.diags)
        row = next(x for x in parsed.rows if x.values.get("id") == "usr.x")
        self.assertEqual((row.values["room"], row.values["kind"]), ("rm.b", "Actor"))

    def test_appended_rows_join_the_last_section_when_neither_side_has_set_lines(self):
        base = BASE.replace("@set repo=main\n", "")
        r = self.apply("## evidences\nEVD-2 | src/b.py | def go( | goes | repo=main\n", base)
        self.assertEqual(r.text.count("## evidences"), 1)
        with_set = self.apply("## evidences\nEVD-2 | src/b.py | def go( | goes | repo=main\n")      # the base section holds a @set line
        self.assertEqual(with_set.text.count("## evidences"), 2)
        self.assertTrue(r.text.rstrip("\n").endswith("EVD-2 | src/b.py | def go( | goes | repo=main"))

    def test_applying_the_same_patch_twice_gives_the_same_text(self):
        for patch in ("## nodes\nsvc.two | Two b | Second b. | room=rm.a kind=Module\n",
                      "## nodes\n@set room=rm.b kind=Actor\nusr.x | X | Someone. | ev=EVD-1\nusr.y | Y | Another. | ev=EVD-1\n",
                      "## nodes\n-tbl.one\n## evidences\nEVD-2 | src/b.py | def go( | goes | repo=main\n",
                      "## edges\nsvc.one | calls | svc.two | renamed call | ev=EVD-1\n"):
            with self.subTest(patch=patch):
                once = self.apply(patch).text
                twice = self.apply(patch, once).text
                self.assertEqual(once, twice)
        ev_patch = "## events\n@set case=CS-1\nCall | svc.two | tbl.one\n"
        once = self.apply(ev_patch, EVENTS).text
        self.assertEqual(once, self.apply(ev_patch, once).text)

    def test_when_identities_repeat_an_operation_takes_the_last_one_so_the_first_definition_stays(self):
        base = BASE + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nsvc.one | Dup | Second definition. | ev=EVD-1\n"
        r = self.apply("## nodes\n-svc.one\n", base)
        self.assertEqual(r.deleted, 1)
        self.assertIn("svc.one | One | First.", r.text)
        self.assertNotIn("Second definition", r.text)

    def test_final_and_pending_lines_are_merged_once(self):
        r = self.apply("## edges\n@final edges\n## nodes\n@pending svc.later\nsvc.q | Q | Quiet. | room=rm.a kind=Module\n")
        self.assertEqual(r.text.count("@final edges"), 1)
        self.assertEqual(r.text.count("@pending svc.later"), 1)
        again = self.apply("## nodes\n@pending svc.later\n@pending svc.more\n", r.text)
        self.assertEqual(again.text.count("@pending svc.later"), 1)
        self.assertIn("@pending svc.more", again.text)

    def test_rows_the_parser_refuses_stay_until_a_patch_replaces_them(self):
        broken = BASE.replace("svc.two | Two | Second. | ev=EVD-1", "svc.two | Two | Second. | ev=EVD-1 kind=Modul")
        r = self.apply("## nodes\n-svc.one\n", broken)
        self.assertRegex(r.text, r"kind=Modul\b")                                   # a row the patch did not name is never touched
        fixed = self.apply("## nodes\nsvc.two | Two | Second. | room=rm.a kind=Module ev=EVD-1\n", broken)
        self.assertNotRegex(fixed.text, r"kind=Modul\b")
        self.assertEqual(fixed.replaced, 1)

    def test_a_row_cell_with_an_escaped_pipe_or_quotes_keeps_its_identity(self):
        base = '#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nsvc.p | "Say | hi" | Quoted. | ev=EVD-1\n'
        r = self.apply("## nodes\n-svc.p\n", base)
        self.assertEqual(r.deleted, 1)

    def test_malformed_patches_are_refused_with_nothing_applied(self):
        for patch in ("svc.x | X | Row first. | room=rm.a kind=Module\n", "## nodez\nsvc.x | X | Y.\n", "", "// only a comment\n",
                      "## events\nCall | a | b\n", "## nodes\n-\n"):
            with self.subTest(patch=patch):
                with self.assertRaises(sl.SliceError) as cm:
                    self.apply(patch)
                self.assertEqual(cm.exception.code, sl.E_PATCH)

    def test_jsonl_patch_replaces_deletes_and_replaces_event_blocks(self):
        base = "\n".join([
            '{"_atlas":1}',
            '{"_section":"nodes","id":"svc.one","name":"One","sub":"First.","room":"rm.a","kind":"Module"}',
            '{"_section":"nodes","id":"svc.two","name":"Two","sub":"Second.","room":"rm.a","kind":"Module"}',
            '{"_section":"events","type":"Call","from":"svc.one","to":"svc.two","case":"CS-1"}',
            '{"_section":"events","type":"Return","from":"svc.two","to":"svc.one","case":"CS-1"}',
            '{"_final":"nodes"}', ""])
        patch = "\n".join([
            '{"_section":"nodes","id":"svc.two","name":"Two b","sub":"Second b.","room":"rm.a","kind":"Module"}',
            '-{"_section":"nodes","id":"svc.one"}',
            '{"_section":"events","type":"Call","from":"svc.two","to":"svc.two","case":"CS-1"}', ""])
        r = sl.apply_patch(base, patch, "jsonl")
        self.assertEqual((r.replaced, r.deleted, r.blocks), (1, 1, ["CS-1"]))
        self.assertNotIn('"id":"svc.one"', r.text)
        self.assertIn('"name":"Two b"', r.text)
        self.assertNotIn('"type":"Return"', r.text)
        self.assertEqual(r.text.count('{"_final":"nodes"}'), 1)
        self.assertEqual(r.text, sl.apply_patch(r.text, patch, "jsonl").text)
        with self.assertRaises(sl.SliceError):
            sl.apply_patch(base, "not json\n", "jsonl")
        gone = sl.apply_patch(base, '-{"_section":"events","case":"CS-1"}\n', "jsonl")
        self.assertNotIn('"_section":"events"', gone.text)


class PutTests(TempProject):
    def test_a_clean_slice_commits_and_the_same_text_is_unchanged_with_the_same_sha8(self):
        first = self.put("00-space", SPACE)
        self.assertEqual((first.status, first.unchanged, first.previous), ("committed", False, ""))
        self.assertEqual(self.slice_text("00-space"), SPACE)
        stat = (self.paths.slices / "00-space.atl").stat()
        again = self.put("00-space", SPACE)
        self.assertEqual((again.status, again.unchanged, again.sha8), ("committed", True, first.sha8))
        self.assertEqual((self.paths.slices / "00-space.atl").stat().st_mtime_ns, stat.st_mtime_ns)    # not rewritten
        crlf = self.put("00-space", SPACE.replace("\n", "\r\n"))
        self.assertTrue(crlf.unchanged)
        self.assertEqual(sl.history_of(self.paths, "00-space"), [])
        self.assertFalse(self.paths.lock.exists())

    def test_a_change_archives_the_previous_version_by_its_sha8(self):
        first = self.put("00-space", SPACE)
        second = self.put("00-space", SPACE + "## rooms: id | floor | name | opts\nrm.b | lvl.a | Room B\n")
        self.assertEqual(second.previous, first.sha8)
        history = sl.history_of(self.paths, "00-space")
        self.assertEqual([p.name for p in history], [f"00-space.{hashlib_sha8(SPACE)}.atl"])
        self.assertEqual(history[0].read_text(encoding="utf-8"), SPACE)

    def test_an_error_stores_the_whole_text_as_a_red_draft_and_commits_nothing(self):
        self.put("00-space", SPACE)
        bad = BASE.replace("svc.two | Two | Second. | ev=EVD-1", "svc.two | Two | Second. | ev=EVD-1 kind=Modul")
        out = self.put("10-structure", bad)
        self.assertEqual((out.status, out.red), ("invalid", True))
        self.assertIn("ST-003", out.check.rule_ids())
        self.assertFalse((self.paths.slices / "10-structure.atl").exists())
        self.assertEqual(self.red_text("10-structure"), sl.normalise(bad))
        self.assertEqual([s.name for s in sl.list_slices(self.paths)], ["00-space"])

    def test_a_failed_replace_leaves_the_committed_version_untouched(self):
        self.put("10-structure", BASE)
        before = self.slice_text("10-structure")
        bad = BASE.replace("svc.two | Two", "svc.two | ")
        out = self.put("10-structure", bad)
        self.assertEqual(out.status, "invalid")
        self.assertEqual(self.slice_text("10-structure"), before)
        self.assertEqual(self.red_text("10-structure"), sl.normalise(bad))

    def test_a_patch_applies_to_the_red_draft_and_loses_no_valid_row(self):
        self.put("00-space", SPACE)
        bad = BASE.replace("svc.two | Two | Second. | ev=EVD-1", "svc.two | Two | Second. | ev=EVD-1 kind=Modul")
        self.assertEqual(self.put("10-structure", bad).status, "invalid")
        valid_rows = [x for x in BASE.split("\n") if x and "svc.two | Two |" not in x]
        out = self.put("10-structure", "## nodes\nsvc.two | Two | Second. | room=rm.a kind=Module ev=EVD-1\n", mode="patch")
        self.assertEqual(out.status, "committed", out.check.render())
        self.assertTrue(out.cleared_red)
        self.assertFalse(list(self.paths.red.iterdir()))
        committed = self.slice_text("10-structure").split("\n")
        for row in valid_rows:
            self.assertIn(row, committed)
        model = expand.expand_sources({"s.atl": self.slice_text("10-structure")}).model
        self.assertEqual({n["id"] for n in model["nodes"]}, {"svc.one", "svc.two", "tbl.one"})

    def test_a_patch_with_an_error_updates_the_red_draft_not_the_committed_slice(self):
        self.put("00-space", SPACE)
        self.put("10-structure", BASE)
        committed = self.slice_text("10-structure")
        out = self.put("10-structure", "## nodes\nsvc.three | Three | Third. | room=rm.a kind=Nope\n", mode="patch")
        self.assertEqual(out.status, "invalid")
        self.assertEqual(self.slice_text("10-structure"), committed)
        self.assertIn("kind=Nope", self.red_text("10-structure"))
        self.assertIn("svc.one | One | First.", self.red_text("10-structure"))         # the draft is the committed text plus the patch
        fixed = self.put("10-structure", "## nodes\nsvc.three | Three | Third. | room=rm.a kind=Module\n", mode="patch")
        self.assertEqual(fixed.status, "committed")
        self.assertNotIn("kind=Nope", self.slice_text("10-structure"))
        self.assertEqual(fixed.patch.replaced, 1)

    def test_replace_supersedes_a_red_draft_and_a_patch_needs_something_to_patch(self):
        self.put("00-space", SPACE)
        self.put("10-structure", BASE.replace("kind=Module", "kind=Modul"))
        self.assertTrue((self.paths.red / "10-structure.atl").exists())
        out = self.put("10-structure", BASE)
        self.assertEqual((out.status, out.cleared_red), ("committed", True))
        with self.assertRaises(sl.SliceError) as cm:
            self.put("20-nothing", "## nodes\nx.y | Y | Z.\n", mode="patch")
        self.assertEqual(cm.exception.code, sl.E_NOTHING)
        with self.assertRaises(sl.SliceError) as cm:
            self.put("10-structure", '{"_section":"nodes","id":"a.b"}\n', mode="patch", fmt="jsonl")
        self.assertEqual(cm.exception.code, sl.E_ARG)

    def test_warnings_and_pending_references_never_block_a_commit(self):
        out = self.put("10-structure", BASE)         # rm.a is defined nowhere yet: pending, not an error
        self.assertEqual(out.status, "committed")
        self.assertTrue(out.check.pending)

    def test_st002_across_slices_names_both_locations(self):
        self.put("10-structure", BASE)
        dup = "#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nsvc.one | Again | Same id elsewhere. | ev=EVD-1\n"
        out = self.put("11-structure", dup)
        self.assertEqual(out.status, "invalid")
        text = out.check.render()
        self.assertIn("ST-002", text)
        self.assertIn("11-structure.atl:4", text)
        self.assertIn("10-structure.atl:5", text)
        self.assertTrue((self.paths.red / "11-structure.atl").exists())

    def test_base_conflict_writes_nothing(self):
        first = self.put("00-space", SPACE)
        self.put("00-space", SPACE + "## rooms: id | floor | name | opts\nrm.b | lvl.a | Room B\n")
        before = self.tree()
        with self.assertRaises(sl.SliceError) as cm:
            self.put("00-space", SPACE + "## rooms: id | floor | name | opts\nrm.c | lvl.a | Room C\n", base=first.sha8)
        self.assertEqual(cm.exception.code, sl.E_CONFLICT)
        self.assertTrue(cm.exception.extra["conflict"])
        self.assertEqual(cm.exception.extra["current"], sl.sha8(self.slice_text("00-space")))
        self.assertEqual(self.tree(), before)             # not even a red draft, a history file or a temp file
        with self.assertRaises(sl.SliceError):            # a slice that does not exist has no sha8 to match
            self.put("99-missing", SPACE, base="deadbeef")
        self.assertEqual(self.tree(), before)
        ok = self.put("00-space", SPACE + "## rooms: id | floor | name | opts\nrm.c | lvl.a | Room C\n", base=sl.sha8(self.slice_text("00-space")))
        self.assertEqual(ok.status, "committed")
        with self.assertRaises(sl.SliceError):
            self.put("00-space", SPACE, base="XYZ")

    def test_base_may_name_the_red_draft(self):
        self.put("00-space", SPACE)
        bad = self.put("10-structure", BASE.replace("kind=Module", "kind=Modul"))
        out = self.put("10-structure", "## nodes\nsvc.one | One | First. | room=rm.a kind=Module ev=EVD-1\n", mode="patch", base=bad.sha8)
        self.assertEqual(out.status, "invalid")     # svc.two still has the bad kind; the base was accepted

    def test_history_is_capped_at_ten_newest_versions(self):
        texts = [SPACE + f"## rooms: id | floor | name | opts\nrm.v{i} | lvl.a | Room {i}\n" for i in range(14)]
        for t in texts:
            self.assertEqual(self.put("00-space", t).status, "committed")
        history = sl.history_of(self.paths, "00-space")
        self.assertEqual(len(history), sl.HISTORY_CAP)
        kept = {p.name for p in history}
        for t in texts[3:13]:                  # versions 3..12 were replaced and are the ten newest archives
            self.assertIn(f"00-space.{hashlib_sha8(t)}.atl", kept)
        for t in texts[:3]:
            self.assertNotIn(f"00-space.{hashlib_sha8(t)}.atl", kept)
        self.assertEqual(self.slice_text("00-space"), texts[13])
        mtimes = [p.stat().st_mtime_ns for p in history]
        self.assertEqual(mtimes, sorted(set(mtimes)))      # strictly increasing: age order never ties

    def test_history_of_one_slice_does_not_touch_another(self):
        self.put("00-space", SPACE)
        for i in range(3):
            self.put("00-space", SPACE + f"## rooms: id | floor | name | opts\nrm.v{i} | lvl.a | R{i}\n")
        self.put("a", "#atlas 1\n## tests: id | title\nTS-1 | One\n")
        self.put("a-b", "#atlas 1\n## tests: id | title\nTS-2 | Two\n")
        self.put("a", "#atlas 1\n## tests: id | title\nTS-1 | One b\n")
        self.assertEqual(len(sl.history_of(self.paths, "a")), 1)
        self.assertEqual(len(sl.history_of(self.paths, "00-space")), 3)

    def test_the_format_of_a_slice_can_change_and_the_old_file_goes_to_the_history(self):
        self.put("s", "#atlas 1\n## tests: id | title\nTS-1 | One\n")
        out = self.put("s", '{"_atlas":1}\n{"_section":"tests","id":"TS-1","title":"One"}\n', fmt="jsonl")
        self.assertEqual(out.status, "committed")
        self.assertFalse((self.paths.slices / "s.atl").exists())
        self.assertTrue((self.paths.slices / "s.jsonl").exists())
        self.assertEqual(len(sl.history_of(self.paths, "s")), 1)

    def test_size_limits(self):
        with self.assertRaises(sl.SliceError) as cm:
            self.put("big", "#atlas 1\n" + "// x\n" * 40000)
        self.assertEqual(cm.exception.code, sl.E_LIMIT)
        self.assertEqual(self.tree().get("slices/big.atl"), None)
        over = "#atlas 1\n## tests: id | title\n" + "".join(f"TS-{i} | {'t' * 30}\n" for i in range(2200))
        self.assertGreater(len(over.encode()), sl.MAX_SLICE_BYTES)
        out = self.put("over", over)                                     # above 64 KiB: ST-008, kept as a red draft
        self.assertEqual(out.status, "invalid")
        self.assertIn("ST-008", out.check.rule_ids())
        with mock.patch.object(sl, "MAX_PROJECT_BYTES", 2000):
            with self.assertRaises(sl.SliceError) as cm:
                self.put("00-space", SPACE + "// pad\n" * 400)
        self.assertEqual(cm.exception.code, sl.E_LIMIT)
        with self.assertRaises(sl.SliceError):
            self.put("empty", "  \n")

    def test_names_cannot_leave_the_slices_directory(self):
        outside = self.paths.atlas / "escape.atl"
        for name in ("../escape", "..\\escape", "a/../../escape", "/abs"):
            with self.assertRaises(sl.SliceError):
                self.put(name, SPACE)
        self.assertFalse(outside.exists())

    def test_commit_hooks_run_after_a_commit_and_cannot_undo_it(self):
        seen = []

        def good(paths, outcome):
            seen.append((outcome.slice, outcome.status))

        def bad(paths, outcome):
            raise RuntimeError("seal failed")

        with mock.patch.object(sl, "COMMIT_HOOKS", []):
            sl.register_commit_hook(good)
            sl.register_commit_hook(good)
            sl.register_commit_hook(bad)
            out = self.put("00-space", SPACE)
            self.assertEqual(out.status, "committed")
            self.assertEqual(seen, [("00-space", "committed")])
            self.assertEqual(len(out.hook_errors), 1)
            self.assertEqual(self.slice_text("00-space"), SPACE)
            self.put("bad-one", "#atlas 1\n## nodes: id | name | sub | opts\nx | y\n")     # invalid: no hook
            self.assertEqual(len(seen), 1)

    def test_ownership_map_names_the_first_definition_of_every_id(self):
        a = expand.parse_source("a.atl", BASE)
        b = expand.parse_source("b.atl", SPACE)
        owners = sl.ownership_map([a, b])
        self.assertEqual(owners["svc.one"], "a.atl:5")
        self.assertEqual(owners["svc.one:calls:svc.two"], "a.atl:10")
        self.assertEqual(owners["rm.a"], "b.atl:7")
        self.assertEqual(owners["EVD-1"], "a.atl:15")


def hashlib_sha8(text: str) -> str:
    return sl.sha8(text)


# ---------------------------------------------------------------------------------------------------------------
# Lock, stale lock, crash, two processes
# ---------------------------------------------------------------------------------------------------------------


class LockTests(TempProject):
    def test_a_held_lock_blocks_a_put_until_the_wait_runs_out_and_nothing_is_written(self):
        with sl.project_lock(self.paths):
            before = self.tree()
            t0 = time.monotonic()
            with self.assertRaises(sl.SliceError) as cm:
                self.put("00-space", SPACE, lock_wait=0.3)
            self.assertGreaterEqual(time.monotonic() - t0, 0.25)
            self.assertEqual(cm.exception.code, sl.E_LOCK)
            self.assertEqual({k: v for k, v in self.tree().items() if k != ".atlas/lock"}, {k: v for k, v in before.items() if k != ".atlas/lock"})
        self.assertFalse(self.paths.lock.exists())                  # released
        self.assertEqual(self.put("00-space", SPACE).status, "committed")

    def test_a_put_waits_for_a_lock_that_is_released_in_time(self):
        import threading

        gate = threading.Event()

        def holder():
            with sl.project_lock(self.paths):
                gate.set()
                time.sleep(0.4)

        t = threading.Thread(target=holder)
        t.start()
        gate.wait(5)
        out = self.put("00-space", SPACE, lock_wait=10)
        t.join()
        self.assertEqual(out.status, "committed")

    def test_a_stale_lock_is_recovered_and_a_fresh_one_is_not(self):
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.lock.write_bytes(f"{os.getpid()}:live".encode())  # a live holder (this process)
        with self.assertRaises(sl.SliceError):
            self.put("00-space", SPACE, lock_wait=0.2)              # fresh: honoured
        old = time.time() - sl.LOCK_STALE_S - 5
        os.utime(self.paths.lock, (old, old))
        self.assertEqual(self.put("00-space", SPACE, lock_wait=2).status, "committed")   # stale: broken
        self.assertFalse(self.paths.lock.exists())
        self.assertFalse([p for p in self.paths.dot.iterdir() if p.name.startswith("lock")])

    def test_a_lock_of_a_dead_process_is_broken_at_once(self):
        import subprocess
        import sys

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.lock.write_bytes(f"{child.pid}:deadbeef".encode())  # fresh mtime, but the writer is gone
        started = time.monotonic()
        self.assertEqual(self.put("00-space", SPACE, lock_wait=5).status, "committed")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(self.paths.lock.exists())

    def test_a_lock_without_a_process_id_waits_for_the_stale_time(self):
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.lock.write_bytes(b"")                            # created, token not written yet
        with self.assertRaises(sl.SliceError):
            self.put("00-space", SPACE, lock_wait=0.2)

    def test_breaking_a_stale_lock_never_deletes_a_live_one_that_replaced_it(self):
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        stale = f"{os.getpid()}:old".encode()
        self.paths.lock.write_bytes(stale)
        old = time.time() - sl.LOCK_STALE_S - 5
        os.utime(self.paths.lock, (old, old))
        real_read = type(self.paths.lock).read_bytes
        swapped = []

        def swapping_read(path_self):
            data = real_read(path_self)
            if path_self == self.paths.lock and not swapped:
                swapped.append(True)                                # another process broke the stale lock and took a fresh one
                os.unlink(path_self)
                path_self.write_bytes(f"{os.getpid()}:live".encode())
            return data

        from unittest import mock

        with mock.patch.object(type(self.paths.lock), "read_bytes", swapping_read):
            sl._break_if_stale(self.paths.lock, sl.LOCK_STALE_S)
        self.assertTrue(self.paths.lock.exists())
        self.assertEqual(self.paths.lock.read_bytes(), f"{os.getpid()}:live".encode())

    def test_two_threads_putting_the_same_id_in_two_slices_cannot_both_commit(self):
        import threading

        results = {}

        def worker(name):
            text = f"#atlas 1\n## tests: id | title\nTS-same | From {name}\n"
            results[name] = self.put(name, text, lock_wait=10).status

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("t-a", "t-b")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results.values()), ["committed", "invalid"])


_WORKER = r"""
import sys
from gemarc_mcp.atlas import slices as sl, project as pj
ws = pj.get_workspace(sys.argv[1])
paths = pj.project_paths(ws, "p")
meta = pj.load_config(paths).meta()
tag, count = sys.argv[2], int(sys.argv[3])
for i in range(count):
    text = "#atlas 1\n## tests: id | title\n" + "".join(f"TS-{tag}-{i}-{k} | Test {k}\n" for k in range(5))
    out = sl.put_slice(paths, meta, f"{tag}-{i}", text)
    if out.status != "committed":
        print("NOT COMMITTED", tag, i, out.check.render())
        sys.exit(3)
"""

_CRASHER = r"""
import os, sys
from gemarc_mcp.atlas import slices as sl, project as pj
ws = pj.get_workspace(sys.argv[1])
paths = pj.project_paths(ws, "p")
meta = pj.load_config(paths).meta()
die_on = int(sys.argv[2])
calls = {"n": 0}
real = sl._replace
def hook(src, dst):
    calls["n"] += 1
    if calls["n"] == die_on:
        os._exit(7)          # killed between the temp write and the replace
    return real(src, dst)
sl._replace = hook
sl.put_slice(paths, meta, "s1", "#atlas 1\n## tests: id | title\nTS-NEW | New text\n")
"""


def run_python(code: str, *args, check=False):
    env = dict(os.environ, PYTHONPATH=SRC, GEMARC_ALLOW_WRITE="1")
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], env=env, capture_output=True, text=True, timeout=120)


class ProcessTests(TempProject):
    def test_two_processes_putting_two_different_slices_both_commit(self):
        procs = []
        env = dict(os.environ, PYTHONPATH=SRC, GEMARC_ALLOW_WRITE="1")
        for tag in ("pa", "pb"):
            procs.append(subprocess.Popen([sys.executable, "-c", _WORKER, str(self.root), tag, "8"], env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        outputs = [p.communicate(timeout=180) for p in procs]
        for p, (out, err) in zip(procs, outputs):
            self.assertEqual(p.returncode, 0, out + err)
        names = sorted(s.name for s in sl.list_slices(self.paths))
        self.assertEqual(names, sorted(f"{t}-{i}" for t in ("pa", "pb") for i in range(8)))
        for name in names:
            tag, i = name.rsplit("-", 1)[0].split("-")[0], name.rsplit("-", 1)[1]
            self.assertIn(f"TS-{tag}-{i}-4 | Test 4", self.slice_text(name))
        self.assertFalse(self.paths.lock.exists())
        self.assertFalse([p for p in self.paths.atlas.rglob(".tmp-*")])
        self.assertFalse(list(self.paths.red.iterdir()))

    def test_a_kill_between_the_temp_write_and_the_replace_leaves_a_consistent_slice(self):
        v1 = "#atlas 1\n## tests: id | title\nTS-OLD | Old text\n"
        self.put("s1", v1)
        for die_on in (1, 2):             # 1: while archiving the old version, 2: while replacing the slice
            with self.subTest(die_on=die_on):
                r = run_python(_CRASHER, self.root, die_on)
                self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
                self.assertEqual(self.slice_text("s1"), v1)                               # the old, complete version
                self.assertEqual([s.name for s in sl.list_slices(self.paths)], ["s1"])     # no half-written slice, no temp listed
                self.assertTrue(list(self.paths.atlas.rglob(".tmp-*")))                   # the leftover is there ...
                self.assertTrue(self.paths.lock.exists())                                 # ... and so is the dead process's lock
                old = time.time() - sl.LOCK_STALE_S - 5
                os.utime(self.paths.lock, (old, old))
                # a read-only check still works in this state
                from gemarc_mcp.atlas import tools

                self.assertEqual(tools.check(pj.get_workspace(self.root), "p", mode="full")["status"], "ok")
                out = self.put("s1", v1, lock_wait=3)                                     # recovers the stale lock, sweeps the temp
                self.assertEqual((out.status, out.unchanged), ("committed", True))
                self.assertFalse(list(self.paths.atlas.rglob(".tmp-*")))
                self.assertFalse(self.paths.lock.exists())
        v2 = "#atlas 1\n## tests: id | title\nTS-NEW | New text\n"
        self.assertEqual(self.put("s1", v2).status, "committed")
        self.assertEqual(self.slice_text("s1"), v2)

    def test_readers_see_the_old_or_the_new_slice_while_another_process_replaces_it(self):
        self.put("s1", "#atlas 1\n## tests: id | title\nTS-1 | v0\n")
        env = dict(os.environ, PYTHONPATH=SRC, GEMARC_ALLOW_WRITE="1")
        code = _WORKER.replace('f"{tag}-{i}"', '"s1"').replace("for k in range(5))", "for k in range(1))").replace("TS-{tag}-{i}-{k} | Test {k}", "TS-1 | v{i}")
        p = subprocess.Popen([sys.executable, "-c", code, str(self.root), "w", "25"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seen = set()
        while p.poll() is None:
            data = sl.find_slice(self.paths, "s1").data()
            self.assertTrue(data.startswith(b"#atlas 1\n## tests: id | title\n"))
            self.assertTrue(data.endswith(b"\n"))
            seen.add(len(data))
        out, err = p.communicate()
        self.assertEqual(p.returncode, 0, out + err)


class SweepTests(TempProject):
    def test_slice_discovery_ignores_dot_and_temp_files_and_other_extensions(self):
        self.put("00-space", SPACE)
        (self.paths.slices / ".tmp-1-abcd-x.atl").write_text("junk", encoding="utf-8")
        (self.paths.slices / "notes.txt").write_text("junk", encoding="utf-8")
        (self.paths.slices / ".hidden.atl").write_text("junk", encoding="utf-8")
        (self.paths.slices / "Direct-Edit.atl").write_text(SPACE.replace("zone.a", "zone.z"), encoding="utf-8")
        self.assertEqual([s.fname for s in sl.list_slices(self.paths)], ["00-space.atl", "Direct-Edit.atl"])
        self.assertEqual(sl.sweep_temps(self.paths), 1)


if __name__ == "__main__":
    unittest.main()
