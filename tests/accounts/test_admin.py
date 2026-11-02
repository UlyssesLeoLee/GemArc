"""The operator area: the administrator role, the runtime settings and the routes that use them.

What is pinned here:

* the role is the UNION of the environment allow-list and the runtime grants, and it fails closed
  (an address that cannot be read is not an administrator unless the table grants the role);
* a signed-in user who is not an administrator cannot open the area;
* an administrator sees the user list, the audit log and the settings, and can act on a user;
* every state change goes through the origin + session + CSRF pipeline like the other account forms;
* the token policy in force follows the stored overrides, and the environment decides when nothing
  is stored;
* the console is behind the login, and the operator entry appears only for an administrator;
* the MCP endpoint accepts a same-origin login session, and refuses a cross-origin one.
"""
from __future__ import annotations

import io
import unittest

from gemarc_mcp.accounts import admin as admin_mod
from gemarc_mcp.accounts import __main__ as cli
from gemarc_mcp.accounts.admin import AdminService
from gemarc_mcp.accounts.crypto import b64u
from gemarc_mcp.accounts.db import Database
from gemarc_mcp.accounts.users import UserAccounts

from .fake_idp import Browser, ServerCase
from .support import DbCase, KEY1, T0

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}

ADMIN_ENV = {"GEMARC_ACCOUNTS_ADMIN_EMAILS": "owner@gmail.com"}


class AdminRole(DbCase):
    """The role itself: who counts as an administrator."""

    def setUp(self) -> None:
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.service = AdminService(self.db, self.crypto, ("owner@gmail.com",))
        self.owner = self.users.login("sub-owner", "owner@gmail.com", False, T0).user_id
        self.other = self.users.login("sub-other", "someone@gmail.com", False, T0).user_id

    def test_the_allow_list_alone_makes_an_administrator(self):
        self.assertTrue(self.service.is_admin(self.owner, "owner@gmail.com"))
        self.assertFalse(self.service.is_admin(self.other, "someone@gmail.com"))

    def test_the_allow_list_is_case_insensitive_and_normalised(self):
        self.assertTrue(self.service.in_allowlist("  Owner@Gmail.com "))

    def test_a_runtime_grant_alone_makes_an_administrator(self):
        self.assertFalse(self.service.is_admin(self.other, "someone@gmail.com"))
        self.assertTrue(self.service.grant(self.other, T0, by="cli"))
        self.assertTrue(self.service.is_admin(self.other, "someone@gmail.com"))

    def test_the_role_fails_closed_without_an_address(self):
        # The table grant still counts; the allow-list needs an address and does not get one.
        self.assertFalse(self.service.is_admin(self.other, None))
        self.service.grant(self.other, T0)
        self.assertTrue(self.service.is_admin(self.other, None))

    def test_granting_twice_is_not_an_error_and_revoking_is_explicit(self):
        self.assertTrue(self.service.grant(self.other, T0))
        self.assertFalse(self.service.grant(self.other, T0))
        self.assertEqual(self.service.granted_ids(), [self.other])
        self.assertTrue(self.service.revoke(self.other))
        self.assertFalse(self.service.revoke(self.other))
        self.assertEqual(self.service.granted_ids(), [])

    def test_revoking_the_grant_does_not_touch_the_allow_list(self):
        self.service.grant(self.owner, T0)
        self.service.revoke(self.owner)
        self.assertTrue(self.service.is_admin(self.owner, "owner@gmail.com"))

    def test_the_user_list_shows_the_grant_and_the_address(self):
        self.service.grant(self.other, T0)
        rows = {u.id: u for u in self.service.list_users()}
        self.assertTrue(rows[self.other].granted)
        self.assertFalse(rows[self.owner].granted)
        self.assertEqual(rows[self.other].email, "someone@gmail.com")
        self.assertEqual(rows[self.other].status, "active")

    def test_the_settings_are_numbers_within_bounds(self):
        self.service.set_number(admin_mod.PAT_MAX_DAYS, "45", T0)
        self.assertEqual(self.service.settings()["pat_max_days"], "45")
        for bad in ("0", "1000", "-1", "x", ""):
            with self.assertRaises(ValueError):
                self.service.set_number(admin_mod.PAT_MAX_DAYS, bad, T0)
        with self.assertRaises(ValueError):
            self.service.set_number("something_else", "1", T0)

    def test_the_tool_set_is_one_of_the_known_names(self):
        self.service.set_toolset("stateless", T0)
        self.assertEqual(self.service.settings()["toolset"], "stateless")
        with self.assertRaises(ValueError):
            self.service.set_toolset("nope", T0)

    def test_clearing_a_setting_hands_the_decision_back_to_the_environment(self):
        self.service.set_number(admin_mod.PAT_MAX_DAYS, "45", T0)
        self.assertTrue(self.service.clear(admin_mod.PAT_MAX_DAYS))
        self.assertNotIn(admin_mod.PAT_MAX_DAYS, self.service.settings())

    def test_the_audit_reader_never_returns_the_ip_hash(self):
        from gemarc_mcp.accounts import audit as audit_mod
        ip_hash = self.crypto.ip_hash("203.0.113.7", T0)
        with self.db.transaction() as tx:
            audit_mod.record(tx, T0, "login_ok", user_id=self.owner, ip_hash=ip_hash, code="x")
        rows = self.service.audit_entries()
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn("ip_hash", row)
            self.assertNotIn(ip_hash, str(row))


