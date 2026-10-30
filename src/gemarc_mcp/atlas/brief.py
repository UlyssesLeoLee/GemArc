"""``brief``: the work order the AI reads before it authors anything (design 4.1).

The prose lives in ``atlas/brief/<section>.<lang>.md`` (data, zh-CN and en-US). A few placeholders keep the text from drifting
away from the code that enforces it: ``{{headers}}`` (the section headers of the format, from ``format.rows.SECTIONS``),
``{{derived}}`` (the keys the format refuses, from ``rows.DERIVED``) and ``{{ids:<group>}}`` (vocabulary ids of the profile).
``all`` is a compact work order of at most 9,000 bytes; every other section is at most 3,000 bytes (UTF-8 bytes of the
rendered text; the tests enforce both for both languages). Standard library only.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import vocab
from .format import rows as rw

BRIEF_DIR = Path(__file__).resolve().parent / "brief"
SECTIONS = ("format", "vocab", "derived", "slices", "oracle", "docs", "economics")
LANGUAGES = ("zh-CN", "en-US")
ALL_CAP = 9000
SECTION_CAP = 3000
_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z]+)(?::([A-Za-z]+))?\}\}")


class BriefError(ValueError):
    """An unknown section or language; the message lists the valid ones."""


def _headers() -> str:
    lines = []
    for spec in rw.SECTIONS.values():
        options = " ".join(spec.option_names)
        lines.append(f"{spec.header}" + (f"   opts: {options}" if options else ""))
    return "\n".join(lines)


def _derived() -> str:
    return "\n".join(f"- {name}: {' '.join(sorted(keys))}" for name, keys in rw.DERIVED.items() if keys)


def _fill(match: "re.Match") -> str:
    name, argument = match.group(1), match.group(2)
    if name == "headers":
        return _headers()
    if name == "derived":
        return _derived()
    if name == "ids" and argument:
        return " ".join(vocab.default_profile().ids(argument))
    raise BriefError(f"unknown placeholder {match.group(0)}")


def resolve_language(lang: str) -> str:
    """The language of the text: ``zh-CN`` or ``en-US`` (every other value, ja-JP included, falls back to en-US)."""
    return lang if lang in LANGUAGES else "en-US"


def render(section: str = "", lang: str = "en-US") -> str:
    """The text of ``section`` (``""`` or ``all`` is the whole work order) in ``lang``."""
    name = (section or "all").strip().lower()
    if name != "all" and name not in SECTIONS:
        raise BriefError("section is one of: " + ", ".join(("all",) + SECTIONS))
    path = BRIEF_DIR / f"{name}.{resolve_language(lang)}.md"
    text = path.read_bytes().decode("utf-8")
    return _PLACEHOLDER.sub(_fill, text).replace("\r\n", "\n").strip("\n") + "\n"


def cap_of(section: str) -> int:
    return ALL_CAP if (section or "all") == "all" else SECTION_CAP
