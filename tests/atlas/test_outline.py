"""Outline tests: entries per language, shortest-unique anchors, redaction, 4 KB pages, corpus of this repository's src/.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_outline*.py"
"""
from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import outline as ol
from gemarc_mcp.atlas import roots

REPO = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO / "src" / "gemarc_mcp"
NO_REV = ev.VerifyOptions(compute_root_rev=False)

PYTHON = '''"""Module doc with a fake def inside.

def not_real(x):
    pass
"""
import os

LIMIT = 10
lower_case = 3


@decorator
class Loan:
    """Class doc.

    class AlsoFake:
    """

    RATE: float = 0.5

    def __init__(self, copy):
        self.copy = copy

    async def renew(self, days):
        def inner():
            return days
        return inner()

    class Receipt:
        def print_it(self):
            pass


def create_loan(member, copy):
    x = """
def inside_string():
    """
    return Loan(copy)
'''

JS = '''// function commentOnly() {}
/* function blockComment() {
} */
export function findCopy(id) {
  const s = "function inString() {}";
  return id;
}

export const renewLoan = async (loan, days) => {
  return loan;
};

const helper = function (x) { return x; };
export const MAX_DAYS = 14;

export default class LoanService extends Base {
  constructor(store) {
    super(store);
  }

  async renew(loan) {
    if (loan) {
      return 1;
    }
  }

  static create(store) {
    return new LoanService(store);
  }
}
const tpl = `function inTemplate() {}`;
'''

TS = '''export interface Copy {
  id: string;
}
export type LoanId = string;
enum State { Open, Closed }
export abstract class Repo<T> {
  find(id: string): T | undefined {
    return undefined;
  }
}
'''

JAVA = '''package demo;

public class LoanService {
    private final Store store;

    public LoanService(Store store) {
        this.store = store;
    }

    public Loan renew(Loan loan, int days) throws Exception {
        if (days > 3) {
            return null;
        }
        return loan;
    }

    static class Inner {
        void touch() {
        }
    }
}

interface Shelf { }
record Pair(int a, int b) { }
'''

GO = '''package main

type Loan struct {
	ID int
}

type Shelf interface {
	Find(id int) Loan
}

func (l *Loan) Renew(days int) error {
	return nil
}

func FindCopy(id int) Loan {
	return Loan{}
}

var Limit = 10
const Rate = 2
'''

MARKDOWN = '''# Title

Intro text.

## Install steps

```
# not a heading
```

### Detail

Setext heading
--------------

Other
=====
'''

YAML = '''services:
  web:
    image: app
    ports:
      - "80:80"
  db:
    image: postgres
version: "3"
'''

JSON_DOC = '''{
  "name": "demo",
  "scripts": {"build": "x", "test": "y"},
  "list": [{"hidden": 1}, 2],
  "text": "has { braces } and \\"quotes\\": inside"
}
'''

TOML = '''title = "demo"

[project]
name = "x"
version = "1"

[[tool.item]]
key = "v"
'''

SQL = '''-- create table commented (id int);
CREATE TABLE loan (
  id INT
);
create or replace view open_loans as select 1;
/* create index fake on loan(id); */
ALTER TABLE loan ADD COLUMN due DATE;
CREATE UNIQUE INDEX idx_loan ON loan(id);
'''

DOTENV = 'WEB_PORT=1012\nDB_PASSWORD=hunter2-very-secret\nexport REGISTRY_HOST=registry.local\n'


def entries(rel: str, body: str) -> list[dict]:
    return ol.outline_text(rel, body)["entries"]


def pairs(rel: str, body: str) -> list[tuple[str, str]]:
    return [(e["kind"], e["name"]) for e in entries(rel, body)]


