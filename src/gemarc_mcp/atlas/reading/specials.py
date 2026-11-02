"""The pages that are not a family of facts: ``START``, the glossary ``G01``, the dimension ``INDEX`` cards, ``docs/README.md`` and ``.ignore``
(design A3 2.7.4, 2.7.5, 2.11).

Every one of them is a card in the same frame as the reading pages (``envelope`` + sentinel), written from the fixed texts of
``derive/i18n`` (``read.start.*``, ``read.glossary.*``, ``read.index.*``, ``read.docs.*``) and the facts of the plan (page counts, entries).
START never prints an exact size of a file the build writes later: it prints sizes rounded to KB, and only for files the build knows.
"""
from __future__ import annotations

from ..derive import assemble as A
from ..derive import i18n as T
from . import names as N
from .api import content_capacity
from . import blocks as B
from .blocks import join_cells
from .envelope import envelope, finalize
from .pages import Rendered, clip_text, rel_start, sha8_of, tok_estimate
from .source import Source
from .text import raw, tx, tx_list

HOME = "home"
IGNORE_LINES = ("site/", "docs/", "prompts/", "index.html", "oracle-sheet.md", "manifest.jsonl")
FAMILY_ORDER = ("R", "T", "E", "S", "D", "M", "W", "C", "V", "Q")


def _record(src: Source, pid: str, path: str, kind: str, fam: str, text: str, summary: str, extra: dict | None = None) -> dict:
    data = text.encode("utf-8")
    rec = {"p": path, "id": N.page_ref(pid), "k": kind, "fam": fam, "dim": None, "cls": "card", "ai": True, "b": len(data), "l": text.count("\n"),
           "tok": tok_estimate(text, src.lang), "sha8": sha8_of(data), "twin": None, "sum": clip_text(summary, 100), "m": src.stamp, "i": 1, "of": 1}
    rec.update(extra or {})
    return rec


def render_card(src: Source, pid: str, path: str, kind: str, body: list, covers: str, excludes: str, howto: str, *, index: int = 1, count: int = 1,
                prev=None, nxt=None, start: bool = True, fam: str = "", extra: dict | None = None) -> Rendered:
    meta = {"page": pid, "index": index, "count": count, "cls": "card", "lang": src.lang, "project": src.project, "stamp": src.stamp, "content": "structure",
            "covers": covers, "excludes": excludes, "howto": howto}
    if kind == "index" and pid.startswith("INDEX"):
        meta["title"] = N.family_title("INDEX", src.lang, N.parse_page_id(pid).dim)
    if start:
        meta["start"] = rel_start(path)
    if prev:
        meta["prev"] = prev
    if nxt:
        meta["next"] = nxt
    head, foot = envelope(meta)
    text = finalize(head + body + foot)
    rec = _record(src, pid, path, kind, fam or pid, text, covers, {"i": index, "of": count, **(extra or {})})
    return Rendered(pid, path, text, fam or pid, kind, rec, [], [], text.split("\n")[:-1], len(head), [])


# ---------------------------------------------------------------------------------------------------- families as START sees them

def family_rows(src: Source, plan, rendered: dict, index_pages: dict) -> list:
    """``[(family key, pages, count of its main unit)]`` for the START table: every family, the ones without pages too."""
    out = []
    for key in FAMILY_ORDER:
        pages = [p for p in plan.pages if p.family == key]
        folded = [p for p in plan.pages if p.family in ("A", "P") and any(f.key == key for f, _u in p.blocks)]
        total = 0
        main = plan.families[key].main if key in plan.families else ""
        for p in pages + folded:
            for info in rendered[p.pid].sections:
                if getattr(info, "family", key) == key and info.key == main:
                    total += info.n
        out.append((key, pages, folded, total))
    return out


