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

Pluggable conditions (**not applicable yet**, they default to passing; ``register_gate`` plugs one in): ``oracle`` (every
expectation sealed or confirmed, a fresh verdict for each), ``evidence`` (every as-is Implemented claim rests on a stamped anchor),
``docs`` (no NOT-AUTHORED slot in the derived documents), ``build`` (``dist/`` is the build of this very model),
``browser`` (the browser gate passed on this very page, or the user waived it). A registered gate is
``fn(snapshot) -> {"applicable": bool, "ok": bool, "detail": str}`` (optional ``requires``: the dimensions it makes required,
``needsUser``: a decision only the user can make). Dimensions 4D and 5D become *required* only when the oracle gate is applicable;
until then they are reported but never block.

The REAL gates (``PRODUCT_GATES``, section "Product gates") are active only inside ``with product_gates():`` (the MCP binding and the
shell entry open it around every call); a plain call of ``evaluate`` (the unit tests of the authoring core) sees only what
``register_gate`` put into ``GATES``. A gate only READS records (ledger, stamp, ``dist/``): status never runs a verdict, the
verifier or the browser.

Records read here (written by other lanes): ``.atlas/oracle.json`` (the ledger of ``oracle.py``) and
``.atlas/review.json`` ``{"modelSha": <8 or 64 hex of the model>, "findings": [...], "fixed": [...], "notes": "..."}``.
Standard library only.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
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
from .slices import SliceFile, atomic_write, list_red, list_slices, read_bytes

MAX_NEXT = 10
LINE_BYTES = 118
NEEDS_LINE_BYTES = 150          # a question for the user is a whole sentence: clipped at a word boundary, never mid-sentence
GATE_NAMES = ("oracle", "evidence", "docs", "build", "browser")
#: Rule sets of a full check. Wave 2 (the engine and oracle rules: CS-001, CS-005, CS-008, PT-003, SM-004/006/007, IV-001/002,
#: OR-001..005) is OFF by default because it adds findings (an ERROR for a synthetic intervention whose ``expect.status`` is not its
#: compile status, INFO lines on every sealed project) that the existing sessions of the other lanes were written without; a caller
#: asks for it with ``load_snapshot(..., wave_set=(1, 2))`` and ``check mode=full rule=<family>`` does. The wave-2 rules every full
#: check adds are OR-000 (INFO: an expectation no write call has sealed yet), OR-003 (coverage floor) and CS-001 (event bound to an edge).
FULL_WAVE_SET = (1,)
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
    report_cache: object = None      # the completeness report ``evaluate`` computed (the product gates reuse it)

    @property
    def mode(self) -> str:
        return (self.model.get("meta") or {}).get("mode") or self.config.mode

    @property
    def sha8(self) -> str:
        return am.model_sha8(self.model)


def _oracle_reading(paths: Paths, mode: str):
    """The oracle rules read the ledger (read-only) during a full check; any other check runs without it."""
    import contextlib

    if mode != "full":
        return contextlib.nullcontext()
    try:
        from . import oracle
    except ImportError:  # pragma: no cover - the oracle lane ships in the same package
        return contextlib.nullcontext()
    return oracle.reading(paths)


#: The wave-2 rules that every full check runs (static or read-only, none of them depends on the engine or on a verdict).
DEFAULT_WAVE2 = ("OR-000", "OR-003", "CS-001")


def _with_registration(result: "slicecheck.CheckResult", paths: Paths) -> None:
    """Add OR-000 (not registered), OR-003 (coverage floor) and CS-001 (event bound to an edge) to a default full check, without
    the rest of wave 2 (read-only, no verdict, no engine)."""
    try:
        from . import oracle
        from .rules import cs, or_
        from .rules.context import Context

        ctx = Context(result.expansion.model, result.expansion.provenance, mode="full", meta=None, finals=result.expansion.finals)
        with or_.reading(oracle.make_view(paths)):
            or_.unregistered(ctx)
            or_.anti_vacuity(ctx)
            cs.events_are_bound(ctx)
        extra = [d for d in ctx.out if d.rule in DEFAULT_WAVE2]
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    if extra:
        result.diagnostics = sorted(list(result.diagnostics) + extra, key=dg.Diag.sort_key)


