"""Browser gate spike (design milestone M0, acceptance (b)).

Runs every calibration fixture N times through the real runner (Edge or Chrome, headless,
SwiftShader) and prints a matrix: per fixture and width, how many runs matched the expected verdict
AND the expected finding codes, plus launch-time statistics. Verdict:

    GO     iff every row is deterministic in >= ceil(0.95 * runs) runs (19/20 for --runs 20)
           and the p95 launch time is <= 8 s
    NO-GO  otherwise: work down the fallback ladder in the design (screenshot + stdlib PNG decode,
           then optional CDP via Node)

Retries are OFF here by default (--retries 0) so the matrix shows raw behaviour; the production
runner retries process-level failures once. A row that needed a retry shows it in the "retry" column.

    python tests/atlas/gate_spike.py --runs 20
    python tests/atlas/gate_spike.py --runs 3 --only ok-stub --only frozen-scene

Exit code: 0 GO, 1 NO-GO, 3 no browser found (SKIP, never GO).
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
import tempfile
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gemarc_mcp.atlas.gate import smoke  # noqa: E402

FIXTURES = ROOT / "tests" / "atlas" / "gate_fixtures"
LAUNCH_SECONDS_MAX = 8.0


@dataclass(frozen=True)
class Case:
    fixture: str
    width: int
    status: str
    codes: tuple[str, ...] = ()
    # Optional overrides of the calibrated defaults. Every row runs with the defaults (blank canvas,
    # renderer, header and nonce checks are automatic); none of the rows needs an override.
    non_bg_floor: float | None = None
    require_renderer: str | None = None


CASES = [
    Case("ok-stub", 1256, "PASS"),
    Case("ok-stub", 1366, "PASS"),
    Case("ok-stub", 1576, "PASS"),
    Case("ok-canvas-2d", 1366, "PASS"),
    Case("ok-gl-fbo-left-bound", 1366, "PASS"),
    Case("ok-gl1-instanced", 1366, "PASS"),
    Case("bad-header-overlap", 1256, "FAIL", ("HEADER_OVERLAP",)),
    Case("bad-header-overlap", 1366, "FAIL", ("HEADER_OVERLAP",)),
    Case("bad-header-overlap", 1576, "PASS"),
    Case("frozen-scene", 1366, "FAIL", ("HEARTBEAT",)),
    Case("no-result", 1366, "FAIL", ("NO_RESULT",)),
    Case("console-error", 1366, "FAIL", ("PAGE_ERRORS",)),
    Case("wide-overflow", 1366, "FAIL", ("OVERFLOW_X",)),
    Case("early-error", 1366, "FAIL", ("CONSOLE_STDERR",)),
    Case("page-errors", 1366, "FAIL", ("CONSOLE_STDERR", "PAGE_ERRORS")),
    Case("multi-defect", 1366, "FAIL", ("FIT_CLIPPED", "HEADER_OUTSIDE", "TEXT_BAD")),
    # added with the fix round: each of these was a silent PASS before
    Case("header-unmarked", 1366, "FAIL", ("HEADER_MISSING",)),
    Case("overflow-vscroll", 1366, "FAIL", ("OVERFLOW_X",)),
    Case("console-error-late", 1366, "FAIL", ("PAGE_ERRORS",)),
    Case("late-throw", 1366, "FAIL", ("CONSOLE_STDERR", "PAGE_ERRORS")),
    Case("fake-static", 1366, "FAIL", ("BAD_RESULT",)),
    Case("frame-counts-reads", 1366, "FAIL", ("HEARTBEAT",)),
    Case("canvas-gl-blank-lying", 1366, "FAIL", ("NON_BG", "NO_DRAW")),
    Case("canvas-2d-blank", 1366, "FAIL", ("NON_BG", "NO_DRAW")),
    Case("canvas-no-context", 1366, "FAIL", ("NON_BG",)),
    Case("canvas-declared-hidden", 1366, "FAIL", ("NON_BG",)),
    Case("gl-error", 1366, "FAIL", ("CONSOLE_STDERR", "GL_ERROR")),
    Case("settab-noop", 1366, "FAIL", ("TAB_STATE",)),
    Case("nan-in-attr", 1366, "FAIL", ("TEXT_BAD",)),
]


@dataclass
class Row:
    label: str
    case: Case
    runs: int = 0
    matched: int = 0
    seconds: list[float] = field(default_factory=list)
    spawn: list[float] = field(default_factory=list)
    retried: int = 0
    leftovers: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    non_bg: set[float] = field(default_factory=set)  # nonBgRatio as READ BY THE GATE from the canvases
    inner_widths: set[int] = field(default_factory=set)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def run_row(row: Row, page: pathlib.Path, runs: int, retries: int, tmp_root, browser, inline: bool) -> None:
    case = row.case
    for _ in range(runs):
        res = smoke.run_gate(
            page,
            case.width,
            browser=browser,
            tmp_root=tmp_root,
            retries=retries,
            inline_selftest=inline,
            non_bg_floor=case.non_bg_floor,
            require_renderer=case.require_renderer,
        )
        key = f"{res.status}[{','.join(res.codes)}]"
        row.runs += 1
        row.seconds.append(res.seconds)
        row.spawn.append(res.spawn_seconds)
        row.retried += 1 if res.attempts > 1 else 0
        row.leftovers += len(res.leftovers)
        row.outcomes[key] = row.outcomes.get(key, 0) + 1
        if res.status == case.status and res.codes == sorted(case.codes):
            row.matched += 1
        if res.result:
            if isinstance(res.result.get("innerWidth"), int):
                row.inner_widths.add(res.result["innerWidth"])
            for tab in res.result.get("tabs", []):
                for canvas in (tab.get("canvases") or []) if isinstance(tab, dict) else []:
                    if isinstance(canvas, dict) and isinstance(canvas.get("nonBgRatio"), (int, float)):
                        row.non_bg.add(round(float(canvas["nonBgRatio"]), 6))
        print("." if res.status == case.status else "x", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the browser-gate calibration fixtures N times and print a matrix.")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--retries", type=int, default=0, help="process-level retries (default 0: show raw behaviour)")
    parser.add_argument("--only", action="append", default=[], help="fixture name filter (repeatable)")
    parser.add_argument("--browser", default=None, help="browser executable (default: discover Edge, then Chrome)")
    parser.add_argument("--tmp-root", default=None, help="parent directory for profiles and assembled pages")
    parser.add_argument("--no-non-ascii", action="store_true", help="skip the Japanese-named-directory row")
    parser.add_argument("--json", default=None, metavar="PATH", help="also write the matrix as JSON")
    args = parser.parse_args(argv)

    if args.browser:
        browser = smoke.Browser(args.browser, smoke._family_of(args.browser), smoke.detect_version(args.browser))
    else:
        browser = smoke.find_browser()
    if browser is None:
        print(smoke.no_browser_message(), file=sys.stderr)
        print("RESULT: SKIP (no browser). This is not a pass.", file=sys.stderr)
        return 3
    print(f"browser: {browser.family} {browser.version or '?'} at {browser.path}", file=sys.stderr)

    rows: list[Row] = []
    for case in CASES:
        if args.only and case.fixture not in args.only:
            continue
        row = Row(f"{case.fixture}", case)
        print(f"{case.fixture} @{case.width} x{args.runs} ", end="", file=sys.stderr, flush=True)
        run_row(row, FIXTURES / f"{case.fixture}.html", args.runs, args.retries, args.tmp_root, browser, True)
        rows.append(row)

    if not args.no_non_ascii and (not args.only or "ok-stub" in args.only):
        base = pathlib.Path(tempfile.mkdtemp(prefix="日本語 テスト-", dir=args.tmp_root))
        try:
            nested = base / "子 dir"
            nested.mkdir()
            page = nested / "ok-stub.html"
            smoke.write_text_lf(page, smoke.assemble_page((FIXTURES / "ok-stub.html").read_text(encoding="utf-8")))
            case = Case("ok-stub", 1366, "PASS")
            row = Row("ok-stub (non-ASCII path)", case)
            print(f"ok-stub non-ASCII path @1366 x{args.runs} ", end="", file=sys.stderr, flush=True)
            run_row(row, page, args.runs, args.retries, str(nested), browser, False)
            rows.append(row)
        finally:
            smoke._rmtree_retry(base)

    need = math.ceil(0.95 * args.runs)
    header = f"{'fixture':<28}{'width':>6}  {'expect':<5} {'codes':<34}{'match':>7}  {'med s':>6} {'p95 s':>6} {'max s':>6}  retry  outcomes"
    print()
    print(header)
    print("-" * len(header))
    all_seconds: list[float] = []
    all_spawn: list[float] = []
    all_ok = True
    for row in rows:
        all_seconds += row.seconds
        all_spawn += row.spawn
        ok = row.matched >= need
        all_ok = all_ok and ok
        codes = ",".join(row.case.codes) or "-"
        outcomes = " ".join(f"{k}x{v}" for k, v in sorted(row.outcomes.items(), key=lambda kv: -kv[1]))
        print(
            f"{row.label:<28}{row.case.width:>6}  {row.case.status:<5} {codes:<34}"
            f"{row.matched:>3}/{row.runs:<3}  {statistics.median(row.seconds):>6.2f} {percentile(row.seconds, 0.95):>6.2f} "
            f"{max(row.seconds):>6.2f}  {row.retried:>5}  {outcomes}{'' if ok else '   <-- NOT DETERMINISTIC'}"
        )
    print()
    for row in rows:
        if row.case.status == "PASS" and row.non_bg:
            print(
                f"{row.label} @{row.case.width}: reported innerWidth {sorted(row.inner_widths)}, "
                f"gate-read nonBgRatio distinct values {sorted(row.non_bg)}, leftover profile dirs {row.leftovers}"
            )
    leftovers = sum(r.leftovers for r in rows)
    median = statistics.median(all_seconds)
    p95 = percentile(all_seconds, 0.95)
    print(
        f"launches: {len(all_seconds)}  median {median:.2f}s  p95 {p95:.2f}s  max {max(all_seconds):.2f}s  "
        f"(limit {LAUNCH_SECONDS_MAX:.0f}s on p95)  retried launches {sum(r.retried for r in rows)}  leftover dirs {leftovers}"
    )
    slow = sorted(s for s in all_seconds if s > 2 * median + 1)
    print(
        f"slow launches (> 2x median + 1s): {len(slow)}"
        + (f"  slowest {slow[-1]:.2f}s" if slow else "")
        + f"  |  time inside Popen (process creation): median {statistics.median(all_spawn):.3f}s  max {max(all_spawn):.2f}s"
    )
    go = all_ok and p95 <= LAUNCH_SECONDS_MAX
    print(f"STOP/GO: {'GO' if go else 'NO-GO'}  (need >= {need}/{args.runs} per row and p95 <= {LAUNCH_SECONDS_MAX:.0f}s)")
    if args.json:
        payload = {
            "browser": {"family": browser.family, "version": browser.version},
            "runs": args.runs,
            "retries": args.retries,
            "go": go,
            "p95Seconds": round(p95, 3),
            "rows": [
                {
                    "fixture": r.label,
                    "width": r.case.width,
                    "expect": r.case.status,
                    "codes": list(r.case.codes),
                    "matched": r.matched,
                    "runs": r.runs,
                    "seconds": [round(s, 3) for s in r.seconds],
                    "spawnSeconds": [round(s, 3) for s in r.spawn],
                    "retried": r.retried,
                    "outcomes": r.outcomes,
                    "nonBgRatio": sorted(r.non_bg),
                    "innerWidths": sorted(r.inner_widths),
                }
                for r in rows
            ],
        }
        with open(args.json, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
