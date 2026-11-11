#!/usr/bin/env python3
"""Token ledger re-measure (maintainer tool, not shipped): the BYTES of every request and reply of the scripted pipelines.

    python tools/pilot/ledger.py                 # run the pipelines, print the report as JSON
    python tools/pilot/ledger.py --markdown      # the tables of docs/atlas/TOKEN_LEDGER.md (Chinese headers)
    python tools/pilot/ledger.py --no-browser    # skip the real browser gate (the verify reply is then missing from the numbers)
    python tools/pilot/ledger.py --only reference,asis

It drives the REAL tool functions through ``tests/atlas/pipeline.py`` (the scripted drivers of milestone M8; no model in the loop) on the three
synthetic fixtures (tiny, small, reference), a greenfield project from a requirement brief and an as-is project over a generated 25-module repository,
and records for every call the bytes of the request (what an AI WRITES: the arguments as compact JSON) and of the reply (what it READS: the compact
JSON text of the server's answer). Those are MEASUREMENTS. Tokens are NOT measured here: the report converts bytes with ``bytes / 3.2`` (mixed text),
labelled ESTIMATE everywhere, because only a real run reports tokens (the section "待实测(LLM)" of the ledger document is for that).

What the script does not contain, and a model does: thinking, repair loops, ``index`` reads before cross-slice references, reading source files
(as-is evidence), re-reading after a lost context. The ledger document says so next to every comparison.

Writes nothing but temporary directories (removed at the end). Standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

BYTES_PER_TOKEN = 3.2          # mixed text: ASCII ids and rows at about 3, CJK prose a little higher or lower; an ESTIMATE, never a measurement

#: Design 4.2, one 169-node atlas, T3 tokens, TARGET column (80% first-pass-clean): ``(row, tokens, scales with the node count)``.
DESIGN_ROWS = (
    ("slice text (25 puts)", 57400, True), ("put envelopes (project, slice, mode)", 1200, True), ("other call arguments", 1600, True),
    ("repair patch rows", 2000, True), ("tools/list + brief all", 5800, False), ("init result", 250, False), ("put results", 13600, True),
    ("one-edit typo WARN lines", 850, True), ("scoped index reads", 18800, True), ("status", 3900, True), ("oracle verdicts", 8700, True),
    ("evidence verify", 1900, True), ("doc outline + lint", 2000, True), ("build + gate", 1200, False),
)
DESIGN_TOTAL_TARGET, DESIGN_NODES = 119000, 169


COUNT_KEYS = ("nodes", "edges", "evidences", "cases", "interventions", "requirements", "tbds", "policies")


def model_counts(ws: Path) -> dict:
    """The section counts of the finished project in ``ws``, read from its MODEL: a flow that changes its facts cannot leave a stale literal here
    (the counts scale the design column of the comparison)."""
    from gemarc_mcp.atlas import project as pj
    from gemarc_mcp.atlas import status as status_mod
    from tests.atlas import pipeline as P

    paths = pj.project_paths(pj.get_workspace(ws, create=False), P.PROJECT)
    model = status_mod.load_snapshot(paths, pj.load_config(paths)).model
    return {k: v for k, v in P.synth.section_counts(model).items() if k in COUNT_KEYS}


def est(bytes_: float) -> int:
    return round(bytes_ / BYTES_PER_TOKEN)


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


def derived_bytes(dist: Path) -> dict:
    return {"page": (dist / "index.html").stat().st_size, "docs": dir_bytes(dist / "docs"), "diagrams": dir_bytes(dist / "diagrams"), "prompts": dir_bytes(dist / "prompts"),
            "oracleSheet": (dist / "oracle-sheet.md").stat().st_size if (dist / "oracle-sheet.md").is_file() else 0,
            "buildInfo": (dist / "build-info.json").stat().st_size}


def tools_list_bytes() -> int:
    from gemarc_mcp import server

    app = server.create_server("atlas")
    tools = asyncio.run(app.list_tools())
    return len(json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def prompt_bytes() -> int:
    from gemarc_mcp import atlas_tools

    return len(atlas_tools.create_prompt_local(requirement="An invented requirement.", mode="asis", project="demo", lang="zh-CN").encode("utf-8"))


def summarise(name: str, session, model_counts: dict, dist: Path, extra: "dict | None" = None) -> dict:
    records = session.records
    sums = session.tool_sums()
    puts = [r for r in records if r["tool"] == "gemarc_atlas_put"]
    return {"name": name, "counts": model_counts, "calls": len(records), "request": sum(r["request"] for r in records), "reply": sum(r["reply"] for r in records),
            "put": {"calls": len(puts), "textBytes": sum(r["text"] for r in puts), "requestBytes": sum(r["request"] for r in puts), "replyBytes": sum(r["reply"] for r in puts)},
            "tools": {k.replace("gemarc_atlas_", ""): v for k, v in sorted(sums.items())},
            "phases": {k: v for k, v in sorted(session.by_phase().items())},
            "seconds": round(sum(r["seconds"] for r in records), 1), "derived": derived_bytes(dist), **(extra or {})}


def run_fixture(preset: str, seed: int, base: Path, browser: bool) -> dict:
    from tests.atlas import pipeline as P

    ws, scratch = base / f"{preset}-ws", base / f"{preset}-scratch"
    ws.mkdir()
    scratch.mkdir()
    run = P.run_fixture(preset, seed, ws, scratch, browser=browser)
    counts = {k: v for k, v in P.synth.section_counts(run["model"]).items() if k in ("nodes", "edges", "evidences", "cases", "interventions", "requirements", "tbds", "policies")}
    return summarise(preset, run["session"], counts, ws / P.PROJECT / "atlas" / "dist", {"done": bool(run["final"]["done"])})


def run_greenfield(base: Path, browser: bool) -> dict:
    from tests.atlas import pipeline as P
    from tests.atlas import pipeline_flows as F

    ws = base / "greenfield-ws"
    ws.mkdir()
    with P.Session(ws, {}) as s:
        out = F.run_greenfield(s)
    return summarise("greenfield", s, model_counts(ws), ws / P.PROJECT / "atlas" / "dist",
                     {"done": bool(out["final"]["done"]), "briefBytes": len(F.BRIEF.encode("utf-8"))})


def run_asis(base: Path, browser: bool) -> dict:
    from tests.atlas import pipeline as P
    from tests.atlas import pipeline_flows as F

    ws, repo_dir = base / "asis-ws", base / "asis-repo"
    ws.mkdir()
    repo = F.make_repository(repo_dir)
    sources = sum(p.stat().st_size for p in repo_dir.rglob("*.py"))
    with P.Session(ws, {"main": repo_dir}) as s:
        out = F.run_asis(s, repo)
    return summarise("asis", s, model_counts(ws), ws / P.PROJECT / "atlas" / "dist",
                     {"done": bool(out["final"]["done"]), "sourceBytes": sources})


def compare(run: dict, tools_list: int, brief: int) -> list:
    """The rows of design 4.2 next to the measured scripted traffic of ``run`` (tokens are estimates from bytes). ``scale`` is nodes / 169 for the rows that grow with the atlas."""
    nodes = run["counts"].get("nodes", 0)
    scale = nodes / DESIGN_NODES
    tools = run["tools"]

    def t(name, key):
        return tools.get(name, {}).get(key, 0)
    put = run["put"]
    measured = {
        "slice text (25 puts)": put["textBytes"], "put envelopes (project, slice, mode)": put["requestBytes"] - put["textBytes"],
        "other call arguments": sum(v["request"] for k, v in tools.items() if k != "put"), "repair patch rows": 0,
        "tools/list + brief all": tools_list + brief, "init result": t("init", "reply"), "put results": put["replyBytes"], "one-edit typo WARN lines": 0, "scoped index reads": t("index", "reply"),
        "status": t("status", "reply"), "oracle verdicts": t("simulate", "reply"), "evidence verify": t("evidence", "reply"), "doc outline + lint": t("doc", "reply"),
        "build + gate": t("build", "reply") + t("verify", "reply"),
    }
    rows = []
    for row, tokens, grows in DESIGN_ROWS:
        rows.append({"row": row, "design": tokens, "scaled": round(tokens * scale) if grows else tokens, "bytes": measured[row], "estTokens": est(measured[row])})
    return rows


def render_markdown(report: dict) -> str:
    out = []
    for name in ("reference", "small", "tiny", "asis", "greenfield"):
        run = report["runs"].get(name)
        if not run:
            continue
        c = run["counts"]
        out += [f"#### {name}（节点 {c.get('nodes')}，边 {c.get('edges')}，证据 {c.get('evidences')}，用例 {c.get('cases')}；{run['calls']} 次调用，到 done：{run.get('done')}）", "",
                "| 工具 | 次数 | 写出字节（请求） | 读入字节（回复） | 估算 token（写） | 估算 token（读） |", "|---|---:|---:|---:|---:|---:|"]
        for tool, v in run["tools"].items():
            out.append(f"| `{tool}` | {v['calls']} | {v['request']:,} | {v['reply']:,} | {est(v['request']):,} | {est(v['reply']):,} |")
        out.append(f"| **合计** | {run['calls']} | {run['request']:,} | {run['reply']:,} | {est(run['request']):,} | {est(run['reply']):,} |")
        out += ["", "| 阶段 | 次数 | 写出字节 | 读入字节 |", "|---|---:|---:|---:|"]
        out += [f"| {p} | {v['calls']} | {v['request']:,} | {v['reply']:,} |" for p, v in run["phases"].items()]
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):     # the report is UTF-8 whatever the console or the redirect would use (GBK, cp932, cp1252 crash on the Chinese headers)
        stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--only", default="reference,small,tiny,asis,greenfield")
    args = parser.parse_args(argv)
    wanted = [x for x in args.only.split(",") if x]
    os.environ["GEMARC_ALLOW_WRITE"] = "1"
    started = time.perf_counter()
    report: dict = {"method": {"bytesPerToken": BYTES_PER_TOKEN, "measured": "bytes of request and reply per call", "estimated": "tokens = bytes / 3.2"}, "runs": {}}
    with tempfile.TemporaryDirectory(prefix="atlas-ledger-") as tmp:
        base = Path(tmp)
        browser = not args.no_browser
        for name in wanted:
            if name in ("reference", "small", "tiny"):
                report["runs"][name] = run_fixture(name, {"tiny": 1, "small": 3, "reference": 3}[name], base, browser)
            elif name == "asis":
                report["runs"][name] = run_asis(base, browser)
            elif name == "greenfield":
                report["runs"][name] = run_greenfield(base, browser)
            else:
                raise SystemExit(f"unknown run {name!r}")
        shutil.rmtree(base, ignore_errors=True)
    report["toolsListBytes"] = tools_list_bytes()
    from gemarc_mcp.atlas import tools as core

    report["briefAllBytes"] = len(json.dumps(core.brief("all", "zh-CN"), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    report["promptBytes"] = prompt_bytes()
    report["design"] = {"nodes": DESIGN_NODES, "totalTargetTokens": DESIGN_TOTAL_TARGET, "rows": [{"row": r, "tokens": t, "scales": g} for r, t, g in DESIGN_ROWS]}
    for name, run in report["runs"].items():
        run["comparison"] = compare(run, report["toolsListBytes"], report["briefAllBytes"])
        run["scriptedTrafficEstTokens"] = est(run["request"] + run["reply"])
        run["designScaledTotalTokens"] = round(DESIGN_TOTAL_TARGET * run["counts"].get("nodes", 0) / DESIGN_NODES)
    report["seconds"] = round(time.perf_counter() - started, 1)
    print(render_markdown(report) if args.markdown else json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