class AdminArea(ServerCase):
    """The routes: only an administrator gets in, and every change needs the CSRF token."""

    server_kwargs = {"env": ADMIN_ENV}

    def login_as(self, email):
        return self.login({"sub": "sub-" + email.split("@")[0], "email": email})

    def test_a_signed_in_user_who_is_not_an_administrator_is_refused(self):
        _s, _a, callback = self.login_as("someone@gmail.com")
        self.assertEqual(callback.status, 303)
        page = self.browser.get("/account/admin")
        self.assertEqual(page.status, 403)
        self.assertIn("GEMARC_ACCT_060", page.text)

    def test_the_environment_allow_list_opens_the_area(self):
        self.login_as("owner@gmail.com")
        page = self.browser.get("/account/admin")
        self.assertEqual(page.status, 200)
        self.assertIn("owner@gmail.com", page.text)
        self.assertIn("用户管理", page.text)
        self.assertIn("审计日志", page.text)

    def test_a_runtime_grant_also_opens_the_area(self):
        self.login_as("someone@gmail.com")
        service = AdminService(self.srv.database(), self.srv.runtime().crypto, ())
        service.grant(self.srv.runtime().users.login(
            "sub-someone", "someone@gmail.com", False, self.clock()).user_id, self.clock())
        page = self.browser.get("/account/admin")
        self.assertEqual(page.status, 200)

    def test_a_change_without_the_csrf_token_is_refused(self):
        self.login_as("owner@gmail.com")
        target = self.srv.runtime().users.login("sub-x", "x@gmail.com", False, self.clock()).user_id
        resp = self.browser.post(f"/account/admin/users/{target}/disable", {})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.srv.runtime().users.get(target).status, "active")

    def test_a_change_with_the_csrf_token_takes_effect(self):
        self.login_as("owner@gmail.com")
        target = self.srv.runtime().users.login("sub-x", "x@gmail.com", False, self.clock()).user_id
        page = self.browser.get("/account/admin")
        resp = self.browser.post(f"/account/admin/users/{target}/disable", {"csrf": self.browser.csrf(page.text)})
        self.assertEqual(resp.status, 303)
        self.assertEqual(self.srv.runtime().users.get(target).status, "disabled")

    def test_the_administrator_cannot_lock_themselves_out(self):
        self.login_as("owner@gmail.com")
        me = self.srv.runtime().users.login("sub-owner", "owner@gmail.com", False, self.clock()).user_id
        page = self.browser.get("/account/admin")
        resp = self.browser.post(f"/account/admin/users/{me}/disable", {"csrf": self.browser.csrf(page.text)})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.srv.runtime().users.get(me).status, "active")

    def test_a_token_policy_set_by_the_administrator_is_in_force(self):
        self.login_as("owner@gmail.com")
        page = self.browser.get("/account/admin")
        resp = self.browser.post("/account/admin/settings",
                                 {"csrf": self.browser.csrf(page.text), "key": "pat_max_days", "value": "45"})
        self.assertEqual(resp.status, 303)
        self.assertEqual(self.srv.runtime().tokens.effective_policy().max_days, 45)

    def test_the_administrator_can_ask_for_the_site_to_be_rebuilt(self):
        self.login_as("owner@gmail.com")
        page = self.browser.get("/account/admin")
        self.assertIn("5D 站", page.text)
        resp = self.browser.post("/account/admin/site", {"csrf": self.browser.csrf(page.text)})
        self.assertEqual(resp.status, 303)

    def test_the_account_page_links_to_the_console_and_to_the_area_for_an_administrator(self):
        self.login_as("owner@gmail.com")
        page = self.browser.get("/account")
        self.assertEqual(page.status, 200)
        self.assertIn('href="/"', page.text)
        self.assertIn('href="/account/admin"', page.text)

    def test_the_account_page_of_an_ordinary_user_has_no_link_to_the_area(self):
        self.login_as("someone@gmail.com")
        page = self.browser.get("/account")
        self.assertEqual(page.status, 200)
        self.assertIn('href="/"', page.text)
        self.assertNotIn('href="/account/admin"', page.text)

    def test_an_out_of_range_setting_is_refused_and_changes_nothing(self):
        self.login_as("owner@gmail.com")
        page = self.browser.get("/account/admin")
        resp = self.browser.post("/account/admin/settings",
                                 {"csrf": self.browser.csrf(page.text), "key": "pat_max_days", "value": "9999"})
        self.assertEqual(resp.status, 400)
        self.assertNotIn("pat_max_days", self.srv.runtime().admin.settings())


