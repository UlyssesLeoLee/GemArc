"""``tools/pilot/ledger.py`` and ``docs/atlas/TOKEN_LEDGER.md`` (milestone M8): the ledger tool measures bytes per tool and per phase on the scripted pipelines,
labels tokens as estimates, and the document keeps its sections, its honest labels and an EMPTY section for the real runs.

Run: PYTHONPATH=src ./.venv/Scripts/python.exe -m unittest discover -s tests/atlas -t tests -p "test_ledger_tool*.py"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "pilot" / "ledger.py"
DOC = REPO / "docs" / "atlas" / "TOKEN_LEDGER.md"


def text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


class LedgerToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        done = subprocess.run([sys.executable, "-B", str(TOOL), "--only", "tiny,greenfield", "--no-browser"], capture_output=True, text=True, timeout=600, encoding="utf-8",
                              errors="replace", env={**os.environ, "PYTHONPATH": str(REPO / "src")})
        assert done.returncode == 0, done.stderr[-1000:]
        cls.report = json.loads(done.stdout)

    def test_every_run_reaches_done_and_counts_what_it_sent(self):
        for name, run in self.report["runs"].items():
            self.assertTrue(run["done"], name)
            self.assertEqual(run["calls"], sum(v["calls"] for v in run["tools"].values()), name)
            self.assertEqual(run["request"], sum(v["request"] for v in run["tools"].values()), name)
            self.assertEqual(run["reply"], sum(v["reply"] for v in run["tools"].values()), name)
            self.assertEqual(run["calls"], sum(v["calls"] for v in run["phases"].values()), name)
            self.assertLessEqual(run["put"]["textBytes"], run["put"]["requestBytes"], "the JSON request is the text plus its envelope and escapes")
            self.assertEqual(run["tools"]["put"]["text"], run["put"]["textBytes"])
            self.assertGreater(run["derived"]["page"], 100_000)

    def test_tokens_are_the_bytes_divided_by_three_point_two_and_say_so(self):
        self.assertEqual(self.report["method"]["bytesPerToken"], 3.2)
        self.assertIn("estimated", self.report["method"])
        run = self.report["runs"]["greenfield"]
        self.assertEqual(run["scriptedTrafficEstTokens"], round((run["request"] + run["reply"]) / 3.2))

    def test_the_comparison_rows_are_the_rows_of_design_4_2_scaled_by_nodes(self):
        run = self.report["runs"]["greenfield"]
        rows = {r["row"]: r for r in run["comparison"]}
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows["slice text (25 puts)"]["bytes"], run["put"]["textBytes"])
        self.assertEqual(rows["slice text (25 puts)"]["scaled"], round(57400 * run["counts"]["nodes"] / 169))
        self.assertEqual(rows["tools/list + brief all"]["scaled"], 5800, "the fixed part does not scale with the node count")
        self.assertEqual(run["designScaledTotalTokens"], round(119000 * run["counts"]["nodes"] / 169))
        self.assertEqual(self.report["design"]["totalTargetTokens"], 119000)
        self.assertEqual(sum(r["tokens"] for r in self.report["design"]["rows"]), 119200, "the rows of design 4.2 (writes 62.2K plus reads 57.0K) add up to its 119K total")

    def test_the_model_counts_come_from_the_model_and_not_from_literals(self):
        source = text(TOOL)
        self.assertNotIn('"nodes": 8', source)
        self.assertNotIn('"evidences": 86', source)
        self.assertIn("model_counts(ws)", source)
        counts = self.report["runs"]["greenfield"]["counts"]
        self.assertEqual({k: counts[k] for k in ("nodes", "edges", "evidences", "cases", "requirements", "tbds")},
                         {"nodes": 8, "edges": 7, "evidences": 6, "cases": 5, "requirements": 7, "tbds": 3}, "the greenfield flow writes these facts (the document quotes them)")
        self.assertEqual(set(counts), set(("nodes", "edges", "evidences", "cases", "interventions", "requirements", "tbds", "policies")))

    def test_the_report_is_utf_8_with_lf_whatever_the_console_encoding_is(self):
        """On a cp936, cp932 or cp1252 machine the Chinese headers used to be written in the console's code page (or crashed after the minute-long run)."""
        env = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
        for encoding in ("cp932", "cp1252", "cp936"):
            done = subprocess.run([sys.executable, "-B", str(TOOL), "--only", "tiny", "--no-browser", "--markdown"], capture_output=True, timeout=600,
                                  env={**env, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0"})
            self.assertEqual(done.returncode, 0, (encoding, done.stderr[-500:]))
            self.assertIn("工具".encode("utf-8"), done.stdout, encoding)
            done.stdout.decode("utf-8")                         # strict: every byte is UTF-8
            self.assertNotIn(b"\r", done.stdout, f"{encoding}: LF only, so a redirect into a repository file keeps the repository's line endings")

    def test_the_total_rows_are_the_sum_of_the_calls_and_say_what_they_leave_out(self):
        doc = text(DOC)
        """Section 5: the 'sum of calls' row is the section-4 total, tools/list is added on its own row, and the row with tools/list is the like-for-like figure."""
        for heading, first_total in (("### reference（缩放比 1.0）", (183522, 28642)), ("### as-is，25 个模块（缩放比 25/169）", (18330, 31502))):
            section = doc.split(heading, 1)[1].split("\n### ", 1)[0]

            def cells(prefix):
                line = next(row for row in section.splitlines() if row.startswith(prefix))
                return [c.strip() for c in line.strip("|").split("|")]
            calls, tools_list, both = cells("| **"), cells("| （设计表里有、但不在任何一次调用里）tools/list"), cells("| **含 tools/list 的合计")
            self.assertIn("请求加回复，不含 tools/list", calls[0], heading)
            number = lambda cell: int(cell.replace(",", ""))                   # noqa: E731
            self.assertEqual(number(calls[3]), sum(first_total), heading)
            self.assertEqual(number(tools_list[3]), self.report["toolsListBytes"], f"{heading}: the document quotes the tools/list of the current code (re-measure with tools/pilot/ledger.py when a tool description changes)")
            self.assertEqual(number(both[3]), number(calls[3]) + number(tools_list[3]), heading)
            self.assertEqual(number(calls[4]), round(number(calls[3]) / 3.2), heading)
            self.assertEqual(number(both[4]), round(number(both[3]) / 3.2), heading)
        self.assertIn("分项行是逐行对照用的，不是互不重叠的拆分", doc)
        self.assertIn("1.8%", doc)
        self.assertIn("build-info.json", doc.split("## 2.", 1)[0])
        self.assertIn("字节 / 3 是 +3.6%", doc)
        self.assertIn("12.6%", doc)

    def test_the_fixed_reads_in_the_document_are_the_measured_ones(self):
        doc = text(DOC)
        fixed = self.report["toolsListBytes"] + self.report["briefAllBytes"] + self.report["promptBytes"]
        self.assertIn(f"tools/list {self.report['toolsListBytes']:,} 字节 + brief all（zh-CN）{self.report['briefAllBytes']:,} 字节 + 入口提示 {self.report['promptBytes']:,} 字节 = {fixed:,} 字节，估算 {round(fixed / 3.2):,} token", doc)
        self.assertIn(f"合计 {fixed:,} 字节，估算 {round(fixed / 3.2):,} token", doc)

    def test_the_fixed_reads_are_measured_once(self):
        self.assertGreater(self.report["toolsListBytes"], 5000)
        self.assertLessEqual(self.report["toolsListBytes"], 10 * 1024)
        self.assertGreater(self.report["briefAllBytes"], 3000)
        self.assertGreater(self.report["promptBytes"], 3000)


class LedgerDocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = text(DOC)

    def test_the_document_has_the_sections_and_labels_what_is_measured_and_what_is_estimated(self):
        for heading in ("## 1. 先看结论", "## 2. 方法：哪些是实测，哪些是估算", "## 3. 五次运行的合计", "## 4. 每个工具、每个阶段", "## 5. 与设计账本 4.2 对照",
                        "## 6. 共用部分的节省", "## 7. 待实测(LLM)", "## 8. 限制和诚实说明"):
            self.assertIn(heading, self.doc)
        for label in ("**实测**", "**估算**", "**未测**", "字节 / 3.2", "tools/pilot/ledger.py", "tools/pilot/asis_probe.py"):
            self.assertIn(label, self.doc)

    def test_the_llm_section_is_empty_for_the_lead_to_fill(self):
        section = self.doc.split("## 7. 待实测(LLM)", 1)[1].split("## 8.", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("|") and not line.startswith("|---")]
        self.assertGreaterEqual(len(rows), 6)
        for row in rows[1:]:
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertTrue(cells[0], row)
            self.assertEqual([c for c in cells[1:] if c], [], f"no invented number in the LLM table: {row}")
        self.assertIn("现在都是空的", section)

    def test_the_numbers_of_the_summary_table_are_internally_consistent(self):
        table = self.doc.split("## 3. 五次运行的合计", 1)[1].split("读法：", 1)[0]
        for line in table.splitlines():
            m = re.match(r"^\| (reference|small|tiny|asis|greenfield) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| ([\d,]+) \| ([\d,]+) \| ([\d,]+) \| ([\d,]+) \| ([\d,]+) \|$", line)
            if not m:
                continue
            written, read, tokens_w, tokens_r = (int(m.group(i).replace(",", "")) for i in (7, 8, 9, 10))
            self.assertEqual(tokens_w, round(written / 3.2), line)
            self.assertEqual(tokens_r, round(read / 3.2), line)
            self.assertEqual(int(m.group(11).replace(",", "")), round(119000 * int(m.group(2)) / 169), line)
        self.assertEqual(len(re.findall(r"^\| (?:reference|small|tiny|asis|greenfield) \|", table, re.M)), 5)

    def test_no_machine_path_and_no_timestamp_in_the_document(self):
        self.assertNotIn("D:/GemArc", self.doc)
        self.assertNotIn(str(REPO), self.doc)
        self.assertNotIn("C:\\Users", self.doc)
        self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", self.doc))


if __name__ == "__main__":
    unittest.main()
