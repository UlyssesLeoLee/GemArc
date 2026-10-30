"""Nothing secret reaches logs or stderr, an injected exception with sentinel text stays out of every response, and the
only log statements in the flow modules are event ids plus exception class names."""
from __future__ import annotations

import ast
import io
import logging
import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_http_transport import sdk_available  # noqa: E402

from .fake_idp import CLIENT_SECRET, Browser, ServerCase, sign_in  # noqa: E402
from .test_mcpauth import make_pat, post  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "gemarc_mcp" / "accounts"
EMAIL = "sentinel.user@gmail.com"
SUB = "sentinel-sub-99887766"
IP = "203.0.113.55"
PAT_RE = re.compile(r"gat_[a-z2-7]{8}_[A-Za-z0-9_-]{49}")


class Capture:
    """All logging records (every logger, every level) and everything written to stderr while active."""

    def __init__(self):
        self.records = io.StringIO()
        self.stderr = io.StringIO()

    def __enter__(self):
        self.handler = logging.StreamHandler(self.records)
        self.handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        self.old_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self.handler)
        self.old_stderr = sys.stderr
        sys.stderr = self.stderr
        return self

    def __exit__(self, *exc):
        sys.stderr = self.old_stderr
        root = logging.getLogger()
        root.removeHandler(self.handler)
        root.setLevel(self.old_level)

    @property
    def text(self) -> str:
        return self.records.getvalue() + "\n" + self.stderr.getvalue()


@unittest.skipUnless(sdk_available(), "MCP SDK is not installed")
class NoSecretsInLogs(ServerCase):
    server_kwargs = {"env": {"VERCEL": "1"}}

    def test_whole_flow_and_failures_leave_no_trace_in_logs_or_stderr(self):
        hdr = {"x-vercel-forwarded-for": IP}
        with Capture() as cap:
            browser = Browser(self.srv.base)
            self.idp.identity = {"sub": SUB, "email": EMAIL}
            start = browser.nav("/auth/google/start", headers=hdr)
            authorize = Browser(self.idp.base).get(start.location, cookies=False)
            callback = browser.nav(authorize.location, headers=hdr)
            self.assertEqual(callback.status, 303)
            state = dict(__import__("urllib.parse").parse.parse_qsl(__import__("urllib.parse").parse.urlsplit(start.location).query))
            sid = browser.cookies["gemarc_sid"]
            page = browser.get("/account").text
            created = browser.post("/account/tokens", {"csrf": browser.csrf(page), "name": "p"})
            token = PAT_RE.search(created.text).group(0)
            self.assertEqual(post(self, token, headers=hdr)[0], 200)
            self.assertEqual(post(self, token[:-2] + "zz", headers=hdr)[0], 401)
            # failures of every kind
            browser.nav(authorize.location, headers=hdr)  # replayed state
            bad = Browser(self.srv.base)
            bad.nav("/auth/google/start", headers=hdr)
            bad.get("/auth/google/callback?code=abc&state=" + "A" * 43, headers=hdr)
            self.idp.faults["token_status"] = 500
            sign_in(Browser(self.srv.base), self.idp, {"sub": "other-sub", "email": "other.person@gmail.com"})
            browser.post("/account/tokens", {"csrf": "wrong", "name": "p"}, origin="https://evil.example")
            export = browser.post("/account/export", {"csrf": browser.csrf(page)})
            self.assertEqual(export.status, 200)
            browser.post("/account/delete", {"csrf": browser.csrf(page), "confirm": "DELETE"})
        text = cap.text
        secrets_in_play = [EMAIL, EMAIL.split("@")[0], SUB, IP, token, token[13:56], sid, state["state"], state["nonce"],
                           CLIENT_SECRET, "other.person", "other-sub", self.idp.token_calls[0]["code"],
                           self.idp.token_calls[0]["code_verifier"], browser.cookies.get("gemarc_oauth", "none-cookie-xx")]
        for secret in secrets_in_play:
            self.assertNotIn(secret, text)

    def test_an_injected_exception_with_sentinel_text_is_not_logged_or_returned(self):
        sentinel = "SENTINEL alice@gmail.com gat_abcdefgh_secretsecretsecret"
        self.logged_in_page({"sub": SUB, "email": EMAIL})
        rt = self.srv.runtime()
        original = rt.tokens.list

        def explode(*args, **kwargs):
            raise RuntimeError(sentinel)

        rt.tokens.list = explode
        self.addCleanup(setattr, rt.tokens, "list", original)
        with Capture() as cap:
            resp = self.browser.get("/account")
        self.assertEqual(resp.status, 500)
        self.assertIn("GEMARC_ACCT_001", resp.text)
        self.assertNotIn("SENTINEL", resp.text)
        self.assertNotIn("RuntimeError", resp.text)
        self.assertNotIn("SENTINEL", cap.text)
        self.assertNotIn("gat_abcdefgh", cap.text)
        self.assertRegex(cap.text, r"accounts_event id=[0-9a-f]{8} class=RuntimeError")
        self.assertNotIn("Traceback", cap.text)

    def test_an_exception_inside_pat_verification_is_a_generic_503(self):
        token, _ = make_pat(self)
        rt = self.srv.runtime()
        original = rt.tokens.verify

        def explode(*args, **kwargs):
            raise ValueError("SENTINEL driver detail: key (email)=(alice@gmail.com)")

        rt.tokens.verify = explode
        self.addCleanup(setattr, rt.tokens, "verify", original)
        self.clock.advance(31)
        with Capture() as cap:
            status, _h, body = post(self, token)
        self.assertEqual(status, 503)
        self.assertNotIn(b"SENTINEL", body)
        self.assertNotIn("SENTINEL", cap.text)
        self.assertNotIn("alice@gmail.com", cap.text)
        self.assertRegex(cap.text, r"pat_event id=[0-9a-f]{8} class=ValueError")


class LogStatementsAreEventIdsOnly(unittest.TestCase):
    FILES = ("routes.py", "mcpauth.py", "oidc.py", "security.py", "pages.py")

    def test_log_calls_carry_only_an_event_id_and_a_class_name(self):
        allowed = {"accounts_event id=%s class=%s", "pat_event id=%s class=%s"}
        for name in self.FILES:
            tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id in ("log", "logger", "logging")
                        and node.func.attr != "getLogger"):
                    first = node.args[0]
                    self.assertIsInstance(first, ast.Constant, f"{name}:{node.lineno} log call must use a constant format")
                    self.assertIn(first.value, allowed, f"{name}:{node.lineno}")
                    rendered = [ast.unparse(a) for a in node.args[1:]]
                    self.assertEqual(len(rendered), 2)
                    self.assertTrue(rendered[1].endswith("__name__"), f"{name}:{node.lineno}")

    def test_api_index_logs_only_fixed_messages(self):
        tree = ast.parse((Path(__file__).resolve().parents[2] / "api" / "index.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"
                    and "ACCT" in ast.unparse(node)):
                self.assertEqual(len(node.args), 1)
                self.assertIsInstance(node.args[0], ast.Constant)

    def test_no_print_and_no_traceback_in_the_flow_modules(self):
        for name in self.FILES:
            text = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^\s*print\(")
            self.assertNotIn("traceback", text)
            self.assertNotIn("exc_info", text)
            self.assertNotIn("log.exception", text)


if __name__ == "__main__":
    unittest.main()
