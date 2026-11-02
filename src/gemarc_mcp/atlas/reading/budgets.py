"""Byte budgets of the reading surface (design A3 D-4, section 1.2). Pure data.

``BUDGETS`` is what other lanes import. It has no envelope constant: the bytes the page frame takes are not a constant, the planner asks
the real renderer (``envelope.envelope_size``) for them. The limits ``ENVELOPE_CAPS`` below are the checks (RS-103) that bound that renderer; they
are never planning inputs on their own.

Where the numbers come from (study-reading): the Claude Code ``Read`` tool refuses a page above 25k tokens and ``Grep`` hides lines above 500
characters; Codex ``cat`` keeps the head and the tail of an output above about 10,000 bytes and drops the middle. A Markdown page is therefore
at most 9,000 bytes (1,000 left for the wrapper line of the client, an unmeasured assumption) and a card at most 6,000. An HTML twin may be
2.4 times as large (the measured ratio is 1.32-1.87); the gallery page of a screen at most 40,000 bytes; a semantic SVG at most 20,000.
"""
from __future__ import annotations

from types import MappingProxyType

CAP_MD = MappingProxyType({"card": 6000, "page": 9000})
#: 2.4 times the Markdown cap (14,400 and 21,600)
CAP_HTML = MappingProxyType({cls: cap * 24 // 10 for cls, cap in CAP_MD.items()})

BUDGETS = MappingProxyType({
    "cap_md": CAP_MD,
    "cap_html": CAP_HTML,
    "html_ratio_tenths": 24,               # cap_html = cap_md * 24 / 10
    "cap_svg": 20000,                      # one semantic screen SVG (a degradation bucket: more rows are dropped first)
    "cap_mmd": 6000,                       # one diagram file (a warning, never a failure)
    "cap_gallery": 40000,                  # one gallery page (more is degraded to the default state only)
    "cap_png": 150000,                     # one default-state PNG, an expectation of the pilot, not a check
    "line_chars": 500,                     # longest physical line of a Markdown, HTML, manifest or JSON file for an AI
    "tsv_line_chars": 400,                 # longest line of ids.tsv and refs.tsv
    "wrap_chars": 480,                     # a longer line is folded (design 2.4 rule 6)
    "style_bytes": 2000,                   # the single <style> of a twin
})

#: RS-103: the largest the head and the foot of one page may be (zh-CN bytes; en-US is allowed 10 percent more). ``wide`` is for the
#: pages with a judgement legend or several families on one page and for every continuation part (design 2.5: a part that carries the
#: continuation card line and the "to be continued" line measured 1,051 B, which does not fit the standard bucket). ``screen`` is for
#: the screen card (design 4.5 gives the card its own size caps, not an envelope limit): its page id carries an encoded screen id of up
#: to 80 characters twice.
ENVELOPE_CAPS = MappingProxyType({"standard": 1000, "wide": 1300, "screen": 1500})
ENVELOPE_EN_TENTHS = 11

#: Caps of the free text of the scope card, in bytes of one physical line without its ``> label: `` prefix (clipped with an ellipsis above).
#: Three sets, chosen so that the worst-case envelope of every family class stays inside ``ENVELOPE_CAPS`` (the tests measure it):
#: ``standard`` for the plain pages of the reading families, ``wide`` for the plain pages that carry a judgement legend or several families
#: (``A P C V Q SCREENS NAV``), ``tight`` for continuation parts of those families and for screen cards (a longer frame line is paid for by
#: shorter ``covers``/``excludes`` lines; the ``howto`` line keeps room for a legend).
CARD_FIELD_CAPS = MappingProxyType({
    "standard": MappingProxyType({"covers": 120, "excludes": 130, "howto": 210}),
    "wide": MappingProxyType({"covers": 160, "excludes": 170, "howto": 330}),
    "tight": MappingProxyType({"covers": 100, "excludes": 100, "howto": 330}),
})
#: longest displayed project id, title, and id shown in a continuation line
PROJECT_CLIP = 32
TITLE_CLIP = 48
ID_CLIP = 44
#: longest relative link of the neighbour line (a link cannot be clipped; a longer one is refused)
HREF_MAX = 20


def envelope_cap(bucket: str, lang: str = "zh-CN") -> int:
    """The RS-103 limit in bytes of a bucket (``standard``, ``wide``, ``screen``); en-US and ja-JP get 10 percent more."""
    cap = ENVELOPE_CAPS[bucket]
    return cap if lang == "zh-CN" else cap * ENVELOPE_EN_TENTHS // 10
