"""
Vercel entrypoint for the GemArc MCP server.

This module is the ASGI application that Vercel deploys as a Python Function.
It serves two things from one function:

  /mcp      MCP Streamable HTTP endpoint (14 tools / 2 resources / 1 prompt)
  /healthz  health and deployment-state probe
  /*        the 5D architecture static site (built from the package sources)

Serving the static site from the function as well keeps the whole deployment to
a single build and a single routing rule, which removes any ambiguity about
Vercel's static-build destination conventions. If you later want the site on
the CDN instead, move it to a dedicated static project and delete the static
mount below; nothing else depends on it.

Three serverless-specific decisions are made here. Each was established by
experiment, not assumption (evidence in docs/09_部署/01_Vercel部署.md):

1. Transport is Streamable HTTP in stateless mode with JSON responses.
   Vercel functions do not keep MCP session state between invocations, and a
   long-lived SSE response would occupy a function for its whole lifetime.

2. DNS-rebinding protection is disabled.
   The SDK default only accepts Host: localhost / 127.0.0.1, so on a real domain
   every request would be answered with HTTP 421 "Invalid Host header".
   Measured: 421 with protection on, 200 with it off, same request.

3. The MCP session manager is started in a background thread at import time.
   Serverless hosts do not reliably run ASGI lifespan events
   (vercel/vercel#11677), and without the session manager the SDK raises
   "RuntimeError: Task group is not initialized. Make sure to use run()."

Security posture: write access stays OFF unless an operator sets
GEMARC_ALLOW_WRITE=1. The seeded demo project is published sample data, so the
read-only tools are safe to expose; the write tools are not, because every
caller would share one ephemeral workspace.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import anyio
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from gemarc_mcp import server as gemarc_server  # noqa: E402

# --- workspace -------------------------------------------------------------
# Vercel gives every instance an ephemeral filesystem and only /tmp is writable.
# The root is instance-scoped so separate instances never share state.
def _resolve_workspace() -> Path:
    configured = Path(
        os.environ.get("GEMARC_WORKSPACE_ROOT", "/tmp/gemarc-workspace")
    )
    try:
        configured.mkdir(parents=True, exist_ok=True)
        return configured.resolve()
    except OSError:
        # A misconfigured or read-only root must not take the whole function down.
        fallback = Path(tempfile.gettempdir()) / "gemarc-workspace"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


WORKSPACE_ROOT = _resolve_workspace()
os.environ["GEMARC_WORKSPACE_ROOT"] = str(WORKSPACE_ROOT)

# Writes stay off unless an operator explicitly turns them on. Anything other
# than the exact value "1" is normalised to "0", so a stray "true" or "yes"
# from an outer shell cannot silently enable the write tools.
if os.environ.get("GEMARC_ALLOW_WRITE") != "1":
    os.environ["GEMARC_ALLOW_WRITE"] = "0"

SEED_PROJECT = "demo"
SEED_MODEL = REPO_ROOT / "examples" / "demo-site" / "data" / "architecture.json"
LEGACY_STATIC_DIR = REPO_ROOT / "examples" / "demo-site"

#: The console is a fixed part of the package, not a generated artefact: it is
#: the tuning panel for the MCP itself, so it must ship with the server.
CONSOLE_DIR = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "console"
CONSOLE_HTML = CONSOLE_DIR / "index.html"
SITE_PATH = "/site/"


def materialise_static_site() -> tuple[Path | None, str]:
    """Build the current scaffold site from the package sources.

    The committed ``examples/demo-site`` tree is the legacy MVP preview. The
    scaffold site is generated from the very same sources the MCP ships, so the
    deployed site can never drift from the code. Generation happens once per
    cold instance into a temp directory; if it fails we fall back to the
    committed preview rather than serving nothing.
    """
    try:
        from gemarc_mcp.model import normalized_model
        from gemarc_mcp.scaffold import builder

        model = json.loads(SEED_MODEL.read_text(encoding="utf-8"))
        target = Path(tempfile.mkdtemp(prefix="gemarc-site-"))
        builder.build_site(target, normalized_model(model), "static")
        return target, "scaffold-static"
    except Exception as exc:  # pragma: no cover - the fallback keeps the site up
        return None, f"{type(exc).__name__}: {exc}"


def seed_demo_project() -> bool:
    """Give the read-only tools something to read on a cold instance."""
    target = WORKSPACE_ROOT / SEED_PROJECT / "model" / "architecture.json"
    if target.exists():
        return False
    if not SEED_MODEL.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEED_MODEL, target)
    return True


SEEDED = seed_demo_project()

# --- MCP server ------------------------------------------------------------
_server = gemarc_server.create_server()
_mcp_app = _server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
    host="0.0.0.0",
)


def _run_session_manager() -> None:
    async def _main() -> None:
        async with _server.session_manager.run():
            await anyio.sleep_forever()

    global _session_error
    try:
        anyio.run(_main)
    except Exception as exc:  # pragma: no cover - the thread is best-effort
        # Recorded rather than swallowed: a silent failure here surfaces at
        # request time as "Task group is not initialized", which is impossible
        # to diagnose without this marker.
        _session_error = f"{type(exc).__name__}: {exc}"


_session_error = ""
_session_thread = threading.Thread(
    target=_run_session_manager, daemon=True, name="gemarc-mcp-session"
)
_session_thread.start()


# --- static site -----------------------------------------------------------
_generated_site, _site_source = materialise_static_site()
STATIC_DIR = _generated_site or LEGACY_STATIC_DIR
STATIC_AVAILABLE = (STATIC_DIR / "index.html").is_file()
STATIC_SOURCE = _site_source if _generated_site else "committed-preview"
if not _generated_site:
    # Keep the reason visible instead of letting the fallback look like success.
    STATIC_SOURCE = f"committed-preview (generation failed: {_site_source})"


async def _static_missing(scope, receive, send):
    response = Response(
        "GemArc static site is not present in this deployment bundle. "
        "The MCP endpoint at /mcp is unaffected.",
        status_code=404,
        media_type="text/plain; charset=utf-8",
    )
    await response(scope, receive, send)


static_app = (
    StaticFiles(directory=str(STATIC_DIR), html=True)
    if STATIC_AVAILABLE
    else _static_missing
)


# --- console ----------------------------------------------------------------
def _console_config() -> dict:
    """Values the browser needs, taken from the running server."""
    return {
        "mcpPath": "/mcp",
        "healthPath": "/healthz",
        "sitePath": SITE_PATH,
        "writeTools": sorted(gemarc_server.WRITE_TOOLS),
        "version": getattr(gemarc_server, "__version__", "0.2.0"),
    }


def render_console() -> str:
    """Fill the console shell with the server's own configuration."""
    html = CONSOLE_HTML.read_text(encoding="utf-8")
    config = _console_config()
    html = html.replace("__MCP_PATH__", config["mcpPath"])
    html = html.replace("__HEALTH_PATH__", config["healthPath"])
    html = html.replace("__SITE_PATH__", config["sitePath"])
    html = html.replace("__VERSION__", config["version"])
    html = html.replace("__WRITE_TOOLS__", json.dumps(config["writeTools"], ensure_ascii=False))
    return html


