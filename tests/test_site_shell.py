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

The harness is checked from both sides here: the shells must pass it, and a
shell with the persistence removed must fail it.  A guard that cannot fail is
not a guard.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for candidate in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

HARNESS = REPO_ROOT / "tests" / "js" / "layout-harness.mjs"
NODE = shutil.which("node")

SHARED = REPO_ROOT / "src" / "gemarc_mcp" / "scaffold" / "templates" / "shared" / "app.js"
PREVIEW = REPO_ROOT / "src" / "gemarc_mcp" / "web" / "app.js"

#: The write inside the pointer-move handler: the one that makes the drag
#: survive a pointerup that never arrives. The semicolon is optional: the
#: two shells write the same call with a different punctuation, and the guard
#: must match the call, not the formatting.
DRAG_WRITE = re.compile(r"(pointermove[\s\S]{0,800}?)writeLayout\(\s*layoutState\s*\)\s*;?")


def remove_drag_write(source: str) -> str:
    """A shell that paints the new width but never stores it."""
    mutated, count = DRAG_WRITE.subn(r"\1", source, count=1)
    if count != 1:
        raise AssertionError("the drag write no longer matches: the shell changed shape")
    return mutated


@unittest.skipUnless(NODE, "node is not available")
class LayoutPersistenceTests(unittest.TestCase):
    """A drag that ends without a pointerup must not lose the width."""

    def _run(self, root=None):
        command = [NODE, str(HARNESS)]
        if root is not None:
            command.append(str(root))
        return subprocess.run(command, capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")

    def _report(self, root=None):
        result = self._run(root)
        if not result.stdout.strip():
            self.fail(f"the harness produced no report:\n{result.stderr}")
        return json.loads(result.stdout), result

    def test_the_harness_is_present(self):
        self.assertTrue(HARNESS.is_file(), f"{HARNESS} is missing")

    def test_both_shells_keep_the_width_the_reader_chose(self):
        report, result = self._report()
        self.assertEqual(result.returncode, 0,
                         f"a shell loses the layout the reader chose:\n{result.stdout}\n{result.stderr}")
        self.assertEqual(sorted(report), ["mvp", "shared"],
                         "both shells (the kit templates and the bundled preview) must be checked")
        for name, entry in report.items():
            with self.subTest(shell=name):
                self.assertTrue(entry["ok"], f"{name}: broken scenarios {entry['broken']}")
                self.assertIn("dragWithoutPointerUp", entry["results"])
                for scenario, values in entry["results"].items():
                    with self.subTest(shell=name, scenario=scenario):
                        self.assertEqual(values["stored"], values["expected"],
                                         f"{name}/{scenario}: what is stored is not what was chosen")
                        self.assertEqual(values["painted"], values["expected"],
                                         f"{name}/{scenario}: what is painted is not what was chosen")

    def test_the_harness_sees_the_defect_it_guards(self):
        """Each shell must fail the harness once its drag write is gone.

        The check is per shell and per scenario: "the harness returned non-zero"
        would also be true when the harness crashed on a missing file.
        """
        for name, source in (("shared", SHARED.read_text(encoding="utf-8")),
                             ("mvp", PREVIEW.read_text(encoding="utf-8"))):
            with self.subTest(shell=name):
                with tempfile.TemporaryDirectory(prefix="gemarc-shell-mutant-") as tmp:
                    root = Path(tmp)
                    (root / "src/gemarc_mcp/scaffold/templates/shared").mkdir(parents=True)
                    (root / "src/gemarc_mcp/web").mkdir(parents=True)
                    (root / "src/gemarc_mcp/scaffold/templates/shared/app.js").write_text(
                        remove_drag_write(source) if name == "shared" else source, encoding="utf-8")
                    (root / "src/gemarc_mcp/web/app.js").write_text(
                        remove_drag_write(source) if name == "mvp" else source, encoding="utf-8")

                    report, result = self._report(root)
                    self.assertNotEqual(result.returncode, 0,
                                        "the harness passes on a shell that loses the width: "
                                        "it guards nothing")
                    mutated = report[name]
                    self.assertFalse(mutated["ok"], f"{name} was mutated but the harness did not notice")
                    self.assertIn("dragWithoutPointerUp", mutated["broken"],
                                  f"{name}: the failure must be attributed to the drag scenario, "
                                  f"not to something else: {mutated['broken']}")
                    other = "mvp" if name == "shared" else "shared"
                    self.assertTrue(report[other]["ok"],
                                    f"{other} was not mutated but the harness reported it broken: "
                                    f"{report[other]['broken']}")


if __name__ == "__main__":
    unittest.main()
