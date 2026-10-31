"""Wave 2 in the default full check (oracle-m5 items 2 and 3; debt-b4, item F).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_wave2_default.py"

``status.FULL_WAVE_SET`` is ``(1, 2)``: the status completion contract, ``check mode=full``, the MCP binding, the command line and the build
all run CS-001, CS-005, CS-008, PT-003, SM-004/006/007, IV-001/002 and OR-000..005 without being asked. The acceptance: a synthetic atlas
of every preset (tiny, small, reference, five seeds each) gives NO ERROR and NO WARN there (INFO lines only), both on the slices alone and
on a real project that ``put`` has sealed; the generator no longer writes a claim the engine contradicts (an intervention's ``status``),
no edge between a record port and a message port, every case category from six cases on, no abnormal case that asserts only Completed;
the reference-scale check stays inside the time budget of design 5.5. Synthetic id families only.
"""
from __future__ import annotations

import json
import os
import time
import unittest
from unittest import mock

from gemarc_mcp import atlas_tools as at
from gemarc_mcp.atlas import cli, encode, slicecheck
from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.gate import smoke
from gemarc_mcp.atlas.rules import or_, registry

from . import synth
from .test_oracle_support import OracleProject, consistent, desk_files, slice_texts
from .test_tools_b3_gates import Flow

PRESETS = (("tiny", (None,)), ("small", (1, 2, 3, 4, 5)), ("reference", (1, 2, 3, 4, 5)))
WAVE2 = {rule.id for rule in registry.rules((2,))}
BUDGET_SECONDS = 1.0   # design 5.5: check mode=full at atlas scale


def preset_models():
    for preset, seeds in PRESETS:
        for seed in seeds:
            yield preset, seed, (synth.generate("tiny") if preset == "tiny" else synth.generate(preset, seed))


def findings(result, *severities):
    return sorted((d.rule, d.severity, d.path) for d in result.diagnostics if not d.derived and d.severity in severities)


class DefaultSetTests(unittest.TestCase):
    def test_the_default_full_check_runs_wave_one_and_wave_two(self):
        self.assertEqual(st.FULL_WAVE_SET, (1, 2))
        self.assertEqual(WAVE2, {"CS-001", "CS-002", "CS-003", "CS-005", "CS-008", "PT-003", "SM-004", "SM-006", "SM-007", "IV-001", "IV-002",
                                 "OR-000", "OR-001", "OR-002", "OR-003", "OR-004", "OR-005"})

    def test_every_surface_reports_a_wave_two_finding_without_being_asked(self):
        # an intervention that declares a status the engine does not compute: IV-001 (ERROR, wave 2 only)
        row = "target=svc.stock applies"
        with OracleCase() as case:
            case.desk(("20-scenarios", row, "status=DRAFT " + row))
            self.assertIn("IV-001", st.load_snapshot(case.paths, case.cfg()).check.rule_ids(), "the status snapshot")
            self.assertNotIn("IV-001", st.load_snapshot(case.paths, case.cfg(), wave_set=(1,)).check.rule_ids(), "wave one alone does not")
            reply = tools.check(case.ws, case.name, mode="full")
            self.assertFalse(reply["clean"])
            self.assertIn("IV-001", reply["diagnostics"], "the tool")
            with mock.patch.dict(os.environ, {"GEMARC_WORKSPACE_ROOT": str(case.root)}):
                self.assertIn("IV-001", at.check(project=case.name, mode="full")["diagnostics"], "the MCP binding")
            out, code = cli.run(["--root", str(case.root), "check", case.name, "--mode", "full"])
            self.assertEqual(code, 1, "the command line: an ERROR finding is exit code 1")
            self.assertIn("IV-001", json.dumps(out), "the command line")
            state = tools.status(case.ws, case.name)
            self.assertEqual(state["phase"], "repair", "the completion contract: an ERROR of wave 2 is a repair")
            self.assertFalse(state["done"])


class OracleCase(OracleProject):
    """``OracleProject`` as a context manager (a plain helper object in a test that is not itself a TestCase subclass of it)."""

    def __init__(self):
        super().__init__("run")

    def __enter__(self):
        self.setUp()
        return self

    def __exit__(self, *exc):
        self.doCleanups()


