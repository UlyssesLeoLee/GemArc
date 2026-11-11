"""The planner: how units become pages (design A3 sections 2.3 and 2.4).

Only the Markdown is planned (the HTML twin is a post-processing of it and never changes a boundary). The room for the body of a page is
``cap_md[class] - envelope``, where the envelope is the real frame renderer's size for the worst-case widths (``reading.api.content_capacity``);
a unit that does not fit one page is cut into parts at row seams (``blocks.split_unit``) and every part gets a page of its own; the other
units are packed in order, rooms (seams) first:

    for each seam: put it on the open page when it fits; else close the open page when it is at least 60 percent full; else
    put the units of the seam one by one, closing the page whenever the next one does not fit.

The planner measures real rendered lines, so nothing is an estimate. Small projects fold (design 2.4): everything on one page ``A01``
when it fits; a dimension on one page ``P1D``/``P2D`` when its families fit together.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import blocks as B
from .api import content_capacity
from .compose import compose_size
from .families import DIM_FAMILIES, ORDER, Family

FILL = 0.6
WIDE_FAMILIES = ("A", "P")


@dataclass
class Page:
    pid: str = ""                       # bare page id, assigned by ``assign``
    family: str = ""                    # S D M W R T E C Q, or A, P
    dim: str | None = None
    cls: str = "page"
    blocks: list = field(default_factory=list)      # [(Family, [Unit])]: one block, or several on A/P pages
    shift: int = 0
    index: int = 1
    count: int = 1
    part: tuple | None = None           # (i, n) when the page holds one part of a longer unit
    fold: str = ""                      # ``1D``, ``2D`` or ``3Q`` on a page ``P1D``, ``P2D``, ``P3Q``
    unit_key: str = ""

    @property
    def units(self) -> list:
        return [u for _fam, units in self.blocks for u in units]


@dataclass
class Plan:
    pages: list = field(default_factory=list)
    families: dict = field(default_factory=dict)
    overflow: list = field(default_factory=list)    # (family, unit key): an atom larger than a page
    splits: list = field(default_factory=list)      # (family, unit key, parts): a unit that needs more than three pages (an INFO finding)
    mode: str = "split"                              # "single" | "folded" | "split"
    folded: list = field(default_factory=list)       # ["P1D", "P2D", "P3Q"]

    def family_pages(self, key: str) -> list:
        return [p for p in self.pages if p.family == key]


def size_of(lang: str, fam: Family, units: list, shift: int = 0) -> int:
    return B.body_bytes(B.render_body(lang, fam.sections, units, shift).lines)


def _seams(fam: Family, units: list) -> list:
    """Groups of consecutive units of one seam (rooms); a part of a longer unit is a seam of its own."""
    seams: list = []
    for unit in units:
        if unit.part is not None or not fam.seamed:
            seams.append([unit])
        elif seams and not seams[-1][0].part and fam.seamed and seams[-1][0].seam == unit.seam:
            seams[-1].append(unit)
        else:
            seams.append([unit])
    return seams


def plan_family(lang: str, fam: Family, cls: str, plan: Plan) -> list:
    """The pages of one family (lists of units; a part is alone on its page)."""
    fc = f"{fam.key}:{cls}"
    content = content_capacity(fc, lang)
    cont_cap = content_capacity(fc, lang, True)
    units: list = []
    for unit in fam.units:
        if size_of(lang, fam, [unit]) <= content:
            units.append(unit)
            continue
        parts = B.split_unit(unit, lang, fam.sections, cont_cap)
        if len(parts) == 1:
            parts[0].part = (1, 1)
        if len(parts) > 3:
            plan.splits.append((fam.key, unit.key, len(parts)))
        for part in parts:
            if size_of(lang, fam, [part]) > cont_cap:
                plan.overflow.append((fam.key, part.key))
        units.extend(parts)

    def fits(candidate: list) -> bool:
        return size_of(lang, fam, candidate) <= content

    pages: list = []
    cur: list = []
    for seam in _seams(fam, units):
        if seam[0].part is not None:
            if cur:
                pages.append(cur)
                cur = []
            pages.append(list(seam))
            continue
        if fits(cur + seam):
            cur += seam
            continue
        if cur and size_of(lang, fam, cur) >= FILL * content:
            pages.append(cur)
            cur = []
            if fits(seam):
                cur = list(seam)
                continue
        for unit in seam:
            if cur and not fits(cur + [unit]):
                pages.append(cur)
                cur = []
            cur.append(unit)
    if cur:
        pages.append(cur)
    return pages


def family_class(lang: str, fam: Family, plan: Plan) -> str:
    """``card`` for a family of one small page (``W``, ``Q``), else ``page``."""
    if fam.cls != "card":
        return "page"
    fc = f"{fam.key}:card"
    return "card" if size_of(lang, fam, fam.units) <= content_capacity(fc, lang) else "page"


def best_subset(src, blocks: list, capacity: int) -> list:
    """The families of the 3D-QA group (space, cases, interventions, quality) that share the merged page ``P3Q`` when all of them do not fit one page
    (design 2.4 rule 2, T-R13: every extra family is one more file): the subset of at least two families, in their order, that fits the page with the
    most families, then with the most bytes saved; none when no two fit. The page says which families it holds (its ``含`` line); a family left out has
    pages of its own."""
    best: list = []
    best_key = (0, 0)
    for size in range(len(blocks), 1, -1):
        for combo in itertools.combinations(blocks, size):
            total = compose_size(src, list(combo), 1)
            if total <= capacity and (size, total) > best_key:
                best, best_key = list(combo), (size, total)
        if best:
            break
    return best


FANOUT_BASE = 2
FANOUT_BYTES = 5400


def fanout_bound(src, families: dict) -> int:
    """T-R13 (design 8.2): a project whose family bodies add up to ``C`` bytes has at most ``2 + ceil(C / 5,400)`` pages besides START, the glossary and
    the indexes. ``C`` is the sum of the bodies of the families of facts, each composed on its own as the planner measures it (quality apart)."""
    total = sum(compose_size(src, [(fam, fam.units)], 0) for key, fam in families.items() if key != "Q" and fam.units)
    return FANOUT_BASE + -(-total // FANOUT_BYTES)


def plan_surface(src, families: dict, plan: Plan, partial: bool = False) -> Plan:
    """The whole surface: one page when everything fits, folded dimensions when a dimension fits, else the pages of every family.

    ``partial`` lets the merged page of 3D to QA hold only some of its families when all of them do not fit (``best_subset``). The first plan never
    does; when it has more pages than the fan-out bound (T-R13) the planner plans once more with ``partial`` and keeps the plan that has fewer pages."""
    plan = _plan_surface(src, families, plan, partial)
    if not partial and len(plan.pages) > fanout_bound(src, families):
        again = _plan_surface(src, families, Plan(), True)
        if len(again.pages) < len(plan.pages):
            return again
    return plan


def _plan_surface(src, families: dict, plan: Plan, partial: bool) -> Plan:
    lang = src.lang
    plan.families = families
    present = [key for key in ORDER if key in families]
    quality = families.get("Q")
    everything = [(families[k], families[k].units) for k in present] + ([(quality, quality.units)] if quality else [])
    if everything and compose_size(src, everything, 1) <= content_capacity("A:page", lang):
        plan.mode = "single"
        plan.pages.append(Page(family="A", dim=None, cls="page", blocks=everything, shift=1))
        return plan
    folded_any = False
    for fold in ("1D", "2D", "3Q"):
        keys = DIM_FAMILIES[fold] + (("Q",) if fold == "3Q" else ())
        blocks = [(families[k], families[k].units) for k in keys if k in families]
        if fold == "3Q" and partial and len(blocks) >= 3 and compose_size(src, blocks, 1) > content_capacity("P:page", lang):
            blocks = best_subset(src, blocks, content_capacity("P:page", lang))
        if len(blocks) >= 2 and compose_size(src, blocks, 1) <= content_capacity("P:page", lang):
            plan.pages.append(Page(family="P", dim=fold if fold != "3Q" else None, cls="page", blocks=blocks, shift=1, fold=fold))
            plan.folded.append("P" + fold)
            folded_any = True
    plan.mode = "folded" if folded_any else "split"
    folded_keys = {fam.key for p in plan.pages if p.family == "P" for fam, _u in p.blocks}
    for key in present + (["Q"] if quality else []):
        if key in folded_keys:
            continue
        fam = families[key]
        cls = family_class(lang, fam, plan)
        for units in plan_family(lang, fam, cls, plan):
            part = units[0].part if len(units) == 1 and units[0].part is not None and units[0].part[1] > 1 else None
            plan.pages.append(Page(family=key, dim=fam.dim, cls=cls, blocks=[(fam, units)], shift=0, part=part, unit_key=units[0].key if part else ""))
    return plan


def assign(plan: Plan) -> Plan:
    """Page ids: ``<family><nn>`` from 01 in the order of the families (two digits, three above 99); ``A01``, ``P1D``, ``P2D``, ``Q01``."""
    from . import names as N

    counters: dict = {}
    for family in ORDER + ("Q",):
        pages = plan.family_pages(family)
        for n, page in enumerate(pages, start=1):
            page.pid = N.page_id(family, n)
            page.index, page.count = n, len(pages)
    for page in plan.pages:
        if page.family == "A":
            page.pid = "A01"
        elif page.family == "P":
            page.pid = "P" + page.fold
    return plan
