"""Google OpenID Connect authorization-code flow with PKCE (design 3.3, 3.4, D18-D31, D107).

* The endpoints are MODULE CONSTANTS. There is deliberately no environment variable that can change them: a
  deployment can never be pointed at another identity provider. Tests inject a local fake IdP through
  :class:`IdpEndpoints` (loopback ``http`` only, enforced here).
* ``state`` and ``nonce`` are 256-bit random values; only ``SHA-256(domain NUL value)`` is stored, one time, with a TTL.
  The PKCE ``code_verifier`` is NOT stored: it is derived from ``state`` and the browser's pre-login cookie value
  (so without that cookie a code cannot be redeemed).
* The token endpoint is called with ``urllib`` through an opener that verifies TLS, never follows a redirect, ignores
  proxy environment variables, has a 5 s TOTAL wall-clock deadline per call (a watchdog shuts the socket down, so a peer that drips bytes cannot
  pin a worker) and a 64 KiB response cap. Only ``id_token`` is used and no
  Google token is persisted. Errors carry a reason slug only, never the provider's payload.
* ID-token claims are validated exactly as the design lists them. The signature is verified only with
  ``GEMARC_ACCOUNTS_VERIFY_SIG=1`` (RS256 against a cached JWKS, fail closed); otherwise OIDC Core 3.1.3.7 applies
  (the token came straight from the token endpoint over TLS and we authenticated with the client secret).
"""
from __future__ import annotations

import http.client
import json
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .crypto import Crypto, b64u, b64u_decode, consteq, email_domain, normalize_email, sha256_domain
from .db import Database, Tx

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/auth/google/callback"
SCOPE = "openid email"
STATE_TTL_S = 600
CODE_MAX_BYTES = 512
EXCHANGE_TIMEOUT_S = 5
RESPONSE_CAP = 65536
CLOCK_SKEW_S = 60
IAT_MAX_AGE_S = 600
JWKS_TTL_S = 3600
JWKS_REFETCH_S = 60
OPEN_DOMAINS = ("gmail.com", "googlemail.com")
LOOPBACK = ("localhost", "127.0.0.1", "::1")


class IdpError(Exception):
    """Token exchange or JWKS retrieval failed. ``reason`` is a slug; never provider text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ClaimsError(Exception):
    """An ID-token claim failed validation (``reason`` is for the audit log only)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# -- endpoints -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class IdpEndpoints:
    authorize_url: str = GOOGLE_AUTHORIZE_URL
    token_url: str = GOOGLE_TOKEN_URL
    jwks_url: str = GOOGLE_JWKS_URL

    def __post_init__(self) -> None:
        for field_name, constant in (("authorize_url", GOOGLE_AUTHORIZE_URL), ("token_url", GOOGLE_TOKEN_URL),
                                     ("jwks_url", GOOGLE_JWKS_URL)):
            value = getattr(self, field_name)
            if value == constant:
                continue
            parts = urllib.parse.urlsplit(value)
            if parts.scheme != "http" or (parts.hostname or "").lower() not in LOOPBACK:
                raise ValueError("identity provider endpoints must be the Google constants or a loopback http URL")

    @classmethod
    def google(cls) -> "IdpEndpoints":
        return cls()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        raise IdpError("redirect")


def build_ssl_context() -> ssl.SSLContext:
    """The TLS context of every outgoing identity-provider call: verified chain AND verified host name."""
    ctx = ssl.create_default_context()
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:  # pragma: no cover - defensive
        raise IdpError("tls_context")
    return ctx


class _Guard:
    """Sockets opened by one ``_fetch`` call; ``trip`` shuts them down so a blocked read returns at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._socks: "list[socket.socket]" = []
        self.tripped = False

    def attach(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.append(sock)
            tripped = self.tripped
        if tripped:
            self._shutdown(sock)

    def trip(self) -> None:
        with self._lock:
            self.tripped = True
            socks = list(self._socks)
        for sock in socks:
            self._shutdown(sock)

    @staticmethod
    def _shutdown(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


_active = threading.local()


class _GuardedMixin:
    def connect(self) -> None:  # noqa: D401 - http.client hook
        super().connect()
        guard = getattr(_active, "guard", None)
        if guard is not None:
            guard.attach(self.sock)


class _GuardedHTTPConnection(_GuardedMixin, http.client.HTTPConnection):
    pass


class _GuardedHTTPSConnection(_GuardedMixin, http.client.HTTPSConnection):
    pass


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


def build_opener() -> urllib.request.OpenerDirector:
    """Opener without proxies and without redirects, TLS verified; its connections register with the deadline guard."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _GuardedHTTPHandler(),
        _GuardedHTTPSHandler(context=build_ssl_context()),
        _NoRedirect(),
    )


