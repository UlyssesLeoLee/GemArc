// Shared helpers of the viewer unit tests: a seeded generator of gemarc-atlas/1 shaped models with random id families, a tiny run
// builder and the real vocabulary profile. Synthetic ids only. Deterministic (no Math.random). Not a test file itself.
import { readFileSync } from 'node:fs';

const HERE = new URL('.', import.meta.url);
export const PROFILE = JSON.parse(readFileSync(new URL('../../../src/gemarc_mcp/atlas/profiles/software.json', HERE), 'utf8'));
export const FIXTURE = JSON.parse(readFileSync(new URL('../fixtures/lending_desk.model.json', HERE), 'utf8'));

// mulberry32: small, fast, seeded
export function rng(seed) {
  let a = seed >>> 0;
  const next = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  return { next, int: (n) => Math.floor(next() * n), pick: (arr) => arr[Math.floor(next() * arr.length)] };
}

/** A random id family: two letters and a digit (a different one per seed). */
export function family(seed) {
  const r = rng(seed * 7919 + 13);
  const L = 'abcdefghijklmnopqrstuvwxyz';
  return `${L[r.int(26)]}${L[r.int(26)]}${r.int(10)}`;
}

const KINDS = PROFILE.kinds.filter((k) => k.authorable).map((k) => k.id);
const RELATIONS = PROFILE.relations.map((r) => r.id);
const TRUSTS = PROFILE.trust.map((t) => t.id);

/**
 * A model with `buildings` x `floors` x `rooms` x `perRoom` nodes. Options: seed, prefix (default: family(seed)), edgesPerNode, cases,
 * unknownBuilding (a floor points to a missing building), unknownKind / unknownTrust / unknownRelation (values outside the profile),
 * orphans (nodes with a missing room), levels (explicit floor levels), noCases.
 */
export function makeModel(o = {}) {
  const { seed = 1, buildings = 3, floors = 3, rooms = 3, perRoom = 4, edgesPerNode = 2.2, cases = 3, orphans = 0 } = o;
  const p = o.prefix ?? family(seed); const r = rng(seed);
  const space = { buildings: [], floors: [], rooms: [] }; const nodes = []; const edges = [];
  for (let b = 0; b < buildings; b++) {
    const bid = `${p}b${b}`;
    space.buildings.push({ id: bid, name: `Building ${b}`, trust: o.unknownTrust && b === 0 ? 'no-such-zone' : TRUSTS[b % TRUSTS.length] });
    for (let f = 0; f < floors; f++) {
      const fid = `${p}f${b}_${f}`;
      space.floors.push({ id: fid, buildingId: o.unknownBuilding && b === 0 && f === 0 ? `${p}missing` : bid, name: `Floor ${f}`, level: o.levels ? o.levels[f % o.levels.length] : f });
      for (let q = 0; q < rooms; q++) {
        const rid = `${p}r${b}_${f}_${q}`;
        space.rooms.push({ id: rid, floorId: fid, name: `Room ${q}` });
        for (let k = 0; k < perRoom; k++) {
          const i = nodes.length;
          const kind = o.unknownKind && i % 7 === 0 ? 'NoSuchKind' : KINDS[r.int(KINDS.length)];
          nodes.push({ id: `${p}n${i}`, kind, name: `Node ${i}`, roomId: rid, sub: '', evidenceIds: [], verification: 'Designed', props: {}, portIds: [] });
        }
      }
    }
  }
  for (let i = 0; i < orphans; i++) nodes.push({ id: `${p}o${i}`, kind: 'Module', name: `Orphan ${i}`, roomId: `${p}no-such-room`, sub: '', evidenceIds: [], verification: 'Designed', props: {}, portIds: [] });
  const n = nodes.length;
  const m = n > 1 ? Math.round(n * edgesPerNode) : 0;
  const seen = new Set();
  for (let i = 0; i < m; i++) {
    const s = nodes[r.int(n)].id; const t = nodes[r.int(n)].id;
    const rel = o.unknownRelation && i % 5 === 0 ? 'no-such-relation' : RELATIONS[r.int(RELATIONS.length)];
    const id = `${s}:${rel}:${t}`;
    if (seen.has(id)) continue; seen.add(id);
    edges.push({ id, sourceId: s, targetId: t, type: 'directed', directed: true, relation: rel, static: false, label: `link ${i}`, verification: 'Designed', evidenceIds: [], sourcePortId: null, targetPortId: null, guardId: null });
  }
  const caseList = [];
  for (let c = 0; !o.noCases && c < cases; c++) {
    const len = 3 + r.int(5); const events = []; let cur = edges.length ? edges[r.int(edges.length)] : null;
    for (let s = 1; cur && s <= len; s++) { events.push({ seq: s, type: 'Call', from: cur.sourceId, to: cur.targetId, edge: cur.id }); cur = edges[r.int(edges.length)]; }
    caseList.push({ caseId: `${p.toUpperCase()}C${c}`, category: 'normal', title: `Case ${c}`, requirementIds: [], evidenceIds: [], policy: { timeoutMs: 2000, retryMax: 0, retryBackoffMs: 0 }, context: {}, expectedTerminalState: {}, linkedTestIds: [], events });
  }
  const requirements = nodes.length ? [{ id: `${p.toUpperCase()}Q1`, title: 'Requirement one', source: 'source text', designIds: [], nodeIds: nodes.slice(0, 3).map((x) => x.id), edgeIds: [], tstIds: [], evidenceIds: [], tbdIds: [] }] : [];
  return {
    schema: 'gemarc-atlas/1', meta: { projectId: `proj-${p}`, title: 'Synthetic', mode: 'asis', languages: ['en-US'], profile: 'software', repos: [] },
    space, nodes, edges, ports: [], evidences: [], machines: [], cases: caseList, interventions: [], requirements, tests: [], tbds: [], policies: [],
  };
}

