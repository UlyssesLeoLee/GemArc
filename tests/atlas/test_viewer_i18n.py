"""Viewer interface tables and the "all text lives in the tables" rule (design 9.3, M4 acceptance (d); rule XP-001).

Checks, with the standard library only:

* the three tables ``viewer/i18n/{zh-CN,ja-JP,en-US}.json`` have identical key sets (XP-001) and identical ``{placeholder}`` sets per key,
  no empty value, valid key syntax, UTF-8 without BOM, LF, valid JSON without duplicate keys;
* every key used by the runtime exists in the tables, and every table key is used by the runtime (a key is "used" when a string literal
  of a runtime module equals it; the modules write keys as literals or in literal maps, never by concatenation, so the check is static);
* no CJK character and no non-ASCII character at all in runtime JavaScript (all text is in the tables; non-ASCII punctuation is written as an
  escape), and no bare English literal in a runtime module: a string literal that reads like prose (two words, or a capitalised word) must
  be an interface key, a known token (key names, tier ids) or sit on a line marked ``// diag:`` (an author-facing diagnostic, which the bundle
  contract exempts from the interface tables, section 2.2);
* every vocabulary entry (kinds, relations, domains, trust, levels, verification, views, layers, node states) carries a label in all three
  languages, so a label never falls back to an id for lack of a translation (labels are read from the profile, not from the tables);
* the template, stylesheet and runtime contain none of the forbidden self-containment strings (``fetch(``, ``import(``, ``@import``,
  ``url(http``, remote ``src`` / ``href``) and the template carries every placeholder the build fills.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_viewer_i18n*.py"
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VIEWER = REPO / "src" / "gemarc_mcp" / "atlas" / "viewer"
I18N = VIEWER / "i18n"
SRC = VIEWER / "src"
LANGS = ("zh-CN", "ja-JP", "en-US")
KEY = re.compile(r"^[a-z][A-Za-z0-9]*(\.[A-Za-z0-9_-]+)+$")
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
RUNTIME = sorted(SRC.glob("*.js")) + sorted((SRC / "ui").glob("*.js"))
CJK = re.compile("[⺀-⿟　-ヿ㄀-ㄯ㆐-ㇿ㐀-䶿一-鿿豈-﫿＀-￯]")

# Literals that read like prose to the heuristic but are not interface text, each with its reason:
KNOWN_TOKENS = {
    "Escape", "Enter", "Home", "End", "Right", "Left", "Up", "Down",  # DOM key names
    "Selected", "Direct", "Context", "Unrelated", "Reached", "Stage", "Outside", "Hover",  # tier ids of the relevance model (drawing classes)
    "Blocked", "Denied", "Failed", "Timeout", "Waiting", "Degraded", "Completed", "Idle", "Element",  # vocabulary and engine enumeration values (data)
    "AtlasApp", "AtlasGate",  # the names of the two page globals of the documented API
    "xMidYMid meet",  # the preserveAspectRatio value of the SVG view
}


def load_table(lang: str) -> dict:
    raw = (I18N / f"{lang}.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{lang}: BOM"
    assert b"\r" not in raw, f"{lang}: CR"
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), f"{lang}: one final LF"
    pairs: list = []

    def hook(items):
        pairs.extend(k for k, _ in items)
        return dict(items)

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    assert len(pairs) == len(set(pairs)), f"{lang}: duplicate keys {sorted({k for k in pairs if pairs.count(k) > 1})}"
    return data


def strip_code(text: str):
    """Yield ``(line_number, literal_value, line_text, before)`` for every string literal of a JavaScript source (comments and regular
    expression literals skipped; a template literal yields its static parts). A small scanner, enough for the repository's style."""
    i, n, line = 0, len(text), 1
    lines = text.split("\n")
    out = []
    prev = ""  # last significant character before the current position (to tell division from a regular expression)
    prev_word = ""

    def regex_allowed() -> bool:
        return prev in "" or prev in "(,=:[!&|?{};+-*%<>~^" or prev_word in {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "else"}

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
            continue
        if c in "'\"":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1] if text[j + 1] not in "nrt" else " ")
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            out.append((line, "".join(buf), lines[line - 1], text[max(0, i - 48):i]))
            i = j + 1
            prev, prev_word = ")", ""
            continue
        if c == "`":
            j, buf, depth = i + 1, [], 0
            start_line = line
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(" ")
                    j += 2
                    continue
                if text.startswith("${", j):
                    out.append((start_line, "".join(buf), lines[start_line - 1], text[max(0, i - 48):i]))
                    buf = []
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(text[j], 0)
                        j += 1
                    continue
                if text[j] == "`":
                    break
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            out.append((start_line, "".join(buf), lines[start_line - 1], text[max(0, i - 48):i]))
            i = j + 1
            prev, prev_word = ")", ""
            continue
        if c == "/" and regex_allowed():
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            while i < n and text[i].isalpha():
                i += 1
            prev, prev_word = ")", ""
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            prev_word = text[i:j]
            prev = text[j - 1]
            i = j
            continue
        prev, prev_word = c, ""
        i += 1
    return out


