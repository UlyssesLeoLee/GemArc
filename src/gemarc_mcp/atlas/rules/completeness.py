"""The completeness report: for every slot that a document or a builder reads, is the fact there?

A pure function over the expanded model (``completeness_report``); the ``status`` tool renders it. No file is read, nothing
is derived twice: every fact comes from the model, and ``provenance`` (the expander's ``model path -> file:line`` map) only
tells a defaulted value from a stated one and locates a slot.

Per slot a **status** and a **severity**:

========================  =======================================================================================
``OK``                    the fact is there
``MISSING``               the fact is absent (an empty text, no evidence, no case, no requirement)
``UNRESOLVED_REF``        the slot names an id that is not defined
``DEFAULTED``             the value was filled in by a derivation, not stated (a default verification)
``N/A``                   the slot does not apply here (no state machines, no data tables)
========================  =======================================================================================

``BLOCK``: a document would print a ``[NOT-AUTHORED:...]`` marker or garbage, or a builder would fail (release is blocked
until it is cleared). ``GAP``: the document is complete in form but the reader loses information. ``INFO``: worth knowing.

Slots are named like the skeleton markers (``node.sub``, ``ch9.nfr``); ``level`` is the shallowest document level (REQ, BD,
DD) that prints the slot; ``Report.by_level`` counts cumulatively (REQ is contained in BD, BD in DD). OK slots are not listed,
only counted (``Report.tally``). Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

STATUSES = ("MISSING", "UNRESOLVED_REF", "DEFAULTED", "N/A", "OK")
SEVERITIES = ("BLOCK", "GAP", "INFO")
LEVELS = ("REQ", "BD", "DD")
DATA_KINDS = ("Entity", "Table")


@dataclass(frozen=True)
class Slot:
    """One slot that is not OK."""

    slot: str        # ``node.room``, ``ch9.nfr`` ...
    ref: str         # the entity id, or ``""`` for a chapter slot
    status: str
    severity: str
    level: str       # shallowest document level that prints it
    at: str = ""     # ``file:line`` when a provenance map is given
    note: str = ""

    def as_dict(self) -> dict:
        return {"slot": self.slot, "ref": self.ref, "status": self.status, "severity": self.severity, "level": self.level,
                "at": self.at, "note": self.note}


@dataclass
class Report:
    entries: list = field(default_factory=list)                 # slots that are not OK, in model order
    tally: dict = field(default_factory=dict)                  # ``{slot name: {status: count}}`` including OK

    def counts(self) -> dict:
        out = {s: 0 for s in SEVERITIES}
        for e in self.entries:
            out[e.severity] += 1
        return out

    def blockers(self) -> list:
        return [e for e in self.entries if e.severity == "BLOCK"]

    def by_level(self) -> dict:
        """``{level: {severity: count}}``, cumulative: BD includes REQ, DD includes BD."""
        out = {}
        for depth, level in enumerate(LEVELS):
            shown = LEVELS[: depth + 1]
            out[level] = {s: sum(1 for e in self.entries if e.severity == s and e.level in shown) for s in SEVERITIES}
        return out

    def slot_summary(self) -> list:
        """Compact rows ``(slot, status, severity, count, first refs)``, BLOCK first."""
        groups: dict = {}
        for e in self.entries:
            groups.setdefault((e.slot, e.status, e.severity), []).append(e.ref)
        rows = [(slot, status, severity, len(refs), refs[:3]) for (slot, status, severity), refs in groups.items()]
        return sorted(rows, key=lambda r: (SEVERITIES.index(r[2]), r[0], r[1]))

    @property
    def ready(self) -> bool:
        """No BLOCK slot: a document would not print a marker and a builder would not fail."""
        return not self.blockers()

    def to_dict(self) -> dict:
        return {"counts": self.counts(), "levels": self.by_level(), "tally": self.tally,
                "slots": [e.as_dict() for e in self.entries]}

    def render(self, limit: int = 8) -> str:
        """A few lines: the counts, then the ``limit`` biggest groups (``BLOCK slot status xN: ids``)."""
        c = self.counts()
        lines = [f"completeness: {c['BLOCK']} BLOCK, {c['GAP']} GAP, {c['INFO']} INFO"]
        for slot, status, severity, count, refs in self.slot_summary()[:limit]:
            shown = ", ".join(refs) + (" ..." if count > len(refs) else "")
            lines.append(f"{severity} {slot} {status} x{count}" + (f": {shown}" if any(refs) else ""))
        return "\n".join(lines)


class _Builder:
    def __init__(self, provenance: "Mapping[str, str] | None"):
        self.prov = provenance or {}
        self.report = Report()

    def at(self, *keys: str) -> str:
        for key in keys:
            if key in self.prov:
                return self.prov[key]
        return ""

    def ok(self, slot: str, status: str = "OK") -> None:
        bucket = self.report.tally.setdefault(slot, {})
        bucket[status] = bucket.get(status, 0) + 1

    def check(self, fine: bool, slot: str, ref: str, status: str, severity: str, level: str, at: str = "", note: str = "") -> None:
        """Count the slot as OK when ``fine``, else list it with ``status``."""
        if fine:
            self.ok(slot)
        else:
            self.add(slot, ref, status, severity, level, at, note)

    def add(self, slot: str, ref: str, status: str, severity: str, level: str, at: str = "", note: str = "") -> None:
        self.ok(slot, status)
        self.report.entries.append(Slot(slot, ref, status, severity, level, at, note))


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def completeness_report(model: Mapping, provenance: "Mapping[str, str] | None" = None) -> Report:
    """The completeness report of an expanded model (see the module doc). Pure; the model is not changed."""
    b = _Builder(provenance)
    space = model.get("space") or {}
    ids = {
        "room": {r.get("id") for r in space.get("rooms", ())},
        "node": {n.get("id") for n in model.get("nodes", ())},
        "evidence": {e.get("evidenceId") for e in model.get("evidences", ())},
        "requirement": {r.get("id") for r in model.get("requirements", ())},
    }
    nodes, edges = list(model.get("nodes", ())), list(model.get("edges", ()))
    cases, reqs = list(model.get("cases", ())), list(model.get("requirements", ()))
    policies = list(model.get("policies", ()))

    # ---- chapter slots
    def chapter(slot: str, fine: bool, status: str, severity: str, note: str) -> None:
        b.check(fine, slot, "", status, severity, "REQ", note=note)

    chapter("ch3.requirements", bool(reqs), "MISSING", "GAP", "no requirement: chapter 3 says there is none")
    chapter("ch4.structure", bool(nodes), "MISSING", "GAP", "no node: chapter 4 says there is no component")
    chapter("ch5.cases", bool(cases), "MISSING", "GAP", "no case: chapter 5 says no case is selected")
    chapter("ch6.data", any(n.get("kind") in DATA_KINDS for n in nodes), "N/A", "INFO", "no Entity or Table node")
    chapter("ch8.machines", bool(model.get("machines")), "N/A", "INFO", "no state machine")
    chapter("ch9.nfr", any(p.get("kind") == "nfr" for p in policies), "MISSING", "BLOCK", "no nfr policy: chapter 9 prints [NOT-AUTHORED:ch9.nfr]")
    chapter("ch10.constraints", any(p.get("kind") == "constraint" for p in policies), "N/A", "INFO",
            "no constraint declared (the document says so, it is not a gap)")
    derivable = any(c.get("expectedTerminalState") or c.get("expectedOutcomes") for c in cases) or any(r.get("tstIds") for r in reqs)
    chapter("ch11.acceptance", derivable, "MISSING", "GAP", "no expected state, outcome or test: chapter 11 has nothing to derive")

    # ---- nodes
    for n in nodes:
        nid = n.get("id", "")
        key = f"nodes[{nid}]"
        at = b.at(key)
        b.check(_text(n.get("sub")), "node.sub", nid, "MISSING", "BLOCK", "REQ", at, "[NOT-AUTHORED:node.sub@" + nid + "]")
        b.check(n.get("roomId") in ids["room"], "node.room", nid, "UNRESOLVED_REF", "BLOCK", "BD", at, f"room {n.get('roomId')!r} is not defined")
        refs = list(n.get("evidenceIds", ()))
        if not refs:
            b.add("node.evidence", nid, "MISSING", "GAP", "BD", at, "no evidence")
        elif any(x not in ids["evidence"] for x in refs):
            b.add("node.evidence", nid, "UNRESOLVED_REF", "BLOCK", "BD", at, "an evidence id is not defined")
        else:
            b.ok("node.evidence")
        if f"{key}.verification" in b.prov:
            b.add("node.verification", nid, "DEFAULTED", "INFO", "BD", b.at(f"{key}.verification", key), "derived from the evidence")
        else:
            b.ok("node.verification")
        if n.get("kind") in DATA_KINDS:
            columns = (n.get("props") or {}).get("columns")
            if isinstance(columns, list) and columns:
                b.ok("node.columns")
            else:
                b.add("node.columns", nid, "MISSING", "BLOCK", "DD", at, "[NOT-AUTHORED:node.columns@" + nid + "]")

    # ---- edges
    for e in edges:
        eid = e.get("id", "")
        key = f"edges[{eid}]"
        at = b.at(key)
        b.check(e.get("sourceId") in ids["node"] and e.get("targetId") in ids["node"], "edge.endpoints", eid, "UNRESOLVED_REF", "BLOCK", "REQ",
                at, "an endpoint is not a defined node")
        b.check(_text(e.get("label")), "edge.label", eid, "MISSING", "GAP", "REQ", at, "no label")
        if f"{key}.verification" in b.prov:
            b.add("edge.verification", eid, "DEFAULTED", "INFO", "BD", b.at(f"{key}.verification", key), "derived from the evidence")
        else:
            b.ok("edge.verification")

    # ---- cases
    for c in cases:
        cid = c.get("caseId", "")
        key = f"cases[{cid}]"
        at = b.at(key)
        b.check(bool(c.get("expectedTerminalState") or c.get("expectedOutcomes")), "case.expect", cid, "MISSING", "GAP", "REQ", at,
                "no expected final state or outcome")
        if not c.get("requirementIds"):
            b.add("case.requirement", cid, "MISSING", "GAP", "REQ", at, "traces no requirement")
        elif any(x not in ids["requirement"] for x in c["requirementIds"]):
            b.add("case.requirement", cid, "UNRESOLVED_REF", "BLOCK", "REQ", at, "a requirement id is not defined")
        else:
            b.ok("case.requirement")
        bad = [x for ev in c.get("events", ()) for x in (ev.get("from"), ev.get("to")) if x not in ids["node"]]
        b.check(not bad, "case.events", cid, "UNRESOLVED_REF", "BLOCK", "DD", at, f"{len(bad)} event endpoint(s) are not defined nodes")

    # ---- requirements
    for r in reqs:
        rid = r.get("id", "")
        at = b.at(f"requirements[{rid}]")
        refs = list(r.get("nodeIds", ()))
        if not refs:
            b.add("requirement.nodes", rid, "MISSING", "GAP", "REQ", at, "names no node")
        elif any(x not in ids["node"] for x in refs):
            b.add("requirement.nodes", rid, "UNRESOLVED_REF", "BLOCK", "REQ", at, "a node id is not defined")
        else:
            b.ok("requirement.nodes")
        b.check(bool(r.get("tstIds")), "requirement.tests", rid, "MISSING", "INFO", "REQ", at, "no test")

    # ---- evidences
    for ev in model.get("evidences", ()):
        eid = ev.get("evidenceId", "")
        at = b.at(f"evidences[{eid}]")
        b.check(_text(ev.get("anchor")) and _text(ev.get("topic")), "evidence.claim", eid, "MISSING", "GAP", "DD", at, "anchor or topic is empty")
        if ev.get("sourceKind") == "source-code":
            b.check(bool(ev.get("lineRange")), "evidence.stamp", eid, "MISSING", "INFO", "DD", at, "no stamped line range yet")
    return b.report
