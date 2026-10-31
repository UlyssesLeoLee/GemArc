"""Pure service functions behind the atlas tools and the CLI (design 4.1): init, put, check, status, index, brief.

Every function returns a JSON-able dict ``{"status", "authority", ...}`` and caps itself with ``fit_result``: default 4,500
bytes, ``brief`` 9,000, never more than 20 KiB; a cut reply says ``truncated: true`` and, where it pages, ``more: "offset=N"``.
Sizes are the UTF-8 bytes of ``json.dumps(reply, ensure_ascii=False)``: a binding that serialises with ``ensure_ascii=True``
makes Chinese text twice as large and must not be used for these replies.

``status``: ``"created"`` (init), ``"committed"`` or ``"invalid"`` (put), ``"ok"`` (check, status, index, brief), ``"invalid"``
(a refused request: ``code`` + ``message``, nothing written) and ``"error"`` (an unexpected failure; ``GEMARC_ATLAS_DEBUG=1``
re-raises it). The functions take the workspace as first argument (``project.get_workspace()``); ``check`` with inline
``text`` needs none. Writes need ``GEMARC_ALLOW_WRITE=1`` (``Workspace.writable``); ``check``, ``status``, ``index`` and ``brief``
never write. Standard library only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..paths import GemArcError, Workspace
from . import brief as brief_mod
from . import diag as dg
from . import indexer
from . import model as am
from . import oracle as oracle_mod
from . import project as pj
from . import slices as sl
from . import slicecheck
from . import status as status_mod

AUTHORITY = "local"
DEFAULT_CAP = 4500
BRIEF_CAP = 9000
HARD_CAP = 20 * 1024
INDEX_CAP = 4500
EXPANDED_CAP = 3000
INLINE_NAME = "inline"
E_ARG = "GEMARC_ATLAS_TOOL_001"
E_EMIT = "GEMARC_ATLAS_TOOL_002"
_PROTECTED = frozenset({"status", "authority", "truncated", "more", "code", "message", "view", "scope", "done", "phase", "slice"})


# ---------------------------------------------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------------------------------------------


def reply_bytes(reply: Mapping) -> int:
    """UTF-8 size of the reply as the tools send it."""
    return len(json.dumps(reply, ensure_ascii=False).encode("utf-8"))


def invalid(code: str, message: str, **extra) -> dict:
    """The reply of a refused request (nothing was written)."""
    out = {"status": "invalid", "authority": AUTHORITY, "code": code, "message": message,
           "diagnostics": [{"id": code, "severity": "ERROR", "message": message, "entityId": ""}]}
    out.update(extra)
    return out


def _shrink(out: dict, key: str) -> bool:
    """Make the value at ``key`` smaller; ``False`` when it cannot get smaller."""
    value = out[key]
    if isinstance(value, list) and value:
        out[key] = value[: len(value) // 2]
        return True
    if isinstance(value, str) and len(value) > 40:
        cut = value[: int(len(value) * 0.7)]
        if "\n" in cut:
            cut = cut[: cut.rindex("\n")]
        out[key] = cut
        return True
    if isinstance(value, dict):
        nested = max((k for k, v in value.items() if isinstance(v, (list, str)) and v), key=lambda k: len(json.dumps(value[k], ensure_ascii=False)), default=None)
        if nested is None:
            return False
        copy = dict(value)
        if _shrink_in(copy, nested):
            out[key] = copy
            return True
    return False


def _shrink_in(container: dict, key: str) -> bool:
    return _shrink(container, key)


def fit_result(result: Mapping, cap: int = DEFAULT_CAP, hard: int = HARD_CAP, *, offset: int = 0, items_key: str = "") -> dict:
    """``result`` within ``cap`` bytes (never above ``hard``): the largest list or text is cut, ``truncated`` is set.

    ``items_key`` names the paged list (or the newline-separated text) of the reply: ``more`` then says ``offset=<offset + kept>``.
    """
    cap = min(int(cap), hard)
    if reply_bytes(result) <= cap:
        return dict(result)
    out = dict(result)
    out["truncated"] = True
    guard = 0
    while reply_bytes(out) > cap and guard < 400:
        guard += 1
        candidates = [(len(json.dumps(v, ensure_ascii=False)), k) for k, v in out.items() if k not in _PROTECTED and isinstance(v, (list, str, dict))]
        for _size, key in sorted(candidates, reverse=True):
            if _shrink(out, key):
                break
        else:
            break
    if reply_bytes(out) > cap:  # nothing left to cut: keep the envelope only
        out = {k: out[k] for k in ("status", "authority", "code", "message", "view") if k in out}
        out["truncated"] = True
    if items_key and items_key in out:
        value = out[items_key]
        kept = len(value) if isinstance(value, list) else len(value.splitlines()) if isinstance(value, str) else 0
        out["more"] = f"offset={offset + kept}"
    return out


def _guard(fn: Callable[[], dict]) -> dict:
    """Run a tool body; refusals become ``invalid`` replies, unexpected failures ``error`` replies (type name only)."""
    try:
        return fn()
    except sl.SliceError as ex:
        return invalid(ex.code, ex.message, **ex.extra)
    except GemArcError as ex:
        return invalid(ex.code, ex.message)
    except (indexer.IndexError_, brief_mod.BriefError) as ex:
        return invalid(E_ARG, str(ex))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError, MemoryError) as ex:
        if os.environ.get("GEMARC_ATLAS_DEBUG") == "1":
            raise
        return {"status": "error", "authority": AUTHORITY, "code": "GEMARC_INTERNAL_001", "message": type(ex).__name__,
                "diagnostics": [{"id": "GEMARC_INTERNAL_001", "severity": "ERROR", "message": type(ex).__name__, "entityId": ""}]}


def _loaded(ws: Workspace, project: str):
    paths = pj.project_paths(ws, project)
    return paths, pj.load_config(paths)


BRIEF_LANGS = ("", "zh-CN", "en-US", "ja-JP")


def _offset(value) -> int:
    """A paging offset: a whole number, never negative (a negative offset used to be read as 0, which looks like success)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise GemArcError(E_ARG, "offset is a whole number (the offset=N of the previous reply)") from None
    if number < 0:
        raise GemArcError(E_ARG, "offset is not negative")
    return number


def _strs(value, name: str) -> list:
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, Sequence):
        return [str(x) for x in value]
    raise GemArcError(E_ARG, f"{name} must be a list of strings")


# ---------------------------------------------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------------------------------------------


def init(ws: Workspace, project: str, title: str = "", mode: str = "asis", languages: "Sequence[str] | str" = ("zh-CN",),
         profile: str = "software", repos: "Sequence[str] | str" = ()) -> dict:
    """Create ``<workspace>/<project>/atlas`` (``atlas.toml``, ``slices/``, ``.atlas/``, ``.gitignore``). Refuses to overwrite."""
    def body() -> dict:
        ws.writable()
        langs, aliases = _strs(languages, "languages"), _strs(repos, "repos")
        label = title or project
        pj.validate_init_args(project, label, mode, langs, profile, aliases)
        paths = pj.project_paths(ws, project)
        if paths.atlas.exists():
            return invalid(pj.E_EXISTS, f"project {project} already has an atlas directory; init never overwrites it (continue with status view=next)",
                           path=str(paths.atlas), next=["resume: status view=next; saved texts go under <path>/user/"])
        pj.refuse_link(paths.atlas)   # a dangling link or junction "does not exist" for exists() and would crash the mkdir below
        pj.create_layout(paths, pj.render_config(project, label, mode, langs, profile, aliases))
        first = "save the requirement text verbatim to atlas/user/<name>.txt (the directory exists), then " if mode == "tobe" else ""
        reply = {"status": "created", "authority": AUTHORITY, "project": project, "path": str(paths.atlas), "mode": mode,
                 "created": ["atlas.toml", ".gitignore", "slices/", "user/", ".atlas/red/", ".atlas/history/"],
                 "next": [f"read the work order: brief (section=all, lang={langs[0]})",
                          first + "put 00-space (buildings, floors, rooms), then 10-structure slices of about 11 nodes each",
                          "after each put: read the next lines of its reply; status view=next after a red draft, on resume, and about every 5 puts"]}
        inside = _workspace_inside_root(ws)
        if inside:
            reply["next"].insert(0, f"WARNING: the workspace is inside the evidence root '{inside}' (the repository being analysed): the atlas files would sit in the code they describe; "
                                    "the operator should move GEMARC_WORKSPACE_ROOT outside it")
        return reply
    return _guard(body)


