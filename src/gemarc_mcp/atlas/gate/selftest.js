/* GemArc Atlas browser gate: in-page self-test.
 *
 * Plain browser JS (no modules, no dependencies). Real pages embed this file as an inline
 * <script> at the very top of <head>, so error capture and the draw-call counters are armed before
 * any page code runs.
 *
 * Contract (see gate/README.md):
 *   - Active only when the URL query contains gate=1; otherwise window.AtlasGate.active is false
 *     and run() is a no-op, so a page may call it unconditionally.
 *   - The runner appends a per-launch nonce (gn=<token>); it is echoed in the result so a stale or
 *     hard-coded result element is rejected. This stops accidents, not a page that reads the URL.
 *   - The walker is driven by setTimeout, never requestAnimationFrame: under headless
 *     --virtual-time-budget rAF is starved, timers are not.
 *   - Every render is the page's own synchronous renderOnce(). Right after the second call of every
 *     tab this file reads each visible canvas ITSELF, in the same task (so nothing depends on
 *     preserveDrawingBuffer): pixels, WebGL getError(), the unmasked renderer, and the number of
 *     draw calls the page issued during that renderOnce. The page's own return value is carried
 *     along as information only; no verdict uses it.
 *   - This file only MEASURES. All verdicts (thresholds, FAIL/PASS) are computed by the Python
 *     runner (gate/smoke.py) from the raw numbers, so a page cannot grade itself.
 *   - The result is written to <pre id="atlas-gate" data-done="1">{json}</pre> and every <script>
 *     element is removed afterwards (keeps the dumped DOM small). Capture stays live afterwards:
 *     errors that happen after the walker finished are mirrored into the data-late-errors attribute
 *     of that element until the dump (the end of the runner's virtual-time budget).
 */
