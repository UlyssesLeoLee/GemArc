"""``build_screens``: the pure builder of the ``screens/`` tree (design A3 sections 4.3-4.5, 6.0, 12.1).

``build_screens(ui_model, five_index, cfg) -> ScreensResult`` takes an expanded ``gemarc-atlas-ui/1`` model, the index of the 5D model it may
link to (or ``None``) and a ``ScreensConfig``, and returns every file of ``screens/`` (and the galleries of ``site/screens/``) as bytes, one record
per file for the manifest, the rows of ``ids.tsv`` it anchors, the findings and the build-info. It writes nothing, reads nothing but the packaged
sentences, and is deterministic: the same model and configuration give byte-identical files (no clock, no path, no random, no set order).

Degradation (class L, design 6.0): the build never fails because an author's data is large. A table draws fewer sample rows (5, then 3, then 0)
until its SVG fits 20,000 bytes; a gallery that would exceed 40,000 bytes inlines only the default state; a card that does not fit a page continues
on further cards; an index or navigation page that does not fit is cut into parts. Each step is a finding with a rule id, listed in ``degraded``,
and gets one English ``repair`` line that names the screen (and the slice when the configuration knows it). Class I findings (``check.py``: hash,
sentinel, round trip, consistency of card and SVG) make ``status`` ``invalid``; they can only come from a bug of the generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..reading import api as reading
from . import card as cardmod
from . import expand as ex
from . import gallery as gal
from . import index as idx
from . import layout as lay
from . import model as um
from . import nav as navmod
from . import svg as svgmod
from . import textfit as tf
from . import vocab

SVG_CAP = reading.BUDGETS["cap_svg"]
SAMPLE_STEPS = (lay.MAX_SAMPLE_ROWS, 3, 0)


@dataclass(frozen=True)
class ScreensConfig:
    """What the builder needs besides the model. ``model_sha8`` is the 5D ``model_sha`` prefix; it is used (``m=`` in the pages, ``data-model-sha8``
    in the SVG) only when the model has at least one link that was validated against a non-empty 5D index. ``slices`` maps a screen id to the name
    of the slice that defines it (for the repair lines)."""

    lang: "str | None" = None
    project: "str | None" = None
    model_sha8: "str | None" = None
    html: bool = True
    slices: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    rule: str
    cls: str                  # "I", "L" or "T"
    severity: str             # "ERROR", "WARN" or "INFO"
    path: str
    message: str
    repair: str = ""          # one English line that tells the author what to change (class L steps that need a human decision)

    def as_dict(self) -> dict:
        out = {"rule": self.rule, "class": self.cls, "severity": self.severity, "path": self.path, "message": self.message}
        if self.repair:
            out["repair"] = self.repair
        return out


@dataclass
class ScreenAssets:
    screen: str
    enc: str
    states: list                    # StateLayout, default first
    svgs: dict                      # variant -> svg text
    files: dict                     # variant -> svg file name (relative to screens/)
    pages: list                     # CardPage
    card: cardmod.CardModel
    gallery: "str | None"
    inlined: list
    sample_rows: int
    findings: list = field(default_factory=list)


@dataclass
class ScreensResult:
    files: dict = field(default_factory=dict)         # path (relative to dist/) -> bytes
    records: list = field(default_factory=list)       # one dict per file for the manifest
    ids: list = field(default_factory=list)           # (id, kind, role, path, line, hdr, name)
    findings: list = field(default_factory=list)      # Finding
    degraded: list = field(default_factory=list)      # sorted unique rule ids of the class L steps taken
    repairs: list = field(default_factory=list)       # English repair lines
    info: dict = field(default_factory=dict)
    status: str = "ok"                                # "ok" | "invalid"
    ui_sha8: str = ""
    model_sha8: "str | None" = None
    twins: dict = field(default_factory=dict)         # Markdown card path (every part) -> gallery path
    assets: dict = field(default_factory=dict)        # screen id -> ScreenAssets (for the checks)
    lang: str = "zh-CN"
    sample_rows: dict = field(default_factory=dict)

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def screens_dir(self) -> dict:
        return {p: b for p, b in self.files.items() if p.startswith("screens/")}


def coerce_config(cfg, ui_model: Mapping) -> ScreensConfig:
    """A ``ScreensConfig`` from ``None``, a mapping, a config object or a ``ScreensConfig``; language and project default to the model's."""
    if isinstance(cfg, ScreensConfig):
        base = cfg
    elif isinstance(cfg, Mapping):
        base = ScreensConfig(**{k: v for k, v in cfg.items() if k in ScreensConfig.__dataclass_fields__})
    elif cfg is None:
        base = ScreensConfig()
    else:
        base = ScreensConfig(**{k: getattr(cfg, k) for k in ScreensConfig.__dataclass_fields__ if hasattr(cfg, k)})
    meta = ui_model.get("meta", {}) if isinstance(ui_model, Mapping) else {}
    langs = meta.get("languages") or ["zh-CN"]
    return ScreensConfig(base.lang or langs[0], base.project or str(meta.get("projectId") or "project"), base.model_sha8, base.html, dict(base.slices))


