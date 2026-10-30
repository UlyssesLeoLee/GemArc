"""The one-time self-review through the OFFICIAL MCP client (design addendum A1, D15): ``gemarc_atlas_review`` over stdio.

* ``ReviewFlowOverStdio``: a real child process (tool set ``atlas``, writes allowed): init, four puts, status at phase review,
  ``report`` (read-only: the project tree is byte-identical after it), ``record``, status done, a second ``record`` refused, the
  entry prompt's review clause;
* ``ReviewWithoutTheWriteGate``: the same server without ``GEMARC_ALLOW_WRITE``: ``report`` works, ``record`` is refused with
  ``GEMARC_AUTH_002`` and nothing is created.

All data is synthetic (the lending desk fixture). Replies must be one compact JSON text, at most 20 KiB, ``isError`` equal to
``status in invalid|error``.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_review_tool.py"
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
for candidate in (str(REPO / "src"), str(REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

HARD_CAP = 20 * 1024
REPORT_CAP = 4500
DESK = {name: (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8") for name in ("00-space", "10-structure", "20-scenarios")}
DESK["30-policy"] = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"
TOOL = "gemarc_atlas_review"


def _sdk_available() -> bool:
    try:
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: F401
        from mcp.server import MCPServer  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@contextlib.contextmanager
def temp_dir(prefix: str):
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Caller:
    def __init__(self, session, case: unittest.TestCase):
        self.session, self.case = session, case

    async def call(self, name: str, **arguments):
        result = await self.session.call_tool(name, arguments)
        case = self.case
        case.assertEqual(len(result.content), 1, name)
        text = result.content[0].text
        payload = json.loads(text)
        case.assertIsNone(result.structured_content, f"{name}: structuredContent would hide the text in Codex")
        case.assertEqual(text, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), f"{name}: reply is not compact JSON")
        case.assertLessEqual(len(text.encode("utf-8")), HARD_CAP, name)
        failed = payload.get("status") in ("invalid", "error")
        case.assertIs(bool(result.is_error), failed, f"{name}: isError must equal status in invalid|error: {payload.get('status')}")
        return result, payload


async def stdio_session(env_extra: dict, workspace: Path, body):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"GEMARC_TOOLSET": "atlas", "GEMARC_WORKSPACE_ROOT": str(workspace), "PYTHONUTF8": "1", "PYTHONPATH": str(REPO / "src"), **env_extra}
    with temp_dir("atlas-review-cwd-") as cwd, tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errlog:
        params = StdioServerParameters(command=sys.executable, args=["-m", "gemarc_mcp"], env=env, cwd=str(cwd))
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=120) as session:
                init = await session.initialize()
                await body(session, init)
        errlog.seek(0)
        return errlog.read()


def build_desk_project(workspace: Path, name: str = "lending-desk") -> Path:
    """The lending desk as a project on disk (in this process, writes allowed for the duration)."""
    from gemarc_mcp.atlas import project as pj
    from gemarc_mcp.atlas import tools

    with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1"}):
        ws = pj.get_workspace(workspace)
        assert tools.init(ws, name, "Library lending desk", "asis", ["en-US"], repos=["main"])["status"] == "created"
        for slice_, text in DESK.items():
            assert tools.put(ws, name, slice_, text)["status"] == "committed", slice_
    return workspace / name / "atlas"


def check_sample(case: unittest.TestCase, atlas_dir: Path, report: dict):
    """Every sample row names a file and line of the project, and that line is the row of the claimed record."""
    case.assertEqual(len(report["sample"]), 8)
    for row in report["sample"]:
        rel, _, line = row["at"].rpartition(":")
        text = (atlas_dir / rel).read_text(encoding="utf-8").splitlines()[int(line) - 1]
        parts = [row["id"].split(":")[0], row["id"].split(":")[2]] if row["k"] == "ed" else [row["id"]]
        case.assertTrue(all(part in text for part in parts), (row, text))


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class ReviewFlowOverStdio(unittest.TestCase):
    def test_the_whole_flow_through_the_official_client(self):
        with temp_dir("atlas-review-ws-") as workspace:
            stderr = asyncio.run(stdio_session({"GEMARC_ALLOW_WRITE": "1"}, workspace, lambda s, i: self.session(s, i, workspace)))
        self.assertNotIn("Traceback", stderr)

    async def session(self, session, init, workspace: Path):
        from gemarc_mcp import atlas_tools

        call = Caller(session, self).call
        atlas_dir = workspace / "lending-desk" / "atlas"

        # ---- the tool is listed with honest hints and a short description ----------------------------------
        tools = {t.name: t for t in (await session.list_tools()).tools}
        self.assertIn(TOOL, tools)
        self.assertEqual(list(tools), list(atlas_tools.tool_names("atlas")))
        review = tools[TOOL]
        self.assertFalse(review.annotations.read_only_hint, "record writes: the tool is not read-only")
        self.assertFalse(review.annotations.destructive_hint)
        self.assertFalse(review.annotations.idempotent_hint)
        self.assertFalse(review.annotations.open_world_hint)
        self.assertLessEqual(len(review.description.encode("utf-8")), 800)
        self.assertEqual(sorted(review.input_schema["properties"]), ["action", "notes", "project"])
        self.assertEqual(review.input_schema["required"], ["project"])
        self.assertNotIn("anyOf", json.dumps(review.input_schema))

        # ---- the entry prompt now carries the real review clause -----------------------------------------------
        prompt = (await session.get_prompt("gemarc-atlas-create", {"requirement": "x", "project": "lending-desk"})).messages[0].content.text
        self.assertIn('action="record"', prompt)
        self.assertNotIn("尚未提供", prompt)
        self.assertIn("不得再开第二轮自审", prompt)

        # ---- author the desk ---------------------------------------------------------------------------------
        await call("gemarc_atlas_init", project="lending-desk", title="Library lending desk", mode="asis", languages=["en-US"], repos=["main"])
        early, refused = await call(TOOL, project="lending-desk", action="record")
        self.assertTrue(early.is_error)
        self.assertEqual(refused["code"], "GEMARC_ATLAS_REVIEW_003", "no record before phase review")
        self.assertFalse((atlas_dir / ".atlas" / "review.json").exists())
        for slice_, text in DESK.items():
            _, put = await call("gemarc_atlas_put", project="lending-desk", slice=slice_, text=text)
            self.assertEqual(put["status"], "committed", put)
        _, state = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual((state["phase"], state["done"]), ("review", False))
        self.assertIn("review:missing", state["blockers"])

        # ---- report: compact, deterministic, read-only ---------------------------------------------------------
        before = tree(workspace)
        result, report = await call(TOOL, project="lending-desk")
        self.assertFalse(result.is_error)
        _, again = await call(TOOL, project="lending-desk", action="report")
        self.assertEqual(tree(workspace), before, "report never writes")
        self.assertEqual(report, again)
        self.assertEqual((report["status"], report["action"], report["authority"]), ("ok", "report", "local"))
        self.assertLessEqual(len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), REPORT_CAP)
        self.assertEqual(report["counts"]["BLOCK"], 0)
        self.assertEqual(report["record"], {"state": "none"})
        self.assertEqual([f["id"] for f in report["findings"]], ["RV-H1", "RV-E4"], "the by=ai policy and the anchors nobody verified yet")
        check_sample(self, atlas_dir, report)

        # ---- bad calls are refusals (isError), nothing written ------------------------------------------------
        bad, body = await call(TOOL, project="lending-desk", action="bogus")
        self.assertTrue(bad.is_error)
        self.assertEqual(body["code"], "GEMARC_ATLAS_REVIEW_001")
        missing, _ = await call(TOOL, project="no-such-project")
        self.assertTrue(missing.is_error)
        self.assertEqual(tree(workspace), before)

        # ---- record once, status done -----------------------------------------------------------------------
        result, recorded = await call(TOOL, project="lending-desk", action="record", notes="sample checked against the lending docs; open=RV-H1")
        self.assertFalse(result.is_error, recorded)
        self.assertEqual((recorded["status"], recorded["done"], recorded["phase"]), ("recorded", True, "done"))
        record = json.loads((atlas_dir / ".atlas" / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "gemarc-atlas-review/1")
        self.assertIn("open=RV-H1", record["notes"])
        _, done = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual((done["done"], done["phase"], done["blockers"]), (True, "done", []))
        snapshot = tree(workspace)
        second, refused = await call(TOOL, project="lending-desk", action="record", notes="again")
        self.assertTrue(second.is_error)
        self.assertEqual(refused["code"], "GEMARC_ATLAS_REVIEW_002")
        self.assertEqual(tree(workspace), snapshot, "the refused second record changed nothing")
        _, after = await call(TOOL, project="lending-desk")
        self.assertEqual(after["record"]["state"], "current")
        self.assertEqual(after["sample"], report["sample"], "the sample depends on the model only")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class ReviewWithoutTheWriteGate(unittest.TestCase):
    def test_report_reads_and_record_is_refused_without_creating_anything(self):
        with temp_dir("atlas-review-ro-") as workspace:
            atlas_dir = build_desk_project(workspace)
            asyncio.run(stdio_session({}, workspace, lambda s, i: self.session(s, i, workspace, atlas_dir)))

    async def session(self, session, init, workspace: Path, atlas_dir: Path):
        call = Caller(session, self).call
        before = tree(workspace)
        result, report = await call(TOOL, project="lending-desk")
        self.assertFalse(result.is_error, "report is read-only: it works without GEMARC_ALLOW_WRITE")
        self.assertEqual(report["status"], "ok")
        check_sample(self, atlas_dir, report)
        refused, body = await call(TOOL, project="lending-desk", action="record", notes="x")
        self.assertTrue(refused.is_error)
        self.assertEqual(body["code"], "GEMARC_AUTH_002")
        self.assertFalse((atlas_dir / ".atlas" / "review.json").exists())
        self.assertEqual(tree(workspace), before, "neither call changed a byte of the project")
        _, state = await call("gemarc_atlas_status", project="lending-desk")
        self.assertEqual((state["phase"], state["done"]), ("review", False))


if __name__ == "__main__":
    unittest.main()
