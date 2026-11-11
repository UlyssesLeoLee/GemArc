"""The expectation layer of the rules (design section 7): what an assertion is, and the rules OR-000 .. OR-005.

**Pure helpers** (used by ``oracle.py`` and by the rules; no I/O):

* ``collect_items(model)`` -> ``{key: Item}``: every case with an ``expect`` set is an item ``case:<id>``; every intervention
  with an outcome class or terminal expectation is an item ``intervention:<id>``. ``Item.assertion`` is the *normalised*
  assertion (sorted keys, outcomes sorted, ``by`` left out), ``Item.ah`` its sha256, ``Item.clean`` whether it may be sealed
  (design 7.2 (i): every node it names resolves and every outcome rule id is a rule id of the case's events).
* ``effective(entry, item, confirmed_ah8)`` -> ``(state, why)``: the state the ledger entry gives the item *now*:
  ``unregistered`` | ``sealed`` | ``revised`` | ``confirmed``.
* ``coverage_floor`` / ``coverage_gap`` (OR-003) and ``abnormal_all_completed`` (OR-002).

**Rules.** OR-002 and OR-003 are static. OR-000, OR-001, IV-002 (in ``iv.py``), OR-004 and OR-005 report what the ORACLE LEDGER
already holds (a registration, a verdict that ``simulate`` already served, a mutant run): they read it through the
``View`` that ``oracle.reading(paths)`` installs for the duration of one check, so ``check`` shows nothing the AI could not
have asked ``simulate`` for, never runs a verdict, never writes. Without a view (a pure or remote check) these rules say
nothing. CS-002 (terminal oracle) and CS-003 (outcomes oracle) are registered but report as OR-001; the path of the finding
says which part of the expectation (``.expectedTerminalState`` / ``.expectedOutcomes``) it is about.

Standard library only.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Iterator, Mapping

from .. import diag as dg
from .. import model as am
from ..engine import mutate
from .context import Context
from .registry import Rule, check, register

CASE, INTERVENTION = "case", "intervention"
FLOOR_SHARE = 0.35
FLOOR_MIN = 2
LAZY_CATEGORIES = ("abnormal", "recovery", "combination", "combined")
BITE_WARN_RATE = 0.25
BITE_WARN_PARTICIPANTS = 5
STATES = ("sealed", "revised", "confirmed")


def canon(value) -> str:
    """Canonical JSON text (sorted keys, no spaces, no ASCII escaping): what every hash here is taken over."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


# ---------------------------------------------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    key: str            # ``case:<id>`` or ``intervention:<id>``
    kind: str
    id: str
    assertion: dict
    ah: str             # sha256 of the normalised assertion
    by: str             # the author's claim (``author`` or ``user``)
    clean: bool         # may be sealed (design 7.2 (i))
    why: str = ""       # what keeps it from being sealed
    fp: str = ""        # fingerprint of what the assertion is about (the events, the deltas): clone detection

    @property
    def ah8(self) -> str:
        return self.ah[:8]


def _sorted_map(raw) -> dict:
    return {str(k): v for k, v in sorted(raw.items(), key=lambda kv: str(kv[0]))} if isinstance(raw, Mapping) else {}


def normalise_case(case: Mapping) -> "dict | None":
    """The assertion of a case: ``{"terminal": {node: state}, "outcomes": [...]}``; ``None`` when it asserts nothing."""
    terminal = _sorted_map(case.get("expectedTerminalState"))
    outcomes = [{k: o[k] for k in ("ruleId", "outcome", "errorCode") if k in o and o[k] is not None}
                for o in case.get("expectedOutcomes") or [] if isinstance(o, Mapping)]
    outcomes.sort(key=canon)
    return {"terminal": terminal, "outcomes": outcomes} if terminal or outcomes else None


def normalise_intervention(iv: Mapping) -> "dict | None":
    """The assertion of an intervention: its outcome class and terminal states (and ``status`` when those exist)."""
    expect = iv.get("expect") if isinstance(iv.get("expect"), Mapping) else {}
    out: dict = {}
    if expect.get("outcomeClass"):
        out["class"] = expect["outcomeClass"]
    terminal = _sorted_map(expect.get("terminal"))
    if terminal:
        out["terminal"] = terminal
    if not out:
        return None
    if expect.get("status"):
        out["status"] = expect["status"]
    return out


