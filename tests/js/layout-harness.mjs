/**
 * Layout harness: drive the real shell code in a stub DOM and report what it
 * stores, so the Python suite can assert on the behaviour instead of on the
 * source text.
 *
 * The question it answers: if the reader drags a splitter and the drag ends
 * without a pointerup (the pointer leaves the window, the gesture is
 * cancelled), is the width they chose still remembered?  A shell that only
 * persists on pointerup loses it: the pane is painted at the new width, but
 * the next focus toggle or reload jumps it back.
 *
 * Every scenario runs in a fresh sandbox, and the expected numbers come from
 * the shell's own defaults - never from a number shared by both shells: a
 * threshold that happens to sit below one shell's default would make that
 * shell's check pass while its drag is completely broken.
 *
 * Output: JSON on stdout, exit code 1 when a shell misbehaves.
 */
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const REPO = process.argv[2]
  ? process.argv[2].replace(/[\\/]+$/, '') + '/'
  : new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const LAYOUT_KEY = 'gemarc.layout.v1';

/** The layout block of a shell file, with everything else cut away. */
function extractLayout(source) {
  const start = source.indexOf('const LAYOUT_KEY');
  if (start < 0) throw new Error('no layout block found');
  const markers = ['/* -------------------------------------------------------------------- boot */', 'async function main('];
  let end = -1;
  for (const marker of markers) {
    const at = source.indexOf(marker, start);
    if (at > start && (end < 0 || at < end)) end = at;
  }
  if (end < 0) throw new Error('no end marker found');
  // The epilogue exposes the module constants to the test: the expected
  // numbers must come from the shell under test, not from a copy here.
  return `${source.slice(start, end)}\nglobalThis.__probe={LAYOUT_DEFAULTS,PANE_LIMITS,MAIN_MIN,GUTTER,KEY_STEP};\n`;
}

/** A DOM stub with just enough surface for the layout block. */
function makeSandbox() {
  const store = new Map();
  const painted = new Map();
  const listeners = new Map();
  const state = { width: 1280 };

  const splitter = (pane) => ({
    dataset: { pane, label: `调整${pane}宽度` },
    attrs: {},
    classList: { add() {}, remove() {} },
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute(name) { return this.attrs[name]; },
    addEventListener(type, handler) { listeners.set(`${pane}:${type}`, handler); },
    setPointerCapture() {},
    releasePointerCapture() {},
  });

  const dividers = [splitter('sidebar'), splitter('inspector')];
  const main = {
    classList: { toggle() {} },
    getBoundingClientRect: () => ({ left: 0, right: state.width, width: state.width }),
  };
  const toggle = {
    textContent: '',
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute(name) { return this.attrs[name]; },
    addEventListener(type, handler) { listeners.set(`toggle:${type}`, handler); },
  };

  const sandbox = {
    window: {
      localStorage: {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => store.set(key, value),
      },
      addEventListener(type, handler) { listeners.set(`window:${type}`, handler); },
    },
    document: {
      documentElement: { style: { setProperty: (name, value) => painted.set(name, value) } },
      querySelector(selector) {
        if (selector === 'main') return main;
        if (selector === '#focus-toggle') return toggle;
        return null;
      },
      querySelectorAll(selector) {
        return selector === '.splitter' ? dividers : [];
      },
    },
    console,
  };
  sandbox.__store = store;
  sandbox.__painted = painted;
  sandbox.__listeners = listeners;
  sandbox.__state = state;
  return sandbox;
}

function run(sandbox) {
  runInNewContext(sandbox.__source, sandbox);
  sandbox.setupLayout();
  return sandbox;
}

function stored(sandbox) {
  const raw = sandbox.__store.get(LAYOUT_KEY);
  return raw ? JSON.parse(raw) : null;
}

function paintedWidth(sandbox) {
  const value = sandbox.__painted.get('--sidebar-w');
  return value ? Number.parseInt(value, 10) : null;
}

/** A fresh sandbox running the given shell source. */
function boot(source) {
  const sandbox = makeSandbox();
  sandbox.__source = extractLayout(source);
  return run(sandbox);
}

const DRAG = 100;

