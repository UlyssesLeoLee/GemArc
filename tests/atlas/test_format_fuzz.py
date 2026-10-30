"""Fuzz gate of the format core (design M1 acceptance (c), stop/go): mutated rows never crash the parser and never change
the model silently.

Every run mutates at least 500 rows (default 60 per class over 10 guarded classes, plus the .jsonl classes) of encoded
synthetic models. A mutation is *silent* when the expanded model differs from the unmutated one while the expander
reported no diagnostic. Two oracles are applied, both made of ``Diag`` records:

* ``structural`` (slice mode, ``closed_world=False``): the ST/DV diagnostics of the format lane;
* ``closed`` (full mode, ``closed_world=True``): the structural diagnostics plus RF-001..RF-005 for every unresolved
  reference (``expand.reference_diagnostics``).

Gate (asserted): silent-swallow rate 0 under ``closed`` for every guarded class, and 0 under ``structural`` for every
guarded class except ``swapped-cells`` / ``swapped-values`` (two same-grammar cells exchanged, for example a building id
and a name that is also a legal id: only reference resolution, i.e. full mode, can see that; in slice mode such a
dangling reference is a pending reference by design). Informational classes (well-formed rows with different
content: dropped options cell, same-kind swap, truncation, deleted row, deleted ``@set`` line) are measured and printed,
not asserted: no syntax rule can close a row that is valid with other content.

Tunables: ``ATLAS_FUZZ_SEED`` (default 20261030) and ``ATLAS_FUZZ_N`` (mutations per class, default 60).
Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_format_fuzz.py"
"""
from __future__ import annotations

import json
import os
import random
import re
import unittest

from gemarc_mcp.atlas import encode, expand
from gemarc_mcp.atlas import model as am
from gemarc_mcp.atlas.format import atl, jsonl
from gemarc_mcp.atlas.format import rows as rw

from . import synth

SEED = int(os.environ.get("ATLAS_FUZZ_SEED", "20261030"))
N = int(os.environ.get("ATLAS_FUZZ_N", "60"))

GUARDED = ("missing-pipe", "extra-pipe", "dropped-cell", "swapped-cells", "unbalanced-quote", "unknown-key", "wrong-case-enum",
           "duplicate-key", "empty-value", "bad-number", "option-key-is-column")
INFORMATIONAL = ("drop-options-cell", "same-kind-swap", "truncate-line", "delete-row", "delete-set-line", "delete-header")
JSONL_GUARDED = ("unknown-key", "wrong-case-enum", "dropped-key", "swapped-values", "wrong-type", "unbalanced-quote", "missing-brace",
                 "extra-comma", "duplicate-key")
#: Only these may be closed by reference resolution alone (documented above).
CLOSED_BY_REFERENCES_ONLY = {"swapped-cells", "swapped-values"}


# ---------------------------------------------------------------------------------------------------------------
# .atl text mutators
# ---------------------------------------------------------------------------------------------------------------


def _in_string(line: str, k: int) -> bool:
    in_str, i = False, 0
    while i < k:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        i += 1
    return in_str


def structural_pipes(line: str) -> list[int]:
    """Positions of ``|`` outside quoted strings and not escaped (bare cells hold no quotes, JSON holds no ``|`` outside strings)."""
    out, in_str, i = [], False, 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif c == "|" and not in_str:
            out.append(i)
        i += 1
    return out


def cell_spans(line: str, positional: int) -> list[tuple[int, int]]:
    bounds = [-1] + structural_pipes(line)[:positional] + [len(line)]
    return [(bounds[i] + 1, bounds[i + 1]) for i in range(len(bounds) - 1)]


def row_lines(lines: list[str]) -> tuple[list[int], list]:
    """Indices of data rows and the section name in force at each line."""
    section, sections = None, []
    for text in lines:
        s = text.strip()
        if s.startswith("## "):
            section = s[3:].split(":")[0].strip()
            sections.append(None)
        else:
            sections.append(section)
    rows = [i for i, text in enumerate(lines) if sections[i] and text.strip() and not text.startswith(("//", "@", "#"))]
    return rows, sections


