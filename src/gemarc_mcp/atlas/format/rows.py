"""Row objects and the section schema shared by the two front-ends (``.atl`` and ``.jsonl``).

Both front-ends turn authored text into the same ``Row`` objects: a section name, a dict of *typed* values (positional
cells and options together, ``@set`` defaults already applied) and the ``file:line`` of the source line. The expander
(``atlas/expand.py``) reads only rows; the encoder (``atlas/encode.py``) writes them back.

The schema below is the single list of sections, positional columns and options (design 3.2). It carries, per field, a
*syntax type* (how a value is read from text) and a *role* (which semantic check the expander applies: id grammar,
vocabulary group, ...). Nothing here knows the vocabulary: roles only name a check. Standard library only.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import diag as dg

MAX_SLICE_BYTES = 64 * 1024
#: Nesting limit of a JSON value inside a row. The model limit (64) counts from the model root and an option value sits up to
#: six containers below it (model, cases, case, events, event, params), so the row limit leaves that much headroom.
MAX_JSON_NESTING = 56
#: Largest absolute value of an authored whole number (levels, chapters, milliseconds).
MAX_INT = 10 ** 15

# ---------------------------------------------------------------------------------------------------------------
# Raw value kinds produced by the text front-ends
# ---------------------------------------------------------------------------------------------------------------


class Bare(str):
    """A bare ``.atl`` token (no quotes): the only form that may be read as a number or boolean."""


class Quoted(str):
    """A ``"quoted string"`` value (JSON string syntax)."""


# ---------------------------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------------------------

# syntax types: text, int, bool, expects (comma list of ``node`` / ``node=State``), ids, strs, edgeev (ids or ``^``),
# obj, list, intlist (int or list of ints), rewrite (comma list of ``edge`` / ``edge=disposition``), elem (id or object)
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
    derived: frozenset = frozenset()

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

    def is_column(self, name: str) -> bool:
        return name in self.column_names


def _t(name, role="free"):
    return Field(name, "text", role)


def _o(name, type_="text", role="free", required=False):
    return Field(name, type_, role, required)


_SECTION_LIST = (
    Section("buildings", (_t("id", "id"), _t("name")), (_o("trust", role="enum:trust"),)),
    Section("floors", (_t("id", "id"), _t("building", "id"), _t("name"), Field("level", "int"))),
    Section("rooms", (_t("id", "id"), _t("floor", "id"), _t("name")),
            (_o("layer", role="enum:layers"), _o("tier", role="enum:tiers"), _o("area", role="token"))),
    Section("nodes", (_t("id", "aid"), _t("name"), _t("sub")),
            (_o("room", role="id", required=True), _o("kind", role="enum:kinds", required=True), _o("ev", "ids", "ids"),
             _o("verif", role="enum:verification"), _o("props", "obj"), _o("group", role="token"),
             _o("level", role="enum:levels"))),
    Section("edges", (_t("from", "aid"), _t("rel", "enum:relations"), _t("to", "aid"), _t("label")),
            (_o("ev", "edgeev", "ids"), _o("verif", role="enum:verification"), _o("card", role="card"),
             _o("alias", role="aid"), _o("group", role="token"))),
    Section("evidences", (_t("id", "id"), _t("file"), _t("anchor"), _t("topic")), (_o("repo", role="token"),)),
    Section("machines", (_t("id", "aid"), _t("initial", "state")),
            (_o("owner", role="aid"), _o("err"), _o("ev", "ids", "ids"))),
    Section("transitions", (_t("from", "state"), _t("to", "state")),
            (_o("machine", role="aid", required=True), _o("guard", "obj"), _o("err"), _o("id", role="id"))),
    Section("tests", (_t("id", "id"), _t("title"))),
    Section("cases", (_t("id", "id"), _t("cat", "enum:caseCategories"), _t("title")),
            (_o("req", "ids", "ids"), _o("ev", "ids", "ids"), _o("policy", "obj"), _o("ctx", "obj"), _o("init", "obj"),
             _o("tests", "ids", "ids"))),
    Section("events", (_t("type", "enum:eventTypes"), _t("from", "aid"), _t("to", "aid")),
            (_o("case", role="id", required=True), _o("ruleId"), _o("latencyMs", "intlist"), _o("ack", "bool"),
             _o("group"), _o("section", "bool"), _o("when", "obj"), _o("params", "obj"), _o("fault", "obj"),
             _o("edge", role="id"), _o("guard", "obj"), _o("dedupKey"), _o("role", role="enumplain:eventRoles"),
             _o("errorCode"), _o("ms", "int"))),
    Section("expect", (_t("case", "id"), Field("nodes", "expects")),
            (_o("by", role="enumplain:expectBy"), _o("outcomes", "list"))),
    Section("interventions", (_t("id", "id"), _t("type", "enum:interventionTypes"), _t("description")),
            (_o("target", role="id"), _o("applies", "ids", "ids", required=True), _o("class", role="enum:outcomeClasses"),
             _o("terminal", "obj"), _o("status", role="enumplain:runStatuses"), _o("why"), _o("note"), _o("params", "obj"),
             _o("element", "elem"), _o("use", role="enum:elementKinds"), _o("rewrite", "rewrite"), _o("parts", "list"))),
    Section("requirements", (_t("id", "id"), _t("title"), _t("source")),
            (_o("nodes", "ids", "aids"), _o("edges", "ids", "ids"), _o("tests", "ids", "ids"), _o("ev", "ids", "ids"),
             _o("tbd", "ids", "ids"), _o("design", "strs"))),
    Section("tbds", (_t("id", "id"), _t("title"), _t("decision")), (_o("ev", "ids", "ids"),)),
    Section("policies", (_t("id", "id"), _t("text")),
            (_o("src", role="id", required=True), _o("scope", role="scope"), _o("level", role="enum:levels"),
             Field("chapter", "int", "chapter"), _o("kind", role="enum:policyKinds"), _o("by", role="enumplain:policyBy"))),
)

# a positional column whose role names a vocabulary group is an enum cell: ``role`` lives in the Field
SECTIONS: dict[str, Section] = {s.name: s for s in _SECTION_LIST}
SECTION_ORDER = tuple(SECTIONS)

#: Keys an author must never write because the expander derives them (DV-001), per section.
DERIVED = {name: frozenset() for name in SECTIONS}
DERIVED.update({
    "nodes": frozenset({"portIds", "ports"}),
    "edges": frozenset({"id", "type", "directed", "static", "sourcePortId", "targetPortId", "guardId"}),
    "evidences": frozenset({"sourceKind", "lineRange", "verification"}),
    "machines": frozenset({"nodeId", "states", "terminal"}),
    "events": frozenset({"seq"}),
    "interventions": frozenset({"chain"}),
})

#: Section groups of the encoder's files, in dependency-friendly order (the expander does not depend on the order).
FILE_GROUPS = (
    ("space", ("buildings", "floors", "rooms")),
    ("structure", ("nodes", "edges", "evidences", "machines", "transitions")),
    ("scenarios", ("tests", "cases", "events", "expect", "interventions", "requirements", "tbds", "policies")),
)


def column_kind(section: str, name: str) -> str:
    """Coarse kind of a positional column for tools that mutate or explain rows: id, enum, int, free."""
    f = SECTIONS[section].field(name)
    if f is None:
        return "free"
    if f.type == "int":
        return "int"
    if f.type == "expects":
        return "expects"
    if f.role.startswith("enum"):
        return "enum"
    if f.role in ("id", "aid", "state"):
        return "id"
    return "free"


def option_regex(section: Section) -> "re.Pattern[str] | None":
    names = section.option_names
    if not names:
        return None
    return re.compile(r"(?:^|[ \t])(%s)=\S" % "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))


#: Every option name of every section. A text cell is checked against this union, not only against the options of its own
#: section: a deleted section header re-types the rows below it, and their options then land inside a text cell of the
#: section above (``src=T1 level=BD`` inside a decision cell).
ALL_OPTION_NAMES = frozenset(name for spec in SECTIONS.values() for name in spec.option_names)
_ANY_OPTION_RE = re.compile(r"(?:^|[ \t])(%s)=\S" % "|".join(re.escape(n) for n in sorted(ALL_OPTION_NAMES, key=len, reverse=True)))


def looks_like_option(section: str, text: str) -> str | None:
    """The option-looking token (``ev=EVD-1``, ``src=T1``) inside a text cell of ``section``, else ``None``.

    Any option name of any section counts (``ALL_OPTION_NAMES``); ``section`` is kept for callers that pass it.
    """
    m = _ANY_OPTION_RE.search(text)
    return text[m.start(1):].split()[0] if m else None


# ---------------------------------------------------------------------------------------------------------------
# Row
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Row:
    """One authored row. Equality ignores ``file``, ``line`` and ``explicit`` (a ``.jsonl`` row equals its ``.atl`` twin)."""

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
    """What a front-end returns for one file: rows, structural diagnostics, ``@final`` and ``@pending`` declarations."""

    name: str
    rows: list = field(default_factory=list)
    diags: list = field(default_factory=list)
    finals: list = field(default_factory=list)
    pendings: list = field(default_factory=list)
    mode: str | None = None
    size: int = 0
    fmt: str = "atl"

    def errors(self) -> list:
        return [d for d in self.diags if d.severity == dg.ERROR]


# ---------------------------------------------------------------------------------------------------------------
# Value coercion (shared by both front-ends)
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


def _split_list(raw: str, what: str, allow_space: bool) -> list:
    items = [part.strip() for part in raw.split(",")]
    if any(not item for item in items):
        raise FieldError("ST-007", raw, f"a comma-separated list of {what} without empty items",
                         "remove the stray comma, or write [] for an explicitly empty list")
    if not allow_space:
        for item in items:
            if re.search(r"\s", item):
                raise FieldError("ST-007", item, f"{what} without spaces",
                                 "separate ids with commas only; quote the whole value if it really contains spaces")
    return items


def _unique(items: list, what: str, unique: bool) -> list:
    if unique:
        seen: set = set()
        for item in items:
            if item in seen:
                raise FieldError("ST-007", item, f"each of the {what} once", f"{item} is listed twice; remove the repeat")
            seen.add(item)
    return items


def _list_of_strings(raw, what: str, allow_space: bool, unique: bool = False) -> list:
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise FieldError("ST-007", item, f"a list of non-empty strings ({what})", "write every item as a string")
        return _unique(list(raw), what, unique)
    if isinstance(raw, str):
        return _unique(_split_list(str(raw), what, allow_space), what, unique)
    raise FieldError("ST-007", raw, f"a comma-separated list or a JSON list of {what}", "write ev=A,B or ev=[\"A\",\"B\"]")


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
        raise FieldError("ST-007", raw, "a whole number (at most 15 digits)", "write the number without quotes, for example 20")
    if type_ == "bool":
        if isinstance(raw, Bare) and raw in ("true", "false"):
            return raw == "true"
        if isinstance(raw, bool):
            return raw
        raise FieldError("ST-007", raw, "true or false", "write true or false in lower case, without quotes")
    if type_ == "expects":
        if isinstance(raw, str) and str(raw).strip() == "-":
            return []
        items = _list_of_strings(raw, "node or node=State entries", False, True)
        return items
    if type_ == "ids":
        if isinstance(raw, list) and not raw:
            return []
        return _list_of_strings(raw, "ids", False, True)
    if type_ == "strs":
        if isinstance(raw, list) and not raw:
            return []
        return _list_of_strings(raw, "strings", True)
    if type_ == "rewrite":
        if isinstance(raw, list) and not raw:
            return []
        return _list_of_strings(raw, "edge ids or edge=disposition entries", False, True)
    if type_ == "edgeev":
        if isinstance(raw, str) and raw == "^":
            return "^"
        return coerce("ids", raw)
    if type_ == "elem":
        if isinstance(raw, dict):
            return raw
        return coerce("text", raw)
    if type_ == "obj":
        if isinstance(raw, dict):
            return raw
        raise FieldError("ST-007", raw, "a JSON object {...}", "write the value as a JSON object, for example props={\"k\":\"v\"}")
    if type_ == "list":
        if isinstance(raw, list):
            return raw
        raise FieldError("ST-007", raw, "a JSON array [...]", "write the value as a JSON array")
    if type_ == "intlist":
        if _bare_int(raw) is not None:
            return _bare_int(raw)
        if _is_int(raw):
            return raw
        if isinstance(raw, list) and raw and all(_is_int(x) for x in raw):
            return list(raw)
        raise FieldError("ST-007", raw, "a whole number or a non-empty JSON array of whole numbers", "write 20 or [20,40]")
    raise FieldError("ST-007", raw, type_, "internal: unknown field type")


def closest(name: str, pool) -> str:
    """The closest allowed name (case-insensitive) for a hint, or ``""``."""
    lowered = {p.lower(): p for p in pool}
    hit = difflib.get_close_matches(str(name).lower(), list(lowered), n=1, cutoff=0.6)
    return lowered[hit[0]] if hit else ""


# ---------------------------------------------------------------------------------------------------------------
# Row construction (shared)
# ---------------------------------------------------------------------------------------------------------------


def make_row(section: Section, raw: Mapping[str, Any], file: str, line: int, diags: list, defaults: Mapping[str, Any] | None = None,
             *, path_prefix: str | None = None) -> Row | None:
    """Validate and type one row's raw values; append diagnostics; return the ``Row`` or ``None`` when it is unusable.

    ``raw`` maps column and option names to raw values (positional columns must be present). ``defaults`` are typed
    ``@set`` values for option names. A row with any syntax problem is dropped (its diagnostics say why).
    """
    at = f"{file}:{line}"
    prefix = path_prefix or section.name
    problems = len(diags)
    for key in raw:
        if section.field(key) is None:
            if key in DERIVED.get(section.name, ()):
                diags.append(dg.dv001(f"{prefix}.{key}", at, key,
                                      "derived; delete it" + (" (a shared or repeated triple needs alias=)" if key == "id" else "")))
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
            values[col.name] = coerce(col.type if not col.type.startswith("enum") else "text", raw[col.name])
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
        if opt.name not in values and defaults and opt.name in defaults and not _default_exempt(section.name, opt.name, values):
            values[opt.name] = defaults[opt.name]
        if opt.required and opt.name not in values:
            diags.append(dg.st001(f"{prefix}.{opt.name}", at, "missing", f"a value for {opt.name}",
                                  f"add {opt.name}=... to the row or '@set {opt.name}=...' above it"))
    if len(diags) != problems:
        return None
    return Row(section.name, values, file, line, explicit)


def _default_exempt(section: str, key: str, values: Mapping) -> bool:
    """A ``@set`` default never applies where the expander derives the value: a ``user:`` evidence has no repository."""
    return section == "evidences" and key == "repo" and str(values.get("file", "")).startswith("user:")


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
