"""The author-side rules of the interface family (``UX-*``), their own registry and the slice / full check pipeline.

The registry is independent of the 5D one (``atlas/rules/registry.py``): the shared registry pins the 31 wave 1 rules and the
shared phase order, and a UX rule never runs inside a 5D check. A rule here is ``{id, wave, severity, hint template, modes}``;
severity is always taken from this registry (the 5D default would make every unknown rule an ERROR).

Wave numbers (design A3 6.3): wave 1 is the syntax-near structure (references, tree, tap sources, routes, sizes, file-name safety, text
lengths: ``UX-001..003``, ``UX-006..011``) and runs in slice and full mode; wave 2 would be the layout rules (``UX-101..104``,
registered by the layout lane together with their check functions: a rule without a check is never registered); wave 3 is the
whole-prototype and link rules (``UX-005``, ``UX-201..207``). Of wave 3, ``UX-201``, ``UX-202`` and ``UX-205`` are *binding*: in
slice mode they wait for ``@final nav`` (a half-written navigation would report them wrongly), ``UX-206`` degrades in slice mode
(pending / probable typo) and ``UX-005``, ``UX-203``, ``UX-204``, ``UX-207`` run in full mode only.

``check_sources`` / ``check_expansion`` are the one entry point of ``put`` and ``check``: parse, expand, run the registered rules, return a
``UiCheckResult``. Slice mode reports only findings located in the incoming slices (the committed rest of the prototype is ordered
first); full mode is the closed world: every unresolved reference is an ERROR and every red draft is ST-009.
Verdict rules (``UV-*``) are the verdict lane's and are not registered here. Standard library only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from .. import diag as dg
from . import atl, expand as ex, rows as rw, vocab
from . import model as um
from .rows import ParsedSlice

#: A slice above this size is SZ-001 (a screen costs about 750 bytes, so about eight screens).
SOFT_SLICE_BYTES = 6 * 1024
MAX_WIDGETS_PER_SCREEN = 40
TITLE_LIMIT = 40
TEXT_LIMIT = 120


# ---------------------------------------------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One registry entry. ``binding``: deferred in slice mode until ``@final nav`` (counted as pending-bind). ``modes``: where it runs."""

    id: str
    wave: int
    severity: str
    hint: str
    summary: str
    modes: tuple = ("slice", "full")
    binding: bool = False

    @property
    def phase(self) -> str:
        return self.id.split("-", 1)[0]


REGISTRY: dict = {}
#: ``(order, rule ids, function)`` in registration order.
CHECKS: list = []


def register(rule: Rule) -> Rule:
    if rule.severity not in dg.SEVERITIES:
        raise ValueError(f"{rule.id}: bad severity {rule.severity!r}")
    if rule.wave not in (1, 2, 3):
        raise ValueError(f"{rule.id}: wave must be 1, 2 or 3")
    if len(rule.hint) > dg.HINT_LIMIT:
        raise ValueError(f"{rule.id}: hint template is too long")
    REGISTRY[rule.id] = rule
    return rule


def check(rules: tuple) -> Callable:
    """Decorator: register a check function that reports the given rule ids."""
    def wrap(fn: Callable) -> Callable:
        CHECKS[:] = [c for c in CHECKS if c[2] is not fn]
        CHECKS.append((len(CHECKS), tuple(rules), fn))
        return fn
    return wrap


def rule(rule_id: str) -> Rule:
    try:
        return REGISTRY[rule_id]
    except KeyError:
        raise KeyError(f"unknown interface rule id {rule_id!r}") from None


def rules(wave_set=None) -> list:
    """Registered rules (of the given wave numbers) in id order."""
    return sorted((r for r in REGISTRY.values() if wave_set is None or r.wave in wave_set), key=lambda r: r.id)


def hint_of(rule_id: str, **values) -> str:
    return REGISTRY[rule_id].hint.format_map(_Keep(values))


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


