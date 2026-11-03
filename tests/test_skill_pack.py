"""The multimedia document workflow must ship with the server, not beside it as a skill.

The workflow used to live only in ``skills/gemarc-media-doc/``, so a client had
to install a separate skill before it could make a multimedia document. The server now
carries the same text inside the package and hands it out as a tool, as
resources and through the prompt (see ADR-012).

Checks:

* the editable source and the copy inside the package carry the same text -
  the generator must never be forgotten;
* the package data declaration covers every packed file, so a wheel built from
  this tree really carries the workflow;
* the workflow is not empty and names only tools the server registers: a
  workflow that degraded into an empty string, or into a tool name that does
  not exist, would still satisfy a pure "the copies agree" check;
* the server really hands it out - the tool, the two resources and the prompt.
"""
from __future__ import annotations

import asyncio
import re
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import server, service  # noqa: E402
from gemarc_mcp.scaffold import skillpack  # noqa: E402

PACKAGE_ROOT = REPO_ROOT / "src" / "gemarc_mcp"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The tool a client calls to get the workflow.
GUIDE_TOOL = "gemarc_media_doc_guide"
#: The URIs the workflow is served under.
WORKFLOW_URI = "gemarc://skill/gemarc-media-doc"
ASSEMBLE_URI = "gemarc://skill/gemarc-media-doc/references/assemble-site.md"

#: ``gemarc_`` words that are not tool names.
NOT_TOOLS = {"gemarc_mcp", "gemarc_local"}
TOOL_TOKEN = re.compile(r"(?<![A-Za-z0-9])gemarc_[a-z0-9][a-z0-9_]*")


def _pattern_matches(pattern: str, path: str) -> bool:
    """``fnmatch``-like matching where ``**`` crosses directory separators."""
    regex = re.escape(pattern)
    regex = regex.replace(r"\*\*/", ".*").replace(r"\*\*", ".*")
    regex = regex.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(regex, path) is not None


