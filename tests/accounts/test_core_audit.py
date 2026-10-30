"""audit.py: closed event set, shape validation (no PII), dedup, hourly ceiling, retention purge."""
from __future__ import annotations

import unittest

from gemarc_mcp.accounts import audit as A

from .support import T0, DbCase

IP = "A" * 43


class Audit(DbCase):
    def setUp(self):
        super().setUp()
        self.audit = A.Audit(self.db, self.crypto, retention_days=30)
        from gemarc_mcp.accounts.users import UserAccounts

        self.uid = UserAccounts(self.db, self.crypto).login("s-audit", "audit@gmail.com", False, T0).user_id
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM audit_log")

    def rec(self, event, now=T0, **kw):
        with self.db.transaction() as tx:
            return A.record(tx, now, event, **kw)

    def test_every_documented_event_can_be_written_and_unknown_ones_cannot(self):
        for event in sorted(A.EVENTS):
            if event in A.ANONYMOUS_EVENTS:
                continue
            self.assertTrue(self.rec(event, code="x"), event)
        self.assertEqual(len(A.EVENTS), 25)
        with self.assertRaises(ValueError):
            self.rec("made_up")
        self.assertEqual(self.count("audit_log"), len(A.EVENTS) - len(A.ANONYMOUS_EVENTS))

    def test_fields_that_could_hold_pii_are_rejected(self):
        bad = [dict(ref="alice@gmail.com"), dict(ref="with space"), dict(ref="x" * 65), dict(code="Has Upper"),
               dict(code="a@b"), dict(user_id="alice@gmail.com"), dict(user_id="G" * 32), dict(ip_hash="203.0.113.7"),
               dict(ip_hash="short")]
        for kw in bad:
            with self.assertRaises(ValueError, msg=str(kw)):
                self.rec("login_ok", **kw)
        self.assertEqual(self.count("audit_log"), 0)
        self.assertTrue(self.rec("token_created", ref="k3x7q2mn", user_id=self.uid, ip_hash=IP, code="ok_1"))

    def test_table_has_no_pii_columns(self):
        with self.db.transaction() as tx:
            cols = [r["name"] for r in tx.fetchall("PRAGMA table_info(audit_log)")]
        self.assertEqual(cols, ["id", "ts", "event", "user_id", "ip_hash", "ref", "code"])

    def test_login_denied_dedup_per_ip_hash_for_ten_minutes(self):
        self.assertTrue(self.rec("login_denied", ip_hash=IP, code="domain"))
        self.assertFalse(self.rec("login_denied", T0 + 599, ip_hash=IP, code="state"))
        self.assertTrue(self.rec("login_denied", T0 + 600, ip_hash=IP, code="domain"))
        self.assertTrue(self.rec("login_denied", T0 + 1, ip_hash="B" * 43, code="domain"))
        self.assertEqual(self.count("audit_log", "event = 'login_denied'"), 3)

    def test_token_auth_failed_dedup_is_five_minutes_and_no_ip_has_its_own_bucket(self):
        self.assertTrue(self.rec("token_auth_failed", ip_hash=IP))
        self.assertFalse(self.rec("token_auth_failed", T0 + 299, ip_hash=IP))
        self.assertTrue(self.rec("token_auth_failed", T0 + 300, ip_hash=IP))
        self.assertTrue(self.rec("token_auth_failed", T0 + 400))
        self.assertFalse(self.rec("token_auth_failed", T0 + 500))
        self.assertTrue(self.rec("login_denied", T0))  # a different event has its own dedup key

    def test_non_anonymous_events_are_never_deduplicated(self):
        for _ in range(3):
            self.assertTrue(self.rec("login_ok", user_id=self.uid, ip_hash=IP))
        self.assertEqual(self.count("audit_log", "event = 'login_ok'"), 3)

    def test_hourly_global_ceiling_then_single_overflow_row(self):
        results = [self.rec("login_denied", T0 + i, ip_hash=f"{i:043d}", ceiling=5) for i in range(12)]
        self.assertEqual(results, [True] * 5 + [False] * 7)
        self.assertEqual(self.count("audit_log", "event = 'login_denied'"), 5)
        self.assertEqual(self.count("audit_log", "event = 'audit_overflow'"), 1)
        self.assertTrue(self.rec("login_ok", user_id=self.uid))  # non-anonymous events are unaffected
        later = T0 + 3601 + 5
        self.assertTrue(self.rec("login_denied", later, ip_hash="C" * 43, ceiling=5))
        flood = [self.rec("login_denied", later + i, ip_hash=f"{100 + i:043d}", ceiling=5) for i in range(8)]
        self.assertEqual(flood.count(True), 4)
        self.assertEqual(self.count("audit_log", "event = 'audit_overflow'"), 2)

    def test_wrapper_hashes_ip_with_a_daily_keyed_hash_and_never_stores_it_plain(self):
        self.assertTrue(self.audit.event(T0, "login_ok", ip="203.0.113.7", user_id=self.uid))
        self.assertTrue(self.audit.event(T0, "login_ok", ip="not an ip"))
        self.assertTrue(self.audit.event(T0, "logout", ip=None))
        with self.db.transaction() as tx:
            rows = tx.fetchall("SELECT ip_hash FROM audit_log ORDER BY rowid")
        self.assertEqual(rows[0]["ip_hash"], self.crypto.ip_hash("203.0.113.7", T0))
        self.assertIsNone(rows[1]["ip_hash"])
        self.assertIsNone(rows[2]["ip_hash"])
        self.assertNotIn("203.0.113.7", self.dump())

    def test_purge_is_bounded_and_respects_retention(self):
        for i in range(5):
            self.rec("logout", T0 - 40 * 86400 - i)
        self.rec("logout", T0 - 29 * 86400)
        self.rec("logout", T0)
        self.assertEqual(self.audit.purge(T0, limit=3), 3)
        self.assertEqual(self.audit.purge(T0), 2)
        self.assertEqual(self.count("audit_log"), 2)
        self.assertEqual(self.audit.purge(T0), 0)

    def test_deleting_a_user_nulls_the_reference(self):
        from gemarc_mcp.accounts.users import UserAccounts

        uid = UserAccounts(self.db, self.crypto).login("s", "a@gmail.com", False, T0).user_id
        self.rec("login_ok", user_id=uid, ip_hash=IP)
        UserAccounts(self.db, self.crypto).delete(uid, T0 + 1)
        self.assertEqual(self.count("audit_log", "user_id IS NOT NULL"), 0)
        self.assertEqual(self.count("audit_log", "ip_hash IS NOT NULL"), 0)


if __name__ == "__main__":
    unittest.main()
