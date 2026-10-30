"""Tiny database layer with two backends behind one interface: SQLite (tests, local) and Postgres (production).

Rules (design 3.10, 5.1, 5.5):

* SQL is written once with ``?`` placeholders; the Postgres backend translates them to ``%s`` and escapes a literal
  ``%``. ``?`` inside an SQL string literal is refused so the translation is unambiguous. Values are ALWAYS bound
  parameters: nothing in this package builds SQL text from values (a scan test enforces it).
* One short-lived connection per unit of work (``Database.transaction()``), no in-process pool, never connected at
  import time. The driver is imported lazily on the first Postgres connection.
* Pooled-Neon friendly: no session state, no server-side prepared statements (``prepare_threshold=None``),
  ``SET LOCAL statement_timeout`` at the start of every Postgres transaction, advisory locks only as
  ``pg_advisory_xact_lock`` inside a transaction. No network call belongs inside a transaction.
* Connection ESTABLISHMENT is retried once on a transient error. Nothing is retried after a statement ran, because
  replaying an INSERT/UPDATE is not idempotent.
* Driver exceptions are never forwarded (a Postgres DETAIL can carry key values): they are mapped to ``DBError``
  subclasses carrying a reason slug and the driver exception CLASS NAME only.
* Times are epoch seconds supplied by the application (injectable clock); SQL never calls NOW().
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

CONNECT_TIMEOUT_S = 5
STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '5s'"
#: Fixed 64-bit key of the migration advisory lock.
MIGRATION_LOCK_KEY = 7_247_308_145_226_001
#: Fixed 64-bit key of the advisory lock that serialises the user-cap check with a new registration.
SIGNUP_LOCK_KEY = 7_247_308_145_226_002

ROLE_TABLE_SQL = "SELECT to_regclass('public.users') IS NOT NULL AS present"
ROLE_SCHEMA_SQL = "SELECT has_schema_privilege(current_user, 'public', 'CREATE') AS can_create"
ROLE_TABLE_PRIV_SQL = (
    "SELECT has_table_privilege(current_user, 'public.users', 'TRUNCATE') AS can_truncate, "
    "(SELECT pg_get_userbyid(c.relowner) = current_user FROM pg_class c "
    "WHERE c.oid = to_regclass('public.users')) AS is_owner"
)


class DBError(Exception):
    """Base error. ``code`` is the user-facing GEMARC_ACCT code; ``reason`` a slug; never driver text."""

    code = "GEMARC_ACCT_002"

    def __init__(self, reason: str, cause_class: str = "") -> None:
        super().__init__(f"{self.code} {reason}")
        self.reason = reason
        self.cause_class = cause_class


class DBUnavailable(DBError):
    code = "GEMARC_ACCT_002"


class SchemaOutdated(DBError):
    code = "GEMARC_ACCT_003"


class RolePrivileged(DBError):
    code = "GEMARC_ACCT_004"


class KeyRingMismatch(DBError):
    """Stored rows reference a key id that is missing from the key ring (a key was dropped before ``rekey`` ended)."""

    code = "GEMARC_ACCT_004"


class IntegrityConflict(DBError):
    """A constraint (unique, check, foreign key) refused the statement."""

    code = "GEMARC_ACCT_002"


# -- placeholder handling -----------------------------------------------------------------------------------------
def _scan(sql: str) -> "tuple[list[int], int]":
    """Return ``(positions of ? outside literals, count of ? inside literals)``."""
    outside: "list[int]" = []
    inside = 0
    in_lit = False
    for i, ch in enumerate(sql):
        if ch == "'":
            in_lit = not in_lit  # a doubled '' toggles twice, which is correct
        elif ch == "?":
            if in_lit:
                inside += 1
            else:
                outside.append(i)
    return outside, inside


def check_sql(sql: str, params: Sequence[Any]) -> "list[int]":
    """Validate placeholders against params; returns the positions of the placeholders."""
    positions, inside = _scan(sql)
    if inside:
        raise ValueError("placeholder inside an SQL literal")
    if len(positions) != len(params):
        raise ValueError("placeholder count does not match parameter count")
    return positions


def translate_placeholders(sql: str, params: Sequence[Any]) -> str:
    """``?`` -> ``%s`` and literal ``%`` -> ``%%`` (Postgres driver paramstyle)."""
    positions = check_sql(sql, params)
    out = []
    marks = set(positions)
    for i, ch in enumerate(sql):
        if i in marks:
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def _rows(cursor: Any) -> "list[dict]":
    description = getattr(cursor, "description", None)
    if not description:
        return []
    names = [col[0] for col in description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


# -- transaction handle -------------------------------------------------------------------------------------------
class Tx:
    """One open transaction. Methods take ``?``-style SQL and a parameter sequence."""

    def __init__(self, conn: Any, backend: "Backend") -> None:
        self._conn = conn
        self._backend = backend
        self.kind = backend.kind

    def _run(self, sql: str, params: Sequence[Any]) -> Any:
        params = tuple(params)
        text = translate_placeholders(sql, params) if self.kind == "postgres" else sql
        if self.kind != "postgres":
            check_sql(sql, params)
        cursor = self._conn.cursor()
        try:
            cursor.execute(text, params)
        except BaseException as exc:
            self._backend.raise_mapped(exc)
            raise
        return cursor

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement; returns the affected row count (-1 when the driver cannot tell)."""
        cursor = self._run(sql, params)
        try:
            return int(getattr(cursor, "rowcount", -1))
        finally:
            _close(cursor)

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> "list[dict]":
        """Run a statement and return all rows as dicts (also for INSERT/UPDATE ... RETURNING)."""
        cursor = self._run(sql, params)
        try:
            return _rows(cursor)
        except BaseException as exc:
            self._backend.raise_mapped(exc)
            raise
        finally:
            _close(cursor)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def advisory_xact_lock(self, key: int) -> None:
        """Transaction-scoped advisory lock (Postgres). SQLite already holds the write lock via BEGIN IMMEDIATE."""
        if self.kind == "postgres":
            self.fetchall("SELECT pg_advisory_xact_lock(?)", (key,))


