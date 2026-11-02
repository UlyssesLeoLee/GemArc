"""The screens tree as a whole (``protoui/build.py``, ``card.py``, ``gallery.py``, ``nav.py``, ``index.py``): cards, index and navigation pages, the gallery,
the build-info, the anchors for ``ids.tsv``, degradation (design 6.0 class L, T-R12 for screens), purity of the builder, the fixed sentences, the goldens.

Goldens: the whole output of the two-screen login example (``ui_fixtures/screens/login/``) byte for byte, and the path, size and SHA-256 of every file of the
12-screen sample and of the showcase (``ui_fixtures/screens/*.manifest.tsv``). Rewrite them after a deliberate change (and raise ``LAYOUT_VERSION`` when boxes
moved):  PYTHONPATH=src python -m tests.atlas.test_screens_build --write-golden

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_screens_build.py"
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas.protoui import build as bld, card as cardmod, check as chk, gallery as gal, labels, model as um, svg as svgmod
from gemarc_mcp.atlas.reading import api as reading

from . import screens_fixtures as fx

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "ui_fixtures" / "screens"
PROTOUI = HERE.parents[1] / "src" / "gemarc_mcp" / "atlas" / "protoui"
CHECKED = ("sample12", "showcase")


def lines_of(files: dict, path: str) -> list:
    return files[path].decode("utf-8").split("\n")


def manifest_text(files: dict) -> str:
    return "".join(f"{p}\t{len(b)}\t{hashlib.sha256(b).hexdigest()}\n" for p, b in sorted(files.items()))


def golden_files() -> dict:
    out = {}
    for path, data in fx.built("login").files.items():
        out[GOLDEN / "login" / path] = data
    for name in CHECKED:
        out[GOLDEN / f"{name}.manifest.tsv"] = manifest_text(fx.built(name).files).encode("utf-8")
    return out


class CardTests(unittest.TestCase):
    def test_every_card_is_a_page_with_a_valid_frame_the_screen_bucket_envelope_and_at_most_9000_bytes(self):
        for name in ("login", "sample12", "showcase"):
            built = fx.built(name)
            for path, data in built.files.items():
                if ".card" not in path:
                    continue
                text = data.decode("utf-8")
                self.assertEqual(reading.verify_sentinel(text), [], path)
                self.assertLessEqual(len(data), reading.BUDGETS["cap_md"]["page"], path)
                rows = text.split("\n")
                top = next(k for k, ln in enumerate(rows) if ln.startswith("> 读法")) + 2
                bottom = next(k for k, ln in enumerate(rows) if ln.startswith("邻页:")) - 1
                self.assertLessEqual(len("\n".join(rows[:top] + rows[bottom:]).encode("utf-8")), reading.envelope_limit("CARD:page"), path)
                first, second = text.split("\n")[:2]
                self.assertRegex(first, r"^页 §CARD-[A-Za-z0-9._~+-]+ · .+ · \d+/\d+ · 共 \d+ 行 · 页类 page$")
                self.assertIn("u=" + um.ui_sha8({"sample12": fx.sample12_model, "showcase": fx.showcase_model, "login": fx.login_model}[name]()), second)

    def test_the_widget_table_lists_every_widget_with_its_circle_number_and_the_state_table_every_state(self):
        model = fx.sample12_model()
        files = fx.built("sample12").files
        for s in model["screens"]:
            enc = reading.enc(s["id"])
            lines = lines_of(files, f"screens/{enc}.card.md")
            rows = chk._table_rows(lines, "控件")
            self.assertEqual([r[1].strip("`") for r in rows], s["widgetIds"], s["id"])
            self.assertEqual([r[0] for r in rows], [str(i + 1) for i in range(len(s["widgetIds"]))], "the # column is the circle number")
            states = chk._table_rows(lines, "状态")
            self.assertEqual([r[0].strip("`") for r in states], [um.variant_id(s["id"], v) for v in s["variantNames"]])
            text = "\n".join(lines)
            self.assertEqual(int(re.search(r"checkpoints=(\d+)", text).group(1)), sum(1 for c in um.checkpoints(model) if c.screen == s["id"]))

    def test_the_card_carries_every_visible_string_with_the_states_it_appears_in(self):
        files = fx.built("login").files
        lines = lines_of(files, "screens/SCR-LOGIN.card.md")
        strings = cardmod.visible_strings(lines, "zh-CN")
        for expected in ("邮箱", "密码", "登录", "邮箱或密码不正确"):
            self.assertIn(expected, strings)
        self.assertTrue(any("loading" in s and "示意线框" in s for s in strings), "the state band text of every state is listed")
        self.assertIn("全部", "\n".join(lines), "a string shown by every state is listed once, for all states")

    def test_derived_elements_are_listed_apart_from_authored_text(self):
        lines = lines_of(fx.built("showcase").files, "screens/SCR-DASH.card.md")
        derived = chk._table_rows(lines, "渲染器推导")
        self.assertEqual({r[0] for r in derived}, {"无示例数据的灰条", "加载骨架块", "未画行提示", "空态占位"})
        self.assertIn("（空）", [r[2] for r in derived if r[0] == "空态占位"])

    def test_every_anchor_of_ids_points_to_the_row_of_its_id_and_to_a_heading(self):
        for name in ("login", "sample12", "showcase"):
            built = fx.built(name)
            seen = set()
            for rid, kind, role, path, line, hdr, label in built.ids:
                lines = lines_of(built.files, path)
                self.assertIn(f"`{rid}`", lines[line - 1], (name, rid, path, line))
                self.assertTrue(lines[hdr - 1].startswith("## "), (name, rid, path, hdr))
                self.assertLess(hdr, line)
                self.assertEqual(role, "home")
                self.assertNotIn("\t", label)
                seen.add((rid, kind))
            model = {"login": fx.login_model, "sample12": fx.sample12_model, "showcase": fx.showcase_model}[name]()
            want = ({(s["id"], "screen") for s in model["screens"]} | {(w["id"], "widget") for w in model["widgets"]} |
                    {(v["id"], "variant") for s in model["screens"] for v in [{"id": um.variant_id(s["id"], n)} for n in s["variantNames"]]} | {(n["id"], "nav") for n in model["nav"]})
            self.assertEqual(seen, want, name)

    def test_a_card_that_does_not_fit_a_page_continues_at_a_row_seam_with_the_identity_line_and_the_header_repeated(self):
        model = fx.model_of(fx.stress_text(60, 5, 2))
        total = len(model["widgets"])
        built = bld.build_screens(model, None, fx.CFG)
        pages = [p for p in built.files if ".card" in p]
        self.assertEqual(sorted(pages), ["screens/SCR-BIG.card-2.md", "screens/SCR-BIG.card.md"])
        first, second = lines_of(built.files, "screens/SCR-BIG.card.md"), lines_of(built.files, "screens/SCR-BIG.card-2.md")
        self.assertIn("1/2", first[0])
        self.assertIn("2/2", second[0])
        self.assertTrue(any(ln.startswith("> 续:") for ln in second), "the continuation line of the scope card")
        self.assertIn("（未完：下一部分在 §CARD-SCR-BIG-2）", first)
        self.assertIn("（本单元到此结束）", second)
        ident = next(ln for ln in first if ln.startswith("屏幕 `SCR-BIG`"))
        self.assertIn(ident, second)
        self.assertTrue(any(re.match(r"^## .+（\d+ / \d+，第 \d+-\d+ 行）$", ln) for ln in first), "a table that is cut says which of its rows the part holds")
        self.assertTrue(any(re.match(r"^## .+（\d+ / \d+，第 \d+-\d+ 行）$", ln) for ln in second))
        for p in pages:
            self.assertLessEqual(len(built.files[p]), 9000)
            self.assertEqual(reading.verify_sentinel(built.files[p].decode("utf-8")), [])
        ids = [r[1].strip("`") for part in (first, second) for r in chk._table_rows(part, "控件")]
        self.assertEqual(len(ids), total)
        self.assertEqual(len(set(ids)), total, "every widget is in exactly one part")
        self.assertEqual([f for f in chk.check_files(dict(built.files), model, None, fx.CFG, include_t=True) if f.cls != "L"], [])


class IndexNavTests(unittest.TestCase):
    def test_the_index_lists_every_screen_once_with_its_counts_and_the_requirement_coverage(self):
        files = fx.built("sample12").files
        lines = lines_of(files, "screens/INDEX.md")
        rows = chk._table_rows(lines, "界面")
        self.assertEqual([r[0].strip("`") for r in rows], [s["id"] for s in fx.sample12_model()["screens"]])
        by_id = {r[0].strip("`"): r for r in rows}
        self.assertEqual(by_id["SCR-LOGIN"][5:9], ["3", "6", "29", "2/2"])
        self.assertEqual(by_id["SCR-LOGIN"][-1], "[§CARD-SCR-LOGIN](SCR-LOGIN.card.md)")
        self.assertEqual(chk._table_rows(lines, "需求覆盖"), [["`RQ-1`", "SCR-BOOK, SCR-HOME, SCR-LOGIN"]])
        self.assertIn("gemarc_atlas_index scope=\"screens\"", "\n".join(lines), "the live state of the PNGs is not in a static file, a pointer is")

    def test_the_navigation_page_has_the_diagram_the_aliases_and_the_table(self):
        files = fx.built("sample12").files
        text = files["screens/NAV.md"].decode("utf-8")
        block = text.split("```mermaid\n", 1)[1].split("\n```", 1)[0].split("\n")
        self.assertEqual(block[0], "flowchart LR")
        from gemarc_mcp.atlas.protoui import nav
        self.assertEqual(nav.count_diagram(block), (12, 30))
        aliases = chk._table_rows(text.split("\n"), "别名对照")
        self.assertEqual([r[0] for r in aliases], [f"n{i}" for i in range(1, 13)])
        self.assertEqual({r[1].strip("`") for r in aliases if r[2] == "start"}, {"SCR-LOGIN", "SCR-RETURN", "SCR-STOCK"})
        self.assertEqual(len(chk._table_rows(text.split("\n"), "导航表")), 30)

    def test_unreachable_screens_and_dead_ends_are_listed(self):
        text = fx.built("showcase").files["screens/NAV.md"].decode("utf-8")
        self.assertEqual(chk._table_rows(text.split("\n"), "不可达屏幕"), [["`SCR-ALIGN`"]])
        from gemarc_mcp.atlas.protoui import nav
        declared, unreachable, dead = nav.reachability(fx.showcase_model())
        self.assertEqual((declared, unreachable, dead), (True, ["SCR-ALIGN"], []))
        none = nav.reachability(fx.model_of(fx.many_screens_text(3).replace(" start=true", "")))
        self.assertEqual(none[:2], (False, []), "no start screen, no judgement of reachability")

    def test_more_than_40_screens_without_a_group_are_cut_into_numbered_parts_that_link_each_other(self):
        model = fx.model_of(fx.many_screens_text(45))
        built = bld.build_screens(model, None, fx.CFG)
        self.assertEqual(built.status, "ok")
        nav_parts = sorted(p for p in built.files if re.fullmatch(r"screens/NAV(-\d+)?\.md", p))
        index_parts = sorted(p for p in built.files if re.fullmatch(r"screens/INDEX(-\d+)?\.md", p))
        self.assertGreaterEqual(len(nav_parts), 2)
        self.assertGreaterEqual(len(index_parts), 2)
        self.assertIn("screens/NAV-2.md", nav_parts)
        self.assertIn("screens/INDEX-2.md", index_parts)
        for p in nav_parts + index_parts:
            text = built.files[p].decode("utf-8")
            self.assertLessEqual(len(text.encode("utf-8")), reading.BUDGETS["cap_md"]["card"], p)
            family = "NAV" if "NAV" in p else "SCREENS"
            count = len(nav_parts if "NAV" in p else index_parts)
            for k in range(1, count + 1):
                self.assertIn(f"§{family}" + ("" if k == 1 else f"-{k}"), text, (p, k))
        rows = sum(len(chk._table_rows(lines_of(built.files, p), "界面")) for p in index_parts)
        self.assertEqual(rows, 45)
        self.assertEqual(sum(len(chk._table_rows(lines_of(built.files, p), "导航表")) for p in nav_parts), 44)
        self.assertEqual(chk.check_files(dict(built.files), model, None, fx.CFG), [])
        self.assertIn("RS-101", built.degraded)

    def test_groups_decide_the_parts_when_there_are_groups(self):
        model = fx.model_of(fx.grouped_screens_text(80, 4))
        built = bld.build_screens(model, None, fx.CFG)
        parts = sorted(p for p in built.files if re.fullmatch(r"screens/NAV(-\d+)?\.md", p))
        self.assertEqual(len(parts), 4)
        for p in parts:
            groups = set(re.findall(r'subgraph g\d+\["(g\d)"\]', built.files[p].decode("utf-8")))
            self.assertEqual(len(groups), 1, p)
        self.assertEqual(chk.check_files(dict(built.files), model, None, fx.CFG), [])


class GalleryTests(unittest.TestCase):
    def test_the_gallery_wraps_the_widgets_that_start_a_jump_and_deleting_the_wrappers_gives_the_svg_back(self):
        built = fx.built("login")
        text = built.files["site/screens/SCR-LOGIN.html"].decode("utf-8")
        self.assertIn('<a href="SCR-HOME.html">', text)
        svg = built.files["screens/SCR-LOGIN.svg"].decode("utf-8")
        wrapped = gal.wrap_links(svg, "SCR-LOGIN", {"SCR-LOGIN": "SCR-LOGIN", "SCR-HOME": "SCR-HOME"})
        self.assertGreater(len(wrapped), len(svg.rstrip("\n").split("\n")))
        self.assertEqual("\n".join(gal.strip_links(wrapped)) + "\n", svg)

    def test_a_jump_to_a_state_goes_to_the_figure_of_that_state(self):
        text = fx.built("showcase").files["site/screens/SCR-FORMX.html"].decode("utf-8")
        self.assertIn('<a href="SCR-DASH.html">', text)
        phone = fx.built("showcase").files["site/screens/SCR-PHONE.html"].decode("utf-8")
        self.assertIn('<a href="#st-empty">', phone, "SCR-PHONE.go leads to the empty state of the same screen")
        self.assertIn('<figure id="st-empty">', phone)

    def test_a_gallery_has_no_script_no_event_attribute_and_no_external_reference(self):
        for name in CHECKED:
            for path, data in fx.built(name).files.items():
                if not path.startswith("site/"):
                    continue
                text = data.decode("utf-8")
                self.assertNotRegex(text, r"<script|<[^>]*\son[a-z]+\s*=")
                self.assertNotRegex(text, r"(?:src|href)=\"(?:https?:|//|data:)")
                self.assertLessEqual(len(data), 40000, path)
                self.assertTrue(text.startswith("<!doctype html>\n") and text.endswith("</html>\n"))
                self.assertIn("default-src 'none'", text)

    def test_the_first_line_and_the_sentinel_use_logical_counts_and_the_sha8_of_the_card(self):
        built = fx.built("login")
        text = built.files["site/screens/SCR-LOGIN.html"].decode("utf-8")
        card = built.files["screens/SCR-LOGIN.card.md"].decode("utf-8")
        sha = re.search(r"sha8 ([0-9a-f]{8})", card.split("\n")[-2]).group(1)
        self.assertIn(f"sha8 {sha}", text)
        self.assertRegex(text, r"页 §CARD-SCR-LOGIN · .+ · 1/1 · 含 \d+ 个块、\d+ 张表 · 页类 page")
        tables = text.count("<table>")
        self.assertIn(f"{tables} 张表", text)

    def test_html_off_writes_no_site_files_and_names_no_twin(self):
        built = bld.build_screens(fx.login_model(), None, bld.ScreensConfig(lang="zh-CN", project="demo", html=False))
        self.assertEqual([p for p in built.files if p.startswith("site/")], [])
        self.assertTrue(all(r["twin"] is None for r in built.records if r["kind"] in ("card", "index", "nav")))
        self.assertEqual(built.twins, {})
        self.assertEqual(built.status, "ok")

    def test_every_card_part_has_the_gallery_page_as_its_twin(self):
        built = bld.build_screens(fx.model_of(fx.stress_text(60, 5, 2)), None, fx.CFG)
        self.assertEqual(built.twins, {"screens/SCR-BIG.card.md": "site/screens/SCR-BIG.html", "screens/SCR-BIG.card-2.md": "site/screens/SCR-BIG.html"})
        for path, twin in built.twins.items():
            self.assertEqual(reading.twin_path(path), twin)


class DegradationTests(unittest.TestCase):
    """T-R12 for screens: every limit an author's data can reach degrades, the build stays ok, the reply names the screen."""

    def build(self, controls, states, tables=0, **kw):
        model = fx.model_of(fx.stress_text(controls, states, tables, **kw))
        built = bld.build_screens(model, None, fx.CFG)
        self.assertEqual(built.status, "ok", [f.message for f in built.errors])
        found = chk.check_files(dict(built.files), model, None, fx.CFG, include_t=True)
        self.assertEqual([f for f in found if f.cls != "L"], [], "only the class L warnings of an oversize screen may remain")
        return built

    def test_30_controls_in_5_states_inline_only_the_default_state_and_say_which_screen_to_split(self):
        built = self.build(30, 5)
        self.assertIn("SV-213", built.degraded)
        self.assertTrue(any(r.startswith("split screen SCR-BIG:") and "30 controls x 6 states exceed the gallery size" in r for r in built.repairs), built.repairs)
        text = built.files["site/screens/SCR-BIG.html"].decode("utf-8")
        self.assertEqual(text.count("<figure "), 1)
        self.assertIn("未内联", text)
        self.assertIn('href="../../screens/SCR-BIG~s1.svg"', text)
        self.assertLessEqual(len(text), 40000)

    def test_40_controls_in_4_states_with_four_tables_reduce_the_sample_rows_until_each_svg_fits(self):
        built = self.build(40, 4, tables=4)
        self.assertIn("SV-204", built.degraded)
        self.assertEqual(built.sample_rows["SCR-BIG"], 0, "5 rows give a 24 KB SVG, 3 rows 22 KB, none 19 KB")
        sizes = [len(d) for p, d in built.files.items() if p.endswith(".svg")]
        self.assertTrue(max(sizes) <= 20000 and len(sizes) == 5, sizes)
        self.assertTrue(any("sample table rows reduced from 5 to 0" in f.message for f in built.findings))

    def test_60_controls_beyond_the_budget_even_without_rows_are_a_warning_with_a_repair_line_not_a_failure(self):
        built = self.build(60, 5, tables=2)
        self.assertTrue(any(f.rule == "SV-204" and f.severity == "WARN" and f.cls == "L" for f in built.findings))
        self.assertTrue(any(r.startswith("split screen SCR-BIG: 60 widgets in one screen") for r in built.repairs), built.repairs)
        self.assertTrue(any(r.endswith("consider fewer widgets or states") and "needs 2 pages" in r for r in built.repairs))

    def test_the_repair_line_names_the_slice_when_the_configuration_knows_it(self):
        model = fx.model_of(fx.stress_text(30, 5))
        built = bld.build_screens(model, None, bld.ScreensConfig(lang="zh-CN", project="demo", slices={"SCR-BIG": "ui-20-big"}))
        self.assertTrue(any(r.startswith("slice ui-20-big: split screen SCR-BIG") for r in built.repairs), built.repairs)

    def test_a_gallery_above_the_budget_even_with_one_state_is_a_warning(self):
        built = self.build(100, 3, tables=3)
        self.assertTrue(any(f.rule == "SV-213" and f.severity == "WARN" and "even with one state" in f.message for f in built.findings))

    def test_hostile_text_builds_ok_and_stays_consistent(self):
        model = fx.model_of(fx.hostile_text())
        built = bld.build_screens(model, None, fx.CFG)
        self.assertEqual(built.status, "ok", [f.message for f in built.errors])
        self.assertEqual(chk.check_files(dict(built.files), model, None, fx.CFG, include_t=True), [])
        card = built.files["screens/SCR-HOSTILE.card.md"].decode("utf-8")
        self.assertNotIn("{lines}", card)
        self.assertNotIn("{sha8}", card)
        self.assertIn("{ lines}", card, "the frame placeholders in an author text are defused with a blank")
        self.assertNotRegex(card.split("## 控件")[1].split("## 状态")[0].replace("`SCR", ""), r"`tick`")
        self.assertTrue(all(len(x) <= 500 for x in card.split("\n")))
        self.assertTrue(any("see note" in x or "见注" in x for x in card.split("\n")), "a cell above 480 characters is folded into a note")

    def test_a_chain_deeper_than_the_layout_recurses_builds_ok_and_names_the_widgets_it_does_not_draw(self):
        model = fx.model_of(fx.deep_text(60))
        built = bld.build_screens(model, None, fx.CFG)
        self.assertEqual(built.status, "ok")
        card = built.files["screens/SCR-DEEP.card.md"].decode("utf-8")
        self.assertIn("(depth)", card)
        self.assertIn("(parent)", card)

    def test_a_model_without_screens_builds_nothing_and_does_not_raise(self):
        result = bld.build_screens(um.empty_model(), None, None)
        self.assertEqual((result.files, result.status, result.info["status"]), ({}, "ok", "empty"))

    def test_a_screen_without_any_widget_still_has_every_artifact(self):
        model = fx.model_of("#atlas-ui 1\n## screens: id | title | purpose | opts\nSCR-EMPTY | Empty | Nothing. | kind=page route=/e start=true\n")
        built = bld.build_screens(model, None, fx.CFG)
        self.assertEqual(built.status, "ok")
        self.assertEqual(sorted(p for p in built.files if "EMPTY" in p), ["screens/SCR-EMPTY.card.md", "screens/SCR-EMPTY.svg", "site/screens/SCR-EMPTY.html"])
        self.assertEqual(chk.check_files(dict(built.files), model, None, fx.CFG), [])


