"""Request-level security helpers for the account routes and the /mcp PAT path (design 3.2, 3.7, D37, D98, D99).

Everything here is small and side-effect free except the two bounded in-process structures
(:class:`MemoryLimiter`, :class:`BoundedExecutor`). Nothing here logs, and nothing here reads the process environment:
callers pass the mapping they want consulted.

* ``owns(path)``            which paths belong to the account routes (exact ``/account`` or ``/account/...``, ``/auth/...``).
* ``trusted_ip``            the client address, taken ONLY from ``x-vercel-forwarded-for`` (when running on Vercel) or from
                            the N-th entry from the right of ``x-forwarded-for`` when the operator configured N hops.
* ``origin_ok``             CSRF origin rule: ``Origin`` equals the configured origin, else ``Sec-Fetch-Site: same-origin``.
* cookie / form / body helpers with strict parsing (a duplicated cookie name means "no cookie").
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
MAX_FORM_BYTES = 8192
B64U43_RE = re.compile(r"^[A-Za-z0-9_-]{43}\Z")


def owns(path: str) -> bool:
    """True for ``/account``, ``/account/...`` and ``/auth/...`` (never a bare prefix such as ``/accounting``)."""
    return path == "/account" or path.startswith("/account/") or path.startswith("/auth/")


# -- client address ------------------------------------------------------------------------------------------------
def _clean_ip(raw: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def trusted_ip(headers: Any, environ: Mapping[str, str], hops: Optional[int] = None) -> Optional[str]:
    """The client IP, or None when no header can be trusted (design D99).

    ``x-vercel-forwarded-for`` counts only when the ``VERCEL`` variable exists AND the header is present.
    ``x-forwarded-for`` counts only with an operator-set hop count N and then the N-th entry from the RIGHT.
    ``x-real-ip`` is never read.
    """
    if environ.get("VERCEL"):
        value = headers.get("x-vercel-forwarded-for")
        if value:
            return _clean_ip(value.split(",")[0])
    if hops:
        value = headers.get("x-forwarded-for")
        if value:
            parts = [p for p in (item.strip() for item in value.split(",")) if p]
            if len(parts) >= hops:
                return _clean_ip(parts[-hops])
    return None


# -- csrf ------------------------------------------------------------------------------------------------------------
def origin_ok(headers: Any, origin: str) -> bool:
    """Design D37: ``Origin`` must equal the configured origin; with no ``Origin`` header ``Sec-Fetch-Site`` must be
    ``same-origin``. Anything else (including ``Origin: null``) is refused."""
    presented = headers.get("origin")
    if presented is not None:
        return presented == origin
    return headers.get("sec-fetch-site") == "same-origin"


def fetch_metadata_ok(headers: Any) -> bool:
    """Navigation check for ``/auth/google/start`` (D106): when present, mode must be navigate and dest document."""
    mode = headers.get("sec-fetch-mode")
    dest = headers.get("sec-fetch-dest")
    return (mode is None or mode == "navigate") and (dest is None or dest == "document")


# -- cookies ---------------------------------------------------------------------------------------------------------
def cookie_values(headers: Any, name: str) -> "list[str]":
    found = []
    for line in headers.getlist("cookie"):
        for part in line.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == name:
                found.append(value.strip())
    return found


def single_cookie(headers: Any, name: str) -> Optional[str]:
    """The cookie value, or None when absent OR duplicated (a duplicated name is treated as signed out)."""
    values = cookie_values(headers, name)
    return values[0] if len(values) == 1 else None


def has_cookie(headers: Any, name: str) -> bool:
    return bool(cookie_values(headers, name))


def cookie_header(name: str, value: str, *, secure: bool, max_age: Optional[int] = None) -> str:
    """``Set-Cookie`` value: HttpOnly, SameSite=Lax, Path=/, no Domain; ``Secure`` on https bases."""
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if max_age is not None:
        parts.append(f"Max-Age={int(max_age)}")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(name: str, *, secure: bool) -> str:
    return cookie_header(name, "", secure=secure, max_age=0)


# -- request bodies ------------------------------------------------------------------------------------------------
class BodyTooLarge(Exception):
    pass


class BodyReader:
    """Reads a request body once, up to a limit, and remembers whether the whole body was consumed."""

    def __init__(self, receive: Callable) -> None:
        self._receive = receive
        self.complete = False
        self.disconnected = False

    async def read(self, limit: int) -> bytes:
        chunks = []
        seen = 0
        while not self.complete:
            message = await self._receive()
            if message["type"] == "http.disconnect":
                self.disconnected = True
                self.complete = True
                break
            piece = message.get("body", b"")
            seen += len(piece)
            if seen > limit:
                raise BodyTooLarge()
            chunks.append(piece)
            if not message.get("more_body"):
                self.complete = True
        return b"".join(chunks)


def parse_form(body: bytes) -> "Optional[dict[str, str]]":
    """Strict ``application/x-www-form-urlencoded``: UTF-8, at most 20 fields, every key single-valued."""
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False, max_num_fields=20,
                          errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    out = {}
    for key, values in parsed.items():
        if len(values) != 1:
            return None
        out[key] = values[0]
    return out


def parse_query(raw: bytes) -> "Optional[dict[str, str]]":
    """Query string to a single-valued dict; ``None`` for a malformed or repeated parameter."""
    try:
        parsed = parse_qs(raw.decode("ascii"), keep_blank_values=True, max_num_fields=20, errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    out = {}
    for key, values in parsed.items():
        if len(values) != 1:
            return None
        out[key] = values[0]
    return out


# -- in-process limiter ----------------------------------------------------------------------------------------------
class MemoryLimiter:
    """Fixed-window counters kept in this process only (design D96/D50). Bounded: at most ``max_keys`` entries."""

    def __init__(self, clock: Callable[[], float] = time.time, max_keys: int = 4096) -> None:
        self._clock = clock
        self._max = max_keys
        self._lock = threading.Lock()
        self._counts: "dict[tuple[str, str], tuple[int, int]]" = {}

    def _prune(self, now: int) -> None:
        if len(self._counts) < self._max:
            return
        live = {k: v for k, v in self._counts.items() if v[0] + 86400 > now}
        if len(live) >= self._max:  # still full: drop the oldest half
            keep = sorted(live.items(), key=lambda kv: kv[1][0])[len(live) // 2:]
            live = dict(keep)
        self._counts = live

    def hit(self, scope: str, key: str, limit: int, window: int) -> "tuple[bool, int, int]":
        """Count one hit. Returns ``(allowed, count, retry_after_seconds)``."""
        now = int(self._clock())
        start = now - now % window
        with self._lock:
            self._prune(now)
            old = self._counts.get((scope, key))
            count = old[1] + 1 if old and old[0] == start else 1
            self._counts[(scope, key)] = (start, count)
        allowed = count <= limit
        return allowed, count, 0 if allowed else start + window - now

    def count(self, scope: str, key: str, window: int) -> int:
        now = int(self._clock())
        start = now - now % window
        with self._lock:
            old = self._counts.get((scope, key))
            return old[1] if old and old[0] == start else 0


# -- bounded executor ------------------------------------------------------------------------------------------------
class Busy(Exception):
    """Every worker is busy and the wait queue is full (GEMARC_ACCT_023)."""


class BoundedExecutor:
    """A thread pool with a bounded wait queue: when full, work is refused at once instead of piling up (D98)."""

    def __init__(self, workers: int, queue: int, name: str = "gemarc-acct") -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._capacity = workers + queue
        self._pending = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        return self._pending

    async def run(self, fn: Callable, *args: Any) -> Any:
        with self._lock:
            if self._pending >= self._capacity:
                raise Busy()
            self._pending += 1
        try:
            return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)
        finally:
            with self._lock:
                self._pending -= 1
