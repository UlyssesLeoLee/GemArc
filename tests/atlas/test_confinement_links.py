"""Links, junctions and reparse points in the project directories (contract-requests item 37; debt-b4, item D).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_confinement_links.py"

Every test that needs a link makes a REAL one: ``mklink /J`` (a junction, no administrator rights needed) on Windows, a directory symlink
elsewhere, and SKIPS cleanly where neither can be created. The junction and a target that holds a sentinel file sit in the same temporary
directory; the junction is removed with ``os.rmdir`` (never ``rmtree``) before the directory goes, and every test asserts that the sentinel
survived and that the refusal carries ``GEMARC_AUTH_001``. Synthetic id families only.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, tools
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas.gate import smoke
from gemarc_mcp.paths import GemArcError

from .test_tools_b3_fixes import make_link
from .test_tools_b3_gates import Flow

CODE = "GEMARC_AUTH_001"


@contextlib.contextmanager
def planted(test: unittest.TestCase, link: Path, target: Path):
    """A real directory link at ``link`` pointing at ``target``; removed with ``os.rmdir`` (the link itself, never what it points to)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if not make_link(link, target):
        test.skipTest("no directory link (junction or symlink) could be made here")
    try:
        yield link
    finally:
        try:
            os.rmdir(link)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(link)


class LinkProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.target = self.base / "target"
        self.target.mkdir()
        (self.target / "sentinel.txt").write_text("keep me", encoding="utf-8")

    def test_a_plain_directory_a_file_and_a_missing_path_are_not_links(self):
        plain = self.base / "plain"
        plain.mkdir()
        for path in (plain, self.target / "sentinel.txt", self.base / "absent", self.base / "absent" / "deeper"):
            self.assertFalse(pj.is_link(path), path)
        pj.refuse_link(plain)  # no exception

    def test_a_real_junction_or_symlink_is_a_link_and_is_refused(self):
        with planted(self, self.base / "link", self.target) as link:
            self.assertTrue(pj.is_link(link))
            self.assertTrue(pj.is_link(str(link)), "a str path as well")
            with self.assertRaises(GemArcError) as caught:
                pj.refuse_link(link)
            self.assertEqual(caught.exception.code, CODE)
            self.assertIn("link or junction", str(caught.exception))
        self.assertTrue((self.target / "sentinel.txt").exists())

    @unittest.skipUnless(os.name == "nt", "a junction is a Windows reparse point")
    def test_a_junction_is_seen_by_each_of_the_three_tests_and_by_the_attribute_alone(self):
        with planted(self, self.base / "junction", self.target) as link:
            self.assertFalse(link.is_symlink(), "Path.is_symlink misses a junction: the reason for this module's test")
            self.assertTrue(os.path.isjunction(link))
            self.assertTrue(os.lstat(link).st_file_attributes & pj._REPARSE_POINT)
            with mock.patch.object(os.path, "isjunction", return_value=False):  # an interpreter without isjunction: the attribute is enough
                self.assertTrue(pj.is_link(link))
            with mock.patch.object(os.path, "isjunction", create=True, new=None):
                self.assertTrue(pj.is_link(link))


class CheckConfinedTests(Flow):
    """``project.check_confined``: dist and out are in the list, and a link is refused even when it points inside the workspace."""

    def setUp(self):
        super().setUp()
        self.authored()
        self.outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        (self.outside / "sentinel.txt").write_text("keep me", encoding="utf-8")
        self.inside = self.root / "elsewhere"
        self.inside.mkdir()
        (self.inside / "sentinel.txt").write_text("keep me", encoding="utf-8")

    def sentinels(self):
        for folder in (self.outside, self.inside):
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["sentinel.txt"], folder)
            self.assertEqual((folder / "sentinel.txt").read_text(encoding="utf-8"), "keep me")

    def test_a_clean_project_passes(self):
        pj.check_confined(self.ws, self.paths)

    def test_dist_as_a_junction_is_refused_whether_it_points_outside_or_inside_the_workspace(self):
        dist = self.paths.atlas / "dist"
        for target in (self.outside, self.inside):
            with planted(self, dist, target):
                with self.assertRaises(GemArcError) as caught:
                    pj.check_confined(self.ws, self.paths)
                self.assertEqual(caught.exception.code, CODE)
            self.sentinels()

    def test_out_as_a_junction_is_refused(self):
        with planted(self, self.paths.out, self.inside):
            with self.assertRaises(GemArcError) as caught:
                pj.check_confined(self.ws, self.paths)
            self.assertEqual(caught.exception.code, CODE)
        self.sentinels()

    def test_every_write_tool_refuses_when_a_project_directory_is_a_junction_to_another_place_in_the_workspace(self):
        self.paths.user.rmdir()   # init made it (empty); a link can only take the place of a missing path
        with planted(self, self.paths.user, self.inside):
            reply = self.put("31-extra", "#atlas 1\n## tbds: id | title | decision | opts\nTBD-9 | A question | open\n")
            self.assertEqual((reply["status"], reply["code"]), ("invalid", CODE), reply)
        self.sentinels()


