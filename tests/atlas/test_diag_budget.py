"""Diagnostics rendering and budgets (design 5.2, M2 (b)).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_diag_budget.py"

<= 12 groups, <= 2 examples per group, ``got`` cut at 80 characters with control characters stripped, hints <= 140 characters,
default diagnostics <= 2,800 B (trailer included), a 200-defect mutant <= 3,000 B, a 5,000-defect input <= 24 KiB,
text and json, ``+N more (offset=K)``, derived diagnostics counted and never listed. Synthetic ids only.
"""
from __future__ import annotations

import json
import re
import time
import unittest

from gemarc_mcp.atlas import diag as dg
from gemarc_mcp.atlas import slicecheck

from .test_rules import META, M, desk

# One defect class per index: (section, row). Every class hits a different rule, so a mutant has more than 12 groups.
CLASSES = (
    ("nodes", "gen.a{i} | Gen | Text. | room=rm.desk kind=Modul"),                                   # ST-003
    ("nodes", "svc.loan | Twin {i} | Text. | room=rm.desk kind=Module"),                               # ST-002
    ("nodes", "gen.b{i} | Gen | Text. | room=rm.desk kind=Module foo=1"),                              # ST-004
    ("nodes", "gen.c{i} | Only two"),                                                                  # ST-005
    ("nodes", "gen.d{i} | Gen | Text ev=EVD-1 | room=rm.desk kind=Module"),                            # ST-006
    ("nodes", "gen.e{i} | Gen | Text. | room=rm.desk kind=Module props={{bad"),                         # ST-007
    ("edges", "svc.loan | calls | gen.nowhere{i} | go | ev=EVD-1"),                                    # RF-002
    ("nodes", "gen.g{i} | Gen | Text. | room=rm.desk kind=Module ev=XVD-{i}"),                          # RF-003
    ("events", "Call | usr.member | svc.loan | case=CASE-NO-{i}"),                                     # RF-004
    ("requirements", "RQ-G{i} | Gen | source | nodes=gen.missing{i}"),                                 # RF-005
    ("nodes", "gen.k{i} | Gen | Text. | room=rm.desk kind=Module ev=EVD-3 verif=Implemented"),         # EV-001
    ("nodes", "gen.m{i} | Gen | Text. | room=rm.desk kind=Module"),                                    # EV-009
    ("evidences", "GEV-{i} | src/x{i}.py | def f( | unused"),                                          # EV-008
    ("nodes", "gen.n{i} | Gen | Text. | room=rm.desk kind=Module portIds=[]"),                         # DV-001
    ("nodes", "gen.p{i} | Gen | Text. | room=rm.nowhere{i} kind=Module ev=EVD-1"),                     # RF-001
    ("cases", "CS-G{i} | normal | Generated case"),                                                    # CS-004
)
HEADERS = {
    "nodes": "## nodes: id | name | sub | opts", "edges": "## edges: from | rel | to | label | opts",
    "evidences": "## evidences: id | file | anchor | topic | opts", "events": "## events: type | from | to | opts",
    "requirements": "## requirements: id | title | source | opts", "cases": "## cases: id | cat | title | opts",
}
SECTION_ORDER = ("nodes", "edges", "evidences", "cases", "events", "requirements")
FILE_LIMIT = 56 * 1024


def mutant(defects: int) -> dict:
    """The lending desk plus ``defects`` generated defect rows spread over files of at most 56 KiB (the slice limit is 64)."""
    files = desk()
    chunks: list[dict] = [{}]
    size = 0
    for i in range(defects):
        section, row = CLASSES[i % len(CLASSES)]
        text = row.format(i=i)
        size += len(text) + 1
        if size > FILE_LIMIT:
            chunks.append({})
            size = len(text) + 1
        chunks[-1].setdefault(section, []).append(text)
    for n, chunk in enumerate(chunks):
        lines = ["#atlas 1"]
        for section in SECTION_ORDER:
            if section in chunk:
                lines.append(HEADERS[section])
                if section == "events":
                    lines.append("@set case=CS-BORROW-OK")
                lines.extend(chunk[section])
        files[f"50-gen-{n:03d}.atl"] = "\n".join(lines) + "\n"
    return files


def groups_of(text: str) -> dict:
    """``{rule: example lines}`` of a text view (lines that start with a severity letter and a rule id)."""
    out: dict[str, list] = {}
    for line in text.splitlines():
        m = re.match(r"^([EWI]) ([A-Z]{2}-\d{3}) ", line)
        if m and not line.startswith("I pending"):
            out.setdefault((m.group(1), m.group(2)), []).append(line)
    return out


