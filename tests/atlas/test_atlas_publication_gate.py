"""Publication gate: nothing of the atlas is published until the user says so.

The user authorised PORTING generic engine/viewer code (2026-10-30). Pushing, deploying or exposing hosted routes
for ``src/gemarc_mcp/atlas`` is NOT authorised yet. While ``src/gemarc_mcp/atlas/PROVENANCE.md`` reads
``PUBLICATION: PENDING`` this test asserts the three facts that keep it that way:

1. ``.vercelignore`` excludes ``/src/gemarc_mcp/atlas`` (Vercel uploads ``src/**``; a deploy is a publication);
2. ``api/index.py`` registers no atlas route except the local opt-in ``/kit/atlas.zip``, which exists only behind an explicit
   ``GEMARC_ATLAS_KIT == "1"`` read (milestone M8): off by default, off for every other value, the same 404 and 405 answers
   as before, and a coded 503 where the atlas package is absent (``AtlasKitRouteTests``, including the upload set that
   ``.vercelignore`` leaves);
3. the default toolsets are unchanged: ``full`` locally, ``stateless`` (8 tools) on the hosted app;
4. no module that the hosted function loads imports the atlas package, or the later ``atlas_tools`` glue module,
   unconditionally. ``.vercelignore`` keeps ``src/gemarc_mcp/atlas`` out of the upload, and ``atlas_tools.py`` sits
   outside that directory, so an unguarded ``import`` in ``server.py``, ``service.py`` or ``api/index.py`` would
   break the hosted function at import time. An import inside a function, an ``if`` or a ``try`` is fine.

Flipping the publication row in ``src/gemarc_mcp/atlas/provenance/authorisation.tsv`` to GRANTED (a user edit, then
``python tools/gen_provenance.py``) switches facts 1 and 2 off; the marker format is still checked.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_atlas_publication_gate*.py"
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
VERCELIGNORE = REPO / ".vercelignore"
API_INDEX = REPO / "api" / "index.py"
PROVENANCE_MD = REPO / "src" / "gemarc_mcp" / "atlas" / "PROVENANCE.md"
ATLAS_IGNORE_LINE = "/src/gemarc_mcp/atlas"

MARKER_RE = re.compile(
    r"^PUBLICATION: (?P<state>PENDING|GRANTED by (?P<who>\S.*?) on (?P<date>\d{4}-\d{2}-\d{2}), scope (?P<scope>\S.*))$",
    re.MULTILINE,
)
STATELESS_TOOLS = (
    "gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model", "gemarc_validate", "gemarc_simulate",
    "gemarc_diff", "gemarc_render",
)


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def ignore_lines() -> list[str]:
    return [line.strip() for line in read_text(VERCELIGNORE).split("\n") if line.strip() and not line.strip().startswith("#")]


def publication_marker():
    """The single ``PUBLICATION:`` match of PROVENANCE.md, or None when it is missing or not unique."""
    if not PROVENANCE_MD.is_file():
        return None
    found = list(MARKER_RE.finditer(read_text(PROVENANCE_MD)))
    return found[0] if len(found) == 1 else None


def pending() -> bool:
    marker = publication_marker()
    return marker is None or marker.group("state") == "PENDING"  # a missing or malformed marker counts as PENDING


def _sdk_available() -> bool:
    try:
        import mcp.server  # noqa: F401
        import starlette  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class MarkerTests(unittest.TestCase):
    def test_provenance_carries_exactly_one_well_formed_publication_marker(self):
        self.assertTrue(PROVENANCE_MD.is_file(), "src/gemarc_mcp/atlas/PROVENANCE.md is missing (python tools/gen_provenance.py)")
        found = MARKER_RE.findall(read_text(PROVENANCE_MD))
        self.assertEqual(len(found), 1, "PROVENANCE.md must hold exactly one 'PUBLICATION: PENDING|GRANTED by <who> on <date>, scope <scope>' line")

    def test_publication_is_still_pending_unless_the_user_granted_it(self):
        marker = publication_marker()
        self.assertIsNotNone(marker)
        if marker.group("state") != "PENDING":
            self.assertTrue(marker.group("who") and marker.group("scope"), "a GRANTED marker names who and the scope")


class VercelIgnoreTests(unittest.TestCase):
    def test_atlas_is_excluded_from_the_vercel_upload(self):
        if not pending():
            self.skipTest("publication was granted by the user")
        self.assertIn(ATLAS_IGNORE_LINE, ignore_lines(),
                      ".vercelignore must list /src/gemarc_mcp/atlas while PUBLICATION is PENDING (Vercel uploads src/**)")

    def test_the_atlas_tool_binding_is_excluded_too_and_its_absence_is_a_clean_refusal(self):
        if not pending():
            self.skipTest("publication was granted by the user")
        self.assertIn("/src/gemarc_mcp/atlas_tools.py", ignore_lines())
        from gemarc_mcp import server
        from gemarc_mcp.paths import GemArcError
        with mock.patch.dict(sys.modules, {"gemarc_mcp.atlas_tools": None}):  # what a deploy without the file looks like
            with self.assertRaises(GemArcError) as caught:
                server.create_server("atlas_remote")
        self.assertEqual(caught.exception.code, "GEMARC_TOOLSET_001")

    def test_the_kit_output_directory_and_the_tools_are_excluded_too(self):
        """``tools/make_kit.py`` writes ``dist-kit/atlas-kit.zip`` (the whole atlas package in one archive) into the repository root: a later deploy must not upload it."""
        if not pending():
            self.skipTest("publication was granted by the user")
        for line in ("/dist-kit", "/tools", "/docs"):
            self.assertIn(line, ignore_lines(), f".vercelignore must list {line}")
        text = read_text(REPO / "tools" / "make_kit.py")
        self.assertIn('"dist-kit"', text, "the tool's default output directory is the one that is excluded")

    def test_no_negation_re_includes_the_atlas(self):
        if not pending():
            self.skipTest("publication was granted by the user")
        for line in ignore_lines():
            self.assertFalse(line.startswith("!") and "atlas" in line.lower(), f"negated ignore line {line!r}")

    def test_the_rest_of_the_function_inputs_are_not_ignored(self):
        lines = ignore_lines()
        for needed in ("/api", "/src", "/examples/demo-site", "pyproject.toml", "requirements.txt", "vercel.json"):
            self.assertNotIn(needed, lines, f".vercelignore must never ignore {needed}")

    def test_vercel_json_still_uploads_src(self):
        config = json.loads(read_text(REPO / "vercel.json"))
        include = config["builds"][0]["config"]["includeFiles"]
        self.assertIn("src/**", include, "the function reads src/gemarc_mcp at run time; the atlas is kept out through .vercelignore")


class ApiRoutesTests(unittest.TestCase):
    def test_api_index_has_no_atlas_route_unless_behind_the_explicit_env_switch(self):
        text = read_text(API_INDEX)
        if "atlas" not in text.lower():
            return  # nothing registered: the expected state today
        if not pending():
            self.skipTest("publication was granted by the user")
        self.assertRegex(text, r"""os\.environ\.get\(\s*["']GEMARC_ATLAS_KIT["']\s*\)\s*==\s*["']1["']""",
                         "api/index.py mentions atlas, but the route is not guarded by an exact GEMARC_ATLAS_KIT == '1' read")

    def test_no_other_runtime_entry_point_reads_the_kit_switch_yet(self):
        for path in (REPO / "src" / "gemarc_mcp").glob("*.py"):
            self.assertNotIn("GEMARC_ATLAS_KIT", read_text(path), f"{path.name}: the kit switch belongs to api/index.py only")

    def test_pyproject_does_not_expose_an_atlas_console_script(self):
        text = read_text(REPO / "pyproject.toml")
        scripts = text.split("[project.scripts]", 1)[1].split("[", 1)[0] if "[project.scripts]" in text else ""
        self.assertNotIn("atlas", scripts.lower(), "no atlas entry point is registered before publication is granted")


@unittest.skipUnless(_sdk_available(), "MCP SDK / starlette is not installed")
class LiveAppBase(unittest.TestCase):
    """Build the hosted ASGI app in-process (no server, no lifespan) and ask it for atlas routes (the helpers; the tests are in the subclasses)."""

    ENV_KEYS = ("GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_ATLAS_KIT")

    def setUp(self):
        from gemarc_mcp import server

        # create_server() rewrites these module-level lists; restore them so later tests are not affected.
        self._registered = list(server.REGISTERED_TOOLS)
        self._write = set(server.WRITE_TOOLS)
        self.server = server
        self.addCleanup(self._restore)
        patcher = mock.patch.dict(os.environ)  # build_app() writes GEMARC_ALLOW_WRITE / GEMARC_WORKSPACE_ROOT
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        self.server.REGISTERED_TOOLS[:] = self._registered
        self.server.WRITE_TOOLS.clear()
        self.server.WRITE_TOOLS.update(self._write)

    def build(self, **env):
        import shutil
        import tempfile

        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(env)
        if str(REPO) not in sys.path:  # test_http_transport does the same; the repo root holds the `api` package
            sys.path.insert(0, str(REPO))
        module = importlib.import_module("api.index")
        workspace = tempfile.mkdtemp(prefix="gemarc-gate-test-")
        self.addCleanup(shutil.rmtree, workspace, True)
        return module.build_app(token="", workspace_root=workspace)

    @staticmethod
    def get(app, path):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
                 "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1), "headers": [(b"host", b"gemarc.test")]}
        asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=20))
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")

