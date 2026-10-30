"""users.py: upsert by sub, encryption at rest, status, delete cascade, export, rekey, and the full-dump leak scan."""
from __future__ import annotations

import threading
import unittest

from gemarc_mcp.accounts import audit, ratelimit
from gemarc_mcp.accounts.crypto import Crypto, KeyRing, b64u, normalize_email
from gemarc_mcp.accounts.db import Database
from gemarc_mcp.accounts.sessions import SessionStore
from gemarc_mcp.accounts.tokens import TokenStore
from gemarc_mcp.accounts.users import UserAccounts

from .support import KEY1, KEY2, T0, DbCase, make_crypto

SUB = "110169484474386276334"
EMAIL = "Alice.Example@Gmail.com"
SENTINEL_NAME = "SENTINEL-DISPLAY-NAME-Alice Example"


class Users(DbCase):
    def setUp(self):
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)

    def login(self, sub=SUB, email=EMAIL, hd=False, now=T0, **kw):
        return self.users.login(sub, email, hd, now, **kw)

    def test_first_login_registers_and_second_does_not_duplicate(self):
        first = self.login()
        self.assertEqual((first.status, first.created), ("active", True))
        again = self.login(now=T0 + 60)
        self.assertEqual((again.status, again.created, again.user_id), ("active", False, first.user_id))
        self.assertEqual(self.count("users"), 1)
        self.assertEqual(self.users.get(first.user_id).last_login_at, T0 + 60)
        self.assertEqual(self.count("audit_log", "event = ?", ("user_created",)), 1)

    def test_email_and_sub_are_encrypted_and_normalised(self):
        uid = self.login().user_id
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT * FROM users WHERE id = ?", (uid,))
        self.assertTrue(row["email_enc"].startswith("v1.k1."))
        self.assertTrue(row["sub_enc"].startswith("v1.k1."))
        self.assertNotIn("alice", row["email_enc"].lower())
        self.assertEqual(self.users.email_of(uid), "alice.example@gmail.com")
        self.assertEqual(row["email_bidx"], self.crypto.blind_index("email", "alice.example@gmail.com"))
        self.assertEqual(row["domain_bidx"], self.crypto.blind_index("domain", "gmail.com"))
        self.assertEqual(row["sub_bidx"], self.crypto.blind_index("sub", SUB))
        self.assertEqual((row["has_hd"], row["can_write"], row["status"], row["bidx_kid"]), (0, 0, "active", "k1"))

    def test_no_password_or_name_column_exists(self):
        with self.db.transaction() as tx:
            cols = {r["name"] for r in tx.fetchall("PRAGMA table_info(users)")}
        self.assertEqual(cols, {"id", "sub_bidx", "sub_enc", "email_bidx", "email_enc", "domain_bidx", "has_hd",
                                "can_write", "bidx_kid", "status", "created_at", "last_login_at"})

    def test_email_change_keeps_account_and_audits(self):
        uid = self.login().user_id
        res = self.login(email="alice.new@gmail.com", now=T0 + 5)
        self.assertEqual((res.user_id, res.email_changed), (uid, True))
        self.assertEqual(self.users.email_of(uid), "alice.new@gmail.com")
        self.assertEqual(self.count("audit_log", "event = ?", ("email_changed",)), 1)
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchone("SELECT email_bidx FROM users WHERE id = ?", (uid,))["email_bidx"],
                             self.crypto.blind_index("email", "alice.new@gmail.com"))

    def test_email_change_colliding_with_another_row_still_succeeds(self):
        self.login(sub="s-other", email="shared@gmail.com")
        uid = self.login(sub="s-me", email="me@gmail.com").user_id
        res = self.login(sub="s-me", email="shared@gmail.com", now=T0 + 1)
        self.assertEqual((res.status, res.email_changed, res.user_id), ("active", True, uid))
        self.assertEqual(self.count("users"), 2)

    def test_email_recycled_new_sub_disables_old_row_and_revokes_everything(self):
        old = self.login(sub="s-old", email="reused@gmail.com").user_id
        sessions = SessionStore(self.db, self.crypto)
        tokens = TokenStore(self.db, self.crypto)
        sid = sessions.create(old, T0).sid
        tok = tokens.create(old, "ci", T0)
        new = self.login(sub="s-new", email="Reused@gmail.com", now=T0 + 10)
        self.assertEqual((new.created, new.recycled), (True, (old,)))
        self.assertNotEqual(new.user_id, old)
        self.assertEqual(self.users.get(old).status, "disabled")
        self.assertIsNone(sessions.lookup(sid, T0 + 11))
        self.assertFalse(tokens.verify(tok.token, T0 + 11).ok)
        self.assertEqual(self.count("audit_log", "event = ?", ("email_recycled",)), 1)
        self.assertEqual(self.users.get(new.user_id).status, "active")

    def test_disabled_user_login_is_denied_without_touching_last_login(self):
        uid = self.login().user_id
        self.assertTrue(self.users.disable(uid, T0 + 1))
        res = self.login(now=T0 + 99)
        self.assertEqual((res.status, res.user_id), ("disabled", uid))
        self.assertEqual(self.users.get(uid).last_login_at, T0)

    def test_disable_deletes_sessions_and_revokes_tokens_in_one_go_and_enable_does_not_restore(self):
        uid = self.login().user_id
        sessions = SessionStore(self.db, self.crypto)
        tokens = TokenStore(self.db, self.crypto)
        sessions.create(uid, T0)
        tok = tokens.create(uid, "t", T0)
        self.users.disable(uid, T0 + 1)
        self.assertEqual(self.count("sessions"), 0)
        self.assertEqual(self.count("api_tokens", "revoked_at IS NOT NULL"), 1)
        self.users.enable(uid, T0 + 2)
        self.assertEqual(self.users.get(uid).status, "active")
        self.assertFalse(tokens.verify(tok.token, T0 + 3).ok)  # revoked tokens stay revoked
        self.assertFalse(self.users.disable("0" * 32, T0))
        self.assertFalse(self.users.enable("0" * 32, T0))

    def test_max_users_blocks_new_registrations_only(self):
        a = self.login(sub="a", email="a@gmail.com", max_users=1)
        self.assertEqual(a.status, "active")
        b = self.login(sub="b", email="b@gmail.com", max_users=1)
        self.assertEqual((b.status, b.user_id), ("closed", None))
        self.assertEqual(self.count("users"), 1)
        self.assertEqual(self.login(sub="a", email="a@gmail.com", max_users=1).status, "active")
        self.assertEqual(self.login(sub="c", email="c@gmail.com", max_users=0).status, "active")

    def test_has_hd_is_stored_as_one_bit(self):
        uid = self.login(hd=True).user_id
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchone("SELECT has_hd FROM users WHERE id = ?", (uid,))["has_hd"], 1)
        self.login(hd=False, now=T0 + 1)
        with self.db.transaction() as tx:
            self.assertEqual(tx.fetchone("SELECT has_hd FROM users WHERE id = ?", (uid,))["has_hd"], 0)

    def test_invalid_identity_inputs(self):
        for sub, email in (("", "a@gmail.com"), ("s", "no-at"), ("s", "a@b@c.com"), ("s", "@gmail.com"), ("s" * 256, "a@gmail.com")):
            with self.assertRaises(ValueError):
                self.users.login(sub, email, False, T0)

    def test_concurrent_same_sub_logins_create_exactly_one_user(self):
        errors, results = [], []
        barrier = threading.Barrier(8)

        def go():
            try:
                barrier.wait()
                results.append(UserAccounts(Database.sqlite(self.path), self.crypto).login(SUB, EMAIL, False, T0))
            except Exception as exc:  # pragma: no cover
                errors.append(repr(exc))

        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(self.count("users"), 1)
        self.assertEqual(sum(1 for r in results if r.created), 1)
        self.assertEqual(len({r.user_id for r in results}), 1)

    def test_set_can_write_and_audit(self):
        uid = self.login().user_id
        self.assertTrue(self.users.set_can_write(uid, True, T0))
        self.assertTrue(self.users.get(uid).can_write)
        self.assertTrue(self.users.set_can_write(uid, False, T0))
        self.assertFalse(self.users.get(uid).can_write)
        self.assertEqual(self.count("audit_log", "event IN ('write_granted', 'write_revoked')"), 2)
        self.assertFalse(self.users.set_can_write("0" * 32, True, T0))