def _workspace_inside_root(ws: Workspace) -> str:
    """The alias of an evidence root (``GEMARC_ATLAS_ROOTS``) that contains the workspace, or ``""``: nothing is refused, ``init`` only warns."""
    from . import roots as roots_mod

    try:
        table = roots_mod.roots_from_env()
    except roots_mod.RootsError:
        return ""
    here = ws.root
    for alias, root in table.items():
        try:
            base = Path(root).resolve(strict=False)
        except OSError:
            continue
        if here == base or base in here.parents:
            return str(alias)
    return ""


# ---------------------------------------------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------------------------------------------


def _diag_counts(check: "slicecheck.CheckResult") -> dict:
    c = dg.counts(check.diagnostics)
    return {"error": c["error"], "warn": c["warn"], "info": c["info"], "derived": c["derived"]}


def put(ws: Workspace, project: str, slice: str, text: str, mode: str = "replace", format: str = "atl", base: str = "") -> dict:
    """Validate and store one slice (``slices.put_slice``): commit without ERROR, otherwise keep the text as a red draft."""
    def body() -> dict:
        ws.writable()
        paths, cfg = _loaded(ws, project)
        pj.check_confined(ws, paths)
        _ensure_ledger(paths)
        out = sl.put_slice(paths, cfg.meta(), slice, text, mode=mode, fmt=format, base=base,
                           check_options={"element_templates": status_mod.element_templates()})
        reply = {"status": out.status, "authority": AUTHORITY, "slice": out.slice, "format": out.fmt, "sha8": out.sha8,
                 "counts": out.counts, "diagnostics": out.check.render("text")}
        quiet = _diag_counts(out.check)
        if any(quiet.values()):
            reply["diagnostic_counts"] = quiet
        if out.check.pending:
            reply["pending"] = {kind: len(ids) for kind, ids in out.check.pending.items()}
        if out.patch is not None:
            reply["patch"] = out.patch.as_dict()
        if cfg.mode == "tobe" and mode == "replace" and not _states_mode(text, format):
            reply["note"] = "this project is mode=tobe: start the slice with '#atlas 1 mode=tobe' (the project mode applies either way)"
        note = _seal_after_put(paths, cfg, out)
        if note:
            reply["oracle"] = note
        if out.committed:
            reply["unchanged"] = out.unchanged
            reply["previous"] = out.previous or None
            if out.cleared_red:
                reply["red_draft_cleared"] = True
            if out.hook_errors:
                reply["hook_errors"] = out.hook_errors
            reply["next"] = _next_after_commit(paths, cfg)
        else:
            reply["red"] = True
            reply["draft_sha8"] = out.sha8
            reply["next"] = [f"nothing was committed; your text is the red draft of {out.slice} (sha8 {out.sha8})",
                             f"repair: put slice={out.slice} mode=patch with only the changed rows (same identity replaces, -id deletes); "
                             "more diagnostics: check slice=" + out.slice + " offset=N",
                             "fix the first listed error first: later ones are often its consequence"]
        return fit_result(reply, DEFAULT_CAP)
    return _guard(body)


def _ensure_ledger(paths: pj.Paths) -> None:
    """Make the oracle ledger exist BEFORE the first put records itself, so that a missing ledger after any put is a deletion."""
    try:
        oracle_mod.touch(paths)
    except (oracle_mod.OracleError, sl.SliceError, OSError, ValueError, KeyError, TypeError):
        pass


def _seal_after_put(paths: pj.Paths, cfg: pj.Config, out: "sl.PutOutcome") -> dict:
    """Seal the expectations a clean commit made registrable (design 7.2: only a write call seals); a put that committed nothing only
    makes sure the ledger file exists. A failure here never fails the put: the next write call seals what is waiting."""
    try:
        if not out.committed:
            oracle_mod.touch(paths)
            return {}
        snap = status_mod.load_snapshot(paths, cfg, mode="slice")
        return oracle_mod.seal(paths, snap.model).summary()
    except oracle_mod.OracleError as ex:
        return {"error": ex.code}
    except (sl.SliceError, OSError, ValueError, KeyError, TypeError):
        return {"error": "ledger-not-written"}


def _states_mode(text: str, fmt: str) -> bool:
    """Whether the header line of a slice text carries ``mode=``."""
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip().lstrip("\ufeff")
        if stripped and not stripped.startswith("//"):
            return "mode" in stripped if (stripped.startswith("#atlas") or stripped.startswith('{"_atlas"')) else True
    return True


def _next_after_commit(paths: pj.Paths, cfg: pj.Config) -> list:
    """The one line the AI acts on after a clean commit (the full list is ``status view=next``, asked for when it is needed)."""
    try:
        state = status_mod.evaluate(status_mod.load_snapshot(paths, cfg))
    except (OSError, ValueError, KeyError, TypeError):
        return ["status view=next"]
    stop = [x for x in state.next if x.startswith("0 STOP")]
    return (stop or state.next)[:1] if state.next else ["status view=next"]


# ---------------------------------------------------------------------------------------------------------------
# simulate (design 4.1, section 7: blind verdicts on sealed expectations)
# ---------------------------------------------------------------------------------------------------------------

SIMULATE_CAP = 3000


def simulate(ws: Workspace, project: str, case: str = "", intervention: str = "", detail: str = "count") -> dict:
    """Blind verdicts for the registered expectations (``oracle.run_verdicts``): seals what can be sealed first, so it is a write call.

    ``detail="count"`` (default) says pass or fail and HOW MANY asserted nodes differ, never which; ``ids`` names them and ``trace``
    adds a bounded trace summary: both are reveals, recorded in the oracle ledger (an expectation edited afterwards is ``revised``).
    A case or pair whose expectation is not registered gets ``OR-000`` and no verdict. The reply stays within 3 KB.
    """
    def body() -> dict:
        try:
            reply = oracle_mod.run_verdicts(ws, project, case, intervention, detail)
        except oracle_mod.OracleError as ex:
            return fit_result(invalid(ex.code, ex.message, **ex.extra), SIMULATE_CAP)     # the 3 KB cap holds for refusals too
        return fit_result(reply, SIMULATE_CAP)
    return _guard(body)


def _oracle_call(fn, *args, cap: int = DEFAULT_CAP) -> dict:
    def body() -> dict:
        try:
            reply = fn(*args)
        except oracle_mod.OracleError as ex:
            return fit_result(invalid(ex.code, ex.message, **ex.extra), cap)
        return fit_result({"status": "ok", "authority": AUTHORITY, **reply}, cap)
    return _guard(body)


def oracle_bite(ws: Workspace, project: str) -> dict:
    """The build-style oracle run: verdicts, up to 12 mutants per case that held (OR-004, ``bite``) and the guard scan (SM-006)."""
    return _oracle_call(oracle_mod.bite_run, ws, project)


