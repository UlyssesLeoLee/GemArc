"""Google OIDC pieces: ID-token claim checklist, access policy, hardened token client, signature mode, state store."""
from __future__ import annotations

import json
import ssl
import time
import unittest
import urllib.parse
from pathlib import Path

from gemarc_mcp.accounts import oidc
from gemarc_mcp.accounts.crypto import b64u, sha256_domain
from gemarc_mcp.accounts.oidc import (AccessPolicy, ClaimsError, GoogleClient, IdpEndpoints, IdpError, OAuthStates,
                                      validate_id_token)

from .fake_idp import CLIENT_ID, CLIENT_SECRET, GONE, Browser, FakeIdp, ServerCase, sign_in
from .support import KEY1, KEY2, T0, Clock, DbCase, make_crypto

NOW = 1_900_000_000
NONCE = "nonce-value-for-tests"
NONCE_HASH = sha256_domain("nonce", NONCE)
BASE_CLAIMS = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": "1234567890",
               "email": "Alice@Gmail.com", "email_verified": True, "nonce": NONCE, "iat": NOW, "exp": NOW + 3600}


def token_for(claims: dict, raw: "str | None" = None) -> str:
    payload = raw if raw is not None else json.dumps(claims)
    return ".".join([b64u(b'{"alg":"RS256","kid":"x"}'), b64u(payload.encode()), b64u(b"signature")])


def check(**changes):
    claims = dict(BASE_CLAIMS)
    for key, value in changes.items():
        if value is GONE:
            claims.pop(key, None)
        else:
            claims[key] = value
    return validate_id_token(token_for(claims), client_id=CLIENT_ID, nonce_hash=NONCE_HASH, now=NOW)


class ClaimsChecklist(unittest.TestCase):
    def test_a_good_token_is_accepted_and_the_email_normalised(self):
        claims = check()
        self.assertEqual((claims.sub, claims.email, claims.hd), ("1234567890", "alice@gmail.com", None))

    def test_issuer(self):
        self.assertEqual(check(iss="accounts.google.com").sub, "1234567890")
        for bad in ("https://accounts.google.com/", "http://accounts.google.com", "https://evil.example", None, 5, GONE):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(iss=bad)

    def test_audience_and_authorized_party(self):
        for bad in ("someone-else", ["someone-else"], [], 5, GONE, [CLIENT_ID, 3]):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(aud=bad)
        self.assertEqual(check(aud=[CLIENT_ID, "other"], azp=CLIENT_ID).sub, "1234567890")
        for azp in (GONE, "other"):
            with self.assertRaises(ClaimsError):
                check(aud=[CLIENT_ID, "other"], azp=azp)  # a list of audiences needs azp == client id
        with self.assertRaises(ClaimsError):
            check(azp="other")
        self.assertEqual(check(azp=GONE).sub, "1234567890")

    def test_expiry_boundary(self):
        self.assertEqual(check(exp=NOW - 59).sub, "1234567890")
        for bad in (NOW - 60, NOW - 3600, "9999999999", None, True, GONE, float("nan")):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(exp=bad)

    def test_issued_at_window(self):
        self.assertEqual(check(iat=NOW + 60).sub, "1234567890")
        self.assertEqual(check(iat=NOW - 600).sub, "1234567890")
        for bad in (NOW + 61, NOW - 601, "1", None, False, GONE):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(iat=bad)

    def test_nonce(self):
        for bad in ("other", "", None, 5, GONE):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(nonce=bad)

    def test_subject(self):
        for bad in ("", "x" * 256, "sub-é", 5, None, GONE):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(sub=bad)
        self.assertEqual(check(sub="x" * 255).sub, "x" * 255)

    def test_email(self):
        for bad in ("nobody", "a@b@gmail.com", "@gmail.com", "a@", "a b@gmail.com", "", None, 5, GONE, ["a@gmail.com"]):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(email=bad)

    def test_email_verified_must_be_the_boolean_true(self):
        for bad in (False, "true", "True", 1, None, GONE):
            with self.assertRaises(ClaimsError, msg=repr(bad)):
                check(email_verified=bad)

    def test_hd_is_kept_only_in_memory(self):
        claims = check(hd="Example.ORG")
        self.assertEqual(claims.hd, "example.org")
        self.assertTrue(claims.has_hd)
        with self.assertRaises(ClaimsError):
            check(hd=5)

    def test_structure_and_duplicate_keys(self):
        good = token_for(BASE_CLAIMS)
        dup = token_for({}, raw=json.dumps(BASE_CLAIMS)[:-1] + ', "sub": "other"}')
        for token in ("", "a.b", "a.b.c.d", "a..c", ".b.c", good.replace(".", "~"), dup,
                      token_for({}, raw="[1,2]"), token_for({}, raw='"string"'), token_for({}, raw="{bad json"),
                      token_for({}, raw='{"exp": NaN}'), None, 5):
            with self.assertRaises(ClaimsError, msg=repr(token)[:40]):
                validate_id_token(token, client_id=CLIENT_ID, nonce_hash=NONCE_HASH, now=NOW)

    def test_the_unsigned_header_is_not_parsed_when_signatures_are_off(self):
        token = ".".join(["!!!not-base64!!!", b64u(json.dumps(BASE_CLAIMS).encode()), "sig"])
        self.assertEqual(validate_id_token(token, client_id=CLIENT_ID, nonce_hash=NONCE_HASH, now=NOW).sub, "1234567890")


