"""Regression for the strict-mode evidence defect in ``gemarc_mcp.model.validate``.

Before the fix, strict mode (and the lenient "no evidence" warning) read the free-text field ``evidence``
instead of the canonical ``evidenceIds`` field of ``scaffold/contracts/node.schema.json`` and
``edge.schema.json``. A model whose nodes and edges cited real evidence records through ``evidenceIds``
and carried an Implemented/Observed level was therefore rejected by ``strict=True`` with
GEMARC_EVIDENCE_002 / GEMARC_EVIDENCE_004, and every such node still drew a spurious "no evidence" warning.

The ids and names below are synthetic. Run:
PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_strict_evidence_regression*.py"
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gemarc_mcp import model as canonical_model
from gemarc_mcp.model import validate

CONTRACTS = Path(canonical_model.__file__).resolve().parent / "scaffold" / "contracts"


def strict_grade_model() -> dict:
    """The smallest model that clears every strict gate except possibly the evidence-reference one."""
    kind = "Service" if "Service" in canonical_model.ENTITY_KINDS else sorted(canonical_model.ENTITY_KINDS)[0]
    edge_type = "calls" if "calls" in canonical_model.EDGE_TYPES else sorted(canonical_model.EDGE_TYPES)[0]
    return {
        "meta": {"system": "synthetic-desk", "evidenceState": "Implemented"},
        "evidence": [
            {"id": "EVD-1", "level": "Implemented", "source": {"locator": "src/alpha.py#create"}},
            {"id": "EVD-2", "level": "Implemented", "source": {"locator": "src/beta.py#find"}},
        ],
        "nodes": [
            {"id": "svc.alpha", "name": "Alpha service", "kind": kind, "verification": "Implemented", "evidenceIds": ["EVD-1"]},
            {"id": "svc.beta", "name": "Beta service", "kind": kind, "verification": "Observed", "evidenceIds": ["EVD-2"]},
        ],
        "edges": [
            {"id": "e.alpha-beta", "sourceId": "svc.alpha", "targetId": "svc.beta", "type": edge_type,
             "verification": "Implemented", "evidenceIds": ["EVD-1", "EVD-2"]},
        ],
        "views": [],
        "scenarios": [],
    }


def ids(items, key="id"):
    return {item[key] for item in items}


class SchemaFieldTests(unittest.TestCase):
    def test_the_canonical_schemas_name_evidence_ids_not_evidence(self):
        for name in ("node.schema.json", "edge.schema.json"):
            schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
            self.assertIn("evidenceIds", schema["properties"], name)
            self.assertNotIn("evidence", schema["properties"], name)


class StrictEvidenceTests(unittest.TestCase):
    def test_strict_accepts_nodes_and_edges_that_cite_evidence_ids(self):
        result = validate(strict_grade_model(), strict=True)
        self.assertEqual(result["errors"], [], result["errors"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(ids(result["errors"]) & {"GEMARC_EVIDENCE_002", "GEMARC_EVIDENCE_004"})

    def test_lenient_mode_does_not_warn_about_missing_evidence_when_ids_are_present(self):
        for strict in (False, True):
            result = validate(strict_grade_model(), strict=strict)
            self.assertFalse(ids(result["warnings"]) & {"GEMARC_EVIDENCE_003", "GEMARC_EVIDENCE_005"},
                             f"strict={strict}: {result['warnings']}")

    def test_strict_rejects_a_node_without_evidence_ids(self):
        model = strict_grade_model()
        model["nodes"][0]["evidenceIds"] = []
        result = validate(model, strict=True)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual([e["entityId"] for e in result["errors"] if e["id"] == "GEMARC_EVIDENCE_002"], ["svc.alpha"])

    def test_strict_rejects_an_edge_without_evidence_ids(self):
        model = strict_grade_model()
        model["edges"][0]["evidenceIds"] = []
        result = validate(model, strict=True)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual([e["entityId"] for e in result["errors"] if e["id"] == "GEMARC_EVIDENCE_004"], ["e.alpha-beta"])

    def test_free_text_evidence_is_not_a_reference_in_strict_mode(self):
        """A prose note is not a citation: it must not satisfy strict mode on its own."""
        model = strict_grade_model()
        model["nodes"][0]["evidenceIds"] = []
        model["nodes"][0]["evidence"] = "documented in the design notes"
        model["edges"][0]["evidenceIds"] = []
        model["edges"][0]["evidence"] = "documented in the design notes"
        result = validate(model, strict=True)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(ids(result["errors"]) & {"GEMARC_EVIDENCE_002", "GEMARC_EVIDENCE_004"},
                         {"GEMARC_EVIDENCE_002", "GEMARC_EVIDENCE_004"})

    def test_a_level_below_observed_still_fails_strict_even_with_evidence_ids(self):
        model = strict_grade_model()
        model["nodes"][0]["verification"] = "Designed"
        result = validate(model, strict=True)
        self.assertEqual([e["entityId"] for e in result["errors"] if e["id"] == "GEMARC_EVIDENCE_002"], ["svc.alpha"])

    def test_dangling_evidence_reference_is_an_error_in_both_modes(self):
        model = strict_grade_model()
        model["nodes"][1]["evidenceIds"] = ["EVD-404"]
        for strict in (False, True):
            result = validate(model, strict=strict)
            self.assertIn("GEMARC_EVIDENCE_008", ids(result["errors"]), f"strict={strict}")


class LenientCompatibilityTests(unittest.TestCase):
    """The lenient warning keeps accepting the legacy free-text note (sample_model still uses it)."""

    def test_legacy_free_text_silences_the_lenient_warning(self):
        model = strict_grade_model()
        for record in (*model["nodes"], *model["edges"]):  # every record carries only the legacy note, none cites a record id
            record["evidenceIds"] = []
            record["evidence"] = "legacy note"
        result = validate(model)
        self.assertFalse(ids(result["warnings"]) & {"GEMARC_EVIDENCE_003", "GEMARC_EVIDENCE_005"}, result["warnings"])

    def test_no_evidence_of_any_kind_still_warns(self):
        model = strict_grade_model()
        model["nodes"][0]["evidenceIds"] = []
        model["edges"][0]["evidenceIds"] = []
        result = validate(model)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(ids(result["warnings"]) & {"GEMARC_EVIDENCE_003", "GEMARC_EVIDENCE_005"},
                         {"GEMARC_EVIDENCE_003", "GEMARC_EVIDENCE_005"})

    def test_the_input_model_is_not_mutated(self):
        model = strict_grade_model()
        snapshot = copy.deepcopy(model)
        validate(model, strict=True)
        self.assertEqual(model, snapshot)


if __name__ == "__main__":
    unittest.main()
