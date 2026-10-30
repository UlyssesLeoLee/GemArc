"""Migration runner for the accounts schema (design 5.1 and 5.3).

* Files: ``migrations/NNNN_name.sql``; add-only. Each is applied in ONE transaction with a transaction-scoped
  advisory lock (Postgres) or ``BEGIN IMMEDIATE`` (SQLite), so concurrent runners cannot interleave: the loser
  re-checks ``schema_migrations`` under the lock and skips.
* Splitting rule (no SQL parser): a statement ends at the line whose last non-space character is a semicolon; that
  character appears nowhere else, comments included. Comments (``--``) are stripped before execution.
* The SHA-256 of each applied file (LF-normalised) is recorded; a changed file makes the runner refuse.
* ``schema_migrations`` is created before the first migration (chicken and egg); ``0001`` repeats it idempotently.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .db import MIGRATION_LOCK_KEY, Database, DBError, SchemaOutdated, Tx

_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql\Z")
ENSURE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at BIGINT NOT NULL)"
)
SELECT_APPLIED_SQL = "SELECT version, checksum FROM schema_migrations"
SELECT_ONE_SQL = "SELECT checksum FROM schema_migrations WHERE version = ?"
INSERT_APPLIED_SQL = "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)"
PROBE_SQLITE_SQL = "SELECT count(*) AS present FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
PROBE_PG_SQL = "SELECT to_regclass('public.schema_migrations') IS NOT NULL AS present"


class MigrationError(Exception):
    """A migration file or the recorded state is wrong (reason slug only). ``version`` names the migration file that
    failed (for example ``0002_x``); it is never driver text."""

    def __init__(self, reason: str, version: str = "") -> None:
        super().__init__(f"{reason} {version}".strip())
        self.reason = reason
        self.version = version


@dataclass(frozen=True)
class Migration:
    version: str  # file stem, e.g. "0001_init"
    checksum: str
    statements: "tuple[str, ...]"


@dataclass(frozen=True)
class Status:
    applied: "tuple[str, ...]"
    pending: "tuple[str, ...]"
    unknown: "tuple[str, ...]"  # applied in the database but not shipped with this code

    @property
    def current(self) -> bool:
        return not self.pending and not self.unknown

    @property
    def head(self) -> str:
        return self.applied[-1] if self.applied else ""


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def _strip_comment(line: str) -> str:
    in_lit = False
    for i, ch in enumerate(line):
        if ch == "'":
            in_lit = not in_lit
        elif ch == "-" and not in_lit and line.startswith("--", i):
            return line[:i]
    return line


def normalize_text(raw: bytes) -> str:
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def split_statements(text: str) -> "list[str]":
    """Apply the splitting rule; raises MigrationError for any violation."""
    statements: "list[str]" = []
    current: "list[str]" = []
    for raw in text.split("\n"):
        if ";" in raw:
            stripped = raw.strip()
            if stripped.startswith("--") or raw.count(";") != 1 or not stripped.endswith(";"):
                raise MigrationError("semicolon_rule")
        code = _strip_comment(raw).rstrip()
        if not code.strip():
            continue
        current.append(code)
        if code.endswith(";"):
            statements.append("\n".join(current)[:-1].rstrip())
            current = []
    if current:
        raise MigrationError("unterminated_statement")
    return statements


def load_migrations(directory: Optional[Path] = None) -> "list[Migration]":
    folder = directory or migrations_dir()
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix == ".sql")
    found: "list[Migration]" = []
    expected = 1
    for path in files:
        match = _NAME_RE.match(path.name)
        if not match:
            raise MigrationError("bad_file_name")
        if int(match.group(1)) != expected:
            raise MigrationError("gap_or_duplicate")
        expected += 1
        text = normalize_text(path.read_bytes())
        found.append(
            Migration(path.stem, hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(split_statements(text)))
        )
    return found


def _ensure_table(db: Database) -> None:
    with db.transaction() as tx:
        tx.advisory_xact_lock(MIGRATION_LOCK_KEY)
        tx.execute(ENSURE_TABLE_SQL)


def _table_present(tx: Tx) -> bool:
    row = tx.fetchone(PROBE_PG_SQL if tx.kind == "postgres" else PROBE_SQLITE_SQL)
    return bool(row and row["present"])


def status(db: Database, directory: Optional[Path] = None) -> Status:
    shipped = load_migrations(directory)
    with db.transaction() as tx:
        recorded = {r["version"]: r["checksum"] for r in tx.fetchall(SELECT_APPLIED_SQL)} if _table_present(tx) else {}
    for m in shipped:
        if m.version in recorded and recorded[m.version] != m.checksum:
            raise MigrationError("checksum_mismatch")
    names = [m.version for m in shipped]
    return Status(
        applied=tuple(n for n in names if n in recorded),
        pending=tuple(n for n in names if n not in recorded),
        unknown=tuple(sorted(v for v in recorded if v not in names)),
    )


def require_current(db: Database, directory: Optional[Path] = None) -> None:
    """Raise ``SchemaOutdated`` (GEMARC_ACCT_003) unless every shipped migration is applied."""
    if status(db, directory).pending:
        raise SchemaOutdated("pending_migrations")


def migrate(
    db: Database,
    now: int,
    directory: Optional[Path] = None,
    on_apply: Optional[Callable[[str], None]] = None,
) -> "list[str]":
    """Apply every pending migration; returns the versions applied by THIS call (empty when up to date)."""
    shipped = load_migrations(directory)
    _ensure_table(db)
    applied_now: "list[str]" = []
    for m in shipped:
        with db.transaction() as tx:
            tx.advisory_xact_lock(MIGRATION_LOCK_KEY)
            row = tx.fetchone(SELECT_ONE_SQL, (m.version,))
            if row is not None:
                if row["checksum"] != m.checksum:
                    raise MigrationError("checksum_mismatch")
                continue
            try:
                for statement in m.statements:
                    tx.execute(statement)
                tx.execute(INSERT_APPLIED_SQL, (m.version, m.checksum, int(now)))
            except DBError as exc:
                # A DDL conflict (for example a foreign pre-existing table) is NOT an outage: say which migration
                # failed and keep the driver text out. The transaction rolls back with the exception.
                raise MigrationError("statement_failed", m.version) from None
        applied_now.append(m.version)
        if on_apply:
            on_apply(m.version)
    return applied_now
