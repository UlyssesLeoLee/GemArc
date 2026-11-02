"""ORACLE_FIRST on the reading surface (milestone R3, design 2.10, AM-08, AM-20, RS-115, T-R7).

Two parts. The first part works on bundles and ``sealed_meta`` that are made by hand (cheater fixtures): a result of an item the ledger does not call
sealed or confirmed is in ``bundle.runs`` anyway, the ledger says something else, the oracle reply is missing or half there; the pages must print
no result for it, RS-115 must report the input, and no file of the surface (pages, ``ids.tsv``, ``refs.tsv``, the manifest) may carry a value of
it. The second part is the scripted cheater of T-R7 on a REAL workspace (``OracleProject``, the real ledger and ``sealed_runs``): the cheater reads
the result block of a sealed case from the page text and writes it into the expectation of another case through ``put``; it may not run the engine.
The question is what the ledger calls the other case afterwards, and what the next build prints for it. The LLM arm of T-R7 is run later by the lead.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_oracle
"""
from __future__ import annotations

import copy
import re
import unittest

from gemarc_mcp.atlas import build as BLD
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.reading import parse as PA

from . import read_runs as RR
from . import read_support as RS
from .test_oracle_cheaters import EVENTS, Cheater, patch_expect

LANG = "zh-CN"


def pages_text(b: RS.Built) -> dict:
    return {path: data.decode("utf-8") for path, data in b.result.files.items()}


