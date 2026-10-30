"""The ``.atl`` front-end: header-declared pipe rows (design 3.1) -> ``Row`` objects with ``file:line``.

Grammar in short (the normative text is design section 3.1):

* UTF-8, LF written (CRLF and a BOM are accepted), first line ``#atlas 1`` (optionally ``#atlas 1 mode=tobe``),
  ``//`` comment lines.
* ``## name: col | col | opts`` declares a section; the header must echo the canonical columns (``opts`` may be left out;
  ``## name`` alone is also accepted).
* A row has exactly as many positional text cells as the header names, separated by unescaped ``|``; whatever follows
  the last separator is the options cell, kept verbatim, so ``|`` inside a quoted or JSON option value is safe.
* A text cell is never empty. A literal ``|`` is written ``\\|`` and a backslash before ``|`` or ``\\`` is written
  ``\\\\``. A text cell may also be one complete JSON string (``"..."``); a bare cell must not contain ``"``.
* Options: ``key=value`` separated by spaces; a value is a bare token, a ``"quoted string"`` or a balanced ``{json}`` or
  ``[json]``. Lists are comma-joined ids.
* ``@set k=v ...`` sets scalar defaults for the rest of the section instance (``@set k=`` clears); a positional cell ``~``
  takes the ``@set`` default of its column. ``@final <section>`` and ``@pending id,id`` are declarations.
* Arity is exact (ST-005), an option token inside a text cell is ST-006, malformed option syntax is ST-007.

Standard library only; no file I/O (callers pass text or bytes).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .. import diag as dg
from . import rows as rw
from .rows import Bare, ParsedSlice, Quoted, SECTIONS

BOM = "﻿"
HEADER_RE = re.compile(r"^#atlas[ \t]+([^ \t]+)(.*)$")
KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
WS = " \t"
#: Control and invisible characters (C0/C1 controls except TAB, line and paragraph separators, BOM, zero-width and bidi marks).
#: The encoder always quotes them; a bare cell or option that holds one is refused (ST-007).
BARE_CTRL_RE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029\ufeff\u200b-\u200f\u202a-\u202e\u2060-\u2064]")
#: Section names in the order of ``rows.SECTIONS`` (for hints).
ALLOWED_SECTIONS = ", ".join(SECTIONS)


def decode_bytes(data: bytes, name: str, diags: list) -> str:
    """UTF-8 text without BOM; a decode failure is ST-008 and the text is decoded with replacement characters."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as ex:
        diags.append(dg.st008("file", f"{name}:0", f"byte {ex.start}", "UTF-8 text", "save the file as UTF-8"))
        text = data.decode("utf-8", errors="replace")
    return text[1:] if text.startswith(BOM) else text


def file_checks(text: str, name: str, diags: list, max_bytes: int) -> int:
    """ST-008 size, NUL and lone-surrogate checks; returns the UTF-8 size.

    The size is measured as the parser reads the text: CRLF folded to LF and the BOM gone, so an LF file and its CRLF copy
    (a Windows editor, ``autocrlf``) get the same verdict.
    """
    folded = text.replace("\r\n", "\n")
    size = len(folded.encode("utf-8", errors="replace"))
    if rw.SURROGATE_RE.search(text):
        diags.append(dg.st008("file", f"{name}:0", "a lone UTF-16 surrogate", "text that encodes to UTF-8", "remove the character"))
    if size > max_bytes:
        diags.append(dg.st008("file", f"{name}:0", f"{size} bytes", f"at most {max_bytes} bytes",
                              "split the slice into smaller files (about 11 nodes with their edges and evidences per file)"))
    if "\x00" in text:
        diags.append(dg.st008("file", f"{name}:0", "NUL character", "text without NUL", "remove the NUL byte"))
    return size


def refused(diags: list) -> bool:
    """True when the file as a whole is refused (ST-008): it is not parsed, so the author sees that one error only."""
    return any(d.rule == "ST-008" for d in diags)


