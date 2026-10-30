"""The diagnostic record of GemArc Atlas and the structural rules ST-001..ST-008 and DV-001.

A diagnostic is ``{rule, severity, path, at, got, expected, hint, derived}``:

``rule``      rule id (``ST-005``, ``DV-001``; later lanes add RF-, EV-, SM-, CS-, IV-, OR- ids)
``severity``  ``ERROR`` | ``WARN`` | ``INFO``
``path``      where in the model or in the row the problem sits (``nodes.sub``, ``nodes[svc.loan].verification``)
``at``        ``file:line`` of the row the AI has to repair (``name:0`` when the problem is the file itself)
``got``       what was found (any JSON value; rendering truncates it)
``expected``  what the rule wanted, as a short phrase
``hint``      how to repair it, one sentence
``derived``   ``True`` when the diagnostic is a consequence of an earlier one (counted, not printed, by renderers)

This module owns the record, its constructors, ``render_line`` and the renderer (grouping, budgets, ``+N more``,
text and json formats, section "Rendering" below) plus the look-alike function the slice-mode rules use (section
"Look-alike ids"). The rule registry lives in ``atlas/rules/``; this module keeps the record shape and the structural
constructor names stable. Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
SEVERITIES = (ERROR, WARN, INFO)
FIELDS = ("rule", "severity", "path", "at", "got", "expected", "hint", "derived")

#: Rules defined by this lane: id -> (severity, one-line meaning). The registry of the rules lane extends it.
STRUCTURAL_RULES = {
    "ST-001": (ERROR, "required or non-empty cell, option or header line is missing"),
    "ST-002": (ERROR, "an id is defined twice (both places are named)"),
    "ST-003": (ERROR, "a value is outside its enum, id grammar or numeric range"),
    "ST-004": (ERROR, "unknown section, column, option or key"),
    "ST-005": (ERROR, "wrong number of cells in a row (the header is echoed)"),
    "ST-006": (ERROR, "an option token sits inside a text cell (a '|' is probably missing)"),
    "ST-007": (ERROR, "option syntax: token, quoting, JSON, number or boolean is malformed"),
    "ST-008": (ERROR, "file size or encoding"),
    "DV-001": (ERROR, "a derived field was authored"),
}

GOT_LIMIT = 80
#: A string ``got`` is cut to this many characters when the record is built (renderers cut again at ``GOT_LIMIT``).
STORED_GOT_LIMIT = 200


@dataclass(frozen=True)
class Diag:
    """One diagnostic. ``as_dict()`` is the stable record shape; ``at`` is ``file:line``."""

    rule: str
    severity: str
    path: str
    at: str
    got: Any
    expected: str
    hint: str
    derived: bool = False

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in FIELDS}

    def sort_key(self) -> tuple:
        """Errors first, then rule id, then location (``file``, numeric line)."""
        file, _, line = self.at.rpartition(":")
        return (SEVERITIES.index(self.severity) if self.severity in SEVERITIES else len(SEVERITIES), self.rule,
                file or self.at, int(line) if line.isdigit() else 0, self.path, repr(self.got), self.expected, self.hint)


def default_severity(rule: str) -> str:
    """Severity of ``rule``: the structural table first, then the rule registry (``ERROR`` for an unknown rule)."""
    hit = STRUCTURAL_RULES.get(rule)
    if hit is not None:
        return hit[0]
    try:
        from .rules import registry
    except ImportError:  # pragma: no cover - the registry ships with the package
        return ERROR
    known = registry.REGISTRY.get(rule)
    return known.severity if known is not None else ERROR


def diag(rule: str, path: str, at: str, got: Any, expected: str, hint: str, *, severity: str | None = None,
         derived: bool = False) -> Diag:
    """Build a diagnostic; ``severity`` defaults to the registry entry of ``rule`` (``ERROR`` for unknown rules)."""
    if severity is None:
        severity = default_severity(rule)
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
    if isinstance(got, str) and len(got) > STORED_GOT_LIMIT:
        got = got[:STORED_GOT_LIMIT - 3] + "..."  # a diagnostic never echoes unbounded author text
    return Diag(rule, severity, path, at, got, expected, hint, derived)


def clip(value: Any, limit: int = GOT_LIMIT) -> str:
    """A ``got`` value as one printable line: control characters stripped, cut at ``limit`` characters."""
    import json

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


HINT_LIMIT = 140
PATH_LIMIT = 110
WANT_LIMIT = 100
AT_LIMIT = 60
RULE_LIMIT = 12
#: ST-004 lists the allowed names in ``want``; they are the repair, so that rule gets a longer ``want``.
LONG_WANT = {"ST-004": 260}


def clip_at(at: Any, limit: int = AT_LIMIT) -> str:
    """``file:line`` as one printable line; a long file name is shortened in the middle so the line number survives."""
    text = clip(at, 10 ** 6)
    if len(text) <= limit:
        return text
    head, sep, tail = text.rpartition(":")
    if not sep or not tail.isdigit() or len(tail) > 10:
        return text[: limit - 3] + "..."
    return head[: max(8, limit - len(tail) - 4)] + "...:" + tail


def _letter(severity: Any) -> str:
    text = clip(severity, 10)
    return text[:1] if text else "?"


def want_limit(d: Diag) -> int:
    return LONG_WANT.get(d.rule, WANT_LIMIT)


def render_line(d: Diag) -> str:
    """``E ST-005 10-structure.atl:5 nodes got=... want=... | hint`` (one line, no budget logic).

    ``got`` is cut at 80 characters, the hint at 140, ``want`` at 100 and the path at 110; control characters are replaced
    by a space everywhere (a diagnostic can carry text the author wrote, and the AI reads it). ``at`` is cut at 60
    characters (the line number is kept) and ``rule`` at 12.
    """
    return (f"{_letter(d.severity)} {clip(d.rule, RULE_LIMIT)} {clip_at(d.at)} {clip(d.path, PATH_LIMIT)} got={clip(d.got)} "
            f"want={clip(d.expected, want_limit(d))} | {clip(d.hint, HINT_LIMIT)}")


# ---------------------------------------------------------------------------------------------------------------
# Constructors, one per structural rule
# ---------------------------------------------------------------------------------------------------------------


def st001(path: str, at: str, got: Any, expected: str, hint: str, *, derived: bool = False) -> Diag:
    """Required or non-empty: empty cell, missing option, missing header line, ``~`` without a ``@set`` default."""
    return diag("ST-001", path, at, got, expected, hint, derived=derived)


def st002(path: str, at: str, identifier: str, first_at: str, first_path: str, *, hint: str = "") -> Diag:
    """Duplicate id; both locations are printed (``at`` is the second one, the first is in the hint)."""
    return diag("ST-002", path, at, identifier, "an id that is defined once",
                f"already defined at {first_at} ({first_path}); rename one of them{(' ' + hint) if hint else ''}")


def st003(path: str, at: str, got: Any, expected: str, hint: str, *, derived: bool = False) -> Diag:
    """Value outside its enum, id grammar or range."""
    return diag("ST-003", path, at, got, expected, hint, derived=derived)


def st004(path: str, at: str, got: Any, allowed: Any, hint: str = "") -> Diag:
    """Unknown section, column, option or key; ``allowed`` is listed."""
    shown = allowed if isinstance(allowed, str) else ", ".join(allowed)
    return diag("ST-004", path, at, got, f"one of: {shown}", hint or "use one of the allowed names")


def st005(path: str, at: str, cells: int, positional: int, header: str, *, too_many: bool = False) -> Diag:
    """Arity; the header is echoed so the AI can count."""
    want = f"{positional} text cells" + (" and then key=value options" if not too_many else "")
    return diag("ST-005", path, at, f"{cells} cells", want,
                f"header is: {header}. A '|' inside text must be written \\|")


def st006(path: str, at: str, token: str) -> Diag:
    """An option-looking token inside a text cell."""
    return diag("ST-006", path, at, token, "plain text",
                "option-like token inside a text cell: is a '|' missing before the options? "
                "(write the cell as a quoted string to keep the text)")


def st007(path: str, at: str, got: Any, expected: str, hint: str) -> Diag:
    """Option syntax."""
    return diag("ST-007", path, at, got, expected, hint)


def st008(path: str, at: str, got: Any, expected: str, hint: str) -> Diag:
    """Size or encoding of a file."""
    return diag("ST-008", path, at, got, expected, hint)


def dv001(path: str, at: str, name: str, hint: str = "") -> Diag:
    """A derived field was authored (``.atl`` option or ``.jsonl`` key)."""
    return diag("DV-001", path, at, name, "nothing: the field is derived", hint or "derived; delete it")


# ---------------------------------------------------------------------------------------------------------------
# Look-alike ids (slice-mode typo detection, design 5.3)
# ---------------------------------------------------------------------------------------------------------------

MIN_LOOKALIKE_LENGTH = 5
_DIGITS = re.compile(r"\d+")
#: ``nearest`` looks at most at this many candidates and one ``LookAlike`` computes at most this many fuzzy matches.
NEAREST_CANDIDATES = 64
NEAREST_CALLS = 400


def digit_mask(identifier: str) -> str:
    """The id with every digit run replaced by ``#``: ids with the same mask differ only inside digit runs."""
    return _DIGITS.sub("#", identifier)