/** A run for one case: one trace event per case event (bound to its edge), nodes go Running then Completed; snapshots every 4 events. */
export function makeRun(model, caseItem, { snapshots = true } = {}) {
  const initialState = Object.fromEntries(model.nodes.map((x) => [x.id, 'Idle']));
  const state = { ...initialState }; const trace = []; const checkpoints = [{ index: -1, state: { ...initialState } }];
  caseItem.events.forEach((e, i) => {
    const changes = [];
    for (const [id, to] of [[e.from, 'Running'], [e.to, i % 3 === 2 ? 'Failed' : 'Completed']]) if (state[id] !== to) { changes.push({ nodeId: id, from: state[id], to }); state[id] = to; }
    trace.push({ index: i, eventId: `${caseItem.caseId}#${e.seq}`, caseId: caseItem.caseId, logicalTime: i * 10, sourceNodeId: e.from, targetNodeId: e.to, edgeId: e.edge ?? null, eventType: e.type, outcome: i % 3 === 2 ? 'Failed' : 'OK', stateChanges: changes });
    if (snapshots && (i + 1) % 4 === 0) checkpoints.push({ index: i, state: { ...state } });
  });
  return { caseId: caseItem.caseId, status: 'OK', errors: [], trace, initialState, finalState: { ...state }, snapshots: snapshots ? checkpoints : [] };
}

/** A chain over (A, B): B = A with its last event removed and one node failed, so there are 'lost' and 'block' steps and one effect. */
export function makeChain(model, A) {
  const last = A.trace.at(-1); const first = A.trace[0];
  const root = { id: 'root', role: 'cause', type: 'disable_module', label: 'cause', nodeIds: [first.targetNodeId], edgeIds: first.edgeId ? [first.edgeId] : [], facts: [], element: null };
  const steps = [
    { id: 'B0', side: 'B', role: 'block', kind: 'changed', index: 0, at: 0, aIndex: 0, bIndex: 0, eventType: first.eventType, sourceNodeId: first.sourceNodeId, targetNodeId: first.targetNodeId, edgeId: first.edgeId, outcome: 'Blocked', cause: null, parents: ['root'], via: [] },
    { id: `A${A.trace.length - 1}`, side: 'A', role: 'lost', kind: 'aOnly', index: A.trace.length - 1, at: Math.max(0, A.trace.length - 1), aIndex: A.trace.length - 1, bIndex: null, eventType: last.eventType, sourceNodeId: last.sourceNodeId, targetNodeId: last.targetNodeId, edgeId: last.edgeId, outcome: last.outcome, cause: null, parents: ['B0'], via: [] },
  ];
  return { caseId: A.caseId, interventionId: 'iv-test', status: 'OK', outcomeClass: 'DIVERGED', root, rows: [], steps, effects: [{ nodeId: last.targetNodeId, a: 'Completed', b: 'Idle', parents: ['B0'] }], counts: {}, design: [] };
}

/** Plain recursive walk: true when every number inside is finite. */
export function allFinite(v) {
  if (typeof v === 'number') return Number.isFinite(v);
  if (v && typeof v === 'object' && !(ArrayBuffer.isView(v))) return Object.values(v instanceof Map ? Object.fromEntries(v) : v).every(allFinite);
  if (ArrayBuffer.isView(v)) { for (let i = 0; i < v.length; i++) if (!Number.isFinite(v[i])) return false; }
  return true;
}
