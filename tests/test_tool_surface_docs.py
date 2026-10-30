"""The documented tool surface must match the registered one.

Checks (see docs/03_详细设计/02_MCP工具详细契约.md and docs/05_项目管理/04_配置变更与ADR.md, ADR-008):

* every registered tool is documented, and the annotation table is the live one;
* no legacy dotted tool name survives outside the ADR that records the rename;
* every example config under integration-examples/ parses and carries no literal secret;
* the skill is a valid agentskills.io skill;
* every tool name cited by the skill and the integration docs exists.
"""
import asyncio
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import server  # noqa: E402

CONTRACT_DOC = REPO_ROOT / "docs" / "03_详细设计" / "02_MCP工具详细契约.md"
ADR_DOC = REPO_ROOT / "docs" / "05_项目管理" / "04_配置变更与ADR.md"
EXAMPLES = REPO_ROOT / "integration-examples"
SKILL_DIR = REPO_ROOT / "skills" / "gemarc-5d-design"

LEGACY_NAME = re.compile(
    r"gemarc\.(?:inspect|plan|assets|contracts|brief|scaffold|init|model|validate|build|"
    r"simulate|verify|diff|export)\b(?!\.[A-Za-z])")
TOOL_TOKEN = re.compile(r"(?<![A-Za-z0-9])gemarc_[a-z][a-z0-9_]*")
#: ``gemarc_`` words that are not tool names, each with the reason it is legitimate.
NOT_TOOLS = {
    "gemarc_mcp": "the Python package / module (python -m gemarc_mcp)",
    "gemarc_local": "the server name of the stdio entry in the Claude Code .mcp.json example",
}
#: Placeholders the integration examples may use in their config files (listed in integration-examples/README.md).
PLACEHOLDERS = {"<project>", "<workspace>", "<token>"}
TEXT_SUFFIXES = {".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".py", ".js", ".mjs", ".css", ".html", ".mmd", ".csv", ".example"}


def _sdk_available() -> bool:
    try:
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:
        return False


def _registered(toolset: str):
    """(names, tools). REGISTERED_TOOLS is module state overwritten by every create_server call."""
    app = server.create_server(toolset)
    names = list(server.REGISTERED_TOOLS)
    return names, asyncio.run(app.list_tools())


def _text(path: Path) -> str:
    """UTF-8 text with LF line endings (many repo files are CRLF)."""
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _files(root: Path, *, suffixes=None):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        if suffixes is not None and path.suffix not in suffixes and path.name not in {".mcp.json"}:
            continue
        yield path


def _without_adr_008(text: str) -> str:
    """The ADR document minus the ADR-008 section, the only place the old names may be listed."""
    start = re.search(r"^### ADR-008\b", text, flags=re.M)
    if not start:
        return text
    end = re.search(r"^### ADR-009\b", text[start.end():], flags=re.M)
    stop = start.end() + end.start() if end else len(text)
    return text[:start.start()] + text[stop:]


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class RegisteredToolsAreDocumentedTests(unittest.TestCase):
    def test_every_registered_tool_is_in_the_contract_document(self):
        names, _ = _registered("full")
        self.assertEqual(len(names), 16)
        text = _text(CONTRACT_DOC)
        missing = [name for name in names if f"`{name}`" not in text]
        self.assertEqual(missing, [], f"not documented in {CONTRACT_DOC.name}")

    def test_documented_tools_all_exist(self):
        names, _ = _registered("full")
        documented = set(re.findall(r"^\| `(gemarc_[a-z0-9_]+)` \|", _text(CONTRACT_DOC), flags=re.M))
        self.assertTrue(documented, "no tool rows found in the contract document")
        self.assertEqual(sorted(documented - set(names)), [])

    def test_annotation_table_is_the_live_one(self):
        _, tools = _registered("full")
        text = _text(CONTRACT_DOC)

        def cell(value):
            return "—" if value is None else ("true" if value else "false")

        for tool in tools:
            a = tool.annotations
            row = re.search(rf"^\| `{tool.name}` \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \|$",
                            text, flags=re.M)
            self.assertIsNotNone(row, f"{tool.name}: no annotation row in the contract document")
            title, read_only, destructive, idempotent, open_world, _ = (c.strip() for c in row.groups())
            self.assertEqual(
                (title, read_only, destructive, idempotent, open_world),
                (tool.title, cell(a.read_only_hint), cell(a.destructive_hint), cell(a.idempotent_hint),
                 cell(a.open_world_hint)), f"{tool.name}: documented annotations differ from the live server")

    def test_stateless_column_matches_the_stateless_toolset(self):
        stateless_names, _ = _registered("stateless")
        self.assertEqual(len(stateless_names), 8)
        text = _text(CONTRACT_DOC)
        for name in stateless_names:
            self.assertRegex(text, re.compile(rf"^\| `{name}` \| S \|", re.M),
                             msg=f"{name} must be marked S in the tool table")


class LegacyNamesTests(unittest.TestCase):
    def test_no_dotted_tool_name_remains(self):
        offenders = []
        targets = [REPO_ROOT / "README.md"]
        for root in (REPO_ROOT / "docs", EXAMPLES, SKILL_DIR, REPO_ROOT / "src"):
            targets += [p for p in _files(root, suffixes=TEXT_SUFFIXES)]
        for path in targets:
            if path.name == "registry.json":  # its gemarc.scaffold* ids are component ids, not tool names
                continue
            if not path.is_file():
                continue
            text = _text(path)
            if path == ADR_DOC:
                text = _without_adr_008(text)
            for match in LEGACY_NAME.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}: {match.group(0)}")
        self.assertEqual(offenders, [])

    def test_adr_008_records_the_rename(self):
        text = _text(ADR_DOC)
        self.assertRegex(text, re.compile(r"^### ADR-008\b", re.M), "the ADR that records the rename is missing")
        self.assertTrue(LEGACY_NAME.search(text), "ADR-008 should list the old names it replaced")
        self.assertFalse(LEGACY_NAME.search(_without_adr_008(text)), "old names leaked outside ADR-008")
        for number in ("ADR-009", "ADR-010", "ADR-011"):
            self.assertRegex(text, re.compile(rf"^### {number}\b", re.M))


