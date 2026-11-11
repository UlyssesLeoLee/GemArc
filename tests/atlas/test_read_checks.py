"""RS-101 ... RS-133: one mutation fixture per check (design A3 6.4).

A clean build has no finding. Each I-class check has a mutation that edits ONE thing in a written tree (pages stay sealed and the manifest row
refreshed unless the mutation is about the seal itself) and the test asserts that exactly that rule id turns red among the I-class errors. L-class checks
assert that the build is still ``ok`` and that the degradation is recorded. T-class checks (RS-118, 119, 126, 130, 133) are not part of a build and have
their tests elsewhere (``test_i18n_read``, ``test_read_manifest``, lane I).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_checks
"""
from __future__ import annotations

import re
import unittest

from gemarc_mcp.atlas.reading import check as C
from gemarc_mcp.atlas.reading import envelope as E

from . import read_runs as RR
from . import read_support as RS

LANG = "zh-CN"
NEIGHBOURS = E.TEXT[LANG]["read.foot.neighbours"].split("{links}")[0]


def small() -> RS.Built:
    return RS.built("small")


def hub() -> RS.Built:
    return _hub()


_HUB: dict = {}


def _hub() -> RS.Built:
    if "b" not in _HUB:
        _HUB["b"] = RS.make(RS.hub_model(160, 0))
    return _HUB["b"]


def head_line(text: str, prefix_key: str) -> str:
    prefix = E.TEXT[LANG][prefix_key].split("{text}")[0]
    return next(line for line in text.split("\n") if line.startswith(prefix))


def first_table_heading(text: str, label: str):
    match = re.search(r"^### " + re.escape(label) + r"（(\d+)）$", text, re.M)
    return match


# ---------------------------------------------------------------------------------------------------- one mutation per rule

def mut_101(b, s):
    marker = "\n\n" + NEIGHBOURS
    return RS.edit_page(s, LANG, "read/2D/S01.md", lambda t: t.replace(marker, "\n" + ("x" * 80 + "\n") * 150 + marker, 1))


def mut_102(b, s):
    marker = "\n\n" + NEIGHBOURS
    return RS.edit_page(s, LANG, "read/2D/S01.md", lambda t: t.replace(marker, "\n" + "y" * 600 + marker, 1))


def mut_103(b, s):
    def widen(text):
        line = head_line(text, "read.card.howto")
        return text.replace(line, line + "。" * 140, 1)

    return RS.edit_page(s, LANG, "read/2D/S01.md", widen)


def mut_104(b, s):
    def alter(text):
        line = head_line(text, "read.card.howto")
        return text.replace(line, line + "。", 1)

    return RS.edit_page(s, LANG, "read/2D/S02.md", alter, seal=False)


def mut_105(b, s):
    stamp = b.src.stamp
    return RS.edit_page(s, LANG, "read/2D/S02.md", lambda t: t.replace("m=" + stamp, "m=00000000", 1))


def mut_106(b, s):
    return RS.edit_page(s, LANG, "read/2D/S01.md", lambda t: t.replace("[索引](INDEX.md)", "[索引](INDEXX.md)", 1))


def mut_107(b, s):
    def bump(text):
        m = first_table_heading(text, "出向连接")
        n = int(m.group(1))
        text = text.replace(m.group(0), f"### 出向连接（{n + 1}）", 1)
        return text.replace(f"出向连接 {n}", f"出向连接 {n + 1}", 1)

    return RS.edit_page(s, LANG, "read/2D/S01.md", bump)


def mut_109(b, s):
    out = RS.copy_surface(s)
    rec = next(r for r in out.records if r["p"] == "read/2D/S03.md")
    rec["b"] += 1
    return out


def mut_111(b, s):
    return RS.edit_page(s, LANG, "read/2D/S05.md", lambda t: t.replace("的第 2/3 部分", "的第 3/3 部分", 1))


def mut_113(b, s):
    return RS.edit_page(s, LANG, "read/QA/Q01.md", lambda t: t.replace('- gate: {"blockers":5,"release":false}', '- gate: {"blockers":9,"release":false}', 1))


