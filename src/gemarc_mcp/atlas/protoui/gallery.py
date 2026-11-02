"""The gallery page of a screen, ``site/screens/<enc>.html`` (design A3 4.5, D-2): the clickable prototype for people and ``WebFetch``, and at the
same time the HTML twin of the screen card (the one exception of D-2).

The page holds the very same card model as the Markdown card (``card.CardModel``, rendered here as HTML tables, so card and gallery cannot
disagree) and the semantic SVG of every state, inline and one after the other. Every widget that starts a jump is wrapped in an anchor by
inserting a whole ``<a href>`` line before and a ``</a>`` line after its group; deleting exactly those lines gives back the ``.svg`` file byte for
byte (SV-213). A jump to a state of a screen goes to the figure ``st-<state>`` of that screen's gallery. No script, no external reference, no
``data:`` content, one ``<style>`` of the page.

A gallery is at most 40,000 bytes. When all states do not fit, only the default state is inlined and the others are named as ``not inlined``
with a link to their ``.svg``; the caller records the L-class degradation and a repair line. The first line and the sentinel of the page use
logical counts (blocks and tables) instead of a line count, because physical lines mean nothing to an HTML reader; the sentinel's ``sha8`` is
the one of the first Markdown card page this page mirrors. Standard library only.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from ..reading import api as reading
from . import card as cardmod
from . import labels
from . import textfit as tf

STYLE = (
    "body{font:14px/1.5 'Microsoft YaHei','PingFang SC',sans-serif;margin:16px;color:#1f2328}",
    "header,footer{color:#57606a;font-size:12px}",
    "table{border-collapse:collapse;margin:8px 0}",
    "th,td{border:1px solid #d0d7de;padding:2px 8px;text-align:left;vertical-align:top}",
    "figure{margin:12px 0;overflow:auto}",
    "figcaption{font-size:12px;color:#57606a}",
    "code{font-family:Consolas,monospace}",
    "aside{background:#f6f8fa;padding:4px 12px;margin:8px 0}",
)
_LINK_LINE = re.compile(r'^<a href="[^"]*">$')
_GOTO = re.compile(r' data-goto="([^"]*)"')
_CODE = re.compile(r"`([^`]+)`")
MAX_BYTES = reading.BUDGETS["cap_gallery"]


def path_of(enc_name: str) -> str:
    return f"site/screens/{enc_name}.html"


def state_anchor(variant: str) -> str:
    return f"st-{variant}"


def href_of(target: str, own_screen: str, enc_of: Mapping) -> "str | None":
    """The link of a jump target (``SCR`` or ``SCR#state``): the gallery of the screen (``<enc>.html``), the figure of the state after ``#``."""
    screen, _hash, variant = target.partition("#")
    anchor = "#" + state_anchor(variant or "default") if (variant or screen == own_screen) else ""
    if screen == own_screen:
        return anchor or "#" + state_anchor("default")
    if screen not in enc_of:
        return None
    return f"{enc_of[screen]}.html{anchor}"


def wrap_links(svg_text: str, own_screen: str, enc_of: Mapping) -> list:
    """The lines of ``svg_text`` with an ``<a href>`` line before and a ``</a>`` line after the group of every widget that has a jump (the group of
    the screen itself, which may carry a screen-level jump, is left alone)."""
    out: list = []
    lines = svg_text.rstrip("\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _GOTO.search(line) if line.startswith("<g ") and 'data-kind="screen"' not in line else None
        href = href_of(m.group(1).split(",")[0], own_screen, enc_of) if m else None
        if href is None:
            out.append(line)
            i += 1
            continue
        out.append(f'<a href="{tf.esc_attr(href)}">')
        while True:
            out.append(lines[i])
            i += 1
            if out[-1] == "</g>" or i >= len(lines):
                break
        out.append("</a>")
    return out


def strip_links(lines: Sequence) -> list:
    """Inverse of ``wrap_links``: the lines without the anchor lines it inserts."""
    return [ln for ln in lines if ln != "</a>" and not _LINK_LINE.match(ln)]


def _inline(text: str) -> str:
    return _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", tf.esc_text(text))


def _cell_html(cell) -> str:
    return f"<code>{tf.esc_text(str(cell))}</code>" if isinstance(cell, cardmod.Code) and cell else tf.esc_text(str(cell))


def table_html(table: cardmod.Table, lang: str) -> list:
    heading, headers, rows, notes = cardmod.materialize(table, lang)
    out = ["<table>", f"<caption>{tf.esc_text(heading[3:])}</caption>", "<tr>" + "".join(f"<th>{tf.esc_text(h)}</th>" for h in headers) + "</tr>"]
    for k, cells in enumerate(rows):
        rid = table.ids[k] if k < len(table.ids) else ""
        open_tag = f'<tr id="r-{reading.enc(rid)}">' if rid else "<tr>"
        line = open_tag + "".join(f"<td>{_cell_html(c)}</td>" for c in cells) + "</tr>"
        if len(line) <= tf.FOLD_AT:
            out.append(line)
        else:
            out.extend([open_tag, *[f"<td>{_cell_html(c)}</td>" for c in cells], "</tr>"])
    out.append("</table>")
    out.extend(f'<p class="n">{tf.esc_text(x)}</p>' for x in notes)
    return out


def counts(lang: str, blocks: int, tables: int) -> str:
    return labels.t(lang, "gallery.counts", b=blocks, t=tables)


def render(card: cardmod.CardModel, first_page: cardmod.CardPage, states: Sequence, inline: Sequence, enc_of: Mapping, lang: str, not_inlined: Sequence) -> str:
    """The gallery text. ``states`` are ``(variant, note, svg text)`` in model order, ``inline`` the variants to inline, ``not_inlined`` the
    ``(variant, svg file name)`` pairs that get a link instead."""
    t = lambda key, **v: labels.t(lang, key, **v)  # noqa: E731
    head, _foot = reading.envelope(cardmod._page_meta(card, 1, 1, lang))
    page = reading.card_page_id(card.enc, 1)
    tables = [b for b in card.blocks if isinstance(b, cardmod.Table)]
    shown = [(v, note, text) for v, note, text in states if v in set(inline)]
    cnt = counts(lang, len(tables) + len(shown), len(tables))
    sha8 = first_page.text.rstrip("\n").rsplit(" sha8 ", 1)[1].split(" ", 1)[0] if " sha8 " in first_page.text else ""
    stamp = f"u={card.ui_sha8}" + (f" m={card.model_sha8}" if card.model_sha8 else "")
    out = ["<!doctype html>", f'<html lang="{lang}">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">",
           f"<title>{tf.esc_text(card.title)} · {tf.esc_text(reading.family_title('CARD', lang))}</title>",
           f'<meta name="atlas-page" content="{page}">', f'<meta name="atlas-stamp" content="{stamp}">', '<meta name="atlas-class" content="page">',
           f'<meta name="atlas-lang" content="{lang}">', '<meta name="atlas-schema" content="gemarc-atlas-read/1">',
           f'<link rel="alternate" type="text/markdown" href="../../{reading.card_path(card.enc, 1)}">', "<style>", *STYLE, "</style>", "</head>", "<body>", "<header>",
           "<p>" + tf.esc_text(t("gallery.head", page=page, title=card.title, i=1, n=1, counts=cnt, cls="page")) + "</p>",
           "<p>" + tf.esc_text(head[1]) + "</p>", "</header>", "<main>", f"<h1>{tf.esc_text(card.title)}</h1>", "<aside>"]
    for line in head[4:7]:
        out.append("<p>" + tf.esc_text(re.sub(r"^> ", "", line)) + "</p>")
    out.append("</aside>")
    out.extend("<p>" + _inline(x) + "</p>" for x in card.intro)
    for variant, note, text in shown:
        out.append(f'<figure id="{state_anchor(variant)}">')
        out.append("<figcaption>" + tf.esc_text(t("gallery.state", name=variant) + (" · " + tf.card_text(note) if note else "")) + "</figcaption>")
        out.extend(wrap_links(text, card.screen, enc_of))
        out.append("</figure>")
    if not_inlined:
        links = ", ".join(f'<a href="../../screens/{tf.esc_attr(name)}">{tf.esc_text(name)}</a>' for _v, name in not_inlined)
        out.append(f"<p>{tf.esc_text(t('gallery.not_inlined', list='@@'))}".replace("@@", links) + "</p>")
    for table in tables:
        out.extend(table_html(table, lang))
    out.extend(["</main>", "<footer>", "<nav>",
                " · ".join([f'<a href="../../{reading.card_path(card.enc, 1)}">{tf.esc_text(t("gallery.card"))}</a>', f'<a href="INDEX.html">{tf.esc_text(t("gallery.index"))}</a>',
                                 f'<a href="NAV.html">{tf.esc_text(t("gallery.nav"))}</a>', f'<a href="../START.html">{tf.esc_text(t("gallery.start"))}</a>']),
                "</nav>", "<p>" + tf.esc_text(t("gallery.sentinel", page=page, counts=cnt, sha8=sha8)) + "</p>", "</footer>", "</body>", "</html>"])
    return "\n".join(out) + "\n"
