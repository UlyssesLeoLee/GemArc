"""The site shell must remember the layout the reader chose.

The shell (the fixed part of the kit) is the one place where "the reader
dragged a pane" turns into a number that is either kept or lost.  Nothing in
the unit tests renders a DOM, so a shell that paints the new width but keeps
the old one in storage passes every other test and loses the reader's choice
at the next focus toggle or reload.

``tests/js/layout-harness.mjs`` drives the real shell code in a stub DOM: it
drags a splitter and deliberately never delivers the ``pointerup``.  A shell
that only persists on ``pointerup`` stores nothing there; a shell that
persists during the drag stores the width the reader picked.
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

HARNESS = REPO_ROOT / "tests" / "js" / "layout-harness.mjs"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not available")
class LayoutPersistenceTests(unittest.TestCase):
    """A drag that ends without a pointerup must not lose the width."""

    def _run(self):
        result = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True,
                                timeout=120, encoding="utf-8", errors="replace")
        return result

    def test_the_harness_is_present(self):
        self.assertTrue(HARNESS.is_file(), f"{HARNESS} is missing")

    def test_both_shells_keep_the_width_the_reader_chose(self):
        result = self._run()
        self.assertEqual(result.returncode, 0,
                         f"the shell loses the dragged width:\n{result.stdout}\n{result.stderr}")
        report = json.loads(result.stdout)
        self.assertEqual(sorted(report), ["mvp", "shared"],
                         "both shells (the kit templates and the bundled preview) must be checked")
        for name, entry in report.items():
            with self.subTest(shell=name):
                self.assertTrue(entry["ok"], f"{name}: {entry}")
                stored = entry["stored"]
                self.assertIsNotNone(stored, f"{name} stored nothing")
                self.assertGreater(stored["sidebar"], 232,
                                   f"{name} did not keep the dragged width")
                self.assertEqual(entry["painted"], f"{stored['sidebar']}px",
                                 f"{name}: what is painted and what is stored disagree")

    def test_the_harness_sees_the_defect_it_guards(self):
        """The harness must fail on a shell that only persists on pointerup."""
        mutant = REPO_ROOT.parent / "GemArcWork" / "mutant-tree"
        shared = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "app.js"
        preview = REPO_ROOT / "src" / "gemarc_mcp" / "web" / "app.js"
        if not mutant.parent.is_dir():
            self.skipTest("the work directory is not available")
        try:
            (mutant / "src/gemarc_mcp/scaffold/templates/shared").mkdir(parents=True, exist_ok=True)
            (mutant / "src/gemarc_mcp/web").mkdir(parents=True, exist_ok=True)
            # The pre-fix behaviour: the drag paints, but only pointerup persists.
            broken = shared.read_text(encoding="utf-8").replace(
                "    writeLayout(current);\n  });\n  const stop",
                "  });\n  const stop", 1)
            self.assertNotEqual(broken, shared.read_text(encoding="utf-8"),
                                "the mutation no longer applies: the shell changed")
            (mutant / "src/gemarc_mcp/scaffold/templates/shared/app.js").write_text(broken, encoding="utf-8")
            (mutant / "src/gemarc_mcp/web/app.js").write_text(
                preview.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run([NODE, str(HARNESS), str(mutant)], capture_output=True,
                                    text=True, timeout=120, encoding="utf-8", errors="replace")
            self.assertNotEqual(result.returncode, 0,
                                "the harness passes on a shell that loses the width: it guards nothing")
        finally:
            shutil.rmtree(mutant, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