def mut_116(b, s):
    def shift(text):
        out = []
        done = False
        for line in text.split("\n"):
            cols = line.split("\t")
            if not done and len(cols) == 7 and cols[1] == "node" and cols[2] == "home":
                cols[4] = str(int(cols[4]) + 1)
                line = "\t".join(cols)
                done = True
            out.append(line)
        return "\n".join(out)

    return RS.edit_file(s, "ids.tsv", shift)


def mut_117(b, s):
    out = RS.copy_surface(s)
    out.extra_paths.add("read/2D/a b.md")
    return out


def mut_120(b, s):
    def bump(text):
        m = re.search(r"入向连接: (\d+)（", text)
        return text.replace(m.group(0), f"入向连接: {int(m.group(1)) + 1}（", 1)

    return RS.edit_page(s, LANG, "read/2D/S01.md", bump)


def mut_121(b, s):
    out = RS.copy_surface(s)
    out.extra_paths.add("notes/long.md")
    out.records.append({"p": "notes/long.md", "k": "data", "cls": "data", "ai": False, "b": 300000, "l": 9000, "tok": 0, "sha8": "00000000", "twin": None})
    return out


def mut_122(b, s):
    def bump(text):
        m = re.search(r"节点 (\d+)", head_line(text, "read.card.covers"))
        return text.replace(head_line(text, "read.card.covers"), head_line(text, "read.card.covers").replace(m.group(0), f"节点 {int(m.group(1)) + 1}", 1), 1)

    return RS.edit_page(s, LANG, "read/2D/S01.md", bump)


def mut_123(b, s):
    def strip(text):
        line = head_line(text, "read.card.howto")
        return text.replace(line, "> 读法: 按页内标题读", 1)

    path = next(p for p in sorted(s.files) if p.startswith("read/2D/S") and "[N/A]" in s.text(p))
    return RS.edit_page(s, LANG, path, strip)


def mut_124(b, s):
    ix = b.src.ix
    cited = {h.key for h in b.src.by_group("ev")}
    eid = sorted(set(ix.evidence) - cited)[0]
    unstamped = "（未盖章）"
    path = next(p for p in sorted(s.files) if p.startswith("read/1D/E") and "`" + eid + "`" in s.text(p))

    def alter(text):
        out = []
        for line in text.split("\n"):
            if line.startswith("| `" + eid + "` |"):
                line = line.replace(unstamped, "1-2", 1)
            out.append(line)
        return "\n".join(out)

    return RS.edit_page(s, LANG, path, alter)


def mut_125(b, s):
    out = RS.copy_surface(s)
    del out.files["docs/README.md"]
    out.records = [r for r in out.records if r["p"] != "docs/README.md"]
    out.extra_paths.add("docs/zh-CN/DD.md")
    return out


def mut_128(b, s):
    """On the tiny build (one page ``A01``): the explicit empty state of the constraints is replaced by a line of no meaning (same line count)."""
    empty = b.src.groups["con"]["empty"]
    return RS.edit_page(s, LANG, "read/A01.md", lambda t: t.replace(empty + "\n", "nothing\n", 1))


def mut_129(b, s):
    return RS.edit_page(s, LANG, "START.md", lambda t: re.sub(r"(\| 2D 结构 \| )(\d+)( \|)", lambda m: m.group(1) + str(int(m.group(2)) + 1) + m.group(3), t, count=1))


def mut_131(b, s):
    def drop(text):
        lines = text.split("\n")
        at = next(n for n, line in enumerate(lines) if n > 1 and "\tev\t" in line)
        del lines[at]
        return "\n".join(lines)

    return RS.edit_file(s, "refs.tsv", drop)


def mut_110(b, s):
    """A script in a twin (the twin side only; its manifest row is refreshed so that RS-109 stays quiet)."""
    return RS.edit_file(s, "site/read/2D/S01.html", lambda t: t.replace("</body>", "<script>1</script></body>", 1))


def mut_114(b, s):
    """One visible character more in a twin heading: the twin no longer says what the page says."""
    return RS.edit_file(s, "site/read/2D/S01.html", lambda t: t.replace("<h2>", "<h2>x", 1))