class GeneratorClaimsTests(unittest.TestCase):
    def test_every_generated_intervention_compiles_to_what_it_declares(self):
        compared = explicit = 0
        models = [m for _p, _s, m in preset_models()]
        models += [synth.edge_case(name, seed) for name in synth.EDGE_CASES if name != "unknown-values" for seed in (0, 1, 2)]
        for index, model in enumerate(models):
            ix = E.index_model(model)
            for iv in model["interventions"]:
                compiled = E.compile_deltas(ix, [iv])
                real = "REJECTED" if compiled.status == "INVALID" else compiled.status
                declared = iv["expect"].get("status", "OK")
                self.assertEqual(declared, real, (index, iv["interventionId"], iv["type"]))
                compared += 1
                explicit += "status" in iv["expect"]
        self.assertGreater(compared, 300, "the sweep looked at something")
        self.assertGreater(explicit, 0, "some intervention still writes its status (the claim is checked, not just absent)")

    def test_a_status_the_engine_contradicts_is_what_IV_001_finds(self):
        # the non-vacuous half: take a generated model and make ONE claim wrong; the rule names exactly that intervention
        model = synth.generate("small", 3)
        victim = next(iv for iv in model["interventions"] if iv["type"] == "insert_element")
        victim["expect"]["status"] = "DRAFT"
        result = slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                  mode="full", wave_set=(1, 2))
        self.assertEqual([(d.rule, d.path) for d in result.diagnostics if d.rule == "IV-001"],
                         [("IV-001", f"interventions[{victim['interventionId']}].expect.status")])

    def test_no_edge_joins_a_record_port_to_a_message_port_and_the_record_nodes_are_still_there(self):
        events_seen = 0
        for preset, seed, model in preset_models():
            ports = {p["id"]: p["type"] for p in model["ports"]}
            for edge in model["edges"]:
                a, b = ports.get(edge["sourcePortId"]), ports.get(edge["targetPortId"])
                if a is not None and b is not None:
                    self.assertEqual(a, b, (preset, seed, edge["id"]))
            kinds = {n["id"]: n["kind"] for n in model["nodes"]}
            events_seen += sum(1 for k in kinds.values() if k == "Event")
            for edge in model["edges"]:
                if "Event" in (kinds[edge["sourceId"]], kinds[edge["targetId"]]):
                    self.assertEqual((kinds[edge["sourceId"]], kinds[edge["targetId"]]), ("Event", "Event"), (preset, seed, edge["id"]))
        self.assertGreater(events_seen, 20, "the generator still draws Event nodes")

    def test_every_case_category_occurs_from_six_cases_on(self):
        wanted = set(synth.vocab.default_profile().ids("caseCategories"))
        for preset, seed, model in preset_models():
            if len(model["cases"]) >= synth.CATEGORY_COVER_FROM:
                self.assertEqual(wanted - {c["category"] for c in model["cases"]}, set(), (preset, seed))

    def test_no_generated_case_asserts_only_completed_where_the_rule_wants_more(self):
        for preset, seed, model in preset_models():
            for case in model["cases"]:
                self.assertFalse(or_.abnormal_all_completed(case), (preset, seed, case["caseId"]))

    def test_no_two_generated_cases_have_the_same_events(self):
        for preset, seed, model in preset_models():
            result = slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                      mode="full", wave_set=(1, 2))
            self.assertEqual([d.path for d in result.diagnostics if d.rule == "CS-005"], [], (preset, seed))


