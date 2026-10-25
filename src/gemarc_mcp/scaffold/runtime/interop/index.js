/**
 * Interoperability runtime - barrel for the format adapters.
 *
 * Every adapter offers the same pair of functions:
 *   parseX(input)      -> { nodes, edges, warnings }
 *   serializeX(canonical) -> string
 * plus a `format` / `supports` identifier. A parse never throws on a
 * partially broken document: problems land in `warnings`. It throws only
 * when the input is not that format at all
 * (`GemArcError('GEMARC_INTEROP_001', ...)`).
 *
 * Covered canonical subsets:
 *   bpmn   - canonical *workflow* data (workflow.schema.json)
 *   xmi    - canonical *model* nodes and edges (node/edge.schema.json)
 *   er     - canonical *model* nodes and edges (node/edge.schema.json)
 *   drawio - canonical *model* nodes and edges (node/edge.schema.json)
 */
export {
  BPMN_FORMAT,
  parseBpmn,
  serializeBpmn,
  bpmnAdapter,
  parseXmlDocument,
  findAll,
  findFirst,
  childrenNamed,
  attr,
  attrRaw,
  escapeXml,
  decodeEntities,
} from './bpmn-adapter.js';

export {
  XMI_FORMAT,
  parseXmi,
  serializeXmi,
  xmiAdapter,
} from './uml-xmi-adapter.js';

export {
  ER_FORMAT,
  parseEr,
  serializeEr,
  erAdapter,
} from './er-adapter.js';

export {
  DRAWIO_FORMAT,
  parseDrawio,
  serializeDrawio,
  drawioAdapter,
  parseStyle,
  buildStyle,
  plainLabel,
} from './drawio-adapter.js';

import { bpmnAdapter, BPMN_FORMAT } from './bpmn-adapter.js';
import { xmiAdapter, XMI_FORMAT } from './uml-xmi-adapter.js';
import { erAdapter, ER_FORMAT } from './er-adapter.js';
import { drawioAdapter, DRAWIO_FORMAT } from './drawio-adapter.js';
import { GemArcError } from '../core/errors.js';

/** Every adapter, keyed by its format identifier. */
export const INTEROP_ADAPTERS = Object.freeze({
  [BPMN_FORMAT]: bpmnAdapter,
  [XMI_FORMAT]: xmiAdapter,
  [ER_FORMAT]: erAdapter,
  [DRAWIO_FORMAT]: drawioAdapter,
});

/** The supported format identifiers. */
export const INTEROP_FORMATS = Object.freeze(Object.keys(INTEROP_ADAPTERS));

/**
 * Look up an adapter by format identifier.
 * @param {string} format
 * @returns {{format: string, parse: Function, serialize: Function}|null}
 */
export function getAdapter(format) {
  if (typeof format !== 'string') return null;
  return INTEROP_ADAPTERS[format.toLowerCase()] || null;
}

/**
 * Parse a document with the adapter of the given format.
 * @param {string} format
 * @param {string|object} input
 */
export function parseInterop(format, input) {
  const adapter = getAdapter(format);
  if (!adapter) throw new GemArcError('GEMARC_INTEROP_002', '不支持的互操作格式', String(format));
  return adapter.parse(input);
}

/**
 * Serialise canonical data with the adapter of the given format.
 * @param {string} format
 * @param {object} canonical
 */
export function serializeInterop(format, canonical) {
  const adapter = getAdapter(format);
  if (!adapter) throw new GemArcError('GEMARC_INTEROP_002', '不支持的互操作格式', String(format));
  return adapter.serialize(canonical);
}

/**
 * Guess the format of a document.
 * @param {string} input
 * @returns {string|null} one of the format identifiers, or null
 */
export function detectFormat(input) {
  if (typeof input !== 'string' || !input.trim()) return null;
  const text = input.trim();
  if (/<(?:[A-Za-z0-9_.-]+:)?definitions[\s>]/.test(text)) return BPMN_FORMAT;
  if (/<(?:[A-Za-z0-9_.-]+:)?XMI[\s>]/.test(text)) return XMI_FORMAT;
  if (/mxGraphModel|<mxfile[\s>]/.test(text)) return DRAWIO_FORMAT;
  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      const parsed = JSON.parse(text);
      const source = Array.isArray(parsed) ? { entities: parsed } : parsed;
      if (source && (Array.isArray(source.entities) || Array.isArray(source.relations))) return ER_FORMAT;
    } catch {
      return null;
    }
    return null;
  }
  if (/^\s*(entity|table|relation|rel)\s/m.test(text)) return ER_FORMAT;
  return null;
}
