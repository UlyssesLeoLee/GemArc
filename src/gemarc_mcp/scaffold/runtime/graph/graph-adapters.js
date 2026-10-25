/**
 * Optional third-party adapters.
 *
 * The runtime has ZERO required third-party dependency, so no file in this
 * package may import a package by name. Every integration below is therefore
 * a factory that takes the library as an argument:
 *
 *   const adapter = createGraphologyAdapter(window.Graphology);
 *
 * The application decides which libraries it ships; the runtime only knows
 * how to talk to them once they are handed in. When Dagre is absent the
 * deterministic built-in layered layout takes over, so a layout always
 * exists.
 */
import { GemArcError } from '../core/errors.js';
import { IndexedDbPersistenceAdapter } from './graph-persistence.js';

/** The optional integrations this package knows how to activate. */
export const OPTIONAL_ADAPTERS = Object.freeze([
  'graphology',
  'dagre',
  'dexie',
  'sigma',
  'three',
]);

function resolveGraphConstructor(Graphology) {
  if (!Graphology) return null;
  if (typeof Graphology === 'function') return Graphology;
  if (Graphology.Graph && typeof Graphology.Graph === 'function') return Graphology.Graph;
  return null;
}

function resolveDagre(dagre) {
  if (!dagre) return null;
  const candidate = dagre.graphlib ? dagre : (dagre.default && dagre.default.graphlib ? dagre.default : null);
  if (!candidate || !candidate.graphlib || typeof candidate.layout !== 'function') return null;
  return candidate;
}

/**
 * Report which optional libraries the environment provides.
 * @param {object} [environment] the object to look at, defaults to globalThis
 * @returns {{graphology: boolean, dagre: boolean, dexie: boolean, sigma: boolean, three: boolean}}
 */
export function detectOptionalAdapters(environment = typeof globalThis !== 'undefined' ? globalThis : {}) {
  const scope = environment || {};
  return {
    graphology: Boolean(scope.Graphology),
    dagre: Boolean(scope.dagre),
    dexie: Boolean(scope.Dexie),
    sigma: Boolean(scope.Sigma),
    three: Boolean(scope.THREE),
  };
}

/**
 * The deterministic built-in layered layout. It is the fallback whenever
 * Dagre is not supplied, and it always produces the same picture for the
 * same graph.
 * @param {Array<object|string>} nodes the nodes to place
 * @param {Array<object>} edges the edges between them
 * @param {{spacingX?: number, spacingY?: number, originX?: number, originY?: number}} [options]
 * @returns {{positions: Map<string, {x: number, y: number, layer: number}>, width: number, height: number, layers: number}}
 */
