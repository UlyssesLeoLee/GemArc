"""Per-element Markdown pieces of the REQ / BD / DD document chain (structure only, no engine output).

``fragments.py`` calls the renderers of this module once per element and level; ``assemble.py`` joins the pieces into a
document. Nothing here knows about a selection: a piece is a table row, a ``###`` section or a bullet list that is true
on its own.

Rules every renderer follows (skeleton-spec.md, risk 14):

* the AI wrote facts, the code derives text: a value that the model lacks (or has with the wrong type) becomes a
  ``[NOT-AUTHORED:<slot>]`` marker, a structurally inapplicable slot ``[N/A]``, never a guess and never a blank;
* every model-sourced string goes through ``T.md_text`` / ``T.md_cell`` / ``T.code`` (no ``|``, newline, link or
  marker forgery); structured values are printed with ``json.dumps`` (``T.scalar``), never ``str``;
* no engine output: case sections show the authored events and expectations, never a trace or a verdict;
* a renderer never raises: ``Ctx.safe`` turns an exception into one ``DC-D-099`` issue plus a marker piece.

The 12 chapters are shared by the three levels (``REQ`` within ``BD`` within ``DD``); which pieces exist at which level
is decided in ``fragments.py``, what a piece says is decided here.
"""
from __future__ import annotations

import copy
from typing import Iterable, Mapping

from .. import model as am
from . import diagrams as D
from . import i18n as T
from . import policy as P
from .policy import Index, mapping, str_list, sval

LEVELS = ("REQ", "BD", "DD")
RANK = {"REQ": 1, "BD": 2, "DD": 3}
DOC_PREFIX = {"REQ": "RD", "BD": "BD", "DD": "DD"}
RULE_INTERNAL = "DC-D-099"
RULE_NODE_ID = "DC-D-001"
RULE_POLICY_SCOPE = "DC-D-002"
RULE_ELEMENT_ID = "DC-D-003"

# Placeholders of the selection-dependent text (document numbers and the target list). They contain control characters,
# which ``T.clean`` strips from every model string, so no model text can contain one.
PH_DOCNO = "\x01DOCNO\x01"
PH_UPPER = "\x01UPPER\x01"
PH_LOWER = "\x01LOWER\x01"
PH_TARGET = "\x01TARGET\x01"

MAX_IDS = 8       # ids listed in one table cell before "+N"
MAX_PAIRS = 6     # "node=state" pairs in one table cell before "+N"
OPTION_CLIP = 60  # characters of one event option value

# The one-sentence purpose of each level (chapter 2). Kept here, not in the shared skeleton tables, because the
# skeleton tables belong to the skeleton lane; same three languages.
_PURPOSE = {
    "en-US": {
        "REQ": "This document states what the selected parts of the system must do for the people who use them, derived from the authored model.",
        "BD": "This document shows how the selected parts are arranged and connected so that the requirements are met.",
        "DD": "This document gives the detail needed to build or review each selected part: connections, steps, data and transitions.",
    },
    "zh-CN": {
        "REQ": "本文写明所选部分要为使用者做到什么，内容由已作者的模型导出。",
        "BD": "本文说明所选部分如何组织与连接，以满足需求。",
        "DD": "本文给出建造或评审所选部分所需的细节：连接、步骤、数据与转移。",
    },
    "ja-JP": {
        "REQ": "ここでは、選択した部分が利用者のために満たすべきことを、作成済みモデルから導出して示す。",
        "BD": "ここでは、要件を満たすために選択した部分をどう構成し接続するかを示す。",
        "DD": "ここでは、選択した部分を作り、また確認するために必要な詳細(接続、手順、データ、遷移)を示す。",
    },
}


# --------------------------------------------------------------------------------------------- context

