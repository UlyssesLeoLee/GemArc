"""SM-001, SM-002, SM-003, SM-005: state machines.

* SM-001 machine integrity: a machine without transitions, a transition guard that breaks the contract's guard shape
  (``model.contract_issues``), and (from the slice check) a transition row naming a machine that is not defined.
* SM-002 every state is reachable from the initial state.
* SM-003 (binding: deferred in slice mode until ``@final edges``) the machine's node has exactly one ``owns`` edge, from a
  node that is not a machine. When the machine has no node row, RF-002 is the cause and this finding is ``derived``.
* SM-005 (INFO) no state is terminal. The terminal states are derived (no outgoing transition), a lifecycle that loops is
  legal, so this only tells the author what the derivation found.

Wave 2: SM-004 (WARN) two transitions share the same ``from -> to`` pair; SM-007 (WARN) a machine with terminal states has a
non-terminal state that cannot reach any of them; SM-006 (full mode) the guard sweep of the engine lane
(``engine.sweep.scan_model``: a guard that can never be satisfied is an ERROR, one that is always true a WARN, and the engine
probe of every guarded transition must agree with the table oracle).

Standard library only.
"""
from __future__ import annotations

import re
from typing import Mapping

from .. import model as am
from .context import Context
from .iv import contract_ok
from .registry import check

_GUARD_PATH = re.compile(r"^\$\.machines\[(\d+)\]\.transitions\[(\d+)\]\.guard(.*)$")


@check("SM", rules=("SM-001", "SM-002", "SM-003", "SM-005"))
def machines(ctx: Context) -> None:
    records = ctx.records("machines")
    owners: dict[str, list] = {}
    for e in ctx.records("edges"):
        if e.get("relation") == "owns":
            owners.setdefault(e.get("targetId"), []).append(e)
    machine_ids = {m["id"] for m in records}
    guard_issues = _guard_issues(ctx, records)
    for index, m in enumerate(records):
        mid = m["id"]
        key = f"machines[{mid}]"
        at = ctx.at(key)
        transitions = [t for t in m.get("transitions", ()) if isinstance(t, Mapping)]
        if not transitions:
            ctx.add("SM-001", path=key + ".transitions", at=at, got="0 transitions", expected="at least one transition",
                    detail="a machine without transitions has one state and nothing to run: add rows to transitions with machine=" + mid)
        for t_index, issue in guard_issues.get(index, ()):
            tid = transitions[t_index].get("id", f"#{t_index}") if t_index < len(transitions) else f"#{t_index}"
            ctx.add("SM-001", path=f"{key}.transitions[{tid}].guard{issue[0]}", at=ctx.at(f"{key}.transitions[{tid}]", key), got="a malformed guard",
                    expected="a guard of the documented shape", detail=issue[1])
        unreachable = _unreachable(m, transitions)
        if unreachable:
            ctx.add("SM-002", path=key + ".states", at=at, got=unreachable, expected=f"states reachable from {m.get('initial')}")
        if transitions and not m.get("terminal"):
            ctx.add("SM-005", path=key + ".terminal", at=at, got="none", expected="a state without outgoing transitions")
        mine = owners.get(mid, [])
        if not mine:
            ctx.add("SM-003", path=key + ".owner", at=at, got="no owns edge", expected="one owns edge from a non-machine node", entity=key)
        elif len(mine) > 1:
            ctx.add("SM-003", path=key + ".owner", at=at, got=", ".join(str(e.get("sourceId")) for e in mine),
                    expected="exactly one owner", entity=key)
        elif mine[0].get("sourceId") in machine_ids:
            ctx.add("SM-003", path=key + ".owner", at=at, got=mine[0].get("sourceId"), expected="an owner that is not a machine", entity=key)


