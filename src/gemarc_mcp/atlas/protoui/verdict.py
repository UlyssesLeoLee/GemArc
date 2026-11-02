"""Frontend check verdicts of the interface family (design A3 4.8, 6.3 ``UV-001`` .. ``UV-008``).

A verdict is the testimony of the AI that compared a frontend with the prototype: slices named ``ui-verdict-*`` hold two sections only,
``conform`` (one row per screen: ``conforms | differs | unchecked``, a ``basis`` of the form ``<alias>:<path>:<line>``, the stamp ``sha=`` of the
screen at the time of the check, ``checked=<n>``, ``repo=<alias>``) and ``diffs`` (one row per check point that was found wrong, missing, extra
or not verifiable). Verdict rows never reach the prototype model, so ``ui_sha8`` does not depend on them; ``verdict_sha8`` (``model.py``) is their
own hash. The check points are the claims of ``model.checkpoints`` (the table of the screen card); the stamp of a screen is
``model.screen_sha8`` (a hash of its claims, shown on the card as ``sha=``).

What the machine can check is whether the two quotations are real and whether the AI accounts for every check point; it cannot check that the AI
looked. The rules:

* ``UV-001`` (ERROR) a ``diffs`` ref names a check point of the current prototype (an ``extra`` row names a screen or ``<screen>@route``), a ``conform`` row names a screen.
* ``UV-002`` (ERROR) a ref appears once in ``diffs``, a screen once in ``conform``.
* ``UV-003`` (ERROR) ``expected`` is the value the prototype has (the plain value or its card form, so that text copied from a card is accepted).
* ``UV-004`` (ERROR / INFO) ``basis`` resolves in an evidence root (``GEMARC_ATLAS_ROOTS``) and ``found`` is within 3 lines of its line; a basis whose alias
  has no root is one INFO line per alias and never counts as a pass; a sensitive file is refused, never read or echoed.
* ``UV-005`` (ERROR) ``checked`` plus the ``unverifiable`` rows equals the number of check points of the screen (a verdict says it looked at
  all of them, or names the ones it did not), a ``conforms`` / ``differs`` row has a basis, ``conforms`` has no differing row and ``differs`` has one.
* ``UV-006`` (ERROR) ``conforms`` does not contradict the last ``screens scan`` of the same screen stamp (a literal check point that was ``ABSENT`` needs a row).
* ``UV-007`` (ERROR for a new ``conforms`` row, else WARN) ``sha`` is the current stamp of the screen: a verdict of an older screen is stale.
* ``UV-008`` (INFO, full check only) coverage: screens with a current verdict, check points looked at.

The rules have their own table here (not the registry of ``rules.py``, which pins the author-side prototype rules). Evidence text is read only through
the public names of ``evidence.py`` (``read_text_file``, ``is_sensitive_path``, ``context_echo_allowed``, ``echo_source_line``, ``CONTEXT_RADIUS``) and the
sandbox of ``roots.py``; a source line leaves this module only through ``echo_source_line``. Standard library only.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .. import diag as dg
from .. import evidence as ev
from .. import roots as rt
from . import atl, expand as ex, model as um
from . import textfit as tf
from .rows import ParsedSlice

#: Regular rule table: ``(id, wave, severity, summary)``. Hints are built where the finding is made (they carry the repair).
RULES = {
    "UV-001": (1, dg.ERROR, "a diffs ref names a check point of the current prototype; a conform row names a screen"),
    "UV-002": (1, dg.ERROR, "a ref appears once in diffs, a screen once in conform"),
    "UV-003": (1, dg.ERROR, "expected equals the value the prototype has"),
    "UV-004": (3, dg.ERROR, "basis resolves in an evidence root and found is within 3 lines of it (no root: INFO)"),
    "UV-005": (1, dg.ERROR, "checked plus the unverifiable rows equals the check points of the screen; conforms and differs agree with their rows"),
    "UV-006": (3, dg.ERROR, "conforms does not contradict the last scan of the same screen stamp"),
    "UV-007": (1, dg.ERROR, "sha is the current stamp of the screen (a new conforms row: ERROR, a stored stale verdict: WARN)"),
    "UV-008": (3, dg.INFO, "coverage of the verdicts (full check only)"),
}

#: ``<alias>:<path>:<line>``: the alias is the text before the first colon, the line the digits after the last one.
BASIS_RE = re.compile(r"^(?P<alias>[A-Za-z0-9][\w.-]{0,39}):(?P<path>.+):(?P<line>[1-9][0-9]{0,8})$")
#: The kinds of a ``diffs`` row that state a difference (``unverifiable`` states a gap in the check instead).
DIFFERENT = ("differs", "missing", "extra")
#: Findings listed per cause before the rest is counted.
SHOWN = 3
LITERAL_COLUMNS = ("route", "label", "bind", "goto")


def parse_basis(text: str) -> "tuple | None":
    """``(alias, path, line)`` of a basis cell, or ``None`` when it is not ``<alias>:<path>:<line>``."""
    m = BASIS_RE.match(str(text or "").strip())
    return (m.group("alias"), m.group("path"), int(m.group("line"))) if m else None


def screen_of(ref: str) -> str:
    """The screen id a check point (or a screen id) belongs to: the leading id of ``SCR-X``, ``SCR-X.w@col``, ``SCR-X#v@state``."""
    m = re.match(r"[A-Za-z][A-Za-z0-9_-]*", str(ref))
    return m.group(0) if m else ""


