"""Synthetic screens for the screen builder tests (``protoui/layout.py``, ``svg.py``, ``card.py``, ``gallery.py``, ``build.py``): stress shapes of
design A3 T-R12, a showcase screen set that uses every layout feature, and hostile strings. Test helper, standard library only; every id and
text is invented.

``model_of(text)``              the expanded ``gemarc-atlas-ui/1`` model of ``.atl`` text (0 diagnostics asserted by the caller)
``stress_text(...)``            one screen with a given number of controls, states, tables with sample rows
``many_screens_text(n)``        ``n`` small screens, no group, a chain of navigation rows
``showcase_text()``             the fixed showcase of the goldens (``ui_fixtures/screens/showcase.atl`` holds the same text)
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from gemarc_mcp.atlas.protoui import atl, build as bld, expand as ex

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "ui_fixtures"
STATE_KINDS = ("loading", "empty", "error", "success", "other", "locked")


def model_of(text: str, name: str = "ui-10-synthetic.atl", five=None) -> dict:
    """The model of ``.atl`` text; the diagnostics are on the expansion (``expand_text``), here only the model is returned."""
    return expand_text(text, name, five).model


def expand_text(text: str, name: str = "ui-10-synthetic.atl", five=None):
    return ex.expand_ui([atl.parse_atl(text, name)], five)


def _data(cols: int, rows: int, tag: str) -> str:
    head = ",".join(f'"{tag}c{c}"' for c in range(cols))
    body = ",".join("[" + ",".join(f'"{tag}r{r}c{c}"' for c in range(cols)) + "]" for r in range(rows))
    return '{"cols":[' + head + '],"rows":[' + body + "]}"


def stress_text(controls: int, states: int, tables: int = 0, rows: int = 5, cols: int = 6, screen: str = "SCR-BIG", frame: str = "desktop") -> str:
    """One screen with ``controls`` widgets in all (a header, a form and its inputs, ``tables`` tables with sample data, buttons) and ``states``
    variants beside ``default``; every variant hides one input so that the states differ."""
    lines = ["#atlas-ui 1", "## screens: id | title | purpose | opts",
             f"{screen} | Big screen | A synthetic screen used to stress the renderer. | kind=page frame={frame} route=/big start=true",
             "## variants: screen | name | note | opts"]
    for i in range(states):
        lines.append(f"{screen} | s{i + 1} | State number {i + 1} of the big screen. | kind={STATE_KINDS[i % len(STATE_KINDS)]}")
    lines += ["## widgets: local | label | opts", f"@set screen={screen}", "bar | Big header | kind=header", "form | Big form | kind=form"]
    count = 2
    for t in range(tables):
        lines.append(f"t{t + 1} | Table {t + 1} | kind=table w=fill data={_data(cols, rows, f't{t + 1}')}")
        count += 1
    i = 0
    while count < controls:
        i += 1
        count += 1
        hide = f" in=default,{','.join('s' + str(k + 1) for k in range(1, states))}" if states > 1 and i == 3 else ""
        kind = ("input", "select", "checkbox", "button", "text")[i % 5]
        lines.append(f"w{i:02d} | Field number {i:02d} | kind={kind} parent=form bind=f{i:02d}{hide}")
    lines += ["## nav: from | to | label | opts", f"{screen}.w{max(i, 1):02d} | {screen} | Back to itself | on=tap"] if i else []
    return "\n".join(lines) + "\n"


def many_screens_text(n: int, per_row: int = 1) -> str:
    """``n`` small screens (``SCR-N001``...) without any group, each with a header, a heading, an input and a button that leads to the next one."""
    lines = ["#atlas-ui 1", "## screens: id | title | purpose | opts"]
    for k in range(1, n + 1):
        lines.append(f"SCR-N{k:03d} | Screen number {k} | The {k}th synthetic screen. | kind=page frame=desktop route=/n{k}" + (" start=true" if k == 1 else ""))
    lines += ["## variants: screen | name | note | opts", "## widgets: local | label | opts"]
    for k in range(1, n + 1):
        lines += [f"@set screen=SCR-N{k:03d}", f"title | Screen number {k} | kind=heading", f"name | Name {k} | kind=input bind=name{k}", f"go | Next | kind=button emph=primary"]
    lines.append("## nav: from | to | label | opts")
    for k in range(1, n):
        lines.append(f"SCR-N{k:03d}.go | SCR-N{k + 1:03d} | Go to number {k + 1} | on=tap")
    return "\n".join(lines) + "\n"


def grouped_screens_text(n: int, groups: int) -> str:
    """``many_screens_text`` with ``group=g1``... (round robin), for the shards by group."""
    text = many_screens_text(n)
    out = []
    count = 0
    for line in text.split("\n"):
        if line.startswith("SCR-N") and " | kind=page" in line:
            count += 1
            line += f" group=g{(count - 1) % groups + 1}"
        out.append(line)
    return "\n".join(out)


HOSTILE = (
    'He said "hi" & <left>',                       # XML-significant characters
    "pipe | and `tick` and **bold** and [a](b)",    # Markdown-significant characters
    "braces {lines} and {sha8}",                    # the two placeholders of the page frame
    "x" * 450,                                      # longer than the 400-character limit of an author string
)


def hostile_text() -> str:
    """One screen whose labels, purpose, note and variant text carry the hostile strings (quoted cells)."""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["#atlas-ui 1", "## screens: id | title | purpose | opts",
             f"SCR-HOSTILE | {q(HOSTILE[0])} | {q(HOSTILE[1])} | kind=page frame=phone route=/hostile start=true",
             "## variants: screen | name | note | opts",
             f"SCR-HOSTILE | empty | {q(HOSTILE[2])} | kind=empty text={q(HOSTILE[1])}",
             f"SCR-HOSTILE | error | {q(HOSTILE[0])} | kind=error text={q(HOSTILE[2])}",
             "## widgets: local | label | opts", "@set screen=SCR-HOSTILE",
             f"head | {q(HOSTILE[0])} | kind=heading", f"note | {q(HOSTILE[2])} | kind=text", f"long | {q(HOSTILE[3])} | kind=text req={','.join('RQ-' + str(k) for k in range(1, 61))}",
             f"btn | {q(HOSTILE[1])} | kind=button emph=primary", f"field | {q(HOSTILE[3])} | kind=input bind=field",
             f"grid | {q(HOSTILE[0])} | kind=table w=fill data={{\"cols\":[{q(HOSTILE[1])},\"b\"],\"rows\":[[{q(HOSTILE[2])},{q(HOSTILE[3])}]]}}",
             "## nav: from | to | label | opts", f"SCR-HOSTILE.btn | SCR-HOSTILE#error | {q(HOSTILE[1])} | on=tap"]
    return "\n".join(lines) + "\n"


def deep_text(depth: int) -> str:
    """A chain of ``depth`` nested stacks with one text in the innermost."""
    lines = ["#atlas-ui 1", "## screens: id | title | purpose | opts", "SCR-DEEP | Deep | Nested containers. | kind=page frame=desktop route=/deep start=true",
             "## variants: screen | name | note | opts", "## widgets: local | label | opts", "@set screen=SCR-DEEP"]
    for d in range(depth):
        lines.append(f"s{d} | Level {d} | kind=stack" + (f" parent=s{d - 1}" if d else ""))
    lines.append(f"leaf | Innermost | kind=text parent=s{depth - 1}")
    return "\n".join(lines) + "\n"


def showcase_text() -> str:
    return (FIXTURES / "screens" / "showcase.atl").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
# Shared builds (one per process: the layout of the 12-screen sample takes tens of milliseconds, the checks as much again)
# ---------------------------------------------------------------------------------------------------------------

CFG = bld.ScreensConfig(lang="zh-CN", project="demo")
_CACHE: dict = {}


def login_model() -> dict:
    from . import ui_synth
    return _cached("login-model", lambda: model_of(ui_synth.login_text(), "ui-10-login.atl"))


def sample12_model() -> dict:
    from . import ui_synth
    return _cached("sample12-model", lambda: ex.expand_ui([atl.parse_atl(t, n) for n, t in sorted(ui_synth.sample12_sources().items())]).model)


def showcase_model() -> dict:
    return _cached("showcase-model", lambda: model_of(showcase_text(), "ui-10-showcase.atl"))


def _cached(key, make):
    if key not in _CACHE:
        _CACHE[key] = make()
    return _CACHE[key]


def built(name: str) -> "bld.ScreensResult":
    """The build of one of the three fixed models (``login``, ``sample12``, ``showcase``) with ``CFG`` and no 5D index."""
    models = {"login": login_model, "sample12": sample12_model, "showcase": showcase_model}
    return _cached("build-" + name, lambda: bld.build_screens(models[name](), None, CFG))


def refinalize(text: str) -> str:
    """A Markdown page of the screens tree with a valid sentinel again after a test edited its body: the line count in the first line and in the
    sentinel, then the ``sha8`` of everything before the last line (zh-CN pages)."""
    lines = text.rstrip("\n").split("\n")
    count = len(lines)
    lines[0] = re.sub(r"共 \d+ 行", f"共 {count} 行", lines[0])
    lines[-1] = re.sub(r"共 \d+ 行", f"共 {count} 行", lines[-1])
    prefix = "\n".join(lines[:-1]) + "\n"
    lines[-1] = re.sub(r"sha8 [0-9a-f]{8}", "sha8 " + hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:8], lines[-1])
    return prefix + lines[-1] + "\n"


def mutate(files: dict, path: str, old: str, new: str, *, count: int = 1, fix_sentinel: bool = False) -> dict:
    """A copy of ``files`` with ``old`` replaced by ``new`` in ``path`` (``old`` must be there; ``count`` 0 replaces every occurrence)."""
    out = dict(files)
    text = out[path].decode("utf-8")
    assert old in text, (path, old)
    text = text.replace(old, new) if count == 0 else text.replace(old, new, count)
    out[path] = (refinalize(text) if fix_sentinel else text).encode("utf-8")
    return out
