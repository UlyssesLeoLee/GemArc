"""Slice-mode and full-mode validation: expand, run the rules, return diagnostics, pending references and counts.

``check(sources, meta, mode="slice" | "full", incoming=..., red_drafts=..., known=...)`` is the one entry point that the
``put`` / ``check`` tools and the CLI call. It parses the slices, expands them **without** the expander's own closed-world
check (``closed_world=False``; the reference rules RF-001..RF-005 are this module's), runs the registered rules of the
requested wave numbers root cause first and returns a ``CheckResult``.

Slice mode (design 5.3), for a slice that may reference ids written later:

* an unresolved reference is **pending** (INFO, grouped by kind), unless exactly one defined id of the same kind is one edit
  away (``diag.LookAlike``: insert, delete, substitute, transpose; ids shorter than 5 characters and ids that differ only
  inside digit runs never count): then it is ``WARN probable typo, one edit from: <id>``. ``@pending id,...`` silences a
  deliberate forward reference. Nothing here blocks a commit except ERROR rules.
* **binding rules** (SM-003 owner; later CS-001 and intervention binding) stay *pending-bind* (counted, not printed) until a
  slice declares ``@final edges`` or the mode is ``full``.
* only findings located in the **incoming** slices are reported; the other slices are the committed rest of the project
  (ordered first, so a duplicate id is reported where the new text defines it).
* **cascade suppression**: an entity with an unresolved reference is tainted; a later finding that this reference may explain
  (EV-001 with an unresolved evidence id) is counted as ``derived`` and not printed.

Full mode: closed world, every unresolved reference is an ERROR with the nearest id as the hint, every red draft is ST-009.
Remote ``check`` (no project store): pass the ids of the slices the server does not hold as ``known``; they resolve references
and the verdict is advisory. Standard library only.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import diag as dg
from . import expand as ex
from . import rules  # noqa: F401  (registers every rule and check)
from .format.rows import ParsedSlice
from .rules import registry as reg
from .rules.context import Context
from .rules.rf import pending_summary

#: Kinds of ids a remote ``check`` can be told about.
KNOWN_KINDS = ("building", "floor", "room", "node", "edge", "evidence", "machine", "case", "requirement", "test", "tbd")

#: ``(rule, path)`` of the expander diagnostics that are references to something that is not (yet) there: they are re-run
#: through the pending / typo logic of the rules (kind of the missing id) instead of being printed as the expander wrote them.
_UNPLACED = {("RF-004", "events.case"): "case", ("RF-004", "expect.case"): "case", ("SM-001", "transitions.machine"): "machine"}
#: expander diagnostics that the model scan of the rules reports again (one source of truth): dropped
_REPORTED_BY_RULES = {("RF-002", "machines.id"), ("RF-002", "interventions.rewrite")}


@dataclass
class CheckResult:
    """What a check found. ``diagnostics`` holds everything (derived ones flagged); renderers list only the rest."""

    diagnostics: list
    mode: str
    pending: dict = field(default_factory=dict)       # kind -> sorted unique ids of unresolved references (slice mode)
    pending_refs: int = 0
    pending_bind: int = 0                              # binding findings deferred until ``@final edges`` or full mode
    expansion: "ex.Expansion | None" = None
    seconds: float = 0.0
    wave_set: tuple = (1,)

    @property
    def derived_count(self) -> int:
        return sum(d.derived for d in self.diagnostics)

    @property
    def shown(self) -> list:
        return dg.visible(self.diagnostics)

    @property
    def errors(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.ERROR and not d.derived]

    @property
    def warnings(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.WARN and not d.derived]

    @property
    def infos(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.INFO and not d.derived]

    @property
    def ok(self) -> bool:
        """No ERROR: a ``put`` commits."""
        return not self.errors

    def render(self, fmt: str = "text", offset: int = 0, budget: "int | None" = None) -> str:
        return dg.render(self.diagnostics, fmt=fmt, offset=offset, budget=budget, pending=self.pending,
                         pending_bind=self.pending_bind, derived=self.derived_count)

    def rule_ids(self, *, derived: bool = False) -> set:
        """Rule ids of the findings (derived ones only on request)."""
        return {d.rule for d in self.diagnostics if derived or not d.derived}


def check(sources: "Mapping[str, str | bytes] | Iterable[tuple[str, str | bytes]] | Sequence[ParsedSlice]", meta: "Mapping | None" = None, *,
          mode: str = "slice", incoming: "Iterable[str] | None" = None, red_drafts: Iterable[str] = (),
          known: "Mapping | Iterable | str | None" = None, stamps: "Mapping | None" = None, element_templates=None,
          profile=None, wave_set=(1,)) -> CheckResult:
    """Parse, expand and check slices. See the module doc for the two modes.

    ``sources`` is a mapping ``name -> text`` (processed in file-name order), a sequence of ``(name, text)`` pairs (given
    order) or already parsed slices. ``incoming`` (slice mode) names the slices that are being put or checked; omitted, all.
    """
    if isinstance(sources, Mapping):
        pairs = sorted(sources.items())
    else:
        pairs = [x if isinstance(x, tuple) else (x.name, x) for x in sources]
    texts = {name: _text(data) for name, data in pairs if not isinstance(data, ParsedSlice)}
    parsed = [data if isinstance(data, ParsedSlice) else ex.parse_source(name, data) for name, data in pairs]
    return check_slices(parsed, meta, mode=mode, incoming=incoming, red_drafts=red_drafts, known=known, stamps=stamps,
                        element_templates=element_templates, profile=profile, wave_set=wave_set, texts=texts)


def check_slices(parsed: Sequence[ParsedSlice], meta: "Mapping | None" = None, *, mode: str = "slice", incoming: "Iterable[str] | None" = None,
                 red_drafts: Iterable[str] = (), known=None, stamps=None, element_templates=None, profile=None, wave_set=(1,),
                 texts: "Mapping[str, str] | None" = None) -> CheckResult:
    """``check`` for slices that are already parsed; ``texts`` (``name -> text``) lets a refused row's id be recovered."""
    started = time.perf_counter()
    names = None if incoming is None else set(incoming)
    ordered = list(parsed)
    if mode == "slice" and names is not None:  # the committed rest first: a duplicate id is reported where the incoming text defines it
        ordered = [s for s in parsed if s.name not in names] + [s for s in parsed if s.name in names]
    expansion = ex.expand_slices(ordered, meta, stamps=stamps, element_templates=element_templates, profile=profile, closed_world=False)
    return check_expansion(expansion, meta, mode=mode, incoming=names, slices=[(s.name, s.size) for s in ordered], red_drafts=red_drafts,
                           known=known, wave_set=wave_set, started=started, texts=texts)


