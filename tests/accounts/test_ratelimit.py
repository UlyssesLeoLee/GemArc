"""ratelimit.py: atomic fixed windows, keyed storage, cleanup, per-user key removal."""
from __future__ import annotations

import threading
import unittest

from gemarc_mcp.accounts import ratelimit as R
from gemarc_mcp.accounts.db import Database

from .support import T0, DbCase, make_crypto


class RateLimit(DbCase):
    def setUp(self):
        super().setUp()
        self.rl = R.RateLimiter(self.db, self.crypto)

    def test_allows_up_to_the_limit_then_denies_with_retry_after(self):
        base = T0 - T0 % 600  # start of a 10 minute window
        for i in range(1, 4):
            res = self.rl.hit(base + 10, "x", "subject", limit=3, window=600)
            self.assertEqual((res.allowed, res.count, res.limit, res.retry_after), (True, i, 3, 0))
        res = self.rl.hit(base + 100, "x", "subject", limit=3, window=600)
        self.assertEqual((res.allowed, res.count, res.retry_after), (False, 4, 500))
        self.assertEqual(self.rl.hit(base + 599, "x", "subject", limit=3, window=600).retry_after, 1)

    def test_new_window_resets_and_subjects_and_scopes_are_independent(self):
        base = T0 - T0 % 600
        for _ in range(4):
            self.rl.hit(base, "x", "a", limit=3, window=600)
        self.assertTrue(self.rl.hit(base + 600, "x", "a", limit=3, window=600).allowed)
        self.assertTrue(self.rl.hit(base, "x", "b", limit=3, window=600).allowed)
        self.assertTrue(self.rl.hit(base, "y", "a", limit=3, window=600).allowed)

    def test_design_table_values(self):
        self.assertEqual(R.LIMITS, {
            "login_start": (20, 600), "login_callback": (30, 600), "signup_global": (60, 3600), "signup_ip": (3, 3600),
            "pat_create": (10, 3600), "pat_auth_fail": (30, 300), "account_post": (60, 600), "export": (5, 86400),
            "delete_attempt": (5, 86400)})
        results = [self.rl.hit(T0, "signup_ip", "ip").allowed for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])
        with self.assertRaises(ValueError):
            self.rl.hit(T0, "no_such_scope", "s")
        with self.assertRaises(ValueError):
            self.rl.hit(T0, "x", "s", limit=1, window=0)

    def test_table_holds_only_keyed_hashes(self):
        self.rl.hit(T0, "login_start", "203.0.113.7")
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT * FROM rate_limits")
        self.assertEqual(row["key_hash"], self.crypto.ratelimit_key("login_start", "203.0.113.7"))
        self.assertEqual(row["window_start"], T0 - T0 % 600)
        self.assertNotIn("203.0.113.7", self.dump())
        self.assertNotIn("login_start", self.dump())
        other = make_crypto(("k1", bytes(range(77, 109))))
        self.assertNotEqual(other.ratelimit_key("login_start", "203.0.113.7"), row["key_hash"])

    def test_concurrent_hits_are_counted_exactly(self):
        results, errors = [], []
        barrier = threading.Barrier(8)

        def go():
            try:
                limiter = R.RateLimiter(Database.sqlite(self.path), self.crypto)
                barrier.wait()
                for _ in range(5):
                    results.append(limiter.hit(T0, "x", "s", limit=25, window=3600))
            except Exception as exc:  # pragma: no cover
                errors.append(repr(exc))

        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r.count for r in results), list(range(1, 41)))
        self.assertEqual(sum(1 for r in results if r.allowed), 25)

    def test_cleanup_removes_only_old_windows_in_bounded_batches(self):
        for i in range(5):
            self.rl.hit(T0 - 3 * 86400 - i * 7200, "x", f"old{i}", limit=1, window=3600)
        self.rl.hit(T0, "x", "fresh", limit=1, window=3600)
        self.assertEqual(self.count("rate_limits"), 6)
        self.assertEqual(self.rl.cleanup(T0, limit=2), 2)
        self.assertEqual(self.rl.cleanup(T0, limit=200), 3)
        self.assertEqual(self.count("rate_limits"), 1)
        self.assertEqual(self.rl.cleanup(T0), 0)

    def test_delete_user_keys_removes_all_user_scopes_for_every_ring_key(self):
        from gemarc_mcp.accounts.crypto import Crypto, KeyRing

        from .support import KEY1, KEY2

        old = R.RateLimiter(self.db, self.crypto)
        for scope in R.USER_SCOPES:
            old.hit(T0, scope, "user-1")
        old.hit(T0, "export", "user-2")
        old.hit(T0, "login_start", "user-1")  # not a per-user scope: stays
        rotated = Crypto(KeyRing((("k2", KEY2), ("k1", KEY1))))
        R.RateLimiter(self.db, rotated).hit(T0, "export", "user-1")
        with self.db.transaction() as tx:
            removed = R.delete_user_keys(tx, rotated, "user-1")
        self.assertEqual(removed, 5)
        self.assertEqual(self.count("rate_limits"), 2)


if __name__ == "__main__":
    unittest.main()
