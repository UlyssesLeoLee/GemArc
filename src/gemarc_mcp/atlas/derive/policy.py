"""Defensive model access, selection scope and ``policies`` rows for the derive layer.

Three things live here because diagrams, prompts, lint and the document builders all need them:

* ``Index``: a read-only, total index over a model. The model may have failed the contract (a field was deleted, a
  value has the wrong type); every accessor degrades to "absent" instead of raising, so builders can report one issue
  and keep going. Contract-valid models behave exactly as the spec describes.
* ``Scope`` and ``resolve_selection``: what a document or prompt is about. The closure is deliberately one step
  (node: itself, incident edges, cases it appears in, requirements naming it; case: participants, bound edges, the
  nodes of its requirements; requirement: nodes, edges, cases; edge: itself and its endpoints). Unknown ids are
  reported, never silently dropped.
* Policy handling (design 9.1): rows carry a mandatory ``src``, a ``scope`` (all, kind:X, node:Y, req:Z), a ``level``,
  a ``chapter``, a ``kind`` and ``by``. A policy shows in its level and deeper; ``chapter = null`` resolves through
  ``DEFAULT_CHAPTER``. Missing chapter content becomes a NOT-AUTHORED marker, never a blank (release BLOCK DC-L-004).
"""
from __future__ import annotations

from typing import Iterable, Mapping

from .. import model as am
from .. import vocab
from . import i18n as T

DEFAULT_CHAPTER = {"constraint": 10, "nfr": 9, "data": 6}
FALLBACK_CHAPTER = 10
LEVEL_RANK = {"REQ": 1, "BD": 2, "DD": 3}

SEL_KEYS = ("nodeIds", "edgeIds", "caseIds", "requirementIds")
RULE_SEL_UNKNOWN = "DC-S-001"
RULE_SEL_SHAPE = "DC-S-002"
RULE_POLICY_SRC = "DC-P-001"
RULE_POLICY_SCOPE = "DC-P-002"
RULE_POLICY_TEXT = "DC-P-003"
RULE_POLICY_ENUM = "DC-P-004"


# --------------------------------------------------------------------------------------------- safe access

def rows(model, key: str) -> list:
    """The dict records of ``model[key]`` (non-dict entries and a non-list value are ignored)."""
    value = model.get(key) if isinstance(model, Mapping) else None
    return [r for r in value if isinstance(r, Mapping)] if isinstance(value, list) else []


def sval(value) -> str:
    """The value when it is a string, else the empty string."""
    return value if isinstance(value, str) else ""


def str_list(value) -> list:
    """The string items of a list (other items dropped); anything else gives ``[]``."""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _index_by(records: Iterable[Mapping], key: str) -> dict:
    out: dict = {}
    for record in records:
        rid = record.get(key)
        if isinstance(rid, str) and rid and rid not in out:
            out[rid] = record
    return out


