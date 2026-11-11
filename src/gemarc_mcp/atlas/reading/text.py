"""Access to the fixed texts of the reading surface (``derive/i18n/<lang>.json``, top-level object ``read``).

``tx(lang, "start.title")`` is the text of ``read.start.title`` in the text language of ``lang`` (ja-JP uses the en-US texts, D-24) with
the ``{name}`` placeholders filled in one pass (a value that contains braces is never scanned again). A missing key or a missing value is a
generator bug and raises ``KeyError`` (``test_i18n_read`` proves every key the code can ask for exists in every language).
"""
from __future__ import annotations

import re
from typing import Sequence

from ..derive import i18n as T
from . import names as N

_HOLE = re.compile(r"\{(\w+)\}")


def raw(lang: str, key: str):
    """The value (string, list or mapping) of ``read.<key>`` in the text language of ``lang``."""
    value = T.lookup(N.text_lang(lang), "read." + key)
    if value is None:
        raise KeyError(f"missing text read.{key}")
    return value


def fill(template: str, **values) -> str:
    def one(match):
        name = match.group(1)
        if name not in values:
            raise KeyError(f"missing value {name!r} for {template!r}")
        return str(values[name])

    return _HOLE.sub(one, template)


def tx(lang_: str, key: str, **values) -> str:
    value = raw(lang_, key)
    if not isinstance(value, str):
        raise KeyError(f"read.{key} is not a text")
    return fill(value, **values) if values or "{" in value else value


def tx_list(lang: str, key: str) -> list:
    value = raw(lang, key)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise KeyError(f"read.{key} is not a list")
    return [str(v) for v in value]


def pattern(lang_: str, key: str, **groups: str) -> str:
    """A regular expression from a text template: the literal text escaped, ``{name}`` replaced by the named group ``groups[name]``.

    The checks build every expression from the template of the page frame, so no check writes a fixed sentence of its own."""
    template = raw(lang_, key)
    out = re.escape(template)
    for name, expression in groups.items():
        out = out.replace(re.escape("{" + name + "}"), "(?P<" + name + ">" + expression + ")")
    return out
