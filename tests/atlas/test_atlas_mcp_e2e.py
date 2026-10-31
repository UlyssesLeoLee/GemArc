"""End-to-end MCP sessions of the atlas tool sets, spoken through the OFFICIAL client library.

* ``StdioAuthoringSession``: a real child process (``python -m gemarc_mcp``, tool set ``atlas``, an explicit filtered environment
  like Claude Code, Codex and Hermes give it), a full authoring session on the synthetic lending desk: init, three puts including
  one red draft repaired by a patch, status after each, check, index, the entry prompt, refusals;
* ``StdioReadOnlySession``: the same server without ``GEMARC_ALLOW_WRITE``: writes are refused, check / status / index / brief work;
* ``RemoteAsgiTests``: the ``atlas_remote`` tool set behind ``api.index.build_app(toolset="atlas_remote")`` (real HTTP under uvicorn,
  the official streamable-HTTP client): two tools, advisory lint with ``known``, size limit, no filesystem path in any reply;
* ``InProcessTests``: the server object directly (the hard 20 KiB safety net of the reply wrapper).

Every reply must be one compact JSON text (no structuredContent), at most 20 KiB. All data is synthetic (``fixtures/lending_desk``).

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_atlas_mcp_e2e*.py"
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

HARD_CAP = 20 * 1024
DESK = {name: (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8") for name in ("00-space", "10-structure", "20-scenarios")}
POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"
REPAIR = ("## nodes\n@set room=rm.desk kind=Module\nsvc.loan | Loan service | Creates a loan and takes returns. | ev=EVD-1,EVD-2\n"
          "svc.stock | Stock service | Knows which copy is on the shelf. | ev=EVD-2\n@set kind=Storage\n"
          "tbl.loan | Loan table | One row per open loan. | ev=EVD-1\n@set room=rm.vis kind=Actor\n"
          "usr.member | Member | Borrower using the web page. | ev=EVD-3 verif=Inferred\n")
FILESYSTEM_SHAPES = (re.compile(r"[A-Za-z]:[\\/][A-Za-z0-9_.$-]"), re.compile(r"^\\\\[A-Za-z0-9_.$-]+\\"),
                     re.compile(r"(^|\s)/(tmp|var|home|usr|opt|Users|private|workspace)/"))


def _sdk_available() -> bool:
    try:
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: F401
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _http_available() -> bool:
    try:
        import httpx2  # noqa: F401
        import uvicorn  # noqa: F401
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def leaked_paths(value) -> list:
    """String values of a JSON document that look like server filesystem locations."""
    found = []
    if isinstance(value, dict):
        for item in value.values():
            found += leaked_paths(item)
    elif isinstance(value, list):
        for item in value:
            found += leaked_paths(item)
    elif isinstance(value, str) and any(shape.search(value) for shape in FILESYSTEM_SHAPES):
        found.append(value)
    return found


class Caller:
    """Calls tools through an MCP session and checks the shape that every reply must have."""

    def __init__(self, session, case: unittest.TestCase):
        self.session, self.case = session, case
        self.sizes: list = []

    async def call(self, name: str, **arguments):
        result = await self.session.call_tool(name, arguments)
        case = self.case
        case.assertEqual(len(result.content), 1, name)
        text = result.content[0].text
        payload = json.loads(text)
        case.assertIsNone(result.structured_content, f"{name}: structuredContent would hide the text in Codex")
        case.assertEqual(text, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), f"{name}: reply is not compact JSON")
        size = len(text.encode("utf-8"))
        case.assertLessEqual(size, HARD_CAP, f"{name}: {size} B")
        self.sizes.append((name, size))
        failed = payload.get("status") in ("invalid", "error")
        case.assertIs(bool(result.is_error), failed, f"{name}: isError must equal status in invalid|error: {payload.get('status')}")
        return result, payload


@contextlib.contextmanager
def temp_dir(prefix: str):
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def stdio_session(env_extra: dict, workspace: Path, body):
    """Start ``python -m gemarc_mcp`` the way a client does (filtered env + explicit variables), run ``body(session, init)``."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"GEMARC_TOOLSET": "atlas", "GEMARC_WORKSPACE_ROOT": str(workspace), "PYTHONUTF8": "1",
           "PYTHONPATH": str(REPO / "src"), **env_extra}
    with temp_dir("atlas-e2e-cwd-") as cwd, tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errlog:
        params = StdioServerParameters(command=sys.executable, args=["-m", "gemarc_mcp"], env=env, cwd=str(cwd))
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=120) as session:
                init = await session.initialize()
                await body(session, init)
        errlog.seek(0)
        return errlog.read()


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioAuthoringSession(unittest.TestCase):
    def test_full_authoring_session_over_stdio(self):
        from gemarc_mcp import atlas_tools

        with temp_dir("atlas-e2e-ws-") as workspace:
            stderr = asyncio.run(stdio_session({"GEMARC_ALLOW_WRITE": "1"}, workspace, lambda s, i: self.session(s, i, workspace)))
        self.assertNotIn("Traceback", stderr, "the server must not crash during a normal session")
        self.assertTrue(atlas_tools.REGISTRY)

    async def session(self, session, init, workspace: Path):
        from gemarc_mcp import atlas_tools

        caller = Caller(session, self)
        call = caller.call
        # ---- handshake: instructions, tools, prompt, no resources ------------------------------------
        self.assertEqual(init.server_info.name, "GemArc MCP")
        self.assertIn("gemarc_atlas_brief", init.instructions)
        self.assertLessEqual(len(init.instructions), 4096)
        tools = (await session.list_tools()).tools
        self.assertEqual([t.name for t in tools], list(atlas_tools.tool_names("atlas")))
        listed = len(json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools], ensure_ascii=False, separators=(",", ":")).encode())
        self.assertLessEqual(listed, 10 * 1024)
        self.assertEqual([p.name for p in (await session.list_prompts()).prompts], ["gemarc-atlas-create"])
        self.assertEqual((await session.list_resources()).resources, [])
        for mode in ("asis", "tobe"):
            prompt = await session.get_prompt("gemarc-atlas-create", {"requirement": "An invented lending desk.", "mode": mode, "project": "lending-desk"})
            text = prompt.messages[0].content.text
            self.assertIn("恰好一次", text)
            self.assertIn("lending-desk", text)

        # ---- the work order ------------------------------------------------------------------------
        _, brief = await call("gemarc_atlas_brief", lang="zh-CN")
        self.assertEqual((brief["status"], brief["lang"], brief["section"]), ("ok", "zh-CN", "all"))
        self.assertLessEqual(brief["bytes"], 9000)
        self.assertIn("工作指令", brief["text"])
        _, section = await call("gemarc_atlas_brief", section="slices")
        self.assertLessEqual(section["bytes"], 3000)
        result, bad = await call("gemarc_atlas_brief", section="nope")
        self.assertTrue(result.is_error)
        self.assertEqual(bad["status"], "invalid")

        # ---- init ------------------------------------------------------------------------------------
        result, missing = await call("gemarc_atlas_status", project="lending-desk")
        self.assertTrue(result.is_error, "status of a project that does not exist is a refusal")
        result, created = await call("gemarc_atlas_init", project="lending-desk", title="Library lending desk", mode="asis", languages=["en-US"])
        self.assertEqual(created["status"], "created", created)
        self.assertTrue(Path(created["path"]).is_dir())
        self.assertEqual(Path(created["path"]).resolve(), (workspace / "lending-desk" / "atlas").resolve())
        result, again = await call("gemarc_atlas_init", project="lending-desk")
        self.assertTrue(result.is_error, "init never overwrites")
        _, first = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual((first["phase"], first["done"]), ("collect", False))

        # ---- put x3 with one red draft and a patch repair ---------------------------------------------
        _, space = await call("gemarc_atlas_put", project="lending-desk", slice="00-space", text=DESK["00-space"])
        self.assertEqual(space["status"], "committed", space)
        result, red = await call("gemarc_atlas_put", project="lending-desk", slice="10-structure",
                                 text=DESK["10-structure"].replace("kind=Module", "kind=Modul"))
        self.assertTrue(result.is_error, "a red draft is flagged isError, the payload is kept")
        self.assertEqual((red["status"], red["red"]), ("invalid", True))
        self.assertTrue(red["diagnostics"] and red["next"])
        _, state = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual(state["phase"], "repair")
        _, check_red = await call("gemarc_atlas_check", project="lending-desk", slice="10-structure")
        self.assertEqual((check_red["clean"], check_red["source"]), (False, "red draft"))
        self.assertEqual(check_red["red_drafts"], ["10-structure.atl"])
        _, repaired = await call("gemarc_atlas_put", project="lending-desk", slice="10-structure", text=REPAIR, mode="patch")
        self.assertEqual(repaired["status"], "committed", repaired)
        self.assertTrue(repaired.get("red_draft_cleared"))
        _, scenarios = await call("gemarc_atlas_put", project="lending-desk", slice="20-scenarios", text=DESK["20-scenarios"])
        self.assertEqual(scenarios["status"], "committed", scenarios)

        # ---- status / more slices / idempotence / conflicts -------------------------------------------
        _, author = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual(author["phase"], "author")
        self.assertLessEqual(len(author["next"]), 10)
        _, policy = await call("gemarc_atlas_put", project="lending-desk", slice="30-policy", text=POLICY)
        self.assertEqual(policy["status"], "committed", policy)
        _, review = await call("gemarc_atlas_status", project="lending-desk")
        # later lanes register gates (oracle, build, browser): then the phase may be author or build, with a gate or dims blocker
        # an as-is project with no evidence root configured stops at needs-user (the operator sets GEMARC_ATLAS_ROOTS, or the user lowers verif=)
        self.assertIn(review["phase"], ("review", "build", "author", "needs-user"), review)
        self.assertFalse(any("GEMARC_ALLOW_WRITE" in x for x in review["needsUser"]))
        if review["phase"] != "review":
            self.assertTrue([b for b in review["blockers"] if b.startswith(("gate-", "dims:"))], review)
        self.assertFalse(review["done"], "done needs the review record (the review tool is a later lane)")
        self.assertIn("review:missing", review["blockers"])
        _, same = await call("gemarc_atlas_put", project="lending-desk", slice="30-policy", text=POLICY)
        self.assertEqual((same["unchanged"], same["sha8"]), (True, policy["sha8"]))
        _, edited = await call("gemarc_atlas_put", project="lending-desk", slice="30-policy", base=policy["sha8"],
                               text=POLICY.replace("two seconds", "three seconds"))
        self.assertEqual((edited["status"], edited["previous"]), ("committed", policy["sha8"]))
        result, stale = await call("gemarc_atlas_put", project="lending-desk", slice="30-policy", base=policy["sha8"], text=POLICY)
        self.assertTrue(result.is_error)
        self.assertEqual(stale["status"], "invalid")
        result, badname = await call("gemarc_atlas_put", project="lending-desk", slice="Bad Name", text=POLICY)
        self.assertTrue(result.is_error)

        # ---- check / index ---------------------------------------------------------------------------
        _, full = await call("gemarc_atlas_check", project="lending-desk", mode="full")
        self.assertTrue(full["clean"], full["diagnostics"])
        self.assertEqual((full["authority"], full["scope"]), ("authoritative", "project"))
        _, js = await call("gemarc_atlas_check", project="lending-desk", mode="full", format="json")
        # debt-b4: wave 2 is part of the default full check, so the desk shows its INFO lines (events without a ruleId, a sealed expectation the user has
        # not confirmed) and nothing above INFO; it used to show none
        self.assertEqual([d for d in js["diagnostics"]["diagnostics"] if d["severity"] != "INFO"], [])
        self.assertEqual({d["rule"] for d in js["diagnostics"]["diagnostics"]}, {"CS-008", "OR-005"})
        _, inline = await call("gemarc_atlas_check", text=DESK["20-scenarios"])
        self.assertEqual(inline["authority"], "advisory")
        result, both = await call("gemarc_atlas_check", project="lending-desk", text="x")
        self.assertTrue(result.is_error, "exactly one of project and text")
        _, cases = await call("gemarc_atlas_index", project="lending-desk", scope="case:CS-BORROW-OK")
        self.assertIn("svc.loan=Completed", cases["text"])
        _, room = await call("gemarc_atlas_index", project="lending-desk", scope="room:rm.vis", detail="expanded")
        self.assertIn("usr.member", room["text"])
        _, edges = await call("gemarc_atlas_index", project="lending-desk", with_edges=True, ids=["svc.loan"])
        self.assertEqual(edges["status"], "ok")
        result, badscope = await call("gemarc_atlas_index", project="lending-desk", scope="bogus:1")
        self.assertTrue(result.is_error)

        # ---- files: the slices are what the tools said, and nothing escaped the project -------------------
        slices = workspace / "lending-desk" / "atlas" / "slices"
        self.assertEqual(sorted(p.name for p in slices.iterdir()), ["00-space.atl", "10-structure.atl", "20-scenarios.atl", "30-policy.atl"])
        self.assertIn("three seconds", (slices / "30-policy.atl").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in workspace.iterdir()), ["lending-desk"])

        # ---- tools of other tool sets do not exist -----------------------------------------------------
        for name in ("gemarc_build", "gemarc_validate", "gemarc_atlas_no_such_tool"):
            try:
                result = await session.call_tool(name, {})
                refused = bool(result.is_error)
            except Exception:  # noqa: BLE001 - a protocol error is an acceptable refusal
                refused = True
            self.assertTrue(refused, name)
        puts = [size for name, size in caller.sizes if name == "gemarc_atlas_put"]
        print(f"\n[stdio session] {len(caller.sizes)} calls, largest reply {max(s for _, s in caller.sizes)} B, put replies {puts}")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioReadOnlySession(unittest.TestCase):
    def test_writes_are_refused_and_reads_work_without_the_write_flag(self):
        with temp_dir("atlas-e2e-ws-") as workspace:
            asyncio.run(stdio_session({}, workspace, lambda s, i: self.session(s, i, workspace)))

    async def session(self, session, init, workspace: Path):
        call = Caller(session, self).call
        result, refused = await call("gemarc_atlas_init", project="nope")
        self.assertTrue(result.is_error)
        self.assertEqual(refused["code"], "GEMARC_AUTH_002")
        result, refused = await call("gemarc_atlas_put", project="nope", slice="00-space", text=DESK["00-space"])
        self.assertTrue(result.is_error)
        self.assertEqual(refused["code"], "GEMARC_AUTH_002")
        self.assertEqual(list(workspace.iterdir()), [], "a refused write created nothing")
        result, ok = await call("gemarc_atlas_check", text=DESK["00-space"])
        self.assertFalse(result.is_error, "check works without GEMARC_ALLOW_WRITE")
        self.assertEqual(ok["status"], "ok")
        result, brief = await call("gemarc_atlas_brief", section="format")
        self.assertFalse(result.is_error)
        result, missing = await call("gemarc_atlas_status", project="nope")
        self.assertTrue(result.is_error)


