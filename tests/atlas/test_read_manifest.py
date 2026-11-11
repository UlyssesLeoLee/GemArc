"""manifest.jsonl, ids.tsv, refs.tsv, INDEX, START and the determinism of all of them (design A3 2.7, D-7, D-34, D-35, RS-119).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_manifest
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.reading import check as C
from gemarc_mcp.atlas.reading import tables as TB

from . import read_support as RS

LANG = "zh-CN"
REPO = Path(__file__).resolve().parents[2]


class ManifestTests(unittest.TestCase):
    def test_the_manifest_registers_what_the_design_says_and_nothing_else(self):
        b = RS.built("small")
        with RS.TempDist(b) as d:
            m = d.manifest
            paths = {r["p"] for r in m.rows}
            self.assertNotIn("manifest.jsonl", paths)
            self.assertNotIn("build-info.json", paths)
            self.assertFalse([p for p in paths if p.startswith(".verify")])
            by = {r["p"]: r for r in m.rows}
            self.assertEqual({k: by["oracle-sheet.md"].get(k) for k in ("k", "cls", "ai", "twin")}, {"k": "sheet", "cls": "handoff", "ai": False, "twin": None})
            self.assertNotIn("sha8", by["oracle-sheet.md"])
            self.assertEqual((by["docs/zh-CN/DD.md"]["k"], by["docs/zh-CN/DD.md"]["ai"], by["docs/zh-CN/DD.md"]["twin"]), ("doc", False, None))
            self.assertEqual((by["prompts/PR-DD-0123abcd.md"]["k"], by["prompts/PR-DD-0123abcd.md"]["ai"]), ("prompt", False))
            self.assertEqual((by["index.html"]["k"], by["index.html"]["ai"]), ("viewer", False))
            diagrams = [r for r in m.rows if r["k"] == "diagram"]
            self.assertTrue(diagrams)
            self.assertTrue(all(r["ai"] and r["twin"] is None for r in diagrams))
            for row in m.rows:
                if row["p"] == "oracle-sheet.md":
                    continue
                data = (d.tmp / row["p"]).read_bytes()
                self.assertEqual((row["b"], row["sha8"]), (len(data), hashlib.sha256(data).hexdigest()[:8]), row["p"])
            self.assertEqual(m.unregistered, [])

    def test_the_header_is_consistent_and_short(self):
        b = RS.built("small")
        with RS.TempDist(b) as d:
            h, rows = d.manifest.header, d.manifest.rows
            self.assertEqual(h["schema"], "gemarc-atlas-manifest/1")
            self.assertEqual(h["files"], len(rows))
            self.assertEqual(sum(h["classes"].values()), h["files"])
            ai = [r for r in rows if r.get("ai") and r["cls"] in ("card", "page", "svg")]
            self.assertEqual((h["aiFiles"], h["aiBytes"]), (len(ai), sum(r["b"] for r in ai)))
            self.assertEqual(h["diagramFiles"], len([r for r in rows if r["cls"] == "diagram"]))
            self.assertEqual((h["m"], h["lang"], h["project"], h["blockers"]), (b.src.stamp, LANG, b.src.project, 5))
            self.assertEqual(set(h["ui"]), {"decision"})
            for line in d.manifest.text.split("\n"):
                self.assertLessEqual(len(line), 500)
            first = d.manifest.text.split("\n")[0]
            self.assertEqual(json.loads(first), h)
            self.assertEqual(first, json.dumps(h, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            self.assertNotIn('"withheld"', first)
            self.assertEqual(h["withheldCount"], len(b.src.unsealed_cases()) + len(b.src.unsealed_interventions()))

    def test_a_stranger_file_is_a_warning_and_a_clean_tree_has_no_integrity_error(self):
        b = RS.built("small")
        with RS.TempDist(b) as d:
            (d.tmp / "Thumbs.db").write_bytes(b"x")
            m = R.finalize(d.tmp, b.result)
            self.assertEqual(m.unregistered, ["Thumbs.db"])
            self.assertNotIn("Thumbs.db", {r["p"] for r in m.rows})
            findings = C.check_surface(b.src, C.surface_from_dist(d.tmp))
            self.assertEqual(RS.rules_of(findings), set())
            self.assertEqual({(f.severity, f.path) for f in findings if f.rule == "RS-109"}, {("WARN", "Thumbs.db")})

    def test_the_manifest_is_written_last_and_identical_when_written_again(self):
        b = RS.built("small")
        with RS.TempDist(b) as d:
            first = (d.tmp / "manifest.jsonl").read_bytes()
            mtimes = {p.name: p.stat().st_mtime_ns for p in d.tmp.rglob("*") if p.is_file()}
            self.assertGreaterEqual(mtimes["manifest.jsonl"], max(v for k, v in mtimes.items() if k != "manifest.jsonl"))
            R.finalize(d.tmp, b.result)
            self.assertEqual((d.tmp / "manifest.jsonl").read_bytes(), first)


class IdsAndRefsTests(unittest.TestCase):
    def test_ids_tsv_has_one_home_per_id_and_kind_with_the_columns_of_the_design(self):
        b = RS.built("small")
        text = b.text("ids.tsv")
        lines = text.split("\n")
        self.assertTrue(lines[0].startswith("# schema gemarc-atlas-ids/1 m=" + b.src.stamp))
        self.assertEqual(lines[1], "# id\tkind\trole\tpath\tline\thdr\tname")
        rows = [x.split("\t") for x in lines[2:] if x]
        self.assertTrue(all(len(r) == 7 for r in rows))
        homes = {}
        for ident, kind, role, path, line, hdr, name in rows:
            self.assertIn(role, ("home", "view:inbound"))
            self.assertLessEqual(len(name), 120)
            self.assertLessEqual(len("\t".join((ident, kind, role, path, line, hdr, name))), 400)
            if role == "home":
                homes[(ident, kind)] = homes.get((ident, kind), 0) + 1
        self.assertEqual(set(homes), C.expected_homes(b.src))
        self.assertEqual(set(homes.values()), {1})
        self.assertEqual({r[1] for r in rows} - {"node", "edge", "case", "event", "requirement", "test", "tbd", "evidence", "machine", "transition", "entity",
                                                 "room", "floor", "building", "diagram", "intervention"}, set())

    def test_an_alias_edge_is_named_by_its_ends_and_found_by_the_anchored_pattern(self):
        b = RS.built("reference")
        ix = b.src.ix
        alias = next(e for e in ix.edges if e["targetId"] not in e["id"])
        rows = [x.split("\t") for x in b.text("ids.tsv").split("\n") if x and not x.startswith("#")]
        mine = [r for r in rows if r[0] == alias["id"]]
        self.assertEqual(sorted(r[2] for r in mine), ["home", "view:inbound"])
        home = next(r for r in mine if r[2] == "home")
        self.assertTrue(home[6].startswith(alias["sourceId"] + ">" + alias["targetId"]))
        hits = [r for r in rows if re.match("^" + re.escape(alias["id"]) + "$", r[0])]
        self.assertEqual(len(hits), 2)
        # a prefix of ids is not an id: the anchored pattern does not match it
        self.assertEqual([r for r in rows if r[0] == alias["id"][:6]], [])

    def test_every_reference_line_contains_its_target_and_its_owner(self):
        b = RS.built("small")
        lines = [x.split("\t") for x in b.text("refs.tsv").split("\n") if x and not x.startswith("#")]
        self.assertTrue(lines)
        self.assertEqual({r[1] for r in lines}, {"req", "ev", "test"} | ({"tbd"} & {r[1] for r in lines}))
        for target, field, owner, path, line in lines:
            text = b.text(path).split("\n")[int(line) - 1]
            self.assertIn("`" + target + "`", text)
        self.assertEqual(len(set(map(tuple, lines))), len(lines))

    def test_the_references_that_a_capped_cell_hides_are_counted_not_lost_silently(self):
        b = RS.built("reference")
        self.assertGreater(b.result.plan["unprinted_refs"], 0)
        self.assertEqual(b.result.plan["refs"], len(b.result.refs))
        every = b.result.plan["refs"] + b.result.plan["unprinted_refs"]
        self.assertLess(b.result.plan["unprinted_refs"] / every, 0.20)      # 12-14 percent measured at reference scale


class IndexAndStartTests(unittest.TestCase):
    def test_every_dimension_with_four_pages_has_an_index_that_lists_each_page_once(self):
        b = RS.built("reference")
        indexes = [p for p in b.result.files if re.search(r"/INDEX(-\d+)?\.md$", p)]
        self.assertTrue(indexes)
        listed = []
        for path in indexes:
            self.assertLessEqual(len(b.result.files[path]), 6000)
            listed += re.findall(r"^\| \[§([A-Z]\d+)\]", b.text(path), re.M)
        pages = [p.rsplit("/", 1)[1][:-3] for p in b.result.files if re.search(r"/(1D|2D|4D)/[A-Z]\d+\.md$", p)]
        self.assertEqual(sorted(listed), sorted(pages))
        # an index of two parts links all its parts
        two = [p for p in indexes if p.endswith("INDEX-2.md")]
        for path in two:
            self.assertIn("INDEX.md", b.text(path))
            self.assertIn("INDEX-2.md", b.text(path))

    def test_start_has_a_row_for_every_family_and_says_why_a_count_is_zero(self):
        model = copy.deepcopy(RS.model_of("small", 1))
        model["cases"], model["requirements"], model["tests"], model["interventions"] = [], [], [], []
        b = RS.make(model)
        start = b.text("START.md")
        rows = {x.split(" | ")[0].lstrip("| "): x for x in start.split("\n") if x.startswith("| ") and " | " in x}
        for family in ("1D 需求", "1D 待决与政策", "1D 证据", "2D 结构", "2D 数据", "2D 状态机", "3D 空间", "4D 用例", "5D 干预", "质量与阻塞"):
            self.assertIn(family, rows)
        self.assertIn("本项目没有", rows["4D 用例"])
        self.assertIn("模型里没有用例", rows["4D 用例"])
        self.assertIn("模型里没有需求", rows["1D 需求"])
        self.assertIn("模型里没有干预", rows["5D 干预"])
        self.assertLessEqual(len(b.result.files["START.md"]), 6000)

    def test_start_names_the_folded_page_and_the_single_page(self):
        tiny = RS.built("tiny").text("START.md")
        self.assertIn("见 §A01", tiny)
        self.assertEqual(RS.rules_of(RS.check(RS.built("tiny"), RS.built("tiny").surface())), set())

    def test_start_points_to_the_stamp_check_and_never_links_a_handover_file(self):
        b = RS.built("small")
        start = b.text("START.md")
        self.assertIn('gemarc_atlas_index scope="stamp"', start)
        self.assertNotRegex(start, r"\]\((docs|prompts)/")
        self.assertNotIn("](index.html)", start)
        self.assertIn("<dist>", start)           # no machine path in a generated file


class StartDegradationTests(unittest.TestCase):
    def test_start_above_its_cap_moves_the_client_hints_to_the_glossary(self):
        from gemarc_mcp.atlas.reading import specials as SP

        b = RS.built("small")
        rendered = {}
        from gemarc_mcp.atlas.reading import pages as PG
        from gemarc_mcp.atlas.reading import families as F
        from gemarc_mcp.atlas.reading import plan as PL
        from gemarc_mcp.atlas.reading import quality as Q

        families = {k: build(b.src) for k, build in F.BUILDERS.items()}
        families = {k: f for k, f in families.items() if f.units}
        families["Q"] = Q.build_q(b.src, b.result.exclusions)
        plan = PL.assign(PL.plan_surface(b.src, families, PL.Plan()))
        home, summaries = PG.home_map(plan), PG.summaries_of(plan)
        for page in plan.pages:
            rendered[page.pid] = PG.render_page(b.src, page, plan, summaries, home, {})
        full, degraded = SP.build_start(b.src, plan, rendered, {}, RS.UI, R.ReadConfig())
        self.assertFalse(degraded)
        small, degraded = SP.build_start(b.src, plan, rendered, {}, RS.UI, R.ReadConfig(), cap=1000)
        self.assertTrue(degraded)
        self.assertLess(len(small.text), len(full.text))
        self.assertIn("§G01", small.text)
        self.assertNotIn("Hermes", small.text)
        self.assertIn("Hermes", b.text("read/G01.md"))


class DeterminismTests(unittest.TestCase):
    SCRIPT = (
        "import hashlib, json, sys\n"
        "sys.path[:0] = [sys.argv[1] + '/src', sys.argv[1]]\n"
        "from tests.atlas import read_support as RS\n"
        "b = RS.make(RS.model_of(sys.argv[2], 1))\n"
        "h = hashlib.sha256()\n"
        "for p in sorted(b.result.files):\n"
        "    h.update(p.encode()); h.update(b.result.files[p])\n"
        "h.update(json.dumps(b.result.records, sort_keys=True).encode())\n"
        "print(h.hexdigest(), len(b.result.files))\n"
    )

    def test_three_hash_seeds_give_the_same_bytes(self):
        digests = set()
        for seed in ("0", "1", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
            done = subprocess.run([sys.executable, "-c", self.SCRIPT, str(REPO), "small"], capture_output=True, text=True, env=env, timeout=240)
            self.assertEqual(done.returncode, 0, done.stderr[-600:])
            digests.add(done.stdout.strip())
        self.assertEqual(len(digests), 1, digests)


if __name__ == "__main__":
    unittest.main()
