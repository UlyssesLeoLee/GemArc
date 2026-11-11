"""The frozen interface of the reading surface (milestone R0, design A3 sections 2.1-2.6, D-4, D-5, 12.1): page ids and the section sign, ``enc``,
the budgets, the real envelope renderer and the sentinel.

What is pinned here, in the order of the risks:

* ``enc`` is injective on a 5,000-id sample made of authored-looking ids, derived ids, case variants, look-alikes of the escape characters,
  Windows device names, trailing dots, long ids sharing a prefix and non-ASCII ids; ``enc_many`` additionally has no two names equal ignoring case.
* The frame of a page is never larger than the widest frame of its family class: a fuzz over admissible metadata (any text, any length, every
  number up to its width) renders at most ``envelope_size``, and ``envelope_size`` is inside the RS-103 limit for every family class, both
  languages, with and without the continuation lines.
* The sentinel verifies, and every way of damaging a page is found.
* The interface is what the table of design 12.1 says (names and parameter names), pure (no I/O imports) and independent of ``PYTHONHASHSEED``.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_read_api*.py"
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path

from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.reading import api, budgets, envelope as E, names as N

READING = Path(api.__file__).resolve().parent
ALL_FAMILY_CLASSES = [f"{key}:{cls}" for key, spec in N.FAMILIES.items() for cls in spec.classes]
ENC_ALLOWED = re.compile(r"^[A-Za-z0-9._~+-]+$")
ESCAPE_FREE_HEAD = re.compile(r"^(?:[A-Za-z0-9._~-]|\+[0-9a-f]+\+)*$")


# ---------------------------------------------------------------------------------------------------- sample ids

def sample_ids(count: int = 5000, seed: int = 11) -> list:
    rng = random.Random(seed)
    word = lambda: "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(rng.randint(1, 9)))  # noqa: E731
    ids: set = set()
    hand = [
        "Pump", "pump", "PUMP", "pUmP", "SCR-LOGIN", "scr-login", "SCR-LOGIN#error", "SCR-LOGIN.submit", "a.b", "a-b", "a_b", "a+b", "a~b", "a#b",
        "a%2e", "a+2e+", "a+2E+", "a~", "~", "+", "++", "+2b+", "+h+", "x+h+0123456789ab", "CON", "con", "Con", "_CON", "__CON", "_con.txt", "CON.txt",
        "con.txt", "NUL", "nul.log", "AUX", "COM1", "com9", "LPT1", "lpt9", "COM0", "COM10", "CONSOLE", "PRN.", "a.", "a..", ".", "..", "a b", "a\tb", "a\nb",
        "a/b", "a\\b", "a:b", "a?b", "a*b", "a<b", "a>b", "a|b", 'a"b', "\u7a7a", "a\u7a7a", "\u30c6\u30b9\u30c8", "\uff21\uff22", "A", "\U0001f600", "a\U0001f600",
        "zh6.pallet-lock", "zh6.pallet-lock:publishes:ja5.trace-schedule", "LZ3-ALERT-12", "_", "_a", "_.x", "-", "x-", "0",
        "a" * 80, "a" * 81, "a" * 124, "a" * 79 + "\u7a7a", "b" * 47 + "\u7a7a" + "c" * 60, "b" * 48 + "\u7a7a" + "c" * 60, "d" * 80 + "x", "d" * 80 + "y",
        "e" * 47 + "#" * 40, "e" * 46 + "\u7a7a\u7a7a" + "f" * 30,
    ]
    ids.update(hand)
    for text in list(ids):  # every case variant of the hand-made ids as well
        ids.add(text.upper())
        ids.add(text.lower())
        ids.add(text.swapcase())
    while len(ids) < count:
        kind = rng.random()
        if kind < 0.35:      # authored-looking: letter or digit first, then word characters, dots and dashes
            text = word() + "".join(rng.choice([".", "-", "_", ""]) + word() for _ in range(rng.randint(0, 4)))
        elif kind < 0.55:    # derived ids: edges and events
            text = f"{word()}.{word()}:{rng.choice(['calls', 'sends', 'publishes'])}:{word()}.{word()}" + (f"#{rng.randint(1, 30)}" if rng.random() < 0.3 else "")
        elif kind < 0.70:    # case variants of a random id
            base = word() + "." + word()
            text = "".join(ch.upper() if rng.random() < 0.5 else ch for ch in base)
        elif kind < 0.80:    # characters the encoding must escape, in random places
            text = word() + "".join(rng.choice(["+", "~", "#", " ", "/", "\u7a7a", "\u00e9", "%", "=", "'"]) + word() for _ in range(rng.randint(1, 3)))
        elif kind < 0.88:    # device names and neighbours
            text = rng.choice(["con", "CON", "prn", "Aux", "NUL", "com3", "LPT7", "_con", "_NUL", "__aux"]) + rng.choice(["", ".", ".txt", "x", "-1", "#a", ".a.b"])
        else:                # long ids that share a long prefix
            text = "p" * rng.randint(40, 90) + word() + "-" + word()
        ids.add(text)
    return sorted(ids)


def decode(name: str) -> str:
    """Test-local inverse of ``enc`` for names without the hash form (the function itself is never used to recover ids)."""
    assert not N.is_hashed_name(name)
    if name.startswith("_") and N._is_device(name[1:]):
        name = name[1:]
    out, i = [], 0
    while i < len(name):
        ch = name[i]
        if ch == "~":
            out.append("#")
        elif ch == "+":
            end = name.index("+", i + 1)
            out.append(chr(int(name[i + 1:end], 16)))
            i = end
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class EncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = sample_ids()
        cls.names = {i: N.enc(i) for i in cls.ids}

    def test_the_sample_is_large_and_mixed(self):
        self.assertGreaterEqual(len(self.ids), 5000)
        lowered = {i.lower() for i in self.ids}
        self.assertLess(len(lowered), len(self.ids), "the sample has case variants")
        self.assertTrue(any(i.startswith("_") for i in self.ids) and any(i.endswith(".") for i in self.ids))

    def test_enc_is_injective(self):
        by_name: dict = {}
        for identifier, name in self.names.items():
            self.assertNotIn(name, by_name, f"two ids share the file name {name!r}")
            by_name[name] = identifier
        self.assertEqual(len(by_name), len(self.ids))

    def test_every_name_is_safe_on_windows(self):
        for identifier, name in self.names.items():
            self.assertRegex(name, ENC_ALLOWED, identifier)
            self.assertLessEqual(len(name), N.ENC_MAX, identifier)
            self.assertFalse(name.endswith("."), identifier)
            self.assertNotIn(name.split(".", 1)[0].upper(), N.WINDOWS_DEVICES, identifier)
            self.assertNotIn(name, (".", ".."))

    def test_names_without_the_hash_form_decode_back_to_the_id(self):
        plain = [(i, n) for i, n in self.names.items() if not N.is_hashed_name(n)]
        self.assertGreater(len(plain), 4000)
        for identifier, name in plain:
            self.assertEqual(decode(name), identifier)

    def test_the_hash_form_is_used_exactly_for_long_names_and_is_well_formed(self):
        hashed = [(i, n) for i, n in self.names.items() if N.is_hashed_name(n)]
        self.assertGreater(len(hashed), 100)
        for identifier, name in hashed:
            head, _, tail = name.rpartition("+h+")
            self.assertRegex(tail, r"^[0-9a-f]{12}$")
            self.assertEqual(tail, hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12])
            self.assertRegex(head, ESCAPE_FREE_HEAD, "the 48-character head is cut at an escape boundary")
            self.assertLessEqual(len(head), N.ENC_HASH_PREFIX)
            self.assertGreater(len("".join(N._full_tokens(identifier))), N.ENC_MAX, "only a long encoded name is hashed")
        for identifier, name in self.names.items():
            if not N.is_hashed_name(name):
                self.assertLessEqual(len(name), N.ENC_MAX)
        # a plain name can contain the substring +h+ (an escape, a literal h, an escape) without being a hashed name
        self.assertEqual(N.enc("x+h+0123456789ab"), "x+2b+h+2b+0123456789ab")
        self.assertIn("+h+", N.enc("x+h+0123456789ab"))
        self.assertFalse(N.is_hashed_name(N.enc("x+h+0123456789ab")))
        self.assertTrue(N.is_hashed_name(N.enc("q" * 90)))

    def test_the_design_examples(self):
        self.assertEqual(N.enc("SCR-LOGIN"), "SCR-LOGIN")
        self.assertEqual(N.enc("SCR-LOGIN#error"), "SCR-LOGIN~error")
        self.assertEqual(N.enc("a b"), "a+20+b")
        self.assertEqual(N.enc("a+b~c"), "a+2b+b+7e+c")
        self.assertEqual(N.enc("\u7a7a"), "+7a7a+")
        self.assertEqual(N.enc("CON"), "_CON")
        self.assertEqual(N.enc("nul.log"), "_nul.log")
        self.assertEqual(N.enc("Com3"), "_Com3")
        self.assertEqual(N.enc("COM0"), "COM0")
        self.assertEqual(N.enc("CONSOLE"), "CONSOLE")
        self.assertEqual(N.enc("a."), "a+2e+")

    def test_a_device_prefix_cannot_collide_with_an_id_that_starts_with_an_underscore(self):
        self.assertEqual(N.enc("CON"), "_CON")
        self.assertEqual(N.enc("_CON"), "+5f+CON")
        self.assertNotEqual(N.enc("CON"), N.enc("_CON"))
        self.assertEqual(N.enc("__CON"), "__CON")
        self.assertEqual(N.enc("_con.txt"), "+5f+con.txt")
        self.assertEqual(N.enc("_x"), "_x")

    def test_dots_alone_are_never_a_name(self):
        self.assertEqual(N.enc("."), "+2e+")
        self.assertEqual(N.enc(".."), ".+2e+")

    def test_a_cut_never_lands_inside_an_escape(self):
        # 47 characters, then the six-character escape of one CJK character: it does not fit into the 48-character head, so it is dropped whole
        cut = N.enc("b" * 47 + "\u7a7a" + "c" * 60)
        self.assertTrue(cut.startswith("b" * 47 + "+h+"), cut)
        # 42 + 6 = 48 characters: the escape fits exactly and stays
        fits = N.enc("b" * 42 + "\u7a7a" + "c" * 60)
        self.assertTrue(fits.startswith("b" * 42 + "+7a7a+" + "+h+"), fits)

    def test_a_lone_surrogate_and_odd_input(self):
        self.assertEqual(N.enc("a\ud800b"), "a+d800+b")
        self.assertIn("+h+", N.enc("\ud800" * 30))
        with self.assertRaises(ValueError):
            N.enc("")
        with self.assertRaises(TypeError):
            N.enc(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            N.enc(b"abc")  # type: ignore[arg-type]

    def test_enc_many_has_no_two_names_equal_ignoring_case(self):
        mapping = N.enc_many(self.ids)
        self.assertEqual(set(mapping), set(self.ids))
        lowered = [name.lower() for name in mapping.values()]
        self.assertEqual(len(set(lowered)), len(lowered), "names equal ignoring case")
        self.assertEqual(len(set(mapping.values())), len(mapping), "injective")
        for name in mapping.values():
            self.assertRegex(name, ENC_ALLOWED)
            self.assertLessEqual(len(name), N.ENC_MAX)

    def test_enc_many_hashes_the_colliding_groups_only_and_leaves_the_rest_alone(self):
        mapping = N.enc_many(["Pump", "pump", "PUMP", "Other", "other_one", "SCR-LOGIN"])
        for identifier in ("Pump", "pump", "PUMP"):
            self.assertIn("+h+", mapping[identifier])
        for identifier in ("Other", "other_one", "SCR-LOGIN"):
            self.assertEqual(mapping[identifier], N.enc(identifier))
        self.assertEqual(len({mapping[i].lower() for i in ("Pump", "pump", "PUMP")}), 3)

    def test_enc_many_is_independent_of_the_input_order_and_of_repeats(self):
        shuffled = list(self.ids[:800]) * 2
        random.Random(3).shuffle(shuffled)
        self.assertEqual(N.enc_many(shuffled), N.enc_many(sorted(set(shuffled))))

    def test_enc_many_keeps_an_already_hashed_member_stable(self):
        long_a, long_b = "q" * 90 + "A", "q" * 90 + "a"
        mapping = N.enc_many([long_a, long_b])
        self.assertEqual(mapping[long_a], N.enc(long_a))
        self.assertEqual(mapping[long_b], N.enc(long_b))
        self.assertNotEqual(mapping[long_a].lower(), mapping[long_b].lower())

    def test_enc_many_survives_a_hash_prefix_tie(self):
        real = N._digest
        calls = {"n": 0}

        def tied(identifier, length):  # every id gets the same 12-digit prefix, but a longer hash tells them apart
            calls["n"] += 1
            return "0" * 12 + real(identifier, length)[12:] if length > 12 else "0" * 12

        N._digest = tied
        try:
            mapping = N.enc_many(["Pump", "pump"])
        finally:
            N._digest = real
        self.assertNotEqual(mapping["Pump"].lower(), mapping["pump"].lower())
        self.assertGreater(len(mapping["Pump"]), len("Pump+h+") + 12)


# ---------------------------------------------------------------------------------------------------- page ids

class PageIdTests(unittest.TestCase):
    GOOD = ["START", "SCREENS", "SCREENS-2", "NAV", "NAV-12", "CARD-SCR-LOGIN", "CARD-SCR-LOGIN-2", "CARD-a~b+2e+", "INDEX-2D", "INDEX-QA-3", "INDEX-1D-2",
            "S07", "S123", "D01", "M12", "W01", "R03", "T01", "E15", "C09", "V03", "Q01", "G01", "A01", "P1D", "P2D"]
    BAD = ["", "start", "S7", "S1234", "X01", "S07 ", " S07", "S0A", "INDEX", "INDEX-6D", "INDEX-2D-", "INDEX-2D-x", "P3D", "P1d", "CARD-", "CARD- x", "CARD-a/b",
           "SCREENS-", "NAV--2", "\u00a7S07", "S07.md", "s07", "G1", "STARTS", "A0", "V1000"]

    def test_the_grammar(self):
        for page in self.GOOD:
            self.assertTrue(N.is_page_id(page), page)
            self.assertIsNotNone(N.PAGE_ID_RE.fullmatch(page), page)
        for page in self.BAD:
            self.assertFalse(N.is_page_id(page), page)
        for value in (None, 7, b"S07", ["S07"]):
            self.assertFalse(N.is_page_id(value))

    def test_the_section_sign_form_is_never_a_model_id(self):
        for page in self.GOOD:
            ref = N.page_ref(page)
            self.assertEqual(ref, "\u00a7" + page)
            self.assertIsNotNone(N.PAGE_REF_RE.fullmatch(ref))
            self.assertIsNone(am.ID_RE.fullmatch(ref), "a section-sign page id must never match a model id")
            self.assertIsNone(am.AUTHORED_ID_RE.fullmatch(ref))
            self.assertIsNone(N.PAGE_ID_RE.fullmatch(ref))
        # the bare page ids of the letter families ARE valid model ids: that is why the text always writes the section sign
        self.assertIsNotNone(am.AUTHORED_ID_RE.fullmatch("S07"))
        self.assertNotIn("\u00a7", "".join(chr(c) for c in range(0x30, 0x7b)))

    def test_parse_page_ref_takes_both_forms(self):
        for page in self.GOOD:
            self.assertEqual(N.parse_page_ref("\u00a7" + page), page)
            self.assertEqual(N.parse_page_ref(page), page)
            self.assertEqual(N.parse_page_ref("  \u00a7" + page + " "), page)
        for bad in ("\u00a7\u00a7S07", "\u00a7", "page:S07", "\u00a7S7", None, 5):
            self.assertIsNone(N.parse_page_ref(bad))
        with self.assertRaises(ValueError):
            N.page_ref("S7")

    def test_page_id_builds_two_and_three_digit_numbers(self):
        self.assertEqual(N.page_id("S", 7), "S07")
        self.assertEqual(N.page_id("S", 99), "S99")
        self.assertEqual(N.page_id("S", 100), "S100")
        self.assertEqual(N.page_id("A", 1), "A01")
        for family, number in (("S", 0), ("S", 1000), ("S", True), ("S", "7"), ("P", 1), ("X", 1), ("SS", 1)):
            with self.assertRaises(ValueError):
                N.page_id(family, number)

    def test_parse_page_id_and_the_opaque_card_tail(self):
        self.assertEqual(N.parse_page_id("S07"), N.PageId("S", 7, "2D", None, None))
        self.assertEqual(N.parse_page_id("G01"), N.PageId("G", 1, None, None, None))
        self.assertEqual(N.parse_page_id("W12"), N.PageId("W", 12, "3D", None, None))
        self.assertEqual(N.parse_page_id("INDEX-2D-2"), N.PageId("INDEX", None, "2D", 2, None))
        self.assertEqual(N.parse_page_id("INDEX-QA"), N.PageId("INDEX", None, "QA", None, None))
        self.assertEqual(N.parse_page_id("SCREENS-3"), N.PageId("SCREENS", None, None, 3, None))
        self.assertEqual(N.parse_page_id("P1D"), N.PageId("P", None, "1D", None, None))
        self.assertEqual(N.parse_page_id("START"), N.PageId("START", None, None, None, None))
        # a screen whose id ends in -<digits> is indistinguishable from a continuation card: the tail is never split
        self.assertEqual(N.parse_page_id("CARD-SCR-2"), N.PageId("CARD", None, None, None, "SCR-2"))
        self.assertEqual(N.parse_page_id("CARD-SCR-2-2"), N.PageId("CARD", None, None, None, "SCR-2-2"))
        self.assertEqual(N.card_page_id("SCR-2"), "CARD-SCR-2")
        self.assertEqual(N.card_page_id("SCR-2", 2), "CARD-SCR-2-2")
        self.assertIsNone(N.parse_page_id("nope"))
        with self.assertRaises(ValueError):
            N.card_page_id("a/b")
        with self.assertRaises(ValueError):
            N.card_page_id("a", 0)

    def test_every_generated_page_id_parses(self):
        ids = [N.page_id(f, n) for f in N.LETTER_FAMILIES for n in (1, 9, 10, 99, 100, 999)]
        for page in ids:
            parsed = N.parse_page_id(page)
            self.assertEqual(parsed.family, page[0])
            self.assertEqual(parsed.number, int(page[1:]))

    def test_paths_and_twins(self):
        cases = {
            "START": "START.md", "G01": "read/G01.md", "A01": "read/A01.md", "S07": "read/2D/S07.md", "D01": "read/2D/D01.md", "M01": "read/2D/M01.md",
            "W01": "read/3D/W01.md", "R03": "read/1D/R03.md", "T01": "read/1D/T01.md", "E15": "read/1D/E15.md", "C09": "read/4D/C09.md",
            "V03": "read/5D/V03.md", "Q01": "read/QA/Q01.md", "P1D": "read/1D/P1D.md", "P2D": "read/2D/P2D.md", "INDEX-2D": "read/2D/INDEX.md",
            "INDEX-2D-2": "read/2D/INDEX-2.md", "SCREENS": "screens/INDEX.md", "SCREENS-2": "screens/INDEX-2.md", "NAV": "screens/NAV.md", "NAV-2": "screens/NAV-2.md",
        }
        for page, path in cases.items():
            self.assertEqual(N.page_path(page), path)
            twin = N.twin_path(path)
            self.assertEqual(twin, "site/" + path[:-3] + ".html")
        with self.assertRaises(ValueError):
            N.page_path("CARD-SCR-LOGIN")
        self.assertEqual(N.card_path("SCR-LOGIN"), "screens/SCR-LOGIN.card.md")
        self.assertEqual(N.card_path("SCR-LOGIN", 2), "screens/SCR-LOGIN.card-2.md")
        self.assertEqual(N.twin_path("screens/SCR-LOGIN.card.md"), "site/screens/SCR-LOGIN.html")
        self.assertEqual(N.twin_path("screens/SCR-LOGIN.card-2.md"), "site/screens/SCR-LOGIN.html")
        for none in ("docs/README.md", "docs/zh-CN/DD.md", "prompts/PR-DD-0123abcd.md", "oracle-sheet.md", "diagrams/x.mmd", "ids.tsv", "read/2D/S07.html", "screens/SCR-LOGIN.svg"):
            self.assertIsNone(N.twin_path(none), none)
        self.assertIsNone(N.twin_path(None))  # type: ignore[arg-type]

    def test_family_classes(self):
        self.assertEqual(N.parse_family_class("S:page"), ("S", "page"))
        self.assertEqual(N.parse_family_class(("W", "card")), ("W", "card"))
        self.assertEqual(N.parse_family_class("START"), ("START", "card"))
        self.assertEqual(N.parse_family_class("CARD"), ("CARD", "page"))
        self.assertEqual(N.family_class_key(("Q", "page")), "Q:page")
        for bad in ("W", "Q", "X:page", "S:card", "CARD:card", "S:wide", "", ":page", None, 3, ("S",), ("S", "page", "x"), ["S", 1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                N.parse_family_class(bad)
        self.assertEqual(N.family_title("S", "zh-CN"), "2D \u7ed3\u6784")
        self.assertEqual(N.family_title("S", "ja-JP"), "2D structure")
        self.assertEqual(N.family_title("INDEX", "en-US", "4D"), "4D index")
        with self.assertRaises(ValueError):
            N.family_title("X")
        with self.assertRaises(ValueError):
            N.text_lang("fr-FR")


# ---------------------------------------------------------------------------------------------------- budgets

class BudgetTests(unittest.TestCase):
    def test_the_numbers_of_the_design(self):
        self.assertEqual(dict(budgets.BUDGETS["cap_md"]), {"card": 6000, "page": 9000})
        self.assertEqual(dict(budgets.BUDGETS["cap_html"]), {"card": 14400, "page": 21600})
        self.assertEqual(budgets.BUDGETS["cap_html"]["page"], budgets.BUDGETS["cap_md"]["page"] * 2.4)
        self.assertEqual(budgets.BUDGETS["cap_svg"], 20000)
        self.assertEqual(budgets.BUDGETS["cap_gallery"], 40000)
        self.assertEqual(budgets.BUDGETS["line_chars"], 500)
        self.assertEqual(budgets.BUDGETS["tsv_line_chars"], 400)
        self.assertIs(api.BUDGETS, budgets.BUDGETS)
        self.assertEqual(dict(budgets.ENVELOPE_CAPS), {"standard": 1000, "wide": 1300, "screen": 1500})

    def test_there_is_no_envelope_constant(self):
        for key in budgets.BUDGETS:
            self.assertFalse(key.upper().startswith("ENV"), key)
        for module in (budgets, N, E, api):
            self.assertEqual([n for n in dir(module) if n.startswith("ENV_")], [], module.__name__)

    def test_budgets_are_read_only(self):
        with self.assertRaises(TypeError):
            budgets.BUDGETS["cap_md"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            budgets.BUDGETS["cap_md"]["page"] = 1  # type: ignore[index]

    def test_a_page_has_about_eight_thousand_bytes_of_room(self):
        room = api.content_capacity("S:page")
        self.assertGreater(room, 7800)
        self.assertLess(room, 8300)
        self.assertEqual(room, 9000 - api.envelope_bytes("S:page"))
        self.assertEqual(api.content_capacity("INDEX:card"), 6000 - api.envelope_bytes("INDEX:card"))
        self.assertLess(api.content_capacity("C:page", "zh-CN", True), api.content_capacity("C:page"))

    def test_the_clip_constants_leave_the_card_caps_inside_the_limits(self):
        # the three card cap sets are ordered by what each bucket can pay
        std, wide, tight = (budgets.CARD_FIELD_CAPS[k] for k in ("standard", "wide", "tight"))
        self.assertLess(sum(std.values()), sum(wide.values()))
        self.assertLessEqual(sum(tight.values()), sum(wide.values()))
        self.assertGreaterEqual(std["covers"], 110)  # the design's own example scope card fits


# ---------------------------------------------------------------------------------------------------- envelope

def example_meta(**changes) -> dict:
    meta = {
        "page": "S07", "index": 7, "count": 26, "cls": "page", "project": "refscale-3", "lang": "zh-CN", "stamp": "0262885f",
        "covers": "\u8282\u70b9 9\u3001\u5176\u51fa\u5411\u8fde\u63a5 21\u3001\u5165\u5411\u8fde\u63a5 15",
        "excludes": "\u5176\u4f59 160 \u8282\u70b9\uff08\u00a7S01-\u00a7S06\u3001\u00a7S08-\u00a7S26\uff09",
        "howto": "`[N/A]` n/a; `[NOT-AUTHORED:\u2026]` missing; grades (high to low): Implemented > Observed > Designed",
        "prev": ("S06", "S06.md"), "next": ("S08", "S08.md"), "up": "INDEX.md", "start": "../../START.md",
    }
    meta.update(changes)
    return meta


def render(meta: dict, body=None) -> str:
    head, foot = E.envelope(meta)
    return E.finalize(head + (body if body is not None else ["## body", "text"]) + foot)


class EnvelopeShapeTests(unittest.TestCase):
    def test_the_example_page_has_the_layout_of_design_2_5(self):
        text = render(example_meta(), ["## node (9)", "..."])
        lines = text.split("\n")
        self.assertEqual(lines.pop(), "")
        self.assertRegex(lines[0], r"^\u9875 \u00a7S07 \u00b7 2D \u7ed3\u6784 \u00b7 7/26 \u00b7 \u5171 13 \u884c \u00b7 \u9875\u7c7b page$")
        self.assertEqual(lines[1], '\u9879\u76ee refscale-3 \u00b7 zh-CN \u00b7 m=0262885f \u00b7 \u4ec5\u7ed3\u6784 \u00b7 \u6838\u5bf9\u662f\u5426\u8fc7\u671f: gemarc_atlas_index scope="stamp"')
        self.assertEqual(lines[2], "# 2D \u7ed3\u6784 \u00b7 \u7b2c 7/26 \u9875")
        self.assertEqual(lines[3], "")
        self.assertTrue(lines[4].startswith("> \u542b: ") and lines[5].startswith("> \u4e0d\u542b: ") and lines[6].startswith("> \u8bfb\u6cd5: "))
        self.assertEqual(lines[7], "")
        self.assertEqual(lines[8:10], ["## node (9)", "..."])
        self.assertEqual(lines[10], "")
        self.assertEqual(lines[11], "\u90bb\u9875: [\u4e0a\u4e00\u9875 \u00a7S06](S06.md) \u00b7 [\u4e0b\u4e00\u9875 \u00a7S08](S08.md) \u00b7 [\u7d22\u5f15](INDEX.md) \u00b7 [START](../../START.md)")
        self.assertRegex(lines[12], r"^\u9875\u5c3e \u00a7S07 \u00b7 \u5171 13 \u884c \u00b7 sha8 [0-9a-f]{8} \u00b7 \u4ee5\u4e0a\u4e3a\u5168\u90e8\u5185\u5bb9$")
        self.assertEqual(len(lines), 13)
        self.assertEqual(E.verify_sentinel(text), [])

    def test_head_is_eight_lines_and_the_card_is_inside_the_first_twelve(self):
        head, foot = E.envelope(example_meta())
        self.assertEqual(len(head), 8)
        t = E.TEXT["zh-CN"]
        for key in ("read.card.covers", "read.card.excludes", "read.card.howto"):
            prefix = t[key].split("{text}")[0]
            self.assertTrue(any(line.startswith(prefix) for line in head[:12]), key)
        self.assertEqual(foot[0], "")
        self.assertIn(E.LINES_TOKEN, head[0])
        self.assertIn(E.LINES_TOKEN, foot[-1])
        self.assertIn(E.SHA_TOKEN, foot[-1])

    def test_english_and_japanese_use_the_english_texts_and_keep_the_language_code(self):
        en = render(example_meta(lang="en-US", covers="nodes 9", excludes="the rest", howto="n/a"))
        self.assertTrue(en.startswith("Page \u00a7S07 \u00b7 2D structure \u00b7 7/26 \u00b7 "))
        self.assertIn("Project refscale-3 \u00b7 en-US \u00b7 m=0262885f \u00b7 structure only \u00b7 check for staleness: gemarc_atlas_index scope=\"stamp\"", en)
        self.assertIn("> Covers: nodes 9\n> Does not cover: the rest\n> How to read: n/a", en)
        self.assertEqual(E.verify_sentinel(en), [])
        ja = render(example_meta(lang="ja-JP"))
        self.assertIn("Project refscale-3 \u00b7 ja-JP \u00b7 m=", ja)
        self.assertEqual(E.verify_sentinel(ja), [])

    def test_a_single_page_family_has_a_plain_heading_and_screens_carry_both_stamps(self):
        text = render(example_meta(page="G01", index=1, count=1, cls="card", prev=None, next=None, up=None, start="../START.md"))
        self.assertIn("\n# \u672f\u8bed\u4e0e\u56fe\u4f8b\n", text)
        self.assertNotIn("\u7b2c 1/1 \u9875", text)
        card = render(example_meta(page="CARD-SCR-LOGIN", cls="page", ui_stamp="77c1da79", content="prototype", prev=None, next=None, up="INDEX.md", start="../START.md"))
        self.assertIn("\u00b7 u=77c1da79 \u00b7 m=0262885f \u00b7 \u539f\u578b\u754c\u9762 \u00b7", card)
        only_ui = render(example_meta(page="SCREENS", cls="card", stamp=None, ui_stamp="77c1da79", content="prototype", prev=None, next=None))
        self.assertIn("\u00b7 u=77c1da79 \u00b7 \u539f\u578b\u754c\u9762 \u00b7", only_ui)
        self.assertNotIn("m=", only_ui.split("\n")[1])

    def test_a_missing_neighbour_leaves_no_empty_link_and_none_at_all_leaves_no_line(self):
        text = render(example_meta(prev=None, next=("S08", "S08.md"), up=None))
        self.assertIn("\u90bb\u9875: [\u4e0b\u4e00\u9875 \u00a7S08](S08.md) \u00b7 [START](../../START.md)", text)
        bare = render(example_meta(prev=None, next=None, up=None, start=None))
        self.assertNotIn("\u90bb\u9875", bare)
        self.assertEqual(E.verify_sentinel(bare), [])

    def test_a_continuation_part_has_the_card_line_and_the_tail_line(self):
        cont = {"unit": "zh6.pallet-lock", "part": 2, "parts": 4, "others": ["S08", "S09", "S11"], "next": "S10"}
        text = render(example_meta(continuation=cont))
        self.assertIn("> \u7eed: \u672c\u9875\u662f zh6.pallet-lock \u7684\u7b2c 2/4 \u90e8\u5206\uff1b\u5176\u4f59\u90e8\u5206: \u00a7S08-\u00a7S09\u3001\u00a7S11", text)
        self.assertIn("\uff08\u672a\u5b8c\uff1a\u4e0b\u4e00\u90e8\u5206\u5728 \u00a7S10\uff09", text)
        last = render(example_meta(continuation={**cont, "part": 4, "next": None}))
        self.assertIn("\uff08\u672c\u5355\u5143\u5230\u6b64\u7ed3\u675f\uff09", last)
        self.assertEqual(E.verify_sentinel(text) + E.verify_sentinel(last), [])
        for broken in ({**cont, "part": 5}, {**cont, "parts": 1, "part": 1}, {**cont, "next": "nope"}, {**cont, "others": ["nope"]}):
            with self.assertRaises(ValueError):
                E.envelope(example_meta(continuation=broken))

    def test_format_page_list_makes_ranges_of_consecutive_numbers_only(self):
        f = E.format_page_list
        self.assertEqual(f(["S08", "S09", "S10", "S12"]), "\u00a7S08-\u00a7S10\u3001\u00a7S12")
        self.assertEqual(f(["S08", "C09"]), "\u00a7S08\u3001\u00a7C09")
        self.assertEqual(f(["S99", "S100"]), "\u00a7S99-\u00a7S100")
        self.assertEqual(f(["SCREENS-2", "SCREENS-3"]), "\u00a7SCREENS-2\u3001\u00a7SCREENS-3")
        self.assertEqual(f([]), "")
        self.assertEqual(f(["S01", "S02"], ", "), "\u00a7S01-\u00a7S02")
        long = f([N.page_id("S", n) for n in range(1, 400, 2)])
        self.assertLessEqual(len(long.encode("utf-8")), E.OTHERS_CLIP)
        self.assertTrue(long.endswith("\u2026"))

    def test_invalid_metadata_is_refused(self):
        bad = [
            {"page": "s07"}, {"page": "S7"}, {"cls": "wide"}, {"cls": "card"}, {"index": 0}, {"index": 27}, {"count": 0}, {"index": True}, {"lang": "fr-FR"},
            {"lang": None}, {"project": ""}, {"project": 3}, {"stamp": "XYZ"}, {"stamp": "0262885"}, {"stamp": None}, {"ui_stamp": "0262885G"}, {"content": "sealed"},
            {"content": "prototype"}, {"prev": ("S06",)}, {"prev": ("nope", "S06.md")}, {"prev": ("S06", "x" * 21)}, {"up": "has space.md"}, {"up": "a)b.md"}, {"start": ""},
            {"covers": 5}, {"howto": "line {lines} placeholder"}, {"covers": "{sha8}"}, {"title": "my own title"}, {"index": 1000, "count": 1000},
            {"page": "INDEX-2D-1234", "cls": "card", "prev": None, "next": None}, {"prev": ("INDEX-2D-1234", "x.md")}, {"project": "has {lines}"},
            {"continuation": {"unit": "u", "part": 1, "parts": 1000, "others": [], "next": None}}, {"continuation": {"unit": "u", "part": 1, "parts": 2, "others": ["CARD-" + "x" * 90]}},
            {"continuation": {"unit": "u", "part": True, "parts": 2, "others": []}}, {"continuation": "yes"},
        ]
        for change in bad:
            with self.assertRaises(ValueError, msg=repr(change)):
                E.envelope(example_meta(**change))
        with self.assertRaises(ValueError):
            E.envelope([])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            E.envelope(example_meta(page="CARD-SCR-LOGIN", cls="page", ui_stamp="77c1da79", content="prototype"))  # a card has no prev/next
        with self.assertRaises(ValueError):
            E.envelope(example_meta(page="G01", cls="page"))

    def test_text_is_one_line_and_clipped_and_never_formatted(self):
        meta = example_meta(covers="a\nb\r\nc {title} {i}", excludes="x" * 400, howto="\u7a7a" * 400, project="p" * 80)
        head, _foot = E.envelope(meta)
        for line in head:
            self.assertNotIn("\n", line)
            self.assertNotIn("\r", line)
        self.assertEqual(head[4], "> \u542b: a b c {title} {i}")
        caps = budgets.CARD_FIELD_CAPS["standard"]
        self.assertLessEqual(len(head[5].split(": ", 1)[1].encode()), caps["excludes"])
        self.assertTrue(head[5].endswith("\u2026"))
        self.assertLessEqual(len(head[6].split(": ", 1)[1].encode()), caps["howto"])
        self.assertIn("p" * 29 + "\u2026", head[1])
        self.assertNotIn("p" * 30, head[1])
        self.assertEqual(E.clip_bytes("\u7a7a\u7a7a\u7a7a", 7), "\u7a7a\u2026")
        card = E.envelope(example_meta(page="CARD-SCR-LOGIN", cls="page", ui_stamp="77c1da79", content="prototype", title="t" * 90, prev=None, next=None))[0]
        self.assertIn("\u00b7 " + "t" * 45 + "\u2026 \u00b7", card[0])
        self.assertEqual(card[2], "# " + "t" * 45 + "\u2026 \u00b7 \u7b2c 7/26 \u9875")
        self.assertEqual(E.clip_bytes("abc", 3), "abc")
        self.assertEqual(E.clip_bytes("abcd", 4), "abcd")
        self.assertLessEqual(len(E.clip_bytes("abcd", 3).encode()), 3)
        self.assertTrue(E.clip_bytes("abcd", 3).endswith("\u2026"))


class SentinelTests(unittest.TestCase):
    def page(self, lang="zh-CN") -> str:
        meta = example_meta(lang=lang)
        if lang != "zh-CN":
            meta.update(covers="c", excludes="e", howto="h")
        return render(meta, ["## body", "| a | b |", "|---|---|", "| 1 | 2 |"])

    def test_a_finished_page_verifies_in_every_language_and_the_hash_is_the_documented_one(self):
        for lang in ("zh-CN", "en-US"):
            text = self.page(lang)
            self.assertEqual(E.verify_sentinel(text), [])
            prefix = text[:text.rindex("\n", 0, len(text) - 1) + 1]
            digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8]
            self.assertTrue(text.rstrip("\n").endswith(" \u00b7 sha8 " + digest + (" \u00b7 \u4ee5\u4e0a\u4e3a\u5168\u90e8\u5185\u5bb9" if lang == "zh-CN" else " \u00b7 that is the whole page")))
            physical = len(text.split("\n")) - 1
            self.assertIn(f" {physical} ", text.split("\n")[0])

    def test_every_damage_is_found(self):
        text = self.page()
        lines = text.split("\n")
        damaged = {
            "a body byte": text.replace("| 1 | 2 |", "| 1 | 3 |"),
            "a head byte": text.replace("refscale-3", "refscale-4"),
            "a missing line": text.replace("|---|---|\n", ""),
            "an added line": text.replace("## body\n", "## body\nextra\n"),
            "a wrong sentinel count": text.replace(f"\u5171 {len(lines) - 1} \u884c \u00b7 sha8", "\u5171 99 \u884c \u00b7 sha8"),
            "a wrong first-line count": text.replace("\u5171 17 \u884c \u00b7 \u9875\u7c7b", "\u5171 18 \u884c \u00b7 \u9875\u7c7b", 1),
            "a wrong sha": re.sub(r"sha8 [0-9a-f]{8}", "sha8 00000000", text),
            "a trailing blank line": text + "\n",
            "no final newline": text.rstrip("\n"),
            "a cut-off sentinel": text[:text.rindex("\u00b7")] + "\n",
            "a foreign last line": text.rsplit("\n", 2)[0] + "\nthe end\n",
            "another page id": text.replace("\u9875\u5c3e \u00a7S07", "\u9875\u5c3e \u00a7S08"),
            "CRLF": text.replace("\n", "\r\n"),
            "empty": "",
        }
        for what, changed in damaged.items():
            if changed == text:
                continue
            self.assertNotEqual(E.verify_sentinel(changed), [], what)

    def test_the_first_line_count_is_checked_separately_from_the_sentinel(self):
        text = self.page()
        first = text.split("\n")[0]
        count = re.search(r"\u5171 (\d+) \u884c", first).group(1)
        altered = text.replace(first, first.replace(f"\u5171 {count} \u884c", "\u5171 7 \u884c"), 1)
        problems = E.verify_sentinel(altered)
        self.assertTrue(any("first line" in p for p in problems), problems)

    def test_finalize_refuses_what_it_cannot_make_true(self):
        head, foot = E.envelope(example_meta())
        with self.assertRaises(ValueError):
            E.finalize(head + ["has\nnewline"] + foot)
        with self.assertRaises(ValueError):
            E.finalize(head + ["has\rreturn"] + foot)
        with self.assertRaises(ValueError):
            E.finalize(head + ["{lines} in the body"] + foot)
        with self.assertRaises(ValueError):
            E.finalize(head + ["{sha8} in the body"] + foot)
        with self.assertRaises(ValueError):
            E.finalize(["only one line"])
        with self.assertRaises(ValueError):
            E.finalize(["first {lines}", "sentinel without sha {lines}"])
        with self.assertRaises(ValueError):
            E.finalize(["no placeholder", "sentinel {lines} {sha8}"])
        with self.assertRaises(ValueError):
            E.finalize(head + [5] + foot)  # type: ignore[list-item]
        self.assertEqual(E.finalize(head + [] + foot).count("\n"), len(head) + len(foot))

    def test_finalize_does_not_change_the_input_list(self):
        head, foot = E.envelope(example_meta())
        lines = head + ["x"] + foot
        copy = list(lines)
        E.finalize(lines)
        self.assertEqual(lines, copy)

    def test_the_line_count_counts_the_sentinel_itself_and_the_digest_ignores_it(self):
        text = render(example_meta(), [])
        count = int(re.search(r"\u5171 (\d+) \u884c", text).group(1))
        self.assertEqual(count, text.count("\n"))
        other = text.replace(re.search(r"sha8 [0-9a-f]{8}", text).group(0), "sha8 ffffffff")
        self.assertEqual(hashlib.sha256(text[:text.rindex("\n", 0, len(text) - 1) + 1].encode()).hexdigest(),
                         hashlib.sha256(other[:other.rindex("\n", 0, len(other) - 1) + 1].encode()).hexdigest())


class EnvelopeBoundTests(unittest.TestCase):
    """The frame of a page can never be larger than the frame the planner reserved."""

    def test_the_widest_frame_is_inside_the_limit_for_every_family_class(self):
        self.assertGreaterEqual(len(ALL_FAMILY_CLASSES), 20)
        for family_class in ALL_FAMILY_CLASSES:
            for lang in ("zh-CN", "en-US", "ja-JP"):
                for continuation in (False, True):
                    size = E.envelope_size(family_class, lang, continuation)
                    limit = E.envelope_limit(family_class, lang, continuation)
                    self.assertLessEqual(size, limit, f"{family_class} {lang} continuation={continuation}: {size} > {limit}")
                    self.assertGreater(size, 500)

    def test_the_limits_follow_design_2_5(self):
        spec = {"S:page": 1000, "D:page": 1000, "M:page": 1000, "R:page": 1000, "T:page": 1000, "E:page": 1000, "W:card": 1000, "W:page": 1000,
                "G:card": 1000, "INDEX:card": 1000, "START:card": 1000, "C:page": 1300, "V:page": 1300, "Q:card": 1300, "Q:page": 1300, "P:page": 1300,
                "A:page": 1300, "SCREENS:card": 1300, "NAV:card": 1300, "CARD:page": 1500}
        self.assertEqual(set(spec), set(ALL_FAMILY_CLASSES))
        for family_class, limit in spec.items():
            self.assertEqual(E.envelope_limit(family_class), limit, family_class)
            self.assertEqual(E.envelope_limit(family_class, "en-US"), limit * 11 // 10, family_class)
        for family_class in ("S:page", "M:page", "R:page", "G:card"):  # a continuation part of a standard family moves to the wide bucket
            self.assertEqual(E.envelope_limit(family_class, "zh-CN", True), 1300)

    def test_envelope_bytes_is_the_widest_plain_zh_frame(self):
        for family_class in ALL_FAMILY_CLASSES:
            self.assertEqual(api.envelope_bytes(family_class), E.envelope_size(family_class, "zh-CN", False))
        # the design's own measurement of the typical frame (853 bytes with 15-character ids) is below the worst case it reserves
        self.assertGreater(api.envelope_bytes("S:page"), 853)
        self.assertLess(api.envelope_bytes("S:page"), 1000)

    def test_the_worst_case_frame_is_what_the_renderer_makes_of_the_worst_case_meta(self):
        meta = E.worst_case_meta("S:page")
        head, foot = E.envelope(meta)
        four = sum(len(line.replace(E.LINES_TOKEN, "9999").replace(E.SHA_TOKEN, "f" * 8).encode()) + 1 for line in head + foot)
        self.assertEqual(four, E.envelope_size("S:page"))
        self.assertEqual(meta["index"], 999)
        self.assertEqual(len(meta["covers"].encode()), budgets.CARD_FIELD_CAPS["standard"]["covers"])

    def test_no_admissible_metadata_renders_a_frame_wider_than_the_reserved_one(self):
        rng = random.Random(5)
        glyphs = ["a", "b", "7", " ", "`", "|", "\u00a7", "\u7a7a", "\u30c6", "\u00e9", "\U0001f600", "\u2026", "{", "}", "\n", "\r"]

        def junk(limit: int) -> str:
            return "".join(rng.choice(glyphs) for _ in range(rng.randint(0, limit)))

        checked = widest = 0
        for _ in range(900):
            family_class = rng.choice(ALL_FAMILY_CLASSES)
            family, cls = N.parse_family_class(family_class)
            lang = rng.choice(["zh-CN", "en-US", "ja-JP"])
            count = rng.choice([1, 2, 9, 99, 999])
            index = rng.randint(1, count)
            page = {"START": "START", "P": rng.choice(["P1D", "P2D"]), "INDEX": rng.choice(["INDEX-2D", "INDEX-QA-999"]), "SCREENS": rng.choice(["SCREENS", "SCREENS-9999"]),
                    "NAV": rng.choice(["NAV", "NAV-99999999"]), "CARD": "CARD-" + "x" * rng.randint(1, 80) + rng.choice(["", "-99"])}.get(family, family + f"{rng.randint(1, 999):02d}")
            meta = {"page": page, "index": index, "count": count, "cls": cls, "lang": lang, "project": "p" + junk(120).replace("{", "("),
                    "stamp": "%08x" % rng.getrandbits(32), "ui_stamp": ("%08x" % rng.getrandbits(32)) if family in ("SCREENS", "NAV", "CARD") else None,
                    "content": rng.choice(E.content_kinds(family)), "covers": junk(500), "excludes": junk(500), "howto": junk(900),
                    "up": "../" + "u" * rng.randint(1, 14) + ".md", "start": "../" * 2 + "s" * rng.randint(1, 8) + ".md"}
            if family == "CARD":
                meta["title"] = junk(120)
            else:
                meta["prev"] = (page, "p" * rng.randint(1, 20))
                meta["next"] = (page, "n" * rng.randint(1, 20))
            if count >= 2 and rng.random() < 0.5:
                width = E.CARD_ID_MAX if family == "CARD" else E.PAGE_ID_MAX
                others = [N.page_id("S", rng.randint(1, 999)) if family != "CARD" else "CARD-" + "y" * rng.randint(1, 80) for _ in range(rng.randint(0, 60))]
                meta["continuation"] = {"unit": junk(200) or "u", "part": index if index >= 1 else 1, "parts": count, "next": rng.choice([None, page]),
                                        "others": [p for p in others if len(p) <= width]}
            has_cont = "continuation" in meta
            head, foot = E.envelope(meta)
            body = ["b"] * rng.choice([0, 5, 120, 1500, 9000])
            text = E.finalize(head + body + foot)
            self.assertEqual(E.verify_sentinel(text), [], (family_class, lang))
            frame = len(text.encode("utf-8")) - sum(len(b.encode()) + 1 for b in body)
            reserved = E.envelope_size(family_class, lang, has_cont)
            self.assertLessEqual(frame, reserved, f"{family_class} {lang} continuation={has_cont}: {frame} > {reserved}")
            self.assertLessEqual(frame, E.envelope_limit(family_class, lang, has_cont))
            widest = max(widest, reserved - frame)
            checked += 1
        self.assertEqual(checked, 900)
        self.assertGreater(widest, 0)

    def test_a_typical_design_example_page_fits_the_standard_caps_unclipped(self):
        covers = "\u8282\u70b9 9\u3001\u5176\u51fa\u5411\u8fde\u63a5 21\u3001\u5165\u5411\u8fde\u63a5 15\uff08`zh6.pallet-lock`\u2026`zh6.trace-audit`\uff0c\u7a7a\u95f4\u987a\u5e8f\uff0c\u5b8c\u6574\uff09"
        excludes = "\u5176\u4f59 160 \u8282\u70b9\uff08\u00a7S01-\u00a7S06\u3001\u00a7S08-\u00a7S26\uff09\uff1b\u7528\u4f8b\u3001\u8bc1\u636e\u3001\u72b6\u6001\u673a\u3001\u6570\u636e\u8868\u89c1 \u00a7C\u3001\u00a7E\u3001\u00a7M\u3001\u00a7D \u9875"
        howto = ("`[N/A]` \u65e0\u6b64\u9879\uff08\u672a\u5199\u6216\u4e0d\u9002\u7528\uff0c\u672c\u6a21\u578b\u4e0d\u533a\u5206\uff09\uff1b`[NOT-AUTHORED:\u2026]` \u4e8b\u5b9e\u7f3a\u5931\uff08\u963b\u65ad\u53d1\u5e03\uff09\uff1b"
                 "\u7b49\u7ea7(\u9ad8\u2192\u4f4e): Implemented > Observed > Designed > Inferred > Hypothetical > Unknown")
        head, _foot = E.envelope(example_meta(covers=covers, excludes=excludes, howto=howto))
        self.assertTrue(head[4].endswith("\uff09"), "the covers line of the design example is not clipped")
        self.assertTrue(head[5].endswith("\u9875"), "the excludes line of the design example is not clipped")
        self.assertTrue(head[6].endswith("Unknown"), "the how-to-read line of the design example is not clipped")


# ---------------------------------------------------------------------------------------------------- the interface

class InterfaceTests(unittest.TestCase):
    def test_the_signatures_of_the_table_in_design_12_1(self):
        self.assertEqual(list(inspect.signature(api.envelope).parameters), ["meta"])
        self.assertEqual(list(inspect.signature(api.envelope_bytes).parameters), ["family_class"])
        self.assertEqual(list(inspect.signature(api.enc).parameters), ["id"])
        self.assertTrue(hasattr(api, "BUDGETS") and "cap_md" in api.BUDGETS and "cap_html" in api.BUDGETS)
        self.assertEqual(list(inspect.signature(api.finalize).parameters), ["lines"])
        self.assertEqual(list(inspect.signature(api.verify_sentinel).parameters), ["text"])
        self.assertIs(api.enc, N.enc)
        self.assertIs(api.envelope, E.envelope)
        # the package level finalize is the manifest writer of R1 (``finalize(dist_dir, result)``), a different function from the line-level one
        import gemarc_mcp.atlas.reading as reading
        self.assertTrue(hasattr(reading, "finalize"))
        self.assertIsNot(reading.finalize, api.finalize)
        self.assertEqual(list(inspect.signature(reading.finalize).parameters), ["dist_dir", "result"])
        self.assertIn("finalize", api.__all__)

    def test_envelope_returns_two_lists_of_single_lines(self):
        head, foot = api.envelope(example_meta())
        self.assertIsInstance(head, list)
        self.assertIsInstance(foot, list)
        self.assertTrue(all(isinstance(line, str) and "\n" not in line for line in head + foot))

    def test_all_exported_names_exist(self):
        for name in api.__all__:
            self.assertTrue(hasattr(api, name), name)

    def test_the_modules_are_pure_standard_library_and_do_no_io(self):
        allowed = {"__future__", "hashlib", "re", "types", "typing", "collections", "dataclasses", "json", "math", "difflib", "pathlib", "html", "posixpath", "itertools"}
        for path in sorted(READING.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed, f"{path.name} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn((node.module or "").split(".")[0], allowed, f"{path.name} imports from {node.module}")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "print", "input", "exec", "eval", "__import__"}, f"{path.name} calls {node.func.id}")

    def test_the_files_are_utf8_with_lf_only(self):
        for path in sorted(READING.glob("*.py")):
            data = path.read_bytes()
            self.assertNotIn(b"\r", data, path.name)
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), path.name)
            data.decode("utf-8")

    def test_the_results_do_not_depend_on_the_hash_seed(self):
        script = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "from gemarc_mcp.atlas.reading import api\n"
            "ids = ['Pump', 'pump', 'PUMP', 'CON', '_CON', 'a.', 'x'*90, 'SCR-LOGIN#error', 'zh6.a:b:c', '\\u7a7a'*30]\n"
            "out = {'enc': api.enc_many(ids), 'sizes': {fc: api.envelope_size(fc) for fc in ['S:page', 'C:page', 'CARD:page', 'INDEX:card']}}\n"
            "print(json.dumps(out, sort_keys=True))\n" % str(Path(api.__file__).resolve().parents[3])
        )
        results = set()
        for seed in ("0", "1", "4242"):
            done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, env={"PYTHONHASHSEED": seed, "PATH": ""}, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            results.add(done.stdout)
        self.assertEqual(len(results), 1)
        self.assertEqual(set(json.loads(next(iter(results)))["enc"]), {"Pump", "pump", "PUMP", "CON", "_CON", "a.", "x" * 90, "SCR-LOGIN#error", "zh6.a:b:c", "\u7a7a" * 30})


if __name__ == "__main__":
    unittest.main()
