#!/usr/bin/env python3
"""Launch the GemArc MCP server over stdio the way a real client does, then exercise it.

What "the way a client does" means here:

* the server is a child process started from ``--command`` (default
  ``<this python> -m gemarc_mcp``) with an explicit ``env`` block merged over the
  SDK's *filtered* default environment (``PATH`` and a few Windows basics only;
  no ``PYTHONPATH``, no inherited ``GEMARC_*``) - the same thing Claude Code,
  Codex and Hermes do;
* the working directory is a fresh empty directory, so a pass proves the server
  does not depend on the repository being the cwd (it must be ``pip install``-ed);
* everything is spoken through the official ``mcp`` client library.

Exit status is 0 only when every check passed.

Examples::

    python scripts/verify_stdio.py                          # full toolset, read-only
    python scripts/verify_stdio.py --toolset stateless      # the 8 remote tools
    python scripts/verify_stdio.py --allow-write            # also init/build/verify/export
    python scripts/verify_stdio.py --no-utf8 --allow-write  # without PYTHONUTF8=1
    python scripts/verify_stdio.py --command gemarc-mcp     # the console script
    python scripts/verify_stdio.py --prove-detector         # the stdout-purity check fires
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError:  # pragma: no cover - environment problem, say so plainly
    sys.exit("The official 'mcp' package is required: pip install -e .   (or pip install mcp)")

FUNCTION_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVER_ALIAS = "gemarc"                  # the name a client config gives the server
CLAUDE_PREFIX = f"mcp__{SERVER_ALIAS}__"  # the longest prefix any of the three clients adds
CLAUDE_TEXT_LIMIT = 4096                  # Claude Code truncates instructions/descriptions here
TIMEOUT_S = 180

STATELESS_TOOLS = {"gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model",
                   "gemarc_validate", "gemarc_simulate", "gemarc_diff", "gemarc_render"}
WRITE_TOOLS = {"gemarc_scaffold", "gemarc_init", "gemarc_model", "gemarc_build", "gemarc_export"}
FULL_TOOLS = STATELESS_TOOLS | WRITE_TOOLS | {"gemarc_inspect", "gemarc_plan", "gemarc_verify"}

# Same shape as examples/demo-site/data/architecture.json (illustrative, all Hypothetical).
DEMO_MODEL = {
    "meta": {"system": "demo", "schemaVersion": "0.1.0", "version": "0.1.0", "language": "zh-CN",
             "evidenceState": "Hypothetical", "description": "示例模型，非真实工程事实"},
    "nodes": [
        {"id": "sensor", "kind": "Component", "name": "事件源", "layer": "设备层", "verification": "Hypothetical"},
        {"id": "queue", "kind": "Component", "name": "消息队列", "layer": "平台层", "verification": "Hypothetical"},
        {"id": "rule", "kind": "Component", "name": "规则引擎", "layer": "平台层", "verification": "Hypothetical"},
        {"id": "notify", "kind": "Component", "name": "通知服务", "layer": "应用层", "verification": "Hypothetical"},
    ],
    "edges": [
        {"id": "e1", "sourceId": "sensor", "targetId": "queue", "type": "sends", "verification": "Hypothetical"},
        {"id": "e2", "sourceId": "queue", "targetId": "rule", "type": "sends", "verification": "Hypothetical"},
        {"id": "e3", "sourceId": "rule", "targetId": "notify", "type": "sends", "verification": "Hypothetical"},
    ],
    "views": [{"id": "logical", "title": "逻辑架构", "diagramType": "logical-view",
               "nodeIds": ["sensor", "queue", "rule", "notify"], "edgeKinds": ["sends"],
               "stakeholders": ["架构师"], "concerns": ["事件依赖"], "reviewIntent": "识别单点故障"}],
    "scenarios": [{"id": "normal", "name": "示例：正常事件传播", "evidenceClass": "hypothetical",
                   "events": [{"event": "emit", "module": "sensor", "timeMs": 0}],
                   "rules": [{"source": "sensor", "target": "queue", "whenEvent": "emit", "emit": "ingest", "delayMs": 10},
                             {"source": "queue", "target": "rule", "whenEvent": "ingest", "emit": "evaluate", "delayMs": 20},
                             {"source": "rule", "target": "notify", "whenEvent": "evaluate", "emit": "send", "delayMs": 10}]}],
    "evidence": [], "cases": [], "workflows": [], "prototypes": [], "space": None,
}


class Report:
    def __init__(self):
        self.failed = 0
        self.total = 0

    def check(self, name: str, ok: bool, detail: str = ""):
        self.total += 1
        self.failed += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""), flush=True)
        return ok

    def info(self, text: str):
        print(f"INFO  {text}", flush=True)


class Caller:
    """Thin wrapper that turns a tool call into (CallToolResult, decoded JSON payload)."""

    def __init__(self, session, report: Report):
        self.session, self.report = session, report

    async def call(self, name: str, **arguments):
        from mcp.types import CallToolResult
        result = await self.session.call_tool(name, arguments)
        if not isinstance(result, CallToolResult):
            raise AssertionError(f"{name}: unexpected result type {type(result).__name__}")
        text = result.content[0].text if result.content else ""
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"_raw": text}
        return result, payload, len(text.encode("utf-8"))


def diagnostic_ids(payload: dict) -> list[str]:
    """Refusals carry diagnostics[], validation failures carry errors[]/warnings[]."""
    found = []
    for key in ("diagnostics", "errors", "warnings"):
        found += [item.get("id") for item in payload.get(key, []) if isinstance(item, dict)]
    return found


def build_parameters(args, workspace: Path, cwd: Path) -> StdioServerParameters:
    env = {"GEMARC_TOOLSET": args.toolset, "GEMARC_WORKSPACE_ROOT": str(workspace)}
    if args.allow_write:
        env["GEMARC_ALLOW_WRITE"] = "1"
    if not args.no_utf8:
        env["PYTHONUTF8"] = "1"
    for pair in args.env or []:
        key, _, value = pair.partition("=")
        env[key] = value
    if args.command:
        command, extra = args.command[0], args.command[1:]
    else:
        command, extra = sys.executable, ["-m", "gemarc_mcp"]
    return StdioServerParameters(command=command, args=extra, env=env, cwd=str(cwd))


async def run_checks(session, init, report: Report, args, workspace: Path):
    call = Caller(session, report).call
    full = args.toolset == "full"

    # ---- initialize -----------------------------------------------------------------
    info = init.server_info
    report.check("initialize", bool(info and info.name),
                 f"server={info.name!r} version={info.version!r} protocol={init.protocol_version}")
    text = init.instructions or ""
    report.check("instructions present and within Claude's 4096-char limit",
                 0 < len(text) <= CLAUDE_TEXT_LIMIT, f"{len(text)} chars")
    report.check("instructions: Chinese text survived the pipe (UTF-8 round trip)",
                 "只使用已授权工作区" in text)

    # ---- tools/list -----------------------------------------------------------------
    tools = (await session.list_tools()).tools
    names = [tool.name for tool in tools]
    expected = FULL_TOOLS if full else STATELESS_TOOLS
    report.check(f"tools/list count == {len(expected)} for toolset={args.toolset}",
                 len(tools) == len(expected), f"got {len(tools)}")
    report.check("tool names are exactly the expected set", set(names) == expected,
                 "" if set(names) == expected else f"missing={sorted(expected - set(names))} extra={sorted(set(names) - expected)}")
    report.check("tool names unique", len(set(names)) == len(names))
    bad = [n for n in names if not FUNCTION_NAME.match(n)]
    report.check("every name matches ^[a-zA-Z0-9_-]{1,64}$ (Claude API / OpenAI function-name charset)",
                 not bad, str(bad))
    too_long = [n for n in names if len(CLAUDE_PREFIX + n) > 64]
    report.check(f"'{CLAUDE_PREFIX}'+name <= 64 chars", not too_long,
                 f"longest={max(len(CLAUDE_PREFIX + n) for n in names)}")
    report.check("every tool has a title", all(tool.title for tool in tools))
    report.check("every tool has annotations with readOnlyHint and openWorldHint",
                 all(t.annotations is not None and t.annotations.read_only_hint is not None
                     and t.annotations.open_world_hint is not None for t in tools))
    read_only = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
    report.info(f"readOnlyHint=true: {len(read_only)} tools; writers: {sorted(set(names) - read_only)}")
    report.check("write tools (and only those) are not readOnly",
                 (set(names) - read_only) == (WRITE_TOOLS if full else set()))
    report.check("descriptions within 4096 chars",
                 all(len(t.description or "") <= CLAUDE_TEXT_LIMIT for t in tools))
    report.check("no 'null' branches in input schemas (Hermes/Codex sanitise them)",
                 all('"null"' not in json.dumps(t.input_schema) for t in tools))

    # ---- stateless tools --------------------------------------------------------------
    result, payload, size = await call("gemarc_new_model", system="demo")
    report.check("gemarc_new_model -> ok, not isError, has model + slots",
                 not result.is_error and payload.get("status") == "ok"
                 and isinstance(payload.get("model"), dict) and payload.get("slots"), f"{size} bytes")
    report.check("no structuredContent (Codex would hide `content` if present)",
                 result.structured_content is None)

    result, payload, _ = await call("gemarc_validate", model=DEMO_MODEL)
    report.check("gemarc_validate(model) -> ok", not result.is_error and payload.get("status") == "ok"
                 and bool(payload.get("modelSha256")), f"sha={str(payload.get('modelSha256'))[:12]}")

    result, payload, _ = await call("gemarc_validate", model=DEMO_MODEL, strict=True)
    ids = diagnostic_ids(payload)
    report.check("gemarc_validate(model, strict=true) on an all-Hypothetical model -> isError=true, "
                 "status=invalid, errors[].id present",
                 bool(result.is_error) and payload.get("status") == "invalid" and "GEMARC_EVIDENCE_001" in ids,
                 f"{len(payload.get('errors', []))} errors, {len(payload.get('warnings', []))} warnings")

    result, payload, _ = await call("gemarc_validate")
    report.check("gemarc_validate() with neither project nor model -> isError, GEMARC_INPUT_003",
                 bool(result.is_error) and "GEMARC_INPUT_003" in diagnostic_ids(payload))
    result, payload, _ = await call("gemarc_validate", project="x", model=DEMO_MODEL)
    report.check("gemarc_validate(project AND model) -> isError, GEMARC_INPUT_003",
                 bool(result.is_error) and "GEMARC_INPUT_003" in diagnostic_ids(payload))

    result, payload, _ = await call("gemarc_simulate", scenario_id="normal", model=DEMO_MODEL,
                                    compare_baseline=True, disabled_nodes=["queue"])
    report.check("gemarc_simulate(model, compare_baseline, disabled_nodes) -> ok",
                 not result.is_error and payload.get("status") == "ok", f"keys={sorted(payload)[:6]}")

    result, payload, size = await call("gemarc_diff", base_model=DEMO_MODEL, target_model=DEMO_MODEL)
    report.check("gemarc_diff(model, model) -> ok", not result.is_error and payload.get("status") == "ok")

    result, payload, size = await call("gemarc_render", model=DEMO_MODEL)
    files = payload.get("files", [])
    parsed = all(json.loads(f["content"]) is not None for f in files) if payload.get("inline") else False
    report.check("gemarc_render(model) -> ok, 3 data files inline and parseable, kit.path /kit/static.zip",
                 not result.is_error and payload.get("status") == "ok" and len(files) == 3 and parsed
                 and payload.get("kit", {}).get("path") == "/kit/static.zip",
                 f"payload {size} bytes; kit.url={payload.get('kit', {}).get('url')!r}")
    result, payload, _ = await call("gemarc_render", model=DEMO_MODEL, profile="nope")
    report.check("gemarc_render(profile='nope') -> isError, GEMARC_BUILD_005",
                 bool(result.is_error) and "GEMARC_BUILD_005" in diagnostic_ids(payload))

    result, payload, size = await call("gemarc_brief", section="vocabulary")
    report.check("gemarc_brief(section='vocabulary') -> ok", not result.is_error and payload.get("status") == "ok",
                 f"{size} bytes")
    result, payload, size_all = await call("gemarc_brief")
    report.check("gemarc_brief() -> ok; Chinese text survived", not result.is_error and "用固定的架构模型" in payload.get("purpose", ""),
                 f"{size_all} bytes")
    result, payload, _ = await call("gemarc_brief", section="nope")
    report.check("gemarc_brief(section='nope') -> isError, GEMARC_BRIEF_001",
                 bool(result.is_error) and "GEMARC_BRIEF_001" in diagnostic_ids(payload))
    result, payload, size = await call("gemarc_contracts")
    report.check("gemarc_contracts() -> ok", not result.is_error and payload.get("status") == "ok", f"{size} bytes")
    result, payload, _ = await call("gemarc_assets", query="scaffold")
    report.check("gemarc_assets(query) -> ok", not result.is_error and payload.get("status") == "ok")

    # ---- resources and prompt -----------------------------------------------------------
    uris = sorted(str(r.uri) for r in (await session.list_resources()).resources)
    report.check("resources/list == [gemarc://registry, gemarc://schema/model]",
                 uris == ["gemarc://registry", "gemarc://schema/model"], str(uris))
    registry = await session.read_resource("gemarc://registry")
    report.check("resources/read gemarc://registry parses as JSON", bool(json.loads(registry.contents[0].text)))
    prompts = (await session.list_prompts()).prompts
    report.check("prompts/list == [gemarc-design]", [p.name for p in prompts] == ["gemarc-design"])
    prompt = await session.get_prompt("gemarc-design", {"project": "demo"})
    prompt_text = prompt.messages[0].content.text
    cited = set(re.findall(r"(?<![A-Za-z0-9])gemarc_[a-z][a-z0-9_]*", prompt_text))
    report.check("prompt only cites tools that exist in this toolset", cited <= set(names),
                 f"unknown={sorted(cited - set(names))}")

    # ---- tools that only exist in the stateless set / only in the full set -------------------
    if not full:
        try:
            result = await session.call_tool("gemarc_build", {"project": "x"})
            refused = bool(getattr(result, "is_error", False))
            detail = "isError result"
        except Exception as ex:  # noqa: BLE001 - a protocol error is an acceptable refusal
            refused, detail = True, f"protocol error {type(ex).__name__}"
        report.check("stateless set refuses gemarc_build (tool not registered)", refused, detail)
        return

    # ---- full set: workspace tools ----------------------------------------------------------
    project = f"verify-{uuid.uuid4().hex[:8]}"
    result, payload, _ = await call("gemarc_inspect", path="..")
    report.check("gemarc_inspect('..') is refused (outside the workspace) -> GEMARC_AUTH_001",
                 bool(result.is_error) and "GEMARC_AUTH_001" in diagnostic_ids(payload))
    result, payload, _ = await call("gemarc_init", project=project)
    if not args.allow_write:
        report.check("gemarc_init without GEMARC_ALLOW_WRITE=1 -> isError, GEMARC_AUTH_002",
                     bool(result.is_error) and "GEMARC_AUTH_002" in diagnostic_ids(payload))
        return
    report.check("gemarc_init(project) with GEMARC_ALLOW_WRITE=1 -> ok",
                 not result.is_error and payload.get("status") == "ok", str(payload)[:100])
    result, payload, _ = await call("gemarc_init", project=project)
    report.check("gemarc_init again refuses to overwrite", bool(result.is_error))
    result, payload, _ = await call("gemarc_validate", project=project)
    sha = payload.get("modelSha256")
    report.check("gemarc_validate(project) -> ok with modelSha256", not result.is_error and bool(sha))
    result, payload, _ = await call("gemarc_plan", project=project)
    report.check("gemarc_plan(project) -> ok", not result.is_error and payload.get("status") == "ok")
    result, payload, _ = await call("gemarc_inspect", path=project)
    report.check("gemarc_inspect(project dir inside workspace) -> ok", not result.is_error and payload.get("status") == "ok")
    result, payload, _ = await call("gemarc_simulate", project=project, scenario_id="normal")
    report.check("gemarc_simulate(project) -> ok", not result.is_error and payload.get("status") == "ok")
    result, payload, _ = await call("gemarc_diff", base_project=project, target_project=project)
    report.check("gemarc_diff(project, project) -> ok", not result.is_error and payload.get("status") == "ok")
    result, payload, _ = await call("gemarc_model", project=project, replacement_model=DEMO_MODEL,
                                    expected_sha256="0" * 64)
    report.check("gemarc_model with a stale expected_sha256 -> isError (optimistic concurrency)",
                 bool(result.is_error))
    result, payload, _ = await call("gemarc_model", project=project, replacement_model=DEMO_MODEL,
                                    expected_sha256=sha or "")
    report.check("gemarc_model with the current sha256 -> ok", not result.is_error and payload.get("status") == "ok",
                 str(payload)[:100])
    result, payload, _ = await call("gemarc_build", project=project, profile="static")
    report.check("gemarc_build(profile=static) -> ok", not result.is_error and payload.get("status") == "ok",
                 str(payload)[:100])
    result, payload, _ = await call("gemarc_verify", project=project)
    report.check("gemarc_verify -> ok", not result.is_error and payload.get("status") == "ok", str(payload)[:100])
    result, payload, _ = await call("gemarc_export", project=project)
    report.check("gemarc_export -> ok", not result.is_error and payload.get("status") == "ok", str(payload)[:100])
    scaffolded = f"{project}-s"
    result, payload, _ = await call("gemarc_scaffold", project=scaffolded, profile="static")
    report.check("gemarc_scaffold(project, static) -> ok", not result.is_error and payload.get("status") == "ok",
                 str(payload)[:100])
    for leftover in (project, scaffolded):
        shutil.rmtree(workspace / leftover, ignore_errors=True)


class StdoutNoise(logging.Handler):
    """Records every stdout line the SDK's stdio transport could not parse as JSON-RPC.

    In SDK 2.3.0 such a line is logged at ERROR by ``mcp.client.stdio`` and then dropped
    (it is *not* delivered to ``message_handler``), so the logger is the one place to see it.
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord):
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            found = re.search(r"input_value=(.{0,60})", str(record.exc_info[1]))
            text += f" ({found.group(1)})" if found else ""
        self.lines.append(text)


