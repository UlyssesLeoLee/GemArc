"""The 4D/5D pages of the reading surface (milestone R3, design 2.2 rows C, V, Q; 2.10; D-32; AM-07; RS-108, RS-115, RS-116, RS-123).

Every model is synthetic (``synth``); the runs are the ones ``read_runs.reply_of`` makes (``oracle.sealed_runs`` without a ledger), the verdicts the ones
of its reference helper ``verdicts_of``. The tree lint at the end writes a whole ``dist/`` (documents, headed diagrams, twins) and runs every check.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_results
"""
from __future__ import annotations

import ast
import copy
import hashlib
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.reading import parse as PA
from gemarc_mcp.atlas.reading import quality as Q
from gemarc_mcp.atlas.reading import results as RES

from . import read_runs as RR
from . import read_support as RS

READING = Path(__file__).resolve().parents[2] / "src" / "gemarc_mcp" / "atlas" / "reading"
LANGS = ("zh-CN", "en-US")
PRESET_SEEDS = (("tiny", 1), ("small", 1), ("small", 6), ("reference", 1))


def texts(b: RS.Built) -> dict:
    return {p: d.decode("utf-8") for p, d in b.result.files.items() if p.endswith(".md")}


def all_lines(b: RS.Built):
    for path, text in texts(b).items():
        for line in text.split("\n"):
            yield path, line


class CleanTests(unittest.TestCase):
    def test_every_preset_seed_and_language_builds_with_results_and_no_integrity_error(self):
        for preset, seed in PRESET_SEEDS:
            for lang in LANGS:
                b = RR.sealed(preset, lang, seed)
                with self.subTest(preset=preset, seed=seed, lang=lang):
                    self.assertEqual(b.result.status, "ok", [f"{f.rule} {f.message}" for f in b.result.errors][:3])
                    self.assertEqual(b.result.header["content"], "sealed")
                    self.assertTrue(b.src.shown_cases and b.src.shown_pairs)
                    self.assertEqual(set(b.src.shown_cases), set(b.bundle["runs"]["baselines"]))

    def test_the_scope_of_a_verdict_never_exceeds_the_nodes_of_the_model(self):
        rx = RES.regexes("zh-CN")
        for preset, seed in PRESET_SEEDS:
            b = RR.sealed(preset, "zh-CN", seed)
            seen = 0
            for _path, line in all_lines(b):
                for name in ("verdict_case", "verdict_pair"):
                    m = rx[name].match(line)
                    if m:
                        seen += 1
                        k, n, j = int(m.group("k")), int(m.group("n")), int(m.group("j"))
                        self.assertLessEqual(j, k)
                        self.assertLessEqual(k, n)
                        self.assertEqual(n, len(b.src.node_order))
            self.assertGreater(seen, 0, preset)


