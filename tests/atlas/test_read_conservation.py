"""RS-128 conservation (design A3 D-27, T-R9): every fragment of the deepest document that has a home is printed once; what is not printed is in the
exclusion table with a reason that is mechanically true; the explicit empty states and the markers survive.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_conservation
"""
from __future__ import annotations

import copy
import re
import unittest

from gemarc_mcp.atlas import build as BLD
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.derive import assemble as A
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.reading import envelope as E

from . import read_support as RS

LANG = "zh-CN"
NA = T.NOT_AUTHORED_RE


def only_128(b, surface):
    return [f for f in RS.check(b, surface, ["RS-128"])]


def marker_slots(lines) -> set:
    out = set()
    for line in lines:
        out.update(NA.findall(line))
    return out


class CleanConservationTests(unittest.TestCase):
    def test_every_preset_is_conserved(self):
        for preset in ("tiny", "small", "reference", "refcjk"):
            b = RS.built(preset)
            with self.subTest(preset=preset):
                self.assertEqual(only_128(b, b.surface()), [])

    def test_every_home_fragment_is_found_and_the_exclusion_is_the_interface_group(self):
        b = RS.built("reference")
        self.assertEqual([name for name, _n, _r in b.result.exclusions], ["ifc"])
        self.assertEqual(b.result.exclusions[0][2], "rebuildable-from:e.DD")
        self.assertEqual(b.result.exclusions[0][1], len(b.src.by_group("ifc")))
        self.assertGreater(len(b.src.by_group("ports")), 0)
        self.assertEqual({h.family for h in b.src.homes if h.grp == "ports"}, {"S"})
        self.assertEqual(b.src.problems, [])

    def test_the_markers_of_the_deepest_document_survive_distinct_by_slot(self):
        """The slots of NOT-AUTHORED markers on the pages equal those of the deepest document (the connection rows print each marker twice in the
        document: once in the connection table and once in the interface table that the pages leave out)."""
        for preset in ("small", "reference"):
            b = RS.built(preset)
            doc = A.assemble(b.bundle, "DD", b.bundle["fragments"]["elements"]).split("\n")
            frame_lines = set()
            for chapter in b.bundle["fragments"]["frame"]["DD"]["chapters"]:
                frame_lines.update({chapter["empty"], chapter["note"], chapter["h"]})
            pages = []
            for path, data in b.result.files.items():
                if path.startswith("read/") and path.endswith(".md") and "/Q" not in path and "INDEX" not in path and "G01" not in path:
                    pages.extend(data.decode("utf-8").split("\n"))
            want = marker_slots(x for x in doc if x not in frame_lines)
            got = marker_slots(pages)
            # the evidences no element cites are printed by the pages and are not in any document
            cited = {h.key for h in b.src.by_group("ev")}
            from gemarc_mcp.atlas.derive import docs as X

            for eid in b.src.ix.evidence:
                if eid not in cited:
                    want |= marker_slots([X.evidence_row(b.src.xctx(), eid)])
            with self.subTest(preset=preset):
                self.assertEqual(got - want, set())
                self.assertEqual(want - got, set())

    def test_split_units_are_conserved(self):
        b = RS.make(RS.hub_model(160, 400))
        self.assertEqual(only_128(b, b.surface()), [])
        self.assertEqual(RS.rules_of(b.result.findings), set())
        self.assertGreater(len([p for p in b.result.files if re.match(r"read/4D/C\d+\.md", p)]), 3)


class ConservationMutationTests(unittest.TestCase):
    def test_a_marker_turned_into_plain_text_is_found(self):
        b = RS.built("small")
        path = next(p for p in sorted(b.result.files) if p.startswith("read/") and NA.search(b.text(p)) and "/Q" not in p and "INDEX" not in p)
        out = RS.edit_page(b.surface(), LANG, path, lambda t: NA.sub("marker removed", t, count=1))
        self.assertIn("RS-128", RS.rules_of(RS.check(b, out)))

    def test_a_fragment_printed_on_two_pages_is_found(self):
        b = RS.built("small")
        row = next(h.md for h in b.src.by_group("tbd"))
        t_path = next(p for p in sorted(b.result.files) if p.startswith("read/1D/T"))
        r_path = next(p for p in sorted(b.result.files) if p.startswith("read/1D/R"))
        marker = "\n\n" + E.TEXT[LANG]["read.foot.neighbours"].split("{links}")[0]
        s = RS.edit_page(b.surface(), LANG, r_path, lambda t: t.replace(marker, "\n" + row + marker, 1))
        self.assertIn("RS-128", RS.rules_of(RS.check(b, s)))
        self.assertTrue(t_path)

    def test_a_port_row_that_is_missing_is_found(self):
        b = RS.built("small")
        row = b.src.by_group("ports")[0].lines[0]
        path = next(p for p in sorted(b.result.files) if p.startswith("read/2D/S") and row in b.text(p))
        out = RS.edit_page(b.surface(), LANG, path, lambda t: t.replace(row, row.replace("`", "'", 2), 1))
        self.assertIn("RS-128", RS.rules_of(RS.check(b, out)))

    def test_an_exclusion_that_is_not_listed_is_found(self):
        b = RS.built("small")
        path = "read/QA/Q01.md"
        out = RS.edit_page(b.surface(), LANG, path, lambda t: re.sub(r"^\| ifc \|.*$", "| ifc | 0 | none |", t, count=1, flags=re.M))
        self.assertIn("RS-128", RS.rules_of(RS.check(b, out)))

    def test_an_interface_row_that_cannot_be_rebuilt_is_an_error_of_the_build(self):
        b = RS.built("small")
        bundle = copy.deepcopy(b.bundle)
        item = next(i for i in bundle["fragments"]["items"] if i["grp"] == "ifc")
        item["md"] = item["md"][:-1] + " extra cell |"
        result = R.build(bundle, RS.meta_for(bundle), R.ReadConfig(), RS.UI)
        self.assertEqual(result.status, "invalid")
        self.assertIn("RS-128", RS.rules_of(result.findings))
        self.assertEqual(result.exclusions, [])

    def test_a_group_without_a_home_is_an_error_and_never_a_catch_all(self):
        b = RS.built("small")
        bundle = copy.deepcopy(b.bundle)
        items = bundle["fragments"]["items"]
        fid = len(items)
        items.append({"fid": fid, "chapter": 4, "order": fid, "level": "DD", "grp": "zz.new", "md": "| `x` | y |"})
        bundle["fragments"]["always"].append(fid * 8 + 4)
        result = R.build(bundle, RS.meta_for(bundle), R.ReadConfig(), RS.UI)
        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(f.rule == "RS-128" and "zz.new" in f.message for f in result.findings))

    def test_the_group_of_the_requirement_rows_is_in_the_universe(self):
        """Audit finding 1: the requirement rows exist only in the REQ document; their group is homed on the requirement pages."""
        b = RS.built("small")
        homes = [h for h in b.src.homes if h.grp == "req"]
        self.assertEqual(len(homes), len(b.src.ix.requirement))
        self.assertEqual({h.family for h in homes}, {"R"})
        dd = set(A.fragment_ids(b.bundle, b.bundle["fragments"]["elements"], 2))
        self.assertTrue(all(h.fid not in dd for h in homes))


if __name__ == "__main__":
    unittest.main()
