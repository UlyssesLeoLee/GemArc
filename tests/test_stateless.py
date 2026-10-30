"""Stateless tools and the cross-client tool contract.

Two groups of guarantees live here:

* the *tool contract* every MCP client depends on: legal function names, input
  schemas without null branches, safety annotations, failures flagged as errors;
* the *stateless tools*: model in, result out, no workspace, and an output that
  equals what the stateful build writes.

The contract tests run against the real SDK (skipped when it is not installed).
"""
import asyncio
import copy
import itertools
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
import io
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import model as model_lib, server, service, simulation, stateless  # noqa: E402
from gemarc_mcp.paths import GemArcError, Workspace, safe_response  # noqa: E402

FUNCTION_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DEMO_MODEL = json.loads((REPO_ROOT / "examples" / "demo-site" / "data" / "architecture.json")
                        .read_text(encoding="utf-8"))


def _sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:
        return False


def _lf(blob: bytes) -> bytes:
    return blob.replace(bytes([13, 10]), bytes([10]))


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class ToolContractTests(unittest.TestCase):
    """What Claude, Codex and Hermes each require of a tool, checked on the live list."""

    def _tools(self, toolset):
        app = server.create_server(toolset)
        return app, asyncio.run(app.list_tools())

    def test_names_are_legal_function_names_in_every_toolset(self):
        for toolset in server.TOOLSETS:
            _, tools = self._tools(toolset)
            self.assertTrue(tools)
            for tool in tools:
                self.assertRegex(tool.name, FUNCTION_NAME, f"{toolset}: {tool.name}")
                self.assertTrue(tool.name.startswith("gemarc_"), "keep the prefix: some clients do not namespace")

    def test_names_fit_the_prefixed_length_budget(self):
        # Codex qualifies a tool as mcp__<server>__<tool>; clients cap the whole thing at 64.
        _, tools = self._tools("full")
        for tool in tools:
            self.assertLessEqual(len("mcp__gemarc__") + len(tool.name), 64, tool.name)

    def test_input_schemas_have_no_null_branches(self):
        for toolset in server.TOOLSETS:
            _, tools = self._tools(toolset)
            for tool in tools:
                schema = tool.model_dump(by_alias=True, exclude_none=True)["inputSchema"]
                text = json.dumps(schema)
                self.assertNotIn('"null"', text, f"{tool.name}: null type in {text}")
                self.assertNotIn("anyOf", text, f"{tool.name}: anyOf in {text}")
                self.assertEqual(schema["type"], "object")

    def test_every_tool_declares_title_and_honest_safety_hints(self):
        app, tools = self._tools("full")
        for tool in tools:
            data = tool.model_dump(by_alias=True, exclude_none=True)
            self.assertTrue(data.get("title"), tool.name)
            hints = data["annotations"]
            self.assertIs(hints["openWorldHint"], False, tool.name)
            writes = tool.name in server.WRITE_TOOLS
            self.assertIs(hints["readOnlyHint"], not writes, tool.name)
        self.assertEqual(server.WRITE_TOOLS,
                         {"gemarc_scaffold", "gemarc_init", "gemarc_model", "gemarc_build", "gemarc_export"})
        by_name = {t.name: t.model_dump(by_alias=True, exclude_none=True) for t in tools}
        self.assertIs(by_name["gemarc_model"]["annotations"]["destructiveHint"], True)
        self.assertIs(by_name["gemarc_scaffold"]["annotations"]["destructiveHint"], False)

    def test_stateless_toolset_has_no_workspace_or_write_tool(self):
        _, tools = self._tools("stateless")
        names = [t.name for t in tools]
        self.assertEqual(names, ["gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model",
                                 "gemarc_validate", "gemarc_simulate", "gemarc_diff", "gemarc_render"])
        self.assertEqual(server.WRITE_TOOLS, set())

    def test_failures_are_flagged_as_errors_and_keep_the_payload(self):
        app, _ = self._tools("stateless")
        failed = asyncio.run(app.call_tool("gemarc_validate", {}))
        self.assertTrue(failed.is_error)
        payload = _decode(failed)
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["diagnostics"][0]["id"], "GEMARC_INPUT_003")

        ok = asyncio.run(app.call_tool("gemarc_validate", {"model": DEMO_MODEL}))
        self.assertFalse(ok.is_error)
        self.assertEqual(_decode(ok)["status"], "ok")

    def test_unknown_toolset_is_rejected(self):
        with self.assertRaises(ValueError):
            server.active_toolset("everything")


