"""``ids.tsv`` and ``refs.tsv`` (design A3 2.7.2, 2.7.3): the two lookup files an AI only greps.

``ids.tsv``: one line per (id, kind) home and one per view line; columns ``id kind role path line hdr name``; every line at most 400
characters by construction (the name is cut at 120 characters). ``refs.tsv``: one line per reference a home prints, columns
``target field owner path line``, ``field`` one of ``req ev test tbd``.

A reference is listed when the model says that the owner refers to the target AND the owner's home prints the target as a code span:
the tables cap their id lists (an edge row shows four evidence ids, a coverage row eight elements), and a reference that the page does
not show has no line to point at. ``unprinted`` counts those, so the loss is measured and not silent.
"""
from __future__ import annotations

from ..derive import i18n as T
from ..derive import policy as P
from .budgets import BUDGETS
from .model import SCHEMA_IDS, SCHEMA_REFS
from .pages import display_name
from .source import CODE, Source

HOME = "home"
LINE_MAX = BUDGETS["tsv_line_chars"]
NAME_MAX = 120
REF_OWNERS = ("node", "edge", "case", "machine", "requirement", "tbd")
BLOCK_OWNERS = ("node", "case", "machine")
KIND_ORDER = ("node", "edge", "case", "event", "intervention", "requirement", "test", "tbd", "evidence", "machine", "transition", "entity", "room", "floor", "building",
              "diagram")


def ids_rows(src: Source, pages: list) -> list:
    """The rows of ``ids.tsv`` as tuples ``(id, kind, role, path, line, hdr, name)`` in file order."""
    rows: list = []
    for r in pages:
        for ident, kind, role, line, hdr in r.marks:
            rows.append((ident, kind, role, r.path, line, hdr, display_name(src, kind, ident)))
    for unit in src.bundle.get("diagrams") or []:
        if isinstance(unit, dict) and isinstance(unit.get("id"), str):
            rows.append((unit["id"], "diagram", HOME, f"diagrams/{T.dg_id(unit['id'])}.mmd", 1, 1, " ".join(T.clean(unit.get("title") or "").split())[:NAME_MAX]))
    kind_pos = {k: n for n, k in enumerate(KIND_ORDER)}
    rows.sort(key=lambda r: (r[0], kind_pos.get(r[1], 99), r[2] != HOME, r[2], r[3], r[4]))
    return rows


def ids_text(src: Source, rows: list) -> str:
    out = [f"# schema {SCHEMA_IDS} m={src.stamp} lang={src.lang}", "# id\tkind\trole\tpath\tline\thdr\tname"]
    for ident, kind, role, path, line, hdr, name in rows:
        out.append("\t".join((ident, kind, role, path, str(line), str(hdr), name.replace("\t", " ").replace("\n", " "))))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------------- refs

def _owner_span(lines: list, line: int, kind: str) -> tuple:
    """First and last line (1-based, inclusive) of an owner's home inside a page: the row itself, or the block (from the heading line above
    the id line up to the next blank line or heading)."""
    if kind not in BLOCK_OWNERS:
        return line, line
    first = max(1, line - 1)
    last = first
    while last < len(lines):
        nxt = lines[last]            # lines is 0-based: ``lines[last]`` is line ``last + 1``
        if not nxt.strip() or (nxt.startswith("#") and last + 1 > first):
            break
        last += 1
    return first, last


def _targets(src: Source, kind: str, ident: str) -> list:
    """``[(target, field)]`` the model says an owner refers to, in a fixed order."""
    ix = src.ix
    out: list = []

    def add(values, field):
        for v in P.str_list(values):
            if (v, field) not in out:
                out.append((v, field))

    if kind == "node":
        node = ix.node.get(ident) or {}
        add(node.get("evidenceIds"), "ev")
        add([rid for rid, req in ix.requirement.items() if ident in P.str_list(req.get("nodeIds"))], "req")
    elif kind == "edge":
        add((ix.edge.get(ident) or {}).get("evidenceIds"), "ev")
    elif kind == "case":
        case = ix.case.get(ident) or {}
        add(case.get("requirementIds"), "req")
        add(case.get("evidenceIds"), "ev")
        add(case.get("linkedTestIds"), "test")
    elif kind == "machine":
        add((ix.machine.get(ident) or {}).get("evidenceIds"), "ev")
    elif kind == "requirement":
        req = ix.requirement.get(ident) or {}
        add(req.get("evidenceIds"), "ev")
        add(req.get("tstIds"), "test")
        add(req.get("tbdIds"), "tbd")
    elif kind == "tbd":
        add((ix.tbd.get(ident) or {}).get("evidenceIds"), "ev")
    return out


def refs_rows(src: Source, pages: list) -> tuple:
    """``(rows, unprinted)``: ``rows`` are ``(target, field, owner, path, line)`` sorted; ``unprinted`` counts the references of the model
    that the home of the owner does not print."""
    rows: set = set()
    unprinted = 0
    for r in pages:
        for ident, kind, role, line, _hdr in r.marks:
            if role != HOME or kind not in REF_OWNERS:
                continue
            first, last = _owner_span(r.lines, line, kind)
            span = r.lines[first - 1:last]
            spans = [set(m.replace("\\|", "|") for m in CODE.findall(text)) for text in span]
            for target, field in _targets(src, kind, ident):
                hit = next((first + n for n, found in enumerate(spans) if target in found), None)
                if hit is None:
                    unprinted += 1
                    continue
                rows.add((target, field, ident, r.path, hit))
    return sorted(rows), unprinted


def refs_text(src: Source, rows: list) -> str:
    out = [f"# schema {SCHEMA_REFS} m={src.stamp} lang={src.lang}", "# target\tfield\towner\tpath\tline"]
    for target, field, owner, path, line in rows:
        out.append("\t".join((target, field, owner, path, str(line))))
    return "\n".join(out) + "\n"
