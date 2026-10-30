// Test helper (not a test): reads every runtime module of the viewer (viewer/src/*.js and viewer/src/ui/*.js), orders them by their
// single-line imports (dependencies first), strips the import lines and the `export ` keyword, and returns one classic script.
// This is the "strip and concatenate" bundling option of docs/atlas/spec/bundle-contract.md; the probe page builder and the
// structure test use it. Synthetic: no model data.
import { readdirSync, readFileSync } from 'node:fs';

export const SRC = new URL('../../../src/gemarc_mcp/atlas/viewer/src/', import.meta.url);
export const UI = new URL('ui/', SRC);

/** Relative path (from viewer/src/) of every runtime module, sorted. */
export function moduleFiles() {
  const top = readdirSync(SRC).filter((f) => f.endsWith('.js')).sort();
  const ui = readdirSync(UI).filter((f) => f.endsWith('.js')).sort().map((f) => 'ui/' + f);
  return [...top, ...ui];
}

export const readModule = (rel) => readFileSync(new URL(rel, SRC), 'utf8');

const IMPORT = /^import \{ ([A-Za-z0-9_, ]+) \} from '(\.{1,2}\/[a-z0-9\-/]+\.js)';$/;

/** Resolve an import specifier against the importing module's directory, giving a path relative to viewer/src/. */
export function resolveImport(fromRel, spec) {
  const base = fromRel.includes('/') ? fromRel.slice(0, fromRel.lastIndexOf('/')).split('/') : [];
  for (const part of spec.split('/')) { if (part === '.') continue; if (part === '..') base.pop(); else base.push(part); }
  return base.join('/');
}

/** Imports of a module: [{names, from}] with `from` resolved. */
export function importsOf(rel, text = readModule(rel)) {
  const out = [];
  for (const line of text.split('\n')) {
    if (!/^import\b/.test(line)) continue;
    const m = IMPORT.exec(line);
    if (!m) throw new Error(`${rel}: unsupported import line: ${line}`);
    out.push({ names: m[1].split(',').map((x) => x.trim()), from: resolveImport(rel, m[2]) });
  }
  return out;
}

/** Dependency order (every module after the modules it imports). Throws on a cycle or a missing file. */
export function orderModules(files = moduleFiles()) {
  const set = new Set(files); const order = []; const state = new Map();
  const visit = (f, stack) => {
    if (state.get(f) === 2) return;
    if (state.get(f) === 1) throw new Error('import cycle: ' + [...stack, f].join(' -> '));
    if (!set.has(f)) throw new Error(`missing module ${f} (imported by ${stack[stack.length - 1]})`);
    state.set(f, 1);
    for (const d of importsOf(f)) visit(d.from, [...stack, f]);
    state.set(f, 2); order.push(f);
  };
  for (const f of files) visit(f, []);
  return order;
}

/** The module text without its import lines and without the `export ` keyword. */
export function stripModule(rel) {
  return readModule(rel).split('\n').filter((l) => !/^import\b/.test(l)).map((l) => l.replace(/^export (const|function|class) /, '$1 ')).join('\n');
}

/** Top-level declared names of a stripped module (one declaration per statement at column 0, the repository style). */
export function topLevelNames(code) {
  const names = [];
  for (const line of code.split('\n')) {
    const m = /^(?:async )?function (\w+)|^(?:const|let|var|class) (\w+)/.exec(line);
    if (m) names.push(m[1] || m[2]);
  }
  return names;
}

/** One classic script of all modules in dependency order. */
export function concatModules(files = moduleFiles()) {
  const order = orderModules(files);
  return { order, code: order.map((f) => `// ---- ${f}\n${stripModule(f)}`).join('\n') };
}
