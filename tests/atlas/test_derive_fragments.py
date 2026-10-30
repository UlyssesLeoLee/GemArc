"""Fragments, closures and ``assemble``: shapes, one-step closure semantics, level gating, union properties, size, twin payload.

The Python-vs-JavaScript byte equality itself is ``tests/atlas/js/assemble.test.mjs`` (Node, CI only); here a test starts it
when Node is installed (skipped loudly otherwise) and the payload that test consumes is checked for determinism.

Size (design 9.1, M3 acceptance (d)): the Markdown of the primary-language fragments at the reference scale is at most 400 KB
raw; the serialized ``fragments`` object (what the page embeds) is measured too and must stay below the 700 KB stop line.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_fragments*.py"
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas.derive import assemble as A
from gemarc_mcp.atlas.derive import docbuild as B
from gemarc_mcp.atlas.derive import fragments as F
from gemarc_mcp.atlas.derive import i18n as T

from .test_derive_docs import base_model, built, fixture_model, reference_model

LEVELS = T.LEVELS
RANK = {"REQ": 1, "BD": 2, "DD": 3}
ROOT = Path(__file__).resolve().parents[2]
MD_BUDGET = 400 * 1024
STOP_LINE = 700 * 1024


def lines_of(text: str) -> set:
    return set(text.split("\n"))


def unpack(x: int):
    return x >> F.SHIFT, x & 7


class ShapeTests(unittest.TestCase):
    def test_docset_shape(self):
        for name in ("fixture", "base", "reference"):
            ds = built(name).langs["en-US"][0]
            self.assertEqual(ds["schema"], "gemarc-atlas-docset/1")
            fs, closures = ds["fragments"], ds["closures"]
            self.assertEqual(fs["language"], "en-US")
            self.assertEqual(len(fs["modelSha8"]), 8)
            items = fs["items"]
            for n, it in enumerate(items):
                self.assertEqual(sorted(it), ["chapter", "fid", "grp", "level", "md", "order"])
                self.assertEqual((it["fid"], it["order"]), (n, n), "fid and order are the final index")
                self.assertIn(it["level"], LEVELS)
                self.assertTrue(1 <= it["chapter"] <= 12)
                self.assertIn(it["grp"], fs["groups"])
                self.assertIsInstance(it["md"], str)
                self.assertNotIn("\x01", it["md"].replace(A.PH_DOCNO, "").replace(A.PH_UPPER, "").replace(A.PH_LOWER, "").replace(A.PH_TARGET, ""))
            self.assertEqual([(i["chapter"], i["order"]) for i in items], sorted((i["chapter"], i["order"]) for i in items))
            self.assertEqual(set(closures), set(fs["elements"]))
            self.assertEqual(len(fs["elements"]), len(set(fs["elements"])))
            for packed in list(closures.values()) + [fs["always"]]:
                self.assertEqual(packed, sorted(packed))
                for x in packed:
                    fid, mask = unpack(x)
                    self.assertTrue(0 <= fid < len(items) and 1 <= mask <= 7)
                    # a fragment only appears at levels it exists at: never deeper than its own shallowest level allows
                    for k, level in enumerate(LEVELS):
                        if mask & (1 << k):
                            self.assertLessEqual(RANK[items[fid]["level"]], k + 1)
            for level in LEVELS:
                chapters = fs["frame"][level]["chapters"]
                self.assertEqual([c["n"] for c in chapters], list(range(1, 13)))
                for c in chapters:
                    self.assertEqual(c["h"], T.chapter_heading("en-US", level, c["n"]))
                    for gid in c["groups"]:
                        self.assertIn(gid, fs["groups"])
            for g in fs["groups"].values():
                self.assertEqual(sorted(g), ["empty", "head", "join"])
                self.assertIn(g["join"], ("\n", "\n\n"))
            json.dumps(ds)  # serialisable as is

    def test_element_ids_cover_nodes_edges_cases_requirements(self):
        b = built("base")
        ds = b.langs["en-US"][0]
        expected = list(b.ix.node) + list(b.ix.edge) + list(b.ix.case) + list(b.ix.requirement)
        self.assertEqual(ds["fragments"]["elements"], expected)

    def test_interventions_are_listed_by_id_as_outside_the_chain(self):
        for name in ("fixture", "base", "reference"):
            b = built(name)
            outside = b.langs["en-US"][0]["coverage"]["notInDocuments"]
            self.assertEqual(outside["interventions"], [iv["interventionId"] for iv in b.ix.interventions])
            self.assertTrue(outside["reason"])
        self.assertGreater(len(built("reference").ix.interventions), 0)

    def test_a_node_without_an_id_is_reported_and_left_out(self):
        model = json.loads(json.dumps(base_model()))
        del model["nodes"][0]["id"]
        ds = F.build_docset(model, "en-US")
        self.assertTrue([i for i in ds["issues"] if i["rule"] == "DC-D-001"])
        self.assertFalse({i["rule"] for i in ds["issues"]} & {"DC-D-099"})


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.b = built("base")
        self.ds = self.b.langs["en-US"][0]

    def doc(self, level, *ids):
        return A.assemble(self.ds, level, list(ids))

    def test_node_closure(self):
        text = self.doc("DD", "svc.loan")
        self.assertIn("### Loan service", text)
        for edge in self.b.ix.incident_edge_ids("svc.loan"):
            self.assertIn(f"| `{edge}` |", text)
        self.assertIn("### Member borrows an available copy", text)   # a case it takes part in
        self.assertIn("### A late loan is flagged", text)
        self.assertIn("| `RQ-1` |", text)                                # the requirement naming it
        self.assertIn("`EVD-1`", text)
        # one step only: neither the other participants' sections nor the nodes of the requirement
        self.assertNotIn("### Stock service", text)
        self.assertNotIn("### Member\n", text)
        self.assertNotIn("### Copy entity", text)

    def test_edge_closure(self):
        text = self.doc("DD", "svc.loan:calls:svc.stock")
        self.assertIn("| `svc.loan:calls:svc.stock` |", text)
        self.assertIn("### Loan service", text)
        self.assertIn("### Stock service", text)
        self.assertNotIn("### Loan table", text)
        self.assertNotIn("### Member borrows", text)

    def test_case_closure_closes_the_participant_gap(self):
        text = self.doc("DD", "CS-BORROW-OK")
        case = self.b.ix.case["CS-BORROW-OK"]
        for node in {e["from"] for e in case["events"]} | {e["to"] for e in case["events"]}:
            self.assertIn(f"| `{node}` | ", text.split("## 3.")[1].split("## 4.")[0])  # mapping row of every participant
            self.assertIn(f"`{node}` · ", text)                                       # its section
        for edge in ("usr.member:calls:svc.loan", "svc.loan:calls:svc.stock", "svc.loan:writes:tbl.loan"):
            self.assertIn(f"| `{edge}` |", text)
        self.assertIn("| `RQ-1` |", text)
        self.assertIn("### Copy entity", text)   # a node of its requirement, not a participant
        self.assertIn("`TS-1`", text)

    def test_requirement_closure(self):
        text = self.doc("DD", "RQ-1")
        for nid in self.b.ix.requirement["RQ-1"]["nodeIds"]:
            self.assertIn(f"`{nid}` · ", text)
        for eid in self.b.ix.requirement["RQ-1"]["edgeIds"]:
            self.assertIn(f"| `{eid}` |", text)
        self.assertIn("### Member borrows an available copy", text)
        self.assertIn("### A late loan is flagged", text)

    def test_level_gating_follows_the_detail_level_of_the_node(self):
        # Actor: REQ level; Class: DD level; Module: BD level
        self.assertIn("| `usr.member` | Member |", self.doc("REQ", "usr.member"))
        self.assertNotIn("| `svc.loan` | Loan service |", self.doc("REQ", "svc.loan"))
        self.assertIn("| `svc.loan` | Loan service |", self.doc("BD", "svc.loan"))
        self.assertNotIn("`cls.loan`", self.doc("BD", "cls.loan").split("## 5.")[0].split("## 4.")[1])
        self.assertIn("### Loan class", self.doc("DD", "cls.loan"))
        # the cases and requirements of a hidden node still show
        self.assertIn("| `RQ-1` |", self.doc("REQ", "svc.loan"))
        self.assertIn("`CS-BORROW-OK`", self.doc("REQ", "svc.loan"))

    def test_policies_follow_their_scope_in_the_closure(self):
        model = json.loads(json.dumps(base_model()))
        model["policies"].append({"id": "PL-NODE", "text": "Loan service rule.", "src": "RQ-1", "scope": "node:svc.loan", "level": "REQ",
                                  "chapter": None, "kind": "constraint", "by": "user"})
        model["policies"].append({"id": "PL-REQ", "text": "Requirement rule.", "src": "RQ-1", "scope": "req:RQ-1", "level": "REQ",
                                  "chapter": None, "kind": "constraint", "by": "user"})
        ds = F.build_docset(model, "en-US")
        self.assertIn("Loan service rule.", A.assemble(ds, "DD", ["svc.loan"]))
        self.assertIn("Loan service rule.", A.assemble(ds, "DD", ["CS-BORROW-OK"]))   # via its participant
        self.assertNotIn("Loan service rule.", A.assemble(ds, "DD", ["usr.member"]))
        self.assertIn("Requirement rule.", A.assemble(ds, "DD", ["RQ-1"]))
        self.assertIn("Requirement rule.", A.assemble(ds, "DD", ["svc.loan"]))        # the requirement naming it is in scope
        self.assertNotIn("Requirement rule.", A.assemble(ds, "DD", []))

    def test_every_closure_is_only_a_subset_of_the_whole_document(self):
        for name in ("base", "reference"):
            b = built(name)
            ds = b.langs["en-US"][0]
            for level in LEVELS:
                whole = lines_of(A.assemble(ds, level, ds["fragments"]["elements"]))
                for eid in ds["fragments"]["elements"][:: max(1, len(ds["fragments"]["elements"]) // 40)]:
                    part = A.assemble(ds, level, [eid])
                    body = part.split("\n## 2.", 1)[1].split("\n", 1)[1]
                    for line in lines_of(body):
                        if line.startswith(("[NONE-", "> ", "|---")) or line.startswith("Purpose") or not line.strip():
                            continue
                        self.assertIn(line, whole, (name, level, eid, line[:80]))


class AssembleTests(unittest.TestCase):
    def setUp(self):
        self.ds = built("base").langs["en-US"][0]
        self.els = self.ds["fragments"]["elements"]

    def test_order_repeats_and_unknown_ids_do_not_matter(self):
        ids = ["svc.loan", "CS-BORROW-OK", "RQ-1", "usr.member:calls:svc.loan"]
        for level in LEVELS:
            plain = A.assemble(self.ds, level, ids)
            self.assertEqual(plain, A.assemble(self.ds, level, list(reversed(ids))))
            self.assertEqual(plain, A.assemble(self.ds, level, ids + ids + ["nope", "", 7, None]))
            self.assertEqual(plain, A.assemble(self.ds, level, tuple(ids)))
            self.assertEqual(plain, A.assemble(self.ds, level, iter(ids)))

    def test_whole_model_equals_all_element_ids(self):
        for level in LEVELS:
            self.assertEqual(A.assemble(self.ds, level, self.els), A.assemble(self.ds, level, list(self.els) + ["x"]))
            self.assertGreater(len(A.assemble(self.ds, level, self.els)), len(A.assemble(self.ds, level, [])))

    def test_bad_level_raises_value_error_only(self):
        with self.assertRaises(ValueError):
            A.assemble(self.ds, "XX", [])

    def test_empty_selection_is_the_fixed_skeleton(self):
        text = A.assemble(self.ds, "DD", [])
        self.assertIn("`TBD-2`", text)                       # every TBD
        self.assertIn("Only members may borrow.", text)      # policies with scope all
        self.assertIn(T.tr("en-US", "skeleton.doc_info.unselected"), text)
        self.assertEqual(len(re.findall(r"^## \d+\. ", text, re.M)), 12)

    def test_the_target_line_lists_at_most_six_ids(self):
        text = A.assemble(self.ds, "BD", self.els)
        line = next(x for x in text.split("\n") if x.startswith("- Target"))
        self.assertEqual(line.count("`") // 2, 6)
        self.assertTrue(line.endswith(f" +{len(self.els) - 6}"))

    def test_fnv_vectors_and_unicode(self):
        self.assertEqual(A.hash32_hex(""), "811c9dc5")
        self.assertEqual(A.hash32_hex("a"), "e40c292c")
        self.assertEqual(A.hash32_hex("foobar"), "bf9cf968")
        self.assertEqual(len(A.hash32_hex("中文")), 8)


class SizeTests(unittest.TestCase):
    """Measured numbers; the table is printed once so the handoff can quote a run."""

    results: list = []

    def test_reference_scale_size_budget(self):
        for seed in (7, 3, 11):
            model = reference_model(seed)
            for lang in T.LANGS:
                ds = F.build_docset(model, lang)
                st = ds["stats"]
                self.results.append((seed, lang, st["fragments"], st["mdBytes"], st["fragmentsJsonBytes"], st["closuresJsonBytes"]))
                self.assertLessEqual(st["mdBytes"], MD_BUDGET, (seed, lang, st["mdBytes"]))
                self.assertLess(st["fragmentsJsonBytes"], STOP_LINE, (seed, lang))
                self.assertEqual([i for i in ds["issues"] if i["rule"] == "DC-D-099"], [])
        print("\nfragment size at reference scale (seed, lang, fragments, md bytes, fragments json, closures json):", file=sys.stderr)
        for row in self.results:
            print("  ", row, file=sys.stderr)

    def test_stats_are_consistent(self):
        ds = built("reference").langs["zh-CN"][0]
        st = ds["stats"]
        self.assertEqual(st["fragments"], len(ds["fragments"]["items"]))
        self.assertEqual(st["mdBytes"], sum(len(i["md"].encode("utf-8")) for i in ds["fragments"]["items"]))
        self.assertEqual(sum(st["bytesPerChapter"].values()), st["mdBytes"])


class TwinPayloadTests(unittest.TestCase):
    def test_payload_is_deterministic_and_covers_the_special_selections(self):
        model = reference_model(7)
        first = B.twin_payload(model, "zh-CN", 50, 1)
        again = B.twin_payload(model, "zh-CN", 50, 1)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(again, sort_keys=True))
        cases = first["cases"]
        reqs = list(built("reference").ix.requirement)
        self.assertGreaterEqual(len(cases), 50 + len(reqs) * 3)
        singles = {(c["level"], c["ids"][0]) for c in cases if len(c["ids"]) == 1}
        for rid in reqs:
            for level in LEVELS:
                self.assertIn((level, rid), singles)
        self.assertTrue(any(c["ids"] == [] for c in cases))
        self.assertTrue(any("no-such-id" in c["ids"] for c in cases))
        self.assertTrue(any(len(c["ids"]) > 6 for c in cases))
        other = B.twin_payload(model, "zh-CN", 50, 2)
        self.assertNotEqual([c["ids"] for c in cases[-50:]], [c["ids"] for c in other["cases"][-50:]])

    def test_selection_hashes_are_the_fnv_of_the_text(self):
        for name in ("fixture", "reference"):
            ds = built(name).langs["en-US"][0]
            hashes = B.selection_hashes(ds, 8)
            self.assertEqual(len(hashes), 8)
            for h in hashes:
                self.assertEqual(sorted(h), ["elementIds", "hash32", "level"])
                self.assertEqual(h["hash32"], A.hash32_hex(A.assemble(ds, h["level"], h["elementIds"])))
            self.assertEqual(hashes, B.selection_hashes(ds, 8))

    def test_viewer_payload_shape(self):
        payload = B.viewer_payload(fixture_model(), "en-US")
        self.assertEqual(sorted(payload), ["closures", "docSelections", "fragments"])
        self.assertEqual(payload["fragments"]["language"], "en-US")
        json.dumps(payload)


@unittest.skipUnless(shutil.which("node"), "Node is not installed: the JavaScript twin check is CI only")
class JavaScriptTwinTests(unittest.TestCase):
    def test_node_twin_check_passes(self):
        run = subprocess.run(["node", "--test", "tests/atlas/js/assemble.test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=600,
                             env={**__import__("os").environ, "GEMARC_PYTHON": sys.executable})
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-500:])
        self.assertIn("# fail 0", run.stdout)
        self.assertIn("# skipped 0", run.stdout)


if __name__ == "__main__":
    unittest.main()
