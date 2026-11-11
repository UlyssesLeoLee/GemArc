"""Names of the reading surface: page ids and the section sign, the page families, the file-name encoding ``enc``.

Pure functions, no I/O (design A3 sections 2.1, 2.5, 2.6; decisions D-2, D-5).

Page ids (D-5). A page has a short id made of a family letter and a two (three when more than 99) digit number (``S07``), or one of
the fixed ids ``START``, ``SCREENS``, ``NAV``, ``INDEX-<dim>``, ``CARD-<enc>``, ``P1D``, ``P2D``, ``P3Q``. File names never contain a model id. In running
text a page id is always written with the section sign, ``§S07``: ``§`` is not in the first-character set of a model id (``ID_RE``
starts with ``[A-Za-z0-9]``), so ``§S07`` can never be mistaken for the model element called ``S07``. ``doc unit="page:§S07"`` also
accepts the bare ``S07``; ``parse_page_ref`` does both.

``CARD-<enc>`` is opaque after the prefix: a continuation card is ``CARD-<enc>-2`` and a screen id may itself end in ``-<digits>``, so the
tail cannot be split without knowing the manifest. ``parse_page_id`` therefore never splits it (``part`` stays ``None`` for cards); build
card ids and paths with ``card_page_id`` and ``card_path``, and resolve a given card id by exact lookup in the manifest.

File names (``enc``, design 2.6). Kept as they are: ``[A-Za-z0-9._-]``. ``#`` becomes ``~``. Every other character ``c`` becomes
``+<hex of its code point>+`` (lower case, no padding), so ``~`` and ``+`` only ever introduce an escape and the mapping is injective.
Windows rules on top: a trailing ``.`` is escaped (Windows drops it); a stem (text before the first ``.``) that equals a device name
(``CON PRN AUX NUL COM1-9 LPT1-9``, any case) gets the prefix ``_``; an id that itself starts with ``_`` followed by a device stem has that
first ``_`` escaped so it cannot collide with the prefixed name; a name longer than 80 characters is replaced by its first 48 characters
(cut at an escape boundary) plus ``+h+`` plus the first 12 hex digits of ``sha256(id)``. The hashed form ends in ``+h+`` and 12 hex digits
with no ``+`` after it; a plain name cannot end like that, because a ``+`` always opens an escape and an escape is closed by a second ``+``
(``h`` is not a hex digit), so a hashed name never equals the plain name of another id (``is_hashed_name`` tells them apart; the substring
``+h+`` alone proves nothing: ``+2b+h+2b+`` contains it). ``enc(id)`` is a function of one id. Case-insensitive collisions (``Pump`` and ``pump`` on NTFS) belong to a set of ids and are
handled by ``enc_many``, which writes every member of a colliding group in the hashed form. Never recover an id from a file name.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable, NamedTuple

from ..derive import i18n as T

SECTION_SIGN = "§"

LETTER_FAMILIES = "SDMWRTECVQGA"
DIMS = ("1D", "2D", "3D", "4D", "5D", "QA")

ENC_KEEP = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
ENC_MAX = 80
ENC_HASH_PREFIX = 48
ENC_HASH_LEN = 12
ENC_CHARS = "A-Za-z0-9._~+-"
WINDOWS_DEVICES = frozenset(["CON", "PRN", "AUX", "NUL"] + [f"COM{n}" for n in range(1, 10)] + [f"LPT{n}" for n in range(1, 10)])

PAGE_ID_BODY = (
    r"(?:START|SCREENS(?:-\d+)?|NAV(?:-\d+)?|CARD-[" + ENC_CHARS + r"]+|INDEX-(?:1D|2D|3D|4D|5D|QA)(?:-\d+)?|[" + LETTER_FAMILIES + r"]\d{2,3}|P(?:1D|2D|3Q))"
)
#: A bare page id (``S07``); ``PAGE_REF_RE`` is the same with the section sign (``§S07``), the form used in text.
PAGE_ID_RE = re.compile("^" + PAGE_ID_BODY + "$")
PAGE_REF_RE = re.compile("^" + SECTION_SIGN + PAGE_ID_BODY + "$")
_PARTS = re.compile(r"^(?P<family>SCREENS|NAV)(?:-(?P<part>\d+))?$|^INDEX-(?P<dim>1D|2D|3D|4D|5D|QA)(?:-(?P<ipart>\d+))?$")


# ---------------------------------------------------------------------------------------------------- families

class Family(NamedTuple):
    key: str
    dim: str | None          # the dimension directory of ``read/`` (None: root or screens)
    classes: tuple           # page classes the family can have
    wide: bool               # judgement legend or several families on one page: the 1,300 B envelope bucket (``CARD``: the screen bucket)
    title_zh: str
    title_en: str


def _title(lang: str, key: str) -> str:
    return T.tr(lang, "read.family." + key)


_FAMILY_ROWS = (
    ("START", None, ("card",), False), ("G", None, ("card",), False), ("A", None, ("page",), True), ("P", None, ("page",), True),
    ("INDEX", None, ("card",), False), ("S", "2D", ("page",), False), ("D", "2D", ("page",), False), ("M", "2D", ("page",), False),
    ("W", "3D", ("card", "page"), False), ("R", "1D", ("page",), False), ("T", "1D", ("page",), False), ("E", "1D", ("page",), False),
    ("C", "4D", ("page",), True), ("V", "5D", ("page",), True), ("Q", "QA", ("card", "page"), True),
    ("SCREENS", None, ("card",), True), ("NAV", None, ("card",), True), ("CARD", None, ("page",), True),
)
#: the family registry; the titles come from ``derive/i18n/<lang>.json`` (``read.family.<key>``), none is written here
FAMILIES: dict = {key: Family(key, dim, classes, wide, _title("zh-CN", key), _title("en-US", key)) for key, dim, classes, wide in _FAMILY_ROWS}
PAGE_CLASSES = ("card", "page")
LANGS = ("zh-CN", "en-US", "ja-JP")


def text_lang(lang: str) -> str:
    """The language of the fixed texts: ``ja-JP`` uses the ``en-US`` texts (D-24); anything unknown is an error."""
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}")
    return "zh-CN" if lang == "zh-CN" else "en-US"


def parse_family_class(value) -> tuple:
    """``("S", "page")`` from ``"S:page"`` or ``("S", "page")``; a bare family key is accepted when the family has a single class.

    Unknown families, unknown classes and classes the family cannot have raise ``ValueError``.
    """
    if isinstance(value, str):
        family, _, cls = value.partition(":")
    elif isinstance(value, (tuple, list)) and len(value) == 2 and all(isinstance(x, str) for x in value):
        family, cls = value
    else:
        raise ValueError(f"family class must be 'FAMILY:class' or a (family, class) pair, got {value!r}")
    spec = FAMILIES.get(family)
    if spec is None:
        raise ValueError(f"unknown page family {family!r}; known: {', '.join(FAMILIES)}")
    if not cls:
        if len(spec.classes) != 1:
            raise ValueError(f"family {family!r} has the classes {spec.classes}; write {family}:card or {family}:page")
        cls = spec.classes[0]
    if cls not in spec.classes:
        raise ValueError(f"family {family!r} has no class {cls!r} (it has {spec.classes})")
    return family, cls


def family_class_key(value) -> str:
    family, cls = parse_family_class(value)
    return f"{family}:{cls}"


def family_title(family: str, lang: str = "zh-CN", dim: str | None = None) -> str:
    """The title text of a family (``INDEX`` takes its dimension, ``P`` its ``1D``/``2D``)."""
    spec = FAMILIES.get(family)
    if spec is None:
        raise ValueError(f"unknown page family {family!r}")
    tl = text_lang(lang)
    if family == "INDEX":
        return T.fill(T.tr(tl, "read.title.index"), dim=dim or "2D")
    if family == "P":
        return T.tr(tl, {"1D": "read.title.p1d", "3Q": "read.title.p3q"}.get(dim or "2D", "read.title.p2d"))
    return spec.title_zh if tl == "zh-CN" else spec.title_en


# ---------------------------------------------------------------------------------------------------- page ids

class PageId(NamedTuple):
    family: str              # START, SCREENS, NAV, CARD, INDEX, P or one of the family letters
    number: int | None       # the page number of a letter family (``S07`` -> 7); ``P1D``/``P2D`` -> None
    dim: str | None          # INDEX: its dimension; P: ``1D``/``2D``; letter families: the dimension directory
    part: int | None         # continuation number (``SCREENS-2``, ``NAV-2``, ``INDEX-2D-2``); never set for cards
    enc: str | None          # CARD: everything after ``CARD-`` (opaque, see the module docstring)


def is_page_id(text) -> bool:
    return isinstance(text, str) and PAGE_ID_RE.fullmatch(text) is not None


def page_id(family: str, number: int) -> str:
    """``S07`` from ``("S", 7)``; numbers above 99 use three digits, above 999 are an error (the grammar stops there)."""
    if family not in LETTER_FAMILIES or len(family) != 1:
        raise ValueError(f"{family!r} is not a letter family ({LETTER_FAMILIES})")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 999:
        raise ValueError(f"page number must be 1..999, got {number!r}")
    return f"{family}{number:02d}"


def card_page_id(enc_name: str, part: int = 1) -> str:
    """``CARD-<enc>`` for the first card of a screen, ``CARD-<enc>-2`` for the second part (the screen id is passed through ``enc`` first)."""
    if not enc_name or re.fullmatch("[" + ENC_CHARS + "]+", enc_name) is None:
        raise ValueError(f"{enc_name!r} is not an encoded name")
    if isinstance(part, bool) or not isinstance(part, int) or part < 1:
        raise ValueError("part must be 1 or more")
    return f"CARD-{enc_name}" + ("" if part == 1 else f"-{part}")


def page_ref(page: str) -> str:
    """``§S07`` from ``S07`` (the form every text uses); an id that is not a page id is an error."""
    if not is_page_id(page):
        raise ValueError(f"{page!r} is not a page id")
    return SECTION_SIGN + page


def parse_page_ref(text) -> str | None:
    """The bare page id of ``§S07`` or ``S07`` (spaces around are ignored), ``None`` when it is neither."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith(SECTION_SIGN):
        text = text[len(SECTION_SIGN):]
    return text if is_page_id(text) else None


