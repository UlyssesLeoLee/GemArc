"""
Real MCP handshake over Streamable HTTP against the deployed ASGI app.

`test_mcp_contract.py` replaces the whole SDK with a fake, so it proves the
decorator wiring but not that a client can actually talk to the server. This
module starts the real application (``api.index.build_app``) under uvicorn and
drives it with the official MCP client and with plain HTTP, so a broken
transport, a missing session manager or a wrong configuration fails here
instead of in production.

The remote default is the ``stateless`` tool set (8 tools, model passed inline).
``FullToolsetTests`` covers the operator opt-in ``GEMARC_TOOLSET=full``.

Skipped when the MCP SDK is not installed, so the suite still runs offline.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

FUNCTION_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
STATELESS_TOOLS = {
    "gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model",
    "gemarc_validate", "gemarc_simulate", "gemarc_diff", "gemarc_render",
}
WRITE_TOOLS = {"gemarc_build", "gemarc_export", "gemarc_init", "gemarc_model", "gemarc_scaffold"}
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "gemarc-test", "version": "0"}}}
#: Environment the app reads. Each live server saves and restores it, so a test
#: run never leaks configuration into the next test module.
ENV_KEYS = ("GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT",
            "GEMARC_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")


def sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        import httpx2  # noqa: F401
        return True
    except Exception:
        return False


def probe_model() -> dict:
    """A tiny valid model (3 nodes, 2 edges, 1 view, 1 scenario) with ASCII text only."""
    def node(ident, kind, name, layer):
        return {"attributes": [], "domainId": None, "evidence": "probe", "evidenceIds": [], "id": ident,
                "kind": kind, "layer": layer, "methods": [], "name": name, "parentId": None,
                "properties": {}, "role": None, "spaceRef": None, "verification": "Hypothetical"}

    def edge(ident, source, target):
        return {"condition": None, "description": "probe edge", "directed": True, "evidence": "probe",
                "evidenceIds": [], "id": ident, "properties": {}, "sourceId": source, "spaceSpan": None,
                "targetId": target, "trustBoundary": None, "type": "sends",
                "verification": "Hypothetical", "weight": None}

    return {
        "cases": [], "evidence": [], "prototypes": [], "workflows": [], "space": None,
        "edges": [edge("e1", "src", "svc"), edge("e2", "svc", "out")],
        "meta": {"description": "probe model, not real facts", "evidenceState": "Hypothetical",
                 "language": "en", "schemaVersion": "0.1.0", "system": "probe", "version": "0.1.0"},
        "nodes": [node("src", "Component", "Source", "edge"), node("svc", "Service", "Service", "app"),
                  node("out", "Service", "Output", "app")],
        "scenarios": [{"events": [{"event": "emit", "module": "src", "timeMs": 0}],
                       "evidenceClass": "hypothetical", "id": "normal", "name": "probe flow",
                       "rules": [{"delayMs": 10, "emit": "ingest", "source": "src", "target": "svc", "whenEvent": "emit"},
                                 {"delayMs": 10, "emit": "send", "source": "svc", "target": "out", "whenEvent": "ingest"}]}],
        "views": [{"concerns": ["flow"], "diagramType": "logical-view", "edgeKinds": ["sends"],
                   "id": "logical", "nodeIds": ["src", "svc", "out"], "reviewIntent": "probe",
                   "stakeholders": ["architect"], "title": "Logical"}],
    }


class LiveServer:
    """``api.index.build_app(**kwargs)`` under uvicorn on a random port, inside this process."""

    def __init__(self, *, lifespan: str = "on", **build_kwargs):
        self.lifespan = lifespan
        self.build_kwargs = build_kwargs
        self.app = self.server = self.thread = self.port = self.workspace = None
        self._saved = {}

    def start(self):
        import uvicorn

        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        # The module-level ``app`` is built at import time from the environment:
        # make sure that default instance is the plain open one.
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.workspace = tempfile.mkdtemp(prefix="gemarc-http-test-")
        from api.index import build_app

        kwargs = {"workspace_root": self.workspace, **self.build_kwargs}
        self.app = build_app(**kwargs)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error",
                                lifespan=self.lifespan)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not self.server.started:
            time.sleep(0.05)
        if not self.server.started:
            self.stop()
            raise unittest.SkipTest("uvicorn did not start in time")
        return self

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self.workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


def http(url, method="GET", headers=None, body=None, timeout=10):
    """(status, headers, body) for any status, including 4xx/5xx and 304."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def rpc(url, method, params=None, headers=None, rid=1):
    """One JSON-RPC POST; returns (status, headers, parsed body or None)."""
    payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    status, response_headers, raw = http(
        url, "POST",
        {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
         **(headers or {})},
        json.dumps(payload).encode("utf-8"))
    try:
        return status, response_headers, json.loads(raw)
    except ValueError:
        return status, response_headers, None


