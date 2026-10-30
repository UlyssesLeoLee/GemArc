"""Environment configuration of the accounts package: read everything, validate ALL of it at once, fail closed.

Standard library only (plus the stdlib-only parts of ``crypto``): importing this module never loads a database
driver or ``cryptography``. Problems are reported as ``(variable name, reason slug)`` pairs: never values, never
secrets, never paths. Any problem means the configuration is incomplete and the caller must fail closed.

The tri-state parsers (``parse_flag``, ``parse_mcp_auth``, ``evaluate_mcp_mode``) live in the stdlib-only
``accounts/flags.py`` (design 3.0, I2) and are imported here, never re-implemented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from .crypto import KeyRing, KeyRingError
from .flags import clean, evaluate_mcp_mode, parse_flag, parse_mcp_auth

CODE = "GEMARC_ACCT_001"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
OPEN_REGISTRATION_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_PLACEHOLDER_RE = re.compile(r"^(<.*>|your[-_ ].*|change[-_ ]?me|x{3,}|todo|placeholder|example)\Z", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\Z")
_INT_RE = re.compile(r"^[0-9]{1,9}\Z")


# -- result types -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Problem:
    var: str
    reason: str
    code: str = CODE

    def __str__(self) -> str:
        return f"{self.code} {self.var}: {self.reason}"


@dataclass(frozen=True)
class DbTarget:
    kind: str  # "postgres" | "sqlite"
    url: str = field(repr=False)
    sqlite_path: Optional[str] = field(default=None, repr=False)
    loopback: bool = False
    pooled: bool = False
    var: str = ""  # name of the environment variable it came from


@dataclass(frozen=True)
class AccountsConfig:
    base_url: str
    origin: str
    host: str
    secure_cookies: bool
    google_client_id: str = field(repr=False)
    google_client_secret: str = field(repr=False)
    keyring: KeyRing = field(repr=False)
    db: DbTarget = field(repr=False)
    allowed_domains: "tuple[str, ...]"
    allowed_emails: "tuple[str, ...]" = field(repr=False)
    open_registration: bool
    max_users: int
    session_idle_min: int
    session_max_hours: int
    pat_default_days: int
    pat_max_days: int
    pat_max_per_user: int
    pat_cache_seconds: int
    pat_negcache_seconds: int
    audit_retention_days: int
    trusted_proxy_hops: Optional[int]
    auto_migrate: bool
    allow_sqlite: bool
    db_tls_relaxed: bool
    db_role_relaxed: bool
    ack_shared_workspace: bool
    verify_sig: bool
    mcp_host_only: bool
    privacy_url: Optional[str]
    mcp_mode: Optional[str]
    toolset: str


@dataclass(frozen=True)
class ConfigResult:
    config: Optional[AccountsConfig]
    problems: "tuple[Problem, ...]"
    warnings: "tuple[str, ...]"

    @property
    def ok(self) -> bool:
        return self.config is not None and not self.problems

    def names(self) -> "list[str]":
        return [p.var for p in self.problems]

    def describe(self) -> "list[str]":
        return [str(p) for p in self.problems]


# -- helpers ------------------------------------------------------------------------------------------------------
def _int(environ, name, default, lo, hi, problems) -> int:
    raw = clean(environ.get(name))
    if not raw:
        return default
    if not _INT_RE.match(raw):
        problems.append(Problem(name, "invalid"))
        return default
    value = int(raw)
    if value < lo or value > hi:
        problems.append(Problem(name, "out_of_range"))
        return default
    return value


def _flag(environ, name, problems) -> bool:
    state = parse_flag(environ.get(name))
    if state == "bad":
        problems.append(Problem(name, "bad_flag"))
    return state == "on"


def _split_list(raw: Optional[str]) -> "list[str]":
    return [part.strip().lower() for part in clean(raw).split(",") if part.strip()]


def parse_base_url(value: str) -> "Optional[tuple[str, str, str, bool]]":
    """``(base_url, origin, host[:port], secure)`` or None when the value breaks design 3.9 item 2."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.path or parts.query or parts.fragment or parts.username is not None or parts.password is not None:
        return None
    if value.endswith("/") or "?" in value or "#" in value:
        return None
    if parts.scheme == "http" and host.lower() not in LOOPBACK_HOSTS:
        return None
    shown_host = f"[{host}]" if ":" in host else host
    hostport = shown_host + (f":{port}" if port is not None else "")
    return value, f"{parts.scheme}://{hostport}", hostport, parts.scheme == "https"