class SyntheticAtlasTests(unittest.TestCase):
    def test_the_slices_of_every_preset_give_no_error_and_no_warn_in_the_default_full_check(self):
        checked = 0
        for preset, seed, model in preset_models():
            for variant, candidate in (("raw", model), ("consistent", consistent(model))):
                result = slicecheck.check(encode.encode_atl(candidate, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(candidate),
                                          mode="full", wave_set=st.FULL_WAVE_SET)
                self.assertEqual(findings(result, "ERROR", "WARN"), [], (preset, seed, variant))
                checked += 1
        self.assertEqual(checked, 2 * 11)

    def test_a_longer_sweep_of_seeds_stays_clean(self):
        for preset in ("small", "reference"):
            for seed in range(6, 26):
                model = synth.generate(preset, seed)
                result = slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                          mode="full", wave_set=st.FULL_WAVE_SET)
                self.assertEqual(findings(result, "ERROR", "WARN"), [], (preset, seed))

    def test_the_info_lines_are_the_ones_the_atlas_is_expected_to_carry(self):
        # CS-008 (events without a ruleId) and OR-005 (a sealed expectation the user has not confirmed) are what wave 2 adds; none of them is above INFO
        for preset, seed, model in preset_models():
            result = slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model),
                                      mode="full", wave_set=st.FULL_WAVE_SET)
            self.assertLessEqual({d.rule for d in result.diagnostics if d.rule in WAVE2}, {"CS-008", "OR-000"}, (preset, seed))


class RealProjectTests(OracleProject):
    """``put`` seals every slice; then the default full check is the one every surface runs."""

    def project(self, model, name):
        self.init(name=name, repos=model["meta"]["repos"])
        for slice_name, text in slice_texts(model).items():
            self.assertEqual(self.put(slice_name, text)["status"], "committed", (name, slice_name))
        return self.paths

    def default_check(self, paths):
        return st.load_snapshot(paths, pj.load_config(paths)).check

    def test_tiny_and_small_projects_have_no_error_and_no_warn(self):
        for preset, seed, model in preset_models():
            if preset == "reference":
                continue
            name = f"{preset}{seed or 0}"
            paths = self.project(model, name)
            result = self.default_check(paths)
            self.assertEqual(findings(result, "ERROR", "WARN"), [], name)
            self.assertTrue({d.rule for d in result.diagnostics} >= {"OR-005"}, "the sealed expectations show up as INFO")
            reply = tools.check(self.ws, name, mode="full")
            self.assertTrue(reply["clean"], (name, reply["diagnostics"][:300]))

    def test_the_reference_projects_have_no_error_and_no_warn_and_the_check_is_inside_the_budget(self):
        times = []
        for seed in (1, 2, 3, 4, 5):
            paths = self.project(synth.generate("reference", seed), f"reference{seed}")
            result = self.default_check(paths)
            self.assertEqual(findings(result, "ERROR", "WARN"), [], seed)
            best = min(self.timed(lambda: st.load_snapshot(paths, pj.load_config(paths))) for _ in range(3))
            check_best = min(self.timed(lambda: tools.check(self.ws, f"reference{seed}", mode="full")) for _ in range(3))
            times.append((seed, round(best, 3), round(check_best, 3)))
            self.assertLess(best, BUDGET_SECONDS, f"seed {seed}: the default full check took {best:.2f} s (design 5.5: {BUDGET_SECONDS} s)")
            self.assertLess(check_best, BUDGET_SECONDS)
        print(f"\n[default full check, reference scale, wave 1 + wave 2, best of 3, (seed, snapshot s, tools.check s)] {times}")

    @staticmethod
    def timed(call) -> float:
        start = time.perf_counter()
        call()
        return time.perf_counter() - start


def mixed_edges(model) -> list:
    """Ids of the edges whose source out-port and target in-port have different concrete types (what PT-003 reports)."""
    ports = {p["id"]: p["type"] for p in model["ports"]}
    return [e["id"] for e in model["edges"] if e["sourcePortId"] and e["targetPortId"] and ports[e["sourcePortId"]] != ports[e["targetPortId"]]]


def slice_check(model, wave_set=st.FULL_WAVE_SET):
    return slicecheck.check(encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES, state_hypothetical=True), encode.meta_of(model), mode="full", wave_set=wave_set)


