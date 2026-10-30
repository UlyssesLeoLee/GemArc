"""The release-time viewer bundler (``tools/bundle_viewer.py``, design 9.3) and its staleness test (M4 acceptance (f)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_bundle*.py"

* ``StalenessTests``: the committed ``viewer/dist`` and ``viewer/SOURCES.sha256`` equal a fresh bundle of the sources. Editing a
  source without re-bundling turns this red (and ``python tools/bundle_viewer.py`` makes it green again).
* ``BundlerTests``: behaviour on a temporary copy of the viewer tree (edits are made THERE, never in the repository): private
  top-level names cannot collide, a wrong import or an unsupported form is a hard error naming the line, CRLF and BOM do not change
  the output, two bundles are byte-identical, the runtime compiles and runs a module graph (Node, skipped LOUDLY when absent).
Synthetic content only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "bundle_viewer.py"
VIEWER = ROOT / "src" / "gemarc_mcp" / "atlas" / "viewer"


def load_tool():
    spec = importlib.util.spec_from_file_location("bundle_viewer_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BV = load_tool()
NODE = shutil.which("node")


class StalenessTests(unittest.TestCase):
    def test_the_committed_bundle_is_fresh(self):
        problems = BV.check()
        self.assertEqual(problems, [], "the viewer sources changed without a re-bundle: run python tools/bundle_viewer.py\n" + "\n".join(problems))

    def test_sources_record_every_input_and_every_output(self):
        recorded = BV.parse_sources((VIEWER / BV.SOURCES_OUT).read_text(encoding="utf-8"))
        for rel in BV.source_paths(VIEWER):
            self.assertIn(rel, recorded, rel)
            self.assertEqual(recorded[rel], hashlib.sha256((VIEWER / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest(), rel)
        for rel in BV.OUTPUTS:
            self.assertIn(rel, recorded)
        self.assertIn(BV.TOOL_ENTRY, recorded, "the bundler itself is an input: changing its logic makes the outputs stale")

    def test_the_command_line_check_exits_zero_on_a_fresh_bundle(self):
        done = subprocess.run([sys.executable, str(TOOL), "--check"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class CopyCase(unittest.TestCase):
    """A temporary copy of the viewer tree to edit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.viewer = Path(self._tmp.name) / "viewer"
        shutil.copytree(VIEWER, self.viewer, ignore=shutil.ignore_patterns("dist", "SOURCES.sha256", "__pycache__"))
        BV.write(self.viewer)

    def src(self, rel: str) -> Path:
        return self.viewer / "src" / rel

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.src(rel)
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


class StalenessDetectionTests(CopyCase):
    def test_a_fresh_copy_is_fresh(self):
        self.assertEqual(BV.check(self.viewer), [])

    def test_editing_a_source_without_rebundling_is_stale(self):
        self.edit("search.js", "export const SEARCH_TABS", "const editedMarker = 1;\nexport const SEARCH_TABS")
        problems = BV.check(self.viewer)
        self.assertTrue(any("src/search.js" in p for p in problems), problems)
        self.assertTrue(any("atlas-runtime.js" in p for p in problems), problems)
        BV.write(self.viewer)
        self.assertEqual(BV.check(self.viewer), [])

    def test_editing_the_css_the_template_or_a_table_is_stale(self):
        for rel, path in (("css", self.viewer / "src" / "atlas.css"), ("template", self.viewer / "src" / "atlas.template.html"),
                          ("table", self.viewer / "i18n" / "en-US.json")):
            before = path.read_bytes()
            path.write_bytes(before + b"\n" if rel != "table" else before.replace(b"{", b"{ ", 1))
            self.assertNotEqual(BV.check(self.viewer), [], rel)
            path.write_bytes(before)
            self.assertEqual(BV.check(self.viewer), [], rel)

    def test_a_hand_edited_output_is_stale(self):
        out = self.viewer / BV.RUNTIME_OUT
        out.write_bytes(out.read_bytes() + b"// hand edit\n")
        self.assertTrue(any("atlas-runtime.js" in p for p in BV.check(self.viewer)))

    def test_a_missing_output_or_record_is_stale(self):
        (self.viewer / BV.CSS_OUT).unlink()
        self.assertTrue(any("atlas.css is missing" in p for p in BV.check(self.viewer)))
        (self.viewer / BV.SOURCES_OUT).unlink()
        self.assertTrue(any("SOURCES.sha256 is missing" in p for p in BV.check(self.viewer)))

    def test_a_new_source_file_is_stale_until_bundled(self):
        self.src("extra.js").write_text("export const EXTRA_VALUE = 1;\n", encoding="utf-8", newline="\n")
        self.assertNotEqual(BV.check(self.viewer), [])