async def session_for(args, report: Report, body):
    """Open the server like a client would; hand `body` the live session and its recorded noise."""
    collector = StdoutNoise()
    transport_log = logging.getLogger("mcp.client.stdio")
    transport_log.addHandler(collector)
    transport_log.propagate = False  # keep the SDK's traceback out of the PASS/FAIL report
    noise = collector.lines

    own_workspace = args.workspace is None
    workspace = Path(tempfile.mkdtemp(prefix="gemarc-ws-")) if own_workspace else Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    cwd = Path(args.cwd) if args.cwd else Path(tempfile.mkdtemp(prefix="gemarc-cwd-"))
    params = build_parameters(args, workspace.resolve(), cwd.resolve())
    report.info(f"command={params.command} {' '.join(params.args)}")
    report.info(f"cwd={params.cwd}  toolset={args.toolset}  allow_write={args.allow_write}  "
                f"PYTHONUTF8={'1' if not args.no_utf8 else '(unset)'}  workspace={workspace}")
    errlog = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    try:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=60) as session:
                init = await session.initialize()
                await body(session, init, workspace)
        errlog.seek(0)
        stderr_text = errlog.read()
        return noise, stderr_text
    finally:
        transport_log.removeHandler(collector)
        errlog.close()
        if own_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
        if not args.cwd:
            shutil.rmtree(cwd, ignore_errors=True)


