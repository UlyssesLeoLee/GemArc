/**
 * Building–Floor–Room space tests (v5.0 §6).
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/space.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { SpaceModel, SpaceNavigation, NAVIGATION_IDS, CROSS_SPACE_RELATION_IDS } from '../space/index.js';

/**
 * Two buildings, three floors, four rooms and five nodes.
 */
function buildFixture() {
  const model = {
    meta: { schemaVersion: '0.2.0', language: 'zh-CN', evidenceState: 'Observed' },
    nodes: [
      { id: 'a1', kind: 'Service', name: 'A1', verification: 'Implemented' },
      { id: 'a2', kind: 'Module', name: 'A2', verification: 'Implemented' },
      { id: 'a3', kind: 'Module', name: 'A3', verification: 'Designed' },
      { id: 'a4', kind: 'Module', name: 'A4', verification: 'Designed' },
      { id: 'a5', kind: 'Service', name: 'A5', verification: 'Inferred' },
    ],
    space: {
      buildings: [
        { id: 'b1', name: 'B1', kind: '系统', semanticSource: '系统边界定义', nodeIds: [] },
        { id: 'b2', name: 'B2', kind: '信任边界', semanticSource: '部署清单', nodeIds: [] },
      ],
      floors: [
        { id: 'f1', buildingId: 'b1', name: 'F1', kind: '应用层', semanticSource: '架构层定义', nodeIds: [] },
        { id: 'f2', buildingId: 'b1', name: 'F2', kind: '领域层', semanticSource: '架构层定义', nodeIds: [] },
        { id: 'f3', buildingId: 'b2', name: 'F3', kind: '基础设施层', semanticSource: '部署清单', nodeIds: [] },
      ],
      rooms: [
        { id: 'r1', floorId: 'f1', name: 'R1', kind: '模块组', semanticSource: '包清单', nodeIds: ['a1', 'a2'] },
        { id: 'r2', floorId: 'f1', name: 'R2', kind: '模块组', semanticSource: '包清单', nodeIds: ['a3'] },
        { id: 'r3', floorId: 'f2', name: 'R3', kind: '子域', semanticSource: '限界上下文清单', nodeIds: ['a4'] },
        { id: 'r4', floorId: 'f3', name: 'R4', kind: '服务簇', semanticSource: '部署清单', nodeIds: ['a5'] },
      ],
    },
  };
  return { model, space: new SpaceModel(model) };
}

test('building -> floor -> room nesting is readable', () => {
  const { space } = buildFixture();

  assert.equal(space.container('b1').level, 'Building');
  assert.equal(space.container('f1').level, 'Floor');
  assert.equal(space.container('r1').level, 'Room');
  assert.equal(space.container('nope'), null);

  assert.deepEqual(space.floorsOf('b1').map((floor) => floor.id), ['f1', 'f2']);
  assert.deepEqual(space.roomsOf('f1').map((room) => room.id), ['r1', 'r2']);

  assert.deepEqual(space.nodeIdsOf('r1'), ['a1', 'a2']);
  assert.deepEqual(space.nodeIdsOf('f1'), ['a1', 'a2', 'a3']);
  assert.deepEqual(space.nodeIdsOf('b1'), ['a1', 'a2', 'a3', 'a4']);

  assert.equal(space.roomOfNode('a1'), 'r1');
  assert.equal(space.floorOfNode('a1'), 'f1');
  assert.equal(space.buildingOfNode('a1'), 'b1');
});

test('a dangling building reference is detected, not ignored', () => {
  const { model } = buildFixture();
  model.space.floors[0].buildingId = 'missing-building';
  const space = new SpaceModel(model);

  const diagnostics = space.validate();
  const dangling = diagnostics.filter((item) => item.id === 'GEMARC_SPACE_001');
  assert.ok(dangling.length >= 1);
  assert.equal(dangling[0].entityId, 'f1');
  assert.throws(() => space.assertValid(), (error) => error.code === 'GEMARC_SPACE_001');
});