def tokenize_options(cell: str) -> tuple[list[tuple[str, Any]], tuple[str, str, str] | None]:
    """``([(key, raw)], problem)``: raw values in order; ``problem`` is ``(got, expected, hint)`` for the first defect.

    Tokenizing stops at the first defect (the row is dropped anyway).
    """
    items: list[tuple[str, Any]] = []
    i, n = 0, len(cell)
    seen: set[str] = set()
    while True:
        while i < n and cell[i] in WS:
            i += 1
        if i >= n:
            return items, None
        m = KEY_RE.match(cell, i)
        if m is None or m.end() >= n or cell[m.end()] != "=":
            end = i
            while end < n and cell[end] not in WS:
                end += 1
            token = cell[i:end]
            return items, (token, "key=value tokens", "an option is key=value without spaces around '='; "
                           "a stray '|' or word here usually means a text cell was split or merged")
        key = m.group()
        if key in seen:
            return items, (key, "each option once", f"{key}= appears twice in the options cell")
        seen.add(key)
        i = m.end() + 1
        if i >= n or cell[i] in WS:
            items.append((key, Bare("")))
            continue
        ch = cell[i]
        if ch == '"' or ch in "{[":
            try:
                value, end = rw.json_loads_strict(cell, i)
            except ValueError as ex:
                what = "quoted string" if ch == '"' else "JSON value"
                return items, (cell[i:i + 24], f"a well-formed {what}", f"{key}=: {str(ex)[:60]}; quotes and braces must balance")
            if ch == '"':
                value = Quoted(value)
            if end < n and cell[end] not in WS:
                return items, (cell[end:end + 12], "a space after the value", f"{key}=: text directly after the closing quote or bracket")
            items.append((key, value))
            i = end
            continue
        end = i
        while end < n and cell[end] not in WS:
            end += 1
        token = cell[i:end]
        if '"' in token or "|" in token:
            return items, (token, "a bare value without quotes or '|'", f"{key}=: quote the value (\"...\"), or check for a stray quote or '|'")
        if BARE_CTRL_RE.search(token):
            return items, (token, "a bare value without control or invisible characters", f"{key}=: write the value as a \"quoted string\"")
        items.append((key, Bare(token)))
        i = end


def _split_row(line: str, section: rw.Section) -> tuple[list[Any], str | None, tuple | None]:
    """``(cells, options_cell, problem)``: positional raw cells (``Bare``/``Quoted``), the options remainder, a defect."""
    positional = len(section.columns)
    n = len(line)
    cells: list[Any] = []
    i = 0
    rest: str | None = None
    for k in range(positional):
        while i < n and line[i] in WS:
            i += 1
        if i < n and line[i] == '"':
            try:
                value, end = rw.json_loads_strict(line, i)
            except ValueError as ex:
                return cells, None, (line[i:i + 24], "a well-formed quoted string", str(ex)[:60] + "; a text cell containing a quote must be one complete \"...\" string")
            cell: Any = Quoted(value)
            i = end
            while i < n and line[i] in WS:
                i += 1
            if i < n and line[i] != "|":
                return cells, None, (line[i:i + 12], "'|' after a quoted cell", "text after the closing quote of a cell")
        else:
            chars: list[str] = []
            while i < n:
                ch = line[i]
                if ch == "\\" and i + 1 < n and line[i + 1] in "|\\":
                    chars.append(line[i + 1])
                    i += 2
                    continue
                if ch == "|":
                    break
                chars.append(ch)
                i += 1
            text = "".join(chars).strip(WS)
            if '"' in text:
                return cells, None, (text[:24], "a cell without quotes, or one complete \"...\" string",
                                     "a quote inside a bare text cell: wrap the whole cell in quotes (use \\\" for an inner quote)")
            cell = Bare(text)
        cells.append(cell)
        if i < n and line[i] == "|":
            i += 1
            if k == positional - 1:
                rest = line[i:]
            elif i >= n:
                cells.append(Bare(""))  # a separator with nothing after it: an empty cell
                break
        else:
            break
    return cells, rest, None