def column_of(ref: str) -> str:
    """The column of a check point ref (``label``, ``route``, ``order``, ``state`` ...), ``""`` for a screen id."""
    return str(ref).partition("@")[2]


def _clip(value, limit: int = 60) -> str:
    return dg.clip(value, limit)


# ---------------------------------------------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenCoverage:
    """What the verdicts say about one screen at the current stamp."""

    screen: str
    points: int                 # check points of the screen
    sha: str                    # current stamp of the screen
    verdict: str                # none | unchecked | conforms | differs
    current: bool               # the verdict row quotes the current stamp
    checked: int                # check points the row says it looked at (0 without a row)
    unverifiable: int           # rows of kind unverifiable that name a check point of the screen
    diffs: int                  # rows of kind differs, missing, extra of the screen

    @property
    def judged(self) -> bool:
        """Counted in ``n/m``: a current ``conforms`` or ``differs``."""
        return self.current and self.verdict in ("conforms", "differs")

    @property
    def label(self) -> str:
        """``none``, ``unchecked``, ``conforms``, ``conforms+u3`` (three check points not verified), ``differs``, with ``(stale)`` for an older stamp."""
        if self.verdict in ("none", "unchecked"):
            return self.verdict
        text = self.verdict + (f"+u{self.unverifiable}" if self.verdict == "conforms" and self.unverifiable else "")
        return text if self.current else text + "(stale)"


@dataclass(frozen=True)
class Coverage:
    screens: tuple

    def summary(self) -> dict:
        """``judged`` of ``total`` screens (current ``conforms`` / ``differs``), counts per label word, check points looked at of the total."""
        out = {"total": len(self.screens), "judged": sum(1 for s in self.screens if s.judged), "conforms": 0, "differs": 0, "stale": 0,
               "unchecked": 0, "none": 0, "points": sum(s.points for s in self.screens),
               "looked": sum(s.checked for s in self.screens if s.judged)}
        for s in self.screens:
            if s.verdict in ("conforms", "differs") and not s.current:
                out["stale"] += 1
            elif s.verdict in out:
                out[s.verdict] += 1
        return out

    def by_screen(self) -> dict:
        return {s.screen: s for s in self.screens}


@dataclass
class VerdictCheck:
    """What a verdict check found: diagnostics in the shape of every other check, the coverage, the evidence aliases that had no root."""

    diagnostics: list
    coverage: Coverage
    mode: str = "full"
    skipped_aliases: tuple = ()

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
        return not self.errors

    def rule_ids(self) -> set:
        return {d.rule for d in self.diagnostics}

    def render(self, fmt: str = "text", offset: int = 0, budget: "int | None" = None) -> str:
        return dg.render(self.diagnostics, fmt=fmt, offset=offset, budget=budget)


