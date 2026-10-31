"""The oracle: expectations are intent, sealed before any result exists (design section 7, milestone M5).

Everything an assertion *is* (normalisation, the hash, whether it may be sealed) lives in ``rules/or_.py`` (pure). This module
is the **ledger** (``.atlas/oracle.json``) and the services that use it:

``seal(paths, model)``           register every clean assertion (a write call: put, simulate, build).
``run_verdicts(...)``            ``simulate``: blind, countable verdicts; ``ids`` and ``trace`` are reveals (ledgered).
``bite_run(...)``                the build-style anti-vacuity run (OR-004 mutants, SM-006 guard scan), time-boxed.
``sealed_runs(ws, project)``     the provider other code embeds results from: ONLY sealed or confirmed items (docs/atlas/spec/oracle-provider.md).
``sheet_text`` / ``write_sheet`` the oracle sheet (design 7.6); the MCP never writes the confirmation file.
``release_gate(ws, project)``    the structured list of blockers of the oracle part of the release gate.
``effective_summary``            counts for ``status`` (read-only).

**Ledger schema** (``gemarc-atlas-oracle/1``; sorted keys, LF, no timestamp: a ``tick`` counts the writes)::

    {schema, tick, next, provenance: "ok"|"unverified", reason, chain,
     items: {"case:<id>"|"intervention:<id>": {kind, id, ordinal, ah, seal, state: "sealed"|"revised", why, first: {ordinal, ah8},
            fp, withdrawn, revisions, exposed: {mismatch, ids, trace, build}, verdicts, last, bite, pairs, confirmedAh8, log}},
     issued: {digests: [...], items: {key: [ah8, ...]}}}

``seal = sha256(ah + ":" + ordinal)``, ``ah = sha256(normalised assertion)``; ``chain`` is the sha256 of the rest of the file.
``.atlas/oracle-anchor.json`` holds ``{tick, chain}`` of the last write: an older copy of the ledger put back has a smaller tick
(``rolled-back``), a recomputed chain no longer matches the anchor (``edited``), and a ledger missing after any put or any ledger
write is ``deleted`` (``put`` creates the ledger before the first put records itself).

**Exposure.** Every failing verdict is a mismatch on record whether or not the 3 KB reply listed it (the counts and the pass list
tell which items failed); only the ids and the trace follow what the reply showed. Items of one kind with one fingerprint share
exposure at once (``_spread_exposure``): one mismatch or reveal makes every other unconfirmed copy ``revised``.
The first seal (``first``) is kept when the row is deleted. The effective state ``confirmed`` is an overlay: the user's
confirmation file ``atlas/oracle-confirm.txt`` approves the item's current hash8. **Attestation, not authentication:**
nothing here can stop a writer who recomputes ``chain`` and ``seal`` or who forges the confirmation file after reading the sheet;
``tests/atlas/test_oracle_cheaters.py`` documents what each cheat leaves behind. Standard library only.
"""
from __future__ import annotations

import contextlib
import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..paths import Workspace
from . import model as am
from . import project as pj
from . import slices as sl
from . import status as status_mod
from .engine import causal_chain, check_prediction, index_model, mutate, run_causal, simulate, sweep
from .rules import or_

SCHEMA = "gemarc-atlas-oracle/1"
LEDGER_MAX_BYTES = 1024 * 1024
#: Deepest nesting a ledger may have: an authored cell is at most 56 levels deep (``format.rows.MAX_JSON_NESTING``) and the ledger wraps
#: it in about ten, so a real ledger stays below this. A deeper file is not one the tool wrote (and may not survive ``json.dumps`` with an
#: indent or ``copy.deepcopy``, which hit the recursion limit far below the decoder's).
LEDGER_MAX_DEPTH = 96
MAX_ITEMS = 2000
LOG_KEEP = 16
ISSUED_KEEP = 8
CONFIRM_NAME = "oracle-confirm.txt"
ANCHOR_NAME = "oracle-anchor.json"
IDS_LINE_CHARS = 480
FINAL_NODES = 12
CLIP_CHARS = 60
SHEET_DIR = "dist"
SHEET_NAME = "oracle-sheet.md"
DETAILS = ("count", "ids", "trace")
TRACE_LINES = 16
SIMULATE_CAP = 3000
TERMINAL_DIFF_LINES = 6
BITE_BUDGET_S = 8.0

E_UNREGISTERED = "GEMARC_ATLAS_ORACLE_001"   # OR-000
E_ARG = "GEMARC_ATLAS_ORACLE_002"
E_MODEL = "GEMARC_ATLAS_ORACLE_003"
E_LIMIT = "GEMARC_ATLAS_ORACLE_004"


class OracleError(Exception):
    """A refused oracle request: ``code`` and ``message`` become an ``invalid`` tool reply (nothing was written)."""

    def __init__(self, code: str, message: str, **extra):
        self.code, self.message, self.extra = code, message, extra
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------------------------------------------------------
# The ledger file
# ---------------------------------------------------------------------------------------------------------------


def _empty() -> dict:
    return {"schema": SCHEMA, "tick": 0, "next": 1, "provenance": "ok", "reason": "", "items": {},
            "issued": {"digests": [], "items": {}}, "summary": {}, "chain": ""}


def _seal_of(ah: str, ordinal: int) -> str:
    return or_.digest(f"{ah}:{ordinal}")


def _chain_of(data: Mapping) -> str:
    return or_.digest(or_.canon({k: v for k, v in data.items() if k != "chain"}))


@dataclass
class Ledger:
    data: dict
    tamper: str = ""        # "" | "edited" | "unreadable" | "deleted" | "rolled-back"
    exists: bool = True


def _clip(value, limit: int = CLIP_CHARS) -> str:
    """Text echoed from the caller: control characters become ``?`` and the length is bounded (a reply never grows with its input)."""
    text = "".join("?" if (ord(ch) < 32 or ord(ch) == 127 or ch in "\u2028\u2029") else ch for ch in str(value))
    return text if len(text) <= limit else text[:limit] + "..."


