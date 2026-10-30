"""The build: model + fragments + sealed runs -> ``dist/index.html`` (design 9.3, milestone M4; reply shapes in design 4.1).

Pure part (no project, no disk): ``make_bundle(model, ...)`` builds the ten-key ``window.__ATLAS__`` bundle of
``docs/atlas/spec/bundle-contract.md`` (profile, fragments and closures of the primary language, 20 canonical document
selections for the in-page twin check, structure diagrams, the sealed runs it is given, scene and vocabulary findings);
``render(model, ...)`` fills the page (``htmlassemble``) and runs the build checks BD-001..BD-007.

Project part: ``build_project`` refuses a model with ERROR findings, asks the oracle provider (``oracle.sealed_runs``: the ONLY
source of any simulation result, ORACLE_FIRST) for the sealed runs, renders the page, writes ``atlas/dist/`` (``index.html``,
``build-info.json``, optionally ``docs/``, ``diagrams/``, ``prompts/``; ``oracle-sheet.md`` and ``.verify/`` are never touched)
and optionally runs the browser gate; ``verify_project`` runs the gate on the page that is already there.

Findings computed here (they need no engine and no browser; the viewer shows them in the QA tab):

* ``meta.warnings``: the vocabulary warnings the viewer raises at run time (``GEMARC_ATLAS_VIEW_001``, ``_002``, ``_003``,
  ``_005``, ``_006``, ``_009``), derived from the model and the profile in Python so the page needs no warm-up;
* ``meta.qa.notes``: RC-001 (two floors of a building share a level, or a level is not a usable number), RC-002 (an unknown node
  kind or trust value is drawn with a neutral default) and LB-001 (a node name is shortened on its 2D card).

Deterministic: no timestamp, no machine path, sorted keys; two builds of one project are byte-identical (``build-info.json``
included, ``dist/.verify/`` excluded). Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..paths import GemArcError, Workspace
from . import htmlassemble as H
from . import model as am
from . import oracle as oracle_mod
from . import project as pj
from . import slices as sl
from . import status as status_mod
from . import vocab as vocab_mod
from .derive import docbuild
from .derive import i18n as T

E_REFUSED = "GEMARC_ATLAS_BUILD_001"
E_CONTRACT = "GEMARC_ATLAS_BUILD_002"
E_PAGE = "GEMARC_ATLAS_BUILD_003"
E_ORACLE = "GEMARC_ATLAS_BUILD_004"
E_ARG = "GEMARC_ATLAS_BUILD_005"
E_VIEWER = H.E_VIEWER
E_RELEASE = "GEMARC_ATLAS_BUILD_020"
E_GATE_FAIL = "GEMARC_ATLAS_BUILD_030"
E_GATE_SKIP = "GEMARC_ATLAS_BUILD_031"
E_NO_PAGE = "GEMARC_ATLAS_BUILD_032"
E_GATE_INCONCLUSIVE = "GEMARC_ATLAS_BUILD_033"

DIST_NAME = "dist"
VERIFY_DIR = ".verify"
OWNED_DIRS = ("docs", "diagrams", "prompts")
OWNED_FILES = ("index.html", "build-info.json")
SELECTIONS = 20
WARN_CAP = 25
GATE_WIDTHS = (1024, 1366, 1600)
GATE_MODES = ("none", "smoke")
#: Scripted steps a built page must run in the gate (viewer shell: doc-twin, levels, inspector, twod-labels, qa-rows, table-words, search, inspector-tabs;
#: stage tabs: scene-3d, case-play, pair-play).
#: A page that reports fewer has lost checks, and the gate fails closed on it (the runner only counts the steps a page brings).
GATE_STEPS = 11
#: The tabs the walker must visit and the step names the page must run (viewer ``main.js`` / ``shell.js``; ``test_bundle_sources`` pins both to the
#: runtime). ``require_steps`` compares a gate verdict with them.
GATE_TABS = ("1D", "2D", "3D", "4D", "5D", "QA")
GATE_STEP_NAMES = ("doc-twin", "levels", "inspector", "twod-labels", "qa-rows", "table-words", "search", "inspector-tabs", "scene-3d", "case-play", "pair-play")
CARD_NAME_PX = 150 - 34          # the 2D card (layout.js CARD.w minus the text inset of tab-twod.js)
CARD_NAME_SIZE = 12
WIDE_FROM = 11904                # label-fit.js: from this code point on a glyph is one em wide
IDLE = "Idle"
VIEW = {"kind": "GEMARC_ATLAS_VIEW_001", "relation": "GEMARC_ATLAS_VIEW_002", "trust": "GEMARC_ATLAS_VIEW_003", "unplaced": "GEMARC_ATLAS_VIEW_005",
        "floor": "GEMARC_ATLAS_VIEW_006", "level": "GEMARC_ATLAS_VIEW_009"}


class BuildRefused(Exception):
    """A request the build refuses (nothing was written): ``code``, ``message`` and optional ``extra`` for the reply."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


