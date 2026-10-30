"""Regression tests for the acct-fix round: one class per fixed verifier finding.

Every test here failed (or could not even run) against the code before the fix. The identity provider is the local
fake or a raw loopback socket; the databases are local SQLite files.
"""
from __future__ import annotations

import io
import socket
import threading
import time
import unittest

from gemarc_mcp.accounts import __main__ as cli
from gemarc_mcp.accounts import audit, config as cfg, oidc, ratelimit, retention, schema, security, sessions
from gemarc_mcp.accounts import crypto as C
from gemarc_mcp.accounts import db as dbmod
from gemarc_mcp.accounts.crypto import b64u
from gemarc_mcp.accounts.oidc import ClaimsError, GoogleClient, IdpEndpoints, IdpError
from gemarc_mcp.accounts.tokens import TokenStore
from gemarc_mcp.accounts.users import UserAccounts

from .fake_idp import CLIENT_ID, CLIENT_SECRET, AccountsServer, Browser, ServerCase
from .support import KEY1, KEY2, T0, Clock, DbCase, make_crypto

DAY = 86400
RING1 = "k1:" + b64u(KEY1)
RING2_ONLY = "k2:" + b64u(KEY2)
RING_ROTATED = "k2:" + b64u(KEY2) + ",k1:" + b64u(KEY1)


def run(argv, environ, clock=lambda: T0):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ, out, err, clock)
    return code, out.getvalue(), err.getvalue()


# -- finding: denied signup attempts burn the global budget ------------------------------------------------------
class SignupBudgetIsNotBurnedByDeniedAddresses(ServerCase):
    server_kwargs = {"env": {"VERCEL": "1"},
                     "accounts_extra": {"limit_overrides": {"signup_ip": (2, 3600), "signup_global": (3, 3600)}}}

    def signin(self, sub, ip):
        b = Browser(self.srv.base)
        self.idp.identity = {"sub": sub, "email": f"{sub}@gmail.com"}
        hdr = {"x-vercel-forwarded-for": ip}
        start = b.nav("/auth/google/start", headers=hdr)
        auth = Browser(self.idp.base).get(start.location, cookies=False)
        return b.nav(auth.location, headers=hdr)

    def test_retries_from_one_address_leave_the_global_budget_for_everybody_else(self):
        self.assertEqual(self.signin("a1", "203.0.113.5").status, 303)
        self.assertEqual(self.signin("a2", "203.0.113.5").status, 303)
        for _ in range(8):  # the address is over ITS limit; every retry is denied and must not touch the global one
            self.assertEqual(self.signin("a3", "203.0.113.5").status, 403)
        # global budget is 3 and only 2 registrations happened: a legitimate user elsewhere still gets in
        self.assertEqual(self.signin("b1", "198.51.100.9").status, 303)
        # and the budget is real: the fourth registration overall is refused
        self.assertEqual(self.signin("c1", "192.0.2.77").status, 403)
        self.assertEqual(self.count("users"), 3)


# -- finding: no total deadline on the identity-provider exchange -------------------------------------------------
class DripServer:
    """A loopback 'token endpoint' that sends one byte per ``interval`` seconds after a valid-looking status line."""

    def __init__(self, *, in_headers: bool = False, interval: float = 0.15) -> None:
        self.in_headers, self.interval = in_headers, interval
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._drip, args=(conn,), daemon=True).start()

    def _drip(self, conn) -> None:
        try:
            conn.settimeout(2)
            conn.recv(65536)
            head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 65000\r\n\r\n"
            if self.in_headers:
                head = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 400
            for i in range(len(head)):
                if self.stop.is_set():
                    return
                conn.sendall(head[i:i + 1])
                time.sleep(self.interval)
            while not self.stop.is_set():
                conn.sendall(b" ")
                time.sleep(self.interval)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