class DeleteAndExport(DbCase):
    def setUp(self):
        super().setUp()
        self.users = UserAccounts(self.db, self.crypto)
        self.uid = self.users.login(SUB, EMAIL, False, T0).user_id
        self.other = self.users.login("other-sub", "other@gmail.com", False, T0).user_id
        self.sessions = SessionStore(self.db, self.crypto)
        self.tokens = TokenStore(self.db, self.crypto)
        self.sessions.create(self.uid, T0)
        self.tokens.create(self.uid, "mine", T0)
        self.limiter = ratelimit.RateLimiter(self.db, self.crypto)
        self.limiter.hit(T0, "pat_create", self.uid)
        self.limiter.hit(T0, "export", self.other)
        self.ip = self.crypto.ip_hash("203.0.113.9", T0)
        with self.db.transaction() as tx:
            audit.record(tx, T0, "login_ok", user_id=self.uid, ip_hash=self.ip)

    def test_delete_is_hard_cascades_anonymises_and_removes_counters(self):
        self.assertTrue(self.users.delete(self.uid, T0 + 5))
        self.assertIsNone(self.users.get(self.uid))
        self.assertEqual(self.count("sessions"), 0)
        self.assertEqual(self.count("api_tokens"), 0)
        self.assertEqual(self.count("audit_log", "user_id = ?", (self.uid,)), 0)
        self.assertEqual(self.count("audit_log", "ip_hash IS NOT NULL"), 0)
        self.assertGreaterEqual(self.count("audit_log", "event = 'login_ok' AND user_id IS NULL"), 1)
        deleted = self.count("audit_log", "event = 'account_deleted' AND user_id IS NULL AND ip_hash IS NULL")
        self.assertEqual(deleted, 1)
        self.assertEqual(self.count("rate_limits"), 1)  # only the OTHER user's counter survives
        self.assertNotIn(self.uid, self.dump())
        self.assertIsNotNone(self.users.get(self.other))
        self.assertFalse(self.users.delete(self.uid, T0 + 6))

    def test_relogin_after_delete_creates_a_new_account(self):
        self.users.delete(self.uid, T0 + 5)
        again = self.users.login(SUB, EMAIL, False, T0 + 9)
        self.assertTrue(again.created)
        self.assertNotEqual(again.user_id, self.uid)

    def test_export_content_and_no_credentials(self):
        data = self.users.export(self.uid, T0 + 10)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["profile"], {"email": normalize_email(EMAIL)})
        self.assertEqual(data["exportedAt"], T0 + 10)
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(set(data["sessions"][0]), {"createdAt", "lastSeenAt", "expiresAt"})
        self.assertEqual(set(data["tokens"][0]), {"id", "name", "scope", "createdAt", "expiresAt", "lastUsedAt", "revokedAt"})
        for entry in data["audit"]:
            self.assertEqual(set(entry), {"ts", "event", "code", "ref"})
        text = str(data)
        for forbidden in (self.ip, "secret_hash", "id_hash", "ip_hash", "sub_bidx", "email_bidx"):
            self.assertNotIn(forbidden, text)
        self.assertIsNone(self.users.export("0" * 32, T0))
        self.assertEqual(self.count("audit_log", "event = 'export' AND user_id = ?", (self.uid,)), 1)


