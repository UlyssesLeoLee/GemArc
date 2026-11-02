"""The PNG reader, the pixel probes and the cache key of the screens (``protoui/pngread.py``, ``protoui/png.py``; design A3 4.6, check SV-208; task U3).

No browser is started here. The fixtures in ``ui_fixtures/png/`` are real pictures made once by headless Edge 152 (``SCR-LOGIN.png``: the default state of
``SCR-LOGIN.svg`` beside it, 1280x828; ``not-found.png``: what the browser draws when the URL is wrong, same size, exit code 0, so only its pixels can
reject it). Everything else is synthetic: hand-made screens (``png_support.mini_svg``), the 12-screen sample of ``screens_fixtures`` and a stand-in that
paints the rectangles of an SVG (``png_support.paint``).

Run: PYTHONPATH=src python -m unittest tests.atlas.test_screens_png
"""
from __future__ import annotations

import hashlib
import os
import random
import re
import struct
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
import zlib
from dataclasses import replace
from pathlib import Path

from gemarc_mcp.atlas.protoui import build as bld
from gemarc_mcp.atlas.protoui import png, pngread
from gemarc_mcp.atlas.protoui.expand import FiveIndex

from . import png_support as ps
from . import screens_fixtures as fx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIX = HERE / "ui_fixtures" / "png"
LOGIN_SVG = (FIX / "SCR-LOGIN.svg").read_text(encoding="utf-8")
LOGIN_PNG = (FIX / "SCR-LOGIN.png").read_bytes()
NOT_FOUND = (FIX / "not-found.png").read_bytes()
SRC = REPO / "src" / "gemarc_mcp" / "atlas" / "protoui"
SVG = "{http://www.w3.org/2000/svg}"


def rows_of(rng: random.Random, width: int, height: int, bpp: int) -> list:
    return [bytes(rng.randrange(256) for _ in range(width * bpp)) for _ in range(height)]


def sample_defaults() -> list:
    """``(path, bytes)`` of the default-state SVG of each screen of the 12-screen sample."""
    return [(p, d) for p, d in sorted(fx.built("sample12").files.items()) if p.startswith("screens/") and p.endswith(".svg") and "~" not in p]