def _too_deep(value, limit: int = LEDGER_MAX_DEPTH) -> bool:
    """True when ``value`` nests deeper than ``limit`` levels (iterative: a hostile file cannot make the check itself recurse)."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return False


def _anchor(paths: pj.Paths) -> "dict | None":
    """``.atlas/oracle-anchor.json`` (``{tick, chain}`` of the last ledger write): ``None`` when absent, ``{}`` when unreadable.
    A copy of an older ``oracle.json`` put back over the new one is older than this file, so it is a ``rolled-back`` ledger."""
    target = paths.dot / ANCHOR_NAME
    if not target.is_file():
        return None
    try:
        raw = json.loads(sl.read_limited(target, sl.COUNTER_MAX_BYTES).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):   # RecursionError: a file nested past the decoder's limit
        return {}
    return raw if isinstance(raw, dict) and isinstance(raw.get("tick"), int) and isinstance(raw.get("chain"), str) else {}


def _puts(paths: pj.Paths) -> int:
    try:
        value = json.loads(sl.read_limited(paths.dot / "puts.json", sl.COUNTER_MAX_BYTES).decode("utf-8")).get("puts")
    except (OSError, ValueError, AttributeError, RecursionError):
        return 0
    return value if isinstance(value, int) else 0


def _entry_ok(key, entry) -> bool:
    if not isinstance(key, str) or not isinstance(entry, dict):
        return False
    ah, ordinal = entry.get("ah"), entry.get("ordinal")
    if not (isinstance(ah, str) and isinstance(ordinal, int) and entry.get("kind") in (or_.CASE, or_.INTERVENTION)):
        return False
    if entry.get("state") not in ("sealed", "revised") or not isinstance(entry.get("exposed"), dict):
        return False
    return entry.get("seal") == _seal_of(ah, ordinal) and key == f"{entry['kind']}:{entry.get('id')}"


def read_ledger(paths: pj.Paths) -> Ledger:
    """Read ``.atlas/oracle.json`` (never writes). A damaged, edited or (with earlier puts on record) missing file is ``tamper``."""
    path = paths.oracle
    try:
        present = path.is_file()
    except OSError:
        present = False
    anchor = _anchor(paths)
    if not present:
        # every put (red ones too) leaves a ledger file behind, so a missing file after any put, or after any ledger write, is a deletion
        return Ledger(_empty(), "deleted" if (_puts(paths) >= 1 or anchor is not None) else "", False)
    try:
        if path.stat().st_size > LEDGER_MAX_BYTES:
            return Ledger(_empty(), "unreadable")
        data = json.loads(sl.read_bytes(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):   # RecursionError: nested past the decoder's limit
        return Ledger(_empty(), "unreadable")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("items"), dict) or _too_deep(data):
        return Ledger(_empty(), "unreadable")
    base = _empty()
    for key, default in base.items():
        data.setdefault(key, copy.deepcopy(default))
    ok = (data.get("chain") == _chain_of(data) and all(_entry_ok(k, v) for k, v in data["items"].items())
          and isinstance(data.get("issued"), dict) and isinstance(data["tick"], int) and isinstance(data["next"], int))
    tamper = "" if ok else "edited"
    if not tamper and anchor is not None:
        if not anchor:
            tamper = "edited"
        elif data["tick"] < anchor["tick"]:
            tamper = "rolled-back"
        elif data["tick"] == anchor["tick"] and data.get("chain") != anchor["chain"]:
            tamper = "edited"
    return Ledger(data, tamper)


def _write_ledger(paths: pj.Paths, data: dict) -> None:
    data["chain"] = ""
    data["chain"] = _chain_of(data)
    raw = (json.dumps(data, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    if len(raw) > LEDGER_MAX_BYTES:
        raise OracleError(E_LIMIT, f"the oracle ledger would exceed {LEDGER_MAX_BYTES // 1024} KiB: split the project")
    sl.atomic_write(paths.oracle, raw)
    try:                                               # written after the ledger: a crash in between leaves the ledger ahead, which is fine
        sl.atomic_write(paths.dot / ANCHOR_NAME, (json.dumps({"chain": data["chain"], "tick": data["tick"]}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    except (OSError, sl.SliceError):
        pass


# ---------------------------------------------------------------------------------------------------------------
# The confirmation file and the oracle sheet
# ---------------------------------------------------------------------------------------------------------------

_TICK = re.compile(r"^\s*(?:[-*]\s*)?\[[xX]\]\s+(\S+)\s+([0-9a-f]{8})\b")
_APPROVE = re.compile(r"^\s*APPROVE-ALL\s+([0-9a-f]{12})\b")


def confirm_path(paths: pj.Paths) -> Path:
    return paths.atlas / CONFIRM_NAME


def parse_confirmation(text: str) -> tuple:
    """``(ticks [(id, hash8)], approve_all [digest])`` of a confirmation text; anything else is ignored."""
    ticks, approve = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = _TICK.match(line)
        if m:
            ticks.append((m.group(1), m.group(2)))
            continue
        m = _APPROVE.match(line)
        if m:
            approve.append(m.group(1))
    return ticks, approve


def ledger_digest(data: Mapping) -> str:
    """The digest of ``APPROVE-ALL``: over every registered assertion of the ledger (``id:hash8``), so any later edit or addition
    lapses a batch approval and the user has to look again."""
    lines = sorted(f"{e['id']}:{str(e['ah'])[:8]}" for e in data["items"].values() if not e.get("withdrawn"))
    return or_.digest("\n".join(lines))[:12]


def _read_confirmation(paths: pj.Paths) -> str:
    path = confirm_path(paths)
    try:
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            return ""
        return sl.read_bytes(path).decode("utf-8-sig", errors="replace")
    except OSError:
        return ""


def resolve_confirmations(paths: pj.Paths, data: Mapping) -> tuple:
    """``({key: ah8 the file approves}, [ignored lines])``, resolved against the ledger entries. Only what the MCP printed in a
    sheet counts (``issued`` in the ledger): a tick for a hash that was never issued, or an APPROVE-ALL digest that was never
    printed, is ignored and reported."""
    ticks, approve = parse_confirmation(_read_confirmation(paths))
    issued = data.get("issued") or {}
    by_id = {e["id"]: (key, e) for key, e in data["items"].items() if not e.get("withdrawn")}
    out: dict = {}
    ignored: list = []
    for ident, ah8 in ticks:
        found = by_id.get(ident)
        if found is not None and str(found[1]["ah"])[:8] == ah8 and ah8 in (issued.get("items") or {}).get(found[0], ()):
            out[found[0]] = ah8
        else:
            ignored.append(f"{ident} {ah8}")
    digest = ledger_digest(data)
    for line in approve:
        if line == digest and line in (issued.get("digests") or ()):
            for key, e in data["items"].items():
                if not e.get("withdrawn"):
                    out[key] = str(e["ah"])[:8]
        else:
            ignored.append(f"APPROVE-ALL {line}")
    return out, ignored


# ---------------------------------------------------------------------------------------------------------------
# Sealing (design 7.2) and the states (7.4)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class SealReport:
    sealed: list = field(default_factory=list)       # keys sealed by this call
    resealed: list = field(default_factory=list)     # re-sealed in place (edit before any result was shown)
    revised: list = field(default_factory=list)      # became or stayed revised through this call
    waiting: list = field(default_factory=list)      # (key, why): clean-row condition not met, sealing waits
    withdrawn: list = field(default_factory=list)

    def summary(self) -> dict:
        out = {}
        if self.sealed or self.resealed:
            out["sealed"] = len(self.sealed) + len(self.resealed)
        if self.revised:
            out["revised"] = len(self.revised)
        if self.waiting:
            out["waiting"] = len(self.waiting)
            out["why"] = f"{self.waiting[0][0]} {self.waiting[0][1]}"[:110]
        return out


def _exposure(entry: Mapping) -> int:
    return sum(int(v) for v in (entry.get("exposed") or {}).values() if isinstance(v, int))


def _log(entry: dict, tick: int, what: str) -> None:
    log = entry.setdefault("log", [])
    log.append([tick, what])
    del log[:-LOG_KEEP]


def _new_entry(item: or_.Item, ordinal: int, state: str, why: str, tick: int) -> dict:
    entry = {"kind": item.kind, "id": item.id, "ordinal": ordinal, "ah": item.ah, "seal": _seal_of(item.ah, ordinal), "state": state,
             "why": why, "first": {"ordinal": ordinal, "ah8": item.ah8}, "fp": item.fp, "withdrawn": False, "revisions": 0,
             "exposed": {"mismatch": 0, "ids": 0, "trace": 0, "build": 0}, "verdicts": 0, "firstPass": None, "last": None, "bite": None, "pairs": {},
             "confirmedAh8": "", "log": []}
    _log(entry, tick, "sealed" if state == "sealed" else "sealed-revised")
    return entry


def _clone_of(data: Mapping, item: or_.Item) -> str:
    """A different item of the same kind with the same fingerprint whose result was shown (a copy inherits that exposure)."""
    for key, entry in data["items"].items():
        if key != item.key and entry.get("kind") == item.kind and entry.get("fp") == item.fp and _exposure(entry) > 0:
            return key
    return ""


def _spread_exposure(data: dict, tick: int) -> list:
    """Items of one kind with one fingerprint (same events, policy and context; same deltas) are ONE exposure unit: what a result
    taught about any member is known for all of them. As soon as a member has a mismatch, a reveal or a build on record, every other
    member that is not confirmed becomes ``revised`` (a passing sibling too: it is the copy a shotgun of guesses leaves standing).
    Withdrawn members still count as sources. Returns the keys that changed."""
    groups: dict = {}
    for key, entry in data["items"].items():
        if isinstance(entry, Mapping) and entry.get("fp"):
            groups.setdefault((entry.get("kind"), entry["fp"]), []).append(key)
    changed: list = []
    for keys in groups.values():
        sources = [k for k in keys if _exposure(data["items"][k]) > 0]
        if len(keys) < 2 or not sources:
            continue
        for key in keys:
            entry = data["items"][key]
            if (key in sources or entry.get("withdrawn") or entry["state"] == "revised"
                    or entry.get("confirmedAh8") == str(entry["ah"])[:8]):
                continue
            entry["state"] = "revised"
            entry["why"] = f"a copy of it ({sources[0]}, same events) was shown a result"
            entry["revisions"] += 1
            _log(entry, tick, "revised")
            changed.append(key)
    return changed


def apply_tamper(led: Ledger, paths: "pj.Paths | None" = None) -> dict:
    """The ledger to continue from after damage: parsable but edited or rolled-back items are demoted to ``revised`` (their history
    can no longer be trusted), a missing or unreadable ledger starts empty; either way the provenance is ``unverified`` for good and
    the tick never falls behind the anchor (the next write is not itself a rollback)."""
    data = led.data
    if led.tamper:
        data["provenance"] = "unverified"
        data["reason"] = led.tamper
        anchor = _anchor(paths) if paths is not None else None
        if anchor:
            data["tick"] = max(data["tick"], anchor["tick"])
        if led.tamper in ("edited", "rolled-back"):
            for key, entry in list(data["items"].items()):
                if not isinstance(entry, dict):
                    del data["items"][key]
                    continue
                entry["state"] = "revised"
                entry["why"] = "the ledger was edited: its history cannot be trusted"
                if isinstance(entry.get("ah"), str) and isinstance(entry.get("ordinal"), int):
                    entry["seal"] = _seal_of(entry["ah"], entry["ordinal"])
    return data


def _sync(data: dict, items: Mapping, report: SealReport) -> None:
    """Register the clean assertions and apply the rules of 7.2 and 7.4 (``data`` is changed in place)."""
    tick = data["tick"]
    for key, item in items.items():
        entry = data["items"].get(key)
        if entry is None:
            if not item.clean:
                report.waiting.append((key, item.why))
                continue
            if len(data["items"]) >= MAX_ITEMS:
                raise OracleError(E_LIMIT, f"the oracle ledger holds {MAX_ITEMS} assertions already")
            clone = _clone_of(data, item)
            ordinal = data["next"]
            data["next"] = ordinal + 1
            if clone:
                data["items"][key] = _new_entry(item, ordinal, "revised", f"copy of {clone}, whose result was shown", tick)
                report.revised.append(key)
            else:
                data["items"][key] = _new_entry(item, ordinal, "sealed", "", tick)
            report.sealed.append(key)
            continue
        if not item.clean:
            report.waiting.append((key, item.why))
            continue
        changed = entry["ah"] != item.ah
        returning = bool(entry.get("withdrawn"))
        exposed = _exposure(entry) > 0
        if changed:
            clone = _clone_of(data, item)              # a probe copy (same events) was shown a result: what it taught is not blind
            if exposed or clone or entry["state"] == "revised" or entry.get("confirmedAh8") == entry["ah"][:8]:
                if entry["state"] != "revised":
                    entry["state"] = "revised"
                    entry["why"] = ("edited after a mismatch report, a reveal or a build that embedded its trace" if exposed else
                                    f"edited after a copy of it ({clone}) was shown a result" if clone else "edited after the user confirmed it")
                report.revised.append(key)
                entry["revisions"] += 1
                _log(entry, tick, "revised")
            else:
                report.resealed.append(key)
                _log(entry, tick, "resealed")
            entry["ah"] = item.ah
            entry["seal"] = _seal_of(item.ah, entry["ordinal"])
            entry["last"], entry["bite"], entry["pairs"] = None, None, {}
        elif returning and exposed and entry["state"] != "revised":
            entry["state"] = "revised"
            entry["why"] = "withdrawn and added again after a result was shown"
            entry["revisions"] += 1
            report.revised.append(key)
            _log(entry, tick, "readded")
        entry["withdrawn"] = False
        entry["fp"] = item.fp
    for key, entry in data["items"].items():
        if key not in items and not entry.get("withdrawn"):
            entry["withdrawn"] = True
            report.withdrawn.append(key)
            _log(entry, tick, "withdrawn")


def _confirm_into(data: dict, confirmed: Mapping) -> None:
    """Cache what the confirmation file approves in the entries (``confirmedAh8``): the next edit compares against it."""
    for key, entry in data["items"].items():
        entry["confirmedAh8"] = confirmed.get(key, "") if not entry.get("withdrawn") else ""


def _load_for_write(paths: pj.Paths) -> dict:
    return apply_tamper(read_ledger(paths), paths)


@contextlib.contextmanager
def _writing(paths: pj.Paths, model: Mapping):
    """Inside the project lock: load the ledger, register the model's assertions, yield ``(data, items, report)``, save."""
    with sl.project_lock(paths):
        data = _load_for_write(paths)
        items = or_.collect_items(model)
        report = SealReport()
        _confirm_into(data, resolve_confirmations(paths, data)[0])      # what the user approved BEFORE this call's edits
        _sync(data, items, report)
        _confirm_into(data, resolve_confirmations(paths, data)[0])
        yield data, items, report
        _spread_exposure(data, data["tick"] + 1)
        data["tick"] += 1
        data["summary"] = _summary(data)
        _write_ledger(paths, data)