# ---------------------------------------------------------------------------------------------------------------------
# Batch 3: the whole product flow through the OFFICIAL client over real stdio (acceptance (b), (c), (d), (e) of task tools-b3)
# ---------------------------------------------------------------------------------------------------------------------

#: The reply cap of every tool of design 4.1 (bytes; ``put`` 4.5 KB, ``status next`` 1.2 KB, ``doc`` outline 1 KiB, a chapter page 20 KiB).
REPLY_CAPS = {"gemarc_atlas_brief": 9000, "gemarc_atlas_init": 1024, "gemarc_atlas_put": 4500, "gemarc_atlas_check": 4500,
              "gemarc_atlas_status": 4500, "gemarc_atlas_index": 4500, "gemarc_atlas_simulate": 3000, "gemarc_atlas_doc": 20 * 1024,
              "gemarc_atlas_outline": 4500, "gemarc_atlas_evidence": 3000, "gemarc_atlas_build": 2000, "gemarc_atlas_verify": 3000,
              "gemarc_atlas_review": 4500}
SOURCE_FILES = {"src/loan.py": "class Loans:\n    def create_loan(self, member):\n        return 1\n",
                "src/stock.py": "def find_copy(title):\n    return None\n",
                "docs/lending.md": "# Lending\nThe borrower opens the page to borrow.\n"}
