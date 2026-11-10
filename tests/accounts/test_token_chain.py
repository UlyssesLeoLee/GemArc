"""OPS-002 section 3.15: the token chain. The website issues the token, the agent fills it in.

The chain has one direction and two ends:

* the account page is the only place where a token is issued; right after the issue it hands
  out the client configuration with the token already filled in;
* the console only *fills in* a token: it says where the token comes from and links the
  account page; it never issues one.

Each class names the item of the checklist it covers.
"""
from __future__ import annotations

import re
import unittest
from html import unescape

from gemarc_mcp import i18n

from .fake_idp import ServerCase

EMAIL = "alice@gmail.com"
PAT_RE = re.compile(r"gat_[a-z2-7]{8}_[A-Za-z0-9_-]{49}")


class TheAccountPageIssues(ServerCase):
    """The account page is the one place that issues a token."""

    def setUp(self):
        super().setUp()
        self.logged_in_page({"sub": "chain-1", "email": EMAIL})

    def create(self, name: str = "laptop"):
        page = self.browser.get("/account").text
        return self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": name})

    def test_the_created_token_appears_on_the_response_that_creates_it(self):
        resp = self.create()
        self.assertEqual(resp.status, 200)
        self.assertIsNotNone(PAT_RE.search(resp.text))

    def test_the_page_hands_out_the_client_configurations_with_the_token_filled_in(self):
        text = unescape(self.create().text)
        token = PAT_RE.search(text).group(0)
        # Claude Code: the command carries the token
        self.assertIn("claude mcp add", text)
        self.assertIn(f"--header 'Authorization: Bearer {token}'", text)
        # Hermes: the configuration carries the token
        self.assertIn(f'Authorization: "Bearer {token}"', text)
        # Codex: the configuration names the variable (Codex refuses an inline bearer token)
        self.assertIn('bearer_token_env_var = "GEMARC_MCP_PAT"', text)
        # the two environment ways carry the token as well
        self.assertIn(f"$env:GEMARC_MCP_PAT = '{token}'", text)
        self.assertIn(f"export GEMARC_MCP_PAT='{token}'", text)

    def test_the_page_says_where_to_put_the_token_and_how_to_change_it(self):
        """The reader must learn the two things the account page is the only place to learn."""
        text = unescape(self.create().text)
        self.assertIn(i18n.text("zh-CN", "accounts", "account.snipintro"), text)
        self.assertIn("GEMARC_MCP_PAT", text)

    def test_the_configurations_are_ready_to_copy(self):
        """The Codex block is TOML and the Hermes block is YAML: the lines must be real lines."""
        text = unescape(self.create().text)
        codex = text[text.index("[mcp_servers.gemarc]"):]
        codex = codex[:codex.index("</pre>")]
        self.assertIn("\nurl = ", codex)
        self.assertNotIn("\\n", codex)
        hermes = text[text.index("mcp_servers:"):]
        hermes = hermes[:hermes.index("</pre>")]
        self.assertIn("\n  gemarc:", hermes)
        self.assertNotIn("\\n", hermes)

    def test_the_service_address_of_the_configurations_is_the_deployment(self):
        text = self.create().text
        self.assertIn(self.srv.base + "/mcp", text)

    def test_the_plaintext_does_not_come_back(self):
        token = PAT_RE.search(self.create().text).group(0)
        later = self.browser.get("/account").text
        self.assertNotIn(token, later)
        self.assertNotIn(token[13:56], later)

    def test_the_token_does_not_travel_in_a_url(self):
        resp = self.create()
        self.assertNotIn("gat_", resp.location or "")
        for url in re.findall(r'href="([^"]+)"', resp.text):
            self.assertNotIn("gat_", url)

    def test_the_token_is_not_stored_in_plaintext(self):
        token = PAT_RE.search(self.create().text).group(0)
        self.assertNotIn(token, self.dump())
        self.assertNotIn(token[13:56], self.dump())

    def test_the_message_of_the_creation_is_the_text_of_the_language(self):
        page = self.browser.get("/account").text
        resp = self.browser.post("/account/tokens", {"csrf": self.browser.csrf(page), "name": "laptop"})
        self.assertIn(i18n.text("zh-CN", "accounts", "account.tokencreated"), resp.text)