E, W, I = dg.ERROR, dg.WARN, dg.INFO
_REF = "{detail}"
for _r in (
    Rule("ST-009", 1, E, "red draft {slice}: fix it and put again; check full and build refuse until none is left", "a red draft is present", modes=("full",)),
    Rule("SZ-001", 1, W, "{size} bytes: split the slice, 4-6 screens per file are cheapest to repair", "slice larger than 6 KiB"),
    Rule("UX-001", 1, E, _REF, "a screen, widget or variant that is referenced is defined (widget screen and parent, in=, variants.screen, nav from and to)"),
    Rule("UX-002", 1, E, _REF, "the widget tree is valid: parents are containers, no cycle, a tab sits in a tabs group"),
    Rule("UX-003", 1, E, "navigate from a button, link, tab, menu, card, list, table, image or icon, or fix the from cell", "a nav row starts from a widget that can be tapped"),
    Rule("UX-006", 1, W, "{first} already uses this route: give every screen its own", "routes are unique"),
    Rule("UX-007", 1, W, "split the screen: move a region into a dialog or a second screen", "a screen has at most 40 widgets"),
    Rule("UX-008", 1, E, "{first} and this id differ only in case and share one file name on Windows: rename one", "screen ids are distinct ignoring case"),
    Rule("UX-009", 1, E, "{why}: rename the screen", "a screen id is not a device name or the name of a build artifact"),
    Rule("UX-010", 1, E, "root and state name the root group and the state band of a screen SVG: rename the widget", "no widget is named root or state"),
    Rule("UX-011", 1, W, "shorten it: a title is at most 40 characters, any other text at most 120", "text fields are short"),
    Rule("UX-005", 3, W, "rename one of them so that an id names one thing", "no interface id equals a 5D id", modes=("full",)),
    Rule("UX-201", 3, W, "add a nav row that leads here, or mark the screen start=true", "every screen is reachable from a start screen", binding=True),
    Rule("UX-202", 3, W, "add a nav row from this page (on=back counts)", "a page that is entered can be left", binding=True),
    Rule("UX-203", 3, W, "{detail}", "a screen with data widgets has a loading or empty variant; a form has an error variant", modes=("full",)),
    Rule("UX-204", 3, W, "add a button widget with parent={parent}", "a form with inputs has a button", modes=("full",)),
    Rule("UX-205", 3, W, "say where it leads (nav row) or what it calls (api=, req=)", "a button or link leads somewhere", binding=True),
    Rule("UX-206", 3, E, _REF, "links to 5D facts resolve (actor, req, api, case, rule, machine, state)"),
    Rule("UX-207", 3, I, "{detail}", "requirements that no screen, widget or nav row cites (only when some are cited)", modes=("full",)),
):
    register(_r)


# ---------------------------------------------------------------------------------------------------------------
# The context
# ---------------------------------------------------------------------------------------------------------------


def file_of(at: str) -> str:
    """The file part of a ``file:line`` location."""
    return at.rpartition(":")[0] if ":" in at else at


