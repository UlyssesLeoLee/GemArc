"""L1.1: the deterministic integer layout of a screen state (design A3 4.4, D-13; ``layoutVersion`` 1).

``layout_state(index, screen_id, variant, sample_rows)`` places the widget tree of one screen in one state on the canvas of its frame and
returns a ``StateLayout``: the box of every visible widget, what is drawn inside it (primitives), every visible string, the widgets the
state hides, what the renderer derived on its own and the layout findings. Everything is integer arithmetic on the facts of the model; no
randomness, no clock, no dependence on set order: the same ``ui_sha8`` and the same ``LAYOUT_VERSION`` give the same boxes.

The corrections to the first L1 draft that the design measured (D-13) and the decisions of this implementation:

* text width 107/59 hundredths of an em (``textfit``); a text that does not fit is wrapped (``text``, ``heading``, buttons, links, tabs, badges,
  choice labels) or clipped with an ellipsis (inputs, table cells, placeholders) and then listed as drawn, so the card holds the drawn string;
* interactive boxes are at least 32x32 (desktop, tablet) or 44x44 (phone) by construction; a smaller author size is lifted (``UX-103``);
* a ``form`` or ``card`` directly under the root is at most 640 wide and centred on desktop and tablet unless it says ``w``, ``w=fill`` or
  ``align``; ``align=start|center|end`` places any widget in the cross axis of its column, and on a screen it is the default for its root children;
* the label of a container is the accessible name only, it is not drawn (a ``heading`` draws a visible title);
* the state band above the canvas is 12 px plus 16 px per text line; its lines are wrapped to the frame width (a phone frame needs several);
* a mark (the circle with the widget number) sits at ``(max(x - 10, 10), y + 10)`` with radius 9; gaps of 20 px between the children of a row and
  20 px side padding in cards, forms, headers and the root keep marks off the neighbours;
* rows (``row header footer toolbar tabs``) wrap their children onto further lines instead of overflowing, so no child ever leaves its parent;
  a child is always inside its parent, siblings never overlap, nothing leaves the canvas (the invariants the I-class checks read back).

State rendering (``loading`` skeleton blocks, ``empty`` and ``error`` bodies with the variant ``text=`` or the grey placeholder) marks every
element the author did not declare as derived. v1 cannot express layers on top of each other, absolute positions, responsive breakpoints, themes or animation.
Standard library only; the layout never reads a 5D model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from . import labels
from . import model as um
from . import textfit as tf
from . import vocab

LAYOUT_VERSION = um.LAYOUT_VERSION
BAND_BASE = 12
BAND_LINE = 16
BAND_FONT = 12
MARK_R = 9
MAX_SAMPLE_ROWS = 5
MAX_SAMPLE_COLS = 6
NOTE_CHARS = 60
#: Pixels added to the box of a text that sizes its own box (``text``, ``heading``, ``link``, choice labels): the width model is a ceiling for text of
#: ordinary mix, a short run of capitals measured up to 12 percent wider than the model (Edge, Microsoft YaHei), and the box must still hold it.
TEXT_SLACK = 4
#: Share of the frame width the state band wraps its lines to (capital-heavy ids are wider than the model)
BAND_ROOM_PERCENT = 90
#: Deepest nesting that is laid out: a deeper widget is not drawn (the layout recurses once per level) and is named as not shown.
MAX_DEPTH = 40
FORM_MAX_W = 640
INPUT_MAX_W = 480
SIDEBAR_W = 240

HORIZONTAL = frozenset({"row", "header", "footer", "toolbar", "tabs"})
BORDERED = frozenset({"card", "form", "sidebar", "list"})
FILLED = frozenset({"header", "footer", "sidebar", "toolbar"})
#: kind -> (pad x, pad y, gap x, gap y)
PADS = {
    "stack": (0, 0, 0, 12), "card": (20, 16, 0, 12), "form": (20, 16, 0, 12), "list": (20, 8, 0, 8), "sidebar": (20, 16, 0, 8),
    "row": (0, 0, 20, 12), "header": (20, 8, 20, 8), "footer": (20, 8, 20, 8), "toolbar": (12, 8, 20, 8), "tabs": (0, 0, 20, 8),
    "grid": (0, 0, 20, 16),
}
MIN_H = {"header": 56, "footer": 48, "toolbar": 44, "tabs": 40, "list": 56}
#: leaves (and containers) that share the free width of a row when they have no width of their own: kind -> minimum width
FLEX_MIN = {"input": 120, "select": 120, "textarea": 160, "chart": 160, "table": 200, "custom": 120, "divider": 40}
SAMPLE_DEFAULT_KINDS = ("default", "success", "locked", "other")
ROW_H = 40
HEAD_H = 36
BODY_EMPTY_H = 56
GLYPH = 24
SWITCH_W = 44


def min_target(frame: str) -> tuple:
    """Smallest box of an interactive widget on the frame: (width, height)."""
    return (44, 44) if frame == "phone" else (32, 32)


# ---------------------------------------------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int
    cls: str
    rx: int = 0
    derived: bool = False


@dataclass(frozen=True)
class Txt:
    """One ``<text>``: ``lines`` are its lines (one ``tspan`` each when there are several); ``y`` is the first baseline."""

    x: int
    y: int
    lines: tuple
    cls: str = "t"
    anchor: str = "start"
    pitch: int = 22
    derived: bool = False


@dataclass(frozen=True)
class Path:
    d: str
    cls: str


@dataclass
class Placed:
    wid: str
    kind: str
    box: tuple                      # x, y, w, h in canvas coordinates (the band is not included)
    n: int                          # data-n: 1-based position in the pre-order widget list of the screen, state independent
    parent: str                     # parent widget id or the root id
    prims: list = field(default_factory=list)
    sample: "str | None" = None    # data-sample: "true" (sample rows drawn) or "none" (a table without data in a default-like state)
    mark: bool = False


@dataclass
class StateLayout:
    screen: str
    variant: str
    vkind: str
    note: str
    text: str                       # the variant text= (authored UI copy), "" when none
    frame: str
    width: int
    height: int                     # canvas height: max(frame height, content)
    band_h: int = 0
    band_text: str = ""             # the first band line as one string (what the state label says)
    band_lines: tuple = ()          # ... wrapped to the frame width
    note_text: str = ""             # the variant note as drawn (at most NOTE_CHARS characters)
    note_lines: tuple = ()
    placed: list = field(default_factory=list)
    hidden: list = field(default_factory=list)       # (widget id, "in" | "parent")
    texts: list = field(default_factory=list)        # (string, derived, widget id or "", role)
    derived: list = field(default_factory=list)      # (kind, widget id, string or "")
    findings: list = field(default_factory=list)     # {"rule", "widget", "message"}
    total: int = 0
    sample_rows: int = MAX_SAMPLE_ROWS

    @property
    def shown(self) -> int:
        return len(self.placed)


# ---------------------------------------------------------------------------------------------------------------
# Model index
# ---------------------------------------------------------------------------------------------------------------


class ModelIndex:
    """The lookups the layout needs, built once per model."""

    def __init__(self, model: Mapping):
        self.model = model
        self.screens = {s["id"]: s for s in model.get("screens", ())}
        self.widgets = {w["id"]: w for w in model.get("widgets", ())}
        self.variants: dict = {}
        for v in model.get("variants", ()):
            self.variants.setdefault(v["screenId"], {})[v["name"]] = v
        self.nav_from: dict = {}
        for n in model.get("nav", ()):
            self.nav_from.setdefault(n["fromId"], []).append(n)


class _Scene:
    def __init__(self, index: ModelIndex, screen: Mapping, variant: str, sample_rows: int, lang: str):
        self.lang = lang
        self.index = index
        self.screen = screen
        self.sid = screen["id"]
        self.variant = variant
        vrec = index.variants.get(self.sid, {}).get(variant)
        self.vkind = vrec["kind"] if vrec else "other"
        self.note = tf.author(vrec["note"]) if vrec else ""
        self.vtext = tf.author(vrec["text"]) if vrec and vrec.get("text") else ""
        self.frame = screen.get("frame", vocab.DEFAULT_FRAME)
        self.W, self.H0 = vocab.FRAMES[self.frame]
        self.pad = 20 if self.frame == "phone" else 24
        self.min_w, self.min_h = min_target(self.frame)
        self.sample_rows = sample_rows
        wids = list(screen.get("widgetIds", ()))
        self.reach = {wid: index.widgets[wid] for wid in wids if wid in index.widgets}
        self.nums = {wid: i + 1 for i, wid in enumerate(wids)}
        self.kids: dict = {}
        for wid in wids:
            w = self.reach.get(wid)
            if w is not None:
                self.kids[wid] = [c for c in w.get("childIds", ()) if c in self.reach]
        self.roots = [wid for wid in wids if wid in self.reach and self.reach[wid].get("parentId") is None]
        self.visible: dict = {}
        self.hidden: list = []
        for wid in wids:                       # pre-order: a parent is decided before its children
            w = self.reach.get(wid)
            if w is None:
                continue
            parent = w.get("parentId")
            own = w.get("inVariants")
            if parent is not None and not self.visible.get(parent, False):
                self.visible[wid] = False
                self.hidden.append((wid, "parent"))
            elif w.get("depth", 0) > MAX_DEPTH:
                self.visible[wid] = False
                self.hidden.append((wid, "depth"))
            elif own is not None and variant not in own:
                self.visible[wid] = False
                self.hidden.append((wid, "in"))
            else:
                self.visible[wid] = True
        self.out = StateLayout(self.sid, variant, self.vkind, self.note, self.vtext, self.frame, self.W, self.H0, hidden=self.hidden,
                               total=len(self.reach), sample_rows=sample_rows)
        self.memo: dict = {}
        self.placed_by: dict = {}

    def kids_of(self, wid: str) -> list:
        return [self.reach[c] for c in self.kids.get(wid, ()) if self.visible.get(c)]

    def finding(self, rule: str, wid: str, message: str) -> None:
        item = {"rule": rule, "widget": wid, "message": message}
        if item not in self.out.findings:
            self.out.findings.append(item)

    def text(self, string: str, wid: str, role: str, derived: bool = False) -> None:
        item = (string, derived, wid, role)
        if item not in self.out.texts:
            self.out.texts.append(item)


def _px(value) -> "int | None":
    return int(value) if isinstance(value, str) and value.isdigit() else (value if isinstance(value, int) and not isinstance(value, bool) else None)


def _fr(value) -> "int | None":
    """The weight of ``fill`` (1) and ``<n>fr``; ``None`` for anything else."""
    if value == "fill":
        return 1
    if isinstance(value, str) and value.endswith("fr") and value[:-2].isdigit():
        return int(value[:-2])
    return None


def _sample(w: Mapping) -> tuple:
    """``(cols, rows)`` of the sample data of a table or list: cleaned strings, rows as lists; both empty when the widget has none."""
    data = w.get("data")
    if not isinstance(data, Mapping):
        return [], []
    cols = [tf.author(c) for c in data.get("cols", ()) if isinstance(c, (str, int, float))] if isinstance(data.get("cols"), list) else []
    rows = []
    if isinstance(data.get("rows"), list):
        for r in data["rows"]:
            if isinstance(r, list):
                rows.append([tf.author(v) for v in r if isinstance(v, (str, int, float, bool))])
    return cols, rows


# ---------------------------------------------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------------------------------------------

_WRAPPING = frozenset({"text", "heading", "button", "link", "badge", "tab", "menu", "checkbox", "radio", "switch"})
_CLIPPING = frozenset({"input", "select", "textarea", "pagination", "image", "chart", "custom"})


def _label(w: Mapping) -> str:
    return tf.author(w.get("label", ""))


def _body_state(sc: _Scene) -> str:
    """How a table or an empty list draws its body in this state: ``rows`` (default-like), ``loading``, ``empty``."""
    if sc.vkind == "loading":
        return "loading"
    if sc.vkind in ("empty", "error"):
        return "empty"
    return "rows"


def _table_h(sc: _Scene, w: Mapping) -> int:
    cols, rows = _sample(w)
    state = _body_state(sc)
    if state == "loading":
        return HEAD_H + 3 * ROW_H
    if state == "empty":
        return HEAD_H + BODY_EMPTY_H
    if not rows:
        return HEAD_H + ROW_H
    drawn = min(len(rows), sc.sample_rows)
    return HEAD_H + drawn * ROW_H + (28 if len(rows) > drawn else 0)


def _leaf(sc: _Scene, w: Mapping, aw: int, force: "int | None" = None) -> tuple:
    """``(width, height, lines)`` of a leaf offered ``aw`` pixels of width; ``force`` fixes the width (author size, share of a row).
    ``lines`` are the wrapped lines of the label for the kinds that wrap, else ``()``."""
    key = (w["id"], aw, force)
    got = sc.memo.get(key)
    if got is not None:
        return got
    kind, label = w["kind"], _label(w)
    mw, mh = sc.min_w, sc.min_h
    aw = max(aw, 1)
    fixed = _px(w.get("w"))
    if fixed is not None and force is None:
        force = min(max(fixed, 1), aw)
    lines: tuple = ()
    if kind == "heading":
        lines = tuple(tf.wrap(label, (force or aw), 20))
        nat_w = max(tf.width(x, 20) for x in lines) + TEXT_SLACK
        nat_h = 32 if len(lines) == 1 else 28 * len(lines) + 4
    elif kind == "text":
        lines = tuple(tf.wrap(label, (force or aw), 14))
        nat_w = max(tf.width(x) for x in lines) + TEXT_SLACK
        nat_h = 22 * len(lines)
    elif kind in ("input", "select"):
        nat_w, nat_h = min(aw, INPUT_MAX_W), max(40, mh)
    elif kind == "textarea":
        nat_w, nat_h = min(aw, INPUT_MAX_W), 96
    elif kind in ("checkbox", "radio", "switch"):
        glyph = SWITCH_W if kind == "switch" else GLYPH
        room = (force or aw) - glyph - 8
        lines = tuple(tf.wrap(label, max(room, 1), 14)) if label else ()
        text_w = max((tf.width(x) for x in lines), default=0)
        nat_w = glyph + (8 + text_w + TEXT_SLACK if lines else 0)
        nat_h = max(mh, 22 * max(len(lines), 1) + 2)
    elif kind == "button":
        lines = tuple(tf.wrap(label, max((force or aw) - 32, 1), 14))
        nat_w = max(88, max(tf.width(x) for x in lines) + 32)
        nat_h = max(mh, 36, 22 * len(lines) + 14)
    elif kind == "link":
        lines = tuple(tf.wrap(label, (force or aw), 14))
        nat_w = max(mw, max(tf.width(x) for x in lines) + TEXT_SLACK)
        nat_h = max(mh, 22 * len(lines) + 4)
    elif kind in ("tab", "menu"):
        lines = tuple(tf.wrap(label, max((force or aw) - 32, 1), 14))
        nat_w = max(64, max(tf.width(x) for x in lines) + 32)
        nat_h = max(40 if kind == "tab" else 36, mh, 22 * len(lines) + 14)
    elif kind == "badge":
        lines = tuple(tf.wrap(label, max((force or aw) - 16, 1), 12))
        nat_w = max(24, max(tf.width(x, 12) for x in lines) + 16)
        nat_h = max(22, 16 * len(lines) + 6)
    elif kind == "divider":
        nat_w, nat_h = aw, 1
    elif kind == "image":
        nat_w, nat_h = min(aw, 160), 160
    elif kind == "icon":
        nat_w, nat_h = 24, 24
    elif kind == "chart":
        nat_w, nat_h = aw, 200
    elif kind == "table":
        nat_w, nat_h = aw, _table_h(sc, w)
    elif kind == "pagination":
        nat_w, nat_h = min(aw, 240), max(32, mh)
    elif kind == "custom":
        nat_w, nat_h = aw, 80
    else:                                                    # a container asked as a leaf (not reached) or an unknown kind
        nat_w, nat_h = aw, 40
    width = force if force is not None else min(max(nat_w, 1), aw)
    height = nat_h
    wanted_h = _px(w.get("h"))
    if wanted_h is not None:
        if wanted_h < nat_h:
            sc.finding("UX-103" if kind in vocab.INTERACTIVE else "UX-102", w["id"], f"height {wanted_h} px is below the {nat_h} px the content needs: lifted")
        height = max(wanted_h, nat_h)
    if fixed is not None and kind in vocab.INTERACTIVE and fixed < mw:
        sc.finding("UX-103", w["id"], f"width {fixed} px is below the minimum target of {mw}x{mh}: lifted")
        width = min(max(width, mw), aw)
    result = (width, height, lines)
    sc.memo[key] = result
    return result


def _flex_min(w: Mapping) -> int:
    if w["kind"] in vocab.CONTAINERS:
        return 160
    return FLEX_MIN.get(w["kind"], 0)


# ---------------------------------------------------------------------------------------------------------------
# Sizes of containers and widgets (the measuring pass)
# ---------------------------------------------------------------------------------------------------------------


def _width(sc: _Scene, w: Mapping, aw: int) -> int:
    """The width a widget takes in a column of ``aw`` pixels."""
    kind = w["kind"]
    if kind in vocab.CONTAINERS:
        fixed = _px(w.get("w"))
        return min(max(fixed, 1), aw) if fixed is not None else aw
    if _fr(w.get("w")) is not None:                      # fill or <n>fr: the whole width of the column
        return max(aw, 1)
    return _leaf(sc, w, aw)[0]


def _height(sc: _Scene, w: Mapping, width: int) -> int:
    """The natural height of a widget at a definite width (an author height only lifts it)."""
    kind = w["kind"]
    if kind not in vocab.CONTAINERS:
        return _leaf(sc, w, width, force=width)[1]
    key = ("h", w["id"], width)
    got = sc.memo.get(key)
    if got is not None:
        return got
    px, py, gx, gy = PADS[kind]
    inner = max(width - 2 * px, 1)
    kids = sc.kids_of(w["id"])
    if not kids:
        h = max(MIN_H.get(kind, 0), 2 * py + 40)
    elif kind in HORIZONTAL:
        plan = _row_plan(sc, w, inner)
        h = sum(line_h for _items, line_h in plan) + gy * (len(plan) - 1) + 2 * py
        h = max(h, MIN_H.get(kind, 0))
    elif kind == "grid":
        h = sum(max(_height(sc, c, cw) for c, cw in row) for row in _grid_rows(sc, w, inner)) \
            + gy * (len(_grid_rows(sc, w, inner)) - 1) + 2 * py
    else:
        h = sum(_height(sc, c, _width(sc, c, inner)) for c in kids) + gy * (len(kids) - 1) + 2 * py
        h = max(h, MIN_H.get(kind, 0))
    wanted = _px(w.get("h"))
    if wanted is not None:
        if wanted < h:
            sc.finding("UX-102", w["id"], f"height {wanted} px is below the {h} px the children need: lifted")
        h = max(wanted, h)
    sc.memo[key] = h
    return h


def _row_plan(sc: _Scene, w: Mapping, inner: int) -> list:
    """Lines of a horizontal container: ``[([(child, width), ...], line height), ...]``, children wrapped onto a new line when they do not fit."""
    key = ("row", w["id"], inner)
    got = sc.memo.get(key)
    if got is not None:
        return got
    kind = w["kind"]
    gx = PADS[kind][2]
    specs = []
    for c in sc.kids_of(w["id"]):
        fixed = _px(c.get("w"))
        weight = _fr(c.get("w"))
        flexible = weight is None and fixed is None and (c["kind"] in FLEX_MIN or c["kind"] in vocab.CONTAINERS)
        if fixed is not None:
            lifted = _leaf(sc, c, inner)[0] if c["kind"] not in vocab.CONTAINERS else min(max(fixed, 1), inner)
            specs.append((c, "px", min(lifted, inner), 0))
        elif weight is not None or flexible:
            specs.append((c, "fr", min(_flex_min(c), inner), weight or 1))
        else:
            specs.append((c, "nat", min(_leaf(sc, c, inner)[0], inner), 0))
    lines, cur, used = [], [], 0
    for spec in specs:
        need = spec[2] + (gx if cur else 0)
        if cur and used + need > inner:
            lines.append(cur)
            cur, used = [], 0
            need = spec[2]
        cur.append(spec)
        used += need
    if cur:
        lines.append(cur)
    plan = []
    for line in lines:
        base = sum(s[2] for s in line) + gx * (len(line) - 1)
        free = max(inner - base, 0)
        weights = sum(s[3] for s in line if s[1] == "fr")
        widths = [s[2] for s in line]
        if weights:
            shares = [free * s[3] // weights if s[1] == "fr" else 0 for s in line]
            last = max(i for i, s in enumerate(line) if s[1] == "fr")
            shares[last] += free - sum(shares)
            widths = [widths[i] + shares[i] for i in range(len(line))]
        items = [(s[0], widths[i]) for i, s in enumerate(line)]
        line_h = max(_height(sc, c, cw) for c, cw in items)
        plan.append((items, line_h))
    if not plan:
        plan = []
    sc.memo[key] = plan
    return plan


def _grid_rows(sc: _Scene, w: Mapping, inner: int) -> list:
    key = ("grid", w["id"], inner)
    got = sc.memo.get(key)
    if got is not None:
        return got
    gx = PADS["grid"][2]
    cols = max(1, min(int(w.get("cols") or 2), um.MAX_COLS))
    cw = max((inner - gx * (cols - 1)) // cols, 1)
    kids = sc.kids_of(w["id"])
    rows = []
    for i in range(0, len(kids), cols):
        rows.append([(c, _cell_width(sc, c, cw)) for c in kids[i:i + cols]])
    sc.memo[key] = rows
    return rows


def _cell_width(sc: _Scene, c: Mapping, cw: int) -> int:
    return _width(sc, c, cw)


# ---------------------------------------------------------------------------------------------------------------
# Placement and drawing
# ---------------------------------------------------------------------------------------------------------------


def _text_baseline(y: int, h: int, em: int, lines: int, pitch: int) -> int:
    """First baseline of ``lines`` lines centred vertically in a box of height ``h``."""
    block = pitch * (lines - 1) + em + 6
    return y + (h - block) // 2 + em + 1 if lines > 1 else y + h // 2 + (em * 35 + 50) // 100


def _wire(sc: _Scene, w: Mapping, box: tuple, placed: Placed) -> None:
    """The primitives of a leaf at its final box (a container only gets its outline)."""
    x, y, bw, bh = box
    kind = w["kind"]
    label = _label(w)
    emph = w.get("emph")
    prims = placed.prims
    wid = w["id"]
    em_cls = {"muted": " g", "danger": " r"}.get(emph or "", "")
    if kind in vocab.CONTAINERS:
        cls = "b" if kind in BORDERED else "f" if kind in FILLED else "n"
        prims.append(Rect(x, y, bw, bh, cls, 4 if kind in BORDERED else 0))
        return
    if kind == "heading":
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Rect(x, y, bw, bh, "z"))
        prims.append(Txt(x, y + 23, lines, "h" + em_cls, pitch=28))
        sc.text(label, wid, "label")
    elif kind == "text":
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Rect(x, y, bw, bh, "z"))
        prims.append(Txt(x, y + 16, lines, "t" + em_cls))
        sc.text(label, wid, "label")
    elif kind in ("input", "select", "textarea"):
        prims.append(Rect(x, y, bw, bh, "b", 4))
        shown = tf.clip(label, bw - 24 - (24 if kind == "select" else 0), 14)
        if shown != label:
            sc.finding("UX-102", wid, "label clipped to the box width")
        prims.append(Txt(x + 12, y + 25 if kind == "textarea" else y + bh // 2 + 5, (shown,), "t g"))
        sc.text(shown, wid, "label")
        if kind == "select":
            prims.append(Path(f"M{x + bw - 28} {y + bh // 2 - 3}l6 7 6-7z", "k"))
    elif kind in ("checkbox", "radio", "switch"):
        glyph = SWITCH_W if kind == "switch" else GLYPH
        gy = y + (bh - GLYPH) // 2
        prims.append(Rect(x, y, bw, bh, "z"))
        prims.append(Rect(x, gy, glyph, GLYPH, "b", 12 if kind != "checkbox" else 3))
        lines = _leaf(sc, w, bw, force=bw)[2]
        if lines:
            prims.append(Txt(x + glyph + 8, _text_baseline(y, bh, 14, len(lines), 22), lines, "t" + em_cls))
            sc.text(label, wid, "label")
    elif kind == "button":
        cls = {"primary": "k", "secondary": "s", "danger": "d"}.get(emph or "", "b")
        tcls = "t w" if emph == "primary" else "t r" if emph == "danger" else "t"
        prims.append(Rect(x, y, bw, bh, cls, 4))
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Txt(x + bw // 2, _text_baseline(y, bh, 14, len(lines), 22), lines, tcls, "middle"))
        sc.text(label, wid, "label")
    elif kind == "link":
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Rect(x, y, bw, bh, "z"))
        prims.append(Txt(x, _text_baseline(y, bh, 14, len(lines), 22), lines, "t u"))
        sc.text(label, wid, "label")
    elif kind in ("tab", "menu"):
        prims.append(Rect(x, y, bw, bh, "f", 4))
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Txt(x + bw // 2, _text_baseline(y, bh, 14, len(lines), 22), lines, "t" + em_cls, "middle"))
        sc.text(label, wid, "label")
    elif kind == "badge":
        prims.append(Rect(x, y, bw, bh, "f", 11))
        lines = _leaf(sc, w, bw, force=bw)[2]
        prims.append(Txt(x + bw // 2, _text_baseline(y, bh, 12, len(lines), 16), lines, "s2", "middle", 16))
        sc.text(label, wid, "label")
    elif kind == "divider":
        prims.append(Rect(x, y, bw, bh, "f"))
    elif kind in ("image", "chart", "custom", "icon"):
        skeleton = sc.vkind == "loading" and kind in ("image", "chart")
        prims.append(Rect(x, y, bw, bh, "q" if skeleton else "f", 4, derived=skeleton))
        if skeleton:
            sc.out.derived.append(("skeleton", wid, ""))
        elif kind in ("image", "chart"):
            prims.append(Path(f"M{x} {y}L{x + bw} {y + bh}M{x + bw} {y}L{x} {y + bh}", "ln"))
        if label and kind != "icon":
            shown = tf.clip(label, bw - 24, 14)
            if shown != label:
                sc.finding("UX-102", wid, "label clipped to the box width")
            prims.append(Txt(x + 12, y + 24, (shown,), "t g"))
            sc.text(shown, wid, "label")
    elif kind == "table":
        _wire_table(sc, w, box, placed)
    elif kind == "pagination":
        prims.append(Rect(x, y, bw, bh, "f", 4))
        shown = tf.clip(label, bw - 24, 14)
        if shown != label:
            sc.finding("UX-102", wid, "label clipped to the box width")
        prims.append(Txt(x + 12, y + bh // 2 + 5, (shown,), "t g"))
        sc.text(shown, wid, "label")


def _wire_table(sc: _Scene, w: Mapping, box: tuple, placed: Placed) -> None:
    x, y, bw, bh = box
    wid = w["id"]
    cols, rows = _sample(w)
    state = _body_state(sc)
    prims = placed.prims
    prims.append(Rect(x, y, bw, bh, "b"))
    prims.append(Rect(x, y, bw, HEAD_H, "f"))
    ncols = max(1, min(MAX_SAMPLE_COLS, len(cols) if cols else max((len(r) for r in rows), default=1)))
    colw = max(bw // ncols, 1)
    if cols:
        for i in range(ncols):
            text = tf.clip(cols[i], colw - 20, 14) if i < len(cols) else ""
            if text:
                if text != cols[i]:
                    sc.finding("UX-102", wid, "column title clipped to the column width")
                prims.append(Txt(x + i * colw + 12, y + 23, (text,), "t bd"))
                sc.text(text, wid, "header")
    else:
        label = _label(w)
        shown = tf.clip(label, bw - 24, 14)
        if shown != label:
            sc.finding("UX-102", wid, "label clipped to the box width")
        prims.append(Txt(x + 12, y + 23, (shown,), "t bd"))
        sc.text(shown, wid, "header")
    top = y + HEAD_H
    if state == "loading":
        for i in range(3):
            prims.append(Rect(x + 12, top + i * ROW_H + 12, bw - 24, 16, "q", 4, derived=True))
        sc.out.derived.append(("skeleton", wid, ""))
    elif state == "empty":
        body = sc.vtext or tf.author(_placeholder(sc))
        shown = tf.clip(body, bw - 24, 14)
        if shown != body:
            sc.finding("UX-102", wid, "the state text is clipped to the box width")
        if sc.vtext:
            sc.text(shown, wid, "state")
        else:
            sc.out.derived.append(("placeholder", wid, body))
            sc.text(shown, wid, "placeholder", True)
        prims.append(Txt(x + bw // 2, top + 33, (shown,), "t g" if sc.vtext else "t g i", "middle", derived=not sc.vtext))
    elif not rows:
        prims.append(Rect(x + 12, top + 12, bw - 24, 16, "q", 4))
        placed.sample = "none"
        sc.out.derived.append(("bar", wid, ""))
    else:
        drawn = min(len(rows), sc.sample_rows)
        placed.sample = "true"
        for r in range(drawn):
            for i in range(ncols):
                if i < len(rows[r]) and rows[r][i] != "":
                    cell = rows[r][i]
                    text = tf.clip(cell, colw - 20, 14)
                    if text != cell:
                        sc.finding("UX-102", wid, "cell clipped to the column width")
                    prims.append(Txt(x + i * colw + 12, top + r * ROW_H + 25, (text,), "t"))
                    sc.text(text, wid, "cell")
        if len(rows) > drawn:
            more = tf.author(_more_rows(sc, len(rows) - drawn))
            prims.append(Txt(x + 12, top + drawn * ROW_H + 19, (more,), "s2 g i", derived=True))
            sc.text(more, wid, "more", True)
            sc.out.derived.append(("more", wid, more))


def _placeholder(sc: _Scene) -> str:
    return labels.t(sc.lang, "svg.placeholder.empty")


def _more_rows(sc: _Scene, k: int) -> str:
    return labels.t(sc.lang, "svg.more_rows", k=k)


def _place(sc: _Scene, w: Mapping, box: tuple, parent: str) -> None:
    placed = Placed(w["id"], w["kind"], box, sc.nums[w["id"]], parent, mark=w["kind"] not in vocab.CONTAINERS and w["kind"] != "divider")
    sc.out.placed.append(placed)
    sc.placed_by[w["id"]] = placed
    _wire(sc, w, box, placed)
    kind = w["kind"]
    if kind not in vocab.CONTAINERS:
        return
    kids = sc.kids_of(w["id"])
    px, py, gx, gy = PADS[kind]
    x, y, bw, bh = box
    ix, iy, iw, ih = x + px, y + py, max(bw - 2 * px, 1), max(bh - 2 * py, 1)
    if not kids:
        if kind == "list":
            _list_body(sc, w, (x, y, bw, bh), placed)
        return
    if kind in HORIZONTAL:
        plan = _row_plan(sc, w, iw)
        total = sum(h for _i, h in plan) + gy * (len(plan) - 1)
        cy = iy + max((ih - total) // 2, 0) if kind in ("header", "footer") else iy
        for items, line_h in plan:
            cx = ix
            for c, cw in items:
                ch = _height(sc, c, cw)
                if c["kind"] in vocab.CONTAINERS or _fr(c.get("h")) is not None:
                    ch = line_h
                    ey = cy
                else:
                    ey = cy + (line_h - ch) // 2
                _place(sc, c, (cx, ey, cw, ch), w["id"])
                cx += cw + gx
            cy += line_h + gy
    elif kind == "grid":
        cy = iy
        for row in _grid_rows(sc, w, iw):
            cols = max(1, min(int(w.get("cols") or 2), um.MAX_COLS))
            cell = max((iw - gx * (cols - 1)) // cols, 1)
            rh = max(_height(sc, c, cw) for c, cw in row)
            for j, (c, cw) in enumerate(row):
                ch = rh if c["kind"] in vocab.CONTAINERS or _fr(c.get("h")) is not None else _height(sc, c, cw)
                _place(sc, c, (ix + j * (cell + gx), cy, cw, ch), w["id"])
            cy += rh + gy
    else:
        _place_column(sc, kids, ix, iy, iw, ih, gy, w["id"], default_align=None)


def _list_body(sc: _Scene, w: Mapping, box: tuple, placed: Placed) -> None:
    """A list without visible children draws its body like a table does: one bar, three skeleton bars, or the empty text."""
    x, y, bw, bh = box
    state = _body_state(sc)
    if state == "loading":
        for i in range(3):
            placed.prims.append(Rect(x + 12, y + i * 56 + 20, bw - 24, 16, "q", 4, derived=True))
        sc.out.derived.append(("skeleton", w["id"], ""))
    elif state == "empty":
        body = sc.vtext or tf.author(_placeholder(sc))
        shown = tf.clip(body, bw - 24, 14)
        if shown != body:
            sc.finding("UX-102", w["id"], "the state text is clipped to the box width")
        if sc.vtext:
            sc.text(shown, w["id"], "state")
        else:
            sc.out.derived.append(("placeholder", w["id"], body))
            sc.text(shown, w["id"], "placeholder", True)
        placed.prims.append(Txt(x + bw // 2, y + bh // 2 + 5, (shown,), "t g" if sc.vtext else "t g i", "middle", derived=not sc.vtext))
    else:
        placed.prims.append(Rect(x + 12, y + 20, bw - 24, 16, "q", 4))
        placed.sample = "none"
        sc.out.derived.append(("bar", w["id"], ""))


def _place_column(sc: _Scene, kids: list, ix: int, iy: int, iw: int, ih: int, gap: int, parent: str, default_align: "str | None") -> None:
    """Children of a vertical container top to bottom; the free height goes to the children that say ``h=fill`` or ``h=<n>fr``."""
    widths = []
    heights = []
    for c in kids:
        cw = _root_width(sc, c, iw) if default_align is not None else _width(sc, c, iw)
        widths.append(cw)
        heights.append(_height(sc, c, cw))
    free = max(ih - (sum(heights) + gap * (len(kids) - 1)), 0)
    weights = [(_fr(c.get("h")) or 0) for c in kids]
    total = sum(weights)
    extra = [free * wt // total if total else 0 for wt in weights]
    if total:
        last = max(i for i, wt in enumerate(weights) if wt)
        extra[last] += free - sum(extra)
    cy = iy
    for i, c in enumerate(kids):
        cw, ch = widths[i], heights[i] + extra[i]
        align = c.get("align") or (default_align if default_align and default_align != "auto" else None)
        if align is None and default_align == "auto" and c["kind"] in ("form", "card") and c.get("w") is None and sc.frame != "phone":
            align = "center"
        x = ix + ((iw - cw) // 2 if align == "center" else (iw - cw if align == "end" else 0))
        _place(sc, c, (x, cy, cw, ch), parent)
        cy += ch + gap


def _root_width(sc: _Scene, c: Mapping, iw: int) -> int:
    """Width of a root child: a ``form`` or ``card`` without a width of its own is at most 640 on desktop and tablet."""
    if c["kind"] in ("form", "card") and c.get("w") is None and sc.frame != "phone":
        return min(iw, FORM_MAX_W)
    return _width(sc, c, iw)


# ---------------------------------------------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------------------------------------------


def _wrap_items(items: list, max_w: int, em: int) -> tuple:
    """Lines of ``items`` joined by `` · ``; a line break falls between two items, the middle dot stays at the end of the line."""
    tokens = [item + (" ·" if i < len(items) - 1 else "") for i, item in enumerate(items)]
    lines, cur = [], ""
    for token in tokens:
        cand = f"{cur} {token}" if cur else token
        if cur and tf.width(cand, em) > max_w:
            lines.append(cur)
            cur = token
        else:
            cur = cand
    lines.append(cur)
    out = []
    for line in lines:
        out.extend(tf.wrap(line, max_w, em) if tf.width(line, em) > max_w else [line])
    return tuple(out)


def band(sc: _Scene, ui_sha8: str) -> None:
    """The state band: the identity and the wireframe statement on the first lines, the variant note below them (design 4.3)."""
    out = sc.out
    lang = sc.lang
    items = [sc.sid, sc.variant, f"{sc.frame} {out.width}x{out.height}", labels.t(lang, "svg.band.layout", n=LAYOUT_VERSION),
             labels.t(lang, "svg.band.ui", sha=ui_sha8), labels.t(lang, "svg.band.wire"), labels.t(lang, "svg.band.shown", k=out.shown, n=out.total)]
    items = [tf.author(x) for x in items]
    room = (out.width - 16) * BAND_ROOM_PERCENT // 100
    out.band_text = " ".join(item + (" ·" if i < len(items) - 1 else "") for i, item in enumerate(items))
    out.band_lines = _wrap_items(items, room, BAND_FONT)
    out.note_text = sc.note[:NOTE_CHARS]
    out.note_lines = tuple(tf.wrap(out.note_text, room, BAND_FONT)) if out.note_text else ()
    out.band_h = BAND_BASE + BAND_LINE * (len(out.band_lines) + len(out.note_lines))
    sc.text(out.band_text, "", "band")
    if out.note_text:
        sc.text(out.note_text, "", "note")


def layout_state(index: ModelIndex, screen_id: str, variant: str = vocab.DEFAULT_VARIANT, sample_rows: int = MAX_SAMPLE_ROWS, lang: str = "zh-CN",
                 ui_sha8: str = "00000000") -> StateLayout:
    """Place the widgets of a screen in one state. ``sample_rows`` is the most sample rows a table draws (5, the degradation steps use 3 and 0)."""
    screen = index.screens[screen_id]
    sc = _Scene(index, screen, variant, sample_rows, lang)
    W, pad = sc.W, sc.pad
    roots = [sc.reach[r] for r in sc.roots if sc.visible.get(r)]
    heads = [r for r in roots if r["kind"] == "header"]
    foots = [r for r in roots if r["kind"] == "footer"]
    sides = [r for r in roots if r["kind"] == "sidebar"]
    main = [r for r in roots if r not in heads and r not in foots and r not in sides]
    root_id = um.root_id(sc.sid)
    side_w = SIDEBAR_W if sides and sc.frame != "phone" else 0
    y = 0
    for r in heads:
        h = _height(sc, r, W)
        _place(sc, r, (0, y, W, h), root_id)
        y += h
    foot_h = sum(_height(sc, r, W) for r in foots)
    mw = W - side_w
    inner = max(mw - 2 * pad, 1)
    column = ([] if side_w else sides) + main
    widths = [_root_width(sc, c, inner) for c in column]
    heights = [_height(sc, c, cw) for c, cw in zip(column, widths)]
    gap = 16
    content = (sum(heights) + gap * (len(column) - 1) + 2 * pad) if column else 0
    side_min = max((_height(sc, r, side_w) for r in sides), default=0) if side_w else 0
    avail = max(sc.H0 - y - foot_h, content, side_min)
    if column:
        _place_column(sc, column, side_w + pad, y + pad, inner, avail - 2 * pad, gap, root_id, default_align=(screen.get("align") or "auto"))
    if side_w:
        for r in sides:
            _place(sc, r, (0, y, side_w, avail), root_id)
    fy = y + avail
    for r in foots:
        h = _height(sc, r, W)
        _place(sc, r, (0, fy, W, h), root_id)
        fy += h
    sc.out.height = max(sc.H0, fy)
    sc.out.placed.sort(key=lambda p: p.n)
    band(sc, ui_sha8)
    return sc.out
