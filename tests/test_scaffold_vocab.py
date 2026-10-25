"""The vocabulary is one source of truth for three consumers.

The prompt, the Python validator and the browser runtime all read the same
vocabulary. The browser reads it through a generated JavaScript module, so
that module can drift away from the JSON it was generated from - and when it
does, the Python side and the JavaScript side silently stop agreeing about
what an evidence level or an edge type is.

This test is the gate that catches it.
"""
import json
import unittest
from pathlib import Path

from gemarc_mcp.scaffold import sync
from gemarc_mcp.model import (
    CANONICAL_EVIDENCE_LEVELS,
    EDGE_TYPES,
    ENTITY_KINDS,
    CASE_CATEGORIES,
    canonical_evidence_level,
    evidence_rank,
)

SCAFFOLD = Path(__file__).resolve().parent.parent / "src" / "gemarc_mcp" / "scaffold"
GENERATED = SCAFFOLD / "runtime" / "core" / "vocab-data.js"


class VocabularyTests(unittest.TestCase):
    def test_generated_module_is_in_sync_with_the_json_sources(self):
        self.assertTrue(
            sync.is_in_sync(),
            "runtime/core/vocab-data.js is stale. Run: python -m gemarc_mcp.scaffold.sync")

    def test_generated_module_carries_the_current_digest(self):
        content = GENERATED.read_text(encoding="utf-8")
        self.assertIn(sync.vocab_digest(), content)

    def test_python_reads_the_same_vocabulary_files(self):
        vocabulary = sync.load_vocab()
        self.assertEqual(
            set(CANONICAL_EVIDENCE_LEVELS),
            {item["id"] for item in vocabulary["evidence-levels"]["canonical"]})
        self.assertEqual(
            set(ENTITY_KINDS),
            {item["id"] for item in vocabulary["entity-kinds"]["kinds"]})
        self.assertEqual(
            set(EDGE_TYPES),
            {item["id"] for item in vocabulary["edge-types"]["types"]})
        self.assertEqual(
            set(CASE_CATEGORIES),
            {item["id"] for item in vocabulary["case-categories"]["categories"]})

    def test_every_legacy_alias_points_at_a_canonical_level(self):
        vocabulary = sync.load_vocab()
        for alias, target in vocabulary["evidence-levels"]["legacyAliases"].items():
            self.assertIn(target, CANONICAL_EVIDENCE_LEVELS, alias)

    def test_evidence_ranks_are_strictly_ordered(self):
        vocabulary = sync.load_vocab()
        ranks = [item["rank"] for item in vocabulary["evidence-levels"]["canonical"]]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual(len(set(ranks)), len(ranks))
        self.assertEqual(evidence_rank("Implemented"), max(ranks))
        self.assertEqual(evidence_rank("Unknown"), min(ranks))

    def test_canonicalisation_is_case_insensitive(self):
        self.assertEqual(canonical_evidence_level("illustrative"), "Hypothetical")
        self.assertEqual(canonical_evidence_level("Illustrative"), "Hypothetical")
        self.assertEqual(canonical_evidence_level("IMPLEMENTED"), "Implemented")
        self.assertEqual(canonical_evidence_level("unknown"), "Unknown")
        self.assertEqual(canonical_evidence_level("nonsense"), "nonsense")

    def test_generated_module_is_valid_javascript(self):
        content = GENERATED.read_text(encoding="utf-8")
        self.assertIn("export const VOCAB", content)
        self.assertIn("export const VOCAB_DIGEST", content)
        # The payload must be parseable JSON, otherwise Node cannot import it.
        start = content.index("export const VOCAB = Object.freeze(")
        payload = content[start + len("export const VOCAB = Object.freeze("):]
        payload = payload[: payload.rindex(");")]
        self.assertIsInstance(json.loads(payload), dict)


if __name__ == "__main__":
    unittest.main()
