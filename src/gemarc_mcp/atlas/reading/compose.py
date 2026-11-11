"""The body of a page of one family or of several (single-page mode ``A01``, folded dimensions ``P1D``/``P2D``).

A page of one family is ``render_body`` of its units. A page of several families gives each family a ``##`` heading, its own ``> covers`` line
(the same card line a page of that family would carry) and its sections one level lower (design 2.4 rule 2: one ``##`` section per family,
each with its own ``含`` line, one shared frame).
"""
from __future__ import annotations

from . import blocks as B
from .blocks import Body
from .cards import covers_text
from .source import Source
from .text import tx


def compose_body(src: Source, blocks: list, shift: int = 0) -> Body:
    """``blocks`` is ``[(Family, [Unit])]``; ``shift`` 0 for a page of one family, 1 for a page of several."""
    lang = src.lang
    if len(blocks) == 1 and shift == 0:
        fam, units = blocks[0]
        body = B.render_body(lang, fam.sections, units, 0)
        for info in body.sections:
            info.family = fam.key
        return body
    body = Body()
    for fam, units in blocks:
        inner = B.render_body(lang, fam.sections, units, shift)
        if not inner.lines:
            continue
        if body.lines:
            body.lines.append("")
        body.lines.append("## " + tx(lang, "family." + fam.key))
        body.lines.append(tx(lang, "card.covers", text=covers_text(src, "A", inner.sections, units)))
        offset = len(body.lines)
        body.lines.extend(inner.lines)
        for idx, mark in inner.marks.items():
            body.marks[idx + offset] = (*mark[:3], mark[3] + offset)
        for info in inner.sections:
            info.heading += offset
            info.first += offset
            info.last += offset
            info.family = fam.key
            body.sections.append(info)
        body.fit += inner.fit
    return body


def compose_size(src: Source, blocks: list, shift: int = 1) -> int:
    return compose_body(src, blocks, shift).size