def check_expansion(expansion: "ex.Expansion", meta: "Mapping | None" = None, *, mode: str = "slice", incoming: "Iterable[str] | None" = None,
                    slices: Iterable = (), red_drafts: Iterable[str] = (), known=None, wave_set=(1,), started: "float | None" = None,
                    texts: "Mapping[str, str] | None" = None) -> CheckResult:
    """Run the rules over an expansion made with ``closed_world=False`` (the default of ``expand``)."""
    started = time.perf_counter() if started is None else started
    structural, unplaced = [], []
    for d in expansion.diagnostics:
        kind = _UNPLACED.get((d.rule, d.path))
        if kind is not None and d.severity != dg.ERROR:
            unplaced.append((d.rule, kind, d.got, d.path, d.at))
        elif (d.rule, d.path) not in _REPORTED_BY_RULES or d.severity == dg.ERROR:
            structural.append(d)
    ctx = Context(expansion.model, expansion.provenance, mode=mode, meta=meta, finals=expansion.finals, pendings=expansion.pendings,
                  slices=slices, incoming=incoming, red_drafts=red_drafts, known=parse_known(known), structural=structural, wave_set=wave_set)
    ctx.unplaced = unplaced
    ctx.dropped_ids |= refused_row_ids(texts or {}, structural)
    reg.run(ctx, wave_set)
    keep = [d for d in structural if ctx.in_scope(d.at)]
    diagnostics = _dedupe(keep + ctx.out)
    return CheckResult(diagnostics, mode, pending_summary(ctx), len(ctx.pending), ctx.pending_bind, expansion,
                       time.perf_counter() - started, tuple(wave_set))


def check_model(model: Mapping, meta: "Mapping | None" = None, *, mode: str = "full", provenance: "Mapping[str, str] | None" = None,
                known=None, wave_set=(1,)) -> CheckResult:
    """Run the rules over an already expanded model (no rows; without a provenance map the locations are ``model:0``)."""
    started = time.perf_counter()
    ctx = Context(model, provenance, mode=mode, meta=meta, known=parse_known(known), wave_set=wave_set)
    reg.run(ctx, wave_set)
    return CheckResult(_dedupe(ctx.out), mode, pending_summary(ctx), len(ctx.pending), ctx.pending_bind, None, time.perf_counter() - started,
                       tuple(wave_set))


