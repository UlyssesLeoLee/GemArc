"""``build`` and ``verify`` on real projects (design 4.1, 9.3 and 7.1; M4 acceptance (a) and (g)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_build_project*.py"

* ``DeskBuildTests``: the lending desk through ``tools.build`` / the CLI: files, reply size, determinism, owned subtrees, refusals.
* ``OracleFirstTests``: a case that is not sealed (no expectation, or revised after a reveal) puts NO verdict, trace, sequence or
  step into the page and the 4D / 5D data are empty (acceptance (g)); sealed cases do.
* ``ReferenceBuildTests``: one reference-scale sealed project (169 nodes, 23 cases, 34 pairs): size, breakdown, byte-identical rebuild.
No browser here (``test_gate_html.py``). Synthetic id families only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import build, cli, oracle, tools
from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas import project as pj

from . import synth
from .test_oracle import ROW, expect_patch
from .test_oracle_support import OracleProject, consistent, slice_texts

CASE_KEY = "case:CS-BORROW-OK"


def dist_tree(dist: Path) -> dict:
    """``{relative path: sha256}`` of everything the build owns (``.verify`` and ``oracle-sheet.md`` are not part of a build)."""
    out = {}
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        rel = path.relative_to(dist).as_posix()
        if rel.startswith(".verify/") or rel == "oracle-sheet.md":
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def page_data(dist: Path) -> dict:
    return H.extract_data((dist / "index.html").read_text(encoding="utf-8"))


class BuildProject(OracleProject):
    def build(self, **kw):
        reply = tools.build(self.ws, self.name, **kw)
        return reply

    @property
    def dist(self) -> Path:
        return build.dist_dir(self.paths)

    def ok(self, **kw) -> dict:
        reply = self.build(**kw)
        self.assertEqual(reply["status"], "ok", reply)
        return reply


class DeskBuildTests(BuildProject):
    def test_build_writes_the_page_the_info_and_the_documents(self):
        self.desk()
        reply = self.ok()
        self.assertLessEqual(tools.reply_bytes(reply), tools.BUILD_CAP)
        page = self.dist / "index.html"
        self.assertTrue(page.is_file())
        raw = page.read_bytes()
        self.assertEqual(reply["page"], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertNotIn(b"\r", raw)
        self.assertEqual(reply["checks"], [])
        self.assertEqual(reply["gate"], "NOT-RUN")
        self.assertIn("run verify", " ".join(reply["next"]))
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info["schema"], "gemarc-atlas-build/1")
        self.assertEqual(info["page"], reply["page"])
        self.assertEqual(set(info["parts"]), {"data", "runtime", "self-test", "style", "skeleton"})
        self.assertEqual(sum(info["parts"].values()), len(raw))
        text = json.dumps(info)
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:", text))
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), text)
        files = sorted(p.relative_to(self.dist).as_posix() for p in self.dist.rglob("*") if p.is_file())
        for expected in ("build-info.json", "index.html", "docs/en-US/REQ.md", "docs/en-US/BD.md", "docs/en-US/DD.md"):
            self.assertIn(expected, files)
        self.assertTrue(any(f.startswith("diagrams/") and f.endswith(".mmd") for f in files))
        self.assertTrue(any(f.startswith("prompts/") for f in files))
        owned = [f for f in files if f.startswith(("docs/", "diagrams/", "prompts/"))]
        self.assertGreater(info["docs"]["files"], 6)
        self.assertLessEqual(info["docs"]["files"], len(owned), "the run diagrams of sealed cases are written next to the structure diagrams")

    def test_docs_false_writes_only_the_page_and_the_info(self):
        self.desk()
        reply = self.ok(docs=False)
        self.assertIsNone(reply["docs"])
        files = sorted(p.relative_to(self.dist).as_posix() for p in self.dist.rglob("*") if p.is_file())
        self.assertEqual(files, ["build-info.json", "index.html"])

    def test_the_page_carries_the_sealed_runs_of_the_desk_and_matching_counts(self):
        self.desk()
        self.ok()
        data = page_data(self.dist)
        self.assertEqual(list(data["runs"]["baselines"]), ["CS-BORROW-OK"])
        run = data["runs"]["baselines"]["CS-BORROW-OK"]
        self.assertEqual(run["status"], "OK")
        self.assertEqual(len(run["trace"]), 6)
        self.assertEqual([p["interventionId"] for p in data["runs"]["pairs"]], ["IV-STOCK-DOWN"])
        self.assertEqual(data["meta"]["counts"]["sealedCases"], 1)
        self.assertEqual(data["meta"]["counts"]["sealedPairs"], 1)
        self.assertEqual(data["calibrationHooks"]["traceLengths"], [{"caseId": "CS-BORROW-OK", "interventionId": None, "length": 6},
                                                                    {"caseId": "CS-BORROW-OK", "interventionId": "IV-STOCK-DOWN", "length": len(data["runs"]["pairs"][0]["B"]["trace"])}])
        self.assertEqual(data["meta"]["qa"]["oracle"]["sealed"], 2)
        self.assertEqual(data["meta"]["qa"]["oracle"]["sheet"], "oracle-sheet.md")
        self.assertEqual(len(data["calibrationHooks"]["docSelections"]), 20)
        self.assertEqual(sorted(data), sorted(H.BUNDLE_KEYS))
        types = {u["type"] for u in data["diagrams"]}
        self.assertTrue({"sequence", "staterun"} <= types, "sealed runs also give the run diagrams")

    def test_hostile_text_that_the_slice_format_accepts_reaches_the_page_safely(self):
        hostile = "Loan </script><script>throw 1</script><!-- service"
        self.desk(("10-structure", "Loan service | Creates a loan and takes returns.", hostile + " | Creates </style> $& \\g<0> a loan."),
                  ("10-structure", "creates the loan row", "<!-- --> fetch(x) import(y)"))
        self.ok()
        text = (self.dist / "index.html").read_text(encoding="utf-8")
        self.assertEqual(text.lower().count("</script"), 3)
        self.assertNotIn("<!--", text)
        data = H.extract_data(text)
        self.assertEqual([n["name"] for n in data["model"]["nodes"] if n["id"] == "svc.loan"], [hostile])
        self.assertIn("fetch(x) import(y)", [e["topic"] for e in data["model"]["evidences"] if e["evidenceId"] == "EVD-1"][0])
        self.assertEqual(json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))["checks"], [])

    def test_two_builds_of_one_project_are_byte_identical(self):
        self.desk()
        self.ok()
        first = dist_tree(self.dist)
        self.ok()
        self.assertEqual(dist_tree(self.dist), first)

    def test_a_rebuild_replaces_what_it_owns_and_keeps_the_sheet_and_the_verify_directory(self):
        self.desk()
        self.ok()
        sheet = tools.oracle_sheet(self.ws, self.name)
        self.assertEqual(sheet["status"], "ok")
        (self.dist / ".verify").mkdir(exist_ok=True)
        (self.dist / ".verify" / "keep.txt").write_text("x", encoding="utf-8")
        (self.dist / "docs" / "en-US" / "stale.md").write_text("old", encoding="utf-8")
        (self.dist / "prompts" / "stale.md").write_text("old", encoding="utf-8")
        self.ok()
        self.assertFalse((self.dist / "docs" / "en-US" / "stale.md").exists())
        self.assertFalse((self.dist / "prompts" / "stale.md").exists())
        self.assertTrue((self.dist / "oracle-sheet.md").is_file())
        self.assertTrue((self.dist / ".verify" / "keep.txt").is_file())

    def test_a_model_with_error_findings_is_refused_and_nothing_is_written(self):
        self.desk()
        reply = self.put("30-extra", "#atlas 1\n## nodes: id | name | sub | opts\nsvc.x | X | y | room=rm.nope kind=Module\n")
        self.assertEqual(reply["status"], "committed", "a forward reference is pending in slice mode")
        self.assertIs(tools.check(self.ws, self.name, mode="full")["clean"], False)
        refused = self.build()
        self.assertEqual(refused["status"], "invalid", refused)
        self.assertEqual(refused["code"], build.E_REFUSED)
        self.assertEqual(refused["diagnostics"][0]["id"], "RF-001")
        self.assertFalse((self.dist / "index.html").exists())

    def test_a_case_without_any_expectation_is_refused_by_the_coverage_floor_not_embedded(self):
        self.desk(("20-scenarios", ROW + "\n", ""))
        refused = self.build()
        self.assertEqual((refused["status"], refused["code"]), ("invalid", build.E_REFUSED), refused)
        self.assertEqual(refused["diagnostics"][0]["id"], "OR-003")
        self.assertFalse((self.dist / "index.html").exists())

    def test_a_project_that_does_not_exist_is_refused(self):
        self.init()
        reply = tools.build(self.ws, "no-such-project")
        self.assertIn(reply["status"], ("invalid", "error"))
        self.assertFalse(self.dist.joinpath("index.html").exists())

    def test_build_needs_write_permission(self):
        self.desk()
        with mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "0"}):
            reply = self.build()
        self.assertEqual(reply["status"], "invalid")
        self.assertFalse((self.dist / "index.html").exists())

    def test_the_gate_argument_is_validated(self):
        self.desk()
        reply = self.build(gate="full")
        self.assertEqual(reply["status"], "invalid")
        self.assertEqual(reply["code"], build.E_ARG)

    def test_release_without_the_browser_gate_is_not_a_release_but_the_page_is_written(self):
        self.desk()
        reply = self.build(release=True)
        self.assertEqual(reply["status"], "invalid", reply)
        self.assertEqual(reply["code"], build.E_RELEASE)
        self.assertTrue(any("browser gate was not run" in b for b in reply["blockers"]) or reply["release"]["ok"] is False)
        self.assertTrue((self.dist / "index.html").is_file())
        self.assertIn("bite", self.entry(CASE_KEY), "a release build runs the mutants the oracle gate needs")
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertTrue(info["release"]["requested"])
        self.assertFalse(info["release"]["ok"])
        self.assertTrue(any(b["kind"] == "gate" for b in info["release"]["blockers"]))

    def test_verify_without_a_built_page_and_with_a_stale_page_is_refused_without_a_browser(self):
        self.desk()
        reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_NO_PAGE))
        self.ok()
        self.put("20-scenarios", "#atlas 1\n## tests: id | title\nTS-2 | A second test\n", mode="patch")
        reply = tools.verify(self.ws, self.name)
        self.assertEqual((reply["status"], reply["code"]), ("invalid", build.E_NO_PAGE), reply)
        self.assertIn("current model", reply["message"])

    def test_verify_validates_the_widths(self):
        self.desk()
        self.ok()
        for widths in ([], [100], ["x"], [1024] * 7):
            self.assertEqual(tools.verify(self.ws, self.name, widths)["status"], "invalid", widths)

    def test_the_command_line_builds_and_prints_the_breakdown(self):
        self.desk()
        reply, code = cli.run(["--root", str(self.root), "build", "desk", "--human"])
        self.assertEqual((reply["status"], code), ("ok", 0), reply)
        text = cli._human(reply)
        self.assertIn("index.html", text)
        self.assertIn("parts:", text)
        self.assertIn("fragments", text)
        reply, code = cli.run(["--root", str(self.root), "build", "desk", "--release"])
        self.assertEqual((reply["status"], code), ("invalid", 1))
        reply, code = cli.run(["--root", str(self.root), "verify", "desk", "--widths", "1024"])
        self.assertIn(reply["status"], ("ok", "invalid"))

    def test_the_repository_is_not_touched_by_a_build(self):
        self.desk()
        before = {p: p.stat().st_mtime_ns for p in (Path(__file__).resolve().parents[2] / "src" / "gemarc_mcp" / "atlas" / "viewer" / "dist").iterdir()}
        self.ok()
        after = {p: p.stat().st_mtime_ns for p in before}
        self.assertEqual(before, after)


class OracleFirstTests(BuildProject):
    """Acceptance (g): nothing the oracle has not sealed reaches the page."""

    def raw_data_text(self) -> str:
        page = (self.dist / "index.html").read_text(encoding="utf-8")
        return H.parse_skeleton(page).scripts[1]

    def assert_empty_runs(self):
        data = page_data(self.dist)
        self.assertEqual(data["runs"], {"baselines": {}, "pairs": []})
        self.assertEqual(data["calibrationHooks"]["traceLengths"], [])
        self.assertFalse([u for u in data["diagrams"] if u["type"] in ("sequence", "staterun")])
        text = self.raw_data_text()
        for needle in ("stateChanges", "sequenceDiagram", "CS-BORROW-OK#", "\"trace\":["):
            self.assertNotIn(needle, text, needle)
        self.assertEqual(data["meta"]["counts"]["sealedCases"], 0)
        self.assertEqual(data["meta"]["counts"]["sealedPairs"], 0)

    def test_a_revised_case_also_keeps_the_documents_and_diagrams_free_of_its_trace(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="trace")
        self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")
        self.ok()
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info["withheld"]["cases"], ["CS-BORROW-OK"])
        self.assertEqual(info["withheld"]["interventions"], ["IV-STOCK-DOWN"], "its only case has no sealed baseline: no pair to show")
        for doc in sorted((self.dist / "docs").rglob("*.md")) + sorted((self.dist / "diagrams").rglob("*.mmd")) + sorted((self.dist / "prompts").rglob("*.md")):
            self.assertNotIn("sequenceDiagram", doc.read_text(encoding="utf-8"), doc.name)
        self.assertFalse([p for p in (self.dist / "diagrams").iterdir() if p.name.startswith(("sequence", "staterun"))])

    def test_the_state_before_any_seal_exists_is_the_empty_state_in_the_pure_path(self):
        model = consistent(synth.generate("small", 3))
        rendered = build.render(model)                      # no provider reply: nothing sealed
        data = H.extract_data(rendered.html)
        self.assertEqual(data["runs"], {"baselines": {}, "pairs": []})
        text = H.parse_skeleton(rendered.html).scripts[1]
        for needle in ("stateChanges", "sequenceDiagram", "\"trace\":["):
            self.assertNotIn(needle, text)

    def test_a_revised_case_loses_its_run_until_the_user_confirms_it(self):
        self.desk()
        self.simulate(case="CS-BORROW-OK", detail="trace")               # a reveal, then an edit: the expectation is revised
        self.put("20-scenarios", expect_patch("usr.member,svc.loan"), mode="patch")
        self.assertEqual(self.states()[CASE_KEY], "revised")
        self.ok()
        self.assert_empty_runs()
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info["oracle"]["revised"], 1)
        self.assertIn("CS-BORROW-OK", info["withheld"]["cases"])

    def test_mixed_projects_embed_exactly_the_sealed_cases(self):
        model = consistent(synth.generate("small", 3))
        first, second = model["cases"][0]["caseId"], model["cases"][1]["caseId"]
        self.init(name="mixed", repos=model["meta"]["repos"])
        slices = slice_texts(model)
        for name, text in slices.items():
            self.assertEqual(self.put(name, text)["status"], "committed", name)
        expect_slice = next(name for name, text in slices.items() if any(line.startswith(second + " |") for line in text.split("## expect:")[-1].splitlines()) and "## expect:" in text)
        self.assertEqual(self.states()[f"case:{second}"], "sealed")
        self.simulate(case=second, detail="trace")                       # a reveal of the second case ...
        terminal = list(model["cases"][1]["expectedTerminalState"])
        row = f"{second} | {terminal[0]}=Failed," + ",".join(terminal[1:]) + " | by=author"
        reply = self.put(expect_slice, f"#atlas 1\n## expect: case | nodes | opts\n{row}\n", mode="patch")
        self.assertEqual(reply["status"], "committed", reply)             # ... then an edit: revised
        self.assertEqual(self.states()[f"case:{second}"], "revised")
        self.ok()
        data = page_data(self.dist)
        self.assertIn(first, data["runs"]["baselines"])
        self.assertNotIn(second, data["runs"]["baselines"])
        text = self.raw_data_text()
        self.assertNotIn(second + "#", text, "no event id of the unsealed case")
        self.assertIn(first + "#", text)
        for pair in data["runs"]["pairs"]:
            self.assertNotEqual(pair["caseId"], second)
        self.assertNotIn(second, [t["caseId"] for t in data["calibrationHooks"]["traceLengths"]])
        for unit in data["diagrams"]:
            if unit["type"] in ("sequence", "staterun"):
                self.assertNotIn(second, unit["covers"]["caseIds"])


class ReferenceBuildTests(unittest.TestCase):
    """One sealed reference-scale project, built once (169 nodes, 374 edges, 23 cases, 21 interventions)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.ws = pj.get_workspace(Path(cls._tmp.name))
        cls.model = consistent(synth.generate("reference", 3))
        assert tools.init(cls.ws, "atlas", "Reference scale", "asis", ["en-US"], repos=cls.model["meta"]["repos"])["status"] == "created"
        for name, text in slice_texts(cls.model).items():
            assert tools.put(cls.ws, "atlas", name, text)["status"] == "committed", name
        cls.paths = pj.project_paths(cls.ws, "atlas")
        cls.dist = build.dist_dir(cls.paths)
        cls.reply = tools.build(cls.ws, "atlas")
        cls.first = dist_tree(cls.dist)

    def test_the_build_succeeds_and_the_page_is_within_two_megabytes(self):
        self.assertEqual(self.reply["status"], "ok", self.reply)
        size = self.reply["page"]["bytes"]
        print(f"\n[reference-scale page] {size} bytes; parts {self.reply['parts']}; data {self.reply['data']}")
        # design M4 (a): at most 2 MB (2,000,000 bytes). Measured on seeds 1, 2, 3, 4, 6, 7, 8 of the generator: 1,865,088 to 1,966,866 (the
        # largest, seed 7, is built in LargestSeedTests below); seed 5 of the generator does not put (see notes/view-b1.md).
        self.assertLessEqual(size, 2_000_000)
        self.assertLessEqual(tools.reply_bytes(self.reply), tools.BUILD_CAP)
        self.assertEqual(self.reply["checks"], [], "BD-001..BD-007 are clean (no WARN and no ERROR)")

    def test_the_sealed_runs_of_the_whole_atlas_are_embedded(self):
        data = page_data(self.dist)
        self.assertEqual(len(data["runs"]["baselines"]), 23)
        self.assertGreaterEqual(len(data["runs"]["pairs"]), 20)
        self.assertEqual(self.reply["withheld"], {"cases": 0, "interventions": 0})
        self.assertEqual(data["meta"]["counts"]["cases"], 23)
        self.assertEqual(len(data["calibrationHooks"]["traceLengths"]), 23 + len(data["runs"]["pairs"]))

    def test_the_breakdown_names_every_part(self):
        info = json.loads((self.dist / "build-info.json").read_text(encoding="utf-8"))
        self.assertGreater(info["data"]["fragments"], 100_000)
        self.assertGreater(info["data"]["runs"], 50_000)
        self.assertEqual(sum(info["parts"].values()), info["page"]["bytes"])

    def test_a_second_build_is_byte_identical(self):
        again = tools.build(self.ws, "atlas")
        self.assertEqual(again["status"], "ok")
        self.assertEqual(dist_tree(self.dist), self.first)

    def test_the_embedded_runs_are_the_engine_results_of_the_sealed_items(self):
        sealed = oracle.sealed_runs(self.ws, "atlas")
        data = page_data(self.dist)
        for cid, entry in sealed["baselines"].items():
            run = data["runs"]["baselines"][cid]
            self.assertEqual(len(run["trace"]), len(entry["run"]["trace"]))
            self.assertEqual([e["stateChanges"] for e in run["trace"] if "stateChanges" in e], [e["stateChanges"] for e in entry["run"]["trace"] if e["stateChanges"]])
            self.assertEqual(run["hash"], entry["run"]["hash"])


class LargestSeedTests(unittest.TestCase):
    """The largest of the measured seeds (7) stays within the 2,000,000 byte budget of design M4 (a)."""

    def test_seed_seven_page_is_within_two_megabytes(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"}):
            ws = pj.get_workspace(Path(tmp))
            model = consistent(synth.generate("reference", 7))
            self.assertEqual(tools.init(ws, "atlas", "Reference scale", "asis", ["en-US"], repos=model["meta"]["repos"])["status"], "created")
            for name, text in slice_texts(model).items():
                self.assertEqual(tools.put(ws, "atlas", name, text)["status"], "committed", name)
            reply = tools.build(ws, "atlas", docs=False)
        self.assertEqual(reply["status"], "ok", reply)
        print(f"[seed 7 page] {reply['page']['bytes']} bytes; parts {reply['parts']}")
        self.assertLessEqual(reply["page"]["bytes"], 2_000_000)


if __name__ == "__main__":
    unittest.main()
