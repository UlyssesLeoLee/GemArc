// Test helper (not a test): synthetic sealed runs for the stage tests, and a recording stand-in for a WebGL context. Synthetic ids only: the runs are
// made from the events of a synthetic model (one trace entry per event, a state change on the receiving part), so the shapes are exactly what the
// build embeds (bundle-contract section 4) without needing the engine.

/** One embedded run for a case of the model: status OK, a trace entry per event, `Running` entering the receiving node. */
export function fakeRun(model, caseId, extra = {}) {
  const c = model.cases.find((x) => x.caseId === caseId);
  const events = Array.isArray(c && c.events) ? c.events : [];
  const trace = events.map((e, i) => ({
    index: i, eventId: caseId + '#' + (i + 1), sourceNodeId: e.from, targetNodeId: e.to, edgeId: null, eventType: i % 3 === 2 ? 'Return' : 'Call', outcome: 'OK',
    stateChanges: [{ nodeId: e.to, from: 'Idle', to: i % 2 ? 'Completed' : 'Running' }],
  }));
  return { caseId, status: 'OK', errors: [], trace, initialState: {}, finalState: {}, ...extra };
}

/** A chain over a run: a cause on `rootNode`, a step per event of the run (side B) and one A-only step, one effect. */
export function fakeChain(run, rootNode, status = 'OK') {
  const steps = run.trace.map((ev, i) => ({ id: 'step-' + i, side: 'B', role: i === 0 ? 'cause' : 'block', at: i, aIndex: i, bIndex: i, sourceNodeId: ev.sourceNodeId, targetNodeId: ev.targetNodeId, edgeId: null, outcome: ev.outcome, why: 'step ' + i }));
  if (run.trace.length) steps.push({ id: 'step-a', side: 'A', role: 'lost', at: run.trace.length - 1, aIndex: 0, bIndex: null, sourceNodeId: run.trace[0].sourceNodeId, targetNodeId: run.trace[0].targetNodeId, edgeId: null, outcome: 'OK', why: 'only without the change' });
  const ends = run.trace.flatMap((ev) => [ev.targetNodeId, ev.sourceNodeId]);
  const last = ends.find((id) => id !== rootNode) || rootNode; // an effect on another part than the cause (the cause keeps its own mark)
  return { status, outcomeClass: 'DIVERGED', question: 'q', headline: 'h', root: { id: 'root', role: 'cause', nodeIds: [rootNode], edgeIds: [], element: null }, steps, effects: [{ nodeId: last, a: 'Completed', b: 'Failed', name: 'effect' }] };
}

/**
 * Seal the first `sealedCases` cases of a bundle (baselines) and, for each, one pair per intervention that applies to it (a pair needs a run B and a chain).
 * Returns the bundle (mutated) and what was sealed.
 */
export function sealBundle(bundle, sealedCases = 1) {
  const model = bundle.model;
  const sealed = model.cases.slice(0, sealedCases).map((c) => c.caseId);
  bundle.runs = { baselines: {}, pairs: [] };
  bundle.calibrationHooks.traceLengths = [];
  for (const id of sealed) {
    const run = fakeRun(model, id);
    bundle.runs.baselines[id] = run;
    bundle.calibrationHooks.traceLengths.push({ caseId: id, interventionId: null, length: run.trace.length });
    const fits = model.interventions.filter((iv) => Array.isArray(iv.appliesTo) && iv.appliesTo.includes(id));
    for (const iv of fits.length ? fits : model.interventions.slice(0, 1)) { // a synthetic pair is enough for the viewer when nothing applies
      const b = fakeRun(model, id);
      const root = typeof iv.targetNodeId === 'string' ? iv.targetNodeId : (run.trace[0] ? run.trace[0].sourceNodeId : model.nodes[0].id);
      bundle.runs.pairs.push({ caseId: id, interventionId: iv.interventionId, status: 'OK', errors: [], B: b, diff: { outcomeClass: 'DIVERGED' }, chain: fakeChain(b, root) });
      bundle.calibrationHooks.traceLengths.push({ caseId: id, interventionId: iv.interventionId, length: b.trace.length });
    }
  }
  bundle.meta.counts.sealedCases = sealed.length;
  return { bundle, sealed };
}

