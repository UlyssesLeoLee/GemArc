// Test helper (not a test): builds a complete window.__ATLAS__ bundle (all ten keys) from a synthetic model through the project interpreter,
// with the real fragments, closures, diagram units, profile and the three interface tables, so the UI tests run on exactly the data shapes
// the build will embed. Also reads the interface tables. Synthetic ids only. Node is CI only; without a Python interpreter the tests that
// need a bundle skip loudly (they never pass silently).
import { spawnSync } from 'node:child_process';
import { accessSync, mkdtempSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
export const I18N_DIR = join(ROOT, 'src', 'gemarc_mcp', 'atlas', 'viewer', 'i18n');
export const LANGS = ['zh-CN', 'ja-JP', 'en-US'];

export function interpreter() {
  const venv = [join(ROOT, '.venv', 'Scripts', 'python.exe'), join(ROOT, '.venv', 'bin', 'python')].find((p) => { try { accessSync(p); return true; } catch { return false; } });
  for (const c of [process.env.GEMARC_PYTHON, venv, 'python', 'python3'].filter(Boolean)) {
    const probe = spawnSync(c, ['-c', 'import sys; print(sys.version_info[0])'], { encoding: 'utf8' });
    if (probe.status === 0 && probe.stdout.trim() === '3') return c;
  }
  return null;
}
export const PYTHON = interpreter();
export const skipNoPython = PYTHON ? false : 'no Python 3 interpreter found (set GEMARC_PYTHON); the bundle-based UI tests were NOT run';

export const tables = () => Object.fromEntries(LANGS.map((l) => [l, JSON.parse(readFileSync(join(I18N_DIR, l + '.json'), 'utf8'))]));
export const i18nFiles = () => readdirSync(I18N_DIR).filter((f) => f.endsWith('.json')).sort();

const DRIVER = [
  'import sys, json',
  "sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')",
  'from atlas import synth',
  'from gemarc_mcp.atlas import model as am',
  'from gemarc_mcp.atlas.derive import diagrams as D, docbuild as DB',
  'kind, seed, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]',
  "if kind == 'lending':",
  "    model = json.load(open('tests/atlas/fixtures/lending_desk.model.json', encoding='utf-8'))",
  "elif kind.startswith('edge:'):",
  '    model = synth.edge_case(kind[5:], seed)',
  'else:',
  '    model = synth.generate(kind, seed)',
  "profile = json.load(open('src/gemarc_mcp/atlas/profiles/software.json', encoding='utf-8'))",
  "tables = {l: json.load(open('src/gemarc_mcp/atlas/viewer/i18n/%s.json' % l, encoding='utf-8')) for l in ('zh-CN', 'ja-JP', 'en-US')}",
  "langs = list(model['meta'].get('languages') or ['en-US'])",
  'primary = langs[0]',
  'payload = DB.viewer_payload(model, primary, 20)',
  'sha = am.model_sha(model)',
  "meta = {'projectId': model['meta']['projectId'], 'title': model['meta'].get('title', ''), 'modelSha': sha, 'modelSha8': sha[:8],",
  "        'prefKey': 'gemarc-atlas:%s:%s' % (model['meta']['projectId'], sha[:8]), 'languages': langs, 'primaryLanguage': primary,",
  "        'profileId': profile['id'], 'mode': model['meta'].get('mode', 'asis'),",
  "        'counts': {'nodes': len(model['nodes']), 'edges': len(model['edges']), 'cases': len(model['cases']), 'interventions': len(model['interventions']),",
  "                   'requirements': len(model['requirements']), 'tbds': len(model['tbds']), 'sealedCases': 0, 'sealedPairs': 0}, 'warnings': []}",
  "units = D.build_units(model, primary)['units']",
  "bundle = {'schema': 'gemarc-atlas-bundle/1', 'meta': meta, 'profile': profile, 'model': model, 'i18n': {'languages': ['zh-CN', 'ja-JP', 'en-US'], 'tables': tables},",
  "          'fragments': payload['fragments'], 'closures': payload['closures'], 'runs': {'baselines': {}, 'pairs': []}, 'diagrams': units,",
  "          'calibrationHooks': {'docSelections': payload['docSelections'], 'traceLengths': [], 'scene': {'nodeCount': len(model['nodes']),",
  "                               'buildingIds': [b['id'] for b in model['space']['buildings']], 'unassignedCount': 0}}}",
  "json.dump(bundle, open(out, 'w', encoding='utf-8'), ensure_ascii=False)",
].join('\n');

const cache = new Map();
/** A fresh deep copy of the bundle of `kind` ('lending', 'tiny', 'small', 'reference', 'edge:<name>') and seed. */
export function makeBundle(kind, seed = 0) {
  const key = kind + '#' + seed;
  if (!cache.has(key)) {
    const dir = mkdtempSync(join(tmpdir(), 'atlas-ui-'));
    try {
      const out = join(dir, 'bundle.json');
      const run = spawnSync(PYTHON, ['-c', DRIVER, kind, String(seed), out], { cwd: ROOT, encoding: 'utf8', env: { ...process.env, PYTHONPATH: join(ROOT, 'src') }, maxBuffer: 1 << 26 });
      if (run.status !== 0) throw new Error('python driver failed: ' + run.stderr.slice(-600));
      cache.set(key, readFileSync(out, 'utf8'));
    } finally { rmSync(dir, { recursive: true, force: true }); }
  }
  return JSON.parse(cache.get(key));
}
