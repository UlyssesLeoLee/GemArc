"""The ``.jsonl`` front-end: one JSON object per line -> the same ``Row`` objects as the ``.atl`` front-end.

Line shapes::

    {"_atlas":1,"mode":"tobe"}                                 header (first line; ``mode`` optional)
    {"_section":"nodes","id":"svc.loan","name":"Loan service","sub":"...","room":"rm.desk","kind":"Module","ev":["EVD-1"]}
    {"_final":"edges"}                                         same as ``@final edges``
    {"_pending":["svc.a","svc.b"]}                             same as ``@pending``

A row is flat: ``_section`` plus the positional columns and options of ``rows.SECTIONS`` (design 3.2). Values are native
JSON (a list for ids, a number for a number, ``true`` for a boolean, an object for an object), so there is no ``@set``
and no ``~``: every value is written out. Unknown keys are ST-004, derived keys DV-001, exactly as in ``.atl``.
Standard library only.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .. import diag as dg
from . import rows as rw
from .atl import ALLOWED_SECTIONS, BOM, decode_bytes, file_checks, refused
from .rows import ParsedSlice, SECTIONS

SECTION_KEY = "_section"
ATLAS_KEY, FINAL_KEY, PENDING_KEY = "_atlas", "_final", "_pending"


def parse_jsonl(text: str, name: str = "<memory>", *, max_bytes: int = rw.MAX_SLICE_BYTES) -> ParsedSlice:
    """Parse ``.jsonl`` text. Never raises; defects become diagnostics and the affected rows are dropped."""
    out = ParsedSlice(name=name, fmt="jsonl")
    diags = out.diags
    if text.startswith(BOM):
        text = text[1:]
    out.size = file_checks(text, name, diags, max_bytes)
    if refused(diags):
        return out
    header_seen = False
    for number, raw_line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        at = f"{name}:{number}"
        line = raw_line.strip(" \t\r")
        if not line:
            continue
        try:
            obj, end = rw.json_loads_strict(line, 0)
            if line[end:].strip():
                raise ValueError("text after the JSON value")
        except ValueError as ex:
            diags.append(dg.st007("row", at, line[:30], "one JSON object per line", f"{str(ex)[:60]}; quotes and braces must balance"))
            continue
        if not isinstance(obj, dict):
            diags.append(dg.st007("row", at, line[:30], "a JSON object", "each line is one object such as {\"_section\":\"nodes\",...}"))
            continue
        if ATLAS_KEY in obj and SECTION_KEY not in obj:
            if header_seen:
                diags.append(dg.st004("header", at, line[:40], "one header line at the top of the file", "remove the repeated header"))
                continue
            header_seen = True
            _header(out, obj, at)
            continue
        if not header_seen:
            header_seen = True
            diags.append(dg.st001("header", at, line[:30], '{"_atlas":1} on the first line', 'start the file with {"_atlas":1}'))
        if FINAL_KEY in obj and len(obj) == 1:
            value = obj[FINAL_KEY]
            if isinstance(value, str) and value in SECTIONS:
                out.finals.append(value)
            else:
                diags.append(dg.st004("@final", at, value, ALLOWED_SECTIONS, "write {\"_final\":\"<section>\"} with a known section name"))
            continue
        if PENDING_KEY in obj and len(obj) == 1:
            value = obj[PENDING_KEY]
            if isinstance(value, list) and value and all(isinstance(x, str) and x for x in value):
                out.pendings.extend(value)
            else:
                diags.append(dg.st007("@pending", at, value, "a non-empty list of ids", "write {\"_pending\":[\"id\",...]}"))
            continue
        section_name = obj.get(SECTION_KEY)
        spec = SECTIONS.get(section_name) if isinstance(section_name, str) else None
        if spec is None:
            near = rw.closest(section_name, SECTIONS) if isinstance(section_name, str) else ""
            diags.append(dg.st004("section", at, section_name if section_name is not None else "(none)", ALLOWED_SECTIONS,
                                  f"unknown or missing '_section'; did you mean {near}?" if near else "every row has a \"_section\" key"))
            continue
        row = rw.make_row(spec, {k: v for k, v in obj.items() if k != SECTION_KEY}, name, number, diags)
        if row is not None:
            out.rows.append(row)
    if not header_seen and not diags:
        diags.append(dg.st001("header", f"{name}:1", "empty file", '{"_atlas":1} on the first line', 'start the file with {"_atlas":1}'))
    return out


def parse_jsonl_bytes(data: bytes, name: str = "<memory>", *, max_bytes: int = rw.MAX_SLICE_BYTES) -> ParsedSlice:
    pre: list = []
    text = decode_bytes(data, name, pre)
    if pre:
        return ParsedSlice(name=name, fmt="jsonl", diags=pre, size=len(data))
    return parse_jsonl(text, name, max_bytes=max_bytes)


def _header(out: ParsedSlice, obj: Mapping, at: str) -> None:
    if obj.get(ATLAS_KEY) != 1 or isinstance(obj.get(ATLAS_KEY), bool):
        out.diags.append(dg.st004("header", at, obj.get(ATLAS_KEY), "1", "the only supported format version is 1"))
    for key, value in obj.items():
        if key == ATLAS_KEY:
            continue
        if key != "mode":
            out.diags.append(dg.st004("header", at, key, "_atlas, mode", "the header takes only _atlas and mode"))
        elif isinstance(value, str):
            out.mode = value
        else:
            out.diags.append(dg.st007("header", at, value, "asis or tobe", "mode is a string"))


def dumps_line(obj: Mapping[str, Any]) -> str:
    """One compact JSON line (keys in insertion order, non-ASCII kept)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def header_line(mode: str | None) -> str:
    return dumps_line({ATLAS_KEY: 1, **({"mode": mode} if mode and mode != "asis" else {})})


def row_line(section: str, values: Mapping[str, Any]) -> str:
    """The line of one row: ``section`` first, then the positional columns, then the options, each in schema order."""
    spec = SECTIONS[section]
    obj: dict[str, Any] = {SECTION_KEY: section}
    for f in spec.columns + spec.options:
        if f.name in values:
            obj[f.name] = values[f.name]
    return dumps_line(obj)