class Ctx:
    """Everything the renderers share: the index, the language, the diagram units and a few reverse lookups."""

    def __init__(self, model, lang: str, units=None, profile=None):
        self.ix = Index(model, profile)
        self.lang = T.normalize_lang(lang)
        self.issues: list = []
        if units is None:
            built = D.build_units(model, self.lang, profile=profile)
            units = built["units"]
        self.units = list(units)
        ix = self.ix
        self.ports = P.rows(ix.model, "ports")
        self.ports_of: dict = {}
        for port in self.ports:
            self.ports_of.setdefault(sval(port.get("nodeId")), []).append(port)
        self.req_by_node: dict = {}
        for rid, req in ix.requirement.items():
            for nid in str_list(req.get("nodeIds")):
                bucket = self.req_by_node.setdefault(nid, [])
                if rid not in bucket:
                    bucket.append(rid)
        self.participants = {cid: ix.case_participants(case) for cid, case in ix.case.items()}
        self.cases_by_node: dict = {}
        for cid, nodes in self.participants.items():
            for nid in nodes:
                self.cases_by_node.setdefault(nid, []).append(cid)
        self._node_rank: dict = {}
        self._edge_rank: dict = {}
        self._iface: dict | None = None

    # -- robustness
    def issue(self, rule, severity, path, hint, got=None):
        self.issues.append(T.issue(rule, severity, path, hint, got=got))

    def safe(self, label: str, function, *args):
        """``function(*args)`` or, when it raises, a marker piece plus one DC-D-099 issue."""
        try:
            return function(*args)
        except Exception as exc:  # the deletion tests assert this never fires
            self.issue(RULE_INTERNAL, "ERROR", label, "internal document builder error", got=type(exc).__name__)
            return T.not_authored(T.slot_name("render", label))

    # -- ranks (the level at which an element first appears)
    def node_rank(self, node: Mapping) -> int:
        nid = sval(node.get("id"))
        if nid not in self._node_rank:
            try:
                level = am.node_level(dict(node), self.ix.room_of(node), profile=self.ix.profile)
            except Exception:
                level = "DD"
            self._node_rank[nid] = RANK.get(level, 3)
        return self._node_rank[nid]

    def rank_of_node_id(self, nid: str) -> int:
        node = self.ix.node.get(nid)
        return self.node_rank(node) if node is not None else 3

    def edge_rank(self, edge: Mapping) -> int:
        eid = sval(edge.get("id"))
        if eid not in self._edge_rank:
            item = self.ix.profile.item("relations", sval(edge.get("relation")))
            rel = RANK.get(item["level"], 3) if item else 3
            self._edge_rank[eid] = max(rel, self.rank_of_node_id(sval(edge.get("sourceId"))),
                                       self.rank_of_node_id(sval(edge.get("targetId"))))
        return self._edge_rank[eid]

    # -- interface edges (chapter 7): a trust-boundary relation (profile flag) whose two ends lie in different colour domains
    def domain_of(self, nid: str) -> str:
        node = self.ix.node.get(nid)
        if node is None:
            return ""
        building, _floor, room = self.ix.placement(node)
        try:
            return am.node_domain(dict(node), room, building, profile=self.ix.profile)
        except Exception:
            return ""

    def interface_edges(self) -> list:
        if self._iface is None:
            boundary = self.ix.profile.trust_boundary_relations
            out = []
            for edge in self.ix.edges:
                eid = sval(edge.get("id"))
                a, b = sval(edge.get("sourceId")), sval(edge.get("targetId"))
                if not eid or a not in self.ix.node or b not in self.ix.node:
                    continue
                if sval(edge.get("relation")) in boundary and self.domain_of(a) != self.domain_of(b):
                    out.append(edge)
            self._iface = out
        return self._iface


# --------------------------------------------------------------------------------------------- small helpers

def row(cells: Iterable) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def head(c: Ctx, key: str) -> str:
    """The two header lines of the table set ``skeleton.th.<key>``."""
    return "\n".join(T.md_table(T.tr_list(c.lang, "skeleton.th." + key), []))


