"""Measure the browser gate on BUILT pages (design M4 acceptance (b)): N runs per page and width, retries OFF, a matrix and a verdict.

Not part of the test suite (it takes minutes); ``test_gate_html.py`` runs one launch per width.

    python tests/atlas/gate_html_matrix.py --runs 20
    python tests/atlas/gate_html_matrix.py --runs 3 --only reference --fixtures
    python tests/atlas/gate_html_matrix.py --runs 20 --json out.json

Pages (all synthetic, built here): ``reference`` (sealed reference-scale project with runs embedded, the page ``build`` writes),
``unsealed`` (the same model without any seal: the 4D/5D empty state), ``probe`` (random id family, 400 nodes, unknown kind / trust /
building, no cases), ``hostile`` (hostile markup in every free-text field), ``unknown-building``. Widths: 1024, 1366, 1600 (innerWidth).
With ``--fixtures`` the calibration fixtures of ``gate_fixtures/MATRIX.json`` run N times at their widths too and must give exactly the
expected verdict and finding names.

Verdict: GO when every row passed in at least ceil(0.95 * runs) runs (19 of 20). Exit 0 GO, 1 NO-GO, 3 no browser (SKIP, never GO).
Set TEMP and TMP to a scratch directory when you want to see anything the browser drops outside its profile.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import statistics
import sys
import tempfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from atlas import synth  # noqa: E402
from atlas.test_build_page import hostile_model  # noqa: E402
from atlas.test_oracle_support import consistent, slice_texts  # noqa: E402
from gemarc_mcp.atlas import build, tools  # noqa: E402
from gemarc_mcp.atlas import project as pj  # noqa: E402
from gemarc_mcp.atlas.gate import smoke  # noqa: E402

FIXTURES = ROOT / "tests" / "atlas" / "gate_fixtures"
WIDTHS = (1024, 1366, 1600)
PAGES = ("reference", "unsealed", "probe", "hostile", "unknown-building")


def percentile(values: list, fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def project_page(work: pathlib.Path, name: str, sealed: bool) -> pathlib.Path:
    (work / f"ws-{name}").mkdir()
    ws = pj.get_workspace(work / f"ws-{name}")
    model = consistent(synth.generate("reference", 3))
    assert tools.init(ws, "atlas", "Reference scale", "asis", ["en-US"], repos=model["meta"]["repos"])["status"] == "created"
    for slice_name, text in slice_texts(model).items():
        assert tools.put(ws, "atlas", slice_name, text)["status"] == "committed", slice_name
    if sealed:
        reply = tools.build(ws, "atlas")
        assert reply["status"] == "ok", reply
        return build.dist_dir(pj.project_paths(ws, "atlas")) / "index.html"
    page = work / f"{name}.html"
    page.write_text(build.render(model).html, encoding="utf-8", newline="\n")      # no seal, no provider reply: the empty state
    return page


def make_pages(work: pathlib.Path, only: list) -> dict:
    pages = {}
    wanted = only or list(PAGES)
    for name in wanted:
        if name == "reference":
            pages[name] = project_page(work, name, True)
        elif name == "unsealed":
            pages[name] = project_page(work, name, False)
        else:
            if name == "probe":
                model = synth.edge_case("big400", 1)
                synth._inject_unknown_values(model)
                model["nodes"][0]["roomId"] = "no-such-room"
                model["cases"], model["interventions"] = [], []
            elif name == "hostile":
                model = hostile_model(synth.generate("small", 2))
            elif name == "unknown-building":
                model = synth.generate("small", 4)
                model["nodes"][0]["roomId"] = "no-such-room"
            else:
                raise SystemExit(f"unknown page {name!r}; known: {', '.join(PAGES)}")
            page = work / f"{name}.html"
            page.write_text(build.render(model).html, encoding="utf-8", newline="\n")
            pages[name] = page
    return pages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--only", action="append", default=[], help="page name (repeatable)")
    parser.add_argument("--fixtures", action="store_true", help="also run the calibration fixtures of MATRIX.json")
    parser.add_argument("--json", default="", help="write the raw rows here")
    args = parser.parse_args(argv)
    browser = smoke.find_browser()
    if browser is None:
        print("SKIPPED, NOT PASSED: no Edge or Chrome found; nothing was measured.", file=sys.stderr)
        return 3
    need = math.ceil(0.95 * args.runs)
    rows = []
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1"}):
        work = pathlib.Path(tmp)
        pages = make_pages(work, args.only)
        jobs = [(name, page, w, "PASS", [], False) for name, page in pages.items() for w in WIDTHS]
        if args.fixtures:
            matrix = json.loads((FIXTURES / "MATRIX.json").read_text(encoding="utf-8"))
            for fixture in matrix["fixtures"]:
                if fixture.get("pending"):
                    continue
                if "file" in fixture:
                    for width, want in fixture["expect"].items():
                        jobs.append((fixture["name"], FIXTURES / fixture["file"], int(width), want["status"], sorted(want["codes"]), True))
        for name, page, width, status, codes, inline in jobs:
            seconds, matched, outcomes = [], 0, {}
            for _ in range(args.runs):
                res = smoke.run_gate(page, width, browser=browser, tmp_root=work / "gate", retries=args.retries, inline_selftest=inline)
                key = f"{res.status}{res.codes or ''}"
                outcomes[key] = outcomes.get(key, 0) + 1
                seconds.append(res.seconds)
                if res.status == status and res.codes == codes and (res.result or {}).get("innerWidth", width) == width:
                    matched += 1
                print("." if res.status == status else "x", end="", file=sys.stderr, flush=True)
            print(file=sys.stderr)
            rows.append({"page": name, "width": width, "expect": status + (str(codes) if codes else ""), "matched": matched, "runs": args.runs, "outcomes": outcomes,
                         "median": round(statistics.median(seconds), 2), "p95": round(percentile(seconds, 0.95), 2), "max": round(max(seconds), 2)})
    print(f"browser {browser.family} {browser.version}; runs {args.runs}; retries {args.retries}; need {need}/{args.runs} per row")
    print(f"{'page':<18}{'width':>6} {'expect':<28}{'ok':>7}  {'median':>6} {'p95':>6} {'max':>6}  outcomes")
    for r in rows:
        print(f"{r['page']:<18}{r['width']:>6} {r['expect']:<28}{r['matched']:>3}/{r['runs']:<3}  {r['median']:>6} {r['p95']:>6} {r['max']:>6}  {r['outcomes']}")
    bad = [r for r in rows if r["matched"] < need]
    launches = sum(r["runs"] for r in rows)
    print(f"{launches} launches; rows below {need}/{args.runs}: {len(bad)}; {'GO' if not bad else 'NO-GO'}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
