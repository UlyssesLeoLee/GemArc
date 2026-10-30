"""Deterministic mutants of a case, for the kill-rate check (rule OR-004).

A mutant is a small, mechanical change of one case: either one participant is disabled (an ordinary ``disable_module``
what-if) or one event is deleted. A mutant is *killed* when the case's expectation set is violated on the engine result
of the mutant. A case whose expectations survive every mutant constrains nothing that the case exercises.

Per case there are at most ``DEFAULT_CAP`` (12) mutants:

* single-participant disable for the (at most 8) participants with the highest degree (number of events of the case that
  touch the node; ties by node id), and
* single-event deletion for at most 4 events spread evenly over the case (first and last included).

With a smaller ``cap`` the two groups shrink in proportion (cap 6 gives 4 and 2). Mutants are listed in a fixed order, so the
same model gives the same report. Nothing is random; nothing is written.

The expectation set (the ``assertions`` argument) is one of:

* ``None``: taken from the case itself: ``expectedTerminalState`` (node -> state, compared with the final state) and
  ``expectedOutcomes`` (``{ruleId, outcome, [errorCode]}``, each must be matched by some trace entry);
* a mapping ``{"terminal": {...}, "outcomes": [...]}`` of the same two parts;
* a callable ``f(run) -> sequence`` returning the violations of a run (an empty sequence means the expectations hold);
  this is how the oracle lane supplies its own assertion semantics.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .deltas import Index, compile_deltas, index_model
from .sim import simulate

__all__ = ["DEFAULT_CAP", "MAX_DISABLE", "MAX_DELETE", "participants", "degrees", "generate_mutants", "assertion_set",
           "violations", "run_mutants", "mutation_report"]

DEFAULT_CAP = 12
MAX_DISABLE = 8
MAX_DELETE = 4


def participants(case: Mapping) -> list[str]:
    """Distinct event endpoints of ``case`` in order of first appearance."""
    seen: dict[str, None] = {}
    for event in sorted(case.get("events", []), key=lambda e: e["seq"]):
        for key in ("from", "to"):
            node = event.get(key)
            if isinstance(node, str) and node:
                seen.setdefault(node, None)
    return list(seen)


def degrees(case: Mapping) -> dict[str, int]:
    """Number of events of ``case`` that touch each participant (a self-event counts once)."""
    out: dict[str, int] = {}
    for event in case.get("events", []):
        for node in {event.get("from"), event.get("to")}:
            if isinstance(node, str) and node:
                out[node] = out.get(node, 0) + 1
    return out


def _spread(count: int, k: int) -> list[int]:
    """``k`` indexes spread evenly over ``range(count)``, first and last included, without duplicates."""
    if count <= 0 or k <= 0:
        return []
    if k >= count:
        return list(range(count))
    if k == 1:
        return [0]
    picked: list[int] = []
    for i in range(k):
        position = (i * (count - 1) + (k - 1) // 2) // (k - 1)
        if position not in picked:
            picked.append(position)
    return picked


def _split(cap: int) -> tuple[int, int]:
    """``(disable slots, delete slots)`` for a mutant cap."""
    if cap <= 0:
        return 0, 0
    delete = min(MAX_DELETE, cap // 3)
    return min(MAX_DISABLE, cap - delete), delete


def generate_mutants(case: Mapping, cap: int = DEFAULT_CAP) -> list[dict]:
    """The mutants of ``case``: ``{id, kind, target, deltas}`` (disable) or ``{id, kind, target, seq}`` (delete)."""
    case_id = case["caseId"]
    disable_slots, delete_slots = _split(cap)
    deg = degrees(case)
    ranked = sorted(participants(case), key=lambda node: (-deg[node], node))[:disable_slots]
    mutants: list[dict] = [
        {"id": f"{case_id}:disable:{node}", "kind": "disable", "target": node,
         "deltas": [{"type": "disable_module", "targetNodeId": node}]} for node in ranked]
    events = sorted(case.get("events", []), key=lambda e: e["seq"])
    for position in _spread(len(events), delete_slots):
        seq = events[position]["seq"]
        mutants.append({"id": f"{case_id}:delete:{seq}", "kind": "delete", "target": seq, "seq": seq})
    return mutants


# --------------------------------------------------------------------------------------------------------
# Expectation set
# --------------------------------------------------------------------------------------------------------


def assertion_set(case: Mapping) -> dict:
    """The expectation set a case carries: ``{"terminal": {...}, "outcomes": [...]}`` (copies)."""
    return {"terminal": dict(case.get("expectedTerminalState") or {}),
            "outcomes": [dict(o) for o in case.get("expectedOutcomes") or [] if isinstance(o, Mapping)]}


def violations(assertions: Mapping | Callable | None, run: Mapping, case: Mapping | None = None) -> list[dict]:
    """The expectations that do not hold on ``run`` (empty list = they hold).

    Each entry is ``{"kind": "terminal", "nodeId", "expected", "actual"}`` or ``{"kind": "outcome", "ruleId", "outcome"}``;
    a callable's own return values are passed through as a list.
    """
    if callable(assertions):
        return list(assertions(run))
    spec = assertions if assertions is not None else assertion_set(case or {})
    out: list[dict] = []
    final = run.get("finalState") or {}
    for node, expected in (spec.get("terminal") or {}).items():
        actual = final.get(node)
        if actual != expected:
            out.append({"kind": "terminal", "nodeId": node, "expected": expected, "actual": actual})
    for want in spec.get("outcomes") or []:
        matched = any(
            entry["ruleId"] == want.get("ruleId") and entry["outcome"] == want.get("outcome")
            and (want.get("errorCode") is None or entry["errorCode"] == want.get("errorCode"))
            for entry in run.get("trace", []))
        if not matched:
            out.append({"kind": "outcome", "ruleId": want.get("ruleId"), "outcome": want.get("outcome")})
    return out


# --------------------------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------------------------


def _mutant_run(model: Mapping, case: Mapping, mutant: Mapping, ix: Index) -> dict:
    if mutant["kind"] == "disable":
        compiled = compile_deltas(ix, mutant["deltas"])
        return simulate(model, case, None, {"index": ix, "compiled": compiled})
    pruned = {**case, "events": [e for e in case.get("events", []) if e["seq"] != mutant["seq"]]}
    return simulate(model, pruned, None, {"index": ix})


def run_mutants(model: Mapping, case: Mapping, assertions: Mapping | Callable | None = None, *, cap: int = DEFAULT_CAP,
                index: Index | None = None) -> dict:
    """Mutants of one case and which of them the expectation set kills.

    Returns ``{caseId, baselineHolds, baselineViolations, total, killed, rate, zeroKilled, mutants: [...]}``; each mutant is
    ``{id, kind, target, killed, violations}`` (``violations`` is a count). ``rate`` is ``killed / total`` (``None`` for no
    mutants). When the expectations already fail on the unmodified case (``baselineHolds`` false) every mutant is trivially
    "killed" and the numbers say nothing about the strength of the expectations: callers must check the flag.
    """
    ix = index or index_model(model)
    base_run = simulate(model, case, None, {"index": ix})
    base = violations(assertions, base_run, case)
    mutants = generate_mutants(case, cap)
    rows = []
    for mutant in mutants:
        found = violations(assertions, _mutant_run(model, case, mutant, ix), case)
        rows.append({"id": mutant["id"], "kind": mutant["kind"], "target": mutant["target"], "killed": bool(found),
                     "violations": len(found)})
    killed = sum(1 for row in rows if row["killed"])
    return {"caseId": case["caseId"], "baselineHolds": not base, "baselineViolations": len(base), "total": len(rows),
            "killed": killed, "rate": round(killed / len(rows), 4) if rows else None,
            "zeroKilled": bool(rows) and killed == 0, "mutants": rows}


def mutation_report(model: Mapping, assertions: Mapping[str, Any] | Callable[[Mapping], Any] | None = None, *,
                    cap: int = DEFAULT_CAP, case_ids: Iterable[str] | None = None, index: Index | None = None) -> dict:
    """``run_mutants`` for every case (or ``case_ids``): ``{cases: [...], total, killed, rate}``.

    ``assertions`` is ``None`` (each case's own expectations), a mapping ``caseId -> expectation set`` (a case that is
    missing from it uses its own), or a callable ``f(case) -> expectation set``.
    """
    ix = index or index_model(model)
    wanted = set(case_ids) if case_ids is not None else None
    rows = []
    for case in model.get("cases", []):
        if wanted is not None and case["caseId"] not in wanted:
            continue
        if callable(assertions):
            spec = assertions(case)
        elif assertions is not None:
            spec = assertions.get(case["caseId"])
        else:
            spec = None
        rows.append(run_mutants(model, case, spec, cap=cap, index=ix))
    total = sum(r["total"] for r in rows)
    killed = sum(r["killed"] for r in rows)
    return {"cases": rows, "total": total, "killed": killed, "rate": round(killed / total, 4) if total else None}
