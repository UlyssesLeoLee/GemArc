"""Backward compatibility for 0.1.x models.

0.2.0 renamed the relation endpoints to sourceId / targetId and the relation
type to `type`, and replaced the four evidence levels with the six canonical
ones. A 0.1.x model must still load, validate and normalise. These tests pin
that promise in both directions.
"""
import copy
import unittest

from gemarc_mcp.model import (
    canonical_evidence_level,
    evidence_rank,
    normalized_model,
    validate,
)
from gemarc_mcp.service import sample_model


def legacy_model():
    """A 0.1.x shaped model: source/target/kind and the old evidence levels."""
    model = copy.deepcopy(sample_model('demo'))
    for node in model['nodes']:
        node['kind'] = 'adapter' if node['id'] == 'sensor' else 'service'
        node['verification'] = 'confirmed'
    for edge in model['edges']:
        edge['source'] = edge.pop('sourceId')
        edge['target'] = edge.pop('targetId')
        edge['kind'] = edge.pop('type')
        edge['verification'] = 'confirmed'
    model['meta']['evidenceState'] = 'confirmed'
    model['views'][0].pop('diagramType', None)
    model['views'][0]['edgeKinds'] = ['event']
    return model


class LegacyCompatibilityTests(unittest.TestCase):
    def test_legacy_model_still_validates(self):
        result = validate(legacy_model())
        self.assertEqual(result['status'], 'ok')

    def test_normalisation_rewrites_legacy_edge_keys(self):
        normalized = normalized_model(legacy_model())
        for edge in normalized['edges']:
            self.assertIn('sourceId', edge)
            self.assertIn('targetId', edge)
            self.assertNotIn('source', edge)
            self.assertNotIn('target', edge)
            self.assertNotIn('kind', edge)
            self.assertIn('type', edge)

    def test_legacy_evidence_levels_are_canonicalised(self):
        normalized = normalized_model(legacy_model())
        self.assertEqual(normalized['meta']['evidenceState'], 'Implemented')
        for node in normalized['nodes']:
            self.assertEqual(node['verification'], 'Implemented')

    def test_evidence_alias_table(self):
        self.assertEqual(canonical_evidence_level('confirmed'), 'Implemented')
        self.assertEqual(canonical_evidence_level('assumed'), 'Inferred')
        self.assertEqual(canonical_evidence_level('illustrative'), 'Hypothetical')
        self.assertEqual(canonical_evidence_level('unknown'), 'Unknown')
        self.assertEqual(canonical_evidence_level('Observed'), 'Observed')
        self.assertEqual(canonical_evidence_level('nonsense'), 'nonsense')

    def test_evidence_rank_ordering(self):
        self.assertGreater(evidence_rank('Implemented'), evidence_rank('Observed'))
        self.assertGreater(evidence_rank('Observed'), evidence_rank('Designed'))
        self.assertGreater(evidence_rank('Designed'), evidence_rank('Inferred'))
        self.assertGreater(evidence_rank('Inferred'), evidence_rank('Hypothetical'))
        self.assertGreater(evidence_rank('Hypothetical'), evidence_rank('Unknown'))
        self.assertEqual(evidence_rank('nonsense'), 0)

    def test_normalisation_does_not_mutate_the_input(self):
        original = legacy_model()
        snapshot = copy.deepcopy(original)
        normalized_model(original)
        self.assertEqual(original, snapshot)

    def test_missing_diagram_type_is_a_warning_not_an_error(self):
        result = validate(legacy_model())
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(any(w['id'] == 'GEMARC_VIEW_006' for w in result['warnings']))

    def test_unregistered_kinds_are_warnings_outside_strict_mode(self):
        result = validate(legacy_model())
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(any(w['id'] == 'GEMARC_MODEL_022' for w in result['warnings']))

    def test_strict_mode_rejects_unregistered_kinds(self):
        result = validate(legacy_model(), strict=True)
        self.assertEqual(result['status'], 'invalid')
        self.assertTrue(any(e['id'] == 'GEMARC_MODEL_022' for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