class TheConsoleOnlyFillsIn(ServerCase):
    """The console fills a token in; it never issues one."""

    def setUp(self):
        super().setUp()
        self.login({"sub": "chain-2", "email": EMAIL})

    def console(self, accept_language: str = "") -> str:
        headers = {"Accept-Language": accept_language} if accept_language else None
        resp = self.browser.get("/", headers=headers)
        self.assertEqual(resp.status, 200)
        return resp.text

    def test_the_console_has_a_box_to_fill_the_token_in(self):
        self.assertIn('id="token-input"', self.console())

    def test_the_console_says_where_the_token_comes_from_and_links_the_account_page(self):
        html = self.console()
        self.assertIn('href="/account"', html)
        self.assertIn(i18n.text("zh-CN", "console", "auth.source"), html)

    def test_the_console_names_the_client_variable(self):
        """The console is the other place a reader is; it must say where the token goes."""
        html = self.console()
        self.assertIn(i18n.text("zh-CN", "console", "auth.sourcenote"), html)
        self.assertIn("GEMARC_MCP_PAT", html)

    def test_the_console_has_no_way_to_issue_a_token(self):
        """The console only fills a token in: it never offers to create one."""
        self.assertNotIn("/account/tokens", self.console())

    def test_the_console_carries_the_three_tables_so_the_switcher_works_without_a_reload(self):
        html = self.console()
        for lang in i18n.LANGUAGES:
            self.assertIn(i18n.text(lang, "console", "auth.source"), html)

    def test_the_console_is_rendered_in_the_language_of_the_browser(self):
        html = self.console("ja-JP")
        self.assertIn(i18n.text("ja-JP", "console", "auth.source"), html)

    def test_the_console_offers_the_three_languages(self):
        html = self.console()
        for lang in i18n.LANGUAGES:
            self.assertIn(f'data-uilang="{lang}"', html)


class TheAccountPageSpeaksThreeLanguages(ServerCase):
    """The account page follows the order of the design: the choice, the cookie, the browser."""

    def signed_out(self, url: str = "/account", headers=None):
        browser = self.browser
        return browser.get(url, headers=headers)

    def test_the_page_answers_in_the_language_of_the_browser(self):
        resp = self.signed_out(headers={"Accept-Language": "ja-JP"})
        self.assertEqual(resp.status, 200)
        self.assertIn(i18n.text("ja-JP", "accounts", "signedout.google"), resp.text)

    def test_an_explicit_choice_wins_and_is_remembered_in_a_cookie(self):
        resp = self.signed_out("/account?lang=en-US", headers={"Accept-Language": "ja-JP"})
        self.assertEqual(resp.status, 200)
        self.assertIn(i18n.text("en-US", "accounts", "signedout.google"), resp.text)
        self.assertIn(i18n.LANG_COOKIE + "=en-US", " ".join(resp.set_cookies()))

    def test_the_cookie_remembers_the_choice(self):
        self.signed_out("/account?lang=en-US", headers={"Accept-Language": "ja-JP"})
        resp = self.signed_out(headers={"Accept-Language": "ja-JP"})
        self.assertIn(i18n.text("en-US", "accounts", "signedout.google"), resp.text)

    def test_the_browser_guess_is_not_remembered(self):
        resp = self.signed_out(headers={"Accept-Language": "ja-JP"})
        self.assertNotIn(i18n.LANG_COOKIE, " ".join(resp.set_cookies()))

    def test_the_switcher_offers_the_three_languages_as_links(self):
        resp = self.signed_out()
        for lang in i18n.LANGUAGES:
            self.assertIn(f"?lang={lang}", resp.text)

    def test_the_error_page_speaks_the_language_of_the_request(self):
        """An error page is a page too: it must answer in the language of the reader."""
        resp = self.signed_out("/auth/google/callback?code=x&state=y",
                               headers={"Accept-Language": "ja-JP"})
        self.assertEqual(resp.status, 400)
        self.assertIn('lang="ja-JP"', resp.text)
        self.assertIn(i18n.text("ja-JP", "messages", "GEMARC_ACCT_010"), resp.text)
        english = self.signed_out("/auth/google/callback?code=x&state=y",
                                  headers={"Accept-Language": "en-US"})
        self.assertIn(i18n.text("en-US", "messages", "GEMARC_ACCT_010"), english.text)

    def test_an_explicit_choice_wins_on_the_error_page(self):
        resp = self.signed_out("/auth/google/callback?code=x&state=y&lang=en-US",
                               headers={"Accept-Language": "ja-JP"})
        self.assertIn(i18n.text("en-US", "messages", "GEMARC_ACCT_010"), resp.text)


if __name__ == "__main__":
    unittest.main()