def load_snapshot(paths: Paths, config: Config, *, mode: str = "full", only: "str | None" = None, wave_set: "tuple | None" = None) -> Snapshot:
    """Read the committed slices and check them. Reads only: nothing is written, no lock is taken (writes are atomic).

    ``mode="slice"`` with ``only=<file name>`` checks one slice against the committed rest (``check slice=...``).
    """
    files = list_slices(paths)
    texts = {f.fname: read_bytes(f.path) for f in files}
    parsed = [ex.parse_source(f.fname, texts[f.fname]) for f in files]
    red = list_red(paths)
    decoded = {name: data.decode("utf-8", errors="replace") for name, data in texts.items()}
    with _oracle_reading(paths, mode):
        result = slicecheck.check_slices(parsed, config.meta(), mode=mode, incoming=None if only is None else [only],
                                         red_drafts=[r.fname for r in red] if mode == "full" else (), element_templates=element_templates(),
                                         wave_set=(tuple(wave_set) if wave_set else FULL_WAVE_SET) if mode == "full" else (1,), texts=decoded)
    if mode == "full" and not wave_set and 2 not in FULL_WAVE_SET:
        _with_registration(result, paths)
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


def review_is_valid_record(paths: Paths) -> bool:
    """A review record exists that the review tool wrote (a second ``record`` is then refused by the review core)."""
    record = read_review(paths)
    return bool(record) and _is_valid_record(record)


def review_is_current(record: "Mapping | None", model: Mapping) -> bool:
    """A review record counts for the model whose sha (8 hex digits, or the full 64) it carries."""
    if not record:
        return False
    sha = record.get("modelSha")
    full = am.model_sha(model)
    return isinstance(sha, str) and len(sha) in (8, 64) and full.startswith(sha.lower())


def read_oracle(paths: Paths) -> "dict | None":
    return _read_json(paths.oracle)


def _legacy_summary(data: "Mapping | None") -> dict:
    """The provisional ledger shape ``{"seals": {key: {"kind", "state"}}}`` (or a list under ``entries``) that tests of the
    first authoring core write; the oracle lane itself never writes it. ``sealed`` counts ``sealed`` and ``confirmed``."""
    out = {"present": data is not None, "total": 0, "sealed": 0, "revised": 0, "confirmed": 0, "cases": 0, "interventions": 0, "unconfirmed": 0}
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


def oracle_summary(data: "Mapping | None") -> dict:
    """Counts from a raw ``.atlas/oracle.json``. The real schema (``gemarc-atlas-oracle/1``, see ``oracle.py``) maps
    ``case:<id>`` / ``intervention:<id>`` to an entry with ``kind``, ``state`` (``sealed`` or ``revised``), ``ah`` and the cache
    ``confirmedAh8``; ``cases`` / ``interventions`` count the sealed or confirmed ones and a withdrawn entry is not counted. The
    provisional ``seals`` shape is still read. ``evaluate`` prefers ``oracle.effective_summary`` (the user's confirmation file and
    the model are part of it)."""
    if isinstance(data, Mapping) and isinstance(data.get("items"), Mapping):
        from . import oracle

        return oracle.ledger_summary(data)
    return _legacy_summary(data)


def _oracle_state(snap: "Snapshot") -> dict:
    raw = read_oracle(snap.paths)
    if isinstance(raw, Mapping) and raw.get("schema") == "gemarc-atlas-oracle/1":
        from . import oracle

        try:
            return oracle.effective_summary(snap.paths, snap.model)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return oracle_summary(raw)


# ---------------------------------------------------------------------------------------------------------------
# Gates (pluggable, default: not applicable yet)
# ---------------------------------------------------------------------------------------------------------------

GATES: dict = {}


#: The real gates, active only inside ``with product_gates():`` (see the module doc); filled at the end of this module.
PRODUCT_GATES: dict = {}
_PRODUCT: "contextvars.ContextVar[bool]" = contextvars.ContextVar("gemarc_atlas_product_gates", default=False)


def register_gate(name: str, fn: Callable, *, product: bool = False) -> None:
    """Plug a condition into ``done``: ``fn(snapshot) -> {"applicable": bool, "ok": bool, "detail": str}``.

    ``product=True`` registers it among the real gates (``PRODUCT_GATES``), which only ``with product_gates():`` activates."""
    if name not in GATE_NAMES:
        raise ValueError(f"unknown gate {name!r}; one of {', '.join(GATE_NAMES)}")
    (PRODUCT_GATES if product else GATES)[name] = fn


