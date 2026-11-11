"""The screen card (design A3 4.5, D-12, SV-207): one derived Markdown page per screen with all its states, from the same layout structure the
SVGs come from.

``build`` turns the layouts of every state of a screen into a ``CardModel``: identity and property lines, the check point count, and the
tables that carry the facts (widgets with the number the SVG circle shows, states, outgoing and incoming navigation, what the renderer
derived on its own, every visible string with the states it appears in, the sample data). ``CardModel.pages`` renders the model through the
reading envelope (``reading.api``) and paginates a card that does not fit a page at table row seams (continuation cards repeat the
identity line and the table heading). The gallery renders the very same model as HTML (``gallery.py``), so a card and its gallery cannot
disagree. A table row longer than 480 characters has its longest cells replaced by ``see note [k]`` with the full text under the table
(design 2.4 rule 6). Everything authored passes ``textfit.card_text`` (clean, Markdown-safe, placeholder-safe). Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..reading import api as reading
from . import labels
from . import layout as lay
from . import model as um
from . import textfit as tf
from . import vocab

MAX_NAMED = 12


class Code(str):
    """A table cell that is a code span (an id): printed between backticks."""


@dataclass
class Table:
    title: str                      # the heading text without the number: ``Widgets``
    headers: list
    rows: list                      # list of lists of str | Code
    ids: list                       # per row: the id the row anchors, "" for none
    kind: str = ""                  # what the ids are (widget, variant, nav): the manifest records it

    def heading(self, lang: str, a: int = 0, b: "int | None" = None) -> str:
        total = len(self.rows)
        b = total if b is None else b
        if a == 0 and b == total:
            return "## " + labels.t(lang, "card.heading", title=self.title, n=total)
        return "## " + labels.t(lang, "card.heading_part", title=self.title, n=b - a, m=total, a=a + 1, b=b)


@dataclass
class CardModel:
    screen: str
    enc: str
    title: str
    lang: str
    covers: str
    excludes: str
    howto: str
    ident: str
    intro: list
    blocks: list
    checkpoints: int
    ui_sha8: str
    model_sha8: "str | None"
    project: str
    states: list                    # variant names in model order

    def all_rows(self) -> int:
        return sum(len(b.rows) for b in self.blocks if isinstance(b, Table))


@dataclass
class CardPage:
    path: str
    page: str                       # the page id without the section sign
    part: int
    parts: int
    text: str
    anchors: list = field(default_factory=list)   # (id, kind, line, hdr)


# ---------------------------------------------------------------------------------------------------------------
# Building the model
# ---------------------------------------------------------------------------------------------------------------


def _cell(value) -> str:
    return tf.card_text(value)


def _named(ids: Sequence, lang: str) -> str:
    ids = list(ids)
    shown = ", ".join(ids[:MAX_NAMED])
    return shown + (labels.t(lang, "card.more", k=len(ids) - MAX_NAMED) if len(ids) > MAX_NAMED else "")


def widget_attrs(w: Mapping, navs: Sequence) -> str:
    """The sparse ``attrs`` cell: links to 5D and the sample data (neither is an interface fact, neither is a check point)."""
    parts = []
    if w.get("reqIds"):
        parts.append("req=" + ",".join(w["reqIds"]))
    cases = [n["caseId"] for n in navs if n.get("caseId")]
    rules = [n["ruleId"] for n in navs if n.get("ruleId")]
    if cases:
        parts.append("case=" + ",".join(cases))
    if rules:
        parts.append("rule=" + ",".join(rules))
    data = w.get("data")
    if isinstance(data, Mapping):
        cols, rows = lay._sample(w)
        parts.append(f"data=cols:{len(cols)},rows:{len(rows)}")
    return " ".join(parts)


def nav_links(n: Mapping) -> str:
    parts = []
    if n.get("apiId"):
        parts.append("api=" + n["apiId"])
    if n.get("caseId"):
        parts.append("case=" + n["caseId"])
    if n.get("ruleId"):
        parts.append("rule=" + n["ruleId"])
    if n.get("reqIds"):
        parts.append("req=" + ",".join(n["reqIds"]))
    return " ".join(parts)


def build(index: lay.ModelIndex, screen_id: str, states: Sequence, *, enc_name: str, files: Mapping, lang: str, ui_sha8: str,
          model_sha8: "str | None", project: str, checkpoints: int, svg_bytes: Mapping, stamp: str = "") -> CardModel:
    """The card of one screen. ``states`` are the ``StateLayout`` of every variant (default first), ``files`` maps a variant to its SVG file
    name, ``svg_bytes`` to its size."""
    screen = index.screens[screen_id]
    model = index.model
    wids = [w for w in screen.get("widgetIds", ()) if w in index.widgets]
    nums = {wid: i + 1 for i, wid in enumerate(screen.get("widgetIds", ()))}
    t = lambda key, **v: labels.t(lang, key, **v)  # noqa: E731
    frame = screen.get("frame", vocab.DEFAULT_FRAME)
    W, H0 = vocab.FRAMES[frame]
    title = tf.author(screen.get("title", "")) or screen_id
    nav_out = [n for n in model.get("nav", ()) if n["fromScreenId"] == screen_id]
    nav_in = [n for n in model.get("nav", ()) if n["toScreenId"] == screen_id]

    # ---- widgets
    rows, ids = [], []
    for wid in wids:
        w = index.widgets[wid]
        navs = index.nav_from.get(wid, ())
        parent = w["parentId"].split(".", 1)[1] if w.get("parentId") else ""
        rows.append([str(nums[wid]), Code(wid), w["kind"], _cell(w.get("label", "")), parent, ",".join(w["inVariants"]) if w.get("inVariants") else "",
                     w.get("bind", ""), w.get("apiId", ""), w.get("emph", ""), ",".join(n["toId"] for n in navs), widget_attrs(w, navs)])
        ids.append(wid)
    widgets_table = Table(t("card.t.widgets"), ["#", "id", "kind", "label", "parent", "in", "bind", "api", "emph", "goto", t("card.col.attrs")], rows, ids, "widget")

    # ---- states
    srows, sids = [], []
    for st in states:
        vid = um.variant_id(screen_id, st.variant)
        hidden = [f"{wid}" + ("" if why == "in" else f"({why})") for wid, why in st.hidden]
        vrec = index.variants.get(screen_id, {}).get(st.variant)
        srows.append([Code(vid), vrec["kind"] if vrec else "", "" if st.variant == vocab.DEFAULT_VARIANT else _cell(st.note), _cell(st.text), Code(files[st.variant]),
                      f"{st.shown}/{st.total}", _named(hidden, lang)])
        sids.append(vid)
    states_table = Table(t("card.t.states"), [t("card.col.state"), t("card.col.kind"), t("card.col.note"), t("card.col.text"), t("card.col.file"), t("card.col.shown"),
                                              t("card.col.hidden")], srows, sids, "variant")

    # ---- the order of the children of every container with two or more (the ``@order`` check points)
    orows = []
    roots = [w for w in wids if index.widgets[w].get("parentId") is None]
    if len(roots) >= 2:
        orows.append([Code(um.root_id(screen_id)), ",".join(index.widgets[w]["local"] for w in roots)])
    for wid in wids:
        children = [c for c in index.widgets[wid].get("childIds", ()) if c in index.widgets]
        if len(children) >= 2:
            orows.append([Code(wid), ",".join(index.widgets[c]["local"] for c in children)])
    order_table = Table(t("card.t.order"), [t("card.col.container"), t("card.col.children")], orows, [""] * len(orows), "")

    # ---- navigation
    out_rows = [[Code(n["fromId"]), Code(n["toId"]), _cell(n.get("label", "")), _cell(n.get("guard", "")), n.get("on", vocab.DEFAULT_TRIGGER), nav_links(n)]
                for n in nav_out]
    in_rows = [[Code(n["fromId"]), Code(n["toId"]), _cell(n.get("label", "")), n.get("on", vocab.DEFAULT_TRIGGER)] for n in nav_in]
    out_table = Table(t("card.t.navout"), [t("card.col.from"), t("card.col.to"), t("card.col.label"), t("card.col.guard"), t("card.col.on"), t("card.col.links")],
                      out_rows, [""] * len(out_rows), "")
    in_table = Table(t("card.t.navin"), [t("card.col.from"), t("card.col.to"), t("card.col.label"), t("card.col.on")], in_rows, [""] * len(in_rows), "")

    # ---- what the renderer derived
    derived: dict = {}
    for st in states:
        for kind, wid, string in st.derived:
            derived.setdefault((kind, wid, string), []).append(st.variant)
    drows = [[t("card.derived." + kind), Code(wid), _cell(string), _states_cell(vs, states, lang)] for (kind, wid, string), vs in derived.items()]
    derived_table = Table(t("card.t.derived"), [t("card.col.what"), t("card.col.widget"), t("card.col.text"), t("card.col.state")], drows, [""] * len(drows), "")

    # ---- every visible string
    seen: dict = {}
    for st in states:
        for string, is_derived, wid, role in st.texts:
            key = (string, is_derived, role if role in ("band", "note") else "")
            entry = seen.setdefault(key, {"states": [], "sources": []})
            if st.variant not in entry["states"]:
                entry["states"].append(st.variant)
            source = wid or ""
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
    trows = []
    for (string, is_derived, special), entry in seen.items():
        if special == "band":
            source = t("card.src.band")
        elif special == "note":
            source = t("card.src.note")
        else:
            source = _named(entry["sources"], lang) + (" " + t("card.src.derived") if is_derived else "")
        trows.append([_cell(string), _states_cell(entry["states"], states, lang), source])
    texts_table = Table(t("card.t.texts"), [t("card.col.string"), t("card.col.state"), t("card.col.source")], trows, [""] * len(trows), "")

    # ---- sample data
    arows = []
    for wid in wids:
        w = index.widgets[wid]
        if w["kind"] != "table":
            continue
        cols, data_rows = lay._sample(w)
        has = bool(cols or data_rows)
        drawn = min(len(data_rows), states[0].sample_rows) if states else 0
        arows.append([Code(wid), _cell(", ".join(cols)), str(len(data_rows)), str(drawn) if has else "", "" if has else t("card.nodata")])
    sample_table = Table(t("card.t.sample"), [t("card.col.widget"), t("card.col.columns"), t("card.col.rows"), t("card.col.drawn"), t("card.col.note")], arows,
                         [""] * len(arows), "")

    blocks: list = [widgets_table, order_table, states_table, out_table, in_table, derived_table, texts_table, sample_table]
    # ---- the lines above the tables
    props = [f"frame={frame}", f"route={screen['route']}" if screen.get("route") else "", f"kind={screen.get('kind', vocab.DEFAULT_SCREEN_KIND)}",
             f"start={'true' if screen.get('start') else 'false'}", f"actor={screen['actorId']}" if screen.get("actorId") else "",
             "req=" + ",".join(screen["reqIds"]) if screen.get("reqIds") else "", f"group={screen['group']}" if screen.get("group") else "",
             f"align={screen['align']}" if screen.get("align") else ""]
    intro = [t("card.ident", id=screen_id, kind=labels.word(lang, "screenKinds", screen.get("kind", vocab.DEFAULT_SCREEN_KIND)),
               frame=labels.word(lang, "frames", frame), w=W, h=H0)]
    if screen.get("purpose"):
        intro.append(t("card.purpose", purpose=tf.card_text(screen["purpose"])))
    intro.append(t("card.props", props=" · ".join(p for p in props if p)))
    intro.append(t("card.checkpoints", n=checkpoints))
    if stamp:
        intro.append(t("card.stamp", sha=stamp))
    covers = t("card.covers", w=len(wids), s=len(states), n=len(nav_out), c=checkpoints)
    return CardModel(screen_id, enc_name, tf.card_text(title), lang, covers, t("card.excludes"), t("card.howto"), intro[0], intro, blocks, checkpoints,
                     ui_sha8, model_sha8, project, [st.variant for st in states])


def _states_cell(variants: Sequence, states: Sequence, lang: str) -> str:
    all_names = [st.variant for st in states]
    if len(variants) == len(all_names) and len(all_names) > 1:
        return labels.t(lang, "card.all_states")
    return ", ".join(variants)


# ---------------------------------------------------------------------------------------------------------------
# Rendering a table (shared by the Markdown pages and the gallery)
# ---------------------------------------------------------------------------------------------------------------


def _row_line(cells: Sequence) -> str:
    return "| " + " | ".join(f"`{c}`" if isinstance(c, Code) and c else str(c) for c in cells) + " |"


def materialize(table: Table, lang: str, a: int = 0, b: "int | None" = None) -> tuple:
    """``(heading, header cells, rows, notes)`` of rows ``a..b``: a row whose line is longer than 480 characters has its longest cells replaced
    by ``see note [k]`` (the full text goes to ``notes``, folded at 480 characters). ``rows`` are lists of cells, ``notes`` lists of lines."""
    b = len(table.rows) if b is None else b
    mark = labels.t(lang, "card.cont")
    out_rows, notes = [], []
    k = 0
    for cells in table.rows[a:b]:
        cells = list(cells)
        guard = 0
        while len(_row_line(cells)) > tf.FOLD_AT and guard < len(cells):
            guard += 1
            candidates = [(len(c), i) for i, c in enumerate(cells) if not isinstance(c, Code) and len(c) > 40]
            if not candidates:
                break
            _n, i = max(candidates)
            k += 1
            notes.append((k, cells[i]))
            cells[i] = labels.t(lang, "card.note", k=k)
        out_rows.append(cells)
    note_lines = []
    for number, text in notes:
        note_lines.extend(tf.fold(f"[{number}] {text}", mark))
    return table.heading(lang, a, b), list(table.headers), out_rows, note_lines


def table_lines(table: Table, lang: str, a: int = 0, b: "int | None" = None) -> tuple:
    """``(lines, row line offsets)``: heading, header and separator, the rows, then an empty line and the notes when there are any."""
    heading, headers, rows, notes = materialize(table, lang, a, b)
    lines = [heading, "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    offsets = []
    for cells in rows:
        offsets.append(len(lines))
        lines.append(_row_line(cells))
    if notes:
        lines.append("")
        lines.extend(notes)
    return lines, offsets


# ---------------------------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------------------------


def _page_meta(card: CardModel, part: int, parts: int, lang: str) -> dict:
    meta = {"page": reading.card_page_id(card.enc, part), "index": 1, "count": 1, "cls": "page", "title": card.title, "project": card.project, "lang": lang,
            "ui_stamp": card.ui_sha8, "content": "prototype", "covers": card.covers, "excludes": card.excludes, "howto": card.howto,
            "up": "INDEX.md", "start": "../START.md"}
    if card.model_sha8:
        meta["stamp"] = card.model_sha8
    if parts > 1:
        meta["index"], meta["count"] = part, parts
        meta["covers"] = labels.t(lang, "card.covers_part", base=card.covers)
        meta["continuation"] = {"unit": card.screen, "part": part, "parts": parts,
                                "others": [reading.card_page_id(card.enc, p) for p in range(1, parts + 1) if p != part],
                                "next": reading.card_page_id(card.enc, part + 1) if part < parts else None}
    return meta


def _render(card: CardModel, body: list, part: int, parts: int, lang: str) -> tuple:
    head, foot = reading.envelope(_page_meta(card, part, parts, lang))
    return reading.finalize(head + body + foot), len(head)


def _size(lines: Sequence) -> int:
    return sum(len(x.encode("utf-8")) + 1 for x in lines)


def pages(card: CardModel, lang: str) -> list:
    """The pages of a card: one when it fits ``cap_md['page']`` bytes, else continuation pages cut at table row seams."""
    intro = [ln for x in card.intro for ln in tf.fold(x, labels.t(lang, "card.cont"))]
    body, anchors = _paginate(card, lang, intro, None)[0]
    text, head_len = _render(card, body, 1, 1, lang)
    if len(text.encode("utf-8")) <= reading.BUDGETS["cap_md"]["page"]:
        return [_page(card, 1, 1, text, head_len, anchors)]
    plan = _paginate(card, lang, intro, reading.content_capacity("CARD:page", lang, True))
    out = []
    for i, (body, anchors) in enumerate(plan, start=1):
        text, head_len = _render(card, body, i, len(plan), lang)
        out.append(_page(card, i, len(plan), text, head_len, anchors))
    return out


def _page(card: CardModel, part: int, parts: int, text: str, head_len: int, anchors: list) -> CardPage:
    return CardPage(reading.card_path(card.enc, part), reading.card_page_id(card.enc, part), part, parts, text,
                    [(rid, kind, head_len + row + 1, head_len + hdr + 1) for rid, kind, row, hdr in anchors])


def _rows_that_fit(block: Table, lang: str, a: int, room: int) -> int:
    """The largest ``b`` such that rows ``a..b`` of the table (heading, header, notes) take at most ``room`` bytes; ``a`` when not even one row fits."""
    lo, hi = a, len(block.rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _size(table_lines(block, lang, a, mid)[0]) + 1 <= room:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _paginate(card: CardModel, lang: str, intro: list, capacity: "int | None") -> list:
    """``[(body lines, anchors)]``: everything in one part when ``capacity`` is ``None``, else parts filled up to ``capacity`` bytes (newlines
    counted), cut at row seams; a part after the first starts with the identity line. ``anchors`` are ``(id, kind, row index, heading index)``
    with indices into the body."""
    ident = [intro[0]]
    parts = [[list(intro), []]]
    for block in card.blocks:
        cur, anchors = parts[-1]
        rows = len(block.rows)
        if rows == 0:
            lines = [block.heading(lang), labels.t(lang, "card.none")]
            if capacity is not None and _size(cur) + _size(lines) + 1 > capacity and len(cur) > len(ident):
                parts.append([list(ident), []])
                cur, anchors = parts[-1]
            cur.extend(lines + [""])
            continue
        a = 0
        while a < rows:
            b = rows
            if capacity is not None:
                b = _rows_that_fit(block, lang, a, capacity - _size(cur))
                if b == a:
                    if len(cur) > len(ident):
                        parts.append([list(ident), []])
                        cur, anchors = parts[-1]
                        continue
                    b = a + 1                              # a single row alone is larger than a part cannot happen (author strings are capped)
            lines, offsets = table_lines(block, lang, a, b)
            for j, off in enumerate(offsets):
                if block.ids[a + j]:
                    anchors.append((block.ids[a + j], block.kind, len(cur) + off, len(cur)))
            cur.extend(lines + [""])
            a = b
            if a < rows:
                parts.append([list(ident), []])
                cur, anchors = parts[-1]
    for part in parts:
        while part[0] and part[0][-1] == "":
            part[0].pop()
    return [(p[0], p[1]) for p in parts]


def visible_strings(lines: Sequence, lang: str) -> list:
    """The strings of the ``all visible text`` table of a card (first cells), with ``see note`` cells replaced by the note and folded lines
    joined: what SV-207 compares the SVG texts with."""
    lines = tf.unfold(list(lines), labels.t(lang, "card.cont"))
    prefix = "## " + labels.t(lang, "card.t.texts")
    header = "| " + labels.t(lang, "card.col.string") + " |"
    out, inside, notes = [], False, {}
    for line in lines:
        if line.startswith("## "):
            inside = line.startswith(prefix)
            continue
        if not inside:
            continue
        if line.startswith("[") and "] " in line[:8]:
            notes[line[1:line.index("]")]] = line[line.index("] ") + 2:]
        elif line.startswith("| ") and not line.startswith(header) and not line.startswith("|---"):
            out.append(line[2:].split(" | ")[0].rstrip(" |"))
    refs = {labels.t(lang, "card.note", k=k): v for k, v in notes.items()}
    return [refs.get(s, s) for s in out]
