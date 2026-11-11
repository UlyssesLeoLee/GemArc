"""What the page builder reads from a bundle: the fragments of the deepest document, their homes, the model's placement order.

``reading.build`` accepts only ``bundle`` (the ten-key ``window.__ATLAS__`` dict ``build.make_bundle`` returns) and ``sealed_meta``; every
fact on a page comes from there (design A3 2.10). This module turns the bundle into the plain tables the families use.

Homes (design 2.2, D-3, audit N0). The universe of the reading surface is the set of fragments of the whole-model DD document (``always`` plus
the closures of every element at the DD level) minus the structural groups, plus the ``req`` group (requirement rows exist only in the REQ
document: audit finding 1). Every fragment of the universe belongs to exactly one family by its group; an unknown group is an error, never a
catch-all. The DD closure is taken from ``bundle["closures"]`` (the same masks the viewer uses), never from ``item.level``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..derive import assemble as A
from ..derive import docs as X
from ..derive import i18n as T
from ..derive import policy as P
from ..vocab import load_profile
from . import names as N

#: groups of the document skeleton: they are not facts and have no home on any page
STRUCTURAL = ("c1", "c2", "chmap", "xmap", "sign", "dg")
#: the data-policy group (chapter 6) belongs to the data pages, the other ``pol:*`` groups to the open-items pages
DATA_POLICY = "pol:6:data"
EXCLUDED_GROUP = "ifc"
CODE = re.compile(r"`([^`\n]+)`")
BLOCK_GROUPS = ("n.DD", "ent.DD", "case.DD", "mach")
ROW_GROUPS = ("e.DD", "ports", "ev", "req", "cov", "tbd", "con", "nfr", "ifc")

FAMILY_OF = {"n.DD": "S", "e.DD": "S", "ports": "S", "ent.DD": "D", "mach": "M", "case.DD": "C", "req": "R", "cov": "R", "ev": "E",
             "tbd": "T", "con": "T", "nfr": "T", DATA_POLICY: "D"}


def first_code(line: str):
    found = CODE.search(line)
    return found.group(1).replace("\\|", "|") if found else None


def frag_key(grp: str, md: str):
    """The element a fragment is about: the first code span of its first line (rows) or of its second line (blocks)."""
    lines = md.split("\n")
    if grp in BLOCK_GROUPS:
        return first_code(lines[1]) if len(lines) > 1 else None
    if grp in ("acc",) or grp in ROW_GROUPS:
        return first_code(lines[0]) if lines else None
    return None


def _id_list(value) -> list:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def sealed_meta_from_reply(reply, verdicts=None) -> dict:
    """The ``sealed_meta`` of ``reading.build`` from the reply of ``oracle.sealed_runs`` (design D-32): ``withheld`` as it is, ``states`` for every
    returned baseline (``case:<id>``) and pair (``pair:<intervention>|<case>``), and ``verdicts`` (the additive key of the reply, or the argument).
    One call of the oracle gives all three, so a page never mixes two ledger moments. Nothing is computed here."""
    reply = reply if isinstance(reply, dict) else {}
    states: dict = {}
    for cid, entry in (reply.get("baselines") or {}).items():
        if isinstance(entry, dict) and isinstance(entry.get("state"), str):
            states[f"case:{cid}"] = entry["state"]
    for pair in reply.get("pairs") or []:
        if isinstance(pair, dict) and isinstance(pair.get("state"), str):
            states[f"pair:{pair.get('interventionId')}|{pair.get('caseId')}"] = pair["state"]
    meta: dict = {"withheld": reply.get("withheld") if isinstance(reply.get("withheld"), dict) else {"cases": [], "interventions": []}, "states": states}
    found = verdicts if verdicts is not None else reply.get("verdicts")
    if isinstance(found, dict):
        meta["verdicts"] = found
    return meta


@dataclass
class Home:
    fid: int
    grp: str
    key: str | None
    family: str | None          # S D M C R T E, or None when excluded
    md: str
    lines: list = field(default_factory=list)


class Source:
    def __init__(self, bundle: dict, sealed_meta=None):
        self.bundle = bundle
        self.model = bundle["model"]
        self.meta = bundle["meta"]
        self.lang = self.meta.get("primaryLanguage") or "en-US"
        self.tl = N.text_lang(self.lang)
        self.project = self.meta.get("projectId") or "project"
        self.stamp = self.meta.get("modelSha8") or str(self.meta.get("modelSha", ""))[:8]
        self.mode = self.meta.get("mode") or "asis"
        self.profile = load_profile(self.meta.get("profileId") or "software")
        self.ix = P.Index(self.model, self.profile)
        self.sealed_meta = sealed_meta if isinstance(sealed_meta, dict) else {}
        fr = bundle["fragments"]
        self.fr = fr
        self.items = fr["items"]
        self.groups = fr["groups"]
        self.dd_fids = set(A.fragment_ids(bundle, fr["elements"], 2))
        self.homes: list = []
        self.problems: list = []
        self._classify()
        self.node_order = self._node_order()
        self.node_pos = {nid: n for n, nid in enumerate(self.node_order)}
        self.runs = bundle.get("runs") or {"baselines": {}, "pairs": []}
        self._xctx = None
        self._ledger()

    # -- the table of homes
    def _classify(self) -> None:
        ix = self.ix
        for item in self.items:
            grp = item["grp"]
            fid = item["fid"]
            if grp in STRUCTURAL:
                continue
            if fid not in self.dd_fids and grp != "req":
                continue
            md = item["md"]
            if grp == EXCLUDED_GROUP:
                self.homes.append(Home(fid, grp, frag_key(grp, md), None, md))
                continue
            if grp == "acc":
                key = frag_key(grp, md)
                in_case, in_req = key in ix.case, key in ix.requirement
                if in_case and in_req:
                    family = "C" if self._is_case_acceptance(md) else "R"
                else:
                    family = "C" if in_case else ("R" if in_req else None)
                if family is None:
                    self.problems.append(f"fragment {fid} (acc): the element {key!r} is neither a case nor a requirement")
                self.homes.append(Home(fid, grp, key, family, md))
                continue
            if grp.startswith("pol:") and grp != DATA_POLICY:
                self.homes.append(Home(fid, grp, None, "T", md))
                continue
            family = FAMILY_OF.get(grp)
            if family is None:
                self.problems.append(f"fragment {fid}: group {grp!r} has no home family")
                self.homes.append(Home(fid, grp, None, None, md))
                continue
            key = frag_key(grp, md)
            known = {"n.DD": ix.node, "e.DD": ix.edge, "ports": ix.node, "ent.DD": ix.node, "mach": ix.machine, "case.DD": ix.case,
                     "req": ix.requirement, "cov": ix.requirement, "ev": ix.evidence, "tbd": ix.tbd}.get(grp)
            if known is not None and key not in known:
                self.problems.append(f"fragment {fid} ({grp}): the element {key!r} is not in the model")
            self.homes.append(Home(fid, grp, key, family, md))
        for home in self.homes:
            home.lines = home.md.split("\n")

    def _is_case_acceptance(self, md: str) -> bool:
        """An ``acc`` fragment is a case's when it starts with the case template of the language (a requirement's starts with the tests template)."""
        template = T.tr(self.lang, "prompts.acceptance.case_terminal")
        head = template.split("{")[0]
        return bool(head) and md.startswith("- " + head)

    def by_group(self, grp: str) -> list:
        return [h for h in self.homes if h.grp == grp]

    def by_family(self, family: str) -> list:
        return [h for h in self.homes if h.family == family]

    # -- the ledger (design A3 2.10, D-32): what the oracle says about every case and intervention
    def _ledger(self) -> None:
        """Read ``sealed_meta`` once. ``states_known`` / ``withheld_known`` say whether the caller passed the keys at all (a build with no oracle reply
        has neither); a result is shown only for an item that is in ``bundle.runs`` AND that the ledger calls sealed or confirmed AND that is not
        listed as withheld. Everything in ``bundle.runs`` that fails that test is a *stray* (``strays``): it is never printed and RS-115 reports it."""
        meta = self.sealed_meta
        states = meta.get("states")
        self.states_known = isinstance(states, dict)
        self.states = {k: v for k, v in states.items() if isinstance(k, str) and isinstance(v, str)} if self.states_known else {}
        withheld = meta.get("withheld")
        self.withheld_known = isinstance(withheld, dict)
        self.withheld_cases = _id_list(withheld.get("cases")) if self.withheld_known else []
        self.withheld_ivs = _id_list(withheld.get("interventions")) if self.withheld_known else []
        verdicts = meta.get("verdicts")
        self.verdicts_known = isinstance(verdicts, dict)
        self.verdicts = verdicts if self.verdicts_known else {}
        baselines = self.runs.get("baselines") if isinstance(self.runs.get("baselines"), dict) else {}
        pairs = [p for p in (self.runs.get("pairs") or []) if isinstance(p, dict)]
        self.baselines = baselines
        self.pairs = pairs
        ok = ("sealed", "confirmed")
        self.shown_cases = {cid: self.states[f"case:{cid}"] for cid in baselines
                            if self.states.get(f"case:{cid}") in ok and cid not in self.withheld_cases}
        self.shown_pairs = {}
        for pair in pairs:
            key = f"pair:{pair.get('interventionId')}|{pair.get('caseId')}"
            if (self.states.get(key) in ok and pair.get("interventionId") not in self.withheld_ivs and pair.get("caseId") in self.shown_cases):
                self.shown_pairs[(pair["interventionId"], pair["caseId"])] = self.states[key]
        self.stray_cases = sorted(cid for cid in baselines if cid not in self.shown_cases)
        self.stray_pairs = sorted((p.get("interventionId"), p.get("caseId")) for p in pairs
                                  if (p.get("interventionId"), p.get("caseId")) not in self.shown_pairs)

    def case_ids(self) -> list:
        return [P.sval(c.get("caseId")) for c in self.ix.cases if P.sval(c.get("caseId"))]

    def intervention_ids(self) -> list:
        return [P.sval(i.get("interventionId")) for i in self.ix.interventions if P.sval(i.get("interventionId"))]

    def unsealed_cases(self) -> list:
        """Model cases without a shown result, in model order."""
        return [cid for cid in self.case_ids() if cid not in self.shown_cases]

    def unsealed_interventions(self) -> list:
        """Model interventions that have no shown pair at all, in model order."""
        have = {iid for iid, _cid in self.shown_pairs}
        return [iid for iid in self.intervention_ids() if iid not in have]

    # -- model helpers
    def _node_order(self) -> list:
        """Node ids in reading order: (building, floor level, room, node) by the order of the ``space`` arrays; unplaced nodes last."""
        ix = self.ix
        b_pos = {bid: n for n, bid in enumerate(P.sval(b.get("id")) for b in ix.buildings)}
        f_pos = {P.sval(f.get("id")): n for n, f in enumerate(ix.floors)}
        r_pos = {P.sval(r.get("id")): n for n, r in enumerate(ix.rooms)}

        def key(entry):
            n, node = entry
            nid = P.sval(node.get("id"))
            room = ix.room_of(node)
            floor = ix.floor_of(room)
            building = ix.building_of(floor)
            if room is None or floor is None or building is None:
                return (1, 0, 0, 0, 0, n)
            level = floor.get("level")
            level = level if isinstance(level, int) and not isinstance(level, bool) else 0
            return (0, b_pos.get(P.sval(building.get("id")), 0), level, f_pos.get(P.sval(floor.get("id")), 0), r_pos.get(P.sval(room.get("id")), 0), n)

        seen: set = set()
        entries = []
        for n, node in enumerate(ix.nodes):
            nid = P.sval(node.get("id"))
            if nid and nid not in seen:
                seen.add(nid)
                entries.append((n, node))
        return [P.sval(node.get("id")) for _n, node in sorted(entries, key=key)]

    def room_of(self, nid: str) -> str:
        node = self.ix.node.get(nid)
        room = self.ix.room_of(node) if node else None
        return P.sval(room.get("id")) if room else ""

    def node_name(self, nid: str) -> str:
        node = self.ix.node.get(nid)
        return P.sval(node.get("name")) if node else ""

    def xctx(self):
        """The renderer context of ``derive.docs`` (for the rows of evidences no element cites: the same function builds every ``ev`` row)."""
        if self._xctx is None:
            self._xctx = X.Ctx(self.model, self.lang, units=[], profile=self.profile)
        return self._xctx

    def grade_ruler(self) -> list:
        """Trust grades from the highest to the lowest rank, as the pages print them: the documents print the grade id of the profile's
        ``verification`` group (``Implemented``), in every language."""
        items = sorted((i for i in self.bundle["profile"].get("verification", []) if isinstance(i, dict) and isinstance(i.get("id"), str)),
                       key=lambda i: -int(i.get("rank", 0)))
        return [i["id"] for i in items]

    def label(self, group: str, value) -> str:
        return self.profile.label(group, value, self.lang) if isinstance(value, str) and value else ""