class LiveAppTests(LiveAppBase):
    def test_hosted_default_is_the_stateless_toolset(self):
        health = self.build().state.gemarc_health()
        self.assertEqual(health["toolset"], "stateless")
        self.assertEqual(tuple(health["tools"]), STATELESS_TOOLS)
        self.assertEqual(health["writeTools"], [])

    def test_no_atlas_route_is_served(self):
        if not pending():
            self.skipTest("publication was granted by the user")
        for env in ({}, {"GEMARC_ATLAS_KIT": "0"}, {"GEMARC_ATLAS_KIT": "true"}):
            app = self.build(**env)
            for path in ("/kit/atlas.zip", "/atlas", "/atlas/build", "/api/atlas"):
                status, body = self.get(app, path)
                self.assertEqual(status, 404, f"{path} answered {status} with env {env}")
                self.assertNotIn(b"atlas", body.lower(), f"{path}: the 404 body mentions atlas")

    def test_kit_listing_does_not_advertise_an_atlas_profile(self):
        status, body = self.get(self.build(), "/kit/not-a-profile.zip")
        self.assertEqual(status, 404)
        self.assertNotIn("atlas", json.loads(body).get("profiles", []))


class AtlasKitRouteTests(LiveAppBase):
    """``/kit/atlas.zip`` (milestone M8): a LOCAL opt-in, behind the exact flag ``GEMARC_ATLAS_KIT=1``, that cannot be switched on in the deployed
    configuration (the atlas is not uploaded) and changes no answer of the default configuration."""

    @staticmethod
    def call(app, method, path, headers=()):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
                 "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1), "headers": [(b"host", b"gemarc.test"), *headers]}
        asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=30))
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")

    METHODS = ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS")
    OFF_VALUES = ("0", "", "true", "True", "yes", "on", "2", "01", " 1", "1 ", "1\n", "enabled", "ON", "11")

    def test_the_route_is_off_by_default_and_answers_exactly_like_any_unknown_kit(self):
        app = self.build()
        for method in self.METHODS:
            status, headers, body = self.call(app, method, "/kit/atlas.zip")
            reference = self.call(app, method, "/kit/no-such-profile.zip")
            self.assertEqual((status, body), (reference[0], reference[2]), f"{method}: the 404 of an unknown kit, nothing more")
            self.assertEqual(status, 404, method)
            self.assertNotIn(b"atlas", body.lower(), method)
            self.assertNotIn("content-disposition", headers)

    def test_the_route_stays_off_for_every_value_but_exactly_1(self):
        for value in self.OFF_VALUES:
            app = self.build(GEMARC_ATLAS_KIT=value)
            self.assertEqual(os.environ.get("GEMARC_ATLAS_KIT"), value)
            for method in ("GET", "POST", "DELETE"):
                status, _headers, body = self.call(app, method, "/kit/atlas.zip")
                reference = self.call(app, method, "/kit/no-such-profile.zip")
                self.assertEqual((status, body), (reference[0], reference[2]), f"flag {value!r}, {method}")
                self.assertEqual(status, 404, f"flag {value!r}, {method}")

    def test_with_the_flag_the_kit_is_served_as_one_deterministic_attachment(self):
        import hashlib
        import io
        import zipfile

        app = self.build(GEMARC_ATLAS_KIT="1")
        status, headers, body = self.call(app, "GET", "/kit/atlas.zip")
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(headers["content-type"], "application/zip")
        self.assertIn("attachment", headers["content-disposition"])
        self.assertEqual(headers["etag"], '"%s"' % hashlib.sha256(body).hexdigest())
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertIn("atlas-kit/atlas.py", names)
        self.assertEqual(self.call(app, "GET", "/kit/atlas.zip")[2], body, "the same bytes on every call")
        self.assertEqual(self.call(app, "GET", "/kit/atlas.zip", [(b"if-none-match", headers["etag"].encode())])[0], 304)
        self.assertEqual(self.call(app, "HEAD", "/kit/atlas.zip")[0], 200)
        status, headers, _body = self.call(app, "POST", "/kit/atlas.zip")
        self.assertEqual((status, headers["allow"]), (405, "GET, HEAD, OPTIONS"))
        self.assertEqual(self.call(app, "OPTIONS", "/kit/atlas.zip")[0], 204)

    def test_with_the_flag_the_other_kit_answers_are_unchanged(self):
        plain = self.build()
        flagged = self.build(GEMARC_ATLAS_KIT="1")
        for path in ("/kit/no-such-profile.zip", "/kit/static.zip", "/kit/", "/kit/atlas", "/kit/atlas.zip.bak", "/kit/Atlas.zip", "/kit/../atlas.zip"):
            self.assertEqual(self.call(plain, "GET", path)[::2], self.call(flagged, "GET", path)[::2], path)
        status, _headers, body = self.call(flagged, "GET", "/kit/no-such-profile.zip")
        self.assertEqual(status, 404)
        self.assertNotIn("atlas", json.loads(body)["profiles"], "the kit listing never advertises the atlas profile")

    def test_without_the_atlas_package_the_route_fails_closed_with_a_code(self):
        import gemarc_mcp.atlas as atlas_package

        app = self.build(GEMARC_ATLAS_KIT="1")
        saved = atlas_package.__dict__.pop("kit", None)       # a package that was imported earlier keeps its submodule as an attribute
        self.addCleanup(lambda: saved is not None and atlas_package.__dict__.__setitem__("kit", saved))
        for missing in ({"gemarc_mcp.atlas.kit": None}, {"gemarc_mcp.atlas": None, "gemarc_mcp.atlas.kit": None}):
            with mock.patch.dict(sys.modules, missing):
                status, headers, body = self.call(app, "GET", "/kit/atlas.zip")
            self.assertEqual(status, 503, missing)
            self.assertEqual(json.loads(body), {"error": "atlas_kit_unavailable", "code": "GEMARC_ATLAS_KIT_001"})
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertNotIn(b"/", body.replace(b'"', b""), "no path in the answer")

    def test_a_tree_that_cannot_build_the_kit_fails_closed_with_the_kit_code(self):
        from gemarc_mcp.atlas import kit

        app = self.build(GEMARC_ATLAS_KIT="1")
        with mock.patch.object(kit, "entries", side_effect=kit.KitError("docs are missing here")):
            status, _headers, body = self.call(app, "GET", "/kit/atlas.zip")
        self.assertEqual((status, json.loads(body)), (503, {"error": "atlas_kit_unavailable", "code": "GEMARC_ATLAS_KIT_001"}))

    def test_the_flag_is_read_in_one_place_in_the_exact_form_and_the_atlas_import_is_lazy(self):
        text = read_text(API_INDEX)
        reads = re.findall(r"""os\.environ\.get\(\s*["']GEMARC_ATLAS_KIT["']\s*\)""", text)
        self.assertEqual(len(reads), 1, "one read of the flag")
        self.assertRegex(text, r'os\.environ\.get\("GEMARC_ATLAS_KIT"\) == "1"')
        tree = ast.parse(text)
        module_level = [n.lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and "atlas" in ast.dump(n)]
        self.assertEqual(module_level, [], "no module-level import of the atlas in api/index.py")

    def test_the_vercel_upload_set_cannot_serve_the_kit_even_with_the_flag(self):
        """The upload as ``.vercelignore`` leaves it (the real file is applied): the flag is set and the route answers 503 ``GEMARC_ATLAS_KIT_001``;
        the atlas package cannot be imported there; the default answers are the same as everywhere."""
        import shutil
        import subprocess
        import tempfile

        prefixes = [line.lstrip("/").rstrip("/") for line in ignore_lines() if line.startswith("/")]
        self.assertIn("src/gemarc_mcp/atlas", prefixes)
        with tempfile.TemporaryDirectory(prefix="gemarc-upload-") as tmp:
            upload = Path(tmp)
            for base in ("api", "src", "examples/demo-site"):
                for path in sorted((REPO / base).rglob("*")):
                    relative = path.relative_to(REPO).as_posix()
                    if not path.is_file() or "__pycache__" in path.parts or any(relative == p or relative.startswith(p + "/") for p in prefixes):
                        continue
                    (upload / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, upload / relative)
            for name in ("pyproject.toml", "requirements.txt", "vercel.json"):
                shutil.copyfile(REPO / name, upload / name)
            self.assertFalse((upload / "src" / "gemarc_mcp" / "atlas").exists())
            self.assertFalse((upload / "src" / "gemarc_mcp" / "atlas_tools.py").exists())
            script = (
                "import asyncio, json, sys\n"
                "sys.path.insert(0, 'api_dir')\n"
                "from api.index import build_app\n"
                "app = build_app(token='', workspace_root=sys.argv[1])\n"
                "def call(method, path):\n"
                "    sent = []\n"
                "    async def receive(): return {'type': 'http.request', 'body': b'', 'more_body': False}\n"
                "    async def send(m): sent.append(m)\n"
                "    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method, 'path': path, 'raw_path': path.encode(),\n"
                "             'query_string': b'', 'root_path': '', 'scheme': 'http', 'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 1),\n"
                "             'headers': [(b'host', b'x')]}\n"
                "    asyncio.run(app(scope, receive, send))\n"
                "    return next(m for m in sent if m['type'] == 'http.response.start')['status'], b''.join(m.get('body', b'') for m in sent if m['type'] == 'http.response.body').decode()\n"
                "try:\n"
                "    import gemarc_mcp.atlas\n"
                "    importable = True\n"
                "except ImportError:\n"
                "    importable = False\n"
                "print(json.dumps({'importable': importable, 'kit': call('GET', '/kit/atlas.zip'), 'other': call('GET', '/kit/nope.zip')}))\n"
            ).replace("api_dir", str(upload).replace("\\", "/"))
            workspace = upload / "workspace"
            workspace.mkdir()
            for flag in ("1", "0"):
                done = subprocess.run([sys.executable, "-I", "-c", script, str(workspace)], capture_output=True, text=True, timeout=120, cwd=str(upload),
                                      env={**{k: v for k, v in os.environ.items() if not k.startswith("GEMARC_")}, "GEMARC_ATLAS_KIT": flag, "PYTHONPATH": ""})
                self.assertEqual(done.returncode, 0, done.stderr[-800:])
                result = json.loads(done.stdout.strip().splitlines()[-1])
                self.assertFalse(result["importable"], "the upload does not contain the atlas package")
                status, body = result["kit"]
                if flag == "1":
                    self.assertEqual((status, json.loads(body)), (503, {"error": "atlas_kit_unavailable", "code": "GEMARC_ATLAS_KIT_001"}))
                else:
                    self.assertEqual(status, 404)
                    self.assertEqual(result["kit"], result["other"], "flag off: the same 404 as any unknown kit")
                    self.assertNotIn("atlas", body.lower())