class ExchangeHasATotalDeadline(unittest.TestCase):
    def client(self, drip: DripServer, timeout: float = 1.0) -> GoogleClient:
        endpoints = IdpEndpoints(authorize_url="http://127.0.0.1:1/a", token_url=f"http://127.0.0.1:{drip.port}/token",
                                 jwks_url=f"http://127.0.0.1:{drip.port}/jwks")
        return GoogleClient(endpoints, CLIENT_ID, CLIENT_SECRET, "http://127.0.0.1:1/cb", timeout=timeout)

    def check(self, drip: DripServer, call) -> None:
        self.addCleanup(drip.close)
        started = time.monotonic()
        with self.assertRaises(IdpError) as ctx:
            call()
        elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.reason, "transport")
        self.assertLess(elapsed, 4.0, "the 1 s deadline must bound the whole exchange, not each socket read")

    def test_body_drip_on_token_endpoint(self):
        drip = DripServer()
        self.check(drip, lambda: self.client(drip).exchange_code("c", "v" * 43))

    def test_header_drip_on_token_endpoint(self):
        drip = DripServer(in_headers=True)
        self.check(drip, lambda: self.client(drip).exchange_code("c", "v" * 43))

    def test_body_drip_on_the_jwks_endpoint(self):
        drip = DripServer()
        self.check(drip, lambda: self.client(drip).jwks("kid-1"))

    def test_idp_calls_use_their_own_pool_not_the_database_pool(self):
        from gemarc_mcp.accounts.routes import AccountsRuntime

        self.assertTrue(hasattr(AccountsRuntime, "run_idp"))


class HttpsPathKeepsItsTlsGuarantees(unittest.TestCase):
    def test_opener_uses_the_guarded_handler_with_a_verifying_context(self):
        import ssl
        import urllib.request

        opener = oidc.build_opener()
        https = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual([type(h).__name__ for h in https], ["_GuardedHTTPSHandler"])
        self.assertEqual(https[0]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https[0]._context.check_hostname)
        plain = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPHandler)]
        self.assertEqual([type(h).__name__ for h in plain], ["_GuardedHTTPHandler"])


class IdpPoolIsSeparate(ServerCase):
    def test_runtime_has_a_separate_idp_executor(self):
        rt = self.srv.runtime()
        self.assertIsNot(rt.idp_executor, rt.anon_executor)
        self.assertIsNot(rt.idp_executor, rt.pat_executor)


class TokenCreationIsOneExecutorJob(ServerCase):
    def test_housekeeping_rides_along_with_the_create(self):
        page = self.logged_in_page({"sub": "t-1", "email": "t1@gmail.com"})
        csrf = self.browser.csrf(page)
        rt = self.srv.runtime()
        names = []
        original = rt.run_db

        async def spy(fn, *args):
            names.append(getattr(fn, "__name__", "lambda"))
            return await original(fn, *args)

        rt.run_db = spy
        resp = self.browser.post("/account/tokens", {"csrf": csrf, "name": "x"})
        self.assertEqual(resp.status, 200, resp.text[:300])
        self.assertIn("_create_and_tidy", names)
        self.assertNotIn("housekeeping", names, "a second awaited job could be refused as busy after the token exists")
        self.assertEqual(self.count("api_tokens"), 1)


# -- finding: deeply nested JSON from the identity provider ---------------------------------------------------------
class DeepJsonIsAClaimsOrExchangeFailureNotA500(ServerCase):
    def test_token_endpoint_body(self):
        self.idp.faults["token_body"] = b"[" * 40000
        _s, _a, callback = self.login({"sub": "deep-1", "email": "d1@gmail.com"})
        self.assertEqual(callback.status, 502)
        self.assertIn("GEMARC_ACCT_012", callback.text)
        self.assertEqual(self.count("users"), 0)

    def test_id_token_payload_in_the_callback(self):
        self.idp.faults["raw_payload"] = "[" * 40000
        _s, _a, callback = self.login({"sub": "deep-2", "email": "d2@gmail.com"})
        self.assertEqual(callback.status, 403)
        self.assertIn("GEMARC_ACCT_013", callback.text)
        self.assertEqual(self.count("users"), 0)

    def test_unit_level_mapping(self):
        with self.assertRaises(ValueError):
            oidc._json_no_dups("[" * 40000)
        head = b64u(b'{"alg":"RS256"}')
        token = ".".join((head, b64u(b"[" * 40000), "c2ln"))
        with self.assertRaises(ClaimsError) as ctx:
            oidc.validate_id_token(token, client_id=CLIENT_ID, nonce_hash="x", now=T0)
        self.assertEqual(ctx.exception.reason, "payload")


