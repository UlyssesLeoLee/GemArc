"""The result blocks of the 4D and 5D pages (families ``C`` and ``V``) and the lines that name them (design A3 2.2 rows C and V, 2.10, D-32, AM-07, AM-08).

ORACLE_FIRST on this surface. A result is printed only for an item that is in ``bundle.runs`` AND that ``sealed_meta`` calls sealed or confirmed
(``Source.shown_cases`` and ``Source.shown_pairs``); every other case and intervention gets one fixed sentence and its id goes to the quality
page. This module renders; it never decides and never computes: the engine is not imported, no run is made, no state is compared. The verdict of
a block (pass, fail, how many of the nodes the author asserted differ) is read from ``sealed_meta["verdicts"]`` exactly as the oracle stored it;
without that key the block says so in one fixed line and has no verdict line (D-32). What this module does write down itself is plain
presentation of stored facts: the engine terminal state as the run recorded it, the author's expectation as the model has it, the evidence ids
and the weakest grade of the evidence the model cites (a minimum over ranks, never a result).

Vocabulary. The *result lines* are the lines made by the templates ``read.res.case_head``, ``pair_head``, ``verdict_*``, ``mismatch``,
``question``, ``conclusion``, ``first*`` and ``chain`` and the rows of the tables ``final``, ``steps`` and ``effects``; they exist only inside
the sections ``results``, ``pairs``, ``final``, ``steps`` and ``effects`` (RS-115 checks that). The words of an expectation the author wrote
(an outcome class, a state) are not result lines.
"""
from __future__ import annotations

import json
import re

from .. import model as am
from ..derive import i18n as T
from ..derive import policy as P
from . import blocks as B
from .blocks import Item, Seg, Unit, make_item
from .families import Family, clip_name, row, sec, th
from .source import Source
from .text import pattern, tx

HOME = "home"
ID_CAP = 8                  # ids printed in a basis or an applies-to list before ``+k``
STEP_CAP = 40               # rows of the causal-step table of one pair (design 2.2: more is written n / m)
EFFECT_CAP = 40
FINAL_CAP = 400             # rows of the side-by-side terminal table of one case (a whole-model assertion is the only way to get near it)
IDLE = "Idle"               # the default state of a node: the engine run does not record it (``build.compact_run`` drops it)
OK_STATES = ("sealed", "confirmed")
RESULT_SECTIONS = ("results", "pairs", "final", "steps", "effects")


# ---------------------------------------------------------------------------------------------------- small text helpers

def pair_key(iid: str, cid: str) -> str:
    return T.code(iid) + "×" + T.code(cid)


def ids_text(lang: str, ids, cap: int = ID_CAP) -> str:
    ids = [i for i in ids if isinstance(i, str) and i]
    if len(ids) <= cap:
        return ", ".join(T.code(i) for i in ids)
    return tx(lang, "res.ids_more", ids=", ".join(T.code(i) for i in ids[:cap]), k=len(ids) - cap)


def none_text(lang: str, reason: str) -> str:
    return tx(lang, "res.none", reason=tx(lang, "res.why." + reason))


def state_text(src: Source, value) -> str:
    return T.md_text(value) if isinstance(value, str) and value else "-"


def nonneg_pair(value):
    """``(a, b)`` when ``value`` is a list of two non-negative integers, else ``None``."""
    if (isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)):
        return int(value[0]), int(value[1])
    return None


# ---------------------------------------------------------------------------------------------------- basis and grade

def case_basis(src: Source, cid: str) -> tuple:
    """``(basis text, grade text)`` of a case: the evidence ids the model cites for it and the weakest grade among the evidence that resolves."""
    lang, ix = src.lang, src.ix
    case = ix.case.get(cid) or {}
    ids = P.str_list(case.get("evidenceIds"))
    if not ids:
        return none_text(lang, "no_case_evidence"), none_text(lang, "no_case_evidence")
    levels = [P.sval(ix.evidence[i].get("verification")) for i in ids if i in ix.evidence]
    levels = [lv for lv in levels if src.profile.has("verification", lv)]
    if not levels:
        return ids_text(lang, ids), none_text(lang, "no_resolvable")
    return ids_text(lang, ids), am.weakest(levels, profile=src.profile)


