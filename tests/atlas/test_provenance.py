"""Provenance harness: one row per file of ``src/gemarc_mcp/atlas/``, no dangling rows, generated PROVENANCE.md.

The rows live in ``src/gemarc_mcp/atlas/provenance/<lane>.tsv``; ``tools/gen_provenance.py`` turns them (plus the
authorisation record ``authorisation.tsv``) into ``src/gemarc_mcp/atlas/PROVENANCE.md``. Every lane adds its rows to its
own table, then regenerates the file: ``python tools/gen_provenance.py``.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_provenance*.py"
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "gen_provenance.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("gen_provenance_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered while it executes
    spec.loader.exec_module(module)
    return module


gp = load_tool()

HEADER = "\t".join(gp.COLUMNS) + "\n"
AUTH_OK = (
    "scope\tstatus\tby\tdate\tnote\n"
    "ported-code\tGRANTED\tuser\t2026-10-30\tgeneric code may be ported\n"
    "publication\tPENDING\t-\t-\tnothing published\n"
)


class RepositoryTests(unittest.TestCase):
    """The real tree."""

    def test_tables_are_valid(self):
        rows = gp.load_rows()
        self.assertTrue(rows)
        gp.load_authorisation()

    def test_every_atlas_file_has_a_row(self):
        row_paths = {r.path for r in gp.load_rows()}
        missing = [p for p in gp.files_requiring_rows() if p not in row_paths]
        self.assertEqual(missing, [], "files without a provenance row; add them to your lane's "
                                     "src/gemarc_mcp/atlas/provenance/<lane>.tsv and run python tools/gen_provenance.py")

    def test_no_row_points_to_a_missing_file(self):
        dangling = [r.path for r in gp.load_rows() if not (REPO / r.path).is_file()]
        self.assertEqual(dangling, [])

    def test_provenance_md_equals_the_generated_text(self):
        output = REPO / "src" / "gemarc_mcp" / "atlas" / "PROVENANCE.md"
        self.assertTrue(output.is_file(), "run python tools/gen_provenance.py")
        expected = gp.render(gp.load_rows(), gp.load_authorisation())
        self.assertEqual(gp.read_text(output), expected, "PROVENANCE.md is stale: run python tools/gen_provenance.py")

    def test_provenance_md_is_stored_with_lf_only_and_no_bom(self):
        data = (REPO / "src" / "gemarc_mcp" / "atlas" / "PROVENANCE.md").read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"))

    def test_the_check_command_agrees(self):
        self.assertEqual(gp.problems_of(), [])

    def test_authorisation_record_says_porting_granted_and_publication_pending(self):
        auth = gp.load_authorisation()
        self.assertEqual((auth["ported-code"]["status"], auth["ported-code"]["by"], auth["ported-code"]["date"]), ("GRANTED", "user", "2026-10-30"))
        self.assertEqual(auth["publication"]["status"], "PENDING", "publication of the atlas is a user decision; this test pins PENDING "
                         "until the user edits provenance/authorisation.tsv")
        text = gp.read_text(REPO / "src" / "gemarc_mcp" / "atlas" / "PROVENANCE.md")
        self.assertEqual(text.count("PUBLICATION: PENDING"), 1)
        self.assertIn("Ported code: GRANTED by user on 2026-10-30", text)

    def test_origins_are_the_four_documented_values(self):
        self.assertEqual(gp.ORIGINS, ("ported-semantic", "ported-structural", "clean-room", "new"))
        self.assertTrue({r.origin for r in gp.load_rows()} <= set(gp.ORIGINS))

    def test_ported_rows_name_a_generic_reference_unit(self):
        for r in gp.load_rows():
            if r.origin == "new":
                self.assertEqual(r.reference_unit, "-", r.path)
            else:
                self.assertRegex(r.reference_unit, gp.UNIT_RE.pattern, r.path)


class GeneratorTests(unittest.TestCase):
    """The generator and the checks, on throw-away trees."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gemarc-prov-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prov = self.tmp / "provenance"
        self.prov.mkdir()
        (self.prov / gp.AUTHORISATION_FILE).write_bytes(AUTH_OK.encode("utf-8"))

    def table(self, lane: str, *rows: str) -> None:
        (self.prov / f"{lane}.tsv").write_bytes((HEADER + "".join(rows)).encode("utf-8"))

    def errors(self) -> str:
        with self.assertRaises(gp.ProvenanceError) as ctx:
            gp.load_rows(self.prov)
        return str(ctx.exception)

    def test_valid_rows_are_sorted_by_path_whatever_the_table_order(self):
        self.table("beta", "src/b.py\tnew\t-\tauthored\t-\n")
        self.table("alpha", "src/c.py\tclean-room\tengine core\twritten from the spec\t-\n", "src/a.py\tported-semantic\tsearch index\tre-implemented\tnone\n")
        rows = gp.load_rows(self.prov)
        self.assertEqual([r.path for r in rows], ["src/a.py", "src/b.py", "src/c.py"])
        self.assertEqual([r.lane for r in rows], ["alpha", "beta", "alpha"])

    def test_render_is_deterministic_lf_and_free_of_timestamps(self):
        self.table("alpha", "src/a.py\tnew\t-\tauthored\t-\n")
        rows, auth = gp.load_rows(self.prov), gp.load_authorisation(self.prov)
        first = gp.render(rows, auth)
        self.assertEqual(first, gp.render(rows, auth))
        self.assertNotIn("\r", first)
        self.assertTrue(first.endswith("|\n"))
        self.assertNotRegex(first, r"\d{2}:\d{2}:\d{2}")
        self.assertIn("PUBLICATION: PENDING", first)
        self.assertIn("| `src/a.py` | new | - | authored | - | alpha |", first)

    def test_pipes_in_cells_are_escaped(self):
        self.table("alpha", "src/a.py\tnew\t-\ta | b\t-\n")
        text = gp.render(gp.load_rows(self.prov), gp.load_authorisation(self.prov))
        self.assertIn("a \\| b", text)

    def test_bad_origin_is_reported(self):
        self.table("alpha", "src/a.py\tcopied\tengine core\tx\t-\n")
        self.assertIn("origin 'copied'", self.errors())

    def test_wrong_cell_count_and_empty_cells_are_reported(self):
        self.table("alpha", "src/a.py\tnew\t-\tonly four cells\n", "src/b.py\tnew\t-\t\t-\n")
        message = self.errors()
        self.assertIn("expected 5 tab separated cells", message)
        self.assertIn("empty treatment", message)

    def test_header_must_match(self):
        (self.prov / "alpha.tsv").write_bytes(b"path\torigin\n")
        self.assertIn("header", self.errors())

    def test_duplicate_paths_across_lanes_are_reported(self):
        self.table("alpha", "src/a.py\tnew\t-\tx\t-\n")
        self.table("beta", "src/a.py\tnew\t-\ty\t-\n")
        self.assertIn("already has a row in alpha.tsv", self.errors())

    def test_unsafe_paths_are_reported(self):
        for bad in ("/abs/x.py", "..\\x.py", "a/../b.py", "C:/x.py"):
            self.table("alpha", f"{bad}\tnew\t-\tx\t-\n")
            self.assertIn("relative to the repository root", self.errors(), bad)

    def test_reference_unit_rules(self):
        self.table("alpha", "src/a.py\tnew\tengine core\tx\t-\n")
        self.assertIn("no reference unit", self.errors())
        self.table("alpha", "src/a.py\tported-semantic\t-\tx\t-\n")
        self.assertIn("generic lower-case module name", self.errors())
        for unit in ("Engine Core", "src/widget.mjs", "unit.id", "x"):
            self.table("alpha", f"src/a.py\tclean-room\t{unit}\tx\t-\n")
            self.assertIn("generic lower-case module name", self.errors(), unit)

    def test_crlf_bom_and_blank_lines_are_tolerated(self):
        data = "\ufeff" + (HEADER + "\n" + "src/a.py\tnew\t-\tx\t-\n").replace("\n", "\r\n")
        (self.prov / "alpha.tsv").write_bytes(data.encode("utf-8"))
        self.assertEqual([r.path for r in gp.load_rows(self.prov)], ["src/a.py"])

    def test_authorisation_rules(self):
        (self.prov / gp.AUTHORISATION_FILE).write_bytes(b"scope\tstatus\tby\tdate\tnote\nported-code\tGRANTED\t-\t-\tx\n")
        with self.assertRaises(gp.ProvenanceError) as ctx:
            gp.load_authorisation(self.prov)
        message = str(ctx.exception)
        self.assertIn("names who granted it", message)
        self.assertIn("no row for scope publication", message)
        (self.prov / gp.AUTHORISATION_FILE).write_bytes(
            (AUTH_OK.replace("publication\tPENDING\t-\t-\tnothing published", "publication\tGRANTED\tsomeone\t2027-01-02\tscope text")).encode("utf-8"))
        text = gp.render([], gp.load_authorisation(self.prov))
        self.assertIn("PUBLICATION: GRANTED by someone on 2027-01-02, scope scope text", text)
        self.assertNotIn("PUBLICATION: PENDING", text)

    def make_tree(self) -> Path:
        root = self.tmp / "repo"
        atlas = root / "src" / "gemarc_mcp" / "atlas"
        (atlas / "provenance").mkdir(parents=True)
        (atlas / "sub" / "__pycache__").mkdir(parents=True)
        (atlas / "provenance" / gp.AUTHORISATION_FILE).write_bytes(AUTH_OK.encode("utf-8"))
        return root

    def test_files_requiring_rows_skips_exempt_files_only(self):
        root = self.make_tree()
        atlas = root / "src" / "gemarc_mcp" / "atlas"
        (atlas / "__init__.py").write_text('"""Only a docstring."""\n', encoding="utf-8")
        (atlas / "sub" / "__init__.py").write_text("", encoding="utf-8")
        (atlas / "sub" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x00")
        (atlas / "PROVENANCE.md").write_text("x", encoding="utf-8")
        (atlas / "provenance" / "lane.tsv").write_text(HEADER, encoding="utf-8")
        (atlas / "sub" / "code.py").write_text("x = 1\n", encoding="utf-8")
        (atlas / "sub2").mkdir()
        (atlas / "sub2" / "__init__.py").write_text('"""doc"""\nVALUE = 1\n', encoding="utf-8")
        (atlas / "data.json").write_text("{}", encoding="utf-8")
        (atlas / "provenance" / "notes.txt").write_text("not a table", encoding="utf-8")
        self.assertEqual(gp.files_requiring_rows(atlas, root), [
            "src/gemarc_mcp/atlas/data.json", "src/gemarc_mcp/atlas/provenance/notes.txt", "src/gemarc_mcp/atlas/sub/code.py",
            "src/gemarc_mcp/atlas/sub2/__init__.py"])

    def test_problems_of_reports_missing_rows_dangling_rows_and_staleness(self):
        root = self.make_tree()
        atlas = root / "src" / "gemarc_mcp" / "atlas"
        (atlas / "code.py").write_text("x = 1\n", encoding="utf-8")
        (atlas / "provenance" / "lane.tsv").write_bytes((HEADER + "src/gemarc_mcp/atlas/ghost.py\tnew\t-\tx\t-\n").encode("utf-8"))
        issues = gp.problems_of(root)
        self.assertIn("no provenance row for src/gemarc_mcp/atlas/code.py", issues)
        self.assertIn("lane.tsv: row points to a missing file: src/gemarc_mcp/atlas/ghost.py", issues)
        self.assertIn("PROVENANCE.md is missing (run python tools/gen_provenance.py)", issues)
        # fix everything: rows for the real files, regenerate
        (atlas / "provenance" / "lane.tsv").write_bytes((HEADER + "src/gemarc_mcp/atlas/code.py\tnew\t-\tx\t-\n").encode("utf-8"))
        gp.write(root)
        self.assertEqual(gp.problems_of(root), [])
        (atlas / "PROVENANCE.md").write_text("stale", encoding="utf-8")
        self.assertEqual(gp.problems_of(root), ["PROVENANCE.md is stale (run python tools/gen_provenance.py)"])

    def test_main_check_passes_on_the_real_tree(self):
        self.assertEqual(gp.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