class Policy(unittest.TestCase):
    def setUp(self):
        self.crypto = make_crypto()

    def claims(self, email, hd=None):
        return oidc.Claims("s", email, hd)

    def test_default_gmail_policy(self):
        policy = AccessPolicy(("gmail.com",), (), self.crypto)
        self.assertIsNone(policy.check(self.claims("a@gmail.com")))
        self.assertEqual(policy.check(self.claims("a@gmail.com", "example.org")), "domain")  # consumer rule: no hd
        for email in ("a@evilgmail.com", "a@gmail.com.evil.com", "a@sub.gmail.com", "a@googlemail.com", "a@example.org"):
            self.assertEqual(policy.check(self.claims(email)), "domain", email)

    def test_workspace_domain_needs_a_matching_hd(self):
        policy = AccessPolicy(("example.org",), (), self.crypto)
        self.assertIsNone(policy.check(self.claims("a@example.org", "example.org")))
        for hd in (None, "other.org"):
            self.assertEqual(policy.check(self.claims("a@example.org", hd)), "domain")
        self.assertEqual(policy.check(self.claims("a@gmail.com")), "domain")

    def test_email_list_is_exact_without_dot_or_plus_folding(self):
        policy = AccessPolicy(("gmail.com",), ("john.doe@gmail.com",), self.crypto)
        self.assertIsNone(policy.check(self.claims("john.doe@gmail.com")))
        for email in ("johndoe@gmail.com", "john.doe+x@gmail.com", "other@gmail.com"):
            self.assertEqual(policy.check(self.claims(email)), "email_list", email)

    def test_row_recheck_uses_blind_indexes_and_survives_a_key_rotation(self):
        old = make_crypto()
        row = {"domain_bidx": old.blind_index("domain", "gmail.com"), "email_bidx": old.blind_index("email", "a@gmail.com"),
               "has_hd": 0, "bidx_kid": "k1"}
        rotated = make_crypto(("k2", KEY2), ("k1", KEY1))
        for crypto in (old, rotated):
            self.assertTrue(AccessPolicy(("gmail.com",), (), crypto).row_ok(row))
            self.assertTrue(AccessPolicy(("gmail.com",), ("a@gmail.com",), crypto).row_ok(row))
            self.assertFalse(AccessPolicy(("gmail.com",), ("b@gmail.com",), crypto).row_ok(row))
            self.assertFalse(AccessPolicy(("example.org",), (), crypto).row_ok(row))
            self.assertFalse(AccessPolicy(("gmail.com",), (), crypto).row_ok({**row, "has_hd": 1}))
        ws = {"domain_bidx": old.blind_index("domain", "example.org"), "email_bidx": "x", "has_hd": 1}
        self.assertTrue(AccessPolicy(("example.org",), (), old).row_ok(ws))
        self.assertFalse(AccessPolicy(("example.org",), (), old).row_ok({**ws, "has_hd": 0}))


