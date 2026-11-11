#!/usr/bin/env python3
"""Denylist precheck for NEW text (maintainer tool, not shipped): run the repository's own denylist machinery over text before it is written.

    python tools/pilot/deny_precheck.py path/to/draft.md other.py        # files
    python tools/pilot/deny_precheck.py src/gemarc_mcp/atlas/reading     # directories (every text file below, __pycache__ etc. skipped)
    python tools/pilot/deny_precheck.py --stdin < draft.txt              # text that is not on disk yet
    python tools/pilot/deny_precheck.py --changed                        # what git reports as new or modified (the live-tree check)

It imports ``tokenize``, ``digest``, ``load_denylist`` and ``read_lines`` from ``tests/atlas/test_denylist.py`` (so the tokeniser, the
digest list and the text rule are the ones the test uses; nothing is duplicated here) and reports COUNTS only: per input the number of
lines with a hit, the number of hits and the number of distinct digests, plus ``line number`` and a digest prefix for each hit line so
a draft can be fixed (the digest prefix cannot be turned back into a word, and the test prints the same). It never prints a token and
never prints the line text.

What it can and cannot tell you. The digest list is the one in the repository (``tests/atlas/denylist.sha256``). A hit here is a hit in
``test_denylist.py``: reword the generic word or, when a generic word truly must stay, list the exact token in the lane's "issues" for
the lead (the maintainer script keeps the exempt list OUTSIDE the repository; nothing in the repository edits the digest file or the pin).
This script does not read the maintainer's exempt list, so it can never say a hit is acceptable.

Exit code: 0 when nothing hit, 1 when something hit, 2 on a usage error (no input, unreadable digest file). Standard library only.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DENYLIST_TEST = REPO / "tests" / "atlas" / "test_denylist.py"
SKIP_PARTS = {"__pycache__", ".git", ".venv", "node_modules", ".vercel", ".pytest_cache"}


def load_machinery():
    """The ``test_denylist`` module loaded by path (it lives in the repository, no package import needed)."""
    spec = importlib.util.spec_from_file_location("gemarc_denylist_machinery", DENYLIST_TEST)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_lines(machinery, digests: frozenset, lines: list[str]) -> list[tuple[int, str]]:
    """``(line number, digest)`` for every denylisted token of ``lines`` (the rule of ``test_denylist.scan``: tokens shorter than 2 are skipped)."""
    cache: dict[str, str] = {}
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(lines, start=1):
        for token in machinery.tokenize(line):
            if len(token) < 2:
                continue
            value = cache.get(token)
            if value is None:
                value = cache[token] = machinery.digest(token)
            if value in digests:
                hits.append((number, value))
    return sorted(set(hits))


def text_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*") if p.is_file() and not SKIP_PARTS & set(p.relative_to(path).parts)))
        else:
            out.append(path)
    return out


def summarise(name: str, hits: list[tuple[int, str]]) -> str:
    distinct = len({value for _, value in hits})
    lines = sorted({number for number, _ in hits})
    return f"{name}: {len(lines)} line(s) with a hit, {len(hits)} hit(s), {distinct} distinct digest(s)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="files or directories to check")
    ap.add_argument("--stdin", action="store_true", help="check the text on standard input (UTF-8)")
    ap.add_argument("--changed", action="store_true", help="check the files git reports as new or modified (``git ls-files -mo --exclude-standard``)")
    ap.add_argument("--quiet", action="store_true", help="print only the sums")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    machinery = load_machinery()
    try:
        digests, canary = machinery.load_denylist()
    except OSError as exc:
        print(f"cannot read the digest file: {type(exc).__name__}", file=sys.stderr)
        return 2
    all_digests = frozenset(digests | ({canary} if canary else set()))
    inputs: list[tuple[str, list[str]]] = []
    if args.stdin:
        data = sys.stdin.buffer.read()
        inputs.append(("<stdin>", data.decode("utf-8-sig", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n")))
    files = text_files(args.paths)
    if args.changed:
        delta = machinery.git_delta(REPO)
        if delta is None:
            print("git cannot list the working tree here", file=sys.stderr)
            return 2
        files.extend(REPO / name for name in delta)
    seen: set[Path] = set()
    for path in files:
        if path in seen or not path.is_file() or path.name == machinery.DENYLIST.name:
            continue
        seen.add(path)
        lines = machinery.read_lines(path)  # None for a binary or non-UTF-8 file: skipped, like the test
        if lines is None:
            continue
        try:
            shown = path.relative_to(REPO).as_posix()
        except ValueError:
            shown = path.name
        inputs.append((shown, lines))
    if not inputs:
        print("nothing to check (give files, a directory, --stdin or --changed)", file=sys.stderr)
        return 2
    total_hits = 0
    bad_inputs = 0
    for name, lines in inputs:
        hits = check_lines(machinery, all_digests, lines)
        if hits:
            bad_inputs += 1
            total_hits += len(hits)
            if not args.quiet:
                print(summarise(name, hits))
                for number, value in hits:
                    print(f"  line {number}: digest {value[:12]}")
    print(f"checked {len(inputs)} input(s) against {len(all_digests)} digests: {bad_inputs} input(s) with hits, {total_hits} hit(s) in total")
    return 1 if total_hits else 0


if __name__ == "__main__":
    sys.exit(main())
