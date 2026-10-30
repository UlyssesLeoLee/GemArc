"""CS-004: a case traces a requirement (wave 1), and the wave-2 case rules.

Wave 2: CS-001 every event is bound to a runtime edge (binding rule: deferred in slice mode until ``@final edges``; the test
is the engine's own routing, so it also holds for ``Return`` events that travel an edge backwards), CS-005 category cover
and copies (WARN), CS-008 events without a ``ruleId`` (INFO). CS-002 and CS-003 (the terminal and outcomes oracles) are
registered in ``or_.py`` and report as OR-001. Standard library only.
"""
from __future__ import annotations

from .. import vocab
from .context import Context
from .registry import check


@check("CS", rules=("CS-004",))
def traces_requirement(ctx: Context) -> None:
    for c in ctx.records("cases"):
        if not c.get("requirementIds"):
            key = f"cases[{c['caseId']}]"
            ctx.add("CS-004", path=key + ".requirementIds", at=ctx.at(key), got="none", expected=">= 1 requirement id", entity=key)


# ---------------------------------------------------------------------------------------------------------------
# Wave 2
# ---------------------------------------------------------------------------------------------------------------

MIN_CASES_FOR_COVER = 6


def _event_at(ctx: Context, cid: str, seq) -> tuple:
    key = f"cases[{cid}].events[{seq}]"
    return key, ctx.at(key, f"cases[{cid}]")


@check("CS", rules=("CS-001",))
def events_are_bound(ctx: Context) -> None:
    """CS-001: an event between two different nodes needs a runtime edge between them (either direction) or an ``edge=``."""
    nodes = {n["id"] for n in ctx.records("nodes")}
    pairs = {(e.get("sourceId"), e.get("targetId")) for e in ctx.records("edges") if e.get("static") is not True}
    for c in ctx.records("cases"):
        cid = c["caseId"]
        for ev in sorted((e for e in c.get("events", ()) if isinstance(e, dict) and isinstance(e.get("seq"), int)), key=lambda e: e["seq"]):
            a, b = ev.get("from"), ev.get("to")
            if a == b or ev.get("edge") or a not in nodes or b not in nodes or (a, b) in pairs or (b, a) in pairs:
                continue
            path, at = _event_at(ctx, cid, ev["seq"])
            ctx.add("CS-001", path=path, at=at, got=f"{a} -> {b}", expected="a runtime edge between the two nodes, or edge=<id>", entity=f"cases[{cid}]",
                    a=a, b=b)


def _signature(case: dict) -> str:
    events = sorted((e for e in case.get("events", ()) if isinstance(e, dict)), key=lambda e: e.get("seq", 0))
    return repr(([{k: v for k, v in e.items() if k not in ("seq", "ruleId")} for e in events], case.get("policy"), case.get("context"),
                 case.get("initialMachines")))


@check("CS", rules=("CS-005",))
def category_cover(ctx: Context) -> None:
    """CS-005 (WARN): a case that is a renamed copy of an earlier one; and, from six cases on, a category no case covers."""
    cases = ctx.records("cases")
    seen: dict = {}
    for c in cases:
        if not c.get("events"):
            continue
        sig = _signature(c)
        if sig in seen:
            key = f"cases[{c['caseId']}]"
            ctx.add("CS-005", path=key + ".events", at=ctx.at(key), got=f"same events as {seen[sig]}", expected="events that differ from every other case",
                    detail=f"{c['caseId']} is a renamed copy of {seen[sig]}: change the events, parameters, latency or fault")
        else:
            seen[sig] = c["caseId"]
    if len(cases) >= MIN_CASES_FOR_COVER:
        profile = vocab.default_profile()
        wanted = list(profile.ids("caseCategories"))
        have = {profile.alias("caseCategories", c.get("category")) or c.get("category") for c in cases}
        missing = [x for x in wanted if x not in have]
        if missing:
            key = f"cases[{cases[0]['caseId']}]"
            ctx.add("CS-005", path="cases.category", at=ctx.at(key), got="no case of: " + ", ".join(missing), expected="every category covered",
                    detail="add a case for " + ", ".join(missing) + " or accept the gap")


@check("CS", rules=("CS-008",))
def rule_ids_present(ctx: Context) -> None:
    """CS-008 (INFO): events without a ``ruleId`` cannot be named by an expected outcome or a delay."""
    count, first, total = 0, None, 0
    for c in ctx.records("cases"):
        for ev in c.get("events", ()):
            if isinstance(ev, dict):
                total += 1
                if not ev.get("ruleId"):
                    count += 1
                    first = first or (c["caseId"], ev.get("seq"))
    if count:
        path, at = _event_at(ctx, first[0], first[1])
        ctx.add("CS-008", path=path, at=at, got=f"{count} of {total} events", expected="a ruleId on the events that expectations name")


# the wave-2 families register themselves when the package is imported (``rules/__init__`` imports ``cs`` last)
from . import iv as _iv, or_ as _or, pt as _pt  # noqa: E402,F401
