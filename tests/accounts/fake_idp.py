"""Test support for the account flow: a local fake Google, a cookie-keeping browser and a live app on a random port.

* ``FakeIdp``     stdlib ``http.server`` on 127.0.0.1:0 implementing the authorization-code flow of Google closely
                  enough for the app: ``/authorize`` (auto-approves, 302 back with ``code`` and ``state``), ``/token``
                  (client secret, redirect uri, single-use code, PKCE S256) and ``/jwks`` (a throw-away RSA key made
                  with ``cryptography``). Faults are switched on per test through ``idp.faults``.
* ``Browser``     a cookie jar over ``urllib`` that never follows a redirect.
* ``AccountsServer`` ``api.index.build_app(accounts=...)`` under uvicorn in a thread. The port is chosen BEFORE the app
                  is built because the configured base URL must be exactly ``http://127.0.0.1:<port>``.

Nothing here talks to a real network service.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from gemarc_mcp.accounts import schema  # noqa: E402
from gemarc_mcp.accounts.db import Database  # noqa: E402
from gemarc_mcp.accounts.oidc import IdpEndpoints  # noqa: E402

from .support import KEY1, Clock  # noqa: E402

CLIENT_ID = "test-client-id.apps.fake"
CLIENT_SECRET = "test-client-secret-value"
GONE = object()  # remove a claim
ENV_RESTORE = ("GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "VERCEL")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def int_b64(n: int) -> str:
    return b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class FakeIdp:
    def __init__(self, redirect_uri: str, clock: Clock, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET):
        self.redirect_uri = redirect_uri  # the ONE registered redirect URI
        self.client_id, self.client_secret = client_id, client_secret
        self.clock = clock
        self.identity = {"sub": "sub-0001", "email": "alice@gmail.com"}
        self.faults: dict = {}
        self.codes: dict = {}
        self.violations: "list[str]" = []
        self.authorize_calls: "list[dict]" = []
        self.token_calls: "list[dict]" = []
        self.jwks_hits = 0
        self._lock = threading.Lock()
        self._keys = {}
        self._server = None
        self.base = ""

    # -- RSA keys for the signature tests ----------------------------------------------------------------------
    def key(self, name: str = "good"):
        from cryptography.hazmat.primitives.asymmetric import rsa

        with self._lock:
            if name not in self._keys:
                self._keys[name] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            return self._keys[name]

    def jwk(self) -> dict:
        numbers = self.key("good").public_key().public_numbers()
        return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "kid-good", "n": int_b64(numbers.n),
                "e": int_b64(numbers.e)}

    def sign(self, signing_input: bytes, key_name: str) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        return self.key(key_name).sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    # -- lifecycle ---------------------------------------------------------------------------------------------
    def start(self) -> "FakeIdp":
        idp = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def _send(self, status, body=b"", ctype="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:  # the client gave up (oversized-body test)
                    pass

            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}
                if parts.path == "/authorize":
                    idp._authorize(self, query)
                elif parts.path == "/jwks":
                    idp._jwks(self)
                else:
                    self._send(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
                if self.path == "/token":
                    idp._token(self, form)
                else:
                    self._send(404, b"{}")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def endpoints(self) -> IdpEndpoints:
        return IdpEndpoints(self.base + "/authorize", self.base + "/token", self.base + "/jwks")

    # -- endpoints ---------------------------------------------------------------------------------------------
    def _authorize(self, h, q: dict) -> None:
        self.authorize_calls.append(dict(q))
        if q.get("client_id") != self.client_id or q.get("redirect_uri") != self.redirect_uri:
            return h._send(400, b'{"error":"redirect_uri_mismatch"}')
        if q.get("response_type") != "code":
            self.violations.append("response_type")
        if q.get("scope") != "openid email":
            self.violations.append("scope")
        if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
            self.violations.append("pkce")
        if q.get("prompt") != "select_account":
            self.violations.append("prompt")
        for forbidden in ("hd", "login_hint", "access_type", "include_granted_scopes", "response_mode"):
            if forbidden in q:
                self.violations.append(forbidden)
        if not q.get("state") or not q.get("nonce"):
            self.violations.append("state_nonce")
        if "authorize_error" in self.faults:
            target = f"{self.redirect_uri}?error={self.faults['authorize_error']}&state={q.get('state', '')}"
            return h._send(302, b"", headers={"Location": target})
        code = secrets.token_urlsafe(24)
        with self._lock:
            self.codes[code] = {"identity": dict(self.identity), "nonce": q.get("nonce"),
                                "challenge": q.get("code_challenge"), "used": False}
        state = self.faults.get("state", q.get("state", ""))
        params = {"code": code, "state": state}
        if "redirect_iss" in self.faults:
            params["iss"] = self.faults["redirect_iss"]
        h._send(302, b"", headers={"Location": self.redirect_uri + "?" + urllib.parse.urlencode(params)})

    def _jwks(self, h) -> None:
        self.jwks_hits += 1
        if self.faults.get("jwks_fail"):
            return h._send(500, b"{}")
        h._send(200, json.dumps({"keys": [self.jwk()]}).encode())

    def _claims(self, record: dict) -> dict:
        now = int(self.clock())
        who = record["identity"]
        claims = {"iss": "https://accounts.google.com", "azp": self.client_id, "aud": self.client_id,
                  "sub": who["sub"], "email": who["email"], "email_verified": who.get("email_verified", True),
                  "nonce": record["nonce"], "iat": now, "exp": now + 3600}
        if who.get("hd"):
            claims["hd"] = who["hd"]
        for key, value in self.faults.get("claims", {}).items():
            if value is GONE:
                claims.pop(key, None)
            else:
                claims[key] = value
        return claims

    def _id_token(self, record: dict) -> str:
        claims = self._claims(record)
        header = {"alg": self.faults.get("alg", "RS256"), "kid": self.faults.get("kid", "kid-good"), "typ": "JWT"}
        payload = self.faults.get("raw_payload") or json.dumps(claims)
        head = b64u(json.dumps(header).encode())
        body = b64u(payload.encode())
        mode = self.faults.get("sign", "none")
        if mode == "none":
            sig = b64u(b"unsigned-test-signature")
        else:
            sig = b64u(self.sign((head + "." + body).encode("ascii"), "good" if mode == "good" else "other"))
        return f"{head}.{body}.{sig}"

    def _token(self, h, form: dict) -> None:
        self.token_calls.append({k: v for k, v in form.items() if k not in ("client_secret",)})
        if self.faults.get("token_delay"):
            time.sleep(self.faults["token_delay"])
        if self.faults.get("token_redirect"):
            return h._send(302, b"", headers={"Location": self.base + "/elsewhere"})
        if self.faults.get("token_status"):
            return h._send(self.faults["token_status"], b'{"error":"server_error"}')
        if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return h._send(401, b'{"error":"invalid_client"}')
        if form.get("redirect_uri") != self.redirect_uri or form.get("grant_type") != "authorization_code":
            return h._send(400, b'{"error":"invalid_request"}')
        with self._lock:
            record = self.codes.get(form.get("code", ""))
            if record is None or record["used"]:
                return h._send(400, b'{"error":"invalid_grant"}')
            record["used"] = True
        verifier = form.get("code_verifier", "")
        if b64u(hashlib.sha256(verifier.encode("ascii", "replace")).digest()) != record["challenge"] or not verifier:
            return h._send(400, b'{"error":"invalid_grant"}')
        if "token_body" in self.faults:
            return h._send(200, self.faults["token_body"], ctype=self.faults.get("token_ctype", "application/json"))
        payload = {"access_token": "fake-access-token", "expires_in": 3599, "token_type":
                   self.faults.get("token_type", "Bearer"), "scope": "openid email"}
        if not self.faults.get("no_id_token"):
            payload["id_token"] = self._id_token(record)
        if self.faults.get("big"):
            payload["padding"] = "x" * 70000
        h._send(200, json.dumps(payload).encode(), ctype=self.faults.get("token_ctype", "application/json"))


class Resp:
    def __init__(self, status: int, headers, body: bytes):
        self.status, self.headers, self.body = status, headers, body

    @property
    def location(self) -> str:
        return self.headers.get("Location", "")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def set_cookies(self) -> "list[str]":
        return self.headers.get_all("Set-Cookie") or []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    """Cookie-keeping client. Never follows a redirect; ``base`` is the app origin used for Origin headers."""

    def __init__(self, base: str):
        self.base = base
        self.cookies: "dict[str, str]" = {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _remember(self, resp: Resp) -> None:
        for line in resp.set_cookies():
            first, *attrs = [p.strip() for p in line.split(";")]
            name, _, value = first.partition("=")
            if "max-age=0" in [a.lower() for a in attrs] or value == "":
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    def request(self, method: str, url: str, *, headers=None, data=None, cookies=True) -> Resp:
        if url.startswith("/"):
            url = self.base + url
        merged = dict(headers or {})
        if cookies and self.cookies:
            merged.setdefault("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        request = urllib.request.Request(url, data=data, method=method, headers=merged)
        try:
            with self.opener.open(request, timeout=20) as response:
                resp = Resp(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            resp = Resp(error.code, error.headers, error.read())
        if cookies:
            self._remember(resp)
        return resp

    def get(self, url: str, **kw) -> Resp:
        return self.request("GET", url, **kw)

    def nav(self, url: str, **kw) -> Resp:
        return self.get(url, headers={"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                                      **kw.pop("headers", {})}, **kw)

    def post(self, url: str, fields: dict, *, origin="default", headers=None, **kw) -> Resp:
        merged = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin == "default":
            merged["Origin"] = self.base
        elif origin is not None:
            merged["Origin"] = origin
        merged.update(headers or {})
        return self.request("POST", url, headers=merged, data=urllib.parse.urlencode(fields).encode(), **kw)

    def csrf(self, html: str) -> str:
        marker = 'name="csrf" value="'
        start = html.index(marker) + len(marker)
        return html[start:html.index('"', start)]


def sign_in(browser: Browser, idp: FakeIdp, identity=None, *, via_authorize=True):
    """Run start -> fake Google -> callback. Returns (start, authorize, callback) responses."""
    if identity is not None:
        idp.identity = dict(identity)
    start = browser.nav("/auth/google/start")
    if start.status != 302:
        return start, None, None
    plain = Browser(idp.base)  # Google does not see the app's cookies
    authorize = plain.get(start.location, cookies=False)
    if authorize.status != 302:
        return start, authorize, None
    callback = browser.nav(authorize.location)
    return start, authorize, callback


class AccountsServer:
    """The deployable app with accounts enabled, served by uvicorn on a pre-chosen port."""

    def __init__(self, tmp: str, clock: Clock, *, env=None, toolset=None, token=None, accounts_extra=None,
                 write=False, db_name="accounts.db", migrate=True, idp: "FakeIdp | None" = None):
        self.tmp, self.clock = tmp, clock
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.db_path = os.path.join(tmp, db_name)
        self.env = {
            "GEMARC_ACCOUNTS": "1",
            "GEMARC_ACCOUNTS_BASE_URL": self.base,
            "GEMARC_GOOGLE_CLIENT_ID": CLIENT_ID,
            "GEMARC_GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
            "GEMARC_ACCOUNTS_KEYS": "k1:" + b64u(KEY1),
            "GEMARC_ACCOUNTS_DATABASE_URL": "sqlite:///" + self.db_path.replace("\\", "/"),
            "GEMARC_ACCOUNTS_ALLOW_SQLITE": "1",
            "GEMARC_MCP_AUTH": "pat",
        }
        self.env.update(env or {})
        self.toolset, self.token, self.write = toolset, token, write
        self.accounts_extra = dict(accounts_extra or {})
        self.idp = idp or FakeIdp(self.base + "/auth/google/callback", clock)
        self.migrate = migrate
        self.server = self.thread = self.app = None
        self.workspace = None
        self._saved = {}

    def database(self) -> Database:
        return Database.sqlite(self.db_path)

    def start(self) -> "AccountsServer":
        import uvicorn

        self.idp.start()
        if self.migrate:
            schema.migrate(self.database(), self.clock())
        self._saved = {k: os.environ.get(k) for k in ENV_RESTORE}
        for key in ENV_RESTORE:
            os.environ.pop(key, None)
        self.workspace = tempfile.mkdtemp(prefix="gemarc-acct-ws-")
        from api.index import build_app

        kwargs = {"workspace_root": self.workspace,
                  "accounts": {"environ": self.env, "idp": self.idp.endpoints, "clock": self.clock,
                               **self.accounts_extra}}
        if self.toolset:
            kwargs["toolset"] = self.toolset
        if self.token is not None:
            kwargs["token"] = self.token
        if self.write:
            kwargs["allow_write"] = True
        self.app = build_app(**kwargs)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not self.server.started:
            time.sleep(0.05)
        if not self.server.started:
            self.stop()
            raise unittest.SkipTest("uvicorn did not start in time")
        return self

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.idp.stop()
        if self.workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)

    def url(self, path: str = "") -> str:
        return self.base + path

    def runtime(self):
        return self.app.state.gemarc_accounts()


class ServerCase(unittest.TestCase):
    """Base class: one fresh database, fake IdP and live app per test (fixed fake clock)."""

    server_kwargs: dict = {}

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gemarc-flow-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = Clock()
        self.srv = AccountsServer(self.tmp, self.clock, **self.server_kwargs).start()
        self.addCleanup(self.srv.stop)
        self.idp = self.srv.idp
        self.browser = Browser(self.srv.base)

    def login(self, identity=None, browser=None):
        browser = browser or self.browser
        return sign_in(browser, self.idp, identity)

    def logged_in_page(self, identity=None, browser=None) -> str:
        browser = browser or self.browser
        _s, _a, callback = self.login(identity, browser)
        self.assertEqual(callback.status, 303, callback.text[:300])
        page = browser.get("/account")
        self.assertEqual(page.status, 200)
        return page.text

    def dump(self) -> str:
        parts = []
        with self.srv.database().transaction() as tx:
            for table in [r["name"] for r in tx.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")]:
                if table.startswith("sqlite_"):
                    continue
                for row in tx.fetchall("SELECT * FROM " + table):  # test-only helper
                    parts.append(table + "|" + "|".join(str(v) for v in row.values()))
        return "\n".join(parts)

    def count(self, table: str, where: str = "1 = 1", params=()) -> int:
        with self.srv.database().transaction() as tx:
            return tx.fetchone("SELECT count(*) AS n FROM " + table + " WHERE " + where, params)["n"]


async def asgi_call(app, method: str, path: str, headers=None, body: bytes = b"", query: bytes = b""):
    """One in-process ASGI request (no server, no lifespan): ``(status, {lower-case header: value}, body)``."""
    import asyncio

    sent = []
    delivered = False
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    if not any(k == b"host" for k, _ in raw):
        raw.append((b"host", b"testserver"))
    if body:
        raw.append((b"content-length", str(len(body)).encode()))

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": query, "headers": raw, "client": ("127.0.0.1", 1),
             "server": ("testserver", 80), "scheme": "http", "root_path": ""}
    await asyncio.wait_for(app(scope, receive, send), timeout=30)
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, data
