// Boot: read the embedded bundle, register the tabs, render once, publish window.AtlasApp and, in gate mode, hand the app's gate
// configuration to the walker that the gate self-test script provides (the walker is not part of the viewer).
//
// To add a tab: write its module and add the definition to TAB_DEFINITIONS after the reserved slot of the same id (a later definition
// replaces an earlier one). No other file changes.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

import { createApp } from './shell.js';
import { domReadSlot, domExpose, domSlotArray } from './dom.js';
import { boundaryRecord } from './boundary.js';
import { DOC_TAB } from './tab-doc.js';
import { TWOD_TAB } from './tab-twod.js';
import { QA_TAB } from './tab-qa.js';
import { STAGE_TAB_3D, STAGE_TAB_4D, STAGE_TAB_5D, STAGE_SIDE_TABS } from './tab-slots.js';

/** Registration order = tab order. The reserved slots of viewer-A are replaced by the stage tabs of tab-slots.js (viewer-B). */
export const TAB_DEFINITIONS = [DOC_TAB, TWOD_TAB, STAGE_TAB_3D, STAGE_TAB_4D, STAGE_TAB_5D, QA_TAB];
export const SIDE_TAB_DEFINITIONS = [...STAGE_SIDE_TABS];

/** Start the viewer. Never throws: a failure is recorded in window.__ATLAS_ERRORS__ and the page stays up. */
export function bootAtlas() {
  try {
    const app = createApp(domReadSlot('__ATLAS__'), { tabs: TAB_DEFINITIONS, sideTabs: SIDE_TAB_DEFINITIONS });
    domExpose('AtlasApp', app);
    app.renderOnce();
    const gate = domReadSlot('AtlasGate');
    if (gate && gate.active && typeof gate.run === 'function') gate.run(app.gateConfig());
    return app;
  } catch (e) {
    domSlotArray('__ATLAS_ERRORS__').push(boundaryRecord('boot', e));
    return null;
  }
}
