"""The one-time self-review (design addendum A1, D15): report, sample, record, and the rule that lets ``status`` say done.

All data is synthetic (the lending desk fixture and ``synth`` models). Planted defects go through the pure ``build_report`` when
the project format refuses them (an Implemented claim with only document evidence is an ERROR at ``put``), and through real slice
files otherwise. ``accepts_changed_model`` (the stale rule) is unit-tested directly and through a seam that has the shape of the
one-line hook proposed for ``status.evaluate``.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_review.py"
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp.atlas import encode  # noqa: E402
from gemarc_mcp.atlas import model as am  # noqa: E402
from gemarc_mcp.atlas import project as pj  # noqa: E402
from gemarc_mcp.atlas import review  # noqa: E402
from gemarc_mcp.atlas import status as st  # noqa: E402
from gemarc_mcp.atlas import tools  # noqa: E402

from . import synth  # noqa: E402

DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
DESK = {name: (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8") for name in ("00-space", "10-structure", "20-scenarios")}
POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"
USER_POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr by=user\n"
TBD_SLICE = "#atlas 1\n## tbds: id | title | decision | opts\nTBD-1 | Who may extend a loan | open question\n"
FEE_SLICE = ("#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.desk kind=Module\nsvc.fee | Fee service | Charges late fees. | ev=EVD-2\n"
             "## edges: from | rel | to | label | opts\nsvc.loan | calls | svc.fee | charge late fee | ev=EVD-2\n")
CAP = 4500


class Project(unittest.TestCase):
    """A temporary workspace with writes allowed, no gate registered, and a helper to build the lending desk."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        saved = dict(st.GATES)
        self.addCleanup(lambda: (st.GATES.clear(), st.GATES.update(saved)))
        st.GATES.clear()
        self.ws = pj.get_workspace(self.root)

    # ---- building ----------------------------------------------------------------------------------------------
    def init(self, name="desk", mode="asis", repos=("main",)):
        reply = tools.init(self.ws, name, "Library lending desk", mode, ["en-US"], repos=list(repos))
        self.assertEqual(reply["status"], "created", reply)
        self.name, self.paths = name, pj.project_paths(self.ws, name)
        return reply

    def put(self, slice_, text, **kw):
        return tools.put(self.ws, self.name, slice_, text, **kw)

    def desk(self, policy=POLICY, name="desk"):
        self.init(name)
        for slice_, text in DESK.items():
            self.assertEqual(self.put(slice_, text)["status"], "committed", slice_)
        if policy:
            self.assertEqual(self.put("30-policy", policy)["status"], "committed")

    def write_slice(self, slice_, text):
        """A direct file edit (supported): it can commit what ``put`` would refuse."""
        (self.paths.slices / f"{slice_}.atl").write_bytes(text.encode("utf-8"))

    def snap(self):
        return st.load_snapshot(self.paths, pj.load_config(self.paths))

    def report(self, **kw):
        out = review.review_report(self.ws, self.name)
        self.assertEqual(out["status"], "ok", out)
        return out

    def ids(self, report, code):
        for f in report["findings"]:
            if f["id"] == code:
                return f
        return None

    def tree(self):
        return {str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(self.root.rglob("*")) if p.is_file()}


def fabricate_prov(model) -> dict:
    """A provenance map with one distinct line per record (what the sample needs when there is no project)."""
    prov, line = {}, 0
    for coll, key, label in (("nodes", "id", "nodes"), ("edges", "id", "edges"), ("evidences", "evidenceId", "evidences"),
                             ("requirements", "id", "requirements"), ("policies", "id", "policies"), ("cases", "caseId", "cases")):
        for rec in model.get(coll, ()):
            line += 1
            prov[f"{label}[{rec[key]}]"] = f"x-structure.atl:{line}"
    return prov


def report_of(model, prov=None, **kw) -> dict:
    return review.build_report(review.Context(model, prov if prov is not None else fabricate_prov(model), **kw))


def ids_of(report, code):
    for f in report["findings"]:
        if f["id"] == code:
            return f
    return None


# ---------------------------------------------------------------------------------------------------------------
# The report of a real project
# ---------------------------------------------------------------------------------------------------------------


