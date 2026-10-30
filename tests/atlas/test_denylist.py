"""Hashed denylist: no string of the reference system may live in the atlas files, not even in a test.

``tests/atlas/denylist.sha256`` holds SHA-256 digests of lower-cased tokens (domain nouns, company, product and
repository names, whole id-shaped strings, file and directory names, Japanese and Chinese domain terms) taken from the
reference by a maintainer script that is kept OUTSIDE this repository. This test tokenises every text file under the
scan roots (``SCAN_ROOTS``: the atlas package, all of ``tests/``, ``docs/atlas/``, ``skills/gemarc-atlas/``, ``tools/``
and ``api/``) AND every file git reports as untracked or modified anywhere in the repository (``git ls-files -mo
--exclude-standard``), and fails on any digest hit. A file is text when it has no NUL byte and decodes as UTF-8 (no
suffix whitelist). A hit is reported as ``path:line`` plus a digest prefix, never as the token. Two more checks keep
the guard honest: the digest file is pinned (its sha256 and its entry count, see ``PIN``) so a replaced or truncated
list cannot pass, and no untracked file may sit directly in the repository root.

Tokenisation (``tokenize``), after NFKC normalisation and removal of zero-width format characters: lower-cased ASCII
alphanumeric runs, the camelCase pieces of each run, letter+digit neighbours fused (``Ab123`` also gives ``ab123``),
whole id-shaped compounds (``word.word-x``, ``a:b:c``) and every 2-, 3- and 4-gram of each run of kanji / kana
characters. The maintainer script imports ``tokenize`` from this file, so both sides always agree. Known limit: a
reference word glued to other lower-case letters without any separator or case change (``fooadapter``) is one run and
is only caught when that whole run is on the list.

If a legitimate generic word is flagged (for example a Japanese label a locale table needs), do NOT edit the digest
file: add the word to the exempt list of the maintainer script and regenerate (see ``tests/atlas/README.md``).

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_denylist*.py"
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
DENYLIST = REPO / "tests" / "atlas" / "denylist.sha256"
SCAN_ROOTS = ("src/gemarc_mcp/atlas", "tests", "docs/atlas", "skills/gemarc-atlas", "tools", "api")
#: Directories that are never scanned (virtual environments, git internals, caches, dependency trees, deploy state).
SKIP_PARTS = {"__pycache__", ".git", ".venv", "node_modules", ".vercel", ".pytest_cache"}
ASCII_RUN = re.compile(r"[A-Za-z0-9]+")
CAMEL_PIECE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
#: An id-shaped compound: letters and digits joined by ``.``, ``-`` or ``:`` (``word.word-x``, ``a:b:c``).
COMPOUND = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-:][A-Za-z0-9]+)+")
CJK_RUN = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Pin of ``denylist.sha256``: the sha256 of its bytes and its number of digest lines (without the canary line).
#: Update both ONLY after the maintainer rebuilt the list: ``python tests/atlas/test_denylist.py --print-pin``.
PIN = {"sha256": "86b793958e40036e706f2d8936639677b641f64b70217adc41a848863e1d1fcc", "digests": 6211}
#: Digests of two reference tokens that must be on the list (a list made of random digests cannot contain them).
#: Only the hex values are written here, never the words.
PROBE_DIGESTS = ("9261ceef0b969e70ac20f1510f07a1e0d8db05f20c75161a2ef43b4eba27a7aa", "211074b6238601653abaed100a2a70e6c28dc2b9febd9564b50155e0878d4ffe")
#: Untracked files that may sit directly in the repository root (none: a stray file there once held reference data).
ROOT_UNTRACKED_ALLOWED: tuple[str, ...] = ()


def canary_token() -> str:
    """The self-test token; assembled at run time so that the literal never appears in a repository file."""
    return "zz" + "atlas" + "canary" + "zz"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalise(text: str) -> str:
    """NFKC (full-width Latin and half-width kana become their plain forms), then drop zero-width format characters."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def tokenize(text: str) -> set[str]:
    """Lower-cased tokens of a text: ASCII runs, camelCase pieces, fused letter+digit pieces, id compounds, CJK 2-4-grams."""
    text = _normalise(text)
    tokens: set[str] = set()
    for run in ASCII_RUN.findall(text):
        tokens.add(run.lower())
        pieces = CAMEL_PIECE.findall(run)
        for piece in pieces:
            tokens.add(piece.lower())
        for left, right in zip(pieces, pieces[1:]):
            if left.isdigit() != right.isdigit():  # a letter piece next to a digit piece: Ab123 -> ab123
                tokens.add((left + right).lower())
    for compound in COMPOUND.findall(text):
        tokens.add(compound.lower())
    for run in CJK_RUN.findall(text):
        for size in (2, 3, 4):
            for start in range(len(run) - size + 1):
                tokens.add(run[start:start + size])
    return tokens