def _event_rule_ids(case: Mapping) -> set:
    return {e.get("ruleId") for e in case.get("events") or [] if isinstance(e, Mapping) and e.get("ruleId")}


#: Labels of an event that never change what the engine computes for a BASELINE run (the step name and the position; ``role``
#: is not one: the outcome class of a causal comparison reads it): two cases that differ only in them are the same case for
#: clone detection.
_EVENT_LABELS = ("ruleId", "seq")


def _case_fp(case: Mapping) -> str:
    """Fingerprint of what a case DOES: its events without their labels, the policy, the context and the initial machines."""
    events = [{k: v for k, v in e.items() if k not in _EVENT_LABELS} if isinstance(e, Mapping) else e for e in case.get("events") or []]
    body = {"events": events, **{k: case.get(k) for k in ("policy", "context", "initialMachines")}}
    return digest(canon(body))


def _iv_fp(iv: Mapping) -> str:
    body = {k: v for k, v in iv.items() if k not in ("interventionId", "description", "expect", "note", "why")}
    if isinstance(body.get("appliesTo"), list):
        body["appliesTo"] = sorted(body["appliesTo"])
    return digest(canon(body))


def _inserted_elements(iv: Mapping) -> set:
    try:
        parts = am.flatten_parts(iv)
    except (am.AtlasModelError, KeyError, TypeError, RecursionError):
        return set()
    return {p["element"]["elementId"] for p in parts if isinstance(p.get("element"), Mapping) and isinstance(p["element"].get("elementId"), str)}


def collect_items(model: Mapping) -> dict:
    """Every assertion of the model as an ``Item`` (insertion order: cases, then interventions)."""
    nodes = {n.get("id") for n in model.get("nodes") or [] if isinstance(n, Mapping)}
    items: dict = {}
    for case in model.get("cases") or []:
        if not isinstance(case, Mapping) or not isinstance(case.get("caseId"), str):
            continue
        assertion = normalise_case(case)
        if assertion is None:
            continue
        missing = [k for k in assertion["terminal"] if k not in nodes]
        known_rules = _event_rule_ids(case)
        unknown_rules = [o["ruleId"] for o in assertion["outcomes"] if o.get("ruleId") not in known_rules]
        why = ""
        if missing:
            why = "names a node that is not defined: " + ", ".join(missing[:3])
        elif unknown_rules:
            why = "names an outcome rule id that no event of the case carries: " + ", ".join(str(x) for x in unknown_rules[:3])
        cid = case["caseId"]
        items[f"{CASE}:{cid}"] = Item(f"{CASE}:{cid}", CASE, cid, assertion, digest(canon(assertion)),
                                      case.get("expectedBy") if case.get("expectedBy") in ("author", "user") else "author", not why, why, _case_fp(case))
    for iv in model.get("interventions") or []:
        if not isinstance(iv, Mapping) or not isinstance(iv.get("interventionId"), str):
            continue
        assertion = normalise_intervention(iv)
        if assertion is None:
            continue
        known_keys = nodes | _inserted_elements(iv)
        missing = [k for k in assertion.get("terminal", {}) if k not in known_keys]
        why = "names a node or element that is not defined: " + ", ".join(missing[:3]) if missing else ""
        iid = iv["interventionId"]
        items[f"{INTERVENTION}:{iid}"] = Item(f"{INTERVENTION}:{iid}", INTERVENTION, iid, assertion, digest(canon(assertion)), "author",
                                              not why, why, _iv_fp(iv))
    return items


# ---------------------------------------------------------------------------------------------------------------
# State of an item under a ledger entry
# ---------------------------------------------------------------------------------------------------------------


def effective(entry: "Mapping | None", item: Item, confirmed_ah8: "str | None" = None) -> tuple:
    """``(state, why)`` of ``item`` under its ledger ``entry`` (and the ``confirmed_ah8`` the confirmation file gives it)."""
    if entry is None:
        return "unregistered", item.why or "not registered: call put, simulate or build"
    if not item.clean:
        return "unregistered", item.why
    if entry.get("withdrawn") or entry.get("ah") != item.ah:
        return "unregistered", "edited since the last write call: put, simulate or build registers the new text"
    if confirmed_ah8 is not None and confirmed_ah8 == item.ah8:
        return "confirmed", ""
    state = entry.get("state")
    return (state if state in STATES else "unregistered"), str(entry.get("why") or "")