class ConsoleGate(ServerCase):
    """The console is behind the login; the operator entry is for administrators only."""

    server_kwargs = {"env": ADMIN_ENV}

    def test_the_console_sends_a_caller_without_a_session_to_the_sign_in(self):
        resp = self.browser.nav("/")
        self.assertEqual(resp.status, 303)
        self.assertEqual(resp.location, "/auth/google/start")

    def test_a_signed_in_user_reaches_the_console_without_the_operator_entry(self):
        self.login({"sub": "sub-someone", "email": "someone@gmail.com"})
        page = self.browser.get("/")
        self.assertEqual(page.status, 200)
        self.assertIn('admin: false', page.text)
        self.assertIn('id="admin-entry" hidden', page.text)

    def test_an_administrator_reaches_the_console_with_the_operator_entry(self):
        self.login({"sub": "sub-owner", "email": "owner@gmail.com"})
        page = self.browser.get("/")
        self.assertEqual(page.status, 200)
        self.assertIn('admin: true', page.text)
        self.assertIn('id="admin-entry" hidden', page.text)  # the entry is shown by the script, not by the markup
        self.assertIn("/account/admin", page.text)


class McpSessionLane(ServerCase):
    """``/mcp`` accepts the login session of a signed-in user, and only a same-origin one."""

    server_kwargs = {"env": ADMIN_ENV}

    def test_a_same_origin_session_may_call_the_mcp_endpoint(self):
        self.login({"sub": "sub-someone", "email": "someone@gmail.com"})
        resp = self.browser.request("POST", "/mcp",
                                    headers={"Content-Type": "application/json",
                                             "Accept": "application/json, text/event-stream",
                                             "Sec-Fetch-Site": "same-origin"},
                                    data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
        self.assertEqual(resp.status, 200, resp.text[:300])

    def test_without_a_session_the_mcp_endpoint_refuses(self):
        anonymous = Browser(self.srv.base)
        resp = anonymous.request("POST", "/mcp",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"},
                                 data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
        self.assertEqual(resp.status, 401)

    def test_a_cross_origin_session_is_refused(self):
        self.login({"sub": "sub-someone", "email": "someone@gmail.com"})
        resp = self.browser.request("POST", "/mcp",
                                    headers={"Content-Type": "application/json",
                                             "Accept": "application/json, text/event-stream",
                                             "Origin": "https://evil.example"},
                                    data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
        self.assertEqual(resp.status, 401)


class AdminCli(DbCase):
    """The operator CLI: list, grant and revoke the administrator role."""

    def setUp(self) -> None:
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.target = self.users.login("sub-cli", "cli@gmail.com", False, T0).user_id

    def env(self) -> dict:
        return {
            "GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
            "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1",
            "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(KEY1),
            "GEMARC_ACCOUNTS_ADMIN_EMAILS": "owner@gmail.com",
        }

    def cli_run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, self.env(), out, err, lambda: T0)
        return code, out.getvalue(), err.getvalue()

    def test_list_shows_the_allow_list_and_the_runtime_grants(self):
        code, out, _ = self.cli_run(["admin", "list"])
        self.assertEqual(code, 0)
        self.assertIn("environment allow-list: 1 address(es)", out)
        self.assertIn("runtime grants: 0", out)

    def test_grant_and_revoke_move_the_runtime_grant(self):
        code, out, _ = self.cli_run(["admin", "grant", "--email", "cli@gmail.com"])
        self.assertEqual(code, 0)
        self.assertIn(self.target, out)
        self.assertTrue(AdminService(self.db, self.crypto).granted(self.target))
        code, out, _ = self.cli_run(["admin", "revoke", "--email", "cli@gmail.com"])
        self.assertEqual(code, 0)
        self.assertFalse(AdminService(self.db, self.crypto).granted(self.target))

    def test_granting_an_unknown_address_fails_without_a_leak(self):
        code, _, err = self.cli_run(["admin", "grant", "--email", "nobody@gmail.com"])
        self.assertEqual(code, 1)
        self.assertNotIn("nobody@gmail.com", err)


if __name__ == "__main__":
    unittest.main()
