"""The HTML twins of the reading surface (design A3 2.5, 2.6, D-2; milestone R2): ``reading/render_html.py``, the checks RS-110 and RS-114 and the twin
halves of RS-104, RS-106, RS-109 and RS-132.

What is pinned here:

* the inline grammar (one tokenizer for the renderer and for ``visible_md``) and the block grammar, on hand-made lines and on hostile text;
* the structure of a twin: one policy (the constant of the viewer page), one fixed style, ids unique by construction, links that resolve, no script;
* the equality of what the page says and what the twin says (``visible_md`` against ``visible_html``) on every synthetic preset, seed and language;
* the size of a twin (T-R2: per class aggregate and the caps of RS-132);
* one mutation per sub-rule of the twin checks, each turning exactly its own rule red (two sub-rules cannot be isolated: they are named below);
* the manifest rows of the twins, the gallery exceptions of lane U (a build of the screens joined with a reading build into one ``dist``), the
  documents clause of RS-114 on real documents, determinism over three hash seeds.

Every model is synthetic. Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_twin
"""
from __future__ import annotations

import copy
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gemarc_mcp.atlas import htmlassemble as H
from gemarc_mcp.atlas.derive import i18n as T
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.reading import api
from gemarc_mcp.atlas.reading import check as C
from gemarc_mcp.atlas.reading import envelope as E
from gemarc_mcp.atlas.reading import names as N
from gemarc_mcp.atlas.reading import render_html as RH

from . import read_support as RS
from . import synth
from .test_read_fanout import scaled

REPO = Path(__file__).resolve().parents[2]
LANG = "zh-CN"
TWIN = "site/read/2D/S01.html"
PAGE = "read/2D/S01.md"


def small() -> RS.Built:
    return RS.built("small", html=True)


def text_of(b: RS.Built, path: str) -> str:
    return b.result.files[path].decode("utf-8")


# ---------------------------------------------------------------------------------------------------- the grammar

class TokenizerTests(unittest.TestCase):
    def text(self, md: str, table: bool = False) -> str:
        return RH.tokens_text(RH.tokenize(md, table))

    def test_a_code_span_is_taken_raw_and_a_table_unescapes_the_pipe(self):
        self.assertEqual(RH.tokenize("a `x*y_z` b"), [("t", "a "), ("c", "x*y_z"), ("t", " b")])
        self.assertEqual(RH.tokenize("`a\\|b`", True), [("c", "a|b")])
        self.assertEqual(RH.tokenize("`a\\|b`", False), [("c", "a\\|b")], "outside a table the backslash of a code span stays")
        self.assertEqual(RH.tokenize("a ` b"), [("t", "a ` b")], "a backtick that never closes is a character")
        self.assertEqual(RH.tokenize("``"), [("t", "``")], "an empty span is not a span")

    def test_a_backslash_before_punctuation_is_that_character_and_before_a_letter_it_stays(self):
        self.assertEqual(self.text("a\\[b\\]\\|\\\\\\`c\\~"), "a[b]|\\`c~")
        self.assertEqual(self.text("\\t and \\1"), "\\t and \\1")

    def test_the_three_entities_are_the_three_characters_and_nothing_else_is_decoded(self):
        self.assertEqual(self.text("a &lt;b&gt; &amp; c"), "a <b> & c")
        self.assertEqual(self.text("&quot; &copy; &"), "&quot; &copy; &")
        self.assertEqual(self.text("`&lt;`"), "&lt;", "inside a code span nothing is decoded")

    def test_a_link_has_a_label_and_a_plain_address_and_everything_else_is_text(self):
        self.assertEqual(RH.tokenize("[go](S02.md)"), [("a", [("t", "go")], "S02.md")])
        self.assertEqual(RH.tokenize("x [§S01](S01.md) y"), [("t", "x "), ("a", [("t", "§S01")], "S01.md"), ("t", " y")])
        for plain in ("[no address]", "[a](b c)", "[a](b", "[a] (b)", "\\[a](b)"):
            self.assertEqual(self.text(plain), plain.replace("\\[", "["), plain)
            self.assertFalse([t for t in RH.tokenize(plain) if t[0] == "a"], plain)

    def test_emphasis_marks_of_an_author_stay_literal_on_both_sides(self):
        for text in ("**bold**", "*one*", "_u_", "~~gone~~", "a * b * c"):
            self.assertEqual(self.text(text), text)
        page = RH.visible_md("p\n" + "x\n" * 3 + "\n**bold** and _u_\np\n")
        self.assertIn("**bold** and _u_", page)

    def test_the_html_of_a_text_is_escaped_and_the_visible_text_is_what_was_written(self):
        links = RH._Links("read/2D/S01.md", RH.SiteIndex({"read/2D/S02.md": "页 §S02 · x\n"}))
        tokens = RH.tokenize("a <b> & \"q\" `<id>` [x](S02.md)")
        html = RH.render_tokens(tokens, links)
        self.assertEqual(html, 'a &lt;b&gt; &amp; "q" <code>&lt;id&gt;</code> <a href="S02.html">x</a>')
        self.assertEqual(RH.norm(RH.tokens_text(tokens)), 'a <b> & "q" <id> x')


