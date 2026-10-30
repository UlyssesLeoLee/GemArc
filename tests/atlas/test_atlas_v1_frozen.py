"""The v1 contract stays frozen while the atlas is built next to it.

Pins, with literals written in this file AND read from the code at test time:

* the 16-name ``full`` toolset and the 8-name ``stateless`` toolset of ``gemarc_mcp.server``
  (registered order, write-tool set, ``stateless`` is a subset of ``full``);
* sha256 of the simulation golden file ``scaffold/cases/simulation-golden.json``;
* the per-file sha256 and two digests of ``scaffold/vocab/*.json``.

File hashes are taken over LF-normalised bytes (BOM stripped, CRLF -> LF), so a Windows checkout with
``core.autocrlf=true`` and a Linux checkout agree.

HOW TO UPDATE A PIN: a pin may only change because the v1 maintainers deliberately changed v1 (never because of
atlas work). Then run ``PYTHONPATH=src python tests/atlas/test_atlas_v1_frozen.py --print`` and paste the printed
literals over the constants below, in the same commit as the v1 change.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_atlas_v1_frozen*.py"
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
SERVER_PY = REPO / "src" / "gemarc_mcp" / "server.py"
GOLDEN = REPO / "src" / "gemarc_mcp" / "scaffold" / "cases" / "simulation-golden.json"
VOCAB_DIR = REPO / "src" / "gemarc_mcp" / "scaffold" / "vocab"

# --------------------------------------------------------------------------------------------------------
# Pinned values (computed at commit dad6f73 plus the strict-evidence fix, which touches neither artifact)
# --------------------------------------------------------------------------------------------------------

FULL_TOOLS = (
    "gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model", "gemarc_validate", "gemarc_simulate",
    "gemarc_diff", "gemarc_render", "gemarc_inspect", "gemarc_plan", "gemarc_scaffold", "gemarc_init", "gemarc_model",
    "gemarc_build", "gemarc_verify", "gemarc_export",
)
STATELESS_TOOLS = (
    "gemarc_brief", "gemarc_contracts", "gemarc_assets", "gemarc_new_model", "gemarc_validate", "gemarc_simulate",
    "gemarc_diff", "gemarc_render",
)
WRITE_TOOLS = ("gemarc_build", "gemarc_export", "gemarc_init", "gemarc_model", "gemarc_scaffold")

GOLDEN_SHA256 = "d3f55d2558e6f6cd8ef8f42a07a121694abe3d62a799c8b64bb93f743f7b76d6"
VOCAB_FILE_SHA256 = {
    "case-categories.json": "a6fe24e4e4f9258906c16161c5baf38e04c8daaaacaae308a84399ac551b1df9",
    "diagram-types.json": "7b2c0ba0368165ed609a0071becf920d16222edb8b1eef96b572a92958f29f65",
    "edge-types.json": "abaa2d5c42b3b1e64c975b0add23637054ca93161eda5b6e5608d04f87868d7c",
    "entity-kinds.json": "3e122f6a458b200723f2f142ce329e66404ee1b31788fb4a620a5f56c06d13e1",
    "evidence-levels.json": "68d073f5b1f0d257b3dd9488659e3002b319b581e1e6af6bf5868996f06fc6da",
    "relation-classes.json": "ece00f119702565e50ceacbab1e331c9c505d874c5be6e0fabdbec122af4b1f9",
    "space-levels.json": "af9f12817abdb7f7377c063ebd8b40199d2c00a857c605d5e11c6744d605ff23",
    "workflow-kinds.json": "00c79a1a621a361ba32d24e7190f56cc246696ffd197138938bb60dc5f5767fe",
}
#: sha256 over ``name\n<LF-normalised bytes>\n`` of every vocab file in name order.
VOCAB_BYTES_DIGEST = "9a921e3fab4774751413de13e9939a5ed0fc0381d8af187e79ce27b5f4342546"
#: ``gemarc_mcp.scaffold.sync.vocab_digest()``: parse-level digest the generated JS vocabulary also embeds.
VOCAB_SYNC_DIGEST = "fb8d1213f44a1d626f6d519df97806a86424731308b5a445c4ac672542f5e886"

UPDATE_HINT = (
    " A v1 artifact is frozen while the atlas is built. If v1 was changed on purpose by its maintainers, run "
    "`PYTHONPATH=src python tests/atlas/test_atlas_v1_frozen.py --print` and paste the printed literals into "
    "tests/atlas/test_atlas_v1_frozen.py in the same commit; if you did not mean to change v1, revert the change."
)


def normalised_bytes(path: Path) -> bytes:
    """File bytes with BOM stripped and CRLF/CR folded to LF (checkout independent)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(normalised_bytes(path)).hexdigest()