def oracle_sheet(ws: Workspace, project: str, lang: str = "") -> dict:
    """Write ``atlas/dist/oracle-sheet.md`` (one line per unconfirmed expectation plus ``APPROVE-ALL``); the confirmation file is never written."""
    blocked = _guard(lambda: confine_dist(ws, project) or {"status": "ok"})
    if blocked.get("status") != "ok":
        return blocked
    reply = _oracle_call(oracle_mod.write_sheet, ws, project, lang)
    if reply.get("status") == "ok":
        _neutralise_sheet(ws, project)
    return reply


def _neutralise_sheet(ws: Workspace, project: str) -> None:
    """Model text reaches ``oracle-sheet.md`` verbatim, and a Markdown viewer may render raw HTML in it: escape ``<`` and ``>`` (the ids, hashes
    and the APPROVE-ALL line never contain them, so the user's confirmation still parses)."""
    target = pj.project_paths(ws, project).atlas / "dist" / "oracle-sheet.md"
    try:
        data = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return
    clean = data.replace("<", "&lt;").replace(">", "&gt;")
    if clean != data:
        sl.atomic_write(target, clean.encode("utf-8"))


def oracle_gate(ws: Workspace, project: str) -> dict:
    """The oracle part of the release gate: blockers, counts and the attestation badge (read-only)."""
    def read(*_args):
        return oracle_mod.release_gate(ws, project)
    return _oracle_call(read, ws, project)


# ---------------------------------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------------------------------


def _filter(diags: Sequence, rule: str, file: str = "") -> list:
    out = list(diags)
    if rule:
        out = [d for d in out if d.rule.startswith(rule.upper())]
    if file:
        out = [d for d in out if d.at.rpartition(":")[0] == file]
    return out


def _check_reply(result: "slicecheck.CheckResult", *, scope: str, authority: str, mode: str, rule: str, offset: int, fmt: str,
                 file: str = "", extra: "Mapping | None" = None) -> dict:
    diags = _filter(result.diagnostics, rule, file)
    derived = sum(d.derived for d in diags)
    rendered = dg.render(diags, fmt=fmt, offset=offset, pending=result.pending if not rule and not file else None,
                         pending_bind=result.pending_bind if not rule and not file else 0, derived=derived)
    errors = [d for d in diags if d.severity == dg.ERROR and not d.derived]
    reply = {"status": "ok", "authority": authority, "scope": scope, "mode": mode, "clean": not errors,
             "counts": _diag_counts(CheckLike(diags)), "diagnostics": json.loads(rendered) if fmt == "json" else rendered}
    if rule:
        reply["rule"] = rule.upper()
    if result.pending and not rule:
        reply["pending"] = {kind: len(ids) for kind, ids in result.pending.items()}
    reply.update(extra or {})
    return reply


class CheckLike:
    """The ``diagnostics`` of a filtered view, shaped like a ``CheckResult`` for ``_diag_counts``."""

    def __init__(self, diagnostics):
        self.diagnostics = list(diagnostics)


def check(ws: "Workspace | None" = None, project: str = "", text: str = "", slice: str = "", mode: str = "slice", rule: str = "",
          offset: int = 0, format: str = "text") -> dict:
    """Validate, never writing: a project (``slice`` narrows to one slice) or inline ``text`` (exactly one of the two).

    ``mode="slice"`` checks the slice(s) against the committed rest (a forward reference is pending); ``mode="full"`` is the
    closed world of the whole project (every unresolved reference is an ERROR and every red draft is ST-009). With ``slice``
    set, the red draft of that slice is checked when there is one (that is the text a ``put`` rejected); ``offset`` pages
    through the diagnostics the way ``put`` said (``more: offset=N``); ``rule`` keeps one rule id or family (``RF``). A full check runs wave 1
    and wave 2 (``status.FULL_WAVE_SET``): the wave-2 rules (``CS``, ``SM``, ``IV``, ``OR``, ``PT``) need the engine and read the oracle
    ledger, read-only: ``check`` never seals, never runs a verdict and writes nothing.
    """
    def body() -> dict:
        if bool(project) == bool(text):
            raise GemArcError(E_ARG, "pass exactly one of project and text")
        if mode not in ("slice", "full"):
            raise GemArcError(E_ARG, "mode is 'slice' or 'full'")
        if format not in ("text", "json"):
            raise GemArcError(E_ARG, "format is 'text' or 'json'")
        offset_ = _offset(offset)
        if text:
            if len(text.encode("utf-8", errors="replace")) > sl.HARD_TEXT_BYTES:
                raise GemArcError(sl.E_LIMIT, f"inline text is limited to {sl.HARD_TEXT_BYTES // 1024} KiB")
            name = INLINE_NAME + (".jsonl" if text.lstrip().startswith("{") else ".atl")
            result = slicecheck.check([(name, text)], None, mode=mode, incoming=[name] if mode == "slice" else None,
                                      element_templates=status_mod.element_templates())
            return fit_result(_check_reply(result, scope="grammar+intra-slice", authority="advisory", mode=mode, rule=rule, offset=offset_,
                                           fmt=format, extra={"next": ["inline text sees only itself: references to other slices are pending; "
                                                                       "check the project to validate them"]}), DEFAULT_CAP, offset=offset_, items_key="")
        if ws is None:
            raise GemArcError(E_ARG, "a workspace is needed to check a project")
        paths, cfg = _loaded(ws, project)
        red_names = [r.fname for r in sl.list_red(paths)]
        file, source = "", "committed"
        if mode == "slice" and slice:
            sl.check_name(slice)
            target = sl.find_red(paths, slice) or sl.find_slice(paths, slice)
            if target is None:
                raise sl.SliceError(sl.E_NOTHING, f"no slice {slice} (committed or red draft)")
            file = target.fname
            source = "red draft" if target.path.parent == paths.red else "committed"
            rest = [(s.fname, s.data()) for s in sl.list_slices(paths) if s.name != slice]
            result = slicecheck.check(rest + [(target.fname, target.data())], cfg.meta(), mode="slice", incoming=[target.fname],
                                      element_templates=status_mod.element_templates())
            _add_registration(result, paths, only=target.fname)
        else:
            snap = status_mod.load_snapshot(paths, cfg, mode=mode, wave_set=WAVE2_SET if _asks_wave2(rule) else None)
            result = snap.check
            if mode == "slice":
                _add_registration(result, paths, only=sl.find_slice(paths, slice).fname if slice and sl.find_slice(paths, slice) else "")
            if slice:
                sl.check_name(slice)
                found = sl.find_slice(paths, slice)
                file = found.fname if found else f"{slice}.atl"
        scope = f"slice:{slice}" if slice else "project"
        extra = {"red_drafts": red_names} if red_names else {}
        if slice and mode == "slice":
            extra["source"] = source
        reply = _check_reply(result, scope=scope, authority="authoritative", mode=mode, rule=rule, offset=offset_, fmt=format, file=file if mode == "full" else "",
                             extra=extra)
        reply["next"] = _check_next(reply["clean"], mode, slice, bool(red_names))
        return fit_result(reply, DEFAULT_CAP)
    return _guard(body)


def _add_registration(result: "slicecheck.CheckResult", paths: pj.Paths, only: str = "") -> None:
    """OR-000 for the slice-mode check (read-only): the assertions in ``only`` (a slice file; all when empty) that no write call sealed."""
    try:
        extra = oracle_mod.registration_findings(paths, result.expansion.model, result.expansion.provenance)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    extra = [d for d in extra if not only or d.at.rpartition(":")[0] == only]
    if extra:
        result.diagnostics = sorted(list(result.diagnostics) + extra, key=dg.Diag.sort_key)


