"""The semantic SVG of one screen state (design A3 4.3): grouped ids, roles, title and description, explicit geometry, a state band that says
``wireframe sketch``, and the stable attribute set the checks and a frontend comparison read back.

``render_state`` turns a ``layout.StateLayout`` into the text of one ``.svg`` file: one element per line, flat sibling groups with
``data-parent`` for the tree, all visible text in ``<text>`` (no text turned into paths, no images, no scripts, no external references), one ``<style>``
element of class rules (text colour comes from classes: a CSS rule beats a presentation attribute). The first child of every group is a
``<rect>`` with the box of the group, equal to its ``data-box``; boxes are in canvas coordinates, the content group is translated below the
band. Standard library only; the output is a function of the layout and of the identity stamps given, nothing else.
"""
from __future__ import annotations

from typing import Mapping

from . import labels
from . import layout as lay
from . import model as um
from . import textfit as tf
from . import vocab

SCHEMA = "gemarc-atlas-screen/1"
FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"
#: The class rules, one group of rules per line (a line stays below 480 characters). Sizes are in the classes (``t`` 14, ``h`` 20 bold, ``s2`` 12,
#: ``mk`` 12), so no text carries a presentation size; colours come from classes too (a CSS rule beats a presentation attribute).
CSS_LINES = (
    ".p{fill:#f6f7f9}.b{fill:#fff;stroke:#8a8f98}.f{fill:#e9ebee;stroke:#8a8f98}.n{fill:none;stroke:#c3c7cd;stroke-dasharray:3 3}.z{fill:transparent}"
    ".k{fill:#1f2328}.s{fill:#fff;stroke:#1f2328;stroke-width:2}.d{fill:#fff;stroke:#c0392b;stroke-width:2}.q{fill:#e5e7eb}.m{fill:#6b7280}.ln{stroke:#8a8f98;fill:none}",
    ".t,.h,.s2,.mk{font-family:" + FONT + ";font-size:14px;fill:#1f2328}.h{font-size:20px;font-weight:bold}.s2{font-size:12px}",
    ".mk{font-size:12px;fill:#fff;text-anchor:middle}.bd{font-weight:bold}.g{fill:#6b7280}.w{fill:#fff}.r{fill:#c0392b}.u{fill:#1a5fb4;text-decoration:underline}.i{font-style:italic}",
)
CSS = "\n".join(CSS_LINES)
MAX_HIDDEN_NAMED = 30
MAX_ATTRIBUTE_LINE = 480


def file_name(enc_name: str, variant: str) -> str:
    """``<enc>.svg`` for the default state, ``<enc>~<variant>.svg`` for the others (``enc`` of the screen id, the mark ``~`` is what ``enc`` writes for ``#``)."""
    return f"{enc_name}.svg" if variant == vocab.DEFAULT_VARIANT else f"{enc_name}~{variant}.svg"


def _a(name: str, value) -> str:
    return f' {name}="{tf.esc_attr(str(value))}"'


def _title(lang: str, screen: Mapping, variant: str) -> str:
    return tf.clean(labels.t(lang, "svg.title", id=screen["id"], title=tf.author(screen.get("title", "")), variant=variant))


def kinds_summary(widgets: list) -> str:
    """``button×1, form×1`` over the widgets given: kinds sorted by name."""
    counts: dict = {}
    for w in widgets:
        counts[w["kind"]] = counts.get(w["kind"], 0) + 1
    return ", ".join(f"{k}×{counts[k]}" for k in sorted(counts))


def description(lang: str, index: lay.ModelIndex, state: lay.StateLayout) -> str:
    """The ``<desc>``: what the screen contains and what this state shows, the widgets it does not show by name with the reason, what is not
    drawn, the state note, and the sentence that the geometry is only a sketch."""
    screen = index.screens[state.screen]
    wids = [index.widgets[i] for i in screen.get("widgetIds", ()) if i in index.widgets]
    first = labels.t(lang, "svg.word.contains") + " " + labels.t(lang, "svg.desc.contains", n=state.total, kinds=kinds_summary(wids), k=state.shown)
    if state.hidden:
        named = [labels.t(lang, "svg.desc.named", id=wid, reason=labels.t(lang, "svg.reason." + why)) for wid, why in state.hidden[:MAX_HIDDEN_NAMED]]
        extra = len(state.hidden) - len(named)
        first += labels.t(lang, "svg.desc.hidden", list=", ".join(named) + (labels.t(lang, "svg.desc.more", k=extra) if extra > 0 else ""))
    sentences = [first + labels.t(lang, "svg.desc.end"), labels.t(lang, "svg.desc.excludes", w=labels.t(lang, "svg.word.excludes"), text=labels.t(lang, "svg.desc.excl"))]
    if state.note:
        sentences.append(state.note)
    sentences.append(labels.t(lang, "svg.desc.geometry"))
    return tf.clean(" ".join(sentences))


def _text(t: lay.Txt) -> str:
    attrs = f'x="{t.x}" y="{t.y}"' + (f' text-anchor="{t.anchor}"' if t.anchor != "start" else "") + f' class="{t.cls}"'
    if t.derived:
        attrs += ' data-derived="true"'
    if len(t.lines) == 1:
        return f"<text {attrs}>{tf.esc_text(t.lines[0])}</text>"
    spans = "".join(f'<tspan x="{t.x}"' + ("" if i == 0 else f' dy="{t.pitch}"') + f">{tf.esc_text(line)}</tspan>" for i, line in enumerate(t.lines))
    return f"<text {attrs}>{spans}</text>"


