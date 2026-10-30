"""CLI: keygen, migrate, doctor, rekey. Output must never contain secrets."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest

from gemarc_mcp.accounts import __main__ as cli
from gemarc_mcp.accounts import schema
from gemarc_mcp.accounts.crypto import Crypto, KeyRing, b64u
from gemarc_mcp.accounts.db import Database
from gemarc_mcp.accounts.users import UserAccounts

from .support import KEY1, KEY2, T0, DbCase

RING1 = "k1:" + b64u(KEY1)
RING_ROTATED = "k2:" + b64u(KEY2) + ",k1:" + b64u(KEY1)


def run(argv, environ, clock=lambda: T0):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, environ, out, err, clock)
    return code, out.getvalue(), err.getvalue()


class Keygen(unittest.TestCase):
    def test_prints_one_fresh_32_byte_entry_and_writes_no_file(self):
        before = set(os.listdir("."))
        code, out, err = run(["keygen"], {})
        self.assertEqual(code, 0)
        kid, _, material = out.strip().partition(":")
        self.assertEqual(kid, "k1")
        self.assertEqual(len(__import__("base64").urlsafe_b64decode(material + "=")), 32)
        self.assertEqual(KeyRing.parse(out.strip()).active_kid, "k1")
        self.assertNotIn(material, err)
        self.assertNotEqual(out, run(["keygen"], {})[1])
        self.assertEqual(set(os.listdir(".")), before)

    def test_custom_kid_and_validation(self):
        self.assertTrue(run(["keygen", "--kid", "k2"], {})[1].startswith("k2:"))
        code, out, err = run(["keygen", "--kid", "BAD KID"], {})
        self.assertEqual((code, out), (2, ""))

    def test_usage_errors_return_codes_not_tracebacks(self):
        import contextlib

        with contextlib.redirect_stderr(io.StringIO()):  # argparse prints its usage text to the real stderr
            self.assertNotEqual(run([], {})[0], 0)
            self.assertNotEqual(run(["nope"], {})[0], 0)

    def test_module_entry_point_runs(self):
        env = dict(os.environ)
        res = subprocess.run([sys.executable, "-m", "gemarc_mcp.accounts", "keygen"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(res.stdout.strip(), r"^k1:[A-Za-z0-9_-]{43}$")


class MigrateCli(DbCase):
    migrate_on_setup = False

    def env(self, **extra):
        env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"), "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1"}
        env.update(extra)
        return env

    def test_migrate_twice(self):
        code, out, err = run(["migrate"], self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("applied: 0001_init", out)
        self.assertIn("pending: 0", out)
        code, out, _ = run(["migrate"], self.env())
        self.assertEqual((code, out.split("\n")[0]), (0, "up to date"))
        self.assertEqual(self.count("audit_log", "event = 'migration_applied' AND ref = '0001_init'"), 1)

    def test_uses_unpooled_variable_and_prefers_migrate_variable(self):
        env = {"DATABASE_URL_UNPOOLED": "sqlite:///" + self.path.replace("\\", "/"), "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1",
               "DATABASE_URL": "postgresql://u:p@h.example/db"}
        self.assertEqual(run(["migrate"], env)[0], 0)

    def test_missing_url_and_gates(self):
        code, _, err = run(["migrate"], {"DATABASE_URL": "postgresql://u@localhost/x"})
        self.assertEqual(code, 2)
        self.assertIn("GEMARC_MIGRATE_DATABASE_URL", err)
        code, _, err = run(["migrate"], {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///x.db"})
        self.assertEqual(code, 2)
        self.assertIn("sqlite_not_allowed", err)
        insecure = {"GEMARC_MIGRATE_DATABASE_URL": "postgresql://user:SECRETPW@db.example/x?sslmode=disable"}
        code, out, err = run(["migrate"], insecure)
        self.assertEqual(code, 2)
        self.assertNotIn("SECRETPW", out + err)
        self.assertIn("tls_insecure", err)
        self.assertEqual(run(["migrate"], self.env(GEMARC_ACCOUNTS_ALLOW_SQLITE="yes"))[0], 2)

    def test_checksum_mismatch_is_reported_without_traceback(self):
        run(["migrate"], self.env())
        with self.db.transaction() as tx:
            tx.execute("UPDATE schema_migrations SET checksum = 'x'")
        code, out, err = run(["migrate"], self.env())
        self.assertEqual(code, 1)
        self.assertIn("checksum_mismatch", err)


class Doctor(DbCase):
    def env(self, **extra):
        env = {"GEMARC_ACCOUNTS": "1", "GEMARC_ACCOUNTS_BASE_URL": "http://localhost:8000",
               "GEMARC_GOOGLE_CLIENT_ID": "id-123.apps.example", "GEMARC_GOOGLE_CLIENT_SECRET": "SECRET-CLIENT-VALUE",
               "GEMARC_ACCOUNTS_KEYS": RING1, "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1",
               "GEMARC_ACCOUNTS_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/")}
        env.update(extra)
        return env

    def test_healthy_setup_passes_and_leaves_no_row_behind(self):
        code, out, err = run(["doctor"], self.env())
        self.assertEqual(code, 0, out)
        for needle in ("config: every variable", "key ring: 1 key(s)", "connected", "schema: up to date at 0001_init",
                       "runtime role class: app", "round trip", "all checks passed"):
            self.assertIn(needle, out)
        self.assertEqual(self.count("rate_limits"), 0)

    def test_output_contains_no_secrets(self):
        code, out, err = run(["doctor"], self.env())
        for secret in ("SECRET-CLIENT-VALUE", b64u(KEY1), self.path, "id-123", "sqlite:///"):
            self.assertNotIn(secret, out + err)

    def test_unmigrated_database_fails(self):
        with self.db.transaction() as tx:
            tx.execute("DROP TABLE schema_migrations")
        code, out, _ = run(["doctor"], self.env())
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] schema", out)

    def test_config_problems_are_names_and_reasons_only(self):
        env = self.env(GEMARC_ACCOUNTS_KEYS="k1:" + b64u(b"\x05" * 32))
        del env["GEMARC_GOOGLE_CLIENT_SECRET"]
        code, out, _ = run(["doctor"], env)
        self.assertEqual(code, 1)
        self.assertIn("GEMARC_GOOGLE_CLIENT_SECRET: missing", out)
        self.assertIn("GEMARC_ACCOUNTS_KEYS: weak", out)
        self.assertNotIn(b64u(b"\x05" * 32), out)

    def test_not_enabled_is_info_not_failure(self):
        env = self.env()
        del env["GEMARC_ACCOUNTS"]
        code, out, _ = run(["doctor"], env)
        self.assertEqual(code, 0, out)
        self.assertIn("[info] GEMARC_ACCOUNTS is not enabled", out)

    def test_no_database_variable(self):
        env = self.env()
        del env["GEMARC_ACCOUNTS_DATABASE_URL"]
        code, out, _ = run(["doctor"], env)
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] database: no database URL variable", out)

    def test_unreachable_database_reports_code_and_class_only(self):
        env = self.env(GEMARC_ACCOUNTS_DATABASE_URL="sqlite:///" + os.path.join(self.tmp, "no", "dir", "x.db").replace("\\", "/"))
        code, out, _ = run(["doctor"], env)
        self.assertEqual(code, 1)
        self.assertIn("GEMARC_ACCT_002", out)
        self.assertNotIn(self.tmp.replace("\\", "/"), out)


class RekeyCli(DbCase):
    def test_dry_run_then_rekey(self):
        old = UserAccounts(self.db, self.crypto)
        for i in range(3):
            old.login(f"s{i}", f"u{i}@gmail.com", False, T0)
        env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
               "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1", "GEMARC_ACCOUNTS_KEYS": RING_ROTATED}
        code, out, _ = run(["rekey", "--dry-run"], env)
        self.assertEqual(code, 0)
        self.assertIn("pending 3, rekeyed 0, failed 0", out)
        self.assertEqual(self.count("users", "bidx_kid = 'k1'"), 3)
        code, out, _ = run(["rekey"], env)
        self.assertIn("pending 3, rekeyed 3, failed 0", out)
        self.assertIn("k2=3", out)
        self.assertIn("enc:k1=0", out)
        self.assertEqual(self.count("users", "bidx_kid = 'k2'"), 3)
        self.assertIn("pending 0, rekeyed 0", run(["rekey"], env)[1])

    def test_failed_rows_give_exit_code_one_and_missing_keys_are_an_error(self):
        UserAccounts(self.db, self.crypto).login("s", "u@gmail.com", False, T0)
        env = {"GEMARC_MIGRATE_DATABASE_URL": "sqlite:///" + self.path.replace("\\", "/"),
               "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1", "GEMARC_ACCOUNTS_KEYS": "k9:" + b64u(bytes(range(60, 92)))}
        self.assertEqual(run(["rekey"], env)[0], 1)
        del env["GEMARC_ACCOUNTS_KEYS"]
        self.assertEqual(run(["rekey"], env)[0], 2)


if __name__ == "__main__":
    unittest.main()