class BlockTests(unittest.TestCase):
    def kinds(self, lines):
        return [(b.kind, b.level or None) for b in RH.parse_blocks(lines)]

    def test_a_heading_directly_above_a_table_is_its_caption_and_a_table_in_a_block_has_none(self):
        blocks = RH.parse_blocks(["## Things (2)", "| a | b |", "|---|---|", "| 1 | 2 |", "", "### Block", "`id` · kind", "", "| c |", "|---|", "| 3 |"])
        tables = [b for b in blocks if b.kind == "table"]
        self.assertEqual([t.caption for t in tables], ["Things (2)", None])
        self.assertEqual([b.kind for b in blocks], ["table", "h", "p", "table"])

    def test_every_physical_line_is_one_block_item(self):
        blocks = RH.parse_blocks(["# T", "", "> a", "> b", "", "- x", "- y", "plain", "(cont.) rest", "```mermaid", "flowchart LR", "  a --> b", "```", "last"])
        self.assertEqual([b.kind for b in blocks], ["h", "quote", "list", "p", "p", "fence", "p"])
        self.assertEqual(blocks[1].lines, ["a", "b"])
        self.assertEqual(blocks[5].lines, ["flowchart LR", "  a --> b"])
        self.assertEqual(RH.block_atoms(blocks), ["T", "a", "b", "x", "y", "plain", "(cont.) rest", "flowchart LR", "a --> b", "last"])

    def test_an_unterminated_fence_runs_to_the_end_and_never_raises(self):
        blocks = RH.parse_blocks(["```", "a", "b"])
        self.assertEqual([(b.kind, b.lines) for b in blocks], [("fence", ["a", "b"])])

    def test_a_table_row_is_one_atom_per_cell_and_the_separator_is_none(self):
        blocks = RH.parse_blocks(["| h1 | h2 |", "|---|---|", "| `a\\|b` | [x](y.md) |"])
        self.assertEqual(RH.block_atoms(blocks), ["h1", "h2", "a|b", "x"])
        self.assertEqual(RH.block_atoms(blocks, content=True), ["h1", "h2", "a|b", "x"])

    def test_content_atoms_leave_out_headings_captions_and_the_scope_card(self):
        blocks = RH.parse_blocks(["# T", "> scope", "## Cap (1)", "| h |", "|---|", "| v |", "- item", "line"])
        self.assertEqual(RH.block_atoms(blocks, content=True), ["h", "v", "item", "line"])

    def test_ids_are_the_first_use_of_an_identity_line_or_a_first_cell(self):
        blocks = RH.parse_blocks(["### Node", "`n1` · kind", "- x", "## Out (2)", "| id | to |", "|---|---|", "| `e1` | `n1` |", "| `e1` | `n2` |", "## In (1)",
                                  "| to | edge |", "|---|---|", "| `n1` | `e1` |", "| text `x` | y |"])
        self.assertEqual(RH.block_ids(blocks), ["n1", "e1"], "a later use of an id carries none; a cell with more than the code span is no id")


# ---------------------------------------------------------------------------------------------------- the twin of a page

class StructureTests(unittest.TestCase):
    def test_a_twin_is_a_whole_document_with_the_one_policy_and_no_script(self):
        b = small()
        for path, data in b.result.files.items():
            if not path.startswith("site/"):
                continue
            text = data.decode("utf-8")
            with self.subTest(path=path):
                self.assertTrue(text.startswith("<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta http-equiv=\"Content-Security-Policy\" content=\""))
                self.assertIn(f'content="{H.CSP_POLICY}">', text)
                self.assertIs(RH.CSP_POLICY, H.CSP_POLICY, "the policy is the constant of the viewer page, imported and not copied")
                self.assertEqual(text.count("<style>"), 1)
                self.assertNotIn("<script", text)
                self.assertNotRegex(text, r"\son[a-z]+=")
                self.assertLess(text.index('<meta charset="utf-8">'), 1024)
                self.assertTrue(text.endswith("</footer>\n</body>\n</html>\n"))
                self.assertNotIn("\r", text)
                self.assertLessEqual(max(len(line) for line in text.split("\n")), 500)

    def test_the_style_is_one_fixed_style_of_one_rule_per_line_on_every_page(self):
        b = small()
        styles = {re.search(r"<style>\n(.*?)\n</style>", data.decode("utf-8"), re.S).group(1) for path, data in b.result.files.items() if path.startswith("site/")}
        self.assertEqual(styles, {"\n".join(RH.STYLE_LINES)})
        self.assertLessEqual(len("\n".join(RH.STYLE_LINES).encode("utf-8")), api.BUDGETS["style_bytes"])
        self.assertTrue(all(line.count("{") == line.count("}") and "\n" not in line for line in RH.STYLE_LINES))

    def test_a_page_and_its_twin_have_the_same_blocks_tables_and_ids(self):
        b = small()
        for path in b.result.files:
            if not (path.endswith(".md") and N.twin_path(path)):
                continue
            md = text_of(b, path)
            a = RH.analyse(b.result.files[N.twin_path(path)])
            blocks = RH.parse_blocks(md.split("\n"))
            blocks_of_a_section = [blk for blk, nxt in zip(blocks, blocks[1:]) if blk.kind == "h" and blk.level == 3 and nxt.kind == "p" and RH.CODE_LEAD.match(nxt.text)]
            with self.subTest(path=path):
                self.assertEqual(a.tables, len([x for x in blocks if x.kind == "table"]))
                self.assertEqual(a.sections, len(blocks_of_a_section))
                self.assertEqual(a.ids, RH.expected_ids(path, md, LANG))
                self.assertEqual(len(set(a.ids)), len(a.ids))

    def test_a_table_inside_a_block_has_no_caption_and_the_twin_check_knows_it(self):
        b = small()
        case = text_of(b, "site/read/4D/C01.html")
        self.assertIn("<section id=", case)
        a = RH.analyse(case)
        self.assertEqual(a.table_problems, [])
        self.assertGreater(a.tables, len(re.findall(r"<caption>", case)), "the event tables of the cases have no caption")
        self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set())

    def test_the_first_line_and_the_sentinel_count_blocks_and_tables_and_carry_the_sha8_of_the_page(self):
        b = small()
        md = text_of(b, PAGE)
        a = RH.analyse(b.result.files[TWIN])
        counts = RH.counts_of(LANG, a.sections, a.tables)
        self.assertIn(counts, a.header_first)
        self.assertIn(counts, a.footer_last)
        sha = re.search(r"sha8 ([0-9a-f]{8})", md.rstrip("\n").split("\n")[-1]).group(1)
        self.assertIn("sha8 " + sha, a.footer_last)
        self.assertIsNone(re.search(r"共 \d+ 行", a.header_first + a.footer_last), "no physical line count: lines mean nothing to a browser")

    def test_the_head_names_the_page_and_the_md_it_stands_for(self):
        b = small()
        a = RH.analyse(b.result.files[TWIN])
        meta = {m["name"]: m["content"] for m in a.metas if "name" in m}
        self.assertEqual(meta["atlas-page"], "S01")
        self.assertEqual(meta["atlas-stamp"], "m=" + b.src.stamp)
        self.assertEqual((meta["atlas-class"], meta["atlas-lang"], meta["atlas-schema"]), ("page", LANG, "gemarc-atlas-read/1"))
        self.assertEqual([d["href"] for d in a.links], ["../../../read/2D/S01.md"])
        self.assertTrue(a.title.startswith("§S01 · "))

    def test_a_frame_that_cannot_be_read_is_an_error_not_a_twin(self):
        with self.assertRaises(ValueError):
            RH.render_twin("read/2D/S01.md", "not a page\n", LANG)
        with self.assertRaises(ValueError):
            RH.render_twin("docs/README.md", "x\n" * 12, LANG)
        problems: list = []
        good = text_of(small(), PAGE)
        out = RH.render_site({PAGE: good, "read/2D/S02.md": "not a page\n" * 12}, LANG, problems=problems)
        self.assertEqual(sorted(out), [TWIN])
        self.assertEqual([p for p, _m in problems], ["read/2D/S02.md"])
        with self.assertRaises(ValueError):
            RH.render_site({"read/2D/S02.md": "not a page\n" * 12}, LANG)