def mutate_atl(rng: random.Random, lines: list[str], cls: str):
    """``(new_lines, description)`` or ``None`` when the class does not apply to the row picked."""
    rows, sections = row_lines(lines)
    if not rows:
        return None
    i = rng.choice(rows)
    line, sec = lines[i], sections[i]
    spec = rw.SECTIONS[sec]
    positional = len(spec.columns)
    spans = cell_spans(line, positional)
    pipes = structural_pipes(line)

    def put(new: str):
        out = list(lines)
        out[i] = new
        return out

    if cls == "missing-pipe":
        if not pipes:
            return None
        p = rng.choice(pipes[:positional])
        return put(line[:p] + line[p + 1:]), f"{sec}: drop the pipe at {p}"
    if cls == "extra-pipe":
        spots = [k for k in range(1, len(line)) if not _in_string(line, k) and line[k - 1] != "\\"]
        k = rng.choice(spots)
        return put(line[:k] + "|" + line[k:]), f"{sec}: insert a pipe at {k}"
    if cls == "dropped-cell":
        if len(spans) < 2:
            return None
        k = rng.randrange(min(positional, len(spans)))
        a, b = spans[k]
        return put(line[:a] + line[b + 1:] if b < len(line) else line[:max(a - 1, 0)]), f"{sec}: drop cell {k}"
    if cls in ("swapped-cells", "same-kind-swap"):
        if min(positional, len(spans)) < 2:
            return None
        k1, k2 = rng.sample(range(min(positional, len(spans))), 2)
        kind1, kind2 = rw.column_kind(sec, spec.columns[k1].name), rw.column_kind(sec, spec.columns[k2].name)
        if (cls == "swapped-cells") == (kind1 == kind2):
            return None
        c1, c2 = line[spans[k1][0]:spans[k1][1]], line[spans[k2][0]:spans[k2][1]]
        if c1.strip() == c2.strip():
            return None
        (a1, b1), (a2, b2) = sorted([spans[k1], spans[k2]])
        first, second = (c1, c2) if spans[k1][0] < spans[k2][0] else (c2, c1)
        return put(line[:a1] + second + line[b1:a2] + first + line[b2:]), f"{sec}: swap cells {k1},{k2} ({kind1}/{kind2})"
    if cls == "option-key-is-column":
        # an option whose key is the name of a positional column silently replaced that cell
        if not spec.options:
            return None
        k = rng.randrange(positional)
        col = spec.columns[k]
        value = "7" if col.type == "int" else "zz9"
        glue = " " if len(spans) > positional else " | "
        return put(line + glue + f"{col.name}={value}"), f"{sec}: option {col.name}= overrides cell {k}"
    if cls == "delete-header":
        heads = [k for k, text in enumerate(lines) if text.startswith("## ")]
        if not heads:
            return None
        out = list(lines)
        del out[rng.choice(heads)]
        return out, "delete a section header"
    if cls == "unbalanced-quote":
        quotes = [k for k, c in enumerate(line) if c == '"' and (k == 0 or line[k - 1] != "\\")]
        if quotes and rng.random() < 0.6:
            k = rng.choice(quotes)
            return put(line[:k] + line[k + 1:]), f"{sec}: delete the quote at {k}"
        k = rng.randrange(len(line) + 1)
        return put(line[:k] + '"' + line[k:]), f"{sec}: insert a quote at {k}"
    opts = spans[positional] if len(spans) > positional else None
    if cls == "drop-options-cell":
        if opts is None:
            return None
        return put(line[:pipes[positional - 1]].rstrip()), f"{sec}: drop the options cell"
    tokens = [m for m in re.finditer(r"(?:(?<=[ |])|^)([A-Za-z][A-Za-z0-9_]*)=", line) if opts and m.start() >= opts[0]]
    if cls == "wrong-case-enum":
        choices = [spans[k] for k in range(min(positional, len(spans))) if spec.columns[k].role.startswith("enum")]
        for t in tokens:
            f = spec.field(t.group(1))
            m = re.match(r"[^ ]+", line[t.end():])
            if f and f.role.startswith("enum") and m:
                choices.append((t.end(), t.end() + m.end()))
        if not choices:
            return None
        a, b = rng.choice(choices)
        word = line[a:b]
        if not any(ch.isalpha() for ch in word):
            return None
        pos = a + len(word) - len(word.lstrip())
        flipped = line[pos].lower() if line[pos].isupper() else line[pos].upper()
        return put(line[:pos] + flipped + line[pos + 1:]), f"{sec}: flip the case of {word.strip()!r}"
    if cls in ("unknown-key", "duplicate-key", "empty-value", "bad-number"):
        if not tokens:
            return None
        t = rng.choice(tokens)
        key = t.group(1)
        if cls == "unknown-key":
            new = rng.choice([key + "x", "x" + key, "frobnicate", key[:-1] or "q", key.upper()])
            if new in {f.name for f in spec.columns + spec.options}:
                return None
            return put(line[:t.start(1)] + new + line[t.end(1):]), f"{sec}: rename {key} to {new}"
        value = re.match(r"[^ ]+", line[t.end():])
        if cls == "duplicate-key":
            return put(line + " " + re.match(r"[^ ]+", line[t.start(1):]).group(0)), f"{sec}: repeat {key}"
        if cls == "empty-value":
            end = t.end() + (value.end() if value else 0)
            return put(line[:t.end()] + line[end:]), f"{sec}: empty {key}"
        f = spec.field(key)
        if f is not None and f.type in ("int", "intlist") and value:
            return put(line[:t.end()] + "2O" + line[t.end() + value.end():]), f"{sec}: bad number in {key}"
        ints = [k for k in range(min(positional, len(spans))) if spec.columns[k].type == "int"]
        if not ints:
            return None
        a, b = spans[rng.choice(ints)]
        return put(line[:a] + " x" + line[b:]), f"{sec}: bad number in a cell"
    if cls == "truncate-line":
        k = rng.randrange(2, len(line))
        return put(line[:k]), f"{sec}: truncate at {k}"
    if cls == "delete-row":
        out = list(lines)
        del out[i]
        return out, f"{sec}: delete the row"
    if cls == "delete-set-line":
        sets = [k for k, text in enumerate(lines) if text.startswith("@set")]
        if not sets:
            return None
        out = list(lines)
        del out[rng.choice(sets)]
        return out, "delete a @set line"
    raise KeyError(cls)


