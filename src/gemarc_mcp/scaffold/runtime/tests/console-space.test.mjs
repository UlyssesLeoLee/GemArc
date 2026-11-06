/**
 * The crystal scene of the console's tool surface.
 *
 * The scene carries runtime facts and nothing else: one crystal per tool the
 * server really registered, the read ones aqua and the write ones topaz. The
 * layout has to be the same every time, and picking a crystal has to name the
 * tool it stands for.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CONSOLE_SPACE_COLORS,
  createConsoleSpace,
  layoutConsoleCrystals,
} from '../space/console-space.js';
import { createStubCanvas, createStubContainer, createStubThree } from './helpers/three-stub.mjs';

const TOOLS = [
  { name: 'gemarc_brief', write: false },
  { name: 'gemarc_validate', write: false },
  { name: 'gemarc_model', write: true },
];

test('one crystal per tool, in the order of the tool list', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  assert.equal(layout.length, 3);
  assert.deepEqual(layout.map((item) => item.name), ['gemarc_brief', 'gemarc_validate', 'gemarc_model']);
});

test('a read tool is aqua and a write tool is topaz', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  assert.equal(layout[0].color, CONSOLE_SPACE_COLORS.read);
  assert.equal(layout[1].color, CONSOLE_SPACE_COLORS.read);
  assert.equal(layout[2].color, CONSOLE_SPACE_COLORS.write);
});

test('the same tool list always gives the same picture', () => {
  assert.deepEqual(layoutConsoleCrystals(TOOLS), layoutConsoleCrystals(TOOLS));
});

test('no tool means no crystal: the scene never draws a placeholder', () => {
  assert.deepEqual(layoutConsoleCrystals([]), []);
  assert.deepEqual(layoutConsoleCrystals(null), []);
});

test('the crystals do not sit on top of each other', () => {
  const layout = layoutConsoleCrystals(TOOLS);
  for (let i = 0; i < layout.length; i += 1) {
    for (let j = i + 1; j < layout.length; j += 1) {
      const a = layout[i].position;
      const b = layout[j].position;
      const distance = Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
      assert.ok(distance > 1, `crystals ${i} and ${j} are on top of each other`);
    }
  }
});

test('the scene builds one crystal per tool and marks the selection', () => {
  const THREE = createStubThree();
  const canvas = createStubCanvas();
  const container = createStubContainer({ canvas });
  const scene = createConsoleSpace(THREE, { container, tools: TOOLS });

  assert.equal(scene.crystals.size, 3);
  const record = scene.crystals.get('gemarc_model');
  assert.ok(record);
  // The body is a toon material with the shared gradient map.
  assert.equal(record.material.gradientMap.width, 3);
  assert.equal(record.material.transparent, true);

  scene.setSelected('gemarc_model');
  assert.equal(record.edgeMaterial.opacity, 1);
  assert.equal(scene.crystals.get('gemarc_brief').edgeMaterial.opacity, 0.8);
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