# ---------------------------------------------------------------------------------------------------------------
# Evidence files (read through the public names of evidence.py, inside the sandbox of roots.py)
# ---------------------------------------------------------------------------------------------------------------

_ROOT_PROBLEM = {
    rt.E_ABSOLUTE: "the path is absolute: write it relative to the root",
    rt.E_DRIVE: "the path has a drive letter or ':': write it relative to the root",
    rt.E_DOTDOT: "the path has '..': write it relative to the root",
    rt.E_NAME: "the path has a segment that no repository path uses",
    rt.E_PATH_TYPE: "the path is not a file name",
    rt.E_ESCAPE: "the path leaves the evidence root",
}


class _Files:
    """Reads each basis file once. ``get`` returns ``(SourceText | None, problem text)``: a problem is a fixed sentence, never a host path."""

    def __init__(self, roots: "Mapping | None"):
        self.roots = {str(k): Path(v) for k, v in (roots or {}).items()}
        self._src: dict = {}
        self._dirs: dict = {}

    def has_root(self, alias: str) -> bool:
        return alias in self.roots and rt.root_available(self.roots[alias])

    def get(self, alias: str, rel: str) -> tuple:
        key = (alias, rel)
        if key not in self._src:
            self._src[key] = self._read(alias, rel)
        return self._src[key]

    def _read(self, alias: str, rel: str) -> tuple:
        if ev.is_sensitive_path(rel):
            return None, "the file is sensitive (key, credential or .env): it is never read or quoted; cite a source file"
        try:
            res = rt.resolve_in_root(self.roots[alias], rel, dir_cache=self._dirs)
        except rt.RootsError as ex_:
            return None, _ROOT_PROBLEM.get(ex_.code, "the path is not usable inside the root")
        if not res.exists or res.path is None or not rt.is_regular_file(res.path):
            return None, f"no such file in root {alias}: check the path"
        try:
            return ev.read_text_file(res.path), ""
        except ev.TextError as ex_:
            return None, f"the file cannot be read as text ({ex_})"
        except OSError:
            return None, "the file could not be read"


def _window_has(src, line: int, needle: str) -> bool:
    """``needle`` is inside lines ``line - r .. line + r`` (``r`` = ``CONTEXT_RADIUS``), as written or with white space folded."""
    first, last = max(1, line - ev.CONTEXT_RADIUS), min(src.line_count, line + ev.CONTEXT_RADIUS)
    text = "\n".join(src.lines[first - 1:last])
    return needle in text or " ".join(needle.split()) in " ".join(text.split())


def _nearest_lines(src, needle: str, line: int) -> list:
    """Up to three lines (numbers only, no text) where ``needle`` is, nearest to ``line`` first: the repair when the line number is off."""
    hits = sorted({first for first, _last in src.occurrences(needle, cap=50)}, key=lambda n: (abs(n - line), n))
    return hits[:SHOWN]


# ---------------------------------------------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------------------------------------------


def _file_of(at: str) -> str:
    return at.rpartition(":")[0] if ":" in at else at


class _Findings:
    """Builds the diagnostics; a finding outside the incoming slices is dropped (slice mode reports only what is being put)."""

    def __init__(self, incoming: "Iterable[str] | None"):
        self.incoming = None if incoming is None else set(incoming)
        self.out: list = []

    def in_scope(self, at: str) -> bool:
        return self.incoming is None or _file_of(at) in self.incoming

    def new(self, at: str) -> bool:
        """A row of a slice that is being put (never true in a full check: every row there is a stored one)."""
        return self.incoming is not None and _file_of(at) in self.incoming

    def add(self, rule: str, path: str, at: str, got, expected: str, hint: str, severity: "str | None" = None) -> None:
        if self.in_scope(at):
            self.out.append(dg.diag(rule, path, at, got, expected, hint, severity=severity or RULES[rule][1]))


