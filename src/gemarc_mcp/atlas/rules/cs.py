"""CS-004: a case traces a requirement.

The other case rules (CS-001 event bound to a runtime edge with pending-bind, CS-002/CS-003 oracles, CS-005, CS-008) need
the engine and belong to wave 2; they plug into the same registry. Standard library only.
"""
from __future__ import annotations

from .context import Context
from .registry import check


@check("CS", rules=("CS-004",))
def traces_requirement(ctx: Context) -> None:
    for c in ctx.records("cases"):
        if not c.get("requirementIds"):
            key = f"cases[{c['caseId']}]"
            ctx.add("CS-004", path=key + ".requirementIds", at=ctx.at(key), got="none", expected=">= 1 requirement id", entity=key)