def pair_basis(src: Source, chain, outcome_class) -> tuple:
    """``(basis text, grade text)`` of a pair from ``chain.rootDesign`` and ``chain.confidence`` as the engine stored them."""
    lang = src.lang
    chain = chain if isinstance(chain, dict) else {}
    root = chain.get("rootDesign") if isinstance(chain.get("rootDesign"), dict) else {}
    evidence = P.str_list(root.get("evidenceIds"))
    confidence = chain.get("confidence") if isinstance(chain.get("confidence"), dict) else None
    reason = "off_path" if outcome_class == "NO_DIFFERENCE" else "no_grade"
    basis = ids_text(lang, evidence) if evidence else none_text(lang, "off_path" if outcome_class == "NO_DIFFERENCE" else "no_root_evidence")
    if confidence and isinstance(confidence.get("weakest"), str) and confidence["weakest"]:
        counts = confidence.get("counts") if isinstance(confidence.get("counts"), dict) else {}
        order = list(src.profile.verification_order)
        parts = [f"{g} {counts[g]}" for g in order if isinstance(counts.get(g), int)] + [f"{g} {n}" for g, n in sorted(counts.items()) if g not in order and isinstance(n, int)]
        grade = tx(lang, "res.grade_counts", weakest=confidence["weakest"], counts=" · ".join(parts)) if parts else confidence["weakest"]
        return basis, T.md_text(grade)
    return basis, none_text(lang, reason)


# ---------------------------------------------------------------------------------------------------- verdict lines

def _mismatch_line(src: Source, entry: dict, klass_bad=False, status_bad=False) -> list:
    lang = src.lang
    items: list = []
    nodes = [n for n in entry.get("badNodes", []) if isinstance(n, str)] if isinstance(entry.get("badNodes"), list) else []
    rules = [r for r in entry.get("badRules", []) if isinstance(r, str)] if isinstance(entry.get("badRules"), list) else []
    if nodes:
        items.append(tx(lang, "res.mm_nodes", ids=ids_text(lang, nodes, 20)))
    if rules:
        items.append(tx(lang, "res.mm_rules", ids=ids_text(lang, rules, 20)))
    if klass_bad:
        items.append(tx(lang, "res.mm_class"))
    if status_bad:
        items.append(tx(lang, "res.mm_status"))
    return [tx(lang, "res.mismatch", items="; ".join(items))] if items and entry.get("pass") is False else []


def case_decision(src: Source, cid: str, run: dict) -> tuple:
    """What ``sealed_meta`` says about a shown case: ``("none",)`` (no verdict data at all), ``("undecided", reason key)`` or
    ``("decided", entry, (bad nodes, asserted nodes), (bad rules, asserted rules))``. Nothing is computed: the entry is read as stored."""
    if not src.verdicts_known:
        return ("none",)
    entry = src.verdicts.get(f"case:{cid}")
    terminal = nonneg_pair(entry.get("terminal")) if isinstance(entry, dict) else None
    outcomes = nonneg_pair(entry.get("outcomes")) if isinstance(entry, dict) else None
    if isinstance(entry, dict) and "undecided" in entry or run.get("status") != "OK":
        return ("undecided", "run_not_ok")
    if not isinstance(entry, dict) or terminal is None or outcomes is None or not isinstance(entry.get("pass"), bool):
        return ("undecided", "no_verdict")
    return ("decided", entry, terminal, outcomes)


def pair_decision(src: Source, iid: str, cid: str, run_ok: bool) -> tuple:
    """The same for a shown pair: ``("decided", entry, (bad nodes, asserted nodes))``."""
    if not src.verdicts_known:
        return ("none",)
    entry = src.verdicts.get(f"pair:{iid}|{cid}")
    terminal = nonneg_pair(entry.get("terminal")) if isinstance(entry, dict) else None
    if isinstance(entry, dict) and "undecided" in entry or not run_ok:
        return ("undecided", "run_not_ok")
    if not isinstance(entry, dict) or terminal is None or not isinstance(entry.get("pass"), bool):
        return ("undecided", "no_verdict")
    return ("decided", entry, terminal)


def case_verdict_lines(src: Source, cid: str, run: dict, basis: str, grade: str) -> list:
    """The verdict line (and the line of what differs) of a shown case, from ``sealed_meta["verdicts"]``; one fixed line without the key."""
    lang = src.lang
    nature = tx(lang, "res.nature")
    decision = case_decision(src, cid, run)
    if decision[0] == "none":
        return [tx(lang, "res.verdict_none")]
    if decision[0] == "undecided":
        return [tx(lang, "res.verdict_undecided", reason=tx(lang, "res.why." + decision[1]), basis=basis, grade=grade, nature=nature)]
    _kind, entry, terminal, outcomes = decision
    word = tx(lang, "res.word." + ("pass" if entry["pass"] else "fail"))
    line = tx(lang, "res.verdict_case", word=word, k=terminal[1], n=len(src.node_order), j=terminal[0], ra=outcomes[1], rj=outcomes[0], basis=basis, grade=grade,
              nature=nature)
    return [line] + _mismatch_line(src, entry)


