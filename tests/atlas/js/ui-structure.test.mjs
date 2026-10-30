// Structure of the UI modules (viewer/src/ui/): bundler conventions, the DOM boundary (acceptance: every runtime module is DOM-free except the
// shell and tab modules, which reach the page only through the adapter dom.js), unique top-level names across ALL runtime modules (so the
// strip-and-concatenate bundling of the contract works), no network strings, and the one-classic-script compile.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { moduleFiles, readModule, importsOf, orderModules, concatModules, stripModule, topLevelNames } from './ui-concat.mjs';

const ALL = moduleFiles();
const UI = ALL.filter((f) => f.startsWith('ui/'));
const ADAPTER = 'ui/dom.js';
// the modules that build or change elements: they may only do so through the adapter
const DOM_USERS = ['ui/shell.js', 'ui/main.js', 'ui/panel-views.js', 'ui/render-md.js', 'ui/tab-doc.js', 'ui/tab-qa.js', 'ui/tab-slots.js', 'ui/tab-twod.js'];
const PURE = UI.filter((f) => f !== ADAPTER && !DOM_USERS.includes(f));
const strip = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').filter((l) => !/^\s*\/\//.test(l) && !/^import\b/.test(l)).map((l) => l.replace(/\s\/\/.*$/, '')).join('\n'); // code only: comments may name the forbidden words

test('the UI module set: adapter, pure logic modules, shell and tab modules (nothing unclassified)', () => {
  assert.ok(UI.includes(ADAPTER));
  for (const f of DOM_USERS) assert.ok(UI.includes(f), f);
  assert.deepEqual(UI.filter((f) => f !== ADAPTER && !DOM_USERS.includes(f) && !PURE.includes(f)), []);
  assert.ok(PURE.length >= 10, 'the logic modules');
});

