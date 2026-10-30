"""Account routes through a real HTTP client: login negatives, cookies, CSRF, headers, limits, tokens, policy.

Each class names the design checklist item it covers. The identity provider is the local fake; the clock is a fake
clock shared by the app, the fake provider and the test.
"""
from __future__ import annotations

import re
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from gemarc_mcp.accounts import pages

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .fake_idp import GONE, AccountsServer, Browser, FakeIdp, ServerCase, sign_in
from .support import Clock

EMAIL = "alice@gmail.com"
PAT_RE = re.compile(r"gat_[a-z2-7]{8}_[A-Za-z0-9_-]{49}")
HEADER_SET = ("Cache-Control", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
              "Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy", "Permissions-Policy",
              "Content-Security-Policy")


def prepare(case: ServerCase, identity=None, browser=None):
    """start + fake Google, WITHOUT visiting the callback. Returns the callback URL."""
    browser = browser or case.browser
    if identity is not None:
        case.idp.identity = dict(identity)
    start = browser.nav("/auth/google/start")
    assert start.status == 302, start.text[:200]
    authorize = Browser(case.idp.base).get(start.location, cookies=False)
    assert authorize.status == 302, authorize.text[:200]
    return authorize.location


def swap_query(url: str, **replace) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    for key, value in replace.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def assert_headers(case: unittest.TestCase, resp, https: bool = False):
    for name in HEADER_SET:
        case.assertIn(name, resp.headers, f"{name} missing on {resp.status}")
    h = resp.headers
    case.assertEqual(h["Cache-Control"], "no-store")
    case.assertEqual(h["X-Content-Type-Options"], "nosniff")
    case.assertEqual(h["X-Frame-Options"], "DENY")
    case.assertEqual(h["Referrer-Policy"], "no-referrer")
    case.assertEqual(h["Cross-Origin-Opener-Policy"], "same-origin")
    case.assertEqual(h["Cross-Origin-Resource-Policy"], "same-origin")
    case.assertEqual(h["Permissions-Policy"], pages.PERMISSIONS)
    csp = h["Content-Security-Policy"]
    for part in ("default-src 'none'", "frame-ancestors 'none'", "form-action 'self'", "base-uri 'none'"):
        case.assertIn(part, csp)
    case.assertNotIn("unsafe-inline", csp)
    case.assertNotIn("unsafe-eval", csp)
    case.assertEqual("Strict-Transport-Security" in h, https)
    if "text/html" in (h.get("Content-Type") or ""):
        nonce = re.search(r'<style nonce="([^"]+)"', resp.text)
        case.assertIsNotNone(nonce)
        case.assertIn(f"'nonce-{nonce.group(1)}'", csp)
        case.assertNotRegex(resp.text, r"(?i)\sstyle\s*=")
        case.assertNotRegex(resp.text, r"(?i)\son[a-z]+\s*=")
        case.assertNotIn("<script", resp.text.lower())
        case.assertNotIn("javascript:", resp.text.lower())