def _rows(records: Sequence, locations: "Sequence | None", default_file: str = "ui-verdict.atl") -> list:
    """``(record, at)`` pairs; a record without a location gets ``<file>:<n>`` so that every finding has a place."""
    out = []
    for i, rec in enumerate(records):
        at = locations[i] if locations is not None and i < len(locations) else f"{default_file}:{i + 1}"
        out.append((rec, at))
    return out


def check_rows(model: Mapping, conform: Sequence, diffs: Sequence, *, conform_at: "Sequence | None" = None, diffs_at: "Sequence | None" = None,
               roots: "Mapping | None" = None, scans: "Mapping | None" = None, mode: str = "full", incoming: "Iterable[str] | None" = None) -> VerdictCheck:
    """The verdict rules over verdict records (``conform`` and ``diffs`` rows as ``expand_ui`` keeps them) against an expanded prototype ``model``.

    ``mode="full"`` checks every row and adds the coverage line; ``incoming`` (file names of the slices being put) limits the findings to the rows of
    those slices, and a ``conforms`` row among them that quotes an older stamp is an ERROR (a stored one is only a WARN). ``roots`` maps an alias to
    a directory (``roots.roots_from_env()``; ``None``: no alias has a root); ``scans`` maps a screen id to the content of its ``scan-*.json``.
    """
    if mode not in ("slice", "full"):
        raise ValueError("mode must be 'slice' or 'full'")
    out = _Findings(incoming)
    points = um.checkpoints(model)
    by_ref = {c.ref: c for c in points}
    screen_points: dict = {}
    for c in points:
        screen_points.setdefault(c.screen, []).append(c.ref)
    stamps = um.screen_hashes(model)
    screens = set(stamps)
    extra_refs = screens | {f"{s}@route" for s in screens}
    files = _Files(roots)
    crows, drows = _rows(conform, conform_at), _rows(diffs, diffs_at)
    skipped: dict = {}     # alias -> [count, first at]
    # A STORED verdict of a screen whose stamp moved is a verdict about an older prototype (design 4.8: only a WARN, UV-007): its rows are not held
    # against the prototype as it is now, so a changed prototype never turns the stored verdicts into ERRORs. Rows that are being put are always checked.
    first_row: dict = {}
    for rec, at in crows:
        first_row.setdefault(rec["screen"], (rec, at))
    old = {sid for sid, (rec, at) in first_row.items() if sid in screens and rec["verdict"] != "unchecked" and rec.get("sha") != stamps[sid] and not out.new(at)}

    # -- UV-001 / UV-002 / UV-003 on diffs ----------------------------------------------------------------------
    seen_refs: dict = {}
    for rec, at in drows:
        ref, kind = rec["ref"], rec["kind"]
        path = f"diffs[{ref}]"
        if ref in seen_refs:
            out.add("UV-002", path + ".ref", at, ref, "a ref that appears once", f"{ref} is already a diffs row at {seen_refs[ref]}: merge the two rows into one")
        else:
            seen_refs[ref] = at
        if screen_of(ref) in old and not out.new(at):
            continue
        if ref not in (extra_refs if kind == "extra" else by_ref):
            out.add("UV-001", path + ".ref", at, ref, "a check point of the prototype" if kind != "extra" else "a screen id or <screen>@route",
                    _bad_ref_hint(ref, kind, by_ref, screens, model))
            continue
        if "expected" in rec and ref in by_ref:
            value = by_ref[ref].value
            if rec["expected"] not in (value, tf.card_text(value)):
                out.add("UV-003", path + ".expected", at, _clip(rec["expected"]), "the value the prototype has",
                        f"the prototype has {_clip(value)}: expected= quotes the prototype, found= quotes the frontend")

    # -- conform rows: UV-001 / UV-002 / UV-005 / UV-007 -----------------------------------------------------------
    diffs_of: dict = {}
    for rec, _at in drows:
        diffs_of.setdefault(screen_of(rec["ref"]), []).append(rec)
    first_conform: dict = {}
    for rec, at in crows:
        sid, verdict = rec["screen"], rec["verdict"]
        path = f"conform[{sid}]"
        if sid not in screens:
            out.add("UV-001", path + ".screen", at, sid, "a screen of the prototype", _near_screen_hint(sid, screens))
            continue
        if sid in first_conform:
            out.add("UV-002", path + ".screen", at, sid, "one conform row per screen", f"{sid} already has a conform row at {first_conform[sid]}: keep one")
            continue
        first_conform[sid] = at
        mine = diffs_of.get(sid, [])
        refs = set(screen_points.get(sid, ()))
        unver = sum(1 for d in mine if d["kind"] == "unverifiable" and d["ref"] in refs)
        differing = sum(1 for d in mine if d["kind"] in DIFFERENT)
        n = len(refs)
        if verdict in ("conforms", "differs") and sid not in old:
            checked = rec.get("checked")
            if checked is None:
                out.add("UV-005", path + ".checked", at, "missing", "checked=<n>, the check points looked at",
                        f"{sid} has {n} check points: write checked={max(n - unver, 0)} (all but the {unver} unverifiable rows)")
            elif checked + unver != n:
                out.add("UV-005", path + ".checked", at, f"{checked} + {unver} unverifiable = {checked + unver}", f"{n} check points of {sid}",
                        f"account for every check point: checked={max(n - unver, 0)}, or write the ones you did not look at as unverifiable rows")
            if parse_basis(rec["basis"]) is None:
                out.add("UV-005", path + ".basis", at, _clip(rec["basis"]), f"a basis <alias>:<path>:<line> for a {verdict} verdict",
                        "name the frontend file and line the verdict rests on, for example fe:src/pages/Login.tsx:34")
            if verdict == "conforms" and differing:
                out.add("UV-005", path + ".verdict", at, f"conforms with {differing} differing rows", "no differs, missing or extra row for a conforming screen",
                        "write verdict=differs, or remove the rows that say the frontend differs")
            if verdict == "differs" and not differing:
                out.add("UV-005", path + ".verdict", at, "differs without a differing row", "at least one differs, missing or extra row",
                        "list what differs in the diffs section, or write verdict=conforms (or unchecked)")
        stamp = stamps[sid]
        if verdict != "unchecked" and rec.get("sha") != stamp:
            new = out.new(at)
            severity = dg.ERROR if (verdict == "conforms" and new) else dg.WARN
            got = rec.get("sha") or "missing"
            hint = (f"the screen changed since this verdict, or sha= was not copied from its card: check {sid} against the card again, then write sha={stamp}"
                    if rec.get("sha") else f"write sha={stamp} (the stamp on the card of {sid}) after you checked the frontend against that card")
            out.add("UV-007", path + ".sha", at, got, f"sha={stamp}, the current stamp of {sid}", hint, severity)

    # -- UV-004: basis cells --------------------------------------------------------------------------------------
    def basis_row(path: str, at: str, basis: str, found: "str | None") -> None:
        parsed = parse_basis(basis)
        if parsed is None:
            out.add("UV-004", path, at, _clip(basis), "a basis <alias>:<path>:<line>", "write it as alias:relative/path:line, for example fe:src/pages/Login.tsx:34")
            return
        alias, rel, line = parsed
        if not files.has_root(alias):
            if out.in_scope(at):
                skipped.setdefault(alias, [0, at])[0] += 1
            return
        src, problem = files.get(alias, rel)
        if src is None:
            out.add("UV-004", path, at, _clip(f"{alias}:{rel}:{line}", 80), "a file of the evidence root", problem)
            return
        if line > src.line_count:
            out.add("UV-004", path, at, f"line {line}", f"a line of {rel} ({src.line_count} lines)", f"the file has {src.line_count} lines: cite a line inside it")
            return
        if found is not None and not _window_has(src, line, found):
            near = _nearest_lines(src, found, line)
            where = f"it is at line {', '.join(map(str, near))}" if near else "it is nowhere in the file"
            shown = ev.echo_source_line(rel, src, line, 60) if ev.context_echo_allowed(rel) else ""
            hint = f"found= must be within {ev.CONTEXT_RADIUS} lines of the basis line: {where}" + (f"; line {line} reads: {shown}" if shown else "")
            out.add("UV-004", path, at, _clip(found), f"found= inside {rel}:{line} +-{ev.CONTEXT_RADIUS}", hint)

    for rec, at in crows:
        if rec["verdict"] in ("conforms", "differs") and rec["screen"] in screens and rec["screen"] not in old and parse_basis(rec["basis"]) is not None:
            basis_row(f"conform[{rec['screen']}].basis", at, rec["basis"], None)
    for rec, at in drows:
        ref = rec["ref"]
        if screen_of(ref) in old and not out.new(at):
            continue
        if "basis" in rec:
            basis_row(f"diffs[{ref}].basis", at, rec["basis"], rec.get("found"))
        elif "found" in rec and rec["kind"] in ("differs", "extra"):
            out.add("UV-004", f"diffs[{ref}].found", at, _clip(rec["found"]), "a basis <alias>:<path>:<line> next to found=",
                    "found= is checked against the file: add basis=alias:path:line")
    for alias, (count, first_at) in sorted(skipped.items()):
        if count:
            out.add("UV-004", "basis", first_at, f"{count} basis cells", f"an evidence root for alias {alias}",
                    f"SKIP: {alias} has no root, so {count} basis cells are not verified (this is not a pass): the operator sets GEMARC_ATLAS_ROOTS {alias}=<dir>",
                    dg.INFO)

    # -- UV-006: conforms against the last scan ------------------------------------------------------------------
    for rec, at in crows:
        sid = rec["screen"]
        if rec["verdict"] != "conforms" or sid not in screens or sid in old or first_conform.get(sid) != at:
            continue
        scan = (scans or {}).get(sid)
        if not isinstance(scan, Mapping) or scan.get("screenSha8") != stamps[sid]:
            continue
        if rec.get("repo") and scan.get("repo") and rec["repo"] != scan["repo"]:
            continue
        have = {d["ref"] for d in diffs_of.get(sid, ())}
        absent = sorted(ref for ref, lit in (scan.get("literals") or {}).items()
                        if isinstance(lit, Mapping) and lit.get("state") == "ABSENT" and ref in by_ref and ref not in have)
        if absent:
            more = f" +{len(absent) - SHOWN}" if len(absent) > SHOWN else ""
            out.add("UV-006", f"conform[{sid}].verdict", at, f"{len(absent)} literals absent in the last scan",
                    "conforms only when the scanned literals were found, or unverifiable rows for the ones that were not",
                    f"the last scan of {sid} did not find: {', '.join(absent[:SHOWN])}{more}: check them, then write differs, a diffs row or unverifiable")

    coverage = coverage_of(model, conform, diffs)
    if mode == "full":
        s = coverage.summary()
        if s["total"]:
            out.add("UV-008", "coverage", "model:0", f"{s['judged']} of {s['total']} screens", "every screen has a current verdict",
                    f"judged {s['judged']}/{s['total']} (conforms {s['conforms']}, differs {s['differs']}, stale {s['stale']}, unchecked {s['unchecked']}, "
                    f"no verdict {s['none']}); check points looked at {s['looked']} of {s['points']}", dg.INFO)
    return VerdictCheck(_dedupe(out.out), coverage, mode, tuple(sorted(a for a, (n, _at) in skipped.items() if n)))


