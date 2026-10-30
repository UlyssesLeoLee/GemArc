"""End to end through the deployable app: fake Google -> sign in -> PAT -> official MCP client -> revoke -> 401.

A real HTTP client (``urllib``) talks to ``api.index.build_app`` under uvicorn; the identity provider is the local fake
in ``fake_idp.py``; the database is a throw-away SQLite file. Nothing here touches Google, Neon or Vercel.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_http_transport import INITIALIZE, drive, probe_model, rpc, sdk_available  # noqa: E402

from .fake_idp import CLIENT_SECRET, Browser, ServerCase  # noqa: E402

PAT_RE = re.compile(r"gat_[a-z2-7]{8}_[A-Za-z0-9_-]{49}")
EMAIL = "alice@gmail.com"


def create_token(case: ServerCase, name="laptop", **extra):
    page = case.browser.get("/account").text
    resp = case.browser.post("/account/tokens", {"csrf": case.browser.csrf(page), "name": name, **extra})
    return resp


def first_token(resp) -> str:
    found = PAT_RE.search(resp.text)
    assert found, resp.text[:400]
    return found.group(0)


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class EndToEnd(ServerCase):
    def test_full_flow_with_the_official_mcp_client(self):
        # 1. start -> Google (fake) -> callback
        start, authorize, callback = self.login({"sub": "g-1", "email": EMAIL})
        self.assertEqual(start.status, 302)
        self.assertTrue(start.location.startswith(self.idp.base + "/authorize?"))
        self.assertEqual(authorize.status, 302)
        self.assertEqual(callback.status, 303)
        self.assertEqual(callback.location, "/account")
        self.assertEqual(self.idp.violations, [])
        cookie = next(c for c in callback.set_cookies() if c.startswith("gemarc_sid="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain", cookie)
        # 2. the account page
        page = self.browser.get("/account")
        self.assertEqual(page.status, 200)
        self.assertIn(EMAIL, page.text)
        # 3. create a PAT
        created = create_token(self)
        self.assertEqual(created.status, 200, created.text[:300])
        token = first_token(created)
        self.assertIn("claude mcp add", created.text)
        self.assertIn("bearer_token_env_var", created.text)
        self.assertIn("Bearer ${GEMARC_MCP_PAT}", created.text)
        # 4. /mcp without it and with a wrong one
        status, headers, _ = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"])
        self.assertEqual(status, 401)
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        status, _h, _b = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"],
                             headers={"Authorization": "Bearer " + token[:-1] + ("a" if token[-1] != "a" else "b")})
        self.assertEqual(status, 401)
        # 5. the OFFICIAL client: initialize, tools/list, a tool call
        results = drive(self.srv.url("/mcp"), {"validate": ("gemarc_validate", {"model": probe_model()})},
                        headers={"Authorization": "Bearer " + token})
        self.assertEqual(results["server"], "GemArc MCP")
        self.assertIn("gemarc_validate", results["tools"])
        self.assertFalse(results["validate_isError"])
        # 6. revoke through the page -> immediately 401, in the same process, inside the cache TTL
        page = self.browser.get("/account").text
        token_id = token[4:12]
        resp = self.browser.post(f"/account/tokens/{token_id}/revoke", {"csrf": self.browser.csrf(page)})
        self.assertEqual(resp.status, 303)
        status, _h, _b = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"],
                             headers={"Authorization": "Bearer " + token})
        self.assertEqual(status, 401)
        self.assertEqual(self.srv.app.state.gemarc_accounts().db_state, "ok")

    def test_second_login_keeps_one_user_and_rotates_the_session(self):
        self.login({"sub": "g-2", "email": EMAIL})
        first_sid = self.browser.cookies["gemarc_sid"]
        _s, _a, callback = self.login()
        self.assertEqual(callback.status, 303)
        self.assertNotEqual(self.browser.cookies["gemarc_sid"], first_sid)
        self.assertEqual(self.count("users"), 1)
        self.assertEqual(self.count("sessions"), 1)  # the presented session was revoked by rotation
        other = Browser(self.srv.base)
        other.cookies["gemarc_sid"] = first_sid
        self.assertNotIn(EMAIL, other.get("/account").text)

    def test_healthz_reports_ok_and_nothing_else(self):
        health = Browser(self.srv.base).get("/healthz").json()
        self.assertEqual(health["accounts"], "ok")
        self.assertTrue(health["authRequired"])
        flat = json.dumps(health)
        for secret in (CLIENT_SECRET, "sqlite", self.tmp.replace("\\", "/"), "k1:"):
            self.assertNotIn(secret, flat)
        for banned in ("accountsMissing", "mcpAuth", "role"):
            self.assertNotIn(banned, health)


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class DeleteExportLeak(ServerCase):
    # VERCEL + x-vercel-forwarded-for make every audit row of this user carry a real ip_hash
    server_kwargs = {"env": {"VERCEL": "1"}}
    IP = {"x-vercel-forwarded-for": "203.0.113.20"}

    def setUp(self):
        super().setUp()
        self.idp.identity = {"sub": "g-secret-sub-77", "email": EMAIL}
        start = self.browser.nav("/auth/google/start", headers=self.IP)
        authorize = Browser(self.idp.base).get(start.location, cookies=False)
        callback = self.browser.nav(authorize.location, headers=self.IP)
        self.assertEqual(callback.status, 303)
        page = self.browser.get("/account", headers=self.IP).text
        created = self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": "laptop"},
                                    headers=self.IP)
        self.token = first_token(created)
        self.sid = self.browser.cookies["gemarc_sid"]

    def test_export_has_no_secrets_and_is_no_store(self):
        page = self.browser.get("/account").text
        resp = self.browser.post("/account/export", {"csrf": self.browser.csrf(page)})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        data = resp.json()
        self.assertEqual(data["profile"]["email"], EMAIL)
        self.assertEqual(len(data["tokens"]), 1)
        text = resp.text
        for secret in (self.token, self.token[13:56], self.sid, "g-secret-sub-77", "secret_hash", "ip_hash"):
            self.assertNotIn(secret, text)
        self.assertNotRegex(text, r"[A-Za-z0-9_-]{43}")  # no 43-char hash or secret shaped value anywhere
        self.assertEqual(self.browser.request("GET", "/account/export").status, 405)

    def test_database_dump_leak_scenario(self):
        """An attacker holding the whole database (all rows and the raw file) still has no usable credential."""
        self.idp.faults["claims"] = {}
        dump = self.dump()
        raw = Path(self.srv.db_path).read_bytes()
        secrets_in_play = {
            "pat": self.token, "pat-secret": self.token[13:56], "session id": self.sid,
            "sub": "g-secret-sub-77", "email": EMAIL, "email local part": "alice",
            "client secret": CLIENT_SECRET,
        }
        for label, value in secrets_in_play.items():
            self.assertNotIn(value, dump, label)
            self.assertNotIn(value.encode(), raw, label)
        # state, nonce and the authorisation code were used: none of them (nor the verifier) is in the database
        for call in self.idp.authorize_calls:
            for value in (call["state"], call["nonce"]):
                self.assertNotIn(value, dump)
                self.assertNotIn(value.encode(), raw)
        for call in self.idp.token_calls:
            for value in (call["code"], call["code_verifier"]):
                self.assertNotIn(value, dump)
                self.assertNotIn(value.encode(), raw)
        self.assertNotIn("password", dump.lower())
        # and the table layout has no password or name column at all
        with self.srv.database().transaction() as tx:
            columns = {r["name"] for t in ("users", "sessions", "api_tokens") for r in tx.fetchall(f"PRAGMA table_info({t})")}
        self.assertFalse({c for c in columns if "pass" in c or c in ("name_enc", "display_name")} - {"name"})
        # the stored token hash alone does not authenticate
        with self.srv.database().transaction() as tx:
            stored = tx.fetchone("SELECT secret_hash FROM api_tokens")["secret_hash"]
        status, _h, _b = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"],
                             headers={"Authorization": "Bearer " + stored})
        self.assertEqual(status, 401)

    def test_delete_removes_everything(self):
        page = self.browser.get("/account").text
        self.assertIn("DELETE", page)
        self.assertIn(EMAIL, page)  # the confirmation form shows the full address
        # another user keeps their data
        other = Browser(self.srv.base)
        self.login({"sub": "g-other", "email": "bob@gmail.com"}, other)
        before_users = self.count("users")
        self.assertEqual(before_users, 2)
        # wrong confirmation word
        resp = self.browser.post("/account/delete", {"csrf": self.browser.csrf(page), "confirm": "delete"})
        self.assertEqual(resp.status, 400)
        self.assertIn("GEMARC_ACCT_041", resp.text)
        self.assertEqual(self.count("users"), 2)
        user_id = self._user_id_of(EMAIL)
        with self.srv.database().transaction() as tx:
            owned = [r["id"] for r in tx.fetchall("SELECT id FROM audit_log WHERE user_id = ?", (user_id,))]
            hashed = [r["id"] for r in tx.fetchall(
                "SELECT id FROM audit_log WHERE user_id = ? AND ip_hash IS NOT NULL", (user_id,))]
        self.assertGreaterEqual(len(owned), 3)  # user_created, login_ok, token_created at least
        self.assertGreaterEqual(len(hashed), 2)  # the sign-in rows really carry an ip hash before the delete
        resp = self.browser.post("/account/delete", {"csrf": self.browser.csrf(page), "confirm": "DELETE"})
        self.assertEqual(resp.status, 200)
        self.assertIn("账号已删除", resp.text)
        # the PAT stopped working at once and the session is gone
        status, _h, _b = rpc(self.srv.url("/mcp"), "initialize", INITIALIZE["params"],
                             headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(status, 401)
        stale = Browser(self.srv.base)
        stale.cookies["gemarc_sid"] = self.sid
        self.assertNotIn(EMAIL, stale.get("/account").text)
        # the database holds nothing of the deleted user
        self.assertEqual(self.count("users"), 1)
        self.assertEqual(self.count("sessions", "user_id = ?", (user_id,)), 0)
        self.assertEqual(self.count("api_tokens", "user_id = ?", (user_id,)), 0)
        self.assertEqual(self.count("audit_log", "user_id = ?", (user_id,)), 0)
        self.assertEqual(self.count("audit_log", "event = 'account_deleted' AND user_id IS NULL"), 1)
        # the very same rows survive, anonymised: neither user id nor ip hash is left on them
        with self.srv.database().transaction() as tx:
            for audit_id in owned:
                row = tx.fetchone("SELECT user_id, ip_hash FROM audit_log WHERE id = ?", (audit_id,))
                self.assertIsNotNone(row, "audit rows are kept")
                self.assertIsNone(row["user_id"])
                self.assertIsNone(row["ip_hash"])
        dump = self.dump()
        self.assertNotIn(user_id, dump)
        # the other user still exists and signing in again with the deleted identity creates a NEW account
        self.login({"sub": "g-secret-sub-77", "email": EMAIL})
        self.assertEqual(self.count("users"), 2)
        self.assertNotEqual(self._user_id_of(EMAIL), user_id)

    def _user_id_of(self, email: str) -> str:
        rt = self.srv.runtime()
        with rt.db.transaction() as tx:
            for candidate in rt.crypto.blind_index_all("email", email):
                row = tx.fetchone("SELECT id FROM users WHERE email_bidx = ?", (candidate,))
                if row:
                    return row["id"]
        return ""


if __name__ == "__main__":
    unittest.main()