def edit_within_one(a: str, b: str) -> bool:
    """``True`` when ``a`` and ``b`` are one insertion, deletion, substitution or adjacent transposition apart."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = 0
    while i < min(la, lb) and a[i] == b[i]:
        i += 1
    if la == lb:
        if a[i + 1:] == b[i + 1:]:
            return True  # one substitution
        return i + 1 < la and a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2:] == b[i + 2:]  # one transposition
    if la > lb:
        return a[i + 1:] == b[i:]  # one deletion from a
    return b[i + 1:] == a[i:]  # one insertion into a


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class LookAlike:
    """Look-alike lookup over a pool of defined ids.

    ``find(x)`` returns the **unique** pool id within edit distance 1 of ``x`` (insert, delete, substitute, transpose),
    or ``None``. Excluded: ids shorter than 5 characters (on either side) and ids that differ from ``x`` only inside
    digit runs (``EVD-22`` vs ``EVD-23``). Two or more candidates are ambiguous and give ``None``. Results are cached
    per value; the pool is bucketed by length so a lookup compares only ids of length -1, 0 and +1.
    """

    def __init__(self, pool):
        self.pool = set(pool)
        self._by_len: dict[int, list[str]] = {}
        for item in self.pool:
            if len(item) >= MIN_LOOKALIKE_LENGTH:
                self._by_len.setdefault(len(item), []).append(item)
        for items in self._by_len.values():
            items.sort()
        self._cache: dict[str, "str | None"] = {}
        self._near_cache: dict[str, "str | None"] = {}
        self._near_calls = 0

    def find(self, value: str) -> "str | None":
        if not isinstance(value, str):
            return None
        if value in self._cache:
            return self._cache[value]
        found = None
        if len(value) >= MIN_LOOKALIKE_LENGTH and value not in self.pool:
            mask = digit_mask(value)
            hits = [c for n in (len(value) - 1, len(value), len(value) + 1) for c in self._by_len.get(n, ())
                    if digit_mask(c) != mask and edit_within_one(value, c)]
            if len(hits) == 1:
                found = hits[0]
        self._cache[value] = found
        return found

    def nearest(self, value: str, cutoff: float = 0.8) -> "str | None":
        """A fuzzy best match for hints in full mode (bounded work; a digit-only difference never counts)."""
        if not isinstance(value, str):
            return None
        if value in self._near_cache:
            return self._near_cache[value]
        if self._near_calls >= NEAREST_CALLS:
            return None
        import difflib

        self._near_calls += 1
        mask = digit_mask(value)
        slack = max(2, len(value) // 4)
        candidates = [c for c in self.pool if abs(len(c) - len(value)) <= slack and digit_mask(c) != mask]
        candidates.sort(key=lambda c: (-_common_prefix(c, value), abs(len(c) - len(value)), c))
        match = difflib.get_close_matches(value, candidates[:NEAREST_CANDIDATES], n=1, cutoff=cutoff)
        self._near_cache[value] = match[0] if match else None
        return self._near_cache[value]

    def sample(self, limit: int = 4) -> str:
        """``a, b, c, d ...``: some defined ids for a hint (sorted, so the text is deterministic)."""
        items = sorted(self.pool)
        return ", ".join(items[:limit]) + (" ..." if len(items) > limit else "")


def lookalike(value: str, pool) -> "str | None":
    """One-shot ``LookAlike(pool).find(value)`` (use the class when many values are checked against one pool)."""
    return LookAlike(pool).find(value)


# ---------------------------------------------------------------------------------------------------------------
# Rendering (design 5.2): text and json, errors first, grouped by rule, <= 12 groups, <= 2 examples per group
# ---------------------------------------------------------------------------------------------------------------

TEXT_BUDGET = 2800          # bytes of the default text view, trailer included
JSON_BUDGET = 4500          # bytes of the default json view
TRAILER_BUDGET = 190        # of the text budget, kept for the "+N more" and summary lines
JSON_RESERVE = 400          # of the json budget, kept for the counts object
HARD_CAP = 24 * 1024        # no view, whatever the caller asks for, is larger than this
MAX_GROUPS = 12
MAX_EXAMPLES = 2


def visible(diags) -> list:
    """The diagnostics a view lists: derived ones removed, errors first, then rule, file, line, path; exact repeats once."""
    seen, out = set(), []
    for d in sorted((d for d in diags if not d.derived), key=Diag.sort_key):
        key = (d.rule, d.severity, d.path, d.at, repr(d.got), d.expected, d.hint)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def counts(diags) -> dict:
    """``{"error", "warn", "info", "derived", "rules"}``: derived diagnostics are only counted as such; exact repeats count once
    (the same list ``visible`` returns, so the summary and the listing agree)."""
    diags = list(diags)
    shown = visible(diags)
    return {"error": sum(d.severity == ERROR for d in shown), "warn": sum(d.severity == WARN for d in shown),
            "info": sum(d.severity == INFO for d in shown), "derived": sum(1 for d in diags if d.derived),
            "rules": len({d.rule for d in shown})}


def _blen(text: str) -> int:
    return len(text.encode("utf-8")) + 1


def _overview(items: list, budget: int, size=None):
    """Positions of the grouped overview: one example per group first, then second examples while the budget lasts."""
    size = size or (lambda d: _blen(render_line(d)))
    groups: list[list[int]] = []
    index: dict[tuple, int] = {}
    for position, d in enumerate(items):
        key = (d.severity, d.rule)
        if key not in index:
            if len(groups) >= MAX_GROUPS:
                continue
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(position)
    chosen: set[int] = set()
    used = 0
    for rank in range(MAX_EXAMPLES):
        for members in groups:
            if rank < len(members):
                cost = size(items[members[rank]])
                if used + cost > budget:
                    return chosen or {groups[0][0]}  # the first diagnostic is always shown (shortened by the caller)
                chosen.add(members[rank])
                used += cost
    return chosen


def _fit_line(line: str, room: int) -> str:
    """``line`` cut to at most ``room`` bytes (a view always lists at least one diagnostic, so the paging advances)."""
    room = max(room, 40)
    if _blen(line) <= room:
        return line
    return line.encode("utf-8")[: room - 4].decode("utf-8", "ignore") + "..."


def _pending_lines(pending) -> list:
    lines = []
    for kind in sorted(pending or {}):
        value = pending[kind]
        ids = [] if isinstance(value, int) else sorted(value)
        total = value if isinstance(value, int) else len(ids)
        shown = ", ".join(clip(x, 40) for x in ids[:3]) + (f" +{total - 3}" if total > 3 else "")
        lines.append(f"I pending {kind} ({total}): {shown} | not defined yet; @pending silences, a typo would be a WARN")
    return lines


def summary_line(diags, pending_bind: int = 0, derived: "int | None" = None) -> str:
    c = counts(diags)
    parts = [f"{c['error']} errors, {c['warn']} warnings, {c['info']} info in {c['rules']} rules" if c["rules"] else "nothing to repair"]
    d = c["derived"] if derived is None else derived
    if d:
        parts.append(f"derived (suppressed): {d}")
    if pending_bind:
        parts.append(f"pending-bind: {pending_bind}")
    return "; ".join(parts)


def render(diags, *, fmt: str = "text", offset: int = 0, budget: "int | None" = None, pending=None,
           pending_bind: int = 0, derived: "int | None" = None) -> str:
    """Render diagnostics as text or json within a byte budget.

    ``offset=0`` is the grouped overview: at most 12 groups (severity + rule), at most 2 examples per group, lines
    clipped (``got`` 80, hint 140), then ``+N more (offset=K)`` where ``K`` is the position of the first hidden
    diagnostic in the sorted list. ``offset=K`` (> 0) is the flat continuation ``list[K:]``, one line per diagnostic,
    until the budget ends; it names the next offset again (a line already shown may repeat). Derived diagnostics are
    never listed, only counted. The default budget is 2,800 bytes for text (4,500 for json), trailer included; no budget,
    even one asked for, makes a view larger than 24 KiB. ``pending`` maps a kind to its ids (or a count) and is listed as
    INFO lines in the overview.
    """
    if fmt not in ("text", "json"):
        raise ValueError("fmt must be 'text' or 'json'")
    items = visible(diags)
    offset = max(0, int(offset))
    budget = (TEXT_BUDGET if fmt == "text" else JSON_BUDGET) if budget is None else max(0, int(budget))
    budget = min(budget, HARD_CAP)
    reserve = TRAILER_BUDGET if fmt == "text" else JSON_RESERVE  # room for the trailer (text) or the counts object (json)
    if offset == 0:
        chosen = _overview(items, budget - reserve, None if fmt == "text" else (lambda d: len(_json_record(d)) + 1))
        shown = [items[i] for i in sorted(chosen)]
        hidden = [i for i in range(len(items)) if i not in chosen]
        next_offset, more = (hidden[0] if hidden else len(items)), len(hidden)
    else:
        used, position = 0, offset
        while position < len(items):
            cost = _blen(render_line(items[position])) if fmt == "text" else len(_json_record(items[position])) + 2
            if used + cost > budget - reserve and position > offset:
                break
            used += cost
            position += 1
        shown = items[offset:position]
        next_offset, more = position, max(0, len(items) - position)
    if fmt == "json":
        return _render_json(shown, more, next_offset, diags, pending if offset == 0 else None, pending_bind, derived)
    lines = [render_line(d) for d in shown]
    if len(lines) == 1:
        lines[0] = _fit_line(lines[0], budget - reserve)
    used = sum(_blen(x) for x in lines)
    listed_pending = False
    if offset == 0:
        for line in _pending_lines(pending):
            if used + _blen(line) <= budget - reserve:
                lines.append(line)
                used += _blen(line)
                listed_pending = True
    trailer = []
    if more:
        trailer.append(f"+{more} more (offset={next_offset})")
    quiet = counts(diags)["derived"] if derived is None else derived
    if more or quiet or pending_bind or (pending and offset == 0 and not listed_pending):
        trailer.append(summary_line(diags, pending_bind, derived))
    room = TRAILER_BUDGET
    for line in trailer:
        if _blen(line) > room:
            line = line.encode("utf-8")[: max(0, room - 4)].decode("utf-8", "ignore") + "..."
        lines.append(line)
        room -= _blen(line)
    return "\n".join(lines)


def _json_record(d: Diag) -> str:
    import json

    record = d.as_dict()
    record.update(got=clip(d.got), hint=clip(d.hint, HINT_LIMIT), path=clip(d.path, PATH_LIMIT),
                  expected=clip(d.expected, want_limit(d)), at=clip_at(d.at), rule=clip(d.rule, RULE_LIMIT),
                  severity=clip(d.severity, 8))
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _render_json(shown, more, next_offset, diags, pending, pending_bind, derived) -> str:
    import json

    body = {"counts": counts(diags), "diagnostics": [json.loads(_json_record(d)) for d in shown]}
    if derived is not None:
        body["counts"]["derived"] = derived
    if more:
        body["more"], body["next_offset"] = more, next_offset
    if pending_bind:
        body["pending_bind"] = pending_bind
    if pending:
        body["pending"] = {k: (v if isinstance(v, int) else len(v)) for k, v in sorted(pending.items())}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