class Context:
    """The expanded model plus what a check needs; the one place diagnostics are built (severity and hint come from the registry)."""

    def __init__(self, exp: "ex.UiExpansion", *, mode: str = "full", incoming: "Iterable[str] | None" = None,
                 red_drafts: Iterable[str] = (), refused: "Mapping | None" = None):
        if mode not in ("slice", "full"):
            raise ValueError("mode must be 'slice' or 'full'")
        self.exp = exp
        self.model = exp.model
        self.prov = exp.provenance
        self.five = exp.five
        self.mode = mode
        self.finals = set(exp.finals)
        self.pendings = set(exp.pendings)
        self.incoming = None if incoming is None else set(incoming)
        self.red_drafts = list(red_drafts)
        self.refused = {k: set(v) for k, v in (refused or {}).items()}
        self.out: list = []
        self.pending: list = []          # (label, value, at) forward references (slice mode)
        self.pending_bind = 0
        self.screens = {s["id"]: s for s in self.model["screens"]}
        self.widgets = {w["id"]: w for w in self.model["widgets"]}
        self.variant_ids = {v["id"] for v in self.model["variants"]} | {um.variant_id(s, vocab.DEFAULT_VARIANT) for s in self.screens}
        self._look: dict = {}

    def at(self, key: str, *fallbacks: str) -> str:
        for k in (key,) + fallbacks:
            if k in self.prov:
                return self.prov[k]
        return "model:0"

    def in_scope(self, at: str) -> bool:
        return self.incoming is None or at.startswith("model:") or file_of(at) in self.incoming

    @property
    def binding_open(self) -> bool:
        """Binding rules report in full mode, or once the navigation is declared ``@final nav``."""
        return self.mode == "full" or "nav" in self.finals

    def add(self, rule_id: str, *, path: str, at: str, got, expected: str, severity: "str | None" = None, derived: bool = False, **hint):
        """Report a finding. Returns the diagnostic, or ``None`` when it was filtered (rule off in this mode, outside the incoming
        slices) or deferred (a binding rule in slice mode before ``@final nav``: counted in ``pending_bind``)."""
        r = rule(rule_id)
        if self.mode not in r.modes or not self.in_scope(at):
            return None
        if r.binding and not self.binding_open:
            self.pending_bind += 1
            return None
        d = dg.Diag(rule_id, severity or r.severity, path, at, got, expected, hint_of(rule_id, **hint), bool(derived))
        self.out.append(d)
        return d

    # -- references -------------------------------------------------------------------------------------------

    def lookalike(self, kind: str, pool: set) -> dg.LookAlike:
        key = (kind, id(pool))
        if key not in self._look:
            self._look[key] = dg.LookAlike(pool)
        return self._look[key]

    def is_refused(self, kind: str, value: str) -> bool:
        """True when the row that would define ``value`` was refused by the front-end (its dangling references are consequences).

        The id is compared exactly, and for a screen also by one edit: ``SCR.HOME`` (refused: the dot) is the id that ``SCR-HOME`` was
        meant to be. Any other near miss of a kept id is a real typo and is not suppressed.
        """
        group = self.refused.get(kind, set())
        if value in group:
            return True
        return kind == "screen" and len(value) >= dg.MIN_LOOKALIKE_LENGTH and any(
            len(g) >= dg.MIN_LOOKALIKE_LENGTH and dg.edit_within_one(value, g) for g in group)

    def ref(self, kind: str, value, pool: set, *, rule_id: str, path: str, at: str, label: "str | None" = None, shown=None,
            advice: "str | None" = None) -> bool:
        """Check one reference against ``pool``. Returns ``True`` when it resolves.

        Unresolved, slice mode: declared with ``@pending`` or no look-alike -> pending (INFO, grouped by kind); exactly one defined id
        one edit away -> ``WARN probable typo, one edit from: <id>``. Unresolved, full mode: ERROR with the nearest id as the hint.
        A reference to an id whose row was refused is a consequence: marked ``derived`` (counted, not printed).
        """
        if not isinstance(value, str) or value in pool:
            return True
        label = label or kind
        got = value if shown is None else shown
        if self.is_refused(kind, value):
            self.add(rule_id, path=path, at=at, got=got, expected=f"a defined {label}", derived=True,
                     detail="the row that defines it was refused; fix that first")
            return False
        look = self.lookalike(kind, pool)
        if self.mode == "slice":
            if value in self.pendings or (shown is not None and shown in self.pendings):
                return False
            found = look.find(value)
            if found:
                self.add(rule_id, path=path, at=at, got=got, expected=f"a defined {label}", severity=dg.WARN,
                         detail=f"probable typo, one edit from: {found}")
            elif self.in_scope(at):
                self.pending.append((label, got, at))
            return False
        if advice:
            detail = advice
        else:
            near = look.find(value) or look.nearest(value)
            if near:
                detail = f"probable typo, nearest: {near}"
            elif len(pool) <= 6:
                detail = "define it first, or use one of: " + (", ".join(sorted(pool)) or "(none is defined yet)")
            else:
                detail = f"define it first, or fix the spelling ({len(pool)} {label} ids are defined)"
        self.add(rule_id, path=path, at=at, got=got, expected=f"a defined {label}", detail=detail)
        return False


def pending_summary(ctx: Context) -> dict:
    """``{label: sorted unique ids}`` of the forward references of a slice-mode check."""
    out: dict = {}
    for label, value, _at in ctx.pending:
        out.setdefault(label, set()).add(str(value))
    return {k: sorted(v) for k, v in sorted(out.items())}


def run(ctx: Context, wave_set=(1, 3)) -> None:
    """Run every check function that reports a rule of the given wave numbers, in registration order."""
    for _order, ids, fn in CHECKS:
        if any(i in REGISTRY and REGISTRY[i].wave in wave_set for i in ids):
            fn(ctx)


# ---------------------------------------------------------------------------------------------------------------
# Wave 1: structure
# ---------------------------------------------------------------------------------------------------------------


