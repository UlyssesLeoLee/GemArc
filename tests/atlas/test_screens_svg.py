"""The semantic SVG of a screen state and the checks ``SV-201`` to ``SV-216`` (``protoui/svg.py``, ``check.py``; design A3 4.3, 6.2, T-S1).

Every I-class check has a mutant: one edit of a built file (an SVG, a card, the gallery, the navigation page, the build-info) and the test says that
exactly that rule id is red; the clean baselines (login, the 12-screen sample, the showcase, 50 random models) have no finding at all; the L-class
checks degrade instead of failing and the T-class clause of SV-205 only runs when asked. The round trip (what the SVG says equals the model state)
and the consistency of card and SVG are counted over 50 random models: 100 percent.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_screens_svg.py"
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from gemarc_mcp.atlas.protoui import atl, build as bld, check as chk, expand as ex, layout as lay, model as um, svg as svgmod, vocab

from . import screens_fixtures as fx
from . import ui_synth

SRC = Path(__file__).resolve().parents[2] / "src"
LOGIN = "screens/SCR-LOGIN.svg"
I_RULES = {f"SV-{n}" for n in range(201, 217) if f"SV-{n}" != "SV-208"}


def findings(files: dict, model: dict, five=None, include_t: bool = False) -> list:
    return chk.check_files(files, model, five, fx.CFG, include_t=include_t)


def red(items: list, cls: str = "I") -> set:
    return {f.rule for f in items if f.cls == cls and f.severity == "ERROR"}


def login_files() -> dict:
    return dict(fx.built("login").files)


def number_in(text: str, pattern: str) -> int:
    return int(re.search(pattern, text).group(1))


def card_mutant(text_edit):
    """A mutant of the login card with a valid sentinel again (so that only the edit is wrong)."""
    def make(files):
        path = "screens/SCR-LOGIN.card.md"
        text = files[path].decode("utf-8")
        out = dict(files)
        out[path] = fx.refinalize(text_edit(text)).encode("utf-8")
        return out
    return make


def drop_first_edge(files):
    out = dict(files)
    text = files["screens/NAV.md"].decode("utf-8")
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if "-->" in ln)
    del lines[i]
    out["screens/NAV.md"] = fx.refinalize("\n".join(lines)).encode("utf-8")
    return out


def breaking_build_info(files):
    out = dict(files)
    info = json.loads(files["screens/build-info.json"].decode("utf-8"))
    info["uiSha8"] = "00000000"
    out["screens/build-info.json"] = (json.dumps(info, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return out


MUTANTS = [
    ("SV-201", "the root role", lambda f: fx.mutate(f, LOGIN, 'role="group"', 'role="img"')),
    ("SV-201", "the viewBox", lambda f: fx.mutate(f, LOGIN, 'viewBox="0 0 1280 828"', 'viewBox="0 0 1280 800"')),
    ("SV-201", "an aria reference to another element by id", lambda f: fx.mutate(f, LOGIN, 'role="group"', 'role="group" aria-owns="x"')),
    ("SV-201", "the schema stamp", lambda f: fx.mutate(f, LOGIN, 'data-ui-sha8="', 'data-ui-sha8="0')),
    ("SV-202", "a data-box that is not the box of the first rect", lambda f: fx.mutate(f, LOGIN, 'data-box="340 40 480 40"', 'data-box="341 40 480 40"')),
    ("SV-202", "a box outside its parent", lambda f: fx.mutate(fx.mutate(f, LOGIN, 'data-box="340 144 88 36"', 'data-box="340 144 88 600"'), LOGIN,
                                                               '<rect x="340" y="144" width="88" height="36"', '<rect x="340" y="144" width="88" height="600"')),
    ("SV-203", "a script", lambda f: fx.mutate(f, LOGIN, "</svg>", "<script>alert(1)</script>\n</svg>")),
    ("SV-203", "an external link", lambda f: fx.mutate(f, LOGIN, '<rect x="0" y="0" width="1280" height="800" class="p"/>',
                                                       '<rect x="0" y="0" width="1280" height="800" class="p" fill="url(http://example.invalid/x)"/>')),
    ("SV-205", "two sibling boxes that overlap", lambda f: fx.mutate(fx.mutate(f, "screens/SCR-HOME.svg", 'data-box="24 80 1232 76"', 'data-box="24 40 1232 76"'), "screens/SCR-HOME.svg",
                                                                     '<rect x="24" y="80" width="1232" height="76" class="b"/>', '<rect x="24" y="40" width="1232" height="76" class="b"/>')),
    ("SV-206", "a navigation id that is not in the model", lambda f: fx.mutate(f, LOGIN, 'data-nav="SCR-LOGIN.submit:go:SCR-HOME"', 'data-nav="SCR-LOGIN.submit:go:SCR-NOPE"')),
    ("SV-207", "a check point count on the card", card_mutant(lambda t: re.sub(r"checkpoints=(\d+)", lambda m: f"checkpoints={int(m.group(1)) - 1}", t, count=1))),
    ("SV-207", "a widget missing from the table of the card", card_mutant(lambda t: "\n".join(ln for ln in t.split("\n") if not ln.startswith("| 3 | `SCR-LOGIN.pwd`")))),
    ("SV-207", "a field name of a widget on the card", card_mutant(lambda t: t.replace("| form |  | email |", "| form |  | mail |", 1))),
    ("SV-207", "the kind of a state on the card", card_mutant(lambda t: t.replace("| `SCR-LOGIN#loading` | loading |", "| `SCR-LOGIN#loading` | empty |", 1))),
    ("SV-207", "the order of the children of a container", card_mutant(lambda t: t.replace("| email,pwd,err,submit |", "| pwd,email,err,submit |", 1))),
    ("SV-207", "the route of the screen", card_mutant(lambda t: t.replace("route=/login", "route=/logon", 1))),
    ("SV-209", "a font size below 12", lambda f: fx.mutate(f, LOGIN, ".s2{font-size:12px}", ".s2{font-size:10px}")),
    ("SV-209", "an interactive box below the minimum", lambda f: fx.mutate(fx.mutate(f, LOGIN, 'data-box="340 144 88 36"', 'data-box="340 144 20 20"'), LOGIN,
                                                                           '<rect x="340" y="144" width="88" height="36"', '<rect x="340" y="144" width="20" height="20"')),
    ("SV-210", "no CJK font in the stack", lambda f: fx.mutate(f, LOGIN, "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif", "sans-serif", count=0)),
    ("SV-211", "a label that differs from the model", lambda f: fx.mutate(f, LOGIN, 'aria-label="邮箱"', 'aria-label="邮箱!"')),
    ("SV-211", "a state without an SVG", lambda f: {k: v for k, v in f.items() if k != "screens/SCR-LOGIN~error.svg"}),
    ("SV-212", "the build-info", breaking_build_info),
    ("SV-213", "an inlined SVG that differs from the file", lambda f: fx.mutate(f, "site/screens/SCR-LOGIN.html", 'data-box="340 144 88 36"', 'data-box="340 144 88 37"')),
    ("SV-213", "a script in the gallery", lambda f: fx.mutate(f, "site/screens/SCR-LOGIN.html", "</body>", "<script>1</script>\n</body>")),
    ("SV-214", "an edge missing from the navigation diagram", drop_first_edge),
    ("SV-215", "a band note that is not the variant note", lambda f: fx.mutate(f, "screens/SCR-LOGIN~error.svg", ">邮箱或密码不正确。</text>", ">提交后等待服务器响应。</text>")),
    ("SV-216", "a data-n that is not the pre-order position", lambda f: fx.mutate(f, LOGIN, 'data-n="2"', 'data-n="9"')),
]


class BaselineTests(unittest.TestCase):
    def test_the_fixed_models_build_ok_and_check_clean_in_every_class(self):
        for name in ("login", "sample12", "showcase"):
            built = fx.built(name)
            self.assertEqual(built.status, "ok", name)
            self.assertEqual([f for f in built.findings if f.severity == "ERROR"], [], name)
            model = {"login": fx.login_model, "sample12": fx.sample12_model, "showcase": fx.showcase_model}[name]()
            self.assertEqual(findings(dict(built.files), model, include_t=True), [], name)

    def test_the_five_stamps_agree(self):
        built = fx.built("sample12")
        sha = um.ui_sha8(fx.sample12_model())
        info = json.loads(built.files["screens/build-info.json"])
        self.assertEqual((info["uiSha8"], info["modelSha8"], info["layoutVersion"], info["schema"], info["status"]), (sha, None, 1, um.BUILD_SCHEMA, "ok"))
        for path, data in built.files.items():
            text = data.decode("utf-8")
            if path.endswith(".svg"):
                self.assertIn(f'data-ui-sha8="{sha}"', text, path)
                self.assertNotIn("data-model-sha8", text, path)
            elif path.endswith(".md"):
                self.assertIn(f"u={sha}", text.split("\n")[1], path)
                self.assertNotIn(" m=", text.split("\n")[1], path)

    def test_every_rule_but_the_render_time_one_has_a_mutant(self):
        self.assertEqual({m[0] for m in MUTANTS} | {"SV-204"}, I_RULES, "SV-204 is the L-class size rule (see the degradation tests); SV-208 is the PNG probe of the render step")

    def test_the_class_of_every_rule_is_the_one_of_design_6_0(self):
        self.assertEqual({r for r, c in chk.RULES.items() if c == "L"}, {"SV-204"})
        self.assertEqual({r for r, c in chk.RULES.items() if c == "I"}, (I_RULES - {"SV-204"}) | {"SV-208"})


class MutantTests(unittest.TestCase):
    def test_each_mutant_is_red_with_exactly_its_rule(self):
        files, model = login_files(), fx.login_model()
        for rule, what, make in MUTANTS:
            got = findings(make(files), model, include_t=False)
            self.assertEqual(red(got), {rule}, (what, [(f.rule, f.message[:90]) for f in got]))

    def test_a_mutant_does_not_change_the_input_files(self):
        files = login_files()
        copy = dict(files)
        for _rule, _what, make in MUTANTS[:5]:
            make(files)
        self.assertEqual(files, copy)

    def test_the_t_class_clause_of_sv_205_runs_only_when_asked(self):
        circle = ('<circle cx="330" cy="50" r="9"', '<circle cx="400" cy="110" r="9"')
        files = fx.mutate(fx.mutate(login_files(), LOGIN, *circle), "site/screens/SCR-LOGIN.html", *circle)   # the gallery inlines the same SVG
        model = fx.login_model()
        quiet = chk.check_files(files, model, None, fx.CFG, include_t=False, regenerate=False)
        self.assertEqual((red(quiet), red(quiet, "T")), (set(), set()))
        loud = chk.check_files(files, model, None, fx.CFG, include_t=True, regenerate=False)
        self.assertEqual((red(loud), red(loud, "T")), (set(), {"SV-205"}))
        self.assertIn("SV-211", red(findings(files, model)), "the regeneration still sees that the file is not what the generator writes")

    def test_a_file_that_differs_from_its_regeneration_without_another_explanation_is_reported_by_its_family(self):
        files = login_files()
        cases = (("screens/SCR-LOGIN.svg", lambda t: t.replace('class="p"', 'class="p" opacity="1"', 1), "SV-211"),
                 ("screens/INDEX.md", lambda t: t.replace("登录", "登陆"), "SV-207"), ("screens/NAV.md", lambda t: t.replace("导航表", "导航表 "), "SV-214"))
        for path, edit, rule in cases:
            out = dict(files)
            text = edit(files[path].decode("utf-8"))
            self.assertNotEqual(text, files[path].decode("utf-8"), path)
            out[path] = (fx.refinalize(text) if path.endswith(".md") else text).encode("utf-8")
            got = findings(out, fx.login_model())
            self.assertIn(rule, red(got), (path, got))

    def test_links_that_point_into_a_5d_model_are_checked_when_there_is_one(self):
        from gemarc_mcp.atlas.protoui.expand import FiveIndex
        five = FiveIndex(nodes={"usr.member": "Actor", "svc.loan": "Module"}, edges=set(), requirements={"RQ-1"}, cases={"CS-BORROW-OK": {"R-BORROW"}}, machines={})
        model = fx.login_model()
        built = bld.build_screens(model, five, bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="abcdef12"))
        self.assertEqual(built.status, "ok")
        self.assertEqual(built.model_sha8, "abcdef12")
        svg = built.files[LOGIN].decode("utf-8")
        self.assertIn('data-model-sha8="abcdef12"', svg)
        self.assertIn(" m=abcdef12", built.files["screens/SCR-LOGIN.card.md"].decode("utf-8").split("\n")[1])
        cfg = bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="abcdef12")
        self.assertEqual(chk.check_files(dict(built.files), model, five, cfg, include_t=True), [])
        gone = FiveIndex(nodes={"usr.member": "Actor"}, edges=set(), requirements={"RQ-1"}, cases={"CS-BORROW-OK": {"R-BORROW"}}, machines={})
        self.assertIn("SV-206", red(chk.check_files(dict(built.files), model, gone, cfg)), "svc.loan is not in the 5D model any more")
        stamp = fx.mutate(dict(built.files), LOGIN, 'data-model-sha8="abcdef12"', 'data-model-sha8="abcdef13"')
        self.assertIn("SV-201", red(chk.check_files(stamp, model, five, cfg)))

    def test_an_expanded_5d_model_given_as_a_mapping_is_indexed_by_the_builder(self):
        five = {"nodes": [{"id": "usr.member", "kind": "Actor"}, {"id": "svc.loan", "kind": "Module"}], "edges": [], "requirements": [{"id": "RQ-1"}],
                "cases": [{"caseId": "CS-BORROW-OK", "events": [{"ruleId": "R-BORROW"}]}], "machines": []}
        cfg = bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="abcdef12")
        built = bld.build_screens(fx.login_model(), five, cfg)
        self.assertEqual((built.status, built.model_sha8), ("ok", "abcdef12"))
        self.assertEqual(chk.check_files(dict(built.files), fx.login_model(), five, cfg), [])

    def test_an_empty_5d_index_validates_nothing_and_writes_no_model_stamp(self):
        from gemarc_mcp.atlas.protoui.expand import FiveIndex
        built = bld.build_screens(fx.login_model(), FiveIndex(), bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="abcdef12"))
        self.assertIsNone(built.model_sha8)
        self.assertNotIn("data-model-sha8", built.files[LOGIN].decode("utf-8"))


class DamageTests(unittest.TestCase):
    """The checker reads files that may be anything: it never raises, and a file that is not what the generator wrote is always found."""

    def test_any_single_character_edit_of_any_file_is_found(self):
        files, model = login_files(), fx.login_model()
        names = sorted(p for p in files if p != "screens/build-info.json")      # the status of the build-info is ignored on purpose
        rng = random.Random(20261031)
        for _ in range(40):
            path = names[rng.randrange(len(names))]
            text = files[path].decode("utf-8")
            pos = rng.randrange(len(text))
            new = rng.choice('xX7 <>"&')
            if text[pos] == new:
                new = "q"
            damaged = dict(files)
            damaged[path] = (text[:pos] + new + text[pos + 1:]).encode("utf-8")
            self.assertTrue(red(findings(damaged, model)), (path, pos, new))

    def test_a_file_of_garbage_bytes_is_reported_and_never_raises(self):
        files, model = login_files(), fx.login_model()
        for path in sorted(files):
            damaged = dict(files)
            damaged[path] = bytes([0xFF, 0xFE, 0x00]) + b"garbage" + bytes([0x80])
            got = findings(damaged, model)
            self.assertTrue(red(got) or path == "screens/build-info.json", path)

    def test_an_svg_with_a_wrong_number_or_a_truncated_file_is_reported(self):
        files, model = login_files(), fx.login_model()
        for edit in (lambda t: t.replace('x="340"', 'x="3x0"', 1), lambda t: t[:len(t) // 2], lambda t: t.replace("<svg", "<svg ", 1) + "<", lambda t: ""):
            damaged = dict(files)
            damaged[LOGIN] = edit(files[LOGIN].decode("utf-8")).encode("utf-8")
            self.assertTrue(red(findings(damaged, model)))


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.files = login_files()
        self.text = self.files[LOGIN].decode("utf-8")
        self.root = ET.fromstring(self.text.encode("utf-8"))

    def test_the_root_carries_the_stable_attribute_set(self):
        for key in ("viewBox", "width", "height", "role", "aria-label", "data-schema", "data-screen", "data-variant", "data-frame", "data-layout", "data-ui-sha8"):
            self.assertIn(key, self.root.attrib)
        self.assertEqual((self.root.get("data-schema"), self.root.get("data-layout"), self.root.get("data-variant")), ("gemarc-atlas-screen/1", "1", "default"))
        self.assertEqual(self.root.get("viewBox"), f"0 0 {self.root.get('width')} {self.root.get('height')}")
        self.assertEqual(int(self.root.get("height")), 800 + 28, "the canvas plus the band of one line")

    def test_every_group_is_flat_with_a_parent_and_the_first_child_is_the_rect_of_its_box(self):
        ns = "{http://www.w3.org/2000/svg}"
        groups = [g for g in self.root.iter(ns + "g") if g.get("data-n")]
        self.assertEqual([g.get("data-n") for g in groups], ["1", "2", "3", "5"])
        for g in groups:
            self.assertEqual(g.find(ns + "g"), None, "no nested groups: a tree is data-parent, not nesting")
            first = g[0]
            self.assertEqual(first.tag, ns + "rect")
            self.assertEqual(g.get("data-box"), " ".join(first.get(k) for k in ("x", "y", "width", "height")))
        self.assertEqual(groups[1].get("data-parent"), "SCR-LOGIN.form")
        self.assertEqual(groups[0].get("data-parent"), "SCR-LOGIN.root")
        submit = groups[3]
        self.assertEqual((submit.get("data-goto"), submit.get("data-on"), submit.get("data-api"), submit.get("data-case"), submit.get("data-rule"), submit.get("data-emph")),
                         ("SCR-HOME", "submit", "svc.loan", "CS-BORROW-OK", "R-BORROW", "primary"))

    def test_the_marks_are_numbers_with_data_mark_and_containers_have_none(self):
        marks = re.findall(r'<text x="\d+" y="\d+" class="mk" data-mark="(\d+)">(\d+)</text>', self.text)
        self.assertEqual(marks, [("2", "2"), ("3", "3"), ("5", "5")])

    def test_a_widget_that_shows_in_other_states_only_is_named_in_the_description_and_the_card(self):
        desc = re.search(r"<desc>(.*?)</desc>", self.text).group(1)
        self.assertIn("SCR-LOGIN.err", desc)
        self.assertIn("本状态显示 4 / 共 5", desc)
        error = self.files["screens/SCR-LOGIN~error.svg"].decode("utf-8")
        self.assertNotIn("未显示", re.search(r"<desc>(.*?)</desc>", error).group(1))
        self.assertIn('data-in="error"', error)
        self.assertNotIn("data-in=", self.text.replace('data-in="error"', ""))

    def test_the_state_band_says_wireframe_sketch_in_visible_text(self):
        self.assertIn("示意线框", self.text)
        en = bld.build_screens(fx.login_model(), None, bld.ScreensConfig(lang="en-US", project="demo")).files[LOGIN].decode("utf-8")
        self.assertIn("wireframe sketch", en)
        self.assertIn("Contains:", en)
        self.assertIn("Excludes:", en)

    def test_there_is_no_outline_image_script_or_external_reference_and_no_denied_attribute(self):
        for path, data in fx.built("sample12").files.items():
            if not path.endswith(".svg"):
                continue
            text = data.decode("utf-8")
            for bad in ("<image", "<foreignObject", "<script", "href=", "url("):
                self.assertNotIn(bad, text, (path, bad))
            self.assertNotRegex(text, r"aria-(?!label=)", path)
            self.assertTrue(text.endswith("</svg>\n") and "\r" not in text, path)

    def test_the_svg_files_are_well_formed_xml_and_every_line_is_short(self):
        longest = 0
        for path, data in fx.built("showcase").files.items():
            if path.endswith(".svg"):
                ET.fromstring(data)
                longest = max(longest, max(len(x) for x in data.decode("utf-8").split("\n")))
        self.assertLess(longest, 500)

    def test_text_that_is_not_ascii_xml_safe_is_escaped_and_the_svg_stays_well_formed(self):
        built = fx.built("sample12")
        self.assertEqual(built.status, "ok")
        hostile = bld.build_screens(fx.model_of(fx.hostile_text()), None, fx.CFG)
        self.assertEqual(hostile.status, "ok", [f.message for f in hostile.findings if f.severity == "ERROR"])
        for path, data in hostile.files.items():
            if path.endswith(".svg"):
                ET.fromstring(data)
        self.assertIn("He said &quot;hi&quot; &amp; &lt;left&gt;", hostile.files["screens/SCR-HOSTILE.svg"].decode("utf-8"))


class SizeTests(unittest.TestCase):
    def test_svg_sizes_of_the_12_screen_sample_against_the_budget(self):
        sizes = sorted(len(d) for p, d in fx.built("sample12").files.items() if p.endswith(".svg"))
        self.assertEqual(len(sizes), 35)
        self.assertLess(sizes[-1], 20000)
        self.assertLess(sizes[len(sizes) // 2], 6000, "the median SVG is a few KB")
        self.assertLess(sum(sizes), 200000)


class RoundTripTests(unittest.TestCase):
    """T-S1: the round trip is 100 percent over 50 random synthetic models, a card is consistent with its SVGs, two builds are byte identical."""

    def models(self):
        for seed in range(1, 51):
            spec = ui_synth.generate(screens=2 + seed % 9, seed=100 + seed, lang="zh" if seed % 2 else "en")
            yield seed, ex.expand_ui([atl.parse_atl(t, n) for n, t in sorted(ui_synth.render(spec, per_slice=1 + seed % 4).items())]).model

    def test_fifty_random_models_build_ok_round_trip_and_stay_consistent(self):
        screens = consistent = states = 0
        for seed, model in self.models():
            built = bld.build_screens(model, None, fx.CFG)
            self.assertEqual(built.status, "ok", (seed, [f.message for f in built.findings if f.severity == "ERROR"][:2]))
            found = chk.check_files(dict(built.files), model, None, fx.CFG, include_t=True)
            self.assertEqual(found, [], (seed, [(f.rule, f.message[:80]) for f in found][:3]))
            again = bld.build_screens(model, None, fx.CFG)
            self.assertEqual(again.files, built.files, seed)
            screens += len(model["screens"])
            states += sum(len(s["variantNames"]) for s in model["screens"])
            consistent += sum(1 for s in model["screens"] if not {f.rule for f in found if f.path.startswith("screens/") and f.rule in ("SV-207", "SV-211")})
        self.assertEqual(consistent, screens, "card and SVG consistency")
        self.assertGreater(states, 400)

    def test_the_build_is_independent_of_the_hash_seed(self):
        code = ("import hashlib,sys\nfrom tests.atlas import screens_fixtures as fx\n"
                "b = fx.built('showcase')\nh = hashlib.sha256()\n"
                "for p in sorted(b.files): h.update(p.encode()); h.update(b.files[p])\nprint(h.hexdigest())")
        digests = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(SRC) + os.pathsep + str(Path(__file__).resolve().parents[2]), PYTHONIOENCODING="utf-8")
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parents[2]), timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1)
        local = hashlib.sha256()
        for p in sorted(fx.built("showcase").files):
            local.update(p.encode())
            local.update(fx.built("showcase").files[p])
        self.assertEqual(digests, {local.hexdigest()})


if __name__ == "__main__":
    unittest.main()
