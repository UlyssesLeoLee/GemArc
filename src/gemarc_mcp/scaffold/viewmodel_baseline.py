"""
Regenerate the committed view-model structure baseline.

The baseline pins what the site's presentation layer produces for a fixed
fixture model. Run this only when a presentation change is intended:

    python -m gemarc_mcp.scaffold.viewmodel_baseline --write
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import service
from ..paths import Workspace

SCAFFOLD = Path(__file__).resolve().parent
FIXTURE = SCAFFOLD / "cases" / "view-model-fixture.json"
BASELINE = SCAFFOLD / "cases" / "view-model-baseline.json"
DUMP_TOOL = SCAFFOLD / "runtime" / "tools" / "dump-view-model.mjs"


def build_fixture_site(root: Path) -> Path:
    os.environ["GEMARC_ALLOW_WRITE"] = "1"
    workspace = Workspace(root)
    project = workspace.project("fixture")
    project.mkdir(parents=True, exist_ok=True)
    (project / "model").mkdir(parents=True, exist_ok=True)
    (project / "model" / "architecture.json").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    built = service.build(workspace, "fixture", None, "static")
    return Path(built["site"])


def dump(site: Path) -> dict:
    result = subprocess.run(
        ["node", str(DUMP_TOOL), str(site)],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"dump failed: {(result.stderr or '').strip()[:2000]}")
    return json.loads(result.stdout)


def write_baseline() -> Path:
    tmp = tempfile.mkdtemp(prefix="gemarc-baseline-")
    try:
        site = build_fixture_site(Path(tmp))
        payload = dump(site)
    finally:
        os.environ["GEMARC_ALLOW_WRITE"] = "0"
        shutil.rmtree(tmp, ignore_errors=True)
    BASELINE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return BASELINE


if __name__ == "__main__":
    import sys

    if "--write" in sys.argv:
        print(f"wrote {write_baseline()}")
    else:
        print("usage: python -m gemarc_mcp.scaffold.viewmodel_baseline --write")