def _close(cursor: Any) -> None:
    close = getattr(cursor, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


# -- backends -----------------------------------------------------------------------------------------------------
class Backend:
    kind = ""

    def open(self) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def begin(self, conn: Any) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def commit(self, conn: Any) -> None:
        conn.commit()

    def rollback(self, conn: Any) -> None:
        conn.rollback()

    def raise_mapped(self, exc: BaseException) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def retryable(self, exc: BaseException) -> bool:  # pragma: no cover - interface
        return False


class SqliteBackend(Backend):
    kind = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path

    def open(self) -> Any:
        conn = sqlite3.connect(self.path, timeout=CONNECT_TIMEOUT_S, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def begin(self, conn: Any) -> None:
        conn.execute("BEGIN IMMEDIATE")

    def commit(self, conn: Any) -> None:
        conn.execute("COMMIT")

    def rollback(self, conn: Any) -> None:
        if conn.in_transaction:
            conn.execute("ROLLBACK")

    def raise_mapped(self, exc: BaseException) -> None:
        if isinstance(exc, sqlite3.IntegrityError):
            raise IntegrityConflict("constraint", type(exc).__name__) from None
        if isinstance(exc, sqlite3.OperationalError):
            raise DBUnavailable("operational", type(exc).__name__) from None
        if isinstance(exc, sqlite3.Error):
            raise DBError("statement", type(exc).__name__) from None

    def retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError)


class PostgresBackend(Backend):
    kind = "postgres"

    def __init__(self, url: str, driver: Any = None) -> None:
        self.url = url
        self._driver = driver

    def driver(self) -> Any:
        if self._driver is None:
            try:
                import psycopg  # lazy: never imported unless a Postgres connection is really opened
            except ImportError:
                raise DBUnavailable("driver_missing", "ImportError") from None
            self._driver = psycopg
        return self._driver

    def open(self) -> Any:
        return self.driver().connect(
            self.url, connect_timeout=CONNECT_TIMEOUT_S, prepare_threshold=None, autocommit=False
        )

    def begin(self, conn: Any) -> None:
        cursor = conn.cursor()
        try:
            cursor.execute(STATEMENT_TIMEOUT_SQL)
        finally:
            _close(cursor)

    def _errors(self) -> "tuple[type, type, type]":
        d = self.driver()
        return d.IntegrityError, d.OperationalError, d.Error

    def raise_mapped(self, exc: BaseException) -> None:
        integrity, operational, base = self._errors()
        if isinstance(exc, integrity):
            raise IntegrityConflict("constraint", type(exc).__name__) from None
        if isinstance(exc, operational):
            raise DBUnavailable("operational", type(exc).__name__) from None
        if isinstance(exc, base):
            raise DBError("statement", type(exc).__name__) from None

    def retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, self._errors()[1])


