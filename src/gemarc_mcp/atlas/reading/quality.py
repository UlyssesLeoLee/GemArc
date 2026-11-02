"""The quality page ``Q01`` (family ``Q``, dimension ``QA``): the numbers of the build, the withheld items, the fragments the reading surface
does not print (design A3 2.2 row Q, D-27).

Milestone R1 is the structure-only version: ``meta.qa`` is printed field by field as it is (no field added, none combined: ``blockers`` and
``rules.block`` are two fields of the bundle and stay two lines), ``sealed_meta["withheld"]`` as a table of ids with one fixed phrase (the
bundle has no per-id reason), and the exclusion table of the conservation check. R3 adds the ledger counts (no id and no value of an item that is
not sealed) and lists every unsealed item of the model in the withheld table.
"""
from __future__ import annotations

import json

from ..derive import i18n as T
from .blocks import Seg, Unit, make_item
from .families import Family, code, row, sec, th
from .source import Source
from .text import tx


def qa_lines(src: Source) -> list:
    """``- <field>: <compact JSON>`` for every field of ``meta.qa``, in key order, plus the counts of ``meta``."""
    qa = src.meta.get("qa") if isinstance(src.meta.get("qa"), dict) else {}
    out = []
    for key in sorted(qa):
        out.append(f"- {key}: " + json.dumps(qa[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    counts = src.meta.get("counts")
    if isinstance(counts, dict):
        out.append("- counts: " + json.dumps(counts, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return out


def withheld_rows(src: Source) -> list:
    """``[(id, kind)]`` of every item with no shown result: the model's cases and interventions the ledger does not call sealed or confirmed (so an
    unsealed item is listed even when the caller's ``withheld`` forgot it), then the ids ``sealed_meta`` withholds that the model does not have."""
    rows = [(cid, "case") for cid in src.unsealed_cases()] + [(iid, "intervention") for iid in src.unsealed_interventions()]
    known = {key for key, _kind in rows}
    for kind, ids, model_ids in (("case", src.withheld_cases, set(src.case_ids())), ("intervention", src.withheld_ivs, set(src.intervention_ids()))):
        rows += [(i, kind) for i in ids if i not in model_ids and i not in known]
    return rows


def ledger_lines(src: Source) -> list:
    """Counts only (design 2.10, AM-04): how many cases and interventions are sealed, confirmed and withheld; one line when there is no oracle reply
    at all and one when the verdict data is missing. No id and no result value of any item."""
    lang = src.lang
    cases = src.case_ids()
    sealed = sum(1 for c in cases if src.shown_cases.get(c) == "sealed")
    confirmed = sum(1 for c in cases if src.shown_cases.get(c) == "confirmed")
    ivs = src.intervention_ids()
    have = {iid: next(state for (i, _c), state in src.shown_pairs.items() if i == iid) for iid in ivs if any(i == iid for (i, _c) in src.shown_pairs)}
    out = [tx(lang, "res.ledger_count", what=tx(lang, "res.ledger_cases"), s=sealed, c=confirmed, w=len(cases) - sealed - confirmed, total=len(cases)),
           tx(lang, "res.ledger_count", what=tx(lang, "res.ledger_ivs"), s=sum(1 for v in have.values() if v == "sealed"), c=sum(1 for v in have.values() if v == "confirmed"),
              w=len(ivs) - len(have), total=len(ivs))]
    if not (src.states_known or src.withheld_known):
        out.append("- " + tx(lang, "res.no_oracle"))
    elif not src.verdicts_known and (src.shown_cases or src.shown_pairs):
        out.append("- " + tx(lang, "res.verdicts_absent"))
    return out


def build_q(src: Source, exclusions: list) -> Family:
    """``exclusions`` is a list of ``(group, count, reason)`` the conservation check has verified."""
    lang = src.lang
    bands: list = []
    lines = qa_lines(src)
    if lines:
        bands.append(Seg("qa", [make_item([line], lang, None, "qa") for line in lines]))
    bands.append(Seg("ledger", [make_item([line], lang, None, "ledger") for line in ledger_lines(src)]))
    phrase = tx(lang, "q.withheld_phrase")
    rows = [make_item([row([code(i), tx(lang, "kind." + kind), phrase])], lang, None, i) for i, kind in withheld_rows(src)]
    if rows:
        bands.append(Seg("withheld", rows))
    excl = [make_item([row([T.md_text(group), str(count), T.md_text(reason)])], lang, None, group) for group, count, reason in exclusions]
    if excl:
        bands.append(Seg("exclusions", excl))
    sections = [sec(lang, "qa", "bullets", 3, always=True, empty=tx(lang, "q.none")),
                sec(lang, "ledger", "bullets", 3, always=True, empty=tx(lang, "q.none")),
                sec(lang, "withheld", "table", 3, header=th(lang, "withheld"), always=True, empty=tx(lang, "q.none")),
                sec(lang, "exclusions", "table", 3, header=th(lang, "exclusions"), always=True, empty=tx(lang, "q.none"))]
    return Family("Q", "QA", sections, [Unit("quality", "quality", tx(lang, "family.Q"), bands)], "none", "card", "qa")
