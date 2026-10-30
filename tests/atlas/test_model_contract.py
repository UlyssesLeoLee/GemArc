"""Contract tests for ``gemarc-atlas/1``: derivation helpers, id grammar, canonical JSON, vocabulary and profile.

All ids are synthetic. The worked example is the generic lending-desk family from the design.
Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_model_contract*.py"
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from gemarc_mcp import model as canonical_model
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import vocab

REPO = Path(__file__).resolve().parents[2]
CANONICAL_VOCAB = REPO / "src" / "gemarc_mcp" / "scaffold" / "vocab"
PROFILE_PATH = REPO / "src" / "gemarc_mcp" / "atlas" / "profiles" / "software.json"

# Ids that the atlas adds on top of the canonical GemArc vocabulary. Anything not listed here must be canonical.
ADDITIVE = {
    "kinds": {"API", "Storage", "Workflow", "Element"},
    "relations": {"references"},
    "caseCategories": {"concurrency"},
    "views": set(),  # the layering view reuses the canonical id dependency-diagram
}


def canonical_ids(file_name: str, key: str) -> list[str]:
    data = json.loads((CANONICAL_VOCAB / f"{file_name}.json").read_text(encoding="utf-8"))
    return [item["id"] for item in data[key]]


def lending_model(mode: str = "asis") -> dict:
    """The design's worked example, built only through the contract helpers."""
    p = vocab.default_profile()
    m = am.empty_model("lending-desk", "Library lending desk", mode, ("zh-CN", "en-US"), repos=("main",))
    b_lib = am.build_building("zone.lib", "Library system", "internal")
    b_ext = am.build_building("zone.ext", "Outside", "external")
    f_core = am.build_floor("lvl.core", "zone.lib", "Core services", 1)
    f_out = am.build_floor("lvl.out", "zone.ext", "Visitors", 0)
    r_desk = am.build_room("rm.desk", "lvl.core", "Lending desk", tier="T2", area="lending")
    r_vis = am.build_room("rm.vis", "lvl.out", "Members", tier="T1")
    m["space"] = {"buildings": [b_lib, b_ext], "floors": [f_core, f_out], "rooms": [r_desk, r_vis]}

    ev = [
        am.build_evidence("EVD-1", "src/loan.py", "def create_loan(", "creates the loan row", "main", mode=mode),
        am.build_evidence("EVD-2", "src/stock.py", "def find_copy(", "shelf lookup", "main", mode=mode),
        am.build_evidence("EVD-3", "docs/lending.md", "borrower opens the page", "the borrower actor", "main", mode=mode),
        am.build_evidence("EVD-4", "user:brief", "members may borrow three copies", "the lending limit", mode=mode),
    ]
    m["evidences"] = ev
    ev_index = {e["evidenceId"]: e for e in ev}

    def kinds(ids):
        return am.evidence_kinds_of(ids, ev_index)

    machine_ids = {"st.loan"}
    spec = [
        ("svc.loan", "Module", "Loan service", "rm.desk", "Creates a loan and takes returns.", ["EVD-1", "EVD-2"], None),
        ("svc.stock", "Module", "Stock service", "rm.desk", "Knows which copy is on the shelf.", ["EVD-2"], None),
        ("tbl.loan", "Storage", "Loan table", "rm.desk", "One row per open loan.", ["EVD-1"], None),
        ("usr.member", "Actor", "Member", "rm.vis", "Borrower using the web page.", ["EVD-3"], "Inferred"),
        ("st.loan", "State", "Loan status", "rm.desk", "Lifecycle of one loan.", ["EVD-1"], None),
    ]
    rooms = {"rm.desk": r_desk, "rm.vis": r_vis}
    nodes = []
    for node_id, kind, name, room_id, sub, evidence_ids, verif in spec:
        room = rooms[room_id]
        building = b_ext if room_id == "rm.vis" else b_lib
        nodes.append(am.build_node(node_id, kind, name, room_id, sub, evidence_ids, verif, None, None, None,
                                   evidence_kinds=kinds(evidence_ids), is_machine=node_id in machine_ids, mode=mode,
                                   room=room, building=building, profile=p))
    m["nodes"] = nodes
    node_by_id = {n["id"]: n for n in nodes}

    def edge(src, rel, tgt, label, evidence_ids, **kw):
        return am.build_edge(src, rel, tgt, label, evidence_ids, evidence_kinds=kinds(evidence_ids), machine_ids=machine_ids,
                             mode=mode, profile=p, **kw)

    edges = [
        edge("usr.member", "calls", "svc.loan", "borrow a copy", ["EVD-3"]),
        edge("svc.loan", "calls", "svc.stock", "check copy status", ["EVD-2"]),
        edge("svc.loan", "writes", "tbl.loan", "write loan row", ["EVD-1"]),
        edge("svc.loan", "calls", "svc.stock", "check again after a retry", ["EVD-2"], alias="e.stock-retry"),
    ]
    machine = am.build_machine("st.loan", "open", [
        {"from": "open", "to": "returned"},
        {"from": "open", "to": "overdue"},
        {"from": "overdue", "to": "returned", "id": "tr.pay-late", "guard": {"param": "fee", "op": "present"}, "errorCode": "FEE_MISSING"},
    ], "BAD_TRANSITION", ["EVD-1"])
    m["machines"] = [machine]
    edges.append(am.build_owns_edge("svc.loan", machine, node_by_id["st.loan"], evidence_kinds=kinds(machine["evidenceIds"]),
                                    machine_ids=machine_ids, mode=mode, profile=p))
    m["edges"] = edges
    m["ports"] = am.derive_ports(nodes, machine_ids, p.record_kinds)

    m["tests"] = [am.build_test("TS-1", "A member can borrow a copy")]
    m["tbds"] = [am.build_tbd("TBD-1", "Loan period", "Open: how many days?", ["EVD-4"])]
    m["requirements"] = [am.build_requirement(
        "RQ-1", "A member can borrow an available copy", "interview 1", ["usr.member", "svc.loan", "svc.stock", "tbl.loan"],
        None, ["TS-1"], ["EVD-4"], ["TBD-1"], edges=edges)]
    m["cases"] = [am.build_case(
        "CS-BORROW-OK", "normal", "Member borrows an available copy",
        [("Call", "usr.member", "svc.loan", {"ruleId": "R-BORROW"}),
         ("Call", "svc.loan", "svc.stock", {"ruleId": "R-CHECK", "latencyMs": 20}),
         ("Return", "svc.stock", "svc.loan"),
         ("Call", "svc.loan", "tbl.loan", {"ruleId": "R-WRITE"}),
         ("Return", "tbl.loan", "svc.loan"),
         ("Transition", "svc.loan", "st.loan", {"params": {"target": "returned"}}),
         ("Return", "svc.loan", "usr.member")],
        ["RQ-1"], ["EVD-1"], None, {"locale": "en"}, {"st.loan": "open"},
        {"usr.member": "Completed", "svc.loan": "Completed", "svc.stock": "Completed", "tbl.loan": "Completed", "st.loan": "returned"},
        None, "author", None, m["requirements"])]
    element = {
        "elementId": "el.cache", "kind": "Cache", "role": "answers repeated stock checks",
        "inputPorts": [{"id": "el.cache:in", "type": "Message"}], "outputPorts": [{"id": "el.cache:out", "type": "Message"}],
        "stateMachine": {"states": ["Cold", "Warm"], "initial": "Cold", "transitions": [{"from": "Cold", "to": "Warm", "on": "receive"}]},
        "eventHandlers": [{"on": "receive", "do": "forward", "setState": "Warm"}], "guardRules": [],
        "errorRules": [{"on": "timeout", "do": "fail"}], "resourceLimits": {"maxEvents": 20, "maxHops": 4},
        "terminationConditions": ["maxEvents"], "latencyMs": 5,
    }
    stock_edge = next(e for e in edges if e["id"] == "svc.loan:calls:svc.stock")
    m["interventions"] = [
        am.build_intervention("IV-STOCK-DOWN", "disable_module", "Stock service is down while borrowing", ["CS-BORROW-OK"],
                              {"outcomeClass": "DIVERGED", "terminal": {"svc.stock": "Blocked"}}, "", None, targetNodeId="svc.stock"),
        am.build_intervention("IV-CACHE", "insert_element", "Put a cache in front of the stock service", ["CS-BORROW-OK"],
                              {"outcomeClass": "CONVERGED"}, "no visible change", "the cache answers from memory",
                              element=element, rewrite=[am.build_rewrite_entry("el.cache", 0, stock_edge)]),
        am.build_intervention("IV-GUARD", "change_guard", "Drop the late-fee guard", ["CS-BORROW-OK"], {"outcomeClass": "NO_DIFFERENCE"},
                              machineId="st.loan", transitionId="tr.pay-late", guard=None),
        am.build_intervention("IV-BOTH", "combined_intervention", "Stock down and slow writes", ["CS-BORROW-OK"],
                              {"outcomeClass": "DIVERGED"}, parts=[
                                  {"type": "disable_module", "targetNodeId": "svc.stock"},
                                  {"type": "change_timeout", "timeoutMs": 500}]),
    ]
    m["policies"] = [am.build_policy("PL-1", "A member may hold at most three copies.", "RQ-1", "all", "REQ", 9, "nfr", "user")]
    return m


def issue_codes(model: dict) -> set[str]:
    return {i["code"] for i in am.contract_issues(model)}


class IdGrammarTests(unittest.TestCase):
    def test_id_re_is_the_canonical_pattern(self):
        self.assertEqual(am.ID_RE.pattern, canonical_model.ID_RE.pattern)
        self.assertEqual(am.ID_RE.flags, canonical_model.ID_RE.flags)

    def test_authored_id_is_a_strict_subset(self):
        self.assertTrue(am.is_authored_id("svc.loan-2_x"))
        self.assertTrue(am.is_authored_id("a" * 124))
        self.assertFalse(am.is_authored_id("a" * 125))
        self.assertFalse(am.is_authored_id("svc:loan"))
        self.assertFalse(am.is_authored_id(""))
        self.assertFalse(am.is_authored_id(".x"))
        for sample in ("svc.loan", "a" * 124, "x-y.z_1"):
            self.assertTrue(am.is_authored_id(sample) and am.is_id(sample))
        # anything authored is also canonical
        self.assertTrue(am.is_id("a:b") and not am.is_authored_id("a:b"))

    def test_trailing_newline_is_not_an_id(self):
        self.assertFalse(am.is_id("svc.loan\n"))
        self.assertFalse(am.is_authored_id("svc.loan\n"))
        self.assertFalse(am.is_id(None))
        self.assertFalse(am.is_id(5))

    def test_state_and_token_grammar(self):
        self.assertTrue(am.is_state_name("Open"))
        self.assertTrue(am.is_state_name("a_to"))
        self.assertFalse(am.is_state_name("1open"))
        self.assertFalse(am.is_state_name("a.b"))
        self.assertFalse(am.is_state_name("x" * 41))


class DerivedIdTests(unittest.TestCase):
    def test_plain_edge_id_and_threshold(self):
        self.assertEqual(am.edge_id("svc.a", "calls", "svc.b"), "svc.a:calls:svc.b")
        src = "s" * 50
        self.assertEqual(am.MAX_PLAIN_ID, 120)  # pinned: the spec says "above 120 characters"
        tgt_len = 120 - 50 - 5 - 2
        self.assertEqual(len(am.edge_id(src, "calls", "t" * tgt_len)), 120)
        self.assertEqual(am.edge_id(src, "calls", "t" * tgt_len), f"{src}:calls:{'t' * tgt_len}")
        hashed = am.edge_id(src, "calls", "t" * (tgt_len + 1))
        self.assertTrue(hashed.startswith("h:") and len(hashed) == 34)

    def test_hash_fallback_is_deterministic_and_documented(self):
        src, tgt = "a" * 124, "b" * 124
        expected = "h:" + hashlib.sha256(f"edge\n{src}\ncalls\n{tgt}".encode("utf-8")).hexdigest()[:32]
        self.assertEqual(am.edge_id(src, "calls", tgt), expected)
        self.assertEqual(am.edge_id(src, "calls", tgt), am.edge_id(src, "calls", tgt))
        self.assertNotEqual(am.edge_id(src, "calls", tgt), am.edge_id(src, "writes", tgt))
        self.assertNotEqual(am.edge_id(src, "calls", tgt), am.edge_id(tgt, "calls", src))

    def test_hash_domain_separates_edge_and_transition(self):
        self.assertNotEqual(am.hash_id("edge", "a", "b", "c"), am.hash_id("transition", "a", "b", "c"))

    def test_colon_in_a_part_is_rejected(self):
        # 'a:b' + 'calls' + 'c' and 'a' + 'calls' + 'b:calls:c' would otherwise collide on the same id
        with self.assertRaises(am.AtlasModelError) as ctx:
            am.edge_id("a:b", "calls", "c")
        self.assertEqual(ctx.exception.code, am.E_BAD_PART)
        with self.assertRaises(am.AtlasModelError):
            am.edge_id("a", "calls", "b:calls:c")
        with self.assertRaises(am.AtlasModelError):
            am.edge_id("a", "", "c")
        with self.assertRaises(am.AtlasModelError):
            am.edge_id("a\nb", "calls", "c")

    def test_every_derived_id_passes_id_re(self):
        longest = "n" * 124
        samples = [
            am.edge_id("svc.a", "calls", "svc.b"), am.edge_id(longest, "calls", longest), am.edge_id(longest, "x", "y"),
            am.port_id(longest, "in"), am.port_id(longest, "out"),
            am.transition_id("m", "A", "B"), am.transition_id(longest, "S" * 40, "T" * 40), am.transition_id("m", "S" * 40, "T" * 40),
            am.chain_edge_id(longest, 1), am.chain_edge_id(longest, am.MAX_CHAIN_ORDINAL),
        ]
        for sample in samples:
            self.assertTrue(am.is_id(sample), sample)
            self.assertLessEqual(len(sample), 128)

    def test_port_id_direction(self):
        self.assertIs(am.node_port_ids, am.port_ids)
        self.assertEqual(am.port_ids("svc.a"), ["svc.a:in", "svc.a:out"])
        self.assertEqual(am.port_ids("st.a", is_machine=True), [])
        with self.assertRaises(am.AtlasModelError):
            am.port_id("svc.a", "sideways")

    def test_port_id_cannot_collide_with_a_node_id(self):
        # a node id has no ':'; a port id has exactly one ':' followed by in/out
        self.assertFalse(am.is_authored_id("x:in"))
        self.assertEqual(am.port_id("x", "in").count(":"), 1)

    def test_chain_ordinal_bounds(self):
        self.assertEqual(am.chain_edge_id("el.x", 1), "el.x:1")
        for bad in (0, -1, am.MAX_CHAIN_ORDINAL + 1, True, "1"):
            with self.assertRaises(am.AtlasModelError):
                am.chain_edge_id("el.x", bad)


