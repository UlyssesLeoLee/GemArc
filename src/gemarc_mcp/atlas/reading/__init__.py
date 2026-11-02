"""The reading surface: split Markdown pages an AI reads without an over-long context (design A3).

Public surface (frozen in ``api``): ``build(bundle, sealed_meta, cfg, ui) -> ReadResult`` (a pure function: nothing is written),
``finalize(dist_dir, result) -> Manifest`` (scans ``dist/`` after the build wrote every file and writes ``manifest.jsonl`` last) and
``serve.load_page(dist, page_id, current)`` (the reader of the written files for the tools). Atlas core is standard library only.

``build`` takes only the ten-key bundle of ``build.make_bundle`` (its fragments, closures, model and meta) and ``sealed_meta``; every fact on a page is
in one of them (design 2.10). ``ui`` is a small mapping about the prototype screens that START prints: ``{"decision": "" | "yes" | "only" |
"none", "reason": str, "entry": "screens/INDEX.md" | "", "hint": bool}``; ``None`` means "no decision recorded".

``sealed_meta_from_reply(reply, verdicts=None) -> dict`` is the one adapter from the reply of ``oracle.sealed_runs`` to the ``sealed_meta`` of ``build`` (R3).

``render_site(files, lang, gallery=None) -> {twin path: bytes}`` writes the HTML twins (``site/``) of the Markdown pages of ``files`` (design D-2,
``render_html``); ``build`` calls it itself when ``ReadConfig.html`` is set, lane I calls it for the screens INDEX and NAV that lane U writes.
"""
from __future__ import annotations

from . import serve  # noqa: F401  (``reading.serve.load_page``)
from .manifest import Manifest, finalize
from .model import Finding, ReadConfig, ReadResult
from .pipeline import build
from .render_html import render_site, render_twin
from .source import sealed_meta_from_reply

__all__ = ["Finding", "Manifest", "ReadConfig", "ReadResult", "build", "finalize", "render_site", "render_twin", "sealed_meta_from_reply", "serve"]