def parse_page_id(text) -> PageId | None:
    """The parts of a bare page id, ``None`` for anything that is not one. ``CARD-<enc>`` keeps ``enc`` whole (see the module docstring)."""
    if not is_page_id(text):
        return None
    if text == "START":
        return PageId("START", None, None, None, None)
    if text.startswith("CARD-"):
        return PageId("CARD", None, None, None, text[5:])
    parts = _PARTS.match(text)
    if parts and parts.group("family"):
        return PageId(parts.group("family"), None, None, int(parts.group("part")) if parts.group("part") else None, None)
    if parts:
        return PageId("INDEX", None, parts.group("dim"), int(parts.group("ipart")) if parts.group("ipart") else None, None)
    if text in ("P1D", "P2D", "P3Q"):
        return PageId("P", None, text[1:], None, None)
    letter = text[0]
    return PageId(letter, int(text[1:]), FAMILIES[letter].dim, None, None)


# ---------------------------------------------------------------------------------------------------- paths

def page_path(page: str) -> str:
    """The Markdown path of a page relative to ``dist/`` (``read/2D/S07.md``); cards go through ``card_path``."""
    parsed = parse_page_id(page)
    if parsed is None:
        raise ValueError(f"{page!r} is not a page id")
    family = parsed.family
    if family == "START":
        return "START.md"
    if family == "CARD":
        raise ValueError("card paths depend on the part number: use card_path(enc, part)")
    if family in ("SCREENS", "NAV"):
        name = "INDEX" if family == "SCREENS" else "NAV"
        return f"screens/{name}" + (f"-{parsed.part}" if parsed.part else "") + ".md"
    if family == "INDEX":
        return f"read/{parsed.dim}/INDEX" + (f"-{parsed.part}" if parsed.part else "") + ".md"
    if family == "P":
        return f"read/{page}.md" if parsed.dim == "3Q" else f"read/{parsed.dim}/{page}.md"
    if parsed.dim is None:  # G and A sit in the root of read/
        return f"read/{page}.md"
    return f"read/{parsed.dim}/{page}.md"


