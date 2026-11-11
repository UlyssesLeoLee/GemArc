"""The page envelope: the visible locator lines, the scope card, the neighbour line and the sentinel (design A3 section 2.5).

Pure functions, no I/O. A page is ``head + body + foot`` where ``envelope(meta)`` gives ``(head, foot)`` as lists of physical lines
(no newline characters) and the body is the page builder's own list of lines. ``finalize(lines)`` then fills the two placeholders the
frame carries (the physical line count in the first line and in the sentinel, and the ``sha8`` in the sentinel) and returns the file
text; ``verify_sentinel(text)`` is the one implementation of the RS-104 check.

Head, in this order (8 lines): locator line 1 (page id, title, ``i/n``, line count, class), locator line 2 (project, language, stamps,
content kind, the one-line staleness check), the ``#`` heading, an empty line, the scope card ``> 含:`` ``> 不含:`` ``> 读法:`` (plus
``> 续:`` on a continuation part) and an empty line. Foot: an empty line, the neighbour line (when there is a neighbour), on a part of a
longer unit the line ``（未完：下一部分在 §<页>）`` or ``（本单元到此结束）``, and the sentinel
``页尾 §<页> · 共 <N> 行 · sha8 <H> · 以上为全部内容``. Everything is visible text, never a comment or an attribute (D-6).

``meta`` is a mapping with these keys (a missing required key or a value out of its grammar raises ``ValueError``: a frame that cannot be
rendered is a generator bug, RS-103 is an integrity check):

``page``          page id without the section sign (``S07``, ``START``, ``CARD-<enc>``; the grammar of ``names.PAGE_ID_RE``)
``index, count``  ``i`` and ``n`` of ``i/n`` (default 1 and 1; ``1 <= i <= n``)
``cls``           ``card`` or ``page``; the family of ``page`` must have it
``title``         the title (default: the family title); the heading adds ``第 i/n 页`` when ``n > 1``
``project``       ``meta.projectId`` (shown clipped to 40 characters)
``lang``          the language of the page; ``ja-JP`` uses the English fixed texts (D-24)
``stamp``         ``m=`` model sha8 (8 hex digits) and/or ``ui_stamp`` ``u=`` ui sha8; at least one
``content``       ``structure``, ``sealed`` (families C V Q A P only) or ``prototype`` (the screen families only); default: the first the family allows
``covers``, ``excludes``, ``howto``   the three card lines; one line each, clipped (with ``…``) to ``budgets.CARD_FIELD_CAPS``
``continuation``  ``{"unit": id, "part": i, "parts": n, "others": [page ids], "next": page id or None}`` on a part of a longer unit
``prev``, ``next``  ``(page id, relative href)`` pairs;  ``up`` and ``start``: relative hrefs of the dimension index and of START

The fixed texts live in ``derive/i18n/<lang>.json`` under ``read`` (``TEXT`` is their flat view, keys ``read.*``); the checks derive their
regular expressions from the same templates, so no check writes a Chinese literal of its own.
"""
from __future__ import annotations

import hashlib
import re
from typing import Mapping, Sequence

from ..derive import i18n as T
from . import names as N
from .budgets import CARD_FIELD_CAPS, HREF_MAX, ID_CLIP, PROJECT_CLIP, TITLE_CLIP, envelope_cap

LINES_TOKEN = "{lines}"
SHA_TOKEN = "{sha8}"
HEX8 = re.compile(r"^[0-9a-f]{8}$")
STALE_CHECK = 'gemarc_atlas_index scope="stamp"'
OTHERS_CLIP = 48      # bytes of the list of the other parts of a longer unit

def _flat(prefix: str, node) -> dict:
    """``{"read.head.page": text, ...}`` from the nested ``read`` object of a language table."""
    if isinstance(node, str):
        return {prefix: node}
    if not hasattr(node, "keys"):       # a list of texts (a table header): not a template of the frame
        return {}
    out: dict = {}
    for key in node:
        out.update(_flat(f"{prefix}.{key}", node[key]))
    return out


#: The fixed texts of every page frame, ``{"zh-CN": {"read.head.page": ...}, "en-US": {...}}`` (ja-JP uses the en-US texts, D-24). They live
#: in ``derive/i18n/<lang>.json`` under the top-level object ``read``; this is the flat view of it (the same bytes as in R0).
TEXT = {lang: _flat("read", T.tables(lang)["read"]) for lang in ("zh-CN", "en-US")}