def _dedupe(diags: Iterable) -> list:
    seen, out = set(), []
    for d in sorted(diags, key=dg.Diag.sort_key):
        key = (d.rule, d.severity, d.path, d.at, repr(d.got), d.derived)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


# ---------------------------------------------------------------------------------------------------------------
# ``known``: ids the caller holds outside the slices being checked (remote check)
# ---------------------------------------------------------------------------------------------------------------


def known_ids(model: Mapping, exclude: Iterable[str] = ()) -> dict:
    """``{kind: sorted ids}`` of a model: what a remote ``check`` needs as ``known`` to resolve references."""
    ctx = Context(model)
    return {kind: sorted(ctx.model_ids(kind)) for kind in KNOWN_KINDS if kind not in set(exclude)}


def format_known(known: Mapping[str, Iterable[str]]) -> str:
    """The compact wire form of ``known``: one ``<kind> <id>`` line per id (design 5.3)."""
    return "\n".join(f"{kind} {identifier}" for kind in sorted(known) for identifier in sorted(known[kind]))


def parse_known(known: "Mapping | Iterable | str | None") -> dict:
    """Accept ``known`` as a mapping ``kind -> ids``, a list of ``"<kind> <id>"`` lines or one text of such lines."""
    if not known:
        return {}
    if isinstance(known, Mapping):
        return {str(k): set(v) for k, v in known.items()}
    lines = known.splitlines() if isinstance(known, str) else list(known)
    out: dict[str, set] = {}
    for line in lines:
        kind, _, identifier = str(line).strip().partition(" ")
        if kind and identifier.strip():
            out.setdefault(kind, set()).add(identifier.strip())
    return out


# ---------------------------------------------------------------------------------------------------------------
# Refused rows: ids that a syntax error took out of the model (their dangling references are consequences, not causes)
# ---------------------------------------------------------------------------------------------------------------

#: sections whose first cell is the id of the record the row defines
_ID_FIRST = frozenset({"buildings", "floors", "rooms", "nodes", "evidences", "machines", "tests", "cases", "interventions",
                       "requirements", "tbds", "policies"})
_SYNTAX_RULES = ("ST-001", "ST-003", "ST-004", "ST-005", "ST-006", "ST-007", "DV-001")


def _text(data) -> str:
    return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)


def refused_row_ids(texts: Mapping[str, str], diags: Iterable) -> set:
    """Ids of the rows that carry a syntax diagnostic: the front-end drops such a row, so a reference to its id dangles.

    The id is the first cell of the line (``.atl``) or the ``id`` key of the object (``.jsonl``). The result is only
    consulted for references that are unresolved anyway, so an id of a row that was kept does no harm.
    """
    lines: dict[str, list] = {}
    section_at: dict[str, list] = {}  # per file: the section name in force at every line (one forward pass, O(n) in total)
    out: set = set()
    for d in diags:
        if d.severity != dg.ERROR or d.rule not in _SYNTAX_RULES:
            continue
        name, _, number = d.at.rpartition(":")
        if name not in texts or not number.isdigit() or int(number) < 1:
            continue
        rows = lines.setdefault(name, texts[name].replace("\r\n", "\n").split("\n"))
        index = int(number) - 1
        if index >= len(rows):
            continue
        line = rows[index].strip()
        if name.lower().endswith(".jsonl"):
            try:
                record = json.loads(line)
            except (ValueError, RecursionError, MemoryError):  # deeply nested or hostile JSON is already a diagnostic
                continue
            if isinstance(record, dict) and record.get("_section") in _ID_FIRST and isinstance(record.get("id"), str):
                out.add(record["id"])
            continue
        if not line or line.startswith(("#", "@", "//")):
            continue
        if name not in section_at:
            current, table = "", []
            for text in rows:
                head = text.strip()
                if head.startswith("## "):
                    current = head[3:].split(":")[0].strip()
                table.append(current)
            section_at[name] = table
        section = section_at[name][index]
        first = line.split("|", 1)[0].strip()
        if section in _ID_FIRST and first and " " not in first:
            out.add(first)
    return out