async def amain(args) -> int:
    report = Report()

    if args.prove_detector:
        # A server that prints one junk line before speaking MCP must trip the purity check.
        args.command = [sys.executable, "-c",
                        "print('junk on stdout'); import runpy; runpy.run_module('gemarc_mcp', run_name='__main__')"]

        async def body(session, init, workspace):
            report.check("initialize still works around the junk line", bool(init.server_info))

        noise, _ = await session_for(args, report, body)
        report.check("stdout-purity detector fires on a polluted server", len(noise) >= 1,
                     f"{len(noise)} non-JSON-RPC line(s) recorded")
        return 1 if report.failed else 0

    async def body(session, init, workspace):
        await run_checks(session, init, report, args, workspace)

    try:
        with_timeout = asyncio.wait_for(session_for(args, report, body), TIMEOUT_S)
        noise, stderr_text = await with_timeout
    except Exception as ex:  # noqa: BLE001
        report.check("session ran to completion", False, f"{type(ex).__name__}: {ex}")
        return 1
    report.check("stdout carried only MCP messages (0 non-JSON-RPC lines)", not noise, "; ".join(noise)[:200])
    report.info(f"server stderr: {len(stderr_text)} chars" + (f" -> {stderr_text.strip()[:160]!r}" if stderr_text.strip() else ""))
    print(f"\n{report.total - report.failed}/{report.total} checks passed", flush=True)
    return 1 if report.failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--toolset", choices=("full", "stateless"), default="full",
                        help="GEMARC_TOOLSET given to the server (default: full, the local default)")
    parser.add_argument("--workspace", help="existing directory used as GEMARC_WORKSPACE_ROOT "
                                            "(default: a throwaway temp directory)")
    parser.add_argument("--allow-write", action="store_true", help="set GEMARC_ALLOW_WRITE=1 and test the write tools")
    parser.add_argument("--no-utf8", action="store_true", help="do not put PYTHONUTF8=1 in the env block")
    parser.add_argument("--env", action="append", metavar="K=V", help="extra env var for the server (repeatable)")
    parser.add_argument("--cwd", help="working directory of the server (default: a fresh empty temp directory)")
    parser.add_argument("--prove-detector", action="store_true",
                        help="launch a server that pollutes stdout and require the purity check to FAIL it")
    parser.add_argument("--command", nargs=argparse.REMAINDER,
                        help="server command and its arguments (must be last); default: <python> -m gemarc_mcp")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
