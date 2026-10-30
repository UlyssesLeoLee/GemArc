"""Document fragments and closures: what a free selection of elements turns into.

``build_docset(model, lang)`` renders one Markdown piece (a *fragment*) per element and level and precomputes, for every
element id, the fragments one selection step pulls in (its *closure*). A document is then the union of the closures of
the selected ids plus the always-present fragments, ordered by ``(chapter, order)`` and joined by ``assemble.py`` (the
same few lines exist in JavaScript for the browser; a CI test asserts both give the same bytes).

Docset shape (``gemarc-atlas-docset/1``; one language):

```
{"schema", "fragments": {"language", "modelSha8", "unselected", "items", "frame", "groups", "always", "elements"},
 "closures": {"<elementId>": [fid * 8 + levelMask, ...]}, "issues", "stats"}
items[]    {"fid", "chapter", "order", "level", "grp", "md"}   fid == index == order, level = shallowest level using it
groups     {"<gid>": {"head", "empty", "join"}}                head = table header lines (printed once, before the first
                                                              member), empty = text when the group has no member, join =
                                                              separator between members ("\\n" rows, "\\n\\n" blocks)
frame      {"REQ"|"BD"|"DD": {"head", "chapters": [{"n", "h", "note", "empty", "groups": [gid, ...]}]}}
always     [fid * 8 + levelMask]   fragments of every document (fixed chapters, all TBDs, policies with scope all)
closures   levelMask bits: 1 = REQ, 2 = BD, 4 = DD
```

Closures (one step, never transitive; the closure at a level only holds fragments that exist at that level):

* node: its own pieces (section or row, data, state machine, ports, mapping row, diagram links, evidence rows, policies
  scoped to it or to its kind), its incident edges, the cases it takes part in, the requirements naming it;
* edge: itself, its evidence, both end nodes (with their own pieces), and the link of a diagram that covers no node
  (the building overview, which illustrates the edges that cross buildings);
* case: itself, its evidence and acceptance item, its participants, the edges its events use, its requirements and the
  nodes of those requirements;
* requirement: itself, its evidence, its nodes, its edges, the cases that cite it, policies scoped to it.

Order: rows, links and capped id lists follow the order of the model arrays (nodes, edges, cases, requirements, policies,
TBDs), so the same facts in another array order give another (equally valid) document and another document number. The
expander emits one canonical order; a hand-assembled model is not re-sorted here. Evidences, tests and machines are looked
up by id and do not depend on their array order.

Failure: ``build_docset`` never raises. When the builder fails it returns the same shape with no fragment and no element
(the frame of the three levels, an empty coverage) plus one DC-D-099 issue, so ``assemble`` and the outline still work.

Detail levels follow the vocabulary (spec 5.9): a node or edge is part of a document of level L only when its detail
level is at most L; REQ is inside BD is inside DD.
"""
from __future__ import annotations

import json

from . import diagrams as D
from . import docs as X
from . import i18n as T
from . import policy as P
from .policy import str_list, sval

SCHEMA = "gemarc-atlas-docset/1"
LEVELS = X.LEVELS
RANK = X.RANK
BIT = {1: 1, 2: 2, 3: 4}
SHIFT = 3
MAX_CHAPTER = T.CHAPTER_COUNT
PORT_LIST = 8

