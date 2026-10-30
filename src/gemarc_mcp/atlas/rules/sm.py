"""SM-001, SM-002, SM-003, SM-005: state machines.

* SM-001 machine integrity: a machine without transitions, a transition guard that breaks the contract's guard shape
  (``model.contract_issues``), and (from the slice check) a transition row naming a machine that is not defined.
* SM-002 every state is reachable from the initial state.
* SM-003 (binding: deferred in slice mode until ``@final edges``) the machine's node has exactly one ``owns`` edge, from a
  node that is not a machine. When the machine has no node row, RF-002 is the cause and this finding is ``derived``.
* SM-005 (INFO) no state is terminal. The terminal states are derived (no outgoing transition), a lifecycle that loops is
  legal, so this only tells the author what the derivation found.

Standard library only.
"""
from __future__ import annotations

import re
from typing import Mapping

from .. import model as am
from .context import Context
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
