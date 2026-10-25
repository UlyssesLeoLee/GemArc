/**
 * Run the golden simulation cases through the JavaScript engine.
 *
 * Usage:
 *   node run-simulation.mjs <cases.json> [--case <id>]
 *
 * Prints one JSON object per case on stdout, so the Python side can compare
 * the result against the committed expectation field by field.
 */
import { readFile } from 'node:fs/promises';
import { compare, simulate } from '../sim/index.js';

const [, , casesPath, ...rest] = process.argv;
if (!casesPath) {
  process.stderr.write('usage: node run-simulation.mjs <cases.json> [--case <id>]\n');
  process.exit(2);
}

const onlyIndex = rest.indexOf('--case');
const only = onlyIndex >= 0 ? rest[onlyIndex + 1] : null;

const payload = JSON.parse(await readFile(casesPath, 'utf8'));
const results = [];

for (const testCase of payload.cases) {
  if (only && testCase.id !== only) continue;
  const baseline = simulate(testCase.model, testCase.scenarioId, []);
  const intervention = simulate(testCase.model, testCase.scenarioId, testCase.disabledNodeIds || []);
  const comparison = compare(testCase.model, testCase.scenarioId, testCase.disabledNodeIds || []);
  results.push({
    id: testCase.id,
    baseline,
    intervention,
    comparison: {
      status: comparison.status,
      scenarioId: comparison.scenarioId,
      lostEvents: comparison.lostEvents,
      gainedEvents: comparison.gainedEvents,
    },
  });
}

process.stdout.write(JSON.stringify(results));
