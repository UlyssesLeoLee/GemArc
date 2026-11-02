"""Row objects and the section schema of the interface family ``gemarc-atlas-ui/1`` (design A3 D-10, 4.2).

This module is a COPY of the row layer of ``atlas/format/rows.py``, adapted to the interface family: its own section table
(``SECTIONS``), derived-key table (``DERIVED``) and option-name union (``ALL_OPTION_NAMES``), so that nothing the interface
authoring format declares can ever reach the 5D parser (and the other way round). ``format/rows.py`` is not imported and not
changed. Nothing here knows the vocabulary: a field's *role* only names a check that ``expand.py`` applies (id grammar,
vocabulary group, ...).

The six sections: ``screens``, ``variants``, ``widgets``, ``nav`` (the prototype itself, slices named ``ui-*``) and
``conform``, ``diffs`` (a frontend check verdict, only in slices named ``ui-verdict-*``). Standard library only.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import diag as dg

MAX_SLICE_BYTES = 64 * 1024
#: Nesting limit of a JSON value inside a row (a widget ``data`` option).
MAX_JSON_NESTING = 56
#: Largest absolute value of an authored whole number.
MAX_INT = 10 ** 15

# ---------------------------------------------------------------------------------------------------------------
# Raw value kinds produced by the text front-end
# ---------------------------------------------------------------------------------------------------------------


class Bare(str):
    """A bare ``.atl`` token (no quotes): the only form that may be read as a number or boolean."""


class Quoted(str):
    """A ``"quoted string"`` value (JSON string syntax)."""


# ---------------------------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------------------------

# syntax types: text, int, bool, ids (comma list or JSON list of ids), obj (a JSON object)
SCALAR_TYPES = ("text", "int", "bool")


@dataclass(frozen=True)
class Field:
    name: str
    type: str = "text"
    role: str = "free"
    required: bool = False  # options only: a row must carry it (or get it from ``@set``)


@dataclass(frozen=True)
class Section:
    name: str
    columns: tuple[Field, ...]
    options: tuple[Field, ...] = ()

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.columns)

    @property
    def option_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.options)

    @property
    def header(self) -> str:
        cols = list(self.column_names) + (["opts"] if self.options else [])
        return f"## {self.name}: " + " | ".join(cols)

    def field(self, name: str) -> Field | None:
        for f in self.columns + self.options:
            if f.name == name:
                return f
        return None


def _t(name, role="free"):
    return Field(name, "text", role)


def _o(name, type_="text", role="free", required=False):
    return Field(name, type_, role, required)


_SECTION_LIST = (
    Section("screens", (_t("id", "sid"), _t("title"), _t("purpose")),
            (_o("kind", role="enum:screenKinds"), _o("frame", role="enum:frames"), _o("route", role="route"), _o("start", "bool"),
             _o("actor", role="aid"), _o("req", "ids", "ids"), _o("group", role="token"), _o("align", role="enum:aligns"))),
    Section("variants", (_t("screen", "sid"), _t("name", "vname"), _t("note")),
            (_o("kind", role="enum:variantKinds"), _o("machine", role="aid"), _o("state", role="state"), _o("text"))),
    Section("widgets", (_t("local", "lid"), _t("label")),
            (_o("screen", role="sid", required=True), _o("kind", role="enum:widgetKinds", required=True), _o("parent", role="lid"),
             _o("w", role="size"), _o("h", role="size"), _o("cols", "int", "cols"), _o("in", "ids", "vnames"), _o("bind", role="field"),
             _o("emph", role="enum:emphasis"), _o("data", "obj"), _o("api", role="id"), _o("req", "ids", "ids"),
             _o("align", role="enum:aligns"))),
    Section("nav", (_t("from", "uref"), _t("to", "vref"), _t("label")),
            (_o("on", role="enum:triggers"), _o("guard"), _o("api", role="id"), _o("case", role="id"), _o("rule"),
             _o("req", "ids", "ids"), _o("alias", role="aid"))),
    # the frontend check verdict (design 4.8). The positional column ``screen`` and the screen hash cannot share a name, so the
    # hash option is ``sha`` (the design text writes ``screen=<sha8>``; see the issues of the u1 handoff).
    Section("conform", (_t("screen", "sid"), _t("verdict", "enum:verdicts"), _t("basis")),
            (_o("sha", role="sha8"), _o("checked", "int", "count"), _o("repo", role="token"))),
    Section("diffs", (_t("ref", "ref"), _t("kind", "enum:diffKinds"), _t("note")),
            (_o("expected"), _o("found"), _o("basis"))),
)

SECTIONS: dict[str, Section] = {s.name: s for s in _SECTION_LIST}
#: Sections of a prototype slice (``ui-*``) and of a verdict slice (``ui-verdict-*``): a slice holds one kind only, so a verdict
#: row can never reach the prototype model and ``ui_sha8`` does not move when a verdict is written.
PROTOTYPE_SECTIONS = ("screens", "variants", "widgets", "nav")
VERDICT_SECTIONS = ("conform", "diffs")

#: Keys an author must never write because the expander derives them (DV-001), per section.
DERIVED = {
    "screens": frozenset({"variantNames", "widgetIds", "navOutIds", "navInIds"}),
    "variants": frozenset({"id"}),
    "widgets": frozenset({"id", "screenId", "parentId", "order", "depth", "childIds", "container", "navSource", "interactive"}),
    "nav": frozenset({"id", "fromScreenId", "toScreenId"}),
    "conform": frozenset(),
    "diffs": frozenset(),
}

#: Every option name of every section of the family. A text cell is checked against this union (ST-006): a deleted section
#: header re-types the rows below it and their options then land inside a text cell of the section above.
ALL_OPTION_NAMES = frozenset(name for spec in SECTIONS.values() for name in spec.option_names)
_ANY_OPTION_RE = re.compile(r"(?:^|[ \t])(%s)=\S" % "|".join(re.escape(n) for n in sorted(ALL_OPTION_NAMES, key=lambda n: (-len(n), n))))


def looks_like_option(text: str) -> str | None:
    """The option-looking token (``kind=button``, ``in=error``) inside a text cell, else ``None``."""
    m = _ANY_OPTION_RE.search(text)
    return text[m.start(1):].split()[0] if m else None


# ---------------------------------------------------------------------------------------------------------------
# Row
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Row:
    """One authored row. Equality ignores ``file``, ``line`` and ``explicit``."""

    section: str
    values: Mapping[str, Any]
    file: str = ""
    line: int = 0
    explicit: frozenset = field(default_factory=frozenset)

    @property
    def at(self) -> str:
        return f"{self.file}:{self.line}"

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def __eq__(self, other) -> bool:
        return isinstance(other, Row) and self.section == other.section and dict(self.values) == dict(other.values)

    def __hash__(self) -> int:
        return hash((self.section, tuple(sorted(self.values))))


@dataclass
class ParsedSlice:
    """What the front-end returns for one file: rows, structural diagnostics, ``@final`` and ``@pending`` declarations."""

    name: str
    rows: list = field(default_factory=list)
    diags: list = field(default_factory=list)
    finals: list = field(default_factory=list)
    pendings: list = field(default_factory=list)
    size: int = 0
    fmt: str = "atl"

    def errors(self) -> list:
        return [d for d in self.diags if d.severity == dg.ERROR]


# ---------------------------------------------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------------------------------------------


class FieldError(Exception):
    """A value cannot be read as its field type. ``rule`` is ST-001 (empty) or ST-007 (malformed)."""

    def __init__(self, rule: str, got: Any, expected: str, hint: str):
        super().__init__(expected)
        self.rule, self.got, self.expected, self.hint = rule, got, expected, hint


_INT_RE = re.compile(r"-?[0-9]{1,16}")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_INT


def _bare_int(raw) -> "int | None":
    """The value of a bare whole number token, or ``None`` (not a number, too long, or outside +-MAX_INT)."""
    if isinstance(raw, Bare) and _INT_RE.fullmatch(raw):
        value = int(raw)
        if abs(value) <= MAX_INT:
            return value
    return None


def _split_list(raw: str, what: str) -> list:
    items = [part.strip() for part in raw.split(",")]
    if any(not item for item in items):
        raise FieldError("ST-007", raw, f"a comma-separated list of {what} without empty items",
                         "remove the stray comma, or write [] for an explicitly empty list")
    for item in items:
        if re.search(r"\s", item):
            raise FieldError("ST-007", item, f"{what} without spaces",
                             "separate items with commas only; quote the whole value if it really contains spaces")
    return items


def _unique(items: list, what: str) -> list:
    seen: set = set()
    for item in items:
        if item in seen:
            raise FieldError("ST-007", item, f"each of the {what} once", f"{item} is listed twice; remove the repeat")
        seen.add(item)
    return items


def _list_of_strings(raw, what: str) -> list:
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise FieldError("ST-007", item, f"a list of non-empty strings ({what})", "write every item as a string")
        return _unique(list(raw), what)
    if isinstance(raw, str):
        return _unique(_split_list(str(raw), what), what)
    raise FieldError("ST-007", raw, f"a comma-separated list or a JSON list of {what}", "write req=A,B or req=[\"A\",\"B\"]")


def coerce(type_: str, raw: Any) -> Any:
    """Typed value of a raw value; raises ``FieldError``. ``raw`` is ``Bare``/``Quoted``/plain ``str`` or a JSON value."""
    if type_ == "text":
        if isinstance(raw, str):
            if not raw.strip():
                raise FieldError("ST-001", raw, "non-empty text", "positional cells and option values are never empty")
            return str(raw)
        raise FieldError("ST-007", raw, "text", "write a bare word or a \"quoted string\"")
    if type_ == "int":
        if _bare_int(raw) is not None:
            return _bare_int(raw)
        if _is_int(raw):
            return raw
        raise FieldError("ST-007", raw, "a whole number (at most 15 digits)", "write the number without quotes, for example 3")
    if type_ == "bool":
        if isinstance(raw, Bare) and raw in ("true", "false"):
            return raw == "true"
        if isinstance(raw, bool):
            return raw
        raise FieldError("ST-007", raw, "true or false", "write true or false in lower case, without quotes")
    if type_ == "ids":
        if isinstance(raw, list) and not raw:
            return []
        return _list_of_strings(raw, "ids")
    if type_ == "obj":
        if isinstance(raw, dict):
            return raw
        raise FieldError("ST-007", raw, "a JSON object {...}", "write the value as a JSON object, for example data={\"cols\":[\"a\"]}")
    raise FieldError("ST-007", raw, type_, "internal: unknown field type")


def closest(name: str, pool) -> str:
    """The closest allowed name (case-insensitive) for a hint, or ``""``."""
    lowered = {p.lower(): p for p in pool}
    hit = difflib.get_close_matches(str(name).lower(), list(lowered), n=1, cutoff=0.6)
    return lowered[hit[0]] if hit else ""


# ---------------------------------------------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------------------------------------------


def make_row(section: Section, raw: Mapping[str, Any], file: str, line: int, diags: list, defaults: Mapping[str, Any] | None = None) -> Row | None:
    """Validate and type one row's raw values; append diagnostics; return the ``Row`` or ``None`` when it is unusable.

    ``raw`` maps column and option names to raw values (positional columns must be present). ``defaults`` are typed ``@set``
    values for option names. A row with any syntax problem is dropped (its diagnostics say why).
    """
    at = f"{file}:{line}"
    prefix = section.name
    problems = len(diags)
    for key in raw:
        if section.field(key) is None:
            if key in DERIVED.get(section.name, ()):
                diags.append(dg.dv001(f"{prefix}.{key}", at, key, "derived; delete it"))
            else:
                near = closest(key, section.column_names + section.option_names)
                diags.append(dg.st004(f"{prefix}.opts", at, key, section.column_names + section.option_names,
                                      f"unknown key; did you mean {near}?" if near else ""))
    values: dict[str, Any] = {}
    for col in section.columns:
        if col.name not in raw:
            diags.append(dg.st001(f"{prefix}.{col.name}", at, "missing", "a value", f"add the {col.name} cell"))
            continue
        try:
            values[col.name] = coerce("text", raw[col.name])
        except FieldError as ex:
            rule = dg.st001 if ex.rule == "ST-001" else dg.st007
            diags.append(rule(f"{prefix}.{col.name}", at, ex.got, ex.expected, ex.hint))
    for opt in section.options:
        if opt.name in raw:
            try:
                values[opt.name] = coerce(opt.type, raw[opt.name])
            except FieldError as ex:
                rule = dg.st001 if ex.rule == "ST-001" else dg.st007
                diags.append(rule(f"{prefix}.{opt.name}", at, ex.got, ex.expected, ex.hint))
    if len(diags) != problems:
        return None
    explicit = frozenset(values)
    for opt in section.options:
        if opt.name not in values and defaults and opt.name in defaults:
            values[opt.name] = defaults[opt.name]
        if opt.required and opt.name not in values:
            diags.append(dg.st001(f"{prefix}.{opt.name}", at, "missing", f"a value for {opt.name}",
                                  f"add {opt.name}=... to the row or '@set {opt.name}=...' above it"))
    if len(diags) != problems:
        return None
    return Row(section.name, values, file, line, explicit)


def json_loads_strict(text: str, start: int = 0):
    """``(value, end)`` of the JSON value at ``text[start:]``; rejects duplicate keys, NaN/Infinity (also ``1e999``), lone
    surrogates and deep nesting. Raises ``ValueError`` only."""
    import json
    import math

    def pairs(items):
        out: dict = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def constant(name):
        raise ValueError(f"{name} is not JSON")

    def number(token):
        value = float(token)
        if not math.isfinite(value):
            raise ValueError(f"{token} is not a finite number")
        return value

    decoder = json.JSONDecoder(object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
    try:
        value, end = decoder.raw_decode(text, start)
    except RecursionError as ex:
        raise ValueError("nested too deeply") from ex
    if _too_deep(value):
        raise ValueError(f"nested deeper than {MAX_JSON_NESTING} levels")
    if has_surrogate(value):
        raise ValueError("a lone UTF-16 surrogate (a \\ud800-style escape) cannot be stored")
    return value, end


SURROGATE_RE = re.compile("[\ud800-\udfff]")


def has_surrogate(value) -> bool:
    """True when a string (key or value) inside the JSON value holds a lone UTF-16 surrogate."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if SURROGATE_RE.search(item):
                return True
        elif isinstance(item, dict):
            for k, v in item.items():
                stack.append(k)
                stack.append(v)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _too_deep(value) -> bool:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_NESTING:
            return True
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return False
