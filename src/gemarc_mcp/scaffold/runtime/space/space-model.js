/**
 * Building–Floor–Room engineering space (Universal 5D GemArc Web v5.0 §6).
 *
 * Hierarchy: `System -> Building -> Floor -> Room -> {Node, Node, Edge}`.
 *
 * A building may stand for a system, a subsystem, a deployment domain, an
 * application cluster, a trust boundary or a bounded-context group. A floor
 * may stand for an architecture / runtime / access / application / domain /
 * data / infrastructure layer. A room may stand for a module group, a package,
 * a service cluster, a subdomain, a component group, a data domain or a
 * UML/ER subgraph.
 *
 * Two rules are enforced here:
 *
 *   1. The three levels are a *reading-oriented* spatial organisation. Every
 *      container keeps its `semanticSource`, so a virtual room can never
 *      masquerade as a real code module or a physical deployment facility.
 *   2. Dangling references are detected and reported, never ignored: a floor
 *      whose `buildingId` does not exist is a `GEMARC_SPACE_001` diagnostic.
 */
import { diagnostic, GemArcError } from '../core/errors.js';
import { SPACE_LEVEL_IDS, VOCAB } from '../core/vocab.js';

export { SPACE_LEVEL_IDS };

/**
 * The four cross-space relation classes.
 * @type {readonly string[]}
 */
export const CROSS_SPACE_RELATION_IDS = Object.freeze([
  ...VOCAB['space-levels'].crossSpaceRelations,
]);

const SAME_ROOM = 'same-room';
const CROSS_ROOM = 'cross-room';
const CROSS_FLOOR = 'cross-floor';
const CROSS_BUILDING = 'cross-building';

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

/**
 * The Building–Floor–Room space of a canonical model.
 */
export class SpaceModel {
  /**
   * @param {object} model a canonical model (`{space, nodes}`) or a bare space
   */
  constructor(model = {}) {
    const source = model && typeof model === 'object' ? model : {};
    const space = Array.isArray(source.buildings)
      ? source
      : (source.space && typeof source.space === 'object' ? source.space : {});

    /** @type {Map<string, object>} */
    this.buildings = new Map();
    /** @type {Map<string, object>} */
    this.floors = new Map();
    /** @type {Map<string, object>} */
    this.rooms = new Map();
    /** @type {Map<string, object>} */
    this.nodes = new Map();

    const nodes = source.nodes instanceof Map
      ? [...source.nodes.values()]
      : asArray(source.nodes);
    for (const node of nodes) {
      if (node && node.id) this.nodes.set(node.id, node);
    }
    for (const building of asArray(space.buildings)) {
      this.buildings.set(building.id, { ...building, level: 'Building' });
    }
    for (const floor of asArray(space.floors)) {
      this.floors.set(floor.id, { ...floor, level: 'Floor' });
    }
    for (const room of asArray(space.rooms)) {
      this.rooms.set(room.id, { ...room, level: 'Room' });
    }
  }

  /**
   * Build a space model from a canonical model.
   * @param {object} model
   * @returns {SpaceModel}
   */
  static fromModel(model) {
    return new SpaceModel(model);
  }

  /**
   * A building by id.
   * @param {string} id
   * @returns {object|null}
   */
  building(id) {
    return this.buildings.get(id) || null;
  }

  /**
   * A floor by id.
   * @param {string} id
   * @returns {object|null}
   */
  floor(id) {
    return this.floors.get(id) || null;
  }

  /**
   * A room by id.
   * @param {string} id
   * @returns {object|null}
   */
  room(id) {
    return this.rooms.get(id) || null;
  }

  /**
   * Any space container by id.
   * @param {string} id
   * @returns {{level: string, entity: object}|null}
   */
  container(id) {
    if (this.buildings.has(id)) return { level: 'Building', entity: this.buildings.get(id) };
    if (this.floors.has(id)) return { level: 'Floor', entity: this.floors.get(id) };
    if (this.rooms.has(id)) return { level: 'Room', entity: this.rooms.get(id) };
    return null;
  }

  /**
   * The floors of a building.
   * @param {string} buildingId
   * @returns {object[]}
   */
  floorsOf(buildingId) {
    return [...this.floors.values()].filter((floor) => floor.buildingId === buildingId);
  }

