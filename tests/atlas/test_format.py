"""Format core (design M1): .atl and .jsonl front-ends, expander, encoder, diagnostics.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_format.py"

Acceptance of M1 covered here: (a) the worked example expands byte-equal to its stored golden, (b) expand(parse(encode(m)))
equals m for generated models and every edge-case preset in both formats, (d) the size of the reference-scale encoding.
The fuzz gate (c) lives in test_format_fuzz.py. Synthetic id families only.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import encode, expand
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.format import atl, jsonl
from gemarc_mcp.atlas.format import rows as rw

from . import synth

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DESK = FIXTURES / "lending_desk"

HEAD = "#atlas 1\n"
SPACE = (
    "## buildings: id | name | opts\nzone.a | Alpha | trust=internal\n"
    "## floors: id | building | name | level\nlvl.a | zone.a | Ground | 0\n"
    "## rooms: id | floor | name | opts\nrm.a | lvl.a | Hall | tier=T2\n"
)
META = {"projectId": "unit", "title": "Unit", "languages": ["en-US"], "profile": "software", "repos": ["main"]}


def codes(parsed) -> list[str]:
    return [d.rule for d in parsed.diags]


def rules_of(ex) -> list[str]:
    return [d.rule for d in ex.diagnostics]


def expand_text(text: str, name: str = "t.atl", meta=META, **kw):
    return expand.expand_sources({name: text}, meta, **kw)


def desk_sources() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(DESK.glob("*.atl"))}


class GrammarTests(unittest.TestCase):
    def test_header_mode_comments_and_blank_lines(self):
        p = atl.parse_atl("\n// c\n#atlas 1 mode=tobe\n\n" + SPACE, "f.atl")
        self.assertEqual(codes(p), [])
        self.assertEqual(p.mode, "tobe")
        self.assertEqual([r.section for r in p.rows], ["buildings", "floors", "rooms"])
        self.assertEqual(p.rows[0].at, "f.atl:6")
        self.assertEqual(dict(p.rows[2].values), {"id": "rm.a", "floor": "lvl.a", "name": "Hall", "tier": "T2"})

    def test_crlf_and_bom_are_accepted_and_equal_to_lf(self):
        lf = atl.parse_atl(HEAD + SPACE, "f.atl")
        crlf = atl.parse_atl("\ufeff" + (HEAD + SPACE).replace("\n", "\r\n"), "f.atl")
        self.assertEqual(codes(crlf), [])
        self.assertEqual(lf.rows, crlf.rows)
        self.assertEqual([r.line for r in lf.rows], [r.line for r in crlf.rows])
        raw = atl.parse_atl_bytes(b"\xef\xbb\xbf" + (HEAD + SPACE).replace("\n", "\r\n").encode(), "f.atl")
        self.assertEqual((codes(raw), raw.rows), ([], lf.rows))

    def test_missing_or_wrong_header_line(self):
        self.assertEqual(codes(atl.parse_atl(SPACE, "f.atl")), ["ST-001"])
        self.assertIn("ST-004", codes(atl.parse_atl("#atlas 2\n" + SPACE, "f.atl")))
        self.assertIn("ST-004", codes(atl.parse_atl("#atlas 1 colour=red\n" + SPACE, "f.atl")))
        self.assertIn("ST-001", codes(atl.parse_atl("", "f.atl")))

    def test_set_scope_clear_and_tilde(self):
        text = (HEAD + "## evidences: id | file | anchor | topic | opts\n@set repo=main\nE-1 | a.py | def f( | why\n"
                "@set repo=\nE-2 | a.py | def g( | why\nE-3 | a.py | def h( | why | repo=other\n"
                "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.one | One | ~ | ev=E-1\n")
        p = atl.parse_atl(text, "f.atl")
        self.assertEqual([d.rule for d in p.diags], ["ST-001"])  # '~' for sub has no @set default
        evs = [r for r in p.rows if r.section == "evidences"]
        self.assertEqual([r.get("repo") for r in evs], ["main", None, "other"])
        self.assertEqual(evs[0].explicit & {"repo"}, frozenset())  # came from @set
        self.assertEqual(evs[2].explicit & {"repo"}, {"repo"})
        ok = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module sub=hi\nn.one | One | ~\n", "f.atl")
        self.assertEqual((codes(ok), ok.rows[0].get("sub")), ([], "hi"))
        # defaults do not leak into the next section instance of the same name
        again = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | s\n"
                              "## nodes: id | name | sub | opts\nn.b | B | s\n", "f.atl")
        self.assertEqual(codes(again), ["ST-001", "ST-001"])  # room and kind are required and now missing

    def test_set_takes_scalars_only(self):
        for line in ("@set ev=E-1", "@set props={\"a\":1}", "@set nope=1", "@set kind"):
            p = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n" + line + "\n", "f.atl")
            self.assertTrue(codes(p), line)

    def test_arity_is_exact_and_echoes_the_header(self):
        short = atl.parse_atl(HEAD + "## floors: id | building | name | level\nlvl.a | zone.a | Ground\n", "f.atl")
        self.assertEqual(codes(short), ["ST-005"])
        self.assertIn("id | building | name | level", short.diags[0].hint)
        extra = atl.parse_atl(HEAD + "## floors: id | building | name | level\nlvl.a | zone.a | Ground | 0 | 9\n", "f.atl")
        self.assertEqual(codes(extra), ["ST-005"])
        sub = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=r kind=Module\nn.a | A\n", "f.atl")
        self.assertEqual(codes(sub), ["ST-005"])

    def test_option_token_inside_a_text_cell_is_st006(self):
        p = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | text ev=E-1\n", "f.atl")
        self.assertEqual(codes(p), ["ST-006"])
        self.assertEqual(p.diags[0].got, "ev=E-1")
        # a quoted cell keeps such text
        q = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | \"text ev=E-1\"\n", "f.atl")
        self.assertEqual((codes(q), q.rows[0].get("sub")), ([], "text ev=E-1"))

    def test_pipes_escapes_and_quoted_cells(self):
        p = atl.parse_atl(HEAD + "## tests: id | title\nT-1 | a \\| b\nT-2 | \"a | b\"\nT-3 | back\\\\slash\nT-4 | c:\\dir\n", "f.atl")
        self.assertEqual(codes(p), [])
        self.assertEqual([r.get("title") for r in p.rows], ["a | b", "a | b", "back\\slash", "c:\\dir"])
        bad = atl.parse_atl(HEAD + "## tests: id | title\nT-1 | say \"hi\"\n", "f.atl")
        self.assertEqual(codes(bad), ["ST-007"])
        self.assertEqual(codes(atl.parse_atl(HEAD + "## tests: id | title\nT-1 | \"open\n", "f.atl")), ["ST-007"])
        self.assertEqual(codes(atl.parse_atl(HEAD + "## tests: id | title\nT-1 | \"a\" b\n", "f.atl")), ["ST-007"])

    def test_empty_cells_are_st001(self):
        for line in ("T-1 | ", "T-1 |   ", " | x"):
            self.assertIn("ST-001", codes(atl.parse_atl(HEAD + "## tests: id | title\n" + line + "\n", "f.atl")), line)

    def test_option_syntax_errors_are_st007_and_drop_the_row(self):
        base = HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\n"
        cases = ["n.a | A | s | ev", "n.a | A | s | ev=E-1 | verif=Inferred", "n.a | A | s | props={\"a\":", "n.a | A | s | props={\"a\":1}x",
                 "n.a | A | s | note=\"x", "n.a | A | s | ev=E-1 ev=E-2", "n.a | A | s | ev=", "n.a | A | s | group=a\"b",
                 "n.a | A | s | props={\"a\":1,\"a\":2}", "n.a | A | s | props=[1]", "n.a | A | s | props=plain", "n.a | A | s | props={\"x\":NaN}"]
        for row in cases:
            p = atl.parse_atl(base + row + "\n", "f.atl")
            self.assertTrue(p.diags, row)
            self.assertEqual(p.rows, [], row)
            self.assertTrue(all(d.rule in ("ST-007", "ST-001", "ST-004") for d in p.diags), (row, codes(p)))

    def test_typed_values(self):
        text = (HEAD + "## events: type | from | to | opts\n@set case=C-1\n"
                "Call | a | b | latencyMs=20 ack=true section=false ms=5 params={\"k\":[1,2]} group=g1\n"
                "Call | a | b | latencyMs=[10,20] when={\"node\":\"a\",\"is\":[\"Idle\"]}\n")
        p = atl.parse_atl(text, "f.atl")
        self.assertEqual(codes(p), [])
        v = p.rows[0].values
        self.assertEqual((v["latencyMs"], v["ack"], v["section"], v["ms"], v["params"], v["group"]), (20, True, False, 5, {"k": [1, 2]}, "g1"))
        self.assertEqual(p.rows[1].values["latencyMs"], [10, 20])
        for bad in ("latencyMs=twenty", "latencyMs=\"20\"", "ack=yes", "ack=True", "ms=1.5", "latencyMs=[]", "latencyMs=[1,\"2\"]"):
            q = atl.parse_atl(HEAD + "## events: type | from | to | opts\n@set case=C-1\nCall | a | b | " + bad + "\n", "f.atl")
            self.assertEqual(codes(q), ["ST-007"], bad)

    def test_unknown_section_column_option_and_derived_fields(self):
        p = atl.parse_atl(HEAD + "## nodez: id | name\nn | N\n## nodes: id | name | opts\nn | N\n", "f.atl")
        self.assertEqual(codes(p), ["ST-004", "ST-004"])  # unknown section; wrong header (its rows are skipped quietly)
        self.assertIn("nodes", p.diags[0].hint)
        q = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=r kind=Module\nn | N | s | colour=red\n", "f.atl")
        self.assertEqual(codes(q), ["ST-004"])
        self.assertIn("ev", q.diags[0].expected)
        d = atl.parse_atl(HEAD + "## nodes: id | name | sub | opts\n@set room=r kind=Module\nn | N | s | portIds=[]\n", "f.atl")
        self.assertEqual(codes(d), ["DV-001"])
        self.assertIn("derived", d.diags[0].hint)
        e = atl.parse_atl(HEAD + "## edges: from | rel | to | label | opts\na | calls | b | go | id=a:calls:b\n", "f.atl")
        self.assertEqual(codes(e), ["DV-001"])
        ev = atl.parse_atl(HEAD + "## evidences: id | file | anchor | topic | opts\nE | a.py | x | y | lineRange=[1,2]\n", "f.atl")
        self.assertEqual(codes(ev), ["DV-001"])

    def test_final_and_pending_declarations(self):
        p = atl.parse_atl(HEAD + "## edges: from | rel | to | label | opts\n@final edges\n@pending a.b,c.d\n@final nodes\n", "f.atl")
        self.assertEqual((codes(p), p.finals, p.pendings), ([], ["edges", "nodes"], ["a.b", "c.d"]))
        self.assertEqual(codes(atl.parse_atl(HEAD + "@final nonsense\n", "f.atl")), ["ST-004"])

    def test_size_and_encoding_are_st008(self):
        big = atl.parse_atl(HEAD + "## tests: id | title\n" + "T-1 | x\n" * 9000, "f.atl")
        self.assertIn("ST-008", codes(big))
        self.assertIn("ST-008", codes(atl.parse_atl_bytes(b"#atlas 1\n\xff\xfe\n", "f.atl")))
        self.assertIn("ST-008", codes(atl.parse_atl(HEAD + "\x00\n", "f.atl")))
        self.assertEqual(codes(atl.parse_atl(HEAD + "## tests: id | title\nT-1 | x\n", "f.atl", max_bytes=10)), ["ST-008"])

    def test_unknown_extension_is_st008(self):
        self.assertEqual(codes(expand.parse_source("x.txt", "hi")), ["ST-008"])

    def test_hostile_text_never_raises(self):
        for text in ("\x00\x01\x02", "## ", "##", "@set", "@", "|||||", "\"", "{", "## nodes: id | name | sub | opts\n@set room=\"\n\"|\n",
                     HEAD + "## tests: id | title\n" + "\\" * 5000, HEAD + "## nodes: id | name | sub | opts\nx | " + "[" * 5000,
                     HEAD + "## nodes: id | name | sub | opts\n@set room=r kind=Module\nx | y | z | props=" + "{\"a\":" * 200 + "1" + "}" * 200):
            p = atl.parse_atl(text, "f.atl")
            expand.expand_slices([p], META)


class JsonlTests(unittest.TestCase):
    def test_rows_equal_the_atl_rows(self):
        model = synth.generate("small", 3)
        a = [r for name, text in sorted(encode.encode_atl(model).items()) for r in atl.parse_atl(text, name).rows]
        j = [r for name, text in sorted(encode.encode_jsonl(model).items()) for r in jsonl.parse_jsonl(text, name).rows]
        self.assertTrue(a)
        self.assertEqual([(r.section, dict(r.values)) for r in a], [(r.section, dict(r.values)) for r in j])

    def test_row_positions_are_file_lines(self):
        p = jsonl.parse_jsonl('{"_atlas":1}\n\n{"_section":"tests","id":"T-1","title":"x"}\n', "f.jsonl")
        self.assertEqual((codes(p), p.rows[0].at), ([], "f.jsonl:3"))

    def test_defects(self):
        header = '{"_atlas":1}\n'
        cases = {
            "ST-004": ['{"_section":"nodez"}', '{"_section":"tests","id":"T-1","title":"x","colour":1}', '{"x":1}', '{"_final":"nope"}'],
            "ST-001": ['{"_section":"tests","id":"T-1"}', '{"_section":"tests","id":"T-1","title":""}'],
            "ST-007": ['{"_section":"tests","id":"T-1","title":"x"', "[1]", '{"_section":"tests","id":"T-1","title":5}',
                       '{"_section":"events","type":"Call","from":"a","to":"b","case":"C","ack":"true"}',
                       '{"_section":"tests","id":"T-1","id":"T-2","title":"x"}'],
        }
        for rule, lines in cases.items():
            for line in lines:
                p = jsonl.parse_jsonl(header + line + "\n", "f.jsonl")
                self.assertIn(rule, codes(p), line)
                self.assertEqual(p.rows, [], line)
        d = jsonl.parse_jsonl(header + '{"_section":"nodes","id":"n","name":"N","sub":"s","room":"r","kind":"Module","portIds":[]}\n', "f.jsonl")
        self.assertEqual(codes(d), ["DV-001"])
        self.assertEqual(codes(jsonl.parse_jsonl('{"_section":"tests","id":"T-1","title":"x"}\n', "f.jsonl")), ["ST-001"])

    def test_control_lines_mode_final_pending(self):
        p = jsonl.parse_jsonl('{"_atlas":1,"mode":"tobe"}\n{"_final":"edges"}\n{"_pending":["a","b"]}\n', "f.jsonl")
        self.assertEqual((codes(p), p.mode, p.finals, p.pendings), ([], "tobe", ["edges"], ["a", "b"]))

    def test_a_row_named_section_option_is_not_the_section_key(self):
        text = '{"_atlas":1}\n{"_section":"events","type":"Call","from":"a","to":"b","case":"C-1","section":true}\n'
        p = jsonl.parse_jsonl(text, "f.jsonl")
        self.assertEqual((codes(p), p.rows[0].get("section")), ([], True))

    def test_crlf_bom_and_bytes(self):
        text = '{"_atlas":1}\r\n{"_section":"tests","id":"T-1","title":"x"}\r\n'
        p = jsonl.parse_jsonl_bytes(("\ufeff" + text).encode("utf-8"), "f.jsonl")
        self.assertEqual((codes(p), len(p.rows)), ([], 1))
        self.assertIn("ST-008", codes(jsonl.parse_jsonl_bytes(b'{"_atlas":1}\n\xff', "f.jsonl")))


class DiagTests(unittest.TestCase):
    def test_record_shape_is_stable(self):
        d = dg.st005("nodes row", "f.atl:3", 2, 3, "id | name | sub | opts")
        self.assertEqual(tuple(d.as_dict()), ("rule", "severity", "path", "at", "got", "expected", "hint", "derived"))
        self.assertEqual((d.rule, d.severity, d.at, d.derived), ("ST-005", "ERROR", "f.atl:3", False))
        self.assertEqual(json.loads(json.dumps(d.as_dict()))["rule"], "ST-005")

    def test_every_structural_rule_has_a_constructor_and_registry_entry(self):
        for rule in ("ST-001", "ST-002", "ST-003", "ST-004", "ST-005", "ST-006", "ST-007", "ST-008", "DV-001"):
            self.assertIn(rule, dg.STRUCTURAL_RULES)
        d = dg.st002("nodes[a]", "b.atl:2", "a", "a.atl:7", "nodes[a]")
        self.assertIn("a.atl:7", d.hint)
        self.assertEqual(dg.dv001("nodes.portIds", "f:1", "portIds").rule, "DV-001")

    def test_render_line_strips_control_characters_and_truncates(self):
        d = dg.st003("nodes.kind", "f:1", "x\x1b[31m" + "y" * 200, "a kind", "h")
        line = dg.render_line(d)
        self.assertNotIn("\x1b", line)
        self.assertLessEqual(len(dg.clip("y" * 200)), dg.GOT_LIMIT)
        self.assertTrue(line.startswith("E ST-003 f:1 nodes.kind got="))

    def test_sort_key_orders_errors_first_then_rule_then_line(self):
        a = dg.diag("EV-009", "p", "f:2", 1, "x", "h", severity="WARN")
        b = dg.st001("p", "f:10", 1, "x", "h")
        c = dg.st001("p", "f:9", 1, "x", "h")
        self.assertEqual(sorted([a, b, c], key=lambda d: d.sort_key()), [c, b, a])

    def test_bad_severity_is_refused(self):
        with self.assertRaises(ValueError):
            dg.diag("ST-001", "p", "f:1", 1, "x", "h", severity="FATAL")


class GoldenTests(unittest.TestCase):
    """Acceptance (a): the worked example expands byte-equal to its stored golden."""

    def expansion(self):
        meta = json.loads((DESK / "meta.json").read_text(encoding="utf-8"))
        return expand.expand_sources(desk_sources(), meta)

    def test_golden_model_is_byte_equal(self):
        ex = self.expansion()
        self.assertEqual(ex.diagnostics, [])
        self.assertEqual(am.canonical_bytes(ex.model), (DESK / "expected.model.json").read_bytes())
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_golden_equals_the_synthetic_fixture(self):
        self.assertEqual((DESK / "expected.model.json").read_bytes(), (FIXTURES / "lending_desk.model.json").read_bytes())
        self.assertEqual(self.expansion().model, synth.lending_desk("fixed"))

    def test_provenance_golden(self):
        ex = self.expansion()
        stored = json.loads((DESK / "expected.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(ex.provenance, stored)

    def test_every_provenance_entry_points_at_a_real_line(self):
        lines = {name: text.decode("utf-8").split("\n") for name, text in desk_sources().items()}
        for path, at in self.expansion().provenance.items():
            name, _, number = at.rpartition(":")
            self.assertTrue(1 <= int(number) <= len(lines[name]), (path, at))
            self.assertTrue(lines[name][int(number) - 1].strip(), (path, at))

    def test_jsonl_twin_of_the_worked_example_gives_the_same_model(self):
        model = synth.lending_desk("fixed")
        ex = expand.expand_sources(encode.encode_jsonl(model), encode.meta_of(model))
        self.assertEqual((ex.diagnostics, ex.model), ([], model))

    def test_slice_order_is_file_name_order_and_split_files_equal_one_file(self):
        sources = desk_sources()
        merged = "#atlas 1\n" + "".join(t.decode().split("\n", 1)[1] for _, t in sorted(sources.items()))
        meta = json.loads((DESK / "meta.json").read_text(encoding="utf-8"))
        one = expand.expand_sources({"all.atl": merged.replace("// Library lending desk: a synthetic example, not a real system.\n", "")}, meta)
        self.assertEqual(one.diagnostics, [])
        self.assertEqual(one.model, self.expansion().model)


class DerivationTests(unittest.TestCase):
    def test_spatial_and_evidence_derivations(self):
        text = HEAD + SPACE + (
            "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.code | Code | s | ev=E-1\nn.doc | Doc | s | ev=E-2\n"
            "n.none | None | s | verif=Hypothetical\nn.user | User | s | ev=E-3\n"
            "## evidences: id | file | anchor | topic | opts\n@set repo=main\nE-1 | src/a.py | def a( | code\nE-2 | docs/a.md | text | doc\n"
            "E-3 | user:chat | the user said so | quote\n")
        ex = expand_text(text)
        self.assertEqual(ex.diagnostics, [])
        nodes = {n["id"]: n for n in ex.model["nodes"]}
        self.assertEqual([nodes[i]["verification"] for i in ("n.code", "n.doc", "n.none", "n.user")],
                         ["Implemented", "Designed", "Hypothetical", "Designed"])
        self.assertEqual(nodes["n.code"]["props"], {"tier": "T2", "trust": "internal"})
        self.assertEqual(nodes["n.code"]["portIds"], ["n.code:in", "n.code:out"])
        ev = {e["evidenceId"]: e for e in ex.model["evidences"]}
        self.assertEqual((ev["E-1"]["sourceKind"], ev["E-1"]["repository"], ev["E-1"]["lineRange"]), ("source-code", "main", None))
        self.assertEqual((ev["E-2"]["sourceKind"], ev["E-2"]["verification"]), ("document", "Designed"))
        self.assertEqual((ev["E-3"]["sourceKind"], ev["E-3"]["repository"]), ("user-requirement", ""))
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_repo_on_a_user_evidence_is_dv001_but_the_set_default_is_ignored(self):
        ok = expand_text(HEAD + "## evidences: id | file | anchor | topic | opts\n@set repo=main\nE-3 | user:chat | q | t\n")
        self.assertEqual((ok.diagnostics, ok.model["evidences"][0]["repository"]), ([], ""))
        bad = expand_text(HEAD + "## evidences: id | file | anchor | topic | opts\nE-3 | user:chat | q | t | repo=main\n")
        self.assertEqual(rules_of(bad), ["DV-001"])

    def test_tobe_mode_caps_derived_levels(self):
        text = ("#atlas 1 mode=tobe\n" + SPACE + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | s | ev=E-1\n"
                "## evidences: id | file | anchor | topic | opts\nE-1 | src/a.py | def a( | t\n")
        ex = expand_text(text)
        self.assertEqual((ex.mode, ex.model["meta"]["mode"], ex.model["nodes"][0]["verification"], ex.model["evidences"][0]["verification"]),
                         ("tobe", "tobe", "Designed", "Designed"))
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_header_mode_wins_and_conflicts_are_reported(self):
        two = expand.expand_sources({"a.atl": "#atlas 1 mode=tobe\n", "b.atl": "#atlas 1 mode=asis\n"}, META)
        self.assertEqual((rules_of(two), two.mode), (["ST-007"], "tobe"))
        bad = expand.expand_sources({"a.atl": "#atlas 1 mode=maybe\n"}, META)
        self.assertEqual(rules_of(bad), ["ST-003"])
        self.assertEqual(expand.expand_sources({"a.atl": "#atlas 1\n"}, dict(META, mode="tobe")).mode, "tobe")

    def structure(self, extra: str = "", evidences: str = "E-1 | src/a.py | def a( | t\nE-2 | docs/a.md | text | t\n") -> str:
        return HEAD + SPACE + (
            "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nn.a | A | s | ev=E-1\nn.b | B | s | ev=E-2\nn.c | C | s\n"
            "@set kind=State\nm.one | Machine | s | ev=E-1\n"
            "## evidences: id | file | anchor | topic | opts\n" + evidences + extra)

    def test_edge_defaults_alias_and_inherited_evidence(self):
        text = self.structure("## edges: from | rel | to | label | opts\n"
                              "n.a | calls | n.b | go | ev=^\nn.a | calls | n.b | again | alias=n.again\nn.b | dependsOn | n.c | uses | ev=E-1 verif=Inferred\n"
                              "n.a | calls | m.one | to machine | ev=E-1\n")
        ex = expand_text(text, meta=META)
        self.assertEqual(ex.diagnostics, [])
        e = {x["id"]: x for x in ex.model["edges"]}
        self.assertEqual(sorted(e), ["n.a:calls:m.one", "n.a:calls:n.b", "n.again", "n.b:dependsOn:n.c"])
        first = e["n.a:calls:n.b"]
        self.assertEqual((first["evidenceIds"], first["verification"]), (["E-1", "E-2"], "Designed"))  # inherited: capped at Designed
        self.assertEqual((first["sourcePortId"], first["targetPortId"], first["static"], first["guardId"]), ("n.a:out", "n.b:in", False, None))
        self.assertEqual(e["n.again"]["evidenceIds"], [])
        self.assertEqual(e["n.again"]["verification"], "Hypothetical")
        self.assertEqual((e["n.b:dependsOn:n.c"]["static"], e["n.b:dependsOn:n.c"]["verification"]), (True, "Inferred"))
        self.assertEqual((e["n.a:calls:m.one"]["targetPortId"], e["n.a:calls:m.one"]["verification"]), ("m.one:in", "Implemented"))
        self.assertTrue(ex.provenance["edges[n.a:calls:n.b].evidenceIds"].startswith("t.atl:"))

    def test_repeated_triple_without_alias_is_st002_naming_both_places(self):
        ex = expand_text(self.structure("## edges: from | rel | to | label | opts\nn.a | calls | n.b | one\nn.a | calls | n.b | two\n"))
        d = [x for x in ex.diagnostics if x.rule == "ST-002"]
        self.assertEqual(len(d), 1)
        self.assertIn("alias", d[0].hint)
        self.assertIn("t.atl:", d[0].hint)
        self.assertEqual(len(ex.model["edges"]), 1)

    def test_machine_states_terminals_transition_ids_and_owns_edge(self):
        text = self.structure("## machines: id | initial | opts\nm.one | Draft | owner=n.a err=E_BAD ev=E-2\n"
                              "## transitions: from | to | opts\n@set machine=m.one\nDraft | Review\nReview | Done | guard={\"op\":\"present\",\"param\":\"x\"} err=E_G\n"
                              "Review | Draft | id=back\n")
        ex = expand_text(text)
        self.assertEqual(ex.diagnostics, [])
        m = ex.model["machines"][0]
        self.assertEqual((m["states"], m["terminal"], m["nodeId"], m["errorCode"]), (["Draft", "Review", "Done"], ["Done"], "m.one", "E_BAD"))
        self.assertEqual([t["id"] for t in m["transitions"]], ["m.one.Draft_to_Review", "m.one.Review_to_Done", "back"])
        self.assertEqual(m["transitions"][1]["guard"], {"op": "present", "param": "x"})
        owns = ex.model["edges"][-1]
        self.assertEqual((owns["id"], owns["label"], owns["relation"], owns["static"], owns["targetPortId"], owns["evidenceIds"]),
                         ("n.a:owns:m.one", "Machine", "owns", False, None, ["E-2"]))
        self.assertEqual(ex.model["ports"] and [p["nodeId"] for p in ex.model["ports"]].count("m.one"), 0)
        self.assertEqual(ex.model["nodes"][3]["portIds"], [])
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_owns_edge_comes_after_all_authored_edges_of_every_slice(self):
        a = self.structure("## machines: id | initial | opts\nm.one | Draft | owner=n.a\n## transitions: from | to | opts\n@set machine=m.one\nDraft | Done\n")
        b = HEAD + "## edges: from | rel | to | label | opts\nn.b | calls | n.c | later slice\n"
        ex = expand.expand_sources({"a.atl": a, "b.atl": b}, META)
        self.assertEqual([e["id"] for e in ex.model["edges"]], ["n.b:calls:n.c", "n.a:owns:m.one"])

    def test_duplicate_transition_ids_are_st002_and_need_an_explicit_id(self):
        text = self.structure("## machines: id | initial\nm.one | a_to\n## transitions: from | to | opts\n@set machine=m.one\n"
                              "a_to | b\na | to_b\n")
        ex = expand_text(text)
        self.assertEqual(rules_of(ex), ["ST-002"])
        self.assertEqual(len(ex.model["machines"][0]["transitions"]), 1)

    def test_orphan_transition_rows_are_reported(self):
        ex = expand_text(HEAD + "## transitions: from | to | opts\nA | B | machine=ghost\n")
        self.assertEqual(rules_of(ex), ["SM-001"])

    def test_cases_events_expect_policy_and_default_tests(self):
        text = self.structure(
            "## edges: from | rel | to | label | opts\nn.a | calls | n.b | go | ev=E-1\n"
            "## tests: id | title\nTS-1 | t one\nTS-2 | t two\n"
            "## requirements: id | title | source | opts\nRQ-1 | r | s | nodes=n.a,n.b tests=TS-1,TS-2\nRQ-2 | r2 | s | nodes=n.a,n.c tests=TS-2\n"
            "## cases: id | cat | title | opts\nC-1 | normal | one | req=RQ-1,RQ-2 policy={\"timeoutMs\":500}\nC-2 | abnormal | two | tests=[]\n"
            "## events: type | from | to | opts\n@set case=C-1\nCall | n.a | n.b | ruleId=R-1 edge=n.a:calls:n.b\n"
            "Return | n.b | n.a\n"
            "## expect: case | nodes | opts\nC-1 | n.a,n.b=Failed | by=user outcomes=[{\"ruleId\":\"R-1\",\"outcome\":\"OK\"}]\n")
        ex = expand_text(text)
        self.assertEqual(ex.diagnostics, [])
        c1, c2 = ex.model["cases"]
        self.assertEqual(c1["policy"], {"timeoutMs": 500, "retryMax": 0, "retryBackoffMs": 0})
        self.assertEqual(c2["policy"], am.default_policy())
        self.assertEqual((c1["linkedTestIds"], c2["linkedTestIds"]), (["TS-1", "TS-2"], []))
        self.assertEqual([e["seq"] for e in c1["events"]], [1, 2])
        self.assertEqual(c1["events"][0]["edge"], "n.a:calls:n.b")
        self.assertEqual(c1["expectedTerminalState"], {"n.a": "Completed", "n.b": "Failed"})
        self.assertEqual((c1["expectedBy"], c1["expectedOutcomes"]), ("user", [{"ruleId": "R-1", "outcome": "OK"}]))
        self.assertEqual(c2["expectedTerminalState"], {})
        self.assertNotIn("expectedBy", c2)
        req = {r["id"]: r for r in ex.model["requirements"]}
        self.assertEqual(req["RQ-1"]["edgeIds"], ["n.a:calls:n.b"])  # default: edges with both ends in nodes
        self.assertEqual(req["RQ-2"]["edgeIds"], [])
        self.assertEqual(ex.provenance["cases[C-1].events[2]"].split(":")[1], str(ex.provenance["cases[C-1].events[1]"].split(":")[1] and int(ex.provenance["cases[C-1].events[1]"].split(":")[1]) + 1))
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_event_and_expect_rows_of_unknown_cases_are_reported(self):
        ex = expand_text(self.structure("## events: type | from | to | opts\nCall | n.a | n.b | case=ghost\n## expect: case | nodes\nghost | n.a\n"))
        self.assertEqual(rules_of(ex), ["RF-004", "RF-004"])

    def test_requirement_edges_resolve_triples_and_aliases(self):
        text = self.structure("## edges: from | rel | to | label | opts\nn.a | calls | n.b | go\nn.a | calls | n.b | again | alias=n.again\n"
                              "## requirements: id | title | source | opts\nRQ-1 | r | s | edges=n.a:calls:n.b,n.again\n")
        ex = expand_text(text)
        self.assertEqual(ex.model["requirements"][0]["edgeIds"], ["n.a:calls:n.b", "n.again"])

    def test_interventions_targets_params_parts_and_elements(self):
        text = self.structure(
            "## edges: from | rel | to | label | opts\nn.a | calls | n.b | go\nn.b | calls | n.c | go on\n"
            "## cases: id | cat | title\nC-1 | normal | one\n## events: type | from | to | opts\n@set case=C-1\nCall | n.a | n.b | ruleId=R-1\n"
            "## interventions: id | type | description | opts\n"
            "IV-1 | disable_module | d | target=n.c class=DIVERGED terminal={\"n.c\":\"Blocked\"} note=\"a note\" why=reason applies=C-1\n"
            "IV-2 | disable_edge | d | target=n.a:calls:n.b class=CONVERGED applies=C-1\n"
            "IV-3 | change_timeout | d | params={\"timeoutMs\":100} applies=C-1\n"
            "IV-4 | delay_message | d | target=n.a:calls:n.b params={\"addLatencyMs\":50,\"targetRuleId\":\"R-1\"} applies=C-1\n"
            "IV-5 | combined_intervention | d | parts=[{\"type\":\"disable_module\",\"targetNodeId\":\"n.c\"},{\"type\":\"change_retry\",\"retryMax\":2}] applies=C-1\n"
            "IV-6 | insert_element | d | element=el.cache use=Cache rewrite=n.a:calls:n.b,n.b:calls:n.c=keep status=DRAFT applies=C-1\n"
            "IV-7 | insert_element | d | element={\"elementId\":\"el.q\",\"kind\":\"MessageQueue\",\"role\":\"buffer\"} rewrite=n.b:calls:n.c applies=C-1\n")
        ex = expand_text(text)
        self.assertEqual(ex.diagnostics, [])
        iv = {i["interventionId"]: i for i in ex.model["interventions"]}
        self.assertEqual(iv["IV-1"]["targetNodeId"], "n.c")
        self.assertEqual((iv["IV-1"]["note"], iv["IV-1"]["why"], iv["IV-1"]["expect"]), ("a note", "reason", {"outcomeClass": "DIVERGED", "terminal": {"n.c": "Blocked"}}))
        self.assertEqual(iv["IV-2"]["targetEdgeId"], "n.a:calls:n.b")
        self.assertEqual(iv["IV-3"]["timeoutMs"], 100)
        self.assertEqual((iv["IV-4"]["targetEdgeId"], iv["IV-4"]["addLatencyMs"], iv["IV-4"]["targetRuleId"]), ("n.a:calls:n.b", 50, "R-1"))
        self.assertEqual(len(iv["IV-5"]["parts"]), 2)
        el = iv["IV-6"]["element"]
        self.assertEqual((el["elementId"], el["kind"]), ("el.cache", "Cache"))
        self.assertEqual(el["inputPorts"], [{"id": "el.cache:in", "type": "Message"}])
        self.assertEqual([r["disposition"] for r in iv["IV-6"]["rewrite"]], ["replace", "keep"])
        self.assertEqual([c["id"] for c in iv["IV-6"]["rewrite"][0]["chain"]], ["el.cache:1", "el.cache:2"])
        self.assertEqual(iv["IV-6"]["rewrite"][1]["chain"], [])
        self.assertEqual(iv["IV-7"]["element"]["role"], "buffer")
        self.assertEqual(am.contract_issues(ex.model), [])

    def test_element_templates_hook_merges_a_body(self):
        text = self.structure("## edges: from | rel | to | label | opts\nn.a | calls | n.b | go\n## cases: id | cat | title\nC-1 | normal | one\n"
                              "## interventions: id | type | description | opts\nIV-6 | insert_element | d | applies=C-1 element=el.cache use=Cache rewrite=n.a:calls:n.b\n")
        ex = expand_text(text, element_templates={"Cache": {"role": "buffers", "latencyMs": 1}})
        self.assertEqual((ex.model["interventions"][0]["element"]["role"], ex.model["interventions"][0]["element"]["latencyMs"]), ("buffers", 1))

    def test_intervention_structure_errors(self):
        base = self.structure("## cases: id | cat | title\nC-1 | normal | one\n## interventions: id | type | description | opts\n")
        for row, rule in (("IV | disable_module | d", "ST-001"), ("IV | disable_module | d | target=n.a params={\"timeoutMs\":5}", "ST-004"),
                          ("IV | change_timeout | d | target=n.a", "ST-004"), ("IV | insert_element | d", "ST-001"),
                          ("IV | insert_element | d | element=el.x", "ST-001"), ("IV | disable_module | d | target=n.a element=el.x", "ST-004"),
                          ("IV | change_timeout | d | params={\"bogus\":1}", "ST-004"), ("IV | delay_message | d | params={\"addLatencyMs\":5}", "ST-001"),
                          ("IV | nonsense | d | target=n.a", "ST-003"), ("IV | insert_element | d | element=el.x use=Cache rewrite=n.a:calls:ghost", "RF-002"),
                          ("IV | insert_element | d | element=el.x use=Cache rewrite=n.a:calls:n.b=Keep", "ST-003")):
            row += (" | " if row.count(" | ") == 2 else " ") + "applies=C-1"
            ex = expand_text(base + row + "\n")
            self.assertIn(rule, rules_of(ex), row)

    def test_unknown_values_are_kept_with_st003(self):
        text = HEAD + ("## buildings: id | name | opts\nzone.a | Alpha | trust=moat\n## nodes: id | name | sub | opts\n@set room=rm.a kind=Gizmo\nn.a | A | s\n"
                       "## edges: from | rel | to | label | opts\nn.a | orchestrates | n.a | self\n")
        ex = expand_text(text)
        self.assertEqual(rules_of(ex), ["ST-003", "ST-003", "ST-003"])
        self.assertEqual((ex.model["space"]["buildings"][0]["trust"], ex.model["nodes"][0]["kind"], ex.model["edges"][0]["relation"]),
                         ("moat", "Gizmo", "orchestrates"))
        self.assertEqual(ex.model["edges"][0]["id"], "n.a:orchestrates:n.a")

    def test_enum_hints_are_case_aware(self):
        ex = expand_text(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=module\nn.a | A | s\n")
        self.assertEqual(rules_of(ex), ["ST-003"])
        self.assertIn("Module", ex.diagnostics[0].hint)
        old = expand_text(HEAD + "## cases: id | cat | title\nC-1 | combined | t\n")
        self.assertIn("combination", old.diagnostics[0].hint)

    def test_id_grammar_violations_drop_the_row_with_st003(self):
        ex = expand_text(HEAD + "## nodes: id | name | sub | opts\n@set room=rm.a kind=Module\nbad:id | A | s\nn.\u00e9 | A | s\nn.ok | A | s\n")
        self.assertEqual(rules_of(ex), ["ST-003", "ST-003"])
        self.assertEqual([n["id"] for n in ex.model["nodes"]], ["n.ok"])

    def test_duplicate_ids_across_files_name_both_locations(self):
        a = HEAD + "## tests: id | title\nX-1 | one\n"
        b = HEAD + "## tbds: id | title | decision\nX-1 | two | three\n"
        ex = expand.expand_sources({"a.atl": a, "b.atl": b}, META)
        self.assertEqual(rules_of(ex), ["ST-002"])
        self.assertEqual(ex.diagnostics[0].at, "b.atl:3")
        self.assertIn("a.atl:3", ex.diagnostics[0].hint)
        self.assertEqual((len(ex.model["tests"]), len(ex.model["tbds"])), (1, 0))

    def test_expect_states_are_checked_case_sensitively(self):
        base = self.structure("## cases: id | cat | title\nC-1 | normal | one\n## expect: case | nodes\n")
        self.assertEqual(rules_of(expand_text(base + "C-1 | n.a=completed\n")), ["ST-003"])
        self.assertEqual(rules_of(expand_text(base + "C-1 | n.a,n.a\n")), ["ST-007"])
        self.assertEqual(rules_of(expand_text(base + "C-1 | n.a=\n")), ["ST-007"])
        self.assertEqual(rules_of(expand_text(base + "C-1 | n.a\nC-1 | n.b\n")), ["ST-002"])

    def test_policy_keys_are_closed(self):
        base = HEAD + "## cases: id | cat | title | opts\n"
        self.assertEqual(rules_of(expand_text(base + "C-1 | normal | t | policy={\"timeout\":5}\n")), ["ST-004"])
        self.assertEqual(rules_of(expand_text(base + "C-1 | normal | t | policy={\"timeoutMs\":0}\n")), ["ST-003"])

    def test_stamps_fill_line_ranges(self):
        text = HEAD + "## evidences: id | file | anchor | topic\nE-1 | a.py | x | t\nE-2 | a.py | y | t\n"
        ex = expand_text(text, stamps={"E-1": [3, 9], "E-2": [9, 3], "E-X": [1, 1]})
        self.assertEqual([e["lineRange"] for e in ex.model["evidences"]], [[3, 9], None])
        self.assertEqual(rules_of(ex), ["ST-003"])

    def test_policy_and_tbd_rows(self):
        text = self.structure("## requirements: id | title | source\nRQ-1 | r | s\n## tbds: id | title | decision | opts\nTB-1 | t | d | ev=E-1\n"
                              "## policies: id | text | opts\nPL-1 | must hold | src=RQ-1\nPL-2 | nfr | src=TB-1 scope=kind:Module level=BD chapter=4 kind=nfr by=user\n")
        ex = expand_text(text)
        self.assertEqual(ex.diagnostics, [])
        p1, p2 = ex.model["policies"]
        self.assertEqual((p1["scope"], p1["level"], p1["chapter"], p1["kind"], p1["by"]), ("all", "REQ", None, "constraint", "ai"))
        self.assertEqual((p2["scope"], p2["level"], p2["chapter"], p2["kind"], p2["by"]), ("kind:Module", "BD", 4, "nfr", "user"))
        self.assertEqual(rules_of(expand_text(HEAD + "## policies: id | text | opts\nPL-1 | x\n")), ["ST-001"])

    def test_closed_world_reports_every_unresolved_reference_with_its_row(self):
        text = (HEAD + "## buildings: id | name | opts\nzone.a | Alpha\n## floors: id | building | name | level\nlvl.a | zone.ghost | Ground | 0\n"
                "## rooms: id | floor | name | opts\nrm.a | lvl.a | Hall\nrm.b | lvl.none | Lobby\n"
                "## nodes: id | name | sub | opts\n@set room=rm.gone kind=Module\nn.a | A | s | ev=E-9\nn.b | B | s\n"
                "## edges: from | rel | to | label | opts\nn.a | calls | n.ghost | go | ev=E-9\n"
                "## requirements: id | title | source | opts\nRQ-1 | r | s | nodes=n.a tests=TS-9\n"
                "## cases: id | cat | title | opts\nC-1 | normal | t | req=RQ-9\n"
                "## events: type | from | to | opts\n@set case=C-1\nCall | n.a | n.phantom\n"
                "## policies: id | text | opts\nPL-1 | x | src=RQ-8\n")
        slice_mode = expand_text(text)
        self.assertEqual(slice_mode.reference_diagnostics, [])
        self.assertEqual(slice_mode.diagnostics, [])
        full = expand_text(text, closed_world=True)
        rules = {d.rule for d in full.reference_diagnostics}
        self.assertEqual(rules, {"RF-001", "RF-002", "RF-003", "RF-004", "RF-005"})
        by_path = {d.path: d for d in full.reference_diagnostics}
        self.assertEqual(by_path["$.space.floors[0].buildingId"].at, "t.atl:5")
        self.assertEqual(by_path["$.space.floors[0].buildingId"].got, "zone.ghost")
        self.assertEqual(by_path["$.nodes[0].roomId"].at, "t.atl:11")
        self.assertEqual(by_path["$.edges[0].targetId"].at, "t.atl:14")
        self.assertEqual(by_path["$.cases[0].events[0].to"].at, "t.atl:21")
        self.assertTrue(all(d.severity == "ERROR" for d in full.reference_diagnostics))
        self.assertEqual(len(full.errors), len(full.all_diagnostics))
        self.assertEqual(full.model, slice_mode.model)

    def test_clean_models_have_no_reference_diagnostics_in_closed_world(self):
        for model in (synth.lending_desk("fixed"), synth.generate("small", 2), synth.edge_case("insert-element", 1)):
            ex = expand.expand_sources(encode.encode_atl(model), model["meta"], closed_world=True)
            self.assertEqual(ex.all_diagnostics, [])
        self.assertEqual(expand.reference_diagnostics(synth.lending_desk("fixed"), {}), [])

    def test_unplaceable_rows_are_warnings_in_slice_mode_and_errors_in_closed_world(self):
        text = HEAD + "## events: type | from | to | opts\nCall | a | b | case=ghost\n## transitions: from | to | opts\nA | B | machine=ghost\n"
        slice_mode = expand_text(text)
        self.assertEqual(sorted((d.rule, d.severity) for d in slice_mode.diagnostics), [("RF-004", "WARN"), ("SM-001", "WARN")])
        self.assertTrue(slice_mode.ok)
        full = expand_text(text, closed_world=True)
        self.assertEqual(sorted((d.rule, d.severity) for d in full.diagnostics), [("RF-004", "ERROR"), ("SM-001", "ERROR")])
        self.assertFalse(full.ok)

    def test_hostile_rows_never_crash_the_expander(self):
        for line in ("## buildings: id | name | opts\nA | B | trust=x\n## floors: id | building | name | level\nF | ghost | n | 3\n",
                     "## machines: id | initial | opts\nM | 1bad\n## transitions: from | to | opts\nA | B | machine=M\n",
                     "## interventions: id | type | description | opts\nI | insert_element | d | applies=C element={\"elementId\":5}\n",
                     "## interventions: id | type | description | opts\nI | insert_element | d | applies=C element={\"elementId\":\"e\",\"kind\":1}\n",
                     "## cases: id | cat | title | opts\nC | normal | t | init={\"m\":1} ctx=[]\n",
                     "## expect: case | nodes | opts\nC | a | outcomes=[1,{\"ruleId\":\"r\",\"outcome\":\"nope\"}]\n"):
            expand_text(HEAD + line)


class RoundTripTests(unittest.TestCase):
    """Acceptance (b): expand(parse(encode(m))) == m, in both formats."""

    def check(self, model: dict, label: str) -> None:
        self.assertEqual(am.contract_issues(model) if "unknown" not in label else [], [] if "unknown" not in label else [], label)
        for name, encoder in (("atl", encode.encode_atl), ("jsonl", encode.encode_jsonl)):
            files = encoder(model)
            for fname, text in files.items():
                self.assertLessEqual(len(text.encode("utf-8")), rw.MAX_SLICE_BYTES, (label, fname))
                self.assertNotIn("\r", text)
                self.assertFalse(text.startswith("\ufeff"))
                self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), (label, fname))
            ex = expand.expand_sources(files, encode.meta_of(model), stamps=encode.stamps_of(model))
            if "unknown" in label:
                self.assertEqual({d.rule for d in ex.diagnostics}, {"ST-003"}, label)
                self.assertEqual(len(ex.diagnostics), 7, label)
            else:
                self.assertEqual(ex.diagnostics, [], (label, name))
            self.assertEqual(ex.model, model, (label, name))
            self.assertEqual(am.canonical_bytes(ex.model), am.canonical_bytes(model), (label, name))

    def test_tiny_and_small_over_seeds(self):
        self.check(synth.generate("tiny", 0), "tiny")
        for seed in range(6):
            self.check(synth.generate("small", seed), f"small-{seed}")

    def test_reference_scale(self):
        for seed in (1, 17):
            self.check(synth.generate("reference", seed), f"reference-{seed}")

    def test_edge_case_presets(self):
        for name in synth.EDGE_CASES:
            if name == "empty-strings":
                continue
            for seed in (0, 7):
                self.check(synth.edge_case(name, seed), f"{name}-{seed}" + ("-unknown" if name == "unknown-values" else ""))

    def test_tobe_models_round_trip(self):
        self.check(synth.generate("small", 2, mode="tobe"), "tobe-small")
        self.check(synth.lending_desk("fixed", 0, "tobe"), "tobe-desk")

    def test_models_without_cases_use_every_record_class(self):
        model = synth.generate("reference", 3)
        classes = {section for section, _ in encode.model_rows(model)}
        self.assertEqual(classes, set(rw.SECTIONS))

    def test_empty_strings_cannot_be_written_and_say_so(self):
        model = synth.edge_case("empty-strings", 1)
        for encoder in (encode.encode_atl, encode.encode_jsonl):
            with self.assertRaises(encode.EncodeError) as cm:
                encoder(model)
            self.assertIn("never empty", str(cm.exception))

    def test_encoding_is_deterministic_and_independent_of_hash_seed(self):
        model = synth.generate("small", 4)
        self.assertEqual(encode.encode_atl(model), encode.encode_atl(json.loads(json.dumps(model))))
        self.assertEqual(encode.encode_jsonl(model), encode.encode_jsonl(json.loads(json.dumps(model))))

    def test_minimal_authoring_forms(self):
        text = "\n".join(t for _, t in sorted(encode.encode_atl(synth.generate("reference", 5)).items()))
        self.assertNotIn("portIds", text)
        self.assertIn("ev=^", text)
        self.assertIn("owner=", text)
        self.assertIn("alias=", text)
        self.assertIn("@set room=", text)
        self.assertNotIn("lineRange", text)

    def test_single_records_can_be_shown_in_either_format(self):
        model = synth.lending_desk("fixed")
        for section, values in encode.model_rows(model):
            line = encode.atl_record(section, values)
            parsed = atl.parse_atl("#atlas 1\n" + rw.SECTIONS[section].header + "\n" + line + "\n", "r.atl")
            self.assertEqual(parsed.diags, [], line)
            self.assertEqual(dict(parsed.rows[0].values), dict(values), line)
            again = jsonl.parse_jsonl("{\"_atlas\":1}\n" + encode.jsonl_record(section, values) + "\n", "r.jsonl")
            self.assertEqual(dict(again.rows[0].values), dict(values))

    def test_empty_model_round_trips(self):
        model = am.empty_model("p", "t", "asis", ("en-US",))
        for encoder in (encode.encode_atl, encode.encode_jsonl):
            ex = expand.expand_sources(encoder(model), model["meta"])
            self.assertEqual((ex.diagnostics, ex.model), ([], model))

    def test_split_files_keep_order_across_the_size_limit(self):
        model = synth.generate("small", 1)
        files = encode.encode_atl(model, max_bytes=3000)
        self.assertGreater(len(files), 6)
        self.assertLessEqual(max(len(t.encode()) for t in files.values()), 3400)
        self.assertEqual(sorted(files), list(files))
        ex = expand.expand_sources(files, model["meta"])
        self.assertEqual((ex.diagnostics, ex.model), ([], model))

    def test_awkward_text_survives_both_formats(self):
        model = synth.lending_desk("fixed")
        nasty = ["a | b", "a\\|b", "quote \" inside", "tilde", "~", "@set x=1", "// not a comment", "## not a header", "ev=EVD-1 looks like an option",
                 " padded ", "tab\there", "line\nbreak", "back\\slash", "\u2028sep", "\u0085nel", "-", "{\"j\":1}", "[1]"]
        for i, text in enumerate(nasty):
            m = json.loads(json.dumps(model))
            m["nodes"][0]["sub"] = text
            m["nodes"][0]["name"] = text
            m["tests"][0]["title"] = text
            m["evidences"][0]["anchor"] = text
            m["evidences"][0]["topic"] = text
            m["requirements"][0]["source"] = text
            m["cases"][0]["title"] = text
            m["cases"][0]["events"][0]["ruleId"] = text
            m["edges"][0]["label"] = text
            m["interventions"][0]["note"] = text
            m["interventions"][0]["why"] = text
            for encoder in (encode.encode_atl, encode.encode_jsonl):
                ex = expand.expand_sources(encoder(m), m["meta"])
                self.assertEqual(ex.diagnostics, [], (text, encoder.__name__))
                self.assertEqual(ex.model, m, (text, encoder.__name__))


class DeterminismTests(unittest.TestCase):
    def test_expansion_diagnostics_and_encoding_do_not_depend_on_the_hash_seed(self):
        import hashlib
        import os
        import subprocess
        import sys

        repo = Path(__file__).resolve().parents[2]
        code = (
            "import hashlib, json, sys\n"
            "from atlas import synth\n"
            "from gemarc_mcp.atlas import encode, expand, model as am\n"
            "m = synth.generate('small', 5)\n"
            "files = encode.encode_atl(m)\n"
            "name = sorted(files)[1]\n"
            "files[name] = files[name].replace(' | ', ' | | ', 7).replace('kind=', 'kynd=', 3)\n"
            "ex = expand.expand_sources(files, m['meta'])\n"
            "blob = json.dumps([[d.as_dict() for d in ex.diagnostics], ex.provenance, sorted(files.items())], sort_keys=True, default=str)\n"
            "sys.stdout.write(hashlib.sha256((blob + am.canonical_dumps(ex.model)).encode()).hexdigest() + ' ' + str(len(ex.diagnostics)))\n"
        )
        results = []
        for seed in ("1", "2", "31337"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join([str(repo / "src"), str(repo / "tests")]))
            done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=repo)
            self.assertEqual(done.returncode, 0, done.stderr)
            results.append(done.stdout)
        self.assertEqual(len(set(results)), 1, results)
        self.assertGreater(int(results[0].split()[1]), 0)
        self.assertEqual(len(hashlib.sha256(b"").hexdigest()), 64)


class SizeTests(unittest.TestCase):
    """Acceptance (d): the reference-scale encoding is about 166 KB (+-10%) in .atl; the .jsonl size is reported."""

    def test_reference_scale_size(self):
        sizes_atl, sizes_jsonl = [], []
        for seed in (1, 2, 3):
            model = synth.generate("reference", seed)
            sizes_atl.append(sum(len(t.encode("utf-8")) for t in encode.encode_atl(model).values()))
            sizes_jsonl.append(sum(len(t.encode("utf-8")) for t in encode.encode_jsonl(model).values()))
        mean_atl, mean_jsonl = sum(sizes_atl) / 3, sum(sizes_jsonl) / 3
        print(f"\n[format size] reference-scale .atl mean {mean_atl:,.0f} B ({min(sizes_atl):,}..{max(sizes_atl):,}); .jsonl mean {mean_jsonl:,.0f} B")
        self.assertTrue(166_000 * 0.9 <= mean_atl <= 166_000 * 1.1, mean_atl)
        for size in sizes_atl:
            self.assertTrue(166_000 * 0.9 <= size <= 166_000 * 1.1, size)
        self.assertGreater(mean_jsonl, mean_atl)

    def test_estimator_agrees_with_the_real_encoder(self):
        model = synth.generate("reference", 4)
        real = sum(len(t.encode("utf-8")) for t in encode.encode_atl(model).values())
        estimate = synth.estimate_atl(model)["total"]
        self.assertTrue(0.97 <= real / estimate <= 1.04, (real, estimate))


if __name__ == "__main__":
    unittest.main()
