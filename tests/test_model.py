import copy
import unittest
from gemarc_mcp.model import validate
from gemarc_mcp.service import sample_model

class ModelTests(unittest.TestCase):
    def setUp(self): self.m = sample_model('demo')
    def test_valid_sample(self): self.assertEqual(validate(self.m)['status'], 'ok')
    def test_strict_requires_provenance(self): self.assertEqual(validate(self.m, strict=True)['status'], 'invalid')
    def test_reject_dangling_edge(self):
        self.m['edges'][0]['target'] = 'missing'
        self.assertEqual(validate(self.m)['status'], 'invalid')
    def test_reject_duplicate_id(self):
        self.m['nodes'].append(copy.deepcopy(self.m['nodes'][0]))
        self.assertEqual(validate(self.m)['status'], 'invalid')
    def test_reject_invalid_scenario(self):
        self.m['scenarios'][0]['rules'][0]['target'] = 'missing'
        self.assertEqual(validate(self.m)['status'], 'invalid')
    def test_malformed_scenario_items_no_crash(self):
        self.m['scenarios'][0]['events'] = [None]
        self.m['scenarios'][0]['rules'] = [None]
        self.assertEqual(validate(self.m)['status'], 'invalid')
    def test_malformed_edge_endpoint_no_crash(self):
        self.m['edges'][0]['source'] = []
        self.assertEqual(validate(self.m)['status'], 'invalid')
    def test_missing_view_is_allowed(self):
        self.m['views'] = []
        self.assertEqual(validate(self.m)['status'], 'ok')

if __name__ == '__main__': unittest.main()
