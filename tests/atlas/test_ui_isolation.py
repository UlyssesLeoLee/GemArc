"""Separation of the interface family from the 5D side (T-S5 / AC-07), the 5D brief (AC-09) and the read-only path (AC-14).

A real project: the synthetic lending desk is authored, built and reviewed (so ``oracle.json``, ``puts.json``, ``dist/index.html`` and
``review.json`` all exist), then the interface store is exercised in every way (committed put, red put, patch, verdict put, reads). Every
file outside ``atlas/ui`` and ``atlas/.atlas/ui`` must be byte for byte and ``mtime_ns`` for ``mtime_ns`` what it was; the only new files
are inside the two interface directories. The other direction: a change on the 5D side leaves the interface files and ``ui_sha8`` alone.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_isolation.py"
"""
from __future__ import annotations

import hashlib
import json
import unittest

from gemarc_mcp.atlas import brief
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas import slices as sl
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.protoui import store

from . import ui_synth
from .test_ui_store import LOGIN, UiProject, verdict_for_model

POLICY = "#atlas 1\n## policies: id | text | opts\nPL-NFR-1 | Borrowing completes within two seconds. | src=RQ-1 kind=nfr\n"

#: The brief at HEAD 81ab5b5, before the ``ui`` section existed: sha256 prefix (16 hex) of the UTF-8 text per section and language (AC-09).
BRIEF_AT_HEAD = {
    ("all", "zh-CN"): "e266426931250fed", ("all", "en-US"): "578fcedb707e6acf", ("format", "zh-CN"): "7c8761ce64357689",
    ("format", "en-US"): "baa0de63e8c814cb", ("vocab", "zh-CN"): "7b4b6cbb1cdc5151", ("vocab", "en-US"): "add641e6e3a7bc79",
    ("derived", "zh-CN"): "44ddff7cd8c330a0", ("derived", "en-US"): "771c4061de149a51", ("slices", "zh-CN"): "2f6297af30621a4e",
    ("slices", "en-US"): "d951ef753a914164", ("oracle", "zh-CN"): "055633a9cf7ff2d1", ("oracle", "en-US"): "8532fcb0a36a8f47",
    ("docs", "zh-CN"): "9ac0c2caa0e59a5f", ("docs", "en-US"): "a429dec6de8ab05b", ("economics", "zh-CN"): "a615a986c0323f08",
    ("economics", "en-US"): "b5049f04d769f664",
}
INTERFACE_DIRS = ("desk/atlas/ui/", "desk/atlas/.atlas/ui/")


class BuiltProject(UiProject):
    """The desk, authored, built and reviewed: every 5D state file exists."""

    def setUp(self):
        super().setUp()
        self.desk()
        self.assertEqual(tools.put(self.ws, "desk", "30-policy", POLICY)["status"], "committed")
        self.assertEqual(tools.build(self.ws, "desk")["status"], "ok")
        tools.review(self.ws, "desk", "report", require_report=False)
        self.assertEqual(tools.review(self.ws, "desk", "record", notes="reviewed the sample", require_report=False)["status"], "recorded")

    def snapshot(self, outside_only: bool = True) -> dict:
        out = {}
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            if outside_only and rel.startswith(INTERFACE_DIRS):
                continue
            stat = p.stat()
            out[rel] = (hashlib.sha256(p.read_bytes()).hexdigest(), stat.st_mtime_ns, stat.st_size)
        return out

    def five_state(self) -> dict:
        snap = st.load_snapshot(self.paths, self.cfg)
        return {"model_sha": am.model_sha(snap.model), "status": tools.status(self.ws, "desk"), "index": tools.index(self.ws, "desk"),
                "check": tools.check(self.ws, "desk", mode="full")}

    def exercise_the_interface(self) -> None:
        five = self.five()
        sources = ui_synth.sample12_sources()
        for name, text in sources.items():
            stem = name[:-4]
            self.assertEqual(self.put(stem, text, five=five).status, "committed", name)
        own = LOGIN.replace("SCR-", "SCY-")      # the sample defines SCR-LOGIN and SCR-HOME already: this slice has screens of its own
        self.assertTrue(self.put("ui-50-bad", own.replace("kind=input parent=form bind=email", "kind=inputt parent=form bind=email")).red)
        self.put("ui-50-bad", "## widgets: local | label | opts\n@set screen=SCY-LOGIN\nemail | 邮箱 | kind=input parent=form bind=email\n", mode="patch", five=five)
        self.put("ui-60-extra", LOGIN.replace("SCR-", "SCX-"), five=five)
        verdict = verdict_for_model(store.load_state(self.up, self.cfg.meta(), five).model)      # valid for the prototype as it is now (UV-001 .. UV-008)
        self.assertEqual(self.put("ui-verdict-fe", verdict, five=five).status, "committed")
        store.load_state(self.up, self.cfg.meta(), five)
        store.check_store(self.up, self.cfg.meta(), five=five)
        store.check_store(self.up, self.cfg.meta(), mode="slice", incoming=["ui-50-bad.atl"])


