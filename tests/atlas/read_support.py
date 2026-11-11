"""Shared builders of the reading-surface tests: one build per (preset, language, seed) cached for the whole run, helpers to write a
``dist/`` tree, to read it back as a ``Surface`` and to edit a written tree the way a defect would (design A3 6.4).

Every model is synthetic (``tests/atlas/synth.py``); nothing here names a real project.
"""
from __future__ import annotations

import copy
import dataclasses
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from gemarc_mcp.atlas import build as BLD
from gemarc_mcp.atlas import reading as R
from gemarc_mcp.atlas.derive import diagram_header as DH
from gemarc_mcp.atlas.reading import check as C
from gemarc_mcp.atlas.reading import envelope as E
from gemarc_mcp.atlas.reading import names as N
from gemarc_mcp.atlas.reading.source import Source

from . import synth

QA = {"rules": {"block": 2, "ERROR": 0, "WARN": 3}, "gate": {"release": False, "blockers": 5},
      "oracle": {"sealed": 0, "revised": 0, "confirmed": 0, "pending": 3, "sheet": "oracle-sheet.md", "ledger": "none", "badge": "none"}}


def meta_for(model_or_bundle: dict) -> dict:
    """The ``sealed_meta`` of a build in which nothing is sealed yet: every case and every intervention of the model is withheld, no state, no verdict.
    It is what ``oracle.sealed_runs`` returns for such a project (``baselines`` and ``withheld`` split the items exactly, RS-115), so a fixture with
    no run and this meta is consistent; ``read_runs`` builds the ones with results."""
    model = model_or_bundle["model"] if "fragments" in model_or_bundle else model_or_bundle
    return {"withheld": {"cases": [c["caseId"] for c in model.get("cases", ()) if isinstance(c, dict) and isinstance(c.get("caseId"), str)],
                         "interventions": [i["interventionId"] for i in model.get("interventions", ()) if isinstance(i, dict) and isinstance(i.get("interventionId"), str)]},
            "states": {}}


UI = {"decision": "none", "reason": "no screens in this synthetic project", "hint": False}
SEEDS = (1, 2, 3, 4, 6, 7, 8)          # seed 5 cannot be put through the generator
PRESETS = ("tiny", "small", "reference", "refcjk")


def model_of(preset: str, seed: int = 1, mode: str = "asis") -> dict:
    if preset == "refcjk":
        return synth.generate(dataclasses.replace(synth.PRESETS["reference"], lang="cjk"), seed, mode=mode)
    return synth.generate(preset, seed, mode=mode)


@dataclass
class Built:
    model: dict
    bundle: dict
    result: object
    src: Source
    lang: str

    def surface(self) -> C.Surface:
        extra = {f"diagrams/{u['id']}.mmd" for u in self.bundle.get("diagrams") or [] if isinstance(u, dict)}
        return C.Surface(dict(self.result.files), [dict(r) for r in self.result.records], dict(self.result.header), extra, list(self.result.exclusions))

    def text(self, path: str) -> str:
        return self.result.files[path].decode("utf-8")


def make(model: dict, lang: str = "zh-CN", sealed_meta=None, ui=UI, cfg=None, qa=QA) -> Built:
    bundle = BLD.make_bundle(model, lang=lang, qa=qa)
    sealed_meta = meta_for(model) if sealed_meta is None else sealed_meta
    result = R.build(bundle, sealed_meta, cfg or R.ReadConfig(), ui)
    return Built(model, bundle, result, Source(bundle, sealed_meta), lang)


@lru_cache(maxsize=None)
def built(preset: str, lang: str = "zh-CN", seed: int = 1, mode: str = "asis", html: bool = False) -> Built:
    """The cached build of a synthetic model; ``html`` writes the HTML twins (``site/``) as well (milestone R2)."""
    return make(model_of(preset, seed, mode), lang, cfg=R.ReadConfig(html=True) if html else None)


def write_dist(b: Built, root: Path, extras: bool = True, real_docs: bool = False):
    """Write the files of the build under ``root`` (and, with ``extras``, what ``build_project`` writes besides: documents, a prompt, the
    diagrams, ``index.html``, ``oracle-sheet.md``, ``build-info.json``, a ``.verify`` file); then ``finalize``: returns the ``Manifest``.
    ``real_docs`` writes the three documents of the project's language (``REQ``, ``BD``, ``DD``) as ``docbuild`` makes them instead of a stub (the
    twin check RS-114 compares the fragments of the pages with them)."""
    root = Path(root)
    for path, data in b.result.files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if extras:
        if real_docs:
            from gemarc_mcp.atlas.derive import docbuild

            bundle = docbuild.get_bundle(b.model, b.lang)
            for level in docbuild.LEVELS:
                target = root / "docs" / b.lang / f"{level}.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(docbuild.document_text(bundle, level).encode("utf-8"))
        for rel, text in (("docs/zh-CN/DD.md", "# detailed design\n\ndocument body\n"), ("prompts/PR-DD-0123abcd.md", "# prompt\n"),
                          ("index.html", "<!doctype html><title>x</title>\n"), ("oracle-sheet.md", "# sheet\n"), ("build-info.json", "{}\n"),
                          (".verify/report.json", "{}\n")):
            if real_docs and rel.startswith("docs/"):
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        header_ctx = DH.HeaderContext.from_model(b.model, b.lang, b.src.stamp)
        for unit in b.bundle.get("diagrams") or []:
            if isinstance(unit, dict) and isinstance(unit.get("id"), str) and isinstance(unit.get("mermaid"), str):
                target = root / "diagrams" / (unit["id"] + ".mmd")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(DH.apply(unit, header_ctx).encode("utf-8"))      # the file as ``docbuild.build_docs`` and the build write it (R3: a header)
    return R.finalize(root, b.result)


