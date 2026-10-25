/**
 * Space navigation (Universal 5D GemArc Web v5.0 §6).
 *
 * The eight required navigations form a small state machine:
 *
 *   overview           global bird's eye
 *   building-select    pick a building
 *   floor-switch       switch to a floor
 *   floor-exploded     explode a floor into its rooms
 *   room-focus         focus a room
 *   room-cutaway       cut a room open
 *   node-focus         focus a node or an edge
 *   back-to-overview   step back to the global bird's eye
 *
 * The machine only moves the camera. It never invents geometry: there are no
 * doors, windows or furniture, and a relationship line is picked through the
 * graph, never through a decoration.
 */
import { GemArcError } from '../core/errors.js';
import { VOCAB } from '../core/vocab.js';

/**
 * The navigation ids, straight from the vocabulary.
 * @type {readonly string[]}
 */
export const NAVIGATION_IDS = Object.freeze(
  VOCAB['space-levels'].navigations.map((item) => item.id),
);

/**
 * The label of every navigation id.
 * @type {Readonly<Record<string, string>>}
 */
export const NAVIGATION_LABELS = Object.freeze(
  Object.fromEntries(VOCAB['space-levels'].navigations.map((item) => [item.id, item.label])),
);

const NAVIGATION_SPECS = {
  overview: { from: [...NAVIGATION_IDS], requires: [] },
  'building-select': { from: ['overview'], requires: ['buildingId'] },
  'floor-switch': { from: ['building-select'], requires: ['floorId'] },
  'floor-exploded': { from: ['floor-switch'], requires: ['floorId'] },
  'room-focus': {
    from: ['floor-switch', 'floor-exploded'],
    requires: ['roomId'],
  },
  'room-cutaway': { from: ['room-focus'], requires: ['roomId'] },
  'node-focus': {
    from: ['room-focus', 'room-cutaway'],
    requires: [],
  },
  'back-to-overview': { from: [...NAVIGATION_IDS], requires: [] },
};

/**
 * The state the machine starts in and returns to.
 * @returns {object} the initial navigation state
 */
function initialState() {
  return {
    navigation: 'overview',
    buildingId: null,
    floorId: null,
    roomId: null,
    nodeId: null,
    edgeId: null,
    path: ['overview'],
  };
}

/**
 * The Building–Floor–Room navigation state machine.
 */
export class SpaceNavigation {
  /**
   * @param {{space?: object|null, onChange?: Function|null}} [options]
   */
  constructor({ space = null, onChange = null } = {}) {
    this.space = space;
    this.onChange = onChange;
    this.state = initialState();
    /** @type {object[]} states visited before the current one */
    this.history = [];
  }

  /**
   * The current navigation id.
   * @returns {string}
   */
  get navigation() {
    return this.state.navigation;
  }

  /**
   * True when the navigation may be entered from the current state.
   * @param {string} navigationId
   * @returns {boolean}
   */
  canNavigate(navigationId) {
    const spec = NAVIGATION_SPECS[navigationId];
    if (!spec) return false;
    return spec.from.includes(this.state.navigation);
  }

  /**
   * Move the camera to another navigation.
   *
   * @param {string} navigationId one of the eight navigations
   * @param {{buildingId?: string, floorId?: string, roomId?: string,
   *          nodeId?: string, edgeId?: string}} [payload]
   * @returns {object} the new navigation state
   */
  navigate(navigationId, payload = {}) {
    if (!NAVIGATION_IDS.includes(navigationId)) {
      throw new GemArcError('GEMARC_SPACE_003', '未知的空间导航', String(navigationId));
    }
    const spec = NAVIGATION_SPECS[navigationId];
    if (!spec.from.includes(this.state.navigation)) {
      throw new GemArcError(
        'GEMARC_SPACE_003',
        `非法的空间导航迁移: ${this.state.navigation} -> ${navigationId}`,
      );
    }
    for (const key of spec.requires) {
      const value = payload[key];
      if (!value) {
        throw new GemArcError('GEMARC_SPACE_003', `空间导航缺少参数: ${key}`, navigationId);
      }
      this._assertExists(key, value);
    }
    if (navigationId === 'node-focus' && !payload.nodeId && !payload.edgeId) {
      throw new GemArcError('GEMARC_SPACE_003', '空间导航缺少参数: nodeId', navigationId);
    }

    this.history.push(this.state);
    let next;
    if (navigationId === 'overview' || navigationId === 'back-to-overview') {
      next = initialState();
      next.path = [...this.state.path, navigationId];
    } else {
      next = { ...this.state, navigation: navigationId, path: [...this.state.path, navigationId] };
      if (payload.buildingId) next.buildingId = payload.buildingId;
      if (payload.floorId) next.floorId = payload.floorId;
      if (payload.roomId) next.roomId = payload.roomId;
      if (payload.nodeId) next.nodeId = payload.nodeId;
      if (payload.edgeId) next.edgeId = payload.edgeId;
      if (next.floorId && !payload.buildingId) {
        const floor = this.space?.floor(next.floorId);
        if (floor) next.buildingId = floor.buildingId;
      }
      if (next.roomId && !payload.floorId) {
        const room = this.space?.room(next.roomId);
        if (room) next.floorId = room.floorId;
      }
      this._assertConsistent(next);
    }

    this.state = next;
    if (this.onChange) this.onChange(this.state);
    return this.state;
  }

  /**
   * Step back to the previous navigation state.
   * @returns {object} the restored navigation state
   */
  back() {
    if (this.history.length === 0) return this.state;
    this.state = this.history.pop();
    if (this.onChange) this.onChange(this.state);
    return this.state;
  }

  /**
   * Return to the global bird's eye.
   * @returns {object} the initial navigation state
   */
  backToOverview() {
    return this.navigate('back-to-overview');
  }

  /**
   * Reset the machine to the initial state.
   * @returns {object} the initial navigation state
   */
  reset() {
    this.state = initialState();
    this.history = [];
    if (this.onChange) this.onChange(this.state);
    return this.state;
  }

  _assertExists(key, value) {
    if (!this.space) return;
    if (key === 'buildingId' && !this.space.buildings?.has(value)) {
      throw new GemArcError('GEMARC_SPACE_001', '导航目标楼宇不存在', value);
    }
    if (key === 'floorId' && !this.space.floors?.has(value)) {
      throw new GemArcError('GEMARC_SPACE_001', '导航目标楼层不存在', value);
    }
    if (key === 'roomId' && !this.space.rooms?.has(value)) {
      throw new GemArcError('GEMARC_SPACE_001', '导航目标房间不存在', value);
    }
    if (key === 'nodeId' && this.space.nodes?.size && !this.space.nodes.has(value)) {
      throw new GemArcError('GEMARC_SPACE_001', '导航目标节点不存在', value);
    }
  }

  _assertConsistent(state) {
    if (state.floorId) {
      const floor = this.space?.floor(state.floorId);
      if (floor && state.buildingId && floor.buildingId !== state.buildingId) {
        throw new GemArcError('GEMARC_SPACE_002', '楼层不属于所选楼宇', state.floorId);
      }
    }
    if (state.roomId) {
      const room = this.space?.room(state.roomId);
      if (room && state.floorId && room.floorId !== state.floorId) {
        throw new GemArcError('GEMARC_SPACE_002', '房间不属于所选楼层', state.roomId);
      }
    }
  }
}
