"""Linter for generated documents, diagram text and diagram structure.

Document rules (they read rendered Markdown, so the next lane can call them on whatever it renders):

* ``DC-L-001`` no literal ``undefined``, ``NaN``, ``None`` (whole words) or ``[object`` anywhere;
* ``DC-L-002`` every ``dg:`` link resolves to a known diagram unit, no other link scheme appears, and every id-shaped
  code span that looks like an id of this model (same family prefix as a known id) names a defined id;
* ``DC-L-003`` the twelve chapter headings are present once, in order, with the skeleton titles of the level and language,
  and each chapter has content or an explicit marker (never a blank);
* ``DC-L-004`` every ``[NOT-AUTHORED:slot]`` marker that is left is a **release BLOCK** (one issue per slot).

Diagram rules (they read the units of ``diagrams.py`` and the model):

* ``DG-002`` the status enumeration of a class equals the states of its linked state machine (a hint when the class has an
  enumeration but no linked machine, spec 5.14);
* ``DG-003`` layer direction: a static dependency (``dependsOn``, ``implements``) between components must not point from a
  lower floor to a higher one (``floors.level``);
* ``DG-005`` ER foreign key: a ``references`` edge between two tables needs a column of the source table whose ``fk`` names
  the target (a column given only by name cannot carry ``fk``, so it does not satisfy the rule);
* ``DG-006`` ER policy list as data: the ER unit lists only ``pii`` column flags and ``kind=data`` policies, and a data policy
  whose scope selects no table is reported.

Nothing here raises; every rule has a mutation fixture in ``tests/atlas/test_derive_lint.py``.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from . import diagrams as D
from . import i18n as T
from .policy import Index, parse_scope, policy_issues, str_list, sval

RULE_JUNK = "DC-L-001"
RULE_LINK = "DC-L-002"
RULE_CHAPTER = "DC-L-003"
RULE_MARKER = "DC-L-004"
RULE_CLASS_ENUM = "DG-002"
RULE_LAYER = "DG-003"
RULE_ER_FK = "DG-005"
RULE_ER_POLICY = "DG-006"
MAX_PER_RULE = 25

_HEADING = re.compile(r"^## (\d{1,2})\. (.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_DG_LINK = re.compile(r"(?<!\\)\]\(dg:([^)\s]*)\)")
_ANY_LINK = re.compile(r"(?<!\\)\]\((?!dg:)([^)\s]*)\)")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_ID_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*[.:#-][A-Za-z0-9_.:#-]*$")
_GENERATED = re.compile(r"^(?:RD|BD|DD|PR-[A-Z]+)-[0-9A-Za-z]{8}$")
_SEPARATORS = ".:#-"


# --------------------------------------------------------------------------------------------- helpers

def _prefix(token: str) -> str:
    for i, ch in enumerate(token):
        if ch in _SEPARATORS:
            return token[:i]
    return token


def known_ids(ix: Index) -> set:
    """Every id a document may mention in a code span: elements, derived ids, events, rules, error codes, space ids."""
    ids: set = set()
    for table in (ix.node, ix.edge, ix.machine, ix.case, ix.requirement, ix.evidence, ix.tbd, ix.test, ix.policy,
                  ix.building, ix.floor, ix.room):
        ids.update(table)
    for iv in ix.interventions:
        ids.add(sval(iv.get("interventionId")))
    for n in ix.node:
        ids.update((f"{n}:in", f"{n}:out"))
    for m in ix.machines:
        ids.update(str_list(m.get("states")))
        ids.add(sval(m.get("errorCode")))
        for t in m.get("transitions") if isinstance(m.get("transitions"), list) else []:
            if isinstance(t, Mapping):
                ids.add(sval(t.get("id")))
                ids.add(sval(t.get("errorCode")))
    for cid, case in ix.case.items():
        for position, event in enumerate(case.get("events") if isinstance(case.get("events"), list) else [], start=1):
            if isinstance(event, Mapping):
                seq = event.get("seq") if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool) else position
                ids.add(f"{cid}#{seq}")
                ids.add(sval(event.get("ruleId")))
                ids.add(sval(event.get("errorCode")))
                ids.add(sval(event.get("edge")))
                ids.add(sval(event.get("group")))
        for item in case.get("expectedOutcomes") if isinstance(case.get("expectedOutcomes"), list) else []:
            if isinstance(item, Mapping):
                ids.add(sval(item.get("ruleId")))
                ids.add(sval(item.get("errorCode")))
    for e in ix.evidences:
        ids.add(sval(e.get("filePath")))
    ids.discard("")
    return ids


def _cap(issues: list) -> list:
    """At most ``MAX_PER_RULE`` issues per rule id; the last one of a capped rule says how many were left out."""
    counts: dict = {}
    out: list = []
    skipped: dict = {}
    for item in issues:
        n = counts.get(item["rule"], 0) + 1
        counts[item["rule"]] = n
        if n <= MAX_PER_RULE:
            out.append(item)
        else:
            skipped[item["rule"]] = skipped.get(item["rule"], 0) + 1
    for rule, n in skipped.items():
        out.append(T.issue(rule, "INFO", "-", f"{n} more issues of this rule are not listed"))
    return out


def _lines(text) -> list:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if isinstance(text, str) else []


# --------------------------------------------------------------------------------------------- documents

def parse_chapters(text: str) -> list:
    """``[{number, title, line, body}]`` of the ``## n. title`` headings (code blocks is skipped); ``body`` is the lines up to the next one."""
    chapters: list = []
    inside = False
    for number, line in enumerate(_lines(text), start=1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            if chapters:
                chapters[-1]["body"].append(line)
            continue
        m = _HEADING.match(line)
        if m:
            chapters.append({"number": int(m.group(1)), "title": m.group(2).strip(), "line": number, "body": []})
        elif chapters:
            chapters[-1]["body"].append(line)
    return chapters


def _filled(body: list) -> bool:
    for line in body:
        stripped = line.strip()
        if stripped and not stripped.startswith("###"):
            return True
    return False


def lint_chapters(text: str, lang: str, level: str) -> list:
    """DC-L-003."""
    issues: list = []
    chapters = parse_chapters(text)
    lang = T.normalize_lang(lang)
    expected = list(range(1, T.CHAPTER_COUNT + 1))
    seen = [c["number"] for c in chapters]
    for n in expected:
        count = seen.count(n)
        if count == 0:
            issues.append(T.issue(RULE_CHAPTER, "ERROR", f"chapter {n}", "the chapter heading is missing", expected=T.chapter_heading(lang, level, n)))
        elif count > 1:
            issues.append(T.issue(RULE_CHAPTER, "ERROR", f"chapter {n}", "the chapter heading appears more than once"))
    in_range = [c for c in chapters if c["number"] in expected]
    if [c["number"] for c in in_range] != sorted(c["number"] for c in in_range):
        issues.append(T.issue(RULE_CHAPTER, "ERROR", "chapters", "the chapters are not in order"))
    for c in chapters:
        if c["number"] not in expected:
            issues.append(T.issue(RULE_CHAPTER, "ERROR", f"line {c['line']}", "a chapter number outside 1..12", got=str(c["number"])))
            continue
        title = T.chapter_title(lang, level, c["number"])
        if c["title"] != title:
            issues.append(T.issue(RULE_CHAPTER, "ERROR", f"chapter {c['number']}", "the chapter title is not the skeleton title",
                                  got=c["title"], expected=title))
        if not _filled(c["body"]):
            issues.append(T.issue(RULE_CHAPTER, "ERROR", f"chapter {c['number']}", "the chapter is blank: fill it or write an explicit marker"))
    return issues


def lint_junk(text: str) -> list:
    """DC-L-001 (also used for diagram text and prompts)."""
    issues: list = []
    for number, line in enumerate(_lines(text), start=1):
        for m in T.JUNK_RE.finditer(line):
            issues.append(T.issue(RULE_JUNK, "ERROR", f"line {number}", "a literal that means a value was missing or a type was wrong", got=m.group(0)))
    return issues


def lint_markers(text: str) -> list:
    """DC-L-004: one BLOCK per distinct NOT-AUTHORED slot, with its first line and its count."""
    first: dict = {}
    count: dict = {}
    for number, line in enumerate(_lines(text), start=1):
        for m in T.NOT_AUTHORED_RE.finditer(line):
            slot = m.group(1)
            first.setdefault(slot, number)
            count[slot] = count.get(slot, 0) + 1
    return [T.issue(RULE_MARKER, "BLOCK", f"line {first[slot]}", f"NOT-AUTHORED marker left ({count[slot]}x): author this fact before a release", got=slot)
            for slot in first]


def lint_links(text: str, units=None, ix: Index | None = None, extra_known: Iterable[str] = ()) -> list:
    """DC-L-002. ``units`` (diagram unit ids or unit dicts) enables the ``dg:`` check, ``ix`` the id check."""
    issues: list = []
    unit_set = None
    if units is not None:
        unit_set = {u["id"] if isinstance(u, Mapping) else u for u in units}
    ids = known_ids(ix) if ix is not None else None
    prefixes = {_prefix(i) for i in ids if any(ch in i for ch in _SEPARATORS)} if ids is not None else set()
    extra = set(extra_known)
    inside = False
    for number, line in enumerate(_lines(text), start=1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            continue
        for m in _DG_LINK.finditer(line):
            target = m.group(1)
            if not target or (unit_set is not None and target not in unit_set):
                issues.append(T.issue(RULE_LINK, "ERROR", f"line {number}", "a dg: link names no diagram unit", got=target or "dg:"))
        for m in _ANY_LINK.finditer(line):
            issues.append(T.issue(RULE_LINK, "ERROR", f"line {number}", "only dg: links may appear in a generated document", got=m.group(1)))
        if ids is None:
            continue
        for m in _CODE_SPAN.finditer(line):
            token = m.group(1).replace("\\|", "|").strip()
            if (token in ids or token in extra or _GENERATED.match(token) or not _ID_SHAPE.match(token)):
                continue
            if _prefix(token) in prefixes:
                issues.append(T.issue(RULE_LINK, "ERROR", f"line {number}", "an id-shaped reference names nothing that is defined", got=token))
    return issues


def lint_document(text: str, *, lang: str, level: str, ix: Index | None = None, units=None, extra_known: Iterable[str] = ()) -> list:
    """All document rules on one rendered Markdown text. ``units`` / ``ix`` switch on the link and id checks."""
    try:
        issues = lint_junk(text) + lint_links(text, units, ix, extra_known) + lint_chapters(text, lang, level) + lint_markers(text)
        return _cap(issues)
    except Exception as exc:  # last resort with its own code: the tests assert it never fires
        return [T.issue("DC-L-099", "ERROR", "lint", "internal linter error", got=type(exc).__name__)]


def lint_free_text(text: str) -> list:
    """Rules that need no skeleton: junk and markers (diagram text, prompts, any generated Markdown)."""
    return _cap(lint_junk(text) + lint_markers(text))


def summarize(issues: Iterable[Mapping], text: str | None = None, lang: str = T.PRIMARY_LANG, level: str = "DD") -> dict:
    """The compact outline of a lint run (design: reply of at most 1 KiB): chapters filled/total, junk, links, markers."""
    items = list(issues)
    out = {"junk": sum(1 for i in items if i["rule"] == RULE_JUNK), "unresolvedLinks": sum(1 for i in items if i["rule"] == RULE_LINK),
           "notAuthored": sum(1 for i in items if i["rule"] == RULE_MARKER),
           "blocks": sum(1 for i in items if i["severity"] == "BLOCK"), "errors": sum(1 for i in items if i["severity"] == "ERROR")}
    if text is not None:
        chapters = {c["number"]: c for c in parse_chapters(text)}
        filled = sum(1 for n in range(1, T.CHAPTER_COUNT + 1) if n in chapters and _filled(chapters[n]["body"]))
        out["chapters"] = {"filled": filled, "total": T.CHAPTER_COUNT}
    return out


def release_blockers(issues: Iterable[Mapping]) -> list:
    """The issues that stop a release: DC-L-004 markers (severity BLOCK)."""
    return [i for i in issues if i["severity"] == "BLOCK"]


# --------------------------------------------------------------------------------------------- diagrams

def _states_of(ix: Index, machine_id: str) -> set:
    return set(str_list(ix.machine.get(machine_id, {}).get("states")))


def check_class_enums(ix: Index, units: Iterable[Mapping]) -> list:
    """DG-002."""
    issues: list = []
    for unit in units:
        if unit["type"] != "class":
            continue
        data = unit["data"]
        node_id = unit["covers"]["nodeIds"][0] if unit["covers"]["nodeIds"] else unit["id"]
        enums = data.get("enums") or {}
        machines = data.get("linkedMachines") or []
        for m in machines:
            states = set(m.get("states") or [])
            if not any(set(values) == states for values in enums.values()):
                issues.append(T.issue(RULE_CLASS_ENUM, "WARN", f"class {T.slot_name(node_id)}",
                                      "no status enumeration of the class equals the states of its state machine", got=m.get("id"),
                                      expected=", ".join(sorted(states))))
        if enums and not machines:
            issues.append(T.issue(RULE_CLASS_ENUM, "INFO", f"class {T.slot_name(node_id)}",
                                  "the class has an enumeration but no linked state machine (props.owner plus an owns edge): the check is skipped",
                                  got=node_id))
    return issues


def check_layers(ix: Index) -> list:
    """DG-003."""
    view = ix.profile.item("views", "development-view")
    kinds = set(view["kinds"]) if view and view.get("kinds") else set()
    relations = {r["id"] for r in ix.profile.raw["relations"] if "development-view" in r.get("views", []) and r.get("static")}
    issues: list = []
    for position, edge in enumerate(ix.edges):
        if sval(edge.get("relation")) not in relations:
            continue
        a, b = ix.node.get(sval(edge.get("sourceId"))), ix.node.get(sval(edge.get("targetId")))
        if not a or not b or sval(a.get("kind")) not in kinds or sval(b.get("kind")) not in kinds:
            continue
        la, lb = ix.floor_level(a), ix.floor_level(b)
        if la is not None and lb is not None and la < lb:
            issues.append(T.issue(RULE_LAYER, "WARN", f"edges[{position}]", "the edge target sits on a higher floor than its source",
                                  got=sval(edge.get("id")), expected="source floor level >= target floor level"))
    return issues


def check_er_foreign_keys(ix: Index, units: Iterable[Mapping]) -> list:
    """DG-005."""
    issues: list = []
    for unit in units:
        if unit["type"] != "er":
            continue
        by_id = {e["id"]: e for e in unit["data"].get("entities", [])}
        for rel in unit["data"].get("relations", []):
            source = by_id.get(rel["from"])
            if source is None:
                continue
            if not any(c.get("fk") == rel["to"] for c in source["columns"]):
                issues.append(T.issue(RULE_ER_FK, "WARN", f"edge {T.slot_name(rel['id'])}",
                                      "no column of the source table has fk naming the target table (a column given only by name cannot carry fk)",
                                      got=rel["id"], expected=f"column with fk={rel['to']}"))
    return issues


def check_er_policies(ix: Index, units: Iterable[Mapping]) -> list:
    """DG-006: a ``kind=data`` policy whose scope selects no table is reported; the ER unit lists data and flags only."""
    tables = {n for n, node in ix.node.items() if sval(node.get("kind")) in D.TABLE_KINDS}
    issues: list = []
    for position, row in enumerate(ix.policies):
        if row.get("kind") != "data":
            continue
        parsed = parse_scope(row.get("scope"))
        if parsed is None:
            continue
        kind, arg = parsed
        if kind == "all":
            hit = bool(tables)
        elif kind == "kind":
            hit = arg in D.TABLE_KINDS and bool(tables)
        elif kind == "node":
            hit = arg in tables
        else:
            hit = arg in ix.requirement and bool(tables & set(str_list(ix.requirement[arg].get("nodeIds"))))
        if not hit:
            issues.append(T.issue(RULE_ER_POLICY, "WARN", f"policies[{position}].scope", "a data policy whose scope selects no table is not shown in any ER diagram",
                                  got=sval(row.get("scope")) or sval(row.get("id"))))
    for unit in units:
        if unit["type"] == "er":
            listed = {p["id"] for p in unit["data"].get("policies", [])}
            stray = [i for i in listed if ix.policy.get(i, {}).get("kind") != "data"]
            for i in stray:
                issues.append(T.issue(RULE_ER_POLICY, "ERROR", f"unit {unit['id']}", "the ER unit lists a policy that is not a data policy", got=i))
    return issues


def lint_diagrams(model, units: Iterable[Mapping], profile=None) -> list:
    """DG-002, DG-003, DG-005, DG-006 on the units of ``diagrams.build_units`` and the model."""
    try:
        ix = Index(model, profile)
        listed = list(units)
        return _cap(check_class_enums(ix, listed) + check_layers(ix) + check_er_foreign_keys(ix, listed) + check_er_policies(ix, listed))
    except Exception as exc:
        return [T.issue("DG-099", "ERROR", "lint", "internal diagram lint error", got=type(exc).__name__)]


def lint_diagram_text(units: Iterable[Mapping]) -> list:
    """DC-L-001 and DC-L-004 on the Mermaid text of every unit."""
    out: list = []
    for unit in units:
        for item in lint_free_text(unit.get("mermaid", "")):
            item["path"] = f"unit {unit['id']} {item['path']}"
            out.append(item)
    return out


def lint_model(model, *, lang: str = T.PRIMARY_LANG, documents: Mapping | None = None, units=None, profile=None) -> dict:
    """Everything this module can say about ``model`` (and rendered ``documents``: ``{name: (level, text)}``).

    Returns ``{"issues", "summary"}``; ``issues`` are policy rows, builder issues, DG rules, diagram text and each document.
    """
    ix = Index(model, profile)
    built = D.build_units(model, lang, profile=profile) if units is None else {"units": list(units), "issues": []}
    issues = list(policy_issues(ix)) + list(built["issues"]) + lint_diagrams(model, built["units"], profile)
    seen = {(i["rule"], i["path"], i.get("got")) for i in issues}  # the unit builder already reports DC-L-001 on its own text
    issues += [i for i in lint_diagram_text(built["units"]) if (i["rule"], i["path"], i.get("got")) not in seen]
    for name, (level, text) in (documents or {}).items():
        for item in lint_document(text, lang=lang, level=level, ix=ix, units=built["units"]):
            item["path"] = f"{name} {item['path']}"
            issues.append(item)
    summary = summarize(issues)
    summary["policiesByAi"] = sum(1 for r in ix.policies if r.get("by") == "ai")
    return {"issues": issues, "summary": summary, "units": built["units"]}