class PngReaderTests(unittest.TestCase):
    def test_every_filter_gives_back_the_rows_in_every_colour_type(self):
        rng = random.Random(11)
        for color, bpp in ((0, 1), (2, 3), (4, 2), (6, 4)):
            for kind in range(5):
                rows = rows_of(rng, 19, 7, bpp)
                image = pngread.Image(ps.encode_png(19, 7, rows, color, [kind]))
                for y in range(7):
                    self.assertEqual(image.row(y), rows[y], (color, kind, y))

    def test_rows_of_mixed_filters_come_out_right_in_any_order_and_agree_with_the_textbook_reader(self):
        rng = random.Random(5)
        for color, bpp in ((2, 3), (6, 4)):
            rows = rows_of(rng, 41, 30, bpp)
            data = ps.encode_png(41, 30, rows, color, [rng.randrange(5) for _ in range(30)])
            self.assertEqual(ps.decode_png_naive(data), rows)
            order = list(range(30))
            rng.shuffle(order)
            image = pngread.Image(data)
            for y in order:
                self.assertEqual(image.row(y), rows[y], (color, y))

    def test_pixels_in_every_colour_type_including_the_palette(self):
        self.assertEqual(pngread.Image(ps.encode_png(2, 1, [bytes([10, 20, 30, 40, 50, 60])], 2)).pixel(1, 0), (40, 50, 60, 255))
        self.assertEqual(pngread.Image(ps.encode_png(2, 1, [bytes([1, 2, 3, 4, 5, 6, 7, 8])], 6)).pixel(1, 0), (5, 6, 7, 8))
        self.assertEqual(pngread.Image(ps.encode_png(2, 1, [bytes([9, 77])], 0)).pixel(1, 0), (77, 77, 77, 255))
        self.assertEqual(pngread.Image(ps.encode_png(1, 1, [bytes([9, 200])], 4)).pixel(0, 0), (9, 9, 9, 200))
        palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
        image = pngread.Image(ps.encode_png(3, 1, [bytes([2, 0, 1])], 3, palette=palette))
        self.assertEqual([image.pixel(x, 0) for x in range(3)], [(0, 0, 255, 255), (255, 0, 0, 255), (0, 255, 0, 255)])
        with self.assertRaises(pngread.PngError):
            pngread.Image(ps.encode_png(1, 1, [bytes([3])], 3, palette=palette)).pixel(0, 0)

    def test_the_sum_of_two_rows_wraps_at_256_in_every_byte(self):
        # Up filter: 255 + 1, 128 + 128, 0 + 0, 127 + 129, 1 + 255, 254 + 2 in one row, so that the carry of a byte must not reach its neighbour
        above = bytes([255, 128, 0, 127, 1, 254] * 5)
        residual = bytes([1, 128, 0, 129, 255, 2] * 5)
        header = struct.pack(">IIBBBBB", 30, 2, 8, 0, 0, 0, 0)
        data = ps.SIGNATURE + ps.chunk(b"IHDR", header) + ps.chunk(b"IDAT", zlib.compress(b"\x00" + above + b"\x02" + residual)) + ps.chunk(b"IEND", b"")
        self.assertEqual(pngread.Image(data).row(1), bytes((a + r) & 255 for a, r in zip(above, residual)))

    def test_only_the_rows_a_pixel_needs_are_decoded(self):
        rows = rows_of(random.Random(3), 8, 8, 3)
        # rows 0 and 3 do not read the row above (None, Sub); every other row does (Up, Average, Paeth)
        image = pngread.Image(ps.encode_png(8, 8, rows, 2, [0, 2, 3, 1, 2, 4, 2, 3]))
        image.pixel(0, 6)
        self.assertEqual(sorted(image._rows), [3, 4, 5, 6])
        image.pixel(0, 2)
        self.assertEqual(sorted(image._rows), [0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(image.row(2), rows[2])

    def test_the_header_is_read_without_inflating_anything(self):
        self.assertEqual(pngread.size_of(LOGIN_PNG), (1280, 828))
        self.assertEqual(pngread.size_of(ps.encode_png(3, 2, [bytes(9)] * 2)), (3, 2))
        with self.assertRaises(pngread.PngError):
            pngread.size_of(b"<svg/>")

    def test_a_damaged_file_is_refused_and_is_not_called_unsupported(self):
        good = ps.encode_png(4, 4, [bytes(12)] * 4)
        flipped = bytearray(good)
        flipped[good.index(b"IDAT") + 6] ^= 0xFF
        short_data = ps.SIGNATURE + ps.chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + ps.chunk(b"IDAT", zlib.compress(bytes(20))) + ps.chunk(b"IEND", b"")
        no_header = ps.SIGNATURE + ps.chunk(b"IEND", b"")
        for name, data in (("signature", b"GIF89a" + good[6:]), ("checksum", bytes(flipped)), ("no end chunk", good[:-12]), ("data too short", short_data),
                           ("no header", no_header), ("empty", b""), ("text", b"not a picture")):
            with self.assertRaises(pngread.PngError, msg=name) as caught:
                pngread.Image(data)
            self.assertFalse(caught.exception.unsupported, name)

    def test_a_valid_file_of_a_kind_the_reader_does_not_decode_is_called_unsupported(self):
        data = ps.chunk(b"IDAT", zlib.compress(b"")) + ps.chunk(b"IEND", b"")
        huge = ps.SIGNATURE + ps.chunk(b"IHDR", struct.pack(">IIBBBBB", 3000, 3000, 8, 2, 0, 0, 0)) + data
        odd = ps.SIGNATURE + ps.chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 5, 0, 0, 0)) + data
        for name, data in (("16 bit", ps.encode_png(2, 2, [bytes(12)] * 2, 2, depth=16)), ("interlaced", ps.encode_png(2, 2, [bytes(6)] * 2, 2, interlace=1)),
                           ("too many pixels", huge), ("colour type 5", odd)):
            with self.assertRaises(pngread.PngError, msg=name) as caught:
                pngread.Image(data)
            self.assertTrue(caught.exception.unsupported, name)

    def test_a_file_above_the_byte_limit_is_refused_before_it_is_read(self):
        with self.assertRaises(pngread.PngError) as caught:
            pngread.Image(ps.SIGNATURE + b"\x00" * pngread.MAX_BYTES)
        self.assertTrue(caught.exception.unsupported)

    def test_reading_outside_the_image_is_an_index_error(self):
        image = pngread.Image(ps.encode_png(3, 2, [bytes(9)] * 2))
        for x, y in ((3, 0), (0, 2), (-1, 0), (0, -1)):
            with self.assertRaises(IndexError):
                image.pixel(x, y)