function scenarios(source) {
  const out = {};

  // 1. The drag that matters: it ends without a pointerup.
  {
    const box = boot(source);
    const { LAYOUT_DEFAULTS } = box.__probe;
    const start = LAYOUT_DEFAULTS.sidebar + 4;
    box.__listeners.get('sidebar:pointerdown')({ clientX: start, clientY: 300, pointerId: 1, preventDefault() {} });
    box.__listeners.get('sidebar:pointermove')({ clientX: start + DRAG, clientY: 300, pointerId: 1 });
    out.dragWithoutPointerUp = {
      expected: LAYOUT_DEFAULTS.sidebar + DRAG,
      stored: stored(box) ? stored(box).sidebar : null,
      painted: paintedWidth(box),
    };
  }

  // 2. The three ways a drag can end: each writes the final value once.
  for (const [name, event] of [['pointerup', 'pointerup'], ['pointercancel', 'pointercancel'],
                                ['lostpointercapture', 'lostpointercapture']]) {
    const box = boot(source);
    const { LAYOUT_DEFAULTS } = box.__probe;
    const start = LAYOUT_DEFAULTS.sidebar + 4;
    box.__listeners.get('sidebar:pointerdown')({ clientX: start, clientY: 300, pointerId: 1, preventDefault() {} });
    box.__listeners.get('sidebar:pointermove')({ clientX: start + DRAG, clientY: 300, pointerId: 1 });
    box.__listeners.get(`sidebar:${event}`)({ clientX: start + DRAG, clientY: 300, pointerId: 1 });
    out[`drag${name}`] = {
      expected: LAYOUT_DEFAULTS.sidebar + DRAG,
      stored: stored(box) ? stored(box).sidebar : null,
      painted: paintedWidth(box),
    };
  }

  // 3. The keyboard nudge.
  {
    const box = boot(source);
    const { LAYOUT_DEFAULTS, KEY_STEP } = box.__probe;
    box.__listeners.get('sidebar:keydown')({ key: ['Arrow','Right'].join(''), shiftKey: false, preventDefault() {} });
    out.keyboard = {
      expected: LAYOUT_DEFAULTS.sidebar + KEY_STEP,
      stored: stored(box) ? stored(box).sidebar : null,
      painted: paintedWidth(box),
    };
  }

  // 4. The double click resets the pane.
  {
    const box = boot(source);
    const { LAYOUT_DEFAULTS } = box.__probe;
    box.__listeners.get('sidebar:dblclick')({});
    out.doubleClick = {
      expected: LAYOUT_DEFAULTS.sidebar,
      stored: stored(box) ? stored(box).sidebar : null,
      painted: paintedWidth(box),
    };
  }

  // 5. The inspector pane, which measures from the other edge.
  {
    const box = boot(source);
    const { LAYOUT_DEFAULTS } = box.__probe;
    const start = 1280 - 4 - LAYOUT_DEFAULTS.inspector;
    box.__listeners.get('inspector:pointerdown')({ clientX: start, clientY: 300, pointerId: 2, preventDefault() {} });
    box.__listeners.get('inspector:pointermove')({ clientX: start - 60, clientY: 300, pointerId: 2 });
    const value = stored(box);
    out.inspector = {
      expected: LAYOUT_DEFAULTS.inspector + 60,
      stored: value ? value.inspector : null,
      painted: Number.parseInt(sandboxPaint(box), 10),
    };
  }

  // 6. A narrower window gives the room back to the main column.
  {
    const box = boot(source);
    const { PANE_LIMITS } = box.__probe;
    box.__state.width = 600;
    box.__listeners.get('window:resize')({});
    const value = stored(box);
    out.resize = {
      expected: PANE_LIMITS.sidebar.min,
      stored: value ? value.sidebar : null,
      painted: paintedWidth(box),
    };
  }

  return out;
}

function sandboxPaint(box) {
  return box.__painted.get('--inspector-w') || '';
}

const shells = {
  shared: readFileSync(`${REPO}src/gemarc_mcp/scaffold/templates/shared/app.js`, 'utf8'),
  mvp: readFileSync(`${REPO}src/gemarc_mcp/web/app.js`, 'utf8'),
};

const report = {};
let failed = false;
for (const [name, source] of Object.entries(shells)) {
  const results = scenarios(source);
  const broken = [];
  for (const [scenario, result] of Object.entries(results)) {
    const ok = result.stored === result.expected
      && (scenario === 'resize' ? result.stored === result.expected : result.painted === result.expected);
    if (!ok) broken.push(scenario);
  }
  report[name] = { results, broken, ok: broken.length === 0 };
  if (broken.length) failed = true;
}

process.stdout.write(JSON.stringify(report, null, 2));
process.stdout.write('\n');
if (failed) {
  process.stderr.write('a shell does not keep the layout the reader chose\n');
  process.exit(1);
}
