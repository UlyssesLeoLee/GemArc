"""The atlas kit (milestone M8): ``tools/make_kit.py`` and ``gemarc_mcp.atlas.kit`` build ``atlas-kit.zip``, a stdlib-only copy of the atlas core with
a launcher, and a client with a shell but no MCP runs it.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_kit*.py"

* the archive is deterministic and complete (the atlas package, the two v1 modules it imports, the specifications, README with Chinese primary text
  and English and Japanese lines, no build artefacts, no ``mcp``);
* the kit is unzipped into a temporary directory and run with ``python -I -S atlas.py`` (isolated, no site-packages: ``mcp`` cannot be imported there,
  and the test proves it in that very kind of process), and with the BASE interpreter without ``-S`` when that has no ``mcp`` either, on a project that the
  scripted pipeline produced: ``check``, ``status``, ``evidence`` and ``build`` work, and ``build`` writes the very bytes the repository build writes;
* a tripwire run (``sys.modules['mcp'] = None`` before the launcher) shows that no module of the kit even tries to import ``mcp``.

Nothing here writes ``dist-kit/`` into the repository: the zips go to temporary directories. Synthetic data only.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from gemarc_mcp.atlas import kit

from . import pipeline as P

REPO = Path(__file__).resolve().parents[2]
_BASE: dict = {}


def setUpModule() -> None:
    tmp = tempfile.TemporaryDirectory(prefix="atlas-kit-")
    _BASE["tmp"] = tmp
    base = Path(tmp.name)
    (base / "ws").mkdir()
    (base / "scratch").mkdir()
    run = P.run_fixture("tiny", 1, base / "ws", base / "scratch", browser=False)   # a finished project, produced by the scripted pipeline
    assert run["final"]["done"], run["final"]
    archive = kit.build_zip()
    (base / "kit").mkdir()
    zipfile.ZipFile(io.BytesIO(archive)).extractall(base / "kit")
    _BASE.update(base=base, roots=run["roots"], archive=archive, kit=base / "kit" / "atlas-kit", counter=0)


def tearDownModule() -> None:
    _BASE["tmp"].cleanup()


def clean_env(**extra) -> dict:
    """What a bare shell client gives a child process: nothing of this test process but what Windows needs to start Python."""
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "COMSPEC", "PATH") if key in os.environ}
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def stdlib_only(source: str) -> list:
    """Top-level module names imported by ``source`` that are neither the standard library nor ``gemarc_mcp`` (relative imports are the package's own)."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
        for name in names:
            top = name.split(".")[0]
            if top and top not in sys.stdlib_module_names and top != "gemarc_mcp":
                bad.append(name)
    return bad


class ArchiveTests(unittest.TestCase):
    def test_the_archive_is_deterministic(self):
        again = kit.build_zip()
        self.assertEqual(again, _BASE["archive"])
        self.assertEqual(kit.sha256_hex(again), hashlib.sha256(_BASE["archive"]).hexdigest())

    def test_the_archive_holds_what_the_kit_needs_and_nothing_else(self):
        names = zipfile.ZipFile(io.BytesIO(_BASE["archive"])).namelist()
        self.assertEqual(names, sorted(names), "sorted: the archive does not depend on the directory order of the host")
        for needed in ("atlas-kit/atlas.py", "atlas-kit/README.md", "atlas-kit/gemarc_mcp/__init__.py", "atlas-kit/gemarc_mcp/paths.py",
                       "atlas-kit/gemarc_mcp/atlas/cli.py", "atlas-kit/gemarc_mcp/atlas/build.py", "atlas-kit/gemarc_mcp/atlas/profiles/software.json",
                       "atlas-kit/gemarc_mcp/atlas/viewer/dist/atlas.template.html", "atlas-kit/gemarc_mcp/atlas/viewer/SOURCES.sha256",
                       "atlas-kit/gemarc_mcp/atlas/gate/selftest.js", "atlas-kit/gemarc_mcp/atlas/brief/all.zh-CN.md",
                       "atlas-kit/docs/atlas/spec/model-schema.md", "atlas-kit/docs/atlas/spec/bundle-contract.md"):
            self.assertIn(needed, names)
        for name in names:
            self.assertFalse("__pycache__" in name or name.endswith((".pyc", ".pyo")), name)
            self.assertNotIn("atlas_tools", name, "the MCP binding is not part of the kit")
            self.assertNotEqual(name, "atlas-kit/gemarc_mcp/atlas/kit.py", "the builder is not shipped inside what it builds")
            self.assertFalse(name.startswith(("atlas-kit/gemarc_mcp/atlas/viewer/src/", "atlas-kit/gemarc_mcp/atlas/viewer/i18n/")), name)
        self.assertFalse([n for n in names if n.startswith("atlas-kit/gemarc_mcp/") and n.count("/") == 2 and n not in ("atlas-kit/gemarc_mcp/__init__.py", "atlas-kit/gemarc_mcp/paths.py")],
                         "of the v1 package only the two modules the core imports")
        self.assertLessEqual(len(_BASE["archive"]), 2 * 1024 * 1024)

    def test_the_archive_holds_exactly_the_pinned_list_of_files(self):
        """The route rebuilds the archive from the live tree on every GET, so a new file in the package must be a visible decision: the exact name list is
        pinned in ``kit_names.txt`` (one sorted name per line, LF; after a deliberate change regenerate it from ``kit.entries()``:
        ``PYTHONPATH=src python -c "from gemarc_mcp.atlas import kit; open('tests/atlas/kit_names.txt','w',newline='\\n').write('\\n'.join(n for n,_ in kit.entries())+'\\n')"``)."""
        names = zipfile.ZipFile(io.BytesIO(_BASE["archive"])).namelist()
        pinned = (REPO / "tests" / "atlas" / "kit_names.txt").read_bytes().decode("utf-8").split("\n")
        self.assertEqual(pinned.pop(), "", "the pinned list ends with a newline")
        self.assertEqual(names, pinned)

    def test_a_stray_file_next_to_the_package_cannot_ride_along(self):
        """An allowlist, not 'everything under the package': a copy of the package tree with a key, a note, a project ledger, a test and a hidden directory."""
        work = Path(_BASE["base"]) / "strays"
        package = work / "src" / "gemarc_mcp"
        shutil.copytree(REPO / "src" / "gemarc_mcp" / "atlas", package / "atlas", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(REPO / "src" / "gemarc_mcp" / "__init__.py", package / "__init__.py")
        shutil.copy2(REPO / "src" / "gemarc_mcp" / "paths.py", package / "paths.py")
        shutil.copytree(REPO / "docs" / "atlas" / "spec", work / "docs" / "atlas" / "spec")
        spec = importlib.util.spec_from_file_location("kit_in_the_copy", package / "atlas" / "kit.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        clean = [name for name, _ in module.entries(work)]
        self.assertEqual(clean, zipfile.ZipFile(io.BytesIO(_BASE["archive"])).namelist(), "control: the copy without strays gives the very archive of the tree")
        atlas = package / "atlas"
        strays = {".env": b"TOKEN=secret\n", "NOTES.local.txt": b"private\n", "secret.pem": b"key\n", ".atlas/oracle.json": b"{}\n", ".hidden/notes.md": b"x\n",
                  "tests/test_x.py": b"import mcp\n", "test/helper.py": b"x = 1\n", "viewer/dist/.cache.json": b"{}\n", "provenance/.draft.tsv": b"x\n",
                  "profiles/software.json.bak": b"{}\n", "brief/notes.docx": b"x\n", "__pycache__/x.cpython-312.pyc": b"x"}
        for relative, data in strays.items():
            target = atlas / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            (atlas / "link.py").symlink_to(atlas / "cli.py")
        except (OSError, NotImplementedError):
            pass                                                         # no privilege for a symlink on this machine: the cases above still run
        self.assertEqual([name for name, _ in module.entries(work)], clean, "none of the strays is packed")
        self.assertEqual(module.build_zip(work), module.build_zip(work), "still deterministic")

    def test_the_launcher_prints_its_own_name_in_help(self):
        done = subprocess.run([sys.executable, "-I", "-S", "atlas.py", "--help"], capture_output=True, text=True, timeout=120, cwd=str(_BASE["kit"]), encoding="utf-8",
                              errors="replace", env=clean_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("usage: atlas.py "), done.stdout[:120])
        self.assertNotIn("python -m gemarc_mcp.atlas", done.stdout.splitlines()[0])
        module = subprocess.run([sys.executable, "-B", "-m", "gemarc_mcp.atlas", "--help"], capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                                env=clean_env(PYTHONPATH=str(REPO / "src")))
        self.assertTrue(module.stdout.startswith("usage: python -m gemarc_mcp.atlas "), "the module entry keeps its own name")

    def test_the_readme_maps_the_tool_names_of_the_replies_to_kit_commands(self):
        text = zipfile.ZipFile(io.BytesIO(_BASE["archive"])).read("atlas-kit/README.md").decode("utf-8")
        self.assertIn("`gemarc_atlas_<名>`", text)
        self.assertIn("`python -I atlas.py <名>`", text)

    def test_no_text_file_has_a_carriage_return_and_no_host_path_is_inside(self):
        with zipfile.ZipFile(io.BytesIO(_BASE["archive"])) as archive:
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)
                if info.filename.endswith((".py", ".md", ".json", ".js", ".css", ".html", ".tsv", ".toml")):
                    data = archive.read(info.filename)
                    self.assertNotIn(b"\r", data, info.filename)
                    self.assertNotIn(str(REPO).encode("utf-8"), data, info.filename)
                    self.assertNotIn(str(REPO).replace("\\", "/").encode("utf-8"), data, info.filename)

    def test_every_python_file_of_the_kit_uses_only_the_standard_library(self):
        with zipfile.ZipFile(io.BytesIO(_BASE["archive"])) as archive:
            checked = 0
            for name in archive.namelist():
                if name.endswith(".py"):
                    checked += 1
                    self.assertEqual(stdlib_only(archive.read(name).decode("utf-8")), [], name)
        self.assertGreater(checked, 40)
        self.assertEqual(stdlib_only("import mcp\nfrom starlette import x\nimport os\nfrom . import y\nfrom gemarc_mcp import z\n"), ["mcp", "starlette"], "the detector bites")

    def test_the_readme_is_chinese_with_english_and_japanese_lines(self):
        text = zipfile.ZipFile(io.BytesIO(_BASE["archive"])).read("atlas-kit/README.md").decode("utf-8")
        self.assertIn("# GemArc Atlas 工具包", text)
        self.assertIn("English:", text)
        self.assertIn("日本語：", text)
        for command in ("brief", "init", "put", "check", "status", "simulate", "evidence", "build", "review"):
            self.assertIn(f"atlas.py {command}", text)
        for variable in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE", "GEMARC_ATLAS_ROOTS"):
            self.assertIn(variable, text)

    def test_the_maintainer_tool_writes_the_same_bytes_and_leaves_the_repository_alone(self):
        before = (REPO / "dist-kit").exists()
        out = Path(_BASE["base"]) / "tool" / "kit.zip"
        done = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "make_kit.py"), "--out", str(out)], capture_output=True, text=True, timeout=120,
                              env=clean_env(PYTHONPATH=str(REPO / "src")))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(out.read_bytes(), _BASE["archive"])
        self.assertIn(kit.sha256_hex(_BASE["archive"]), done.stdout)
        self.assertEqual((REPO / "dist-kit").exists(), before, "an explicit --out never creates dist-kit/ in the repository")
        check = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "make_kit.py"), "--check"], capture_output=True, text=True, timeout=120, env=clean_env(PYTHONPATH=str(REPO / "src")))
        self.assertEqual((check.returncode, check.stdout.split(":")[0]), (0, "deterministic"))
        listing = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "make_kit.py"), "--list"], capture_output=True, text=True, timeout=120, env=clean_env(PYTHONPATH=str(REPO / "src")))
        self.assertIn("atlas-kit/atlas.py", listing.stdout)

    def test_the_kit_directory_is_not_committed(self):
        ignore = (REPO / ".gitignore").read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
        self.assertIn("dist-kit/", ignore)
        self.assertFalse(subprocess.run(["git", "-C", str(REPO), "ls-files", "dist-kit"], capture_output=True, text=True).stdout.strip(), "nothing under dist-kit/ is tracked")


