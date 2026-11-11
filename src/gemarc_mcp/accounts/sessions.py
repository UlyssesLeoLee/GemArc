"""Browser sessions (design 3.5, D34-D36): opaque 256-bit ids, only an unkeyed domain-separated hash stored.

* The id exists only in the cookie; the table holds ``SHA-256("session" NUL sid)``. A cookie value that is not
  exactly 43 base64url characters is treated as "signed out" BEFORE any database access.
* Idle expiry and absolute expiry are enforced server side. ``last_seen_at`` is written at most every
  ``touch_every`` seconds (never more often than half the idle timeout, so a short idle limit cannot expire an
  active user early).
* Login rotates: the presented old session is revoked, at most ``max_per_user`` sessions are kept (oldest evicted).
* Lookup joins the user and requires ``status = 'active'``; an optional ``policy_check`` (the blind-index policy
  re-check of design D92) can veto without any extra query. The stored hash is compared in constant time.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable, Optional

from . import audit as audit_mod
from .crypto import Crypto, b64u, consteq, sha256_domain
from .db import Database, Tx

SID_RE = re.compile(r"^[A-Za-z0-9_-]{43}\Z")
MAX_PER_USER = 5
TOUCH_EVERY_S = 300
RECENT_LOGIN_S = 900
CLEANUP_BATCH = 200
EXPIRED_GRACE_S = 86400

LOCK_USER_SQL = "UPDATE users SET status = status WHERE id = ? AND status = 'active'"
DELETE_BY_HASH_SQL = "DELETE FROM sessions WHERE id_hash = ?"
DELETE_EXPIRED_OF_USER_SQL = "DELETE FROM sessions WHERE user_id = ? AND (expires_at <= ? OR idle_expires_at <= ?)"
INSERT_SQL = (
    "INSERT INTO sessions (id_hash, user_id, created_at, last_seen_at, idle_expires_at, expires_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
LIST_OF_USER_SQL = "SELECT id_hash FROM sessions WHERE user_id = ? ORDER BY created_at ASC, id_hash ASC"
LOOKUP_SQL = (
    "SELECT s.id_hash AS id_hash, s.user_id AS user_id, s.created_at AS created_at, s.last_seen_at AS last_seen_at, "
    "s.idle_expires_at AS idle_expires_at, s.expires_at AS expires_at, u.status AS status, u.can_write AS can_write, "
    "u.email_bidx AS email_bidx, u.domain_bidx AS domain_bidx, u.has_hd AS has_hd, u.bidx_kid AS bidx_kid "
    "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id_hash = ?"
)
TOUCH_SQL = "UPDATE sessions SET last_seen_at = ?, idle_expires_at = ? WHERE id_hash = ?"
REVOKE_ALL_SQL = "DELETE FROM sessions WHERE user_id = ?"
CLEANUP_SQL = (
    "DELETE FROM sessions WHERE id_hash IN (SELECT id_hash FROM sessions WHERE expires_at < ? LIMIT ?)"
)


class SessionError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SessionPolicy:
    idle_seconds: int = 30 * 60
    absolute_seconds: int = 8 * 3600
    max_per_user: int = MAX_PER_USER
    touch_every: int = TOUCH_EVERY_S
    recent_login_seconds: int = RECENT_LOGIN_S

    @classmethod
    def from_config(cls, config) -> "SessionPolicy":
        return cls(idle_seconds=config.session_idle_min * 60, absolute_seconds=config.session_max_hours * 3600)


@dataclass(frozen=True)
class SessionInfo:
    user_id: str
    created_at: int
    expires_at: int
    can_write: bool
    id_hash: str


@dataclass(frozen=True)
class Created:
    sid: str
    evicted: int
    replaced: bool


def new_sid() -> str:
    return b64u(secrets.token_bytes(32))


def valid_sid_format(value: object) -> bool:
    return isinstance(value, str) and SID_RE.match(value) is not None


def hash_sid(sid: str) -> str:
    return sha256_domain("session", sid)


class SessionStore:
    def __init__(self, db: Database, crypto: Crypto, policy: Optional[SessionPolicy] = None) -> None:
        self.db = db
        self.crypto = crypto  # kept for symmetry with the other stores; session hashes are unkeyed by design
        self.policy = policy or SessionPolicy()

    def create(self, user_id: str, now: int, *, replace_sid: Optional[str] = None) -> Created:
        """Create a session for an ACTIVE user, revoking ``replace_sid`` (rotation) and evicting the oldest ones
        beyond ``max_per_user``. Raises ``SessionError('user_inactive')`` for a missing or disabled user."""
        now = int(now)
        p = self.policy
        sid = new_sid()
        with self.db.transaction() as tx:
            if tx.execute(LOCK_USER_SQL, (user_id,)) < 1:
                raise SessionError("user_inactive")
            replaced = False
            if valid_sid_format(replace_sid):
                replaced = tx.execute(DELETE_BY_HASH_SQL, (hash_sid(replace_sid),)) > 0
            tx.execute(DELETE_EXPIRED_OF_USER_SQL, (user_id, now, now))
            expires = now + p.absolute_seconds
            tx.execute(INSERT_SQL, (hash_sid(sid), user_id, now, now, min(now + p.idle_seconds, expires), expires))
            evicted = 0
            fresh = hash_sid(sid)
            held = [r["id_hash"] for r in tx.fetchall(LIST_OF_USER_SQL, (user_id,)) if r["id_hash"] != fresh]
            for old in held[: max(len(held) + 1 - p.max_per_user, 0)]:  # never evict the session just created
                evicted += max(tx.execute(DELETE_BY_HASH_SQL, (old,)), 0)
            if evicted:
                audit_mod.record(tx, now, "session_evicted", user_id=user_id)
        return Created(sid, evicted, replaced)

    def lookup(self, sid: object, now: int, *,
               policy_check: Optional[Callable[[dict], bool]] = None) -> Optional[SessionInfo]:
        """Return the live session or None. No database access at all for a malformed or missing id."""
        if not valid_sid_format(sid):
            return None
        now = int(now)
        p = self.policy
        digest = hash_sid(sid)  # type: ignore[arg-type]
        with self.db.transaction() as tx:
            row = tx.fetchone(LOOKUP_SQL, (digest,))
            if row is None or not consteq(row["id_hash"], digest):
                return None
            if row["status"] != "active" or now >= row["expires_at"] or now >= row["idle_expires_at"]:
                return None
            if policy_check is not None and not policy_check(row):
                return None
            if now - row["last_seen_at"] >= min(p.touch_every, max(p.idle_seconds // 2, 1)):
                tx.execute(TOUCH_SQL, (now, min(now + p.idle_seconds, row["expires_at"]), digest))
        return SessionInfo(row["user_id"], row["created_at"], row["expires_at"], bool(row["can_write"]), digest)

    def is_recent(self, info: SessionInfo, now: int) -> bool:
        """Step-up check: the session was created (the user signed in) at most ``recent_login_seconds`` ago."""
        return 0 <= int(now) - info.created_at <= self.policy.recent_login_seconds

    def revoke(self, sid: object) -> bool:
        if not valid_sid_format(sid):
            return False
        with self.db.transaction() as tx:
            return tx.execute(DELETE_BY_HASH_SQL, (hash_sid(sid),)) > 0  # type: ignore[arg-type]

    def revoke_all(self, user_id: str, now: int = 0) -> int:
        with self.db.transaction() as tx:
            count = max(tx.execute(REVOKE_ALL_SQL, (user_id,)), 0)
            if now:
                audit_mod.record(tx, now, "sessions_revoked_all", user_id=user_id)
            return count

    def cleanup(self, now: int, limit: int = CLEANUP_BATCH) -> int:
        """Delete up to ``limit`` sessions that expired more than a day ago."""
        with self.db.transaction() as tx:
            return max(tx.execute(CLEANUP_SQL, (int(now) - EXPIRED_GRACE_S, int(limit))), 0)


def revoke_all_in(tx: Tx, user_id: str) -> int:
    return max(tx.execute(REVOKE_ALL_SQL, (user_id,)), 0)