  /**
   * The rooms of a floor.
   * @param {string} floorId
   * @returns {object[]}
   */
  roomsOf(floorId) {
    return [...this.rooms.values()].filter((room) => room.floorId === floorId);
  }

  /**
   * Every node id a container holds, including the nodes of its children.
   * @param {string} containerId building, floor or room id
   * @returns {string[]}
   */
  nodeIdsOf(containerId) {
    const room = this.rooms.get(containerId);
    if (room) return [...new Set(asArray(room.nodeIds))];
    const floor = this.floors.get(containerId);
    if (floor) {
      const ids = new Set(asArray(floor.nodeIds));
      for (const child of this.roomsOf(floor.id)) {
        for (const id of asArray(child.nodeIds)) ids.add(id);
      }
      return [...ids];
    }
    const building = this.buildings.get(containerId);
    if (building) {
      const ids = new Set(asArray(building.nodeIds));
      for (const child of this.floorsOf(building.id)) {
        for (const id of this.nodeIdsOf(child.id)) ids.add(id);
      }
      return [...ids];
    }
    return [];
  }

  /**
   * The room a node belongs to. The explicit `spaceRef` wins over the
   * container membership.
   * @param {string} nodeId
   * @returns {string|null}
   */
  roomOfNode(nodeId) {
    const node = this.nodes.get(nodeId);
    const ref = node?.spaceRef || null;
    if (ref?.roomId && this.rooms.has(ref.roomId)) return ref.roomId;
    for (const room of this.rooms.values()) {
      if (asArray(room.nodeIds).includes(nodeId)) return room.id;
    }
    return null;
  }

  /**
   * The floor a node belongs to.
   * @param {string} nodeId
   * @returns {string|null}
   */
  floorOfNode(nodeId) {
    return this.locationOf(nodeId).floorId;
  }

  /**
   * The building a node belongs to.
   * @param {string} nodeId
   * @returns {string|null}
   */
  buildingOfNode(nodeId) {
    return this.locationOf(nodeId).buildingId;
  }

  /**
   * The spatial location of a node.
   * @param {string} nodeId
   * @returns {{buildingId: string|null, floorId: string|null, roomId: string|null}}
   */
  locationOf(nodeId) {
    const node = this.nodes.get(nodeId);
    const ref = node?.spaceRef || {};
    const roomId = this.roomOfNode(nodeId);
    const floorId = ref.floorId && this.floors.has(ref.floorId)
      ? ref.floorId
      : (roomId ? this.rooms.get(roomId).floorId || null : null);
    const buildingId = ref.buildingId && this.buildings.has(ref.buildingId)
      ? ref.buildingId
      : (floorId ? this.floors.get(floorId).buildingId || null : null);
    return { buildingId: buildingId || null, floorId: floorId || null, roomId: roomId || null };
  }

  /**
   * True when the space knows the node.
   * @param {string} nodeId
   * @returns {boolean}
   */
  knowsNode(nodeId) {
    return this.nodes.has(nodeId);
  }

  /**
   * Classify how two nodes relate across the space.
   *
   * @param {string} nodeIdA
   * @param {string} nodeIdB
   * @returns {'same-room'|'cross-room'|'cross-floor'|'cross-building'|null}
   *   null when at least one node has no spatial location
   */
  classifyRelation(nodeIdA, nodeIdB) {
    const a = this.locationOf(nodeIdA);
    const b = this.locationOf(nodeIdB);
    if (a.roomId && a.roomId === b.roomId) return SAME_ROOM;
    if (a.floorId && a.floorId === b.floorId) return CROSS_ROOM;
    if (a.buildingId && a.buildingId === b.buildingId) return CROSS_FLOOR;
    if (a.buildingId && b.buildingId) return CROSS_BUILDING;
    return null;
  }