@contextlib.contextmanager
def product_gates():
    """Activate the real gates for the calls made inside the ``with`` block (the MCP binding and the shell entry do)."""
    token = _PRODUCT.set(True)
    try:
        yield
    finally:
        _PRODUCT.reset(token)


def product_gates_active() -> bool:
    return _PRODUCT.get()


def unregister_gate(name: str) -> None:
    GATES.pop(name, None)


def _gate(name: str, snap: Snapshot) -> dict:
    fn = GATES.get(name)
    if fn is None and _PRODUCT.get():
        fn = PRODUCT_GATES.get(name)
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
# Product gates (the A1 completion contract; every one only READS records)
# ---------------------------------------------------------------------------------------------------------------

DIST_DIR = "dist"
VERIFY_DIR = ".verify"
GATE_STAMP_NAME = "gate-stamp.json"
GATE_STAMP_SCHEMA = "gemarc-atlas-gate-stamp/1"
REQUIRED_WIDTHS = (1024, 1366, 1600)   # the browser gate counts only a run that passed at all of them (design A2)
WAIVER_NAME = "waive-browser.txt"      # the USER's file (no tool writes it), like atlas/oracle-confirm.txt: attestation, not authentication
WAIVER_MAX_BYTES = 4096
#: Blocker kinds of the oracle release gate that keep ``done`` false (``no-bite``, the mutant run, belongs to ``build release=true``).
ORACLE_DONE_KINDS = ("unsealed", "no-verdict", "mismatch", "vacuous", "revised", "provenance")
_ORACLE_ADVICE = {
    "unsealed": "it names an undefined node: define every node it names, then put it again",
    "no-verdict": "run gemarc_atlas_simulate (detail=count) for this model",
    "mismatch": "the last verdict failed: check the case events first",
    "vacuous": "assert more of the nodes the case exercises (coverage floor)",
    "revised": "run gemarc_atlas_build; the user then approves the edit on the oracle sheet",
    "provenance": "the ledger was altered: the user confirms every expectation",
}


def _structure_open(snap: "Snapshot") -> bool:
    """True while the structure is not authored cleanly: the later gates have nothing to say yet."""
    if snap.red or snap.check.errors or not snap.model.get("nodes"):
        return True
    report = snap.report_cache
    return bool(report is not None and report.blockers())


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gate_oracle(snap: "Snapshot") -> dict:
    """Every expectation sealed or confirmed, a fresh passing verdict for each, nothing vacuous; ``revised`` ones wait for the user.

    The blockers are those of ``oracle.release_gate`` (kept in step by a test) minus ``no-bite``, computed from the snapshot's model
    without loading the project a second time."""
    from . import oracle
    from .rules import or_

    model = snap.model
    if not model.get("nodes"):
        return {"applicable": False, "ok": True, "detail": "nothing authored yet"}
    sha8 = snap.sha8
    view = oracle.make_view(snap.paths)
    items = or_.collect_items(model)
    view.bind(items)
    cases = {c.get("caseId"): c for c in model.get("cases", ())}
    case_ids = set(cases)
    kinds: dict = {}
    first: dict = {}
    revised_of = {"cases": 0, "interventions": 0}

    def add(kind: str, ident: str) -> None:
        kinds[kind] = kinds.get(kind, 0) + 1
        first.setdefault(kind, ident)

    states = {}
    for key, item in items.items():
        state, why = view.state_of(item)
        states[key] = (item, state, why)
        entry = view.entries.get(key) or {}
        if state == "unregistered":
            add("unsealed", item.id)
            continue
        if item.kind == or_.CASE:
            if or_.coverage_gap(cases.get(item.id, {})) is not None:
                add("vacuous", item.id)
            bite = entry.get("bite")
            if isinstance(bite, Mapping) and bite.get("sha") == sha8 and bite.get("holds") and (not bite.get("total") or not bite.get("killed")):
                add("vacuous", item.id)
        if state == "revised":
            add("revised", item.id)
            revised_of["cases" if item.kind == or_.CASE else "interventions"] += 1
        last = entry.get("last")
        fresh = isinstance(last, Mapping) and last.get("sha") == sha8
        if item.kind == or_.INTERVENTION:
            wanted = {c for c in next((i.get("appliesTo", ()) for i in model.get("interventions", ()) if i.get("interventionId") == item.id), ())
                      if c in case_ids}
            fresh = fresh and wanted <= {c for c, p in (entry.get("pairs") or {}).items() if isinstance(p, Mapping) and p.get("sha") == sha8}
        if not fresh:
            add("no-verdict", item.id)
        elif last.get("pass") is False:
            add("mismatch", item.id)
    counts = oracle.counts_of(states)
    lang = snap.config.languages[0] if snap.config.languages else "en-US"
    badge = oracle.attestation(lang, counts, view)
    if badge["class"] == "unverified-provenance" and counts["total"] and counts["confirmed"] < counts["total"]:
        add("provenance", "")
    out = {"applicable": True, "ok": not any(k in kinds for k in ORACLE_DONE_KINDS), "requires": ("4D", "5D"), "kinds": dict(kinds),
           "revised": revised_of}
    if out["ok"]:
        out["detail"] = f"{counts['sealed']} sealed, {counts['confirmed']} confirmed"
        return out
    kind = next(k for k in ORACLE_DONE_KINDS if k in kinds)
    ident = f" ({first[kind]})" if first.get(kind) else ""
    out["detail"] = f"{kinds[kind]} {kind}{ident}: {_ORACLE_ADVICE[kind]}"
    out["short"] = f"{kinds[kind]} {kind}"
    if ("revised" in kinds or "provenance" in kinds) and _sheet_is_current(snap):
        # the user is asked only when there IS a sheet to approve: before that the way on is the build (phase build, not needs-user)
        n = kinds.get("revised", 0) + kinds.get("provenance", 0)
        out["needsUser"] = f"approve the oracle sheet atlas/dist/oracle-sheet.md ({n} edited expectation(s))"
        out["needsHow"] = ("hand the sheet to the user: they tick lines or keep APPROVE-ALL in atlas/oracle-confirm.txt "
                           "(you never write it); then run simulate")
    return out