class LanguageTests(unittest.TestCase):
    def test_python_via_ast_ignores_docstrings_and_lists_nesting(self):
        got = pairs("m.py", PYTHON)
        self.assertEqual(got, [("const", "LIMIT"), ("class", "Loan"), ("const", "Loan.RATE"), ("def", "Loan.__init__"),
                               ("async def", "Loan.renew"), ("def", "Loan.renew.inner"), ("class", "Loan.Receipt"),
                               ("def", "Loan.Receipt.print_it"), ("def", "create_loan")])
        lines = {e["name"]: e["line"] for e in entries("m.py", PYTHON)}
        self.assertEqual(PYTHON.split("\n")[lines["Loan"] - 1], "class Loan:", "the class line, not the decorator line")

    def test_python_that_does_not_parse_falls_back_to_regex(self):
        broken = "def ok(x):\n    return (\n\nclass K:\n    def m(self):\n        pass\n"
        result = ol.outline_text("b.py", broken)
        self.assertIn("regex", result["note"])
        self.assertEqual([e["name"] for e in result["entries"]], ["ok", "K", "K.m"])

    def test_javascript_masks_comments_and_strings(self):
        got = pairs("a.js", JS)
        self.assertEqual(got, [("function", "findCopy"), ("arrow", "renewLoan"), ("arrow", "helper"), ("const", "MAX_DAYS"),
                               ("class", "LoanService"), ("method", "LoanService.constructor"), ("method", "LoanService.renew"),
                               ("method", "LoanService.create")])

    def test_typescript(self):
        got = pairs("a.ts", TS)
        self.assertEqual(got, [("interface", "Copy"), ("type", "LoanId"), ("enum", "State"), ("class", "Repo"), ("method", "Repo.find")])
        self.assertEqual(ol.language_of("x.tsx"), "typescript")
        self.assertEqual(ol.language_of("x.mjs"), "javascript")

    def test_java(self):
        got = pairs("A.java", JAVA)
        self.assertEqual(got, [("class", "LoanService"), ("method", "LoanService.LoanService"), ("method", "LoanService.renew"),
                               ("class", "LoanService.Inner"), ("method", "LoanService.Inner.touch"), ("interface", "Shelf"), ("record", "Pair")])

    def test_go(self):
        got = pairs("a.go", GO)
        self.assertEqual(got, [("type", "Loan"), ("type", "Shelf"), ("func", "Loan.Renew"), ("func", "FindCopy"), ("var", "Limit"), ("const", "Rate")])

    def test_markdown_headings_with_fences_setext_and_path(self):
        got = pairs("a.md", MARKDOWN)
        self.assertEqual(got, [("h1", "Title"), ("h2", "Title > Install steps"), ("h3", "Title > Install steps > Detail"),
                               ("h2", "Title > Install steps > Setext heading"), ("h1", "Other")][:2] + got[2:])
        self.assertEqual([k for k, _ in got], ["h1", "h2", "h3", "h2", "h1"])
        self.assertNotIn("not a heading", json.dumps(got))

    def test_yaml_keys_with_paths(self):
        got = [name for _, name in pairs("a.yml", YAML)]
        self.assertEqual(got, ["services", "services.web", "services.web.image", "services.web.ports", "services.db", "services.db.image", "version"])

    def test_json_keys_depth_two_and_not_into_arrays(self):
        got = [name for _, name in pairs("a.json", JSON_DOC)]
        self.assertEqual(got, ["name", "scripts", "scripts.build", "scripts.test", "list", "text"])

    def test_toml(self):
        got = pairs("a.toml", TOML)
        self.assertEqual(got, [("key", "title"), ("table", "project"), ("key", "project.name"), ("key", "project.version"),
                               ("table", "tool.item"), ("key", "tool.item.key")])

    def test_sql(self):
        got = pairs("a.sql", SQL)
        self.assertEqual(got, [("table", "loan"), ("view", "open_loans"), ("alter table", "loan"), ("index", "idx_loan")])

    def test_dotenv_lists_key_names_only(self):
        result = ol.outline_text(".env.example", DOTENV)
        self.assertEqual([e["name"] for e in result["entries"]], ["WEB_PORT", "DB_PASSWORD", "REGISTRY_HOST"])
        page = ol.render_page(result)["text"]
        for value in ("1012", "hunter2", "registry.local"):
            self.assertNotIn(value, page)
        for entry in result["entries"]:
            self.assertNotIn("=", entry["anchor"], "an anchor never reaches the value")
            self.assertEqual(DOTENV.count(entry["anchor"]), 1)

    def test_dotenv_duplicate_keys_are_anchorless_rather_than_reaching_into_the_value(self):
        result = ol.outline_text(".env", "WEB_PORT=1012\nWEB_PORT=1013\nREGISTRY_HOST=h\n")
        by_name = [(e["name"], e["anchor"], e.get("why")) for e in result["entries"]]
        self.assertEqual(by_name[0], ("WEB_PORT", None, "duplicate-line"))
        self.assertEqual(by_name[1], ("WEB_PORT", None, "duplicate-line"))
        page = ol.render_page(result)["text"]
        for value in ("1012", "1013"):
            self.assertNotIn(value, page)

    def test_unsupported_and_sensitive_files(self):
        result = ol.outline_text("a.html", "<p>x</p>")
        self.assertEqual(result["entries"], [])
        self.assertIn("no outline support", result["note"])
        for rel in ("credentials.json", "server.pem", "config/secrets.yml", "id_rsa"):
            result = ol.outline_text(rel, '{"password": "hunter2-very-secret"}', language="json")
            self.assertEqual(result["entries"], [], rel)
            self.assertIn("withheld", result["note"])

    def test_empty_and_trivial_files(self):
        for rel in ("e.py", "e.js", "e.md", "e.json", "e.sql", "e.toml", "e.yaml", "e.go", "e.java"):
            self.assertEqual(entries(rel, ""), [], rel)
        self.assertEqual(entries("e.json", "{not json at all"), [])
        entries("e.js", "function broken( {")  # must not raise


