"""IV-001 (compile status equals ``expect.status``) and IV-002 (the sealed prediction, counts only).

* IV-001 (ERROR, full mode) every intervention is compiled by the engine; the compile status (``INVALID`` is the run status
  ``REJECTED``) must equal ``expect.status`` (default ``OK``). A deliberately unfinished element carries
  ``expect.status = DRAFT`` and is then fine. The finding quotes the engine's own error text: that is structure, not an outcome.
* IV-002 (ERROR, full mode) the prediction of an intervention (outcome class, terminal states) disagrees with the engine on a
  case it applies to. It is reported only from the verdict that ``simulate`` already served and the ledger holds (the same
  number of disagreeing nodes, never which), see ``or_.py``.

Both need a model that passes the contract (the engine does not defend itself): ``contract_ok`` computes that once per check.
Standard library only.
"""
from __future__ import annotations

from typing import Mapping

from .. import model as am
from ..engine import compile_deltas, index_model
from .context import Context
from .or_ import INTERVENTION, _fresh, collect_items, current_view
from .registry import Rule, check, register
from .. import diag as dg

register(Rule("IV-001", 2, dg.ERROR, "{detail}", "the intervention's compile status equals expect.status (default OK)", modes=("full",)))
register(Rule("IV-002", 2, dg.ERROR, "{detail}",
              "the sealed prediction of an intervention differs from the engine on a case it applies to (counts only)", modes=("full",)))

MAX_QUOTED = 160


def contract_ok(ctx: Context) -> bool:
    """Whether the model passes ``model.contract_issues`` (computed once per check and kept on the context)."""
    cached = getattr(ctx, "_contract_ok", None)
    if cached is None:
        try:
            cached = not am.contract_issues(ctx.model)
        except Exception:  # noqa: BLE001 - a crash in the checker means "not usable by the engine"
            cached = False
        ctx._contract_ok = cached
    return cached


@check("IV", rules=("IV-001",))
def compile_status(ctx: Context) -> None:
    ivs = ctx.records("interventions")
    if not ivs or not contract_ok(ctx):
        return
    try:
        index = index_model(ctx.model)
    except Exception:  # noqa: BLE001
        return
    for iv in ivs:
        iid = iv.get("interventionId")
        if not isinstance(iid, str):
            continue
        try:
            compiled = compile_deltas(index, [iv])
        except Exception:  # noqa: BLE001
            continue
        status = "REJECTED" if compiled.status == "INVALID" else compiled.status
        expect = iv.get("expect") if isinstance(iv.get("expect"), Mapping) else {}
        wanted = expect.get("status") or "OK"
        if status != wanted:
            key = f"interventions[{iid}]"
            first = (compiled.errors or [""])[0]
            ctx.add("IV-001", path=key + ".expect.status", at=ctx.at(key), got=status, expected=f"expect.status {wanted}",
                    detail=(f"compiles as {status}, expect.status is {wanted}: " + str(first)[:MAX_QUOTED]) if first else
                    f"compiles as {status}, expect.status is {wanted}: fix the intervention or write status={status}")


@check("IV", rules=("IV-002",))
def sealed_prediction(ctx: Context) -> None:
    view = current_view()
    if view is None:
        return
    try:
        sha8 = am.model_sha8(ctx.model)
    except (am.AtlasModelError, UnicodeError, ValueError, TypeError):
        return
    items = collect_items(ctx.model)
    view.bind(items)
    for item in items.values():
        if item.kind != INTERVENTION:
            continue
        entry = view.entries.get(item.key) or {}
        if view.state_of(item)[0] == "unregistered":
            continue
        last = _fresh(entry, "last", sha8)
        if last is None or last.get("pass") is not False:
            continue
        bad = last.get("terminal") or [0, 0]
        parts = [f"{x} of {y} asserted {w}" for x, y, w in ((bad[0], bad[1], "nodes differ"),) if x]
        if last.get("klass"):
            parts.append("the outcome class differs")
        if last.get("status"):
            parts.append("the run status differs")
        key = f"interventions[{item.id}]"
        ctx.add("IV-002", path=key + ".expect", at=ctx.at(key), got=item.key, expected="the sealed prediction to hold (counts only: ids are a recorded reveal)",
                detail=("; ".join(parts) or "the prediction does not hold") + "; fix the intervention, or revise it (user confirms)")