def reads_like_prose(value: str) -> bool:
    if value in KNOWN_TOKENS:
        return False
    if re.search(r"[A-Za-z]{2,}[ ,;:]+[A-Za-z]{2,}", value):
        return True
    return bool(re.match(r"^[A-Z][a-z]{3,}", value))


def key_literals(value: str, namespaces: set) -> bool:
    return bool(KEY.match(value)) and value.split(".", 1)[0] in namespaces


class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = {lang: load_table(lang) for lang in LANGS}

    def test_the_three_files_exist_and_nothing_else_sits_in_the_folder(self):
        self.assertEqual(sorted(p.name for p in I18N.iterdir()), sorted(f"{lang}.json" for lang in LANGS))

    def test_key_coverage_is_identical_in_the_three_languages_XP_001(self):
        base = set(self.tables["en-US"])
        for lang in LANGS:
            keys = set(self.tables[lang])
            self.assertEqual(sorted(base - keys), [], f"{lang}: keys missing")
            self.assertEqual(sorted(keys - base), [], f"{lang}: extra keys")

    def test_placeholders_match_per_key(self):
        for key, value in self.tables["en-US"].items():
            want = sorted(PLACEHOLDER.findall(value))
            for lang in LANGS:
                self.assertEqual(sorted(PLACEHOLDER.findall(self.tables[lang][key])), want, f"{lang}:{key}")

    def test_values_are_non_empty_single_line_strings_and_keys_have_the_dotted_form(self):
        for lang, table in self.tables.items():
            for key, value in table.items():
                self.assertRegex(key, KEY, f"{lang}: {key}")
                self.assertIsInstance(value, str, f"{lang}:{key}")
                self.assertTrue(value.strip(), f"{lang}:{key}: empty")
                self.assertNotIn("\n", value, f"{lang}:{key}")
                self.assertNotRegex(value, r"</?[a-z]+[^>]*>", f"{lang}:{key}: markup in a table value (values are shown as text)")
                self.assertNotRegex(value, r"undefined|NaN|\[object", f"{lang}:{key}")

    def test_each_language_is_written_in_its_script(self):
        # a cheap guard against a table that was copied untranslated: the CJK languages must carry CJK text, English must not
        for lang in ("zh-CN", "ja-JP"):
            share = sum(1 for v in self.tables[lang].values() if CJK.search(v)) / len(self.tables[lang])
            self.assertGreater(share, 0.8, f"{lang}: only {share:.0%} of the values contain CJK text")
        self.assertFalse([k for k, v in self.tables["en-US"].items() if CJK.search(v)])
        zh_only = [k for k, v in self.tables["zh-CN"].items() if re.search(r"[぀-ヿ]", v)]
        self.assertEqual(zh_only, [], "kana in the Chinese table")
        ja_kana = sum(1 for v in self.tables["ja-JP"].values() if re.search(r"[぀-ヿ]", v))
        self.assertGreater(ja_kana, len(self.tables["ja-JP"]) * 0.5, "the Japanese table carries kana")

    def test_vocabulary_entries_carry_a_label_in_every_language(self):
        profile = json.loads((REPO / "src" / "gemarc_mcp" / "atlas" / "profiles" / "software.json").read_text(encoding="utf-8"))
        for collection in ("kinds", "relations", "domains", "trust", "levels", "verification", "views", "layers", "nodeStates"):
            for entry in profile[collection]:
                for lang in LANGS:
                    label = (entry.get("label") or {}).get(lang)
                    self.assertTrue(isinstance(label, str) and label.strip(), f"profile.{collection}[{entry['id']}] has no {lang} label")


class RuntimeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = {lang: load_table(lang) for lang in LANGS}
        cls.namespaces = {k.split(".", 1)[0] for k in cls.tables["en-US"]}
        cls.sources = {p: p.read_text(encoding="utf-8") for p in RUNTIME}

    def test_the_runtime_has_modules_to_check(self):
        names = {p.name for p in RUNTIME}
        self.assertTrue({"shell.js", "dom.js", "tab-qa.js", "tab-doc.js", "tab-twod.js", "model-access.js", "assemble.js"} <= names)

    def test_no_cjk_and_no_non_ascii_character_in_runtime_javascript(self):
        for path, text in self.sources.items():
            self.assertIsNone(CJK.search(text), f"{path.name}: CJK text in runtime JavaScript (it belongs in viewer/i18n)")
            bad = [(n, ch) for n, line in enumerate(text.split("\n"), 1) for ch in line if ord(ch) > 127]
            self.assertEqual(bad[:3], [], f"{path.name}: non-ASCII character (write it as an escape)")

    def test_no_bare_english_literal_in_runtime_javascript(self):
        offences = []
        for path, text in self.sources.items():
            for line, value, line_text, before in strip_code(text):
                if key_literals(value, self.namespaces) or not reads_like_prose(value):
                    continue
                if "// diag:" in line_text or "new Error(" in line_text:
                    continue  # author-facing diagnostic (bundle contract 2.2); a thrown Error message is recorded in the error sink, never shown as text
                if re.search(r"(class|style|d|points|transform|viewBox|aria-label|role|type|id)\s*:\s*[^,]*$|cls\s*=\s*$", before) and re.fullmatch(r"[a-z0-9 :;.\-_,()#%]+", value):
                    continue  # CSS class lists, inline style, SVG data: attribute values, not text
                if "FONT_FAMILY =" in line_text or "font" in before[-20:].lower() or "System" in value and "sans-serif" in value:
                    continue  # CSS font stacks
                if value.startswith(("--", "(")) or re.match(r"^[a-z]+:\s*(fixed|absolute|-?\d)", value):
                    continue  # inline style text
                if "warnings.add(" in line_text or ".add(VIEW_WARNING" in line_text or "vocab.warnings.add" in line_text or "warnings?.add(" in line_text:
                    continue  # warning details of the pure modules: author-facing diagnostics (contract sections 2.2 and 6)
                offences.append(f"{path.name}:{line}: {value!r}")
        self.assertEqual(offences, [], "bare English literals (put the text in viewer/i18n, or mark a diagnostic with // diag:)")

    def test_every_key_used_by_the_runtime_is_defined(self):
        defined = set(self.tables["en-US"])
        used = set()
        for path, text in self.sources.items():
            for _line, value, _l, _b in strip_code(text):
                if key_literals(value, self.namespaces):
                    used.add(value)
        self.assertEqual(sorted(used - defined), [], "keys used in the runtime but not defined in the tables")

    def test_every_table_key_is_used_by_the_runtime(self):
        defined = set(self.tables["en-US"])
        used = set()
        for text in self.sources.values():
            for _line, value, _l, _b in strip_code(text):
                if value in defined:
                    used.add(value)
        self.assertEqual(sorted(defined - used), [], "keys defined in the tables but never used (remove them or use them)")

    def test_keys_are_never_built_by_concatenation(self):
        # a key built from pieces cannot be checked statically; the runtime writes keys as whole literals
        for path, text in self.sources.items():
            if path.name == "dom.js":
                continue
            for line in text.split("\n"):
                code = re.sub(r"//.*$", "", line)
                self.assertIsNone(re.search(r"""\bt\(\s*['"][a-z][A-Za-z0-9.]*\.['"]\s*\+""", code), f"{path.name}: {line.strip()}")
                self.assertIsNone(re.search(r"""\bt\(\s*`""", code), f"{path.name}: template-literal key: {line.strip()}")

    def test_scanner_self_check(self):
        sample = "const a = 'two words'; // 'ignored'\nconst r = /['\"x y]/g; const b = `p ${x} q`; const c = \"Loading\"; const d = 'tab.1d'; /* 'no' */\n"
        values = [v for _l, v, _t, _b in strip_code(sample)]
        self.assertEqual(values, ["two words", "p ", " q", "Loading", "tab.1d"])
        self.assertTrue(reads_like_prose("two words") and reads_like_prose("Loading") and not reads_like_prose("tab.1d") and not reads_like_prose("Escape") and not reads_like_prose("aria-label"))


class PageFilesTests(unittest.TestCase):
    TEMPLATE = SRC / "atlas.template.html"
    PLACEHOLDERS = ["<!--ATLAS_LANG-->", "<!--ATLAS_SELFTEST-->", "<!--ATLAS_TITLE-->", "<!--ATLAS_STYLE-->", "<!--ATLAS_DATA-->", "<!--ATLAS_RUNTIME-->"]

    def test_the_template_has_every_placeholder_exactly_once_in_order(self):
        text = self.TEMPLATE.read_text(encoding="utf-8")
        positions = [text.find(p) for p in self.PLACEHOLDERS]
        self.assertTrue(all(p >= 0 for p in positions), positions)
        for p in self.PLACEHOLDERS:
            self.assertEqual(text.count(p), 1, p)
        # the self-test comes first in the head (it must arm the error capture before any other script), data before runtime
        self.assertLess(text.find("<!--ATLAS_SELFTEST-->"), text.find("<!--ATLAS_STYLE-->"))
        self.assertLess(text.find("<!--ATLAS_DATA-->"), text.find("<!--ATLAS_RUNTIME-->"))
        self.assertIn('id="atlas-root"', text)

    def test_page_files_are_self_contained_lf_utf8(self):
        forbidden = [r"\bfetch\s*\(", r"\bimport\s*\(", r"@import", r"url\(\s*['\"]?https?:", r"\bsrc\s*=\s*['\"]?(https?:)?//", r"\bhref\s*=\s*['\"]?(https?:)?//"]
        for path in [self.TEMPLATE, SRC / "atlas.css", *RUNTIME]:
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, path.name)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), path.name)
            text = raw.decode("utf-8")
            for pattern in forbidden:
                self.assertIsNone(re.search(pattern, text, re.I), f"{path.name}: {pattern}")
        css = (SRC / "atlas.css").read_text(encoding="utf-8")
        self.assertNotIn("url(", css)
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn(':root[data-scheme="dark"]', css)


if __name__ == "__main__":
    unittest.main()