BROWSER_ENV = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA", "TMP", "TEMP", "SystemRoot", "GEMARC_ATLAS_BROWSER")


def tree_hash(root: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def full_env(roots: Path, write: bool = True, no_browser: bool = False) -> dict:
    """What a client config gives the server: the explicit variables plus what the browser gate needs to find Edge or Chrome
    (``no_browser`` pins GEMARC_ATLAS_BROWSER to a file that does not exist: the gate then has no browser, a loud SKIP)."""
    env = {key: os.environ[key] for key in BROWSER_ENV if key in os.environ}
    if no_browser:
        env["GEMARC_ATLAS_BROWSER"] = str(roots / "no-such-browser.exe")
    env["GEMARC_ATLAS_ROOTS"] = f"main={roots}"
    if write:
        env["GEMARC_ALLOW_WRITE"] = "1"
    return env


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioFullFlow(unittest.TestCase):
    """init, puts, status until the structure is done, simulate (count), doc, evidence, build, verify (skip-aware), review report and
    record, status says done: on a synthetic requirement, over two server processes (the first one is killed mid-run)."""

    no_browser = False

    def test_the_flow_from_one_sentence_to_done_with_a_restart_in_the_middle(self):
        self.run_flow()

    def run_flow(self):
        from gemarc_mcp import atlas_tools

        with temp_dir("atlas-e2e-ws-") as workspace, temp_dir("atlas-e2e-src-") as roots:
            for rel, text in SOURCE_FILES.items():
                (roots / rel).parent.mkdir(parents=True, exist_ok=True)
                (roots / rel).write_text(text, encoding="utf-8")
            self.sizes: list = []
            first_err = asyncio.run(stdio_session(full_env(roots, no_browser=self.no_browser), workspace, lambda s, i: self.first_half(s, i, workspace, roots)))
            # the first server process is gone (the client closed the pipe and ended it): every fact below comes from the directory
            second_err = asyncio.run(stdio_session(full_env(roots, no_browser=self.no_browser), workspace, lambda s, i: self.second_half(s, i, workspace, roots)))
        for err in (first_err, second_err):
            self.assertNotIn("Traceback", err)
        for name, size in self.sizes:
            self.assertLessEqual(size, REPLY_CAPS[name], f"{name}: {size} B")
        by_tool: dict = {}
        for name, size in self.sizes:
            by_tool[name] = max(by_tool.get(name, 0), size)
        print(f"\n[full flow over stdio] {len(self.sizes)} calls; largest reply per tool: " + ", ".join(f"{k.replace('gemarc_atlas_', '')} {v}" for k, v in sorted(by_tool.items())))
        self.assertEqual(sorted(by_tool), sorted(set(by_tool) & set(REPLY_CAPS)))
        self.assertTrue(atlas_tools.REGISTRY)

    async def next_lines(self, caller):
        """``status view=next`` with the design cap of 1.2 KB checked on the reply that was really sent."""
        _, state = await caller.call("gemarc_atlas_status", project="desk")
        self.assertLessEqual(caller.sizes[-1][1], 1200, "status next is at most 1.2 KB")
        return None, state

    async def first_half(self, session, init, workspace: Path, roots: Path):
        from gemarc_mcp import atlas_tools

        caller = Caller(session, self)
        call = caller.call
        tools = (await session.list_tools()).tools
        names = [t.name for t in tools]
        self.assertEqual(names, list(atlas_tools.tool_names("atlas")))
        self.assertEqual(len(names), 13, "the 12 tools of design 4.1 plus the review tool of addendum A1")
        listed = len(json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools], ensure_ascii=False, separators=(",", ":")).encode())
        self.assertLessEqual(listed, 10 * 1024, f"tools/list is {listed} B")
        for tool in tools:
            self.assertLessEqual(len((tool.description or "").encode("utf-8")), 800, tool.name)
        prompt = (await session.get_prompt("gemarc-atlas-create", {"requirement": "An invented lending desk.", "mode": "asis", "project": "desk"})).messages[0].content.text
        for tool in ("gemarc_atlas_simulate", "gemarc_atlas_evidence", "gemarc_atlas_build", "gemarc_atlas_verify", "gemarc_atlas_doc", "oracle-sheet.md", "waive-browser.txt"):
            self.assertIn(tool, prompt, tool)
        _, created = await call("gemarc_atlas_init", project="desk", title="Library lending desk", mode="asis", languages=["en-US"], repos=["main"])
        self.assertEqual(created["status"], "created", created)
        for name in ("00-space", "10-structure", "20-scenarios", "30-policy"):
            text = DESK[name] if name in DESK else POLICY
            _, put = await call("gemarc_atlas_put", project="desk", slice=name, text=text)
            self.assertEqual(put["status"], "committed", put)
        # status next until the structure is done: the next phase is the gates, never done
        _, state = await self.next_lines(caller)
        self.assertEqual((state["done"], state["phase"]), (False, "build"), state)
        self.assertTrue(state["next"][0].startswith("oracle:") and "gemarc_atlas_simulate" in state["next"][0], state["next"])
        # reads never write: the tree hash before and after every read tool
        before = tree_hash(workspace)
        for tool, args in (("gemarc_atlas_status", {"project": "desk"}), ("gemarc_atlas_status", {"project": "desk", "view": "dims"}),
                           ("gemarc_atlas_check", {"project": "desk", "mode": "full"}), ("gemarc_atlas_index", {"project": "desk"}),
                           ("gemarc_atlas_doc", {"project": "desk"}), ("gemarc_atlas_doc", {"project": "desk", "chapter": "2"}),
                           ("gemarc_atlas_outline", {"root": "main", "path": "src/loan.py"}),
                           ("gemarc_atlas_review", {"project": "desk", "action": "report"}), ("gemarc_atlas_brief", {})):
            _, reply = await call(tool, **args)
            self.assertNotEqual(reply["status"], "error", (tool, reply))
        self.assertEqual(tree_hash(workspace), before, "no read tool may change a byte of the workspace")
        _, outline = await call("gemarc_atlas_outline", root="main", path="src/loan.py")
        self.assertIn('"def create_loan"', outline["text"])
        # doc: outline within 1 KiB, a chapter page whole
        _, doc = await call("gemarc_atlas_doc", project="desk", unit="req:RQ-1", level="REQ")
        self.assertEqual((doc["status"], doc["markers"]), ("ok", 0))
        self.assertLessEqual(len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()), 1024)
        # review before the gates: refused, the pass is not burnt
        result, early = await call("gemarc_atlas_review", project="desk", action="record")
        self.assertTrue(result.is_error)
        self.assertEqual(early["code"], "GEMARC_ATLAS_REVIEW_003")
        # simulate (count) after sealing: the verdict is blind
        _, sim = await call("gemarc_atlas_simulate", project="desk")
        self.assertTrue(sim["clean"], sim)
        self.assertEqual(sim["detail"], "count")
        self.assertEqual(sim["summary"]["baselines"]["pass"], 1)
        text = json.dumps(sim)
        for leaked in ("svc.stock", "Completed", "trace"):
            self.assertNotIn(leaked, text, "count-only: no node id, no state, no trace")
        _, state = await self.next_lines(caller)
        self.assertEqual(state["phase"], "build")
        self.assertTrue(state["next"][0].startswith("evidence:"), state["next"])
        self.sizes += caller.sizes
        # ---- the server is killed here (the context manager of the caller ends the process) -------------

    async def second_half(self, session, init, workspace: Path, roots: Path):
        caller = Caller(session, self)
        call = caller.call
        # state is restored from the directory: the new process says where the old one stopped
        _, state = await self.next_lines(caller)
        self.assertEqual((state["done"], state["phase"]), (False, "build"), state)
        self.assertTrue(state["next"][0].startswith("evidence:"), f"the restart lost the verdicts: {state['next']}")
        _, evidence = await call("gemarc_atlas_evidence", project="desk")
        self.assertEqual((evidence["status"], evidence["pass"], evidence["fail"], evidence["skip"]), ("ok", 3, 0, 0), evidence)
        self.assertEqual(evidence["claims"], {"implemented": 5, "unverified": 0})
        _, state = await self.next_lines(caller)
        self.assertTrue(state["next"][0].startswith("build:"), state["next"])
        _, built = await call("gemarc_atlas_build", project="desk")
        self.assertEqual(built["status"], "ok", built)
        self.assertEqual(built["sheet"], "atlas/dist/oracle-sheet.md")
        dist = workspace / "desk" / "atlas" / "dist"
        for rel in ("index.html", "build-info.json", "oracle-sheet.md", "docs/en-US/BD.md"):
            self.assertTrue((dist / rel).is_file(), rel)
        self.assertFalse((workspace / "desk" / "atlas" / "oracle-confirm.txt").exists(), "the MCP never writes the user's confirmation file")
        # verify (skip-aware): a real browser when there is one, else the loud SKIP and the user's waiver
        result, verified = await call("gemarc_atlas_verify", project="desk")
        _, state = await self.next_lines(caller)
        if verified["status"] == "ok":
            self.assertFalse(self.no_browser, "a pinned browser that does not exist must not be replaced by another one")
            self.assertEqual(verified["gate"], "PASS", verified)
            self.assertEqual([w["width"] for w in verified["widths"]], [1024, 1366, 1600])
            self.browser = "PASS"
        else:
            self.assertEqual((result.is_error, verified["code"], verified["gate"]), (True, "GEMARC_ATLAS_BUILD_031", "SKIP"), verified)
            self.assertEqual((state["done"], state["phase"]), (False, "needs-user"), "a missing browser blocks and the user must decide")
            self.assertTrue(any("waive-browser.txt" in x for x in state["needsUser"]), state["needsUser"])
            (workspace / "desk" / "atlas" / "waive-browser.txt").write_text("this machine has no browser; the user opens the page by hand\n", encoding="utf-8")
            _, state = await self.next_lines(caller)
            self.browser = "WAIVED"
        self.assertEqual((state["done"], state["phase"]), (False, "review"), state)
        self.assertEqual(state["blockers"], ["review:missing"])
        # the one review: report is read-only, record once
        before = tree_hash(workspace)
        _, report = await call("gemarc_atlas_review", project="desk", action="report")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(report["sample"]), 8)
        self.assertEqual(tree_hash(workspace), before)
        _, recorded = await call("gemarc_atlas_review", project="desk", action="record", notes="fixed=; open=RV-O1")
        self.assertEqual((recorded["status"], recorded["done"], recorded["phase"]), ("recorded", True, "done"), recorded)
        _, done = await self.next_lines(caller)
        self.assertEqual((done["done"], done["phase"], done["blockers"], done["needsUser"]), (True, "done", [], []), done)
        if self.browser == "PASS":
            self.assertIn("gates green: oracle, evidence, docs, build, browser", done["next"][0])
            self.assertNotIn("waived", done)
        else:       # a waiver is not a pass: the state says so, in a field and in a line, and the done line does not call the browser green
            self.assertIn("gates green: oracle, evidence, docs, build;", done["next"][0])
            self.assertEqual(done["waived"], ["browser"])
            self.assertTrue(any("WAIVED by the user" in x and "UNVERIFIED" in x for x in done["next"]), done["next"])
        # a second review is never demanded, and never accepted
        result, again = await call("gemarc_atlas_review", project="desk", action="record")
        self.assertTrue(result.is_error)
        self.assertEqual(again["code"], "GEMARC_ATLAS_REVIEW_002")
        self.sizes += caller.sizes
        print(f"[full flow over stdio] browser gate: {self.browser}; sheet {built['sheet']}")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioFullFlowWithoutABrowser(StdioFullFlow):
    """The same flow with no browser: verify is a loud SKIP (isError, NOT passed), status stops for the user, only the user's waiver file
    lets it finish, and then exactly one review ends it."""

    no_browser = True

    def test_the_flow_from_one_sentence_to_done_with_a_restart_in_the_middle(self):
        self.run_flow()
        self.assertEqual(self.browser, "WAIVED")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class StdioWriteGateOnTheNewTools(unittest.TestCase):
    """Without GEMARC_ALLOW_WRITE=1 every write tool refuses and every read works, in a second server on a finished project."""

    def test_refusals_and_reads_on_a_project_made_by_another_process(self):
        with temp_dir("atlas-e2e-ws-") as workspace, temp_dir("atlas-e2e-src-") as roots:
            for rel, text in SOURCE_FILES.items():
                (roots / rel).parent.mkdir(parents=True, exist_ok=True)
                (roots / rel).write_text(text, encoding="utf-8")
            asyncio.run(stdio_session(full_env(roots), workspace, lambda s, i: self.make(s, i)))
            before = tree_hash(workspace)
            asyncio.run(stdio_session(full_env(roots, write=False), workspace, lambda s, i: self.refused(s, i)))
            self.assertEqual(tree_hash(workspace), before, "refusals and reads change no byte")

    async def make(self, session, init):
        call = Caller(session, self).call
        await call("gemarc_atlas_init", project="desk", title="Desk", mode="asis", languages=["en-US"], repos=["main"])
        for name in ("00-space", "10-structure", "20-scenarios", "30-policy"):
            await call("gemarc_atlas_put", project="desk", slice=name, text=DESK[name] if name in DESK else POLICY)
        await call("gemarc_atlas_simulate", project="desk")

    async def refused(self, session, init):
        call = Caller(session, self).call
        for tool, args in (("gemarc_atlas_simulate", {"project": "desk"}), ("gemarc_atlas_evidence", {"project": "desk"}),
                           ("gemarc_atlas_build", {"project": "desk"}), ("gemarc_atlas_verify", {"project": "desk"}),
                           ("gemarc_atlas_review", {"project": "desk", "action": "record"}),
                           ("gemarc_atlas_put", {"project": "desk", "slice": "40-x", "text": POLICY})):
            result, reply = await call(tool, **args)
            self.assertTrue(result.is_error, tool)
            self.assertEqual(reply["code"], "GEMARC_AUTH_002", (tool, reply))
        for tool, args in (("gemarc_atlas_status", {"project": "desk"}), ("gemarc_atlas_doc", {"project": "desk"}),
                           ("gemarc_atlas_outline", {"root": "main", "path": "src/loan.py"}), ("gemarc_atlas_index", {"project": "desk"}),
                           ("gemarc_atlas_check", {"project": "desk", "mode": "full"}), ("gemarc_atlas_review", {"project": "desk", "action": "report"})):
            result, reply = await call(tool, **args)
            self.assertFalse(result.is_error, (tool, reply))
        _, state = await call("gemarc_atlas_status", project="desk")
        self.assertEqual(state["phase"], "build", "status keeps working and still names the next gate")


