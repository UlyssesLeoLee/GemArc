"""The fingerprints and the versions of what the atlas produced (design ``docs/atlas/spec/versions.md``).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_versions*.py"

* ``FingerprintTests``: the three words (fingerprint, version label, ledger) and the class of a file.
* ``InventoryTests``: the fingerprint list of an output tree (the trust roots stay out of it).
* ``SiteFingerprintTests``: the fingerprint of the whole site.
* ``LedgerTests``: the ledger on disk (a damaged one reads as empty, an unchanged document keeps its label).
* ``BuildTests``: the build carries the fingerprints, writes the ledger and stays byte-identical.
* ``VersionsToolTests``: the three views of ``gemarc_atlas_versions`` (history, fingerprints, the pre-update check).

Synthetic id families only.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gemarc_mcp.atlas import build, tools
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import versions as vrs

from .test_oracle_support import OracleProject, desk_files

#: The shape of a version label: a fingerprint, a date and a minute.
LABEL_RE = re.compile(r"^[0-9a-f]{8}-\d{8}-\d{4}$")
#: A label must not appear in the output (V-1: the output is byte-identical between two builds).
LABEL_IN_OUTPUT_RE = re.compile(r"[0-9a-f]{8}-\d{8}-\d{4}")
#: A timestamp must not appear in the output either.
STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:")


def doc_rows(path: Path) -> list:
    """The fingerprint rows of the derived documents of a built tree."""
    return [row for row in vrs.inventory(path) if row["k"] == "doc"]


def make_paths(root: Path) -> pj.Paths:
    """The paths of one project inside a temporary root (the same shape ``pj.project_paths`` builds)."""
    atlas = root / "desk" / "atlas"
    dot = atlas / ".atlas"
    return pj.Paths("desk", root / "desk", atlas, atlas / "atlas.toml", atlas / "slices", dot, dot / "red", dot / "history",
                    dot / "lock", dot / "oracle.json", dot / "evidence-stamp.json", dot / "review.json", atlas / "out", atlas / "user")


class FingerprintTests(unittest.TestCase):
    def test_the_fingerprint_is_the_first_eight_hex_digits_of_the_sha256(self):
        import hashlib

        self.assertEqual(vrs.sha8_of(b"abc"), hashlib.sha256(b"abc").hexdigest()[:8])
        self.assertEqual(len(vrs.sha8_of(b"")), 8)
        self.assertNotEqual(vrs.sha8_of(b"a"), vrs.sha8_of(b"b"))

    def test_the_label_is_the_fingerprint_and_the_minute_in_utc(self):
        when = datetime(2026, 11, 10, 15, 34, 56, tzinfo=timezone.utc)
        label = vrs.label_of("3f2a1b9c", when)
        self.assertEqual(label, "3f2a1b9c-20261110-1534")
        self.assertRegex(label, LABEL_RE)

    def test_the_label_is_the_utc_minute_and_not_the_local_one(self):
        # 23:30 UTC is the next day in a +09:00 zone: the label must not follow the local clock.
        when = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(vrs.label_of("aaaaaaaa", when), "aaaaaaaa-20261110-2330")

    def test_the_minute_of_a_label_is_read_back(self):
        self.assertEqual(vrs.label_time("3f2a1b9c-20261110-1534"), "20261110-1534")
        self.assertEqual(vrs.label_time("3f2a1b9c-2026111-1534"), "")
        self.assertEqual(vrs.label_time("3f2a1b9c-20261310-1534"), "")
        self.assertEqual(vrs.label_time("not a label"), "")

    def test_the_class_of_a_file_says_what_it_is(self):
        self.assertEqual(vrs.classify("docs/en-US/BD.md"), ("doc", "1D"))
        self.assertEqual(vrs.classify("read/1D/R01.md"), ("read", "1D"))
        self.assertEqual(vrs.classify("read/2D/S01.md"), ("read", "2D"))
        self.assertEqual(vrs.classify("diagrams/rm.desk.mmd"), ("diagram", ""))
        self.assertEqual(vrs.classify("prompts/p1.md"), ("prompt", ""))
        self.assertEqual(vrs.classify("index.html"), ("viewer", ""))
        self.assertEqual(vrs.classify("site/read/1D/R01.html"), ("twin", ""))
        self.assertEqual(vrs.classify("manifest.jsonl"), ("data", ""))

    def test_every_kind_the_classes_use_is_in_the_vocabulary(self):
        for path in ("docs/en-US/BD.md", "read/1D/R01.md", "read/root.md", "screens/a.md", "diagrams/a.mmd",
                     "prompts/a.md", "site/a.html", "index.html", "other.txt"):
            kind, _ = vrs.classify(path)
            self.assertIn(kind, vrs.KINDS, path)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "dist"
        (self.root / "docs" / "en-US").mkdir(parents=True)
        (self.root / "read" / "1D").mkdir(parents=True)
        (self.root / ".verify").mkdir()
        (self.root / "docs" / "en-US" / "BD.md").write_text("# BD\n", encoding="utf-8")
        (self.root / "read" / "1D" / "R01.md").write_text("# R01\n", encoding="utf-8")
        (self.root / "index.html").write_text("<html></html>", encoding="utf-8")
        (self.root / "build-info.json").write_text("{}\n", encoding="utf-8")
        (self.root / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
        (self.root / "oracle-sheet.md").write_text("# sheet\n", encoding="utf-8")
        (self.root / ".verify" / "gate-report.json").write_text("{}\n", encoding="utf-8")

    def test_the_inventory_lists_the_deliverable_files_only(self):
        rows = vrs.inventory(self.root)
        self.assertEqual([row["p"] for row in rows], ["docs/en-US/BD.md", "index.html", "read/1D/R01.md"])
        self.assertEqual([row["k"] for row in rows], ["doc", "viewer", "read"])
        self.assertEqual([row["dim"] for row in rows], ["1D", "", "1D"])
        for row in rows:
            self.assertRegex(row["sha8"], r"^[0-9a-f]{8}$")
            self.assertGreater(row["b"], 0)

    def test_the_trust_roots_stay_out_of_the_inventory(self):
        listed = {row["p"] for row in vrs.inventory(self.root)}
        for name in ("build-info.json", "manifest.jsonl", "oracle-sheet.md"):
            self.assertNotIn(name, listed, name)
        self.assertFalse(any(p.startswith(".verify/") for p in listed))

    def test_a_file_bigger_than_the_limit_is_listed_without_a_fingerprint(self):
        big = self.root / "big.bin"
        big.write_bytes(b"0" * (vrs.MAX_FILE_BYTES + 1))
        rows = {row["p"]: row for row in vrs.inventory(self.root)}
        self.assertIn("big.bin", rows)
        self.assertEqual(rows["big.bin"]["sha8"], "")
        self.assertEqual(rows["big.bin"]["b"], vrs.MAX_FILE_BYTES + 1)

    def test_a_missing_tree_is_an_empty_inventory(self):
        self.assertEqual(vrs.inventory(self.root / "nowhere"), [])


class SiteFingerprintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "dist"
        self.root.mkdir(parents=True)
        (self.root / "index.html").write_text("<html>a</html>", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "BD.md").write_text("# BD\n", encoding="utf-8")

    def test_the_same_tree_has_the_same_fingerprint(self):
        first = vrs.site_fingerprint(self.root)
        second = vrs.site_fingerprint(self.root)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["sha8"], first["sha256"][:8])
        self.assertEqual(first["files"], 2)

    def test_a_changed_file_changes_the_fingerprint(self):
        before = vrs.site_fingerprint(self.root)
        (self.root / "docs" / "BD.md").write_text("# BD changed\n", encoding="utf-8")
        after = vrs.site_fingerprint(self.root)
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_a_renamed_file_changes_the_fingerprint(self):
        before = vrs.site_fingerprint(self.root)
        (self.root / "docs" / "BD.md").rename(self.root / "docs" / "DD.md")
        after = vrs.site_fingerprint(self.root)
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_the_fingerprint_file_itself_is_not_inside_the_fingerprint(self):
        before = vrs.site_fingerprint(self.root)
        (self.root / "build-info.json").write_text('{"fingerprints": {}}\n', encoding="utf-8")
        self.assertEqual(before["sha256"], vrs.site_fingerprint(self.root)["sha256"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = make_paths(Path(self._tmp.name))
        self.paths.dot.mkdir(parents=True)

    def ledger(self) -> dict:
        return vrs.read_ledger(self.paths)

    def test_a_missing_ledger_reads_as_empty(self):
        self.assertEqual(self.ledger(), {"schema": vrs.SCHEMA, "entries": []})

    def test_a_damaged_ledger_reads_as_empty(self):
        vrs.ledger_file(self.paths).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.ledger()["entries"], [])

    def test_a_ledger_of_another_schema_reads_as_empty(self):
        vrs.ledger_file(self.paths).write_text(json.dumps({"schema": "other/1", "entries": [{"at": "x"}]}), encoding="utf-8")
        self.assertEqual(self.ledger()["entries"], [])

    def test_an_unchanged_document_keeps_its_label_and_its_minute(self):
        first = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        later = first + timedelta(hours=3)
        rows = [{"p": "docs/en-US/BD.md", "sha8": "3f2a1b9c"}]
        one = vrs.record(self.paths, {"sha256": "a" * 64, "sha8": "aaaaaaaa"}, rows, "11111111", when=first)
        two = vrs.record(self.paths, {"sha256": "b" * 64, "sha8": "bbbbbbbb"}, rows, "22222222", when=later)
        self.assertEqual(one["docs"]["docs/en-US/BD.md"]["label"], "3f2a1b9c-20261110-1534")
        self.assertEqual(two["docs"]["docs/en-US/BD.md"]["label"], "3f2a1b9c-20261110-1534")
        self.assertEqual(two["docs"]["docs/en-US/BD.md"]["at"], "2026-11-10T15:34:00Z")

    def test_a_changed_document_gets_a_new_label(self):
        first = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        later = first + timedelta(hours=3)
        vrs.record(self.paths, {"sha256": "a" * 64, "sha8": "aaaaaaaa"},
                   [{"p": "docs/en-US/BD.md", "sha8": "3f2a1b9c"}], "11111111", when=first)
        two = vrs.record(self.paths, {"sha256": "b" * 64, "sha8": "bbbbbbbb"},
                         [{"p": "docs/en-US/BD.md", "sha8": "9f8e7d6c"}], "22222222", when=later)
        self.assertEqual(two["docs"]["docs/en-US/BD.md"]["label"], "9f8e7d6c-20261110-1834")

    def test_the_entry_carries_the_site_fingerprint_and_its_label(self):
        when = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        entry = vrs.record(self.paths, {"sha256": "a" * 64, "sha8": "aaaaaaaa"}, [], "11111111", when=when)
        self.assertEqual(entry["site"]["sha8"], "aaaaaaaa")
        self.assertEqual(entry["site"]["label"], "aaaaaaaa-20261110-1534")
        self.assertEqual(entry["site"]["at"], "2026-11-10T15:34:00Z")
        self.assertEqual(entry["modelSha8"], "11111111")

    def test_a_site_that_did_not_change_keeps_its_label(self):
        when = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        site = {"sha256": "a" * 64, "sha8": "aaaaaaaa"}
        first = vrs.record(self.paths, site, [], "11111111", when=when)
        later = when + timedelta(hours=3)
        second = vrs.record(self.paths, site, [], "22222222", when=later)
        self.assertEqual(first["site"]["label"], second["site"]["label"])
        self.assertEqual(second["site"]["at"], "2026-11-10T15:34:00Z")
        self.assertEqual(second["at"], "2026-11-10T18:34:00Z", "the build time is still the build time")

    def test_the_ledger_keeps_the_newest_fifty_entries(self):
        when = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        for step in range(vrs.LEDGER_CAP + 7):
            vrs.record(self.paths, {"sha256": f"{step:064x}", "sha8": f"{step:08x}"}, [], "11111111",
                       when=when + timedelta(minutes=step))
        entries = self.ledger()["entries"]
        self.assertEqual(len(entries), vrs.LEDGER_CAP)
        self.assertEqual(entries[-1]["site"]["sha8"], f"{vrs.LEDGER_CAP + 6:08x}")

    def test_the_document_versions_are_newest_first(self):
        when = datetime(2026, 11, 10, 15, 34, tzinfo=timezone.utc)
        for step, sha in enumerate(("3f2a1b9c", "9f8e7d6c")):
            vrs.record(self.paths, {"sha256": "a" * 64, "sha8": "aaaaaaaa"},
                       [{"p": "docs/en-US/BD.md", "sha8": sha}], "11111111", when=when + timedelta(minutes=step))
        versions = vrs.document_versions(self.ledger())
        rows = versions["docs/en-US/BD.md"]
        self.assertEqual([row["sha8"] for row in rows], ["9f8e7d6c", "3f2a1b9c"])

    def test_the_slice_archives_are_readable(self):
        history = self.paths.history
        history.mkdir(parents=True)
        (history / "10-structure.3f2a1b9c.atl").write_text("x", encoding="utf-8")
        (history / "10-structure.9f8e7d6c.atl").write_text("y", encoding="utf-8")
        (history / "not-an-archive.txt").write_text("z", encoding="utf-8")
        rows = vrs.slice_versions(self.paths)
        self.assertEqual([row["slice"] for row in rows], ["10-structure"])
        self.assertEqual(sorted(row["sha8"] for row in rows[0]["versions"]), ["3f2a1b9c", "9f8e7d6c"])


class BuildTests(OracleProject):
    def setUp(self):
        super().setUp()
        self.desk()

    def build(self, **kw):
        reply = tools.build(self.ws, self.name, **kw)
        self.assertEqual(reply["status"], "ok", reply)
        return reply

    @property
    def dist(self) -> Path:
        return build.dist_dir(self.paths)

    def test_the_build_info_carries_the_fingerprints(self):
        self.build()
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertIn("fingerprints", info)
        site = info["fingerprints"]["site"]
        self.assertRegex(site["sha8"], r"^[0-9a-f]{8}$")
        self.assertRegex(site["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(site["files"], 0)
        documents = info["fingerprints"]["documents"]
        paths = [row["p"] for row in documents]
        for expected in ("docs/en-US/REQ.md", "docs/en-US/BD.md", "docs/en-US/DD.md"):
            self.assertIn(expected, paths)

    def test_the_fingerprint_of_a_document_is_the_fingerprint_of_its_bytes(self):
        self.build()
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        rows = {row["p"]: row for row in info["fingerprints"]["documents"]}
        for path, row in rows.items():
            data = (self.dist / path).read_bytes()
            self.assertEqual(row["sha8"], vrs.sha8_of(data), path)
            self.assertEqual(row["b"], len(data), path)

    def test_the_build_writes_the_ledger(self):
        self.build()
        ledger = vrs.read_ledger(self.paths)
        self.assertEqual(len(ledger["entries"]), 1)
        entry = ledger["entries"][0]
        self.assertRegex(entry["site"]["label"], LABEL_RE)
        self.assertEqual(entry["site"]["sha8"], json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))["fingerprints"]["site"]["sha8"])
        self.assertIn("docs/en-US/BD.md", entry["docs"])
        self.assertRegex(entry["docs"]["docs/en-US/BD.md"]["label"], LABEL_RE)

    def test_the_output_carries_no_label_and_no_timestamp(self):
        self.build()
        for path in sorted(p for p in self.dist.rglob("*") if p.is_file()):
            rel = path.relative_to(self.dist).as_posix()
            if rel in ("oracle-sheet.md", "build-info.json"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(LABEL_IN_OUTPUT_RE.search(text), rel)
            self.assertIsNone(STAMP_RE.search(text), rel)

    def test_two_builds_of_one_model_have_the_same_site_fingerprint(self):
        self.build()
        first = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))["fingerprints"]["site"]["sha256"]
        self.build()
        second = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))["fingerprints"]["site"]["sha256"]
        self.assertEqual(first, second)

    def test_a_changed_document_gets_a_new_fingerprint(self):
        self.build()
        before = {row["p"]: row["sha8"] for row in doc_rows(self.dist)}
        self.put("10-structure", desk_files(("10-structure",
                                             "svc.loan | Loan service | Creates a loan and takes returns.",
                                             "svc.loan | Loan service | Creates a loan and takes it back."))["10-structure"])
        self.build()
        after = {row["p"]: row["sha8"] for row in doc_rows(self.dist)}
        self.assertNotEqual(before["docs/en-US/BD.md"], after["docs/en-US/BD.md"])

    def test_a_build_that_changes_no_document_keeps_the_labels(self):
        self.build()
        first = vrs.read_ledger(self.paths)["entries"][-1]
        self.build()
        second = vrs.read_ledger(self.paths)["entries"][-1]
        self.assertEqual(first["docs"], second["docs"], "an unchanged document keeps its label and its minute")
        self.assertEqual(first["site"]["label"], second["site"]["label"], "an unchanged site keeps its label too")


class VersionsToolTests(OracleProject):
    def setUp(self):
        super().setUp()
        self.desk()

    def call(self, **kw):
        reply = tools.versions(self.ws, self.name, **kw)
        self.assertEqual(reply["status"], "ok", reply)
        return reply

    @property
    def dist(self) -> Path:
        return build.dist_dir(self.paths)

    def test_the_history_view_lists_the_builds_and_the_documents(self):
        tools.build(self.ws, self.name)
        reply = self.call(view="history")
        self.assertEqual(reply["view"], "history")
        self.assertEqual(len(reply["builds"]), 1)
        self.assertRegex(reply["builds"][0]["label"], LABEL_RE)
        self.assertIn("docs/en-US/BD.md", reply["documents"])
        row = reply["documents"]["docs/en-US/BD.md"][0]
        self.assertRegex(row["label"], LABEL_RE)
        self.assertRegex(row["sha8"], r"^[0-9a-f]{8}$")

    def test_the_history_view_is_empty_before_the_first_build(self):
        reply = self.call(view="history")
        self.assertEqual(reply["builds"], [])
        self.assertEqual(reply["documents"], {})

    def test_the_scope_narrows_the_history(self):
        tools.build(self.ws, self.name)
        reply = self.call(view="history", scope="docs/en-US/BD.md")
        self.assertEqual(list(reply["documents"]), ["docs/en-US/BD.md"])

    def test_the_fingerprints_view_reports_the_built_site(self):
        tools.build(self.ws, self.name)
        reply = self.call(view="fingerprints")
        self.assertEqual(reply["view"], "fingerprints")
        self.assertRegex(reply["site"]["sha8"], r"^[0-9a-f]{8}$")
        paths = [row["p"] for row in reply["documents"]]
        self.assertIn("docs/en-US/BD.md", paths)
        for row in reply["documents"]:
            self.assertRegex(row["sha8"], r"^[0-9a-f]{8}$")

    def test_the_check_view_says_that_the_documents_are_at_their_recorded_version(self):
        tools.build(self.ws, self.name)
        reply = self.call(view="check")
        self.assertEqual(reply["view"], "check")
        self.assertTrue(reply["same"], reply)
        row = {r["p"]: r for r in reply["documents"]}["docs/en-US/BD.md"]
        self.assertTrue(row["same"])
        self.assertEqual(row["now"], row["was"])
        self.assertRegex(row["label"], LABEL_RE)

    def test_the_check_view_reports_a_changed_document_after_a_put(self):
        tools.build(self.ws, self.name)
        self.put("10-structure", desk_files(("10-structure",
                                             "svc.loan | Loan service | Creates a loan and takes returns.",
                                             "svc.loan | Loan service | Creates a loan and takes it back."))["10-structure"])
        reply = self.call(view="check")
        self.assertFalse(reply["same"], reply)
        rows = {r["p"]: r for r in reply["documents"]}
        self.assertFalse(rows["docs/en-US/BD.md"]["same"])
        self.assertNotEqual(rows["docs/en-US/BD.md"]["now"], rows["docs/en-US/BD.md"]["was"])
        self.assertRegex(rows["docs/en-US/BD.md"]["label"], LABEL_RE)

    def test_the_check_view_before_the_first_build_says_nothing_is_recorded(self):
        reply = self.call(view="check")
        self.assertFalse(reply["same"])
        row = {r["p"]: r for r in reply["documents"]}["docs/en-US/BD.md"]
        self.assertEqual(row["was"], "")
        self.assertFalse(row["same"])

    def test_the_check_view_writes_nothing(self):
        tools.build(self.ws, self.name)
        before = sorted(p.name for p in self.paths.dot.iterdir())
        self.call(view="check")
        self.assertEqual(before, sorted(p.name for p in self.paths.dot.iterdir()))

    def test_an_unknown_view_is_refused(self):
        reply = tools.versions(self.ws, self.name, view="nonsense")
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertEqual(reply["code"], tools.E_ARG)


if __name__ == "__main__":
    unittest.main()
