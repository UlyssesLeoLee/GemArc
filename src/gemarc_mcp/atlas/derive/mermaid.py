"""Mermaid text builders for component, class, state and ER diagrams.

Each builder takes plain structured data (the ``data`` of a diagram unit, see ``diagrams.py``) and returns
``(text, issues)``. **No builder raises**: a missing or malformed field becomes an issue plus an explicit
``[NOT-AUTHORED:slot]`` marker inside the drawing, and the rest of the diagram is still produced.

Mermaid safety (design risk 14): identifiers in the drawing are generated (``n1``, ``s2``, ``t3`` ...) so an authored id
such as ``svc.loan`` or ``a:b`` never has to be a valid Mermaid identifier and two ids can never collide; every piece of
authored text is either inside a quoted label (quotes, angle brackets, ``&``, ``#``, ``%``, ``;``, braces, square
brackets, ``|``, backslash, backtick written as entities) or, where Mermaid reads text unquoted (class members,
state-transition labels), reduced to a safe alphabet. Authored text can therefore never produce a marker: markers are
inserted by this module as ``Raw`` text only.

Extension point: ``register_builder(kind, function)``. The sequence builder arrives with the oracle lane and only for
sealed items; ``BUILDERS`` is a plain registry so adding it needs no change here.
"""
from __future__ import annotations

import re
from typing import Callable, Mapping

from . import i18n as T

RULE_DIAGRAM = "DG-011"
RULE_UNKNOWN_KIND = "DG-008"
RULE_TOO_LARGE = "DG-009"
RULE_INTERNAL = "DG-099"
MAX_LABEL = 80
MAX_TEXT = 45000

_ENTITY = {'"': "quot", "<": "lt", ">": "gt", "&": "amp"}
_NUMERIC = set("#%;{}[]|\\`~")
_RAW_KEEP = set(" _.-,()/+=@'")
_ATTR_BAD = re.compile(r"[^A-Za-z0-9_]")
_TOKEN_BAD = re.compile(r"[^A-Za-z0-9_]")


Raw = T.Raw


def label(value, limit: int = MAX_LABEL) -> str:
    """Text for a quoted Mermaid label (without the quotes): control characters gone, risky characters as entities."""
    if isinstance(value, Raw):
        return str(value)
    text = T.clip(value, limit)
    out = []
    for ch in text:
        if ch in _ENTITY:
            out.append(f"#{_ENTITY[ch]};")
        elif ch in _NUMERIC:
            out.append(f"#{ord(ch)};")
        else:
            out.append(ch)
    return "".join(out)


def raw(value, limit: int = MAX_LABEL) -> str:
    """Text for an unquoted position: letters, digits and a few punctuation marks only, the rest becomes ``_``."""
    text = T.clip(value, limit)
    return "".join(ch if (ch.isalnum() or ch in _RAW_KEEP) else "_" for ch in text)


def quoted(value, limit: int = MAX_LABEL) -> str:
    return '"' + label(value, limit) + '"'


def _slot(slot) -> str:
    """A marker slot for drawings: a raw ``#`` (the entity escape of Mermaid labels) never appears in a slot, so no renderer can read it as one."""
    return T.slot_name(slot).replace("#", ".")


def marker(slot) -> Raw:
    """The NOT-AUTHORED marker as trusted label text."""
    return Raw(T.not_authored(_slot(slot)))


def with_marker(slot, shown=None) -> Raw:
    """Label text carrying the marker, preceded by the escaped id or name it belongs to when given."""
    mark = T.not_authored(_slot(slot))
    return Raw(f"{label(shown)} {mark}" if shown else mark)


def has(container, key) -> bool:
    """``key in container`` that tolerates an unhashable key (garbage data)."""
    return isinstance(key, str) and key in container


