"""Skeleton text tables, explicit markers and text hygiene for generated documents, diagrams and prompts.

Everything a generated file says in prose lives in ``derive/i18n/<lang>.json`` (three languages, identical keys); this
module loads those tables and offers the few helpers every builder shares:

* ``tr`` / ``fill``: dotted-key lookup and ``{name}`` substitution that never raises and never prints a bare ``None``;
* markers: ``not_authored`` (a required fact is missing: release BLOCK), ``NA``, ``NONE_IN_SCOPE``, ``NONE_DECLARED``;
* hygiene: ``clean``, ``clip``, ``md_text``, ``md_cell``, ``code``, ``dg_link`` neutralise ``|``, newlines, backticks,
  fences, ``[``/``]`` and ``<``/``>`` so model text cannot forge a marker, a link or an instruction block;
* ``sanitize``: a copy of a model in which every lone UTF-16 surrogate is replaced by U+FFFD (such a character is a
  valid Python ``str`` and passes the contract, but it cannot be encoded to UTF-8, so one would fail every file write
  and every hash);
* ``issue``: the small diagnostic record (``rule, severity, path, hint`` plus optional ``got``/``expected``) used by
  every builder and by ``lint.py``.

Standard library only. No function here raises on bad input: a value of the wrong type becomes an empty string or a
marker so the caller can keep rendering and report one issue.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..vocab import freeze

LANGS = ("zh-CN", "ja-JP", "en-US")
PRIMARY_LANG = "zh-CN"
FALLBACK_LANG = "en-US"
I18N_DIR = Path(__file__).resolve().parent / "i18n"

LEVELS = ("REQ", "BD", "DD")
CHAPTER_COUNT = 12

SEVERITIES = ("BLOCK", "ERROR", "WARN", "INFO")

# Language-independent sentinels: one regex counts them in every language.
NA = "[N/A]"
NONE_IN_SCOPE = "[NONE-IN-SCOPE]"
NONE_DECLARED = "[NONE-DECLARED]"
NOT_AUTHORED_RE = re.compile(r"\[NOT-AUTHORED:([A-Za-z0-9._@#:-]{0,60})\]")
SENTINEL_RE = re.compile(r"\[(?:NOT-AUTHORED:[A-Za-z0-9._@#:-]{0,60}|N/A|NONE-IN-SCOPE|NONE-DECLARED)\]")
# Literal junk that must never reach a generated file (DC-L-001).
JUNK_RE = re.compile(r"\b(?:undefined|NaN|None)\b|\[object")

# A lone UTF-16 surrogate: legal in a Python str, impossible in UTF-8.
_SURROGATE = re.compile("[\ud800-\udfff]")
REPLACEMENT = "�"
SANITIZE_DEPTH = 80
#: Printed instead of a NaN or an infinity (JSON text must never carry ``NaN`` / ``Infinity``).
NON_FINITE = "[non-finite]"

_SLOT_BAD = re.compile(r"[^A-Za-z0-9._@#:-]")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ​-‏‪-‮⁠⁦-⁩﻿]")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MD_SPECIAL = re.compile(r"([\\`|\[\]~])")
_LINE_START = re.compile(r"^(\s*)([#>+\-]|\d+[.)])")
_DG_BAD = re.compile(r"[^A-Za-z0-9._-]")


# --------------------------------------------------------------------------------------------- tables

def normalize_lang(lang) -> str:
    """A supported language id; anything else falls back to the primary language."""
    return lang if isinstance(lang, str) and lang in LANGS else PRIMARY_LANG


@lru_cache(maxsize=None)
def _load(lang: str):
    path = I18N_DIR / f"{lang}.json"
    return freeze(json.loads(path.read_text(encoding="utf-8")))


def tables(lang) -> Mapping:
    """The whole table of ``lang`` (read-only). Unknown languages use the primary language."""
    return _load(normalize_lang(lang))


def skeleton(lang) -> Mapping:
    """The ``skeleton`` part (chapter titles, headers, empty-state text, fact note) of ``lang``."""
    return tables(lang)["skeleton"]


def _walk(root, key: str):
    node = root
    for part in key.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            return None
    return node


def lookup(lang, key: str):
    """Value at the dotted ``key`` (``skeleton.th.case``), falling back to en-US; ``None`` when absent in both."""
    value = _walk(tables(lang), key)
    if value is None and normalize_lang(lang) != FALLBACK_LANG:
        value = _walk(tables(FALLBACK_LANG), key)
    return value


def tr(lang, key: str) -> str:
    """Text of ``key`` as a string. A missing key yields a visible marker (and is caught by the key-coverage test)."""
    value = lookup(lang, key)
    if isinstance(value, str):
        return value
    return not_authored("i18n." + key)


def tr_list(lang, key: str) -> list:
    value = lookup(lang, key)
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else []


def scalar(value) -> str:
    """Text of a scalar. ``None`` and containers never print as ``None`` / Python reprs: containers use JSON.

    A lone surrogate becomes U+FFFD and a NaN / infinity (alone or inside a container) becomes ``NON_FINITE``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return _SURROGATE.sub(REPLACEMENT, value) if _SURROGATE.search(value) else value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else NON_FINITE
    try:
        text = json.dumps(_finite(value), ensure_ascii=False, sort_keys=True, default=_json_default, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return ""
    return _SURROGATE.sub(REPLACEMENT, text)


def _json_default(value):
    return ""


def _finite(value, depth: int = 0):
    """``value`` with every NaN / infinity replaced by the ``NON_FINITE`` marker (depth-limited)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else NON_FINITE
    if depth > SANITIZE_DEPTH:
        return ""
    if isinstance(value, Mapping):
        return {k: _finite(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v, depth + 1) for v in value]
    return value


def has_surrogate(value, depth: int = 0, seen=None) -> bool:
    """True when a string (or a dict key) anywhere inside ``value`` holds a lone surrogate; cycles and absurd depth end the walk."""
    if isinstance(value, str):
        return bool(_SURROGATE.search(value))
    if depth > SANITIZE_DEPTH or not isinstance(value, (Mapping, list, tuple)):
        return False
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, Mapping):
        return any(has_surrogate(k, depth + 1, seen) or has_surrogate(v, depth + 1, seen) for k, v in value.items())
    return any(has_surrogate(v, depth + 1, seen) for v in value)


def sanitize(value, depth: int = 0):
    """``value`` itself when it holds no lone surrogate, else a plain copy in which each one is U+FFFD.

    A model that passes the contract may still carry such a character in free text. Every derive builder reads the model
    through ``policy.Index``, which calls this once, so no builder has to care.
    """
    if depth == 0 and not has_surrogate(value):
        return value
    if isinstance(value, str):
        return _SURROGATE.sub(REPLACEMENT, value)
    if depth > SANITIZE_DEPTH:
        return value
    if isinstance(value, Mapping):
        return {sanitize(k, depth + 1): sanitize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in value]
    return value


class Raw(str):
    """Trusted text built by this package (a marker, a pre-escaped fragment): ``fill`` and the Mermaid labels keep it as is."""


def fill(template, esc: bool = False, **values) -> str:
    """Replace ``{name}`` with ``values[name]``; a missing or ``None`` value becomes a NOT-AUTHORED marker.

    With ``esc=True`` plain values are Markdown-escaped (``md_text``); ``Raw`` values (markers) are never touched.
    """
    if not isinstance(template, str):
        return ""

    def one(match):
        name = match.group(1)
        if name not in values or values[name] is None:
            return not_authored(name)
        value = values[name]
        if isinstance(value, Raw):
            return str(value)
        return md_text(value) if esc else scalar(value)

    return _PLACEHOLDER.sub(one, template)


def chapter_title(lang, level: str, number: int) -> str:
    return tr(lang, f"skeleton.chapters.{level}.{number}")


def chapter_heading(lang, level: str, number: int) -> str:
    """The exact heading line the document builders and the linter agree on."""
    return f"## {number}. {chapter_title(lang, level, number)}"


# --------------------------------------------------------------------------------------------- markers

def slot_name(*parts) -> str:
    """A marker slot: parts joined with ``@``, characters outside the marker alphabet replaced, 60 characters at most."""
    text = "@".join(scalar(p) for p in parts if p is not None and scalar(p) != "")
    text = _SLOT_BAD.sub("_", text) or "slot"
    return text[:60]


def not_authored(slot, lang=None) -> str:
    """The marker for a required fact that the model does not carry (blocks a release)."""
    marker = f"[NOT-AUTHORED:{slot_name(slot)}]"
    if lang is not None:
        note = lookup(lang, "skeleton.marker.not_authored")
        if isinstance(note, str) and note:
            return f"{marker} {note}"
    return marker


def na(lang=None) -> str:
    if lang is not None:
        note = lookup(lang, "skeleton.marker.na")
        if isinstance(note, str) and note:
            return f"{NA} {note}"
    return NA


def empty_line(lang, key: str, declared: bool = False) -> str:
    """A chapter-level explicit empty state: sentinel plus the localized sentence of ``skeleton.empty.<key>``."""
    sentence = lookup(lang, f"skeleton.empty.{key}")
    sentence = sentence if isinstance(sentence, str) else ""
    return f"{NONE_DECLARED if declared else NONE_IN_SCOPE} {sentence}".rstrip()


# --------------------------------------------------------------------------------------------- hygiene

def clean(value) -> str:
    """Text with control characters removed and every line break turned into one space."""
    text = scalar(value)
    if not text:
        return ""
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return _CONTROL.sub("", text).strip()


def clip(value, limit: int = 80) -> str:
    text = clean(value)
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def md_text(value) -> str:
    """Inline Markdown-safe text: structural characters escaped, ``<`` ``>`` as entities, line breaks flattened."""
    text = clean(value)
    if not text:
        return ""
    text = _MD_SPECIAL.sub(r"\\\1", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text)


def md_cell(value, slot=None, lang=None) -> str:
    """A table cell for a text field. With a ``slot``, a missing value, an empty text or a value of the wrong type (not a
    string) becomes the NOT-AUTHORED marker of the slot; without a slot the value is flattened as it is."""
    if slot is not None and (not isinstance(value, str) or not value.strip()):
        return not_authored(slot)
    return md_text(value)


def code(value) -> str:
    """An inline code span for an id: no backtick can break out of it, ``|`` is escaped for tables."""
    text = clean(value).replace("`", "'").replace("|", "\\|")
    return f"`{text}`" if text else ""


def md_table(headers: Sequence, rows: Iterable[Sequence]) -> list:
    """GFM table lines. Cells must already be escaped (``md_cell`` / ``code``); a ragged row is padded, never dropped."""
    head = [md_text(h) for h in headers]
    width = len(head)
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    for row in rows:
        cells = [scalar(c) for c in row][:width]
        cells += [""] * (width - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def dg_id(value) -> str:
    """A link-safe diagram unit id fragment."""
    text = _DG_BAD.sub("_", clean(value)) or "x"
    return text[:100]


def dg_link(title, unit_id) -> str:
    """``[title](dg:<unit id>)``; the id is restricted to a link-safe alphabet."""
    return f"[{md_text(title)}](dg:{dg_id(unit_id)})"


# --------------------------------------------------------------------------------------------- issues

def _got(value) -> str:
    text = clip(value, 80)
    return text


def issue(rule: str, severity: str, path, hint, got=None, expected=None) -> dict:
    """One diagnostic record. ``got`` is truncated to 80 characters and control-stripped (prompt-injection hygiene)."""
    record = {
        "rule": rule,
        "severity": severity if severity in SEVERITIES else "ERROR",
        "path": clip(path, 120),
        "hint": clip(hint, 220),
    }
    if got is not None:
        record["got"] = _got(got)
    if expected is not None:
        record["expected"] = _got(expected)
    return record