def texts(lang: str) -> dict:
    return TEXT[N.text_lang(lang)]


# ---------------------------------------------------------------------------------------------------- small helpers

def clip_bytes(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` UTF-8 bytes at a character boundary, ending in ``…`` when something was cut."""
    if len(text.encode("utf-8", "surrogatepass")) <= limit:
        return text
    mark = "…"
    room = limit - len(mark.encode("utf-8"))
    out, size = [], 0
    for ch in text:
        step = len(ch.encode("utf-8", "surrogatepass"))
        if size + step > room:
            break
        out.append(ch)
        size += step
    return "".join(out).rstrip() + mark


def one_line(text, what: str) -> str:
    """A string for one physical line: every run of white space (line breaks included) becomes one space; the frame placeholders are refused."""
    if not isinstance(text, str):
        raise ValueError(f"{what} must be a string")
    if LINES_TOKEN in text or SHA_TOKEN in text:
        raise ValueError(f"{what} contains a frame placeholder")
    return " ".join(text.split())


def _fill(template: str, **values) -> str:
    """``{name}`` placeholders replaced in one pass (a value that contains braces is never scanned again)."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def format_page_list(pages: Sequence[str], sep: str | None = None, limit: int = OTHERS_CLIP) -> str:
    """``§S08-§S10、§S12`` from page ids: consecutive numbers of one letter family become a range; the result is clipped to ``limit`` bytes."""
    sep = TEXT["zh-CN"]["read.range.sep"] if sep is None else sep
    parsed = [(p, N.parse_page_id(p)) for p in pages]
    for page, info in parsed:
        if info is None:
            raise ValueError(f"{page!r} is not a page id")
    out, i = [], 0
    while i < len(parsed):
        page, info = parsed[i]
        j = i
        if info.number is not None:
            while (j + 1 < len(parsed) and parsed[j + 1][1].number is not None and parsed[j + 1][1].family == info.family
                   and parsed[j + 1][1].number == parsed[j][1].number + 1):
                j += 1
        if j > i:
            out.append(N.page_ref(page) + "-" + N.page_ref(parsed[j][0]))
        else:
            out.append(N.page_ref(page))
        i = j + 1
    return clip_bytes(sep.join(out), limit)


#: the content kinds a family can have: a page of the others can only be ``structure`` (so the worst case is known by construction)
def content_kinds(family: str) -> tuple:
    if family in ("SCREENS", "NAV", "CARD"):
        return ("prototype",)
    if family in ("C", "V", "Q", "A", "P"):
        return ("structure", "sealed")
    return ("structure",)


def bucket_of(family: str, continuation: bool = False) -> str:
    """The envelope bucket of a family (``standard``, ``wide``, ``screen``); a continuation part of a standard family is ``wide``."""
    if family == "CARD":
        return "screen"
    return "wide" if N.FAMILIES[family].wide or continuation else "standard"


def card_caps(family: str, continuation: bool = False):
    """The ``CARD_FIELD_CAPS`` set of a family: ``standard``, ``wide``, or ``tight`` (continuation parts of a wide family, screen cards)."""
    if family == "CARD":
        return CARD_FIELD_CAPS["tight"]
    if N.FAMILIES[family].wide:
        return CARD_FIELD_CAPS["tight" if continuation else "wide"]
    return CARD_FIELD_CAPS["standard"]


def _family_of(page: str) -> tuple:
    info = N.parse_page_id(page)
    if info is None:
        raise ValueError(f"{page!r} is not a page id")
    return info.family, N.FAMILIES[info.family]


def _check_href(href, what: str) -> str:
    if not isinstance(href, str) or not href or len(href) > HREF_MAX or "\n" in href or ")" in href or " " in href:
        raise ValueError(f"{what} must be a relative path of 1-{HREF_MAX} characters without spaces or ')'")
    return href


#: a page id in a frame is at most 12 characters (``INDEX-QA-999``), a screen card id at most ``CARD-`` + an 80-character name + ``-99``
PAGE_ID_MAX = 12
CARD_ID_MAX = 5 + N.ENC_MAX + 3
MAX_NUMBER = 999
MAX_LINES = 9999


def _check_page(page, what: str, family: str) -> str:
    """A referenced page id: the grammar, and the width the worst case is computed with (a longer one is refused, not clipped)."""
    if not N.is_page_id(page):
        raise ValueError(f"{what}: {page!r} is not a page id")
    limit = CARD_ID_MAX if family == "CARD" else PAGE_ID_MAX
    if len(page) > limit:
        raise ValueError(f"{what}: the page id {page!r} is longer than {limit} characters")
    return page


def _check_pair(pair, what: str, family: str):
    if pair is None:
        return None
    if not (isinstance(pair, (tuple, list)) and len(pair) == 2):
        raise ValueError(f"{what} must be a (page id, href) pair")
    page, href = pair
    return _check_page(page, what, family), _check_href(href, what)


# ---------------------------------------------------------------------------------------------------- the frame

def envelope(meta: Mapping) -> tuple:
    """``(head_lines, foot_lines)`` of a page (module docstring). The first head line and the last foot line carry the placeholders that
    ``finalize`` fills; nothing else is left open."""
    if not isinstance(meta, Mapping):
        raise ValueError("meta must be a mapping")
    page = meta.get("page")
    if not N.is_page_id(page):
        raise ValueError(f"meta['page'] must be a page id without the section sign, got {page!r}")
    family, spec = _family_of(page)
    _check_page(page, "meta['page']", family)
    cls = meta.get("cls")
    if cls not in spec.classes:
        raise ValueError(f"page {page!r} (family {family}) cannot have the class {cls!r}; it has {spec.classes}")
    index, count = meta.get("index", 1), meta.get("count", 1)
    for name, value in (("index", index), ("count", count)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_NUMBER:
            raise ValueError(f"meta[{name!r}] must be an integer from 1 to {MAX_NUMBER}")
    if index > count:
        raise ValueError("meta['index'] is larger than meta['count']")
    lang = meta.get("lang")
    t = texts(lang)  # validates the language
    project = one_line(meta.get("project"), "meta['project']")
    if not project:
        raise ValueError("meta['project'] is empty")
    stamp, ui_stamp = meta.get("stamp"), meta.get("ui_stamp")
    for name, value in (("stamp", stamp), ("ui_stamp", ui_stamp)):
        if value is not None and not (isinstance(value, str) and HEX8.fullmatch(value)):
            raise ValueError(f"meta[{name!r}] must be 8 lowercase hex digits")
    if stamp is None and ui_stamp is None:
        raise ValueError("meta needs 'stamp' (m=) or 'ui_stamp' (u=)")
    stamps = " · ".join(([f"u={ui_stamp}"] if ui_stamp else []) + ([f"m={stamp}"] if stamp else []))
    caps = card_caps(family, bool(meta.get("continuation")))
    content_key = meta.get("content", content_kinds(family)[0])
    if content_key not in content_kinds(family):
        raise ValueError(f"page family {family} cannot have the content kind {content_key!r}; it has {content_kinds(family)}")
    content = t["read.content." + content_key]
    info = N.parse_page_id(page)
    default_title = N.family_title(family, lang, info.dim if family in ("INDEX", "P") else None)
    title = one_line(meta.get("title", default_title), "meta['title']") or default_title
    if family != "CARD" and title != default_title:
        raise ValueError("only a screen card has a title of its own (the other families have a fixed title, so the frame width is known)")
    title = clip_bytes(title, TITLE_CLIP)

    head = [
        _fill(t["read.head.page"], page=page, title=title, i=index, n=count, lines=LINES_TOKEN, cls=cls),
        _fill(t["read.head.project"], project=clip_bytes(project, PROJECT_CLIP), lang=lang, stamps=stamps, content=content, check=STALE_CHECK),
        _fill(t["read.head.heading" if count == 1 else "read.head.heading_part"], title=title, i=index, n=count),
        "",
    ]
    for key in ("covers", "excludes", "howto"):
        text = clip_bytes(one_line(meta.get(key, ""), f"meta[{key!r}]"), caps[key])
        head.append(_fill(t["read.card." + key], text=text))
    continuation = meta.get("continuation")
    foot_more = None
    if continuation:
        if not isinstance(continuation, Mapping):
            raise ValueError("meta['continuation'] must be a mapping")
        unit = clip_bytes(one_line(continuation.get("unit"), "continuation unit"), ID_CLIP)
        part, parts = continuation.get("part"), continuation.get("parts")
        if not (isinstance(part, int) and isinstance(parts, int) and not isinstance(part, bool) and not isinstance(parts, bool)
                and 1 <= part <= parts <= MAX_NUMBER and parts >= 2):
            raise ValueError(f"continuation needs 1 <= part <= parts <= {MAX_NUMBER} and parts >= 2")
        others_list = [_check_page(p, "continuation['others']", family) for p in (continuation.get("others") or [])]
        others = format_page_list(others_list, t["read.range.sep"])
        head.append(_fill(t["read.card.continued"], unit=unit, part=part, parts=parts, others=others or "-"))
        nxt = continuation.get("next")
        if nxt is not None:
            _check_page(nxt, "continuation['next']", family)
        foot_more = _fill(t["read.foot.more"], page=nxt) if nxt else t["read.foot.end_unit"]
    head.append("")

    links = []
    prev, nxt_pair = _check_pair(meta.get("prev"), "meta['prev']", family), _check_pair(meta.get("next"), "meta['next']", family)
    if family == "CARD" and (prev or nxt_pair):
        raise ValueError("a screen card has no prev/next link: its other parts are named in the continuation line")
    if prev:
        links.append(_fill(t["read.foot.prev"], page=prev[0], href=prev[1]))
    if nxt_pair:
        links.append(_fill(t["read.foot.next"], page=nxt_pair[0], href=nxt_pair[1]))
    if meta.get("up") is not None:
        links.append(_fill(t["read.foot.up"], href=_check_href(meta["up"], "meta['up']")))
    if meta.get("start") is not None:
        links.append(_fill(t["read.foot.start"], href=_check_href(meta["start"], "meta['start']")))
    foot = [""]
    if links:
        foot.append(_fill(t["read.foot.neighbours"], links=" · ".join(links)))
    if foot_more:
        foot.append(foot_more)
    foot.append(_fill(t["read.foot.sentinel"], page=page, lines=LINES_TOKEN, sha8=SHA_TOKEN))
    return head, foot


def finalize(lines: Sequence[str]) -> str:
    """The file text of a complete page (head + body + foot): the physical line count goes into the first line and the sentinel, then
    ``sha8`` (the first 8 hex digits of the SHA-256 of every byte except the last line and its newline) into the sentinel. The text ends
    with one ``\\n``; the count includes the sentinel line itself."""
    lines = list(lines)
    if not 2 <= len(lines) <= MAX_LINES:
        raise ValueError(f"a page has 2 to {MAX_LINES} lines (the frame is sized for a four-digit line count)")
    for number, line in enumerate(lines, start=1):
        if not isinstance(line, str) or "\n" in line or "\r" in line:
            raise ValueError(f"line {number} is not a single physical line")
    if lines[0].count(LINES_TOKEN) != 1 or lines[-1].count(LINES_TOKEN) != 1 or lines[-1].count(SHA_TOKEN) != 1:
        raise ValueError("the first line and the sentinel must each carry the line-count placeholder once, the sentinel the sha8 placeholder once")
    if any(LINES_TOKEN in line or SHA_TOKEN in line for line in lines[1:-1]):
        raise ValueError("a frame placeholder sits in the body")
    count = str(len(lines))
    lines[0] = lines[0].replace(LINES_TOKEN, count)
    lines[-1] = lines[-1].replace(LINES_TOKEN, count)
    prefix = "\n".join(lines[:-1]) + "\n"
    digest = hashlib.sha256(prefix.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return prefix + lines[-1].replace(SHA_TOKEN, digest) + "\n"


def _pattern(template: str, **groups: str) -> str:
    out = re.escape(template)
    for key, expression in groups.items():
        out = out.replace(re.escape("{" + key + "}"), "(?P<" + key + ">" + expression + ")")
    return out


def sentinel_regex(lang: str) -> re.Pattern:
    """The sentinel template of a language as a regular expression (the checks derive it from the template, not from a literal)."""
    return re.compile("^" + _pattern(texts(lang)["read.foot.sentinel"], page=r"[^\s·]+", lines=r"\d+", sha8=r"[0-9a-f]{8}") + "$")


def first_line_regex(lang: str) -> re.Pattern:
    return re.compile("^" + _pattern(texts(lang)["read.head.page"], page=r"[^\s·]+", title=r".+?", i=r"\d+", n=r"\d+", lines=r"\d+", cls=r"card|page") + "$")


def verify_sentinel(text: str) -> list:
    """Problems of a finished page text (empty list = the sentinel verifies): the last line matches the sentinel template of some language,
    its line count equals the physical line count and the count in the first line, its page id equals the first line's, and its ``sha8``
    is the SHA-256 prefix of everything before the last line."""
    problems: list = []
    if not text.endswith("\n") or text.endswith("\n\n"):
        return ["the text must end with exactly one newline after the sentinel"]
    lines = text[:-1].split("\n")
    last_match = first_match = None
    for lang in ("zh-CN", "en-US"):
        last_match = last_match or sentinel_regex(lang).match(lines[-1])
        first_match = first_match or first_line_regex(lang).match(lines[0])
    if last_match is None:
        return ["the last line is not a sentinel"]
    if first_match is None:
        return ["the first line is not a page locator line"]
    count = len(lines)
    if int(last_match.group("lines")) != count:
        problems.append(f"the sentinel counts {last_match.group('lines')} lines, the text has {count}")
    if int(first_match.group("lines")) != count:
        problems.append(f"the first line counts {first_match.group('lines')} lines, the text has {count}")
    if last_match.group("page") != first_match.group("page").lstrip(N.SECTION_SIGN):
        problems.append("the page id of the sentinel differs from the first line")
    digest = hashlib.sha256((text[:text.rindex("\n", 0, len(text) - 1) + 1]).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    if last_match.group("sha8") != digest:
        problems.append("the sha8 of the sentinel does not match the page bytes")
    return problems


# ---------------------------------------------------------------------------------------------------- worst case

def worst_case_meta(family_class, lang: str = "zh-CN", continuation: bool = False) -> dict:
    """The metadata of the widest page of a family class: the widest page id, ``999/999``, every free field at its cap, both stamps for a
    screen page, the longest content label, every neighbour present with the longest link, and (when asked) the continuation lines.
    Only the placeholders of ``finalize`` are narrower than what they become (four digits are reserved by ``envelope_size``)."""
    family, cls = N.parse_family_class(family_class)
    spec = N.FAMILIES[family]
    caps = card_caps(family, continuation)
    ui = family in ("SCREENS", "NAV", "CARD")
    page = {"START": "START", "INDEX": "INDEX-QA-999", "SCREENS": "SCREENS-9999", "NAV": "NAV-99999999",
            "CARD": "CARD-" + "x" * N.ENC_MAX + "-99"}.get(family, family + "999")
    if family == "P":  # the merged page whose fixed title is the longer one
        page = max(("P1D", "P2D", "P3Q"), key=lambda p: len(N.family_title("P", lang, p[1:]).encode("utf-8")))
    href = "../" * 3 + "x" * (HREF_MAX - 12) + ".md"
    others = [N.page_id("S", n) for n in range(100, 1000, 2)]
    title = "t" * TITLE_CLIP if family == "CARD" else N.family_title(family, lang, N.parse_page_id(page).dim if family in ("INDEX", "P") else None)
    meta = {
        "page": page, "index": 999, "count": 999, "cls": cls, "lang": lang, "project": "p" * PROJECT_CLIP,
        "stamp": "f" * 8, "ui_stamp": "f" * 8 if ui else None,
        "content": max(content_kinds(family), key=lambda key: len(texts(lang)["read.content." + key].encode("utf-8"))),
        "title": title,
        "covers": "a" * caps["covers"], "excludes": "a" * caps["excludes"], "howto": "a" * caps["howto"],
        "up": href, "start": href,
    }
    if family != "CARD":
        meta["prev"], meta["next"] = (page, href), (page, href)
    if continuation:
        meta["continuation"] = {"unit": "u" * ID_CLIP, "part": 999, "parts": 999, "others": others, "next": page}
    return meta


def envelope_size(family_class, lang: str = "zh-CN", continuation: bool = False) -> int:
    """Bytes (UTF-8, newlines counted) of head + foot of the widest page of a family class, rendered by the real renderer with four-digit line
    counts. The planner reserves this much of the page budget; ``finalize`` can only produce something narrower."""
    head, foot = envelope(worst_case_meta(family_class, lang, continuation))
    four = "9999"
    return sum(len(line.replace(LINES_TOKEN, four).replace(SHA_TOKEN, "f" * 8).encode("utf-8")) + 1 for line in head + foot)


def envelope_limit(family_class, lang: str = "zh-CN", continuation: bool = False) -> int:
    """The RS-103 limit of a family class (``bucket_of``: wide for judgement legends and merged pages and every continuation part)."""
    family, _cls = N.parse_family_class(family_class)
    return envelope_cap(bucket_of(family, continuation), lang)
