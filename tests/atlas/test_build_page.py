"""Page assembly and the build checks BD-001..BD-007 on in-memory models (design 9.3, M4 acceptance (a), (c), (g) at the data level).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_build_page*.py"

No project, no browser: ``build.render(model, ...)`` and ``htmlassemble`` only. The browser side of the same checks is in
``test_gate_html.py``. Synthetic id families only; hostile strings are made of markup, not of any model data.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas import build
from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas import vocab

from . import synth

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
HOSTILE = ("</script><script>throw new Error('injected')</script><!-- --> <!--ATLAS_DATA--> </style><style>*{display:none}</style> "
           "\u2028 \u2029 ' \\ \" $& \\g<0> \\1 %s ${x} `t` & &amp; &#60; <img src=x onerror=alert(1)> </title><title>x")
FREE_TEXT_KEYS = {"title", "label", "anchor", "filePath", "topic", "description", "note", "why", "role", "name", "sub", "text", "source", "decision"}


def hostile_model(base: dict) -> dict:
    """A copy of ``base`` with the hostile text appended to every free-text field (never to an id or an enum value)."""
    model = copy.deepcopy(base)
    count = [0]

    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in list(value.items()):
                if isinstance(v, str) and k in FREE_TEXT_KEYS:
                    value[k] = v + HOSTILE
                    count[0] += 1
                elif isinstance(v, list) and k == "methods":
                    value[k] = [x + HOSTILE for x in v]
                    count[0] += 1
                else:
                    walk(v, k)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    walk(model)
    assert count[0] > 10, count
    return model


def run_node(code: str, *args: str, suffix: str = ".js"):
    """Run ``code`` with Node from a temporary file (a Windows command line cannot hold a page-sized script)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"probe{suffix}"
        path.write_text(code, encoding="utf-8", newline="\n")
        return subprocess.run([NODE, str(path), *args], capture_output=True, text=True, encoding="utf-8")


class PageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viewer = H.load_viewer()

    def render(self, model, **kw):
        return build.render(model, viewer=self.viewer, **kw)

    def errors(self, rendered):
        return [f"{i['rule']} {i['message']}" for i in rendered.errors]