class DeskReportTests(Project):
    def test_report_is_deterministic_compact_read_only_and_has_eight_rows(self):
        self.desk()
        before = self.tree()
        first = review.review_report(self.ws, self.name)
        second = review.review_report(self.ws, self.name)
        self.assertEqual(self.tree(), before, "report must never write")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertLessEqual(tools.reply_bytes(first), CAP)
        self.assertNotIn("truncated", first)
        self.assertEqual((first["status"], first["authority"], first["action"], first["schema"]), ("ok", "local", "report", review.SCHEMA))
        self.assertEqual(len(first["sample"]), review.SAMPLE_K)
        self.assertEqual(first["counts"]["BLOCK"], 0, "a clean model has no BLOCK finding")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{8}", first["modelSha"]))
        print(f"\n[desk report] {tools.reply_bytes(first)} B, findings {[f['id'] for f in first['findings']]}")

    def test_same_model_in_another_workspace_gives_the_same_bytes(self):
        self.desk()
        a = json.dumps(self.report(), sort_keys=True)
        with tempfile.TemporaryDirectory() as other:
            ws2 = pj.get_workspace(Path(other))
            self.assertEqual(tools.init(ws2, "desk", "Library lending desk", "asis", ["en-US"], repos=["main"])["status"], "created")
            for slice_, text in {**DESK, "30-policy": POLICY}.items():
                self.assertEqual(tools.put(ws2, "desk", slice_, text)["status"], "committed")
            b = json.dumps(review.review_report(ws2, "desk"), sort_keys=True)
        self.assertEqual(a, b, "no absolute path, no timestamp, nothing of the machine is in the report")

    def test_every_sample_row_points_at_its_line(self):
        self.desk()
        report = self.report()
        seen = set()
        for row in report["sample"]:
            self.assertRegex(row["at"], r"^slices/[a-z0-9-]+\.atl:[0-9]+$")
            rel, _, line = row["at"].rpartition(":")
            text = (self.paths.atlas / rel).read_text(encoding="utf-8").splitlines()[int(line) - 1]
            ident = row["id"]
            if row["k"] == "ed":
                for end in (ident.split(":")[0], ident.split(":")[2]):
                    self.assertIn(end, text, row)
            else:
                self.assertIn(ident, text, row)
            self.assertNotIn(row["at"], seen)
            seen.add(row["at"])
            self.assertTrue(row["claim"] and "\n" not in row["claim"] and row["vs"], row)
        self.assertEqual({row["k"] for row in report["sample"]}, {"ev", "ed", "rq", "pl", "no", "ca"})
        self.assertEqual(set(report["compare"]), {"ev", "ed", "rq", "pl", "no", "ca"})

    def test_sample_mixes_every_kind_and_is_seeded_by_the_set_of_ids(self):
        self.desk(policy=None)
        base = {r["id"] for r in self.report()["sample"]}
        self.assertEqual(len(base), 8, "the pools without a policy give their place back to the other pools")
        self.assertLessEqual({"ev", "ed", "rq", "no", "ca"}, {r["k"] for r in self.report()["sample"]})
        model = synth.generate("reference", 3)
        prov = fabricate_prov(model)
        again = review.sample_rows(model, prov)
        self.assertEqual(again, review.sample_rows(copy.deepcopy(model), dict(reversed(list(prov.items())))))
        kinds = {r["k"] for r in again}
        self.assertEqual(kinds, {"ev", "ed", "rq", "pl", "no", "ca"}, "nodes and cases are sampled at reference scale too")
        # editing the content of rows does not re-roll the sample (a gameable seed); adding a row does
        other = copy.deepcopy(model)
        other["nodes"][0]["sub"] = other["nodes"][0]["sub"] + " changed"
        other["evidences"][0]["anchor"] = other["evidences"][0]["anchor"] + " changed"
        self.assertEqual([r["id"] for r in again], [r["id"] for r in review.sample_rows(other, prov)])
        more = copy.deepcopy(model)
        extra = copy.deepcopy(model["nodes"][0])
        extra["id"] = extra["id"] + ".copy"
        more["nodes"].append(extra)
        self.assertNotEqual([r["id"] for r in again], [r["id"] for r in review.sample_rows(more, dict(prov, **{f"nodes[{extra['id']}]": "x.atl:1"}))])

    def test_the_sample_prefers_risky_rows(self):
        model = synth.generate("reference", 3)
        prov = fabricate_prov(model)
        for n in model["nodes"]:
            n["verification"] = "Designed"
        risky = model["nodes"][5]
        risky["verification"] = "Implemented"
        picked = {r["id"] for r in review.sample_rows(model, prov) if r["k"] == "no"}
        self.assertEqual(picked, {risky["id"]})

    def test_sample_is_stable_under_other_hash_seeds(self):
        code = ("import json,sys\nsys.path[:0]=[%r,%r]\nfrom tests.atlas import synth\nfrom gemarc_mcp.atlas import review\n"
                "from tests.atlas.test_review import fabricate_prov\nm=synth.generate('reference',7)\n"
                "print(json.dumps(review.sample_rows(m,fabricate_prov(m)),sort_keys=True))\n") % (str(REPO), str(REPO / "src"))
        outs = []
        for seed in ("1", "977"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO / "src")}
            done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO))
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            outs.append(done.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(len(json.loads(outs[0])), review.SAMPLE_K)

    def test_findings_are_ranked_and_each_has_a_one_line_fix(self):
        self.desk()
        self.write_slice("40-extra", "#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.desk kind=Module\nsvc.lonely | Lonely thing | Does a thing. | ev=EVD-2\n")
        report = self.report()
        ranks = [review.SEVERITIES.index(f["sev"]) for f in report["findings"]]
        self.assertEqual(ranks, sorted(ranks))
        for f in report["findings"]:
            self.assertRegex(f["id"], r"^RV-[A-Z][0-9]+$")
            self.assertTrue(f["fix"] and "\n" not in f["fix"] and len(f["fix"]) <= 140, f)
            self.assertGreaterEqual(f["n"], 1)
            self.assertLessEqual(len(f.get("ids", ())), review.SHOW_IDS)


class PlantedDefectTests(Project):
    """Each planted defect appears as the right finding (design: requirement without case, isolated node, by=ai policy, Implemented
    with only document evidence, near-duplicate name)."""

    def test_a_requirement_with_no_case_is_a_gap(self):
        self.desk()
        self.write_slice("40-more", "#atlas 1\n## requirements: id | title | source | opts\nRQ-2 | A member can renew a loan | interview 2 | nodes=svc.loan\n")
        f = ids_of(self.report(), "RV-C2")
        self.assertEqual((f["sev"], f["ids"]), ("GAP", ["RQ-2"]))

    def test_a_requirement_with_no_node_and_one_whose_cases_assert_nothing(self):
        self.desk()
        self.write_slice("40-more", "#atlas 1\n## requirements: id | title | source | opts\nRQ-2 | A member can renew a loan | interview 2\n"
                                    "RQ-3 | A member can reserve a copy | interview 3 | nodes=svc.loan\n"
                                    "## cases: id | cat | title | opts\nCS-RES | normal | Member reserves | req=RQ-3\n")
        report = self.report()
        self.assertEqual(ids_of(report, "RV-C1")["ids"], ["RQ-2"])
        self.assertEqual(ids_of(report, "RV-C3")["ids"], ["RQ-3"])
        self.assertEqual(ids_of(report, "RV-C6")["ids"], ["CS-RES"])

    def test_an_isolated_node_is_a_gap(self):
        self.desk()
        self.write_slice("40-extra", "#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.desk kind=Module\nsvc.lonely | Lonely thing | Does a thing. | ev=EVD-2\n")
        f = ids_of(self.report(), "RV-C5")
        self.assertEqual((f["sev"], f["ids"]), ("GAP", ["svc.lonely"]))
        self.assertIn("svc.lonely", ids_of(self.report(), "RV-C4")["ids"], "it is also in no requirement")

    def test_a_policy_by_ai_is_listed_and_by_user_is_not(self):
        self.desk()
        f = ids_of(self.report(), "RV-H1")
        self.assertEqual((f["sev"], f["ids"]), ("GAP", ["PL-NFR-1"]))
        self.assertEqual(self.put("30-policy", USER_POLICY)["status"], "committed")
        self.assertIsNone(ids_of(self.report(), "RV-H1"))

    def test_an_implemented_claim_with_only_document_evidence_is_a_block(self):
        self.desk()
        text = DESK["10-structure"].replace("usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Inferred",
                                            "usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Implemented")
        self.assertNotEqual(text, DESK["10-structure"])
        refused = self.put("10-structure", text)
        self.assertEqual(refused["status"], "invalid", "put refuses it (EV-001): only a direct file edit can commit it")
        self.write_slice("10-structure", text)
        f = ids_of(self.report(), "RV-E1")
        self.assertEqual((f["sev"], f["ids"]), ("BLOCK", ["usr.member"]))
        self.assertEqual(self.report()["counts"]["BLOCK"], 1)

    def test_a_near_duplicate_name_is_an_info_hint(self):
        self.desk()
        self.write_slice("40-dup", "#atlas 1\n## nodes: id | name | sub | opts\n@set room=rm.desk kind=Module\nsvc.loan2 | Loan services | A second loan service. | ev=EVD-2\n"
                                   "## edges: from | rel | to | label | opts\nsvc.loan2 | calls | svc.stock | check copy status | ev=EVD-2\n")
        f = ids_of(self.report(), "RV-K1")
        self.assertEqual((f["sev"], f["ids"]), ("INFO", ["svc.loan~svc.loan2"]))

    def test_not_authored_slots_and_open_tbds(self):
        self.desk(policy=None)
        report = self.report()
        f = ids_of(report, "RV-H2")
        self.assertEqual((f["sev"], f["ids"]), ("BLOCK", ["ch9.nfr"]))
        self.write_slice("40-tbd", "#atlas 1\n## tbds: id | title | opts\nTBD-1 | Who may extend a loan | \n")
        text = self.snap().model.get("tbds")
        if text:
            self.assertEqual(ids_of(self.report(), "RV-H3")["ids"], ["TBD-1"])


class MoreFindingsPureTests(unittest.TestCase):
    """The remaining findings on synthetic models (pure ``build_report``)."""

    def model(self):
        return synth.generate("small", 5)

    def test_clean_synthetic_models_have_no_block_unless_their_slots_say_so(self):
        for preset in ("tiny", "small"):
            model = synth.generate(preset, 4)
            report = report_of(model)
            blocked = {f["id"] for f in report["findings"] if f["sev"] == "BLOCK"}
            self.assertLessEqual(blocked, {"RV-H2"}, (preset, blocked))

    def test_inherited_edges_and_inferred_share_and_unreachable_states(self):
        model = copy.deepcopy(self.model())
        inferred = 0
        for n in model["nodes"]:
            n["verification"] = "Inferred"
            inferred += 1
        prov = fabricate_prov(model)
        prov[f"edges[{model['edges'][0]['id']}].evidenceIds"] = "x-structure.atl:1"
        report = report_of(model, prov)
        self.assertEqual(ids_of(report, "RV-E2")["ids"], [model["edges"][0]["id"]])
        self.assertEqual(ids_of(report, "RV-E3")["sev"], "GAP")
        self.assertEqual(report["stats"]["inheritedEdges"], 1)
        machine = {"id": "svc.m", "nodeId": "svc.m", "initial": "A", "errorCode": None, "evidenceIds": [], "states": ["A", "B", "Z"], "terminal": ["B", "Z"],
                   "transitions": [{"id": "t", "from": "A", "to": "B"}]}
        model["machines"] = [machine]
        self.assertEqual(ids_of(report_of(model), "RV-C7")["ids"], ["svc.m:Z"])

    def test_kind_relation_sanity_empty_rooms_and_unplaced_nodes_are_hints(self):
        model = copy.deepcopy(self.model())
        store = next(n for n in model["nodes"] if n["kind"] == "Module")
        store["kind"] = "Storage"
        edge = next(e for e in model["edges"] if e["sourceId"] == store["id"] and not e["static"])
        edge["relation"] = "calls"
        model["space"]["rooms"].append({"id": "rm.empty", "floorId": model["space"]["floors"][0]["id"], "name": "Empty"})
        model["nodes"][0]["roomId"] = "rm.nowhere"
        report = report_of(model)
        k4, k2, k3 = ids_of(report, "RV-K4"), ids_of(report, "RV-K2"), ids_of(report, "RV-K3")
        self.assertEqual(k4["sev"], "INFO")
        found = {f.id: f for f in review.collect(review.Context(model))[0]}
        self.assertIn(edge["id"], found["RV-K4"].refs)
        self.assertEqual(k2["sev"], "INFO")
        self.assertIn("rm.empty", found["RV-K2"].refs)
        self.assertEqual((k3["sev"], k3["ids"][0]), ("GAP", model["nodes"][0]["id"]))
        for code in ("RV-K1", "RV-K2", "RV-K4"):
            f = ids_of(report, code)
            self.assertTrue(f is None or f["sev"] == "INFO", "the heuristics are never above INFO")

    def test_stamp_oracle_and_unconfirmed(self):
        model = self.model()
        ids = [e["evidenceId"] for e in model["evidences"] if e["sourceKind"] != "user-requirement"]
        none = report_of(model, stamp=None)
        self.assertEqual((ids_of(none, "RV-E4")["sev"], none["stats"]["anchors"]), ("INFO", "not verified yet"))
        part = report_of(model, stamp=set(ids[:3]))
        f = ids_of(part, "RV-E4")
        self.assertEqual(f["sev"], "GAP")
        self.assertEqual(f["n"], len(ids) - 3)
        self.assertEqual(part["stats"]["anchors"], f"3/{len(ids)} stamped")
        self.assertIsNone(ids_of(report_of(model, stamp=set(ids)), "RV-E4"))
        self.assertEqual(none["oracle"], "not available yet")
        ledger = {"seals": {"CS-1": {"kind": "case", "state": "sealed"}, "CS-2": {"kind": "case", "state": "confirmed"}}, "blindMismatch": 2, "bite": 0.5}
        with_oracle = report_of(model, oracle=ledger)
        self.assertEqual(with_oracle["oracle"], {"total": 2, "sealed": 1, "revised": 0, "confirmed": 1, "unconfirmed": 1, "blindMismatch": 2, "bite": 0.5})
        self.assertEqual(ids_of(with_oracle, "RV-O1")["sev"], "GAP")

    def test_to_be_quotes_sources_and_missing_texts(self):
        model = copy.deepcopy(self.model())
        model["meta"]["mode"] = "tobe"
        model["evidences"] = [dict(model["evidences"][0], evidenceId="EV-U1", sourceKind="user-requirement", filePath="user:req.txt",
                                   anchor="A member must borrow a copy.", repository="")]
        model["requirements"] = [dict(r, source="A member must borrow a copy.") for r in model["requirements"][:2]]
        model["requirements"][1]["source"] = "The librarian shall adore every reader."
        text = review._norm_quote("A member must borrow a copy. Fees are never charged.")
        report = report_of(model, user_texts=text)
        h5 = ids_of(report, "RV-H5")
        self.assertEqual((h5["sev"], h5["ids"]), ("BLOCK", [model["requirements"][1]["id"]]))
        self.assertIsNone(ids_of(report, "RV-E5"))
        lost = report_of(model, user_texts=review._norm_quote("Nothing relevant."))
        self.assertEqual(ids_of(lost, "RV-E5")["sev"], "BLOCK")
        nothing = report_of(model, user_texts="")
        self.assertEqual(ids_of(nothing, "RV-E5")["sev"], "GAP")
        self.assertIsNone(ids_of(nothing, "RV-H5"), "no saved text: only the GAP says so")
        model["requirements"][0]["source"] = " "
        self.assertEqual(ids_of(report_of(model), "RV-H4")["sev"], "BLOCK")
        model["meta"]["mode"] = "asis"
        self.assertEqual(ids_of(report_of(model), "RV-H4")["sev"], "GAP")

    def test_quote_matching_rules(self):
        hay = review._norm_quote("A member MUST borrow   a copy.\nFees are never charged.")
        self.assertTrue(review.quote_in_text('"a member must borrow a copy."', hay))
        self.assertTrue(review.quote_in_text("user: A member must borrow ... never charged.", hay))
        self.assertFalse(review.quote_in_text("A member must lend a copy.", hay))
        self.assertFalse(review.quote_in_text("", hay))
        self.assertFalse(review.quote_in_text("ab", hay), "a quote of fewer than four characters proves nothing")

    def test_near_duplicate_rules(self):
        def pairs(*names):
            return review.near_duplicates([{"id": f"n{i}", "name": n} for i, n in enumerate(names)])
        self.assertEqual(pairs("Loan service", "Loan services"), ["n0~n1"])
        self.assertEqual(pairs("Retry queue", "retry-queue"), ["n0~n1"])
        self.assertEqual(pairs("Loan service", "Loan table"), [])
        self.assertEqual(pairs("Service 12", "Service 13"), [], "names that differ only inside digit runs are different on purpose")
        self.assertEqual(pairs("Cache", "Caches"), [], "short ASCII names are never compared")
        self.assertEqual(pairs("借阅服务", "借阅服务器"), ["n0~n1"])
        self.assertEqual(pairs("Alpha beta gamma"), [])

    def test_reference_scale_synthetic_does_not_drown_in_near_duplicates(self):
        model = synth.generate("reference", 11)
        found = review.near_duplicates(model["nodes"])
        print(f"\n[near-duplicates] {len(found)} pair(s) among {len(model['nodes'])} synthetic nodes")
        self.assertLessEqual(len(found), 5)


class FitTests(unittest.TestCase):
    def test_a_report_with_every_finding_still_fits_and_keeps_its_sample(self):
        model = copy.deepcopy(synth.generate("reference", 9))
        model["meta"]["mode"] = "tobe"
        for n in model["nodes"][:40]:
            n["verification"] = "Implemented"
            n["evidenceIds"] = []
        for n in model["nodes"][40:90]:
            n["verification"] = "Inferred"
        for r in model["requirements"]:
            r["source"] = ""
            r["nodeIds"] = []
        report = report_of(model, user_texts="some text", stamp=set(), oracle={"seals": {"a": {"kind": "case", "state": "sealed"}}})
        self.assertLessEqual(tools.reply_bytes(report), CAP)
        self.assertEqual(len(report["sample"]), review.SAMPLE_K)
        self.assertGreaterEqual(len(report["findings"]), 8)

    def test_the_staged_shrink_never_drops_a_sample_row(self):
        model = synth.generate("reference", 9)
        for cap in (3600, 3200):
            report = review.build_report(review.Context(model, fabricate_prov(model)), cap=cap)
            self.assertLessEqual(tools.reply_bytes(report), cap)
            self.assertEqual(len(report["sample"]), review.SAMPLE_K, cap)


# ---------------------------------------------------------------------------------------------------------------
# Reference scale (a real project from the reference-scale synthetic)
# ---------------------------------------------------------------------------------------------------------------


class ReferenceScaleTests(Project):
    def test_reference_scale_report_is_deterministic_small_and_its_rows_resolve(self):
        self.init("big", repos=())
        model = synth.generate("reference", 11)
        for name, text in encode.encode_atl(model, max_bytes=60 * 1024).items():
            self.assertEqual(self.put(name.rsplit(".", 1)[0], text)["status"], "committed", name)
        t0 = time.perf_counter()
        first = review.review_report(self.ws, "big")
        elapsed = time.perf_counter() - t0
        second = review.review_report(self.ws, "big")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        size = tools.reply_bytes(first)
        self.assertLessEqual(size, CAP)
        self.assertNotIn("truncated", first)
        self.assertEqual(len(first["sample"]), review.SAMPLE_K)
        for row in first["sample"]:
            rel, _, line = row["at"].rpartition(":")
            text = (self.paths.atlas / rel).read_text(encoding="utf-8").splitlines()[int(line) - 1]
            key = row["id"].split(":")
            self.assertTrue(all(part in text for part in ([key[0], key[2]] if row["k"] == "ed" else [row["id"]])), (row, text[:120]))
        self.assertEqual(first["stats"]["n"]["nodes"], 169)
        print(f"\n[reference-scale report] {size} B, {elapsed:.3f} s, findings {[(f['id'], f['n']) for f in first['findings']]}")


# ---------------------------------------------------------------------------------------------------------------
# The project directory feeds the report: saved text, stamp, oracle
# ---------------------------------------------------------------------------------------------------------------


class ProjectInputsTests(Project):
    def tobe_desk(self, saved="A member must be able to borrow an available copy.\nBorrowing completes within two seconds.\n", source=None):
        self.init("tobe", mode="tobe", repos=())
        self.paths.user.mkdir(parents=True, exist_ok=True)
        if saved is not None:
            (self.paths.user / "requirement.txt").write_text(saved, encoding="utf-8")
        struct = DESK["10-structure"].replace("ev=EVD-1,EVD-2", "ev=EVD-3").replace("ev=EVD-2", "ev=EVD-3").replace("ev=EVD-1", "ev=EVD-3").replace(" verif=Inferred", "")
        struct = re.sub(r"## evidences.*", "## evidences: id | file | anchor | topic | opts\nEVD-3 | user:requirement.txt | A member must be able to borrow an available copy. | the borrower actor\n",
                        struct, flags=re.S)
        scenarios = DESK["20-scenarios"].replace(" ev=EVD-1", "").replace("interview 1", source or "A member must be able to borrow an available copy.")
        for slice_, text in (("00-space", DESK["00-space"]), ("10-structure", struct), ("20-scenarios", scenarios), ("30-policy", POLICY)):
            self.assertEqual(self.put(slice_, text)["status"], "committed", slice_)

    def test_to_be_quotes_are_read_from_the_saved_text(self):
        self.tobe_desk()
        report = self.report()
        self.assertEqual(report["mode"], "tobe")
        self.assertIsNone(ids_of(report, "RV-E5"))
        self.assertIsNone(ids_of(report, "RV-H5"))
        self.assertEqual(report["counts"]["BLOCK"], 0)

    def test_a_quote_the_user_never_wrote_is_a_block(self):
        self.tobe_desk(saved="Something else entirely about opening hours.\n")
        report = self.report()
        self.assertEqual(ids_of(report, "RV-E5")["sev"], "BLOCK")
        self.assertEqual(ids_of(report, "RV-H5")["ids"], ["RQ-1"])

    def test_no_saved_text_is_a_gap_not_a_block(self):
        self.tobe_desk(saved=None)
        report = self.report()
        self.assertEqual(ids_of(report, "RV-E5")["sev"], "GAP")
        self.assertEqual(report["counts"]["BLOCK"], 0)

    def test_the_evidence_stamp_and_the_oracle_ledger_are_read(self):
        self.desk()
        self.assertEqual(self.report()["stats"]["anchors"], "not verified yet")
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.stamp.write_text(json.dumps({"schema": "gemarc-atlas-evidence-stamp/1", "entries": {"EVD-1": {"rangeSha256": "x"}}}), encoding="utf-8")
        report = self.report()
        self.assertEqual(report["stats"]["anchors"], "1/3 stamped")
        self.assertEqual(ids_of(report, "RV-E4")["ids"], ["EVD-2", "EVD-3"])
        self.paths.oracle.write_text(json.dumps({"seals": {"CS-BORROW-OK": {"kind": "case", "state": "sealed"}}}), encoding="utf-8")
        report = self.report()
        self.assertEqual(report["oracle"]["unconfirmed"], 1)
        self.assertEqual(ids_of(report, "RV-O1")["sev"], "GAP")


# ---------------------------------------------------------------------------------------------------------------
# Record, status and the matrix of conditions
# ---------------------------------------------------------------------------------------------------------------


def _seam(snap, original):
    """``status.evaluate`` with the one-line hook proposed for it: ``review_is_current(...) or accepts_changed_model(snap, record)``."""
    with mock.patch.object(st, "review_is_current", lambda record, model: original(record, model) or review.accepts_changed_model(snap, record)):
        return st.evaluate(snap)


class RecordTests(Project):
    def status(self):
        return tools.status(self.ws, self.name)

    def record(self, notes=""):
        return review.review_record(self.ws, self.name, notes)

    def test_record_writes_one_canonical_file_and_status_becomes_done(self):
        self.desk()
        self.assertEqual((self.status()["phase"], self.status()["done"]), ("review", False))
        self.assertIn("review:missing", self.status()["blockers"])
        out = self.record("checked the sample; fixed=RV-C2; open=RV-H1")
        self.assertEqual((out["status"], out["action"], out["done"], out["phase"]), ("recorded", "record", True, "done"), out)
        raw = self.paths.review.read_bytes()
        self.assertTrue(raw.endswith(b"\n") and b"\r" not in raw)
        record = json.loads(raw)
        self.assertEqual(list(record), sorted(record), "canonical: sorted keys")
        self.assertEqual(record["schema"], review.SCHEMA)
        self.assertEqual(record["modelSha"], am.model_sha(self.snap().model))
        self.assertRegex(record["modelSha"], r"^[0-9a-f]{64}$")
        self.assertEqual(record["fixedAfter"], ["RV-C2"], "RV-C2 is claimed fixed and is gone")
        self.assertEqual(record["blockKeys"], [])
        self.assertEqual({f["id"] for f in record["findingsAtReview"]}, {f["id"] for f in self.report()["findings"]})
        self.assertNotRegex(raw.decode("utf-8"), r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T", "no timestamp")
        status = self.status()
        self.assertEqual((status["done"], status["phase"], status["blockers"]), (True, "done", []))

    def test_claimed_fixed_is_checked_against_the_findings(self):
        self.desk()
        out = self.record("fixed=RV-H1, RV-C5; the rest open")
        self.assertEqual(out["claimedButOpen"], ["RV-H1"], "RV-H1 (the by=ai policy) is still there")
        self.assertEqual(out["fixedAfter"], ["RV-C5"])

    def test_only_one_pass(self):
        self.desk()
        self.assertEqual(self.record("first")["status"], "recorded")
        before = self.paths.review.read_bytes()
        again = self.record("second")
        self.assertEqual((again["status"], again["code"]), ("invalid", review.E_REVIEW_ONCE))
        self.assertEqual(self.paths.review.read_bytes(), before)
        self.assertEqual(self.status()["done"], True, "report keeps working and status does not ask again")
        self.assertEqual(self.report()["record"]["state"], "current")

    def test_record_needs_the_write_gate_and_creates_nothing_without_it(self):
        self.desk()
        before = self.tree()
        with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "0"}):
            out = self.record("x")
            self.assertEqual((out["status"], out["code"]), ("invalid", "GEMARC_AUTH_002"))
            self.assertEqual(review.review_report(self.ws, self.name)["status"], "ok", "report is read-only and needs no gate")
        self.assertFalse(self.paths.review.exists())
        self.assertEqual(self.tree(), before)

    def test_record_is_refused_before_phase_review_and_does_not_burn_the_pass(self):
        self.init()
        self.put("00-space", DESK["00-space"])
        out = self.record()
        self.assertEqual((out["status"], out["code"], out["phase"]), ("invalid", review.E_REVIEW_PHASE, "author"), out)
        self.assertFalse(self.paths.review.exists())
        for slice_, text in list(DESK.items())[1:]:
            self.put(slice_, text)
        self.put("30-policy", POLICY)
        self.assertEqual(self.record()["status"], "recorded")

    def test_arguments_are_checked(self):
        self.desk()
        too_big = self.record("x" * (review.NOTES_MAX_BYTES + 1))
        self.assertEqual((too_big["status"], too_big["code"]), ("invalid", review.E_REVIEW_ARG))
        self.assertEqual(review.run(self.ws, self.name, "bogus")["code"], review.E_REVIEW_ARG)
        self.assertEqual(review.run(self.ws, "nope")["status"], "invalid")
        self.assertFalse(self.paths.review.exists())

    def test_an_invalid_record_file_does_not_block_the_one_pass(self):
        self.desk()
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.review.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.report()["record"]["state"], "none")
        self.assertEqual(self.record()["status"], "recorded")
        self.paths.review.write_text(json.dumps({"modelSha": "zz", "findings": []}), encoding="utf-8")
        self.assertFalse(self.status()["done"])

    def test_report_after_record_says_what_the_record_is(self):
        self.desk()
        self.record()
        self.assertEqual(self.report()["record"], {"state": "current", "sha": am.model_sha(self.snap().model)[:8]})
        self.assertIn("no second pass", " ".join(self.report()["next"]))
        extra = DESK["10-structure"] + "@set room=rm.desk kind=Module\nsvc.fee | Fee service | Charges late fees. | ev=EVD-2\n"
        # a harmless extra node with an edge: the model changes, no BLOCK appears
        self.assertEqual(self.put("40-fee", FEE_SLICE)["status"], "committed")
        view = self.report()["record"]
        self.assertEqual(view["state"], "accepted", view)
        raw = self.status()
        self.assertEqual((raw["done"], raw["phase"]), (True, "done"), "the stale rule is wired into status: no second review")
        self.assertNotIn("review:stale", raw["blockers"])
        self.assertEqual(st.evaluate(self.snap()).blockers, [])


