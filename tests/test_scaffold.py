"""Scaffold builder: the fixed parts land on disk and the slots are described.

The scaffold is the thing a language model receives instead of a blank page.
Its value is entirely in what it does NOT leave to the model: the contracts,
the vocabulary, the shared runtime and the site shell all arrive ready to use,
and the manifest says exactly which slots remain.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.model import validate
from gemarc_mcp.scaffold import builder, sync


class ScaffoldBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemarc-scaffold-")
        self.root = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builds_the_static_profile(self):
        manifest = builder.build_scaffold(self.root, "demo", "static")
        self.assertEqual(manifest["profile"], "static")
        self.assertTrue((self.root / "scaffold-manifest.json").is_file())
        self.assertTrue((self.root / "FILL-IN.md").is_file())
        self.assertTrue((self.root / "index.html").is_file())
        self.assertTrue((self.root / "app.js").is_file())
        self.assertTrue((self.root / "style.css").is_file())
        self.assertTrue((self.root / "runtime" / "core" / "model.js").is_file())

    def test_builds_the_vite_profile(self):
        builder.build_scaffold(self.root, "demo", "vite")
        self.assertTrue((self.root / "package.json").is_file())
        self.assertTrue((self.root / "vite.config.js").is_file())
        self.assertTrue((self.root / "src" / "main.js").is_file())
        # The application source is shared between the two profiles.
        self.assertTrue((self.root / "app.js").is_file())

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            builder.build_scaffold(self.root, "demo", "webpack")

    def test_contracts_and_vocabulary_are_copied(self):
        manifest = builder.build_scaffold(self.root, "demo", "static")
        self.assertTrue(manifest["fixed"]["contracts"])
        self.assertTrue(manifest["fixed"]["vocab"])
        self.assertTrue((self.root / "contracts" / "canonical-model.schema.json").is_file())
        self.assertTrue((self.root / "vocab" / "evidence-levels.json").is_file())

    def test_copied_vocabulary_module_matches_the_sources(self):
        builder.build_scaffold(self.root, "demo", "static")
        copied = (self.root / "runtime" / "core" / "vocab-data.js").read_text(encoding="utf-8")
        self.assertIn(sync.vocab_digest(), copied)

    def test_manifest_lists_every_slot(self):
        manifest = builder.build_scaffold(self.root, "demo", "static")
        slot_ids = [slot["id"] for slot in manifest["slots"]]
        for required in ("meta.system", "meta.source", "nodes", "edges", "evidence", "views"):
            self.assertIn(required, slot_ids)
        for slot in manifest["slots"]:
            self.assertTrue(slot["pointer"].startswith("/"))
            self.assertTrue(slot["what"])
            self.assertTrue(slot["rule"])

    def test_model_template_is_valid(self):
        builder.build_scaffold(self.root, "demo", "static")
        model = json.loads((self.root / "model" / "architecture.template.json").read_text(encoding="utf-8"))
        result = validate(model)
        self.assertEqual(result["status"], "ok")

    def test_fill_in_document_lists_the_slots(self):
        builder.build_scaffold(self.root, "demo", "static")
        text = (self.root / "FILL-IN.md").read_text(encoding="utf-8")
        for slot in builder.SLOTS:
            self.assertIn(slot["id"], text)

    def test_static_profile_resolves_only_optional_libraries_through_the_import_map(self):
        builder.build_scaffold(self.root, "demo", "static")
        html = (self.root / "index.html").read_text(encoding="utf-8")
        # The runtime is reached by a relative path, so the import map only has
        # to carry the optional third-party libraries.
        self.assertNotIn("@gemarc/runtime", html)
        self.assertIn('"three"', html)

    def test_vite_profile_resolves_optional_libraries_from_node_modules(self):
        builder.build_scaffold(self.root, "demo", "vite")
        config = (self.root / "vite.config.js").read_text(encoding="utf-8")
        self.assertNotIn("@gemarc/runtime", config)
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertIn("three", package["dependencies"])

    def test_application_source_reaches_the_runtime_by_a_relative_path(self):
        builder.build_scaffold(self.root, "demo", "static")
        app = (self.root / "app.js").read_text(encoding="utf-8")
        self.assertIn("from './runtime/", app)
        self.assertNotIn("@gemarc/runtime", app)

    def test_both_profiles_ship_the_same_application_source(self):
        builder.build_scaffold(self.root / "a", "demo", "static")
        builder.build_scaffold(self.root / "b", "demo", "vite")
        self.assertEqual((self.root / "a" / "app.js").read_text(encoding="utf-8"),
                         (self.root / "b" / "app.js").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "a" / "style.css").read_text(encoding="utf-8"),
                         (self.root / "b" / "style.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