class Index:
    """Total, read-only lookup tables over a (possibly contract-violating) model."""

    def __init__(self, model, profile: "vocab.Profile | None" = None):
        # lone surrogates (valid str, not encodable) become U+FFFD once here, so no builder or hash can fail on them
        self.model = T.sanitize(model) if isinstance(model, Mapping) else {}
        self.profile = profile or vocab.default_profile()
        m = self.model
        self.nodes = rows(m, "nodes")
        self.edges = rows(m, "edges")
        self.machines = rows(m, "machines")
        self.cases = rows(m, "cases")
        self.requirements = rows(m, "requirements")
        self.evidences = rows(m, "evidences")
        self.tbds = rows(m, "tbds")
        self.policies = rows(m, "policies")
        self.tests = rows(m, "tests")
        self.interventions = rows(m, "interventions")
        space = mapping(m.get("space"))
        self.buildings = rows(space, "buildings")
        self.floors = rows(space, "floors")
        self.rooms = rows(space, "rooms")
        self.node = _index_by(self.nodes, "id")
        self.edge = _index_by(self.edges, "id")
        self.machine = _index_by(self.machines, "id")
        self.case = _index_by(self.cases, "caseId")
        self.requirement = _index_by(self.requirements, "id")
        self.evidence = _index_by(self.evidences, "evidenceId")
        self.tbd = _index_by(self.tbds, "id")
        self.test = _index_by(self.tests, "id")
        self.policy = _index_by(self.policies, "id")
        self.building = _index_by(self.buildings, "id")
        self.floor = _index_by(self.floors, "id")
        self.room = _index_by(self.rooms, "id")
        self.node_pos = {sval(n.get("id")): i for i, n in enumerate(self.nodes) if sval(n.get("id"))}
        meta = mapping(m.get("meta"))
        self.mode = sval(meta.get("mode")) or "asis"
        self.title = sval(meta.get("title"))
        self.project_id = sval(meta.get("projectId"))
        self._incident: dict | None = None

    # -- placement
    def room_of(self, node: Mapping):
        return self.room.get(sval(node.get("roomId")))

    def floor_of(self, room: Mapping | None):
        return self.floor.get(sval(room.get("floorId"))) if room else None

    def building_of(self, floor: Mapping | None):
        return self.building.get(sval(floor.get("buildingId"))) if floor else None

    def placement(self, node: Mapping) -> tuple:
        """``(building, floor, room)`` of a node; an entry is ``None`` where the chain is broken."""
        room = self.room_of(node)
        floor = self.floor_of(room)
        return self.building_of(floor), floor, room

    def floor_level(self, node: Mapping):
        floor = self.floor_of(self.room_of(node))
        level = floor.get("level") if floor else None
        return level if isinstance(level, int) and not isinstance(level, bool) else None

    # -- graph
    def incident_edge_ids(self, node_id: str) -> list:
        if self._incident is None:
            table: dict = {}
            for e in self.edges:
                eid = sval(e.get("id"))
                if not eid:
                    continue
                for end in (sval(e.get("sourceId")), sval(e.get("targetId"))):
                    if end:
                        bucket = table.setdefault(end, [])
                        if eid not in bucket:
                            bucket.append(eid)
            self._incident = table
        return list(self._incident.get(node_id, ()))

    def owners_of_machine(self, machine_id: str) -> list:
        """Node ids that own the machine through an ``owns`` edge (edge order, no duplicates)."""
        out: list = []
        for e in self.edges:
            if e.get("relation") == "owns" and e.get("targetId") == machine_id:
                owner = sval(e.get("sourceId"))
                if owner and owner not in out:
                    out.append(owner)
        return out

    def linked_machines(self, node: Mapping) -> list:
        """Machine ids linked to a class-like node through ``props.owner`` and the ``owns`` edges (spec 5.14)."""
        try:
            return am.linked_machine_ids(dict(node), self.edges, list(self.machine))
        except Exception:  # a malformed props.owner must not stop a diagram
            return []

    def label_of(self, node_id: str) -> str:
        node = self.node.get(node_id)
        return sval(node.get("name")) if node else ""

    def sha8(self) -> str:
        try:
            return am.model_sha8(dict(self.model))
        except Exception:  # NaN, cycles, absurd nesting
            return "unhashed"

    def case_participants(self, case: Mapping) -> list:
        out: list = []
        for event in case.get("events") if isinstance(case.get("events"), list) else []:
            if not isinstance(event, Mapping):
                continue
            for end in (event.get("from"), event.get("to")):
                if isinstance(end, str) and end in self.node and end not in out:
                    out.append(end)
        return out

    def case_bound_edges(self, case: Mapping) -> list:
        """Edge ids an event of the case uses: the explicit ``edge`` or the runtime edges between its two nodes."""
        static = self.profile.static_relations
        out: list = []
        for event in case.get("events") if isinstance(case.get("events"), list) else []:
            if not isinstance(event, Mapping):
                continue
            explicit = event.get("edge")
            if isinstance(explicit, str) and explicit in self.edge:
                if explicit not in out:
                    out.append(explicit)
                continue
            a, b = event.get("from"), event.get("to")
            if not (isinstance(a, str) and isinstance(b, str)):
                continue
            for e in self.edges:
                eid = sval(e.get("id"))
                if not eid or sval(e.get("relation")) in static or eid in out:
                    continue
                pair = (e.get("sourceId"), e.get("targetId"))
                if pair == (a, b) or pair == (b, a):
                    out.append(eid)
        return out