class LinkTests(unittest.TestCase):
    def test_a_link_to_a_page_with_a_twin_is_a_link_to_the_twin_and_one_to_a_file_without_is_one_level_deeper(self):
        b = small()
        s = text_of(b, "site/read/2D/S02.html")
        self.assertIn('<a href="S01.html">§S01</a>', s)
        self.assertIn('<a href="../1D/R01.html">§R01</a>', s)
        self.assertIn('href="../../START.html"', s)
        self.assertIn('href="../../../diagrams/', s, "a diagram has no twin: the real file, from a directory one level deeper than the page's")
        self.assertNotIn('.md"', re.sub(r'<link rel="alternate"[^>]*>', "", s), "no link of a twin points to a Markdown page that has a twin")
        start = text_of(b, "site/START.html")
        self.assertIn('href="read/1D/R01.html"', start)
        self.assertIn('href="read/G01.html"', start)

    def test_every_link_of_every_twin_resolves(self):
        b = small()
        known = set(b.result.files) | {f"diagrams/{T.dg_id(u['id'])}.mmd" for u in b.bundle["diagrams"]}
        checked = 0
        for path, data in b.result.files.items():
            if not path.startswith("site/"):
                continue
            a = RH.analyse(data)
            for href in a.hrefs + [d["href"] for d in a.links]:
                target = os.path.normpath(os.path.join(os.path.dirname(path), href)).replace("\\", "/")
                self.assertIn(target, known, (path, href))
                checked += 1
        self.assertGreater(checked, 100)

    def test_an_address_that_is_not_a_relative_path_never_becomes_a_scheme(self):
        index = RH.SiteIndex({})
        for md in ("read/2D/S01.md", "START.md"):
            links = RH._Links(md, index)
            self.assertFalse(links.href("javascript:alert(1)").lower().startswith("javascript:"))
            self.assertFalse(links.href("../../../../etc/x").startswith("/"))
            self.assertEqual(links.href("https://example.invalid/x"), "https://example.invalid/x")
            self.assertEqual(links.href("#frag"), "#frag")

    def test_a_page_reference_is_a_link_only_for_a_page_of_the_site(self):
        index = RH.SiteIndex({"read/2D/S01.md": "页 §S01 · x\n", "read/2D/S02.md": "页 §S02 · x\n"})
        links = RH._Links("read/2D/S01.md", index)
        html = RH.render_tokens(RH.tokenize("本族: §S02、§S99、§S02-§S02 与 `§S02`"), links)
        self.assertEqual(html.count('<a href="S02.html">§S02</a>'), 3)
        self.assertIn("§S99、", html)
        self.assertNotIn(">§S99<", html)
        self.assertIn("<code>§S02</code>", html, "a code span is never a link")

    def test_render_site_takes_the_context_of_other_pages_so_that_their_links_are_twins(self):
        b = small()
        mine = {p: d for p, d in b.result.files.items() if p == "read/G01.md"}
        alone = RH.render_site(mine, LANG)["site/read/G01.html"].decode("utf-8")
        with_context = RH.render_site(mine, LANG, context={p: d for p, d in b.result.files.items() if p.endswith(".md")})["site/read/G01.html"].decode("utf-8")
        self.assertIn('<a href="../START.html">', with_context)
        self.assertNotIn('<a href="../START.html">', alone)
        self.assertEqual(with_context, text_of(b, "site/read/G01.html"))

    def test_a_card_has_no_twin_of_this_renderer_unless_the_gallery_hook_hands_one_in(self):
        card = text_of(small(), "read/G01.md")
        self.assertEqual(RH.render_site({"screens/SCR-X.card.md": card}, LANG), {})
        seen = []

        def hook(path, text):
            seen.append(path)
            return b"<!doctype html>\n"

        out = RH.render_site({"screens/SCR-X.card.md": card, "screens/SCR-X.card-2.md": card}, LANG, gallery=hook)
        self.assertEqual(out, {"site/screens/SCR-X.html": b"<!doctype html>\n"})
        self.assertEqual(seen, ["screens/SCR-X.card-2.md"], "one call per gallery (the first part in path order here; the manifest picks the .card.md part)")


# ---------------------------------------------------------------------------------------------------- what the page says