class HostileTextTests(PageCase):
    def test_hostile_text_in_every_free_text_field_cannot_break_the_page_or_inject_script(self):
        for name, base in (("tiny", synth.generate("tiny")), ("small", synth.generate("small", 2)), ("cjk", synth.edge_case("cjk", 3))):
            with self.subTest(model=name):
                model = hostile_model(base)
                title = model["meta"]["title"]
                self.assertIn("</script>", title)
                r = self.render(model)
                self.assertEqual(self.errors(r), [])
                page = r.html
                self.assertEqual(page.lower().count("</script"), 3, "only the three script elements of the page")
                self.assertNotIn("<!--", page)
                self.assertNotIn("\u2028", page)
                self.assertNotIn("\u2029", page)
                skeleton = H.parse_skeleton(page)
                self.assertEqual(len(skeleton.scripts), 3)
                self.assertEqual([t for t, _a in skeleton.tags if t in ("script", "style", "link", "img", "iframe", "base")], ["script", "style", "script", "script"])
                import html as htmllib
                self.assertEqual(htmllib.unescape(skeleton.title), re.sub(r"[\x00-\x1f\x7f  ]+", " ", title).strip())
                data = H.extract_data(page)
                self.assertEqual(data["model"], model, "every hostile string survives byte for byte inside the data")
                self.assertEqual(data, json.loads(json.dumps(r.bundle)))
                hostile_nodes = [n for n in data["model"]["nodes"] if "\u2028" in n["name"]]
                self.assertTrue(hostile_nodes)

    def test_fragments_and_diagrams_carry_the_hostile_text_safely(self):
        r = self.render(hostile_model(synth.generate("small", 4)))
        text = json.dumps(r.bundle["fragments"]) + json.dumps(r.bundle["diagrams"])
        self.assertIn("injected", text, "the text reaches the documents and diagrams (they quote the model)")
        self.assertEqual(self.errors(r), [])

    def test_replacement_patterns_in_author_text_are_inert(self):
        model = synth.generate("tiny")
        model["meta"]["title"] = "$& \\g<0> \\1 <!--ATLAS_STYLE--> <!--ATLAS_RUNTIME-->"
        r = self.render(model)
        self.assertEqual(self.errors(r), [])
        self.assertEqual(r.html.count("<!--ATLAS_"), 0)
        data = H.extract_data(r.html)
        self.assertEqual(data["meta"]["title"], model["meta"]["title"])
        self.assertIn("&amp;", H.parse_skeleton(r.html).title)

    def test_a_proto_key_is_an_own_property_after_the_json_parse(self):
        model = synth.generate("tiny")
        bundle = build.make_bundle(model, viewer=self.viewer)
        bundle["closures"]["__proto__"] = [1, 2]
        text = H.embed_data(bundle)
        self.assertTrue(text.startswith("JSON.parse('") and text.endswith("')"))
        self.assertIn('"__proto__"', H.unescape_single_quoted(text[len("JSON.parse('"):-2]))
        if NODE:
            probe = f"const v = {text};\nconsole.log(Object.prototype.hasOwnProperty.call(v.closures, '__proto__'), Object.keys(v).length);\n"
            done = run_node(probe)
            self.assertEqual(done.stdout.split(), ["true", "10"], done.stderr)

    def test_the_embedded_text_parses_back_in_node_to_the_same_bundle(self):
        if not NODE:
            self.skipTest("NODE NOT FOUND: the JS side of the data literal was NOT checked")
        r = self.render(hostile_model(synth.generate("tiny")))
        script = H.parse_skeleton(r.html).scripts[1]
        probe = "globalThis.window = globalThis;\n" + script + "\nprocess.stdout.write(JSON.stringify(window.__ATLAS__));"
        done = run_node(probe)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), json.loads(json.dumps(r.bundle)))

    def test_code_looking_text_in_the_data_is_not_a_portability_violation(self):
        model = synth.generate("tiny")
        for node in model["nodes"]:
            node["name"] += " fetch(url) import('x') @import url(http://x.test/a.css) XMLHttpRequest eval(x) new Function('a')"
        for ev in model["evidences"]:
            ev["anchor"] += " fetch( import( eval("
        r = self.render(model)
        self.assertEqual(self.errors(r), [])


