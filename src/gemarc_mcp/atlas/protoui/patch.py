"""Row-level patch of an interface slice (the ``put mode="patch"`` of the interface store).

Adapted from the 5D slice store (``slices.apply_patch``) to the interface identities, at the text level so that no row (valid or
not) of the base is rewritten:

* a row with the identity of a base row replaces it (the new row is appended, the old line is removed); a new identity is appended;
  ``-<key cells>`` deletes (``-SCR-LOGIN`` under ``## screens``; ``@set screen=SCR-LOGIN`` and ``-err`` under ``## widgets``);
* identity: screens ``id``; variants ``screen`` + ``name``; widgets ``screen`` + ``local`` (the ``screen=`` option or the ``@set`` in force);
  nav ``from`` + ``to`` (+ ``alias``); conform ``screen``; diffs ``ref``;
* when several base rows share an identity (the duplicate that ST-002 reports) an operation takes the **last** one, so the first, valid
  definition stays;
* appended rows go under a fresh canonical section header (which resets ``@set`` defaults), or into the last section when that section has
  no ``@set`` line and the patch brings none. A row that replaces a base row and brings no ``@set`` line of its own takes over the
  ``@set`` lines the old row was written under (so ``screen=`` need not be repeated). Everything else of the base is kept verbatim.

Standard library only; no file I/O.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping

from . import atl as atl_fmt
from . import rows as rw
from .rows import SECTIONS


class PatchError(Exception):
    """A patch that cannot be applied; nothing is written."""


def normalise(text: str) -> str:
    """Stored form: no BOM, LF newlines, no trailing blank lines, exactly one final LF (empty text stays empty)."""
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return text + "\n" if text else ""


@dataclass
class _U:
    kind: str                   # blank comment header section set final pending row other orphan
    text: str
    section: str = ""
    inst: int = 0               # section instance number (a ``## name`` line starts a new one)
    key: "tuple | None" = None
    delete: bool = False
    sets: tuple = ()            # the @set lines in force, cumulative within the instance
    arg: tuple = ()             # final: (section,) / pending: ids


@dataclass
class PatchReport:
    text: str
    replaced: int = 0
    added: int = 0
    deleted: int = 0
    missed: list = field(default_factory=list)      # deletes that matched nothing

    def as_dict(self) -> dict:
        out = {"replaced": self.replaced, "added": self.added, "deleted": self.deleted}
        if self.missed:
            out["nothing_to_delete"] = self.missed[:8]
        return out


_ID_COLUMN = {"screens": "id", "conform": "screen", "diffs": "ref"}


def _unq(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return str(json.loads(value))
        except ValueError:
            return value[1:-1]
    return value


def _naive_cells(body: str, count: int) -> tuple:
    parts = re.split(r"(?<!\\)\|", body)
    cells = [_unq(p.replace("\\|", "|")) for p in parts[:count]]
    return cells, ("|".join(parts[count:]) if len(parts) > count else "")


def _cells(body: str, spec: rw.Section) -> tuple:
    need = len(spec.columns)
    try:
        cells, rest, problem = atl_fmt._split_row(body, spec)
    except Exception:  # noqa: BLE001 - a defective row must never stop a patch; fall back to the naive split
        cells, rest, problem = [], None, True
    if problem or len(cells) < need:
        cells, rest = _naive_cells(body, need)
    else:
        cells = [str(c) for c in cells]
    return cells, rest or ""


def _values(section: str, body: str, defaults: Mapping) -> dict:
    """The values of the identity cells of one row: columns by position, options by name; ``~`` and absent options take ``@set``."""
    spec = SECTIONS[section]
    cells, rest = _cells(body, spec)
    options = {}
    if rest.strip():
        items, _problem = atl_fmt.tokenize_options(rest)
        options = {k: str(v) for k, v in items if isinstance(v, str)}
    values = {}
    for index, col in enumerate(spec.columns):
        value = cells[index] if index < len(cells) else None
        if value == "~":
            value = defaults.get(col.name)
        if value:
            values[col.name] = value
    for name, value in options.items():
        values.setdefault(name, value)
    for name, value in defaults.items():
        values.setdefault(name, value)
    return values


def _identity(section: str, values: Mapping) -> "tuple | None":
    """The identity of a row (``None`` when a cell of it is missing)."""
    if section in _ID_COLUMN:
        v = values.get(_ID_COLUMN[section])
        return (section, v) if v else None
    if section == "variants":
        parts = [values.get("screen"), values.get("name")]
        return ("variants", *parts) if all(parts) else None
    if section == "widgets":
        parts = [values.get("screen"), values.get("local")]
        return ("widgets", *parts) if all(parts) else None
    if section == "nav":
        parts = [values.get("from"), values.get("to")]
        return ("nav", *parts, values.get("alias") or "") if all(parts) else None
    return None


def _scan(text: str, *, patch: bool) -> tuple:
    units, errors = [], []
    section, inst, defaults, sets = "", 0, {}, []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip(" \t\r")
        if not line:
            units.append(_U("blank", raw))
        elif line.startswith("//"):
            units.append(_U("comment", raw))
        elif line.startswith("#atlas"):
            units.append(_U("header", raw))
        elif line.startswith("## ") or line == "##":
            name = line[2:].strip().partition(":")[0].strip()
            inst += 1
            defaults, sets = {}, []
            if name in SECTIONS:
                section = name
            else:
                section = ""
                if patch:
                    errors.append(f"line {number}: unknown section '{name or line[:20]}'")
            units.append(_U("section", raw, section, inst))
        elif line.startswith("@set") and (len(line) == 4 or line[4] in " \t"):
            sets.append(line)
            items, _problem = atl_fmt.tokenize_options(line[4:])
            for key, value in items:
                if isinstance(value, str) and not isinstance(value, bool):
                    if value == "":
                        defaults.pop(key, None)
                    else:
                        defaults[key] = str(value)
            units.append(_U("set", raw, section, inst, sets=tuple(sets)))
        elif line.startswith("@final") and (len(line) == 6 or line[6] in " \t"):
            arg = line[6:].strip() or section
            units.append(_U("final", raw, section, inst, arg=(arg,)))
        elif line.startswith("@pending") and (len(line) == 8 or line[8] in " \t"):
            ids = tuple(x.strip() for x in line[8:].split(",") if x.strip())
            units.append(_U("pending", raw, section, inst, arg=ids))
        elif line.startswith("@"):
            units.append(_U("other", raw, section, inst))
        elif not section:
            units.append(_U("orphan", raw))
            if patch:
                errors.append(f"line {number}: a row outside a '## section:' block")
        else:
            delete = patch and line.startswith("-")
            body = line[1:].lstrip() if delete else line
            values = _values(section, body, defaults)
            units.append(_U("row", body if patch else raw, section, inst, _identity(section, values), delete, tuple(sets)))
    return units, errors


def _show(key: tuple) -> str:
    return " | ".join(str(x) for x in key[1:] if x != "")


def apply_patch(base_text: str, patch_text: str) -> PatchReport:
    """Apply row-level changes (see the module doc) to ``base_text`` and return the new text and what happened."""
    base_text, patch_text = normalise(base_text), normalise(patch_text)
    base, _ = _scan(base_text, patch=False)
    ops, errors = _scan(patch_text, patch=True)
    if errors:
        raise PatchError("the patch cannot be read: " + "; ".join(errors[:3]))
    if not any(u.kind in ("row", "final", "pending") for u in ops):
        raise PatchError("the patch has no rows and no @final/@pending line")
    removed: set = set()                 # indexes into ``base``
    touched: set = set()                 # section instances that lost a row
    added: list = []                     # patch units in order
    report = PatchReport("")

    def drop_base(match) -> list:
        return [i for i, u in enumerate(base) if i not in removed and u.kind == "row" and match(u)]

    def drop_added(match) -> int:
        n = len(added)
        added[:] = [u for u in added if not match(u)]
        return n - len(added)

    for u in ops:
        if u.kind != "row":
            continue
        if u.key is None:
            raise PatchError(f"cannot tell which {u.section} row this is: {u.text[:40]}")
        hits = drop_base(lambda b, k=u.key: b.key == k)
        dropped = drop_added(lambda a, k=u.key: a.key == k)
        if hits:  # the last base row of that identity: the first definition stays
            removed.add(hits[-1])
            touched.add(base[hits[-1]].inst)
            if not u.delete and not u.sets and base[hits[-1]].sets:
                u.sets = base[hits[-1]].sets  # the replacing row keeps the @set defaults the old row relied on
        if u.delete:
            if hits:
                report.deleted += 1
            elif not dropped:
                report.missed.append(_show(u.key))
            continue
        report.replaced += 1 if hits else 0
        added.append(u)
    report.added = max(0, len(added) - report.replaced)
    # a section instance that lost its last row disappears with its @set lines (so that the same patch applied twice gives the same text)
    for inst in touched:
        members = [i for i, u in enumerate(base) if u.inst == inst and u.section and i not in removed]
        if inst and not any(base[i].kind in ("row", "final", "pending", "other") for i in members):
            removed.update(i for i in members if base[i].kind in ("section", "set"))
    kept = [u.text for i, u in enumerate(base) if i not in removed]
    lines = list(kept)
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(_emit(added, lines))
    have_final = {a for u in base for a in u.arg if u.kind == "final"}
    have_pending = {a for u in base for a in u.arg if u.kind == "pending"}
    new_final = [a for u in ops if u.kind == "final" for a in u.arg if a not in have_final]
    new_pending = [a for u in ops if u.kind == "pending" for a in u.arg if a not in have_pending]
    for name in dict.fromkeys(new_final):
        lines.append(f"@final {name}")
    if new_pending:
        lines.append("@pending " + ",".join(dict.fromkeys(new_pending)))
    report.text = normalise("\n".join(lines))
    return report


def _emit(added: list, lines: list) -> list:
    """The lines to append: each run of rows of one patch section instance under a section header."""
    tail_section, tail_sets = "", False
    for u in _scan("\n".join(lines), patch=False)[0]:
        if u.kind == "section":
            tail_section, tail_sets = u.section, False
        elif u.kind == "set":
            tail_sets = True
    out: list = []
    previous_inst, emitted = None, ()
    for u in added:
        if u.inst != previous_inst:
            previous_inst, emitted = u.inst, ()
            if u.sets or u.section != tail_section or tail_sets:
                out.append(SECTIONS[u.section].header)
                tail_section, tail_sets = u.section, False
        if u.sets[: len(emitted)] == emitted:
            fresh = u.sets[len(emitted):]
        else:
            fresh, emitted = u.sets, ()
        out.extend(fresh)
        if fresh:
            tail_sets = True
        emitted = u.sets
        out.append(u.text)
    return out