def parse_atl(text: str, name: str = "<memory>", *, max_bytes: int = rw.MAX_SLICE_BYTES) -> ParsedSlice:
    """Parse ``.atl`` text. Never raises; defects become diagnostics and the affected rows are dropped."""
    out = ParsedSlice(name=name, fmt="atl")
    diags = out.diags
    if text.startswith(BOM):
        text = text[1:]
    out.size = file_checks(text, name, diags, max_bytes)
    if refused(diags):
        return out
    lines = text.replace("\r\n", "\n").split("\n")
    section: rw.Section | None = None
    skip_rows = False  # after a section header that could not be used
    defaults: dict[str, Any] = {}
    header_seen = False
    for number, raw_line in enumerate(lines, start=1):
        at = f"{name}:{number}"
        line = raw_line.strip(" \t\r")
        if not line or line.startswith("//"):
            continue
        if "\r" in line:
            diags.append(dg.st007("row", at, line[:30], "lines ended by LF (or CRLF)",
                                  "a lone CR is not a line break here and would swallow the next line; save the file with LF line endings"))
            continue
        if line.startswith("#atlas") and HEADER_RE.match(line):
            if header_seen:
                diags.append(dg.st004("header", at, line[:40], "one '#atlas 1' line at the top of the file", "remove the repeated header line"))
                continue
            header_seen = True
            _read_header(out, line, at)
            continue
        if not header_seen:
            header_seen = True
            diags.append(dg.st001("header", at, line[:30], "#atlas 1 on the first line", "start the file with '#atlas 1'"))
        if line.startswith("## ") or line == "##":
            section, skip_rows = _read_section_header(line, at, diags)
            defaults = {}
            continue
        if line.startswith("@set") and (len(line) == 4 or line[4] in WS):
            if section is None:
                if not skip_rows:
                    diags.append(dg.st004("@set", at, line[:30], "a '## section: columns' header first", "put '@set' below a section header"))
                continue
            _read_set(section, line[4:], defaults, at, diags)
            continue
        if line.startswith("@final") and (len(line) == 6 or line[6] in WS):
            _read_final(out, line[6:].strip(WS), section, at)
            continue
        if line.startswith("@pending") and (len(line) == 8 or line[8] in WS):
            ids = [x.strip() for x in line[8:].split(",") if x.strip()]
            if not ids:
                diags.append(dg.st001("@pending", at, "empty", "id,id", "list the ids after @pending"))
            out.pendings.extend(ids)
            continue
        if line.startswith("@"):
            diags.append(dg.st004("directive", at, line.split()[0], "@set, @final, @pending", "unknown directive"))
            continue
        if section is None:
            if not skip_rows:
                diags.append(dg.st004("row", at, line[:30], "a '## section: columns' header first", "add the section header above the rows"))
            continue
        row = _read_row(section, line, name, number, defaults, diags)
        if row is not None:
            out.rows.append(row)
    if not header_seen and not diags:
        diags.append(dg.st001("header", f"{name}:1", "empty file", "#atlas 1 on the first line", "start the file with '#atlas 1'"))
    return out


def parse_atl_bytes(data: bytes, name: str = "<memory>", *, max_bytes: int = rw.MAX_SLICE_BYTES) -> ParsedSlice:
    pre: list = []
    text = decode_bytes(data, name, pre)
    if pre:
        return ParsedSlice(name=name, fmt="atl", diags=pre, size=len(data))
    return parse_atl(text, name, max_bytes=max_bytes)


def _read_header(out: ParsedSlice, line: str, at: str) -> None:
    m = HEADER_RE.match(line)
    version, rest = m.group(1), m.group(2)
    if version != "1":
        out.diags.append(dg.st004("header", at, line[:40], "#atlas 1", "the only supported format version is 1"))
    items, problem = tokenize_options(rest)
    if problem:
        out.diags.append(dg.st007("header", at, *problem))
        return
    for key, value in items:
        if key != "mode":
            out.diags.append(dg.st004("header", at, key, "mode", "the header takes only mode=asis or mode=tobe"))
        else:
            out.mode = str(value)