def start_body(src: Source, plan, rendered: dict, index_pages: dict, ui: dict, cfg, compact: bool = False) -> list:
    lang = src.lang
    lines: list = []
    sections = tx_list(lang, "start.h")
    content = tx(lang, "content.sealed" if any(getattr(r, "content", "structure") == "sealed" for r in rendered.values()) else "content.structure")
    lines.append("## " + sections[0])
    lines.append(tx(lang, "start.identity", project=src.project, language=lang, stamp=src.stamp, mode=src.mode, content=content))
    lines.append(tx(lang, "start.lead"))
    rows = family_rows(src, plan, rendered, index_pages)
    by_key = {key: (pages, folded, total) for key, pages, folded, total in rows}

    def link(pid: str) -> str:
        return "[" + N.page_ref(pid) + "](" + N.page_path(pid) + ")"

    def entry(key: str) -> str:
        pages, folded, _total = by_key.get(key, ([], [], 0))
        if pages:
            return link(pages[0].pid)
        if folded:
            return link(folded[0].pid)
        return tx(lang, "start.dim_none")

    # route table
    route_head = tx_list(lang, "start.route_head")
    fill = {"r": entry("R"), "d": entry("D"), "m": entry("M"), "w": entry("W"), "c": entry("C"), "v": entry("V"), "q": entry("Q"), "g": link("G01")}
    ui_entry = ui.get("entry") or ""
    route = [_fill(str(template), fill) for template in raw(lang, "start.route")]
    if ui_entry:
        route.append(_fill(str(raw(lang, "start.route_ui")), {"ui": "`" + ui_entry + "`"}))
    lines += ["", "## " + sections[1] + tx(lang, "sec.count", n=len(route)), join_cells(route_head), "|" + "---|" * len(route_head)] + route
    # dimension table
    dim_head = tx_list(lang, "start.dim_head")
    lines += ["", "## " + sections[2] + tx(lang, "sec.count", n=len(rows)), join_cells(dim_head), "|" + "---|" * len(dim_head)]
    for key, pages, folded, total in rows:
        title = tx(lang, "family." + key)
        if pages or folded:
            if pages:
                dim = pages[0].dim
                where = ("[" + N.page_ref("INDEX-" + dim) + "](read/" + dim + "/INDEX.md)") if index_pages.get(dim) else ", ".join(link(p.pid) for p in pages)
                count = str(len(pages))
            else:
                where = tx(lang, "start.dim_in", page=N.page_ref(folded[0].pid))
                count = "0"
            unit = plan.families[key].section(plan.families[key].main).unit_label if key in plan.families else ""
            content_cell = f"{unit} {total}".strip()
        else:
            count = "0"
            where = tx(lang, "start.dim_none")
            content_cell = tx(lang, "start.reason." + key)
        lines.append(join_cells([T.md_text(title), count, where, T.md_text(content_cell)]))
    # stale, skip, ui, clients, results, cannot
    lines += ["", "## " + sections[3]] + ["- " + s for s in tx_list(lang, "start.stale")]
    size = _kb(src, cfg, "index.html")
    size_docs = _kb_docs(src, lang)
    lines += ["", "## " + sections[4]] + ["- " + _fill(s, {"size": size, "size_docs": size_docs}) for s in tx_list(lang, "start.skip")]
    lines += ["", "## " + sections[5]]
    decision = (ui or {}).get("decision") or ""
    if ui_entry:
        lines.append("- " + tx(lang, "start.ui_yes", entry="`" + ui_entry + "`"))
    elif decision.startswith("none") and (ui or {}).get("reason"):
        lines.append("- " + tx(lang, "start.ui_declared", reason=T.md_text(ui["reason"])))
    else:
        lines.append("- " + tx(lang, "start.ui_none"))
    if ui.get("hint") and decision.startswith("none"):
        lines.append("- " + tx(lang, "start.ui_warn"))
    lines += ["", "## " + sections[6], "- " + tx(lang, "start.client_ref" if compact else "start.client"), "", "## " + sections[7], "- " + tx(lang, "start.results"), "", "## " + sections[8],
              "- " + tx(lang, "start.cannot")]
    return lines


def _fill(template: str, values: dict) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _kb(src: Source, cfg, name: str) -> str:
    size = (cfg.sizes or {}).get(name)
    return tx(src.lang, "start.size_kb", kb=max(1, round(size / 1024))) if isinstance(size, int) and size > 0 else ""


def _kb_docs(src: Source, lang: str) -> str:
    """The sizes of the three documents of the primary language, rounded to 10 KB (``assemble`` gives exactly the text ``docs/`` receives)."""
    items = []
    for level in A.LEVELS:
        try:
            text = A.assemble(src.bundle, level, src.fr["elements"])
        except Exception:
            return ""
        items.append(tx(lang, "docs.size", name=level, kb=max(10, round(len(text.encode("utf-8")) / 10240) * 10)))
    return tx(lang, "docs.sizes", items=tx(lang, "covers.sep").join(items))


def build_start(src: Source, plan, rendered: dict, index_pages: dict, ui: dict, cfg, cap: int | None = None) -> tuple:
    """``(Rendered, degraded)``: when the card is above its cap the hints per client (section 7) move to the glossary and a pointer stays."""
    lang = src.lang
    cap = content_capacity("START:card", lang) if cap is None else cap
    body = start_body(src, plan, rendered, index_pages, ui or {}, cfg)
    degraded = B.body_bytes(body) > cap
    if degraded:
        body = start_body(src, plan, rendered, index_pages, ui or {}, cfg, compact=True)
    page = render_card(src, "START", "START.md", "start", body, tx(lang, "start.covers"), tx(lang, "special.excludes"), tx(lang, "start.howto"), start=False,
                       fam="START")
    return page, degraded