class RunWithoutMcpTests(unittest.TestCase):
    """The kit in a Python that cannot import ``mcp``, on a project the scripted pipeline made."""

    def setUp(self):
        _BASE["counter"] += 1
        self.work = Path(_BASE["base"]) / f"run{_BASE['counter']}"
        self.ws = self.work / "ws"
        shutil.copytree(Path(_BASE["base"]) / "ws", self.ws)
        self.env = clean_env(GEMARC_WORKSPACE_ROOT=str(self.ws), GEMARC_ALLOW_WRITE="1",
                             GEMARC_ATLAS_ROOTS=";".join(f"{a}={p}" for a, p in _BASE["roots"].items()))

    def atlas(self, *args, python=None, flags=("-I", "-S")):
        done = subprocess.run([python or sys.executable, *flags, "atlas.py", *args], capture_output=True, text=True, timeout=300, cwd=str(_BASE["kit"]),
                              env=self.env, encoding="utf-8", errors="replace")
        return done

    def test_mcp_is_not_importable_in_the_process_that_runs_the_kit(self):
        probe = subprocess.run([sys.executable, "-I", "-S", "-c", "import importlib.util as u; print(u.find_spec('mcp'))"], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual((probe.returncode, probe.stdout.strip()), (0, "None"), probe.stderr)
        sane = subprocess.run([sys.executable, "-I", "-c", "import importlib.util as u; print(u.find_spec('mcp') is not None)"], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(sane.stdout.strip(), "True", "the control: the same interpreter WITH its site-packages does see mcp (so '-S' is what hides it)")

    def test_check_runs_on_a_scripted_project_with_python_dash_I(self):
        done = self.atlas("check", "demo", "--mode", "full", "--human")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        again = self.atlas("check", "demo", "--mode", "full", "--format", "json")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["status"], "ok")

    def test_check_runs_with_the_base_interpreter_when_that_has_no_mcp(self):
        base = getattr(sys, "_base_executable", None) or sys.executable
        probe = subprocess.run([base, "-I", "-c", "import importlib.util as u; print(u.find_spec('mcp') is None)"], capture_output=True, text=True, env=self.env, timeout=60)
        if probe.stdout.strip() != "True":
            self.skipTest("the base interpreter of this machine has mcp installed: the '-I -S' runs above are the proof here")
        done = self.atlas("check", "demo", "--mode", "full", "--human", python=base, flags=("-I",))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_tripwire_shows_that_nothing_of_the_kit_imports_mcp(self):
        code = ("import runpy, sys; sys.modules['mcp'] = None; sys.modules['starlette'] = None; sys.modules['anyio'] = None; "
                "sys.argv = ['atlas.py', 'check', 'demo', '--mode', 'full', '--human']; "
                "sys.exit(runpy.run_path('atlas.py', run_name='__main__') and 0)")
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=300, cwd=str(_BASE["kit"]), env=self.env, encoding="utf-8", errors="replace")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("ImportError", done.stderr)

    def test_the_whole_shell_flow_works_and_builds_the_repository_bytes(self):
        status = self.atlas("status", "demo")
        self.assertEqual(status.returncode, 0, status.stderr)
        state = json.loads(status.stdout)
        self.assertEqual((state["done"], state["phase"]), (True, "done"), state)
        evidence = self.atlas("evidence", "demo")
        self.assertEqual(evidence.returncode, 0, evidence.stdout + evidence.stderr)
        self.assertEqual(json.loads(evidence.stdout)["fail"], 0)
        dist = self.ws / "demo" / "atlas" / "dist"
        reference = hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()
        shutil.rmtree(dist)
        built = self.atlas("build", "demo")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        reply = json.loads(built.stdout)
        self.assertEqual(reply["status"], "ok", reply)
        self.assertEqual(hashlib.sha256((dist / "index.html").read_bytes()).hexdigest(), reference, "the kit builds the very page the repository builds")
        for rel in ("docs/en-US/BD.md", "build-info.json", "oracle-sheet.md"):
            self.assertTrue((dist / rel).is_file(), rel)
        brief = self.atlas("brief", "--section", "format", "--lang", "zh-CN")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("atlas", brief.stdout.lower())

    def test_a_release_without_the_documents_is_refused_by_the_kit_and_dist_keeps_its_files(self):
        dist = self.ws / "demo" / "atlas" / "dist"

        def files():
            return {p.relative_to(dist).as_posix(): p.read_bytes() for p in sorted(dist.rglob("*")) if p.is_file() and p.name != "oracle-sheet.md"}
        before = files()
        self.assertTrue([n for n in before if n.startswith("docs/")], "the finished project has documents in dist/")
        done = self.atlas("build", "demo", "--release", "--no-docs", "--gate", "smoke")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        reply = json.loads(done.stdout)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_ATLAS_BUILD_020"), reply)
        self.assertTrue(any("docs=true" in b for b in reply["blockers"]), reply["blockers"])
        self.assertEqual(files(), before, "the documents, diagrams and prompts are still there")

    def test_the_kit_without_the_write_flag_still_checks_and_reads(self):
        self.env["GEMARC_ALLOW_WRITE"] = "0"
        self.assertEqual(self.atlas("check", "demo", "--mode", "full", "--human").returncode, 0)
        self.assertEqual(self.atlas("status", "demo").returncode, 0)
        refused = self.atlas("build", "demo")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("GEMARC_AUTH_002", refused.stdout + refused.stderr)

    def test_a_project_can_be_created_from_nothing_by_the_kit(self):
        fresh = self.work / "fresh"
        fresh.mkdir()
        self.env["GEMARC_WORKSPACE_ROOT"] = str(fresh)
        made = self.atlas("init", "tiny-kit", "--title", "From the kit", "--mode", "tobe", "--languages", "en-US")
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.assertEqual(sorted(p.name for p in fresh.iterdir()), ["tiny-kit"])
        space = "#atlas 1 mode=tobe\n## buildings: id | name | opts\nzone.a | Area | trust=internal\n## floors: id | building | name | level\nlvl.a | zone.a | Floor | 1\n## rooms: id | floor | name | opts\nrm.a | lvl.a | Room | tier=T1\n"
        (self.work / "space.atl").write_text(space, encoding="utf-8", newline="\n")
        put = self.atlas("put", "tiny-kit", "00-space", "--file", str(self.work / "space.atl"))
        self.assertEqual(put.returncode, 0, put.stdout + put.stderr)
        self.assertEqual(json.loads(put.stdout)["status"], "committed")


if __name__ == "__main__":
    unittest.main()
