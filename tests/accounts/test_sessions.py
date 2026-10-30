"""sessions.py: ids, hashing, expiry, rotation, caps, revoke, no-DB paths."""
from __future__ import annotations

import hashlib
import unittest

from gemarc_mcp.accounts import sessions as S
from gemarc_mcp.accounts.crypto import b64u
from gemarc_mcp.accounts.db import Database
from gemarc_mcp.accounts.users import UserAccounts

from .support import T0, DbCase


class CountingDb(Database):
    def __init__(self, inner):
        super().__init__(inner.backend)
        self.opened = 0

    def transaction(self):
        self.opened += 1
        return super().transaction()


class Sessions(DbCase):
    def setUp(self):
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.uid = self.users.login("sub-1", "one@gmail.com", False, T0).user_id
        self.policy = S.SessionPolicy(idle_seconds=1800, absolute_seconds=8 * 3600)
        self.store = S.SessionStore(self.db, self.crypto, self.policy)

    def test_sid_is_256_bit_base64url_and_only_its_hash_is_stored(self):
        created = self.store.create(self.uid, T0)
        self.assertRegex(created.sid, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(len(__import__("base64").urlsafe_b64decode(created.sid + "=")), 32)
        self.assertNotEqual(created.sid, self.store.create(self.uid, T0).sid)
        expect = b64u(hashlib.sha256(b"session\x00" + created.sid.encode()).digest())
        self.assertEqual(S.hash_sid(created.sid), expect)
        with self.db.transaction() as tx:
            hashes = {r["id_hash"] for r in tx.fetchall("SELECT id_hash FROM sessions")}
        self.assertIn(expect, hashes)
        self.assertNotIn(created.sid, self.dump())
        self.assertNotIn(created.sid.encode(), self.raw_bytes())

    def test_lookup_returns_the_user_and_rejects_everything_else(self):
        sid = self.store.create(self.uid, T0).sid
        info = self.store.lookup(sid, T0 + 1)
        self.assertEqual((info.user_id, info.created_at, info.can_write), (self.uid, T0, False))
        for bad in (None, "", "short", sid[:-1], sid + "A", sid.replace(sid[0], "!", 1), 12345, b"x" * 43, S.new_sid()):
            self.assertIsNone(self.store.lookup(bad, T0 + 1), bad)

    def test_malformed_or_missing_cookie_causes_no_database_access(self):
        counting = CountingDb(self.db)
        store = S.SessionStore(counting, self.crypto, self.policy)
        for bad in (None, "", "short", "!" * 43, "A" * 44, 5):
            self.assertIsNone(store.lookup(bad, T0))
            self.assertFalse(store.revoke(bad))
        self.assertEqual(counting.opened, 0)
        store.lookup(S.new_sid(), T0)  # well formed but unknown: one lookup, no new id issued
        self.assertEqual(counting.opened, 1)
        self.assertEqual(self.count("sessions"), 0)

    def test_idle_timeout_and_touch(self):
        sid = self.store.create(self.uid, T0).sid
        self.assertIsNotNone(self.store.lookup(sid, T0 + 1799))
        self.assertIsNone(self.store.lookup(sid, T0 + 1800 + 1800))  # idle expired relative to the last touch
        sid2 = self.store.create(self.uid, T0).sid
        t = T0
        for _ in range(5):  # keep it alive by activity every 20 minutes
            t += 1200
            self.assertIsNotNone(self.store.lookup(sid2, t))
        self.assertIsNone(self.store.lookup(sid2, t + 1801))

    def test_absolute_timeout_even_with_activity(self):
        sid = self.store.create(self.uid, T0).sid
        t = T0
        while t < T0 + 8 * 3600 - 600:
            t += 600
            self.assertIsNotNone(self.store.lookup(sid, t), t)
        self.assertIsNone(self.store.lookup(sid, T0 + 8 * 3600))
        self.assertIsNone(self.store.lookup(sid, T0 + 8 * 3600 + 5))

    def test_last_seen_write_is_throttled(self):
        sid = self.store.create(self.uid, T0).sid

        def last_seen():
            with self.db.transaction() as tx:
                return tx.fetchone("SELECT last_seen_at FROM sessions WHERE id_hash = ?", (S.hash_sid(sid),))["last_seen_at"]

        self.store.lookup(sid, T0 + 100)
        self.assertEqual(last_seen(), T0)
        self.store.lookup(sid, T0 + 299)
        self.assertEqual(last_seen(), T0)
        self.store.lookup(sid, T0 + 300)
        self.assertEqual(last_seen(), T0 + 300)

    def test_short_idle_limit_does_not_expire_an_active_user_early(self):
        store = S.SessionStore(self.db, self.crypto, S.SessionPolicy(idle_seconds=300))
        sid = store.create(self.uid, T0).sid
        for t in (T0 + 140, T0 + 280, T0 + 420, T0 + 560):
            self.assertIsNotNone(store.lookup(sid, t), t)

    def test_rotation_revokes_the_presented_session(self):
        old = self.store.create(self.uid, T0).sid
        res = self.store.create(self.uid, T0 + 10, replace_sid=old)
        self.assertTrue(res.replaced)
        self.assertIsNone(self.store.lookup(old, T0 + 11))
        self.assertIsNotNone(self.store.lookup(res.sid, T0 + 11))
        self.assertFalse(self.store.create(self.uid, T0 + 12, replace_sid="garbage").replaced)
        self.assertFalse(self.store.create(self.uid, T0 + 13, replace_sid=S.new_sid()).replaced)

    def test_concurrent_limit_five_evicts_oldest_never_the_new_one(self):
        sids = [self.store.create(self.uid, T0 + i).sid for i in range(7)]
        self.assertEqual(self.count("sessions", "user_id = ?", (self.uid,)), 5)
        self.assertIsNone(self.store.lookup(sids[0], T0 + 7))
        self.assertIsNone(self.store.lookup(sids[1], T0 + 7))
        for sid in sids[2:]:
            self.assertIsNotNone(self.store.lookup(sid, T0 + 7))
        same_second = [self.store.create(self.uid, T0 + 100).sid for _ in range(8)]
        self.assertIsNotNone(self.store.lookup(same_second[-1], T0 + 101))
        self.assertEqual(self.count("sessions", "user_id = ?", (self.uid,)), 5)
        self.assertGreaterEqual(self.count("audit_log", "event = 'session_evicted'"), 1)

    def test_revoke_and_revoke_all(self):
        a, b = self.store.create(self.uid, T0).sid, self.store.create(self.uid, T0).sid
        other = self.users.login("sub-2", "two@gmail.com", False, T0).user_id
        c = self.store.create(other, T0).sid
        self.assertTrue(self.store.revoke(a))
        self.assertFalse(self.store.revoke(a))
        self.assertIsNone(self.store.lookup(a, T0 + 1))
        self.assertEqual(self.store.revoke_all(self.uid, T0 + 2), 1)
        self.assertIsNone(self.store.lookup(b, T0 + 3))
        self.assertIsNotNone(self.store.lookup(c, T0 + 3))
        self.assertEqual(self.count("audit_log", "event = 'sessions_revoked_all'"), 1)

    def test_disabled_user_cannot_create_or_use_a_session(self):
        sid = self.store.create(self.uid, T0).sid
        with self.db.transaction() as tx:
            tx.execute("UPDATE users SET status = 'disabled' WHERE id = ?", (self.uid,))
        self.assertIsNone(self.store.lookup(sid, T0 + 1))
        with self.assertRaises(S.SessionError):
            self.store.create(self.uid, T0 + 2)
        with self.assertRaises(S.SessionError):
            self.store.create("0" * 32, T0)

    def test_policy_check_can_veto_without_extra_query(self):
        sid = self.store.create(self.uid, T0).sid
        seen = []

        def veto(row):
            seen.append(set(row))
            return False

        self.assertIsNone(self.store.lookup(sid, T0 + 1, policy_check=veto))
        self.assertTrue({"email_bidx", "domain_bidx", "has_hd", "bidx_kid"} <= seen[0])
        self.assertIsNotNone(self.store.lookup(sid, T0 + 1, policy_check=lambda row: True))

    def test_recent_login_window_is_fifteen_minutes(self):
        info = self.store.lookup(self.store.create(self.uid, T0).sid, T0)
        self.assertTrue(self.store.is_recent(info, T0))
        self.assertTrue(self.store.is_recent(info, T0 + 900))
        self.assertFalse(self.store.is_recent(info, T0 + 901))

    def test_cascade_when_user_is_deleted_and_cleanup_of_expired(self):
        sid = self.store.create(self.uid, T0).sid
        self.store.cleanup(T0 + 9 * 3600)  # expired less than a day ago: kept
        self.assertEqual(self.count("sessions"), 1)
        self.assertEqual(self.store.cleanup(T0 + 8 * 3600 + 86400 + 1), 1)
        self.assertEqual(self.count("sessions"), 0)
        sid = self.store.create(self.uid, T0).sid
        self.users.delete(self.uid, T0 + 1)
        self.assertEqual(self.count("sessions"), 0)
        self.assertIsNone(self.store.lookup(sid, T0 + 2))

    def test_policy_from_config(self):
        class Cfg:
            session_idle_min = 10
            session_max_hours = 2

        p = S.SessionPolicy.from_config(Cfg)
        self.assertEqual((p.idle_seconds, p.absolute_seconds, p.max_per_user), (600, 7200, 5))


if __name__ == "__main__":
    unittest.main()
