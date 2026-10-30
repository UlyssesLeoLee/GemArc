#!/usr/bin/env python3
"""Post-deploy probe for a remote GemArc MCP deployment (Vercel or any host).

    python scripts/verify_remote.py https://<project>.vercel.app
    python scripts/verify_remote.py https://<project>.vercel.app --token <T>      # or env GEMARC_MCP_TOKEN
    python scripts/verify_remote.py <url> --header "x-vercel-protection-bypass: <secret>"   # repeatable
    python scripts/verify_remote.py --local                                       # http://127.0.0.1:8000

Standard library only (urllib). Every check prints PASS, FAIL or INFO with the
reason; the exit code is 1 when any check FAILs, 0 otherwise. The bearer token
is only ever sent in the Authorization header and is never printed.

--local starts nothing: it just defaults the URL to http://127.0.0.1:8000 and
skips the Vercel-specific advice. Start the server yourself, for example
`uvicorn api.index:app --port 8000`.

If the URL is behind Vercel Deployment Protection the probe stops at the first
response and says so: use the production alias, `vercel curl`, or pass the
bypass header with --header.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
STATELESS_TOOLS = (
    "gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model",
    "gemarc_validate", "gemarc_simulate", "gemarc_diff", "gemarc_render",
)
PROTOCOL_VERSION = "2025-06-18"
GET_MCP_BUDGET_S = 5.0
PROTECTION_HINT = ("Deployment Protection is on for this URL - use the production alias "
                   "or `vercel curl` (or pass the bypass header with --header)")
#: URL paths that legitimately appear in /healthz; anything else path-like is a leak.
PUBLIC_URL_PATHS = {"/mcp", "/site/", "/healthz"}
_FS_SHAPES = (
    re.compile(r"^[A-Za-z]:[\\/]"),
    re.compile(r"\\"),
    re.compile(r"^/(tmp|var|home|usr|opt|Users|private|workspace|vercel|task|root|mnt|etc)\b"),
    re.compile(r"gemarc-(workspace|site|kit)-?", re.IGNORECASE),
)
_PROTECTION_MARKERS = ("vercel.com/sso", "_vercel_sso_nonce", "authentication required",
                       "vercel authentication", "deployment protection", "vercel.com/login",
                       "/sso-api", "vercel.com/api/")


# --- pure helpers (imported by the test suite) ---------------------------------

def probe_model() -> dict:
    """A tiny valid model (3 nodes, 2 edges, 1 view, 1 scenario); ASCII only, not real facts."""
    def node(ident, kind, name, layer):
        return {"attributes": [], "domainId": None, "evidence": "probe", "evidenceIds": [], "id": ident,
                "kind": kind, "layer": layer, "methods": [], "name": name, "parentId": None,
                "properties": {}, "role": None, "spaceRef": None, "verification": "Hypothetical"}

    def edge(ident, source, target):
        return {"condition": None, "description": "probe edge", "directed": True, "evidence": "probe",
                "evidenceIds": [], "id": ident, "properties": {}, "sourceId": source, "spaceSpan": None,
                "targetId": target, "trustBoundary": None, "type": "sends",
                "verification": "Hypothetical", "weight": None}

    return {
        "cases": [], "evidence": [], "prototypes": [], "workflows": [], "space": None,
        "edges": [edge("e1", "src", "svc"), edge("e2", "svc", "out")],
        "meta": {"description": "probe model, not real facts", "evidenceState": "Hypothetical",
                 "language": "en", "schemaVersion": "0.1.0", "system": "probe", "version": "0.1.0"},
        "nodes": [node("src", "Component", "Source", "edge"), node("svc", "Service", "Service", "app"),
                  node("out", "Service", "Output", "app")],
        "scenarios": [{"events": [{"event": "emit", "module": "src", "timeMs": 0}],
                       "evidenceClass": "hypothetical", "id": "normal", "name": "probe flow",
                       "rules": [{"delayMs": 10, "emit": "ingest", "source": "src", "target": "svc", "whenEvent": "emit"},
                                 {"delayMs": 10, "emit": "send", "source": "svc", "target": "out", "whenEvent": "ingest"}]}],
        "views": [{"concerns": ["flow"], "diagramType": "logical-view", "edgeKinds": ["sends"],
                   "id": "logical", "nodeIds": ["src", "svc", "out"], "reviewIntent": "probe",
                   "stakeholders": ["architect"], "title": "Logical"}],
    }


def path_like_values(value) -> list[str]:
    """String values that look like server filesystem locations (URL paths in PUBLIC_URL_PATHS are fine)."""
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found += path_like_values(item)
    elif isinstance(value, list):
        for item in value:
            found += path_like_values(item)
    elif isinstance(value, str) and value not in PUBLIC_URL_PATHS:
        if any(shape.search(value) for shape in _FS_SHAPES):
            found.append(value)
    return found


def looks_like_vercel_protection(status, content_type: str, body: bytes) -> bool:
    """Heuristic: an HTML login/SSO challenge instead of the JSON/HTML this server would send."""
    text = body[:20000].decode("utf-8", "replace").lower()
    is_html = "html" in (content_type or "").lower() or text.lstrip().startswith(("<!doctype", "<html"))
    if not is_html:
        return False
    return status == 401 or any(marker in text for marker in _PROTECTION_MARKERS)


def parse_jsonrpc(body: bytes):
    """A JSON-RPC response from either a JSON body or a one-event SSE body."""
    text = body.decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                continue
    return None


# --- the probe -------------------------------------------------------------------

class Response:
    def __init__(self, status, headers, body, elapsed, error=None):
        self.status, self.headers, self.body, self.elapsed, self.error = status, headers or {}, body or b"", elapsed, error

    def header(self, name: str) -> str:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return ""

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except ValueError:
            return None


class Probe:
    def __init__(self, base: str, token: str, extra_headers: list[str], local: bool):
        self.base = base.rstrip("/")
        self.token = token
        self.local = local
        self.extra = {}
        for raw in extra_headers:
            name, _, value = raw.partition(":")
            if not name.strip() or not _:
                raise SystemExit(f"--header must look like 'Name: value', got {raw!r}")
            self.extra[name.strip()] = value.strip()
        self.results: list[tuple[str, str, str]] = []
        self.protected = False

    # -- output ------------------------------------------------------------

    def redact(self, text: str) -> str:
        for secret in (self.token, *[v for k, v in self.extra.items() if k.lower() == "authorization"]):
            if secret:
                text = text.replace(secret, "***")
        return text

    def record(self, status: str, name: str, detail: str = "") -> bool:
        line = f"{status:<4}  {name}" + (f" - {self.redact(detail)}" if detail else "")
        print(line, flush=True)
        self.results.append((status, name, detail))
        return status != "FAIL"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        return self.record("PASS" if ok else "FAIL", name, detail)

    def info(self, name: str, detail: str = "") -> None:
        self.record("INFO", name, detail)

    # -- transport -----------------------------------------------------------

    def request(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None,
                token: bool | str = True, timeout: float = 30.0) -> Response:
        url = path if path.startswith("http") else self.base + path
        merged = dict(self.extra)
        if token is False:
            merged = {k: v for k, v in merged.items() if k.lower() != "authorization"}
        elif isinstance(token, str):
            merged["Authorization"] = f"Bearer {token}"
        elif self.token:
            merged["Authorization"] = f"Bearer {self.token}"
        merged.update(headers or {})
        merged.setdefault("User-Agent", "gemarc-verify-remote/1")
        request = urllib.request.Request(url, data=body, method=method, headers=merged)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:
                response = Response(raw.status, dict(raw.headers.items()), raw.read(), time.monotonic() - started)
        except urllib.error.HTTPError as error:
            response = Response(error.code, dict(error.headers.items()), error.read(), time.monotonic() - started)
        except Exception as error:  # timeout, DNS, TLS, connection reset
            response = Response(None, {}, b"", time.monotonic() - started, f"{type(error).__name__}: {error}")
        if response.status and looks_like_vercel_protection(response.status, response.header("content-type"), response.body):
            self.protected = True
        return response

    def rpc(self, method: str, params: dict | None = None, *, token: bool | str = True, rid: int = 1):
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}).encode()
        response = self.request("POST", "/mcp", body=payload, token=token, headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        return response, parse_jsonrpc(response.body) if response.status == 200 else None

    def call_tool(self, name: str, arguments: dict):
        response, message = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if not message or "result" not in message:
            return response, None, None
        result = message["result"]
        text = next((part.get("text") for part in result.get("content", []) if part.get("type") == "text"), None)
        try:
            payload = json.loads(text) if text is not None else None
        except ValueError:
            payload = None
        return response, payload, bool(result.get("isError"))


def describe(response: Response) -> str:
    if response.error:
        return response.error
    return f"HTTP {response.status} in {response.elapsed:.2f}s"


def stop_if_protected(probe: Probe, response: Response) -> bool:
    if probe.protected:
        probe.check("reachable without a Deployment Protection challenge", False,
                    f"{describe(response)}; {PROTECTION_HINT}")
        return True
    return False


# --- the checks ------------------------------------------------------------------

def run_checks(probe: Probe) -> None:
    # -- health ------------------------------------------------------------------
    response = probe.request("GET", "/healthz", token=False)
    if stop_if_protected(probe, response):
        return
    report = response.json() if response.status == 200 else None
    if not probe.check("/healthz answers ok", bool(report) and report.get("status") == "ok",
                       describe(response) if not report else
                       f"status={report.get('status')} version={report.get('version')} "
                       f"toolset={report.get('toolset')} authRequired={report.get('authRequired')} "
                       f"sessionManager={report.get('sessionManager')}"):
        probe.info("remaining checks skipped", "the server is not healthy")
        return
    leaks = path_like_values(report)
    probe.check("/healthz leaks no filesystem path", not leaks, f"path-like values: {leaks}" if leaks else "")
    probe.check("/healthz carries no token material", not probe.token or probe.token not in response.body.decode("utf-8", "replace"))
    auth_required = bool(report.get("authRequired"))
    toolset = report.get("toolset")

    # -- static content ----------------------------------------------------------
    response = probe.request("GET", "/", token=False)
    body = response.body.decode("utf-8", "replace")
    probe.check("/ is the HTML console",
                response.status == 200 and "html" in response.header("content-type").lower()
                and "GemArc MCP" in body and "__MCP_PATH__" not in body, describe(response))
    response = probe.request("GET", "/site/", token=False, timeout=60)
    probe.check("/site/ answers 200", response.status == 200 and b"<html" in response.body.lower(), describe(response))

    # -- the method gate -----------------------------------------------------------
    response = probe.request("GET", "/mcp", token=False, timeout=GET_MCP_BUDGET_S)
    if response.error:
        probe.check("GET /mcp -> 405 immediately", False,
                    f"{response.error} (an SSE stream held open? budget {GET_MCP_BUDGET_S:.0f}s)")
    else:
        probe.check("GET /mcp -> 405 immediately",
                    response.status == 405 and "POST" in response.header("allow") and response.elapsed < GET_MCP_BUDGET_S,
                    f"{describe(response)} Allow={response.header('allow')!r}")
    for method in ("HEAD", "DELETE"):
        response = probe.request(method, "/mcp", token=False, timeout=GET_MCP_BUDGET_S)
        probe.check(f"{method} /mcp -> 405", response.status == 405, describe(response))
    response = probe.request("GET", "/.well-known/oauth-protected-resource", token=False)
    probe.check("OAuth discovery probe -> clean 404 JSON",
                response.status == 404 and "json" in response.header("content-type").lower(), describe(response))

    # -- authentication ------------------------------------------------------------
    response, _ = probe.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                           "clientInfo": {"name": "verify-remote", "version": "1"}}, token=False)
    if auth_required:
        probe.check("POST /mcp without a token -> 401",
                    response.status == 401 and "bearer" in response.header("www-authenticate").lower(), describe(response))
        wrong = probe.rpc("tools/list", token="not-the-token")[0]
        probe.check("POST /mcp with a wrong token -> 401", wrong.status == 401, describe(wrong))
        if not probe.token:
            probe.check("a bearer token is available", False, "the server requires one: use --token or env GEMARC_MCP_TOKEN")
            return
    else:
        probe.check("POST /mcp without a token -> 200 (authless)", response.status == 200, describe(response))
        if probe.token:
            probe.info("token ignored", "the server has no token configured; the supplied one is not needed")

    # -- the MCP surface -----------------------------------------------------------
    response, message = probe.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                 "clientInfo": {"name": "verify-remote", "version": "1"}})
    result = (message or {}).get("result") or {}
    if response.status == 401:
        probe.check("initialize with the token", False, "HTTP 401: the server rejected this token (rotated, or the wrong environment?)")
        return
    probe.check("initialize",
                bool(result.get("serverInfo", {}).get("name")) and bool(result.get("protocolVersion")),
                f"server={result.get('serverInfo', {}).get('name')!r} protocolVersion={result.get('protocolVersion')!r}"
                if result else describe(response))
    response, message = probe.rpc("tools/list", rid=2)
    names = [tool.get("name", "") for tool in ((message or {}).get("result") or {}).get("tools", [])]
    expected = list(STATELESS_TOOLS) if toolset == "stateless" else list(report.get("tools") or [])
    probe.check("tools/list matches the advertised tool set",
                bool(names) and sorted(names) == sorted(expected),
                f"{len(names)} tools; missing={sorted(set(expected) - set(names))} extra={sorted(set(names) - set(expected))}"
                if names else describe(response))
    illegal = [n for n in names if not TOOL_NAME.match(n)]
    probe.check("every tool name matches ^[a-zA-Z0-9_-]{1,64}$", bool(names) and not illegal, f"illegal: {illegal}" if illegal else "")
    if not names or "gemarc_validate" not in names:
        return

    model = probe_model()
    _, payload, is_error = probe.call_tool("gemarc_new_model", {"system": "Remote Probe"})
    probe.check("gemarc_new_model returns an empty model",
                bool(payload) and payload.get("status") == "ok" and isinstance(payload.get("model"), dict) and not is_error)
    empty = (payload or {}).get("model")
    if isinstance(empty, dict):
        _, payload, _ = probe.call_tool("gemarc_validate", {"model": empty})
        probe.check("gemarc_validate accepts that empty model", bool(payload) and payload.get("status") == "ok",
                    f"status={(payload or {}).get('status')}")
    _, payload, is_error = probe.call_tool("gemarc_validate", {"model": model, "strict": False})
    probe.check("gemarc_validate (inline probe model) -> ok", bool(payload) and payload.get("status") == "ok" and not is_error,
                f"status={(payload or {}).get('status')}")
    _, payload, is_error = probe.call_tool("gemarc_validate", {"project": "demo"})
    probe.check('gemarc_validate (project="demo") -> ok', bool(payload) and payload.get("status") == "ok" and not is_error,
                f"status={(payload or {}).get('status')}")
    _, payload, _ = probe.call_tool("gemarc_simulate", {"model": model, "scenario_id": "normal"})
    delivered = ((payload or {}).get("summary") or {}).get("delivered")
    probe.check("gemarc_simulate (probe scenario) delivers 3 events", delivered == 3, f"delivered={delivered}")
    _, payload, is_error = probe.call_tool("gemarc_render", {"model": model, "profile": "static"})
    kit = (payload or {}).get("kit") or {}
    probe.check("gemarc_render (probe model) -> ok with files and a kit",
                bool(payload) and payload.get("status") == "ok" and len(payload.get("files", [])) == 3 and bool(kit.get("path"))
                and not is_error, f"status={(payload or {}).get('status')} kit.path={kit.get('path')}")
    if not kit.get("path"):
        return

    # -- the site kit ----------------------------------------------------------------
    response = probe.request("GET", kit["path"], token=False, timeout=60)
    digest = hashlib.sha256(response.body).hexdigest()
    probe.check("kit download matches kit.archiveSha256",
                response.status == 200 and digest == kit.get("archiveSha256"),
                f"{describe(response)} {len(response.body)} bytes sha256={digest[:16]}... expected={str(kit.get('archiveSha256'))[:16]}...")
    probe.check("kit download is an attachment with an ETag",
                "zip" in response.header("content-type").lower() and "attachment" in response.header("content-disposition")
                and response.header("etag").strip('"') == digest,
                f"type={response.header('content-type')!r} etag={response.header('etag')[:20]!r}")
    cached = probe.request("GET", kit["path"], token=False, headers={"If-None-Match": response.header("etag")})
    probe.check("kit honours If-None-Match with 304", cached.status == 304, describe(cached))
    if kit.get("url"):
        remote = probe.request("GET", kit["url"], token=False, timeout=60)
        same = remote.status == 200 and hashlib.sha256(remote.body).hexdigest() == kit.get("archiveSha256")
        probe.check("kit.url is reachable and identical", same, f"{kit['url']} -> {describe(remote)}")
    else:
        probe.info("kit.url is null", "set GEMARC_PUBLIC_URL (or enable Vercel system environment variables) so render returns an absolute URL")

    # -- official client (optional) ----------------------------------------------------
    sdk_handshake(probe)

    # -- session manager state, after real traffic ------------------------------------------
    after = probe.request("GET", "/healthz", token=False).json() or {}
    probe.check("session manager is running after traffic", after.get("sessionManager") in ("lifespan", "lazy"),
                f"sessionManager={after.get('sessionManager')} status={after.get('status')}")


def sdk_handshake(probe: Probe) -> None:
    try:
        import asyncio
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except Exception as error:  # the SDK is optional for this script
        probe.info("official MCP client handshake skipped", f"mcp 2.x client not importable ({type(error).__name__})")
        return
    headers = dict(probe.extra)
    if probe.token:
        headers["Authorization"] = f"Bearer {probe.token}"

    async def handshake():
        async with httpx2.AsyncClient(headers=headers, timeout=30) as client:
            async with streamable_http_client(probe.base + "/mcp", http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = (await session.list_tools()).tools
                    return init.server_info.name, init.protocol_version, len(tools)

    try:
        name, version, count = asyncio.run(handshake())
        probe.check("official MCP client handshake", count > 0, f"server={name!r} protocol={version} tools={count}")
    except BaseException as error:  # noqa: BLE001 - report any client failure as a FAIL
        probe.check("official MCP client handshake", False, f"{type(error).__name__}: {error}")


# --- entry point -----------------------------------------------------------------------

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Probe a deployed GemArc MCP server.")
    parser.add_argument("url", nargs="?", help="base URL, e.g. https://<project>.vercel.app")
    parser.add_argument("--token", default=None, help="bearer token (default: env GEMARC_MCP_TOKEN)")
    parser.add_argument("--header", action="append", default=[], metavar="'Name: value'",
                        help="extra request header, repeatable (for example a Vercel protection-bypass header)")
    parser.add_argument("--local", action="store_true",
                        help="convenience: default the URL to http://127.0.0.1:8000 and skip Vercel advice (starts nothing)")
    args = parser.parse_args(argv)
    url = args.url or ("http://127.0.0.1:8000" if args.local else None)
    if not url:
        parser.error("give the deployment URL (or --local)")
    token = args.token if args.token is not None else os.environ.get("GEMARC_MCP_TOKEN", "")
    probe = Probe(url, token.strip(), args.header, args.local)
    print(f"GemArc remote probe: {probe.base}  (token: {'given' if probe.token else 'none'}, "
          f"extra headers: {len(probe.extra)})")
    run_checks(probe)
    failed = [name for status, name, _ in probe.results if status == "FAIL"]
    passed = sum(1 for status, *_ in probe.results if status == "PASS")
    print(f"\n{passed} passed, {len(failed)} failed")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