# --------------------------------------------------------------------------------------------- selection

class Scope:
    """What a document or prompt covers. Id lists are in model order; ``kinds`` are the kinds of the covered nodes."""

    def __init__(self, node_ids=(), edge_ids=(), case_ids=(), requirement_ids=(), kinds=(), whole: bool = False):
        self.node_ids = list(node_ids)
        self.edge_ids = list(edge_ids)
        self.case_ids = list(case_ids)
        self.requirement_ids = list(requirement_ids)
        self.kinds = set(kinds)
        self.whole = whole

    def is_empty(self) -> bool:
        return not (self.node_ids or self.edge_ids or self.case_ids or self.requirement_ids)

    def as_selection(self) -> dict:
        return {"nodeIds": list(self.node_ids), "edgeIds": list(self.edge_ids),
                "caseIds": list(self.case_ids), "requirementIds": list(self.requirement_ids)}


def whole_scope(ix: Index) -> Scope:
    ids = lambda table: [k for k in table]  # noqa: E731  (dict order is model order)
    kinds = {sval(n.get("kind")) for n in ix.nodes if sval(n.get("kind"))}
    return Scope(ids(ix.node), ids(ix.edge), ids(ix.case), ids(ix.requirement), kinds, whole=True)


def resolve_selection(ix: Index, selection=None) -> tuple:
    """``(Scope, issues)`` for a selection dict; ``None`` means the whole model, ``{}`` means nothing."""
    if selection is None:
        return whole_scope(ix), []
    issues: list = []
    if not isinstance(selection, Mapping):
        return Scope(), [T.issue(RULE_SEL_SHAPE, "ERROR", "selection", "the selection must be an object with id lists",
                                 got=type(selection).__name__)]
    picked: dict = {}
    tables = {"nodeIds": ix.node, "edgeIds": ix.edge, "caseIds": ix.case, "requirementIds": ix.requirement}
    for key in SEL_KEYS:
        raw = selection.get(key)
        if raw is None:
            picked[key] = []
            continue
        if not isinstance(raw, list):
            issues.append(T.issue(RULE_SEL_SHAPE, "ERROR", f"selection.{key}", "must be a list of ids",
                                  got=type(raw).__name__))
            picked[key] = []
            continue
        known: list = []
        for item in raw:
            if isinstance(item, str) and item in tables[key]:
                if item not in known:
                    known.append(item)
            else:
                issues.append(T.issue(RULE_SEL_UNKNOWN, "WARN", f"selection.{key}", "unknown id: left out of the scope",
                                      got=item if isinstance(item, str) else T.scalar(item)))
        picked[key] = known

    nodes, edges, cases, reqs = (set(picked[k]) for k in SEL_KEYS)
    for nid in list(picked["nodeIds"]):
        edges.update(ix.incident_edge_ids(nid))
        for cid, case in ix.case.items():
            if nid in ix.case_participants(case):
                cases.add(cid)
        for rid, req in ix.requirement.items():
            if nid in str_list(req.get("nodeIds")):
                reqs.add(rid)
    for cid in list(picked["caseIds"]):
        case = ix.case[cid]
        nodes.update(ix.case_participants(case))
        edges.update(ix.case_bound_edges(case))
        for rid in str_list(case.get("requirementIds")):
            if rid in ix.requirement:
                reqs.add(rid)
                nodes.update(n for n in str_list(ix.requirement[rid].get("nodeIds")) if n in ix.node)
    for rid in list(picked["requirementIds"]):
        req = ix.requirement[rid]
        nodes.update(n for n in str_list(req.get("nodeIds")) if n in ix.node)
        edges.update(e for e in str_list(req.get("edgeIds")) if e in ix.edge)
        for cid, case in ix.case.items():
            if rid in str_list(case.get("requirementIds")):
                cases.add(cid)
    for eid in list(picked["edgeIds"]):
        e = ix.edge[eid]
        for end in (sval(e.get("sourceId")), sval(e.get("targetId"))):
            if end in ix.node:
                nodes.add(end)

    order = lambda table, wanted: [k for k in table if k in wanted]  # noqa: E731
    node_ids = order(ix.node, nodes)
    kinds = {sval(ix.node[n].get("kind")) for n in node_ids if sval(ix.node[n].get("kind"))}
    return Scope(node_ids, order(ix.edge, edges), order(ix.case, cases), order(ix.requirement, reqs), kinds), issues


