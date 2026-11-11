"""Browser measurement of the screen SVGs (design A3 U2, SV-205, SV-209): a harness page inlines every SVG, a script measures them, headless
Edge dumps the DOM, and ``analyse`` turns the measurements into numbers. Test helper only (the product never starts a browser here).

What is measured, per ``<text>`` that is not a mark: its ``getBBox`` against the declared box of the group it sits in (``data-box``), the width of
each line against the width model of ``textfit``; per mark (the circle with the widget number): whether its circle covers the box of another
widget that is not an ancestor; per widget with a jump: whether the topmost element at the centre of its box belongs to it (a click target);
per SVG file: whether the browser can load it as an image (well-formed XML) and with which size. Console errors come from the launcher log.

Edge is started only through the hardened launcher of ``gate/smoke.py`` (process tree kill, redirected temp directory, output caps); a caller
must hold the edge lock of the machine (several agents share one) for the duration of ``run``.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from gemarc_mcp.atlas.gate import smoke
from gemarc_mcp.atlas.protoui import textfit as tf

EM = {"t": 14, "h": 20, "s2": 12, "mk": 12}

SCRIPT = r"""
window.addEventListener('load', function () {
  const out = {texts: [], marks: [], targets: [], images: [], errors: [], svgs: 0};
  try {
    const rect = (e) => { const b = e.getBBox(); return [b.x, b.y, b.width, b.height]; };
    const inter = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
    document.querySelectorAll('svg[data-screen]').forEach((svg) => {
      out.svgs += 1;
      const name = svg.getAttribute('data-screen') + '#' + svg.getAttribute('data-variant');
      svg.querySelectorAll('text').forEach((t) => {
        if (t.hasAttribute('data-mark')) return;
        const g = t.closest('g[data-box]');
        const spans = t.querySelectorAll('tspan');
        const lines = spans.length ? Array.from(spans, (s) => s.textContent) : [t.textContent];
        const widths = spans.length ? Array.from(spans, (s) => s.getComputedTextLength()) : [t.getComputedTextLength()];
        out.texts.push({s: name, g: g.id, cls: t.getAttribute('class'), lines: lines, widths: widths, bb: rect(t), box: g.getAttribute('data-box').split(' ').map(Number)});
      });
      const leaves = Array.from(svg.querySelectorAll('g[data-n]')).filter((g) => !['form','card','stack','row','grid','header','footer','sidebar','toolbar','tabs','list','divider'].includes(g.getAttribute('data-kind')));
      const boxes = Array.from(svg.querySelectorAll('g[data-n]')).map((g) => ({id: g.id, parent: g.getAttribute('data-parent'), box: g.getAttribute('data-box').split(' ').map(Number), kind: g.getAttribute('data-kind')}));
      const ancestors = (id) => { const set = new Set(); let cur = boxes.find((b) => b.id === id); while (cur) { set.add(cur.id); cur = boxes.find((b) => b.id === cur.parent); } return set; };
      svg.querySelectorAll('circle').forEach((c) => {
        const g = c.closest('g[data-n]');
        const own = ancestors(g.id);
        const cx = +c.getAttribute('cx'), cy = +c.getAttribute('cy'), r = +c.getAttribute('r');
        const ring = [cx - r, cy - r, 2 * r, 2 * r];
        const hits = boxes.filter((b) => !own.has(b.id) && b.kind !== 'divider' && inter(ring, b.box)).map((b) => b.id);
        const texts = [];
        svg.querySelectorAll('g[data-n]').forEach((o) => { if (o === g) return; o.querySelectorAll('text').forEach((t) => { if (t.hasAttribute('data-mark')) return; if (inter(ring, rect(t))) texts.push(o.id); }); });
        out.marks.push({s: name, g: g.id, ring: ring, boxes: hits, texts: texts});
      });
      svg.querySelectorAll('g[data-goto]').forEach((g) => {
        if (g.id.endsWith('.root')) return;
        g.scrollIntoView({block: 'center', inline: 'center'});
        const r = g.querySelector('rect').getBoundingClientRect();
        const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const hit = e ? (e.closest('g[data-n]') || {}).id || '' : '(none)';
        out.targets.push({s: name, g: g.id, hit: hit, w: r.width, h: r.height});
      });
    });
    document.querySelectorAll('img[data-file]').forEach((img) => {
      out.images.push({file: img.getAttribute('data-file'), ok: img.complete && img.naturalWidth > 0, w: img.naturalWidth, h: img.naturalHeight});
    });
  } catch (e) { out.errors.push(String(e)); }
  document.getElementById('probe-result').textContent = JSON.stringify(out);
});
"""


def harness(svgs: dict, image_files: dict, pages: "dict | None" = None) -> str:
    """The page: every SVG inline (in the given order), the body of every gallery page, one ``<img>`` per file name, the script, an empty result element."""
    parts = ["<!doctype html>", '<html><head><meta charset="utf-8"><title>probe</title>', "<style>body{margin:0}.s{margin:0 0 8px 0}</style></head><body>"]
    for name, text in svgs.items():
        parts.append(f'<div class="s" data-name="{name}">')
        parts.append(re.sub(r"^<\?xml[^>]*>\s*", "", text))
        parts.append("</div>")
    for name, html in (pages or {}).items():
        body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
        style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
        parts.append(f'<div class="s" data-page="{name}"><style>{style}</style>{body}</div>')
    for name, relative in image_files.items():
        parts.append(f'<img data-file="{name}" src="{relative}" alt="">')
    parts.append('<pre id="probe-result"></pre>')
    parts.append("<script>" + SCRIPT + "</script></body></html>")
    return "\n".join(parts)


def run(svgs: dict, pages: "dict | None" = None, *, window=(1400, 900), timeout: float = 120.0) -> dict:
    """Measure ``svgs`` (``{name: svg text}``) and the gallery ``pages`` (``{name: html text}``) in headless Edge: the SVGs are also written as files
    and loaded as images. Returns ``{"status": "OK"|"SKIP"|"FAIL", "result": ..., "console_errors": [...], "seconds": ...}``."""
    browser = smoke.find_browser()
    if browser is None:
        return {"status": "SKIP", "reason": smoke.no_browser_message()}
    with tempfile.TemporaryDirectory(prefix="screens-edge-") as work:
        work = Path(work)
        shown = work / "svg"
        shown.mkdir()
        files = {}
        for name, text in svgs.items():
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".svg"
            (shown / safe).write_bytes(text.encode("utf-8"))
            files[name] = "svg/" + safe
        page = work / "probe.html"
        page.write_bytes(harness(svgs, files, pages).encode("utf-8"))
        profile = work / "profile"
        args = smoke.build_args(browser.path, str(profile), window, smoke.page_url(page, ""), 5000)
        launch = smoke._launch(args, timeout, env=smoke._browser_env(work / "tmp"))
        reason = smoke.process_failure_reason(launch)
        smoke._rmtree_retry(profile)
        if reason:
            return {"status": "FAIL", "reason": reason, "seconds": launch.seconds}
        dom = launch.stdout.decode("utf-8", "replace")
        m = re.search(r'<pre id="probe-result">(.*?)</pre>', dom, re.S)
        if not m:
            return {"status": "FAIL", "reason": "no probe result in the DOM dump", "seconds": launch.seconds}
        text = m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")
        console = smoke.parse_console_lines(launch.stderr)
        errors = smoke.console_errors_from_origin(console, smoke.page_url(work, ""), smoke.load_calibration())
        return {"status": "OK", "result": json.loads(text), "console": console, "console_errors": errors, "seconds": launch.seconds,
                "browser": f"{browser.family} {browser.version}"}


def analyse(result: dict, tolerance: float = 1.0) -> dict:
    """Numbers from a probe result: texts, overflows (a text box that leaves its group box by more than ``tolerance`` px), the ratio of the
    measured to the modelled width (max, min), marks that cover another widget's box or text, click targets that are not hit, images not loaded."""
    overflow, ratios = [], []
    for t in result["texts"]:
        bx, by, bw, bh = t["box"]
        x, y, w, h = t["bb"]
        em = EM.get(t["cls"].split()[0], 14)
        if x < bx - tolerance or y < by - tolerance or x + w > bx + bw + tolerance or y + h > by + bh + tolerance:
            overflow.append({"s": t["s"], "g": t["g"], "lines": t["lines"], "bb": t["bb"], "box": t["box"]})
        for line, width in zip(t["lines"], t["widths"]):
            model = tf.width(line, em)
            if model:
                ratios.append(width / model)
    return {
        "texts": len(result["texts"]), "overflow": overflow, "ratio_max": max(ratios, default=0), "ratio_min": min(ratios, default=0),
        "marks": len(result["marks"]), "marks_over_box": [m for m in result["marks"] if m["boxes"]], "marks_over_text": [m for m in result["marks"] if m["texts"]],
        "targets": len(result["targets"]), "targets_missed": [t for t in result["targets"] if t["hit"] != t["g"]],
        "images": len(result["images"]), "images_failed": [i for i in result["images"] if not i["ok"]], "errors": result["errors"], "svgs": result["svgs"],
    }
