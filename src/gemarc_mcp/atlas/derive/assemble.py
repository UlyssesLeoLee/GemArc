"""Assemble a document from a docset: union of closures, sort by ``(chapter, order)``, join.

This is the Python twin of ``viewer/src/assemble.js``. Both read the docset of ``fragments.build_docset`` and must give
the same bytes for the same ``(level, ids)``; ``tests/atlas/js/assemble.test.mjs`` (Node, CI only) asserts that for 50
seeded selections and every requirement, and every build embeds ``hash32_hex`` hashes of a few canonical selections so
that the page recomputes them (twin drift fails the build).

``assemble(docset, level, ids)``:

1. the fragment ids of a level are those of ``fragments.always`` plus the closures of the selected ids, restricted to the
   levels flagged in each packed entry (``fid * 8 + levelMask``, bit 0 REQ, bit 1 BD, bit 2 DD);
2. they are sorted by ``(chapter, order)`` and bucketed by group;
3. the frame of the level lists the twelve chapters and, per chapter, its groups in order: a group with members prints its
   head (table header lines) once and then its members; a group without members prints its ``empty`` text if it has one;
   a chapter that printed nothing prints its own explicit empty line;
4. the document number placeholders are replaced: the number of a level is ``<RD|BD|DD>-`` plus the FNV-1a hash of the
   level, the model stamp and the fragment ids of that level. The number of a document therefore changes with the model
   and with the selection, and the upper and lower numbers of chapter 1 are those of the other two levels of the same
   selection.

Unknown ids are ignored; ids repeat or come in any order without changing the result (canonical order = ``elements``).
"""
from __future__ import annotations

LEVELS = ("REQ", "BD", "DD")
PREFIX = ("RD", "BD", "DD")
SHIFT = 3
TARGET_LIMIT = 6
PH_DOCNO = "\x01DOCNO\x01"
PH_UPPER = "\x01UPPER\x01"
PH_LOWER = "\x01LOWER\x01"
PH_TARGET = "\x01TARGET\x01"


def hash32_hex(text: str) -> str:
    """FNV-1a (32 bit) of the UTF-8 bytes of ``text`` as 8 lowercase hex digits."""
    h = 2166136261
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def selected(fs: dict, ids) -> list:
    """The known ids of ``ids`` once each, in canonical (``elements``) order."""
    pos = {eid: n for n, eid in enumerate(fs["elements"])}
    return sorted({i for i in ids if isinstance(i, str) and i in pos}, key=pos.__getitem__)


def fragment_ids(ds: dict, picked, level_index: int) -> list:
    """Fragment ids of one level for the selected ids, sorted by ``(chapter, order)``."""
    fs, closures = ds["fragments"], ds["closures"]
    items, bit = fs["items"], 1 << level_index
    out = {x >> SHIFT for x in fs["always"] if x & bit}
    for eid in picked:
        out.update(x >> SHIFT for x in closures[eid] if x & bit)
    return sorted(out, key=lambda f: (items[f]["chapter"], items[f]["order"]))


def document_number(fs: dict, level_index: int, fids) -> str:
    return PREFIX[level_index] + "-" + hash32_hex(LEVELS[level_index] + "\n" + fs["modelSha8"] + "\n" + ",".join(map(str, fids)))


def assemble(ds: dict, level: str, ids) -> str:
    """The Markdown document of ``level`` for the selected ``ids`` (an empty list gives the fixed skeleton)."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    fs = ds["fragments"]
    k = LEVELS.index(level)
    picked = selected(fs, ids)
    sets = [fragment_ids(ds, picked, n) for n in range(3)]
    numbers = [document_number(fs, n, sets[n]) for n in range(3)]
    items, groups, frame = fs["items"], fs["groups"], fs["frame"][level]
    by: dict = {}
    for f in sets[k]:
        by.setdefault(items[f]["grp"], []).append(items[f]["md"])
    parts = [frame["head"]]
    for ch in frame["chapters"]:
        parts.append(ch["h"])
        if ch["note"]:
            parts.append(ch["note"])
        body = []
        for gid in ch["groups"]:
            g, members = groups[gid], by.get(gid)
            if members:
                body.append(g["join"].join(([g["head"]] if g["head"] else []) + members))
            elif g["empty"]:
                body.append(g["empty"])
        parts.append("\n\n".join(body) if body else ch["empty"])
    target = fs["unselected"]
    if picked:
        shown = ", ".join("`" + i.replace("`", "'").replace("|", "\\|") + "`" for i in picked[:TARGET_LIMIT])
        target = shown + (f" +{len(picked) - TARGET_LIMIT}" if len(picked) > TARGET_LIMIT else "")
    text = "\n\n".join(parts) + "\n"
    for placeholder, value in ((PH_DOCNO, numbers[k]), (PH_UPPER, numbers[k - 1] if k > 0 else ""),
                               (PH_LOWER, numbers[k + 1] if k < 2 else ""), (PH_TARGET, target)):
        text = text.replace(placeholder, value)
    return text