def has_validated_links(model: Mapping, five) -> bool:
    """Whether the model has at least one link to 5D and a non-empty 5D index to validate it against (``data-model-sha8`` and ``m=`` follow)."""
    if five is None or getattr(five, "empty", True):
        return False
    for s in model.get("screens", ()):
        if s.get("actorId") or s.get("reqIds"):
            return True
    for w in model.get("widgets", ()):
        if w.get("apiId") or w.get("reqIds"):
            return True
    for n in model.get("nav", ()):
        if n.get("apiId") or n.get("caseId") or n.get("ruleId") or n.get("reqIds"):
            return True
    return any(v.get("machineId") for v in model.get("variants", ()))


def _has_sample_rows(index: lay.ModelIndex, screen: Mapping) -> bool:
    return any(index.widgets[w]["kind"] == "table" and lay._sample(index.widgets[w])[1] for w in screen.get("widgetIds", ()) if w in index.widgets)


def screen_slice(cfg: ScreensConfig, screen_id: str) -> str:
    name = cfg.slices.get(screen_id)
    return f"slice {name}: " if name else ""


def assemble_screen(index: lay.ModelIndex, screen: Mapping, *, lang: str, ui_sha8: str, model_sha8: "str | None", project: str, enc_name: str,
                    checkpoints: int, enc_of: Mapping, html: bool, cfg: "ScreensConfig | None" = None, sample_rows: "int | None" = None,
                    stamp: str = "") -> ScreenAssets:
    """Layouts, SVGs, card pages and gallery of one screen. Without ``sample_rows`` the steps 5, 3, 0 are tried until every SVG fits 20,000 bytes."""
    sid = screen["id"]
    cfg = cfg or ScreensConfig()
    steps = (sample_rows,) if sample_rows is not None else (SAMPLE_STEPS if _has_sample_rows(index, screen) else (lay.MAX_SAMPLE_ROWS,))
    findings: list = []
    for rows in steps:
        states = [lay.layout_state(index, sid, v, rows, lang, ui_sha8) for v in screen["variantNames"]]
        svgs = {st.variant: svgmod.render_state(index, st, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8) for st in states}
        biggest = max(len(t.encode("utf-8")) for t in svgs.values())
        if biggest <= SVG_CAP:
            break
    prefix = screen_slice(cfg, sid)
    if rows != steps[0]:
        findings.append(Finding("SV-204", "L", "WARN", f"screens/{enc_name}.svg",
                                f"screen {sid}: sample table rows reduced from {steps[0]} to {rows} so that every SVG fits {SVG_CAP} bytes"))
    if biggest > SVG_CAP:
        findings.append(Finding("SV-204", "L", "WARN", f"screens/{enc_name}.svg", f"screen {sid}: its largest state is {biggest} bytes, above the {SVG_CAP} bytes budget",
                                f"{prefix}split screen {sid}: {len(screen.get('widgetIds', ()))} widgets in one screen make a {biggest}-byte SVG, above the {SVG_CAP}-byte budget"))
    for st in states:
        for f in st.findings:
            findings.append(Finding(f["rule"], "L", "WARN", f"screens/{enc_name}.svg", f"screen {sid}, widget {f['widget']}: {f['message']}"))
    files = {st.variant: svgmod.file_name(enc_name, st.variant) for st in states}
    svg_bytes = {v: len(t.encode("utf-8")) for v, t in svgs.items()}
    card = cardmod.build(index, sid, states, enc_name=enc_name, files=files, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8, project=project,
                         checkpoints=checkpoints, svg_bytes=svg_bytes, stamp=stamp)
    pages = cardmod.pages(card, lang)
    if len(pages) > 1:
        findings.append(Finding("RS-101", "L", "WARN", pages[0].path, f"screen {sid}: its card needs {len(pages)} pages",
                                f"{prefix}split screen {sid}: its card needs {len(pages)} pages, consider fewer widgets or states"))
    gallery, inlined = None, [st.variant for st in states]
    if html:
        triples = [(st.variant, "" if st.variant == vocab.DEFAULT_VARIANT else st.note_text, svgs[st.variant]) for st in states]
        gallery = gal.render(card, pages[0], triples, inlined, enc_of, lang, [])
        if len(gallery.encode("utf-8")) > gal.MAX_BYTES and len(states) > 1:
            inlined = [vocab.DEFAULT_VARIANT]
            rest = [(st.variant, files[st.variant]) for st in states if st.variant != vocab.DEFAULT_VARIANT]
            gallery = gal.render(card, pages[0], triples, inlined, enc_of, lang, rest)
            findings.append(Finding("SV-213", "L", "WARN", gal.path_of(enc_name), f"screen {sid}: the gallery would exceed {gal.MAX_BYTES} bytes, only the default state is inlined",
                                    f"{prefix}split screen {sid}: {len(screen.get('widgetIds', ()))} controls x {len(states)} states exceed the gallery size, the gallery shows the default state only"))
        if len(gallery.encode("utf-8")) > gal.MAX_BYTES:
            findings.append(Finding("SV-213", "L", "WARN", gal.path_of(enc_name), f"screen {sid}: its gallery is {len(gallery.encode('utf-8'))} bytes even with one state",
                                    f"{prefix}split screen {sid}: its gallery is larger than {gal.MAX_BYTES} bytes even with the default state only"))
    return ScreenAssets(sid, enc_name, states, svgs, files, pages, card, gallery, inlined, rows, findings)