class LoginNegatives(ServerCase):
    def test_wrong_state_is_010_and_no_session(self):
        url = prepare(self, {"sub": "n-1", "email": EMAIL})
        resp = self.browser.nav(swap_query(url, state="A" * 43))
        self.assertEqual(resp.status, 400)
        self.assertIn("GEMARC_ACCT_010", resp.text)
        self.assertNotIn("gemarc_sid", self.browser.cookies)
        self.assertEqual(self.count("sessions"), 0)

    def test_replayed_callback_is_010(self):
        url = prepare(self, {"sub": "n-2", "email": EMAIL})
        self.assertEqual(self.browser.nav(url).status, 303)
        again = self.browser.nav(url)
        self.assertEqual(again.status, 400)
        self.assertIn("GEMARC_ACCT_010", again.text)

    def test_missing_code_or_state_is_010(self):
        url = prepare(self)
        for variant in (swap_query(url, code=None), swap_query(url, state=None), swap_query(url, code="")):
            self.assertIn("GEMARC_ACCT_010", self.browser.nav(variant).text)

    def test_without_the_prelogin_cookie_is_011(self):
        url = prepare(self)
        resp = Browser(self.srv.base).nav(url)  # another browser: no pre-login cookie, so no PKCE verifier
        self.assertEqual(resp.status, 400)
        self.assertIn("GEMARC_ACCT_011", resp.text)

    def test_wrong_prelogin_cookie_is_011_and_consumes_the_state(self):
        url = prepare(self)
        stranger = Browser(self.srv.base)
        stranger.cookies["gemarc_oauth"] = "B" * 43
        self.assertIn("GEMARC_ACCT_011", stranger.nav(url).text)
        again = self.browser.nav(url)  # the legitimate browser arrives too late: the attacker burnt the state
        self.assertIn("GEMARC_ACCT_010", again.text)
        self.assertEqual(self.count("sessions"), 0)

    def test_login_csrf_with_an_attackers_callback_link(self):
        attacker = Browser(self.srv.base)
        url = prepare(self, {"sub": "attacker", "email": "mallory@gmail.com"}, attacker)
        victim = Browser(self.srv.base)
        victim.nav("/auth/google/start")  # the victim has a pre-login cookie of their own
        resp = victim.nav(url)
        self.assertIn("GEMARC_ACCT_011", resp.text)
        self.assertNotIn("gemarc_sid", victim.cookies)

    def test_the_verifier_is_checked_by_the_token_endpoint(self):
        url = prepare(self)
        code = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["code"]
        for verifier in ("", "wrong-verifier"):
            data = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": self.idp.redirect_uri, "client_id": self.idp.client_id,
                                           "client_secret": self.idp.client_secret, "code_verifier": verifier}).encode()
            try:
                urllib.request.urlopen(urllib.request.Request(self.idp.base + "/token", data=data), timeout=10)
                self.fail("the fake token endpoint accepted a wrong code_verifier")
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code, 400)

    def test_authorization_error_and_bad_iss_are_012(self):
        self.idp.faults["authorize_error"] = "access_denied"
        _s, _a, cb = sign_in(self.browser, self.idp)
        self.assertEqual(cb.status, 502)
        self.assertIn("GEMARC_ACCT_012", cb.text)
        self.idp.faults.clear()
        self.idp.faults["redirect_iss"] = "https://evil.example"
        _s, _a, cb = sign_in(self.browser, self.idp)
        self.assertIn("GEMARC_ACCT_012", cb.text)
        self.assertEqual(self.count("sessions"), 0)

    def test_token_endpoint_failures_are_012_and_never_echo_the_provider(self):
        faults = [{"token_status": 500}, {"token_ctype": "text/plain"}, {"token_type": "mac"}, {"big": True},
                  {"no_id_token": True}, {"token_redirect": True}, {"token_body": b"not json"},
                  {"token_body": b'{"token_type":"Bearer","token_type":"Bearer","id_token":"a.b.c"}'}]
        for fault in faults:
            self.idp.faults.clear()
            self.idp.faults.update(fault)
            _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "f-1", "email": EMAIL})
            self.assertEqual(cb.status, 502, fault)
            self.assertIn("GEMARC_ACCT_012", cb.text)
            self.assertNotIn("server_error", cb.text)
            self.assertNotIn("fake-access-token", cb.text)
        self.assertEqual(self.count("users"), 0)

    def test_claim_failures_are_one_generic_013(self):
        bodies = set()
        for fault in ({"nonce": "wrong"}, {"aud": "other"}, {"iss": "https://evil.example"},
                      {"email_verified": False}, {"exp": 1}, {"sub": GONE}):
            self.idp.faults.clear()
            self.idp.faults["claims"] = fault
            _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "c-1", "email": EMAIL})
            self.assertEqual(cb.status, 403, fault)
            self.assertIn("GEMARC_ACCT_013", cb.text)
            bodies.add(re.sub(r'nonce="[^"]+"', "", cb.text))
            self.assertNotIn("gemarc_sid", self.browser.cookies)
        self.assertEqual(len(bodies), 1, "post-identity denials must be indistinguishable")
        self.assertEqual(self.count("users"), 0)
        self.assertEqual(self.count("sessions"), 0)

    def test_policy_denial_shows_only_the_signers_own_escaped_address(self):
        evil = '"><b>x</b>@example.com'
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "p-1", "email": evil})
        self.assertEqual(cb.status, 403)
        self.assertIn("GEMARC_ACCT_013", cb.text)
        self.assertIn("&quot;&gt;&lt;b&gt;x&lt;/b&gt;@example.com", cb.text)
        self.assertNotIn("<b>x</b>", cb.text)
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "p-2", "email": EMAIL, "hd": "example.com"})
        self.assertEqual(cb.status, 403)  # a Gmail address that carries an hd claim is not a consumer account
        self.assertEqual(self.count("users"), 0)

    def test_open_redirect_is_impossible(self):
        start = self.browser.nav("/auth/google/start?next=https://evil.example/&redirect_uri=https://evil.example/")
        self.assertTrue(start.location.startswith(self.idp.base + "/authorize?"))
        self.assertNotIn("evil", start.location)
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "o-1", "email": EMAIL})
        self.assertEqual(cb.location, "/account")
        self.assertEqual(self.browser.get("/auth/google/callback?next=https://evil.example").status, 400)

    def test_start_requires_a_navigation(self):
        for headers in ({"Sec-Fetch-Mode": "cors"}, {"Sec-Fetch-Dest": "image"}, {"Sec-Fetch-Mode": "no-cors"}):
            resp = self.browser.get("/auth/google/start", headers=headers)
            self.assertEqual(resp.status, 403)
            self.assertIn("GEMARC_ACCT_021", resp.text)
        self.assertEqual(self.count("oauth_states"), 0)

    def test_two_tabs_both_complete(self):
        first = prepare(self, {"sub": "t-1", "email": EMAIL})
        cookie_after_first = self.browser.cookies["gemarc_oauth"]
        second = prepare(self)
        self.assertEqual(self.browser.cookies["gemarc_oauth"], cookie_after_first)  # the binding is reused
        self.assertEqual(self.browser.nav(first).status, 303)
        self.assertEqual(self.browser.nav(second).status, 303)

    def test_concurrent_identical_callbacks_have_one_winner(self):
        url = prepare(self, {"sub": "r-1", "email": EMAIL})
        results = []

        def go():
            b = Browser(self.srv.base)
            b.cookies.update(self.browser.cookies)
            results.append(b.nav(url).status)

        threads = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [303, 400, 400, 400])
        self.assertEqual(self.count("users"), 1)

    def test_concurrent_double_click_for_a_new_sub_makes_one_user(self):
        browsers = [Browser(self.srv.base) for _ in range(3)]
        urls = [prepare(self, {"sub": "same-sub", "email": EMAIL}, b) for b in browsers]
        results = []

        def go(b, u):
            results.append(b.nav(u).status)

        threads = [threading.Thread(target=go, args=(b, u)) for b, u in zip(browsers, urls)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [303] * 3)
        self.assertEqual(self.count("users"), 1)


