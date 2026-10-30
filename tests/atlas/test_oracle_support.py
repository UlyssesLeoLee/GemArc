"""Shared fixtures of the oracle tests (design M5): a temporary workspace, the lending desk with a ruleId on every event,
engine-derived expectations for synthetic models, the confirmation helpers. It has one test of its own.

Run: PYTHONPATH=src python -m unittest discover -s tests/atlas -t tests -p "test_oracle*.py"

Expectations built from engine output (``consistent``) are legitimate here: they are test FIXTURES that are written before the
project exists; the oracle rules are about an AI that sees a result before it writes (see ``test_oracle_cheaters.py``).
Synthetic id families only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemarc_mcp.atlas import encode
from gemarc_mcp.atlas import engine as E
from gemarc_mcp.atlas import oracle
from gemarc_mcp.atlas import project as pj
from gemarc_mcp.atlas import status as st
from gemarc_mcp.atlas import tools
from gemarc_mcp.atlas.rules import or_

from . import synth

DESK_DIR = Path(__file__).resolve().parent / "fixtures" / "lending_desk"
DESK = {name: (DESK_DIR / f"{name}.atl").read_text(encoding="utf-8") for name in ("00-space", "10-structure", "20-scenarios")}

#: The desk with a ruleId on the three Return events, so the wave-2 INFO rule CS-008 is quiet.
RETURNS = {"Return | svc.stock | svc.loan\n": "Return | svc.stock | svc.loan | ruleId=R-RET-STOCK\n",
           "Return | tbl.loan | svc.loan\n": "Return | tbl.loan | svc.loan | ruleId=R-RET-TABLE\n",
           "Return | svc.loan | usr.member\n": "Return | svc.loan | usr.member | ruleId=R-RET-MEMBER\n"}


def desk_files(*edits, ruleids: bool = True) -> dict:
    """The desk slices (``name -> text``) with edits ``(slice name, old, new)`` applied (an edit that finds nothing is a test bug)."""
    files = dict(DESK)
    if ruleids:
        for old, new in RETURNS.items():
            files["20-scenarios"] = files["20-scenarios"].replace(old, new, 1)
    for name, old, new in edits:
        if old not in files[name]:
            raise AssertionError(f"edit target not found in {name}: {old!r}")
        files[name] = files[name].replace(old, new, 1)
    return files


def tree_digest(root: Path) -> str:
    """One hash over every file under ``root`` (relative path and bytes): the 'check changes no file' witness."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).replace("\\", "/").encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\1")
    return h.hexdigest()


def consistent(model: dict) -> dict:
    """A copy of ``model`` whose expectations are what the engine computes: every participant of a case asserted with its final
    state; an intervention gets ``status`` only when it is not OK, the outcome class when all its cases agree and the terminal
    states of the nodes that differ when it applies to one case. (A fixture written before the project exists.)"""
    m = copy.deepcopy(model)
    ix = E.index_model(m)
    for case in m["cases"]:
        run = E.simulate(m, case, None, {"index": ix})
        parts = []
        for ev in case["events"]:
            for key in ("from", "to"):
                if ev[key] not in parts:
                    parts.append(ev[key])
        case["expectedTerminalState"] = {n: run["finalState"][n] for n in parts if n in run["finalState"]}
        case.pop("expectedOutcomes", None)
    for iv in m["interventions"]:
        compiled = E.compile_deltas(ix, [iv])
        status = "REJECTED" if compiled.status == "INVALID" else compiled.status
        expect: dict = {}
        if status != "OK":
            expect["status"] = status
        else:
            classes, terminal = set(), {}
            for cid in iv["appliesTo"]:
                result = E.run_causal(m, cid, [iv], {"index": ix})
                classes.add(result["diff"]["outcomeClass"])
                if len(iv["appliesTo"]) == 1:
                    terminal = {d["nodeId"]: d["b"] for d in result["diff"]["terminalDiff"][:3]}
            if len(classes) == 1:
                expect["outcomeClass"] = classes.pop()
            if terminal:
                expect["terminal"] = terminal
        iv["expect"] = expect
    return m


def slice_texts(model: dict, *, advisory: bool = True) -> dict:
    files = encode.encode_atl(model, max_bytes=encode.ADVISORY_BYTES if advisory else 64 * 1024, state_hypothetical=True)
    return {name[:-4]: text for name, text in sorted(files.items())}