def result_rows_of(b: RS.Built, cid: str) -> list:
    """The cells of the side-by-side rows of a case, read back from the written pages."""
    out = []
    for path, text in pages_text(b).items():
        if path.startswith("read/") and path.endswith(".md"):
            for line in text.split("\n"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if line.startswith("| `" + cid + "` |") and len(cells) == 4:         # the side-by-side table has four columns, the withheld table three
                    out.append(cells)
    return out


def trace_value_lines(b: RS.Built, cid: str, run: dict) -> list:
    """Strings that only a printed result of the case ``cid`` could contain: its head line, a side-by-side row for each node of its final state."""
    head = "- `" + cid + "` · 账本"
    rows = [f"| `{cid}` | `{node}` |" for node in (run.get("finalState") or {})]
    return [head] + rows


class Fixtures:
    """One model, three views of it: the honest reply (two items sealed), the leaky reply (everything sealed) and their metas."""

    def __init__(self, preset: str = "small", seed: int = 1):
        self.model = RS.model_of(preset, seed)
        cases, ivs = RR.eligible(self.model)
        self.cases, self.ivs = cases, ivs
        self.sealed_cases = cases[:2]
        self.sealed_ivs = [i for i in ivs if set(next(x for x in self.model["interventions"] if x["interventionId"] == i)["appliesTo"]) & set(self.sealed_cases)][:2]
        self.honest = RR.reply_of(self.model, LANG, self.sealed_cases, self.sealed_ivs)
        self.leaky = RR.reply_of(self.model, LANG)                      # the same model with EVERYTHING sealed: what the cheater must never get
        self.honest_meta = RR.meta_of(self.honest, self.model)
        self.unsealed_case = next(c for c in cases if c not in self.sealed_cases)
        self.unsealed_iv = next(i for i in ivs if i not in self.sealed_ivs)

    def build(self, reply=None, meta=None, **kw) -> RS.Built:
        return RR.make(self.model, LANG, reply=reply if reply is not None else self.honest, meta=meta if meta is not None else self.honest_meta, **kw)


_FIX: dict = {}


def fixtures() -> Fixtures:
    if "f" not in _FIX:
        _FIX["f"] = Fixtures()
    return _FIX["f"]


class HonestTests(unittest.TestCase):
    def test_only_the_sealed_items_have_a_result_and_everything_else_is_named_as_withheld(self):
        f = fixtures()
        b = f.build()
        self.assertEqual(b.result.status, "ok", [x.message for x in b.result.findings if x.klass == "I"][:3])
        self.assertEqual(set(b.src.shown_cases), set(f.sealed_cases))
        for cid in f.cases:
            self.assertEqual(bool(result_rows_of(b, cid)), cid in f.sealed_cases, cid)
        q = b.text("read/QA/Q01.md") if "read/QA/Q01.md" in b.result.files else b.text("read/P3Q.md")
        for cid in f.cases:
            self.assertEqual("`" + cid + "` | 用例 |" in q, cid not in f.sealed_cases, cid)
        for iid in f.ivs:
            self.assertEqual("`" + iid + "` | 干预 |" in q, iid not in {i for i, _c in b.src.shown_pairs}, iid)

    def test_the_quality_page_counts_the_items_and_prints_no_value_of_a_withheld_one(self):
        f = fixtures()
        b = f.build()
        q = b.text("read/QA/Q01.md") if "read/QA/Q01.md" in b.result.files else b.text("read/P3Q.md")
        self.assertIn(f"- 用例: 已封存 {len(f.sealed_cases)} · 已确认 0 · 被扣下 {len(f.cases) - len(f.sealed_cases)} · 共 {len(f.cases)}", q)
        for line in q.split("\n"):
            if line.startswith("| `") and "` | 用例 |" in line:
                self.assertEqual(len(line.split("|")) - 2, 3, "id, kind, the fixed phrase: nothing else")
        self.assertNotIn("引擎终态", q)
        self.assertNotIn("判定:", q)


class CheaterFixtureTests(unittest.TestCase):
    """A result of an item that is not sealed is in ``bundle.runs`` anyway: nothing of it may reach a page (and the input is reported)."""

    def assert_no_value_of(self, b: RS.Built, f: Fixtures, cid: str) -> None:
        run = f.leaky["baselines"][cid]["run"]
        for needle in trace_value_lines(b, cid, run):
            for path, text in pages_text(b).items():
                self.assertNotIn(needle, text, f"{path} prints a result of the case {cid}")
        for path in ("ids.tsv", "refs.tsv"):
            for line in b.text(path).split("\n"):
                self.assertNotIn("账本", line)
                self.assertNotIn("引擎", line)
        for record in b.result.records:
            for key in ("sum",):
                self.assertNotIn("引擎终态", str(record.get(key, "")))
        self.assertEqual(result_rows_of(b, cid), [], cid)

    def test_a_result_in_the_runs_that_the_ledger_withholds_is_not_printed_and_the_input_is_refused(self):
        f = fixtures()
        cid = f.unsealed_case
        b = RR.make(f.model, LANG, reply=f.leaky, meta=f.honest_meta)             # the bundle has everything, the ledger knows two items
        self.assertEqual(b.result.status, "invalid")
        self.assertIn(cid, b.src.stray_cases)
        self.assertEqual({x.rule for x in b.result.errors}, {"RS-115"})
        self.assertTrue(any(cid in x.message for x in b.result.errors))
        self.assert_no_value_of(b, f, cid)
        self.assertIn(cid, b.text("read/QA/Q01.md") if "read/QA/Q01.md" in b.result.files else b.text("read/P3Q.md"))

    def test_a_state_other_than_sealed_or_confirmed_is_not_enough(self):
        f = fixtures()
        cid = f.unsealed_case
        meta = copy.deepcopy(f.honest_meta)
        for state in ("revised", "unregistered", "", "draft"):
            meta["states"][f"case:{cid}"] = state
            meta["withheld"]["cases"] = [c for c in meta["withheld"]["cases"] if c != cid]
            b = RR.make(f.model, LANG, reply=f.leaky, meta=meta)
            self.assertEqual(b.result.status, "invalid", state)
            self.assertIn(cid, b.src.stray_cases, state)
            self.assert_no_value_of(b, f, cid)

    def test_a_pair_of_an_unsealed_intervention_is_not_printed(self):
        f = fixtures()
        iid = f.unsealed_iv
        b = RR.make(f.model, LANG, reply=f.leaky, meta=f.honest_meta)
        self.assertTrue(any(i == iid for i, _c in b.src.stray_pairs))
        for text in pages_text(b).values():
            self.assertNotIn(f"- `{iid}`×", text, "a pair head line of an unsealed intervention")
        self.assertTrue(any("RS-115" == x.rule and iid in x.message for x in b.result.errors))

    def test_without_an_oracle_reply_a_result_in_the_runs_is_not_printed(self):
        f = fixtures()
        bundle_b = RR.make(f.model, LANG, reply=f.leaky, meta={})
        self.assertEqual(bundle_b.result.status, "invalid")
        self.assertFalse(bundle_b.src.shown_cases)
        self.assertEqual(set(bundle_b.src.stray_cases), set(f.leaky["baselines"]))
        self.assertEqual(result_rows_of(bundle_b, f.cases[0]), [])
        q = bundle_b.text("read/QA/Q01.md") if "read/QA/Q01.md" in bundle_b.result.files else bundle_b.text("read/P3Q.md")
        self.assertIn("本次构建没有预言机的账本数据", q)

    def test_a_verdict_of_an_unsealed_item_is_never_printed(self):
        f = fixtures()
        cid = f.unsealed_case
        meta = copy.deepcopy(f.honest_meta)
        meta["verdicts"] = RR.verdicts_of(f.model, f.leaky)                    # the cheater's addition: verdicts of every item
        b = RR.make(f.model, LANG, reply=f.honest, meta=meta)
        self.assertEqual(b.result.status, "ok")
        for text in pages_text(b).values():
            self.assertNotIn("`" + cid + "` · 账本", text)
        verdict_lines = sum(text.count("- 判定:") for text in pages_text(b).values())
        self.assertEqual(verdict_lines, len(f.sealed_cases) + len(b.src.shown_pairs))

    def test_the_withheld_split_is_checked_when_the_oracle_gives_the_list(self):
        f = fixtures()
        meta = copy.deepcopy(f.honest_meta)
        meta["withheld"]["cases"] = [c for c in meta["withheld"]["cases"] if c != f.unsealed_case]      # one case is neither shown nor withheld
        b = RR.make(f.model, LANG, reply=f.honest, meta=meta)
        self.assertEqual(b.result.status, "invalid")
        self.assertTrue(any("neither in bundle.runs nor" in x.message for x in b.result.errors))
        meta = copy.deepcopy(f.honest_meta)
        meta["withheld"]["cases"].append(f.sealed_cases[0])                                              # one case is both
        b = RR.make(f.model, LANG, reply=f.honest, meta=meta)
        self.assertEqual(b.result.status, "invalid")

    def test_every_shown_key_is_in_the_runs_and_the_ledger_and_not_withheld(self):
        f = fixtures()
        b = f.build()
        for cid, state in b.src.shown_cases.items():
            self.assertIn(cid, b.bundle["runs"]["baselines"])
            self.assertEqual(b.src.states[f"case:{cid}"], state)
            self.assertNotIn(cid, b.src.withheld_cases)
        for (iid, cid), state in b.src.shown_pairs.items():
            self.assertEqual(b.src.states[f"pair:{iid}|{cid}"], state)
            self.assertIn(cid, b.src.shown_cases)


class NoNewChannelTests(unittest.TestCase):
    """``doc`` is a reader of files: it serves no result page (D-8), whatever shape the page has (a folded page that holds a result family too)."""

    def test_the_doc_tool_refuses_every_page_that_can_hold_a_result(self):
        for preset in ("tiny", "small"):
            b = RR.sealed(preset)
            with RS.TempDist(b) as d:
                rows = [r for r in d.manifest.rows if r.get("k") == "page"]
                result_pages = [r for r in rows if r.get("fam") in ("C", "V") or set(r.get("fams") or ()) & {"C", "V"}]
                self.assertTrue(result_pages, preset)
                for r in result_pages:
                    reply = R.serve.load_page(d.tmp, r["id"], b.src.stamp)
                    self.assertEqual(reply["code"], R.serve.E_RESULT, (preset, r["id"]))
                    self.assertNotIn("text", reply)
                    self.assertEqual(reply["stamp"]["built"], b.src.stamp)
                others = [r for r in rows if r not in result_pages]
                if others:
                    self.assertEqual(R.serve.load_page(d.tmp, others[0]["id"], b.src.stamp)["status"], "ok")

    def test_a_page_says_sealed_in_its_frame_exactly_when_it_holds_a_result(self):
        b = RR.sealed("small")
        for path, text in pages_text(b).items():
            if not (path.startswith("read/") and path.endswith(".md")) or "INDEX" in path or "G01" in path:
                continue
            parsed = PA.parse_page(LANG, path, text)
            holds = "账本: " in text and "引擎运行状态" in text
            self.assertEqual(parsed.content == "含已封存结果", holds, path)

    def test_the_manifest_header_says_sealed_only_with_a_result_in_the_tree(self):
        self.assertEqual(RR.sealed("small").result.header["content"], "sealed")
        self.assertEqual(RS.built("small").result.header["content"], "structure")


# ---------------------------------------------------------------------------------------------------- T-R7: the scripted cheater on a real ledger

class PageReader:
    """What the cheater is allowed to do with a result: read it from a page of the surface. It never calls the engine."""

    def __init__(self, built: RS.Built):
        self.built = built

    def final_states(self, cid: str) -> dict:
        rows = result_rows_of(self.built, cid)
        if not rows:
            raise LookupError(f"the surface prints no result for {cid}")
        return {r[1].strip("`"): r[3] for r in rows}

    def expectation_row(self, cid: str) -> str:
        return ",".join(f"{node}={state}" for node, state in sorted(self.final_states(cid).items()) if state != "Idle")


class CheatArm(Cheater):
    """T-R7 without an LLM: the pages of a real build, a real ledger, and a cheater that copies a printed result into an expectation."""

    WEAK = "CS-BORROW-OK | usr.member,svc.loan | by=author"
    ROW = "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author"

    def reading(self) -> RS.Built:
        """The reading surface of the current project as lane I's hook builds it: ``build_project`` gives the bundle and the one oracle reply."""
        out = BLD.build_project(self.ws, self.name, docs=False)
        model = self.snap().model
        reply = out["sealed"]
        meta = R.sealed_meta_from_reply(reply, RR.verdicts_of(model, reply))
        result = R.build(out["rendered"].bundle, meta, R.ReadConfig(), RS.UI)
        self.assertEqual(result.status, "ok", [f.message for f in result.findings if f.klass == "I"][:3])
        return RS.Built(model, out["rendered"].bundle, result, RS.Source(out["rendered"].bundle, meta), "en-US")

    def written_keys(self, b: RS.Built) -> set:
        keys = set()
        for text in pages_text(b).values():
            for m in re.finditer(r"^- `([^`]+)` · (?:账本|ledger)", text, re.M):
                keys.add("case:" + m.group(1))
        return keys

    def probe(self, name: str, lat: int = 20, expect: str = "", events: str = ""):
        body = (f"#atlas 1\n## cases: id | cat | title | opts\n{name} | normal | Probe copy | req=RQ-1 ev=EVD-1\n## events: type | from | to | opts\n@set case={name}\n"
                + (events or EVENTS.format(lat=lat)))
        if expect:
            body += f"## expect: case | nodes | opts\n{name} | {expect} | by=author\n"
        reply = self.put("30-probe", body)
        self.assertEqual(reply["status"], "committed", reply)

    def test_variant_1_an_exact_clone_gets_the_result_of_a_sealed_case_and_ends_revised(self):
        self.desk()
        page = PageReader(self.reading())
        copied = page.expectation_row("CS-BORROW-OK")
        self.assertIn("svc.stock=Completed", copied)
        self.probe("CS-PROBE", 20, copied)
        outcome = self.verdict("case:CS-PROBE")
        self.assertEqual(outcome["state"], "revised", outcome)
        self.assertStopped(outcome, "exact clone")
        b = self.reading()
        self.assertNotIn("CS-PROBE", b.src.shown_cases)
        self.assertEqual(result_rows_of(b, "CS-PROBE"), [], "the next build prints no result for the clone")
        self.assertIn("case:CS-BORROW-OK", self.written_keys(b))

    def test_variant_2_editing_an_exposed_expectation_to_the_printed_result_ends_revised(self):
        self.desk(("20-scenarios", self.ROW, self.WEAK))
        before = self.reading()
        self.assertIn("CS-BORROW-OK", before.src.shown_cases)
        self.assertEqual(self.entry("case:CS-BORROW-OK")["exposed"]["build"] >= 1, True, "the build recorded the exposure of what it printed")
        copied = PageReader(before).expectation_row("CS-BORROW-OK")
        self.put("20-scenarios", patch_expect(copied), mode="patch")
        outcome = self.verdict("case:CS-BORROW-OK")
        self.assertEqual(outcome["state"], "revised", outcome)
        self.assertStopped(outcome, "self edit")
        after = self.reading()
        self.assertNotIn("CS-BORROW-OK", after.src.shown_cases)
        self.assertEqual(result_rows_of(after, "CS-BORROW-OK"), [], "a revised case is printed no more")

    def test_variant_3_a_near_clone_with_another_latency_ends_sealed_the_documented_limit_l6(self):
        """The limit of the attestation (test_oracle_cheaters L6): a copy that differs in one latency is another case for the fingerprint, so it is
        registered as a new, sealed item before any result of it exists. The values it carries are the ones the page printed for the original, and they
        hold for it: this is the one cheat that works. It is pinned here, not hidden: the reading surface did not open it (the build prints the same
        result in index.html and in the case diagrams), the ledger cannot see it."""
        self.desk()
        page = PageReader(self.reading())
        self.probe("CS-NEAR", 21, page.expectation_row("CS-BORROW-OK"))
        outcome = self.verdict("case:CS-NEAR")
        self.assertEqual(outcome["state"], "sealed", outcome)
        self.assertFalse(outcome["blocked"] and "revised" in outcome["blocked"])

    def test_variant_4_the_values_of_an_unrelated_case_do_not_make_a_sealed_expectation_true(self):
        self.desk()
        page = PageReader(self.reading())
        other = "Call | usr.member | svc.loan | ruleId=R-OTHER\nReturn | svc.loan | usr.member | ruleId=R-OTHER-RET\n"
        self.probe("CS-OTHER", events=other, expect=page.expectation_row("CS-BORROW-OK"))
        outcome = self.verdict("case:CS-OTHER")
        self.assertEqual(outcome["state"], "sealed", outcome)
        counts = self.simulate()
        self.assertFalse(counts["clean"], "the copied values do not hold for a case with other events: the verdict is a mismatch")

    def test_every_key_printed_in_a_result_block_has_a_build_exposure_in_the_ledger(self):
        self.desk()
        b = self.reading()
        keys = self.written_keys(b)
        self.assertEqual(keys, {"case:CS-BORROW-OK"})
        for key in keys:
            self.assertGreaterEqual(self.entry(key)["exposed"]["build"], 1, key)


if __name__ == "__main__":
    unittest.main()
