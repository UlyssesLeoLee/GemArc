"""
Real MCP handshake over Streamable HTTP against the deployed ASGI app.

`test_mcp_contract.py` replaces the whole SDK with a fake, so it proves the
decorator wiring but not that a client can actually talk to the server. This
module starts the real application and drives it with the official MCP client
over HTTP, so a broken transport, a missing session manager or a wrong
transport configuration fails here instead of in production.

Skipped when the MCP SDK is not installed, so the suite still runs offline.
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class HttpTransportTests(unittest.TestCase):
    server = None
    thread = None
    port = None
    workspace = None

    @classmethod
    def setUpClass(cls):
        import uvicorn

        cls.workspace = tempfile.mkdtemp(prefix="gemarc-http-test-")
        os.environ["GEMARC_WORKSPACE_ROOT"] = cls.workspace
        # Write access must stay off unless a test asks for it.
        os.environ["GEMARC_ALLOW_WRITE"] = "0"

        from api.index import app  # noqa: E402  (path set up above)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]

        config = uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()

        deadline = time.time() + 20
        while time.time() < deadline and not cls.server.started:
            time.sleep(0.05)
        if not cls.server.started:
            raise unittest.SkipTest("uvicorn did not start in time")

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.should_exit = True
        if cls.thread is not None:
            cls.thread.join(timeout=10)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _drive(self, body):
        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            results = {}
            async with streamable_http_client(self.url) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    results["server"] = init.server_info.name
                    results["protocol"] = init.protocol_version
                    results["tools"] = [t.name for t in (await session.list_tools()).tools]
                    results["resources"] = [str(r.uri) for r in (await session.list_resources()).resources]
                    results["prompts"] = [p.name for p in (await session.list_prompts()).prompts]

                    async def call(name, args):
                        reply = await session.call_tool(name, args)
                        return json.loads(reply.content[0].text)

                    for key, (tool, args) in body.items():
                        results[key] = await call(tool, args)
            return results

        return asyncio.run(run())

    def test_handshake_exposes_full_tool_surface(self):
        out = self._drive({})
        self.assertEqual(out["server"], "GemArc MCP")
        self.assertEqual(len(out["tools"]), 13)
        self.assertEqual(len(set(out["tools"])), 13)
        for name in ("gemarc.scaffold", "gemarc.contracts"):
            self.assertIn(name, out["tools"])
        self.assertEqual(out["resources"], ["gemarc://registry", "gemarc://schema/model"])
        self.assertEqual(out["prompts"], ["gemarc-design"])

    def test_read_tools_work_against_the_seeded_project(self):
        out = self._drive({
            "assets": ("gemarc.assets", {}),
            "plan": ("gemarc.plan", {"project": "demo"}),
            "validate": ("gemarc.validate", {"project": "demo"}),
        })
        self.assertEqual(out["assets"]["status"], "ok")
        self.assertEqual(out["plan"]["status"], "ok")
        self.assertEqual(out["validate"]["status"], "ok")

    def test_simulation_is_deterministic(self):
        out = self._drive({
            "simulate": ("gemarc.simulate", {"project": "demo", "scenario_id": "normal"}),
        })
        self.assertEqual(out["simulate"]["status"], "ok")
        self.assertEqual(out["simulate"]["summary"]["delivered"], 4)
        self.assertEqual(out["simulate"]["summary"]["blocked"], 0)

    def test_write_tools_are_refused_without_explicit_opt_in(self):
        out = self._drive({
            "init": ("gemarc.init", {"project": "http-test-should-not-exist"}),
            "build": ("gemarc.build", {"project": "demo"}),
            "validate": ("gemarc.validate", {"project": "http-test-should-not-exist"}),
        })
        for key in ("init", "build"):
            self.assertEqual(out[key]["status"], "invalid")
            self.assertEqual(out[key]["diagnostics"][0]["id"], "GEMARC_AUTH_002")
        # A refused write must not have created anything.
        self.assertEqual(out["validate"]["status"], "invalid")

    def test_paths_outside_the_workspace_are_refused(self):
        out = self._drive({"inspect": ("gemarc.inspect", {"path": ".."})})
        self.assertEqual(out["inspect"]["status"], "invalid")
        self.assertEqual(out["inspect"]["diagnostics"][0]["id"], "GEMARC_AUTH_001")

    def test_health_reports_deployment_state(self):
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/healthz", timeout=10
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["writeEnabled"])
        self.assertTrue(payload["staticSiteAvailable"])
        self.assertEqual(payload["sessionManagerError"], "")

    def test_static_site_is_served(self):
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/", timeout=10
        ) as response:
            body = response.read().decode("utf-8", "replace")
        self.assertIn("GemArc", body)
        self.assertIn("5D Architecture", body)


if __name__ == "__main__":
    unittest.main()