class Endpoints(unittest.TestCase):
    def test_only_google_or_loopback_http(self):
        IdpEndpoints.google()
        IdpEndpoints("http://127.0.0.1:9/a", "http://localhost:9/t", "http://[::1]:9/j")
        for url in ("https://evil.example/token", "http://evil.example/token", "http://127.0.0.1.evil.example/t",
                    "https://oauth2.googleapis.com/token2", "ftp://127.0.0.1/t", "file:///etc/passwd"):
            with self.assertRaises(ValueError, msg=url):
                IdpEndpoints(token_url=url)

    def test_no_environment_variable_can_move_the_endpoints(self):
        source = Path(oidc.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|import os\b")
        self.assertEqual(IdpEndpoints.google().token_url, "https://oauth2.googleapis.com/token")

    def test_the_app_factory_defaults_to_google(self):
        from gemarc_mcp.accounts import config as accounts_config
        from gemarc_mcp.accounts.routes import AccountsRuntime

        env = {"GEMARC_ACCOUNTS": "1", "GEMARC_ACCOUNTS_BASE_URL": "http://127.0.0.1:1", "GEMARC_GOOGLE_CLIENT_ID": "c.id",
               "GEMARC_GOOGLE_CLIENT_SECRET": "secret-xyz", "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(KEY1),
               "GEMARC_ACCOUNTS_DATABASE_URL": "sqlite:///x.db", "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1",
               "GEMARC_IDP_TOKEN_URL": "http://127.0.0.1:2/t", "GOOGLE_TOKEN_URL": "http://127.0.0.1:2/t"}
        result = accounts_config.load(env)
        self.assertTrue(result.ok, result.describe())
        runtime = AccountsRuntime(result.config)
        self.assertEqual(runtime.google.endpoints, IdpEndpoints.google())

    def test_authorization_url_is_exactly_the_designed_request(self):
        url = oidc.authorization_url(IdpEndpoints.google(), client_id="cid", redirect_uri="https://h/auth/google/callback",
                                     state="S", nonce="N", challenge="C")
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", oidc.GOOGLE_AUTHORIZE_URL)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        self.assertEqual(query, {"response_type": "code", "client_id": "cid", "redirect_uri": "https://h/auth/google/callback",
                                 "scope": "openid email", "state": "S", "nonce": "N", "code_challenge": "C",
                                 "code_challenge_method": "S256", "prompt": "select_account"})

    def test_pkce_derivation(self):
        verifier = oidc.derive_verifier("state", "binding")
        self.assertRegex(verifier, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(verifier, oidc.derive_verifier("state", "binding"))
        self.assertNotEqual(verifier, oidc.derive_verifier("state", "other"))
        self.assertNotEqual(verifier, oidc.derive_verifier("other", "binding"))
        import base64
        import hashlib
        self.assertEqual(oidc.code_challenge(verifier),
                         base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode())


class HardenedClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clock = Clock(NOW)
        cls.idp = FakeIdp("http://127.0.0.1:1/cb", cls.clock).start()

    @classmethod
    def tearDownClass(cls):
        cls.idp.stop()

    def setUp(self):
        self.idp.faults.clear()
        self.idp.identity = {"sub": "u1", "email": "a@gmail.com"}

    def client(self, **kw):
        return GoogleClient(self.idp.endpoints, CLIENT_ID, CLIENT_SECRET, self.idp.redirect_uri, clock=self.clock, **kw)

    def code(self, verifier="v" * 43, nonce=NONCE):
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": self.idp.redirect_uri, "scope": "openid email",
            "state": "s" * 43, "nonce": nonce, "code_challenge": oidc.code_challenge(verifier),
            "code_challenge_method": "S256", "prompt": "select_account"})
        resp = Browser(self.idp.base).get(self.idp.base + "/authorize?" + query, cookies=False)
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(resp.location).query))["code"]

    def test_tls_context_verifies_chain_and_host_name(self):
        ctx = oidc.build_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_opener_has_no_proxy_and_a_redirect_handler_that_raises(self):
        opener = oidc.build_opener()
        names = {type(h).__name__ for h in opener.handlers}
        self.assertIn("_NoRedirect", names)
        self.assertNotIn("HTTPRedirectHandler", names)
        # an explicit empty ProxyHandler replaces urllib's default one, which would read the *_proxy variables
        self.assertTrue(all(not getattr(h, "proxies", None) for h in opener.handlers))

    def test_exchange_returns_only_the_id_token(self):
        verifier = "q" * 43
        token = self.client().exchange_code(self.code(verifier), verifier)
        self.assertEqual(token.count("."), 2)
        sent = self.idp.token_calls[-1]
        self.assertEqual(sent["code_verifier"], verifier)
        self.assertEqual(sent["grant_type"], "authorization_code")

    def test_every_failure_is_an_idp_error_with_a_slug_only(self):
        cases = [({"token_status": 500}, None), ({"token_ctype": "text/html"}, None), ({"token_type": "mac"}, None),
                 ({"big": True}, None), ({"no_id_token": True}, None), ({"token_redirect": True}, None),
                 ({"token_body": b"{not json"}, None)]
        for fault, _ in cases:
            self.idp.faults.clear()
            self.idp.faults.update(fault)
            with self.assertRaises(IdpError) as ctx:
                self.client().exchange_code(self.code(), "v" * 43)
            self.assertRegex(ctx.exception.reason, r"^[a-z0-9_]+$")
            self.assertNotIn("server_error", str(ctx.exception))

    def test_wrong_secret_wrong_verifier_and_reused_code(self):
        bad = GoogleClient(self.idp.endpoints, CLIENT_ID, "wrong", self.idp.redirect_uri)
        with self.assertRaises(IdpError):
            bad.exchange_code(self.code(), "v" * 43)
        with self.assertRaises(IdpError):
            self.client().exchange_code(self.code("a" * 43), "b" * 43)
        code = self.code()
        self.client().exchange_code(code, "v" * 43)
        with self.assertRaises(IdpError):
            self.client().exchange_code(code, "v" * 43)

    def test_redirect_uri_that_differs_from_the_registered_one_is_refused(self):
        code = self.code()
        other = GoogleClient(self.idp.endpoints, CLIENT_ID, CLIENT_SECRET, "http://127.0.0.1:1/other", clock=self.clock)
        with self.assertRaises(IdpError):
            other.exchange_code(code, "v" * 43)

    def test_timeout(self):
        self.idp.faults["token_delay"] = 1.5
        started = time.time()
        with self.assertRaises(IdpError):
            self.client(timeout=0.3).exchange_code(self.code(), "v" * 43)
        self.assertLess(time.time() - started, 1.2)


class SignatureMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clock = Clock(NOW)
        cls.idp = FakeIdp("http://127.0.0.1:1/cb", cls.clock).start()

    @classmethod
    def tearDownClass(cls):
        cls.idp.stop()

    def setUp(self):
        self.idp.faults.clear()
        self.idp.jwks_hits = 0
        self.client = GoogleClient(self.idp.endpoints, CLIENT_ID, CLIENT_SECRET, self.idp.redirect_uri, clock=self.clock)

    def token(self, **faults):
        self.idp.faults.clear()
        self.idp.faults.update({"sign": "good", **faults})
        return self.idp._id_token({"identity": {"sub": "u1", "email": "a@gmail.com"}, "nonce": NONCE})

    def verify(self, token, verify_sig=True):
        return validate_id_token(token, client_id=CLIENT_ID, nonce_hash=NONCE_HASH, now=self.clock(), google=self.client,
                                 verify_sig=verify_sig)

    def test_correct_signature_passes_and_the_jwks_is_cached(self):
        self.assertEqual(self.verify(self.token()).sub, "u1")
        self.assertEqual(self.verify(self.token()).sub, "u1")
        self.assertEqual(self.idp.jwks_hits, 1)

    def test_default_mode_never_fetches_the_jwks(self):
        self.assertEqual(self.verify(self.token(), verify_sig=False).sub, "u1")
        self.assertEqual(self.idp.jwks_hits, 0)

    def test_wrong_key_wrong_alg_unknown_kid_and_unreachable_jwks_are_denied(self):
        for faults in ({"sign": "other"}, {"alg": "HS256"}, {"alg": "none"}, {"kid": "kid-unknown"}):
            with self.assertRaises(ClaimsError, msg=str(faults)):
                self.verify(self.token(**faults))
        self.client = GoogleClient(self.idp.endpoints, CLIENT_ID, CLIENT_SECRET, self.idp.redirect_uri, clock=self.clock)
        self.idp.faults["jwks_fail"] = True
        with self.assertRaises(ClaimsError):
            self.verify(self.token(jwks_fail=True))

    def test_a_tampered_payload_fails_the_signature(self):
        head, payload, sig = self.token().split(".")
        forged = b64u(json.dumps({**BASE_CLAIMS, "sub": "attacker", "iat": self.clock(), "exp": self.clock() + 99}).encode())
        with self.assertRaises(ClaimsError):
            self.verify(".".join([head, forged, sig]))

    def test_unknown_kid_refetch_is_throttled_to_once_a_minute(self):
        self.verify(self.token())
        self.assertEqual(self.idp.jwks_hits, 1)
        for _ in range(3):
            with self.assertRaises(ClaimsError):
                self.verify(self.token(kid="kid-unknown"))
        self.assertEqual(self.idp.jwks_hits, 1)
        self.clock.advance(61)
        with self.assertRaises(ClaimsError):
            self.verify(self.token(kid="kid-unknown"))
        self.assertEqual(self.idp.jwks_hits, 2)
        self.clock.now = NOW


