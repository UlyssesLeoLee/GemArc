"""With accounts disabled, ``api/index.py`` behaves byte for byte like the committed version (invariant I1).

The "before" application is the file ``git show HEAD:api/index.py`` executed as a separate module; the "after" is the
working tree. Both are driven in process (no uvicorn, so no ``Date`` header) with the same request sequence and
compared on status, every header and the body. Once the accounts work is committed HEAD equals the working tree and the
comparison is trivially equal; the source scan and the import-surface test below keep holding either way.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ACCOUNT_ENV = ("GEMARC_ACCOUNTS", "GEMARC_REQUIRE_LOGIN", "GEMARC_MCP_AUTH")
ENV_KEYS = ("GEMARC_ACCOUNTS_BASE_URL", "GEMARC_ACCOUNTS_KEYS", "GEMARC_GOOGLE_CLIENT_ID",
            "GEMARC_GOOGLE_CLIENT_SECRET", "GEMARC_ACCOUNTS_DATABASE_URL", "DATABASE_URL", "GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_PUBLIC_URL",
            "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL", *ACCOUNT_ENV)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
MCP_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
TOKEN = "tok-disabled-0123456789abcdef"


def git_head_source() -> str:
    try:
        out = subprocess.run(["git", "show", "HEAD:api/index.py"], cwd=str(REPO), capture_output=True, check=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("git is not available or HEAD has no api/index.py")
    return out.stdout.decode("utf-8")


def load_before_module():
    """Execute HEAD's api/index.py as a module named ``api_index_before`` with the REAL file path."""
    saved = dict(os.environ)
    module = types.ModuleType("api_index_before")
    module.__file__ = str(REPO / "api" / "index.py")
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        exec(compile(git_head_source(), "api_index_before.py", "exec"), module.__dict__)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


async def _call(app, method, path, headers=None, body=b"", query=b""):
    sent = []
    delivered = False
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
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
    return start["status"], sorted((k, v) for k, v in start["headers"]), data


def sequence(token_header):
    """The request sequence; ``token_header`` is a list of extra header dicts to try on /mcp."""
    plain = [
        ("GET", "/healthz", {}, b""), ("HEAD", "/healthz", {}, b""), ("OPTIONS", "/healthz", {}, b""),
        ("POST", "/healthz", {}, b""), ("GET", "/", {}, b""), ("GET", "/console.css", {}, b""),
        ("GET", "/console.js", {}, b""), ("GET", "/.well-known/oauth-protected-resource", {}, b""),
        ("GET", "/.well-known/oauth-authorization-server", {}, b""), ("GET", "/account", {}, b""),
        ("GET", "/account/tokens", {}, b""), ("GET", "/auth/google/start", {}, b""),
        ("GET", "/auth/google/callback", {}, b""), ("POST", "/auth/logout", {}, b""), ("GET", "/account/export", {}, b""),
        ("GET", "/register", {}, b""), ("GET", "/nope", {}, b""), ("GET", "/site", {}, b""),
        ("GET", "/kit/bogus.zip", {}, b""),
        ("GET", "/mcp", {}, b""), ("HEAD", "/mcp", {}, b""), ("DELETE", "/mcp", {}, b""), ("OPTIONS", "/mcp", {}, b""),
        ("PUT", "/mcp", {}, b""),
    ]
    mcp = []
    for extra in token_header:
        for payload in (INIT, LIST):
            mcp.append(("POST", "/mcp", {**MCP_HEADERS, **extra}, json.dumps(payload).encode()))
    mcp.append(("POST", "/mcp?token=" + TOKEN, dict(MCP_HEADERS), json.dumps(INIT).encode()))
    return plain + mcp


async def drive(app, calls):
    out = []
    for method, path, headers, body in calls:
        target, _, query = path.partition('?')
        out.append(await _call(app, method, target, headers, body, query.encode()))
    return out


class Env:
    """Clean process environment for one build; restores everything afterwards."""

    def __init__(self, **extra):
        self.extra = extra

    def __enter__(self):
        self.saved = dict(os.environ)
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self.extra)
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self.saved)


def build_and_drive(build, calls, **env):
    ws = tempfile.mkdtemp(prefix="gemarc-unchanged-")
    try:
        with Env(**env):
            app = build(workspace_root=ws)
            return asyncio.run(drive(app, calls))
    finally:
        shutil.rmtree(ws, ignore_errors=True)


