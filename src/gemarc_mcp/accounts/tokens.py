"""Personal access tokens (PATs) for MCP clients (design 3.6, D42-D45).

Format (62 characters, parsed by FIXED OFFSETS because base64url secrets may contain ``_``)::

    gat_ k3x7q2mn _ <43 chars base64url of 32 random bytes> <6 chars checksum>
    0..3 4..11     12  13..55                                56..61

* The database stores only ``SHA-256("pat" NUL secret)`` (unkeyed, base64url) plus the public 8-character id; the
  plaintext token exists once, in the return value of ``create``.
* Verification: format + checksum (no database), lookup by public id, constant-time compare of the stored hash with
  the presented one, then revoked / expired / user status / optional policy re-check. Every failure is one opaque
  "invalid" for the caller; the reason slug is for the audit log only.
* ``create`` serialises on the user row (``UPDATE users SET status = status ...``, a portable row lock that also
  refuses disabled users), counts live tokens under that lock and inserts: the per-user cap cannot be exceeded by
  concurrent creators. ``write`` scope needs the operator switch AND ``users.can_write`` (D89).
* ``last_used_at`` is written at most once per ``last_used_throttle`` seconds, in the verifying transaction.
"""
from __future__ import annotations

import secrets
import zlib
from dataclasses import dataclass
from typing import Callable, Optional

from . import audit as audit_mod
from .crypto import Crypto, b64u, b64u_decode, consteq, sha256_domain
from .db import Database, Tx

PREFIX = "gat_"
TOKEN_LENGTH = 62
ID_LENGTH = 8
SECRET_LENGTH = 43
CHECKSUM_LENGTH = 6
BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_B64U_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
SCANNER_REGEX = r"gat_[a-z2-7]{8}_[A-Za-z0-9_-]{49}"
NAME_MAX = 40
LAST_USED_THROTTLE_S = 3600
CLEANUP_BATCH = 200
CLEANUP_AFTER_S = 30 * 86400
ID_ATTEMPTS = 3

LOCK_USER_SQL = "UPDATE users SET status = status WHERE id = ? AND status = 'active'"
USER_WRITE_SQL = "SELECT can_write FROM users WHERE id = ?"
COUNT_LIVE_SQL = "SELECT count(*) AS n FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?"
INSERT_SQL = (
    "INSERT INTO api_tokens (id, user_id, kind, name, scope, secret_hash, created_at, expires_at) "
    "VALUES (?, ?, 'pat', ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
)
VERIFY_SQL = (
    "SELECT t.id AS id, t.user_id AS user_id, t.scope AS scope, t.secret_hash AS secret_hash, "
    "t.expires_at AS expires_at, t.last_used_at AS last_used_at, t.revoked_at AS revoked_at, "
    "u.status AS status, u.can_write AS can_write, u.email_bidx AS email_bidx, u.domain_bidx AS domain_bidx, "
    "u.has_hd AS has_hd, u.bidx_kid AS bidx_kid FROM api_tokens t JOIN users u ON u.id = t.user_id "
    "WHERE t.id = ? AND t.kind = 'pat'"
)
TOUCH_SQL = "UPDATE api_tokens SET last_used_at = ? WHERE id = ?"
LIST_SQL = (
    "SELECT id, name, scope, created_at, expires_at, last_used_at, revoked_at FROM api_tokens "
    "WHERE user_id = ? ORDER BY created_at DESC, id"
)
REVOKE_ONE_SQL = "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL"
REVOKE_ALL_SQL = "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL"
CLEANUP_SQL = (
    "DELETE FROM api_tokens WHERE id IN (SELECT id FROM api_tokens WHERE (revoked_at IS NOT NULL AND revoked_at < ?) "
    "OR expires_at < ? LIMIT ?)"
)


