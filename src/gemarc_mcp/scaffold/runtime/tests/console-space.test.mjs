/**
 * The crystal scene of the console's tool surface.
 *
 * The scene carries runtime facts and nothing else: one crystal per tool the
 * server really registered, the read ones blue and the write ones persimmon.
 * The layout has to be the same every time, and picking a crystal has to name
 * the tool it stands for.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CONSOLE_SPACE_COLORS,
  createConsoleSpace,
  layoutConsoleCrystals,
} from '../space/console-space.js';
import { PALETTE } from '../space/gem-material.js';
import { createStubCanvas, createStubContainer, createStubThree } from './helpers/three-stub.mjs';

const TOOLS = [
  { name: 'gemarc_brief', write: false },
  { name: 'gemarc_validate', write: false },
  { name: 'gemarc_model', write: true },
];

test('one crystal per tool, in the order of the tool list', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  assert.equal(layout.nodes.length, 3);
  assert.deepEqual(layout.nodes.map((item) => item.name),
    ['gemarc_brief', 'gemarc_validate', 'gemarc_model']);
});

test('a read tool is blue and a write tool is persimmon', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });
  // The colour is a runtime fact: read or write (BD-010 section 12.4).
  assert.equal(scene.crystals.get('gemarc_brief').material.attenuationColor.getHex(),
    CONSOLE_SPACE_COLORS.read);
  assert.equal(scene.crystals.get('gemarc_validate').material.attenuationColor.getHex(),
    CONSOLE_SPACE_COLORS.read);
  assert.equal(scene.crystals.get('gemarc_model').material.attenuationColor.getHex(),
    CONSOLE_SPACE_COLORS.write);
});

test('the same tool list always gives the same picture', () => {
  assert.deepEqual(layoutConsoleCrystals(TOOLS), layoutConsoleCrystals(TOOLS));
});

test('no tool means no crystal: the scene never draws a placeholder', () => {
  assert.deepEqual(layoutConsoleCrystals([]).nodes, []);
  assert.deepEqual(layoutConsoleCrystals(null).nodes, []);
  assert.deepEqual(layoutConsoleCrystals([]).links, []);
});

test('the crystals do not sit on top of each other', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  for (let i = 0; i < layout.nodes.length; i += 1) {
    for (let j = i + 1; j < layout.nodes.length; j += 1) {
      const a = layout.nodes[i].position;
      const b = layout.nodes[j].position;
      const distance = Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
      assert.ok(distance > 1, `crystals ${i} and ${j} are on top of each other`);
    }
  }
});

test('the scene builds one stone per tool and marks the selection', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });

  assert.equal(scene.crystals.size, 3);
  const record = scene.crystals.get('gemarc_model');
  assert.ok(record);
  // The body is a real stone (BD-010 section 13.1): it is transparent
  // because the light bends on the way in, not because it is painted pale.
  assert.equal(record.material.transmission, 0.9,
    'the stone does not let the light through');
  assert.equal(record.material.clearcoat, 1,
    'the stone has no polished coat: the cut faces lose their glint');
  assert.equal(record.material.metalness, 0, 'a stone is not a metal');
  assert.ok(record.material.roughness <= 0.05,
    `the stone is too rough (${record.material.roughness}): it would read as plastic`);
  assert.ok(record.material.ior >= 2.0 && record.material.ior <= 2.4,
    `the index of refraction ${record.material.ior} is outside the range of the policy`);
  assert.ok(record.material.dispersion > 0,
    'the stone does not split the light: there is no colour at its edges');
  // One normal per cut face: the edge between two faces stays hard.
  assert.equal(record.material.flatShading, true,
    'the stone is smooth: the border between two cut faces would blur');
  // The inner cut faces are seen through the stone.
  assert.equal(record.material.side, THREE.DoubleSide);
  // The type colour is in the stone, not on it (BD-010 section 4.7).
  assert.equal(record.material.attenuationColor.getHex(), CONSOLE_SPACE_COLORS.write,
    'the stone does not carry the colour of its type');
  assert.equal(record.material.color.getHex(), 0xffffff,
    'the surface of the stone is painted: a stone is not painted');

  scene.setSelected('gemarc_model');
  // The selection is carried by the ink line, which grows one step.
  assert.equal(record.outline.material.opacity, 1);
  assert.ok(
    record.outline.userData.outlinePixels > scene.crystals.get('gemarc_brief').outline.userData.outlinePixels,
    'the selected crystal carries the heavier ink line');
});

test('every stone is a real gem with an ink line around its outside', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });

  for (const [name, record] of scene.crystals) {
    // The ink line is a hairline around the outside of the stone; the inner
    // edges are left to the refraction (BD-010 section 4.8).
    assert.ok(record.outline, `the crystal ${name} has no ink line`);
    assert.equal(record.outline.geometry, record.mesh.geometry,
      'the ink line is not on the geometry of the stone');
    // Only the back of the shell is drawn, so it never covers the stone.
    assert.equal(record.outline.material.side, THREE.BackSide,
      'the ink line is drawn over the stone');
    // The line never hides what is behind the stone.
    assert.equal(record.outline.material.depthWrite, false,
      'the ink line writes depth: what is behind the stone would be hidden');
    // The ink is ink: it is never tone mapped into another colour.
    assert.equal(record.outline.material.toneMapped, false,
      'the ink line is tone mapped: the ink would not be the ink');
    assert.equal(record.outline.material.color.getHex(), PALETTE.ink,
      'the ink line is not the ink of the palette');
  }
});

test('the cut of a stone says which stage of the product the tool is in', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });

  const entry = scene.crystals.get('gemarc_brief');
  const verify = scene.crystals.get('gemarc_validate');
  const produce = scene.crystals.get('gemarc_model');
  assert.ok(entry && verify && produce);
  // The three tools stand in three different stages of the product's own
  // workflow, and every stage has its own cut (BD-010 section 12.4 and 13.6).
  const cuts = new Set([entry, verify, produce].map((record) => record.mesh.geometry.parameters.segments));
  assert.equal(cuts.size, 3, 'the stages are cut the same way');
});

/* --- the architecture of the product (BD-010 section 12.4, 13.6) -------- */