def mut_108(b, s):
    """A verdict line says one node more differs than the verdict data (sealed_meta) stored."""
    path = next(p for p in sorted(s.files) if p.startswith("read/4D/C") and "规则断言" in s.text(p))

    def bump(text):
        m = re.search(r"不符 (\d+) · 规则断言", text)
        return text.replace(m.group(0), f"不符 {int(m.group(1)) + 1} · 规则断言", 1)

    return RS.edit_page(s, LANG, path, bump)


def mut_115(b, s):
    """A result block of a sealed case says the ledger word of a confirmed one."""
    path = next(p for p in sorted(s.files) if p.startswith("read/4D/C") and "账本: 已封存" in s.text(p))
    return RS.edit_page(s, LANG, path, lambda t: t.replace("账本: 已封存", "账本: 已确认", 1))


def mut_127(b, s):
    """A paired sequence diagram on disk that lost its intervention line (the file, not the unit)."""
    path = next(p for p in sorted(s.files) if p.startswith("diagrams/sequence-") and "%% intervention: " in s.text(p))
    return RS.edit_file(s, path, lambda t: "\n".join(line for line in t.split("\n") if not line.startswith("%% intervention: ")))


BASES = {"RS-128": "tiny"}
#: the rules whose fixtures need a build that has result blocks (read_runs: every item that can be sealed is sealed)
SEALED_RULES = frozenset({"RS-108", "RS-115", "RS-127"})
#: the rules that read a file of a written dist (the diagram headers are written by docbuild and the build, not by reading.build)
DIST_RULES = frozenset({"RS-127"})


def built_for(rule: str) -> RS.Built:
    return RR.sealed("small") if rule in SEALED_RULES else RS.built(BASES.get(rule, "small"), html=rule in HTML_RULES)


def surface_for(rule: str, b: RS.Built):
    if rule in DIST_RULES:
        with RS.TempDist(b, real_docs=True) as d:
            return d.surface
    return b.surface()
#: the rules whose fixtures need a build with HTML twins (``html=True``); every other build here has none
HTML_RULES = frozenset({"RS-110", "RS-114"})


MUTATIONS = {
    "RS-101": mut_101, "RS-102": mut_102, "RS-103": mut_103, "RS-104": mut_104, "RS-105": mut_105, "RS-106": mut_106, "RS-107": mut_107,
    "RS-109": mut_109, "RS-113": mut_113, "RS-116": mut_116, "RS-117": mut_117, "RS-120": mut_120, "RS-121": mut_121, "RS-122": mut_122,
    "RS-123": mut_123, "RS-124": mut_124, "RS-125": mut_125, "RS-128": mut_128, "RS-129": mut_129, "RS-131": mut_131,
    "RS-110": mut_110, "RS-114": mut_114, "RS-108": mut_108, "RS-115": mut_115, "RS-127": mut_127,
}


class CleanTests(unittest.TestCase):
    def test_a_clean_build_has_no_integrity_finding(self):
        for preset in ("tiny", "small"):
            b = RS.built(preset)
            self.assertEqual(RS.rules_of(b.result.findings), set(), preset)
            self.assertEqual(b.result.status, "ok")
            self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set(), preset)
            sealed = RR.sealed(preset)       # the same models with every sealable item sealed: results, verdicts, headers
            self.assertEqual(RS.rules_of(sealed.result.findings), set(), preset)
            self.assertEqual(RS.rules_of(RS.check(sealed, surface_for("RS-127", sealed))), set(), preset)

    def test_every_active_integrity_check_has_a_mutation(self):
        have = set(MUTATIONS) | {"RS-111"}
        self.assertEqual(set(C.ACTIVE_I) - have, set(), "an active I-class check without a mutation fixture")

    def test_the_registry_is_complete_and_the_deferred_ones_say_why(self):
        self.assertEqual(sorted(C.CHECKS), [f"RS-{n}" for n in range(101, 134)])
        for rule, milestone in C.NOT_APPLICABLE.items():
            self.assertIn(milestone, ("R2", "R3"))
            self.assertEqual(C.CHECKS[rule][0], "I")
        self.assertEqual({r for r, (k, _t) in C.CHECKS.items() if k == "T"}, {"RS-118", "RS-119", "RS-126", "RS-130", "RS-133"})
        self.assertEqual({r for r, (k, _t) in C.CHECKS.items() if k == "L"}, {"RS-112", "RS-132"})