class EdgeReferenceTests(unittest.TestCase):
    def test_repeated_triple_needs_an_alias(self):
        first = am.build_edge("a", "calls", "b", "first")
        again = am.build_edge("a", "calls", "b", "second")
        self.assertEqual(first["id"], again["id"])
        model = lending_model()
        broken = copy.deepcopy(model)
        broken["edges"].append(am.build_edge("svc.loan", "writes", "tbl.loan", "same triple again", machine_ids={"st.loan"}))
        self.assertIn(am.E_DUP, issue_codes(broken))
        fixed = copy.deepcopy(model)
        fixed["edges"].append(am.build_edge("svc.loan", "writes", "tbl.loan", "same triple again", alias="e.write-twice", machine_ids={"st.loan"}))
        self.assertEqual(am.contract_issues(fixed), [])

    def test_alias_becomes_the_id_verbatim(self):
        edge = am.build_edge("a", "calls", "b", "x", alias="e.special")
        self.assertEqual(edge["id"], "e.special")
        self.assertNotIn("alias", edge)
        for bad in ("bad alias", "a:calls:b", "x:y", "h:" + "0" * 32, "a" * 125, ""):
            with self.assertRaises(am.AtlasModelError) as ctx:
                am.build_edge("a", "calls", "b", "x", alias=bad)
            self.assertEqual(ctx.exception.code, am.E_ID, bad)
        self.assertEqual(am.build_edge("a", "calls", "b", "x", alias="a" * 124)["id"], "a" * 124)

    def test_checker_tells_a_derived_edge_id_from_a_smuggled_one(self):
        base = lending_model()
        self.assertEqual(am.contract_issues(base), [])
        for smuggled in ("foo:bar", "x:calls:y", "svc.loan:writes:tbl.loan:extra", "h:" + "0" * 32):
            model = copy.deepcopy(base)
            model["edges"][1]["id"] = smuggled
            issues = [i for i in am.contract_issues(model) if i["path"] == "$.edges[1].id"]
            self.assertTrue(any(i["code"] == am.E_ID for i in issues), (smuggled, issues))
        # aliases without a colon and the true derived ids stay valid
        self.assertEqual({e["id"] for e in base["edges"] if ":" not in e["id"]}, {"e.stock-retry"})
        model = copy.deepcopy(base)
        model["edges"][1]["id"] = "e.renamed"
        self.assertFalse([i for i in am.contract_issues(model) if i["code"] == am.E_ID])  # only dangling references remain

    def test_a_typo_triple_does_not_resolve_to_an_edge_with_a_smuggled_id(self):
        # x:calls:y is not an edge id anywhere in a conformant model, so it can never shadow a triple lookup
        index = am.edge_ref_index(lending_model()["edges"])
        self.assertNotIn("x:calls:y", index)

    def test_ref_index_is_exact_and_never_parses_colons(self):
        model = lending_model()
        index = am.edge_ref_index(model["edges"])
        self.assertEqual(index["e.stock-retry"], "e.stock-retry")
        self.assertEqual(index["svc.loan:writes:tbl.loan"], "svc.loan:writes:tbl.loan")
        # the triple svc.loan:calls:svc.stock is used by two edges, so it resolves to the unaliased one only through its own id
        self.assertEqual(index["svc.loan:calls:svc.stock"], "svc.loan:calls:svc.stock")
        self.assertNotIn("svc.loan:calls", index)

    def test_ref_index_resolves_the_plain_triple_of_a_hashed_edge(self):
        src, tgt = "a" * 100, "b" * 100
        edge = am.build_edge(src, "calls", tgt, "long")
        self.assertTrue(edge["id"].startswith("h:"))
        index = am.edge_ref_index([edge])
        self.assertEqual(index[f"{src}:calls:{tgt}"], edge["id"])

    def test_ref_index_omits_an_ambiguous_triple(self):
        one = am.build_edge("a", "calls", "b", "x", alias="e.one")
        two = am.build_edge("a", "calls", "b", "y", alias="e.two")
        index = am.edge_ref_index([one, two])
        self.assertEqual(set(index), {"e.one", "e.two"})


class PortTests(unittest.TestCase):
    def test_derive_ports_order_type_and_machine_skip(self):
        nodes = [{"id": "svc.a", "kind": "Module"}, {"id": "st.a", "kind": "State"}, {"id": "ev.a", "kind": "Event"},
                 {"id": "usr.a", "kind": "Actor"}]
        ports = am.derive_ports(nodes, {"st.a"})
        self.assertEqual([p["id"] for p in ports], ["svc.a:in", "svc.a:out", "ev.a:in", "ev.a:out", "usr.a:in", "usr.a:out"])
        self.assertEqual({p["id"]: p["type"] for p in ports}["ev.a:in"], "Record")
        self.assertEqual({p["id"]: p["type"] for p in ports}["svc.a:out"], "Message")
        self.assertTrue(all(set(p) == {"id", "nodeId", "direction", "type"} for p in ports))

    def test_record_kinds_come_from_the_profile_flag(self):
        self.assertEqual(vocab.default_profile().record_kinds, frozenset({"Event"}))
        self.assertEqual(am.port_type_for_kind("Storage"), "Message")
        self.assertEqual(am.port_type_for_kind("Storage", {"Storage"}), "Record")

    def test_machine_node_ports_are_null(self):
        self.assertEqual(am.edge_ports("svc.a", "st.a", {"st.a"}), ("svc.a:out", None))
        self.assertEqual(am.edge_ports("st.a", "svc.a", {"st.a"}), (None, "svc.a:in"))
        self.assertEqual(am.edge_ports("svc.a", "svc.b"), ("svc.a:out", "svc.b:in"))
        edge = am.build_edge("svc.a", "owns", "st.a", "owns", machine_ids={"st.a"})
        self.assertIsNone(edge["targetPortId"])
        self.assertEqual(edge["sourcePortId"], "svc.a:out")


class EdgeFieldTests(unittest.TestCase):
    def test_constant_and_derived_edge_fields(self):
        edge = am.build_edge("svc.a", "writes", "tbl.a", "write row")
        self.assertEqual((edge["type"], edge["directed"], edge["guardId"]), ("directed", True, None))
        self.assertIs(edge["static"], False)
        self.assertEqual(edge["id"], "svc.a:writes:tbl.a")
        self.assertEqual(am.build_edge("a", "dependsOn", "b", "x")["static"], True)
        self.assertEqual(am.build_edge("a", "references", "b", "x", cardinality="N:1")["cardinality"], "N:1")
        self.assertNotIn("cardinality", edge)

    def test_unknown_relation_is_rejected(self):
        with self.assertRaises(am.AtlasModelError) as ctx:
            am.build_edge("a", "depends_on", "b", "x")
        self.assertEqual(ctx.exception.code, am.E_ENUM)


class VerificationTests(unittest.TestCase):
    def test_node_default_rule(self):
        self.assertEqual(am.default_verification_for_node(["source-code", "document"]), "Implemented")
        self.assertEqual(am.default_verification_for_node(["document"]), "Designed")
        self.assertEqual(am.default_verification_for_node(["user-requirement"]), "Designed")
        self.assertEqual(am.default_verification_for_node([]), "Hypothetical")

    def test_edge_default_uses_own_evidence_and_inherited_cap(self):
        self.assertEqual(am.default_verification_for_edge(["source-code"]), "Implemented")
        self.assertEqual(am.default_verification_for_edge(["source-code"], inherited=True), "Designed")
        self.assertEqual(am.default_verification_for_edge(["document"], inherited=True), "Designed")
        self.assertEqual(am.default_verification_for_edge([], inherited=True), "Hypothetical")

    def test_tobe_mode_never_derives_above_designed(self):
        self.assertEqual(am.default_verification_for_node(["source-code"], "tobe"), "Designed")
        self.assertEqual(am.default_verification_for_edge(["source-code"], "tobe"), "Designed")
        self.assertEqual(am.default_verification_for_node([], "tobe"), "Hypothetical")
        self.assertEqual(am.evidence_verification("source-code", "tobe"), "Designed")
        self.assertEqual(am.evidence_verification("source-code", "asis"), "Implemented")

    def test_weakest_uses_gemarc_rank(self):
        self.assertEqual(am.weakest(["Implemented", "Observed"]), "Observed")
        self.assertEqual(am.weakest(["Designed", "Inferred", "Implemented"]), "Inferred")
        self.assertEqual(am.weakest([]), "Unknown")
        self.assertEqual(am.weakest(["nonsense"]), "Unknown")
        self.assertEqual(am.cap_verification("Implemented", "Designed"), "Designed")
        self.assertEqual(am.cap_verification("Inferred", "Designed"), "Inferred")

    def test_inherited_evidence_is_source_first_and_deduplicated(self):
        self.assertEqual(am.inherit_edge_evidence(["E1", "E2"], ["E2", "E3"]), ["E1", "E2", "E3"])
        self.assertEqual(am.inherit_edge_evidence([], []), [])

    def test_evidence_kinds_of_skips_unresolved_ids(self):
        index = {"E1": {"sourceKind": "document"}}
        self.assertEqual(am.evidence_kinds_of(["E1", "E9"], index), ["document"])


class EvidenceTests(unittest.TestCase):
    def test_kind_precedence_and_extension_rules(self):
        cases = {
            "src/loan.py": "source-code", "docs/lending.md": "document", "README.MD": "document", "a/b/Notes.Pdf": "document",
            "Dockerfile": "source-code", "Makefile": "source-code", "dir.md/script.sh": "source-code",
            "user:brief": "user-requirement", "user:brief.md": "user-requirement", "user:x": "user-requirement",
            ".md": "source-code", "a\\b\\spec.docx": "document", "archive.tar.gz": "source-code",
            # the extension is the part after the LAST dot of the LAST path segment
            "docs/.md": "source-code", "dir.pdf/.md": "source-code", "dir.md/Makefile": "source-code", "x/.hidden.md": "document",
            "notes.v2.md": "document", "x.y/z.v2.pdf": "document", "report.final.docx": "document", "file.": "source-code", "a.b/c.d.py": "source-code",
        }
        for path, expected in cases.items():
            self.assertEqual(am.evidence_kind(path), expected, path)

    def test_plain_text_manifests_are_source_not_documents(self):
        for path in ("requirements.txt", "CMakeLists.txt", "notes/robots.txt"):
            self.assertEqual(am.evidence_kind(path), "source-code", path)
        self.assertNotIn(".txt", vocab.default_profile().doc_ext)

    def test_explicit_doc_ext_override(self):
        self.assertEqual(am.evidence_kind("a.xyz", [".XYZ"]), "document")
        self.assertEqual(am.evidence_kind("a.md", [".txt"]), "source-code")

    def test_build_evidence_fields(self):
        doc = am.build_evidence("E1", "docs/a.md", "text", "claim", "main")
        self.assertEqual((doc["sourceKind"], doc["verification"], doc["lineRange"], doc["repository"]), ("document", "Designed", None, "main"))
        code = am.build_evidence("E2", "src/a.py", "def f(", "claim", "main", line_range=(3, 5))
        self.assertEqual((code["sourceKind"], code["verification"], code["lineRange"]), ("source-code", "Implemented", [3, 5]))
        user = am.build_evidence("E3", "user:brief", "quote", "claim")
        self.assertEqual((user["sourceKind"], user["verification"], user["repository"]), ("user-requirement", "Designed", ""))
        self.assertEqual(set(doc), {"evidenceId", "sourceKind", "repository", "filePath", "lineRange", "anchor", "topic", "verification"})
        self.assertEqual(am.build_evidence("E4", "src/a.py", "x", "y", mode="tobe")["verification"], "Designed")


class SpaceAndPropsTests(unittest.TestCase):
    def test_inheritance_and_authored_wins(self):
        room = {"id": "rm.a", "tier": "T2", "area": "billing"}
        building = {"id": "zone.a", "trust": "internal"}
        self.assertEqual(am.inherit_props(None, room, building), {"tier": "T2", "area": "billing", "trust": "internal"})
        self.assertEqual(am.inherit_props({"tier": "T4", "trust": "restricted", "x": 1}, room, building),
                         {"tier": "T4", "area": "billing", "trust": "restricted", "x": 1})
        self.assertEqual(am.inherit_props({"x": 1}, {"id": "rm.b"}, None), {"x": 1})

    def test_inheritance_does_not_alias_authored_props(self):
        authored = {"columns": ["id"]}
        out = am.inherit_props(authored, None, None)
        out["columns"].append("x")
        self.assertEqual(authored, {"columns": ["id"]})

    def test_node_builder_inherits_and_orders_derived_fields(self):
        room = am.build_room("rm.a", "lvl.a", "Room A", tier="T3", area="core")
        building = am.build_building("zone.a", "Zone", "perimeter")
        node = am.build_node("svc.a", "Module", "A", "rm.a", "sub", ["E1"], None, {"owner": "svc.b"}, "grp", "DD",
                             evidence_kinds=["source-code"], room=room, building=building)
        self.assertEqual(node["props"], {"owner": "svc.b", "tier": "T3", "area": "core", "trust": "perimeter"})
        self.assertEqual((node["verification"], node["portIds"], node["group"], node["level"]),
                         ("Implemented", ["svc.a:in", "svc.a:out"], "grp", "DD"))
        bare = am.build_node("svc.b", "Module", "B", "rm.a")
        self.assertNotIn("group", bare)
        self.assertNotIn("level", bare)
        self.assertEqual(bare["verification"], "Hypothetical")

    def test_building_default_trust_and_room_optionals(self):
        self.assertEqual(am.build_building("z", "Z")["trust"], "internal")
        self.assertEqual(am.build_room("r", "f", "R"), {"id": "r", "floorId": "f", "name": "R"})

    def test_level_and_domain_resolution(self):
        p = vocab.default_profile()
        biz = {"id": "rm.biz", "layer": "business"}
        plain = {"id": "rm.plain"}
        self.assertEqual(am.node_level({"kind": "Module"}, biz), "REQ")
        self.assertEqual(am.node_level({"kind": "Table"}, biz), "BD")
        self.assertEqual(am.node_level({"kind": "Module"}, plain), "BD")
        self.assertEqual(am.node_level({"kind": "Actor"}, plain), "REQ")
        self.assertEqual(am.node_level({"kind": "Module", "level": "DD"}, biz), "DD")
        self.assertEqual(am.node_level({"kind": "Strange"}, plain), "DD")
        self.assertEqual(am.relation_level("calls"), "REQ")
        self.assertEqual(am.relation_level("strange"), "DD")
        building = {"trust": "restricted"}
        self.assertEqual(am.node_domain({"kind": "Module", "props": {}}, plain, building), "restricted")
        self.assertEqual(am.node_domain({"kind": "Module", "props": {"trust": "external"}}, plain, building), "external")
        self.assertEqual(am.node_domain({"kind": "Module", "props": {}}, biz, building), "business")
        self.assertEqual(am.node_domain({"kind": "State", "props": {}}, plain, building), "business")
        self.assertEqual(am.node_domain({"kind": "Module", "props": {"trust": "weird"}}, plain, None), p.default_trust)


class MachineTests(unittest.TestCase):
    def test_states_first_seen_and_terminal(self):
        transitions = [("a", "b"), ("a", "c"), ("c", "b"), ("d", "a")]
        self.assertEqual(am.machine_states("a", transitions), ["a", "b", "c", "d"])
        self.assertEqual(am.machine_terminal(["a", "b", "c", "d"], transitions), ["b"])
        self.assertEqual(am.machine_states("only", []), ["only"])
        self.assertEqual(am.machine_terminal(["only"], []), ["only"])

    def test_cyclic_machine_has_no_terminal(self):
        transitions = [("x", "y"), ("y", "x")]
        self.assertEqual(am.machine_terminal(am.machine_states("x", transitions), transitions), [])

    def test_build_machine_ids_guard_and_error_code(self):
        machine = am.build_machine("st.a", "open", [
            {"from": "open", "to": "closed"},
            {"from": "closed", "to": "open", "id": "tr.reopen", "guard": {"param": "ok", "op": "present"}, "errorCode": "E_REOPEN"},
        ], None, ["E1"])
        self.assertEqual([t["id"] for t in machine["transitions"]], ["st.a.open_to_closed", "tr.reopen"])
        self.assertEqual(machine["transitions"][0], {"id": "st.a.open_to_closed", "from": "open", "to": "closed"})
        self.assertEqual(machine["transitions"][1]["guard"], {"param": "ok", "op": "present"})
        self.assertEqual((machine["id"], machine["nodeId"], machine["errorCode"], machine["terminal"]), ("st.a", "st.a", None, []))
        self.assertEqual(machine["states"], ["open", "closed"])

    def test_underscore_states_can_derive_the_same_transition_id(self):
        pairs = [{"from": "a_to", "to": "b"}, {"from": "a", "to": "to_b"}]
        ids, dups = am.assign_transition_ids("m", pairs)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(dups, [ids[0]])
        with self.assertRaises(am.AtlasModelError) as ctx:
            am.build_machine("m", "a_to", pairs)
        self.assertEqual(ctx.exception.code, am.E_DUP)
        fixed = [dict(pairs[0]), dict(pairs[1], id="tr.second")]
        self.assertEqual(am.assign_transition_ids("m", fixed)[1], [])
        self.assertEqual(len(am.build_machine("m", "a_to", fixed)["transitions"]), 2)

    def test_duplicate_pair_without_explicit_id_is_a_duplicate_id(self):
        _, dups = am.assign_transition_ids("m", [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}])
        self.assertEqual(len(dups), 1)

    def test_long_transition_id_falls_back_to_a_hash(self):
        tid = am.transition_id("m" * 124, "S" * 40, "T" * 40)
        self.assertEqual(tid, "h:" + hashlib.sha256(("transition\n" + "m" * 124 + "\n" + "S" * 40 + "\n" + "T" * 40).encode()).hexdigest()[:32])

    def test_generated_owns_edge_is_pinned(self):
        machine = am.build_machine("st.a", "open", [{"from": "open", "to": "done"}], None, ["E1", "E2"])
        node = {"id": "st.a", "name": "Order status"}
        edge = am.build_owns_edge("svc.a", machine, node, evidence_kinds=["document", "source-code"], machine_ids={"st.a"})
        self.assertEqual(edge["id"], "svc.a:owns:st.a")
        self.assertEqual((edge["relation"], edge["label"], edge["evidenceIds"], edge["verification"]),
                         ("owns", "Order status", ["E1", "E2"], "Implemented"))
        self.assertEqual((edge["sourcePortId"], edge["targetPortId"], edge["static"]), ("svc.a:out", None, False))
        bare = am.build_owns_edge("svc.a", am.build_machine("st.a", "open"), node, machine_ids={"st.a"})
        self.assertEqual((bare["evidenceIds"], bare["verification"]), ([], "Hypothetical"))

    def test_authored_duplicate_of_the_owns_edge_is_an_error(self):
        model = lending_model()
        owns = next(e for e in model["edges"] if e["relation"] == "owns")
        model["edges"].append(copy.deepcopy(owns))
        self.assertIn(am.E_DUP, issue_codes(model))


