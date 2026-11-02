"""Reading a finished page back: the frame, the scope card, the sections and the tables (the checks and the page server use it).

Everything is derived from the same templates the writer used (``derive/i18n`` ``read.*``), so no check carries a fixed sentence. A page
that cannot be parsed returns ``None``: the caller reports it (RS-103), it never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import blocks as B
from . import names as N
from .text import pattern, raw, tx

HEADING = re.compile(r"^(#{1,6}) (.*)$")


@dataclass
class ParsedPage:
    path: str
    text: str
    lines: list
    pid: str
    index: int
    count: int
    total_lines: int
    cls: str
    project: str
    lang: str
    stamps: dict
    content: str
    covers: str
    excludes: str
    howto: str
    continuation: dict | None
    head_len: int
    foot_start: int                  # index of the first foot line
    body: list = field(default_factory=list)
    neighbours: list = field(default_factory=list)
    sentinel: str = ""
    envelope_bytes: int = 0


def _regex(lang_: str, key: str, **groups) -> re.Pattern:
    return re.compile("^" + pattern(lang_, key, **groups) + "$")


def parse_page(lang: str, path: str, text: str):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if len(lines) < 10:
        return None
    first = _regex(lang, "head.page", page=r"[^\s·]+", title=r".+?", i=r"\d+", n=r"\d+", lines=r"\d+", cls=r"card|page").match(lines[0])
    second = _regex(lang, "head.project", project=r".+?", lang=r"[A-Za-z-]+", stamps=r"[^·]+(?: · [^·]+)*?", content=r".+?", check=r".+").match(lines[1])
    if first is None or second is None:
        return None
    covers = _strip(lang, "card.covers", lines[4])
    excludes = _strip(lang, "card.excludes", lines[5])
    howto = _strip(lang, "card.howto", lines[6])
    if covers is None or excludes is None or howto is None:
        return None
    continuation = None
    head_len = 8
    cont = _regex(lang, "card.continued", unit=r".+?", part=r"\d+", parts=r"\d+", others=r".*").match(lines[7])
    if cont is not None:
        continuation = {"unit": cont.group("unit"), "part": int(cont.group("part")), "parts": int(cont.group("parts")), "others": cont.group("others")}
        head_len = 9
    stamps: dict = {}
    for token in second.group("stamps").split(" · "):
        key, _sep, value = token.partition("=")
        stamps[key] = value
    # foot: blank, neighbour line (optional), more/end (optional), sentinel
    sentinel = lines[-1]
    at = len(lines) - 1
    more = _regex(lang, "foot.more", page=r"[A-Za-z0-9-]+")
    if at - 1 >= head_len and (more.match(lines[at - 1]) or lines[at - 1] == tx(lang, "foot.end_unit")):
        at -= 1
    neigh_prefix = tx(lang, "foot.neighbours", links="")
    neighbours: list = []
    if at - 1 >= head_len and lines[at - 1].startswith(neigh_prefix):
        neighbours = lines[at - 1][len(neigh_prefix):].split(" · ")
        at -= 1
    if at - 1 >= head_len and lines[at - 1] == "":
        at -= 1
    foot_start = at
    head_bytes = sum(len(x.encode("utf-8")) + 1 for x in lines[:head_len])
    foot_bytes = sum(len(x.encode("utf-8")) + 1 for x in lines[foot_start:])
    return ParsedPage(path, text, lines, first.group("page").lstrip(N.SECTION_SIGN), int(first.group("i")), int(first.group("n")), int(first.group("lines")),
                      first.group("cls"), second.group("project"), second.group("lang"), stamps, second.group("content"), covers, excludes, howto, continuation,
                      head_len, foot_start, lines[head_len:foot_start], neighbours, sentinel, head_bytes + foot_bytes)


def _strip(lang: str, key: str, line: str):
    template = raw(lang, key)
    prefix = template.split("{text}")[0]
    return line[len(prefix):] if line.startswith(prefix) else None


# ---------------------------------------------------------------------------------------------------- sections and tables

COUNT_RES: dict = {}


def count_regexes(lang: str) -> tuple:
    """``(complete, part)`` regular expressions of the count a heading carries (``(n)`` and ``(n / total, rows a-b)``)."""
    key = N.text_lang(lang)
    if key not in COUNT_RES:
        complete = re.compile(pattern(lang, "sec.count", n=r"\d+") + "$")
        part = re.compile(pattern(lang, "sec.part", n=r"\d+", total=r"\d+", a=r"\d+", b=r"\d+") + "$")
        COUNT_RES[key] = (complete, part)
    return COUNT_RES[key]


def headings(lang: str, lines: list) -> list:
    """``[(index, level, label, n, total, a, b)]`` of every heading that carries a count (``total`` is None for a complete one)."""
    complete, part = count_regexes(lang)
    out = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        title = m.group(2)
        p = part.search(title)
        if p:
            out.append((i, len(m.group(1)), title[:p.start()], int(p.group("n")), int(p.group("total")), int(p.group("a")), int(p.group("b"))))
            continue
        c = complete.search(title)
        if c:
            out.append((i, len(m.group(1)), title[:c.start()], int(c.group("n")), None, None, None))
    return out


def tables(lines: list) -> list:
    """``[(first index of the header, header lines, row lines, index after the last row)]`` for every table of ``lines``."""
    out = []
    i = 0
    while i < len(lines):
        if B.is_row(lines[i]) and i + 1 < len(lines) and B.is_separator(lines[i + 1]):
            j = i + 2
            while j < len(lines) and B.is_row(lines[j]):
                j += 1
            out.append((i, lines[i:i + 2], lines[i + 2:j], j))
            i = j
        else:
            i += 1
    return out


def unfold_lines(lang: str, lines: list) -> list:
    """The body lines of a page as they were before folding: footnote lines folded back into their table rows, ``(cont.)`` lines joined."""
    mark = tx(lang, "fold.cont")
    joined = B.unfold_text(lines, mark)
    note_re = re.compile("^" + pattern(lang, "fold.note", k=r"\d+", text=r".*") + "$")
    ref_template = raw(lang, "fold.ref")
    ref_re = re.compile(re.escape(ref_template).replace(re.escape("{k}"), r"(\d+)"))
    out: list = []
    i = 0
    while i < len(joined):
        line = joined[i]
        if B.is_row(line) and not B.is_separator(line) and ref_re.search(line):
            out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
    # a table whose rows carry note references: the notes are the lines right after the table
    result: list = []
    i = 0
    while i < len(out):
        if B.is_row(out[i]) and i + 1 < len(out) and B.is_separator(out[i + 1]):
            j = i + 2
            while j < len(out) and B.is_row(out[j]):
                j += 1
            rows = out[i + 2:j]
            notes: dict = {}
            k = j
            while k < len(out):
                m = note_re.match(out[k])
                if not m:
                    break
                notes[int(m.group("k"))] = m.group("text")
                k += 1
            if notes:
                rows = [ref_re.sub(lambda mm: notes.get(int(mm.group(1)), mm.group(0)), row) for row in rows]
                j = k
            result.extend(out[i:i + 2])
            result.extend(rows)
            i = j
            continue
        result.append(out[i])
        i += 1
    return result
