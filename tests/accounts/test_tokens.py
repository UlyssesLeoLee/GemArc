"""tokens.py: format, checksum, storage, verify, cap under concurrency, scopes, revoke, throttle."""
from __future__ import annotations

import hashlib
import re
import threading
import unittest

from gemarc_mcp.accounts import tokens as K
from gemarc_mcp.accounts.crypto import b64u
from gemarc_mcp.accounts.db import Database
from gemarc_mcp.accounts.users import UserAccounts

from .support import T0, DbCase

DAY = 86400


class Format(unittest.TestCase):
    def test_shape_and_fixed_offsets(self):
        token, pid, secret = K.mint()
        self.assertEqual(len(token), 62)
        self.assertRegex(token, K.SCANNER_REGEX + r"$")
        self.assertEqual((token[:4], token[4:12], token[12], token[13:56]), ("gat_", pid, "_", secret))
        self.assertEqual(K.parse(token), (pid, secret))
        self.assertEqual(K.checksum(token[:56]), token[56:])
        self.assertEqual(len(token[56:]), 6)

    def test_secrets_containing_underscore_and_dash_parse_correctly(self):
        seen = set()
        for _ in range(3000):
            token, pid, secret = K.mint()
            if "_" in secret or "-" in secret:
                seen.add(("_" in secret, "-" in secret))
                self.assertEqual(K.parse(token), (pid, secret))
        self.assertTrue(any(u for u, d in seen))
        self.assertTrue(any(d for u, d in seen))

    def test_checksum_known_answer(self):
        import zlib

        head = "gat_abcdefgh_" + "A" * 43
        self.assertEqual(K.checksum(head), b64u(zlib.crc32(head.encode()).to_bytes(4, "big")))
        self.assertEqual(K.checksum("a" * 56), "eXkNNw")  # value cross-checked with binascii.crc32

    def test_every_single_character_change_is_rejected(self):
        token, _, _ = K.mint()
        for i in range(len(token)):
            for repl in ("a", "B", "_", "7"):
                if token[i] != repl:
                    self.assertIsNone(K.parse(token[:i] + repl + token[i + 1:]), (i, repl))

    def test_malformed_inputs(self):
        token, pid, secret = K.mint()
        for bad in (None, 5, b"x", "", token[:-1], token + "A", "gat-" + token[4:], "xat_" + token[4:],
                    token[:12] + "-" + token[13:], token.upper(), "gat_" + "A" * 8 + "_" + secret + token[56:],
                    token[:4] + "0" * 8 + token[12:], token[:-1] + "é"):
            self.assertIsNone(K.parse(bad), bad)

    def test_ids_use_the_lowercase_base32_alphabet_and_are_random(self):
        ids = {K.mint()[1] for _ in range(500)}
        self.assertEqual(len(ids), 500)
        for pid in ids:
            self.assertRegex(pid, r"^[a-z2-7]{8}$")

    def test_scanner_regex_matches_exactly_the_token_shape(self):
        token = K.mint()[0]
        self.assertRegex("key=" + token + " x", K.SCANNER_REGEX)
        self.assertIsNone(re.fullmatch(K.SCANNER_REGEX, token[:-1]))