def _fetch(opener, request: urllib.request.Request, timeout: float) -> "tuple[int, str, bytes]":
    """(status, content-type, body). Raises ``IdpError`` for transport failures, redirects, oversized bodies and for
    exceeding ``timeout`` seconds IN TOTAL (connect, headers and body together): ``urllib``'s own timeout is per
    socket operation, so a peer sending one byte per second would never trip it."""
    guard = _Guard()
    _active.guard = guard
    timer = threading.Timer(timeout, guard.trip)
    timer.daemon = True
    timer.start()
    deadline = time.monotonic() + timeout
    try:
        with opener.open(request, timeout=timeout) as response:
            read = getattr(response, "read1", response.read)  # one raw read per call, so the deadline is re-checked
            chunks: "list[bytes]" = []
            total = 0
            while total <= RESPONSE_CAP:
                if guard.tripped or time.monotonic() >= deadline:
                    raise IdpError("transport")
                chunk = read(min(8192, RESPONSE_CAP + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if guard.tripped:
                raise IdpError("transport")
            return response.status, response.headers.get("Content-Type", ""), b"".join(chunks)
    except IdpError:
        raise
    except urllib.error.HTTPError as error:
        error.close()
        raise IdpError(f"http_{error.code}") from None
    except Exception:  # URLError, timeout, ssl, connection reset, a watchdog shutdown ...
        raise IdpError("transport") from None
    finally:
        timer.cancel()
        _active.guard = None


def _json_no_dups(raw: "str | bytes") -> object:
    def hook(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key")
        return dict(pairs)

    def bad_constant(_name):
        raise ValueError("non-finite number")

    try:
        return json.loads(raw, object_pairs_hook=hook, parse_constant=bad_constant)
    except RecursionError:  # absurdly nested input from a hostile peer: same class of failure as malformed JSON
        raise ValueError("nesting too deep") from None


# -- authorization request -------------------------------------------------------------------------------------------
def derive_verifier(state: str, binding: str) -> str:
    """PKCE ``code_verifier`` (43 chars): base64url(SHA-256("pkce" NUL state NUL cookie value)). Never stored."""
    return sha256_domain("pkce", state + "\0" + binding)


def code_challenge(verifier: str) -> str:
    import hashlib

    return b64u(hashlib.sha256(verifier.encode("ascii")).digest())


def binding_hash(state: str, binding: str) -> str:
    return sha256_domain("bind", state + "\0" + binding)


def new_random() -> str:
    return b64u(secrets.token_bytes(32))


def authorization_url(endpoints: IdpEndpoints, *, client_id: str, redirect_uri: str, state: str, nonce: str,
                      challenge: str) -> str:
    """The Google authorization URL (design table 3.3). No ``profile`` scope, no ``hd``, no ``login_hint``."""
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    return f"{endpoints.authorize_url}?{query}"


# -- server-side one-time state --------------------------------------------------------------------------------------
INSERT_STATE_SQL = (
    "INSERT INTO oauth_states (state_hash, nonce_hash, binding_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)"
)
CONSUME_STATE_SQL = (
    "DELETE FROM oauth_states WHERE state_hash = ? RETURNING nonce_hash, binding_hash, expires_at"
)
CLEAN_STATE_SQL = (
    "DELETE FROM oauth_states WHERE state_hash IN (SELECT state_hash FROM oauth_states WHERE expires_at < ? LIMIT ?)"
)


@dataclass(frozen=True)
class StateRecord:
    nonce_hash: str
    binding_hash: str


class OAuthStates:
    def __init__(self, db: Database, ttl: int = STATE_TTL_S) -> None:
        self.db = db
        self.ttl = ttl

    def create(self, state: str, nonce: str, binding: str, now: int, tx: Optional[Tx] = None) -> None:
        params = (sha256_domain("state", state), sha256_domain("nonce", nonce), binding_hash(state, binding),
                  int(now), int(now) + self.ttl)
        if tx is not None:
            tx.execute(INSERT_STATE_SQL, params)
            tx.execute(CLEAN_STATE_SQL, (int(now), 200))
            return
        with self.db.transaction() as own:
            own.execute(INSERT_STATE_SQL, params)
            own.execute(CLEAN_STATE_SQL, (int(now), 200))

    def consume(self, state: str, now: int) -> Optional[StateRecord]:
        """Atomic one-time use. An expired record is consumed too and reported as missing."""
        with self.db.transaction() as tx:
            row = tx.fetchone(CONSUME_STATE_SQL, (sha256_domain("state", state),))
        if row is None or int(row["expires_at"]) <= int(now):
            return None
        return StateRecord(row["nonce_hash"], row["binding_hash"])


# -- token exchange --------------------------------------------------------------------------------------------------
class GoogleClient:
    """Token exchange and JWKS retrieval for one client registration. Blocking: run it in a worker thread."""

    def __init__(self, endpoints: IdpEndpoints, client_id: str, client_secret: str, redirect_uri: str,
                 *, opener=None, clock: Callable[[], float] = time.time, timeout: float = EXCHANGE_TIMEOUT_S) -> None:
        self.endpoints = endpoints
        self._client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._opener = opener or build_opener()
        self._clock = clock
        self._timeout = timeout
        self._jwks_lock = threading.Lock()
        self._jwks: "Optional[dict]" = None
        self._jwks_at = 0.0
        self._jwks_try = 0.0

    def exchange_code(self, code: str, verifier: str) -> str:
        """POST the code to the token endpoint and return the raw ``id_token`` string."""
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code_verifier": verifier,
        }).encode("ascii")
        request = urllib.request.Request(
            self.endpoints.token_url, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        status, ctype, body = _fetch(self._opener, request, self._timeout)
        if status != 200 or len(body) > RESPONSE_CAP:
            raise IdpError("status" if status != 200 else "too_large")
        if not ctype.lower().split(";")[0].strip() == "application/json":
            raise IdpError("content_type")
        try:
            payload = _json_no_dups(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise IdpError("json") from None
        if not isinstance(payload, dict) or payload.get("token_type") != "Bearer":
            raise IdpError("token_type")
        token = payload.get("id_token")
        if not isinstance(token, str) or not token or len(token) > RESPONSE_CAP:
            raise IdpError("no_id_token")
        return token

    def jwks(self, kid: str) -> dict:
        """The JWK for ``kid`` (cached up to an hour; at most one refetch per minute on a miss). Fail closed."""
        with self._jwks_lock:
            now = self._clock()
            fresh = self._jwks is not None and now - self._jwks_at < JWKS_TTL_S
            if fresh and kid in self._jwks:
                return self._jwks[kid]
            if (not fresh or kid not in (self._jwks or {})) and now - self._jwks_try >= JWKS_REFETCH_S:
                self._jwks_try = now
                request = urllib.request.Request(self.endpoints.jwks_url, method="GET",
                                                 headers={"Accept": "application/json"})
                status, ctype, body = _fetch(self._opener, request, self._timeout)
                if status != 200 or len(body) > RESPONSE_CAP:
                    raise IdpError("jwks")
                try:
                    doc = _json_no_dups(body.decode("utf-8"))
                    keys = {k["kid"]: k for k in doc["keys"] if isinstance(k, dict) and isinstance(k.get("kid"), str)}
                except (ValueError, UnicodeDecodeError, KeyError, TypeError):
                    raise IdpError("jwks") from None
                self._jwks, self._jwks_at = keys, now
            if self._jwks is not None and kid in self._jwks and now - self._jwks_at < JWKS_TTL_S:
                return self._jwks[kid]
            raise IdpError("jwks_kid")


# -- id token --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Claims:
    sub: str
    email: str  # normalised strip().lower()
    hd: Optional[str]

    @property
    def has_hd(self) -> bool:
        return bool(self.hd)


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) < 1e15


def verify_rs256(token: str, jwk: dict) -> None:
    """RS256 signature check with ``cryptography`` (imported lazily). Raises ``ClaimsError('signature')``."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        head, payload, sig = token.split(".")
        n = int.from_bytes(b64u_decode(jwk["n"]), "big")
        e = int.from_bytes(b64u_decode(jwk["e"]), "big")
        key = rsa.RSAPublicNumbers(e, n).public_key()
        key.verify(b64u_decode(sig), (head + "." + payload).encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        raise ClaimsError("signature") from None


def validate_id_token(token: str, *, client_id: str, nonce_hash: str, now: float,
                      google: Optional[GoogleClient] = None, verify_sig: bool = False) -> Claims:
    """Validate the ID token exactly as listed in design 3.3. Raises ``ClaimsError``."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or not all(parts):
        raise ClaimsError("structure")
    if verify_sig:
        try:
            header = _json_no_dups(b64u_decode(parts[0]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ClaimsError("header") from None
        if not isinstance(header, dict) or header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ClaimsError("alg")
        if google is None:
            raise ClaimsError("signature")
        try:
            jwk = google.jwks(header["kid"])
        except IdpError:
            raise ClaimsError("jwks") from None
        if jwk.get("kty") != "RSA":
            raise ClaimsError("signature")
        verify_rs256(token, jwk)
    try:
        claims = _json_no_dups(b64u_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ClaimsError("payload") from None
    if not isinstance(claims, dict):
        raise ClaimsError("payload")

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ClaimsError("iss")
    aud, azp = claims.get("aud"), claims.get("azp")
    if isinstance(aud, str):
        if aud != client_id:
            raise ClaimsError("aud")
    elif isinstance(aud, list) and all(isinstance(a, str) for a in aud):
        if client_id not in aud or azp != client_id:
            raise ClaimsError("aud")
    else:
        raise ClaimsError("aud")
    if azp is not None and azp != client_id:
        raise ClaimsError("azp")
    exp, iat = claims.get("exp"), claims.get("iat")
    if not _numeric(exp) or not exp > now - CLOCK_SKEW_S:
        raise ClaimsError("exp")
    if not _numeric(iat) or iat > now + CLOCK_SKEW_S or now - iat > IAT_MAX_AGE_S:
        raise ClaimsError("iat")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not consteq(sha256_domain("nonce", nonce), nonce_hash):
        raise ClaimsError("nonce")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255 or not sub.isascii():
        raise ClaimsError("sub")
    email = claims.get("email")
    if not isinstance(email, str) or email.count("@") != 1:
        raise ClaimsError("email")
    email_n = normalize_email(email)
    local, _, domain = email_n.partition("@")
    if not local or not domain or any(ord(c) < 33 or ord(c) == 127 for c in email_n) or len(email_n) > 320:
        raise ClaimsError("email")
    if claims.get("email_verified") is not True:
        raise ClaimsError("unverified")
    hd = claims.get("hd")
    if hd is not None and not isinstance(hd, str):
        raise ClaimsError("hd")
    return Claims(sub, email_n, hd.strip().lower() if hd else None)


# -- access policy (design 3.4, D92) ---------------------------------------------------------------------------------
class AccessPolicy:
    """Domain allow-list and optional e-mail list; also the blind-index re-check of existing users (D92)."""

    def __init__(self, domains: "tuple[str, ...]", emails: "tuple[str, ...]", crypto: Crypto) -> None:
        self.domains = tuple(d.strip().lower() for d in domains)
        self.emails = frozenset(normalize_email(e) for e in emails)
        # blind-index sets for every ring key: domain_bidx -> required has_hd, email_bidx set
        self._domain_hd = {}
        for entry in self.domains:
            needs_hd = 0 if entry in OPEN_DOMAINS else 1
            for idx in crypto.blind_index_all("domain", entry):
                self._domain_hd[idx] = needs_hd
        self._email_bidx = frozenset(idx for e in self.emails for idx in crypto.blind_index_all("email", e))

    def check(self, claims: Claims) -> Optional[str]:
        """None when the identity is allowed, else the audit reason (``domain`` or ``email_list``)."""
        domain = email_domain(claims.email)
        allowed = False
        for entry in self.domains:
            if domain != entry:
                continue
            if entry in OPEN_DOMAINS:
                allowed = allowed or claims.hd is None
            else:
                allowed = allowed or claims.hd == entry
        if not allowed:
            return "domain"
        if self.emails and claims.email not in self.emails:
            return "email_list"
        return None

    def row_ok(self, row: dict) -> bool:
        """Policy re-check of a stored user from its blind indexes and ``has_hd`` (no decryption, no extra query)."""
        needs = self._domain_hd.get(row.get("domain_bidx"))
        if needs is None or int(row.get("has_hd") or 0) != needs:
            return False
        if self.emails and row.get("email_bidx") not in self._email_bidx:
            return False
        return True
