"""Text measurement and text hygiene shared by the screen builders (design A3 4.3, 4.4, D-13). Pure functions, standard library only.

* Width model (D-13, measured once with the browser's ``getBBox`` on 191 strings): a CJK character is 107 and any other character 59
  hundredths of an em; ``width(text, em)`` is the ceiling of ``sum * em / 100`` in whole pixels. The ellipsis counts as a wide character (a
  CJK font draws it a full em wide). The constants are calibrated on the local font only; they size boxes, they are not a browser measurement.
* ``wrap`` and ``clip`` cut by that model; ``clean`` makes any author string legal XML text on one line; ``author`` also limits its length;
  ``defuse`` keeps the two frame placeholders of ``reading.envelope`` out of a page body; ``fold`` and ``unfold`` implement the rule for
  physical lines longer than 480 characters (design 2.4 rule 6).
* Comparison of strings between the SVG and the card ignores all white space (``squash``): a wrapped ``<text>`` loses the blanks it broke at.

The source holds no unicode escape on purpose: every special character is built with ``chr`` so that no tool between the author and the file
can turn an escape into a literal character.
"""
from __future__ import annotations

import re

CJK_UNITS = 107
OTHER_UNITS = 59
ELLIPSIS = chr(0x2026)
#: Longest physical line of a card, in characters (design 2.4 rule 6); above it a line is folded.
FOLD_AT = 480
#: Longest author string any screen artifact carries, in characters; a longer one ends in an ellipsis everywhere (SVG, card, gallery), so
#: that one string can never make a card row or a part larger than a page.
MAX_CHARS = 400

_REPLACEMENT = chr(0xFFFD)
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f" + chr(0xFFFE) + chr(0xFFFF) + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_BLANKS = re.compile(r"[\t\r\n]")
_WHITE = re.compile(r"\s+")
_GRAVE, _STAR, _BAR = chr(0x2CB), chr(0x2217), chr(0xA6)
_LBRACK, _RBRACK = chr(0xFF3B), chr(0xFF3D)


def is_wide(ch: str) -> bool:
    o = ord(ch)
    return 0x3000 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xFF00 <= o <= 0xFFEF or ch == ELLIPSIS


def units(text: str) -> int:
    return sum(CJK_UNITS if is_wide(ch) else OTHER_UNITS for ch in text)


def width(text: str, em: int = 14) -> int:
    """Pixel width of ``text`` at ``em`` pixels under the width model (integer ceiling)."""
    return -(-units(text) * em // 100)


def clean(value) -> str:
    """An author value as one line of legal XML 1.0 text: control characters, lone surrogates and the two non-characters become U+FFFD,
    tab, CR and LF become a blank. Applied once, at the model boundary, by every producer and every check."""
    return _BLANKS.sub(" ", _INVALID_XML.sub(_REPLACEMENT, value if isinstance(value, str) else str(value)))


def limit(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1].rstrip() + ELLIPSIS


def author(value) -> str:
    """An author string as the artifacts carry it: legal XML text on one line, at most ``MAX_CHARS`` characters."""
    return limit(clean(value))


def md_safe(text: str) -> str:
    """Text that survives a Markdown table cell and the ``visible`` normalisation of the twin check unchanged: a backtick becomes U+02CB, an
    asterisk U+2217, a vertical bar U+00A6, the brackets of a ``](`` link pattern their full-width forms. Only the card, the gallery and the
    pages use it; the SVG keeps the author text as it is, so a comparison between the two goes through ``cmp_form``."""
    out = text.replace("`", _GRAVE).replace("*", _STAR).replace("|", _BAR)
    return out.replace("[", _LBRACK).replace("]", _RBRACK) if "](" in out else out


def card_text(value) -> str:
    """What a card, an index or a navigation page prints for an author string."""
    return defuse(md_safe(author(value)))


def cmp_form(value) -> str:
    """The form in which an SVG string and a card string are compared: author text, made Markdown-safe, without any white space."""
    return squash(md_safe(author(value)))


def esc_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(text: str) -> str:
    return esc_text(text).replace('"', "&quot;")


def squash(text: str) -> str:
    """``text`` without any white space (the comparison form of the SVG-versus-card check)."""
    return _WHITE.sub("", text)


def defuse(text: str) -> str:
    """The two frame placeholders of ``reading.envelope`` (``{lines}``, ``{sha8}``) are legal author text; in a page body they would be
    refused by ``finalize``, so a blank follows the brace (``{ lines}``). ``squash`` makes the mapping invisible to the comparison."""
    return text.replace("{lines}", "{ lines}").replace("{sha8}", "{ sha8}")


def wrap(text: str, max_w: int, em: int = 14) -> list:
    """``text`` cut into lines whose width stays within ``max_w`` pixels (greedy). Lines break at a blank when there is one on the line (the
    blank is dropped) and at any character otherwise (CJK text, long words). Never returns an empty list; one character per line at least."""
    room_units = max(max_w, 1) * 100
    lines: list = []
    cur, cur_u, last_blank = "", 0, -1
    for ch in text:
        u = CJK_UNITS if is_wide(ch) else OTHER_UNITS
        while cur and ch != " " and (cur_u + u) * em > room_units:
            if last_blank >= 0:
                lines.append(cur[:last_blank].rstrip())
                cur = cur[last_blank + 1:]
                cur_u = units(cur)
                last_blank = -1
            else:
                lines.append(cur)
                cur, cur_u = "", 0
        if ch == " ":
            if not cur:
                continue
            last_blank = len(cur)
        cur += ch
        cur_u += u
    lines.append(cur.rstrip())
    return lines


def clip(text: str, max_w: int, em: int = 14) -> str:
    """``text`` itself when it fits in ``max_w`` pixels, else its longest prefix that fits together with an ellipsis."""
    if width(text, em) <= max_w:
        return text
    out, used = "", CJK_UNITS
    room_units = max(max_w, 1) * 100
    for ch in text:
        u = CJK_UNITS if is_wide(ch) else OTHER_UNITS
        if (used + u) * em > room_units:
            break
        out += ch
        used += u
    return out.rstrip() + ELLIPSIS


def fold(text: str, mark: str, at: int = FOLD_AT) -> list:
    """The physical lines of ``text`` when it is longer than ``at`` characters: the first line as it is, every other line starts with ``mark``;
    breaks fall at a blank when one is near the end of the line, else at the character limit. One line when short."""
    if len(text) <= at:
        return [text]
    lines, rest, first = [], text, True
    while rest:
        room = at if first else at - len(mark)
        if len(rest) <= room:
            piece, rest = rest, ""
        else:
            cut = rest.rfind(" ", room - 40, room)
            cut = cut if cut > 0 else room
            piece = rest[:cut]
            rest = rest[cut:].lstrip(" ") if rest[cut:cut + 1] == " " else rest[cut:]
        lines.append(piece if first else mark + piece)
        first = False
    return lines


def unfold(lines, mark: str) -> list:
    """Inverse of ``fold`` over a list of physical lines: a line that starts with ``mark`` is joined to the previous one. Breaks at a blank
    lose the blank, so the result equals the original up to white space (compare with ``squash``)."""
    out: list = []
    for line in lines:
        if out and mark and line.startswith(mark):
            out[-1] += line[len(mark):]
        else:
            out.append(line)
    return out
