"""RQ-001 (trace-complete) and RQ-002 (coverage, INFO); both need the whole project, so they run in full mode only.

* RQ-001 a requirement that names no node is not traced to the design.
* RQ-002 (INFO) the gaps that are legal but worth seeing: requirements no case traces, nodes no requirement names.
  One finding per kind of gap, with the first ids; ``status view=next`` lists the rest.

Standard library only.
"""
from __future__ import annotations

from .context import Context
from .registry import check

LISTED = 3


def _some(ids: list) -> str:
    return ", ".join(ids[:LISTED]) + (" ..." if len(ids) > LISTED else "")


@check("RQ", rules=("RQ-001", "RQ-002"))
def requirements(ctx: Context) -> None:
    reqs = ctx.records("requirements")
    for r in reqs:
        if not r.get("nodeIds"):
            key = f"requirements[{r['id']}]"
            ctx.add("RQ-001", path=key + ".nodeIds", at=ctx.at(key), got="none", expected=">= 1 node that realizes the requirement", entity=key)
    traced = {x for c in ctx.records("cases") for x in c.get("requirementIds", ())}
    no_case = [r["id"] for r in reqs if r["id"] not in traced]
    if no_case:
        ctx.add("RQ-002", path="requirements", at=ctx.at(f"requirements[{no_case[0]}]"), got=f"{len(no_case)} without a case: {_some(no_case)}",
                expected="a case with req=<id> per requirement", detail="add a case that traces each requirement, or accept the gap")
    named = {x for r in reqs for x in r.get("nodeIds", ())}
    machine_ids = {m["id"] for m in ctx.records("machines")}
    free = [n["id"] for n in ctx.records("nodes") if n["id"] not in named and n["id"] not in machine_ids]
    if reqs and free:
        ctx.add("RQ-002", path="nodes", at=ctx.at(f"nodes[{free[0]}]"), got=f"{len(free)} in no requirement: {_some(free)}",
                expected="every node named by a requirement", detail="name them in nodes= of the requirement they realize, or accept the gap")