class RealPictureTests(unittest.TestCase):
    """A picture made by the real browser: filters of every kind in it, read by the reader and by the textbook reader alike."""

    def test_the_fixture_is_what_the_browser_makes_of_the_svg_beside_it(self):
        image = pngread.Image(LOGIN_PNG)
        self.assertEqual((image.width, image.height, image.color), (1280, 828, 2))
        used = {image._filter(y) for y in range(image.height)}
        self.assertTrue({1, 2, 4} <= used, f"the picture should exercise Sub, Up and Paeth rows, it has {sorted(used)}")
        self.assertIn('viewBox="0 0 1280 828"', LOGIN_SVG)

    def test_the_reader_agrees_with_the_textbook_reader_on_every_row(self):
        image = pngread.Image(LOGIN_PNG)
        naive = ps.decode_png_naive(LOGIN_PNG)
        self.assertEqual(len(naive), 828)
        self.assertEqual([y for y in range(image.height) if image.row(y) != naive[y]], [])

    def test_the_probes_of_the_svg_all_hit_the_real_picture(self):
        probes = png.probes_for(LOGIN_SVG)
        self.assertEqual(probes.problem, "")
        report = png.probe_report(LOGIN_PNG, probes.probes, probes.size)
        self.assertEqual((report.ok, report.misses, report.error), (True, [], ""))
        self.assertEqual(report.hits, len(probes.probes))
        self.assertTrue(png.probe(LOGIN_PNG, probes.probes))
        self.assertEqual({p.kind for p in probes.probes}, {"band", "background", "box"})

    def test_the_page_background_is_the_pinned_colour_and_is_read_from_the_svg(self):
        probes = png.probes_for(LOGIN_SVG)
        self.assertEqual({p.rgb for p in probes.probes if p.kind == "background"}, {png.BACKGROUND})
        self.assertEqual(png.BACKGROUND, (0xF6, 0xF7, 0xF9))
        self.assertEqual({p.rgb for p in probes.probes if p.kind == "band"}, {(0xE9, 0xEB, 0xEE)})


