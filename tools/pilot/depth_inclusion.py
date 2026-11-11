#!/usr/bin/env python3
"""N0 row-level depth-inclusion audit (maintainer tool, not shipped; design A3 section 2.2, decision D-3).

The reading surface prints every fact once, at its deepest level ("the home"). That is only safe when the deeper document
contains everything the shallow-level documents print: DD contains BD contains REQ. This script checks it on the synthetic
presets, row by row and column by column, and prints one table:

    python tools/pilot/depth_inclusion.py                       # tiny, small, reference, refcjk x seeds 1-4, 6-8 (seed 5 cannot be generated)
    python tools/pilot/depth_inclusion.py --presets small --seeds 1,2 --markdown
    python tools/pilot/depth_inclusion.py --json audit.json     # the raw numbers as well

What is compared
================
Every fragment of ``bundle.fragments.items`` that the whole-model REQ or BD document prints but the whole-model DD document
does NOT print ("shallow-only" items) is matched to its deeper counterpart by the element key (the id in the first code span):

* ROW level: the key must exist in the deeper group (a missing key is a lost row);
* COLUMN level: every non-trivial cell of the shallow row must be present in the deeper unit of the same key. A cell is present
  when it is an exact *atom* of the deeper unit (a table cell, a ``- label: value`` value, a part of the ``id . kind . grade`` meta
  line, a heading). Only three declared rules relax that, each counted separately and never silent:
  COMPOSED (``relation (cardinality)`` whose two parts are atoms), COUNT (an entity's column count equals the row count of the deeper
  columns table), DOCREF (a ``REQ 3`` / ``BD 3`` pointer into another document, which names no fact).
  Trivial cells (``[N/A]``, empty) carry no information and are skipped.

Groups the design puts in the DD closure itself (single groups that span levels: ``ifc ports nfr con pol:*``, plus ``tbd ev acc``) are
checked for coverage instead: every item must be in the DD fragment set and every row must appear verbatim in the assembled DD text.
``ifc`` is additionally checked against ``e.DD`` (the design excludes it from the pages when every row can be rebuilt from the deeper
edge table). Document-structure groups (``c1 c2 chmap xmap sign dg``) are facts of no page and are listed as STRUCTURAL.

The verdict of a group is the worst over all runs. ``CONTAINED``: nothing missing. ``CONTAINED-DERIVABLE``: contained once the declared
rules apply (listed). ``NOT-CONTAINED``: something is absent from the deeper unit (the missing column is named and a fix is proposed).
``NO-DEEPER-VARIANT``: the group exists only at a shallow level; there is nothing to include it in (information, not a defect of
the inclusion).

``--simulate-fixes`` (default on) repeats the check for the groups that are not contained with the one repair the design allows
(2.2 step 1, "add the missing column to the deeper variant") applied in memory: the deeper edge row gets the label cell that the
shallow-level edge and interface rows already print. It proves the repair would be enough; it changes no repository file.

Stop-loss of the design (8.1): more than 3 groups not contained, or the duplicated print of two versions above 20 percent of the
reading surface. The script prints both numbers and the decision. Exit code 0 always (the decision is a table, not a test).

Standard library only. Writes nothing unless ``--json`` is given.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import dataclasses
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp.atlas.derive import assemble as A  # noqa: E402
from gemarc_mcp.atlas.derive import docbuild as B  # noqa: E402
from gemarc_mcp.atlas.derive import docs as X  # noqa: E402
from tests.atlas import synth  # noqa: E402

PRESETS = ("tiny", "small", "reference", "refcjk")
SEEDS = (1, 2, 3, 4, 6, 7, 8)  # seed 5 cannot be put through the generator (study notes)
LEVEL_BIT = {"REQ": 0, "BD": 1, "DD": 2}

#: shallow group -> (deeper group, how the deeper unit is read). The deeper unit of one key is the md of that key's item.
COUNTERPART = {
    "n.REQ": "n.DD", "n.BD": "n.DD",
    "e.REQ": "e.DD", "e.BD": "e.DD",
    "case": "case.DD",
    "ent": "ent.DD",
    "mach.REQ": "mach",
    "cov": "cov",  # BD rows against DD rows (same group, the other level)
}
#: groups that are document structure, not facts: they have no home on any page (design 2.2)
STRUCTURAL = ("c1", "c2", "chmap", "xmap", "sign", "dg")
#: single groups that span levels (the design takes them from the DD closure) plus the always-present row groups
CROSS_LEVEL_FIXED = ("ifc", "ports", "nfr", "con", "tbd", "ev", "acc")

CODE = re.compile(r"`([^`]+)`")
DOCREF = re.compile(r"^(REQ|BD|DD) \d+$")
COMPOSED = re.compile(r"^(.+?) \((.+)\)$")
LABELLED = re.compile(r"^[^\s`\[]+ ((?:`[^`]+`|\[[^\]]+\]).*)$")
TRIVIAL = {"", "[N/A]"}


# ------------------------------------------------------------------------------------------------ markdown readers


def split_cells(row: str) -> list[str]:
    """The cells of a Markdown table row; an escaped pipe never ends a cell."""
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, current, i = [], [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            current.append(body[i:i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def is_separator(row: str) -> bool:
    return bool(re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", row.strip()))


def table_rows(md: str) -> list[list[str]]:
    return [split_cells(line) for line in md.split("\n") if line.strip().startswith("|") and not is_separator(line)]


def first_code(text: str) -> str | None:
    found = CODE.search(text)
    return found.group(1) if found else None


def item_key(grp: str, md: str) -> str | None:
    """The element key of a fragment: the id of its first row, or of the second line of a block (``### name`` then ``id . meta``)."""
    lines = md.split("\n")
    if lines and lines[0].startswith("###"):
        return first_code(lines[1]) if len(lines) > 1 else None
    return first_code(lines[0]) if lines else None


def atoms_of(text: str) -> set[str]:
    """Every exact piece a reader finds in a unit: table cells, bullet values, meta-line parts, headings, whole lines."""
    out: set[str] = set()
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|"):
            if not is_separator(line):
                out.update(split_cells(line))
            continue
        out.add(line)
        if line.startswith("###"):
            out.add(line[3:].strip())
        body = line[2:] if line.startswith("- ") else line
        label, sep, value = body.partition(": ")
        if sep and line.startswith("- "):
            out.add(value.strip())
            out.add(body)
        if " · " in line:
            for part in line.split(" · "):
                part = part.strip()
                out.add(part)
                labelled = LABELLED.match(part)  # ``Owner `x```: the value is a fact of its own, the label is the field name
                if labelled:
                    out.add(labelled.group(1))
    return out


def head_names(head: str) -> list[str]:
    first = head.split("\n")[0] if head else ""
    return split_cells(first) if first.startswith("|") else []


# ------------------------------------------------------------------------------------------------ one bundle


@dataclasses.dataclass
class GroupStat:
    items: int = 0               # shallow-only items examined
    rows_missing: int = 0        # key without a deeper counterpart
    duplicate_keys: int = 0
    cells: int = 0               # non-trivial cells examined
    exact: int = 0
    composed: int = 0
    count: int = 0
    docref: int = 0
    absent: int = 0
    absent_by_column: Counter = dataclasses.field(default_factory=Counter)
    absent_examples: dict = dataclasses.field(default_factory=dict)
    bytes_shallow: int = 0       # md bytes of the shallow items (what a side-by-side print would add)
    deeper: str = "-"

    def verdict(self, strict: bool = False) -> str:
        if self.rows_missing or self.absent:
            return "NOT-CONTAINED"
        if strict and (self.composed or self.count or self.docref):
            return "NOT-CONTAINED(strict)"
        if self.composed or self.count or self.docref:
            return "CONTAINED-DERIVABLE"
        return "CONTAINED"


def fragment_sets(ds: dict) -> dict:
    fs = ds["fragments"]
    picked = A.selected(fs, fs["elements"])
    return {level: set(A.fragment_ids(ds, picked, LEVEL_BIT[level])) for level in A.LEVELS}


def audit_variant_pair(shallow_items, deeper_grp, deeper_items, headers, extra_atoms=None, skip_cell=None) -> GroupStat:
    """Match every shallow item to the deeper item of the same key and test row presence and every cell."""
    stat = GroupStat(deeper=deeper_grp or "-")
    deep_by_key: dict = {}
    for item in deeper_items:
        key = item_key(item["grp"], item["md"])
        if key in deep_by_key:
            stat.duplicate_keys += 1
        deep_by_key.setdefault(key, item)
    for item in shallow_items:
        stat.items += 1
        stat.bytes_shallow += len(item["md"].encode("utf-8"))
        key = item_key(item["grp"], item["md"])
        deep = deep_by_key.get(key)
        if deep is None:
            stat.rows_missing += 1
            continue
        deep_text = deep["md"]
        atoms = atoms_of(deep_text)
        if extra_atoms:
            atoms |= extra_atoms.get(key, set())
        deep_rows = table_rows(deep_text)
        for row in table_rows(item["md"]) or [None]:
            if row is None:  # a block (machine facts): every line must be a line of the deeper block
                deep_lines = {ln.strip() for ln in deep_text.split("\n")}
                for ln in item["md"].split("\n"):
                    if not ln.strip():
                        continue
                    stat.cells += 1
                    if ln.strip() in deep_lines:
                        stat.exact += 1
                    else:
                        stat.absent += 1
                        stat.absent_by_column["(block line)"] += 1
                        stat.absent_examples.setdefault("(block line)", key)
                continue
            for column, cell in enumerate(row):
                if cell in TRIVIAL:
                    continue
                name = headers[column] if column < len(headers) else f"col{column}"
                stat.cells += 1
                if cell in atoms:
                    stat.exact += 1
                    continue
                if skip_cell and column == len(headers) - 1 and skip_cell(cell, deep_text, deep_rows):
                    stat.count += 1
                    continue
                if DOCREF.match(cell):
                    stat.docref += 1
                    continue
                composed = COMPOSED.match(cell)
                if composed and composed.group(1) in atoms and composed.group(2) in atoms:
                    stat.composed += 1
                    continue
                stat.absent += 1
                stat.absent_by_column[name] += 1
                stat.absent_examples.setdefault(name, key)
    return stat


def entity_count_rule(cell, deep_text, deep_rows) -> bool:
    """COUNT: the last shallow cell (the column count) equals the number of body rows of the deeper columns table, or is 0 when
    the deeper unit says the columns are not authored."""
    if not re.fullmatch(r"\d+", cell):
        return False
    if int(cell) == 0 and "[NOT-AUTHORED:node.columns" in deep_text:
        return True
    return len(deep_rows) - 1 == int(cell)  # ``table_rows`` has the header row and no separator row


def audit_bundle(model, lang: str, simulate: bool = True) -> dict:
    bundle = B.get_bundle(model, lang, use_cache=False)
    ds = bundle.docset
    fs = ds["fragments"]
    items, groups = fs["items"], fs["groups"]
    sets = fragment_sets(ds)
    dd_text = B.document_text(bundle, "DD")
    dd_lines = {ln for ln in dd_text.split("\n")}
    by_grp: dict = defaultdict(list)
    for it in items:
        by_grp[it["grp"]].append(it)
    in_dd = lambda it: it["fid"] in sets["DD"]  # noqa: E731
    shallow_only = {g: [it for it in xs if not in_dd(it) and (it["fid"] in sets["REQ"] or it["fid"] in sets["BD"])] for g, xs in by_grp.items()}
    shallow_only = {g: xs for g, xs in shallow_only.items() if xs}
    out: dict = {"groups": {}, "unmapped_shallow": {}, "cross": {}, "structural": {}, "sums": {}}

    # ---- level-suffixed variants and the other shallow-only groups
    ctx = X.Ctx(model, lang, bundle.units) if simulate else None
    label_atoms: dict = {}
    if ctx is not None:
        for edge in ctx.ix.edges:
            eid = str(edge.get("id") or "")
            row = X.edge_row_min(ctx, edge)
            label_atoms[eid] = {split_cells(row)[-1]}
    for grp, shallow in sorted(shallow_only.items()):
        if grp in STRUCTURAL:
            out["structural"][grp] = {"items": len(shallow), "bytes": sum(len(i["md"].encode()) for i in shallow)}
            continue
        counterpart = COUNTERPART.get(grp)
        if counterpart is None:
            out["unmapped_shallow"][grp] = {"items": len(shallow), "bytes": sum(len(i["md"].encode()) for i in shallow)}
            continue
        if counterpart == grp:  # cov: BD rows against DD rows
            deeper_items = [it for it in by_grp[grp] if in_dd(it)]
        else:
            deeper_items = [it for it in by_grp[counterpart] if in_dd(it)]
        headers = head_names(groups.get(grp, {}).get("head", ""))
        skip = entity_count_rule if grp == "ent" else None
        stat = audit_variant_pair(shallow, counterpart, deeper_items, headers, None, skip)
        entry = {"stat": stat}
        if simulate and stat.verdict() == "NOT-CONTAINED" and grp.startswith("e."):
            fixed = audit_variant_pair(shallow, counterpart, deeper_items, headers, label_atoms, skip)
            entry["after_fix"] = fixed
        out["groups"][grp] = entry

    # ---- cross-level single groups: coverage of the DD closure, verbatim in the assembled DD text
    for grp in ("ifc", "ports", "nfr", "con", "tbd", "ev", "acc") + tuple(sorted(g for g in by_grp if g.startswith("pol:"))):
        xs = by_grp.get(grp, [])
        if not xs:
            continue
        rows = [ln for it in xs for ln in it["md"].split("\n") if ln.strip()]
        out["cross"][grp] = {
            "items": len(xs), "by_level": dict(Counter(i["level"] for i in xs)),
            "in_dd": sum(1 for i in xs if in_dd(i)),
            "rows": len(rows), "rows_verbatim": sum(1 for ln in rows if ln in dd_lines),
            "bytes": sum(len(i["md"].encode()) for i in xs),
        }
    # ---- ifc rebuilt from e.DD (+ the label column the repair adds)
    if by_grp.get("ifc"):
        deeper = [it for it in by_grp["e.DD"] if in_dd(it)]
        headers = head_names(groups["ifc"]["head"])
        plain = audit_variant_pair(by_grp["ifc"], "e.DD", deeper, headers)
        entry = {"stat": plain}
        if simulate:
            entry["after_fix"] = audit_variant_pair(by_grp["ifc"], "e.DD", deeper, headers, label_atoms)
        out["ifc_vs_edge_dd"] = entry
    # ---- bytes of the reading surface = the fragments the DD document prints, without the structure groups
    homes = sum(len(items[f]["md"].encode()) for f in sets["DD"] if items[f]["grp"] not in STRUCTURAL)
    out["sums"] = {"dd_fragment_bytes": homes, "items": len(items), "dd_items": len(sets["DD"]), "dd_doc_bytes": len(dd_text.encode()),
                     "issues": len(ds.get("issues", []))}
    # req: REQ-only group, listed so that nothing is silent
    return out


# ------------------------------------------------------------------------------------------------ the runs


def build_model(preset: str, seed: int):
    if preset == "refcjk":
        return synth.generate(dataclasses.replace(synth.PRESETS["reference"], lang="cjk"), seed)
    return synth.generate(preset, seed)


def merge(runs: list) -> dict:
    """Fold the per-run results into one row per group (the worst verdict wins, counts add up)."""
    table: dict = {}
    order = ["n.REQ", "n.BD", "e.REQ", "e.BD", "mach.REQ", "case", "ent", "cov", "req"]
    for run in runs:
        for grp, entry in run["result"]["groups"].items():
            row = table.setdefault(grp, {"stat": GroupStat(), "after_fix": GroupStat(), "has_fix": False, "runs": Counter()})
            row["stat"] = add_stats(row["stat"], entry["stat"])
            row["runs"][entry["stat"].verdict()] += 1
            if "after_fix" in entry:
                row["has_fix"] = True
                row["after_fix"] = add_stats(row["after_fix"], entry["after_fix"])
        for grp, info in run["result"]["unmapped_shallow"].items():
            row = table.setdefault(grp, {"stat": GroupStat(), "after_fix": GroupStat(), "has_fix": False, "runs": Counter()})
            row["unmapped"] = row.get("unmapped", 0) + info["items"]
            row["unmapped_bytes"] = row.get("unmapped_bytes", 0) + info["bytes"]
            row["runs"]["NO-DEEPER-VARIANT"] += 1
    cross: dict = {}
    for run in runs:
        for grp, info in run["result"]["cross"].items():
            c = cross.setdefault(grp, Counter())
            for k in ("items", "in_dd", "rows", "rows_verbatim", "bytes"):
                c[k] += info[k]
    structural: Counter = Counter()
    for run in runs:
        for grp, info in run["result"]["structural"].items():
            structural[grp] += info["items"]
    ifc = {"stat": GroupStat(), "after_fix": GroupStat()}
    for run in runs:
        entry = run["result"].get("ifc_vs_edge_dd")
        if entry:
            ifc["stat"] = add_stats(ifc["stat"], entry["stat"])
            ifc["after_fix"] = add_stats(ifc["after_fix"], entry["after_fix"])
    ordered = {g: table[g] for g in order if g in table}
    ordered.update({g: table[g] for g in sorted(table) if g not in ordered})
    return {"groups": ordered, "cross": cross, "ifc": ifc, "structural": structural}


def add_stats(a: GroupStat, b: GroupStat) -> GroupStat:
    out = GroupStat(deeper=b.deeper if b.deeper != "-" else a.deeper)
    for name in ("items", "rows_missing", "duplicate_keys", "cells", "exact", "composed", "count", "docref", "absent", "bytes_shallow"):
        setattr(out, name, getattr(a, name) + getattr(b, name))
    out.absent_by_column = a.absent_by_column + b.absent_by_column
    out.absent_examples = {**b.absent_examples, **a.absent_examples}
    return out


def stat_dict(stat: GroupStat) -> dict:
    """The numbers of a group as plain JSON (``dataclasses.asdict`` would rebuild a Counter from its items)."""
    out = {f.name: getattr(stat, f.name) for f in dataclasses.fields(stat)}
    out["absent_by_column"] = dict(sorted(stat.absent_by_column.items()))
    out["absent_examples"] = dict(sorted(stat.absent_examples.items()))
    out["verdict"] = stat.verdict()
    out["verdict_strict"] = stat.verdict(strict=True)
    return out


def fmt_columns(stat: GroupStat) -> str:
    return ", ".join(f"{name} x{n}" for name, n in sorted(stat.absent_by_column.items())) or "-"


def run_numbers(result: dict) -> dict:
    """Per run: the groups not contained before and after the simulated repair, and the bytes a side-by-side print would add."""
    before, after = [], []
    upper = remaining = 0
    for grp, entry in result["groups"].items():
        stat = entry["stat"]
        if stat.verdict() != "NOT-CONTAINED":
            continue
        before.append(grp)
        upper += stat.bytes_shallow
        fixed = entry.get("after_fix")
        if fixed is None or fixed.verdict() == "NOT-CONTAINED":
            after.append(grp)
            remaining += stat.bytes_shallow
    cross_bad = [g for g, c in result["cross"].items() if c["items"] != c["in_dd"] or c["rows"] != c["rows_verbatim"]]
    return {"before": before, "after": after + cross_bad, "cross_bad": cross_bad, "upper": upper, "remaining": remaining,
            "home": result["sums"]["dd_fragment_bytes"]}


def family_of(grp: str) -> str:
    return grp.split(".")[0] if grp[:2] in ("n.", "e.") else grp


def render(runs: list, merged: dict, args) -> tuple[str, dict]:
    lines: list[str] = []
    add = lines.append
    add(f"runs: {len(runs)} ({', '.join(args.presets)} x seeds {','.join(map(str, args.seeds))}; language {args.lang})")
    sums = Counter()
    for run in runs:
        sums["items"] += run["result"]["sums"]["items"]
        sums["dd_fragment_bytes"] += run["result"]["sums"]["dd_fragment_bytes"]
    add(f"fragment items audited over all runs: {sums['items']}; DD fragment bytes (the reading surface without the structure groups): {sums['dd_fragment_bytes']}")
    add("")
    add("A. Level-suffixed variants and the other shallow-only groups (shallow = printed by REQ or BD, not by DD), summed over all runs")
    add("| group | deeper | shallow items | rows missing | cells | exact | composed | count | docref | absent | verdict | missing column(s) | examples (first key) |")
    add("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|---|---|")
    strict_not_contained: list[str] = []
    for grp, row in merged["groups"].items():
        stat = row["stat"]
        if "unmapped" in row:
            add(f"| {grp} | - | {row['unmapped']} | - | - | - | - | - | - | - | NO-DEEPER-VARIANT | exists only at a shallow level ({row['unmapped_bytes']} B in total) | - |")
            continue
        if stat.verdict(strict=True) != "CONTAINED":
            strict_not_contained.append(grp)
        examples = "; ".join(f"{name}: {key}" for name, key in sorted(stat.absent_examples.items())) or "-"
        add(f"| {grp} | {stat.deeper} | {stat.items} | {stat.rows_missing} | {stat.cells} | {stat.exact} | {stat.composed} | {stat.count} | {stat.docref} | {stat.absent} | "
            f"{stat.verdict()} | {fmt_columns(stat)} | {examples} |")
    add("")
    add(f"Structural groups printed by REQ or BD only (document skeleton, no fact, no home on any page): {dict(sorted(merged['structural'].items())) or 'none'}")
    add("")
    add("B. Groups taken from the DD closure (single groups spanning levels) and the always-present row groups, summed over all runs")
    add("| group | items | by shallowest level | in DD set | rows | rows verbatim in assembled DD | verdict |")
    add("|---|--:|---|--:|--:|--:|---|")
    for grp, c in sorted(merged["cross"].items()):
        ok = c["items"] == c["in_dd"] and c["rows"] == c["rows_verbatim"]
        levels = Counter()
        for run in runs:
            for level, n in run["result"]["cross"].get(grp, {}).get("by_level", {}).items():
                levels[level] += n
        add(f"| {grp} | {c['items']} | {dict(levels)} | {c['in_dd']} | {c['rows']} | {c['rows_verbatim']} | {'CONTAINED' if ok else 'NOT-CONTAINED'} |")
    add("")
    add("C. ifc rebuilt from e.DD (design 2.2: ifc leaves the pages when every row is rebuildable; a decision, not an inclusion failure)")
    ifc = merged["ifc"]["stat"]
    fixed = merged["ifc"]["after_fix"]
    add(f"- as it is: cells {ifc.cells}, absent {ifc.absent} ({fmt_columns(ifc)}), rows missing {ifc.rows_missing}")
    add(f"- with the label column added to e.DD (simulated): cells {fixed.cells}, absent {fixed.absent} ({fmt_columns(fixed)}), rows missing {fixed.rows_missing}")
    ifc_rebuildable_after_fix = fixed.absent == 0 and fixed.rows_missing == 0
    add(f"- verdict: ifc {'IS' if ifc_rebuildable_after_fix else 'is NOT'} rebuildable from e.DD after the repair; "
        f"{'it may go into the exclusion table of Q01' if ifc_rebuildable_after_fix else 'it must be printed as an interface table on the S pages'}")
    add("")
    add("D. Simulated repair (the label cell of the shallow-level edge rows appended to e.DD in memory; no repository file is touched)")
    for grp, row in merged["groups"].items():
        if row["has_fix"]:
            fixed_stat = row["after_fix"]
            add(f"- {grp}: absent before {row['stat'].absent} ({fmt_columns(row['stat'])}); after {fixed_stat.absent}, rows missing {fixed_stat.rows_missing} -> {fixed_stat.verdict()}")
    add("")
    add("E. Stop-loss per run (design 8.1 / 2.2: more than 3 groups not contained, or a side-by-side print of the unrepairable groups above 20 percent of the reading surface)")
    add("| preset | runs | groups not contained (before repair) | after the simulated repair | side-by-side bytes if NOT repaired: min-max percent of DD fragment bytes | after repair |")
    add("|---|--:|---|---|---|---|")
    per_preset: dict = defaultdict(list)
    for run in runs:
        per_preset[run["preset"]].append(run_numbers(run["result"]))
    worst_groups: set = set()
    worst_after: set = set()
    worst_upper = 0.0
    worst_remaining = 0.0
    for preset, numbers in per_preset.items():
        before = sorted({family_of(g) for n in numbers for g in n["before"]})
        after = sorted({family_of(g) for n in numbers for g in n["after"]})
        ratios = [n["upper"] / n["home"] * 100 if n["home"] else 0.0 for n in numbers]
        rest = [n["remaining"] / n["home"] * 100 if n["home"] else 0.0 for n in numbers]
        worst_groups |= set(before)
        worst_after |= set(after)
        worst_upper = max(worst_upper, max(ratios))
        worst_remaining = max(worst_remaining, max(rest))
        add(f"| {preset} | {len(numbers)} | {before or '-'} | {after or '-'} | {min(ratios):.1f}-{max(ratios):.1f} | {max(rest):.1f} |")
    add("")
    cross_bad = sorted({g for run in runs for g in run_numbers(run["result"])["cross_bad"]})
    add(f"- distinct groups not contained over all runs, before any repair: {sorted(worst_groups)} -> {len(worst_groups)}")
    add(f"- distinct groups still not contained after the simulated repair (cross-level groups not fully in the DD closure: {cross_bad or 'none'}): "
        f"{sorted(worst_after)} -> {len(worst_after)}")
    add(f"- strict reading (derivable cells and document pointers counted as absent): {sorted(strict_not_contained)} -> {len(strict_not_contained)} groups")
    add(f"- side-by-side print if NOTHING were repaired: worst run {worst_upper:.1f} percent of the DD fragment bytes (information: the fallback of design 2.2 step 2 is not needed when the repair holds)")
    add(f"- side-by-side print after the repair: worst run {worst_remaining:.1f} percent")
    sites = sorted({(row["stat"].deeper, column) for row in merged["groups"].values() if row["stat"].verdict() == "NOT-CONTAINED" for column in row["stat"].absent_by_column})
    add(f"- repair sites needed (deeper group, missing column): {sites} -> {len(sites)} (design allows 3)")
    blocked = len(worst_groups) > 3 or len(sites) > 3 or worst_remaining > 20.0 or bool(cross_bad)
    add(f"- decision: {'STOP (blocked)' if blocked else 'CONTINUE (inside the stop-loss)'}")
    summary = {"not_contained_before_repair": sorted(worst_groups), "not_contained_after_repair": sorted(worst_after), "strict": sorted(strict_not_contained),
               "repair_sites": [list(x) for x in sites], "worst_side_by_side_percent_unrepaired": round(worst_upper, 2),
               "worst_side_by_side_percent_after_repair": round(worst_remaining, 2), "cross_level_not_in_closure": cross_bad, "blocked": blocked,
               "ifc_rebuildable_after_fix": ifc_rebuildable_after_fix}
    return "\n".join(lines), summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--presets", default=",".join(PRESETS))
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    ap.add_argument("--lang", default="zh-CN")
    ap.add_argument("--no-simulate", action="store_true")
    ap.add_argument("--json", default="", help="also write the merged numbers to this file")
    ap.add_argument("--markdown", action="store_true", help="accepted for symmetry; the output is Markdown already")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args.presets = [p for p in args.presets.split(",") if p]
    args.seeds = [int(s) for s in args.seeds.split(",") if s]
    runs = []
    for preset in args.presets:
        for seed in args.seeds:
            model = build_model(preset, seed)
            result = audit_bundle(model, args.lang, simulate=not args.no_simulate)
            runs.append({"preset": preset, "seed": seed, "result": result})
            print(f"  audited {preset} seed {seed}", file=sys.stderr)
    merged = merge(runs)
    text, summary = render(runs, merged, args)
    print(text)
    if args.json:
        payload = {"summary": summary, "runs": [{"preset": r["preset"], "seed": r["seed"], "sums": r["result"]["sums"]} for r in runs],
                   "groups": {g: stat_dict(row["stat"]) for g, row in merged["groups"].items()},
                   "cross": {g: dict(c) for g, c in merged["cross"].items()}}
        Path(args.json).write_text(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
