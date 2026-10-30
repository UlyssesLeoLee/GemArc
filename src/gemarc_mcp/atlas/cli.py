"""``python -m gemarc_mcp.atlas <command>``: the atlas tools from a shell (check, status, index, put, init, brief).

::

    python -m gemarc_mcp.atlas init <project> [--title T] [--mode asis|tobe] [--languages zh-CN,en-US] [--repos main,web]
    python -m gemarc_mcp.atlas put <project> <slice> (--file F | --text T | -) [--mode replace|patch] [--format atl|jsonl] [--base sha8]
    python -m gemarc_mcp.atlas check <project> [--slice S] [--mode slice|full] [--rule R] [--offset N] [--format text|json] [--emit-model]
    python -m gemarc_mcp.atlas check --text FILE|-            # inline text, advisory, no project
    python -m gemarc_mcp.atlas status <project> [--view next|report|dims|oracle|evidence] [--scope S]
    python -m gemarc_mcp.atlas index <project> [--scope nodes|room:ID|req:ID|case:ID|ids:PREFIX] [--with-edges] [--ids A,B] [--detail line|expanded] [--offset N]
    python -m gemarc_mcp.atlas review <project> [--action report|record] [--notes "fixed=RV-..; open=RV-.."]
    python -m gemarc_mcp.atlas brief [--section S] [--lang zh-CN|en-US]

The workspace root is ``GEMARC_WORKSPACE_ROOT`` (default ``~/.gemarc/workspace``; ``--root`` overrides it for one call); ``init``,
``put``, ``review --action record`` and ``check --emit-model`` write and need ``GEMARC_ALLOW_WRITE=1``; a read never creates the
default workspace. Output is the compact JSON reply of the tool (UTF-8); ``--human`` prints only the human text (diagnostics,
next lines, questions for the user, brief, index lines) and is about half the size, so a shell client should use it. Exit code 0 = ok / committed / created,
1 = a refused or invalid request, ERROR findings or a red draft, 2 = bad command line. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from ..paths import GemArcError
from . import project as pj
from . import review as review_mod
from . import tools


def _read(arg_file: "str | None", arg_text: "str | None", stdin_flag: bool) -> str:
    if arg_text is not None:
        return arg_text
    if arg_file == "-" or stdin_flag:
        try:
            return sys.stdin.buffer.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SystemExit("standard input is not UTF-8") from None
    if arg_file:
        try:
            with open(arg_file, "rb") as stream:
                return stream.read().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as ex:
            raise SystemExit(f"cannot read {arg_file}: {type(ex).__name__}") from None
    raise SystemExit("give the text with --file F, --text T or -")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m gemarc_mcp.atlas", description=__doc__.split("\n\n")[0])
    p.set_defaults(root="", human=False)
    common = argparse.ArgumentParser(add_help=False)   # --root / --human are accepted before and after the command
    common.add_argument("--root", default=argparse.SUPPRESS, help="workspace root (default: GEMARC_WORKSPACE_ROOT)")
    common.add_argument("--human", action="store_true", default=argparse.SUPPRESS, help="print only the human text of the reply")
    p.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p.add_argument("--human", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser
    sub.add_parser = lambda *a, **k: add(*a, parents=[common], **k)  # type: ignore[method-assign]
    s = sub.add_parser("init", help="create a project")
    s.add_argument("project")
    s.add_argument("--title", default="")
    s.add_argument("--mode", default="asis", choices=pj.MODES)
    s.add_argument("--languages", default="zh-CN")
    s.add_argument("--profile", default="software")
    s.add_argument("--repos", default="")
    s = sub.add_parser("put", help="validate and store a slice")
    s.add_argument("project")
    s.add_argument("slice")
    s.add_argument("stdin", nargs="?", choices=["-"], help="'-' reads the text from standard input")
    s.add_argument("--file")
    s.add_argument("--text")
    s.add_argument("--mode", default="replace", choices=["replace", "patch"])
    s.add_argument("--format", default="atl", choices=["atl", "jsonl"])
    s.add_argument("--base", default="")
    s = sub.add_parser("check", help="validate (never writes, except --emit-model)")
    s.add_argument("project", nargs="?", default="")
    s.add_argument("--text", help="inline slice text: a file path, or '-' for standard input")
    s.add_argument("--slice", default="")
    s.add_argument("--mode", default="slice", choices=["slice", "full"])
    s.add_argument("--rule", default="")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--format", default="text", choices=["text", "json"])
    s.add_argument("--emit-model", action="store_true", help="write atlas/out/model.json when the full check is clean")
    s = sub.add_parser("status", help="what to do next")
    s.add_argument("project")
    s.add_argument("--view", default="next", choices=list(tools.VIEWS))
    s.add_argument("--scope", default="")
    s = sub.add_parser("index", help="compact lines about the model")
    s.add_argument("project")
    s.add_argument("--scope", default="nodes")
    s.add_argument("--with-edges", action="store_true")
    s.add_argument("--ids", default="")
    s.add_argument("--detail", default="line", choices=["line", "expanded"])
    s.add_argument("--offset", type=int, default=0)
    s = sub.add_parser("review", help="the one self-review: report (read-only) or record (once)")
    s.add_argument("project")
    s.add_argument("--action", default="report", choices=["report", "record"])
    s.add_argument("--notes", default="")
    s = sub.add_parser("brief", help="the work order")
    s.add_argument("--section", default="")
    s.add_argument("--lang", default="en-US")
    return p


def _human(reply: dict) -> str:
    if isinstance(reply.get("text"), str):
        return reply["text"]
    lines = []
    head = [str(reply[k]) for k in ("status", "slice", "sha8", "view", "phase") if k in reply]
    if "done" in reply:
        head.append("done" if reply["done"] else "not-done")
    if reply.get("code"):
        head.append(f"{reply['code']}: {reply.get('message', '')}")
    if head:
        lines.append(" ".join(head))
    if isinstance(reply.get("diagnostics"), str) and reply["diagnostics"].strip():
        lines.append(reply["diagnostics"].rstrip("\n"))
    if isinstance(reply.get("next"), list):
        lines.extend(str(x) for x in reply["next"])
    if isinstance(reply.get("needsUser"), list):
        lines.extend("ask user: " + str(x) for x in reply["needsUser"])   # the stop condition: never decide for the user
    if isinstance(reply.get("openBlocks"), list) and reply["openBlocks"]:
        lines.append("open BLOCK findings (tell the user): " + ", ".join(str(x) for x in reply["openBlocks"]))
    if isinstance(reply.get("blockers"), list) and reply["blockers"] and not reply.get("done"):
        lines.append("blockers: " + ", ".join(str(x) for x in reply["blockers"]))
    if isinstance(reply.get("findings"), list):
        for f in reply["findings"]:
            ids = (" " + ",".join(f["ids"])) if f.get("ids") else ""
            lines.append(f"{f.get('sev')} {f.get('id')} x{f.get('n')}{ids}: {f.get('fix')}")
    if isinstance(reply.get("sample"), list):
        for row in reply["sample"]:
            lines.append(f"check {row.get('at')} {row.get('k')} {row.get('id')}: {row.get('claim')} | vs {row.get('vs')}")
    if isinstance(reply.get("slots"), list):
        lines.extend(reply["slots"])
    return "\n".join(lines) if lines else json.dumps(reply, ensure_ascii=False, indent=2)


def run(argv: Sequence[str]) -> "tuple[dict, int]":
    """Run one command; ``(reply, exit code)``. Never raises for a refused request."""
    args = _parser().parse_args(list(argv))
    writes = args.cmd in ("init", "put") or (args.cmd == "check" and args.emit_model) or (args.cmd == "review" and args.action == "record")
    try:
        ws = pj.get_workspace(args.root or None, create=writes) if not (args.cmd == "brief" or (args.cmd == "check" and args.text)) else None
    except GemArcError as ex:
        reply = tools.invalid(ex.code, ex.message)
        return reply, 1
    if args.cmd == "init":
        reply = tools.init(ws, args.project, args.title, args.mode, args.languages, args.profile, args.repos)
    elif args.cmd == "put":
        reply = tools.put(ws, args.project, args.slice, _read(args.file, args.text, args.stdin == "-"), args.mode, args.format, args.base)
    elif args.cmd == "check":
        if args.emit_model:
            if not args.project:
                raise SystemExit("--emit-model needs a project")
            reply = tools.emit_model(ws, args.project)
        elif args.text:
            text = _read(None, None, True) if args.text == "-" else _file_or_text(args.text)
            reply = tools.check(None, "", text, args.slice, args.mode, args.rule, args.offset, args.format)
        else:
            reply = tools.check(ws, args.project, "", args.slice, args.mode, args.rule, args.offset, args.format)
    elif args.cmd == "status":
        reply = tools.status(ws, args.project, args.view, args.scope)
    elif args.cmd == "index":
        reply = tools.index(ws, args.project, args.scope, args.with_edges, args.ids, args.detail, args.offset)
    elif args.cmd == "review":
        reply = review_mod.run(ws, args.project, args.action, args.notes)
    else:
        reply = tools.brief(args.section, args.lang)
    bad = reply.get("status") in ("invalid", "error")
    if reply.get("status") == "ok" and reply.get("clean") is False:
        bad = True
    reply["_human"] = args.human
    return reply, 1 if bad else 0


def _file_or_text(value: str) -> str:
    """``check --text``: a path to a file, else the text itself."""
    try:
        with open(value, "rb") as stream:
            return stream.read().decode("utf-8-sig")
    except (OSError, ValueError):  # not a path (a NUL character, a name too long...): it is the text itself
        return value


def main(argv: "Sequence[str] | None" = None) -> int:
    try:
        reply, code = run(sys.argv[1:] if argv is None else argv)
    except SystemExit as ex:
        if isinstance(ex.code, str):
            print(ex.code, file=sys.stderr)
            return 2
        return int(ex.code or 0)
    human = reply.pop("_human", False)
    out = _human(reply) if human else json.dumps(reply, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write((out + "\n").encode("utf-8"))
    sys.stdout.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