# -- finding: no trusted client address -----------------------------------------------------------------------------
class NoTrustedAddressWarning(unittest.TestCase):
    def env(self, **over):
        env = {
            "GEMARC_ACCOUNTS": "1", "GEMARC_ACCOUNTS_BASE_URL": "https://gem-arc.vercel.app",
            "GEMARC_GOOGLE_CLIENT_ID": "1234-abc.apps.googleusercontent.com", "GEMARC_GOOGLE_CLIENT_SECRET": "s3cr3t-value-zz",
            "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(bytes(range(32))),
            "GEMARC_ACCOUNTS_DATABASE_URL": "postgresql://u:p@ep-x-pooler.eu-1.aws.neon.tech/db?sslmode=verify-full&sslrootcert=system",
        }
        env.update(over)
        return env

    def test_warns_outside_vercel_without_hops(self):
        self.assertIn("no_trusted_client_ip", cfg.load(self.env()).warnings)

    def test_quiet_on_vercel_or_with_hops(self):
        self.assertNotIn("no_trusted_client_ip", cfg.load(self.env(VERCEL="1")).warnings)
        self.assertNotIn("no_trusted_client_ip", cfg.load(self.env(GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS="1")).warnings)


class NoTrustedAddressDoesNotShareOneBucket(ServerCase):
    server_kwargs = {"accounts_extra": {"prefilter": (6, 6)}}  # no VERCEL, no hops: no address can be trusted

    def statuses(self, agent: str, n: int):
        b = Browser(self.srv.base)
        return [b.get("/account", headers={"User-Agent": agent}).status for _ in range(n)]

    def test_one_noisy_client_does_not_lock_out_another(self):
        noisy = self.statuses("noisy-client/1", 10)
        self.assertIn(429, noisy)
        calm = self.statuses("calm-client/1", 3)
        self.assertEqual(calm, [200, 200, 200])

    def test_key_shape(self):
        rt = self.srv.runtime()
        a = rt.client_key({"headers": [(b"user-agent", b"A")], "client": ("10.0.0.1", 1)})
        b = rt.client_key({"headers": [(b"user-agent", b"B")], "client": ("10.0.0.1", 1)})
        self.assertTrue(a.startswith("no-ip:"))
        self.assertNotEqual(a, b)
        self.assertEqual(a, rt.client_key({"headers": [(b"user-agent", b"A")], "client": ("10.0.0.1", 9)}))


# -- finding: '$' matches before a trailing newline -----------------------------------------------------------------
class StrictFormats(unittest.TestCase):
    def test_exactly_43_characters(self):
        good = "A" * 43
        self.assertIsNotNone(security.B64U43_RE.match(good))
        self.assertIsNone(security.B64U43_RE.match(good + "\n"))
        self.assertTrue(sessions.valid_sid_format(good))
        self.assertFalse(sessions.valid_sid_format(good + "\n"))

    def test_revoke_path_and_audit_shapes(self):
        from gemarc_mcp.accounts.routes import REVOKE_PATH

        self.assertIsNotNone(REVOKE_PATH.match("/account/tokens/abcdefgh/revoke"))
        self.assertIsNone(REVOKE_PATH.match("/account/tokens/abcdefgh/revoke\n"))
        for pattern in (audit._USER_RE, audit._HASH_RE, audit._REF_RE, audit._CODE_RE):
            self.assertIsNone(pattern.match("a" * 32 + "\n") if pattern is audit._USER_RE else pattern.match("a\n"))


# -- finding: weak or ambiguous key material ------------------------------------------------------------------------
class KeyRingStrength(unittest.TestCase):
    def ring(self, *entries):
        return ",".join(f"{kid}:{b64u(key)}" for kid, key in entries)

    def test_weak_patterns_are_refused(self):
        for key in (bytes([0, 1] * 16), b"password" * 4, bytes([7] * 32), bytes(range(8)) * 4):
            with self.assertRaises(C.KeyRingError) as ctx:
                C.KeyRing.parse(self.ring(("k1", key)))
            self.assertEqual(ctx.exception.reason, "weak")

    def test_sequential_and_random_keys_are_accepted(self):
        C.KeyRing.parse(self.ring(("k1", bytes(range(32)))))
        C.KeyRing.parse(self.ring(("k1", KEY1)))

    def test_same_material_under_two_ids_is_refused(self):
        with self.assertRaises(C.KeyRingError) as ctx:
            C.KeyRing.parse(self.ring(("k1", KEY1), ("k2", KEY1)))
        self.assertEqual(ctx.exception.reason, "duplicate_key")

    def test_padded_base64url_has_no_second_string_form(self):
        with self.assertRaises(ValueError):
            C.b64u_decode(b64u(b"abc") + "=")
        with self.assertRaises(ValueError):
            C.b64u_decode("QUJD=")
        self.assertEqual(C.b64u_decode("QUJD"), b"ABC")