class DisabledUnchanged(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gemarc_mcp import server  # noqa: F401  (imported before the "before" module executes)
        cls.before = load_before_module()
        import api.index as after

        cls.after = after

    def compare(self, build_kwargs, extra_headers, **env):
        calls = sequence(extra_headers)
        before = build_and_drive(lambda **kw: self.before.build_app(**build_kwargs, **kw), calls, **env)
        after = build_and_drive(lambda **kw: self.after.build_app(**build_kwargs, **kw), calls, **env)
        self.assertEqual(len(before), len(after))
        for call, b, a in zip(calls, before, after):
            self.assertEqual(b, a, f"response differs for {call[0]} {call[1]}")
        return before

    def test_open_endpoint_is_identical(self):
        results = self.compare({"token": ""}, [{}])
        statuses = {(c[0], c[1]): r[0] for c, r in zip(sequence([{}]), results)}
        self.assertEqual(statuses[("POST", "/auth/logout")], 404)
        self.assertEqual(statuses[("GET", "/account")], 404)
        self.assertEqual(statuses[("GET", "/mcp")], 405)
        health = json.loads(results[0][2])
        self.assertNotIn("accounts", health)

    def test_bearer_token_endpoint_is_identical(self):
        results = self.compare({"token": TOKEN}, [{}, {"authorization": "Bearer wrong"},
                                                  {"authorization": "Bearer " + TOKEN}])
        statuses = [r[0] for c, r in zip(sequence([{}, {}, {}]), results) if c[1].startswith("/mcp") and c[0] == "POST"]
        self.assertIn(401, statuses)
        self.assertIn(200, statuses)

    def test_environment_token_is_identical(self):
        self.compare({}, [{}, {"authorization": "Bearer " + TOKEN}], GEMARC_MCP_TOKEN=TOKEN)

    def test_full_toolset_is_identical(self):
        self.compare({"toolset": "full"}, [{}])

    def test_safe_off_literals_equal_unset(self):
        baseline = build_and_drive(lambda **kw: self.before.build_app(token="", **kw), sequence([{}]))
        for literal in ("0", "", " ", " 0 "):
            after = build_and_drive(lambda **kw: self.after.build_app(token="", **kw), sequence([{}]),
                                    GEMARC_ACCOUNTS=literal)
            self.assertEqual(baseline, after, f"GEMARC_ACCOUNTS={literal!r}")
        for name in ("GEMARC_REQUIRE_LOGIN", "GEMARC_MCP_AUTH"):
            for literal in ("0", "", " ") if name == "GEMARC_REQUIRE_LOGIN" else ("", " "):
                after = build_and_drive(lambda **kw: self.after.build_app(token="", **kw), sequence([{}]),
                                        **{name: literal})
                self.assertEqual(baseline, after, f"{name}={literal!r}")

    def test_preprovisioned_account_variables_without_the_flag_change_nothing(self):
        """The staging deploy of the runbook: everything is set EXCEPT GEMARC_ACCOUNTS (design D87)."""
        provisioned = {
            "GEMARC_ACCOUNTS_BASE_URL": "https://gem-arc.example",
            "GEMARC_ACCOUNTS_KEYS": "k1:AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA",
            "GEMARC_GOOGLE_CLIENT_ID": "client.id.fake",
            "GEMARC_GOOGLE_CLIENT_SECRET": "client-secret-fake",
            "GEMARC_ACCOUNTS_DATABASE_URL": "postgresql://user:pw@db.example/neondb?sslmode=require",
            "DATABASE_URL": "postgresql://user:pw@db.example/neondb?sslmode=require",
        }
        baseline = build_and_drive(lambda **kw: self.before.build_app(token="", **kw), sequence([{}]))
        after = build_and_drive(lambda **kw: self.after.build_app(token="", **kw), sequence([{}]), **provisioned)
        self.assertEqual(baseline, after)
        results = self.compare({"token": TOKEN}, [{}, {"authorization": "Bearer " + TOKEN}], **provisioned)
        self.assertNotIn(b"accounts", results[0][2])

    def test_explicit_token_wins_over_the_environment(self):
        # build_app(token="") is how the existing tests force an open endpoint even with GEMARC_MCP_TOKEN set
        before = build_and_drive(lambda **kw: self.before.build_app(token="", **kw), sequence([{}]),
                                 GEMARC_MCP_TOKEN=TOKEN)
        after = build_and_drive(lambda **kw: self.after.build_app(token="", **kw), sequence([{}]),
                                GEMARC_MCP_TOKEN=TOKEN)
        self.assertEqual(before, after)


class SourceAndImports(unittest.TestCase):
    def test_index_never_reads_the_three_switches_itself(self):
        tree = ast.parse((REPO / "api" / "index.py").read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and ast.get_docstring(n, clean=False) is not None}
        hits = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
                and any(name in n.value for name in ACCOUNT_ENV)]
        self.assertEqual(hits, [])

    def test_disabled_import_surface_is_flags_only(self):
        env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        code = (
            "import sys\n"
            f"sys.path[:0] = [{str(REPO)!r}, {str(REPO / 'src')!r}]\n"
            "import api.index as m\n"
            "app = m.build_app(token='')\n"
            "bad = sorted(n for n in sys.modules if n.startswith('gemarc_mcp.accounts') or n.split('.')[0] in ('psycopg', 'psycopg_binary'))\n"
            "print(repr(bad))\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120,
                             cwd=tempfile.gettempdir())
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(out.stdout.strip(), repr(["gemarc_mcp.accounts", "gemarc_mcp.accounts.flags"]))


if __name__ == "__main__":
    unittest.main()
