"""Sealed runs for the reading-surface tests (milestone R3): the reply ``oracle.sealed_runs`` would give for a synthetic model, the ``verdicts`` key
that lane I adds to that reply, and the bundle and ``sealed_meta`` a reading build is made from.

Pure Python, no workspace and no ledger: the items are sealed because the test says so (``sealed``/``confirmed`` sets). ``verdicts_of`` is the
REFERENCE of the helper lane I extracts from ``oracle._case_verdict`` / ``_pair_verdict`` (design 2.10, D-32): the same comparisons
(``mutate.violations``, ``check_prediction``) on a run that is already stored, no second simulation. The reading package never does this
comparison; this file is the test-side stand-in until ``sealed_runs`` returns the key, and ``test_read_oracle`` pins its shape.

Every model is synthetic (``tests/atlas/synth.py``).
"""
from __future__ import annotations

import copy
from functools import lru_cache

from gemarc_mcp.atlas import build as BLD
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.derive import diagrams
from gemarc_mcp.atlas.engine import causal_chain, check_prediction, index_model, mutate, run_causal, simulate
from gemarc_mcp.atlas.rules import or_

from . import read_support as RS


def eligible(model: dict) -> tuple:
    """``(case ids, intervention ids)`` that have an assertion that may be sealed (``or_.collect_items``: what ``sealed_runs`` registers)."""
    items = or_.collect_items(model)
    return ([k.split(":", 1)[1] for k, i in items.items() if i.kind == or_.CASE and i.clean], [k.split(":", 1)[1] for k, i in items.items() if i.kind == or_.INTERVENTION and i.clean])


def reply_of(model: dict, lang: str = "zh-CN", sealed_cases=None, sealed_ivs=None, confirmed_cases=(), confirmed_ivs=()) -> dict:
    """The reply of ``oracle.sealed_runs`` for ``model`` when the items in ``sealed_cases`` / ``sealed_ivs`` (``None``: every one that has an assertion)
    are sealed and the ones in ``confirmed_*`` are confirmed (a confirmed item is shown like a sealed one)."""
    ix = index_model(model)
    cases = {c["caseId"]: c for c in model.get("cases", ())}
    ivs = {i["interventionId"]: i for i in model.get("interventions", ())}
    able_cases, able_ivs = eligible(model)
    want_cases = set(able_cases if sealed_cases is None else sealed_cases) | set(confirmed_cases)
    want_ivs = set(able_ivs if sealed_ivs is None else sealed_ivs) | set(confirmed_ivs)
    baselines: dict = {}
    pairs: list = []
    withheld = {"cases": [], "interventions": []}
    for cid, case in cases.items():
        if cid not in want_cases:
            withheld["cases"].append(cid)
            continue
        baselines[cid] = {"state": "confirmed" if cid in confirmed_cases else "sealed", "run": simulate(model, case, None, {"index": ix})}
    for iid, iv in ivs.items():
        if iid not in want_ivs:
            withheld["interventions"].append(iid)
            continue
        used = False
        for cid in iv.get("appliesTo", ()):
            if cid not in baselines:
                continue
            result = run_causal(model, cid, [iv], {"index": ix, "locale": lang})
            pairs.append({"interventionId": iid, "caseId": cid, "state": "confirmed" if iid in confirmed_ivs else "sealed", "result": result,
                          "chain": causal_chain(model, result, iv, ix, lang)})
            used = True
        if not used:
            withheld["interventions"].append(iid)
    units = diagrams.run_units(model, baselines, pairs, lang)
    return {"schema": "gemarc-atlas-sealed-runs/1", "modelSha8": "", "lang": lang, "baselines": baselines, "pairs": pairs, "diagrams": units["units"],
            "issues": units["issues"], "withheld": withheld, "provenance": {"ledger": "ok", "reason": ""}}