class Tokens(DbCase):
    def setUp(self):
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.uid = self.users.login("sub-1", "one@gmail.com", False, T0).user_id
        self.store = K.TokenStore(self.db, self.crypto, K.TokenPolicy(default_days=30, max_days=90, max_per_user=10))

    def test_create_shows_plaintext_once_and_stores_only_the_hash(self):
        created = self.store.create(self.uid, "laptop", T0)
        self.assertEqual(created.scope, "read")
        self.assertEqual(created.expires_at, T0 + 30 * DAY)
        parsed = K.parse(created.token)
        self.assertEqual(parsed[0], created.id)
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT * FROM api_tokens WHERE id = ?", (created.id,))
        expect = b64u(hashlib.sha256(b"pat\x00" + parsed[1].encode()).digest())
        self.assertEqual(row["secret_hash"], expect)
        self.assertEqual((row["kind"], row["name"], row["revoked_at"], row["last_used_at"]), ("pat", "laptop", None, None))
        self.assertNotIn(created.token, self.dump())
        self.assertNotIn(parsed[1], self.dump())
        self.assertNotIn(parsed[1].encode(), self.raw_bytes())
        listed = self.store.list(self.uid)
        self.assertEqual(len(listed), 1)
        self.assertFalse(hasattr(listed[0], "secret_hash"))
        self.assertNotIn(created.token, repr(listed))
        self.assertNotIn(expect, repr(listed))

    def test_verify_ok_and_every_failure_is_not_ok(self):
        created = self.store.create(self.uid, "t", T0)
        ok = self.store.verify(created.token, T0 + 5)
        self.assertEqual((ok.ok, ok.reason, ok.token_id, ok.user_id, ok.scope), (True, "ok", created.id, self.uid, "read"))
        self.assertEqual(ok.expires_at, created.expires_at)
        pid, secret = K.parse(created.token)
        other_secret = b64u(bytes(32))
        forged = "gat_" + pid + "_" + other_secret
        forged += K.checksum(forged)
        unknown = K.mint()[0]
        cases = {
            "format": ["garbage", None, created.token[:-1]],
            "mismatch": [forged],
            "unknown": [unknown],
        }
        for reason, tokens in cases.items():
            for token in tokens:
                res = self.store.verify(token, T0 + 5)
                self.assertEqual((res.ok, res.reason), (False, reason), (reason, token))

    def test_expiry_boundary_and_max_lifetimes(self):
        created = self.store.create(self.uid, "t", T0, days=1)
        self.assertTrue(self.store.verify(created.token, T0 + DAY - 1).ok)
        res = self.store.verify(created.token, T0 + DAY)
        self.assertEqual((res.ok, res.reason), (False, "expired"))

    def test_lifetime_rules(self):
        self.assertEqual(self.store.create(self.uid, "d", T0).expires_at, T0 + 30 * DAY)
        self.assertEqual(self.store.create(self.uid, "m", T0, days=90).expires_at, T0 + 90 * DAY)
        for bad in (0, -1, 91, 1000, None.__class__, "30", 1.5, True):
            with self.assertRaises(K.TokenError) as ctx:
                self.store.create(self.uid, "x", T0, days=bad)
            self.assertEqual(ctx.exception.code, "GEMARC_ACCT_034", bad)
        wide = K.TokenStore(self.db, self.crypto, K.TokenPolicy(default_days=30, max_days=365))
        self.assertEqual(wide.create(self.uid, "y", T0, days=365).expires_at, T0 + 365 * DAY)
        with self.assertRaises(K.TokenError):
            wide.create(self.uid, "y", T0, days=366)

    def test_name_rules(self):
        for bad in ("", "   ", "x" * 41, None, 5, "a\nb", "a\x00b"):
            with self.assertRaises(K.TokenError) as ctx:
                self.store.create(self.uid, bad, T0)
            self.assertEqual(ctx.exception.code, "GEMARC_ACCT_034")
        self.assertEqual(self.store.list(self.uid), [])
        self.store.create(self.uid, "  ok name  ", T0)
        self.store.create(self.uid, "x" * 40, T0)
        self.assertEqual({m.name for m in self.store.list(self.uid)}, {"ok name", "x" * 40})

    def test_cap_counts_only_live_tokens(self):
        small = K.TokenStore(self.db, self.crypto, K.TokenPolicy(max_per_user=2))
        a = small.create(self.uid, "a", T0)
        small.create(self.uid, "b", T0)
        with self.assertRaises(K.TokenError) as ctx:
            small.create(self.uid, "c", T0)
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_033")
        small.revoke(self.uid, a.id, T0 + 1)
        small.create(self.uid, "c", T0 + 2)
        with self.assertRaises(K.TokenError):
            small.create(self.uid, "d", T0 + 3)
        other = self.users.login("sub-2", "two@gmail.com", False, T0).user_id
        small.create(other, "ok", T0)  # per user, not global

    def test_concurrent_creation_cannot_exceed_the_cap(self):
        made, refused, errors = [], [], []
        barrier = threading.Barrier(20)

        def go(i):
            try:
                store = K.TokenStore(Database.sqlite(self.path), self.crypto, K.TokenPolicy(max_per_user=10))
                barrier.wait()
                try:
                    made.append(store.create(self.uid, f"t{i}", T0).id)
                except K.TokenError as exc:
                    refused.append(exc.code)
            except Exception as exc:  # pragma: no cover
                errors.append(repr(exc))

        ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(made), 10)
        self.assertEqual(refused, ["GEMARC_ACCT_033"] * 10)
        self.assertEqual(self.count("api_tokens"), 10)
        self.assertEqual(len(set(made)), 10)

    def test_write_scope_needs_switch_and_user_permission(self):
        with self.assertRaises(K.TokenError) as ctx:
            self.store.create(self.uid, "w", T0, scope="write", allow_write=True)  # can_write is 0
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_035")
        self.users.set_can_write(self.uid, True, T0)
        with self.assertRaises(K.TokenError) as ctx:
            self.store.create(self.uid, "w", T0, scope="write", allow_write=False)  # operator switch off
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_035")
        created = self.store.create(self.uid, "w", T0, scope="write", allow_write=True)
        self.assertEqual(self.store.verify(created.token, T0 + 1).scope, "write")
        with self.assertRaises(K.TokenError) as ctx:
            self.store.create(self.uid, "w", T0, scope="admin")
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_034")
        self.assertEqual(self.store.create(self.uid, "r", T0, scope="read", allow_write=True).scope, "read")

    def test_effective_scope_is_min_of_token_and_user(self):
        self.users.set_can_write(self.uid, True, T0)
        created = self.store.create(self.uid, "w", T0, scope="write", allow_write=True)
        self.assertEqual(self.store.verify(created.token, T0 + 1).scope, "write")
        self.users.set_can_write(self.uid, False, T0 + 2)
        res = self.store.verify(created.token, T0 + 3)
        self.assertEqual((res.ok, res.scope), (True, "read"))

    def test_revoked_token_fails_and_other_users_cannot_revoke_it(self):
        created = self.store.create(self.uid, "t", T0)
        other = self.users.login("sub-2", "two@gmail.com", False, T0).user_id
        self.assertFalse(self.store.revoke(other, created.id, T0 + 1))
        self.assertTrue(self.store.verify(created.token, T0 + 2).ok)
        self.assertTrue(self.store.revoke(self.uid, created.id, T0 + 3))
        self.assertFalse(self.store.revoke(self.uid, created.id, T0 + 4))
        res = self.store.verify(created.token, T0 + 5)
        self.assertEqual((res.ok, res.reason), (False, "revoked"))
        self.assertEqual(self.store.list(self.uid)[0].state(T0 + 5), "revoked")
        self.assertEqual(self.count("audit_log", "event = 'token_revoked' AND ref = ?", (created.id,)), 1)

    def test_revoke_all(self):
        tokens = [self.store.create(self.uid, f"t{i}", T0) for i in range(3)]
        self.assertEqual(self.store.revoke_all(self.uid, T0 + 1), 3)
        self.assertEqual(self.store.revoke_all(self.uid, T0 + 2), 0)
        for t in tokens:
            self.assertFalse(self.store.verify(t.token, T0 + 3).ok)
        self.assertEqual(self.count("audit_log", "event = 'tokens_revoked_all'"), 2)

    def test_disabled_user_cannot_create_and_existing_tokens_fail(self):
        created = self.store.create(self.uid, "t", T0)
        with self.db.transaction() as tx:
            tx.execute("UPDATE users SET status = 'disabled' WHERE id = ?", (self.uid,))
        res = self.store.verify(created.token, T0 + 1)
        self.assertEqual((res.ok, res.reason), (False, "disabled"))
        with self.assertRaises(K.TokenError) as ctx:
            self.store.create(self.uid, "n", T0 + 2)
        self.assertEqual(ctx.exception.code, "GEMARC_ACCT_013")
        with self.assertRaises(K.TokenError):
            self.store.create("0" * 32, "n", T0 + 2)

    def test_policy_check_veto(self):
        created = self.store.create(self.uid, "t", T0)
        res = self.store.verify(created.token, T0 + 1, policy_check=lambda row: False)
        self.assertEqual((res.ok, res.reason), (False, "policy"))
        self.assertTrue(self.store.verify(created.token, T0 + 1, policy_check=lambda row: True).ok)

    def test_last_used_is_throttled_to_once_an_hour(self):
        created = self.store.create(self.uid, "t", T0)

        def last():
            return self.store.list(self.uid)[0].last_used_at

        self.assertIsNone(last())
        self.store.verify(created.token, T0 + 10)
        self.assertEqual(last(), T0 + 10)
        self.store.verify(created.token, T0 + 3609)
        self.assertEqual(last(), T0 + 10)
        self.store.verify(created.token, T0 + 3610)
        self.assertEqual(last(), T0 + 3610)

    def test_malformed_tokens_never_touch_the_database(self):
        class Boom(Database):
            def transaction(self):
                raise AssertionError("database touched")

        store = K.TokenStore(Boom(self.db.backend), self.crypto)
        for bad in ("", "x", None, "gat_" + "a" * 58, K.mint()[0][:-1]):
            self.assertEqual(store.verify(bad, T0).reason, "format")

    def test_database_errors_propagate_instead_of_becoming_a_verdict(self):
        from gemarc_mcp.accounts.db import DBUnavailable

        class Down(Database):
            def transaction(self):
                raise DBUnavailable("operational")

        token = K.mint()[0]
        with self.assertRaises(DBUnavailable):
            K.TokenStore(Down(self.db.backend), self.crypto).verify(token, T0)

    def test_tokens_cascade_with_user_delete_and_survive_unrelated_rows(self):
        created = self.store.create(self.uid, "t", T0)
        self.users.delete(self.uid, T0 + 1)
        self.assertEqual(self.count("api_tokens"), 0)
        self.assertEqual(self.store.verify(created.token, T0 + 2).reason, "unknown")

    def test_cleanup_removes_only_long_dead_tokens(self):
        live = self.store.create(self.uid, "live", T0)
        dead = self.store.create(self.uid, "dead", T0, days=1)
        revoked = self.store.create(self.uid, "rev", T0)
        self.store.revoke(self.uid, revoked.id, T0)
        self.assertEqual(self.store.cleanup(T0 + 20 * DAY), 0)
        self.assertEqual(self.store.cleanup(T0 + 31 * DAY + 1), 2)
        self.assertEqual([m.id for m in self.store.list(self.uid)], [live.id])
        del dead

    def test_audit_has_the_public_id_but_no_secret(self):
        created = self.store.create(self.uid, "t", T0)
        self.assertEqual(self.count("audit_log", "event = 'token_created' AND ref = ?", (created.id,)), 1)
        self.assertNotIn(K.parse(created.token)[1], self.dump())

    def test_policy_from_config(self):
        class Cfg:
            pat_default_days = 7
            pat_max_days = 14
            pat_max_per_user = 3

        p = K.TokenPolicy.from_config(Cfg)
        self.assertEqual((p.default_days, p.max_days, p.max_per_user), (7, 14, 3))

    def test_master_key_rotation_does_not_invalidate_tokens(self):
        from gemarc_mcp.accounts.crypto import Crypto, KeyRing

        from .support import KEY2

        created = self.store.create(self.uid, "t", T0)
        rotated = K.TokenStore(self.db, Crypto(KeyRing((("k2", KEY2),))))
        self.assertTrue(rotated.verify(created.token, T0 + 1).ok)


if __name__ == "__main__":
    unittest.main()