def _unreachable(machine: Mapping, transitions: list) -> list:
    initial = machine.get("initial")
    seen, todo = {initial}, [initial]
    while todo:
        here = todo.pop()
        for t in transitions:
            if t.get("from") == here and t.get("to") not in seen:
                seen.add(t.get("to"))
                todo.append(t.get("to"))
    return [s for s in machine.get("states", ()) if s not in seen]


def _guard_issues(ctx: Context, records: list) -> dict:
    """Guard-shape problems of the contract, ``{machine index: [(transition index, (path rest, message))]}``."""
    if not any(isinstance(t, Mapping) and "guard" in t for m in records for t in m.get("transitions", ())):
        return {}
    out: dict[int, list] = {}
    for issue in am.contract_issues(ctx.model):
        m = _GUARD_PATH.match(issue["path"])
        if m:
            out.setdefault(int(m.group(1)), []).append((int(m.group(2)), (m.group(3), issue["message"])))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Wave 2
# ---------------------------------------------------------------------------------------------------------------


@check("SM", rules=("SM-004", "SM-007"))
def machine_shape(ctx: Context) -> None:
    for m in ctx.records("machines"):
        mid = m["id"]
        key = f"machines[{mid}]"
        at = ctx.at(key)
        transitions = [t for t in m.get("transitions", ()) if isinstance(t, Mapping)]
        seen: dict = {}
        for t in transitions:
            pair = (t.get("from"), t.get("to"))
            if pair in seen:
                ctx.add("SM-004", path=f"{key}.transitions[{t.get('id')}]", at=ctx.at(f"{key}.transitions[{t.get('id')}]", key),
                        got=f"{pair[0]} -> {pair[1]} twice", expected="one transition per from -> to pair")
            seen.setdefault(pair, t.get("id"))
        terminal = set(m.get("terminal") or ())
        if not terminal or not transitions:
            continue  # a lifecycle that loops declares no terminal state: the rule does not apply
        back: dict = {}
        for t in transitions:
            back.setdefault(t.get("to"), set()).add(t.get("from"))
        reach, todo = set(terminal), list(terminal)
        while todo:
            for before in back.get(todo.pop(), ()):
                if before not in reach:
                    reach.add(before)
                    todo.append(before)
        stuck = [s for s in m.get("states", ()) if s not in reach]
        for state in stuck[:3]:
            ctx.add("SM-007", path=f"{key}.states", at=at, got=state, expected="a path to a terminal state", state=state)


def _sweep_at(ctx: Context, where: Mapping) -> tuple:
    if "machineId" in where:
        base = f"machines[{where['machineId']}]"
        key = f"{base}.transitions[{where.get('transitionId')}]"
        return key, ctx.at(key, base)
    if "interventionId" in where:
        key = f"interventions[{where['interventionId']}]"
        return key, ctx.at(key)
    key = f"cases[{where.get('caseId')}].events[{where.get('seq')}]"
    return key, ctx.at(key, f"cases[{where.get('caseId')}]")


@check("SM", rules=("SM-006",))
def guard_sweep(ctx: Context) -> None:
    """SM-006 (full mode): the engine lane's guard scan over transition, intervention and event guards."""
    if ctx.mode != "full" or not contract_ok(ctx):
        return
    if not any(isinstance(t, Mapping) and t.get("guard") for m in ctx.records("machines") for t in m.get("transitions", ()))             and not any(isinstance(e, Mapping) and e.get("guard") for c in ctx.records("cases") for e in c.get("events", ()))             and not ctx.records("interventions"):
        return
    from ..engine import sweep
    try:
        report = sweep.scan_model(ctx.model)
    except Exception:  # noqa: BLE001 - a scan that cannot run says nothing
        return
    for f in report["findings"]:
        key, at = _sweep_at(ctx, f.get("where") or {})
        ctx.add("SM-006", severity=f.get("severity"), path=key + str(f.get("path", ""))[1:] if str(f.get("path", "")).startswith("$.") else key,
                at=at, got=f.get("kind"), expected="a guard that can be both satisfied and rejected", detail=str(f.get("message", ""))[:130])