class CaseResultTests(unittest.TestCase):
    def test_a_result_block_is_the_stored_verdict_and_the_whole_terminal_state_side_by_side(self):
        b = RR.sealed("small", "zh-CN", 1)
        verdicts = RR.verdicts_of(b.model, RR.cached_reply("small", "zh-CN", 1))
        rx = RES.regexes("zh-CN")
        by_case: dict = {}
        for path, text in texts(b).items():
            lines = text.split("\n")
            for n, line in enumerate(lines):
                m = rx["case_head"].match(line)
                if m:
                    cid = m.group("ref").strip("`")
                    by_case[cid] = (path, lines[n + 1])
        self.assertEqual(set(by_case), set(b.src.shown_cases))
        for cid, (_path, verdict_line) in by_case.items():
            entry = verdicts[f"case:{cid}"]
            m = rx["verdict_case"].match(verdict_line)
            self.assertIsNotNone(m, verdict_line)
            self.assertEqual((int(m.group("j")), int(m.group("k"))), tuple(entry["terminal"]))
            self.assertEqual((int(m.group("rj")), int(m.group("ra"))), tuple(entry["outcomes"]))
            self.assertEqual(m.group("word"), "通过" if entry["pass"] else "不通过")
            case = b.src.ix.case[cid]
            self.assertEqual(int(m.group("k")), len(case["expectedTerminalState"]))
            run = b.bundle["runs"]["baselines"][cid]
            rows = {}
            for _p, line in all_lines(b):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if line.startswith("| `" + cid + "` |") and len(cells) == 4:
                    rows[cells[1].strip("`")] = (cells[2], cells[3])
            self.assertEqual(set(rows), set(case["expectedTerminalState"]) | set(run["finalState"]), "no cap and no omission: every asserted and every non-Idle node")
            for node, (want, got) in rows.items():
                self.assertEqual(want, case["expectedTerminalState"].get(node, "（未断言）"))
                self.assertEqual(got, run["finalState"].get(node, "Idle"))

    def test_an_unsealed_case_has_one_sentence_and_nothing_else(self):
        b = RR.make(RS.model_of("small", 1), "zh-CN", sealed_cases=[])
        self.assertEqual(b.result.status, "ok")
        self.assertEqual(b.result.header["content"], "structure")
        sentences = [line for _p, line in all_lines(b) if line.startswith("- `") and "未封存，此处不显示结果" in line]
        self.assertEqual(len(sentences), len(b.src.case_ids()))
        self.assertFalse([1 for _p, line in all_lines(b) if "账本: " in line and "引擎运行状态" in line])
        self.assertFalse([1 for _p, line in all_lines(b) if line.startswith("- 判定:")])

    def test_a_confirmed_item_says_so_and_a_sealed_one_says_that(self):
        model = RS.model_of("small", 1)
        cases, ivs = RR.eligible(model)
        b = RR.make(model, "zh-CN", sealed_cases=cases[:1], confirmed_cases=cases[1:2], sealed_ivs=[], confirmed_ivs=[])
        self.assertEqual(b.result.status, "ok", [f.message for f in b.result.errors][:3])
        heads = [line for _p, line in all_lines(b) if "· 账本: " in line and "引擎运行状态" in line]
        self.assertEqual(sorted(re.search(r"账本: (\S+)", line).group(1) for line in heads), ["已确认", "已封存"])

    def test_an_item_the_engine_could_not_decide_says_undecided_and_the_check_agrees(self):
        f = RR.cached_reply("small", "zh-CN", 1)
        model = RS.model_of("small", 1)
        meta = RR.meta_of(f, model)
        cid = sorted(f["baselines"])[0]
        meta["verdicts"][f"case:{cid}"] = {"undecided": "LIMIT_EXCEEDED"}
        pkey = next(k for k in meta["verdicts"] if k.startswith("pair:"))
        meta["verdicts"][pkey] = {"undecided": "LIMIT_EXCEEDED"}
        b = RR.make(model, "zh-CN", reply=f, meta=meta)
        self.assertEqual(b.result.status, "ok", [x.message for x in b.result.errors][:3])
        lines = [line for _p, line in all_lines(b) if line.startswith("- 判定: 无法判定")]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("原因: 引擎没有给出可比较的结果" in line for line in lines))

    def test_without_verdict_data_the_blocks_say_so_and_the_page_does_not_compute_one(self):
        b = RR.sealed("small", "zh-CN", 1, verdicts=False)
        self.assertEqual(b.result.status, "ok")
        verdict_lines = [line for _p, line in all_lines(b) if line.startswith("- 判定:")]
        self.assertTrue(verdict_lines)
        self.assertEqual(set(verdict_lines), {"- 判定: 无（本构建没有判定数据；页面不自算判定）"})
        self.assertEqual(len(verdict_lines), len(b.src.shown_cases) + len(b.src.shown_pairs))
        q = texts(b)["read/QA/Q01.md"] if "read/QA/Q01.md" in b.result.files else texts(b)["read/P3Q.md"]
        self.assertIn("本次构建没有判定数据", q)

    def test_the_frame_of_a_result_page_says_so_and_the_reading_line_explains_the_verdict(self):
        b = RR.sealed("small", "zh-CN", 1)
        for path, text in texts(b).items():
            if re.search(r"/(C|V)\d+\.md$", path):
                page = PA.parse_page("zh-CN", path, text)
                self.assertEqual(page.content, "含已封存结果", path)
                if "- 判定:" in text:
                    self.assertIn("判定只比较作者列出的节点", page.howto, path)
                if any(w in text for w in ("Implemented", "Designed", "Inferred", "Hypothetical", "Observed")):
                    self.assertIn("等级(高→低)", page.howto, path)


