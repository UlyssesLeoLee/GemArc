"""The interface front-end (``protoui/atl.py``, ``protoui/rows.py``): the grammar, AC-02 (the 5D format is untouched), AC-04 (the two
parsers refuse each other's files) and AC-18 (the header version).

The front-end is a COPY of ``format/atl.py`` and ``format/rows.py``: these tests pin the copy's behaviour and prove that nothing the
interface declares reaches the 5D parser. Synthetic ids only.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_ui_format.py"
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import expand as ex5
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.format import atl as atl5
from gemarc_mcp.atlas.format import rows as rw5
from gemarc_mcp.atlas.protoui import atl, rows

from . import synth, ui_synth

HERE = Path(__file__).resolve().parent
DESK_DIR = HERE / "fixtures" / "lending_desk"
HEAD = "#atlas-ui 1\n"
SCREENS = "## screens: id | title | purpose | opts\n"
WIDGETS = "## widgets: local | label | opts\n"

#: The 5D format at HEAD 81ab5b5, pinned before the interface family existed (AC-02).
FIVE_OPTION_NAMES = ['ack', 'alias', 'applies', 'area', 'by', 'card', 'case', 'chapter', 'class', 'ctx', 'dedupKey', 'design', 'edge', 'edges',
                     'element', 'err', 'errorCode', 'ev', 'fault', 'group', 'guard', 'id', 'init', 'kind', 'latencyMs', 'layer', 'level', 'machine',
                     'ms', 'nodes', 'note', 'outcomes', 'owner', 'params', 'parts', 'policy', 'props', 'repo', 'req', 'rewrite', 'role', 'room',
                     'ruleId', 'scope', 'section', 'src', 'status', 'target', 'tbd', 'terminal', 'tests', 'tier', 'trust', 'use', 'verif', 'when', 'why']
FIVE_SECTIONS = ["buildings", "floors", "rooms", "nodes", "edges", "evidences", "machines", "transitions", "tests", "cases", "events", "expect",
                 "interventions", "requirements", "tbds", "policies"]
FIVE_OPTION_REGEX_HEAD = "(?:^|[ \\t])(errorCode|latencyMs|outcomes|dedupKey|terminal|section|chapter|rewrite|applies|element|machine|ruleId|target|status|policy|params|design|tests|layer|owner|edges|parts|nodes|class|trust|scope|group|props|fault|guard|verif|level|alias|area|room|role|tier|kind|note|card|init|repo|case|edge|when|tbd|ack|src|ctx|err|why|use|req|ev|by|id|ms)=\\S"


def parse(text: str, name: str = "ui-10-x.atl"):
    return atl.parse_atl(text, name)


def rules_of(parsed) -> list:
    return [d.rule for d in parsed.diags]


class HeaderTests(unittest.TestCase):
    def test_the_header_is_atlas_ui_1_and_a_valid_file_has_no_diagnostic(self):
        parsed = parse(ui_synth.login_text(), "ui-10-login.atl")
        self.assertEqual(parsed.diags, [])
        self.assertEqual(len(parsed.rows), 15)
        self.assertEqual({r.section for r in parsed.rows}, {"screens", "variants", "widgets", "nav"})

    def test_a_missing_header_is_st001_but_the_rows_are_still_read(self):
        parsed = parse(ui_synth.login_text().replace(HEAD, ""))
        self.assertEqual(rules_of(parsed), ["ST-001"])
        self.assertEqual(len(parsed.rows), 15)

    def test_the_version_must_be_1_ac18(self):
        self.assertEqual(rules_of(parse(HEAD.replace("1", "2") + SCREENS)), ["ST-004"])
        self.assertEqual(rules_of(parse(HEAD.replace("1", "0") + SCREENS)), ["ST-004"])

    def test_the_header_takes_no_options(self):
        self.assertEqual(rules_of(parse("#atlas-ui 1 mode=tobe\n")), ["ST-004"])
        self.assertEqual(rules_of(parse("#atlas-ui 1 mode=\n")), ["ST-004"])
        self.assertEqual(rules_of(parse("#atlas-ui 1 \"broken\n")), ["ST-007"])

    def test_a_repeated_header_is_st004(self):
        self.assertEqual(rules_of(parse(HEAD + HEAD + SCREENS)), ["ST-004"])

    def test_the_header_may_follow_comments_and_blank_lines(self):
        self.assertEqual(rules_of(parse("// first\n\n" + HEAD + SCREENS + "SCR-A | A | a.\n")), [])

    def test_a_bom_and_crlf_are_accepted_and_give_the_same_rows(self):
        text = HEAD + SCREENS + "SCR-A | A | a. | start=true\n"
        plain = parse(text)
        for variant in ("﻿" + text, text.replace("\n", "\r\n")):
            parsed = parse(variant)
            self.assertEqual(parsed.diags, [])
            self.assertEqual([r.values for r in parsed.rows], [r.values for r in plain.rows])
            self.assertEqual(parsed.size, plain.size)

    def test_a_lone_cr_is_st007(self):
        self.assertIn("ST-007", rules_of(parse(HEAD + SCREENS + "SCR-A | A | a.\rSCR-B | B | b.\n")))

    def test_an_empty_file_is_st001(self):
        self.assertEqual(rules_of(parse("")), ["ST-001"])
        self.assertEqual(rules_of(parse("// only a comment\n")), ["ST-001"])


class SectionTests(unittest.TestCase):
    def test_the_six_sections_and_their_headers(self):
        self.assertEqual(list(rows.SECTIONS), ["screens", "variants", "widgets", "nav", "conform", "diffs"])
        self.assertEqual(rows.SECTIONS["widgets"].header, "## widgets: local | label | opts")
        self.assertEqual(rows.SECTIONS["nav"].column_names, ("from", "to", "label"))
        self.assertEqual(rows.PROTOTYPE_SECTIONS + rows.VERDICT_SECTIONS, tuple(rows.SECTIONS))

    def test_a_prototype_slice_refuses_the_verdict_sections_and_the_other_way_round(self):
        verdict = HEAD + "## conform: screen | verdict | basis | opts\nSCR-A | unchecked | fe:a.tsx:1 | checked=0\n"
        in_prototype = parse(verdict, "ui-10-x.atl")
        self.assertEqual(rules_of(in_prototype), ["ST-004"])
        self.assertIn("ui-verdict-*", in_prototype.diags[0].hint)
        self.assertEqual(in_prototype.rows, [])
        self.assertEqual(rules_of(parse(verdict, "ui-verdict-fe.atl")), [])
        in_verdict = parse(HEAD + SCREENS + "SCR-A | A | a.\n", "ui-verdict-fe.atl")
        self.assertEqual(rules_of(in_verdict), ["ST-004"])
        self.assertEqual(in_verdict.rows, [])

    def test_a_name_that_only_starts_with_the_verdict_word_is_a_prototype_slice(self):
        self.assertFalse(atl.is_verdict_name("ui-verdict"))
        self.assertFalse(atl.is_verdict_name("ui-verdicts"))
        self.assertTrue(atl.is_verdict_name("ui-verdict-fe"))
        self.assertTrue(atl.is_verdict_name("ui-verdict-fe.atl"))

    def test_an_unknown_section_and_a_header_that_does_not_echo_the_columns(self):
        unknown = parse(HEAD + "## widget: local | label | opts\nx | X | kind=text screen=A\n")
        self.assertEqual(rules_of(unknown), ["ST-004"])
        self.assertIn("did you mean widgets", unknown.diags[0].hint)
        wrong = parse(HEAD + "## screens: title | id | purpose | opts\nSCR-A | A | a.\n")
        self.assertEqual(rules_of(wrong), ["ST-004"])
        self.assertEqual(wrong.rows, [])

    def test_the_opts_column_may_be_left_out_of_the_header(self):
        self.assertEqual(rules_of(parse(HEAD + "## screens: id | title | purpose\nSCR-A | A | a.\n")), [])
        self.assertEqual(rules_of(parse(HEAD + "## screens\nSCR-A | A | a.\n")), [])

    def test_final_names_a_section_of_the_slice_kind(self):
        parsed = parse(HEAD + SCREENS + "SCR-A | A | a.\n@final nav\n")
        self.assertEqual((parsed.diags, parsed.finals), ([], ["nav"]))
        self.assertEqual(rules_of(parse(HEAD + "@final conform\n")), ["ST-004"])
        self.assertEqual(rules_of(parse(HEAD + "@final tubes\n")), ["ST-004"])

    def test_pending_and_the_unknown_directive(self):
        parsed = parse(HEAD + SCREENS + "SCR-A | A | a.\n@pending SCR-B, SCR-C\n")
        self.assertEqual((parsed.diags, parsed.pendings), ([], ["SCR-B", "SCR-C"]))
        self.assertEqual(rules_of(parse(HEAD + "@pending\n")), ["ST-001"])
        self.assertEqual(rules_of(parse(HEAD + "@later x\n")), ["ST-004"])


class RowTests(unittest.TestCase):
    def test_arity_is_exact_st005(self):
        self.assertEqual(rules_of(parse(HEAD + SCREENS + "SCR-A | A\n")), ["ST-005"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "x | X | y | kind=text\n")), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + "## nav: from | to | label\nSCR-A | SCR-B\n")), ["ST-005"])
        self.assertEqual(rules_of(parse(HEAD + "## nav: from | to | label\nSCR-A | SCR-B | go | x\n")), ["ST-007"])   # a fourth cell is read as options

    def test_an_option_token_inside_a_text_cell_is_st006(self):
        parsed = parse(HEAD + WIDGETS + "x | kind=text w=3 | screen=SCR-A\n")
        self.assertEqual(rules_of(parsed), ["ST-006"])
        self.assertEqual(parsed.rows, [])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "x | \"kind=text w=3\" | screen=SCR-A kind=text\n")), [])

    def test_a_5d_only_option_name_is_not_a_trap_in_the_interface_family(self):
        text = HEAD + WIDGETS + "x | Retries until ev=sync passes | screen=SCR-A kind=text\n"
        self.assertEqual(rules_of(parse(text)), [])

    def test_a_pipe_in_text_is_written_with_a_backslash_and_a_quoted_cell_keeps_quotes(self):
        parsed = parse(HEAD + SCREENS + 'SCR-A | A \\| B | "say \\"hi\\" | now"\n')
        self.assertEqual(parsed.diags, [])
        self.assertEqual((parsed.rows[0].values["title"], parsed.rows[0].values["purpose"]), ("A | B", 'say "hi" | now'))
        self.assertEqual(rules_of(parse(HEAD + SCREENS + 'SCR-A | A | say "hi"\n')), ["ST-007"])

    def test_options_values_bare_quoted_json_and_lists(self):
        text = HEAD + WIDGETS + ('x | X | screen=SCR-A kind=table w=fill data={"cols":["a","b"],"rows":[["1","2"]]} in=loading,empty req=RQ-1,RQ-2\n'
                                 'y | Y | screen=SCR-A kind=text bind="a.b" emph=muted\n')
        parsed = parse(text)
        self.assertEqual(parsed.diags, [])
        first = parsed.rows[0].values
        self.assertEqual((first["w"], first["in"], first["req"], first["data"]["cols"]), ("fill", ["loading", "empty"], ["RQ-1", "RQ-2"], ["a", "b"]))

    def test_malformed_options_are_st007(self):
        for options in ('kind=', 'kind=text kind=text', 'bind="open', 'data={"a":1', 'cols=two', 'start=yes', 'in=a,,b', 'screen=SCR-A, kind=text'):
            parsed = parse(HEAD + WIDGETS + f"x | X | screen=SCR-A {options}\n")
            self.assertTrue({"ST-007", "ST-001", "ST-003"} & set(rules_of(parsed)) or parsed.rows == [], options)
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + 'x | X | screen=SCR-A kind=text bind="open\n')), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "x | X | screen=SCR-A kind=text cols=two\n")), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + SCREENS + "SCR-A | A | a. | start=yes\n")), ["ST-007"])

    def test_a_key_that_is_a_positional_cell_is_st007_and_an_unknown_key_is_st004(self):
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "x | X | local=y screen=SCR-A kind=text\n")), ["ST-007"])
        unknown = parse(HEAD + WIDGETS + "x | X | screen=SCR-A kind=text colour=red\n")
        self.assertEqual(rules_of(unknown), ["ST-004"])
        self.assertIn("one of:", unknown.diags[0].expected)

    def test_an_authored_derived_key_is_dv001_not_st004(self):
        for key in ("order=1", "depth=2", "id=x", "parentId=y", "container=true"):
            self.assertEqual(rules_of(parse(HEAD + WIDGETS + f"x | X | screen=SCR-A kind=text {key}\n")), ["DV-001"], key)
        self.assertEqual(rules_of(parse(HEAD + SCREENS + "SCR-A | A | a. | widgetIds=x\n")), ["DV-001"])

    def test_the_verdict_screen_hash_option_does_not_repeat_the_positional_cell(self):
        text = HEAD + "## conform: screen | verdict | basis | opts\nSCR-A | differs | fe:a.tsx:3 | sha=0a1b2c3d checked=4 repo=fe\n"
        parsed = parse(text, "ui-verdict-fe.atl")
        self.assertEqual(parsed.diags, [])
        self.assertEqual(parsed.rows[0].values["sha"], "0a1b2c3d")
        repeated = parse(text.replace("sha=0a1b2c3d", "screen=0a1b2c3d"), "ui-verdict-fe.atl")
        self.assertEqual(rules_of(repeated), ["ST-007"])

    def test_the_size_limit_refuses_the_whole_file_st008(self):
        parsed = parse(ui_synth.login_text() + "// " + "x" * 66000 + "\n")
        self.assertEqual(rules_of(parsed), ["ST-008"])
        self.assertEqual(parsed.rows, [])
        self.assertEqual(rules_of(parse(HEAD + "\x00")), ["ST-008"])
        pre: list = []
        atl.decode_bytes(b"#atlas-ui 1\n\xff\xfe", "ui-x.atl", pre)
        self.assertEqual([d.rule for d in pre], ["ST-008"])

    def test_an_invisible_control_character_in_a_bare_cell_is_st007(self):
        self.assertEqual(rules_of(parse(HEAD + SCREENS + "SCR-A | A​ | a.\n")), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + SCREENS + 'SCR-A | "A\\u200b" | a.\n')), [])


class SetTests(unittest.TestCase):
    def test_set_gives_the_default_of_an_option_and_tilde_the_default_of_a_cell(self):
        text = HEAD + WIDGETS + "@set screen=SCR-A kind=text\nx | X\ny | Y | kind=button\n@set kind=\nz | Z | kind=link\n"
        parsed = parse(text)
        self.assertEqual(parsed.diags, [])
        self.assertEqual([(r.values["screen"], r.values["kind"]) for r in parsed.rows], [("SCR-A", "text"), ("SCR-A", "button"), ("SCR-A", "link")])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "@set screen=SCR-A kind=text\n@set kind=\nz | Z\n")), ["ST-001"])

    def test_a_set_default_resets_at_the_next_section_header(self):
        text = HEAD + WIDGETS + "@set screen=SCR-A kind=text\nx | X\n" + WIDGETS + "y | Y\n"
        self.assertEqual(rules_of(parse(text)), ["ST-001", "ST-001"])

    def test_tilde_needs_a_default_and_set_takes_scalars_only(self):
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "x | ~ | screen=SCR-A kind=text\n")), ["ST-001"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "@set req=RQ-1\n")), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "@set data={\"a\":1}\n")), ["ST-007"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "@set order=1\n")), ["DV-001"])
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + "@set colour=red\n")), ["ST-004"])
        self.assertEqual(rules_of(parse(HEAD + "@set screen=SCR-A\n")), ["ST-004"])

    def test_a_quoted_set_value_and_a_rows_own_option_wins(self):
        parsed = parse(HEAD + WIDGETS + '@set screen=SCR-A kind=text bind="a.b"\nx | X\ny | Y | bind=c\n')
        self.assertEqual([r.values["bind"] for r in parsed.rows], ["a.b", "c"])


class FiveDUntouchedTests(unittest.TestCase):
    """AC-02: importing the interface family and parsing with it changes nothing about the 5D format."""

    def test_the_5d_section_table_the_option_union_and_the_hint_text_are_the_pinned_head_values(self):
        parse(ui_synth.login_text())      # use the interface parser first
        self.assertEqual(list(rw5.SECTIONS), FIVE_SECTIONS)
        self.assertEqual(sorted(rw5.ALL_OPTION_NAMES), FIVE_OPTION_NAMES)
        names = lambda pattern: sorted(pattern.rsplit("(", 1)[1].split(")")[0].split("|"))   # the text order of equal-length names depends on the hash seed
        self.assertEqual(names(rw5._ANY_OPTION_RE.pattern), names(FIVE_OPTION_REGEX_HEAD))
        self.assertEqual(atl5.ALLOWED_SECTIONS, ", ".join(FIVE_SECTIONS))
        self.assertIs(atl5.SECTIONS, rw5.SECTIONS)

    def test_the_two_tables_are_different_objects_and_share_no_section_name(self):
        self.assertIsNot(rows.SECTIONS, rw5.SECTIONS)
        self.assertFalse(set(rows.SECTIONS) & set(rw5.SECTIONS))
        self.assertIsNot(rows.Row, rw5.Row)

    def test_the_interface_option_names_are_not_5d_text_traps(self):
        text = "#atlas 1\n## nodes: id | name | sub | opts\nsvc.a | Retries until in=sync and w=3 passes | bind=x screen=y | room=rm.a kind=Module\n"
        parsed = atl5.parse_atl(text, "10-x.atl")
        self.assertNotIn("ST-006", [d.rule for d in parsed.diags])
        for name in ("in", "w", "screen", "bind", "emph", "cols", "frame", "route", "on", "text", "align", "h", "data", "api", "sha", "checked", "found", "expected"):
            if name not in FIVE_OPTION_NAMES:
                self.assertNotIn(name, rw5.ALL_OPTION_NAMES, name)
        self.assertIn("ST-006", rules_of(parse(HEAD + WIDGETS + "x | Retries until in=sync | screen=SCR-A kind=text\n")))

    def test_the_5d_corpora_parse_clean_with_the_5d_parser_after_the_interface_parser_ran(self):
        parse(ui_synth.login_text())
        corpora = [{p.name: p.read_text(encoding="utf-8") for p in sorted(DESK_DIR.glob("*.atl"))}]
        for seed in (1, 2, 3):
            corpora.append({name: "#atlas 1\n" + text for name, text in synth.atl_sections_text(synth.generate("reference", seed=seed)).items()})
        for corpus in corpora:
            for name, text in corpus.items():
                self.assertEqual([d.rule for d in atl5.parse_atl(text, name + ".atl").diags], [], name)

    def test_the_lending_desk_fixture_still_expands_to_its_golden_bytes(self):
        sources = {p.name: p.read_text(encoding="utf-8") for p in sorted(DESK_DIR.glob("*.atl"))}
        done = ex5.expand_sources(sources, json.loads((DESK_DIR / "meta.json").read_text(encoding="utf-8")))
        self.assertEqual(am.canonical_bytes(done.model), (DESK_DIR / "expected.model.json").read_bytes())
        self.assertEqual(am.model_sha8(done.model), "1703af87")
        self.assertEqual(done.diagnostics, [])

    def test_the_synthetic_presets_keep_their_pinned_model_hashes(self):
        pinned = {("reference", 1): "470db2db", ("reference", 2): "ff72fd42", ("reference", 3): "cf5613b2"}
        for (preset, seed), sha in pinned.items():
            self.assertEqual(am.model_sha8(synth.generate(preset, seed=seed)), sha, (preset, seed))


class CrossReadTests(unittest.TestCase):
    """AC-04: the two parsers refuse each other's files with ST-001 and keep no row."""

    def test_the_5d_parser_reads_an_interface_file_as_st001_and_keeps_no_row(self):
        for text in (ui_synth.login_text(), *ui_synth.sample12_sources().values()):
            parsed = atl5.parse_atl(text, "ui-10-x.atl")
            self.assertEqual(parsed.rows, [])
            self.assertIn("ST-001", [d.rule for d in parsed.diags])
            first = next(d for d in parsed.diags if d.rule == "ST-001")
            self.assertIn("#atlas 1", first.expected)

    def test_the_interface_parser_reads_a_5d_slice_as_st001_and_keeps_no_row(self):
        for path in sorted(DESK_DIR.glob("*.atl")):
            parsed = parse(path.read_text(encoding="utf-8"), "ui-10-x.atl")
            self.assertEqual(parsed.rows, [], path.name)
            first = next(d for d in parsed.diags if d.rule == "ST-001")
            self.assertIn("#atlas-ui 1", first.expected)
            self.assertIn("5D", first.hint)
        for name, text in synth.atl_sections_text(synth.generate("small", seed=4)).items():
            parsed = parse("#atlas 1\n" + text, "ui-20-x.atl")
            self.assertEqual(parsed.rows, [], name)
            self.assertIn("ST-001", [d.rule for d in parsed.diags], name)

    def test_a_5d_header_with_options_is_still_not_an_interface_header(self):
        parsed = parse("#atlas 1 mode=tobe\n" + SCREENS + "SCR-A | A | a.\n")
        self.assertIn("ST-001", rules_of(parsed))

    def test_a_header_that_only_starts_like_the_other_family_is_not_confused(self):
        self.assertFalse(atl5.HEADER_RE.match("#atlas-ui 1"))
        self.assertFalse(atl.HEADER_RE.match("#atlas 1"))
        self.assertFalse(atl.HEADER_RE.match("#atlas-uix 1"))
        self.assertTrue(atl.HEADER_RE.match("#atlas-ui\t1"))