#: ``check mode=full rule=<family>`` for one of these families names wave-2 rules (the engine and oracle rules); since the default full check
#: runs wave 1 and wave 2 the set below only matters when ``status.FULL_WAVE_SET`` is set back to ``(1,)``.
WAVE2_FAMILIES = ("CS", "SM", "IV", "OR", "PT")
WAVE2_SET = (1, 2)


def _asks_wave2(rule: str) -> bool:
    return bool(rule) and rule.strip().upper()[:2] in WAVE2_FAMILIES


def _check_next(clean: bool, mode: str, slice: str, reds: bool) -> list:
    if not clean:
        where = f" slice={slice}" if slice else ""
        return [f"repair with put{where} mode=patch (only the changed rows); fix the first listed error first"]
    if reds:
        return ["a red draft exists: patch or replace it (mode=full lists the red drafts as ST-009, plus what they leave undefined)"]
    return ["status view=next" if mode == "full" else "status view=next; check mode=full when all slices are in"]


def emit_model(ws: Workspace, project: str) -> dict:
    """``check --emit-model``: run the full check and write ``atlas/out/model.json`` (canonical JSON) when it is clean."""
    def body() -> dict:
        ws.writable()
        paths, cfg = _loaded(ws, project)
        pj.check_confined(ws, paths)
        snap = status_mod.load_snapshot(paths, cfg)
        if not snap.check.ok:
            reply = _check_reply(snap.check, scope="project", authority="authoritative", mode="full", rule="", offset=0, fmt="text")
            reply.update(status="invalid", code=E_EMIT, message="the model has ERROR findings: nothing was written")
            return fit_result(reply, DEFAULT_CAP)
        issues = am.contract_issues(snap.model)
        if issues:
            return invalid(E_EMIT, f"the model breaks the contract ({len(issues)} issue(s)); first: {issues[0]['code']} {issues[0]['path']}",
                           issues=[{"code": i["code"], "path": i["path"]} for i in issues[:5]])
        paths.out.mkdir(parents=True, exist_ok=True)
        data = am.canonical_bytes(snap.model, indent=2)
        sl.atomic_write(paths.out / "model.json", data)
        return {"status": "ok", "authority": AUTHORITY, "path": str(paths.out / "model.json"), "bytes": len(data), "modelSha8": snap.sha8,
                "counts": {k: len(snap.model[k]) for k in ("nodes", "edges", "evidences", "cases", "requirements")}}
    return _guard(body)


# ---------------------------------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------------------------------

VIEWS = ("next", "report", "dims", "oracle", "evidence")


def status(ws: Workspace, project: str, view: str = "next", scope: str = "") -> dict:
    """Where the project stands: ``next`` (the completion contract), ``report`` (completeness), ``dims``, ``oracle``, ``evidence``."""
    def body() -> dict:
        if view not in VIEWS:
            raise GemArcError(E_ARG, f"view is one of {', '.join(VIEWS)}")
        paths, cfg = _loaded(ws, project)
        snap = status_mod.load_snapshot(paths, cfg)
        state = status_mod.evaluate(snap)
        base = {"status": "ok", "authority": AUTHORITY, "view": view}
        if view == "next":
            base.update(state.as_dict())
            return fit_result(_fit_next(base), DEFAULT_CAP)
        if view == "dims":
            base.update(dimensions=state.dims, required=list(state.required), gates=state.gates, mode=snap.mode)
            return fit_result(base, DEFAULT_CAP)
        if view == "oracle":
            base.update(state.oracle)
            base["note"] = "seals are written by put, simulate and build; an expectation naming an undefined node waits; the user confirms revised ones in the oracle sheet"
            return fit_result(base, DEFAULT_CAP)
        if view == "evidence":
            base.update(_evidence_view(snap))
            return fit_result(base, DEFAULT_CAP)
        report = state.report
        wanted = (scope or "").strip()
        slots = [e for e in report.entries if not wanted or e.level == wanted.upper() or e.slot.startswith(wanted)]
        rows = report.slot_summary()
        if wanted:
            rows = [r for r in rows if r[0].startswith(wanted) or any(e.slot == r[0] and e.level == wanted.upper() for e in slots)]
        lines = [f"{sev} {slot} {st} x{n}" + (": " + ", ".join(refs) + (" ..." if n > len(refs) else "") if any(refs) else "")
                 for slot, st, sev, n, refs in rows]
        base.update(counts=report.counts(), levels=report.by_level(), ready=report.ready, slots=lines)
        return fit_result(base, DEFAULT_CAP, items_key="slots")
    return _guard(body)


NEXT_CAP = 1200
NEEDS_SHOWN = 2
BLOCKERS_SHOWN = 8


def _fit_next(reply: dict, cap: int = NEXT_CAP) -> dict:
    """``status view=next`` within ``cap`` bytes (the AI reads it after every few puts): the questions to ask (up to 3, each a whole
    sentence, then a count), the first blockers (whole tokens, then a count), and as many ``next`` lines as fit (the first three always
    stay). Nothing is cut inside a sentence: ``status`` already clipped each line at a word boundary with an explicit `` ...``."""
    out = dict(reply)
    needs = list(out.get("needsUser") or [])
    if len(needs) > NEEDS_SHOWN:
        needs = needs[:NEEDS_SHOWN] + [f"+{len(needs) - NEEDS_SHOWN} more"]
    out["needsUser"] = needs
    blockers = list(out.get("blockers") or [])
    if len(blockers) > BLOCKERS_SHOWN:
        blockers = blockers[:BLOCKERS_SHOWN] + [f"+{len(blockers) - BLOCKERS_SHOWN}"]
    out["blockers"] = blockers
    lines = list(out.get("next") or [])
    while reply_bytes(out) > cap and len(lines) > 3:
        del lines[-2]  # keep the last line (dimension readiness) and the leading, highest-priority ones
        out["next"] = lines
    out["next"] = lines
    return out


def _evidence_view(snap: "status_mod.Snapshot") -> dict:
    from . import evidence as ev

    evidences = snap.model.get("evidences", [])
    try:
        stamp = ev.load_stamp(snap.paths.atlas)
    except ev.EvidenceError as ex:
        return {"evidences": len(evidences), "stamp": "unreadable", "message": str(ex)[:120]}
    entries = stamp.get("entries") or {}
    code = [e["evidenceId"] for e in evidences if e.get("sourceKind") == "source-code"]
    unstamped = [i for i in code if i not in entries]
    return {"evidences": len(evidences), "sourceCode": len(code), "stamped": len(code) - len(unstamped), "unstamped": unstamped[:8],
            "stampFile": snap.paths.stamp.is_file(),
            "note": "anchors are stamped by the evidence verifier when it is available; otherwise check the sampled anchors by hand (review report)"}


# ---------------------------------------------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------------------------------------------


def index(ws: Workspace, project: str, scope: str = "nodes", with_edges: bool = False, ids: "Sequence[str] | str" = (),
          detail: str = "line", offset: int = 0) -> dict:
    """Compact lines about the committed model (scopes: nodes, room:<id>, req:<id>, case:<id>, ids:<prefix>)."""
    def body() -> dict:
        start = _offset(offset)
        paths, cfg = _loaded(ws, project)
        model, prov = indexer.load_model(paths, cfg)
        lines = indexer.index_lines(model, prov, scope, with_edges=bool(with_edges), ids=_strs(ids, "ids") if ids else (), detail=detail)
        budget = (EXPANDED_CAP if detail == "expanded" else INDEX_CAP) - 400
        shown, used = [], 0
        for line in lines[start:]:
            cost = len(line.encode("utf-8")) + 1
            if used + cost > budget and shown:
                break
            shown.append(line)
            used += cost
        reply = {"status": "ok", "authority": AUTHORITY, "scope": scope or "nodes", "detail": detail, "total": len(lines),
                 "offset": start, "shown": len(shown), "text": "\n".join(shown)}
        if start + len(shown) < len(lines):
            reply["more"] = f"offset={start + len(shown)}"
            reply["truncated"] = True
        return fit_result(reply, EXPANDED_CAP + 400 if detail == "expanded" else INDEX_CAP, offset=start, items_key="text")
    return _guard(body)


