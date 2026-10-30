"""
Optional bearer-token authentication of the remote MCP endpoint.

``GEMARC_MCP_TOKEN`` unset or empty: open ("authless", what claude.ai custom
connectors need). Set (comma-separated list, for rotation): ``POST /mcp``
requires ``Authorization: Bearer <token>``; public static content, health,
the kit and CORS preflight stay reachable without a token.

Each configuration is a separate ``api.index.build_app`` instance served by
uvicorn in this process; the environment is saved and restored around each one.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_http_transport import (  # noqa: E402
    ENV_KEYS, INITIALIZE, STATELESS_TOOLS, LiveServer, drive, health, http, rpc, sdk_available,
)

TOKEN = "tok-alpha-0123456789abcdef"
SECRET_MARKERS = (TOKEN,)


@contextlib.contextmanager
def clean_environment():
    """Importing api.index builds the default app and normalises os.environ: undo all of it afterwards."""
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def mcp_status(live, headers=None, path="/mcp"):
    return rpc(live.url(path), "initialize", INITIALIZE["params"], headers=headers)


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class BearerTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = LiveServer(token=TOKEN).start()

    @classmethod
    def tearDownClass(cls):
        cls.live.stop()

    def bearer(self, value=TOKEN, scheme="Bearer"):
        return {"Authorization": f"{scheme} {value}"}

    # -- the header is the only accepted credential ---------------------------

    def test_missing_token_is_401_with_a_bearer_challenge(self):
        status, headers, raw = http(
            self.live.url("/mcp"), "POST",
            {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json.dumps(INITIALIZE).encode())
        self.assertEqual(status, 401)
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(raw), {"error": "unauthorized"})
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_wrong_tokens_are_401_and_never_echoed(self):
        for headers in (self.bearer("nope"), self.bearer(TOKEN[:-1]), self.bearer(TOKEN + "x"),
                        self.bearer(TOKEN.upper()), {"Authorization": "Bearer"}, {"Authorization": "Bearer "},
                        {"Authorization": TOKEN}, self.bearer(TOKEN, "Basic"), self.bearer(TOKEN, "Token"),
                        self.bearer("tökén")):
            status, response_headers, body = mcp_status(self.live, headers)
            self.assertEqual(status, 401, headers)
            self.assertEqual(body, {"error": "unauthorized"})
            self.assertNotIn(TOKEN, json.dumps(dict(response_headers.items())))

    def test_the_query_string_is_never_a_credential(self):
        for query in (f"?token={TOKEN}", f"?access_token={TOKEN}", f"?key={TOKEN}", f"?Authorization=Bearer%20{TOKEN}"):
            status, _, body = mcp_status(self.live, path="/mcp" + query)
            self.assertEqual(status, 401, query)
            self.assertEqual(body, {"error": "unauthorized"})
        # A wrong header is not rescued by a right query string either.
        status, _, _ = mcp_status(self.live, self.bearer("nope"), path=f"/mcp?token={TOKEN}")
        self.assertEqual(status, 401)

    def test_the_right_token_works_on_every_mcp_path(self):
        for path in ("/mcp", "/mcp/", "/api/index"):
            status, _, body = mcp_status(self.live, self.bearer(), path)
            self.assertEqual(status, 200, path)
            self.assertEqual(body["result"]["serverInfo"]["name"], "GemArc MCP")

    def test_scheme_is_case_insensitive_and_surrounding_space_is_ignored(self):
        for headers in (self.bearer(scheme="bearer"), self.bearer(scheme="BEARER"),
                        {"Authorization": f"Bearer   {TOKEN}  "}):
            status, _, _ = mcp_status(self.live, headers)
            self.assertEqual(status, 200, headers)

    def test_the_official_client_needs_and_accepts_the_token(self):
        out = drive(self.live.url("/mcp"), {"validate": ("gemarc_validate", {"project": "demo"})},
                    headers=self.bearer())
        self.assertEqual(set(out["tools"]), STATELESS_TOOLS)
        self.assertEqual(out["validate"]["status"], "ok")
        with self.assertRaises(BaseException):
            drive(self.live.url("/mcp"))  # no token: the handshake cannot complete

    # -- what the token does not cover -----------------------------------------

    def test_method_gate_runs_before_auth(self):
        for method in ("GET", "HEAD", "DELETE"):
            for headers in (None, self.bearer()):
                status, response_headers, _ = http(self.live.url("/mcp"), method, headers)
                self.assertEqual(status, 405, method)
                self.assertEqual(response_headers["Allow"], "POST")

    def test_options_is_not_401(self):
        status, headers, _ = http(self.live.url("/mcp"), "OPTIONS")
        self.assertEqual(status, 204)
        self.assertIn("POST", headers["Allow"])

    def test_public_content_stays_public(self):
        for path, marker in (("/healthz", b'"status"'), ("/", b"GemArc MCP"), ("/console.js", b"sessionStorage"),
                             ("/console.css", b"--bg"), ("/site/", b"importmap"), ("/kit/static.zip", b"PK"),
                             ("/kit/vite.zip", b"PK")):
            status, _, body = http(self.live.url(path))
            self.assertEqual(status, 200, path)
            self.assertIn(marker, body, path)

    def test_health_says_auth_is_required_and_carries_no_secret(self):
        status, _, raw = http(self.live.url("/healthz"))
        report = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertIs(report["authRequired"], True)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, raw.decode("utf-8"))
        self.assertNotIn(TOKEN, http(self.live.url("/"))[2].decode("utf-8"))

    def test_oauth_discovery_is_a_clean_404_not_a_401_or_the_console(self):
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                     "/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp"):
            status, headers, raw = http(self.live.url(path))
            self.assertEqual(status, 404, path)
            self.assertIn("application/json", headers["Content-Type"])
            self.assertEqual(json.loads(raw)["error"], "not_found")
            self.assertIsNone(headers.get("WWW-Authenticate"))


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class TokenRotationTests(unittest.TestCase):
    def test_a_comma_separated_list_accepts_old_and_new_tokens(self):
        live = LiveServer(token=f"  old-token-aaaa ,, new-token-bbbb , ").start()
        try:
            for token in ("old-token-aaaa", "new-token-bbbb"):
                self.assertEqual(mcp_status(live, {"Authorization": f"Bearer {token}"})[0], 200, token)
            for token in ("old-token", "new-token-bbbb,old-token-aaaa", "", " ", ","):
                self.assertEqual(mcp_status(live, {"Authorization": f"Bearer {token}"})[0], 401, repr(token))
            self.assertIs(health(live)["authRequired"], True)
        finally:
            live.stop()

    def test_parse_tokens_strips_whitespace_and_drops_empty_entries(self):
        with clean_environment():
            from api.index import parse_tokens, token_matches

        self.assertEqual(parse_tokens(None), ())
        self.assertEqual(parse_tokens(""), ())
        self.assertEqual(parse_tokens(" , ,"), ())
        self.assertEqual(parse_tokens(" a , b ,, c"), (b"a", b"b", b"c"))
        tokens = parse_tokens("a,b")
        self.assertTrue(token_matches(b"b", tokens))
        self.assertFalse(token_matches(b"c", tokens))
        self.assertFalse(token_matches(None, tokens))
        self.assertFalse(token_matches(b"", tokens))
        self.assertFalse(token_matches("é".encode("latin-1"), tokens))


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class AuthlessTests(unittest.TestCase):
    """No token configured: the endpoint is open, as claude.ai custom connectors require."""

    def test_unset_and_empty_token_leave_the_endpoint_open(self):
        for configured in ("", "   ", " , "):
            live = LiveServer(token=configured).start()
            try:
                self.assertEqual(mcp_status(live)[0], 200, repr(configured))
                self.assertIs(health(live)["authRequired"], False)
                # A header that nobody asked for is simply ignored.
                self.assertEqual(mcp_status(live, {"Authorization": "Bearer whatever"})[0], 200)
                out = drive(live.url("/mcp"))
                self.assertEqual(set(out["tools"]), STATELESS_TOOLS)
            finally:
                live.stop()

    def test_the_token_is_read_from_the_environment_when_not_given(self):
        workspace = tempfile.mkdtemp(prefix="gemarc-auth-env-")
        try:
            with clean_environment():
                from api.index import build_app

                def required(**kwargs):
                    return build_app(workspace_root=workspace, **kwargs).state.gemarc_health()["authRequired"]

                os.environ["GEMARC_MCP_TOKEN"] = "env-token-1234"
                self.assertTrue(required())
                os.environ["GEMARC_MCP_TOKEN"] = ""
                self.assertFalse(required())
                # An explicit empty token overrides an environment token.
                os.environ["GEMARC_MCP_TOKEN"] = "env-token-1234"
                self.assertFalse(required(token=""))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


SCRIPT = HERE.parent / "scripts" / "verify_remote.py"


def run_probe(*args, env=None):
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=180, encoding="utf-8", errors="replace",
                          env={**{k: v for k, v in os.environ.items() if k != "GEMARC_MCP_TOKEN"}, **(env or {})})
    return done.returncode, done.stdout + done.stderr


class _FakeHost:
    """A tiny HTTP server that answers every GET with a canned response (a stand-in for another host)."""

    def __init__(self, status, content_type, body):
        import http.server
        import threading

        canned = (status, content_type, body)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                code, ctype, payload = canned
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_HEAD = do_DELETE = do_POST = do_GET

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class VerifyRemoteScriptTests(unittest.TestCase):
    """scripts/verify_remote.py is the post-deploy probe; keep it honest against the real app."""

    def test_passes_against_a_protected_server_with_the_token(self):
        live = LiveServer(token=TOKEN).start()
        try:
            code, out = run_probe(live.url(), "--token", TOKEN)
            self.assertEqual(code, 0, out)
            self.assertIn("POST /mcp without a token -> 401", out)
            self.assertNotIn(TOKEN, out, "the token must never be printed")
            code, out = run_probe(live.url(), env={"GEMARC_MCP_TOKEN": TOKEN})
            self.assertEqual(code, 0, out)
        finally:
            live.stop()

    def test_fails_when_a_protected_server_gets_no_or_a_wrong_token(self):
        live = LiveServer(token=TOKEN).start()
        try:
            code, out = run_probe(live.url())
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL  a bearer token is available", out)
            code, out = run_probe(live.url(), "--token", "wrong-token-value")
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL  initialize with the token", out)
            self.assertNotIn("wrong-token-value", out)
        finally:
            live.stop()

    def test_passes_against_an_authless_server(self):
        live = LiveServer().start()
        try:
            code, out = run_probe(live.url())
            self.assertEqual(code, 0, out)
            self.assertIn("POST /mcp without a token -> 200 (authless)", out)
            self.assertIn("session manager is running after traffic - sessionManager=lifespan", out)
        finally:
            live.stop()

    def test_passes_when_the_host_starts_the_session_manager_lazily(self):
        live = LiveServer(lifespan="off").start()
        try:
            code, out = run_probe(live.url())
            self.assertEqual(code, 0, out)
            self.assertIn("sessionManager=lazy", out)
        finally:
            live.stop()

    def test_sends_extra_headers_to_the_server(self):
        live = LiveServer().start()
        try:
            code, out = run_probe(live.url(), "--header", "x-vercel-protection-bypass: not-a-real-secret",
                                  "--header", "X-Other: 1")
            self.assertEqual(code, 0, out)
            self.assertIn("extra headers: 2", out)
            self.assertNotIn("not-a-real-secret", out)
        finally:
            live.stop()

    def test_detects_deployment_protection(self):
        page = (b"<!doctype html><html><head><title>Authentication Required</title></head><body>"
                b"<a href='https://vercel.com/sso-api?url=x'>Log in to Vercel</a></body></html>")
        for status in (401, 200):
            host = _FakeHost(status, "text/html; charset=utf-8", page)
            try:
                code, out = run_probe(host.url)
                self.assertEqual(code, 1, out)
                self.assertIn("Deployment Protection is on for this URL", out)
                self.assertIn("vercel curl", out)
            finally:
                host.close()

    def test_flags_a_healthz_that_leaks_a_server_path(self):
        leaky = json.dumps({"status": "ok", "workspace": "/tmp/gemarc-workspace", "authRequired": False}).encode()
        host = _FakeHost(200, "application/json", leaky)
        try:
            code, out = run_probe(host.url)
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL  /healthz leaks no filesystem path", out)
            self.assertIn("/tmp/gemarc-workspace", out)
        finally:
            host.close()

    def test_unreachable_host_is_a_failure_not_a_crash(self):
        code, out = run_probe("http://127.0.0.1:1")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  /healthz answers ok", out)

    def test_helpers(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("verify_remote", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.path_like_values({"protocolPath": "/mcp", "staticSitePath": "/site/", "tools": ["a_b"]}), [])
        for leaked in ("/tmp/gemarc-workspace", r"D:\GemArc\x", "C:/Users/me", "/var/task/src", "/home/u"):
            self.assertEqual(module.path_like_values({"x": [leaked]}), [leaked], leaked)
        self.assertTrue(module.looks_like_vercel_protection(401, "text/html", b"<html>anything</html>"))
        self.assertTrue(module.looks_like_vercel_protection(200, "text/html", b"<html>x _vercel_sso_nonce y</html>"))
        self.assertFalse(module.looks_like_vercel_protection(401, "application/json", b'{"error":"unauthorized"}'))
        self.assertFalse(module.looks_like_vercel_protection(200, "text/html", b"<html>GemArc MCP console</html>"))
        self.assertEqual(module.parse_jsonrpc(b'{"id":1,"result":{}}'), {"id": 1, "result": {}})
        self.assertEqual(module.parse_jsonrpc(b'event: message\ndata: {"id":2}\n\n'), {"id": 2})
        self.assertIsNone(module.parse_jsonrpc(b"<html>"))
        self.assertEqual(module.probe_model(), __import__("test_http_transport").probe_model(),
                         "the script's embedded model and the test fixture must stay identical")


NODE_HARNESS = r"""
// Runs console.js against a minimal fake DOM and a fake fetch to exercise the token flow.
const fs = require('fs');
const source = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];

