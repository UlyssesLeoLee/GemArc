#!/usr/bin/env python3
"""Thin command-line MCP client for the GemArc Atlas tools: hand testing, and headless agents without MCP access.

It starts the stdio server (``<this python> -m gemarc_mcp``) the way a real client does, speaks MCP through the official SDK
and prints compact JSON on stdout. The exit status is 0 unless the call answered ``isError`` (or the session failed).

Subcommands::

    python scripts/atlas_mcp.py list-tools
    python scripts/atlas_mcp.py list-prompts
    python scripts/atlas_mcp.py get-prompt gemarc-atlas-create mode=asis project=demo "requirement=An invented lending desk."
    python scripts/atlas_mcp.py call gemarc_atlas_brief '{"section":"format"}'
    python scripts/atlas_mcp.py call gemarc_atlas_check '{"text":"#atlas 1\\n## nodes: id | name | sub | opts\\n"}'

The server environment is NOT inherited wholesale (the SDK passes a filtered environment, as Claude Code, Codex and Hermes do):
``GEMARC_*`` variables of this shell are forwarded, ``GEMARC_TOOLSET`` defaults to ``atlas``, ``PYTHONUTF8=1`` is set, and
``PYTHONPATH`` is the checkout's ``src`` directory when this script lives in a checkout. ``--env K=V`` adds or overrides one.
Writing tools need ``GEMARC_ALLOW_WRITE=1`` in the environment (or ``--allow-write``) and a workspace
(``GEMARC_WORKSPACE_ROOT``, default ``~/.gemarc/workspace``).

Output: ``list-tools`` prints ``[{name, title, description, annotations, inputSchema}]``; ``call`` prints the decoded JSON payload
of the tool (the text content is JSON); ``get-prompt`` prints the prompt text. Server log lines go to stderr.
Dependencies: the Python standard library and the official ``mcp`` package.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError:  # pragma: no cover - an environment problem, say so plainly
    sys.exit("The official 'mcp' package is required: pip install -e .   (or pip install mcp)")

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
TIMEOUT_S = 300


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def server_env(args) -> dict:
    """The explicit environment of the server process (see the module docstring)."""
    env = {key: value for key, value in os.environ.items() if key.startswith("GEMARC_")}
    env.setdefault("GEMARC_TOOLSET", "atlas")
    if args.toolset:
        env["GEMARC_TOOLSET"] = args.toolset
    if args.workspace:
        env["GEMARC_WORKSPACE_ROOT"] = str(Path(args.workspace).resolve())
    if args.allow_write:
        env["GEMARC_ALLOW_WRITE"] = "1"
    env["PYTHONUTF8"] = "1"
    if (REPO_SRC / "gemarc_mcp").is_dir():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_SRC), os.environ.get("PYTHONPATH", "")]))
    elif os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    for pair in args.env or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--env expects K=V, got {pair!r}")
        env[key] = value
    return env


def parse_pairs(items: list) -> dict:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"prompt arguments are k=v, got {item!r}")
        out[key] = value
    return out


def parse_json_object(text: str) -> dict:
    if text.startswith("@"):  # @file.json reads the arguments from a file
        text = Path(text[1:]).read_text(encoding="utf-8")
    try:
        value = json.loads(text) if text.strip() else {}
    except ValueError as ex:
        raise SystemExit(f"the tool arguments are not JSON: {ex}")
    if not isinstance(value, dict):
        raise SystemExit("the tool arguments must be a JSON object")
    return value


async def run(args) -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "gemarc_mcp"], env=server_env(args), cwd=args.cwd or None)
    async with stdio_client(params, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=TIMEOUT_S) as session:
            await session.initialize()
            if args.command == "list-tools":
                tools = (await session.list_tools()).tools
                print(compact([t.model_dump(by_alias=True, exclude_none=True) for t in tools]))
                return 0
            if args.command == "list-prompts":
                prompts = (await session.list_prompts()).prompts
                print(compact([p.model_dump(by_alias=True, exclude_none=True) for p in prompts]))
                return 0
            if args.command == "get-prompt":
                result = await session.get_prompt(args.name, parse_pairs(args.pairs))
                print("\n".join(m.content.text for m in result.messages if getattr(m.content, "type", "") == "text"))
                return 0
            result = await session.call_tool(args.name, parse_json_object(args.arguments))
            text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
            try:
                print(compact(json.loads(text)))
            except ValueError:
                print(text)
            return 1 if result.is_error else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--toolset", help="GEMARC_TOOLSET of the server (default: $GEMARC_TOOLSET or atlas)")
    parser.add_argument("--workspace", help="GEMARC_WORKSPACE_ROOT of the server")
    parser.add_argument("--allow-write", action="store_true", help="set GEMARC_ALLOW_WRITE=1 for the server")
    parser.add_argument("--env", action="append", metavar="K=V", help="extra server environment variable (repeatable)")
    parser.add_argument("--cwd", help="working directory of the server process")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-tools", help="print the tools/list answer as JSON")
    sub.add_parser("list-prompts", help="print the prompts/list answer as JSON")
    prompt = sub.add_parser("get-prompt", help="print the text of a prompt")
    prompt.add_argument("name")
    prompt.add_argument("pairs", nargs="*", metavar="k=v")
    call = sub.add_parser("call", help="call a tool; exit status 1 when it answers isError")
    call.add_argument("name")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object, or @file.json (default: {})")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Chinese replies must not depend on the console code page
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        return asyncio.run(asyncio.wait_for(run(args), TIMEOUT_S))
    except (OSError, asyncio.TimeoutError, RuntimeError, ValueError) as ex:
        print(compact({"status": "error", "message": f"{type(ex).__name__}: {ex}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