class Rekey(DbCase):
    def setUp(self):
        super().setUp()
        self.old = UserAccounts(self.db, self.crypto)
        self.ids = [self.old.login(f"sub-{i}", f"user{i}@gmail.com", False, T0).user_id for i in range(5)]
        self.rotated = Crypto(KeyRing((("k2", KEY2), ("k1", KEY1))))
        self.new = UserAccounts(self.db, self.rotated)

    def test_rotation_login_still_works_before_rekey_and_migrates_that_row(self):
        res = self.new.login("sub-0", "user0@gmail.com", False, T0 + 1)
        self.assertEqual((res.created, res.user_id), (False, self.ids[0]))
        with self.db.transaction() as tx:
            row = tx.fetchone("SELECT bidx_kid, email_enc FROM users WHERE id = ?", (self.ids[0],))
        self.assertEqual(row["bidx_kid"], "k2")
        self.assertTrue(row["email_enc"].startswith("v1.k2."))

    def test_dry_run_changes_nothing_then_rekey_moves_all_rows_and_is_idempotent(self):
        before = self.dump()
        report = self.new.rekey(dry_run=True)
        self.assertEqual((report.pending, report.rekeyed, report.failed, report.dry_run), (5, 0, 0, True))
        self.assertEqual(self.dump(), before)
        report = self.new.rekey(batch=2, now=T0 + 2)
        self.assertEqual((report.pending, report.rekeyed, report.failed), (5, 5, 0))
        refs = self.new.key_references()
        self.assertEqual(refs.get("k2"), 5)
        self.assertNotIn("k1", refs)
        self.assertEqual(refs["enc:k1"], 0)
        self.assertEqual(refs["enc:k2"], 5)
        again = self.new.rekey()
        self.assertEqual((again.pending, again.rekeyed), (0, 0))
        # old key removed: everything still decrypts and logs in
        only_new = UserAccounts(self.db, Crypto(KeyRing((("k2", KEY2),))))
        self.assertEqual(only_new.email_of(self.ids[3]), "user3@gmail.com")
        self.assertEqual(only_new.login("sub-3", "user3@gmail.com", False, T0 + 3).user_id, self.ids[3])
        self.assertEqual(self.count("audit_log", "event = 'key_rekeyed'"), 3)

    def test_unknown_key_rows_are_counted_failed_and_skipped_without_looping(self):
        lonely = UserAccounts(self.db, Crypto(KeyRing((("k3", bytes(range(50, 82))),))))
        report = lonely.rekey(batch=2)
        self.assertEqual((report.pending, report.rekeyed, report.failed), (5, 0, 5))

    def test_sessions_and_tokens_survive_key_rotation(self):
        uid = self.ids[1]
        sid = SessionStore(self.db, self.crypto).create(uid, T0).sid
        tok = TokenStore(self.db, self.crypto).create(uid, "t", T0).token
        self.new.rekey(now=T0 + 1)
        final = Crypto(KeyRing((("k2", KEY2),)))
        self.assertIsNotNone(SessionStore(self.db, final).lookup(sid, T0 + 2))
        self.assertTrue(TokenStore(self.db, final).verify(tok, T0 + 2).ok)