test('the tools stand in the stages of the product, not in a spiral', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  const stages = layout.nodes.map((node) => node.stage);
  assert.deepEqual(stages, ['entry', 'verify', 'produce']);
  // The stages stand in a row: the work flows from one to the next.
  const xs = layout.nodes.map((node) => node.position.x);
  assert.ok(xs[0] < xs[1] && xs[1] < xs[2], 'the stages do not stand in a row');
});

test('one arrow joins every pair of neighbouring stages that have stones', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  assert.equal(layout.links.length, 2);
  assert.deepEqual(layout.links.map((link) => [link.fromStage, link.toStage]),
    [['entry', 'verify'], ['verify', 'produce']]);
});

test('a stage without a stone gets no arrow', () => {
  const layout = layoutConsoleCrystals([{ name: 'gemarc_brief', write: false }]);
  assert.equal(layout.nodes.length, 1);
  assert.equal(layout.links.length, 0, 'an arrow points at a stage that is not drawn');
});

test('the whole row of stages stays inside the frame', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  // The proportions of the console's surface panel: wide and short. The row
  // is long, so the near end of it is the one that leaves the frame first.
  container.clientWidth = 880;
  container.clientHeight = 292;
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });
  const layout = layoutConsoleCrystals(TOOLS);
  const size = 3.4;

  let widest = 0;
  for (const node of layout.nodes) {
    for (const dx of [-size, size]) {
      for (const dy of [-size, size]) {
        const point = new THREE.Vector3(
          node.position.x + dx,
          node.position.y + dy,
          node.position.z,
        );
        point.project(scene.camera);
        assert.ok(Math.abs(point.x) <= 1,
          `the stone ${node.name} is cut off at the side of the frame`);
        assert.ok(Math.abs(point.y) <= 1,
          `the stone ${node.name} is cut off at the top of the frame`);
        widest = Math.max(widest, Math.abs(point.x));
      }
    }
  }
  // The row fills the picture: a fit that ran away would leave the stones
  // as a line of dust in the middle of the panel.
  assert.ok(widest > 0.6,
    `the row only takes ${widest.toFixed(2)} of the frame: it is too far away`);
});

test('the focus key frames the row the way the console shows it', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  container.clientWidth = 880;
  container.clientHeight = 292;
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });
  const shown = scene.controls.distance;

  // Unity's F frames the scene. The console has to land on the same
  // distance the console already shows.
  scene.controls.frameSelection();
  assert.ok(Math.abs(scene.controls.distance - shown) < 1e-6,
    'the focus key pulls the camera away from the picture of the console');
});

test('the scene draws the arrows and a bead walks along them', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  let clock = 0;
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS, now: () => clock });

  assert.equal(scene.arrows.length, 2, 'the arrows of the stages are missing');
  const arrow = scene.arrows[0];
  assert.ok(arrow.shaft.geometry, 'the arrow has no shaft');
  assert.ok(arrow.head.geometry, 'the arrow has no head');
  const placeBefore = { ...arrow.pulse.position };
  clock = 1.3;
  scene.tick();
  assert.notDeepEqual({ ...arrow.pulse.position }, placeBefore,
    'the bead does not walk along the arrow');
  assert.equal(arrow.pulse.visible, true, 'the bead is hidden');
});

