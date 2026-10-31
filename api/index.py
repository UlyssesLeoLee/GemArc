"""
Vercel entrypoint for the GemArc MCP server.

This module is the ASGI application that Vercel deploys as a Python Function.
It serves, from one function:

  POST /mcp      MCP Streamable HTTP endpoint (optionally behind a bearer token)
  /healthz       health and deployment-state probe (public, leaks no paths)
  /              the MCP console (public static shell, no secrets)
  /site/*        the 5D architecture static site (public, built on first use)
  /kit/<p>.zip   the fixed part of a 5D site, one deterministic archive (public)
  /kit/atlas.zip LOCAL OPT-IN only (GEMARC_ATLAS_KIT=1, off by default and unreachable on the Vercel upload): the atlas kit

The default tool set of a remote deployment is ``stateless``: the model travels
inside each tool call, so no durable filesystem and no caller identity are
needed. An operator may still set ``GEMARC_TOOLSET=full`` (see docs/09_部署).

Serverless-specific decisions. The first three were measured on the local ASGI
app and then on a real Vercel deployment (docs/09_部署/01_Vercel部署.md, section
12, which also lists what is still unverified).

1. Streamable HTTP in stateless mode with JSON responses, POST only.
   A GET on the MCP path would open a server-sent-events stream that holds a
   function instance until ``maxDuration``. Measured: ``GET /mcp`` answered 200
   and kept the stream open. Every non-POST method is therefore answered with
   405 + ``Allow: POST`` before authentication and before the SDK is reached
   (MCP lets a server decline the optional GET stream with 405).

2. DNS-rebinding protection is disabled.
   The SDK default only accepts Host: localhost / 127.0.0.1, so on a real domain
   every request would be answered with HTTP 421 "Invalid Host header".
   The access control that replaces it is the optional bearer token below.

3. The MCP session manager runs on the SERVING event loop, never in a thread.
   The SDK needs the outer app to enter ``session_manager.run()`` (it refuses a
   second call on the same manager and raises "Task group is not initialized"
   without it). A background thread would put the task group on another loop,
   which races under concurrent invocations. Two ways in, both on the serving loop:
     * ``lifespan``  this app's ASGI lifespan enters ``run()`` (hosts that run
       lifespan events, e.g. uvicorn);
     * ``lazy``      the first MCP request starts ``run()`` as a background task
       on the current loop, for hosts that never run lifespan events (the
       legacy @vercel/python runtime; vercel/vercel#11677).
   Each manager is entered exactly once (guarded by a flag and a per-loop
   lock). If the loop that owns the running manager is gone (a host that runs
   every invocation on a fresh event loop), has been shut down, or failed to
   start, the next request pairs a FRESH inner app + manager with its own loop,
   because a manager cannot be re-entered. A request never reaches the SDK
   without a running manager: when a start fails or times out, ``POST /mcp``
   answers 503 ``session manager not running``. ``/healthz`` reports
   ``sessionManager``: lifespan | lazy | not-running.

Security posture:

* ``GEMARC_MCP_TOKEN`` unset or empty: the endpoint is open ("authless"). That
  is required for claude.ai custom connectors, which support OAuth or no
  authentication. The tools only compute on what the caller sends, but anyone
  who finds the URL can call them.
* ``GEMARC_MCP_TOKEN`` set (comma-separated list, so a token can be rotated
  with an overlap): ``POST /mcp`` requires ``Authorization: Bearer <token>``.
  Tokens are read from the header only, compared in constant time, and never
  logged or echoed. Public static content stays public.
* Write tools stay OFF unless ``GEMARC_ALLOW_WRITE`` is exactly ``1``, and the
  stateless tool set contains no write tool at all.

Optional accounts (Google sign-in, personal access tokens; docs/09_部署/02_账号与安全.md):
off unless ``GEMARC_ACCOUNTS=1`` and the configuration is complete. With them off and no
login intent this module behaves exactly as described above and imports nothing from
``gemarc_mcp.accounts`` except the stdlib-only ``flags``. A login intent
(``GEMARC_REQUIRE_LOGIN`` or a PAT mode) with accounts that are not usable answers
``POST /mcp`` with 503 (fail closed), never with an open endpoint. The three switches are
interpreted only by ``gemarc_mcp.accounts.flags``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import anyio
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import gemarc_mcp  # noqa: E402
from gemarc_mcp import server as gemarc_server  # noqa: E402
from gemarc_mcp import stateless  # noqa: E402
from gemarc_mcp.accounts import flags as account_flags  # noqa: E402  (stdlib only; nothing else of the package)

log = logging.getLogger("gemarc.api")

TOKEN_ENV = "GEMARC_MCP_TOKEN"
DEFAULT_REMOTE_TOOLSET = "stateless"

SEED_PROJECT = "demo"
SEED_MODEL = REPO_ROOT / "examples" / "demo-site" / "data" / "architecture.json"
LEGACY_STATIC_DIR = REPO_ROOT / "examples" / "demo-site"

#: The console is a fixed part of the package, not a generated artefact: it is
#: the tuning panel for the MCP itself, so it ships with the server. The path
#: comes from the imported package, so it is right for an installed copy too.
CONSOLE_DIR = Path(gemarc_mcp.__file__).resolve().parent / "scaffold" / "templates" / "console"
CONSOLE_HTML = CONSOLE_DIR / "index.html"
CONSOLE_ASSETS = ("console.css", "console.js")

MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"
SITE_PATH = "/site/"
KIT_PROFILES = stateless.SITE_PROFILES
_KIT_RE = re.compile(r"/kit/([A-Za-z0-9_-]+)\.zip")
#: LOCAL OPT-IN, off in every deployed configuration: ``GET /kit/atlas.zip`` exists only while the operator's environment says
#: ``GEMARC_ATLAS_KIT=1`` (exactly that value). The atlas is not part of the Vercel upload (``.vercelignore``), so even with the flag set the route
#: answers 503 ``GEMARC_ATLAS_KIT_001`` there (the import is lazy and fails closed). Without the flag the path is an unknown kit profile: 404, as before.
ATLAS_KIT_PATH = "/kit/atlas.zip"

#: Paths under which a platform may hand the function the MCP request. The
#: legacy ``builds`` + ``routes`` configuration rewrites everything to
#: /api/index.py, so those spellings stay accepted.
MCP_PATHS = ("/mcp", "/api/index", "/api/index.py", "/index.py")


# --- small helpers ----------------------------------------------------------

def _json(status: int, body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=headers)


#: Request body consumed (at most) before an early error response, and the longest wait for it.
MAX_DRAIN_BYTES = 1 << 20
DRAIN_TIMEOUT_S = 2.0


async def _drain(receive, limit: int = MAX_DRAIN_BYTES) -> None:
    seen = 0
    while seen <= limit:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            return
        seen += len(message.get("body", b""))


def _is_mcp_path(path: str) -> bool:
    bare = path.rstrip("/") or "/"
    return bare in MCP_PATHS or bare.endswith("/mcp")


def _is_health_path(path: str) -> bool:
    return (path.rstrip("/") or "/").endswith("/healthz")


def parse_tokens(raw: str | None) -> tuple[bytes, ...]:
    """``a, b`` -> (b"a", b"b"); whitespace stripped, empty entries ignored."""
    return tuple(part.strip().encode("utf-8") for part in (raw or "").split(",") if part.strip())


def _presented_token(headers: Headers) -> bytes | None:
    """The bearer token of the Authorization header, or None. Never the query string."""
    value = headers.get("authorization")
    if not value:
        return None
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip().encode("latin-1", "replace")


def token_matches(presented: bytes | None, tokens: tuple[bytes, ...]) -> bool:
    """Constant-time comparison against every configured token (bytes, so any input is safe)."""
    if presented is None:
        return False
    matched = False
    for token in tokens:
        matched |= hmac.compare_digest(presented, token)
    return matched


def _etag_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match (weak comparison): quotes, W/, lists and * all handled."""
    if not header:
        return False
    bare = etag.strip('"')
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate.strip('"') == bare:
            return True
    return False