def db_url_candidates(environ: Mapping[str, str]) -> "tuple[str, ...]":
    """Runtime order (design D66): GEMARC_ACCOUNTS_DATABASE_URL, DATABASE_URL, POSTGRES_URL."""
    return ("GEMARC_ACCOUNTS_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")


def pick_db_var(environ: Mapping[str, str], names: "tuple[str, ...]" = ()) -> Optional[str]:
    for name in names or db_url_candidates(environ):
        if clean(environ.get(name)):
            return name
    return None


def parse_db_url(
    var: str, value: str, *, allow_sqlite: bool, tls_relaxed: bool
) -> "tuple[Optional[DbTarget], Optional[str]]":
    """Validate one database URL (design D67/D101). Returns ``(target, None)`` or ``(None, reason slug)``."""
    lowered = value.lower()
    if lowered.startswith("sqlite:"):
        if not allow_sqlite:
            return None, "sqlite_not_allowed"
        if not lowered.startswith("sqlite:///"):
            return None, "invalid"
        path = value[len("sqlite:///"):]
        if not path or path.startswith(":memory:") or "?" in path:
            return None, "invalid"
        return DbTarget("sqlite", value, sqlite_path=unquote(path), loopback=True, var=var), None
    if not (lowered.startswith("postgres://") or lowered.startswith("postgresql://")):
        return None, "invalid_scheme"
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises ValueError on a bad port
        query = parse_qs(parts.query, keep_blank_values=True)
    except ValueError:
        return None, "invalid"
    if not host:
        return None, "invalid"
    loopback = host in LOOPBACK_HOSTS
    modes = query.get("sslmode", [])
    roots = query.get("sslrootcert", [])
    if len(modes) > 1 or len(roots) > 1:
        return None, "invalid"
    mode = modes[0] if modes else ""
    root = roots[0] if roots else ""
    if not loopback:
        if mode == "verify-full" and root.strip():
            pass
        elif mode in ("require", "verify-ca"):
            if not tls_relaxed:
                return None, "tls_insecure"
        else:  # disable / allow / prefer / missing / unknown / verify-full without a root
            return None, "tls_insecure"
    return DbTarget("postgres", value, loopback=loopback, pooled="-pooler" in host, var=var), None


