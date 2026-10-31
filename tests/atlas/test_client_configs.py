"""The client side of the atlas toolset (milestone M8): the example configs for Claude Code (interactive and headless ``claude -p``), Codex and
Hermes, and the thin command-line client ``scripts/atlas_mcp.py``, tested for the 13-tool ``atlas`` toolset with ``GEMARC_ALLOW_WRITE=1`` and
``GEMARC_ATLAS_ROOTS``.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_client_configs*.py"

* JSON, TOML and YAML syntax of every example (YAML: PyYAML when it is installed, else a small strict parser of the subset the example uses, which is
  itself tested; the two agree whenever both exist);
* the headless settings list exactly the 13 registered atlas tools (as ``mcp__gemarc_atlas__<name>``) plus ``Read``, ``Grep``, ``Glob``, deny the writing
  tools, and the command line in the README carries the same list;
* a server is started from each config (placeholders replaced, the config's own command, arguments and environment, a filtered environment as the
  real clients give it) over real stdio: 13 tools, the entry prompt, a write into the workspace, an outline of a file of the evidence root;
* ``scripts/atlas_mcp.py``: ``list-tools``, ``list-prompts``, ``get-prompt``, ``call`` (JSON on the command line and ``@file``), the exit status 1 on
  ``isError``, the write gate, ``--toolset atlas_remote``.

Real clients (Claude Code, Codex, Hermes) are NOT started here; the claude command line was not run either. Synthetic data only.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "docs" / "atlas" / "examples"
SCRIPT = REPO / "scripts" / "atlas_mcp.py"
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _sdk() -> bool:
    try:
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------------------------------------------
# A small strict YAML parser for the subset the Hermes example uses (mappings by indentation, scalars, quoted strings, inline lists, comments)
# ---------------------------------------------------------------------------------------------------------------


class YamlSubsetError(ValueError):
    pass


def _scalar(raw: str, number: int):
    raw = raw.strip()
    if not raw:
        raise YamlSubsetError(f"line {number}: an empty value")
    if raw[0] == '"':
        end = _closing(raw, '"', number)
        return json.loads(raw[: end + 1])
    if raw[0] == "'":
        end = _closing(raw, "'", number)
        return raw[1:end].replace("''", "'")
    if raw[0] == "[":
        if not raw.endswith("]"):
            raise YamlSubsetError(f"line {number}: an inline list must end with ]")
        return [_scalar(part, number) for part in _split_list(raw[1:-1], number)]
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _closing(raw: str, quote: str, number: int) -> int:
    i = 1
    while i < len(raw):
        if raw[i] == "\\" and quote == '"':
            i += 2
            continue
        if raw[i] == quote:
            if quote == "'" and raw[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        i += 1
    raise YamlSubsetError(f"line {number}: unterminated {quote} string")


def _split_list(body: str, number: int) -> list:
    parts, depth, start, i, quote = [], 0, 0, 0, ""
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    if quote:
        raise YamlSubsetError(f"line {number}: unterminated string in a list")
    last = body[start:]
    if last.strip() or parts:
        parts.append(last)
    return [p for p in parts if p.strip()]


def _strip_comment(line: str) -> str:
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def parse_yaml_subset(source: str) -> dict:
    """Mappings nested by indentation (multiples of two spaces), ``key: value`` and ``key:``, scalars, quoted strings, inline lists, ``#`` comments."""
    root: dict = {}
    stack = [(-1, root)]
    for number, raw in enumerate(source.split("\n"), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise YamlSubsetError(f"line {number}: a tab in the indentation")
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            raise YamlSubsetError(f"line {number}: the indentation is not a multiple of two")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            raise YamlSubsetError(f"line {number}: the indentation goes back past the first level")
        body = line.strip()
        if body.endswith(":") and ": " not in body:
            key, sep, value = body[:-1], ":", ""
        elif ": " in body:
            key, sep, value = body.partition(": ")
        else:
            key, sep, value = body, "", None
        if not sep or not key or key != key.strip() or re.search(r"\s", key.strip("\"'")):
            raise YamlSubsetError(f"line {number}: not a 'key: value' line: {body[:40]!r}")
        key = key.strip().strip("\"'")
        parent = stack[-1][1]
        if key in parent:
            raise YamlSubsetError(f"line {number}: the key {key!r} is repeated")
        if value == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _scalar(value, number)
    return root


def substitute(value, repo: Path, workspace: Path):
    if isinstance(value, str):
        return value.replace("<repo>", str(repo)).replace("<workspace>", str(workspace))
    if isinstance(value, list):
        return [substitute(v, repo, workspace) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, repo, workspace) for k, v in value.items()}
    return value