def slot(*parts) -> str:
    return T.slot_name(*parts)


def ids_cell(ids, limit: int = MAX_IDS) -> str:
    ids = [i for i in (ids or []) if isinstance(i, str) and i]
    if not ids:
        return T.na()
    shown = ", ".join(T.code(i) for i in ids[:limit])
    return shown + (f" +{len(ids) - limit}" if len(ids) > limit else "")


def code_or_na(value) -> str:
    return T.code(value) if isinstance(value, str) and value.strip() else T.na()


def label_cell(c: Ctx, group: str, value, slot_text: str) -> str:
    if not isinstance(value, str) or not value:
        return T.not_authored(slot_text)
    return T.md_text(c.ix.profile.label(group, value, c.lang))


def pairs_text(value, limit: int = MAX_PAIRS) -> str | None:
    """``id=State; id=State`` of a ``{nodeId: state}`` mapping, ``None`` when it is empty or not a mapping."""
    if not isinstance(value, Mapping) or not value:
        return None
    items = [(k, v) for k, v in value.items()]
    parts = []
    for key, state in items[:limit]:
        if isinstance(key, str) and isinstance(state, str):
            parts.append(f"{T.md_text(T.clip(key, 60))}={T.md_text(T.clip(state, 40))}")
        else:
            parts.append(T.not_authored("pair"))
    text = "; ".join(parts)
    return text + (f" +{len(items) - limit}" if len(items) > limit else "")


def kv_text(value, limit: int = 120) -> str:
    """``k=v k=v`` of a mapping (values through ``T.scalar``), flattened and clipped; ``""`` for anything else."""
    if not isinstance(value, Mapping):
        return ""
    return T.md_text(T.clip(" ".join(f"{k}={T.scalar(v)}" for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))), limit))


def bullet(label: str, value: str) -> str:
    return f"- {T.md_text(label)}: {value}"


def field(c: Ctx, key: str) -> str:
    return T.tr(c.lang, "skeleton.field." + key)


def docs_name(c: Ctx, level: str) -> str:
    return T.tr(c.lang, f"skeleton.docs.{level}")


def level_upper(level: str):
    return {"BD": "REQ", "DD": "BD"}.get(level)


def level_lower(level: str):
    return {"REQ": "BD", "BD": "DD"}.get(level)


# --------------------------------------------------------------------------------------------- frame (chapter 1, 2, 3 map, 12)

def frame_head(c: Ctx, level: str) -> str:
    return f"# {T.md_text(docs_name(c, level))} {PH_DOCNO}\n\n{T.md_text(T.tr(c.lang, 'skeleton.label'))}"


def chapter_note(c: Ctx, level: str, number: int) -> str:
    upper = level_upper(level)
    if upper is None:
        return ""
    return "> " + T.fill(T.tr(c.lang, "skeleton.upper_note"), doc=docs_name(c, upper), n=str(number))


def c1_block(c: Ctx, level: str) -> str:
    """Chapter 1: document facts (the number placeholders are filled by ``assemble``) and the fixed statement."""
    info = lambda key: T.tr(c.lang, "skeleton.doc_info." + key)  # noqa: E731
    upper, lower = level_upper(level), level_lower(level)
    level_name = c.ix.profile.label("levels", level, c.lang)
    title = T.md_text(c.ix.title)
    target = (title + " / " if title else "") + PH_TARGET
    lines = [
        bullet(info("kind"), f"{T.md_text(docs_name(c, level))} `{PH_DOCNO}`"),
        bullet(info("level"), T.md_text(level_name)),
        bullet(info("upper"), f"{T.md_text(docs_name(c, upper))} `{PH_UPPER}`" if upper else T.na()),
        bullet(info("lower"), f"{T.md_text(docs_name(c, lower))} `{PH_LOWER}`" if lower else T.na()),
        bullet(info("model"), f"`{c.ix.sha8()}`"),
        bullet(info("status"), T.md_text(info("draft"))),
        bullet(info("target"), target),
    ]
    notes = ["- " + T.md_text(n) for n in T.tr_list(c.lang, "skeleton.facts_note")]
    return "\n".join(lines) + "\n\n" + "\n".join(notes)


