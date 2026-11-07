"""DD-009, the section "language handling": one source of truth for the three languages.

What is bound here:

* the three tables carry the same keys, and every key has a text in every language;
* the placeholders of a key are the same in the three languages;
* a key that is not in the table shows the key itself and is recorded as a gap;
* the language of a request follows the order of the design: the explicit choice, the
  cookie that remembers it, the browser, the default;
* the console template and the account pages ask for text by key only (no text of their own);
* every tool of the product tool sets carries the three languages in its ``_meta``.

A gap is a defect, not a detail: these tests are the gate that keeps it from reaching a user.
"""
from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path

from gemarc_mcp import i18n

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "gemarc_mcp"
CONSOLE_DIR = PACKAGE / "scaffold" / "templates" / "console"
INDEX_HTML = CONSOLE_DIR / "index.html"
CONSOLE_JS = CONSOLE_DIR / "console.js"
SPACE_BOOT_JS = CONSOLE_DIR / "console-space-boot.js"
ACCOUNTS_DIR = PACKAGE / "accounts"
#: The three sections the three tables must agree on, key by key.
SECTIONS = ("console", "accounts", "messages")
#: The tool sets this product serves; the Atlas tool sets keep the English of the code.
PRODUCT_TOOLSETS = ("full", "stateless")
#: Any CJK run (the template and the scripts must not carry text of their own).
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]+")
#: The ``{{key}}`` marker of the console template.
MARKER = re.compile(r"\{\{([a-z0-9_.]+)\}\}")


def table_path(lang: str) -> Path:
    """The file of one language table."""
    return Path(i18n.__file__).resolve().parent / f"{lang}.json"


class Tables(unittest.TestCase):
    """The three tables are the same table in three languages."""

    def test_the_three_tables_carry_the_same_keys(self):
        for section in SECTIONS:
            sets = {lang: set(i18n.section(lang, section)) for lang in i18n.LANGUAGES}
            first = sets[i18n.LANGUAGES[0]]
            for lang in i18n.LANGUAGES[1:]:
                self.assertEqual(sets[lang], first,
                                 f"{section}: {lang} differs by {sorted(sets[lang] ^ first)}")

    def test_every_key_has_a_text_in_every_language(self):
        for lang in i18n.LANGUAGES:
            for section in SECTIONS:
                for key, value in i18n.section(lang, section).items():
                    self.assertIsInstance(value, str, f"{lang}/{section}/{key}")
                    self.assertTrue(value.strip(), f"{lang}/{section}/{key} is empty")

    def test_the_placeholders_are_the_same_in_every_language(self):
        for section in SECTIONS:
            for key in sorted(i18n.section(i18n.LANGUAGES[0], section)):
                shapes = {frozenset(self.placeholders(i18n.section(lang, section).get(key, "")))
                          for lang in i18n.LANGUAGES}
                self.assertEqual(len(shapes), 1, f"{section}/{key}: {shapes}")

    def test_no_key_is_written_twice(self):
        """A duplicate key is silently kept by ``json.loads``; it must not hide a gap."""
        for lang in i18n.LANGUAGES:
            def hook(pairs):
                names = [name for name, _ in pairs]
                repeated = {name for name in names if names.count(name) > 1}
                self.assertEqual(repeated, set(), f"{lang}: {sorted(repeated)}")
                return dict(pairs)

            json.loads(table_path(lang).read_text(encoding="utf-8"), object_pairs_hook=hook)

    def test_the_tables_are_utf8_without_a_bom(self):
        for lang in i18n.LANGUAGES:
            data = table_path(lang).read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), lang)
            data.decode("utf-8")

    def placeholders(self, text: str) -> set[str]:
        """The ``{name}`` placeholders of a text."""
        return set(re.findall(r"\{(\w+)\}", text))


class Gaps(unittest.TestCase):
    """A key that is not there shows the key itself and is recorded."""

    def test_a_key_that_is_not_there_shows_the_key_itself(self):
        self.assertEqual(i18n.text("zh-CN", "console", "no.such.key"), "no.such.key")
        self.assertIn("zh-CN/console/no.such.key", i18n.missing_keys())

    def test_a_placeholder_is_filled(self):
        text = i18n.text("zh-CN", "accounts", "account.snipservice", url="https://example.invalid/mcp")
        self.assertIn("https://example.invalid/mcp", text)
        self.assertNotIn("{url}", text)

    def test_the_recorded_gap_keeps_the_language_and_the_section(self):
        i18n.text("ja-JP", "messages", "no.such.message")
        self.assertIn("ja-JP/messages/no.such.message", i18n.missing_keys())