class PackagingTests(unittest.TestCase):
    """A wheel install must carry the vocabulary profile (the checker cannot load without it)."""

    def test_pyproject_package_data_ships_the_atlas_profiles(self):
        import fnmatch
        import tomllib

        data = tomllib.loads(read_text(REPO / "pyproject.toml"))
        globs = data["tool"]["setuptools"]["package-data"]["gemarc_mcp"]
        profiles = sorted(p.relative_to(REPO / "src" / "gemarc_mcp").as_posix() for p in (REPO / "src" / "gemarc_mcp" / "atlas" / "profiles").glob("*.json"))
        self.assertTrue(profiles)
        for name in profiles:
            self.assertTrue(any(fnmatch.fnmatch(name, pattern) for pattern in globs), f"{name} is not covered by package-data: wheel installs would miss it")


# ---------------------------------------------------------------------------------------------------------------
# 4. no unconditional import of the atlas by the modules the hosted function loads
# ---------------------------------------------------------------------------------------------------------------

SRC_PACKAGE = REPO / "src" / "gemarc_mcp"
GUARDED_MODULES = ("gemarc_mcp.atlas", "gemarc_mcp.atlas_tools")


def _targets(node) -> list[tuple[str, int]]:
    """Dotted module names an import statement may bring in, as ``(name, relative level)``."""
    if isinstance(node, ast.Import):
        return [(alias.name, 0) for alias in node.names]
    base = node.module or ""
    names = [base] + [f"{base}.{alias.name}" if base else alias.name for alias in node.names]
    return [(name, node.level) for name in names if name]