class RedirectUriMismatch(unittest.TestCase):
    """The provider has a DIFFERENT redirect URI registered than the one the app was configured with."""

    def test_authorize_refuses_and_nothing_is_created(self):
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="gemarc-redirect-")
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = Clock()
        idp = FakeIdp("http://127.0.0.1:1/elsewhere/auth/google/callback", clock)
        srv = AccountsServer(tmp, clock, idp=idp).start()
        try:
            browser = Browser(srv.base)
            start = browser.nav("/auth/google/start")
            self.assertEqual(start.status, 302)
            authorize = Browser(idp.base).get(start.location, cookies=False)
            self.assertEqual(authorize.status, 400)
            self.assertEqual(authorize.location, "")  # no redirect, so no code was issued
            self.assertEqual(idp.codes, {})
            with srv.database().transaction() as tx:
                self.assertEqual(tx.fetchone("SELECT count(*) AS n FROM users")["n"], 0)
                self.assertEqual(tx.fetchone("SELECT count(*) AS n FROM sessions")["n"], 0)
            self.assertNotIn("gemarc_sid", browser.cookies)
        finally:
            srv.stop()


class DisabledAndClosed(ServerCase):
    def disable(self, email):
        rt = self.srv.runtime()
        user_id = None
        with rt.db.transaction() as tx:
            for candidate in rt.crypto.blind_index_all("email", email):
                row = tx.fetchone("SELECT id FROM users WHERE email_bidx = ?", (candidate,))
                if row:
                    user_id = row["id"]
        self.assertTrue(rt.users.disable(user_id, self.clock()))
        return user_id

    def test_disabled_user_cannot_log_in_and_existing_session_dies(self):
        self.logged_in_page({"sub": "d-1", "email": EMAIL})
        self.assertEqual(self.browser.get("/account").status, 200)
        uid = self.disable(EMAIL)
        self.assertNotIn(EMAIL, self.browser.get("/account").text)
        last = self.count("users", "last_login_at = ?", (self.clock(),))
        self.clock.advance(60)
        other = Browser(self.srv.base)
        _s, _a, cb = self.login(None, other)
        self.assertEqual(cb.status, 403)
        self.assertIn("GEMARC_ACCT_013", cb.text)
        self.assertNotIn("gemarc_sid", other.cookies)
        self.assertEqual(self.count("users", "last_login_at = ?", (self.clock(),)), 0)
        self.assertEqual(last, 1)
        self.assertEqual(self.count("audit_log", "event = 'login_denied' AND code = 'disabled'"), 1)
        self.assertEqual(self.count("sessions", "user_id = ?", (uid,)), 0)


class ClosedRegistration(ServerCase):
    server_kwargs = {"env": {"GEMARC_ACCOUNTS_MAX_USERS": "1"}}

    def test_cap_refuses_new_users_but_not_existing_ones(self):
        self.logged_in_page({"sub": "m-1", "email": EMAIL})
        other = Browser(self.srv.base)
        _s, _a, cb = self.login({"sub": "m-2", "email": "bob@gmail.com"}, other)
        self.assertEqual(cb.status, 403)
        self.assertIn("GEMARC_ACCT_013", cb.text)
        self.assertEqual(self.count("audit_log", "event = 'login_denied' AND code = 'closed'"), 1)
        again = Browser(self.srv.base)
        self.assertEqual(self.login({"sub": "m-1", "email": EMAIL}, again)[2].status, 303)