class PolicyAndCaseTests(unittest.TestCase):
    def test_default_policy_is_one_immutable_constant(self):
        self.assertEqual(dict(am.DEFAULT_POLICY), {"timeoutMs": 2000, "retryMax": 0, "retryBackoffMs": 0})
        with self.assertRaises(TypeError):
            am.DEFAULT_POLICY["timeoutMs"] = 1  # type: ignore[index]
        fresh = am.default_policy()
        fresh["timeoutMs"] = 5
        self.assertEqual(am.DEFAULT_POLICY["timeoutMs"], 2000)

    def test_merge_policy_fills_missing_keys(self):
        self.assertEqual(am.merge_policy(None), am.default_policy())
        self.assertEqual(am.merge_policy({"timeoutMs": 3000}), {"timeoutMs": 3000, "retryMax": 0, "retryBackoffMs": 0})
        self.assertEqual(am.merge_policy({"retryMax": 1, "retryBackoffMs": 100, "ignored": 9}),
                         {"timeoutMs": 2000, "retryMax": 1, "retryBackoffMs": 100})

    def test_linked_tests_default_is_the_union_in_citation_order(self):
        reqs = [{"id": "RQ-1", "tstIds": ["TS-1", "TS-2"]}, {"id": "RQ-2", "tstIds": ["TS-2", "TS-3"]}]
        self.assertEqual(am.default_linked_tests(["RQ-2", "RQ-1"], reqs), ["TS-2", "TS-3", "TS-1"])
        self.assertEqual(am.default_linked_tests(["RQ-9"], reqs), [])
        self.assertEqual(am.default_linked_tests([], reqs), [])

    def test_requirement_edges_default_and_inverse_case_link(self):
        edges = [{"id": "e1", "sourceId": "a", "targetId": "b"}, {"id": "e2", "sourceId": "b", "targetId": "c"}, {"id": "e3", "sourceId": "a", "targetId": "a"}]
        self.assertEqual(am.default_requirement_edges(["a", "b"], edges), ["e1", "e3"])
        self.assertEqual(am.default_requirement_edges([], edges), [])
        cases = [{"caseId": "C1", "requirementIds": ["R1", "R2"]}, {"caseId": "C2", "requirementIds": ["R2"]}]
        self.assertEqual(am.cases_of_requirement(cases, "R2"), ["C1", "C2"])
        self.assertEqual(am.cases_of_requirement(cases, "R1"), ["C1"])

    def test_events_are_numbered_in_row_order_and_options_are_checked(self):
        events = am.number_events([("Call", "a", "b", {"ruleId": "R1", "latencyMs": [10, 20]}), {"type": "Return", "from": "b", "to": "a", "ack": True}])
        self.assertEqual(events, [
            {"seq": 1, "type": "Call", "from": "a", "to": "b", "ruleId": "R1", "latencyMs": [10, 20]},
            {"seq": 2, "type": "Return", "from": "b", "to": "a", "ack": True},
        ])
        with self.assertRaises(am.AtlasModelError):
            am.build_event(1, "Call", "a", "b", bogus=1)
        self.assertEqual(am.build_event(1, "Call", "a", "b", ruleId=None), {"seq": 1, "type": "Call", "from": "a", "to": "b"})

    def test_case_builder_defaults(self):
        reqs = [{"id": "RQ-1", "tstIds": ["TS-1"]}]
        case = am.build_case("C-1", "normal", "t", [("Call", "a", "b")], ["RQ-1"], requirements=reqs)
        self.assertEqual(case["policy"], am.default_policy())
        self.assertEqual((case["context"], case["expectedTerminalState"], case["linkedTestIds"]), ({}, {}, ["TS-1"]))
        for optional in ("initialMachines", "expectedOutcomes", "expectedBy"):
            self.assertNotIn(optional, case)
        explicit = am.build_case("C-2", "abnormal", "t", [], ["RQ-1"], linked_tests=[], requirements=reqs,
                                 expected_outcomes=[{"ruleId": "R1", "outcome": "Rejected"}], expected_by="user", initial_machines={"st.a": "open"})
        self.assertEqual(explicit["linkedTestIds"], [])
        self.assertEqual((explicit["expectedBy"], explicit["initialMachines"]), ("user", {"st.a": "open"}))


class RewriteChainTests(unittest.TestCase):
    def setUp(self):
        self.edge = {"id": "svc.a:calls:svc.b", "sourceId": "svc.a", "targetId": "svc.b",
                     "sourcePortId": "svc.a:out", "targetPortId": "svc.b:in"}

    def test_single_rewrite_chain(self):
        chain = am.derive_rewrite_chain("el.x", 0, self.edge)
        self.assertEqual(chain, [
            {"id": "el.x:1", "sourceId": "svc.a", "targetId": "el.x", "sourcePortId": "svc.a:out", "targetPortId": "el.x:in"},
            {"id": "el.x:2", "sourceId": "el.x", "targetId": "svc.b", "sourcePortId": "el.x:out", "targetPortId": "svc.b:in"},
        ])

    def test_second_rewrite_continues_the_numbering(self):
        chain = am.derive_rewrite_chain("el.x", 1, self.edge)
        self.assertEqual([c["id"] for c in chain], ["el.x:3", "el.x:4"])

    def test_disposition_controls_the_chain(self):
        self.assertEqual(len(am.build_rewrite_entry("el.x", 0, self.edge)["chain"]), 2)
        for disposition in ("keep", "disable"):
            entry = am.build_rewrite_entry("el.x", 0, self.edge, disposition)
            self.assertEqual(entry, {"replaceEdgeId": self.edge["id"], "disposition": disposition, "chain": []})


class CanonicalJsonTests(unittest.TestCase):
    def test_keys_are_sorted_at_every_depth_and_compact(self):
        text = am.canonical_dumps({"b": {"z": 1, "a": [3, 1, 2]}, "a": None})
        self.assertEqual(text, '{"a":null,"b":{"a":[3,1,2],"z":1}}')

    def test_arrays_keep_authoring_order(self):
        self.assertEqual(am.canonical_dumps({"x": ["b", "a"]}), '{"x":["b","a"]}')

    def test_non_ascii_is_not_escaped_and_bytes_are_utf8_lf(self):
        raw = am.canonical_bytes({"name": "借出服务", "t": "貸出"})
        self.assertIn("借出服务".encode("utf-8"), raw)
        self.assertTrue(raw.endswith(b"}\n") and raw.count(b"\n") == 1 and b"\r" not in raw)

    def test_sha_ignores_dict_insertion_order_but_not_array_order(self):
        a = {"x": 1, "y": [1, 2]}
        b = {"y": [1, 2], "x": 1}
        self.assertEqual(am.model_sha(a), am.model_sha(b))
        self.assertNotEqual(am.model_sha(a), am.model_sha({"x": 1, "y": [2, 1]}))

    def test_sha_is_sha256_of_the_compact_bytes(self):
        model = lending_model()
        self.assertEqual(am.model_sha(model), hashlib.sha256(am.canonical_bytes(model)).hexdigest())
        self.assertEqual(am.model_sha8(model), am.model_sha(model)[:8])
        self.assertEqual(am.canonical_bytes(model), (am.canonical_dumps(model) + "\n").encode("utf-8"))

    def test_round_trip_through_json_is_stable(self):
        model = lending_model()
        reloaded = json.loads(am.canonical_dumps(model))
        self.assertEqual(reloaded, model)
        self.assertEqual(am.model_sha(reloaded), am.model_sha(model))

    def test_tuples_and_proxies_normalise_and_bad_values_raise(self):
        self.assertEqual(am.canonical_dumps({"a": (1, 2), "p": am.DEFAULT_POLICY}),
                         '{"a":[1,2],"p":{"retryBackoffMs":0,"retryMax":0,"timeoutMs":2000}}')
        for bad in (float("nan"), float("inf"), {1: 2}, {"s": {1, 2}}, object()):
            with self.assertRaises(am.AtlasModelError):
                am.canonical_dumps({"v": bad} if not isinstance(bad, dict) else bad)

    def test_pretty_form_is_sorted_with_lf_and_same_data(self):
        model = {"b": 1, "a": [1, {"d": 1, "c": 2}]}
        pretty = am.canonical_dumps(model, indent=2)
        self.assertNotIn("\r", pretty)
        self.assertEqual(json.loads(pretty), model)
        self.assertLess(pretty.index('"a"'), pretty.index('"b"'))

    def test_empty_model_shape(self):
        model = am.empty_model()
        self.assertEqual(list(model), ["schema"] + list(am.COLLECTIONS))
        self.assertEqual(model["schema"], "gemarc-atlas/1")
        self.assertEqual(list(model["space"]), ["buildings", "floors", "rooms"])
        self.assertEqual(am.contract_issues(model), [])
        self.assertEqual(am.contract_issues(am.empty_model(mode="tobe", languages=("ja-JP", "en-US"))), [])


