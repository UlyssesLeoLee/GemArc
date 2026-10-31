"""The one-time self-review (design addendum A1, D15): a mechanical report, a deterministic spot-check sample, one record.

``build_report(ctx)`` is pure: from the expanded model (plus what the project directory can add: the provenance map, the saved
requirement texts, the evidence stamp, the oracle ledger and the review record) it computes ranked findings and a sample of
K = 8 rows to check against the real source. ``review_report(ws, project)`` and ``review_record(ws, project, notes)`` load a
project and answer in the ``atlas.tools`` envelope; the report never writes, the record needs ``GEMARC_ALLOW_WRITE=1``.

Findings are ranked ``BLOCK`` (a fact that is probably false or a slot that prints garbage), ``GAP`` (coverage or honesty is
missing) and ``INFO`` (hints; the consistency heuristics are never above INFO). Ids ``RV-<area><n>``:

====== ==== ============================================================================================================
RV-C1  GAP  requirement names no node          RV-C5  GAP   node without any edge
RV-C2  GAP  requirement has no case            RV-C6  GAP   case without expectation
RV-C3  GAP  requirement whose cases assert     RV-C7  GAP   unreachable machine state
            nothing                            RV-E1  BLOCK Implemented claim without a source-code evidence
RV-C4  INFO node in no requirement             RV-E2  INFO  edges that inherit their evidence (ev=^)
RV-E3  GAP  Inferred share above 30%           RV-E4  GAP   (as-is) anchors not verified; INFO when no stamp exists yet
RV-E5  BLOCK (to-be) user: quote not in the saved text (or GAP: no saved text)
RV-H1  GAP  policy by=ai                       RV-H2  BLOCK NOT-AUTHORED slot or marker
RV-H3  GAP  TBD without a user decision        RV-H4  BLOCK/GAP requirement without source (BLOCK in to-be)
RV-H5  BLOCK (to-be) requirement source quote not in the saved text
RV-K1  INFO near-duplicate node names          RV-K2  INFO  empty room or floor
RV-K3  GAP  node without a defined room        RV-K4  INFO  kind/relation sanity
RV-O1  GAP  oracle: expectations unconfirmed
====== ==== ============================================================================================================

The record (``.atlas/review.json``) is **one pass**. ``record`` is refused unless the project is at phase ``review`` (every other
condition of ``done`` holds) and no valid record exists yet; after it the report keeps working. ``status`` allows ``done`` for the
recorded model, and for a changed model through ``accepts_changed_model``: the changed model is accepted only when every BLOCK
finding that the *model alone* can show (RV-E1, RV-H2, RV-H4; the checks that read files are marked ``fileDependent`` and are
judged at record time only) was already in the record, so no new BLOCK finding appeared. ``check full`` without ERROR, no red
draft, no completeness BLOCK and the other gates stay conditions of ``done`` in ``status.evaluate`` itself.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..paths import GemArcError, Workspace
from . import model as am
from . import project as pj
from . import slices as sl
from . import status as status_mod
from . import tools as core
from .rules.completeness import completeness_report

SCHEMA = "gemarc-atlas-review/1"
REPORT_CAP = 4500
SAMPLE_K = 8
SAMPLE_QUOTAS = (("evidence", 2), ("edge", 2), ("requirement", 1), ("policy", 1), ("node", 1), ("case", 1))
SAMPLE_FALLBACK = ()
MIN_QUOTE_CHARS = 16        # a quotation shorter than this (all pieces together) proves nothing
ID_CLIP = 64
SHOW_IDS = 3
INFERRED_SHARE_LIMIT = 0.30
NOTES_MAX_BYTES = 4096
RECORD_FINDINGS_MAX = 60
BLOCK_KEYS_MAX = 400
DATA_KINDS = frozenset({"Storage", "Table", "Entity"})
ACTIVE_RELATIONS = frozenset({"calls", "sends", "publishes", "triggers"})
DATA_RELATIONS = frozenset({"writes", "reads", "owns", "implements"})
SEVERITIES = ("BLOCK", "GAP", "INFO")

E_REVIEW_ARG = "GEMARC_ATLAS_REVIEW_001"
E_REVIEW_ONCE = "GEMARC_ATLAS_REVIEW_002"
E_REVIEW_PHASE = "GEMARC_ATLAS_REVIEW_003"
AUTHORITY = "local"

_FIXED_RE = re.compile(r"RV-[A-Z][0-9]+")
_MARKER = "[NOT-AUTHORED"
_QUOTE_CHARS = "\"'`“”‘’「」『』"


# ---------------------------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Finding:
    """One ranked finding; ``refs`` lists every affected id (the report shows the first three)."""

    id: str
    sev: str
    fix: str
    refs: list = field(default_factory=list)
    file_dependent: bool = False
    count: int = -1             # the number to report when it is not ``len(refs)`` (a share, a count of rows)

    @property
    def n(self) -> int:
        return len(self.refs) if self.count < 0 else self.count

    def keys(self) -> list:
        return [f"{self.id}:{r}" for r in self.refs]

    def shown(self, limit: int = SHOW_IDS) -> dict:
        out = {"id": self.id, "sev": self.sev, "n": self.n}
        if limit and self.refs and self.count < 0:
            out["ids"] = [_clip(r, ID_CLIP) for r in self.refs[:limit]]
        out["fix"] = self.fix
        return out


@dataclass
class Context:
    """Everything the report reads. ``user_texts`` / ``stamp`` / ``oracle`` / ``record`` are ``None`` when the source is absent;
    ``user_texts=None`` also means "do not run the checks that need the saved requirement text" (the model-only mode)."""

    model: Mapping
    prov: Mapping = field(default_factory=dict)
    user_texts: "str | None" = None
    stamp: "set | None" = None
    oracle: "Mapping | None" = None
    record: "Mapping | None" = None
    mode: str = ""
    record_view: dict = field(default_factory=lambda: {"state": "none"})
    vanished_tbds: tuple = ()

    def __post_init__(self):
        if not self.mode:
            self.mode = (self.model.get("meta") or {}).get("mode") or "asis"


def _clean(text) -> str:
    """Author text made safe to print into a report that the AI reads as data: control, format, line and paragraph separator
    characters (ESC, NUL, bidi and zero-width marks) become blanks, and the characters that start Markdown structure are
    replaced by plain ones, so a stored sentence cannot pose as an instruction or a heading."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text))
    text = text.replace("`", "'").replace("[", "(").replace("]", ")")
    return " ".join(text.split()).lstrip("#>-*")