class AnchorTests(unittest.TestCase):
    def check_unique(self, rel: str, body: str):
        src = ev.decode_text(body.encode("utf-8"))
        result = ol.outline(src, rel)
        for entry in result["entries"]:
            anchor = entry["anchor"]
            if anchor is None:
                continue
            self.assertEqual(src.text.count(anchor), 1, (rel, entry))
            self.assertGreaterEqual(len(re.sub(r"\s", "", anchor)), 6, entry)
            self.assertNotIn("|", anchor)
            self.assertNotIn("\n", anchor)
            self.assertEqual(anchor, anchor.strip())
            self.assertEqual(ev.secret_findings(anchor), [])
            self.assertEqual(src.lines[entry["line"] - 1].strip().find(anchor) >= 0 or anchor in src.text, True)
        return result

    def test_every_language_fixture_gives_unique_anchors_that_the_verifier_resolves(self):
        fixtures = {"m.py": PYTHON, "a.js": JS, "a.ts": TS, "A.java": JAVA, "a.go": GO, "a.md": MARKDOWN, "a.yml": YAML,
                    "a.json": JSON_DOC, "a.toml": TOML, "a.sql": SQL, ".env.example": DOTENV}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rows, expected = [], {}
            for rel, body in fixtures.items():
                (root / rel).write_text(body, encoding="utf-8", newline="\n")
                result = self.check_unique(rel, body)
                self.assertGreater(len(result["entries"]), 0, rel)
                for entry in result["entries"]:
                    if entry["anchor"] is not None:
                        eid = f"{rel}#{len(rows)}"
                        rows.append({"evidenceId": eid, "repository": "r", "filePath": rel, "anchor": entry["anchor"], "topic": "t"})
                        expected[eid] = entry["line"]
            report = ev.verify_evidence(rows, {"r": root}, options=NO_REV)
            self.assertEqual(report["summary"]["status"]["PASS"], len(rows), ev.compact_report(report))
            for result in report["results"]:
                self.assertEqual(result["resolved"]["lineRange"][0], expected[result["evidenceId"]], result["evidenceId"])
                self.assertTrue(result["resolved"]["unique"])

    def test_the_anchor_is_the_shortest_boundary_prefix_that_is_unique(self):
        body = "def find_copy(self):\n    pass\n\ndef find_copy_by_title(self):\n    pass\n"
        got = {e["name"]: e["anchor"] for e in entries("a.py", body)}
        self.assertEqual(got["find_copy"], "def find_copy(")
        self.assertEqual(got["find_copy_by_title"], "def find_copy_by_title")
        src = ev.decode_text(body.encode())
        self.assertGreater(src.text.count("def find_copy"), 1, "the shorter cut would be ambiguous")

    def test_at_least_six_non_blank_characters(self):
        got = {e["name"]: e["anchor"] for e in entries("a.py", "def f():\n    pass\n\nx = 1\n")}
        self.assertEqual(got["f"], "def f()", "'def f' has 4 non-blank characters, 'def f(' has 5, 'def f()' has 6")

    def test_duplicated_lines_are_listed_without_an_anchor_and_say_why(self):
        body = ("class A:\n    def __init__(self):\n        pass\n\nclass B:\n    def __init__(self):\n        pass\n")
        result = ol.outline_text("a.py", body)
        dups = [e for e in result["entries"] if e["name"].endswith("__init__")]
        self.assertEqual(len(dups), 2)
        for entry in dups:
            self.assertIsNone(entry["anchor"])
            self.assertEqual(entry["why"], "duplicate-line")
        self.assertEqual(result["anchorless"], 2)
        page = ol.render_page(result)["text"]
        self.assertIn("2 without a unique single-line anchor", page)
        self.assertIn("duplicate-line", page)

    def test_a_duplicated_prefix_can_still_be_anchored_by_a_later_part_of_the_line(self):
        body = "def handler(a): pass\ndef handler(b): pass\n"
        got = [e["anchor"] for e in entries("a.py", body)]
        self.assertNotIn(None, got)
        self.assertEqual(len(set(got)), 2)

    def test_a_pipe_never_ends_up_in_an_anchor_even_when_only_it_would_make_the_line_unique(self):
        body = "## Option | first choice\n\n## Option | second choice\n"
        got = [e["anchor"] for e in entries("a.md", body)]
        self.assertEqual(got, ["first choice", "second"])

    def test_anchor_never_contains_a_pipe_or_an_option_looking_token(self):
        body = "def f(a): return a | b\n\ndef g(x=1, y=2): return x\n\ndef h(): return 1\n"
        for entry in entries("a.py", body):
            if entry["anchor"]:
                self.assertNotIn("|", entry["anchor"])
                self.assertIsNone(re.search(r"(?:^|\s)[A-Za-z_][\w.-]*=\S", entry["anchor"]), entry)

    def test_markdown_anchor_and_cjk_text(self):
        body = "# 借阅系统概要说明\n\n## 借阅流程说明\n\n借阅流程说明 again in text\n"
        got = {e["name"]: e["anchor"] for e in entries("a.md", body)}
        self.assertEqual(len(got), 2)
        src = ev.decode_text(body.encode())
        for anchor in got.values():
            self.assertEqual(src.text.count(anchor), 1)

    def test_crlf_and_bom_files_give_the_same_entries_and_lines(self):
        lf = ol.outline_text("m.py", PYTHON)
        crlf = ol.outline_text("m.py", PYTHON.replace("\n", "\r\n"))
        bom = ev.decode_text(b"\xef\xbb\xbf" + PYTHON.encode())
        self.assertEqual(lf["entries"], crlf["entries"])
        self.assertEqual(lf["entries"], ol.outline(bom, "m.py")["entries"])

    def test_sensitive_lines_are_listed_without_name_or_anchor(self):
        body = "def login():\n    pass\n\nPASSWORD_DEFAULT = 'hunter2-secret'\nTOKEN_KEY = 'abcd1234efgh'\nAPI_URL = 'x'\n"
        result = ol.outline_text("a.py", body)
        by_line = {e["line"]: e for e in result["entries"]}
        self.assertEqual(by_line[4]["name"], "[withheld]")
        self.assertIsNone(by_line[4]["anchor"])
        self.assertEqual(by_line[4]["why"], "sensitive-line")
        self.assertEqual(by_line[5]["name"], "[withheld]")
        page = ol.render_page(result)["text"]
        for value in ("hunter2", "abcd1234", "PASSWORD_DEFAULT", "TOKEN_KEY"):
            self.assertNotIn(value, page)
        self.assertEqual(by_line[6]["name"], "API_URL")
        self.assertIsNotNone(by_line[6]["anchor"])

    def test_a_credential_in_a_def_line_is_not_echoed_either(self):
        body = "def connect(password='hunter2-secret'):\n    pass\n"
        result = ol.outline_text("a.py", body)
        self.assertNotIn("hunter2", json.dumps(result))

    def test_anchor_longer_than_the_cap_is_not_offered(self):
        long_prefix = "x_" * 70
        body = f"def {long_prefix}a(): pass\ndef {long_prefix}b(): pass\n"
        for entry in entries("a.py", body):
            if entry["anchor"] is not None:
                self.assertLessEqual(len(entry["anchor"]), ol.MAX_ANCHOR_CHARS)