class RecordCrossingTests(OracleProject):
    """The clean preset models have no Message -> Record edge, a real model has a few (a module publishing into an event record: PT-003, a WARN
    by design, and nothing records an acceptance). The ``record-crossings`` edge case keeps that population, so the viewer, the documents and
    the cases that run along such an edge stay covered, and the exact number of WARNs is pinned (debt-b4 fix: the generator change that made
    the baseline clean had also removed every crossing)."""

    SEEDS = range(0, 12)

    def test_the_edge_case_has_exactly_the_deliberate_crossings_and_cases_run_along_them(self):
        for seed in self.SEEDS:
            model = synth.edge_case("record-crossings", seed)
            crossing = mixed_edges(model)
            self.assertEqual(len(crossing), synth.RECORD_CROSSINGS, seed)
            along = {(e["sourceId"], e["targetId"]) for e in model["edges"] if e["id"] in crossing}
            traversed = {(ev["from"], ev["to"]) for case in model["cases"] for ev in case["events"]}
            self.assertEqual(along - traversed, set(), f"seed {seed}: every crossing edge is run by an event of some case")
            nodes = {n["id"]: n["kind"] for n in model["nodes"]}
            for source, target in along:
                self.assertEqual(nodes[target], "Event", seed)
                self.assertNotEqual(nodes[source], "Event", seed)

    def test_the_default_check_carries_exactly_those_warnings_and_no_error(self):
        for seed in self.SEEDS:
            model = synth.edge_case("record-crossings", seed)
            result = slice_check(model)
            self.assertEqual(findings(result, "ERROR"), [], seed)
            warns = findings(result, "WARN")
            self.assertEqual([rule for rule, _severity, _path in warns], ["PT-003"] * synth.RECORD_CROSSINGS, (seed, warns))
            self.assertEqual(sorted(path for _rule, _severity, path in warns), sorted(f"edges[{edge}].sourcePortId" for edge in mixed_edges(model)), seed)

    def test_the_other_edge_cases_and_presets_stay_free_of_crossings(self):
        for name in synth.EDGE_CASES:
            if name != "record-crossings":
                self.assertEqual(mixed_edges(synth.edge_case(name, 1)), [], name)
        for _preset, _seed, model in preset_models():
            self.assertEqual(mixed_edges(model), [])

    def test_a_real_project_shows_them_in_every_surface_and_still_builds(self):
        model = synth.edge_case("record-crossings", 3)
        self.init(name="cross", repos=model["meta"]["repos"])
        for slice_name, text in slice_texts(model).items():
            self.assertEqual(self.put(slice_name, text)["status"], "committed", slice_name)
        counts = tools.check(self.ws, "cross", mode="full")["counts"]
        self.assertEqual((counts["error"], counts["warn"]), (0, synth.RECORD_CROSSINGS), counts)
        only = tools.check(self.ws, "cross", mode="full", rule="PT-003", format="json")
        self.assertEqual(only["counts"]["warn"], synth.RECORD_CROSSINGS)
        shown = {d["path"] for d in only["diagnostics"]["diagnostics"]}   # the reply budget may show fewer lines than the count
        self.assertTrue(shown and shown <= {f"edges[{edge}].sourcePortId" for edge in mixed_edges(model)}, shown)
        reply = tools.build(self.ws, "cross", docs=True)
        self.assertEqual(reply["status"], "ok", reply)
        page = (self.paths.atlas / "dist" / "index.html").read_text(encoding="utf-8")
        for edge in mixed_edges(model):
            self.assertIn(edge, page, "the crossing edge is in the page's data")
        state = tools.status(self.ws, "cross")
        self.assertEqual(state["status"], "ok", state)

    @unittest.skipUnless(smoke.find_browser(), "NO BROWSER (Edge or Chrome) FOUND: the page with crossing edges was NOT checked in a browser (a skip is not a pass)")
    def test_the_page_with_crossing_edges_passes_the_browser_gate(self):
        model = consistent(synth.edge_case("record-crossings", 3))
        self.init(name="crossgate", repos=model["meta"]["repos"])
        for slice_name, text in slice_texts(model).items():
            self.assertEqual(self.put(slice_name, text)["status"], "committed", slice_name)
        self.assertEqual(tools.build(self.ws, "crossgate", docs=False)["status"], "ok")
        verdict = tools.verify(self.ws, "crossgate", [1366])
        self.assertEqual(verdict["status"], "ok", verdict)

    def test_the_hint_does_not_promise_an_acceptance_that_does_not_exist(self):
        hint = registry.rule("PT-003").hint
        self.assertNotIn("accept", hint.lower())
        self.assertIn("publishes into an event record", hint)
        self.assertEqual(registry.rule("PT-003").severity, "WARN", "the severity is design 5.1 and stays")