class ContractCheckerTests(unittest.TestCase):
    def test_worked_example_conforms_in_both_modes(self):
        for mode in ("asis", "tobe"):
            self.assertEqual(am.contract_issues(lending_model(mode)), [], mode)
        am.check_contract(lending_model())

    def test_worked_example_has_the_documented_derived_values(self):
        model = lending_model()
        nodes = {n["id"]: n for n in model["nodes"]}
        self.assertEqual(nodes["svc.loan"]["verification"], "Implemented")
        self.assertEqual(nodes["svc.loan"]["portIds"], ["svc.loan:in", "svc.loan:out"])
        self.assertEqual(nodes["usr.member"]["verification"], "Inferred")  # authored override kept
        self.assertEqual(nodes["st.loan"]["portIds"], [])
        self.assertEqual(nodes["svc.loan"]["props"], {"tier": "T2", "area": "lending", "trust": "internal"})
        self.assertEqual(nodes["usr.member"]["props"], {"tier": "T1", "trust": "external"})
        edges = {e["id"]: e for e in model["edges"]}
        self.assertEqual(edges["svc.loan:writes:tbl.loan"]["verification"], "Implemented")
        self.assertEqual(edges["usr.member:calls:svc.loan"]["verification"], "Designed")  # document evidence only
        self.assertEqual(edges["svc.loan:owns:st.loan"]["targetPortId"], None)
        self.assertEqual(len(model["ports"]), 8)
        evidence = {e["evidenceId"]: e for e in model["evidences"]}
        self.assertEqual(evidence["EVD-3"]["lineRange"], None)
        self.assertEqual(evidence["EVD-3"]["verification"], "Designed")
        machine = model["machines"][0]
        self.assertEqual((machine["states"], machine["terminal"]), (["open", "returned", "overdue"], ["returned"]))
        self.assertEqual([t["id"] for t in machine["transitions"]], ["st.loan.open_to_returned", "st.loan.open_to_overdue", "tr.pay-late"])
        case = model["cases"][0]
        self.assertEqual([e["seq"] for e in case["events"]], list(range(1, 8)))
        self.assertEqual(case["linkedTestIds"], ["TS-1"])
        self.assertEqual(case["policy"], {"timeoutMs": 2000, "retryMax": 0, "retryBackoffMs": 0})
        self.assertEqual(model["requirements"][0]["edgeIds"][:3], ["usr.member:calls:svc.loan", "svc.loan:calls:svc.stock", "svc.loan:writes:tbl.loan"])
        chain = model["interventions"][1]["rewrite"][0]["chain"]
        self.assertEqual([c["id"] for c in chain], ["el.cache:1", "el.cache:2"])

    def catch(self, code: str, path_part: str, fn) -> None:
        """Apply ``fn`` to a fresh worked example; the checker must report ``code`` on a path containing ``path_part``."""
        model = lending_model()
        fn(model)
        issues = am.contract_issues(model)
        self.assertTrue(any(i["code"] == code and path_part in i["path"] for i in issues), f"{code} at {path_part}: {issues[:4]}")

    def clean(self, fn) -> None:
        model = lending_model()
        fn(model)
        self.assertEqual(am.contract_issues(model), [])

    def test_derived_field_tampering_is_caught(self):
        D = am.E_DERIVED
        self.catch(D, "$.ports", lambda m: m["ports"].pop())
        self.catch(D, "$.nodes[0].portIds", lambda m: m["nodes"][0].update(portIds=[]))
        self.catch(D, "$.edges[0]", lambda m: m["edges"][0].update(sourcePortId=None))
        self.catch(D, "$.edges[2]", lambda m: m["edges"][2].update(targetPortId="tbl.loan:out"))
        self.catch(D, "$.edges[0].static", lambda m: m["edges"][0].update(static=True))
        self.catch(D, "$.edges[0]", lambda m: m["edges"][0].update(type="undirected"))
        self.catch(D, "$.edges[0]", lambda m: m["edges"][0].update(directed=False))
        self.catch(am.E_FIELD, "$.edges[0].guardId", lambda m: m["edges"][0].update(guardId="g1"))  # constant null: a type error
        self.catch(D, "$.evidences[0].sourceKind", lambda m: m["evidences"][0].update(sourceKind="document"))
        self.catch(D, "$.evidences[2].verification", lambda m: m["evidences"][2].update(verification="Implemented"))
        self.catch(D, "$.evidences[3].repository", lambda m: m["evidences"][3].update(repository="main"))
        self.catch(D, "$.machines[0].terminal", lambda m: m["machines"][0].update(terminal=["open"]))
        self.catch(D, "$.machines[0].states", lambda m: m["machines"][0].update(states=["returned", "open", "overdue"]))
        self.catch(D, "$.cases[0].events[2].seq", lambda m: m["cases"][0]["events"][2].update(seq=9))
        self.catch(D, "$.cases[0].policy", lambda m: m["cases"][0]["policy"].pop("retryMax"))
        self.catch(D, "$.nodes[0].props.tier", lambda m: m["nodes"][0]["props"].pop("tier"))
        self.catch(D, "$.nodes[0].props.trust", lambda m: m["nodes"][0]["props"].pop("trust"))
        self.catch(D, "$.nodes[1].props.area", lambda m: m["nodes"][1]["props"].pop("area"))
        self.catch(D, "$.interventions[1].rewrite[0]", lambda m: m["interventions"][1]["rewrite"][0]["chain"].reverse())
        self.catch(D, "$.machines[0].nodeId", lambda m: m["machines"][0].update(nodeId="svc.loan"))

    def test_derived_evidence_follows_the_mode(self):
        model = lending_model("tobe")
        self.assertEqual(model["evidences"][0]["verification"], "Designed")  # source-code evidence capped in tobe
        model["meta"]["mode"] = "asis"
        self.assertTrue(any(i["path"] == "$.evidences[0].verification" for i in am.contract_issues(model)))

    def test_authored_props_override_is_accepted(self):
        def override(model):
            model["nodes"][0]["props"]["trust"] = "restricted"
            model["nodes"][0]["props"]["tier"] = "T4"
        self.clean(override)

    def test_vocabulary_violations_are_caught(self):
        E = am.E_ENUM
        self.catch(E, "$.nodes[0].kind", lambda m: m["nodes"][0].update(kind="Gizmo"))
        self.catch(E, "$.nodes[0].verification", lambda m: m["nodes"][0].update(verification="Confirmed"))
        self.catch(E, "$.nodes[0].level", lambda m: m["nodes"][0].update(level="XX"))
        self.catch(E, "$.edges[0].relation", lambda m: m["edges"][0].update(relation="depends_on"))
        self.catch(E, "$.cases[0].category", lambda m: m["cases"][0].update(category="combined"))
        self.catch(E, "$.cases[0].events[0].type", lambda m: m["cases"][0]["events"][0].update(type="Ping"))
        self.catch(E, "$.cases[0].events[0].role", lambda m: m["cases"][0]["events"][0].update(role="hero"))
        self.catch(E, "$.cases[0].events[0].fault.mode", lambda m: m["cases"][0]["events"][0].update(fault={"mode": "melt"}))
        self.catch(E, "svc.loan", lambda m: m["cases"][0]["expectedTerminalState"].update({"svc.loan": "Done"}))
        self.catch(E, "st.loan", lambda m: m["cases"][0]["expectedTerminalState"].update({"st.loan": "Completed"}))
        self.catch(E, "initialMachines", lambda m: m["cases"][0]["initialMachines"].update({"st.loan": "gone"}))
        self.catch(E, "expectedOutcomes[0].outcome", lambda m: m["cases"][0].update(expectedOutcomes=[{"ruleId": "R1", "outcome": "Maybe"}]))
        self.catch(E, "expectedBy", lambda m: m["cases"][0].update(expectedBy="oracle"))
        self.catch(E, "$.space.buildings[0].trust", lambda m: m["space"]["buildings"][0].update(trust="nowhere"))
        self.catch(E, "$.space.rooms[0].tier", lambda m: m["space"]["rooms"][0].update(tier="T9"))
        self.catch(E, "$.space.rooms[0].layer", lambda m: m["space"]["rooms"][0].update(layer="basement"))
        self.catch(E, "$.interventions[0].type", lambda m: m["interventions"][0].update(type="melt_module"))
        self.catch(E, "expect.outcomeClass", lambda m: m["interventions"][0]["expect"].update(outcomeClass="DRIFTED"))
        self.catch(E, "expect.status", lambda m: m["interventions"][0]["expect"].update(status="MAYBE"))
        self.catch(E, "element.kind", lambda m: m["interventions"][1]["element"].update(kind="Teapot"))
        self.catch(E, "eventHandlers", lambda m: m["interventions"][1]["element"]["eventHandlers"].append({"on": "x", "do": "explode"}))
        self.catch(E, "rewrite[0].disposition", lambda m: m["interventions"][1]["rewrite"][0].update(disposition="vanish"))
        self.catch(E, "$.policies[0].kind", lambda m: m["policies"][0].update(kind="whim"))
        self.catch(E, "$.policies[0].by", lambda m: m["policies"][0].update(by="bot"))
        self.catch(E, "$.policies[0].level", lambda m: m["policies"][0].update(level="L9"))
        self.catch(E, "$.meta.mode", lambda m: m["meta"].update(mode="maybe"))
        self.catch(E, "$.meta.languages", lambda m: m["meta"].update(languages=["xx-XX"]))
        self.catch(E, "$.evidences[0].sourceKind", lambda m: m["evidences"][0].update(sourceKind="telepathy"))

    def test_element_kind_cannot_be_a_node(self):
        self.catch(am.E_ENUM, "$.nodes[0].kind", lambda m: m["nodes"][0].update(kind="Element"))

    def test_reference_violations_are_caught(self):
        R = am.E_REF
        self.catch(R, "$.nodes[0].roomId", lambda m: m["nodes"][0].update(roomId="rm.nowhere"))
        self.catch(R, "$.nodes[0].evidenceIds", lambda m: m["nodes"][0]["evidenceIds"].append("EVD-9"))
        self.catch(R, "$.edges[0].targetId", lambda m: m["edges"][0].update(targetId="svc.nowhere"))
        self.catch(R, "$.edges[0].evidenceIds", lambda m: m["edges"][0]["evidenceIds"].append("EVD-9"))
        self.catch(R, "$.cases[0].events[0].to", lambda m: m["cases"][0]["events"][0].update(to="svc.nowhere"))
        self.catch(R, "$.cases[0].events[0].edge", lambda m: m["cases"][0]["events"][0].update(edge="a:calls:b"))
        self.catch(R, "$.cases[0].requirementIds", lambda m: m["cases"][0]["requirementIds"].append("RQ-9"))
        self.catch(R, "$.cases[0].linkedTestIds", lambda m: m["cases"][0]["linkedTestIds"].append("TS-9"))
        self.catch(R, "$.cases[0].expectedTerminalState", lambda m: m["cases"][0]["expectedTerminalState"].update({"svc.ghost": "Idle"}))
        self.catch(R, "$.cases[0].initialMachines", lambda m: m["cases"][0]["initialMachines"].update({"svc.loan": "open"}))
        self.catch(R, "$.requirements[0].edgeIds", lambda m: m["requirements"][0]["edgeIds"].append("a:calls:b"))
        self.catch(R, "$.requirements[0].tstIds", lambda m: m["requirements"][0]["tstIds"].append("TS-9"))
        self.catch(R, "$.requirements[0].tbdIds", lambda m: m["requirements"][0]["tbdIds"].append("TBD-9"))
        self.catch(R, "$.tbds[0].evidenceIds", lambda m: m["tbds"][0]["evidenceIds"].append("EVD-9"))
        self.catch(R, "$.policies[0].src", lambda m: m["policies"][0].update(src="RQ-9"))
        self.catch(R, "$.policies[0].scope", lambda m: m["policies"][0].update(scope="node:svc.ghost"))
        self.catch(R, "$.policies[0].scope", lambda m: m["policies"][0].update(scope="req:RQ-9"))
        self.catch(am.E_ENUM, "$.policies[0].scope", lambda m: m["policies"][0].update(scope="kind:Gizmo"))
        self.catch(am.E_FIELD, "$.policies[0].scope", lambda m: m["policies"][0].update(scope="everything"))
        self.catch(R, "$.interventions[0].targetNodeId", lambda m: m["interventions"][0].update(targetNodeId="svc.nowhere"))
        self.catch(R, "$.interventions[0].appliesTo", lambda m: m["interventions"][0]["appliesTo"].append("CS-9"))
        self.catch(R, "$.interventions[2].transitionId", lambda m: m["interventions"][2].update(transitionId="tr.nowhere"))
        self.catch(R, "$.interventions[2].machineId", lambda m: m["interventions"][2].update(machineId="st.ghost"))
        self.catch(R, "$.interventions[1].rewrite[0].replaceEdgeId", lambda m: m["interventions"][1]["rewrite"][0].update(replaceEdgeId="a:calls:b"))
        self.catch(R, "expect.terminal", lambda m: m["interventions"][0]["expect"]["terminal"].update({"svc.ghost": "Failed"}))
        self.catch(R, "$.space.floors[0].buildingId", lambda m: m["space"]["floors"][0].update(buildingId="zone.nowhere"))
        self.catch(R, "$.space.rooms[0].floorId", lambda m: m["space"]["rooms"][0].update(floorId="lvl.nowhere"))
        self.catch(R, "$.machines[0].evidenceIds", lambda m: m["machines"][0]["evidenceIds"].append("EVD-9"))

    def test_inserted_element_ids_are_valid_terminal_keys(self):
        self.clean(lambda m: m["interventions"][1]["expect"].update(terminal={"el.cache": "Completed"}))

    def test_shape_and_grammar_violations_are_caught(self):
        self.catch(am.E_SHAPE, "$.extra", lambda m: m.update(extra=[]))
        self.catch(am.E_SHAPE, "$.tbds", lambda m: m.pop("tbds"))
        self.catch(am.E_SHAPE, "$.schema", lambda m: m.update(schema="gemarc-atlas/2"))
        self.catch(am.E_SHAPE, "$.space", lambda m: m.update(space={"floors": [], "buildings": [], "rooms": []}))
        self.catch(am.E_FIELD, "$.nodes[0].lineRange", lambda m: m["nodes"][0].update(lineRange=[1, 2]))
        self.catch(am.E_FIELD, "$.nodes[0].sub", lambda m: m["nodes"][0].pop("sub"))
        self.catch(am.E_FIELD, "$.requirements[0].tbd", lambda m: m["requirements"][0].update(tbd="TBD-1"))
        self.catch(am.E_FIELD, "$.evidences[0].lineRange", lambda m: m["evidences"][0].update(lineRange=[5, 2]))
        self.catch(am.E_FIELD, "$.edges[0].cardinality", lambda m: m["edges"][0].update(cardinality="lots"))
        self.catch(am.E_FIELD, "$.cases[0].policy.timeoutMs", lambda m: m["cases"][0]["policy"].update(timeoutMs=0))
        self.catch(am.E_FIELD, "latencyMs", lambda m: m["cases"][0]["events"][1].update(latencyMs=[-1]))
        self.catch(am.E_FIELD, "$.policies[0].chapter", lambda m: m["policies"][0].update(chapter=40))
        self.catch(am.E_ID, "$.nodes[0].id", lambda m: m["nodes"][0].update(id="svc:loan"))
        self.catch(am.E_ID, "$.nodes[0].id", lambda m: m["nodes"][0].update(id="a" * 125))
        self.catch(am.E_ID, "$.meta.projectId", lambda m: m["meta"].update(projectId="bad id"))
        self.catch(am.E_DUP, "$.nodes[1].id", lambda m: m["nodes"][1].update(id="svc.loan"))

    def test_ids_are_unique_across_collections(self):
        self.catch(am.E_DUP, "$.cases[0].caseId", lambda m: m["cases"][0].update(caseId="svc.loan"))
        self.catch(am.E_DUP, "$.requirements[0].id", lambda m: m["requirements"][0].update(id="EVD-1"))
        self.catch(am.E_DUP, "$.nodes[0].id", lambda m: m["space"]["rooms"][0].update(id="svc.loan"))  # reported at the later claimant
        self.catch(am.E_DUP, "$.tbds[0].id", lambda m: m["tests"][0].update(id="TBD-1"))

    def test_a_port_id_cannot_be_reused_by_an_authored_id(self):
        self.catch(am.E_DUP, "$.ports", lambda m: m["cases"][0].update(caseId="svc.loan:in"))

    def test_claim_rules(self):
        self.catch(am.E_CLAIM, "$.nodes[3].verification", lambda m: m["nodes"][3].update(verification="Implemented"))
        self.catch(am.E_CLAIM, "$.edges[0].verification", lambda m: m["edges"][0].update(verification="Implemented"))
        self.clean(lambda m: m["nodes"][0].update(verification="Designed"))  # claiming less than the evidence shows is allowed

    def test_machine_checks(self):
        def dup_transition(model):
            model["machines"][0]["transitions"][1]["id"] = model["machines"][0]["transitions"][0]["id"]
        self.catch(am.E_MACHINE, "$.machines[0].transitions", dup_transition)

    def test_intervention_parameter_checks(self):
        self.catch(am.E_FIELD, "$.interventions[0].targetNodeId", lambda m: m["interventions"][0].pop("targetNodeId"))
        self.catch(am.E_FIELD, "$.interventions[0].timeoutMs", lambda m: m["interventions"][0].update(timeoutMs=5))
        self.catch(am.E_FIELD, "$.interventions[0].expect", lambda m: m["interventions"][0]["expect"].update(bogus=1))
        self.catch(am.E_FIELD, "$.interventions[0].bogus", lambda m: m["interventions"][0].update(bogus=1))
        self.catch(am.E_DUP, "$.interventions[1].element.elementId", lambda m: m["interventions"][1]["element"].update(elementId="svc.stock"))
        self.catch(am.E_ID, "$.interventions[1].element.elementId", lambda m: m["interventions"][1]["element"].update(elementId="el:cache"))
        self.catch(am.E_FIELD, "$.interventions[1].rewrite", lambda m: m["interventions"][1].update(rewrite="none"))
        self.catch(am.E_FIELD, "$.interventions[2].machineId", lambda m: m["interventions"][2].pop("machineId"))
        self.clean(lambda m: m["interventions"][2].update(guard={"param": "fee", "op": "eq", "value": "0"}))
        self.catch(am.E_FIELD, "$.interventions[2].guard", lambda m: m["interventions"][2].update(guard="always"))
        self.catch(am.E_ENUM, "parts[0]", lambda m: m["interventions"][3]["parts"].insert(0, {"type": "melt"}))

    def test_delay_message_needs_a_target(self):
        def delay(model):
            model["interventions"].append(am.build_intervention("IV-LATE", "delay_message", "Slow", ["CS-BORROW-OK"], {"outcomeClass": "CONVERGED"}, addLatencyMs=100))

        def delay_ok(model):
            model["interventions"].append(am.build_intervention("IV-LATE", "delay_message", "Slow", ["CS-BORROW-OK"], {"outcomeClass": "CONVERGED"}, addLatencyMs=100, targetRuleId="R-CHECK"))

        self.catch(am.E_FIELD, "$.interventions[4]", delay)
        self.clean(delay_ok)

    def test_checker_never_raises_on_garbage(self):
        for garbage in ({}, {"schema": 1}, {"nodes": "x"}, {"space": []}, []):
            self.assertTrue(am.contract_issues(garbage))
        broken = lending_model()
        broken["nodes"][0] = "not a record"
        broken["edges"][1] = None
        broken["cases"][0]["events"][0] = 5
        self.assertTrue(am.contract_issues(broken))
        with self.assertRaises(am.AtlasModelError):
            am.check_contract(broken)