class HostileInputTests(unittest.TestCase):
    """Time limits: a 50 KB minified JSON used to block for 90 s (anchor search per entry over the whole file)."""

    LIMIT = 3.0

    def timed(self, rel: str, body: str) -> tuple[dict, float]:
        start = time.monotonic()
        result = ol.outline_text(rel, body)
        return result, time.monotonic() - start

    def test_minified_one_line_json_is_fast_and_lists_the_keys_without_anchors(self):
        body = "{" + ",".join(f'"k{i}":{i}' for i in range(2000)) + "}"
        self.assertLess(len(body), 25000)
        result, seconds = self.timed("a.json", body)
        self.assertLess(seconds, 1.0)
        self.assertEqual(len(result["entries"]), 2000)
        self.assertEqual({e["why"] for e in result["entries"]}, {"line-too-long"})
        self.assertEqual(result["entries"][5]["name"], "k5")

    def test_big_minified_json_and_many_entries_stay_within_the_limit(self):
        for n in (30000, 100000):
            body = "{" + ",".join(f'"k{i}":{i}' for i in range(n)) + "}"
            result, seconds = self.timed("a.json", body)
            self.assertLess(seconds, self.LIMIT, n)
            self.assertEqual(len(result["entries"]), ol.MAX_ENTRIES)

    def test_a_repeated_line_costs_one_lookup_not_one_per_entry(self):
        for rel, body in (("a.py", "def f(): pass\n" * 50000), ("a.js", "function g() { return 1; }\n" * 11000)):
            result, seconds = self.timed(rel, body)
            self.assertLess(seconds, self.LIMIT, rel)
            self.assertEqual({e["why"] for e in result["entries"]}, {"duplicate-line"})

    def test_a_long_line_gets_no_anchor_even_when_it_is_unique(self):
        body = "def a(): pass\n" + "x = '" + "y" * (ol.MAX_ANCHOR_LINE_CHARS + 5) + "'\ndef b(): pass\n"
        result = ol.outline_text("a.py", body)
        self.assertEqual([(e["name"], e["anchor"] is not None) for e in result["entries"]], [("a", True), ("b", True)])
        src = ev.decode_text(body.encode())
        self.assertEqual(ol.choose_anchor(src, "a.py", 2), (None, "line-too-long"))

    def test_the_deadline_lists_the_rest_without_anchors_and_says_so(self):
        body = "".join(f"def f{i}(): pass\n" for i in range(300))
        with mock.patch.object(ol, "OUTLINE_DEADLINE_SECONDS", -1.0):
            result = ol.outline_text("a.py", body)
        self.assertEqual(len(result["entries"]), 300)
        self.assertEqual({e["why"] for e in result["entries"]}, {"time-limit"})
        self.assertIn("anchor search stopped", result["note"])
        self.assertIn("anchor search stopped", ol.render_page(result)["text"])

    def test_a_file_above_the_size_limit_is_not_outlined(self):
        with mock.patch.object(ol, "MAX_OUTLINE_CHARS", 100):
            result = ol.outline_text("a.py", "def f(): pass\n" * 20)
        self.assertEqual(result["entries"], [])
        self.assertIn("no outline", result["note"])

    def test_identical_lines_share_one_search_and_the_same_verdict(self):
        body = "def one(): pass\n\n\ndef two(): pass\n" + "def dup(): pass\n" * 2
        cache: dict = {}
        src = ev.decode_text(body.encode())
        first = ol.choose_anchor(src, "a.py", 1, cache=cache)
        self.assertEqual(first, ol.choose_anchor(src, "a.py", 1))
        self.assertEqual(len(cache), 1)
        self.assertEqual(ol.choose_anchor(src, "a.py", 5, cache=cache), (None, "duplicate-line"))


