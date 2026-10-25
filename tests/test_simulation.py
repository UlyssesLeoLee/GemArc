import unittest
from gemarc_mcp.service import sample_model
from gemarc_mcp.simulation import simulate, compare, MAX_EVENTS

class SimulationTests(unittest.TestCase):
    def setUp(self): self.m = sample_model('demo')
    def test_normal(self):
        r=simulate(self.m,'normal')
        self.assertEqual(r['summary']['delivered'],4)
        self.assertEqual(r['trace'][-1]['event'],'send')
        self.assertEqual(r['trace'][-1]['timeMs'],40)
    def test_counterfactual(self):
        r=compare(self.m,'normal',['queue'])
        self.assertEqual(r['intervention']['summary']['delivered'],1)
        self.assertEqual(len(r['lostEvents']),3)
    def test_bad_module(self):
        with self.assertRaises(Exception): simulate(self.m,'normal',['not-found'])
    def test_cyclic_rule_bound(self):
        self.m['scenarios'][0]['rules'].append({'source':'notify','target':'sensor','whenEvent':'send','emit':'emit','delayMs':0})
        r=simulate(self.m,'normal')
        self.assertEqual(r['status'],'truncated')
        self.assertEqual(len(r['trace']),MAX_EVENTS)

if __name__=='__main__': unittest.main()