class SeparationTests(BuiltProject):
    def test_the_fixture_has_every_5d_state_file(self):
        files = self.snapshot()
        for name in ("desk/atlas/.atlas/oracle.json", "desk/atlas/.atlas/puts.json", "desk/atlas/.atlas/review.json", "desk/atlas/dist/index.html"):
            self.assertIn(name, files)

    def test_interface_puts_change_no_file_outside_the_interface_directories_not_even_its_mtime(self):
        before, five_before = self.snapshot(), self.five_state()
        self.exercise_the_interface()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.five_state(), five_before)

    def test_the_named_5d_state_is_unchanged_bytes_and_mtime(self):
        paths = [self.paths.dot / "oracle.json", self.paths.dot / "puts.json", self.paths.dot / "review.json", self.paths.atlas / "dist" / "index.html"]
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        model_sha = am.model_sha(st.load_snapshot(self.paths, self.cfg).model)
        review_valid = st.review_is_valid_record(self.paths)
        self.exercise_the_interface()
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}, before)
        self.assertEqual(am.model_sha(st.load_snapshot(self.paths, self.cfg).model), model_sha)
        self.assertEqual(st.review_is_valid_record(self.paths), review_valid)
        self.assertTrue(st.review_is_current(st.read_review(self.paths), st.load_snapshot(self.paths, self.cfg).model))

    def test_the_only_new_files_are_inside_the_two_interface_directories(self):
        before = set(self.snapshot(outside_only=False))
        self.exercise_the_interface()
        new = set(self.snapshot(outside_only=False)) - before
        self.assertTrue(new)
        self.assertTrue(all(path.startswith(INTERFACE_DIRS) for path in new), sorted(p for p in new if not p.startswith(INTERFACE_DIRS)))
        self.assertEqual(set(self.snapshot(outside_only=False)) - new, before)

    def test_the_5d_counters_and_the_interface_counters_are_separate_files(self):
        five_puts = json.loads((self.paths.dot / "puts.json").read_text(encoding="utf-8"))
        self.exercise_the_interface()
        self.assertEqual(json.loads((self.paths.dot / "puts.json").read_text(encoding="utf-8")), five_puts)
        counters = store.read_counters(self.up)
        self.assertEqual((counters["puts"], counters["verdictPuts"]), (7, 1))
        self.assertEqual(counters["red"], {})

    def test_a_5d_change_leaves_the_interface_files_and_the_hash_alone(self):
        self.exercise_the_interface()
        files = {s.path.name: (s.path.read_bytes(), s.path.stat().st_mtime_ns) for s in store.list_slices(self.up)}
        state = store.load_state(self.up, self.cfg.meta())
        tools.put(self.ws, "desk", "31-policy", POLICY.replace("PL-NFR-1", "PL-NFR-2").replace("two seconds", "three seconds"))
        tools.build(self.ws, "desk")
        self.assertEqual({s.path.name: (s.path.read_bytes(), s.path.stat().st_mtime_ns) for s in store.list_slices(self.up)}, files)
        after = store.load_state(self.up, self.cfg.meta(), self.five())
        self.assertEqual((after.ui_sha8, after.verdict_sha8), (state.ui_sha8, state.verdict_sha8))

    def test_a_5d_slice_that_happens_to_be_named_like_an_interface_slice_is_still_a_5d_slice(self):
        self.assertEqual(tools.put(self.ws, "desk", "ui-layer", "#atlas 1\n## tests: id | title\nTS-9 | a layer test\n")["status"], "committed")
        self.assertFalse(store.ui_present(self.up))
        self.assertIn("ui-layer", [f.name for f in sl.list_slices(self.paths)])
        self.assertEqual(store.list_slices(self.up), [])

    def test_the_5d_commit_hooks_are_never_called_for_an_interface_commit(self):
        called = []
        hook = lambda paths, outcome: called.append(outcome.slice)
        sl.register_commit_hook(hook)
        self.addCleanup(lambda: sl.COMMIT_HOOKS.remove(hook))
        self.exercise_the_interface()
        self.assertEqual(called, [])
        tools.put(self.ws, "desk", "32-policy", POLICY.replace("PL-NFR-1", "PL-NFR-3"))
        self.assertEqual(called, ["32-policy"])

    def test_a_project_without_an_interface_builds_no_interface_artifact(self):
        dist = sorted(str(p.relative_to(self.paths.atlas / "dist")).replace("\\", "/") for p in (self.paths.atlas / "dist").rglob("*") if p.is_file())
        self.assertFalse([d for d in dist if d.split("/")[0] in ("screens", "site", "read", "ui")])
        self.assertFalse((self.paths.atlas / "ui").exists())
        self.assertFalse((self.paths.dot / "ui").exists())


