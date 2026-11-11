"""Tri-state flag parsing and the effective /mcp mode. Standard library only, no side effects.

This is the ONE place that interprets ``GEMARC_ACCOUNTS``, ``GEMARC_REQUIRE_LOGIN`` and ``GEMARC_MCP_AUTH``
(design 3.0, D86/D87). ``api/index.py`` and ``accounts/config.py`` must both go through :func:`evaluate`.
It is importable when accounts are disabled: it imports nothing outside the standard library.

Public functions: :func:`parse_bool`, :func:`parse_mcp_auth`, :func:`has_env_token`, :func:`evaluate`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

ACCOUNTS_ENV = "GEMARC_ACCOUNTS"
REQUIRE_LOGIN_ENV = "GEMARC_REQUIRE_LOGIN"
MCP_AUTH_ENV = "GEMARC_MCP_AUTH"
MCP_TOKEN_ENV = "GEMARC_MCP_TOKEN"

OFF = "off"
ON = "on"
BAD = "bad"

MODES = ("none", "env-token", "pat", "pat-or-env")


def parse_bool(raw: Optional[str]) -> str:
    """Tri-state boolean. After ``strip()``: unset/""/"0" -> off, exactly "1" -> on, anything else -> bad.

    A bad value is never treated as "unset": callers must fail closed on it.
    """
    if raw is None:
        return OFF
    value = raw.strip()
    if value in ("", "0"):
        return OFF
    if value == "1":
        return ON
    return BAD


def parse_mcp_auth(raw: Optional[str]) -> Optional[str]:
    """``GEMARC_MCP_AUTH``: None when unset/empty, a mode name (case-sensitive allow-list) or ``"bad"``."""
    if raw is None:
        return None
    value = raw.strip()
    if value == "":
        return None
    if value in MODES:
        return value
    return BAD


def has_env_token(raw: Optional[str]) -> bool:
    """Same rule as ``api/index.py``'s ``parse_tokens``: a comma list with at least one non-blank entry."""
    return any(part.strip() for part in (raw or "").split(","))


#: Names used by ``config.py`` (same functions, kept under both spellings).
parse_flag = parse_bool
MCP_AUTH_VALUES = MODES


def clean(raw: Optional[str]) -> str:
    return (raw or "").strip()


@dataclass(frozen=True)
class Flags:
    """Result of :func:`evaluate`. ``mode`` is one of MODES or ``"bad"`` (bad value or conflict)."""

    accounts: str  # off | on | bad
    require_login: str  # off | on | bad
    mcp_auth: Optional[str]  # None | one of MODES | bad
    env_token: bool
    mode: str
    conflict: bool
    login_intent: bool

    @property
    def accounts_present(self) -> bool:
        """True when accounts is on or bad (the config module must then be consulted)."""
        return self.accounts != OFF

    @property
    def uses_pat(self) -> bool:
        return self.mode in ("pat", "pat-or-env")


def evaluate(env: Mapping[str, str]) -> Flags:
    """Evaluate the three switches plus the env token into the effective mode and the login intent."""
    accounts = parse_bool(env.get(ACCOUNTS_ENV))
    require = parse_bool(env.get(REQUIRE_LOGIN_ENV))
    auth = parse_mcp_auth(env.get(MCP_AUTH_ENV))
    token = has_env_token(env.get(MCP_TOKEN_ENV))

    conflict = False
    if require == BAD or auth == BAD:
        mode = BAD
    elif require == ON:
        if auth in ("none", "env-token"):
            mode, conflict = BAD, True
        elif auth is not None:
            mode = auth
        else:
            mode = "pat-or-env" if token else "pat"
    elif auth == "none" and token:
        mode, conflict = BAD, True
    elif auth == "env-token" and not token:
        mode, conflict = BAD, True
    elif auth is not None:
        mode = auth
    else:
        mode = "env-token" if token else "none"

    intent = require != OFF or auth == BAD or conflict or mode in ("pat", "pat-or-env")
    return Flags(accounts, require, auth, token, mode, conflict, intent)


def evaluate_mcp_mode(env: Mapping[str, str]) -> "tuple[Optional[str], bool]":
    """``(effective mode, conflict_or_bad)``; ``(None, True)`` means bad or conflicting input (fail closed)."""
    flags = evaluate(env)
    if flags.mode == BAD:
        return None, True
    return flags.mode, False
