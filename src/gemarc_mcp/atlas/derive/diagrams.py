"""Diagram units (component, class, state, ER) derived from the structure of an expanded model.

A unit is a plain dict ``{id, type, title, covers{nodeIds, edgeIds, caseIds}, requirementIds, evidenceLevel,
evidenceIds, data, mermaid, issues}``. ``id`` is the target of ``dg:<id>`` links in documents; ids are stable
(``component-<building>``, ``class-<node>``, ``state-<machine>``, ``er-all``) and link-safe. ``data`` is the structured
form the Mermaid builders and the DG lint rules read; ``mermaid`` is the diagram text.

**Nothing here raises.** The reference crashes on a State node without an owner edge, an Entity without columns and a
node in an unknown room; here each of those yields an issue and an explicit marker and the rest is still drawn. The
deletion-matrix test deletes every required field in turn and asserts exactly that. Sequence diagrams are not built
here: ``run_units`` (below) draws them, and only from the sealed runs that ``oracle.sealed_runs`` hands over.
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping

from .. import model as am
from . import i18n as T
from . import mermaid as M
from .policy import Index, data_policies, mapping, str_list, sval, whole_scope

KINDS = ("component", "class", "state", "er")
CLASS_KINDS = ("Class", "Interface")
TABLE_KINDS = ("Entity", "Table")
RULE_STATE_OWNER = "SM-003"
RULE_PLACEMENT = "DG-011"
SPLIT_LIMIT = M.MAX_TEXT
UNKNOWN_KIND = "DG-008"
UNIT_FIELDS = ("id", "type", "title", "covers", "requirementIds", "evidenceLevel", "evidenceIds", "data", "mermaid", "issues")


# --------------------------------------------------------------------------------------------- helpers

def guard_text(guard, depth: int = 0) -> str:
    """Compact one-line text of a guard (``param op value``; ``all(..)``, ``any(..)``, ``not(..)``). Total: bad input is ``''``."""
    if not isinstance(guard, Mapping) or depth > 8:
        return ""
    for key in ("all", "any"):
        if key in guard and isinstance(guard[key], list):
            parts = [guard_text(g, depth + 1) for g in guard[key]]
            return f"{key}(" + ", ".join(p for p in parts if p) + ")"
    if "not" in guard:
        inner = guard_text(guard["not"], depth + 1)
        return f"not({inner})" if inner else ""
    param, op = guard.get("param"), guard.get("op")
    if not isinstance(param, str) or not isinstance(op, str):
        return ""
    value = guard.get("value")
    return f"{param} {op}" + (f" {T.scalar(value)}" if "value" in guard else "")


def _unique(base: str, used: set) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _weakest(ix: Index, node_ids: Iterable[str]) -> str:
    levels = [sval(ix.node[n].get("verification")) for n in node_ids if n in ix.node]
    levels = [lv for lv in levels if ix.profile.has("verification", lv)]
    return am.weakest(levels, profile=ix.profile) if levels else "Unknown"


def _unit(ix: Index, kind: str, uid: str, title: str, node_ids: list, edge_ids: list, case_ids: list, data: dict,
          text: str, issues: list) -> dict:
    node_set = set(node_ids)
    evidence: list = []
    for n in node_ids:
        for ev in str_list(ix.node.get(n, {}).get("evidenceIds")):
            if ev not in evidence:
                evidence.append(ev)
    reqs = [rid for rid, r in ix.requirement.items() if node_set & set(str_list(r.get("nodeIds")))]
    return {"id": uid, "type": kind, "title": T.clip(title, 120),
            "covers": {"nodeIds": list(node_ids), "edgeIds": list(edge_ids), "caseIds": list(case_ids)},
            "requirementIds": reqs, "evidenceLevel": _weakest(ix, node_ids), "evidenceIds": evidence,
            "data": data, "mermaid": text, "issues": issues}


def _node_title(ix: Index, node_id: str) -> str:
    return ix.label_of(node_id) or node_id


def _placement_issue(ix: Index, node: Mapping, position: int) -> dict | None:
    """The first broken link of the node -> room -> floor -> building chain, as an issue (``None`` when intact)."""
    nid = sval(node.get("id"))
    path = f"nodes[{position}]"
    room = ix.room_of(node)
    if room is None:
        return T.issue(RULE_PLACEMENT, "ERROR", path + ".roomId", "the node is in no known room; drawn under the unplaced group",
                       got=node.get("roomId") if isinstance(node.get("roomId"), str) else nid)
    floor = ix.floor_of(room)
    if floor is None:
        return T.issue(RULE_PLACEMENT, "ERROR", f"space.rooms.{T.slot_name(room.get('id'))}.floorId",
                       "the room is on no known floor; its nodes are drawn under the unplaced group", got=room.get("floorId"))
    if ix.building_of(floor) is None:
        return T.issue(RULE_PLACEMENT, "ERROR", f"space.floors.{T.slot_name(floor.get('id'))}.buildingId",
                       "the floor is in no known building; its nodes are drawn under the unplaced group", got=floor.get("buildingId"))
    return None


# --------------------------------------------------------------------------------------------- component

def _component_node(ix: Index, node: Mapping) -> dict:
    kind = sval(node.get("kind"))
    item = ix.profile.item("kinds", kind)
    return {"id": sval(node.get("id")), "label": sval(node.get("name")), "kind": kind,
            "shape": sval(item.get("shape")) if item else "box", "floorLevel": ix.floor_level(node)}


def _component_units(ix: Index, lang: str) -> tuple:
    view = ix.profile.item("views", "development-view")
    wanted = set(view["kinds"]) if view and view.get("kinds") else set()
    relations = {r["id"] for r in ix.profile.raw["relations"] if "logical-view" in r.get("views", [])}
    issues: list = []
    members: dict = {}  # building id (or None) -> list of nodes
    for position, node in enumerate(ix.nodes):
        if sval(node.get("kind")) not in wanted or not sval(node.get("id")):
            continue
        building, floor, room = ix.placement(node)
        problem = _placement_issue(ix, node, position)
        if problem:
            issues.append(problem)
        members.setdefault(sval(building.get("id")) if building else None, []).append(node)
    order = [sval(b.get("id")) for b in ix.buildings if sval(b.get("id")) in members]
    if None in members:
        order.append(None)
    used: set = set()
    units: list = []
    node_building: dict = {}
    for bid in order:
        for n in members[bid]:
            node_building[sval(n.get("id"))] = bid
    cross: dict = {}  # (source building, target building) -> edge ids; collected once, not once per building
    for e in ix.edges:
        if sval(e.get("relation")) in relations:
            a, b = node_building.get(sval(e.get("sourceId"))), node_building.get(sval(e.get("targetId")))
            # only edges between two placed buildings: the unplaced group (key None) is no node of the overview, so an edge to or from it
            # is neither drawn nor counted in a label, and the unit must not claim to cover it (a node of no listed kind has no key at all)
            if a is not None and b is not None and a != b:
                cross.setdefault((a, b), []).append(sval(e.get("id")))
    for bid in order:
        group_nodes = members[bid]
        bname = sval(ix.building[bid].get("name")) if bid in ix.building else ""
        if bid is not None and not bname:
            issues.append(T.issue(RULE_PLACEMENT, "INFO", f"space.buildings.{T.slot_name(bid)}.name", "the building has no name; its id is used in the title", got=bid))
        title_name = bname or (bid if bid else T.tr(lang, "diagrams.unplaced"))
        if bid is None:
            title_name = T.tr(lang, "diagrams.unplaced")
        edge_rows = [e for e in ix.edges if sval(e.get("relation")) in relations
                     and node_building.get(sval(e.get("sourceId")), 0) == bid and node_building.get(sval(e.get("targetId")), 0) == bid]
        pieces = [(bid, None, group_nodes, edge_rows)]
        data, text, bissues = _component_data(ix, lang, group_nodes, edge_rows)
        if len(text) > SPLIT_LIMIT:
            pieces = []
            by_floor: dict = {}
            for n in group_nodes:
                by_floor.setdefault(sval(ix.room_of(n).get("floorId")) if ix.room_of(n) else None, []).append(n)
            for fid, fnodes in by_floor.items():
                ids = {sval(n.get("id")) for n in fnodes}
                pieces.append((bid, fid, fnodes, [e for e in edge_rows if sval(e.get("sourceId")) in ids and sval(e.get("targetId")) in ids]))
            issues.append(T.issue("DG-010", "INFO", f"component.{T.slot_name(bid)}", "the diagram was split by floor; edges between floors are not drawn"))
        for _, fid, nodes_in, edges_in in pieces:
            if len(pieces) > 1:
                data, text, bissues = _component_data(ix, lang, nodes_in, edges_in)
            uid = _unique("component-" + T.dg_id(bid if bid else "unplaced") + (("-" + T.dg_id(fid)) if len(pieces) > 1 and fid else ""), used)
            issues.extend(bissues)
            title = T.fill(T.tr(lang, "diagrams.component"), name=title_name)
            units.append(_unit(ix, "component", uid, title, [n["id"] for n in data["nodes"]], [e["id"] for e in data["edges"]], [],
                               data, text, list(bissues)))
    if len([b for b in order if b is not None]) >= 2 and cross:
        units.append(_overview_unit(ix, lang, order, cross, used))
    return units, issues


def _component_data(ix: Index, lang: str, nodes: list, edges: list) -> tuple:
    ids = {sval(n.get("id")) for n in nodes}
    by_floor: dict = {}
    for n in nodes:
        room = ix.room_of(n)
        by_floor.setdefault(sval(room.get("floorId")) if room else "", []).append(sval(n.get("id")))
    groups = []
    for fid, node_ids in by_floor.items():
        floor = ix.floor.get(fid)
        groups.append({"id": fid or "unplaced", "label": sval(floor.get("name")) if floor else T.tr(lang, "diagrams.unplaced"),
                       "nodeIds": node_ids})
    drawn_edges = []
    for e in edges:
        if sval(e.get("sourceId")) in ids and sval(e.get("targetId")) in ids:
            drawn_edges.append({"id": sval(e.get("id")), "from": sval(e.get("sourceId")), "to": sval(e.get("targetId")),
                                "label": sval(e.get("label")), "static": e.get("static") is True,
                                "relation": sval(e.get("relation"))})
    data = {"groups": groups, "nodes": [_component_node(ix, n) for n in nodes], "edges": drawn_edges}
    text, issues = M.build("component", data)
    return data, text, issues


def _overview_unit(ix: Index, lang: str, order: list, cross: dict, used: set) -> dict:
    nodes = [{"id": b, "label": sval(ix.building[b].get("name")), "shape": "box"} for b in order if b is not None and b in ix.building]
    edges = [{"id": f"{a}>{b}", "from": a, "to": b, "label": str(len(ids)), "static": True} for (a, b), ids in cross.items()]
    data = {"groups": [], "nodes": nodes, "edges": edges}
    text, issues = M.build("component", data)
    edge_ids = [e for ids in cross.values() for e in ids]
    title = T.fill(T.tr(lang, "diagrams.component"), name=T.tr(lang, "diagrams.all"))
    unit = _unit(ix, "component", _unique("component-overview", used), title, [], edge_ids, [], data, text, list(issues))
    return unit


# --------------------------------------------------------------------------------------------- class

def _class_units(ix: Index, lang: str) -> tuple:
    units: list = []
    issues: list = []
    used: set = set()
    for position, node in enumerate(ix.nodes):
        nid = sval(node.get("id"))
        if sval(node.get("kind")) not in CLASS_KINDS or not nid:
            continue
        props = mapping(node.get("props"))
        methods = [m for m in props.get("methods", []) if isinstance(m, str) and m.strip()] if isinstance(props.get("methods"), list) else []
        enums_raw = mapping(props.get("enums"))
        enums = {k: [v for v in vals if isinstance(v, str)] for k, vals in enums_raw.items()
                 if isinstance(k, str) and isinstance(vals, list)}
        machines = []
        for mid in ix.linked_machines(node):
            machine = ix.machine.get(mid, {})
            machines.append({"id": mid, "name": ix.label_of(mid) or mid, "states": str_list(machine.get("states"))})
        classes = [{"id": nid, "label": sval(node.get("name")), "annotation": "interface" if node.get("kind") == "Interface" else "",
                    "members": methods, "missing": [] if methods else ["props.methods"]}]
        relations = []
        for name, values in enums.items():
            classes.append({"id": f"{nid}#enum#{name}", "label": name, "annotation": "enumeration", "members": values, "missing": []})
            relations.append({"from": nid, "to": f"{nid}#enum#{name}", "label": "uses"})
        for m in machines:
            classes.append({"id": f"{nid}#machine#{m['id']}", "label": m["name"], "annotation": "machine", "members": m["states"], "missing": []})
            relations.append({"from": nid, "to": f"{nid}#machine#{m['id']}", "label": "state machine"})
        data = {"classes": classes, "relations": relations, "enums": enums, "linkedMachines": machines,
                "owner": sval(props.get("owner"))}
        text, bissues = M.build("class", data)
        for item in bissues:
            item["path"] = f"nodes[{position}] " + item["path"]
        node_ids = [nid] + [m["id"] for m in machines if m["id"] in ix.node]
        uid = _unique("class-" + T.dg_id(nid), used)
        units.append(_unit(ix, "class", uid, T.fill(T.tr(lang, "diagrams.class"), name=_node_title(ix, nid)), node_ids, [], [],
                           data, text, list(bissues)))
        issues.extend(bissues)
    return units, issues


# --------------------------------------------------------------------------------------------- state

def _transition_label(t: Mapping) -> str:
    guard = guard_text(t.get("guard"))
    code = sval(t.get("errorCode"))
    return " ".join(p for p in (guard, f"[{code}]" if code else "") if p)


def _state_units(ix: Index, lang: str) -> tuple:
    units: list = []
    issues: list = []
    used: set = set()
    for position, machine in enumerate(ix.machines):
        mid = sval(machine.get("id"))
        path = f"machines[{position}]"
        if not mid:
            issues.append(T.issue("DG-011", "ERROR", path + ".id", "a machine without an id cannot be drawn"))
            continue
        local: list = []
        if mid not in ix.node:
            local.append(T.issue("DG-011", "ERROR", path + ".id", "the machine id is not a node id", got=mid))
        owners = ix.owners_of_machine(mid)
        if not owners:
            local.append(T.issue(RULE_STATE_OWNER, "ERROR", f"{path}.owner", "the machine has no owner: no owns edge points at it",
                                 got=mid))
        transitions = [t for t in machine.get("transitions", []) if isinstance(t, Mapping)] if isinstance(machine.get("transitions"), list) else []
        data = {"initial": sval(machine.get("initial")), "states": str_list(machine.get("states")),
                "terminal": str_list(machine.get("terminal")), "errorCode": sval(machine.get("errorCode")),
                "owners": owners, "machineId": mid,
                "transitions": [{"id": sval(t.get("id")), "from": sval(t.get("from")), "to": sval(t.get("to")),
                                 "guard": guard_text(t.get("guard")), "errorCode": sval(t.get("errorCode")),
                                 "label": _transition_label(t)} for t in transitions]}
        text, bissues = M.build("state", data)
        for item in bissues:
            item["path"] = f"{path} " + item["path"]
        local.extend(bissues)
        owns_edges = [sval(e.get("id")) for e in ix.edges if e.get("relation") == "owns" and e.get("targetId") == mid]
        case_ids = [cid for cid, c in ix.case.items() if any(isinstance(ev, Mapping) and ev.get("to") == mid
                                                             for ev in (c.get("events") if isinstance(c.get("events"), list) else []))]
        nodes = [n for n in [mid] + owners if n in ix.node]
        uid = _unique("state-" + T.dg_id(mid), used)
        units.append(_unit(ix, "state", uid, T.fill(T.tr(lang, "diagrams.state"), name=_node_title(ix, mid)), nodes, owns_edges, case_ids,
                           data, text, local))
        issues.extend(local)
    return units, issues


# --------------------------------------------------------------------------------------------- ER

def normalise_columns(raw, node_id: str, position: int) -> tuple:
    """``(columns, issues)``: strings and dicts become ``{name, type, pk, fk, unique, pii, nullable, required}``; garbage is reported."""
    issues: list = []
    path = f"nodes[{position}].props.columns"
    if raw is None:
        return [], issues
    if not isinstance(raw, list):
        return [], [T.issue("DG-011", "ERROR", path, "columns must be a list of names or column objects", got=T.scalar(raw))]
    columns = []
    for j, item in enumerate(raw):
        if isinstance(item, str) and item.strip():
            columns.append({"name": item, "type": "", "pk": False, "fk": "", "unique": False, "pii": False,
                            "nullable": False, "required": False})
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str) and item["name"].strip():
            fk = item.get("fk")
            columns.append({"name": item["name"], "type": sval(item.get("type")), "pk": item.get("pk") is True,
                            "fk": fk if isinstance(fk, str) else ("?" if fk is True else ""),
                            "unique": item.get("unique") is True, "pii": item.get("pii") is True,
                            "nullable": item.get("nullable") is True, "required": item.get("required") is True})
        else:
            issues.append(T.issue("DG-011", "ERROR", f"{path}[{j}]", "a column needs a name: left out", got=T.scalar(item)))
    return columns, issues


def _er_units(ix: Index, lang: str) -> tuple:
    tables = [(i, n) for i, n in enumerate(ix.nodes) if sval(n.get("kind")) in TABLE_KINDS and sval(n.get("id"))]
    if not tables:
        return [], []
    found: list = []
    entities = []
    pii: list = []
    for position, node in tables:
        nid = sval(node.get("id"))
        props = mapping(node.get("props"))
        columns, problems = normalise_columns(props.get("columns"), nid, position)
        found.extend(problems)
        entities.append({"id": nid, "label": sval(node.get("name")), "columns": columns,
                         "owner": sval(props.get("owner")), "store": sval(props.get("store"))})
        pii.extend({"table": nid, "column": c["name"]} for c in columns if c["pii"])
    ids = {e["id"] for e in entities}
    relations = [{"id": sval(e.get("id")), "from": sval(e.get("sourceId")), "to": sval(e.get("targetId")), "label": sval(e.get("label")),
                  "cardinality": sval(e.get("cardinality"))}
                 for e in ix.edges if e.get("relation") == "references" and sval(e.get("sourceId")) in ids and sval(e.get("targetId")) in ids]
    policies = [{"id": sval(p.get("id")), "text": sval(p.get("text")), "scope": sval(p.get("scope")), "src": sval(p.get("src"))}
                for p in data_policies(ix, "DD", whole_scope(ix))]
    data = {"entities": entities, "relations": relations, "policies": policies, "piiColumns": pii}
    text, built = M.build("er", data)
    found.extend(built)
    title = T.fill(T.tr(lang, "diagrams.er"), name=ix.title or T.tr(lang, "diagrams.all"))
    unit = _unit(ix, "er", "er-all", title, [e["id"] for e in entities], [r["id"] for r in relations], [], data, text, list(found))
    return [unit], found


# --------------------------------------------------------------------------------------------- registry

UnitBuilder = Callable[[Index, str], tuple]
UNIT_BUILDERS: dict = {"component": _component_units, "class": _class_units, "state": _state_units, "er": _er_units}


def register_unit_builder(kind: str, function: UnitBuilder) -> None:
    """Add a unit kind (``sequence``, from sealed traces only). ``function(index, lang) -> (units, issues)``."""
    if kind in UNIT_BUILDERS and UNIT_BUILDERS[kind] is not function:
        raise ValueError(f"unit kind {kind!r} is already registered")
    UNIT_BUILDERS[kind] = function


def build_units(model, lang: str = T.PRIMARY_LANG, *, kinds: Iterable[str] | None = None, profile=None) -> dict:
    """All diagram units of ``model``: ``{"lang", "units", "issues"}``. Never raises."""
    lang = T.normalize_lang(lang)
    units: list = []
    issues: list = []
    try:
        ix = Index(model, profile)
    except Exception as exc:
        return {"lang": lang, "units": [], "issues": [T.issue("DG-099", "ERROR", "model", "the model could not be indexed", got=type(exc).__name__)]}
    for kind in (list(kinds) if kinds is not None else list(UNIT_BUILDERS)):
        function = UNIT_BUILDERS.get(kind)
        if function is None:
            issues.append(T.issue(UNKNOWN_KIND, "ERROR", "kind", "no unit builder is registered for this kind", got=T.scalar(kind)))
            continue
        try:
            built, found = function(ix, lang)
        except Exception as exc:  # the deletion matrix asserts this never happens
            issues.append(T.issue("DG-099", "ERROR", f"units.{kind}", "internal unit builder error", got=type(exc).__name__))
            continue
        units.extend(built)
        issues.extend(found)
    for unit in units:  # DC-L-001: the author's own text may carry a literal that means "missing" (same rule as the document lint)
        for number, line in enumerate(str(unit["mermaid"]).split("\n"), start=1):
            for m in T.JUNK_RE.finditer(line):
                found_junk = T.issue("DC-L-001", "ERROR", f"unit {unit['id']} line {number}", "a literal that means a value was missing or a type was wrong", got=m.group(0))
                unit["issues"] = list(unit["issues"]) + [found_junk]
                issues.append(found_junk)
    return {"lang": lang, "units": units, "issues": issues}


# --------------------------------------------------------------------------------------------- links and files

def unit_ids(units: Iterable[Mapping]) -> list:
    return [u["id"] for u in units]


def units_for_node(units: Iterable[Mapping], node_id: str) -> list:
    return [u for u in units if node_id in u["covers"]["nodeIds"]]


_PREFERRED = {"Entity": "er", "Table": "er", "State": "state", "Interface": "class", "Class": "class"}


def best_unit_for_node(ix: Index, units: Iterable[Mapping], node_id: str):
    """The most telling unit for a node (table: ER, state: state diagram, interface: class view, else component)."""
    node = ix.node.get(node_id) or {}
    mine = units_for_node(units, node_id)
    want = _PREFERRED.get(sval(node.get("kind")), "component")
    return next((u for u in mine if u["type"] == want), mine[0] if mine else None)


def links_line(units: Iterable[Mapping], node_ids: Iterable[str], lang: str, limit: int = 6) -> str:
    """``label: [title](dg:id) / ...`` for the units that touch ``node_ids`` ('' when there are none)."""
    wanted = set(node_ids)
    picked = [u for u in units if wanted & set(u["covers"]["nodeIds"])][:limit]
    if not picked:
        return ""
    return T.tr(lang, "skeleton.field.diagrams") + ": " + " / ".join(T.dg_link(u["title"], u["id"]) for u in picked)


def diagram_files(units: Iterable[Mapping]) -> dict:
    """``{"<unit id>.mmd": text}`` for ``dist/diagrams/``."""
    return {f"{u['id']}.mmd": u["mermaid"] for u in units}


# --------------------------------------------------------------------------------------------- runs (sealed items only)

RUN_TRACE_LINES = 60
RUN_STATE_NODES = 6
_RUN_TITLE = {
    "sequence": {"en-US": "Sequence: {name}", "zh-CN": "时序：{name}", "ja-JP": "メッセージの流れ：{name}"},
    "staterun": {"en-US": "State changes: {name}", "zh-CN": "状态变化：{name}", "ja-JP": "状態の推移：{name}"},
}
_ARROW_FAILED = frozenset({"Blocked", "Starved", "Rejected", "Denied", "Failed", "Timeout", "Unbound"})


def _run_title(kind: str, lang: str, name: str) -> str:
    table = _RUN_TITLE[kind]
    return T.clip(table.get(lang, table["en-US"]).format(name=name), 120)


def _sequence_text(ix: Index, run: Mapping) -> tuple:
    """``sequenceDiagram`` of a run's trace entries (at most ``RUN_TRACE_LINES``; the cut is announced)."""
    pid = M.IdMap("p")
    trace = [t for t in run.get("trace", []) if isinstance(t, Mapping)]
    lines = ["sequenceDiagram"]
    seen: list = []
    for t in trace[:RUN_TRACE_LINES]:
        for node in (t.get("sourceNodeId"), t.get("targetNodeId")):
            if isinstance(node, str) and node not in seen:
                seen.append(node)
    for node in seen:
        lines.append(f"  participant {pid(node)} as {M.quoted(_node_title(ix, node), 40)}")
    for t in trace[:RUN_TRACE_LINES]:
        a, b = t.get("sourceNodeId"), t.get("targetNodeId")
        if not (isinstance(a, str) and isinstance(b, str)):
            continue
        outcome = str(t.get("outcome"))
        arrow = "-x" if outcome in _ARROW_FAILED else "-->>" if t.get("eventType") == "Return" else "->>"
        lines.append(f"  {pid(a)}{arrow}{pid(b)}: {M.raw(str(t.get('eventType')) + ' ' + outcome, 40)}")
    issues: list = []
    if len(trace) > RUN_TRACE_LINES:
        lines.append(f"  Note over {pid(seen[0])}: {len(trace) - RUN_TRACE_LINES} more entries not drawn" if seen else "")
        issues.append(T.issue("DG-009", "INFO", "trace", f"the trace has {len(trace)} entries; the first {RUN_TRACE_LINES} are drawn"))
    return M._finish([x for x in lines if x], issues), seen