# --------------------------------------------------------------------------------------------- findings (RC, LB, VIEW)

def estimate_text_width(text, size: float) -> float:
    """Mirror of ``estimateTextWidth`` in ``viewer/src/label-fit.js`` (full-width glyphs one em, others 0.56 em); the same order of
    additions, so the floating point result is the same."""
    total = 0.0
    for ch in str(text if text is not None else ""):
        total += size if ord(ch) > WIDE_FROM else size * 0.56
    return total


def _records(value) -> list:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _id_of(record: Mapping):
    value = record.get("id")
    return value if isinstance(value, str) and value else None


def vocab_findings(model: Mapping, profile) -> dict:
    """``{"warnings": [...], "notes": [...], "suppressed": {code: n}, "unassigned": int, "buildings": [ids], "labels": int}``.

    ``warnings`` are the viewer's vocabulary warnings (``{code, subject, detail}``, subjects as the viewer writes them so the QA
    tab merges the two lists), ``notes`` are RC-001, RC-002 and LB-001. At most ``WARN_CAP`` per code are kept; the rest is counted."""
    space = model.get("space") if isinstance(model.get("space"), dict) else {}
    buildings = {b["id"]: b for b in _records(space.get("buildings")) if _id_of(b)}
    floors = [f for f in _records(space.get("floors")) if _id_of(f)]
    rooms = {r["id"]: r for r in _records(space.get("rooms")) if _id_of(r)}
    floor_by_id = {f["id"]: f for f in floors}
    nodes = [n for n in _records(model.get("nodes")) if _id_of(n)]
    edges = _records(model.get("edges"))
    warnings: dict = {}
    notes: dict = {}

    def warn(code: str, subject, detail: str) -> None:
        warnings.setdefault(code, {}).setdefault(str(subject), detail)

    def note(code: str, subject, detail: str) -> None:
        notes.setdefault(code, {}).setdefault(str(subject), detail)

    kinds_known = bool(profile.ids("kinds"))
    for n in nodes:
        kind = n.get("kind")
        if kinds_known and not profile.has("kinds", kind):
            warn(VIEW["kind"], kind, "unknown node kind: neutral shape")
            note("RC-002", kind, "unknown node kind: drawn with a neutral shape and the deepest level")
    for e in edges:
        if profile.ids("relations") and not profile.has("relations", e.get("relation")):
            warn(VIEW["relation"], e.get("relation"), "unknown relation: neutral colour, open views only")
    levels = set(profile.ids("levels"))
    trust_known = bool(profile.ids("trust"))
    unassigned = 0
    for n in nodes:
        level = n.get("level")
        if isinstance(level, str) and levels and level not in levels:
            warn(VIEW["level"], level, "unknown node level: taken from the room layer or the kind")
        props = n.get("props") if isinstance(n.get("props"), dict) else {}
        room = rooms.get(n.get("roomId"))
        floor = floor_by_id.get(room.get("floorId")) if room else None
        building = buildings.get(floor.get("buildingId")) if floor else None
        if building is None:
            unassigned += 1
            warn(VIEW["unplaced"], n["id"], "node does not resolve to a room, floor and building: unassigned region")
        trust = props.get("trust") if isinstance(props.get("trust"), str) and props.get("trust") else (
            building.get("trust") if building and isinstance(building.get("trust"), str) and building.get("trust") else None)
        if trust is not None and trust_known and not profile.has("trust", trust):
            warn(VIEW["trust"], trust, "unknown trust value: default domain")
            note("RC-002", trust, "unknown trust value: drawn in the default domain")
        if estimate_text_width(n.get("name"), CARD_NAME_SIZE) > CARD_NAME_PX:
            note("LB-001", n["id"], "the node name is shortened with an ellipsis on its 2D card")
    by_building: dict = {}
    for f in floors:
        by_building.setdefault(f.get("buildingId"), []).append(f)
    for fs in by_building.values():
        used: set = set()
        for f in fs:
            level = f.get("level")
            usable = isinstance(level, (int, float)) and not isinstance(level, bool) and math.isfinite(level) and abs(level) <= 1e6
            if usable and level not in used:
                used.add(level)
                continue
            warn(VIEW["floor"], f["id"], "floor level repeated, not a number or out of range: floor moved above the building")
            note("RC-001", f["id"], "the floor shares a level with another floor of its building, or the level is not a usable number")
    suppressed: dict = {}

    def flatten(groups: dict) -> list:
        out = []
        for code in sorted(groups):
            items = list(groups[code].items())
            if len(items) > WARN_CAP:
                suppressed[code] = len(items) - WARN_CAP
            out.extend({"code": code, "subject": subject, "detail": detail} for subject, detail in items[:WARN_CAP])
        return out

    labels = len(notes.get("LB-001", {}))
    return {"warnings": flatten(warnings), "notes": flatten(notes), "suppressed": suppressed, "unassigned": unassigned,
            "buildings": [b["id"] for b in _records(space.get("buildings")) if _id_of(b)], "labels": labels}


