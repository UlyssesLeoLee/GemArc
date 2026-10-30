"""Tests of the element templates (``engine/elements.py`` and ``derive/templates/elements.json``).

Coverage of every element kind of the profile, template hygiene, validity under the engine and the contract, end-to-end
behaviour on the synthetic element fixture, and the wiring into the expander (``use=<kind>``) and the ``insert_element`` path.

Run only this file::

    PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_engine_elements*.py"
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas import expand
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import vocab
from gemarc_mcp.atlas.engine import elements as T

from . import synth

FIXTURE = json.loads((Path(__file__).resolve().parent / "engine_fixtures" / "elements.json").read_text(encoding="utf-8"))
PROFILE = vocab.default_profile()
ALLOWED_KEYS = {"role", "stateMachine", "eventHandlers", "guardRules", "errorRules", "resourceLimits", "terminationConditions",
                "latencyMs"}


def model_and_ix():
    model = copy.deepcopy(FIXTURE["model"])
    return model, E.index_model(model)


def signature(run: dict) -> list:
    """What a run did, without clock values and element-internal detail."""
    rows = [(e["eventId"], e["sourceNodeId"], e["targetNodeId"], e["eventType"], e["outcome"], e["nextState"], e["errorCode"],
             (e["cause"] or {}).get("code")) for e in run["trace"]]
    return [run["status"], rows, sorted(run["finalState"].items())]


class CoverageTests(unittest.TestCase):
    def test_all_fourteen_kinds_have_a_template(self):
        self.assertEqual(len(PROFILE.ids("elementKinds")), 14)
        self.assertEqual(set(T.kinds()), set(PROFILE.ids("elementKinds")))
        self.assertEqual(len(T.kinds()), 14)
        self.assertEqual(T.profile_kinds_covered(), [])

    def test_the_data_file_is_canonical_utf8_lf(self):
        raw = T.TEMPLATE_PATH.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
        self.assertEqual(json.loads(raw)["schema"], T.TEMPLATE_SCHEMA)

    def test_templates_carry_no_identity_and_no_ports(self):
        for kind in T.kinds():
            body = T.template_for(kind)
            self.assertLessEqual(set(body), ALLOWED_KEYS, kind)
            for key in ("elementId", "kind", "inputPorts", "outputPorts"):
                self.assertNotIn(key, body, kind)

    def test_template_for_returns_copies_and_none_for_unknown_kinds(self):
        a = T.template_for("Cache")
        a["role"] = "changed"
        a["stateMachine"]["states"].append("X")
        self.assertNotEqual(T.template_for("Cache")["role"], "changed")
        self.assertNotIn("X", T.template_for("Cache")["stateMachine"]["states"])
        self.assertIsNone(T.template_for("NoSuchKind"))
        self.assertIsNone(T.template_for(None))

    def test_every_template_is_internally_consistent(self):
        for kind in T.kinds():
            body = T.template_for(kind)
            machine = body["stateMachine"]
            states = set(machine["states"])
            self.assertIn(machine["initial"], states, kind)
            self.assertEqual(len(states), len(machine["states"]), kind)
            for t in machine["transitions"]:
                self.assertLessEqual({t["from"], t["to"]}, states, kind)
                self.assertTrue(t["on"], kind)
            self.assertTrue(body["eventHandlers"], kind)
            for h in body["eventHandlers"]:
                self.assertEqual(h["on"], "receive", kind)
                self.assertIn(h["do"], PROFILE.enums["handlerActions"], kind)
                if "setState" in h:
                    self.assertIn(h["setState"], states, kind)
                if "when" in h:
                    self.assertIn(h["when"]["elementState"], states, kind)
                if "retry" in h:
                    self.assertIsInstance(h["retry"], int)
            self.assertIsInstance(body["resourceLimits"]["maxEvents"], int)
            self.assertTrue(body["terminationConditions"], kind)
            self.assertTrue(body["role"].strip(), kind)
            self.assertLess(body.get("latencyMs", 0), 1000, kind)

    def test_kinds_differ_in_behaviour_not_only_in_name(self):
        shapes = {kind: json.dumps([T.template_for(kind)["eventHandlers"], T.template_for(kind)["stateMachine"]], sort_keys=True)
                  for kind in T.kinds()}
        self.assertEqual(len(set(shapes.values())), 14)
        actions = {kind: {h["do"] for h in T.template_for(kind)["eventHandlers"]} for kind in T.kinds()}
        self.assertEqual(set().union(*actions.values()), {"forward", "respond", "drop", "fail"})


class ValidityTests(unittest.TestCase):
    def test_every_built_element_passes_validate_element(self):
        _, ix = model_and_ix()
        for kind in T.kinds():
            el = T.build_element(kind, "qz1.el")
            self.assertEqual(E.validate_element(el, ix), [], kind)
            self.assertEqual((el["elementId"], el["kind"]), ("qz1.el", kind))
            self.assertEqual([p["id"] for p in el["inputPorts"] + el["outputPorts"]], ["qz1.el:in", "qz1.el:out"])

    def test_every_template_is_accepted_by_the_contract_in_a_real_intervention(self):
        model = synth.generate("small", 1)
        cases = [c["caseId"] for c in model["cases"]]
        edge = next(e for e in model["edges"] if not e["static"] and e["sourcePortId"] and e["targetPortId"]
                    and e["relation"] != "owns")
        in_type, out_type = am.element_port_types([edge], model["ports"])
        for kind in T.kinds():
            element = T.build_element(kind, "qz1.el", input_type=in_type, output_type=out_type)
            iv = am.build_intervention(f"IV-{kind}", "insert_element", "insert " + kind, cases[:1], {"outcomeClass": "CONVERGED"},
                                       element=element, rewrite=[am.build_rewrite_entry("qz1.el", 0, edge, "replace")])
            candidate = {**model, "interventions": [iv]}
            self.assertEqual(am.contract_issues(candidate), [], kind)
            self.assertEqual(E.compile_deltas(E.index_model(candidate), [iv]).status, "OK", kind)

    def test_build_element_rejects_bad_input_with_a_model_error(self):
        for args in (("NoSuchKind", "qz1.el"), ("Cache", "bad:id"), ("Cache", "café.x"), ("Cache", "")):
            with self.assertRaises(am.AtlasModelError):
                T.build_element(*args)
        with self.assertRaises(am.AtlasModelError):
            T.build_element("Cache", "qz1.el", input_type="Bogus")

    def test_overrides_win_but_never_change_identity(self):
        el = T.build_element("Cache", "qz1.el", overrides={"role": "mine", "latencyMs": 9, "kind": "Proxy", "elementId": "evil"})
        self.assertEqual((el["role"], el["latencyMs"], el["kind"], el["elementId"]), ("mine", 9, "Cache", "qz1.el"))

    def test_ports_follow_the_requested_types(self):
        el = T.build_element("Proxy", "qz1.el", input_type="Message", output_type="Record")
        self.assertEqual((el["inputPorts"][0]["type"], el["outputPorts"][0]["type"]), ("Message", "Record"))


class CompletionTests(unittest.TestCase):
    def iv(self, suffix: str) -> dict:
        return copy.deepcopy(next(i for i in FIXTURE["model"]["interventions"] if i["interventionId"].endswith(suffix)))

    def test_a_bare_element_and_a_bare_rewrite_become_the_authored_fixture_intervention(self):
        """``{elementId, kind}`` + ``{replaceEdgeId, disposition}`` completes to a body the engine runs like the hand-written one."""
        model, ix = model_and_ix()
        pairs = (("VAL", "Validator", "QM1-TWO"), ("CACHE", "Cache", "QM1-TWO"), ("FAIL", "FaultSource", "QM1-ONE"),
                 ("RETRY", "RetryController", "QM1-SLOW"))
        for suffix, kind, case_id in pairs:
            authored = self.iv(suffix)
            bare = {**authored, "element": {"elementId": authored["element"]["elementId"], "kind": kind},
                    "rewrite": [{"replaceEdgeId": r["replaceEdgeId"], "disposition": r["disposition"]} for r in authored["rewrite"]]}
            done = T.complete_deltas(model, [bare])[0]
            self.assertEqual(done["rewrite"], authored["rewrite"], suffix)
            self.assertEqual(done["element"]["inputPorts"], authored["element"]["inputPorts"], suffix)
            self.assertEqual(done["element"]["outputPorts"], authored["element"]["outputPorts"], suffix)
            case = ix.cases[case_id]
            a = E.simulate(model, case, [authored], {"index": ix})
            b = T.simulate_with_templates(model, case, [bare], {"index": ix})
            self.assertEqual(b["status"], "OK", suffix)
            self.assertEqual(signature(a), signature(b), suffix)

    def test_a_circuit_breaker_that_starts_open_drops_like_the_authored_drop(self):
        model, ix = model_and_ix()
        authored = self.iv("DROP")
        body = T.template_for("CircuitBreaker")
        body["stateMachine"]["initial"] = "OPEN"
        bare = {**authored, "element": {"elementId": authored["element"]["elementId"], "kind": "CircuitBreaker", **body}}
        case = ix.cases["QM1-ONE"]
        a = E.simulate(model, case, [authored], {"index": ix})
        b = T.simulate_with_templates(model, case, [bare], {"index": ix})
        self.assertEqual(signature(a), signature(b))
        self.assertEqual(b["elementStates"][authored["element"]["elementId"]], "OPEN")

    def test_a_closed_circuit_breaker_forwards_like_no_element(self):
        model, ix = model_and_ix()
        authored = self.iv("DROP")
        bare = {**authored, "element": {"elementId": authored["element"]["elementId"], "kind": "CircuitBreaker"}}
        case = ix.cases["QM1-ONE"]
        run = T.simulate_with_templates(model, case, [bare], {"index": ix})
        base = E.simulate(model, case, [], {"index": ix})
        el_id = authored["element"]["elementId"]
        self.assertEqual({k: v for k, v in run["finalState"].items() if k != el_id}, base["finalState"])
        self.assertEqual(run["finalState"][el_id], "Completed")  # the relayed Return completes it
        self.assertTrue(all(e["outcome"] == "OK" for e in run["trace"]))

    def test_the_cache_answers_only_the_second_request(self):
        model, ix = model_and_ix()
        authored = self.iv("CACHE")
        el_id = authored["element"]["elementId"]
        bare = {**authored, "element": {"elementId": el_id, "kind": "Cache"}}
        run = T.simulate_with_templates(model, ix.cases["QM1-TWO"], [bare], {"index": ix})
        causes = [(e["eventId"], e["cause"]["code"]) for e in run["trace"] if e["cause"]]
        self.assertIn(("QM1-TWO#4#h2", "element_answered"), causes)
        self.assertNotIn("QM1-TWO#2#h2", [c[0] for c in causes if c[1] == "element_answered"])
        self.assertEqual(run["elementStates"][el_id], "WARM")

    def test_each_remaining_kind_runs_and_the_fault_and_stub_behave(self):
        model, ix = model_and_ix()
        authored = self.iv("VAL")
        el_id = authored["element"]["elementId"]
        results, causes = {}, {}
        for kind in T.kinds():
            bare = {**authored, "element": {"elementId": el_id, "kind": kind}}
            run = T.simulate_with_templates(model, ix.cases["QM1-ONE"], [bare], {"index": ix})
            self.assertEqual(run["status"], "OK", kind)
            results[kind] = run["finalState"][el_id]
            causes[kind] = {e["cause"]["code"] for e in run["trace"] if e["cause"]}
        self.assertEqual(results["FaultSource"], "Failed")
        self.assertIn("element_answered", causes["ExternalServiceStub"])  # the stub answers the request itself
        self.assertNotIn("element_answered", causes["Proxy"])
        self.assertEqual(results["Proxy"], "Completed")  # forwarded, then completed by the relayed Return
        self.assertEqual(results["CircuitBreaker"], "Completed")

    def test_completion_turns_the_draft_fixtures_into_ok_or_invalid(self):
        model, ix = model_and_ix()
        draft, sparse = self.iv("DRAFT"), self.iv("SPARSE")
        self.assertEqual(E.compile_deltas(ix, [draft]).status, "DRAFT")
        self.assertEqual(E.compile_deltas(ix, [sparse]).status, "DRAFT")
        # the sparse element is connected: the template completes it
        completed = T.compile_with_templates(ix, [sparse])
        self.assertEqual((completed.status, completed.errors), ("OK", []))
        # The draft element has no rewrite: its body completes, but it is not connected to any formal edge yet. Spec 4.9
        # (`rewrite` may be empty: a draft element is not connected yet) makes that a DRAFT, not INVALID (deviation C8).
        still = T.compile_with_templates(ix, [draft])
        self.assertEqual(still.status, "DRAFT")
        self.assertEqual(still.errors, [f"element {draft['element']['elementId']} is not connected to any formal edge"])

    def test_explicit_keys_win_over_the_template(self):
        model, ix = model_and_ix()
        sparse = self.iv("SPARSE")
        sparse["element"].update({"latencyMs": 7, "role": "my role", "eventHandlers": [{"on": "receive", "do": "drop"}]})
        done = T.complete_deltas(model, [sparse])[0]["element"]
        self.assertEqual((done["latencyMs"], done["role"]), (7, "my role"))
        self.assertEqual(done["eventHandlers"], [{"on": "receive", "do": "drop"}])
        self.assertEqual(done["stateMachine"], T.template_for("Validator")["stateMachine"])

    def test_ports_are_derived_from_the_replaced_edge_types(self):
        """Message out-port to Record in-port: the element reads Message and writes Record (hand-derived from the fixture)."""
        model, ix = model_and_ix()
        authored = self.iv("ANY")
        bare = {**authored, "element": {"elementId": authored["element"]["elementId"], "kind": "StorageAdapter"},
                "rewrite": [{"replaceEdgeId": "mx5.b:publishes:yu5.n", "disposition": "replace"}]}
        el = T.complete_deltas(model, [bare])[0]["element"]
        self.assertEqual((el["inputPorts"][0]["type"], el["outputPorts"][0]["type"]), ("Message", "Record"))
        self.assertEqual(E.compile_deltas(ix, T.complete_deltas(model, [bare])).status, "OK")

    def test_keep_and_disable_rewrites_get_no_chain_and_no_ports_from_them(self):
        model, ix = model_and_ix()
        authored = self.iv("KEEP")
        bare = {**authored, "element": {"elementId": authored["element"]["elementId"], "kind": "Proxy"},
                "rewrite": [{"replaceEdgeId": "mx5.b:calls:mx5.c", "disposition": "keep"}]}
        done = T.complete_deltas(model, [bare])[0]
        self.assertEqual(done["rewrite"], [{"replaceEdgeId": "mx5.b:calls:mx5.c", "disposition": "keep", "chain": []}])
        self.assertEqual(done["element"]["inputPorts"][0]["type"], "Any")

    def test_combined_parts_are_completed_recursively(self):
        model, ix = model_and_ix()
        authored = self.iv("VAL")
        bare_part = {"type": "insert_element", "element": {"elementId": "qz1.deep", "kind": "Validator"},
                     "rewrite": [{"replaceEdgeId": "mx5.b:calls:mx5.c", "disposition": "replace"}]}
        combined = {"interventionId": "IX-C", "type": "combined_intervention", "description": "d", "appliesTo": ["QM1-TWO"],
                    "expect": {}, "note": "", "parts": [{"type": "combined_intervention", "parts": [bare_part]},
                                                         {"type": "disable_module", "targetNodeId": "mx5.d"}]}
        done = T.complete_deltas(model, [combined])[0]
        inner = done["parts"][0]["parts"][0]
        self.assertEqual(inner["element"]["inputPorts"], [{"id": "qz1.deep:in", "type": "Message"}])
        self.assertEqual(inner["rewrite"][0]["chain"][0]["targetId"], "qz1.deep")
        self.assertEqual(E.compile_deltas(ix, [done]).status, "OK")
        # the input is untouched
        self.assertNotIn("inputPorts", bare_part["element"])

    def test_unrecognisable_records_pass_through_unchanged(self):
        model, _ = model_and_ix()
        records = [{"type": "disable_module", "targetNodeId": "mx5.c"}, {"type": "insert_element", "element": "text"},
                   {"type": "insert_element", "element": {"elementId": "bad:id", "kind": "Cache"}, "rewrite": []},
                   {"type": "insert_element", "element": {"elementId": "qz1.x", "kind": "NoSuchKind"}, "rewrite": []}, "junk", None]
        self.assertEqual(T.complete_deltas(model, records), records)
        self.assertEqual(T.complete_deltas(model, None), [])

    def test_completion_never_modifies_the_model_or_the_input(self):
        model, ix = model_and_ix()
        before_model = json.dumps(model, sort_keys=True)
        deltas = [self.iv("SPARSE")]
        before = json.dumps(deltas, sort_keys=True)
        T.complete_deltas(model, deltas)
        T.compile_with_templates(ix, deltas)
        self.assertEqual(json.dumps(deltas, sort_keys=True), before)
        self.assertEqual(json.dumps(model, sort_keys=True), before_model)

    def test_deterministic(self):
        model, _ = model_and_ix()
        a = json.dumps(T.complete_deltas(model, [self.iv("SPARSE")]), sort_keys=True)
        b = json.dumps(T.complete_deltas(copy.deepcopy(model), [self.iv("SPARSE")]), sort_keys=True)
        self.assertEqual(a, b)


class ExpanderWiringTests(unittest.TestCase):
    """``use=<kind>`` in the authoring format: the expander takes ``template_for`` as its ``element_templates``."""

    SOURCE = (
        "#atlas 1\n"
        "## buildings: id | name | opts\nzone.a | Alpha | trust=internal\n"
        "## floors: id | building | name | level\nlvl.a | zone.a | Ground | 0\n"
        "## rooms: id | floor | name | opts\nrm.a | lvl.a | Hall | tier=T2\n"
        "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | s\nn.b | B | s\nn.c | C | s\n"
        "## edges: from | rel | to | label | opts\nn.a | calls | n.b | go\nn.b | calls | n.c | go on\n"
        "## cases: id | cat | title | opts\nC-1 | normal | one\n"
        "## events: type | from | to | opts\n@set case=C-1\nCall | n.a | n.b | ruleId=R-1\nCall | n.b | n.c | ruleId=R-2\n"
        "Return | n.c | n.b\n" "Return | n.b | n.a\n"
        "## interventions: id | type | description | opts\n"
        "IV-1 | insert_element | cache between b and c | applies=C-1 element=el.cache use={kind} rewrite=n.b:calls:n.c\n")
    META = {"projectId": "unit", "title": "Unit", "languages": ["en-US"], "profile": "software", "repos": ["main"]}

    def expand(self, kind: str):
        return expand.expand_sources({"t.atl": self.SOURCE.replace("{kind}", kind)}, self.META, element_templates=T.template_for)

    def test_every_kind_expands_to_a_complete_element_the_engine_accepts(self):
        for kind in T.kinds():
            ex = self.expand(kind)
            errors = [d for d in ex.diagnostics if d.get("severity") == "ERROR"]
            self.assertEqual(errors, [], kind)
            iv = ex.model["interventions"][0]
            el = iv["element"]
            self.assertEqual((el["elementId"], el["kind"]), ("el.cache", kind))
            self.assertEqual(el["role"], T.template_for(kind)["role"])
            ix = E.index_model(ex.model)
            self.assertEqual(E.compile_deltas(ix, [iv]).status, "OK", kind)
            run = E.simulate(ex.model, ex.model["cases"][0], [iv], {"index": ix})
            self.assertEqual(run["status"], "OK", kind)

    def test_without_templates_the_element_stays_a_draft(self):
        ex = expand.expand_sources({"t.atl": self.SOURCE.replace("{kind}", "Cache")}, self.META)
        ix = E.index_model(ex.model)
        self.assertEqual(E.compile_deltas(ix, [ex.model["interventions"][0]]).status, "DRAFT")

    def test_the_expanded_model_with_templates_passes_the_contract(self):
        for kind in ("Cache", "Validator", "HumanApproval"):
            self.assertEqual(am.contract_issues(self.expand(kind).model), [], kind)


if __name__ == "__main__":
    unittest.main()