class BuildCheckTests(PageCase):
    def good(self):
        model = synth.generate("tiny")
        r = self.render(model)
        self.assertEqual(self.errors(r), [])
        return model, r

    def rules(self, issues):
        return {i["rule"] for i in issues if i["severity"] == "ERROR"}

    def check(self, page, bundle=None, viewer=None, **kw):
        return H.check_page(page, viewer or self.viewer, bundle, **kw)

    def test_a_clean_page_has_no_findings_at_all(self):
        _model, r = self.good()
        self.assertEqual(r.issues, [])

    def test_bd001_flags_every_forbidden_construct_in_the_code_parts(self):
        model, _r = self.good()
        for needle in ("fetch(", "import(", "@import", "XMLHttpRequest", "sendBeacon", "importScripts", "new WebSocket", "EventSource", "eval(", "new Function"):
            with self.subTest(needle=needle):
                bad = dataclasses.replace(self.viewer, runtime=self.viewer.runtime + f"\n// {needle} 1\n")
                page = H.fill_page(bad, build.make_bundle(model, viewer=bad))
                self.assertIn("BD-001", self.rules(self.check(page, viewer=bad)))
        bad_css = dataclasses.replace(self.viewer, css=self.viewer.css + "\nbody { background: url(https://x.test/a.png); }")
        self.assertIn("BD-001", self.rules(self.check(H.fill_page(bad_css, build.make_bundle(model, viewer=bad_css)), viewer=bad_css)))
        for tail in ('<link rel="stylesheet" href="https://x.test/a.css">', '<img src="a.png">', '<a href="#x">x</a>', '<iframe src="about:blank"></iframe>',
                     '<script src="https://x.test/a.js"></script>', '<form action="x"></form>'):
            with self.subTest(tail=tail):
                page = H.fill_page(self.viewer, build.make_bundle(model, viewer=self.viewer)).replace("</body>", tail + "</body>")
                self.assertIn("BD-001", self.rules(self.check(page)))

    def test_bd002_a_left_over_or_duplicated_placeholder(self):
        model, r = self.good()
        self.assertIn("BD-002", self.rules(self.check(r.html.replace("</body>", "<!--ATLAS_DATA--></body>"))))
        broken = dataclasses.replace(self.viewer, template=self.viewer.template.replace("<!--ATLAS_TITLE-->", "<!--ATLAS_TITLE--><!--ATLAS_TITLE-->"))
        self.assertIn("BD-002", self.rules(self.check(r.html, viewer=broken)))

    def test_load_viewer_refuses_a_template_with_a_missing_or_doubled_placeholder(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            shutil.copytree(H.DIST, dist)
            shutil.copy2(H.DIST.parent / H.SOURCES_NAME, Path(tmp) / H.SOURCES_NAME)
            self.assertIsInstance(H.load_viewer(dist), H.Viewer)
            path = dist / "atlas.template.html"
            path.write_text(path.read_text(encoding="utf-8") + "<!--ATLAS_TITLE-->", encoding="utf-8", newline="\n")
            with self.assertRaises(H.ViewerError) as cm:
                H.load_viewer(dist)
            self.assertIn("does not match", cm.exception.message, "an edited dist file no longer matches SOURCES.sha256")

    def test_load_viewer_refuses_a_missing_or_edited_bundle(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(H.ViewerError):
                H.load_viewer(Path(tmp) / "nodist")
            dist = Path(tmp) / "dist"
            shutil.copytree(H.DIST, dist)
            shutil.copy2(H.DIST.parent / H.SOURCES_NAME, Path(tmp) / H.SOURCES_NAME)
            (dist / "atlas-runtime.js").write_text("// edited\n", encoding="utf-8")
            with self.assertRaises(H.ViewerError):
                H.load_viewer(dist)

    def test_bd003_the_data_script(self):
        model, r = self.good()
        prefix = H.DATA_PREFIX
        i = r.html.index(prefix)
        tampered = r.html[:i + len(prefix)] + "<" + r.html[i + len(prefix):]
        self.assertIn("BD-003", self.rules(self.check(tampered, r.bundle)))
        other = dict(r.bundle)
        other["meta"] = dict(other["meta"], title="different")
        self.assertIn("BD-003", self.rules(self.check(r.html, other)), "the embedded data must equal the input bundle")
        extra = dict(r.bundle, extra=1)
        page = H.fill_page(self.viewer, extra)
        self.assertIn("BD-003", self.rules(self.check(page)))
        missing = {k: v for k, v in r.bundle.items() if k != "closures"}
        self.assertIn("BD-003", self.rules(self.check(H.fill_page(self.viewer, missing))))

    def test_bd004_structure(self):
        model, r = self.good()
        no_runtime = r.html.replace(self.viewer.runtime.rstrip("\n"), "")
        self.assertIn("BD-004", self.rules(self.check(no_runtime)))
        swapped = r.html.replace("<script>", "<script> ", 1)
        self.assertIn("BD-004", self.rules(self.check(swapped)))
        self.assertIn("BD-004", self.rules(self.check(r.html.replace(' lang="en-US"', ' lang="xx"', 1))))
        extra = r.html.replace("</body>", "<script>1</script></body>")
        self.assertIn("BD-004", self.rules(self.check(extra)))

    def test_bd005_size_warn_and_fail(self):
        model = synth.generate("tiny")
        bundle = build.make_bundle(model, viewer=self.viewer)
        bundle["meta"]["blob"] = "x" * (3 * 1024 * 1024)
        issues = self.check(H.fill_page(self.viewer, bundle), bundle)
        self.assertEqual([(i["rule"], i["severity"]) for i in issues if i["rule"] == "BD-005"], [("BD-005", "WARN")])
        bundle["meta"]["blob"] = "x" * (8 * 1024 * 1024)
        issues = self.check(H.fill_page(self.viewer, bundle), bundle)
        self.assertEqual([(i["rule"], i["severity"]) for i in issues if i["rule"] == "BD-005"], [("BD-005", "ERROR")])

    def test_bd006_encoding(self):
        _model, r = self.good()
        self.assertIn("BD-006", self.rules(self.check(r.html.replace("\n", "\r\n", 3))))
        self.assertIn("BD-006", self.rules(self.check(b"\xef\xbb\xbf" + r.html.encode("utf-8"))))
        self.assertIn("BD-006", self.rules(self.check(r.html + "\x00")))
        self.assertIn("BD-006", self.rules(self.check(r.html.encode("utf-8") + b"\xff")))

    def test_bd007_machine_paths_and_unreproducible_fills(self):
        model, r = self.good()
        for text in ("see C:\\Users\\someone\\x", "built in /home/someone/x", "stamp 2026-01-02T03:04:05"):
            with self.subTest(text=text):
                bad = dataclasses.replace(self.viewer, runtime=self.viewer.runtime + f"\n// {text}\n")
                page = H.fill_page(bad, build.make_bundle(model, viewer=bad))
                self.assertIn("BD-007", self.rules(self.check(page, viewer=bad)))
        self.assertIn("BD-007", self.rules(self.check(r.html + " ", r.bundle)), "a page that a second fill does not reproduce")

    def test_the_title_is_text_and_the_language_is_a_whitelist(self):
        self.assertEqual(H.safe_lang("xx"), "en-US")
        self.assertEqual(H.safe_lang("ja-JP"), "ja-JP")
        self.assertEqual(H.safe_title("<b>&\"'", ""), "&lt;b&gt;&amp;&quot;&#x27;")
        self.assertEqual(H.safe_title("a\nb\x00c", ""), "a b c")
        self.assertEqual(H.safe_title("", "fallback"), "fallback")


class SizeAndDeterminismTests(PageCase):
    def test_the_breakdown_adds_up(self):
        r = self.render(synth.generate("reference", 3))
        report = r.breakdown
        self.assertEqual(sum(size for _n, size in report["parts"]), report["total"])
        data = dict(report["data"])
        self.assertEqual(sum(data.values()), dict(report["parts"])["data"])
        self.assertEqual(set(data) - {"data-escape"}, set(H.BUNDLE_KEYS))
        text = H.format_breakdown(report)
        self.assertIn("fragments", text)
        self.assertIn(str(report["total"]), text)

    def test_the_unsealed_reference_scale_page_is_within_the_budget(self):
        r = self.render(synth.generate("reference", 3))
        self.assertEqual(self.errors(r), [])
        self.assertLessEqual(len(r.html.encode("utf-8")), 2_000_000)

    def test_two_renders_are_byte_identical_and_hash_seed_independent(self):
        model = synth.generate("small", 5)
        first = self.render(model).html
        self.assertEqual(first, self.render(copy.deepcopy(model)).html)
        script = ("import sys; sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')\n"
                  "from atlas import synth\nfrom gemarc_mcp.atlas import build\nimport hashlib\n"
                  "print(hashlib.sha256(build.render(synth.generate('small', 5)).html.encode('utf-8')).hexdigest())\n")
        seen = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(ROOT / "src"))
            done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen.add(done.stdout.strip())
        self.assertEqual(seen, {hashlib.sha256(first.encode("utf-8")).hexdigest()})

    def test_the_page_holds_no_timestamp_and_no_machine_path(self):
        page = self.render(synth.generate("small", 5)).html
        skeleton = H.parse_skeleton(page)
        code = "\n".join([skeleton.scripts[0], skeleton.scripts[2], *skeleton.styles])        # the data are author text and may quote anything
        for text in (code, H.extract_data(page)["meta"]["modelSha"] + page[:400]):
            self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text))
            self.assertIsNone(re.search(r"[A-Za-z]:\\|/Users/|/home/", text))
        self.assertNotIn(str(ROOT).replace("\\", "/"), page.replace("\\\\", "/"))
        self.assertNotIn(str(ROOT), page)


