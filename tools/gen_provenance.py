#!/usr/bin/env python3
"""Generate ``src/gemarc_mcp/atlas/PROVENANCE.md`` from the lane tables in ``src/gemarc_mcp/atlas/provenance/``.

Inputs (all tab separated, UTF-8, first line is the header):

``provenance/<lane>.tsv``        one row per file: ``path, origin, reference_unit, treatment, deviations``
                                 ``origin`` is ported-semantic | ported-structural | clean-room | new;
                                 ``reference_unit`` names a generic module of the reference ("engine core"), never an id,
                                 a file path or a data value, and is ``-`` exactly for ``new`` files;
                                 ``path`` is relative to the repository root, with forward slashes.
``provenance/authorisation.tsv`` ``scope, status, by, date, note``: the authorisation record. ``ported-code`` and
                                 ``publication`` are the two scopes; flipping ``publication`` to GRANTED is a user edit.

Usage (from the repository root)::

    python tools/gen_provenance.py            # rewrite PROVENANCE.md
    python tools/gen_provenance.py --check    # exit 1 when PROVENANCE.md is stale or a table is invalid

The output is deterministic: rows sorted by path, LF newlines, no timestamps. Standard library only.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATLAS_DIR = ROOT / "src" / "gemarc_mcp" / "atlas"
PROVENANCE_DIR = ATLAS_DIR / "provenance"
OUTPUT = ATLAS_DIR / "PROVENANCE.md"
AUTHORISATION_FILE = "authorisation.tsv"

COLUMNS = ("path", "origin", "reference_unit", "treatment", "deviations")
AUTH_COLUMNS = ("scope", "status", "by", "date", "note")
ORIGINS = ("ported-semantic", "ported-structural", "clean-room", "new")
ORIGIN_MEANING = {
    "ported-semantic": "behaviour of a generic reference module re-implemented here (no code copied)",
    "ported-structural": "generic reference code carried over and genericised (constants and domain names removed)",
    "clean-room": "written from a specification of the function, without reading the reference implementation",
    "new": "no reference counterpart",
}
UNIT_RE = re.compile(r"^[a-z][a-z0-9 ,()+-]{1,79}$")
LANE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
SCOPES = ("ported-code", "publication")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProvenanceError(ValueError):
    """One or more problems in the provenance tables (``args[0]`` lists all of them)."""


@dataclass(frozen=True)
class Row:
    lane: str
    path: str
    origin: str
    reference_unit: str
    treatment: str
    deviations: str


def read_text(path: Path) -> str:
    """UTF-8 text with the BOM removed and CRLF/CR folded to LF (checkout independent)."""
    text = path.read_bytes().decode("utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _table(path: Path, columns: tuple[str, ...], problems: list[str]) -> list[list[str]]:
    lines = [line for line in read_text(path).split("\n") if line.strip() != ""]
    if not lines or tuple(lines[0].split("\t")) != columns:
        problems.append(f"{path.name}: the first line must be the header {'<TAB>'.join(columns)}")
        return []
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        cells = line.split("\t")
        if len(cells) != len(columns):
            problems.append(f"{path.name}:{number}: expected {len(columns)} tab separated cells, found {len(cells)}")
            continue
        rows.append([cell.strip() for cell in cells])
        for column, cell in zip(columns, rows[-1]):
            if cell == "":
                problems.append(f"{path.name}:{number}: empty {column} (write '-' when there is nothing to say)")
    return rows


def _path_problem(value: str) -> str | None:
    if not value or value.startswith("/") or "\\" in value or ".." in value.split("/") or re.match(r"^[A-Za-z]:", value):
        return "path must be relative to the repository root with forward slashes and no '..'"
    return None


def load_rows(provenance_dir: Path = PROVENANCE_DIR) -> list[Row]:
    """All lane rows, validated. Raises ``ProvenanceError`` listing every problem."""
    problems: list[str] = []
    rows: list[Row] = []
    seen: dict[str, str] = {}
    for table in sorted(provenance_dir.glob("*.tsv")):
        if table.name == AUTHORISATION_FILE:
            continue
        lane = table.stem
        if not LANE_RE.match(lane):
            problems.append(f"{table.name}: the lane name (file stem) must match {LANE_RE.pattern}")
        for number, cells in enumerate(_table(table, COLUMNS, problems), start=2):
            path, origin, unit, treatment, deviations = cells
            where = f"{table.name}:{number}"
            if (issue := _path_problem(path)) is not None:
                problems.append(f"{where}: {issue}")
            if origin not in ORIGINS:
                problems.append(f"{where}: origin {origin!r} is not one of {', '.join(ORIGINS)}")
            elif origin == "new" and unit != "-":
                problems.append(f"{where}: a 'new' file has no reference unit; write '-'")
            elif origin != "new" and not UNIT_RE.match(unit):
                problems.append(f"{where}: reference_unit must be a generic lower-case module name (letters, digits, spaces), got {unit!r}")
            if path in seen:
                problems.append(f"{where}: {path} already has a row in {seen[path]}")
            seen[path] = table.name
            rows.append(Row(lane, path, origin, unit, treatment, deviations))
    if problems:
        raise ProvenanceError("\n".join(problems))
    return sorted(rows, key=lambda r: (r.path, r.lane))


def load_authorisation(provenance_dir: Path = PROVENANCE_DIR) -> dict[str, dict[str, str]]:
    problems: list[str] = []
    table = provenance_dir / AUTHORISATION_FILE
    if not table.is_file():
        raise ProvenanceError(f"{AUTHORISATION_FILE} is missing")
    records: dict[str, dict[str, str]] = {}
    for number, cells in enumerate(_table(table, AUTH_COLUMNS, problems), start=2):
        record = dict(zip(AUTH_COLUMNS, cells))
        where = f"{AUTHORISATION_FILE}:{number}"
        if record["scope"] not in SCOPES:
            problems.append(f"{where}: scope must be one of {', '.join(SCOPES)}")
        if record["status"] not in ("GRANTED", "PENDING"):
            problems.append(f"{where}: status must be GRANTED or PENDING")
        if record["status"] == "GRANTED" and (record["by"] == "-" or not DATE_RE.match(record["date"])):
            problems.append(f"{where}: a GRANTED record names who granted it and a yyyy-mm-dd date")
        if record["status"] == "PENDING" and (record["by"] != "-" or record["date"] != "-"):
            problems.append(f"{where}: a PENDING record has by and date '-'")
        if record["scope"] in records:
            problems.append(f"{where}: duplicate scope {record['scope']}")
        records[record["scope"]] = record
    for scope in SCOPES:
        if scope not in records:
            problems.append(f"{AUTHORISATION_FILE}: no row for scope {scope}")
    if problems:
        raise ProvenanceError("\n".join(problems))
    return records


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render(rows: list[Row], authorisation: dict[str, dict[str, str]]) -> str:
    """The text of PROVENANCE.md (LF, deterministic)."""
    ported, publication = authorisation["ported-code"], authorisation["publication"]
    out: list[str] = []
    add = out.append
    add("# GemArc Atlas: provenance and authorisation record")
    add("")
    add("Generated by `python tools/gen_provenance.py` from `src/gemarc_mcp/atlas/provenance/*.tsv`; do not edit by hand.")
    add("A test (`tests/atlas/test_provenance.py`) fails when a file under `src/gemarc_mcp/atlas/` has no row, a row points to a")
    add("missing file, or this file differs from the generated text. Add rows to your lane's table, then regenerate.")
    add("")
    add("## Authorisation")
    add("")
    add(f"- Ported code: {ported['status']} by {ported['by']} on {ported['date']}. {ported['note']}")
    if publication["status"] == "GRANTED":
        add(f"- Publication: GRANTED by {publication['by']} on {publication['date']}. {publication['note']}")
    else:
        add(f"- Publication (push, deploy, hosted routes, kit download): NOT authorised. {publication['note']}")
    add("")
    if publication["status"] == "GRANTED":
        add(f"PUBLICATION: GRANTED by {publication['by']} on {publication['date']}, scope {publication['note']}")
    else:
        add("PUBLICATION: PENDING")
    add("")
    add("`tests/atlas/test_atlas_publication_gate.py` keys on the `PUBLICATION:` line above.")
    add("")
    add("## Origins")
    add("")
    for origin in ORIGINS:
        add(f"- `{origin}`: {ORIGIN_MEANING[origin]}")
    add("")
    add("No data of the reference system (ids, names, prose, constants of its model) is carried over; fixtures and tests use")
    add("synthetic id families. `reference unit` is a generic module name, never an id or a path.")
    add("")
    counts = {origin: sum(1 for r in rows if r.origin == origin) for origin in ORIGINS}
    add("## Files")
    add("")
    add(f"{len(rows)} rows: " + ", ".join(f"{counts[o]} {o}" for o in ORIGINS) + ".")
    add("")
    add("| path | origin | reference unit | treatment | deviations | lane |")
    add("|---|---|---|---|---|---|")
    for r in rows:
        add(f"| `{_cell(r.path)}` | {r.origin} | {_cell(r.reference_unit)} | {_cell(r.treatment)} | {_cell(r.deviations)} | {r.lane} |")
    add("")
    return "\n".join(out)


def _is_docstring_only_module(path: Path) -> bool:
    text = read_text(path)
    if text.strip() == "":
        return True
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return False
    return all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) for node in body)


def files_requiring_rows(atlas_dir: Path = ATLAS_DIR, root: Path = ROOT) -> list[str]:
    """Repository-relative paths of every file under the atlas directory that needs a provenance row.

    Excluded: ``__pycache__`` trees and ``*.pyc``, ``PROVENANCE.md`` itself, the ``provenance/*.tsv`` tables, and
    ``__init__.py`` files that are empty or hold only a docstring.
    """
    found: list[str] = []
    for path in sorted(atlas_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(atlas_dir)
        if relative.as_posix() == "PROVENANCE.md":
            continue
        if relative.parts[0] == "provenance" and path.suffix == ".tsv":
            continue
        if path.name == "__init__.py" and _is_docstring_only_module(path):
            continue
        found.append(path.relative_to(root).as_posix())
    return found


def problems_of(root: Path = ROOT) -> list[str]:
    """Missing rows, rows pointing nowhere, and a stale PROVENANCE.md, as human readable lines."""
    atlas_dir = root / "src" / "gemarc_mcp" / "atlas"
    prov_dir = atlas_dir / "provenance"
    issues: list[str] = []
    try:
        rows = load_rows(prov_dir)
        authorisation = load_authorisation(prov_dir)
    except ProvenanceError as ex:
        return str(ex).split("\n")
    row_paths = {r.path for r in rows}
    for path in files_requiring_rows(atlas_dir, root):
        if path not in row_paths:
            issues.append(f"no provenance row for {path}")
    for r in rows:
        if not (root / r.path).is_file():
            issues.append(f"{r.lane}.tsv: row points to a missing file: {r.path}")
    output = atlas_dir / "PROVENANCE.md"
    if not output.is_file():
        issues.append("PROVENANCE.md is missing (run python tools/gen_provenance.py)")
    elif read_text(output) != render(rows, authorisation):
        issues.append("PROVENANCE.md is stale (run python tools/gen_provenance.py)")
    return issues


def write(root: Path = ROOT) -> Path:
    atlas_dir = root / "src" / "gemarc_mcp" / "atlas"
    prov_dir = atlas_dir / "provenance"
    text = render(load_rows(prov_dir), load_authorisation(prov_dir))
    target = atlas_dir / "PROVENANCE.md"
    target.write_bytes(text.encode("utf-8"))
    return target


def main(argv: list[str]) -> int:
    if "--check" in argv:
        issues = problems_of()
        for issue in issues:
            print(issue)
        return 1 if issues else 0
    try:
        print("wrote", write().relative_to(ROOT).as_posix())
    except ProvenanceError as ex:
        print(ex, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