class EquivalenceTests(unittest.TestCase):
    """T-R1 for the twins: every preset, seed and language builds with twins, no I-class finding, RS-110 and RS-114 clean, the atoms equal."""

    def test_every_preset_and_seed_builds_with_twins_and_the_twin_checks_are_clean(self):
        for preset in RS.PRESETS:
            for seed in RS.SEEDS:
                b = RS.built(preset, LANG, seed, html=True)
                with self.subTest(preset=preset, seed=seed):
                    self.assertEqual(b.result.status, "ok")
                    self.assertEqual(RS.rules_of(b.result.findings), set(), [f.message for f in b.result.findings if f.klass == "I"][:3])
                    twins = [p for p in b.result.files if p.startswith("site/")]
                    self.assertEqual(len(twins), len([p for p in b.result.files if p.endswith(".md") and N.twin_path(p)]))

    def test_the_other_languages_build_with_twins_too(self):
        for preset, lang in (("small", "en-US"), ("tiny", "ja-JP"), ("tiny", "en-US")):
            b = RS.built(preset, lang, 1, html=True)
            with self.subTest(preset=preset, lang=lang):
                self.assertEqual(b.result.status, "ok")
                self.assertEqual(RS.rules_of(b.result.findings), set())
                for path, data in b.result.files.items():
                    if path.endswith(".md") and N.twin_path(path):
                        self.assertEqual(RH.visible_md(data.decode("utf-8")), RH.visible_html(b.result.files[N.twin_path(path)]), path)
                self.assertIn(f'<html lang="{lang}">', text_of(b, "site/START.html"))

    def test_the_legal_models_of_t_r11_build_with_clean_twins(self):
        """A to-be model (every line range null: the cells say so) and an as-is model with lowered verification and no evidence root."""
        for preset in ("small", "reference"):
            b = RS.make(synth.generate(preset, 2, mode="tobe"), LANG, cfg=R.ReadConfig(html=True))
            with self.subTest(model=f"tobe {preset}"):
                self.assertEqual(b.result.status, "ok")
                self.assertEqual(RS.rules_of(b.result.findings), set())
        model = copy.deepcopy(synth.generate("small", 3))
        for node in model["nodes"]:
            node["verification"], node["evidenceIds"] = "Hypothetical", []
        for edge in model["edges"]:
            edge["verification"], edge["evidenceIds"] = "Unknown", []
        for table in ("cases", "requirements", "tbds"):
            for rec in model[table]:
                rec["evidenceIds"] = []
        for machine in model["machines"]:
            machine["evidenceIds"] = []
        model["evidences"] = []
        b = RS.make(model, LANG, cfg=R.ReadConfig(html=True))
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set())

    def test_the_atoms_of_every_page_equal_the_atoms_of_its_twin_on_reference_scale(self):
        for preset in ("reference", "refcjk"):
            b = RS.built(preset, LANG, 2, html=True)
            for path, data in b.result.files.items():
                if path.endswith(".md") and N.twin_path(path):
                    with self.subTest(path=path):
                        self.assertEqual(RH.visible_md(data.decode("utf-8")), RH.visible_html(b.result.files[N.twin_path(path)]))

    def test_folded_pages_continuation_parts_and_small_projects_build_with_clean_twins(self):
        """The fan-out scales (single page, folded pages) and a hub with a long case (continuation parts)."""
        models = [("x0.1", synth.generate(scaled(0.1), 1)), ("x0.25", synth.generate(scaled(0.25), 1)), ("x0.6", synth.generate(scaled(0.6), 1)),
                  ("hub", RS.hub_model(150, 300))]
        for name, model in models:
            b = RS.make(model, LANG, cfg=R.ReadConfig(html=True))
            with self.subTest(model=name):
                self.assertEqual(b.result.status, "ok")
                self.assertEqual(RS.rules_of(b.result.findings), set(), [f.message for f in b.result.findings if f.klass == "I"][:3])
                self.assertTrue([p for p in b.result.files if p.startswith("site/")])
        single = RS.built("tiny", LANG, 1, html=True)
        self.assertIn("site/read/A01.html", single.result.files, "the single page of a small project has its twin")

    def test_hostile_author_text_changes_neither_the_markup_nor_the_text(self):
        model = copy.deepcopy(synth.generate("small", 2))
        hostile = '<script>alert(1)</script> & "q" \'a\' **b** _c_ *d* [e](javascript:alert(1)) \\ ~~f~~ | g `h` <b onmouseover=x>'
        model["requirements"][0]["title"] = hostile
        model["tbds"][0]["decision"] = hostile + " " + "long " * 150
        model["nodes"][1]["sub"] = "字" * 700
        model["nodes"][2]["name"] = hostile[:60]
        b = RS.make(model, cfg=R.ReadConfig(html=True))
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(RS.rules_of(b.result.findings), set(), [f.message for f in b.result.findings if f.klass == "I"][:3])
        for path, data in b.result.files.items():
            if path.startswith("site/"):
                text = data.decode("utf-8")
                a = RH.analyse(data)
                self.assertNotIn("<script", text, path)
                self.assertEqual((a.scripts, a.event_attrs, a.forbidden, a.external), (0, [], [], []), path)
                self.assertLessEqual(max(len(line) for line in text.split("\n")), 500, path)
                self.assertFalse([h for h in a.hrefs if h.lower().startswith("javascript")], path)
        joined = " ".join(RH.visible_html(b.result.files[p]).__repr__() for p in b.result.files if p.startswith("site/read/1D/R0"))
        self.assertIn("**b** _c_ *d*", joined)

    def test_the_size_of_a_twin_is_inside_its_cap_and_the_class_ratio_inside_the_bound(self):
        """T-R2: ``cap_html`` (2.4 times the Markdown cap) per twin, and the byte ratio twin / page per page class at most 2.4."""
        for preset, lang in (("tiny", LANG), ("small", LANG), ("reference", LANG), ("refcjk", LANG), ("small", "en-US")):
            b = RS.built(preset, lang, 1, html=True)
            sums: dict = {}
            for path, data in b.result.files.items():
                if not (path.endswith(".md") and N.twin_path(path)):
                    continue
                cls = E.first_line_regex(lang).match(data.decode("utf-8").split("\n", 1)[0]).group("cls")
                twin = b.result.files[N.twin_path(path)]
                self.assertLessEqual(len(twin), api.BUDGETS["cap_html"][cls], (preset, path))
                total = sums.setdefault(cls, [0, 0])
                total[0] += len(twin)
                total[1] += len(data)
            for cls, (twin_bytes, md_bytes) in sums.items():
                with self.subTest(preset=preset, lang=lang, cls=cls):
                    self.assertLessEqual(twin_bytes / md_bytes, 2.4, (twin_bytes, md_bytes))


# ---------------------------------------------------------------------------------------------------- the checks

def sub(pattern: str, replacement, count: int = 1):
    """A text edit: the first match of a regular expression replaced (the edit must change something)."""
    def edit(text: str) -> str:
        out, n = re.subn(pattern, replacement, text, count=count, flags=re.S)
        assert n, f"the pattern {pattern!r} matches nothing"
        return out

    return edit


def padding(lines: int = 60):
    return lambda t: t.replace("</body>", "".join("<!--" + "x" * 460 + "-->\n" for _ in range(lines)) + "</body>", 1)


