"""User accounts: upsert by Google ``sub``, encrypted email, blind-index lookup, status, delete, export, rekey.

Storage rules (design 1.2, 4.3, 4.4, D61/D62):

* The account key is the Google ``sub`` (never the email). ``users.sub_bidx`` is the only UNIQUE lookup.
* ``sub`` and the lower-cased email are stored only as AES-256-GCM envelopes (``sub_enc``, ``email_enc``); the
  blind indexes ``sub_bidx`` / ``email_bidx`` / ``domain_bidx`` are keyed HMACs. There is NO name or password column.
* Lookups run one fixed query per ring key (``Crypto.blind_index_all``), never a dynamically built ``IN`` list.
* Everything one operation changes happens in one transaction, audit rows included.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import audit as audit_mod
from . import ratelimit
from .crypto import Crypto, CryptoError, email_domain, normalize_email
from .db import SIGNUP_LOCK_KEY, Database, Tx

REKEY_BATCH = 200

SELECT_BY_SUB_SQL = "SELECT id, status, email_enc, bidx_kid FROM users WHERE sub_bidx = ?"
INSERT_SQL = (
    "INSERT INTO users (id, sub_bidx, sub_enc, email_bidx, email_enc, domain_bidx, has_hd, can_write, bidx_kid, "
    "status, created_at, last_login_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 'active', ?, ?) "
    "ON CONFLICT (sub_bidx) DO NOTHING"
)
TOUCH_SQL = "UPDATE users SET last_login_at = ?, has_hd = ? WHERE id = ?"
REWRITE_SQL = (
    "UPDATE users SET sub_bidx = ?, sub_enc = ?, email_bidx = ?, email_enc = ?, domain_bidx = ?, has_hd = ?, "
    "bidx_kid = ?, last_login_at = ? WHERE id = ?"
)
RECYCLE_SELECT_SQL = "SELECT id FROM users WHERE email_bidx = ? AND status = 'active' AND id <> ?"
COUNT_SQL = "SELECT count(*) AS n FROM users"
DISABLE_SQL = "UPDATE users SET status = 'disabled' WHERE id = ?"
ENABLE_SQL = "UPDATE users SET status = 'active' WHERE id = ? AND status = 'disabled'"
KILL_SESSIONS_SQL = "DELETE FROM sessions WHERE user_id = ?"
REVOKE_TOKENS_SQL = "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL"
GET_SQL = (
    "SELECT id, status, can_write, has_hd, bidx_kid, email_bidx, domain_bidx, created_at, last_login_at "
    "FROM users WHERE id = ?"
)
EMAIL_SQL = "SELECT email_enc FROM users WHERE id = ?"
SET_WRITE_SQL = "UPDATE users SET can_write = ? WHERE id = ?"
DELETE_SQL = "DELETE FROM users WHERE id = ?"
ANON_AUDIT_SQL = "UPDATE audit_log SET ip_hash = NULL, ref = NULL WHERE user_id = ?"
EXPORT_SESSIONS_SQL = "SELECT created_at, last_seen_at, expires_at FROM sessions WHERE user_id = ? ORDER BY created_at"
EXPORT_TOKENS_SQL = (
    "SELECT id, name, scope, created_at, expires_at, last_used_at, revoked_at FROM api_tokens "
    "WHERE user_id = ? ORDER BY created_at, id"
)
EXPORT_AUDIT_SQL = "SELECT ts, event, code, ref FROM audit_log WHERE user_id = ? ORDER BY ts, id"
REKEY_SELECT_SQL = (
    "SELECT id, sub_enc, email_enc, bidx_kid FROM users WHERE bidx_kid <> ? AND id > ? ORDER BY id LIMIT ?"
)
REKEY_UPDATE_SQL = (
    "UPDATE users SET sub_bidx = ?, sub_enc = ?, email_bidx = ?, email_enc = ?, domain_bidx = ?, bidx_kid = ? "
    "WHERE id = ? AND bidx_kid = ?"
)
BY_EMAIL_SQL = "SELECT id FROM users WHERE email_bidx = ? ORDER BY id"
STATUS_COUNTS_SQL = "SELECT status, count(*) AS n FROM users GROUP BY status"
WRITERS_SQL = "SELECT count(*) AS n FROM users WHERE can_write = 1"
RECONCILE_SELECT_SQL = (
    "SELECT id, has_hd, domain_bidx, email_bidx FROM users WHERE status = 'active' AND id > ? ORDER BY id LIMIT ?"
)
DISTINCT_KIDS_SQL = "SELECT DISTINCT bidx_kid FROM users"
REFS_SQL = "SELECT bidx_kid, count(*) AS n FROM users GROUP BY bidx_kid"
ENC_REFS_SQL = "SELECT count(*) AS n FROM users WHERE sub_enc LIKE ? OR email_enc LIKE ?"
EXPORT_SCHEMA = 1


@dataclass(frozen=True)
class LoginResult:
    status: str  # "active" | "disabled" | "closed" (registration refused: user cap reached)
    user_id: Optional[str] = None
    created: bool = False
    email_changed: bool = False
    recycled: "tuple[str, ...]" = field(default_factory=tuple)  # ids of users disabled because their email moved


@dataclass(frozen=True)
class UserRecord:
    id: str
    status: str
    can_write: bool
    has_hd: bool
    bidx_kid: str
    email_bidx: str
    domain_bidx: str
    created_at: int
    last_login_at: Optional[int]


@dataclass(frozen=True)
class RekeyReport:
    pending: int
    rekeyed: int
    failed: int
    dry_run: bool


def disable_in(tx: Tx, user_id: str, now: int) -> bool:
    """Disable a user: sessions deleted and PATs revoked in the SAME transaction (D88)."""
    if tx.execute(DISABLE_SQL, (user_id,)) < 1:
        return False
    tx.execute(KILL_SESSIONS_SQL, (user_id,))
    tx.execute(REVOKE_TOKENS_SQL, (int(now), user_id))
    return True


def count_users(db: Database) -> "dict[str, int]":
    """Users per status plus ``can_write`` holders (no key ring needed: plain counts)."""
    out = {"active": 0, "disabled": 0}
    with db.transaction() as tx:
        for r in tx.fetchall(STATUS_COUNTS_SQL):
            out[r["status"]] = r["n"]
        out["can_write"] = tx.fetchone(WRITERS_SQL)["n"]
    return out


class UserAccounts:
    def __init__(self, db: Database, crypto: Crypto) -> None:
        self.db = db
        self.crypto = crypto

    # -- login / registration -----------------------------------------------------------------------------------
    def login(self, sub: str, email: str, has_hd: bool, now: int, *, max_users: int = 0,
              ip_hash: Optional[str] = None) -> LoginResult:
        """Register or refresh the account for a verified Google identity.

        ``active`` + ``created`` for a new user; ``disabled`` leaves the row untouched (no ``last_login_at``);
        ``closed`` when the user cap blocks a NEW registration. A new ``sub`` that carries an email held by an
        active row of another ``sub`` disables that row (sessions deleted, tokens revoked, audit
        ``email_recycled``); rows are never merged.
        """
        if not isinstance(sub, str) or not sub or len(sub) > 255:
            raise ValueError("sub")
        email_n = normalize_email(email)
        if email_n.count("@") != 1 or email_n.startswith("@") or email_n.endswith("@"):
            raise ValueError("email")
        domain = email_domain(email_n)
        hd = 1 if has_hd else 0
        now = int(now)
        crypto = self.crypto
        kid = crypto.ring.active_kid
        with self.db.transaction() as tx:
            row = None
            for candidate in crypto.blind_index_all("sub", sub):
                row = tx.fetchone(SELECT_BY_SUB_SQL, (candidate,))
                if row:
                    break
            if row is not None:
                return self._refresh(tx, row, sub, email_n, domain, hd, now, kid, ip_hash)
            if max_users > 0:
                # Serialise the cap check with the insert (Postgres READ COMMITTED would let concurrent first
                # logins at cap-1 all pass); the lock is released with the transaction. SQLite already serialises.
                tx.advisory_xact_lock(SIGNUP_LOCK_KEY)
                if tx.fetchone(COUNT_SQL)["n"] >= max_users:
                    return LoginResult("closed")
            user_id = crypto.new_row_id()
            inserted = tx.execute(INSERT_SQL, (
                user_id, crypto.blind_index("sub", sub), crypto.encrypt_field(sub, table="users", column="sub_enc", row_id=user_id),
                crypto.blind_index("email", email_n),
                crypto.encrypt_field(email_n, table="users", column="email_enc", row_id=user_id),
                crypto.blind_index("domain", domain), hd, kid, now, now,
            ))
            if inserted < 1:  # lost a race with a concurrent login of the same sub: treat it as an existing user
                for candidate in crypto.blind_index_all("sub", sub):
                    row = tx.fetchone(SELECT_BY_SUB_SQL, (candidate,))
                    if row:
                        return self._refresh(tx, row, sub, email_n, domain, hd, now, kid, ip_hash)
                raise ValueError("user vanished during login")
            recycled = self._recycle_email(tx, email_n, user_id, now, ip_hash)
            audit_mod.record(tx, now, "user_created", user_id=user_id, ip_hash=ip_hash)
            return LoginResult("active", user_id, created=True, recycled=recycled)

    def _recycle_email(self, tx: Tx, email_n: str, user_id: str, now: int, ip_hash: Optional[str]) -> "tuple[str, ...]":
        """Disable every OTHER active row that holds ``email_n`` (an address moved to another Google account).
        One rule for both paths (first registration and an e-mail change of an existing sub): an e-mail belongs
        to at most one active row. Rows are never merged."""
        recycled: "list[str]" = []
        for candidate in self.crypto.blind_index_all("email", email_n):
            for old in tx.fetchall(RECYCLE_SELECT_SQL, (candidate, user_id)):
                if old["id"] not in recycled and disable_in(tx, old["id"], now):
                    recycled.append(old["id"])
                    audit_mod.record(tx, now, "email_recycled", user_id=old["id"], ip_hash=ip_hash)
        return tuple(recycled)

    def _refresh(self, tx: Tx, row: dict, sub: str, email_n: str, domain: str, hd: int, now: int, kid: str,
                 ip_hash: Optional[str]) -> LoginResult:
        crypto = self.crypto
        user_id = row["id"]
        if row["status"] != "active":
            return LoginResult("disabled", user_id)
        previous = crypto.decrypt_field(row["email_enc"], table="users", column="email_enc", row_id=user_id)
        changed = previous != email_n
        if changed or row["bidx_kid"] != kid:
            tx.execute(REWRITE_SQL, (
                crypto.blind_index("sub", sub),
                crypto.encrypt_field(sub, table="users", column="sub_enc", row_id=user_id),
                crypto.blind_index("email", email_n),
                crypto.encrypt_field(email_n, table="users", column="email_enc", row_id=user_id),
                crypto.blind_index("domain", domain), hd, kid, now, user_id,
            ))
        else:
            tx.execute(TOUCH_SQL, (now, hd, user_id))
        recycled: "tuple[str, ...]" = ()
        if changed:
            audit_mod.record(tx, now, "email_changed", user_id=user_id, ip_hash=ip_hash)
            recycled = self._recycle_email(tx, email_n, user_id, now, ip_hash)
        return LoginResult("active", user_id, email_changed=changed, recycled=recycled)

    # -- reads --------------------------------------------------------------------------------------------------
    def get(self, user_id: str) -> Optional[UserRecord]:
        with self.db.transaction() as tx:
            row = tx.fetchone(GET_SQL, (user_id,))
        if row is None:
            return None
        return UserRecord(row["id"], row["status"], bool(row["can_write"]), bool(row["has_hd"]), row["bidx_kid"],
                          row["email_bidx"], row["domain_bidx"], row["created_at"], row["last_login_at"])

    def email_of(self, user_id: str) -> Optional[str]:
        with self.db.transaction() as tx:
            row = tx.fetchone(EMAIL_SQL, (user_id,))
            return None if row is None else self.crypto.decrypt_field(
                row["email_enc"], table="users", column="email_enc", row_id=user_id)

    # -- operator actions ---------------------------------------------------------------------------------------
    def disable(self, user_id: str, now: int) -> bool:
        with self.db.transaction() as tx:
            done = disable_in(tx, user_id, now)
            if done:
                audit_mod.record(tx, now, "user_disabled", user_id=user_id)
            return done

    def enable(self, user_id: str, now: int) -> bool:
        """Re-allow login. Revoked tokens are NOT restored (D88)."""
        with self.db.transaction() as tx:
            done = tx.execute(ENABLE_SQL, (user_id,)) > 0
            if done:
                audit_mod.record(tx, now, "user_enabled", user_id=user_id)
            return done

    def find_ids_by_email(self, email: str) -> "list[str]":
        """User ids whose stored e-mail blind index equals ``email`` (any status; one lookup per ring key)."""
        email_n = normalize_email(email)
        found: "list[str]" = []
        with self.db.transaction() as tx:
            for candidate in self.crypto.blind_index_all("email", email_n):
                for row in tx.fetchall(BY_EMAIL_SQL, (candidate,)):
                    if row["id"] not in found:
                        found.append(row["id"])
        return found

    def counts(self) -> "dict[str, int]":
        """Users per status plus ``can_write`` holders: numbers only."""
        return count_users(self.db)

    def reconcile(self, row_ok, now: int, *, dry_run: bool = False, batch: int = REKEY_BATCH) -> "tuple[int, int]":
        """Disable every ACTIVE user the current policy no longer allows (``row_ok`` is ``AccessPolicy.row_ok``:
        blind indexes only, no decryption). Sessions are deleted and tokens revoked in the same transaction as the
        disable. Returns ``(examined, disabled)``; with ``dry_run`` nothing is changed (disabled = would disable)."""
        examined = disabled = 0
        last = ""
        while True:
            with self.db.transaction() as tx:
                rows = tx.fetchall(RECONCILE_SELECT_SQL, (last, int(batch)))
                for row in rows:
                    last = row["id"]
                    examined += 1
                    if row_ok(row):
                        continue
                    if dry_run:
                        disabled += 1
                    elif disable_in(tx, row["id"], now):
                        disabled += 1
                        audit_mod.record(tx, now, "user_reconciled", user_id=row["id"])
            if len(rows) < batch:
                break
        return examined, disabled

    def set_can_write(self, user_id: str, allowed: bool, now: int) -> bool:
        with self.db.transaction() as tx:
            done = tx.execute(SET_WRITE_SQL, (1 if allowed else 0, user_id)) > 0
            if done:
                audit_mod.record(tx, now, "write_granted" if allowed else "write_revoked", user_id=user_id)
            return done

    # -- delete / export ----------------------------------------------------------------------------------------
    def delete(self, user_id: str, now: int) -> bool:
        """Hard delete in one transaction (D55): audit rows anonymised, per-user counters removed, cascade on
        sessions and tokens, and an ``account_deleted`` event WITHOUT a user id."""
        with self.db.transaction() as tx:
            tx.execute(ANON_AUDIT_SQL, (user_id,))
            ratelimit.delete_user_keys(tx, self.crypto, user_id)
            removed = tx.execute(DELETE_SQL, (user_id,)) > 0
            if removed:
                audit_mod.record(tx, now, "account_deleted")
            return removed

    def export(self, user_id: str, now: int) -> Optional[dict]:
        """The data-subject export (design 3.12). Never contains hashes, ip hashes or credentials."""
        with self.db.transaction() as tx:
            row = tx.fetchone(GET_SQL, (user_id,))
            if row is None:
                return None
            email = tx.fetchone(EMAIL_SQL, (user_id,))["email_enc"]
            data = {
                "schema": EXPORT_SCHEMA,
                "exportedAt": int(now),
                "profile": {"email": self.crypto.decrypt_field(email, table="users", column="email_enc", row_id=user_id)},
                "createdAt": row["created_at"],
                "lastLoginAt": row["last_login_at"],
                "sessions": [{"createdAt": s["created_at"], "lastSeenAt": s["last_seen_at"], "expiresAt": s["expires_at"]}
                             for s in tx.fetchall(EXPORT_SESSIONS_SQL, (user_id,))],
                "tokens": [{"id": t["id"], "name": t["name"], "scope": t["scope"], "createdAt": t["created_at"],
                            "expiresAt": t["expires_at"], "lastUsedAt": t["last_used_at"], "revokedAt": t["revoked_at"]}
                           for t in tx.fetchall(EXPORT_TOKENS_SQL, (user_id,))],
                "audit": [{"ts": a["ts"], "event": a["event"], "code": a["code"], "ref": a["ref"]}
                          for a in tx.fetchall(EXPORT_AUDIT_SQL, (user_id,))],
            }
            audit_mod.record(tx, now, "export", user_id=user_id)
            return data

    # -- key rotation ---------------------------------------------------------------------------------------------
    def rekey(self, *, dry_run: bool = False, batch: int = REKEY_BATCH, now: int = 0) -> RekeyReport:
        """Move every row whose ``bidx_kid`` is not the active key onto the active key (decrypt with any ring key,
        re-encrypt, recompute the three blind indexes). Idempotent, resumable (only non-active rows are touched),
        one transaction per batch. A row that cannot be decrypted is counted as failed and skipped."""
        crypto = self.crypto
        kid = crypto.ring.active_kid
        pending = rekeyed = failed = 0
        last_id = ""
        while True:
            with self.db.transaction() as tx:
                rows = tx.fetchall(REKEY_SELECT_SQL, (kid, last_id, int(batch)))
                for row in rows:
                    pending += 1
                    last_id = row["id"]
                    try:
                        sub = crypto.decrypt_field(row["sub_enc"], table="users", column="sub_enc", row_id=row["id"])
                        email = crypto.decrypt_field(row["email_enc"], table="users", column="email_enc", row_id=row["id"])
                    except CryptoError:
                        failed += 1
                        continue
                    if dry_run:
                        continue
                    done = tx.execute(REKEY_UPDATE_SQL, (
                        crypto.blind_index("sub", sub),
                        crypto.encrypt_field(sub, table="users", column="sub_enc", row_id=row["id"]),
                        crypto.blind_index("email", email),
                        crypto.encrypt_field(email, table="users", column="email_enc", row_id=row["id"]),
                        crypto.blind_index("domain", email_domain(email)), kid, row["id"], row["bidx_kid"],
                    ))
                    rekeyed += 1 if done > 0 else 0
                if rows and not dry_run and rekeyed:
                    audit_mod.record(tx, now, "key_rekeyed", code="batch")
            if len(rows) < batch:
                break
        return RekeyReport(pending, rekeyed, failed, dry_run)

    def unknown_key_ids(self) -> "list[str]":
        """Key ids referenced by stored rows (``bidx_kid``: blind indexes and envelopes always move together) that are
        NOT in the key ring.
        Non-empty means the operator dropped a key before ``rekey`` finished: the affected users could not be found
        by their ``sub`` any more and their next login would silently create a second account."""
        ring = set(self.crypto.ring.kids)
        with self.db.transaction() as tx:
            found = {r["bidx_kid"] for r in tx.fetchall(DISTINCT_KIDS_SQL)}
        return sorted(found - ring)

    def key_references(self) -> "dict[str, int]":
        """Rows per blind-index key id, plus ``enc:<kid>`` counts of rows holding ciphertext under each ring key."""
        out: "dict[str, int]" = {}
        with self.db.transaction() as tx:
            for r in tx.fetchall(REFS_SQL):
                out[r["bidx_kid"]] = r["n"]
            for ring_kid in self.crypto.ring.kids:
                pattern = "v1." + ring_kid + ".%"
                out["enc:" + ring_kid] = tx.fetchone(ENC_REFS_SQL, (pattern, pattern))["n"]
        return out