@unittest.skipUnless(_sdk_available() and _http_available(), "MCP SDK, uvicorn or httpx2 is not installed")
class RemoteAsgiTests(unittest.TestCase):
    """``GEMARC_TOOLSET=atlas_remote`` through the deployed ASGI app (api/index.py is not edited: build_app takes the tool set)."""

    ENV_KEYS = ("GEMARC_MCP_TOKEN", "GEMARC_TOOLSET", "GEMARC_ALLOW_WRITE", "GEMARC_WORKSPACE_ROOT", "GEMARC_PUBLIC_URL",
                "VERCEL_PROJECT_PRODUCTION_URL")

    @classmethod
    def setUpClass(cls):
        import uvicorn

        cls.saved = {key: os.environ.get(key) for key in cls.ENV_KEYS}
        for key in cls.ENV_KEYS:
            os.environ.pop(key, None)
        cls.workspace = tempfile.mkdtemp(prefix="atlas-e2e-remote-")
        cls.addClassCleanup(cls.restore)
        from api.index import build_app

        cls.app = build_app(workspace_root=cls.workspace, toolset="atlas_remote")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not cls.server.started:
            time.sleep(0.05)
        if not cls.server.started:
            raise unittest.SkipTest("uvicorn did not start in time")

    @classmethod
    def restore(cls):
        if getattr(cls, "server", None) is not None:
            cls.server.should_exit = True
        if getattr(cls, "thread", None) is not None:
            cls.thread.join(timeout=10)
        for key, value in cls.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls.workspace, ignore_errors=True)

    def drive(self, calls: dict) -> dict:
        async def run():
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            out = {}
            async with httpx2.AsyncClient(timeout=60) as client:
                async with streamable_http_client(self.base + "/mcp", http_client=client) as (read, write):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        out["instructions"] = init.instructions
                        out["tools"] = (await session.list_tools()).tools
                        out["prompts"] = [p.name for p in (await session.list_prompts()).prompts]
                        out["resources"] = (await session.list_resources()).resources
                        for key, (tool, args) in calls.items():
                            reply = await session.call_tool(tool, args)
                            out[key] = (json.loads(reply.content[0].text), bool(reply.is_error), reply.structured_content)
            return out
        return asyncio.run(run())

    def test_the_remote_set_is_two_read_only_tools_and_the_short_prompt(self):
        out = self.drive({})
        self.assertEqual([t.name for t in out["tools"]], ["gemarc_atlas_brief", "gemarc_atlas_check"])
        for tool in out["tools"]:
            self.assertIs(tool.annotations.read_only_hint, True)
            self.assertIs(tool.annotations.open_world_hint, False)
        self.assertEqual(out["prompts"], ["gemarc-atlas-create"])
        self.assertEqual(out["resources"], [])
        self.assertIn("gemarc_atlas_brief", out["instructions"])
        with urllib_open(self.base + "/healthz") as report:
            self.assertEqual(report["toolset"], "atlas_remote")
            self.assertEqual(report["writeTools"], [])
            self.assertEqual(sorted(report["tools"]), ["gemarc_atlas_brief", "gemarc_atlas_check"])
            self.assertEqual(leaked_paths(report), [])

    def test_advisory_lint_with_known_ids_and_limits(self):
        scenarios = DESK["20-scenarios"]
        known = "node svc.loan\nnode svc.stock\nnode tbl.loan\nnode usr.member\nevidence EVD-1\n"
        typo = scenarios.replace("Call | svc.loan | svc.stock | ruleId=R-CHECK", "Call | svc.loan | svc.stok | ruleId=R-CHECK")
        self.assertNotEqual(typo, scenarios)
        calls = {
            "alone": ("gemarc_atlas_check", {"text": scenarios}),
            "known": ("gemarc_atlas_check", {"text": scenarios, "known": known}),
            "typo": ("gemarc_atlas_check", {"text": typo, "known": known}),
            "json": ("gemarc_atlas_check", {"text": scenarios, "format": "json"}),
            "broken": ("gemarc_atlas_check", {"text": "#atlas 1\n## nodes: id | name | sub | opts\nonly-one-cell\n"}),
            "empty": ("gemarc_atlas_check", {"text": ""}),
            "huge": ("gemarc_atlas_check", {"text": "#atlas 1\n" + "x" * (33 * 1024)}),
            "badformat": ("gemarc_atlas_check", {"text": scenarios, "format": "xml"}),
            "brief": ("gemarc_atlas_brief", {"section": "format"}),
        }
        out = self.drive(calls)
        for key in calls:
            payload, is_error, structured = out[key]
            self.assertIsNone(structured, key)
            self.assertIs(is_error, payload.get("status") in ("invalid", "error"), key)
            self.assertEqual(leaked_paths(payload), [], f"{key}: a filesystem path in the reply")
            self.assertNotIn(Path(self.workspace).name, json.dumps(payload), f"{key}: the server's workspace in the reply")
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")), HARD_CAP, key)
        alone, known_reply, typo_reply = out["alone"][0], out["known"][0], out["typo"][0]
        for reply in (alone, known_reply, typo_reply):
            self.assertEqual((reply["status"], reply["authority"], reply["scope"]), ("ok", "advisory", "grammar+intra-slice"))
        self.assertIn("pending", alone, "without known, references to other slices are pending")
        self.assertNotIn("pending", known_reply, "known ids resolve the references")
        self.assertEqual(known_reply["known"], 5)
        self.assertGreaterEqual(typo_reply["counts"]["warn"], 1, "a typo next to a known id is a WARN")
        self.assertIsInstance(out["json"][0]["diagnostics"]["diagnostics"], list)
        self.assertFalse(out["broken"][0]["clean"], out["broken"][0])
        self.assertEqual(out["empty"][0]["status"], "invalid")
        self.assertEqual(out["huge"][0]["status"], "invalid", "the remote text limit is 32 KB")
        self.assertEqual(out["badformat"][0]["status"], "invalid")
        self.assertEqual(out["brief"][0]["status"], "ok")


