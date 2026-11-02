"""Seeded synthetic generator of interface slices (``gemarc-atlas-ui/1``): test harness, standard library only.

Everything is invented: ids use SYNTHETIC families (``SCR-LOGIN`` style or a random family per seed) and the text comes from small generic
pools (English or Chinese). Nothing of any real system is used or imitated. The generator builds a *spec* (the authored facts: the
option names of the four sections) that always checks clean (0 diagnostics, full mode), and ``render`` writes it as ``.atl`` slices;
``encode_model`` turns an expanded model back into a spec, so ``model -> text -> model`` is a fixed point (the round trip of U1).

Public API
----------
``generate(screens=6, seed=0, lang="en", family="random", five=None)``   a spec ``{"screens", "variants", "widgets", "nav"}``
``render(spec, per_slice=4)``                                             ``{slice name: .atl text}``
``encode_model(model)``                                                   a spec from an expanded ``gemarc-atlas-ui/1`` model
``sample12_sources()``                                                    the pinned 12-screen sample of ``ui_fixtures/sample12``
``LOGIN_FILE`` / ``login_text()``                                         the two-screen worked example (design 4.2)

Determinism: the SplitMix64 stream of ``synth.Rng`` (never ``random``); nothing depends on ``hash()`` or on set ordering.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from gemarc_mcp.atlas.protoui import rows as ui_rows

from .synth import Rng

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "ui_fixtures"
LOGIN_FILE = FIXTURES / "ui-10-login.atl"
SAMPLE12_DIR = FIXTURES / "sample12"

EN_NOUNS = ("ledger", "member", "copy", "desk", "shelf", "notice", "fee", "return", "loan", "report", "profile", "order", "stock",
            "pallet", "lane", "dock", "route", "invoice", "booking", "garden", "plot", "rota", "ticket", "voucher", "supplier")
EN_FIELDS = ("name", "email", "phone", "address", "status", "quantity", "note", "code", "date", "amount")
EN_ACTIONS = ("Save", "Submit", "Open", "Next", "Back", "Confirm", "Cancel", "Search", "Export", "Review")
ZH_NOUNS = ("名称", "邮箱", "电话", "地址", "状态", "备注", "数量", "日期", "编号", "类别", "说明", "摘要", "总计", "明细")
ZH_ACTIONS = ("保存", "提交", "打开", "下一步", "返回", "确认", "取消", "搜索", "导出", "查看")
BIND_NAMES = ("email", "phone", "address.city", "note", "quantity", "code", "due", "amount", "name", "kind")
FRAMES = ("desktop", "desktop", "desktop", "tablet", "phone")
INPUT_KINDS = ("input", "input", "select", "checkbox", "textarea", "radio", "switch")


class _Words:
    def __init__(self, rng: Rng, lang: str):
        self.rng, self.lang = rng, lang

    def noun(self) -> str:
        return self.rng.choice(ZH_NOUNS if self.lang == "zh" else EN_NOUNS)

    def action(self) -> str:
        return self.rng.choice(ZH_ACTIONS if self.lang == "zh" else EN_ACTIONS)

    def title(self) -> str:
        if self.lang == "zh":
            return self.noun() + self.noun()
        text = f"{self.noun()} {self.noun()}"
        return text[:1].upper() + text[1:]

    def sentence(self) -> str:
        if self.lang == "zh":
            return f"{self.noun()}和{self.noun()}的{self.noun()}。"
        return f"Shows the {self.noun()} and the {self.noun()} of one {self.noun()}."

    def label(self) -> str:
        return self.noun() if self.lang == "zh" else self.noun().capitalize()


def _prefix(rng: Rng, family: str) -> str:
    if family == "fixed":
        return "SCR-"
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(rng.choice(letters) for _ in range(3)) + "-"


def generate(screens: int = 6, seed: int = 0, lang: str = "en", family: str = "random", five: "Mapping | None" = None) -> dict:
    """A spec of ``screens`` screens that checks clean in full mode (every screen reachable, every button leads somewhere, every form has
    a button and an error variant, every data widget a loading and an empty variant). ``five`` (an expanded 5D model) lets a few
    links (actor, req, api) point at real 5D ids."""
    rng = Rng(seed, f"ui/{family}/{lang}")
    words = _Words(rng, lang)
    prefix = _prefix(rng, family)
    spec: dict = {"screens": [], "variants": [], "widgets": [], "nav": []}
    ids = [f"{prefix}{rng.choice(EN_NOUNS).upper()}{i}" for i in range(screens)]
    actors = [n["id"] for n in (five or {}).get("nodes", ()) if n.get("kind") == "Actor"]
    reqs = [r["id"] for r in (five or {}).get("requirements", ())]
    apis = [n["id"] for n in (five or {}).get("nodes", ())]
    for index, sid in enumerate(ids):
        screen = {"id": sid, "title": words.title(), "purpose": words.sentence(), "route": f"/{sid.lower().replace(prefix.lower(), '')}", "group": f"g{index // 4}"}
        if rng.chance(0.3):
            screen["frame"] = rng.choice(FRAMES)
        if index == 0:
            screen["start"] = True
        if actors and rng.chance(0.3):
            screen["actor"] = rng.choice(actors)
        if reqs and rng.chance(0.4):
            screen["req"] = [rng.choice(reqs)]
        spec["screens"].append(screen)
        _screen_body(spec, sid, ids[(index + 1) % screens], ids, rng, words, apis)
    return spec


def _add_widget(spec: dict, sid: str, local: str, label: str, kind: str, **extra) -> str:
    row = {"screen": sid, "local": local, "label": label, "kind": kind}
    row.update({k: v for k, v in extra.items() if v is not None})
    spec["widgets"].append(row)
    return local


def _screen_body(spec: dict, sid: str, nxt: str, ids: Sequence[str], rng: Rng, words: _Words, apis: Sequence[str]) -> None:
    """The widgets, variants and nav rows of one screen."""
    taken: set = set()

    def local(base: str) -> str:
        n = 1
        while f"{base}{n}" in taken:
            n += 1
        taken.add(f"{base}{n}")
        return f"{base}{n}"

    def leads(widget: str, target: str, label: str, on: str = "tap") -> None:
        row = {"from": f"{sid}.{widget}", "to": target, "label": label}
        if on != "tap":
            row["on"] = on
        spec["nav"].append(row)

    bar = local("bar")
    _add_widget(spec, sid, bar, words.label(), "header")
    home = local("go")
    _add_widget(spec, sid, home, words.action(), "link", parent=bar)
    leads(home, nxt, words.action())
    archetype = rng.choice(("form", "table", "cards", "tabs", "board"))
    if archetype == "form":
        form = local("form")
        _add_widget(spec, sid, form, words.label(), "form")
        for _ in range(rng.randint(2, 4)):
            kind = rng.choice(INPUT_KINDS)
            _add_widget(spec, sid, local("field"), words.label(), kind, parent=form, bind=rng.choice(BIND_NAMES) if kind != "radio" else None)
        err = local("err")
        _add_widget(spec, sid, err, words.sentence(), "text", parent=form, **{"in": ["error"]}, emph="danger")
        send = local("send")
        _add_widget(spec, sid, send, words.action(), "button", parent=form, emph="primary", api=rng.choice(apis) if apis and rng.chance(0.4) else None)
        leads(send, f"{sid}#success" if rng.chance(0.3) else nxt, words.action(), "submit")
        spec["variants"].append({"screen": sid, "name": "error", "note": words.sentence(), "kind": "error", "text": words.sentence()})
        if spec["nav"][-1]["to"].endswith("#success"):
            spec["variants"].append({"screen": sid, "name": "success", "note": words.sentence(), "kind": "success"})
    elif archetype == "table":
        _add_widget(spec, sid, local("title"), words.title(), "heading")
        data = {"cols": [words.label() for _ in range(3)], "rows": [[words.label() for _ in range(3)] for _ in range(rng.randint(1, 3))]}
        _add_widget(spec, sid, local("grid"), words.label(), "table", w="fill", data=data if rng.chance(0.6) else None)
        _add_widget(spec, sid, local("pager"), words.label(), "pagination")
        more = local("more")
        _add_widget(spec, sid, more, words.action(), "button", emph="secondary")
        leads(more, ids[rng.below(len(ids))], words.action())
        spec["variants"].append({"screen": sid, "name": "loading", "note": words.sentence(), "kind": "loading"})
        spec["variants"].append({"screen": sid, "name": "empty", "note": words.sentence(), "kind": "empty", "text": words.sentence()})
    elif archetype == "cards":
        grid = local("grid")
        _add_widget(spec, sid, grid, words.label(), "grid", cols=2)
        for _ in range(rng.randint(2, 4)):
            card = local("card")
            _add_widget(spec, sid, card, words.label(), "card", parent=grid)
            _add_widget(spec, sid, local("head"), words.title(), "heading", parent=card)
            _add_widget(spec, sid, local("body"), words.sentence(), "text", parent=card)
            open_ = local("open")
            _add_widget(spec, sid, open_, words.action(), "button", parent=card)
            leads(open_, ids[rng.below(len(ids))], words.action())
    elif archetype == "tabs":
        tabs = local("tabs")
        _add_widget(spec, sid, tabs, words.label(), "tabs")
        for _ in range(rng.randint(2, 3)):
            _add_widget(spec, sid, local("tab"), words.label(), "tab", parent=tabs)
        _add_widget(spec, sid, local("note"), words.sentence(), "text")
    else:
        row = local("row")
        _add_widget(spec, sid, row, words.label(), "row", align="center" if rng.chance(0.5) else None)
        for _ in range(rng.randint(2, 4)):
            _add_widget(spec, sid, local("badge"), words.label(), "badge", parent=row)
        _add_widget(spec, sid, local("chart"), words.title(), "chart", h="200")
        spec["variants"].append({"screen": sid, "name": "loading", "note": words.sentence(), "kind": "loading"})
    if rng.chance(0.25):
        _add_widget(spec, sid, local("rule"), words.label(), "divider")


# ---------------------------------------------------------------------------------------------------------------
# Writing a spec as .atl
# ---------------------------------------------------------------------------------------------------------------

_BARE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/+^@%$#~*=<>!?&()-]+$")
_CTRL_RE = re.compile("[\x00-\x1f\x7f-\x9f﻿​-‏‪-‮⁠-⁤  ]")

SCREEN_OPTS = ("kind", "frame", "route", "start", "actor", "req", "group", "align")
VARIANT_OPTS = ("kind", "machine", "state", "text")
WIDGET_OPTS = ("kind", "parent", "w", "h", "cols", "in", "bind", "emph", "data", "api", "req", "align")
NAV_OPTS = ("on", "guard", "api", "case", "rule", "req", "alias")


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
        if items and all(_BARE_VALUE_RE.match(x) and "," not in x and x[0] not in '{["' for x in items):
            return ",".join(items)
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = str(v)
    if text and _BARE_VALUE_RE.match(text) and text[0] not in '{["' and text != "^":
        return text
    return json.dumps(text, ensure_ascii=False)


def _cell(text: str) -> str:
    quote = (text == "" or text != text.strip(" \t") or '"' in text or "\\" in text or text == "~" or text.startswith(("//", "@", "#", "##"))
             or _CTRL_RE.search(text) is not None or ui_rows.looks_like_option(text) is not None)
    return json.dumps(text, ensure_ascii=False) if quote else text.replace("|", "\\|")


def _row(cells: Sequence[str], options: Sequence[tuple]) -> str:
    opts = " ".join(f"{k}={_value(v)}" for k, v in options)
    return " | ".join([_cell(c) for c in cells] + ([opts] if opts else []))


def _options(row: Mapping, names: Sequence[str], skip: Sequence[str] = ()) -> list:
    return [(n, row[n]) for n in names if n in row and n not in skip]


def render(spec: Mapping, per_slice: int = 4, comment: str = "synthetic interface example, not a real system") -> dict:
    """``{slice name: text}``: the screens in chunks of ``per_slice``, each chunk with its variants, widgets and the nav rows that start in it."""
    out: dict = {}
    screens = list(spec["screens"])
    for k in range(0, len(screens), per_slice):
        chunk = screens[k:k + per_slice]
        ids = {s["id"] for s in chunk}
        lines = ["#atlas-ui 1", f"// {comment}", "## screens: id | title | purpose | opts"]
        for s in chunk:
            lines.append(_row((s["id"], s["title"], s["purpose"]), _options(s, SCREEN_OPTS)))
        variants = [v for v in spec["variants"] if v["screen"] in ids]
        if variants:
            lines.append("## variants: screen | name | note | opts")
            lines.extend(_row((v["screen"], v["name"], v["note"]), _options(v, VARIANT_OPTS)) for v in variants)
        lines.append("## widgets: local | label | opts")
        for s in chunk:
            lines.append(f"@set screen={s['id']}")
            for w in spec["widgets"]:
                if w["screen"] == s["id"]:
                    lines.append(_row((w["local"], w["label"]), _options(w, WIDGET_OPTS)))
        nav = [n for n in spec["nav"] if n["from"].split(".", 1)[0] in ids]
        if nav:
            lines.append("## nav: from | to | label | opts")
            lines.extend(_row((n["from"], n["to"], n["label"]), _options(n, NAV_OPTS)) for n in nav)
        out[f"ui-{k // per_slice + 1:03d}-part"] = "\n".join(lines) + "\n"
    return out


def encode_model(model: Mapping) -> dict:
    """The spec an expanded model was made from (derived fields dropped), so that ``render(encode_model(m))`` expands to ``m`` again."""
    spec: dict = {"screens": [], "variants": [], "widgets": [], "nav": []}
    for s in model["screens"]:
        row = {"id": s["id"], "title": s["title"], "purpose": s["purpose"], "kind": s["kind"], "frame": s["frame"]}
        if s["start"]:
            row["start"] = True
        for src, dst in (("route", "route"), ("actorId", "actor"), ("group", "group"), ("align", "align")):
            if src in s:
                row[dst] = s[src]
        if s["reqIds"]:
            row["req"] = list(s["reqIds"])
        spec["screens"].append(row)
    for v in model["variants"]:
        row = {"screen": v["screenId"], "name": v["name"], "note": v["note"], "kind": v["kind"]}
        for src, dst in (("machineId", "machine"), ("state", "state"), ("text", "text")):
            if src in v:
                row[dst] = v[src]
        spec["variants"].append(row)
    for w in model["widgets"]:
        row = {"screen": w["screenId"], "local": w["local"], "label": w["label"], "kind": w["kind"]}
        if w["parentId"]:
            row["parent"] = w["parentId"].split(".", 1)[1]
        for key in ("w", "h", "cols", "bind", "emph", "data", "align"):
            if key in w:
                row[key] = w[key]
        if w["inVariants"] is not None:
            row["in"] = list(w["inVariants"])
        if "apiId" in w:
            row["api"] = w["apiId"]
        if w["reqIds"]:
            row["req"] = list(w["reqIds"])
        spec["widgets"].append(row)
    for n in model["nav"]:
        row = {"from": n["fromId"], "to": n["toId"], "label": n["label"], "on": n["on"]}
        for src, dst in (("guard", "guard"), ("apiId", "api"), ("caseId", "case"), ("ruleId", "rule")):
            if src in n:
                row[dst] = n[src]
        if n["reqIds"]:
            row["req"] = list(n["reqIds"])
        if n["id"] != f"{n['fromId']}:go:{n['toId']}" and not n["id"].startswith("h:"):
            row["alias"] = n["id"]
        spec["nav"].append(row)
    return spec


def login_text() -> str:
    return LOGIN_FILE.read_text(encoding="utf-8")


def sample12_sources() -> dict:
    """The pinned 12-screen sample (4 slices, 12 screens, 23 variants, 79 widgets, 30 nav rows) as ``{file name: text}``."""
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(SAMPLE12_DIR.glob("*.atl"))}
