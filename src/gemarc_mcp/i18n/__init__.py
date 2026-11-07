"""The three-language tables (DD-009, the section "language handling").

One source of truth: ``<locale>.json`` next to this module. Three tables; the ``console``
and ``accounts`` sections have the same key set in all three. A key that cannot be found
returns the key itself and is recorded: a gap is a defect, never a silent mix of two
languages.

The ``tools`` section exists only in the two translation tables. The English text of a tool
is the description the code registers itself (the docstring), so it is never written twice;
:func:`tool_texts` puts the three languages together for the ``_meta`` of ``tools/list``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

#: The three languages, in the order the product presents them.
LANGUAGES: tuple[str, ...] = ("zh-CN", "ja-JP", "en-US")
#: The language a page falls back to when nothing else says which one to use.
DEFAULT_LANG = "zh-CN"
#: The languages that carry a translation of the tool descriptions. English is the code.
TRANSLATION_LANGS: tuple[str, ...] = ("zh-CN", "ja-JP")
#: The cookie that remembers the language the reader chose.
LANG_COOKIE = "gemarc_lang"

_DIR = Path(__file__).resolve().parent
_TABLES: dict[str, dict] = {}
_MISSING: set[str] = set()


def tables() -> dict[str, dict]:
    """Every table, read once."""
    if not _TABLES:
        for lang in LANGUAGES:
            path = _DIR / f"{lang}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _TABLES[lang] = data if isinstance(data, dict) else {}
    return _TABLES


def section(lang: str, name: str) -> dict:
    """One section of one table (``{}`` when it is not there)."""
    value = tables().get(lang, {}).get(name)
    return value if isinstance(value, dict) else {}


def missing_keys() -> list[str]:
    """The keys that were looked up and not found. A defect list, never a silent mix."""
    return sorted(_MISSING)


def normalize(value: Optional[str]) -> Optional[str]:
    """One of :data:`LANGUAGES`, or ``None``. ``ja``, ``ja-JP`` and ``JA`` all mean ``ja-JP``."""
    if not value:
        return None
    text = value.strip()
    for lang in LANGUAGES:
        if text.lower() == lang.lower():
            return lang
    base = text.split("-")[0].lower()
    for lang in LANGUAGES:
        if lang.split("-")[0].lower() == base:
            return lang
    return None


def parse_accept_language(header: Optional[str]) -> Optional[str]:
    """The first supported language of an ``Accept-Language`` header, in the order the client writes it."""
    if not header:
        return None
    for part in header.split(","):
        tag = part.split(";")[0].strip()
        if not tag:
            continue
        found = normalize(tag)
        if found:
            return found
    return None


def cookie_value(header: Optional[str], name: str) -> Optional[str]:
    """The value of one cookie of a ``Cookie`` header."""
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip() == name:
            return value.strip() or None
    return None


def resolve(*, cookie_header: Optional[str] = None, accept_language: Optional[str] = None,
            query: Optional[str] = None) -> str:
    """The language of a request.

    The order is the one of DD-009: the explicit choice (the ``?lang=`` parameter), then the
    cookie that remembers that choice, then the browser, then the default. The browser guess
    is not a choice and is never remembered.
    """
    explicit = normalize(query)
    if explicit:
        return explicit
    stored = normalize(cookie_value(cookie_header, LANG_COOKIE))
    if stored:
        return stored
    return parse_accept_language(accept_language) or DEFAULT_LANG


def text(lang: str, name: str, key: str, **fmt) -> str:
    """One string of one section in one language.

    A key that is not there returns the key itself and is recorded. ``{placeholders}`` are
    filled from the keyword arguments.
    """
    raw = section(lang, name).get(key)
    if not isinstance(raw, str) or not raw:
        _MISSING.add(f"{lang}/{name}/{key}")
        return key
    if not fmt:
        return raw
    try:
        return raw.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return raw


def tool_texts(name: str, english_title: str, english_description: str) -> Optional[dict[str, dict]]:
    """The three languages of one tool, or ``None`` when the tables do not translate it.

    A tool the tables do not translate (the Atlas tool set has its own texts) keeps the
    English of the code: a ``_meta`` would only repeat that English three times, and the
    list of the tools would grow for nothing.

    A translation that is missing falls back to the English of the code and is recorded:
    the tool description layer is allowed to fall back (the English is the canonical text),
    the interface text layer is not (it shows the key).
    """
    translated = {}
    for lang in TRANSLATION_LANGS:
        entry = section(lang, "tools").get(name)
        if isinstance(entry, dict) and entry:
            translated[lang] = entry
    if not translated:
        return None
    out: dict[str, dict] = {}
    for lang in TRANSLATION_LANGS:
        entry = translated.get(lang) or {}
        title = entry.get("title")
        description = entry.get("description")
        if not isinstance(title, str) or not title:
            title = english_title
            _MISSING.add(f"{lang}/tools/{name}.title")
        if not isinstance(description, str) or not description:
            description = english_description
            _MISSING.add(f"{lang}/tools/{name}.description")
        out[lang] = {"title": title, "description": description}
    out["en-US"] = {"title": english_title, "description": english_description}
    return out