(function () {
  "use strict";

  var RESULT_VERSION = 2;
  var search = String(location.search || "");
  var ACTIVE = /[?&]gate=1(?:&|$)/.test(search);
  var NONCE = (/[?&]gn=([A-Za-z0-9]{8,64})(?:&|$)/.exec(search) || [])[1] || null;
  var MAX_ERRORS = 20;
  var MAX_LIST = 12;
  var SAMPLE_LIMIT = 250000; // pixels examined per canvas read
  var BG_TOLERANCE = 8; // per-channel distance from the dominant colour that still counts as background
  var BOUNDARY_POLLS = 60; // 60 x 500 ms of virtual time, past the runner's 20 s budget
  var errors = [];
  var errorTotal = 0;
  var finished = false;
  var finishedErrorCount = 0;
  var finishedErrorTotal = 0;
  var resultEl = null;

  function clip(text, n) {
    text = String(text);
    return text.length > n ? text.slice(0, n) + "..." : text;
  }

  function fmt(value) {
    try {
      if (value && value.stack) return String(value.stack).split("\n")[0];
      return typeof value === "string" ? value : JSON.stringify(value);
    } catch (e) {
      return String(value);
    }
  }

  function syncLateErrors() {
    if (!resultEl) return;
    try {
      resultEl.setAttribute(
        "data-late-errors",
        JSON.stringify({ count: errorTotal - finishedErrorTotal, errors: errors.slice(finishedErrorCount) })
      );
    } catch (e) {
      /* the element may have been detached by the page; nothing more to do */
    }
  }

  function note(kind, message) {
    errorTotal += 1;
    if (errors.length < MAX_ERRORS) errors.push(kind + ": " + clip(message, 300));
    if (finished) syncLateErrors();
  }

  // ---- pixels, used by the page-side helper and by the gate's own canvas probe -----------------
  var HIST_BINS = null;
  var HIST_FIRST = null;

  function dominantColourStats(px, step) {
    // Background = the most frequent colour (5 bits per channel bins), not a page-supplied value
    // and not the corner pixel. nonBg = sampled pixels further than BG_TOLERANCE from it.
    if (!HIST_BINS) {
      HIST_BINS = new Uint32Array(1 << 20); // 5 bits x 4 channels
      HIST_FIRST = new Int32Array(1 << 20);
    }
    var bins = HIST_BINS;
    var first = HIST_FIRST;
    bins.fill(0);
    first.fill(-1);
    var sampled = 0;
    var best = 0;
    var bestIndex = 0;
    var i;
    for (i = 0; i < px.length; i += 4 * step) {
      var key = ((px[i] >> 3) << 15) | ((px[i + 1] >> 3) << 10) | ((px[i + 2] >> 3) << 5) | (px[i + 3] >> 3);
      if (first[key] < 0) first[key] = i;
      bins[key] += 1;
      sampled += 1;
      if (bins[key] > best) {
        best = bins[key];
        bestIndex = first[key];
      }
    }
    var r = px[bestIndex];
    var g = px[bestIndex + 1];
    var b = px[bestIndex + 2];
    var a = px[bestIndex + 3];
    var count = 0;
    for (i = 0; i < px.length; i += 4 * step) {
      if (
        Math.abs(px[i] - r) > BG_TOLERANCE ||
        Math.abs(px[i + 1] - g) > BG_TOLERANCE ||
        Math.abs(px[i + 2] - b) > BG_TOLERANCE ||
        Math.abs(px[i + 3] - a) > BG_TOLERANCE
      ) {
        count++;
      }
    }
    return { sampled: sampled, nonBg: count, nonBgRatio: sampled ? count / sampled : 0, bg: [r, g, b, a] };
  }

  function pixelStats(gl, bg) {
    // Page-side convenience (kept for pages that want to log numbers). INFORMATION ONLY: the gate
    // never reads this value, it measures the canvas itself.
    var w = gl.drawingBufferWidth;
    var h = gl.drawingBufferHeight;
    var px = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    var ref = bg && bg.length >= 3 ? bg : [px[0], px[1], px[2]];
    var count = 0;
    for (var i = 0; i < px.length; i += 4) {
      if (Math.abs(px[i] - ref[0]) > 8 || Math.abs(px[i + 1] - ref[1]) > 8 || Math.abs(px[i + 2] - ref[2]) > 8) count++;
    }
    return { width: w, height: h, total: w * h, nonBg: count, nonBgRatio: w * h ? count / (w * h) : 0 };
  }

  function glRenderer(gl) {
    try {
      var ext = gl.getExtension("WEBGL_debug_renderer_info");
      return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    } catch (e) {
      return "";
    }
  }

  if (!ACTIVE) {
    window.AtlasGate = { active: false, run: function () {}, pixelStats: pixelStats, glRenderer: glRenderer };
    return;
  }

  // ---- error capture, armed immediately -------------------------------------------------------
  try {
    window.addEventListener("error", function (event) {
      var target = event.target;
      if (target && target !== window && target.nodeType === 1) {
        note("resource", (target.tagName || "?") + " " + (target.src || target.href || ""));
      } else {
        note("onerror", (event.message || "error") + " @" + clip(event.filename || "", 80) + ":" + (event.lineno || 0));
      }
    }, true);
    window.addEventListener("unhandledrejection", function (event) {
      note("unhandledrejection", fmt(event.reason));
    });
    var originalError = console.error;
    console.error = function () {
      note("console.error", Array.prototype.map.call(arguments, fmt).join(" "));
      return originalError.apply(console, arguments);
    };
  } catch (e) {
    note("capture", e);
  }

  // ---- draw-call counters and canvas registry, armed immediately -------------------------------
  var draws = { gl: 0, c2d: 0 };
  var contexts = []; // { canvas, kind, ctx } for every canvas that obtained a context
  var originalGetContext = HTMLCanvasElement.prototype.getContext;

  var wrappedProtos = []; // every prototype already wrapped, so a method is never counted twice

  function wrapProto(proto, names, key) {
    if (!proto || wrappedProtos.indexOf(proto) >= 0) return;
    wrappedProtos.push(proto);
    names.forEach(function (name) {
      var original = proto[name];
      if (typeof original !== "function") return;
      proto[name] = function () {
        draws[key] += 1;
        return original.apply(this, arguments);
      };
    });
  }

  function wrapMethods(ctor, names, key) {
    wrapProto(ctor && ctor.prototype, names, key);
  }

  // WebGL extension objects (instanced and multi draw) are NOT globals in Chromium: their draw methods
  // exist only on the object getExtension() returns, so they are wrapped when it is first handed out.
  // Without this a WebGL1 page that draws only through ANGLE_instanced_arrays shows zero draw calls.
  var EXT_DRAWS = {
    angle_instanced_arrays: ["drawArraysInstancedANGLE", "drawElementsInstancedANGLE"],
    webgl_multi_draw: ["multiDrawArraysWEBGL", "multiDrawElementsWEBGL", "multiDrawArraysInstancedWEBGL", "multiDrawElementsInstancedWEBGL"]
  };

  function wrapGetExtension(ctor) {
    var proto = ctor && ctor.prototype;
    if (!proto || typeof proto.getExtension !== "function") return;
    var original = proto.getExtension;
    proto.getExtension = function (name) {
      var ext = original.apply(this, arguments);
      var names = EXT_DRAWS[String(name).toLowerCase()];
      if (ext && names) wrapProto(Object.getPrototypeOf(ext), names, "gl");
      return ext;
    };
  }

  try {
    var GL_DRAWS = ["drawArrays", "drawElements", "drawArraysInstanced", "drawElementsInstanced", "drawRangeElements"];
    wrapMethods(window.WebGLRenderingContext, GL_DRAWS, "gl");
    wrapMethods(window.WebGL2RenderingContext, GL_DRAWS, "gl");
    wrapGetExtension(window.WebGLRenderingContext);
    wrapGetExtension(window.WebGL2RenderingContext);
    wrapMethods(window.ANGLE_instanced_arrays, EXT_DRAWS.angle_instanced_arrays, "gl");
    wrapMethods(window.WEBGL_multi_draw, EXT_DRAWS.webgl_multi_draw, "gl");
    wrapMethods(
      window.CanvasRenderingContext2D,
      ["fillRect", "strokeRect", "fill", "stroke", "fillText", "strokeText", "drawImage", "putImageData"],
      "c2d"
    );
    HTMLCanvasElement.prototype.getContext = function (type) {
      var ctx = originalGetContext.apply(this, arguments);
      if (ctx) {
        var known = false;
        for (var i = 0; i < contexts.length; i++) if (contexts[i].canvas === this) known = true;
        if (!known) contexts.push({ canvas: this, kind: String(type), ctx: ctx });
      }
      return ctx;
    };
  } catch (e) {
    note("capture", e);
  }

  // ---- measurements ---------------------------------------------------------------------------
  var HEADER_SELECTOR = "[data-gate~=header]";
  var CONTROL_SELECTOR =
    "a[href],button,input,select,textarea,summary,[role=button],[role=tab],[tabindex]:not([tabindex='-1']),[data-gate~=ctl]";
  var ATTR_SCAN_SELECTOR = "[title],[aria-label],[alt],[placeholder]";
  var ATTR_NAMES = ["title", "aria-label", "alt", "placeholder"];

  function label(el) {
    var text = (el.getAttribute("aria-label") || el.textContent || el.id || el.tagName || "?").replace(/\s+/g, " ").trim();
    return clip(el.tagName.toLowerCase() + ":" + text, 40);
  }

  function visibleRect(el) {
    var rect = el.getBoundingClientRect();
    if (!(rect.width > 0 && rect.height > 0)) return null;
    var style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return null;
    return rect;
  }

  function measureHeader(headerSelector) {
    var roots = document.querySelectorAll(headerSelector);
    var items = [];
    for (var r = 0; r < roots.length; r++) {
      var found = roots[r].querySelectorAll(CONTROL_SELECTOR);
      for (var k = 0; k < found.length; k++) {
        var rect = visibleRect(found[k]);
        if (rect) items.push({ el: found[k], rect: rect });
      }
    }
    var maxOverlap = 0;
    var pair = null;
    for (var i = 0; i < items.length; i++) {
      for (var j = i + 1; j < items.length; j++) {
        var a = items[i].rect;
        var b = items[j].rect;
        var w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        var h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (w > 0 && h > 0) {
          var depth = Math.min(w, h); // penetration depth, so a 1 px shared border is ~1
          if (depth > maxOverlap) {
            maxOverlap = depth;
            pair = [label(items[i].el), label(items[j].el)];
          }
        }
      }
    }
    var tol = 0.5;
    var outside = [];
    for (var m = 0; m < items.length; m++) {
      var q = items[m].rect;
      if (q.left < -tol || q.top < -tol || q.right > window.innerWidth + tol || q.bottom > window.innerHeight + tol) {
        if (outside.length < MAX_LIST) outside.push(label(items[m].el));
      }
    }
    return {
      roots: roots.length,
      n: items.length,
      maxOverlapPx: Math.round(maxOverlap * 100) / 100,
      pair: pair,
      outside: outside
    };
  }

  function measureFit() {
    var nodes = document.querySelectorAll("[data-gate~=fit]");
    var clipped = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var rect = el.getBoundingClientRect();
      var bad = rect.left < -0.5 || rect.right > window.innerWidth + 0.5;
      var style = getComputedStyle(el);
      if (style.overflowX !== "visible" && el.scrollWidth > el.clientWidth + 1) bad = true;
      if (bad && clipped.length < MAX_LIST) clipped.push(label(el));
    }
    return { n: nodes.length, clipped: clipped };
  }

  var BAD_TEXT = /undefined|NaN|\[object/g;

  function scanString(text, prefix, bad) {
    var found = 0;
    var m;
    BAD_TEXT.lastIndex = 0;
    while ((m = BAD_TEXT.exec(text)) && found < 3) {
      bad.push(prefix + clip(text.slice(Math.max(0, m.index - 12), m.index + 24).replace(/\s+/g, " "), 40));
      found++;
    }
  }

  function scanText() {
    // Visible text (innerText) plus the text a user can still read or hear: title, aria-label, alt,
    // placeholder attributes (hidden elements included) and document.title.
    var bad = [];
    scanString(document.body ? document.body.innerText || "" : "", "", bad);
    try {
      scanString(document.title || "", "@document.title: ", bad);
      var nodes = document.querySelectorAll(ATTR_SCAN_SELECTOR);
      var attrHits = 0;
      for (var i = 0; i < nodes.length && attrHits < 3; i++) {
        for (var k = 0; k < ATTR_NAMES.length; k++) {
          var value = nodes[i].getAttribute(ATTR_NAMES[k]);
          if (value) {
            var before = bad.length;
            scanString(value, "@" + ATTR_NAMES[k] + ": ", bad);
            if (bad.length > before) attrHits++;
          }
        }
      }
    } catch (e) {
      bad.push("attribute scan failed: " + clip(fmt(e), 60));
    }
    return bad;
  }

  function scrollMetrics() {
    var de = document.documentElement;
    var body = document.body;
    var root = document.scrollingElement || de;
    return {
      scrollWidth: Math.max(de ? de.scrollWidth : 0, body ? body.scrollWidth : 0),
      // clientWidth excludes a classic vertical scrollbar; innerWidth does not. Comparing
      // scrollWidth with innerWidth lets up to ~15 px of horizontal overflow through.
      clientWidth: root ? root.clientWidth : 0
    };
  }

  function jsonSafe(value) {
    try {
      return JSON.parse(JSON.stringify(value === undefined ? null : value));
    } catch (e) {
      return { unserialisable: String(e) };
    }
  }

  // ---- canvas probe (reads pixels itself, same task as the page's renderOnce) -------------------
  var GL_ERROR_NAMES = {
    1280: "INVALID_ENUM",
    1281: "INVALID_VALUE",
    1282: "INVALID_OPERATION",
    1285: "OUT_OF_MEMORY",
    1286: "INVALID_FRAMEBUFFER_OPERATION",
    37442: "CONTEXT_LOST_WEBGL"
  };

  function drainGlErrors(gl) {
    var out = [];
    for (var i = 0; i < 8; i++) {
      var code = gl.getError();
      if (code === gl.NO_ERROR) break;
      out.push(GL_ERROR_NAMES[code] || "0x" + code.toString(16));
      if (code === 37442) break; // CONTEXT_LOST_WEBGL repeats forever
    }
    return out;
  }

  function readGl(gl, rec) {
    var w = gl.drawingBufferWidth;
    var h = gl.drawingBufferHeight;
    rec.width = w;
    rec.height = h;
    rec.renderer = glRenderer(gl);
    rec.contextLost = !!gl.isContextLost();
    rec.glErrors = drainGlErrors(gl); // errors the page's own draw calls left behind
    if (rec.contextLost || !(w > 0 && h > 0)) return;
    var isGl2 = typeof window.WebGL2RenderingContext !== "undefined" && gl instanceof window.WebGL2RenderingContext;
    var target = isGl2 ? gl.READ_FRAMEBUFFER : gl.FRAMEBUFFER;
    var binding = isGl2 ? gl.READ_FRAMEBUFFER_BINDING : gl.FRAMEBUFFER_BINDING;
    var previous = gl.getParameter(binding);
    var px = new Uint8Array(w * h * 4);
    try {
      gl.bindFramebuffer(target, null); // read what the page displays, not a leftover offscreen target
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    } finally {
      gl.bindFramebuffer(target, previous);
    }
    var readErrors = drainGlErrors(gl);
    for (var i = 0; i < readErrors.length; i++) rec.glErrors.push("readPixels:" + readErrors[i]);
    var stats = dominantColourStats(px, Math.max(1, Math.floor((w * h) / SAMPLE_LIMIT)));
    rec.nonBgRatio = stats.nonBgRatio;
    rec.sampled = stats.sampled;
    rec.bg = stats.bg;
  }

  function readViaDrawImage(canvas, rec) {
    // 2D canvases, canvases without a registered context (a blank bitmap) and exotic kinds.
    var w = canvas.width;
    var h = canvas.height;
    rec.width = w;
    rec.height = h;
    if (!(w > 0 && h > 0)) return;
    var copy = document.createElement("canvas");
    copy.width = w;
    copy.height = h;
    var c2d = originalGetContext.call(copy, "2d", { willReadFrequently: true }); // ours: not registered
    c2d.drawImage(canvas, 0, 0);
    var px = c2d.getImageData(0, 0, w, h).data;
    var stats = dominantColourStats(px, Math.max(1, Math.floor((w * h) / SAMPLE_LIMIT)));
    rec.nonBgRatio = stats.nonBgRatio;
    rec.sampled = stats.sampled;
    rec.bg = stats.bg;
  }

  function probeCanvases() {
    var savedGl = draws.gl;
    var savedC2d = draws.c2d; // the probe's own drawImage calls must not count as page draws
    var out = probeCanvasList();
    draws.gl = savedGl;
    draws.c2d = savedC2d;
    return out;
  }

  function probeCanvasList() {
    var out = [];
    var nodes = document.querySelectorAll("canvas,[data-gate~=canvas]");
    for (var i = 0; i < nodes.length && out.length < MAX_LIST; i++) {
      var el = nodes[i];
      var declared = el.matches("[data-gate~=canvas]");
      var visible = visibleRect(el) !== null;
      if (!visible && !declared) continue;
      var rec = { el: label(el), declared: declared, visible: visible, isCanvas: el.tagName === "CANVAS", kind: null };
      if (!rec.isCanvas) {
        out.push(rec); // data-gate=canvas on a non-canvas element: the runner reports it
        continue;
      }
      if (visible) {
        var entry = null;
        for (var k = 0; k < contexts.length; k++) if (contexts[k].canvas === el) entry = contexts[k];
        rec.kind = entry ? entry.kind : null;
        try {
          if (entry && (entry.kind === "webgl" || entry.kind === "webgl2" || entry.kind === "experimental-webgl")) {
            readGl(entry.ctx, rec);
          } else {
            readViaDrawImage(el, rec);
          }
        } catch (e) {
          rec.readError = clip(fmt(e), 120);
        }
      }
      out.push(rec);
    }
    return out;
  }

  // ---- walker ---------------------------------------------------------------------------------
  function run(hooks) {
    hooks = hooks || {};
    var tabs = hooks.tabs && hooks.tabs.length ? hooks.tabs.slice() : [];
    var sleepMs = typeof hooks.sleepMs === "number" ? hooks.sleepMs : 50;
    var headerSelector = hooks.headerSelector || HEADER_SELECTOR;
    var setTab = hooks.setTab || function (tab) { location.hash = "#/" + tab; };
    var steps = hooks.steps && hooks.steps.length ? hooks.steps.slice() : [];
    var result = { v: RESULT_VERSION, nonce: NONCE, tabs: [], steps: [], errors: [], missingHooks: [], tabStateChecked: false };
    if (typeof hooks.renderOnce !== "function") result.missingHooks.push("renderOnce");
    if (typeof hooks.frame !== "function") result.missingHooks.push("frame");
    // Optional: with a tabState hook the runner asserts that the state really changes from one tab
    // to the next (a setTab that silently does nothing would otherwise measure the same view N times).
    var tabStateHook = typeof hooks.tabState === "function" ? hooks.tabState : null;
    result.tabStateChecked = !!tabStateHook;

    var tabIndex = 0;
    var stepIndex = 0;

    function readFrame() {
      try {
        return typeof hooks.frame === "function" ? Number(hooks.frame()) : null;
      } catch (e) {
        note("frame", e);
        return null;
      }
    }

    function callRender(tab, rec) {
      if (typeof hooks.renderOnce !== "function") return null;
      try {
        return jsonSafe(hooks.renderOnce(tab));
      } catch (e) {
        rec.threw = clip(fmt(e), 200);
        note("renderOnce", e);
        return null;
      }
    }

    function readTabState() {
      if (!tabStateHook) return undefined;
      try {
        return String(tabStateHook());
      } catch (e) {
        note("tabState", e);
        return null;
      }
    }

    function measureTab(tab) {
      var rec = { tab: String(tab), threw: null };
      var state = readTabState();
      if (state !== undefined) rec.state = state;
      // Two consecutive reads with nothing rendered in between: a real frame counter is a pure getter.
      // A frame() that increments whenever it is CALLED (a fake heartbeat) shows up as reads[0] != reads[1].
      var firstRead = readFrame();
      var f0 = readFrame();
      rec.frameReads = [firstRead, f0];
      callRender(tab, rec);
      var f1 = readFrame();
      var d0 = draws.gl + draws.c2d;
      rec.render = callRender(tab, rec); // the second call is the reported one: the heartbeat
      rec.draws = draws.gl + draws.c2d - d0;
      rec.canvases = probeCanvases(); // same task as renderOnce: the drawing buffer is still valid
      var f2 = readFrame();
      rec.frames = [f0, f1, f2];
      rec.header = measureHeader(headerSelector);
      var metrics = scrollMetrics();
      rec.scrollWidth = metrics.scrollWidth;
      rec.clientWidth = metrics.clientWidth;
      rec.fit = measureFit();
      rec.textBad = scanText();
      result.tabs.push(rec);
    }

    function nextTab() {
      if (tabIndex >= tabs.length) {
        setTimeout(nextStep, 0);
        return;
      }
      var tab = tabs[tabIndex++];
      try {
        setTab(tab);
      } catch (e) {
        note("setTab", e);
      }
      setTimeout(function () {
        try {
          measureTab(tab);
        } catch (e) {
          note("walker", e);
          result.tabs.push({ tab: String(tab), threw: clip(fmt(e), 200), walkerError: true });
        }
        setTimeout(nextTab, 0);
      }, sleepMs);
    }

    function nextStep() {
      if (stepIndex >= steps.length) {
        finish();
        return;
      }
      var step = steps[stepIndex++];
      var rec = { name: String(step && step.name), ok: true, msg: "" };
      try {
        var out = step.run();
        if (out === false || typeof out === "string") {
          rec.ok = false;
          rec.msg = clip(out === false ? "returned false" : out, 200);
        }
      } catch (e) {
        rec.ok = false;
        rec.msg = clip(fmt(e), 200);
        note("step", e);
      }
      result.steps.push(rec);
      setTimeout(nextStep, sleepMs);
    }

    var boundarySeen = 0;

    function pollBoundary(left) {
      // The page's error boundary appends to window.__ATLAS_ERRORS__; keep reading it after finish.
      var boundary = window.__ATLAS_ERRORS__;
      if (boundary && boundary.length > boundarySeen) {
        for (var i = boundarySeen; i < boundary.length; i++) note("boundary", fmt(boundary[i]));
        boundarySeen = boundary.length;
      }
      if (left > 0) setTimeout(function () { pollBoundary(left - 1); }, 500);
    }

    function finish() {
      try {
        var boundary = window.__ATLAS_ERRORS__;
        if (boundary && boundary.length) {
          for (var i = 0; i < boundary.length; i++) note("boundary", fmt(boundary[i]));
          boundarySeen = boundary.length;
        }
        result.ua = String(navigator.userAgent || "");
        result.innerWidth = window.innerWidth;
        result.innerHeight = window.innerHeight;
        result.devicePixelRatio = window.devicePixelRatio;
        result.errors = errors.slice();
      } catch (e) {
        result.errors = errors.concat(["finish: " + fmt(e)]);
      }
      finishedErrorCount = errors.length;
      finishedErrorTotal = errorTotal;
      var pre = document.createElement("pre");
      pre.id = "atlas-gate";
      pre.setAttribute("data-done", "1");
      pre.textContent = JSON.stringify(result);
      var scripts = document.getElementsByTagName("script");
      while (scripts.length) scripts[0].parentNode.removeChild(scripts[0]);
      (document.body || document.documentElement).appendChild(pre);
      resultEl = pre;
      finished = true; // from here on every note() mirrors into pre[data-late-errors]
      setTimeout(function () { pollBoundary(BOUNDARY_POLLS); }, 500);
    }

    setTimeout(nextTab, 0);
  }

  window.AtlasGate = { active: true, run: run, pixelStats: pixelStats, glRenderer: glRenderer };
})();