def _summary(data: Mapping) -> dict:
    """What the review report and the pilots read: ``blindMismatch`` (share of items whose FIRST shown verdict failed), ``bite``
    (kill rate of the mutant runs whose assertions held) and how many items have a verdict."""
    entries = [e for e in data["items"].values() if isinstance(e, Mapping)]
    first = [e["firstPass"] for e in entries if isinstance(e.get("firstPass"), bool)]
    out: dict = {"verdicted": len(first)}
    if first:
        out["blindMismatch"] = round(sum(1 for x in first if not x) / len(first), 4)
    bites = [e["bite"] for e in entries if isinstance(e.get("bite"), Mapping) and e["bite"].get("holds") and e["bite"].get("total")]
    if bites:
        out["bite"] = round(sum(b["killed"] for b in bites) / sum(b["total"] for b in bites), 4)
    return out


def seal(paths: pj.Paths, model: Mapping) -> SealReport:
    """Register every clean assertion of ``model`` (a write: put calls this after a clean commit)."""
    with _writing(paths, model) as (_data, _items, report):
        pass
    return report


def touch(paths: pj.Paths) -> None:
    """Make sure a ledger file exists (a put that committed nothing): a later missing file is then a deletion."""
    with sl.project_lock(paths):
        if not paths.oracle.is_file():
            data = apply_tamper(read_ledger(paths), paths)
            data["tick"] += 1
            _write_ledger(paths, data)


# ---------------------------------------------------------------------------------------------------------------
# Reading: views for the rules, summaries for status
# ---------------------------------------------------------------------------------------------------------------


def make_view(paths: pj.Paths) -> or_.View:
    """A read-only ``View`` of the ledger for one check; the confirmation file is resolved once the items are known."""
    led = read_ledger(paths)

    def resolve(items: Mapping) -> Mapping:
        return resolve_confirmations(paths, led.data)[0]

    return or_.View(led.data["items"], resolve=resolve, tamper=led.tamper, provenance=led.data.get("provenance", "ok"),
                    reason=str(led.data.get("reason") or ""))


def registration_findings(paths: pj.Paths, model: Mapping, provenance: "Mapping | None") -> list:
    """OR-000 diagnostics for a full check (read-only): the assertions of ``model`` that the ledger has not sealed."""
    return or_.registration_findings(model, provenance, make_view(paths))


@contextlib.contextmanager
def reading(paths: pj.Paths):
    """``with oracle.reading(paths): slicecheck.check(...)``: the oracle rules then see the ledger (read-only)."""
    try:
        view = make_view(paths)
    except (OSError, ValueError, KeyError, TypeError):
        view = None
    with or_.reading(view):
        yield


def effective_states(paths: pj.Paths, model: Mapping) -> dict:
    """``{key: (item, state, why)}`` for every assertion of ``model`` (state: unregistered, sealed, revised, confirmed)."""
    view = make_view(paths)
    items = or_.collect_items(model)
    view.bind(items)
    return {key: (item, *view.state_of(item)) for key, item in items.items()}


def effective_summary(paths: pj.Paths, model: Mapping) -> dict:
    """The counts ``status`` reads: ``cases``/``interventions`` are the sealed or confirmed ones."""
    out = {"present": paths.oracle.is_file(), "total": 0, "sealed": 0, "revised": 0, "confirmed": 0, "cases": 0, "interventions": 0,
           "unconfirmed": 0, "unregistered": 0}
    for item, state, _why in effective_states(paths, model).values():
        out["total"] += 1
        if state == "unregistered":
            out["unregistered"] += 1       # needs a write call (put, simulate, build), not the user: not "unconfirmed"
            continue
        out[state] += 1
        if state in ("sealed", "confirmed"):
            out["cases" if item.kind == or_.CASE else "interventions"] += 1
        if state != "confirmed":
            out["unconfirmed"] += 1
    return out


def ledger_summary(data: "Mapping | None") -> dict:
    """The same counts from a raw ledger (no model: a confirmation overlay needs one, so only the ledger's own cache counts)."""
    out = {"present": data is not None, "total": 0, "sealed": 0, "revised": 0, "confirmed": 0, "cases": 0, "interventions": 0, "unconfirmed": 0}
    items = (data or {}).get("items") if isinstance(data, Mapping) else None
    if not isinstance(items, Mapping):
        return out
    for entry in items.values():
        if not isinstance(entry, Mapping) or entry.get("withdrawn") or entry.get("kind") not in (or_.CASE, or_.INTERVENTION):
            continue
        out["total"] += 1
        confirmed = bool(entry.get("confirmedAh8")) and entry.get("confirmedAh8") == str(entry.get("ah", ""))[:8]
        state = "confirmed" if confirmed else entry.get("state")
        if state in ("sealed", "revised", "confirmed"):
            out[state] += 1
        if state in ("sealed", "confirmed"):
            out["cases" if entry["kind"] == or_.CASE else "interventions"] += 1
        if state != "confirmed":
            out["unconfirmed"] += 1
    return out