def _slug(state) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(state)) or "x"


def _state_run_text(ix: Index, run: Mapping) -> tuple:
    """``stateDiagram-v2`` of the state changes of the most active nodes of a run (flat, one state per node and name)."""
    changes: dict = {}
    for t in run.get("trace", []):
        for c in (t.get("stateChanges") or []) if isinstance(t, Mapping) else []:
            if isinstance(c, Mapping) and isinstance(c.get("nodeId"), str):
                changes.setdefault(c["nodeId"], []).append((t.get("index"), c.get("from"), c.get("to")))
    busiest = sorted(changes, key=lambda n: (-len(changes[n]), n))[:RUN_STATE_NODES]
    nid = M.IdMap("n")
    lines = ["stateDiagram-v2"]
    declared: set = set()
    body: list = []
    for node in busiest:
        for index, a, b in changes[node]:
            for state in (a, b):
                if (node, state) not in declared:
                    declared.add((node, state))
                    lines.append(f"  state {M.quoted(_node_title(ix, node) + ': ' + str(state), 60)} as {nid(node)}_{_slug(state)}")
            body.append(f"  {nid(node)}_{_slug(a)} --> {nid(node)}_{_slug(b)} : {M.raw('#' + str(index), 12)}")
    return M._finish(lines + body, []), busiest