def _clip(text, limit: int) -> str:
    """One line of at most ``limit`` UTF-8 bytes (cleaned, see ``_clean``)."""
    text = _clean(text)
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[: max(0, limit - 3)].decode("utf-8", "ignore") + "..."


def _by_id(records, key: str = "id") -> dict:
    return {r.get(key): r for r in records if isinstance(r, Mapping) and isinstance(r.get(key), str)}


def _has_expectation(case: Mapping) -> bool:
    return bool(case.get("expectedTerminalState") or case.get("expectedOutcomes"))


# ---- coverage ------------------------------------------------------------------------------------------------


def _coverage(model: Mapping, out: list) -> None:
    reqs, cases = list(model.get("requirements", ())), list(model.get("cases", ()))
    nodes, edges = list(model.get("nodes", ())), list(model.get("edges", ()))
    traced: dict = {}
    for c in cases:
        for rid in c.get("requirementIds", ()):
            traced.setdefault(rid, []).append(c)
    no_node = [r["id"] for r in reqs if not r.get("nodeIds")]
    no_case = [r["id"] for r in reqs if r["id"] not in traced]
    no_expect = [r["id"] for r in reqs if r["id"] in traced and not any(_has_expectation(c) for c in traced[r["id"]])]
    if no_node:
        out.append(Finding("RV-C1", "GAP", "requirements name no node: add nodes= to the requirement row", no_node))
    if no_case:
        out.append(Finding("RV-C2", "GAP", "requirements have no case: add a case with req=<id> (20-scenarios)", no_case))
    if no_expect:
        out.append(Finding("RV-C3", "GAP", "requirements whose cases assert nothing: add expect rows to the case", no_expect))
    machine_ids = {m.get("id") for m in model.get("machines", ())}
    degree: dict = {}
    for e in edges:
        for end in (e.get("sourceId"), e.get("targetId")):
            degree[end] = degree.get(end, 0) + 1
    isolated = [n["id"] for n in nodes if not degree.get(n["id"]) and n["id"] not in machine_ids]
    if isolated:
        out.append(Finding("RV-C5", "GAP", "nodes without any edge: add the missing edge or remove the node", isolated))
    if reqs:
        named = {x for r in reqs for x in r.get("nodeIds", ())}
        free = [n["id"] for n in nodes if n["id"] not in named and n["id"] not in machine_ids]
        if free:
            out.append(Finding("RV-C4", "INFO", "nodes in no requirement: list them under nodes= of a requirement or drop them", free))
    bare = [c["caseId"] for c in cases if not _has_expectation(c)]
    if bare:
        out.append(Finding("RV-C6", "GAP", "cases without expectation: add an expect row (state your intent first)", bare))
    unreachable = []
    for m in model.get("machines", ()):
        seen, todo = {m.get("initial")}, [m.get("initial")]
        transitions = [t for t in m.get("transitions", ()) if isinstance(t, Mapping)]
        while todo:
            here = todo.pop()
            for t in transitions:
                if t.get("from") == here and t.get("to") not in seen:
                    seen.add(t.get("to"))
                    todo.append(t.get("to"))
        unreachable.extend(f"{m.get('id')}:{s}" for s in m.get("states", ()) if s not in seen)
    if unreachable:
        out.append(Finding("RV-C7", "GAP", "unreachable machine states: add a transition into the state or remove it", unreachable))


# ---- evidence honesty ----------------------------------------------------------------------------------------


def _implemented_without_code(model: Mapping) -> list:
    kinds = {e.get("evidenceId"): e.get("sourceKind") for e in model.get("evidences", ())}
    bad = []
    for n in model.get("nodes", ()):
        if n.get("verification") == "Implemented" and not any(kinds.get(x) == "source-code" for x in n.get("evidenceIds", ())):
            bad.append(n["id"])
    for e in model.get("edges", ()):
        if e.get("verification") == "Implemented" and not any(kinds.get(x) == "source-code" for x in e.get("evidenceIds", ())):
            bad.append(e["id"])
    return bad