class Database:
    """Unit-of-work factory. ``with db.transaction() as tx:`` commits on success and rolls back on any exception."""

    def __init__(
        self,
        backend: Backend,
        *,
        enforce_role: bool = False,
        role_relaxed: bool = False,
        sleep: Any = time.sleep,
    ) -> None:
        self.backend = backend
        self.kind = backend.kind
        self._enforce_role = enforce_role
        self._role_relaxed = role_relaxed
        self._role_class: Optional[str] = None
        self._sleep = sleep

    @classmethod
    def from_target(cls, target: Any, *, driver: Any = None, **kw: Any) -> "Database":
        """Build from ``config.DbTarget`` (``kind``, ``url``, ``sqlite_path``)."""
        if target.kind == "sqlite":
            return cls(SqliteBackend(target.sqlite_path), **kw)
        return cls(PostgresBackend(target.url, driver), **kw)

    @classmethod
    def sqlite(cls, path: str, **kw: Any) -> "Database":
        return cls(SqliteBackend(path), **kw)

    @property
    def role_class(self) -> Optional[str]:
        """``'app'`` / ``'privileged'`` once detected, None before the first Postgres use (or never for SQLite)."""
        return self._role_class

    def _connect(self) -> Any:
        try:
            return self.backend.open()
        except BaseException as first:
            if not self.backend.retryable(first):
                self._map_connect(first)
                raise
            self._sleep(0.05)
            try:
                return self.backend.open()
            except BaseException as second:
                self._map_connect(second)
                raise

    def _map_connect(self, exc: BaseException) -> None:
        if isinstance(exc, DBError):
            raise exc
        try:
            self.backend.raise_mapped(exc)
        except DBError as mapped:
            if isinstance(mapped, DBUnavailable):
                raise
            raise DBUnavailable("connect", type(exc).__name__) from None

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        conn = self._connect()
        try:
            try:
                self.backend.begin(conn)
            except BaseException as exc:
                self._map_connect(exc)
                raise
            tx = Tx(conn, self.backend)
            if self._enforce_role and self.kind == "postgres":
                self._check_role(tx)
            try:
                yield tx
            except BaseException:
                self._safe_rollback(conn)
                raise
            try:
                self.backend.commit(conn)
            except BaseException as exc:
                self._safe_rollback(conn)
                self.backend.raise_mapped(exc)
                raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _safe_rollback(self, conn: Any) -> None:
        try:
            self.backend.rollback(conn)
        except Exception:
            pass

    # -- runtime role detection (design D100) -------------------------------------------------------------------
    def detect_role(self, tx: Tx) -> str:
        """Classify the connected role: ``privileged`` when it owns ``users``, can CREATE in ``public`` or can
        TRUNCATE ``users``; otherwise ``app``. SQLite counts as ``app``. Read-only queries."""
        if tx.kind != "postgres":
            return "app"
        schema = tx.fetchone(ROLE_SCHEMA_SQL)
        if schema and schema.get("can_create"):
            return "privileged"
        present = tx.fetchone(ROLE_TABLE_SQL)
        if present and present.get("present"):
            table = tx.fetchone(ROLE_TABLE_PRIV_SQL)
            if table and (table.get("can_truncate") or table.get("is_owner")):
                return "privileged"
        return "app"

    def _check_role(self, tx: Tx) -> None:
        if self._role_class is None:
            self._role_class = self.detect_role(tx)
        if self._role_class == "privileged" and not self._role_relaxed:
            raise RolePrivileged("privileged_runtime_role")

    # -- conveniences -------------------------------------------------------------------------------------------
    def ping(self) -> bool:
        with self.transaction() as tx:
            row = tx.fetchone("SELECT 1 AS one")
        return bool(row and row["one"] == 1)


@contextmanager
def use_tx(db: Database, tx: Optional[Tx]) -> Iterator[Tx]:
    """Yield ``tx`` when the caller already has one, otherwise open (and commit) a transaction of our own."""
    if tx is not None:
        yield tx
    else:
        with db.transaction() as own:
            yield own