class ConditionMatrixTests(Project):
    """done is true only when every other condition holds: each condition is broken alone with a valid, current record in place."""

    def with_record(self):
        """Record for the current model by hand (``review_record`` itself refuses outside phase review)."""
        snap = self.snap()
        findings, _s, _o = review.collect(review.context_of(snap))
        record = review.make_record(snap.model, snap.mode, findings, "matrix")
        self.paths.dot.mkdir(parents=True, exist_ok=True)
        self.paths.review.write_bytes(review._canonical(record))

    def state(self):
        return st.evaluate(self.snap())

    def test_baseline_is_done(self):
        self.desk()
        self.with_record()
        self.assertTrue(self.state().done)

    def test_no_record_is_not_done(self):
        self.desk()
        state = self.state()
        self.assertEqual((state.done, state.phase), (False, "review"))

    def test_each_condition_alone_keeps_done_false(self):
        # 1 red draft
        self.desk()
        self.put("40-bad", "#atlas 1\n## nodes: id | name | sub | opts\nsvc.bad | Bad | no room or kind\n")
        self.assertTrue(self.snap().red)
        self.with_record()
        state = self.state()
        self.assertFalse(state.done)
        self.assertTrue(any(b.startswith("red-drafts") for b in state.blockers), state.blockers)
        # 2 check full ERROR (a direct edit of a committed slice: an edge to a node that does not exist)
        self.desk(name="err")
        self.write_slice("40-err", "#atlas 1\n## edges: from | rel | to | label | opts\nsvc.loan | calls | svc.nowhere | into the void | ev=EVD-2\n")
        self.with_record()
        state = self.state()
        self.assertFalse(state.done)
        self.assertTrue(any(b.startswith("check-full") for b in state.blockers), state.blockers)
        # 3 completeness BLOCK (no nfr policy -> chapter 9 would print a marker)
        self.desk(policy=None, name="nfr")
        self.with_record()
        state = self.state()
        self.assertFalse(state.done)
        self.assertTrue(any(b.startswith("completeness-block") for b in state.blockers), state.blockers)
        # 4 a red gate
        self.desk(name="gate")
        self.with_record()
        st.register_gate("build", lambda snap: {"applicable": True, "ok": False, "detail": "no output yet"})
        state = self.state()
        self.assertFalse(state.done)
        self.assertEqual(state.phase, "build")
        st.unregister_gate("build")
        self.assertTrue(self.state().done)
        # 5 the oracle gate is applicable: 4D and 5D become required and nothing is sealed (put seals, so the ledger is emptied)
        self.paths.oracle.write_text("{}", encoding="utf-8")
        st.register_gate("oracle", lambda snap: {"applicable": True, "ok": True, "detail": "sealed"})
        state = self.state()
        self.assertFalse(state.done)
        self.assertTrue(any(b.startswith("dims:") for b in state.blockers), state.blockers)
        st.unregister_gate("oracle")
        # 6 an open TBD that only the user can decide
        self.desk(name="tbd")
        self.assertEqual(self.put("40-tbd", TBD_SLICE)["status"], "committed")
        self.with_record()
        state = self.state()
        self.assertFalse(state.done)
        self.assertEqual(state.phase, "needs-user")
        bare = "#atlas 1\n## policies: id | text | opts\nPL-TBD-1 | Only staff extend a loan. | src=TBD-1 by=user\n"
        self.assertEqual(self.put("41-decided", bare)["status"], "committed")
        self.assertFalse(self.state().done, "a by=user policy that quotes nothing the user said does not settle the TBD")
        self.paths.user.mkdir(parents=True, exist_ok=True)
        (self.paths.user / "answer.txt").write_text("Asked about renewals the user said: only staff extend a loan.\n", encoding="utf-8")
        decided = "#atlas 1\n## policies: id | text | opts\nPL-TBD-1 | Decision of the user: 'only staff extend a loan'. | src=TBD-1 by=user\n"
        self.assertEqual(self.put("41-decided", decided)["status"], "committed")
        self.with_record()
        self.assertTrue(self.state().done, "a user decision (policy by=user src=<tbd>) that quotes the saved answer settles the TBD")

    def test_a_stale_record_with_a_new_block_is_not_done_even_through_the_hook(self):
        self.desk()
        self.with_record()
        text = DESK["10-structure"].replace("verif=Inferred", "verif=Implemented")
        self.write_slice("10-structure", text)
        hooked = _seam(self.snap(), st.review_is_current)
        self.assertFalse(hooked.done)
        self.assertTrue(any(b.startswith("check-full") for b in hooked.blockers), "EV-001 also fails the full check")