def pair_verdict_lines(src: Source, iid: str, cid: str, run_ok: bool, klass: str, basis: str, grade: str) -> list:
    lang = src.lang
    nature = tx(lang, "res.nature")
    decision = pair_decision(src, iid, cid, run_ok)
    if decision[0] == "none":
        return [tx(lang, "res.verdict_none")]
    if decision[0] == "undecided":
        return [tx(lang, "res.verdict_undecided", reason=tx(lang, "res.why." + decision[1]), basis=basis, grade=grade, nature=nature)]
    _kind, entry, terminal = decision
    word = tx(lang, "res.word." + ("pass" if entry["pass"] else "fail"))
    line = tx(lang, "res.verdict_pair", word=word, klass=T.md_text(klass), k=terminal[1], n=len(src.node_order), j=terminal[0], basis=basis, grade=grade, nature=nature)
    return [line] + _mismatch_line(src, entry, entry.get("klass") is True, entry.get("status") is True)


# ---------------------------------------------------------------------------------------------------- C: the result of a case

def case_result(src: Source, cid: str) -> tuple:
    """``(head item, [row items of the side-by-side table])`` of a shown case."""
    lang, ix = src.lang, src.ix
    run = src.baselines[cid]
    run = run if isinstance(run, dict) else {}
    state = src.shown_cases[cid]
    status = run.get("status") if isinstance(run.get("status"), str) else "-"
    trace = run.get("trace") if isinstance(run.get("trace"), list) else []
    basis, grade = case_basis(src, cid)
    lines = [tx(lang, "res.case_head", ref=T.code(cid), ledger=tx(lang, "res.ledger." + state), status=T.md_text(status), events=len(trace))]
    lines += case_verdict_lines(src, cid, run, basis, grade)
    head = make_item(lines, lang, None, cid)
    rows: list = []
    if status == "OK":
        expected = (ix.case.get(cid) or {}).get("expectedTerminalState")
        expected = expected if isinstance(expected, dict) else {}
        final = run.get("finalState") if isinstance(run.get("finalState"), dict) else {}
        nodes = sorted(set(k for k in expected if isinstance(k, str)) | set(k for k in final if isinstance(k, str)), key=lambda n: (src.node_pos.get(n, 10 ** 9), n))
        for node in nodes[:FINAL_CAP]:
            want = tx(lang, "res.not_asserted") if node not in expected else state_text(src, expected.get(node))
            got = state_text(src, final.get(node, IDLE))
            rows.append(make_item([row([T.code(cid), T.code(node), want, got])], lang, None, cid))
    return head, rows


# ---------------------------------------------------------------------------------------------------- V: the interventions