@check(("ST-009", "SZ-001"))
def slice_state(ctx: Context) -> None:
    for name in ctx.red_drafts:
        ctx.add("ST-009", path="red", at=f"{name}:0", got=name, expected="no red draft", slice=name)
    for name, size in ctx.exp.slices:
        if SOFT_SLICE_BYTES < size <= rw.MAX_SLICE_BYTES:  # past 64 KiB the file is refused whole (ST-008)
            ctx.add("SZ-001", path="file", at=f"{name}:0", got=f"{size} bytes", expected=f"at most {SOFT_SLICE_BYTES} bytes", size=size)


@check(("UX-001",))
def references(ctx: Context) -> None:
    screens, widgets = set(ctx.screens), set(ctx.widgets)
    for w in ctx.model["widgets"]:
        key = f"widgets[{w['id']}]"
        at = ctx.at(key)
        if not ctx.ref("screen", w["screenId"], screens, rule_id="UX-001", path=key + ".screen", at=at):
            continue
        if w["parentId"] is not None:
            ctx.ref("widget", w["parentId"], widgets, rule_id="UX-001", path=key + ".parent", at=at, shown=w["parentId"].split(".", 1)[1],
                    label="widget of this screen", advice="a parent is a container widget of the same screen: define it first or fix parent=")
        for name in w["inVariants"] or ():
            ctx.ref("variant", um.variant_id(w["screenId"], name), ctx.variant_ids, rule_id="UX-001", path=key + ".in", at=at, shown=name,
                    label="declared variant", advice=f"add a variants row for {w['screenId']} | {name}, or fix in=")
    for v in ctx.model["variants"]:
        key = f"variants[{v['id']}]"
        ctx.ref("screen", v["screenId"], screens, rule_id="UX-001", path=key + ".screen", at=ctx.at(key))
    for n in ctx.model["nav"]:
        key = f"nav[{n['id']}]"
        at = ctx.at(key)
        if ctx.ref("screen", n["fromScreenId"], screens, rule_id="UX-001", path=key + ".from", at=at, shown=n["fromId"]) and "." in n["fromId"]:
            ctx.ref("widget", n["fromId"], widgets, rule_id="UX-001", path=key + ".from", at=at, label="widget")
        if ctx.ref("screen", n["toScreenId"], screens, rule_id="UX-001", path=key + ".to", at=at, shown=n["toId"]) and "#" in n["toId"]:
            ctx.ref("variant", n["toId"], ctx.variant_ids, rule_id="UX-001", path=key + ".to", at=at, label="declared variant",
                    advice=f"add a variants row for {n['toScreenId']} | {n['toId'].split('#', 1)[1]}, or fix the to cell")


@check(("UX-002", "UX-003"))
def tree_and_taps(ctx: Context) -> None:
    widgets = ctx.widgets
    for w in ctx.model["widgets"]:
        key = f"widgets[{w['id']}]"
        parent = widgets.get(w["parentId"]) if w["parentId"] else None
        if parent is not None and parent["kind"] not in vocab.CONTAINERS:
            ctx.add("UX-002", path=key + ".parent", at=ctx.at(key), got=parent["kind"], expected="a container kind as parent",
                    detail=f"{parent['local']} is a {parent['kind']}: use stack, row, grid, card, form, header, footer, sidebar, toolbar, tabs or list")
        if w["kind"] == "tab" and (parent is None or parent["kind"] != "tabs") and (w["parentId"] is None or parent is not None):
            ctx.add("UX-002", path=key + ".parent", at=ctx.at(key), got="tab", expected="a tab inside a tabs group", detail="set parent= to a tabs widget")
    reported: set = set()
    position = {w["id"]: i for i, w in enumerate(ctx.model["widgets"])}
    for w in ctx.model["widgets"]:  # a cycle is reported once, on its first widget
        seen, chain, cur = set(), [], w
        while cur is not None and cur["id"] not in seen:
            seen.add(cur["id"])
            chain.append(cur["id"])
            cur = widgets.get(cur["parentId"]) if cur["parentId"] else None
        if cur is not None:
            cycle = chain[chain.index(cur["id"]):]
            first = min(cycle, key=position.__getitem__)
            if first not in reported and not reported.intersection(cycle):
                reported.add(first)
                ctx.add("UX-002", path=f"widgets[{first}].parent", at=ctx.at(f"widgets[{first}]"), got="cycle", expected="a parent chain without a loop",
                        detail="break the parent= loop")
    for n in ctx.model["nav"]:
        if "." in n["fromId"] and n["fromId"] in widgets and widgets[n["fromId"]]["kind"] not in vocab.NAV_SOURCES:
            key = f"nav[{n['id']}]"
            ctx.add("UX-003", path=key + ".from", at=ctx.at(key), got=widgets[n["fromId"]]["kind"], expected="a widget that can be tapped")


