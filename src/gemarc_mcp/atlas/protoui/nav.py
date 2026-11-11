"""``screens/NAV.md`` (page ``§NAV``): the navigation of the prototype as a Mermaid ``flowchart LR`` with an alias table and a navigation
table (design A3 D-15, 4.5, SV-214). Standard library only; the diagram text comes from ``derive.mermaid.build_component`` (generated node
ids ``n1``, ``n2``...: an authored id never has to be a valid Mermaid id), so the alias table is what gives the screen behind ``n7``.

More than 40 screens are cut into parts (``NAV-2.md``...; by ``group`` when there are groups, else in sorted id runs; every part links all
parts). A part draws the navigation rows whose two ends are both in the part; every row that starts in the part is in its table, with the page
that holds its target, so a jump across parts is in the tables only. Screens that cannot be reached from a start screen and screens without an
outgoing navigation are listed (the same facts as UX-201 and UX-202, derived here from the model without the rule machinery).
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from ..derive import mermaid
from ..reading import api as reading
from . import card as cardmod
from . import index as idx
from . import labels
from . import textfit as tf

_NODE = re.compile(r"^\s*n\d+[\[\(\{]")
_EDGE = re.compile(r"-->|-\.->")


def reachability(model: Mapping) -> tuple:
    """``(declared, unreachable, dead_ends)``: whether any screen is a start screen, the screen ids not reachable from the start screens through
    navigation (empty when none is declared), and the screen ids without an outgoing navigation."""
    screens = list(model.get("screens", ()))
    out: dict = {}
    for n in model.get("nav", ()):
        out.setdefault(n["fromScreenId"], []).append(n["toScreenId"])
    starts = [s["id"] for s in screens if s.get("start")]
    seen = set(starts)
    stack = list(starts)
    while stack:
        for target in out.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    unreachable = [s["id"] for s in screens if starts and s["id"] not in seen]
    dead = [s["id"] for s in screens if s["id"] not in out]
    return bool(starts), unreachable, dead


def diagram(part: Sequence, nav_rows: Sequence) -> tuple:
    """``(text, aliases)`` of one part: the Mermaid text and ``[(alias, screen id)]`` in node order; edges only between screens of the part."""
    inside = {s["id"] for s in part}
    data = {"groups": [], "nodes": [], "edges": []}
    groups: dict = {}
    for s in part:
        data["nodes"].append({"id": s["id"], "label": f"{s['id']} {tf.author(s.get('title', ''))}".strip(), "shape": "round" if s.get("start") else "box"})
        if s.get("group"):
            groups.setdefault(s["group"], []).append(s["id"])
    for name in sorted(groups):
        data["groups"].append({"id": name, "label": name, "nodeIds": groups[name]})
    count = 0
    for n in nav_rows:
        if n["fromScreenId"] in inside and n["toScreenId"] in inside:
            count += 1
            variant = n["toId"].split("#", 1)[1] if "#" in n["toId"] else ""
            label = tf.author(n.get("label", "")) + (f" #{variant}" if variant else "")
            data["edges"].append({"id": n["id"], "from": n["fromScreenId"], "to": n["toScreenId"], "label": label.strip() or n["id"]})
    text, _issues = mermaid.build_component(data)
    aliases = [(f"n{i + 1}", s["id"]) for i, s in enumerate(part)]
    return text, aliases


def count_diagram(lines: Sequence) -> tuple:
    """``(nodes, edges)`` declared in the lines of a Mermaid block (what SV-214 compares with the model)."""
    return sum(1 for ln in lines if _NODE.match(ln)), sum(1 for ln in lines if _EDGE.search(ln))


def build_nav(model: Mapping, *, project: str, lang: str, ui_sha8: str, model_sha8: "str | None") -> list:
    """The navigation pages: ``[(path, text, anchors, page id)]``."""
    screens = list(model.get("screens", ()))
    nav_rows = list(model.get("nav", ()))
    t = lambda key, **v: labels.t(lang, key, **v)  # noqa: E731
    declared, unreachable, dead = reachability(model)

    def blocks_for(part: list, count: int, page_of: Mapping) -> list:
        inside = {s["id"] for s in part}
        text, aliases = diagram(part, nav_rows)
        edges = count_diagram(text.split("\n"))[1]
        blocks: list = []
        if count > 1:
            blocks.append(idx.shards_line("NAV", count, lang, "nav.shards"))
        blocks.append("## " + t("card.heading", title=t("nav.t.graph"), n=edges))
        blocks.append(("fence", "mermaid", text.rstrip("\n").split("\n")))
        starts = {s["id"] for s in part if s.get("start")}
        alias_table = cardmod.Table(t("nav.t.alias"), [t("nav.col.alias"), t("nav.col.screen"), t("nav.col.start")],
                                    [[a, cardmod.Code(sid), t("nav.start") if sid in starts else ""] for a, sid in aliases], [""] * len(aliases), "")
        mine = [n for n in nav_rows if n["fromScreenId"] in inside]
        # the id of a row is ``<from>:go:<to>`` unless the author gave an alias or the id was long (a hash): only then do the ends need columns of their own
        ends = any(n["id"] != f"{n['fromId']}:go:{n['toId']}" for n in mine)
        rows, row_ids = [], []
        for n in mine:
            target_page = "" if n["toScreenId"] in inside else reading.page_ref(page_of[n["toScreenId"]])
            tail = [tf.card_text(n.get("label", "")), tf.card_text(n.get("guard", "")), n.get("on", "tap"), cardmod.nav_links(n), target_page]
            rows.append([cardmod.Code(n["id"])] + ([cardmod.Code(n["fromId"]), cardmod.Code(n["toId"])] if ends else []) + tail)
            row_ids.append(n["id"])
        columns = ("id", "from", "to", "label", "guard", "on", "links", "page") if ends else ("id", "label", "guard", "on", "links", "page")
        table = cardmod.Table(t("nav.t.table"), [t("nav.col." + k) for k in columns], rows, row_ids, "nav")
        blocks.extend([alias_table, table])
        if declared:
            mine = [x for x in unreachable if x in inside]
            blocks.append(cardmod.Table(t("nav.t.unreach"), [t("nav.col.screen")], [[cardmod.Code(x)] for x in mine], [""] * len(mine), ""))
        else:
            blocks.append(t("nav.nostart"))
        mine = [x for x in dead if x in inside]
        blocks.append(cardmod.Table(t("nav.t.dead"), [t("nav.col.screen")], [[cardmod.Code(x)] for x in mine], [""] * len(mine), ""))
        return blocks

    covers_args = {"s": len(screens), "n": len(nav_rows)}

    def meta_for(i: int, count: int, edges: int) -> dict:
        return idx.envelope_meta("NAV", i, count, project=project, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8,
                                 covers=t("nav.covers", k=edges, **covers_args), excludes=t("nav.excludes"), howto=t("nav.howto"))

    def fits(part: list, count: int) -> bool:
        lines, _a = _lines(blocks_for(part, count, {s["id"]: f"NAV-{count}" for s in screens}), lang)
        text, _h = idx.render_page(meta_for(count - 1, count, len(nav_rows)), lines)
        return len(text.encode("utf-8")) <= reading.BUDGETS["cap_md"]["card"]

    parts = idx.partition(screens, idx.NAV_SPLIT_AT, fits)
    ids = idx.page_ids("NAV", len(parts))
    page_of = {s["id"]: ids[i] for i, part in enumerate(parts) for s in part}
    out = []
    for i, part in enumerate(parts):
        lines, anchors = _lines(blocks_for(part, len(parts), page_of), lang)
        inside = {s["id"] for s in part}
        edges = sum(1 for n in nav_rows if n["fromScreenId"] in inside and n["toScreenId"] in inside)
        text, head_len = idx.render_page(meta_for(i, len(parts), edges), lines)
        out.append((idx.page_path("NAV", i), text, [(rid, kind, head_len + r + 1, head_len + h + 1) for rid, kind, r, h in anchors], ids[i]))
    return out


def _lines(blocks: Sequence, lang: str) -> tuple:
    """``idx.blocks_lines`` extended by a code block (``("fence", language, lines)``)."""
    lines: list = []
    anchors: list = []
    plain: list = []

    def flush():
        got, an = idx.blocks_lines(plain, lang)
        for rid, kind, r, h in an:
            anchors.append((rid, kind, len(lines) + r, len(lines) + h))
        lines.extend(got + ([""] if got else []))
        plain.clear()

    for block in blocks:
        if isinstance(block, tuple) and block and block[0] == "fence":
            flush()
            lines.extend(["```" + block[1], *block[2], "```", ""])
        else:
            plain.append(block)
    flush()
    while lines and lines[-1] == "":
        lines.pop()
    return lines, anchors
