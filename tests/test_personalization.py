"""DD-010: the tool list and the tool descriptions follow the account that calls.

The accounts package authenticates a request and knows who the caller is; the MCP layer lives
in the core package and must not import the accounts package. The seam between them is
``gemarc_mcp.userctx``: the HTTP layer fills it, the tool layer reads it.

What is bound here:

* without an identity in the request the answer is exactly the one of the server (invariant I1);
* with an identity the list is the list of that account, and the descriptions are in its language;
* a tool outside the list of the account is refused;
* the default render form of an account is the default of ``gemarc_render``, and an explicit
  value of the caller still wins;
* the context is per request: it never leaks into the next one.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gemarc_mcp import i18n, server, stateless, userctx  # noqa: E402

DEMO_MODEL = json.loads((REPO_ROOT / "examples" / "demo-site" / "data" / "architecture.json")
                        .read_text(encoding="utf-8"))


def call(app, name, arguments):
    """One tool call through the server, as the protocol does it."""
    return asyncio.run(app.call_tool(name, arguments))


def payload_of(result) -> dict:
    text = "".join(getattr(item, "text", "") or "" for item in (result.content or []))
    return json.loads(text)


class TheContext(unittest.TestCase):
    """The carrier itself: small, per request and free of secrets."""

    def test_an_empty_list_keeps_every_tool(self):
        ctx = userctx.UserContext(user_id="u1")
        self.assertTrue(ctx.allows("gemarc_brief"))
        self.assertTrue(ctx.allows("anything"))

    def test_a_list_only_removes(self):
        ctx = userctx.UserContext(user_id="u1", tools=frozenset({"gemarc_brief"}))
        self.assertTrue(ctx.allows("gemarc_brief"))
        self.assertFalse(ctx.allows("gemarc_validate"))

    def test_the_render_form_falls_back_to_the_server(self):
        self.assertEqual(userctx.UserContext(user_id="u1").render_form(""), "static")
        self.assertEqual(userctx.UserContext(user_id="u1", render_profile="vite").render_form(""), "vite")
        # an explicit value of the caller wins over the account
        self.assertEqual(userctx.UserContext(user_id="u1", render_profile="vite").render_form("mvp"), "mvp")

    def test_the_context_is_empty_outside_a_request(self):
        self.assertIsNone(userctx.current())

    def test_the_context_does_not_leak_into_the_next_request(self):
        token = userctx.activate(userctx.UserContext(user_id="u1"))
        try:
            self.assertEqual(userctx.current().user_id, "u1")
        finally:
            userctx.reset(token)
        self.assertIsNone(userctx.current())


class TheToolListFollowsTheAccount(unittest.TestCase):
    """``tools/list`` is the account's own list, in the account's own language."""

    def setUp(self):
        self.app = server.create_server("stateless")

    def list_tools(self, ctx=None):
        token = userctx.activate(ctx) if ctx is not None else None
        try:
            return asyncio.run(self.app.list_tools())
        finally:
            if token is not None:
                userctx.reset(token)

    def test_without_an_identity_the_list_is_the_one_of_the_server(self):
        tools = self.list_tools()
        self.assertEqual([t.name for t in tools], list(server.REGISTERED_TOOLS))
        for tool in tools:
            meta = tool.model_dump(by_alias=True, exclude_none=True).get("_meta") or {}
            texts = meta.get("gemarc.dev/descriptions")
            self.assertIsNotNone(texts, tool.name)
            # the description of the list is the English of the code
            self.assertEqual(texts["en-US"]["description"].strip(), (tool.description or "").strip(), tool.name)

    def test_the_list_of_an_account_without_a_list_is_the_whole_one(self):
        ctx = userctx.UserContext(user_id="u1", lang="ja-JP")
        self.assertEqual([t.name for t in self.list_tools(ctx)], list(server.REGISTERED_TOOLS))

    def test_the_list_of_an_account_is_narrower(self):
        ctx = userctx.UserContext(user_id="u1", lang="ja-JP", tools=frozenset({"gemarc_brief"}))
        self.assertEqual([t.name for t in self.list_tools(ctx)], ["gemarc_brief"])

    def test_the_description_is_the_language_of_the_account(self):
        ctx = userctx.UserContext(user_id="u1", lang="ja-JP")
        tools = {t.name: t for t in self.list_tools(ctx)}
        wanted = i18n.section("ja-JP", "tools")["gemarc_brief"]
        self.assertEqual(tools["gemarc_brief"].description, wanted["description"])
        self.assertEqual(tools["gemarc_brief"].title, wanted["title"])
        # the three languages stay in the _meta: a client that wants another one still finds it
        meta = tools["gemarc_brief"].model_dump(by_alias=True, exclude_none=True).get("_meta") or {}
        texts = meta.get("gemarc.dev/descriptions")
        self.assertEqual(sorted(texts), sorted(i18n.LANGUAGES))

    def test_an_account_without_a_language_keeps_the_english(self):
        ctx = userctx.UserContext(user_id="u1", lang="en-US")
        tools = {t.name: t for t in self.list_tools(ctx)}
        plain = {t.name: t for t in self.list_tools()}
        # the English of a tool is the English of the code (the table has no English section)
        self.assertEqual(tools["gemarc_brief"].description, plain["gemarc_brief"].description)


class ACallOutsideTheListIsRefused(unittest.TestCase):
    """The list is not decoration: a tool the account dropped cannot be called."""

    def setUp(self):
        self.app = server.create_server("stateless")

    def test_a_tool_outside_the_list_is_refused(self):
        token = userctx.activate(userctx.UserContext(user_id="u1", tools=frozenset({"gemarc_brief"})))
        try:
            result = call(self.app, "gemarc_validate", {"model": DEMO_MODEL})
        finally:
            userctx.reset(token)
        self.assertTrue(result.is_error)
        body = payload_of(result)
        self.assertEqual(body["code"], "GEMARC_ACCT_036")
        self.assertEqual(body["tool"], "gemarc_validate")

    def test_a_tool_of_the_list_still_works(self):
        token = userctx.activate(userctx.UserContext(user_id="u1", tools=frozenset({"gemarc_brief"})))
        try:
            result = call(self.app, "gemarc_brief", {})
        finally:
            userctx.reset(token)
        self.assertFalse(result.is_error)
        self.assertEqual(payload_of(result)["status"], "ok")

    def test_without_a_list_every_tool_still_works(self):
        result = call(self.app, "gemarc_validate", {"model": DEMO_MODEL})
        self.assertFalse(result.is_error)


class TheDefaultRenderForm(unittest.TestCase):
    """``gemarc_render`` takes the render form of the account when the caller names none."""

    def setUp(self):
        self.app = server.create_server("stateless")

    def render(self, ctx=None, arguments=None):
        token = userctx.activate(ctx) if ctx is not None else None
        try:
            return payload_of(call(self.app, "gemarc_render", arguments or {"model": DEMO_MODEL}))
        finally:
            if token is not None:
                userctx.reset(token)

    def test_without_an_account_the_form_is_static(self):
        body = self.render()
        self.assertEqual(body["kit"]["path"], "/kit/static.zip")

    def test_the_account_decides_the_form(self):
        ctx = userctx.UserContext(user_id="u1", render_profile="vite")
        body = self.render(ctx)
        self.assertEqual(body["kit"]["path"], "/kit/vite.zip")

    def test_an_explicit_value_of_the_caller_wins(self):
        ctx = userctx.UserContext(user_id="u1", render_profile="vite")
        body = self.render(ctx, {"model": DEMO_MODEL, "profile": "static"})
        self.assertEqual(body["kit"]["path"], "/kit/static.zip")

    def test_the_parameter_of_the_tool_has_no_fixed_default(self):
        """The empty string is what tells "the caller named nothing" from "the caller asked for static"."""
        tools = asyncio.run(self.app.list_tools())
        schema = next(t for t in tools if t.name == "gemarc_render").model_dump(
            by_alias=True, exclude_none=True)["inputSchema"]
        self.assertEqual(schema["properties"]["profile"]["default"], "")

    def test_the_empty_form_never_reaches_the_server(self):
        """The server only knows three forms; the empty string must be resolved before it gets there."""
        self.assertEqual(userctx.DEFAULT_RENDER_PROFILE, "static")
        self.assertNotIn("", stateless.SITE_PROFILES)


if __name__ == "__main__":
    unittest.main()