@check(("UX-006", "UX-007", "UX-008", "UX-009", "UX-010", "UX-011"))
def screens_and_text(ctx: Context) -> None:
    routes: dict = {}
    folded: dict = {}
    for s in ctx.model["screens"]:
        key = f"screens[{s['id']}]"
        at = ctx.at(key)
        if s.get("route"):
            if s["route"] in routes:
                ctx.add("UX-006", path=key + ".route", at=at, got=s["route"], expected="a unique route", first=routes[s["route"]])
            else:
                routes[s["route"]] = s["id"]
        low = s["id"].casefold()
        if low in folded:
            ctx.add("UX-008", path=key + ".id", at=at, got=s["id"], expected="an id that is distinct ignoring case", first=folded[low])
        else:
            folded[low] = s["id"]
        if low in vocab.DEVICE_NAMES:
            ctx.add("UX-009", path=key + ".id", at=at, got=s["id"], expected="not a Windows device name", why=f"{s['id']} is a Windows device name")
        elif low in vocab.ARTIFACT_NAMES:
            ctx.add("UX-009", path=key + ".id", at=at, got=s["id"], expected="not the name of a build artifact",
                    why=f"{s['id']} is the name of a file the build writes (INDEX, NAV, START, CARD)")
        if len(s["title"]) > TITLE_LIMIT:
            ctx.add("UX-011", path=key + ".title", at=at, got=f"{len(s['title'])} characters", expected=f"at most {TITLE_LIMIT} characters")
        if len(s["purpose"]) > TEXT_LIMIT:
            ctx.add("UX-011", path=key + ".purpose", at=at, got=f"{len(s['purpose'])} characters", expected=f"at most {TEXT_LIMIT} characters")
    counts: dict = {}
    for w in ctx.model["widgets"]:
        counts[w["screenId"]] = counts.get(w["screenId"], 0) + 1
        key = f"widgets[{w['id']}]"
        at = ctx.at(key)
        if w["local"] in vocab.RESERVED_LOCALS:
            ctx.add("UX-010", path=key + ".local", at=at, got=w["local"], expected="a local name other than root and state")
        if len(w["label"]) > TEXT_LIMIT:
            ctx.add("UX-011", path=key + ".label", at=at, got=f"{len(w['label'])} characters", expected=f"at most {TEXT_LIMIT} characters")
    for sid, n in counts.items():
        if n > MAX_WIDGETS_PER_SCREEN and sid in ctx.screens:
            ctx.add("UX-007", path=f"screens[{sid}]", at=ctx.at(f"screens[{sid}]"), got=n, expected=f"at most {MAX_WIDGETS_PER_SCREEN} widgets")
    for v in ctx.model["variants"]:
        key = f"variants[{v['id']}]"
        for field_name in ("note", "text"):
            if len(v.get(field_name, "")) > TEXT_LIMIT:
                ctx.add("UX-011", path=f"{key}.{field_name}", at=ctx.at(key), got=f"{len(v[field_name])} characters", expected=f"at most {TEXT_LIMIT} characters")
    for n in ctx.model["nav"]:
        if len(n["label"]) > TEXT_LIMIT:
            key = f"nav[{n['id']}]"
            ctx.add("UX-011", path=key + ".label", at=ctx.at(key), got=f"{len(n['label'])} characters", expected=f"at most {TEXT_LIMIT} characters")


# ---------------------------------------------------------------------------------------------------------------
# Wave 3: the whole prototype and the links
# ---------------------------------------------------------------------------------------------------------------