# (chapter, group id, shallowest level, deepest level, member separator)
_STATIC_GROUPS = [
    (1, "c1", 1, 3, "\n\n"),
    (2, "c2", 1, 3, "\n\n"), (2, "ev", 1, 3, "\n"),
    (3, "req", 1, 1, "\n"), (3, "cov", 2, 3, "\n"), (3, "chmap", 3, 3, "\n"), (3, "xmap", 3, 3, "\n"),
    (4, "n.REQ", 1, 1, "\n"), (4, "e.REQ", 1, 1, "\n"), (4, "n.BD", 2, 2, "\n"), (4, "e.BD", 2, 2, "\n"),
    (4, "n.DD", 3, 3, "\n\n"), (4, "e.DD", 3, 3, "\n"), (4, "dg", 2, 3, "\n"),
    (5, "case", 1, 2, "\n"), (5, "case.DD", 3, 3, "\n\n"),
    (6, "ent", 1, 2, "\n"), (6, "ent.DD", 3, 3, "\n\n"),
    (7, "ifc", 1, 3, "\n"), (7, "ports", 2, 3, "\n"),
    (8, "mach.REQ", 1, 1, "\n\n"), (8, "mach", 2, 3, "\n\n"),
    (9, "nfr", 1, 3, "\n"),
    (10, "con", 1, 3, "\n"), (10, "tbd", 1, 3, "\n"),
    (11, "acc", 1, 3, "\n"),
    (12, "sign", 1, 3, "\n\n"),
]
_HEAD_KEY = {"req": "requirement", "cov": "coverage", "ev": "evidence", "xmap": "crossmap", "chmap": "crossmap", "n.REQ": "node_req", "e.REQ": "edge_min",
             "n.BD": "node_bd", "e.BD": "edge_bd", "e.DD": "edge_dd", "case": "case", "ent": "entity", "ifc": "edge_min", "ports": "port",
             "nfr": "policy", "con": "policy", "tbd": "tbd"}
# the chapter-level empty sentence (``skeleton.empty.<key>``) of the chapters that can be empty
_CHAPTER_EMPTY = {3: "requirements", 4: "nodes", 5: "cases", 6: "entities", 7: "interfaces", 8: "machines", 11: "acceptance"}
# groups placed after the dynamic policy groups of their chapter
_TAIL = {10: ("tbd",)}


class _Store:
    """Fragments in creation order; the final order is ``(chapter, group rank, creation order)``."""

    def __init__(self):
        self.items: list = []

    def add(self, chapter: int, grp: str, md, lo: int = 1, hi: int = 3) -> int:
        self.items.append({"chapter": chapter, "grp": grp, "md": md if isinstance(md, str) else "", "lo": lo, "hi": hi})
        return len(self.items) - 1


def _mask(lo: int, hi: int, gate: int) -> int:
    mask = 0
    for level in (1, 2, 3):
        if lo <= level <= hi and gate <= level:
            mask |= BIT[level]
    return mask


def _clean_id(value) -> bool:
    """An id the target list of chapter 1 prints the same way in Python and JavaScript."""
    return isinstance(value, str) and bool(value) and T.code(value) == "`" + value.replace("`", "'").replace("|", "\\|") + "`" \
        and T.clean(value) == value


def build_docset(model, lang: str = T.PRIMARY_LANG, units=None, profile=None) -> dict:
    """The fragments and closures of ``model`` in ``lang``. Never raises (a failing piece becomes a marker and an issue)."""
    lang = T.normalize_lang(lang)
    try:
        return _build(model, lang, units, profile)
    except Exception as exc:  # last resort; the tests assert this code never appears
        empty = _empty_docset(lang)
        empty["issues"].append(T.issue(X.RULE_INTERNAL, "ERROR", "docset", "internal fragment builder error", got=type(exc).__name__))
        return empty


def _empty_docset(lang: str) -> dict:
    """The failure result: the full shape (frame of the three levels, coverage, stats) with no fragment and no element, so that
    ``assemble``, the outline and the viewer payload keep working and print the fixed skeleton of every document."""
    frame = {}
    for level in LEVELS:
        name = T.md_text(T.tr(lang, "skeleton.docs." + level))
        frame[level] = {"head": f"# {name} {X.PH_DOCNO}",
                        "chapters": [{"n": n, "h": T.chapter_heading(lang, level, n), "note": "", "empty": T.NONE_IN_SCOPE, "groups": []}
                                     for n in range(1, MAX_CHAPTER + 1)]}
    ds = {"schema": SCHEMA, "fragments": {"language": lang, "modelSha8": "unhashed", "unselected": T.md_text(T.tr(lang, "skeleton.doc_info.unselected")),
                                          "items": [], "frame": frame, "groups": {}, "always": [], "elements": []},
          "closures": {}, "issues": []}
    ds["stats"] = _stats(ds)
    ds["coverage"] = {"unreached": {}, "notInDocuments": {"interventions": [], "reason": "no document could be built"}}
    return ds


