"""Database layer tests: placeholder translation, SQLite behaviour, Postgres path via a fake DBAPI driver,
and a source scan that forbids string-built SQL anywhere in the accounts package.

The Postgres tests use an in-process FAKE driver. They check what the adapter sends (SQL text, parameters, order of
calls), not what a real server does: real Postgres is covered, if at all, by test_pg_integration.py.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.accounts import db as D

PACKAGE = Path(D.__file__).resolve().parent
SQL_WORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|CREATE INDEX|DROP|ALTER|PRAGMA|SET LOCAL|WHERE)\b")


class TranslatePlaceholders(unittest.TestCase):
    def test_question_marks_become_percent_s(self):
        self.assertEqual(D.translate_placeholders("SELECT 1 WHERE a = ? AND b = ?", (1, 2)), "SELECT 1 WHERE a = %s AND b = %s")

    def test_literal_percent_is_escaped(self):
        self.assertEqual(D.translate_placeholders("SELECT 1 WHERE a LIKE 'x%' AND b = ?", (1,)), "SELECT 1 WHERE a LIKE 'x%%' AND b = %s")

    def test_question_mark_inside_literal_is_refused(self):
        with self.assertRaises(ValueError):
            D.translate_placeholders("SELECT 'what?' WHERE a = ?", (1,))

    def test_count_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            D.translate_placeholders("SELECT ?", ())
        with self.assertRaises(ValueError):
            D.translate_placeholders("SELECT 1", (1,))

    def test_doubled_quote_does_not_break_literal_tracking(self):
        self.assertEqual(D.translate_placeholders("SELECT 'it''s' WHERE a = ?", (1,)), "SELECT 'it''s' WHERE a = %s")


class SqliteBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gemarc-db-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = D.Database.sqlite(os.path.join(self.tmp, "t.db"))
        with self.db.transaction() as tx:
            tx.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
            tx.execute("CREATE TABLE child (id TEXT PRIMARY KEY, pid TEXT NOT NULL REFERENCES parent(id) ON DELETE CASCADE)")

    def test_foreign_keys_are_on_and_cascade(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO parent (id) VALUES (?)", ("p",))
            tx.execute("INSERT INTO child (id, pid) VALUES (?, ?)", ("c", "p"))
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM parent WHERE id = ?", ("p",))
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchall("SELECT id FROM child"), [])

    def test_foreign_key_violation_is_an_integrity_conflict_without_driver_text(self):
        with self.assertRaises(D.IntegrityConflict) as ctx:
            with self.db.transaction() as tx:
                tx.execute("INSERT INTO child (id, pid) VALUES (?, ?)", ("c", "missing-parent"))
        self.assertNotIn("missing-parent", str(ctx.exception))
        self.assertNotIn("FOREIGN", str(ctx.exception).upper())

    def test_exception_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as tx:
                tx.execute("INSERT INTO parent (id) VALUES (?)", ("p",))
                raise RuntimeError("boom")
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchall("SELECT id FROM parent"), [])

    def test_commit_persists_and_rows_are_dicts(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO parent (id) VALUES (?)", ("p",))
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchone("SELECT id FROM parent WHERE id = ?", ("p",)), {"id": "p"})
            self.assertIsNone(tx.fetchone("SELECT id FROM parent WHERE id = ?", ("q",)))

    def test_returning_clause_is_fetchable(self):
        with self.db.transaction() as tx:
            rows = tx.fetchall("INSERT INTO parent (id) VALUES (?) RETURNING id", ("p",))
        self.assertEqual(rows, [{"id": "p"}])

    def test_no_set_local_on_sqlite(self):
        with self.db.transaction() as tx:
            self.assertEqual(tx.kind, "sqlite")
            self.assertEqual(self.db.role_class, None)

    def test_placeholder_mismatch_is_refused_on_sqlite_too(self):
        with self.assertRaises(ValueError):
            with self.db.transaction() as tx:
                tx.execute("SELECT ?", ())

    def test_ping(self):
        self.assertTrue(self.db.ping())


# -- fake DBAPI driver ---------------------------------------------------------------------------------------------
class _FakeError(Exception):
    pass


class _FakeIntegrity(_FakeError):
    pass


class _FakeOperational(_FakeError):
    pass


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self.rowcount = 1
        self._rows = []

    def execute(self, sql, params=()):
        self.conn.log.append(("execute", sql, tuple(params)))
        handler = self.conn.handler
        if handler is not None:
            result = handler(sql, tuple(params))
            if isinstance(result, BaseException):
                raise result
            if result is not None:
                names, rows = result
                self.description = [(n,) for n in names]
                self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, log, handler):
        self.log = log
        self.handler = handler
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.closed = True
        self.log.append(("close",))


class FakeDriver:
    Error = _FakeError
    IntegrityError = _FakeIntegrity
    OperationalError = _FakeOperational

    def __init__(self, *, fail_connects=0, handler=None):
        self.fail_connects = fail_connects
        self.handler = handler
        self.log = []
        self.connect_calls = []

    def connect(self, url, **kwargs):
        self.connect_calls.append((url, kwargs))
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise _FakeOperational("could not connect to " + url)
        return FakeConnection(self.log, self.handler)


def pg_db(driver, **kw):
    return D.Database(D.PostgresBackend("postgresql://u:secret@host.example/db", driver), sleep=lambda s: None, **kw)


class PostgresViaFakeDriver(unittest.TestCase):
    def test_connect_arguments(self):
        drv = FakeDriver()
        with pg_db(drv).transaction():
            pass
        url, kwargs = drv.connect_calls[0]
        self.assertTrue(url.startswith("postgresql://"))
        self.assertEqual(kwargs, {"connect_timeout": 5, "prepare_threshold": None, "autocommit": False})

    def test_first_statement_is_set_local_statement_timeout_then_commit_then_close(self):
        drv = FakeDriver()
        with pg_db(drv).transaction() as tx:
            tx.execute("DELETE FROM sessions WHERE user_id = ?", ("u",))
        self.assertEqual(drv.log[0], ("execute", "SET LOCAL statement_timeout = '5s'", ()))
        self.assertEqual(drv.log[1], ("execute", "DELETE FROM sessions WHERE user_id = %s", ("u",)))
        self.assertEqual([e[0] for e in drv.log[2:]], ["commit", "close"])

    def test_percent_is_escaped_and_params_are_never_inlined(self):
        drv = FakeDriver()
        sentinel = "x'; DROP TABLE users; --"
        with pg_db(drv).transaction() as tx:
            tx.execute("SELECT 1 WHERE a LIKE 'p%' AND b = ?", (sentinel,))
        sql, params = drv.log[1][1], drv.log[1][2]
        self.assertEqual(sql, "SELECT 1 WHERE a LIKE 'p%%' AND b = %s")
        self.assertEqual(params, (sentinel,))
        self.assertNotIn("DROP", sql)

    def test_exception_rolls_back_and_never_commits(self):
        drv = FakeDriver()
        with self.assertRaises(RuntimeError):
            with pg_db(drv).transaction():
                raise RuntimeError("x")
        kinds = [e[0] for e in drv.log]
        self.assertIn("rollback", kinds)
        self.assertNotIn("commit", kinds)
        self.assertEqual(kinds[-1], "close")

    def test_connect_is_retried_exactly_once(self):
        drv = FakeDriver(fail_connects=1)
        with pg_db(drv).transaction():
            pass
        self.assertEqual(len(drv.connect_calls), 2)
        drv2 = FakeDriver(fail_connects=5)
        with self.assertRaises(D.DBUnavailable) as ctx:
            with pg_db(drv2).transaction():
                pass
        self.assertEqual(len(drv2.connect_calls), 2)
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_002")
        # neither the URL nor the driver message may appear in the error
        self.assertNotIn("secret", str(ctx.exception))
        self.assertNotIn("host.example", str(ctx.exception))

    def test_statement_is_never_retried(self):
        def handler(sql, params):
            if sql.startswith("INSERT"):
                return _FakeOperational("server closed the connection")
            return None

        drv = FakeDriver(handler=handler)
        with self.assertRaises(D.DBUnavailable):
            with pg_db(drv).transaction() as tx:
                tx.execute("INSERT INTO t (a) VALUES (?)", (1,))
        inserts = [e for e in drv.log if e[0] == "execute" and e[1].startswith("INSERT")]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(len(drv.connect_calls), 1)

    def test_integrity_error_is_mapped_without_driver_text(self):
        drv = FakeDriver(handler=lambda sql, params: _FakeIntegrity("DETAIL: Key (sub_bidx)=(SENTINEL) already exists") if sql.startswith("INSERT") else None)
        with self.assertRaises(D.IntegrityConflict) as ctx:
            with pg_db(drv).transaction() as tx:
                tx.execute("INSERT INTO t (a) VALUES (?)", (1,))
        self.assertNotIn("SENTINEL", str(ctx.exception))
        self.assertNotIn("SENTINEL", repr(ctx.exception.__cause__))
        self.assertEqual(ctx.exception.cause_class, "_FakeIntegrity")

    def test_advisory_lock_is_transaction_scoped_function(self):
        drv = FakeDriver()
        with pg_db(drv).transaction() as tx:
            tx.advisory_xact_lock(D.MIGRATION_LOCK_KEY)
        sql = [e[1] for e in drv.log if e[0] == "execute"]
        self.assertIn("SELECT pg_advisory_xact_lock(%s)", sql)
        self.assertFalse(any("pg_advisory_lock(" in s or "pg_try_advisory_lock" in s for s in sql))

    def test_sqlite_never_gets_an_advisory_lock_statement(self):
        tmp = tempfile.mkdtemp(prefix="gemarc-db-")
        self.addCleanup(shutil.rmtree, tmp, True)
        db = D.Database.sqlite(os.path.join(tmp, "t.db"))
        with db.transaction() as tx:
            tx.advisory_xact_lock(1)  # a no-op, would raise on SQLite otherwise

    def test_missing_driver_is_a_clean_unavailable_error(self):
        backend = D.PostgresBackend("postgresql://u:p@h/db")
        saved = sys.modules.get("psycopg", "absent")
        sys.modules["psycopg"] = None  # forces ImportError even if the driver is installed
        try:
            with self.assertRaises(D.DBUnavailable) as ctx:
                D.Database(backend, sleep=lambda s: None)._connect()
        finally:
            if saved == "absent":
                del sys.modules["psycopg"]
            else:
                sys.modules["psycopg"] = saved
        self.assertEqual(ctx.exception.reason, "driver_missing")


class RoleDetection(unittest.TestCase):
    def role(self, can_create, present, can_truncate, is_owner):
        def handler(sql, params):
            if "has_schema_privilege" in sql:
                return (["can_create"], [(can_create,)])
            if "IS NOT NULL AS present" in sql:
                return (["present"], [(present,)])
            if "has_table_privilege" in sql:
                return (["can_truncate", "is_owner"], [(can_truncate, is_owner)])
            return None

        drv = FakeDriver(handler=handler)
        db = pg_db(drv)
        with db.transaction() as tx:
            return db.detect_role(tx)

    def test_classification(self):
        self.assertEqual(self.role(False, True, False, False), "app")
        self.assertEqual(self.role(True, True, False, False), "privileged")
        self.assertEqual(self.role(False, True, True, False), "privileged")
        self.assertEqual(self.role(False, True, False, True), "privileged")
        self.assertEqual(self.role(False, False, False, False), "app")

    def test_privileged_runtime_role_is_refused_unless_relaxed(self):
        def handler(sql, params):
            if "has_schema_privilege" in sql:
                return (["can_create"], [(True,)])
            return None

        with self.assertRaises(D.RolePrivileged) as ctx:
            with pg_db(FakeDriver(handler=handler), enforce_role=True).transaction():
                pass
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_004")
        with pg_db(FakeDriver(handler=handler), enforce_role=True, role_relaxed=True).transaction():
            pass

    def test_sqlite_counts_as_app(self):
        tmp = tempfile.mkdtemp(prefix="gemarc-db-")
        self.addCleanup(shutil.rmtree, tmp, True)
        db = D.Database.sqlite(os.path.join(tmp, "t.db"), enforce_role=True)
        with db.transaction() as tx:
            self.assertEqual(db.detect_role(tx), "app")


class NoDriverAtImport(unittest.TestCase):
    def test_importing_the_package_modules_does_not_import_psycopg(self):
        import importlib

        for name in ("config", "crypto", "db", "flags"):
            importlib.import_module("gemarc_mcp.accounts." + name)
        if "psycopg" in sys.modules:
            self.skipTest("psycopg was imported by another test or the environment")
        self.assertNotIn("psycopg", sys.modules)


# -- source scan: no string-built SQL -------------------------------------------------------------------------------
def _package_files():
    return [p for p in PACKAGE.rglob("*.py")]


def _str_constants(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


class SourceScan(unittest.TestCase):
    def test_package_is_not_empty(self):
        self.assertGreaterEqual(len(_package_files()), 5)

    def test_no_fstring_format_percent_or_concatenation_builds_sql(self):
        offences = []
        for path in _package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and ast.get_docstring(node, clean=False) is not None:
                    docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                where = f"{path.name}:{getattr(node, 'lineno', 0)}"
                if isinstance(node, ast.JoinedStr):
                    if any(SQL_WORD.search(s) for s in _str_constants(node)):
                        offences.append(where + " f-string with SQL text")
                elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
                    parts = [n for n in (node.left, node.right)]
                    consts = [s for p in parts for s in _str_constants(p) if id(p) not in docstrings]
                    if any(SQL_WORD.search(s) for s in consts):
                        offences.append(where + " %/+ on SQL text")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "format_map"):
                    if isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str) and SQL_WORD.search(node.func.value.value):
                        offences.append(where + " .format on SQL text")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
                    if any(s.strip() in ("?", "?,", ",?", "%s") for s in _str_constants(node)):
                        offences.append(where + " join of placeholders (dynamic IN list)")
        self.assertEqual(offences, [])

    def test_no_question_mark_inside_sql_string_literals(self):
        offences = []
        for path in _package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_WORD.search(node.value) and len(node.value) < 2000:
                    # quoted regions of the statement
                    for quoted in re.findall(r"'([^']*)'", node.value):
                        if "?" in quoted:
                            offences.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offences, [])

    def test_no_exception_logging_with_text(self):
        offences = []
        for path in _package_files():
            text = path.read_text(encoding="utf-8")
            for needle in ("log.exception", "logger.exception", "exc_info", "logging.exception", "print(exc", "traceback"):
                if needle in text:
                    offences.append(f"{path.name}: {needle}")
        self.assertEqual(offences, [])

    def test_sql_statements_in_migrations_are_dialect_portable(self):
        banned = re.compile(r"\b(ILIKE|SERIAL|BYTEA|BLOB|TIMESTAMPTZ|BOOLEAN|NOW\(\)|CURRENT_TIMESTAMP|AUTOINCREMENT)\b|::|INSERT OR REPLACE|DELETE\b[^;]*\bLIMIT", re.IGNORECASE)
        for path in (PACKAGE / "migrations").glob("*.sql"):
            body = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
            self.assertIsNone(banned.search(body), path.name)


if __name__ == "__main__":
    unittest.main()
