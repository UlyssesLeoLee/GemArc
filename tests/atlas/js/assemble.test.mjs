// Python-vs-JavaScript twin check of the 1D document assembly (design 9.1, M3 acceptance (c)). Node is CI only, never a product dependency.
//
// A small Python driver (run through the project interpreter) builds the docset of a model with derive/docbuild.py and writes it together
// with the selections and the SHA-256 / byte length / FNV-1a of the text Python assembled for each of them. This test assembles every
// selection with viewer/src/assemble.js from the same docset JSON and demands the same bytes. Selections: the empty one, unknown ids,
// repeated and reversed ids beyond the six the target line lists, the whole model at three levels, every requirement alone at every
// level, and 50 seeded random selections. Models: the lending-desk fixture, the reference-scale synthetic (three seeds) and two synthetic
// edge cases (CJK text, odd ids), each in a different language.
//
// Run: node --test tests/atlas/js/assemble.test.mjs   (the interpreter is found through GEMARC_PYTHON, then .venv, then PATH)

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { accessSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as AS from '../../../src/gemarc_mcp/atlas/viewer/src/assemble.js';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const DRIVER = [
  'import sys, json',
  "sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')",
  'from atlas import synth',
  'from gemarc_mcp.atlas.derive import docbuild as B',
  'kind, lang, out = sys.argv[1:4]',
  "if kind == 'lending':",
  "    model = json.load(open('tests/atlas/fixtures/lending_desk.model.json', encoding='utf-8'))",
  "elif kind.startswith('ref'):",
  "    model = synth.generate('reference', int(kind[3:] or 7))",
  'else:',
  '    model = synth.edge_case(kind, 3)',
  "json.dump(B.twin_payload(model, lang, 50, 1), open(out, 'w', encoding='utf-8'), ensure_ascii=False)",
].join('\n');

function interpreter() {
  const venv = [join(ROOT, '.venv', 'Scripts', 'python.exe'), join(ROOT, '.venv', 'bin', 'python')].find((p) => { try { accessSync(p); return true; } catch { return false; } });
  const candidates = [process.env.GEMARC_PYTHON, venv, 'python', 'python3'].filter(Boolean);
  for (const c of candidates) {
    const probe = spawnSync(c, ['-c', 'import sys; print(sys.version_info[0])'], { encoding: 'utf8' });
    if (probe.status === 0 && probe.stdout.trim() === '3') return c;
  }
  return null;
}

const PYTHON = interpreter();
const SCENARIOS = [['lending', 'en-US'], ['ref7', 'zh-CN'], ['ref3', 'en-US'], ['ref11', 'ja-JP'], ['cjk', 'zh-CN'], ['odd-ids', 'en-US']];

function payload(kind, lang) {
  const dir = mkdtempSync(join(tmpdir(), 'atlas-twin-'));
  try {
    const out = join(dir, 'twin.json');
    const run = spawnSync(PYTHON, ['-c', DRIVER, kind, lang, out], { cwd: ROOT, encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'src') }, maxBuffer: 1 << 26 });
    assert.equal(run.status, 0, `python driver failed: ${run.stderr.slice(-600)}`);
    return JSON.parse(readFileSync(out, 'utf8'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

test('FNV-1a known vectors', () => {
  assert.equal(AS.hash32Hex(''), '811c9dc5');
  assert.equal(AS.hash32Hex('a'), 'e40c292c');
  assert.equal(AS.hash32Hex('foobar'), 'bf9cf968');
  assert.equal(AS.hash32Hex('é中'), AS.hash32Hex(Buffer.from('é中', 'utf8').toString('utf8')));
});

test('unknown level throws, unknown ids are ignored', () => {
  const ds = { fragments: { items: [], frame: { REQ: { head: 'H', chapters: [] } }, groups: {}, always: [], elements: [], unselected: 'none', modelSha8: '00000000' }, closures: {} };
  assert.throws(() => AS.assemble(ds, 'XX', []), /unknown level/);
  assert.equal(AS.assemble(ds, 'REQ', ['ghost']), 'H\n');
});

for (const [kind, lang] of SCENARIOS) {
  test(`JS assemble equals Python assemble byte for byte: ${kind} (${lang})`, (t) => {
    if (!PYTHON) { t.skip('no Python 3 interpreter found (set GEMARC_PYTHON)'); return; }
    const data = payload(kind, lang);
    const ds = data.docset;
    assert.ok(data.cases.length >= 56, `only ${data.cases.length} cases`);
    let checked = 0;
    for (const c of data.cases) {
      const text = AS.assemble(ds, c.level, c.ids);
      const bytes = Buffer.from(text, 'utf8');
      assert.equal(bytes.length, c.bytes, `${c.level} ${JSON.stringify(c.ids).slice(0, 80)}: length`);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), c.sha256, `${c.level} ${JSON.stringify(c.ids).slice(0, 80)}: bytes differ`);
      assert.equal(AS.hash32Hex(text), c.hash32, `${c.level}: hash32`);
      assert.ok(!text.includes('\u0001'), 'placeholder left in the text');
      checked += 1;
    }
    if (kind.startsWith('ref')) assert.ok(checked >= 100, `${checked} cases`);
  });
}