class BuildInfoAndRecordTests(unittest.TestCase):
    def test_the_build_info_is_canonical_json_with_no_time_and_no_path(self):
        built = fx.built("sample12")
        data = built.files["screens/build-info.json"]
        info = json.loads(data)
        self.assertEqual(sorted(info), ["checkpoints", "degraded", "layoutVersion", "modelSha8", "schema", "screens", "states", "status", "uiSha8", "widgets"])
        self.assertEqual((info["screens"], info["states"], info["widgets"], info["checkpoints"]), (12, 35, 79, 316))
        self.assertTrue(data.endswith(b"\n") and b"\r" not in data and b": " not in data)
        self.assertNotRegex(data.decode("utf-8"), r"[A-Za-z]:[\\/]|/Users/|\d{4}-\d{2}-\d{2}")

    def test_the_records_name_every_file_once_with_its_kind_and_the_trust_root_is_marked(self):
        built = fx.built("showcase")
        self.assertEqual(sorted(r["path"] for r in built.records), sorted(built.files))
        kinds = {r["path"]: r["kind"] for r in built.records}
        self.assertEqual(kinds["screens/build-info.json"], "build-info")
        self.assertFalse(next(r for r in built.records if r["kind"] == "build-info")["manifest"])
        self.assertTrue(all(r["ai"] for r in built.records if r["kind"] in ("svg", "card", "index", "nav")))
        self.assertTrue(all(not r["ai"] for r in built.records if r["kind"] == "gallery"))
        self.assertEqual({r["twinBy"] for r in built.records if r["kind"] in ("index", "nav")}, {"reading"})

    def test_ja_jp_uses_the_english_sentences_and_keeps_the_language_in_the_page(self):
        built = bld.build_screens(fx.login_model(), None, bld.ScreensConfig(lang="ja-JP", project="demo"))
        self.assertEqual(built.status, "ok")
        card = built.files["screens/SCR-LOGIN.card.md"].decode("utf-8")
        self.assertRegex(card, r"(?m)^## Widgets \(\d+\)$")
        self.assertIn(" · ja-JP · ", card.split("\n")[1])
        self.assertIn("wireframe sketch", built.files["screens/SCR-LOGIN.svg"].decode("utf-8"))
        self.assertIn('<html lang="ja-JP">', built.files["site/screens/SCR-LOGIN.html"].decode("utf-8"))

    def test_the_config_can_be_given_as_a_mapping_an_object_or_nothing(self):
        model = fx.login_model()
        a = bld.build_screens(model, None, None)
        b = bld.build_screens(model, None, {"lang": "zh-CN", "project": "project"})
        self.assertEqual(a.files, b.files)

        class Cfg:
            lang, project, model_sha8, html, slices = "zh-CN", "project", None, True, {}
        self.assertEqual(bld.build_screens(model, None, Cfg()).files, a.files)