def _prim(p) -> str:
    if isinstance(p, lay.Rect):
        rx = f' rx="{p.rx}"' if p.rx else ""
        derived = ' data-derived="true"' if p.derived else ""
        return f'<rect x="{p.x}" y="{p.y}" width="{p.w}" height="{p.h}"{rx} class="{p.cls}"{derived}/>'
    if isinstance(p, lay.Path):
        return f'<path d="{p.d}" class="{p.cls}"/>'
    return _text(p)


def _links(index: lay.ModelIndex, wid: str) -> dict:
    """The attributes a widget (or the screen, for a navigation that starts at the screen) carries for its outgoing navigation."""
    navs = index.nav_from.get(wid, ())
    out: dict = {}
    if navs:
        out["data-goto"] = ",".join(n["toId"] for n in navs)
        out["data-nav"] = ",".join(n["id"] for n in navs)
        out["data-on"] = ",".join(n.get("on", vocab.DEFAULT_TRIGGER) for n in navs)
        cases = [n["caseId"] for n in navs if n.get("caseId")]
        rules = [n["ruleId"] for n in navs if n.get("ruleId")]
        if cases:
            out["data-case"] = ",".join(cases)
        if rules:
            out["data-rule"] = ",".join(rules)
    return out


def _group_open(index: lay.ModelIndex, screen: Mapping, p: lay.Placed) -> str:
    w = index.widgets[p.wid]
    attrs = [("id", p.wid), ("data-kind", p.kind), ("data-box", "%d %d %d %d" % p.box), ("data-n", p.n), ("data-parent", p.parent),
             ("aria-label", tf.author(w.get("label", "")))]
    for key, value in (("data-emph", w.get("emph")), ("data-bind", w.get("bind")), ("data-api", w.get("apiId"))):
        if value:
            attrs.append((key, value))
    if w.get("reqIds"):
        attrs.append(("data-req", ",".join(w["reqIds"])))
    links = _links(index, p.wid)
    if links.get("data-case"):
        attrs.append(("data-case", links["data-case"]))
    if links.get("data-rule"):
        attrs.append(("data-rule", links["data-rule"]))
    own = w.get("inVariants")
    if own is not None and not set(screen.get("variantNames", ())) <= set(own):
        attrs.append(("data-in", ",".join(own)))
    for key in ("data-goto", "data-nav", "data-on"):
        if key in links:
            attrs.append((key, links[key]))
    if p.sample:
        attrs.append(("data-sample", p.sample))
    return "<g" + "".join(_a(k, v) for k, v in attrs) + ">"


def _band(state: lay.StateLayout, sid: str) -> list:
    lines = [f'<g id="{sid}.state" data-kind="state-label" data-box="0 0 {state.width} {state.band_h}">',
             f'<rect x="0" y="0" width="{state.width}" height="{state.band_h}" class="f"/>']
    row = 0
    for block in (state.band_lines, state.note_lines):
        if block:
            lines.append(_text(lay.Txt(8, 18 + lay.BAND_LINE * row, tuple(block), "s2 g", pitch=lay.BAND_LINE)))
            row += len(block)
    lines.append("</g>")
    return lines


def render_state(index: lay.ModelIndex, state: lay.StateLayout, *, lang: str, ui_sha8: str, model_sha8: "str | None" = None) -> str:
    """The text of the ``.svg`` file of one state (LF, one element per line, ends with a newline)."""
    screen = index.screens[state.screen]
    sid = state.screen
    total_h = state.height + state.band_h
    title = _title(lang, screen, state.variant)
    root = ('<svg xmlns="http://www.w3.org/2000/svg"' + _a("viewBox", f"0 0 {state.width} {total_h}") + _a("width", state.width) + _a("height", total_h)
            + ' role="group"' + _a("aria-label", title) + _a("data-schema", SCHEMA) + _a("data-screen", sid) + _a("data-variant", state.variant)
            + _a("data-frame", state.frame) + _a("data-layout", lay.LAYOUT_VERSION) + _a("data-ui-sha8", ui_sha8)
            + (_a("data-model-sha8", model_sha8) if model_sha8 else "") + ">")
    out = [root, f"<title>{tf.esc_text(title)}</title>", f"<desc>{tf.esc_text(description(lang, index, state))}</desc>", "<style>", *CSS_LINES, "</style>"]
    out.extend(_band(state, sid))
    root_attrs = [("id", um.root_id(sid)), ("data-kind", "screen"), ("data-box", f"0 0 {state.width} {state.height}")]
    if screen.get("reqIds"):
        root_attrs.append(("data-req", ",".join(screen["reqIds"])))
    for key, value in _links(index, sid).items():
        if key in ("data-goto", "data-nav", "data-on"):
            root_attrs.append((key, value))
    root_attrs.append(("transform", f"translate(0 {state.band_h})"))
    out.append("<g" + "".join(_a(k, v) for k, v in root_attrs) + ">")
    out.append(f'<rect x="0" y="0" width="{state.width}" height="{state.height}" class="p"/>')
    for p in state.placed:
        out.append(_group_open(index, screen, p))
        out.extend(_prim(prim) for prim in p.prims)
        if p.mark:
            x, y = p.box[0], p.box[1]
            cx, cy = max(x - 10, 10), y + 10
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{lay.MARK_R}" class="m"/>')
            out.append(f'<text x="{cx}" y="{cy + 4}" class="mk" data-mark="{p.n}">{p.n}</text>')
        out.append("</g>")
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"