def _is_guarded_target(name: str, level: int, own_package: str) -> bool:
    """True when ``name`` (resolved against ``own_package`` for relative imports) is the atlas package or ``atlas_tools``."""
    if level:
        parts = own_package.split(".")
        parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
        name = ".".join(parts + ([name] if name else []))
    return any(name == target or name.startswith(target + ".") for target in GUARDED_MODULES)


def unconditional_atlas_imports(source: str, own_package: str = "gemarc_mcp") -> list[int]:
    """Line numbers of imports of the atlas (or ``atlas_tools``) that run when the module is imported, unguarded.

    Statements at module level and in class bodies count; anything inside a function, an ``if``, a ``try`` or a
    ``with`` block that is itself inside one of those does not.
    """
    found: list[int] = []

    def visit(statements) -> None:
        for node in statements:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if any(_is_guarded_target(name, level, own_package) for name, level in _targets(node)):
                    found.append(node.lineno)
            elif isinstance(node, ast.ClassDef):
                visit(node.body)
            elif isinstance(node, ast.With):
                visit(node.body)
            # FunctionDef, If, Try and loops are conditional: not visited

    visit(ast.parse(source).body)
    return found


def hosted_modules() -> list[Path]:
    """Python files the hosted function can load: the v1 package without the atlas folder and ``atlas_tools``, and ``api/``."""
    out = []
    for path in sorted(SRC_PACKAGE.rglob("*.py")):
        relative = path.relative_to(SRC_PACKAGE).as_posix()
        if relative.startswith("atlas/") or relative == "atlas_tools.py" or "__pycache__" in path.parts:
            continue
        out.append(path)
    out += sorted((REPO / "api").glob("*.py"))
    return out