class DumpContainsNoSecrets(DbCase):
    """Acceptance: a full dump of ALL tables has no plaintext email, name, token or session id; keyed values cannot
    be recomputed without the keys."""

    def test_full_dump(self):
        users = UserAccounts(self.db, self.crypto)
        res = users.login(SUB, EMAIL, True, T0)
        sessions = SessionStore(self.db, self.crypto)
        tokens = TokenStore(self.db, self.crypto)
        sid = sessions.create(res.user_id, T0).sid
        created = tokens.create(res.user_id, "laptop", T0)
        limiter = ratelimit.RateLimiter(self.db, self.crypto)
        limiter.hit(T0, "login_start", self.crypto.canonical_ip("203.0.113.77"))
        ip = "203.0.113.77"
        with self.db.transaction() as tx:
            audit.record(tx, T0, "login_denied", ip_hash=self.crypto.ip_hash(ip, T0), code="domain")
            audit.record(tx, T0, "login_ok", user_id=res.user_id, ip_hash=self.crypto.ip_hash(ip, T0))
        secret = created.token[13:56]
        dump = self.dump()
        raw = self.raw_bytes()
        self.assertGreater(len(dump), 500)
        sensitive = [SUB, EMAIL, EMAIL.lower(), "alice.example", "gmail.com", SENTINEL_NAME, "Alice", sid, created.token,
                     secret, ip, "203.0.113", "laptop-secret"]
        for needle in sensitive:
            if needle == "laptop-secret":
                continue
            self.assertNotIn(needle, dump, needle)
            self.assertNotIn(needle.encode(), raw, needle)
        # the PAT name is a user label (C1), it is stored but is not a secret
        self.assertIn("laptop", dump)
        # keyed values cannot be recomputed without the keys
        wrong = make_crypto(("k1", bytes(range(200, 232))))
        stored = {r for r in dump.replace("|", "\n").split("\n")}
        self.assertIn(self.crypto.blind_index("sub", SUB), stored)
        self.assertNotIn(wrong.blind_index("sub", SUB), stored)
        self.assertNotIn(wrong.blind_index("email", EMAIL.lower()), stored)
        self.assertNotIn(wrong.ip_hash(ip, T0), stored)
        self.assertNotIn(wrong.ratelimit_key("login_start", ip), stored)
        # unkeyed digests of guessed inputs do not match either (blind index is keyed, not a plain hash)
        import hashlib

        for guess in (SUB, EMAIL.lower()):
            self.assertNotIn(b64u(hashlib.sha256(("sub\x00" + guess).encode()).digest()), stored)
            self.assertNotIn(b64u(hashlib.sha256(("email\x00" + guess).encode()).digest()), stored)
        # session / PAT hashes are unkeyed SHA-256 of 256-bit random input: stored digest != plaintext, and it is
        # not the hash of anything else a dump reader could guess
        self.assertNotIn(sid, stored)
        self.assertEqual(self.count("sessions"), 1)
        self.assertIn(b64u(hashlib.sha256(b"session\x00" + sid.encode()).digest()), stored)
        self.assertNotIn(b64u(hashlib.sha256(b"pat\x00" + sid.encode()).digest()), stored)


if __name__ == "__main__":
    unittest.main()