class LanguageChoice(unittest.TestCase):
    """The order of DD-009: the choice, the cookie, the browser, the default."""

    def test_the_choice_wins_over_everything(self):
        self.assertEqual(i18n.resolve(query="ja-JP", cookie_header="gemarc_lang=en-US",
                                       accept_language="zh-CN"), "ja-JP")

    def test_the_cookie_wins_over_the_browser(self):
        self.assertEqual(i18n.resolve(cookie_header="gemarc_lang=en-US", accept_language="zh-CN"), "en-US")

    def test_the_browser_is_the_third_source(self):
        self.assertEqual(i18n.resolve(accept_language="ja"), "ja-JP")
        self.assertEqual(i18n.resolve(accept_language="en-GB"), "en-US")

    def test_the_default_is_the_last_fallback(self):
        self.assertEqual(i18n.resolve(), i18n.DEFAULT_LANG)

    def test_an_unknown_language_is_not_a_choice(self):
        self.assertIsNone(i18n.normalize("xx-YY"))
        self.assertIsNone(i18n.normalize(""))
        self.assertEqual(i18n.resolve(query="xx-YY"), i18n.DEFAULT_LANG)
        self.assertEqual(i18n.resolve(cookie_header="gemarc_lang=xx"), i18n.DEFAULT_LANG)

    def test_the_browser_guess_is_read_in_the_order_of_the_header(self):
        self.assertEqual(i18n.parse_accept_language("fr-FR,ja;q=0.9,en;q=0.8"), "ja-JP")
        self.assertEqual(i18n.parse_accept_language("en-US,en;q=0.9"), "en-US")
        self.assertIsNone(i18n.parse_accept_language("fr"))
        self.assertIsNone(i18n.parse_accept_language(None))

    def test_the_cookie_is_read_out_of_the_header(self):
        self.assertEqual(i18n.cookie_value("a=1; gemarc_lang=ja-JP; b=2", "gemarc_lang"), "ja-JP")
        self.assertIsNone(i18n.cookie_value("a=1", "gemarc_lang"))
        self.assertIsNone(i18n.cookie_value(None, "gemarc_lang"))

    def test_the_language_names_are_normalised(self):
        for written, expected in (("ja", "ja-JP"), ("JA", "ja-JP"), ("en", "en-US"), ("zh-cn", "zh-CN")):
            self.assertEqual(i18n.normalize(written), expected, written)