#: (label, the rules that must turn red, the edit of the twin of S01). The twin side only; the manifest row is refreshed, so RS-109 stays quiet.
TWIN_MUTATIONS = [
    ("script", {"RS-110"}, lambda t: t.replace("</body>", "<script>1</script></body>", 1)),
    ("event handler", {"RS-110"}, sub(r"<p>", '<p onclick="x">')),
    ("external address", {"RS-110"}, sub(r'<a href="[^"]+">', '<a href="http://example.invalid/x">')),
    ("data address", {"RS-110"}, lambda t: t.replace("</head>", '<meta name="x" content="y" style="background:url(data:image/png;base64,AAAA)"></head>', 1)),
    ("a weaker policy", {"RS-110"}, lambda t: t.replace("default-src 'none'", "default-src *", 1)),
    ("a second policy", {"RS-110"}, lambda t: t.replace("<head>\n", "<head>\n" + re.search(r'<meta http-equiv="Content-Security-Policy"[^>]*>', t).group(0) + "\n", 1)),
    ("a refresh meta", {"RS-110"}, lambda t: t.replace("</head>", '<meta http-equiv="refresh" content="5"></head>', 1)),
    ("another style", {"RS-110"}, lambda t: t.replace("</style>", "a{color:red}\n</style>", 1)),
    ("two styles", {"RS-110"}, lambda t: t.replace("</head>", "<style>\na{x:y}\n</style></head>", 1)),
    ("a late charset", {"RS-110"}, lambda t: t.replace('<meta charset="utf-8">', "<!--" + ("x" * 100 + "\n") * 12 + '--><meta charset="utf-8">', 1)),
    ("another language", {"RS-110"}, lambda t: t.replace('<html lang="zh-CN">', '<html lang="en-US">', 1)),
    ("no viewport", {"RS-110"}, sub(r'<meta name="viewport"[^>]*>\n', "")),
    ("an element that must not occur", {"RS-110"}, lambda t: t.replace("</body>", "<iframe></iframe></body>", 1)),
    ("a table without caption outside a block", {"RS-110", "RS-114"}, sub(r"<caption>.*?</caption>\n", "")),
    ("the same id twice", {"RS-110", "RS-114"}, sub(r'<tr id="([^"]+)">(.*?)<tr id="[^"]+">', r'<tr id="\1">\2<tr id="\1">')),
    ("one visible character more", {"RS-114"}, lambda t: t.replace("<h2>", "<h2>x", 1)),
    ("a row dropped", {"RS-114"}, sub(r'<tr id="[^"]+">.*?</tr>\n', "")),
    ("an id dropped", {"RS-114"}, sub(r'<tr id="[^"]+">', "<tr>")),
    ("another stamp", {"RS-114"}, sub(r'name="atlas-stamp" content="m=[0-9a-f]{8}"', 'name="atlas-stamp" content="m=00000000"')),
    ("another class", {"RS-114"}, lambda t: t.replace('name="atlas-class" content="page"', 'name="atlas-class" content="card"', 1)),
    ("the alternate link elsewhere", {"RS-114"}, lambda t: t.replace("../../../read/2D/S01.md", "../../../read/2D/S02.md", 1)),
    ("counts in the first line", {"RS-104"}, sub(r"含 (\d+) 个块", lambda m: f"含 {int(m.group(1)) + 1} 个块")),
    ("sha8 of the sentinel", {"RS-104"}, sub(r"sha8 [0-9a-f]{8}", "sha8 00000000")),
    ("page of the sentinel", {"RS-104"}, sub(r"页尾 §S01", "页尾 §S02")),
    ("a broken address", {"RS-106"}, sub(r'<a href="S0\d\.html">', '<a href="S99.html">')),
    ("an address to the page instead of the twin", {"RS-106"}, sub(r'<a href="(S0\d)\.html">', r'<a href="\1.md">')),
]


class TwinMutationTests(unittest.TestCase):
    def test_each_sub_rule_turns_exactly_its_rules_red(self):
        b = small()
        base = b.surface()
        self.assertEqual(RS.rules_of(RS.check(b, base)), set())
        for label, rules, edit in TWIN_MUTATIONS:
            with self.subTest(label=label):
                findings = RS.check(b, RS.edit_file(base, TWIN, edit))
                self.assertEqual(RS.rules_of(findings), rules, [f"{f.rule} {f.path} {f.message}" for f in findings if f.klass == "I"][:4])

    def test_the_mutations_change_something(self):
        b = small()
        base = b.surface()
        for label, _rules, edit in TWIN_MUTATIONS:
            self.assertNotEqual(edit(base.files[TWIN].decode("utf-8")), base.files[TWIN].decode("utf-8"), label)

    def test_a_missing_twin_a_wrong_row_and_a_twin_of_no_page_are_the_tree_rule_RS109(self):
        b = small()
        base = b.surface()
        gone = RS.copy_surface(base)
        del gone.files[TWIN]
        self.assertEqual(RS.rules_of(RS.check(b, gone)), {"RS-109", "RS-106"}, "the other twins still link to the one that is gone: the link rule agrees")
        wrong = RS.copy_surface(base)
        for rows in (wrong.records, wrong.manifest_rows or []):
            for rec in rows:
                if rec["p"] == PAGE:
                    rec["twin"] = "site/other.html"
        self.assertEqual(RS.rules_of(RS.check(b, wrong)), {"RS-109"})
        orphan = RS.copy_surface(base)
        orphan.files["site/read/2D/S99.html"] = base.files[TWIN]
        orphan.records.append({"p": "site/read/2D/S99.html", "k": "twin", "cls": "twin", "ai": False, "twin": None, "twinOf": PAGE})
        self.assertEqual(RS.rules_of(RS.check(b, orphan)), {"RS-109"})
        bad_row = RS.copy_surface(base)
        for rec in bad_row.records:
            if rec["p"] == TWIN:
                rec["ai"] = True
        self.assertEqual(RS.rules_of(RS.check(b, bad_row)), {"RS-109"})

    def test_without_twins_no_twin_rule_applies(self):
        b = RS.built("small")
        s = b.surface()
        self.assertFalse(any(p.startswith("site/") for p in s.files))
        self.assertEqual(RS.rules_of(RS.check(b, s, ["RS-104", "RS-106", "RS-109", "RS-110", "RS-114"])), set())

    def test_a_twin_above_its_cap_is_a_degradation_and_never_a_failure(self):
        b = small()
        out = RS.edit_file(b.surface(), TWIN, padding(60))
        self.assertGreater(len(out.files[TWIN]), api.BUDGETS["cap_html"]["page"])
        findings = RS.check(b, out)
        self.assertEqual(RS.rules_of(findings), set())
        warn = [f for f in findings if f.rule == "RS-132" and f.path == TWIN]
        self.assertEqual({(f.klass, f.severity) for f in warn}, {("L", "WARN")})
        self.assertTrue(all(f.hint for f in warn))

    def test_a_twin_the_reading_surface_renders_may_not_use_a_policy_of_its_own_but_a_gallery_may_narrow_it(self):
        b = small()
        base = b.surface()
        gallery = "site/screens/SCR-X.html"
        good = base.files[TWIN].decode("utf-8")
        narrow = re.sub(r'content="[^"]*"', 'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:"', good, count=1)
        ctx = C.Ctx(b.src, with_files(base, {gallery: narrow.encode("utf-8")}))
        C.rs110(ctx)
        self.assertEqual([f for f in ctx.findings if f.path == gallery], [])
        wide = re.sub(r'content="[^"]*"', 'content="default-src *"', good, count=1)
        ctx = C.Ctx(b.src, with_files(base, {gallery: wide.encode("utf-8")}))
        C.rs110(ctx)
        self.assertEqual([f.rule for f in ctx.findings if f.path == gallery], ["RS-110"])