class InitLinkTests(Flow):
    """``init`` never creates anything through a link at the project's ``atlas`` directory, not even a dangling one."""

    def test_init_refuses_a_dangling_atlas_junction_cleanly(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        target = outside / "gone"
        target.mkdir()
        project = self.root / "linked"
        project.mkdir()
        with planted(self, project / "atlas", target) as link:
            shutil.rmtree(target)                       # the link now points at nothing
            self.assertFalse(link.exists())
            self.assertTrue(pj.is_link(link), "a dangling junction is still a link")
            reply = tools.init(self.ws, "linked", "T", "asis", ["en-US"])
            self.assertEqual((reply["status"], reply.get("code")), ("invalid", CODE), reply)
            self.assertNotIn("INTERNAL", str(reply))
            self.assertFalse(target.exists(), "nothing was created behind the link")
        self.assertEqual(sorted(p.name for p in project.iterdir()), [])

    def test_init_still_says_already_exists_for_an_ordinary_atlas_directory(self):
        self.assertEqual(tools.init(self.ws, "plain", "T", "asis", ["en-US"])["status"], "created")
        again = tools.init(self.ws, "plain", "T", "asis", ["en-US"])
        self.assertEqual(again["status"], "invalid", again)


class BuildLinkTests(Flow):
    """``build.py`` itself (no ``tools`` wrapper in front of it): every part the build deletes or writes is checked, and again where it deletes."""

    def setUp(self):
        super().setUp()
        self.authored()
        self.outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        (self.outside / "docs").mkdir()
        self.victim = self.outside / "docs" / "victim.txt"
        self.victim.write_text("keep me", encoding="utf-8")
        (self.outside / "sentinel.txt").write_text("keep me", encoding="utf-8")
        self.dist = self.paths.atlas / "dist"
        self.dist.mkdir(parents=True)

    def assert_untouched(self):
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "keep me")
        self.assertEqual((self.outside / "sentinel.txt").read_text(encoding="utf-8"), "keep me")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["docs", "sentinel.txt"])
        self.assertEqual(sorted(p.name for p in (self.outside / "docs").iterdir()), ["victim.txt"])

    def refused(self, call):
        with self.assertRaises(GemArcError) as caught:
            call()
        self.assertEqual(caught.exception.code, CODE)

    def test_check_dist_confined_refuses_a_link_at_each_part_of_dist(self):
        for part in ("docs", "diagrams", "prompts", ".verify"):
            with planted(self, self.dist / part, self.outside):
                self.refused(lambda: build._check_dist_confined(self.ws, self.paths))
            self.assert_untouched()
        (self.dist / ".verify").mkdir()
        with planted(self, self.dist / ".verify" / "work", self.outside):
            self.refused(lambda: build._check_dist_confined(self.ws, self.paths))
        self.assert_untouched()

    def test_check_dist_confined_refuses_dist_and_out_themselves(self):
        shutil.rmtree(self.dist)
        with planted(self, self.dist, self.outside):
            self.refused(lambda: build._check_dist_confined(self.ws, self.paths))
        with planted(self, self.paths.out, self.outside):
            self.refused(lambda: build._check_dist_confined(self.ws, self.paths))
        self.assert_untouched()

    def test_an_ordinary_dist_is_accepted_and_returned(self):
        self.assertEqual(build._check_dist_confined(self.ws, self.paths), self.dist)

    def test_clear_owned_never_goes_through_a_junction_at_an_owned_directory(self):
        (self.dist / "index.html").write_text("page", encoding="utf-8")
        (self.dist / "prompts").mkdir()
        (self.dist / "prompts" / "one.md").write_text("one", encoding="utf-8")
        with planted(self, self.dist / "docs", self.outside / "docs"):
            self.refused(lambda: build._clear_owned(self.dist))
            self.assertTrue((self.dist / "docs").exists(), "the link is refused, not removed")
            self.assertTrue((self.dist / "prompts" / "one.md").exists(), "nothing was deleted before the refusal")
            self.assertTrue((self.dist / "index.html").exists())
        self.assert_untouched()

    def test_clear_owned_refuses_a_junction_nested_inside_an_owned_directory_before_deleting_a_file(self):
        (self.dist / "docs").mkdir()
        for name in ("a.md", "b.md"):
            (self.dist / "docs" / name).write_text(name, encoding="utf-8")
        with planted(self, self.dist / "docs" / "nested", self.outside):
            self.refused(lambda: build._clear_owned(self.dist))
            self.assertEqual(sorted(p.name for p in (self.dist / "docs").iterdir()), ["a.md", "b.md", "nested"], "not one file was deleted")
        self.assert_untouched()

    def test_clear_owned_deletes_nothing_when_the_link_is_in_the_second_or_third_owned_directory(self):
        # debt-b4 fix: each owned directory used to be walked just before it was deleted, so "docs" was already gone when a junction
        # was found inside "diagrams"; the message said "nothing was written"
        for name in build.OWNED_DIRS:
            (self.dist / name / "sub").mkdir(parents=True)
            (self.dist / name / "sub" / "keep.txt").write_text(name, encoding="utf-8")
        for name in build.OWNED_FILES:
            (self.dist / name).write_text("x", encoding="utf-8")

        def tree():   # a link is listed but never entered (os.walk enters a junction on this interpreter)
            found, stack = [], [self.dist]
            while stack:
                for child in sorted(stack.pop().iterdir()):
                    found.append(str(child.relative_to(self.dist)))
                    if child.is_dir() and not pj.is_link(child):
                        stack.append(child)
            return sorted(found)

        for holder in build.OWNED_DIRS[1:]:
            before = tree()
            with planted(self, self.dist / holder / "sub" / "nested", self.outside):
                self.refused(lambda: build._clear_owned(self.dist))
                self.assertEqual(tree(), sorted([*before, str(Path(holder, "sub", "nested"))]), holder)
            self.assertTrue(all((self.dist / name / "sub" / "keep.txt").is_file() for name in build.OWNED_DIRS), holder)
            self.assertTrue(all((self.dist / name).is_file() for name in build.OWNED_FILES), holder)
            self.assert_untouched()

    def test_clear_owned_removes_real_directories_and_files(self):
        for name in build.OWNED_DIRS:
            (self.dist / name / "sub").mkdir(parents=True)
            (self.dist / name / "sub" / "x.txt").write_text("x", encoding="utf-8")
        for name in build.OWNED_FILES:
            (self.dist / name).write_text("x", encoding="utf-8")
        (self.dist / "oracle-sheet.md").write_text("sheet", encoding="utf-8")
        (self.dist / ".verify").mkdir()
        build._clear_owned(self.dist)
        self.assertEqual(sorted(p.name for p in self.dist.iterdir()), [".verify", "oracle-sheet.md"])

    def test_build_project_and_verify_project_refuse_a_junction_before_they_write(self):
        self.assertTrue(self.simulate()["clean"])
        with planted(self, self.dist / "docs", self.outside / "docs"):
            self.refused(lambda: build.build_project(self.ws, self.name))
            self.assertFalse((self.dist / "index.html").exists(), "nothing was written")
        self.assert_untouched()
        self.assertEqual(tools.build_all(self.ws, self.name)["status"], "ok")
        shutil.rmtree(self.dist / ".verify", ignore_errors=True)
        with planted(self, self.dist / ".verify", self.outside):
            self.refused(lambda: build.verify_project(self.ws, self.name))
        self.assert_untouched()

    def test_run_gate_refuses_a_junction_planted_at_verify_since_the_check_and_never_deletes_through_it(self):
        page = self.dist / "index.html"
        page.write_text("<html></html>", encoding="utf-8")
        work = self.outside / "work"
        work.mkdir()
        (work / "scratch.txt").write_text("keep me", encoding="utf-8")
        with planted(self, self.dist / ".verify", self.outside), mock.patch.object(smoke, "verify_gate", side_effect=AssertionError("no browser may start")):
            self.refused(lambda: build.run_gate(page, (1024,), self.dist))
        self.assertTrue((work / "scratch.txt").exists(), "the scratch directory behind the link was not removed")
        self.assert_untouched_except_work()

    def assert_untouched_except_work(self):
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "keep me")
        self.assertEqual((self.outside / "sentinel.txt").read_text(encoding="utf-8"), "keep me")

    def test_the_tool_replies_name_the_code_and_leave_everything_alone(self):
        self.assertTrue(self.simulate()["clean"])
        with planted(self, self.dist / "diagrams", self.outside):
            for label, call in (("build", lambda: tools.build_all(self.ws, self.name)), ("verify", lambda: tools.verify(self.ws, self.name)),
                                ("sheet", lambda: tools.oracle_sheet(self.ws, self.name))):
                reply = call()
                self.assertEqual((reply["status"], reply["code"]), ("invalid", CODE), (label, reply))
        self.assert_untouched()

    def test_the_gate_scratch_directory_is_removed_when_nothing_is_a_link(self):
        page = self.dist / "index.html"
        page.write_text("<html></html>", encoding="utf-8")
        verdict = {"status": "SKIP", "widths": [], "lines": [], "browser": None, "message": "x"}

        def fake(page_, widths, tmp_root=None, **kw):
            Path(tmp_root).mkdir(parents=True, exist_ok=True)
            (Path(tmp_root) / "scratch.txt").write_text("x", encoding="utf-8")
            return dict(verdict)

        with mock.patch.object(smoke, "verify_gate", side_effect=fake):
            build.run_gate(page, (1024,), self.dist)
        self.assertFalse((self.dist / ".verify" / "work").exists())
        self.assertTrue((self.dist / ".verify" / "gate-report.json").is_file())


if __name__ == "__main__":
    unittest.main()