# --- workspace (only the `full` tool set and project="demo" use it) ---------

def _resolve_workspace(configured: str | Path | None = None) -> Path:
    """Vercel gives every instance an ephemeral filesystem; only /tmp is writable."""
    default = Path(tempfile.gettempdir()) / "gemarc-workspace"
    root = Path(configured) if configured else Path(os.environ.get("GEMARC_WORKSPACE_ROOT") or default)
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    except OSError:
        # A misconfigured or read-only root must not take the whole function down.
        default.mkdir(parents=True, exist_ok=True)
        return default.resolve()


def seed_demo_project(workspace_root: Path) -> bool:
    """Give ``project="demo"`` something to read on a cold instance."""
    target = workspace_root / SEED_PROJECT / "model" / "architecture.json"
    if target.exists() or not SEED_MODEL.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEED_MODEL, target)
    return True


# --- lazily materialised static site ----------------------------------------

class LazySite:
    """The scaffold site, built from the package sources on first use.

    Building is deferred to the first ``/site/*`` request so that a cold start
    that only serves ``/mcp`` never pays for it. The result (including a
    fallback to the committed preview) is cached for the life of the instance.
    """

    NOT_BUILT = "not-built-yet"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = False
        self._app = None
        self._source = self.NOT_BUILT
        self._available = None

    @property
    def built(self) -> bool:
        return self._done

    @property
    def source(self) -> str:
        return self._source

    @property
    def available(self) -> bool:
        """Cheap before the build: can a site be produced at all?"""
        if self._available is not None:
            return self._available
        return SEED_MODEL.is_file() or (LEGACY_STATIC_DIR / "index.html").is_file()

    def ensure(self) -> None:
        """Blocking; run it in a worker thread. Safe to call concurrently."""
        if self._done:
            return
        with self._lock:
            if self._done:
                return
            directory, source = self._materialise()
            if directory is None:
                directory, source = LEGACY_STATIC_DIR, source or "committed-preview"
            if (directory / "index.html").is_file():
                self._app = StaticFiles(directory=str(directory), html=True)
                self._available = True
            else:
                self._app = None
                self._available = False
            self._source = source
            self._done = True

    @staticmethod
    def _materialise() -> tuple[Path | None, str]:
        try:
            from gemarc_mcp.model import normalized_model
            from gemarc_mcp.scaffold import builder

            model = json.loads(SEED_MODEL.read_text(encoding="utf-8"))
            target = Path(tempfile.mkdtemp(prefix="gemarc-site-"))
            builder.build_site(target, normalized_model(model), "static")
            return target, "scaffold-static"
        except Exception as exc:  # the committed preview keeps the site up
            # The exception text can contain filesystem paths: log it, but
            # expose only its class through /healthz.
            log.exception("static site generation failed")
            return None, f"committed-preview (generation failed: {type(exc).__name__})"

    async def __call__(self, scope, receive, send):
        if not self._done:
            await anyio.to_thread.run_sync(self.ensure)
        if self._app is None:
            response = Response(
                "GemArc static site is not present in this deployment bundle. "
                "The MCP endpoint at /mcp is unaffected.",
                status_code=404, media_type="text/plain; charset=utf-8")
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


