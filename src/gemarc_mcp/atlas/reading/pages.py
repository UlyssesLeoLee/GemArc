"""Page assembly: a planned page becomes its final text, its manifest record and its ``ids.tsv`` rows (design A3 sections 2.5, 2.7).

The frame (locator lines, scope card, neighbours, sentinel) comes from ``reading.envelope``; the body from ``compose``. Cross-references are
resolved here, once every page has its id. ``ids.tsv`` and ``refs.tsv`` line numbers are taken from the final text, after folding and after
the resolution of placeholders, never from the measuring pass.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from ..derive import i18n as T
from . import blocks as B
from . import names as N
from .cards import covers_text, excludes_text, howto_text, merge_counts
from .compose import compose_body
from .envelope import envelope, finalize
from .results import has_results
from .source import Source
from .text import tx

HOME = "home"


@dataclass
class Rendered:
    pid: str
    path: str
    text: str
    family: str
    kind: str = "page"                  # manifest ``k``
    record: dict = field(default_factory=dict)
    marks: list = field(default_factory=list)       # (id, kind, role, line, hdr, span_first, span_last) absolute, 1-based
    sections: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    head_len: int = 0
    fams: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    content: str = "structure"           # ``sealed`` when the page carries a result block (the frame says so, design 2.5)


def sha8_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


def tok_estimate(text: str, lang: str) -> int:
    """Tokens of a text: table lines at 2.2 bytes per token, other lines at 2.9 (zh-CN) or 3.7 (other languages), the study's lowest ratios."""
    table = other = 0
    for line in text.split("\n"):
        size = len(line.encode("utf-8")) + 1
        if line.startswith("|"):
            table += size
        else:
            other += size
    return math.ceil(table / 2.2 + other / (2.9 if N.text_lang(lang) == "zh-CN" else 3.7))


def rel_start(path: str) -> str:
    depth = path.count("/")
    return "../" * depth + "START.md"


def page_title(src: Source, page) -> str:
    if page.family in ("A", "P"):
        return N.family_title(page.family, src.lang, page.fold or page.dim)
    return N.family_title(page.family, src.lang)


def summaries_of(plan) -> list:
    return [{"pid": p.pid, "family": p.family, "fams": [f.key for f, _u in p.blocks]} for p in plan.pages]


def display_name(src: Source, kind: str, ident: str) -> str:
    """The ``name`` column of ``ids.tsv``: a display name, at most 120 characters."""
    ix = src.ix
    sval = lambda v: v if isinstance(v, str) else ""  # noqa: E731
    name = ""
    if kind == "node" or kind == "entity":
        name = sval((ix.node.get(ident) or {}).get("name"))
    elif kind == "edge":
        edge = ix.edge.get(ident) or {}
        what = sval(edge.get("label")) or src.label("relations", edge.get("relation")) or sval(edge.get("relation"))
        name = f"{sval(edge.get('sourceId'))}>{sval(edge.get('targetId'))} {what}".strip()
    elif kind == "case":
        name = sval((ix.case.get(ident) or {}).get("title"))
    elif kind == "intervention":
        name = next((sval(iv.get("description")) for iv in ix.interventions if sval(iv.get("interventionId")) == ident), "")
    elif kind == "event":
        case, _sep, seq = ident.rpartition("#")
        for event in (ix.case.get(case) or {}).get("events", []):
            if isinstance(event, dict) and str(event.get("seq")) == seq:
                name = f"{sval(event.get('type'))} {sval(event.get('from'))}>{sval(event.get('to'))}".strip()
    elif kind == "requirement":
        name = sval((ix.requirement.get(ident) or {}).get("title"))
    elif kind == "test":
        name = sval((ix.test.get(ident) or {}).get("title"))
    elif kind == "tbd":
        name = sval((ix.tbd.get(ident) or {}).get("title"))
    elif kind == "evidence":
        name = sval((ix.evidence.get(ident) or {}).get("topic"))
    elif kind == "machine":
        machine = ix.machine.get(ident) or {}
        name = sval((ix.node.get(sval(machine.get("nodeId")) or ident) or {}).get("name"))
    elif kind == "transition":
        for machine in ix.machines:
            for tr in machine.get("transitions", []) if isinstance(machine.get("transitions"), list) else []:
                if isinstance(tr, dict) and tr.get("id") == ident:
                    name = f"{sval(tr.get('from'))}>{sval(tr.get('to'))}"
    elif kind in ("room", "floor", "building"):
        name = sval({"room": ix.room, "floor": ix.floor, "building": ix.building}[kind].get(ident, {}).get("name"))
    elif kind == "diagram":
        name = ident
    name = " ".join(T.clean(name).split())
    return name if len(name) <= 120 else name[:119] + "…"