class BriefTests(unittest.TestCase):
    """AC-09: the 5D work order is byte for byte what it was; the new section is within its cap in both languages."""

    def test_every_section_that_existed_at_head_renders_to_the_same_bytes(self):
        for (section, lang), prefix in BRIEF_AT_HEAD.items():
            text = brief.render(section, lang)
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], prefix, (section, lang))

    def test_the_ui_section_exists_and_fits_the_section_cap_in_both_languages(self):
        self.assertIn("ui", brief.SECTIONS)
        self.assertEqual(brief.SECTIONS[:7], ("format", "vocab", "derived", "slices", "oracle", "docs", "economics"))
        for lang in brief.LANGUAGES:
            text = brief.render("ui", lang)
            self.assertLessEqual(len(text.encode("utf-8")), brief.SECTION_CAP, lang)
            self.assertGreater(len(text.encode("utf-8")), 1500, lang)
            self.assertNotIn("{{", text)
            for header in ("## screens: id | title | purpose | opts", "## widgets: local | label | opts", "## nav: from | to | label | opts"):
                self.assertIn(header, text)
        self.assertEqual(brief.render("ui", "ja-JP"), brief.render("ui", "en-US"))

    def test_the_ui_section_is_the_same_text_through_the_tool_function(self):
        for lang in brief.LANGUAGES:
            reply = tools.brief("ui", lang)
            self.assertEqual(reply["text"], brief.render("ui", lang))
            self.assertLessEqual(reply["bytes"], brief.SECTION_CAP)

    def test_the_headers_in_the_ui_text_are_the_section_table_of_the_family(self):
        from gemarc_mcp.atlas.protoui import rows

        text = brief.render("ui", "en-US")
        for spec in rows.SECTIONS.values():
            self.assertIn(spec.header + "   opts: " + " ".join(spec.option_names), text)
        self.assertNotIn("## nodes:", text)

    def test_the_ui_text_names_every_kind_and_the_5d_work_order_names_none_of_them(self):
        from gemarc_mcp.atlas.protoui import vocab

        text = brief.render("ui", "zh-CN")
        for kind in vocab.WIDGET_KINDS:
            self.assertIn(kind, text)
        self.assertNotIn("ui-verdict", brief.render("all", "en-US"))
        self.assertNotIn("#atlas-ui", brief.render("all", "zh-CN"))

    def test_an_unknown_placeholder_is_still_an_error(self):
        with self.assertRaises(brief.BriefError):
            brief._PLACEHOLDER.sub(brief._fill, "{{nothing}}")
        with self.assertRaises(brief.BriefError):
            brief._PLACEHOLDER.sub(brief._fill, "{{uiids:nothing}}")


if __name__ == "__main__":
    unittest.main()