const nodes = {};
function makeNode(id) {
  const n = {
    id, hidden: id === '#authbar', value: '', className: '', disabled: false, children: [],
    dataset: {}, listeners: {}, style: {}, childElementCount: 0, firstChild: null,
    append(...c) { this.children.push(...c); this.childElementCount = this.children.length; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    addEventListener(t, f) { this.listeners[t] = f; }, focus() { this.focused = true; },
    classList: { add() {} },
  };
  // Like the DOM: assigning '' to textContent removes the children.
  let text = '';
  Object.defineProperty(n, 'textContent', {
    get() { return text; },
    set(value) { text = value; if (value === '') { n.children.length = 0; n.childElementCount = 0; } },
  });
  return n;
}
const document = {
  querySelector(sel) { return nodes[sel] || (nodes[sel] = makeNode(sel)); },
  createElement() { return makeNode('x'); }, createTextNode() { return makeNode('t'); },
  createDocumentFragment() { return makeNode('f'); },
};
let storage = {}; let storageBroken = scenario === 'broken-storage';
const sessionStorage = {
  getItem(k) { if (storageBroken) throw new Error('blocked'); return k in storage ? storage[k] : null; },
  setItem(k, v) { if (storageBroken) throw new Error('blocked'); storage[k] = String(v); },
  removeItem(k) { if (storageBroken) throw new Error('blocked'); delete storage[k]; },
};
if (scenario === 'stored-token') storage['gemarc.mcp.token'] = 'stored-secret';
if (scenario === 'stored-wrong') storage['gemarc.mcp.token'] = 'wrong';
if (scenario === 'stale-race') storage['gemarc.mcp.token'] = 'stale-wrong';
const calls = [];
const REQUIRE = process.argv[4] === 'auth';
const GOOD = 'good-token';
async function fetch(url, options = {}) {
  const headers = options.headers || {};
  calls.push({ url, auth: headers.Authorization || null });
  if (url === '/healthz') {
    return { ok: true, status: 200, json: async () => ({ status: 'ok', authRequired: REQUIRE, tools: ['a'], writeTools: [], toolset: 'stateless' }) };
  }
  if (scenario === 'down-503') return { ok: false, status: 503, text: async () => '{"error":"session manager not running"}' };
  // The answers to the stale token are slow, so a newer token can be pasted before they arrive.
  if (scenario === 'stale-race' && headers.Authorization === 'Bearer stale-wrong') await new Promise((r) => setTimeout(r, 150));
  const good = !REQUIRE || headers.Authorization === 'Bearer ' + GOOD || headers.Authorization === 'Bearer stored-secret';
  if (!good) return { ok: false, status: 401, text: async () => '{"error":"unauthorized"}' };
  const method = JSON.parse(options.body).method;
  const result = method === 'tools/list' ? { tools: [{ name: 'gemarc_brief', inputSchema: { properties: {} } }] } : {};
  return { ok: true, status: 200, text: async () => JSON.stringify({ jsonrpc: '2.0', id: 1, result }) };
}
const window = { GEMARC: { mcpPath: '/mcp', healthPath: '/healthz', writeTools: [] } };
const sandbox = { window, document, fetch, sessionStorage, performance: { now: () => 0 }, navigator: {}, console };
new Function(...Object.keys(sandbox), source)(...Object.values(sandbox));
const settle = () => new Promise((r) => setTimeout(r, 30));

(async () => {
  await settle();
  const out = { rpcCalls: calls.filter((c) => c.url === '/mcp').length, auth: calls.filter((c) => c.url === '/mcp').map((c) => c.auth),
    barHidden: document.querySelector('#authbar').hidden, msg: document.querySelector('#token-msg').textContent, connText: document.querySelector('#conn-text').textContent,
    storage: Object.assign({}, storage), urls: calls.map((c) => c.url),
    statChips: document.querySelector('#statbar').children.length };
  if (scenario === 'paste-token') {
    const before = calls.length;
    document.querySelector('#token-input').value = GOOD;
    document.querySelector('#token-save').listeners.click();
    await settle();
    out.after = { auth: calls.slice(before).filter((c) => c.url === '/mcp').map((c) => c.auth), inputCleared: document.querySelector('#token-input').value === '',
      storage: Object.assign({}, storage), connText: document.querySelector('#conn-text').textContent };
  }
  if (scenario === 'bad-char') {
    const before = calls.length;
    document.querySelector('#token-input').value = GOOD + '\u200b';
    document.querySelector('#token-save').listeners.click();
    await settle();
    out.bad = { newRpcCalls: calls.slice(before).filter((c) => c.url === '/mcp').length, storage: Object.assign({}, storage),
      msg: document.querySelector('#token-msg').textContent };
  }
  if (scenario === 'stale-race') {
    await new Promise((r) => setTimeout(r, 20));
    document.querySelector('#token-input').value = GOOD;
    document.querySelector('#token-save').listeners.click();
    await new Promise((r) => setTimeout(r, 300)); // the stale 401s (150 ms) have arrived by now
    out.race = { storage: Object.assign({}, storage), connText: document.querySelector('#conn-text').textContent,
      msg: document.querySelector('#token-msg').textContent };
  }
  console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class ConsoleTokenFlowTests(unittest.TestCase):
    """console.js against a fake DOM: no MCP call before a token, 401 handling, storage rules."""

    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["node", "--version"], capture_output=True, check=True, timeout=20)
        except Exception:
            raise unittest.SkipTest("node is not available")
        cls.dir = tempfile.TemporaryDirectory(prefix="gemarc-console-")
        cls.harness = Path(cls.dir.name) / "harness.cjs"
        cls.harness.write_text(NODE_HARNESS, encoding="utf-8")
        cls.script = HERE.parent / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console" / "console.js"

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def run_scenario(self, scenario, mode):
        done = subprocess.run(["node", str(self.harness), str(self.script), scenario, mode],
                              capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_open_server_connects_without_a_token_or_the_token_bar(self):
        out = self.run_scenario("none", "open")
        self.assertEqual(out["rpcCalls"], 3)
        self.assertEqual(out["auth"], [None, None, None])
        self.assertTrue(out["barHidden"], "the token bar stays hidden when no token is required")

    def test_protected_server_makes_no_mcp_call_until_a_token_is_pasted(self):
        out = self.run_scenario("none", "auth")
        self.assertEqual(out["rpcCalls"], 0)
        self.assertFalse(out["barHidden"], "the token bar is shown")
        self.assertEqual(out["storage"], {})

    def test_pasting_a_token_sends_it_as_a_header_and_keeps_it_only_in_session_storage(self):
        out = self.run_scenario("paste-token", "auth")
        self.assertEqual(out["after"]["auth"], ["Bearer good-token"] * 3)
        self.assertTrue(out["after"]["inputCleared"])
        self.assertEqual(out["after"]["storage"], {"gemarc.mcp.token": "good-token"})
        self.assertFalse(any("good-token" in url for url in out["urls"]), "never in a URL")

    def test_a_stored_token_is_reused_and_a_wrong_one_is_dropped_on_401(self):
        reused = self.run_scenario("stored-token", "auth")
        self.assertEqual(reused["auth"], ["Bearer stored-secret"] * 3)
        wrong = self.run_scenario("stored-wrong", "auth")
        self.assertEqual(wrong["storage"], {}, "a rejected token is forgotten")
        self.assertIn("401", wrong["msg"])
        self.assertFalse(wrong["barHidden"])

    def test_a_bad_character_in_a_pasted_token_is_refused_before_it_is_stored(self):
        out = self.run_scenario("bad-char", "auth")
        self.assertEqual(out["bad"]["newRpcCalls"], 0, "fetch would throw on a header value with a zero-width space")
        self.assertEqual(out["bad"]["storage"], {})
        self.assertIn("不可见字符", out["bad"]["msg"])

    def test_a_late_401_for_an_old_token_does_not_discard_the_new_token(self):
        out = self.run_scenario("stale-race", "auth")
        self.assertEqual(out["race"]["storage"], {"gemarc.mcp.token": "good-token"})
        self.assertTrue(out["race"]["connText"].startswith("已连接"), out["race"]["connText"])

    def test_the_servers_own_error_answer_is_shown_and_the_health_chips_are_kept(self):
        out = self.run_scenario("down-503", "open")
        self.assertIn("session manager not running", out["connText"])
        self.assertIn("503", out["connText"])
        self.assertGreater(out["statChips"], 1, "the chips rendered from /healthz must survive a failed /mcp call")

    def test_blocked_session_storage_does_not_break_the_console(self):
        out = self.run_scenario("broken-storage", "open")
        self.assertEqual(out["rpcCalls"], 3)
        paste = self.run_scenario("broken-storage", "auth")
        self.assertEqual(paste["rpcCalls"], 0)


if __name__ == "__main__":
    unittest.main()
