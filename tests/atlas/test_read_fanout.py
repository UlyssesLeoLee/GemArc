"""T-R13 (design A3 8.2): fan-out and envelope. A project of C bytes of content has at most ``2 + ceil(C / 5,400)`` reading pages (START and the
glossary apart), the frame is at most 45 percent of the page bytes (measured: 12-17 percent), no frame is wider than the limit of its class, no manifest line
is longer than 500 characters.

``C`` is the size of the bodies of all families as the planner measures them. The synthetic projects are the presets scaled down (the generator cannot
make a project of exactly 5, 10, 20 or 40 KB, so the nearest scales are used and the real ``C`` is asserted, not the nominal one).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_read_fanout
"""
from __future__ import annotations

import dataclasses
import math
import unittest

from gemarc_mcp.atlas.reading import api
from gemarc_mcp.atlas.reading import compose as CP
from gemarc_mcp.atlas.reading import families as F
from gemarc_mcp.atlas.reading import names as N
from gemarc_mcp.atlas.reading import parse as P

from . import read_support as RS
from . import synth

LANG = "zh-CN"
BASE = synth.PRESETS["small"]


def scaled(factor: float):
    kw = dict(nodes=max(4, int(BASE.nodes * factor)), edges=max(3, int(BASE.edges * factor)), evidences=max(3, int(BASE.evidences * factor)),
              cases=max(1, int(BASE.cases * factor)), events=max(6, int(BASE.events * factor)), interventions=max(1, int(BASE.interventions * factor)),
              requirements=max(1, int(BASE.requirements * factor)), tbds=max(1, int(BASE.tbds * factor)), tests=max(1, int(BASE.tests * factor)),
              policies=max(1, int(BASE.policies * factor)), rooms=max(2, int(BASE.rooms * factor)), floors=max(2, int(BASE.floors * factor)) if factor > 0.3 else 2,
              buildings=2)
    return dataclasses.replace(BASE, **kw)


def content_bytes(b: RS.Built) -> int:
    fams = {k: build(b.src) for k, build in F.BUILDERS.items()}
    return sum(CP.compose_size(b.src, [(f, f.units)], 0) for f in fams.values() if f.units)


def figures(b: RS.Built) -> dict:
    pages = {p: P.parse_page(LANG, p, d.decode("utf-8")) for p, d in b.result.files.items()
             if p.startswith("read/") and p.endswith(".md") and "INDEX" not in p and "G01" not in p}
    env = sum(x.envelope_bytes for x in pages.values())
    total = sum(len(b.result.files[p]) for p in pages)
    return {"pages": len(pages), "ratio": env / total, "pagesinfo": pages}


class FanOutTests(unittest.TestCase):
    CASES = (("tiny", None), ("small x0.1", 0.1), ("small x0.25", 0.25), ("small x0.6", 0.6), ("small", None))

    def build(self, name: str, factor):
        if name == "tiny":
            return RS.built("tiny")
        if name == "small":
            return RS.built("small")
        return RS.make(synth.generate(scaled(factor), 1))

    def test_pages_stay_within_the_bound_and_the_frame_within_its_share(self):
        seen = []
        for name, factor in self.CASES:
            b = self.build(name, factor)
            c = content_bytes(b)
            f = figures(b)
            seen.append((name, c, f["pages"]))
            with self.subTest(project=name, content=c):
                self.assertLessEqual(f["pages"], 2 + math.ceil(c / 5400))
                self.assertLessEqual(f["ratio"], 0.45)
                self.assertEqual(b.result.status, "ok")
        sizes = [c for _n, c, _p in seen]
        self.assertTrue(sizes[0] < 10_000 and 9_000 < sizes[1] < 11_000 and 18_000 < sizes[2] < 22_000 and 40_000 < sizes[3] < 48_000, sizes)

    def test_the_widest_frame_stays_inside_the_limit_of_its_class_on_every_preset_and_seed(self):
        for preset in RS.PRESETS:
            for seed in (1, 6):
                b = RS.built(preset, LANG, seed)
                for path, page in figures(b)["pagesinfo"].items():
                    info = N.parse_page_id(page.pid)
                    with self.subTest(preset=preset, seed=seed, path=path):
                        self.assertLessEqual(page.envelope_bytes, api.envelope_limit(f"{info.family}:{page.cls}", LANG, page.continuation is not None))
                for row in [b.result.records[0]] + b.result.records:
                    self.assertLessEqual(len(__import__("json").dumps(row, ensure_ascii=False)), 500)

    def test_the_frame_share_is_measured_at_every_scale(self):
        for preset in ("small", "reference", "refcjk"):
            b = RS.built(preset)
            self.assertLess(figures(b)["ratio"], 0.20, preset)


if __name__ == "__main__":
    unittest.main()