def load_denylist(path: Path = DENYLIST) -> tuple[frozenset[str], str | None]:
    """``(token digests, canary digest)``; comment lines start with ``#``, the canary line with ``canary ``."""
    digests: set[str] = set()
    canary = None
    for raw in path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("canary "):
            canary = line.split(None, 1)[1].strip()
            continue
        digests.add(line)
    return frozenset(digests), canary


def iter_scan_files(root: Path = REPO, roots: tuple[str, ...] = SCAN_ROOTS, extra: Iterable[str] = ()):
    """Files of the scan roots plus the repository-relative paths in ``extra`` (binary files are skipped by ``read_lines``)."""
    seen: set[Path] = set()
    candidates: list[Path] = []
    for name in roots:
        base = root / name
        if base.is_dir():
            candidates.extend(sorted(base.rglob("*")))
    candidates.extend(root / name for name in extra)
    for path in candidates:
        if path in seen or not path.is_file() or path.name == DENYLIST.name or SKIP_PARTS & set(path.relative_to(root).parts):
            continue
        seen.add(path)
        yield path


def git_delta(root: Path = REPO) -> list[str] | None:
    """Repository-relative paths git reports as modified or untracked (and not ignored); ``None`` when git cannot say."""
    try:
        done = subprocess.run(["git", "ls-files", "-mo", "--exclude-standard", "-z"], cwd=root, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({name for name in done.stdout.decode("utf-8", "replace").split("\0") if name})


def git_untracked(root: Path = REPO) -> list[str] | None:
    try:
        done = subprocess.run(["git", "ls-files", "-o", "--exclude-standard", "-z"], cwd=root, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({name for name in done.stdout.decode("utf-8", "replace").split("\0") if name})


def read_lines(path: Path) -> list[str] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    except UnicodeDecodeError:
        return None


def scan(digests: frozenset[str], root: Path = REPO, roots: tuple[str, ...] = SCAN_ROOTS,
         extra: Iterable[str] = ()) -> list[tuple[str, int, str]]:
    """``(relative path, line number, digest)`` of every token whose digest is in ``digests`` (token never returned)."""
    hits: list[tuple[str, int, str]] = []
    cache: dict[str, str] = {}
    for path in iter_scan_files(root, roots, extra):
        lines = read_lines(path)
        if lines is None:
            continue
        relative = path.relative_to(root).as_posix()
        for number, line in enumerate(lines, start=1):
            for token in tokenize(line):
                if len(token) < 2:
                    continue
                value = cache.get(token)
                if value is None:
                    value = cache[token] = digest(token)
                if value in digests:
                    hits.append((relative, number, value))
    return sorted(set(hits))


def pin_problems(path: Path = DENYLIST, pin: dict | None = None, probes: tuple[str, ...] | None = None) -> list[str]:
    """Why ``path`` is not the digest list the maintainer pinned (empty list = it is)."""
    pin = PIN if pin is None else pin
    probes = PROBE_DIGESTS if probes is None else probes
    problems: list[str] = []
    data = path.read_bytes()
    digests, canary = load_denylist(path)
    if hashlib.sha256(data).hexdigest() != pin["sha256"]:
        problems.append("the sha256 of the file differs from PIN['sha256']")
    if len(digests) != pin["digests"]:
        problems.append(f"{len(digests)} digests, the pin says {pin['digests']}")
    for index, probe in enumerate(probes):
        if probe not in digests:
            problems.append(f"known reference digest #{index} is missing")
    if canary != digest(canary_token()):
        problems.append("the canary line is wrong")
    return problems


def current_pin(path: Path = DENYLIST) -> dict:
    digests, _ = load_denylist(path)
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "digests": len(digests)}


def report(hits: list[tuple[str, int, str]]) -> str:
    return "\n".join(f"{path}:{line}  digest {value[:12]}" for path, line, value in hits)


class TokenizerTests(unittest.TestCase):
    def test_ascii_runs_camel_pieces_and_cjk_grams(self):
        self.assertEqual(tokenize("Hello_World42 fooBarBaz"), {"hello", "world42", "world", "42", "foobarbaz", "foo", "bar", "baz"})
        self.assertEqual(tokenize("HTTPServer-v2"), {"httpserver", "http", "server", "v2", "v", "2", "httpserver-v2"})
        grams = tokenize("abサーバーxy")
        self.assertIn("サー", grams)
        self.assertIn("サーバー", grams)
        self.assertEqual({g for g in grams if not g.isascii()}, {"サー", "ーバ", "バー", "サーバ", "ーバー", "サーバー"})
        self.assertNotIn("abサ", grams)

    def test_single_cjk_characters_and_punctuation_make_no_gram(self):
        self.assertEqual({g for g in tokenize("顧, 客") if not g.isascii()}, set())

    def test_lowercasing_and_separators(self):
        self.assertEqual(tokenize("A.B-C/D:E"), {"a", "b", "c", "d", "e", "a.b-c", "d:e"})  # the id-shaped compounds a.b-c and d:e
        self.assertIn("serviceprovider", tokenize("ServiceProvider"))
        self.assertTrue({"service", "provider"} <= tokenize("ServiceProvider"))

    def test_canary_literal_is_assembled_at_run_time(self):
        self.assertEqual(len(canary_token()), 15)
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn(canary_token(), source, "the canary literal must never be written in a repository file")


class DenylistFileTests(unittest.TestCase):
    def test_file_is_well_formed(self):
        self.assertTrue(DENYLIST.is_file(), "tests/atlas/denylist.sha256 is missing; build it with the maintainer script (see README)")
        digests, canary = load_denylist()
        self.assertGreaterEqual(len(digests), 100, "the denylist looks truncated")
        self.assertTrue(all(HEX64.match(d) for d in digests))
        self.assertIsNotNone(canary, "the canary entry is missing")
        self.assertTrue(HEX64.match(canary))
        self.assertEqual(canary, digest(canary_token()))
        lines = [line for line in DENYLIST.read_text(encoding="utf-8").split("\n") if line and not line.startswith("#") and not line.startswith("canary ")]
        self.assertEqual(len(lines), len(set(lines)), "duplicate digests")
        self.assertEqual(lines, sorted(lines), "digests are written in sorted order")

    def test_file_has_lf_newlines_and_no_bom(self):
        data = DENYLIST.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))

    def test_the_canary_digest_is_kept_on_its_own_line(self):
        digests, canary = load_denylist()
        self.assertNotIn(canary, digests, "the canary digest is kept on its own line")