class RenderingRuleTests(unittest.TestCase):
    def diag(self, **kw):
        base = dict(rule="ST-003", path="nodes.kind", at="f.atl:3", got="x", expected="a kind", hint="fix it")
        base.update(kw)
        return dg.diag(base.pop("rule"), base.pop("path"), base.pop("at"), base.pop("got"), base.pop("expected"), base.pop("hint"), **base)

    def test_got_is_cut_at_80_characters_and_control_characters_are_stripped(self):
        d = self.diag(got="ok\x1b[31m\x00\n\r\t" + "y" * 300)
        line = dg.render_line(d)
        got = re.search(r" got=(.*?) want=", line).group(1)
        self.assertLessEqual(len(got), 80)
        self.assertTrue(got.endswith("..."))
        for ch in "\x1b\x00\n\r\t":
            self.assertNotIn(ch, line)
        record = json.loads(dg.render([d], fmt="json"))["diagnostics"][0]
        self.assertLessEqual(len(record["got"]), 80)
        self.assertNotIn("\x1b", record["got"])

    def test_non_text_got_is_rendered_as_json_within_the_limit(self):
        d = self.diag(got={"k": ["v"] * 100})
        self.assertLessEqual(len(re.search(r" got=(.*?) want=", dg.render_line(d)).group(1)), 80)

    def test_hint_is_cut_at_140_characters(self):
        d = self.diag(hint="h" * 500)
        self.assertLessEqual(len(dg.render_line(d).split(" | ", 1)[1]), 140)
        self.assertLessEqual(len(json.loads(dg.render([d], fmt="json"))["diagnostics"][0]["hint"]), 140)

    def test_every_registered_hint_with_its_values_stays_within_140_characters(self):
        # the worst realistic fill: a 128-character id in a placeholder
        for rule_id in ("RF-001", "RF-002", "ST-009", "SZ-001", "EV-007", "EV-011"):
            from gemarc_mcp.atlas.rules import registry
            text = registry.hint_of(rule_id, detail="x" * 60, slice="s" * 40, size=99999, repo="r" * 40, count=99999)
            self.assertLessEqual(len(dg.clip(text, dg.HINT_LIMIT)), 140)

    def test_two_examples_per_group_and_twelve_groups_even_for_a_small_input(self):
        five = [self.diag(rule="ST-003", at=f"f.atl:{i}") for i in range(1, 6)]
        lines = dg.render(five).splitlines()
        self.assertEqual([l.split()[1] for l in lines[:2]], ["ST-003", "ST-003"])
        self.assertEqual(lines[2], "+3 more (offset=2)")
        twenty = [self.diag(rule=f"ST-{i:03d}", at=f"f.atl:{i}") for i in range(1, 21)]
        text = dg.render(twenty)
        self.assertEqual(len([l for l in text.splitlines() if l.startswith("E ST-")]), 12)
        self.assertIn("+8 more (offset=12)", text)
        # the continuation starts exactly at the first hidden diagnostic
        self.assertEqual(dg.render(twenty, offset=12).splitlines()[0].split()[1], "ST-013")

    def test_derived_diagnostics_are_counted_but_never_listed(self):
        shown = self.diag(rule="ST-001", at="f.atl:1")
        hidden = self.diag(rule="EV-001", at="f.atl:2", derived=True)
        text = dg.render([shown, hidden])
        self.assertNotIn("EV-001", text)
        self.assertIn("derived (suppressed): 1", text)
        self.assertEqual(dg.counts([shown, hidden])["derived"], 1)

    def test_errors_come_first_then_rule_then_location(self):
        a = self.diag(rule="EV-009", severity=dg.WARN, at="b.atl:1")
        b = self.diag(rule="ST-003", at="a.atl:9")
        c = self.diag(rule="ST-001", at="a.atl:2")
        lines = dg.render([a, b, c]).splitlines()
        self.assertEqual([l.split()[1] for l in lines[:3]], ["ST-001", "ST-003", "EV-009"])

    def test_a_clean_result_renders_as_an_empty_string(self):
        self.assertEqual(dg.render([]), "")

    def test_bad_format_is_refused(self):
        with self.assertRaises(ValueError):
            dg.render([], fmt="yaml")

    def test_rendering_is_deterministic(self):
        diags = [self.diag(rule=f"ST-00{i % 7 + 1}", at=f"f.atl:{i}") for i in range(40)]
        self.assertEqual(dg.render(diags), dg.render(list(reversed(diags))))


class SmallInputTests(unittest.TestCase):
    def test_two_defects_print_at_most_three_lines_and_one_k_tokens(self):
        for edits in ([(M, "svc.loan | calls | svc.stock |", "svc.loan | calls | svc.stok |"), (M, "borrow a copy | ev=EVD-3", "borrow a copy | ev=EVD-9")],
                      [(M, "ev=EVD-3 verif=Inferred", "ev=EVD-39 verif=Implemented"), (M, "svc.loan | writes | tbl.loan |", "svc.loan | writes | tbl.loam |")]):
            text = slicecheck.check(desk(edits=edits), META, mode="full").render()
            self.assertLessEqual(len(text.splitlines()), 3, text)
            self.assertLessEqual(len(text.encode()) / 3, 1000, text)
            print(f"\n[2-defect fixture] {len(text.splitlines())} lines, {len(text.encode())} B = {len(text.encode()) / 3:.0f} T3")


