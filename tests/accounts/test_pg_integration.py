"""Real-Postgres integration tests for the database layer.

They run ONLY when ``GEMARC_TEST_PG_URL`` points at a disposable local Postgres (never a production database: the
tests create and drop tables whose names start with ``gemarc_t_``). Otherwise the whole class is skipped with a loud
reason, which adds one skipped test to the suite count.

    GEMARC_TEST_PG_URL=postgresql://user@127.0.0.1:5432/scratch  (psycopg must be importable)
"""
from __future__ import annotations

import os
import unittest

from gemarc_mcp.accounts import db as D

URL = os.environ.get("GEMARC_TEST_PG_URL", "").strip()
REASON = (
    "REAL POSTGRES NOT EXERCISED: set GEMARC_TEST_PG_URL to a disposable local Postgres "
    "(and install psycopg) to run the Postgres integration tests"
)


class RealPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not URL:  # one skip for the whole class, not one per test
            raise unittest.SkipTest(REASON)

    def setUp(self):
        self.db = D.Database(D.PostgresBackend(URL))
        with self.db.transaction() as tx:
            tx.execute("DROP TABLE IF EXISTS gemarc_t_child")
            tx.execute("DROP TABLE IF EXISTS gemarc_t_parent")
            tx.execute("CREATE TABLE gemarc_t_parent (id TEXT PRIMARY KEY, n BIGINT NOT NULL DEFAULT 0)")
            tx.execute("CREATE TABLE gemarc_t_child (id TEXT PRIMARY KEY, pid TEXT NOT NULL REFERENCES gemarc_t_parent(id) ON DELETE CASCADE)")
        self.addCleanup(self._drop)

    def _drop(self):
        with self.db.transaction() as tx:
            tx.execute("DROP TABLE IF EXISTS gemarc_t_child")
            tx.execute("DROP TABLE IF EXISTS gemarc_t_parent")

    def test_ping_and_statement_timeout_is_local(self):
        self.assertTrue(self.db.ping())
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT current_setting('statement_timeout') AS t")
        self.assertEqual(row["t"], "5s")
        with self.db.transaction() as tx:  # a second transaction on a new connection: SET LOCAL is applied again
            self.assertEqual(tx.fetchone("SELECT current_setting('statement_timeout') AS t")["t"], "5s")

    def test_parameters_are_bound_and_percent_is_literal(self):
        sentinel = "x'; DROP TABLE gemarc_t_parent; --"
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO gemarc_t_parent (id) VALUES (?)", (sentinel,))
            rows = tx.fetchall("SELECT id FROM gemarc_t_parent WHERE id LIKE 'x%' AND id = ?", (sentinel,))
        self.assertEqual(rows, [{"id": sentinel}])

    def test_upsert_returning_with_qualified_column(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO gemarc_t_parent (id, n) VALUES (?, ?)", ("a", 1))
            row = tx.fetchone(
                "INSERT INTO gemarc_t_parent (id, n) VALUES (?, ?) "
                "ON CONFLICT (id) DO UPDATE SET n = gemarc_t_parent.n + 1 RETURNING n",
                ("a", 1),
            )
        self.assertEqual(row, {"n": 2})

    def test_integrity_error_mapped_and_rolled_back(self):
        with self.assertRaises(D.IntegrityConflict):
            with self.db.transaction() as tx:
                tx.execute("INSERT INTO gemarc_t_child (id, pid) VALUES (?, ?)", ("c", "missing"))
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchall("SELECT id FROM gemarc_t_child"), [])

    def test_cascade_and_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as tx:
                tx.execute("INSERT INTO gemarc_t_parent (id) VALUES (?)", ("p",))
                raise RuntimeError("x")
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO gemarc_t_parent (id) VALUES (?)", ("p",))
            tx.execute("INSERT INTO gemarc_t_child (id, pid) VALUES (?, ?)", ("c", "p"))
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM gemarc_t_parent WHERE id = ?", ("p",))
            self.assertEqual(tx.fetchall("SELECT id FROM gemarc_t_child"), [])

    def test_advisory_xact_lock_and_role_detection_run(self):
        with self.db.transaction() as tx:
            tx.advisory_xact_lock(D.MIGRATION_LOCK_KEY)
            self.assertIn(self.db.detect_role(tx), ("app", "privileged"))

    def test_missing_table_is_not_an_unavailable_database(self):
        with self.assertRaises(D.DBError):
            with self.db.transaction() as tx:
                tx.fetchall("SELECT id FROM gemarc_t_does_not_exist")


if __name__ == "__main__":
    unittest.main()