@dataclass
class View:
    """A read-only picture of the ledger for one check: entries by key, the ticks of the confirmation file, tamper status.

    ``resolve(items)`` (given by ``oracle.py``) maps the current items to the ``{key: hash8}`` the user's confirmation file
    approves; ``bind(items)`` calls it once the items of the model are known."""

    entries: Mapping = field(default_factory=dict)
    confirmed: Mapping = field(default_factory=dict)     # key -> ah8 that the (solicited) confirmation file approves
    tamper: str = ""                                      # "" | "edited" | "unreadable" | "deleted" | "rolled-back"
    provenance: str = "ok"
    resolve: object = None
    reason: str = ""                                      # why the provenance is unverified (kept in the ledger)

    def bind(self, items: Mapping) -> "View":
        if self.resolve is not None:
            self.confirmed = self.resolve(items)
        return self

    def state_of(self, item: Item) -> tuple:
        return effective(self.entries.get(item.key), item, self.confirmed.get(item.key))


_VIEW: "contextvars.ContextVar[View | None]" = contextvars.ContextVar("gemarc_atlas_oracle_view", default=None)


@contextlib.contextmanager
def reading(view: "View | None") -> Iterator[None]:
    """Install ``view`` for the checks that run inside the ``with`` block (``None`` = no oracle information)."""
    token = _VIEW.set(view)
    try:
        yield
    finally:
        _VIEW.reset(token)


def current_view() -> "View | None":
    return _VIEW.get()


# ---------------------------------------------------------------------------------------------------------------
# Anti-vacuity arithmetic (OR-002, OR-003)
# ---------------------------------------------------------------------------------------------------------------


def coverage_floor(participants: int) -> int:
    """Asserted nodes a case must have: ``max(2, ceil(0.35 * participants))`` (design 7.5)."""
    return max(FLOOR_MIN, math.ceil(FLOOR_SHARE * participants - 1e-9))


def effective_asserted(case: Mapping, parts: "list | None" = None) -> int:
    """Asserted nodes that constrain anything: a participant, or a non-participant asserted to a state other than ``Idle``
    (``Idle`` for a node the case never touches holds for every case and for every mutant)."""
    parts = mutate.participants(case) if parts is None else parts
    terminal = case.get("expectedTerminalState") or {}
    return sum(1 for node, state in terminal.items() if node in parts or state != "Idle")


def coverage_gap(case: Mapping) -> "tuple | None":
    """``(asserted, floor, participants)`` when the case asserts fewer nodes than the floor, else ``None``.

    Only participants (and non-Idle claims) count toward the floor; a case with an expectation and no events has no participants
    at all, so its gap is ``(asserted, 2, 0)``: nothing it asserts is exercised."""
    parts = mutate.participants(case)
    if not parts:
        if case.get("expectedTerminalState") or case.get("expectedOutcomes"):
            return (effective_asserted(case, parts), FLOOR_MIN, 0)
        return None
    asserted = effective_asserted(case, parts)
    floor = coverage_floor(len(parts))
    return (asserted, floor, len(parts)) if asserted < floor else None


def gap_text(gap: tuple) -> str:
    """The repair sentence for a coverage gap (used by OR-003 and the release gate)."""
    if gap[2] == 0:
        return "the case has no events: its expectations constrain nothing; write the events it exercises"
    return f"assert at least {gap[1]} of the {gap[2]} participants (35%, minimum 2); Idle on an untouched node does not count"


#: The event outcome every successful run has (the vocabulary's ``eventOutcomes`` has no other success value): asserting it says nothing
#: abnormal (OR-002).
UNREMARKABLE_OUTCOMES = ("OK",)


def abnormal_all_completed(case: Mapping) -> bool:
    """OR-002: an abnormal, recovery or combination case whose effective assertions are only ``Completed`` states and ``OK`` outcomes.
    Effective is the same notion as the coverage floor's: a participant, or a state other than
    ``Idle`` (``Idle`` on a node the case never touches holds for every run, so adding one does not make a lazy case less lazy)."""
    if case.get("category") not in LAZY_CATEGORIES:
        return False
    parts = mutate.participants(case)
    terminal = case.get("expectedTerminalState") or {}
    states = [state for node, state in terminal.items() if node in parts or state != "Idle"]
    remarkable = [o for o in case.get("expectedOutcomes") or [] if isinstance(o, Mapping) and o.get("outcome") not in UNREMARKABLE_OUTCOMES]
    return bool(states) and not remarkable and all(state == "Completed" for state in states)