def _bad_ref_hint(ref: str, kind: str, by_ref: Mapping, screens: set, model: Mapping) -> str:
    sid = screen_of(ref)
    if kind == "extra":
        return "an extra row names the screen (SCR-X) or its route (SCR-X@route): the frontend has something the prototype does not"
    widget, _, column = ref.partition("@")
    if column and any(w["id"] == widget for w in model.get("widgets", ())):
        return f"{widget} has no {column} cell, so there is nothing to check: use a check point from the card table of {sid}"
    pool = [r for r, c in by_ref.items() if c.screen == sid] or list(by_ref)
    near = difflib.get_close_matches(ref, pool, n=1, cutoff=0.75)
    if near:
        return f"did you mean {near[0]}? The check points are the cells of the widget table, <widget id>@<column>, and the order, route and state rows"
    return f"use a check point of {sid} from its card: <widget id>@<column>, <container id>@order, <screen>@route, <screen>#<name>@state"


def _near_screen_hint(sid: str, screens: set) -> str:
    near = difflib.get_close_matches(sid, sorted(screens), n=1, cutoff=0.7)
    return f"did you mean {near[0]}?" if near else "write the id of a screen of the prototype"


def _dedupe(diags: Iterable) -> list:
    seen, out = set(), []
    for d in sorted(diags, key=dg.Diag.sort_key):
        key = (d.rule, d.severity, d.path, d.at, repr(d.got))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Coverage and the entry points