# ---------------------------------------------------------------------------------------------------------------
# Project access
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Loaded:
    paths: pj.Paths
    config: pj.Config
    model: dict
    sha8: str
    lang: str


def _load(ws: Workspace, project: str, *, need_contract: bool) -> Loaded:
    paths = pj.project_paths(ws, project)
    cfg = pj.load_config(paths)
    pj.check_confined(ws, paths)
    snap = status_mod.load_snapshot(paths, cfg, mode="slice")
    model = snap.model
    if need_contract:
        issues = am.contract_issues(model)
        if issues:
            first = issues[0]
            raise OracleError(E_MODEL, f"the committed model breaks the contract ({len(issues)} issue(s)); the engine runs only on a valid model; "
                              f"first: {first['code']} {_clip(first['path'], 120)}", issues=[{"code": i["code"], "path": i["path"]} for i in issues[:3]])
    return Loaded(paths, cfg, model, am.model_sha8(model), cfg.languages[0] if cfg.languages else "en-US")


# ---------------------------------------------------------------------------------------------------------------
# Verdicts (design 7.3)
# ---------------------------------------------------------------------------------------------------------------


def _case_verdict(model: Mapping, ix, item: or_.Item, case: Mapping) -> dict:
    run = simulate(model, case, None, {"index": ix})
    if run["status"] != "OK":
        return {"undecided": run["status"], "run": run}
    found = mutate.violations({"terminal": item.assertion["terminal"], "outcomes": item.assertion["outcomes"]}, run, case)
    bad_nodes = [v["nodeId"] for v in found if v["kind"] == "terminal"]
    bad_rules = [v["ruleId"] for v in found if v["kind"] == "outcome"]
    trace = run["trace"]
    return {"run": run, "pass": not found, "terminal": [len(bad_nodes), len(item.assertion["terminal"])],
            "outcomes": [len(bad_rules), len(item.assertion["outcomes"])], "badNodes": bad_nodes, "badRules": bad_rules,
            "events": len(case.get("events", ())), "unbound": sum(1 for t in trace if t["outcome"] == "Unbound"),
            "blocked": sum(1 for t in trace if t["outcome"] == "Blocked")}


def _pair_verdict(model: Mapping, ix, item: or_.Item, iv: Mapping, case: Mapping, locale: str) -> dict:
    result = run_causal(model, case["caseId"], [iv], {"index": ix, "locale": locale})
    if result["status"] == "LIMIT_EXCEEDED" or result.get("diff", {}) and (result.get("diff") or {}).get("truncated"):
        return {"undecided": "LIMIT_EXCEEDED", "result": result}
    pred = check_prediction({**iv, "expect": {**(iv.get("expect") or {})}}, result) or {"mismatches": []}
    kinds = {m["code"] if "code" in m else m.get("kind"): m for m in pred["mismatches"]}
    bad_nodes = [m["args"]["nodeId"] for m in pred["mismatches"] if m.get("code") == "terminal"]
    run_b = result.get("B") or {}
    trace = run_b.get("trace") or []
    return {"result": result, "pass": not pred["mismatches"], "terminal": [len(bad_nodes), len(item.assertion.get("terminal", {}))],
            "klass": "outcomeClass" in kinds, "status": "status" in kinds, "badNodes": bad_nodes,
            "events": len(case.get("events", ())), "unbound": sum(1 for t in trace if t["outcome"] == "Unbound"),
            "blocked": sum(1 for t in trace if t["outcome"] == "Blocked")}


def _usable(states: Mapping, key: str, *, sealed_only: bool = False) -> bool:
    entry = states.get(key)
    if entry is None:
        return False
    state = entry[1]
    return state in ("sealed", "confirmed") if sealed_only else state != "unregistered"


def _refusal(states: Mapping, key: str, kind: str, ident: str) -> str:
    entry = states.get(key)
    ident = _clip(ident)
    if entry is None:
        return f"OR-000 {ident}: not registered: the {kind} has no expectation to seal (write its expect row first)"
    return f"OR-000 {ident}: not registered: {_clip(entry[2] or 'call put, simulate or build', 200)}"


def _record_case(entry: dict, sha8: str, tick: int, v: Mapping) -> None:
    entry["verdicts"] += 1
    if entry.get("firstPass") is None:
        entry["firstPass"] = bool(v["pass"])          # the blind verdict: the first one the reply showed
    entry["last"] = {"sha": sha8, "pass": bool(v["pass"]), "terminal": v["terminal"], "outcomes": v["outcomes"], "tick": tick}
    if not v["pass"]:
        entry["exposed"]["mismatch"] += 1
        _log(entry, tick, "mismatch")


def _record_pair(entry: dict, case_id: str, sha8: str, tick: int, v: Mapping) -> None:
    entry["verdicts"] += 1
    if entry.get("firstPass") is None:
        entry["firstPass"] = bool(v["pass"])
    entry["pairs"][case_id] = {"sha": sha8, "pass": bool(v["pass"]), "terminal": v["terminal"], "klass": bool(v["klass"]), "status": bool(v["status"])}
    fresh = [p for p in entry["pairs"].values() if p.get("sha") == sha8]
    entry["pairs"] = {k: p for k, p in entry["pairs"].items() if p.get("sha") == sha8}
    entry["last"] = {"sha": sha8, "pass": all(p["pass"] for p in fresh), "terminal": [sum(p["terminal"][0] for p in fresh), sum(p["terminal"][1] for p in fresh)],
                     "klass": any(p["klass"] for p in fresh), "status": any(p["status"] for p in fresh), "tick": tick}
    if not v["pass"]:
        entry["exposed"]["mismatch"] += 1
        _log(entry, tick, "mismatch")


def _reveal(entry: dict, kind: str, tick: int) -> None:
    entry["exposed"][kind] += 1
    _log(entry, tick, kind)


def _trace_lines(trace: list) -> list:
    return [f"{t['index']} {t['eventType']} {t['sourceNodeId']}>{t['targetNodeId']} {t['outcome']}" for t in trace[:TRACE_LINES]]


