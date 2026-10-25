/**
 * Stable identifier rules.
 *
 * A model id must survive round trips through JSON, URLs, DOM ids and file
 * names, and must not collide when two entities share a display name. The
 * Python validator enforces the same pattern; keep the two in sync.
 */
import { GemArcError } from './errors.js';

export const ID_PATTERN = /^[A-Za-z0-9][\w.:-]{0,127}$/;

export function isValidId(value) {
  return typeof value === 'string' && ID_PATTERN.test(value);
}

export function assertId(value, entityId = '') {
  if (!isValidId(value)) {
    throw new GemArcError('GEMARC_ID_001', 'ID 无效', entityId || String(value));
  }
  return value;
}

/**
 * Deterministic slug used when a human name has to become an id.
 * The caller is responsible for disambiguating collisions; see `dedupeId`.
 */
export function slugify(text) {
  if (typeof text !== 'string') return '';
  const slug = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^0-9A-Za-z㐀-鿿]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
  return slug.slice(0, 96) || 'item';
}

/**
 * Append a numeric suffix until the id is free. Keeps ids stable for the first
 * occurrence of a name and deterministic afterwards.
 */
export function dedupeId(base, taken) {
  if (!taken.has(base)) return base;
  let index = 2;
  while (taken.has(`${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

/**
 * Build a namespaced id, e.g. `evidence.evt-0042`.
 */
export function namespacedId(prefix, suffix) {
  return `${prefix}.${suffix}`;
}
