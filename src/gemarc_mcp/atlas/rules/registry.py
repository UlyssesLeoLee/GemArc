"""The rule registry: ``{id, wave, severity, hint template}`` per AI-facing rule, and the phase runner.

Every rule of design 5.1 is one ``Rule`` record. A **check function** (``@check(phase, rules=...)``) walks the context and
reports through ``Context.add`` / ``Context.ref``; the context fills in severity, hint, ``derived`` (cascade suppression)
and the slice-mode deferral of binding rules. The wave 2 and wave 3 rules register their entries and checks in the same registry:

    registry.register(Rule("CS-001", 2, "ERROR", "add edge {a} | <rel> | {b}", "...", cascade=("node",), binding=True))

    @registry.check("CS", rules=("CS-001",))
    def bind_events(ctx): ...

Phases run root-cause first: ``ST, SZ, DV, RF, EV, SM, CS, RQ, PT, IV, OR, DG, RC, DC, LB`` (the order of ``PHASE_ORDER``);
a later check sees which entities an earlier phase tainted. Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .. import diag as dg

#: Execution order of the rule families (design 5.2: ST, DV, RF, EV, SM, CS, IV, OR, then DG/RC/DC); ids sort by it.
PHASE_ORDER = ("ST", "SZ", "DV", "RF", "EV", "SM", "CS", "RQ", "PT", "IV", "OR", "DG", "RC", "DC", "LB")

#: Hint templates are formatted with ``str.format_map``; the rendered hint is cut at ``dg.HINT_LIMIT`` characters.


@dataclass(frozen=True)
class Rule:
    """One registry entry.

    ``cascade``: the reference kinds whose failure may explain the finding (``("evidence",)`` for EV-001: an unresolved
    evidence id may have been the code evidence); while the entity is tainted for one of them the finding is counted as
    ``derived`` (not printed). ``binding``: deferred in slice mode (counted as pending-bind)
    until an ``@final edges`` declaration or full mode. ``modes``: where the rule runs. ``source``: ``format`` when the
    front-end or the expander produces it (ST/DV), ``rules`` when a check function does.
    """

    id: str
    wave: int
    severity: str
    hint: str
    summary: str
    cascade: tuple = ()
    binding: bool = False
    modes: tuple = ("slice", "full")
    source: str = "rules"

    @property
    def phase(self) -> str:
        return self.id.split("-", 1)[0]


REGISTRY: dict[str, Rule] = {}
#: ``(phase_index, order, phase, rule ids, function)`` in registration order.
CHECKS: list[tuple] = []


def register(rule: Rule) -> Rule:
    """Add a rule (re-registering the same id replaces it, so a later wave can refine an earlier stub)."""
    if rule.severity not in dg.SEVERITIES:
        raise ValueError(f"{rule.id}: bad severity {rule.severity!r}")
    if rule.wave not in (1, 2, 3):
        raise ValueError(f"{rule.id}: wave must be 1, 2 or 3")
    REGISTRY[rule.id] = rule
    return rule


def check(phase: str, rules: tuple) -> Callable:
    """Decorator: register a check function that reports the given rule ids during ``phase``."""
    def wrap(fn: Callable) -> Callable:
        index = PHASE_ORDER.index(phase) if phase in PHASE_ORDER else len(PHASE_ORDER)
        CHECKS[:] = [c for c in CHECKS if c[4].__qualname__ != fn.__qualname__ or c[4].__module__ != fn.__module__]
        CHECKS.append((index, len(CHECKS), phase, tuple(rules), fn))
        CHECKS.sort(key=lambda c: (c[0], c[1]))
        return fn
    return wrap


def rule(rule_id: str) -> Rule:
    try:
        return REGISTRY[rule_id]
    except KeyError:
        raise KeyError(f"unknown rule id {rule_id!r}") from None


def rules(wave_set=None) -> list:
    """Registered rules (optionally of the given wave_set) in phase and id order."""
    out = [r for r in REGISTRY.values() if wave_set is None or r.wave in wave_set]
    return sorted(out, key=lambda r: (PHASE_ORDER.index(r.phase) if r.phase in PHASE_ORDER else len(PHASE_ORDER), r.id))


def wave_ids(wave: int) -> list:
    return [r.id for r in rules((wave,))]


def checks_for(wave_set) -> list:
    """The check functions that report at least one rule of the given wave numbers, in phase order."""
    return [c for c in CHECKS if any(rule_id in REGISTRY and REGISTRY[rule_id].wave in wave_set for rule_id in c[3])]


def run(ctx, wave_set=(1,)) -> None:
    """Run every check of the given wave numbers over ``ctx`` in phase order (root cause first)."""
    for _, _, phase, _, fn in checks_for(wave_set):
        ctx.phase = phase
        fn(ctx)
    ctx.phase = ""


def hint_of(rule_id: str, **values) -> str:
    """The hint of ``rule_id`` with the placeholders filled (an unfilled placeholder stays visible as ``{name}``)."""
    return REGISTRY[rule_id].hint.format_map(_Keep(values))


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# ---------------------------------------------------------------------------------------------------------------
# Wave 1 (design 5.1): 31 rules
# ---------------------------------------------------------------------------------------------------------------

E, W, I = dg.ERROR, dg.WARN, dg.INFO

_STRUCTURAL = (
    ("ST-001", "a required or non-empty cell, option or header line is missing", "write the missing cell or option; positional cells are never empty"),
    ("ST-002", "an id is defined twice (both places are named)", "already defined at {first}; rename one of them"),
    ("ST-003", "a value is outside its enum, id grammar or numeric range", "use one of the allowed values (ids and states are case-sensitive)"),
    ("ST-004", "unknown section, column, option or key", "use one of the allowed names"),
    ("ST-005", "wrong number of cells in a row (the header is echoed)", "count the cells against the header; a '|' inside text is written \\|"),
    ("ST-006", "an option token sits inside a text cell", "is a '|' missing before the options? quote the cell to keep the text"),
    ("ST-007", "option syntax: token, quoting, JSON, number or boolean is malformed", "fix the option syntax: key=value, \"quoted\", {json}"),
    ("ST-008", "file size or encoding", "slices are UTF-8 .atl or .jsonl text of at most 64 KiB"),
    ("DV-001", "a derived field was authored", "derived; delete it"),
)
for _id, _summary, _hint in _STRUCTURAL:
    register(Rule(_id, 1, E, _hint, _summary, source="format"))

register(Rule("ST-009", 1, E, "red draft {slice}: fix it and put again (or replace it); check full and build refuse until none is left",
              "a red draft is present", modes=("full",)))
register(Rule("SZ-001", 1, W, "{size} bytes: split the slice by section, slices of at most 8 KiB are cheapest to repair",
              "slice larger than 8 KiB"))

_REF = "{detail}"
register(Rule("RF-001", 1, E, _REF, "spatial chain (floor -> building, room -> floor, node -> room) and unique floor level"))
register(Rule("RF-002", 1, E, _REF, "edge endpoints and machine node rows"))
register(Rule("RF-003", 1, E, _REF, "evidence references (nodes, edges, cases, machines, TBDs, requirements)"))
register(Rule("RF-004", 1, E, _REF, "case, event, expectation and initial-state references"))
register(Rule("RF-005", 1, E, _REF, "requirement, test, TBD, policy and intervention references"))

register(Rule("EV-001", 1, E, "downgrade to Designed, or add a source-code evidence", "Implemented needs source-code evidence (nodes and edges)",
              cascade=("evidence",)))
register(Rule("EV-002", 1, W, "has code evidence but claims Designed: remove verif=Designed to derive Implemented, or keep it on purpose",
              "code evidence but authored as Designed", cascade=("evidence",)))
register(Rule("EV-006", 1, E, "cite the key name or the surrounding code, never the secret value", "credential in an anchor, topic or props"))
register(Rule("EV-007", 1, E, "declare {repo} in the project's repos (init), or fix repo=", "repository alias is not declared"))
register(Rule("EV-008", 1, I, "an unused evidence proves nothing: cite it with ev=... or delete it", "unused evidence"))
register(Rule("EV-009", 1, W, "add ev=... or write verif=Hypothetical on purpose", "node without evidence and no stated verif=Hypothetical"))
register(Rule("EV-010", 1, E, "to-be ceiling is Designed: write verif=Designed or leave verif out", "to-be mode: Implemented or Observed is illegal"))
register(Rule("EV-011", 1, I, "{count} edges inherit endpoint evidence (ev=^), capped at Designed; give them own evidence to claim more",
              "inherited edge evidence count"))

register(Rule("SM-001", 1, E, _REF, "machine integrity"))
register(Rule("SM-002", 1, E, "add a transition into the state, or remove it", "every state is reachable from the initial state"))
register(Rule("SM-003", 1, E, "add owner=<node> on the machine row, or an owns edge from a non-machine node", "the machine has one valid owner",
              cascade=("node",), binding=True))
register(Rule("SM-005", 1, I, "no state is terminal: fine for a cycle, otherwise leave the last state without outgoing transitions",
              "a machine without a terminal state"))

register(Rule("CS-004", 1, W, "add req=<requirement id> so the case traces a requirement", "case traces a requirement"))
register(Rule("RQ-001", 1, E, "add nodes=<ids> to the requirement so it is traced to the design", "requirement is trace-complete",
              modes=("full",)))
register(Rule("RQ-002", 1, I, _REF, "requirement and node coverage", modes=("full",)))


# ---------------------------------------------------------------------------------------------------------------
# Wave 2 (design 5.1): the rules that need the engine or the oracle ledger. The OR-, IV- and PT- rows live next to their
# checks (``or_.py``, ``iv.py``, ``pt.py``); the case, machine and evidence rows are registered here.
# ---------------------------------------------------------------------------------------------------------------

register(Rule("CS-001", 2, E, "add edge {a} | <rel> | {b} (a runtime relation), or set edge=<id> on the event",
              "every event is bound to a runtime edge (Unbound is an error)", cascade=("node", "edge"), binding=True))
register(Rule("CS-005", 2, W, "{detail}", "category cover and renamed copies"))
register(Rule("CS-008", 2, I, "give the events that expectations or delays name a ruleId", "events without a ruleId"))
register(Rule("SM-004", 2, W, "merge the transitions or give them different targets", "no two transitions share the same from -> to pair"))
register(Rule("SM-006", 2, W, "{detail}", "guard sweep: every guard can be satisfied and rejected, and the engine agrees with the table oracle",
              modes=("full",)))
register(Rule("SM-007", 2, W, "state {state} cannot reach a terminal state: add a closing transition or declare it terminal",
              "every non-terminal state can reach a terminal state (machines that declare terminals)"))

# Wave 3 rows whose findings come from the evidence verifier (``evidence.py``); the id and meaning of each come from its table.
_EV_ROWS = {
    "EV-003": (E, "anchor in range: the file, the anchor text and the cited range must agree"),
    "EV-004": (I, "revision pin: the source root moved since the anchors were stamped"),
    "EV-005": (W, "fingerprint stamp: the stamped block around the anchor changed"),
    "EV-012": (W, "a user: anchor quotes text that is not in the saved requirement text"),
}
for _id, (_sev, _summary) in _EV_ROWS.items():
    register(Rule(_id, 3, _sev, "{detail}", _summary, modes=("full",), source="evidence"))


# Wave 3 build-time rows (M4). The findings come from ``build.vocab_findings`` while the page is built (they need the profile and
# the card geometry, not the slices), so they run in no slice or full check: ``modes`` is ``("build",)``.
register(Rule("RC-001", 3, W, "give each floor of a building its own level: two floors share level {level}, or the level is not a usable number",
              "floor levels are unique numbers within a building", modes=("build",), source="build"))
register(Rule("RC-002", 3, W, "use a kind or trust value of the profile: {value} is not known and is drawn with a neutral default",
              "node kinds and trust values are known to the profile", modes=("build",), source="build"))
register(Rule("LB-001", 3, I, "shorten the name of {id} (or accept the ellipsis): it does not fit its card label",
              "node names fit the 2D card label", modes=("build",), source="build"))


def _selfcheck() -> None:  # the registry is data, keep it honest at import time
    for r in REGISTRY.values():
        if len(r.hint) > dg.HINT_LIMIT:
            raise AssertionError(f"{r.id}: hint template is too long")


_selfcheck()