@check(("UX-201", "UX-202"))
def graph(ctx: Context) -> None:
    screens = ctx.model["screens"]
    if not screens:
        return
    starts = [s["id"] for s in screens if s["start"]]
    if not starts:
        ctx.add("UX-201", path="screens", at="model:0", got="no start screen", expected="one screen with start=true", severity=dg.INFO)
    else:
        out_of: dict = {}
        for n in ctx.model["nav"]:
            out_of.setdefault(n["fromScreenId"], set()).add(n["toScreenId"])
        reach, todo = set(starts), list(starts)
        while todo:
            cur = todo.pop()
            for nxt in sorted(out_of.get(cur, ())):
                if nxt not in reach:
                    reach.add(nxt)
                    todo.append(nxt)
        for s in screens:
            if s["id"] not in reach:
                ctx.add("UX-201", path=f"screens[{s['id']}]", at=ctx.at(f"screens[{s['id']}]"), got="unreachable", expected="reachable from a start screen")
    for s in screens:
        if s["kind"] == "page" and s["navInIds"] and not s["navOutIds"]:
            ctx.add("UX-202", path=f"screens[{s['id']}]", at=ctx.at(f"screens[{s['id']}]"), got="dead end", expected="at least one way out")


@check(("UX-203", "UX-204", "UX-205"))
def completeness(ctx: Context) -> None:
    kinds: dict = {}
    for w in ctx.model["widgets"]:
        kinds.setdefault(w["screenId"], set()).add(w["kind"])
    have: dict = {}
    for v in ctx.model["variants"]:
        have.setdefault(v["screenId"], set()).add(v["kind"])
    for s in ctx.model["screens"]:
        ks, vs = kinds.get(s["id"], set()), have.get(s["id"], set())
        key = f"screens[{s['id']}]"
        if ks & vocab.DATA_KINDS and not ({"loading", "empty"} & vs):
            ctx.add("UX-203", path=key, at=ctx.at(key), got="no loading or empty variant", expected="a loading or empty variant for data widgets",
                    detail=f"add variants rows for {s['id']} with kind=loading and kind=empty")
        if "form" in ks and "error" not in vs:
            ctx.add("UX-203", path=key, at=ctx.at(key), got="no error variant", expected="an error variant for a form",
                    detail=f"add a variants row for {s['id']} with kind=error")
    nav_from = {n["fromId"] for n in ctx.model["nav"]}
    for w in ctx.model["widgets"]:
        key = f"widgets[{w['id']}]"
        if w["kind"] == "form":
            below, stack, seen = [], list(w["childIds"]), {w["id"]}
            while stack:
                cur_id = stack.pop()
                cur = ctx.widgets.get(cur_id)
                if cur is not None and cur_id not in seen:  # a loop in the parent chain (UX-002) must not loop here
                    seen.add(cur_id)
                    below.append(cur)
                    stack.extend(cur["childIds"])
            if any(d["kind"] in vocab.INTERACTIVE - {"button", "link"} for d in below) and not any(d["kind"] == "button" for d in below):
                ctx.add("UX-204", path=key, at=ctx.at(key), got="no button", expected="a button inside a form that has inputs", parent=w["local"])
        if w["kind"] in ("button", "link") and w["id"] not in nav_from and "apiId" not in w and not w["reqIds"]:
            ctx.add("UX-205", path=key, at=ctx.at(key), got="dead control", expected="a nav row from it, or api=, or req=")


def _links(model: Mapping) -> list:
    """Every link to a 5D fact as ``(kind, value, key, option, extra)``; ``key`` is the provenance key of the holder."""
    out = []
    for s in model["screens"]:
        key = f"screens[{s['id']}]"
        if "actorId" in s:
            out.append(("actor", s["actorId"], key, "actor", None))
        out.extend(("req", q, key, "req", None) for q in s["reqIds"])
    for w in model["widgets"]:
        key = f"widgets[{w['id']}]"
        if "apiId" in w:
            out.append(("api", w["apiId"], key, "api", None))
        out.extend(("req", q, key, "req", None) for q in w["reqIds"])
    for n in model["nav"]:
        key = f"nav[{n['id']}]"
        if "apiId" in n:
            out.append(("api", n["apiId"], key, "api", None))
        if "caseId" in n:
            out.append(("case", n["caseId"], key, "case", None))
        if "ruleId" in n:
            out.append(("rule", n["ruleId"], key, "rule", n.get("caseId")))
        out.extend(("req", q, key, "req", None) for q in n["reqIds"])
    for v in model["variants"]:
        key = f"variants[{v['id']}]"
        if "machineId" in v:
            out.append(("machine", v["machineId"], key, "machine", None))
        if "state" in v:
            out.append(("state", v["state"], key, "state", v.get("machineId")))
    return out