class DoneStillReachableTests(Flow):
    """The completion contract with wave 2 on: the desk goes through every gate to ``done`` (the stdio version is in test_atlas_mcp_e2e)."""

    def test_the_desk_reaches_done_with_the_real_gates(self):
        state = self.done_state()
        self.assertTrue(state["done"])
        snapshot = st.load_snapshot(self.paths, self.cfg())
        self.assertEqual(findings(snapshot.check, "ERROR", "WARN"), [])

    def test_a_warn_that_stays_open_is_named_in_the_done_line(self):
        # the desk's only case becomes abnormal and still asserts only Completed: OR-002 is a WARN, and a WARN never blocks done (design D14),
        # but a done line that says "clean" would hide it (debt-b4 fix)
        self.authored(edits=(("20-scenarios", "CS-BORROW-OK | normal |", "CS-BORROW-OK | abnormal |"),))
        self.gates_green()
        self.record()
        state = self.status()
        self.assertTrue(state["done"], state)
        self.assertEqual(state["blockers"], [])
        first = state["next"][0]
        self.assertTrue(first.startswith("done: check full has no ERROR (1 WARN open: gemarc_atlas_check mode=full lists them), 1D-5D ready"), first)
        self.assertNotIn("is clean", first)
        self.assertEqual([(rule, severity) for rule, severity, _path in findings(st.load_snapshot(self.paths, self.cfg()).check, "WARN")], [("OR-002", "WARN")])

    def test_a_done_project_without_a_warn_keeps_the_plain_line(self):
        state = self.done_state()
        self.assertTrue(state["next"][0].startswith("done: check full has no ERROR, 1D-5D ready"), state["next"][0])
        self.assertNotIn("WARN", state["next"][0])


class BuildRefusesAFailedVerdictTests(Flow):
    """With wave 2 in the default check, a sealed expectation the engine contradicts (OR-001) is an ERROR of the check that ``build`` runs: the
    build refuses and writes nothing, with or without ``release``. Before debt-b4 it wrote the page and the release gate then named the
    mismatch (``mismatch`` blocker); that path is reachable only through the oracle gate tool now. The refusal carries the rule's hint."""

    ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"

    def failed_verdict(self):
        self.authored(edits=[("20-scenarios", self.ROW, self.ROW.replace("svc.stock", "svc.stock=Failed"))])
        self.assertFalse(self.simulate()["clean"])

    def test_the_refusal_names_the_rule_and_how_to_repair_it_and_nothing_is_written(self):
        self.failed_verdict()
        self.assertEqual(self.status()["phase"], "repair")
        for release in (False, True):
            reply = tools.build(self.ws, self.name, docs=False, release=release)
            self.assertEqual((reply["status"], reply["code"]), ("invalid", "GEMARC_ATLAS_BUILD_001"), (release, reply))
            self.assertIn("gemarc_atlas_check mode=full", reply["message"])
            first = reply["diagnostics"][0]
            self.assertEqual(first["id"], "OR-001")
            self.assertIn(" | ", first["message"], "the rule's hint follows the location")
            self.assertLessEqual(tools.reply_bytes(reply), tools.BUILD_CAP)
            self.assertFalse((self.paths.atlas / "dist" / "index.html").exists(), "nothing was written")
        self.assertEqual(tools.build_all(self.ws, self.name)["status"], "invalid")

    def test_repairing_the_expectation_lets_the_build_through(self):
        self.failed_verdict()
        self.assertEqual(tools.build(self.ws, self.name, docs=False)["status"], "invalid")
        self.assertEqual(self.put("20-scenarios", desk_files()["20-scenarios"])["status"], "committed")
        self.assertTrue(self.simulate()["clean"])
        self.assertEqual(tools.build(self.ws, self.name, docs=False)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