# -- main entry ---------------------------------------------------------------------------------------------------
def load(environ: Mapping[str, str], *, toolset: Optional[str] = None) -> ConfigResult:
    """Read and validate every accounts variable. ``config`` is set only when there are no problems."""
    problems: "list[Problem]" = []
    warnings: "list[str]" = []

    if parse_flag(environ.get("GEMARC_ACCOUNTS")) != "on":
        # off: not enabled; bad: invalid literal. Both mean the accounts feature must not run.
        reason = "bad_flag" if parse_flag(environ.get("GEMARC_ACCOUNTS")) == "bad" else "not_enabled"
        problems.append(Problem("GEMARC_ACCOUNTS", reason))

    allow_sqlite = _flag(environ, "GEMARC_ACCOUNTS_ALLOW_SQLITE", problems)
    auto_migrate = _flag(environ, "GEMARC_ACCOUNTS_AUTO_MIGRATE", problems)
    tls_relaxed = _flag(environ, "GEMARC_ACCOUNTS_DB_TLS_RELAXED", problems)
    role_relaxed = _flag(environ, "GEMARC_ACCOUNTS_DB_ROLE_RELAXED", problems)
    ack_shared = _flag(environ, "GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE", problems)
    verify_sig = _flag(environ, "GEMARC_ACCOUNTS_VERIFY_SIG", problems)
    host_only = _flag(environ, "GEMARC_ACCOUNTS_MCP_HOST_ONLY", problems)

    mcp_mode, conflict = evaluate_mcp_mode(environ)
    if conflict:
        if parse_flag(environ.get("GEMARC_REQUIRE_LOGIN")) == "bad":
            problems.append(Problem("GEMARC_REQUIRE_LOGIN", "bad_flag"))
        if parse_mcp_auth(environ.get("GEMARC_MCP_AUTH")) == "bad":
            problems.append(Problem("GEMARC_MCP_AUTH", "bad_value"))
        if not any(p.var in ("GEMARC_REQUIRE_LOGIN", "GEMARC_MCP_AUTH") for p in problems):
            problems.append(Problem("GEMARC_MCP_AUTH", "conflict"))

    # public origin
    base = None
    raw_base = clean(environ.get("GEMARC_ACCOUNTS_BASE_URL"))
    if not raw_base:
        problems.append(Problem("GEMARC_ACCOUNTS_BASE_URL", "missing"))
    else:
        base = parse_base_url(raw_base)
        if base is None:
            problems.append(Problem("GEMARC_ACCOUNTS_BASE_URL", "invalid"))

    # Google client
    client_id = clean(environ.get("GEMARC_GOOGLE_CLIENT_ID"))
    client_secret = clean(environ.get("GEMARC_GOOGLE_CLIENT_SECRET"))
    for name, value in (("GEMARC_GOOGLE_CLIENT_ID", client_id), ("GEMARC_GOOGLE_CLIENT_SECRET", client_secret)):
        if not value:
            problems.append(Problem(name, "missing"))
        elif _PLACEHOLDER_RE.match(value):
            problems.append(Problem(name, "placeholder"))

    # keyring
    ring = None
    raw_keys = clean(environ.get("GEMARC_ACCOUNTS_KEYS"))
    if not raw_keys:
        problems.append(Problem("GEMARC_ACCOUNTS_KEYS", "missing"))
    else:
        try:
            ring = KeyRing.parse(raw_keys)
        except KeyRingError as exc:
            problems.append(Problem("GEMARC_ACCOUNTS_KEYS", exc.reason))

    # database
    target = None
    db_var = pick_db_var(environ)
    if db_var is None:
        problems.append(Problem("GEMARC_ACCOUNTS_DATABASE_URL", "missing"))
    else:
        target, reason = parse_db_url(db_var, clean(environ.get(db_var)), allow_sqlite=allow_sqlite, tls_relaxed=tls_relaxed)
        if reason:
            problems.append(Problem(db_var, reason))
        elif target is not None and target.kind == "postgres" and not target.loopback and not target.pooled:
            warnings.append("db_host_not_pooled")

    # policy
    domains = _split_list(environ.get("GEMARC_ACCOUNTS_ALLOWED_DOMAINS")) if clean(environ.get("GEMARC_ACCOUNTS_ALLOWED_DOMAINS")) else ["gmail.com"]
    if not domains or any(not _DOMAIN_RE.match(d) for d in domains):
        problems.append(Problem("GEMARC_ACCOUNTS_ALLOWED_DOMAINS", "invalid"))
    emails = _split_list(environ.get("GEMARC_ACCOUNTS_ALLOWED_EMAILS"))
    if any(e.count("@") != 1 or e.startswith("@") or e.endswith("@") or " " in e for e in emails):
        problems.append(Problem("GEMARC_ACCOUNTS_ALLOWED_EMAILS", "invalid"))
    open_registration = not emails and any(d in OPEN_REGISTRATION_DOMAINS for d in domains)

    # numbers (out of range = incomplete; never clamped)
    max_users = _int(environ, "GEMARC_ACCOUNTS_MAX_USERS", 1000, 0, 10_000_000, problems)
    idle = _int(environ, "GEMARC_ACCOUNTS_SESSION_IDLE_MIN", 30, 5, 240, problems)
    max_hours = _int(environ, "GEMARC_ACCOUNTS_SESSION_MAX_HOURS", 8, 1, 72, problems)
    pat_max = _int(environ, "GEMARC_ACCOUNTS_PAT_MAX_DAYS", 90, 1, 365, problems)
    pat_default = _int(environ, "GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS", 30, 1, 365, problems)
    if pat_default > pat_max and not any(p.var in ("GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS", "GEMARC_ACCOUNTS_PAT_MAX_DAYS") for p in problems):
        problems.append(Problem("GEMARC_ACCOUNTS_PAT_DEFAULT_DAYS", "exceeds_max"))
    pat_per_user = _int(environ, "GEMARC_ACCOUNTS_PAT_MAX_PER_USER", 10, 1, 50, problems)
    cache = _int(environ, "GEMARC_ACCOUNTS_PAT_CACHE_SECONDS", 30, 0, 120, problems)
    negcache = _int(environ, "GEMARC_ACCOUNTS_PAT_NEGCACHE_SECONDS", 15, 0, 60, problems)
    retention = _int(environ, "GEMARC_ACCOUNTS_AUDIT_RETENTION_DAYS", 30, 7, 90, problems)
    hops = None
    if clean(environ.get("GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS")):
        hops = _int(environ, "GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS", 1, 1, 5, problems)

    privacy = clean(environ.get("GEMARC_ACCOUNTS_PRIVACY_URL")) or None
    if privacy is not None:
        try:
            p = urlsplit(privacy)
            good = p.scheme == "https" and bool(p.hostname) and p.username is None
        except ValueError:
            good = False
        if not good:
            problems.append(Problem("GEMARC_ACCOUNTS_PRIVACY_URL", "invalid"))

    # shared workspace rule (D90): needs the toolset, same default as api/index.py
    tool = (toolset if toolset is not None else clean(environ.get("GEMARC_TOOLSET")) or "stateless").strip().lower()
    if tool == "full" and open_registration and not ack_shared:
        problems.append(Problem("GEMARC_ACCOUNTS_ACK_SHARED_WORKSPACE", "required"))

    if not clean(environ.get("VERCEL")) and hops is None:
        # No client address can be trusted: per-address database limits (login, callback, signup) are skipped and the
        # in-memory pre-filter falls back to a coarse client fingerprint. Set GEMARC_ACCOUNTS_TRUSTED_PROXY_HOPS.
        warnings.append("no_trusted_client_ip")
    if tls_relaxed:
        warnings.append("db_tls_relaxed")
    if role_relaxed:
        warnings.append("db_role_relaxed")
    if not verify_sig:
        warnings.append("verify_sig_off")

    if problems or base is None or ring is None or target is None:
        return ConfigResult(None, tuple(problems), tuple(warnings))

    config = AccountsConfig(
        base_url=base[0], origin=base[1], host=base[2], secure_cookies=base[3],
        google_client_id=client_id, google_client_secret=client_secret, keyring=ring, db=target,
        allowed_domains=tuple(domains), allowed_emails=tuple(emails), open_registration=open_registration,
        max_users=max_users, session_idle_min=idle, session_max_hours=max_hours,
        pat_default_days=pat_default, pat_max_days=pat_max, pat_max_per_user=pat_per_user,
        pat_cache_seconds=cache, pat_negcache_seconds=negcache, audit_retention_days=retention,
        trusted_proxy_hops=hops, auto_migrate=auto_migrate, allow_sqlite=allow_sqlite,
        db_tls_relaxed=tls_relaxed, db_role_relaxed=role_relaxed, ack_shared_workspace=ack_shared,
        verify_sig=verify_sig, mcp_host_only=host_only, privacy_url=privacy, mcp_mode=mcp_mode, toolset=tool,
    )
    return ConfigResult(config, (), tuple(warnings))


def migration_db_var(environ: Mapping[str, str]) -> Optional[str]:
    """Migration/CLI order (design D66): GEMARC_MIGRATE_DATABASE_URL, then DATABASE_URL_UNPOOLED."""
    return pick_db_var(environ, ("GEMARC_MIGRATE_DATABASE_URL", "DATABASE_URL_UNPOOLED"))
