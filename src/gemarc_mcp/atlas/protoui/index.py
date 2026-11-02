"""``screens/INDEX.md`` (page ``§SCREENS``) and ``screens/build-info.json`` (design A3 4.5, 5.4, 7.1).

The index lists every screen once (id, title, route, frame, start, state and widget counts, check points, navigation out and in, the bytes of
its SVGs, a link to its card), the requirement coverage, and says what it leaves out. More than 30 screens are cut into parts by ``group``
(parts are named ``INDEX-2.md``... in group order, every part links all parts); a screen list without groups that is still too large for a
card (6,000 bytes) is cut by halves in model order (L-class degradation: the build is ``ok`` and says so). ``shard_plan`` is the planner both
the index and the navigation page use. ``build_info`` is the trust root of the screens tree: it is not in the manifest and holds no time and
no path. Standard library only.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping, Sequence

from ..reading import api as reading
from . import card as cardmod
from . import labels
from . import model as um
from . import textfit as tf

GROUP_SPLIT_AT = 30          # more screens than this: parts by group
NAV_SPLIT_AT = 40


def shard_plan(screens: Sequence, limit: int) -> list:
    """Parts of a screen list: one part when there are at most ``limit`` screens, else one part per ``group`` (sorted by name, screens without a
    group last, a group larger than ``limit`` cut into runs in model order), else runs of ``limit`` in sorted id order... model order is kept
    inside every part."""
    screens = list(screens)
    if len(screens) <= limit:
        return [screens]
    groups: dict = {}
    for s in screens:
        groups.setdefault(s.get("group") or "", []).append(s)
    if len(groups) > 1 or "" not in groups:
        names = sorted(g for g in groups if g) + ([""] if "" in groups else [])
        parts = []
        for name in names:
            members = groups[name]
            parts.extend(members[i:i + limit] for i in range(0, len(members), limit))
        return parts
    order = sorted(range(len(screens)), key=lambda i: screens[i]["id"])
    runs = [sorted(order[i:i + limit]) for i in range(0, len(order), limit)]
    return [[screens[i] for i in run] for run in runs]


def split_to_fit(part: list, fits: Callable) -> list:
    """``part`` itself when ``fits(part)`` else its two halves (model order), recursively; a single screen is returned as it is."""
    if len(part) <= 1 or fits(part):
        return [part]
    middle = len(part) // 2
    return split_to_fit(part[:middle], fits) + split_to_fit(part[middle:], fits)


def partition(screens: Sequence, limit: int, fits: Callable) -> list:
    """The parts of a screen list: ``shard_plan``, each part cut further by halves until ``fits(part, count)`` holds, where ``count`` is the number
    of parts the page will have (the parts link each other, so the count is part of the page); repeated until the count is stable."""
    parts = shard_plan(screens, limit)
    for _round in range(len(screens) + 2):
        count = len(parts)
        refined: list = []
        for part in parts:
            refined.extend(split_to_fit(part, lambda p, n=count: fits(p, n)))
        if len(refined) == count:
            break
        parts = refined
    return parts


def page_ids(family: str, count: int) -> list:
    return [family if i == 0 else f"{family}-{i + 1}" for i in range(count)]


def page_path(family: str, i: int) -> str:
    name = "INDEX" if family == "SCREENS" else "NAV"
    return f"screens/{name}" + ("" if i == 0 else f"-{i + 1}") + ".md"


def _href(family: str, i: int) -> str:
    return page_path(family, i).split("/", 1)[1]


def shards_line(family: str, count: int, lang: str, key: str) -> str:
    links = [f"[{reading.page_ref(pid)}]({_href(family, i)})" for i, pid in enumerate(page_ids(family, count))]
    return labels.t(lang, key, list=" · ".join(links))


def envelope_meta(family: str, i: int, count: int, *, project: str, lang: str, ui_sha8: str, model_sha8: "str | None", covers: str, excludes: str,
                  howto: str) -> dict:
    ids = page_ids(family, count)
    meta = {"page": ids[i], "index": i + 1, "count": count, "cls": "card", "project": project, "lang": lang, "ui_stamp": ui_sha8, "content": "prototype",
            "covers": covers, "excludes": excludes, "howto": howto, "start": "../START.md"}
    if model_sha8:
        meta["stamp"] = model_sha8
    if i > 0:
        meta["prev"] = (ids[i - 1], _href(family, i - 1))
    if i < count - 1:
        meta["next"] = (ids[i + 1], _href(family, i + 1))
    return meta


def render_page(meta: dict, body: Sequence) -> tuple:
    """``(text, head length)`` of a page made of ``body`` lines inside the envelope of ``meta``."""
    head, foot = reading.envelope(meta)
    return reading.finalize(head + list(body) + foot), len(head)


def blocks_lines(blocks: Sequence, lang: str) -> tuple:
    """Body lines of a list of ``Table`` and ``str`` blocks; ``(lines, anchors)`` with anchors ``(row id, kind, row index, heading index)``."""
    lines: list = []
    anchors: list = []
    for block in blocks:
        if isinstance(block, str):
            lines.extend(tf.fold(block, labels.t(lang, "card.cont")) + [""])
            continue
        if not block.rows:
            lines.extend([block.heading(lang), labels.t(lang, "card.none"), ""])
            continue
        body, offsets = cardmod.table_lines(block, lang)
        for j, off in enumerate(offsets):
            if block.ids[j]:
                anchors.append((block.ids[j], block.kind, len(lines) + off, len(lines)))
        lines.extend(body + [""])
    while lines and lines[-1] == "":
        lines.pop()
    return lines, anchors


# ---------------------------------------------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------------------------------------------


def _cardinality(card_pages: Sequence, enc_name: str) -> str:
    first = f"[{reading.page_ref(card_pages[0])}]({enc_name}.card.md)"
    return first + (f" +{len(card_pages) - 1}" if len(card_pages) > 1 else "")


def requirement_coverage(model: Mapping, screens: Sequence) -> list:
    """``[(requirement id, [screen ids])]`` over the requirement links of the screens, their widgets and navigation rows, sorted by id."""
    inside = {s["id"] for s in screens}
    out: dict = {}
    for s in screens:
        for r in s.get("reqIds", ()):
            out.setdefault(r, set()).add(s["id"])
    for w in model.get("widgets", ()):
        if w["screenId"] in inside:
            for r in w.get("reqIds", ()):
                out.setdefault(r, set()).add(w["screenId"])
    for n in model.get("nav", ()):
        if n["fromScreenId"] in inside:
            for r in n.get("reqIds", ()):
                out.setdefault(r, set()).add(n["fromScreenId"])
    return [(r, sorted(v)) for r, v in sorted(out.items())]


def build_index(model: Mapping, rows: Mapping, *, project: str, lang: str, ui_sha8: str, model_sha8: "str | None") -> list:
    """The index pages: ``[(path, text, anchors, page id)]``. ``rows`` maps a screen id to its facts: ``{"states", "widgets", "checkpoints", "svg",
    "enc", "cards": [card page ids]}``. A single screen in a part is never cut further."""
    screens = list(model.get("screens", ()))
    t = lambda key, **v: labels.t(lang, key, **v)  # noqa: E731
    sums = {"s": len(screens), "v": sum(r["states"] for r in rows.values()), "w": sum(r["widgets"] for r in rows.values()),
              "c": sum(r["checkpoints"] for r in rows.values()), "n": len(model.get("nav", ()))}
    nav_out: dict = {}
    nav_in: dict = {}
    for n in model.get("nav", ()):
        nav_out[n["fromScreenId"]] = nav_out.get(n["fromScreenId"], 0) + 1
        nav_in[n["toScreenId"]] = nav_in.get(n["toScreenId"], 0) + 1

    def blocks_for(part: list, count: int) -> list:
        table_rows, ids = [], []
        for s in part:
            r = rows[s["id"]]
            table_rows.append([cardmod.Code(s["id"]), tf.card_text(s.get("title", "")), s.get("route", ""), s.get("frame", "desktop"), "true" if s.get("start") else "",
                               str(r["states"]), str(r["widgets"]), str(r["checkpoints"]), f"{nav_out.get(s['id'], 0)}/{nav_in.get(s['id'], 0)}", str(r["svg"]),
                               _cardinality(r["cards"], r["enc"])])
            ids.append(s["id"])
        screens_table = cardmod.Table(t("index.t.screens"), [t("index.col." + k) for k in ("id", "title", "route", "frame", "start", "states", "widgets", "checkpoints",
                                                                                               "nav", "svg", "card")], table_rows, ids, "screen")
        coverage = requirement_coverage(model, part)
        req_table = cardmod.Table(t("index.t.req"), [t("index.col.req"), t("index.col.screens")],
                                  [[cardmod.Code(r), ", ".join(v)] for r, v in coverage], [""] * len(coverage), "")
        blocks: list = []
        if count > 1:
            blocks.append(shards_line("SCREENS", count, lang, "index.shards"))
        return blocks + [screens_table, req_table]

    meta_args = dict(project=project, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8, covers=t("index.covers", **sums), excludes=t("index.excludes"),
                     howto=t("index.howto"))

    def fits(part: list, count: int) -> bool:
        lines, _a = blocks_lines(blocks_for(part, count), lang)
        text, _h = render_page(envelope_meta("SCREENS", count - 1, count, **meta_args), lines)
        return len(text.encode("utf-8")) <= reading.BUDGETS["cap_md"]["card"]

    parts = partition(screens, GROUP_SPLIT_AT, fits)
    out = []
    for i, part in enumerate(parts):
        lines, anchors = blocks_lines(blocks_for(part, len(parts)), lang)
        text, head_len = render_page(envelope_meta("SCREENS", i, len(parts), **meta_args), lines)
        out.append((page_path("SCREENS", i), text, [(rid, kind, head_len + r + 1, head_len + h + 1) for rid, kind, r, h in anchors], page_ids("SCREENS", len(parts))[i]))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Build info
# ---------------------------------------------------------------------------------------------------------------


def build_info(*, ui_sha8: str, model_sha8: "str | None", states: int, screens: int, widgets: int, checkpoints: int, status: str, degraded: Sequence) -> bytes:
    """``screens/build-info.json`` (schema ``gemarc-atlas-ui-build/1``): canonical JSON, LF, no time, no path."""
    info = {"schema": um.BUILD_SCHEMA, "uiSha8": ui_sha8, "modelSha8": model_sha8, "layoutVersion": um.LAYOUT_VERSION, "states": states, "screens": screens,
            "widgets": widgets, "checkpoints": checkpoints, "status": status, "degraded": sorted(set(degraded))}
    return (json.dumps(info, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
