"""Database-backed fixed-window rate limiter (design 3.7, D51).

``window_start = now - now % window``; one atomic upsert per hit
(``INSERT ... ON CONFLICT (key_hash, window_start) DO UPDATE SET count = rate_limits.count + 1 RETURNING count``).
The key is ``HMAC(ratelimit sub-key, scope NUL subject)``, so the table holds no plain IP or user id. A fixed
window can let through up to twice the limit around a boundary (known trade-off). The per-instance in-memory
pre-filter and the memory-only ``pat_auth_fail`` counter belong to the route layer, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .crypto import Crypto
from .db import Database, Tx

#: scope -> (limit, window seconds); design table 3.7.
LIMITS = {
    "login_start": (20, 600),
    "login_callback": (30, 600),
    "signup_global": (60, 3600),
    "signup_ip": (3, 3600),
    "pat_create": (10, 3600),
    "pat_auth_fail": (30, 300),
    "account_post": (60, 600),
    "export": (5, 86400),
    "delete_attempt": (5, 86400),
}
#: Scopes keyed by user id: their rows are deleted with the account.
USER_SCOPES = ("pat_create", "account_post", "export", "delete_attempt")
GLOBAL_SUBJECT = "global"
RETENTION_SECONDS = 2 * 86400
CLEANUP_BATCH = 200

HIT_SQL = (
    "INSERT INTO rate_limits (key_hash, window_start, count) VALUES (?, ?, 1) "
    "ON CONFLICT (key_hash, window_start) DO UPDATE SET count = rate_limits.count + 1 RETURNING count"
)
CLEANUP_SQL = (
    "DELETE FROM rate_limits WHERE (key_hash, window_start) IN "
    "(SELECT key_hash, window_start FROM rate_limits WHERE window_start < ? LIMIT ?)"
)
DELETE_KEY_SQL = "DELETE FROM rate_limits WHERE key_hash = ?"


@dataclass(frozen=True)
class RateResult:
    allowed: bool
    count: int
    limit: int
    retry_after: int  # seconds until the window ends (0 when allowed)


def hit_in(tx: Tx, crypto: Crypto, now: int, scope: str, subject: str, *, limit: Optional[int] = None,
           window: Optional[int] = None) -> RateResult:
    """Count one hit inside the caller's transaction. Unknown scopes need explicit ``limit`` and ``window``."""
    default = LIMITS.get(scope)
    if (limit is None or window is None) and default is None:
        raise ValueError("unknown rate-limit scope")
    max_hits = limit if limit is not None else default[0]
    span = window if window is not None else default[1]
    if max_hits < 0 or span < 1:
        raise ValueError("bad limit or window")
    now = int(now)
    start = now - now % span
    count = tx.fetchone(HIT_SQL, (crypto.ratelimit_key(scope, subject), start))["count"]
    allowed = count <= max_hits
    return RateResult(allowed, count, max_hits, 0 if allowed else start + span - now)


class RateLimiter:
    def __init__(self, db: Database, crypto: Crypto) -> None:
        self.db = db
        self.crypto = crypto

    def hit(self, now: int, scope: str, subject: str, *, limit: Optional[int] = None,
            window: Optional[int] = None) -> RateResult:
        with self.db.transaction() as tx:
            return hit_in(tx, self.crypto, now, scope, subject, limit=limit, window=window)

    def cleanup(self, now: int, limit: int = CLEANUP_BATCH) -> int:
        with self.db.transaction() as tx:
            return cleanup(tx, now, limit)


def cleanup(tx: Tx, now: int, limit: int = CLEANUP_BATCH) -> int:
    """Delete up to ``limit`` counters whose window ended more than two days ago."""
    return max(tx.execute(CLEANUP_SQL, (int(now) - RETENTION_SECONDS, int(limit))), 0)


def delete_user_keys(tx: Tx, crypto: Crypto, user_id: str) -> int:
    """Remove every per-user counter of ``user_id`` (all scopes, every ring key). Used by account deletion."""
    removed = 0
    for scope in USER_SCOPES:
        for key in crypto.ratelimit_keys_all(scope, user_id):
            removed += max(tx.execute(DELETE_KEY_SQL, (key,)), 0)
    return removed
