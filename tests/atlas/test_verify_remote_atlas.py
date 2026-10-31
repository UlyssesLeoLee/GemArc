"""``scripts/verify_remote.py --atlas`` (milestone M8): the optional extra checks of the atlas_remote tool set and of the kit route, tested only
against LOCALLY started ASGI apps (real HTTP under uvicorn); the script is never pointed at a deployment here.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_verify_remote_atlas*.py"

* an ``atlas_remote`` app with ``GEMARC_ATLAS_KIT=1``: every ``--atlas`` check passes (two read-only tools, advisory lint, the kit served as one zip);
* the same app without the flag: the kit route is the plain 404 of an unknown kit; ``--atlas-kit on`` fails, ``off`` and ``auto`` pass;
* flag on, atlas package absent: the route fails closed with ``GEMARC_ATLAS_KIT_001`` and the probe says so;
* a ``stateless`` app (the deployed default): ``--atlas`` FAILS on purpose (the atlas is not deployed);
* without ``--atlas`` the probe prints no atlas line at all (its behaviour is unchanged; the existing tests of the default path stay green).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_remote.py"
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _available() -> bool:
    try:
        import mcp.server  # noqa: F401
        import starlette  # noqa: F401
        import uvicorn  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@contextlib.contextmanager
def serve(toolset: str, **env):
    """A local ASGI app of the hosted function under uvicorn; yields its base URL. ``env`` is set for the whole time (the app reads it per request)."""
    import uvicorn

    from gemarc_mcp import server

    registered, writes = list(server.REGISTERED_TOOLS), set(server.WRITE_TOOLS)
    workspace = tempfile.mkdtemp(prefix="atlas-vr-")
    keys = ("GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_ATLAS_KIT", "GEMARC_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(env)
    try:
        from api.index import build_app

        app = build_app(token="", workspace_root=workspace, toolset=toolset)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        instance = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
        thread = threading.Thread(target=instance.run, daemon=True)
        thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not instance.started:
            time.sleep(0.05)
        if not instance.started:
            raise unittest.SkipTest("uvicorn did not start in time")
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            instance.should_exit = True
            thread.join(timeout=10)
    finally:
        patcher.stop()
        server.REGISTERED_TOOLS[:] = registered
        server.WRITE_TOOLS.clear()
        server.WRITE_TOOLS.update(writes)
        shutil.rmtree(workspace, ignore_errors=True)


def probe(url: str, *args: str) -> "tuple[int, str]":
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), url, *args], capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace",
                          env={k: v for k, v in os.environ.items() if k != "GEMARC_MCP_TOKEN"})
    return done.returncode, done.stdout + done.stderr


@unittest.skipUnless(_available(), "MCP SDK, starlette or uvicorn is not installed")
class AtlasProbeTests(unittest.TestCase):
    def test_every_atlas_check_passes_on_an_atlas_remote_app_with_the_kit_flag(self):
        with serve("atlas_remote", GEMARC_ATLAS_KIT="1") as url:
            code, out = probe(url, "--atlas")
            self.assertEqual(code, 0, out)
            for line in ("PASS  --atlas: the server runs the atlas_remote tool set", "PASS  --atlas: tools/list is exactly the two atlas tools",
                         "PASS  --atlas: both tools are read-only", "PASS  --atlas: gemarc_atlas_brief returns the work order",
                         "PASS  --atlas: gemarc_atlas_check lints a valid slice", "PASS  --atlas: gemarc_atlas_check reports a broken slice",
                         "PASS  --atlas: gemarc_atlas_check refuses a text above 32 KB with isError", "PASS  --atlas: no reply names a filesystem path",
                         "PASS  --atlas: the kit route is allowed here", "PASS  --atlas: kit download is a zip with the launcher and the atlas package",
                         "PASS  --atlas: kit ETag is the sha256 of the archive and If-None-Match gives 304"):
                self.assertIn(line, out)
            self.assertIn("0 failed", out)
            code, out = probe(url, "--atlas", "--atlas-kit", "on")
            self.assertEqual(code, 0, out)
            code, out = probe(url, "--atlas", "--atlas-kit", "off")
            self.assertEqual(code, 1, "the route serves the kit but the operator said it must be off")
            self.assertIn("FAIL  --atlas: the kit route is allowed here", out)

    def test_without_the_flag_the_route_is_the_plain_404_and_the_probe_says_so(self):
        with serve("atlas_remote") as url:
            code, out = probe(url, "--atlas")
            self.assertEqual(code, 0, out)
            self.assertIn("PASS  --atlas: the kit route is off", out)
            self.assertEqual(probe(url, "--atlas", "--atlas-kit", "off")[0], 0)
            code, out = probe(url, "--atlas", "--atlas-kit", "on")
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL  --atlas: the kit route is off", out)

    def test_flag_on_with_the_atlas_absent_fails_closed_and_the_probe_reports_the_code(self):
        import gemarc_mcp.atlas as atlas_package

        saved = atlas_package.__dict__.pop("kit", None)
        try:
            with serve("atlas_remote", GEMARC_ATLAS_KIT="1") as url, mock.patch.dict(sys.modules, {"gemarc_mcp.atlas.kit": None}):
                code, out = probe(url, "--atlas")
        finally:
            if saved is not None:
                atlas_package.kit = saved
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  --atlas: the kit route is on but fails closed with a code", out)
        self.assertIn("GEMARC_ATLAS_KIT_001", out)

    def test_atlas_kit_on_requires_the_zip_and_does_not_accept_the_503(self):
        """``--atlas-kit on`` means the route must serve the kit: the coded 503 of a server that has the flag but not the atlas is a FAIL there (and a PASS under auto)."""
        import gemarc_mcp.atlas as atlas_package

        saved = atlas_package.__dict__.pop("kit", None)
        try:
            with serve("atlas_remote", GEMARC_ATLAS_KIT="1") as url, mock.patch.dict(sys.modules, {"gemarc_mcp.atlas.kit": None}):
                auto_code, auto_out = probe(url, "--atlas", "--atlas-kit", "auto")
                on_code, on_out = probe(url, "--atlas", "--atlas-kit", "on")
                off_code, off_out = probe(url, "--atlas", "--atlas-kit", "off")
        finally:
            if saved is not None:
                atlas_package.kit = saved
        self.assertEqual(auto_code, 0, auto_out)
        self.assertEqual(on_code, 1, "on: the route must serve the zip, and it answered the fail-closed 503")
        self.assertIn("FAIL  --atlas: the kit route is on but fails closed with a code", on_out)
        self.assertIn("HTTP 503", on_out)
        self.assertEqual(off_code, 1, "off: the route is not absent")

    def test_a_stateless_app_fails_the_atlas_checks_on_purpose(self):
        with serve("stateless") as url:
            code, out = probe(url, "--atlas")
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL  --atlas: the server runs the atlas_remote tool set", out)
            self.assertIn("toolset='stateless'", out)
            self.assertIn("PASS  --atlas: the kit route is off", out, "the kit route is still checked, and it is off")
            plain_code, plain = probe(url)
            self.assertEqual(plain_code, 0, plain)
            self.assertNotIn("--atlas", plain, "without the flag the probe prints no atlas line")

    def test_without_the_flag_an_atlas_remote_app_gets_no_atlas_line(self):
        with serve("atlas_remote", GEMARC_ATLAS_KIT="1") as url:
            code, out = probe(url)
            self.assertNotIn("--atlas", out)
            self.assertNotIn("kit/atlas", out)
            self.assertIn("tools/list matches the advertised tool set", out)
            self.assertIn("PASS  tools/list matches the advertised tool set", out)

    def test_the_flags_are_validated_and_documented(self):
        code, out = probe("http://127.0.0.1:9", "--atlas-kit", "maybe")
        self.assertEqual(code, 2, out)
        self.assertIn("invalid choice", out)
        help_text = subprocess.run([sys.executable, "-I", str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace").stdout
        self.assertIn("--atlas", help_text)
        self.assertIn("--atlas-kit", help_text)
        self.assertIn("atlas_remote", help_text)


if __name__ == "__main__":
    unittest.main()