# ---------------------------------------------------------------------------------------------------------------


def coverage_of(model: Mapping, conform: Sequence, diffs: Sequence) -> Coverage:
    """The coverage of the verdict rows over the screens of ``model``: per screen the verdict word, whether it quotes the current stamp, the counts.

    Only a row whose ``sha`` equals the current stamp of its screen counts (``current``); an older one is shown as ``conforms(stale)`` and is not
    in ``judged``. The first ``conform`` row of a screen is the one that counts (a second is UV-002)."""
    stamps = um.screen_hashes(model)
    per_screen: dict = {}
    for c in um.checkpoints(model):
        per_screen.setdefault(c.screen, set()).add(c.ref)
    first: dict = {}
    for rec in conform:
        first.setdefault(rec["screen"], rec)
    out = []
    for sid, stamp in stamps.items():
        refs = per_screen.get(sid, set())
        mine = [d for d in diffs if screen_of(d["ref"]) == sid]
        rec = first.get(sid)
        out.append(ScreenCoverage(
            sid, len(refs), stamp, rec["verdict"] if rec else "none", bool(rec) and rec.get("sha") == stamp,
            int(rec.get("checked", 0)) if rec and rec["verdict"] != "unchecked" else 0,
            len({d["ref"] for d in mine if d["kind"] == "unverifiable" and d["ref"] in refs}), sum(1 for d in mine if d["kind"] in DIFFERENT)))
    return Coverage(tuple(out))