def present(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


class IdMap:
    """Deterministic authored-id -> Mermaid-id mapping: ``<prefix>1``, ``<prefix>2`` ... in first-seen order."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self._ids: dict = {}

    def __call__(self, authored) -> str:
        key = authored if isinstance(authored, str) else T.scalar(authored)
        if key not in self._ids:
            self._ids[key] = f"{self.prefix}{len(self._ids) + 1}"
        return self._ids[key]

    def __contains__(self, authored) -> bool:
        return authored in self._ids

    def __len__(self) -> int:
        return len(self._ids)


def _data(data) -> Mapping:
    return data if isinstance(data, Mapping) else {}


def _items(data, key: str) -> list:
    value = _data(data).get(key)
    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, list) else []


def _texts(data, key: str) -> list:
    value = _data(data).get(key)
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def _issue(path, hint, got=None, severity: str = "WARN") -> dict:
    return T.issue(RULE_DIAGRAM, severity, path, hint, got=got)


# --------------------------------------------------------------------------------------------- component

_SHAPES = {
    "round": ('("', '")'), "hex": ('{{"', '"}}'), "box": ('["', '"]'), "tri": ('[/"', '"/]'),
    "diamond": ('{"', '"}'), "octa": ('{{"', '"}}'), "crown": ('[["', '"]]'),
}


def build_component(data) -> tuple:
    """``flowchart LR`` of grouped nodes and edges.

    ``data``: ``{"groups": [{"id", "label", "nodeIds"}], "nodes": [{"id", "label", "shape"}],
    "edges": [{"id", "from", "to", "label", "static"}]}``. An edge whose endpoint is not a drawn node is dropped with an
    issue; a node without a label is drawn with its id and a marker.
    """
    issues: list = []
    nid = IdMap("n")
    drawn: dict = {}
    lines = ["flowchart LR"]
    declared: list = []
    for node in _items(data, "nodes"):
        authored = node.get("id")
        if not present(authored) or authored in drawn:
            issues.append(_issue("nodes", "a diagram node needs a unique id", got=T.scalar(authored)))
            continue
        if present(node.get("label")):
            shown = node["label"]
        else:
            issues.append(_issue(f"nodes.{T.slot_name(authored)}", "node has no name; drawn with its id", got=authored))
            shown = with_marker(T.slot_name("node.name", authored), authored)
        shape = _SHAPES.get(node.get("shape") if isinstance(node.get("shape"), str) else "", _SHAPES["box"])
        drawn[authored] = nid(authored)
        declared.append((authored, f"{drawn[authored]}{shape[0]}{label(shown)}{shape[1]}"))
    declared_by_id = dict(declared)
    placed: set = set()
    gid = IdMap("g")
    for group in _items(data, "groups"):
        wanted = group.get("nodeIds") if isinstance(group.get("nodeIds"), list) else []
        members = [m for m in wanted if isinstance(m, str) and m in drawn and m not in placed]
        if not members:
            continue
        if present(group.get("label")):
            shown = group["label"]
        else:
            issues.append(_issue(f"groups.{T.slot_name(group.get('id'))}", "the group has no name; marker drawn", got=T.scalar(group.get("id"))))
            shown = with_marker(T.slot_name("group.name", group.get("id")))
        lines.append(f"  subgraph {gid(group.get('id'))}[{quoted(shown)}]")
        for member in members:
            lines.append(f"    {declared_by_id[member]}")
            placed.add(member)
        lines.append("  end")
    for authored, text in declared:
        if authored not in placed:
            lines.append(f"  {text}")
    for edge in _items(data, "edges"):
        a, b = edge.get("from"), edge.get("to")
        if not (has(drawn, a) and has(drawn, b)):
            issues.append(_issue(f"edges.{T.slot_name(edge.get('id'))}", "edge endpoint is not a drawn node: edge left out",
                                 got=T.scalar(edge.get("id"))))
            continue
        arrow = "-.->" if edge.get("static") is True else "-->"
        if present(edge.get("label")):
            text = edge["label"]
        else:
            issues.append(_issue(f"edges.{T.slot_name(edge.get('id'))}.label", "edge has no label",
                                 got=T.scalar(edge.get("id")), severity="INFO"))
            text = marker(T.slot_name("edge.label", edge.get("id")))
        lines.append(f"  {drawn[a]} {arrow}|{quoted(text)}| {drawn[b]}")
    if not drawn:
        lines.append(f"  empty[{quoted(marker('diagram.nodes'))}]")
        issues.append(_issue("nodes", "no node to draw"))
    return _finish(lines, issues)


# --------------------------------------------------------------------------------------------- class

def build_class(data) -> tuple:
    """``classDiagram`` of classes (and enumerations) with members and relations.

    ``data``: ``{"classes": [{"id", "label", "annotation", "members": [str], "missing": [slot]}],
    "relations": [{"from", "to", "label"}]}``. A class with ``missing`` slots gets one marker member per slot.
    """
    issues: list = []
    cid = IdMap("c")
    lines = ["classDiagram"]
    known: set = set()
    for cls in _items(data, "classes"):
        authored = cls.get("id")
        if not present(authored) or authored in known:
            issues.append(_issue("classes", "a class needs a unique id", got=T.scalar(authored)))
            continue
        known.add(authored)
        if present(cls.get("label")):
            shown = cls["label"]
        else:
            issues.append(_issue(f"classes.{T.slot_name(authored)}", "class has no name; drawn with its id", got=authored))
            shown = with_marker(T.slot_name("node.name", authored), authored)
        mid = cid(authored)
        lines.append(f"  class {mid}[{quoted(shown)}]")
        annotation = cls.get("annotation")
        if isinstance(annotation, str) and re.fullmatch(r"[A-Za-z]{1,20}", annotation):
            lines.append(f"  <<{annotation}>> {mid}")
        for member in cls.get("members") if isinstance(cls.get("members"), list) else []:
            if present(member):
                lines.append(f"  {mid} : {raw(member, 70)}")
            else:
                issues.append(_issue(f"classes.{T.slot_name(authored)}.members", "member is not text: left out", got=T.scalar(member)))
        for slot in cls.get("missing") if isinstance(cls.get("missing"), list) else []:
            lines.append(f"  {mid} : {T.not_authored(_slot(T.slot_name(slot, authored)))}")
            issues.append(_issue(f"classes.{T.slot_name(authored)}", f"{T.slot_name(slot)} is missing; marker drawn", got=authored))
    for rel in _items(data, "relations"):
        a, b = rel.get("from"), rel.get("to")
        if not (has(known, a) and has(known, b)):
            issues.append(_issue("relations", "relation endpoint is not a drawn class: relation left out",
                                 got=f"{T.scalar(a)}>{T.scalar(b)}"))
            continue
        suffix = f" : {raw(rel.get('label'), 40)}" if present(rel.get("label")) else ""
        lines.append(f"  {cid(a)} --> {cid(b)}{suffix}")
    if not known:
        lines.append(f"  class empty[{quoted(marker('diagram.classes'))}]")
        issues.append(_issue("classes", "no class to draw"))
    return _finish(lines, issues)


# --------------------------------------------------------------------------------------------- state

def build_state(data) -> tuple:
    """``stateDiagram-v2`` from ``{"initial", "states", "terminal", "transitions": [{"from", "to", "label"}]}``.

    A missing initial state or an empty state list is marked, not guessed; a transition naming a state that is not in
    ``states`` still draws (the state is added) so the picture shows what the data says.
    """
    issues: list = []
    sid = IdMap("s")
    lines = ["stateDiagram-v2"]
    states = _texts(data, "states")
    transitions = _items(data, "transitions")
    for t in transitions:
        for end in (t.get("from"), t.get("to")):
            if present(end) and end not in states:
                states.append(end)
    initial = _data(data).get("initial")
    if not present(initial):
        issues.append(_issue("initial", "the machine has no initial state", severity="ERROR"))
        initial = None
    elif initial not in states:
        states.insert(0, initial)
    if not states:
        issues.append(_issue("states", "the machine has no states", severity="ERROR"))
        lines.append(f"  state {quoted(marker('machine.states'))} as s0")
        return _finish(lines, issues)
    for state in states:
        lines.append(f"  state {quoted(state)} as {sid(state)}")
    if initial is None:
        lines.append(f"  state {quoted(marker('machine.initial'))} as s0")
        lines.append("  [*] --> s0")
    else:
        lines.append(f"  [*] --> {sid(initial)}")
    for t in transitions:
        a, b = t.get("from"), t.get("to")
        if not (present(a) and present(b)):
            issues.append(_issue("transitions", "a transition needs from and to: left out", got=T.scalar(t.get("id"))))
            continue
        suffix = f" : {raw(t.get('label'), 60)}" if present(t.get("label")) else ""
        lines.append(f"  {sid(a)} --> {sid(b)}{suffix}")
    for state in _texts(data, "terminal"):
        if state in sid:
            lines.append(f"  {sid(state)} --> [*]")
    return _finish(lines, issues)


# --------------------------------------------------------------------------------------------- ER

_CARD_LEFT = {"1": "||", "0..1": "|o", "N": "}o", "*": "}o", "0..N": "}o", "0..*": "}o", "1..N": "}|", "1..*": "}|"}
_CARD_RIGHT = {"1": "||", "0..1": "o|", "N": "o{", "*": "o{", "0..N": "o{", "0..*": "o{", "1..N": "|{", "1..*": "|{"}
UNSPECIFIED_LEFT = "}o"
UNSPECIFIED_RIGHT = "o{"
UNSPECIFIED_TYPE = "unspecified"


def cardinality_symbols(cardinality) -> tuple:
    """``(left, right, specified)`` Mermaid ER symbols for ``"N:1"`` style text. Unspecified means zero-or-more on both
    sides (the weakest claim) and is flagged so the label can say so."""
    if isinstance(cardinality, str) and cardinality.count(":") == 1:
        a, b = cardinality.split(":")
        if a in _CARD_LEFT and b in _CARD_RIGHT:
            return _CARD_LEFT[a], _CARD_RIGHT[b], True
    return UNSPECIFIED_LEFT, UNSPECIFIED_RIGHT, False


def _attr_name(name: str, ordinal: int) -> str:
    token = _ATTR_BAD.sub("_", name)
    if not token or not token[0].isalpha():
        token = "c" + token
    return token or f"c{ordinal}"


def _attr_type(value) -> str:
    if isinstance(value, str) and value and not _TOKEN_BAD.search(value) and value[0].isalpha():
        return value[:24]
    return UNSPECIFIED_TYPE


def build_er(data) -> tuple:
    """``erDiagram`` from ``{"entities": [{"id", "label", "columns": [{"name", "type", "pk", "fk", "unique"}]}],
    "relations": [{"id", "from", "to", "label", "cardinality"}]}``.

    A column given only by name has the neutral type ``unspecified`` (no type is invented). An entity without any column
    is drawn with one marker attribute and reported.
    """
    issues: list = []
    tid = IdMap("t")
    lines = ["erDiagram"]
    known: set = set()
    for entity in _items(data, "entities"):
        authored = entity.get("id")
        if not present(authored) or authored in known:
            issues.append(_issue("entities", "an entity needs a unique id", got=T.scalar(authored)))
            continue
        known.add(authored)
        if present(entity.get("label")):
            shown = entity["label"]
        else:
            issues.append(_issue(f"entities.{T.slot_name(authored)}", "table has no name; drawn with its id", got=authored))
            shown = with_marker(T.slot_name("node.name", authored), authored)
        lines.append(f"  {tid(authored)}[{quoted(shown)}] {{")
        columns = _items(entity, "columns")
        used: set = set()
        if not columns:
            issues.append(_issue(f"entities.{T.slot_name(authored)}.columns", "the table has no columns; marker drawn", got=authored))
            lines.append(f'    {UNSPECIFIED_TYPE} columns "{label(marker(T.slot_name("props.columns", authored)))}"')
        for n, col in enumerate(columns, start=1):
            name = col.get("name")
            if not present(name):
                issues.append(_issue(f"entities.{T.slot_name(authored)}.columns", "a column has no name: left out",
                                     got=T.scalar(col.get("name"))))
                continue
            token = _attr_name(name, n)
            while token in used:
                token += "_"
            used.add(token)
            keys = [k for k, flag in (("PK", col.get("pk")), ("FK", col.get("fk")), ("UK", col.get("unique"))) if flag]
            comment = f' "{label(name, 40)}"' if token != name else ""
            lines.append(f"    {_attr_type(col.get('type'))} {token}{' ' + ', '.join(keys) if keys else ''}{comment}")
        lines.append("  }")
    for rel in _items(data, "relations"):
        a, b = rel.get("from"), rel.get("to")
        if not (has(known, a) and has(known, b)):
            issues.append(_issue(f"relations.{T.slot_name(rel.get('id'))}",
                                 "relation endpoint is not a drawn table: relation left out", got=T.scalar(rel.get("id"))))
            continue
        left, right, specified = cardinality_symbols(rel.get("cardinality"))
        text = T.clip(rel.get("label"), MAX_LABEL) if present(rel.get("label")) else ""
        if not specified:
            text = (text + " / " if text else "") + UNSPECIFIED_TYPE
        lines.append(f"  {tid(a)} {left}--{right} {tid(b)} : {quoted(text)}")
    if not known:
        lines.append(f"  empty[{quoted(marker('diagram.entities'))}] {{")
        lines.append(f"    {UNSPECIFIED_TYPE} columns")
        lines.append("  }")
        issues.append(_issue("entities", "no table to draw"))
    return _finish(lines, issues)


# --------------------------------------------------------------------------------------------- registry

BuilderFn = Callable[[Mapping], tuple]
BUILDERS: dict = {"component": build_component, "class": build_class, "state": build_state, "er": build_er}


def register_builder(kind: str, function: BuilderFn) -> None:
    """Add a diagram kind (the sequence builder of the oracle lane). An existing kind cannot be replaced silently."""
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", kind):
        raise ValueError("diagram kind must be a short lower-case token")
    if kind in BUILDERS and BUILDERS[kind] is not function:
        raise ValueError(f"diagram kind {kind!r} is already registered")
    BUILDERS[kind] = function


def build(kind, data) -> tuple:
    """``(text, issues)`` for any registered kind; an unknown kind or an internal error is reported, not raised."""
    function = BUILDERS.get(kind) if isinstance(kind, str) else None
    if function is None:
        return "%% unknown diagram kind\n", [T.issue(RULE_UNKNOWN_KIND, "ERROR", "kind",
                                                     "no builder is registered for this diagram kind", got=T.scalar(kind))]
    try:
        text, issues = function(data)
    except Exception as exc:  # last resort; the tests assert this branch is never taken for the deletion matrix
        return "%% diagram builder failed\n", [T.issue(RULE_INTERNAL, "ERROR", "builder", "internal builder error",
                                                       got=type(exc).__name__)]
    return text, issues


def _finish(lines: list, issues: list) -> tuple:
    text = "\n".join(lines) + "\n"
    if len(text) > MAX_TEXT:
        issues.append(T.issue(RULE_TOO_LARGE, "WARN", "text",
                              f"diagram text is {len(text)} characters, above the {MAX_TEXT} the renderer accepts: split the diagram"))
    return text, issues