def verdicts_of(model: dict, reply: dict) -> dict:
    """``{"case:<id>": {...}, "pair:<iid>|<cid>": {...}}``: the verdict of every shown item, by the comparison ``oracle._case_verdict`` and
    ``_pair_verdict`` make, on the stored runs (the shape of design 2.10; an item the engine could not decide has ``{"undecided": status}``)."""
    items = or_.collect_items(model)
    cases = {c["caseId"]: c for c in model.get("cases", ())}
    ivs = {i["interventionId"]: i for i in model.get("interventions", ())}
    out: dict = {}
    for cid, entry in reply["baselines"].items():
        item, run = items[f"case:{cid}"], entry["run"]
        if run["status"] != "OK":
            out[f"case:{cid}"] = {"undecided": run["status"]}
            continue
        found = mutate.violations({"terminal": item.assertion["terminal"], "outcomes": item.assertion["outcomes"]}, run, cases[cid])
        bad_nodes = [v["nodeId"] for v in found if v["kind"] == "terminal"]
        bad_rules = [v["ruleId"] for v in found if v["kind"] == "outcome"]
        out[f"case:{cid}"] = {"pass": not found, "terminal": [len(bad_nodes), len(item.assertion["terminal"])], "outcomes": [len(bad_rules), len(item.assertion["outcomes"])],
                              "badNodes": bad_nodes, "badRules": bad_rules}
    for pair in reply["pairs"]:
        iid, cid, result = pair["interventionId"], pair["caseId"], pair["result"]
        item = items[f"intervention:{iid}"]
        if result["status"] == "LIMIT_EXCEEDED" or (result.get("diff") or {}).get("truncated"):
            out[f"pair:{iid}|{cid}"] = {"undecided": "LIMIT_EXCEEDED"}
            continue
        pred = check_prediction({**ivs[iid], "expect": {**(ivs[iid].get("expect") or {})}}, result) or {"mismatches": []}
        kinds = {m.get("code") or m.get("kind") for m in pred["mismatches"]}
        bad_nodes = [m["args"]["nodeId"] for m in pred["mismatches"] if m.get("code") == "terminal"]
        out[f"pair:{iid}|{cid}"] = {"pass": not pred["mismatches"], "terminal": [len(bad_nodes), len(item.assertion.get("terminal", {}))], "klass": "outcomeClass" in kinds,
                                    "status": "status" in kinds, "badNodes": bad_nodes}
    return out


def meta_of(reply: dict, model: dict, verdicts: bool = True) -> dict:
    """The ``sealed_meta`` a hook would hand to ``reading.build``: from the one reply, with or without the verdicts."""
    return R.sealed_meta_from_reply(reply, verdicts_of(model, reply) if verdicts else None)


def make(model: dict, lang: str = "zh-CN", reply=None, meta=None, verdicts: bool = True, cfg=None, ui=RS.UI, qa=RS.QA, **kw) -> RS.Built:
    """A reading build of ``model`` with the runs of ``reply`` (default: everything that can be sealed is sealed)."""
    reply = reply if reply is not None else reply_of(model, lang, **kw)
    bundle = BLD.make_bundle(model, sealed=reply, lang=lang, qa=qa)
    meta = meta if meta is not None else meta_of(reply, model, verdicts)
    result = R.build(bundle, meta, cfg or R.ReadConfig(), ui)
    return RS.Built(model, bundle, result, RS.Source(bundle, meta), lang)


@lru_cache(maxsize=None)
def sealed(preset: str, lang: str = "zh-CN", seed: int = 1, mode: str = "asis", html: bool = False, verdicts: bool = True) -> RS.Built:
    """The cached build of a synthetic preset with every sealable item sealed."""
    return make(RS.model_of(preset, seed, mode), lang, verdicts=verdicts, cfg=R.ReadConfig(html=True) if html else None)


@lru_cache(maxsize=None)
def cached_reply(preset: str, lang: str = "zh-CN", seed: int = 1, mode: str = "asis") -> dict:
    return reply_of(RS.model_of(preset, seed, mode), lang)


def copy_reply(reply: dict) -> dict:
    return copy.deepcopy(reply)