def server_entries() -> dict:
    """The ``gemarc_atlas`` entry of every example config, as ``{command, args, env}``."""
    entries = {
        "claude-code": json.loads(text(EXAMPLES / "claude-code.mcp.json"))["mcpServers"]["gemarc_atlas"],
        "claude-headless": json.loads(text(EXAMPLES / "claude-headless.mcp.json"))["mcpServers"]["gemarc_atlas"],
        "codex": tomllib.loads(text(EXAMPLES / "codex.config.toml"))["mcp_servers"]["gemarc_atlas"],
        "hermes": parse_yaml_subset(text(EXAMPLES / "hermes.config.yaml"))["mcp_servers"]["gemarc_atlas"],
    }
    return entries


class SyntaxTests(unittest.TestCase):
    def test_json_toml_and_yaml_examples_parse(self):
        for name in ("claude-code.mcp.json", "claude-headless.mcp.json", "claude-headless.settings.json", "claude-headless-tobe.settings.json"):
            self.assertIsInstance(json.loads(text(EXAMPLES / name)), dict, name)
            self.assertNotIn(b"\r", (EXAMPLES / name).read_bytes(), name)
        self.assertIn("mcp_servers", tomllib.loads(text(EXAMPLES / "codex.config.toml")))
        self.assertIn("mcp_servers", parse_yaml_subset(text(EXAMPLES / "hermes.config.yaml")))

    def test_the_yaml_subset_parser_agrees_with_pyyaml_when_pyyaml_exists(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed: the subset parser alone checks the Hermes example (its own tests below prove that it can fail)")
        self.assertEqual(parse_yaml_subset(text(EXAMPLES / "hermes.config.yaml")), yaml.safe_load(text(EXAMPLES / "hermes.config.yaml")))

    def test_the_yaml_subset_parser_rejects_what_a_real_parser_rejects(self):
        good = parse_yaml_subset('a:\n  b: "x"\n  c: [1, "two"]\n  d: \'it\'\'s\'   # comment\n')
        self.assertEqual(good, {"a": {"b": "x", "c": [1, "two"], "d": "it's"}})
        for bad in ("a:\n b: 1\n", "a:\n\tb: 1\n", "a: 1\na: 2\n", "a: [1, 2\n", 'a: "x\n', "a\n", "a:\n  b: 1\n c: 2\n", "  a: 1\nb: 2\nc d: 3\n"):
            with self.assertRaises(YamlSubsetError, msg=bad):
                parse_yaml_subset(bad)

    def test_every_config_selects_the_atlas_toolset_with_write_and_roots(self):
        for name, entry in server_entries().items():
            env = entry["env"]
            self.assertEqual(env["GEMARC_TOOLSET"], "atlas", name)
            self.assertEqual(env["GEMARC_ALLOW_WRITE"], "1", name)
            self.assertTrue(env["GEMARC_ATLAS_ROOTS"].startswith("main="), name)
            self.assertEqual(env["PYTHONUTF8"], "1", name)
            self.assertEqual(list(entry["args"]), ["-m", "gemarc_mcp"], name)
            self.assertTrue(entry["command"].endswith(("python.exe", "python")), name)

    def test_placeholders_are_only_the_two_documented_ones_and_no_secret_is_written(self):
        for path in sorted(EXAMPLES.glob("*")):
            if path.suffix == ".md":
                continue
            body = text(path)
            self.assertLessEqual(set(re.findall(r"<[a-z][a-z-]*>", body)), {"<repo>", "<workspace>"}, path.name)
            self.assertIsNone(re.search(r"\bsk-[A-Za-z0-9]{8,}", body), path.name)
            self.assertIsNone(re.search(r"Bearer\s+(?!\$\{|<)[A-Za-z0-9._~+/-]{6,}", body), path.name)


class HeadlessTests(unittest.TestCase):
    def registered(self) -> list:
        from gemarc_mcp import atlas_tools

        return [f"mcp__gemarc_atlas__{name}" for name in atlas_tools.tool_names("atlas")]

    def test_the_settings_allow_exactly_the_atlas_tools_and_the_three_read_only_tools(self):
        settings = json.loads(text(EXAMPLES / "claude-headless.settings.json"))["permissions"]
        self.assertEqual(sorted(settings["allow"]), sorted([*self.registered(), "Read", "Grep", "Glob"]))
        self.assertEqual(len(self.registered()), 13)
        for writing in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
            self.assertIn(writing, settings["deny"])
            self.assertNotIn(writing, settings["allow"])
        for name in settings["allow"]:
            self.assertLessEqual(len(name), 64, name)

    def test_the_tobe_settings_add_only_a_write_rule_for_the_user_texts(self):
        """A to-be run saves the requirement text under ``atlas/user/``; the tools cannot, so the to-be settings allow ``Write`` there and nowhere else
        (and keep it out of ``deny``: a deny rule beats an allow rule, so a blanket deny would defeat the allowance)."""
        plain = json.loads(text(EXAMPLES / "claude-headless.settings.json"))["permissions"]
        tobe = json.loads(text(EXAMPLES / "claude-headless-tobe.settings.json"))["permissions"]
        extra = [rule for rule in tobe["allow"] if rule not in plain["allow"]]
        self.assertEqual(len(extra), 1, extra)
        self.assertRegex(extra[0], r"^Write\(//<workspace>/\*/atlas/user/\*\*\)$")
        self.assertEqual(sorted(rule for rule in plain["allow"] if rule not in tobe["allow"]), [])
        self.assertNotIn("Write", tobe["deny"])
        for still_denied in ("Bash", "Edit", "NotebookEdit", "WebFetch", "WebSearch"):
            self.assertIn(still_denied, tobe["deny"])
        self.assertEqual(sorted(set(plain["deny"]) - {"Write"}), sorted(tobe["deny"]))
        readme = text(EXAMPLES / "README.md")
        self.assertIn("claude-headless-tobe.settings.json", readme)
        self.assertIn("没有在真实客户端上试过", readme, "the README says that the path rule was not tried on a real client")

    def test_the_mcp_config_has_only_the_server_the_names_refer_to(self):
        config = json.loads(text(EXAMPLES / "claude-headless.mcp.json"))
        self.assertEqual(list(config), ["mcpServers"], "allowedTools is not a key of an --mcp-config file")
        self.assertEqual(list(config["mcpServers"]), ["gemarc_atlas"])
        self.assertEqual(config["mcpServers"]["gemarc_atlas"], json.loads(text(EXAMPLES / "claude-code.mcp.json"))["mcpServers"]["gemarc_atlas"])

    def test_the_readme_command_line_carries_the_same_allowed_tools_flag(self):
        readme = text(EXAMPLES / "README.md")
        found = re.search(r'--allowedTools "([^"]+)"', readme)
        self.assertIsNotNone(found, "the README shows the --allowedTools flag")
        listed = found.group(1).split(",")
        self.assertEqual(sorted(listed), sorted([*self.registered(), "Read", "Grep", "Glob"]))
        for flag in ("--mcp-config docs/atlas/examples/claude-headless.mcp.json", "--strict-mcp-config", "--settings docs/atlas/examples/claude-headless.settings.json",
                     "--output-format stream-json", "为这个库创建5D设计"):
            self.assertIn(flag, readme)
        self.assertIn("没有在真实的 `claude` 上跑过", readme, "the README says plainly that the command line was not run against a real client")


@unittest.skipUnless(_sdk(), "MCP SDK is not installed")
class ConfigServerTests(unittest.TestCase):
    """The server as each config starts it: the config's own command, arguments and environment, a filtered environment around them."""

    def run_entry(self, name: str, entry: dict):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        from gemarc_mcp import atlas_tools

        workspace = Path(tempfile.mkdtemp(prefix="atlas-cfg-"))
        self.addCleanup(shutil.rmtree, workspace, True)
        entry = substitute(entry, REPO, workspace)
        if not Path(entry["command"]).is_file():
            self.skipTest(f"{entry['command']} does not exist on this machine (the example is written for the repository's own .venv)")
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if key in os.environ}
        env.update(entry["env"])
        before = subprocess.run(["git", "-C", str(REPO), "ls-files", "-m", "-o", "--exclude-standard"], capture_output=True, text=True).stdout

        async def session():
            params = StdioServerParameters(command=entry["command"], args=list(entry["args"]), env=env, cwd=str(workspace))
            with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=120) as client:
                        init = await client.initialize()
                        tools = (await client.list_tools()).tools
                        prompts = [p.name for p in (await client.list_prompts()).prompts]
                        made = json.loads((await client.call_tool("gemarc_atlas_init", {"project": "cfg", "title": "Config test"})).content[0].text)
                        outline = json.loads((await client.call_tool("gemarc_atlas_outline", {"root": "main", "path": "src/gemarc_mcp/stateless.py"})).content[0].text)
                        refused = await client.call_tool("gemarc_atlas_outline", {"root": str(REPO), "path": "src/gemarc_mcp/stateless.py"})
                return init, tools, prompts, made, outline, refused

        init, tools, prompts, made, outline, refused = asyncio.run(session())
        self.assertEqual([t.name for t in tools], list(atlas_tools.tool_names("atlas")), name)
        self.assertEqual(len(tools), 13, name)
        self.assertEqual(prompts, ["gemarc-atlas-create"], name)
        self.assertEqual(made["status"], "created", f"{name}: GEMARC_ALLOW_WRITE=1 lets init write into the workspace: {made}")
        self.assertTrue((workspace / "cfg" / "atlas" / "atlas.toml").is_file(), name)
        self.assertEqual(outline["status"], "ok", f"{name}: GEMARC_ATLAS_ROOTS main=<repo> lets outline read the repository: {outline}")
        self.assertIn("def site_kit_zip", outline["text"], name)
        self.assertTrue(refused.is_error, f"{name}: a path where an alias is expected is refused")
        after = subprocess.run(["git", "-C", str(REPO), "ls-files", "-m", "-o", "--exclude-standard"], capture_output=True, text=True).stdout
        self.assertEqual(before, after, f"{name}: the repository (the evidence root) was only read")

    def test_each_example_config_starts_a_working_atlas_server(self):
        for name, entry in server_entries().items():
            with self.subTest(config=name):
                self.run_entry(name, entry)