class TokenError(Exception):
    """A request-level refusal; ``code`` is the user-facing GEMARC_ACCT code, ``reason`` a slug."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code} {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class TokenPolicy:
    default_days: int = 30
    max_days: int = 90
    max_per_user: int = 10
    last_used_throttle: int = LAST_USED_THROTTLE_S

    @classmethod
    def from_config(cls, config) -> "TokenPolicy":
        return cls(config.pat_default_days, config.pat_max_days, config.pat_max_per_user)


@dataclass(frozen=True)
class CreatedToken:
    token: str  # the ONLY time the plaintext exists
    id: str
    scope: str
    expires_at: int


@dataclass(frozen=True)
class TokenMeta:
    id: str
    name: str
    scope: str
    created_at: int
    expires_at: int
    last_used_at: Optional[int]
    revoked_at: Optional[int]

    def state(self, now: int) -> str:
        if self.revoked_at is not None:
            return "revoked"
        return "expired" if self.expires_at <= now else "active"


@dataclass(frozen=True)
class Verification:
    ok: bool
    reason: str  # "ok" or format | unknown | mismatch | revoked | expired | disabled | policy (audit only)
    token_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: Optional[str] = None  # EFFECTIVE scope: min(token scope, users.can_write)
    expires_at: Optional[int] = None


def checksum(first56: str) -> str:
    """base64url of the big-endian CRC32 of the first 56 characters: catches typos, is NOT a security control."""
    return b64u(zlib.crc32(first56.encode("ascii")).to_bytes(4, "big"))


def mint(public_id: Optional[str] = None) -> "tuple[str, str, str]":
    """Return ``(token, public_id, secret)``."""
    pid = public_id or "".join(BASE32_ALPHABET[b & 31] for b in secrets.token_bytes(ID_LENGTH))
    secret = b64u(secrets.token_bytes(32))
    head = PREFIX + pid + "_" + secret
    return head + checksum(head), pid, secret


def parse(token: object) -> Optional["tuple[str, str]"]:
    """Fixed-offset parse; returns ``(public_id, secret)`` or None. Never touches the database."""
    if not isinstance(token, str) or len(token) != TOKEN_LENGTH or not token.isascii():
        return None
    if token[:4] != PREFIX or token[12] != "_":
        return None
    pid, secret, tail = token[4:12], token[13:56], token[56:]
    if any(c not in BASE32_ALPHABET for c in pid) or any(c not in _B64U_CHARS for c in secret):
        return None
    if not consteq(tail, checksum(token[:56])):
        return None
    try:
        if len(b64u_decode(secret)) != 32:
            return None
    except ValueError:
        return None
    return pid, secret


def hash_secret(secret: str) -> str:
    return sha256_domain("pat", secret)


def _clean_name(name: object) -> str:
    if not isinstance(name, str):
        raise TokenError("GEMARC_ACCT_034", "name")
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= NAME_MAX or any(ord(c) < 32 or ord(c) == 127 for c in cleaned):
        raise TokenError("GEMARC_ACCT_034", "name")
    return cleaned


class TokenStore:
    def __init__(self, db: Database, crypto: Crypto, policy: Optional[TokenPolicy] = None) -> None:
        self.db = db
        self.crypto = crypto  # tokens are hashed unkeyed (D110); kept for API symmetry
        self.policy = policy or TokenPolicy()

    def create(self, user_id: str, name: str, now: int, *, scope: str = "read", days: Optional[int] = None,
               allow_write: bool = False) -> CreatedToken:
        """Create a PAT. Raises ``TokenError``: 034 bad name/days/scope, 035 write not permitted, 033 cap reached,
        013 user missing or disabled."""
        p = self.policy
        cleaned = _clean_name(name)
        if scope not in ("read", "write"):
            raise TokenError("GEMARC_ACCT_034", "scope")
        lifetime = p.default_days if days is None else days
        if isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 1 <= lifetime <= p.max_days:
            raise TokenError("GEMARC_ACCT_034", "days")
        now = int(now)
        expires = now + lifetime * 86400
        with self.db.transaction() as tx:
            if tx.execute(LOCK_USER_SQL, (user_id,)) < 1:  # also serialises concurrent creators for this user
                raise TokenError("GEMARC_ACCT_013", "user_inactive")
            if scope == "write":
                row = tx.fetchone(USER_WRITE_SQL, (user_id,))
                if not allow_write or not row or not row["can_write"]:
                    raise TokenError("GEMARC_ACCT_035", "write_not_permitted")
            if tx.fetchone(COUNT_LIVE_SQL, (user_id, now))["n"] >= p.max_per_user:
                raise TokenError("GEMARC_ACCT_033", "cap")
            for _ in range(ID_ATTEMPTS):
                token, pid, secret = mint()
                if tx.execute(INSERT_SQL, (pid, user_id, cleaned, scope, hash_secret(secret), now, expires)) == 1:
                    audit_mod.record(tx, now, "token_created", user_id=user_id, ref=pid)
                    return CreatedToken(token, pid, scope, expires)
            raise TokenError("GEMARC_ACCT_034", "id_collision")

    def verify(self, token: object, now: int, *,
               policy_check: Optional[Callable[[dict], bool]] = None) -> Verification:
        """Validate a presented token. Database errors PROPAGATE (the caller answers 503, never 401 or allow)."""
        parsed = parse(token)
        if parsed is None:
            return Verification(False, "format")
        pid, secret = parsed
        digest = hash_secret(secret)
        now = int(now)
        with self.db.transaction() as tx:
            row = tx.fetchone(VERIFY_SQL, (pid,))
            if row is None:
                return Verification(False, "unknown")
            if not consteq(row["secret_hash"], digest):
                return Verification(False, "mismatch")
            if row["revoked_at"] is not None:
                return Verification(False, "revoked")
            if row["expires_at"] <= now:
                return Verification(False, "expired")
            if row["status"] != "active":
                return Verification(False, "disabled")
            if policy_check is not None and not policy_check(row):
                return Verification(False, "policy")
            last = row["last_used_at"]
            if last is None or now - last >= self.policy.last_used_throttle:
                tx.execute(TOUCH_SQL, (now, pid))
        effective = "write" if row["scope"] == "write" and row["can_write"] else "read"
        return Verification(True, "ok", pid, row["user_id"], effective, row["expires_at"])

    def list(self, user_id: str) -> "list[TokenMeta]":
        """Public id and metadata only: never a hash, never a secret."""
        with self.db.transaction() as tx:
            return [TokenMeta(r["id"], r["name"], r["scope"], r["created_at"], r["expires_at"], r["last_used_at"],
                              r["revoked_at"]) for r in tx.fetchall(LIST_SQL, (user_id,))]

    def revoke(self, user_id: str, public_id: str, now: int) -> bool:
        """Revoke one of the user's own tokens (another user's id behaves like an unknown one)."""
        with self.db.transaction() as tx:
            done = tx.execute(REVOKE_ONE_SQL, (int(now), public_id, user_id)) > 0
            if done:
                audit_mod.record(tx, now, "token_revoked", user_id=user_id, ref=public_id)
            return done

    def revoke_all(self, user_id: str, now: int) -> int:
        with self.db.transaction() as tx:
            count = max(tx.execute(REVOKE_ALL_SQL, (int(now), user_id)), 0)
            audit_mod.record(tx, now, "tokens_revoked_all", user_id=user_id)
            return count

    def cleanup(self, now: int, limit: int = CLEANUP_BATCH) -> int:
        """Delete up to ``limit`` tokens revoked or expired more than 30 days ago."""
        cutoff = int(now) - CLEANUP_AFTER_S
        with self.db.transaction() as tx:
            return max(tx.execute(CLEANUP_SQL, (cutoff, cutoff, int(limit))), 0)


def revoke_all_in(tx: Tx, user_id: str, now: int) -> int:
    return max(tx.execute(REVOKE_ALL_SQL, (int(now), user_id)), 0)