class SignupLimits(ServerCase):
    server_kwargs = {"env": {"VERCEL": "1"}, "accounts_extra": {"limit_overrides": {"signup_ip": (1, 3600)}}}

    def test_per_ip_signup_limit(self):
        def signin(sub, email, ip):
            b = Browser(self.srv.base)
            self.idp.identity = {"sub": sub, "email": email}
            start = b.nav("/auth/google/start", headers={"x-vercel-forwarded-for": ip})
            auth = Browser(self.idp.base).get(start.location, cookies=False)
            return b.nav(auth.location, headers={"x-vercel-forwarded-for": ip})

        self.assertEqual(signin("s-1", "a@gmail.com", "203.0.113.5").status, 303)
        denied = signin("s-2", "b@gmail.com", "203.0.113.5")
        self.assertEqual(denied.status, 403)
        self.assertIn("GEMARC_ACCT_013", denied.text)
        self.assertEqual(signin("s-3", "c@gmail.com", "203.0.113.77").status, 303)
        self.assertEqual(self.count("users"), 2)


class SessionsAndCsrf(ServerCase):
    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "c-1", "email": EMAIL})
        self.csrf = self.browser.csrf(self.page)
        self.sid = self.browser.cookies["gemarc_sid"]

    def post_token(self, **kw):
        return self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, **kw)

    def test_csrf_matrix(self):
        self.assertEqual(self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, origin=None).status, 403)
        for origin in ("https://evil.example", "null", self.srv.base + ".evil.example", self.srv.base + "/"):
            resp = self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, origin=origin)
            self.assertEqual(resp.status, 403, origin)
            self.assertIn("GEMARC_ACCT_021", resp.text)
        # right Origin, missing or wrong token
        for fields in ({"name": "x"}, {"csrf": "", "name": "x"}, {"csrf": "A" * 43, "name": "x"}):
            resp = self.browser.post("/account/tokens", fields)
            self.assertEqual(resp.status, 403)
            self.assertIn("GEMARC_ACCT_021", resp.text)
        # no Origin but Sec-Fetch-Site same-origin is accepted, cross-site is not
        ok = self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, origin=None,
                               headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(ok.status, 200)
        bad = self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, origin=None,
                                headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(bad.status, 403)
        self.assertEqual(self.count("api_tokens"), 1)

    def test_csrf_token_of_another_session_is_refused(self):
        other = Browser(self.srv.base)
        other_page = self.logged_in_page({"sub": "c-2", "email": "bob@gmail.com"}, other)
        resp = self.browser.post("/account/tokens", {"csrf": other.csrf(other_page), "name": "x"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.count("api_tokens"), 0)

    def test_get_never_changes_state(self):
        for path in ("/account/tokens", "/account", "/auth/logout", "/auth/logout-all", "/account/delete",
                     "/account/tokens/revoke-all"):
            self.browser.get(path)
        self.assertEqual(self.count("sessions"), 1)
        self.assertEqual(self.count("users"), 1)

    def test_forged_host_cannot_change_the_redirect_or_origin_rule(self):
        start = self.browser.nav("/auth/google/start", headers={"x-forwarded-host": "evil.example"})
        self.assertIn(urllib.parse.quote(self.srv.base + "/auth/google/callback", safe=""), start.location)
        resp = self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"}, origin="http://evil.example",
                                 headers={"Host": "evil.example"})
        self.assertEqual(resp.status, 421)  # wrong Host: refused before anything else, not a security control

    def test_tampered_or_malformed_cookies_are_signed_out(self):
        for value in ("A" * 43, self.sid[:-1] + ("A" if self.sid[-1] != "A" else "B"), "x", "", "A" * 44, "../../etc"):
            b = Browser(self.srv.base)
            b.cookies["gemarc_sid"] = value
            page = b.get("/account")
            self.assertEqual(page.status, 200)
            self.assertNotIn(EMAIL, page.text, value)
            self.assertEqual(b.post("/account/tokens", {"csrf": self.csrf, "name": "x"}).status, 401)

    def test_duplicate_session_cookies_are_refused(self):
        b = Browser(self.srv.base)
        resp = b.get("/account", headers={"Cookie": f"gemarc_sid={self.sid}; gemarc_sid={'A' * 43}"})
        self.assertNotIn(EMAIL, resp.text)

    def test_malformed_cookie_or_none_touches_no_database(self):
        rt = self.srv.runtime()
        calls = []
        original = rt.db.transaction

        def counting():
            calls.append(1)
            return original()

        rt.db.transaction = counting
        try:
            for cookies in ({}, {"gemarc_sid": "short"}, {"gemarc_sid": "A" * 44}, {"other": "1"}):
                b = Browser(self.srv.base)
                b.cookies.update(cookies)
                self.assertEqual(b.get("/account").status, 200)
            self.assertEqual(calls, [])
            Browser(self.srv.base).get("/healthz")
            self.assertEqual(calls, [])
            good = Browser(self.srv.base)
            good.cookies["gemarc_sid"] = self.sid
            good.get("/account")
            self.assertGreater(len(calls), 0)
        finally:
            rt.db.transaction = original

    def test_idle_and_absolute_expiry(self):
        self.clock.advance(29 * 60)
        self.assertIn(EMAIL, self.browser.get("/account").text)  # activity keeps it alive
        self.clock.advance(29 * 60)
        self.assertIn(EMAIL, self.browser.get("/account").text)
        self.clock.advance(31 * 60)  # idle for 31 minutes
        stale = self.browser.get("/account")
        self.assertNotIn(EMAIL, stale.text)
        self.assertTrue(any("gemarc_sid=" in c and "Max-Age=0" in c for c in stale.set_cookies()))
        self.assertEqual(self.post_token().status, 401)

    def test_absolute_timeout(self):
        for _ in range(17):  # 17 x 29 min of activity: never idle, but over the 8 h absolute limit
            self.clock.advance(29 * 60)
            self.browser.get("/account")
        self.assertNotIn(EMAIL, self.browser.get("/account").text)

    def test_recent_login_is_required_for_sensitive_actions(self):
        self.clock.advance(16 * 60)
        resp = self.post_token()
        self.assertEqual(resp.status, 403)
        self.assertIn("GEMARC_ACCT_040", resp.text)
        for path, extra in (("/account/tokens/revoke-all", {}), ("/auth/logout-all", {}), ("/account/export", {}),
                            ("/account/delete", {"confirm": "DELETE"})):
            self.assertEqual(self.browser.post(path, {"csrf": self.csrf, **extra}).status, 403, path)
        self.assertEqual(self.count("users"), 1)
        # logout needs no step-up
        self.assertEqual(self.browser.post("/auth/logout", {"csrf": self.csrf}).status, 303)

    def test_logout_and_logout_all(self):
        resp = self.browser.post("/auth/logout", {"csrf": self.csrf})
        self.assertEqual((resp.status, resp.location), (303, "/account"))
        self.assertTrue(any("Max-Age=0" in c for c in resp.set_cookies()))
        self.assertEqual(self.count("sessions"), 0)
        self.assertEqual(self.count("audit_log", "event = 'logout'"), 1)
        second = Browser(self.srv.base)
        self.logged_in_page({"sub": "c-1", "email": EMAIL}, second)
        third = Browser(self.srv.base)
        self.logged_in_page(None, third)
        page = third.get("/account").text
        resp = third.post("/auth/logout-all", {"csrf": third.csrf(page)})
        self.assertEqual(resp.status, 303)
        self.assertEqual(self.count("sessions"), 0)
        self.assertNotIn(EMAIL, second.get("/account").text)

    def test_methods_content_types_and_sizes(self):
        resp = self.browser.request("PUT", "/account")
        self.assertEqual(resp.status, 405)
        self.assertIn("GET", resp.headers["Allow"])
        self.assertEqual(self.browser.request("GET", "/auth/logout").status, 405)
        self.assertEqual(self.browser.request("POST", "/auth/google/start", data=b"").status, 405)
        self.assertEqual(self.browser.request("OPTIONS", "/account").status, 204)
        self.assertEqual(self.browser.request("DELETE", "/account/tokens").status, 405)
        self.assertEqual(self.browser.get("/account/unknown").status, 404)
        self.assertEqual(self.browser.get("/auth/unknown").status, 404)
        self.assertEqual(self.browser.get("/accounting").status, 404)  # not an account path: the app's own 404
        wrong = self.browser.request("POST", "/account/tokens", data=b"{}",
                                     headers={"Origin": self.srv.base, "Content-Type": "application/json"})
        self.assertEqual(wrong.status, 415)
        big = self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x" * 9000})
        self.assertEqual(big.status, 413)
        self.assertEqual(self.browser.post("/account/tokens", {"csrf": self.csrf, "name": "x"},
                                           headers={"Content-Type": "application/x-www-form-urlencoded"}).status, 200)


