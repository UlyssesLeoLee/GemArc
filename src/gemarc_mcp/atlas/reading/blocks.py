"""Items, units and the section renderer: how the body of a page is made from the pieces the families give (design A3 sections 2.3-2.5).

An *item* is the smallest thing a page prints: a block of lines (a node block, a case), or one row of a table. A *segment* is the run of
items of one unit that belongs to one section of the page (the outgoing rows of a node go to the section ``out``). A *unit* is the thing
that never splits across pages (a node with its rows, a case, a machine); a unit that does not fit one page is cut into *parts* at row
seams (``split_unit``).

Rendering is one function, ``render_body``: the same call measures a candidate page while the planner packs (cross-references stay
placeholders of the worst width) and writes the final page (placeholders resolved). Nothing is estimated: the planner measures the real lines.

Long lines (design 2.4 rule 6): a physical line above 480 characters is folded, exactly invertibly. A table cell becomes ``see note [k]``
and the full text goes under the table as ``[k] ...``; any other line is cut at a space or a character boundary and the rest starts with the
continuation mark. ``fold_text`` and ``unfold_text`` are inverse; the conservation check relies on that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .budgets import BUDGETS
from .text import tx

PH_OPEN, PH_CLOSE = "\x01", "\x02"        # a cross-reference to the home page of an element: ``\x01<key>\x02``
NOTE_OPEN, NOTE_CLOSE = "\x03", "\x04"    # a folded cell: ``\x03<index>\x04``
PH_RE = re.compile(PH_OPEN + "([^" + PH_OPEN + PH_CLOSE + "]*)" + PH_CLOSE)
NOTE_RE = re.compile(NOTE_OPEN + r"(\d+)" + NOTE_CLOSE)
ROOT = "\x05"                              # the relative path from the page to ``dist/`` (``../../`` or ``../``)
WORST_REF = "§S999"
WORST_RANGE = "§S999-§S999"
WORST_ROOT = "../../"
RANGE_PREFIX = "range:"                     # ``\x01range:<key>|<key>\x02``: ``§S03`` or ``§S03-§S04``
WRAP = BUDGETS["wrap_chars"]

Mark = tuple    # (id, kind, role)
EMPTY_KEY = "~empty"   # an item with this key is the explicit empty state of a section: printed under a ``(0)`` heading, never counted


def ref(key: str) -> str:
    """A cross-reference placeholder to the home page of ``key`` (``node:<id>``, ``edge:<id>``, ...)."""
    return PH_OPEN + key + PH_CLOSE


def _worst(match) -> str:
    return WORST_RANGE if match.group(1).startswith(RANGE_PREFIX) else WORST_REF


def line_bytes(line: str) -> int:
    return len(PH_RE.sub(_worst, line).replace(ROOT, WORST_ROOT).encode("utf-8")) + 1


def body_bytes(lines: Iterable[str]) -> int:
    return sum(line_bytes(line) for line in lines)


# ---------------------------------------------------------------------------------------------------- table cells

def split_cells(row: str) -> list:
    """The cells of a Markdown table row; an escaped character never ends a cell."""
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


def is_row(line: str) -> bool:
    return line.startswith("|")


def is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|(?:\s*:?-{3,}:?\s*\|)+", line.strip()))


def join_cells(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


# ---------------------------------------------------------------------------------------------------- folding

def fold_text(text: str, mark: str, limit: int = WRAP) -> list:
    """``text`` as lines of at most ``limit`` characters; every line after the first starts with ``mark``. Cut at the last space that leaves a
    first line of useful length, else at the limit; the cut space stays at the head of the next line, so ``unfold_text`` is exact."""
    if len(text) <= limit:
        return [text]
    out, rest, first = [], text, True
    while True:
        room = limit if first else limit - len(mark)
        if len(rest) <= room:
            out.append(rest if first else mark + rest)
            return out
        cut = rest.rfind(" ", 1, room + 1)
        if cut < room // 2:
            cut = room
        piece, rest = rest[:cut], rest[cut:]
        out.append(piece if first else mark + piece)
        first = False


def unfold_text(lines: Sequence[str], mark: str) -> list:
    """The inverse of ``fold_text`` for a run of physical lines: a line that starts with ``mark`` continues the previous one."""
    out: list = []
    for line in lines:
        if out and line.startswith(mark):
            out[-1] += line[len(mark):]
        else:
            out.append(line)
    return out


def fold_row(row: str, lang: str, limit: int = WRAP) -> tuple:
    """``(row, notes)``: a table row above ``limit`` characters with its longest cells replaced by a note placeholder until it fits.
    ``notes[i]`` is the full text of the cell behind placeholder ``i``; a row that fits comes back unchanged with no note."""
    if len(row) <= limit:
        return row, []
    cells = split_cells(row)
    order = sorted(range(len(cells)), key=lambda i: (-len(cells[i]), i))
    notes: list = []
    marker_width = len(tx(lang, "fold.ref", k=99))
    for i in order:
        if len(join_cells(cells)) <= limit:
            break
        if len(cells[i]) <= marker_width:
            continue
        notes.append(cells[i])
        cells[i] = NOTE_OPEN + str(len(notes) - 1) + NOTE_CLOSE
    return join_cells(cells), notes


# ---------------------------------------------------------------------------------------------------- items and units

@dataclass
class Split:
    """A block with a table inside (a case with its events, a machine with its transitions, an entity with its columns). The table rows are
    atoms of their own: a unit that is too large is cut between them. What follows the first table (a second table, a note) is the ``tail``,
    one atom that stays at the end."""

    prefix: list            # the lines before the table (heading, id line, bullets), no trailing blank line; long lines already folded
    prefix_marks: dict      # index in ``prefix`` -> (id, kind, role)
    header: list            # the two header lines of the table
    rows: list              # per row the physical lines (more than one only when a row was too long and was cut)
    row_marks: list         # per row ``(id, kind, role)`` or None
    label: str = ""         # the label of the table in the caption a continuation part gives it
    tail: list = field(default_factory=list)    # the lines after the rows of the first table, as they are (long lines folded)
    gap: int = 1            # blank lines between the prefix and the table in the fragment

    def whole(self) -> list:
        return self.prefix + [""] * self.gap + self.header + [line for row in self.rows for line in row] + self.tail


@dataclass
class Item:
    lines: list
    marks: dict = field(default_factory=dict)       # line index -> (id, kind, role)
    notes: list = field(default_factory=list)       # full text of the cells folded into placeholders (table rows)
    split: Split | None = None
    key: str = ""                                    # the element the item belongs to
    fit: int = 0                                     # folded lines of this item (page record ``fit``)


@dataclass
class Seg:
    section: str
    items: list
    span: tuple | None = None       # (first, last, total) 1-based: this is a part of a longer list


@dataclass
class Unit:
    key: str
    kind: str
    title: str
    bands: list
    seam: object = None             # room id and the like: units of one seam are kept together when they fit
    part: tuple | None = None       # (i, n) on a continuation part

    def items(self) -> list:
        return [it for seg in self.bands for it in seg.items]


@dataclass
class Section:
    key: str
    kind: str                       # "blocks" | "table" | "bullets"
    base: int                       # heading level (2 for blocks, 3 for tables)
    label: str                      # the text of the heading without its count
    unit_label: str = ""            # the label the scope card counts it under (default: ``label``)
    header: tuple = ()              # table header lines
    always: bool = False            # printed even without items
    empty: str = ""                 # the text under the heading when there is no item
    dedupe: bool = False            # items with the same ``key`` print once (diagram references shared by the units of a page)


def _fold_plain(lines: Sequence[str], mark: str) -> tuple:
    """``(lines, index map, number of folded lines)`` for lines cut at the wrap limit (no note placeholders)."""
    out: list = []
    index_map: dict = {}
    fit = 0
    for i, line in enumerate(lines):
        index_map[i] = len(out)
        pieces = fold_text(line, mark) if len(line) > WRAP else [line]
        fit += len(pieces) > 1
        out.extend(pieces)
    return out, index_map, fit


def make_item(lines: Sequence[str], lang: str, marks: dict | None = None, key: str = "") -> Item:
    """An item from lines; a table row above the wrap limit is folded into notes, any other long line is cut with the continuation mark."""
    marks = dict(marks or {})
    out: list = []
    notes: list = []
    index_map: dict = {}
    fit = 0
    mark = tx(lang, "fold.cont")
    for i, line in enumerate(lines):
        index_map[i] = len(out)
        if len(line) <= WRAP:
            out.append(line)
        elif is_row(line) and not is_separator(line):
            folded, cell_notes = fold_row(line, lang)
            base = len(notes)
            for n in range(len(cell_notes) - 1, -1, -1):
                folded = folded.replace(NOTE_OPEN + str(n) + NOTE_CLOSE, NOTE_OPEN + str(base + n) + NOTE_CLOSE)
            notes.extend(cell_notes)
            if len(folded) > WRAP:                       # nothing left to fold in the row: cut it like a plain line
                out.extend(fold_text(folded, mark))
            else:
                out.append(folded)
            fit += 1
        else:
            out.extend(fold_text(line, mark))
            fit += 1
    return Item(out, {index_map[i]: m for i, m in marks.items()}, notes, None, key, fit)


def split_fragment(md: str):
    """``(prefix, gap, header, rows, tail)`` of a block fragment that has a table, or ``None``: ``gap`` is the number of blank lines before the
    table, ``rows`` the rows of the first table and ``tail`` every line after them."""
    lines = md.split("\n")
    for i, line in enumerate(lines):
        if is_row(line) and i + 1 < len(lines) and is_separator(lines[i + 1]):
            prefix = lines[:i]
            gap = 0
            while prefix and not prefix[-1].strip():
                prefix.pop()
                gap += 1
            j = i + 2
            while j < len(lines) and is_row(lines[j]):
                j += 1
            return prefix, gap, lines[i:i + 2], lines[i + 2:j], lines[j:]
    return None


def make_split_item(md: str, lang: str, prefix_marks: dict | None = None, row_mark=None, key: str = "", label: str = "") -> Item:
    """An item for a block fragment that has a table; ``row_mark(row_cells, n)`` gives the mark of row ``n`` of the first table or None. A
    fragment with no table is a plain item. The whole item prints the fragment exactly as it is (plus the folding of long lines)."""
    parts = split_fragment(md)
    if parts is None:
        return make_item(md.split("\n"), lang, prefix_marks, key)
    prefix, gap, header, rows, tail = parts
    mark = tx(lang, "fold.cont")
    prefix_lines, index_map, fit = _fold_plain(prefix, mark)
    tail_lines, _map, tail_fit = _fold_plain(tail, mark)
    fit += tail_fit
    row_lines: list = []
    marks: list = []
    for n, row in enumerate(rows):
        pieces, _m, folded = _fold_plain([row], mark)
        fit += folded
        row_lines.append(pieces)
        marks.append(row_mark(split_cells(row), n) if row_mark else None)
    split = Split(prefix_lines, {index_map[i]: m for i, m in (prefix_marks or {}).items()}, header, row_lines, marks, label, tail_lines, gap)
    whole = split.whole()
    item_marks = dict(split.prefix_marks)
    at = len(prefix_lines) + gap + len(header)
    for n, pieces in enumerate(row_lines):
        if marks[n] is not None:
            item_marks[at] = marks[n]
        at += len(pieces)
    return Item(whole, item_marks, [], split, key, fit)


# ---------------------------------------------------------------------------------------------------- the renderer

@dataclass
class SectionInfo:
    key: str
    label: str
    n: int
    heading: int                    # index of the heading line in the body
    first: int                      # index of the first line after the heading
    last: int
    total: int | None = None        # the full length of a list when this is a part
    span: tuple | None = None


@dataclass
class Body:
    lines: list = field(default_factory=list)
    marks: dict = field(default_factory=dict)       # line index -> (id, kind, role, hdr line index)
    sections: list = field(default_factory=list)
    fit: int = 0

    @property
    def size(self) -> int:
        return body_bytes(self.lines)


def count_text(lang: str, n: int, span: tuple | None) -> str:
    if span is None:
        return tx(lang, "sec.count", n=n)
    first, last, total = span
    return tx(lang, "sec.part", n=n, total=total, a=first, b=last)


def heading_line(level: int, label: str, count: str) -> str:
    return "#" * level + " " + label + count


def identity_lines(unit: Unit, lang: str) -> list:
    """The identity lines a continuation part starts with (design 2.4 rule 5)."""
    i, n = unit.part
    return ["### " + tx(lang, "cont.title", title=unit.title, i=i, n=n), "`" + unit.key.replace("`", "'") + "` · " + tx(lang, "kind." + unit.kind)]


def _emit_notes(lang: str, lines: list, notes: list) -> None:
    for k, text in enumerate(notes, start=1):
        lines.extend(fold_text(tx(lang, "fold.note", k=k, text=text), tx(lang, "fold.cont")))


def render_body(lang: str, sections: Sequence[Section], units: Sequence[Unit], shift: int = 0) -> Body:
    """The body lines of a page that holds ``units`` (placeholders unresolved). Sections print in the order of ``sections``; a section with
    no item is skipped unless it is ``always``. ``shift`` lowers every section heading by that many levels (families folded into one page)."""
    body = Body()
    lines = body.lines
    for unit in units:
        if unit.part is not None and unit.part[0] > 1:
            lines.extend(identity_lines(unit, lang))
            lines.append("")
    for sec in sections:
        bands = [seg for unit in units for seg in unit.bands if seg.section == sec.key]
        items = [it for seg in bands for it in seg.items]
        if sec.dedupe:
            seen: set = set()
            items = [it for it in items if not (it.key in seen or seen.add(it.key))]
        placeholders = [it for it in items if it.key == EMPTY_KEY]
        if placeholders:
            items = [it for it in items if it.key != EMPTY_KEY]
        if not items and not placeholders and not sec.always:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        spans = [seg.span for seg in bands if seg.span is not None]
        span = (spans[0][0], spans[-1][1], spans[0][2]) if spans else None
        heading_at = len(lines)
        lines.append(heading_line(sec.base + shift, sec.label, count_text(lang, len(items), span)))
        first = len(lines)
        if not items:
            for it in placeholders:
                lines.extend(it.lines)
            if not placeholders and sec.empty:
                lines.append(sec.empty)
        elif sec.kind == "table":
            lines.extend(sec.header)
            notes: list = []
            for it in items:
                at = len(lines)
                row = it.lines[0]
                if it.notes:
                    base = len(notes)
                    row = NOTE_RE.sub(lambda m: tx(lang, "fold.ref", k=base + int(m.group(1)) + 1), row)
                    notes.extend(it.notes)
                lines.append(row)
                lines.extend(it.lines[1:])
                for idx, mark in it.marks.items():
                    body.marks[at + idx] = (*mark, heading_at)
                body.fit += it.fit
            _emit_notes(lang, lines, notes)
        elif sec.kind == "bullets":
            for it in items:
                at = len(lines)
                lines.extend(it.lines)
                for idx, mark in it.marks.items():
                    body.marks[at + idx] = (*mark, heading_at)
                body.fit += it.fit
        else:
            for n, it in enumerate(items):
                if n:
                    lines.append("")
                at = len(lines)
                lines.extend(it.lines)
                for idx, mark in it.marks.items():
                    body.marks[at + idx] = (*mark, at)
                body.fit += it.fit
        body.sections.append(SectionInfo(sec.key, sec.unit_label or sec.label, len(items), heading_at, first, len(lines) - 1,
                                         span[2] if span else None, span))
    return body


def resolve(lines: Sequence[str], pages: dict, root: str = WORST_ROOT) -> list:
    """Placeholders replaced by ``§<page id>`` (``pages[key]`` is the bare page id; a range key gives ``§S03-§S04``) and the root token by
    ``root``; an unknown key is a generator bug."""
    def one(match):
        key = match.group(1)
        if key.startswith(RANGE_PREFIX):
            first, last = key[len(RANGE_PREFIX):].split("|", 1)
            a, b = pages[first], pages[last]
            return "§" + a if a == b else "§" + a + "-§" + b
        if key not in pages:
            raise KeyError(f"cross-reference to {key!r} has no home page")
        return "§" + pages[key]

    return [PH_RE.sub(one, line).replace(ROOT, root) if (PH_OPEN in line or ROOT in line) else line for line in lines]


# ---------------------------------------------------------------------------------------------------- continuation

def _atoms(unit: Unit, order: dict) -> list:
    """``(section, item, row)`` in print order: ``row`` is None for a whole item, ``-1`` for the head of a split item (its prefix), ``n`` for
    row ``n`` of a split item."""
    out: list = []
    for seg in sorted(unit.bands, key=lambda s: order.get(s.section, 99)):
        for it in seg.items:
            if it.split is None:
                out.append((seg.section, it, None))
            else:
                out.append((seg.section, it, -1))
                out.extend((seg.section, it, n) for n in range(len(it.split.rows)))
                if it.split.tail:
                    out.append((seg.section, it, -2))
    return out


def _part_unit(unit: Unit, lang: str, atoms: list, order: dict, sums: dict, offsets: dict) -> Unit:
    """The part of ``unit`` made of ``atoms`` (consecutive in print order)."""
    by_section: dict = {}
    for section, it, row in atoms:
        by_section.setdefault(section, []).append((it, row))
    bands: list = []
    for section in sorted(by_section, key=lambda s: order.get(s, 99)):
        items: list = []
        positions: list = []
        entries = by_section[section]
        n = 0
        while n < len(entries):
            it, row = entries[n]
            if row is None:
                items.append(it)
                positions.append(offsets[(section, id(it))])
                n += 1
                continue
            sp = it.split
            has_prefix = row == -1
            rows: list = []
            has_tail = False
            if has_prefix:
                n += 1
            while n < len(entries) and entries[n][0] is it and entries[n][1] is not None and entries[n][1] >= 0:
                rows.append(entries[n][1])
                n += 1
            if n < len(entries) and entries[n][0] is it and entries[n][1] == -2:
                has_tail = True
                n += 1
            lines = list(sp.prefix) if has_prefix else []
            marks = dict(sp.prefix_marks) if has_prefix else {}
            if rows:
                first, last = rows[0], rows[-1] + 1
                if not (has_prefix and first == 0 and last == len(sp.rows)):
                    lines.append("#### " + sp.label + count_text(lang, last - first, (first + 1, last, len(sp.rows))))
                elif sp.gap:
                    lines.extend([""] * sp.gap)
                lines.extend(sp.header)
                for r in range(first, last):
                    if sp.row_marks[r] is not None:
                        marks[len(lines)] = sp.row_marks[r]
                    lines.extend(sp.rows[r])
            if has_tail:
                tail = list(sp.tail)
                if not lines:
                    while tail and not tail[0].strip():
                        tail.pop(0)
                lines.extend(tail)
            items.append(Item(lines, marks, [], None, it.key, 0))
        span = None
        if positions and len(positions) < sums.get(section, 0):
            span = (min(positions) + 1, max(positions) + 1, sums[section])
        bands.append(Seg(section, items, span))
    return Unit(unit.key, unit.kind, unit.title, bands, unit.seam, (1, 1))


def split_unit(unit: Unit, lang: str, sections: Sequence[Section], capacity: int, shift: int = 0) -> list:
    """The parts of a unit that does not fit one page of ``capacity`` body bytes: every part fits (row seams only; the identity lines and
    the captions a part adds are measured), the parts keep the order of the rows, nothing is lost. A single atom that cannot fit even alone
    becomes a part of its own (the planner records that as a finding). The result has ``part = (i, n)`` set."""
    order = {sec.key: n for n, sec in enumerate(sections)}
    atoms = _atoms(unit, order)
    sums: dict = {}
    offsets: dict = {}
    for section, it, row in atoms:
        if row is None:
            offsets[(section, id(it))] = sums.get(section, 0)
            sums[section] = sums.get(section, 0) + 1

    def size_of(chunk: list) -> int:
        part = _part_unit(unit, lang, chunk, order, sums, offsets)
        part.part = (2, 2)                                   # a continuation identity is measured (the widest case)
        return body_bytes(render_body(lang, sections, [part], shift).lines)

    parts: list = []
    cur: list = []
    for atom in atoms:
        if cur and size_of(cur + [atom]) > capacity:
            parts.append(cur)
            cur = []
        cur.append(atom)
    if cur:
        parts.append(cur)
    out = []
    for n, chunk in enumerate(parts, start=1):
        part = _part_unit(unit, lang, chunk, order, sums, offsets)
        part.part = (n, len(parts))
        out.append(part)
    return out
