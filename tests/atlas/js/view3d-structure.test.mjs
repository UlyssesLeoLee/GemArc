// Structure of the stage modules (viewer-B): the same boundaries ui-structure.test.mjs enforces for the shell, stated for the new modules by name, so the
// guarantee is stated here for the new modules by name. The DOM half of the stage lives in ui/tab-slots.js, the one new-tab path the shell structure test lists as a DOM user.
//  * stage-state, scene-model, gl-renderer, diagram-model: pure (no page, no timer, no randomness, no clock; gl-renderer only calls methods of the context it is given);
//  * tab-slots (the stage tabs): reaches the page only through ui/dom.js;
//  * nothing in the stage depends on time for its result (the clock is passed in; only dom.js reads performance.now), so a gate run is deterministic.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { moduleFiles, readModule, importsOf, concatModules, topLevelNames, stripModule } from './ui-concat.mjs';

const PURE = ['ui/stage-state.js', 'ui/scene-model.js', 'ui/gl-renderer.js', 'ui/diagram-model.js'];
const DOM_USERS = ['ui/tab-slots.js'];
const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').filter((l) => !/^\s*\/\//.test(l) && !/^import\b/.test(l)).map((l) => l.replace(/\s\/\/.*$/, '')).join('\n');
const PAGE = [/\bdocument\s*[.[]/, /\bwindow\b/, /\bglobalThis\b/, /\blocalStorage\b/, /\bsessionStorage\b/, /\bnavigator\s*[.[]/, /\blocation\s*[.[]/, /\bhistory\./, /\bsetTimeout\b/, /\bsetInterval\b/, /requestAnimationFrame/, /\bconsole\./,
  /\bfetch\s*\(/, /\bimport\s*\(/, /XMLHttpRequest/, /\beval\s*\(/, /new Function/, /https?:\/\//];
const CLOCK = [/Math\.random/, /\bDate\b/, /\bperformance\b/];
const MARKUP = [/\.createElement|\.createElementNS|\.createTextNode|\.querySelector|\.getElementById|\.addEventListener|\.removeEventListener|\.setAttribute|\.removeAttribute|\.innerHTML|\.outerHTML|\.insertAdjacentHTML|\.textContent\s*=|\.classList|\.dataset|\.style\./];

test('the stage modules exist and are classified', () => {
  const all = moduleFiles();
  for (const f of [...PURE, ...DOM_USERS]) assert.ok(all.includes(f), f);
});

test('pure stage modules: no page, no timer, no clock, no randomness, no markup, no adapter import', () => {
  for (const f of PURE) {
    const s = strip(readModule(f));
    for (const re of [...PAGE, ...CLOCK, ...MARKUP, /\.appendChild|\.removeChild|\.textContent|\.innerText/, /from '\.\/dom\.js'/]) assert.ok(!re.test(s), `${f}: ${re}`);
    assert.ok(!importsOf(f).some((d) => d.from === 'ui/dom.js'), f);
  }
});

test('DOM users of the stage reach the page only through the adapter, and neither reads a clock nor randomness', () => {
  for (const f of DOM_USERS) {
    const s = strip(readModule(f));
    for (const re of [...PAGE, ...CLOCK, ...MARKUP]) assert.ok(!re.test(s), `${f}: ${re}`);
    assert.ok(importsOf(f).some((d) => d.from === 'ui/dom.js'), f + ' uses the adapter');
  }
});

test('only the adapter names the animation frame, the clock, the pixel ratio, the motion preference and the GL context', () => {
  const adapter = strip(readModule('ui/dom.js'));
  for (const name of ['requestAnimationFrame', 'performance', 'devicePixelRatio', 'prefers-reduced-motion', 'getContext']) assert.ok(adapter.includes(name), name);
  for (const f of moduleFiles().filter((x) => x !== 'ui/dom.js')) {
    const s = strip(readModule(f));
    assert.ok(!/devicePixelRatio|prefers-reduced-motion|requestAnimationFrame|\bperformance\b/.test(s), f);
  }
});

test('gl-renderer calls only methods of the context it is handed: no canvas, no context creation, no extension lookup but the two it needs', () => {
  const s = strip(readModule('ui/gl-renderer.js'));
  assert.ok(!/getContext|canvas|HTMLCanvas|OffscreenCanvas/i.test(s));
  const exts = [...s.matchAll(/getExtension\('([A-Za-z_]+)'\)/g)].map((m) => m[1]).sort();
  assert.deepEqual(exts, ['ANGLE_instanced_arrays', 'WEBGL_debug_renderer_info']);
});

test('top-level names are unique across the whole runtime and the concatenation of all modules still compiles', () => {
  const seen = new Map();
  for (const f of moduleFiles()) for (const n of topLevelNames(stripModule(f))) { assert.ok(!seen.has(n), `${n}: ${seen.get(n)} and ${f}`); seen.set(n, f); }
  assert.doesNotThrow(() => new vm.Script(concatModules().code));
});

test('shader lines carry the diagnostic marker the literal scan of test_viewer_i18n.py needs, and every stage module is ASCII with LF', () => {
  const lines = readModule('ui/gl-renderer.js').split('\n').filter((l) => /^const GL_[VF]S_/.test(l));
  assert.equal(lines.length, 8); assert.ok(lines.every((l) => l.includes('// diag:')));
  for (const f of [...PURE, ...DOM_USERS]) { const raw = readModule(f); assert.ok(!/[^\x00-\x7f]/.test(raw) && !raw.includes('\r'), f); }
});
