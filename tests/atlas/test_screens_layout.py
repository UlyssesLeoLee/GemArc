"""The layout L1.1 and the text utilities of the screen builders (``protoui/layout.py``, ``textfit.py``): the width model, wrapping, the boxes of
pinned screens, the five corrections of design D-13, the state rendering, and the invariants every layout must keep (a box inside its parent,
siblings that do not overlap, nothing outside the canvas, interactive boxes of the minimum size) over the fixtures and 40 random models.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_screens_layout.py"
"""
from __future__ import annotations

import unittest

from gemarc_mcp.atlas.protoui import atl, expand as ex, layout as lay, model as um, textfit as tf, vocab

from . import screens_fixtures as fx
from . import ui_synth


def layouts(model: dict, rows: int = 5):
    index = lay.ModelIndex(model)
    for s in model["screens"]:
        for v in s["variantNames"]:
            yield s, v, lay.layout_state(index, s["id"], v, rows)


def random_models(count: int):
    for seed in range(1, count + 1):
        spec = ui_synth.generate(screens=2 + seed % 9, seed=seed, lang="zh" if seed % 2 else "en")
        yield seed, ex.expand_ui([atl.parse_atl(t, n) for n, t in sorted(ui_synth.render(spec, per_slice=1 + seed % 4).items())]).model


class TextFitTests(unittest.TestCase):
    def test_the_width_model_is_107_for_wide_characters_and_59_for_the_rest(self):
        self.assertEqual((tf.CJK_UNITS, tf.OTHER_UNITS), (107, 59))
        for text, em, want in (("订单", 14, 30), ("Hello, world", 14, 100), ("订单 128 比昨天", 14, 117), ("…", 14, 15), ("标题", 20, 43), ("", 14, 0), ("a", 12, 8)):
            self.assertEqual(tf.width(text, em), want, (text, em))
        for ch in "　鿿가힯＀￯…":
            self.assertTrue(tf.is_wide(ch), hex(ord(ch)))
        for ch in "aZ09 -é—　".replace("　", ""):
            self.assertFalse(tf.is_wide(ch), hex(ord(ch)))

    def test_wrap_keeps_every_line_inside_the_width_and_loses_only_blanks(self):
        text = "This paragraph is long enough to need several lines on a narrow phone screen"
        for room in (60, 120, 200, 350):
            lines = tf.wrap(text, room, 14)
            self.assertTrue(all(tf.width(x) <= room for x in lines), (room, lines))
            self.assertEqual(tf.squash("".join(lines)), tf.squash(text))
        self.assertEqual(tf.wrap(text, 200, 14), ["This paragraph is long", "enough to need several", "lines on a narrow phone", "screen"])
        cjk = "订单号一二三四五六七八九十"
        self.assertEqual(tf.wrap(cjk, 60, 14), ["订单号一", "二三四五", "六七八九", "十"])
        self.assertEqual(tf.wrap("", 100), [""])
        self.assertEqual(tf.wrap("ab", 1, 14), ["a", "b"], "one character per line at least")

    def test_clip_ends_in_an_ellipsis_and_fits(self):
        self.assertEqual(tf.clip("订单号一二三四五六七八九十", 60, 14), "订单号…")
        self.assertEqual(tf.clip("short", 100, 14), "short")
        for room in (30, 60, 90, 300):
            out = tf.clip("The quick brown fox jumps over the lazy dog", room, 14)
            self.assertLessEqual(tf.width(out), room)

    def test_clean_makes_legal_one_line_text_and_author_limits_the_length(self):
        self.assertEqual(tf.clean("a\tb\r\nc"), "a b  c")
        self.assertEqual(tf.clean("x\x00y\x1fz￾"), "x�y�z�")
        self.assertEqual(tf.clean("lone \ud800 surrogate"), "lone � surrogate")
        self.assertEqual(len(tf.author("x" * 450)), tf.MAX_CHARS)
        self.assertTrue(tf.author("x" * 450).endswith(tf.ELLIPSIS))
        self.assertEqual(tf.author("x" * 400), "x" * 400)
        self.assertEqual(tf.esc_attr('a<b>&"c'), "a&lt;b&gt;&amp;&quot;c")

    def test_markdown_safety_and_the_frame_placeholders(self):
        self.assertEqual(tf.md_safe("a|b`c**d"), "a¦bˋc∗∗d")
        self.assertEqual(tf.md_safe("[a](b)"), "［a］(b)")
        self.assertEqual(tf.md_safe("[a] and (b)"), "[a] and (b)", "brackets change only inside a link pattern")
        self.assertEqual(tf.defuse("{lines} {sha8}"), "{ lines} { sha8}")
        for raw in ("x {lines} y|`z`", "a **b** [c](d)", "plain"):
            self.assertEqual(tf.squash(tf.card_text(raw)), tf.cmp_form(raw), raw)

    def test_fold_and_unfold_round_trip_up_to_blanks(self):
        mark = "(cont.)"
        long = ("word " * 200).strip()
        lines = tf.fold(long, mark)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(x) <= tf.FOLD_AT for x in lines))
        self.assertTrue(all(x.startswith(mark) for x in lines[1:]))
        self.assertEqual(tf.squash("".join(tf.unfold(lines, mark))), tf.squash(long))
        self.assertEqual(tf.fold("short", mark), ["short"])
        hard = "x" * 1200
        self.assertEqual(tf.squash("".join(tf.unfold(tf.fold(hard, mark), mark))), hard)


