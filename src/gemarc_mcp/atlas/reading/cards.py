"""The scope card of a page: the three lines ``covers``, ``excludes`` and ``howto`` (design A3 2.5, AM-01, AM-14).

Each line is built from parts in priority order and the lowest-priority parts are dropped, whole, until the line fits its byte cap
(``budgets.CARD_FIELD_CAPS``): a card line is never cut in the middle of an item, so the checks can parse it back (RS-122, RS-123).
"""
from __future__ import annotations

import re

from ..derive import i18n as T
from . import names as N
from .envelope import card_caps, format_page_list
from .results import is_verdict_like
from .source import Source
from .text import raw, tx

RANGE_KINDS = ("node", "entity", "machine", "case", "intervention", "requirement", "test", "evidence", "building")
RANGE_CLIP = 20


def utf8(text: str) -> int:
    return len(text.encode("utf-8"))


def merge_counts(sections) -> list:
    """``[(label, n)]`` from the section infos of a page: sections with one label add up, the first appearance fixes the order."""
    out: dict = {}
    for info in sections:
        out[info.label] = out.get(info.label, 0) + info.n
    return list(out.items())


def _clip_id(text: str) -> str:
    return text if len(text) <= RANGE_CLIP else text[:RANGE_CLIP - 1] + "…"


def covers_text(src: Source, family: str, sections, units, continuation: bool = False) -> str:
    """``<label n、label n>（<range>，<order>，<complete or partial>）``; the range and then the order drop out first when the line is too long."""
    lang = src.lang
    cap = card_caps(family if family in N.FAMILIES else "A", continuation)["covers"]
    sep = tx(lang, "covers.sep")
    items = sep.join(tx(lang, "covers.item", label=label, n=n) for label, n in merge_counts(sections))
    spanned = next((s for s in sections if s.span is not None), None)
    flag = tx(lang, "covers.partial", n=spanned.n, m=spanned.total) if spanned else tx(lang, "covers.complete")
    first = next((u for u in units if u.kind in RANGE_KINDS), None)
    last = next((u for u in reversed(units) if u.kind in RANGE_KINDS), None)
    rng = ""
    if first is not None and last is not None:
        rng = tx(lang, "covers.range_one", first=_clip_id(first.key)) if first.key == last.key else \
            tx(lang, "covers.range", first=_clip_id(first.key), last=_clip_id(last.key))
    order = tx(lang, "order." + ORDER_KEY[family]) if family in ORDER_KEY else ""
    for pieces in ([rng, order, flag], [order, flag], [flag]):
        pieces = [p for p in pieces if p]
        text = tx(lang, "covers.line", items=items, rest=tx(lang, "covers.rest_sep").join(pieces))
        if utf8(text) <= cap:
            return text
    return tx(lang, "covers.line", items=items, rest=flag)


ORDER_KEY = {"S": "space", "D": "space", "M": "space", "W": "space", "R": "id", "T": "id", "E": "id", "C": "case", "V": "iv"}


def excludes_text(src: Source, family: str, here: str, summaries: list, continuation: bool = False) -> str:
    """What the page leaves out and where it is: the rest of its own family (page ids), then the other families (a page id each).
    ``summaries`` is ``[{"pid", "family", "fams"}]`` of every page of the surface. Whole items drop out, last first, until the line fits."""
    lang = src.lang
    cap = card_caps(family if family in N.FAMILIES else "A", continuation)["excludes"]
    sep = tx(lang, "range.sep")
    same = [p["pid"] for p in summaries if p["family"] == family and p["pid"] != here and family not in ("A", "P")]
    here_fams = set(next((p["fams"] for p in summaries if p["pid"] == here), [family]))
    others: list = []
    seen: set = set()
    for p in summaries:
        if p["pid"] == here:
            continue
        for key in p["fams"]:
            if key in here_fams or key in seen or key in ("START", "G"):
                continue
            seen.add(key)
            others.append(tx(lang, "excludes.other_item", title=tx(lang, "family." + key), page=N.page_ref(p["pid"])))

    def build(same_text: str, items: list) -> str:
        parts = ([same_text] if same_text else []) + ([tx(lang, "excludes.others", items=sep.join(items))] if items else [])
        return tx(lang, "excludes.line", rest=tx(lang, "excludes.sep").join(parts) or tx(lang, "excludes.nothing"))

    same_text = tx(lang, "excludes.same", pages=format_page_list(same, sep, 400)) if same else ""
    text = build(same_text, others)
    while utf8(text) > cap and others:
        others.pop()
        text = build(same_text, others)
    if utf8(text) > cap and same_text:
        same_text = tx(lang, "excludes.same_count", n=len(same))
        text = build(same_text, others)
    return text


def _grade_pattern(label: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z])" + re.escape(label) + r"(?![A-Za-z])")


def page_markers(src: Source, lines) -> dict:
    """Which legends a page needs: ``na`` (``[N/A]``), ``na_auth`` (a ``[NOT-AUTHORED:...]`` marker), ``grade`` (any grade word)."""
    text = "\n".join(lines)
    ruler = src.grade_ruler()
    return {"na": T.NA in text, "auth": T.NOT_AUTHORED_RE.search(text) is not None,
            "grade": any(_grade_pattern(label).search(text) for label in ruler),
            "verdict": any(is_verdict_like(src.lang, line) for line in lines)}


def howto_text(src: Source, family: str, lines, continuation: bool = False, folded: bool = False) -> tuple:
    """``(text, degraded)``: the legends the page needs (RS-123), then a sentence about the family while there is room.

    ``degraded`` is True when the grade ruler did not fit the line and was replaced by the pointer to ``§G01`` (an L-class degradation)."""
    lang = src.lang
    cap = card_caps(family if family in N.FAMILIES else "A", continuation)["howto"]
    need = page_markers(src, lines)
    sep = tx(lang, "howto.sep")
    ruler = src.grade_ruler()
    parts: list = []
    if need["na"]:
        parts.append(tx(lang, "howto.na"))
    if need["auth"]:
        parts.append(tx(lang, "howto.auth"))
    if need["verdict"]:
        parts.append(tx(lang, "howto.verdict"))
    degraded = False
    if need["grade"] and ruler:
        full = tx(lang, "howto.ruler", ruler=" > ".join(ruler))
        if utf8(sep.join(parts + [full])) <= cap:
            parts.append(full)
        else:
            parts.append(tx(lang, "howto.ruler_ref"))
            degraded = True
    for extra in ([tx(lang, "howto." + family)] if has_text(lang, "howto." + family) else []) + ([tx(lang, "howto.fold")] if folded else []):
        if utf8(sep.join(parts + [extra])) <= cap:
            parts.append(extra)
    return (sep.join(parts) or tx(lang, "howto.plain")), degraded


def has_text(lang: str, key: str) -> bool:
    try:
        raw(lang, key)
        return True
    except KeyError:
        return False
