"""The frozen interface of the reading surface (design A3 section 12.1): what lanes U and I import. Do not rename; add, never change.

====================================  ==============================================================================================
``envelope(meta) -> (head, foot)``     the real page frame (``envelope.py``): locator lines, scope card, neighbours, sentinel placeholders
``envelope_bytes(family_class) -> int``  bytes of head + foot of the widest page of the family class, zh-CN, no continuation (``envelope_size``
                                       is the same with a language and the continuation switch); the planner and RS-103 share it
``enc(id) -> str``                     injective file-name encoding with the ``+h+`` fallback (``names.py``); ``enc_many`` for a set of ids
``BUDGETS``                            ``cap_md`` (card 6,000, page 9,000), ``cap_html`` (2.4 times), further byte limits; no envelope constant
``finalize(lines) -> str``             fills the line count and the ``sha8`` of the sentinel (the line-level one; the manifest writer of R1 is a
                                       different function and lives at package level, never here)
``verify_sentinel(text) -> [problem]``  the RS-104 check, one implementation for R, U and the tests
====================================  ==============================================================================================

``family_class`` is ``"<FAMILY>:<class>"`` (or the pair) with the families and classes of ``names.FAMILIES`` (``S:page``, ``W:card``, ``Q:page``,
``INDEX:card``, ``START:card``, ``CARD:page``, ``SCREENS:card``, ``NAV:card`` ...); an unknown family or class raises ``ValueError``.
``content_capacity`` is what is left for the body of a page of that class.

Added in R2 (additive, nothing above changed): ``render_site(files, lang, gallery=None, context=None, problems=None) -> {twin path: bytes}`` renders the HTML
twins of the Markdown files of ``files`` (``{path: bytes | str}``; lane I passes the screens INDEX and NAV of lane U through it, the pages of
``reading.build`` as ``context`` so that links resolve to twins; a card has no twin of this renderer: its twin is the gallery page of lane U, and
``gallery(card_path, card_text) -> bytes | None`` is the hook through which a caller may hand it in), ``render_twin(path, text, lang, index=None)
-> bytes`` renders one page, ``visible_md(text)`` / ``visible_html(data)`` are the two atom lists
the equality check RS-114 compares.

``meta`` of ``envelope`` is a mapping; its keys (``page index count cls title project lang stamp ui_stamp content covers excludes howto continuation
prev next up start``), their grammar and the failures are documented at the top of ``envelope.py``. A page is ``head + body + foot`` as lists of
physical lines; ``finalize(head + body + foot)`` returns the file text. A screen card passes its own ``title``, ``ui_stamp`` and
``content="prototype"``; it has no ``prev``/``next`` (its parts are named in the continuation line).
"""
from __future__ import annotations

from .budgets import BUDGETS, CARD_FIELD_CAPS, ENVELOPE_CAPS
from .envelope import (  # noqa: F401  (re-exports)
    LINES_TOKEN, SHA_TOKEN, TEXT, bucket_of, clip_bytes, envelope, envelope_limit, envelope_size, finalize, format_page_list, verify_sentinel,
    worst_case_meta,
)
from .render_html import render_site, render_twin, visible_html, visible_md  # noqa: F401  (R2, additive: the HTML twins)
from .names import (  # noqa: F401  (re-exports)
    FAMILIES, PAGE_ID_RE, PAGE_REF_RE, SECTION_SIGN, card_page_id, card_path, enc, enc_many, family_class_key, family_title, is_page_id, page_id,
    page_path, page_ref, parse_family_class, parse_page_id, parse_page_ref, twin_path,
)


def envelope_bytes(family_class) -> int:
    """Bytes of the frame (head + foot, newlines counted) of the widest page of a family class, in zh-CN, without continuation lines."""
    return envelope_size(family_class)


def content_capacity(family_class, lang: str = "zh-CN", continuation: bool = False) -> int:
    """``cap_md[class]`` minus the worst-case frame: what a page of this family class can hold of body, in bytes."""
    _family, cls = parse_family_class(family_class)
    return BUDGETS["cap_md"][cls] - envelope_size(family_class, lang, continuation)


__all__ = [
    "BUDGETS", "CARD_FIELD_CAPS", "ENVELOPE_CAPS", "FAMILIES", "LINES_TOKEN", "PAGE_ID_RE", "PAGE_REF_RE", "SECTION_SIGN", "SHA_TOKEN", "TEXT",
    "bucket_of", "card_page_id", "card_path", "clip_bytes", "content_capacity", "enc", "enc_many", "envelope", "envelope_bytes", "envelope_limit",
    "envelope_size", "family_class_key", "family_title", "finalize", "format_page_list", "is_page_id", "page_id", "page_path", "page_ref",
    "parse_family_class", "parse_page_id", "parse_page_ref", "render_site", "render_twin", "twin_path", "verify_sentinel", "visible_html", "visible_md",
    "worst_case_meta",
]
