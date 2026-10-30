"""Operator CLI: ``python -m gemarc_mcp.accounts keygen|migrate|doctor|status|rekey|gc|reconcile|disable-user|
enable-user|grant-write|revoke-write|purge-user``.

* ``keygen``  prints a fresh ``kid:base64url(32 random bytes)`` keyring entry to stdout. It never writes a file; the
  operator places the value into the Vercel environment (design 8.4). Output is the only place a secret appears.
* ``migrate`` applies pending migrations with ``GEMARC_MIGRATE_DATABASE_URL`` (or ``DATABASE_URL_UNPOOLED``).
* ``doctor``  checks configuration, key ring, connectivity, schema version, runtime role class and a
  create/read/delete round trip of a throwaway row. It prints variable NAMES and reason slugs only.
* ``status``  read-only report: configuration problems and warnings (names and reasons), schema state, user counts,
  key references (and key ids that are missing from the ring). Numbers only, never a user.
* ``rekey``   moves rows onto the active key (``--dry-run`` counts only).
* ``gc``      retention pass until nothing is left to delete (audit, rate limits, sessions, tokens, OAuth states).
* ``reconcile`` disables active users the CURRENT policy (ALLOWED_DOMAINS / ALLOWED_EMAILS) no longer allows
  (``--dry-run`` counts only).
* ``disable-user`` / ``enable-user`` / ``grant-write`` / ``revoke-write`` / ``purge-user`` act on ONE user, chosen with
  ``--id <32 hex>`` or ``--email <address>`` (only the id is printed; ``purge-user`` needs ``--yes``).

Nothing here prints a database URL, a key, an email or any other secret; failures print the GEMARC_ACCT code, a
reason slug and the exception CLASS name only.
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
import time
from typing import Callable, Mapping, Optional, Sequence, TextIO

from . import audit, config, retention, schema
from .crypto import Crypto, KeyRing, KeyRingError, b64u
from .db import Database, DBError
from .flags import parse_flag
from .oidc import AccessPolicy
from .ratelimit import RateLimiter
from .sessions import SessionStore
from .tokens import TokenStore
from . import users as users_mod
from .users import UserAccounts

_KID_RE = re.compile(r"^[a-z0-9]{1,8}\Z")
DOCTOR_ROW_SQL_INSERT = "INSERT INTO rate_limits (key_hash, window_start, count) VALUES (?, 0, 1)"
DOCTOR_ROW_SQL_SELECT = "SELECT count FROM rate_limits WHERE key_hash = ? AND window_start = 0"
DOCTOR_ROW_SQL_DELETE = "DELETE FROM rate_limits WHERE key_hash = ? AND window_start = 0"


#: Reason slug -> operator hint (printed next to the variable name; never a value).
HINTS = {
    "tls_insecure": (
        "Neon strings use sslmode=require; use sslmode=verify-full&sslrootcert=system (UNVERIFIED against the libpq "
        "inside psycopg-binary) or set GEMARC_ACCOUNTS_DB_TLS_RELAXED=1 to accept require/verify-ca (encrypted, "
        "certificate chain not checked)"
    ),
}
_USER_ID_RE = re.compile(r"[0-9a-f]{32}\Z")


def _hint(reason: str) -> str:
    return f" (hint: {HINTS[reason]})" if reason in HINTS else ""


class CliError(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def keygen_line(kid: str) -> str:
    if not _KID_RE.match(kid):
        raise CliError("kid must match [a-z0-9]{1,8}")
    return f"{kid}:{b64u(secrets.token_bytes(32))}"


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, DBError):
        return f"{exc.code} {exc.reason} ({exc.cause_class or type(exc).__name__})"
    if isinstance(exc, schema.MigrationError):
        where = f" in {exc.version}" if exc.version else ""
        return f"migration refused: {exc.reason}{where}"
    return f"failed: {type(exc).__name__}"


def _open_database(environ: Mapping[str, str], var_names: Sequence[str]) -> "tuple[Database, str]":
    """Pick the first set variable among ``var_names``, validate it like the app does, return (db, var name)."""
    allow_sqlite = parse_flag(environ.get("GEMARC_ACCOUNTS_ALLOW_SQLITE"))
    relaxed = parse_flag(environ.get("GEMARC_ACCOUNTS_DB_TLS_RELAXED"))
    if "bad" in (allow_sqlite, relaxed):
        raise CliError("a boolean GEMARC_ACCOUNTS_* variable has an invalid value (use 1 or 0)")
    var = config.pick_db_var(environ, tuple(var_names))
    if var is None:
        raise CliError("no database URL variable is set (" + ", ".join(var_names) + ")")
    target, reason = config.parse_db_url(var, config.clean(environ.get(var)), allow_sqlite=allow_sqlite == "on",
                                         tls_relaxed=relaxed == "on")
    if target is None:
        raise CliError(f"{config.CODE} {var}: {reason}{_hint(reason)}")
    return Database.from_target(target), var


def _keyring(environ: Mapping[str, str]) -> KeyRing:
    raw = config.clean(environ.get("GEMARC_ACCOUNTS_KEYS"))
    if not raw:
        raise CliError("GEMARC_ACCOUNTS_KEYS is not set")
    try:
        return KeyRing.parse(raw)
    except KeyRingError as exc:
        raise CliError(f"{config.CODE} GEMARC_ACCOUNTS_KEYS: {exc.reason}") from None


def cmd_keygen(args, environ, out, err, clock) -> int:
    print(keygen_line(args.kid), file=out)
    print("Put the whole line into GEMARC_ACCOUNTS_KEYS (first entry is the active key) and keep a copy in a "
          "password manager. Nothing was written to disk.", file=err)
    return 0


def cmd_migrate(args, environ, out, err, clock) -> int:
    db, var = _open_database(environ, ("GEMARC_MIGRATE_DATABASE_URL", "DATABASE_URL_UNPOOLED"))
    try:
        now = int(clock())
        applied = schema.migrate(db, now)
        if applied:
            with db.transaction() as tx:
                for version in applied:
                    audit.record(tx, now, "migration_applied", ref=version)
            print("applied: " + ", ".join(applied), file=out)
        else:
            print("up to date", file=out)
        st = schema.status(db)
        print(f"schema head: {st.head or '-'}; pending: {len(st.pending)}", file=out)
        if config.clean(environ.get("GEMARC_ACCOUNTS_KEYS")):  # optional extra check when the ring is at hand
            try:
                missing = UserAccounts(db, Crypto(_keyring(environ))).unknown_key_ids()
            except (CliError, DBError):
                missing = []
            if missing:
                print("warning: stored rows reference key id(s) " + ", ".join(missing)
                      + " that are not in GEMARC_ACCOUNTS_KEYS", file=out)
        return 0
    except (DBError, schema.MigrationError) as exc:
        print(_describe_error(exc), file=err)
        return 1


def cmd_doctor(args, environ, out, err, clock) -> int:
    failures = 0

    def show(level: str, text: str) -> None:
        nonlocal failures
        if level == "FAIL":
            failures += 1
        print(f"[{level}] {text}", file=out)

    result = config.load(environ)
    problems = [p for p in result.problems if not (p.var == "GEMARC_ACCOUNTS" and p.reason == "not_enabled")]
    if len(problems) != len(result.problems):
        show("info", "GEMARC_ACCOUNTS is not enabled (expected until the last rollout step)")
    if problems:
        for problem in problems:
            show("FAIL", f"config {problem}{_hint(problem.reason)}")
    else:
        show("ok", "config: every variable present and valid")
    for warning in result.warnings:
        show("warn", f"config warning: {warning}")

    try:
        ring = _keyring(environ)
    except CliError as exc:
        ring = None
        show("FAIL", f"key ring: {exc}")
    if ring is not None:
        try:
            crypto = Crypto(ring)
            row_id = crypto.new_row_id()
            sealed = crypto.encrypt_field("doctor@example.invalid", table="users", column="email_enc", row_id=row_id)
            good = crypto.decrypt_field(sealed, table="users", column="email_enc", row_id=row_id) == "doctor@example.invalid"
            show("ok" if good else "FAIL", f"key ring: {len(ring)} key(s), active id {ring.active_kid}, encrypt/decrypt round trip")
        except Exception as exc:  # the class name only, never the message
            show("FAIL", f"key ring self-test {_describe_error(exc)}")

    try:
        db, var = _open_database(environ, config.db_url_candidates(environ))
    except CliError as exc:
        show("FAIL", f"database: {exc}")
        return 1
    try:
        db.ping()
        show("ok", f"database: connected through {var}")
        st = schema.status(db)
        if st.current:
            show("ok", f"schema: up to date at {st.head}")
        else:
            show("FAIL", f"schema: {len(st.pending)} pending, {len(st.unknown)} unknown migration(s) (run migrate)")
        with db.transaction() as tx:
            role = db.detect_role(tx)
        show("warn" if role == "privileged" else "ok", f"runtime role class: {role}")
        if ring is not None and st.current:
            missing = UserAccounts(db, Crypto(ring)).unknown_key_ids()
            if missing:
                show("FAIL", "key ring: stored rows reference key id(s) " + ", ".join(missing)
                     + " that are NOT in GEMARC_ACCOUNTS_KEYS (restore them; the app refuses to serve until then)")
            else:
                show("ok", "key ring: every key id referenced by stored rows is in the ring")
        marker = "doctor-" + secrets.token_hex(8)
        with db.transaction() as tx:
            tx.execute(DOCTOR_ROW_SQL_INSERT, (marker,))
        with db.transaction() as tx:
            seen = tx.fetchone(DOCTOR_ROW_SQL_SELECT, (marker,))
        with db.transaction() as tx:
            tx.execute(DOCTOR_ROW_SQL_DELETE, (marker,))
        with db.transaction() as tx:
            left = tx.fetchone(DOCTOR_ROW_SQL_SELECT, (marker,))
        if seen and seen["count"] == 1 and left is None:
            show("ok", "round trip: throwaway row created, read back and deleted")
        else:
            show("FAIL", "round trip: throwaway row did not behave as expected")
    except (DBError, schema.MigrationError) as exc:
        show("FAIL", f"database {_describe_error(exc)}")
    except Exception as exc:
        show("FAIL", f"database {_describe_error(exc)}")
    print("doctor: " + ("all checks passed" if not failures else f"{failures} check(s) failed"), file=out)
    return 0 if not failures else 1


def cmd_rekey(args, environ, out, err, clock) -> int:
    ring = _keyring(environ)
    db, var = _open_database(environ, ("GEMARC_MIGRATE_DATABASE_URL", "DATABASE_URL_UNPOOLED") + config.db_url_candidates(environ))
    store = UserAccounts(db, Crypto(ring))
    try:
        report = store.rekey(dry_run=args.dry_run, now=int(clock()))
        refs = store.key_references()
    except (DBError, schema.MigrationError) as exc:
        print(_describe_error(exc), file=err)
        return 1
    mode = "dry run" if report.dry_run else "rekey"
    print(f"{mode}: pending {report.pending}, rekeyed {report.rekeyed}, failed {report.failed}", file=out)
    print("key references: " + ", ".join(f"{k}={v}" for k, v in sorted(refs.items())), file=out)
    missing = store.unknown_key_ids()
    if missing:
        print("error: stored rows reference key id(s) " + ", ".join(missing)
              + " that are not in GEMARC_ACCOUNTS_KEYS; put them back before dropping anything", file=err)
    return 1 if (report.failed or missing) else 0


def _admin(environ) -> "tuple[Database, Crypto, UserAccounts]":
    """Ring + database for the operator commands (same variable order as ``rekey``)."""
    ring = _keyring(environ)
    db, _ = _open_database(
        environ, ("GEMARC_MIGRATE_DATABASE_URL", "DATABASE_URL_UNPOOLED") + config.db_url_candidates(environ))
    crypto = Crypto(ring)
    return db, crypto, UserAccounts(db, crypto)


def _target_user(args, store: UserAccounts) -> str:
    """The one user id selected by ``--id`` or ``--email``. Errors never echo the e-mail."""
    if bool(args.id) == bool(args.email):
        raise CliError("give exactly one of --id or --email")
    if args.id:
        if not _USER_ID_RE.match(args.id):
            raise CliError("--id must be 32 lowercase hex characters")
        return args.id
    found = store.find_ids_by_email(args.email)
    if not found:
        raise CliError("no user matches that e-mail", 1)
    if len(found) > 1:
        raise CliError("that e-mail matches more than one user; use --id (ids: " + ", ".join(found) + ")", 1)
    return found[0]


def _user_action(verb: str, ok_text: str, none_text: str):
    def command(args, environ, out, err, clock) -> int:
        _, _, store = _admin(environ)
        try:
            user_id = _target_user(args, store)
            now = int(clock())
            if verb == "disable":
                done = store.disable(user_id, now)
            elif verb == "enable":
                done = store.enable(user_id, now)
            elif verb == "grant":
                done = store.set_can_write(user_id, True, now)
            elif verb == "revoke":
                done = store.set_can_write(user_id, False, now)
            else:  # purge
                if not args.yes:
                    raise CliError("purge-user deletes the account, its sessions and tokens for good: add --yes", 2)
                done = store.delete(user_id, now)
        except (DBError, schema.MigrationError) as exc:
            print(_describe_error(exc), file=err)
            return 1
        print(f"{ok_text if done else none_text}: {user_id}", file=out)
        return 0 if done else 1

    return command


def cmd_gc(args, environ, out, err, clock) -> int:
    problems: list = []
    retention_days = config._int(environ, "GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS", 30, 7, 90, problems)
    if problems:
        raise CliError(f"{config.CODE} GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS: {problems[0].reason}")
    db, crypto, _ = _admin(environ)
    try:
        removed_sum = retention.run_until_empty(
            db, sessions=SessionStore(db, crypto), tokens=TokenStore(db, crypto), limiter=RateLimiter(db, crypto),
            retention_days=retention_days, now=int(clock()))
    except (DBError, schema.MigrationError) as exc:
        print(_describe_error(exc), file=err)
        return 1
    print("deleted: " + ", ".join(f"{k}={v}" for k, v in sorted(removed_sum.items())), file=out)
    return 0


def cmd_reconcile(args, environ, out, err, clock) -> int:
    domains = config._split_list(environ.get("GEMARC_ACCOUNTS_ALLOWED_DOMAINS")) or ["gmail.com"]
    emails = config._split_list(environ.get("GEMARC_ACCOUNTS_ALLOWED_EMAILS"))
    if any(not config._DOMAIN_RE.match(d) for d in domains) or any(
            e.count("@") != 1 or e.startswith("@") or e.endswith("@") or " " in e for e in emails):
        raise CliError(f"{config.CODE} GEMARC_ACCOUNTS_ALLOWED_DOMAINS/_EMAILS: invalid")
    _, crypto, store = _admin(environ)
    policy = AccessPolicy(tuple(domains), tuple(emails), crypto)
    try:
        examined, disabled = store.reconcile(policy.row_ok, int(clock()), dry_run=args.dry_run)
    except (DBError, schema.MigrationError) as exc:
        print(_describe_error(exc), file=err)
        return 1
    print(f"{'dry run' if args.dry_run else 'reconcile'}: examined {examined}, "
          f"{'would disable' if args.dry_run else 'disabled'} {disabled}", file=out)
    return 0


def cmd_status(args, environ, out, err, clock) -> int:
    """Read-only overview. Exit 1 when something needs the operator's attention."""
    attention = 0
    result = config.load(environ)
    problems = [p for p in result.problems if not (p.var == "GEMARC_ACCOUNTS" and p.reason == "not_enabled")]
    if len(problems) != len(result.problems):
        print("accounts: GEMARC_ACCOUNTS is not enabled in this environment", file=out)
    for problem in problems:
        attention += 1
        print(f"config problem: {problem}{_hint(problem.reason)}", file=out)
    for warning in result.warnings:
        print(f"config warning: {warning}", file=out)
    if not problems:
        print("config: complete", file=out)
    try:
        db, _ = _open_database(
            environ, ("GEMARC_MIGRATE_DATABASE_URL", "DATABASE_URL_UNPOOLED") + config.db_url_candidates(environ))
        st = schema.status(db)
        print(f"schema: head {st.head or '-'}; pending {len(st.pending)}; unknown {len(st.unknown)}", file=out)
        attention += 0 if st.current else 1
        if st.current:
            counts = users_mod.count_users(db)
            print("users: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), file=out)
            if config.clean(environ.get("GEMARC_ACCOUNTS_KEYS")):  # key checks need the ring; the rest does not
                store = UserAccounts(db, Crypto(_keyring(environ)))
                refs = store.key_references()
                print("key references: " + ", ".join(f"{k}={v}" for k, v in sorted(refs.items())), file=out)
                missing = store.unknown_key_ids()
                if missing:
                    attention += 1
                    print("key ring problem: stored rows reference key id(s) " + ", ".join(missing)
                          + " that are not in GEMARC_ACCOUNTS_KEYS", file=out)
            else:
                print("key references: skipped (GEMARC_ACCOUNTS_KEYS is not set in this session)", file=out)
    except CliError as exc:
        attention += 1
        print(f"database: {exc}", file=out)
    except (DBError, schema.MigrationError) as exc:
        attention += 1
        print(f"database: {_describe_error(exc)}", file=out)
    print("status: " + ("ok" if not attention else f"{attention} item(s) need attention"), file=out)
    return 0 if not attention else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gemarc_mcp.accounts", description="GemArc accounts: operator commands (no command prints a secret, a URL or an e-mail address)")
    sub = parser.add_subparsers(dest="command", required=True)
    kg = sub.add_parser("keygen", help="print a fresh key ring entry (stdout only)")
    kg.add_argument("--kid", default="k1", help="key id, [a-z0-9]{1,8} (default k1)")
    sub.add_parser("migrate", help="apply pending migrations")
    sub.add_parser("doctor", help="check configuration, connectivity, schema and a round trip")
    rk = sub.add_parser("rekey", help="re-encrypt rows under the active key")
    rk.add_argument("--dry-run", action="store_true", help="count and verify only, change nothing")
    sub.add_parser("status", help="read-only report: configuration, schema, user counts, key references")
    sub.add_parser("gc", help="retention clean-up until nothing is left to delete")
    rc = sub.add_parser("reconcile", help="disable active users that the current policy no longer allows")
    rc.add_argument("--dry-run", action="store_true", help="count only, change nothing")
    for name, text in (("disable-user", "disable a user (sessions deleted, tokens revoked)"),
                       ("enable-user", "allow a disabled user to sign in again (tokens stay revoked)"),
                       ("grant-write", "allow a user to create write-scope tokens"),
                       ("revoke-write", "withdraw the write permission"),
                       ("purge-user", "delete a user and everything that belongs to the user")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--id", help="user id (32 hex)")
        sp.add_argument("--email", help="user e-mail (looked up by blind index, never printed)")
        if name == "purge-user":
            sp.add_argument("--yes", action="store_true", help="confirm the irreversible deletion")
    return parser


COMMANDS = {
    "keygen": cmd_keygen, "migrate": cmd_migrate, "doctor": cmd_doctor, "rekey": cmd_rekey, "status": cmd_status,
    "gc": cmd_gc, "reconcile": cmd_reconcile,
    "disable-user": _user_action("disable", "disabled", "not disabled (no such active user)"),
    "enable-user": _user_action("enable", "enabled", "not enabled (no such disabled user)"),
    "grant-write": _user_action("grant", "write granted", "no such user"),
    "revoke-write": _user_action("revoke", "write revoked", "no such user"),
    "purge-user": _user_action("purge", "purged", "no such user"),
}


def main(argv: Optional[Sequence[str]] = None, environ: Optional[Mapping[str, str]] = None,
         out: Optional[TextIO] = None, err: Optional[TextIO] = None, clock: Callable[[], float] = time.time) -> int:
    import os

    out = out or sys.stdout
    err = err or sys.stderr
    environ = os.environ if environ is None else environ
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return COMMANDS[args.command](args, environ, out, err, clock)
    except CliError as exc:
        print(str(exc), file=err)
        return exc.code
    except Exception as exc:  # never print the message: it may carry driver detail
        print(_describe_error(exc), file=err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
