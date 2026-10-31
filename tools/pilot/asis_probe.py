#!/usr/bin/env python3
"""As-is probe on a real source tree (maintainer tool, not shipped): what does the ``outline`` tool give an AI that analyses THIS repository?

    GEMARC_ATLAS_ROOTS="main=<repo>" python tools/pilot/asis_probe.py
    python tools/pilot/asis_probe.py --json              # the same numbers as one JSON document
    python tools/pilot/asis_probe.py --exclude atlas,accounts --subdir src/gemarc_mcp

It runs the real ``gemarc_atlas_outline`` function (``gemarc_mcp.atlas.tools.outline``, pages followed to the end) on every ``*.py`` file below
``--subdir`` (default ``src/gemarc_mcp``) of the root alias ``--alias`` (default ``main``) except the directories named by ``--exclude`` (default
``atlas``: the atlas package itself is the tool, not the subject; ``atlas_tools.py`` sits OUTSIDE that directory and is therefore included, as is
``accounts/``), then checks every anchor it listed with the real evidence verifier, and reports:

* files and modules (a module is a ``*.py`` file that is not ``__init__.py``) and packages;
* outline bytes per file: what the AI reads (the compact JSON of every page reply), sum, mean, p50, p90, max, pages;
* anchors per file (entries that carry a verified single-line anchor), entries without one and why, and the unique-anchor rate (anchors that occur
  exactly once in their file, counted independently of the outline tool: 1.0 by construction is the claim, this measures it);
* EV-003 stampable anchors: rows ``repo=<alias>`` with the anchor as the evidence anchor, verified read-only by ``evidence.verify_evidence``
  (PASS, stampable, one occurrence), as a share of all anchors;
* time: wall seconds of the outline runs and of the verification.

READ-ONLY on the repository: nothing is written (``sys.dont_write_bytecode`` is set before the first import; the verifier is used without a stamp and
without the root revision, so no git command runs), and the tree below ``--subdir`` and ``git status`` are fingerprinted before and after
(``repoUnchanged``). The numbers are the size-scaled input of the token ledger for the real run (docs/atlas/TOKEN_LEDGER.md). Standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True       # before any import of the package: a probe must not write .pyc files into the repository

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from gemarc_mcp.atlas import evidence as ev  # noqa: E402
from gemarc_mcp.atlas import roots as roots_mod  # noqa: E402
from gemarc_mcp.atlas import tools  # noqa: E402

LINE = re.compile(r'^L(\d+) (.+?) \| (?:"(.*)"|- \((.*)\))$')
HEADER = re.compile(r"^# .* (\d+) lines, (\d+) entries(?:, (\d+) without)?")


def percentile(values: list, fraction: float):
    """Nearest-rank percentile (the value, not an interpolation); ``None`` for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(-(-fraction * len(ordered) // 1)) - 1))]


def compact_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def fingerprint(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\1")
    return digest.hexdigest()


def git_status(root: Path) -> "str | None":
    try:
        done = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-uall"], capture_output=True, timeout=60,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def source_files(base: Path, exclude: "tuple[str, ...]") -> list:
    out = []
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(base)
        if "__pycache__" in relative.parts or (relative.parts and relative.parts[0] in exclude and len(relative.parts) > 1):
            continue
        out.append(path)
    return out


def outline_file(alias: str, relative: str) -> dict:
    """Every page of the outline of one file: entries, bytes read, pages."""
    entries, replies, offset, pages, header = [], 0, 0, 0, None
    while True:
        reply = tools.outline(alias, relative, offset)
        pages += 1
        replies += compact_bytes(reply)
        if reply.get("status") != "ok":
            return {"error": f"{reply.get('code')}: {str(reply.get('message'))[:100]}", "replyBytes": replies, "pages": pages, "entries": [], "total": 0, "unanchored": 0}
        lines = reply["text"].split("\n")
        if header is None:
            header = HEADER.match(lines[0])
        for line in lines[1:]:
            match = LINE.match(line)
            if match:
                entries.append({"line": int(match.group(1)), "label": match.group(2), "anchor": json.loads('"' + match.group(3) + '"') if match.group(3) is not None else None,
                                "why": match.group(4)})
        if not reply.get("next"):
            break
        offset = int(reply["next"])
    total = int(header.group(2)) if header else len(entries)
    return {"entries": entries, "replyBytes": replies, "pages": pages, "total": total, "unanchored": int(header.group(3) or 0) if header else 0}


def occurrences(text: str, anchor: str) -> int:
    count, at = 0, text.find(anchor)
    while at >= 0:
        count += 1
        at = text.find(anchor, at + max(1, len(anchor)))
    return count


def stats(values: list) -> dict:
    if not values:
        return {"sum": 0, "mean": 0, "p50": None, "p90": None, "max": None}
    return {"sum": sum(values), "mean": round(sum(values) / len(values), 1), "p50": percentile(values, 0.5), "p90": percentile(values, 0.9), "max": max(values)}


def run(alias: str, subdir: str, exclude: "tuple[str, ...]", verify: bool = True) -> dict:
    table = roots_mod.roots_from_env()
    if alias not in table:
        raise SystemExit(f"GEMARC_ATLAS_ROOTS must name the alias {alias!r} (for example GEMARC_ATLAS_ROOTS=\"{alias}=<repository>\")")
    root = table[alias]
    base = root / subdir
    if not base.is_dir():
        raise SystemExit(f"{base} is not a directory")
    before, status_before = fingerprint(base), git_status(root)
    files = source_files(base, exclude)
    per_file, started = [], time.perf_counter()
    rows, ids = [], {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        result = outline_file(alias, relative)
        text = path.read_bytes().decode("utf-8", errors="replace")
        anchored = [e for e in result["entries"] if e["anchor"] is not None]
        unique = sum(1 for e in anchored if occurrences(text, e["anchor"]) == 1)
        per_file.append({"file": relative, "module": path.name != "__init__.py", "replyBytes": result["replyBytes"], "pages": result["pages"], "entries": result["total"],
                         "anchors": len(anchored), "unanchored": result["unanchored"], "unique": unique, "anchorBytes": sum(len(e["anchor"].encode("utf-8")) for e in anchored),
                         "error": result.get("error")})
        for e in anchored:
            ident = f"P{len(rows) + 1}"
            ids[ident] = relative
            rows.append({"evidenceId": ident, "repository": alias, "filePath": relative, "anchor": e["anchor"], "topic": "probe", "lineRange": None})
    outline_seconds = time.perf_counter() - started
    stampable, verify_seconds, by_status = 0, 0.0, {}
    if verify and rows:
        t = time.perf_counter()
        report = ev.verify_evidence(rows, {alias: root}, stamp=None, mode="asis", options=ev.VerifyOptions(compute_root_rev=False))
        verify_seconds = time.perf_counter() - t
        for result in report["results"]:
            by_status[result["status"]] = by_status.get(result["status"], 0) + 1
            if result["status"] == "PASS" and result.get("stampable") and (result.get("resolved") or {}).get("occurrences") == 1:
                stampable += 1
    after, status_after = fingerprint(base), git_status(root)
    total_entries = sum(f["entries"] for f in per_file)
    total_anchors = sum(f["anchors"] for f in per_file)
    reasons: dict = {}
    summary = {
        "root": f"{alias}:{subdir}", "excluded": list(exclude), "includesAtlasToolsBinding": (root / "src" / "gemarc_mcp" / "atlas_tools.py").is_file() and subdir.replace("\\", "/").rstrip("/") == "src/gemarc_mcp",
        "files": len(per_file), "modules": sum(1 for f in per_file if f["module"]), "packages": len(files) - sum(1 for f in per_file if f["module"]),
        "filesWithError": [f["file"] for f in per_file if f["error"]],
        "sourceBytes": sum(path.stat().st_size for path in files),
        "outlineReplyBytesPerFile": stats([f["replyBytes"] for f in per_file]), "pagesPerFile": stats([f["pages"] for f in per_file]),
        "entries": total_entries, "anchors": total_anchors, "anchorsPerFile": stats([f["anchors"] for f in per_file]),
        "entriesWithoutAnchor": total_entries - total_anchors, "anchorRate": round(total_anchors / total_entries, 4) if total_entries else None,
        "uniqueAnchorRate": round(sum(f["unique"] for f in per_file) / total_anchors, 4) if total_anchors else None,
        "anchorBytes": stats([f["anchorBytes"] for f in per_file if f["anchors"]]),
        "ev003": {"verified": bool(verify), "rows": len(rows), "byStatus": by_status, "stampable": stampable, "stampableShareOfAnchors": round(stampable / total_anchors, 4) if total_anchors else None},
        "seconds": {"outline": round(outline_seconds, 2), "verify": round(verify_seconds, 2), "outlineMsPerFile": round(1000 * outline_seconds / max(1, len(per_file)), 1)},
        "repoUnchanged": before == after and status_before == status_after, "reasons": reasons,
        "largestFiles": [{"file": f["file"], "replyBytes": f["replyBytes"], "pages": f["pages"], "anchors": f["anchors"]} for f in sorted(per_file, key=lambda x: -x["replyBytes"])[:5]],
    }
    return summary


def render(summary: dict) -> str:
    o, a, e, s = summary["outlineReplyBytesPerFile"], summary["anchorsPerFile"], summary["ev003"], summary["seconds"]
    return "\n".join([
        f"as-is probe on {summary['root']} (excluded: {', '.join(summary['excluded']) or 'nothing'}; atlas_tools.py binding included: {summary['includesAtlasToolsBinding']})",
        f"files {summary['files']}, modules {summary['modules']} (not __init__.py), packages {summary['packages']}; files with an outline error: {len(summary['filesWithError'])}",
        f"outline bytes read per file: sum {o['sum']}, mean {o['mean']}, p50 {o['p50']}, p90 {o['p90']}, max {o['max']}; pages per file p50 {summary['pagesPerFile']['p50']}, max {summary['pagesPerFile']['max']}",
        f"source bytes of the same files: {summary['sourceBytes']} (the outline replies add up to {o['sum'] / summary['sourceBytes']:.1%} of that)" if summary["sourceBytes"] else "source bytes: 0",
        f"entries {summary['entries']}, anchors {summary['anchors']} (anchor rate {summary['anchorRate']}), without a unique anchor {summary['entriesWithoutAnchor']}",
        f"anchors per file: sum {a['sum']}, mean {a['mean']}, p50 {a['p50']}, p90 {a['p90']}, max {a['max']}; anchor bytes per file p50 {summary['anchorBytes']['p50']}",
        f"unique-anchor rate (counted in the files, independently of the tool): {summary['uniqueAnchorRate']}",
        f"EV-003 stampable anchors: {e['stampable']} of {e['rows']} (share {e['stampableShareOfAnchors']}); verifier statuses {e['byStatus']}" if e["verified"] else "EV-003 check skipped (--no-verify)",
        f"time: outline runs {s['outline']} s ({s['outlineMsPerFile']} ms per file), verification {s['verify']} s",
        f"repository unchanged (tree below the subdir and git status, before and after): {summary['repoUnchanged']}",
        "largest files: " + "; ".join(f"{x['file']} {x['replyBytes']} B in {x['pages']} page(s), {x['anchors']} anchors" for x in summary["largestFiles"]),
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--alias", default="main", help="the root alias in GEMARC_ATLAS_ROOTS (default main)")
    parser.add_argument("--subdir", default="src/gemarc_mcp", help="directory below the root to probe (default src/gemarc_mcp)")
    parser.add_argument("--exclude", default="atlas", help="comma-separated directory names below --subdir to skip (default atlas)")
    parser.add_argument("--no-verify", action="store_true", help="skip the EV-003 verification of every anchor")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)
    if not os.environ.get(roots_mod.ENV_NAME, "").strip():
        os.environ[roots_mod.ENV_NAME] = f"{args.alias}={REPO}"      # for this process only: the repository the tool lives in
        print(f"{roots_mod.ENV_NAME} was not set: using {args.alias}={REPO} for this run", file=sys.stderr)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    summary = run(args.alias, args.subdir, tuple(x for x in args.exclude.split(",") if x), verify=not args.no_verify)
    print(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) if args.json else render(summary))
    return 0 if summary["repoUnchanged"] and not summary["filesWithError"] else 1


if __name__ == "__main__":
    sys.exit(main())