def with_files(surface: C.Surface, extra_files: dict) -> C.Surface:
    out = RS.copy_surface(surface)
    out.files.update(extra_files)
    return out


# ---------------------------------------------------------------------------------------------------- physical lines

class FoldTests(unittest.TestCase):
    def doc(self, lines) -> str:
        return "<!doctype html>\n<html><body>\n" + "\n".join(lines) + "\n</body></html>\n"

    def test_a_short_line_is_untouched(self):
        self.assertEqual(RH.fold_html("<p>short</p>"), ["<p>short</p>"])

    def test_a_long_line_breaks_at_spaces_cells_and_tags_and_the_text_does_not_change(self):
        cases = [
            "<p>" + " ".join(["word"] * 300) + "</p>",
            "<tr>" + "".join(f"<td><code>cell{n}</code> and text of cell</td>" for n in range(40)) + "</tr>",
            "<p>" + "、".join(f"<code>id{n:03d}</code>" for n in range(80)) + "</p>",
            "<li>" + "".join(f'<a href="x{n}.html">link{n}</a>·' for n in range(60)) + "</li>",
        ]
        for line in cases:
            folded = RH.fold_html(line)
            with self.subTest(line=line[:30]):
                self.assertGreater(len(folded), 1)
                self.assertLessEqual(max(len(x) for x in folded), 480)
                self.assertEqual([t for _z, t in RH.analyse(self.doc([line])).atoms], [t for _z, t in RH.analyse(self.doc(folded)).atoms])

    def test_no_break_is_made_between_two_characters_of_a_text(self):
        line = "<p>" + "字" * 600 + "</p>"
        self.assertEqual(RH.fold_html(line), [line], "a text without a space is never cut (a line feed there is a space for the browser)")

    def test_long_lines_of_a_page_survive_the_fold_with_the_atoms_intact(self):
        model = copy.deepcopy(synth.generate("small", 2))
        model["requirements"][0]["title"] = "long " * 150
        model["tbds"][0]["decision"] = "decision " * 90
        b = RS.make(model, cfg=R.ReadConfig(html=True))
        self.assertEqual(RS.rules_of(b.result.findings), set())
        for path, data in b.result.files.items():
            if path.endswith(".md") and N.twin_path(path):
                self.assertEqual(RH.visible_md(data.decode("utf-8")), RH.visible_html(b.result.files[N.twin_path(path)]), path)


# ---------------------------------------------------------------------------------------------------- manifest and START

class ManifestTests(unittest.TestCase):
    def test_a_twin_row_names_its_page_and_a_page_row_its_twin(self):
        b = small()
        by = {r["p"]: r for r in b.result.records}
        for path in b.result.files:
            if path.endswith(".md") and N.twin_path(path):
                self.assertEqual(by[path]["twin"], N.twin_path(path))
                twin = by[N.twin_path(path)]
                self.assertEqual((twin["k"], twin["cls"], twin["ai"], twin["twinOf"], twin["twin"]), ("twin", "twin", False, path, None))
                self.assertEqual(twin["b"], len(b.result.files[twin["p"]]))
                self.assertGreater(twin["tok"], 0)
        self.assertEqual(by["ids.tsv"]["twin"], None)

    def test_without_twins_no_row_names_one_and_the_markdown_is_the_same_byte_for_byte(self):
        off, on = RS.built("small"), small()
        self.assertEqual({p: d for p, d in on.result.files.items() if not p.startswith("site/")}, dict(off.result.files))
        self.assertTrue(all(r["twin"] is None for r in off.result.records if "twin" in r))
        self.assertFalse(any(r["cls"] == "twin" for r in off.result.records))

    def test_the_finalized_manifest_counts_the_twins_and_every_line_fits(self):
        b = small()
        with RS.TempDist(b, real_docs=True) as dist:
            header = dist.manifest.header
            self.assertEqual(header["classes"]["twin"], len([p for p in b.result.files if p.startswith("site/")]))
            self.assertEqual(sum(header["classes"].values()), header["files"])
            self.assertEqual(header["aiFiles"], len([r for r in dist.manifest.rows if r.get("ai") and r["cls"] in ("card", "page", "svg")]))
            lines = (dist.tmp / "manifest.jsonl").read_text(encoding="utf-8").split("\n")
            self.assertLessEqual(max(len(x) for x in lines), 500)
            rows = {r["p"]: r for r in dist.manifest.rows}
            self.assertEqual(rows["site/START.html"]["twinOf"], "START.md")
            self.assertEqual(rows["START.md"]["twin"], "site/START.html")
            self.assertEqual(RS.rules_of(C.check_surface(b.src, dist.surface)), set())

    def test_the_manifest_links_the_files_of_the_screen_lane_by_their_paths(self):
        b = RS.built("tiny", html=True)
        with RS.TempDist(b, real_docs=True) as dist:
            for rel, text in (("screens/INDEX.md", "# i\n"), ("screens/NAV.md", "# n\n"), ("screens/SCR-X.card.md", "# c\n"), ("screens/SCR-X.card-2.md", "# c2\n"),
                              ("site/screens/INDEX.html", "<!doctype html>\n"), ("site/screens/NAV.html", "<!doctype html>\n"), ("site/screens/SCR-X.html", "<!doctype html>\n"),
                              ("screens/SCR-Y.card.md", "# y\n")):
                target = dist.tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
            manifest = R.finalize(dist.tmp, b.result)
            rows = {r["p"]: r for r in manifest.rows}
            self.assertEqual(rows["screens/INDEX.md"]["twin"], "site/screens/INDEX.html")
            self.assertEqual(rows["site/screens/INDEX.html"]["twinOf"], "screens/INDEX.md")
            self.assertEqual({rows["screens/SCR-X.card.md"]["twin"], rows["screens/SCR-X.card-2.md"]["twin"]}, {"site/screens/SCR-X.html"})
            self.assertEqual(rows["site/screens/SCR-X.html"]["twinOf"], "screens/SCR-X.card.md", "the first part of a card stands for its gallery")
            self.assertIsNone(rows["screens/SCR-Y.card.md"]["twin"], "no gallery on disk: no twin")
            self.assertEqual(rows["site/screens/SCR-X.html"]["cls"], "twin")
            self.assertGreater(rows["site/screens/SCR-X.html"]["tok"], 0)