def check_expansion(exp: "ex.UiExpansion", *, roots: "Mapping | None" = None, scans: "Mapping | None" = None, mode: str = "full",
                    incoming: "Iterable[str] | None" = None) -> VerdictCheck:
    """``check_rows`` over the verdict rows of an expansion (``exp.verdict`` and the locations ``exp.verdict_at``)."""
    return check_rows(exp.model, exp.verdict["conform"], exp.verdict["diffs"], conform_at=exp.verdict_at["conform"], diffs_at=exp.verdict_at["diffs"],
                      roots=roots, scans=scans, mode=mode, incoming=incoming)


def check(ui_model: Mapping, verdict_slices: Sequence, roots: "Mapping | None" = None, *, scans: "Mapping | None" = None, mode: str = "full",
          incoming: "Iterable[str] | None" = None) -> VerdictCheck:
    """The verdict rules for ``verdict_slices`` (``ParsedSlice`` objects or ``(name, text)`` pairs of ``ui-verdict-*`` slices) against ``ui_model``.

    The structural diagnostics of the slices themselves (ST-xxx: a malformed row is dropped) are part of the result. Verdict rows hold no
    prototype row, so the expansion of the verdict slices alone carries exactly the ``conform`` and ``diffs`` records.
    """
    parsed = [s if isinstance(s, ParsedSlice) else atl.parse_atl(s[1], s[0]) for s in verdict_slices]
    exp = ex.expand_ui(parsed)
    result = check_rows(ui_model, exp.verdict["conform"], exp.verdict["diffs"], conform_at=exp.verdict_at["conform"], diffs_at=exp.verdict_at["diffs"],
                        roots=roots, scans=scans, mode=mode, incoming=incoming)
    structural = [d for d in exp.diagnostics if incoming is None or _file_of(d.at) in set(incoming)]
    result.diagnostics = _dedupe(structural + result.diagnostics)
    return result