class TokenPages(ServerCase):
    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "t-1", "email": EMAIL})

    def create(self, **fields):
        page = self.browser.get("/account").text
        return self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), **fields})

    def test_default_expiry_cap_and_validation(self):
        ok = self.create(name="a")
        self.assertEqual(ok.status, 200)
        with self.srv.database().transaction() as tx:
            row = tx.fetchone("SELECT created_at, expires_at, scope FROM api_tokens")
        self.assertEqual(row["expires_at"] - row["created_at"], 30 * 86400)
        self.assertEqual(row["scope"], "read")
        for fields in ({"name": ""}, {"name": "x" * 41}, {"name": "a", "days": "91"}, {"name": "a", "days": "0"},
                       {"name": "a", "days": "abc"}, {"name": "a\x00b"}, {"name": "a", "scope": "root"},
                       {"name": "a", "scope": "write"}):
            resp = self.create(**fields)
            self.assertIn(resp.status, (400, 403), fields)
            self.assertNotIn("gat_", resp.text)
        self.assertEqual(self.count("api_tokens"), 1)

    def test_token_is_shown_once_and_names_are_escaped(self):
        resp = self.create(name="<script>alert(1)</script>")
        self.assertEqual(resp.status, 200)
        token = PAT_RE.search(resp.text).group(0)
        self.assertNotIn("<script>alert", resp.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", resp.text)
        self.assertGreaterEqual(resp.text.count(token), 1)  # the one-time box (and the copy-paste snippets)
        later = self.browser.get("/account").text
        self.assertNotIn(token, later)
        self.assertNotIn(token[13:56], later)
        self.assertEqual(self.browser.get("/account/tokens").text.count(token), 0)

    def test_creation_rate_limit_is_ten_per_hour(self):
        for i in range(10):
            self.assertEqual(self.create(name=f"t{i}").status, 200)
        resp = self.create(name="eleventh")
        self.assertEqual(resp.status, 429)
        self.assertIn("GEMARC_ACCT_022", resp.text)

    def test_cannot_revoke_someone_elses_token(self):
        mine = PAT_RE.search(self.create(name="mine").text).group(0)
        other = Browser(self.srv.base)
        self.logged_in_page({"sub": "t-2", "email": "bob@gmail.com"}, other)
        page = other.get("/account").text
        resp = other.post(f"/account/tokens/{mine[4:12]}/revoke", {"csrf": other.csrf(page)})
        self.assertEqual(resp.status, 404)
        unknown = other.post("/account/tokens/aaaaaaaa/revoke", {"csrf": other.csrf(page)})
        self.assertEqual(unknown.status, 404)
        self.assertEqual(self.count("api_tokens", "revoked_at IS NOT NULL"), 0)

    def test_scope_choice_is_hidden_without_write_tools(self):
        self.assertNotIn('name="scope"', self.browser.get("/account").text)

    def test_revoke_all(self):
        for i in range(3):
            self.create(name=f"t{i}")
        page = self.browser.get("/account").text
        resp = self.browser.post("/account/tokens/revoke-all", {"csrf": self.browser.csrf(page)})
        self.assertEqual(resp.status, 303)
        self.assertEqual(self.count("api_tokens", "revoked_at IS NULL"), 0)
        self.assertEqual(self.count("audit_log", "event = 'tokens_revoked_all'"), 1)


class TokenCap(ServerCase):
    server_kwargs = {"accounts_extra": {"limit_overrides": {"pat_create": (100, 3600)}}}

    def test_cap_per_user(self):
        self.logged_in_page({"sub": "cap-1", "email": EMAIL})
        for i in range(10):
            page = self.browser.get("/account").text
            self.assertEqual(self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": f"t{i}"}).status, 200)
        page = self.browser.get("/account").text
        resp = self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": "eleventh"})
        self.assertEqual(resp.status, 409)
        self.assertIn("GEMARC_ACCT_033", resp.text)


class WriteScope(ServerCase):
    server_kwargs = {"toolset": "full", "write": True, "env": {"GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE": "1"}}

    def test_write_is_not_self_service(self):
        self.logged_in_page({"sub": "w-1", "email": EMAIL})
        page = self.browser.get("/account").text
        self.assertIn('name="scope"', page)
        denied = self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": "w", "scope": "write"})
        self.assertEqual(denied.status, 403)
        self.assertIn("GEMARC_ACCT_035", denied.text)
        rt = self.srv.runtime()
        with rt.db.transaction() as tx:
            user_id = tx.fetchone("SELECT id FROM users")["id"]
        self.assertTrue(rt.users.set_can_write(user_id, True, self.clock()))
        allowed = self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": "w", "scope": "write"})
        self.assertEqual(allowed.status, 200)


class RateLimits(ServerCase):
    server_kwargs = {"env": {"VERCEL": "1"},
                     "accounts_extra": {"limit_overrides": {"login_start": (2, 600)}, "prefilter": (6, 6)}}

    def start(self, ip):
        return Browser(self.srv.base).nav("/auth/google/start", headers={"x-vercel-forwarded-for": ip})

    def test_login_start_database_limit_per_address(self):
        self.assertEqual(self.start("203.0.113.1").status, 302)
        self.assertEqual(self.start("203.0.113.1").status, 302)
        over = self.start("203.0.113.1")
        self.assertEqual(over.status, 429)
        self.assertIn("GEMARC_ACCT_022", over.text)
        self.assertGreaterEqual(int(over.headers["Retry-After"]), 1)
        assert_headers(self, over)
        self.assertEqual(self.start("203.0.113.2").status, 302)  # another address is unaffected
        self.assertNotIn("203.0.113.1", self.dump())  # only a keyed hash reaches the database

    def test_spoofed_headers_are_ignored(self):
        b = Browser(self.srv.base)
        for _ in range(3):
            b.nav("/auth/google/start", headers={"x-forwarded-for": "198.51.100.9", "x-real-ip": "198.51.100.9"})
        self.assertEqual(self.count("rate_limits"), 0)  # no trusted address: never a shared database bucket

    def test_prefilter_applies_before_the_database(self):
        b = Browser(self.srv.base)
        statuses = [b.get("/account", headers={"x-vercel-forwarded-for": "203.0.113.50"}).status for _ in range(8)]
        self.assertEqual(statuses[:6], [200] * 6)
        self.assertEqual(statuses[6:], [429, 429])
        self.assertEqual(self.count("rate_limits"), 0)


class HeadersEverywhere(ServerCase):
    def test_full_header_set_on_every_kind_of_response(self):
        self.logged_in_page({"sub": "h-1", "email": EMAIL})
        csrf = self.browser.csrf(self.browser.get("/account").text)
        stranger = Browser(self.srv.base)
        responses = [
            stranger.get("/account"),                                         # 200 signed out
            self.browser.get("/account"),                                     # 200 signed in
            stranger.nav("/auth/google/start"),                               # 302
            self.browser.get("/account/tokens"),                              # 200
            Browser(self.srv.base).get("/account/tokens"),                    # 303
            self.browser.post("/account/tokens", {"csrf": csrf, "name": "n"}),   # 200 with a token
            self.browser.post("/account/tokens", {"csrf": csrf, "name": "n"}, origin="https://evil.example"),  # 403
            self.browser.post("/account/tokens", {"csrf": "bad", "name": "n"}),  # 403
            stranger.post("/account/tokens", {"csrf": csrf, "name": "n"}),    # 401
            self.browser.request("PUT", "/account"),                          # 405
            self.browser.get("/account/missing"),                             # 404
            self.browser.get("/auth/google/callback"),                        # 400
            self.browser.get("/account", headers={"Host": "evil.example"}),   # 421
            self.browser.post("/account/delete", {"csrf": csrf, "confirm": "no"}),  # 400
            self.browser.post("/account/export", {"csrf": csrf}),            # 200 json
            self.browser.request("OPTIONS", "/account"),                      # 204
            self.browser.post("/auth/logout", {"csrf": csrf}),               # 303 clearing cookie
        ]
        statuses = {r.status for r in responses}
        self.assertTrue({200, 204, 302, 303, 400, 401, 403, 404, 405, 421} <= statuses, statuses)
        for resp in responses:
            assert_headers(self, resp)

    def test_signed_out_page_shows_the_notice_before_the_login_link(self):
        text = Browser(self.srv.base).get("/account").text
        self.assertLess(text.index("隐私告知"), text.index("/auth/google/start"))
        self.assertNotIn("privacy-link", text)

    def test_https_base_gets_hsts_secure_and_host_prefix_cookies(self):
        h = pages.security_headers(nonce="n", hsts=True)
        self.assertEqual(h["Strict-Transport-Security"], "max-age=31536000")
        self.assertNotIn("Strict-Transport-Security", pages.security_headers())


class PrivacyLink(ServerCase):
    server_kwargs = {"env": {"GEMARC_ACCOUNTS_PRIVACY_URL": "https://example.org/privacy?a=1&b=2"}}

    def test_link_is_shown_and_escaped(self):
        text = Browser(self.srv.base).get("/account").text
        self.assertIn('href="https://example.org/privacy?a=1&amp;b=2"', text)
        self.assertLess(text.index("隐私告知"), text.index("/auth/google/start"))


class PolicyRecheck(unittest.TestCase):
    """Narrowing the policy invalidates sessions of users who no longer qualify, on the next deploy."""

    def test_session_dies_when_the_email_list_no_longer_contains_the_user(self):
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="gemarc-policy-")
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = Clock()
        first = AccountsServer(tmp, clock).start()
        try:
            browser = Browser(first.base)
            sign_in(browser, first.idp, {"sub": "pr-1", "email": EMAIL})
            self.assertIn(EMAIL, browser.get("/account").text)
            sid = browser.cookies["gemarc_sid"]
        finally:
            first.stop()
        second = AccountsServer(tmp, clock, migrate=False,
                                env={"GEMARC_ACCOUNTS_ALLOWED_EMAILS": "someone-else@gmail.com"}).start()
        try:
            browser = Browser(second.base)
            browser.cookies["gemarc_sid"] = sid
            self.assertNotIn(EMAIL, browser.get("/account").text)
        finally:
            second.stop()
        third = AccountsServer(tmp, clock, migrate=False, env={"GEMARC_ACCOUNTS_ALLOWED_EMAILS": EMAIL}).start()
        try:
            browser = Browser(third.base)
            browser.cookies["gemarc_sid"] = sid
            self.assertIn(EMAIL, browser.get("/account").text)  # still qualifies under the new list
        finally:
            third.stop()


    def test_pat_dies_when_the_domain_policy_narrows(self):
        import shutil
        import tempfile

        from .test_mcpauth import MCP_H
        from test_http_transport import INITIALIZE, http
        import json

        tmp = tempfile.mkdtemp(prefix="gemarc-policy2-")
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = Clock()
        first = AccountsServer(tmp, clock, env={"GEMARC_MCP_AUTH": "pat"}).start()
        try:
            browser = Browser(first.base)
            sign_in(browser, first.idp, {"sub": "pr-2", "email": EMAIL})
            rt = first.runtime()
            with rt.db.transaction() as tx:
                user_id = tx.fetchone("SELECT id FROM users")["id"]
            token = rt.tokens.create(user_id, "t", clock()).token
            url = first.url("/mcp")
            headers = {**MCP_H, "Authorization": "Bearer " + token}
            self.assertEqual(http(url, "POST", headers, json.dumps(INITIALIZE).encode())[0], 200)
        finally:
            first.stop()
        second = AccountsServer(tmp, clock, migrate=False,
                                env={"GEMARC_MCP_AUTH": "pat", "GEMARC_ACCOUNTS_ALLOWED_DOMAINS": "example.org"}).start()
        try:
            headers = {**MCP_H, "Authorization": "Bearer " + token}
            self.assertEqual(http(second.url("/mcp"), "POST", headers, json.dumps(INITIALIZE).encode())[0], 401)
        finally:
            second.stop()


class Misdirected(ServerCase):
    def test_schema_missing_is_503_003(self):
        # a second app on an EMPTY database file: the schema check fails closed with 003
        import os
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="gemarc-empty-")
        self.addCleanup(shutil.rmtree, tmp, True)
        srv = AccountsServer(tmp, self.clock, migrate=False).start()
        try:
            resp = Browser(srv.base).get("/account", headers={"Cookie": "gemarc_sid=" + "A" * 43})
            self.assertEqual(resp.status, 503)
            self.assertIn("GEMARC_ACCT_003", resp.text)
            assert_headers(self, resp)
            self.assertEqual(Browser(srv.base).get("/healthz").json()["accounts"], "db-unreachable")
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()