def _sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class PackedCopyTests(unittest.TestCase):
    """The copy inside the package must follow the editable source."""

    def test_the_workflow_lives_inside_the_package(self):
        packed = PACKAGE_ROOT / "scaffold" / "skills" / skillpack.SKILL_NAME
        self.assertTrue(packed.is_dir(), f"{packed} is missing: run python -m gemarc_mcp.scaffold.skillpack")
        self.assertTrue((packed / "SKILL.md").is_file())

    def test_packed_copy_matches_the_editable_source(self):
        source = skillpack.source_dir()
        if source is None:
            self.skipTest("not an editable checkout: the packaged copy is the only truth")
        expected = {p.relative_to(source).as_posix(): skillpack.normalised_bytes(p.read_bytes())
                    for p in skillpack.source_files()}
        actual = {p.relative_to(PACKAGE_ROOT / "scaffold" / "skills" / skillpack.SKILL_NAME).as_posix():
                  skillpack.normalised_bytes(p.read_bytes())
                  for p in skillpack.packed_files()}
        self.assertEqual(sorted(actual), sorted(expected),
                         "the packed copy has other files than skills/gemarc-media-doc")
        for name, raw in expected.items():
            self.assertEqual(actual.get(name), raw,
                             f"{name} differs from skills/gemarc-media-doc; "
                             "run python -m gemarc_mcp.scaffold.skillpack")
        self.assertTrue(skillpack.is_in_sync())

    def test_package_data_covers_every_packed_file(self):
        """A wheel built from this tree must carry the workflow."""
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        patterns = data["tool"]["setuptools"]["package-data"]["gemarc_mcp"]
        root = PACKAGE_ROOT / "scaffold" / "skills"
        files = sorted(p.relative_to(PACKAGE_ROOT).as_posix() for p in root.rglob("*") if p.is_file())
        self.assertTrue(files, "no packed workflow file found")
        for relative in files:
            self.assertTrue(any(_pattern_matches(pattern, relative) for pattern in patterns),
                            f"{relative} is not covered by the package-data patterns {patterns}")

    def test_the_generator_reproduces_the_committed_copy(self):
        """``sync()`` must be idempotent: running it changes nothing."""
        source = skillpack.source_dir()
        if source is None:
            self.skipTest("not an editable checkout")
        before = {p.relative_to(root).as_posix(): skillpack.normalised_bytes(p.read_bytes())
                  for root in (PACKAGE_ROOT / "scaffold" / "skills" / skillpack.SKILL_NAME,)
                  for p in root.rglob("*") if p.is_file()}
        skillpack.sync()
        after = {p.relative_to(root).as_posix(): skillpack.normalised_bytes(p.read_bytes())
                 for root in (PACKAGE_ROOT / "scaffold" / "skills" / skillpack.SKILL_NAME,)
                 for p in root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class WorkflowContentTests(unittest.TestCase):
    """The workflow must be real, non-empty text that only names real tools."""

    def setUp(self):
        self.text = skillpack.read_text("SKILL.md")
        self.body = skillpack.split_frontmatter(self.text)[1]

    def test_the_workflow_is_not_empty_and_survives_utf8(self):
        self.assertGreater(len(self.body), 3000, "the workflow body is suspiciously short")
        self.assertNotIn("\ufffd", self.text, "the workflow text was mangled")
        self.assertIn("5D", self.body)
        self.assertIn("gemarc_validate", self.body)

    def test_the_workflow_names_only_registered_tools(self):
        app = server.create_server("full")
        known = set(server.REGISTERED_TOOLS) | NOT_TOOLS
        unknown = []
        for match in TOOL_TOKEN.finditer(self.text):
            for part in match.group(0).split("__"):
                if part.startswith("gemarc_") and part not in known:
                    unknown.append(part)
        self.assertEqual(unknown, [], "the workflow names tools the server does not register")
        self.assertIn(GUIDE_TOOL, known)

    def test_the_workflow_covers_every_stateless_tool(self):
        """The remote flow must be complete: every tool it can call is mentioned."""
        app = server.create_server("stateless")
        for name in server.REGISTERED_TOOLS:
            self.assertIn(name, self.text, f"{name} is not mentioned in the workflow")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class ServerSurfaceTests(unittest.TestCase):
    """The server must really hand the workflow out."""

    def setUp(self):
        self._registered = list(server.REGISTERED_TOOLS)
        self._write = set(server.WRITE_TOOLS)
        self.addCleanup(self._restore)

    def _restore(self):
        server.REGISTERED_TOOLS[:] = self._registered
        server.WRITE_TOOLS.clear()
        server.WRITE_TOOLS.update(self._write)

    def test_the_guide_tool_is_registered_in_both_toolsets(self):
        for toolset in ("full", "stateless"):
            app = server.create_server(toolset)
            self.assertIn(GUIDE_TOOL, server.REGISTERED_TOOLS, toolset)
            tools = {tool.name: tool for tool in asyncio.run(app.list_tools())}
            self.assertIn(GUIDE_TOOL, tools)
            description = tools[GUIDE_TOOL].description or ""
            self.assertLessEqual(len(description), 4096)
            self.assertIn("制作多媒体文档", description,
                          "the description must name the Chinese trigger words")
            self.assertIn("制作5D设计", description,
                          "the legacy trigger words must keep working")
            self.assertTrue(tools[GUIDE_TOOL].annotations.read_only_hint)

    def test_the_guide_returns_the_workflow(self):
        payload = service.guide()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["topic"], "workflow")
        self.assertGreater(payload["bytes"], 3000)
        self.assertNotIn("\ufffd", payload["content"])
        self.assertIn("制作多媒体文档", payload["content"] + payload["notice"])
        self.assertIn("制作5D设计", payload["content"] + payload["notice"],
                      "the legacy trigger words must keep working")
        self.assertEqual(payload["source"], WORKFLOW_URI)
        self.assertEqual([item["id"] for item in payload["sections"]], ["workflow", "assemble"])

    def test_the_guide_rejects_an_unknown_topic(self):
        with self.assertRaises(Exception) as caught:
            service.guide("nope")
        self.assertEqual(getattr(caught.exception, "code", ""), "GEMARC_GUIDE_001")

    def test_the_resources_carry_the_same_text(self):
        app = server.create_server("stateless")
        uris = [str(getattr(item, "uri", item)) for item in asyncio.run(app.list_resources())]
        self.assertIn(WORKFLOW_URI, uris)
        self.assertIn(ASSEMBLE_URI, uris)

        async def read(uri):
            contents = await app.read_resource(uri)
            item = contents[0]
            text = getattr(item, "text", None)
            if text is None:
                content = item.content
                text = getattr(content, "text", content)
            return text

        workflow = asyncio.run(read(WORKFLOW_URI))
        self.assertEqual(workflow, skillpack.read_text("SKILL.md"))
        assemble = asyncio.run(read(ASSEMBLE_URI))
        self.assertEqual(assemble, skillpack.read_text("references/assemble-site.md"))
        # the tool hands out the same text without the installer-only front matter
        self.assertEqual(service.guide()["content"], skillpack.split_frontmatter(workflow)[1])

    def test_the_prompt_and_the_instructions_point_at_the_guide(self):
        app = server.create_server("stateless")
        prompts = {getattr(item, "name", item): item for item in asyncio.run(app.list_prompts())}
        self.assertIn("gemarc-media-doc", prompts)
        text = asyncio.run(app.get_prompt("gemarc-media-doc", {"project": "demo"}))
        prompt_text = text.messages[0].content.text
        self.assertIn(GUIDE_TOOL, prompt_text)
        self.assertIn(WORKFLOW_URI, prompt_text)
        self.assertIn(GUIDE_TOOL, server.INSTRUCTIONS)
        self.assertIn(WORKFLOW_URI, server.INSTRUCTIONS)
        self.assertLessEqual(len(server.INSTRUCTIONS), 4096)

    def test_the_brief_names_the_guide(self):
        for stateless in (True, False):
            brief = service.brief(stateless=stateless)
            self.assertTrue(any(GUIDE_TOOL in step for step in brief["procedure"]))
            self.assertIn(GUIDE_TOOL, brief["alreadyDone"]["workflow"])


if __name__ == "__main__":
    unittest.main()