async def _console_endpoint(scope, receive, send):
    if not CONSOLE_HTML.is_file():
        response = Response(
            "The console shell is not present in this deployment bundle. "
            "The MCP endpoint at /mcp is unaffected.",
            status_code=404,
            media_type="text/plain; charset=utf-8",
        )
        await response(scope, receive, send)
        return
    response = HTMLResponse(render_console())
    await response(scope, receive, send)


async def _console_asset(scope, receive, send, name: str):
    """Serve one console asset (css/js) from the package."""
    target = (CONSOLE_DIR / name).resolve()
    if target.parent != CONSOLE_DIR.resolve() or not target.is_file():
        response = Response("not found", status_code=404, media_type="text/plain; charset=utf-8")
    else:
        media = "text/css" if name.endswith(".css") else "text/javascript"
        response = Response(target.read_bytes(), media_type=media)
    await response(scope, receive, send)


# --- dispatch --------------------------------------------------------------
def _health_payload() -> dict:
    return {
        "status": "ok",
        "server": "gemarc-mcp",
        "transport": "streamable-http",
        "protocolPath": "/mcp",
        "workspace": str(WORKSPACE_ROOT),
        "writeEnabled": os.environ.get("GEMARC_ALLOW_WRITE") == "1",
        "seedProject": SEED_PROJECT,
        "seeded": SEEDED,
        "staticSiteAvailable": STATIC_AVAILABLE,
        "staticSiteSource": STATIC_SOURCE,
        "staticSitePath": SITE_PATH,
        "consoleAvailable": CONSOLE_HTML.is_file(),
        "writeTools": sorted(gemarc_server.WRITE_TOOLS),
        "sessionManagerThreadAlive": _session_thread.is_alive(),
        "sessionManagerError": _session_error,
    }


async def healthz(_request):
    return JSONResponse(_health_payload())


async def _health_endpoint(scope, receive, send):
    response = JSONResponse(_health_payload())
    await response(scope, receive, send)


async def _mcp_endpoint(scope, receive, send):
    """Normalise the path so the MCP app always sees its own /mcp route."""
    normalised = dict(scope)
    normalised["path"] = "/mcp"
    normalised["raw_path"] = b"/mcp"
    await _mcp_app(normalised, receive, send)


MCP_PATHS = ("/mcp", "/api/index", "/api/index.py", "/index.py")
CONSOLE_ASSETS = ("console.css", "/console.js")


def _strip_site_prefix(scope, path: str):
    """Rewrite /site/... to the path the static mount actually serves."""
    remainder = "/" + path[len(SITE_PATH):]
    rewritten = dict(scope)
    rewritten["path"] = remainder
    raw = scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)):
        rewritten["raw_path"] = remainder.encode("utf-8")
    return rewritten


async def dispatch(scope, receive, send):
    path = scope.get("path", "/")
    if path.endswith("healthz"):
        await _health_endpoint(scope, receive, send)
    elif path in MCP_PATHS or path.rstrip("/").endswith("/mcp"):
        await _mcp_endpoint(scope, receive, send)
    elif path in ("/console.css", "/console.js"):
        await _console_asset(scope, receive, send, path.rsplit("/", 1)[-1])
    elif path == "/" or path == "":
        # The home page is the MCP console, not a rendering of one design.
        await _console_endpoint(scope, receive, send)
    elif path == SITE_PATH or path.startswith(SITE_PATH):
        # The scaffold product itself stays reachable, under /site/.
        await static_app(_strip_site_prefix(scope, path), receive, send)
    else:
        await static_app(scope, receive, send)


app = Starlette(routes=[Route("/healthz", healthz), Mount("/", app=dispatch)])
