"""Prompts (six types in v1) assembled from per-language templates and the facts of a selection.

* ``REQ``, ``BD``, ``DD``: document prompts. They hand a downstream AI the goal, the selected elements, the grounds
  (evidence), the constraints (policies and data-derived items), the section structure of the document level, the
  acceptance items and a compact fact table, and say again and again: use only these facts, write a marker where a fact is
  missing. The document itself is rendered by the document builder; this prompt is the instruction side.
* ``implement``: hands an AI the selected elements, their authored cases and expected states and the same grounds,
  constraints and acceptance items; it needs no sealing (the authored expectation is the author's own text, not a verdict).
* ``test``, ``failure-analysis``: only for **sealed** cases (the oracle lane supplies ``sealed_case_ids``). Until it does
  they return the empty state "author the expectation first": no prompt body and no expectation value is emitted.
  ``failure-analysis`` also needs the readable causal chain (``chain_lines``).

``generate_prompt`` never raises: bad selection ids and missing facts are issues, missing values are
``[NOT-AUTHORED:...]`` markers, and every text from the model goes through the Markdown hygiene of ``i18n``. The text
fields of the prompt object (``goal``, ``targets``, ``constraints``, ``acceptance``, ``chain``) are already
Markdown-ready (user text escaped, markers verbatim), so the Markdown writer prints them as they are.

The prompt id hashes the type, the selection and ``model_sha8``: the same selection on a changed model gets a new id.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from . import diagrams as D
from . import i18n as T
from . import lint as L
from .policy import Index, Scope, applicable_policies, mapping, resolve_selection, str_list, sval

TYPES = ("REQ", "BD", "DD", "implement", "test", "failure-analysis")
DOC_TYPES = ("REQ", "BD", "DD")
SEALED_TYPES = ("test", "failure-analysis")
TYPE_CODE = {"REQ": "REQ", "BD": "BD", "DD": "DD", "implement": "IMPL", "test": "TEST", "failure-analysis": "FAIL"}
PROMPT_VERSION = "1"
MAX_ROWS = 60
MAX_TARGET_NAMES = 6
RULE_TYPE = "PR-000"
RULE_EMPTY = "PR-001"
RULE_FACT = "PR-002"
RULE_EVIDENCE = "PR-003"
RULE_CHAIN = "PR-004"
RULE_INTERNAL = "PR-099"
PROMPT_FIELDS = ("id", "type", "version", "lang", "modelSha8", "requirementIds", "nodeIds", "edgeIds", "caseIds", "goal",
                 "targets", "evidence", "constraints", "artifacts", "acceptance", "status", "context")


def level_of(ptype: str) -> str:
    """The document level a prompt type works at: REQ, BD, DD; the sealed types work at the deepest level."""
    return ptype if ptype in DOC_TYPES else "DD"


def prompt_id(ptype: str, model_sha8: str, selection: Mapping) -> str:
    """``PR-<TYPE>-<8 hex>`` of the type, the canonical selection and the model stamp."""
    canon = json.dumps({k: sorted(selection.get(k, [])) for k in sorted(selection)}, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256("\n".join([ptype, model_sha8, canon]).encode("utf-8")).hexdigest()[:8]
    return f"PR-{TYPE_CODE.get(ptype, 'X')}-{digest}"


def _mk(slot) -> T.Raw:
    """A NOT-AUTHORED marker as trusted text (kept verbatim by ``fill`` and the Markdown writer)."""
    return T.Raw(T.not_authored(slot))


# --------------------------------------------------------------------------------------------- facts

def _state_text(value, slot: str) -> T.Raw:
    """Markdown-ready ``id=State, ...`` of an expectedTerminalState mapping; a missing or malformed value is a marker."""
    if not isinstance(value, Mapping) or not value:
        return _mk(slot)
    parts = []
    for key, state in value.items():
        if isinstance(key, str) and isinstance(state, str):
            parts.append(f"{T.md_text(T.clip(key, 60))}={T.md_text(T.clip(state, 40))}")
        else:
            parts.append(T.not_authored(slot))
    return T.Raw(", ".join(parts))


def _evidence_rows(ix: Index, scope: Scope) -> tuple:
    ids: list = []

    def take(values):
        for v in str_list(values):
            if v not in ids:
                ids.append(v)

    for n in scope.node_ids:
        take(ix.node[n].get("evidenceIds"))
    for e in scope.edge_ids:
        take(ix.edge[e].get("evidenceIds"))
    for r in scope.requirement_ids:
        take(ix.requirement[r].get("evidenceIds"))
    for c in scope.case_ids:
        take(ix.case[c].get("evidenceIds"))
    ids.sort()
    rows, issues = [], []
    for eid in ids:
        rec = ix.evidence.get(eid)
        if rec is None:
            issues.append(T.issue(RULE_EVIDENCE, "WARN", "evidence", "an evidence id is used but not defined: the row keeps the id and a marker", got=eid))
            rows.append({"evidenceId": eid, "ref": "", "topic": "", "verification": ""})
            continue
        path = sval(rec.get("filePath"))
        span = rec.get("lineRange")
        if path and isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in span):
            path = f"{path}:{span[0]}-{span[1]}"
        rows.append({"evidenceId": eid, "ref": path, "topic": sval(rec.get("topic")), "verification": sval(rec.get("verification"))})
    return rows, issues


def _constraints(ix: Index, lang: str, level: str, scope: Scope, issues: list) -> list:
    """Markdown-ready rule lines: generic rules, data-derived lines for the elements in scope, policies in scope, TBDs touched."""
    out = [T.md_text(t) for t in T.tr_list(lang, "prompts.constraints.base")]
    tpl = lambda key: T.tr(lang, "prompts.constraints." + key)  # noqa: E731
    for nid in scope.node_ids:
        node = ix.node[nid]
        props = mapping(node.get("props"))
        machine = ix.machine.get(nid)
        if machine is not None:
            states = str_list(machine.get("states"))
            out.append(T.fill(tpl("machine"), esc=True, id=nid, states=", ".join(states) if states else _mk(T.slot_name("machine.states", nid))))
        dedup = mapping(props.get("dedup"))
        if dedup:
            out.append(T.fill(tpl("dedup"), esc=True, id=nid, mode=sval(dedup.get("mode")) or _mk(T.slot_name("props.dedup.mode", nid))))
        if props.get("aggregator") is True:
            out.append(T.fill(tpl("aggregator"), esc=True, id=nid))
        if sval(node.get("kind")) in D.TABLE_KINDS:
            columns, _ = D.normalise_columns(props.get("columns"), nid, ix.node_pos.get(nid, 0))
            names = ", ".join(c["name"] for c in columns)
            out.append(T.fill(tpl("entity"), esc=True, id=nid, columns=names or _mk(T.slot_name("props.columns", nid))))
            if not names:
                issues.append(T.issue(RULE_FACT, "WARN", f"nodes.{T.slot_name(nid)}.props.columns", "the table has no columns", got=nid))
    for row in applicable_policies(ix, level, scope):
        text = sval(row.get("text"))
        out.append(T.md_text(text) if text.strip() else T.not_authored(T.slot_name("policy.text", row.get("id"))))
    tbd_ids: list = []
    for rid in scope.requirement_ids:
        for t in str_list(ix.requirement[rid].get("tbdIds")):
            if t not in tbd_ids:
                tbd_ids.append(t)
    for t in tbd_ids:
        out.append(T.fill(tpl("tbd"), esc=True, id=t))
    return out


def _acceptance(ix: Index, lang: str, scope: Scope, issues: list) -> list:
    """Markdown-ready acceptance items derived mechanically from expectations and test ids."""
    tpl = lambda key: T.tr(lang, "prompts.acceptance." + key)  # noqa: E731
    out: list = []
    for cid in scope.case_ids:
        case = ix.case[cid]
        state = _state_text(case.get("expectedTerminalState"), T.slot_name("case.expectedTerminalState", cid))
        if T.NOT_AUTHORED_RE.search(state):
            issues.append(T.issue(RULE_FACT, "WARN", f"cases.{T.slot_name(cid)}.expectedTerminalState", "the case has no expected end state", got=cid))
        out.append(T.fill(tpl("case_terminal"), esc=True, id=cid, state=state))
        outcomes = case.get("expectedOutcomes")
        for item in outcomes if isinstance(outcomes, list) else []:
            if not isinstance(item, Mapping):
                continue
            code = sval(item.get("errorCode"))
            out.append(T.fill(tpl("outcome"), esc=True, id=cid, rule=sval(item.get("ruleId")) or _mk("outcome.ruleId"),
                              outcome=sval(item.get("outcome")) or _mk("outcome.outcome"),
                              code=T.Raw(f" ({T.md_text(code)})") if code else T.Raw("")))
    for rid in scope.requirement_ids:
        tests = str_list(ix.requirement[rid].get("tstIds"))
        out.append(T.fill(tpl("tests"), esc=True, id=rid, tests=", ".join(tests) if tests else _mk(T.slot_name("requirement.tstIds", rid))))
    if not out:
        out.append(T.md_text(tpl("fallback")))
    return out


def _targets_text(ix: Index, lang: str, scope: Scope) -> T.Raw:
    """Markdown-ready names of the selected elements (the first few), or the 'nothing selected' words."""
    names = [ix.label_of(n) or n for n in scope.node_ids][:MAX_TARGET_NAMES]
    if not names:
        names = [sval(ix.requirement[r].get("title")) or r for r in scope.requirement_ids][:MAX_TARGET_NAMES]
    if not names:
        names = list(scope.case_ids)[:MAX_TARGET_NAMES]
    if not names:
        return T.Raw(T.md_text(T.tr(lang, "skeleton.doc_info.unselected")))
    extra = len(scope.node_ids) - MAX_TARGET_NAMES
    return T.Raw(", ".join(T.md_text(n) for n in names) + (f" (+{extra})" if extra > 0 else ""))


def build_prompt(ix: Index, ptype: str, scope: Scope, lang: str, issues: list, selection: Mapping, chain=None) -> dict:
    """The prompt object (``PROMPT_FIELDS``, plus ``chain`` for failure analysis)."""
    level = level_of(ptype)
    evidence, found = _evidence_rows(ix, scope)
    issues.extend(found)
    targets = _targets_text(ix, lang, scope)
    sha8 = ix.sha8()
    prompt = {
        "id": prompt_id(ptype, sha8, selection), "type": ptype, "version": PROMPT_VERSION, "lang": lang, "modelSha8": sha8,
        "requirementIds": list(scope.requirement_ids), "nodeIds": list(scope.node_ids), "edgeIds": list(scope.edge_ids),
        "caseIds": list(scope.case_ids),
        "goal": T.fill(T.tr(lang, f"prompts.goal.{ptype}"), esc=True, targets=targets), "targets": str(targets),
        "evidence": evidence,
        "constraints": _constraints(ix, lang, level, scope, issues),
        "artifacts": T.tr_list(lang, f"prompts.artifacts.{ptype}"),
        "acceptance": _acceptance(ix, lang, scope, issues),
        "status": "Draft",
        "context": {"requirements": [ix.requirement[r] for r in scope.requirement_ids], "nodes": [ix.node[n] for n in scope.node_ids],
                    "edges": [ix.edge[e] for e in scope.edge_ids], "cases": [ix.case[c] for c in scope.case_ids],
                    "tbds": list(ix.tbds)},
    }
    if chain is not None:
        prompt["chain"] = [T.md_text(line) for line in chain if isinstance(line, str)]
    return prompt


# --------------------------------------------------------------------------------------------- markdown

def _table(headers, rows: list, lang: str) -> list:
    lines = T.md_table(headers, rows[:MAX_ROWS])
    if len(rows) > MAX_ROWS:
        lines.append(f"[+{len(rows) - MAX_ROWS}] " + T.md_text(T.tr(lang, "prompts.headings.targets")))
    return lines


def _node_rows(ix: Index, node_ids: Iterable[str]) -> list:
    return [[T.code(n), T.md_cell(ix.node[n].get("name"), T.slot_name("node.name", n)), T.md_cell(ix.node[n].get("kind"), T.slot_name("node.kind", n)),
             T.md_cell(ix.node[n].get("sub"), T.slot_name("node.sub", n))] for n in node_ids]


def _event_rows(case: Mapping) -> list:
    rows = []
    events = case.get("events") if isinstance(case.get("events"), list) else []
    for position, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            continue
        extra = {k: v for k, v in event.items() if k not in ("seq", "type", "from", "to", "ruleId")}
        seq = event.get("seq") if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool) else position
        rows.append([str(seq), T.md_cell(event.get("type"), "event.type"), T.code(event.get("from")) or T.not_authored("event.from"),
                     T.code(event.get("to")) or T.not_authored("event.to"), T.code(event.get("ruleId")) or T.na(),
                     T.md_text(T.scalar(extra)) if extra else T.na()])
    return rows


def _fact_tables(ix: Index, prompt: Mapping, lang: str) -> list:
    out: list = []
    if prompt["edgeIds"]:
        rows = [[T.code(e), T.code(ix.edge[e].get("sourceId")) or T.not_authored("edge.sourceId"),
                 T.code(ix.edge[e].get("targetId")) or T.not_authored("edge.targetId"),
                 T.md_cell(ix.edge[e].get("relation"), T.slot_name("edge.relation", e)),
                 T.md_cell(ix.edge[e].get("label"), T.slot_name("edge.label", e))] for e in prompt["edgeIds"]]
        out += _table(T.tr_list(lang, "skeleton.th.edge_min"), rows, lang) + [""]
    if prompt["requirementIds"]:
        rows = [[T.code(r), T.md_cell(ix.requirement[r].get("title"), T.slot_name("requirement.title", r)),
                 T.md_cell(ix.requirement[r].get("source"), T.slot_name("requirement.source", r)),
                 ", ".join(T.code(t) for t in str_list(ix.requirement[r].get("tstIds"))) or T.not_authored(T.slot_name("requirement.tstIds", r))]
                for r in prompt["requirementIds"]]
        out += _table(T.tr_list(lang, "skeleton.th.requirement"), rows, lang) + [""]
    if prompt["caseIds"]:
        rows = [[T.code(c), T.md_cell(ix.case[c].get("title"), T.slot_name("case.title", c)),
                 T.md_cell(ix.case[c].get("category"), T.slot_name("case.category", c)),
                 ", ".join(T.code(r) for r in str_list(ix.case[c].get("requirementIds"))) or T.na(),
                 _state_text(ix.case[c].get("expectedTerminalState"), T.slot_name("case.expectedTerminalState", c))]
                for c in prompt["caseIds"]]
        out += _table(T.tr_list(lang, "skeleton.th.case"), rows, lang)
    return out


def to_markdown(ix: Index, prompt: Mapping, lang: str, units: Iterable[Mapping] | None = None) -> str:
    """The prompt as Markdown. Model text is escaped, ids are code spans, nothing is printed through ``str()``."""
    ptype = prompt["type"]
    head = lambda key: T.tr(lang, "prompts.headings." + key)  # noqa: E731
    out = [f"# {T.md_text(T.tr(lang, 'prompts.title.' + ptype))}", ""]
    if ptype in DOC_TYPES:
        out += [T.md_text(T.tr(lang, "skeleton.label")), ""]
    out += [f"- {head('id')}: {T.code(prompt['id'])}", f"- {head('status')}: {T.md_text(prompt['status'])}",
            f"- {T.tr(lang, 'skeleton.doc_info.model')}: {T.code(prompt['modelSha8'])}", ""]
    out += [f"## {head('goal')}", prompt["goal"], "", f"## {head('targets')}"]
    if prompt["nodeIds"] or prompt["requirementIds"] or prompt["caseIds"]:
        if prompt["nodeIds"]:
            out += _table(T.tr_list(lang, "skeleton.th.node_req")[:4], _node_rows(ix, prompt["nodeIds"]), lang)
        if prompt["requirementIds"]:
            out += ["", " / ".join(T.code(r) for r in prompt["requirementIds"][:MAX_ROWS])]
        if prompt["caseIds"]:
            out += ["", " / ".join(T.code(c) for c in prompt["caseIds"][:MAX_ROWS])]
    else:
        out.append(T.empty_line(lang, "nodes"))
    out += ["", f"## {head('evidence')}"]
    if prompt["evidence"]:
        rows = [[T.code(e["evidenceId"]), T.md_text(e["ref"]) or T.not_authored(T.slot_name("evidence.filePath", e["evidenceId"])),
                 T.md_text(e["topic"]) or T.not_authored(T.slot_name("evidence.topic", e["evidenceId"])), T.md_text(e["verification"]) or T.na()]
                for e in prompt["evidence"]]
        out += _table(T.tr_list(lang, "skeleton.th.evidence"), rows, lang)
    else:
        out.append(T.na())
    out += ["", f"## {head('constraints')}"] + [f"- {c}" for c in prompt["constraints"]]
    out += ["", f"## {head('artifacts')}"] + [f"- {T.md_text(a)}" for a in prompt["artifacts"]]
    if ptype in DOC_TYPES:
        out += ["", f"## {head('structure')}"]
        out += [f"{n}. {T.md_text(T.chapter_title(lang, ptype, n))}" for n in range(1, T.CHAPTER_COUNT + 1)]
    out += ["", f"## {head('acceptance')}"] + [f"- {a}" for a in prompt["acceptance"]]
    if ptype in DOC_TYPES:
        out += ["", f"## {head('objects')}", ""] + _fact_tables(ix, prompt, lang)
    else:
        out += ["", f"## {head('cases')}"]
        for cid in prompt["caseIds"]:
            case = ix.case[cid]
            out += ["", f"### {T.code(cid)} {T.md_text(case.get('title'))}".rstrip(),
                    f"- {T.tr(lang, 'skeleton.field.expected')}: " + _state_text(case.get("expectedTerminalState"), T.slot_name("case.expectedTerminalState", cid))]
            out += [""] + T.md_table(T.tr_list(lang, "skeleton.th.event"), _event_rows(case)[:MAX_ROWS])
    if prompt.get("chain"):
        out += ["", f"## {head('chain')}"] + [f"- {line}" for line in prompt["chain"]]
    if units is not None:
        line = D.links_line(units, prompt["nodeIds"], lang)
        if line:
            out += ["", line]
    out += ["", f"## {head('rules')}"] + [f"- {T.md_text(r)}" for r in T.tr_list(lang, "prompts.rules")]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------------------------- entry points

def empty_result(ptype: str, lang: str, issues: list, reason: str) -> dict:
    """The 'author the expectation first' state: a heading and one sentence, no prompt body, no expectation value."""
    text = f"# {T.md_text(T.tr(lang, 'prompts.title.' + ptype))}\n\n{T.NONE_IN_SCOPE} {T.md_text(T.tr(lang, 'prompts.empty'))}\n"
    issues.append(T.issue(RULE_EMPTY, "INFO", "prompt", reason, got=ptype))
    return {"ok": False, "empty": True, "type": ptype, "lang": lang, "id": None, "prompt": None, "markdown": text, "issues": issues}


def generate_prompt(model, ptype, selection=None, lang: str = T.PRIMARY_LANG, *, sealed_case_ids: Iterable[str] | None = None,
                    chain_lines: Iterable[str] | None = None, units: Iterable[Mapping] | None = None, profile=None) -> dict:
    """``{"ok", "empty", "type", "lang", "id", "prompt", "markdown", "issues"}``; never raises.

    ``selection`` is ``{"nodeIds", "edgeIds", "caseIds", "requirementIds"}`` (``None``: the whole model). The sealed types
    need ``sealed_case_ids`` to cover every selected case; with ``None`` (no oracle lane yet) they return the empty state.
    """
    lang = T.normalize_lang(lang)
    issues: list = []
    try:
        if ptype not in TYPES:
            return {"ok": False, "empty": False, "type": T.scalar(ptype), "lang": lang, "id": None, "prompt": None, "markdown": "",
                    "issues": [T.issue(RULE_TYPE, "ERROR", "type", "unknown prompt type", got=T.scalar(ptype), expected=", ".join(TYPES))]}
        ix = Index(model, profile)
        scope, found = resolve_selection(ix, selection)
        issues.extend(found)
        if ptype in SEALED_TYPES:
            sealed = set(sealed_case_ids) if sealed_case_ids is not None else set()
            relevant = scope.case_ids if selection is None else [c for c in str_list(mapping(selection).get("caseIds")) if c in ix.case]
            if not relevant:
                return empty_result(ptype, lang, issues, "select at least one case and author its expectation first")
            unsealed = [c for c in relevant if c not in sealed]
            if unsealed:
                return empty_result(ptype, lang, issues, f"{len(unsealed)} of {len(relevant)} selected cases are not sealed")
            if ptype == "failure-analysis" and chain_lines is None:
                return empty_result(ptype, lang, issues, "the readable causal chain comes from the engine result, which is not available")
        chain = list(chain_lines) if chain_lines is not None else None
        prompt = build_prompt(ix, ptype, scope, lang, issues, scope.as_selection(), chain)
        text = to_markdown(ix, prompt, lang, units)
        issues.extend(L.lint_junk(text))  # the author's own text may carry a literal that means "missing" (DC-L-001)
        return {"ok": True, "empty": False, "type": ptype, "lang": lang, "id": prompt["id"], "prompt": prompt, "markdown": text,
                "issues": issues}
    except Exception as exc:  # last resort, reported with a distinct code; the deletion matrix asserts it never fires
        issues.append(T.issue(RULE_INTERNAL, "ERROR", "prompt", "internal prompt builder error", got=type(exc).__name__))
        return {"ok": False, "empty": False, "type": T.scalar(ptype), "lang": lang, "id": None, "prompt": None, "markdown": "",
                "issues": issues}


def to_json(prompt: Mapping) -> str:
    """Deterministic JSON of a prompt object (sorted keys, no timestamps)."""
    return json.dumps(prompt, ensure_ascii=False, sort_keys=True, indent=2, default=lambda value: "") + "\n"


def prompt_files(model, selection=None, lang: str = T.PRIMARY_LANG, *, types: Iterable[str] = DOC_TYPES, units=None) -> dict:
    """``{"<id>.md": markdown}`` for ``dist/prompts/``; empty-state results are skipped."""
    files = {}
    for ptype in types:
        result = generate_prompt(model, ptype, selection, lang, units=units)
        if result["ok"]:
            files[f"{result['id']}.md"] = result["markdown"]
    return files
