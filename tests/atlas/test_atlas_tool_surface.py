"""The atlas MCP surface: both tool sets, the entry prompt, and the documents that must match them.

Everything here iterates the REGISTRY of ``gemarc_mcp.atlas_tools`` (and the live ``tools/list`` of ``server.create_server``),
so a tool added by a later lane is covered the moment it is registered:

* the live list equals the registry, per tool set (``atlas``, ``atlas_remote``);
* legal names, ``mcp__gemarc__`` budget, no ``anyOf`` / null in input schemas, title, honest hints, description <= 800 B,
  ``tools/list`` of ``atlas`` <= 10 KB;
* write tools are exactly the ones the registry marks, and ``atlas_remote`` has no write tool;
* ``docs/atlas/ATLAS_TOOLS.md`` documents every tool (section, live annotation row per tool set) and nothing else;
* names cited by ``skills/gemarc-atlas`` and the examples exist (or are in the pending / planned tuples, which must not
  list a registered tool);
* the entry prompt ``gemarc-atlas-create`` exists in the atlas tool sets only, renders for both, states the stop conditions and the
  single review, and cites only registered (or pending) tools;
* the v1 tool sets are untouched by the new ``sets=`` option (see also ``test_atlas_v1_frozen.py``).

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_atlas_tool_surface*.py"
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "atlas" / "ATLAS_TOOLS.md"
SKILL_DIR = REPO / "skills" / "gemarc-atlas"
EXAMPLES = REPO / "docs" / "atlas" / "examples"

FUNCTION_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
ATLAS_NAME = re.compile(r"^gemarc_[a-z0-9_]+$")
TOOL_TOKEN = re.compile(r"(?<![A-Za-z0-9])gemarc_[a-z][a-z0-9_]*")
#: ``gemarc_`` words that are not tool names.
NOT_TOOLS = {"gemarc_mcp": "the Python package", "gemarc_atlas": "the server name in the example configs"}
TOOLSETS = ("atlas", "atlas_remote")
DESCRIPTION_BYTES = 800
LIST_BYTES = 10 * 1024
LIST_HEADROOM = 600
#: The six tools of milestone M2b: later lanes may add tools, never remove these.
M2B_LOCAL = ("gemarc_atlas_brief", "gemarc_atlas_init", "gemarc_atlas_put", "gemarc_atlas_check", "gemarc_atlas_status",
             "gemarc_atlas_index")
M2B_REMOTE = ("gemarc_atlas_brief", "gemarc_atlas_check")
M2B_WRITE = ("gemarc_atlas_init", "gemarc_atlas_put")


def cited_names(text: str) -> set:
    """Tool-like words of ``text``: globs (``gemarc_atlas_*``) are skipped and ``mcp__server__tool`` is split on ``__``."""
    found = set()
    for match in TOOL_TOKEN.finditer(text):
        if text[match.end():match.end() + 1] == "*":
            continue
        found |= {part for part in match.group(0).split("__") if part.startswith("gemarc_") and not part.endswith("_")}
    return found


def _sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _dump(tool) -> dict:
    return tool.model_dump(by_alias=True, exclude_none=True)


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class Base(unittest.TestCase):
    def setUp(self):
        from gemarc_mcp import atlas_tools, server

        self.server, self.at = server, atlas_tools
        # create_server() rewrites these module-level state objects; restore them for the tests that run after this one
        saved_registered, saved_write = list(server.REGISTERED_TOOLS), set(server.WRITE_TOOLS)
        self.addCleanup(self._restore, saved_registered, saved_write)

    def _restore(self, registered, write):
        self.server.REGISTERED_TOOLS[:] = registered
        self.server.WRITE_TOOLS.clear()
        self.server.WRITE_TOOLS.update(write)

    def live(self, toolset: str):
        """``(app, tools, registered names, write names)`` of a fresh server of this tool set."""
        app = self.server.create_server(toolset)
        tools = asyncio.run(app.list_tools())
        return app, tools, list(self.server.REGISTERED_TOOLS), set(self.server.WRITE_TOOLS)

    def all_registered(self) -> set:
        names = set()
        for toolset in TOOLSETS:
            names |= set(self.live(toolset)[2])
        return names


class ToolListTests(Base):
    def test_the_live_list_is_the_registry_per_tool_set(self):
        for toolset in TOOLSETS:
            _, tools, registered, _ = self.live(toolset)
            expected = list(self.at.tool_names(toolset))
            self.assertEqual([t.name for t in tools], expected, toolset)
            self.assertEqual(registered, expected, toolset)
            self.assertEqual(len(set(expected)), len(expected), f"{toolset}: a name is registered twice")

    def test_the_m2b_tools_are_present_in_the_pinned_shape(self):
        self.assertEqual([n for n in M2B_LOCAL if n not in self.at.tool_names("atlas")], [])
        self.assertEqual(self.at.tool_names("atlas_remote"), M2B_REMOTE, "atlas_remote is exactly two tools")

    def test_names_are_legal_prefixed_and_fit_the_budget(self):
        for toolset in TOOLSETS:
            for tool in self.live(toolset)[1]:
                self.assertRegex(tool.name, FUNCTION_NAME, tool.name)
                self.assertRegex(tool.name, ATLAS_NAME, f"[a-z0-9_] only, prefix gemarc_: {tool.name}")
                self.assertTrue(tool.name.startswith("gemarc_atlas_"), tool.name)
                self.assertLessEqual(len("mcp__gemarc__") + len(tool.name), 64, tool.name)

    def test_schemas_have_no_null_or_anyof_and_optional_inputs_have_plain_defaults(self):
        for toolset in TOOLSETS:
            for tool in self.live(toolset)[1]:
                schema = _dump(tool)["inputSchema"]
                text = json.dumps(schema)
                self.assertNotIn('"null"', text, f"{tool.name}: {text}")
                self.assertNotIn("anyOf", text, f"{tool.name}: {text}")
                self.assertNotIn("oneOf", text, f"{tool.name}: {text}")
                self.assertEqual(schema["type"], "object")
                for prop, spec in schema.get("properties", {}).items():
                    if "default" in spec:
                        self.assertIsNotNone(spec["default"], f"{tool.name}.{prop}: default must not be null")

    def test_title_and_honest_hints(self):
        for toolset in TOOLSETS:
            _, tools, _, write = self.live(toolset)
            specs = {s.name: s for s in self.at.tools_of(toolset)}
            for tool in tools:
                data = _dump(tool)
                hints = data["annotations"]
                self.assertTrue(data.get("title"), tool.name)
                self.assertEqual(hints["title"], data["title"], tool.name)
                self.assertIs(hints["openWorldHint"], False, tool.name)
                spec = specs[tool.name]
                self.assertEqual(data["title"], spec.title)
                self.assertIs(hints["readOnlyHint"], not spec.write, tool.name)
                if spec.write:   # destructive / idempotent only carry meaning for tools that write (as in v1)
                    self.assertIs(hints["destructiveHint"], spec.destructive, tool.name)
                    self.assertIs(hints["idempotentHint"], spec.idempotent, tool.name)
                else:
                    self.assertNotIn("destructiveHint", hints, tool.name)
                    self.assertNotIn("idempotentHint", hints, tool.name)
                self.assertEqual(tool.name in write, spec.write, tool.name)

    def test_write_tools_are_exactly_the_registered_writers_and_remote_has_none(self):
        _, _, _, write = self.live("atlas")
        self.assertEqual(write, {s.name for s in self.at.tools_of("atlas") if s.write})
        self.assertTrue(set(M2B_WRITE) <= write)
        self.assertEqual(self.live("atlas_remote")[3], set())
        by_name = {t.name: _dump(t)["annotations"] for t in self.live("atlas")[1]}
        self.assertIs(by_name["gemarc_atlas_put"]["destructiveHint"], True)     # replace archives, patch deletes rows
        self.assertIs(by_name["gemarc_atlas_put"]["idempotentHint"], True)
        self.assertIs(by_name["gemarc_atlas_init"]["destructiveHint"], False)   # refuses to overwrite
        for name in ("gemarc_atlas_brief", "gemarc_atlas_check", "gemarc_atlas_status", "gemarc_atlas_index"):
            self.assertIs(by_name[name]["readOnlyHint"], True, name)

    def test_description_and_list_budgets(self):
        for toolset in TOOLSETS:
            _, tools, _, _ = self.live(toolset)
            for tool in tools:
                self.assertTrue(tool.description, tool.name)
                self.assertLessEqual(len((tool.description or "").encode("utf-8")), DESCRIPTION_BYTES, tool.name)
            size = len(json.dumps([_dump(t) for t in tools], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.assertLessEqual(size, LIST_BYTES if toolset == "atlas" else 4096, f"{toolset} tools/list is {size} B")
            if toolset == "atlas":     # headroom for the next lane's parameter or longer description (it was 175 B once)
                self.assertLessEqual(size, LIST_BYTES - LIST_HEADROOM, f"tools/list is {size} B: keep {LIST_HEADROOM} B of headroom")
            print(f"\n[tools/list] {toolset}: {len(tools)} tools, {size} B")

    def test_remote_check_has_the_documented_inputs_and_no_project(self):
        by_name = {t.name: _dump(t)["inputSchema"] for t in self.live("atlas_remote")[1]}
        schema = by_name["gemarc_atlas_check"]
        self.assertEqual(sorted(schema["properties"]), ["format", "known", "text"])
        self.assertEqual(schema["required"], ["text"])
        local = {t.name: _dump(t)["inputSchema"] for t in self.live("atlas")[1]}["gemarc_atlas_check"]
        self.assertEqual(sorted(local["properties"]), ["format", "mode", "offset", "project", "rule", "slice", "text"])
        self.assertEqual(local.get("required", []), [])

    def test_instructions_fit_the_client_limit_and_name_the_entry(self):
        for toolset in TOOLSETS:
            app = self.server.create_server(toolset)
            text = app.instructions or ""
            self.assertTrue(0 < len(text) <= 4096, len(text))
            self.assertIn("gemarc_atlas_brief", text)
            self.assertIn("gemarc-atlas-create", text)
            self.assertIn("只写事实", text)
        self.assertNotIn("atlas", self.server.INSTRUCTIONS.lower())

    def test_registration_never_touches_the_workspace_or_the_write_flag(self):
        broken = "Z:/no/such/workspace"
        with mock.patch.dict(os.environ, {"GEMARC_WORKSPACE_ROOT": broken}), \
                mock.patch("gemarc_mcp.atlas.project.get_workspace", side_effect=AssertionError("workspace resolved at registration")):
            for toolset in TOOLSETS:
                self.assertTrue(self.live(toolset)[1])

    def test_a_second_registration_of_the_same_name_in_a_set_is_refused(self):
        before = len(self.at.REGISTRY)
        with self.assertRaises(ValueError):
            self.at.atlas_tool("gemarc_atlas_put", "dup", sets=("atlas",))(lambda: {})
        with self.assertRaises(ValueError):   # "both" overlaps the local registration of brief
            self.at.atlas_tool("gemarc_atlas_brief", "dup", sets=self.at.BOTH)(lambda: {})
        self.assertEqual(len(self.at.REGISTRY), before)

    def test_replies_are_compact_json_text_without_structured_content(self):
        app = self.server.create_server("atlas_remote")
        result = asyncio.run(app.call_tool("gemarc_atlas_brief", {"section": "format"}))
        self.assertIsNone(getattr(result, "structured_content", None))
        self.assertEqual(len(result.content), 1)
        text = result.content[0].text
        payload = json.loads(text)
        self.assertEqual(text, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self.assertFalse(result.is_error)
        failed = asyncio.run(app.call_tool("gemarc_atlas_check", {"text": ""}))
        self.assertTrue(failed.is_error)
        self.assertEqual(json.loads(failed.content[0].text)["status"], "invalid")


class V1UntouchedTests(Base):
    def test_v1_tool_sets_do_not_contain_atlas_tools_and_keep_their_prompt_and_resources(self):
        for toolset, count in (("full", 16), ("stateless", 8)):
            app, tools, registered, _ = self.live(toolset)
            self.assertEqual(len(tools), count)
            self.assertFalse([n for n in registered if "atlas" in n])
            self.assertEqual([p.name for p in asyncio.run(app.list_prompts())], ["gemarc-design"])
            self.assertEqual(len(asyncio.run(app.list_resources())), 2)

    def test_atlas_sets_expose_no_v1_tool_resource_or_prompt(self):
        for toolset in TOOLSETS:
            app, tools, _, _ = self.live(toolset)
            self.assertFalse([t.name for t in tools if "atlas" not in t.name])
            self.assertEqual([p.name for p in asyncio.run(app.list_prompts())], ["gemarc-atlas-create"])
            self.assertEqual(asyncio.run(app.list_resources()), [])

    def test_the_sets_option_defaults_exactly_to_the_old_rule(self):
        # stateless_ok -> full + stateless; otherwise full only: the live v1 lists prove it (names and order are pinned in test_atlas_v1_frozen)
        full = self.live("full")[2]
        stateless = self.live("stateless")[2]
        self.assertEqual(stateless, [n for n in full if n in stateless])
        self.assertEqual((len(full), len(stateless)), (16, 8))


class DocumentTests(Base):
    def setUp(self):
        super().setUp()
        self.doc = _text(DOC)

    def test_every_registered_tool_has_a_section_and_nothing_else_does(self):
        names = self.all_registered()
        sections = set(re.findall(r"^### `(gemarc_[a-z0-9_]+)`", self.doc, flags=re.M))
        self.assertEqual(sorted(names - sections), [], "tool without a section in ATLAS_TOOLS.md")
        self.assertEqual(sorted(sections - names), [], "section of a tool that is not registered")

    def test_annotation_rows_are_the_live_ones_per_tool_set(self):
        def cell(value):
            return "—" if value is None else ("true" if value else "false")

        rows = re.findall(r"^\| `(gemarc_[a-z0-9_]+)` \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \|$", self.doc, flags=re.M)
        self.assertTrue(rows, "no annotation rows found")
        index = {(r[0], r[6].strip()): [c.strip() for c in r[1:6]] for r in rows}
        self.assertEqual(len(index), len(rows), "an annotation row is duplicated")
        live_keys = set()
        for toolset in TOOLSETS:
            for tool in self.live(toolset)[1]:
                a = tool.annotations
                key = (tool.name, toolset)
                live_keys.add(key)
                self.assertIn(key, index, f"{tool.name} [{toolset}]: no annotation row")
                self.assertEqual(index[key], [tool.title, cell(a.read_only_hint), cell(a.destructive_hint), cell(a.idempotent_hint),
                                              cell(a.open_world_hint)], f"{key}: documented annotations differ from the live server")
        self.assertEqual(sorted(set(index) - live_keys), [], "annotation row of a tool that is not registered in that set")

    def test_the_document_states_the_caps_the_surface_test_enforces(self):
        for needle in ("10 KB", "800", "GEMARC_ALLOW_WRITE", "atlas_remote", "gemarc-atlas-create", "isError", "advisory"):
            self.assertIn(needle, self.doc, needle)

    def test_cited_names_exist_or_are_pending_or_planned(self):
        known = self.all_registered() | set(self.at.PENDING_TOOLS) | set(self.at.PLANNED_TOOLS) | set(NOT_TOOLS)
        unknown = sorted(cited_names(self.doc) - known)
        self.assertEqual(unknown, [])

    def test_pending_and_planned_tuples_never_list_a_registered_tool(self):
        registered = self.all_registered()
        self.assertEqual(sorted(registered & set(self.at.PENDING_TOOLS)), [], "a registered tool is still in PENDING_TOOLS: remove it")
        self.assertEqual(sorted(registered & set(self.at.PLANNED_TOOLS)), [], "a registered tool is still in PLANNED_TOOLS: remove it")


class SkillTests(Base):
    def setUp(self):
        super().setUp()
        self.text = _text(SKILL_DIR / "SKILL.md")
        match = re.match(r"^---\n(.*?)\n---\n", self.text, flags=re.S)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        self.front, self.body = match.group(1), self.text[match.end():]

    def _field(self, key):
        match = re.search(rf"^{key}:\s*(.+)$", self.front, flags=re.M)
        self.assertIsNotNone(match, f"frontmatter lacks {key}")
        return match.group(1).strip()

    def test_frontmatter_and_triggers(self):
        self.assertEqual(self._field("name"), SKILL_DIR.name)
        self.assertRegex(self._field("name"), r"^[a-z0-9]+(-[a-z0-9]+)*$")
        description = self._field("description")
        self.assertLessEqual(len(description), 1024)
        self.assertTrue(description.lower().startswith("use when"))
        for phrase in ("创建5D设计", "做5D设计资料", "create 5D design"):
            self.assertIn(phrase, description)
            self.assertIn(phrase, self.body)

    def test_the_skill_is_short_and_its_links_exist(self):
        self.assertLess(len(self.text.splitlines()), 500)
        for target in re.findall(r"\]\((references/[^)#]+)\)", self.body):
            self.assertTrue((SKILL_DIR / target).is_file(), target)
        for path in (SKILL_DIR / "references").glob("*.md"):
            self.assertLess(len(_text(path).splitlines()), 500)

    def test_cited_tool_names_exist_or_are_pending_or_planned(self):
        known = self.all_registered() | set(self.at.PENDING_TOOLS) | set(self.at.PLANNED_TOOLS) | set(NOT_TOOLS)
        unknown = []
        for path in sorted(SKILL_DIR.rglob("*.md")):
            for token in sorted(cited_names(_text(path))):
                if token not in known:
                    unknown.append(f"{path.relative_to(REPO).as_posix()}: {token}")
        self.assertEqual(unknown, [])

    def test_the_skill_cites_every_registered_tool_and_the_prompt(self):
        every = self.text + "".join(_text(p) for p in (SKILL_DIR / "references").glob("*.md"))
        self.assertEqual([n for n in self.all_registered() if n not in every], [])
        self.assertIn("gemarc-atlas-create", self.text)

    def test_the_skill_states_the_same_flow_as_the_prompt(self):
        for needle in ("needs-user", "blocked", "put mode=patch", "status", "恰好一次", "gemarc_atlas_review"):
            self.assertIn(needle, self.text, needle)
        self.assertRegex(self.text, r"3 ?轮|3 repair|三轮")


class ExampleTests(unittest.TestCase):
    def _files(self):
        return [p for p in sorted(EXAMPLES.rglob("*")) if p.is_file()]

    def test_the_four_example_files_exist(self):
        names = {p.name for p in self._files()}
        self.assertTrue({"claude-code.mcp.json", "codex.config.toml", "hermes.config.yaml", "README.md"} <= names, names)

    def test_configs_parse_and_select_the_atlas_toolset_with_the_documented_env(self):
        data = json.loads(_text(EXAMPLES / "claude-code.mcp.json"))["mcpServers"]["gemarc_atlas"]
        codex = tomllib.loads(_text(EXAMPLES / "codex.config.toml"))["mcp_servers"]["gemarc_atlas"]
        envs = [data["env"], codex["env"]]
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            envs.append(yaml.safe_load(_text(EXAMPLES / "hermes.config.yaml"))["mcp_servers"]["gemarc_atlas"]["env"])
        for env in envs:
            self.assertEqual(env["GEMARC_TOOLSET"], "atlas")
            for key in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE", "GEMARC_ATLAS_ROOTS", "PYTHONUTF8"):
                self.assertIn(key, env)
        self.assertIn("startup_timeout_sec", codex)
        self.assertIn("tool_timeout_sec", codex)
        self.assertEqual(data["type"], "stdio")

    def test_only_documented_placeholders_and_no_secret(self):
        readme = _text(EXAMPLES / "README.md")
        for path in self._files():
            if path.suffix == ".md":
                continue
            text = _text(path)
            used = set(re.findall(r"<[a-z][a-z-]*>", text))
            self.assertLessEqual(used, {"<repo>", "<workspace>"}, path.name)
            self.assertIsNone(re.search(r"Bearer\s+(?!\$\{|<)[A-Za-z0-9._~+/-]{6,}", text))
            self.assertIsNone(re.search(r"\bsk-[A-Za-z0-9]{8,}", text))
        for placeholder in ("<repo>", "<workspace>"):
            self.assertIn(f"`{placeholder}`", readme)

    def test_the_examples_are_not_in_the_dir_the_v1_tests_scan(self):
        self.assertFalse((REPO / "integration-examples" / "claude-code.mcp.json").exists())
        for path in (REPO / "integration-examples").rglob("*"):
            if path.is_file():
                self.assertNotIn("gemarc_atlas", _text(path), path.name)


class PromptTests(Base):
    def get(self, toolset, **arguments):
        app = self.server.create_server(toolset)
        result = asyncio.run(app.get_prompt("gemarc-atlas-create", arguments))
        return app, "\n".join(m.content.text for m in result.messages)

    def test_the_prompt_exists_in_the_atlas_sets_only(self):
        for toolset in TOOLSETS:
            app = self.server.create_server(toolset)
            prompts = asyncio.run(app.list_prompts())
            self.assertEqual([p.name for p in prompts], ["gemarc-atlas-create"])
            self.assertEqual(sorted(a.name for a in prompts[0].arguments or []), ["lang", "mode", "project", "requirement"])
        for toolset in ("full", "stateless"):
            app = self.server.create_server(toolset)
            self.assertNotIn("gemarc-atlas-create", [p.name for p in asyncio.run(app.list_prompts())])

    def test_it_renders_for_both_modes_and_both_tool_sets(self):
        for toolset in TOOLSETS:
            for mode in ("asis", "tobe"):
                _, text = self.get(toolset, requirement="An invented lending desk with a shelf and a loan table.", mode=mode,
                                   project="desk", lang="zh-CN")
                self.assertGreater(len(text), 400)
                self.assertIn("invented lending desk", text)
                self.assertIn("desk", text)
                self.assertIn("to-be" if mode == "tobe" else "as-is", text)
        _, empty = self.get("atlas")
        self.assertIn("未提供", empty)   # no requirement: ask the user, never invent one

    def test_it_states_the_stop_conditions_and_the_single_review(self):
        for toolset in TOOLSETS:
            _, text = self.get(toolset, requirement="x", project="desk")
            for needle in ("needs-user", "绝不编造事实", "blocked", "恰好一次", "第二轮", "预算"):
                self.assertIn(needle, text, f"{toolset}: {needle}")
            self.assertIn(f"最多修复 {self.at.MAX_REPAIRS_PER_SLICE} 轮", text)
        _, local = self.get("atlas", requirement="x", project="desk")
        for needle in ("gemarc_atlas_status", "mode=\"patch\"", "done", "红稿", "不得再开第二轮自审"):
            self.assertIn(needle, local, needle)

    def test_the_loop_budget_is_derived_from_the_briefs_sizing(self):
        at = self.at
        self.assertEqual(at.put_budget(11), at.BUDGET_FIXED + at.BUDGET_PER_GROUP)
        self.assertEqual(at.put_budget(12), at.BUDGET_FIXED + 2 * at.BUDGET_PER_GROUP)
        self.assertEqual(at.put_budget(169), at.BUDGET_FIXED + at.BUDGET_PER_GROUP * 16)
        self.assertGreater(at.put_budget(0), 0)
        text = _text(REPO / "src" / "gemarc_mcp" / "atlas" / "brief" / "economics.en-US.md")
        self.assertRegex(text, r"two puts per 11 nodes", "the budget formula follows the brief's sizing sentence")
        self.assertIn(f"{at.BUDGET_FIXED} + {at.BUDGET_PER_GROUP} x ceil(节点数/{at.BUDGET_GROUP})", self.get("atlas", requirement="x")[1])

    def test_it_cites_only_registered_or_pending_tools(self):
        for toolset in TOOLSETS:
            _, text = self.get(toolset, requirement="x", project="desk")
            registered = set(self.live(toolset)[2])
            cited = cited_names(text)
            self.assertTrue(cited, toolset)
            unknown = cited - registered - set(self.at.PENDING_TOOLS)
            self.assertEqual(sorted(unknown), [], f"{toolset}: the prompt cites unregistered tools")
        _, remote = self.get("atlas_remote", requirement="x")
        self.assertEqual(cited_names(remote), {"gemarc_atlas_brief", "gemarc_atlas_check"}, "the remote variant uses only brief and check")

    def test_the_review_clause_follows_the_registry(self):
        local = self.at.create_prompt_local(requirement="x", project="desk")
        if self.at.REVIEW_TOOL in {s.name for s in self.at.tools_of("atlas")}:
            self.assertIn('action="record"', local)
            self.assertEqual(self.at.PENDING_TOOLS, ())
        else:
            self.assertIn(self.at.REVIEW_TOOL, self.at.PENDING_TOOLS)
            self.assertIn("尚未提供", local)

    def test_the_prompt_survives_hostile_arguments(self):
        for requirement in ("{}", "{0} {name}", "%s %(x)s", "x" * 20000, "line\nbreak\n## header", "\x00"):
            text = self.at.create_prompt_local(requirement=requirement, mode="TO-BE", project="p", lang="ja-JP")
            self.assertIn("to-be", text)
            self.assertIn('languages=["ja-JP"]', text)
            self.assertIn('lang="en-US"', text)    # brief only exists in zh-CN and en-US


#: The twelve tools of design 4.1 (task tools-b3): the local tool set is exactly these plus the review tool of addendum A1 (D15).
DESIGN_TOOLS = ("gemarc_atlas_brief", "gemarc_atlas_init", "gemarc_atlas_put", "gemarc_atlas_check", "gemarc_atlas_status", "gemarc_atlas_index",
                "gemarc_atlas_simulate", "gemarc_atlas_doc", "gemarc_atlas_outline", "gemarc_atlas_evidence", "gemarc_atlas_build",
                "gemarc_atlas_verify")
#: Inputs of design 4.1 and their defaults (``None`` = required): the live schema must say exactly this.
DESIGN_INPUTS = {
    "gemarc_atlas_simulate": {"project": None, "case": "", "intervention": "", "detail": "count"},
    "gemarc_atlas_doc": {"project": None, "unit": "system", "level": "BD", "lang": "", "chapter": "", "page": 0},
    "gemarc_atlas_outline": {"root": None, "path": None, "offset": 0},
    "gemarc_atlas_evidence": {"project": None, "ids": []},
    "gemarc_atlas_build": {"project": None, "release": False, "docs": True, "gate": "none"},
    "gemarc_atlas_verify": {"project": None, "widths": [1024, 1366, 1600]},
}
#: (readOnly, destructive, idempotent) of the new tools; destructive and idempotent only exist for write tools.
DESIGN_HINTS = {
    "gemarc_atlas_simulate": (False, False, True), "gemarc_atlas_doc": (True, None, None), "gemarc_atlas_outline": (True, None, None),
    "gemarc_atlas_evidence": (False, False, True), "gemarc_atlas_build": (False, True, True), "gemarc_atlas_verify": (False, False, True),
}


class BatchThreeSurfaceTests(Base):
    def test_the_local_set_is_the_twelve_design_tools_plus_the_review_tool_and_nothing_is_planned_any_more(self):
        local = self.at.tool_names("atlas")
        self.assertEqual(sorted(local), sorted(DESIGN_TOOLS + (self.at.REVIEW_TOOL,)))
        self.assertEqual(len(local), 13)
        self.assertEqual(self.at.tool_names("atlas_remote"), M2B_REMOTE, "atlas_remote stays the two read-only tools")
        self.assertEqual((self.at.PLANNED_TOOLS, self.at.PENDING_TOOLS), ((), ()))
        live = [t.name for t in self.live("atlas")[1]]
        self.assertEqual(live, list(local))

    def test_the_inputs_and_defaults_are_exactly_those_of_the_design_table(self):
        by_name = {t.name: _dump(t)["inputSchema"] for t in self.live("atlas")[1]}
        for name, expected in DESIGN_INPUTS.items():
            schema = by_name[name]
            self.assertEqual(sorted(schema["properties"]), sorted(expected), name)
            self.assertEqual(sorted(schema.get("required", [])), sorted(k for k, v in expected.items() if v is None), name)
            for key, default in expected.items():
                if default is not None:
                    self.assertEqual(schema["properties"][key].get("default"), default, f"{name}.{key}")
        self.assertEqual(by_name["gemarc_atlas_verify"]["properties"]["widths"]["items"]["type"], "integer")
        self.assertEqual(by_name["gemarc_atlas_evidence"]["properties"]["ids"]["items"]["type"], "string")

    def test_the_hints_of_the_new_tools_are_honest(self):
        specs = {s.name: s for s in self.at.tools_of("atlas")}
        by_name = {t.name: _dump(t)["annotations"] for t in self.live("atlas")[1]}
        for name, (read_only, destructive, idempotent) in DESIGN_HINTS.items():
            hints = by_name[name]
            self.assertIs(hints["readOnlyHint"], read_only, name)
            self.assertIs(specs[name].write, not read_only, name)
            if read_only:
                self.assertNotIn("destructiveHint", hints)
                self.assertNotIn("idempotentHint", hints)
            else:
                self.assertIs(hints["destructiveHint"], destructive, name)
                self.assertIs(hints["idempotentHint"], idempotent, name)
            self.assertIs(hints["openWorldHint"], False, name)

    def test_the_write_tools_are_the_ones_that_write_files(self):
        _, _, _, write = self.live("atlas")
        self.assertEqual(write, {"gemarc_atlas_init", "gemarc_atlas_put", "gemarc_atlas_simulate", "gemarc_atlas_evidence", "gemarc_atlas_build",
                                 "gemarc_atlas_verify", "gemarc_atlas_review"})

    def test_the_list_budget_keeps_a_margin(self):
        tools = self.live("atlas")[1]
        size = len(json.dumps([_dump(t) for t in tools], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(size, LIST_BYTES)
        print(f"\n[tools/list] 13 tools {size} B of {LIST_BYTES} B")
        longest = max(len((t.description or "").encode("utf-8")) for t in tools)
        self.assertLessEqual(longest, 400, "descriptions are about 300 B each; the details live in brief and ATLAS_TOOLS.md")

    def test_the_bound_tools_run_inside_the_product_gate_context_and_nothing_else_does(self):
        from gemarc_mcp.atlas import status as status_mod

        seen = []
        bound = self.at._bind(lambda: seen.append(status_mod.product_gates_active()) or {"status": "ok"}, lambda fn: fn())  # noqa: SLF001
        self.assertFalse(status_mod.product_gates_active())
        bound()
        self.assertEqual((seen, status_mod.product_gates_active()), ([True], False))

    def test_every_new_tool_has_a_section_a_row_and_is_cited_by_the_skill_and_the_prompt(self):
        doc = _text(DOC)
        skill = _text(SKILL_DIR / "SKILL.md")
        prompt = self.at.create_prompt_local(requirement="x", project="desk")
        for name in DESIGN_INPUTS:
            self.assertIn(f"### `{name}`", doc, name)
            self.assertIn(name, skill, name)
            if name != "gemarc_atlas_outline":      # the prompt names the outline only through the brief (source reading)
                self.assertIn(name, prompt, name)
        self.assertIn("waive-browser.txt", doc)
        self.assertIn("waive-browser.txt", skill)

    def test_the_prompt_walks_the_gates_in_the_status_order_with_one_review(self):
        text = self.at.create_prompt_local(requirement="x", project="desk")
        positions = [text.index(f"gemarc_atlas_{name}(project") for name in ("simulate", "evidence", "build", "verify")]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], text.index("gemarc_atlas_review(project"))
        self.assertIn("SKIPPED", text)
        self.assertIn("绝不写这个文件", text)
        self.assertIn("oracle-sheet.md", text)
        self.assertEqual(text.count("恰好一次"), 1)
        self.assertIn("不得再开第二轮自审", text)
        self.assertIn("这不是第二轮自审", text, "re-running the gates after a review fix is not a second review")


if __name__ == "__main__":
    unittest.main()