@dataclass
class _Result:
    """One verdict, with the line or trace block it contributes to the reply and what recording it does to the ledger."""

    label: str
    passed: bool
    line: str = ""
    block: "dict | None" = None
    apply: object = None            # fn(data, tick, hidden=False): record the verdict and the reveal
    keys: tuple = ()                # ledger keys of the items this verdict is about


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def run_verdicts(ws: Workspace, project: str, case: str = "", intervention: str = "", detail: str = "count") -> dict:
    """``simulate``: seal, then blind verdicts for the registered assertions (see the module doc). Needs ``GEMARC_ALLOW_WRITE=1``.

    ``ids`` and ``trace`` are reveals and need a target (``case`` and/or ``intervention``): a project-wide reveal would be cut by
    the 3 KB cap. What the reply cannot show is not hidden from the ledger: EVERY failing verdict is recorded as a mismatch (and
    as the first verdict) whether or not its line fits, because the counts and the pass list tell the caller which items failed;
    only an ids or trace reveal is recorded for what the reply really shows (``notShown`` counts the rest)."""
    if detail not in DETAILS:
        raise OracleError(E_ARG, f"detail is one of {', '.join(DETAILS)}")
    if detail != "count" and not (case or intervention):
        raise OracleError(E_ARG, f"detail={detail} is a reveal: name the case or the intervention it is for (the default count needs no target)")
    ws.writable()
    loaded = _load(ws, project, need_contract=False)
    model, paths = loaded.model, loaded.paths
    cases = {c["caseId"]: c for c in model.get("cases", ())}
    ivs = {i["interventionId"]: i for i in model.get("interventions", ())}
    if case and case not in cases:
        raise OracleError(E_ARG, f"no case {_clip(case)}")
    if intervention and intervention not in ivs:
        raise OracleError(E_ARG, f"no intervention {_clip(intervention)}")
    if case and intervention and case not in ivs[intervention].get("appliesTo", ()):
        raise OracleError(E_ARG, f"intervention {_clip(intervention)} does not apply to case {_clip(case)}")
    results: list = []
    refused: list = []
    summary = {"baselines": {"run": 0, "pass": 0, "fail": 0, "undecided": 0}, "pairs": {"run": 0, "pass": 0, "fail": 0, "undecided": 0}}
    undecided: list = []
    not_shown = 0
    contract_error: "OracleError | None" = None
    with _writing(paths, model) as (data, items, report):
        view = or_.View(data["items"], resolve=lambda its: resolve_confirmations(paths, data)[0]).bind(items)
        states = {key: (item, *view.state_of(item)) for key, item in items.items()}
        tick = data["tick"] + 1
        explicit = bool(case or intervention)
        issues = am.contract_issues(model)
        if issues:
            # the engine runs only on a valid model; an expectation that names an undefined node is such a defect, and an explicit
            # request for an item that is not registered is answered with OR-000 (below), not with the contract message
            needed = ([f"{or_.CASE}:{case}"] if case else []) + ([f"{or_.INTERVENTION}:{intervention}"] if intervention else [])
            if not explicit or all(_usable(states, k) for k in needed):
                contract_error = OracleError(E_MODEL, f"the committed model breaks the contract ({len(issues)} issue(s)); the engine runs only on a valid model; "
                                             f"first: {issues[0]['code']} {_clip(issues[0]['path'], 120)}", issues=[{"code": i["code"], "path": i["path"]} for i in issues[:3]])
        ix = index_model(model) if not issues else None
        want_cases = [case] if case and not intervention else ([] if intervention else list(cases))
        pairs: list = []
        if intervention:
            pairs = [(intervention, c) for c in ivs[intervention].get("appliesTo", ()) if c in cases and (not case or c == case)]
        elif not case:
            pairs = [(i, c) for i, iv in ivs.items() for c in iv.get("appliesTo", ()) if c in cases]
        if issues:                                     # nothing runs; only the refusal of an unregistered target remains
            for k, kind, ident in ((f"{or_.CASE}:{case}", "case", case), (f"{or_.INTERVENTION}:{intervention}", "intervention", intervention)):
                if ident and not _usable(states, k):
                    refused.append(_refusal(states, k, kind, ident))
            want_cases, pairs = [], []
        for cid in want_cases:
            key = f"{or_.CASE}:{cid}"
            if key not in items and not explicit:
                continue                               # a case that asserts nothing has nothing to seal: not listed
            if not _usable(states, key):
                refused.append(_refusal(states, key, "case", cid))
                continue
            v = _case_verdict(model, ix, items[key], cases[cid])
            summary["baselines"]["run"] += 1
            if "undecided" in v:
                summary["baselines"]["undecided"] += 1
                undecided.append(f"W {cid}: undecided ({v['undecided']}): raise maxEvents or shorten the case")
                continue
            summary["baselines"]["pass" if v["pass"] else "fail"] += 1
            results.append(_Result(cid, v["pass"], "" if v["pass"] else _fail_line("OR-001", cid, v, detail),
                                   _case_block(cid, v) if detail == "trace" else None, _case_effect(key, loaded.sha8, v, detail), (key,)))
        for iid, cid in pairs:
            ikey, ckey = f"{or_.INTERVENTION}:{iid}", f"{or_.CASE}:{cid}"
            missing = [(k, kind, ident) for k, kind, ident in ((ikey, "intervention", iid), (ckey, "case", cid)) if not _usable(states, k)]
            if missing:
                refused.extend(_refusal(states, k, kind, ident) for k, kind, ident in missing)
                continue
            v = _pair_verdict(model, ix, items[ikey], ivs[iid], cases[cid], loaded.lang)
            summary["pairs"]["run"] += 1
            label = f"{iid} x {cid}"
            if "undecided" in v:
                summary["pairs"]["undecided"] += 1
                undecided.append(f"W {label}: undecided ({v['undecided']})")
                continue
            summary["pairs"]["pass" if v["pass"] else "fail"] += 1
            results.append(_Result(label, v["pass"], "" if v["pass"] else _fail_line("IV-002", label, v, detail),
                                   _pair_block(label, v) if detail == "trace" else None, _pair_effect(ikey, ckey, cid, loaded.sha8, v, detail), (ikey, ckey)))
        reply = {"status": "ok", "authority": "local", "detail": detail, "modelSha8": loaded.sha8, "summary": summary}
        passed = [r.label for r in results if r.passed]
        if passed:
            reply["pass"] = passed[:6] + ([f"+{len(passed) - 6} more"] if len(passed) > 6 else [])
        failing = any(not r.passed for r in results)
        ran = summary["baselines"]["run"] + summary["pairs"]["run"]
        reply["next"] = _next_lines(refused, failing, 0, ran)
        if refused:
            reply["refused"] = refused[:4] + ([f"+{len(refused) - 4} more"] if len(refused) > 4 else [])
        if undecided:
            reply["undecided"] = undecided[:3]
        # nothing ran and nothing was refused: there is nothing to say "clean" about (a vacuous success is not verification)
        reply["clean"] = None if (ran == 0 and not refused) else (not failing and not undecided and not refused)
        budget = SIMULATE_CAP - 300                    # room for ``notShown``, ``registered`` and the revised line
        shown: list = []
        shown_fail: list = []
        shown_trace: list = []
        for r in results:
            if r.passed and r.block is None:
                shown.append(r)                        # a pass shows nothing beyond the count
                continue
            trial_fail = shown_fail + ([r.line] if r.line else [])
            trial_trace = shown_trace + ([r.block] if r.block else [])
            probe = dict(reply)
            if trial_fail:
                probe["fail"] = trial_fail
            if trial_trace:
                probe["trace"] = trial_trace
            if _json_bytes(probe) > budget:
                not_shown += 1
                continue
            shown_fail, shown_trace = trial_fail, trial_trace
            shown.append(r)
        for r in results:
            r.apply(data, tick, hidden=r not in shown)  # every verdict is recorded; only the reveals follow what the reply shows
        _spread_exposure(data, tick)
        revised = sum(1 for r in results if any(_is_revised(data["items"][k]) for k in r.keys))
        if revised:
            reply["next"] = _next_lines(refused, failing, revised, ran)
        if shown_fail:
            reply["fail"] = shown_fail
        if shown_trace:
            reply["trace"] = shown_trace
        if not_shown:
            reply["notShown"] = not_shown
        if report.summary():
            reply["registered"] = report.summary()
    if explicit and not (summary["baselines"]["run"] or summary["pairs"]["run"]) and refused:
        raise OracleError(E_UNREGISTERED, refused[0], rule="OR-000", refused=refused[:4])
    if contract_error is not None:
        raise contract_error
    return reply


def _case_effect(key: str, sha8: str, v: Mapping, detail: str):
    def apply(data: dict, tick: int, hidden: bool = False) -> None:
        entry = data["items"][key]
        _record_case(entry, sha8, tick, v)             # always: the counts and the pass list show which items failed
        if hidden:
            return                                     # the reply did not show the ids or the trace of this verdict
        if detail == "ids" and (v["badNodes"] or v.get("badRules")):
            _reveal(entry, "ids", tick)
        if detail == "trace":
            _reveal(entry, "trace", tick)
    return apply


def _pair_effect(ikey: str, ckey: str, cid: str, sha8: str, v: Mapping, detail: str):
    def apply(data: dict, tick: int, hidden: bool = False) -> None:
        entry = data["items"][ikey]
        _record_pair(entry, cid, sha8, tick, v)
        if hidden:
            return
        if detail == "ids" and v["badNodes"]:
            _reveal(entry, "ids", tick)
        if detail == "trace":
            _reveal(entry, "trace", tick)
            _reveal(data["items"][ckey], "trace", tick)
    return apply


def _final_of(final: Mapping, nodes: list) -> dict:
    """The final states of the disagreeing nodes, at most ``FINAL_NODES`` of them (``finalMore`` counts the rest)."""
    out = {"final": {n: final.get(n) for n in nodes[:FINAL_NODES]}}
    if len(nodes) > FINAL_NODES:
        out["finalMore"] = len(nodes) - FINAL_NODES
    return out


def _case_block(label: str, v: Mapping) -> dict:
    run = v["run"]
    return {"case": label, "events": _trace_lines(run["trace"]), **_final_of(run["finalState"], v["badNodes"])}


def _pair_block(label: str, v: Mapping) -> dict:
    result = v["result"]
    diff = result.get("diff") or {}
    run_b = result.get("B") or {}
    return {"pair": label, "outcomeClass": diff.get("outcomeClass"), "events": _trace_lines(run_b.get("trace") or []),
            "terminalDiff": (diff.get("terminalDiff") or [])[:TERMINAL_DIFF_LINES],
            **_final_of(run_b.get("finalState") or {}, v["badNodes"])}