# ---------------------------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------------------------

E, W, I = dg.ERROR, dg.WARN, dg.INFO

register(Rule("OR-000", 2, I, "not registered: call put, simulate or build ({detail})",
              "an assertion is not sealed yet (a write call seals it once every node it names resolves)", modes=("full",)))
register(Rule("OR-001", 2, E, "{detail}",
              "sealed expectation mismatch (counts only); also reports the terminal oracle (CS-002) and outcomes oracle (CS-003)",
              modes=("full",)))
register(Rule("CS-002", 2, E, "reported as OR-001", "terminal oracle: the asserted final states hold (reports as OR-001)", modes=("full",)))
register(Rule("CS-003", 2, E, "reported as OR-001", "outcomes oracle: the asserted outcomes occur (reports as OR-001)", modes=("full",)))
register(Rule("OR-002", 2, W, "an {category} case that asserts only Completed states (and OK outcomes) asserts nothing abnormal: add an out= row or a state that differs",
              "abnormal, recovery or combination case with only Completed states and OK outcomes asserted"))
register(Rule("OR-003", 2, E, "{detail}",
              "coverage floor: asserted nodes >= max(2, ceil(0.35 x participants))", modes=("full",)))
register(Rule("OR-004", 2, E, "{detail}", "kill rate of the mutants (zero killed = the assertions constrain nothing; low rate = WARN)", modes=("full",)))
register(Rule("OR-005", 2, I, "{detail}", "revised or unconfirmed expectation (the release gate blocks until the user confirms)", modes=("full",)))


def _key_at(ctx: Context, item: Item, rest: str = ".expect") -> tuple:
    if item.kind == CASE:
        key = f"cases[{item.id}]"
        return key + rest, ctx.at(key + ".expect", key)
    key = f"interventions[{item.id}]"
    return key + ".expect", ctx.at(key)


@check("OR", rules=("OR-002", "OR-003"))
def anti_vacuity(ctx: Context) -> None:
    for case in ctx.records("cases"):
        cid = case.get("caseId")
        if not isinstance(cid, str):
            continue
        key = f"cases[{cid}]"
        at = ctx.at(key + ".expect", key)
        if abnormal_all_completed(case):
            ctx.add("OR-002", path=key + ".expectedTerminalState", at=at, got=f"{len(case['expectedTerminalState'])} nodes, all Completed",
                    expected="a state or an outcome that shows the abnormal path", category=case.get("category"))
        gap = coverage_gap(case)
        if gap is not None:
            ctx.add("OR-003", path=key + ".expectedTerminalState", at=at, got=f"{gap[0]} asserted", expected=f">= {gap[1]} of {gap[2]} participants",
                    floor=gap[1], participants=gap[2], detail=gap_text(gap))


@check("OR", rules=("OR-001",))
def unknown_rule_ids(ctx: Context) -> None:
    """OR-001 (static): an expected outcome names a rule id that no event of the case carries (it can never be matched)."""
    for case in ctx.records("cases"):
        cid = case.get("caseId")
        if not isinstance(cid, str):
            continue
        known = _event_rule_ids(case)
        for index, o in enumerate(case.get("expectedOutcomes") or []):
            if isinstance(o, Mapping) and o.get("ruleId") not in known:
                key = f"cases[{cid}]"
                ctx.add("OR-001", path=f"{key}.expectedOutcomes[{index}].ruleId", at=ctx.at(key + ".expect", key), got=o.get("ruleId"),
                        expected="a ruleId carried by an event of the case",
                        detail="the expected outcome can never match: write the ruleId of an event of the case")


def _fresh(entry: Mapping, field_name: str, sha8: str) -> "Mapping | None":
    record = entry.get(field_name)
    return record if isinstance(record, Mapping) and record.get("sha") == sha8 else None


@check("OR", rules=("OR-000",))
def unregistered(ctx: Context) -> None:
    """OR-000 (INFO, full check, read-only): an assertion that no write call has sealed yet. ``status.load_snapshot`` runs this one
    rule in every full check (see ``registration_findings``) so that ``check`` always tells the AI which expectations are not
    registered; it runs no verdict and never seals."""
    view = current_view()
    if view is None:
        return
    items = collect_items(ctx.model)
    view.bind(items)
    for item in items.values():
        state, why = view.state_of(item)
        if state == "unregistered":
            path, at = _key_at(ctx, item)
            ctx.add("OR-000", path=path, at=at, got=item.key, expected="a sealed assertion", detail=why or "not registered")


