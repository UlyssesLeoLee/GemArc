"""DD-010: the settings of an account live in the database and reach the MCP endpoint.

What is bound here:

* the settings are really stored (a row in the database, not in the process);
* they belong to one account only: another account never sees them;
* the account page can change them without any script;
* the console reads them and speaks the language of the account;
* ``tools/list`` of ``/mcp`` is the list of the account, in the language of the account;
* a refused value changes nothing.
"""
from __future__ import annotations

import json
import unittest

from gemarc_mcp import i18n

from .fake_idp import Browser, ServerCase

MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class TheSettingsOfAnAccount(ServerCase):
    """The store, the route and the isolation."""

    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "settings-1", "email": "one@gmail.com"})

    def save(self, fields, browser=None):
        browser = browser or self.browser
        page = browser.get("/account").text
        return browser.post("/account/settings", {"csrf": browser.csrf(page), **fields})

    def stored(self):
        with self.srv.database().transaction() as tx:
            rows = tx.fetchall("SELECT user_id, lang, tools, render_profile FROM user_settings")
        return [dict(row) for row in rows]

    def test_an_account_without_a_row_has_the_defaults(self):
        self.assertEqual(self.stored(), [])
        resp = self.browser.get("/account/settings", headers={"Accept": "application/json"})
        self.assertEqual(resp.status, 200)
        body = resp.json()
        # an account that never chose a language has none: the reader's own language decides
        self.assertEqual(body["lang"], "")
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["render_profile"], "")

    def test_the_read_answers_json_and_names_the_tools_of_the_server(self):
        resp = self.browser.get("/account/settings", headers={"Accept": "application/json"})
        body = resp.json()
        self.assertIn("available_tools", body)
        self.assertIn("gemarc_brief", body["available_tools"])

    def test_saving_writes_a_row_into_the_database(self):
        resp = self.save({"lang": "ja-JP", "render_profile": "vite", "tools_mode": "some",
                          "tools": "gemarc_brief"})
        self.assertEqual(resp.status, 200, resp.text[:300])
        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lang"], "ja-JP")
        self.assertEqual(rows[0]["render_profile"], "vite")
        self.assertEqual(rows[0]["tools"], "gemarc_brief")

    def test_the_settings_are_read_back(self):
        self.save({"lang": "en-US", "render_profile": "mvp", "tools_mode": "all"})
        body = self.browser.get("/account/settings", headers={"Accept": "application/json"}).json()
        self.assertEqual(body["lang"], "en-US")
        self.assertEqual(body["render_profile"], "mvp")
        self.assertEqual(body["tools"], [])

    def test_an_unknown_tool_is_refused_and_nothing_is_written(self):
        resp = self.save({"lang": "zh-CN", "render_profile": "", "tools_mode": "some",
                          "tools": "gemarc_not_a_tool"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.stored(), [])

    def test_an_unknown_language_is_refused(self):
        resp = self.save({"lang": "xx-XX", "render_profile": "", "tools_mode": "all"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.stored(), [])

    def test_an_unknown_render_form_is_refused(self):
        resp = self.save({"lang": "zh-CN", "render_profile": "angular", "tools_mode": "all"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.stored(), [])

    def test_a_list_with_nothing_in_it_is_refused(self):
        resp = self.save({"lang": "zh-CN", "render_profile": "", "tools_mode": "some"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.stored(), [])

    def test_the_token_is_required(self):
        resp = self.browser.post("/account/settings", {"lang": "en-US"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.stored(), [])

    def test_another_account_does_not_see_them(self):
        self.save({"lang": "ja-JP", "render_profile": "vite", "tools_mode": "all"})
        other = Browser(self.srv.base)
        self.logged_in_page({"sub": "settings-2", "email": "two@gmail.com"}, browser=other)
        body = other.get("/account/settings", headers={"Accept": "application/json"}).json()
        self.assertEqual(body["lang"], "")
        self.assertEqual(body["render_profile"], "")
        # the row of the first account is still there, and it is still only one
        self.assertEqual(len(self.stored()), 1)

    def test_the_settings_survive_a_new_process(self):
        """The settings are in the database, not in the memory of a process."""
        self.save({"lang": "en-US", "render_profile": "vite", "tools_mode": "some",
                   "tools": "gemarc_brief"})
        from gemarc_mcp.accounts.settings import SettingsStore

        store = SettingsStore(self.srv.database())
        rows = self.stored()
        loaded = store.load(rows[0]["user_id"])
        self.assertEqual(loaded.lang, "en-US")
        self.assertEqual(loaded.render_profile, "vite")
        self.assertEqual(set(loaded.tools), {"gemarc_brief"})


class TheAccountPageCarriesTheForm(ServerCase):
    """The form works without any script: the page posts to /account/settings."""

    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "settings-3", "email": "three@gmail.com"})

    def test_the_form_is_on_the_page(self):
        self.assertIn('action="/account/settings"', self.page)
        self.assertIn('name="lang"', self.page)
        self.assertIn('name="render_profile"', self.page)
        self.assertIn('name="tools"', self.page)
        self.assertIn('name="tools_mode"', self.page)

    def test_every_choice_of_the_form_carries_its_text(self):
        """A control without its text is a control nobody can read."""
        from gemarc_mcp import i18n

        for key in ("account.settingstoolsall", "account.settingstoolssome",
                    "account.settingslang", "account.settingsprofile", "account.settingssave"):
            text = i18n.text("zh-CN", "accounts", key)
            self.assertIn(text, self.page, key)
        # the two modes are two radio buttons, each with its own text
        self.assertIn('value="all"', self.page)
        self.assertIn('value="some"', self.page)
        self.assertEqual(self.page.count('name="tools_mode"'), 2)
        self.assertEqual(self.page.count("</label>"), self.page.count("<label"))

    def test_the_form_names_the_tools_of_the_server(self):
        self.assertIn('value="gemarc_brief"', self.page)

    def test_the_page_has_no_script_at_all(self):
        self.assertNotIn("<script", self.page)

    def test_the_page_comes_back_in_the_language_that_was_saved(self):
        page = self.browser.get("/account").text
        csrf = self.browser.csrf(page)
        resp = self.browser.post("/account/settings", {"csrf": csrf, "lang": "ja-JP",
                                                        "render_profile": "", "tools_mode": "all"})
        self.assertEqual(resp.status, 200)
        self.assertIn('lang="ja-JP"', resp.text)

    def test_the_account_page_follows_the_account_language(self):
        page = self.browser.get("/account").text
        csrf = self.browser.csrf(page)
        self.browser.post("/account/settings", {"csrf": csrf, "lang": "en-US",
                                                "render_profile": "", "tools_mode": "all"})
        again = self.browser.get("/account").text
        self.assertIn('lang="en-US"', again)


class TheLanguageCanStayUnpinned(ServerCase):
    """The account does not have to pin a language (DD-010 section 4.1)."""

    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "settings-7", "email": "seven@gmail.com"})

    def save(self, fields):
        page = self.browser.get("/account").text
        return self.browser.post("/account/settings", {"csrf": self.browser.csrf(page), **fields})

    def stored(self):
        with self.srv.database().transaction() as tx:
            rows = tx.fetchall("SELECT user_id, lang FROM user_settings")
        return [dict(row) for row in rows]

    def test_the_page_offers_the_choice_to_follow_the_reader(self):
        text = i18n.text("zh-CN", "accounts", "account.settingslangdefault")
        self.assertIn(text, self.page)
        # and it is the one that is marked when the account never chose a language
        self.assertIn(f'<option value="" selected>{text}</option>', self.page)

    def test_saving_another_field_does_not_pin_the_language_of_the_reader(self):
        """The form of an account that never chose a language posts the empty language."""
        resp = self.save({"lang": "", "render_profile": "", "tools_mode": "all"})
        self.assertEqual(resp.status, 200, resp.text[:300])
        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lang"], "")

    def test_the_empty_language_is_accepted_and_read_back(self):
        resp = self.save({"lang": "", "render_profile": "mvp", "tools_mode": "all"})
        self.assertEqual(resp.status, 200, resp.text[:300])
        body = self.browser.get("/account/settings", headers={"Accept": "application/json"}).json()
        self.assertEqual(body["lang"], "")
        self.assertEqual(body["render_profile"], "mvp")

    def test_a_pinned_language_can_be_released(self):
        self.save({"lang": "ja-JP", "render_profile": "", "tools_mode": "all"})
        self.save({"lang": "", "render_profile": "", "tools_mode": "all"})
        self.assertEqual(self.stored()[0]["lang"], "")

    def test_the_page_comes_back_in_the_language_of_the_reader(self):
        """An account that pins no language goes back to the page of the reader."""
        self.browser.get("/account?lang=ja-JP")
        resp = self.save({"lang": "", "render_profile": "", "tools_mode": "all"})
        self.assertIn('lang="ja-JP"', resp.text)


class TheConsoleFollowsTheAccount(ServerCase):
    """The console speaks the language of the account and offers the settings."""

    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "settings-4", "email": "four@gmail.com"})

    def console(self):
        return self.browser.get("/").text

    def test_the_console_is_in_the_language_of_the_account(self):
        page = self.browser.get("/account").text
        csrf = self.browser.csrf(page)
        self.browser.post("/account/settings", {"csrf": csrf, "lang": "en-US",
                                                "render_profile": "", "tools_mode": "all"})
        console = self.console()
        self.assertIn('lang: "en-US"', console)
        self.assertIn('accountLang: "en-US"', console)

    def test_the_console_carries_the_token_that_saves_a_change(self):
        console = self.console()
        self.assertIn("csrf: \"", console)
        self.assertIn("settingsPath: \"/account/settings\"", console)

    def test_the_console_offers_the_settings(self):
        console = self.console()
        self.assertIn('id="settings-open"', console)
        self.assertIn('id="settings-tools"', console)

    def test_the_console_offers_the_choice_to_follow_the_reader(self):
        console = self.console()
        self.assertIn('id="settings-lang"', console)
        self.assertIn(i18n.text("zh-CN", "console", "settings.langreader"), console)