class IntegrationExamplesTests(unittest.TestCase):
    def test_json_parses(self):
        found = [p for p in _files(EXAMPLES) if p.suffix == ".json"]
        self.assertGreaterEqual(len(found), 3)
        for path in found:
            with self.subTest(path.relative_to(EXAMPLES).as_posix()):
                json.loads(_text(path))

    def test_toml_parses(self):
        found = [p for p in _files(EXAMPLES) if p.suffix == ".toml"]
        self.assertGreaterEqual(len(found), 2)
        for path in found:
            with self.subTest(path.relative_to(EXAMPLES).as_posix()):
                tomllib.loads(_text(path))

    def test_yaml_parses_when_pyyaml_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        found = [p for p in _files(EXAMPLES) if p.suffix in (".yaml", ".yml")]
        self.assertGreaterEqual(len(found), 2)
        for path in found:
            with self.subTest(path.relative_to(EXAMPLES).as_posix()):
                data = yaml.safe_load(_text(path))
                self.assertIn("gemarc", data["mcp_servers"])

    def test_config_files_use_only_documented_placeholders(self):
        for path in _files(EXAMPLES):
            if path.suffix in (".md", ".py") or path.name == "codex-marketplace.json":
                continue
            with self.subTest(path.relative_to(EXAMPLES).as_posix()):
                used = set(re.findall(r"<[a-z][a-z-]*>", _text(path)))
                self.assertLessEqual(used, PLACEHOLDERS)
        readme = _text(EXAMPLES / "README.md")
        for placeholder in PLACEHOLDERS:
            self.assertIn(f"`{placeholder}`", readme, f"{placeholder} is not listed in integration-examples/README.md")

    def test_no_literal_secret(self):
        literal_bearer = re.compile(r"Bearer\s+(?!\$\{|<)[A-Za-z0-9._~+/-]{6,}")
        for path in _files(EXAMPLES):
            with self.subTest(path.relative_to(EXAMPLES).as_posix()):
                text = _text(path)
                self.assertIsNone(literal_bearer.search(text))
                self.assertIsNone(re.search(r"\bsk-[A-Za-z0-9]{8,}", text))

    def test_local_stdio_examples_set_the_documented_env(self):
        data = json.loads(_text(EXAMPLES / "claude-code" / ".mcp.json"))
        self.assertEqual(data["mcpServers"]["gemarc"]["type"], "http")  # a url entry without a type is a config error
        local_env = data["mcpServers"]["gemarc_local"]["env"]
        for key in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE", "PYTHONUTF8"):
            self.assertIn(key, local_env)
        desktop = json.loads(_text(EXAMPLES / "claude-desktop" / "claude_desktop_config.json"))
        for key in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE", "PYTHONUTF8"):
            self.assertIn(key, desktop["mcpServers"]["gemarc"]["env"])
        stdio = tomllib.loads(_text(EXAMPLES / "codex" / "config.stdio.toml"))["mcp_servers"]["gemarc"]
        for key in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE", "PYTHONUTF8"):
            self.assertIn(key, stdio["env"])
        self.assertIn("startup_timeout_sec", stdio)
        self.assertIn("tool_timeout_sec", stdio)
        remote = tomllib.loads(_text(EXAMPLES / "codex" / "config.remote.toml"))["mcp_servers"]["gemarc"]
        self.assertEqual(remote["bearer_token_env_var"], "GEMARC_MCP_TOKEN")
        self.assertNotIn("bearer_token", remote)  # an inline token is rejected by Codex


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.text = _text(SKILL_DIR / "SKILL.md")
        match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", self.text, flags=re.S)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        self.front = match.group(1)
        self.body = self.text[match.end():]

    def _field(self, key):
        match = re.search(rf"^{key}:\s*(.+)$", self.front, flags=re.M)
        self.assertIsNotNone(match, f"frontmatter lacks {key}")
        return match.group(1).strip()

    def test_name_equals_directory_name(self):
        name = self._field("name")
        self.assertEqual(name, SKILL_DIR.name)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)

    def test_description_states_when_to_use_and_fits(self):
        description = self._field("description")
        self.assertLessEqual(len(description), 1024)
        self.assertTrue(description.lower().startswith("use when"))
        self.assertGreater(len(description), 100)

    def test_body_is_short_and_references_exist(self):
        self.assertLess(len(self.text.splitlines()), 500)
        for target in re.findall(r"\]\((references/[^)#]+)\)", self.body):
            self.assertTrue((SKILL_DIR / target).is_file(), f"{target} is linked but missing")
        for path in (SKILL_DIR / "references").glob("*.md"):
            self.assertLess(len(_text(path).splitlines()), 500)


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class CitedToolNamesExistTests(unittest.TestCase):
    def test_cited_names_are_registered_tools(self):
        names, _ = _registered("full")
        known = set(names) | set(NOT_TOOLS)
        unknown = []
        for root in (SKILL_DIR, EXAMPLES):
            for path in _files(root):
                if path.suffix in (".py",):
                    continue
                text = _text(path)
                for match in TOOL_TOKEN.finditer(text):
                    if text[match.end():match.end() + 1] == "*":  # a glob such as gemarc_valid*
                        continue
                    # mcp__gemarc_local__gemarc_inspect is one token: check each gemarc_ part on its own
                    for part in match.group(0).split("__"):
                        if part.startswith("gemarc_") and part not in known:
                            unknown.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {part}")
        self.assertEqual(unknown, [])

    def test_skill_cites_every_stateless_tool(self):
        names, _ = _registered("stateless")
        text = _text(SKILL_DIR / "SKILL.md")
        self.assertEqual([n for n in names if n not in text], [])

    def test_vocabulary_section_cited_by_the_skill_is_valid(self):
        from gemarc_mcp import service
        text = _text(SKILL_DIR / "SKILL.md")
        self.assertIn('gemarc_brief(section="vocabulary")', text)
        result = service.brief(section="vocabulary", stateless=True)
        self.assertEqual(result["status"], "ok")
        for section in re.findall(r'section="([a-zA-Z]+)"', text):
            self.assertEqual(service.brief(section=section)["status"], "ok", section)


if __name__ == "__main__":
    unittest.main()