def card_path(enc_name: str, part: int = 1) -> str:
    """``screens/<enc>.card.md`` (part 1) or ``screens/<enc>.card-2.md``."""
    card_page_id(enc_name, part)  # validates both
    return f"screens/{enc_name}.card" + ("" if part == 1 else f"-{part}") + ".md"


def twin_path(path: str) -> str | None:
    """The HTML twin of a Markdown file (D-2): ``site/`` plus the path without its extension plus ``.html``; the screen card
    ``screens/<enc>.card.md`` has the gallery page ``site/screens/<enc>.html`` as its twin. ``None`` for a file without a twin."""
    if not isinstance(path, str) or not path.endswith(".md"):
        return None
    if path in ("START.md", "screens/INDEX.md", "screens/NAV.md") or path.startswith("read/"):
        return "site/" + path[:-3] + ".html"
    parts = re.fullmatch(r"screens/(?P<enc>[^/]+?)\.card(?:-\d+)?\.md", path)
    if parts:
        return "site/screens/" + parts.group("enc") + ".html"
    shard = re.fullmatch(r"screens/(?:INDEX|NAV)-\d+\.md", path)
    if shard:
        return "site/" + path[:-3] + ".html"
    return None


# ---------------------------------------------------------------------------------------------------- enc

#: the shape of a hashed name: complete tokens, ``+h+``, the hash digits (12 and more when a tie lengthened it)
HASHED_RE = re.compile(r"^(?:[A-Za-z0-9._~-]|\+[0-9a-f]+\+)*\+h\+[0-9a-f]{12,64}$")