def build_meta(src: Source, page, pages_by_family: dict, summaries: list, body_lines: list, index_pages: dict) -> tuple:
    """``(meta, degraded, families on the page)`` for the envelope of a planned page."""
    lang = src.lang
    path = N.page_path(page.pid)
    continuation = page.part is not None
    fam_key = page.family if page.family in N.FAMILIES else "A"
    markers = [fam.key for fam, _u in page.blocks]
    body = compose_body(src, page.blocks, page.shift)
    sections = body.sections
    if page.family in ("A", "P"):
        main = {fam.key: fam.main for fam, _u in page.blocks}
        sections = [info for info in body.sections if info.key == main.get(getattr(info, "family", ""))]
    covers = covers_text(src, fam_key, sections, page.units, continuation)
    folded = body.fit > 0
    howto, degraded = howto_text(src, fam_key, body_lines, continuation, folded)
    excludes = excludes_text(src, fam_key, page.pid, summaries, continuation)
    content = "sealed" if page.family in ("C", "V", "A", "P") and has_results(lang, body_lines) else "structure"
    meta = {"page": page.pid, "index": page.index, "count": page.count, "cls": page.cls, "lang": lang, "project": src.project, "stamp": src.stamp,
            "content": content, "title": page_title(src, page), "covers": covers, "excludes": excludes, "howto": howto,
            "start": rel_start(path)}
    same = pages_by_family.get(page.family, []) if page.family not in ("A", "P") else []
    at = same.index(page) if page in same else -1
    if at > 0:
        meta["prev"] = (same[at - 1].pid, same[at - 1].pid + ".md")
    if 0 <= at < len(same) - 1:
        meta["next"] = (same[at + 1].pid, same[at + 1].pid + ".md")
    if page.dim and index_pages.get(page.dim):
        meta["up"] = index_pages[page.dim]
    if continuation:
        others = [p.pid for p in same if p is not page and p.unit_key == page.unit_key]
        nxt = same[at + 1].pid if at + 1 < len(same) and same[at + 1].unit_key == page.unit_key else None
        meta["continuation"] = {"unit": page.unit_key, "part": page.part[0], "parts": page.part[1], "others": others, "next": nxt}
    return meta, degraded, markers


def render_page(src: Source, page, plan, summaries: list, home: dict, index_pages: dict) -> Rendered:
    """The final text of a planned page, its record and its id marks."""
    lang = src.lang
    by_family: dict = {}
    for p in plan.pages:
        by_family.setdefault(p.family, []).append(p)
    body = compose_body(src, page.blocks, page.shift)
    body_lines = B.resolve(body.lines, home, ("../" * (N.page_path(page.pid).count("/"))) or "")
    meta, degraded, fams = build_meta(src, page, by_family, summaries, body_lines, index_pages)
    head, foot = envelope(meta)
    lines = head + body_lines + foot
    text = finalize(lines)
    path = N.page_path(page.pid)
    marks: list = []
    for idx, (ident, kind, role, hdr) in sorted(body.marks.items()):
        marks.append((ident, kind, role, len(head) + idx + 1, len(head) + hdr + 1))
    rec = Rendered(page.pid, path, text, page.family, "page", {}, marks, body.sections, text.split("\n")[:-1], len(head), fams, ["RS-132"] if degraded else [],
                   meta["content"])
    data = text.encode("utf-8")
    keys = [u.key for u in page.units if u.kind not in ("policy", "quality")]
    summary = tx(lang, "covers.sep").join(tx(lang, "covers.item", label=label, n=n) for label, n in merge_counts(sections_for_sum(page, body)))
    rec.record = {"p": path, "id": N.page_ref(page.pid), "k": "page", "fam": page.family, "dim": page.dim, "cls": page.cls, "ai": True, "b": len(data),
                  "l": len(rec.lines), "tok": tok_estimate(text, lang), "sha8": sha8_of(data), "twin": None, "sum": clip_text(summary, 100),
                  "n": sum(info.n for info in body.sections), "i": page.index, "of": page.count, "m": src.stamp, "fit": body.fit}
    if len(page.blocks) > 1:
        rec.record["fams"] = [fam.key for fam, _u in page.blocks]
    if keys:
        rec.record["range"] = [clip_text(keys[0], 60), clip_text(keys[-1], 60)]
    if "prev" in meta:
        rec.record["prev"] = N.page_ref(meta["prev"][0])
    if "next" in meta:
        rec.record["next"] = N.page_ref(meta["next"][0])
    if "up" in meta:
        rec.record["up"] = "INDEX-" + (page.dim or "")
    return rec


def sections_for_sum(page, body) -> list:
    """The section infos the manifest ``sum`` and the card count: on a page of several families only the main section of each."""
    if page.family in ("A", "P"):
        main = {fam.key: fam.main for fam, _u in page.blocks}
        return [info for info in body.sections if info.key == main.get(getattr(info, "family", ""))]
    return body.sections


def clip_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def home_map(plan) -> dict:
    """``{"node:<id>": page id, ...}``: the home page of every element a unit marks as its home (the first page wins; a part of a longer
    unit homes the unit's id on the part that carries its identity)."""
    out: dict = {}
    for page in plan.pages:
        for _fam, units in page.blocks:
            for unit in units:
                for seg in unit.bands:
                    for item in seg.items:
                        for ident, kind, role in item.marks.values():
                            if role == HOME:
                                out.setdefault(f"{kind}:{ident}", page.pid)
    return out