class RatelimitKeyIsUnambiguous(unittest.TestCase):
    def test_no_collision_across_the_scope_subject_boundary(self):
        c = make_crypto()
        self.assertNotEqual(c.ratelimit_key("a", "b\x00c"), c.ratelimit_key("a\x00b", "c"))
        self.assertEqual(c.ratelimit_keys_all("a", "b")[0], c.ratelimit_key("a", "b"))


# -- finding: e-mail uniqueness differs between the two paths ---------------------------------------------------------
class EmailRecycleOnEmailChange(DbCase):
    def test_same_sub_moving_onto_an_active_email_disables_the_old_row(self):
        store = UserAccounts(self.db, self.crypto)
        a = store.login("sub-A", "alice@gmail.com", False, T0)
        b = store.login("sub-B", "bob@gmail.com", False, T0)
        token = TokenStore(self.db, self.crypto).create(b.user_id, "t", T0)
        sess = sessions.SessionStore(self.db, self.crypto).create(b.user_id, T0)
        moved = store.login("sub-A", "bob@gmail.com", False, T0 + 5)
        self.assertEqual(moved.status, "active")
        self.assertTrue(moved.email_changed)
        self.assertEqual(moved.recycled, (b.user_id,))
        self.assertEqual(self.count("users", "status = 'active'"), 1)
        self.assertEqual(store.login("sub-B", "bob@gmail.com", False, T0 + 9).status, "disabled")
        self.assertEqual(self.count("sessions", "user_id = ?", (b.user_id,)), 0)
        self.assertEqual(self.count("api_tokens", "user_id = ? AND revoked_at IS NULL", (b.user_id,)), 0)
        self.assertEqual(self.count("audit_log", "event = 'email_recycled'"), 1)
        self.assertIsNotNone(token)
        self.assertIsNotNone(sess)
        self.assertEqual(a.status, "active")


# -- finding: retention and operator commands were not wired --------------------------------------------------------
class RetentionIsWired(DbCase):
    def stores(self):
        return dict(sessions=sessions.SessionStore(self.db, self.crypto), tokens=TokenStore(self.db, self.crypto),
                    limiter=ratelimit.RateLimiter(self.db, self.crypto))

    def seed(self):
        store = UserAccounts(self.db, self.crypto)
        user = store.login("sub-1", "u1@gmail.com", False, T0)
        tokens = TokenStore(self.db, self.crypto)
        tokens.create(user.user_id, "short", T0, days=1)
        sessions.SessionStore(self.db, self.crypto).create(user.user_id, T0)
        ratelimit.RateLimiter(self.db, self.crypto).hit(T0, "login_start", "x")
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO oauth_states (state_hash, nonce_hash, binding_hash, created_at, expires_at) "
                       "VALUES ('s', 'n', 'b', ?, ?)", (T0, T0 + 600))
        return user

    def test_run_once_purges_every_table_past_its_retention(self):
        self.seed()
        late = T0 + 40 * DAY
        done = retention.run_once(self.db, retention_days=30, now=late, **self.stores())
        self.assertEqual(self.count("audit_log", "ts < ?", (late - 30 * DAY,)), 0)
        self.assertEqual(self.count("rate_limits"), 0)
        self.assertEqual(self.count("sessions"), 0)
        self.assertEqual(self.count("api_tokens"), 0)
        self.assertEqual(self.count("oauth_states"), 0)
        self.assertTrue(all(v >= 1 for v in done.values()), done)

    def test_fresh_rows_are_kept(self):
        self.seed()
        retention.run_once(self.db, retention_days=30, now=T0 + 3600, **self.stores())
        self.assertGreaterEqual(self.count("audit_log"), 1)
        self.assertEqual(self.count("rate_limits"), 1)
        self.assertEqual(self.count("sessions"), 1)
        self.assertEqual(self.count("api_tokens"), 1)

    def test_batches_are_bounded_and_run_until_empty_finishes(self):
        for i in range(7):
            with self.db.transaction() as tx:
                audit.record(tx, T0, "logout", user_id=None)
        one = retention.run_once(self.db, retention_days=7, now=T0 + 30 * DAY, batch=3, **self.stores())
        self.assertEqual(one["audit_log"], 3)
        removed_sum = retention.run_until_empty(self.db, retention_days=7, now=T0 + 30 * DAY, batch=3, **self.stores())
        self.assertEqual(removed_sum["audit_log"], 4)
        self.assertEqual(self.count("audit_log"), 0)

    def test_cli_gc(self):
        self.seed()
        env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
               "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1", "GEMARC_ACCOUNTS_KEYS": RING1}
        code, out, err = run(["gc"], env, clock=lambda: T0 + 400 * DAY)
        self.assertEqual(code, 0, err)
        self.assertIn("audit_log=", out)
        self.assertEqual(self.count("audit_log"), 0)
        self.assertEqual(self.count("rate_limits"), 0)
        self.assertEqual(self.count("api_tokens"), 0)