_LABELS = {"actor": "Actor node", "req": "requirement", "api": "node or edge", "case": "case", "machine": "machine"}


@check(("UX-206",))
def links(ctx: Context) -> None:
    items = _links(ctx.model)
    five = ctx.five
    if not items:
        return
    if five is None or five.empty:
        why = "there is no 5D model" if five is None else "the 5D model is empty"
        ctx.add("UX-206", path="links", at="model:0", got=f"{len(items)} links", expected="a 5D model to check them against", severity=dg.INFO,
                detail=f"{len(items)} links to 5D facts are not verified: {why}")
        return
    pools = {"actor": set(five.nodes), "req": five.requirements, "api": set(five.nodes) | five.edges, "case": set(five.cases),
             "machine": set(five.machines)}
    for kind, value, key, option, extra in items:
        at = ctx.at(key)
        path = f"{key}.{option}"
        if kind in pools:
            if not ctx.ref(kind if kind != "actor" else "node", value, pools[kind], rule_id="UX-206", path=path, at=at, label=_LABELS[kind]):
                continue
            if kind == "actor" and five.nodes.get(value) != "Actor":
                ctx.add("UX-206", path=path, at=at, got=value, expected="a node of kind Actor",
                        detail=f"{value} is a {five.nodes.get(value)}: link a person or system that acts, or drop actor=")
        elif kind == "rule":
            if extra is None:
                ctx.add("UX-206", path=path, at=at, got=value, expected="rule= together with case=", detail="a rule names a step of a case: add case=<case id>")
            elif extra in five.cases and value not in five.cases[extra]:
                ctx.add("UX-206", path=path, at=at, got=value, expected=f"a ruleId of case {extra}", detail=f"{extra} has no event with ruleId {value}")
        elif kind == "state":
            if extra is None:
                ctx.add("UX-206", path=path, at=at, got=value, expected="state= together with machine=", detail="a state belongs to a machine: add machine=<id>")
            elif extra in five.machines and value not in five.machines[extra]:
                ctx.add("UX-206", path=path, at=at, got=value, expected=f"a state of machine {extra}", detail=f"{extra} has no state {value}")


@check(("UX-005", "UX-207"))
def coverage(ctx: Context) -> None:
    five = ctx.five
    if five is None or five.empty:
        return
    ui_ids = [(s["id"], f"screens[{s['id']}]") for s in ctx.model["screens"]] + [(w["id"], f"widgets[{w['id']}]") for w in ctx.model["widgets"]] \
        + [(n["id"], f"nav[{n['id']}]") for n in ctx.model["nav"]]
    clash = five.ids
    for ident, key in ui_ids:
        if ident in clash:
            ctx.add("UX-005", path=key, at=ctx.at(key), got=ident, expected="an interface id that differs from every 5D id")
    cited = {q for s in ctx.model["screens"] for q in s["reqIds"]} | {q for w in ctx.model["widgets"] for q in w["reqIds"]} \
        | {q for n in ctx.model["nav"] for q in n["reqIds"]}
    if cited:
        missing = sorted(five.requirements - cited)
        if missing:
            shown = ", ".join(missing[:3]) + (f" +{len(missing) - 3}" if len(missing) > 3 else "")
            ctx.add("UX-207", path="requirements", at="model:0", got=f"{len(missing)} not cited", expected="every requirement cited by some screen",
                    detail=f"not cited by any screen, widget or nav row: {shown}")


