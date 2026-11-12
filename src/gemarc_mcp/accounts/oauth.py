"""OAuth 2.1 for the MCP endpoint (design 3.16, ADR-020).

The token channel (``Authorization: Bearer gat_...``) and the OAuth channel
(``Authorization: Bearer goa_...``) are two doors into the same room: the
outside shape of the request does not change, only what the bearer may be.
A client that only speaks OAuth (the ones whose "authorize" button drives
discovery and dynamic registration) can now reach the same tools.

Three shapes of secret live here, and none of them is ever stored in clear:

* an **authorisation code** ``goc_<43>``: 60 seconds, one use, bound to the
  client, the redirect URI, the scope, the audience and the PKCE challenge;
* an **access token** ``goa_<43>``: 15 minutes, bound to the audience
  (RFC 8707), so one deployment's token cannot be used on another;
* a **refresh token** ``gor_<43>``: 30 days, **rotated on every use**. Using a
  rotated token again is a reuse: the whole chain is revoked.

Every secret is stored as ``SHA-256`` with a domain separator; the plaintext
exists once, in the value the caller hands to the client.

The rules this module implements are the ones of design section 3.16; the
decision record is ADR-020.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Optional

from . import audit as audit_mod
from .crypto import consteq, sha256_domain
from .db import Database

#: The prefixes are the dispatch key: ``/mcp`` sends ``gat_`` to the token
#: channel and ``goa_`` to the access token channel.
CLIENT_PREFIX = "gcl_"
CODE_PREFIX = "goc_"
ACCESS_PREFIX = "goa_"
REFRESH_PREFIX = "gor_"

SECRET_LENGTH = 43
CLIENT_ID_LENGTH = 8
BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

#: How long each secret lives.
ACCESS_TTL_S = 900
REFRESH_TTL_S = 30 * 86400
CODE_TTL_S = 60
CLIENT_TTL_S = 365 * 86400

#: The scopes this deployment knows.
SCOPES = ("read", "write")

#: The grant types and the response type this deployment speaks.
GRANT_TYPES = ("authorization_code", "refresh_token")
RESPONSE_TYPES = ("code",)

#: The only code challenge method: "plain" is not accepted (RFC 7636 section 4.2).
CODE_CHALLENGE_METHODS = ("S256",)

#: The public clients may use: no secret, no client authentication.
TOKEN_ENDPOINT_AUTH_METHODS = ("none",)

#: The redirect URIs that may be registered: https anywhere, http only on the
#: loopback interface (RFC 8252 section 7.3).
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class OAuthError(Exception):
    """A refusal with an RFC 6749 error code and a reason slug for the audit log."""

    def __init__(self, error: str, reason: str, status: int = 400) -> None:
        super().__init__(f"{error} {reason}")
        self.error = error
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class OAuthClient:
    id: str
    user_id: Optional[str]
    name: str
    redirect_uris: "tuple[str, ...]"
    grant_types: "tuple[str, ...]"
    response_types: "tuple[str, ...]"
    created_at: int


@dataclass(frozen=True)
class Authorization:
    """The code that ``/authorize`` produced."""

    code: str
    client_id: str
    user_id: str
    redirect_uri: str
    scope: str
    resource: str
    code_challenge: str
    expires_at: int


@dataclass(frozen=True)
class IssuedTokens:
    """The plaintext pair; it exists once, in the response to ``/token``."""

    access_token: str
    refresh_token: str
    scope: str
    expires_in: int
    refresh_expires_at: int


@dataclass(frozen=True)
class AccessVerification:
    ok: bool
    reason: str  # "ok" or format | unknown | mismatch | revoked | expired | audience | disabled
    token_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: Optional[str] = None
    client_id: Optional[str] = None
    expires_at: Optional[int] = None


# -- the secrets ------------------------------------------------------------------------------------------------------


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def mint_secret(prefix: str) -> str:
    return prefix + _b64u(secrets.token_bytes(32))


def mint_client_id() -> str:
    return CLIENT_PREFIX + "".join(
        BASE32_ALPHABET[b & 31] for b in secrets.token_bytes(CLIENT_ID_LENGTH)
    )


def split_secret(token: object, prefix: str) -> Optional[str]:
    """The secret part of a token, or None when the shape is wrong. No database."""
    if not isinstance(token, str) or not token.startswith(prefix):
        return None
    secret = token[len(prefix):]
    if len(secret) != SECRET_LENGTH:
        return None
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in secret):
        return None
    return secret


def hash_secret(domain: str, secret: str) -> str:
    return sha256_domain(domain, secret)


def code_challenge(verifier: str) -> str:
    """The S256 challenge of a verifier (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64u(digest)


def challenge_matches(verifier: str, challenge: str) -> bool:
    if not verifier or len(verifier) < 43 or len(verifier) > 128:
        return False
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~" for c in verifier):
        return False
    return consteq(code_challenge(verifier), challenge)


def valid_redirect_uri(uri: object) -> bool:
    """https anywhere, http only on the loopback interface."""
    if not isinstance(uri, str) or not uri or len(uri) > 512:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in uri):
        return False
    lowered = uri.lower()
    if lowered.startswith("https://"):
        return True
    if not lowered.startswith("http://"):
        return False
    rest = lowered[len("http://"):].split("/", 1)[0]
    host = rest.rsplit(":", 1)[0] if rest.count(":") == 1 else rest
    return host in LOOPBACK_HOSTS


