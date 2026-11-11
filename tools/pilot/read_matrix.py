#!/usr/bin/env python3
"""Measurement table of the reading surface (maintainer tool, not shipped; design A3 sections 2.3 and 8.2, T-R1, T-R2, T-R13).

    python tools/pilot/read_matrix.py                              # tiny, small, reference, refcjk x seeds 1-4, 6-8, zh-CN
    python tools/pilot/read_matrix.py --langs zh-CN,en-US --presets small,reference --seeds 1,2
    python tools/pilot/read_matrix.py --json table.json            # the raw numbers as well

For every build it prints the mode, the page counts per family, the Markdown bytes, the largest and the median page, the share of the frames in the
page bytes, the files and bytes an AI reads (``aiFiles``, ``aiBytes``), the references the capped tables hide, and the findings by class. The bands that
``tests/atlas/test_read_plan.py`` pins are read from this table; they may only change with the table that this script prints (never to make a threshold
pass). Standard library only; writes nothing unless ``--json`` is given.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import collections
import dataclasses
import json
import statistics
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp.atlas import build as BLD  # noqa: E402
from gemarc_mcp.atlas import reading as R  # noqa: E402
from gemarc_mcp.atlas.reading import parse as P  # noqa: E402
from tests.atlas import synth  # noqa: E402

PRESETS = ("tiny", "small", "reference", "refcjk")
SEEDS = (1, 2, 3, 4, 6, 7, 8)       # seed 5 cannot be put through the generator
QA = {"rules": {"block": 2, "ERROR": 0, "WARN": 3}, "gate": {"release": False, "blockers": 5},
      "oracle": {"sealed": 0, "revised": 0, "confirmed": 0, "pending": 3, "sheet": "oracle-sheet.md", "ledger": "none", "badge": "none"}}


def model_of(preset: str, seed: int) -> dict:
    if preset == "refcjk":
        return synth.generate(dataclasses.replace(synth.PRESETS["reference"], lang="cjk"), seed)
    return synth.generate(preset, seed)


def measure(preset: str, seed: int, lang: str) -> dict:
    bundle = BLD.make_bundle(model_of(preset, seed), lang=lang, qa=QA)
    started = time.time()
    result = R.build(bundle, None, R.ReadConfig(), None)
    secs = time.time() - started
    md = {p: len(d) for p, d in result.files.items() if p.startswith("read/") and p.endswith(".md")}
    facts = {p: d for p, d in result.files.items() if p in md and "INDEX" not in p and "/G01" not in p}
    sizes = [len(d) for d in facts.values()]
    env = sum(P.parse_page(lang, p, d.decode("utf-8")).envelope_bytes for p, d in facts.items())
    ai = [r for r in result.records if r.get("ai") and r["cls"] in ("card", "page", "svg")]
    findings = collections.Counter(f"{f.rule}/{f.klass}/{f.severity}" for f in result.findings)
    return {"preset": preset, "seed": seed, "lang": lang, "status": result.status, "secs": round(secs, 2), "mode": result.plan["mode"],
            "pages": result.plan["pages"], "folded": result.plan["folded"], "md_files": len(md), "md_bytes": sum(md.values()), "max_page": max(sizes),
            "median_page": int(statistics.median(sizes)), "frame_share": round(env / sum(sizes), 3), "ai_files": len(ai), "ai_bytes": sum(r["b"] for r in ai),
            "refs": result.plan["refs"], "unprinted_refs": result.plan["unprinted_refs"], "ids": result.plan["ids"], "findings": dict(findings)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--presets", default=",".join(PRESETS))
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    ap.add_argument("--langs", default="zh-CN")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    rows = [measure(p, int(s), lang) for lang in args.langs.split(",") for p in args.presets.split(",") for s in args.seeds.split(",")]
    print("| lang | preset | seed | status | secs | mode | S | R/T/E | C | md files | md bytes | max page | median | frame share | aiFiles | aiBytes | unprinted refs | findings |")
    print("|---|---|--:|---|--:|---|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|")
    for r in rows:
        pages = r["pages"]
        print(f"| {r['lang']} | {r['preset']} | {r['seed']} | {r['status']} | {r['secs']} | {r['mode']} | {pages['S']} | {pages['R']}/{pages['T']}/{pages['E']} | {pages['C']} | "
              f"{r['md_files']} | {r['md_bytes']:,} | {r['max_page']:,} | {r['median_page']:,} | {r['frame_share']} | {r['ai_files']} | {r['ai_bytes']:,} | "
              f"{r['unprinted_refs']}/{r['refs'] + r['unprinted_refs']} | {r['findings'] or '-'} |")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