class PurityTests(unittest.TestCase):
    """``build_screens`` is a pure function of its arguments: the modules import nothing that reads a clock, a path, the environment or a network."""

    MODULES = ("build", "card", "check", "gallery", "index", "labels", "layout", "nav", "svg", "textfit")
    FORBIDDEN = {"os", "subprocess", "socket", "time", "datetime", "random", "secrets", "tempfile", "shutil", "urllib", "http", "multiprocessing", "threading", "mcp", "glob"}

    def test_no_module_of_the_builder_imports_a_source_of_impurity(self):
        for name in self.MODULES:
            tree = ast.parse((PROTOUI / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
                for module in modules:
                    self.assertNotIn(module.split(".")[0], self.FORBIDDEN, (name, module))

    def test_only_the_label_loader_reads_a_file_and_nothing_writes_one(self):
        for name in self.MODULES:
            text = (PROTOUI / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\.(write_text|write_bytes|mkdir|unlink|rmtree)\(|\bopen\(", name)
            if name != "labels":
                self.assertNotRegex(text, r"read_bytes\(|read_text\(", name)

    def test_the_builder_imports_the_reading_surface_only_through_its_frozen_api(self):
        for name in self.MODULES:
            text = (PROTOUI / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotRegex(text, r"from \.\.reading\.(names|envelope|budgets)|from \.\.reading import (names|envelope|budgets)|import gemarc_mcp\.atlas\.reading\.(names|envelope|budgets)", name)

    def test_build_screens_does_not_touch_its_arguments(self):
        import copy
        model = fx.showcase_model()
        before = copy.deepcopy(model)
        bld.build_screens(model, None, fx.CFG)
        self.assertEqual(model, before)


class SentenceTests(unittest.TestCase):
    """The fixed sentences (``protoui/i18n``): every key the code uses is there in both languages with the same placeholders."""

    def keys_in_code(self) -> set:
        found = set()
        for path in PROTOUI.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r'(?:\bt|labels\.t|labels\.raw)\(\s*(?:[a-z_.]+,\s*)?"([a-z0-9_.]+)"', text):
                if not m.group(1).endswith("."):         # a prefix of a key built at run time: the DYNAMIC list names those keys
                    found.add(m.group(1))
        return found

    DYNAMIC = (["svg.reason." + k for k in ("in", "parent", "depth")] + ["card.derived." + k for k in ("skeleton", "placeholder", "more", "bar")] +
               ["index.col." + k for k in ("id", "title", "route", "frame", "start", "states", "widgets", "checkpoints", "nav", "svg", "card")] +
               ["nav.col." + k for k in ("id", "from", "to", "label", "guard", "on", "links", "page")])

    def test_every_key_the_code_uses_exists_in_both_languages_with_the_same_placeholders(self):
        zh, en = labels.flatten(labels.table("zh-CN")), labels.flatten(labels.table("en-US"))
        self.assertEqual(set(zh), set(en))
        for key in sorted(self.keys_in_code() | set(self.DYNAMIC)):
            self.assertIn(key, zh, key)
            self.assertEqual(labels.holes(zh[key]), labels.holes(en[key]), key)

    def test_no_sentence_is_unused(self):
        used = self.keys_in_code() | set(self.DYNAMIC)
        # keys written as part of a dotted prefix in the code (``"card.t." ...``) are listed here
        prefixes = {"card.t.", "card.col.", "svg.band.", "svg.word.", "svg.desc.", "svg.placeholder.", "gallery.", "index.t.", "nav.t.", "card.src.", "index.shards", "nav.shards"}
        for key in labels.flatten(labels.table("zh-CN")):
            self.assertTrue(key in used or any(key.startswith(p) for p in prefixes), key)

    def test_the_english_file_has_no_cjk_and_the_chinese_sentences_that_say_something_have_cjk(self):
        cjk = re.compile("[　-鿿]")
        en = labels.flatten(labels.table("en-US"))
        self.assertEqual([k for k, v in en.items() if cjk.search(v)], [])
        zh = labels.flatten(labels.table("zh-CN"))
        silent = [k for k, v in zh.items() if not cjk.search(v)]
        self.assertTrue(all(len(re.sub(r"\{\w+\}", "", zh[k]).strip(" .:·")) <= 8 for k in silent), silent)

    def test_the_two_status_words_the_frame_depends_on_are_derived_from_the_sentences(self):
        self.assertEqual((labels.raw("zh-CN", "svg.word.contains"), labels.raw("zh-CN", "svg.word.excludes")), ("包含:", "不含:"))
        self.assertEqual((labels.raw("en-US", "svg.word.contains"), labels.raw("ja-JP", "svg.word.excludes")), ("Contains:", "Excludes:"))

    def test_the_card_scope_lines_fit_the_tight_caps_without_being_clipped(self):
        caps = reading.CARD_FIELD_CAPS["tight"]
        for lang in ("zh-CN", "en-US"):
            for key, cap in (("covers", "covers"), ("excludes", "excludes"), ("howto", "howto")):
                text = labels.t(lang, "card." + key, w=999, s=99, n=999, c=9999)
                size = len(text.encode("utf-8"))
                self.assertLessEqual(size, caps[cap], (lang, key, size))
        wide = reading.CARD_FIELD_CAPS["wide"]
        for lang in ("zh-CN", "en-US"):
            for prefix, fields in (("index", ("covers", "excludes", "howto")), ("nav", ("covers", "excludes", "howto"))):
                for key in fields:
                    text = labels.t(lang, f"{prefix}.{key}", s=9999, v=9999, w=9999, c=99999, n=9999, k=9999)
                    self.assertLessEqual(len(text.encode("utf-8")), wide[key], (lang, prefix, key))


class GoldenTests(unittest.TestCase):
    def test_the_login_example_matches_its_golden_files_byte_for_byte(self):
        built = fx.built("login")
        for path, data in built.files.items():
            golden = GOLDEN / "login" / path
            self.assertTrue(golden.is_file(), f"missing golden {path}: python -m tests.atlas.test_screens_build --write-golden")
            self.assertEqual(golden.read_bytes(), data, path)
        on_disk = sorted(str(p.relative_to(GOLDEN / "login")).replace("\\", "/") for p in (GOLDEN / "login").rglob("*") if p.is_file())
        self.assertEqual(on_disk, sorted(built.files))

    def test_the_other_fixtures_match_their_manifests(self):
        for name in CHECKED:
            self.assertEqual((GOLDEN / f"{name}.manifest.tsv").read_text(encoding="utf-8"), manifest_text(fx.built(name).files), name)

    def test_the_goldens_are_lf_only_without_bom_and_the_showcase_text_is_the_fixture(self):
        for path in sorted(GOLDEN.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                self.assertNotIn(b"\r", data, path.name)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), path.name)
        self.assertEqual(fx.showcase_model(), fx.model_of(fx.showcase_text(), "ui-10-showcase.atl"))

    def test_the_goldens_are_what_the_writer_writes(self):
        self.assertEqual(len(golden_files()), len(fx.built("login").files) + 2)


def write_golden() -> None:
    wanted = golden_files()
    for stale in sorted((GOLDEN / "login").rglob("*")) if (GOLDEN / "login").exists() else []:
        if stale.is_file() and stale not in wanted:
            stale.unlink()
    for path, data in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"wrote {len(wanted)} golden files under {GOLDEN}")


if __name__ == "__main__":
    if "--write-golden" in sys.argv:
        write_golden()
    else:
        unittest.main()
