"""``screens scan`` (``protoui/scan.py``, design A3 4.8 and 5.8): literal probes of a prototype against a frontend source tree.

* the probes themselves (route, label, bind, goto, ``data-atlas-id``, the kind hint) on small trees;
* the FOUND rate on the unmodified synthetic frontend (target 95 percent of the route, label, bind and goto literals);
* what the scan alone catches of the ten seeded defects of T-S3. The detection rule is fixed here BEFORE the numbers are read: a defect is caught when,
  against the scan of the clean tree, the answer of at least one check point of the defect (``direct``, or ``indirect``: a consequence of it) changes
  from FOUND to ABSENT, or a new ``kind?`` hint appears, or (a tree with ``data-atlas-id``) a widget id turns absent or an unknown id of the screen turns up.
  Nothing in the scan was tuned afterwards to move the count; the numbers are pinned as measured and the three defects without a literal stay uncaught;
* hygiene: no host path, no credential, no word that says a thing is right, the same bytes every time (also under three hash seeds);
* the command (a real subprocess): alias roots only, the scan file is written under ``atlas/.atlas/ui`` and nowhere else, ``.env*`` is never read.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_screens_scan.py"
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import roots as rt
from gemarc_mcp.atlas.protoui import atl, expand as ex, model as um, store
from gemarc_mcp.atlas.protoui import scan as sc

from . import frontend_synth as fs
from .test_ui_store import UiProject

MODEL = fs.prototype_model()
HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"
FORBIDDEN = ("一致", "conform", "consisten", "match", "agree", "identical")


def tiny(route: str = "/book/{id}", label: str = "登录", bind: str = "email", kind: str = "button", extra: str = "") -> dict:
    text = ("#atlas-ui 1\n## screens: id | title | purpose | opts\n"
            f"SCR-A | A | one screen. | kind=page route={route} start=true\nSCR-B | B | another one. | kind=page route=/home\n"
            f"## widgets: local | label | opts\n@set screen=SCR-A\nw | {label} | kind={kind} bind={bind}\n{extra}"
            "## nav: from | to | label | opts\nSCR-A.w | SCR-B | go | on=tap\n")
    exp = ex.expand_ui([atl.parse_atl(text, "ui-10-tiny.atl")])
    assert not [d for d in exp.diagnostics if d.severity == "ERROR"], exp.diagnostics
    return exp.model


def scan_tree(files: dict, model: "dict | None" = None, **kw) -> "sc.ScanResult":
    with tempfile.TemporaryDirectory() as t:
        fs.write(files, t)
        return sc.scan(model or MODEL, t, repo="fe", **kw)


def literal(result: "sc.ScanResult", screen: str, ref: str) -> dict:
    return result.screens[screen].literals[ref]


class GlobTests(unittest.TestCase):
    def test_the_default_glob_and_the_pieces(self):
        rx = sc.compile_glob(sc.DEFAULT_GLOB)
        for good in ("a.html", "src/a.tsx", "src/deep/er/a.vue", "a.svelte", "x/y.js", "x.ts", "a.jsx", "p.htm"):
            self.assertTrue(rx.match(good), good)
        for bad in ("a.css", "a.py", "src/a.tsx.map", "a.json", "dir/"):
            self.assertFalse(rx.match(bad), bad)
        self.assertTrue(sc.compile_glob("src/*.js").match("src/a.js"))
        self.assertFalse(sc.compile_glob("src/*.js").match("src/deep/a.js"))
        self.assertTrue(sc.compile_glob("pages/page-?.html").match("pages/page-1.html"))
        self.assertTrue(sc.compile_glob("**/*.{html,js}").match("a/b/c.js"))

    def test_a_pattern_that_could_be_abused_is_refused(self):
        for bad in ("", "  ", "a\\b", "**/**/**/**/x", "x" * 300, "src/{a,b"):
            with self.assertRaises(ValueError):
                sc.compile_glob(bad)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def put(self, rel: str, text: "str | bytes") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))

    def test_the_tree_is_selected_sorted_and_the_rest_is_counted(self):
        self.put("b.html", "<p>b</p>")
        self.put("a/z.js", "var z;")
        self.put("a/y.ts", "var y;")
        self.put("a/readme.md", "not source")
        self.put("node_modules/lib/x.js", "var x;")
        self.put(".git/hooks.js", "var h;")
        self.put("dist/bundle.js", "var d;")
        self.put(".hidden/x.js", "var h;")
        self.put(".env", "TOKEN_FOR_TESTS=placeholder")
        self.put("config/secrets.js", "var s;")
        self.put("big.js", b"x" * (sc.MAX_FILE_BYTES + 1))
        self.put("binary.js", b"\x00\x01\x02")
        self.put("latin.js", "caf\xe9".encode("latin-1"))
        tree = sc.collect(self.root)
        self.assertEqual([d.rel for d in tree.docs], ["a/y.ts", "a/z.js", "b.html"])
        self.assertEqual(tree.skipped, {"sensitive": 1, "large": 1, "unreadable": 2, "limit": 0})

    def test_a_sensitive_file_is_never_opened(self):
        self.put(".env", "TOKEN_FOR_TESTS=placeholder")
        self.put(".env.local", "X=1")
        self.put("secrets.js", "var s;")
        self.put("keys/server.pem", "x")
        self.put("ok.js", "var ok;")
        seen = []
        real = ev.read_text_file

        def spy(path, **kw):
            seen.append(Path(path).name)
            return real(path, **kw)

        with mock.patch.object(ev, "read_text_file", spy):
            sc.collect(self.root, "**/*")
        self.assertEqual(seen, ["ok.js"])

    def test_a_link_is_not_followed(self):
        target = tempfile.TemporaryDirectory()
        self.addCleanup(target.cleanup)
        (Path(target.name) / "outside.js").write_text("var outside;", encoding="utf-8")
        link = self.root / "linked"
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), target.name], check=True, capture_output=True)
            else:
                link.symlink_to(target.name, target_is_directory=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no directory link can be made here")
        self.put("ok.js", "var ok;")
        self.assertEqual([d.rel for d in sc.collect(self.root).docs], ["ok.js"])

    def test_the_file_count_is_limited(self):
        for i in range(5):
            self.put(f"f{i}.js", "var x;")
        with mock.patch.object(sc, "MAX_FILES", 3):
            tree = sc.collect(self.root)
        self.assertEqual((len(tree.docs), tree.skipped["limit"]), (3, 2))


class SpanTests(unittest.TestCase):
    def test_comments_and_the_title_are_found_and_the_rest_is_not(self):
        text = "<title>T</title>\n<!-- a -->x\n/* b */y\n  // c\nz // d\n<TITLE>U</TITLE>"
        spans = sc.ignored_spans(text)
        picked = [text[a:b] for a, b in spans]
        self.assertEqual(picked, ["<title>T</title>", "<!-- a -->", "/* b */", "  // c", "<TITLE>U</TITLE>"])
        self.assertTrue(all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1)))

    def test_an_unterminated_opener_is_not_tried_again_so_a_hostile_file_cannot_make_it_slow(self):
        import time
        text = "a/*b\n" * 150_000 + "<!--x\n" * 50_000 + "<title>" * 50_000
        started = time.perf_counter()
        self.assertEqual(sc.ignored_spans(text), [])
        self.assertLess(time.perf_counter() - started, 3.0)


class ProbeTests(unittest.TestCase):
    def test_a_route_is_a_token_not_a_part_of_a_longer_path(self):
        model = tiny(route="/login")
        for text, found in (('"/login"', True), ("'/login'", True), ("href=\"/login\"", True), ("`/login`", True), ('"/login/"', True), ('"/login?next=/x"', True),
                            ('"/login/sso"', False), ('"/api/login"', False), ('"/loginx"', False), ('"/log-in"', False), ('// "/login" is the page', False),
                            ("<!-- /login -->", False)):
            result = scan_tree({"a.js": text + "\n"}, model, screens=["SCR-A"])
            self.assertEqual(literal(result, "SCR-A", "SCR-A@route")["state"] == "FOUND", found, text)

    def test_a_route_with_a_parameter_is_found_in_the_usual_spellings(self):
        model = tiny(route="/book/{id}")
        for text, found in (('"/book/{id}"', True), ('"/book/:id"', True), ("`/book/${bookId}`", True), ('"/book/[id]"', True), ('"/book/"', False),
                            ('"/books/:id"', False), ('"/book/:id/edit"', False)):
            result = scan_tree({"a.js": text + "\n"}, model, screens=["SCR-A"])
            self.assertEqual(literal(result, "SCR-A", "SCR-A@route")["state"] == "FOUND", found, text)

    def test_a_label_is_a_whole_string_and_a_longer_string_is_only_the_closest_one(self):
        model = tiny(label="登录")
        for text, found in (("<button>登录</button>", True), ("<button>\n      登录\n    </button>", True), ('<a aria-label="登录">x</a>', True),
                            ("const t = '登录';", True), ("<h1>登录表单</h1>", False), ("<title>登录</title>", False), ("<!-- 登录 -->", False), ("<p>登入</p>", False)):
            result = scan_tree({"a.html": text + "\n"}, model, screens=["SCR-A"])
            entry = literal(result, "SCR-A", "SCR-A.w@label")
            self.assertEqual(entry["state"] == "FOUND", found, text)
        absent = literal(scan_tree({"a.html": "<h1>登录表单</h1>\n"}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@label")
        self.assertEqual(absent["state"], "ABSENT")
        self.assertIn("登录表单", absent["near"])
        self.assertIn("(fe:a.html:1)", absent["near"])

    def test_a_label_with_markup_characters_is_found_escaped_too(self):
        model = tiny(label="Q&A")
        self.assertEqual(literal(scan_tree({"a.html": "<b>Q&amp;A</b>\n"}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@label")["state"], "FOUND")
        self.assertEqual(literal(scan_tree({"a.html": "<b>Q&A</b>\n"}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@label")["state"], "FOUND")

    def test_a_field_name_is_found_by_its_attribute_or_as_a_word_but_not_as_a_type_or_a_class(self):
        model = tiny(bind="email")
        cases = (('<input name="email">', "attr"), ('<input v-model="email">', "attr"), ("x = form['email']", "quoted"), ("payload.email = 1", "word"),
                 ('<input type="email">', None), ('<input autocomplete="email">', None), ('<label for="email">x</label>', None), ('<i class="email"></i>', None),
                 ("var emailAddress;", None), ("// email goes here", None))
        for text, how in cases:
            entry = literal(scan_tree({"a.html": text + "\n"}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@bind")
            self.assertEqual(entry.get("how") if entry["state"] == "FOUND" else None, how, text)

    def test_goto_is_looked_for_near_the_widget_and_not_in_the_whole_file(self):
        model = tiny(label="登录")
        near = '<button onclick="go(\'/home\')">登录</button>\n'
        far = near.replace("/home", "/elsewhere") + "\n" * 12 + '<a href="/home">other</a>\n'
        found = literal(scan_tree({"a.html": near}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@goto")
        self.assertEqual((found["state"], found["how"]), ("FOUND", "window"))
        lost = literal(scan_tree({"a.html": far}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@goto")
        self.assertEqual(lost["state"], "ABSENT")
        self.assertIn("/elsewhere", lost["near"])
        unlocated = literal(scan_tree({"a.html": '<a href="/home">x</a>\n'}, model, screens=["SCR-A"]), "SCR-A", "SCR-A.w@goto")
        self.assertEqual(unlocated["state"], "ABSENT")
        self.assertIn("was not found", unlocated["why"])

    def test_goto_to_a_screen_without_a_route_is_not_a_probe(self):
        text = fs.prototype_text().replace("route=/home", "")
        model = ex.expand_ui([atl.parse_atl(text, "ui-10-x.atl")]).model
        result = scan_tree(fs.render(fs.prototype_model()), model, screens=["SCR-LOGIN"])
        self.assertNotIn("SCR-LOGIN.submit@goto", result.screens["SCR-LOGIN"].literals)

    def test_data_atlas_id_found_absent_and_extra(self):
        model = tiny(extra="v | 另一个 | kind=text\n")
        files = {"a.html": '<main data-atlas-id="SCR-A"><b data-atlas-id="SCR-A.w">x</b><i data-atlas-id="SCR-A.ghost"></i><u data-atlas-id="SCR-B.other"></u></main>\n'
                          '<!-- <p data-atlas-id="SCR-A.v"></p> -->\n'}
        ids = scan_tree(files, model, screens=["SCR-A"]).screens["SCR-A"].ids
        self.assertEqual(ids, {"found": ["SCR-A.w"], "absent": ["SCR-A.v"], "extra": ["SCR-A.ghost"]})       # the id in a comment does not count, another screen's id is not extra
        none = scan_tree({"a.html": "<b>x</b>\n"}, model, screens=["SCR-A"])
        self.assertEqual((none.tagged, none.screens["SCR-A"].ids), (False, {"found": [], "absent": [], "extra": []}))

    def test_the_kind_hint_names_a_tag_that_does_not_fit_and_is_silent_otherwise(self):
        model = tiny(label="登录", kind="button")
        for text, hint in (("<button>登录</button>", False), ('<a href="/home">登录</a>', True), ('<a role="button">登录</a>', False), ("<div>登录</div>", True),
                           ('<input type="submit" value="登录">', False), ("<p>请 <b>登录</b></p>", True)):
            h = scan_tree({"a.html": text + "\n"}, model, screens=["SCR-A"]).screens["SCR-A"].hints
            self.assertEqual(bool(h), hint, text)
        only = scan_tree({"a.html": '<a href="/home">登录</a>\n'}, model, screens=["SCR-A"]).screens["SCR-A"].hints
        self.assertEqual(only, [{"ref": "SCR-A.w@kind", "dom": "a", "want": "button", "at": "fe:a.html:1"}])

    def test_a_field_is_judged_by_its_name_not_by_the_label_next_to_it(self):
        model = tiny(label="邮箱", kind="input", bind="email")
        self.assertEqual(scan_tree({"a.html": '<label for="e">邮箱</label><input id="e" name="email">\n'}, model, screens=["SCR-A"]).screens["SCR-A"].hints, [])
        wrong = scan_tree({"a.html": '<label for="e">邮箱</label><select id="e" name="email"></select>\n'}, model, screens=["SCR-A"]).screens["SCR-A"].hints
        self.assertEqual([h["dom"] for h in wrong], ["select"])

    def test_only_the_asked_screens_are_scanned(self):
        result = scan_tree(fs.render(MODEL), screens=["SCR-HOME"])
        self.assertEqual(list(result.screens), ["SCR-HOME"])


class RateTests(unittest.TestCase):
    """The FOUND rate on the unmodified synthetic frontend. It is a property of that frontend (every literal written where a conventional page writes it); a
    frontend whose texts are translation keys would score lower, which was not measured."""

    def test_the_found_rate_is_at_least_95_percent_for_every_literal_column(self):
        for tag in (False, True):
            result = scan_tree(fs.render(MODEL, tag_ids=tag))
            by_column: dict = {}
            for s in result.screens.values():
                for ref, v in s.literals.items():
                    column = ref.partition("@")[2]
                    got = by_column.setdefault(column, [0, 0])
                    got[0] += v["state"] == "FOUND"
                    got[1] += 1
            self.assertEqual(sorted(by_column), ["bind", "goto", "label", "route"])
            total = sum(n for _f, n in by_column.values())
            self.assertEqual(total, 72)
            self.assertGreaterEqual(sum(f for f, _n in by_column.values()) / total, 0.95, by_column)
            for column, (found, n) in by_column.items():
                self.assertGreaterEqual(found / n, 0.95, (column, by_column))
            self.assertTrue(all(not s.hints for s in result.screens.values()), "no kind hint on the clean frontend")
            if tag:
                self.assertTrue(result.tagged)
                self.assertTrue(all(not s.ids["absent"] and not s.ids["extra"] for s in result.screens.values()))

    def test_every_check_point_of_a_literal_column_has_an_answer(self):
        result = scan_tree(fs.render(MODEL))
        refs = {c.ref for c in um.checkpoints(MODEL) if c.ref.partition("@")[2] in sc.LITERAL_COLUMNS}
        got = {ref for s in result.screens.values() for ref in s.literals}
        self.assertEqual(got, refs)


def caught(defect: "fs.Defect", tag_ids: bool, clean: "sc.ScanResult") -> dict:
    """What changed in the scan of the defective tree against the clean one (the rule of the module doc)."""
    result = scan_tree(fs.render(MODEL, tag_ids=tag_ids, defect=defect))
    flipped, ids, hints = set(), set(), set()
    for sid, s in result.screens.items():
        for ref, v in s.literals.items():
            if v["state"] != clean.screens[sid].literals[ref]["state"]:
                flipped.add(ref)
        hints |= {h["ref"] for h in s.hints if h not in clean.screens[sid].hints}
        for key in ("absent", "extra"):
            ids |= {f"{key}:{i}" for i in s.ids[key] if i not in clean.screens[sid].ids[key]}
    direct = {r for r in defect.direct if r in flipped | hints}
    indirect = {r for r in defect.indirect if r in flipped | hints}
    return {"direct": direct, "indirect": indirect, "ids": ids, "caught": bool(direct or indirect or ids)}


class SeededDefectTests(unittest.TestCase):
    """T-S3, the "scan only" arm: ten single defects in a six-screen frontend (module doc for the detection rule)."""

    @classmethod
    def setUpClass(cls):
        cls.clean = {tag: scan_tree(fs.render(MODEL, tag_ids=tag)) for tag in (False, True)}
        cls.table = {tag: {d.key: caught(d, tag, cls.clean[tag]) for d in fs.DEFECTS} for tag in (False, True)}

    def keys(self, tag: bool, field: str = "caught", wanted=True) -> list:
        return sorted(k for k, v in self.table[tag].items() if (bool(v[field]) is wanted))

    def test_there_are_ten_defects_of_the_registered_kinds(self):
        self.assertEqual([d.kind for d in fs.DEFECTS],
                         ["missing field", "missing field", "wrong text", "wrong text", "wrong route", "missing state", "wrong goto", "extra control",
                          "misplaced container", "wrong order"])

    def test_every_defect_leaves_the_prototype_untouched_and_changes_only_the_frontend(self):
        clean = fs.render(MODEL)
        for d in fs.DEFECTS:
            files = fs.render(MODEL, defect=d)
            changed = {rel for rel in files if files[rel] != clean[rel]}
            self.assertTrue(changed, d.key)
            if d.key != "wrong-route":         # the route is the one defect that follows its links through the whole tree
                self.assertEqual(changed, {f"pages/{d.screen.lower()}.html"}, d.key)
            self.assertEqual(sorted(files), sorted(clean))

    def test_scan_alone_on_a_plain_frontend_catches_seven_of_ten_six_by_a_literal_of_the_defect_itself(self):
        self.assertEqual(self.keys(False, "direct"), ["missing-field-1", "missing-field-2", "wrong-goto", "wrong-route", "wrong-text-1", "wrong-text-2"])
        self.assertEqual(self.keys(False), ["missing-field-1", "missing-field-2", "missing-state", "wrong-goto", "wrong-route", "wrong-text-1", "wrong-text-2"])
        self.assertEqual(self.keys(False, wanted=False), ["extra-control", "misplaced-container", "wrong-order"])
        self.assertLessEqual(len(self.keys(False, "direct")), 6, "the design expects at most 6 of 10 from literals")

    def test_the_missing_state_is_caught_only_through_the_text_that_was_removed_with_it(self):
        row = self.table[False]["missing-state"]
        self.assertEqual((row["direct"], row["indirect"]), (set(), {"SCR-LOGIN.err@label"}))

    def test_with_data_atlas_id_in_the_frontend_scan_also_sees_an_extra_control_eight_of_ten(self):
        self.assertEqual(self.keys(True), ["extra-control", "missing-field-1", "missing-field-2", "missing-state", "wrong-goto", "wrong-route", "wrong-text-1", "wrong-text-2"])
        self.assertEqual(self.table[True]["extra-control"]["ids"], {"extra:SCR-HOME.export"})
        self.assertEqual(self.table[True]["missing-state"]["ids"], {"absent:SCR-LOGIN.err"})

    def test_a_misplaced_container_and_a_wrong_order_are_never_seen_by_the_scan(self):
        for tag in (False, True):
            for key in ("misplaced-container", "wrong-order"):
                self.assertFalse(self.table[tag][key]["caught"], (tag, key))

    def test_a_word_that_is_also_somewhere_else_is_a_known_miss(self):
        """The limit of a literal probe: the new text is wrong, but the old word is still a whole string of the page (here a second element says it)."""
        d = next(x for x in fs.DEFECTS if x.key == "wrong-text-1")
        files = fs.render(MODEL, defect=d)
        files["pages/scr-login.html"] = files["pages/scr-login.html"].replace("</main>", '  <p class="note">登录</p>\n  </main>')
        result = scan_tree(files)
        self.assertEqual(literal(result, "SCR-LOGIN", "SCR-LOGIN.submit@label")["state"], "FOUND")


class HygieneTests(unittest.TestCase):
    SECRET = "sk" + "-" + "Zq9" * 8

    def outputs(self, result: "sc.ScanResult") -> str:
        parts = ["\n".join(sc.report_lines(result, show_all=True))]
        parts += [result.to_json(sid).decode("utf-8") for sid in result.screens]
        return "\n".join(parts)

    def test_no_host_path_and_no_word_that_says_a_thing_is_right(self):
        with tempfile.TemporaryDirectory() as t:
            fs.write(fs.render(MODEL, defect=next(d for d in fs.DEFECTS if d.key == "wrong-text-2")), t)
            result = sc.scan(MODEL, t, repo="fe")
            text = self.outputs(result)
            for forbidden in {t, Path(t).as_posix(), str(Path.home()), Path.home().as_posix(), os.getcwd(), Path(os.getcwd()).as_posix(), SRC.as_posix()}:
                self.assertNotIn(forbidden, text.replace("\\\\", "\\"))
                self.assertNotIn(forbidden.lower(), text.lower())
        lowered = text.lower()
        for word in FORBIDDEN:
            self.assertNotIn(word, lowered, word)
        self.assertIn("ABSENT", text)

    def test_a_credential_on_a_line_that_would_be_shown_is_withheld(self):
        files = {"src/legacy.js": f'const title = "登入", passwd = "{self.SECRET}";\n', ".env": f"API_TOKEN={self.SECRET}\n", "src/secrets.js": f'var token = "{self.SECRET}";\n'}
        result = scan_tree(files, screens=["SCR-LOGIN"])
        out = self.outputs(result)
        self.assertNotIn(self.SECRET, out)
        self.assertNotIn("Zq9Zq9", out)
        near = literal(result, "SCR-LOGIN", "SCR-LOGIN.submit@label")["near"]
        self.assertTrue(near.startswith("[withheld]"), near)
        self.assertEqual(result.skipped["sensitive"], 1)       # the .env is not even selected by the glob, secrets.js is selected and refused

    def test_the_same_tree_gives_the_same_bytes(self):
        files = fs.render(MODEL, tag_ids=True, defect=fs.DEFECTS[4])
        a, b = scan_tree(files), scan_tree(files)
        for sid in a.screens:
            self.assertEqual(a.to_json(sid), b.to_json(sid))
        self.assertTrue(a.to_json("SCR-LOGIN").endswith(b"}\n"))
        self.assertNotIn(b"\r", a.to_json("SCR-LOGIN"))
        doc = json.loads(a.to_json("SCR-LOGIN"))
        self.assertEqual((doc["schema"], doc["screen"], doc["screenSha8"], doc["uiSha8"]), (store.SCAN_SCHEMA, "SCR-LOGIN", um.screen_sha8(MODEL, "SCR-LOGIN"), um.ui_sha8(MODEL)))
        self.assertEqual(list(doc), sorted(doc))

    def test_the_same_bytes_under_three_hash_seeds(self):
        with tempfile.TemporaryDirectory() as t:
            fs.write(fs.render(MODEL, tag_ids=True, defect=fs.DEFECTS[5]), t)
            code = ("import sys, hashlib\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
                    "from gemarc_mcp.atlas.protoui import scan\nfrom tests.atlas import frontend_synth as fs\nm = fs.prototype_model()\n"
                    "r = scan.scan(m, sys.argv[1], repo='fe')\n"
                    "print(hashlib.sha256(b''.join(r.to_json(s) for s in r.screens)).hexdigest())\n") % (str(SRC), str(HERE.parents[1]))
            digests = set()
            for seed in ("0", "1", "4242"):
                out = subprocess.run([sys.executable, "-c", code, t], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, check=True)
                digests.add(out.stdout.strip())
            self.assertEqual(len(digests), 1, digests)

    def test_the_report_is_short_when_everything_is_found(self):
        result = scan_tree(fs.render(MODEL))
        lines = sc.report_lines(result)
        self.assertEqual(len(lines), 8)           # a header, six screens, the closing note: nothing per literal unless asked
        self.assertLess(len("\n".join(lines).encode("utf-8")), 1200)
        self.assertGreater(len(sc.report_lines(result, show_all=True)), 70)


class CommandTests(UiProject):
    """The command ``screens scan``: a real workspace and real subprocesses."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.put("ui-10-synthetic", fs.prototype_text()).status, "committed")
        self._front = tempfile.TemporaryDirectory()
        self.addCleanup(self._front.cleanup)
        self.front = Path(self._front.name)
        fs.write(fs.render(MODEL, defect=next(d for d in fs.DEFECTS if d.key == "wrong-text-1")), self.front)
        (self.front / ".env").write_text(f"API_TOKEN={HygieneTests.SECRET}\n", encoding="utf-8")
        self.env = {**os.environ, "GEMARC_WORKSPACE_ROOT": str(self.root), "GEMARC_ALLOW_WRITE": "1", rt.ENV_NAME: f"fe={self.front}", "PYTHONPATH": str(SRC),
                    "PYTHONIOENCODING": "utf-8"}

    def command(self, *args: str, env=None) -> "subprocess.CompletedProcess":
        return subprocess.run([sys.executable, "-m", "gemarc_mcp.atlas.protoui.scan", "desk", *args], capture_output=True, env=env or self.env, cwd=str(HERE.parents[1]))

    def text(self, done) -> str:
        return done.stdout.decode("utf-8") + done.stderr.decode("utf-8")

    def test_a_scan_prints_the_answers_and_writes_one_file_per_screen_inside_the_project(self):
        done = self.command("--repo", "fe")
        out = self.text(done)
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("ABSENT SCR-LOGIN.submit@label", out)
        self.assertIn("wrote .atlas/ui/scan-SCR-LOGIN.json", out)
        files = sorted(p.name for p in self.up.state.glob("scan-*.json"))
        self.assertEqual(files, [f"scan-{s}.json" for s in sorted(fs.SCREENS)])
        doc = json.loads((self.up.state / "scan-SCR-LOGIN.json").read_text(encoding="utf-8"))
        self.assertEqual((doc["repo"], doc["literals"]["SCR-LOGIN.submit@label"]["state"]), ("fe", "ABSENT"))
        for forbidden in (str(self.front), self.front.as_posix(), str(self.root), self.root.as_posix(), HygieneTests.SECRET):
            self.assertNotIn(forbidden, out + json.dumps(doc, ensure_ascii=False))
        self.assertEqual(sorted(store.read_scans(self.up)), sorted(fs.SCREENS))
        for word in FORBIDDEN:
            self.assertNotIn(word, out.lower(), word)

    def test_nothing_else_in_the_project_changes(self):
        before = {str(p.relative_to(self.root)).replace("\\", "/"): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.command("--repo", "fe").returncode, 0)
        after = {str(p.relative_to(self.root)).replace("\\", "/"): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        new = set(after) - set(before)
        self.assertTrue(new and all(n.startswith("desk/atlas/.atlas/ui/scan-") for n in new), sorted(new))
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)

    def test_a_dry_run_and_a_screen_filter(self):
        done = self.command("--repo", "fe", "--dry-run", "--screen", "SCR-LOGIN")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(list(self.up.state.glob("scan-*.json")), [])
        self.assertIn("dry run", self.text(done))
        self.assertNotIn("SCR-HOME sha=", self.text(done))
        self.assertEqual(self.command("--repo", "fe", "--screen", "SCR-LOGIN").returncode, 0)
        self.assertEqual([p.name for p in self.up.state.glob("scan-*.json")], ["scan-SCR-LOGIN.json"])

    def test_the_root_is_an_alias_and_never_a_path(self):
        for repo in (str(self.front), self.front.as_posix(), "../x", "C:/Users/someone/project", "~/frontend"):
            done = self.command("--repo", repo)
            text = self.text(done)
            self.assertEqual(done.returncode, 1, repo)
            self.assertIn("error:", text)
            self.assertNotIn(repo, text)                     # a value that looks like a path is never echoed
            self.assertNotIn(str(self.front), text)
            self.assertNotIn(self.front.as_posix(), text)
        nope = self.command("--repo", "nope")
        self.assertEqual(nope.returncode, 1)
        self.assertIn("no evidence root for alias 'nope'", self.text(nope))      # an alias is a plain name: it may be echoed
        self.assertEqual(list(self.up.state.glob("scan-*.json")), [])

    def test_writing_needs_the_write_switch_and_a_dry_run_does_not(self):
        env = {k: v for k, v in self.env.items() if k != "GEMARC_ALLOW_WRITE"}
        done = self.command("--repo", "fe", env=env)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(list(self.up.state.glob("scan-*.json")), [])
        self.assertEqual(self.command("--repo", "fe", "--dry-run", env=env).returncode, 0)

    def test_unknown_screen_a_bad_glob_and_a_project_without_screens(self):
        self.assertEqual(self.command("--repo", "fe", "--screen", "SCR-NOPE").returncode, 1)
        done = sc.run(self.ws, "desk", repo="fe", glob="a\\b", roots={"fe": self.front})
        self.assertEqual(done.exit_code, 1)
        from gemarc_mcp.atlas import tools
        self.assertEqual(tools.init(self.ws, "bare", "Bare", "asis", ["zh-CN"], repos=["main"])["status"], "created")
        bare = sc.run(self.ws, "bare", repo="fe", roots={"fe": self.front})
        self.assertEqual(bare.exit_code, 1)
        self.assertIn("no screen", bare.text)

    def test_the_verdict_rule_reads_what_the_command_wrote(self):
        from gemarc_mcp.atlas.protoui import verdict as vd
        self.assertEqual(self.command("--repo", "fe").returncode, 0)
        model = store.load_state(self.up, self.cfg.meta()).model
        stamp, n = um.screen_hashes(model)["SCR-LOGIN"], sum(1 for c in um.checkpoints(model) if c.screen == "SCR-LOGIN")
        text = f"#atlas-ui 1\n## conform: screen | verdict | basis | opts\nSCR-LOGIN | conforms | fe:pages/scr-login.html:10 | sha={stamp} checked={n} repo=fe\n"
        out = self.put("ui-verdict-fe", text, verdict_roots={"fe": self.front})
        self.assertEqual({d.rule for d in out.check.errors}, {"UV-006"}, out.check.render())
        self.assertIn("SCR-LOGIN.submit@label", out.check.render())
        self.assertIsNotNone(vd)


if __name__ == "__main__":
    unittest.main()