class TheConsoleTemplate(unittest.TestCase):
    """The console asks for text by key; it carries no text of its own."""

    def keys_of_the_template(self) -> set[str]:
        html = INDEX_HTML.read_text(encoding="utf-8")
        keys = set(MARKER.findall(html))
        keys |= set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', html))
        return keys

    def test_every_key_the_template_asks_for_is_in_every_table(self):
        keys = self.keys_of_the_template()
        self.assertTrue(keys, "the template asks for no text at all")
        for lang in i18n.LANGUAGES:
            table = i18n.section(lang, "console")
            for key in sorted(keys):
                self.assertIn(key, table, f"{lang} has no console.{key}")

    def test_the_template_has_no_text_of_its_own(self):
        """Only the names of the languages may be written in the template (each in its own language)."""
        found = set(CJK.findall(INDEX_HTML.read_text(encoding="utf-8")))
        self.assertEqual(found, {"\u4e2d\u6587", "\u65e5\u672c\u8a9e"},
                         f"the template writes text of its own: {sorted(found)}")

    def test_the_scripts_carry_no_text_of_their_own(self):
        for path in (CONSOLE_JS, SPACE_BOOT_JS):
            found = set(CJK.findall(path.read_text(encoding="utf-8")))
            self.assertEqual(found, set(), f"{path.name} writes text of its own: {sorted(found)}")

    def test_every_key_the_scripts_ask_for_is_in_every_table(self):
        for path in (CONSOLE_JS, SPACE_BOOT_JS):
            source = path.read_text(encoding="utf-8")
            for key in re.findall(r"\bt\('([^']+)'", source):
                for lang in i18n.LANGUAGES:
                    self.assertIn(key, i18n.section(lang, "console"), f"{lang} has no console.{key}")

    def test_the_switcher_offers_the_three_languages_in_their_own_language(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for lang in i18n.LANGUAGES:
            self.assertIn(f'data-uilang="{lang}"', html)
        self.assertEqual(len(re.findall(r'class="lang-btn"', html)), len(i18n.LANGUAGES))


class TheAccountPages(unittest.TestCase):
    """The account pages ask for text by key as well."""

    def keys_of_the_pages(self) -> set[str]:
        used: set[str] = set()
        for path in (ACCOUNTS_DIR / "pages.py", ACCOUNTS_DIR / "routes.py"):
            source = path.read_text(encoding="utf-8")
            used |= set(re.findall(r"""\bt\(\s*lang\s*,\s*['"]([^'"]+)['"]""", source))
            used |= set(re.findall(r"""i18n\.text\(\s*[^,]+,\s*['"]accounts['"]\s*,\s*['"]([^'"]+)['"]""", source))
        return used

    def test_every_key_the_account_pages_use_is_in_every_table(self):
        used = self.keys_of_the_pages()
        self.assertTrue(used, "the account pages ask for no text at all")
        for lang in i18n.LANGUAGES:
            table = i18n.section(lang, "accounts")
            for key in sorted(used):
                self.assertIn(key, table, f"{lang} has no accounts.{key}")

    def test_the_rendered_page_shows_no_text_outside_the_table(self):
        """A literal in the code would be a second source of truth (and a Chinese sentence on a Japanese page)."""
        from gemarc_mcp.accounts import pages

        view = pages.View(base_url="https://example.invalid", privacy_url="https://example.invalid/privacy")
        for lang in i18n.LANGUAGES:
            nonce = pages.new_nonce()
            html = pages.signed_out_page(view, nonce, lang)
            html += pages.account_page(view, nonce, lang=lang, email="reader@example.invalid", csrf="c",
                                       tokens=[], now=1_800_000_000, recent=True)
            allowed = set("".join(i18n.section(lang, "accounts").values()))
            for chunk in CJK.findall(html):
                for char in chunk:
                    self.assertIn(char, allowed, f"{lang}: the page shows {char!r}, which is not in the table")

    def test_the_page_source_carries_no_text_of_its_own(self):
        """The pages are rendered from the tables; a literal would be a second source of truth."""
        source = (ACCOUNTS_DIR / "pages.py").read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            self.assertEqual(set(CJK.findall(line)), set(), f"pages.py:{number}: {line.strip()}")


class TheToolDescriptions(unittest.TestCase):
    """``tools/list`` carries the three languages of every tool."""

    def list_tools(self, toolset: str):
        from gemarc_mcp import server

        app = server.create_server(toolset)
        return asyncio.run(app.list_tools())

    def test_every_tool_of_the_product_tool_sets_carries_the_three_languages(self):
        for toolset in PRODUCT_TOOLSETS:
            for tool in self.list_tools(toolset):
                meta = tool.model_dump(by_alias=True, exclude_none=True).get("_meta") or {}
                texts = meta.get("gemarc.dev/descriptions")
                self.assertIsNotNone(texts, f"{toolset}/{tool.name} has no descriptions")
                self.assertEqual(sorted(texts), sorted(i18n.LANGUAGES), tool.name)
                for lang, entry in texts.items():
                    self.assertTrue(entry.get("title"), f"{tool.name}/{lang} has no title")
                    self.assertTrue(entry.get("description"), f"{tool.name}/{lang} has no description")
                # the English of the tool is the description the code registers itself
                self.assertEqual(texts["en-US"]["description"].strip(), (tool.description or "").strip(), tool.name)

    def test_the_table_covers_every_tool_of_the_product_tool_sets(self):
        from gemarc_mcp import server

        for toolset in PRODUCT_TOOLSETS:
            app = server.create_server(toolset)
            for tool in asyncio.run(app.list_tools()):
                for lang in i18n.TRANSLATION_LANGS:
                    self.assertIn(tool.name, i18n.section(lang, "tools"), f"{lang} has no tools.{tool.name}")

    def test_the_table_has_no_key_of_a_tool_that_does_not_exist(self):
        from gemarc_mcp import server

        known: set[str] = set()
        for toolset in PRODUCT_TOOLSETS:
            app = server.create_server(toolset)
            known |= {tool.name for tool in asyncio.run(app.list_tools())}
        for lang in i18n.TRANSLATION_LANGS:
            for name in i18n.section(lang, "tools"):
                self.assertIn(name, known, f"{lang} translates a tool that does not exist: {name}")

    def test_a_tool_the_tables_do_not_translate_carries_no_descriptions(self):
        """The Atlas tool set keeps the English of the code; a `_meta` would only repeat it."""
        from gemarc_mcp import server

        app = server.create_server("atlas")
        for tool in asyncio.run(app.list_tools()):
            meta = tool.model_dump(by_alias=True, exclude_none=True).get("_meta") or {}
            self.assertNotIn("gemarc.dev/descriptions", meta, tool.name)
            self.assertTrue(tool.description, tool.name)


if __name__ == "__main__":
    unittest.main()
