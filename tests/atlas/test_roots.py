"""Roots: env-only configuration, alias arguments, path sandbox (escape, symlink/junction, case)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import roots


def make_link(link: Path, target: Path, *, directory: bool) -> str | None:
    """Create a symlink, falling back to a Windows directory junction. Returns the mechanism used or None."""
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


class ParseRootsTests(unittest.TestCase):
    def test_pairs_with_windows_drive_letters(self):
        table = roots.parse_roots("main=D:/src/app;web=D:\\src\\web")
        self.assertEqual(list(table), ["main", "web"])
        self.assertEqual(table["main"], Path(os.path.normpath("D:/src/app")))
        self.assertEqual(table["web"], Path(os.path.normpath("D:\\src\\web")))

    def test_whitespace_trailing_separator_and_quotes(self):
        table = roots.parse_roots('  a = D:/x ;b="D:/y with space";')
        self.assertEqual(sorted(table), ["a", "b"])
        self.assertEqual(table["b"], Path(os.path.normpath("D:/y with space")))

    def test_blank_and_none_give_no_roots(self):
        self.assertEqual(roots.parse_roots(None), {})
        self.assertEqual(roots.parse_roots("  "), {})
        self.assertEqual(roots.parse_roots(";;"), {})

    def test_rooted_path_without_a_drive_is_absolute_on_posix_and_drive_relative_on_windows(self):
        if os.name == "nt":
            for path in ("/srv/app", "/tmp/y", "\\tmp\\y"):
                with self.subTest(path=path), self.assertRaises(roots.RootsError) as cm:
                    roots.parse_roots(f"r={path}")
                self.assertEqual(cm.exception.code, roots.E_RELATIVE)
            self.assertEqual(list(roots.parse_roots("r=//server/share/x;u=\\\\server\\share\\y")), ["r", "u"])
        else:
            self.assertEqual(roots.parse_roots("r=/srv/app")["r"], Path(os.path.normpath("/srv/app")))
        with mock.patch.object(roots.os, "name", "posix"):
            self.assertTrue(roots._is_absolute_root("/srv/app"))
        with mock.patch.object(roots.os, "name", "nt"):
            self.assertFalse(roots._is_absolute_root("/srv/app"))
            self.assertTrue(roots._is_absolute_root("D:/srv/app"))

    def test_errors_are_reported_together(self):
        with self.assertRaises(roots.RootsError) as cm:
            roots.parse_roots("noequals;bad alias=D:/x;ok=D:/y;ok=D:/z;rel=relative/dir;empty=")
        message = str(cm.exception)
        for fragment in ("has no '='", "'bad alias'", "declared twice", "must be an absolute path", "empty path"):
            self.assertIn(fragment, message)
        self.assertEqual(cm.exception.code, roots.E_SPEC)

    def test_alias_must_be_a_token(self):
        for alias in ("", "-x", "a b", "a/b", "../x", "a=b"):
            with self.subTest(alias=alias), self.assertRaises(roots.RootsError):
                roots.parse_roots(f"{alias}=D:/x")

    def test_relative_roots_are_rejected_even_with_drive_relative_form(self):
        for path in ("dir", "./dir", "..\\dir", "D:dir"):
            with self.subTest(path=path), self.assertRaises(roots.RootsError) as cm:
                roots.parse_roots(f"a={path}")
            self.assertEqual(cm.exception.code, roots.E_RELATIVE)

    def test_missing_directory_is_not_a_parse_error(self):
        table = roots.parse_roots("gone=D:/definitely/not/here/zz")
        self.assertFalse(roots.root_available(table["gone"]))


class EnvOnlyTests(unittest.TestCase):
    def test_roots_come_from_the_named_variable(self):
        self.assertEqual(roots.ENV_NAME, "GEMARC_ATLAS_ROOTS")
        table = roots.roots_from_env({"GEMARC_ATLAS_ROOTS": "m=D:/m", "OTHER": "o=D:/o"})
        self.assertEqual(list(table), ["m"])

    def test_default_environment_is_os_environ(self):
        with mock.patch.dict(os.environ, {"GEMARC_ATLAS_ROOTS": "viaenv=D:/e"}):
            self.assertEqual(list(roots.roots_from_env()), ["viaenv"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMARC_ATLAS_ROOTS", None)
            self.assertEqual(roots.roots_from_env(), {})

    def test_a_path_where_an_alias_is_expected_is_an_error(self):
        for value in ("D:/GemArc", "/etc", "../x", "a/b", "a\\b", "~/x", ".", "C:", "x:y"):
            with self.subTest(value=value), self.assertRaises(roots.RootsError) as cm:
                roots.require_alias(value)
            self.assertEqual(cm.exception.code, roots.E_ARGUMENT)
        for value in ("", None, 5, "-x"):
            with self.subTest(value=value), self.assertRaises(roots.RootsError):
                roots.require_alias(value)
        self.assertEqual(roots.require_alias("main"), "main")
        self.assertEqual(roots.require_alias("web-2.v1"), "web-2.v1")

    def test_root_arguments_are_refused_by_name(self):
        for key in ("root", "roots", "rootPath", "root_dir", "repo-path", "workspace", "cwd"):
            with self.subTest(key=key), self.assertRaises(roots.RootsError) as cm:
                roots.forbid_root_arguments({"repo": "main", key: "D:/x"})
            self.assertEqual(cm.exception.code, roots.E_ARGUMENT)
        roots.forbid_root_arguments({"repo": "main", "file": "src/a.py", "start": 0})

    def test_root_like_names_are_refused_in_any_spelling(self):
        for key in ("project_dir", "projectDir", "repo_root", "repoRoot", "baseDirectory", "base_directory", "work-dir", "source dir",
                    "folder", "ROOT"):
            with self.subTest(key=key), self.assertRaises(roots.RootsError) as cm:
                roots.forbid_root_arguments({key: "x"})
            self.assertEqual(cm.exception.code, roots.E_ARGUMENT)

    def test_a_tool_accepts_only_its_declared_arguments(self):
        declared = ("repo", "file", "start")
        roots.require_declared_arguments({"repo": "main", "file": "a.py"}, declared)
        for bad in ({"repo": "main", "mount_point": "D:/x"}, {"repo": "main", "Repo": "x"}, {"unknown": 1}, {"project_dir": "x"}):
            with self.subTest(bad=bad), self.assertRaises(roots.RootsError) as cm:
                roots.require_declared_arguments(bad, declared)
            self.assertEqual(cm.exception.code, roots.E_ARGUMENT)

    def test_error_text_names_the_env_variable(self):
        with self.assertRaises(roots.RootsError) as cm:
            roots.require_alias("D:/GemArc")
        self.assertIn("GEMARC_ATLAS_ROOTS", str(cm.exception))


class CleanRelativeTests(unittest.TestCase):
    def test_accepts_plain_paths_and_normalises(self):
        self.assertEqual(roots.clean_relative("src/a.py"), ["src", "a.py"])
        self.assertEqual(roots.clean_relative("./src//a.py"), ["src", "a.py"])
        self.assertEqual(roots.clean_relative("src\\sub\\a.py"), ["src", "sub", "a.py"])

    def test_rejections_have_distinct_codes(self):
        cases = {
            "/etc/passwd": roots.E_ABSOLUTE, "\\\\server\\share\\x": roots.E_ABSOLUTE,
            "C:/Windows/win.ini": roots.E_DRIVE, "C:rel.txt": roots.E_DRIVE, "a.txt:stream": roots.E_DRIVE,
            "../x": roots.E_DOTDOT, "a/../../x": roots.E_DOTDOT, "a\\..\\x": roots.E_DOTDOT,
            "NUL": roots.E_NAME, "dir/con.txt": roots.E_NAME, "LPT1": roots.E_NAME, "trail.": roots.E_NAME,
            "trail ": roots.E_NAME, "a/b|c": roots.E_NAME, "a?b": roots.E_NAME,
            "": roots.E_PATH_TYPE, "   ": roots.E_PATH_TYPE, "a\0b": roots.E_PATH_TYPE, ".": roots.E_PATH_TYPE, "a\nb": roots.E_PATH_TYPE,
        }
        for value, code in cases.items():
            with self.subTest(value=value), self.assertRaises(roots.RootsError) as cm:
                roots.clean_relative(value)
            self.assertEqual(cm.exception.code, code)
        for value in (None, 5, b"x", ["a"]):
            with self.subTest(value=value), self.assertRaises(roots.RootsError):
                roots.clean_relative(value)


class ResolveInRootTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "repo"
        self.outside = base / "outside"
        (self.root / "Sub").mkdir(parents=True)
        self.outside.mkdir()
        (self.root / "Sub" / "File.TXT").write_text("inside\n", encoding="utf-8")
        (self.root / "plain.txt").write_text("plain\n", encoding="utf-8")
        (self.outside / "secret.txt").write_text("outside secret\n", encoding="utf-8")

    def test_existing_file(self):
        found = roots.resolve_in_root(self.root, "plain.txt")
        self.assertTrue(found.exists)
        self.assertEqual(found.rel, "plain.txt")
        self.assertIsNone(found.case_mismatch)
        self.assertTrue(roots.is_regular_file(found.path))

    def test_missing_file_is_not_an_error(self):
        found = roots.resolve_in_root(self.root, "nope/none.txt")
        self.assertFalse(found.exists)
        self.assertIsNone(found.path)

    def test_directory_is_not_a_regular_file(self):
        found = roots.resolve_in_root(self.root, "Sub")
        self.assertTrue(found.exists)
        self.assertFalse(roots.is_regular_file(found.path))

    def test_dotdot_and_absolute_are_errors_even_when_the_target_exists(self):
        with self.assertRaises(roots.RootsError):
            roots.resolve_in_root(self.root, "../outside/secret.txt")
        with self.assertRaises(roots.RootsError):
            roots.resolve_in_root(self.root, str(self.outside / "secret.txt"))
        with self.assertRaises(roots.RootsError):
            roots.resolve_in_root(self.root, "sub/../../outside/secret.txt")

    def test_letter_case_mismatch_is_reported_with_the_on_disk_name(self):
        found = roots.resolve_in_root(self.root, "sub/file.txt")
        self.assertTrue(found.exists, "the file is found by case-insensitive listing on every platform")
        self.assertEqual(found.case_mismatch, "Sub/File.TXT")
        self.assertEqual(found.rel, "Sub/File.TXT")
        exact = roots.resolve_in_root(self.root, "Sub/File.TXT")
        self.assertIsNone(exact.case_mismatch)

    def test_case_mismatch_on_the_directory_only(self):
        found = roots.resolve_in_root(self.root, "SUB/File.TXT")
        self.assertEqual(found.case_mismatch, "Sub/File.TXT")

    @unittest.skipUnless(sys.platform == "win32", "Windows file systems accept a case-different name")
    def test_this_windows_machine_really_accepts_the_wrong_case(self):
        # documents why a mismatch is a warning: the OS itself would have opened the wrong-case path
        self.assertTrue((self.root / "sub" / "file.txt").exists())
        self.assertIsNotNone(roots.resolve_in_root(self.root, "sub/file.txt").case_mismatch)

    def test_symlink_or_junction_directory_escape(self):
        link = self.root / "link"
        mechanism = make_link(link, self.outside, directory=True)
        if mechanism is None:
            print("SKIPPED: this OS refused to create a symlink and a junction; escape through a directory link NOT tested", file=sys.stderr)
            self.skipTest("cannot create a symlink or junction on this machine")
        sys.stderr.write(f"[test_roots] directory escape tested with a {mechanism}\n")
        with self.assertRaises(roots.RootsError) as cm:
            roots.resolve_in_root(self.root, "link/secret.txt")
        self.assertEqual(cm.exception.code, roots.E_ESCAPE)

    def test_symlink_file_escape(self):
        link = self.root / "stolen.txt"
        mechanism = make_link(link, self.outside / "secret.txt", directory=False)
        if mechanism is None:
            print("SKIPPED: this OS refused to create a file symlink; escape through a file link NOT tested", file=sys.stderr)
            self.skipTest("cannot create a file symlink on this machine")
        with self.assertRaises(roots.RootsError) as cm:
            roots.resolve_in_root(self.root, "stolen.txt")
        self.assertEqual(cm.exception.code, roots.E_ESCAPE)

    def test_dangling_link_to_the_outside_is_an_escape_too(self):
        link = self.root / "dangling"
        mechanism = make_link(link, self.outside / "does-not-exist", directory=False)
        if mechanism is None:
            self.skipTest("cannot create a symlink on this machine")
        with self.assertRaises(roots.RootsError):
            roots.resolve_in_root(self.root, "dangling")

    def test_link_that_stays_inside_the_root_is_allowed(self):
        link = self.root / "inner"
        mechanism = make_link(link, self.root / "Sub", directory=True)
        if mechanism is None:
            self.skipTest("cannot create a symlink or junction on this machine")
        found = roots.resolve_in_root(self.root, "inner/File.TXT")
        self.assertTrue(found.exists)

    def test_root_that_does_not_exist(self):
        found = roots.resolve_in_root(self.root / "missing", "a.txt")
        self.assertFalse(found.exists)


if __name__ == "__main__":
    unittest.main()