export function layeredLayout(nodes, edges, options = {}) {
  const spacingX = options.spacingX ?? 180;
  const spacingY = options.spacingY ?? 120;
  const originX = options.originX ?? 0;
  const originY = options.originY ?? 0;

  const ids = [];
  const seen = new Set();
  for (const node of nodes || []) {
    const id = typeof node === 'string' ? node : node && node.id;
    if (typeof id !== 'string' || id === '' || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  ids.sort();

  const adjacency = new Map(ids.map((id) => [id, new Set()]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  for (const edge of edges || []) {
    if (!edge || typeof edge !== 'object') continue;
    const source = edge.sourceId;
    const target = edge.targetId;
    if (!source || !target || source === target) continue;
    if (!adjacency.has(source) || !adjacency.has(target)) continue;
    if (adjacency.get(source).has(target)) continue;
    adjacency.get(source).add(target);
    indegree.set(target, indegree.get(target) + 1);
  }

  const layer = new Map(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => indegree.get(id) === 0);
  while (queue.length) {
    const current = queue.shift();
    for (const next of adjacency.get(current)) {
      layer.set(next, Math.max(layer.get(next), layer.get(current) + 1));
      indegree.set(next, indegree.get(next) - 1);
      if (indegree.get(next) === 0) queue.push(next);
    }
  }

  const byLayer = new Map();
  for (const id of ids) {
    const index = layer.get(id);
    if (!byLayer.has(index)) byLayer.set(index, []);
    byLayer.get(index).push(id);
  }

  const positions = new Map();
  const layers = [...byLayer.keys()].sort((left, right) => left - right);
  let widest = 0;
  for (const index of layers) {
    const members = byLayer.get(index).slice().sort();
    members.forEach((id, column) => {
      positions.set(id, { x: originX + column * spacingX, y: originY + index * spacingY, layer: index });
      widest = Math.max(widest, column * spacingX);
    });
  }

  return {
    positions,
    width: ids.length ? widest + spacingX : 0,
    height: layers.length ? layers.length * spacingY : 0,
    layers: layers.length,
  };
}

/**
 * Create a layout engine. Dagre is used when it is supplied, otherwise the
 * deterministic layered layout takes over.
 * @param {{dagre?: object}} [options] the Dagre library, when available
 */
export function createLayoutEngine(options = {}) {
  const dagreAdapter = options.dagre ? createDagreLayoutAdapter(options.dagre) : null;
  return {
    name: dagreAdapter ? 'dagre' : 'layered',
    available: Boolean(dagreAdapter),
    /**
     * @param {Array<object|string>} nodes the nodes to place
     * @param {Array<object>} edges the edges between them
     * @param {object} [layoutOptions] engine specific options
     */
    layout(nodes, edges, layoutOptions = {}) {
      if (dagreAdapter) return dagreAdapter.layout(nodes, edges, layoutOptions);
      return layeredLayout(nodes, edges, layoutOptions);
    },
  };
}

/**
 * Create the Graphology adapter. Graphology is the in-memory property graph
 * the store can be projected into.
 * @param {object} Graphology the Graphology library
 * @param {object} [options] reserved for future use
 */
export function createGraphologyAdapter(Graphology, options = {}) {
  const GraphConstructor = resolveGraphConstructor(Graphology);
  if (!GraphConstructor) {
    throw new GemArcError('GEMARC_GRAPH_004', 'Graphology 适配器缺少 Graphology 实例');
  }
  const adapter = {
    name: 'graphology',
    available: true,
    /** @returns {object} an empty Graphology graph. */
    createGraph() {
      return new GraphConstructor();
    },
    /**
     * Copy nodes and edges into a Graphology graph.
     * @param {object} graph the Graphology graph
     * @param {Iterable<object>} nodes the nodes to copy
     * @param {Iterable<object>} edges the edges to copy
     */
    fill(graph, nodes, edges) {
      for (const node of nodes || []) {
        const attributes = { ...node };
        delete attributes.id;
        if (typeof graph.addNode === 'function') graph.addNode(node.id, attributes);
      }
      for (const edge of edges || []) {
        const attributes = { ...edge };
        delete attributes.id;
        attributes.gemarcId = edge.id;
        const directed = edge.directed !== false;
        const withKey = directed ? 'addDirectedEdgeWithKey' : 'addUndirectedEdgeWithKey';
        const plain = directed ? 'addDirectedEdge' : 'addUndirectedEdge';
        if (typeof graph[withKey] === 'function') {
          graph[withKey](edge.id, edge.sourceId, edge.targetId, attributes);
        } else if (typeof graph.addEdgeWithKey === 'function') {
          graph.addEdgeWithKey(edge.id, edge.sourceId, edge.targetId, attributes);
        } else if (typeof graph[plain] === 'function') {
          graph[plain](edge.sourceId, edge.targetId, attributes);
        } else if (typeof graph.addEdge === 'function') {
          graph.addEdge(edge.sourceId, edge.targetId, attributes);
        }
      }
      return graph;
    },
    /**
     * Build a Graphology graph from a graph store.
     * @param {import('./graph-store.js').GraphStore} store the store to copy
     */
    fromStore(store) {
      const graph = adapter.createGraph();
      return adapter.fill(graph, store.nodes.values(), store.edges.values());
    },
    /**
     * Build a Graphology graph from a canonical model.
     * @param {{nodes?: object[], edges?: object[]}} model the model to copy
     */
    fromModel(model) {
      const graph = adapter.createGraph();
      return adapter.fill(graph, model.nodes || [], model.edges || []);
    },
    /**
     * Read a Graphology graph back into a canonical model.
     * @param {object} graph the Graphology graph
     */
    toModel(graph) {
      const nodes = [];
      const edges = [];
      if (typeof graph.forEachNode === 'function') {
        graph.forEachNode((id, attributes) => {
          nodes.push({ ...attributes, id: (attributes && attributes.gemarcId) || id });
        });
      }
      if (typeof graph.forEachEdge === 'function') {
        graph.forEachEdge((id, attributes, source, target) => {
          edges.push({
            ...attributes,
            id: (attributes && attributes.gemarcId) || id,
            sourceId: source,
            targetId: target,
          });
        });
      }
      return { nodes, edges };
    },
  };
  return adapter;
}

/**
 * Create the Dagre layout adapter. Dagre gives a deterministic layered
 * layout; the built-in layered layout is the fallback when it is absent.
 * @param {object} dagre the Dagre library
 * @param {object} [options] default layout settings
 */
export function createDagreLayoutAdapter(dagre, options = {}) {
  const lib = resolveDagre(dagre);
  if (!lib) {
    throw new GemArcError('GEMARC_GRAPH_004', 'Dagre 适配器缺少 Dagre 实例');
  }
  const defaults = {
    rankdir: 'LR',
    nodesep: 60,
    ranksep: 120,
    marginx: 20,
    marginy: 20,
    nodeWidth: 160,
    nodeHeight: 60,
    ...options,
  };
  return {
    name: 'dagre',
    available: true,
    /**
     * @param {Array<object|string>} nodes the nodes to place
     * @param {Array<object>} edges the edges between them
     * @param {object} [layoutOptions] settings overriding the defaults
     * @returns {{positions: Map<string, {x: number, y: number}>, width: number, height: number}}
     */
    layout(nodes, edges, layoutOptions = {}) {
      const settings = { ...defaults, ...layoutOptions };
      const graph = new lib.graphlib.Graph();
      graph.setGraph({
        rankdir: settings.rankdir,
        nodesep: settings.nodesep,
        ranksep: settings.ranksep,
        marginx: settings.marginx,
        marginy: settings.maxy,
      });
      if (typeof graph.setDefaultEdgeLabel === 'function') graph.setDefaultEdgeLabel(() => ({}));
      for (const node of nodes || []) {
        const id = typeof node === 'string' ? node : node && node.id;
        if (typeof id !== 'string' || id === '') continue;
        graph.setNode(id, { width: settings.nodeWidth, height: settings.nodeHeight });
      }
      for (const edge of edges || []) {
        if (!edge || typeof edge !== 'object') continue;
        if (!graph.hasNode(edge.sourceId) || !graph.hasNode(edge.targetId)) continue;
        graph.setEdge(edge.sourceId, edge.targetId, {
          width: 10,
          height: 10,
          labelpos: 'c',
          labeloffset: 0,
        });
      }
      lib.layout(graph);
      const positions = new Map();
      for (const id of graph.nodes()) {
        const position = graph.node(id);
        positions.set(id, { x: position.x, y: position.y });
      }
      const graphSettings = graph.graph() || {};
      return {
        positions,
        width: graphSettings.width || 0,
        height: graphSettings.height || 0,
      };
    },
  };
}

/**
 * Create the Dexie persistence adapter. Dexie is optional; the adapter
 * activates only when the library is handed in.
 * @param {object} Dexie the Dexie library
 * @param {object} [options] database name and store name
 */
export function createDexieAdapter(Dexie, options = {}) {
  if (!Dexie) {
    throw new GemArcError('GEMARC_GRAPH_004', 'Dexie 适配器缺少 Dexie 实例');
  }
  return new IndexedDbPersistenceAdapter({ ...options, Dexie });
}

/**
 * Create the Sigma.js rendering adapter. The caller supplies the graph, so
 * the adapter works with a Graphology graph or a Sigma graph alike.
 * @param {object} Sigma the Sigma.js library
 * @param {object} [options] default renderer settings
 */
export function createSigmaRendererAdapter(Sigma, options = {}) {
  if (!Sigma) {
    throw new GemArcError('GEMARC_GRAPH_004', 'Sigma 适配器缺少 Sigma 实例');
  }
  const defaults = {
    allowInvalidContainer: false,
    labelDensity: 0.07,
    labelGridCellSize: 60,
    labelRenderedSizeThreshold: 6,
    defaultNodeColor: '#8899aa',
    defaultEdgeColor: '#ccddee',
    zIndex: true,
    ...options,
  };
  return {
    name: 'sigma',
    available: true,
    /**
     * @param {HTMLElement} container the element to render into
     * @param {object} graph the graph to render
     * @param {object} [settings] renderer settings
     */
    create(container, graph, settings = {}) {
      const instance = new Sigma(graph, container, { ...defaults, ...settings });
      return {
        instance,
        /** @param {object} next new renderer settings */
        setSettings(next) {
          if (typeof instance.setSettings === 'function') instance.setSettings(next);
        },
        /** Redraw the canvas. */
        refresh() {
          if (typeof instance.refresh === 'function') instance.refresh();
        },
        /** Tear the renderer down. */
        destroy() {
          if (typeof instance.kill === 'function') instance.kill();
          else if (typeof instance.destroy === 'function') instance.destroy();
        },
      };
    },
  };
}

/**
 * Create the Three.js rendering adapter for the 3D view.
 * @param {object} THREE the Three.js library
 * @param {object} [options] default scene settings
 */
export function createThreeRendererAdapter(THREE, options = {}) {
  if (!THREE) {
    throw new GemArcError('GEMARC_GRAPH_004', 'Three 适配器缺少 Three 实例');
  }
  const defaults = { radius: 12, color: 0x8899aa, edgeColor: 0xccddee, ...options };
  return {
    name: 'three',
    available: true,
    /**
     * @param {HTMLElement} container the element to render into
     * @param {{nodes?: object[], edges?: object[], positions?: Map<string, object>}} [scene]
     */
    create(container, scene = {}) {
      const nodes = scene.nodes || [];
      const edges = scene.edges || [];
      const positions = scene.positions || new Map();
      const threeScene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      const width = (container && container.clientWidth) || 800;
      const height = (container && container.clientHeight) || 600;
      renderer.setSize(width, height);
      if (container && typeof container.appendChild === 'function') {
        container.appendChild(renderer.domElement);
      }

      const meshes = new Map();
      for (const node of nodes) {
        const geometry = new THREE.SphereGeometry(defaults.radius, 16, 12);
        const material = new THREE.MeshBasicMaterial({ color: defaults.color });
        const mesh = new THREE.Mesh(geometry, material);
        const position = positions.get(node.id) || { x: 0, y: 0, z: 0 };
        mesh.position.set(position.x, position.y, position.z);
        mesh.userData = { id: node.id, kind: node.kind };
        threeScene.add(mesh);
        meshes.set(node.id, mesh);
      }

      let lines = null;
      const points = [];
      for (const edge of edges) {
        const source = meshes.get(edge.sourceId);
        const target = meshes.get(edge.targetId);
        if (!source || !target) continue;
        points.push(
          source.position.x, source.position.y, source.position.z,
          target.position.x, target.position.y, target.position.z,
        );
      }
      if (points.length && typeof THREE.BufferGeometry === 'function') {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
        const material = new THREE.LineBasicMaterial({ color: defaults.edgeColor });
        lines = new THREE.LineSegments(geometry, material);
        threeScene.add(lines);
      }

      return {
        scene: threeScene,
        camera,
        renderer,
        meshes,
        lines,
        /** Draw one frame. */
        render() {
          renderer.render(threeScene, camera);
        },
        /** @param {number} nextWidth the new width */
        resize(nextWidth, nextHeight) {
          renderer.setSize(nextWidth, nextHeight);
          camera.aspect = nextWidth / nextHeight;
          camera.updateProjectionMatrix();
        },
        /** Release the GPU resources. */
        dispose() {
          for (const mesh of meshes.values()) {
            if (mesh.geometry && typeof mesh.geometry.dispose === 'function') mesh.geometry.dispose();
            if (mesh.material && typeof mesh.material.dispose === 'function') mesh.material.dispose();
          }
          if (lines && lines.geometry && typeof lines.geometry.dispose === 'function') {
            lines.geometry.dispose();
          }
          if (typeof renderer.dispose === 'function') renderer.dispose();
        },
      };
    },
  };
}