def _inherited_edges(model: Mapping, prov: Mapping) -> list:
    return [e["id"] for e in model.get("edges", ()) if f"edges[{e['id']}].evidenceIds" in prov]


def _inferred_share(model: Mapping) -> tuple:
    claims = list(model.get("nodes", ())) + list(model.get("edges", ()))
    inferred = sum(1 for c in claims if c.get("verification") == "Inferred")
    return inferred, len(claims)


def _norm_quote(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _quote_segments(quote: str) -> list:
    """The pieces of a quotation that must appear verbatim in the saved text (``...`` splits, wrapping quotes and ``user:`` go)."""
    text = str(quote).strip()
    if text.lower().startswith("user:"):
        text = text[5:]
    text = text.strip().strip(_QUOTE_CHARS).strip()
    pieces = re.split(r"…|\.{3}", text)
    return [_norm_quote(p.strip().strip(_QUOTE_CHARS)) for p in pieces if len(p.strip().strip(_QUOTE_CHARS)) >= 4]


def quote_in_text(quote: str, haystack: str) -> bool:
    """True when every piece of ``quote`` (at least 4 characters each) occurs in ``haystack`` ignoring case and white space."""
    segments = _quote_segments(quote)
    return bool(segments) and sum(len(x) for x in segments) >= MIN_QUOTE_CHARS and all(s in haystack for s in segments)


def _evidence(ctx: Context, out: list, stats: dict) -> None:
    model, mode = ctx.model, ctx.mode
    bad = _implemented_without_code(model)
    if bad:
        out.append(Finding("RV-E1", "BLOCK", "Implemented without a source-code evidence: add a code evidence or write verif=Designed/Inferred", bad))
    inherited = _inherited_edges(model, ctx.prov)
    stats["inheritedEdges"] = len(inherited)
    if inherited:
        out.append(Finding("RV-E2", "INFO", "edges inherit the evidence of their ends (ev=^, at most Designed): give code facts their own ev=", inherited))
    inferred, total = _inferred_share(model)
    stats["inferred"] = f"{inferred}/{total}"
    if total and inferred >= 3 and inferred / total > INFERRED_SHARE_LIMIT:
        out.append(Finding("RV-E3", "GAP", f"more than {int(INFERRED_SHARE_LIMIT * 100)}% of the claims are Inferred: confirm them in the code or mark TBD",
                           [f"{inferred}/{total}"], count=inferred))
    evidences = list(model.get("evidences", ()))
    if mode == "asis":
        checkable = [e["evidenceId"] for e in evidences if e.get("sourceKind") != "user-requirement"]
        if ctx.stamp is None:
            stats["anchors"] = "not verified yet"
            if checkable:
                out.append(Finding("RV-E4", "INFO", "anchors not verified yet (no evidence stamp, no verifier tool here): open the sampled anchors yourself before trusting claims",
                                   [f"{len(checkable)} evidence rows"], count=len(checkable)))
        else:
            unverified = [i for i in checkable if i not in ctx.stamp]
            stats["anchors"] = f"{len(checkable) - len(unverified)}/{len(checkable)} stamped"
            if unverified:
                out.append(Finding("RV-E4", "GAP", "anchors without a verified stamp: check them against the files and repair the ones that do not match", unverified))
    else:
        quotes = [e for e in evidences if e.get("sourceKind") == "user-requirement"]
        if quotes and ctx.user_texts:
            stats["userQuotes"] = len(quotes)
        if quotes and ctx.user_texts is not None:
            if not ctx.user_texts:
                out.append(Finding("RV-E5", "GAP", "no requirement text saved under atlas/user/: save it verbatim so quotes can be checked", [e["evidenceId"] for e in quotes]))
            else:
                missing = [e["evidenceId"] for e in quotes if not quote_in_text(e.get("anchor", ""), ctx.user_texts)]
                if missing:
                    out.append(Finding("RV-E5", "BLOCK", "user: quote not found in the saved requirement text: quote the user's words or drop the claim",
                                       missing, file_dependent=True))


# ---- hallucination surface -----------------------------------------------------------------------------------


def _markers(model: Mapping, prov: Mapping) -> list:
    refs = []
    for slot in completeness_report(model, prov).blockers():
        refs.append(f"{slot.slot}@{slot.ref}" if slot.ref else slot.slot)
    for coll, key in (("nodes", "id"), ("edges", "id"), ("requirements", "id"), ("policies", "id"), ("cases", "caseId"), ("tbds", "id")):
        for rec in model.get(coll, ()):
            if isinstance(rec, Mapping) and _MARKER in json.dumps(rec, ensure_ascii=False):
                refs.append(f"marker@{rec.get(key)}")
    return refs


def _hallucination(ctx: Context, out: list, stats: dict) -> None:
    model, mode = ctx.model, ctx.mode
    ai = [p["id"] for p in model.get("policies", ()) if p.get("by", "ai") == "ai"]
    stats["policiesByAi"] = f"{len(ai)}/{len(model.get('policies', ()))}"
    if ai:
        out.append(Finding("RV-H1", "GAP", "policies written by the AI: ask the user to confirm each one (do not relabel them by=user yourself)", ai))
    haystack = ctx.user_texts or ""
    unbacked = [p["id"] for p in model.get("policies", ()) if p.get("by") == "user" and not status_mod.quote_backed(p.get("text", ""), haystack)]
    if unbacked and ctx.user_texts is not None:
        out.append(Finding("RV-H6", "GAP", "policies by=user whose text quotes nothing the user said (atlas/user/*.txt): save the user's answer verbatim and quote it",
                           unbacked, file_dependent=True))
    markers = _markers(model, ctx.prov)
    if markers:
        out.append(Finding("RV-H2", "BLOCK", "slots that print [NOT-AUTHORED]: author the named fact", markers))
    decided = status_mod.decided_tbds(model, ctx.user_texts or "")
    open_tbds = [t["id"] for t in model.get("tbds", ()) if t.get("id") not in decided]
    if open_tbds:
        out.append(Finding("RV-H3", "GAP", "TBDs without a user decision: ask the user, save the answer, then a policies row by=user src=<tbd id> quoting it", open_tbds))
    if ctx.vanished_tbds:
        out.append(Finding("RV-H7", "BLOCK", "a TBD that existed earlier is gone without a user decision: restore it, or ask the user to void it",
                           list(ctx.vanished_tbds), file_dependent=True))
    reqs = list(model.get("requirements", ()))
    nosrc = [r["id"] for r in reqs if not str(r.get("source", "")).strip()]
    if nosrc:
        out.append(Finding("RV-H4", "BLOCK" if mode == "tobe" else "GAP", "requirements without a source: write the user's quoted sentence (to-be) or the document", nosrc))
    if mode == "tobe" and ctx.user_texts and reqs:
        lost = [r["id"] for r in reqs if str(r.get("source", "")).strip() and not quote_in_text(r["source"], ctx.user_texts)]
        if lost:
            out.append(Finding("RV-H5", "BLOCK", "requirement source is not a quote of the saved text: quote the user verbatim", lost, file_dependent=True))


# ---- consistency heuristics (never above INFO, except the room which the completeness report already blocks) ----------


def _name_key(name: str) -> str:
    return re.sub(r"[\W_]+", "", str(name).casefold())


def _digits_only_differ(a: str, b: str) -> bool:
    """Different strings that are the same once every digit run is a placeholder ("service 12" and "service 13")."""
    return a != b and re.sub(r"\d+", "#", a) == re.sub(r"\d+", "#", b)


def near_duplicates(nodes: Sequence[Mapping], limit_bucket: int = 24) -> list:
    """Pairs ``a~b`` of node ids whose names normalise to the same key or one edit apart (names of 6+ characters, 3+ when
    they are not ASCII); names that differ only inside digit runs are different on purpose and never reported."""
    entries = []
    for n in nodes:
        key = _name_key(n.get("name", ""))
        floor = 6 if key.isascii() else 3
        if len(key) >= floor:
            entries.append((n["id"], key))
    pairs, seen = [], set()
    index: dict = {}
    for ident, key in entries:
        for variant in {key, *(key[:i] + key[i + 1:] for i in range(len(key)))}:
            bucket = index.setdefault(variant, [])
            if len(bucket) < limit_bucket:
                bucket.append((ident, key))
    names = dict(entries)
    for bucket in index.values():
        for i, (a, ka) in enumerate(bucket):
            for b, kb in bucket[i + 1:]:
                if a == b or (a, b) in seen:
                    continue
                seen.add((a, b))
                if abs(len(ka) - len(kb)) <= 1 and not _digits_only_differ(ka, kb):
                    pairs.append((a, b))
    order = {ident: i for i, (ident, _k) in enumerate(entries)}
    pairs.sort(key=lambda p: (order[p[0]], order[p[1]]))
    return [f"{a}~{b}" for a, b in pairs if names[a] and names[b]]


def _consistency(model: Mapping, out: list) -> None:
    nodes, edges = list(model.get("nodes", ())), list(model.get("edges", ()))
    dups = near_duplicates(nodes)
    if dups:
        out.append(Finding("RV-K1", "INFO", "near-duplicate names (hint): merge the nodes or make the names say how they differ", dups))
    space = model.get("space") or {}
    rooms, floors = list(space.get("rooms", ())), list(space.get("floors", ()))
    used = {n.get("roomId") for n in nodes}
    empty = [r["id"] for r in rooms if r["id"] not in used]
    floor_ids_with_rooms = {r.get("floorId") for r in rooms}
    empty += [f["id"] for f in floors if f["id"] not in floor_ids_with_rooms]
    if empty:
        out.append(Finding("RV-K2", "INFO", "empty rooms or floors (hint): place nodes there or remove the row", empty))
    room_ids = {r["id"] for r in rooms}
    unplaced = [n["id"] for n in nodes if n.get("roomId") not in room_ids]
    if unplaced:
        out.append(Finding("RV-K3", "GAP", "nodes without a defined room: room= must name a room of 00-space", unplaced))
    kind_of = {n["id"]: n.get("kind") for n in nodes}
    odd = []
    for e in edges:
        src, tgt, rel = kind_of.get(e.get("sourceId")), kind_of.get(e.get("targetId")), e.get("relation")
        if (src in DATA_KINDS and rel in ACTIVE_RELATIONS) or (tgt == "Actor" and rel in DATA_RELATIONS):
            odd.append(e["id"])
    if odd:
        out.append(Finding("RV-K4", "INFO", "kind/relation sanity (hint): a store that originates calls or an actor that is written to; check the direction", odd))


# ---- oracle ---------------------------------------------------------------------------------------------------


def _oracle(ctx: Context, out: list):
    if ctx.oracle is None:
        return "not available yet"
    summary = status_mod.oracle_summary(ctx.oracle)
    info = {k: summary[k] for k in ("total", "sealed", "revised", "confirmed", "unconfirmed")}
    pool = ctx.oracle.get("summary") if isinstance(ctx.oracle.get("summary"), Mapping) else ctx.oracle
    for key in ("blindMismatch", "bite"):
        if key in pool and isinstance(pool[key], (int, float, str, bool)):
            info[key] = pool[key]
    if summary["unconfirmed"]:
        out.append(Finding("RV-O1", "GAP", "expectations the user has not confirmed: send the oracle sheet to the user", [f"{summary['unconfirmed']} expectations"], count=summary["unconfirmed"]))
    return info


def collect(ctx: Context) -> tuple:
    """``(findings, stats, oracle)``: findings ranked BLOCK, GAP, INFO, then by id."""
    out: list = []
    stats: dict = {}
    model = ctx.model
    stats["n"] = {"nodes": len(model.get("nodes", ())), "edges": len(model.get("edges", ())), "reqs": len(model.get("requirements", ())),
                  "cases": len(model.get("cases", ())), "evid": len(model.get("evidences", ()))}
    _coverage(model, out)
    _evidence(ctx, out, stats)
    _hallucination(ctx, out, stats)
    _consistency(model, out)
    oracle = _oracle(ctx, out)
    out.sort(key=lambda f: (SEVERITIES.index(f.sev), f.id))
    return out, stats, oracle


# ---------------------------------------------------------------------------------------------------------------
# Block keys and the stale rule (read by status through ``accepts_changed_model``)
# ---------------------------------------------------------------------------------------------------------------


def block_keys(model: Mapping, prov: "Mapping | None" = None, user_texts: "str | None" = None, vanished: Sequence[str] = ()) -> set:
    """Stable keys ``RV-xx:<ref>`` of the BLOCK findings. Without ``user_texts`` only the model-only findings count; with the
    saved requirement text (and the vanished TBD ids) the file-reading checks count as well."""
    findings, _stats, _oracle_state = collect(Context(model, prov or {}, user_texts=user_texts, vanished_tbds=tuple(vanished)))
    return {key for f in findings if f.sev == "BLOCK" and (user_texts is not None or not f.file_dependent) for key in f.keys()}


def _current_block_keys(snap: "status_mod.Snapshot") -> set:
    paths = getattr(snap, "paths", None)
    if paths is None:  # a bare (model, provenance) pair: only the model-only findings can be judged
        return block_keys(snap.model, snap.prov)
    return block_keys(snap.model, snap.prov, _user_text(paths), status_mod.vanished_tbds(snap))


def valid_record(record: "Mapping | None") -> bool:
    """A record that names a model: ``modelSha`` of 8 or 64 hex digits."""
    if not isinstance(record, Mapping):
        return False
    sha = record.get("modelSha")
    return isinstance(sha, str) and len(sha) in (8, 64) and re.fullmatch(r"[0-9a-f]+", sha) is not None


def accepts_changed_model(snap: "status_mod.Snapshot", record: "Mapping | None") -> bool:
    """The stale rule: a record made for another version of the model still counts when no new BLOCK finding appeared.

    ``False`` for a record without ``blockKeys`` (a record of another writer is judged by the sha only). Exceptions are not
    swallowed: a broken rule must show up in the tests, ``status`` treats an unreadable record as missing.
    """
    if not valid_record(record) or not isinstance(record.get("blockKeys"), list):
        return False
    known = {k for k in record["blockKeys"] if isinstance(k, str)}
    return not (_current_block_keys(snap) - known)


def record_state(snap: "status_mod.Snapshot", record: "Mapping | None") -> dict:
    """``{"state": none|current|accepted|stale, ...}`` for the report."""
    if not record:
        return {"state": "none"}
    if not valid_record(record):
        return {"state": "none", "note": "review.json is not a valid record"}
    sha = record["modelSha"]
    if am.model_sha(snap.model).startswith(sha):
        return {"state": "current", "sha": sha[:8]}
    known = {k for k in record.get("blockKeys", ()) if isinstance(k, str)} if isinstance(record.get("blockKeys"), list) else None
    if known is None:
        return {"state": "stale", "sha": sha[:8], "note": "the record has no blockKeys: only an unchanged model counts"}
    new = sorted(_current_block_keys(snap) - known)
    if not new:
        return {"state": "accepted", "sha": sha[:8], "note": "model changed after the record; every BLOCK finding now was already open at the review"}
    return {"state": "stale", "sha": sha[:8], "newBlock": new[:SHOW_IDS], "note": "fix the new BLOCK findings (put mode=patch); no second review"}


# ---------------------------------------------------------------------------------------------------------------
# The sample
# ---------------------------------------------------------------------------------------------------------------

_KIND_CODE = {"evidence": "ev", "edge": "ed", "requirement": "rq", "policy": "pl", "node": "no", "case": "ca"}
_RISKY_LEVELS = frozenset({"Implemented", "Inferred"})
COMPARE = {
    "evidence": "open the file at `vs`: the anchor text must be there and prove the topic",
    "edge": "open the code or text of the source node: is there really such a call or data flow to the target",
    "requirement": "to-be: find the quote in atlas/user/*.txt; as-is: the cited source must say it",
    "policy": "the requirement or TBD in `vs`: did the user state this, or did the AI invent it",
    "node": "open the evidence files of the node: does the code or text show this component and its role",
    "case": "does the requirement text describe this scenario and this expected outcome",
}


def _rank(seed: str, kind: str, ident: str) -> str:
    return hashlib.sha256(f"{seed}|{kind}|{ident}".encode("utf-8")).hexdigest()


def _seed(model: Mapping, pools: Mapping) -> str:
    """The sample seed: the project id and the sorted set of row ids, never the content of a row. Editing an unrelated row
    cannot re-roll the sample (that made it gameable); adding or removing a row does."""
    ids = sorted(f"{kind}|{row[0]}" for kind, rows in pools.items() for row in rows)
    project = str((model.get("meta") or {}).get("projectId", ""))
    return hashlib.sha256((project + "\n" + "\n".join(ids)).encode("utf-8")).hexdigest()


def _pool(model: Mapping, prov: Mapping) -> dict:
    """``{kind: [(id, prov key, claim, vs)]}`` in model order."""
    out: dict = {kind: [] for kind in _KIND_CODE}
    for e in model.get("evidences", ()):
        where = e.get("filePath", "")
        repo = e.get("repository")
        out["evidence"].append((e["evidenceId"], f"evidences[{e['evidenceId']}]",
                                f"\"{_clip(e.get('anchor', ''), 44)}\" proves: {_clip(e.get('topic', ''), 40)}",
                                (f"{repo}:" if repo else "") + _clip(where, 50), 0 if e.get("verification") == "Implemented" else 1))
    for e in model.get("edges", ()):
        ev = ",".join(e.get("evidenceIds", ())[:3])
        label = f" ({_clip(e['label'], 30)})" if e.get("label") else ""
        out["edge"].append((e["id"], f"edges[{e['id']}]", f"{e.get('sourceId')} {e.get('relation')} {e.get('targetId')}{label}",
                            f"ev {ev}" if ev else "no evidence", 0 if e.get("verification") in _RISKY_LEVELS else 1))
    for r in model.get("requirements", ()):
        out["requirement"].append((r["id"], f"requirements[{r['id']}]", _clip(r.get("title", ""), 56), f"source: {_clip(r.get('source', ''), 40)}", 1))
    for p in model.get("policies", ()):
        out["policy"].append((p["id"], f"policies[{p['id']}]", f"({p.get('by', 'ai')}) {_clip(p.get('text', ''), 60)}", f"src {p.get('src')}",
                              0 if p.get("by", "ai") == "ai" else 1))
    for n in model.get("nodes", ()):
        ev = ",".join(n.get("evidenceIds", ())[:3])
        out["node"].append((n["id"], f"nodes[{n['id']}]", f"{n.get('kind')}: {_clip(n.get('sub', ''), 60)}", f"ev {ev}" if ev else "no evidence",
                            0 if n.get("verification") in _RISKY_LEVELS else 1))
    for c in model.get("cases", ()):
        out["case"].append((c["caseId"], f"cases[{c['caseId']}]", _clip(c.get("title", ""), 60), "req " + ",".join(c.get("requirementIds", ())[:3]), 1))
    return out


def _file_line(at: str):
    name, _, line = at.rpartition(":")
    return (name, int(line)) if line.isdigit() else (at, 0)


def sample_rows(model: Mapping, prov: Mapping, k: int = SAMPLE_K) -> list:
    """The deterministic spot-check sample: ``k`` rows, each with the ``file:line`` of its row.

    Quotas (evidence 2, edge 2, requirement 1, policy 1, node 1, case 1) are filled first; a short pool gives its places back to
    the others in that order. Inside a pool the risky rows come first (Implemented or Inferred claims, policies written by the
    AI), then ``sha256(seed|kind|id)`` where the seed is made of the project id and the sorted row ids only, so changing the
    content of an unrelated row does not re-roll the sample (and nothing depends on the platform, the hash seed or slice order).
    """
    pools = _pool(model, prov)
    seed = _seed(model, pools)
    ranked = {kind: sorted((row for row in rows if prov.get(row[1])), key=lambda row: (row[4], _rank(seed, kind, row[0])))
              for kind, rows in pools.items()}
    taken: dict = {kind: [] for kind in ranked}
    for kind, quota in SAMPLE_QUOTAS:
        taken[kind] = ranked[kind][:quota]
    total = sum(len(v) for v in taken.values())
    order = [kind for kind, _q in SAMPLE_QUOTAS] + list(SAMPLE_FALLBACK)
    while total < k:
        progressed = False
        for kind in order:
            if total >= k:
                break
            if len(taken[kind]) < len(ranked[kind]):
                taken[kind].append(ranked[kind][len(taken[kind])])
                total += 1
                progressed = True
        if not progressed:
            break
    rows = []
    for kind in order:
        for ident, key, claim, vs, _risk in taken[kind]:
            rows.append({"at": "slices/" + prov[key], "k": _KIND_CODE[kind], "id": _clip(ident, ID_CLIP), "claim": claim, "vs": vs})
    rows.sort(key=lambda r: (_file_line(r["at"]), r["id"]))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------------------------------------------

def build_report(ctx: Context, *, sample_k: int = SAMPLE_K, cap: int = REPORT_CAP) -> dict:
    """The review report: a JSON-able dict of at most ``cap`` bytes (never cut by the generic shrinker: the sample stays whole)."""
    findings, stats, oracle = collect(ctx)
    sha = am.model_sha(ctx.model)
    counts = {s: sum(1 for f in findings if f.sev == s) for s in SEVERITIES}
    rows = sample_rows(ctx.model, ctx.prov, sample_k)
    names = {code: kind for kind, code in _KIND_CODE.items()}
    report = {
        "status": "ok", "authority": AUTHORITY, "action": "report", "schema": SCHEMA, "modelSha": sha[:8], "mode": ctx.mode,
        "counts": counts, "stats": stats, "findings": [f.shown(SHOW_IDS) for f in findings], "oracle": oracle,
        "record": ctx.record_view, "sample": rows,
        "compare": {code: COMPARE[names[code]] for code in sorted({row["k"] for row in rows})},
        "next": _next_lines(counts, ctx),
    }
    return _fit(report, cap)


def _next_lines(counts: dict, ctx: Context) -> list:
    if ctx.record_view.get("state") in ("current", "accepted"):
        return ["a review is recorded: no second pass; fix only what the report lists as new BLOCK, then report the open items to the user"]
    return ["check the sample rows against the real source or requirement text (a fresh subagent if you can); a mismatch is a finding",
            f"fix BLOCK ({counts['BLOCK']}) and GAP ({counts['GAP']}) findings with put mode=patch; INFO only when it is a real defect",
            "then once: action=record notes=\"fixed=<ids>; open=<ids>\"; an open BLOCK goes to the user in the final report"]


def _size(report: Mapping) -> int:
    return core.reply_bytes(report)


def _fit(report: dict, cap: int) -> dict:
    """Shrink in fixed stages until the report fits: ids per finding 3, 2, 1, 0, then claims, then ``next``; never the rows."""
    if _size(report) <= cap:
        return report
    for limit in (2, 1, 0):
        for sev in ("INFO", "GAP", "BLOCK"):  # the locations of the worst findings are the last thing to go
            for f in report["findings"]:
                if "ids" in f and f["sev"] == sev:
                    if limit:
                        f["ids"] = f["ids"][:limit]
                    else:
                        del f["ids"]
            if _size(report) <= cap:
                return report
    for clip in (70, 52, 36):
        for row in report["sample"]:
            row["claim"] = _clip(row["claim"], clip)
            row["vs"] = _clip(row["vs"], clip)
        if _size(report) <= cap:
            return report
    report["next"] = report["next"][:1]
    for f in report["findings"]:
        f["fix"] = _clip(f["fix"], 48)
    if _size(report) > cap:
        report["findings"] = [f for f in report["findings"] if f["sev"] != "INFO"] + \
            [{"id": "RV-INFO", "sev": "INFO", "n": sum(1 for f in report["findings"] if f["sev"] == "INFO"), "fix": "INFO findings omitted: size cap"}]
        report["truncated"] = True
    return report


# ---------------------------------------------------------------------------------------------------------------
# Service functions (the MCP tool and the CLI call these)
# ---------------------------------------------------------------------------------------------------------------


def _user_text(paths: pj.Paths) -> str:
    chunks = []
    for path in status_mod._user_texts(paths):  # noqa: SLF001 - the one definition of "saved requirement text"
        try:
            chunks.append(status_mod.read_user_text(path))   # bounded: never the whole file first
        except OSError:
            continue
    return _norm_quote("\n".join(chunks))


def _stamp_ids(paths: pj.Paths) -> "set | None":
    """The evidence ids that carry a stamp entry; ``None`` when there is no stamp file (nothing was verified yet)."""
    if not paths.stamp.is_file():
        return None
    try:
        data = json.loads(sl.read_limited(paths.stamp, sl.RECORD_MAX_BYTES).decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return set()
    entries = data.get("entries") if isinstance(data, dict) else None
    return set(entries) if isinstance(entries, dict) else set()


def context_of(snap: "status_mod.Snapshot") -> Context:
    """The report context of a loaded snapshot (reads the saved texts, the stamp, the oracle ledger and the record)."""
    record = status_mod.read_review(snap.paths)
    ctx = Context(snap.model, snap.prov, user_texts=_user_text(snap.paths), stamp=_stamp_ids(snap.paths),
                  oracle=status_mod.read_oracle(snap.paths), record=record, mode=snap.mode,
                  vanished_tbds=tuple(status_mod.vanished_tbds(snap)))
    ctx.record_view = record_state(snap, record)
    return ctx


def review_report(ws: Workspace, project: str) -> dict:
    """``action=report``: mechanical findings and the spot-check sample of the committed model. Never writes."""
    def body() -> dict:
        paths, cfg = core._loaded(ws, project)  # noqa: SLF001
        snap = status_mod.load_snapshot(paths, cfg)
        report = build_report(context_of(snap))
        if snap.red:
            report.setdefault("warnings", []).append(f"{len(snap.red)} red draft(s): the report covers the committed slices only")
        big = status_mod.oversize_user_texts(paths)
        if big:
            report.setdefault("warnings", []).append(
                f"saved text larger than {status_mod.REVIEW_MAX_BYTES // 1024} KiB ({', '.join(big[:3])}): only its first part is read for quotations; split it")
        return core.fit_result(report, core.HARD_CAP)
    return core._guard(body)  # noqa: SLF001


def _claimed_fixed(notes: str) -> list:
    found = []
    for part in re.split(r"[;\n]", notes or ""):
        if part.strip().lower().startswith("fixed"):
            found.extend(_FIXED_RE.findall(part.upper()))
    return sorted(set(found))


def _canonical(record: Mapping) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def make_record(model: Mapping, mode: str, findings: Sequence[Finding], notes: str = "") -> dict:
    """The content of ``.atlas/review.json`` for ``model`` and its findings (no timestamp, deterministic)."""
    now_ids = {f.id for f in findings}
    return {
        "schema": SCHEMA, "modelSha": am.model_sha(model), "mode": mode, "notes": (notes or "").strip(),
        "findingsAtReview": [{"id": f.id, "sev": f.sev, "n": f.n} for f in findings][:RECORD_FINDINGS_MAX],
        "blockKeys": sorted({k for f in findings if f.sev == "BLOCK" for k in f.keys()})[:BLOCK_KEYS_MAX],
        "fixedAfter": [i for i in _claimed_fixed(notes) if i not in now_ids],
    }


def review_record(ws: Workspace, project: str, notes: str = "") -> dict:
    """``action=record``: write ``.atlas/review.json`` once. Needs the write gate; refused after a record and before phase review."""
    def body() -> dict:
        ws.writable()
        if not isinstance(notes, str) or len(notes.encode("utf-8", errors="replace")) > NOTES_MAX_BYTES:
            raise GemArcError(E_REVIEW_ARG, f"notes must be text of at most {NOTES_MAX_BYTES} bytes")
        paths, cfg = core._loaded(ws, project)  # noqa: SLF001
        pj.check_confined(ws, paths)
        with sl.project_lock(paths):
            snap = status_mod.load_snapshot(paths, cfg)
            existing = status_mod.read_review(paths)
            if valid_record(existing):
                return core.invalid(E_REVIEW_ONCE, "a review is already recorded: one pass only. report still works; "
                                    "fix only new BLOCK findings (put mode=patch) and tell the user about the open items",
                                    modelSha=str(existing["modelSha"])[:8])
            state = status_mod.evaluate(snap)
            if state.phase != "review":
                return core.invalid(E_REVIEW_PHASE, f"the project is at phase {state.phase}: record only at phase review (every other condition "
                                    "of done holds); nothing was recorded", phase=state.phase, blockers=state.blockers[:6])
            ctx = context_of(snap)
            findings, _stats, _oracle_state = collect(ctx)
            record = make_record(snap.model, snap.mode, findings, notes)
            now_ids = {f.id for f in findings}
            claimed = _claimed_fixed(notes)
            sl.atomic_write(paths.review, _canonical(record))
        after = status_mod.evaluate(status_mod.load_snapshot(paths, cfg))
        counts = {s: sum(1 for f in findings if f.sev == s) for s in SEVERITIES}
        reply = {"status": "recorded", "authority": AUTHORITY, "action": "record", "modelSha": record["modelSha"][:8], "counts": counts,
                 "fixedAfter": record["fixedAfter"], "done": after.done, "phase": after.phase, "blockers": after.blockers[:6],
                 "open": [f.shown(SHOW_IDS) for f in findings if f.sev == "BLOCK"],
                 "next": ["final report: say which findings were fixed and list every open BLOCK for the user; no second review"]}
        stale = [i for i in claimed if i in now_ids]
        if stale:
            reply["claimedButOpen"] = stale
        return core.fit_result(reply, core.DEFAULT_CAP)
    return core._guard(body)  # noqa: SLF001


def run(ws: Workspace, project: str, action: str = "report", notes: str = "") -> dict:
    """The tool body: ``action`` is ``report`` (read-only) or ``record``."""
    def body() -> dict:
        if action == "report":
            return review_report(ws, project)
        if action == "record":
            return review_record(ws, project, notes)
        raise GemArcError(E_REVIEW_ARG, "action is 'report' or 'record'")
    return core._guard(body)  # noqa: SLF001
