"""The N0 depth-inclusion audit (``tools/pilot/depth_inclusion.py``, design A3 2.2 / D-3): the invariants that hold before and after the one repair it asks for.

The audit answers one question: does the deepest document (DD) contain every row and every column the shallow-level ones (REQ, BD) print, per fragment
group, so that the reading surface may print each fact once, at its deepest level? At the time of milestone R0 it finds ONE defect: the edge table
of DD has no label column while the REQ and BD edge rows (and the interface rows) have it. Milestone R1 adds that column to ``derive/fragments.py``.
This file therefore pins what must be true in both states (no lost row, no cross-level group outside the DD closure, at most three repair sites,
the simulated repair sufficient, the audit's own detection working) and not the number of defects, so the repair does not turn a test red.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_read_audit*.py"
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "pilot" / "depth_inclusion.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("depth_inclusion_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered while it executes
    spec.loader.exec_module(module)
    return module


di = load_tool()
RUNS = (("tiny", 1), ("small", 1), ("reference", 7), ("refcjk", 3))


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [{"preset": p, "seed": s, "result": di.audit_bundle(di.build_model(p, s), "zh-CN")} for p, s in RUNS]
        cls.merged = di.merge(cls.runs)
        args = Namespace(presets=[p for p, _ in RUNS], seeds=[s for _, s in RUNS], lang="zh-CN")
        cls.text, cls.summary = di.render(cls.runs, cls.merged, args)

    def test_no_row_of_a_shallow_level_document_is_lost(self):
        for grp, row in self.merged["groups"].items():
            if "unmapped" in row:
                continue
            self.assertEqual(row["stat"].rows_missing, 0, f"{grp}: a key of the shallow variant has no row in {row['stat'].deeper}")
            self.assertEqual(row["stat"].duplicate_keys, 0, grp)
            self.assertGreater(row["stat"].items, 0, grp)

    def test_the_level_suffixed_groups_are_all_seen(self):
        seen = set(self.merged["groups"])
        self.assertTrue({"n.REQ", "n.BD", "e.BD", "mach.REQ", "case", "ent", "cov", "req"} <= seen, seen)

    def test_every_cross_level_group_is_in_the_dd_closure_and_verbatim_in_the_dd_text(self):
        self.assertTrue({"ifc", "ports", "ev", "tbd", "acc"} <= set(self.merged["cross"]))
        for grp, c in self.merged["cross"].items():
            self.assertEqual(c["items"], c["in_dd"], f"{grp}: items outside the DD fragment set")
            self.assertEqual(c["rows"], c["rows_verbatim"], f"{grp}: rows that are not in the assembled DD document")
        self.assertEqual(self.summary["cross_level_not_in_closure"], [])

    def test_the_groups_that_exist_only_at_a_shallow_level_are_reported_not_hidden(self):
        self.assertIn("unmapped", self.merged["groups"]["req"])  # the requirement rows (REQ only): no deeper variant, a home of their own

    def test_the_derivation_rules_are_exercised_and_nothing_else_relaxes_a_cell(self):
        ent, cov = self.merged["groups"]["ent"]["stat"], self.merged["groups"]["cov"]["stat"]
        self.assertGreater(ent.count, 0)
        self.assertEqual(ent.absent, 0)
        self.assertGreater(cov.docref, 0)
        self.assertEqual(cov.absent, 0)
        for grp in ("n.REQ", "n.BD", "mach.REQ", "case"):
            stat = self.merged["groups"][grp]["stat"]
            self.assertEqual((stat.absent, stat.composed, stat.count, stat.docref), (0, 0, 0, 0), grp)
            self.assertEqual(stat.cells, stat.exact, grp)

    def test_the_stop_loss_holds_and_the_simulated_repair_is_enough(self):
        self.assertFalse(self.summary["blocked"], self.text)
        self.assertLessEqual(len(self.summary["repair_sites"]), 3)
        self.assertEqual(self.summary["not_contained_after_repair"], [])
        self.assertTrue(self.summary["ifc_rebuildable_after_fix"])
        for grp, row in self.merged["groups"].items():
            if row["stat"].verdict() == "NOT-CONTAINED":
                self.assertTrue(row["has_fix"], f"{grp} is not contained and has no simulated repair")
                self.assertEqual(row["after_fix"].absent, 0, grp)
                self.assertEqual(row["after_fix"].rows_missing, 0, grp)

    def test_what_is_missing_today_is_the_edge_label_and_nothing_else(self):
        for grp, row in self.merged["groups"].items():
            stat = row["stat"]
            if stat.verdict() == "NOT-CONTAINED":
                self.assertEqual(stat.deeper, "e.DD", grp)
                self.assertEqual(len(stat.absent_by_column), 1, f"{grp}: more than one column is missing: {dict(stat.absent_by_column)}")

    def test_the_report_names_the_decision(self):
        self.assertIn("decision:", self.text)
        self.assertIn("E. Stop-loss", self.text)
        self.assertIn("rows missing", self.text)


class DetectionTests(unittest.TestCase):
    """The audit must fail when something really is absent: a negative control on hand-made fragments."""

    def item(self, grp, md):
        return {"grp": grp, "md": md, "fid": 0, "level": "BD"}

    def test_a_cell_missing_from_the_deeper_row_is_counted(self):
        shallow = [self.item("e.BD", "| `a:calls:b` | `a` | `b` | Calls | confirm export | Implemented |")]
        deeper_ok = [self.item("e.DD", "| `a:calls:b` | `a` | `a:out` | `b` | `b:in` | Calls | [N/A] | [N/A] | `E1` | Implemented | confirm export |")]
        deeper_bad = [self.item("e.DD", "| `a:calls:b` | `a` | `a:out` | `b` | `b:in` | Calls | [N/A] | [N/A] | `E1` | Implemented |")]
        headers = ["Id", "From", "To", "Relation", "Label", "Grade"]
        good = di.audit_variant_pair(shallow, "e.DD", deeper_ok, headers)
        bad = di.audit_variant_pair(shallow, "e.DD", deeper_bad, headers)
        self.assertEqual((good.absent, good.rows_missing, good.exact), (0, 0, 6))
        self.assertEqual((bad.absent, bad.rows_missing, bad.exact), (1, 0, 5))
        self.assertEqual(dict(bad.absent_by_column), {"Label": 1})
        self.assertEqual(bad.verdict(), "NOT-CONTAINED")
        self.assertEqual(good.verdict(), "CONTAINED")

    def test_a_missing_row_is_counted(self):
        shallow = [self.item("e.BD", "| `a:calls:b` | `a` | `b` | Calls | x | G |")]
        stat = di.audit_variant_pair(shallow, "e.DD", [self.item("e.DD", "| `other` | `a` | `b` |")], ["Id", "From", "To", "Relation", "Label", "Grade"])
        self.assertEqual((stat.rows_missing, stat.cells), (1, 0))
        self.assertEqual(stat.verdict(), "NOT-CONTAINED")

    def test_a_substring_alone_is_not_enough(self):
        shallow = [self.item("n.BD", "| `n1` | Pump | Service | does a thing |")]
        deeper = [self.item("n.DD", "### Pump station\n`n1` · Service · Designed\n- Summary: does a thing and more")]
        stat = di.audit_variant_pair(shallow, "n.DD", deeper, ["Id", "Name", "Kind", "Summary"])
        self.assertEqual(dict(stat.absent_by_column), {"Name": 1, "Summary": 1})  # the name is a part of the heading, the summary a part of the bullet

    def test_the_declared_rules_and_nothing_more(self):
        headers = ["Id", "Name", "Owner", "Store", "Columns"]
        shallow = [self.item("ent", "| `t1` | Table | `u` | `s` | 2 |")]
        deeper = [self.item("ent.DD", "### Table\n`t1` · Owner `u` · Store `s`\n\n| Column | Type |\n|---|---|\n| a | x |\n| b | y |")]
        self.assertEqual(di.audit_variant_pair(shallow, "ent.DD", deeper, headers, None, di.entity_count_rule).verdict(), "CONTAINED-DERIVABLE")
        wrong = [self.item("ent", "| `t1` | Table | `u` | `s` | 3 |")]
        self.assertEqual(di.audit_variant_pair(wrong, "ent.DD", deeper, headers, None, di.entity_count_rule).verdict(), "NOT-CONTAINED")
        unauthored = [self.item("ent.DD", "### Table\n`t1` · Owner `u` · Store `s`\n\n[NOT-AUTHORED:node.columns@t1]")]
        zero = [self.item("ent", "| `t1` | Table | `u` | `s` | 0 |")]
        self.assertEqual(di.audit_variant_pair(zero, "ent.DD", unauthored, headers, None, di.entity_count_rule).verdict(), "CONTAINED-DERIVABLE")
        composed_shallow = [self.item("e.BD", "| `e1` | `a` | `b` | Refers (1:N) |")]
        composed_deeper = [self.item("e.DD", "| `e1` | `a` | `b` | Refers | 1:N |")]
        stat = di.audit_variant_pair(composed_shallow, "e.DD", composed_deeper, ["Id", "From", "To", "Relation and cardinality"])
        self.assertEqual((stat.composed, stat.absent), (1, 0))
        pointer = di.audit_variant_pair([self.item("cov", "| `r1` | Title | REQ 3 |")], "cov", [self.item("cov", "| `r1` | Title | BD 3 |")], ["Req", "Title", "Upper"])
        self.assertEqual((pointer.docref, pointer.absent), (1, 0))

    def test_the_markdown_readers(self):
        self.assertEqual(di.split_cells("| a | b \\| c | `d|e`? |"), ["a", "b \\| c", "`d", "e`?"])
        self.assertEqual(di.split_cells("|x|"), ["x"])
        self.assertEqual(di.table_rows("| h |\n|---|\n| r |\ntext"), [["h"], ["r"]])
        self.assertEqual(di.item_key("n.DD", "### Name\n`id.1` · Kind"), "id.1")
        self.assertEqual(di.item_key("e.BD", "| `e:1` | `a` |"), "e:1")
        atoms = di.atoms_of("### Name\n`n1` · Owner `u` · Designed\n- Summary: does it\n| a | b |\n|---|---|\n| c | d |")
        for atom in ("Name", "`n1`", "Owner `u`", "`u`", "Designed", "does it", "Summary: does it", "c", "d"):
            self.assertIn(atom, atoms)
        self.assertIsInstance(di, types.ModuleType)


if __name__ == "__main__":
    unittest.main()