def _name(text: str) -> str:
    return tf.card_text(text)[:40].replace("\t", " ")


def build_screens(ui_model: Mapping, five_index=None, cfg=None, *, check: bool = True) -> ScreensResult:
    """The ``screens/`` tree of a model (module doc). ``check=False`` skips the class I checks (a caller that runs ``check.check_result`` itself)."""
    cfg = coerce_config(cfg, ui_model)
    five_index = ex.five_index(five_index)          # a mapping (an expanded 5D model) is indexed here, an index is used as it is
    lang, project = cfg.lang, cfg.project
    result = ScreensResult(lang=lang)
    screens = list(ui_model.get("screens", ())) if isinstance(ui_model, Mapping) else []
    ui_sha8 = um.ui_sha8(ui_model) if isinstance(ui_model, Mapping) else ""
    result.ui_sha8 = ui_sha8
    if not screens:
        result.info = {"schema": um.BUILD_SCHEMA, "status": "empty"}
        return result
    model_sha8 = cfg.model_sha8 if has_validated_links(ui_model, five_index) else None
    result.model_sha8 = model_sha8
    index = lay.ModelIndex(ui_model)
    enc_of = reading.enc_many([s["id"] for s in screens])
    per_screen: dict = {}
    for c in um.checkpoints(ui_model):
        per_screen[c.screen] = per_screen.get(c.screen, 0) + 1
    stamps = um.screen_hashes(ui_model)
    rows: dict = {}
    degraded: set = set()
    for s in screens:
        sid = s["id"]
        a = assemble_screen(index, s, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8, project=project, enc_name=enc_of[sid], checkpoints=per_screen.get(sid, 0),
                            enc_of=enc_of, html=cfg.html, cfg=cfg, stamp=stamps.get(sid, ""))
        result.assets[sid] = a
        result.sample_rows[sid] = a.sample_rows
        for variant in [st.variant for st in a.states]:
            path = f"screens/{a.files[variant]}"
            result.files[path] = a.svgs[variant].encode("utf-8")
            result.records.append({"path": path, "kind": "svg", "cls": "svg", "ai": True, "screen": sid, "variant": variant, "twin": None})
        for page in a.pages:
            path = f"screens/{reading.card_path(a.enc, page.part).split('/', 1)[1]}"
            result.files[path] = page.text.encode("utf-8")
            result.records.append({"path": path, "kind": "card", "cls": "page", "ai": True, "screen": sid, "page": page.page, "part": page.part, "parts": page.parts,
                                   "twin": gal.path_of(a.enc) if a.gallery is not None else None})
            if a.gallery is not None:
                result.twins[path] = gal.path_of(a.enc)
            for rid, kind, line, hdr in page.anchors:
                label = index.widgets[rid]["label"] if rid in index.widgets else rid
                result.ids.append((rid, kind, "home", path, line, hdr, _name(label)))
        if a.gallery is not None:
            result.files[gal.path_of(a.enc)] = a.gallery.encode("utf-8")
            result.records.append({"path": gal.path_of(a.enc), "kind": "gallery", "cls": "html", "ai": False, "screen": sid, "twinOf": f"screens/{reading.card_path(a.enc, 1).split('/', 1)[1]}"})
        for f in a.findings:
            result.findings.append(f)
            if f.cls == "L" and f.rule in ("SV-204", "SV-213", "RS-101"):
                degraded.add(f.rule)
            if f.repair:
                result.repairs.append(f.repair)
        rows[sid] = {"states": len(a.states), "widgets": len(s.get("widgetIds", ())), "checkpoints": per_screen.get(sid, 0), "enc": a.enc,
                     "svg": sum(len(t.encode("utf-8")) for t in a.svgs.values()), "cards": [p.page for p in a.pages]}
    for path, text, anchors, page in idx.build_index(ui_model, rows, project=project, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8):
        result.files[path] = text.encode("utf-8")
        result.records.append({"path": path, "kind": "index", "cls": "card", "ai": True, "page": page, "twin": "site/" + path[:-3] + ".html" if cfg.html else None, "twinBy": "reading"})
        for rid, kind, line, hdr in anchors:
            result.ids.append((rid, kind, "home", path, line, hdr, _name(index.screens[rid].get("title", "")) if rid in index.screens else rid))
    nav_pages = navmod.build_nav(ui_model, project=project, lang=lang, ui_sha8=ui_sha8, model_sha8=model_sha8)
    for path, text, anchors, page in nav_pages:
        result.files[path] = text.encode("utf-8")
        result.records.append({"path": path, "kind": "nav", "cls": "card", "ai": True, "page": page, "twin": "site/" + path[:-3] + ".html" if cfg.html else None, "twinBy": "reading"})
        for rid, kind, line, hdr in anchors:
            result.ids.append((rid, kind, "home", path, line, hdr, rid[:40]))
    for kind, name in (("index", "screens/INDEX.md"), ("nav", "screens/NAV.md")):
        parts = sum(1 for r in result.records if r["kind"] == kind)
        if parts > 1:
            degraded.add("RS-101")
            result.findings.append(Finding("RS-101", "L", "INFO", name, f"the {kind} page is cut into {parts} parts ({len(screens)} screens)"))
    result.degraded = sorted(degraded)
    states = sum(len(a.states) for a in result.assets.values())
    widgets = sum(len(s.get("widgetIds", ())) for s in screens)
    result.info = {"schema": um.BUILD_SCHEMA, "uiSha8": ui_sha8, "modelSha8": model_sha8, "layoutVersion": um.LAYOUT_VERSION, "states": states, "screens": len(screens),
                   "widgets": widgets, "checkpoints": sum(per_screen.values()), "status": "ok", "degraded": result.degraded}
    if check:
        from . import check as chk
        chk.check_result(result, ui_model, five_index, cfg)
    result.info["status"] = result.status
    result.files["screens/build-info.json"] = idx.build_info(ui_sha8=ui_sha8, model_sha8=model_sha8, states=states, screens=len(screens), widgets=widgets,
                                                              checkpoints=sum(per_screen.values()), status=result.status, degraded=result.degraded)
    result.records.append({"path": "screens/build-info.json", "kind": "build-info", "cls": "data", "ai": False, "manifest": False, "twin": None})
    return result