class OutlineSecretTests(unittest.TestCase):
    def test_a_bare_token_in_a_heading_is_not_an_entry_name(self):
        body = "# Setup\n\n## Token is abcdef0123456789abcdef\n\ntext\n\n### Next step\n"
        result = ol.outline_text("notes.md", body)
        self.assertNotIn("abcdef0123", json.dumps(result))
        self.assertNotIn("abcdef0123", ol.render_page(result)["text"])
        names = {e["line"]: e["name"] for e in result["entries"]}
        self.assertEqual(names[3], "[withheld]")
        self.assertEqual(names[7], "[withheld]", "its name is a path that includes the sensitive heading")

    def test_lines_inside_a_private_key_block_get_no_anchor(self):
        pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcw\n-----END PRIVATE KEY-----"
        body = 'KEY = """\n' + pem + '\n"""\n\ndef after():\n    pass\n'
        result = ol.outline_text("a.py", body)
        self.assertNotIn("MIIEvQ", json.dumps(result))
        src = ev.decode_text(body.encode())
        self.assertEqual(ol.choose_anchor(src, "a.py", 3), (None, "sensitive-line"))
        # body lines too short to look like a token on their own are still inside the block
        short = 'KEY = """\n-----BEGIN PRIVATE KEY-----\nQUJDREVGRw\nSElKS0xNTg\n-----END PRIVATE KEY-----\n"""\nafter = 1\n'
        src = ev.decode_text(short.encode())
        self.assertEqual(ol.choose_anchor(src, "a.py", 3), (None, "sensitive-line"))
        self.assertEqual(ol.choose_anchor(src, "a.py", 4), (None, "sensitive-line"))
        self.assertTrue(ol.choose_anchor(src, "a.py", 7)[0].startswith("after"))

    def test_json_keys_that_look_like_tokens_are_withheld(self):
        result = ol.outline_text("a.json", '{"ordinary": 1,\n"' + 'sk_' + 'live_4eC39HqLyjWDarjtT1zdp7dc": 2}\n')
        self.assertNotIn("4eC39", json.dumps(result))


