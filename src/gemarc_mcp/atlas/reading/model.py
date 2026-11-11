"""The data types of the reading surface: configuration, findings, the result of ``reading.build`` (design A3 sections 2.10, 6.0, 12.1).

Plain dataclasses, no I/O. A *finding* carries the rule id, its class (``I`` integrity, ``L`` input limit, ``T`` test only, design 6.0)
and a severity; only an ``I`` finding of severity ``ERROR`` makes ``ReadResult.status`` ``invalid``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

SCHEMA_READ = "gemarc-atlas-read/1"
SCHEMA_MANIFEST = "gemarc-atlas-manifest/1"
SCHEMA_IDS = "gemarc-atlas-ids/1"
SCHEMA_REFS = "gemarc-atlas-refs/1"
READ_VERSION = 1


@dataclass(frozen=True)
class ReadConfig:
    """What the caller decides (lane I builds it from ``atlas.toml [read]``).

    ``html``      write the HTML twins (``site/``) of the pages ``reading.build`` makes (milestone R2, ``render_html``); no Markdown byte depends on it.
                  The default here is ``False`` (a pure function with nothing written unless asked); lane I builds the config from ``atlas.toml
                  [read] html``, whose default is ``true`` (design 7.1)
    ``docs``      ``docs/`` is written by the same build, so ``docs/README.md`` is part of the result
    ``sizes``     ``{path: bytes}`` of files the build knows and the page builder cannot (``index.html``, the prompts); only used to round the sizes
                  START prints in KB; a missing entry is simply not printed
    """

    html: bool = False
    docs: bool = True
    sizes: Mapping = field(default_factory=dict)


@dataclass
class Finding:
    rule: str
    klass: str                  # "I" | "L" | "T" | "INFO"
    severity: str               # "ERROR" | "WARN" | "INFO"
    path: str                   # the file (or "" when none)
    message: str
    hint: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "class": self.klass, "severity": self.severity, "path": self.path, "message": self.message, "hint": self.hint}


@dataclass
class ReadResult:
    """The pure result of ``reading.build``: nothing is written. ``files`` is ``{path relative to dist/: bytes}``; ``records`` the manifest
    rows of those files (the manifest itself is written last by ``finalize``); ``findings`` every finding of every class."""

    status: str                                    # "ok" | "invalid"
    files: dict = field(default_factory=dict)
    records: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    header: dict = field(default_factory=dict)     # the facts of the manifest header that build knows
    plan: dict = field(default_factory=dict)       # summary: pages per family, bytes, envelope ratio ...
    ids: list = field(default_factory=list)        # ids.tsv rows as tuples
    refs: list = field(default_factory=list)       # refs.tsv rows as tuples
    exclusions: list = field(default_factory=list)  # (group, count, reason) of the fragments Q01 lists as not printed

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.klass == "I" and f.severity == "ERROR"]

    @property
    def degraded(self) -> list:
        return sorted({f.rule for f in self.findings if f.klass == "L"})

    @property
    def ok(self) -> bool:
        return self.status == "ok"