def _sheet_is_current(snap: "Snapshot") -> bool:
    """``dist/`` is the current build of this model and ``oracle-sheet.md`` (written by ``gemarc_atlas_build``) exists."""
    _info, _sha, problem = _page_state(snap)
    return not problem and (snap.paths.atlas / DIST_DIR / "oracle-sheet.md").is_file()


def _stamp_matches(entry, row: Mapping) -> bool:
    from . import evidence as ev

    return isinstance(entry, Mapping) and ev._entry_matches(entry, row)  # noqa: SLF001 - the verifier's own identity test


def claims_unverified(model: Mapping, entries: Mapping) -> tuple:
    """``(implemented claims, ids of those without a verified anchor)`` for the as-is evidence policy (design 8.2): an Implemented node or
    edge needs source-code evidence, and every such row must have a stamp entry for the SAME file and anchor (the stamp is the verifier's
    record of a PASS)."""
    by_id = {e.get("evidenceId"): e for e in model.get("evidences", ()) if isinstance(e, dict)}
    total, bad = 0, []
    for kind in ("nodes", "edges"):
        for item in model.get(kind, ()):
            if not isinstance(item, dict) or item.get("verification") != "Implemented":
                continue
            total += 1
            code = [i for i in item.get("evidenceIds", ()) if by_id.get(i, {}).get("sourceKind") == "source-code"]
            if not code or any(not _stamp_matches(entries.get(i), by_id[i]) for i in code):
                bad.append(str(item.get("id") or "?"))
    return total, bad


def _gate_evidence(snap: "Snapshot") -> dict:
    """As-is: every Implemented claim rests on a stamped source-code anchor. To-be has no such claim (the ceiling is Designed)."""
    from . import evidence as ev

    if snap.mode != "asis":
        return {"applicable": False, "ok": True, "detail": "to-be: no Implemented claim"}
    claims = [i for k in ("nodes", "edges") for i in snap.model.get(k, ()) if isinstance(i, dict) and i.get("verification") == "Implemented"]
    if not claims:
        return {"applicable": False, "ok": True, "detail": "no Implemented claim"}
    try:
        entries = ev.load_stamp(snap.paths.atlas).get("entries") or {}
    except ev.EvidenceError:
        return {"applicable": True, "ok": False, "detail": "the evidence stamp is unreadable: run gemarc_atlas_evidence again"}
    total, bad = claims_unverified(snap.model, entries)
    if not bad:
        return {"applicable": True, "ok": True, "detail": f"{total} Implemented claim(s) verified"}
    out = {"applicable": True, "ok": False, "short": f"{len(bad)}/{total} unverified",
           "detail": f"{len(bad)}/{total} Implemented claims lack a verified anchor ({bad[0]}): run gemarc_atlas_evidence, or lower verif="}
    missing = _unmapped_aliases(snap, bad, entries)
    if missing:                      # no source root exists for any of them: the AI cannot make progress, the operator or the user decides
        out["needsUser"] = (f"no evidence root for {', '.join(missing[:3])}: the operator sets GEMARC_ATLAS_ROOTS {missing[0]}=<dir>, "
                            f"or the user accepts a lower verif= for {len(bad)} claim(s)")
        out["needsHow"] = "without a root, evidence skips every row; after the operator sets it run gemarc_atlas_evidence, or lower verif= once the user agrees"
    return out