class InlineModelTests(unittest.TestCase):
    def test_exactly_one_source_is_required(self):
        with self.assertRaises(GemArcError) as both:
            server._source("demo", DEMO_MODEL)
        self.assertEqual(both.exception.code, "GEMARC_INPUT_003")
        with self.assertRaises(GemArcError):
            server._source("", {})

    def test_inline_digest_is_stable_and_order_independent(self):
        shuffled = dict(reversed(list(DEMO_MODEL.items())))
        self.assertEqual(stateless.model_digest(DEMO_MODEL), stateless.model_digest(shuffled))
        changed = json.loads(json.dumps(DEMO_MODEL))
        changed["meta"]["system"] = "other"
        self.assertNotEqual(stateless.model_digest(DEMO_MODEL), stateless.model_digest(changed))

    def test_oversized_model_is_refused(self):
        huge = {"meta": {"system": "x"}, "nodes": [], "edges": [], "pad": "x" * (5 * 1024 * 1024)}
        with self.assertRaises(GemArcError) as ctx:
            stateless.inline_model(huge)
        self.assertEqual(ctx.exception.code, "GEMARC_MODEL_002")

    def test_non_object_is_refused(self):
        for bad in (None, [], "model", 3):
            with self.assertRaises(GemArcError):
                stateless.inline_model(bad)

    def test_diff_models_matches_workspace_diff(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["GEMARC_ALLOW_WRITE"] = "1"
            try:
                ws = Workspace(root)
                service.init_project(ws, "a")
                service.init_project(ws, "b")
                path, data = service.load_model(ws, "b")
                data["nodes"].append({"id": "extra", "name": "Extra", "kind": "Service",
                                      "verification": "illustrative", "evidence": "x"})
                from gemarc_mcp.paths import atomic_json
                atomic_json(path, data)
                by_project = service.diff(ws, "a", "b")
                before = service.load_model(ws, "a")[1]
                by_model = service.diff_models(before, data, by_project["baseSha256"], by_project["targetSha256"])
            finally:
                os.environ["GEMARC_ALLOW_WRITE"] = "0"
        self.assertEqual(by_project, by_model)
        self.assertEqual(by_model["nodes"]["added"], ["extra"])


class NewModelTests(unittest.TestCase):
    def test_new_model_is_the_scaffold_template_with_slots(self):
        out = stateless.new_model("Order System")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["model"]["meta"]["system"], "Order System")
        self.assertEqual(out["model"]["nodes"], [])
        self.assertTrue(out["slots"])
        self.assertTrue(all({"id", "pointer", "what", "required", "rule"} <= set(s) for s in out["slots"]))

    def test_new_model_requires_a_name(self):
        for bad in ("", "   ", "x" * 81):
            with self.assertRaises(GemArcError):
                stateless.new_model(bad)