def _build(model, lang: str, units, profile) -> dict:
    c = X.Ctx(model, lang, units, profile)
    ix = c.ix
    S = _Store()
    safe = c.safe
    own: dict = {}            # element id -> [fragment ids]
    ev_frag: dict = {}        # evidence id -> fragment id
    pol_all: list = []
    pol_node: dict = {}
    pol_kind: dict = {}
    pol_req: dict = {}
    policy_label: dict = {}   # policy fragment id -> policy id (or policies[i]) for the coverage report
    dyn_groups: dict = {}     # chapter -> [gid]
    dyn_seen: set = set()

    def mine(key, fid):
        own.setdefault(key, []).append(fid)

    iface_edges = c.interface_edges()
    iface_ids = {sval(e.get("id")) for e in iface_edges}
    iface_nodes = {sval(e.get(k)) for e in iface_edges for k in ("sourceId", "targetId")}

    # ---- evidence rows (chapter 2): every id any element cites, defined or not
    cited: list = []
    for table in (ix.nodes, ix.edges, ix.cases, ix.requirements, ix.machines):
        for rec in table:
            for eid in str_list(rec.get("evidenceIds")):
                if eid not in cited:
                    cited.append(eid)
    for eid in cited:
        ev_frag[eid] = S.add(2, "ev", safe("evidence " + eid, X.evidence_row, c, eid))

    # ---- fixed pieces
    fixed: list = []
    for level in LEVELS:
        r = RANK[level]
        fixed.append(S.add(1, "c1", safe("c1", X.c1_block, c, level), r, r))
        fixed.append(S.add(2, "c2", safe("c2", X.c2_purpose, c, level), r, r))
    fixed.append(S.add(3, "chmap", "\n".join(X.chapter_map_rows(c)), 3, 3))
    fixed.append(S.add(12, "sign", safe("sign", X.sign_block, c), 1, 3))

    # ---- diagram link pieces (chapter 4, BD and DD): a link shows from the level at which a node it illustrates is listed
    unit_frag: list = []
    unit_label: dict = {}   # diagram link fragment id -> unit id (coverage report)
    unit_edges: list = []   # (fragment id, covered edge ids) of the units that cover no node (the building overview)
    for unit in c.units:
        covered = set(unit["covers"]["nodeIds"])
        first = max(2, min([c.rank_of_node_id(n) for n in covered] or [3]))
        fid = S.add(4, "dg", safe("unit " + unit["id"], X.unit_item, c, unit), first, 3)
        unit_frag.append((fid, covered))
        unit_label[fid] = unit["id"]
        if not covered:
            unit_edges.append((fid, set(str_list(unit["covers"]["edgeIds"]))))

    # ---- nodes (and their data, machine, port and mapping pieces)
    node_ev_gate: dict = {}
    node_ids: list = []
    for position, node in enumerate(ix.nodes):
        nid = sval(node.get("id"))
        if not nid:
            c.issue(X.RULE_NODE_ID, "ERROR", f"nodes[{position}]", "a node without an id cannot be part of a document: left out")
            continue
        if nid in node_ids:
            continue
        node_ids.append(nid)
        r = c.node_rank(node)
        node_ev_gate[nid] = r
        if r <= 1:
            mine(nid, S.add(4, "n.REQ", safe("node " + nid, X.node_row_req, c, node), 1, 1))
        if r <= 2:
            mine(nid, S.add(4, "n.BD", safe("node " + nid, X.node_row_bd, c, node), 2, 2))
        mine(nid, S.add(4, "n.DD", safe("node " + nid, X.node_section_dd, c, node), 3, 3))
        mine(nid, S.add(3, "xmap", safe("node " + nid, X.crossmap_node_row, c, node), 3, 3))
        if sval(node.get("kind")) in D.TABLE_KINDS:
            if r <= 2:
                mine(nid, S.add(6, "ent", safe("entity " + nid, X.entity_row, c, node), r, 2))
            mine(nid, S.add(6, "ent.DD", safe("entity " + nid, X.entity_section_dd, c, node), 3, 3))
        machine = ix.machine.get(nid)
        if machine is not None:
            if r <= 1:
                mine(nid, S.add(8, "mach.REQ", safe("machine " + nid, X.machine_section_req, c, machine), 1, 1))
            mine(nid, S.add(8, "mach", safe("machine " + nid, X.machine_section, c, machine), max(r, 2), 3))
        if nid in iface_nodes:
            text = safe("ports " + nid, X.port_rows, c, nid, iface_ids, PORT_LIST)
            if isinstance(text, str) and text:
                mine(nid, S.add(7, "ports", text, max(r, 2), 3))
    for position, machine in enumerate(ix.machines):
        mid = sval(machine.get("id"))
        if mid and mid not in ix.node:  # a machine without a node: still shown, at the deepest level
            mine(mid, S.add(8, "mach", safe("machine " + mid, X.machine_section, c, machine), 3, 3))

    # ---- edges
    edge_ids: list = []
    edge_ev_gate: dict = {}
    for position, edge in enumerate(ix.edges):
        eid = sval(edge.get("id"))
        if not eid:
            c.issue(X.RULE_NODE_ID, "ERROR", f"edges[{position}]", "an edge without an id cannot be part of a document: left out")
            continue
        if eid in edge_ids:
            continue
        edge_ids.append(eid)
        er = c.edge_rank(edge)
        edge_ev_gate[eid] = er
        if er <= 1:
            mine(eid, S.add(4, "e.REQ", safe("edge " + eid, X.edge_row_min, c, edge), 1, 1))
        if er <= 2:
            mine(eid, S.add(4, "e.BD", safe("edge " + eid, X.edge_row_bd, c, edge), 2, 2))
        mine(eid, S.add(4, "e.DD", safe("edge " + eid, X.edge_row_dd, c, edge), 3, 3))
        if eid in iface_ids:
            mine(eid, S.add(7, "ifc", safe("edge " + eid, X.edge_row_min, c, edge), er, 3))

    # ---- cases
    case_ids: list = []
    for position, case in enumerate(ix.cases):
        cid = sval(case.get("caseId"))
        if not cid:
            c.issue(X.RULE_NODE_ID, "ERROR", f"cases[{position}]", "a case without an id cannot be part of a document: left out")
            continue
        if cid in case_ids:
            continue
        case_ids.append(cid)
        mine(cid, S.add(5, "case", safe("case " + cid, X.case_row, c, case), 1, 2))
        mine(cid, S.add(5, "case.DD", safe("case " + cid, X.case_section_dd, c, case), 3, 3))
        mine(cid, S.add(3, "xmap", safe("case " + cid, X.crossmap_case_row, c, case), 3, 3))
        text = safe("acceptance " + cid, X.acceptance_case, c, case)
        mine(cid, S.add(11, "acc", text, 1, 3))

    # ---- requirements
    req_ids: list = []
    for position, req in enumerate(ix.requirements):
        rid = sval(req.get("id"))
        if not rid:
            c.issue(X.RULE_NODE_ID, "ERROR", f"requirements[{position}]", "a requirement without an id cannot be part of a document: left out")
            continue
        if rid in req_ids:
            continue
        req_ids.append(rid)
        mine(rid, S.add(3, "req", safe("requirement " + rid, X.requirement_row, c, req), 1, 1))
        mine(rid, S.add(3, "cov", safe("requirement " + rid, X.coverage_row, c, req, "BD"), 2, 2))
        mine(rid, S.add(3, "cov", safe("requirement " + rid, X.coverage_row, c, req, "DD"), 3, 3))
        text = safe("acceptance " + rid, X.acceptance_requirement, c, req)
        if text:
            mine(rid, S.add(11, "acc", text, 1, 3))

    # ---- TBDs (every one, whether or not a requirement cites it)
    for position, tbd in enumerate(ix.tbds):
        fixed.append(S.add(10, "tbd", safe(f"tbd {position}", X.tbd_row, c, tbd), 1, 3))

    # ---- policies
    for position, pol in enumerate(ix.policies):
        kind = sval(pol.get("kind")) or "other"
        chapter = P.policy_chapter(pol)
        if chapter > MAX_CHAPTER:
            chapter = P.DEFAULT_CHAPTER.get(kind, P.FALLBACK_CHAPTER)
        if (chapter, kind) == (9, "nfr"):
            gid = "nfr"
        elif (chapter, kind) == (10, "constraint"):
            gid = "con"
        else:
            gid = f"pol:{chapter}:{kind}"
            if gid not in dyn_seen:
                dyn_seen.add(gid)
                dyn_groups.setdefault(chapter, []).append(gid)
        lo = RANK.get(pol.get("level"), 3) if isinstance(pol.get("level"), str) else 3
        fid = S.add(chapter, gid, safe(f"policy {position}", X.policy_row, c, pol), lo, 3)
        policy_label[fid] = sval(pol.get("id")) or f"policies[{position}]"
        parsed = P.parse_scope(pol.get("scope"))
        if parsed is None:
            c.issue(X.RULE_POLICY_SCOPE, "WARN", f"policies[{position}].scope", "the scope has no valid form: the policy appears in no document", got=T.scalar(pol.get("scope")))
        elif parsed[0] == "all":
            fixed.append(fid)
        elif parsed[0] == "kind":
            pol_kind.setdefault(parsed[1], []).append(fid)
        elif parsed[0] == "node":
            pol_node.setdefault(parsed[1], []).append(fid)
        else:
            pol_req.setdefault(parsed[1], []).append(fid)

    # ---- group order, frame
    group_spec: dict = {}
    order_by_chapter: dict = {}
    for chapter, gid, lo, hi, join in _STATIC_GROUPS:
        group_spec[gid] = {"chapter": chapter, "lo": lo, "hi": hi, "join": join}
        order_by_chapter.setdefault(chapter, []).append(gid)
    for chapter, gids in dyn_groups.items():
        for gid in sorted(gids):
            group_spec[gid] = {"chapter": chapter, "lo": 1, "hi": 3, "join": "\n"}
        base = order_by_chapter.setdefault(chapter, [])
        tail = [g for g in base if g in _TAIL.get(chapter, ())]
        base[:] = [g for g in base if g not in tail] + sorted(gids) + tail
    group_rank = {}
    for chapter in range(1, MAX_CHAPTER + 1):
        for gid in order_by_chapter.get(chapter, []):
            group_rank[gid] = len(group_rank)

    groups_out: dict = {}
    for gid, spec in group_spec.items():
        if gid in _HEAD_KEY:
            text = X.head(c, _HEAD_KEY[gid])
        elif gid.startswith("pol:"):
            text = X.head(c, "policy")
        elif gid == "dg":
            text = T.md_text(T.tr(lang, "skeleton.field.diagrams")) + ":"
        else:
            text = ""
        empty = ""
        if gid == "nfr":
            empty = T.not_authored("ch9.nfr", lang)
        elif gid == "con":
            empty = T.empty_line(lang, "constraints", declared=True)
        elif gid == "tbd":
            empty = T.empty_line(lang, "tbds")
        groups_out[gid] = {"head": text, "empty": empty, "join": spec["join"]}

    frame: dict = {}
    for level in LEVELS:
        r = RANK[level]
        chapters = []
        for n in range(1, MAX_CHAPTER + 1):
            key = _CHAPTER_EMPTY.get(n)
            chapters.append({"n": n, "h": T.chapter_heading(lang, level, n), "note": X.chapter_note(c, level, n),
                             "empty": T.empty_line(lang, key) if key else T.NONE_IN_SCOPE,
                             "groups": [g for g in order_by_chapter.get(n, []) if group_spec[g]["lo"] <= r <= group_spec[g]["hi"]]})
        frame[level] = {"head": X.frame_head(c, level), "chapters": chapters}

    # ---- final fragment order
    old_order = sorted(range(len(S.items)), key=lambda i: (S.items[i]["chapter"], group_rank[S.items[i]["grp"]], i))
    new_id = {old: new for new, old in enumerate(old_order)}
    items = []
    for new, old in enumerate(old_order):
        it = S.items[old]
        items.append({"fid": new, "chapter": it["chapter"], "order": new, "level": LEVELS[it["lo"] - 1], "grp": it["grp"], "md": it["md"]})
    span = {new_id[i]: (S.items[i]["lo"], S.items[i]["hi"]) for i in range(len(S.items))}

    # ---- closures
    def put(acc: dict, fids, gate: int = 0):
        for old in fids:
            fid = new_id[old]
            lo, hi = span[fid]
            acc[fid] = acc.get(fid, 0) | _mask(lo, hi, gate)

    def ev_of(rec, gate):
        return [ev_frag[e] for e in str_list(rec.get("evidenceIds")) if e in ev_frag], gate

    pol_for_node: dict = {}
    for nid in node_ids:
        found = list(pol_node.get(nid, ()))
        found += pol_kind.get(sval(ix.node[nid].get("kind")), ())
        pol_for_node[nid] = found
    units_of_node: dict = {}
    for fid, covered in unit_frag:
        for nid in covered:
            units_of_node.setdefault(nid, []).append(fid)

    def fam_node(acc: dict, nid: str):
        if nid not in ix.node or nid not in node_ids:
            return
        put(acc, own.get(nid, ()))
        put(acc, pol_for_node.get(nid, ()))
        put(acc, units_of_node.get(nid, ()), node_ev_gate[nid])
        rec_ids, gate = ev_of(ix.node[nid], node_ev_gate[nid])
        put(acc, rec_ids, gate)
        machine = ix.machine.get(nid)
        if machine is not None:
            rec_ids, gate = ev_of(machine, node_ev_gate[nid])
            put(acc, rec_ids, gate)

    units_of_edge: dict = {}
    for fid, covered_edges in unit_edges:
        for eid in covered_edges:
            units_of_edge.setdefault(eid, []).append(fid)

    def fam_edge(acc: dict, eid: str):
        if eid not in ix.edge or eid not in edge_ids:
            return
        put(acc, own.get(eid, ()))
        put(acc, units_of_edge.get(eid, ()), edge_ev_gate[eid])  # a diagram that covers no node is linked through its edges
        rec_ids, gate = ev_of(ix.edge[eid], edge_ev_gate[eid])
        put(acc, rec_ids, gate)

    def fam_case(acc: dict, cid: str):
        if cid not in ix.case or cid not in case_ids:
            return
        put(acc, own.get(cid, ()))
        put(acc, *ev_of(ix.case[cid], 0)[:1])

    def fam_req(acc: dict, rid: str):
        if rid not in ix.requirement or rid not in req_ids:
            return
        put(acc, own.get(rid, ()))
        put(acc, pol_req.get(rid, ()))
        put(acc, *ev_of(ix.requirement[rid], 0)[:1])

    elements: list = []
    closures: dict = {}

    def element(eid: str, builder):
        acc: dict = closures.setdefault(eid, {})
        if eid not in elements:
            elements.append(eid)
        builder(acc)

    for nid in node_ids:
        def build_node(acc, nid=nid):
            fam_node(acc, nid)
            for eid in ix.incident_edge_ids(nid):
                fam_edge(acc, eid)
            for cid in c.cases_by_node.get(nid, ()):
                fam_case(acc, cid)
            for rid in c.req_by_node.get(nid, ()):
                fam_req(acc, rid)
        element(nid, build_node)
    for eid in edge_ids:
        def build_edge(acc, eid=eid):
            fam_edge(acc, eid)
            for end in (sval(ix.edge[eid].get("sourceId")), sval(ix.edge[eid].get("targetId"))):
                fam_node(acc, end)
        element(eid, build_edge)
    for cid in case_ids:
        def build_case(acc, cid=cid):
            case = ix.case[cid]
            fam_case(acc, cid)
            for nid in c.participants.get(cid, ()):
                fam_node(acc, nid)
            for eid in ix.case_bound_edges(case):
                fam_edge(acc, eid)
            for rid in str_list(case.get("requirementIds")):
                fam_req(acc, rid)
                for nid in str_list(ix.requirement.get(rid, {}).get("nodeIds")):
                    fam_node(acc, nid)
        element(cid, build_case)
    cases_citing: dict = {}
    for cid in case_ids:
        for rid in str_list(ix.case[cid].get("requirementIds")):
            cases_citing.setdefault(rid, []).append(cid)
    for rid in req_ids:
        def build_req(acc, rid=rid):
            req = ix.requirement[rid]
            fam_req(acc, rid)
            for nid in str_list(req.get("nodeIds")):
                fam_node(acc, nid)
            for eid in str_list(req.get("edgeIds")):
                fam_edge(acc, eid)
            for cid in cases_citing.get(rid, ()):
                fam_case(acc, cid)
        element(rid, build_req)
    for mid in ix.machine:
        if mid not in ix.node and mid in own:
            def build_machine(acc, mid=mid):
                put(acc, own[mid])
                put(acc, *ev_of(ix.machine[mid], 0)[:1])
            element(mid, build_machine)

    always_acc: dict = {}
    put(always_acc, fixed)

    # elements whose code span differs between Python and JavaScript are dropped from the target list only
    kept = []
    for eid in elements:
        if _clean_id(eid):
            kept.append(eid)
        else:
            c.issue(X.RULE_ELEMENT_ID, "WARN", "element", "an id with control characters cannot be selected", got=eid)
    pack = lambda acc: sorted(fid * (1 << SHIFT) + mask for fid, mask in acc.items() if mask)  # noqa: E731
    closure_out = {eid: pack(closures[eid]) for eid in kept}

    docset = {
        "schema": SCHEMA,
        "fragments": {"language": lang, "modelSha8": ix.sha8(), "unselected": T.md_text(T.tr(lang, "skeleton.doc_info.unselected")),
                      "items": items, "frame": frame, "groups": groups_out, "always": pack(always_acc), "elements": kept},
        "closures": closure_out,
        "issues": c.issues,
    }
    docset["stats"] = _stats(docset)
    docset["coverage"] = _coverage(c, docset, own, ev_frag, node_ids, edge_ids, case_ids, req_ids, new_id, policy_label, unit_label)
    return docset