class PinnedBoxTests(unittest.TestCase):
    """The boxes of ``layoutVersion`` 1: a change here is a change of the algorithm and needs ``LAYOUT_VERSION + 1``."""

    def test_the_layout_version_is_the_one_of_the_model_contract(self):
        self.assertEqual(lay.LAYOUT_VERSION, um.LAYOUT_VERSION)
        self.assertEqual(lay.LAYOUT_VERSION, 1)

    def test_the_login_form_is_centred_640_wide_and_its_children_are_pinned(self):
        index = lay.ModelIndex(fx.login_model())
        st = lay.layout_state(index, "SCR-LOGIN", "default")
        got = {p.wid.split(".")[1]: (p.box, p.n) for p in st.placed}
        self.assertEqual(got, {"form": ((320, 24, 640, 172), 1), "email": ((340, 40, 480, 40), 2), "pwd": ((340, 92, 480, 40), 3), "submit": ((340, 144, 88, 36), 5)})
        self.assertEqual((st.width, st.height, st.band_h), (1280, 800, 28))
        self.assertEqual([wid for wid, why in st.hidden], ["SCR-LOGIN.err"])
        self.assertEqual(lay.layout_state(index, "SCR-LOGIN", "error").shown, 5, "a widget that says in=error shows in that state and the layout flows again")

    def test_align_end_and_center_and_the_wrapping_row_on_a_phone(self):
        index = lay.ModelIndex(fx.showcase_model())
        align = {p.wid.split(".")[1]: p.box for p in lay.layout_state(index, "SCR-ALIGN", "default").placed}
        self.assertEqual(align["note"][0] + align["note"][2], 1280 - 24, "the screen says align=end")
        self.assertEqual(align["act"][0] + align["act"][2], 1280 - 24)
        self.assertAlmostEqual(align["mid"][0] + align["mid"][2] / 2, 1280 / 2, delta=1)
        phone = {p.wid.split(".")[1]: p.box for p in lay.layout_state(index, "SCR-PHONE", "default").placed}
        self.assertEqual(phone["b1"][1], phone["b2"][1], "three buttons share a line")
        self.assertGreater(phone["b4"][1], phone["b1"][1], "the fourth button wraps onto a new line instead of leaving the row")
        self.assertTrue(all(phone[k][2] >= 44 and phone[k][3] >= 44 for k in ("b1", "b2", "b3", "b4", "back", "nm", "go")), "44x44 targets on a phone")
        self.assertEqual(phone["hd"][3], 28 * 3 + 4, "the heading wraps onto three lines")

    def test_a_form_under_the_root_is_centred_on_a_tablet_and_a_card_with_fill_takes_the_width(self):
        boxes = {p.wid.split(".")[1]: p.box for p in lay.layout_state(lay.ModelIndex(fx.showcase_model()), "SCR-FORMX", "default").placed}
        self.assertEqual((boxes["form"][0], boxes["form"][2]), (97, 640))
        self.assertEqual((boxes["side"][0], boxes["side"][2]), (24, 834 - 48), "w=fill: no centring, no 640 limit")

    def test_sample_rows_and_the_state_bodies(self):
        index = lay.ModelIndex(fx.showcase_model())
        for rows, drawn_height, more in ((5, 264, "+2"), (3, 184, "+4"), (0, 64, "+7")):
            st = lay.layout_state(index, "SCR-DASH", "default", rows)
            self.assertEqual(next(p.box[3] for p in st.placed if p.wid.endswith(".recent")), drawn_height, rows)
            self.assertTrue(any(s.startswith(more) and role == "more" and derived for s, derived, _w, role in st.texts), rows)
        loading = lay.layout_state(index, "SCR-DASH", "loading")
        self.assertIn(("skeleton", "SCR-DASH.recent", ""), loading.derived)
        self.assertIn(("skeleton", "SCR-DASH.stats", ""), loading.derived)
        empty = lay.layout_state(index, "SCR-DASH", "empty")
        self.assertEqual([s for s, _d, w, role in empty.texts if role == "state" and w.endswith(".recent")], ["没有记录"])
        self.assertEqual(empty.derived, [], "the variant text= is authored, so nothing is derived")
        error = lay.layout_state(index, "SCR-DASH", "error")
        self.assertEqual({kind for kind, _w, _s in error.derived}, {"placeholder"}, "no text= on the error variant: a grey placeholder, marked derived")
        default = lay.layout_state(index, "SCR-DASH", "default")
        self.assertEqual([w for kind, w, _s in default.derived if kind == "bar"], ["SCR-DASH.stats"], "a table without data draws a grey bar and invents no text")

    def test_the_state_band_wraps_to_the_frame_and_the_note_is_at_most_60_characters(self):
        index = lay.ModelIndex(fx.showcase_model())
        phone = lay.layout_state(index, "SCR-PHONE", "loading", lang="zh-CN", ui_sha8="12345678")
        self.assertGreater(len(phone.band_lines), 1)
        self.assertEqual(phone.band_h, lay.BAND_BASE + lay.BAND_LINE * (len(phone.band_lines) + len(phone.note_lines)))
        self.assertTrue(all(tf.width(x, lay.BAND_FONT) <= (390 - 16) * lay.BAND_ROOM_PERCENT // 100 for x in phone.band_lines + phone.note_lines))
        self.assertEqual(tf.squash("".join(phone.band_lines)), tf.squash(phone.band_text))
        desktop = lay.layout_state(index, "SCR-DASH", "default", lang="zh-CN", ui_sha8="12345678")
        self.assertEqual((len(desktop.band_lines), desktop.note_lines, desktop.band_h), (1, (), 28), "the band of a state without note is 28 px")
        self.assertIn("ui 12345678", desktop.band_text)
        self.assertIn("显示 38/38", desktop.band_text)

    def test_nesting_deeper_than_the_layout_recurses_is_named_as_not_shown(self):
        model = fx.model_of(fx.deep_text(60))
        st = lay.layout_state(lay.ModelIndex(model), "SCR-DEEP", "default")
        reasons = {why for _wid, why in st.hidden}
        self.assertEqual(reasons, {"depth", "parent"})
        self.assertEqual(st.shown, lay.MAX_DEPTH + 1)
        self.assertEqual(st.shown + len(st.hidden), st.total)


class InvariantTests(unittest.TestCase):
    """What the I-class checks read back must hold by construction, on every fixture and on random models."""

    def assert_invariants(self, model: dict, label: str, rows: int = 5):
        for s, variant, st in layouts(model, rows):
            where = f"{label} {s['id']}#{variant}"
            boxes = {p.wid: p.box for p in st.placed}
            self.assertEqual([p.n for p in st.placed], sorted(p.n for p in st.placed), where)
            order = {wid: i + 1 for i, wid in enumerate(s["widgetIds"])}
            frame_w, frame_h = vocab.FRAMES[s.get("frame", "desktop")]
            self.assertEqual((st.width, st.height >= frame_h), (frame_w, True), where)
            by_parent: dict = {}
            minimum = lay.min_target(s.get("frame", "desktop"))
            for p in st.placed:
                x, y, w, h = p.box
                self.assertEqual(p.n, order[p.wid], where)
                self.assertTrue(w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= st.width and y + h <= st.height, (where, p.wid, p.box))
                parent = (0, 0, st.width, st.height) if p.parent.endswith(".root") else boxes[p.parent]
                self.assertTrue(x >= parent[0] and y >= parent[1] and x + w <= parent[0] + parent[2] and y + h <= parent[1] + parent[3], (where, p.wid, p.box, parent))
                by_parent.setdefault(p.parent, []).append(p)
                if p.kind in vocab.INTERACTIVE:
                    self.assertTrue(w >= minimum[0] and h >= minimum[1], (where, p.wid, p.box, minimum))
            for parent, kids in by_parent.items():
                for i in range(len(kids)):
                    for j in range(i + 1, len(kids)):
                        a, b = kids[i].box, kids[j].box
                        self.assertFalse(a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3], (where, kids[i].wid, kids[j].wid))
            self.assertEqual(st.shown + len(st.hidden), st.total, where)

    def test_the_three_fixed_models_in_every_sample_row_step(self):
        for name, model in (("login", fx.login_model()), ("sample12", fx.sample12_model()), ("showcase", fx.showcase_model())):
            for rows in (5, 3, 0):
                self.assert_invariants(model, name, rows)

    def test_stress_shapes(self):
        for controls, states, tables in ((30, 5, 0), (40, 4, 4), (60, 5, 2), (100, 3, 3)):
            self.assert_invariants(fx.model_of(fx.stress_text(controls, states, tables)), f"stress {controls}x{states}")
        self.assert_invariants(fx.model_of(fx.hostile_text()), "hostile")
        self.assert_invariants(fx.model_of(fx.deep_text(60)), "deep")

    def test_forty_random_models(self):
        count = states = 0
        for seed, model in random_models(40):
            self.assert_invariants(model, f"seed {seed}")
            count += 1
            states += sum(len(s["variantNames"]) for s in model["screens"])
        self.assertEqual(count, 40)
        self.assertGreater(states, 300, "the random models hold hundreds of states, the invariants are not checked on nothing")

    def test_the_layout_is_deterministic_and_does_not_touch_the_model(self):
        import copy
        model = fx.showcase_model()
        before = copy.deepcopy(model)
        first = [(p.wid, p.box, tuple(p.prims)) for _s, _v, st in layouts(model) for p in st.placed]
        second = [(p.wid, p.box, tuple(p.prims)) for _s, _v, st in layouts(model) for p in st.placed]
        self.assertEqual(first, second)
        self.assertEqual(model, before)


if __name__ == "__main__":
    unittest.main()