def _json_short(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition_lines(src: Source, iv: dict) -> list:
    """The block of an intervention: the author's definition as the model has it (a view of the model: the intervention has no fragment)."""
    lang = src.lang
    iid = P.sval(iv.get("interventionId"))
    kind = P.sval(iv.get("type"))
    title = T.clip(P.sval(iv.get("description")) or iid, 80)
    lines = ["### " + T.md_text(title), tx(lang, "v.ident", id=T.code(iid), type=T.md_text(kind) or T.na())]
    fixed = ("interventionId", "type", "description", "appliesTo", "expect", "note", "why")
    params = [f"{k}={v if isinstance(v, str) else _json_short(v)}" for k, v in sorted(iv.items()) if isinstance(k, str) and k not in fixed]
    if params:
        lines.append(tx(lang, "v.target", text=T.md_text("; ".join(params))))
    applies = P.str_list(iv.get("appliesTo"))
    if applies:
        lines.append(tx(lang, "v.applies", ids=ids_text(lang, applies, 20)))
    expect = iv.get("expect") if isinstance(iv.get("expect"), dict) else {}
    parts: list = []
    if expect.get("outcomeClass"):
        parts.append(tx(lang, "v.expect_class", text=T.md_text(expect["outcomeClass"])))
    terminal = expect.get("terminal") if isinstance(expect.get("terminal"), dict) else {}
    if terminal:
        parts.append(tx(lang, "v.expect_terminal", text=T.md_text("; ".join(f"{n}={s}" for n, s in sorted(terminal.items()) if isinstance(n, str) and isinstance(s, str)))))
    if expect.get("status"):
        parts.append(tx(lang, "v.expect_status", text=T.md_text(expect["status"])))
    lines.append(tx(lang, "v.expect", text=" · ".join(parts) if parts else tx(lang, "v.expect_none")))
    if P.sval(iv.get("note")).strip():
        lines.append(tx(lang, "v.note", text=T.md_text(iv["note"])))
    if P.sval(iv.get("why")).strip():
        lines.append(tx(lang, "v.why", text=T.md_text(iv["why"])))
    return lines


def pair_result(src: Source, iid: str, cid: str) -> tuple:
    """``(item, step rows, effect rows)`` of a shown pair, from ``bundle.runs.pairs`` as the engine stored them."""
    lang = src.lang
    pair = next(p for p in src.pairs if p.get("interventionId") == iid and p.get("caseId") == cid)
    chain = pair.get("chain") if isinstance(pair.get("chain"), dict) else {}
    diff = pair.get("diff") if isinstance(pair.get("diff"), dict) else {}
    status = pair.get("status") if isinstance(pair.get("status"), str) else "-"
    klass = diff.get("outcomeClass") if isinstance(diff.get("outcomeClass"), str) else (chain.get("outcomeClass") if isinstance(chain.get("outcomeClass"), str) else "-")
    key = pair_key(iid, cid)
    basis, grade = pair_basis(src, chain, klass)
    lines = [tx(lang, "res.pair_head", ref=key, ledger=tx(lang, "res.ledger." + src.shown_pairs[(iid, cid)]), status=T.md_text(status), klass=T.md_text(klass))]
    if isinstance(chain.get("question"), str) and chain["question"].strip():
        lines.append(tx(lang, "res.question", text=T.md_text(chain["question"])))
    if isinstance(chain.get("headline"), str) and chain["headline"].strip():
        lines.append(tx(lang, "res.conclusion", text=T.md_text(chain["headline"]), basis=basis, grade=grade, nature=tx(lang, "res.nature")))
    first = chain.get("firstDivergence") if isinstance(chain.get("firstDivergence"), dict) else None
    if first is not None and isinstance(first.get("index"), int):
        lines.append(tx(lang, "res.first", index=first["index"] + 1, time=first.get("logicalTime", "-")))
    else:
        lines.append(tx(lang, "res.first_none"))
    lines += pair_verdict_lines(src, iid, cid, status == "OK", klass, basis, grade)
    steps = [s for s in (chain.get("steps") or []) if isinstance(s, dict)]
    effects = [e for e in (chain.get("effects") or []) if isinstance(e, dict)]
    step_rows: list = []
    effect_rows: list = []
    if klass not in ("NO_DIFFERENCE", "-") and (steps or effects):
        shown_steps, shown_effects = steps[:STEP_CAP], effects[:EFFECT_CAP]
        lines.append(tx(lang, "res.chain", steps_n=len(shown_steps), steps_m=len(steps), eff_n=len(shown_effects), eff_m=len(effects)))
        for what, n, m in ((tx(lang, "v.steps"), len(shown_steps), len(steps)), (tx(lang, "v.effects"), len(shown_effects), len(effects))):
            if n < m:
                lines.append(tx(lang, "v.truncated", what=what, n=n, m=m, k=m - n))
        for s in shown_steps:
            event = " ".join(x for x in (P.sval(s.get("eventType")), P.sval(s.get("sourceNodeId")) + ">" + P.sval(s.get("targetNodeId"))) if x and x != ">")
            outcome = P.sval(s.get("outcome")) if not s.get("aOutcome") or s.get("aOutcome") == s.get("outcome") else f"{s.get('aOutcome')}->{s.get('outcome')}"
            step_rows.append(make_item([row([key, T.md_text(P.sval(s.get("id")) or "-"), T.md_text(P.sval(s.get("side")) or "-"), T.md_text(P.sval(s.get("role")) or "-"),
                                             T.md_text(event) or "-", T.md_text(outcome) or "-", T.md_text(T.clip(P.sval(s.get("why")), 60)) or "-"])], lang, None, iid))
        for e in shown_effects:
            effect_rows.append(make_item([row([key, T.code(P.sval(e.get("nodeId"))), clip_name(e.get("name")), state_text(src, e.get("a")), state_text(src, e.get("b"))])],
                                         lang, None, iid))
    return make_item(lines, lang, None, iid + ">" + cid), step_rows, effect_rows


def build_v(src: Source) -> Family:
    lang, ix = src.lang, src.ix
    units: list = []
    for iv in ix.interventions:
        iid = P.sval(iv.get("interventionId"))
        if not iid:
            continue
        applies = list(dict.fromkeys(P.str_list(iv.get("appliesTo"))))
        shown = [cid for cid in applies if (iid, cid) in src.shown_pairs]
        bands = [Seg("ivs", [make_item(definition_lines(src, iv), lang, {1: (iid, "intervention", HOME)}, iid)])]
        pair_items: list = []
        step_rows: list = []
        effect_rows: list = []
        if not shown:
            pair_items.append(make_item(["- " + tx(lang, "v.unsealed", id=T.code(iid))], lang, None, iid))
        else:
            for cid in applies:
                if cid in shown:
                    item, steps, effects = pair_result(src, iid, cid)
                    pair_items.append(item)
                    step_rows.extend(steps)
                    effect_rows.extend(effects)
                else:
                    pair_items.append(make_item(["- " + tx(lang, "v.pair_unsealed", ref=pair_key(iid, cid))], lang, None, iid))
        bands.append(Seg("pairs", pair_items))
        if step_rows:
            bands.append(Seg("steps", step_rows))
        if effect_rows:
            bands.append(Seg("effects", effect_rows))
        units.append(Unit(iid, "intervention", T.clip(P.sval(iv.get("description")) or iid, 60), bands))
    sections = [sec(lang, "ivs", "blocks", 2), sec(lang, "pairs", "bullets", 3), sec(lang, "steps", "table", 3, header=th(lang, "steps")),
                sec(lang, "effects", "table", 3, header=th(lang, "effects"))]
    return Family("V", "5D", sections, units, "iv", "page", "ivs")


# ---------------------------------------------------------------------------------------------------- reading a written page back

def result_regexes(lang: str) -> dict:
    """The templates of the result lines as regular expressions (the checks derive them from ``derive/i18n``, they write no sentence of their own)."""
    num, text = r"\d+", r".+?"
    rx = {
        "case_head": pattern(lang, "res.case_head", ref=r"`.+`", ledger=text, status=text, events=num),
        "pair_head": pattern(lang, "res.pair_head", ref=r"`.+`×`.+`", ledger=text, status=text, klass=text),
        "verdict_case": pattern(lang, "res.verdict_case", word=text, k=num, n=num, j=num, ra=num, rj=num, basis=text, grade=text, nature=text),
        "verdict_pair": pattern(lang, "res.verdict_pair", word=text, klass=text, k=num, n=num, j=num, basis=text, grade=text, nature=text),
        "verdict_undecided": pattern(lang, "res.verdict_undecided", reason=text, basis=text, grade=text, nature=text),
        "verdict_none": pattern(lang, "res.verdict_none"),
        "mismatch": pattern(lang, "res.mismatch", items=r".+"),
        "question": pattern(lang, "res.question", text=r".*"),
        "conclusion": pattern(lang, "res.conclusion", text=text, basis=text, grade=text, nature=text),
        "first": pattern(lang, "res.first", index=num, time=text),
        "first_none": pattern(lang, "res.first_none"),
        "chain": pattern(lang, "res.chain", steps_n=num, steps_m=num, eff_n=num, eff_m=num),
        "truncated": pattern(lang, "v.truncated", what=text, n=num, m=num, k=num),
    }
    return {name: re.compile("^" + expr + "$") for name, expr in rx.items()}


_REGEX_CACHE: dict = {}


def regexes(lang: str) -> dict:
    key = lang if lang == "zh-CN" else "en-US"
    if key not in _REGEX_CACHE:
        _REGEX_CACHE[key] = result_regexes(key)
    return _REGEX_CACHE[key]


def line_kind(lang: str, line: str):
    """The name of the result template a line follows (``case_head``, ``verdict_case`` ...), or ``None``. A line that starts like a verdict but
    follows no template of the verdict is reported by ``is_verdict_like``."""
    for name, rx in regexes(lang).items():
        if rx.match(line):
            return name
    return None


def verdict_prefix(lang: str) -> str:
    """The words every verdict line starts with (the text of the template before its first placeholder)."""
    template = tx(lang, "res.verdict_none")
    return template.split(":")[0] + ":"


def is_verdict_like(lang: str, line: str) -> bool:
    return line.startswith(verdict_prefix(lang))


def has_results(lang: str, lines) -> bool:
    """Does a page body carry a result block? (A case or pair head line: the one line a block cannot do without.)"""
    rx = regexes(lang)
    return any(rx["case_head"].match(line) or rx["pair_head"].match(line) for line in lines)
