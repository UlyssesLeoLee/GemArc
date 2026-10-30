"""``POST /mcp`` authentication in the account modes (design 3.6, D8-D14, D46-D50, D91, D97).

Modes (see ``flags.py`` for how the effective mode is chosen): ``none``, ``env-token`` (both handled by
``api/index.py`` exactly as before, without the database), ``pat`` and ``pat-or-env`` (handled here).

* ``pat-or-env`` compares the environment token first (constant time), then falls back to the PAT.
* A PAT is checked for format + checksum before any cache or database access. A small in-process positive cache
  (default TTL 30 s, at most 512 entries, key = public id + digest, never the token) and a negative cache (15 s) keep
  the database quiet; the revoke / delete / logout-all routes evict from the positive cache at once, so revocation is
  immediate on the handling instance and bounded by the TTL elsewhere.
* Concurrent cache misses for the same token share ONE database query (single flight).
* A database failure answers 503 (GEMARC_ACCT_002/003/004), never 401 and never "allow".
* Failures answer 401 with a body byte-identical to the legacy bearer-token failure, whatever the reason.
* Only when the toolset has write tools and the token's effective scope is ``read`` is the request body buffered and
  checked against an ALLOW-LIST of read methods and read tools; the exact buffered bytes are forwarded.
* Nothing here logs or echoes a token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from starlette.responses import JSONResponse, Response

from . import tokens as tokens_mod
from .db import DBError
from .security import Busy

log = logging.getLogger("gemarc.accounts")

POSITIVE_MAX = 512
NEGATIVE_MAX = 1024
FAIL_LIMIT = 30
FAIL_WINDOW_S = 300
MAX_BODY_BYTES = 4 << 20
READ_METHODS = frozenset({
    "initialize", "ping", "tools/list", "resources/list", "resources/templates/list", "resources/read",
    "prompts/list", "prompts/get",
})


def unauthorized_response() -> Response:
    """Byte-identical to the legacy bearer failure of ``api/index.py``."""
    return JSONResponse({"error": "unauthorized"}, status_code=401,
                        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})


def unavailable_response(code: str, retry_after: Optional[int] = None) -> Response:
    headers = {"Cache-Control": "no-store"}
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse({"error": "accounts_unavailable", "code": code}, status_code=503, headers=headers)


def throttled_response(retry_after: int) -> Response:
    return JSONResponse({"error": "too_many_requests", "code": "GEMARC_ACCT_022"}, status_code=429,
                        headers={"Retry-After": str(max(int(retry_after), 1)), "Cache-Control": "no-store"})


def insufficient_scope_response() -> Response:
    return JSONResponse({"error": "insufficient_scope", "code": "GEMARC_ACCT_032"}, status_code=403,
                        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"', "Cache-Control": "no-store"})


# -- cache -----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CachedGrant:
    token_id: str
    user_id: str
    scope: str
    until: float


class PatCache:
    """Bounded LRU positive and negative caches. Keys are ``(public id, digest)``; the token is never stored."""

    def __init__(self, ttl: int, neg_ttl: int, clock: Callable[[], float] = time.time) -> None:
        self.ttl = ttl
        self.neg_ttl = neg_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._pos: "OrderedDict[tuple[str, str], CachedGrant]" = OrderedDict()
        self._neg: "OrderedDict[tuple[str, str], float]" = OrderedDict()

    def get(self, key: "tuple[str, str]") -> "tuple[str, Optional[CachedGrant]]":
        """``("pos", grant)``, ``("neg", None)`` or ``("miss", None)``."""
        now = self._clock()
        with self._lock:
            grant = self._pos.get(key)
            if grant is not None:
                if grant.until > now:
                    self._pos.move_to_end(key)
                    return "pos", grant
                del self._pos[key]
            until = self._neg.get(key)
            if until is not None:
                if until > now:
                    self._neg.move_to_end(key)
                    return "neg", None
                del self._neg[key]
        return "miss", None

    def put_positive(self, key: "tuple[str, str]", grant: CachedGrant) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._pos[key] = grant
            self._pos.move_to_end(key)
            while len(self._pos) > POSITIVE_MAX:
                self._pos.popitem(last=False)

    def put_negative(self, key: "tuple[str, str]") -> None:
        if self.neg_ttl <= 0:
            return
        with self._lock:
            self._neg[key] = self._clock() + self.neg_ttl
            self._neg.move_to_end(key)
            while len(self._neg) > NEGATIVE_MAX:
                self._neg.popitem(last=False)

    def evict_token(self, token_id: str) -> None:
        with self._lock:
            for key in [k for k in self._pos if k[0] == token_id]:
                del self._pos[key]

    def evict_user(self, user_id: str) -> None:
        with self._lock:
            for key in [k for k, g in self._pos.items() if g.user_id == user_id]:
                del self._pos[key]

    def clear(self) -> None:
        with self._lock:
            self._pos.clear()
            self._neg.clear()


# -- scope gate ------------------------------------------------------------------------------------------------------
def _loads_strict(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("bom")
    text = raw.decode("utf-8")  # strict: UnicodeDecodeError is a ValueError

    def hook(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key")
        return dict(pairs)

    def bad(_name):
        raise ValueError("non-finite")

    return json.loads(text, object_pairs_hook=hook, parse_constant=bad)


def _message_allowed(message: Any, read_tools: "frozenset[str]") -> bool:
    if not isinstance(message, dict):
        return False
    method = message.get("method")
    if not isinstance(method, str):
        return False
    if method.startswith("notifications/") or method in READ_METHODS:
        return True
    if method == "tools/call":
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        return isinstance(name, str) and name in read_tools
    return False


def read_scope_allows(body: bytes, read_tools: "frozenset[str]") -> bool:
    """Allow-list gate (D91): strict UTF-8 without BOM, duplicate keys refused, top level an object or a non-empty
    array of objects, every member allowed. Anything unparsable is refused."""
    try:
        parsed = _loads_strict(body)
    except (ValueError, RecursionError):
        return False
    if isinstance(parsed, list):
        return bool(parsed) and all(_message_allowed(m, read_tools) for m in parsed)
    return _message_allowed(parsed, read_tools)


def replay_receive(body: bytes, receive: Callable) -> Callable:
    """An ASGI ``receive`` that first delivers exactly ``body`` and then delegates (for disconnects)."""
    sent = False

    async def replayed():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replayed


async def buffer_body(receive: Callable, limit: int = MAX_BODY_BYTES) -> "Optional[bytes]":
    """The complete request body, or None when it exceeds ``limit`` or the client went away."""
    chunks, seen = [], 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        piece = message.get("body", b"")
        seen += len(piece)
        if seen > limit:
            return None
        chunks.append(piece)
        if not message.get("more_body"):
            return b"".join(chunks)


# -- authenticator ---------------------------------------------------------------------------------------------------
@dataclass
class Decision:
    allowed: bool
    response: Optional[Response] = None
    scope: Optional[str] = None  # "env" | "read" | "write" when allowed
    user_id: Optional[str] = None
    receive: Optional[Callable] = None  # replacement ``receive`` when the body was buffered for the scope gate
    body_consumed: bool = False  # the request body was fully read: answer without draining it again


class McpAuth:
    """Authenticates one ``/mcp`` request in ``pat`` or ``pat-or-env`` mode.

    ``runtime`` provides ``now()``, ``run_pat_db(fn, *args)``, ``tokens``, ``policy``, ``client_key(scope)``,
    ``audit_async(event, ...)`` and the configuration. ``env_match(presented)`` is the constant-time environment-token
    comparison of ``api/index.py``.
    """

    def __init__(self, runtime: Any, mode: str, env_match: Callable[[Optional[bytes]], bool], *,
                 registered_tools: "tuple[str, ...]" = (), write_tools: "tuple[str, ...]" = ()) -> None:
        if mode not in ("pat", "pat-or-env"):
            raise ValueError("mode")
        self.runtime = runtime
        self.mode = mode
        self._env_match = env_match
        self.write_tools = frozenset(write_tools)
        self.read_tools = frozenset(registered_tools) - self.write_tools
        self._inflight: "dict[tuple[int, str, str], asyncio.Future]" = {}
        self._penalty = threading.Lock()
        self._fails = runtime.memory

    async def authenticate(self, scope: dict, headers: Any, presented: Optional[bytes], receive: Callable) -> Decision:
        """Top-level catch-all: an unexpected failure is logged as an event id and a class name only and answers 503."""
        try:
            return await self._authenticate(scope, headers, presented, receive)
        except Exception as exc:
            log.error("pat_event id=%s class=%s", secrets.token_hex(4), type(exc).__name__)
            return Decision(False, unavailable_response("GEMARC_ACCT_002"))

    async def _authenticate(self, scope: dict, headers: Any, presented: Optional[bytes], receive: Callable) -> Decision:
        if self.mode == "pat-or-env" and self._env_match(presented):
            return Decision(True, scope="env")
        rt = self.runtime
        if rt.config.mcp_host_only and (headers.get("host") or "").lower() != rt.config.host.lower():
            return Decision(False, unauthorized_response())
        token = None
        if presented is not None and presented.isascii():
            token = presented.decode("ascii")
        parsed = tokens_mod.parse(token) if token is not None else None
        if parsed is None:  # no cache, no database
            self._note_failure(scope)
            return Decision(False, unauthorized_response())
        pid, secret = parsed
        key = (pid, tokens_mod.hash_secret(secret))
        state, grant = rt.pat_cache.get(key)
        if state == "neg":
            return Decision(False, unauthorized_response())
        if state == "pos" and grant is not None:
            return await self._admit(grant.user_id, grant.scope, scope, receive, pid)
        # cache miss: slow lane when this client is already failing a lot
        lane = False
        if self._failures(scope) >= FAIL_LIMIT:
            if not self._penalty.acquire(blocking=False):
                return Decision(False, throttled_response(5))
            lane = True
        try:
            try:
                verification = await self._single_flight(key, token)
            except Busy:
                return Decision(False, unavailable_response("GEMARC_ACCT_023", retry_after=1))
            except DBError as exc:
                return Decision(False, unavailable_response(exc.code))
        finally:
            if lane:
                self._penalty.release()
        if not verification.ok:
            rt.pat_cache.put_negative(key)
            await self._record_failure(scope)
            return Decision(False, unauthorized_response())
        now = rt.now()
        remaining = max(int(verification.expires_at or 0) - now, 0)
        ttl = min(rt.pat_cache.ttl, remaining)
        if ttl > 0:
            rt.pat_cache.put_positive(key, CachedGrant(verification.token_id or pid, verification.user_id or "",
                                                       verification.scope or "read", rt.clock() + ttl))
        return await self._admit(verification.user_id or "", verification.scope or "read", scope, receive, pid)

    # -- internals -----------------------------------------------------------------------------------------------
    async def _single_flight(self, key: "tuple[str, str]", token: str):
        loop = asyncio.get_running_loop()
        flight = (id(loop),) + key
        waiting = self._inflight.get(flight)
        if waiting is not None:
            kind, value = await waiting
            if kind == "err":
                raise value
            return value
        future = loop.create_future()
        self._inflight[flight] = future
        try:
            try:
                value = await self.runtime.run_pat_db(self._verify, token)
                outcome = ("ok", value)
            except BaseException as exc:  # shared with the waiters, then re-raised for the leader
                outcome = ("err", exc)
            future.set_result(outcome)
        finally:
            self._inflight.pop(flight, None)
        if outcome[0] == "err":
            raise outcome[1]
        return outcome[1]

    def _verify(self, token: str):
        rt = self.runtime
        return rt.tokens.verify(token, rt.now(), policy_check=rt.policy.row_ok)

    def _failures(self, scope: dict) -> int:
        return self._fails.count("pat_auth_fail", self.runtime.client_key(scope), FAIL_WINDOW_S)

    def _note_failure(self, scope: dict) -> int:
        return self._fails.hit("pat_auth_fail", self.runtime.client_key(scope), 10 ** 9, FAIL_WINDOW_S)[1]

    async def _record_failure(self, scope: dict) -> None:
        """Count in memory; only when the threshold is crossed does a (deduplicated) audit row reach the database."""
        count = self._note_failure(scope)
        if count > FAIL_LIMIT:
            try:
                await self.runtime.audit_async("token_auth_failed", scope=scope)
            except Exception:
                pass

    async def _admit(self, user_id: str, effective: str, scope: dict, receive: Callable, pid: str) -> Decision:
        if not self.write_tools or effective == "write":
            return Decision(True, scope=effective, user_id=user_id)
        body = await buffer_body(receive)
        if body is None:
            return Decision(False, JSONResponse({"error": "request_too_large", "code": "GEMARC_ACCT_050"},
                                                status_code=413, headers={"Cache-Control": "no-store"}),
                            body_consumed=True)
        if not read_scope_allows(body, self.read_tools):
            try:
                await self.runtime.audit_async("token_scope_denied", scope=scope, ref=pid, user_id=user_id)
            except Exception:
                pass
            return Decision(False, insufficient_scope_response(), body_consumed=True)
        return Decision(True, scope=effective, user_id=user_id, receive=replay_receive(body, receive))