# --------------------------------------------------------------------------------------------- sealed runs -> bundle runs

#: Event keys the stage tabs never read (viewer-B shows the source, target, edge, type, outcome and state changes of an event; the cause is shown as the
#: chain's own ``why`` text, the rule and the evidence belong to the documents). About 68 KB of a reference-scale sealed page; without them the page of the
#: largest measured seed stays under the 2,000,000 byte budget with the stage modules in it. The engine's result and the run ``hash`` are untouched.
EVENT_UNREAD_KEYS = ("evidenceIds", "cause", "ruleId", "attempt")


def _compact_event(ev: Mapping) -> dict:
    """A trace event without the keys that carry no information (null, false, empty), the keys the viewer derives from others
    (``sequence`` = index + 1, ``caseId`` = the run's, ``previousState`` / ``nextState`` = the ``stateChanges``) and the keys it never reads
    (:data:`EVENT_UNREAD_KEYS`)."""
    out = {}
    for key, value in ev.items():
        if key in ("sequence", "caseId", "previousState", "nextState") or key in EVENT_UNREAD_KEYS:
            continue
        if value is None or value is False or value == [] or value == {}:
            continue
        out[key] = value
    return out


def _compact_states(states) -> dict:
    """A node-state record without the nodes in the default state ``Idle`` (the viewer draws no ring for them, so a missing node is Idle)."""
    return {k: v for k, v in states.items() if v != IDLE} if isinstance(states, dict) else {}


#: The fields of a diagram unit the viewer reads (search: id, title, type; the diagram board: title, type, covers, mermaid). ``data`` (the
#: structured form the Mermaid text was written from) and ``evidenceIds`` are what the documents are built from, not what the page shows.
UNIT_PAGE_FIELDS = ("id", "type", "title", "covers", "requirementIds", "evidenceLevel", "mermaid", "issues")


def compact_unit(unit: Mapping) -> dict:
    """A diagram unit as the page embeds it: only :data:`UNIT_PAGE_FIELDS` (about 55 KB of a reference-scale sealed page; lossless for the viewer)."""
    out = {k: unit[k] for k in UNIT_PAGE_FIELDS if k in unit}
    covers = out.get("covers")
    if isinstance(covers, dict):   # a repeated id is one thing covered once (the first order is kept)
        out["covers"] = {k: list(dict.fromkeys(v)) if isinstance(v, list) and all(isinstance(x, str) for x in v) else v for k, v in covers.items()}
    return out


