"""``reading.build``: the pure function from a bundle to the whole reading surface (design A3 2.10, 12.1).

Order: classify the fragments (``source``), find what the pages do not print and prove it can be rebuilt (``check.ifc_exclusions``), build the
families, plan the pages (``plan``), number them, render them (``pages``), then the cards that need the pages (INDEX, START), the glossary,
``docs/README.md``, ``ids.tsv``, ``refs.tsv``, ``.ignore``, and run the checks (``check``). Nothing is written; the result carries the
files, the manifest rows and every finding. A tree with an I-class ERROR has ``status == "invalid"`` (design D-18).
"""
from __future__ import annotations

import hashlib
import json
import math

from . import check as C
from . import families as F
from . import names as N
from . import plan as PL
from . import quality as Q
from . import render_html as RH
from . import specials as SP
from . import tables as TB
from .model import Finding, ReadConfig, ReadResult
from .pages import render_page, sha8_of, summaries_of, home_map
from .source import Source

INDEX_MIN_PAGES = 4
HTML_BYTES_PER_TOKEN = 1.7        # study-reading: HTML, JSON and TSV cost 1.7 bytes per token at the lowest


def _data_record(path: str, data: bytes, summary: str, m: str) -> dict:
    return {"p": path, "id": "", "k": "data", "fam": "", "dim": None, "cls": "data", "ai": False, "b": len(data), "l": data.count(b"\n"), "tok": 0,
            "sha8": sha8_of(data), "twin": None, "sum": summary, "m": m}


def _twin_record(path: str, data: bytes, md_path: str, stamp: str) -> dict:
    return {"p": path, "id": "", "k": "twin", "fam": "", "dim": None, "cls": "twin", "ai": False, "b": len(data), "l": data.count(b"\n"),
            "tok": math.ceil(len(data) / HTML_BYTES_PER_TOKEN), "sha8": sha8_of(data), "twin": None, "twinOf": md_path, "m": stamp}