# ---------------------------------------------------------------------------------------------------------------
# .jsonl mutators
# ---------------------------------------------------------------------------------------------------------------


def mutate_jsonl(rng: random.Random, lines: list[str], cls: str):
    rows = [i for i, text in enumerate(lines) if text.startswith('{"_section"')]
    if not rows:
        return None
    i = rng.choice(rows)
    obj = json.loads(lines[i])
    sec = obj["_section"]
    spec = rw.SECTIONS[sec]
    keys = [k for k in obj if k != "_section"]
    positional = [f.name for f in spec.columns]

    def put(new_obj=None, text=None):
        out = list(lines)
        out[i] = text if text is not None else jsonl.dumps_line(new_obj)
        return out

    text = lines[i]
    if cls == "unknown-key":
        k = rng.choice(keys)
        new = rng.choice([k + "x", "frobnicate", k.upper()])
        if new in {f.name for f in spec.columns + spec.options}:
            return None
        return put({(new if key == k else key): v for key, v in obj.items()}), f"{sec}: rename {k}"
    if cls == "wrong-case-enum":
        choices = [f.name for f in spec.columns + spec.options if f.role.startswith("enum") and f.name in obj and isinstance(obj[f.name], str)]
        if not choices:
            return None
        k = rng.choice(choices)
        v = obj[k]
        obj[k] = (v[0].lower() if v[0].isupper() else v[0].upper()) + v[1:]
        return put(obj), f"{sec}: flip the case of {k}"
    if cls == "dropped-key":
        k = rng.choice(positional)
        del obj[k]
        return put(obj), f"{sec}: drop {k}"
    if cls == "swapped-values":
        k1, k2 = rng.sample(positional, 2) if len(positional) > 1 else (None, None)
        if k1 is None or rw.column_kind(sec, k1) == rw.column_kind(sec, k2):
            return None
        obj[k1], obj[k2] = obj[k2], obj[k1]
        return put(obj), f"{sec}: swap {k1}/{k2}"
    if cls == "wrong-type":
        typed = [f.name for f in spec.columns + spec.options if f.type in ("int", "bool", "obj", "intlist") and f.name in obj]
        if not typed:
            return None
        k = rng.choice(typed)
        obj[k] = "7" if isinstance(obj[k], (int, bool)) else ["x"]
        return put(obj), f"{sec}: wrong type for {k}"
    if cls == "unbalanced-quote":
        quotes = [k for k, c in enumerate(text) if c == '"' and (k == 0 or text[k - 1] != "\\")]
        k = rng.choice(quotes)
        return put(text=text[:k] + text[k + 1:]), f"{sec}: delete the quote at {k}"
    if cls == "missing-brace":
        spots = [k for k, c in enumerate(text) if c in "{}[]" and not _in_string(text, k)]
        k = rng.choice(spots)
        return put(text=text[:k] + text[k + 1:]), f"{sec}: delete {text[k]} at {k}"
    if cls == "extra-comma":
        spots = [k for k in range(1, len(text)) if not _in_string(text, k)]
        k = rng.choice(spots)
        return put(text=text[:k] + "," + text[k:]), f"{sec}: insert a comma at {k}"
    if cls == "duplicate-key":
        k = rng.choice(keys)
        pair = json.dumps(k) + ":" + json.dumps(obj[k], ensure_ascii=False, separators=(",", ":"))
        return put(text=text[:-1] + "," + pair + "}"), f"{sec}: repeat {k}"
    raise KeyError(cls)