def build_glossary(src: Source) -> Rendered:
    lang = src.lang
    h = tx_list(lang, "glossary.h")
    ruler = " > ".join(src.grade_ruler()) or "-"
    body: list = []
    groups = [("page", tx_list(lang, "glossary.page")), ("ruler", [tx(lang, "glossary.ruler", ruler=ruler)]), ("marks", tx_list(lang, "glossary.marks")),
              ("ids", tx_list(lang, "glossary.ids")), ("fold", tx_list(lang, "glossary.fold")), ("trunc", tx_list(lang, "glossary.trunc")),
              ("results", tx_list(lang, "glossary.results")), ("cannot", tx_list(lang, "glossary.cannot"))]
    for n, (_key, lines) in enumerate(groups):
        if body:
            body.append("")
        body.append("## " + h[n])
        body.extend("- " + line for line in lines)
    return render_card(src, "G01", "read/G01.md", "glossary", body, tx(lang, "glossary.covers"), tx(lang, "special.excludes"), tx(lang, "glossary.howto"), fam="G")


# ---------------------------------------------------------------------------------------------------- INDEX

def build_index(src: Source, dim: str, entries: list) -> list:
    """The shards of one dimension INDEX: ``entries`` is ``[Rendered]`` of the pages of the dimension (each listed once, in plan order)."""
    lang = src.lang
    cap = content_capacity("INDEX:card", lang)
    head_lines = list(_table_head(lang))
    rows = [_index_row(src, r) for r in entries]
    shards: list = []
    cur: list = []
    base = sum(len(x.encode("utf-8")) + 1 for x in head_lines) + 400
    for row in rows:
        size = base + sum(len(x.encode("utf-8")) + 1 for x in cur) + len(row.encode("utf-8")) + 1
        if cur and size > cap:
            shards.append(cur)
            cur = []
        cur.append(row)
    if cur:
        shards.append(cur)
    count = len(shards)
    out: list = []
    for n, shard in enumerate(shards, start=1):
        pid = "INDEX-" + dim + ("" if n == 1 else f"-{n}")
        path = N.page_path(pid)
        links = " · ".join(f"[INDEX{'' if m == 1 else '-' + str(m)}](INDEX{'' if m == 1 else '-' + str(m)}.md)" for m in range(1, count + 1))
        body = ["### " + tx(lang, "sec.pages") + tx(lang, "sec.count", n=len(shard))] + head_lines + shard
        if count > 1:
            body += ["", tx(lang, "index.shards", links=links)]
        covers = tx(lang, "covers.line", items=tx(lang, "covers.item", label=tx(lang, "sec.pages"), n=len(shard)), rest=tx(lang, "covers.complete") if count == 1 else
                    tx(lang, "covers.partial", n=len(shard), m=len(rows)))
        prev = ("INDEX-" + dim + ("" if n == 2 else f"-{n - 1}"), "INDEX" + ("" if n == 2 else f"-{n - 1}") + ".md") if n > 1 else None
        nxt = ("INDEX-" + dim + f"-{n + 1}", f"INDEX-{n + 1}.md") if n < count else None
        out.append(render_card(src, pid, path, "index", body, covers, tx(lang, "special.excludes"), tx(lang, "index.howto"), index=n, count=count, prev=prev,
                               nxt=nxt, fam="INDEX", extra={"dim": dim}))
    return out


def _table_head(lang: str) -> tuple:
    heads = tx_list(lang, "index.head")
    return (join_cells(heads), "|" + "---|" * len(heads))


def _index_row(src: Source, r: Rendered) -> str:
    lang = src.lang
    rec = r.record
    link = "[" + N.page_ref(r.pid) + "](" + r.pid + ".md)"
    rng = rec.get("range")
    span = "`" + rng[0] + "`…`" + rng[1] + "`" if rng and rng[0] != rng[1] else ("`" + rng[0] + "`" if rng else "-")
    tok = rec["tok"]
    return join_cells([link, T.md_text(rec.get("sum") or ""), str(rec.get("n", 0)), f"{rec['b']:,}", f"~{tok / 1000:.1f}k", span])


# ---------------------------------------------------------------------------------------------------- docs/README.md, .ignore

def build_docs_readme(src: Source) -> tuple:
    """``(path, bytes, record)`` of ``docs/README.md`` (hand-over card; not part of the reading frame)."""
    lang = src.lang
    sizes = _kb_docs(src, lang)
    lines = ["# " + tx(lang, "docs.title"), ""] + [_fill(x, {"sizes": sizes}) for x in tx_list(lang, "docs.lines")]
    text = "\n".join(lines) + "\n"
    data = text.encode("utf-8")
    rec = {"p": "docs/README.md", "id": "", "k": "doc", "fam": "", "dim": None, "cls": "handoff", "ai": False, "b": len(data), "l": len(lines), "tok": 0,
           "sha8": sha8_of(data), "twin": None, "sum": clip_text(lines[0].lstrip("# "), 100), "m": src.stamp}
    return "docs/README.md", data, rec


def ignore_bytes() -> bytes:
    return ("\n".join(IGNORE_LINES) + "\n").encode("utf-8")