test('a dangling room reference and a missing semantic source are reported', () => {
  const { model } = buildFixture();
  model.space.rooms[0].floorId = 'missing-floor';
  delete model.space.rooms[1].semanticSource;
  const space = new SpaceModel(model);

  const diagnostics = space.validate();
  assert.ok(diagnostics.some((item) => item.id === 'GEMARC_SPACE_001' && item.entityId === 'r1'));
  assert.ok(diagnostics.some((item) => item.id === 'GEMARC_SPACE_002' && item.entityId === 'r2'));
});

test('cross space relations are classified by level', () => {
  const { space } = buildFixture();

  assert.deepEqual(CROSS_SPACE_RELATION_IDS, ['same-room', 'cross-room', 'cross-floor', 'cross-building']);
  assert.equal(space.classifyRelation('a1', 'a2'), 'same-room');
  assert.equal(space.classifyRelation('a1', 'a3'), 'cross-room');
  assert.equal(space.classifyRelation('a1', 'a4'), 'cross-floor');
  assert.equal(space.classifyRelation('a1', 'a5'), 'cross-building');

  const pairs = space.crossSpaceRelations(['a1', 'a2', 'a3', 'a4', 'a5']);
  assert.equal(pairs.length, 10);
  assert.ok(pairs.every((pair) => CROSS_SPACE_RELATION_IDS.includes(pair.relation)));
});

test('all eight navigations are reachable and back-to-overview resets the state', () => {
  const { space } = buildFixture();
  const navigation = new SpaceNavigation({ space });

  assert.deepEqual([...NAVIGATION_IDS], [
    'overview',
    'building-select',
    'floor-switch',
    'floor-exploded',
    'room-focus',
    'room-cutaway',
    'node-focus',
    'back-to-overview',
  ]);

  assert.equal(navigation.state.navigation, 'overview');
  assert.equal(navigation.state.buildingId, null);

  navigation.navigate('building-select', { buildingId: 'b1' });
  assert.equal(navigation.state.navigation, 'building-select');
  assert.equal(navigation.state.buildingId, 'b1');

  navigation.navigate('floor-switch', { floorId: 'f1' });
  assert.equal(navigation.state.navigation, 'floor-switch');
  assert.equal(navigation.state.floorId, 'f1');

  navigation.navigate('floor-exploded', { floorId: 'f1' });
  assert.equal(navigation.state.navigation, 'floor-exploded');

  navigation.navigate('room-focus', { roomId: 'r1' });
  assert.equal(navigation.state.navigation, 'room-focus');
  assert.equal(navigation.state.roomId, 'r1');

  navigation.navigate('room-cutaway', { roomId: 'r1' });
  assert.equal(navigation.state.navigation, 'room-cutaway');

  navigation.navigate('node-focus', { nodeId: 'a1' });
  assert.equal(navigation.state.navigation, 'node-focus');
  assert.equal(navigation.state.nodeId, 'a1');

  const reset = navigation.navigate('back-to-overview');
  assert.equal(reset.navigation, 'overview');
  assert.equal(reset.buildingId, null);
  assert.equal(reset.floorId, null);
  assert.equal(reset.roomId, null);
  assert.equal(reset.nodeId, null);
});

test('illegal navigations and dangling targets are rejected', () => {
  const { space } = buildFixture();
  const navigation = new SpaceNavigation({ space });

  assert.throws(
    () => navigation.navigate('floor-switch', { floorId: 'f1' }),
    (error) => error.code === 'GEMARC_SPACE_003',
  );
  assert.throws(
    () => navigation.navigate('roof-view'),
    (error) => error.code === 'GEMARC_SPACE_003',
  );

  navigation.navigate('building-select', { buildingId: 'b1' });
  assert.throws(
    () => navigation.navigate('floor-switch', { floorId: 'missing' }),
    (error) => error.code === 'GEMARC_SPACE_001',
  );

  // A floor of another building may not be entered while that building is selected.
  navigation.navigate('floor-switch', { floorId: 'f1' });
  assert.throws(
    () => navigation.navigate('room-focus', { roomId: 'r4' }),
    (error) => error.code === 'GEMARC_SPACE_002',
  );
});
