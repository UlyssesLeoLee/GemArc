"""Hardening of the document lane after the verification round.

* a lone UTF-16 surrogate in any free-text field is replaced by U+FFFD at the input boundary (``policy.Index``): documents,
  diagrams, prompts, the outline, the viewer payload and the file writer keep working and every written file is UTF-8;
* the failure result of the docset builder has the full shape, so ``assemble``, the outline and the viewer payload never
  see a missing ``frame`` or ``coverage``;
* unhashable values (a dict or list where an id or a relation belongs) never reach the last-resort ``099`` handlers;
* a literal that means "missing" in the author's own text is reported (DC-L-001) by diagrams and prompts too, and a NaN
  inside a container prints as a marker;
* a Mermaid marker slot never holds a raw ``#``;
* ``docbuild.embed_json`` makes the viewer payload safe inside an inline script element;
* the building overview diagram is linked through the edges it covers, and the coverage report names unreached diagrams.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_derive_hardening*.py"
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.derive import assemble as A
from gemarc_mcp.atlas.derive import diagrams as D
from gemarc_mcp.atlas.derive import docbuild as B
from gemarc_mcp.atlas.derive import fragments as F
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas.derive import lint as L
from gemarc_mcp.atlas.derive import prompts as R

from . import synth
from .test_derive_deletion_matrix import build_base

SURROGATE_PATHS = ("nodes.1.name", "nodes.0.sub", "meta.title", "evidences.0.anchor", "evidences.0.filePath", "evidences.0.topic",
                   "machines.0.errorCode", "policies.2.text", "tbds.0.decision", "tbds.1.title", "space.buildings.1.name",
                   "space.rooms.1.name", "nodes.0.props.trust", "cases.0.events.2.from")


def set_path(model, path, value):
    holder = model
    parts = path.split(".")
    for part in parts[:-1]:
        holder = holder[int(part)] if isinstance(holder, list) else holder[part]
    last = parts[-1]
    if isinstance(holder, list):
        holder[int(last)] = value
    else:
        holder[last] = value


def internal(issues):
    return [i for i in issues if i["rule"].endswith("-099") or i["rule"].endswith("099")]


class SurrogateTests(unittest.TestCase):
    def test_sanitize_is_identity_without_surrogates_and_a_copy_with_them(self):
        clean = {"a": ["x", {"b": "y"}]}
        self.assertIs(T.sanitize(clean), clean)
        dirty = {"a\ud800": ["x\udfff", {"b": "y\ud801z"}]}
        out = T.sanitize(dirty)
        self.assertEqual(out, {"a�": ["x�", {"b": "y�z"}]})
        self.assertIn("\ud800", next(iter(dirty)))  # the input is untouched
        json.dumps(out, ensure_ascii=False).encode("utf-8")

    def test_sanitize_survives_cycles_and_depth(self):
        cyc: dict = {}
        cyc["self"] = cyc
        self.assertIs(T.sanitize(cyc), cyc)
        deep: list = []
        node = deep
        for _ in range(200):
            nxt: list = []
            node.append(nxt)
            node = nxt
        node.append("x\ud800")
        T.sanitize(deep)  # must not raise

    def test_every_free_text_field_is_survivable(self):
        for path in SURROGATE_PATHS:
            model = build_base()
            set_path(model, path, "ab\ud800cd")
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as tmp:
                    report = B.build_docs(model, tmp, ["en-US"])
                    for entry in report["files"]:
                        (Path(tmp) / entry["path"]).read_bytes().decode("utf-8")
                self.assertEqual(internal(report["issues"]), [])
                outline = json.loads(B.doc_outline_json(model, None, "DD", "en-US"))
                self.assertTrue(outline["ok"], outline)
                for level in T.LEVELS:
                    for chapter in (3, 4):
                        self.assertTrue(B.doc_outline(model, None, level, "en-US", chapter=chapter)["ok"])
                ds = F.build_docset(model, "en-US")
                self.assertEqual(internal(ds["issues"]), [])
                self.assertIn("coverage", ds)
                for ptype in ("implement", "DD"):
                    result = R.generate_prompt(model, ptype, None, "en-US")
                    self.assertTrue(result["ok"], (ptype, result["issues"]))
                    self.assertEqual(internal(result["issues"]), [])
                payload = B.viewer_payload(model, "en-US")
                B.embed_json(payload).encode("utf-8")

    def test_the_replacement_character_is_what_gets_printed(self):
        model = build_base()
        model["nodes"][1]["name"] = "ab\ud800cd"
        ds = B.get_bundle(model, "en-US", use_cache=False).docset
        text = A.assemble(ds, "DD", ds["fragments"]["elements"])
        self.assertIn("ab�cd", text)
        self.assertNotEqual(ds["fragments"]["modelSha8"], "unhashed")  # the stamp is computed on the sanitised model
        self.assertEqual(ds["fragments"]["modelSha8"], am.model_sha8(T.sanitize(model)))
        self.assertEqual(T.md_text("a\udc00b"), "a�b")

    def test_surrogates_in_caller_arguments_do_not_raise(self):
        model = build_base()
        self.assertFalse(B.doc_outline(model, "x\ud800", "DD", "en-US")["ok"])
        json.dumps(B.doc_outline(model, ["x\udc00"], "DD", "en-US"), ensure_ascii=False).encode("utf-8")
        result = R.generate_prompt(model, "implement", {"nodeIds": ["n\ud800"]}, "en-US")
        result["markdown"].encode("utf-8")
        json.dumps(result["issues"], ensure_ascii=False).encode("utf-8")

    def test_a_surrogate_in_a_dict_key_or_id_is_survivable(self):
        model = build_base()
        model["nodes"][0]["props"] = {"k\ud800": 1}
        model["cases"][0]["caseId"] = "CS-\ud800"
        B.build_docs(model, tempfile.mkdtemp(prefix="gemarc-hardening-"), ["en-US"])


class FailureShapeTests(unittest.TestCase):
    def setUp(self):
        B._cache.clear()
        self.addCleanup(B._cache.clear)

    def test_failed_builder_gives_the_full_shape(self):
        model = build_base()
        model["meta"]["title"] = "failure shape"
        with mock.patch.object(F, "_build", side_effect=RuntimeError("boom")):
            ds = F.build_docset(model, "en-US")
            self.assertEqual([i["rule"] for i in ds["issues"]], ["DC-D-099"])
            for key in ("schema", "fragments", "closures", "issues", "stats", "coverage"):
                self.assertIn(key, ds)
            self.assertEqual(sorted(ds["fragments"]["frame"]), ["BD", "DD", "REQ"])
            for level in T.LEVELS:
                self.assertEqual(len(ds["fragments"]["frame"][level]["chapters"]), T.CHAPTER_COUNT)
                text = A.assemble(ds, level, [])
                self.assertEqual([c["number"] for c in L.parse_chapters(text)], list(range(1, 13)))
                text.encode("utf-8")
            outline = B.doc_outline(model, None, "DD", "en-US")
            self.assertTrue(outline["ok"], outline)
            self.assertTrue(B.doc_outline(model, None, "BD", "en-US", chapter=2)["ok"])
            with tempfile.TemporaryDirectory() as tmp:
                report = B.build_docs(model, tmp, ["en-US", "ja-JP"])
                self.assertTrue(any(i["rule"] == "DC-D-099" for i in report["issues"]))
            payload = B.viewer_payload(model, "en-US")
            self.assertIn("docSelections", payload)
            B.embed_json(payload)


class UnhashableTests(unittest.TestCase):
    def test_dict_and_list_where_a_key_belongs(self):
        for value in ({"a": 1}, [1, {"b": 2}]):
            for path in ("edges.0.relation", "cases.0.caseId", "nodes.0.kind", "policies.0.scope", "edges.0.sourceId", "requirements.0.id"):
                model = build_base()
                set_path(model, path, copy.deepcopy(value))
                with self.subTest(path=path, value=type(value).__name__):
                    built = D.build_units(model, "en-US")
                    self.assertEqual(internal(built["issues"]), [])
                    self.assertEqual(internal(L.lint_diagrams(model, built["units"])), [])
                    self.assertEqual(internal(F.build_docset(model, "en-US", built["units"])["issues"]), [])
                    for ptype in ("implement", "BD"):
                        self.assertEqual(internal(R.generate_prompt(model, ptype, None, "en-US", units=built["units"])["issues"]), [])


class JunkTests(unittest.TestCase):
    def test_author_text_with_a_missing_value_literal_is_reported_by_prompts_and_diagrams(self):
        model = build_base()
        model["nodes"][0]["name"] = "undefined NaN None"
        built = D.build_units(model, "en-US")
        self.assertIn("DC-L-001", {i["rule"] for i in built["issues"]})
        self.assertTrue(any(i["rule"] == "DC-L-001" for u in built["units"] for i in u["issues"]))
        result = R.generate_prompt(model, "implement", None, "en-US")
        self.assertIn("DC-L-001", {i["rule"] for i in result["issues"]})
        report = L.lint_model(model, lang="en-US")
        keys = [(i["rule"], i["path"], i.get("got")) for i in report["issues"] if i["rule"] == "DC-L-001"]
        self.assertEqual(len(keys), len(set(keys)), "the unit builder and the diagram text lint must not double report")

    def test_a_clean_model_has_no_junk_issue_in_prompts_or_units(self):
        model = build_base()
        for lang in T.LANGS:
            self.assertEqual([i for i in D.build_units(model, lang)["issues"] if i["rule"] == "DC-L-001"], [])
            for ptype in R.TYPES:
                result = R.generate_prompt(model, ptype, None, lang, sealed_case_ids=[c["caseId"] for c in model["cases"]], chain_lines=["step"])
                self.assertEqual([i for i in result["issues"] if i["rule"] == "DC-L-001"], [], (lang, ptype))

    def test_non_finite_numbers_print_as_a_marker_everywhere(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            model = build_base()
            model["cases"][0]["events"][0]["params"] = {"target": value, "n": [1, value]}
            B.get_bundle(model, "en-US", use_cache=False)
            ds = B.get_bundle(model, "en-US", use_cache=False).docset
            text = A.assemble(ds, "DD", ds["fragments"]["elements"])
            result = R.generate_prompt(model, "implement", None, "en-US")
            for out in (text, result["markdown"]):
                self.assertNotRegex(out, r"\b(?:NaN|Infinity|inf)\b")
            self.assertIn(T.NON_FINITE, T.scalar({"x": value}))
            self.assertEqual(T.scalar(value), T.NON_FINITE)


class MermaidSlotTests(unittest.TestCase):
    def test_a_marker_slot_has_no_raw_hash_and_is_still_counted(self):
        model = build_base()
        for node in model["nodes"]:
            if node["id"] == "if.status":
                node["props"]["enums"] = {"": []}
        units = D.build_units(model, "en-US")["units"]
        marked = [u for u in units if "NOT-AUTHORED" in u["mermaid"]]
        self.assertTrue(marked)
        for unit in units:
            for found in T.NOT_AUTHORED_RE.findall(unit["mermaid"]):
                self.assertNotIn("#", found, unit["id"])
            self.assertNotRegex(re.sub(r"#\d+;", "", unit["mermaid"]), r"#")
        self.assertTrue(any(i["rule"] == "DC-L-004" for i in L.lint_diagram_text(units)))


class EmbedTests(unittest.TestCase):
    def test_embed_json_is_script_safe_and_round_trips(self):
        value = {"md": "x</script><!-- y     & > <", "n": [1, 2.5, None, True]}
        text = B.embed_json(value)
        for needle in ("<", ">", "&", " ", " "):
            self.assertNotIn(needle, text)
        self.assertEqual(json.loads(text), value)
        with self.assertRaises(ValueError):
            B.embed_json({"x": float("nan")})

    def test_a_hostile_file_path_cannot_leave_the_script_element(self):
        model = build_base()
        model["evidences"][0]["filePath"] = "x</script><!--   y"
        bundle = B.get_bundle(model, "en-US", use_cache=False)
        payload = B.viewer_payload(model, "en-US")
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertIn("</script", raw)  # the raw JSON is not safe on its own ...
        safe = B.embed_json(payload)
        self.assertNotIn("</script", safe.lower())  # ... the embed form is
        self.assertNotIn("<!--", safe)
        self.assertEqual(json.loads(safe), json.loads(raw))
        self.assertTrue(any("</script" in item["md"] for item in bundle.docset["fragments"]["items"]))


class OverviewLinkTests(unittest.TestCase):
    def test_the_overview_is_linked_through_its_edges_and_nothing_is_unreached(self):
        seen = 0
        for seed in (1, 2, 3):
            model = synth.generate("small", seed)
            bundle = B.get_bundle(model, "en-US", use_cache=False)
            ds = bundle.docset
            overview = [u for u in bundle.units if u["id"] == "component-overview"]
            if not overview:
                continue
            seen += 1
            self.assertNotIn("diagrams", ds["coverage"]["unreached"], seed)
            whole = A.assemble(ds, "DD", ds["fragments"]["elements"])
            self.assertIn("(dg:component-overview)", whole)
            edge = overview[0]["covers"]["edgeIds"][0]
            one = A.assemble(ds, "DD", [edge])
            self.assertIn("(dg:component-overview)", one)
            self.assertNotIn("(dg:component-overview)", A.assemble(ds, "DD", []))
            self.assertNotIn("(dg:component-overview)", A.assemble(ds, "REQ", ds["fragments"]["elements"]))
        self.assertGreaterEqual(seen, 1)


if __name__ == "__main__":
    unittest.main()
