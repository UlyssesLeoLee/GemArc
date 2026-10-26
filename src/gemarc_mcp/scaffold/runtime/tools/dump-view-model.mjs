/**
 * Dump the view models of a built site as JSON.
 *
 * The view models are the presentation layer of the site: they decide what is
 * shown, not what the system does. This tool turns them into a comparable
 * structure so a regression can be caught by diffing a committed baseline
 * against the current output.
 *
 * Usage:
 *   node dump-view-model.mjs <siteRoot> [modelFile]
 *
 * <siteRoot> must be a built site (the directory that holds index.html and
 * view-model.js). modelFile defaults to <siteRoot>/data/architecture.json.
 */
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const [, , siteRoot, modelFile] = process.argv;
if (!siteRoot) {
  process.stderr.write('usage: node dump-view-model.mjs <siteRoot> [modelFile]\n');
  process.exit(2);
}

const root = resolve(siteRoot);
const modelPath = resolve(modelFile || `${root}/data/architecture.json`);

const load = (relative) => import(pathToFileURL(resolve(root, relative)).href);

const models = await load('view-model.js');
const { normalizeModel } = await load('runtime/core/model.js');
const { GraphStore } = await load('runtime/graph/graph-store.js');
const { GraphQueryEngine } = await load('runtime/graph/graph-query.js');
const { ViewState } = await load('runtime/view/view-state.js');
const { SpaceModel } = await load('runtime/space/space-model.js');

const model = normalizeModel(JSON.parse(await readFile(modelPath, 'utf8')));
const store = new GraphStore(model);
const query = new GraphQueryEngine(store);
const viewState = new ViewState();
viewState.attachStore(store);
const spaceModel = new SpaceModel(model);
const scenarios = model.scenarios || [];
const scenario = scenarios.length ? scenarios[0].id : null;
const disabled = [];

const output = {
  facts: models.buildFactsView(model, store),
  graph: models.buildGraphView(model, viewState),
  space: models.buildSpaceView(model, spaceModel),
  timeline: models.buildTimelineView(model, scenario, disabled, 0),
  causal: models.buildCausalView(model, scenario, disabled),
  views: models.buildViewsView(model),
  workflow: models.buildWorkflowView(model),
  inspector: models.buildInspector(model, store, query, { nodeIds: [], edgeIds: [] }),
};

process.stdout.write(JSON.stringify(output, null, 2));
