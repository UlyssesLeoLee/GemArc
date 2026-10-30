"""Where is the project, and what should the AI do next? (design 5.4 and addendum A1 D14: the completion contract).

``load_snapshot`` reads the committed slices once and runs the full-mode check (closed world, every red draft is ST-009);
``evaluate`` turns the snapshot into a ``State``:

``done``      no red draft, no BLOCK slot in the completeness report, ``check full`` without ERROR, the required dimensions ready
              for the mode, every applicable gate green, and a review record for the current model (``.atlas/review.json``).
``phase``     ``collect`` (nothing authored yet, or to-be without requirement text), ``repair`` (red draft or ERROR), ``author``
              (a BLOCK slot or a dimension is open), ``needs-user`` (only decisions the user can make remain), ``build``
              (an applicable gate is red), ``review`` (everything is green but the one self-review), ``done``.
``next``      at most 10 lines in the 5.4 priority order: red drafts, other errors, (to-be: the elicitation step), BLOCK slots,
              pending references by number of referrers, requirement gaps, cases without expectation, unreachable machine
              states, coverage; the last line is the dimension readiness.
``blockers``  what keeps ``done`` false, as short tokens. ``needsUser`` decisions that only the user can make.

Pluggable conditions (**not applicable yet**, they default to passing; later lanes call ``register_gate``): ``oracle`` (every
expectation sealed), ``build`` (the build wrote its outputs), ``browser`` (the browser gate is green). A registered gate is
``fn(snapshot) -> {"applicable": bool, "ok": bool, "detail": str}``. Dimensions 4D and 5D become *required* only when the
oracle gate is applicable; until then they are reported but never block.

Records read here (written by other lanes): ``.atlas/oracle.json`` (provisional shape in ``oracle_summary``) and
``.atlas/review.json`` ``{"modelSha": <8 or 64 hex of the model>, "findings": [...], "fixed": [...], "notes": "..."}``.
Standard library only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from . import diag as dg
from . import expand as ex
from . import model as am
from . import slicecheck
from .project import Config, Paths
from .rules.completeness import Report, completeness_report
from .slices import SliceFile, list_red, list_slices, read_bytes

MAX_NEXT = 10
LINE_BYTES = 118
GATE_NAMES = ("oracle", "build", "browser")
DIMENSIONS = ("1D", "2D", "3D", "4D", "5D")
ALWAYS_REQUIRED = ("1D", "2D", "3D")
USER_TEXT_GLOB = "*.txt"
REVIEW_MAX_BYTES = 1024 * 1024


def element_templates():
    """The engine lane's element templates for ``use=<kind>`` (``None`` when that module is not available)."""
    try:
        from .engine import elements
    except ImportError:  # pragma: no cover - the engine lane ships in the same package
        return None
    return elements.template_for


def _review_accepts(snap: "Snapshot", record: "Mapping | None") -> bool:
    """A review record made for an earlier model version still counts when no new BLOCK finding appeared (the one-review rule)."""
    try:
        from . import review
    except ImportError:  # pragma: no cover - the review lane ships in the same package
        return False
    return review.accepts_changed_model(snap, record)


# ---------------------------------------------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Snapshot:
    paths: Paths
    config: Config
    files: list                      # committed SliceFile
    red: list                        # red-draft SliceFile
    parsed: list                     # ParsedSlice of the committed files
    check: "slicecheck.CheckResult"  # full mode, red drafts reported as ST-009
    model: dict
    prov: dict

    @property
    def mode(self) -> str:
        return (self.model.get("meta") or {}).get("mode") or self.config.mode

    @property
    def sha8(self) -> str:
        return am.model_sha8(self.model)


def load_snapshot(paths: Paths, config: Config, *, mode: str = "full", only: "str | None" = None) -> Snapshot:
    """Read the committed slices and check them. Reads only: nothing is written, no lock is taken (writes are atomic).

    ``mode="slice"`` with ``only=<file name>`` checks one slice against the committed rest (``check slice=...``).
    """
    files = list_slices(paths)
    texts = {f.fname: read_bytes(f.path) for f in files}
    parsed = [ex.parse_source(f.fname, texts[f.fname]) for f in files]
    red = list_red(paths)
    decoded = {name: data.decode("utf-8", errors="replace") for name, data in texts.items()}
    result = slicecheck.check_slices(parsed, config.meta(), mode=mode, incoming=None if only is None else [only],
                                     red_drafts=[r.fname for r in red] if mode == "full" else (), element_templates=element_templates(),
                                     texts=decoded)
    expansion = result.expansion
    return Snapshot(paths, config, files, red, parsed, result, expansion.model, expansion.provenance)