# -- the metadata documents --------------------------------------------------------------------------------------------


def resource_metadata(base_url: str) -> dict:
    """RFC 9728: what this resource is and which server may issue for it."""
    return {
        "resource": base_url,
        "authorization_servers": [base_url],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "GemArc MCP",
    }


def authorization_server_metadata(base_url: str) -> dict:
    """RFC 8414: the endpoints and the shapes this server speaks."""
    return {
        "issuer": base_url,
        "authorization_endpoint": base_url + "/authorize",
        "token_endpoint": base_url + "/token",
        "registration_endpoint": base_url + "/register",
        "scopes_supported": list(SCOPES),
        "response_types_supported": list(RESPONSE_TYPES),
        "grant_types_supported": list(GRANT_TYPES),
        "code_challenge_methods_supported": list(CODE_CHALLENGE_METHODS),
        "token_endpoint_auth_methods_supported": list(TOKEN_ENDPOINT_AUTH_METHODS),
        "service_documentation": base_url + "/account",
    }


# -- the store ----------------------------------------------------------------------------------------------------------

INSERT_CLIENT_SQL = (
    "INSERT INTO oauth_clients (id, user_id, name, redirect_uris, grant_types, response_types, created_at, expires_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
)
SELECT_CLIENT_SQL = (
    "SELECT id, user_id, name, redirect_uris, grant_types, response_types, created_at FROM oauth_clients WHERE id = ?"
)
INSERT_CODE_SQL = (
    "INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, scope, resource, code_challenge, "
    "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
SELECT_CODE_SQL = (
    "SELECT code_hash, client_id, user_id, redirect_uri, scope, resource, code_challenge, created_at, expires_at, "
    "used_at FROM oauth_codes WHERE code_hash = ?"
)
USE_CODE_SQL = "UPDATE oauth_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL"
INSERT_ACCESS_SQL = (
    "INSERT INTO oauth_access_tokens (id, token_hash, client_id, user_id, scope, resource, created_at, expires_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
SELECT_ACCESS_SQL = (
    "SELECT t.id AS id, t.token_hash AS token_hash, t.client_id AS client_id, t.user_id AS user_id, t.scope AS scope, "
    "resource, t.expires_at AS expires_at, t.revoked_at AS revoked_at, u.status AS status, "
    "u.can_write AS can_write FROM oauth_access_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?"
)
TOUCH_ACCESS_SQL = "UPDATE oauth_access_tokens SET last_used_at = ? WHERE id = ?"
INSERT_REFRESH_SQL = (
    "INSERT INTO oauth_refresh_tokens (id, token_hash, client_id, user_id, scope, resource, chain_id, created_at, "
    "expires_at, rotated_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)"
)
SELECT_REFRESH_SQL = (
    "SELECT id, token_hash, client_id, user_id, scope, resource, chain_id, created_at, expires_at, revoked_at, "
    "rotated_to FROM oauth_refresh_tokens WHERE token_hash = ?"
)
REVOKE_REFRESH_SQL = "UPDATE oauth_refresh_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
ROTATE_REFRESH_SQL = (
    "UPDATE oauth_refresh_tokens SET revoked_at = ?, rotated_to = ? WHERE id = ? AND revoked_at IS NULL"
)
REVOKE_CHAIN_SQL = "UPDATE oauth_refresh_tokens SET revoked_at = ? WHERE chain_id = ? AND revoked_at IS NULL"
REVOKE_CLIENT_SQL = (
    "UPDATE oauth_access_tokens SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL"
)
CLEANUP_CODES_SQL = "DELETE FROM oauth_codes WHERE expires_at < ? LIMIT ?"
CLEANUP_ACCESS_SQL = "DELETE FROM oauth_access_tokens WHERE expires_at < ? LIMIT ?"
CLEANUP_REFRESH_SQL = (
    "DELETE FROM oauth_refresh_tokens WHERE (expires_at < ?) OR (revoked_at IS NOT NULL AND revoked_at < ?) LIMIT ?"
)
CLEANUP_CLIENTS_SQL = "DELETE FROM oauth_clients WHERE expires_at < ? LIMIT ?"


class OAuthStore:
    """Everything OAuth touches in the database. The plaintext never comes back."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- clients ------------------------------------------------------------------------------------------------
    def register_client(self, name: str, redirect_uris: "list[str]", now: int, *,
                        user_id: Optional[str] = None, grant_types: "Optional[list[str]]" = None,
                        response_types: "Optional[list[str]]" = None) -> OAuthClient:
        """Register a public client (RFC 7591). No secret is issued."""
        client_id = mint_client_id()
        grants = list(grant_types or GRANT_TYPES)
        responses = list(response_types or RESPONSE_TYPES)
        with self.db.transaction() as tx:
            done = tx.execute(
                INSERT_CLIENT_SQL,
                (client_id, user_id, name, json.dumps(redirect_uris, ensure_ascii=False),
                 json.dumps(grants), json.dumps(responses), now, now + CLIENT_TTL_S),
            )
            if done != 1:
                raise OAuthError("invalid_client_metadata", "id_collision", 409)
            audit_mod.record(tx, now, "oauth_client_registered", user_id=user_id, ref=client_id)
        return OAuthClient(client_id, user_id, name, tuple(redirect_uris), tuple(grants), tuple(responses), now)

    def get_client(self, client_id: object) -> Optional[OAuthClient]:
        if not isinstance(client_id, str) or not client_id.startswith(CLIENT_PREFIX):
            return None
        with self.db.transaction() as tx:
            row = tx.fetchone(SELECT_CLIENT_SQL, (client_id,))
        if row is None:
            return None
        return OAuthClient(
            row["id"], row["user_id"], row["name"],
            tuple(json.loads(row["redirect_uris"])),
            tuple(json.loads(row["grant_types"])),
            tuple(json.loads(row["response_types"])),
            row["created_at"],
        )

    # -- authorization codes ------------------------------------------------------------------------------------
    def issue_code(self, client_id: str, user_id: str, redirect_uri: str, scope: str, resource: str,
                   code_challenge_value: str, now: int) -> Authorization:
        code = mint_secret(CODE_PREFIX)
        with self.db.transaction() as tx:
            tx.execute(
                INSERT_CODE_SQL,
                (hash_secret("oauth-code", code[len(CODE_PREFIX):]), client_id, user_id, redirect_uri, scope,
                 resource, code_challenge_value, now, now + CODE_TTL_S),
            )
            audit_mod.record(tx, now, "oauth_authorized", user_id=user_id, ref=client_id)
        return Authorization(code, client_id, user_id, redirect_uri, scope, resource, code_challenge_value,
                             now + CODE_TTL_S)

    def consume_code(self, code: object, client_id: str, redirect_uri: str, verifier: str, now: int) -> Authorization:
        """Redeem a code: one use only, and only with the right PKCE verifier."""
        secret = split_secret(code, CODE_PREFIX)
        if secret is None:
            raise OAuthError("invalid_grant", "code_format")
        digest = hash_secret("oauth-code", secret)
        with self.db.transaction() as tx:
            row = tx.fetchone(SELECT_CODE_SQL, (digest,))
            if row is None:
                raise OAuthError("invalid_grant", "code_unknown")
            if row["used_at"] is not None:
                raise OAuthError("invalid_grant", "code_used")
            if row["expires_at"] <= now:
                raise OAuthError("invalid_grant", "code_expired")
            if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
                raise OAuthError("invalid_grant", "code_binding")
            if not challenge_matches(verifier, row["code_challenge"]):
                raise OAuthError("invalid_grant", "code_challenge")
            if tx.execute(USE_CODE_SQL, (now, digest)) != 1:
                raise OAuthError("invalid_grant", "code_used")
            audit_mod.record(tx, now, "oauth_code_redeemed", user_id=row["user_id"], ref=client_id)
            return Authorization("", client_id, row["user_id"], row["redirect_uri"], row["scope"], row["resource"],
                                 row["code_challenge"], row["expires_at"])

    # -- tokens -------------------------------------------------------------------------------------------------
    def issue_tokens(self, client_id: str, user_id: str, scope: str, now: int, *,
                     resource: str = "", chain_id: Optional[str] = None) -> IssuedTokens:
        """Issue an access token and a refresh token. The plaintext pair exists once."""
        access = mint_secret(ACCESS_PREFIX)
        refresh = mint_secret(REFRESH_PREFIX)
        access_id = mint_client_id()
        refresh_id = mint_client_id()
        with self.db.transaction() as tx:
            tx.execute(
                INSERT_ACCESS_SQL,
                (access_id, hash_secret("oauth-access", access[len(ACCESS_PREFIX):]), client_id, user_id, scope,
                 resource, now, now + ACCESS_TTL_S),
            )
            tx.execute(
                INSERT_REFRESH_SQL,
                (refresh_id, hash_secret("oauth-refresh", refresh[len(REFRESH_PREFIX):]), client_id, user_id, scope,
                 resource, chain_id or refresh_id, now, now + REFRESH_TTL_S),
            )
            audit_mod.record(tx, now, "oauth_token_issued", user_id=user_id, ref=client_id)
        return IssuedTokens(access, refresh, scope, ACCESS_TTL_S, now + REFRESH_TTL_S)

    def rotate(self, refresh_token: object, now: int) -> IssuedTokens:
        """Use a refresh token once: a new pair comes back, the old one dies.

        Using a token that was already rotated is a reuse: the whole chain dies.
        """
        secret = split_secret(refresh_token, REFRESH_PREFIX)
        if secret is None:
            raise OAuthError("invalid_grant", "refresh_format")
        digest = hash_secret("oauth-refresh", secret)
        reuse = False
        access = refresh = ""
        scope = "read"
        with self.db.transaction() as tx:
            row = tx.fetchone(SELECT_REFRESH_SQL, (digest,))
            if row is None:
                raise OAuthError("invalid_grant", "refresh_unknown")
            scope = row["scope"]
            if row["revoked_at"] is not None:
                # Reuse of a rotated token: the chain is compromised. The revocation is committed
                # BEFORE the refusal leaves this function, so it cannot be rolled back with it.
                tx.execute(REVOKE_CHAIN_SQL, (now, row["chain_id"]))
                tx.execute(REVOKE_CLIENT_SQL, (now, row["client_id"]))
                audit_mod.record(tx, now, "oauth_token_reuse_detected", user_id=row["user_id"],
                                ref=row["client_id"])
                reuse = True
            elif row["expires_at"] <= now:
                raise OAuthError("invalid_grant", "refresh_expired")
            else:
                access = mint_secret(ACCESS_PREFIX)
                refresh = mint_secret(REFRESH_PREFIX)
                access_id = mint_client_id()
                refresh_id = mint_client_id()
                tx.execute(
                    INSERT_ACCESS_SQL,
                    (access_id, hash_secret("oauth-access", access[len(ACCESS_PREFIX):]), row["client_id"],
                     row["user_id"], row["scope"], row["resource"], now, now + ACCESS_TTL_S),
                )
                tx.execute(
                    INSERT_REFRESH_SQL,
                    (refresh_id, hash_secret("oauth-refresh", refresh[len(REFRESH_PREFIX):]), row["client_id"],
                     row["user_id"], row["scope"], row["resource"], row["chain_id"], now, now + REFRESH_TTL_S),
                )
                if tx.execute(ROTATE_REFRESH_SQL, (now, refresh_id, row["id"])) != 1:
                    raise OAuthError("invalid_grant", "refresh_reused")
                audit_mod.record(tx, now, "oauth_token_refreshed", user_id=row["user_id"], ref=row["client_id"])
        if reuse:
            raise OAuthError("invalid_grant", "refresh_reused")
        return IssuedTokens(access, refresh, scope, ACCESS_TTL_S, now + REFRESH_TTL_S)

    def verify_access_token(self, token: object, resource: str, now: int) -> AccessVerification:
        """Check an access token: shape, hash, life, audience, and the user behind it."""
        secret = split_secret(token, ACCESS_PREFIX)
        if secret is None:
            return AccessVerification(False, "format")
        digest = hash_secret("oauth-access", secret)
        with self.db.transaction() as tx:
            row = tx.fetchone(SELECT_ACCESS_SQL, (digest,))
            if row is None:
                return AccessVerification(False, "unknown")
            if not consteq(row["token_hash"], digest):
                return AccessVerification(False, "mismatch")
            if row["revoked_at"] is not None:
                return AccessVerification(False, "revoked")
            if row["expires_at"] <= now:
                return AccessVerification(False, "expired")
            if row["resource"] and row["resource"] != resource:
                return AccessVerification(False, "audience")
            if row["status"] != "active":
                return AccessVerification(False, "disabled")
            tx.execute(TOUCH_ACCESS_SQL, (now, row["id"]))
        effective = "write" if row["scope"] == "write" and row["can_write"] else "read"
        return AccessVerification(True, "ok", row["id"], row["user_id"], effective, row["client_id"], row["expires_at"])

    def cleanup(self, now: int, limit: int = 200) -> int:
        """Delete what is past its life. Bounded, so it can run in a housekeeping pass."""
        cutoff = int(now) - 30 * 86400
        removed = 0
        with self.db.transaction() as tx:
            removed += max(tx.execute(CLEANUP_CODES_SQL, (now, limit)), 0)
            removed += max(tx.execute(CLEANUP_ACCESS_SQL, (now, limit)), 0)
            removed += max(tx.execute(CLEANUP_REFRESH_SQL, (now, cutoff, limit)), 0)
            removed += max(tx.execute(CLEANUP_CLIENTS_SQL, (now, limit)), 0)
        return removed