class HousekeepingRunsInTheApp(ServerCase):
    def test_a_login_prunes_old_rows_and_the_pass_is_throttled(self):
        self.assertEqual(self.login({"sub": "h-1", "email": "h1@gmail.com"})[2].status, 303)
        old = self.count("audit_log", "ts = ?", (T0,))
        self.assertGreaterEqual(old, 1)
        self.clock.advance(31 * DAY)
        self.assertEqual(self.login({"sub": "h-2", "email": "h2@gmail.com"}, Browser(self.srv.base))[2].status, 303)
        self.assertEqual(self.count("audit_log", "ts = ?", (T0,)), 0, "30-day retention must run after a login")
        # throttle: a second pass inside five minutes does nothing
        with self.srv.database().transaction() as tx:
            tx.execute("INSERT INTO audit_log (id, ts, event) VALUES ('x1', ?, 'logout')", (T0,))
        self.srv.runtime().housekeeping(int(self.clock()) + 10)
        self.assertEqual(self.count("audit_log", "id = 'x1'"), 1)
        self.srv.runtime().housekeeping(int(self.clock()) + 400)
        self.assertEqual(self.count("audit_log", "id = 'x1'"), 0)


class OperatorCommands(DbCase):
    def setUp(self):
        super().setUp()
        self.env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
                    "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1", "GEMARC_ACCOUNTS_KEYS": RING1}
        self.store = UserAccounts(self.db, self.crypto)
        self.alice = self.store.login("sub-A", "alice@gmail.com", False, T0).user_id
        self.bob = self.store.login("sub-B", "bob@gmail.com", False, T0).user_id
        self.tokens = TokenStore(self.db, self.crypto)
        self.sess = sessions.SessionStore(self.db, self.crypto)

    def test_disable_and_enable_by_email_and_by_id(self):
        self.tokens.create(self.alice, "t", T0)
        self.sess.create(self.alice, T0)
        code, out, err = run(["disable-user", "--email", "Alice@gmail.com"], self.env)
        self.assertEqual(code, 0, err)
        self.assertIn(self.alice, out)
        self.assertNotIn("alice@", out + err)
        self.assertEqual(self.count("users", "id = ? AND status = 'disabled'", (self.alice,)), 1)
        self.assertEqual(self.count("sessions", "user_id = ?", (self.alice,)), 0)
        self.assertEqual(self.count("api_tokens", "user_id = ? AND revoked_at IS NULL", (self.alice,)), 0)
        self.assertEqual(run(["disable-user", "--id", self.alice], self.env)[0], 0)  # idempotent
        self.assertEqual(run(["disable-user", "--id", "0" * 32], self.env)[0], 1)  # no such user
        self.assertEqual(run(["enable-user", "--id", self.alice], self.env)[0], 0)
        self.assertEqual(self.count("users", "id = ? AND status = 'active'", (self.alice,)), 1)
        self.assertGreaterEqual(self.count("audit_log", "event = 'user_disabled'"), 1)
        self.assertEqual(self.count("audit_log", "event = 'user_enabled'"), 1)

    def test_selector_errors(self):
        self.assertEqual(run(["disable-user"], self.env)[0], 2)
        self.assertEqual(run(["disable-user", "--id", "x", "--email", "a@b.c"], self.env)[0], 2)
        self.assertEqual(run(["disable-user", "--id", "nothex"], self.env)[0], 2)
        code, out, err = run(["disable-user", "--email", "nobody@gmail.com"], self.env)
        self.assertEqual(code, 1)
        self.assertNotIn("nobody@", out + err)

    def test_grant_and_revoke_write_gate_write_tokens(self):
        from gemarc_mcp.accounts.tokens import TokenError

        with self.assertRaises(TokenError):
            self.tokens.create(self.alice, "w", T0, scope="write", allow_write=True)
        self.assertEqual(run(["grant-write", "--email", "alice@gmail.com"], self.env)[0], 0)
        self.assertIsNotNone(self.tokens.create(self.alice, "w", T0, scope="write", allow_write=True))
        self.assertEqual(run(["revoke-write", "--id", self.alice], self.env)[0], 0)
        with self.assertRaises(TokenError):
            self.tokens.create(self.alice, "w2", T0, scope="write", allow_write=True)
        self.assertEqual(self.count("audit_log", "event IN ('write_granted', 'write_revoked')"), 2)

    def test_purge_needs_yes_and_leaves_no_user_data(self):
        self.tokens.create(self.alice, "t", T0)
        self.sess.create(self.alice, T0)
        self.assertEqual(run(["purge-user", "--id", self.alice], self.env)[0], 2)
        self.assertEqual(self.count("users", "id = ?", (self.alice,)), 1)
        code, out, _ = run(["purge-user", "--id", self.alice, "--yes"], self.env)
        self.assertEqual(code, 0)
        self.assertIn("purged", out)
        self.assertEqual(self.count("users", "id = ?", (self.alice,)), 0)
        self.assertEqual(self.count("sessions", "user_id = ?", (self.alice,)), 0)
        self.assertEqual(self.count("api_tokens", "user_id = ?", (self.alice,)), 0)
        self.assertEqual(self.count("audit_log", "user_id = ?", (self.alice,)), 0)

    def test_reconcile_disables_users_the_policy_no_longer_allows(self):
        self.store.login("sub-C", "carol@example.org", False, T0)  # not possible via the app; seeds a stale row
        env = dict(self.env, GEMARC_ACCOUNTS_ALLOWED_EMAILS="alice@gmail.com")
        code, out, _ = run(["reconcile", "--dry-run"], env)
        self.assertEqual(code, 0)
        self.assertIn("examined 3, would disable 2", out)
        self.assertEqual(self.count("users", "status = 'active'"), 3)
        code, out, _ = run(["reconcile"], env)
        self.assertIn("disabled 2", out)
        self.assertEqual(self.count("users", "status = 'active'"), 1)
        self.assertEqual(self.count("users", "id = ? AND status = 'active'", (self.alice,)), 1)
        self.assertEqual(self.count("audit_log", "event = 'user_reconciled'"), 2)
        self.assertIn("disabled 0", run(["reconcile"], env)[1])

    def test_status_reports_numbers_only(self):
        env = dict(self.env)
        code, out, err = run(["status"], env)
        self.assertEqual(code, 1)  # the accounts variables are not complete in this environment
        self.assertIn("users: active=2", out)
        self.assertIn("can_write=0", out)
        self.assertIn("schema: head 0001_init; pending 0; unknown 0", out)
        self.assertNotIn("@", out + err)
        self.assertNotIn(self.alice, out + err)

    def test_status_works_with_only_the_migrate_url_as_the_runbook_uses_it(self):
        env = {"GEMARC_MIGRATE_DATABASE_URL": self.env["GEMARC_MIGRATE_DATABASE_URL"],
               "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1"}  # no keys, no other accounts variables (section 8.7)
        code, out, err = run(["status"], env)
        self.assertIn("schema: head 0001_init; pending 0; unknown 0", out)
        self.assertIn("users: active=2", out)
        self.assertIn("key references: skipped", out)
        self.assertNotIn("database:", out + err)  # the schema is reported, not a refusal to open the database

    def test_status_hints_at_the_neon_tls_string(self):
        env = {"GEMARC_ACCOUNTS_DATABASE_URL": "postgresql://u:SECRETPW@ep-x-pooler.example.neon.tech/db?sslmode=require",
               "GEMARC_ACCOUNTS_KEYS": RING1}
        code, out, err = run(["status"], env)
        self.assertEqual(code, 1)
        self.assertIn("tls_insecure", out)
        self.assertIn("GEMARC_ACCOUNTS_DB_TLS_RELAXED", out)
        self.assertNotIn("SECRETPW", out + err)

    def test_doctor_hint_for_tls_insecure(self):
        env = {"GEMARC_ACCOUNTS": "1", "GEMARC_ACCOUNTS_DATABASE_URL": "postgresql://u:SECRETPW@h.example/db?sslmode=require"}
        code, out, err = run(["doctor"], env)
        self.assertEqual(code, 1)
        self.assertIn("GEMARC_ACCOUNTS_DB_TLS_RELAXED", out)
        self.assertNotIn("SECRETPW", out + err)


