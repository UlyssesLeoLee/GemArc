"""The fixed texts of the reading surface (design A3 5.9, RS-118, D-24): three languages with one key set, ja-JP with the en-US texts, no CJK literal and no
unknown key in the Python of ``reading/``, no word a page must not use (RS-118, T-class).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_i18n_read
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.reading import check as C
from gemarc_mcp.atlas.reading import envelope as E
from gemarc_mcp.atlas.reading import names as N

READING = Path(__file__).resolve().parents[2] / "src" / "gemarc_mcp" / "atlas" / "reading"
CJK = re.compile("[　-〿぀-ヿ㐀-鿿＀-￯]")
FORBIDDEN = {"zh-CN": ("已验证", "已核实", "已确认"), "en-US": ("verified", "validated", "confirmed")}
USED = ("tx", "tx_list", "raw", "has_text", "pattern")


def keys_of(node, prefix="") -> set:
    if hasattr(node, "keys"):
        out = set()
        for k in node:
            out |= keys_of(node[k], prefix + k + ".")
        return out
    return {prefix[:-1]}


class KeyTests(unittest.TestCase):
    def test_the_three_languages_have_one_key_set_in_every_object(self):
        base = keys_of(T.tables("en-US"))
        for lang in ("zh-CN", "ja-JP"):
            self.assertEqual(keys_of(T.tables(lang)), base, lang)

    def test_japanese_reading_texts_are_the_english_ones(self):
        self.assertEqual(T.tables("ja-JP")["read"], T.tables("en-US")["read"])
        self.assertEqual(N.text_lang("ja-JP"), "en-US")
        self.assertEqual(E.texts("ja-JP"), E.texts("en-US"))

    def test_the_flat_view_of_the_frame_texts_is_what_the_json_says(self):
        for lang in ("zh-CN", "en-US"):
            for key, value in E.TEXT[lang].items():
                self.assertEqual(T.lookup(lang, key), value)
        self.assertEqual(N.FAMILIES["S"].title_zh, T.lookup("zh-CN", "read.family.S"))

    def test_every_placeholder_of_a_template_is_the_same_in_both_languages(self):
        zh, en = T.tables("zh-CN")["read"], T.tables("en-US")["read"]

        def walk(a, b, path):
            if isinstance(a, str):
                self.assertEqual(sorted(re.findall(r"\{(\w+)\}", a)), sorted(re.findall(r"\{(\w+)\}", b)), path)
            elif hasattr(a, "keys"):
                for k in a:
                    walk(a[k], b[k], path + "." + k)

        walk(zh, en, "read")


class SourceTests(unittest.TestCase):
    def test_no_cjk_literal_in_the_python_of_the_reading_package(self):
        for path in sorted(READING.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                    and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                    self.assertIsNone(CJK.search(node.value), f"{path.name}: a CJK literal {node.value[:20]!r}")

    def test_every_key_the_code_asks_for_exists_in_both_languages(self):
        asked = set()
        for path in sorted(READING.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in USED and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        asked.add(arg.value)
                    elif isinstance(arg, ast.BinOp) and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str):
                        asked.add(arg.left.value + "*")
        self.assertGreater(len(asked), 60)
        for lang in ("zh-CN", "en-US"):
            have = keys_of(T.tables(lang)["read"], "")
            for key in sorted(asked):
                if key.endswith("*"):
                    self.assertTrue(any(h.startswith(key[:-1]) for h in have), (lang, key))
                elif key in ("covers.line_open",):
                    continue
                else:
                    self.assertTrue(key in have or any(h.startswith(key + ".") for h in have), (lang, key))


class ForbiddenWordTests(unittest.TestCase):
    """RS-118 (T-class): a generated sentence never claims more than the checks did."""

    def test_no_text_of_the_skeleton_or_the_reading_surface_uses_a_forbidden_word(self):
        for lang, words in FORBIDDEN.items():
            tables = T.tables(lang)
            self.assertEqual(C.forbidden_hits(lang, tables, words), [], lang)
        self.assertEqual(C.forbidden_hits("ja-JP", T.tables("ja-JP"), FORBIDDEN["en-US"]), [])

    def test_the_fact_note_says_a_structure_check_not_a_verification(self):
        self.assertIn("已通过结构检查", T.tr_list("zh-CN", "skeleton.facts_note")[0])
        self.assertIn("passed the structure checks", T.tr_list("en-US", "skeleton.facts_note")[0])
        self.assertIn("構造の点検を通った", T.tr_list("ja-JP", "skeleton.facts_note")[0])

    def test_the_detector_finds_a_word_and_ignores_case(self):
        self.assertEqual(C.forbidden_hits("en-US", {"a": {"b": "It is Verified."}}, FORBIDDEN["en-US"]), [("a.b", "verified")])


if __name__ == "__main__":
    unittest.main()
