"""The page families: how the homes of a bundle become units and sections (design A3 section 2.2).

Each ``build_<family>`` returns a ``Family``: its sections (what a page of the family can print, in order) and its units (what never splits
across pages). Everything printed is a fragment of the bundle printed as it is, or a *view* rendered from the model (inbound rows, the table of
elements a requirement is realised by, the test table, the room table): a view carries ``role=view:*`` in ``ids.tsv`` and is never the home
of a fact. Cross-references are placeholders resolved when the page numbers are known (``blocks.ref``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..derive import docs as X
from ..derive import i18n as T
from ..derive import policy as P
from . import blocks as B
from .blocks import Item, Section, Seg, Unit, make_item, make_split_item, ref
from .source import DATA_POLICY, Source
from .text import tx, tx_list

NAME_CLIP = 24
HOME = "home"


@dataclass
class Family:
    key: str
    dim: str
    sections: list
    units: list
    order: str = ""                  # the key of the text that names the order of the units ("space", "id", ...)
    cls: str = "page"
    main: str = ""                   # the section whose items the "does not cover" line counts
    seamed: bool = False             # the planner keeps the units of one seam together when they fit
    notes: list = field(default_factory=list)

    def section(self, key: str) -> Section:
        for sec in self.sections:
            if sec.key == key:
                return sec
        raise KeyError(key)


# ---------------------------------------------------------------------------------------------------- small helpers

def clip_name(text: str) -> str:
    """A display name of at most ``NAME_CLIP`` characters (longer: cut and ``…``), Markdown-safe; an empty name is ``[N/A]``."""
    text = T.clean(text) if isinstance(text, str) else ""
    if not text:
        return T.na()
    if len(text) > NAME_CLIP:
        text = text[:NAME_CLIP - 1] + "…"
    return T.md_text(text)


def code(value) -> str:
    return T.code(value) or T.na()


def row(cells) -> str:
    return B.join_cells(cells)


def table_lines(header: str) -> tuple:
    return tuple(header.split("\n"))


def th(lang: str, key: str) -> tuple:
    """The two header lines of a table whose columns are the list ``read.th.<key>``."""
    return tuple(T.md_table([T.md_text(h) for h in tx_list(lang, "th." + key)], []))


def label(lang: str, key: str) -> str:
    return tx(lang, "sec." + key)


_CUTS = tuple(sorted({w for _lang in ("zh-CN", "en-US") for w in tx_list(_lang, "label.cut")}))


def card_label(text: str) -> str:
    """The label the scope card counts a section under: the heading label without a parenthesised qualifier (``Policies (chapter 3 · data)``)."""
    for cut in _CUTS:
        text = text.split(cut)[0]
    return text


def sec(lang: str, key: str, kind: str, base: int, *, header=(), always: bool = False, empty: str = "", dedupe: bool = False, text: str = "") -> Section:
    lab = text or label(lang, key)
    return Section(key, kind, base, lab, card_label(lab), tuple(header), always, empty, dedupe)


#: the diagrams that are drawn from a run (only for items the ledger shows): they belong to the case and pair pages, not to every node they touch
RUN_DIAGRAMS = ("sequence", "staterun")


def diagram_lines(src: Source, covered: str, wanted_ids, skip=()) -> dict:
    """``{element id: [Item]}``: for every diagram unit of the bundle, one reference line per covered element of the kind ``covered`` (``nodeIds``,
    ``caseIds``); the units whose type is in ``skip`` are left out. The link target is the file name ``build_docs`` writes; the text says what it covers."""
    out: dict = {}
    lang = src.lang
    wanted = set(wanted_ids)
    for unit in src.bundle.get("diagrams") or []:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or unit.get("type") in skip:
            continue
        covers = unit.get("covers") if isinstance(unit.get("covers"), dict) else {}
        ids = [i for i in covers.get(covered, []) if isinstance(i, str)]
        mine = [i for i in ids if i in wanted]
        if not mine:
            continue
        name = T.dg_id(unit["id"]) + ".mmd"
        what = tx(lang, "diagram.covers", n=len(ids), what=tx(lang, "diagram." + covered))
        line = "- [" + T.md_text(T.clip(unit.get("title") or unit["id"], 60)) + "](" + B.ROOT + "diagrams/" + name + ") " + what
        for element in mine:
            out.setdefault(element, []).append(Item([line], {}, [], None, "diagram:" + unit["id"]))
    return out


# ---------------------------------------------------------------------------------------------------- S: structure

def build_s(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    node_home = {h.key: h for h in src.by_group("n.DD")}
    out_rows: dict = {}
    orphans: list = []
    for h in src.by_group("e.DD"):
        edge = ix.edge.get(h.key)
        source = P.sval(edge.get("sourceId")) if edge else ""
        (out_rows.setdefault(source, []) if source in node_home else orphans).append(h)
    port_rows: dict = {}
    for h in src.by_group("ports"):
        port_rows.setdefault(h.key, []).append(h)
    inbound: dict = {}
    for edge in ix.edges:
        eid, target = P.sval(edge.get("id")), P.sval(edge.get("targetId"))
        if eid and target in node_home:
            inbound.setdefault(target, []).append(eid)
    shown = [nid for nid in src.node_order if nid in node_home]
    diagrams = diagram_lines(src, "nodeIds", shown, RUN_DIAGRAMS)
    units: list = []
    for nid in shown:
        home = node_home[nid]
        in_ids = sorted(set(inbound.get(nid, [])))
        block = make_item(home.lines + ["- " + tx(lang, "s.inbound", k=len(in_ids))], lang, {1: (nid, "node", HOME)}, nid)
        bands = [Seg("nodes", [block])]
        rows = [make_item([h.md], lang, {0: (h.key, "edge", HOME)}, h.key) for h in out_rows.get(nid, [])]
        if rows:
            bands.append(Seg("out", rows))
        ports = [make_item([line], lang, None, nid) for h in port_rows.get(nid, []) for line in h.lines if line.strip()]
        if ports:
            bands.append(Seg("ports", ports))
        views = []
        for eid in in_ids:
            edge = ix.edge[eid]
            source = P.sval(edge.get("sourceId"))
            where = ref("node:" + source) if source in node_home else T.na()
            views.append(make_item([row([code(nid), code(eid), code(source), clip_name(src.node_name(source)), where])], lang,
                                   {0: (eid, "edge", "view:inbound")}, eid))
        if views:
            bands.append(Seg("in", views))
        if nid in diagrams:
            bands.append(Seg("diagrams", diagrams[nid]))
        units.append(Unit(nid, "node", src.node_name(nid) or nid, bands, src.room_of(nid)))
    if orphans:
        rows = [make_item([h.md], lang, {0: (h.key, "edge", HOME)}, h.key) for h in orphans]
        units.append(Unit("~edges", "edge", tx(lang, "s.orphans"), [Seg("out", rows)], "~"))
    sections = [
        sec(lang, "nodes", "blocks", 2),
        sec(lang, "out", "table", 3, header=table_lines(src.groups["e.DD"]["head"])),
        sec(lang, "ports", "table", 3, header=table_lines(src.groups["ports"]["head"])) if "ports" in src.groups else None,
        sec(lang, "in", "table", 3, header=th(lang, "inbound")),
        sec(lang, "diagrams", "bullets", 3, dedupe=True),
    ]
    return Family("S", "2D", [s for s in sections if s is not None], units, "space", "page", "nodes", True)


# ---------------------------------------------------------------------------------------------------- D, M: data and machines

def build_d(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    rows_policy = src.by_group(DATA_POLICY)
    entities = sorted(src.by_group("ent.DD"), key=lambda h: (src.node_pos.get(h.key, 10 ** 9), h.fid))
    units: list = []
    for h in entities:
        item = make_split_item(h.md, lang, {1: (h.key, "entity", HOME)}, None, h.key, tx(lang, "label.columns"))
        units.append(Unit(h.key, "entity", src.node_name(h.key) or h.key, [Seg("entities", [item])]))
    for n, h in enumerate(rows_policy, start=1):
        units.append(Unit(f"policy:{n}", "policy", tx(lang, "kind.policy"), [Seg("datapol", [make_item([h.md], lang, None, f"policy:{n}")])]))
    sections = [sec(lang, "entities", "blocks", 2),
                sec(lang, "datapol", "table", 3, header=table_lines(src.groups[DATA_POLICY]["head"]) if rows_policy else ())]
    return Family("D", "2D", sections, units, "space", "page", "entities")


def build_m(src: Source) -> Family:
    lang, ix = src.lang, src.ix

    def pos(h):
        machine = ix.machine.get(h.key) or {}
        owner = P.sval(machine.get("nodeId")) or h.key
        return (src.node_pos.get(owner, 10 ** 9), h.fid)

    units: list = []
    for h in sorted(src.by_group("mach"), key=pos):
        item = make_split_item(h.md, lang, {1: (h.key, "machine", HOME)}, lambda cells, n: _first_code_mark(cells, "transition"), h.key,
                               tx(lang, "label.transitions"))
        units.append(Unit(h.key, "machine", h.lines[0].lstrip("# ").strip() or h.key, [Seg("machines", [item])]))
    return Family("M", "2D", [sec(lang, "machines", "blocks", 2)], units, "space", "page", "machines")


def _first_code_mark(cells, kind: str):
    """The mark of a table row whose first cell is the code span of an id."""
    if not cells:
        return None
    text = cells[0]
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return (text[1:-1].replace("\\|", "|"), kind, HOME)
    return None


# ---------------------------------------------------------------------------------------------------- C: cases (and the results of the sealed ones)

def build_c(src: Source) -> Family:
    """The case blocks (structure: events, the author's expectation) and, for a case the ledger calls sealed or confirmed, its result block
    (``results.case_result``); every other case gets one fixed sentence (ORACLE_FIRST: nothing of a result is read for it)."""
    from . import results as RES

    lang, ix = src.lang, src.ix
    case_home = {h.key: h for h in src.by_group("case.DD")}
    acc = {}
    for h in src.by_group("acc"):
        if h.family == "C":
            acc.setdefault(h.key, []).append(h)
    order = [P.sval(c.get("caseId")) for c in ix.cases if P.sval(c.get("caseId")) in case_home]
    diagrams = diagram_lines(src, "caseIds", order)
    units: list = []
    for cid in order:
        h = case_home[cid]

        def event_mark(cells, n, cid=cid):
            return (f"{cid}#{cells[0]}", "event", HOME) if cells and cells[0].isdigit() else None

        item = make_split_item(h.md, lang, {1: (cid, "case", HOME)}, event_mark, cid, tx(lang, "label.events"))
        bands = [Seg("cases", [item])]
        lines = [line for a in acc.get(cid, []) for line in a.lines if line.strip()]
        if lines:
            bands.append(Seg("acc", [make_item(lines, lang, None, cid)]))
        if cid in src.shown_cases:
            head, rows = RES.case_result(src, cid)
            bands.append(Seg("results", [head]))
            if rows:
                bands.append(Seg("final", rows))
        else:
            bands.append(Seg("results", [make_item(["- " + tx(lang, "c.unsealed", id=T.code(cid))], lang, None, cid)]))
        if cid in diagrams:
            bands.append(Seg("diagrams", diagrams[cid]))
        title = P.sval(ix.case[cid].get("title")) or cid
        units.append(Unit(cid, "case", title, bands))
    sections = [sec(lang, "cases", "blocks", 2), sec(lang, "acc", "bullets", 3), sec(lang, "results", "bullets", 3),
                sec(lang, "final", "table", 3, header=th(lang, "final")), sec(lang, "diagrams", "bullets", 3, dedupe=True)]
    return Family("C", "4D", sections, units, "case", "page", "cases")


# ---------------------------------------------------------------------------------------------------- R: requirements

def build_r(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    req_home = {h.key: h for h in src.by_group("req")}
    cov_home = {h.key: h for h in src.by_group("cov")}
    acc = {}
    for h in src.by_group("acc"):
        if h.family == "R":
            acc.setdefault(h.key, []).append(h)
    units: list = []
    for rid in sorted(req_home):
        rec = ix.requirement.get(rid) or {}
        bands = [Seg("req", [make_item([req_home[rid].md], lang, {0: (rid, "requirement", HOME)}, rid)])]
        if rid in cov_home:
            bands.append(Seg("cov", [make_item([cov_home[rid].md], lang, None, rid)]))
        lines = [line for a in acc.get(rid, []) for line in a.lines if line.strip()]
        if lines:
            bands.append(Seg("acc", [make_item(lines, lang, None, rid)]))
        views: list = []
        for nid in P.str_list(rec.get("nodeIds")):
            where = ref("node:" + nid) if nid in src.node_pos else T.na()
            node = ix.node.get(nid)
            kind = src.label("kinds", node.get("kind")) if node else ""
            views.append(make_item([row([code(rid), code(nid), clip_name(src.node_name(nid)), T.md_text(kind) or T.na(), where])], lang, None, rid))
        for eid in P.str_list(rec.get("edgeIds")):
            edge = ix.edge.get(eid)
            where = ref("edge:" + eid) if edge else T.na()
            name = (P.sval(edge.get("label")) or P.sval(edge.get("relation"))) if edge else ""
            views.append(make_item([row([code(rid), code(eid), clip_name(name), tx(lang, "kind.edge"), where])], lang, None, rid))
        if views:
            bands.append(Seg("elements", views))
        units.append(Unit(rid, "requirement", P.sval(rec.get("title")) or rid, bands))
    cited_by_req: dict = {}
    for rid, rec in ix.requirement.items():
        for tid in P.str_list(rec.get("tstIds")):
            cited_by_req[tid] = cited_by_req.get(tid, 0) + 1
    cited_by_case: dict = {}
    for cid, rec in ix.case.items():
        for tid in P.str_list(rec.get("linkedTestIds")):
            cited_by_case[tid] = cited_by_case.get(tid, 0) + 1
    for tid in sorted(ix.test):
        title = T.md_text(T.clean(P.sval(ix.test[tid].get("title")))) or T.na()
        cells = [code(tid), title, str(cited_by_req.get(tid, 0)), str(cited_by_case.get(tid, 0))]
        units.append(Unit(tid, "test", P.sval(ix.test[tid].get("title")) or tid, [Seg("tests", [make_item([row(cells)], lang, {0: (tid, "test", HOME)}, tid)])],
                          "tests"))
    sections = [
        sec(lang, "req", "table", 3, header=table_lines(src.groups["req"]["head"])),
        sec(lang, "cov", "table", 3, header=table_lines(src.groups["cov"]["head"]) if "cov" in src.groups else ()),
        sec(lang, "acc", "bullets", 3),
        sec(lang, "elements", "table", 3, header=th(lang, "elements")),
        sec(lang, "tests", "table", 3, header=th(lang, "tests")),
    ]
    return Family("R", "1D", sections, units, "id", "page", "req")


# ---------------------------------------------------------------------------------------------------- T: open items and policies, E: evidence

def build_t(src: Source) -> Family:
    lang = src.lang
    plain = {"tbd": "tbd", "con": "con", "nfr": "nfr"}
    dynamic = sorted({h.grp for h in src.homes if h.family == "T" and h.grp not in plain})
    order = ["tbd", "con", "nfr"] + dynamic
    sections: list = []
    units: list = []
    for gid in order:
        homes = src.by_group(gid)
        group = src.groups.get(gid, {})
        header = table_lines(group["head"]) if group.get("head") else ()
        if gid in plain:
            lab = label(lang, gid)
        else:
            _p, chapter, kind = gid.split(":", 2)
            lab = tx(lang, "sec.pol", chapter=chapter, kind=kind)
        sections.append(Section(gid, "table", 3, lab, card_label(lab), header, False, group.get("empty") or ""))
        if not homes:
            text = group.get("empty") or ""
            units.append(Unit("empty:" + gid, "policy", lab, [Seg(gid, [Item([text] if text else [T.na()], {}, [], None, B.EMPTY_KEY)])]))
        for n, h in enumerate(homes, start=1):
            mark = {0: (h.key, "tbd", HOME)} if gid == "tbd" and h.key else None
            units.append(Unit(h.key or f"{gid}:{n}", "tbd" if gid == "tbd" else "policy", lab, [Seg(gid, [make_item([h.md], lang, mark, h.key or "")])]))
    return Family("T", "1D", sections, units, "id", "page", "tbd")


def build_e(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    ev_home = {h.key: h for h in src.by_group("ev")}
    ids = sorted(set(ev_home) | set(ix.evidence))
    units: list = []
    for eid in ids:
        text = ev_home[eid].md if eid in ev_home else X.evidence_row(src.xctx(), eid)
        units.append(Unit(eid, "evidence", eid, [Seg("ev", [make_item([text], lang, {0: (eid, "evidence", HOME)}, eid)])]))
    header = table_lines(src.groups["ev"]["head"]) if "ev" in src.groups else tuple(T.md_table(T.tr_list(src.lang, "skeleton.th.evidence_ref"), []))
    return Family("E", "1D", [sec(lang, "ev", "table", 3, header=header)], units, "id", "page", "ev")


# ---------------------------------------------------------------------------------------------------- W: space

def build_w(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    nodes_in_room: dict = {}
    for nid in src.node_order:
        nodes_in_room.setdefault(src.room_of(nid), []).append(nid)
    floors_of: dict = {}
    for f in ix.floors:
        floors_of.setdefault(P.sval(f.get("buildingId")), []).append(f)
    rooms_of: dict = {}
    for r in ix.rooms:
        rooms_of.setdefault(P.sval(r.get("floorId")), []).append(r)

    def where(nids) -> str:
        if not nids:
            return "-"
        return ref(B.RANGE_PREFIX + "node:" + nids[0] + "|node:" + nids[-1])

    units: list = []
    for b in ix.buildings:
        bid = P.sval(b.get("id"))
        if not bid:
            continue
        floors = [f for f in floors_of.get(bid, []) if P.sval(f.get("id"))]
        room_rows, floor_rows = [], []
        building_nodes = 0
        for f in floors:
            fid = P.sval(f.get("id"))
            rooms = [r for r in rooms_of.get(fid, []) if P.sval(r.get("id"))]
            floor_nodes = 0
            for r in rooms:
                rid = P.sval(r.get("id"))
                nids = nodes_in_room.get(rid, [])
                floor_nodes += len(nids)
                room_rows.append(make_item([row([code(rid), code(fid), clip_name(r.get("name")), str(len(nids)), where(nids)])], lang,
                                           {0: (rid, "room", HOME)}, rid))
            level = f.get("level")
            floor_rows.append(make_item([row([code(fid), code(bid), clip_name(f.get("name")), str(level) if isinstance(level, int) and not isinstance(level, bool) else T.na(),
                                              str(len(rooms)), str(floor_nodes)])], lang, {0: (fid, "floor", HOME)}, fid))
            building_nodes += floor_nodes
        brow = make_item([row([code(bid), clip_name(b.get("name")), str(len(floors)), str(len(room_rows)), str(building_nodes)])], lang,
                         {0: (bid, "building", HOME)}, bid)
        bands = [Seg("buildings", [brow])]
        if floor_rows:
            bands.append(Seg("floors", floor_rows))
        if room_rows:
            bands.append(Seg("rooms", room_rows))
        units.append(Unit(bid, "building", P.sval(b.get("name")) or bid, bands))
    unplaced = [nid for nid in src.node_order if not src.room_of(nid) or src.room_of(nid) not in ix.room]
    if unplaced:
        line = "- " + tx(lang, "w.unplaced", n=len(unplaced), where=where(unplaced))
        units.append(Unit("~unplaced", "building", tx(lang, "sec.unplaced"), [Seg("unplaced", [make_item([line], lang, None, "~unplaced")])]))
    sections = [sec(lang, "buildings", "table", 3, header=th(lang, "buildings")),
                sec(lang, "floors", "table", 3, header=th(lang, "floors")),
                sec(lang, "rooms", "table", 3, header=th(lang, "rooms")),
                sec(lang, "unplaced", "bullets", 3)]
    return Family("W", "3D", sections, units, "space", "card", "buildings")


# ---------------------------------------------------------------------------------------------------- registry

def build_v(src: Source) -> Family:
    """The intervention blocks and, for the sealed pairs, their comparison results (``results.build_v``)."""
    from . import results as RES

    return RES.build_v(src)


BUILDERS = {"S": build_s, "D": build_d, "M": build_m, "W": build_w, "R": build_r, "T": build_t, "E": build_e, "C": build_c, "V": build_v}
#: print order of the families inside a dimension (design 2.2 table)
DIM_FAMILIES = {"1D": ("R", "T", "E"), "2D": ("S", "D", "M"), "3D": ("W",), "4D": ("C",), "5D": ("V",), "3Q": ("W", "C", "V")}
ORDER = ("R", "T", "E", "S", "D", "M", "W", "C", "V")