class BundlerTests(CopyCase):
    def runtime(self) -> str:
        return (self.viewer / BV.RUNTIME_OUT).read_text(encoding="utf-8")

    def test_two_bundles_are_byte_identical(self):
        first = BV.bundle(self.viewer)
        second = BV.bundle(self.viewer)
        self.assertEqual(first, second)
        self.assertEqual(BV.write(self.viewer), [], "nothing changes when nothing changed")

    def test_crlf_and_bom_do_not_change_the_output(self):
        before = BV.bundle(self.viewer)
        for path in (self.viewer / "src").rglob("*.js"):
            path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(BV.bundle(self.viewer), before)

    def test_output_is_ascii_lf_and_has_no_script_hazard(self):
        text = self.runtime()
        self.assertTrue(text.isascii())
        self.assertNotIn("\r", text)
        for needle in ("</script", "<!--", "</style"):
            self.assertNotIn(needle, text.lower())
        self.assertTrue(text.endswith("__mod_ui_main.bootAtlas();\n})();\n"))

    def test_every_module_is_in_its_own_function_scope_in_dependency_order(self):
        text = self.runtime()
        order = [line[len("// ---- "):] for line in text.split("\n") if line.startswith("// ---- ")]
        self.assertEqual(sorted(order), sorted(BV.module_files(self.viewer / "src")))
        position = {rel: i for i, rel in enumerate(order)}
        mods = BV.load_modules(self.viewer / "src")
        for rel, mod in mods.items():
            for _names, target in mod.imports:
                self.assertLess(position[target], position[rel], f"{target} must come before {rel}")

    def test_private_top_level_names_of_two_modules_do_not_collide(self):
        self.src("alpha.js").write_text("const helper = () => 1;\nexport const alphaValue = () => helper();\n", encoding="utf-8", newline="\n")
        self.src("beta.js").write_text("const helper = () => 2;\nexport const betaValue = () => helper();\n", encoding="utf-8", newline="\n")
        self.edit("ui/main.js", "import { createApp } from './shell.js';",
                  "import { createApp } from './shell.js';\nimport { alphaValue } from '../alpha.js';\nimport { betaValue } from '../beta.js';")
        text = BV.bundle_runtime(BV.load_modules(self.viewer / "src"))
        self.assertEqual(text.count("const helper ="), 2)
        if not NODE:
            self.skipTest("NODE NOT FOUND: the collision bundle was built but NOT executed")
        probe = text.replace("__mod_ui_main.bootAtlas();", "globalThis.__probe = [__mod_alpha.alphaValue(), __mod_beta.betaValue()];")
        script = Path(self._tmp.name) / "probe.js"
        script.write_text(probe + "\nconsole.log(JSON.stringify(globalThis.__probe));\n", encoding="utf-8", newline="\n")
        run = subprocess.run([NODE, str(script)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [1, 2])

    def test_an_import_of_a_name_the_target_does_not_export_is_an_error(self):
        self.edit("ui/main.js", "import { createApp } from './shell.js';", "import { createApp, noSuchExport } from './shell.js';")
        with self.assertRaises(BV.BundleError) as cm:
            BV.bundle(self.viewer)
        self.assertIn("noSuchExport", str(cm.exception))

    def test_unsupported_forms_are_errors_that_name_the_line(self):
        cases = [
            ("export { a };", "unsupported export"),
            ("export default 1;", "unsupported export"),
            ("import * as ns from './coords.js';", "unsupported import"),
            ("import { a as b } from './coords.js';", "aliases"),
            ("import {\n a } from './coords.js';", "unsupported import"),
            ("const café = 1;", "non-ASCII"),
            ("const s = '</script>';", "</script"),
        ]
        for line, expect in cases:
            with self.subTest(line=line):
                path = self.src("coords.js")
                original = path.read_text(encoding="utf-8")
                path.write_text(original + "\n" + line + "\n", encoding="utf-8", newline="\n")
                try:
                    with self.assertRaises(BV.BundleError) as cm:
                        BV.bundle(self.viewer)
                    self.assertIn(expect, str(cm.exception))
                    self.assertIn("coords.js:", str(cm.exception))
                finally:
                    path.write_text(original, encoding="utf-8", newline="\n")

    def test_an_import_cycle_and_a_missing_module_are_errors(self):
        self.src("cyc1.js").write_text("import { cyc2 } from './cyc2.js';\nexport const cyc1 = () => cyc2;\n", encoding="utf-8", newline="\n")
        self.src("cyc2.js").write_text("import { cyc1 } from './cyc1.js';\nexport const cyc2 = () => cyc1;\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BV.BundleError) as cm:
            BV.bundle(self.viewer)
        self.assertIn("cycle", str(cm.exception))
        self.src("cyc1.js").unlink()
        self.src("cyc2.js").write_text("import { cyc1 } from './nowhere.js';\nexport const cyc2 = () => cyc1;\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BV.BundleError) as cm:
            BV.bundle(self.viewer)
        self.assertIn("nowhere", str(cm.exception))

    def test_the_runtime_text_drops_comment_lines_blank_lines_and_indentation_but_keeps_trailing_comments(self):
        body = ["// a note", "", "  const a = 1; // trailing stays", "  /** a block", "   * more", "   */", "  /* one line */", "  function f() {", "    return a;", "  }"]
        self.assertEqual(BV.strip_body("x.js", body), ["const a = 1; // trailing stays", "function f() {", "return a;", "}"])
        self.assertEqual(BV.strip_body("x.js", ["const re = /[`]/; const s = '`'; const t = `a ${b}`;"]), ["const re = /[`]/; const s = '`'; const t = `a ${b}`;"])

    def test_a_template_literal_that_spans_lines_is_a_hard_error_naming_the_line(self):
        with self.assertRaises(BV.BundleError) as cm:
            BV.strip_body("x.js", ["const a = 1;", "const t = `one", "  two`;"])
        self.assertIn("x.js:2", str(cm.exception))
        with self.assertRaises(BV.BundleError):
            BV.strip_body("x.js", ["/* never closed"])

    def test_the_style_sheet_loses_comments_and_blank_lines_only(self):
        css = "/* head\n   more */\n\n.a { color: red; } /* tail */\n\n.b { margin: 0; }\n"
        self.assertEqual(BV.strip_css(css), ".a { color: red; }\n.b { margin: 0; }\n")

    def test_the_committed_runtime_has_no_comment_only_lines_and_no_indentation(self):
        text = (VIEWER / BV.RUNTIME_OUT).read_text(encoding="utf-8")
        lines = [ln for ln in text.split("\n") if ln]
        self.assertTrue(all(ln == ln.lstrip() for ln in lines), "indentation is dropped")
        stray = [ln for ln in lines if ln.startswith("//") and not ln.startswith("// ----")]
        self.assertEqual(stray, [], "only the module banners are comment lines")

    def test_the_runtime_compiles_in_node(self):
        if not NODE:
            self.skipTest("NODE NOT FOUND: the runtime syntax was NOT compiled")
        code = "const fs=require('fs');new (require('vm').Script)(fs.readFileSync(process.argv[1],'utf8'));console.log('ok')"
        run = subprocess.run([NODE, "-e", code, str(VIEWER / BV.RUNTIME_OUT)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_interface_tables_are_embedded_for_all_three_languages_with_equal_keys(self):
        data = json.loads((self.viewer / BV.I18N_OUT).read_text(encoding="utf-8"))
        self.assertEqual(data["languages"], ["zh-CN", "ja-JP", "en-US"])
        keys = [set(data["tables"][lang]) for lang in data["languages"]]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[1], keys[2])
        for lang in data["languages"]:
            self.assertEqual(data["tables"][lang], json.loads((VIEWER / "i18n" / f"{lang}.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