class RenderTests(unittest.TestCase):
    @staticmethod
    def _build_with_workspace(profile):
        with tempfile.TemporaryDirectory() as root:
            os.environ["GEMARC_ALLOW_WRITE"] = "1"
            try:
                ws = Workspace(root)
                service.init_project(ws, "p")
                from gemarc_mcp.paths import atomic_json
                atomic_json(ws.path("p/model/architecture.json"), DEMO_MODEL)
                result = service.build(ws, "p", None, profile)
                assert result["status"] == "ok", result
                site = Path(root) / "p" / "site"
                return {p.relative_to(site).as_posix(): p.read_bytes() for p in site.rglob("*") if p.is_file()}
            finally:
                os.environ["GEMARC_ALLOW_WRITE"] = "0"

    def test_render_plus_kit_equals_build_byte_for_byte(self):
        for profile in stateless.SITE_PROFILES:
            built = self._build_with_workspace(profile)
            rendered = stateless.render(DEMO_MODEL, profile)
            self.assertEqual(rendered["status"], "ok")
            self.assertTrue(rendered["inline"])
            assembled = {path: raw for path, raw in stateless._kit_entries(profile)}
            for entry in rendered["files"]:
                assembled[entry["path"]] = entry["content"].encode("utf-8")
            self.assertEqual(sorted(assembled), sorted(built), profile)
            for path in built:
                # The builder writes in text mode, so a Windows checkout gets CRLF;
                # compare content, as tests/test_generated_artifacts.py does.
                self.assertEqual(_lf(assembled[path]), _lf(built[path]), f"{profile}:{path}")

    def test_render_rejects_an_invalid_model_without_files(self):
        broken = json.loads(json.dumps(DEMO_MODEL))
        broken["edges"][0]["targetId"] = "does-not-exist"
        out = stateless.render(broken)
        self.assertEqual(out["status"], "invalid")
        self.assertNotIn("files", out)

    def test_render_rejects_unknown_profile(self):
        for bad in ("mvp", "nope", ""):
            with self.assertRaises(GemArcError):
                stateless.render(DEMO_MODEL, bad)

    def test_render_withholds_content_when_it_would_be_too_large(self):
        original = stateless.RENDER_INLINE_LIMIT
        stateless.RENDER_INLINE_LIMIT = 100
        try:
            out = stateless.render(DEMO_MODEL)
        finally:
            stateless.RENDER_INLINE_LIMIT = original
        self.assertFalse(out["inline"])
        self.assertTrue(all("content" not in f and f["sha256"] for f in out["files"]))
        self.assertIn("gemarc build", out["notice"])

    def test_inline_render_stays_well_under_client_output_caps(self):
        out = stateless.render(DEMO_MODEL)
        # Codex caps what the model sees at ~12k tokens (~36 KB of JSON).
        self.assertLess(len(json.dumps(out, ensure_ascii=False).encode("utf-8")), 32 * 1024)


class SiteKitTests(unittest.TestCase):
    def test_kit_excludes_model_dependent_files(self):
        for profile in stateless.SITE_PROFILES:
            paths = [e["path"] for e in stateless.kit_listing(profile)]
            self.assertFalse([p for p in paths if p.startswith("data/")])
            self.assertIn("runtime/core/model.js", paths)
            self.assertIn("index.html", paths)

    def test_kit_archive_is_deterministic(self):
        first = stateless.site_kit_zip("static")
        stateless.site_kit_zip.cache_clear()
        stateless._kit_entries.cache_clear()
        second = stateless.site_kit_zip("static")
        self.assertEqual(first, second)

    def test_kit_archive_is_a_valid_zip_matching_the_manifest(self):
        kit = stateless.site_kit("static")
        with zipfile.ZipFile(io.BytesIO(stateless.site_kit_zip("static"))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), kit["fileCount"])
            self.assertTrue(all(not n.startswith(("/", "..")) for n in archive.namelist()))
        self.assertEqual(kit["path"], "/kit/static.zip")

    def test_kit_url_follows_the_public_url_setting(self):
        previous = {k: os.environ.pop(k, None) for k in ("GEMARC_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL")}
        try:
            self.assertIsNone(stateless.site_kit("static")["url"])
            os.environ["VERCEL_PROJECT_PRODUCTION_URL"] = "gemarc.example.app"
            self.assertEqual(stateless.site_kit("static")["url"], "https://gemarc.example.app/kit/static.zip")
            os.environ["GEMARC_PUBLIC_URL"] = "https://mcp.example.com/"
            self.assertEqual(stateless.site_kit("static")["url"], "https://mcp.example.com/kit/static.zip")
        finally:
            for key, value in previous.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

    def test_preview_deployments_do_not_advertise_the_production_kit(self):
        names = ("GEMARC_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_ENV")
        previous = {k: os.environ.pop(k, None) for k in names}
        try:
            os.environ["VERCEL_PROJECT_PRODUCTION_URL"] = "gemarc.example.app"
            os.environ["VERCEL_ENV"] = "preview"  # that variable names PRODUCTION even on a preview
            self.assertIsNone(stateless.site_kit("static")["url"])
            os.environ["VERCEL_ENV"] = "production"
            self.assertEqual(stateless.site_kit("static")["url"], "https://gemarc.example.app/kit/static.zip")
            os.environ["VERCEL_ENV"] = "preview"
            os.environ["GEMARC_PUBLIC_URL"] = "https://mcp.example.com"  # an explicit setting always wins
            self.assertEqual(stateless.site_kit("static")["url"], "https://mcp.example.com/kit/static.zip")
        finally:
            for key, value in previous.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

    def test_kit_archive_does_not_depend_on_the_host_os(self):
        def build(platform):
            stateless.site_kit_zip.cache_clear()
            stateless._kit_entries.cache_clear()
            with mock.patch.object(sys, "platform", platform):  # zipfile stamps the host OS into every entry
                return stateless.site_kit_zip("static")
        try:
            self.assertEqual(build("win32"), build("linux"))
        finally:
            stateless.site_kit_zip.cache_clear()
            stateless._kit_entries.cache_clear()

    def test_unknown_kit_profile_is_rejected(self):
        with self.assertRaises(GemArcError):
            stateless.site_kit_zip("mvp")