def _fail_line(rule: str, label: str, v: Mapping, detail: str) -> str:
    parts = []
    if v["terminal"][0]:
        parts.append(f"terminal {v['terminal'][0]}/{v['terminal'][1]}")
    if rule == "OR-001":
        if v["outcomes"][0]:
            parts.append(f"outcomes {v['outcomes'][0]}/{v['outcomes'][1]}")
    else:
        if v["klass"]:
            parts.append("class differs")
        if v["status"]:
            parts.append("status differs")
    text = ", ".join(parts) or "differs"
    line = f"E {rule} {label}: {text} (events {v['events']}, Unbound {v['unbound']}, Blocked {v['blocked']})"
    if detail in ("ids", "trace"):
        ids = list(v["badNodes"]) + [f"out:{r}" for r in v.get("badRules", ())]
        if ids:
            kept, used = [], 0
            for ident in ids:
                if kept and used + len(ident) + 2 > IDS_LINE_CHARS:
                    break
                kept.append(ident)
                used += len(ident) + 2
            line += " ids: " + ", ".join(kept) + (f", ... +{len(ids) - len(kept)} more" if len(kept) < len(ids) else "")
    return line


def _is_revised(entry: Mapping) -> bool:
    return entry.get("state") == "revised" and entry.get("confirmedAh8") != str(entry.get("ah", ""))[:8]


def _next_lines(refused: list, failing: bool, revised: int, ran: int) -> list:
    out = []
    if failing:
        out.append("a mismatch: decide from the requirement or the code, not from the result; ids and trace are reveals (recorded); an edit after a mismatch makes the item revised")
    if refused:
        out.append("OR-000: an unregistered expectation gets no verdict: name only defined nodes in expect rows, then put")
    if revised:
        out.append(f"{revised} verdict(s) are for revised items: they do not count as sealed; the user confirms them in the oracle sheet before release")
    if not ran and not refused:
        out.append("nothing to verify: no registered expectation (write expect rows in a case or an intervention, then put)")
    elif not failing and not refused and not revised:
        out.append("all verdicts agree: status view=oracle shows the seal counts; the user confirms the oracle sheet before release")
    return out[:3]


# ---------------------------------------------------------------------------------------------------------------
# Anti-vacuity run (OR-004, SM-006), the build-style part
# ---------------------------------------------------------------------------------------------------------------


def bite_run(ws: Workspace, project: str, *, budget: float = BITE_BUDGET_S, cap: int = mutate.DEFAULT_CAP) -> dict:
    """Verdicts for every registered case, then up to ``cap`` mutants per case whose assertion held, and the guard scan.

    Time-boxed (``budget`` seconds for the mutants): cases not reached are listed as skipped and the reply says ``partial``.
    A failing baseline gets no mutant run (its numbers would say nothing); that fact is the OR-001 verdict that was just recorded.
    The kill counts are stored in the ledger (``bite``) for the OR-004 rule; reported as ``bite`` in the quality page.
    """
    ws.writable()
    loaded = _load(ws, project, need_contract=True)
    model, paths = loaded.model, loaded.paths
    ix = index_model(model)
    started = time.perf_counter()
    bite_rows: list = []
    skipped: list = []
    with _writing(paths, model) as (data, items, _report):
        view = or_.View(data["items"], resolve=lambda its: resolve_confirmations(paths, data)[0]).bind(items)
        tick = data["tick"] + 1
        for case in model.get("cases", ()):
            key = f"{or_.CASE}:{case['caseId']}"
            item = items.get(key)
            if item is None or view.state_of(item)[0] == "unregistered":
                continue
            entry = data["items"][key]
            v = _case_verdict(model, ix, item, case)
            if "undecided" in v:
                skipped.append(f"{case['caseId']} undecided")
                continue
            _record_case(entry, loaded.sha8, tick, v)
            if not v["pass"]:
                skipped.append(f"{case['caseId']} mismatch")
                entry["bite"] = None
                continue
            have = entry.get("bite")
            if isinstance(have, Mapping) and have.get("sha") == loaded.sha8 and have.get("holds"):
                # a run of an earlier call for this very model: a time-boxed build makes progress instead of redoing the same cases
                total, killed = int(have.get("total") or 0), int(have.get("killed") or 0)
                bite_rows.append({"case": case["caseId"], "total": total, "killed": killed, "rate": round(killed / total, 4) if total else None})
                continue
            if time.perf_counter() - started > budget:
                skipped.append(f"{case['caseId']} time")
                continue
            res = mutate.run_mutants(model, case, {"terminal": item.assertion["terminal"], "outcomes": item.assertion["outcomes"]}, cap=cap, index=ix)
            entry["bite"] = {"sha": loaded.sha8, "total": res["total"], "killed": res["killed"], "holds": res["baselineHolds"]}
            bite_rows.append({"case": case["caseId"], "total": res["total"], "killed": res["killed"], "rate": res["rate"]})
    scan = sweep.scan_model(model, index=ix)
    total = sum(r["total"] for r in bite_rows)
    killed = sum(r["killed"] for r in bite_rows)
    return {"status": "ok", "authority": "local", "modelSha8": loaded.sha8,
            "bite": {"cases": len(bite_rows), "mutants": total, "killed": killed, "rate": round(killed / total, 4) if total else None,
                     "zeroKilled": [r["case"] for r in bite_rows if r["total"] and r["killed"] == 0],
                     "weak": [r["case"] for r in bite_rows if r["rate"] is not None and r["rate"] < or_.BITE_WARN_RATE]},
            "guards": {"findings": len(scan["findings"]), "stats": scan["stats"]}, "skipped": skipped,
            "partial": any(s.endswith(" time") for s in skipped), "seconds": round(time.perf_counter() - started, 3)}


# ---------------------------------------------------------------------------------------------------------------
# The provider: sealed runs only (ORACLE_FIRST)
# ---------------------------------------------------------------------------------------------------------------


def sealed_runs(ws: Workspace, project: str, lang: str = "", expose: bool = True) -> dict:
    """Engine results for the SEALED or CONFIRMED items only, for build, the viewer and the documents (shape: oracle-provider.md).

    It is a write call: it seals what can be sealed first, and it records a ``build`` exposure on every item whose trace it returns
    (an edit of such an expectation afterwards makes it ``revised``). A ``revised`` or unregistered item is never included.

    ``expose=False`` seals and returns the same reply but records no exposure: a caller that may still refuse to show the traces (a release
    build that fails a gate) calls ``expose_build`` with the reply at the moment it does show them.
    """
    ws.writable()
    loaded = _load(ws, project, need_contract=True)
    model, paths = loaded.model, loaded.paths
    locale = lang or loaded.lang
    ix = index_model(model)
    cases = {c["caseId"]: c for c in model.get("cases", ())}
    ivs = {i["interventionId"]: i for i in model.get("interventions", ())}
    baselines: dict = {}
    pairs: list = []
    withheld = {"cases": [], "interventions": []}
    with _writing(paths, model) as (data, items, report):
        view = or_.View(data["items"], resolve=lambda its: resolve_confirmations(paths, data)[0]).bind(items)
        states = {key: (item, *view.state_of(item)) for key, item in items.items()}
        tick = data["tick"] + 1
        for cid, case in cases.items():
            key = f"{or_.CASE}:{cid}"
            if not _usable(states, key, sealed_only=True):
                withheld["cases"].append(cid)
                continue
            run = simulate(model, case, None, {"index": ix})
            baselines[cid] = {"state": states[key][1], "run": run}
            if expose:
                _reveal(data["items"][key], "build", tick)
        for iid, iv in ivs.items():
            ikey = f"{or_.INTERVENTION}:{iid}"
            if not _usable(states, ikey, sealed_only=True):
                withheld["interventions"].append(iid)
                continue
            used = False
            for cid in iv.get("appliesTo", ()):
                if cid not in baselines:
                    continue
                result = run_causal(model, cid, [iv], {"index": ix, "locale": locale})
                pairs.append({"interventionId": iid, "caseId": cid, "state": states[ikey][1], "result": result,
                              "chain": causal_chain(model, result, iv, ix, locale)})
                used = True
            if used:
                if expose:
                    _reveal(data["items"][ikey], "build", tick)
            else:
                withheld["interventions"].append(iid)       # sealed, but none of its cases has a sealed baseline: no pair to show
        provenance = {"ledger": "unverified" if (data.get("provenance") != "ok") else "ok", "reason": data.get("reason", "")}
    from .derive import diagrams
    units = diagrams.run_units(model, baselines, pairs, locale)
    return {"schema": "gemarc-atlas-sealed-runs/1", "modelSha8": loaded.sha8, "lang": locale, "baselines": baselines, "pairs": pairs,
            "diagrams": units["units"], "issues": units["issues"], "withheld": withheld, "provenance": provenance}