class TokenizerHardeningTests(unittest.TestCase):
    def test_full_width_letters_and_zero_width_characters_do_not_hide_a_word(self):
        self.assertIn("abcd", tokenize("\uff21\uff22\uff23\uff24"))
        self.assertIn("abcd", tokenize("ab\u200bcd"))
        self.assertIn("abcd", tokenize("a\u2060b\ufeffcd"))
        self.assertIn("abcd", tokenize("a\u00adbcd"))  # soft hyphen
        self.assertIn("\u30b5\u30fc\u30d0", tokenize("\uff7b\uff70\uff8a\uff9e"))  # half-width katakana become full width

    def test_letter_and_digit_neighbours_are_also_emitted_fused(self):
        tokens = tokenize("MyAb123Adapter")
        self.assertTrue({"myab123adapter", "my", "ab", "123", "adapter", "ab123"} <= tokens, tokens)
        self.assertIn("123adapter", tokens)
        self.assertNotIn("my123", tokens)

    def test_id_shaped_compounds_are_tokens(self):
        tokens = tokenize("see xx.order-create:7 and a:b:c, x.y.")
        self.assertTrue({"xx.order-create:7", "a:b:c", "x.y"} <= tokens, tokens)
        self.assertNotIn("x.y.", tokens)
        self.assertNotIn("1.2.3", tokenize("version 1.2.3"))  # a compound starts with a letter
        self.assertIn("web.api-v2", tokenize("Web.API-v2"))  # lower-cased
        self.assertTrue({"order", "create"} <= tokenize("xx.order-create"))  # the pieces stay tokens too

    def test_a_word_glued_to_other_lower_case_letters_is_a_known_limit(self):
        self.assertNotIn("foo", tokenize("fooadapter"))
        self.assertIn("fooadapter", tokenize("fooadapter"))


class DigestFileIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-pin-"))
        self.addCleanup(shutil.rmtree, self.temp, True)

    def write(self, name: str, lines: list[str]) -> Path:
        path = self.temp / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return path

    def test_the_shipped_list_is_the_pinned_one(self):
        self.assertEqual(pin_problems(), [], "denylist.sha256 is not the list that PIN describes; after a maintainer rebuild "
                                              "run `python tests/atlas/test_denylist.py --print-pin` and update PIN")
        self.assertEqual(current_pin(), PIN)

    def test_the_pin_has_real_values(self):
        self.assertRegex(PIN["sha256"], HEX64)
        self.assertNotEqual(PIN["sha256"], "0" * 64)
        self.assertGreater(PIN["digests"], 5000)
        for probe in PROBE_DIGESTS:
            self.assertRegex(probe, HEX64)
            self.assertNotEqual(probe, "0" * 64)
        self.assertEqual(len(set(PROBE_DIGESTS)), 2)

    def test_the_two_probe_digests_are_on_the_list_and_not_written_as_words(self):
        digests, _ = load_denylist()
        for probe in PROBE_DIGESTS:
            self.assertIn(probe, digests)

    def test_a_list_of_random_digests_with_the_right_canary_is_rejected(self):
        lines = [hashlib.sha256(f"random {i}".encode()).hexdigest() for i in range(PIN["digests"])]
        lines.sort()
        fake = self.write("random.sha256", lines + [f"canary {digest(canary_token())}"])
        problems = pin_problems(fake)
        self.assertTrue(any("sha256" in problem for problem in problems), problems)
        self.assertTrue(any("known reference digest" in problem for problem in problems), problems)

    def test_a_truncated_or_edited_copy_is_rejected(self):
        original = DENYLIST.read_text(encoding="utf-8").split("\n")
        body = [line for line in original if line and not line.startswith("#") and not line.startswith("canary ")]
        truncated = self.write("truncated.sha256", body[: len(body) // 2] + [f"canary {digest(canary_token())}"])
        self.assertTrue(pin_problems(truncated))
        without_probe = self.write("noprobe.sha256", [line for line in body if line != PROBE_DIGESTS[0]] + [f"canary {digest(canary_token())}"])
        self.assertTrue(any("missing" in problem for problem in pin_problems(without_probe)))
        bad_canary = self.write("canary.sha256", body + ["canary " + "f" * 64])
        self.assertTrue(any("canary" in problem for problem in pin_problems(bad_canary)))

    def test_planting_reference_words_next_to_a_fake_list_is_still_caught_by_the_real_one(self):
        """The scan that matters uses the shipped list: a replaced file fails the pin test above, and the shipped list flags the canary."""
        digests, canary = load_denylist()
        self.assertEqual(scan(digests | {canary}, self.temp), [])
        base = self.temp / "docs" / "atlas"
        base.mkdir(parents=True)
        (base / "x.md").write_text(canary_token() + "\n", encoding="utf-8")
        self.assertEqual([(a, b) for a, b, _ in scan(digests | {canary}, self.temp)], [("docs/atlas/x.md", 1)])


class ScanReachTests(unittest.TestCase):
    """The scan reads every non-binary file under every root, plus whatever git reports as untracked or modified."""

    @classmethod
    def setUpClass(cls):
        cls.digests, cls.canary = load_denylist()
        cls.all_digests = frozenset(cls.digests | {cls.canary})

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-reach-"))
        self.addCleanup(shutil.rmtree, self.temp, True)

    def plant(self, relative: str, text: str | None = None, data: bytes | None = None) -> None:
        path = self.temp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if data is not None else ((text if text is not None else "note " + canary_token() + " end") + "\n").encode("utf-8"))

    def found(self, **kwargs) -> list[str]:
        return [name for name, _, _ in scan(self.all_digests, self.temp, **kwargs)]

    def test_every_scan_root_is_read(self):
        for root in SCAN_ROOTS:
            self.plant(f"{root}/planted.txt")
        self.assertEqual(sorted(self.found()), sorted(f"{root}/planted.txt" for root in SCAN_ROOTS))
        for root in ("tools", "api", "tests", "skills/gemarc-atlas"):
            self.assertIn(root, SCAN_ROOTS)

    def test_the_whole_tests_tree_is_scanned_not_only_the_atlas_folder(self):
        self.plant("tests/test_atlas_future.py")
        self.plant("tests/atlas/inner.py")
        self.assertEqual(sorted(self.found()), ["tests/atlas/inner.py", "tests/test_atlas_future.py"])

    def test_the_file_suffix_does_not_matter(self):
        names = ["a.svg", "b.sh", "c.ps1", "d.xml", "e.ts", "f.tsx", "g.cjs", "h.jsx", "i.mdx", "j.htm", "k.tmpl", "l.j2", "NOEXT", "m.PY", "n.lock", "o.env"]
        for name in names:
            self.plant(f"tools/{name}")
        self.assertEqual(sorted(self.found()), sorted(f"tools/{name}" for name in names))

    def test_binary_and_non_utf8_files_and_virtual_environments_are_skipped(self):
        self.plant("tools/blob.bin", data=b"\x00" + canary_token().encode())
        self.plant("tools/latin.txt", data=b"\xff\xfe " + canary_token().encode())
        self.plant("tools/.venv/lib/x.py")
        self.plant("tools/node_modules/p/x.js")
        self.plant("tools/__pycache__/x.py")
        self.plant("tools/.git/x")
        self.assertEqual(self.found(), [])

    def test_utf8_with_a_bom_and_crlf_is_read(self):
        self.plant("tools/crlf.md", data=b"\xef\xbb\xbfa\r\n" + canary_token().encode() + b"\r\n")
        self.assertEqual(self.found(), ["tools/crlf.md"])

    def test_extra_paths_outside_the_roots_are_scanned_too(self):
        self.plant("props_stray.json")
        self.plant("deep/elsewhere/file.txt")
        self.plant("ignored/.venv/x.txt")
        self.assertEqual(self.found(), [])
        self.assertEqual(sorted(self.found(extra=["props_stray.json", "deep/elsewhere/file.txt", "ignored/.venv/x.txt", "missing.txt"])),
                         ["deep/elsewhere/file.txt", "props_stray.json"])

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.temp, check=True, capture_output=True, timeout=60)

    def test_git_delta_lists_untracked_and_modified_files_and_the_scan_flags_them(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.plant("tracked.md", "clean text")
        self.git("add", "tracked.md")
        self.git("commit", "-q", "-m", "x")
        self.plant("stray_at_root.json")
        self.plant("tracked.md")  # modified now
        self.plant("sub/new.txt")
        self.plant(".gitignore", "ignored.txt")
        self.plant("ignored.txt")
        delta = git_delta(self.temp)
        self.assertEqual(delta, [".gitignore", "stray_at_root.json", "sub/new.txt", "tracked.md"])
        self.assertEqual(sorted(self.found(roots=(), extra=delta)), ["stray_at_root.json", "sub/new.txt", "tracked.md"])
        self.assertEqual(git_untracked(self.temp), [".gitignore", "stray_at_root.json", "sub/new.txt"])

    def test_git_helpers_return_none_outside_a_repository(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available")
        outside = Path(tempfile.mkdtemp(prefix="gemarc-not-a-repo-"))
        self.addCleanup(shutil.rmtree, outside, True)
        env_marker = outside / "x"
        env_marker.write_text("x", encoding="utf-8")
        if subprocess.run(["git", "rev-parse"], cwd=outside, capture_output=True).returncode == 0:
            self.skipTest("the temp directory sits inside a git work tree")
        self.assertIsNone(git_delta(outside))
        self.assertIsNone(git_untracked(outside))


class LiveRepositoryTests(unittest.TestCase):
    """Whatever the working tree holds right now: new, modified or stray files anywhere, not only inside the scan roots."""

    @classmethod
    def setUpClass(cls):
        digests, canary = load_denylist()
        cls.all_digests = frozenset(digests | {canary})

    def test_untracked_and_modified_files_anywhere_in_the_repository_are_clean(self):
        delta = git_delta()
        if delta is None:
            self.skipTest("git cannot list the working tree here")
        hits = scan(self.all_digests, REPO, (), delta)
        self.assertEqual(hits, [], "denylisted tokens in files git reports as new or modified (digests only):\n" + report(hits))

    def test_no_untracked_file_sits_directly_in_the_repository_root(self):
        untracked = git_untracked()
        if untracked is None:
            self.skipTest("git cannot list the working tree here")
        strays = [name for name in untracked if "/" not in name and name not in ROOT_UNTRACKED_ALLOWED]
        self.assertEqual(strays, [], "untracked files in the repository root (a stray file once held reference data there): " + ", ".join(strays))

    def test_the_two_git_based_checks_cover_the_deployed_surface(self):
        # the root, api/ and tools/ are not under SCAN_ROOTS' atlas folders; they are covered by roots or by the git delta
        self.assertTrue({"api", "tools", "tests"} <= set(SCAN_ROOTS))


class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digests, cls.canary = load_denylist()
        cls.all_digests = frozenset(cls.digests | {cls.canary})

    def test_no_file_of_the_atlas_contains_a_denylisted_token(self):
        hits = scan(self.all_digests)
        self.assertEqual(hits, [], "denylisted tokens found (digests only):\n" + report(hits))

    def test_the_scan_covers_code_tests_fixtures_and_docs(self):
        scanned = {p.relative_to(REPO).as_posix() for p in iter_scan_files()}
        self.assertGreater(len(scanned), 10)
        self.assertIn("tools/gen_provenance.py", scanned)
        self.assertTrue(any(name.startswith("tests/test_") for name in scanned), "pre-existing tests are scanned too")
        self.assertIn("tests/atlas/fixtures/lending_desk.model.json", scanned)
        self.assertIn("tests/atlas/synth.py", scanned)
        self.assertIn("src/gemarc_mcp/atlas/model.py", scanned)
        self.assertIn("src/gemarc_mcp/atlas/profiles/software.json", scanned)
        self.assertIn("docs/atlas/spec/model-schema.md", scanned)
        self.assertNotIn("tests/atlas/denylist.sha256", scanned)

    def test_the_scan_proves_itself_with_a_planted_canary(self):
        """Plant the canary in a temp copy of every scanned file kind and require the scan to fail on it only."""
        temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-test-"))
        self.addCleanup(shutil.rmtree, temp, True)
        for name in SCAN_ROOTS:
            source = REPO / name
            if source.is_dir():
                shutil.copytree(source, temp / name, ignore=shutil.ignore_patterns("__pycache__"))
        clean = scan(self.all_digests, temp)
        self.assertEqual(clean, [], "the temp copy must scan clean before the canary is planted")
        target = temp / "tests" / "atlas" / "fixtures" / "planted.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{\n  "note": "harmless",\n  "value": "see ' + canary_token().upper() + ' here"\n}\n', encoding="utf-8")
        hits = scan(self.all_digests, temp)
        self.assertEqual([(p, n) for p, n, _ in hits], [("tests/atlas/fixtures/planted.json", 3)])
        self.assertEqual({d for _, _, d in hits}, {self.canary})
        message = report(hits)
        self.assertNotIn(canary_token(), message.lower(), "a report never prints the token")
        self.assertIn("tests/atlas/fixtures/planted.json:3", message)

    def test_a_planted_canary_in_a_docstring_comment_and_markdown_is_found_too(self):
        temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-test-"))
        self.addCleanup(shutil.rmtree, temp, True)
        base = temp / "src" / "gemarc_mcp" / "atlas"
        base.mkdir(parents=True)
        word = canary_token()
        (base / "a.py").write_text('"""doc"""\n# comment ' + word + "\nx = 1\n", encoding="utf-8")
        (base / "b.md").write_text("# title\n\n`" + word.capitalize() + "` is here\n", encoding="utf-8")
        (base / "c.tsv").write_text("p\tq\n" + word + "-x\t-\n", encoding="utf-8")
        (base / "skip.bin").write_bytes(b"\x00" + word.encode())
        hits = scan(self.all_digests, temp)
        self.assertEqual([(p, n) for p, n, _ in hits],
                         [("src/gemarc_mcp/atlas/a.py", 2), ("src/gemarc_mcp/atlas/b.md", 3), ("src/gemarc_mcp/atlas/c.tsv", 2)])

    def test_missing_scan_roots_are_skipped(self):
        temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-test-"))
        self.addCleanup(shutil.rmtree, temp, True)
        self.assertEqual(scan(self.all_digests, temp), [])

    def test_cjk_grams_are_scanned(self):
        # a digest of a 3-gram planted in kanji text must be found (any 2-4-gram of a run is checked)
        temp = Path(tempfile.mkdtemp(prefix="gemarc-denylist-test-"))
        self.addCleanup(shutil.rmtree, temp, True)
        base = temp / "docs" / "atlas"
        base.mkdir(parents=True)
        (base / "x.md").write_text("模型校验器です\n", encoding="utf-8")
        wanted = digest("校验器")
        hits = scan(frozenset({wanted}), temp)
        self.assertEqual([(p, n) for p, n, _ in hits], [("docs/atlas/x.md", 1)])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--print-pin":
        pin = current_pin()
        print(f'PIN = {{"sha256": "{pin["sha256"]}", "digests": {pin["digests"]}}}')
        print("PROBE_DIGESTS stay as they are (two digests of reference tokens that every rebuild keeps).")
    else:
        unittest.main()