class HostileTextTests(unittest.TestCase):
    """The front-end, the expander and the rules never raise on hostile text: a defect is a diagnostic."""

    TOKENS = ("|", '"', "{", "}", "[", "]", "=", "\n", "@set ", "@final", "#atlas-ui 1", "## ", "~", "\\", "\x00", "​", "界", "kind=", "screen=",
              "parent=", "in=", "SCR-LOGIN.", "#", ":", ",", " ", "\t", "\r", "﻿")

    def test_four_hundred_seeded_mutations_of_the_example_never_raise(self):
        from gemarc_mcp.atlas.protoui import rules

        rng = synth.Rng(11, "ui-fuzz")
        base = ui_synth.login_text()
        for n in range(400):
            text = base
            for _ in range(1 + rng.below(4)):
                at = rng.below(len(text) + 1)
                kind = rng.below(3)
                if kind == 0:
                    text = text[:at] + text[at + 1 + rng.below(12):]
                elif kind == 1:
                    text = text[:at] + rng.choice(self.TOKENS) + text[at:]
                else:
                    lines = text.split("\n")
                    row = rng.below(len(lines))
                    lines.insert(rng.below(len(lines) + 1), lines[row])
                    text = "\n".join(lines)
            for mode in ("slice", "full"):
                result = rules.check_sources({"ui-10-fuzz.atl": text}, mode=mode, incoming=["ui-10-fuzz.atl"] if mode == "slice" else None)
                for d in result.diagnostics:
                    self.assertIn(d.severity, ("ERROR", "WARN", "INFO"), n)
                    self.assertTrue(d.rule and d.at, n)
                self.assertIsInstance(result.render("text"), str)

    def test_deeply_nested_json_and_very_long_lines_are_diagnostics_not_crashes(self):
        deep = '{"a":' * 200 + "1" + "}" * 200
        self.assertEqual(rules_of(parse(HEAD + WIDGETS + f"x | X | screen=SCR-A kind=table data={deep}\n")), ["ST-007"])
        long_cell = "界" * 100000
        self.assertEqual(rules_of(parse(HEAD + SCREENS + f"SCR-A | {long_cell} | p\n")), ["ST-008"])
        many = HEAD + SCREENS + "".join(f"SCR-{i} | T | p\n" for i in range(6000))
        self.assertIn("ST-008", rules_of(parse(many)))

    def test_a_row_of_binary_noise_is_refused_without_a_traceback(self):
        rng = synth.Rng(5, "noise")
        for _ in range(50):
            noise = "".join(chr(rng.below(0x2FFF)) for _ in range(rng.randint(1, 60)))
            noise = noise.replace("\n", " ").replace("\x00", " ")
            parsed = parse(HEAD + SCREENS + noise + "\n")
            self.assertIsInstance(parsed.diags, list)


class SourcesTests(unittest.TestCase):
    def test_the_copied_front_end_does_not_import_the_5d_format_modules(self):
        for name in ("atl.py", "rows.py"):
            source = (HERE.parents[1] / "src" / "gemarc_mcp" / "atlas" / "protoui" / name).read_text(encoding="utf-8")
            self.assertNotIn("format", [line.split()[1] for line in source.splitlines() if line.startswith("from .. import")])
            self.assertNotRegex(source, r"from \.\.format|import format|atlas\.format")

    def test_the_text_cell_and_option_helpers_are_the_ones_the_expander_expects(self):
        self.assertEqual(rows.looks_like_option("a kind=b"), "kind=b")
        self.assertIsNone(rows.looks_like_option("plain text"))
        self.assertEqual(rows.closest("widgit", ["widgets", "nav"]), "widgets")
        self.assertEqual(rows.coerce("bool", rows.Bare("true")), True)
        self.assertEqual(rows.coerce("ids", rows.Bare("a,b")), ["a", "b"])
        with self.assertRaises(rows.FieldError):
            rows.coerce("int", rows.Quoted("3"))


if __name__ == "__main__":
    unittest.main()
