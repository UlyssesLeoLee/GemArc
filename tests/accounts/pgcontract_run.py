"""Postgres contract tests for the accounts database layer. NOT collected by ``unittest discover`` (file name).

Run explicitly against a ONE-TIME, throw-away local Postgres you own (it creates and drops tables named like the
accounts tables, so never point it at a real database):

    GEMARC_TEST_PG_URL=postgresql://user@127.0.0.1:5432/scratch PYTHONPATH=src \
        python -m unittest tests.accounts.pgcontract_run -v

Without GEMARC_TEST_PG_URL every test is skipped LOUDLY. Needs ``psycopg`` installed. As of the acct-core task
this file has never been run against a real server (see the handoff notes).
"""
from __future__ import annotations

import os
import threading
import unittest

from gemarc_mcp.accounts import schema
from gemarc_mcp.accounts.db import Database, IntegrityConflict, PostgresBackend

URL = os.environ.get("GEMARC_TEST_PG_URL", "")
SKIP = "SKIPPED LOUDLY: GEMARC_TEST_PG_URL is not set; the Postgres backend was NOT exercised against a real server"
DROP = ("api_tokens", "sessions", "audit_log", "rate_limits", "oauth_states", "users", "schema_migrations")


@unittest.skipUnless(URL, SKIP)
class PostgresContract(unittest.TestCase):
    def setUp(self):
        self.db = Database(PostgresBackend(URL))
        with self.db.transaction() as tx:
            for table in DROP:
                tx.execute("DROP TABLE IF EXISTS " + table + " CASCADE")  # fixed names above; scratch database only
        schema.migrate(self.db, 1_800_000_000)

    def test_migrate_twice_and_status(self):
        self.assertEqual(schema.migrate(self.db, 1_800_000_001), [])
        self.assertTrue(schema.status(self.db).current)

    def test_ping_and_role_detection_runs(self):
        self.assertTrue(self.db.ping())
        with self.db.transaction() as tx:
            self.assertIn(self.db.detect_role(tx), ("app", "privileged"))

    def test_upsert_returning_with_qualified_column(self):
        sql = ("INSERT INTO rate_limits (key_hash, window_start, count) VALUES (?, ?, 1) "
               "ON CONFLICT (key_hash, window_start) DO UPDATE SET count = rate_limits.count + 1 RETURNING count")
        counts = []
        for _ in range(3):
            with self.db.transaction() as tx:
                counts.append(tx.fetchone(sql, ("k", 0))["count"])
        self.assertEqual(counts, [1, 2, 3])

    def test_percent_and_question_mark_translation_against_the_server(self):
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT 'a%b' AS lit, ? AS val, 7 % 4 AS m", ("x",))
        self.assertEqual(row, {"lit": "a%b", "val": "x", "m": 3})

    def test_unique_violation_maps_to_integrity_conflict_without_detail(self):
        ins = ("INSERT INTO users (id, sub_bidx, sub_enc, email_bidx, email_enc, domain_bidx, bidx_kid, created_at) "
               "VALUES (?, ?, 'e', 'b', 'e', 'd', 'k1', 1)")
        with self.db.transaction() as tx:
            tx.execute(ins, ("u1", "SUBSENTINEL"))
        with self.assertRaises(IntegrityConflict) as ctx:
            with self.db.transaction() as tx:
                tx.execute(ins, ("u2", "SUBSENTINEL"))
        self.assertNotIn("SUBSENTINEL", str(ctx.exception))

    def test_cascade_and_set_null(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO users (id, sub_bidx, sub_enc, email_bidx, email_enc, domain_bidx, bidx_kid, created_at) "
                       "VALUES ('u', 's', 'e', 'b', 'e', 'd', 'k1', 1)")
            tx.execute("INSERT INTO sessions (id_hash, user_id, created_at, last_seen_at, idle_expires_at, expires_at) "
                       "VALUES ('h', 'u', 1, 1, 9, 9)")
            tx.execute("INSERT INTO audit_log (id, ts, event, user_id) VALUES ('a', 1, 'login_ok', 'u')")
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM users WHERE id = ?", ("u",))
            self.assertEqual(tx.fetchone("SELECT count(*) AS n FROM sessions")["n"], 0)
            self.assertIsNone(tx.fetchone("SELECT user_id FROM audit_log WHERE id = 'a'")["user_id"])

    def test_row_lock_update_serialises_concurrent_creators(self):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO users (id, sub_bidx, sub_enc, email_bidx, email_enc, domain_bidx, bidx_kid, created_at) "
                       "VALUES ('u', 's', 'e', 'b', 'e', 'd', 'k1', 1)")
        cap, made, errors = 5, [], []

        def create(i):
            try:
                with Database(PostgresBackend(URL)).transaction() as tx:
                    if tx.execute("UPDATE users SET status = status WHERE id = ? AND status = 'active'", ("u",)) != 1:
                        return
                    n = tx.fetchone("SELECT count(*) AS n FROM api_tokens WHERE user_id = ?", ("u",))["n"]
                    if n >= cap:
                        return
                    tx.execute("INSERT INTO api_tokens (id, user_id, name, scope, secret_hash, created_at, expires_at) "
                               "VALUES (?, 'u', 't', 'read', 'h', 1, 2)", ("t%02d" % i,))
                    made.append(i)
            except Exception as exc:  # pragma: no cover
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(made), cap)

    def test_advisory_xact_lock_executes(self):
        with self.db.transaction() as tx:
            tx.advisory_xact_lock(123456789)


if __name__ == "__main__":
    unittest.main()