class InterventionTests(unittest.TestCase):
    def test_every_intervention_has_one_home_and_every_pair_is_a_block_or_a_sentence(self):
        for preset, seed in PRESET_SEEDS:
            b = RR.sealed(preset, "zh-CN", seed)
            rows = [line.split("\t") for line in b.text("ids.tsv").split("\n") if line and not line.startswith("#")]
            homes = [r[0] for r in rows if r[1] == "intervention" and r[2] == "home"]
            ivs = [i["interventionId"] for i in b.model["interventions"]]
            self.assertEqual(sorted(homes), sorted(ivs), preset)
            blocks = {}
            for _p, line in all_lines(b):
                m = RES.regexes("zh-CN")["pair_head"].match(line)
                if m:
                    blocks[m.group("ref")] = blocks.get(m.group("ref"), 0) + 1
            sentences = {}
            for _p, line in all_lines(b):
                m = re.match(r"^- (`[^`]+`×`[^`]+`): 该用例未封存", line)
                if m:
                    sentences[m.group(1)] = sentences.get(m.group(1), 0) + 1
            for iv in b.model["interventions"]:
                iid = iv["interventionId"]
                if not any(i == iid for i, _c in b.src.shown_pairs):
                    continue
                for cid in dict.fromkeys(iv["appliesTo"]):
                    key = RES.pair_key(iid, cid)
                    self.assertEqual(blocks.get(key, 0) + sentences.get(key, 0), 1, (preset, key))

    def test_a_diverged_pair_carries_its_steps_and_effects_a_pair_without_difference_none(self):
        b = RR.sealed("reference", "zh-CN", 1)
        rx = RES.regexes("zh-CN")
        diverged = [p for p in b.bundle["runs"]["pairs"] if (p.get("diff") or {}).get("outcomeClass") not in (None, "NO_DIFFERENCE")]
        self.assertTrue(diverged, "the reference preset has a diverging pair")
        pair = diverged[0]
        key = RES.pair_key(pair["interventionId"], pair["caseId"])
        steps = [line for _p, line in all_lines(b) if line.startswith("| " + key + " |")]
        n_steps, n_effects = len(pair["chain"]["steps"]), len(pair["chain"]["effects"])
        self.assertEqual(len(steps), n_steps + n_effects)
        self.assertTrue([1 for _p, line in all_lines(b) if rx["chain"].match(line)], "a pair with steps and effects says how many of each follow")
        same = [p for p in b.bundle["runs"]["pairs"] if (p.get("diff") or {}).get("outcomeClass") == "NO_DIFFERENCE"][0]
        self.assertFalse([1 for _p, line in all_lines(b) if line.startswith("| " + RES.pair_key(same["interventionId"], same["caseId"]) + " |")])

    def test_a_long_chain_is_cut_at_the_cap_and_says_how_much_is_missing(self):
        model = RS.model_of("reference", 1)
        reply = copy.deepcopy(RR.cached_reply("reference", "zh-CN", 1))
        pair = next(p for p in reply["pairs"] if (p["result"].get("diff") or {}).get("outcomeClass") not in (None, "NO_DIFFERENCE"))
        pair["chain"]["steps"] = [dict(pair["chain"]["steps"][0], id=f"B{n}") for n in range(RES.STEP_CAP + 17)]
        b = RR.make(model, "zh-CN", reply=reply, meta=R.sealed_meta_from_reply(reply, RR.verdicts_of(model, reply)))
        self.assertEqual(b.result.status, "ok", [f.message for f in b.result.errors][:3])
        key = RES.pair_key(pair["interventionId"], pair["caseId"])
        step_rows = [line for _p, line in all_lines(b) if re.match(r"^\| " + re.escape(key) + r" \| B\d+ \|", line)]
        self.assertEqual(len(step_rows), RES.STEP_CAP)
        self.assertTrue([1 for _p, line in all_lines(b) if line.startswith("- 步骤: 40 / 57，+17 未显示")])

    def test_author_text_with_markup_stays_inside_its_line(self):
        model = copy.deepcopy(RS.model_of("small", 1))
        model["interventions"][0]["description"] = "a | b `c` [N/A] <x> **y**"
        model["interventions"][0]["note"] = "line one\nline two | two"
        b = RR.make(model, "zh-CN")
        self.assertEqual(b.result.status, "ok", [f.message for f in b.result.errors][:3])
        for path, text in texts(b).items():
            for line in text.split("\n"):
                self.assertLessEqual(len(line), 500, path)


