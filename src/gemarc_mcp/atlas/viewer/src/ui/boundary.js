// Error boundary. Anything that can throw while the page draws runs through guardRun / frameRun: the error is recorded in the shared sink
// (window.__ATLAS_ERRORS__, an array that is only ever pushed to) and the page keeps its state, so one bad tab never freezes the others.
// frameRun advances the heartbeat counter in a `finally`, so a throwing render still counts as a frame and a "running" flag can never stick.
// Pure: the sink and the counter are passed in.
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

const BOUNDARY_MESSAGE_LIMIT = 300;

/** One error record: { where, message, stack? } with a bounded message; never throws. */
export function boundaryRecord(where, err) {
  let message = '';
  try { message = String(err && err.message !== undefined ? err.message : err); } catch (e) { message = 'unprintable'; } // diag: author-facing
  const rec = { where: String(where), message: message.length > BOUNDARY_MESSAGE_LIMIT ? message.slice(0, BOUNDARY_MESSAGE_LIMIT) : message };
  if (err && typeof err.stack === 'string') rec.stack = err.stack.split('\n').slice(0, 4).join('\n');
  return rec;
}

/** makeBoundary(sink) -> { report(where, err), guardRun(where, fn, fallback), frameRun(counter, where, fn), count() }. */
export function makeBoundary(sink) {
  let n = 0;
  const report = (where, err) => {
    n += 1;
    const rec = boundaryRecord(where, err);
    sink.push(rec);
    return rec;
  };
  return {
    report,
    /** Run fn; on a throw record it and return `fallback` (never rethrows). */
    guardRun(where, fn, fallback) {
      try { return fn(); } catch (e) { report(where, e); return fallback; }
    },
    /** Run one render frame: the counter advances whatever happens; the result is { ok, error }. */
    frameRun(counter, where, fn) {
      let ok = true; let error = null;
      try { fn(); } catch (e) { ok = false; error = report(where, e); } finally { counter.n += 1; }
      return { ok, error };
    },
    count: () => n,
  };
}
