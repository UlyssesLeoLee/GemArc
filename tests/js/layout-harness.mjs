/**
 * Layout harness: drive the real shell code in a stub DOM and report what it
 * stores, so the Python suite can assert on the behaviour instead of on the
 * source text.
 *
 * The question it answers: if the reader drags a splitter and the drag ends
 * without a pointerup (the pointer leaves the window, the gesture is
 * cancelled), is the width they chose still remembered?  A shell that only
 * persists on pointerup loses it: the pane is painted at the new width, but
 * the next focus toggle or reload falls back to the old width.
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
  return source.slice(start, end);
}

/** A DOM stub with just enough surface for the layout block. */
function makeSandbox() {
  const store = new Map();
  const painted = new Map();
  const listeners = new Map();

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

  const paneHandles = [splitter('sidebar'), splitter('inspector')];
  const main = {
    classList: { toggle() {} },
    getBoundingClientRect: () => ({ left: 0, right: 1280, width: 1280 }),
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
      addEventListener() {},
    },
    document: {
      documentElement: { style: { setProperty: (name, value) => painted.set(name, value) } },
      querySelector(selector) {
        if (selector === 'main') return main;
        if (selector === '#focus-toggle') return toggle;
        return null;
      },
      querySelectorAll(selector) {
        return selector === '.splitter' ? paneHandles : [];
      },
    },
    console,
  };
  sandbox.__store = store;
  sandbox.__painted = painted;
  sandbox.__listeners = listeners;
  return sandbox;
}

/** Drag the sidebar splitter to the right, then stop - no pointerup. */
function dragWithoutPointerUp(source) {
  const sandbox = makeSandbox();
  runInNewContext(extractLayout(source), sandbox);
  sandbox.setupLayout();

  const down = sandbox.__listeners.get('sidebar:pointerdown');
  const move = sandbox.__listeners.get('sidebar:pointermove');
  if (!down || !move) throw new Error('the shell does not wire the splitter');

  down({ clientX: 236, clientY: 300, pointerId: 1, preventDefault() {} });
  // 100 px to the right of the starting point.
  move({ clientX: 336, clientY: 300, pointerId: 1 });

  const raw = sandbox.__store.get(LAYOUT_KEY);
  const stored = raw ? JSON.parse(raw) : null;
  return {
    stored,
    painted: sandbox.__painted.get('--sidebar-w') || null,
    ariaLabel: sandbox.__listeners ? null : null,
  };
}

const shells = {
  shared: readFileSync(`${REPO}src/gemarc_mcp/scaffold/templates/shared/app.js`, 'utf8'),
  mvp: readFileSync(`${REPO}src/gemarc_mcp/web/app.js`, 'utf8'),
};

const report = {};
let failed = false;
for (const [name, source] of Object.entries(shells)) {
  const result = dragWithoutPointerUp(source);
  const storedWidth = result.stored ? result.stored.sidebar : null;
  const paintedWidth = Number.parseInt(String(result.painted || ''), 10);
  const ok = storedWidth !== null
    && storedWidth > 232
    && paintedWidth === storedWidth;
  report[name] = { ...result, ok };
  if (!ok) failed = true;
}

process.stdout.write(JSON.stringify(report, null, 2));
process.stdout.write('\n');
if (failed) {
  process.stderr.write('a shell does not persist the width chosen by a drag that ends without pointerup\n');
  process.exit(1);
}