class CheckerRobustnessTests(unittest.TestCase):
    JUNK = [None, True, False, 0, -1, 3.5, "", "x", "svc.loan", [], [None], [[1]], {}, {"a": [1]}, ["a", "a"], "a\nb", 10 ** 30]

    @staticmethod
    def paths(node, prefix=()):
        yield prefix
        if isinstance(node, dict):
            for key, value in node.items():
                yield from CheckerRobustnessTests.paths(value, prefix + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from CheckerRobustnessTests.paths(value, prefix + (index,))

    def test_every_single_leaf_replacement_is_handled_without_a_crash(self):
        base = lending_model()
        all_paths = [p for p in self.paths(base) if p]
        self.assertGreater(len(all_paths), 500)
        crashes = []
        for path in all_paths:
            for junk in self.JUNK:
                model = copy.deepcopy(base)
                holder = model
                for step in path[:-1]:
                    holder = holder[step]
                holder[path[-1]] = copy.deepcopy(junk)
                for issue in am.contract_issues(model):
                    if issue["message"].startswith("internal checker error"):
                        crashes.append((path, junk, issue["message"]))
        self.assertEqual(crashes[:5], [])

    def test_deleting_any_key_is_handled_without_a_crash(self):
        base = lending_model()
        for path in [p for p in self.paths(base) if p and isinstance(p[-1], str)]:
            model = copy.deepcopy(base)
            holder = model
            for step in path[:-1]:
                holder = holder[step]
            del holder[path[-1]]
            messages = [i["message"] for i in am.contract_issues(model)]
            self.assertFalse([m for m in messages if m.startswith("internal checker error")], (path, messages[:2]))

    def test_non_dict_models_report_a_shape_issue(self):
        for garbage in (None, 5, "model", [], ()):
            self.assertEqual({i["code"] for i in am.contract_issues(garbage)}, {am.E_SHAPE})


class CoreHygieneTests(unittest.TestCase):
    def test_atlas_contract_modules_import_only_the_standard_library(self):
        for name in ("model.py", "vocab.py"):
            tree = ast.parse((REPO / "src" / "gemarc_mcp" / "atlas" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [] if node.level else [(node.module or "").split(".")[0]]
                else:
                    continue
                for module in modules:
                    self.assertTrue(module in sys.stdlib_module_names or module == "__future__", f"{name} imports {module}")

    def test_contract_files_are_utf8_lf_without_bom(self):
        for path in (PROFILE_PATH, REPO / "src" / "gemarc_mcp" / "atlas" / "model.py", REPO / "src" / "gemarc_mcp" / "atlas" / "vocab.py"):
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, path.name)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertTrue(raw.endswith(b"\n"), path.name)
            raw.decode("utf-8")


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        cls.profile = vocab.default_profile()

    def test_profile_is_well_formed(self):
        self.assertEqual(vocab.profile_issues(self.data), [])
        self.assertEqual(self.profile.id, "software")
        self.assertEqual(self.profile.locales, ("zh-CN", "ja-JP", "en-US"))

    def test_three_locales_are_complete_for_every_labelled_group(self):
        for group in vocab.LABEL_GROUPS:
            self.assertTrue(self.data[group], group)
            for item in self.data[group]:
                self.assertEqual(set(item["label"]), set(vocab.LOCALES), f"{group}.{item['id']}")
                for locale in vocab.LOCALES:
                    self.assertTrue(item["label"][locale].strip(), f"{group}.{item['id']}.{locale}")

    def test_cjk_labels_are_really_localised(self):
        cjk = re.compile(r"[぀-ヿ㐀-鿿]")
        ascii_only_ok = {"Table"}  # none expected; keep the list explicit
        for group in vocab.LABEL_GROUPS:
            for item in self.data[group]:
                for locale in ("zh-CN", "ja-JP"):
                    text = item["label"][locale]
                    self.assertTrue(cjk.search(text) or item["id"] in ascii_only_ok, f"{group}.{item['id']}.{locale}={text!r}")

    def test_validator_detects_a_missing_locale_or_bad_flag(self):
        broken = copy.deepcopy(self.data)
        del broken["kinds"][0]["label"]["ja-JP"]
        self.assertTrue(any("ja-JP" in i for i in vocab.profile_issues(broken)))
        broken = copy.deepcopy(self.data)
        broken["relations"][0]["static"] = broken["relations"][0]["runtime"]
        self.assertTrue(any("complement" in i for i in vocab.profile_issues(broken)))
        broken = copy.deepcopy(self.data)
        broken["kinds"][1]["id"] = broken["kinds"][0]["id"]
        self.assertTrue(any("duplicate" in i for i in vocab.profile_issues(broken)))
        broken = copy.deepcopy(self.data)
        broken["verification"][0], broken["verification"][1] = broken["verification"][1], broken["verification"][0]
        self.assertTrue(vocab.profile_issues(broken))
        self.assertTrue(vocab.profile_issues([]))

    def test_unknown_profile_name_is_rejected(self):
        with self.assertRaises(vocab.VocabError):
            vocab.load_profile("../software")
        with self.assertRaises(vocab.VocabError):
            vocab.load_profile("missing")

    def test_labels_and_aliases(self):
        self.assertEqual(self.profile.label("kinds", "Module", "ja-JP"), "モジュール")
        self.assertEqual(self.profile.label("kinds", "Module", "xx-XX"), "Module")
        self.assertEqual(self.profile.label("kinds", "Nope", "ja-JP"), "Nope")
        self.assertEqual(self.profile.alias("caseCategories", "combined"), "combination")
        self.assertIsNone(self.profile.alias("caseCategories", "normal"))


class VocabSubsetTests(unittest.TestCase):
    """Atlas ids that also exist in the canonical vocabulary must be the canonical ids; additive ids are listed above."""

    @classmethod
    def setUpClass(cls):
        cls.p = vocab.default_profile()

    def assert_subset(self, group: str, canonical: list[str]) -> None:
        atlas = set(self.p.ids(group))
        additive = ADDITIVE.get(group, set())
        self.assertLessEqual(atlas, set(canonical) | additive, f"{group}: undeclared ids {sorted(atlas - set(canonical) - additive)}")
        self.assertEqual(atlas - set(canonical), additive, f"{group}: the additive list must be exact")
        self.assertFalse(additive & set(canonical), f"{group}: an additive id exists in the canonical vocabulary")
        folded = {c.lower().replace("-", "").replace("_", "") for c in canonical}
        for extra in additive:
            self.assertNotIn(extra.lower().replace("-", "").replace("_", ""), folded, f"{group}: {extra} is a respelling of a canonical id")

    def test_kinds(self):
        self.assert_subset("kinds", canonical_ids("entity-kinds", "kinds"))
        self.assertEqual(ADDITIVE["kinds"], {"API", "Storage", "Workflow", "Element"})

    def test_relations_use_canonical_camel_case_and_categories(self):
        self.assert_subset("relations", canonical_ids("edge-types", "types"))
        canonical = {t["id"]: t for t in json.loads((CANONICAL_VOCAB / "edge-types.json").read_text(encoding="utf-8"))["types"]}
        for item in self.data_group("relations"):
            if item["id"] in canonical:
                self.assertEqual(item["category"], canonical[item["id"]]["category"], item["id"])
            self.assertIsNone(re.search(r"_", item["id"]), item["id"])

    def data_group(self, group):
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))[group]

    def test_case_categories(self):
        self.assert_subset("caseCategories", canonical_ids("case-categories", "categories"))
        self.assertIn("combination", self.p.ids("caseCategories"))
        self.assertNotIn("combined", self.p.ids("caseCategories"))
        self.assertTrue(set(canonical_ids("case-categories", "categories")) <= set(self.p.ids("caseCategories")))

    def test_views_reuse_canonical_diagram_type_ids(self):
        self.assert_subset("views", canonical_ids("diagram-types", "types"))
        self.assertEqual(ADDITIVE["views"], set())
        self.assertIn("dependency-diagram", self.p.ids("views"))
        self.assertEqual(set(self.p.ids("views")), {"logical-view", "development-view", "dependency-diagram", "process-view", "data-view", "security-view"})

    def test_verification_equals_canonical_levels_in_rank_order(self):
        canonical = json.loads((CANONICAL_VOCAB / "evidence-levels.json").read_text(encoding="utf-8"))["canonical"]
        self.assertEqual([(i["id"], i["rank"]) for i in self.data_group("verification")], [(i["id"], i["rank"]) for i in canonical])
        self.assertEqual(dict(self.p.verification_rank), {i["id"]: i["rank"] for i in canonical})
        self.assertEqual(am.weakest(["Observed", "Implemented"]), "Observed")

    def test_shared_kind_ids_are_exact_not_respelled(self):
        canonical = set(canonical_ids("entity-kinds", "kinds"))
        shared = set(self.p.ids("kinds")) & canonical
        self.assertTrue({"Actor", "Module", "Service", "Component", "Interface", "Entity", "Table", "Event", "State", "Class"} <= shared)

    def test_runtime_and_static_flags(self):
        self.assertIn("owns", self.p.runtime_relations)  # Transition events bind through the owns edge
        self.assertFalse(self.p.runtime_relations & self.p.static_relations)
        self.assertEqual(self.p.runtime_relations | self.p.static_relations, set(self.p.ids("relations")))
        for relation in ("dependsOn", "implements", "references", "contains", "belongsTo"):
            self.assertIn(relation, self.p.static_relations)
        for relation in ("calls", "sends", "writes", "reads", "publishes", "subscribes", "triggers", "owns"):
            self.assertIn(relation, self.p.runtime_relations)

    def test_kind_flags(self):
        self.assertEqual(self.p.record_kinds, {"Event"})
        element = self.p.item("kinds", "Element")
        self.assertFalse(element["authorable"])
        for kind in self.p.ids("kinds"):
            item = self.p.item("kinds", kind)
            self.assertIn(item["shape"], vocab.SHAPES)
            self.assertIn(item["level"], self.p.ids("levels"))

    def test_every_relation_view_exists_and_every_view_has_relations(self):
        views = set(self.p.ids("views"))
        used = set()
        for relation in self.p.ids("relations"):
            used |= set(self.p.item("relations", relation)["views"])
        self.assertEqual(used, views)

    def test_engine_enumerations(self):
        self.assertEqual(set(self.p.ids("eventTypes")), {"Call", "Message", "Return", "Transition", "Failure", "Compensation", "Recovery", "Wait"})
        self.assertEqual(self.p.delivery_types, {"Call", "Message"})
        self.assertEqual(len(self.p.ids("interventionTypes")), 10)
        self.assertEqual(set(self.p.ids("outcomeClasses")), {"NO_DIFFERENCE", "CONVERGED", "DIVERGED"})
        self.assertEqual(len(self.p.ids("eventOutcomes")), 10)
        self.assertEqual(self.p.active_states, {"Running", "Waiting", "Completed", "Recovered", "Degraded"})
        self.assertEqual(self.p.enums["handlerActions"], ("forward", "respond", "drop", "fail"))
        self.assertEqual(len(self.p.ids("elementKinds")), 14)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z]+", k) for k in self.p.ids("elementKinds")))

    def test_every_source_kind_and_doc_extension_is_usable(self):
        self.assertEqual(set(self.p.ids("sourceKinds")), {"source-code", "document", "user-requirement"})
        self.assertIn(".md", self.p.doc_ext)
        for ext in self.p.doc_ext:
            self.assertEqual(am.evidence_kind(f"x/y{ext}"), "document", ext)

    def test_trust_and_tiers_are_generic_and_ordered(self):
        self.assertEqual(self.p.trust_order, ("external", "perimeter", "internal", "restricted"))
        self.assertEqual(self.p.ids("tiers"), ("T1", "T2", "T3", "T4", "T5"))
        self.assertEqual(self.p.default_trust, "internal")
        for trust in self.p.trust_order:
            self.assertTrue(self.p.has("domains", trust))
        self.assertTrue(self.p.has("domains", "business"))


def rich_model(mode: str = "asis") -> dict:
    """The worked example plus every event option, guard shape, intervention parameter and rewrite disposition."""
    m = lending_model(mode)
    events = m["cases"][0]["events"]
    events[0].update(when={"all": [{"node": "svc.stock", "not": ["Failed"]}, {"param": "fee", "op": "present"}]}, ack=True)
    events[1].update(guard={"any": [{"param": "fee", "op": "in", "value": ["a", "b"]}, {"not": {"param": "fee", "op": "absent"}}]},
                     errorCode="E_FEE", dedupKey="k1", group="g1", section=True)
    events[2].update(fault={"mode": "fail", "errorCode": "E_DOWN", "httpStatus": 503}, ruleId="R-RETURN")
    events[3].update(guard={"param": "locale", "op": "eq", "value": "$ctx.locale"})
    events[5].update(when={"node": "st.loan", "is": ["open", "overdue"]})
    events.append({"seq": 8, "type": "Wait", "from": "svc.loan", "to": "svc.loan", "ms": 50, "when": {"elementState": "Warm"}})
    events.append({"seq": 9, "type": "Compensation", "from": "svc.loan", "to": "svc.stock", "role": "recovery",
                   "params": {"reason": "late"}, "latencyMs": [5, 6], "edge": "e.stock-retry"})
    m["nodes"][0]["props"].update(dedup={"mode": "reject", "errorCode": "E_DUP", "httpStatus": 409, "key": "request-id"}, owner="svc.loan",
                                  store="tbl.loan", enums={"Status": ["open", "returned", "overdue"]})
    edges = {e["id"]: e for e in m["edges"]}
    stock_edge, write_edge = edges["svc.loan:calls:svc.stock"], edges["svc.loan:writes:tbl.loan"]
    gate = {"elementId": "el.gate", "kind": "Validator", "role": "rejects bad stock replies",
            "inputPorts": [{"id": "el.gate:in", "type": "Message"}], "outputPorts": [{"id": "el.gate:out", "type": "Any"}],
            "stateMachine": {"states": ["Open", "Shut"], "initial": "Open", "transitions": [{"from": "Open", "to": "Shut", "on": "receive"}]},
            "eventHandlers": [{"on": "receive", "do": "respond", "when": {"elementState": "Open"}}], "guardRules": [],
            "errorRules": [], "resourceLimits": {"maxEvents": 10}, "terminationConditions": ["maxEvents"], "latencyMs": 1}
    m["interventions"] += [
        am.build_intervention("IV-LATE", "delay_message", "One slow event", ["CS-BORROW-OK"], {"outcomeClass": "CONVERGED"},
                              addLatencyMs=300, targetEventId="CS-BORROW-OK#2", targetRuleId="R-CHECK", targetEdgeId=stock_edge["id"]),
        am.build_intervention("IV-RETRY", "change_retry", "Retry twice", ["CS-BORROW-OK"], {"outcomeClass": "CONVERGED"}, retryMax=2, retryBackoffMs=0),
        am.build_intervention("IV-MIX", "combined_intervention", "Gate, relaxed guard and a down store", ["CS-BORROW-OK"],
                              {"outcomeClass": "DIVERGED", "terminal": {"el.gate": "Shut", "tbl.loan": "Blocked", "st.loan": "overdue"},
                               "status": "OK"}, parts=[
                                  {"type": "insert_element", "element": gate,
                                   "rewrite": [am.build_rewrite_entry("el.gate", 0, stock_edge),
                                               am.build_rewrite_entry("el.gate", 1, write_edge, "keep")]},
                                  {"type": "change_guard", "machineId": "st.loan", "transitionId": "tr.pay-late",
                                   "guard": {"all": [{"param": "fee", "op": "present"}, {"param": "fee", "op": "neq", "value": "0"}]}},
                                  {"type": "combined_intervention", "parts": [{"type": "disable_module", "targetNodeId": "tbl.loan"},
                                                                               {"type": "disable_edge", "targetEdgeId": write_edge["id"]}]}]),
        am.build_intervention("IV-DRAFT", "insert_element", "A draft element with nothing connected yet", ["CS-BORROW-OK"],
                              {"status": "DRAFT"}, element={"elementId": "el.draft", "kind": "Proxy", "role": "to be designed"}, rewrite=[]),
        am.build_intervention("IV-OFF", "insert_element", "Switch the write path off through an element", ["CS-BORROW-OK"],
                              {"outcomeClass": "DIVERGED"}, element=dict(gate, elementId="el.off", inputPorts=[{"id": "el.off:in", "type": "Message"}],
                                                                           outputPorts=[{"id": "el.off:out", "type": "Message"}]),
                              rewrite=[am.build_rewrite_entry("el.off", 0, write_edge, "disable")]),
    ]
    return m