class RemoteBriefTests(unittest.TestCase):
    def test_remote_brief_only_names_tools_that_exist_remotely(self):
        text = json.dumps(service.brief(stateless=True), ensure_ascii=False)
        self.assertIn("gemarc_render", text)
        for local_only in ("gemarc_build", "gemarc_scaffold", "gemarc_inspect"):
            self.assertNotIn(local_only, text)

    def test_local_brief_is_unchanged(self):
        text = json.dumps(service.brief(), ensure_ascii=False)
        self.assertIn("gemarc_build", text)


class ContractsLookupTests(unittest.TestCase):
    """gemarc_contracts(query) is a key into the listing, never a path (the tool is on the public tool set)."""

    def test_listed_files_are_served(self):
        listing = service.contracts()
        for name in listing["contracts"][:2] + listing["vocabularies"][:2]:
            self.assertIn("document", service.contracts(name), name)

    def test_paths_are_refused_whether_or_not_the_file_exists(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.json"
            secret.write_text('{"api_key": "not-for-you"}', encoding="utf-8")
            listed = service.contracts()["contracts"][0]
            hostile = (str(secret), secret.as_posix(), "../../../../vercel.json", "..\\..\\..\\..\\vercel.json",
                       "./" + listed, "x/../" + listed, listed + "/", str(Path(outside) / "missing.json"))
            for query in hostile:
                with self.subTest(query=query):
                    with self.assertRaises(GemArcError) as caught:
                        service.contracts(query)
                    # one code for "exists" and "does not exist": the answer is no oracle for the file system
                    self.assertEqual(caught.exception.code, "GEMARC_CONTRACT_001")


class ModelBoundaryTests(unittest.TestCase):
    """What validate accepts, render and simulate must accept; what JSON cannot hold is refused up front."""

    def test_collections_that_validate_treats_as_empty_do_not_crash_render_or_simulate(self):
        for value in (None, "x", 0, {"a": 1}, True):
            with self.subTest(scenarios=value):
                model = copy.deepcopy(DEMO_MODEL)
                model["scenarios"] = value
                self.assertEqual(model_lib.validate(model)["status"], "ok")  # the precondition
                self.assertEqual(stateless.render(model)["status"], "ok")
                with self.assertRaises(GemArcError) as caught:
                    simulation.simulate(model, "normal")
                self.assertEqual(caught.exception.code, "GEMARC_DYNAMIC_006")

    def test_non_finite_numbers_are_refused_not_written_into_data_files(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                model = copy.deepcopy(DEMO_MODEL)
                model["nodes"][0].setdefault("properties", {})["load"] = value
                with self.assertRaises(GemArcError) as caught:
                    stateless.inline_model(model)
                self.assertEqual(caught.exception.code, "GEMARC_INPUT_004")
                self.assertEqual(safe_response(lambda: stateless.render(model))["status"], "invalid")


class RenderResultSizeTests(unittest.TestCase):
    def test_the_validation_echo_is_capped_so_the_whole_result_stays_small(self):
        """One warning per evidence-less node must not grow the tool result without bound."""
        model = stateless.new_model("size")["model"]
        model["nodes"] = [{"id": f"n{i}", "name": f"n{i}", "kind": "Service"} for i in range(250)]
        model["edges"] = [{"id": f"e{i}", "sourceId": f"n{i}", "targetId": f"n{i + 1}", "type": "sends"}
                          for i in range(249)]
        total = model_lib.validate(model)["summary"]["warnings"]
        self.assertGreater(total, stateless.RENDER_MAX_WARNINGS)  # the precondition: a flood of warnings
        out = stateless.render(model)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["validation"]["warnings"]), stateless.RENDER_MAX_WARNINGS)
        self.assertEqual(out["validation"]["warningsOmitted"], total - stateless.RENDER_MAX_WARNINGS)
        self.assertEqual(out["validation"]["summary"]["warnings"], total)
        self.assertLess(len(json.dumps(out, ensure_ascii=False).encode("utf-8")), 32 * 1024)

    def test_a_few_warnings_are_returned_untouched(self):
        model = copy.deepcopy(DEMO_MODEL)
        result = model_lib.validate(model)
        self.assertLessEqual(len(result["warnings"]), stateless.RENDER_MAX_WARNINGS)
        self.assertEqual(stateless.render(model)["validation"], result)


