"""OAuth 2.1 for the MCP endpoint (design 3.16, ADR-020).

Two levels are checked here:

* the **store**: the shapes of the secrets, the one-time code, the PKCE binding, the audience
  binding, the rotation of the refresh token and the reuse detection;
* the **endpoints**: the two metadata documents, the dynamic registration, the consent page and
  the token endpoint, through the live app with the fake identity provider.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from gemarc_mcp.accounts import oauth as O  # noqa: E402
from gemarc_mcp.accounts.users import UserAccounts  # noqa: E402

from test_http_transport import INITIALIZE, rpc  # noqa: E402

from .fake_idp import ServerCase  # noqa: E402
from .support import T0, DbCase  # noqa: E402

VERIFIER = "verifier-" + "a" * 43
REDIRECT = "https://client.example/callback"


def challenge(verifier: str = VERIFIER) -> str:
    return O.code_challenge(verifier)


class Shapes(unittest.TestCase):
    """The formats themselves: they are the dispatch key and the first line of defence."""

    def test_every_secret_has_its_own_prefix_and_length(self):
        for prefix, secret in ((O.ACCESS_PREFIX, O.mint_secret(O.ACCESS_PREFIX)),
                                (O.REFRESH_PREFIX, O.mint_secret(O.REFRESH_PREFIX)),
                                (O.CODE_PREFIX, O.mint_secret(O.CODE_PREFIX))):
            with self.subTest(prefix=prefix):
                self.assertTrue(secret.startswith(prefix))
                self.assertEqual(len(secret), len(prefix) + O.SECRET_LENGTH)
                self.assertIsNone(O.split_secret(secret, prefix[:-1]), "the wrong prefix must not parse")

    def test_a_client_id_is_short_and_public(self):
        client_id = O.mint_client_id()
        self.assertTrue(client_id.startswith(O.CLIENT_PREFIX))
        self.assertEqual(len(client_id), len(O.CLIENT_PREFIX) + O.CLIENT_ID_LENGTH)

    def test_the_pkce_challenge_is_s256_and_needs_the_right_verifier(self):
        self.assertEqual(challenge(), O.code_challenge(VERIFIER))
        self.assertNotEqual(challenge(), challenge(VERIFIER + "x"))
        self.assertTrue(O.challenge_matches(VERIFIER, challenge()))
        self.assertFalse(O.challenge_matches(VERIFIER + "x", challenge()))
        self.assertFalse(O.challenge_matches("short", challenge()))
        # "plain" is not accepted: the challenge of the verifier itself is not the verifier.
        self.assertFalse(O.challenge_matches(VERIFIER, VERIFIER))

    def test_only_https_and_the_loopback_interface_may_be_registered(self):
        self.assertTrue(O.valid_redirect_uri("https://client.example/callback"))
        self.assertTrue(O.valid_redirect_uri("http://localhost:1234/cb"))
        self.assertTrue(O.valid_redirect_uri("http://127.0.0.1:1234/cb"))
        self.assertFalse(O.valid_redirect_uri("http://client.example/cb"))
        self.assertFalse(O.valid_redirect_uri("https://client.example/cb\n"))
        self.assertFalse(O.valid_redirect_uri("javascript:alert(1)"))
        self.assertFalse(O.valid_redirect_uri(""))


class Metadata(unittest.TestCase):
    """The two documents a client reads before anything else (RFC 9728, RFC 8414)."""

    def test_the_resource_metadata_names_the_server_and_the_scopes(self):
        document = O.resource_metadata("https://gem-arc.vercel.app")
        self.assertEqual(document["resource"], "https://gem-arc.vercel.app")
        self.assertEqual(document["authorization_servers"], ["https://gem-arc.vercel.app"])
        self.assertEqual(document["scopes_supported"], ["read", "write"])
        self.assertEqual(document["bearer_methods_supported"], ["header"])

    def test_the_server_metadata_names_the_endpoints_and_the_flow(self):
        document = O.authorization_server_metadata("https://gem-arc.vercel.app")
        base = "https://gem-arc.vercel.app"
        self.assertEqual(document["issuer"], base)
        self.assertEqual(document["authorization_endpoint"], base + "/authorize")
        self.assertEqual(document["token_endpoint"], base + "/token")
        self.assertEqual(document["registration_endpoint"], base + "/register")
        self.assertEqual(document["response_types_supported"], ["code"])
        self.assertEqual(document["grant_types_supported"], ["authorization_code", "refresh_token"])
        self.assertEqual(document["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(document["token_endpoint_auth_methods_supported"], ["none"])


class Store(DbCase):
    """The database side: one use, one audience, one rotation."""

    def setUp(self):
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.uid = self.users.login("sub-1", "one@gmail.com", False, T0).user_id
        self.store = O.OAuthStore(self.db)

    def _client(self, uris=(REDIRECT,)):
        return self.store.register_client("test client", list(uris), T0)

    def test_the_registration_issues_no_secret(self):
        client = self._client()
        self.assertTrue(client.id.startswith(O.CLIENT_PREFIX))
        self.assertEqual(client.redirect_uris, (REDIRECT,))
        self.assertEqual(self.count("oauth_clients"), 1)

    def test_the_code_is_one_use_and_bound_to_the_pkce_challenge(self):
        client = self._client()
        authorization = self.store.issue_code(client.id, self.uid, REDIRECT, "read", "https://gem-arc.vercel.app",
                                              challenge(), T0)
        self.assertTrue(authorization.code.startswith(O.CODE_PREFIX))
        with self.assertRaises(O.OAuthError) as ctx:
            self.store.consume_code(authorization.code, client.id, REDIRECT, "wrong-verifier", T0 + 1)
        self.assertEqual(ctx.exception.error, "invalid_grant")
        self.assertEqual(ctx.exception.reason, "code_challenge")
        used = self.store.consume_code(authorization.code, client.id, REDIRECT, VERIFIER, T0 + 1)
        self.assertEqual(used.user_id, self.uid)
        with self.assertRaises(O.OAuthError) as ctx:
            self.store.consume_code(authorization.code, client.id, REDIRECT, VERIFIER, T0 + 2)
        self.assertEqual(ctx.exception.reason, "code_used")

    def test_the_code_expires_after_a_minute(self):
        client = self._client()
        authorization = self.store.issue_code(client.id, self.uid, REDIRECT, "read", "", challenge(), T0)
        with self.assertRaises(O.OAuthError) as ctx:
            self.store.consume_code(authorization.code, client.id, REDIRECT, VERIFIER, T0 + 61)
        self.assertEqual(ctx.exception.reason, "code_expired")

    def test_the_tokens_are_stored_as_hashes_only(self):
        client = self._client()
        authorization = self.store.issue_code(client.id, self.uid, REDIRECT, "read", "", challenge(), T0)
        self.store.consume_code(authorization.code, client.id, REDIRECT, VERIFIER, T0 + 1)
        tokens = self.store.issue_tokens(client.id, self.uid, "read", T0 + 1)
        self.assertTrue(tokens.access_token.startswith(O.ACCESS_PREFIX))
        self.assertTrue(tokens.refresh_token.startswith(O.REFRESH_PREFIX))
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT token_hash FROM oauth_access_tokens")
            self.assertNotIn(tokens.access_token, str(row["token_hash"]))
            count = tx.fetchone("SELECT count(*) AS n FROM oauth_access_tokens")["n"]
            self.assertEqual(count, 1)

    def test_the_access_token_is_bound_to_the_audience(self):
        client = self._client()
        tokens = self.store.issue_tokens(client.id, self.uid, "read", T0, resource="https://gem-arc.vercel.app")
        good = self.store.verify_access_token(tokens.access_token, "https://gem-arc.vercel.app", T0 + 1)
        self.assertTrue(good.ok, good.reason)
        other = self.store.verify_access_token(tokens.access_token, "https://other.vercel.app", T0 + 1)
        self.assertEqual((other.ok, other.reason), (False, "audience"))

    def test_the_access_token_expires_and_the_user_must_stay_active(self):
        client = self._client()
        tokens = self.store.issue_tokens(client.id, self.uid, "read", T0)
        self.assertTrue(self.store.verify_access_token(tokens.access_token, "", T0 + 1).ok)
        self.assertEqual(self.store.verify_access_token(tokens.access_token, "", T0 + O.ACCESS_TTL_S).reason, "expired")

    def test_the_refresh_token_rotates_and_the_old_one_is_refused(self):
        client = self._client()
        tokens = self.store.issue_tokens(client.id, self.uid, "read", T0)
        rotated = self.store.rotate(tokens.refresh_token, T0 + 1)
        self.assertNotEqual(rotated.refresh_token, tokens.refresh_token)
        self.assertNotEqual(rotated.access_token, tokens.access_token)
        # The old refresh token is dead; using it again is a reuse.
        with self.assertRaises(O.OAuthError) as ctx:
            self.store.rotate(tokens.refresh_token, T0 + 2)
        self.assertEqual(ctx.exception.reason, "refresh_reused")
        # ... and the chain dies with it.
        with self.assertRaises(O.OAuthError):
            self.store.rotate(rotated.refresh_token, T0 + 3)

    def test_a_write_scope_needs_the_right_of_the_user(self):
        client = self._client()
        tokens = self.store.issue_tokens(client.id, self.uid, "write", T0)
        verification = self.store.verify_access_token(tokens.access_token, "", T0 + 1)
        self.assertEqual(verification.scope, "read", "a user without the write right never gets the write scope")


class Endpoints(ServerCase):
    """The live app: the metadata, the registration, the consent and the token."""

    def _register(self, uris=(REDIRECT,)):
        body = json.dumps({"client_name": "test client", "redirect_uris": list(uris)}).encode("utf-8")
        return self.browser.request("POST", "/register", headers={"Content-Type": "application/json"}, data=body)

    def test_the_metadata_documents_answer(self):
        resource = self.browser.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resource.status, 200, resource.text[:200])
        document = json.loads(resource.text)
        self.assertEqual(document["authorization_servers"], [self.srv.base])
        again = self.browser.get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(again.status, 200)
        server = self.browser.get("/.well-known/oauth-authorization-server")
        self.assertEqual(server.status, 200)
        metadata = json.loads(server.text)
        self.assertEqual(metadata["token_endpoint"], self.srv.base + "/token")
        self.assertEqual(metadata["registration_endpoint"], self.srv.base + "/register")

    def test_a_client_registers_and_gets_no_secret(self):
        response = self._register()
        self.assertEqual(response.status, 201, response.text[:300])
        document = json.loads(response.text)
        self.assertTrue(document["client_id"].startswith(O.CLIENT_PREFIX))
        self.assertNotIn("client_secret", document)
        self.assertEqual(document["redirect_uris"], [REDIRECT])

    def test_a_plain_http_redirect_uri_is_refused(self):
        response = self._register(uris=("http://client.example/cb",))
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.text)["error"], "invalid_redirect_uri")

    def test_the_consent_page_asks_before_it_grants(self):
        registered = json.loads(self._register().text)
        query = ("?response_type=code&client_id=%s&redirect_uri=%s&scope=read&state=xyz"
                 "&code_challenge=%s&code_challenge_method=S256" % (
                     registered["client_id"], REDIRECT, challenge()))
        self.logged_in_page()
        page = self.browser.get("/authorize" + query)
        self.assertEqual(page.status, 200, page.text[:300])
        self.assertIn("test client", page.text)
        self.assertIn('name="decision" value="allow"', page.text)
        self.assertIn('name="decision" value="deny"', page.text)
        self.assertNotIn("code=", page.text)

    def test_the_reader_must_sign_in_before_the_consent(self):
        registered = json.loads(self._register().text)
        query = ("?response_type=code&client_id=%s&redirect_uri=%s&scope=read&state=xyz"
                 "&code_challenge=%s&code_challenge_method=S256" % (
                     registered["client_id"], REDIRECT, challenge()))
        page = self.browser.get("/authorize" + query)
        self.assertEqual(page.status, 302)
        self.assertTrue(page.location.startswith("/auth/google/start?return_to="), page.location)

    def test_the_denial_does_not_issue_a_code(self):
        registered = json.loads(self._register().text)
        query = ("?response_type=code&client_id=%s&redirect_uri=%s&scope=read&state=xyz"
                 "&code_challenge=%s&code_challenge_method=S256" % (
                     registered["client_id"], REDIRECT, challenge()))
        self.logged_in_page()
        page = self.browser.get("/authorize" + query)
        fields = {"csrf": self.browser.csrf(page.text), "decision": "deny",
                  "response_type": "code", "client_id": registered["client_id"], "redirect_uri": REDIRECT,
                  "scope": "read", "state": "xyz", "code_challenge": challenge(),
                  "code_challenge_method": "S256"}
        denied = self.browser.post("/authorize", fields)
        self.assertEqual(denied.status, 302)
        self.assertIn("error=access_denied", denied.location)
        self.assertEqual(self.count_rows("oauth_codes"), 0)

    def test_the_code_opens_the_mcp_endpoint_and_rotates(self):
        registered = json.loads(self._register().text)
        query = ("?response_type=code&client_id=%s&redirect_uri=%s&scope=read&state=xyz"
                 "&code_challenge=%s&code_challenge_method=S256" % (
                     registered["client_id"], REDIRECT, challenge()))
        self.logged_in_page()
        page = self.browser.get("/authorize" + query)
        fields = {"csrf": self.browser.csrf(page.text), "decision": "allow",
                  "response_type": "code", "client_id": registered["client_id"], "redirect_uri": REDIRECT,
                  "scope": "read", "state": "xyz", "code_challenge": challenge(),
                  "code_challenge_method": "S256"}
        allowed = self.browser.post("/authorize", fields)
        self.assertEqual(allowed.status, 302, allowed.text[:300])
        self.assertIn("code=", allowed.location)
        code = allowed.location.split("code=")[1].split("&")[0]

        token_response = self.browser.post("/token", {
            "grant_type": "authorization_code", "code": code, "code_verifier": VERIFIER,
            "client_id": registered["client_id"], "redirect_uri": REDIRECT})
        self.assertEqual(token_response.status, 200, token_response.text[:300])
        tokens = json.loads(token_response.text)
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertTrue(tokens["access_token"].startswith(O.ACCESS_PREFIX))

        # The access token opens /mcp.
        status, _headers, _body = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"],
                                      headers={"Authorization": "Bearer " + tokens["access_token"]})
        self.assertEqual(status, 200)

        # The code is one use.
        again = self.browser.post("/token", {
            "grant_type": "authorization_code", "code": code, "code_verifier": VERIFIER,
            "client_id": registered["client_id"], "redirect_uri": REDIRECT})
        self.assertEqual(again.status, 400)
        self.assertEqual(json.loads(again.text)["error"], "invalid_grant")

        # The refresh token rotates.
        refreshed = self.browser.post("/token", {
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        self.assertEqual(refreshed.status, 200, refreshed.text[:300])
        rotated = json.loads(refreshed.text)
        self.assertNotEqual(rotated["refresh_token"], tokens["refresh_token"])
        reused = self.browser.post("/token", {
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        self.assertEqual(reused.status, 400)
        self.assertEqual(json.loads(reused.text)["error"], "invalid_grant")

    def test_the_401_points_at_the_metadata(self):
        status, headers, body = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"])
        self.assertEqual(status, 401)
        challenge_header = headers["WWW-Authenticate"]
        self.assertTrue(challenge_header.startswith("Bearer"), challenge_header)
        expected = "resource_metadata=" + chr(34) + self.srv.base + "/.well-known/oauth-protected-resource" + chr(34)
        self.assertIn(expected, challenge_header)

    def count_rows(self, table: str) -> int:
        runtime = self.srv.runtime()
        with runtime.db.transaction() as tx:
            return tx.fetchone("SELECT count(*) AS n FROM " + table)["n"]


if __name__ == "__main__":
    unittest.main()
