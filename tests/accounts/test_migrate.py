"""Migration runner: empty database, idempotence, checksum guard, concurrency, file rules."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from gemarc_mcp.accounts import schema
from gemarc_mcp.accounts.db import Database, SchemaOutdated

from .support import T0, DbCase

EXPECTED_TABLES = {"users", "sessions", "api_tokens", "audit_log", "rate_limits", "oauth_states", "schema_migrations"}


class Migrate(DbCase):
    migrate_on_setup = False

    def tables(self):
        with self.db.transaction() as tx:
            return {r["name"] for r in tx.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def test_empty_database_gets_all_seven_tables(self):
        self.assertEqual(self.tables(), set())
        applied = schema.migrate(self.db, T0)
        self.assertEqual(applied, ["0001_init"])
        self.assertEqual(self.tables() - {"sqlite_sequence"}, EXPECTED_TABLES)

    def test_second_run_is_idempotent(self):
        schema.migrate(self.db, T0)
        before = self.dump()
        self.assertEqual(schema.migrate(self.db, T0 + 10), [])
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.count("schema_migrations"), len(schema.load_migrations()))

    def test_records_version_checksum_and_time(self):
        schema.migrate(self.db, T0)
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT * FROM schema_migrations")
        m = schema.load_migrations()[0]
        self.assertEqual((row["version"], row["checksum"], row["applied_at"]), (m.version, m.checksum, T0))
        self.assertEqual(len(m.checksum), 64)

    def test_status_and_require_current(self):
        st = schema.status(self.db)
        self.assertEqual((st.applied, st.pending), ((), ("0001_init",)))
        with self.assertRaises(SchemaOutdated) as ctx:
            schema.require_current(self.db)
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_003")
        schema.migrate(self.db, T0)
        st = schema.status(self.db)
        self.assertTrue(st.current)
        self.assertEqual(st.head, "0001_init")
        schema.require_current(self.db)

    def test_checksum_change_is_refused(self):
        schema.migrate(self.db, T0)
        with self.db.transaction() as tx:
            tx.execute("UPDATE schema_migrations SET checksum = ?", ("0" * 64,))
        with self.assertRaises(schema.MigrationError) as ctx:
            schema.migrate(self.db, T0)
        self.assertEqual(ctx.exception.reason, "checksum_mismatch")
        with self.assertRaises(schema.MigrationError):
            schema.status(self.db)

    def test_edited_file_is_refused(self):
        folder = Path(self.tmp) / "m"
        shutil.copytree(schema.migrations_dir(), folder)
        schema.migrate(self.db, T0, directory=folder)
        target = folder / "0001_init.sql"
        target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8", newline="\n")
        with self.assertRaises(schema.MigrationError):
            schema.migrate(self.db, T0, directory=folder)

    def test_crlf_checkout_has_the_same_checksum(self):
        folder = Path(self.tmp) / "crlf"
        shutil.copytree(schema.migrations_dir(), folder)
        target = folder / "0001_init.sql"
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(schema.load_migrations(folder)[0].checksum, schema.load_migrations()[0].checksum)

    def test_second_migration_applies_after_first_without_touching_it(self):
        folder = Path(self.tmp) / "two"
        shutil.copytree(schema.migrations_dir(), folder)
        schema.migrate(self.db, T0, directory=folder)
        (folder / "0002_extra.sql").write_text("CREATE TABLE IF NOT EXISTS extra (id TEXT PRIMARY KEY);\n", encoding="utf-8")
        self.assertEqual(schema.migrate(self.db, T0 + 1, directory=folder), ["0002_extra"])
        self.assertIn("extra", self.tables())

    def test_failed_migration_rolls_back_completely(self):
        folder = Path(self.tmp) / "bad"
        shutil.copytree(schema.migrations_dir(), folder)
        schema.migrate(self.db, T0, directory=folder)
        (folder / "0002_bad.sql").write_text(
            "CREATE TABLE half_done (id TEXT PRIMARY KEY);\nINSERT INTO no_such_table VALUES (1);\n", encoding="utf-8")
        with self.assertRaises(Exception):
            schema.migrate(self.db, T0 + 1, directory=folder)
        self.assertNotIn("half_done", self.tables())
        self.assertEqual(schema.status(self.db, folder).pending, ("0002_bad",))

    def test_concurrent_runners_apply_once(self):
        results, errors = [], []
        barrier = threading.Barrier(6)

        def run():
            try:
                barrier.wait()
                results.append(tuple(schema.migrate(Database.sqlite(self.path), T0)))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(repr(exc))

        threads = [threading.Thread(target=run) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [()] * 5 + [("0001_init",)])
        self.assertEqual(self.count("schema_migrations"), 1)


class FileRules(unittest.TestCase):
    def test_every_shipped_file_obeys_the_splitting_rule(self):
        folder = schema.migrations_dir()
        files = sorted(folder.glob("*.sql"))
        self.assertTrue(files)
        for path in files:
            text = schema.normalize_text(path.read_bytes())
            statements = schema.split_statements(text)  # raises on any violation
            self.assertTrue(statements)
            # the semicolon appears exactly once per statement and never in comments
            self.assertEqual(text.count(";"), len(statements), path.name)
            for line in text.split("\n"):
                if line.strip().startswith("--"):
                    self.assertNotIn(";", line, path.name)
            self.assertNotIn("\r", path.read_bytes().decode("utf-8").replace("\r\n", "\n"))

    def test_violations_are_rejected(self):
        for bad in ("SELECT 1; SELECT 2;\n", "SELECT 1 -- a; b\n;\n", "-- comment; here\nSELECT 1;\n", "SELECT 1\n"):
            with self.assertRaises(schema.MigrationError, msg=bad):
                schema.split_statements(bad)

    def test_multiline_statement_and_comment_stripping(self):
        self.assertEqual(schema.split_statements("CREATE TABLE t (\n  a TEXT -- note\n);\n\nSELECT 2;\n"),
                         ["CREATE TABLE t (\n  a TEXT\n)", "SELECT 2"])

    def test_file_names_and_numbering(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "0002_x.sql").write_text("SELECT 1;\n")
        with self.assertRaises(schema.MigrationError):
            schema.load_migrations(tmp)
        (tmp / "0002_x.sql").rename(tmp / "x.sql")
        with self.assertRaises(schema.MigrationError):
            schema.load_migrations(tmp)

    def test_no_password_or_name_column_in_schema(self):
        text = (schema.migrations_dir() / "0001_init.sql").read_text(encoding="utf-8").lower()
        code = "\n".join(line for line in text.split("\n") if not line.strip().startswith("--"))
        for word in ("password", "passwd", "name_enc", "picture", "access_token", "refresh_token"):
            self.assertNotIn(word, code)

    def test_ddl_is_portable(self):
        text = (schema.migrations_dir() / "0001_init.sql").read_text(encoding="utf-8")
        code = "\n".join(l.split("--")[0] for l in text.split("\n")).upper()
        for banned in ("BLOB", "BYTEA", "SERIAL", "TIMESTAMP", "BOOLEAN", "JSON", "NOW()", "CURRENT_TIMESTAMP", "::", "AUTOINCREMENT"):
            self.assertNotIn(banned, code)


if __name__ == "__main__":
    unittest.main()