def c2_purpose(c: Ctx, level: str) -> str:
    table = _PURPOSE.get(c.lang) or _PURPOSE["en-US"]
    return f"{T.md_text(T.tr(c.lang, 'skeleton.doc_info.purpose'))}: {T.md_text(table[level])}"


def chapter_map_rows(c: Ctx) -> list:
    """DD chapter 3: where each chapter of the basic design continues in this document (fixed, always listed)."""
    return [row([str(n), T.md_text(T.chapter_title(c.lang, "DD", n)), f"BD {n}", str(n)]) for n in range(1, T.CHAPTER_COUNT + 1)]


def sign_block(c: Ctx) -> str:
    roles = T.tr_list(c.lang, "skeleton.signoff.roles")
    lines = T.md_table(T.tr_list(c.lang, "skeleton.th.signoff"), [[T.md_text(r), "", "", ""] for r in roles])
    return "\n".join(lines) + "\n\n" + T.md_text(T.tr(c.lang, "skeleton.signoff.judgement"))


# --------------------------------------------------------------------------------------------- evidence, requirements

def evidence_row(c: Ctx, eid: str) -> str:
    rec = c.ix.evidence.get(eid)
    if rec is None:
        # an id used but never defined: keep the row, say what is missing (spec: never drop a row silently)
        return row([T.code(eid), T.not_authored(slot("evidence.file", eid)), T.not_authored(slot("evidence.topic", eid)),
                    T.not_authored(slot("evidence.verification", eid))])
    path = sval(rec.get("filePath"))
    where = T.code(path) if path else T.not_authored(slot("evidence.file", eid))
    span = rec.get("lineRange")
    if isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in span):
        where += f":{span[0]}-{span[1]}"
    anchor = sval(rec.get("anchor"))
    if anchor:
        where += " " + T.md_text(T.clip(anchor, 50))
    return row([T.code(eid), where, T.md_cell(rec.get("topic"), slot("evidence.topic", eid)),
                T.md_cell(rec.get("verification"), slot("evidence.verification", eid))])


def requirement_row(c: Ctx, req: Mapping) -> str:
    rid = sval(req.get("id"))
    return row([T.code(rid), T.md_cell(req.get("title"), slot("requirement.title", rid)),
                T.md_cell(req.get("source"), slot("requirement.source", rid)), ids_cell(str_list(req.get("tstIds")))])


def coverage_row(c: Ctx, req: Mapping, level: str) -> str:
    rid = sval(req.get("id"))
    upper = level_upper(level) or "REQ"
    refs = "; ".join(T.md_text(T.clip(x, 40)) for x in str_list(req.get("designIds"))) or T.na()
    return row([T.code(rid), T.md_cell(req.get("title"), slot("requirement.title", rid)), f"{upper} 3", refs,
                ids_cell(str_list(req.get("nodeIds"))), ids_cell(str_list(req.get("tstIds")))])