def is_hashed_name(name: str) -> bool:
    return isinstance(name, str) and HASHED_RE.fullmatch(name) is not None


def _tokens(identifier: str) -> list:
    """One token per input character: the character itself, ``~`` for ``#``, or a ``+hex+`` escape; the trailing ``.`` is escaped."""
    out = []
    for ch in identifier:
        if ch in ENC_KEEP:
            out.append(ch)
        elif ch == "#":
            out.append("~")
        else:
            out.append("+%x+" % ord(ch))
    if out and out[-1] == ".":
        out[-1] = "+2e+"
    return out


def _is_device(name: str) -> bool:
    return name.split(".", 1)[0].upper() in WINDOWS_DEVICES


def _full_tokens(identifier: str) -> list:
    """The tokens of the encoded name before the length rule: with the device prefix or the escaped leading ``_`` when needed."""
    if not isinstance(identifier, str):
        raise TypeError("an id is a string")
    if not identifier:
        raise ValueError("an empty id has no file name")
    tokens = _tokens(identifier)
    name = "".join(tokens)
    if _is_device(name):
        return ["_"] + tokens
    if name.startswith("_") and _is_device(name[1:]):
        return ["+5f+"] + tokens[1:]  # the literal ``_CON`` must not equal the prefixed form of ``CON``
    return tokens


def _digest(identifier: str, length: int) -> str:
    return hashlib.sha256(identifier.encode("utf-8", "surrogatepass")).hexdigest()[:length]


def _hashed(tokens: list, identifier: str, length: int = ENC_HASH_LEN) -> str:
    head, size = [], 0
    for token in tokens:
        if size + len(token) > ENC_HASH_PREFIX:
            break
        head.append(token)
        size += len(token)
    return "".join(head) + "+h+" + _digest(identifier, length)


def enc(id: str) -> str:  # noqa: A002  (the parameter is called ``id`` in the interface table of design 12.1)
    """The file-name form of one id (module docstring): injective, never recovers the id, long names end in ``+h+<12 hex>``."""
    tokens = _full_tokens(id)
    name = "".join(tokens)
    if len(name) > ENC_MAX:
        return _hashed(tokens, id)
    return name


def enc_many(identifiers: Iterable[str]) -> dict:
    """``{id: file name}`` for a set of ids: ``enc`` per id, then every member of a group of names equal ignoring case (NTFS) is written in
    the hashed form. The result has no two names equal ignoring case; a tie of the hash prefixes (astronomically unlikely) lengthens the hash."""
    ids = sorted(set(identifiers))
    names = {i: enc(i) for i in ids}
    tokens = {i: _full_tokens(i) for i in ids}
    length = ENC_HASH_LEN
    while length <= 64:
        groups: dict = defaultdict(list)
        for i, name in names.items():
            groups[name.lower()].append(i)
        tied = [members for members in groups.values() if len(members) > 1]
        if not tied:
            return names
        for members in tied:
            for i in members:
                names[i] = _hashed(tokens[i], i, length)
        length += 4
    raise ValueError("file names still collide after the full hash")