class MutationTests(unittest.TestCase):
    """Each mutation turns exactly its own rule red (and no other I-class rule)."""

    def test_each_mutation_fires_exactly_its_rule(self):
        for rule, mutate in sorted(MUTATIONS.items()):
            b = built_for(rule)
            base = surface_for(rule, b)
            with self.subTest(rule=rule):
                findings = RS.check(b, mutate(b, base))
                self.assertEqual(RS.rules_of(findings), {rule}, [f"{f.rule} {f.path} {f.message}" for f in findings if f.klass == "I"][:4])

    def test_rs111_continuation_numbering(self):
        b = hub()
        self.assertEqual(RS.rules_of(RS.check(b, b.surface())), set())
        findings = RS.check(b, mut_111(b, b.surface()))
        self.assertEqual(RS.rules_of(findings), {"RS-111"}, [f"{f.rule} {f.message}" for f in findings][:4])

    def test_rs111_rows_that_do_not_continue_are_found(self):
        b = hub()
        path = "read/2D/S05.md"
        out = RS.edit_page(b.surface(), LANG, path, lambda t: re.sub(r"第 (\d+)-(\d+) 行", lambda m: f"第 {int(m.group(1)) + 1}-{int(m.group(2)) + 1} 行", t, count=1))
        self.assertEqual(RS.rules_of(RS.check(b, out)), {"RS-111"})

    def test_the_mutations_are_what_they_say(self):
        for rule, mutate in MUTATIONS.items():
            b = built_for(rule)
            s = surface_for(rule, b)
            changed = mutate(b, s)
            same = (changed.files == s.files and changed.records == s.records and changed.extra_paths == s.extra_paths)
            self.assertFalse(same, rule)


class DegradationTests(unittest.TestCase):
    """L-class: the build is ok and the degradation is recorded."""

    def test_small_pages_are_a_warning_not_a_failure(self):
        b = RS.built("small", seed=4)
        warn = [f for f in b.result.findings if f.rule == "RS-112"]
        self.assertTrue(warn)
        self.assertEqual({(f.klass, f.severity) for f in warn}, {("L", "WARN")})
        self.assertEqual(b.result.status, "ok")
        self.assertIn("RS-112", b.result.degraded)

    def test_a_folded_line_is_recorded_and_the_build_is_ok(self):
        b = RS.built("reference")
        rules = {f.rule for f in b.result.findings if f.klass == "L"}
        self.assertIn("RS-132", rules)
        self.assertEqual(b.result.status, "ok")

    def test_an_overlong_page_is_an_error_but_only_for_the_planner(self):
        """RS-101 for a page the planner made is I-class; the same size in START (not planned) is a degradation."""
        b = small()
        out = RS.edit_page(b.surface(), LANG, "START.md", lambda t: t.replace("\n\n页尾", "\n" + ("z" * 90 + "\n") * 80 + "\n页尾", 1) if False else t.replace("\n页尾", "\n" + ("z" * 90 + "\n") * 80 + "页尾", 1))
        findings = RS.check(b, out)
        starts = [f for f in findings if f.rule == "RS-101" and f.path == "START.md"]
        self.assertTrue(starts)
        self.assertEqual({(f.klass, f.severity) for f in starts}, {("L", "WARN")})
        self.assertEqual(RS.rules_of(findings), set())
        self.assertIn("RS-101", {f.rule for f in findings if f.klass == "L"})


class ReadmeTests(unittest.TestCase):
    def test_not_applicable_checks_are_listed_not_skipped_silently(self):
        for rule in C.NOT_APPLICABLE:
            self.assertNotIn(rule, [r for r, _f in C.ACTIVE])


if __name__ == "__main__":
    unittest.main()