class RichModelTests(unittest.TestCase):
    """What the checker accepts and what it must reject once events, guards and combined parts carry real content."""

    def issues(self, fn):
        model = rich_model()
        fn(model)
        return am.contract_issues(model)

    def catch(self, code: str, path_part: str, fn) -> None:
        issues = self.issues(fn)
        self.assertTrue(any(i["code"] == code and path_part in i["path"] for i in issues), f"{code} at {path_part}: {issues[:4]}")

    def clean(self, fn) -> None:
        self.assertEqual(self.issues(fn), [])

    def iv(self, model, interventions_id):
        return next(i for i in model["interventions"] if i["interventionId"] == interventions_id)

    def test_the_rich_model_conforms_in_both_modes(self):
        for mode in ("asis", "tobe"):
            self.assertEqual(am.contract_issues(rich_model(mode)), [], mode)

    def test_hostile_a_an_element_inserted_by_a_part_is_a_valid_terminal_key(self):
        m = rich_model()
        self.assertIn("el.gate", self.iv(m, "IV-MIX")["expect"]["terminal"])
        self.assertEqual(am.contract_issues(m), [])
        self.catch(am.E_REF, "expect.terminal.el.ghost", lambda x: self.iv(x, "IV-MIX")["expect"]["terminal"].update({"el.ghost": "Shut"}))
        # an element of another intervention is not visible here
        self.catch(am.E_REF, "expect.terminal.el.off", lambda x: self.iv(x, "IV-MIX")["expect"]["terminal"].update({"el.off": "Shut"}))

    def test_hostile_j_every_part_gets_the_checks_of_a_top_level_intervention(self):
        def hostile(model):
            self.iv(model, "IV-MIX")["parts"] = [
                {"type": "disable_edge", "targetEdgeId": "no:such:edge"},
                {"type": "change_guard", "machineId": "st.ghost", "transitionId": "tr.x", "guard": None},
                {"type": "disable_edge"},
                {"type": "insert_element", "element": {"elementId": "el.bad", "kind": "Teapot"},
                 "rewrite": [{"replaceEdgeId": "no:such:edge", "disposition": "vanish", "chain": []}]},
                {"type": "delay_message", "addLatencyMs": -5, "targetRuleId": "R-CHECK"},
                {"type": "change_timeout", "timeoutMs": 0},
                {"type": "disable_module", "targetNodeId": "svc.ghost", "timeoutMs": 5},
            ]
            self.iv(model, "IV-MIX")["expect"] = {"outcomeClass": "DIVERGED"}
        issues = self.issues(hostile)
        seen = {(i["code"], i["path"].split("$.interventions[")[-1].split("]", 1)[1]) for i in issues if "parts[" in i["path"]}
        wanted = {
            (am.E_REF, ".parts[0].targetEdgeId"), (am.E_REF, ".parts[1].machineId"), (am.E_FIELD, ".parts[2].targetEdgeId"),
            (am.E_ENUM, ".parts[3].element.kind"), (am.E_REF, ".parts[3].rewrite[0].replaceEdgeId"), (am.E_FIELD, ".parts[4].addLatencyMs"),
            (am.E_FIELD, ".parts[5].timeoutMs"), (am.E_REF, ".parts[6].targetNodeId"), (am.E_FIELD, ".parts[6].timeoutMs"),
        }
        self.assertEqual(wanted - seen, set(), issues)

    def test_a_part_that_is_not_an_object_or_not_atomic_is_rejected(self):
        for bad in (5, None, "disable_module", {"type": "melt"}, {}, {"targetNodeId": "svc.stock"}):
            self.catch(am.E_ENUM, "parts[0]", lambda x, b=bad: self.iv(x, "IV-MIX").update(parts=[b]))

    def test_parts_must_be_a_non_empty_array_and_nesting_is_capped(self):
        for bad in ([], "x", None, {}):
            self.catch(am.E_FIELD, ".parts", lambda x, b=bad: self.iv(x, "IV-MIX").update(parts=b))
        deep = {"type": "disable_module", "targetNodeId": "svc.stock"}
        for _ in range(am.MAX_PART_DEPTH + 1):
            deep = {"type": "combined_intervention", "parts": [deep]}
        self.catch(am.E_FIELD, "parts[0]", lambda x: self.iv(x, "IV-MIX").update(parts=[deep]))
        shallow = {"type": "disable_module", "targetNodeId": "svc.stock"}
        for _ in range(am.MAX_PART_DEPTH - 2):
            shallow = {"type": "combined_intervention", "parts": [shallow]}
        self.clean(lambda x: self.iv(x, "IV-MIX").update(parts=[shallow], expect={"outcomeClass": "DIVERGED"}))

    def test_a_part_may_not_carry_record_fields_or_foreign_parameters(self):
        self.catch(am.E_FIELD, "parts[0].description", lambda x: self.iv(x, "IV-MIX")["parts"][0].update(description="x"))
        self.catch(am.E_FIELD, "parts[1].timeoutMs", lambda x: self.iv(x, "IV-MIX")["parts"][1].update(timeoutMs=5))

    def test_flatten_parts_is_depth_first_and_atomic_records_are_their_own_part(self):
        mix = self.iv(rich_model(), "IV-MIX")
        flat = am.flatten_parts(mix)
        self.assertEqual([p["type"] for p in flat], ["insert_element", "change_guard", "disable_module", "disable_edge"])
        stock = {"id": "IV-S", "type": "disable_module", "targetNodeId": "svc.stock"}
        self.assertEqual(am.flatten_parts(stock), [stock])
        for bad in ({"type": "combined_intervention"}, {"type": "combined_intervention", "parts": []},
                    {"type": "combined_intervention", "parts": [5]}, {"nope": 1}, 5):
            with self.assertRaises(am.AtlasModelError):
                am.flatten_parts(bad)

    # ---- targetEventId and targetRuleId ------------------------------------------------------------------

    def test_event_id_format_is_case_hash_seq(self):
        self.assertEqual(am.event_id("CS-1", 7), "CS-1#7")
        self.assertEqual(am.parse_event_id("CS-1#7"), ("CS-1", 7))
        self.assertEqual(am.parse_event_id("a:b.c#12"), ("a:b.c", 12))
        for bad in ("garbage", "CS-1#", "#7", "CS-1#0", "CS-1#07", "CS-1#-1", "CS-1#1#2", "CS 1#3", "CS-1#x", "CS-1#1.5", 7, None, "CS-1#" + "9" * 12):
            self.assertIsNone(am.parse_event_id(bad), bad)
        for args in (("CS 1", 1), ("CS-1", 0), ("CS-1", True), ("CS-1", "1"), (None, 1)):
            with self.assertRaises(am.AtlasModelError):
                am.event_id(*args)
        self.assertIsNone(am.ID_RE.fullmatch("CS#1"))

    def test_hostile_d_target_event_id_must_name_an_event_of_an_applied_case(self):
        late = lambda x: self.iv(x, "IV-LATE")
        self.catch(am.E_FIELD, ".targetEventId", lambda x: late(x).update(targetEventId="garbage"))
        self.catch(am.E_REF, ".targetEventId", lambda x: late(x).update(targetEventId="CS-NOPE#1"))
        self.catch(am.E_REF, ".targetEventId", lambda x: late(x).update(targetEventId="CS-BORROW-OK#10"))
        self.catch(am.E_REF, ".targetEventId", lambda x: late(x).update(targetEventId="CS-BORROW-OK#99"))
        self.clean(lambda x: late(x).update(targetEventId="CS-BORROW-OK#9"))
        # a second case that exists but is not applied: the delay would never fire
        def other_case(model):
            model["cases"].append(am.build_case("CS-OTHER", "normal", "Another", [("Call", "usr.member", "svc.loan")]))
            late(model)["targetEventId"] = "CS-OTHER#1"
        self.catch(am.E_REF, ".targetEventId", other_case)

    def test_hostile_d_target_rule_id_must_be_a_rule_of_an_applied_case(self):
        late = lambda x: self.iv(x, "IV-LATE")
        self.catch(am.E_REF, ".targetRuleId", lambda x: late(x).update(targetRuleId="nope"))
        self.catch(am.E_FIELD, ".targetRuleId", lambda x: late(x).update(targetRuleId=5))
        self.catch(am.E_FIELD, ".targetRuleId", lambda x: late(x).update(targetRuleId=""))

        def rule_of_unapplied_case(model):
            model["cases"].append(am.build_case("CS-OTHER", "normal", "Another", [("Call", "usr.member", "svc.loan", {"ruleId": "R-ELSEWHERE"})]))
            late(model)["targetRuleId"] = "R-ELSEWHERE"
        self.catch(am.E_REF, ".targetRuleId", rule_of_unapplied_case)

    def test_delay_message_in_a_part_needs_a_target_too(self):
        self.catch(am.E_FIELD, "parts[0]", lambda x: self.iv(x, "IV-MIX").update(parts=[{"type": "delay_message", "addLatencyMs": 5}]))

    # ---- numeric parameters ----------------------------------------------------------------------------

    def test_intervention_numbers_are_bounded_and_typed(self):
        retry = lambda x: self.iv(x, "IV-RETRY")
        for value in (-1, "x", 1.5, True, None, [1]):
            self.catch(am.E_FIELD, ".retryMax", lambda x, v=value: retry(x).update(retryMax=v))
        for value in (-1, "x", True):
            self.catch(am.E_FIELD, ".retryBackoffMs", lambda x, v=value: retry(x).update(retryBackoffMs=v))
        self.clean(lambda x: retry(x).update(retryMax=0, retryBackoffMs=250))
        for value in (0, -5, "x", False, 2.5):
            self.catch(am.E_FIELD, ".timeoutMs", lambda x, v=value: x["interventions"].append(
                am.build_intervention("IV-T", "change_timeout", "t", ["CS-BORROW-OK"], {"outcomeClass": "CONVERGED"}, timeoutMs=v)))
        for value in (-5, "x", True):
            self.catch(am.E_FIELD, ".addLatencyMs", lambda x, v=value: self.iv(x, "IV-LATE").update(addLatencyMs=v))
        self.clean(lambda x: self.iv(x, "IV-LATE").update(addLatencyMs=0))

    def test_applies_to_is_never_empty(self):
        for target in ("IV-LATE", "IV-MIX", "IV-STOCK-DOWN"):
            self.catch(am.E_FIELD, ".appliesTo", lambda x, t=target: self.iv(x, t).update(appliesTo=[]))

        def no_cases(model):
            model["cases"], model["interventions"] = [], [
                am.build_intervention("IV-X", "disable_module", "x", [], {"outcomeClass": "DIVERGED"}, targetNodeId="svc.stock")]
        self.catch(am.E_FIELD, ".appliesTo", no_cases)

    # ---- expect.terminal ---------------------------------------------------------------------------------

    def test_expected_terminal_states_are_checked_per_node_kind(self):
        terminal = lambda x: self.iv(x, "IV-MIX")["expect"]["terminal"]
        self.catch(am.E_ENUM, "expect.terminal.tbl.loan", lambda x: terminal(x).update({"tbl.loan": "Exploded"}))
        self.catch(am.E_ENUM, "expect.terminal.st.loan", lambda x: terminal(x).update({"st.loan": "Blocked"}))  # a machine state is required
        self.catch(am.E_ENUM, "expect.terminal.el.gate", lambda x: terminal(x).update({"el.gate": "Exploded"}))
        for value in (5, None, ["Shut"], {"a": 1}):
            self.catch(am.E_FIELD, "expect.terminal.tbl.loan", lambda x, v=value: terminal(x).update({"tbl.loan": v}))
        self.clean(lambda x: terminal(x).update({"el.gate": "Completed", "svc.stock": "Idle"}))  # an element may also end in a node state

    # ---- event options -----------------------------------------------------------------------------------

    def event(self, model, index=1):
        return model["cases"][0]["events"][index]

    def test_event_option_types_are_enforced(self):
        bad = {"ack": ["yes", 1, None], "section": ["x", 0], "group": [5, "", " "], "dedupKey": [3, ""], "ruleId": [5, "", None],
               "errorCode": [5, ""], "params": ["x", [], 5], "ms": [-1, "x", True, 1.5], "latencyMs": [-1, "x", [], [1, -2], True]}
        for key, values in bad.items():
            for value in values:
                self.catch(am.E_FIELD, f"events[1].{key}", lambda x, k=key, v=value: self.event(x).update({k: v}))
        self.catch(am.E_FIELD, "events[1].fault.httpStatus", lambda x: self.event(x).update(fault={"mode": "fail", "httpStatus": 99}))
        self.catch(am.E_FIELD, "events[1].fault.httpStatus", lambda x: self.event(x).update(fault={"mode": "fail", "httpStatus": "503"}))
        self.catch(am.E_FIELD, "events[1].fault.errorCode", lambda x: self.event(x).update(fault={"mode": "fail", "errorCode": 5}))
        self.catch(am.E_FIELD, "events[1].fault.colour", lambda x: self.event(x).update(fault={"mode": "fail", "colour": "red"}))
        self.catch(am.E_ENUM, "events[1].fault.mode", lambda x: self.event(x).update(fault=[]))
        self.clean(lambda x: self.event(x).update(ack=False, section=False, ms=0, params={}, group="b"))

    def test_event_guards_have_a_checked_shape(self):
        cases = [
            ({"op": "eq", "value": 1}, am.E_FIELD, "guard.param"), ({"param": "", "op": "present"}, am.E_FIELD, "guard.param"),
            ({"param": "f", "op": "bogus"}, am.E_ENUM, "guard.op"), ({"param": "f"}, am.E_ENUM, "guard.op"), ({"param": "f", "op": 5}, am.E_ENUM, "guard.op"),
            ({"param": "f", "op": "present", "value": 1}, am.E_FIELD, "guard.value"), ({"param": "f", "op": "absent", "value": ""}, am.E_FIELD, "guard.value"),
            ({"param": "f", "op": "eq"}, am.E_FIELD, "guard.value"), ({"param": "f", "op": "neq", "value": {"a": 1}}, am.E_FIELD, "guard.value"),
            ({"param": "f", "op": "in", "value": "plain"}, am.E_FIELD, "guard.value"), ({"param": "f", "op": "eq", "value": "$ctx."}, am.E_FIELD, "guard.value"),
            ({"param": "f", "op": "eq", "value": 1, "extra": 1}, am.E_FIELD, "guard"), ({"all": []}, am.E_FIELD, "guard.all"),
            ({"any": "x"}, am.E_FIELD, "guard.any"), ({"all": [{"param": "f", "op": "bogus"}]}, am.E_ENUM, "guard.all[0].op"),
            ({"all": [{"param": "f", "op": "present"}], "any": [{"param": "f", "op": "present"}]}, am.E_FIELD, "guard"),
            ({"not": 5}, am.E_FIELD, "guard.not"), ({"not": {}}, am.E_ENUM, "guard.not.op"), ([], am.E_FIELD, "guard"), ("g", am.E_FIELD, "guard"), (5, am.E_FIELD, "guard"),
        ]
        for guard, code, where in cases:
            self.catch(code, f"events[1].{where}", lambda x, g=guard: self.event(x).update(guard=g))
            self.catch(code, f"transitions[2].{where}", lambda x, g=guard: x["machines"][0]["transitions"][2].update(guard=g))
            self.catch(code, f"interventions[2].{where}", lambda x, g=guard: x["interventions"][2].update(guard=g))
        for guard in ({"param": "f", "op": "in", "value": ["a"]}, {"param": "f", "op": "in", "value": "$ctx.list"},
                      {"param": "f", "op": "eq", "value": 0}, {"param": "f", "op": "eq", "value": False}, {"not": {"all": [{"param": "f", "op": "absent"}]}}):
            self.clean(lambda x, g=guard: self.event(x).update(guard=g))

    def test_a_guard_nested_beyond_the_cap_is_a_shape_issue(self):
        guard = {"param": "f", "op": "present"}
        for _ in range(am.MAX_GUARD_DEPTH + 2):
            guard = {"not": guard}
        self.catch(am.E_SHAPE, "events[1].guard", lambda x: self.event(x).update(guard=guard))

    def test_event_when_has_a_checked_shape(self):
        cases = [
            ({"node": "svc.ghost", "is": ["Running"]}, am.E_REF, "when.node"), ({"node": "svc.stock", "is": ["Exploded"]}, am.E_ENUM, "when.is[0]"),
            ({"node": "svc.stock", "not": ["Exploded"]}, am.E_ENUM, "when.not[0]"), ({"node": "svc.stock", "is": []}, am.E_FIELD, "when.is"),
            ({"node": "svc.stock", "is": "Running"}, am.E_FIELD, "when.is"), ({"node": "svc.stock"}, am.E_FIELD, "when"),
            ({"node": "svc.stock", "is": ["Running"], "not": ["Idle"]}, am.E_FIELD, "when"), ({"node": "svc.stock", "is": ["Running"], "x": 1}, am.E_FIELD, "when"),
            ({"node": "st.loan", "is": ["Running"]}, am.E_ENUM, "when.is[0]"),  # a machine node takes machine states
            ({"node": ["a"], "is": ["Running"]}, am.E_REF, "when.node"), ({"all": []}, am.E_FIELD, "when.all"),
            ({"any": [{"node": "svc.ghost", "is": ["Idle"]}]}, am.E_REF, "when.any[0].node"), ({"not": []}, am.E_FIELD, "when.not"),
            ({"not": {"node": "svc.stock", "is": ["Exploded"]}}, am.E_ENUM, "when.not.is[0]"),
            ({"elementState": ""}, am.E_FIELD, "when"), ({"elementState": 5}, am.E_FIELD, "when"), ({"elementState": "Warm", "x": 1}, am.E_FIELD, "when"),
            ({}, am.E_FIELD, "when"), ({"colour": "red"}, am.E_FIELD, "when"), ([], am.E_FIELD, "when"), ("w", am.E_FIELD, "when"),
            ({"param": "f", "op": "bogus"}, am.E_ENUM, "when.op"),
        ]
        for when, code, where in cases:
            self.catch(code, f"events[1].{where}", lambda x, w=when: self.event(x).update(when=w))
        for when in ({"node": "st.loan", "not": ["open"]}, {"any": [{"elementState": "Cold"}, {"param": "f", "op": "present"}]},
                     {"not": {"node": "svc.stock", "is": ["Idle", "Failed"]}}):
            self.clean(lambda x, w=when: self.event(x).update(when=w))

    # ---- elements and rewrites ---------------------------------------------------------------------------

    def test_keep_and_disable_entries_carry_no_chain_and_a_draft_element_has_no_rewrite(self):
        m = rich_model()
        keep = self.iv(m, "IV-MIX")["parts"][0]["rewrite"][1]
        self.assertEqual((keep["disposition"], keep["chain"]), ("keep", []))
        self.assertEqual(self.iv(m, "IV-OFF")["rewrite"][0]["chain"], [])
        self.assertEqual(self.iv(m, "IV-DRAFT")["rewrite"], [])
        self.assertEqual(am.contract_issues(m), [])
        # the chain of a keep entry must stay empty: a chain on keep is a derivation mismatch, not an alternative form
        edge = next(e for e in m["edges"] if e["id"] == "svc.loan:writes:tbl.loan")
        self.catch(am.E_DERIVED, "rewrite[1]", lambda x: self.iv(x, "IV-MIX")["parts"][0]["rewrite"][1].update(
            chain=am.derive_rewrite_chain("el.gate", 1, edge)))

    def test_element_ports_are_fixed_and_typed(self):
        gate = lambda x: self.iv(x, "IV-MIX")["parts"][0]["element"]
        self.catch(am.E_DERIVED, "element.inputPorts[0]", lambda x: gate(x).update(inputPorts=[{"id": "p.gate.in", "type": "Message"}]))
        self.catch(am.E_DERIVED, "element.outputPorts[0]", lambda x: gate(x).update(outputPorts=[{"id": "el.gate:in", "type": "Message"}]))
        self.catch(am.E_ENUM, "element.inputPorts[0]", lambda x: gate(x).update(inputPorts=[{"id": "el.gate:in", "type": "Blob"}]))
        self.catch(am.E_ENUM, "element.inputPorts[0]", lambda x: gate(x).update(inputPorts=[{"id": "el.gate:in", "type": "Any", "extra": 1}]))
        self.catch(am.E_FIELD, "element.outputPorts", lambda x: gate(x).update(outputPorts="out"))
        self.clean(lambda x: gate(x).update(inputPorts=[{"id": "el.gate:in", "type": "Record"}]))

    def test_two_parts_cannot_insert_the_same_element(self):
        def twice(model):
            parts = self.iv(model, "IV-MIX")["parts"]
            parts.append(copy.deepcopy(parts[0]))
        self.catch(am.E_DUP, "parts[3].element.elementId", twice)

    def test_a_chain_beyond_the_ordinal_limit_is_an_issue_not_a_crash(self):
        def many(model):
            rewrites = self.iv(model, "IV-CACHE")["rewrite"]
            rewrites.extend(copy.deepcopy(rewrites[0]) for _ in range(60))
        issues = self.issues(many)
        self.assertTrue(any(i["code"] == am.E_FIELD and "cannot derive the chain" in i["message"] for i in issues), issues[:3])
        self.assertFalse([i for i in issues if i["message"].startswith("internal checker error")])

    # ---- checker robustness on the rich model ----------------------------------------------------------

    def test_every_leaf_replacement_and_key_deletion_in_the_rich_model_is_handled_without_a_crash(self):
        base = rich_model()
        junk = CheckerRobustnessTests.JUNK[::2]
        crashes = []
        for path in [p for p in CheckerRobustnessTests.paths(base) if p]:
            variants = [("replace", j) for j in junk] + ([("delete", None)] if isinstance(path[-1], str) else [])
            for mode, value in variants:
                model = copy.deepcopy(base)
                holder = model
                for step in path[:-1]:
                    holder = holder[step]
                if mode == "delete":
                    del holder[path[-1]]
                else:
                    holder[path[-1]] = copy.deepcopy(value)
                crashes += [(path, mode, value, i["message"]) for i in am.contract_issues(model) if i["message"].startswith("internal checker error")]
        self.assertEqual(crashes[:5], [])