def drive(url, calls=None, headers=None):
    """Official-client handshake; ``calls`` maps a key to (tool, arguments)."""
    async def run():
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        results = {}
        async with httpx2.AsyncClient(headers=headers or {}, timeout=30) as client:
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    results["server"] = init.server_info.name
                    results["protocol"] = init.protocol_version
                    results["tools"] = [t.name for t in (await session.list_tools()).tools]
                    results["resources"] = [str(r.uri) for r in (await session.list_resources()).resources]
                    results["prompts"] = [p.name for p in (await session.list_prompts()).prompts]
                    for key, (tool, args) in (calls or {}).items():
                        reply = await session.call_tool(tool, args)
                        results[key] = json.loads(reply.content[0].text)
                        results[key + "_isError"] = bool(reply.is_error)
        return results

    return asyncio.run(run())


@contextlib.contextmanager
def built_app(**build_kwargs):
    """``api.index.build_app`` with a clean, restored environment; no server is started."""
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    workspace = tempfile.mkdtemp(prefix="gemarc-http-test-")
    try:
        from api.index import build_app

        yield build_app(workspace_root=workspace, **build_kwargs)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(workspace, ignore_errors=True)


async def asgi_post(app, payload=INITIALIZE, path="/mcp"):
    """One hand-rolled ASGI request (no server, no lifespan); returns the status code."""
    body = json.dumps(payload).encode("utf-8")
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "scheme": "http", "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1),
             "headers": [(b"host", b"gemarc.test"), (b"content-type", b"application/json"),
                         (b"accept", b"application/json, text/event-stream"),
                         (b"content-length", str(len(body)).encode())]}
    await asyncio.wait_for(app(scope, receive, send), timeout=20)
    return next(m for m in sent if m["type"] == "http.response.start")["status"]


def health(server):
    status, _, raw = http(server.url("/healthz"))
    assert status == 200, status
    return json.loads(raw)


FILESYSTEM_SHAPES = (re.compile(r"^[A-Za-z]:[\\/]"), re.compile(r"\\"), re.compile(r"^/(tmp|var|home|usr|opt|Users|private|workspace)\b"))


