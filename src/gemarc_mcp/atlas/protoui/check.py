"""The build-time checks of the screens tree, ``SV-201`` to ``SV-216`` (design A3 6.2; classes in 6.0).

The checks read the bytes of the files (an SVG is parsed with an XML parser, a card as text), never the in-memory layout objects, so a mutated file is
caught. They are not rules of the shared registry (the 5D wave-1 count is pinned) and not author-side rules (those are ``UX-*``); they are the
generator's own consistency checks.

``I`` class (integrity): can only fail through a bug of the generator; an I-class ``ERROR`` makes the tree invalid. ``L`` class (input limit): the
size and length limits an author's data can reach; the builder degrades, the check only reports (``WARN``). ``T`` class (test only, ``include_t``):
clauses that are not yet validated enough to veto a build (the marks of SV-205).

``SV-208`` (the PNG probe) is a render-time check and belongs to ``png.py``; it is not run here. The index page and the cards are covered by SV-207, the
navigation page by SV-214, the galleries by SV-213, the SVG files by SV-201..206, SV-209..211, SV-215, SV-216. When no specific rule explains a file
that differs from a fresh regeneration (``regenerate=True``), the generic finding of the file's family is reported (SVG: SV-211, card and index:
SV-207, navigation: SV-214, gallery: SV-213, build-info: SV-212).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Mapping, Sequence

from ..reading import api as reading
from . import build as bld
from . import card as cardmod
from . import expand as ex
from . import gallery as gal
from . import labels
from . import layout as lay
from . import model as um
from . import nav as navmod
from . import svg as svgmod
from . import textfit as tf
from . import vocab

SVG_NS = "{http://www.w3.org/2000/svg}"
def has_cjk(text: str) -> bool:
    return any(tf.is_wide(ch) and ch != tf.ELLIPSIS for ch in text)

CJK_FONTS = ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC")
RULES = {
    "SV-201": "I", "SV-202": "I", "SV-203": "I", "SV-204": "L", "SV-205": "I", "SV-206": "I", "SV-207": "I", "SV-208": "I", "SV-209": "I", "SV-210": "I",
    "SV-211": "I", "SV-212": "I", "SV-213": "I", "SV-214": "I", "SV-215": "I", "SV-216": "I",
}


class _Out:
    def __init__(self):
        self.items: list = []

    def add(self, rule: str, path: str, message: str, severity: "str | None" = None, cls: "str | None" = None) -> None:
        cls = cls or RULES[rule]
        severity = severity or ("ERROR" if cls == "I" else "WARN")
        item = bld.Finding(rule, cls, severity, path, message)
        if item not in self.items:
            self.items.append(item)

    def errors_at(self, path: str) -> bool:
        return any(f.path == path and f.severity == "ERROR" for f in self.items)


def _local(tag: str) -> str:
    return tag[len(SVG_NS):] if tag.startswith(SVG_NS) else tag


def _box(value: "str | None") -> "tuple | None":
    try:
        parts = tuple(int(x) for x in (value or "").split())
    except ValueError:
        return None
    return parts if len(parts) == 4 else None


def _rect_box(rect) -> "tuple | None":
    """``(x, y, width, height)`` of a ``<rect>`` as integers, ``None`` when an attribute is missing or not a whole number."""
    try:
        values = tuple(float(rect.get(k)) for k in ("x", "y", "width", "height"))
    except (TypeError, ValueError):
        return None
    return tuple(int(v) for v in values) if all(v == int(v) for v in values) else None


def _inside(a: tuple, p: tuple) -> bool:
    return a[0] >= p[0] and a[1] >= p[1] and a[0] + a[2] <= p[0] + p[2] and a[1] + a[3] <= p[1] + p[3]


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _text_of(element) -> str:
    return "".join(element.itertext())


def parse_css(css: str) -> list:
    """``[(class names, {property: value})]`` of a flat list of class rules, in order (later rules win)."""
    rules = []
    for block in css.split("}"):
        if "{" not in block:
            continue
        selector, body = block.split("{", 1)
        names = [s.strip()[1:] for s in selector.split(",") if s.strip().startswith(".")]
        decls = {}
        for decl in body.split(";"):
            if ":" in decl:
                key, value = decl.split(":", 1)
                decls[key.strip()] = value.strip()
        rules.append((names, decls))
    return rules


def css_value(rules: Sequence, classes: str, prop: str) -> "str | None":
    wanted = set(classes.split())
    value = None
    for names, decls in rules:
        if prop in decls and wanted & set(names):
            value = decls[prop]
    return value


def visible_ids(index: lay.ModelIndex, screen: Mapping, variant: str) -> list:
    """The ids of the widgets a state shows, in pre-order: no ``in`` that leaves the state out, no hidden parent, not deeper than the layout draws."""
    seen: dict = {}
    for wid in screen.get("widgetIds", ()):
        w = index.widgets.get(wid)
        if w is None:
            continue
        parent = w.get("parentId")
        own = w.get("inVariants")
        seen[wid] = (parent is None or seen.get(parent, False)) and w.get("depth", 0) <= lay.MAX_DEPTH and (own is None or variant in own)
    return [wid for wid in screen.get("widgetIds", ()) if seen.get(wid)]


# ---------------------------------------------------------------------------------------------------------------
# One SVG
# ---------------------------------------------------------------------------------------------------------------


def check_svg(path: str, text: str, ctx: "_Ctx", sid: str, variant: str, out: _Out, include_t: bool = False) -> None:
    index, model = ctx.index, ctx.model
    screen = index.screens[sid]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        out.add("SV-201", path, f"not well-formed XML: {exc}")
        return
    frame = screen.get("frame", vocab.DEFAULT_FRAME)
    width = vocab.FRAMES[frame][0]
    groups = [e for e in root.iter() if _local(e.tag) == "g"]
    by_id = {g.get("id"): g for g in groups if g.get("id")}
    root_g = by_id.get(um.root_id(sid))
    band_g = by_id.get(f"{sid}.state")
    ids_seen: dict = {}
    for e in root.iter():
        i = e.get("id")
        if i is not None:
            ids_seen[i] = ids_seen.get(i, 0) + 1

    # ---- SV-201: the root element
    if _local(root.tag) != "svg":
        out.add("SV-201", path, "the root element is not <svg>")
        return
    for key in ("viewBox", "width", "height", "role", "aria-label", "data-schema", "data-screen", "data-variant", "data-frame", "data-layout", "data-ui-sha8"):
        if root.get(key) in (None, ""):
            out.add("SV-201", path, f"the root has no {key}")
    if root.get("role") != "group":
        out.add("SV-201", path, 'the root role is not "group"')
    expected = {"data-schema": svgmod.SCHEMA, "data-screen": sid, "data-variant": variant, "data-frame": frame, "data-layout": str(lay.LAYOUT_VERSION), "data-ui-sha8": ctx.ui_sha8}
    for key, want in expected.items():
        if root.get(key) not in (None, "") and root.get(key) != want:
            out.add("SV-201", path, f"{key} is {root.get(key)!r}, expected {want!r}")
    if ctx.model_sha8:
        if root.get("data-model-sha8") != ctx.model_sha8:
            out.add("SV-201", path, "data-model-sha8 is missing or differs although the model has validated links")
    elif root.get("data-model-sha8") is not None:
        out.add("SV-201", path, "data-model-sha8 is present although the model has no validated link")
    for e in root.iter():
        if any(k.startswith("aria-") and k != "aria-label" for k in e.attrib):
            out.add("SV-201", path, "an aria-* attribute other than aria-label (a reference to another element by id) is not written")
            break
    root_box = _box(root_g.get("data-box")) if root_g is not None else None
    band_box = _box(band_g.get("data-box")) if band_g is not None else None
    if root_box is None or band_box is None:
        out.add("SV-201", path, "the root group or the state band is missing")
        return
    want_box = f"0 0 {root_box[2]} {root_box[3] + band_box[3]}"
    if root.get("viewBox") != want_box or root.get("width") != str(root_box[2]) or root.get("height") != str(root_box[3] + band_box[3]):
        out.add("SV-201", path, f"viewBox {root.get('viewBox')!r}, width {root.get('width')!r}, height {root.get('height')!r} do not agree with the canvas {root_box[2]}x{root_box[3]} plus the band {band_box[3]}")
    if root_box[2] != width or root_box[3] < vocab.FRAMES[frame][1]:
        out.add("SV-201", path, f"the canvas {root_box[2]}x{root_box[3]} is smaller than the frame {frame}")
    children = {_local(e.tag): e for e in root if _local(e.tag) in ("title", "desc")}
    title, desc = children.get("title"), children.get("desc")
    if title is None or desc is None or not _text_of(title).strip():
        out.add("SV-201", path, "<title> or <desc> is missing")
    else:
        words = (labels.raw(ctx.lang, "svg.word.contains"), labels.raw(ctx.lang, "svg.word.excludes"))
        body = _text_of(desc)
        if not all(w in body for w in words):
            out.add("SV-201", path, "the <desc> lacks the contains and excludes parts")
        pattern = re.escape(labels.raw(ctx.lang, "svg.desc.contains")).replace(r"\{n\}", r"(?P<n>\d+)", 1).replace(r"\{n\}", r"(?P=n)").replace(r"\{kinds\}", ".*?").replace(r"\{k\}", r"(?P<k>\d+)")
        m = re.search(pattern, body)
        shown = sum(1 for g in groups if g.get("data-n") is not None)
        if m is None or int(m.group("k")) != shown or int(m.group("n")) != len(screen.get("widgetIds", ())):
            out.add("SV-201", path, "the <desc> does not state the shown and total widget counts of the state")
        if labels.raw(ctx.lang, "svg.desc.geometry").strip(". ") not in body:
            out.add("SV-201", path, "the <desc> lacks the sentence that the geometry is only a sketch")

    # ---- SV-202: ids and boxes
    for i, n in ids_seen.items():
        if n > 1:
            out.add("SV-202", path, f"the id {i!r} is used {n} times")
    for g in groups:
        if g.get("data-kind") is None:
            continue
        gid = g.get("id")
        if not gid:
            out.add("SV-202", path, "a group with a data-kind has no id")
            continue
        local = gid[len(sid) + 1:] if gid.startswith(sid + ".") else None
        if local is None or (local in ("root", "state") and g.get("data-kind") not in ("screen", "state-label")):
            out.add("SV-202", path, f"the id {gid!r} is not <screen>.<local>")
        box = _box(g.get("data-box"))
        rect = next((c for c in g if _local(c.tag) == "rect"), None)
        if box is None or rect is None or _rect_box(rect) != box:
            out.add("SV-202", path, f"the data-box of {gid!r} is not the box of its first rect")
            continue
        if g.get("data-n") is not None:
            parent = by_id.get(g.get("data-parent"))
            pbox = _box(parent.get("data-box")) if parent is not None else None
            if pbox is None:
                out.add("SV-202", path, f"the data-parent of {gid!r} names no group")
            elif not _inside(box, pbox):
                out.add("SV-202", path, f"the box of {gid!r} is not inside the box of its parent")

    # ---- SV-203: no text turned into paths, no images, scripts or external references; all text in <text>
    for e in root.iter():
        name = _local(e.tag)
        if name in ("image", "foreignObject", "script"):
            out.add("SV-203", path, f"<{name}> is not allowed")
        for key, value in e.attrib.items():
            if key.endswith("href") or (key in ("fill", "stroke", "style", "filter", "clip-path", "mask") and "url(" in value):
                out.add("SV-203", path, f"an external reference in {key!r}")
        if name == "style" and ("url(" in _text_of(e) or "@import" in _text_of(e)):
            out.add("SV-203", path, "an external reference in the style")
        if name not in ("text", "tspan", "title", "desc", "style") and (e.text or "").strip():
            out.add("SV-203", path, f"visible text sits outside <text> in <{name}>")
    path_bytes = sum(len(line.encode("utf-8")) + 1 for line in text.split("\n") if line.startswith("<path"))
    if path_bytes * 4 > len(text.encode("utf-8")):
        out.add("SV-203", path, "<path> elements are more than 25 percent of the file")

    # ---- SV-204 (L): size and sample rows
    if len(text.encode("utf-8")) > reading.BUDGETS["cap_svg"]:
        out.add("SV-204", path, f"the SVG is {len(text.encode('utf-8'))} bytes, above {reading.BUDGETS['cap_svg']}", "WARN", "L")
    for g in groups:
        if g.get("data-sample") == "true":
            rows = {t.get("y") for t in g.iter() if _local(t.tag) == "text" and t.get("data-mark") is None and t.get("data-derived") is None}
            if len(rows) - 1 > lay.MAX_SAMPLE_ROWS:
                out.add("SV-204", path, f"{g.get('id')!r} draws more than {lay.MAX_SAMPLE_ROWS} sample rows", "WARN", "L")

    # ---- SV-205: siblings do not overlap (I), marks do not cover another widget (T)
    widgets = [g for g in groups if g.get("data-n") is not None]
    by_parent: dict = {}
    for g in widgets:
        by_parent.setdefault(g.get("data-parent"), []).append(g)
    for parent, kids in by_parent.items():
        boxes = [(g.get("id"), _box(g.get("data-box"))) for g in kids]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if boxes[i][1] and boxes[j][1] and _overlap(boxes[i][1], boxes[j][1]):
                    out.add("SV-205", path, f"the boxes of {boxes[i][0]!r} and {boxes[j][0]!r} overlap")
    if include_t:
        all_boxes = {g.get("id"): (_box(g.get("data-box")), g.get("data-parent"), g.get("data-kind")) for g in widgets}
        for g in widgets:
            for c in g:
                if _local(c.tag) != "circle":
                    continue
                try:
                    cx, cy, r = float(c.get("cx")), float(c.get("cy")), float(c.get("r"))
                except (TypeError, ValueError):
                    out.add("SV-205", path, f"a mark of {g.get('id')!r} has no numeric position")
                    continue
                ring = (int(cx - r), int(cy - r), int(2 * r), int(2 * r))
                chain, cur = set(), g.get("id")
                while cur in all_boxes:
                    chain.add(cur)
                    cur = all_boxes[cur][1]
                for oid, (obox, _parent, kind) in all_boxes.items():
                    if oid not in chain and kind != "divider" and obox and _overlap(ring, obox):
                        out.add("SV-205", path, f"the mark of {g.get('id')!r} covers the box of {oid!r}", "ERROR", "T")

    # ---- SV-206: jumps and links exist
    screens_ids = {s["id"] for s in model.get("screens", ())}
    variants_ids = {v["id"] for v in model.get("variants", ())}
    nav_ids = {n["id"] for n in model.get("nav", ())}
    five = ctx.five if ctx.five is not None and not getattr(ctx.five, "empty", True) else None
    for g in groups:
        for target in filter(None, (g.get("data-goto") or "").split(",")):
            if target not in screens_ids and target not in variants_ids and not (target.endswith("#default") and target.split("#")[0] in screens_ids):
                out.add("SV-206", path, f"data-goto {target!r} is not a screen or a state of the model")
        for nid in filter(None, (g.get("data-nav") or "").split(",")):
            if nid not in nav_ids:
                out.add("SV-206", path, f"data-nav {nid!r} is not a navigation of the model")
        if five is not None:
            for key, known in (("data-api", five.nodes.keys() | five.edges), ("data-req", five.requirements), ("data-case", five.cases.keys()),
                               ("data-rule", set().union(*five.cases.values()) if five.cases else set())):
                for value in filter(None, (g.get(key) or "").split(",")):
                    if value not in known:
                        out.add("SV-206", path, f"{key} {value!r} is not in the 5D model")

    # ---- SV-209, SV-210: sizes, targets, fonts
    css = next((_text_of(e) for e in root if _local(e.tag) == "style"), "")
    rules = parse_css(css)
    minimum = lay.min_target(frame)
    for t in root.iter():
        if _local(t.tag) != "text":
            continue
        cls = t.get("class", "")
        size = css_value(rules, cls, "font-size")
        if size is None or not size.endswith("px") or not size[:-2].isdigit() or int(size[:-2]) < 12:
            out.add("SV-209", path, f"a text with class {cls!r} has font-size {size!r}, below 12px")
        if has_cjk(_text_of(t)):
            family = css_value(rules, cls, "font-family") or ""
            if not any(f in family for f in CJK_FONTS):
                out.add("SV-210", path, f"a text with class {cls!r} holds CJK characters but its font stack has no CJK font")
    interactive = []
    for g in widgets:
        if g.get("data-kind") in vocab.INTERACTIVE:
            box = _box(g.get("data-box"))
            if box and (box[2] < minimum[0] or box[3] < minimum[1]):
                out.add("SV-209", path, f"the interactive box of {g.get('id')!r} is {box[2]}x{box[3]}, below {minimum[0]}x{minimum[1]}")
            interactive.append((g.get("id"), box))
    for i in range(len(interactive)):
        for j in range(i + 1, len(interactive)):
            if interactive[i][1] and interactive[j][1] and _overlap(interactive[i][1], interactive[j][1]):
                out.add("SV-209", path, f"the interactive boxes of {interactive[i][0]!r} and {interactive[j][0]!r} overlap")

    # ---- SV-211: the round trip
    want = {}
    for wid in visible_ids(index, screen, variant):
        w = index.widgets[wid]
        navs = index.nav_from.get(wid, ())
        want[wid] = (tf.author(w.get("label", "")), w["kind"], w.get("parentId") or um.root_id(sid), w.get("bind", ""), ",".join(n["toId"] for n in navs))
    got = {g.get("id"): (g.get("aria-label", ""), g.get("data-kind"), g.get("data-parent"), g.get("data-bind", ""), g.get("data-goto", "")) for g in widgets}
    if want != got:
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        differ = sorted(k for k in set(want) & set(got) if want[k] != got[k])
        out.add("SV-211", path, f"the widgets read back from the SVG differ from the model state (missing {missing[:3]}, extra {extra[:3]}, different {differ[:3]})")

    # ---- SV-215, SV-216
    vrec = index.variants.get(sid, {}).get(variant)
    note = tf.author(vrec["note"])[:lay.NOTE_CHARS] if vrec else ""
    band_texts = [_text_of(e) for e in band_g if _local(e.tag) == "text"]
    got_note = band_texts[1] if len(band_texts) > 1 else ""
    if tf.squash(got_note) != tf.squash(note):
        out.add("SV-215", path, "the second line of the state band is not the variant note")
    order = {wid: i + 1 for i, wid in enumerate(screen.get("widgetIds", ()))}
    for g in widgets:
        if g.get("data-n") != str(order.get(g.get("id"), -1)):
            out.add("SV-216", path, f"data-n of {g.get('id')!r} is {g.get('data-n')!r}, expected {order.get(g.get('id'))}")


# ---------------------------------------------------------------------------------------------------------------
# Cards, index, navigation, galleries
# ---------------------------------------------------------------------------------------------------------------


class _Ctx:
    def __init__(self, model: Mapping, five, cfg: "bld.ScreensConfig"):
        self.model = model
        self.five = five
        self.cfg = cfg
        self.lang = cfg.lang
        self.index = lay.ModelIndex(model)
        self.ui_sha8 = um.ui_sha8(model)
        self.model_sha8 = cfg.model_sha8 if bld.has_validated_links(model, five) else None
        self.enc_of = reading.enc_many([s["id"] for s in model.get("screens", ())])


def _card_texts(files: Mapping, enc: str) -> "list | None":
    lines: list = []
    part = 1
    while True:
        name = f"screens/{reading.card_path(enc, part).split('/', 1)[1]}"
        if name not in files:
            break
        lines.extend(files[name].decode("utf-8").split("\n"))
        part += 1
    return lines or None


def _table_rows(lines: Sequence, heading_title: str, lang: str = "zh-CN") -> list:
    """The cell lists of every table whose heading starts with ``heading_title``, header rows left out (a continuation page repeats the header),
    ``see note [k]`` cells replaced by the note under the table; the parts of a card are concatenated by the caller."""
    lines = tf.unfold(list(lines), labels.raw(lang, "card.cont"))
    out: list = []
    pending: list = []
    notes: dict = {}
    header = None
    inside = False

    def close() -> None:
        refs = {labels.t(lang, "card.note", k=k): v for k, v in notes.items()}
        out.extend([refs.get(c, c) for c in cells] for cells in pending)
        pending.clear()
        notes.clear()

    for line in lines:
        if line.startswith("## "):
            close()
            inside = line.startswith("## " + heading_title)
            continue
        if not inside:
            continue
        if line.startswith("| ") and not line.startswith("|---"):
            cells = [c.strip() for c in line[2:-2].split(" | ")]
            if header is None:
                header = cells
            elif cells != header:
                pending.append(cells)
        elif line.startswith("[") and "] " in line[:8]:
            notes[line[1:line.index("]")]] = line[line.index("] ") + 2:]
    close()
    return out


def check_card(ctx: _Ctx, sid: str, files: Mapping, svg_roots: Mapping, out: _Out) -> None:
    enc = ctx.enc_of[sid]
    paths = [f"screens/{reading.card_path(enc, p).split('/', 1)[1]}" for p in range(1, 1000) if f"screens/{reading.card_path(enc, p).split('/', 1)[1]}" in files]
    if not paths:
        out.add("SV-207", f"screens/{enc}.card.md", f"the screen {sid!r} has no card")
        return
    lines: list = []
    for p in paths:
        text = files[p].decode("utf-8")
        for problem in reading.verify_sentinel(text):
            out.add("SV-207", p, f"sentinel: {problem}")
        lines.extend(text.split("\n"))
    lang = ctx.lang
    screen = ctx.index.screens[sid]
    # the controls table against the ids of the SVGs
    widget_rows = _table_rows(lines, labels.raw(lang, "card.t.widgets"), lang)
    table = {}
    for cells in widget_rows:
        if len(cells) >= 2:
            table[cells[1].strip("`")] = cells[0]
    union: dict = {}
    for root in svg_roots.values():
        for g in root.iter():
            if _local(g.tag) == "g" and g.get("data-n") is not None:
                union[g.get("id")] = g.get("data-n")
    never = set(screen.get("widgetIds", ())) - {wid for v in screen["variantNames"] for wid in visible_ids(ctx.index, screen, v)}
    if len(svg_roots) < len(screen["variantNames"]):
        pass                                                     # a missing SVG is reported by SV-211; the union of the states cannot be formed
    elif set(table) != set(union) | never:
        out.add("SV-207", paths[0], f"the widget table lists {len(table)} ids, the SVGs hold {len(union)} (different: {sorted(set(table) ^ (set(union) | never))[:3]})")
    elif len(svg_roots) == len(screen["variantNames"]) and any(table[k] != union[k] for k in union):
        out.add("SV-207", paths[0], "a # of the widget table differs from the data-n of the SVG")
    # the strings of the SVGs against the card
    strings = {tf.squash(s) for s in cardmod.visible_strings(lines, lang)}
    for root in svg_roots.values():
        for t in root.iter():
            if _local(t.tag) == "text" and t.get("data-mark") is None:
                if tf.cmp_form(_text_of(t)) not in strings:
                    out.add("SV-207", paths[0], f"the SVG text {_text_of(t)[:30]!r} is not in the visible text of the card")
                    break
    # the check points: their number, and the value of every one of them is on the card
    points = [c for c in um.checkpoints(ctx.model) if c.screen == sid]
    found = re.search(r"checkpoints=(\d+)", "\n".join(lines))
    if found is None or int(found.group(1)) != len(points):
        out.add("SV-207", paths[0], f"checkpoints={found.group(1) if found else None} on the card, {len(points)} by definition")
    by_widget = {r[1].strip("`"): r for r in widget_rows if len(r) > 1}
    by_order = {r[0].strip("`"): r[1] for r in _table_rows(lines, labels.raw(lang, "card.t.order"), lang) if len(r) > 1}
    by_state = {r[0].strip("`"): r for r in _table_rows(lines, labels.raw(lang, "card.t.states"), lang)}
    columns = {"kind": 2, "label": 3, "parent": 4, "in": 5, "bind": 6, "api": 7, "emph": 8, "goto": 9}
    text_of_card = "\n".join(lines)
    absent = []
    for point in points:
        host, _at, column = point.ref.rpartition("@")
        if column == "route":
            ok = f"route={point.value}" in text_of_card
        elif column == "order":
            ok = tf.squash(by_order.get(host, "")) == tf.squash(point.value)
        elif column == "state":
            ok = len(by_state.get(host, ())) > 1 and by_state[host][1] == point.value
        elif column in columns:
            row = by_widget.get(host)
            ok = row is not None and len(row) > columns[column] and tf.squash(row[columns[column]]) == tf.cmp_form(point.value)
        else:
            ok = False
        if not ok:
            absent.append(point.ref)
    if absent:
        out.add("SV-207", paths[0], f"{len(absent)} check point values are not on the card, the first are {absent[:3]}")


def check_gallery(ctx: _Ctx, sid: str, files: Mapping, out: _Out) -> None:
    enc = ctx.enc_of[sid]
    path = gal.path_of(enc)
    if path not in files:
        return
    text = files[path].decode("utf-8")
    lines = text.rstrip("\n").split("\n")
    if re.search(r"<script|<[^>]*\son[a-z]+\s*=|(?:href|src)\s*=\s*[\"']data:", text, re.I):
        out.add("SV-213", path, "the gallery has a script, an event attribute or a data: URI")
    figures: dict = {}
    i = 0
    current = None
    while i < len(lines):
        m = re.match(r'<figure id="(st-[^"]+)">', lines[i])
        if m:
            current = m.group(1)
            figures[current] = []
        elif lines[i] == "</figure>":
            current = None
        elif current is not None and not lines[i].startswith("<figcaption"):
            figures[current].append(lines[i])
        i += 1
    screen = ctx.index.screens[sid]
    if "st-default" not in figures:
        out.add("SV-213", path, "the default state is not inlined")
    for anchor, block in figures.items():
        variant = anchor[3:]
        svg_path = f"screens/{svgmod.file_name(enc, variant)}"
        if svg_path not in files:
            if not out.errors_at(svg_path):
                out.add("SV-213", path, f"the inlined state {variant!r} has no SVG file")
            continue
        if "\n".join(gal.strip_links(block)) + "\n" != files[svg_path].decode("utf-8") and not out.errors_at(svg_path):   # a bad SVG file is reported once, by its own rule
            out.add("SV-213", path, f"the inlined SVG of {variant!r} differs from {svg_path}")
    ids = set(re.findall(r'id="([^"]+)"', text))
    for href in re.findall(r'href="([^"]*)"', text):
        if href.startswith("#"):
            if href[1:] not in ids:
                out.add("SV-213", path, f"the link {href!r} has no target in the page")
        elif href.startswith("../../screens/"):
            if "screens/" + href[len("../../screens/"):] not in files:
                out.add("SV-213", path, f"the link {href!r} points to no file")
        elif href.endswith(".html") or ".html#" in href:
            target = "site/screens/" + href.split("#")[0]
            if target not in files and not re.match(r"(INDEX|NAV)(-\d+)?\.html$|\.\./START\.html$", href.split("#")[0]):
                out.add("SV-213", path, f"the link {href!r} points to no gallery")
    if len(text.encode("utf-8")) > gal.MAX_BYTES:
        out.add("SV-213", path, f"the gallery is {len(text.encode('utf-8'))} bytes, above {gal.MAX_BYTES}", "WARN", "L")
    if set(figures) - {f"st-{v}" for v in screen["variantNames"]}:
        out.add("SV-213", path, "the gallery inlines a state the screen does not have")


def check_nav(ctx: _Ctx, files: Mapping, out: _Out) -> None:
    lang = ctx.lang
    nav_paths = [p for p in sorted(files) if re.fullmatch(r"screens/NAV(-\d+)?\.md", p)]
    if not nav_paths:
        out.add("SV-214", "screens/NAV.md", "there is no navigation page")
        return
    nodes = edges = cross = rows_total = 0
    aliases_seen: set = set()
    screens_seen: list = []
    for p in nav_paths:
        text = files[p].decode("utf-8")
        for problem in reading.verify_sentinel(text):
            out.add("SV-214", p, f"sentinel: {problem}")
        lines = text.split("\n")
        block: list = []
        inside = False
        for line in lines:
            if line.startswith("```mermaid"):
                inside = True
                continue
            if inside and line == "```":
                inside = False
                continue
            if inside:
                block.append(line)
        n, e = navmod.count_diagram(block)
        declared = {re.match(r"^\s*(n\d+)", ln).group(1) for ln in block if navmod._NODE.match(ln)}
        table = _table_rows(lines, labels.raw(lang, "nav.t.alias"))
        listed = {c[0] for c in table}
        if declared != listed:
            out.add("SV-214", p, "the alias table does not cover exactly the nodes of the diagram")
        aliases_seen |= listed
        screens_seen.extend(c[1].strip("`") for c in table)
        nav_rows = _table_rows(lines, labels.raw(lang, "nav.t.table"))
        nodes += n
        edges += e
        rows_total += len(nav_rows)
        cross += sum(1 for r in nav_rows if r and r[-1])
    model = ctx.model
    if nodes != len(model.get("screens", ())) or sorted(screens_seen) != sorted(s["id"] for s in model.get("screens", ())):
        out.add("SV-214", nav_paths[0], f"the diagrams hold {nodes} nodes for {len(model.get('screens', ()))} screens")
    if edges + cross != len(model.get("nav", ())) or rows_total != len(model.get("nav", ())):
        out.add("SV-214", nav_paths[0], f"the diagrams hold {edges} edges, {cross} jumps lie across parts and the tables list {rows_total} rows, the model has {len(model.get('nav', ()))}")


# ---------------------------------------------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------------------------------------------


def _guard(out: "_Out", rule: str, path: str, run) -> None:
    """Run one check; a file so damaged that the check itself fails is reported under the rule of its family, never raised."""
    try:
        run()
    except Exception as exc:  # noqa: BLE001 (the checker reads files that may be anything)
        out.add(rule, path, f"the file could not be checked ({type(exc).__name__})")


def check_files(files: Mapping, model: Mapping, five=None, cfg=None, *, include_t: bool = False, regenerate: bool = True) -> list:
    """Every finding of SV-201..SV-216 (except SV-208) over the files of a screens tree given as ``{path: bytes}`` and the model they come from."""
    cfg = bld.coerce_config(cfg, model)
    five = ex.five_index(five)
    ctx = _Ctx(model, five, cfg)
    out = _Out()
    screens = list(model.get("screens", ()))
    expected: set = set()
    for s in screens:
        sid, enc = s["id"], ctx.enc_of[s["id"]]
        roots: dict = {}
        for variant in s["variantNames"]:
            path = f"screens/{svgmod.file_name(enc, variant)}"
            expected.add(path)
            if path not in files:
                out.add("SV-211", path, f"the state {variant!r} of {sid!r} has no SVG")
                continue
            text = files[path].decode("utf-8", "replace")
            _guard(out, "SV-201", path, lambda: check_svg(path, text, ctx, sid, variant, out, include_t))
            try:
                roots[variant] = ET.fromstring(text.encode("utf-8"))
            except ET.ParseError:
                pass
        if roots:
            _guard(out, "SV-207", f"screens/{enc}.card.md", lambda: check_card(ctx, sid, files, roots, out))
        _guard(out, "SV-213", gal.path_of(enc), lambda: check_gallery(ctx, sid, files, out))
    for p in sorted(files):
        if p.startswith("screens/") and p.endswith(".svg") and p not in expected:
            out.add("SV-211", p, "an SVG file of no state of the model")
        if p.startswith("screens/") and p.endswith(".md"):
            for problem in reading.verify_sentinel(files[p].decode("utf-8", "replace")):
                if not re.fullmatch(r"screens/(.*\.card(-\d+)?|NAV(-\d+)?)\.md", p):
                    out.add("SV-207", p, f"sentinel: {problem}")
    _guard(out, "SV-214", "screens/NAV.md", lambda: check_nav(ctx, files, out))
    # no 5D data in the stamps
    for p, data in sorted(files.items()):
        if p.startswith("screens/") and p.endswith(".md"):
            first = data.decode("utf-8", "replace").split("\n", 2)[1] if data.count(b"\n") > 1 else ""
            has_m = re.search(r"\bm=[0-9a-f]{8}\b", first) is not None
            if has_m != bool(ctx.model_sha8) or (has_m and f"m={ctx.model_sha8}" not in first):
                out.add("SV-212", p, "the model stamp m= of the page is not what the validated links allow")
    if regenerate:
        fresh = bld.build_screens(model, five, cfg, check=False)
        rule_of = (("screens/build-info.json", "SV-212"), ("site/", "SV-213"))
        for path in sorted(set(fresh.files) | {p for p in files if p.startswith(("screens/", "site/screens/"))}):
            if files.get(path) == fresh.files.get(path) or out.errors_at(path):
                continue
            if path.endswith("build-info.json") and path in files and path in fresh.files and _without_status(files[path]) == _without_status(fresh.files[path]):
                continue
            if path not in files:
                if path in fresh.files:
                    out.add("SV-211" if path.endswith(".svg") else "SV-207", path, "the file is missing")
                continue
            rule = next((r for prefix, r in rule_of if path.startswith(prefix)), None)
            rule = rule or ("SV-211" if path.endswith(".svg") else "SV-214" if "/NAV" in path else "SV-207")
            out.add(rule, path, "the file differs from its regeneration" if path in fresh.files else "the file belongs to no screen of the model")
    return out.items


def _without_status(data: bytes) -> object:
    try:
        info = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    if isinstance(info, dict):
        info.pop("status", None)
    return info


def check_result(result: "bld.ScreensResult", model: Mapping, five=None, cfg=None, *, include_t: bool = False) -> list:
    """Run the checks on a fresh result (without the regeneration, which would compare the generator with itself) and record the findings:
    the class I errors set ``status`` to ``invalid``."""
    found = check_files(result.files, model, five, cfg, include_t=include_t, regenerate=False)
    result.findings.extend(found)
    if any(f.cls == "I" and f.severity == "ERROR" for f in found):
        result.status = "invalid"
    return found