def _read_section_header(line: str, at: str, diags: list) -> tuple["rw.Section | None", bool]:
    body = line[2:].strip(WS)
    name, colon, rest = body.partition(":")
    name = name.strip(WS)
    spec = SECTIONS.get(name)
    if spec is None:
        near = rw.closest(name, SECTIONS)
        diags.append(dg.st004("section", at, name or line[:30], ALLOWED_SECTIONS, f"unknown section; did you mean {near}?" if near else ""))
        return None, True
    if colon:
        cols = [c.strip(WS) for c in rest.split("|")]
        positional = list(spec.column_names)
        if cols != positional and not (spec.options and cols == positional + ["opts"]):
            diags.append(dg.st004("header", at, line[:60], spec.header,
                                  "the header must echo the columns exactly (the order is fixed); rows of this section are skipped"))
            return None, True
    return spec, False


def _read_set(section: rw.Section, text: str, defaults: dict, at: str, diags: list) -> None:
    items, problem = tokenize_options(text)
    for key, raw in items:
        f = section.field(key)
        if f is None:
            if key in rw.DERIVED.get(section.name, ()):
                diags.append(dg.dv001(f"{section.name}.{key}", at, key))
            else:
                near = rw.closest(key, section.column_names + section.option_names)
                diags.append(dg.st004(f"{section.name}.@set", at, key, section.column_names + section.option_names,
                                      f"unknown key; did you mean {near}?" if near else ""))
            continue
        if f.type not in rw.SCALAR_TYPES:
            diags.append(dg.st007(f"{section.name}.@set", at, key, "a scalar default",
                                  "@set takes scalars only; lists such as ev, nodes, edges, req, tests are never blanket defaults"))
            continue
        if isinstance(raw, (dict, list)):
            diags.append(dg.st007(f"{section.name}.@set", at, key, "a scalar default", "@set takes bare or quoted scalars, not JSON"))
            continue
        if isinstance(raw, Bare) and raw == "":
            defaults.pop(key, None)
            continue
        try:
            defaults[key] = rw.coerce(f.type, raw)
        except rw.FieldError as ex:
            diags.append(dg.st007(f"{section.name}.@set", at, ex.got, ex.expected, ex.hint))
    if problem:
        diags.append(dg.st007(f"{section.name}.@set", at, *problem))


def _read_final(out: ParsedSlice, argument: str, section: "rw.Section | None", at: str) -> None:
    name = argument or (section.name if section else "")
    if name not in SECTIONS:
        out.diags.append(dg.st004("@final", at, name or "(none)", ALLOWED_SECTIONS, "write '@final <section>' with a known section name"))
        return
    out.finals.append(name)


def _read_row(section: rw.Section, line: str, file: str, number: int, defaults: dict, diags: list):
    at = f"{file}:{number}"
    positional = len(section.columns)
    cells, rest, problem = _split_row(line, section)
    if problem:
        diags.append(dg.st007(f"{section.name}.row", at, *problem))
        return None
    if len(cells) < positional:
        diags.append(dg.st005(f"{section.name} row", at, len(cells), positional, section.header[3:].split(":", 1)[1].strip()))
        return None
    if rest is not None and not section.options:
        if rest.strip(WS):
            diags.append(dg.st005(f"{section.name} row", at, positional + 1 + rest.count("|"), positional,
                                  section.header[3:].split(":", 1)[1].strip(), too_many=True))
            return None
        rest = None
    raw: dict[str, Any] = {}
    failed = False
    for col, cell in zip(section.columns, cells):
        if isinstance(cell, Bare):
            if cell == "~":
                if col.name not in defaults:
                    diags.append(dg.st001(f"{section.name}.{col.name}", at, "~", f"an '@set {col.name}=...' default above the row",
                                          "~ means 'use the @set default' and there is none"))
                    failed = True
                    continue
                cell = _default_raw(defaults[col.name])
            elif cell == "":
                diags.append(dg.st001(f"{section.name}.{col.name}", at, "empty cell", "text",
                                      "positional cells are never empty; use ~ for the @set default"))
                failed = True
                continue
            else:
                if BARE_CTRL_RE.search(cell):
                    diags.append(dg.st007(f"{section.name}.{col.name}", at, str(cell)[:30], "text without control or invisible characters",
                                          "write the cell as a \"quoted string\" (JSON escapes) if it really needs such a character"))
                    failed = True
                    continue
                hit = rw.looks_like_option(section.name, str(cell))
                if hit:
                    diags.append(dg.st006(f"{section.name}.{col.name}", at, hit))
                    failed = True
                    continue
        raw[col.name] = cell
    if failed:
        return None
    if rest is not None and rest.strip(WS):
        items, problem = tokenize_options(rest)
        if problem:
            extra = _extra_cells(rest, problem[0])
            if extra:
                diags.append(dg.st007(f"{section.name}.opts", at, f"{positional + extra} cells (near {problem[0][:20]!r})",
                                      f"{positional} text cells, then key=value options",
                                      f"the header names {positional} text cells; this row has {extra} more '|'-separated cells after them. "
                                      "Merge them into the last text cell, quote the text, or write \\| for a literal '|'"))
            else:
                diags.append(dg.st007(f"{section.name}.opts", at, *problem))
            return None
        for key, value in items:
            if key in raw:
                diags.append(dg.st007(f"{section.name}.opts", at, key, "a key that is not a positional cell",
                                      f"{key}= is a positional cell of this row and is already given; edit the cell, do not repeat it as an option"))
                return None
            raw[key] = value
    return rw.make_row(section, raw, file, number, diags, defaults)