class MutantTests(unittest.TestCase):
    def test_the_200_defect_mutant_renders_within_3000_bytes(self):
        result = slicecheck.check(mutant(200), META, mode="full")
        shown = result.shown
        kinds = {(d.severity, d.rule) for d in shown}
        self.assertGreater(len(kinds), 12, "the mutant must have more groups than a view may list")
        self.assertGreaterEqual(len(shown), 200)
        for fmt in ("text",):
            text = result.render(fmt)
            self.assertLessEqual(len(text.encode()), 3000, len(text.encode()))
        text = result.render()
        print(f"\n[200-defect mutant] {len(shown)} findings in {len(kinds)} groups -> {len(text.encode())} B, {len(text.splitlines())} lines")
        groups = groups_of(text)
        self.assertLessEqual(len(groups), 12)
        for key, lines in groups.items():
            self.assertLessEqual(len(lines), 2, key)
        self.assertRegex(text, r"\+\d+ more \(offset=\d+\)")

    def test_default_view_stays_inside_its_budget_at_every_size(self):
        for n in (20, 60, 200, 800):
            text = slicecheck.check(mutant(n), META, mode="full").render()
            self.assertLessEqual(len(text.encode()), dg.TEXT_BUDGET, n)

    def test_slice_mode_of_the_same_mutant_also_fits(self):
        text = slicecheck.check(mutant(200), META, mode="slice").render()
        self.assertLessEqual(len(text.encode()), 3000)

    def test_json_view_has_the_same_caps(self):
        result = slicecheck.check(mutant(200), META, mode="full")
        text = result.render("json")
        data = json.loads(text)
        self.assertLessEqual(len(text.encode()), dg.JSON_BUDGET)
        per_group: dict = {}
        for record in data["diagnostics"]:
            self.assertEqual(sorted(record), sorted(dg.FIELDS))
            per_group[(record["severity"], record["rule"])] = per_group.get((record["severity"], record["rule"]), 0) + 1
        self.assertLessEqual(len(per_group), 12)
        self.assertLessEqual(max(per_group.values()), 2)
        self.assertGreater(data["more"], 0)
        self.assertIn("next_offset", data)
        self.assertGreaterEqual(data["counts"]["derived"], 0)

    def test_offset_walks_the_rest_without_losing_a_diagnostic(self):
        result = slicecheck.check(mutant(200), META, mode="full")
        total = len(result.shown)
        first = dg.render(result.diagnostics)
        m = re.search(r"\+(\d+) more \(offset=(\d+)\)", first)
        self.assertIsNotNone(m)
        offset, listed = int(m.group(2)), len(groups_of(first)) and sum(len(v) for v in groups_of(first).values())
        seen_offsets, covered = set(), set()
        for d in result.shown[offset:]:
            covered.add((d.rule, d.at, d.path))
        guard = 0
        while offset < total and guard < 200:
            guard += 1
            self.assertNotIn(offset, seen_offsets)
            seen_offsets.add(offset)
            page = dg.render(result.diagnostics, offset=offset)
            self.assertLessEqual(len(page.encode()), dg.TEXT_BUDGET)
            n = sum(len(v) for v in groups_of(page).values())
            self.assertGreater(n, 0)
            nxt = re.search(r"\+\d+ more \(offset=(\d+)\)", page)
            if not nxt:
                offset = total
                break
            self.assertGreater(int(nxt.group(1)), offset)
            offset = int(nxt.group(1))
        self.assertGreaterEqual(offset, total)
        self.assertGreater(listed, 0)

    def test_the_5000_defect_input_returns_at_most_24_kib(self):
        started = time.perf_counter()
        result = slicecheck.check(mutant(5000), META, mode="full")
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(len(result.shown), 5000)
        default = result.render()
        self.assertLessEqual(len(default.encode()), dg.TEXT_BUDGET)
        widest = result.render("text", budget=10 ** 9)
        wide_json = result.render("json", budget=10 ** 9)
        self.assertLessEqual(len(widest.encode()), 24 * 1024)
        self.assertLessEqual(len(wide_json.encode()), 24 * 1024)
        json.loads(wide_json)
        flat = result.render("text", offset=2, budget=10 ** 9)
        flat_json = result.render("json", offset=2, budget=10 ** 9)
        self.assertLessEqual(len(flat.encode()), 24 * 1024)
        self.assertLessEqual(len(flat_json.encode()), 24 * 1024)
        self.assertGreater(len(flat.encode()), 20 * 1024)  # a flat page really uses the room it was given
        self.assertEqual(json.loads(flat_json)["next_offset"] > 2, True)
        print(f"\n[5,000-defect input] {len(result.shown)} findings in {elapsed:.2f} s; default {len(default.encode())} B, "
              f"widest text {len(widest.encode())} B, widest json {len(wide_json.encode())} B")
        self.assertLess(elapsed, 30)


if __name__ == "__main__":
    unittest.main()