def expose_build(ws: Workspace, project: str, sealed: Mapping) -> list:
    """Record the ``build`` exposure of every item whose trace the reply ``sealed`` of ``sealed_runs(expose=False)`` embeds (the cases with a
    baseline, the interventions with a pair). What ``sealed_runs`` does by itself for a plain build, done later: a release calls it once every
    gate has passed and the page is about to be swapped into ``dist``, so a refused release exposes nothing. Returns the ledger keys recorded."""
    ws.writable()
    keys = [f"{or_.CASE}:{cid}" for cid in sorted(sealed.get("baselines") or ())]
    keys += [f"{or_.INTERVENTION}:{iid}" for iid in sorted({p["interventionId"] for p in sealed.get("pairs") or ()})]
    if not keys:
        return []
    loaded = _load(ws, project, need_contract=True)
    recorded: list = []
    with _writing(loaded.paths, loaded.model) as (data, _items, _report):
        tick = data["tick"] + 1
        for key in keys:
            entry = data["items"].get(key)
            if isinstance(entry, dict) and isinstance(entry.get("exposed"), dict):
                _reveal(entry, "build", tick)
                recorded.append(key)
    return recorded


# ---------------------------------------------------------------------------------------------------------------
# The oracle sheet (7.6) and the attestation badge
# ---------------------------------------------------------------------------------------------------------------

_TEXT = {
    "en-US": {
        "title": "# Oracle sheet: expectations waiting for the user",
        "how": ["Each line below is something the design is expected to do. Check it against what you want, not against the tool.",
                f"To confirm lines: copy them into atlas/{CONFIRM_NAME}, change `[ ]` to `[x]` and keep the id and the hash.",
                "To confirm everything listed: copy the last line of this sheet (APPROVE-ALL followed by 12 hex characters) into that file.",
                "The tool never writes that file. A later edit of an expectation lapses its confirmation."],
        "counts": "sealed {sealed}, revised {revised}, confirmed {confirmed}; by=user claimed {claimed}, honoured {honoured}",
        "none": "Nothing waits for confirmation.",
        "case": "Case {id} \"{title}\": {facts}", "ends": "{names} should end {state}", "outcome": "step {rule} should end {outcome}",
        "iv": "If {description}: {facts}", "classes": {"NO_DIFFERENCE": "nothing should change", "CONVERGED": "the result should recover to the same outcome",
                                                       "DIVERGED": "the result should differ"},
        "more": "(+{n} more)",
        "badge": {"attested": "Oracle attestation: {sealed} of {total} expectations were sealed before any result was shown, {confirmed} are confirmed by the user, {revised} revised. This is an attestation, not authentication: anyone who can write this project can edit the ledger or the confirmation file.",
                  "unverified-provenance": "UNVERIFIED PROVENANCE: the oracle ledger was edited, deleted, replaced by an older copy or unreadable ({reason}). Sealed and revised counts cannot be trusted; only the user's confirmation counts.",
                  "none": "Oracle attestation: no expectation exists."},
    },
    "zh-CN": {
        "title": "# 期望确认单：等待用户确认的期望",
        "how": ["下面每一行都是设计应当做到的事。请对照你想要的结果核对，不要对照工具的输出。",
                f"确认其中几行：把它们复制进 atlas/{CONFIRM_NAME}，把 `[ ]` 改成 `[x]`，保留编号和哈希。",
                "确认全部列出的内容：把本表最后一行（APPROVE-ALL 加 12 位十六进制摘要）原样写进该文件。",
                "工具从不写这个文件。之后再改某条期望，它的确认就作废。"],
        "counts": "已封存 {sealed}，已修订 {revised}，已确认 {confirmed}；声称 by=user {claimed}，被承认 {honoured}",
        "none": "没有等待确认的期望。",
        "case": "用例 {id}「{title}」：{facts}", "ends": "{names} 应当以 {state} 结束", "outcome": "步骤 {rule} 的结果应当是 {outcome}",
        "iv": "如果{description}：{facts}", "classes": {"NO_DIFFERENCE": "结果不应有变化", "CONVERGED": "结果应当恢复到同样的终局", "DIVERGED": "结果应当不同"},
        "more": "（另有 {n} 项）",
        "badge": {"attested": "期望证明：{total} 条期望中有 {sealed} 条在任何结果展示之前已封存，{confirmed} 条由用户确认，{revised} 条被修订。这是证明而不是认证：任何能写这个项目的人都能改账本或确认文件。",
                  "unverified-provenance": "来源未验证：期望账本被改动、删除、被旧副本替换或无法读取（{reason}）。已封存与已修订的数字不可信，只有用户的确认算数。",
                  "none": "期望证明：没有任何期望。"},
    },
    "ja-JP": {
        "title": "# 期待値確認シート：ユーザーの確認待ち",
        "how": ["以下の各行は、設計が満たすべきことです。ツールの返答ではなく、望む結果と照らして確認してください。",
                f"行ごとに確認する：atlas/{CONFIRM_NAME} に行を書き写し、`[ ]` を `[x]` にして、ID と短い識別子はそのまま残します。",
                "掲載分をすべて確認する：この表の最後の行（APPROVE-ALL と 12 桁の16進ダイジェスト）をそのファイルにそのまま書きます。",
                "ツールはこのファイルを書きません。期待値を後から変更すると、その確認は失効します。"],
        "counts": "封印 {sealed}、修正 {revised}、確認済み {confirmed}；by=user の申告 {claimed}、承認 {honoured}",
        "none": "確認待ちの期待値はありません。",
        "case": "ケース {id}「{title}」：{facts}", "ends": "{names} は {state} で終わるはず", "outcome": "手順 {rule} の結果は {outcome} のはず",
        "iv": "{description}とき：{facts}", "classes": {"NO_DIFFERENCE": "結果は変わらないはず", "CONVERGED": "同じ結果に回復するはず", "DIVERGED": "結果は異なるはず"},
        "more": "（ほか {n} 件）",
        "badge": {"attested": "期待値の証明：{total} 件中 {sealed} 件は結果が示される前に封印され、{confirmed} 件はユーザーが確認し、{revised} 件が修正済みです。これは証明であって認証ではありません。このプロジェクトに書ける人は履歴ファイルも確認ファイルも変更できます。",
                  "unverified-provenance": "出所は未検証：期待値の履歴ファイルが変更、削除、古い写しへの差し替え、または読み取り不能でした（{reason}）。封印と修正の数は信用できず、ユーザーの確認だけが有効です。",
                  "none": "期待値の証明：期待値はありません。"},
    },
}
SHEET_FACTS = 4


def _text(lang: str) -> Mapping:
    return _TEXT.get(lang) or _TEXT["en-US"]


def _name(model_nodes: Mapping, node_id: str) -> str:
    node = model_nodes.get(node_id)
    return str(node.get("name") or node_id) if isinstance(node, Mapping) else node_id