# ---------------------------------------------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------------------------------------------


def brief(section: str = "", lang: str = "en-US") -> dict:
    """The work order (``section`` empty or ``all``: at most 9 KB) or one section (at most 3 KB), zh-CN or en-US."""
    def body() -> dict:
        if (lang or "") not in BRIEF_LANGS:
            raise GemArcError(E_ARG, "lang is zh-CN or en-US (ja-JP shows en-US)")
        text = brief_mod.render(section, lang or "en-US")
        used = brief_mod.resolve_language(lang or "en-US")
        name = (section or "all").strip().lower()
        reply = {"status": "ok", "authority": AUTHORITY, "section": name, "lang": used, "bytes": len(text.encode("utf-8")), "text": text,
                 "sections": list(brief_mod.SECTIONS)}
        if used != (lang or "en-US"):
            reply["note"] = f"no {lang} text: showing {used}"
        return fit_result(reply, BRIEF_CAP if name == "all" else DEFAULT_CAP)
    return _guard(body)


# ---------------------------------------------------------------------------------------------------------------
# build and verify (design 4.1; the MCP binding registers them, this module only provides the pure functions)
# ---------------------------------------------------------------------------------------------------------------

BUILD_CAP = 2000
VERIFY_CAP = 3000
BLOCKERS_SHOWN_IN_BUILD = 4


def _gate_lines(verdict: "Mapping | None") -> list:
    return list(verdict["lines"]) if verdict else []


def _spread_blockers(blockers: "Sequence[Mapping]", limit: int) -> list:
    """At most ``limit`` blockers, the first of each kind before a second of any kind (the AI sees every kind of problem)."""
    first, rest, seen = [], [], set()
    for item in blockers:
        if item["kind"] in seen:
            rest.append(item)
        else:
            seen.add(item["kind"])
            first.append(item)
    return (first + rest)[:limit]


is_link = pj.is_link   # the one definition lives in project.py (symbolic link, junction, reparse point); kept under this name for callers and tests


def confine_dist(ws: Workspace, project: str) -> None:
    """Refuse build, verify and the oracle sheet when ``dist`` or anything the build owns inside it is a link or junction, or resolves
    outside the workspace: the build deletes and rewrites those directories, and a planted junction would send that outside.

    Defence in depth since debt-b4: ``project.check_confined`` now lists ``dist`` and ``out`` and refuses links, and ``build.py`` runs the same
    checks on every part of ``dist`` (``_check_dist_confined``) and again where it deletes (``_clear_owned``); this runs first so that the
    refusal comes before anything else of the tool call."""
    from . import build as build_mod

    ws.writable()
    paths = pj.project_paths(ws, project)
    build_mod._check_dist_confined(ws, paths)  # noqa: SLF001 - same package: one list of what the build owns


def _scrub_strings(value, forms: Sequence[str]):
    """``value`` with every form replaced in every string of it (dict keys stay)."""
    if isinstance(value, str):
        for form in forms:
            value = value.replace(form, "<the pinned browser>")
        return value
    if isinstance(value, list):
        return [_scrub_strings(item, forms) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_strings(item, forms) for key, item in value.items()}
    return value


def scrub_verdict(dist: Path, verdict: Mapping, write: bool = True) -> dict:
    """The verdict without the operator's pinned browser path (``GEMARC_ATLAS_BROWSER``): it is an environment value of the host, and the
    report in ``dist/`` is shared with the user. The gate's message quotes it (``repr``, with doubled backslashes); every string that holds
    it is cleaned, and the report file is rewritten when anything changed (``write=False``: only the returned copy, for a refused release
    whose gate report was never written to ``dist``)."""
    from . import build as build_mod
    from .gate import smoke

    pinned = os.environ.get(smoke.BROWSER_ENV) or ""
    if not pinned:
        return dict(verdict)
    forms = sorted({repr(pinned)[1:-1], pinned}, key=len, reverse=True)       # the quoted form first: it contains the plain one only when no backslash is doubled
    scrubbed = _scrub_strings(dict(verdict), forms)
    if write and scrubbed != dict(verdict):
        build_mod._write_report(dist, scrubbed)  # noqa: SLF001 - same package: keep gate-report.json in step with the reply
    return scrubbed


#: What the AI does about each kind of release blocker (one line per kind that occurs; the first blocker of every kind is listed first).
RELEASE_HOW = {
    "unsealed": "oracle: write the expect rows with defined nodes only, then put (the release build seals them)",
    "revised": "oracle: the user confirms the edited expectations on atlas/dist/oracle-sheet.md (atlas/oracle-confirm.txt); never write that file",
    "provenance": "oracle: the ledger is unverified; only the user's confirmation of every item on atlas/dist/oracle-sheet.md clears it",
    "mismatch": "oracle: a verdict fails: repair the case events first (never copy the engine's answer into the expectation)",
    "no-verdict": "oracle: run gemarc_atlas_simulate, then release again",
    "no-bite": "oracle: run gemarc_atlas_simulate, then release again (the release build runs the mutants)",
    "vacuous": "oracle: an expectation constrains nothing: assert the states the case really decides (OR-003, OR-004)",
    "evidence": "evidence: run gemarc_atlas_evidence (it re-stamps moved anchors); an edited block must be re-read and the row re-cited with a different anchor; then release again",
    "docs": "docs: a release builds the documents, diagrams and prompts: call build release=true gate=smoke with docs=true",
    "markers": "docs: author the NOT-AUTHORED slots (gemarc_atlas_doc shows where), then release again",
    "gate": "browser gate: run build gate=smoke (not a release) to see the findings, repair, then release again",
}


def _release_refusal(result: Mapping, build_mod) -> dict:
    """The reply of a release that was refused: no page or document of ``dist/`` was touched (``build_all`` refreshes ``oracle-sheet.md``), so the
    reply names no page, only what blocked it."""
    blockers, verdict = result["blockers"], result["verdict"]
    if verdict is not None:
        verdict = scrub_verdict(result["dist"], verdict, write=False)
    counts = result["oracle"]
    reply = {"status": "invalid", "authority": AUTHORITY, "code": build_mod.E_RELEASE,
             "message": f"{len(blockers)} release blocker(s): the release was NOT built; no page or document of dist/ was changed (a previous build, if any, is still there; oracle-sheet.md is refreshed)",
             "dist": str(result["dist"]), "modelSha8": result["modelSha8"], "oracle": {k: counts[k] for k in ("sealed", "revised", "confirmed", "pending")},
             "gate": verdict["status"] if verdict else "NOT-RUN", "lines": _gate_lines(verdict),
             "release": {"requested": True, "ok": False, "blockerCount": len(blockers)},
             "blockers": [f"{b['rule']} {b['kind']} {b['id']}: {b['message']}"[:140] for b in _spread_blockers(blockers, BLOCKERS_SHOWN_IN_BUILD)],
             "next": []}
    seen: list = []
    for b in blockers:
        if b["kind"] not in seen:
            seen.append(b["kind"])
    reply["next"] = [RELEASE_HOW[k] for k in seen if k in RELEASE_HOW][:3]
    return fit_result(reply, BUILD_CAP)