def vocab_files() -> list[Path]:
    return sorted(VOCAB_DIR.glob("*.json"))


def vocab_bytes_digest() -> str:
    h = hashlib.sha256()
    for path in vocab_files():
        h.update(path.name.encode("utf-8") + b"\n" + normalised_bytes(path) + b"\n")
    return h.hexdigest()


def static_registrations() -> tuple[list[str], list[str], list[str]]:
    """``(all names, stateless names, write names)`` read from the ``@tool(...)`` decorators of server.py by ``ast``.

    Works without the MCP SDK. Registrations that name a ``sets`` keyword (a later toolset restructure) are
    not v1 tools and are ignored here.
    """
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    names: list[str] = []
    stateless: list[str] = []
    write: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Name) and deco.func.id == "tool"):
                continue
            if not deco.args or not isinstance(deco.args[0], ast.Constant) or not isinstance(deco.args[0].value, str):
                continue
            keywords = {k.arg: k.value for k in deco.keywords if k.arg}
            if "sets" in keywords:
                continue
            name = deco.args[0].value
            names.append(name)
            for key, bucket in (("stateless_ok", stateless), ("write", write)):
                value = keywords.get(key)
                if isinstance(value, ast.Constant) and value.value is True:
                    bucket.append(name)
    return names, stateless, write


def current_values() -> dict:
    return {
        "GOLDEN_SHA256": sha256_file(GOLDEN),
        "VOCAB_FILE_SHA256": {p.name: sha256_file(p) for p in vocab_files()},
        "VOCAB_BYTES_DIGEST": vocab_bytes_digest(),
        "VOCAB_SYNC_DIGEST": _sync_digest(),
    }


def _sync_digest() -> str:
    from gemarc_mcp.scaffold import sync

    return sync.vocab_digest()


def _sdk_available() -> bool:
    try:
        import mcp.server  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class StaticToolsetTests(unittest.TestCase):
    """Read the registrations from the source text: needs no SDK, so it always runs."""

    def test_literal_lists_have_the_pinned_sizes(self):
        self.assertEqual((len(FULL_TOOLS), len(STATELESS_TOOLS), len(WRITE_TOOLS)), (16, 8, 5))
        self.assertEqual(len(set(FULL_TOOLS)), 16)
        self.assertTrue(set(STATELESS_TOOLS) <= set(FULL_TOOLS))
        self.assertTrue(set(WRITE_TOOLS) <= set(FULL_TOOLS) - set(STATELESS_TOOLS))

    def test_server_source_registers_exactly_the_pinned_full_toolset(self):
        names, _, _ = static_registrations()
        self.assertEqual(tuple(names), FULL_TOOLS, "the 16 v1 tools (names and order) are frozen" + UPDATE_HINT)

    def test_server_source_marks_exactly_the_pinned_stateless_tools(self):
        _, stateless, _ = static_registrations()
        self.assertEqual(tuple(stateless), STATELESS_TOOLS, "the 8 stateless tools are frozen" + UPDATE_HINT)

    def test_server_source_marks_exactly_the_pinned_write_tools(self):
        _, _, write = static_registrations()
        self.assertEqual(tuple(sorted(write)), WRITE_TOOLS)

    def test_toolset_names_and_default(self):
        from gemarc_mcp import server

        self.assertEqual(tuple(server.TOOLSETS[:2]), ("full", "stateless"),
                         "later toolsets may be appended, but 'full' and 'stateless' stay the first two")
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMARC_TOOLSET", None)
            self.assertEqual(server.active_toolset(), "full", "the local default toolset is 'full'")


