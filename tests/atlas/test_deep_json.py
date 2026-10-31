"""Deeply nested JSON in the files the tools read back (design: a tampered record is "unreadable", never a crash).

``json.loads`` raises ``RecursionError`` (not ``ValueError``) past the interpreter's recursion limit, and a document that parses can
still be too deep for ``json.dumps`` with an indent or for ``copy.deepcopy`` later. Every file under ``.atlas/`` and ``dist/`` that a
tool reads must therefore turn such a file into its "unreadable" answer: the oracle ledger (tamper state ``unreadable``), the anchor,
the put counters, the review record, the evidence stamp and the build info. Synthetic ids only.

Run: PYTHONPATH=src python -m unittest tests.atlas.test_deep_json
"""
from __future__ import annotations

import json
import unittest

from gemarc_mcp.atlas import evidence as ev
from gemarc_mcp.atlas import oracle
from gemarc_mcp.atlas import slices as sl
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools

from .test_oracle_support import OracleProject, desk_files

#: Past the recursion limit of the JSON decoder.
TOO_DEEP = b"[" * 5000 + b"]" * 5000


def nested(depth: int) -> list:
    out: list = []
    for _ in range(depth):
        out = [out]
    return out


class DeepLedgerTests(OracleProject):
    def setUp(self):
        super().setUp()
        self.desk()
        self.simulate()

    def forged_deep(self, depth: int) -> None:
        """A ledger that parses and carries a valid chain (a forger who read oracle.py): ``summary`` holds a list nested ``depth`` deep."""
        data = self.ledger()
        data["summary"] = nested(depth)
        data["chain"] = ""
        data["chain"] = oracle._chain_of(data)  # noqa: SLF001
        self.paths.oracle.write_bytes(json.dumps(data, sort_keys=True).encode("utf-8"))
        self.write_anchor(data)         # a forger who knows about the anchor: only the depth is wrong

    def test_a_ledger_past_the_recursion_limit_is_unreadable(self):
        self.paths.oracle.write_bytes(TOO_DEEP)
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "unreadable")

    def test_a_deep_ledger_that_parses_is_unreadable_too(self):
        # it would parse and its chain verifies, but it is not a ledger the tool wrote: no real ledger nests anywhere near this
        for depth in (120, 300, 900):
            with self.subTest(depth=depth):
                self.forged_deep(depth)
                self.assertEqual(oracle.read_ledger(self.paths).tamper, "unreadable")

    def test_a_real_ledger_is_far_below_the_depth_limit(self):
        data = self.ledger()
        stack, deepest = [(data, 1)], 0
        while stack:
            node, level = stack.pop()
            deepest = max(deepest, level)
            for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
                stack.append((child, level + 1))
        self.assertLessEqual(deepest * 2, oracle.LEDGER_MAX_DEPTH, deepest)
        self.assertEqual(oracle.read_ledger(self.paths).tamper, "")

    def test_the_tools_answer_a_deep_ledger_like_any_other_damaged_one(self):
        self.paths.oracle.write_bytes(b"{{{{")
        reference = self.replies()
        for label, blob in (("5000", TOO_DEEP), ("900", None)):
            with self.subTest(label=label):
                if blob is None:
                    self.forged_deep(900)
                else:
                    self.paths.oracle.write_bytes(blob)
                self.assertEqual(self.replies(), reference)

    def replies(self) -> dict:
        """The shape of what check, status and an unchanged put answer (no exception may escape any of them)."""
        check = tools.check(self.ws, self.name, mode="full")
        status = tools.status(self.ws, self.name)
        scenarios = desk_files()["20-scenarios"]
        put = tools.put(self.ws, self.name, "20-scenarios", scenarios)
        return {"check": (check["status"], check.get("clean")), "status": (status["status"], status.get("phase")), "put": put["status"]}

    def test_the_resume_entry_point_does_not_stay_blocked(self):
        self.paths.oracle.write_bytes(TOO_DEEP)
        self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")
        self.assertEqual(tools.put(self.ws, self.name, "20-scenarios", desk_files()["20-scenarios"])["status"], "committed")
        # the put wrote a readable ledger again (provenance unverified for good)
        self.assertEqual(oracle.read_ledger(self.paths).data["provenance"], "unverified")

    def test_a_deep_anchor_is_an_unreadable_anchor(self):
        (self.paths.dot / oracle.ANCHOR_NAME).write_bytes(TOO_DEEP)
        self.assertEqual(oracle._anchor(self.paths), {})  # noqa: SLF001
        self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")

    def test_a_deep_put_counter_file_is_ignored(self):
        (self.paths.dot / "puts.json").write_bytes(TOO_DEEP)
        self.assertEqual(oracle._puts(self.paths), 0)  # noqa: SLF001
        self.assertEqual(tools.put(self.ws, self.name, "20-scenarios", desk_files()["20-scenarios"])["status"], "committed")

    def test_a_put_counter_file_that_is_not_an_object_is_ignored(self):
        (self.paths.dot / "puts.json").write_bytes(b"[1, 2]")
        self.assertEqual(tools.put(self.ws, self.name, "20-scenarios", desk_files()["20-scenarios"])["status"], "committed")


class DeepRecordTests(OracleProject):
    def setUp(self):
        super().setUp()
        self.desk()

    def test_a_deep_review_record_is_no_record(self):
        self.paths.review.write_bytes(TOO_DEEP)
        self.assertIsNone(st.read_review(self.paths))
        self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")
        self.assertEqual(tools.review(self.ws, self.name, "report")["status"], "ok")

    def test_a_deep_stamp_is_an_unusable_stamp_not_a_crash(self):
        self.paths.stamp.parent.mkdir(parents=True, exist_ok=True)
        self.paths.stamp.write_bytes(TOO_DEEP)
        with self.assertRaises(ev.EvidenceError) as caught:
            ev.load_stamp(self.paths.atlas)
        self.assertEqual(caught.exception.code, "GEMARC_ATLAS_EV_070")
        self.assertEqual(tools.review(self.ws, self.name, "report")["status"], "ok")   # the review reads the stamp too
        self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")

    def test_a_deep_build_info_is_an_unreadable_one(self):
        reply = tools.build(self.ws, self.name, docs=False)
        self.assertIn(reply["status"], ("ok", "invalid"), reply)
        info = self.paths.atlas / "dist" / "build-info.json"
        if info.is_file():
            info.write_bytes(TOO_DEEP)
            self.assertEqual(tools.status(self.ws, self.name)["status"], "ok")
            verify = tools.verify(self.ws, self.name)
            self.assertIn(verify["status"], ("invalid", "error"), verify)
            self.assertNotEqual(verify.get("code"), "GEMARC_INTERNAL_001", verify)


class DeepSliceTests(OracleProject):
    DEEP = "[" * 5000 + "]" * 5000

    def test_a_deeply_nested_cell_is_a_diagnostic_not_a_crash(self):
        self.init()
        files = desk_files(("20-scenarios", 'terminal={"svc.stock":"Blocked"}', "terminal=" + self.DEEP))
        for name, text in files.items():
            reply = self.put(name, text)
            self.assertNotEqual(reply.get("code"), "GEMARC_INTERNAL_001", reply)
            self.assertNotEqual(reply.get("status"), "error", reply)

    def test_a_deeply_nested_jsonl_patch_line_is_refused_cleanly(self):
        with self.assertRaises(sl.SliceError) as caught:
            sl.apply_patch('{"_section":"nodes","id":"a"}\n', self.DEEP + "\n", "jsonl")
        self.assertEqual(caught.exception.code, sl.E_PATCH)


if __name__ == "__main__":
    unittest.main()