class TempDist:
    """A written and finalized ``dist`` in a temporary directory (a context manager)."""

    def __init__(self, b: Built, extras: bool = True, real_docs: bool = False):
        self.built = b
        self.extras = extras
        self.real_docs = real_docs

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="read-dist-"))
        self.manifest = write_dist(self.built, self.tmp, self.extras, self.real_docs)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def surface(self) -> C.Surface:
        return C.surface_from_dist(self.tmp)


# ---------------------------------------------------------------------------------------------------- editing a written tree

def copy_surface(s: C.Surface) -> C.Surface:
    return dataclasses.replace(s, files=dict(s.files), records=[dict(r) for r in s.records], header=dict(s.header), extra_paths=set(s.extra_paths),
                               exclusions=list(s.exclusions), manifest_rows=[dict(r) for r in s.manifest_rows] if s.manifest_rows is not None else None,
                               manifest_header=dict(s.manifest_header) if s.manifest_header is not None else None, strangers=list(s.strangers))


def reseal(lang: str, text: str) -> str:
    """The page text with the line count of the first line and of the sentinel and the sha8 recomputed (an edited page that is still sealed)."""
    import hashlib

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    count = str(len(lines))
    first = E.first_line_regex(lang).match(lines[0])
    last = E.sentinel_regex(lang).match(lines[-1])
    lines[0] = lines[0][:first.start("lines")] + count + lines[0][first.end("lines"):]
    prefix = "\n".join(lines[:-1]) + "\n"
    digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8]
    template = E.TEXT[N.text_lang(lang)]["read.foot.sentinel"]
    lines[-1] = E._fill(template, page=last.group("page"), lines=count, sha8=digest)
    out = "\n".join(lines) + "\n"
    assert E.verify_sentinel(out) == [], E.verify_sentinel(out)
    return out


def refresh_record(rec: dict, data: bytes) -> None:
    import hashlib

    rec["b"], rec["sha8"] = len(data), hashlib.sha256(data).hexdigest()[:8]
    rec["l"] = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)


def edit_page(s: C.Surface, lang: str, path: str, fn, seal: bool = True, record: bool = True) -> C.Surface:
    """A copy of the surface where ``fn(text) -> text`` was applied to a page; sealed again (``seal``) and the manifest row refreshed (``record``)."""
    out = copy_surface(s)
    text = fn(out.files[path].decode("utf-8"))
    if seal:
        text = reseal(lang, text)
    data = text.encode("utf-8")
    out.files[path] = data
    if record:
        for rows in (out.records, out.manifest_rows or []):
            for rec in rows:
                if rec["p"] == path:
                    refresh_record(rec, data)
    return out


def edit_file(s: C.Surface, path: str, fn) -> C.Surface:
    """The same for a plain file (``ids.tsv``, ``refs.tsv``): ``fn(text) -> text`` and the row refreshed."""
    out = copy_surface(s)
    data = fn(out.files[path].decode("utf-8")).encode("utf-8")
    out.files[path] = data
    for rows in (out.records, out.manifest_rows or []):
        for rec in rows:
            if rec["p"] == path:
                refresh_record(rec, data)
    return out


def rules_of(findings, klass: str = "I", severity: str = "ERROR") -> set:
    return {f.rule for f in findings if f.klass == klass and f.severity == severity}


def check(b: Built, surface: C.Surface, only=None):
    return C.check_surface(b.src, surface, only)


# ---------------------------------------------------------------------------------------------------- models that force continuation

def hub_model(extra_edges: int = 160, long_case_events: int = 0, seed: int = 1) -> dict:
    """The ``small`` model with one node that ends ``extra_edges`` more edges (a hub: its inbound table is longer than a page) and, when asked,
    one more case with ``long_case_events`` events (an event table longer than a page)."""
    model = copy.deepcopy(synth.generate("small", seed))
    nodes = [n["id"] for n in model["nodes"]]
    hub = nodes[0]
    template = next(e for e in model["edges"] if e["sourceId"] != hub and e["targetId"] != hub)
    for n in range(extra_edges):
        edge = copy.deepcopy(template)
        source = nodes[1 + n % (len(nodes) - 1)]
        edge["id"] = f"{source}:calls:{hub}:{n}"
        edge["sourceId"], edge["targetId"] = source, hub
        edge["sourcePortId"] = edge["targetPortId"] = None
        model["edges"].append(edge)
    if long_case_events:
        case = copy.deepcopy(model["cases"][0])
        case["caseId"] = "LZ3-LONG-1"
        base = case["events"]
        case["events"] = []
        for n in range(long_case_events):
            event = copy.deepcopy(base[n % len(base)])
            event["seq"] = n + 1
            case["events"].append(event)
        model["cases"].append(case)
    return model