def crossmap_node_row(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    return row([T.code(nid), T.md_cell(node.get("name"), slot("node.name", nid)), "BD 4", "4"])


def crossmap_case_row(c: Ctx, case: Mapping) -> str:
    cid = sval(case.get("caseId"))
    return row([T.code(cid), T.md_cell(case.get("title"), slot("case.title", cid)), "BD 5", "5"])


# --------------------------------------------------------------------------------------------- nodes

def _placement(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    building, floor, room = c.ix.placement(node)
    parts = []
    for rec, what in ((building, "building"), (floor, "floor"), (room, "room")):
        parts.append(T.md_cell(rec.get("name"), slot("node." + what, nid)) if rec else T.not_authored(slot("node." + what, nid)))
    return " / ".join(parts)


def node_row_req(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    return row([T.code(nid), T.md_cell(node.get("name"), slot("node.name", nid)),
                label_cell(c, "kinds", node.get("kind"), slot("node.kind", nid)), T.md_cell(node.get("sub"), slot("node.sub", nid)),
                ids_cell(c.req_by_node.get(nid))])


def node_row_bd(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    return row([T.code(nid), T.md_cell(node.get("name"), slot("node.name", nid)),
                label_cell(c, "kinds", node.get("kind"), slot("node.kind", nid)), T.md_cell(node.get("sub"), slot("node.sub", nid)),
                _placement(c, node), T.md_cell(node.get("verification"), slot("node.verification", nid)),
                ids_cell(c.req_by_node.get(nid))])


def node_section_dd(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    props = mapping(node.get("props"))
    meta = " · ".join([T.code(nid), label_cell(c, "kinds", node.get("kind"), slot("node.kind", nid)),
                       T.md_cell(node.get("verification"), slot("node.verification", nid))])
    lines = [f"### {T.md_cell(node.get('name'), slot('node.name', nid))}", meta,
             bullet(field(c, "placement"), _placement(c, node)),
             bullet(field(c, "summary"), T.md_cell(node.get("sub"), slot("node.sub", nid))),
             bullet(field(c, "reqs"), ids_cell(c.req_by_node.get(nid))),
             bullet(field(c, "evidence"), ids_cell(str_list(node.get("evidenceIds"))))]
    if isinstance(props.get("trust"), str) and props["trust"]:
        lines.append(bullet(field(c, "trust"), T.md_text(props["trust"])))
    for key, name in (("owner", "owner"), ("store", "store")):
        if props.get(key) is not None:
            lines.append(bullet(field(c, name), code_or_na(props.get(key))))
    methods = [m for m in props.get("methods", []) if isinstance(m, str) and m] if isinstance(props.get("methods"), list) else []
    if methods:
        lines.append(bullet(field(c, "methods"), "; ".join(T.md_text(T.clip(m, 100)) for m in methods)))
    enums = props.get("enums")
    if isinstance(enums, Mapping) and enums:
        parts = []
        for name, values in enums.items():
            vals = ", ".join(T.md_text(T.clip(v, 40)) for v in values if isinstance(v, str)) if isinstance(values, list) else ""
            parts.append(f"{T.md_text(T.clip(name, 40))}: {vals}")
        lines.append(bullet(field(c, "states"), "; ".join(parts)))
    if isinstance(props.get("dedup"), Mapping):
        lines.append(bullet(field(c, "dedup"), kv_text(props["dedup"])))
    if props.get("aggregator") is True:
        lines.append(bullet(field(c, "aggregator"), T.tr(c.lang, "skeleton.bool.true")))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- edges

def _relation(c: Ctx, edge: Mapping, with_cardinality: bool) -> str:
    eid = sval(edge.get("id"))
    text = label_cell(c, "relations", edge.get("relation"), slot("edge.relation", eid))
    card = sval(edge.get("cardinality"))
    if with_cardinality and card:
        text += f" ({T.md_text(card)})"
    return text


def edge_row_min(c: Ctx, edge: Mapping) -> str:
    eid = sval(edge.get("id"))
    return row([T.code(eid), code_or_na(edge.get("sourceId")), code_or_na(edge.get("targetId")), _relation(c, edge, False),
                T.md_cell(edge.get("label"), slot("edge.label", eid))])


def edge_row_bd(c: Ctx, edge: Mapping) -> str:
    eid = sval(edge.get("id"))
    return row([T.code(eid), code_or_na(edge.get("sourceId")), code_or_na(edge.get("targetId")), _relation(c, edge, True),
                T.md_cell(edge.get("label"), slot("edge.label", eid)), T.md_cell(edge.get("verification"), slot("edge.verification", eid))])


def edge_row_dd(c: Ctx, edge: Mapping) -> str:
    eid = sval(edge.get("id"))
    guard = edge.get("guardId")
    return row([T.code(eid), code_or_na(edge.get("sourceId")), code_or_na(edge.get("sourcePortId")), code_or_na(edge.get("targetId")),
                code_or_na(edge.get("targetPortId")), _relation(c, edge, False),
                T.md_text(sval(edge.get("cardinality"))) or T.na(), code_or_na(guard), ids_cell(str_list(edge.get("evidenceIds")), 4),
                T.md_cell(edge.get("verification"), slot("edge.verification", eid))])


def port_rows(c: Ctx, node_id: str, iface_ids: set, limit: int) -> str | None:
    """Port rows of one node that carry an interface edge; ``None`` when no port does."""
    rows = []
    for port in c.ports_of.get(node_id, []):
        pid = sval(port.get("id"))
        wired = [sval(e.get("id")) for e in c.interface_edges()
                 if pid and pid in (e.get("sourcePortId"), e.get("targetPortId")) and sval(e.get("id")) in iface_ids]
        if not wired:
            continue
        rows.append(row([T.code(node_id), T.md_cell(port.get("direction"), slot("port.direction", pid)),
                         T.md_cell(port.get("type"), slot("port.type", pid)), ids_cell(wired, limit)]))
    return "\n".join(rows) if rows else None


# --------------------------------------------------------------------------------------------- cases

def _case_states(c: Ctx, case: Mapping) -> str:
    cid = sval(case.get("caseId"))
    return pairs_text(case.get("expectedTerminalState")) or T.not_authored(slot("case.expect", cid))


def case_row(c: Ctx, case: Mapping) -> str:
    cid = sval(case.get("caseId"))
    return row([T.code(cid), T.md_cell(case.get("title"), slot("case.title", cid)),
                label_cell(c, "caseCategories", case.get("category"), slot("case.category", cid)),
                ids_cell(str_list(case.get("requirementIds"))), _case_states(c, case)])


_EVENT_SKIP = ("seq", "type", "from", "to", "ruleId")


def _event_cells(c: Ctx, cid: str, position: int, event: Mapping) -> list:
    where = f"{cid}#{position}"
    seq = event.get("seq")
    seq_cell = str(seq) if isinstance(seq, int) and not isinstance(seq, bool) else T.not_authored(slot("event.seq", where))
    options = T.md_text(" ".join(f"{k}={T.clip(T.scalar(v), OPTION_CLIP)}" for k, v in sorted(event.items(), key=lambda kv: str(kv[0]))
                                 if k not in _EVENT_SKIP)) or T.na()
    endpoint = lambda key: T.code(event.get(key)) if isinstance(event.get(key), str) and event.get(key) else T.not_authored(slot("event." + key, where))  # noqa: E731
    return [seq_cell, T.md_cell(event.get("type"), slot("event.type", where)), endpoint("from"), endpoint("to"),
            code_or_na(event.get("ruleId")), options]


def _policy_text(c: Ctx, case: Mapping) -> str:
    cid = sval(case.get("caseId"))
    policy = case.get("policy")
    if not isinstance(policy, Mapping):
        return T.not_authored(slot("case.policy", cid))
    parts = []
    for key, name, unit in (("timeoutMs", "timeout", " ms"), ("retryMax", "retry", ""), ("retryBackoffMs", "backoff", " ms")):
        value = policy.get(key)
        ok = isinstance(value, int) and not isinstance(value, bool)
        parts.append(f"{T.md_text(field(c, name))} {value}{unit}" if ok else T.not_authored(slot("case.policy." + key, cid)))
    return "; ".join(parts)


def case_section_dd(c: Ctx, case: Mapping) -> str:
    cid = sval(case.get("caseId"))
    events = [e for e in case.get("events", []) if isinstance(e, Mapping)] if isinstance(case.get("events"), list) else []
    meta = " · ".join([T.code(cid), label_cell(c, "caseCategories", case.get("category"), slot("case.category", cid)),
                       ids_cell(str_list(case.get("requirementIds")))])
    lines = [f"### {T.md_cell(case.get('title'), slot('case.title', cid))}", meta, bullet(field(c, "policy"), _policy_text(c, case))]
    init = pairs_text(case.get("initialMachines"))
    if init:
        lines.append(bullet(field(c, "pre"), init))
    lines.append(bullet(field(c, "expected"), _case_states(c, case)))
    out = ["\n".join(lines)]
    out.append("\n".join(T.md_table(T.tr_list(c.lang, "skeleton.th.event"), [_event_cells(c, cid, i, e) for i, e in enumerate(events, 1)])))
    outcomes = [o for o in case.get("expectedOutcomes", []) if isinstance(o, Mapping)] if isinstance(case.get("expectedOutcomes"), list) else []
    if outcomes:
        body = [[code_or_na(o.get("ruleId")), T.md_cell(o.get("outcome"), slot("outcome.outcome", cid)), code_or_na(o.get("errorCode")), T.code(cid)]
                for o in outcomes]
        out.append(T.tr(c.lang, "skeleton.field.outcomes") + ":\n\n" + "\n".join(T.md_table(T.tr_list(c.lang, "skeleton.th.exception"), body)))
    return "\n\n".join(out)


# --------------------------------------------------------------------------------------------- data

def _columns(c: Ctx, node: Mapping) -> list:
    nid = sval(node.get("id"))
    position = c.ix.node_pos.get(nid, 0)
    columns, problems = D.normalise_columns(mapping(node.get("props")).get("columns"), nid, position)
    return columns


def entity_row(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    props = mapping(node.get("props"))
    return row([T.code(nid), T.md_cell(node.get("name"), slot("node.name", nid)), code_or_na(props.get("owner")), code_or_na(props.get("store")),
                str(len(_columns(c, node)))])


def _flags(c: Ctx, column: Mapping) -> str:
    flag = lambda key: T.md_text(T.tr(c.lang, "skeleton.flag." + key))  # noqa: E731
    out = []
    if column.get("pk"):
        out.append(flag("pk"))
    if column.get("fk"):
        out.append(flag("fk") + (f" {T.code(column['fk'])}" if column["fk"] != "?" else ""))
    for key in ("unique", "required", "nullable", "pii"):
        if column.get(key):
            out.append(flag(key))
    return ", ".join(out) if out else T.na()


def entity_section_dd(c: Ctx, node: Mapping) -> str:
    nid = sval(node.get("id"))
    props = mapping(node.get("props"))
    meta = " · ".join([T.code(nid), f"{T.md_text(field(c, 'owner'))} {code_or_na(props.get('owner'))}",
                       f"{T.md_text(field(c, 'store'))} {code_or_na(props.get('store'))}"])
    columns = _columns(c, node)
    unspecified = T.md_text(T.tr(c.lang, "diagrams.unspecified"))
    body = [[T.md_text(col["name"]), T.md_text(col["type"]) or unspecified, _flags(c, col), T.na()] for col in columns]
    table = "\n".join(T.md_table(T.tr_list(c.lang, "skeleton.th.column"), body)) if columns else T.not_authored(slot("node.columns", nid))
    return f"### {T.md_cell(node.get('name'), slot('node.name', nid))}\n{meta}\n\n{table}"


# --------------------------------------------------------------------------------------------- machines

def _machine_facts(c: Ctx, machine: Mapping, mid: str) -> list:
    states = str_list(machine.get("states"))
    terminal = str_list(machine.get("terminal"))
    initial = machine.get("initial")
    lines = [bullet(field(c, "initial"), T.md_text(initial) if isinstance(initial, str) and initial else T.not_authored(slot("machine.initial", mid))),
             bullet(field(c, "terminal"), ", ".join(T.md_text(s) for s in terminal) or T.na()),
             bullet(field(c, "states"), ", ".join(T.md_text(s) for s in states) or T.not_authored(slot("machine.states", mid)))]
    err = machine.get("errorCode")
    if isinstance(err, str) and err:
        lines.append(bullet(field(c, "error_code"), T.md_text(err)))
    return lines


def _machine_title(c: Ctx, mid: str) -> str:
    node = c.ix.node.get(mid)
    return f"### {T.md_cell(node.get('name'), slot('node.name', mid)) if node else T.code(mid)}\n{T.code(mid)}"


def machine_section_req(c: Ctx, machine: Mapping) -> str:
    mid = sval(machine.get("id"))
    return "\n".join([_machine_title(c, mid)] + _machine_facts(c, machine, mid))


def machine_section(c: Ctx, machine: Mapping) -> str:
    mid = sval(machine.get("id"))
    rows = []
    transitions = machine.get("transitions") if isinstance(machine.get("transitions"), list) else []
    for t in transitions:
        if not isinstance(t, Mapping):
            continue
        tid = sval(t.get("id"))
        guard = t.get("guard")
        rows.append([T.code(tid) if tid else T.not_authored(slot("transition.id", mid)),
                     T.md_cell(t.get("from"), slot("transition.from", tid or mid)), T.md_cell(t.get("to"), slot("transition.to", tid or mid)),
                     T.md_text(T.clip(D.guard_text(guard), 100)) if isinstance(guard, Mapping) and guard else T.na(),
                     T.md_text(sval(t.get("errorCode"))) or T.na()])
    table = "\n".join(T.md_table(T.tr_list(c.lang, "skeleton.th.transition"), rows))
    return "\n".join([_machine_title(c, mid)] + _machine_facts(c, machine, mid)) + "\n\n" + table


# --------------------------------------------------------------------------------------------- policies, tbds, diagrams, acceptance

def policy_row(c: Ctx, policy: Mapping) -> str:
    return P.policy_table(c.ix, c.lang, [policy])[2]


def tbd_row(c: Ctx, tbd: Mapping) -> str:
    one = copy.copy(c.ix)
    one.tbds = [tbd]
    return P.tbd_table(one, c.lang)[2]


def unit_item(c: Ctx, unit: Mapping) -> str:
    return "- " + T.dg_link(unit["title"], unit["id"])


def acceptance_case(c: Ctx, case: Mapping) -> str | None:
    cid = sval(case.get("caseId"))
    tpl = lambda key: T.tr(c.lang, "prompts.acceptance." + key)  # noqa: E731
    lines = []
    states = pairs_text(case.get("expectedTerminalState"))
    lines.append("- " + T.fill(tpl("case_terminal"), id=T.Raw(T.code(cid)), state=T.Raw(states or T.not_authored(slot("case.expect", cid)))))
    outcomes = case.get("expectedOutcomes")
    for item in outcomes if isinstance(outcomes, list) else []:
        if not isinstance(item, Mapping):
            continue
        err = sval(item.get("errorCode"))
        lines.append("- " + T.fill(tpl("outcome"), id=T.Raw(T.code(cid)),
                                   rule=T.Raw(T.code(item.get("ruleId")) if sval(item.get("ruleId")) else T.not_authored(slot("outcome.ruleId", cid))),
                                   outcome=T.Raw(T.md_text(item.get("outcome")) if sval(item.get("outcome")) else T.not_authored(slot("outcome.outcome", cid))),
                                   code=T.Raw(f" ({T.md_text(err)})" if err else "")))
    return "\n".join(lines)


def acceptance_requirement(c: Ctx, req: Mapping) -> str | None:
    tests = str_list(req.get("tstIds"))
    if not tests:
        return None
    rid = sval(req.get("id"))
    return "- " + T.fill(T.tr(c.lang, "prompts.acceptance.tests"), id=T.Raw(T.code(rid)), tests=T.Raw(ids_cell(tests)))
