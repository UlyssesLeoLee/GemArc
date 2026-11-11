"""The operator area: who may change the deployment settings, and the settings themselves.

Two independent things live here.

**The administrator role.** A signed-in user is an administrator when the operator listed
their address in ``GEMARC_ACCOUNTS_ADMIN_EMAILS`` OR when a row for them exists in
``admin_users`` (migration ``0002_admin``). The rule is the UNION of the two: the
environment list is how the deployment declares its owner (the normal case: one account
owns the Vercel project), the table is how the operator adds further administrators at
runtime without a redeploy.

The two are independent on purpose, and that has a consequence the operator must know:
taking the role away means removing the address from the environment AND revoking the
table grant. Removing one does not remove the other.

**The operator settings.** ``operator_settings`` is a small key/value table whose values
override the environment defaults for the settings an operator may change while the
deployment runs: the personal access token policy and the tool set. An empty table means
the environment decides, so a deployment that never opens the admin area behaves exactly
as it did before. The environment stays the source of the DEFAULTS.

Nothing here logs an address, a token or a secret; the audit reader never selects the
ip hash.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Optional

from .crypto import Crypto, CryptoError, consteq, normalize_email
from .db import Database

#: How many users and audit rows the admin page shows at once.
USER_LIST_LIMIT = 200
AUDIT_LIST_LIMIT = 100

#: Setting keys. These names are the ones the operator sees in the admin page.
PAT_DEFAULT_DAYS = "pat_default_days"
PAT_MAX_DAYS = "pat_max_days"
PAT_MAX_PER_USER = "pat_max_per_user"
TOOLSET = "toolset"

#: The numeric settings the admin page may write, with their bounds. A key outside this
#: table is refused: the table is not a general key/value store.
SETTING_BOUNDS = {
    PAT_DEFAULT_DAYS: (1, 365),
    PAT_MAX_DAYS: (1, 365),
    PAT_MAX_PER_USER: (1, 50),
}

#: The tool sets a deployment may run. ``atlas`` and ``atlas_remote`` are in the list
#: because the code knows them, but whether they can run at all is decided by the
# deployment (the atlas package is not part of the Vercel upload).
TOOLSETS = ("full", "stateless", "atlas", "atlas_remote")

#: The modules a tool set needs at run time. A deployment that does not ship them cannot serve that
#: tool set: the Vercel upload excludes the atlas package, so ``create_server("atlas")`` fails to
#: import and the whole function stops answering -- not at the moment the setting is written, but at
#: the NEXT cold start. Knowing the name is not the same as having the code.
TOOLSET_REQUIRES = {
    "full": (),
    "stateless": (),
    "atlas": ("gemarc_mcp.atlas_tools", "gemarc_mcp.atlas"),
    "atlas_remote": ("gemarc_mcp.atlas",),
}


def toolset_available(name: str) -> bool:
    """True when THIS deployment can serve the tool set (the code it needs is importable here)."""
    for module in TOOLSET_REQUIRES.get(name, ()):
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True

GRANT_SQL = (
    "INSERT INTO admin_users (user_id, granted_at, granted_by) VALUES (?, ?, ?) "
    "ON CONFLICT (user_id) DO NOTHING"
)
REVOKE_SQL = "DELETE FROM admin_users WHERE user_id = ?"
IS_ADMIN_SQL = "SELECT user_id FROM admin_users WHERE user_id = ?"
GRANTED_SQL = "SELECT user_id FROM admin_users ORDER BY granted_at, user_id"
USERS_SQL = (
    "SELECT u.id, u.status, u.can_write, u.created_at, u.last_login_at, u.email_enc, "
    "(a.user_id IS NOT NULL) AS granted FROM users u LEFT JOIN admin_users a ON a.user_id = u.id "
    "ORDER BY u.created_at DESC, u.id LIMIT ?"
)
AUDIT_SQL = "SELECT ts, event, code, ref, user_id FROM audit_log ORDER BY ts DESC, id DESC LIMIT ?"
SETTING_GET_SQL = "SELECT key, value, updated_at FROM operator_settings"
SETTING_PUT_SQL = (
    "INSERT INTO operator_settings (key, value, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT (key) DO UPDATE SET value = ?, updated_at = ?"
)
SETTING_DELETE_SQL = "DELETE FROM operator_settings WHERE key = ?"


@dataclass(frozen=True)
class AdminUser:
    """One row of the user list. ``email`` is None when the stored value cannot be read."""

    id: str
    email: Optional[str]
    status: str
    can_write: bool
    granted: bool  # a row in admin_users (the environment allow-list is applied by the caller)
    created_at: int
    last_login_at: Optional[int]


class AdminService:
    """The administrator role and the runtime settings. Constructing it touches neither the network nor the disk."""

    def __init__(self, db: Database, crypto: Crypto, admin_emails: "tuple[str, ...]" = ()) -> None:
        self.db = db
        self.crypto = crypto
        self.admin_emails = tuple(normalize_email(a) for a in admin_emails)

    # -- the role -----------------------------------------------------------------------------------------------
    def in_allowlist(self, email: Optional[str]) -> bool:
        """True when the deployment's environment list names this address. Constant-time over the list."""
        if not email:
            return False
        candidate = normalize_email(email)
        return any(consteq(candidate, allowed) for allowed in self.admin_emails)

    def granted(self, user_id: str) -> bool:
        """True when a runtime grant exists for this user id."""
        if not user_id:
            return False
        with self.db.transaction() as tx:
            return tx.fetchone(IS_ADMIN_SQL, (user_id,)) is not None

    def is_admin(self, user_id: str, email: Optional[str]) -> bool:
        """The union of the deployment's allow-list and the runtime grants.

        Fail-closed: an address that cannot be read is not an administrator unless the
        table grants the role.
        """
        if user_id and self.granted(user_id):
            return True
        return self.in_allowlist(email)

    def grant(self, user_id: str, now: int, by: Optional[str] = None) -> bool:
        """Add a runtime grant. False when the user already had one."""
        if not user_id:
            return False
        with self.db.transaction() as tx:
            return tx.execute(GRANT_SQL, (user_id, int(now), by or "")) > 0

    def revoke(self, user_id: str) -> bool:
        """Remove a runtime grant. The environment allow-list is NOT touched by this."""
        if not user_id:
            return False
        with self.db.transaction() as tx:
            return tx.execute(REVOKE_SQL, (user_id,)) > 0

    def granted_ids(self) -> "list[str]":
        with self.db.transaction() as tx:
            return [r["user_id"] for r in tx.fetchall(GRANTED_SQL)]

    # -- reads --------------------------------------------------------------------------------------------------
    def list_users(self, limit: int = USER_LIST_LIMIT) -> "list[AdminUser]":
        """Every user with its status and its grants. The e-mail is decrypted for the admin page only."""
        with self.db.transaction() as tx:
            rows = tx.fetchall(USERS_SQL, (int(limit),))
        out: "list[AdminUser]" = []
        for row in rows:
            try:
                email = self.crypto.decrypt_field(row["email_enc"], table="users", column="email_enc",
                                                  row_id=row["id"])
            except CryptoError:
                email = None
            out.append(AdminUser(
                row["id"], email, row["status"], bool(row["can_write"]), bool(row["granted"]),
                int(row["created_at"]), None if row["last_login_at"] is None else int(row["last_login_at"])))
        return out

    def audit_entries(self, limit: int = AUDIT_LIST_LIMIT) -> "list[dict]":
        """The most recent audit rows. The ip hash is never selected: it is not part of the answer."""
        with self.db.transaction() as tx:
            rows = tx.fetchall(AUDIT_SQL, (int(limit),))
        return [{"ts": int(r["ts"]), "event": r["event"] or "", "code": r["code"] or "",
                 "ref": r["ref"] or "", "userId": r["user_id"] or ""} for r in rows]

    # -- the settings -------------------------------------------------------------------------------------------
    def settings(self) -> "dict[str, str]":
        """The stored settings. An empty value means the environment decides."""
        with self.db.transaction() as tx:
            return {r["key"]: r["value"] for r in tx.fetchall(SETTING_GET_SQL)}

    def set_number(self, key: str, value: str, now: int) -> None:
        """Write one numeric setting. Raises ValueError with a short reason."""
        bounds = SETTING_BOUNDS.get(key)
        if bounds is None:
            raise ValueError("unknown_setting")
        text = (value or "").strip()
        if not text.isascii() or not text.isdigit():
            raise ValueError("invalid_value")
        number = int(text)
        low, high = bounds
        if number < low or number > high:
            raise ValueError("out_of_range")
        with self.db.transaction() as tx:
            tx.execute(SETTING_PUT_SQL, (key, str(number), int(now), str(number), int(now)))

    def set_toolset(self, value: str, now: int) -> None:
        """Write the desired tool set. Only the known names are accepted, and only the ones THIS
        deployment can actually serve: a stored tool set the deployment cannot run would break the
        next cold start, not this request."""
        name = (value or "").strip().lower()
        if name not in TOOLSETS:
            raise ValueError("unknown_toolset")
        if not toolset_available(name):
            raise ValueError("toolset_unavailable")
        with self.db.transaction() as tx:
            tx.execute(SETTING_PUT_SQL, (TOOLSET, name, int(now), name, int(now)))

    def clear(self, key: str) -> bool:
        """Drop one stored setting, so the environment decides again."""
        if key not in SETTING_BOUNDS and key != TOOLSET:
            raise ValueError("unknown_setting")
        with self.db.transaction() as tx:
            return tx.execute(SETTING_DELETE_SQL, (key,)) > 0