class GenericityProbeTests(PageCase):
    """Acceptance (c) at the data level: random id families, 400 nodes, unknown building / kind / trust values, no cases."""

    def probe_model(self, seed: int) -> dict:
        model = synth.edge_case("big400", seed)
        synth._inject_unknown_values(model)           # unknown trust, layer, tier, kinds and one unknown relation
        node = model["nodes"][0]
        node["roomId"] = "no-such-room-" + node["id"]        # unknown building (the room does not exist)
        model["cases"], model["interventions"] = [], []
        return model

    def test_random_families_unknown_values_and_no_cases_render_with_clean_checks(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                model = self.probe_model(seed)
                self.assertEqual(len(model["nodes"]), 400)
                r = self.render(model)
                self.assertEqual(self.errors(r), [])
                data = H.extract_data(r.html)
                warns = {w["code"] for w in data["meta"]["warnings"]}
                self.assertIn("GEMARC_ATLAS_VIEW_005", warns, "the node of the missing room is reported as unassigned")
                self.assertIn("GEMARC_ATLAS_VIEW_001", warns)
                self.assertGreaterEqual(data["calibrationHooks"]["scene"]["unassignedCount"], 1)
                self.assertEqual(data["runs"], {"baselines": {}, "pairs": []})
                self.assertEqual(data["meta"]["counts"]["cases"], 0)
                self.assertEqual(data["calibrationHooks"]["scene"]["nodeCount"], 400)
                rules = {n["code"] for n in data["meta"]["qa"]["notes"]}
                self.assertTrue({"RC-002"} <= rules, rules)
                self.assertLessEqual(len(data["meta"]["warnings"]), 25 * 10)

    def test_the_id_families_differ_between_seeds(self):
        prefixes = {synth.edge_case("big400", s)["nodes"][0]["id"].split(".")[0] for s in (1, 2, 3, 4, 5)}
        self.assertGreater(len(prefixes), 2)

    def test_models_with_missing_collections_still_render(self):
        base = synth.generate("tiny")
        for drop in ("cases", "interventions", "requirements", "tbds", "policies", "machines", "evidences", "tests", "ports"):
            with self.subTest(drop=drop):
                model = copy.deepcopy(base)
                model[drop] = []
                self.assertEqual(self.errors(self.render(model)), [])


class VocabularyFindingTests(PageCase):
    def profile(self):
        return vocab.load_profile("software")

    def test_each_finding_is_reported_once_with_the_viewer_subject(self):
        model = synth.generate("small", 2)
        nodes = model["nodes"]
        nodes[0]["kind"] = "WidgetKind"
        nodes[1]["kind"] = "WidgetKind"
        nodes[2].setdefault("props", {})["trust"] = "nowhere"
        nodes[3]["roomId"] = "gone"
        model["edges"][0]["relation"] = "frobs"
        floors = model["space"]["floors"]
        floors[1]["buildingId"], floors[1]["level"] = floors[0]["buildingId"], floors[0]["level"]
        found = build.vocab_findings(model, self.profile())
        by_code = {}
        for w in found["warnings"]:
            by_code.setdefault(w["code"], []).append(w["subject"])
        self.assertEqual(by_code["GEMARC_ATLAS_VIEW_001"], ["WidgetKind"], "a kind is reported once however many nodes use it")
        self.assertEqual(by_code["GEMARC_ATLAS_VIEW_003"], ["nowhere"])
        self.assertEqual(by_code["GEMARC_ATLAS_VIEW_005"], [nodes[3]["id"]])
        self.assertEqual(by_code["GEMARC_ATLAS_VIEW_002"], ["frobs"])
        self.assertEqual(by_code["GEMARC_ATLAS_VIEW_006"], [floors[1]["id"]])
        notes = {(n["code"], n["subject"]) for n in found["notes"]}
        self.assertIn(("RC-001", floors[1]["id"]), notes)
        self.assertIn(("RC-002", "WidgetKind"), notes)
        self.assertIn(("RC-002", "nowhere"), notes)
        self.assertEqual(found["unassigned"], 1)

    def test_a_valid_model_has_no_vocabulary_findings(self):
        found = build.vocab_findings(synth.generate("small", 3), self.profile())
        self.assertEqual(found["warnings"], [])
        self.assertEqual({n["code"] for n in found["notes"]} - {"LB-001"}, set())
        self.assertEqual(found["unassigned"], 0)

    def test_findings_are_capped_per_code_and_the_rest_is_counted(self):
        model = synth.edge_case("big400", 1)
        for i, node in enumerate(model["nodes"][:60]):
            node["kind"] = f"Kind{i}"
        found = build.vocab_findings(model, self.profile())
        self.assertEqual(sum(1 for w in found["warnings"] if w["code"] == "GEMARC_ATLAS_VIEW_001"), 25)
        self.assertEqual(found["suppressed"]["GEMARC_ATLAS_VIEW_001"], 35)

    def test_label_estimate_equals_the_viewer_function_for_ascii_and_wide_text(self):
        if not NODE:
            self.skipTest("NODE NOT FOUND: the label estimate twin was NOT compared")
        samples = ["", "abc", "Preference layer roster system", "\u30d7\u30ec\u30d5\u30a1\u30ec\u30f3\u30b9 layer", "\u504f\u597d\u8bbe\u7f6e", "a\U0001F600b", "x" * 40]
        sizes = [9, 10, 12]
        script = ("import { estimateTextWidth } from 'file:///" + (ROOT / "src/gemarc_mcp/atlas/viewer/src/label-fit.js").as_posix() + "';\n"
                  "const rows = JSON.parse(process.argv[2]);\n"
                  "console.log(JSON.stringify(rows.map(([t, s]) => estimateTextWidth(t, s))));\n")
        rows = [[t, s] for t in samples for s in sizes]
        done = run_node(script, json.dumps(rows), suffix=".mjs")
        self.assertEqual(done.returncode, 0, done.stderr)
        for (text, size), expected in zip(rows, json.loads(done.stdout)):
            self.assertEqual(build.estimate_text_width(text, size), expected, (text, size))


class RunsMappingTests(unittest.TestCase):
    def test_no_sealed_input_gives_the_empty_state_everywhere(self):
        runs, units, lengths, withheld = build.runs_from_sealed(None)
        self.assertEqual((runs, units, lengths, withheld), ({"baselines": {}, "pairs": []}, [], [], {"cases": [], "interventions": []}))

    def test_provider_shapes_become_bundle_shapes_without_the_heavy_parts(self):
        event = {"index": 0, "sequence": 1, "caseId": "C-1", "eventId": "C-1#1", "logicalTime": 0, "sourceNodeId": "a", "targetNodeId": "b", "edgeId": "e",
                 "eventType": "Call", "outcome": "OK", "previousState": "Idle", "nextState": "Running",
                 "stateChanges": [{"nodeId": "b", "from": "Idle", "to": "Running"}], "ruleId": None, "evidenceIds": [], "errorCode": None, "cause": None,
                 "attempt": None, "synthetic": False, "role": None, "httpStatus": None}
        run = {"caseId": "C-1", "modelVersion": "abcd1234", "status": "OK", "errors": [], "trace": [event], "initialState": {"a": "Idle", "b": "Idle"},
               "finalState": {"a": "Idle", "b": "Running"}, "elementStates": {}, "snapshots": [{"index": -1, "state": {"a": "Idle"}}], "entryNodeId": "a",
               "aggregatorFailures": [], "hash": "00ff00ff"}
        sealed = {"baselines": {"C-1": {"state": "sealed", "run": run}},
                  "pairs": [{"interventionId": "I-1", "caseId": "C-1", "state": "sealed",
                             "result": {"caseId": "C-1", "status": "OK", "errors": [], "A": run, "B": run, "diff": {"outcomeClass": "NO_DIFFERENCE"}, "explanation": {"x": 1}},
                             "chain": {"status": "OK", "steps": []}}],
                  "diagrams": [{"id": "sequence-C-1", "type": "sequence"}], "withheld": {"cases": ["C-2"], "interventions": []}}
        runs, units, lengths, withheld = build.runs_from_sealed(sealed)
        base = runs["baselines"]["C-1"]
        self.assertNotIn("snapshots", base)
        self.assertEqual(base["initialState"], {})
        self.assertEqual(base["finalState"], {"b": "Running"})
        self.assertEqual(base["trace"], [{"index": 0, "eventId": "C-1#1", "logicalTime": 0, "sourceNodeId": "a", "targetNodeId": "b", "edgeId": "e", "eventType": "Call",
                                          "outcome": "OK", "stateChanges": [{"nodeId": "b", "from": "Idle", "to": "Running"}]}])
        pair = runs["pairs"][0]
        self.assertEqual(set(pair), {"caseId", "interventionId", "status", "errors", "B", "diff", "chain"})
        self.assertNotIn("A", pair)
        self.assertEqual(lengths, [{"caseId": "C-1", "interventionId": None, "length": 1}, {"caseId": "C-1", "interventionId": "I-1", "length": 1}])
        self.assertEqual(withheld["cases"], ["C-2"])
        self.assertEqual(units, [{"id": "sequence-C-1", "type": "sequence"}])


if __name__ == "__main__":
    unittest.main()