# -- finding: audit flooding by an authenticated token holder --------------------------------------------------------
class ScopeDeniedIsDeduplicated(DbCase):
    def record(self, user, ref, now):
        with self.db.transaction() as tx:
            return audit.record(tx, now, "token_scope_denied", user_id=user, ref=ref)

    def test_one_row_per_user_and_token_per_five_minutes(self):
        user = UserAccounts(self.db, self.crypto).login("s", "a@gmail.com", False, T0).user_id
        results = [self.record(user, "abcdefgh", T0 + i) for i in range(500)]
        self.assertEqual(sum(results), 2)  # T0 and T0 + 300 + ... : first window plus the one after the 300 s dedupe
        self.assertEqual(self.count("audit_log", "event = 'token_scope_denied'"), 2)

    def test_hourly_ceiling_per_user(self):
        user = UserAccounts(self.db, self.crypto).login("s", "a@gmail.com", False, T0).user_id
        for i in range(200):
            self.record(user, f"tok{i:05d}", T0 + i)
        self.assertEqual(self.count("audit_log", "event = 'token_scope_denied'"), audit.USER_HOURLY_CEILING)
        other = UserAccounts(self.db, self.crypto).login("s2", "b@gmail.com", False, T0).user_id
        self.assertTrue(self.record(other, "abcdefgh", T0 + 5))


