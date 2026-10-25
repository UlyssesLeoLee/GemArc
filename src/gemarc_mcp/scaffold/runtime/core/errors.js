/**
 * Structured errors for the GemArc runtime.
 *
 * The browser runtime must never leak a host filesystem path or a stack trace
 * into a user-visible message. Every error therefore carries a stable code and
 * an optional entity id, and `toDiagnostic()` produces the same shape the
 * Python side emits so both ends of the contract agree.
 */

export class GemArcError extends Error {
  /**
   * @param {string} code stable diagnostic code, e.g. GEMARC_MODEL_001
   * @param {string} message human readable, free of host paths
   * @param {string} [entityId] id of the offending entity
   */
  constructor(code, message, entityId = '') {
    super(`${code}: ${message}`);
    this.name = 'GemArcError';
    this.code = code;
    this.entityId = entityId;
    this.safeMessage = message;
  }

  toDiagnostic() {
    return { id: this.code, severity: 'ERROR', message: this.safeMessage, entityId: this.entityId };
  }
}

export function diagnostic(code, message, entityId = '', severity = 'ERROR') {
  return { id: code, severity, message, entityId };
}

export function isGemArcError(value) {
  return value instanceof GemArcError;
}

/**
 * Wrap an arbitrary throwable into a diagnostic without leaking internals.
 * Unknown errors expose only their type name.
 */
export function toDiagnostic(error) {
  if (isGemArcError(error)) return error.toDiagnostic();
  return diagnostic('GEMARC_INTERNAL_001', error && error.name ? error.name : 'Error');
}