def _stats(ds: dict) -> dict:
    items = ds["fragments"]["items"]
    md_bytes = sum(len(i["md"].encode("utf-8")) for i in items)
    per_chapter: dict = {}
    for i in items:
        per_chapter[i["chapter"]] = per_chapter.get(i["chapter"], 0) + len(i["md"].encode("utf-8"))
    dump = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))  # noqa: E731
    return {"fragments": len(items), "elements": len(ds["closures"]), "mdBytes": md_bytes,
            "fragmentsJsonBytes": dump(ds["fragments"]), "closuresJsonBytes": dump(ds["closures"]),
            "bytesPerChapter": {str(k): per_chapter[k] for k in sorted(per_chapter)}}


def _coverage(c, ds: dict, own: dict, ev_frag: dict, node_ids, edge_ids, case_ids, req_ids, new_id, policy_label: dict, unit_label: dict) -> dict:
    """Elements that no document of the deepest level can reach (reported, never silent)."""
    ix = c.ix
    reached: set = set()
    deep = BIT[3]
    for x in ds["fragments"]["always"]:
        if x & deep:
            reached.add(x >> SHIFT)
    for packed in ds["closures"].values():
        for x in packed:
            if x & deep:
                reached.add(x >> SHIFT)
    missing: dict = {}

    def check(category: str, key, fids):
        if not any(new_id[f] in reached for f in fids):
            missing.setdefault(category, []).append(key)

    for nid in node_ids:
        check("nodes", nid, own.get(nid, ()))
    for eid in edge_ids:
        check("edges", eid, own.get(eid, ()))
    for cid in case_ids:
        check("cases", cid, own.get(cid, ()))
    for rid in req_ids:
        check("requirements", rid, own.get(rid, ()))
    for mid in ix.machine:
        check("machines", mid, own.get(mid, ()))
    for eid in ix.evidence:
        check("evidences", eid, [ev_frag[eid]] if eid in ev_frag else [])
    for tid in ix.test:
        cited = any(tid in str_list(r.get("tstIds")) for r in ix.requirements) or \
            any(tid in str_list(k.get("linkedTestIds")) for k in ix.cases)
        if not cited:
            missing.setdefault("tests", []).append(tid)
    unreached_policies = [label for fid, label in policy_label.items() if new_id[fid] not in reached]
    if unreached_policies:
        missing["policies"] = unreached_policies
    unreached_units = [label for fid, label in unit_label.items() if new_id[fid] not in reached]
    if unreached_units:
        missing["diagrams"] = unreached_units
    # interventions are 5D simulation inputs: they belong to the engine lane's output, not to the structure-only chain
    outside = [sval(iv.get("interventionId")) for iv in ix.interventions if sval(iv.get("interventionId"))]
    return {"unreached": missing, "notInDocuments": {"interventions": outside, "reason": "5D material (engine lane), outside the structure-only chain"}}