test('the whole arrow is a transparent line, never an opaque body', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });
  const arrow = scene.arrows[0];

  // An opaque object is drawn into the transmission buffer of the scene, and
  // a stone would then refract the head of its own arrow: the gem turns
  // (BD-010 section 4.9). The arrow is a line, so it is drawn transparent.
  for (const part of [arrow.shaft, arrow.head, arrow.pulse]) {
    assert.equal(part.material.transparent, true,
      'the arrow has an opaque part: it would darken the stones');
  }
});

test('a reader who asks for less motion gets still arrows', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  let clock = 0;
  const scene = createConsoleSpace(THREE, {
    container, tools: TOOLS, now: () => clock, lessMotion: true,
  });
  const arrow = scene.arrows[0];
  const placeBefore = { ...arrow.pulse.position };
  clock = 4;
  scene.tick();
  assert.deepEqual({ ...arrow.pulse.position }, placeBefore,
    'the bead walks although the reader asked for less motion');
  assert.equal(arrow.pulse.visible, false, 'the bead is visible although it does not walk');
});

test('picking a crystal names the tool it stands for', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const picked = [];
  const scene = createConsoleSpace(THREE, {
    container,
    tools: TOOLS,
    onSelect: (name) => picked.push(name),
  });

  const record = scene.crystals.get('gemarc_brief');
  const world = record.mesh.worldPosition();
  const point = new THREE.Vector3(world.x, world.y, world.z);
  point.project(scene.camera);
  const rect = canvas.getBoundingClientRect();
  const clientX = rect.left + (point.x * 0.5 + 0.5) * rect.width;
  const clientY = rect.top + (-point.y * 0.5 + 0.5) * rect.height;

  canvas.dispatch('pointerdown', { button: 0, clientX, clientY });
  canvas.dispatch('pointerup', { button: 0, clientX, clientY });
  assert.deepEqual(picked, ['gemarc_brief']);
});

test('a click on empty space clears the selection', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const picked = [];
  const scene = createConsoleSpace(THREE, {
    container,
    tools: TOOLS,
    onSelect: (name) => picked.push(name),
  });
  scene.setSelected('gemarc_model');
  // The top right corner of the canvas is empty space.
  canvas.dispatch('pointerdown', { button: 0, clientX: 795, clientY: 5 });
  canvas.dispatch('pointerup', { button: 0, clientX: 795, clientY: 5 });
  assert.deepEqual(picked, []);
});

test('a scene without a container says so instead of drawing nothing', () => {
  const THREE = createStubThree();
  assert.throws(() => createConsoleSpace(THREE, { container: null }), /容器/);
  assert.throws(() => createConsoleSpace(null, { container: {} }), /THREE/);
});

/* --- the motion of the tool surface (BD-010 section 12.4) --------------- */

test('the crystals turn slowly and breathe', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  let clock = 0;
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS, now: () => clock });
  const record = scene.crystals.get('gemarc_brief');

  const turnBefore = record.mesh.rotation.y;
  const lightBefore = record.material.emissiveIntensity;
  clock = 3;
  scene.tick();

  assert.notEqual(record.mesh.rotation.y, turnBefore, 'the crystal does not turn');
  assert.notEqual(record.material.emissiveIntensity, lightBefore, 'the crystal does not breathe');
  // A slow turn: three seconds is a small part of one turn.
  assert.ok(Math.abs(record.mesh.rotation.y) < Math.PI, 'the crystal spins too fast');
  // The self-light stays inside the band of the policy.
  assert.ok(record.material.emissiveIntensity > 0, 'the crystal is dark');
  assert.ok(record.material.emissiveIntensity < 0.5, 'the crystal is too bright');
});

test('the motion never moves a crystal away from its place', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  let clock = 0;
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS, now: () => clock });
  const record = scene.crystals.get('gemarc_model');
  const place = { ...record.mesh.position };
  const colour = record.material.color;

  clock = 17;
  scene.tick();

  assert.deepEqual({ ...record.mesh.position }, place, 'the crystal left its place');
  assert.equal(record.material.color, colour, 'the crystal changed its colour');
});

test('a reader who asks for less motion gets a still scene', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  let clock = 0;
  const scene = createConsoleSpace(THREE, {
    container, tools: TOOLS, now: () => clock, lessMotion: true,
  });
  const record = scene.crystals.get('gemarc_brief');
  const turnBefore = record.mesh.rotation.y;
  const lightBefore = record.material.emissiveIntensity;

  clock = 9;
  scene.tick();

  assert.equal(scene.lessMotion, true);
  assert.equal(record.mesh.rotation.y, turnBefore, 'the crystal turns although it should not');
  assert.equal(record.material.emissiveIntensity, lightBefore, 'the crystal breathes although it should not');
});
