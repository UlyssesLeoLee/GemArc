"""``python -m gemarc_mcp.atlas <command>``: the atlas tools from a shell (init, put, check, status, index, simulate, doc, outline, evidence, build, verify, review, brief).

::

    python -m gemarc_mcp.atlas init <project> [--title T] [--mode asis|tobe] [--languages zh-CN,en-US] [--repos main,web]
    python -m gemarc_mcp.atlas put <project> <slice> (--file F | --text T | -) [--mode replace|patch] [--format atl|jsonl] [--base sha8]
    python -m gemarc_mcp.atlas check <project> [--slice S] [--mode slice|full] [--rule R] [--offset N] [--format text|json] [--emit-model]
    python -m gemarc_mcp.atlas check --text FILE|-            # inline text, advisory, no project
    python -m gemarc_mcp.atlas status <project> [--view next|report|dims|oracle|evidence] [--scope S]
    python -m gemarc_mcp.atlas index <project> [--scope nodes|room:ID|req:ID|case:ID|ids:PREFIX] [--with-edges] [--ids A,B] [--detail line|expanded] [--offset N]
    python -m gemarc_mcp.atlas simulate <project> [--case C] [--intervention I] [--detail count|ids|trace]   # blind verdicts (seals first)
    python -m gemarc_mcp.atlas simulate <project> --bite | --sheet | --gate                                    # mutants + guard scan | oracle sheet | release gate
    python -m gemarc_mcp.atlas doc <project> [--unit system|req:ID|case:ID|node:ID] [--level REQ|BD|DD] [--lang L] [--chapter N] [--page N]
    python -m gemarc_mcp.atlas outline <root-alias> <path> [--offset N]                                        # source outline with anchors
    python -m gemarc_mcp.atlas evidence <project> [--ids A,B]                                                  # verify evidence, stamp the passes
    python -m gemarc_mcp.atlas build <project> [--release] [--no-docs] [--gate none|smoke]                  # dist/index.html (+ documents, oracle sheet)
    python -m gemarc_mcp.atlas verify <project> [--widths 1024,1366,1600]                                       # browser gate on the built page
    python -m gemarc_mcp.atlas review <project> [--action report|record] [--notes "fixed=RV-..; open=RV-.."]
    python -m gemarc_mcp.atlas brief [--section S] [--lang zh-CN|en-US]

The workspace root is ``GEMARC_WORKSPACE_ROOT`` (default ``~/.gemarc/workspace``; ``--root`` overrides it for one call); ``init``,
``put``, ``simulate`` (it seals and ledgers; ``--gate`` only reads), ``evidence`` (writes the stamp), ``build`` (also writes the oracle sheet), ``verify`` (writes ``dist/.verify``), ``review --action record`` and ``check --emit-model`` write and need ``GEMARC_ALLOW_WRITE=1``;
``doc`` and ``outline`` only read (``outline`` takes an alias of ``GEMARC_ATLAS_ROOTS``, as ``evidence`` does); a read never creates the
default workspace. ``status`` applies the real completion gates (as the MCP tools do) when it is run from this entry. Output is the compact JSON reply of the tool (UTF-8); ``--human`` prints only the human text (diagnostics,
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
from . import status as status_mod
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


def _parser(prog: str = "python -m gemarc_mcp.atlas") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
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
    s = sub.add_parser("simulate", help="blind verdicts for the registered expectations (ids and trace are recorded reveals)")
    s.add_argument("project")
    s.add_argument("--case", default="")
    s.add_argument("--intervention", default="")
    s.add_argument("--detail", default="count", choices=["count", "ids", "trace"])
    group = s.add_mutually_exclusive_group()
    group.add_argument("--bite", action="store_true", help="verdicts, then the mutants of every case that held and the guard scan")
    group.add_argument("--sheet", action="store_true", help="write atlas/dist/oracle-sheet.md")
    group.add_argument("--gate", action="store_true", help="the oracle part of the release gate (read-only)")
    s = sub.add_parser("doc", help="structure of the derived documents: outline, or one chapter page (never engine output)")
    s.add_argument("project")
    s.add_argument("--unit", default="system")
    s.add_argument("--level", default="BD", choices=["REQ", "BD", "DD"])
    s.add_argument("--lang", default="")
    s.add_argument("--chapter", default="")
    s.add_argument("--page", type=int, default=0)
    s = sub.add_parser("outline", help="def/class/heading lines of one source file with unique anchors (root = an alias of GEMARC_ATLAS_ROOTS)")
    s.add_argument("alias", metavar="root-alias")
    s.add_argument("path")
    s.add_argument("--offset", type=int, default=0)
    s = sub.add_parser("evidence", help="verify the evidence rows against the real files and stamp what passes")
    s.add_argument("project")
    s.add_argument("--ids", default="")
    s = sub.add_parser("build", help="write atlas/dist/index.html (and documents) from the project; sealed expectations only")
    s.add_argument("project")
    s.add_argument("--release", action="store_true", help="also require the release gate (oracle, evidence, no NOT-AUTHORED slot, browser gate)")
    s.add_argument("--no-docs", action="store_true", help="do not write docs/, diagrams/ and prompts/")
    s.add_argument("--gate", default="none", choices=["none", "smoke"], help="run the browser gate on the page after writing it")
    s = sub.add_parser("verify", help="run the browser gate on the built page (exit 1 on FAIL, SKIP or INCONCLUSIVE)")
    s.add_argument("project")
    s.add_argument("--widths", default="1024,1366,1600", help="comma separated innerWidth values")
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
    if isinstance(reply.get("page"), dict) and "bytes" in reply["page"]:       # build: where the page is and what it is made of
        lines.append(f"{reply.get('dist', '')}: index.html {reply['page']['bytes']} bytes sha256 {str(reply['page'].get('sha256', ''))[:12]}")
        lines.append("parts: " + ", ".join(f"{k} {v}" for k, v in (reply.get("parts") or {}).items()))
        lines.append("data: " + ", ".join(f"{k} {v}" for k, v in (reply.get("data") or {}).items()))
    if isinstance(reply.get("lines"), list):
        lines.extend(str(x) for x in reply["lines"])
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
    for key in ("fail", "refused", "undecided"):
        if isinstance(reply.get(key), list):
            lines.extend(str(x) for x in reply[key])
    if isinstance(reply.get("pass"), list) and reply["pass"]:
        lines.append("pass: " + ", ".join(str(x) for x in reply["pass"]))
    if isinstance(reply.get("blockers"), list) and reply["blockers"] and isinstance(reply["blockers"][0], dict):
        lines = [x for x in lines if not x.startswith("blockers: ")]
        lines.extend(f"{b['rule']} {b['kind']} {b['id']}: {b['message']}" for b in reply["blockers"])
    if isinstance(reply.get("bite"), dict):
        b = reply["bite"]
        lines.append(f"bite: {b.get('cases')} cases, {b.get('mutants')} mutants, {b.get('killed')} killed, rate {b.get('rate')}"
                     + (f"; zero killed: {', '.join(b['zeroKilled'])}" if b.get("zeroKilled") else "")
                     + (f"; weak: {', '.join(b['weak'])}" if b.get("weak") else "")
                     + (f"; skipped: {', '.join(reply.get('skipped', []))}" if reply.get("skipped") else ""))
    if isinstance(reply.get("attestation"), dict):
        lines.append(reply["attestation"].get("badge", ""))
    return "\n".join(lines) if lines else json.dumps(reply, ensure_ascii=False, indent=2)


def run(argv: Sequence[str], prog: str = "python -m gemarc_mcp.atlas") -> "tuple[dict, int]":
    """Run one command; ``(reply, exit code)``. Never raises for a refused request. ``prog`` is the program name that ``--help`` and usage errors
    print (the kit launcher passes ``atlas.py``)."""
    args = _parser(prog).parse_args(list(argv))
    writes = (args.cmd in ("init", "put") or (args.cmd == "check" and args.emit_model) or (args.cmd == "review" and args.action == "record")
              or (args.cmd == "simulate" and not args.gate) or args.cmd in ("build", "verify", "evidence"))
    try:
        ws = pj.get_workspace(args.root or None, create=writes) if not (args.cmd in ("brief", "outline") or (args.cmd == "check" and args.text)) else None
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
    elif args.cmd == "simulate":
        if args.gate:
            reply = tools.oracle_gate(ws, args.project)
        elif args.sheet:
            reply = tools.oracle_sheet(ws, args.project)
        elif args.bite:
            reply = tools.oracle_bite(ws, args.project)
        else:
            reply = tools.simulate(ws, args.project, args.case, args.intervention, args.detail)
    elif args.cmd == "doc":
        reply = tools.doc(ws, args.project, args.unit, args.level, args.lang, args.chapter, args.page)
    elif args.cmd == "outline":
        reply = tools.outline(args.alias, args.path, args.offset)
    elif args.cmd == "evidence":
        reply = tools.evidence(ws, args.project, args.ids)
    elif args.cmd == "build":
        reply = tools.build_all(ws, args.project, args.release, not args.no_docs, args.gate)
    elif args.cmd == "verify":
        try:
            widths = [int(x) for x in args.widths.split(",") if x.strip()]
        except ValueError:
            raise SystemExit("--widths is a comma separated list of integers") from None
        reply = tools.verify(ws, args.project, widths)
    elif args.cmd == "review":
        reply = tools.review(ws, args.project, args.action, args.notes, require_report=False)   # every shell call is its own process
    else:
        reply = tools.brief(args.section, args.lang)
    bad = reply.get("status") in ("invalid", "error")
    if reply.get("status") == "ok" and (reply.get("clean") is False or reply.get("ok") is False):
        bad = True                                    # ERROR findings of check, a failing verdict, a closed release gate
    if args.cmd == "simulate" and reply.get("status") == "ok" and "clean" in reply and reply["clean"] is not True:
        bad = True                                    # nothing was verified (clean is null): not a success
    reply["_human"] = args.human
    return reply, 1 if bad else 0


def _file_or_text(value: str) -> str:
    """``check --text``: a path to a file, else the text itself."""
    try:
        with open(value, "rb") as stream:
            return stream.read().decode("utf-8-sig")
    except (OSError, ValueError):  # not a path (a NUL character, a name too long...): it is the text itself
        return value


def main(argv: "Sequence[str] | None" = None, prog: str = "python -m gemarc_mcp.atlas") -> int:
    try:
        with status_mod.product_gates():          # the shell entry applies the same real completion gates as the MCP tools
            reply, code = run(sys.argv[1:] if argv is None else argv, prog)
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
