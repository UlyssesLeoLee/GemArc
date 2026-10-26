"""
The bundled preview site must stay in step with the code that generates it.

``examples/demo-site`` is a committed build artifact. It was once left behind
after the canonical model moved from ``source``/``target``/``kind`` to
``sourceId``/``targetId``/``type``: the shipped JavaScript still read the old
field names, so the preview drew four nodes and **zero** relations while the
repository claimed the site worked.

Nothing caught that, because the artifact is only broken once you open it. This
module rebuilds the site with the real build path and compares it byte for byte
with the committed tree, so a stale snapshot fails in CI instead of in a
browser.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

PREVIEW_DIR = REPO_ROOT / "examples" / "demo-site"
SEED_MODEL = PREVIEW_DIR / "data" / "architecture.json"


def _normalised(blob: bytes) -> bytes:
    """Compare content, not the checkout's line-ending style.

    The repository has ``core.autocrlf=true`` and no ``.gitattributes``, so the
    same file arrives as CRLF in one checkout and LF in another. Comparing raw
    bytes would make this gate fail on a fresh clone for a reason that has
    nothing to do with drift.
    """
    return blob.replace(b"\r\n", b"\n")


class GeneratedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="gemarc-artifact-test-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self._previous_env = {
            key: os.environ.get(key)
            for key in ("GEMARC_WORKSPACE_ROOT", "GEMARC_ALLOW_WRITE")
        }
        self.addCleanup(self._restore_env)
        os.environ["GEMARC_WORKSPACE_ROOT"] = str(self.workspace)
        os.environ["GEMARC_ALLOW_WRITE"] = "1"

    def _restore_env(self):
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _build_preview(self) -> Path:
        from gemarc_mcp.paths import Workspace
        from gemarc_mcp import service

        model = json.loads(SEED_MODEL.read_text(encoding="utf-8"))
        project = self.workspace / "preview" / "model"
        project.mkdir(parents=True, exist_ok=True)
        (project / "architecture.json").write_text(
            json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = service.build(Workspace(str(self.workspace)), "preview", None, "mvp")
        self.assertEqual(result["status"], "ok")
        return self.workspace / "preview" / "site"

    def test_committed_preview_matches_a_fresh_build(self):
        site = self._build_preview()
        produced = {
            str(p.relative_to(site)).replace("\\", "/"): _normalised(p.read_bytes())
            for p in site.rglob("*")
            if p.is_file()
        }
        committed = {
            str(p.relative_to(PREVIEW_DIR)).replace("\\", "/"): _normalised(p.read_bytes())
            for p in PREVIEW_DIR.rglob("*")
            if p.is_file()
        }
        self.assertEqual(
            sorted(produced), sorted(committed),
            "examples/demo-site 与构建产物不一致；请用 gemarc build --profile mvp 重新生成",
        )
        for name in sorted(produced):
            self.assertEqual(
                produced[name], committed[name], f"examples/demo-site/{name} 已过期"
            )

    def test_preview_app_reads_both_old_and_new_edge_field_names(self):
        """The preview must not break when the canonical model is renamed again."""
        source = (REPO_ROOT / "src" / "gemarc_mcp" / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("e.sourceId ?? e.source", source)
        self.assertIn("e.targetId ?? e.target", source)
        self.assertIn("e.type ?? e.kind", source)

    def test_preview_capabilities_are_not_placeholders(self):
        capabilities = json.loads(
            (PREVIEW_DIR / "data" / "capabilities.json").read_text(encoding="utf-8")
        )
        for key in ("viewer2D", "viewer3D", "dynamic4D", "causal5D", "profile"):
            self.assertIn(key, capabilities)
            self.assertTrue(capabilities[key])


if __name__ == "__main__":
    unittest.main()