# --------------------------------------------------------------------------------------------- policies

def parse_scope(scope) -> tuple | None:
    """``(type, argument)`` of a scope string, ``None`` when it is not one of the four shapes."""
    if not isinstance(scope, str):
        return None
    if scope == "all":
        return ("all", "")
    for prefix in ("kind", "node", "req"):
        head = prefix + ":"
        if scope.startswith(head) and len(scope) > len(head):
            return (prefix, scope[len(head):])
    return None


def policy_chapter(row: Mapping) -> int:
    """The chapter a policy prints in: its own ``chapter`` (1..20) or the default of its ``kind``."""
    chapter = row.get("chapter")
    if isinstance(chapter, int) and not isinstance(chapter, bool) and 1 <= chapter <= 20:
        return chapter
    return DEFAULT_CHAPTER.get(sval(row.get("kind")), FALLBACK_CHAPTER)


def level_reaches(policy_level, doc_level: str) -> bool:
    """A policy appears at its own level and deeper (REQ is inside BD is inside DD); an unknown level only at DD."""
    return LEVEL_RANK.get(policy_level if isinstance(policy_level, str) else "", 3) <= LEVEL_RANK.get(doc_level, 3)


def scope_matches(scope_text, scope: Scope) -> bool:
    parsed = parse_scope(scope_text)
    if parsed is None:
        return False
    kind, arg = parsed
    if kind == "all":
        return True
    if kind == "kind":
        return arg in scope.kinds
    if kind == "node":
        return arg in scope.node_ids
    return arg in scope.requirement_ids


def applicable_policies(ix: Index, level: str, scope: Scope, kind: str | None = None, chapter: int | None = None) -> list:
    """Policies shown for ``level`` and ``scope`` (optionally of one ``kind`` or one chapter), in chapter then row order."""
    picked = []
    for position, row in enumerate(ix.policies):
        if kind is not None and row.get("kind") != kind:
            continue
        if not level_reaches(row.get("level"), level) or not scope_matches(row.get("scope"), scope):
            continue
        row_chapter = policy_chapter(row)
        if chapter is not None and row_chapter != chapter:
            continue
        picked.append((row_chapter, position, row))
    picked.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in picked]


def policy_stats(ix: Index) -> dict:
    """Counts for the quality report: ``byAi`` is the hallucination surface."""
    by_ai = sum(1 for r in ix.policies if r.get("by") == "ai")
    by_user = sum(1 for r in ix.policies if r.get("by") == "user")
    return {"total": len(ix.policies), "byAi": by_ai, "byUser": by_user,
            "other": len(ix.policies) - by_ai - by_user}


def _src_cell(ix: Index, row: Mapping, lang: str) -> str:
    src = sval(row.get("src"))
    if not src:
        return T.not_authored(T.slot_name("policy.src", row.get("id")))
    holder = ix.requirement.get(src) or ix.tbd.get(src)
    title = sval(holder.get("title")) if holder else ""
    return T.code(src) + (f" {T.md_text(title)}" if title else "")


def policy_table(ix: Index, lang: str, policies: Iterable[Mapping]) -> list:
    """Table lines (statement, source, written by) of ``policies``; an empty text or source becomes a marker."""
    body = []
    for row in policies:
        pid = row.get("id")
        text = T.md_cell(row.get("text"), T.slot_name("policy.text", pid))
        by = sval(row.get("by"))
        by_cell = T.md_text(T.tr(lang, f"skeleton.by.{by}")) if by in ("ai", "user") else T.not_authored(T.slot_name("policy.by", pid))
        body.append([text, _src_cell(ix, row, lang), by_cell])
    return T.md_table(T.tr_list(lang, "skeleton.th.policy"), body)


