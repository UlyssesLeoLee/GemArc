"""The identity and the settings of the caller, carried from the HTTP layer into the MCP layer (DD-010).

The accounts package authenticates a request and knows who the caller is; the MCP layer (the tools, the
tool list) lives in the core package and must not import the accounts package. This module is the seam:
a tiny, dependency-free carrier that both sides can use.

* The core package reads ``current()``. It is ``None`` when the request carries no authenticated identity
  (no login, or the shared environment token): the tool list and the descriptions then behave exactly as
  they did before this design.
* The accounts package fills it with ``activate()`` and releases it with ``reset()``.

The context is per request and never carries a secret: a user id, a scope and three public preferences.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from .i18n import LANGUAGES

#: The render forms a deployment can produce. The empty string means "no personal default".
RENDER_PROFILES: tuple[str, ...] = ("mvp", "static", "vite")
#: The render form used when the caller names none and the account has no personal default.
DEFAULT_RENDER_PROFILE = "static"


@dataclass(frozen=True)
class UserContext:
    """Who is calling and what this caller asked for.

    ``tools`` is an allow list: the empty set means "no restriction", so the default account sees exactly
    the tools the server registers. A name in the set is a tool the account wants; the list can only ever
    REMOVE tools, never add one the server does not register (DD-010 section 3).

    ``lang`` is empty when the account never chose a language: an empty value means "no choice", so the
    tool descriptions stay the English of the code (the canonical text) and the reader's own language
    decides on the pages.
    """

    user_id: str
    scope: str = "read"
    lang: str = ""
    tools: frozenset[str] = frozenset()
    render_profile: str = ""

    @property
    def has_identity(self) -> bool:
        return bool(self.user_id)

    def allows(self, name: str) -> bool:
        """Whether this caller's list keeps the tool. An empty list keeps everything."""
        return not self.tools or name in self.tools

    def render_form(self, requested: str = "") -> str:
        """The render form to use: the caller's own value, then the account's default, then the server's."""
        if requested:
            return requested
        return self.render_profile or DEFAULT_RENDER_PROFILE


_CURRENT: ContextVar[Optional[UserContext]] = ContextVar("gemarc_user_context", default=None)


def current() -> Optional[UserContext]:
    """The context of the request being served, or None when it carries no identity."""
    return _CURRENT.get()


def activate(ctx: UserContext) -> "object":
    """Make ``ctx`` the context of the running request. Returns the token ``reset`` needs."""
    return _CURRENT.set(ctx)


def reset(token: object) -> None:
    """Restore the previous context."""
    _CURRENT.reset(token)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_RENDER_PROFILE",
    "LANGUAGES",
    "RENDER_PROFILES",
    "UserContext",
    "activate",
    "current",
    "reset",
]