def run_units(model, baselines: Mapping, pairs: Iterable[Mapping], lang: str = T.PRIMARY_LANG) -> dict:
    """Sequence and state-change diagram units drawn from SEALED runs: ``baselines`` ``{caseId: {"run": ...}}`` and ``pairs``
    ``[{interventionId, caseId, result}]`` exactly as ``oracle.sealed_runs`` returns them. Nothing is drawn for an item that is
    not in the arguments, so an unsealed case can never get a diagram. Never raises; returns ``{"units", "issues"}``."""
    lang = T.normalize_lang(lang)
    units: list = []
    issues: list = []
    try:
        ix = Index(model)
    except Exception as exc:  # noqa: BLE001
        return {"units": [], "issues": [T.issue("DG-099", "ERROR", "model", "the model could not be indexed", got=type(exc).__name__)]}
    for cid in sorted(baselines):
        run = (baselines[cid] or {}).get("run") if isinstance(baselines[cid], Mapping) else None
        if not isinstance(run, Mapping) or run.get("status") != "OK":
            continue
        case = ix.case.get(cid, {})
        title = str(case.get("title") or cid)
        try:
            (text, found), seen = _sequence_text(ix, run)
            units.append(_unit(ix, "sequence", "sequence-" + T.dg_id(cid), _run_title("sequence", lang, title), seen, [], [cid],
                               {"caseId": cid, "source": "run"}, text, found))
            issues.extend(found)
            (stext, sfound), busiest = _state_run_text(ix, run)
            if busiest:
                units.append(_unit(ix, "staterun", "staterun-" + T.dg_id(cid), _run_title("staterun", lang, title), busiest, [], [cid],
                                   {"caseId": cid, "source": "run"}, stext, sfound))
        except Exception as exc:  # noqa: BLE001 - the deletion matrix asserts the builders never raise
            issues.append(T.issue("DG-099", "ERROR", f"units.run.{cid}", "internal unit builder error", got=type(exc).__name__))
    for pair in pairs:
        result = pair.get("result") if isinstance(pair, Mapping) else None
        run = (result or {}).get("B") if isinstance(result, Mapping) else None
        if not isinstance(run, Mapping) or run.get("status") != "OK":
            continue
        label = f"{pair.get('interventionId')} x {pair.get('caseId')}"
        try:
            (text, found), seen = _sequence_text(ix, run)
            units.append(_unit(ix, "sequence", "sequence-" + T.dg_id(f"{pair.get('interventionId')}-{pair.get('caseId')}"),
                               _run_title("sequence", lang, label), seen, [], [str(pair.get("caseId"))],
                               {"caseId": pair.get("caseId"), "interventionId": pair.get("interventionId"), "source": "run"}, text, found))
            issues.extend(found)
        except Exception as exc:  # noqa: BLE001
            issues.append(T.issue("DG-099", "ERROR", f"units.run.{label}", "internal unit builder error", got=type(exc).__name__))
    return {"units": units, "issues": issues}