# -- finding: dropped key id before rekey ended --------------------------------------------------------------------
class DroppedKeyIsDetected(DbCase):
    def setUp(self):
        super().setUp()
        UserAccounts(self.db, self.crypto).login("sub-A", "alice@gmail.com", False, T0)
        self.env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
                    "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1"}

    def test_unknown_key_ids(self):
        self.assertEqual(UserAccounts(self.db, self.crypto).unknown_key_ids(), [])
        k2 = make_crypto(("k2", KEY2))
        self.assertEqual(UserAccounts(self.db, k2).unknown_key_ids(), ["k1"])
        rotated = make_crypto(("k2", KEY2), ("k1", KEY1))
        self.assertEqual(UserAccounts(self.db, rotated).unknown_key_ids(), [])

    def test_cli_rekey_status_and_doctor_flag_it(self):
        env = dict(self.env, GEMARC_ACCOUNTS_KEYS=RING2_ONLY)
        code, out, err = run(["rekey", "--dry-run"], env)
        self.assertEqual(code, 1)
        self.assertIn("k1", err)
        code, out, _ = run(["status"], env)
        self.assertEqual(code, 1)
        self.assertIn("key ring problem", out)
        code, out, _ = run(["doctor"], dict(env, GEMARC_ACCOUNTS_DATABASE_URL=self.env["GEMARC_MIGRATE_DATABASE_URL"]))
        self.assertIn("NOT in GEMARC_ACCOUNTS_KEYS", out)
        ok_env = dict(self.env, GEMARC_ACCOUNTS_KEYS=RING_ROTATED)
        self.assertEqual(run(["rekey"], ok_env)[0], 0)
        self.assertEqual(run(["rekey", "--dry-run"], dict(self.env, GEMARC_ACCOUNTS_KEYS=RING2_ONLY))[0], 0)

    def test_migrate_warns(self):
        code, out, _ = run(["migrate"], dict(self.env, GEMARC_ACCOUNTS_KEYS=RING2_ONLY))
        self.assertEqual(code, 0)
        self.assertIn("warning: stored rows reference key id(s) k1", out)


