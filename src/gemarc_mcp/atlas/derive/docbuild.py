"""Write the structure-only documents, diagrams and prompts of a model, and answer ``gemarc_atlas_doc``.

* ``build_docs(model, out_dir, langs)`` writes ``docs/<lang>/{REQ,BD,DD}.md``, ``diagrams/<unit id>.mmd`` and
  ``prompts/<prompt id>.md`` (diagrams and prompts in the primary language) under ``out_dir``, deterministically: no
  timestamps, UTF-8, LF, sorted order, so two builds of one model are byte-identical.
* ``doc_outline(model, unit, level, lang, chapter, page)`` is the pure core of the ``gemarc_atlas_doc`` tool. The default
  reply is a compact outline plus lint counts (at most 1 KiB of JSON); a chapter page (at most 20 KiB of Markdown) is
  returned on request. It never raises: a bad argument gives ``{"ok": false, "code": "GEMARC_ATLAS_DOC_00N", ...}``.
* ``viewer_payload(model, lang)`` is what the bundler embeds (fragments, closures and FNV hashes of canonical selections
  for the in-page twin check); ``twin_payload`` feeds the Node twin test.

A *unit* of ``doc_outline`` is what the caller wants an outline of: an element id (node, edge, case, requirement), a
diagram unit id (it stands for the elements it covers), a list of ids, or ``None`` / ``"all"`` for the whole model.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .. import model as am
from . import assemble as A
from . import diagrams as D
from . import fragments as F
from . import i18n as T
from . import lint as L
from . import policy as P
from . import prompts as R

LEVELS = A.LEVELS
REPLY_LIMIT = 1024
PAGE_LIMIT = 20 * 1024
CACHE_SIZE = 8
E_UNIT, E_LEVEL, E_CHAPTER, E_INTERNAL = (f"GEMARC_ATLAS_DOC_{n:03d}" for n in (1, 2, 3, 99))
ALL_UNITS = ("all", "*")

_cache: dict = {}


# --------------------------------------------------------------------------------------------- cached docsets

class Bundle:
    """A docset with the diagram units and the index it was built from (what lint and outline need)."""

    def __init__(self, model, lang: str):
        self.lang = T.normalize_lang(lang)
        built = D.build_units(model, self.lang)
        self.units = built["units"]
        self.unit_issues = built["issues"]
        self.docset = F.build_docset(model, self.lang, self.units)
        self.ix = P.Index(model)
        self.model = model


def get_bundle(model, lang: str = T.PRIMARY_LANG, use_cache: bool = True) -> Bundle:
    """The ``Bundle`` of a model and language; a few are kept, keyed by the model stamp (a changed model builds anew)."""
    lang = T.normalize_lang(lang)
    key = None
    if use_cache:
        try:
            key = (am.model_sha(T.sanitize(dict(model))), lang)
        except Exception:
            key = None
    if key is not None and key in _cache:
        return _cache[key]
    bundle = Bundle(model, lang)
    if key is not None:
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = bundle
    return bundle


def default_langs(model) -> list:
    """The languages a model declares (supported ones only), else the primary language."""
    meta = model.get("meta") if isinstance(model, dict) else None
    declared = meta.get("languages") if isinstance(meta, dict) else None
    langs = [x for x in declared if isinstance(x, str) and x in T.LANGS] if isinstance(declared, list) else []
    return list(dict.fromkeys(langs)) or [T.PRIMARY_LANG]


def document_text(bundle: Bundle, level: str, ids=None) -> str:
    """The whole-model document (``ids=None``) or the document of ``ids``."""
    ds = bundle.docset
    return A.assemble(ds, level, ds["fragments"]["elements"] if ids is None else ids)


def lint_text(bundle: Bundle, level: str, text: str) -> list:
    return L.lint_document(text, lang=bundle.lang, level=level, ix=bundle.ix, units=bundle.units)


# --------------------------------------------------------------------------------------------- build

def _file_name(text: str, used: set) -> str:
    base = T.dg_id(text)
    name, n = base, 1
    while name in used:
        n += 1
        name = f"{base}-{n}"
    used.add(name)
    return name


def build_docs(model, out_dir, langs=None, *, per_requirement: bool = False) -> dict:
    """Write the documents, diagrams and prompts under ``out_dir``; returns the report (no timestamps, deterministic).

    ``langs`` defaults to the languages of ``meta.languages``. With ``per_requirement`` one extra document per requirement
    and level is written under ``docs/<lang>/by-requirement/``. Diagrams and prompts use the first language.
    """
    out = Path(out_dir)
    langs = list(dict.fromkeys(langs)) if langs else default_langs(model)
    primary = langs[0]
    files: dict = {}
    report: dict = {"langs": {}, "files": [], "issues": [], "primary": primary}
    for lang in langs:
        bundle = get_bundle(model, lang)
        ds = bundle.docset
        entry = {"docset": ds["stats"], "unreached": ds["coverage"]["unreached"], "docs": {}}
        for level in LEVELS:
            text = document_text(bundle, level)
            files[f"docs/{lang}/{level}.md"] = text
            issues = lint_text(bundle, level, text)
            entry["docs"][level] = {"bytes": len(text.encode("utf-8")), "lint": L.summarize(issues, text, lang, level)}
        if per_requirement:
            used: set = set()
            for rid in bundle.ix.requirement:
                if rid in ds["closures"]:
                    stem = _file_name(rid, used)
                    for level in LEVELS:
                        files[f"docs/{lang}/by-requirement/{stem}-{level}.md"] = A.assemble(ds, level, [rid])
        report["issues"].extend(ds["issues"])
        report["langs"][lang] = entry
    pbundle = get_bundle(model, primary)
    for name, text in D.diagram_files(pbundle.units).items():
        files[f"diagrams/{name}"] = text
    for name, text in R.prompt_files(model, None, primary, units=pbundle.units).items():
        files[f"prompts/{name}"] = text
    for rel in sorted(files):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = files[rel].encode("utf-8")
        path.write_bytes(data)
        report["files"].append({"path": rel, "bytes": len(data)})
    return report


# --------------------------------------------------------------------------------------------- outline

def _error(code: str, hint: str, **extra) -> dict:
    return {"ok": False, "code": code, "hint": hint, **extra}


def resolve_unit(bundle: Bundle, unit):
    """``(ids, label)`` for an outline unit, or ``(None, error dict)``."""
    elements = bundle.docset["fragments"]["elements"]
    if unit is None or (isinstance(unit, str) and unit in ALL_UNITS):
        return list(elements), "all"
    known = set(elements)
    if isinstance(unit, str):
        if unit in known:
            return [unit], unit
        for u in bundle.units:
            if u["id"] == unit:
                cov = u["covers"]
                ids = [i for i in cov["nodeIds"] + cov["edgeIds"] + cov["caseIds"] if i in known]
                return ids, unit
        return None, _error(E_UNIT, "no element or diagram unit has this id", unit=T.clip(unit, 60))
    if isinstance(unit, (list, tuple)) and all(isinstance(i, str) for i in unit):
        ids = [i for i in unit if i in known]
        if not ids:
            return None, _error(E_UNIT, "none of the ids names an element")
        return ids, T.clip(",".join(unit), 60)
    return None, _error(E_UNIT, "unit must be an id, a list of ids or all", got=type(unit).__name__)


_EMPTYISH = ("[NONE-IN-SCOPE]", "[NONE-DECLARED]", "[NOT-AUTHORED:")


def chapter_states(text: str) -> str:
    """One digit per chapter: 0 explicit empty state only, 1 content, 2 content that still carries a NOT-AUTHORED marker."""
    by_number = {c["number"]: c for c in L.parse_chapters(text)}
    out = []
    for n in range(1, T.CHAPTER_COUNT + 1):
        body = by_number[n]["body"] if n in by_number else []
        lines = [x.strip() for x in body if x.strip() and not x.strip().startswith(("###", ">"))]
        real = [x for x in lines if not x.startswith(_EMPTYISH)]
        if not real:
            out.append("0")
        else:
            out.append("2" if any(T.NOT_AUTHORED_RE.search(x) for x in lines) else "1")
    return "".join(out)


def chapter_text(text: str, number: int) -> tuple:
    """``(title, markdown)`` of one chapter of a rendered document (``("", "")`` when it is missing)."""
    for c in L.parse_chapters(text):
        if c["number"] == number:
            return c["title"], f"## {number}. {c['title']}\n" + "\n".join(c["body"]).rstrip("\n") + "\n"
    return "", ""


def _clip_line(line: str, room: int) -> str:
    data = line.encode("utf-8")
    if len(data) <= room:
        return line
    return data[: max(0, room - 3)].decode("utf-8", "ignore") + "..."


def paginate(text: str, limit: int = PAGE_LIMIT) -> list:
    """Pages of at most ``limit`` UTF-8 bytes, cut between lines; a table that continues on the next page repeats its header."""
    lines = text.rstrip("\n").split("\n")
    pages: list = []
    current: list = []
    size = 0
    header: list = []
    in_table = False
    for i, line in enumerate(lines):
        line = _clip_line(line, limit - 2)
        cost = len(line.encode("utf-8")) + 1
        if size + cost > limit and current:
            pages.append("\n".join(current) + "\n")
            current, size = [], 0
            if in_table and line.startswith("|") and header:
                current = list(header)
                size = sum(len(h.encode("utf-8")) + 1 for h in header)
                if size + cost > limit:
                    current, size = [], 0
        current.append(line)
        size += cost
        if line.startswith("|"):
            if not in_table and i + 1 < len(lines) and set(lines[i + 1]) <= set("|-:") and lines[i + 1].startswith("|"):
                header = [line, lines[i + 1]]
            in_table = True
        else:
            in_table = False
    if current:
        pages.append("\n".join(current) + "\n")
    return pages or [""]


def _compact(reply: dict) -> str:
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":"))


def _fit(reply: dict, limit: int = REPLY_LIMIT) -> dict:
    """Drop optional parts until the compact JSON fits ``limit`` bytes (UTF-8)."""
    for key in ("pages", "unit"):
        if len(_compact(reply).encode("utf-8")) <= limit:
            break
        reply.pop(key, None)
    return reply


def doc_outline(model, unit=None, level: str = "BD", lang: str = T.PRIMARY_LANG, chapter=None, page=1) -> dict:
    """The reply of ``gemarc_atlas_doc`` as a dict (``_compact`` of it is the wire form). Never raises.

    Default (``chapter`` omitted): ``{ok, unit, level, lang, no, bytes, filled, total, ch, junk, links, markers, blocks, pages}``
    in at most 1 KiB, where ``ch`` has one digit per chapter (``chapter_states``), ``links`` counts unresolved links and ids,
    ``junk`` the literal defects and ``markers`` the distinct NOT-AUTHORED slots (release blockers). With ``chapter`` (1..12):
    ``{ok, chapter, page, pages, title, text}``; ``text`` is at most 20 KiB and a continued table repeats its header.
    """
    try:
        if level not in LEVELS:
            return _error(E_LEVEL, "level must be REQ, BD or DD", got=T.clip(level, 20) if isinstance(level, str) else type(level).__name__)
        if chapter is not None and (isinstance(chapter, bool) or not isinstance(chapter, int) or not 1 <= chapter <= T.CHAPTER_COUNT):
            return _error(E_CHAPTER, "chapter must be an integer from 1 to 12")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return _error(E_CHAPTER, "page must be an integer from 1")
        bundle = get_bundle(model, lang)
        ids, label = resolve_unit(bundle, unit)
        if ids is None:
            return label
        text = A.assemble(bundle.docset, level, ids)
        if chapter is not None:
            title, md = chapter_text(text, chapter)
            pages = paginate(md)
            if page > len(pages):
                return _error(E_CHAPTER, "the chapter has fewer pages", pages=len(pages))
            return {"ok": True, "chapter": chapter, "page": page, "pages": len(pages), "title": title, "text": pages[page - 1]}
        issues = lint_text(bundle, level, text)
        summary = L.summarize(issues, text, bundle.lang, level)
        states = chapter_states(text)
        by_rule: dict = {}
        for item in issues:
            by_rule[item["rule"]] = by_rule.get(item["rule"], 0) + 1
        reply = {"ok": True, "unit": label, "level": level, "lang": bundle.lang,
                 "no": text.split("\n", 1)[0].rsplit(" ", 1)[-1], "bytes": len(text.encode("utf-8")),
                 "filled": sum(1 for s in states if s != "0"), "total": T.CHAPTER_COUNT, "ch": states,
                 "junk": summary["junk"], "links": summary["unresolvedLinks"], "markers": summary["notAuthored"],
                 "blocks": summary["blocks"], "other": sum(n for r, n in by_rule.items() if r not in ("DC-L-001", "DC-L-002", "DC-L-004")),
                 "pages": [len(paginate(chapter_text(text, n)[1])) for n in range(1, T.CHAPTER_COUNT + 1)]}
        return _fit(reply)
    except Exception as exc:  # never raise out of the tool core
        return _error(E_INTERNAL, "internal document tool error", got=type(exc).__name__)


def doc_outline_json(model, unit=None, level: str = "BD", lang: str = T.PRIMARY_LANG, chapter=None, page=1) -> str:
    return _compact(doc_outline(model, unit, level, lang, chapter, page))


# --------------------------------------------------------------------------------------------- viewer and twin

def canonical_selections(ds: dict, count: int = 8) -> list:
    """``[{"level", "elementIds"}]``: the empty selection, the whole model and strided slices; deterministic."""
    elements = ds["fragments"]["elements"]
    out = [{"level": "BD", "elementIds": []}, {"level": "DD", "elementIds": list(elements)}]
    for i in range(1, max(0, count - 1)):
        out.append({"level": LEVELS[i % 3], "elementIds": elements[i::i + 2][:12]})
    return out[:count]


def selection_hashes(ds: dict, count: int = 8) -> list:
    """The ``docSelections`` the page recomputes in gate mode: ``{level, elementIds, hash32}`` (FNV-1a of the text)."""
    return [{**s, "hash32": A.hash32_hex(A.assemble(ds, s["level"], s["elementIds"]))} for s in canonical_selections(ds, count)]


def viewer_payload(model, lang: str = T.PRIMARY_LANG, count: int = 8) -> dict:
    """``{"fragments", "closures", "docSelections"}`` for the bundle (see ``fragments.py`` for the shapes)."""
    ds = get_bundle(model, lang).docset
    return {"fragments": ds["fragments"], "closures": ds["closures"], "docSelections": selection_hashes(ds, count)}


_SCRIPT_UNSAFE = "<>&\u2028\u2029"


def embed_json(value) -> str:
    """Compact JSON that is safe inside an inline ``<script>`` block (``viewer_payload`` is meant to go through this).

    Fragment Markdown carries author text (evidence file paths, names), so a raw ``</script`` or ``<!--`` in it would end
    or confuse the script element. ``<``, ``>``, ``&``, U+2028 and U+2029 are written as ``\\uXXXX`` escapes (they occur only
    inside JSON strings, so ``json.loads`` of the result gives the original value back). NaN and infinity are refused.
    """
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for raw in _SCRIPT_UNSAFE:
        text = text.replace(raw, "\\u%04x" % ord(raw))
    return text


class _SplitMix:
    """The same tiny generator as the test data code: seeded, no ``random`` module, no hash order."""

    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFFFFFFFFFF

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next() % n if n > 0 else 0


def twin_cases(ds: dict, requirement_ids=(), seeds: int = 50, seed: int = 1) -> list:
    """Selections for the Python-vs-JavaScript check, each with the digest of the expected text.

    Fixed ones: the empty selection; unknown ids; repeated and reversed ids beyond the six the target line shows; the whole
    model at every level; every requirement alone at all three levels. Then ``seeds`` seeded random selections of 1 to 40
    ids at a random level.
    """
    elements = ds["fragments"]["elements"]
    rng = _SplitMix(seed)
    picks: list = [("BD", []), ("DD", ["no-such-id", "also-missing"]), ("REQ", list(reversed(elements[:9])) + elements[:3] + ["x"])]
    picks.extend((lv, list(elements)) for lv in LEVELS)
    for rid in requirement_ids:
        picks.extend((lv, [rid]) for lv in LEVELS)
    for _ in range(seeds):
        size = 1 + rng.below(min(40, max(1, len(elements))))
        picks.append((LEVELS[rng.below(3)], [elements[rng.below(len(elements))] for _ in range(size)] if elements else []))
    cases = []
    for level, ids in picks:
        text = A.assemble(ds, level, ids)
        data = text.encode("utf-8")
        cases.append({"level": level, "ids": ids, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "hash32": A.hash32_hex(text)})
    return cases


def twin_payload(model, lang: str = T.PRIMARY_LANG, seeds: int = 50, seed: int = 1) -> dict:
    """The docset and the expected digests for ``tests/atlas/js/assemble.test.mjs``."""
    bundle = get_bundle(model, lang, use_cache=False)
    ds = bundle.docset
    requirement_ids = [r for r in bundle.ix.requirement if r in ds["closures"]]
    return {"docset": {"fragments": ds["fragments"], "closures": ds["closures"]}, "cases": twin_cases(ds, requirement_ids, seeds, seed)}


def main(argv=None) -> int:
    """``python -m gemarc_mcp.atlas.derive.docbuild build <model.json> <out_dir> [lang ...]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 3 and args[0] == "build":
        model = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        report = build_docs(model, args[2], args[3:] or None)
        print(json.dumps(report["langs"], ensure_ascii=False, sort_keys=True))
        return 0
    print(main.__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
