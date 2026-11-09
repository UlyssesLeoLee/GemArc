"""The per-user settings (DD-010 sections 2 and 3).

One row per account in ``user_settings``. A user without a row has the defaults, so the table stays
empty until somebody actually changes something. The stored values are public information: a language, a
list of tool names and a render form. **No secret is ever written here** (DD-010 section 6).

* ``load`` never raises on a damaged row: a value outside the allowed set falls back to the default, so one
  bad row can never turn ``/mcp`` into a 500 (DD-010 section 7).
* ``save`` is strict: an unknown language, an unknown tool name or an unknown render form is refused with
  the offending field, and nothing is written.
* The tools are stored sorted and comma separated; the empty string means "no restriction".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .db import Database
from ..i18n import LANGUAGES, normalize as normalize_lang_text
from ..userctx import DEFAULT_RENDER_PROFILE, RENDER_PROFILES

#: A list of tool names cannot grow without bound; the server registers far fewer.
MAX_TOOLS = 64

SELECT_SQL = "SELECT lang, tools, render_profile, updated_at FROM user_settings WHERE user_id = ?"
UPSERT_SQL = (
    "INSERT INTO user_settings (user_id, lang, tools, render_profile, updated_at) VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT (user_id) DO UPDATE SET lang = ?, tools = ?, render_profile = ?, updated_at = ?"
)


class SettingsError(Exception):
    """A refused change. ``field`` names the offending field, ``reason`` is a slug (never a value)."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class UserSettings:
    """The settings of one account.

    ``lang`` is empty when the account never chose a language: an empty value is "no choice",
    not "Chinese". The reader's own language (the cookie, then the browser) decides in that case,
    exactly as it did before this design (DD-010 section 4.1).
    """

    lang: str = ""
    tools: frozenset[str] = frozenset()
    render_profile: str = ""
    updated_at: int = 0

    @property
    def restricted(self) -> bool:
        """Whether this account narrowed its tool list."""
        return bool(self.tools)

    def as_dict(self) -> dict:
        return {
            "lang": self.lang,
            "tools": sorted(self.tools),
            "render_profile": self.render_profile,
            "updated_at": self.updated_at,
        }


def default_settings() -> UserSettings:
    """The settings of an account that never chose anything."""
    return UserSettings()


def clean_lang(raw: object) -> str:
    """The one of the three languages, or the empty string for "follow the reader".

    ``ja`` and ``JA`` mean ``ja-JP``. An empty value is a choice of its own (DD-010 section 3): it
    means the account does not pin a language, so the reader's own language keeps deciding. Only a
    value that is neither empty nor one of the three languages is a refusal.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if text == "":
        return ""
    value = normalize_lang_text(text)
    if value is None:
        raise SettingsError("lang", "invalid")
    return value


def clean_render_profile(raw: object) -> str:
    """``''`` or one of the render forms; anything else is a refusal."""
    value = (raw if isinstance(raw, str) else "").strip()
    if value == "":
        return ""
    if value not in RENDER_PROFILES:
        raise SettingsError("render_profile", "invalid")
    return value


def clean_tools(raw: Iterable[object], available: Sequence[str]) -> "tuple[str, ...]":
    """The tool names this account keeps.

    Every name must be a tool the server really registers: an unknown name is a refusal, not something
    to drop silently. The result is sorted, so the stored row is stable.
    """
    known = set(available)
    names = set()
    for item in raw:
        if not isinstance(item, str):
            raise SettingsError("tools", "invalid")
        name = item.strip()
        if not name:
            continue
        if name not in known:
            raise SettingsError("tools", "unknown_tool")
        names.add(name)
    if len(names) > MAX_TOOLS:
        raise SettingsError("tools", "too_many")
    return tuple(sorted(names))


def encode_tools(names: Iterable[str]) -> str:
    """The stored form of a tool list."""
    return ",".join(sorted({str(n).strip() for n in names if str(n).strip()}))


def decode_tools(raw: object) -> "frozenset[str]":
    """The stored form read back. A damaged value yields an empty list (no restriction)."""
    if not isinstance(raw, str):
        return frozenset()
    parts = [part.strip() for part in raw.split(",")]
    return frozenset(part for part in parts if part)


class SettingsStore:
    """Reads and writes the settings of one account."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self, user_id: str) -> UserSettings:
        """The settings of the account. A missing row and a damaged row both give the defaults."""
        with self.db.transaction() as tx:
            row = tx.fetchone(SELECT_SQL, (user_id,))
        if row is None:
            return default_settings()
        lang = row.get("lang")
        if lang not in LANGUAGES:
            # A damaged value is no choice, not a language: the reader's own language decides.
            lang = ""
        profile = row.get("render_profile")
        if profile not in RENDER_PROFILES and profile != "":
            profile = ""
        try:
            updated = int(row.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        return UserSettings(lang=lang, tools=decode_tools(row.get("tools")),
                            render_profile=profile or "", updated_at=updated)

    def save(self, user_id: str, *, lang: str, tools: "tuple[str, ...]", render_profile: str,
             now: int) -> UserSettings:
        """Store the settings of the account. The values are validated by the caller (the clean_* helpers)."""
        with self.db.transaction() as tx:
            tx.execute(UPSERT_SQL, (user_id, lang, encode_tools(tools), render_profile, int(now),
                                    lang, encode_tools(tools), render_profile, int(now)))
        return UserSettings(lang=lang, tools=frozenset(tools), render_profile=render_profile, updated_at=int(now))