def leaked_paths(value):
    """String values of a JSON document that look like server filesystem locations."""
    found = []
    if isinstance(value, dict):
        for item in value.values():
            found += leaked_paths(item)
    elif isinstance(value, list):
        for item in value:
            found += leaked_paths(item)
    elif isinstance(value, str):
        if any(shape.search(value) for shape in FILESYSTEM_SHAPES):
            found.append(value)
    return found


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class RemoteDefaultTests(unittest.TestCase):
    """The default remote configuration: stateless tool set, open endpoint, lifespan on."""

    @classmethod
    def setUpClass(cls):
        cls.live = LiveServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()

    def mcp(self, path="/mcp"):
        return self.live.url(path)

    # -- protocol ------------------------------------------------------------

    def test_handshake_exposes_the_stateless_tool_surface(self):
        out = drive(self.mcp())
        self.assertEqual(out["server"], "GemArc MCP")
        self.assertEqual(set(out["tools"]), STATELESS_TOOLS)
        self.assertEqual(len(out["tools"]), 8)
        for name in out["tools"]:
            self.assertRegex(name, FUNCTION_NAME)
        self.assertEqual(out["resources"], ["gemarc://registry", "gemarc://schema/model"])
        self.assertEqual(out["prompts"], ["gemarc-design"])

    def test_write_and_workspace_tools_are_absent(self):
        out = drive(self.mcp())
        for name in WRITE_TOOLS | {"gemarc_inspect", "gemarc_plan", "gemarc_verify"}:
            self.assertNotIn(name, out["tools"])
        report = health(self.live)
        self.assertEqual(report["toolset"], "stateless")
        self.assertEqual(report["writeTools"], [])
        self.assertEqual(sorted(report["tools"]), sorted(STATELESS_TOOLS))

    def test_the_demo_project_is_seeded_for_validate_and_simulate(self):
        out = drive(self.mcp(), {
            "assets": ("gemarc_assets", {}),
            "validate": ("gemarc_validate", {"project": "demo"}),
            "simulate": ("gemarc_simulate", {"project": "demo", "scenario_id": "normal"}),
            "missing": ("gemarc_validate", {"project": "http-test-should-not-exist"}),
        })
        self.assertEqual(out["assets"]["status"], "ok")
        self.assertEqual(out["validate"]["status"], "ok")
        self.assertEqual(out["simulate"]["status"], "ok")
        self.assertEqual(out["simulate"]["summary"]["delivered"], 4)
        self.assertEqual(out["simulate"]["summary"]["blocked"], 0)
        self.assertEqual(out["missing"]["status"], "invalid")
        self.assertTrue(out["missing_isError"])

    def test_inline_model_calls_work_without_any_workspace(self):
        model = probe_model()
        out = drive(self.mcp(), {
            "validate": ("gemarc_validate", {"model": model, "strict": False}),
            "simulate": ("gemarc_simulate", {"model": model, "scenario_id": "normal"}),
            "diff": ("gemarc_diff", {"base_model": model, "target_model": model}),
            "both": ("gemarc_validate", {"project": "demo", "model": model}),
        })
        self.assertEqual(out["validate"]["status"], "ok")
        self.assertRegex(out["validate"]["modelSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(out["simulate"]["summary"]["delivered"], 3)
        self.assertEqual(out["diff"]["status"], "ok")
        # Exactly one source is required: both at once is refused, flagged as an error.
        self.assertEqual(out["both"]["status"], "invalid")
        self.assertTrue(out["both_isError"])

    def test_new_model_validate_render_round_trip(self):
        first = drive(self.mcp(), {"new": ("gemarc_new_model", {"system": "Round Trip"})})
        self.assertEqual(first["new"]["status"], "ok")
        model = first["new"]["model"]
        self.assertEqual(model["meta"]["system"], "Round Trip")
        self.assertTrue(first["new"]["slots"])

        second = drive(self.mcp(), {
            "validate": ("gemarc_validate", {"model": model}),
            "render": ("gemarc_render", {"model": model, "profile": "static"}),
            "render_probe": ("gemarc_render", {"model": probe_model(), "profile": "vite"}),
        })
        self.assertEqual(second["validate"]["status"], "ok")
        self.assertEqual(second["render"]["status"], "ok")
        paths = [item["path"] for item in second["render"]["files"]]
        self.assertEqual(paths, ["data/architecture.json", "data/scenarios.json", "data/capabilities.json"])
        self.assertEqual(second["render"]["kit"]["path"], "/kit/static.zip")
        self.assertEqual(second["render_probe"]["kit"]["path"], "/kit/vite.zip")
        self.assertTrue(second["render"]["inline"])

    def test_the_mcp_path_is_normalised(self):
        for path in ("/mcp", "/mcp/", "/api/index", "/api/index.py", "/index.py"):
            status, _, body = rpc(self.mcp(path), "tools/list")
            self.assertEqual(status, 200, path)
            names = {tool["name"] for tool in body["result"]["tools"]}
            self.assertEqual(names, STATELESS_TOOLS, path)

    # -- the method gate -----------------------------------------------------

    def test_only_post_reaches_the_sdk(self):
        """GET would open an SSE stream that pins a serverless function until it times out."""
        for path in ("/mcp", "/mcp/", "/api/index"):
            for method in ("GET", "HEAD", "DELETE", "PUT", "PATCH"):
                started = time.monotonic()
                status, headers, _ = http(self.mcp(path), method, timeout=5)
                elapsed = time.monotonic() - started
                self.assertEqual(status, 405, f"{method} {path}")
                self.assertEqual(headers["Allow"], "POST", f"{method} {path}")
                self.assertLess(elapsed, 2.0, f"{method} {path} must not hold a stream open")

    def test_options_is_answered_without_cors_headers(self):
        status, headers, body = http(self.mcp(), "OPTIONS")
        self.assertEqual(status, 204)
        self.assertIn("POST", headers["Allow"])
        self.assertEqual(body, b"")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_subscriptions_listen_does_not_open_a_stream(self):
        """The newer ``subscriptions/listen`` method answers with a never-ending SSE stream unless it is switched off."""
        params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                            "io.modelcontextprotocol/clientCapabilities": {}},
                  "notifications": {"toolsListChanged": True}}
        started = time.monotonic()
        status, headers, body = rpc(self.mcp(), "subscriptions/listen", params,
                                    headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "subscriptions/listen"})
        self.assertLess(time.monotonic() - started, 5.0, "the request must be answered, not held open")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], -32601)
        self.assertNotIn("text/event-stream", headers.get("Content-Type", ""))

    def test_a_body_that_never_arrives_does_not_hold_back_an_early_answer(self):
        """Early answers drain the request body first; a client that announces a body and never sends it still gets the status."""
        with socket.create_connection(("127.0.0.1", self.live.port), timeout=15) as conn:
            conn.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 5\r\nConnection: close\r\n\r\n")
            started = time.monotonic()
            first = conn.recv(64)
        self.assertTrue(first.startswith(b"HTTP/1.1 200"), first)
        self.assertLess(time.monotonic() - started, 10.0)

    # -- health --------------------------------------------------------------

    def test_health_reports_state_and_leaks_no_paths(self):
        import gemarc_mcp

        status, headers, raw = http(self.live.url("/healthz"))
        report = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["server"], "gemarc-mcp")
        self.assertEqual(report["version"], gemarc_mcp.__version__)
        self.assertEqual(report["protocolPath"], "/mcp")
        self.assertIs(report["authRequired"], False)
        self.assertFalse(report["writeEnabled"])
        self.assertEqual(report["seedProject"], "demo")
        self.assertEqual(report["sessionManager"], "lifespan")
        self.assertTrue(report["consoleAvailable"])
        self.assertTrue(report["staticSiteAvailable"])
        self.assertNotIn("workspace", report)
        self.assertNotIn("sessionManagerThreadAlive", report)
        self.assertNotIn("sessionManagerError", report)
        self.assertEqual(leaked_paths(report), [], "healthz must not expose filesystem locations")
        text = raw.decode("utf-8")
        for secret in (self.live.workspace, tempfile.gettempdir(), str(REPO_ROOT)):
            self.assertNotIn(secret, text)

    # -- console and static content ------------------------------------------

    def test_home_page_is_the_mcp_console(self):
        """The home page is a tuning panel for the MCP, not a rendering of one design."""
        status, headers, raw = http(self.live.url("/"))
        body = raw.decode("utf-8", "replace")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("GemArc MCP", body)
        self.assertIn("功能微调控制台", body)
        for placeholder in ("__MCP_PATH__", "__HEALTH_PATH__", "__SITE_PATH__", "__VERSION__", "__WRITE_TOOLS__"):
            self.assertNotIn(placeholder, body)
        # The write-tool list comes from the running server: empty for the stateless set.
        self.assertIn("writeTools: []", body)
        self.assertNotIn("//site/", body)
        # The token field is part of the shell; it only shows when /healthz says so.
        self.assertIn('id="authbar"', body)
        self.assertIn("hidden", body.split('id="authbar"', 1)[1].split(">", 1)[0])

        for asset in ("/console.css", "/console.js"):
            status, _, content = http(self.live.url(asset))
            self.assertEqual(status, 200, asset)
            self.assertTrue(content)
        script = http(self.live.url("/console.js"))[2].decode("utf-8")
        self.assertIn("sessionStorage", script)
        # The token may only ever live in sessionStorage (a comment may name localStorage).
        self.assertIsNone(re.search(r"localStorage\s*[.\[]", script))
        self.assertIn("Authorization", script)

    def test_scaffold_site_is_served_under_the_site_prefix(self):
        """Requests /site/ first, so it does not depend on test order."""
        status, _, raw = http(self.live.url("/site/"))
        index = raw.decode("utf-8", "replace")
        self.assertEqual(status, 200)
        # The scaffold shell resolves three.js through an import map; the console has none.
        self.assertIn('"three"', index)
        self.assertIn("importmap", index)

        report = health(self.live)
        self.assertEqual(report["staticSiteSource"], "scaffold-static")
        self.assertEqual(report["staticSitePath"], "/site/")

        status, _, adapter = http(self.live.url("/site/runtime/space/space-viewer-three.js"))
        self.assertEqual(status, 200)
        self.assertIn("createThreeSpaceRenderer", adapter.decode("utf-8", "replace"))

        status, headers, _ = http(self.live.url("/site"))
        self.assertEqual(status, 200)  # urllib follows the 307 to /site/

    def test_unknown_paths_are_404_json_and_do_not_serve_the_site_at_the_root(self):
        for path in ("/favicon.ico", "/runtime/space/space-viewer-three.js", "/data/architecture.json", "/register"):
            status, headers, raw = http(self.live.url(path))
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(raw), {"error": "not_found"}, path)

    def test_oauth_discovery_probes_get_a_clean_404(self):
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                     "/.well-known/oauth-authorization-server"):
            status, headers, raw = http(self.live.url(path))
            self.assertEqual(status, 404, path)
            self.assertIn("application/json", headers["Content-Type"])
            self.assertEqual(json.loads(raw)["error"], "not_found")

    # -- site kit ------------------------------------------------------------

    def test_kit_download_etag_and_conditional_requests(self):
        from gemarc_mcp import stateless

        for profile in ("static", "vite"):
            url = self.live.url(f"/kit/{profile}.zip")
            status, headers, raw = http(url)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/zip")
            self.assertEqual(headers["Content-Disposition"], f"attachment; filename=gemarc-kit-{profile}.zip")
            self.assertEqual(headers["Cache-Control"], "public, max-age=3600")
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(headers["ETag"], f'"{digest}"')
            self.assertEqual(digest, stateless.site_kit(profile)["archiveSha256"])
            self.assertEqual(raw, stateless.site_kit_zip(profile))
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(), [item["path"] for item in stateless.kit_listing(profile)])

            etag = headers["ETag"]
            for validator in (etag, "W/" + etag, f'"other", {etag}', "*"):
                status, headers, raw = http(url, headers={"If-None-Match": validator})
                self.assertEqual(status, 304, validator)
                self.assertEqual(raw, b"")
                self.assertEqual(headers["ETag"], etag)
            status, _, raw = http(url, headers={"If-None-Match": '"stale"'})
            self.assertEqual(status, 200)
            self.assertTrue(raw)

        status, headers, raw = http(self.live.url("/kit/static.zip"), "HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")

    def test_unknown_kit_profiles_are_404(self):
        for path in ("/kit/mvp.zip", "/kit/nope.zip", "/kit/static", "/kit/", "/kit/..%2Fstatic.zip"):
            status, _, raw = http(self.live.url(path))
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(raw)["error"], "not_found")

    def test_kit_plus_render_url_follows_the_public_url_setting(self):
        status, _, body = rpc(self.mcp(), "tools/call",
                              {"name": "gemarc_render", "arguments": {"model": probe_model()}})
        payload = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(payload["kit"]["path"], "/kit/static.zip")
        self.assertIsNone(payload["kit"]["url"], "no GEMARC_PUBLIC_URL / Vercel host in this process")


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class LazySiteTests(unittest.TestCase):
    """The scaffold site is built on the first /site/ request, not at import or cold start."""

    def test_site_is_not_built_until_first_requested(self):
        live = LiveServer().start()
        try:
            self.assertEqual(health(live)["staticSiteSource"], "not-built-yet")
            self.assertTrue(health(live)["staticSiteAvailable"])
            # Neither the MCP endpoint, the console, the kit nor a stray probe builds it.
            drive(live.url("/mcp"))
            http(live.url("/"))
            http(live.url("/favicon.ico"))
            http(live.url("/kit/static.zip"))
            self.assertEqual(health(live)["staticSiteSource"], "not-built-yet")

            results = []

            def fetch():
                results.append(http(live.url("/site/"))[0])

            threads = [threading.Thread(target=fetch) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(results, [200, 200, 200, 200])
            self.assertEqual(health(live)["staticSiteSource"], "scaffold-static")
        finally:
            live.stop()


    def test_generation_failure_falls_back_to_the_committed_preview_without_leaking_paths(self):
        from unittest import mock

        from gemarc_mcp.scaffold import builder

        secret = "/secret/server/path/never-shown"
        live = LiveServer().start()
        try:
            with mock.patch.object(builder, "build_site", side_effect=RuntimeError(secret)):
                with self.assertLogs("gemarc.api", level="ERROR"):
                    status, _, raw = http(live.url("/site/"))
            self.assertEqual(status, 200)
            self.assertIn(b"<html", raw.lower())
            report = health(live)
            self.assertEqual(report["staticSiteSource"], "committed-preview (generation failed: RuntimeError)")
            self.assertTrue(report["staticSiteAvailable"])
            self.assertNotIn(secret, json.dumps(report))
            self.assertEqual(leaked_paths(report), [])
        finally:
            live.stop()


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class SessionManagerStartupTests(unittest.TestCase):
    """The session manager runs on the serving loop: via lifespan, else lazily on first use."""

    def test_lazy_start_when_the_host_never_runs_lifespan(self):
        live = LiveServer(lifespan="off").start()
        try:
            before = health(live)
            self.assertEqual(before["sessionManager"], "not-running")
            self.assertEqual(before["status"], "ok", "nothing has failed; it simply has not been needed yet")

            results = []

            def first_call():
                results.append(rpc(live.url("/mcp"), "initialize", INITIALIZE["params"])[0])

            threads = [threading.Thread(target=first_call) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(results, [200] * 6, "concurrent first requests must start run() exactly once")
            self.assertEqual(health(live)["sessionManager"], "lazy")
            self.assertEqual(health(live)["status"], "ok")

            out = drive(live.url("/mcp"), {"validate": ("gemarc_validate", {"project": "demo"})})
            self.assertEqual(set(out["tools"]), STATELESS_TOOLS)
            self.assertEqual(out["validate"]["status"], "ok")
        finally:
            live.stop()

    def test_failed_startup_is_a_503_not_the_sdks_runtime_error(self):
        from unittest import mock

        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        @contextlib.asynccontextmanager
        async def broken_run(self):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        live = LiveServer(lifespan="off").start()
        try:
            with mock.patch.object(StreamableHTTPSessionManager, "run", broken_run):
                with self.assertLogs("gemarc.api", level="ERROR") as logged:
                    for _ in range(2):  # a persistent failure stays a clear 503 on every request
                        status, headers, body = rpc(live.url("/mcp"), "tools/list")
                        self.assertEqual(status, 503)
                        self.assertEqual(body, {"error": "session manager not running"})
                self.assertEqual(len(logged.records), 2)
                report = health(live)
                self.assertEqual(report["status"], "degraded")
                self.assertEqual(report["sessionManager"], "not-running")
            # Once run() works again the next request recovers with a fresh manager.
            self.assertEqual(rpc(live.url("/mcp"), "tools/list")[0], 200)
            report = health(live)
            self.assertEqual((report["status"], report["sessionManager"]), ("ok", "lazy"))
        finally:
            live.stop()

    def test_every_invocation_on_a_fresh_event_loop_still_works(self):
        """A host that runs each request on a new loop must not get a 503 after the first one."""
        for variant in ("loop-closed-each-time", "loop-left-open"):
            with built_app(token="") as app:
                statuses, loops = [], []
                for _ in range(4):
                    if variant == "loop-closed-each-time":
                        statuses.append(asyncio.run(asgi_post(app)))
                    else:
                        loop = asyncio.new_event_loop()
                        loops.append(loop)
                        statuses.append(loop.run_until_complete(asgi_post(app)))
                try:
                    self.assertEqual(statuses, [200] * 4, variant)
                    report = app.state.gemarc_health()
                    self.assertEqual((report["status"], report["sessionManager"]), ("ok", "lazy"), variant)
                finally:
                    for loop in loops:
                        loop.run_until_complete(asyncio.sleep(0))
                        for task in asyncio.all_tasks(loop):
                            task.cancel()
                        loop.run_until_complete(asyncio.sleep(0.05))
                        loop.close()

    def test_a_persistent_loop_keeps_one_manager(self):
        """Many requests on one loop (no lifespan) reuse the manager that the first one started."""
        with built_app(token="") as app:
            async def burst():
                first = app.state.gemarc_mcp_server.session_manager
                statuses = await asyncio.gather(*[asgi_post(app) for _ in range(5)])
                statuses += [await asgi_post(app) for _ in range(3)]
                return statuses, first is app.state.gemarc_mcp_server.session_manager

            statuses, same_manager = asyncio.run(burst())
            self.assertEqual(statuses, [200] * 8)
            self.assertTrue(same_manager, "no rebuild while the manager's loop is the serving loop")


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class FullToolsetTests(unittest.TestCase):
    """An operator may still choose the workspace-based tool set (GEMARC_TOOLSET=full)."""

    @classmethod
    def setUpClass(cls):
        cls.live = LiveServer(toolset="full").start()

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()

    def test_handshake_exposes_the_full_tool_surface(self):
        out = drive(self.live.url("/mcp"))
        self.assertEqual(out["server"], "GemArc MCP")
        self.assertEqual(len(out["tools"]), 16)
        self.assertEqual(len(set(out["tools"])), 16)
        for name in out["tools"]:
            self.assertRegex(name, FUNCTION_NAME)
        for name in ("gemarc_scaffold", "gemarc_contracts", "gemarc_brief", "gemarc_render", "gemarc_new_model"):
            self.assertIn(name, out["tools"])
        self.assertEqual(out["resources"], ["gemarc://registry", "gemarc://schema/model"])
        self.assertEqual(out["prompts"], ["gemarc-design"])

    def test_read_tools_work_against_the_seeded_project(self):
        out = drive(self.live.url("/mcp"), {
            "assets": ("gemarc_assets", {}),
            "plan": ("gemarc_plan", {"project": "demo"}),
            "validate": ("gemarc_validate", {"project": "demo"}),
        })
        for key in ("assets", "plan", "validate"):
            self.assertEqual(out[key]["status"], "ok", key)

    def test_write_tools_are_refused_without_explicit_opt_in(self):
        out = drive(self.live.url("/mcp"), {
            "init": ("gemarc_init", {"project": "http-test-should-not-exist"}),
            "build": ("gemarc_build", {"project": "demo"}),
            "validate": ("gemarc_validate", {"project": "http-test-should-not-exist"}),
        })
        for key in ("init", "build"):
            self.assertEqual(out[key]["status"], "invalid")
            self.assertEqual(out[key]["diagnostics"][0]["id"], "GEMARC_AUTH_002")
        # A refused write must not have created anything.
        self.assertEqual(out["validate"]["status"], "invalid")

    def test_paths_outside_the_workspace_are_refused(self):
        out = drive(self.live.url("/mcp"), {"inspect": ("gemarc_inspect", {"path": ".."})})
        self.assertEqual(out["inspect"]["status"], "invalid")
        self.assertEqual(out["inspect"]["diagnostics"][0]["id"], "GEMARC_AUTH_001")

    def test_health_and_console_list_the_write_tools_of_this_toolset(self):
        report = health(self.live)
        self.assertEqual(report["toolset"], "full")
        self.assertEqual(set(report["writeTools"]), WRITE_TOOLS)
        self.assertFalse(report["writeEnabled"])
        self.assertEqual(len(report["tools"]), 16)
        self.assertEqual(leaked_paths(report), [])
        body = http(self.live.url("/"))[2].decode("utf-8", "replace")
        self.assertIn('"gemarc_scaffold"', body)


if __name__ == "__main__":
    unittest.main()