class NegativeControlTests(unittest.TestCase):
    """The picture the browser draws for a wrong URL (a real one, 1280x828, exit code 0) must be rejected, and so must every flat page."""

    def setUp(self):
        self.probes = png.probes_for(LOGIN_SVG)

    def test_the_not_found_page_has_the_size_of_the_screen_so_only_its_pixels_can_reject_it(self):
        self.assertEqual(pngread.size_of(NOT_FOUND), self.probes.size)
        self.assertGreater(len(NOT_FOUND), len(LOGIN_PNG), "a full-size picture full of text, bigger than the screen's own rendering")
        report = png.probe_report(NOT_FOUND, self.probes.probes, self.probes.size)
        self.assertEqual(report.error, "")
        self.assertFalse(report.ok)
        self.assertFalse(png.probe(NOT_FOUND, self.probes.probes))

    def test_the_trap_is_real_the_background_of_the_not_found_page_is_within_the_tolerance_of_the_page_colour(self):
        image = pngread.Image(NOT_FOUND)
        sample = image.pixel(700, 600)
        self.assertTrue(all(abs(a - b) <= png.TOLERANCE for a, b in zip(sample[:3], png.BACKGROUND)), sample)
        background = [p for p in self.probes.probes if p.kind == "background"]
        self.assertTrue(background)
        for p in background:
            got = image.pixel(p.x, p.y)
            self.assertTrue(all(abs(a - b) <= png.TOLERANCE for a, b in zip(got[:3], p.rgb)), (p, got))
        self.assertFalse(png.probe(NOT_FOUND, background), "the points of the page colour alone all hit, so the set is refused before a pixel is read")
        misses = {p.kind for p, _ in png.probe_report(NOT_FOUND, self.probes.probes).misses}
        self.assertIn("band", misses)

    def test_a_set_that_one_flat_colour_could_pass_proves_nothing(self):
        flat = ps.encode_png(1280, 828, [bytes(png.BACKGROUND) * 1280] * 828)
        alone = [p for p in self.probes.probes if p.kind == "background"]
        self.assertEqual(png.probe_report(flat, alone).error, "probe: the probe set proves nothing")
        self.assertFalse(png.probe(flat, alone))
        self.assertFalse(png.probe(flat, []))
        close = [png.Probe(0, 0, (100, 100, 100), "band"), png.Probe(1, 0, (106, 100, 100), "box")]   # a flat page of 103 would pass both within 3
        self.assertFalse(png.probe(ps.encode_png(2, 1, [bytes([103, 100, 100, 103, 100, 100])], 2), close))
        apart = [png.Probe(0, 0, (100, 100, 100), "band"), png.Probe(1, 0, (107, 100, 100), "box")]
        self.assertTrue(png.probe(ps.encode_png(2, 1, [bytes([100, 100, 100, 107, 100, 100])], 2), apart))
        self.assertFalse(png.probe(ps.encode_png(2, 1, [bytes([103, 100, 100, 103, 100, 100])], 2), apart))

    def test_a_flat_page_of_any_colour_of_the_screen_fails(self):
        for colour in ((255, 255, 255), png.BACKGROUND, (0xE9, 0xEB, 0xEE), (0x1F, 0x23, 0x28), (0xF6, 0xF6, 0xF6), (0, 0, 0)):
            flat = ps.encode_png(1280, 828, [bytes(colour) * 1280] * 828)
            self.assertFalse(png.probe(flat, self.probes.probes), colour)
            self.assertFalse(png.probe_report(flat, self.probes.probes, self.probes.size).ok, colour)

    def test_the_right_drawing_one_pixel_too_narrow_is_refused_by_the_size_check(self):
        picture = ps.paint(LOGIN_SVG)
        self.assertTrue(png.probe(picture, self.probes.probes))
        report = png.probe_report(ps.chop_width(picture), self.probes.probes, self.probes.size)
        self.assertEqual(report.error, "size: expected 1280x828, got 1279x828")

    def test_one_wrong_pixel_in_the_whole_set_is_enough_to_refuse_the_picture(self):
        picture = ps.paint(LOGIN_SVG)
        for index, p in enumerate(self.probes.probes):
            wrong = list(self.probes.probes)
            wrong[index] = replace(p, rgb=(p.rgb[0], p.rgb[1], p.rgb[2] + 4 if p.rgb[2] < 252 else p.rgb[2] - 4))
            report = png.probe_report(picture, wrong)
            self.assertFalse(report.ok, (index, p))
            self.assertEqual([m[0] for m in report.misses], [wrong[index]])

    def test_the_tolerance_is_three_per_channel_and_the_picture_must_be_opaque(self):
        picture = ps.encode_png(2, 1, [bytes([100, 100, 100, 200, 200, 200])], 2)    # two colours 100 apart, so the set is a real one
        for delta, hits in ((0, True), (3, True), (-3, True), (4, False), (-4, False)):
            probes = [png.Probe(0, 0, (100 + delta, 100, 100), "band"), png.Probe(1, 0, (200, 200, 200), "box")]
            self.assertEqual(png.probe(picture, probes), hits, delta)
        pair = [png.Probe(0, 0, (100, 100, 100), "band"), png.Probe(1, 0, (200, 200, 200), "box")]
        self.assertTrue(png.probe(ps.encode_png(2, 1, [bytes([100, 100, 100, 255, 200, 200, 200, 255])], 6), pair))
        self.assertFalse(png.probe(ps.encode_png(2, 1, [bytes([100, 100, 100, 255, 200, 200, 200, 254])], 6), pair), "alpha 254 is not opaque")

    def test_a_point_outside_the_picture_is_a_miss_and_an_unreadable_file_is_false_not_an_exception(self):
        picture = ps.encode_png(2, 1, [bytes([100, 100, 100, 200, 200, 200])], 2)
        self.assertFalse(png.probe(picture, [png.Probe(0, 0, (100, 100, 100), "band"), png.Probe(5, 0, (200, 200, 200), "box")]))
        self.assertFalse(png.probe(b"", self.probes.probes))
        self.assertFalse(png.probe(NOT_FOUND[:-30], self.probes.probes))
        report = png.probe_report(ps.encode_png(2, 2, [bytes(6)] * 2, 2, interlace=1), self.probes.probes)
        self.assertTrue(report.unsupported)