def _extra_cells(rest: str, token: str) -> int:
    """Number of cells the options remainder holds when it looks like more positional cells (a '|' before the bad token)."""
    at = rest.find(token) if token else -1
    head = rest[:at] if at >= 0 else rest
    return head.count("|") + 1 if "|" in head or token == "|" else 0


def _default_raw(value: Any):
    if isinstance(value, bool):
        return Bare("true" if value else "false")
    if isinstance(value, int):
        return Bare(str(value))
    return Quoted(value)


# ---------------------------------------------------------------------------------------------------------------
# Writing (used by atlas/encode.py)
# ---------------------------------------------------------------------------------------------------------------

_BARE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/+^@%$#~*=<>!?&()-]+$")
_CTRL_RE = re.compile("[\x00-\x1f\x7f-\x9f\ufeff\u200b-\u200f\u202a-\u202e\u2060-\u2064  ]")


def json_text(value: Any) -> str:
    """Compact JSON, keys sorted, non-ASCII kept (what the encoder writes inside options)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def bare_safe(text: str) -> bool:
    return bool(text) and _BARE_VALUE_RE.match(text) is not None and text[0] not in "{[\"" and text != "^"


def format_text_value(text: str) -> str:
    """An option value for a text field: bare when safe, else a JSON string."""
    return text if bare_safe(text) else json.dumps(text, ensure_ascii=False)


def format_list_value(items: Sequence[str]) -> str:
    """A list option value: comma-joined when every item is a bare-safe id without comma, else a JSON array."""
    if items and all(bare_safe(x) and "," not in x for x in items):
        return ",".join(items)
    return json.dumps(list(items), ensure_ascii=False, separators=(",", ":"))


def cell_needs_quotes(section: str, text: str) -> bool:
    return (text == "" or text != text.strip(WS) or '"' in text or "\\" in text or text == "~" or text.startswith(("//", "@", "#", "##"))
            or _CTRL_RE.search(text) is not None or rw.looks_like_option(section, text) is not None)


def format_cell(section: str, text: str) -> str:
    """A positional text cell: bare (with ``\\|`` escapes) when plain, else one JSON string."""
    if cell_needs_quotes(section, text):
        return json.dumps(text, ensure_ascii=False)
    return text.replace("|", "\\|")


def format_row(section: str, cells: Sequence[str], options: Iterable[tuple[str, str]]) -> str:
    """``cell | cell | key=value key=value`` (cells already formatted; ``options`` are ``(key, formatted value)``)."""
    opts = " ".join(f"{k}={v}" for k, v in options)
    return " | ".join(list(cells) + ([opts] if opts else []))