class CommandLineClientTests(unittest.TestCase):
    """``scripts/atlas_mcp.py`` over real stdio."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="atlas-cli-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GEMARC_")}
        self.env.update(GEMARC_WORKSPACE_ROOT=str(self.work), GEMARC_ALLOW_WRITE="1", GEMARC_ATLAS_ROOTS=f"main={REPO}")

    def run_cli(self, *args, env=None):
        done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=240, env=env or self.env, encoding="utf-8", errors="replace")
        return done

    @unittest.skipUnless(_sdk(), "MCP SDK is not installed")
    def test_list_tools_prompts_and_get_prompt(self):
        from gemarc_mcp import atlas_tools

        tools = json.loads(self.run_cli("list-tools").stdout)
        self.assertEqual([t["name"] for t in tools], list(atlas_tools.tool_names("atlas")))
        self.assertEqual(len(tools), 13)
        self.assertLessEqual(len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 10 * 1024)
        self.assertEqual([p["name"] for p in json.loads(self.run_cli("list-prompts").stdout)], ["gemarc-atlas-create"])
        prompt = self.run_cli("get-prompt", "gemarc-atlas-create", "mode=asis", "project=demo", "requirement=An invented lending desk.")
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        for needle in ("gemarc_atlas_build", "gemarc_atlas_review", "oracle-sheet.md"):
            self.assertIn(needle, prompt.stdout)

    @unittest.skipUnless(_sdk(), "MCP SDK is not installed")
    def test_calls_write_read_and_report_errors_through_the_exit_status(self):
        made = self.run_cli("call", "gemarc_atlas_init", json.dumps({"project": "demo", "title": "Demo"}))
        self.assertEqual(made.returncode, 0, made.stderr)
        self.assertEqual(json.loads(made.stdout)["status"], "created")
        args = self.work / "put-args.json"
        args.write_text(json.dumps({"project": "demo", "slice": "00-space", "text": "#atlas 1\n## buildings: id | name | opts\nzone.a | Area | trust=internal\n"}), encoding="utf-8")
        put = self.run_cli("call", "gemarc_atlas_put", f"@{args}")
        self.assertEqual(put.returncode, 0, put.stdout + put.stderr)
        self.assertEqual(json.loads(put.stdout)["status"], "committed")
        status = self.run_cli("call", "gemarc_atlas_status", json.dumps({"project": "demo"}))
        self.assertEqual(json.loads(status.stdout)["view"], "next")
        outline = self.run_cli("call", "gemarc_atlas_outline", json.dumps({"root": "main", "path": "src/gemarc_mcp/stateless.py"}))
        self.assertEqual(outline.returncode, 0, outline.stdout + outline.stderr)
        self.assertIn("def site_kit_zip", json.loads(outline.stdout)["text"])
        refused = self.run_cli("call", "gemarc_atlas_outline", json.dumps({"root": str(REPO), "path": "x.py"}))
        self.assertEqual(refused.returncode, 1, "isError gives exit status 1")
        self.assertEqual(json.loads(refused.stdout)["status"], "invalid")
        self.assertNotEqual(self.run_cli("call", "gemarc_atlas_status", "{not json").returncode, 0)

    @unittest.skipUnless(_sdk(), "MCP SDK is not installed")
    def test_without_the_write_flag_writes_are_refused_and_reads_work(self):
        env = dict(self.env, GEMARC_ALLOW_WRITE="0")
        refused = self.run_cli("call", "gemarc_atlas_init", json.dumps({"project": "demo", "title": "Demo"}), env=env)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("GEMARC_AUTH_002", refused.stdout)
        self.assertFalse((self.work / "demo").exists())
        brief = self.run_cli("call", "gemarc_atlas_brief", json.dumps({"section": "format"}), env=env)
        self.assertEqual(brief.returncode, 0, brief.stderr)

    @unittest.skipUnless(_sdk(), "MCP SDK is not installed")
    def test_the_flag_options_set_the_server_environment(self):
        env = {k: v for k, v in self.env.items() if k not in ("GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT")}
        made = self.run_cli("--workspace", str(self.work), "--allow-write", "call", "gemarc_atlas_init", json.dumps({"project": "viaflag", "title": "Flag"}), env=env)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.assertTrue((self.work / "viaflag").is_dir())

    @unittest.skipUnless(_sdk(), "MCP SDK is not installed")
    def test_the_remote_toolset_has_two_tools(self):
        tools = json.loads(self.run_cli("--toolset", "atlas_remote", "list-tools").stdout)
        self.assertEqual([t["name"] for t in tools], ["gemarc_atlas_brief", "gemarc_atlas_check"])


if __name__ == "__main__":
    unittest.main()