def compact_run(run: Mapping) -> dict:
    """A run as the page embeds it: no snapshots (the viewer folds from ``initialState``), only the non-Idle node states, and compact
    trace events. Everything else is kept as the engine wrote it."""
    out = {k: v for k, v in run.items() if k not in ("snapshots", "trace", "initialState", "finalState")}
    out["initialState"] = _compact_states(run.get("initialState"))
    out["finalState"] = _compact_states(run.get("finalState"))
    out["trace"] = [_compact_event(ev) for ev in run.get("trace", ())]
    return out


def runs_from_sealed(sealed: "Mapping | None") -> tuple:
    """``(runs, run_diagrams, trace_lengths, withheld)`` from the provider's reply (``oracle.sealed_runs``), or an empty set for ``None``.

    The provider's shapes differ from the bundle's (``baselines[id] = {state, run}`` becomes the run; a pair ``{result, chain}`` becomes
    ``{caseId, interventionId, status, errors, B, diff, chain}``; ``A`` is the case's baseline and is not embedded twice)."""
    if not sealed:
        return {"baselines": {}, "pairs": []}, [], [], {"cases": [], "interventions": []}
    baselines, lengths = {}, []
    for cid in sorted(sealed.get("baselines") or {}):
        run = compact_run(sealed["baselines"][cid]["run"])
        baselines[cid] = run
        lengths.append({"caseId": cid, "interventionId": None, "length": len(run["trace"])})
    pairs = []
    for pair in sorted(sealed.get("pairs") or [], key=lambda p: (p["interventionId"], p["caseId"])):
        result = pair["result"]
        b = result.get("B")
        compact_b = compact_run(b) if isinstance(b, dict) else None
        pairs.append({"caseId": pair["caseId"], "interventionId": pair["interventionId"], "status": result.get("status"), "errors": result.get("errors") or [],
                      "B": compact_b, "diff": result.get("diff"), "chain": pair["chain"]})
        lengths.append({"caseId": pair["caseId"], "interventionId": pair["interventionId"], "length": len(compact_b["trace"]) if compact_b else 0})
    withheld = sealed.get("withheld") or {"cases": [], "interventions": []}
    return {"baselines": baselines, "pairs": pairs}, list(sealed.get("diagrams") or []), lengths, withheld


# --------------------------------------------------------------------------------------------- the bundle

def make_bundle(model: dict, *, viewer: "H.Viewer | None" = None, sealed: "Mapping | None" = None, qa: "Mapping | None" = None,
                lang: "str | None" = None) -> dict:
    """The ten-key ``window.__ATLAS__`` bundle. ``sealed`` is ``oracle.sealed_runs``'s reply (``None``: no runs, the 4D/5D empty state);
    ``qa`` is merged into ``meta.qa``; ``lang`` overrides the primary language (default: ``model.meta.languages[0]``)."""
    viewer = viewer or H.load_viewer()
    meta_in = model.get("meta") if isinstance(model.get("meta"), dict) else {}
    langs = [x for x in meta_in.get("languages", []) if isinstance(x, str)] if isinstance(meta_in.get("languages"), list) else []
    langs = langs or ["en-US"]
    primary = T.normalize_lang(lang or langs[0])
    profile = vocab_mod.load_profile(meta_in.get("profile") or vocab_mod.DEFAULT_PROFILE)
    sha = am.model_sha(model)
    payload = docbuild.viewer_payload(model, primary, SELECTIONS)
    units = list(docbuild.get_bundle(model, primary).units)
    runs, run_units, lengths, withheld = runs_from_sealed(sealed)
    findings = vocab_findings(model, profile)
    project_id = meta_in.get("projectId") if isinstance(meta_in.get("projectId"), str) else ""
    counts = {"nodes": len(_records(model.get("nodes"))), "edges": len(_records(model.get("edges"))), "cases": len(_records(model.get("cases"))),
              "interventions": len(_records(model.get("interventions"))), "requirements": len(_records(model.get("requirements"))),
              "tbds": len(_records(model.get("tbds"))), "sealedCases": len(runs["baselines"]), "sealedPairs": len(runs["pairs"])}
    meta = {"projectId": project_id, "title": meta_in.get("title") if isinstance(meta_in.get("title"), str) else "", "modelSha": sha, "modelSha8": sha[:8],
            "prefKey": f"gemarc-atlas:{project_id}:{sha[:8]}", "languages": langs, "primaryLanguage": primary, "profileId": profile.id,
            "mode": meta_in.get("mode") if meta_in.get("mode") in ("asis", "tobe") else "asis", "counts": counts, "warnings": findings["warnings"]}
    qa_block = {"notes": findings["notes"]}
    qa_block.update(qa or {})
    qa_block["notes"] = list(findings["notes"]) + [n for n in (qa or {}).get("notes", []) if isinstance(n, dict)]
    meta["qa"] = qa_block
    return {
        "schema": H.BUNDLE_SCHEMA, "meta": meta, "profile": profile.raw, "model": model,
        "i18n": viewer.i18n, "fragments": payload["fragments"], "closures": payload["closures"], "runs": runs,
        "diagrams": [compact_unit(u) for u in units + run_units],
        "calibrationHooks": {"docSelections": payload["docSelections"], "traceLengths": lengths,
                             "scene": {"nodeCount": counts["nodes"], "buildingIds": findings["buildings"], "unassignedCount": findings["unassigned"]}},
    }