class StartAndQualityTests(unittest.TestCase):
    def test_start_has_a_route_for_interventions_and_says_where_results_are(self):
        b = RR.sealed("small", "zh-CN", 1)
        start = b.text("START.md")
        self.assertIn("某干预的影响", start)
        self.assertIn("内容: 含已封存结果", start)
        self.assertIn("经用户确认", start)
        self.assertLessEqual(len(b.result.files["START.md"]), 6000)
        g = b.text("read/G01.md")
        self.assertIn("结果块怎么读", g)
        self.assertIn("断言 k / 共 n 个节点", g)

    def test_the_quality_page_lists_what_has_no_result_by_id_and_counts_the_rest(self):
        b = RR.make(RS.model_of("small", 1), "zh-CN", sealed_cases=RR.eligible(RS.model_of("small", 1))[0][:2])
        q = b.text("read/QA/Q01.md") if "read/QA/Q01.md" in b.result.files else b.text("read/P3Q.md")
        self.assertIn("- 用例: 已封存 2 · 已确认 0 · 被扣下 ", q)
        withheld = [line for line in q.split("\n") if re.match(r"^\| `[^`]+` \| (用例|干预) \| ", line)]
        want = {key for key, _kind in Q.withheld_rows(b.src)}
        self.assertEqual({re.match(r"^\| `([^`]+)`", line).group(1) for line in withheld}, want)

    def test_the_t_r2_bound_holds_with_results_on_the_reference_preset(self):
        b = RR.sealed("reference", "zh-CN", 1)
        ai = [r for r in b.result.records if r.get("ai") and r.get("cls") in ("card", "page")]
        self.assertLessEqual(len(ai), 130)
        self.assertLessEqual(sum(r["b"] for r in ai), 650_000)
        self.assertLessEqual(max(r["b"] for r in ai), 9000)


class ModuleTests(unittest.TestCase):
    def test_the_module_that_makes_the_result_blocks_does_not_touch_the_engine(self):
        tree = ast.parse((READING / "results.py").read_text(encoding="utf-8"))
        banned_modules = {"engine", "oracle", "mutate", "causal"}
        banned_names = {"simulate", "run_causal", "violations", "check_prediction", "index_model", "causal_chain", "sealed_runs", "run_verdicts"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(banned_modules & set((node.module or "").split(".")), node.module)
                self.assertFalse(banned_names & {a.name for a in node.names})
            elif isinstance(node, ast.Import):
                self.assertFalse(banned_modules & {part for a in node.names for part in a.name.split(".")})
            elif isinstance(node, ast.Name):
                self.assertNotIn(node.id, banned_names)
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, banned_names)

    def test_nothing_in_the_page_builder_compares_a_terminal_state(self):
        """The verdict is read from sealed_meta: no ``expectedTerminalState`` value is compared with a ``finalState`` value in ``results.py``."""
        source = (READING / "results.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Compare):
                text = ast.unparse(node)
                self.assertFalse("expected" in text and "final" in text, text)


class DeterminismTests(unittest.TestCase):
    def test_three_hash_seeds_give_the_same_files(self):
        code = (
            "import hashlib, json, sys\n"
            "from tests.atlas import read_runs as RR\n"
            "b = RR.sealed('small', 'zh-CN', 1, html=True)\n"
            "h = hashlib.sha256()\n"
            "for p in sorted(b.result.files):\n"
            "    h.update(p.encode()); h.update(b.result.files[p])\n"
            "h.update(json.dumps(b.result.records, sort_keys=True).encode())\n"
            "print(h.hexdigest())\n")
        root = Path(__file__).resolve().parents[2]
        digests = set()
        for seed in ("0", "1", "4242"):
            out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=300,
                                 env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"})
            self.assertEqual(out.returncode, 0, out.stderr[-400:])
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1, digests)


class TreeLintTests(unittest.TestCase):
    """The whole reading tree of a build with results, documents, headed diagrams and twins: every check, from the files on disk."""

    def lint(self, preset: str, lang: str, seed: int) -> list:
        b = RR.sealed(preset, lang, seed, html=True)
        with RS.TempDist(b, real_docs=True) as d:
            surface = d.surface
            findings = RS.check(b, surface)
            self.assertTrue(any(p.startswith("diagrams/") for p in surface.files))
            self.assertTrue(any(p.startswith("site/") for p in surface.files))
        return [f for f in findings if f.klass == "I" and f.severity == "ERROR"]

    def test_links_resolve_every_id_has_one_home_and_every_sentinel_verifies(self):
        for preset, lang, seed in (("tiny", "zh-CN", 1), ("small", "zh-CN", 1), ("small", "en-US", 6), ("reference", "zh-CN", 1)):
            with self.subTest(preset=preset, lang=lang, seed=seed):
                errors = self.lint(preset, lang, seed)
                self.assertEqual([f"{f.rule} {f.path} {f.message}" for f in errors][:5], [])


if __name__ == "__main__":
    unittest.main()