test('bundler convention: ASCII, LF, single-line relative imports, only export const|function|class, no cycles, imports exist', () => {
  for (const f of ALL) {
    const s = readModule(f);
    assert.ok(!/[^\x00-\x7f]/.test(s), `${f}: non-ASCII character (write it as an escape)`);
    assert.ok(!s.includes('\r'), `${f}: CR`);
    assert.ok(!s.startsWith('﻿'), `${f}: BOM`);
    for (const l of s.split('\n').filter((x) => /^export\b/.test(x))) assert.match(l, /^export (const|function|class) /, `${f}: ${l}`);
    assert.ok(!/^export default|^export \{|^export \*/m.test(s), `${f}: re-export or default export`);
    assert.ok(!/\bimport\s*\(/.test(s), `${f}: dynamic import`);
    importsOf(f); // throws on an unsupported import line or a path that does not resolve in the file set below
  }
  const order = orderModules(); // throws on a cycle or a missing module
  assert.equal(order.length, ALL.length);
  for (const f of UI) for (const d of importsOf(f)) {
    const nameList = d.names;
    const exported = new Set(topLevelNames(readModule(d.from).split('\n').filter((l) => /^export /.test(l)).map((l) => l.replace(/^export /, '')).join('\n')));
    for (const n of nameList) assert.ok(exported.has(n), `${f}: imports ${n} which ${d.from} does not export`);
  }
});

test('the pure logic modules are DOM-free: no page, window, storage, location, timer, console, network or randomness', () => {
  const forbidden = [/\bdocument\s*[.[]/, /\bwindow\b/, /\bglobalThis\b/, /\blocalStorage\b/, /\bsessionStorage\b/, /\bnavigator\s*[.[]/, /\blocation\s*[.[]/, /\bhistory\./, /\bsetTimeout\b/, /\bsetInterval\b/, /requestAnimationFrame/, /\bconsole\./,
    /\bfetch\s*\(/, /\bimport\s*\(/, /url\(\s*http/i, /@import/, /XMLHttpRequest/, /\beval\s*\(/, /new Function/, /Math\.random/, /\bDate\b/, /https?:\/\//, /\.appendChild|\.removeChild|\.setAttribute|\.addEventListener|\.createElement|\.createTextNode|\.querySelector|\.getElementById|\.innerHTML|\.outerHTML|\.textContent|\.classList|\.insertAdjacentHTML/, /from '\.\/dom\.js'/];
  for (const f of PURE) { const s = strip(readModule(f)); for (const re of forbidden) assert.ok(!re.test(s), `${f}: ${re}`); }
});

test('the shell and tab modules reach the page only through the adapter: no global page access, no markup parsing', () => {
  const forbidden = [/\bdocument\s*[.[]/, /\bwindow\b/, /\bglobalThis\b/, /\blocalStorage\b/, /\bsessionStorage\b/, /\bnavigator\s*[.[]/, /\blocation\s*[.[]/, /\bhistory\./, /\bsetTimeout\b/, /\bsetInterval\b/, /requestAnimationFrame/, /\bconsole\./,
    /\bfetch\s*\(/, /\bimport\s*\(/, /XMLHttpRequest/, /\beval\s*\(/, /new Function/, /Math\.random/, /\bDate\b/, /https?:\/\//,
    /\.createElement|\.createElementNS|\.createTextNode|\.querySelector|\.getElementById|\.addEventListener|\.removeEventListener|\.setAttribute|\.removeAttribute|\.innerHTML|\.outerHTML|\.insertAdjacentHTML|\.textContent\s*=|\.classList|\.dataset|\.style\./];
  for (const f of DOM_USERS) { const s = strip(readModule(f)); for (const re of forbidden) assert.ok(!re.test(s), `${f}: ${re}`); }
});

test('the adapter is the only module that names the page; it never parses markup or reaches the network', () => {
  const s = strip(readModule(ADAPTER));
  for (const re of [/\.innerHTML|\.outerHTML|\.insertAdjacentHTML|document\.write|\bfetch\s*\(|\bimport\s*\(|XMLHttpRequest|\beval\s*\(|new Function|Math\.random|\bDate\b/]) assert.ok(!re.test(s), String(re));
  const urls = s.match(/https?:\/\/[^'"\s]*/g) || [];
  assert.deepEqual(urls, ['http://www.w3.org/2000/svg'], 'only the SVG namespace name');
  for (const f of ALL.filter((x) => x !== ADAPTER)) assert.ok(!/\bdomRoot\b|\bdomDoc\b/.test(readModule(f)), `${f}: adapter internals`);
});

test('no module touches the legacy forbidden strings of the self-contained page (BD-001 text scan of the whole runtime)', () => {
  const { code } = concatModules();
  for (const re of [/\bfetch\s*\(/, /\bimport\s*\(/, /@import/, /url\(\s*http/i, /\bsrc\s*=\s*['"]https?:/, /\bhref\s*=\s*['"]https?:/]) assert.ok(!re.test(code), String(re));
});

test('every top-level name is unique across ALL runtime modules (strip-and-concatenate bundling) and the concatenation compiles', () => {
  const seen = new Map();
  for (const f of orderModules()) for (const n of topLevelNames(stripModule(f))) {
    assert.ok(!seen.has(n), `${n} is declared in ${seen.get(n)} and ${f}`);
    seen.set(n, f);
  }
  const { code } = concatModules();
  assert.doesNotThrow(() => new vm.Script(code));
  assert.ok(seen.has('bootAtlas') && seen.has('createApp') && seen.has('makeI18n'));
});

test('the concatenation defines everything and runs without a page when only the pure part is evaluated', () => {
  const { code } = concatModules();
  const sandbox = { out: null };
  vm.createContext(sandbox);
  vm.runInContext(`(function () { 'use strict';\n${code}\nout = { i18n: makeI18n({ 'en-US': { a: 'x {n}' } }, 'en-US').t('a', { n: 2 }), md: mdParse('# h').length, route: navParseRoute('#/2D?level=BD', ['2D']).tab }; })();`, sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.out)), { i18n: 'x 2', md: 1, route: '2D' });
});