class ScenarioPrecomputeTests(unittest.TestCase):
    @staticmethod
    def _model(count=5):
        model = copy.deepcopy(DEMO_MODEL)
        template = model["scenarios"][0]
        for number in range(count):
            clone = copy.deepcopy(template)
            clone["id"], clone["name"] = f"clone{number}", f"clone {number}"
            model["scenarios"].append(clone)
        return model

    def test_precompute_equals_running_each_scenario_directly(self):
        model = self._model()
        first = model["nodes"][0]["id"]
        expected = {s["id"]: {"normal": simulation.simulate(model, s["id"]),
                              "disabledSample": simulation.compare(model, s["id"], [first])}
                    for s in model["scenarios"]}
        self.assertEqual(stateless.precompute_scenarios(model), expected)

    def test_render_validates_the_model_once_not_once_per_scenario_run(self):
        model = self._model()
        with mock.patch.object(stateless, "validate", wraps=stateless.validate) as outer, \
                mock.patch.object(simulation, "validate", wraps=simulation.validate) as inner:
            self.assertEqual(stateless.render(model)["status"], "ok")
        self.assertEqual(outer.call_count, 1)
        self.assertEqual(inner.call_count, 0)

    def test_compare_validates_once_and_can_reuse_a_baseline(self):
        model = self._model(1)
        first = model["nodes"][0]["id"]
        with mock.patch.object(simulation, "validate", wraps=simulation.validate) as spy:
            plain = simulation.compare(model, model["scenarios"][0]["id"], [first])
        self.assertEqual(spy.call_count, 1)
        baseline = simulation.simulate(model, model["scenarios"][0]["id"], validated=True)
        again = simulation.compare(model, model["scenarios"][0]["id"], [first], validated=True, baseline=baseline)
        self.assertEqual(again, plain)


class DiffImpactTests(unittest.TestCase):
    @staticmethod
    def _node(ident, name):
        return {"id": ident, "name": name, "kind": "Service", "verification": "illustrative", "evidence": "x"}

    @staticmethod
    def _edge(ident, source, target):
        return {"id": ident, "sourceId": source, "targetId": target, "type": "sends",
                "verification": "illustrative", "evidence": "x"}

    def test_affected_nodes_are_one_hop_and_do_not_depend_on_edge_order(self):
        edges = {"e1": self._edge("e1", "X", "Y"), "e2": self._edge("e2", "Y", "Z"), "e3": self._edge("e3", "Z", "W")}
        before_nodes = [self._node(ident, ident.lower()) for ident in "XYZW"]
        after_nodes = [self._node("X", "renamed")] + before_nodes[1:]
        answers = set()
        for order in itertools.permutations(edges):
            listed = [edges[key] for key in order]
            before = {"meta": {"system": "t"}, "nodes": before_nodes, "edges": listed}
            after = {"meta": {"system": "t"}, "nodes": after_nodes, "edges": listed}
            answers.add(tuple(service.diff_models(before, after, "a", "b")["affectedNodes"]))
        self.assertEqual(answers, {("X", "Y")})


if __name__ == "__main__":
    unittest.main()