class SignatureThroughTheApp(ServerCase):
    server_kwargs = {"env": {"GEMARC_ACCOUNTS_VERIFY_SIG": "1"}}

    def test_signed_token_logs_in_and_unsigned_or_wrong_key_is_013(self):
        self.idp.faults["sign"] = "good"
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "v-1", "email": "alice@gmail.com"})
        self.assertEqual(cb.status, 303)
        for mode in ("none", "other"):
            self.idp.faults["sign"] = mode
            b = Browser(self.srv.base)
            _s, _a, cb = sign_in(b, self.idp, {"sub": "v-2", "email": "bob@gmail.com"})
            self.assertEqual(cb.status, 403, mode)
            self.assertIn("GEMARC_ACCT_013", cb.text)
        self.assertEqual(self.count("users"), 1)


class DefaultModeThroughTheApp(ServerCase):
    def test_default_flow_never_touches_the_jwks(self):
        _s, _a, cb = sign_in(self.browser, self.idp, {"sub": "d-1", "email": "alice@gmail.com"})
        self.assertEqual(cb.status, 303)
        self.assertEqual(self.idp.jwks_hits, 0)
        for _ in range(2):
            self.assertEqual(self.idp.violations, [])


class StateStore(DbCase):
    def test_one_time_use_ttl_and_cleanup(self):
        states = OAuthStates(self.db, ttl=600)
        states.create("s1", "n1", "b1", T0)
        record = states.consume("s1", T0 + 10)
        self.assertEqual(record.nonce_hash, sha256_domain("nonce", "n1"))
        self.assertEqual(record.binding_hash, oidc.binding_hash("s1", "b1"))
        self.assertIsNone(states.consume("s1", T0 + 11))  # one time
        states.create("s2", "n2", "b2", T0)
        self.assertIsNone(states.consume("s2", T0 + 600))  # expired: consumed and refused
        self.assertEqual(self.count("oauth_states"), 0)
        states.create("old", "n", "b", T0 - 5000)
        states.create("new", "n", "b", T0)
        self.assertEqual(self.count("oauth_states"), 1)  # the expired row was cleaned on the next create
        dump = self.dump()
        for plain in ("s1", "n1", "b1", "new", "old"):
            self.assertNotIn("|" + plain + "|", dump)

    def test_nothing_secret_is_stored(self):
        states = OAuthStates(self.db)
        state, nonce, binding = oidc.new_random(), oidc.new_random(), oidc.new_random()
        states.create(state, nonce, binding, T0)
        dump = self.dump()
        for value in (state, nonce, binding, oidc.derive_verifier(state, binding)):
            self.assertNotIn(value, dump)


if __name__ == "__main__":
    unittest.main()
