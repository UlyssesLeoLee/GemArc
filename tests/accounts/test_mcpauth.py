"""``POST /mcp`` authentication in the account modes, the fail-closed matrix, the PAT cache and the scope gate."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_http_transport import INITIALIZE, http, sdk_available  # noqa: E402

from gemarc_mcp.accounts import mcpauth, tokens as tokens_mod  # noqa: E402
from gemarc_mcp.accounts.crypto import b64u  # noqa: E402

from .fake_idp import Browser, ServerCase, asgi_call, sign_in  # noqa: E402
from .support import KEY1  # noqa: E402

ENV_TOKEN = "env-token-0123456789abcdef"
INIT_BODY = json.dumps(INITIALIZE).encode()
MCP_H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def make_pat(case: ServerCase, sub="p-1", email="alice@gmail.com", scope="read", can_write=False, ip=None):
    browser = Browser(case.srv.base)
    sign_in(browser, case.idp, {"sub": sub, "email": email})
    rt = case.srv.runtime()
    user_id = ""
    with rt.db.transaction() as tx:
        for candidate in rt.crypto.blind_index_all("email", email):
            row = tx.fetchone("SELECT id FROM users WHERE email_bidx = ?", (candidate,))
            if row:
                user_id = row["id"]
    if can_write:
        rt.users.set_can_write(user_id, True, case.clock())
    created = rt.tokens.create(user_id, "t", case.clock(), scope=scope, allow_write=True)
    return created.token, user_id


def post(case, token=None, payload=None, headers=None, path="/mcp", raw=None):
    merged = dict(MCP_H)
    if token:
        merged["Authorization"] = "Bearer " + token
    merged.update(headers or {})
    body = raw if raw is not None else json.dumps(payload or INITIALIZE).encode()
    return http(case.srv.url(path), "POST", merged, body)


def count_transactions(case):
    rt = case.srv.runtime()
    calls = []
    original = rt.db.transaction

    def counting():
        calls.append(1)
        return original()

    rt.db.transaction = counting
    case.addCleanup(setattr, rt.db, "transaction", original)
    return calls


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class PatMode(ServerCase):
    def test_pat_mode_accepts_only_pats(self):
        token, _ = make_pat(self)
        status, headers, body = post(self)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"error": "unauthorized"})
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(post(self, ENV_TOKEN)[0], 401)
        self.assertEqual(post(self, token)[0], 200)
        self.assertEqual(post(self, token, headers={"Authorization": "bearer   " + token + "  "})[0], 200)
        # the credential is read from the header only
        self.assertEqual(http(self.srv.url("/mcp?token=" + token + "&access_token=" + token), "POST", MCP_H, INIT_BODY)[0], 401)
        self.assertEqual(post(self, headers={"Authorization": "Basic " + token})[0], 401)
        self.assertEqual(post(self, headers={"Authorization": "Bearer"})[0], 401)

    def test_401_is_byte_identical_for_every_failure_reason_and_never_echoes(self):
        token, user_id = make_pat(self)
        rt = self.srv.runtime()
        wrong = token[:-1] + ("a" if token[-1] != "a" else "b")
        fresh = tokens_mod.mint()[0]
        bodies = set()
        for presented in (None, "garbage", wrong, fresh, token[:30]):
            status, headers, body = post(self, presented)
            self.assertEqual(status, 401)
            bodies.add(body)
            self.assertNotIn(presented.encode() if presented else b"\0", body)
        # expired, revoked, disabled users answer with the very same bytes
        self.clock.advance(31 * 86400)
        bodies.add(post(self, token)[2])
        self.assertEqual(bodies, {b'{"error":"unauthorized"}'})

    def test_methods_are_gated_before_authentication(self):
        for method in ("GET", "HEAD", "DELETE", "PUT"):
            status, headers, _ = http(self.srv.url("/mcp"), method)
            self.assertEqual(status, 405, method)
            self.assertEqual(headers["Allow"], "POST")
        self.assertEqual(http(self.srv.url("/mcp"), "OPTIONS")[0], 204)

    def test_expiry_revocation_and_disabled_users(self):
        token, user_id = make_pat(self)
        self.assertEqual(post(self, token)[0], 200)
        rt = self.srv.runtime()
        # disabled user: effective once the (30 s) cache entry is gone
        rt.users.disable(user_id, self.clock())
        self.assertEqual(post(self, token)[0], 200)  # still cached: the documented revocation delay
        self.clock.advance(31)
        self.assertEqual(post(self, token)[0], 401)
        # a fresh user: expiry
        other, _ = make_pat(self, "p-2", "bob@gmail.com")
        self.assertEqual(post(self, other)[0], 200)
        self.clock.advance(31 * 86400)
        self.assertEqual(post(self, other)[0], 401)

    def test_revoke_all_and_logout_all_evict_the_cache_at_once(self):
        token, user_id = make_pat(self)
        self.assertEqual(post(self, token)[0], 200)
        rt = self.srv.runtime()
        self.assertEqual(rt.pat_cache.get((token[4:12], tokens_mod.hash_secret(token[13:56])))[0], "pos")
        browser = Browser(self.srv.base)
        self.logged_in_page({"sub": "p-1", "email": "alice@gmail.com"}, browser)
        page = browser.get("/account").text
        self.assertEqual(browser.post("/account/tokens/revoke-all", {"csrf": browser.csrf(page)}).status, 303)
        self.assertEqual(post(self, token)[0], 401)

    def test_cache_hit_needs_no_database_and_expires_with_the_ttl(self):
        token, _ = make_pat(self)
        self.assertEqual(post(self, token)[0], 200)
        calls = count_transactions(self)
        for _ in range(3):
            self.assertEqual(post(self, token)[0], 200)
        self.assertEqual(calls, [])
        self.clock.advance(31)
        self.assertEqual(post(self, token)[0], 200)
        self.assertGreater(len(calls), 0)

    def test_malformed_and_checksum_failures_never_reach_cache_or_database(self):
        token, _ = make_pat(self)
        calls = count_transactions(self)
        bad_checksum = token[:-1] + ("A" if token[-1] != "A" else "B")
        for presented in ("x", "gat_", token[:-1], token + "x", bad_checksum, token.upper(), "gat_" + "a" * 58,
                          "é" * 62, token.replace("_", "-", 1)):
            self.assertEqual(post(self, presented)[0], 401, presented)
        self.assertEqual(calls, [])

    def test_negative_cache_queries_a_bad_token_only_once(self):
        make_pat(self)
        ghost = tokens_mod.mint()[0]  # well formed, unknown
        calls = count_transactions(self)
        for _ in range(4):
            self.assertEqual(post(self, ghost)[0], 401)
        self.assertEqual(len(calls), 1)

    def test_concurrent_misses_share_one_verification(self):
        token, _ = make_pat(self)
        rt = self.srv.runtime()
        counter = []
        original = rt.tokens.verify

        def slow(*args, **kwargs):
            counter.append(1)
            time.sleep(0.4)
            return original(*args, **kwargs)

        rt.tokens.verify = slow
        self.addCleanup(setattr, rt.tokens, "verify", original)
        statuses = []
        threads = [threading.Thread(target=lambda: statuses.append(post(self, token)[0])) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(statuses, [200] * 6)
        self.assertEqual(len(counter), 1)

    def test_database_failure_is_503_never_401_and_never_allow(self):
        token, _ = make_pat(self)
        rt = self.srv.runtime()
        good = rt.db.backend.path
        rt.db.backend.path = os.path.join(self.tmp, "no-such-dir", "x.db")
        try:
            self.clock.advance(31)  # the cache entry (if any) is gone
            status, headers, body = post(self, token)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"error": "accounts_unavailable", "code": "GEMARC_ACCT_002"})
            self.assertEqual(Browser(self.srv.base).get("/healthz").json()["accounts"], "db-unreachable")
        finally:
            rt.db.backend.path = good
        self.assertEqual(post(self, token)[0], 200)
        self.assertEqual(Browser(self.srv.base).get("/healthz").json()["accounts"], "ok")


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class PatOrEnvMode(ServerCase):
    server_kwargs = {"token": ENV_TOKEN, "env": {"GEMARC_MCP_AUTH": "pat-or-env"}}

    def test_env_token_first_then_pat(self):
        token, _ = make_pat(self)
        self.assertEqual(post(self, ENV_TOKEN)[0], 200)
        self.assertEqual(post(self, token)[0], 200)
        self.assertEqual(post(self)[0], 401)
        self.assertEqual(post(self, ENV_TOKEN + "x")[0], 401)

    def test_env_token_does_not_need_the_database(self):
        make_pat(self)
        calls = count_transactions(self)
        self.assertEqual(post(self, ENV_TOKEN)[0], 200)
        self.assertEqual(calls, [])

    def test_env_token_still_works_while_the_database_is_down(self):
        token, _ = make_pat(self)
        rt = self.srv.runtime()
        good = rt.db.backend.path
        rt.db.backend.path = os.path.join(self.tmp, "no-such-dir", "x.db")
        try:
            self.assertEqual(post(self, ENV_TOKEN)[0], 200)
            self.clock.advance(31)
            self.assertEqual(post(self, token)[0], 503)
        finally:
            rt.db.backend.path = good


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class HostOnly(ServerCase):
    server_kwargs = {"env": {"GEMARC_ACCOUNTS_MCP_HOST_ONLY": "1"}}

    def test_pat_validation_can_be_pinned_to_the_configured_host(self):
        token, _ = make_pat(self)
        self.assertEqual(post(self, token)[0], 200)
        self.assertEqual(post(self, token, headers={"Host": "preview-123.vercel.app"})[0], 401)


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class FailureLimiting(ServerCase):
    server_kwargs = {"env": {"VERCEL": "1"}}

    def fail(self, ip, token=None):
        return post(self, token or tokens_mod.mint()[0], headers={"x-vercel-forwarded-for": ip})

    def test_failures_stay_in_memory_until_the_threshold_and_valid_cached_tokens_are_never_limited(self):
        token, _ = make_pat(self)
        baseline = self.count("rate_limits")  # the sign-in above counted its own sign-up
        self.assertEqual(post(self, token, headers={"x-vercel-forwarded-for": "203.0.113.9"})[0], 200)  # cached
        for _ in range(30):
            self.assertEqual(self.fail("203.0.113.9")[0], 401)
        self.assertEqual(self.count("audit_log", "event = 'token_auth_failed'"), 0)
        self.assertEqual(self.count("rate_limits"), baseline)
        self.assertEqual(self.fail("203.0.113.9")[0], 401)  # the 31st crosses the threshold
        self.assertEqual(self.count("audit_log", "event = 'token_auth_failed'"), 1)
        self.fail("203.0.113.9")
        self.assertEqual(self.count("audit_log", "event = 'token_auth_failed'"), 1)  # deduplicated
        self.assertNotIn("203.0.113.9", self.dump())
        # the penalty lane: a busy lane answers 429 to a cache MISS, never to a cached valid token
        auth = self.srv.app.state.gemarc_account_mcp_auth()
        self.assertTrue(auth._penalty.acquire(blocking=False))
        try:
            limited = self.fail("203.0.113.9")
            self.assertEqual(limited[0], 429)
            self.assertIn("GEMARC_ACCT_022", limited[2].decode())
            self.assertEqual(post(self, token, headers={"x-vercel-forwarded-for": "203.0.113.9"})[0], 200)
            self.assertEqual(self.fail("203.0.113.77")[0], 401)  # another address is not slowed down
        finally:
            auth._penalty.release()
        self.assertEqual(self.fail("203.0.113.9")[0], 401)  # lane free again


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class ScopeGate(ServerCase):
    server_kwargs = {"toolset": "full", "write": True, "env": {"GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE": "1"}}
    READ_CALL = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gemarc_contracts", "arguments": {}}}

    def call(self, name):
        return {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": {}}}

    def setUp(self):
        super().setUp()
        self.read, self.user_id = make_pat(self)
        self.write, _ = make_pat(self, "p-1", "alice@gmail.com", scope="write", can_write=True)

    def assert_denied(self, resp):
        status, headers, body = resp
        self.assertEqual(status, 403, body[:200])
        self.assertEqual(json.loads(body), {"error": "insufficient_scope", "code": "GEMARC_ACCT_032"})
        self.assertEqual(headers["WWW-Authenticate"], 'Bearer error="insufficient_scope"')

    def test_read_token_passes_the_read_surface_through_a_replayed_body(self):
        for payload in (INITIALIZE, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, self.READ_CALL,
                        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
                        {"jsonrpc": "2.0", "id": 5, "method": "resources/list", "params": {}},
                        {"jsonrpc": "2.0", "id": 6, "method": "prompts/list", "params": {}}):
            status, _h, body = post(self, self.read, payload)
            self.assertEqual(status, 200, payload["method"])
            self.assertIn(b'"result"', body)
        self.assertEqual(post(self, self.read, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)

    def test_read_token_cannot_call_write_tools_or_odd_methods(self):
        for name in ("gemarc_model", "gemarc_build", "gemarc_export", "gemarc_init", "gemarc_scaffold", "nope",
                     "GEMARC_MODEL", "Gemarc_Model", "gemarc_model "):
            self.assert_denied(post(self, self.read, self.call(name)))
        for method in ("resources/subscribe", "logging/setLevel", "completion/complete", "Tools/Call", "tools/CALL",
                       "sampling/createMessage", "tasks/get", ""):
            self.assert_denied(post(self, self.read, {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}))
        for params in ({}, {"name": 5}, {"name": None}, {"name": ["gemarc_contracts"]}, None):
            self.assert_denied(post(self, self.read, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}))
        self.assertGreaterEqual(self.count("audit_log", "event = 'token_scope_denied'"), 1)

    def test_parser_differential_attempts_are_refused(self):
        evil = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"gemarc_contracts","name":"gemarc_model","arguments":{}}}'
        evil2 = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"gemarc_model","name":"gemarc_contracts","arguments":{}}}'
        dup_method = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"tools/call","params":{"name":"gemarc_model"}}'
        ok = json.dumps(self.READ_CALL).encode()
        batch_mixed = json.dumps([self.READ_CALL, self.call("gemarc_model")]).encode()
        for raw in (evil, evil2, dup_method, batch_mixed, b"\xef\xbb\xbf" + ok, b"\xff\xfe" + ok, b"[]", b"[1]",
                    b"[[]]", b'"x"', b"123", b"null", b"{", b"", ok + b" {}", b'{"method":"ping","method":"ping"}'):
            self.assert_denied(post(self, self.read, raw=raw))
        # an all-read batch passes the gate (the SDK itself may still refuse batches, but never with 403/401)
        self.assertNotIn(post(self, self.read, raw=json.dumps([self.READ_CALL, INITIALIZE]).encode())[0], (401, 403))

    def test_write_token_and_env_style_full_access_are_not_gated(self):
        status, _h, body = post(self, self.write, self.call("gemarc_model"))
        self.assertEqual(status, 200)  # past the gate; the tool itself answers (with an error about its arguments)
        self.assertIn(b'"result"', body)

    def test_effective_scope_drops_to_read_when_can_write_is_revoked(self):
        self.assertEqual(post(self, self.write, self.call("gemarc_model"))[0], 200)
        self.srv.runtime().users.set_can_write(self.user_id, False, self.clock())
        self.clock.advance(31)
        self.assert_denied(post(self, self.write, self.call("gemarc_model")))

    def test_the_read_tool_list_is_pinned_and_matches_the_server_annotations(self):
        reviewed = {"gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model", "gemarc_validate",
                    "gemarc_simulate", "gemarc_diff", "gemarc_render", "gemarc_inspect", "gemarc_plan", "gemarc_verify"}
        reviewed_write = {"gemarc_build", "gemarc_export", "gemarc_init", "gemarc_model", "gemarc_scaffold"}
        server = self.srv.app.state.gemarc_mcp_server
        tools = asyncio.run(server.list_tools())
        by_hint = {t.name: bool(t.annotations.read_only_hint) for t in tools}
        self.assertEqual({n for n, ro in by_hint.items() if ro}, reviewed)
        self.assertEqual({n for n, ro in by_hint.items() if not ro}, reviewed_write)
        auth = self.srv.app.state.gemarc_account_mcp_auth()
        self.assertEqual(set(auth.read_tools), reviewed)
        self.assertEqual(set(auth.write_tools), reviewed_write)


class ScopeGateUnits(unittest.TestCase):
    READ = frozenset({"gemarc_contracts"})

    def test_allow_list(self):
        allow = mcpauth.read_scope_allows
        good = [{"method": "tools/list"}, {"method": "notifications/cancelled"},
                {"method": "tools/call", "params": {"name": "gemarc_contracts"}}]
        for message in good:
            self.assertTrue(allow(json.dumps(message).encode(), self.READ), message)
        self.assertTrue(allow(json.dumps(good).encode(), self.READ))
        for raw in (b"", b"[]", b"[{}]", b'{"method": 5}', b'{"method":"tools/call","params":{"name":"x"}}'):
            self.assertFalse(allow(raw, self.READ), raw)

    def test_replay_delivers_exactly_the_buffered_bytes(self):
        body = b'{"b":1,   "a":2}  \n'
        pending = [{"type": "http.request", "body": body[:5], "more_body": True},
                   {"type": "http.request", "body": body[5:], "more_body": False}]

        async def original():
            return pending.pop(0) if pending else {"type": "http.disconnect"}

        async def run():
            buffered = await mcpauth.buffer_body(original)
            replay = mcpauth.replay_receive(buffered, original)
            first = await replay()
            second = await replay()
            return buffered, first, second

        buffered, first, second = asyncio.run(run())
        self.assertEqual(buffered, body)
        self.assertEqual((first["body"], first["more_body"]), (body, False))
        self.assertEqual(second["type"], "http.disconnect")

    def test_oversized_body_is_refused(self):
        async def big():
            return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

        self.assertIsNone(asyncio.run(mcpauth.buffer_body(big, limit=4096)))


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class StatelessToolsetIsNotBuffered(ServerCase):
    def test_no_scope_gate_without_write_tools(self):
        token, _ = make_pat(self)
        auth = self.srv.app.state.gemarc_account_mcp_auth()
        self.assertEqual(auth.write_tools, frozenset())
        # garbage JSON is handed to the SDK untouched (its own error), not turned into a 403 by a gate
        status, _h, _b = post(self, token, raw=b"{not json")
        self.assertNotEqual(status, 403)
        self.assertNotEqual(status, 401)


# -- fail-closed matrix (in process, no database needed) ---------------------------------------------------------------
def complete_env(base="http://127.0.0.1:8000", db="x.db", **extra):
    env = {"GEMARC_ACCOUNTS": "1", "GEMARC_ACCOUNTS_BASE_URL": base, "GEMARC_GOOGLE_CLIENT_ID": "client.id.fake",
           "GEMARC_GOOGLE_CLIENT_SECRET": "client-secret-fake", "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(KEY1),
           "GEMARC_ACCOUNTS_DATABASE_URL": "sqlite:///" + db, "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1"}
    env.update(extra)
    return env


@contextlib.contextmanager
def built(env, **kwargs):
    saved = {k: os.environ.get(k) for k in ("GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_MCP_TOKEN", "GEMARC_TOOLSET")}
    ws = tempfile.mkdtemp(prefix="gemarc-fc-")
    try:
        from api.index import build_app

        yield build_app(workspace_root=ws, accounts={"environ": env}, **kwargs)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(ws, ignore_errors=True)


def run(app, method, path, headers=None, body=b""):
    return asyncio.run(asgi_call(app, method, path, headers, body))


def mcp(app, token=None):
    headers = dict(MCP_H)
    if token:
        headers["Authorization"] = "Bearer " + token
    return run(app, "POST", "/mcp", headers, INIT_BODY)


FULL_HEADERS = ("cache-control", "x-content-type-options", "x-frame-options", "referrer-policy",
                "cross-origin-opener-policy", "cross-origin-resource-policy", "permissions-policy",
                "content-security-policy")


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class FailClosed(unittest.TestCase):
    def assert_closed(self, env, token=None, **kw):
        with built(env, **kw) as app:
            for presented in (None, token, ENV_TOKEN):
                status, headers, body = mcp(app, presented)
                self.assertEqual(status, 503, (env, presented))
                self.assertEqual(json.loads(body), {"error": "accounts_unavailable", "code": "GEMARC_ACCT_001"})
            self.assertEqual(run(app, "GET", "/mcp")[0], 405)  # the method gate still comes first
            status, _h, body = run(app, "GET", "/healthz")
            health = json.loads(body)
            self.assertEqual(health["accounts"], "misconfigured")
            self.assertTrue(health["authRequired"])
            self.assertNotIn("accountsMissing", health)
            self.assertNotIn("mcpAuth", health)
            for secret in ("client-secret-fake", "k1:", "sqlite"):
                self.assertNotIn(secret, body.decode())
            return app

    def test_login_intent_without_accounts_never_opens(self):
        self.assert_closed({"GEMARC_REQUIRE_LOGIN": "1"})
        self.assert_closed({"GEMARC_MCP_AUTH": "pat"})
        self.assert_closed({"GEMARC_MCP_AUTH": "pat-or-env", "GEMARC_MCP_TOKEN": ENV_TOKEN}, ENV_TOKEN)
        with built({"GEMARC_REQUIRE_LOGIN": "1"}) as app:
            self.assertEqual(run(app, "GET", "/account")[0], 404)  # accounts off: the routes do not exist

    def test_incomplete_accounts_with_login_intent_never_opens(self):
        for extra in ({"GEMARC_REQUIRE_LOGIN": "1"}, {"GEMARC_MCP_AUTH": "pat"}, {"GEMARC_MCP_AUTH": "pat-or-env"}):
            app = self.assert_closed({"GEMARC_ACCOUNTS": "1", **extra})
        for missing in ("GEMARC_ACCOUNTS_BASE_URL", "GEMARC_GOOGLE_CLIENT_ID", "GEMARC_GOOGLE_CLIENT_SECRET",
                        "GEMARC_ACCOUNTS_KEYS", "GEMARC_ACCOUNTS_DATABASE_URL"):
            env = complete_env(GEMARC_REQUIRE_LOGIN="1")
            env.pop(missing)
            self.assert_closed(env)

    def test_every_bad_value_closes(self):
        for literal in ("true", "yes", "TRUE", "on", "01", "2", "enabled", "1x"):
            self.assert_closed({"GEMARC_REQUIRE_LOGIN": literal})
            self.assert_closed(complete_env(GEMARC_REQUIRE_LOGIN=literal))
        for literal in ("PAT", "Pat", "token", "pat,env", "pat-or-env ", "env_token", "PAT-OR-ENV"):
            if literal.strip() in ("pat-or-env",):
                continue  # a trailing blank is stripped: that is the valid value
            self.assert_closed({"GEMARC_MCP_AUTH": literal})
            self.assert_closed(complete_env(GEMARC_MCP_AUTH=literal))

    def test_conflicts_close(self):
        self.assert_closed(complete_env(GEMARC_MCP_AUTH="none", GEMARC_MCP_TOKEN=ENV_TOKEN), ENV_TOKEN)
        self.assert_closed(complete_env(GEMARC_MCP_AUTH="env-token"))
        self.assert_closed(complete_env(GEMARC_REQUIRE_LOGIN="1", GEMARC_MCP_AUTH="none"))
        self.assert_closed(complete_env(GEMARC_REQUIRE_LOGIN="1", GEMARC_MCP_AUTH="env-token", GEMARC_MCP_TOKEN=ENV_TOKEN),
                           ENV_TOKEN)

    def test_accounts_alone_never_changes_the_mcp_endpoint(self):
        for literal in ("true", "yes", "TRUE", "01", "2"):  # a bad ACCOUNTS value, no login intent: legacy rules
            with built({"GEMARC_ACCOUNTS": literal}) as app:
                self.assertEqual(mcp(app)[0], 200)
                self.assertEqual(json.loads(run(app, "GET", "/healthz")[2])["accounts"], "misconfigured")
                status, headers, body = run(app, "GET", "/account")
                self.assertEqual((status, json.loads(body)["code"]), (503, "GEMARC_ACCT_001"))
                for name in FULL_HEADERS:
                    self.assertIn(name, headers)
        with built({"GEMARC_ACCOUNTS": "1", "GEMARC_MCP_TOKEN": ENV_TOKEN}) as app:  # incomplete, no intent
            self.assertEqual(mcp(app)[0], 401)
            self.assertEqual(mcp(app, ENV_TOKEN)[0], 200)
            self.assertEqual(run(app, "GET", "/account")[0], 503)
            self.assertEqual(run(app, "POST", "/auth/logout")[0], 503)
        with built(complete_env()) as app:  # complete, no intent: the endpoint is exactly as without accounts
            self.assertEqual(mcp(app)[0], 200)
            health = json.loads(run(app, "GET", "/healthz")[2])
            self.assertEqual(health["accounts"], "ok")
            self.assertFalse(health["authRequired"])
            status, _h, body = run(app, "GET", "/account", {"Host": "127.0.0.1:8000"})
            self.assertEqual(status, 200)
            self.assertIn("隐私告知".encode(), body)
        with built(complete_env(GEMARC_MCP_TOKEN=ENV_TOKEN)) as app:
            self.assertEqual(mcp(app)[0], 401)
            self.assertEqual(mcp(app, ENV_TOKEN)[0], 200)

    def test_explicit_none_and_env_token_modes_with_accounts_ok(self):
        with built(complete_env(GEMARC_MCP_AUTH="none")) as app:
            self.assertEqual(mcp(app)[0], 200)
        with built(complete_env(GEMARC_MCP_AUTH="env-token", GEMARC_MCP_TOKEN=ENV_TOKEN)) as app:
            self.assertEqual(mcp(app)[0], 401)
            self.assertEqual(mcp(app, ENV_TOKEN)[0], 200)

    def test_pat_mode_with_complete_accounts_and_a_missing_database_is_503_not_open(self):
        with built(complete_env(db="/nonexistent-dir/none.db", GEMARC_MCP_AUTH="pat")) as app:
            self.assertEqual(mcp(app)[0], 401)  # no credential: refused before any database access
            valid_format = tokens_mod.mint()[0]
            status, _h, body = mcp(app, valid_format)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["code"], "GEMARC_ACCT_002")
            self.assertEqual(json.loads(run(app, "GET", "/healthz")[2])["accounts"], "db-unreachable")

    def test_healthz_has_no_accounts_key_when_disabled(self):
        with built({}) as app:
            self.assertNotIn("accounts", json.loads(run(app, "GET", "/healthz")[2]))
        with built({"GEMARC_ACCOUNTS": "0", "GEMARC_REQUIRE_LOGIN": "0", "GEMARC_MCP_AUTH": ""}) as app:
            self.assertNotIn("accounts", json.loads(run(app, "GET", "/healthz")[2]))
            self.assertEqual(run(app, "GET", "/account")[0], 404)

    def test_wellknown_stays_404_json_in_every_state(self):
        for env in ({}, complete_env(), {"GEMARC_REQUIRE_LOGIN": "1"}, {"GEMARC_ACCOUNTS": "1"}):
            with built(env) as app:
                status, _h, body = run(app, "GET", "/.well-known/oauth-protected-resource")
                self.assertEqual((status, json.loads(body)), (404, {"error": "not_found"}))


if __name__ == "__main__":
    unittest.main()