class TheMcpEndpointFollowsTheAccount(ServerCase):
    """The end of the chain: ``tools/list`` of ``/mcp`` is the list of the account."""

    def setUp(self):
        super().setUp()
        self.page = self.logged_in_page({"sub": "settings-5", "email": "five@gmail.com"})

    def tools(self, browser=None):
        browser = browser or self.browser
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = browser.request("POST", "/mcp", headers={**MCP_HEADERS, "Sec-Fetch-Site": "same-origin"},
                               data=json.dumps(payload).encode())
        self.assertEqual(resp.status, 200, resp.text[:300])
        return json.loads(resp.text)["result"]["tools"]

    def save(self, fields):
        page = self.browser.get("/account").text
        return self.browser.post("/account/settings", {"csrf": self.browser.csrf(page), **fields})

    def test_the_list_is_the_whole_one_without_a_list(self):
        names = [tool["name"] for tool in self.tools()]
        self.assertIn("gemarc_brief", names)
        self.assertIn("gemarc_validate", names)

    def test_the_list_of_the_account_is_narrower(self):
        self.save({"lang": "zh-CN", "render_profile": "", "tools_mode": "some",
                   "tools": "gemarc_brief"})
        names = [tool["name"] for tool in self.tools()]
        self.assertEqual(names, ["gemarc_brief"])

    def test_the_description_is_in_the_language_of_the_account(self):
        self.save({"lang": "ja-JP", "render_profile": "", "tools_mode": "all"})
        tools = {tool["name"]: tool for tool in self.tools()}
        wanted = i18n.section("ja-JP", "tools")["gemarc_brief"]
        self.assertEqual(tools["gemarc_brief"]["description"], wanted["description"])
        meta = tools["gemarc_brief"].get("_meta") or {}
        self.assertEqual(sorted(meta.get("gemarc.dev/descriptions") or {}), sorted(i18n.LANGUAGES))

    def test_another_account_keeps_the_whole_list(self):
        self.save({"lang": "zh-CN", "render_profile": "", "tools_mode": "some",
                   "tools": "gemarc_brief"})
        other = Browser(self.srv.base)
        self.logged_in_page({"sub": "settings-6", "email": "six@gmail.com"}, browser=other)
        names = [tool["name"] for tool in self.tools(browser=other)]
        self.assertIn("gemarc_validate", names)
        self.assertIn("gemarc_brief", names)

    def test_the_descriptions_stay_english_while_no_language_is_pinned(self):
        """An account that pins no language gets the formal English text (DD-010 section 4.1)."""
        self.save({"lang": "", "render_profile": "", "tools_mode": "all"})
        tools = {tool["name"]: tool for tool in self.tools()}
        other = Browser(self.srv.base)
        self.logged_in_page({"sub": "settings-8", "email": "eight@gmail.com"}, browser=other)
        plain = {tool["name"]: tool for tool in self.tools(browser=other)}
        self.assertEqual(tools["gemarc_brief"]["description"], plain["gemarc_brief"]["description"])
        self.assertNotEqual(tools["gemarc_brief"]["description"],
                            i18n.section("ja-JP", "tools")["gemarc_brief"]["description"])


if __name__ == "__main__":
    unittest.main()