def describe(item: or_.Item, model: Mapping, lang: str) -> str:
    """One natural-language line for an assertion (no engine output: only what the author wrote)."""
    t = _text(lang)
    nodes = {n["id"]: n for n in model.get("nodes", ())}
    if item.kind == or_.CASE:
        case = next((c for c in model.get("cases", ()) if c["caseId"] == item.id), {})
        groups: dict = {}
        for node, state in item.assertion["terminal"].items():
            groups.setdefault(state, []).append(_name(nodes, node))
        facts = [t["ends"].format(names=", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""), state=state) for state, names in groups.items()]
        facts += [t["outcome"].format(rule=o["ruleId"], outcome=o["outcome"]) for o in item.assertion["outcomes"]]
        more = len(facts) - SHEET_FACTS
        shown = "; ".join(facts[:SHEET_FACTS]) + (" " + t["more"].format(n=more) if more > 0 else "")
        return t["case"].format(id=item.id, title=case.get("title", item.id), facts=shown)
    iv = next((i for i in model.get("interventions", ()) if i["interventionId"] == item.id), {})
    facts = []
    if item.assertion.get("class"):
        facts.append(t["classes"].get(item.assertion["class"], item.assertion["class"]))
    groups = {}
    for node, state in item.assertion.get("terminal", {}).items():
        groups.setdefault(state, []).append(_name(nodes, node))
    facts += [t["ends"].format(names=", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""), state=state) for state, names in groups.items()]
    more = len(facts) - SHEET_FACTS
    return t["iv"].format(description=str(iv.get("description") or item.id).rstrip(".。"), facts="; ".join(facts[:SHEET_FACTS]) + (" " + t["more"].format(n=more) if more > 0 else ""))


def counts_of(states: Mapping) -> dict:
    out = {"total": 0, "sealed": 0, "revised": 0, "confirmed": 0, "unregistered": 0, "claimed": 0, "honoured": 0}
    for item, state, _why in states.values():
        out["total"] += 1
        out[state] += 1
        if item.by == "user":
            out["claimed"] += 1
            if state == "confirmed":
                out["honoured"] += 1
    return out


def attestation(lang: str, counts: Mapping, view: or_.View) -> dict:
    """The badge: class ``attested`` / ``unverified-provenance`` / ``none`` and its text in the project language."""
    t = _text(lang)["badge"]
    if view.tamper or view.provenance != "ok":
        klass = "unverified-provenance"
    elif counts["total"] == 0:
        klass = "none"
    else:
        klass = "attested"
    reason = view.tamper or view.reason or "unverified"
    return {"class": klass, "badge": t[klass].format(sealed=counts["sealed"] + counts["confirmed"], total=counts["total"], confirmed=counts["confirmed"],
                                                      revised=counts["revised"], reason=reason)}


def sheet_text(model: Mapping, states: Mapping, view: or_.View, lang: str, digest: str) -> str:
    """The oracle sheet: one line per item that is not confirmed, a counts line, the badge and the ``APPROVE-ALL`` line."""
    t = _text(lang)
    counts = counts_of(states)
    lines = [t["title"], ""] + [f"- {x}" for x in t["how"]] + ["", t["counts"].format(**counts), "", attestation(lang, counts, view)["badge"], ""]
    open_items = [(item, state) for item, state, _why in states.values() if state in ("sealed", "revised")]
    if not open_items:
        lines.append(t["none"])
    for item, state in open_items:
        lines.append(f"- [ ] {item.id} {item.ah8} {state} | {describe(item, model, lang)}")
    lines += ["", f"APPROVE-ALL {digest}", ""]
    return "\n".join(lines)


def write_sheet(ws: Workspace, project: str, lang: str = "") -> dict:
    """Seal what can be sealed, write ``atlas/dist/oracle-sheet.md`` and record what it issued (a ticked line can only approve a
    hash the MCP printed). Returns ``{path, bytes, lines, digest}``. The confirmation file itself is never written."""
    ws.writable()
    loaded = _load(ws, project, need_contract=False)
    model, paths = loaded.model, loaded.paths
    use_lang = lang or loaded.lang
    with _writing(paths, model) as (data, items, _report):
        view = or_.View(data["items"], resolve=lambda its: resolve_confirmations(paths, data)[0], tamper=data.get("reason") or "",
                        provenance=data.get("provenance", "ok")).bind(items)
        states = {key: (item, *view.state_of(item)) for key, item in items.items()}
        digest = ledger_digest(data)
        text = sheet_text(model, states, view, use_lang, digest)
        issued = data["issued"]
        for key, (item, state, _why) in states.items():
            if state in ("sealed", "revised"):
                bucket = issued["items"].setdefault(key, [])
                if item.ah8 not in bucket:
                    bucket.append(item.ah8)
                    del bucket[:-4]
        if digest not in issued["digests"]:
            issued["digests"].append(digest)
            del issued["digests"][:-ISSUED_KEEP]
    out_dir = paths.atlas / SHEET_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8")
    sl.atomic_write(out_dir / SHEET_NAME, raw)
    return {"path": str(out_dir / SHEET_NAME), "bytes": len(raw), "lines": text.count("\n"), "digest": digest}


# ---------------------------------------------------------------------------------------------------------------
# The release gate (oracle part of design 7.6)
# ---------------------------------------------------------------------------------------------------------------


def release_gate(ws: Workspace, project: str) -> dict:
    """The oracle blockers of ``build --release``: ``{ok, blockers: [{kind, id, rule, message}], counts, attestation}``.

    Blocker kinds: ``unsealed`` (OR-000: not registered, or edited and not yet re-registered), ``revised`` (OR-005: needs the
    user's confirmation), ``no-verdict`` (no verdict for this very model yet: the gate does not run the engine, call
    ``run_verdicts`` first), ``mismatch`` (OR-001 / IV-002: the last verdict for this very model failed), ``provenance`` (the
    ledger was edited, deleted, rolled back or unreadable: only the user's confirmation of every item clears it), ``vacuous``
    (OR-003 coverage floor, a case without events, or OR-004 with a mutant run of this very model that killed nothing or found
    nothing to mutate: a sealed expectation that constrains nothing), ``no-bite`` (a case whose assertions held has no mutant run
    for this very model, so OR-004 cannot be judged; ``bite_run`` stores one). The other parts of 7.6 (EV-003 PASS of each
    Implemented claim, no NOT-AUTHORED marker, the browser gate) belong to the build. Read-only: it writes nothing."""
    paths = pj.project_paths(ws, project)
    cfg = pj.load_config(paths)
    snap = status_mod.load_snapshot(paths, cfg, mode="slice")
    model = snap.model
    sha8 = am.model_sha8(model)
    view = make_view(paths)
    items = or_.collect_items(model)
    view.bind(items)
    states = {key: (item, *view.state_of(item)) for key, item in items.items()}
    blockers: list = []
    cases = {c.get("caseId"): c for c in model.get("cases", ())}
    for key, (item, state, why) in states.items():
        entry = view.entries.get(key) or {}
        if state == "unregistered":
            blockers.append({"kind": "unsealed", "id": item.id, "rule": "OR-000", "message": why or "not registered"})
            continue
        if item.kind == or_.CASE:
            gap = or_.coverage_gap(cases.get(item.id, {}))
            if gap is not None:                        # OR-003 (wave 2, also in the default full check): the gate enforces it on its own
                blockers.append({"kind": "vacuous", "id": item.id, "rule": "OR-003", "message": or_.gap_text(gap)})
            # OR-004 needs a mutant run of THIS model: without one the gate cannot tell a vacuous assertion from a real one
            bite = entry.get("bite")
            bite_now = isinstance(bite, Mapping) and bite.get("sha") == sha8
            last_case = entry.get("last")
            failing_now = isinstance(last_case, Mapping) and last_case.get("sha") == sha8 and last_case.get("pass") is False
            if bite_now and bite.get("holds"):
                if not bite.get("total"):
                    blockers.append({"kind": "vacuous", "id": item.id, "rule": "OR-004",
                                     "message": "no mutant could be built: the case has no event, so its expectations constrain nothing"})
                elif not bite.get("killed"):
                    blockers.append({"kind": "vacuous", "id": item.id, "rule": "OR-004",
                                     "message": f"0 of {bite['total']} mutants killed: the assertions constrain nothing the case exercises"})
            elif not bite_now and not failing_now:
                blockers.append({"kind": "no-bite", "id": item.id, "rule": "OR-004",
                                 "message": "no mutant run for this model yet: run the bite (simulate --bite; the release build does)"})
        if state == "revised":
            blockers.append({"kind": "revised", "id": item.id, "rule": "OR-005", "message": why or "revised: needs the user's confirmation"})
        last = entry.get("last")
        fresh = isinstance(last, Mapping) and last.get("sha") == sha8
        if item.kind == or_.INTERVENTION:
            wanted = {c for c in next((i.get("appliesTo", ()) for i in model.get("interventions", ()) if i.get("interventionId") == item.id), ())
                      if c in {x.get("caseId") for x in model.get("cases", ())}}
            fresh = fresh and wanted <= {c for c, p in (entry.get("pairs") or {}).items() if isinstance(p, Mapping) and p.get("sha") == sha8}
        if not fresh:
            blockers.append({"kind": "no-verdict", "id": item.id, "rule": "OR-001" if item.kind == or_.CASE else "IV-002",
                             "message": "no verdict for this model yet: run simulate (the release build does)"})
        elif last.get("pass") is False:
            blockers.append({"kind": "mismatch", "id": item.id, "rule": "OR-001" if item.kind == or_.CASE else "IV-002",
                             "message": "the last verdict for this model failed"})
    counts = counts_of(states)
    lang = cfg.languages[0] if cfg.languages else "en-US"
    badge = attestation(lang, counts, view)
    if badge["class"] == "unverified-provenance" and counts["total"] and counts["confirmed"] < counts["total"]:
        blockers.append({"kind": "provenance", "id": "", "rule": "OR-005",
                         "message": f"the oracle ledger is unverified ({view.tamper or view.reason or view.provenance}): the user must confirm every expectation"})
    return {"ok": not blockers, "blockers": blockers, "counts": counts, "attestation": badge, "modelSha8": sha8}
