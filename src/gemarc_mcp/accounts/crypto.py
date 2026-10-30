"""Field encryption, blind indexes and domain-separated hashes for the accounts package.

Design (docs/09_部署/02_账号与安全.md section 4):

* One keyring (``kid:base64url(32 bytes)`` items, first = active). Per-purpose sub-keys come from HKDF-SHA256, so
  one key is never used for both encryption and MACs.
* ``users.sub_enc`` and ``users.email_enc`` are AES-256-GCM envelopes ``v1.<kid>.<nonce>.<ct||tag>`` with a random
  96-bit nonce and AAD ``gemarc-acct-v1|table|column|row_id``.
* Blind indexes are HMAC-SHA256 under the ``blind-index`` sub-key (keyed: they resist dictionary enumeration).
* Session ids, PATs, OAuth state and similar are 256-bit random values, so they are stored as UNKEYED SHA-256 with a
  fixed domain prefix (section 4.5). Their irreversibility rests on the input entropy, not on a key, and rotating the
  keyring therefore never invalidates a session or a token.

Only the standard library is imported at module level; ``cryptography`` is imported lazily on first encryption.
Nothing here logs or formats key material; exception messages carry a reason slug only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from typing import Iterable, Optional

ENVELOPE_VERSION = "v1"
HKDF_SALT = b"gemarc-accounts-v1"
HKDF_INFO_PREFIX = b"gemarc/accounts/v1/"
PURPOSES = ("field-enc", "blind-index", "csrf", "ip-hash", "ratelimit")
AAD_PREFIX = b"gemarc-acct-v1|"
#: The only (table, column) pairs that are ever encrypted. Anything else is a programming error.
ENCRYPTED_FIELDS = frozenset({("users", "sub_enc"), ("users", "email_enc")})
#: Domain prefixes of the unkeyed hashes (section 4.5). Each is followed by a NUL byte.
HASH_DOMAINS = frozenset({"session", "pat", "state", "nonce", "bind", "pkce"})
BLIND_FIELDS = frozenset({"sub", "email", "domain"})
KEY_BYTES = 32
NONCE_BYTES = 12

_KID_RE = re.compile(r"^[a-z0-9]{1,8}\Z")
_B64U_RE = re.compile(r"[A-Za-z0-9_-]+")
#: A key must show at least this many distinct byte values (rejects all-equal, repeating-pair and short-word keys).
MIN_DISTINCT_KEY_BYTES = 16


class CryptoError(Exception):
    """Raised for any encryption, decryption or key problem. The message is a reason slug, never key material."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KeyRingError(CryptoError):
    """The keyring text is malformed or weak (reason slug only)."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    """Strict base64url decoder: alphabet-checked, UNPADDED (an ``=`` is refused so every value has exactly one
    string form) and canonical (re-encoding must reproduce the input)."""
    if not isinstance(text, str) or not _B64U_RE.fullmatch(text):
        raise ValueError("base64url")
    stripped = text
    padded = stripped + "=" * (-len(stripped) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except Exception:  # binascii.Error, ValueError
        raise ValueError("base64url") from None
    if b64u(raw) != stripped:
        raise ValueError("base64url")
    return raw


def consteq(a: "str | bytes", b: "str | bytes") -> bool:
    """Constant-time equality for str or bytes (any characters)."""
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hmac.compare_digest(a, b)


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF with SHA-256 (extract-then-expand)."""
    if length < 1 or length > 255 * 32:
        raise ValueError("length")
    prk = hmac.new(salt or b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def sha256_domain(domain: str, value: "str | bytes") -> str:
    """base64url(SHA-256(domain + NUL + value)), unkeyed (design 4.5). ``domain`` must be a known prefix."""
    if domain not in HASH_DOMAINS:
        raise ValueError("domain")
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return b64u(hashlib.sha256(domain.encode("ascii") + b"\x00" + raw).digest())


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_domain(email: str) -> str:
    return normalize_email(email).rpartition("@")[2]


class KeyRing:
    """Immutable list of ``(kid, 32-byte key)``; the first entry is the active one."""

    __slots__ = ("_entries",)

    def __init__(self, entries: "Iterable[tuple[str, bytes]]") -> None:
        items = tuple(entries)
        if not items:
            raise KeyRingError("empty")
        seen = set()
        material = set()
        for kid, key in items:
            if not _KID_RE.fullmatch(kid):
                raise KeyRingError("kid")
            if kid in seen:
                raise KeyRingError("duplicate_kid")
            seen.add(kid)
            if len(key) != KEY_BYTES:
                raise KeyRingError("length")
            if len(set(key)) < MIN_DISTINCT_KEY_BYTES:
                raise KeyRingError("weak")
            if key in material:  # the same bytes under two ids would make a rotation a silent no-op
                raise KeyRingError("duplicate_key")
            material.add(key)
        self._entries = items

    @classmethod
    def parse(cls, text: str) -> "KeyRing":
        """Parse ``kid:b64url,kid:b64url`` (first = active). Errors carry a reason slug and never the value."""
        if not isinstance(text, str) or not text.strip():
            raise KeyRingError("empty")
        entries = []
        for part in text.split(","):
            part = part.strip()
            kid, sep, material = part.partition(":")
            if not sep or not kid or not material:
                raise KeyRingError("format")
            try:
                key = b64u_decode(material)
            except ValueError:
                raise KeyRingError("encoding") from None
            entries.append((kid, key))
        return cls(entries)

    @property
    def active_kid(self) -> str:
        return self._entries[0][0]

    @property
    def kids(self) -> "tuple[str, ...]":
        return tuple(kid for kid, _ in self._entries)

    def key(self, kid: str) -> Optional[bytes]:
        for k, v in self._entries:
            if k == kid:
                return v
        return None

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:  # never print key material
        return f"KeyRing(kids={list(self.kids)!r})"

    __str__ = __repr__


def _seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM seal with a caller-chosen nonce. Internal: public encryption always uses a random nonce."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, nonce: bytes, sealed: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).decrypt(nonce, sealed, aad)


def field_aad(table: str, column: str, row_id: str) -> bytes:
    return AAD_PREFIX + table.encode("ascii") + b"|" + column.encode("ascii") + b"|" + row_id.encode("ascii")


class Crypto:
    """All keyed operations of the accounts package, bound to one keyring."""

    def __init__(self, ring: KeyRing) -> None:
        self.ring = ring
        self._sub: "dict[tuple[str, str], bytes]" = {}

    # -- sub-keys ---------------------------------------------------------------------------------------------
    def subkey(self, kid: str, purpose: str) -> bytes:
        if purpose not in PURPOSES:
            raise ValueError("purpose")
        cached = self._sub.get((kid, purpose))
        if cached is not None:
            return cached
        master = self.ring.key(kid)
        if master is None:
            raise CryptoError("unknown_kid")
        derived = hkdf_sha256(master, HKDF_SALT, HKDF_INFO_PREFIX + purpose.encode("ascii"), 32)
        self._sub[(kid, purpose)] = derived
        return derived

    def _mac(self, kid: str, purpose: str, message: bytes) -> str:
        return b64u(hmac.new(self.subkey(kid, purpose), message, hashlib.sha256).digest())

    # -- field encryption -------------------------------------------------------------------------------------
    def encrypt_field(self, plaintext: str, *, table: str, column: str, row_id: str) -> str:
        """Encrypt with the active key and a fresh random 96-bit nonce; AAD binds table, column and row id."""
        if (table, column) not in ENCRYPTED_FIELDS:
            raise CryptoError("field_not_encryptable")
        kid = self.ring.active_kid
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = _seal(self.subkey(kid, "field-enc"), nonce, plaintext.encode("utf-8"), field_aad(table, column, row_id))
        return ".".join((ENVELOPE_VERSION, kid, b64u(nonce), b64u(sealed)))

    @staticmethod
    def envelope_kid(envelope: str) -> str:
        parts = envelope.split(".") if isinstance(envelope, str) else []
        if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
            raise CryptoError("envelope")
        return parts[1]

    def decrypt_field(self, envelope: str, *, table: str, column: str, row_id: str) -> str:
        if (table, column) not in ENCRYPTED_FIELDS:
            raise CryptoError("field_not_encryptable")
        parts = envelope.split(".") if isinstance(envelope, str) else []
        if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
            raise CryptoError("envelope")
        kid = parts[1]
        if self.ring.key(kid) is None:
            raise CryptoError("unknown_kid")
        try:
            nonce = b64u_decode(parts[2])
            sealed = b64u_decode(parts[3])
        except ValueError:
            raise CryptoError("envelope") from None
        if len(nonce) != NONCE_BYTES or len(sealed) < 16:
            raise CryptoError("envelope")
        try:
            plain = _open(self.subkey(kid, "field-enc"), nonce, sealed, field_aad(table, column, row_id))
        except Exception:  # cryptography.exceptions.InvalidTag; never forward driver text
            raise CryptoError("decrypt_failed") from None
        return plain.decode("utf-8")

    # -- blind indexes ----------------------------------------------------------------------------------------
    def blind_index(self, field: str, value: str, kid: Optional[str] = None) -> str:
        """HMAC(blind-index key, field + NUL + normalised value). Caller normalises (email: ``normalize_email``)."""
        if field not in BLIND_FIELDS:
            raise ValueError("field")
        return self._mac(kid or self.ring.active_kid, "blind-index", field.encode("ascii") + b"\x00" + value.encode("utf-8"))

    def blind_index_all(self, field: str, value: str) -> "list[str]":
        """One index per ring key, active first. Lookups run one fixed query per element (no dynamic IN lists)."""
        return [self.blind_index(field, value, kid) for kid in self.ring.kids]

    # -- csrf / ip / rate-limit keys --------------------------------------------------------------------------
    def csrf_token(self, sid: str) -> str:
        return self._mac(self.ring.active_kid, "csrf", sid.encode("utf-8"))

    def verify_csrf(self, sid: str, token: str) -> bool:
        """Checks the token against EVERY ring key without short-circuit, so a rotation between render and submit works."""
        ok = False
        for kid in self.ring.kids:
            ok |= consteq(self._mac(kid, "csrf", sid.encode("utf-8")), token if isinstance(token, str) else "")
        return ok

    @staticmethod
    def canonical_ip(ip: str) -> str:
        """IPv4 as is, IPv6 truncated to its /64 network. Raises ValueError for non-IP text."""
        addr = ipaddress.ip_address(ip.strip())
        if isinstance(addr, ipaddress.IPv6Address):
            if addr.ipv4_mapped is not None:
                return str(addr.ipv4_mapped)
            return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
        return str(addr)

    def ip_hash(self, ip: str, now: Optional[int] = None) -> str:
        """HMAC(ip-hash key, UTC date + NUL + canonical ip): the salt changes daily on purpose."""
        day = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))
        return self._mac(self.ring.active_kid, "ip-hash", day.encode("ascii") + b"\x00" + self.canonical_ip(ip).encode("ascii"))

    @staticmethod
    def _ratelimit_message(scope: str, subject: str) -> bytes:
        """Unambiguous MAC input: the scope is length-prefixed, so no (scope, subject) pair collides with another."""
        raw_scope = scope.encode("utf-8")
        if len(raw_scope) > 0xFFFF:
            raise ValueError("scope")
        return len(raw_scope).to_bytes(2, "big") + raw_scope + subject.encode("utf-8")

    def ratelimit_key(self, scope: str, subject: str, kid: Optional[str] = None) -> str:
        return self._mac(kid or self.ring.active_kid, "ratelimit", self._ratelimit_message(scope, subject))

    def ratelimit_keys_all(self, scope: str, subject: str) -> "list[str]":
        """Keys under every ring key (used when deleting a user's counters after a rotation)."""
        message = self._ratelimit_message(scope, subject)
        return [self._mac(kid, "ratelimit", message) for kid in self.ring.kids]

    def new_row_id(self) -> str:
        """128-bit random id, 32 hex chars (the AAD row id, generated before encrypting)."""
        return secrets.token_hex(16)