class OracleProject(unittest.TestCase):
    """A temporary workspace with writes allowed, one project, and short names for the calls the tests make."""

    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"GEMARC_ALLOW_WRITE": "1", "GEMARC_ATLAS_DEBUG": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ws = pj.get_workspace(self.root)
        self.name = "desk"

    # -- project -------------------------------------------------------------------------------------------

    def init(self, name="desk", mode="asis", languages=("en-US",), repos=("main",)):
        self.name = name
        reply = tools.init(self.ws, name, "Library lending desk", mode, list(languages), repos=list(repos))
        self.assertEqual(reply["status"], "created", reply)
        self.paths = pj.project_paths(self.ws, name)
        return reply

    def put(self, slice_, text, **kw):
        return tools.put(self.ws, self.name, slice_, text, **kw)

    def load(self, files: dict) -> None:
        for name, text in files.items():
            reply = self.put(name, text)
            self.assertEqual(reply["status"], "committed", (name, reply))

    def desk(self, *edits, ruleids: bool = True):
        self.init()
        self.load(desk_files(*edits, ruleids=ruleids))

    def cfg(self):
        return pj.load_config(self.paths)

    def snap(self, **kw):
        return st.load_snapshot(self.paths, self.cfg(), **kw)

    # -- oracle --------------------------------------------------------------------------------------------

    def simulate(self, **kw):
        return tools.simulate(self.ws, self.name, **kw)

    def ledger(self) -> dict:
        return json.loads(self.paths.oracle.read_text(encoding="utf-8"))

    def entry(self, key: str) -> dict:
        return self.ledger()["items"][key]

    def write_ledger(self, data: dict, *, rechain: bool = False, anchor: bool = True) -> None:
        """Put a hand-made ledger on disk. ``rechain`` recomputes the chain (what a forger who read oracle.py does); ``anchor`` then
        also rewrites ``.atlas/oracle-anchor.json`` (a forger who knows about it); without it only the ledger changes."""
        if rechain:
            data["chain"] = ""
            data["chain"] = oracle._chain_of(data)
        self.paths.oracle.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        if rechain and anchor:
            self.write_anchor(data)

    def write_anchor(self, data: dict) -> None:
        (self.paths.dot / oracle.ANCHOR_NAME).write_text(json.dumps({"chain": data["chain"], "tick": data["tick"]}, sort_keys=True) + "\n", encoding="utf-8")

    def gate(self):
        return oracle.release_gate(self.ws, self.name)

    def bite(self) -> dict:
        """The mutant run the release build makes first (the gate needs one for the current model)."""
        reply = tools.oracle_bite(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        return reply

    def states(self) -> dict:
        return {key: state for key, (_item, state, _why) in oracle.effective_states(self.paths, self.snap().model).items()}

    def sheet(self) -> str:
        reply = tools.oracle_sheet(self.ws, self.name)
        self.assertEqual(reply["status"], "ok", reply)
        return Path(reply["path"]).read_text(encoding="utf-8")

    def confirm_file(self) -> Path:
        return oracle.confirm_path(self.paths)

    def confirm_ticks(self, only=None) -> int:
        """What the user does: copy the sheet lines to the confirmation file and tick them."""
        lines = [re.sub(r"^- \[ \]", "- [x]", line).split(" | ")[0] for line in self.sheet().splitlines() if line.startswith("- [ ] ")]
        if only is not None:
            lines = [x for x in lines if x.split()[2] in only]
        self.confirm_file().write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(lines)

    def approve_all(self) -> str:
        digest = re.search(r"^APPROVE-ALL ([0-9a-f]{12})$", self.sheet(), re.M).group(1)
        self.confirm_file().write_text(f"APPROVE-ALL {digest}\n", encoding="utf-8")
        return digest

    def expect_row(self, nodes: str, case="CS-BORROW-OK") -> tuple:
        return ("20-scenarios", "CS-BORROW-OK | usr.member,svc.loan,svc.stock,tbl.loan | by=author", f"{case} | {nodes} | by=author")


class SupportTests(OracleProject):
    def test_the_desk_with_rule_ids_is_clean_in_both_rule_sets_without_a_ledger(self):
        self.desk()
        snap = self.snap(wave_set=(1, 2))
        self.assertEqual([d.as_dict() for d in snap.check.diagnostics if d.rule != "OR-005"], [])

    def test_consistent_expectations_match_the_engine(self):
        model = consistent(synth.generate("small", 3))
        ix = E.index_model(model)
        for case in model["cases"]:
            run = E.simulate(model, case, None, {"index": ix})
            for node, state in case["expectedTerminalState"].items():
                self.assertEqual(run["finalState"][node], state)
        self.assertEqual([i for i in or_.collect_items(model).values() if not i.clean], [])

    def test_tree_digest_sees_a_changed_byte(self):
        self.desk()
        before = tree_digest(self.root)
        self.paths.oracle.write_bytes(self.paths.oracle.read_bytes() + b" ")
        self.assertNotEqual(before, tree_digest(self.root))


if __name__ == "__main__":
    unittest.main()