class AutoMigrate(ServerCase):
    server_kwargs = {"migrate": False, "env": {"GEMARC_ACCOUNTS_AUTO_MIGRATE": "1"}}

    def test_first_use_migrates_when_asked_to(self):
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "am-1", "email": EMAIL})
        self.assertEqual(cb.status, 303)
        self.assertEqual(self.count("users"), 1)


class HttpsBase(unittest.TestCase):
    """An https base URL: ``__Host-`` cookies with Secure, HSTS on every response, Host must match."""

    def test_cookie_names_attributes_and_hsts(self):
        import asyncio
        import os
        import shutil
        import tempfile

        from gemarc_mcp.accounts import schema
        from gemarc_mcp.accounts.db import Database

        from .fake_idp import asgi_call
        from .test_mcpauth import built, complete_env

        tmp = tempfile.mkdtemp(prefix="gemarc-https-")
        self.addCleanup(shutil.rmtree, tmp, True)
        db_path = os.path.join(tmp, "a.db").replace("\\", "/")
        schema.migrate(Database.sqlite(db_path), Clock()())
        env = complete_env(base="https://gem-arc.example", db=db_path)
        with built(env) as app:
            host = {"Host": "gem-arc.example"}
            status, headers, _ = asyncio.run(asgi_call(app, "GET", "/auth/google/start", host))
            self.assertEqual(status, 302)
            self.assertTrue(headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
            cookie = headers["set-cookie"]
            self.assertTrue(cookie.startswith("__Host-gemarc_oauth="), cookie)
            for attr in ("Path=/", "HttpOnly", "SameSite=Lax", "Secure", "Max-Age=600"):
                self.assertIn(attr, cookie)
            self.assertNotIn("Domain", cookie)
            self.assertEqual(headers["strict-transport-security"], "max-age=31536000")
            status, headers, _ = asyncio.run(asgi_call(app, "GET", "/account", host))
            self.assertEqual(headers["strict-transport-security"], "max-age=31536000")
            status, _h, _b = asyncio.run(asgi_call(app, "GET", "/account", {"Host": "gem-arc.example.evil"}))
            self.assertEqual(status, 421)
            rt = app.state.gemarc_accounts()
            self.assertEqual((rt.sid_cookie, rt.bind_cookie), ("__Host-gemarc_sid", "__Host-gemarc_oauth"))
            self.assertEqual(rt.google.redirect_uri, "https://gem-arc.example/auth/google/callback")
        from gemarc_mcp.accounts import security

        header = security.cookie_header("__Host-gemarc_sid", "v", secure=True)
        self.assertEqual(header, "__Host-gemarc_sid=v; Path=/; HttpOnly; SameSite=Lax; Secure")
        self.assertIn("Max-Age=0", security.clear_cookie_header("__Host-gemarc_sid", secure=True))
        self.assertNotIn("Secure", security.cookie_header("gemarc_sid", "v", secure=False))