@contextlib.contextmanager
def urllib_open(url: str):
    import urllib.request

    with urllib.request.urlopen(url, timeout=20) as response:
        yield json.loads(response.read())


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class InProcessTests(unittest.TestCase):
    def setUp(self):
        from gemarc_mcp import server

        self.server = server
        saved_registered, saved_write = list(server.REGISTERED_TOOLS), set(server.WRITE_TOOLS)
        self.addCleanup(self._restore, saved_registered, saved_write)

    def _restore(self, registered, write):
        self.server.REGISTERED_TOOLS[:] = registered
        self.server.WRITE_TOOLS.clear()
        self.server.WRITE_TOOLS.update(write)

    def test_a_reply_above_the_hard_cap_is_cut_and_says_so(self):
        app = self.server.create_server("atlas")
        huge = {"status": "ok", "authority": "local", "section": "all", "lang": "en-US", "bytes": 1, "text": "line of text\n" * 6000,
                "sections": []}
        with mock.patch("gemarc_mcp.atlas.tools.brief", return_value=huge):
            result = asyncio.run(app.call_tool("gemarc_atlas_brief", {}))
        payload = json.loads(result.content[0].text)
        self.assertLessEqual(len(result.content[0].text.encode("utf-8")), HARD_CAP)
        self.assertIs(payload["truncated"], True)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(result.is_error)

    def test_an_unexpected_failure_is_an_error_reply_with_no_path(self):
        app = self.server.create_server("atlas_remote")
        with mock.patch("gemarc_mcp.atlas.tools.brief", side_effect=OSError("C:/secret/place")):
            result = asyncio.run(app.call_tool("gemarc_atlas_brief", {}))
        payload = json.loads(result.content[0].text)
        self.assertTrue(result.is_error)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn("secret", result.content[0].text)

    def test_the_environment_selects_the_set_like_a_client_config_does(self):
        with mock.patch.dict(os.environ, {"GEMARC_TOOLSET": "atlas_remote"}):
            app = self.server.create_server()
        self.assertEqual([t.name for t in asyncio.run(app.list_tools())], ["gemarc_atlas_brief", "gemarc_atlas_check"])


if __name__ == "__main__":
    unittest.main()