class StartTests(unittest.TestCase):
    def test_start_says_what_site_is_for_in_every_language_with_and_without_twins(self):
        for preset, lang in (("tiny", LANG), ("small", LANG), ("reference", LANG), ("small", "en-US"), ("tiny", "ja-JP")):
            on, off = RS.built(preset, lang, 1, html=True), RS.built(preset, lang)
            start_on, start_off = text_of(on, "START.md"), text_of(off, "START.md")
            with self.subTest(preset=preset, lang=lang):
                self.assertEqual(start_on, start_off, "the purpose is stated whether or not twins are written: html=false changes no Markdown byte")
                line = next(x for x in start_on.split("\n") if x.startswith("- `site/`"))
                for word in (("浏览器", "WebFetch", "cat") if lang == LANG else ("browser", "WebFetch", "cat")):
                    self.assertIn(word, line)
                self.assertNotIn("](site/", start_on, "START never links an ai:false file")
                self.assertLessEqual(len(on.result.files["START.md"]), api.BUDGETS["cap_md"]["card"])
                self.assertNotIn("RS-132", [f.rule for f in on.result.findings if f.path == "START.md"], "START is not degraded by the extra line")

    def test_a_start_with_a_screen_entry_is_built(self):
        ui = {"decision": "yes", "reason": "", "entry": "screens/INDEX.md", "hint": False}
        b = RS.make(RS.model_of("small", 1), ui=ui)
        start = text_of(b, "START.md")
        self.assertIn("`screens/INDEX.md`", start)
        self.assertEqual(RS.rules_of(b.result.findings), set())


# ---------------------------------------------------------------------------------------------------- documents, screens, determinism

class DocumentsClauseTests(unittest.TestCase):
    def test_with_the_real_documents_on_disk_every_fragment_is_verbatim_in_them(self):
        b = small()
        with RS.TempDist(b, real_docs=True) as dist:
            self.assertEqual(RS.rules_of(C.check_surface(b.src, dist.surface)), set())

    def test_a_fragment_missing_from_the_documents_turns_RS114_red(self):
        b = small()
        with RS.TempDist(b, real_docs=True) as dist:
            paths = [f"docs/{LANG}/{level}.md" for level in ("REQ", "BD", "DD")]
            home = next(h for h in b.src.homes if h.family == "E" and h.md in dist.surface.files[paths[2]].decode("utf-8"))
            surface = dist.surface
            for path in paths:                        # the fragment may sit in more than one document: take it out of all of them
                surface = RS.edit_file(surface, path, lambda t: t.replace(home.md, ""))
            findings = C.check_surface(b.src, surface)
            self.assertEqual(RS.rules_of(findings), {"RS-114"})

    def test_a_stub_document_is_only_judged_when_twins_are_written(self):
        off = RS.built("small")
        with RS.TempDist(off) as dist:
            self.assertEqual(RS.rules_of(C.check_surface(off.src, dist.surface)), set())


