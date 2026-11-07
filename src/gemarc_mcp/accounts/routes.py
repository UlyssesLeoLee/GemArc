"""Account routes and the runtime that owns the accounts services (design 3.2, 3.3, 3.5, 3.7, 3.12, 3.13).

``AccountsRuntime`` is built by ``api/index.py`` only when ``GEMARC_ACCOUNTS=1`` and the configuration is complete.
It owns the database handle, the stores, the identity-provider client, the in-process limiter and PAT cache, and the
two bounded executors that keep blocking driver calls off the event loop.

Every response, including errors and redirects, is built by ``pages.py`` (one header function). Every route is
wrapped in a catch-all that logs only an event id and the exception CLASS NAME and answers a generic 5xx. Nothing
here logs a token, an e-mail, an address, a code, a cookie or a secret.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import anyio
from starlette.datastructures import Headers

from .. import i18n
from . import admin as admin_mod
from . import audit as audit_mod
from . import oidc, pages, retention, schema, security
from . import users as users_mod
from .admin import AdminService
from .config import AccountsConfig
from .crypto import Crypto, consteq
from .db import DBError, Database, KeyRingMismatch, SchemaOutdated
from .mcpauth import McpAuth, PatCache
from .oidc import AccessPolicy, ClaimsError, GoogleClient, IdpEndpoints, IdpError, OAuthStates
from .pages import CONFIRM_WORD, View
from .ratelimit import RateLimiter
from .sessions import SessionError, SessionInfo, SessionPolicy, SessionStore, valid_sid_format
from .tokens import TokenError, TokenPolicy, TokenStore
from .users import UserAccounts

log = logging.getLogger("gemarc.accounts")

ACCOUNT_PATH = "/account"
#: Where a successful login goes. Fixed: no ``next`` parameter is read, so there is no open redirect.
POST_LOGIN_PATH = "/account"
REDIRECT_ALLOWLIST = (POST_LOGIN_PATH,)
PREFILTER_LIMIT = 120
PREFILTER_LIMIT_NO_IP = 300
PREFILTER_WINDOW_S = 60
REVOKE_PATH = re.compile(r"^/account/tokens/([a-z2-7]{8})/revoke\Z")
#: The operator area. Every path under it needs an administrator session; the actions are the four
#: verbs the admin page offers (enable, disable, grant-admin, revoke-admin, revoke-tokens).
ADMIN_PATH = "/account/admin"
ADMIN_USER_PATH = re.compile(r"^/account/admin/users/([A-Za-z0-9_-]{1,64})/"
                             r"(enable|disable|grant-admin|revoke-admin|revoke-tokens)\Z")
ADMIN_SETTINGS_PATH = "/account/admin/settings"
ADMIN_SITE_PATH = "/account/admin/site"
DRAIN_TIMEOUT_S = 2.0
DRAIN_LIMIT = 1 << 20
#: How long the cookie that remembers the language of the reader lives (one year).
LANG_COOKIE_MAX_AGE = 365 * 86400

#: The error texts of the account routes. They live in the language tables (the `messages`
#: section), so an error page speaks the language of the reader (DD-009).
MESSAGES = {lang: i18n.section(lang, "messages") for lang in i18n.LANGUAGES}
#: The language of the request being handled. The error pages come from many places (a
#: limit, a stale session, a bad token, a wrong host), and every one of them must speak the
#: language of the reader: the request carries it, the pages take it from here.
REQUEST_LANG: "contextvars.ContextVar[str]" = contextvars.ContextVar("gemarc_request_lang", default="")
TOKEN_ERROR_STATUS = {"GEMARC_ACCT_034": 400, "GEMARC_ACCT_033": 409, "GEMARC_ACCT_035": 403, "GEMARC_ACCT_013": 403}
DB_ERROR_STATE = {"GEMARC_ACCT_002": "unreachable", "GEMARC_ACCT_003": "unreachable", "GEMARC_ACCT_004": "misconfigured"}


class Early(Exception):
    """Leave a handler at once with this response."""

    def __init__(self, response) -> None:
        super().__init__("early")
        self.response = response


@dataclass
class Req:
    method: str
    path: str
    headers: Headers
    scope: dict
    reader: security.BodyReader

    @property
    def query(self) -> "Optional[dict[str, str]]":
        return security.parse_query(self.scope.get("query_string", b""))

    @property
    def lang(self) -> str:
        """The language of this request (DD-009): the explicit choice, then the cookie,
        then the browser, then the default."""
        query = self.query or {}
        return i18n.resolve(query=query.get("lang"),
                            cookie_header=self.headers.get("cookie"),
                            accept_language=self.headers.get("accept-language"))

    @property
    def lang_chosen(self) -> bool:
        """True when the request carries an explicit language choice (the browser guess
        is not a choice and is not remembered)."""
        return i18n.normalize((self.query or {}).get("lang")) is not None


@dataclass
class Authed:
    info: Any  # SessionInfo
    sid: str
    form: "dict[str, str]"
    now: int
    lang: str = ""


@dataclass
class LoginOutcome:
    status: str  # ok | denied
    sid: str = ""
    reason: str = ""


class AccountsRuntime:
    """The accounts feature for one app instance. Constructing it never touches the network or the database."""

    def __init__(self, config: AccountsConfig, *, idp: Optional[IdpEndpoints] = None,
                 clock: Optional[Callable[[], float]] = None, environ: Optional[dict] = None,
                 db: Optional[Database] = None, opener: Any = None,
                 limit_overrides: Optional["dict[str, tuple[int, int]]"] = None,
                 prefilter: "tuple[int, int]" = (PREFILTER_LIMIT, PREFILTER_LIMIT_NO_IP),
                 registered_tools: "tuple[str, ...]" = (), write_tools: "tuple[str, ...]" = (),
                 allow_write: bool = False, site_status: Optional[Callable[[], dict]] = None,
                 site_rebuild: Optional[Callable[[], str]] = None) -> None:
        self.config = config
        self.clock = clock or time.time
        self.environ = environ if environ is not None else {}
        self.crypto = Crypto(config.keyring)
        self.db = db or Database.from_target(config.db, enforce_role=config.db.kind == "postgres",
                                            role_relaxed=config.db_role_relaxed)
        self.users = UserAccounts(self.db, self.crypto)
        self.admin = AdminService(self.db, self.crypto, config.admin_emails)
        self.sessions = SessionStore(self.db, self.crypto, SessionPolicy.from_config(config))
        self.tokens = TokenStore(self.db, self.crypto, TokenPolicy.from_config(config),
                                 policy_provider=self._token_policy)
        self.audit = audit_mod.Audit(self.db, self.crypto, config.audit_retention_days)
        self.limiter = RateLimiter(self.db, self.crypto)
        self.states = OAuthStates(self.db)
        self.policy = AccessPolicy(config.allowed_domains, config.allowed_emails, self.crypto)
        self.google = GoogleClient(idp or IdpEndpoints.google(), config.google_client_id, config.google_client_secret,
                                   config.base_url + oidc.CALLBACK_PATH, opener=opener, clock=self.clock)
        self.memory = security.MemoryLimiter(self.clock)
        self.pat_cache = PatCache(config.pat_cache_seconds, config.pat_negcache_seconds, self.clock)
        self.anon_executor = security.BoundedExecutor(3, 6, "gemarc-acct-anon")
        self.pat_executor = security.BoundedExecutor(6, 24, "gemarc-acct-pat")
        #: Network calls to the identity provider run on their OWN small pool, so a slow upstream can never starve
        #: the database workers that every account route needs.
        self.idp_executor = security.BoundedExecutor(2, 4, "gemarc-acct-idp")
        self._last_housekeeping = 0
        self.limit_overrides = dict(limit_overrides or {})
        self.prefilter = prefilter
        self.registered_tools = tuple(registered_tools)
        self.write_tools = tuple(write_tools)
        #: A write-scope token can be created only when the toolset has write tools AND the operator allows writes.
        self.write_scope = bool(write_tools) and allow_write
        #: The application owns the static site; the operator area may ask it for a rebuild. Both
        #: callables are optional: without them the area shows no site section.
        self.site_status = site_status
        self.site_rebuild = site_rebuild
        self.view = View(config.base_url, config.privacy_url, self.write_scope, config.pat_default_days,
                         config.pat_max_days, config.secure_cookies)
        self.sid_cookie = "__Host-gemarc_sid" if config.secure_cookies else "gemarc_sid"
        self.bind_cookie = "__Host-gemarc_oauth" if config.secure_cookies else "gemarc_oauth"
        #: ok | unreachable | misconfigured: the outcome of the last database operation (healthz never queries).
        self.db_state = "ok"
        self._ready = False
        self._ready_lock = threading.Lock()

    # -- small services -------------------------------------------------------------------------------------------
    def _token_policy(self) -> TokenPolicy:
        """The token policy in force: the stored overrides on top of the environment defaults.

        The environment stays the source of the DEFAULTS, so a deployment that never opens the
        operator area behaves exactly as before. A stored value that is out of range is ignored
        (the environment wins) rather than clamped: the admin page refuses such a value anyway.
        """
        base = TokenPolicy.from_config(self.config)

        def number(key: str, fallback: int) -> int:
            raw = self.admin.settings().get(key)
            if raw is None:
                return fallback
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return fallback
            low, high = admin_mod.SETTING_BOUNDS.get(key, (1, 365))
            return value if low <= value <= high else fallback

        default_days = number(admin_mod.PAT_DEFAULT_DAYS, base.default_days)
        max_days = number(admin_mod.PAT_MAX_DAYS, base.max_days)
        per_user = number(admin_mod.PAT_MAX_PER_USER, base.max_per_user)
        if default_days > max_days:
            default_days = max_days
        return TokenPolicy(default_days, max_days, per_user, base.last_used_throttle)

    def now(self) -> int:
        return int(self.clock())

    def client_ip(self, headers: Any) -> Optional[str]:
        return security.trusted_ip(headers, self.environ, self.config.trusted_proxy_hops)

    def client_key(self, scope: dict) -> str:
        """Per-client key for in-memory counters: the daily ip hash. With no trusted address (not on Vercel and no
        proxy hops configured, or the header is missing) the key is ``no-ip:`` plus a short hash of the connection
        address, ``User-Agent`` and ``Accept``, so one client cannot exhaust a bucket shared by every user. These
        keys are a load guard only; they are not a security boundary (a client can vary its headers)."""
        headers = Headers(scope=scope)
        ip = self.client_ip(headers)
        if ip:
            try:
                return self.crypto.ip_hash(ip, self.now())
            except ValueError:
                pass
        peer = scope.get("client") or ("", 0)
        material = "|".join((str(peer[0]), headers.get("user-agent") or "", headers.get("accept") or ""))
        return "no-ip:" + hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:16]

    def ensure_ready(self) -> None:
        """First database use of this instance: the schema must be current (or is migrated when AUTO_MIGRATE=1)."""
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            if self.config.auto_migrate:
                schema.migrate(self.db, self.now())
            schema.require_current(self.db)
            if self.users.unknown_key_ids():
                # A key id was dropped from GEMARC_ACCOUNTS_KEYS before ``rekey`` finished. Serving would silently
                # create duplicate accounts and orphan the old rows, so refuse until the ring is repaired.
                raise KeyRingMismatch("key_missing_from_ring")
            self._ready = True

    def _guarded(self, fn: Callable, *args: Any) -> Any:
        self.ensure_ready()
        return fn(*args)

    async def _run(self, executor: security.BoundedExecutor, fn: Callable, *args: Any) -> Any:
        try:
            result = await executor.run(self._guarded, fn, *args)
        except DBError as exc:
            self.db_state = DB_ERROR_STATE.get(exc.code, "unreachable")
            raise
        except schema.MigrationError:
            self.db_state = "unreachable"
            raise SchemaOutdated("migration_state") from None
        self.db_state = "ok"
        return result

    async def run_db(self, fn: Callable, *args: Any) -> Any:
        """Blocking database work for account routes (small executor, queue bounded)."""
        return await self._run(self.anon_executor, fn, *args)

    async def run_pat_db(self, fn: Callable, *args: Any) -> Any:
        """Blocking database work for PAT validation (reserved executor)."""
        return await self._run(self.pat_executor, fn, *args)

    async def run_idp(self, fn: Callable, *args: Any) -> Any:
        """Blocking identity-provider network work (own pool; every call has a total deadline in ``oidc._fetch``)."""
        return await self.idp_executor.run(fn, *args)

    def housekeeping(self, now: int) -> None:
        """Bounded retention pass (audit, rate limits, sessions, tokens, OAuth states), at most every five minutes per
        instance. Best effort: never raises, never delays more than one small batch per table."""
        if now - self._last_housekeeping < retention.MIN_INTERVAL_S:
            return
        self._last_housekeeping = now
        try:
            retention.run_once(self.db, sessions=self.sessions, tokens=self.tokens, limiter=self.limiter,
                               retention_days=self.config.audit_retention_days, now=now)
        except Exception:  # best effort: a clean-up problem must never fail a login or hide a new token
            pass

    async def audit_async(self, event: str, *, scope: Optional[dict] = None, ref: Optional[str] = None,
                          user_id: Optional[str] = None) -> None:
        ip = self.client_ip(Headers(scope=scope)) if scope is not None else None
        now = self.now()
        await self.run_pat_db(lambda: self.audit.event(now, event, ip=ip, ref=ref, user_id=user_id))

    def mcp_auth(self, mode: str, env_match: Callable, *, registered_tools: "tuple[str, ...]" = (),
                 write_tools: "tuple[str, ...]" = ()) -> McpAuth:
        return McpAuth(self, mode, env_match, registered_tools=registered_tools or self.registered_tools,
                       write_tools=write_tools or self.write_tools)

    # -- response builders ----------------------------------------------------------------------------------------
    @property
    def hsts(self) -> bool:
        return self.config.secure_cookies

    def _html(self, status: int, render: Callable[[str], str], *, cookies=(), headers=None,
              lang: str = "", lang_cookie: bool = False):
        nonce = pages.new_nonce()
        cookies = list(cookies)
        if lang_cookie and lang:
            cookies.append(security.cookie_header(i18n.LANG_COOKIE, lang, secure=self.config.secure_cookies,
                                                  max_age=LANG_COOKIE_MAX_AGE))
        return pages.respond(status, render(nonce), nonce=nonce, hsts=self.hsts, cookies=cookies, headers=headers)

    def _clear_sid(self) -> str:
        return security.clear_cookie_header(self.sid_cookie, secure=self.config.secure_cookies)

    def error(self, status: int, code: str, *, email: Optional[str] = None, relogin: bool = True, cookies=(),
              retry_after: Optional[int] = None, lang: str = ""):
        headers = {"Retry-After": str(max(int(retry_after), 1))} if retry_after else None
        # No language given: the one of the request being handled (DD-009).
        lang = lang or REQUEST_LANG.get()
        message = MESSAGES.get(lang, {}).get(code) or MESSAGES.get("zh-CN", {}).get(code) or code
        return self._html(status, lambda n: pages.error_page(n, code, message, lang=lang, email=email,
                                                            relogin=relogin),
                          cookies=cookies, headers=headers, lang=lang)

    def not_found(self):
        return pages.json_response(404, {"error": "not_found"}, hsts=self.hsts)

    # -- entry point ----------------------------------------------------------------------------------------------
    async def handle(self, scope: dict, receive: Callable, send: Callable) -> None:
        reader = security.BodyReader(receive)
        req = Req(scope["method"], scope["path"], Headers(scope=scope), scope, reader)
        # The language of this request, for every page that is built on the way (the error
        # pages above all): the reader asked for it with the header, the cookie or the link.
        REQUEST_LANG.set(req.lang)
        try:
            response = await self._route(req)
        except Early as early:
            response = early.response
        except security.Busy:
            response = self.error(503, "GEMARC_ACCT_023", retry_after=1)
        except DBError as exc:
            response = self.error(503, exc.code)
        except Exception as exc:  # catch-all: the event id and the class name are all that is logged
            event = secrets.token_hex(4)
            log.error("accounts_event id=%s class=%s", event, type(exc).__name__)
            response = self.error(500, "GEMARC_ACCT_001")
        if not reader.complete:
            with anyio.move_on_after(DRAIN_TIMEOUT_S):
                try:
                    await reader.read(DRAIN_LIMIT)
                except security.BodyTooLarge:
                    pass
        await response(scope, receive, send)

    async def _route(self, req: Req):
        headers = req.headers
        if (headers.get("host") or "").lower() != self.config.host.lower():
            return self.error(421, "GEMARC_ACCT_001", relogin=False)
        path, method = req.path, req.method
        table = self._table(path)
        if table is None:
            return self.not_found()
        allowed, handler = table
        if method == "OPTIONS":
            return pages.respond(204, b"", content_type=None, hsts=self.hsts,
                                 headers={"Allow": ", ".join((*allowed, "OPTIONS"))})
        if method not in allowed:
            return pages.json_response(405, {"error": "method_not_allowed"}, hsts=self.hsts,
                                       headers={"Allow": ", ".join((*allowed, "OPTIONS"))})
        key = self.client_key(req.scope)
        limit = self.prefilter[1] if key.startswith("no-ip:") else self.prefilter[0]
        ok, _count, retry = self.memory.hit("account_prefilter", key, limit, PREFILTER_WINDOW_S)
        if not ok:
            return self.error(429, "GEMARC_ACCT_022", retry_after=retry)
        return await handler(req)

    def _table(self, path: str):
        fixed = {
            "/auth/google/start": (("GET",), self._start),
            "/auth/google/callback": (("GET",), self._callback),
            "/auth/logout": (("POST",), self._logout),
            "/auth/logout-all": (("POST",), self._logout_all),
            "/account": (("GET", "HEAD"), self._account),
            "/account/tokens": (("GET", "HEAD", "POST"), self._tokens),
            "/account/tokens/revoke-all": (("POST",), self._revoke_all),
            "/account/export": (("POST",), self._export),
            "/account/delete": (("POST",), self._delete),
            ADMIN_PATH: (("GET", "HEAD"), self._admin_page),
            ADMIN_SETTINGS_PATH: (("POST",), self._admin_settings),
            ADMIN_SITE_PATH: (("POST",), self._admin_site),
        }
        if path in fixed:
            return fixed[path]
        if REVOKE_PATH.match(path):
            return (("POST",), self._revoke_one)
        if ADMIN_USER_PATH.match(path):
            return (("POST",), self._admin_user)
        return None

    # -- rate limits ----------------------------------------------------------------------------------------------
    def _hit(self, scope: str, subject: str, now: int):
        override = self.limit_overrides.get(scope)
        if override:
            return self.limiter.hit(now, scope, subject, limit=override[0], window=override[1])
        return self.limiter.hit(now, scope, subject)

    async def _limit(self, scope: str, subject: str, now: int) -> None:
        result = await self.run_db(self._hit, scope, subject, now)
        if not result.allowed:
            raise Early(self.error(429, "GEMARC_ACCT_022", retry_after=result.retry_after))

    async def _limit_ip(self, scope: str, req: Req, now: int) -> "Optional[str]":
        """Database counter per client (only with a trusted address). Returns the ip hash or None."""
        ip = self.client_ip(req.headers)
        if not ip:
            return None
        try:
            ip_hash = self.crypto.ip_hash(ip, now)
        except ValueError:
            return None
        await self._limit(scope, ip_hash, now)
        return ip_hash

    # -- login ------------------------------------------------------------------------------------------------------
    async def _start(self, req: Req):
        if not security.fetch_metadata_ok(req.headers):
            return self.error(403, "GEMARC_ACCT_021")
        now = self.now()
        await self._limit_ip("login_start", req, now)
        binding = security.single_cookie(req.headers, self.bind_cookie)
        if binding is None or not security.B64U43_RE.match(binding):
            binding = oidc.new_random()
        state, nonce = oidc.new_random(), oidc.new_random()
        verifier = oidc.derive_verifier(state, binding)
        url = oidc.authorization_url(self.google.endpoints, client_id=self.config.google_client_id,
                                     redirect_uri=self.google.redirect_uri, state=state, nonce=nonce,
                                     challenge=oidc.code_challenge(verifier))
        await self.run_db(self.states.create, state, nonce, binding, now)
        cookie = security.cookie_header(self.bind_cookie, binding, secure=self.config.secure_cookies,
                                        max_age=oidc.STATE_TTL_S)
        return pages.redirect(url, 302, hsts=self.hsts, cookies=[cookie])

    def _deny(self, now: int, reason: str, ip: Optional[str]) -> None:
        """Best-effort audit of a failed login; never raises and never carries the e-mail."""
        try:
            self.audit.event(now, "login_denied", ip=ip, code=reason)
        except Exception:
            pass

    async def _audit_denied(self, now: int, reason: str, ip: Optional[str]) -> None:
        try:
            await self.run_db(self._deny, now, reason, ip)
        except Exception:
            pass

    async def _callback(self, req: Req):
        now = self.now()
        ip = self.client_ip(req.headers)
        await self._limit_ip("login_callback", req, now)
        # The pre-login cookie is NOT cleared here: it is a reusable per-browser secret (D22), so a second tab that
        # started its own login keeps working; it expires by itself after 10 minutes.
        clear: list = []
        query = req.query
        if query is None:
            return self.error(400, "GEMARC_ACCT_050", cookies=clear)
        if "error" in query:
            await self._audit_denied(now, "idp_error", ip)
            return self.error(502, "GEMARC_ACCT_012", cookies=clear)
        code, state = query.get("code", ""), query.get("state", "")
        if not code or len(code.encode("utf-8")) > oidc.CODE_MAX_BYTES or not security.B64U43_RE.match(state):
            await self._audit_denied(now, "state", ip)
            return self.error(400, "GEMARC_ACCT_010", cookies=clear)
        binding = security.single_cookie(req.headers, self.bind_cookie)
        if binding is None or not security.B64U43_RE.match(binding):
            await self._audit_denied(now, "binding", ip)
            return self.error(400, "GEMARC_ACCT_011", cookies=clear)
        record = await self.run_db(self.states.consume, state, now)
        if record is None:
            await self._audit_denied(now, "state", ip)
            return self.error(400, "GEMARC_ACCT_010", cookies=clear)
        if not consteq(oidc.binding_hash(state, binding), record.binding_hash):
            await self._audit_denied(now, "binding", ip)
            return self.error(400, "GEMARC_ACCT_011", cookies=clear)
        issuer = query.get("iss")
        if issuer is not None and issuer != "https://accounts.google.com":
            await self._audit_denied(now, "exchange", ip)
            return self.error(502, "GEMARC_ACCT_012", cookies=clear)
        verifier = oidc.derive_verifier(state, binding)
        try:
            token = await self.run_idp(self.google.exchange_code, code, verifier)
        except IdpError:
            await self._audit_denied(now, "exchange", ip)
            return self.error(502, "GEMARC_ACCT_012", cookies=clear)
        try:
            claims = await self.run_idp(self._validate, token, record.nonce_hash)
        except ClaimsError as exc:
            await self._audit_denied(now, "unverified" if exc.reason == "unverified" else "claims", ip)
            return self.error(403, "GEMARC_ACCT_013", relogin=True, cookies=clear)
        refused = self.policy.check(claims)
        if refused is not None:
            await self._audit_denied(now, refused, ip)
            return self.error(403, "GEMARC_ACCT_013", email=claims.email, cookies=clear)
        old_sid = security.single_cookie(req.headers, self.sid_cookie)
        outcome = await self.run_db(self._complete_login, claims, ip, old_sid, now)
        if outcome.status != "ok":
            return self.error(403, "GEMARC_ACCT_013", email=claims.email, cookies=clear)
        session_cookie = security.cookie_header(self.sid_cookie, outcome.sid, secure=self.config.secure_cookies)
        return pages.redirect(POST_LOGIN_PATH, 303, hsts=self.hsts, cookies=[session_cookie, *clear])

    def _validate(self, token: str, nonce_hash: str) -> oidc.Claims:
        """Claim checks (and, with VERIFY_SIG, the signature check which may fetch the JWKS): blocking."""
        return oidc.validate_id_token(token, client_id=self.config.google_client_id, nonce_hash=nonce_hash,
                                      now=self.clock(), google=self.google, verify_sig=self.config.verify_sig)

    def _exists(self, sub: str) -> bool:
        with self.db.transaction() as tx:
            for candidate in self.crypto.blind_index_all("sub", sub):
                if tx.fetchone(users_mod.SELECT_BY_SUB_SQL, (candidate,)):
                    return True
        return False

    def _complete_login(self, claims: oidc.Claims, ip: Optional[str], old_sid: Optional[str], now: int) -> LoginOutcome:
        """Everything after the identity is verified and allowed: registration limits, user upsert, session, audit."""
        ip_hash = None
        if ip:
            try:
                ip_hash = self.crypto.ip_hash(ip, now)
            except ValueError:
                ip_hash = None
        if not self._exists(claims.sub):
            # Check-then-consume, per-address budget FIRST: a client that is already over its own limit must never
            # spend the shared budget (otherwise one address with a few Google accounts could exhaust registration
            # for every other new user). Only an attempt that passed the address check counts against the global one.
            allowed = True
            if ip_hash:
                allowed = self._hit("signup_ip", ip_hash, now).allowed
            if allowed:
                allowed = self._hit("signup_global", "global", now).allowed
            if not allowed:
                if self.limiter.hit(now, "signup_saturated_note", "global", limit=1, window=3600).allowed:
                    self.audit.event(now, "signup_saturated", ip=None)
                self._deny(now, "closed", ip)
                return LoginOutcome("denied", reason="closed")
        result = self.users.login(claims.sub, claims.email, claims.has_hd, now, max_users=self.config.max_users,
                                  ip_hash=ip_hash)
        if result.status != "active" or not result.user_id:
            self._deny(now, "closed" if result.status == "closed" else "disabled", ip)
            return LoginOutcome("denied", reason=result.status)
        for old in result.recycled:
            self.pat_cache.evict_user(old)
        try:
            created = self.sessions.create(result.user_id, now, replace_sid=old_sid)
        except SessionError:
            self._deny(now, "disabled", ip)
            return LoginOutcome("denied", reason="disabled")
        self.audit.event(now, "login_ok", ip=ip, user_id=result.user_id)
        self.housekeeping(now)  # bounded and throttled; failures are swallowed inside
        return LoginOutcome("ok", sid=created.sid)

    # -- session helpers --------------------------------------------------------------------------------------------
    async def _session(self, req: Req, now: int):
        """(SessionInfo | None, sid | None). No database access without a well-formed single cookie."""
        sid = security.single_cookie(req.headers, self.sid_cookie)
        if sid is None or not valid_sid_format(sid):
            return None, None
        info = await self.run_db(lambda: self.sessions.lookup(sid, now, policy_check=self.policy.row_ok))
        return (info, sid) if info is not None else (None, None)

    def _account_data(self, user_id: str):
        return self.users.email_of(user_id), self.tokens.list(user_id)

    async def _render_account(self, info, sid: str, now: int, *, created: Optional[dict] = None,
                              message: Optional[str] = None, status: int = 200, cookies=(),
                              lang: str = "", lang_cookie: bool = False):
        email, tokens = await self.run_db(self._account_data, info.user_id)
        csrf = self.crypto.csrf_token(sid)
        recent = self.sessions.is_recent(info, now)
        # The policy in force, not the environment defaults: the operator area can change it while
        # the deployment runs, and the form must not offer a longer lifetime than the store allows.
        policy = await self.run_db(self.tokens.effective_policy)
        is_admin = await self.run_db(self.admin.is_admin, info.user_id, email)
        return self._html(status, lambda n: pages.account_page(self.view, n, lang=lang, email=email or "",
                                                               csrf=csrf, tokens=tokens, now=now, recent=recent,
                                                               created=created, message=message,
                                                               default_days=policy.default_days, max_days=policy.max_days,
                                                               is_admin=is_admin),
                          cookies=cookies, lang=lang, lang_cookie=lang_cookie)

    async def _account(self, req: Req):
        now = self.now()
        info, sid = await self._session(req, now)
        if info is None:
            stale = security.has_cookie(req.headers, self.sid_cookie)
            cookies = [self._clear_sid()] if stale else []
            return self._html(200, lambda n: pages.signed_out_page(self.view, n, lang=req.lang), cookies=cookies,
                              lang=req.lang, lang_cookie=req.lang_chosen)
        return await self._render_account(info, sid, now, lang=req.lang, lang_cookie=req.lang_chosen)

    async def _tokens(self, req: Req):
        now = self.now()
        if req.method == "POST":
            return await self._post(req, self._create_token, recent=True)
        info, sid = await self._session(req, now)
        if info is None:
            return pages.redirect(ACCOUNT_PATH, 303, hsts=self.hsts)
        return await self._render_account(info, sid, now, lang=req.lang, lang_cookie=req.lang_chosen)

    # -- state-changing routes --------------------------------------------------------------------------------------
    async def _post(self, req: Req, action: Callable, *, recent: bool, limit_scope: Optional[str] = None):
        """Common pipeline of every POST: origin, session, body, CSRF token, per-user limit, step-up, action."""
        cfg = self.config
        if not security.origin_ok(req.headers, cfg.origin):
            return self.error(403, "GEMARC_ACCT_021")
        now = self.now()
        info, sid = await self._session(req, now)
        if info is None:
            stale = security.has_cookie(req.headers, self.sid_cookie)
            return self.error(401, "GEMARC_ACCT_020", cookies=[self._clear_sid()] if stale else ())
        ctype = (req.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != security.FORM_CONTENT_TYPE:
            return self.error(415, "GEMARC_ACCT_050")
        try:
            body = await req.reader.read(security.MAX_FORM_BYTES)
        except security.BodyTooLarge:
            return self.error(413, "GEMARC_ACCT_050")
        form = security.parse_form(body)
        if form is None:
            return self.error(400, "GEMARC_ACCT_050")
        if not self.crypto.verify_csrf(sid, form.get("csrf", "")):
            return self.error(403, "GEMARC_ACCT_021")
        await self._limit("account_post", info.user_id, now)
        if recent and not self.sessions.is_recent(info, now):
            return self.error(403, "GEMARC_ACCT_040")
        return await action(req, Authed(info, sid, form, now, req.lang))

    async def _logout(self, req: Req):
        return await self._post(req, self._do_logout, recent=False)

    def _logout_work(self, sid: str, user_id: str, now: int) -> None:
        self.sessions.revoke(sid)
        self.audit.event(now, "logout", user_id=user_id)

    async def _do_logout(self, req: Req, a: Authed):
        await self.run_db(self._logout_work, a.sid, a.info.user_id, a.now)
        return pages.redirect(ACCOUNT_PATH, 303, hsts=self.hsts, cookies=[self._clear_sid()])

    async def _logout_all(self, req: Req):
        return await self._post(req, self._do_logout_all, recent=True)

    async def _do_logout_all(self, req: Req, a: Authed):
        await self.run_db(self.sessions.revoke_all, a.info.user_id, a.now)
        self.pat_cache.evict_user(a.info.user_id)
        return pages.redirect(ACCOUNT_PATH, 303, hsts=self.hsts, cookies=[self._clear_sid()])

    async def _create_token(self, req: Req, a: Authed):
        form = a.form
        days_raw = form.get("days", "").strip()
        if days_raw and not (days_raw.isascii() and days_raw.isdigit() and len(days_raw) <= 4):
            return self.error(400, "GEMARC_ACCT_034", relogin=False)
        days = int(days_raw) if days_raw else None
        scope = form.get("scope", "read") or "read"
        await self._limit("pat_create", a.info.user_id, a.now)
        try:
            created = await self.run_db(self._create_and_tidy, a, form.get("name", ""), scope, days)
        except TokenError as exc:
            return self.error(TOKEN_ERROR_STATUS.get(exc.code, 400), exc.code, relogin=False)
        return await self._render_account(a.info, a.sid, a.now, created={"token": created.token, "id": created.id},
                                          message=i18n.text(a.lang, "accounts", "account.tokencreated"),
                                          lang=a.lang)

    def _create_and_tidy(self, a: Authed, name: str, scope: str, days: Optional[int]):
        """Create the token and run the (throttled, best-effort) retention pass in ONE executor job: a second
        awaited job could be refused as busy after the token already exists, and its plaintext would be lost."""
        created = self.tokens.create(a.info.user_id, name, a.now, scope=scope, days=days, allow_write=self.write_scope)
        self.housekeeping(a.now)
        return created

    async def _revoke_one(self, req: Req):
        return await self._post(req, self._do_revoke_one, recent=False)

    async def _do_revoke_one(self, req: Req, a: Authed):
        found = REVOKE_PATH.match(req.path)
        token_id = found.group(1) if found else ""
        done = await self.run_db(self.tokens.revoke, a.info.user_id, token_id, a.now)
        if not done:
            return self.error(404, "GEMARC_ACCT_404", relogin=False)
        self.pat_cache.evict_token(token_id)
        return pages.redirect(ACCOUNT_PATH, 303, hsts=self.hsts)

    async def _revoke_all(self, req: Req):
        return await self._post(req, self._do_revoke_all, recent=True)

    async def _do_revoke_all(self, req: Req, a: Authed):
        await self.run_db(self.tokens.revoke_all, a.info.user_id, a.now)
        self.pat_cache.evict_user(a.info.user_id)
        return pages.redirect(ACCOUNT_PATH, 303, hsts=self.hsts)

    async def _export(self, req: Req):
        return await self._post(req, self._do_export, recent=True)

    async def _do_export(self, req: Req, a: Authed):
        await self._limit("export", a.info.user_id, a.now)
        data = await self.run_db(self.users.export, a.info.user_id, a.now)
        if data is None:
            return self.error(401, "GEMARC_ACCT_020")
        body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        return pages.respond(200, body, content_type="application/json", hsts=self.hsts,
                             headers={"Content-Disposition": 'attachment; filename="gemarc-account-export.json"'})

    async def _delete(self, req: Req):
        return await self._post(req, self._do_delete, recent=True)

    async def _do_delete(self, req: Req, a: Authed):
        await self._limit("delete_attempt", a.info.user_id, a.now)
        if a.form.get("confirm", "") != CONFIRM_WORD:
            return self.error(400, "GEMARC_ACCT_041", relogin=False)
        done = await self.run_db(self.users.delete, a.info.user_id, a.now)
        self.pat_cache.evict_user(a.info.user_id)
        if not done:
            return self.error(401, "GEMARC_ACCT_020", cookies=[self._clear_sid()])
        return self._html(200, lambda n: pages.deleted_page(n, lang=a.lang), cookies=[self._clear_sid()], lang=a.lang)

    # -- the operator area ------------------------------------------------------------------------------------------
    async def _session_user(self, req: Req):
        """(session info, sid) of a signed-in caller, or (None, None). No database access without a cookie."""
        now = self.now()
        info, sid = await self._session(req, now)
        return info, sid

    async def _admin_session(self, req: Req):
        """(session info, sid, email) for an administrator, or (None, None, None) when the caller is not one.

        The role is the union of the environment allow-list and the runtime grants. A caller whose
        e-mail cannot be read is not an administrator unless the table grants the role.
        """
        info, sid = await self._session_user(req)
        if info is None:
            return None, None, None
        email = await self.run_db(self.users.email_of, info.user_id)
        if not await self.run_db(self.admin.is_admin, info.user_id, email):
            return None, None, None
        return info, sid, email

    async def _admin_page(self, req: Req):
        now = self.now()
        info, sid, email = await self._admin_session(req)
        if info is None:
            return self.error(403, "GEMARC_ACCT_060")
        csrf = self.crypto.csrf_token(sid)
        data = await self.run_db(self._admin_data)
        return self._html(200, lambda n: pages.admin_page(
            self.view, n, lang=req.lang, email=email or "", csrf=csrf, now=now, users=data["users"],
            audit=data["audit"], settings=data["settings"], deployment=data["deployment"]),
            lang=req.lang, lang_cookie=req.lang_chosen)

    def _admin_data(self) -> dict:
        """Everything the admin page shows, read in one executor job (one transaction each)."""
        users = self.admin.list_users()
        audit = self.admin.audit_entries()
        settings = self.admin.settings()
        deployment = {
            "toolset": self.config.toolset,
            "adminEmails": list(self.config.admin_emails),
            "allowedDomains": list(self.config.allowed_domains),
            "allowedEmails": list(self.config.allowed_emails),
            "maxUsers": self.config.max_users,
            "openRegistration": self.config.open_registration,
            "mcpMode": self.config.mcp_mode or "",
        }
        if self.site_status is not None:
            try:
                deployment["site"] = self.site_status()
            except Exception:
                deployment["site"] = {"available": False, "built": False, "source": ""}
        return {"users": users, "audit": audit, "settings": settings, "deployment": deployment}

    async def _require_admin(self, a: Authed) -> bool:
        """The administrator role for a caller that already passed the session and CSRF checks."""
        email = await self.run_db(self.users.email_of, a.info.user_id)
        return await self.run_db(self.admin.is_admin, a.info.user_id, email)

    async def _admin_user(self, req: Req):
        """Enable, disable, grant, revoke or revoke-the-tokens of ONE user. Administrator only."""
        found = ADMIN_USER_PATH.match(req.path)
        if found is None:
            return self.not_found()
        target_id, action = found.group(1), found.group(2)

        async def act(r: Req, a: Authed):
            return await self._do_admin_user(a, target_id, action)

        return await self._post(req, act, recent=True)

    async def _do_admin_user(self, a: Authed, target_id: str, action: str):
        if not await self._require_admin(a):
            return self.error(403, "GEMARC_ACCT_060")
        if target_id == a.info.user_id and action in ("disable", "revoke-admin"):
            # An administrator cannot lock themselves out of the area they are working in.
            return self.error(400, "GEMARC_ACCT_061", relogin=False)
        try:
            message = await self.run_db(self._admin_action, target_id, action, a.now, a.info.user_id)
        except ValueError:
            return self.error(400, "GEMARC_ACCT_061", relogin=False)
        if message is None:
            return self.error(404, "GEMARC_ACCT_404", relogin=False)
        if action == "revoke-tokens":
            self.pat_cache.evict_user(target_id)
        await self.audit_async("admin_action", ref=f"{action}-{target_id}", user_id=a.info.user_id)
        return pages.redirect(ADMIN_PATH, 303, hsts=self.hsts)

    def _admin_action(self, target_id: str, action: str, now: int, actor_id: str) -> Optional[str]:
        """One transaction per action; returns a message for the page or None when the user does not exist."""
        if action == "enable":
            if not self.users.enable(target_id, now):
                return None
            return "已启用该账号。"
        if action == "disable":
            if not self.users.disable(target_id, now):
                return None
            self.pat_cache.evict_user(target_id)
            return "已停用该账号：会话已删除，令牌已撤销。"
        if action == "grant-admin":
            if self.users.get(target_id) is None:
                return None
            self.admin.grant(target_id, now, by=actor_id)
            return "已授予管理员权限。"
        if action == "revoke-admin":
            if self.users.get(target_id) is None:
                return None
            self.admin.revoke(target_id)
            return "已撤销运行时授予的管理员权限（环境变量里的管理员不受影响）。"
        if action == "revoke-tokens":
            if self.users.get(target_id) is None:
                return None
            self.tokens.revoke_all(target_id, now)
            return "已撤销该账号的全部令牌。"
        return None

    async def _admin_settings(self, req: Req):
        """Write one operator setting (the token policy, or the desired tool set)."""
        return await self._post(req, self._do_admin_settings, recent=True)

    async def _do_admin_settings(self, req: Req, a: Authed):
        if not await self._require_admin(a):
            return self.error(403, "GEMARC_ACCT_060")
        key = (a.form.get("key") or "").strip()
        value = (a.form.get("value") or "").strip()
        try:
            if key == "toolset":
                await self.run_db(self.admin.set_toolset, value, a.now)
            else:
                await self.run_db(self.admin.set_number, key, value, a.now)
        except ValueError as exc:
            if str(exc) == "toolset_unavailable":
                # The name is known but this deployment does not ship the code: writing it would
                # break the next cold start, so the request is refused instead.
                return self.error(400, "GEMARC_ACCT_062", relogin=False)
            return self.error(400, "GEMARC_ACCT_061", relogin=False)
        await self.audit_async("admin_setting", ref=key, user_id=a.info.user_id)
        return pages.redirect(ADMIN_PATH, 303, hsts=self.hsts)

    async def _admin_site(self, req: Req):
        """Ask the deployment to rebuild the static site."""
        return await self._post(req, self._do_admin_site, recent=True)

    async def _do_admin_site(self, req: Req, a: Authed):
        if not await self._require_admin(a):
            return self.error(403, "GEMARC_ACCT_060")
        if self.site_rebuild is None:
            return self.error(400, "GEMARC_ACCT_061", relogin=False)
        try:
            message = await self.run_db(self.site_rebuild)
        except Exception:
            return self.error(503, "GEMARC_ACCT_002")
        await self.audit_async("admin_site_rebuild", user_id=a.info.user_id)
        return pages.redirect(ADMIN_PATH, 303, hsts=self.hsts)

    # -- helpers for the application ----------------------------------------------------------------------------
    async def signed_in_session(self, headers: Any) -> Optional[SessionInfo]:
        """The session of a signed-in caller, or None. Used by the console gate and by the MCP session lane.

        The session carries the caller's write permission, so a caller (the console) can be told what
        the server will really let this user do instead of guessing it from the role alone.
        """
        sid = security.single_cookie(headers, self.sid_cookie)
        if sid is None or not valid_sid_format(sid):
            return None
        return await self.run_db(lambda: self.sessions.lookup(sid, self.now(), policy_check=self.policy.row_ok))

    async def signed_in_user(self, headers: Any) -> Optional[str]:
        """The user id of a signed-in caller, or None. Used by the console gate and by the MCP session lane."""
        info = await self.signed_in_session(headers)
        return None if info is None else info.user_id

    async def is_admin_user(self, user_id: str) -> bool:
        """Whether this user id may open the operator area."""
        if not user_id:
            return False
        email = await self.run_db(self.users.email_of, user_id)
        return await self.run_db(self.admin.is_admin, user_id, email)