def _unmapped_aliases(snap: "Snapshot", bad: list, entries: Mapping) -> list:
    """The repository aliases that have NO available root among the code evidence rows that the unverified claims still lack a stamp for.
    A row of such an alias can only be SKIPPED, never verified, so no amount of work by the AI turns it green (an empty list when every
    such alias has a root, or when the roots cannot be read)."""
    from . import roots as roots_mod

    try:
        table = roots_mod.roots_from_env()
    except roots_mod.RootsError:
        return []
    if snap.config.repos:
        table = {k: v for k, v in table.items() if k in snap.config.repos}
    by_id = {e.get("evidenceId"): e for e in snap.model.get("evidences", ()) if isinstance(e, dict)}
    wanted = set(bad)
    missing = []
    for kind in ("nodes", "edges"):
        for item in snap.model.get(kind, ()):
            if not isinstance(item, dict) or str(item.get("id")) not in wanted:
                continue
            for ident in item.get("evidenceIds", ()):
                row = by_id.get(ident) or {}
                alias = row.get("repository")
                if row.get("sourceKind") != "source-code" or _stamp_matches(entries.get(ident), row) or alias in missing:
                    continue
                if not (alias in table and roots_mod.root_available(table[alias])):
                    missing.append(alias)
    return [str(a) for a in missing]


def _gate_docs(snap: "Snapshot") -> dict:
    """The derived documents print no NOT-AUTHORED slot (a release blocker, DC-L-004)."""
    from .derive import docbuild

    if _structure_open(snap):
        return {"applicable": False, "ok": True, "detail": "waits for the structure"}
    lang = snap.config.languages[0] if snap.config.languages else "en-US"
    total = 0
    for level in docbuild.LEVELS:
        outline = docbuild.doc_outline(snap.model, None, level, lang)
        if not outline.get("ok"):
            return {"applicable": True, "ok": False, "detail": f"the {level} document cannot be derived: {outline.get('code', '')}"}
        total += int(outline.get("markers") or 0)
    if total:
        return {"applicable": True, "ok": False, "short": f"{total} NOT-AUTHORED", "detail": f"{total} NOT-AUTHORED slot(s) in the documents: gemarc_atlas_doc shows where"}
    return {"applicable": True, "ok": True, "detail": "no NOT-AUTHORED slot"}


def _page_state(snap: "Snapshot") -> tuple:
    """``(info, page sha, problem)``: ``problem`` is empty when ``dist/`` is the current, unedited build of the snapshot's model."""
    dist = snap.paths.atlas / DIST_DIR
    page, info_path = dist / "index.html", dist / "build-info.json"
    if not page.is_file() or not info_path.is_file():
        return None, "", "no built page yet: run gemarc_atlas_build"
    try:
        info = json.loads(read_bytes(info_path).decode("utf-8-sig"))
    except (OSError, ValueError):
        return None, "", "dist/build-info.json is unreadable: run gemarc_atlas_build"
    if not isinstance(info, dict):
        return None, "", "dist/build-info.json is unreadable: run gemarc_atlas_build"
    if info.get("modelSha") != am.model_sha(snap.model):
        return info, "", "dist/ is the build of an older model: run gemarc_atlas_build"
    try:
        sha = _sha256_file(page)
    except OSError:
        return info, "", "dist/index.html cannot be read: run gemarc_atlas_build"
    if (info.get("page") or {}).get("sha256") != sha:
        return info, sha, "dist/index.html was changed after the build: run gemarc_atlas_build"
    if info.get("docs") is None:
        return info, sha, "the build skipped the documents (docs=false): run gemarc_atlas_build"
    return info, sha, ""