# ---------------------------------------------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------------------------------------------

CORPUS_SPEC = (("tiny", 0), ("small", 1), ("small", 2), ("machine", 3), ("insert-element", 3), ("rich-fields", 5), ("cjk", 1), ("odd-ids", 2))


def build_corpus(encoder):
    corpus = []
    for name, seed in CORPUS_SPEC:
        model = synth.generate(name, seed) if name in ("tiny", "small") else synth.edge_case(name, seed)
        files = encoder(model)
        ex = expand.expand_sources(files, encode.meta_of(model), stamps=encode.stamps_of(model))
        assert ex.diagnostics == [] and ex.model == model, (name, seed)
        full = expand.expand_sources(files, encode.meta_of(model), stamps=encode.stamps_of(model), closed_world=True)
        assert full.all_diagnostics == [], (name, seed)
        corpus.append((f"{name}-{seed}", model, files, am.canonical_bytes(ex.model)))
    return corpus


def run_class(rng, corpus, mutator, cls: str, count: int, split: str) -> dict:
    result = {"done": 0, "crash": 0, "changed": 0, "flagged": 0, "silent_structural": 0, "silent_closed": 0, "examples": []}
    tries = 0
    while result["done"] < count and tries < count * 60:
        tries += 1
        label, model, files, base = rng.choice(corpus)
        fname = rng.choice(sorted(files))
        lines = files[fname].split("\n")
        mutated = mutator(rng, lines, cls)
        if mutated is None:
            continue
        new_lines, description = mutated
        new_files = dict(files)
        new_files[fname] = "\n".join(new_lines)
        result["done"] += 1
        try:
            ex = expand.expand_sources(new_files, encode.meta_of(model), stamps=encode.stamps_of(model), closed_world=True)
            try:
                changed = am.canonical_bytes(ex.model) != base
            except am.AtlasModelError:
                changed = True
        except Exception as ex_:  # noqa: BLE001 - the gate is "never crashes"
            result["crash"] += 1
            result["examples"].append((label, fname, description, f"CRASH {type(ex_).__name__}: {ex_}"))
            continue
        result["changed"] += changed
        if changed and ex.all_diagnostics:
            result["flagged"] += 1
        if changed and not ex.diagnostics:
            result["silent_structural"] += 1
            if not ex.all_diagnostics:
                result["silent_closed"] += 1
                if len(result["examples"]) < 3:
                    before = [a for a, b in zip(lines, new_lines) if a != b][:1]
                    result["examples"].append((label, fname, description, before))
    return result


def report(title: str, results: dict) -> None:
    print(f"\n[{title}] seed={SEED}")
    for cls, r in results.items():
        print(f"  {cls:18s} mutated={r['done']:4d} changed={r['changed']:4d} flagged={r['flagged']:4d} "
              f"silent(structural)={r['silent_structural']:3d} silent(closed)={r['silent_closed']:3d} crash={r['crash']}")


class AtlFuzzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(SEED)
        corpus = build_corpus(encode.encode_atl)
        cls.results = {name: run_class(rng, corpus, mutate_atl, name, N, "atl") for name in GUARDED + INFORMATIONAL}
        report(".atl fuzz", cls.results)

    def test_at_least_500_rows_over_at_least_seven_classes(self):
        guarded = {c: self.results[c]["done"] for c in GUARDED}
        self.assertGreaterEqual(len(guarded), 7)
        self.assertGreaterEqual(sum(guarded.values()), 500 if N >= 50 else 0)
        for cls_name, done in guarded.items():
            self.assertGreaterEqual(done, min(N, 30), cls_name)

    def test_no_mutation_crashes_the_parser_or_the_expander(self):
        for cls_name, r in self.results.items():
            self.assertEqual(r["crash"], 0, (cls_name, r["examples"]))

    def test_silent_swallow_rate_is_zero_for_every_guarded_class(self):
        for cls_name in GUARDED:
            r = self.results[cls_name]
            self.assertEqual(r["silent_closed"], 0, (cls_name, r["examples"]))
            if cls_name not in CLOSED_BY_REFERENCES_ONLY:
                self.assertEqual(r["silent_structural"], 0, (cls_name, r["examples"]))

    def test_the_guarded_classes_actually_change_models(self):
        """A class that never changed a model would pass the gate vacuously."""
        for cls_name in GUARDED:
            self.assertGreater(self.results[cls_name]["changed"], 0, cls_name)

    def test_informational_classes_are_measured(self):
        for cls_name in INFORMATIONAL:
            self.assertGreater(self.results[cls_name]["done"], 0, cls_name)


class JsonlFuzzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(SEED + 1)
        corpus = build_corpus(encode.encode_jsonl)
        count = max(N // 2, 10)
        cls.results = {name: run_class(rng, corpus, mutate_jsonl, name, count, "jsonl") for name in JSONL_GUARDED}
        report(".jsonl fuzz", cls.results)

    def test_no_mutation_crashes_the_parser_or_the_expander(self):
        for cls_name, r in self.results.items():
            self.assertEqual(r["crash"], 0, (cls_name, r["examples"]))

    def test_silent_swallow_rate_is_zero(self):
        for cls_name, r in self.results.items():
            self.assertEqual(r["silent_closed"], 0, (cls_name, r["examples"]))
            if cls_name not in CLOSED_BY_REFERENCES_ONLY:
                self.assertEqual(r["silent_structural"], 0, (cls_name, r["examples"]))

    def test_classes_apply(self):
        for cls_name, r in self.results.items():
            self.assertGreater(r["done"], 0, cls_name)


class GarbageTests(unittest.TestCase):
    """Raw character-level damage anywhere in a file: the parsers and the expander never raise."""

    def test_random_character_damage_never_raises(self):
        rng = random.Random(SEED + 2)
        model = synth.generate("small", 1)
        corpora = (encode.encode_atl(model), encode.encode_jsonl(model))
        alphabet = ["|", "\\", '"', "{", "}", "[", "]", "=", " ", "\t", "\n", "\r", "~", "@", "#", "//", "\x00", "﻿", "é", " ", ",", ":", "^"]
        count = 0
        for files in corpora:
            for _ in range(120):
                fname = rng.choice(sorted(files))
                text = files[fname]
                for _ in range(rng.randint(1, 6)):
                    k = rng.randrange(len(text) + 1)
                    action = rng.randrange(3)
                    if action == 0:
                        text = text[:k] + rng.choice(alphabet) + text[k:]
                    elif action == 1:
                        text = text[:k] + text[k + rng.randint(1, 20):]
                    else:
                        j = rng.randrange(len(text) + 1)
                        a, b = sorted((k, j))
                        text = text[:a] + text[a:b][::-1] + text[b:]
                new = dict(files)
                new[fname] = text
                ex = expand.expand_sources(new, model["meta"])
                self.assertIsInstance(ex.model, dict)
                count += 1
        self.assertGreaterEqual(count, 240)

    def test_truncated_files_never_raise(self):
        model = synth.generate("small", 2)
        for files in (encode.encode_atl(model), encode.encode_jsonl(model)):
            name = sorted(files)[1]
            text = files[name]
            for cut in range(0, len(text), max(len(text) // 80, 1)):
                new = dict(files)
                new[name] = text[:cut]
                expand.expand_sources(new, model["meta"])

    def test_every_single_line_deleted_or_doubled_is_detected_or_equal(self):
        """Deleting a header line or doubling a row either changes nothing or is reported (spot check on the tiny model)."""
        model = synth.lending_desk("fixed")
        files = encode.encode_atl(model)
        base = am.canonical_bytes(expand.expand_sources(files, model["meta"]).model)
        silent = 0
        for fname, text in files.items():
            lines = text.split("\n")
            for k, line in enumerate(lines):
                if not line.startswith("## "):
                    continue
                new = dict(files)
                new[fname] = "\n".join(lines[:k] + lines[k + 1:])
                ex = expand.expand_sources(new, model["meta"])
                if am.canonical_bytes(ex.model) != base and not ex.diagnostics:
                    silent += 1
        self.assertEqual(silent, 0)


if __name__ == "__main__":
    unittest.main()
