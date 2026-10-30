"""Audit log writer (design 3.8, D53/D54/D108): event ids and non-PII references only.

Columns: ``id ts event user_id ip_hash ref code``. There is no field that could hold an email, a plain IP, a user
agent, a token secret or an authorisation code, and the writer validates the shape of ``user_id``, ``ip_hash``,
``ref`` and ``code`` so that such a value cannot be smuggled in by mistake (a ``ValueError`` is a programming error).

* ``login_denied`` is deduplicated per (ip hash, event) for 10 minutes and ``token_auth_failed`` for 5 minutes, so
  anonymous traffic cannot flood the table.
* ``token_scope_denied`` (one row per refused request of an authenticated token holder) is deduplicated per
  (user, token id) for 5 minutes and capped at 60 rows per user per hour, so a read-only token cannot grow the
  table without bound.
* Anonymous events share a global ceiling per hour (default 2000 rows); beyond it they are dropped and ONE
  ``audit_overflow`` row per hour is written instead.
* Retention is a bounded purge (``purge``). It runs from the opportunistic housekeeping after a successful login
  (``AccountsRuntime.housekeeping``) and from ``python -m gemarc_mcp.accounts gc``.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from .crypto import Crypto
from .db import Database, Tx

EVENTS = frozenset({
    "user_created", "login_ok", "login_denied", "email_changed", "email_recycled", "logout", "session_evicted",
    "sessions_revoked_all", "token_created", "token_revoked", "tokens_revoked_all", "token_auth_failed",
    "token_scope_denied", "export", "account_deleted", "signup_saturated", "audit_overflow", "key_rekeyed",
    "migration_applied", "user_disabled", "user_enabled", "user_purged", "user_reconciled", "write_granted",
    "write_revoked",
})
#: Events caused by anonymous traffic: deduplicated and subject to the global hourly ceiling.
ANONYMOUS_EVENTS = frozenset({"login_denied", "token_auth_failed"})
DEDUP_SECONDS = {"login_denied": 600, "token_auth_failed": 300}
#: Authenticated events a token holder can repeat at will (every refused request): deduplicated per (user, ref).
USER_DEDUP_SECONDS = {"token_scope_denied": 300}
#: ... and capped per user per hour (rows beyond it are dropped; the earlier rows already tell the story).
USER_HOURLY_CEILING = 60
HOURLY_CEILING = 2000
PURGE_BATCH = 200

_USER_RE = re.compile(r"^[0-9a-f]{32}\Z")
_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{43}\Z")
_REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}\Z")
_CODE_RE = re.compile(r"^[a-z0-9_]{1,32}\Z")

INSERT_SQL = "INSERT INTO audit_log (id, ts, event, user_id, ip_hash, ref, code) VALUES (?, ?, ?, ?, ?, ?, ?)"
DEDUP_IP_SQL = "SELECT 1 AS hit FROM audit_log WHERE event = ? AND ip_hash = ? AND ts > ? LIMIT 1"
DEDUP_NOIP_SQL = "SELECT 1 AS hit FROM audit_log WHERE event = ? AND ip_hash IS NULL AND ts > ? LIMIT 1"
DEDUP_USER_REF_SQL = "SELECT 1 AS hit FROM audit_log WHERE event = ? AND user_id = ? AND ref = ? AND ts > ? LIMIT 1"
DEDUP_USER_NOREF_SQL = "SELECT 1 AS hit FROM audit_log WHERE event = ? AND user_id = ? AND ref IS NULL AND ts > ? LIMIT 1"
COUNT_USER_EVENT_SQL = "SELECT count(*) AS n FROM audit_log WHERE event = ? AND user_id = ? AND ts >= ?"
COUNT_ANON_SQL = (
    "SELECT count(*) AS n FROM audit_log WHERE event IN ('login_denied', 'token_auth_failed') AND ts >= ?"
)
OVERFLOW_SQL = "SELECT 1 AS hit FROM audit_log WHERE event = 'audit_overflow' AND ts >= ? LIMIT 1"
PURGE_SQL = "DELETE FROM audit_log WHERE id IN (SELECT id FROM audit_log WHERE ts < ? LIMIT ?)"


def record(
    tx: Tx,
    now: int,
    event: str,
    *,
    user_id: Optional[str] = None,
    ip_hash: Optional[str] = None,
    ref: Optional[str] = None,
    code: Optional[str] = None,
    ceiling: int = HOURLY_CEILING,
) -> bool:
    """Write one event inside the caller's transaction. Returns False when it was deduplicated or dropped."""
    if event not in EVENTS:
        raise ValueError("unknown audit event")
    for value, pattern in ((user_id, _USER_RE), (ip_hash, _HASH_RE), (ref, _REF_RE), (code, _CODE_RE)):
        if value is not None and not pattern.match(value):
            raise ValueError("audit field has an invalid shape")
    now = int(now)
    if event in ANONYMOUS_EVENTS:
        since = now - DEDUP_SECONDS[event]
        hit = tx.fetchone(DEDUP_IP_SQL, (event, ip_hash, since)) if ip_hash else tx.fetchone(DEDUP_NOIP_SQL, (event, since))
        if hit:
            return False
        if tx.fetchone(COUNT_ANON_SQL, (now - 3600,))["n"] >= ceiling:
            if not tx.fetchone(OVERFLOW_SQL, (now - 3600,)):
                tx.execute(INSERT_SQL, (secrets.token_hex(16), now, "audit_overflow", None, None, None, None))
            return False
    elif event in USER_DEDUP_SECONDS and user_id:
        since = now - USER_DEDUP_SECONDS[event]
        seen = (tx.fetchone(DEDUP_USER_REF_SQL, (event, user_id, ref, since)) if ref
                else tx.fetchone(DEDUP_USER_NOREF_SQL, (event, user_id, since)))
        if seen or tx.fetchone(COUNT_USER_EVENT_SQL, (event, user_id, now - 3600))["n"] >= USER_HOURLY_CEILING:
            return False
    tx.execute(INSERT_SQL, (secrets.token_hex(16), now, event, user_id, ip_hash, ref, code))
    return True


def purge(tx: Tx, now: int, retention_days: int, limit: int = PURGE_BATCH) -> int:
    """Delete up to ``limit`` rows older than the retention period; returns how many."""
    return max(tx.execute(PURGE_SQL, (int(now) - int(retention_days) * 86400, int(limit))), 0)


class Audit:
    """Convenience wrapper for callers that are not already inside a transaction."""

    def __init__(self, db: Database, crypto: Crypto, retention_days: int = 30) -> None:
        self.db = db
        self.crypto = crypto
        self.retention_days = retention_days

    def ip_hash(self, ip: Optional[str], now: int) -> Optional[str]:
        """Keyed daily hash of ``ip`` (None for a missing or unparsable address)."""
        if not ip:
            return None
        try:
            return self.crypto.ip_hash(ip, now)
        except ValueError:
            return None

    def event(self, now: int, event: str, *, ip: Optional[str] = None, user_id: Optional[str] = None,
              ref: Optional[str] = None, code: Optional[str] = None) -> bool:
        with self.db.transaction() as tx:
            return record(tx, now, event, user_id=user_id, ip_hash=self.ip_hash(ip, now), ref=ref, code=code)

    def purge(self, now: int, limit: int = PURGE_BATCH) -> int:
        with self.db.transaction() as tx:
            return purge(tx, now, self.retention_days, limit)