def _gate_build(snap: "Snapshot") -> dict:
    """``dist/`` is the build of this very model: page unedited, documents written."""
    if _structure_open(snap):
        return {"applicable": False, "ok": True, "detail": "waits for the structure"}
    info, _sha, problem = _page_state(snap)
    if problem:
        return {"applicable": True, "ok": False, "short": "no current build", "detail": problem}
    return {"applicable": True, "ok": True, "detail": f"dist/index.html {(info.get('page') or {}).get('bytes', 0)} bytes"}


def read_gate_stamp(paths: Paths) -> "dict | None":
    """``dist/.verify/gate-stamp.json``: the browser verdict bound to a page (``None`` when absent or not this schema)."""
    data = _read_json(paths.atlas / DIST_DIR / VERIFY_DIR / GATE_STAMP_NAME)
    return data if isinstance(data, dict) and data.get("schema") == GATE_STAMP_SCHEMA else None


def write_gate_stamp(paths: Paths, *, model_sha8: str, page_sha256: str, verdict: Mapping) -> None:
    """Bind a browser verdict to the page it was run on (so a PASS of an earlier build can never satisfy the gate). No timestamp."""
    doc = {"schema": GATE_STAMP_SCHEMA, "modelSha8": model_sha8, "pageSha256": page_sha256, "status": verdict.get("status", ""),
           "widths": [{"width": w.get("width"), "status": w.get("status")} for w in verdict.get("widths", ())]}
    atomic_write(paths.atlas / DIST_DIR / VERIFY_DIR / GATE_STAMP_NAME, (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def read_waiver(paths: Paths) -> str:
    """The reason text of ``atlas/waive-browser.txt`` (the user's file); empty when there is none."""
    path = paths.atlas / WAIVER_NAME
    try:
        if not path.is_file() or path.stat().st_size > WAIVER_MAX_BYTES:
            return ""
        return " ".join(read_bytes(path).decode("utf-8-sig").split())
    except (OSError, ValueError):
        return ""


def _gate_browser(snap: "Snapshot") -> dict:
    """The browser gate passed on THIS page, or the user waived it. A missing browser is a loud SKIP that blocks, and the way out is
    the user's (install a browser, or write ``atlas/waive-browser.txt``); a FAIL is never covered by the waiver."""
    if _structure_open(snap):
        return {"applicable": False, "ok": True, "detail": "waits for the structure"}
    _info, sha, problem = _page_state(snap)
    if problem:
        return {"applicable": True, "ok": False, "short": "waits for build", "detail": "waits for a current build"}
    stamp = read_gate_stamp(snap.paths)
    current = bool(stamp and stamp.get("modelSha8") == snap.sha8 and stamp.get("pageSha256") == sha)
    state = stamp.get("status") if current else None
    waiver = read_waiver(snap.paths)
    if state == "PASS":
        passed = {w.get("width") for w in stamp.get("widths", ()) if isinstance(w, dict) and w.get("status") == "PASS"}
        short = [w for w in REQUIRED_WIDTHS if w not in passed]
        if short:       # a verify run at one width only (the model chose widths=[1024]) does not verify the page
            return {"applicable": True, "ok": False, "short": "widths missing",
                    "detail": f"the browser gate did not pass at {', '.join(str(w) for w in short)} px: run gemarc_atlas_verify with the default widths"}
        return {"applicable": True, "ok": True, "detail": "browser gate PASS on this page"}
    if state == "FAIL":
        return {"applicable": True, "ok": False, "short": "FAIL", "detail": "the browser gate FAILED on this page: fix what gemarc_atlas_verify lists, rebuild, verify"}
    if state == "INCONCLUSIVE":
        return {"applicable": True, "ok": False, "short": "inconclusive", "detail": "the browser gate was INCONCLUSIVE (a launch timed out): run gemarc_atlas_verify again when the machine is idle"}
    if waiver:
        return {"applicable": True, "ok": True, "waived": True, "detail": f"browser gate waived by the user: {waiver[:60]}"}
    if state == "SKIP":
        return {"applicable": True, "ok": False, "skipped": True, "short": "skipped", "detail": "the browser gate was SKIPPED (no browser found): the page is NOT verified",
                "needsUser": f"browser SKIPPED, the page is unverified: install Edge or Chrome, or write a reason in atlas/{WAIVER_NAME}",
                "needsHow": f"then run gemarc_atlas_verify again; you never write {WAIVER_NAME}"}
    return {"applicable": True, "ok": False, "short": "not verified", "detail": "the page has not been verified: run gemarc_atlas_verify"}


def _register_product_gates() -> None:
    for name, fn in (("oracle", _gate_oracle), ("evidence", _gate_evidence), ("docs", _gate_docs), ("build", _gate_build), ("browser", _gate_browser)):
        register_gate(name, fn, product=True)


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
    waived: list = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"done": self.done, "phase": self.phase, "next": self.next, "blockers": self.blockers, "needsUser": self.needs_user}
        if self.open_blocks:
            out["openBlocks"] = self.open_blocks
        if self.waived:
            out["waived"] = self.waived       # a user waiver is not a verification: a client reading only this must not report the gate green
        if self.counters:
            out["counters"] = self.counters
        return out


def _is_valid_record(record: Mapping) -> bool:
    try:
        from . import review
    except ImportError:  # pragma: no cover - the review lane ships in the same package
        return False
    return review.valid_record(record)


def _stale_text(snap: "Snapshot", record: Mapping) -> str:
    try:
        from . import review

        found = review.record_state(snap, record)
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return "the model changed after the review"
    new = found.get("newBlock") or []
    return ("new BLOCK finding(s) " + ", ".join(str(x) for x in new)) if new else "the model changed after the review"


def _clip(text: str, limit: int = LINE_BYTES) -> str:
    """At most ``limit`` UTF-8 bytes. A longer text is cut at a word boundary and ends in `` ...`` (never mid-word without a mark)."""
    text = " ".join(str(text).split())
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    head = raw[: limit - 4].decode("utf-8", "ignore")
    cut = head.rfind(" ")
    if cut >= len(head) // 2:
        head = head[:cut]
    return head.rstrip(" ,;:") + " ..."


def _clip_line(text: str) -> str:
    """A ``next`` line: the questions for the user (``ask the user: ...``) get the longer limit, they are read out to the user whole."""
    return _clip(text, NEEDS_LINE_BYTES + len("ask the user: ")) if text.startswith("ask the user: ") else _clip(text)


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


def _needs_user(snap: Snapshot, oracle: Mapping, oracle_applicable: bool, gates: "Mapping | None" = None) -> list:
    """The decisions only the user (or the operator) can make, as whole sentences meant to be read out to the user. What the AI does
    afterwards is ``_needs_how`` (it goes into ``next``, never into this list)."""
    model, out = snap.model, []
    texts = saved_text(snap.paths)
    # In the product (MCP) the driver saves the requirement text itself right after init: an empty project does not ask the user yet.
    fresh = not snap.files and not snap.red and product_gates_active()
    if snap.mode == "tobe" and not _user_texts(snap.paths) and not fresh:
        out.append("requirement facts: no requirement text is saved under atlas/user/ yet; please provide the requirement text (to-be cannot be invented)")
    done = _tbd_resolved(model, texts)
    open_tbds = [t for t in model.get("tbds", ()) if t.get("id") not in done]
    for t in open_tbds[:3]:
        out.append(_clip(f"decide TBD {t.get('id')}: {t.get('title')}", NEEDS_LINE_BYTES))
    if len(open_tbds) > 3:
        out.append(f"{len(open_tbds) - 3} more open TBDs: please decide them too")
    gone = vanished_tbds(snap)
    if gone:
        out.append(_clip(f"TBD {_some(gone)} was deleted without a user decision: restore the row, or ask the user to void it (policy by=user src=<id>)",
                         NEEDS_LINE_BYTES))
    own = (gates or {}).get("oracle", {}).get("needsUser")
    if own:
        out.append(own)                      # the real oracle gate: only EDITED (revised) expectations need the user
    elif oracle_applicable and "kinds" not in (gates or {}).get("oracle", {}) and oracle["unconfirmed"]:
        out.append(f"oracle confirmation: {oracle['unconfirmed']} expectation(s) wait for the user's approval of the oracle sheet")
    for name in ("evidence", "browser"):
        extra = (gates or {}).get(name, {}).get("needsUser")
        if extra:
            out.append(extra)                # no evidence root / no browser: the operator or the user must act
    return out


def _needs_how(snap: Snapshot, gates: "Mapping | None" = None) -> list:
    """What the AI does once the user has decided: lines for ``next`` in phase needs-user (the questions themselves are ``needsUser``)."""
    out = []
    texts = saved_text(snap.paths)
    done = _tbd_resolved(snap.model, texts)
    if any(t.get("id") not in done for t in snap.model.get("tbds", ())):
        out.append("TBD answered: save the user's words to atlas/user/<name>.txt, then a policies row by=user src=<tbd id> quoting them")
    for name in ("oracle", "evidence", "browser"):
        how = (gates or {}).get(name, {}).get("needsHow")
        if how:
            out.append(how)
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
    snap.report_cache = report
    oracle = _oracle_state(snap)
    gates = {name: _gate(name, snap) for name in GATE_NAMES}
    oracle_applicable = gates["oracle"]["applicable"]
    dims = dimensions(snap, report, oracle)
    waiting = gates["oracle"].get("revised") or {}
    for dim, key in (("4D", "cases"), ("5D", "interventions")):         # an edited expectation that waits for the user is registered work, not a gap
        if oracle_applicable and not dims[dim]["ready"] and waiting.get(key):
            dims[dim] = {"ready": True, "why": f"{waiting[key]} edited expectation(s) wait for the user"}
    required = ALWAYS_REQUIRED + (tuple(gates["oracle"].get("requires") or ("4D", "5D")) if oracle_applicable else ())
    review = read_review(paths)
    review_ok = review_is_current(review, model) or _review_accepts(snap, review)
    errors = snap.check.errors
    needs = _needs_user(snap, oracle, oracle_applicable, gates)
    # A record the review tool wrote (it carries blockKeys) is the ONE review: when new BLOCK findings made it stale, the way back
    # is fixing them, never a second review (``record`` would refuse it).
    stale_record = bool(review) and not review_ok and isinstance(review.get("blockKeys"), list) and _is_valid_record(review)
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
        blockers.append(f"gate-{name}" + (f":{gates[name]['short']}" if gates[name].get("short") else ""))   # a whole token, never a cut sentence
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
    elif stale_record:
        phase = "author"
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
        waived = [n for n in applicable if gates[n].get("waived")]
        green = [n for n in applicable if n not in waived]
        checked = f"check full is clean, {required[0]}-{required[-1]} ready" + (f", gates green: {', '.join(green)}" if green else "; no generate gate is available yet")
        lines.append(f"done: {checked}; the review is recorded")
        for name in waived:       # a waiver is not a pass: the final report must say so
            lines.append(_clip(f"{name} gate WAIVED by the user, the page is UNVERIFIED: " + str(gates[name]["detail"]).partition(": ")[2]))
        if open_blocks:
            lines.append(_clip("open BLOCK (acknowledged at review, tell the user): " + ", ".join(open_blocks[:4])
                               + " (gemarc_atlas_review action=report shows them)"))
        waiting = oracle["unconfirmed"] if oracle_applicable and "kinds" in gates["oracle"] else 0
        if waiting:
            lines.append(_clip(f"{waiting} expectation(s) await the user: hand atlas/dist/oracle-sheet.md (final report)"))
    elif phase == "needs-user":
        lines.extend(_clip_line("ask the user: " + x) for x in needs[:2])
        lines.extend(_needs_how(snap, gates)[:2])
    elif phase == "review":
        lines.append("review: authoring is complete; call gemarc_atlas_review (action=report), fix the findings once, then action=record")
    elif phase == "build":
        lines.extend(_clip(f"{n}: {gates[n]['detail'] or 'not green'}") for n in red_gate[:2])
    elif stale_record:
        lines.append(_clip("review record is stale: " + _stale_text(snap, review) + " (fix with put mode=patch; NO second review)"))
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
    kept = _select([_clip_line(x) for x in lines], MAX_NEXT - 1)
    kept.append(_clip(tail))
    shown = {k: v for k, v in _counter_view(counters).items() if v}
    return State(done, phase, kept, blockers, [_clip(x, NEEDS_LINE_BYTES) for x in needs], dims, required, gates, report, review, review_ok,
                 oracle, open_blocks, shown, waived=[n for n in GATE_NAMES if gates[n].get("waived") and gates[n]["applicable"]])


_register_product_gates()