@dataclass
class Rendered:
    html: str
    bundle: dict
    issues: list
    breakdown: dict
    withheld: dict = field(default_factory=dict)
    suppressed: dict = field(default_factory=dict)

    @property
    def errors(self) -> list:
        return H.errors_of(self.issues)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()


def render(model: dict, *, viewer: "H.Viewer | None" = None, sealed: "Mapping | None" = None, qa: "Mapping | None" = None,
           lang: "str | None" = None, title: "str | None" = None) -> Rendered:
    """The page text of a model, with the build checks. Never writes; a failed ERROR check is returned in ``issues``, not raised.

    A model that cannot be encoded at all (a lone surrogate, NaN or infinity anywhere) is refused with ``BuildRefused(E_CONTRACT)`` instead
    of failing inside the encoder; every other contract issue is the caller's to judge."""
    fatal = [i for i in am.contract_issues(model) if i.get("code") in (am.E_SURROGATE, am.E_NONFINITE)]
    if fatal:
        raise BuildRefused(E_CONTRACT, f"the model cannot be encoded ({len(fatal)} issue(s)); first: {fatal[0]['code']} {fatal[0]['path']}")
    viewer = viewer or H.load_viewer()
    bundle = make_bundle(model, viewer=viewer, sealed=sealed, qa=qa, lang=lang)
    meta = bundle["meta"]
    page_title = title if title is not None else (meta["title"] or meta["projectId"])
    page = H.fill_page(viewer, bundle, lang=meta["primaryLanguage"], title=page_title)
    issues = H.check_page(page, viewer, bundle, lang=meta["primaryLanguage"], title=page_title)
    _runs, _units, _lengths, withheld = runs_from_sealed(sealed)
    return Rendered(page, bundle, issues, H.breakdown(page, bundle, viewer), withheld)


# --------------------------------------------------------------------------------------------- project build