# ---------------------------------------------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class UiCheckResult:
    """What a check found. ``diagnostics`` holds everything (derived ones flagged); renderers list only the rest."""

    diagnostics: list
    mode: str
    pending: dict = field(default_factory=dict)       # label -> sorted unique ids of unresolved references (slice mode)
    pending_refs: int = 0
    pending_bind: int = 0                              # binding findings deferred until ``@final nav`` or full mode
    expansion: "ex.UiExpansion | None" = None
    seconds: float = 0.0
    wave_set: tuple = (1, 3)

    @property
    def model(self) -> "dict | None":
        return None if self.expansion is None else self.expansion.model

    @property
    def derived_count(self) -> int:
        return sum(d.derived for d in self.diagnostics)

    @property
    def shown(self) -> list:
        return dg.visible(self.diagnostics)

    @property
    def errors(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.ERROR and not d.derived]

    @property
    def warnings(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.WARN and not d.derived]

    @property
    def infos(self) -> list:
        return [d for d in self.diagnostics if d.severity == dg.INFO and not d.derived]

    @property
    def ok(self) -> bool:
        """No ERROR: a ``put`` commits."""
        return not self.errors

    def render(self, fmt: str = "text", offset: int = 0, budget: "int | None" = None) -> str:
        return dg.render(self.diagnostics, fmt=fmt, offset=offset, budget=budget, pending=self.pending, pending_bind=self.pending_bind,
                         derived=self.derived_count)

    def rule_ids(self, *, derived: bool = False) -> set:
        """Rule ids of the findings (derived ones only on request)."""
        return {d.rule for d in self.diagnostics if derived or not d.derived}


def check_sources(sources: "Mapping[str, str | bytes] | Sequence", meta: "Mapping | None" = None, *, five: "Mapping | ex.FiveIndex | None" = None,
                  mode: str = "slice", incoming: "Iterable[str] | None" = None, red_drafts: Iterable[str] = (), wave_set=(1, 3)) -> UiCheckResult:
    """Parse, expand and check interface slices. ``sources`` is a mapping ``name -> text`` (processed in file-name order) or a sequence
    of ``(name, text)`` pairs (given order). In slice mode the committed rest comes first and ``incoming`` names the slices being put."""
    if isinstance(sources, Mapping):
        pairs = sorted(sources.items())
    else:
        pairs = list(sources)
    started = time.perf_counter()
    names = None if incoming is None else set(incoming)
    texts: dict = {}
    parsed: list = []
    for name, data in pairs:
        if isinstance(data, ParsedSlice):
            parsed.append(data)
            continue
        text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
        texts[name] = text
        parsed.append(atl.parse_atl(text, name))
    if mode == "slice" and names is not None:
        parsed = [s for s in parsed if s.name not in names] + [s for s in parsed if s.name in names]
    return check_slices(parsed, meta, five=five, mode=mode, incoming=names, red_drafts=red_drafts, wave_set=wave_set, texts=texts, started=started)


def check_slices(parsed: Sequence[ParsedSlice], meta: "Mapping | None" = None, *, five=None, mode: str = "slice",
                 incoming: "Iterable[str] | None" = None, red_drafts: Iterable[str] = (), wave_set=(1, 3), texts: "Mapping[str, str] | None" = None,
                 started: "float | None" = None) -> UiCheckResult:
    """``check_sources`` for slices that are already parsed; ``texts`` (``name -> text``) lets a refused row's id be recovered."""
    started = time.perf_counter() if started is None else started
    expansion = ex.expand_ui(parsed, five, meta)
    return check_expansion(expansion, mode=mode, incoming=incoming, red_drafts=red_drafts, wave_set=wave_set, texts=texts, started=started)


def check_expansion(expansion: "ex.UiExpansion", *, mode: str = "slice", incoming: "Iterable[str] | None" = None, red_drafts: Iterable[str] = (),
                    wave_set=(1, 3), texts: "Mapping[str, str] | None" = None, started: "float | None" = None) -> UiCheckResult:
    started = time.perf_counter() if started is None else started
    names = None if incoming is None else set(incoming)
    refused = ex.refused_ids(texts or {}, expansion.diagnostics)
    ctx = Context(expansion, mode=mode, incoming=names, red_drafts=red_drafts, refused=refused)
    run(ctx, wave_set)
    keep = [d for d in expansion.diagnostics if ctx.in_scope(d.at)]
    return UiCheckResult(_dedupe(keep + ctx.out), mode, pending_summary(ctx), len(ctx.pending), ctx.pending_bind, expansion,
                         time.perf_counter() - started, tuple(wave_set))


def _dedupe(diags: Iterable) -> list:
    seen, out = set(), []
    for d in sorted(diags, key=dg.Diag.sort_key):
        key = (d.rule, d.severity, d.path, d.at, repr(d.got), d.derived)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out