class HostedFunctionImportTests(unittest.TestCase):
    def test_no_hosted_module_imports_the_atlas_unconditionally(self):
        offenders = []
        for path in hosted_modules():
            package = "gemarc_mcp" if path.is_relative_to(SRC_PACKAGE) and path.parent == SRC_PACKAGE else (
                "gemarc_mcp." + ".".join(path.relative_to(SRC_PACKAGE).parts[:-1]) if path.is_relative_to(SRC_PACKAGE) else "api")
            lines = unconditional_atlas_imports(path.read_text(encoding="utf-8"), package)
            offenders += [f"{path.relative_to(REPO).as_posix()}:{line}" for line in lines]
        self.assertEqual(offenders, [], "the hosted function would fail to import (src/gemarc_mcp/atlas is not uploaded): " + ", ".join(offenders))

    def test_the_hosted_modules_are_found(self):
        names = {path.name for path in hosted_modules()}
        self.assertTrue({"server.py", "service.py", "index.py"} <= names, names)
        self.assertFalse([p for p in hosted_modules() if "atlas" in p.relative_to(REPO).parts[:3] and p.name != "index.py"])

    def test_the_detector_flags_unguarded_imports_and_accepts_guarded_ones(self):
        flagged = [
            "import gemarc_mcp.atlas\n",
            "from gemarc_mcp.atlas import model\n",
            "from gemarc_mcp import atlas\n",
            "from gemarc_mcp import atlas_tools\n",
            "from . import atlas\n",
            "from .atlas import model\n",
            "from .atlas_tools import register\n",
            "from gemarc_mcp.atlas.model import x as y\n",
            "import os\nwith open(os.devnull):\n    from .atlas import model\n",
            "class A:\n    from .atlas import model\n",
        ]
        for source in flagged:
            self.assertEqual(len(unconditional_atlas_imports(source)), 1, source)
        guarded = [
            "def f():\n    from .atlas import model\n    return model\n",
            "import os\nif os.environ.get('X') == '1':\n    from .atlas_tools import register\n",
            "try:\n    from .atlas_tools import register\nexcept ImportError:\n    register = None\n",
            "async def g():\n    import gemarc_mcp.atlas\n",
            "from gemarc_mcp import model\nfrom .model import validate\nimport gemarc_mcp.atlasx\n",
            "from . import atlasx\n",
        ]
        for source in guarded:
            self.assertEqual(unconditional_atlas_imports(source), [], source)

    def test_relative_imports_resolve_against_the_package_of_the_file(self):
        self.assertEqual(unconditional_atlas_imports("from .. import atlas\n", "gemarc_mcp.scaffold"), [1])
        self.assertEqual(unconditional_atlas_imports("from .. import atlas\n", "other.pkg"), [])
        self.assertEqual(unconditional_atlas_imports("from . import atlas\n", "gemarc_mcp.scaffold"), [])


if __name__ == "__main__":
    unittest.main()