  /**
   * Classify every pair of a node list.
   * @param {string[]} nodeIds
   * @returns {Array<{from: string, to: string, relation: string|null}>}
   */
  crossSpaceRelations(nodeIds) {
    const list = asArray(nodeIds);
    const pairs = [];
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        pairs.push({
          from: list[i],
          to: list[j],
          relation: this.classifyRelation(list[i], list[j]),
        });
      }
    }
    return pairs;
  }

  /**
   * Check the space for dangling references and hierarchy violations.
   *
   * @returns {Array<{id: string, severity: string, message: string, entityId: string}>}
   */
  validate() {
    const diagnostics = [];
    const add = (code, message, entityId) => {
      diagnostics.push(diagnostic(code, message, entityId, 'ERROR'));
    };

    // Same id used on two different levels breaks the hierarchy.
    for (const id of this.buildings.keys()) {
      if (this.floors.has(id) || this.rooms.has(id)) {
        add('GEMARC_SPACE_002', '空间容器 ID 在不同层级重复', id);
      }
    }
    for (const [id] of this.floors) {
      if (this.rooms.has(id)) add('GEMARC_SPACE_002', '空间容器 ID 在不同层级重复', id);
    }

    for (const building of this.buildings.values()) {
      if (!building.semanticSource) {
        add('GEMARC_SPACE_002', '楼宇缺少 semanticSource，虚拟空间不得冒充真实部署设施', building.id);
      }
    }
    for (const floor of this.floors.values()) {
      if (!floor.semanticSource) {
        add('GEMARC_SPACE_002', '楼层缺少 semanticSource，虚拟空间不得冒充真实部署设施', floor.id);
      }
      if (!this.buildings.has(floor.buildingId)) {
        add('GEMARC_SPACE_001', '楼层引用的楼宇不存在', floor.id);
      }
    }
    for (const room of this.rooms.values()) {
      if (!room.semanticSource) {
        add('GEMARC_SPACE_002', '房间缺少 semanticSource，虚拟房间不得冒充真实代码模块', room.id);
      }
      if (!this.floors.has(room.floorId)) {
        add('GEMARC_SPACE_001', '房间引用的楼层不存在', room.id);
      }
    }

    // Container membership.
    const roomOfNode = new Map();
    for (const container of [...this.buildings.values(), ...this.floors.values(), ...this.rooms.values()]) {
      for (const nodeId of asArray(container.nodeIds)) {
        if (!this.nodes.has(nodeId)) {
          add('GEMARC_SPACE_001', '空间容器引用了不存在的节点', container.id);
          continue;
        }
        if (container.level === 'Room') {
          if (roomOfNode.has(nodeId)) {
            add('GEMARC_SPACE_002', '节点同时归属多个房间', nodeId);
          } else {
            roomOfNode.set(nodeId, container.id);
          }
        }
      }
    }

    // Node side of the space reference.
    for (const node of this.nodes.values()) {
      const ref = node.spaceRef;
      if (!ref) continue;
      if (ref.buildingId && !this.buildings.has(ref.buildingId)) {
        add('GEMARC_SPACE_001', '节点空间归属引用的楼宇不存在', node.id);
      }
      if (ref.floorId && !this.floors.has(ref.floorId)) {
        add('GEMARC_SPACE_001', '节点空间归属引用的楼层不存在', node.id);
      }
      if (ref.roomId && !this.rooms.has(ref.roomId)) {
        add('GEMARC_SPACE_001', '节点空间归属引用的房间不存在', node.id);
      }
      if (ref.roomId && ref.floorId && this.rooms.has(ref.roomId)) {
        const room = this.rooms.get(ref.roomId);
        if (room.floorId !== ref.floorId) {
          add('GEMARC_SPACE_002', '节点空间归属的楼层与房间不一致', node.id);
        }
      }
      if (ref.floorId && ref.buildingId && this.floors.has(ref.floorId)) {
        const floor = this.floors.get(ref.floorId);
        if (floor.buildingId !== ref.buildingId) {
          add('GEMARC_SPACE_002', '节点空间归属的楼宇与楼层不一致', node.id);
        }
      }
      if (ref.roomId && roomOfNode.has(node.id) && roomOfNode.get(node.id) !== ref.roomId) {
        add('GEMARC_SPACE_002', '节点的房间归属与空间归属不一致', node.id);
      }
    }

    return diagnostics;
  }

  /**
   * Throw when the space is not consistent.
   * @throws {GemArcError} with the first diagnostic of the space
   */
  assertValid() {
    const diagnostics = this.validate();
    if (diagnostics.length > 0) {
      const first = diagnostics[0];
      throw new GemArcError(first.id, first.message, first.entityId);
    }
    return this;
  }
}
