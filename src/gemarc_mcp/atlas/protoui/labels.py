"""The fixed sentences of the screen builders (design A3 D-25): ``protoui/i18n/{zh-CN,en-US}.json``, ja-JP falls back to en-US (D-24).

``t(lang, key, **values)`` fills a sentence (``{name}`` placeholders, one pass, values are never scanned again); ``word`` and ``kind_label``
read the 55 vocabulary labels. Keys are dotted paths (``card.h.widgets``) into the JSON objects next to ``vocab``. A missing key is a
programming error and raises ``KeyError``: the tests check that every key the code uses exists in both files with the same placeholders.
Authored text (labels, notes, purposes, variant text) is never translated and never passes through here. Standard library only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent / "i18n"
LANGS = ("zh-CN", "en-US")
_CACHE: dict = {}
_HOLE = re.compile(r"\{(\w+)\}")


def text_lang(lang: str) -> str:
    """The language of the fixed sentences: zh-CN stays, every other language (ja-JP included) uses en-US."""
    return "zh-CN" if lang == "zh-CN" else "en-US"


def table(lang: str) -> dict:
    key = text_lang(lang)
    if key not in _CACHE:
        _CACHE[key] = json.loads((I18N_DIR / f"{key}.json").read_bytes().decode("utf-8"))
    return _CACHE[key]


def flatten(node, prefix: str = "") -> dict:
    """``{dotted key: sentence}`` of every string below ``node`` (the ``vocab`` and ``language`` entries excluded at the top level)."""
    out: dict = {}
    for key, value in node.items():
        if not prefix and key in ("vocab", "language"):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def raw(lang: str, key: str) -> str:
    node = table(lang)
    for part in key.split("."):
        node = node[part]
    if not isinstance(node, str):
        raise KeyError(key)
    return node


def t(lang: str, key: str, **values) -> str:
    return _HOLE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), raw(lang, key))


def holes(sentence: str) -> tuple:
    """The placeholder names of a sentence, sorted (the two languages must agree on them)."""
    return tuple(sorted(set(_HOLE.findall(sentence))))


def word(lang: str, group: str, item: str) -> str:
    """The label of a vocabulary id (``widgetKinds.button``); an id outside the vocabulary is shown as it is."""
    return table(lang)["vocab"].get(group, {}).get(item, item)


def kind_label(lang: str, kind: str) -> str:
    return word(lang, "widgetKinds", kind)