class SelectionTests(unittest.TestCase):
    """Which points the SVG gives: read from its own geometry and style, never a point another shape can touch."""

    def test_the_band_gives_two_points_the_page_up_to_four_and_the_widgets_up_to_eight(self):
        groups = [ps.box(f"w{i}", 40 + (i % 5) * 70, 30 + (i // 5) * 60, 60, 40, n=i + 1) for i in range(20)]
        probes = png.probes_for(ps.mini_svg(500, 400, groups))
        self.assertEqual(probes.problem, "")
        kinds = [p.kind for p in probes.probes]
        self.assertEqual((kinds.count("band"), kinds.count("background"), kinds.count("box")), (2, 4, 8))
        self.assertEqual({(p.x, p.y) for p in probes.probes if p.kind == "band"}, {(3, 24), (496, 24)})

    def test_a_screen_with_few_boxes_gives_fewer_but_always_the_band_and_the_page(self):
        probes = png.probes_for(ps.mini_svg(300, 200, [ps.box("only", 100, 80, 80, 30)]))
        self.assertEqual([p.kind for p in probes.probes].count("box"), 1)
        none = png.probes_for(ps.mini_svg(300, 200, []))
        self.assertEqual(none.problem, "")
        self.assertEqual(sorted({p.kind for p in none.probes}), ["background", "band"])

    def test_a_corner_that_a_later_shape_paints_over_is_not_probed(self):
        under = ps.box("under", 100, 100, 100, 60, n=1)
        over = ps.box("over", 90, 90, 40, 30, cls="k", n=2)
        probes = png.probes_for(ps.mini_svg(400, 300, [under, over]))
        self.assertEqual({p.ref for p in probes.probes if p.kind == "box"}, {"S.over"})
        first = png.probes_for(ps.mini_svg(400, 300, [over, under]))
        self.assertEqual({p.ref for p in first.probes if p.kind == "box"}, {"S.over", "S.under"}, "painted before the other, a box is free at its own corner")

    def test_a_widget_with_more_than_its_one_rectangle_or_a_path_or_a_derived_or_empty_fill_is_not_probed(self):
        inner = ps.box("inner", 20, 20, 120, 60, extra='<rect x="30" y="30" width="20" height="20" class="m"/>')
        path = ps.box("path", 20, 100, 120, 60, extra='<path d="M20 100L140 160" class="ln"/>')
        derived = ps.box("derived", 160, 20, 120, 60).replace('class="b"/>', 'class="b" data-derived="true"/>')
        clear = ps.box("clear", 160, 100, 120, 60, cls="z")
        plain = ps.box("plain", 300, 20, 120, 60, cls="s", kind="button")
        probes = png.probes_for(ps.mini_svg(460, 200, [inner, path, derived, clear, plain]))
        self.assertEqual({p.ref for p in probes.probes if p.kind == "box"}, {"S.plain"})

    def test_a_box_too_small_for_its_margin_is_not_probed_and_a_round_corner_moves_the_point_in(self):
        probes = png.probes_for(ps.mini_svg(300, 200, [ps.box("thin", 20, 20, 7, 40), ps.box("pill", 100, 100, 100, 40, rx=20)]))
        boxes = {p.ref: p for p in probes.probes if p.kind == "box"}
        self.assertNotIn("S.thin", boxes)
        self.assertGreaterEqual(boxes["S.pill"].x - 100, 8)
        self.assertEqual(boxes["S.pill"].y - 28 - 100, boxes["S.pill"].x - 100)

    def test_a_header_across_the_top_takes_the_top_corners_away_from_the_page_points(self):
        header = ps.box("head", 0, 0, 400, 50, cls="f", rx=0, kind="header")
        probes = png.probes_for(ps.mini_svg(400, 300, [header]))
        pages = [p for p in probes.probes if p.kind == "background"]
        self.assertTrue(pages)
        for p in pages:
            self.assertGreaterEqual(p.y - 28, 50 + png.MARGIN, p)

    def test_a_page_with_no_free_point_still_has_the_band_and_its_boxes(self):
        full = ps.box("full", 0, 0, 400, 300, cls="b", rx=0, kind="stack")
        inner = [ps.box(f"w{i}", 20 + 90 * i, 100, 80, 40, n=i + 2, cls="k") for i in range(4)]
        probes = png.probes_for(ps.mini_svg(400, 300, [full, *inner]))
        self.assertEqual(probes.problem, "")
        self.assertNotIn("background", {p.kind for p in probes.probes})
        self.assertGreaterEqual(len(probes.probes), png.MIN_PROBES)

    def test_leaves_come_before_containers(self):
        container = ps.box("form", 20, 20, 360, 200, cls="b", kind="form", n=1)
        leaves = [ps.box(f"f{i}", 40, 40 + i * 50, 200, 40, n=i + 2, parent="S.form") for i in range(3)]
        sparse = png.probes_for(ps.mini_svg(400, 260, [container, *leaves]))
        self.assertEqual({p.ref for p in sparse.probes if p.kind == "box"}, {"S.form", "S.f0", "S.f1", "S.f2"})
        crowded = [ps.box(f"g{i}", 40 + (i % 4) * 80, 40 + (i // 4) * 60, 70, 40, n=i + 2, parent="S.form") for i in range(8)]
        many = png.probes_for(ps.mini_svg(400, 260, [container, *crowded]))
        self.assertEqual({p.ref for p in many.probes if p.kind == "box"}, {f"S.g{i}" for i in range(8)}, "eight leaves fill the quota, the container is not needed")

    def test_the_colours_come_from_the_style_of_the_svg_not_from_a_constant(self):
        svg = ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30, cls="k")]).replace(".k{fill:#1f2328}", ".k{fill:#102030}").replace(".p{fill:#f6f7f9}", ".p{fill:#abc}")
        probes = png.probes_for(svg)
        self.assertEqual({p.rgb for p in probes.probes if p.kind == "box"}, {(0x10, 0x20, 0x30)})
        self.assertEqual({p.rgb for p in probes.probes if p.kind == "background"}, {(0xAA, 0xBB, 0xCC)})
        self.assertTrue(png.probe(ps.paint(svg), probes.probes))

    def test_a_later_rule_of_the_style_wins_and_a_class_without_a_hex_fill_paints_nothing(self):
        svg = ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30, cls="b")]).replace("</style>", ".b{fill:#445566}\n</style>")
        self.assertEqual({p.rgb for p in png.probes_for(svg).probes if p.kind == "box"}, {(0x44, 0x55, 0x66)})
        none = ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30, cls="b")]).replace("</style>", ".b{fill:none}\n</style>")
        self.assertEqual([p for p in png.probes_for(none).probes if p.kind == "box"], [])

    def test_a_set_that_a_flat_colour_could_pass_is_not_made(self):
        same = ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30, cls="p")]).replace(".f{fill:#e9ebee", ".f{fill:#f6f7f9")
        self.assertEqual(png.probes_for(same).problem, "probe: the SVG gives too few distinct probe points")
        self.assertEqual(png.probes_for(same).probes, ())

    def test_what_cannot_be_read_is_a_problem_text_and_no_probes(self):
        good = ps.mini_svg(300, 200, [ps.box("a", 100, 80, 80, 30)])
        cases = {
            "not xml": ("<svg", "svg: not readable"),
            "not svg": (good.replace("<svg ", "<other ", 1).replace("</svg>", "</other>"), "svg: the root element is not svg"),
            "sizes disagree": (good.replace('width="300" height="228"', 'width="301" height="228"', 1), "svg: viewBox, width and height disagree"),
            "no band": (good.replace('data-kind="state-label"', 'data-kind="other"'), "svg: no state band or no content group"),
            "no content": (good.replace('data-kind="screen"', 'data-kind="other"'), "svg: no state band or no content group"),
            "band without a fill": (good.replace('class="f"/>', 'class="z"/>', 1), "svg: the band or the page has no plain fill"),
            "page without a fill": (good.replace('class="p"/>', 'class="z"/>', 1), "svg: the band or the page has no plain fill"),
            "geometry": (good.replace('width="80" height="30" rx="4"', 'width="8.5" height="30" rx="4"', 1), "svg: geometry not understood"),
            "too big": (ps.mini_svg(1280, 20000, [ps.box("a", 100, 80, 80, 30)]), "size: the picture would have more pixels than the probe reader accepts"),
        }
        bomb = ('<?xml version="1.0"?>\n<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>\n' + good.replace("<desc>synthetic</desc>", "<desc>&b;</desc>"))
        cases["document type declaration"] = (bomb, "svg: not readable")
        cases["entity without a declaration line"] = (good.replace("<desc>synthetic</desc>", '<!ENTITY x "y"><desc>x</desc>'), "svg: not readable")
        for name, (text, problem) in cases.items():
            got = png.probes_for(text)
            self.assertEqual((got.problem, got.probes), (problem, ()), name)
        self.assertEqual(png.probes_for(good.encode("utf-8")).problem, "")

    def test_the_probes_of_every_default_screen_of_the_sample_hit_its_stand_in_picture_and_each_one_matters(self):
        count = 0
        for path, data in sample_defaults():
            probes = png.probes_for(data)
            picture = ps.paint(data.decode("utf-8"))
            self.assertEqual(probes.problem, "", path)
            self.assertTrue(png.probe(picture, probes.probes), path)
            self.assertGreaterEqual(len(probes.probes), png.MIN_PROBES, path)
            self.assertEqual({p.rgb for p in probes.probes if p.kind == "background"} - {png.BACKGROUND}, set(), path)
            for index, p in enumerate(probes.probes):
                moved = list(probes.probes)
                moved[index] = replace(p, rgb=tuple((c + 40) % 256 for c in p.rgb))
                self.assertFalse(png.probe(picture, moved), (path, p))
            count += 1
        self.assertEqual(count, 12)

    def test_no_probe_of_the_sample_lies_under_a_path_or_a_circle_of_its_svg(self):
        for path, data in sample_defaults():
            circles, paths, band = [], [], 0
            for g in ET.fromstring(data).iter(SVG + "g"):
                box = tuple(int(v) for v in g.get("data-box", "0 0 0 0").split())
                if g.get("data-kind") == "state-label":
                    band = box[3]
                for el in g:
                    if el.tag == SVG + "circle":
                        r = int(el.get("r"))
                        circles.append((int(el.get("cx")) - r, int(el.get("cy")) - r, 2 * r, 2 * r))
                    elif el.tag == SVG + "path":
                        paths.append(box)
            for p in png.probes_for(data).probes:
                if p.kind == "band":
                    continue
                for x0, y0, w, h in circles + paths:
                    self.assertFalse(x0 - 1 <= p.x < x0 + w + 1 and y0 - 1 <= p.y - band < y0 + h + 1, (path, p))

    def test_probes_do_not_depend_on_the_hash_seed(self):
        code = ("import hashlib\nfrom tests.atlas import screens_fixtures as fx\nfrom gemarc_mcp.atlas.protoui import png\nout = []\n"
                "for p, d in sorted(fx.built('sample12').files.items()):\n"
                "    if p.startswith('screens/') and p.endswith('.svg'):\n        out.append(repr(png.probes_for(d).probes))\n"
                "print(hashlib.sha256('\\n'.join(out).encode()).hexdigest())\n")
        digests = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8", PYTHONPATH=str(REPO / "src") + os.pathsep + str(REPO))
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=str(REPO), timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            digests.add(out.stdout.strip())
        self.assertEqual(len(digests), 1, digests)
        self.assertEqual(len(next(iter(digests))), 64)