class NestingTests(unittest.TestCase):
    def deep(self, levels: int):
        value: object = 1
        for _ in range(levels):
            value = {"k": value}
        return value

    def test_deep_props_are_a_shape_issue_not_a_recursion_error(self):
        model = lending_model()
        model["nodes"][0]["props"]["blob"] = self.deep(5000)
        issues = am.contract_issues(model)
        self.assertEqual([i["code"] for i in issues], [am.E_SHAPE])
        for call in (am.canonical_dumps, am.canonical_bytes, am.model_sha, am.model_sha8):
            with self.assertRaises(am.AtlasModelError):
                call(model)

    def test_a_cyclic_value_is_rejected_the_same_way(self):
        model = lending_model()
        cycle: dict = {}
        cycle["self"] = cycle
        model["nodes"][0]["props"]["loop"] = cycle
        self.assertEqual([i["code"] for i in am.contract_issues(model)], [am.E_SHAPE])
        with self.assertRaises(am.AtlasModelError):
            am.canonical_dumps(model)
        wide: list = []
        wide.extend([wide, wide])
        model["nodes"][0]["props"]["loop"] = wide
        self.assertTrue(am.nesting_exceeds(model))

    def test_the_cap_is_exact_and_ordinary_models_are_far_below_it(self):
        self.assertFalse(am.nesting_exceeds(self.deep(am.MAX_NESTING - 1), am.MAX_NESTING))
        self.assertTrue(am.nesting_exceeds(self.deep(am.MAX_NESTING + 1), am.MAX_NESTING))
        self.assertFalse(am.nesting_exceeds(rich_model()))
        self.assertEqual(am.canonical_dumps(self.deep(20)), '{"k":' * 20 + "1" + "}" * 20)


class AuthoredIdAsciiTests(unittest.TestCase):
    LOOKALIKES = ("svc.а", "svc.ａ", "svc.٣", "svc.café", "svc.café", "ａbc", "svc.ü")

    def test_authored_ids_are_ascii_only(self):
        for text in self.LOOKALIKES:
            self.assertFalse(am.is_authored_id(text), ascii(text))
        self.assertTrue(am.is_authored_id("svc.cafe-2_x"))
        self.assertEqual(am.AUTHORED_ID_RE.flags & re.ASCII, re.ASCII)

    def test_authored_ascii_is_still_a_strict_subset_of_the_canonical_rule(self):
        for text in ("svc.a", "a" * 124, "x.y-z_1"):
            self.assertTrue(am.is_id(text) and am.is_authored_id(text))
        # the canonical ID_RE is verbatim (Unicode word characters after the first one); only node-like ids are stricter
        self.assertTrue(am.is_id("svc.а"))

    def test_the_checker_rejects_a_lookalike_node_id(self):
        for text in self.LOOKALIKES[:4]:
            model = lending_model()
            model["nodes"][0]["id"] = text
            self.assertTrue(any(i["code"] == am.E_ID and i["path"] == "$.nodes[0].id" for i in am.contract_issues(model)), ascii(text))

    def test_builders_reject_lookalikes_too(self):
        with self.assertRaises(am.AtlasModelError):
            am.build_node("svc.а", "Module", "x", "rm.a")
        with self.assertRaises(am.AtlasModelError):
            am.build_edge("svc.а", "calls", "svc.b", "x")
        with self.assertRaises(am.AtlasModelError):
            am.build_machine("svc.а", "open", [])


class HelperValidationTests(unittest.TestCase):
    def test_helpers_reject_wrong_types_with_the_contract_error_not_a_raw_exception(self):
        calls = [
            lambda: am.port_id(None, "out"), lambda: am.port_id("a:b", "in"), lambda: am.port_id("", "in"), lambda: am.port_id(5, "in"),
            lambda: am.transition_id(1, 2, 3), lambda: am.transition_id("m", "a\nb", "c"), lambda: am.transition_id("m", "a", "9z"),
            lambda: am.transition_id("m:x", "a", "b"), lambda: am.transition_id("m", None, "b"),
            lambda: am.evidence_kind(None), lambda: am.evidence_kind(5), lambda: am.derive_ports([{"id": "a"}]), lambda: am.derive_ports([5]),
            lambda: am.derive_ports([{"id": "a", "kind": "Module"}, "x"]),
            lambda: am.machine_states("a", [5]), lambda: am.machine_states("a", [{"from": "a"}]), lambda: am.machine_states("a", [("a",)]),
            lambda: am.machine_terminal(["a"], [None]),
            lambda: am.build_machine("m", "open", [{"from": "open"}]), lambda: am.build_machine("m", "9x", []), lambda: am.build_machine(None, "open", []),
            lambda: am.build_machine("m", "open", [5]), lambda: am.assign_transition_ids("m", [{"to": "a"}]),
            lambda: am.number_events([("Call", "a")]), lambda: am.number_events([5]), lambda: am.number_events([{"type": "Call"}]),
            lambda: am.number_events([("Call", "a", "b", 5)]), lambda: am.number_events([("Call", "a", "b", {}, 1)]),
            lambda: am.inherit_props([1, 2]), lambda: am.inherit_props({}, room=5), lambda: am.inherit_props({}, building="b"),
            lambda: am.edge_ref_index([1]), lambda: am.edge_ref_index([{"id": "x"}]),
            lambda: am.hash_id("d", 5), lambda: am.hash_id(None), lambda: am.edge_ports(None, "b"), lambda: am.edge_ports("a", "b:c"),
            lambda: am.build_node("n", "Module", "x", "rm.a", props=[1]), lambda: am.build_node(5, "Module", "x", "rm.a"),
        ]
        for index, call in enumerate(calls):
            with self.assertRaises(am.AtlasModelError, msg=f"call #{index}"):
                call()

    def test_the_error_codes_are_the_documented_ones(self):
        for call, code in ((lambda: am.port_id(None, "out"), am.E_BAD_PART), (lambda: am.transition_id("m", "a", "9z"), am.E_BAD_PART),
                           (lambda: am.evidence_kind(None), am.E_BAD_ARG), (lambda: am.derive_ports([{"id": "a"}]), am.E_BAD_ARG),
                           (lambda: am.build_node("svc:x", "Module", "x", "rm.a"), am.E_ID)):
            with self.assertRaises(am.AtlasModelError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, code)

    def test_valid_inputs_still_work(self):
        self.assertEqual(am.port_id("svc.a", "out"), "svc.a:out")
        self.assertEqual(am.transition_id("m", "a_to", "b"), "m.a_to_to_b")
        self.assertEqual(am.number_events([("Call", "a", "b"), {"type": "Return", "from": "b", "to": "a", "ruleId": "R"}, ["Wait", "a", "a", {"ms": 5}]])[2],
                         {"seq": 3, "type": "Wait", "from": "a", "to": "a", "ms": 5})
        self.assertEqual(am.machine_states("a", [("a", "b"), ["b", "c"], {"from": "c", "to": "a"}]), ["a", "b", "c"])

    def test_cap_verification_and_merge_policy_are_documented_as_non_validating(self):
        self.assertEqual(am.cap_verification("Foo", "Designed"), "Foo")  # unknown level: returned unchanged, the checker reports it
        self.assertEqual(am.cap_verification("Implemented", "Designed"), "Designed")
        self.assertEqual(am.merge_policy({"timeoutMs": "x"})["timeoutMs"], "x")
        self.assertIn("Does not validate", am.cap_verification.__doc__)

    def test_transition_id_switches_to_the_hash_between_120_and_121_characters(self):
        for total, plain in ((119, True), (120, True), (121, False), (122, False)):
            machine = "m" * (total - len(".a_to_b"))
            result = am.transition_id(machine, "a", "b")
            self.assertEqual(len(machine) + len(".a_to_b"), total)
            if plain:
                self.assertEqual(result, f"{machine}.a_to_b")
            else:
                self.assertEqual(result, am.hash_id("transition", machine, "a", "b"))
                self.assertEqual(len(result), 34)


class DerivationHelperTests(unittest.TestCase):
    def test_expect_cells(self):
        self.assertEqual(am.DEFAULT_EXPECTED_STATE, "Completed")
        self.assertEqual(am.expect_cell("svc.a"), ("svc.a", "Completed"))
        self.assertEqual(am.expect_cell(" svc.a = Failed "), ("svc.a", "Failed"))
        for bad in ("", "=Failed", "svc.a=", "svc.a=b=c", None, 5):
            with self.assertRaises(am.AtlasModelError):
                am.expect_cell(bad)

    def test_element_port_types_follow_the_replaced_edges(self):
        ports = [{"id": "a:out", "type": "Message"}, {"id": "b:in", "type": "Message"}, {"id": "e:out", "type": "Record"}, {"id": "c:in", "type": "Record"}]
        plain = {"sourcePortId": "a:out", "targetPortId": "b:in"}
        record = {"sourcePortId": "e:out", "targetPortId": "c:in"}
        machine_end = {"sourcePortId": "a:out", "targetPortId": None}
        self.assertEqual(am.element_port_types([plain], ports), ("Message", "Message"))
        self.assertEqual(am.element_port_types([plain, record], ports), ("Any", "Any"))
        self.assertEqual(am.element_port_types([record], ports), ("Record", "Record"))
        self.assertEqual(am.element_port_types([machine_end], ports), ("Message", "Any"))
        self.assertEqual(am.element_port_types([], ports), ("Any", "Any"))
        inputs, outputs = am.derive_element_ports("el.x", "Message", "Any")
        self.assertEqual((inputs, outputs), ([{"id": "el.x:in", "type": "Message"}], [{"id": "el.x:out", "type": "Any"}]))
        self.assertEqual(am.derive_element_ports("el.x"), ([{"id": "el.x:in", "type": "Any"}], [{"id": "el.x:out", "type": "Any"}]))
        for bad in (("el.x", "Blob", "Any"), ("el:x", "Any", "Any"), ("el.x", "Any", None)):
            with self.assertRaises(am.AtlasModelError):
                am.derive_element_ports(*bad)

    def test_an_inserted_element_lives_in_the_room_of_the_first_replaced_edge_source(self):
        m = lending_model()
        stock_edge = next(e for e in m["edges"] if e["id"] == "svc.loan:calls:svc.stock")
        member_edge = next(e for e in m["edges"] if e["id"] == "usr.member:calls:svc.loan")
        rewrites = [{"replaceEdgeId": "ghost"}, {"replaceEdgeId": member_edge["id"]}, {"replaceEdgeId": stock_edge["id"]}, 5]
        self.assertEqual(am.element_home_room(rewrites, m["edges"], m["nodes"]), "rm.vis")
        self.assertEqual(am.element_home_room(rewrites[2:], m["edges"], m["nodes"]), "rm.desk")
        self.assertIsNone(am.element_home_room([{"replaceEdgeId": "ghost"}, 5], m["edges"], m["nodes"]))
        self.assertIsNone(am.element_home_room([], m["edges"], m["nodes"]))

    def test_the_class_diagram_machine_comes_from_owner_and_owns_edges(self):
        m = rich_model()
        nodes = {n["id"]: n for n in m["nodes"]}
        interface = {"id": "svc.port", "kind": "Interface", "props": {"owner": "svc.loan"}}
        self.assertEqual(am.linked_machine_ids(interface, m["edges"], ["st.loan"]), ["st.loan"])
        self.assertEqual(am.linked_machine_ids(dict(interface, props={"owner": "svc.stock"}), m["edges"], ["st.loan"]), [])
        self.assertEqual(am.linked_machine_ids(dict(interface, props={}), m["edges"], ["st.loan"]), [])
        self.assertEqual(am.linked_machine_ids(dict(interface, props={"owner": 5}), m["edges"], ["st.loan"]), [])
        self.assertEqual(am.linked_machine_ids(nodes["svc.loan"], m["edges"], ["st.loan"]), ["st.loan"])  # owner=svc.loan was set in rich_model
        self.assertEqual(am.linked_machine_ids(interface, m["edges"], []), [])
        # only an owns edge links a machine: a runtime call from the owner to the machine node does not
        calls = {"id": "x:calls:st.loan", "sourceId": "svc.stock", "relation": "calls", "targetId": "st.loan"}
        self.assertEqual(am.linked_machine_ids(dict(interface, props={"owner": "svc.stock"}), [*m["edges"], calls], ["st.loan"]), [])
        owns = dict(calls, relation="owns", id="x:owns:st.loan")
        self.assertEqual(am.linked_machine_ids(dict(interface, props={"owner": "svc.stock"}), [calls, owns], ["st.loan"]), ["st.loan"])

    def test_the_machine_node_has_an_owner_in_the_worked_example(self):
        m = lending_model()
        owners = [e["sourceId"] for e in m["edges"] if e["relation"] == "owns" and e["targetId"] == "st.loan"]
        self.assertEqual(owners, ["svc.loan"])


class ProfileHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = vocab.default_profile()
        cls.data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    def test_the_shared_profile_cannot_be_edited_through_item_or_raw(self):
        before = json.dumps(self.p.raw, sort_keys=True)
        item = self.p.item("relations", "calls")
        attempts = [
            lambda: item.__setitem__("static", True), lambda: item["views"].append("x"), lambda: item["label"].update({"zh-CN": "x"}),
            lambda: self.p.raw["kinds"].append({}), lambda: self.p.raw["enums"]["guardOps"].append("x"), lambda: item.pop("static"),
            lambda: item.setdefault("new", 1), lambda: item.clear(), lambda: item["views"].sort(),
        ]
        for attempt in attempts:
            with self.assertRaises(TypeError):
                attempt()
        self.assertEqual(json.dumps(self.p.raw, sort_keys=True), before)
        self.assertFalse(vocab.default_profile().item("relations", "calls")["static"])
        self.assertFalse(am.build_edge("svc.a", "calls", "svc.b", "x")["static"])

    def test_a_deep_copy_is_a_private_editable_plain_structure(self):
        item = self.p.item("relations", "calls")
        mine = copy.deepcopy(item)
        mine["static"] = True
        mine["views"].append("x")
        self.assertIs(type(mine), dict)
        self.assertIs(type(mine["views"]), list)
        self.assertFalse(item["static"])
        self.assertNotIn("x", item["views"])
        self.assertIs(type(copy.copy(item)), dict)

    def test_frozen_items_still_behave_like_plain_json(self):
        item = self.p.item("interventionTypes", "change_guard")
        self.assertEqual(item["required"], ["machineId", "transitionId", "guard"])
        self.assertEqual(json.loads(json.dumps(item))["required"], ["machineId", "transitionId", "guard"])
        self.assertEqual(dict(item), {k: item[k] for k in item})
        self.assertEqual(json.loads(am.canonical_dumps({"item": item}))["item"]["id"], "change_guard")
        self.assertEqual(vocab.profile_issues(json.loads(json.dumps(self.p.raw))), [])
        import pickle
        self.assertEqual(pickle.loads(pickle.dumps(item)), item)

    def test_relations_carry_the_canonical_trust_boundary_flag(self):
        canonical = {t["id"]: t["trustBoundary"] for t in json.loads((CANONICAL_VOCAB / "edge-types.json").read_text(encoding="utf-8"))["types"]}
        for relation in self.p.ids("relations"):
            item = self.p.item("relations", relation)
            self.assertIsInstance(item["trustBoundary"], bool, relation)
            if relation in canonical:
                self.assertEqual(item["trustBoundary"], canonical[relation], relation)
            else:
                self.assertFalse(item["trustBoundary"], relation)
        self.assertEqual(self.p.trust_boundary_relations, {"sends", "receives", "publishes", "subscribes"})

    def test_the_profile_checker_requires_the_trust_boundary_flag(self):
        broken = copy.deepcopy(self.data)
        del broken["relations"][0]["trustBoundary"]
        self.assertTrue(any("trustBoundary" in issue for issue in vocab.profile_issues(broken)))
        broken = copy.deepcopy(self.data)
        broken["relations"][0]["trustBoundary"] = "yes"
        self.assertTrue(any("trustBoundary" in issue for issue in vocab.profile_issues(broken)))

    def test_zh_cn_labels_differ_from_the_canonical_ones_in_exactly_four_documented_places(self):
        canonical = {}
        for name, key in (("entity-kinds", "kinds"), ("edge-types", "types")):
            for item in json.loads((CANONICAL_VOCAB / f"{name}.json").read_text(encoding="utf-8"))[key]:
                canonical[(key, item["id"])] = item["label"]
        differing = set()
        for group, key in (("kinds", "kinds"), ("relations", "types")):
            for item in self.data[group]:
                shared = canonical.get((key, item["id"]))
                if shared is not None and shared != item["label"]["zh-CN"]:
                    differing.add((group, item["id"]))
        self.assertEqual(differing, {("kinds", "Table"), ("kinds", "State"), ("relations", "owns"), ("relations", "belongsTo")},
                         "a new difference needs a sentence in docs/atlas/spec/model-schema.md section 7.2")

    def test_design_material_formats_are_documents_and_ambiguous_ones_stay_source(self):
        for name in ("a.xlsx", "d/plan.drawio", "x/y.vsdx", "m.rtf", "n.tex", "s.xls", "o.ods", "p.odp"):
            self.assertEqual(am.evidence_kind(name), "document", name)
        for name in ("a.txt", "page.html", "page.htm", "data.csv", "requirements.txt", "CMakeLists.txt", "run.sh", "conf.json", "x.yaml"):
            self.assertEqual(am.evidence_kind(name), "source-code", name)

    def test_the_known_dedup_prop_names_its_key(self):
        row = self.p.known_props["dedup"]
        self.assertIn("{mode: reject|ignore, key?, errorCode?, httpStatus?}", row)
        for name in ("mode", "key?", "errorCode?", "httpStatus?"):
            self.assertIn(name, row)


class EncodingIssueTests(unittest.TestCase):
    """A lone UTF-16 surrogate (GEMARC_ATLAS_MODEL_012) and a non-finite number (GEMARC_ATLAS_MODEL_013) anywhere in a model.

    Both make ``canonical_bytes`` / ``model_sha`` and every UTF-8 file write fail, so the gate must name them.
    """

    LONE = "x" + chr(0xD800) + "y"  # written with chr(): this file itself must stay encodable

    def issues_of(self, mutate):
        model = rich_model()
        mutate(model)
        return model, am.contract_issues(model)

    def codes_at(self, issues, code: str) -> list[str]:
        return [i["path"] for i in issues if i["code"] == code]

    def test_the_two_codes_are_new_and_distinct(self):
        self.assertEqual(am.E_SURROGATE, "GEMARC_ATLAS_MODEL_012")
        self.assertEqual(am.E_NONFINITE, "GEMARC_ATLAS_MODEL_013")
        old = {am.E_BAD_PART, am.E_BAD_ARG, am.E_SHAPE, am.E_FIELD, am.E_ID, am.E_DUP, am.E_ENUM, am.E_REF, am.E_DERIVED, am.E_CLAIM, am.E_MACHINE}
        self.assertFalse({am.E_SURROGATE, am.E_NONFINITE} & old)

    def test_a_clean_model_has_neither_issue(self):
        self.assertEqual(am.contract_issues(rich_model()), [])
        self.assertEqual(am.contract_issues(lending_model()), [])

    def test_a_surrogate_in_every_free_text_field_class_is_reported_at_its_path(self):
        lone = self.LONE
        cases = {
            "$.meta.title": lambda m: m["meta"].__setitem__("title", lone),
            "$.space.buildings[0].name": lambda m: m["space"]["buildings"][0].__setitem__("name", lone),
            "$.space.floors[0].name": lambda m: m["space"]["floors"][0].__setitem__("name", lone),
            "$.space.rooms[0].name": lambda m: m["space"]["rooms"][0].__setitem__("name", lone),
            "$.nodes[0].name": lambda m: m["nodes"][0].__setitem__("name", lone),
            "$.nodes[0].sub": lambda m: m["nodes"][0].__setitem__("sub", lone),
            "$.nodes[0].props.tier": lambda m: m["nodes"][0]["props"].__setitem__("tier", lone),
            "$.edges[0].label": lambda m: m["edges"][0].__setitem__("label", lone),
            "$.evidences[0].filePath": lambda m: m["evidences"][0].__setitem__("filePath", lone),
            "$.evidences[0].anchor": lambda m: m["evidences"][0].__setitem__("anchor", lone),
            "$.evidences[0].topic": lambda m: m["evidences"][0].__setitem__("topic", lone),
            "$.machines[0].errorCode": lambda m: m["machines"][0].__setitem__("errorCode", lone),
            "$.cases[0].title": lambda m: m["cases"][0].__setitem__("title", lone),
            "$.cases[0].context.locale": lambda m: m["cases"][0]["context"].__setitem__("locale", lone),
            "$.interventions[0].description": lambda m: m["interventions"][0].__setitem__("description", lone),
            "$.requirements[0].title": lambda m: m["requirements"][0].__setitem__("title", lone),
            "$.tests[0].title": lambda m: m["tests"][0].__setitem__("title", lone),
            "$.tbds[0].decision": lambda m: m["tbds"][0].__setitem__("decision", lone),
            "$.policies[0].text": lambda m: m["policies"][0].__setitem__("text", lone),
        }
        for path, mutate in cases.items():
            model, issues = self.issues_of(mutate)
            self.assertIn(path, self.codes_at(issues, am.E_SURROGATE), f"{path}: {issues[:3]}")
            self.assertEqual(self.codes_at(issues, am.E_NONFINITE), [], path)
            for issue in issues:
                issue["path"].encode("utf-8")
                issue["message"].encode("utf-8")  # an issue must be printable and writable itself

    def test_the_surrogate_is_found_in_an_id_a_list_item_and_an_object_key(self):
        _, issues = self.issues_of(lambda m: m["nodes"][0].__setitem__("evidenceIds", ["EVD-1", self.LONE]))
        self.assertIn("$.nodes[0].evidenceIds[1]", self.codes_at(issues, am.E_SURROGATE))
        _, issues = self.issues_of(lambda m: m["nodes"][0].__setitem__("id", self.LONE))
        self.assertIn("$.nodes[0].id", self.codes_at(issues, am.E_SURROGATE))
        _, issues = self.issues_of(lambda m: m["nodes"][0]["props"].__setitem__("k" + chr(0xDC00), "v"))
        paths = self.codes_at(issues, am.E_SURROGATE)
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].startswith("$.nodes[0].props["), paths)
        paths[0].encode("utf-8")
        self.assertNotIn(chr(0xDC00), paths[0])

    def test_both_halves_of_a_pair_split_apart_are_lone_but_a_real_pair_is_fine(self):
        for text in (chr(0xDC00) + "a", "a" + chr(0xDBFF)):
            _, issues = self.issues_of(lambda m, t=text: m["nodes"][0].__setitem__("sub", t))
            self.assertIn("$.nodes[0].sub", self.codes_at(issues, am.E_SURROGATE))
        astral = "ok \U0001F600 中文"  # a real astral character is one code point, not a surrogate
        _, issues = self.issues_of(lambda m: m["nodes"][0].__setitem__("sub", astral))
        self.assertEqual(issues, [])
        am.model_sha(rich_model())

    def test_a_non_finite_number_inside_nested_options_and_params_is_reported(self):
        bad = {"inf": float("inf"), "-inf": float("-inf"), "nan": float("nan")}
        for name, value in bad.items():
            def put(m, v=value):
                m["cases"][0]["events"][1]["latencyMs"] = v
                m["cases"][0]["events"][5]["params"]["deep"] = {"list": [1, {"x": v}]}
                m["nodes"][0]["props"]["dedup"]["httpStatus"] = v
                m["interventions"][1]["element"]["resourceLimits"]["maxHops"] = v
                m["cases"][0]["policy"]["timeoutMs"] = v
            _, issues = self.issues_of(put)
            paths = self.codes_at(issues, am.E_NONFINITE)
            for expected in ("$.cases[0].events[1].latencyMs", "$.cases[0].events[5].params.deep.list[1].x",
                             "$.nodes[0].props.dedup.httpStatus", "$.interventions[1].element.resourceLimits.maxHops",
                             "$.cases[0].policy.timeoutMs"):
                self.assertIn(expected, paths, f"{name}: {paths}")
            self.assertEqual(self.codes_at(issues, am.E_SURROGATE), [], name)

    def test_a_finite_float_and_a_bool_are_not_reported(self):
        def put(m):
            m["cases"][0]["events"][1]["latencyMs"] = 12.5
            m["nodes"][0]["props"]["flag"] = True
        _, issues = self.issues_of(put)
        self.assertEqual([i for i in issues if i["code"] in (am.E_SURROGATE, am.E_NONFINITE)], [])

    def test_a_non_finite_number_in_a_top_level_field_and_a_tuple_is_reported(self):
        _, issues = self.issues_of(lambda m: m["space"]["floors"][0].__setitem__("level", float("nan")))
        self.assertIn("$.space.floors[0].level", self.codes_at(issues, am.E_NONFINITE))
        _, issues = self.issues_of(lambda m: m["nodes"][0]["props"].__setitem__("pair", (1, float("inf"))))
        self.assertIn("$.nodes[0].props.pair[1]", self.codes_at(issues, am.E_NONFINITE))

    def test_what_the_gate_accepts_can_be_hashed_and_what_it_names_could_not(self):
        model = rich_model()
        self.assertEqual(am.contract_issues(model), [])
        self.assertEqual(len(am.model_sha(model)), 64)
        for mutate in (lambda m: m["nodes"][0].__setitem__("sub", self.LONE),
                       lambda m: m["nodes"][0]["props"].__setitem__("x", float("nan"))):
            bad, issues = self.issues_of(mutate)
            self.assertTrue({am.E_SURROGATE, am.E_NONFINITE} & {i["code"] for i in issues})
            with self.assertRaises((am.AtlasModelError, UnicodeEncodeError)):
                am.model_sha(bad)

    def test_existing_codes_keep_their_meaning_next_to_the_new_ones(self):
        def put(m):
            m["nodes"][0]["name"] = self.LONE
            m["nodes"][1]["kind"] = "Teapot"
        _, issues = self.issues_of(put)
        codes = {i["code"] for i in issues}
        self.assertIn(am.E_SURROGATE, codes)
        self.assertIn(am.E_ENUM, codes)

    def test_the_report_is_capped_per_code_and_says_how_many_more(self):
        def put(m):
            m["nodes"][0]["props"]["many"] = [float("inf")] * 250
            m["nodes"][1]["props"]["many"] = [self.LONE] * 250
        _, issues = self.issues_of(put)
        for code in (am.E_NONFINITE, am.E_SURROGATE):
            listed = [i for i in issues if i["code"] == code]
            self.assertEqual(len(listed), 101, code)
            self.assertIn("150 more", listed[-1]["message"])
            self.assertEqual(listed[-1]["path"], "$")

    def test_a_hostile_value_inside_a_deep_but_legal_nest_never_raises(self):
        value: object = float("nan")
        for _ in range(am.MAX_NESTING - 12):
            value = [value]
        model = rich_model()
        model["nodes"][0]["props"]["deep"] = value
        issues = am.contract_issues(model)
        self.assertTrue(any(i["code"] == am.E_NONFINITE for i in issues), issues[:3])
        self.assertFalse(any("internal checker error" in i["message"] for i in issues))


if __name__ == "__main__":
    unittest.main()