class StaleRuleTests(unittest.TestCase):
    """``accepts_changed_model`` on fabricated snapshots: precisely the rule written in the module doc."""

    def snap(self, model):
        return SimpleNamespace(model=model, prov={})

    def record_for(self, model, **extra):
        findings, _s, _o = review.collect(review.Context(model, {}))
        record = review.make_record(model, model["meta"]["mode"], findings)
        record.update(extra)
        return record

    def test_changed_model_without_new_block_is_accepted(self):
        model = synth.generate("small", 2)
        record = self.record_for(model)
        changed = copy.deepcopy(model)
        changed["nodes"][0]["sub"] = "A changed description."
        self.assertNotEqual(am.model_sha(changed), record["modelSha"])
        self.assertTrue(review.accepts_changed_model(self.snap(changed), record))

    def test_a_new_block_finding_is_not_accepted_and_an_old_one_is(self):
        model = copy.deepcopy(synth.generate("small", 2))
        victim, other = model["nodes"][0], model["nodes"][1]
        victim["verification"], victim["evidenceIds"] = "Implemented", []
        record = self.record_for(model)
        self.assertIn(f"RV-E1:{victim['id']}", record["blockKeys"])
        harmless = copy.deepcopy(model)
        harmless["nodes"][2]["sub"] = "Another description."
        self.assertTrue(review.accepts_changed_model(self.snap(harmless), record), "the surviving BLOCK was acknowledged in the record")
        worse = copy.deepcopy(harmless)
        worse["nodes"][1]["verification"], worse["nodes"][1]["evidenceIds"] = "Implemented", []
        self.assertFalse(review.accepts_changed_model(self.snap(worse), record))
        self.assertEqual(review.block_keys(worse) - set(record["blockKeys"]), {f"RV-E1:{other['id']}"})

    def test_new_not_authored_slots_count_as_new_blocks(self):
        model = copy.deepcopy(synth.generate("small", 2))
        record = self.record_for(model)
        changed = copy.deepcopy(model)
        changed["nodes"][0]["sub"] = ""
        self.assertFalse(review.accepts_changed_model(self.snap(changed), record))

    def test_records_of_other_writers_are_judged_by_the_sha_only(self):
        model = synth.generate("small", 2)
        legacy = {"modelSha": am.model_sha8(model), "findings": [], "fixed": [], "notes": ""}
        changed = copy.deepcopy(model)
        changed["nodes"][0]["sub"] = "x"
        self.assertFalse(review.accepts_changed_model(self.snap(changed), legacy))
        self.assertFalse(review.accepts_changed_model(self.snap(changed), None))
        self.assertFalse(review.accepts_changed_model(self.snap(changed), {"modelSha": "nothex", "blockKeys": []}))
        self.assertTrue(st.review_is_current(legacy, model), "an exact 8-digit sha still counts in status")

    def test_record_state_names_the_new_block(self):
        model = copy.deepcopy(synth.generate("small", 2))
        record = self.record_for(model)
        worse = copy.deepcopy(model)
        worse["nodes"][0]["verification"], worse["nodes"][0]["evidenceIds"] = "Implemented", []
        view = review.record_state(self.snap(worse), record)
        self.assertEqual(view["state"], "stale")
        self.assertEqual(view["newBlock"], [f"RV-E1:{worse['nodes'][0]['id']}"])
        self.assertEqual(review.record_state(self.snap(model), record)["state"], "current")


if __name__ == "__main__":
    unittest.main()