# --- application factory -----------------------------------------------------

#: How long a request waits for a lazily started session manager.
LAZY_START_TIMEOUT_S = 15.0


class _State:
    """Per-app session-manager state; one instance per ``build_app`` call."""

    def __init__(self) -> None:
        #: Strategy that last started a manager: "lifespan" | "lazy" ("not-running": none yet, or it failed).
        self.mode = "not-running"
        #: The event loop the current manager is running on (None when none is).
        self.running_on: asyncio.AbstractEventLoop | None = None
        #: The inner MCP app paired with the current manager, and whether its run() was entered.
        self.mcp_app = None
        self.manager = None
        self.run_entered = False
        self.failed = False
        self.stopped = False
        self.tasks: set[asyncio.Task] = set()   # strong references to lazy runners
        self.lock: asyncio.Lock | None = None
        self.lock_loop: asyncio.AbstractEventLoop | None = None

    def lock_for(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        """An asyncio.Lock is bound to the loop that first contends it, so keep one per loop."""
        if self.lock is None or self.lock_loop is not loop:
            self.lock, self.lock_loop = asyncio.Lock(), loop
        return self.lock


def build_app(*, token: str | None = None, toolset: str | None = None,
              allow_write: bool | None = None,
              workspace_root: str | Path | None = None,
              accounts: dict | None = None) -> Starlette:
    """Build one deployable ASGI app.

    Every argument defaults to its environment variable, read *now*:
    ``GEMARC_MCP_TOKEN``, ``GEMARC_TOOLSET`` (default ``stateless``),
    ``GEMARC_ALLOW_WRITE`` (default off) and ``GEMARC_WORKSPACE_ROOT``.
    Tests build variants with explicit arguments; production uses ``app`` below.
    ``token=""`` forces an open endpoint regardless of the environment.
    ``accounts`` is for tests: a dict that may carry ``environ`` (a mapping used instead of
    ``os.environ`` for the accounts switches and configuration), ``idp`` (fake identity-provider
    endpoints), ``clock``, ``db``, ``opener``, ``limit_overrides`` and ``prefilter``.
    """
    env_source = (accounts or {}).get("environ", os.environ)
    tokens = parse_tokens(env_source.get(TOKEN_ENV) if token is None else token)

    # The service layer reads the write flag and the workspace from the process
    # environment, so those two are normalised there. Anything other than the
    # exact value "1" is "0": a stray "true" cannot silently enable writes.
    if allow_write is None:
        allow_write = os.environ.get("GEMARC_ALLOW_WRITE") == "1"
    os.environ["GEMARC_ALLOW_WRITE"] = "1" if allow_write else "0"
    workspace = _resolve_workspace(workspace_root)
    os.environ["GEMARC_WORKSPACE_ROOT"] = str(workspace)
    seeded = seed_demo_project(workspace)

    # The tool set is passed explicitly instead of through os.environ, so
    # building an app never changes the configuration of the host process.
    mode = gemarc_server.active_toolset(toolset or os.environ.get("GEMARC_TOOLSET") or DEFAULT_REMOTE_TOOLSET)
    mcp_server = gemarc_server.create_server(mode)
    # create_server refills these module-level lists on every call; keep this app's copy.
    registered = tuple(gemarc_server.REGISTERED_TOOLS)
    write_tools = tuple(sorted(gemarc_server.WRITE_TOOLS))

    # --- accounts: decided once, here. Off and no login intent: nothing below runs. ---
    env_view = dict(env_source)
    if token is not None:
        env_view[TOKEN_ENV] = token
    switches = account_flags.evaluate(env_view)
    accounts_state = "disabled"          # disabled | ok | misconfigured
    account_config = None
    owns_account_path = None
    account_pages = None
    if switches.accounts_present:
        from gemarc_mcp.accounts import config as accounts_config  # stdlib only
        from gemarc_mcp.accounts import pages as account_pages  # noqa: F811
        from gemarc_mcp.accounts import security as account_security
        owns_account_path = account_security.owns
        loaded = accounts_config.load(env_view, toolset=mode)
        if loaded.ok:
            account_config, accounts_state = loaded.config, "ok"
            for warning in loaded.warnings:  # slugs only (no values); the operator CLI `doctor` explains each
                log.warning("accounts config warning: %s", warning)
        else:
            accounts_state = "misconfigured"
            log.error("accounts are misconfigured (GEMARC_ACCT_001)")  # names and values only via the operator CLI
    elif switches.login_intent:
        accounts_state = "misconfigured"
        log.error("a login is required but accounts are not enabled (GEMARC_ACCT_001)")
    #: Login required but accounts unusable: /mcp answers 503 to everything, even with a valid env token.
    fail_closed = switches.login_intent and accounts_state != "ok"
    account_runtime: list = []           # at most one AccountsRuntime, built on first use
    account_mcp_auth: list = []
    account_lock = threading.Lock()

    def get_account_runtime():
        with account_lock:
            if not account_runtime:
                from gemarc_mcp.accounts import routes as account_routes
                extra = {k: v for k, v in (accounts or {}).items()
                         if k in ("idp", "clock", "db", "opener", "limit_overrides", "prefilter")}
                account_runtime.append(account_routes.AccountsRuntime(
                    account_config, environ=env_source, registered_tools=registered,
                    write_tools=write_tools, allow_write=allow_write, **extra))
            return account_runtime[0]

    def get_account_mcp_auth():
        runtime = get_account_runtime()
        with account_lock:
            if not account_mcp_auth:
                account_mcp_auth.append(runtime.mcp_auth(
                    switches.mode, lambda presented: token_matches(presented, tokens),
                    registered_tools=registered, write_tools=write_tools))
            return account_mcp_auth[0]

    def make_inner():
        """A new inner MCP app. Each call creates a NEW session manager (the SDK enters one once)."""
        return mcp_server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            host="0.0.0.0",
        )

    state = _State()
    state.mcp_app = make_inner()
    state.manager = mcp_server.session_manager
    site = LazySite()
    kit_etags: dict[str, str] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # The SDK needs the OUTER app to enter the session manager; the inner
        # Starlette app is driven as a plain ASGI callable, so its own
        # lifespan never runs.
        loop = asyncio.get_running_loop()
        state.run_entered = True
        try:
            async with state.manager.run():
                state.mode, state.running_on = "lifespan", loop
                state.failed = state.stopped = False
                yield
        except Exception:
            state.mode, state.failed = "not-running", True
            raise
        finally:
            if state.running_on is loop:
                state.running_on = None
            state.stopped = True

    async def lazy_runner(manager, ready: asyncio.Event):
        """Hold ``manager.run()`` open for the life of the loop (when no lifespan ran)."""
        loop = asyncio.get_running_loop()
        try:
            async with manager.run():
                state.mode, state.running_on = "lazy", loop
                state.failed = state.stopped = False
                ready.set()
                await anyio.sleep_forever()
        except Exception:
            log.exception("session manager failed to start")
            state.mode, state.failed = "not-running", True
        finally:
            # Also reached when the loop is torn down: the manager is gone with it.
            if state.running_on is loop:
                state.running_on = None
            ready.set()

    async def ensure_session_manager():
        """The inner MCP app to dispatch to, or None when no manager can run on this loop.

        Normally a manager is already running here (lifespan, or started by an
        earlier request). Otherwise one is started now on the current loop. A
        manager can be entered once, so when the one that was entered belongs to
        another loop (a host that runs every invocation on a fresh event loop),
        has been shut down, or failed, a fresh inner app + manager is paired
        with this loop instead of refusing the request.
        """
        loop = asyncio.get_running_loop()
        if state.running_on is loop:
            return state.mcp_app
        async with state.lock_for(loop):
            if state.running_on is loop:
                return state.mcp_app
            if state.run_entered:
                state.mcp_app = make_inner()
                state.manager = mcp_server.session_manager
            state.run_entered = True
            ready = asyncio.Event()
            task = asyncio.create_task(lazy_runner(state.manager, ready))
            state.tasks.add(task)
            task.add_done_callback(state.tasks.discard)
            with anyio.move_on_after(LAZY_START_TIMEOUT_S):
                await ready.wait()
            return state.mcp_app if state.running_on is loop else None

    # --- endpoints ----------------------------------------------------------

    def accounts_health() -> str:
        if accounts_state != "ok":
            return "misconfigured"
        if not account_runtime:
            return "ok"
        return {"ok": "ok", "unreachable": "db-unreachable"}.get(account_runtime[0].db_state, "misconfigured")

    def health_payload() -> dict:
        payload = {
            "status": "degraded" if (state.failed or state.stopped) else "ok",
            "server": "gemarc-mcp",
            "version": gemarc_mcp.__version__,
            "transport": "streamable-http",
            "protocolPath": MCP_PATH,
            "toolset": mode,
            "tools": list(registered),
            "writeEnabled": allow_write,
            "writeTools": list(write_tools),
            "authRequired": bool(tokens) or switches.login_intent,
            "sessionManager": state.mode,
            "seedProject": SEED_PROJECT,
            "seeded": seeded,
            "staticSiteAvailable": site.available,
            "staticSiteSource": site.source,
            "staticSitePath": SITE_PATH,
            "consoleAvailable": CONSOLE_HTML.is_file(),
        }
        if switches.accounts_present or switches.login_intent:
            payload["accounts"] = accounts_health()
        return payload

    def render_console() -> str:
        html = CONSOLE_HTML.read_text(encoding="utf-8")
        html = html.replace("__MCP_PATH__", MCP_PATH)
        html = html.replace("__HEALTH_PATH__", HEALTH_PATH)
        html = html.replace("__SITE_PATH__", SITE_PATH)
        html = html.replace("__VERSION__", gemarc_mcp.__version__)
        return html.replace("__WRITE_TOOLS__", json.dumps(list(write_tools), ensure_ascii=False))

    async def reply(response: Response, scope, receive, send):
        # Answering early (401, 405, 503) with the request body unread can make
        # the server reset the connection before the client has finished
        # sending; consume a bounded amount first so the client sees the status.
        # Bounded in time too: a client that announces a body and never sends it must
        # still get its answer instead of holding the connection open for it.
        with anyio.move_on_after(DRAIN_TIMEOUT_S):
            await _drain(receive)
        await response(scope, receive, send)

    async def read_only(scope, receive, send, allowed=("GET", "HEAD")) -> bool:
        """Answer OPTIONS / wrong methods; True means the caller may proceed."""
        method = scope["method"]
        if method in allowed:
            return True
        allow = ", ".join((*allowed, "OPTIONS"))
        if method == "OPTIONS":
            await reply(Response(status_code=204, headers={"Allow": allow}), scope, receive, send)
        else:
            await reply(_json(405, {"error": "method_not_allowed"}, {"Allow": allow}), scope, receive, send)
        return False

    async def mcp_endpoint(scope, receive, send):
        method = scope["method"]
        # 1. Method gate. Runs BEFORE authentication and before the SDK: only
        #    POST reaches it, so no request can open an SSE stream.
        if method == "OPTIONS":
            await reply(Response(status_code=204, headers={"Allow": "POST, OPTIONS"}), scope, receive, send)
            return
        if method != "POST":
            await reply(_json(405, {"error": "method_not_allowed"}, {"Allow": "POST"}), scope, receive, send)
            return
        # 2. Authentication (header only; the query string is never consulted).
        if fail_closed:
            await reply(_json(503, {"error": "accounts_unavailable", "code": "GEMARC_ACCT_001"},
                              {"Cache-Control": "no-store"}), scope, receive, send)
            return
        if switches.uses_pat:
            headers = Headers(scope=scope)
            decision = await get_account_mcp_auth().authenticate(scope, headers, _presented_token(headers), receive)
            if not decision.allowed:
                if decision.body_consumed:
                    await decision.response(scope, receive, send)
                else:
                    await reply(decision.response, scope, receive, send)
                return
            if decision.receive is not None:
                receive = decision.receive
        elif tokens and not token_matches(_presented_token(Headers(scope=scope)), tokens):
            await reply(_json(401, {"error": "unauthorized"},
                              {"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"}),
                        scope, receive, send)
            return
        # 3. Never reach the SDK without a running session manager: start one
        #    on this loop if no lifespan ever did, else a clear 503 instead of
        #    the SDK's "Task group is not initialized" RuntimeError.
        inner = await ensure_session_manager()
        if inner is None:
            await reply(_json(503, {"error": "session manager not running"}), scope, receive, send)
            return
        normalised = dict(scope)
        normalised["path"] = MCP_PATH
        normalised["raw_path"] = MCP_PATH.encode("ascii")
        await inner(normalised, receive, send)

    async def accounts_endpoint(scope, receive, send):
        if accounts_state != "ok":
            await reply(account_pages.unavailable("GEMARC_ACCT_001"), scope, receive, send)
            return
        await get_account_runtime().handle(scope, receive, send)

    async def health_endpoint(scope, receive, send):
        if await read_only(scope, receive, send):
            await reply(_json(200, health_payload(), {"Cache-Control": "no-store"}), scope, receive, send)

    async def console_endpoint(scope, receive, send):
        if not await read_only(scope, receive, send):
            return
        if not CONSOLE_HTML.is_file():
            response = Response("The console shell is not present in this deployment bundle. "
                                "The MCP endpoint at /mcp is unaffected.",
                                status_code=404, media_type="text/plain; charset=utf-8")
        else:
            response = HTMLResponse(render_console(), headers={"Cache-Control": "no-cache"})
        await reply(response, scope, receive, send)

    async def console_asset(scope, receive, send, name: str):
        if not await read_only(scope, receive, send):
            return
        target = (CONSOLE_DIR / name).resolve()
        if name not in CONSOLE_ASSETS or not target.is_file():
            response = _json(404, {"error": "not_found"})
        else:
            media = "text/css" if name.endswith(".css") else "text/javascript"
            response = Response(target.read_bytes(), media_type=media,
                                headers={"Cache-Control": "no-cache"})
        await reply(response, scope, receive, send)

    async def kit_endpoint(scope, receive, send, path: str):
        found = _KIT_RE.fullmatch(path)
        if not found or found.group(1) not in KIT_PROFILES:
            await reply(_json(404, {"error": "not_found", "profiles": list(KIT_PROFILES)}), scope, receive, send)
            return
        if not await read_only(scope, receive, send):
            return
        profile = found.group(1)
        # The first call builds the archive (cached afterwards); keep the event loop free.
        archive = await anyio.to_thread.run_sync(stateless.site_kit_zip, profile)
        if profile not in kit_etags:
            kit_etags[profile] = '"%s"' % stateless.site_kit(profile)["archiveSha256"]
        etag = kit_etags[profile]
        headers = {"ETag": etag, "Cache-Control": "public, max-age=3600"}
        if _etag_matches(Headers(scope=scope).get("if-none-match"), etag):
            await reply(Response(status_code=304, headers=headers), scope, receive, send)
            return
        headers["Content-Disposition"] = f"attachment; filename=gemarc-kit-{profile}.zip"
        await reply(Response(archive, media_type="application/zip", headers=headers), scope, receive, send)

    async def atlas_kit_endpoint(scope, receive, send):
        """The atlas kit zip (see ``ATLAS_KIT_PATH``); only reached with ``GEMARC_ATLAS_KIT=1``. Fails closed, with a code, when the atlas is absent."""
        if not await read_only(scope, receive, send):
            return
        unavailable = {"Cache-Control": "no-store"}
        try:
            from gemarc_mcp.atlas import kit as atlas_kit  # lazy: the deployed function does not carry the atlas package
        except ImportError:
            await reply(_json(503, {"error": "atlas_kit_unavailable", "code": "GEMARC_ATLAS_KIT_001"}, unavailable), scope, receive, send)
            return
        try:
            archive = await anyio.to_thread.run_sync(atlas_kit.build_zip)
        except atlas_kit.KitError as ex:
            await reply(_json(503, {"error": "atlas_kit_unavailable", "code": ex.code}, unavailable), scope, receive, send)
            return
        etag = '"%s"' % atlas_kit.sha256_hex(archive)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if _etag_matches(Headers(scope=scope).get("if-none-match"), etag):
            await reply(Response(status_code=304, headers=headers), scope, receive, send)
            return
        headers["Content-Disposition"] = f"attachment; filename={atlas_kit.KIT_FILE}"
        await reply(Response(archive, media_type="application/zip", headers=headers), scope, receive, send)

    async def site_endpoint(scope, receive, send, path: str):
        remainder = "/" + path[len(SITE_PATH):]
        rewritten = dict(scope)
        rewritten["path"] = remainder
        if isinstance(scope.get("raw_path"), (bytes, bytearray)):
            rewritten["raw_path"] = remainder.encode("utf-8")
        await site(rewritten, receive, send)

    # --- dispatch -------------------------------------------------------------

    async def dispatch(scope, receive, send):
        if scope["type"] != "http":  # pragma: no cover - Mount only forwards http
            return
        path = scope.get("path", "/")
        if path == SITE_PATH or path.startswith(SITE_PATH):
            await site_endpoint(scope, receive, send, path)
        elif path == ATLAS_KIT_PATH and os.environ.get("GEMARC_ATLAS_KIT") == "1":
            await atlas_kit_endpoint(scope, receive, send)
        elif path.startswith("/kit/"):
            await kit_endpoint(scope, receive, send, path)
        elif path.startswith("/.well-known/"):
            # OAuth discovery probes: a clean negative, not the HTML console.
            await reply(_json(404, {"error": "not_found"}), scope, receive, send)
        elif _is_health_path(path):
            await health_endpoint(scope, receive, send)
        elif _is_mcp_path(path):
            await mcp_endpoint(scope, receive, send)
        elif path in ("/console.css", "/console.js"):
            await console_asset(scope, receive, send, path[1:])
        elif path in ("/", ""):
            await console_endpoint(scope, receive, send)
        elif path == "/site":
            await reply(Response(status_code=307, headers={"Location": SITE_PATH}), scope, receive, send)
        elif owns_account_path is not None and owns_account_path(path):
            await accounts_endpoint(scope, receive, send)
        else:
            await reply(_json(404, {"error": "not_found"}), scope, receive, send)

    starlette_app = Starlette(routes=[Mount("/", app=dispatch)], lifespan=lifespan)
    # In-process handles for tests and diagnostics.
    starlette_app.state.gemarc_health = health_payload
    starlette_app.state.gemarc_mcp_server = mcp_server
    starlette_app.state.gemarc_accounts = get_account_runtime if accounts_state == "ok" else None
    starlette_app.state.gemarc_account_mcp_auth = (
        get_account_mcp_auth if accounts_state == "ok" and switches.uses_pat else None)
    return starlette_app


#: The application Vercel imports. Reads its configuration from the environment.
app = build_app()
