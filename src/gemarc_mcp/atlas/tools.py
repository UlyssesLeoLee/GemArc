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
from typing import Callable, Mapping, Sequence

from ..paths import GemArcError, Workspace
from . import brief as brief_mod
from . import diag as dg
from . import indexer
from . import model as am
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
            return invalid(pj.E_EXISTS, f"项目 {project} 已有 atlas 目录；init 不会覆盖（直接用 status view=next 继续）",
                           path=str(paths.atlas), next=["resume: status view=next; saved texts go under <path>/user/"])
        pj.create_layout(paths, pj.render_config(project, label, mode, langs, profile, aliases))
        first = "save the requirement text verbatim to atlas/user/<name>.txt (the directory exists), then " if mode == "tobe" else ""
        return {"status": "created", "authority": AUTHORITY, "project": project, "path": str(paths.atlas), "mode": mode,
                "created": ["atlas.toml", ".gitignore", "slices/", "user/", ".atlas/red/", ".atlas/history/"],
                "next": [f"read the work order: brief (section=all, lang={langs[0]})",
                         first + "put 00-space (buildings, floors, rooms), then 10-structure slices of about 11 nodes each",
                         "after each put: read the next lines of its reply; status view=next after a red draft, on resume, and about every 5 puts"]}
    return _guard(body)


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
    through the diagnostics the way ``put`` said (``more: offset=N``); ``rule`` keeps one rule id or family (``RF``).
    """
    def body() -> dict:
        if bool(project) == bool(text):
            raise GemArcError(E_ARG, "pass exactly one of project and text")
        if mode not in ("slice", "full"):
            raise GemArcError(E_ARG, "mode is 'slice' or 'full'")
        if format not in ("text", "json"):
            raise GemArcError(E_ARG, "format is 'text' or 'json'")
        try:
            offset_ = max(0, int(offset))
        except (TypeError, ValueError):
            raise GemArcError(E_ARG, "offset is a whole number (the offset=N of the previous reply)") from None
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
        else:
            snap = status_mod.load_snapshot(paths, cfg, mode=mode)
            result = snap.check
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
            base["note"] = "seals are written by put, simulate and build (oracle lane); until then nothing is sealed"
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
NEEDS_CLIP = 80
BLOCKERS_SHOWN = 5


def _fit_next(reply: dict, cap: int = NEXT_CAP) -> dict:
    """``status view=next`` within ``cap`` bytes (the AI reads it after every few puts): the questions to ask (2 lines of 80
    characters, then a count), the first blockers (then a count), and as many ``next`` lines as fit (the first three always stay)."""
    out = dict(reply)
    needs = list(out.get("needsUser") or [])
    if len(needs) > NEEDS_SHOWN:
        needs = [x[:NEEDS_CLIP] for x in needs[:NEEDS_SHOWN]] + [f"+{len(needs) - NEEDS_SHOWN} more"]
    else:
        needs = [x[:NEEDS_CLIP] for x in needs]
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
        paths, cfg = _loaded(ws, project)
        model, prov = indexer.load_model(paths, cfg)
        lines = indexer.index_lines(model, prov, scope, with_edges=bool(with_edges), ids=_strs(ids, "ids") if ids else (), detail=detail)
        start = max(0, int(offset))
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
        text = brief_mod.render(section, lang)
        used = brief_mod.resolve_language(lang)
        name = (section or "all").strip().lower()
        reply = {"status": "ok", "authority": AUTHORITY, "section": name, "lang": used, "bytes": len(text.encode("utf-8")), "text": text,
                 "sections": list(brief_mod.SECTIONS)}
        if used != lang:
            reply["note"] = f"no {lang} text: showing {used}"
        return fit_result(reply, BRIEF_CAP if name == "all" else DEFAULT_CAP)
    return _guard(body)
