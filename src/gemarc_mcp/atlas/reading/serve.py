"""The reader of the written reading surface, for ``gemarc_atlas_doc`` and ``gemarc_atlas_index`` (design A3 5.2, 5.3, 2.9).

Nothing here recomputes a fact: it reads ``manifest.jsonl`` and the page files that ``build`` wrote (every read goes through
``slices.read_limited``: the size is checked before a byte is read), compares the stamp of the page with the stamp the caller says is
current, and answers with the page text, its neighbours and the stamp. Failures are ``status: "invalid"`` replies with a code and no text.

Codes (``GEMARC_ATLAS_DOC_*``): ``020`` the stamp of the page is not the current model stamp (no text is returned), ``021`` there is no build
yet, ``022`` no such page (the three closest ids are listed), ``023`` result pages (families C and V) are files and are never served here,
the reply carries the stamp.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from .. import slices as sl
from . import names as N

E_STALE, E_NO_BUILD, E_NO_PAGE, E_RESULT = ("GEMARC_ATLAS_DOC_020", "GEMARC_ATLAS_DOC_021", "GEMARC_ATLAS_DOC_022", "GEMARC_ATLAS_DOC_023")
MANIFEST = "manifest.jsonl"
MANIFEST_LIMIT = 4 * 1024 * 1024
PAGE_LIMIT = 64 * 1024
RESULT_FAMILIES = ("C", "V")


def read_manifest(dist) -> tuple:
    """``(header, {id: row})`` of ``dist/manifest.jsonl``; ``(None, {})`` when there is none or it cannot be read."""
    path = Path(dist) / MANIFEST
    try:
        data = sl.read_limited(path, MANIFEST_LIMIT)
    except (OSError, sl.TooBig):
        return None, {}
    header = None
    rows: dict = {}
    for n, line in enumerate(data.decode("utf-8", "replace").split("\n")):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            return None, {}
        if n == 0:
            header = obj
        elif isinstance(obj, dict) and obj.get("id"):
            rows[obj["id"]] = obj
    return header, rows


def stamp(dist, current) -> dict:
    """``{"built": <8 hex> | None, "current": ..., "stale": bool | None}`` from the manifest header (``index scope="stamp"``)."""
    header, _rows = read_manifest(dist)
    built = header.get("m") if header else None
    return {"built": built, "current": current, "stale": None if (built is None or current is None) else built != current}


def stamp_line(dist, current) -> str:
    s = stamp(dist, current)
    return f"built={s['built'] or 'none'} current={s['current'] or 'none'} stale={'true' if s['stale'] else 'false'}"


def _invalid(code: str, message: str, **extra) -> dict:
    return {"status": "invalid", "authority": "local", "code": code, "message": message, **extra}


def load_page(dist, page_id: str, current=None) -> dict:
    """The reply for ``doc unit="page:<id>"`` (``§S07`` or ``S07``). ``current`` is the stamp of the current model (``None``: not checked)."""
    header, rows = read_manifest(dist)
    if header is None:
        return _invalid(E_NO_BUILD, "no reading surface has been built yet: run gemarc_atlas_build")
    s = stamp(dist, current)
    bare = N.parse_page_ref(page_id)
    row = rows.get(N.page_ref(bare)) if bare else None
    if row is None or row.get("k") not in ("page", "index", "start", "glossary"):
        known = sorted(key for key, r in rows.items() if r.get("k") in ("page", "index", "start", "glossary"))
        close = difflib.get_close_matches(page_id if str(page_id).startswith(N.SECTION_SIGN) else N.SECTION_SIGN + str(page_id), known, n=3, cutoff=0.0)
        return _invalid(E_NO_PAGE, "no such page", closest=close, stamp=s)
    if row.get("fam") in RESULT_FAMILIES or set(row.get("fams") or ()) & set(RESULT_FAMILIES):     # a folded page that holds a result family is a file too
        return _invalid(E_RESULT, "result pages are files: Read them, they are never served here", id=row["id"], path=row["p"], stamp=s)
    if s["stale"]:
        return _invalid(E_STALE, "the page was built from an older model: run gemarc_atlas_build", id=row["id"], stamp=s)
    path = Path(dist) / row["p"]
    try:
        text = sl.read_limited(path, PAGE_LIMIT).decode("utf-8")
    except (OSError, sl.TooBig):
        return _invalid(E_NO_BUILD, "the page file is missing or too large: run gemarc_atlas_build", id=row["id"], stamp=s)
    reply = {"status": "ok", "authority": "local", "unit": "page:" + row["id"], "id": row["id"], "family": row.get("fam"), "dim": row.get("dim"),
             "cls": row.get("cls"), "lang": header.get("lang"), "path": row["p"], "bytes": len(text.encode("utf-8")), "lines": text.count("\n"),
             "tokEst": row.get("tok"), "sha8": row.get("sha8"), "stamp": s, "text": text}
    for key in ("prev", "next", "up"):
        if row.get(key):
            reply[key] = row[key]
    return reply


def page_list(dist, dim: str | None = None) -> list:
    """One line per page of a dimension (all pages when ``dim`` is None), the rows ``index scope="pages[:dim]"`` echoes: ``§S07 page 2D 8,841B ~4.0k <sum>``."""
    header, rows = read_manifest(dist)
    out = []
    for key in sorted(rows, key=lambda k: rows[k]["p"]):
        row = rows[key]
        if row.get("k") != "page" or (dim and row.get("dim") != dim):
            continue
        out.append(f"{key} {row.get('cls')} {row.get('dim') or '-'} {row.get('b', 0):,}B ~{row.get('tok', 0) / 1000:.1f}k {row.get('sum', '')}".strip())
    return out
