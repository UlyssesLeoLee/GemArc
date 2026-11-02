"""The labels of the interface vocabulary (``protoui/i18n/{zh-CN,en-US}.json``) and the rule that the Python of ``protoui`` holds no CJK.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_i18n.py"
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from gemarc_mcp.atlas.protoui import vocab

REPO = Path(__file__).resolve().parents[2]
PROTOUI = REPO / "src" / "gemarc_mcp" / "atlas" / "protoui"
I18N = PROTOUI / "i18n"
LANGUAGES = ("zh-CN", "en-US")
CJK = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def load(lang: str) -> dict:
    return json.loads((I18N / f"{lang}.json").read_bytes().decode("utf-8"))


def flatten(table: dict) -> dict:
    return {f"{group}.{key}": value for group, items in table["vocab"].items() for key, value in items.items()}


class LabelTests(unittest.TestCase):
    def test_both_languages_label_exactly_the_55_vocabulary_ids(self):
        expected = set(vocab.label_ids())
        self.assertEqual(len(expected), 55)
        for lang in LANGUAGES:
            self.assertEqual(set(flatten(load(lang))), expected, lang)

    def test_every_label_is_a_non_empty_one_line_string_and_the_files_carry_their_language(self):
        for lang in LANGUAGES:
            table = load(lang)
            self.assertEqual(table["language"], lang)
            for key, label in flatten(table).items():
                self.assertIsInstance(label, str)
                self.assertEqual(label, label.strip(), key)
                self.assertTrue(label and "\n" not in label, key)

    def test_the_chinese_labels_are_chinese_and_the_english_labels_are_not(self):
        zh, en = flatten(load("zh-CN")), flatten(load("en-US"))
        self.assertTrue(all(CJK.search(label) for label in zh.values()), [k for k, v in zh.items() if not CJK.search(v)])
        self.assertFalse([k for k, v in en.items() if CJK.search(v)])

    def test_the_labels_of_one_group_are_distinct_in_each_language(self):
        for lang in LANGUAGES:
            for group, items in load(lang)["vocab"].items():
                self.assertEqual(len(set(items.values())), len(items), (lang, group))

    def test_the_files_are_utf8_without_bom_and_lf_only(self):
        for lang in LANGUAGES:
            data = (I18N / f"{lang}.json").read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", data)
            self.assertTrue(data.endswith(b"\n"))

    def test_there_is_no_japanese_file_because_ja_jp_falls_back_to_en_us(self):
        self.assertEqual(sorted(p.name for p in I18N.glob("*.json")), ["en-US.json", "zh-CN.json"])

    def test_the_package_data_line_of_pyproject_covers_the_i18n_files(self):
        self.assertIn('"atlas/protoui/i18n/*.json"', (REPO / "pyproject.toml").read_text(encoding="utf-8"))


class SourceTests(unittest.TestCase):
    def test_the_python_of_protoui_holds_no_cjk_literal(self):
        found = []
        for path in sorted(PROTOUI.glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if CJK.search(line):
                    found.append(f"{path.name}:{number}")
        self.assertEqual(found, [])

    def test_the_package_is_standard_library_only_and_only_the_store_reads_the_project_layout(self):
        imports = {}
        for path in sorted(PROTOUI.glob("*.py")):
            names = set(re.findall(r"^(?:from|import) ([A-Za-z_.]+)", path.read_text(encoding="utf-8"), re.M))
            imports[path.name] = names
        stdlib_or_local = {"__future__", "contextlib", "dataclasses", "difflib", "hashlib", "json", "math", "os", "re", "time", "typing", "pathlib", "xml.etree.ElementTree", "."}
        # the PNG step (task u3) needs a few more modules of the standard library, and only these two files may import them: a thread pool, temporary
        # directories and removal of trees (png.py), inflate and the header packing and a running sum for the row filters (pngread.py); subprocess stays out
        more = {"png.py": {"concurrent.futures", "shutil", "tempfile"}, "pngread.py": {"itertools", "struct", "zlib"},
                # the frontend scan (task u4): the command line (argparse, sys) and the sorted spans of comments (bisect); no process, no network
                "scan.py": {"argparse", "bisect", "sys"}}
        for name, found in imports.items():
            for module in found:
                self.assertTrue(module in stdlib_or_local or module in more.get(name, ()) or module.startswith("."), (name, module))
        for name in ("atl.py", "rows.py", "vocab.py", "model.py", "expand.py", "rules.py", "patch.py"):
            self.assertFalse({m for m in imports[name] if m in (".project", "..project", ".slices", "..slices", "...paths")}, name)

    def test_no_file_of_the_package_imports_mcp(self):
        for path in sorted(PROTOUI.glob("*.py")):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"^\s*(?:from|import) mcp\b", path.name)


if __name__ == "__main__":
    unittest.main()