# ---------------------------------------------------------------------------------------------------------------
# Records of other lanes: oracle, review
# ---------------------------------------------------------------------------------------------------------------


def _read_json(path) -> "dict | None":
    try:
        if not path.is_file() or path.stat().st_size > REVIEW_MAX_BYTES:
            return None
        data = json.loads(read_bytes(path).decode("utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_review(paths: Paths) -> "dict | None":
    """The review record ``.atlas/review.json`` (``None`` when absent or unreadable)."""
    return _read_json(paths.review)


def review_is_current(record: "Mapping | None", model: Mapping) -> bool:
    """A review record counts for the model whose sha (8 hex digits, or the full 64) it carries."""
    if not record:
        return False
    sha = record.get("modelSha")
    full = am.model_sha(model)
    return isinstance(sha, str) and len(sha) in (8, 64) and full.startswith(sha.lower())


def read_oracle(paths: Paths) -> "dict | None":
    return _read_json(paths.oracle)


def oracle_summary(data: "Mapping | None") -> dict:
    """Counts from ``.atlas/oracle.json`` (the oracle lane owns the file; this reader is defensive).

    Provisional shape: ``{"seals": {<key>: {"kind": "case"|"intervention", "state": "sealed"|"revised"|"confirmed"}}}`` (a list of
    such entries also works). ``sealed`` counts entries in state ``sealed`` or ``confirmed``.
    """
    out = {"present": data is not None, "total": 0, "sealed": 0, "revised": 0, "confirmed": 0, "cases": 0, "interventions": 0,
           "unconfirmed": 0}
    if not data:
        return out
    seals = data.get("seals", data.get("entries", {}))
    entries = list(seals.values()) if isinstance(seals, Mapping) else list(seals) if isinstance(seals, list) else []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        out["total"] += 1
        state = entry.get("state")
        if state in ("sealed", "revised", "confirmed"):
            out[state] += 1
        if state in ("sealed", "confirmed"):
            kind = entry.get("kind")
            if kind == "case":
                out["cases"] += 1
            elif kind == "intervention":
                out["interventions"] += 1
        if state != "confirmed":
            out["unconfirmed"] += 1
    return out


# ---------------------------------------------------------------------------------------------------------------
# Gates (pluggable, default: not applicable yet)
# ---------------------------------------------------------------------------------------------------------------

GATES: dict = {}


def register_gate(name: str, fn: Callable) -> None:
    """Plug a condition into ``done``: ``fn(snapshot) -> {"applicable": bool, "ok": bool, "detail": str}``."""
    if name not in GATE_NAMES:
        raise ValueError(f"unknown gate {name!r}; one of {', '.join(GATE_NAMES)}")
    GATES[name] = fn


def unregister_gate(name: str) -> None:
    GATES.pop(name, None)


def _gate(name: str, snap: Snapshot) -> dict:
    fn = GATES.get(name)
    if fn is None:
        return {"applicable": False, "ok": True, "detail": "not applicable yet"}
    try:
        out = dict(fn(snap))
    except Exception as ex_:  # noqa: BLE001 - a broken gate is a red gate, not a crash
        return {"applicable": True, "ok": False, "detail": f"gate raised {type(ex_).__name__}"}
    out.setdefault("applicable", True)
    out.setdefault("ok", False)
    out.setdefault("detail", "")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------------------------------------------


def dimensions(snap: Snapshot, report: Report, oracle: Mapping) -> dict:
    """``{dim: {"ready": bool, "why": str}}`` for 1D..5D (design 5.4)."""
    model = snap.model
    nodes, edges = model.get("nodes", []), model.get("edges", [])
    open_slots = {e.slot for e in report.entries if e.status != "DEFAULTED" and e.severity != "INFO"}
    block = [e for e in report.blockers()]
    out = {}
    out["1D"] = {"ready": not block, "why": "documents print no [NOT-AUTHORED] marker" if not block else f"{len(block)} BLOCK slot(s) in the completeness report"}
    ref_errors = [d for d in snap.check.errors if d.rule in ("RF-001", "RF-002")]
    two = bool(nodes) and bool(edges) and "edge.endpoints" not in open_slots and not ref_errors
    out["2D"] = {"ready": two, "why": "nodes and edges, every edge endpoint resolves" if two else
                 ("no node yet" if not nodes else "no edge yet" if not edges else "an edge endpoint does not resolve")}
    rooms = {r.get("id") for r in (model.get("space") or {}).get("rooms", ())}
    unplaced = [n["id"] for n in nodes if n.get("roomId") not in rooms]
    out["3D"] = {"ready": bool(nodes) and not unplaced, "why": "every node has a room" if nodes and not unplaced else
                 ("no node yet" if not nodes else f"{len(unplaced)} node(s) without a defined room")}
    out["4D"] = {"ready": oracle["cases"] >= 1, "why": f"{oracle['cases']} sealed case expectation(s)" if oracle["cases"] else "no sealed case expectation"}
    out["5D"] = {"ready": oracle["interventions"] >= 1,
                 "why": f"{oracle['interventions']} sealed intervention expectation(s)" if oracle["interventions"] else "no sealed intervention expectation"}
    return out


# ---------------------------------------------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class State:
    done: bool
    phase: str
    next: list
    blockers: list
    needs_user: list
    dims: dict = field(default_factory=dict)
    required: tuple = ALWAYS_REQUIRED
    gates: dict = field(default_factory=dict)
    report: "Report | None" = None
    review: "dict | None" = None
    review_current: bool = False
    oracle: dict = field(default_factory=dict)
    open_blocks: list = field(default_factory=list)
    counters: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {"done": self.done, "phase": self.phase, "next": self.next, "blockers": self.blockers, "needsUser": self.needs_user}
        if self.open_blocks:
            out["openBlocks"] = self.open_blocks
        if self.counters:
            out["counters"] = self.counters
        return out


def _clip(text: str, limit: int = LINE_BYTES) -> str:
    text = " ".join(str(text).split())
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore") + "..."


def _some(items, limit: int = 3) -> str:
    items = [str(x) for x in items]
    return ", ".join(items[:limit]) + (f" +{len(items) - limit}" if len(items) > limit else "")


_SLOT_ADVICE = {
    "ch9.nfr": "add a policies row kind=nfr (src=<requirement id>) so chapter 9 has text",
    "node.sub": "write the sub (one-line description) of",
    "node.room": "room= must name a defined room, for",
    "node.evidence": "an ev= id is not defined, for",
    "node.columns": "Entity/Table nodes need props={\"columns\":[...]}:",
    "edge.endpoints": "an endpoint is not a defined node:",
    "case.events": "an event endpoint is not a defined node:",
    "case.requirement": "req= names an undefined requirement:",
    "requirement.nodes": "nodes= names an undefined node:",
}

_DIM_HINT = {"1D": "documents", "2D": "structure", "3D": "rooms", "4D": "sealed cases", "5D": "sealed interventions"}


def _user_texts(paths: Paths) -> list:
    return sorted(paths.user.glob(USER_TEXT_GLOB)) if paths.user.is_dir() else []


_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u300c", "\u300d"), ("\u300e", "\u300f"))
MIN_QUOTE_CHARS = 8


def norm_text(text: str) -> str:
    """Case and white space insensitive form used to find a quotation in the saved texts."""
    return " ".join(str(text).casefold().split())


def saved_text(paths: Paths) -> str:
    """The normalised text of every ``atlas/user/*.txt`` (what the user actually said); empty when nothing is saved."""
    chunks = []
    for path in _user_texts(paths):
        try:
            chunks.append(read_bytes(path)[:REVIEW_MAX_BYTES].decode("utf-8-sig", errors="replace"))
        except OSError:
            continue
    return norm_text("\n".join(chunks))


def quote_backed(text: str, haystack: str) -> bool:
    """True when ``text`` holds a quotation (inside quotation marks, ``...`` allowed between pieces) of at least
    ``MIN_QUOTE_CHARS`` characters that occurs in the saved user text. A decision that carries no such quotation was not
    taken from anything the user said."""
    if not haystack:
        return False
    for opening, closing in _QUOTE_PAIRS:
        for m in re.finditer(re.escape(opening) + r"([^" + re.escape(closing) + r"]+)" + re.escape(closing), str(text)):
            pieces = [norm_text(x.strip()) for x in re.split(r"\u2026|\.{3}", m.group(1))]
            pieces = [x for x in pieces if x]
            if pieces and sum(len(x) for x in pieces) >= MIN_QUOTE_CHARS and all(x in haystack for x in pieces):
                return True
    return False


def decided_tbds(model: Mapping, haystack: str) -> set:
    """TBDs the user decided: a policy row ``by=user src=<tbd id>`` whose text quotes the saved user text."""
    return {p.get("src") for p in model.get("policies", ()) if p.get("by") == "user" and quote_backed(p.get("text", ""), haystack)}


def _tbd_resolved(model: Mapping, haystack: str = "") -> set:
    return decided_tbds(model, haystack)


def vanished_tbds(snap: "Snapshot") -> list:
    """TBD ids found in an archived (committed once) version of a slice but not in the current model, with no user decision
    for them: somebody deleted the open question instead of asking the user. Red drafts are not looked at (never committed).
    The history keeps the newest ten versions of a slice, so a deletion is remembered for ten more puts to that slice."""
    seen: set = set()
    for directory in (snap.paths.history,):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix not in (".atl", ".jsonl") or not path.is_file():
                continue
            try:
                data = read_bytes(path)
            except OSError:
                continue
            if b"tbds" not in data:
                continue
            for row in ex.parse_source(path.name, data).rows:
                if row.section == "tbds":
                    seen.add(row.values["id"])
    current = {t.get("id") for t in snap.model.get("tbds", ())}
    decided = {p.get("src") for p in snap.model.get("policies", ()) if p.get("by") == "user"}
    return sorted(seen - current - decided)


def _needs_user(snap: Snapshot, oracle: Mapping, oracle_applicable: bool) -> list:
    model, out = snap.model, []
    texts = saved_text(snap.paths)
    if snap.mode == "tobe" and not _user_texts(snap.paths):
        out.append("requirement facts: no requirement text is saved under atlas/user/; ask the user for it and save it verbatim (to-be cannot be invented)")
    done = _tbd_resolved(model, texts)
    open_tbds = [t for t in model.get("tbds", ()) if t.get("id") not in done]
    for t in open_tbds[:3]:
        out.append(_clip(f"decide TBD {t.get('id')}: {t.get('title')}"))
    if len(open_tbds) > 3:
        out.append(f"{len(open_tbds) - 3} more open TBDs (answered by the user: save the answer to atlas/user/<name>.txt, then a policies row by=user src=<tbd id> whose text quotes it)")
    elif open_tbds:
        out.append("after the user answers: save the answer verbatim to atlas/user/<name>.txt, then a policies row by=user src=<tbd id> whose text quotes it in \"...\"")
    gone = vanished_tbds(snap)
    if gone:
        out.append(_clip(f"TBD {_some(gone)} was deleted without a user decision: restore the row, or ask the user to void it (policy by=user src=<id>)"))
    if oracle_applicable and oracle["unconfirmed"]:
        out.append(f"oracle confirmation: {oracle['unconfirmed']} expectation(s) wait for the user's approval of the oracle sheet")
    return out


def _elicitation(snap: Snapshot) -> "tuple[int, str] | None":
    """The first unmet step of the to-be elicitation order (design 8.3), or ``None`` when all seven are met."""
    m = snap.model
    steps = [
        (bool(_user_texts(snap.paths)) or bool(m.get("requirements")), "save the requirement text verbatim to atlas/user/<name>.txt"),
        (bool(m.get("requirements")) and bool(m.get("tests")), "requirements + tests rows, one TBD per ambiguity (source = a quoted sentence)"),
        (bool((m.get("space") or {}).get("rooms")), "spatial rows: buildings, floors, rooms"),
        (bool(m.get("nodes")), "nodes with user: evidence (verif at most Designed)"),
        (bool(m.get("edges")), "edges between the nodes (end with @final edges)"),
        (bool(m.get("cases")), "cases derived from each requirement's acceptance statements"),
        (any(c.get("expectedTerminalState") or c.get("expectedOutcomes") for c in m.get("cases", ())),
         "expect rows restating the requirements (the oracle sheet is the acceptance criteria)"),
    ]
    for index, (met, text) in enumerate(steps, start=1):
        if not met:
            return index, text
    return None


def _progress_lines(model: Mapping, mode: str, red: bool = False) -> list:
    """What the project has no row of at all yet (the empty chapters of the documents), as one line per missing layer."""
    if mode == "tobe":
        return []  # the elicitation order says it
    if red:
        return []  # a red draft is patched first; the slice it holds is not "missing"
    if not (model.get("space") or {}).get("rooms"):
        return ["2 no room yet: put buildings, floors and rooms (00-space)"]
    if not model.get("nodes"):
        return ["2 no node yet: put 10-structure (about 11 nodes with their edges and evidences per slice)"]
    out = []
    if not model.get("edges"):
        out.append("2 no edge yet: write edges between the nodes, then @final edges")
    if not model.get("cases"):
        out.append("2 no case yet: put 20-scenarios (tests, requirements, cases, events, expect)")
    return out


def _requirement_gaps(model: Mapping) -> list:
    reqs, cases = model.get("requirements", []), model.get("cases", [])
    traced = {}
    for c in cases:
        for rid in c.get("requirementIds", ()):
            traced.setdefault(rid, []).append(c)
    no_node = [r["id"] for r in reqs if not r.get("nodeIds")]
    no_case = [r["id"] for r in reqs if r["id"] not in traced]
    no_expect = [r["id"] for r in reqs if r["id"] in traced
                 and not any(c.get("expectedTerminalState") or c.get("expectedOutcomes") for c in traced[r["id"]])]
    out = []
    if no_node:
        out.append(f"4 {len(no_node)} requirement(s) name no node (nodes=): {_some(no_node)}")
    if no_case:
        out.append(f"4 {len(no_case)} requirement(s) have no case (req=): {_some(no_case)}")
    if no_expect:
        out.append(f"4 {len(no_expect)} requirement(s) have no expectation in their cases: {_some(no_expect)}")
    return out


def _is_unresolved_ref(d) -> bool:
    """A reference to an id that is not defined (an ERROR in full mode, but the AI's cue to write the missing row, not to repair)."""
    return d.rule in ("RF-001", "RF-002", "RF-003", "RF-004", "RF-005") and isinstance(d.got, str) and d.expected.startswith("a defined ")


def _pending_lines(check: "slicecheck.CheckResult") -> list:
    """Unresolved references (ERROR in full mode) grouped by the missing id, most referrers first."""
    groups: dict = {}
    for d in check.errors:
        if _is_unresolved_ref(d):
            kind = d.expected[len("a defined "):].removesuffix(" id")
            groups.setdefault((kind, d.got), []).append(d)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = []
    for (kind, value), items in ranked[:3]:
        near = ""
        if "nearest" in items[0].hint or "one edit from" in items[0].hint:
            near = " (typo? " + items[0].hint.rpartition(": ")[2][:30] + ")"
        lines.append(f"3 undefined {kind} {value}: {len(items)} reference(s), first at {items[0].at}{near}")
    if len(ranked) > 3:
        lines.append(f"3 +{len(ranked) - 3} more undefined ids")
    return lines


def _coverage_line(snap: Snapshot) -> str:
    model = snap.model
    degree: dict = {}
    for e in model.get("edges", ()):
        for end in (e.get("sourceId"), e.get("targetId")):
            degree[end] = degree.get(end, 0) + 1
    machine_ids = {m["id"] for m in model.get("machines", ())}
    isolated = [n["id"] for n in model.get("nodes", ()) if not degree.get(n["id"]) and n["id"] not in machine_ids]
    unused = [d.got for d in snap.check.infos if d.rule == "EV-008"]
    named = {x for r in model.get("requirements", ()) for x in r.get("nodeIds", ())}
    free = [n["id"] for n in model.get("nodes", ()) if model.get("requirements") and n["id"] not in named and n["id"] not in machine_ids]
    parts = []
    if isolated:
        parts.append(f"{len(isolated)} isolated node(s) ({_some(isolated, 2)})")
    if free:
        parts.append(f"{len(free)} node(s) in no requirement")
    if unused:
        parts.append(f"{len(unused)} unused evidence")
    return ("7 coverage: " + ", ".join(parts)) if parts else ""


def _dims_line(dims: Mapping, required: tuple) -> str:
    cells = []
    for name in DIMENSIONS:
        mark = "ok" if dims[name]["ready"] else "no"
        cells.append(f"{name} {mark}" + ("" if name in required else "*"))
    return "dims: " + ", ".join(cells) + (" (*: not required yet)" if any(n not in required for n in DIMENSIONS) else "")


def _red_lines(snap: Snapshot) -> list:
    out = []
    for r in snap.red[:3]:
        out.append(f"1 red draft {r.name}: patch it (put mode=patch) or replace it; check shows what to fix")
    if len(snap.red) > 3:
        out.append(f"1 +{len(snap.red) - 3} more red drafts")
    return out


def _error_lines(check: "slicecheck.CheckResult") -> list:
    groups: dict = {}
    for d in check.errors:
        if _is_unresolved_ref(d) or d.rule in ("SM-002", "ST-009"):
            continue
        groups.setdefault(d.rule, []).append(d)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [f"1 ERROR {rule} x{len(items)}: first at {items[0].at} (check mode=full lists them)" for rule, items in ranked[:2]]


def _unreachable_lines(check: "slicecheck.CheckResult") -> list:
    found = [d for d in check.errors if d.rule == "SM-002"]
    return [f"6 unreachable states in {d.path.split('[')[1].split(']')[0]}: {_some(d.got if isinstance(d.got, list) else [d.got])}" for d in found[:2]]


# ---- loop guards that survive a restart: counters kept by ``put`` in ``.atlas/puts.json``

MAX_REPAIR_ROUNDS = 3
PUT_BUDGET_BASE = 8
PUT_BUDGET_PER_11_NODES = 2
DEFAULT_NODE_ESTIMATE = 40


def read_counters(paths: Paths) -> dict:
    """``{"puts": int, "red": {slice: rounds}}`` as ``put`` recorded them (zero when the file is absent or unreadable)."""
    data = _read_json(paths.dot / "puts.json") or {}
    red = data.get("red") if isinstance(data.get("red"), dict) else {}
    puts = data.get("puts")
    return {"puts": puts if isinstance(puts, int) and puts >= 0 else 0,
            "red": {str(k): v for k, v in red.items() if isinstance(v, int) and v > 0}}


def put_budget(node_count: int) -> int:
    """The put budget of the work order: 8 + 2 per started 11 nodes (a model of unknown size counts as 40 nodes)."""
    nodes = max(node_count, DEFAULT_NODE_ESTIMATE)
    return PUT_BUDGET_BASE + PUT_BUDGET_PER_11_NODES * -(-nodes // 11)


def _counter_view(counters: Mapping) -> dict:
    worst = max(counters["red"].values(), default=0)
    return {"puts": counters["puts"], "redRounds": max(0, worst - 1)}  # the first red put is the draft, the rest are repair rounds


def _guard_lines(counters: Mapping, snap: Snapshot) -> list:
    out = []
    stuck = sorted(name for name, rounds in counters["red"].items() if rounds - 1 >= MAX_REPAIR_ROUNDS)
    if stuck:
        out.append(_clip(f"0 STOP: {_some(stuck, 2)} is still red after {MAX_REPAIR_ROUNDS} repair rounds; report blocked with the last diagnostics"))
    budget = put_budget(len(snap.model.get("nodes", ())))
    if counters["puts"] > budget:
        out.append(_clip(f"0 STOP: {counters['puts']} puts exceed the budget of {budget}; report blocked with the last status and diagnostics"))
    return out


def _open_blocks(snap: Snapshot) -> list:
    """Ids of the BLOCK findings that are open now (the review accepted them; a resumed session must still see them)."""
    try:
        from . import review
    except ImportError:  # pragma: no cover - the review lane ships in the same package
        return []
    findings, _stats, _oracle_state = review.collect(review.context_of(snap))
    return [f.id for f in findings if f.sev == "BLOCK"]


def _select(lines: list, room: int) -> list:
    """At most ``room`` lines, in the order given. Every priority class that has a line keeps its first one (so that coverage or
    unreachable states are not starved by a long run of errors), the remaining room goes to the earlier lines."""
    if len(lines) <= room:
        return lines
    first, seen = set(), set()
    for index, text in enumerate(lines):
        cls = text[0] if text[:1].isdigit() else ""
        if cls not in seen:
            seen.add(cls)
            first.add(index)
    chosen = set(sorted(first)[:room])
    for index in range(len(lines)):
        if len(chosen) >= room:
            break
        chosen.add(index)
    return [lines[i] for i in sorted(chosen)]


def evaluate(snap: Snapshot) -> State:
    """The completion contract (see the module doc) for a snapshot."""
    model, paths = snap.model, snap.paths
    report = completeness_report(model, snap.prov)
    oracle = oracle_summary(read_oracle(paths))
    gates = {name: _gate(name, snap) for name in GATE_NAMES}
    oracle_applicable = gates["oracle"]["applicable"]
    dims = dimensions(snap, report, oracle)
    required = ALWAYS_REQUIRED + (("4D", "5D") if oracle_applicable else ())
    review = read_review(paths)
    review_ok = review_is_current(review, model) or _review_accepts(snap, review)
    errors = snap.check.errors
    needs = _needs_user(snap, oracle, oracle_applicable)
    elicit = _elicitation(snap) if snap.mode == "tobe" else None
    unmet_dims = [d for d in required if not dims[d]["ready"]]
    red_gate = [n for n in GATE_NAMES if gates[n]["applicable"] and not gates[n]["ok"]]
    empty = not snap.files and not snap.red

    blockers = []
    if snap.red:
        blockers.append(f"red-drafts:{len(snap.red)}")
    if errors:
        blockers.append(f"check-full:{len(errors)} ERROR")
    if report.blockers():
        blockers.append(f"completeness-block:{len(report.blockers())}")
    if unmet_dims:
        blockers.append("dims:" + ",".join(unmet_dims))
    for name in red_gate:
        blockers.append(f"gate-{name}:{gates[name]['detail'] or 'red'}"[:60])
    if needs:
        blockers.append(f"needs-user:{len(needs)}")
    if not review_ok:
        blockers.append("review:missing" if not review else "review:stale")

    if empty:
        phase = "collect"
    elif snap.red or any(not _is_unresolved_ref(d) and d.rule != "SM-002" for d in errors):
        phase = "repair"
    elif report.blockers() or unmet_dims or errors:
        phase = "author"
    elif elicit is not None and snap.mode == "tobe":
        phase = "author"
    elif needs:
        phase = "needs-user"
    elif red_gate:
        phase = "build"
    elif not review_ok:
        phase = "review"
    else:
        phase = "done"
    done = phase == "done"

    lines = []
    open_blocks: list = []
    if phase == "done":
        open_blocks = _open_blocks(snap)
        applicable = [n for n in GATE_NAMES if gates[n]["applicable"]]
        checked = "check full is clean, 1D-3D ready" + (f", gates green: {', '.join(applicable)}" if applicable else "; no generate gate is available yet")
        lines.append(f"done: {checked}; the review is recorded")
        if open_blocks:
            lines.append(_clip("open BLOCK (acknowledged at review, tell the user): " + ", ".join(open_blocks[:4])
                               + " (gemarc_atlas_review action=report shows them)"))
    elif phase == "needs-user":
        lines.extend(_clip("ask the user: " + x) for x in needs[:3])
    elif phase == "review":
        lines.append("review: authoring is complete; call gemarc_atlas_review (action=report), fix the findings once, then action=record")
    elif phase == "build":
        lines.extend(_clip(f"{n}: {gates[n]['detail'] or 'not green'}") for n in red_gate[:2])
    counters = read_counters(paths)
    guard = _guard_lines(counters, snap)
    lines.extend(guard)
    lines.extend(_red_lines(snap))
    lines.extend(_error_lines(snap.check))
    if elicit is not None:
        lines.append(f"tobe step {elicit[0]}/7: {elicit[1]}")
    if empty and snap.mode != "tobe":
        lines.append("collect: nothing authored yet; put 00-space (buildings, floors, rooms), then 10-structure slices of about 11 nodes each")
    if not empty:
        lines.extend(_progress_lines(model, snap.mode, bool(snap.red)))
    for slot, status, severity, count, refs in report.slot_summary():
        if severity != "BLOCK" or (empty and snap.mode != "tobe"):
            continue
        lines.append(f"2 BLOCK {slot} x{count}: {_SLOT_ADVICE.get(slot, status)} {_some(r for r in refs if r)}".rstrip())
    lines.extend(_pending_lines(snap.check))
    lines.extend(_requirement_gaps(model))
    cases_open = [e.ref for e in report.entries if e.slot == "case.expect"]
    if cases_open:
        lines.append(f"5 {len(cases_open)} case(s) without expectation (expect rows): {_some(cases_open)}")
    lines.extend(_unreachable_lines(snap.check))
    cov = _coverage_line(snap)
    if cov:
        lines.append(cov)
    tail = _dims_line(dims, required)
    kept = _select([_clip(x) for x in lines], MAX_NEXT - 1)
    kept.append(_clip(tail))
    shown = {k: v for k, v in _counter_view(counters).items() if v}
    return State(done, phase, kept, blockers, [_clip(x) for x in needs], dims, required, gates, report, review, review_ok, oracle,
                 open_blocks, shown)