@unittest.skipUnless(_sdk_available(), "MCP SDK is not installed")
class LiveToolsetTests(unittest.TestCase):
    def setUp(self):
        from gemarc_mcp import server

        self.server = server
        # create_server() rewrites these module-level lists; restore them so later tests are not affected.
        self._registered = list(server.REGISTERED_TOOLS)
        self._write = set(server.WRITE_TOOLS)
        self.addCleanup(self._restore)

    def _restore(self):
        self.server.REGISTERED_TOOLS[:] = self._registered
        self.server.WRITE_TOOLS.clear()
        self.server.WRITE_TOOLS.update(self._write)

    def test_full_toolset_is_registered_as_pinned(self):
        app = self.server.create_server("full")
        self.assertEqual(tuple(self.server.REGISTERED_TOOLS), FULL_TOOLS, UPDATE_HINT)
        self.assertEqual(tuple(sorted(self.server.WRITE_TOOLS)), WRITE_TOOLS)
        listed = [t.name for t in asyncio.run(app.list_tools())]
        self.assertEqual(tuple(listed), FULL_TOOLS)

    def test_stateless_toolset_is_registered_as_pinned(self):
        app = self.server.create_server("stateless")
        self.assertEqual(tuple(self.server.REGISTERED_TOOLS), STATELESS_TOOLS, UPDATE_HINT)
        self.assertEqual(self.server.WRITE_TOOLS, set(), "the stateless toolset has no write tool")
        listed = [t.name for t in asyncio.run(app.list_tools())]
        self.assertEqual(tuple(listed), STATELESS_TOOLS)

    def test_live_lists_agree_with_the_static_reading(self):
        names, stateless, write = static_registrations()
        self.server.create_server("full")
        self.assertEqual(list(self.server.REGISTERED_TOOLS), names)
        self.assertEqual(sorted(self.server.WRITE_TOOLS), sorted(write))
        self.server.create_server("stateless")
        self.assertEqual(list(self.server.REGISTERED_TOOLS), stateless)


class FrozenArtifactTests(unittest.TestCase):
    def test_simulation_golden_file_is_unchanged(self):
        self.assertEqual(sha256_file(GOLDEN), GOLDEN_SHA256,
                         "src/gemarc_mcp/scaffold/cases/simulation-golden.json changed (hash over LF-normalised bytes)." + UPDATE_HINT)

    def test_vocab_files_are_the_pinned_set(self):
        self.assertEqual([p.name for p in vocab_files()], sorted(VOCAB_FILE_SHA256),
                         "a scaffold/vocab/*.json file was added or removed." + UPDATE_HINT)

    def test_each_vocab_file_is_unchanged(self):
        for path in vocab_files():
            self.assertEqual(sha256_file(path), VOCAB_FILE_SHA256[path.name], f"{path.name} changed." + UPDATE_HINT)

    def test_vocab_digests_are_unchanged(self):
        self.assertEqual(vocab_bytes_digest(), VOCAB_BYTES_DIGEST, "vocab byte digest changed." + UPDATE_HINT)
        self.assertEqual(_sync_digest(), VOCAB_SYNC_DIGEST, "scaffold.sync.vocab_digest() changed." + UPDATE_HINT)

    def test_atlas_vocabulary_is_not_stored_in_the_v1_vocab_directory(self):
        # The atlas owns src/gemarc_mcp/atlas/profiles/; scaffold/vocab is pinned by v1 (digest, generated JS, demo site).
        self.assertTrue(all(not p.name.startswith("atlas") for p in vocab_files()))


def _print_values() -> None:
    """Print the literals to paste after a deliberate v1 change."""
    values = current_values()
    print(f'GOLDEN_SHA256 = "{values["GOLDEN_SHA256"]}"')
    print("VOCAB_FILE_SHA256 = {")
    for name, digest in values["VOCAB_FILE_SHA256"].items():
        print(f'    "{name}": "{digest}",')
    print("}")
    print(f'VOCAB_BYTES_DIGEST = "{values["VOCAB_BYTES_DIGEST"]}"')
    print(f'VOCAB_SYNC_DIGEST = "{values["VOCAB_SYNC_DIGEST"]}"')
    names, stateless, write = static_registrations()
    print("# tools registered in server.py (static reading):")
    print("FULL_TOOLS =", tuple(names))
    print("STATELESS_TOOLS =", tuple(stateless))
    print("WRITE_TOOLS =", tuple(sorted(write)))


if __name__ == "__main__":
    if "--print" in sys.argv:
        _print_values()
    else:
        unittest.main()