def build(ws: Workspace, project: str, release: bool = False, docs: bool = True, gate: str = "none") -> dict:
    """``gemarc_atlas_build``: write ``atlas/dist/index.html`` (+ ``build-info.json``, documents, diagrams, prompts) from the project.

    Refuses a model with ERROR findings. Only sealed or confirmed expectations ever put a result into the page (ORACLE_FIRST);
    the others get the empty state. ``gate="smoke"`` runs the browser gate at 1024, 1366 and 1600 px; ``release`` also needs the oracle
    gate, verified and not stale evidence (as-is), no NOT-AUTHORED slot and a passing gate. A release is built beside ``dist`` and swapped in only
    when it passed: a refused release (``GEMARC_ATLAS_BUILD_020``, status ``invalid``) leaves every page and document of ``dist/`` exactly as it was
    (``oracle-sheet.md`` is refreshed); a release needs ``docs=true`` and ``gate="smoke"``.
    The reply stays within 2 KB (paths, bytes, sha256, the per-part size breakdown, check results, oracle class counts, gate lines)."""
    def body() -> dict:
        from . import build as build_mod
        from . import htmlassemble

        confine_dist(ws, project)
        try:
            result = build_mod.build_project(ws, project, release, docs, gate)
        except build_mod.BuildRefused as ex:
            return fit_result(invalid(ex.code, ex.message, **ex.extra), BUILD_CAP)
        except htmlassemble.ViewerError as ex:
            return fit_result(invalid(ex.code, ex.message), BUILD_CAP)
        except oracle_mod.OracleError as ex:
            return fit_result(invalid(ex.code, ex.message, **ex.extra), BUILD_CAP)
        if result["refused"]:
            return _release_refusal(result, build_mod)
        info, verdict = result["info"], result["verdict"]
        if verdict is not None:                  # bind the browser verdict to THIS page: the status gate reads only a stamp of the current page
            verdict = scrub_verdict(result["dist"], verdict)
            status_mod.write_gate_stamp(pj.project_paths(ws, project), model_sha8=info["modelSha8"], page_sha256=info["page"]["sha256"], verdict=verdict)
        reply = {"status": "ok", "authority": AUTHORITY, "dist": str(result["dist"]), "page": info["page"], "modelSha8": info["modelSha8"],
                 "parts": info["parts"], "data": dict(sorted(info["data"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]),
                 "checks": [f"{c['rule']} {c['severity']}" for c in info["checks"]], "findings": info["findings"],
                 "oracle": {k: info["oracle"][k] for k in ("sealed", "revised", "confirmed", "pending")},
                 "withheld": {k: len(v) for k, v in info["withheld"].items()}, "docs": info["docs"],
                 "gate": info["gate"]["status"], "lines": _gate_lines(verdict), "release": {"requested": release, "ok": info["release"]["ok"]},
                 "next": []}
        if verdict and verdict["status"] == "FAIL":
            reply.update(status="invalid", code=build_mod.E_GATE_FAIL, message="the browser gate FAILED: the page is written but is not verified")
        elif verdict and verdict["status"] == "SKIP":
            reply["next"].append("the browser gate was SKIPPED (no browser found): the page is NOT verified; install Edge or Chrome and run verify")
        elif verdict and verdict["status"] == "INCONCLUSIVE":
            reply["next"].append("the browser gate was INCONCLUSIVE (a launch timed out, the machine is busy): the page is NOT verified, and no defect was found; run verify again when the machine is idle")
        if gate == "none":
            reply["next"].append("run verify (or build gate=smoke) to check the page in a headless browser")
        if info["oracle"]["pending"] or info["oracle"]["revised"]:
            reply["next"].append(f"oracle: {info['oracle']['pending']} unregistered, {info['oracle']['revised']} revised: the page shows the empty state for them")
        return fit_result(reply, BUILD_CAP)
    return _guard(body)


def verify(ws: Workspace, project: str, widths: "Sequence[int]" = (1024, 1366, 1600)) -> dict:
    """``gemarc_atlas_verify``: run the browser gate on the built page at ``widths`` (innerWidth). Writes ``atlas/dist/.verify/``.

    ``status`` is ``ok`` only when every width PASSED. A failed gate is ``invalid`` (``GEMARC_ATLAS_BUILD_030``), a gate that could not
    run for lack of a browser is ``invalid`` too (``..._031``: SKIPPED, NOT PASSED); a launch that timed out on a loaded machine is ``..._033`` (INCONCLUSIVE, NOT PASSED, no defect found);
    a missing or stale page is ``..._032``."""
    def body() -> dict:
        from . import build as build_mod

        try:
            wanted = [int(w) for w in widths]
        except (TypeError, ValueError):
            raise GemArcError(E_ARG, "widths must be a list of integers") from None
        if not wanted or any(w < 500 or w > 4000 for w in wanted) or len(wanted) > 6:
            raise GemArcError(E_ARG, "widths: one to six integers between 500 and 4000")
        confine_dist(ws, project)
        try:
            result = build_mod.verify_project(ws, project, wanted)
        except build_mod.BuildRefused as ex:
            return fit_result(invalid(ex.code, ex.message, **ex.extra), VERIFY_CAP)
        verdict = scrub_verdict(result["page"].parent, result["verdict"])
        status_mod.write_gate_stamp(pj.project_paths(ws, project), model_sha8=result["sha8"], page_sha256=build_mod._sha_file(result["page"]), verdict=verdict)  # noqa: SLF001
        rows = [{"width": w["width"], "status": w["status"], "codes": w["codes"], "tabs": len(w["tabs"]), "steps": f"{w['steps']['ok']}/{w['steps']['total']}",
                 "seconds": w["seconds"]} for w in verdict["widths"]]
        reply = {"status": "ok", "authority": AUTHORITY, "gate": verdict["status"], "widths": rows, "lines": verdict["lines"], "modelSha8": result["sha8"],
                 "report": str(result["page"].parent / build_mod.VERIFY_DIR / "gate-report.json"), "shots": [], "browser": verdict.get("browser")}
        if verdict["status"] == "FAIL":
            first = [f for w in verdict["widths"] for f in w["findings"]][:4]
            reply.update(status="invalid", code=build_mod.E_GATE_FAIL, message="the browser gate FAILED",
                         findings=[f"{f['code']} {f['name']}" + (f" [{f['tab']}]" if f.get("tab") else "") + f": {f['detail']}"[:160] for f in first])
        elif verdict["status"] == "SKIP":
            reply.update(status="invalid", code=build_mod.E_GATE_SKIP, message="SKIPPED, NOT PASSED: " + str(verdict["message"])[:300])
        elif verdict["status"] == "INCONCLUSIVE":
            reply.update(status="invalid", code=build_mod.E_GATE_INCONCLUSIVE,
                         message="INCONCLUSIVE, NOT PASSED: a browser launch timed out (the machine is busy); no defect was found in the page; run verify again when the machine is idle (if it times out again on an idle machine, the page itself hangs)"
                         + (" " + str(verdict["message"])[:200] if verdict.get("message") else ""))
        return fit_result(reply, VERIFY_CAP)
    return _guard(body)


# ---------------------------------------------------------------------------------------------------------------
# doc, outline, evidence (design 4.1; batch 3)
# ---------------------------------------------------------------------------------------------------------------

DOC_CAP = 1024                        # the outline with its lint counts: one KiB
DOC_PAGE_BYTES = 15 * 1024            # raw text of a chapter page: JSON escapes and the envelope must stay under the 20 KiB hard cap
OUTLINE_CAP = 4500
OUTLINE_PAGE_BYTES = 3300             # the page text is quoted again inside the JSON reply (anchors are JSON literals): room for the escapes
EVIDENCE_CAP = 3000
EVIDENCE_REPORT_BYTES = 2000
_UNIT_PREFIXES = ("req:", "case:", "node:", "edge:")


def _unit_of(unit: str):
    """``system`` / empty = every element; ``req:RQ-1``, ``case:CS-1``, ``node:svc.x`` name one element (a bare id works too)."""
    word = (unit or "").strip()
    if word in ("", "system"):
        return None
    for prefix in _UNIT_PREFIXES:
        if word.startswith(prefix):
            return word[len(prefix):]
    return word


def doc(ws: Workspace, project: str, unit: str = "system", level: str = "BD", lang: str = "", chapter: str = "", page: int = 0) -> dict:
    """``gemarc_atlas_doc``: the STRUCTURE of the derived documents (never an engine result, so it can never reveal a verdict).

    Default: the outline of one document (``unit`` x ``level`` REQ|BD|DD x ``lang``): ``ch`` has one digit per chapter (0 empty state,
    1 content, 2 content with a NOT-AUTHORED slot), ``markers`` counts the slots that block a release, ``links`` and ``junk`` the lint
    findings; at most one KiB. With ``chapter`` (1..12) one page of that chapter's text (about 15 KiB, ``pages`` says how many)."""
    def body() -> dict:
        from .derive import assemble as dassemble
        from .derive import docbuild

        paths, cfg = _loaded(ws, project)
        snap = status_mod.load_snapshot(paths, cfg)
        use_lang = (lang or "").strip() or (cfg.languages[0] if cfg.languages else "en-US")
        try:
            number = int(str(chapter).strip()) if str(chapter).strip() else None
            page_no = int(page)
        except (TypeError, ValueError):
            raise GemArcError(E_ARG, "chapter and page are whole numbers") from None
        if page_no < 0:
            raise GemArcError(E_ARG, "page is not negative")
        page_no = page_no or 1
        target = _unit_of(unit)
        if number is None:
            out = docbuild.doc_outline(snap.model, target, level, use_lang)
        else:
            out = _doc_page(snap.model, docbuild, dassemble, target, level, use_lang, number, page_no)
        if not out.get("ok"):
            return fit_result(invalid(str(out.get("code", "GEMARC_ATLAS_DOC_099")), str(out.get("hint") or out.get("message") or "the document could not be derived")), DOC_CAP)
        reply = {"status": "ok", "authority": AUTHORITY, **{k: v for k, v in out.items() if k != "ok"}}
        if number is not None:
            return fit_result(reply, HARD_CAP)
        reply["sha8"] = snap.sha8
        for drop in ("pages", "other", "unit"):
            if reply_bytes(reply) <= DOC_CAP:
                break
            reply.pop(drop, None)
        if out.get("markers"):
            reply["next"] = ["chapters with digit 2 hold NOT-AUTHORED slots: author the rows they name (status view=report lists the slots)"]
        return fit_result(reply, DOC_CAP)
    return _guard(body)


def _doc_page(model, docbuild, dassemble, target, level: str, lang: str, number: int, page_no: int) -> dict:
    """One chapter page re-paginated to ``DOC_PAGE_BYTES`` (``docbuild.doc_outline`` pages at 20 KiB of raw text, which the JSON
    reply could not carry within the 20 KiB cap)."""
    try:
        if level not in docbuild.LEVELS:
            return {"ok": False, "code": docbuild.E_LEVEL, "hint": "level must be REQ, BD or DD"}
        if not 1 <= number <= 12:
            return {"ok": False, "code": docbuild.E_CHAPTER, "hint": "chapter must be an integer from 1 to 12"}
        bundle = docbuild.get_bundle(model, lang)
        ids, label = docbuild.resolve_unit(bundle, target)
        if ids is None:
            return label
        title, md = docbuild.chapter_text(dassemble.assemble(bundle.docset, level, ids), number)
        pages = docbuild.paginate(md, DOC_PAGE_BYTES)
        if page_no > len(pages):
            return {"ok": False, "code": docbuild.E_CHAPTER, "hint": f"the chapter has {len(pages)} page(s)"}
        return {"ok": True, "chapter": number, "page": page_no, "pages": len(pages), "title": title, "text": pages[page_no - 1]}
    except Exception as ex:  # noqa: BLE001 - never raise out of the tool core
        return {"ok": False, "code": docbuild.E_INTERNAL, "hint": "internal document tool error: " + type(ex).__name__}


def _too_big(table: Mapping, alias: str, path: str) -> "tuple | None":
    """``(code, message)`` when the file is larger than the verifier reads (checked with ``stat`` BEFORE any read: ``outline_file`` reads
    the whole file first, so an AI-chosen path to a multi-gigabyte file under a root would otherwise be loaded into memory)."""
    from . import evidence as ev
    from . import roots as roots_mod

    try:
        if alias not in table or not isinstance(path, str):
            return None
        resolved = roots_mod.resolve_in_root(Path(table[alias]), path)
        if resolved.exists and os.stat(resolved.path).st_size > ev.MAX_FILE_BYTES:
            return ev._CODES["NOT_TEXT"][0], f"{resolved.rel}: larger than {ev.MAX_FILE_BYTES} bytes, not read"  # noqa: SLF001
    except (roots_mod.RootsError, OSError, KeyError):
        return None
    return None


def outline(root: str, path: str, offset: int = 0) -> dict:
    """``gemarc_atlas_outline``: def/class/heading lines of ONE source file with line numbers and a unique anchor snippet each (read-only).

    ``root`` is an ALIAS declared in the server's ``GEMARC_ATLAS_ROOTS`` (never a path); ``path`` is relative to that root; ``offset`` is the
    entry index to continue from (the reply's ``next``). Secrets are redacted: ``.env*`` shows key names only. One page is at most 4 KB."""
    def body() -> dict:
        from . import outline as outline_mod
        from . import roots as roots_mod

        try:
            alias = roots_mod.require_alias(root, "root")
            table = roots_mod.roots_from_env()
            start = int(offset)
        except roots_mod.RootsError as ex:
            return invalid(ex.code, ex.message)
        except (TypeError, ValueError):
            raise GemArcError(E_ARG, "offset is a whole number") from None
        if start < 0:
            raise GemArcError(E_ARG, "offset is not negative")
        if not isinstance(path, str) or not path.strip():
            raise GemArcError(E_ARG, "path is required: a file path relative to the root")
        too_big = _too_big(table, alias, path)
        if too_big:
            return fit_result(invalid(too_big[0], too_big[1]), OUTLINE_CAP)
        result = outline_mod.outline_file(table, alias, path, start=start, max_bytes=OUTLINE_PAGE_BYTES)
        if not result.get("ok"):
            return fit_result(invalid(str(result.get("code", E_ARG)), str(result.get("message", "no outline"))), OUTLINE_CAP)
        reply = {"status": "ok", "authority": AUTHORITY, "file": result["file"], "language": result["language"], "shown": result["shown"],
                 "total": result["total"], "text": result["text"]}
        if result.get("next") is not None:
            reply["next"] = result["next"]
            reply["more"] = f"offset={result['next']}"
        if result.get("caseMismatch"):
            reply["warn"] = "the letter case of the path differs from the file name on disk (works on Windows, fails on Linux)"
        return fit_result(reply, OUTLINE_CAP)
    return _guard(body)


def evidence(ws: Workspace, project: str, ids: "Sequence[str] | str" = ()) -> dict:
    """``gemarc_atlas_evidence``: verify the evidence rows against the real files and stamp what passes (``.atlas/evidence-stamp.json``).

    Roots come from the server's ``GEMARC_ATLAS_ROOTS`` (a row whose alias has no root is SKIP, never PASS). One line per row that is not
    green, with a repair suggestion that is NEVER applied; an anchor that fails stays red until the row is corrected. ``ids`` limits the run.
    Needs the write gate (it writes the stamp)."""
    def body() -> dict:
        from . import evidence as ev
        from . import roots as roots_mod

        ws.writable()
        paths, cfg = _loaded(ws, project)
        pj.check_confined(ws, paths)
        wanted = _strs(ids, "ids")
        try:
            table = roots_mod.roots_from_env()
        except roots_mod.RootsError as ex:
            return invalid(ex.code, ex.message)
        if cfg.repos:                                    # the project names the aliases it uses: a root it did not name stays unread
            table = {k: v for k, v in table.items() if k in cfg.repos}
        with sl.project_lock(paths):
            snap = status_mod.load_snapshot(paths, cfg)
            rows = [dict(e) for e in snap.model.get("evidences", ()) if isinstance(e, dict)]
            if wanted:
                known = {r.get("evidenceId") for r in rows}
                missing = [i for i in wanted if i not in known]
                if missing:
                    raise GemArcError(E_ARG, "no such evidence id: " + ", ".join(missing[:5]))
                rows = [r for r in rows if r.get("evidenceId") in set(wanted)]
            try:
                stamp = ev.load_stamp(paths.atlas)
            except ev.EvidenceError as ex:
                return invalid(getattr(ex, "code", E_ARG), str(ex)[:200])
            report = ev.verify_evidence(rows, table, stamp=stamp, project_dir=paths.project, mode=snap.mode)
            new = ev.update_stamp(stamp, report)
            for result in report.get("results", ()):    # source drift: a row that fails now is no longer verified, whatever it was before
                if result.get("status") == "FAIL":
                    new.get("entries", {}).pop(result.get("evidenceId"), None)
            if new != stamp:
                ev.save_stamp(paths.atlas, new)
        summary = report["summary"]
        counts = summary["status"]
        total_claims, unverified = status_mod.claims_unverified(snap.model, new.get("entries") or {})
        reply = {"status": "ok", "authority": AUTHORITY, "rows": summary["total"], "pass": counts["PASS"], "warn": counts["WARN"], "fail": counts["FAIL"],
                 "skip": counts["SKIP"], "stamped": len(new.get("entries") or {}), "roots": sorted(table),
                 "lines": ev.compact_report(report, max_bytes=EVIDENCE_REPORT_BYTES).split("\n")[1:], "next": []}
        if snap.mode == "asis" or total_claims:
            reply["claims"] = {"implemented": total_claims, "unverified": len(unverified)}
        # a block edited since it was stamped (EV-005) is never re-stamped by this tool, and a release refuses it: say so instead of "all verified"
        stale = [str(r.get("evidenceId")) for r in report.get("results", ())
                 if r.get("status") in ("PASS", "WARN") and (r.get("stampable") is False or r.get("name") == "RANGE_CONTENT_CHANGED")]
        if stale:
            reply["stale"] = len(stale)
        if counts["SKIP"] and not table:
            reply["next"].append("no evidence root is configured: the operator sets GEMARC_ATLAS_ROOTS (alias=path) in the server environment; Implemented claims stay unverified")
        elif counts["SKIP"]:
            reply["next"].append(f"{counts['SKIP']} row(s) SKIPPED (alias without a root, or a missing root/file): they are not verified")
        if counts["FAIL"]:
            reply["next"].append("fix each FAIL row (put mode=patch with the corrected file/anchor; suggestions are never applied), then run evidence again")
        if unverified:
            reply["next"].append(f"{len(unverified)} Implemented claim(s) still lack a verified anchor (first: {unverified[0]}): verify them, or lower their verif=")
        if stale:
            reply["next"].append(f"{len(stale)} row(s) cite a block that was edited since it was stamped (EV-005, first: {stale[0]}): this tool does not re-stamp them and a release refuses them when an Implemented claim rests on them; "
                                 "re-read the code, re-cite each row with a different anchor (put mode=patch), then run evidence again")
        if not reply["next"]:
            reply["next"].append("all evidence rows verified")
        return fit_result(reply, EVIDENCE_CAP, items_key="lines")
    return _guard(body)


def build_all(ws: Workspace, project: str, release: bool = False, docs: bool = True, gate: str = "none") -> dict:
    """``build`` plus the oracle sheet hand-off (design 7.6): whenever the build wrote ``dist/``, ``atlas/dist/oracle-sheet.md`` is written
    too (one line per expectation the user has not confirmed, and ``APPROVE-ALL``). The MCP never writes the user's confirmation file."""
    from . import build as build_mod

    reply = build(ws, project, release, docs, gate)
    if "page" not in reply and reply.get("code") != build_mod.E_RELEASE:     # a refused release has no page, but the user still needs the sheet
        return reply
    sheet = oracle_sheet(ws, project)
    if sheet.get("status") == "ok":
        reply["sheet"] = "atlas/dist/oracle-sheet.md"
        counts = reply.get("oracle") or {}
        waiting = int(counts.get("sealed", 0)) + int(counts.get("revised", 0))
        if waiting:
            reply.setdefault("next", []).append(f"hand atlas/dist/oracle-sheet.md to the user: {waiting} expectation(s) wait for approval (tick lines or keep APPROVE-ALL in atlas/oracle-confirm.txt)")
    else:
        reply.setdefault("next", []).append(f"the oracle sheet was not written: {sheet.get('code', 'error')}")
    return fit_result(reply, BUILD_CAP)


# ---------------------------------------------------------------------------------------------------------------
# review (the guard that makes "one self-review" more than a formality)
# ---------------------------------------------------------------------------------------------------------------

_REPORTED: set = set()          # (``atlas`` directory, model sha8) of the review reports this server process has produced


def review(ws: Workspace, project: str, action: str = "report", notes: str = "", *, require_report: bool = True) -> dict:
    """``gemarc_atlas_review``: ``review.run`` plus two guards the review core does not have. ``record`` needs non-empty ``notes`` (what was
    fixed, what stays open) and, in a server process, a ``report`` of this project made before it (the spot-check sample was seen). The
    guards come after the phase and once-only refusals of the core, so those keep their codes. ``require_report=False`` is the shell entry,
    where every call is its own process. A ``report`` whose record is stale no longer tells the driver to call ``record``."""
    from . import review as review_mod

    def body() -> dict:
        if action == "record":
            ws.writable()
            paths, cfg = _loaded(ws, project)
            if not status_mod.review_is_valid_record(paths):
                snap = status_mod.load_snapshot(paths, cfg)
                if status_mod.evaluate(snap).phase == "review":
                    if not isinstance(notes, str) or not notes.strip():
                        raise GemArcError(review_mod.E_REVIEW_ARG, "notes are required: what you fixed and what stays open, as `fixed=<ids>; open=<ids>`")
                    if require_report and (str(paths.atlas), snap.sha8) not in _REPORTED:
                        raise GemArcError(review_mod.E_REVIEW_ARG, "read the report of THIS model first: call action=report (it never writes; "
                                          "again after any fix), check its sample rows, then record")
        out = review_mod.run(ws, project, action, notes)
        if action == "report" and out.get("status") == "ok":
            _REPORTED.add((str(pj.project_paths(ws, project).atlas), str(out.get("modelSha", ""))))
            if (out.get("record") or {}).get("state") == "stale":
                out["next"] = ["the review record is stale: the BLOCK finding(s) above are new; fix them with put mode=patch and re-run the gates. "
                               "There is no second review and nothing more to record"]
        return out
    return _guard(body)