@check("OR", rules=("OR-001", "OR-004", "OR-005"))
def ledger_findings(ctx: Context) -> None:
    view = current_view()
    if view is None:
        return
    try:
        sha8 = am.model_sha8(ctx.model)
    except (am.AtlasModelError, UnicodeError, ValueError, TypeError):
        sha8 = ""
    items = collect_items(ctx.model)
    view.bind(items)
    cases = {c.get("caseId"): c for c in ctx.records("cases")}
    unconfirmed = 0
    first_unconfirmed: "Item | None" = None
    for item in items.values():
        state, why = view.state_of(item)
        path, at = _key_at(ctx, item)
        entry = view.entries.get(item.key) or {}
        if state == "unregistered":
            continue
        if state == "revised":
            ctx.add("OR-005", path=path, at=at, got=item.key, expected="confirmed by the user", detail=f"{item.key} is revised ({why or 'edited after a result was shown'}): the user confirms it in the oracle sheet")
        elif state == "sealed":
            unconfirmed += 1
            first_unconfirmed = first_unconfirmed or item
        if item.kind != CASE:
            continue
        last = _fresh(entry, "last", sha8)
        if last is not None and last.get("pass") is False:
            t, o = last.get("terminal") or [0, 0], last.get("outcomes") or [0, 0]
            parts = []
            if t[0]:
                parts.append(f"terminal {t[0]} of {t[1]} asserted nodes differ (CS-002)")
            if o[0]:
                parts.append(f"outcomes {o[0]} of {o[1]} not found (CS-003)")
            sub = ".expectedOutcomes" if (o[0] and not t[0]) else ".expectedTerminalState"
            ctx.add("OR-001", path=f"cases[{item.id}]{sub}", at=at, got=item.key, expected="the sealed expectation to hold (counts only: ids are a recorded reveal)",
                    detail=("; ".join(parts) or "the expectation does not hold") + "; fix the events, or revise it (user confirms)")
        bite = _fresh(entry, "bite", sha8)
        case = cases.get(item.id)
        if bite is not None and case is not None and bite.get("holds"):
            total, killed = int(bite.get("total") or 0), int(bite.get("killed") or 0)
            if total and killed == 0:
                ctx.add("OR-004", path=f"cases[{item.id}].expectedTerminalState", at=at, got=f"0 of {total} mutants killed",
                        expected="at least one mutant killed", detail="the assertions constrain nothing the case exercises: assert the participants that matter, not Idle ones")
            elif (total and killed / total < BITE_WARN_RATE and case.get("category") == "normal"
                  and len(mutate.participants(case)) >= BITE_WARN_PARTICIPANTS):
                ctx.add("OR-004", severity=W, path=f"cases[{item.id}].expectedTerminalState", at=at, got=f"{killed} of {total} mutants killed",
                        expected=f"a kill rate of at least {BITE_WARN_RATE}", detail="weak bite: assert more of the nodes the events change")
    if unconfirmed and first_unconfirmed is not None:
        path, at = _key_at(ctx, first_unconfirmed)
        ctx.add("OR-005", path=path, at=at, got=f"{unconfirmed} sealed, not confirmed", expected="confirmed by the user before release",
                detail=f"{unconfirmed} sealed expectation(s) wait for the user's confirmation (oracle sheet); the release build needs sealed or confirmed")
    if view.tamper or view.provenance != "ok":
        first = next(iter(items.values()), None)
        if first is not None:
            path, at = _key_at(ctx, first)
            ctx.add("OR-005", path=path, at=at, got="unverified provenance", expected="an untouched oracle ledger",
                    detail="the oracle ledger was edited, deleted, replaced by an older copy or unreadable: provenance is unverified until the user confirms every item")


def registration_findings(model: Mapping, provenance: "Mapping | None", view: "View | None") -> list:
    """The OR-000 diagnostics of ``model`` under ``view`` (what ``check mode=full`` adds to wave 1 without running the rest of wave 2)."""
    ctx = Context(model, provenance, mode="full")
    with reading(view):
        unregistered(ctx)
    return ctx.out