def nfr_chapter(ix: Index, lang: str, level: str, scope: Scope) -> list:
    """Chapter 9 body: the ``nfr`` policies in scope, or the NOT-AUTHORED marker ``ch9.nfr`` (release BLOCK)."""
    chosen = applicable_policies(ix, level, scope, kind="nfr")
    if not chosen:
        return [T.not_authored("ch9.nfr", lang)]
    return policy_table(ix, lang, chosen)


def data_policies(ix: Index, level: str, scope: Scope) -> list:
    return applicable_policies(ix, level, scope, kind="data")


def tbd_table(ix: Index, lang: str) -> list:
    """Every TBD of the model (none is dropped because no requirement cites it); a missing decision is a marker."""
    body = []
    for tbd in ix.tbds:
        tid = tbd.get("id")
        decision = T.md_cell(tbd.get("decision"), T.slot_name("tbd.decision", tid))
        evidence = ", ".join(T.code(e) for e in str_list(tbd.get("evidenceIds"))) or T.na()
        body.append([T.code(tid) if sval(tid) else T.not_authored("tbd.id"),
                     T.md_cell(tbd.get("title"), T.slot_name("tbd.title", tid)), decision, evidence])
    return T.md_table(T.tr_list(lang, "skeleton.th.tbd"), body)


def constraints_chapter(ix: Index, lang: str, level: str, scope: Scope) -> list:
    """Chapter 10 body: constraint policies (or the explicit 'none declared' line) followed by every TBD."""
    lines: list = []
    chosen = applicable_policies(ix, level, scope, kind="constraint")
    if chosen:
        lines += policy_table(ix, lang, chosen)
    else:
        lines.append(T.empty_line(lang, "constraints", declared=True))
    lines.append("")
    if ix.tbds:
        lines += tbd_table(ix, lang)
    else:
        lines.append(T.empty_line(lang, "tbds"))
    return lines


def policy_issues(ix: Index) -> list:
    """Row-level problems of ``policies`` (missing or dangling ``src``, bad scope, empty text, unknown enum value)."""
    issues: list = []
    for i, row in enumerate(ix.policies):
        pid = sval(row.get("id")) or f"#{i}"
        path = f"policies[{i}]"
        src = row.get("src")
        if not isinstance(src, str) or not src:
            issues.append(T.issue(RULE_POLICY_SRC, "ERROR", path + ".src", "every policy needs a src (a requirement or a TBD)",
                                  got=pid))
        elif src not in ix.requirement and src not in ix.tbd:
            issues.append(T.issue(RULE_POLICY_SRC, "ERROR", path + ".src", "src names neither a requirement nor a TBD", got=src))
        scope = row.get("scope")
        parsed = parse_scope(scope)
        if parsed is None:
            issues.append(T.issue(RULE_POLICY_SCOPE, "ERROR", path + ".scope", "scope must be all, kind:X, node:Y or req:Z",
                                  got=scope if isinstance(scope, str) else T.scalar(scope)))
        else:
            kind, arg = parsed
            table = {"node": ix.node, "req": ix.requirement}.get(kind)
            if table is not None and arg not in table:
                issues.append(T.issue(RULE_POLICY_SCOPE, "ERROR", path + ".scope", "scope names an object that does not exist",
                                      got=scope))
            if kind == "kind" and not ix.profile.has("kinds", arg):
                issues.append(T.issue(RULE_POLICY_SCOPE, "WARN", path + ".scope", "scope names an unknown node kind", got=scope))
        if not sval(row.get("text")).strip():
            issues.append(T.issue(RULE_POLICY_TEXT, "ERROR", path + ".text", "a policy needs its statement", got=pid))
        for field, group in (("level", "levels"), ("kind", "policyKinds")):
            if not ix.profile.has(group, row.get(field)):
                issues.append(T.issue(RULE_POLICY_ENUM, "ERROR", f"{path}.{field}", f"{field} is not in the vocabulary",
                                      got=row.get(field) if isinstance(row.get(field), str) else T.scalar(row.get(field))))
        if row.get("by") not in ("ai", "user"):
            issues.append(T.issue(RULE_POLICY_ENUM, "ERROR", path + ".by", "by must be ai or user",
                                  got=row.get("by") if isinstance(row.get("by"), str) else T.scalar(row.get("by"))))
    return issues