def qa_sha8(src: Source) -> str:
    qa = src.meta.get("qa") if isinstance(src.meta.get("qa"), dict) else {}
    return hashlib.sha256(json.dumps(qa, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:8]


def site_sources(rendered: dict) -> list:
    """The Markdown paths of the reading build that have a twin (all of them: pages, START, the glossary, the INDEX shards)."""
    return [r.path for r in rendered.values() if N.twin_path(r.path) is not None]


def build(bundle: dict, sealed_meta=None, cfg: ReadConfig | None = None, ui: dict | None = None) -> ReadResult:
    cfg = cfg or ReadConfig()
    ui = dict(ui or {})
    src = Source(bundle, sealed_meta)
    lang = src.lang
    findings: list = []
    for problem in src.problems:
        findings.append(Finding("RS-128", "I", "ERROR", "", problem))

    exclusions, ex_findings = C.ifc_exclusions(src)
    findings.extend(ex_findings)
    families: dict = {}
    for key, builder in F.BUILDERS.items():
        fam = builder(src)
        if fam.units:
            families[key] = fam
    families["Q"] = Q.build_q(src, exclusions)

    plan = PL.assign(PL.plan_surface(src, families, PL.Plan()))
    for family, unit_key in plan.overflow:
        findings.append(Finding("RS-101", "I", "ERROR", "", f"a single piece of {family} {unit_key!r} does not fit one page"))
    for family, unit_key, parts in plan.splits:
        findings.append(Finding("INFO-SPLIT", "INFO", "INFO", "", f"the unit {unit_key!r} of the family {family} needs {parts} pages",
                                "split this case or machine in the model"))
    home = home_map(plan)
    dim_pages: dict = {}
    for page in plan.pages:
        if page.dim:
            dim_pages[page.dim] = dim_pages.get(page.dim, 0) + 1
    index_pages = {dim: "INDEX.md" for dim, n in dim_pages.items() if n >= INDEX_MIN_PAGES}
    summaries = summaries_of(plan)
    rendered: dict = {}
    for page in plan.pages:
        rendered[page.pid] = render_page(src, page, plan, summaries, home, index_pages)
    index_cards: list = []
    for dim in sorted(index_pages):
        entries = [rendered[p.pid] for p in plan.pages if p.dim == dim]
        index_cards.extend(SP.build_index(src, dim, entries))
    for card in index_cards:
        rendered[card.pid] = card
    glossary = SP.build_glossary(src)
    rendered[glossary.pid] = glossary
    start, start_degraded = SP.build_start(src, plan, rendered, index_pages, ui, cfg)
    rendered[start.pid] = start
    if start_degraded:
        findings.append(Finding("RS-132", "L", "WARN", "START.md", "START is above the card cap: the reading hints per client moved to the glossary",
                                "shorten the project id or the number of families"))

    pages_for_tables = [rendered[p.pid] for p in plan.pages]
    ids = TB.ids_rows(src, pages_for_tables)
    refs, unprinted = TB.refs_rows(src, pages_for_tables)
    files: dict = {}
    records: list = []
    for r in rendered.values():
        files[r.path] = r.text.encode("utf-8")
        records.append(r.record)
    if cfg.html:
        failed: list = []
        site = RH.render_site({r.path: r.text for r in rendered.values()}, lang, problems=failed)
        for path, message in failed:
            findings.append(Finding("RS-114", "I", "ERROR", path, f"the twin cannot be rendered: {message}"))
        for md_path in sorted(site_sources(rendered)):
            twin = N.twin_path(md_path)
            if twin not in site:
                continue
            files[twin] = site[twin]
            records.append(_twin_record(twin, site[twin], md_path, src.stamp))
        for r in rendered.values():
            r.record["twin"] = N.twin_path(r.path) if N.twin_path(r.path) in site else None
    ids_data = TB.ids_text(src, ids).encode("utf-8")
    refs_data = TB.refs_text(src, refs).encode("utf-8")
    ignore = SP.ignore_bytes()
    for path, data, summary in (("ids.tsv", ids_data, "id kind role path line hdr name"), ("refs.tsv", refs_data, "target field owner path line"),
                                (".ignore", ignore, "rg ignore list")):
        files[path] = data
        records.append(_data_record(path, data, summary, src.stamp))
    if cfg.docs:
        path, data, rec = SP.build_docs_readme(src)
        files[path] = data
        records.append(rec)
    records.sort(key=lambda r: r["p"])

    qa = src.meta.get("qa") if isinstance(src.meta.get("qa"), dict) else {}
    gate = qa.get("gate") if isinstance(qa.get("gate"), dict) else {}
    withheld = src.sealed_meta.get("withheld") if isinstance(src.sealed_meta.get("withheld"), dict) else {}
    content = "sealed" if any(r.content == "sealed" for r in rendered.values()) else "structure"
    header = {"m": src.stamp, "project": src.project, "lang": lang, "mode": src.mode, "content": content, "blockers": gate.get("blockers", 0),
              "qaSha8": qa_sha8(src), "withheldCount": sum(len(withheld.get(k, [])) for k in ("cases", "interventions") if isinstance(withheld.get(k), list))}
    decision = ui.get("decision")
    header["ui"] = {"decision": decision if decision else "undeclared"}
    if ui.get("u"):
        header["u"] = ui["u"]
    result = ReadResult("ok", files, records, findings, header, {}, ids, refs, exclusions)
    result.plan = {"mode": plan.mode, "pages": {k: len(plan.family_pages(k)) for k in ("S", "D", "M", "W", "R", "T", "E", "C", "V", "Q")},
                   "folded": list(plan.folded), "unprinted_refs": unprinted, "refs": len(refs), "ids": len(ids)}
    findings.extend(C.run(src, bundle, result, plan, rendered, index_pages, cfg))
    result.status = "invalid" if result.errors else "ok"
    return result