def dist_dir(paths: pj.Paths) -> Path:
    return paths.atlas / DIST_NAME


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_lf(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sl.atomic_write(path, data)


def _check_dist_confined(ws: Workspace, paths: pj.Paths) -> Path:
    pj.check_confined(ws, paths)
    out = dist_dir(paths)
    if out.is_symlink():
        raise GemArcError("GEMARC_AUTH_003", "dist is a link: refusing to write through it")
    return out


def _clear_owned(out: Path) -> None:
    """Remove what the build owns (``docs``, ``diagrams``, ``prompts``, ``index.html``, ``build-info.json``). ``oracle-sheet.md`` and
    ``.verify`` stay."""
    for name in OWNED_DIRS:
        target = out / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
    for name in OWNED_FILES:
        target = out / name
        if target.is_file():
            target.unlink()


def _severity_counts(diags) -> dict:
    out = {"error": 0, "warn": 0, "info": 0}
    for d in diags:
        key = {"ERROR": "error", "WARN": "warn", "INFO": "info"}.get(d.severity)
        if key:
            out[key] += 1
    return out


def _evidence_blockers(snap, limit: int = 5) -> list:
    """As-is release rule (design 7.6): every Implemented claim rests on source-code evidence the verifier has stamped (the stamp is the
    record of an EV-003 pass). To-be mode has no such claim."""
    if snap.mode != "asis":
        return []
    from . import evidence as ev

    try:
        entries = (ev.load_stamp(snap.paths.atlas).get("entries") or {})
    except ev.EvidenceError:
        entries = {}
    by_id = {e.get("evidenceId"): e for e in snap.model.get("evidences", ()) if isinstance(e, dict)}
    bad = []
    for kind in ("nodes", "edges"):
        for item in snap.model.get(kind, ()):
            if not isinstance(item, dict) or item.get("verification") != "Implemented":
                continue
            code = [i for i in item.get("evidenceIds", ()) if by_id.get(i, {}).get("sourceKind") == "source-code"]
            if not code or any(i not in entries for i in code):
                bad.append(item.get("id") or item.get("edgeId") or "?")
    return [{"kind": "evidence", "rule": "EV-003", "id": ident, "message": "an Implemented claim without a verified source-code anchor"} for ident in bad[:limit]] + (
        [{"kind": "evidence", "rule": "EV-003", "id": "", "message": f"{len(bad) - limit} more Implemented claims lack a verified anchor"}] if len(bad) > limit else [])


def _marker_blockers(model: dict, lang: str) -> list:
    total = 0
    for level in docbuild.LEVELS:
        total += int(docbuild.doc_outline(model, None, level, lang).get("markers") or 0)
    return [{"kind": "markers", "rule": "DC-L-004", "id": "", "message": f"{total} NOT-AUTHORED slot(s) remain in the documents"}] if total else []


def gate_summary(verdict: Mapping) -> dict:
    """The deterministic part of a gate verdict (status, widths and finding names; no timings, no paths)."""
    return {"status": verdict["status"], "widths": [{"width": w["width"], "status": w["status"], "codes": w["codes"]} for w in verdict["widths"]]}


def build_project(ws: Workspace, project: str, release: bool = False, docs: bool = True, gate: str = "none", widths=GATE_WIDTHS) -> dict:
    """Build ``atlas/dist`` for a project; returns a reply dict (``status`` ``ok``, or raises ``BuildRefused`` / ``oracle.OracleError``).

    ``gate="smoke"`` runs the browser gate on the page at ``widths`` (a SKIP, no browser, is reported as SKIP and never as a pass);
    ``release`` additionally requires the oracle gate (after a mutant run), verified evidence (as-is), no NOT-AUTHORED slot and a
    passing browser gate: a build that cannot be released is still written but replies ``invalid`` with the blockers."""
    if gate not in GATE_MODES:
        raise BuildRefused(E_ARG, f"gate is one of {', '.join(GATE_MODES)}")
    ws.writable()
    paths = pj.project_paths(ws, project)
    cfg = pj.load_config(paths)
    out = _check_dist_confined(ws, paths)
    viewer = H.load_viewer()
    snap = status_mod.load_snapshot(paths, cfg)
    if not snap.check.ok:
        first = [d for d in snap.check.diagnostics if d.severity == "ERROR"][:3]
        raise BuildRefused(E_REFUSED, f"the model has ERROR findings ({sum(1 for d in snap.check.diagnostics if d.severity == 'ERROR')}): fix them, then build",
                           diagnostics=[{"id": d.rule, "severity": d.severity, "message": f"{d.at} {d.path}", "entityId": ""} for d in first])
    issues = am.contract_issues(snap.model)
    if issues:
        raise BuildRefused(E_CONTRACT, f"the model breaks the contract ({len(issues)} issue(s)); first: {issues[0]['code']} {issues[0]['path']}")
    model = snap.model
    lang = cfg.languages[0] if cfg.languages else "en-US"
    if release:
        oracle_mod.bite_run(ws, project)               # the release gate needs a mutant run of this very model (OR-004)
    sealed = oracle_mod.sealed_runs(ws, project, lang)  # write call: seals first, then returns sealed or confirmed items only
    if sealed["modelSha8"] != am.model_sha8(model):
        raise BuildRefused(E_ORACLE, "the project changed while it was being built; run the build again")
    gate_oracle = oracle_mod.release_gate(ws, project)
    state = status_mod.evaluate(snap)
    sev = _severity_counts(snap.check.diagnostics)
    counts = gate_oracle["counts"]
    qa = {"rules": {"block": state.report.counts().get("BLOCK", 0) if state.report else 0, **sev},
          "gate": {"release": bool(gate_oracle["ok"]), "blockers": len(gate_oracle["blockers"])},
          "oracle": {"sealed": counts["sealed"], "revised": counts["revised"], "confirmed": counts["confirmed"], "pending": counts["unregistered"],
                     "sheet": "oracle-sheet.md", "ledger": sealed["provenance"]["ledger"], "badge": gate_oracle["attestation"]["badge"]}}
    rendered = render(model, viewer=viewer, sealed=sealed, qa=qa, lang=lang)
    if rendered.errors:
        raise BuildRefused(E_PAGE, f"the page failed the build checks: {rendered.errors[0]['rule']} {rendered.errors[0]['message']}",
                           checks=[f"{i['rule']} {i['severity']}" for i in rendered.errors[:5]])
    docs_report = None
    with sl.project_lock(paths):
        _clear_owned(out)
        out.mkdir(parents=True, exist_ok=True)
        _write_lf(out / "index.html", rendered.html.encode("utf-8"))
        if docs:
            docs_report = docbuild.build_docs(model, out)
            for unit in sealed["diagrams"]:
                text = unit.get("mermaid")
                if isinstance(text, str) and isinstance(unit.get("id"), str):
                    _write_lf(out / "diagrams" / f"{T.dg_id(unit['id'])}.mmd", text.encode("utf-8"))
    page_path = out / "index.html"
    verdict = None
    if gate == "smoke":
        verdict = run_gate(page_path, tuple(widths), out)
    blockers: list = []
    if release:
        blockers += [dict(b) for b in gate_oracle["blockers"]]
        blockers += _evidence_blockers(snap) + _marker_blockers(model, lang)
        if verdict is None:
            blockers.append({"kind": "gate", "rule": "BD", "id": "", "message": "the browser gate was not run (gate=smoke is required for a release)"})
        elif verdict["status"] != "PASS":
            blockers.append({"kind": "gate", "rule": "BD", "id": "", "message": f"the browser gate is {verdict['status']}, not PASS"})
    info = {"schema": "gemarc-atlas-build/1", "projectId": model.get("meta", {}).get("projectId", ""), "modelSha": am.model_sha(model), "modelSha8": am.model_sha8(model),
            "language": lang, "counts": rendered.bundle["meta"]["counts"], "page": {"bytes": len(rendered.html.encode("utf-8")), "sha256": rendered.sha256},
            "parts": dict(rendered.breakdown["parts"]), "data": dict(rendered.breakdown["data"]), "viewer": {k: v for k, v in sorted(viewer.hashes.items())},
            "checks": [{"rule": i["rule"], "severity": i["severity"], "message": i["message"]} for i in rendered.issues],
            "withheld": rendered.withheld, "oracle": qa["oracle"], "findings": {"warnings": len(rendered.bundle["meta"]["warnings"]), "notes": len(rendered.bundle["meta"]["qa"]["notes"])},
            "docs": {"files": len(docs_report["files"]), "bytes": sum(f["bytes"] for f in docs_report["files"])} if docs_report else None,
            "gate": gate_summary(verdict) if verdict else {"status": "NOT-RUN", "widths": []},
            "release": {"requested": bool(release), "ok": bool(release and not blockers), "blockers": blockers[:20]}}
    _write_lf(out / "build-info.json", (json.dumps(info, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return {"dist": out, "page": page_path, "info": info, "rendered": rendered, "verdict": verdict, "blockers": blockers, "sealed": sealed,
            "docs": docs_report}


def run_gate(page: Path, widths: tuple, out: Path) -> dict:
    """The browser gate on ``page`` at ``widths``; scratch directory and report under ``out/.verify`` (the scratch part is removed)."""
    from .gate import smoke

    work = out / VERIFY_DIR / "work"
    try:
        verdict = smoke.verify_gate(page, tuple(widths), tmp_root=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    verdict = require_steps(verdict)
    _write_report(out, verdict)
    return verdict


def require_steps(verdict: dict, expected: int = GATE_STEPS, tabs: "tuple | None" = None, names: "tuple | None" = None) -> dict:
    """Fail closed on a page that lost checks. A PASS row becomes a FAIL row when the page ran fewer scripted steps than the viewer defines
    (STEPS_MISSING), when a step the viewer defines is not among them, or when the tabs the walker visited are not exactly the viewer's tabs
    (TABS_MISSING): the runner only counts what a page brings, so a page that dropped a tab (and the step that went with it) would pass."""
    want_tabs = list(GATE_TABS if tabs is None else tabs)
    want_names = list(GATE_STEP_NAMES if names is None else names)
    for row in verdict.get("widths", []):
        if row.get("status") != "PASS":
            continue
        steps = row.get("steps") or {}
        got_names = row.get("stepNames")
        problem = None
        if row.get("tabs") != want_tabs:
            missing = [t for t in want_tabs if t not in (row.get("tabs") or [])]
            problem = ("TABS_MISSING", f"the walker visited tabs {row.get('tabs')}, the viewer defines {want_tabs}" + (f" (missing {', '.join(missing)})" if missing else ""))
        elif steps.get("total", 0) < expected:
            problem = ("STEPS_MISSING", f"the page ran {steps.get('total', 0)} scripted steps, the viewer defines {expected}")
        elif isinstance(got_names, list) and [n for n in want_names if n not in got_names]:
            problem = ("STEPS_MISSING", "the page did not run the step(s): " + ", ".join(n for n in want_names if n not in got_names))
        elif not isinstance(got_names, list):
            problem = ("STEPS_MISSING", "the gate result does not name the steps it ran")
        if problem:
            name, detail = problem
            row["status"] = "FAIL"
            row["codes"] = sorted({*row.get("codes", []), name})
            row["findings"] = list(row.get("findings", [])) + [{"code": "GEMARC_GATE_STEPS", "name": name, "detail": detail}]
            row["message"] = "the page ran fewer checks than the viewer defines"
    if verdict.get("status") == "PASS" and any(r.get("status") == "FAIL" for r in verdict.get("widths", [])):
        verdict["status"] = "FAIL"
    verdict["lines"] = [f"FAIL {r['width']}: {r['findings'][-1]['name']} {r['findings'][-1]['detail']}"[:200]
                        if r.get("status") == "FAIL" and r.get("findings") and r["findings"][-1].get("code") == "GEMARC_GATE_STEPS" else line
                        for r, line in zip(verdict.get("widths", []), verdict.get("lines", []))]
    return verdict


def _write_report(out: Path, verdict: Mapping) -> None:
    target = out / VERIFY_DIR
    target.mkdir(parents=True, exist_ok=True)
    _write_lf(target / "gate-report.json", (json.dumps(verdict, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def verify_project(ws: Workspace, project: str, widths=GATE_WIDTHS) -> dict:
    """Run the browser gate on ``atlas/dist/index.html`` (written by ``build``); refuses a page that is not for the current model.

    Writes only ``dist/.verify/`` (the report and the gate's scratch directory). Returns ``{"verdict", "page", "sha8"}``."""
    ws.writable()
    paths = pj.project_paths(ws, project)
    cfg = pj.load_config(paths)
    out = _check_dist_confined(ws, paths)
    page = out / "index.html"
    info_path = out / "build-info.json"
    if not page.is_file() or not info_path.is_file():
        raise BuildRefused(E_NO_PAGE, "there is no built page yet: run build first")
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BuildRefused(E_NO_PAGE, "dist/build-info.json is unreadable: run build again") from None
    snap = status_mod.load_snapshot(paths, cfg, mode="slice")
    if info.get("modelSha8") != am.model_sha8(snap.model) or info.get("page", {}).get("sha256") != _sha_file(page):
        raise BuildRefused(E_NO_PAGE, "the built page is not the page of the current model (or was edited): run build again")
    verdict = run_gate(page, tuple(widths), out)
    return {"verdict": verdict, "page": page, "sha8": info.get("modelSha8", "")}