class DroppedKeyMakesTheAppRefuseToServe(ServerCase):
    def test_503_with_the_misconfigured_code(self):
        self.assertEqual(self.login({"sub": "k-1", "email": "k1@gmail.com"})[2].status, 303)
        self.srv.stop()
        second = AccountsServer(self.tmp, self.clock, env={"GEMARC_ACCOUNTS_KEYS": RING2_ONLY}, migrate=False).start()
        # the ServerCase cleanup stops the first server a second time; keep that harmless
        self.addCleanup(second.stop)
        resp = Browser(second.base).nav("/auth/google/start")
        self.assertEqual(resp.status, 503)
        self.assertIn("GEMARC_ACCT_004", resp.text)
        self.assertEqual(self.count("users"), 1)


# -- finding: cap check not atomic on Postgres ------------------------------------------------------------------------
class UserCapIsSerialisedWithTheInsert(DbCase):
    def test_signup_lock_taken_only_when_a_cap_applies(self):
        taken = []
        original = dbmod.Tx.advisory_xact_lock

        def spy(self, key):
            taken.append(key)
            return original(self, key)

        dbmod.Tx.advisory_xact_lock = spy
        try:
            store = UserAccounts(self.db, self.crypto)
            store.login("s1", "a@gmail.com", False, T0, max_users=5)
            self.assertEqual(taken, [dbmod.SIGNUP_LOCK_KEY])
            store.login("s2", "b@gmail.com", False, T0, max_users=0)
            store.login("s1", "a@gmail.com", False, T0 + 1, max_users=5)  # existing user: no lock
            self.assertEqual(taken, [dbmod.SIGNUP_LOCK_KEY])
        finally:
            dbmod.Tx.advisory_xact_lock = original

    def test_cap_still_enforced(self):
        store = UserAccounts(self.db, self.crypto)
        self.assertEqual(store.login("s1", "a@gmail.com", False, T0, max_users=1).status, "active")
        self.assertEqual(store.login("s2", "b@gmail.com", False, T0, max_users=1).status, "closed")


# -- finding: migration failure reported as an outage -----------------------------------------------------------------
class MigrationFailureHasItsOwnReason(DbCase):
    migrate_on_setup = False

    def test_foreign_table_is_not_reported_as_unavailable(self):
        with self.db.transaction() as tx:
            tx.execute("CREATE TABLE users (id TEXT, other TEXT)")
        with self.assertRaises(schema.MigrationError) as ctx:
            schema.migrate(self.db, T0)
        self.assertEqual((ctx.exception.reason, ctx.exception.version), ("statement_failed", "0001_init"))
        self.assertEqual(self.count("schema_migrations"), 0)
        env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"), "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1"}
        code, _out, err = run(["migrate"], env)
        self.assertEqual(code, 1)
        self.assertIn("statement_failed in 0001_init", err)
        self.assertNotIn("GEMARC_ACCT_002", err)


# -- finding: audit rows keep a pseudonymous reference after deletion --------------------------------------------------
class DeletionClearsTheTokenReference(DbCase):
    def test_no_ref_and_no_user_id_remain(self):
        store = UserAccounts(self.db, self.crypto)
        user = store.login("s1", "a@gmail.com", False, T0).user_id
        created = TokenStore(self.db, self.crypto).create(user, "t", T0)
        self.assertGreaterEqual(self.count("audit_log", "ref = ?", (created.id,)), 1)
        self.assertTrue(store.delete(user, T0 + 5))
        self.assertEqual(self.count("audit_log", "ref = ?", (created.id,)), 0)
        self.assertEqual(self.count("audit_log", "user_id IS NOT NULL"), 0)
        self.assertEqual(self.count("audit_log", "ref IS NOT NULL AND event <> 'migration_applied'"), 0)


if __name__ == "__main__":
    unittest.main()
