"""Known-answer, tamper and property tests for gemarc_mcp.accounts.crypto."""
from __future__ import annotations

import hashlib
import unittest

from gemarc_mcp.accounts import crypto as C

KEY_A = bytes(range(32))
KEY_B = bytes(range(32, 64))
ROW = "a" * 32


def ring(*pairs):
    return C.KeyRing(pairs)


def crypto_a():
    return C.Crypto(ring(("k1", KEY_A)))


class KnownAnswers(unittest.TestCase):
    def test_hkdf_rfc5869_case1(self):
        okm = C.hkdf_sha256(bytes.fromhex("0b" * 22), bytes.fromhex("000102030405060708090a0b0c"),
                            bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), 42)
        self.assertEqual(okm.hex(), "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")

    def test_hkdf_matches_cryptography_library(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        for purpose in C.PURPOSES:
            info = C.HKDF_INFO_PREFIX + purpose.encode()
            ref = HKDF(hashes.SHA256(), 32, C.HKDF_SALT, info).derive(KEY_A)
            self.assertEqual(C.Crypto(ring(("k1", KEY_A))).subkey("k1", purpose), ref)

    def test_subkeys_are_pairwise_distinct_and_differ_from_master(self):
        c = crypto_a()
        subs = {c.subkey("k1", p) for p in C.PURPOSES}
        self.assertEqual(len(subs), len(C.PURPOSES))
        self.assertNotIn(KEY_A, subs)

    def test_aes_gcm_nist_case14(self):
        sealed = C._seal(bytes(32), bytes(12), bytes(16), b"")
        self.assertEqual(sealed[:16].hex(), "cea7403d4d606b6e074ec5d3baf39d18")
        self.assertEqual(sealed[16:].hex(), "d0d1c8a799996bf0265b98b5d48ab919")

    def test_aes_gcm_nist_case16_with_aad(self):
        key = bytes.fromhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
        iv = bytes.fromhex("cafebabefacedbaddecaf888")
        pt = bytes.fromhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
                           "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        sealed = C._seal(key, iv, pt, aad)
        self.assertEqual(sealed[-16:].hex(), "76fc6ece0f4e1768cddf8853bb2d551b")
        self.assertEqual(sealed[:16].hex(), "522dc1f099567d07f47f37a32a84427d")
        self.assertEqual(C._open(key, iv, sealed, aad), pt)

    # The following are regression vectors produced by this implementation (not independent vectors).
    def test_domain_hash_vectors_and_independent_recompute(self):
        self.assertEqual(C.sha256_domain("session", "abc"), "y2EoelmYfrDTtjV-W0iUSrve4pb0vzZLb6eKa17iLnc")
        self.assertEqual(C.sha256_domain("pat", "abc"), "qAV4SZ2K6B8Q1o73mt4U7qkkDSc9u9Y4Nxzku9qyOS0")
        for d in sorted(C.HASH_DOMAINS):
            expect = C.b64u(hashlib.sha256(d.encode() + b"\x00" + b"abc").digest())
            self.assertEqual(C.sha256_domain(d, "abc"), expect)

    def test_domain_prefixes_are_pairwise_different(self):
        outs = {C.sha256_domain(d, "same") for d in C.HASH_DOMAINS}
        self.assertEqual(len(outs), len(C.HASH_DOMAINS))
        with self.assertRaises(ValueError):
            C.sha256_domain("other", "x")

    def test_blind_index_and_keyed_values_vectors(self):
        c = crypto_a()
        self.assertEqual(c.blind_index("email", "a@gmail.com"), "bUBUaIrvtCMV6TsmJS4MBYJ6NLWiEKotw4yaREDqLS0")
        self.assertEqual(c.csrf_token("sid"), "3WGI_twPBklEm3Mnv13nw4ew2HWSI2MGDtfgXhByOJE")
        self.assertEqual(c.ip_hash("203.0.113.7", 0), "EcA1FCeb5rYQ9EUeiUowvvFsymvthAWai4ySwEtjOKo")
        self.assertEqual(c.ratelimit_key("login_start", "x"), "LVRvrfkNFUWEwjnzEhSb4yColN4O7U9jE6GOWGe_DuY")  # length-prefixed scope (acct-fix); was NUL-joined

    def test_envelope_known_answer_with_fixed_nonce(self):
        c = crypto_a()
        sealed = C._seal(c.subkey("k1", "field-enc"), bytes(range(12)), b"user@gmail.com",
                         C.field_aad("users", "email_enc", ROW))
        self.assertEqual(C.b64u(sealed), "cQCoj_RVQr3YRF1f0MN32HoTGtR_yMWuAW9l9xvi")


class EncryptionBehaviour(unittest.TestCase):
    def test_round_trip_and_envelope_shape(self):
        c = crypto_a()
        env = c.encrypt_field("user@gmail.com", table="users", column="email_enc", row_id=ROW)
        v, kid, nonce, ct = env.split(".")
        self.assertEqual((v, kid), ("v1", "k1"))
        self.assertEqual(len(C.b64u_decode(nonce)), 12)
        self.assertEqual(len(C.b64u_decode(ct)), len("user@gmail.com") + 16)
        self.assertNotIn("user@gmail.com", env)
        self.assertEqual(c.decrypt_field(env, table="users", column="email_enc", row_id=ROW), "user@gmail.com")
        self.assertEqual(C.Crypto.envelope_kid(env), "k1")

    def test_unicode_round_trip(self):
        c = crypto_a()
        env = c.encrypt_field("ユーザー@gmail.com", table="users", column="email_enc", row_id=ROW)
        self.assertEqual(c.decrypt_field(env, table="users", column="email_enc", row_id=ROW), "ユーザー@gmail.com")

    def _env(self):
        c = crypto_a()
        return c, c.encrypt_field("user@gmail.com", table="users", column="email_enc", row_id=ROW)

    def test_flip_every_ciphertext_bit_position_class_fails(self):
        c, env = self._env()
        v, kid, nonce, ct = env.split(".")
        raw = bytearray(C.b64u_decode(ct))
        for index in (0, len(raw) // 2, len(raw) - 1):  # first byte, middle, last byte of the tag
            for bit in (0, 7):
                bad = bytearray(raw)
                bad[index] ^= 1 << bit
                tampered = ".".join((v, kid, nonce, C.b64u(bytes(bad))))
                with self.assertRaises(C.CryptoError):
                    c.decrypt_field(tampered, table="users", column="email_enc", row_id=ROW)

    def test_nonce_tamper_fails(self):
        c, env = self._env()
        v, kid, nonce, ct = env.split(".")
        raw = bytearray(C.b64u_decode(nonce))
        raw[0] ^= 1
        with self.assertRaises(C.CryptoError):
            c.decrypt_field(".".join((v, kid, C.b64u(bytes(raw)), ct)), table="users", column="email_enc", row_id=ROW)

    def test_aad_mismatch_table_column_row(self):
        c, env = self._env()
        with self.assertRaises(C.CryptoError):  # other row
            c.decrypt_field(env, table="users", column="email_enc", row_id="b" * 32)
        with self.assertRaises(C.CryptoError):  # other column (ciphertext moved sub_enc <- email_enc)
            c.decrypt_field(env, table="users", column="sub_enc", row_id=ROW)

    def test_wrong_key_and_unknown_kid(self):
        c, env = self._env()
        other = C.Crypto(ring(("k1", KEY_B)))
        with self.assertRaises(C.CryptoError):
            other.decrypt_field(env, table="users", column="email_enc", row_id=ROW)
        other2 = C.Crypto(ring(("k9", KEY_B)))
        with self.assertRaises(C.CryptoError) as ctx:
            other2.decrypt_field(env, table="users", column="email_enc", row_id=ROW)
        self.assertEqual(ctx.exception.reason, "unknown_kid")

    def test_malformed_envelopes(self):
        c = crypto_a()
        for bad in ("", "v1.k1", "v2.k1.AAAA.AAAA", "v1.k1.!!.!!", "v1.k1.AAAAAAAAAAAAAAAA.AAAA", None):
            with self.assertRaises(C.CryptoError):
                c.decrypt_field(bad, table="users", column="email_enc", row_id=ROW)

    def test_only_two_fields_are_encryptable(self):
        self.assertEqual(C.ENCRYPTED_FIELDS, {("users", "sub_enc"), ("users", "email_enc")})
        c = crypto_a()
        for table, column in (("users", "name_enc"), ("sessions", "id_hash"), ("users", "email_bidx")):
            with self.assertRaises(C.CryptoError):
                c.encrypt_field("x", table=table, column=column, row_id=ROW)

    def test_nonce_uniqueness_over_100k_encryptions(self):
        c = crypto_a()
        nonces = set()
        for _ in range(100_000):
            env = c.encrypt_field("x", table="users", column="sub_enc", row_id=ROW)
            nonces.add(env.split(".")[2])
        self.assertEqual(len(nonces), 100_000)

    def test_rotation_decrypts_with_any_key_encrypts_with_newest(self):
        old = C.Crypto(ring(("k1", KEY_A)))
        env_old = old.encrypt_field("a@gmail.com", table="users", column="email_enc", row_id=ROW)
        rotated = C.Crypto(ring(("k2", KEY_B), ("k1", KEY_A)))
        self.assertEqual(rotated.decrypt_field(env_old, table="users", column="email_enc", row_id=ROW), "a@gmail.com")
        env_new = rotated.encrypt_field("a@gmail.com", table="users", column="email_enc", row_id=ROW)
        self.assertEqual(C.Crypto.envelope_kid(env_new), "k2")
        only_new = C.Crypto(ring(("k2", KEY_B)))
        self.assertEqual(only_new.decrypt_field(env_new, table="users", column="email_enc", row_id=ROW), "a@gmail.com")


class BlindIndexAndKeyedValues(unittest.TestCase):
    def test_deterministic_and_field_separated(self):
        c = crypto_a()
        self.assertEqual(c.blind_index("email", "a@gmail.com"), c.blind_index("email", "a@gmail.com"))
        self.assertNotEqual(c.blind_index("email", "a@gmail.com"), c.blind_index("sub", "a@gmail.com"))
        self.assertNotEqual(c.blind_index("email", "a@gmail.com"), c.blind_index("email", "b@gmail.com"))

    def test_not_recomputable_without_the_key(self):
        c = crypto_a()
        stored = c.blind_index("email", "a@gmail.com")
        wrong = C.Crypto(ring(("k1", KEY_B)))
        self.assertNotEqual(wrong.blind_index("email", "a@gmail.com"), stored)
        # an unkeyed SHA-256 of the same message (what an attacker without the key can compute) does not match
        self.assertNotEqual(C.b64u(hashlib.sha256(b"email\x00a@gmail.com").digest()), stored)

    def test_blind_index_all_covers_each_kid_active_first(self):
        c = C.Crypto(ring(("k2", KEY_B), ("k1", KEY_A)))
        got = c.blind_index_all("sub", "s")
        self.assertEqual(got, [c.blind_index("sub", "s", "k2"), c.blind_index("sub", "s", "k1")])
        self.assertNotEqual(got[0], got[1])

    def test_normalisation(self):
        self.assertEqual(C.normalize_email("  John@Gmail.COM "), "john@gmail.com")
        self.assertEqual(C.email_domain("x@y@gmail.com"), "gmail.com")

    def test_csrf_bound_to_session_and_verifies_under_every_ring_key(self):
        old = C.Crypto(ring(("k1", KEY_A)))
        token = old.csrf_token("sid-1")
        self.assertTrue(old.verify_csrf("sid-1", token))
        self.assertFalse(old.verify_csrf("sid-2", token))
        self.assertFalse(old.verify_csrf("sid-1", token[:-1] + ("A" if token[-1] != "A" else "B")))
        self.assertFalse(old.verify_csrf("sid-1", ""))
        rotated = C.Crypto(ring(("k2", KEY_B), ("k1", KEY_A)))
        self.assertTrue(rotated.verify_csrf("sid-1", token))  # signed by the old key
        self.assertNotEqual(rotated.csrf_token("sid-1"), token)
        self.assertTrue(rotated.verify_csrf("sid-1", rotated.csrf_token("sid-1")))

    def test_ip_hash_changes_daily_and_truncates_ipv6(self):
        c = crypto_a()
        self.assertNotEqual(c.ip_hash("203.0.113.7", 0), c.ip_hash("203.0.113.7", 86400))
        self.assertEqual(c.ip_hash("203.0.113.7", 0), c.ip_hash("203.0.113.7", 3600))
        self.assertEqual(c.ip_hash("2001:db8:1:2::1", 0), c.ip_hash("2001:db8:1:2:ffff::9", 0))
        self.assertNotEqual(c.ip_hash("2001:db8:1:2::1", 0), c.ip_hash("2001:db8:1:3::1", 0))
        self.assertEqual(c.ip_hash("::ffff:203.0.113.7", 0), c.ip_hash("203.0.113.7", 0))
        with self.assertRaises(ValueError):
            c.ip_hash("not-an-ip", 0)

    def test_ratelimit_key_scope_and_subject_separation(self):
        c = crypto_a()
        self.assertNotEqual(c.ratelimit_key("a", "bc"), c.ratelimit_key("ab", "c"))
        self.assertEqual(len(c.ratelimit_keys_all("a", "b")), 1)

    def test_consteq_uses_compare_digest(self):
        import inspect

        self.assertIn("compare_digest", inspect.getsource(C.consteq))
        self.assertTrue(C.consteq("é", "é"))
        self.assertFalse(C.consteq("a", "b"))
        self.assertFalse(C.consteq(b"a", "ab"))

    def test_compare_digest_is_the_only_secret_comparison(self):
        import inspect

        src = inspect.getsource(C)
        self.assertNotIn("== token", src)
        self.assertIn("hmac.compare_digest", src)


class KeyRingParsing(unittest.TestCase):
    def good(self, kid="k1", key=KEY_A):
        return f"{kid}:{C.b64u(key)}"

    def test_parse_good_and_order(self):
        r = C.KeyRing.parse(self.good("k2", KEY_B) + ", " + self.good("k1", KEY_A))
        self.assertEqual(r.kids, ("k2", "k1"))
        self.assertEqual(r.active_kid, "k2")
        self.assertEqual(r.key("k1"), KEY_A)

    def test_reasons(self):
        cases = {
            "": "empty",
            "   ": "empty",
            "k1": "format",
            "k1:": "format",
            ":abc": "format",
            "K1:" + C.b64u(KEY_A): "kid",
            "toolongkid1:" + C.b64u(KEY_A): "kid",
            "k1:" + C.b64u(KEY_A) + ",k1:" + C.b64u(KEY_B): "duplicate_kid",
            "k1:" + "!" * 43: "encoding",
            "k1:" + C.b64u(b"\x07" * 32): "weak",
            "k1:" + C.b64u(KEY_A[:31]): "length",
            "k1:" + C.b64u(KEY_A + b"\x00"): "length",
        }
        for text, reason in cases.items():
            with self.assertRaises(C.KeyRingError) as ctx:
                C.KeyRing.parse(text)
            self.assertEqual(ctx.exception.reason, reason, text)

    def test_padding_and_standard_alphabet_handling(self):
        padded = C.base64.urlsafe_b64encode(KEY_A).decode()  # 44 chars with '='
        with self.assertRaises(C.KeyRingError):  # one string form per key: padding is refused (acct-fix)
            C.KeyRing.parse("k1:" + padded)
        self.assertEqual(C.KeyRing.parse("k1:" + padded.rstrip("=")).key("k1"), KEY_A)
        with self.assertRaises(C.KeyRingError):  # '+' and '/' are not base64url
            C.KeyRing.parse("k1:" + "+" * 43)

    def test_repr_never_contains_key_material(self):
        r = C.KeyRing.parse(self.good())
        self.assertNotIn(C.b64u(KEY_A), repr(r) + str(r))
        self.assertNotIn(C.b64u(KEY_A), repr(crypto_a().__dict__.get("ring")))

    def test_keyring_error_message_has_no_value(self):
        secret = C.b64u(b"\x07" * 32)
        with self.assertRaises(C.KeyRingError) as ctx:
            C.KeyRing.parse("k1:" + secret)
        self.assertNotIn(secret, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