class ScaleTests(unittest.TestCase):
    def test_the_long_edge_is_capped_at_1600_but_the_scale_never_goes_below_one_half(self):
        self.assertEqual(png.scale_for(1280, 828), 1.0)
        self.assertEqual(png.scale_for(1600, 1600), 1.0)
        self.assertEqual(png.scale_for(1601, 100), 0.999)
        self.assertEqual(png.scale_for(1280, 2400), 0.666)
        self.assertEqual(png.scale_for(1920, 1080), 0.833)
        self.assertEqual(png.scale_for(390, 9000), 0.5)
        self.assertEqual(png.scale_for(390, 5028), 0.5)
        self.assertEqual(png.scale_for(1000, 3000, max_edge=800), 0.5)
        self.assertEqual(png.expected_size(1280, 2400, 0.666), (852, 1598))
        self.assertEqual(png.expected_size(390, 5028, 0.5), (195, 2514))
        self.assertEqual(png.expected_size(1, 1, 0.5), (1, 1))

    def test_probes_at_every_scale_hit_the_stand_in_picture_of_that_scale(self):
        for width, canvas in ((1280, 2372), (390, 5000), (1920, 1052), (1000, 3305), (1280, 800), (1600, 1572)):
            groups = [ps.box("a", 24, 24, 200, 40, n=1, mark=True), ps.box("b", 24, 80, 200, 40, cls="k", n=2, kind="button"),
                      ps.box("c", 24, canvas - 80, 200, 40, n=3), ps.box("d", 150, canvas // 2, 200, 80, cls="s", n=4, kind="button")]
            svg = ps.mini_svg(width, canvas, groups)
            scale = png.scale_for(width, canvas + 28)
            probes = png.probes_for(svg, scale)
            self.assertEqual(probes.problem, "", (width, canvas))
            self.assertEqual(probes.size, png.expected_size(width, canvas + 28, scale))
            picture = ps.paint(svg, scale)
            self.assertEqual(pngread.size_of(picture), probes.size)
            report = png.probe_report(picture, probes.probes, probes.size)
            self.assertTrue(report.ok, (width, canvas, scale, report.misses[:2]))
            self.assertTrue(all(0 <= p.x < probes.size[0] and 0 <= p.y < probes.size[1] for p in probes.probes))
            self.assertEqual([p.kind for p in probes.probes].count("box"), 4, (width, canvas))

    def test_the_command_line_of_a_rendering(self):
        shot = Path("D:/scratch dir/job-0001/shot.png")
        svg = FIX / "SCR-LOGIN.svg"
        args = png.screenshot_args("C:/Edge/msedge.exe", svg, shot, 1280, 828, 1.0)
        self.assertEqual(args[0], "C:/Edge/msedge.exe")
        self.assertEqual(args[1:5], ["--headless=new", "--hide-scrollbars", "--window-size=1280,828", "--force-device-scale-factor=1"])
        self.assertEqual(args[5], f"--screenshot={shot}")
        self.assertEqual(args[6], svg.resolve().as_uri())
        self.assertTrue(args[6].startswith("file:///"))
        self.assertEqual(len(args), 7)
        self.assertFalse([a for a in args if a.startswith(("--user-data-dir", "--disable-gpu"))], "those belong to the launcher")
        scaled = png.screenshot_args("b", svg, shot, 1280, 2400, 0.666)
        self.assertIn("--window-size=1280,2400", scaled)
        self.assertIn("--force-device-scale-factor=0.666", scaled)
        self.assertIn("--force-device-scale-factor=0.5", png.screenshot_args("b", svg, shot, 390, 9000, 0.5))

    def test_a_path_with_a_space_or_a_non_ascii_name_becomes_a_percent_encoded_file_url(self):
        url = png.screenshot_args("b", Path("D:/out dir/\u8f93\u51fa/SCR-A.svg"), Path("x.png"), 10, 10, 1.0)[-1]
        self.assertTrue(url.startswith("file:///"), url)
        self.assertNotIn(" ", url)
        self.assertNotIn("\\", url)
        self.assertIn("%20", url)
        self.assertIn("%E8%BE%93%E5%87%BA", url)


class KeyTests(unittest.TestCase):
    def test_the_key_is_the_sha8_of_the_text_and_the_same_for_text_and_bytes(self):
        text = fx.built("login").files["screens/SCR-LOGIN.svg"].decode("utf-8")
        self.assertEqual(png.svg_key(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:8])
        self.assertEqual(png.svg_key(text), png.svg_key(text.encode("utf-8")))
        self.assertRegex(png.svg_key(text), r"^[0-9a-f]{8}$")

    def test_the_model_stamp_does_not_change_the_key_but_any_other_change_does(self):
        five = FiveIndex(nodes={"usr.member": "Actor", "svc.loan": "Module"}, edges=set(), requirements={"RQ-1"}, cases={"CS-BORROW-OK": {"R-BORROW"}}, machines={})
        model = fx.login_model()
        plain = bld.build_screens(model, None, bld.ScreensConfig(lang="zh-CN", project="demo")).files["screens/SCR-LOGIN.svg"]
        stamped = bld.build_screens(model, five, bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="abcdef12")).files["screens/SCR-LOGIN.svg"]
        again = bld.build_screens(model, five, bld.ScreensConfig(lang="zh-CN", project="demo", model_sha8="01234567")).files["screens/SCR-LOGIN.svg"]
        self.assertIn(b'data-model-sha8="abcdef12"', stamped)
        self.assertNotIn(b"data-model-sha8", plain)
        self.assertNotEqual(plain, stamped)
        self.assertEqual(png.svg_key(plain), png.svg_key(stamped))
        self.assertEqual(png.svg_key(stamped), png.svg_key(again))
        changed = stamped.replace(b'width="88"', b'width="89"', 1)
        self.assertNotEqual(changed, stamped)
        self.assertNotEqual(png.svg_key(changed), png.svg_key(stamped))
        self.assertNotEqual(png.svg_key(stamped.replace(b'data-ui-sha8="', b'data-ui-sha8="0', 1)), png.svg_key(stamped))

    def test_only_the_attribute_in_the_root_tag_is_dropped(self):
        svg = ps.mini_svg(100, 50, [], model_sha8="abcdef12")
        bare = ps.mini_svg(100, 50, [])
        self.assertEqual(png.svg_key(svg), png.svg_key(bare))
        hidden = bare.replace("<desc>synthetic</desc>", '<desc>x data-model-sha8="abcdef12" y</desc>')
        self.assertNotEqual(png.svg_key(hidden), png.svg_key(bare), "text further down the file is part of the drawing")
        self.assertNotEqual(png.svg_key(svg.replace("abcdef12", "ABCDEF12")), png.svg_key(bare), "only a lower-case stamp of eight hex digits is the model stamp")


class SourceTests(unittest.TestCase):
    def test_png_py_and_the_reader_never_start_a_process_or_touch_the_network(self):
        for name in ("png.py", "pngread.py"):
            text = (SRC / name).read_text(encoding="utf-8")
            code = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))
            for word in ("subprocess", "Popen", "os.system", "os.exec", "os.spawn", "socket", "urllib", "http.", "import mcp", "gate.smoke", "import smoke", "from ..gate"):
                self.assertNotIn(word, code, (name, word))

    def test_every_import_is_standard_library_or_a_neighbour_in_the_package(self):
        for name in ("png.py", "pngread.py"):
            for module in re.findall(r"^(?:from|import) ([A-Za-z_.]+)", (SRC / name).read_text(encoding="utf-8"), re.M):
                self.assertTrue(module.startswith(".") or module.split(".")[0] in sys.stdlib_module_names, (name, module))

    def test_the_check_rule_is_registered_and_is_not_run_by_the_build(self):
        from gemarc_mcp.atlas.protoui import check as chk
        self.assertEqual(chk.RULES["SV-208"], "I")
        self.assertNotIn("SV-208", {f.rule for f in bld.build_screens(fx.login_model(), None, fx.CFG).findings})


if __name__ == "__main__":
    unittest.main()