class ScreensTests(unittest.TestCase):
    """The galleries and cards of lane U joined with a reading build into one ``dist`` (the exceptions of D-2 and the requests U2-5, U2-6)."""

    def join(self, screens_lang: str, html: bool = True):
        """``(reading build, screens result, dist directory)``: the screens of the lane joined with a reading build of the project (language zh-CN)."""
        try:
            from gemarc_mcp.atlas.protoui import build as ui_build

            from . import screens_fixtures as SF
        except ImportError as error:                           # pragma: no cover  (the screen lane is not installed)
            self.skipTest(f"the screen lane is not available: {error}")
        ui = {"decision": "yes", "reason": "", "entry": "screens/INDEX.md", "hint": False}
        b = RS.make(RS.model_of("small", 1), LANG, ui=ui, cfg=R.ReadConfig(html=html))
        screens = ui_build.build_screens(SF.model_of(SF.showcase_text()), None, ui_build.ScreensConfig(lang=screens_lang, project=b.src.project, html=html))
        self.assertEqual(screens.status, "ok")
        return b, screens

    def test_the_screens_of_the_lane_joined_with_a_reading_build_pass_every_check(self):
        b, screens = self.join(LANG)
        tmp = Path(tempfile.mkdtemp(prefix="read-screens-"))
        try:
            RS.write_dist(b, tmp, True, real_docs=True)
            for path, data in screens.files.items():
                target = tmp / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            md = {p: d for p, d in screens.files.items() if p.endswith(".md")}
            twins = R.render_site(md, LANG, context={p: d for p, d in b.result.files.items() if p.endswith(".md")})
            self.assertEqual(set(twins), {"site/screens/INDEX.html", "site/screens/NAV.html"}, "cards have no twin of this renderer: the gallery is the lane's")
            for path, data in twins.items():
                (tmp / path).parent.mkdir(parents=True, exist_ok=True)
                (tmp / path).write_bytes(data)
            manifest = R.finalize(tmp, b.result)
            rows = {r["p"]: r for r in manifest.rows}
            for card in [p for p in rows if re.fullmatch(r"screens/[^/]+\.card\.md", p)]:
                self.assertEqual(rows[card]["twin"], "site/screens/" + card[len("screens/"):-len(".card.md")] + ".html", card)
            self.assertEqual(manifest.unregistered, [])
            findings = C.check_surface(b.src, C.surface_from_dist(tmp))
            self.assertEqual([(f.rule, f.path, f.message) for f in findings if f.klass == "I"], [])
            galleries = [p for p in rows if C.is_gallery(p)]
            self.assertTrue(galleries)
            nav = tmp / "site/screens/NAV.html"
            self.assertIn("<pre>", nav.read_text(encoding="utf-8"), "the Mermaid code block of NAV is text of the page (U2-5c)")
            self.assertEqual(RH.visible_md(md["screens/NAV.md"].decode("utf-8")), RH.visible_html(twins["site/screens/NAV.html"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_screens_in_another_language_than_the_project_and_with_html_off(self):
        """The interface model has its own language: its pages and galleries are in it, the twins of INDEX and NAV follow their own page, and the
        checks compare a twin with the language of its own page. With html off the lane writes no gallery and nothing of the twin rules applies."""
        b, screens = self.join("en-US")
        self.assertIn("Project ", screens.files["screens/INDEX.md"].decode("utf-8"))
        tmp = Path(tempfile.mkdtemp(prefix="read-screens-en-"))
        try:
            RS.write_dist(b, tmp, True, real_docs=True)
            for path, data in screens.files.items():
                (tmp / path).parent.mkdir(parents=True, exist_ok=True)
                (tmp / path).write_bytes(data)
            md = {p: d for p, d in screens.files.items() if p.endswith(".md")}
            twins = R.render_site(md, LANG, context={p: d for p, d in b.result.files.items() if p.endswith(".md")})
            self.assertIn('<html lang="en-US">', twins["site/screens/INDEX.html"].decode("utf-8"))
            for path, data in twins.items():
                (tmp / path).parent.mkdir(parents=True, exist_ok=True)
                (tmp / path).write_bytes(data)
            R.finalize(tmp, b.result)
            findings = C.check_surface(b.src, C.surface_from_dist(tmp))
            self.assertEqual([(f.rule, f.path, f.message) for f in findings if f.klass == "I"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        off, screens_off = self.join(LANG, html=False)
        self.assertEqual([p for p in screens_off.files if p.startswith("site/")], [], "the lane honours html=false")
        tmp = Path(tempfile.mkdtemp(prefix="read-screens-off-"))
        try:
            RS.write_dist(off, tmp, True, real_docs=True)
            for path, data in screens_off.files.items():
                (tmp / path).parent.mkdir(parents=True, exist_ok=True)
                (tmp / path).write_bytes(data)
            manifest = R.finalize(tmp, off.result)
            self.assertFalse([r for r in manifest.rows if r["cls"] == "twin" or r.get("twin")])
            self.assertEqual([f.rule for f in C.check_surface(off.src, C.surface_from_dist(tmp)) if f.klass == "I"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_gallery_that_lost_a_part_of_its_card_is_red_and_a_card_without_gallery_is_red(self):
        b = RS.built("tiny", html=True)
        card = b.result.files["read/G01.md"].decode("utf-8")
        surface = RS.copy_surface(b.surface())
        page = RH.render_twin("read/G01.md", card, LANG).decode("utf-8")
        surface.files["screens/SCR-X.card.md"] = card.encode("utf-8")
        surface.files["site/screens/SCR-X.html"] = page.encode("utf-8")
        for tp in ("screens/SCR-X.card.md", "site/screens/SCR-X.html"):
            surface.records.append({"p": tp, "k": "twin" if tp.startswith("site/") else "screen-card", "cls": "twin" if tp.startswith("site/") else "page",
                                    "ai": not tp.startswith("site/"), "twin": None if tp.startswith("site/") else "site/screens/SCR-X.html",
                                    **({"twinOf": "screens/SCR-X.card.md"} if tp.startswith("site/") else {})})
        findings = C.check_surface(b.src, surface, ["RS-114"])
        self.assertEqual([f.path for f in findings if f.rule == "RS-114"], [], "a gallery that holds everything the card says is fine")
        lost = RS.copy_surface(surface)
        lost.files["site/screens/SCR-X.html"] = re.sub(r"<li>.*?</li>\n", "", page, count=1).encode("utf-8")
        found = [f for f in C.check_surface(b.src, lost, ["RS-114"]) if f.path == "site/screens/SCR-X.html"]
        self.assertEqual([f.rule for f in found], ["RS-114"])
        none = RS.copy_surface(surface)
        del none.files["site/screens/SCR-X.html"]
        self.assertIn("RS-109", {f.rule for f in C.check_surface(b.src, none, ["RS-109"])})


class DeterminismTests(unittest.TestCase):
    SCRIPT = (
        "import hashlib, json, sys\n"
        "sys.path[:0] = [sys.argv[1] + '/src', sys.argv[1]]\n"
        "from tests.atlas import read_support as RS\n"
        "from gemarc_mcp.atlas import reading as R\n"
        "b = RS.make(RS.model_of(sys.argv[2], 1), cfg=R.ReadConfig(html=True))\n"
        "h = hashlib.sha256()\n"
        "for p in sorted(b.result.files):\n"
        "    h.update(p.encode()); h.update(b.result.files[p])\n"
        "h.update(json.dumps(b.result.records, sort_keys=True).encode())\n"
        "print(h.hexdigest(), len(b.result.files), len([p for p in b.result.files if p.startswith('site/')]))\n"
    )

    def test_three_hash_seeds_give_the_same_twins(self):
        for preset in ("small", "reference"):
            digests = set()
            for seed in ("0", "1", "4242"):
                env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
                done = subprocess.run([sys.executable, "-c", self.SCRIPT, str(REPO), preset], capture_output=True, text=True, env=env, timeout=240)
                self.assertEqual(done.returncode, 0, done.stderr[-600:])
                digests.add(done.stdout.strip())
            self.assertEqual(len(digests), 1, (preset, digests))
            self.assertGreater(int(next(iter(digests)).split()[-1]), 0)


class InterfaceTests(unittest.TestCase):
    def test_the_additions_to_the_frozen_interface_are_exported(self):
        for name in ("render_site", "render_twin", "visible_md", "visible_html"):
            self.assertIn(name, api.__all__)
            self.assertTrue(callable(getattr(api, name)))
        self.assertIs(R.render_site, api.render_site)
        self.assertEqual(list(inspect.signature(api.render_site).parameters), ["files", "lang", "gallery", "context", "problems"])

    def test_the_policy_is_the_constant_of_the_viewer_page(self):
        self.assertEqual(RH.CSP_POLICY, H.CSP_POLICY)
        self.assertEqual(RH.policy_directives(H.CSP_POLICY)["default-src"], ["'none'"])

    def test_the_result_does_not_depend_on_the_order_of_the_files(self):
        b = small()
        files = {p: d for p, d in b.result.files.items() if p.endswith(".md") and N.twin_path(p)}
        reverse = dict(reversed(list(files.items())))
        self.assertEqual(RH.render_site(files, LANG), RH.render_site(reverse, LANG))


if __name__ == "__main__":
    unittest.main()