/**
 * A recording stand-in for a WebGL 2 context: every method is a recorder, the queries the viewer makes get an answer that works, nothing is rendered.
 * `gl.log` lists [method, ...args] in call order; `gl.calls(name)` counts one method; `gl.draws` is the number of draw calls.
 */
export function fakeGl({ instancing = true, renderer = 'Fake SwiftShader', webgl2 = true } = {}) {
  const log = []; let next = 1;
  const gl = {
    log, draws: 0, TRIANGLES: 4, LINES: 1, POINTS: 0, FLOAT: 5126, ARRAY_BUFFER: 34962, DYNAMIC_DRAW: 35048, DEPTH_TEST: 2929, BLEND: 3042, CULL_FACE: 2884, LEQUAL: 515,
    SRC_ALPHA: 770, ONE_MINUS_SRC_ALPHA: 771, ONE: 1, COLOR_BUFFER_BIT: 16384, DEPTH_BUFFER_BIT: 256, VERTEX_SHADER: 35633, FRAGMENT_SHADER: 35632, COMPILE_STATUS: 35713, LINK_STATUS: 35714,
    VERSION: 7938, RENDERER: 7937, NO_ERROR: 0, drawingBufferWidth: 800, drawingBufferHeight: 500,
    calls: (name) => log.filter((x) => x[0] === name).length,
    getExtension: (n) => (n === 'WEBGL_debug_renderer_info' ? { UNMASKED_RENDERER_WEBGL: 37446 } : n === 'ANGLE_instanced_arrays' && !webgl2 && instancing ? { vertexAttribDivisorANGLE: (...a) => log.push(['vertexAttribDivisorANGLE', ...a]), drawArraysInstancedANGLE: (...a) => { log.push(['drawArraysInstancedANGLE', ...a]); gl.draws++; } } : null),
    getParameter: (p) => (p === 37446 || p === 7937 ? renderer : p === 7938 ? 'WebGL fake' : 0),
    getShaderParameter: () => true, getProgramParameter: () => true, getShaderInfoLog: () => '', getProgramInfoLog: () => '',
    createShader: (t) => { log.push(['createShader', t]); return { id: next++ }; }, createProgram: () => ({ id: next++ }), createBuffer: () => ({ id: next++ }),
    getAttribLocation: (p, n) => { const k = 'a:' + p.id + n; if (!gl.locs) gl.locs = new Map(); if (!gl.locs.has(k)) gl.locs.set(k, gl.locs.size); const v = gl.locs.get(k); log.push(['getAttribLocation', n, v]); return v; },
    getUniformLocation: (p, n) => ({ n }),
    drawArrays: (...a) => { log.push(['drawArrays', ...a]); gl.draws++; },
    isContextLost: () => false, getError: () => 0,
  };
  if (webgl2) { gl.drawArraysInstanced = (...a) => { log.push(['drawArraysInstanced', ...a]); gl.draws++; }; gl.vertexAttribDivisor = (...a) => log.push(['vertexAttribDivisor', ...a]); }
  for (const name of ['shaderSource', 'compileShader', 'attachShader', 'linkProgram', 'useProgram', 'bindBuffer', 'bufferData', 'enableVertexAttribArray', 'disableVertexAttribArray', 'vertexAttribPointer', 'vertexAttrib1f', 'vertexAttrib3f',
    'uniformMatrix4fv', 'uniform1f', 'uniform2f', 'uniform3f', 'uniform3fv', 'viewport', 'clearColor', 'clear', 'enable', 'disable', 'depthFunc', 'depthMask', 'blendFunc']) gl[name] = (...a) => log.push([name, ...a]);
  return gl;
}

/** Install a canvas factory on the stand-in page: canvases get a size and a fake WebGL context. Returns { gls, restore }. */
export function installFakeCanvas(page, { size = { width: 800, height: 500 }, make = () => fakeGl() } = {}) {
  const doc = page.document; const orig = doc.createElement; const gls = [];
  doc.createElement = (tag) => {
    const el = orig(tag);
    if (String(tag).toLowerCase() === 'canvas') {
      el.rect = { left: 0, top: 0, ...size }; el.width = 300; el.height = 150;
      el.getContext = (kind) => { const gl = make(kind); if (gl) gls.push(gl); return gl; };
    }
    return el;
  };
  return { gls, restore: () => { doc.createElement = orig; } };
}
