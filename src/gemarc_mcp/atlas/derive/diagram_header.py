"""The self-describing header of a diagram file (design A3 2.12, D-20, AM-11, RS-127).

A ``diagrams/<unit id>.mmd`` file is the Mermaid text of one diagram unit with a block of ``%%`` comment lines in front of it: what the diagram is,
what it covers and leaves out, how to read its shapes and arrows, and a table of the short names the drawing uses (``n1``, ``p2`` ...) for the
model ids. The body after the header is the text of ``unit["mermaid"]`` byte for byte (``body_of`` gives it back), so the viewer data and the
unit text never change; the header is added only when the file is written. ``%%`` is the Mermaid comment syntax; it has not been run through a
real renderer (an unverified assumption of the design), and ``apply`` writes a space after ``%%`` so no line can read as a ``%%{...}%%`` directive.

::

    %% atlas-diagram 1
    %% id: component-jl3.buffer · type: component · lang: zh-CN · m=0262885f
    %% title: ...
    %% covers: nodes 37 · connections 52 (full list: ...)
    %% legend: ...
    %% excludes: ...
    %% note: ...                     (only when the body says it was cut)
    %% side: B (after the intervention)   (only on a paired sequence diagram, with ``intervention:`` and ``case:``)
    %% alias n1 = zh6.pallet-lock (Tile valve, Module)
    flowchart LR
    ...

``apply(unit, ctx)`` reads only what a full unit and a page-compact unit (``build.compact_unit``) have in common: ``id``, ``type``, ``title``,
``covers`` and ``mermaid``; names and kinds of the model elements come from ``HeaderContext``. It never raises on an odd unit. The header carries no
page of the reading surface (a standalone ``docbuild build`` has none), and no diagram-type keyword (a withheld case must leave no
``sequenceDiagram`` in a file, ``test_build_project``). Standard library only. The fixed sentences are in ``i18n/<lang>.json`` under ``read.dg``; a
language without its own text (ja-JP) uses en-US, like the rest of the reading surface (D-24).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from . import i18n as T
from . import mermaid as M
from . import policy as P

VERSION = 1
VERSION_LINE = f"%% atlas-diagram {VERSION}"
REQUIRED = ("id", "type", "lang", "m", "title", "covers", "legend", "excludes")
KEYWORDS = ("sequenceDiagram", "flowchart", "stateDiagram", "classDiagram", "erDiagram")
NAME_CLIP = 40
LINE_MAX = 480

#: the short-name prefixes each diagram type uses (``mermaid.IdMap`` prefixes in ``mermaid.py`` and ``diagrams.py``)
TOKEN_PREFIX = {"component": "ng", "class": "c", "state": "s", "er": "t", "sequence": "p", "staterun": "n"}
_QUOTED = re.compile(r'"[^"\n]*"')
_DECL = {
    "component": re.compile(r'^\s*(?P<tok>n\d+)(?:\[\[|\[/|\[|\(|\{\{|\{)"(?P<label>[^"\n]*)"'),
    "group": re.compile(r'^\s*subgraph (?P<tok>g\d+)\["(?P<label>[^"\n]*)"\]'),
    "class": re.compile(r'^\s*class (?P<tok>c\d+)\["(?P<label>[^"\n]*)"\]'),
    "state": re.compile(r'^\s*state "(?P<label>[^"\n]*)" as (?P<tok>s\d+)\s*$'),
    "er": re.compile(r'^\s*(?P<tok>t\d+)\["(?P<label>[^"\n]*)"\] \{'),
    "sequence": re.compile(r'^\s*participant (?P<tok>p\d+) as "(?P<label>[^"\n]*)"'),
    "staterun": re.compile(r'^\s*state "(?P<label>[^"\n]*)" as (?P<tok>n\d+)_'),
}
_TRUNC_SEQUENCE = re.compile(r"Note over \w+: \d+ more entries not drawn")


def text_lang(lang) -> str:
    """The language of the fixed sentences: zh-CN has its own, every other language uses en-US (D-24)."""
    return "zh-CN" if lang == "zh-CN" else "en-US"


def _t(lang, key: str) -> str:
    value = T.lookup(text_lang(lang), "read.dg." + key)
    return value if isinstance(value, str) else ""


def _fill(template: str, **values) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def one_line(value, limit: int = 300) -> str:
    """Header text: one line, no ``%%`` pair, clipped."""
    text = " ".join(T.clean(value).replace("%%", "% %").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass(frozen=True)
class HeaderContext:
    """What a header needs besides the unit: the language, the model stamp (``m=``, empty = none), the names and kinds of the nodes, and the
    ids of the interventions (a paired sequence diagram is recognised by its unit id)."""

    lang: str
    stamp: str = ""
    nodes: Mapping = field(default_factory=dict)            # node id -> (name, kind)
    interventions: tuple = ()

    @classmethod
    def from_model(cls, model, lang: str, stamp: str = "") -> "HeaderContext":
        ix = P.Index(model)
        nodes = {P.sval(n.get("id")): (P.sval(n.get("name")), P.sval(n.get("kind"))) for n in ix.nodes if P.sval(n.get("id"))}
        ivs = tuple(P.sval(i.get("interventionId")) for i in ix.interventions if P.sval(i.get("interventionId")))
        return cls(T.normalize_lang(lang), stamp, nodes, ivs)


# ---------------------------------------------------------------------------------------------------- reading a body

def _tokens(kind: str, body: str) -> list:
    """The short names a body declares, in order of first declaration: ``[(token, label)]`` (a label is the text of the first declaration)."""
    out: dict = {}
    names = ("group", "component") if kind == "component" else (kind,)
    for line in body.split("\n"):
        for name in names:
            m = _DECL[name].match(line) if name in _DECL else None
            if m and m.group("tok") not in out:
                out[m.group("tok")] = m.group("label")
    return sorted(out.items(), key=lambda kv: (kv[0][0], int(re.sub(r"\D", "", kv[0]) or 0)))


def used_tokens(kind: str, body: str) -> set:
    """Every short name of the diagram type that occurs outside quoted text in the body (what ``%% alias`` lines must cover)."""
    prefixes = TOKEN_PREFIX.get(kind)
    if not prefixes:
        return set()
    stripped = _QUOTED.sub('""', body)
    found = set(re.findall(r"(?<![A-Za-z0-9_])([" + prefixes + r"]\d+)(?![A-Za-z0-9])", stripped))
    if kind == "state":
        found.discard("s0")           # the marker state of a machine without an initial state: drawn by the builder, not a name of an element
    return found


def _label_matches(label: str, name: str) -> bool:
    return bool(name) and any(label == M.label(name, limit) for limit in (80, 60, 40))


def alias_lines(unit: Mapping, ctx: HeaderContext) -> list:
    kind = P.sval(unit.get("type"))
    body = P.sval(unit.get("mermaid"))
    covers = unit.get("covers") if isinstance(unit.get("covers"), Mapping) else {}
    ids = P.str_list(covers.get("nodeIds"))
    declared = dict(_tokens(kind, body))
    lang = ctx.lang
    out: list = []
    for token in sorted(used_tokens(kind, body) | set(declared), key=lambda t: (t[0], int(re.sub(r"\D", "", t) or 0))):
        label = declared.get(token, "")
        if kind == "staterun":
            label = label.split(": ", 1)[0] if ": " in label else label
        index = int(re.sub(r"\D", "", token) or 0)
        ident = ids[index - 1] if token[0] in "npct" and kind != "state" and 1 <= index <= len(ids) and (kind != "class" or index == 1) else ""
        name, node_kind = ctx.nodes.get(ident, ("", "")) if ident else ("", "")
        if ident and name and (not label or kind in ("sequence", "staterun") or _label_matches(label, name)):
            out.append(_fill(_t(lang, "alias"), token=token, id=one_line(ident, 120), name=one_line(name, NAME_CLIP), kind=one_line(node_kind, 30) or "-"))
        else:
            out.append(_fill(_t(lang, "alias_label_only"), token=token, label=one_line(label, 80) or "-"))
    return ["%% " + line for line in out]


# ---------------------------------------------------------------------------------------------------- apply

def pair_of(unit: Mapping, ctx: HeaderContext) -> tuple:
    """``(intervention id, case id)`` of a paired sequence diagram, else ``("", "")``: the unit id is ``sequence-<dg_id(iv-case)>``, the case
    comes from ``covers.caseIds`` and the intervention is the model one that makes the id (a file name is never taken apart)."""
    uid = P.sval(unit.get("id"))
    covers = unit.get("covers") if isinstance(unit.get("covers"), Mapping) else {}
    cases = P.str_list(covers.get("caseIds"))
    if P.sval(unit.get("type")) != "sequence" or len(cases) != 1 or not uid.startswith("sequence-"):
        return "", ""
    tail = uid[len("sequence-"):]
    if tail == T.dg_id(cases[0]):
        return "", ""
    for iid in ctx.interventions:
        if T.dg_id(f"{iid}-{cases[0]}") == tail:
            return iid, cases[0]
    return "", ""


def header_lines(unit: Mapping, ctx: HeaderContext) -> list:
    """The ``%%`` lines for a unit (without the body)."""
    lang = ctx.lang
    kind = P.sval(unit.get("type"))
    uid = P.sval(unit.get("id"))
    covers = unit.get("covers") if isinstance(unit.get("covers"), Mapping) else {}
    body = P.sval(unit.get("mermaid"))
    parts = [(_t(lang, key), len(P.str_list(covers.get(field)))) for key, field in (("covers_nodes", "nodeIds"), ("covers_edges", "edgeIds"), ("covers_cases", "caseIds"))]
    shown = " · ".join(_fill(template, n=n) for template, n in parts if n) or _t(lang, "covers_none")
    legend = T.lookup(text_lang(lang), "read.dg.legend." + kind)
    exclude_key = "overview" if uid == "component-overview" else kind
    excludes = T.lookup(text_lang(lang), "read.dg.excludes." + exclude_key)
    stamp = f" · m={ctx.stamp}" if ctx.stamp else ""
    lines = [VERSION_LINE,
             f"%% id: {one_line(uid, 120)} · type: {one_line(kind, 30)} · lang: {lang}{stamp}",
             f"%% title: {one_line(unit.get('title'), 120)}",
             "%% " + _fill(_t(lang, "covers"), parts=shown + " " + _t(lang, "covers_more")),
             f"%% legend: {one_line(legend if isinstance(legend, str) else '-', LINE_MAX - 12)}",
             f"%% excludes: {one_line(excludes if isinstance(excludes, str) else '-', LINE_MAX - 14)}"]
    if kind == "sequence" and _TRUNC_SEQUENCE.search(body):
        lines.append("%% note: " + _t(lang, "note_sequence"))
    if kind == "staterun":
        lines.append("%% note: " + _t(lang, "note_staterun"))
    iid, cid = pair_of(unit, ctx)
    if iid:
        lines += [f"%% side: {_t(lang, 'side')}", f"%% intervention: {one_line(iid, 120)}", f"%% case: {one_line(cid, 120)}"]
    return lines + alias_lines(unit, ctx)


def apply(unit: Mapping, ctx: HeaderContext) -> str:
    """The text of the diagram file: the header and then ``unit["mermaid"]`` unchanged (a unit without text gives the header alone)."""
    body = P.sval(unit.get("mermaid")) if isinstance(unit, Mapping) else ""
    lines = header_lines(unit if isinstance(unit, Mapping) else {}, ctx)
    return "\n".join(lines) + "\n" + body


# ---------------------------------------------------------------------------------------------------- reading a file back (RS-127)

def split(text: str) -> tuple:
    """``(header lines, body)`` of a diagram file: the leading run of ``%%`` lines and the rest (the body keeps every byte, its first line is the
    diagram type)."""
    header: list = []
    rest = text
    while rest.startswith("%%"):
        line, _sep, remainder = rest.partition("\n")
        header.append(line)
        rest = remainder
    return header, rest


def body_of(text: str) -> str:
    return split(text)[1]


_IDENT = re.compile(r"^%% id: (?P<id>.+?) · type: (?P<type>.+?) · lang: (?P<lang>[A-Za-z-]+)(?: · m=(?P<m>[0-9a-f]{8}))?$")


def parse(text: str) -> dict:
    """The fields of a header: ``{"version", "id", "type", "lang", "m", "title", "covers", "legend", "excludes", "notes", "side", "intervention", "case",
    "aliases": [(token, rest)]}`` (a missing field is absent)."""
    header, body = split(text)
    out: dict = {"notes": [], "aliases": [], "body": body, "lines": header}
    if header and header[0] == VERSION_LINE:
        out["version"] = VERSION
    for line in header[1:]:
        m = _IDENT.match(line)
        if m:
            out.update({k: v for k, v in m.groupdict().items() if v is not None})
            continue
        key, _sep, value = line[3:].partition(": ")
        if line.startswith("%% alias "):
            token, _eq, rest = line[len("%% alias "):].partition(" = ")
            out["aliases"].append((token, rest))
        elif key == "note":
            out["notes"].append(value)
        elif key in ("title", "covers", "legend", "excludes", "side", "intervention", "case"):
            out[key] = value
    return out


def problems(text: str, unit: Mapping, ctx: HeaderContext) -> list:
    """What is wrong with the file text of a diagram unit (empty list = it satisfies RS-127)."""
    out: list = []
    got = parse(text)
    if got.get("version") != VERSION:
        return ["the file does not start with " + VERSION_LINE]
    for key in REQUIRED:
        if key not in got:
            out.append(f"the header has no {key}")
    uid, kind = P.sval(unit.get("id")), P.sval(unit.get("type"))
    if got.get("id") not in (None, uid):
        out.append(f"the header id {got.get('id')!r} is not the unit id {uid!r}")
    if got.get("type") not in (None, kind):
        out.append(f"the header type {got.get('type')!r} is not the unit type {kind!r}")
    if ctx.stamp and got.get("m") != ctx.stamp:
        out.append(f"the header stamp {got.get('m')!r} is not the model stamp {ctx.stamp!r}")
    body = P.sval(unit.get("mermaid"))
    if got["body"] != body:
        out.append("the text after the header is not the text of the diagram unit")
    for line in got["lines"]:
        if any(word in line for word in KEYWORDS):
            out.append("a header line names a diagram type")
            break
        if len(line) > LINE_MAX:
            out.append("a header line is longer than 480 characters")
            break
    covered = {token for token, _rest in got["aliases"]}
    missing = sorted(used_tokens(kind, body) - covered)
    if missing:
        out.append(f"the body uses {missing[:5]} and the header has no alias line for them")
    if kind == "sequence" and _TRUNC_SEQUENCE.search(body) and not got["notes"]:
        out.append("the sequence diagram is cut and the header has no note")
    if kind == "staterun" and not got["notes"]:
        out.append("a staterun diagram draws at most 6 nodes and the header has no note")
    iid, cid = pair_of(unit, ctx)
    paired = any(k in got for k in ("side", "intervention", "case"))
    if iid and not (got.get("side") and got.get("intervention") == iid and got.get("case") == cid):
        out.append("a paired sequence diagram needs the side, intervention and case lines of its pair")
    if paired and not (got.get("side") and got.get("intervention") and got.get("case")):
        out.append("side, intervention and case belong together")
    legend = T.lookup(text_lang(ctx.lang), "read.dg.legend." + kind)
    if isinstance(legend, str) and got.get("legend") != one_line(legend, LINE_MAX - 12):
        out.append("the legend is not the fixed text of the diagram type")
    return out