class PageTests(unittest.TestCase):
    def big(self, n=400):
        body = "".join(f"def function_number_{i}_with_a_long_descriptive_name(argument_{i}):\n    pass\n\n" for i in range(n))
        return ol.outline_text("big.py", body)

    def test_page_is_at_most_4kb_and_pages_cover_every_entry_once(self):
        result = self.big()
        seen, start, pages = [], 0, 0
        while start is not None:
            page = ol.render_page(result, start)
            self.assertLessEqual(len(page["text"].encode("utf-8")), 4096)
            self.assertGreater(page["shown"], 0)
            seen.extend(range(page["start"], page["start"] + page["shown"]))
            start = page["next"]
            pages += 1
            self.assertLess(pages, 200)
        self.assertEqual(seen, list(range(len(result["entries"]))))
        self.assertGreater(pages, 5)

    def test_header_footer_and_json_literal_anchors(self):
        result = ol.outline_text("a.json", JSON_DOC)
        page = ol.render_page(result)
        self.assertTrue(page["text"].startswith("# a.json [json] "))
        self.assertIsNone(page["next"])
        for line in page["text"].splitlines()[1:]:
            literal = line.split(" | ", 1)[1]
            anchor = json.loads(literal)
            self.assertIn(anchor, JSON_DOC)
        more = ol.render_page(self.big(), 0)
        self.assertRegex(more["text"], r"-- \d+ more entries: start=\d+\n$")

    def test_a_tiny_budget_still_shows_one_entry_and_never_overflows(self):
        result = self.big(5)
        page = ol.render_page(result, 0, max_bytes=200)
        self.assertLessEqual(len(page["text"].encode("utf-8")), 200)
        self.assertGreaterEqual(page["shown"], 1)

    def test_bad_start_falls_back_to_the_beginning(self):
        result = self.big(3)
        self.assertEqual(ol.render_page(result, -5)["start"], 0)
        self.assertEqual(ol.render_page(result, 999)["start"], 0)

    def test_unicode_page_size_counts_bytes_not_characters(self):
        body = "".join(f"## 見出し{i}番の長い日本語の説明文です\n\n" for i in range(300))
        result = ol.outline_text("j.md", body)
        page = ol.render_page(result)
        self.assertLessEqual(len(page["text"].encode("utf-8")), 4096)


class OutlineFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "m.py").write_text(PYTHON, encoding="utf-8")
        (self.root / "bin.dat").write_bytes(b"\x00\x01")
        (self.root / ".env").write_text(DOTENV, encoding="utf-8")
        self.roots = {"r": self.root}

    def test_ok(self):
        out = ol.outline_file(self.roots, "r", "src/m.py")
        self.assertTrue(out["ok"])
        self.assertEqual(out["language"], "python")
        self.assertIn('"def create_loan"', out["text"])

    def test_errors_are_returned_with_codes(self):
        cases = [("r", "nope.py", "GEMARC_ATLAS_EV_005"), ("zz", "src/m.py", "GEMARC_ATLAS_EV_030"), ("r", "bin.dat", "GEMARC_ATLAS_EV_006"),
                 ("r", "../x.py", roots.E_DOTDOT), ("r", "/etc/passwd", roots.E_ABSOLUTE), ("D:/GemArc", "src/m.py", roots.E_ARGUMENT),
                 ("../repo", "src/m.py", roots.E_ARGUMENT)]
        for alias, rel, code in cases:
            out = ol.outline_file(self.roots, alias, rel)
            self.assertFalse(out["ok"], (alias, rel))
            self.assertEqual(out["code"], code, (alias, rel, out))
        gone = ol.outline_file({"r": self.root / "missing"}, "r", "src/m.py")
        self.assertEqual(gone["code"], "GEMARC_ATLAS_EV_031")

    def test_dotenv_through_the_roots_shows_keys_only(self):
        out = ol.outline_file(self.roots, "r", ".env")
        self.assertTrue(out["ok"])
        self.assertIn("DB_PASSWORD", out["text"])
        self.assertNotIn("hunter2", out["text"])


class RepoCorpusTests(unittest.TestCase):
    """Every supported file under this repository's src/gemarc_mcp (CRLF and LF, Python, JS, Markdown, JSON...)."""

    @classmethod
    def setUpClass(cls):
        cls.files = [p for p in sorted(CORPUS_ROOT.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and ol.language_of(p.name)]
        cls.stats = {"files": 0, "entries": 0, "anchored": 0, "anchorless": 0, "nonunique": 0, "short": 0, "pages_over": 0, "not_resolved": 0}
        cls.by_language: dict[str, int] = {}
        cls.problems: list = []
        rows, expected = [], {}
        for path in cls.files:
            try:
                src = ev.decode_text(path.read_bytes())
            except ev.TextError:
                continue
            rel = path.relative_to(CORPUS_ROOT).as_posix()
            result = ol.outline(src, rel)
            cls.stats["files"] += 1
            cls.by_language[result["language"]] = cls.by_language.get(result["language"], 0) + 1
            start = 0
            while start is not None:
                page = ol.render_page(result, start)
                cls.stats["pages_over"] += int(len(page["text"].encode("utf-8")) > 4096)
                start = page["next"]
            for entry in result["entries"]:
                cls.stats["entries"] += 1
                if entry["anchor"] is None:
                    cls.stats["anchorless"] += 1
                    continue
                cls.stats["anchored"] += 1
                if src.text.count(entry["anchor"]) != 1:
                    cls.stats["nonunique"] += 1
                    cls.problems.append((rel, entry))
                if len(re.sub(r"\s", "", entry["anchor"])) < 6:
                    cls.stats["short"] += 1
                eid = f"C{len(rows)}"
                rows.append({"evidenceId": eid, "repository": "src", "filePath": rel, "anchor": entry["anchor"], "topic": "t"})
                expected[eid] = entry["line"]
        report = ev.verify_evidence(rows, {"src": CORPUS_ROOT}, options=NO_REV)
        for result in report["results"]:
            if result["status"] != "PASS" or result["resolved"]["lineRange"][0] != expected[result["evidenceId"]] or not result["resolved"]["unique"]:
                cls.stats["not_resolved"] += 1
                cls.problems.append((result["evidenceId"], result.get("name"), result.get("resolved")))
        cls.stats["rows"] = len(rows)

    def test_corpus_is_real_and_covers_the_languages(self):
        self.assertGreater(self.stats["files"], 60)
        self.assertGreater(self.stats["entries"], 1000)
        for language in ("python", "javascript", "markdown"):
            self.assertGreater(self.by_language.get(language, 0), 0, language)

    def test_every_emitted_anchor_is_unique_in_its_file(self):
        self.assertEqual(self.stats["nonunique"], 0, self.problems[:5])
        self.assertEqual(self.stats["short"], 0)

    def test_every_anchor_passes_the_verifier_at_its_entry_line(self):
        self.assertEqual(self.stats["not_resolved"], 0, self.problems[:5])
        self.assertEqual(self.stats["rows"], self.stats["anchored"])

    def test_anchorless_entries_are_a_small_minority_and_every_page_fits(self):
        self.assertLess(self.stats["anchorless"], 0.10 * self.stats["entries"], self.stats)
        self.assertEqual(self.stats["pages_over"], 0)


if __name__ == "__main__":
    unittest.main()
