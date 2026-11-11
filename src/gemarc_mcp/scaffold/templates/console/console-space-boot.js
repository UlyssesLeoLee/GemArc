/**
 * The page entry of the console's crystal scene.
 *
 * The scene needs the three-dimensional library, which is optional: when it
 * cannot be loaded the panel says so and the console keeps working. The
 * library is fetched through the import map of the page, exactly like the
 * public site does.
 *
 * The panel and the scene are two modules, so they talk through one bridge:
 * `window.GEMARC_SPACE`.
 *
 * @module console-space-boot
 */

const HOST_ID = 'space-host';
const HINT_ID = 'space-hint';

/**
 * The one word the scene has to say when it cannot load.
 *
 * The panel has no text of its own (BD-010 section 12.7): the word comes from
 * the language table the server injects. A table that is not there returns the
 * key itself, so the gap is visible instead of hidden.
 * @param {string} key the key of the table
 * @returns {string} the word
 */
function t(key) {
  const gemarc = window.GEMARC || {};
  const tables = gemarc.i18n || {};
  const table = tables[gemarc.lang] || {};
  return typeof table[key] === 'string' && table[key] ? table[key] : key;
}

/**
 * The bridge between the panel and the scene.
 * @returns {object} the bridge
 */
function bridge() {
  const win = window;
  if (!win.GEMARC_SPACE) {
    win.GEMARC_SPACE = { tools: [], scene: null, onPick: null, onHover: null };
  }
  return win.GEMARC_SPACE;
}

/**
 * Say what the panel shows instead of the scene.
 * @param {string} text the words
 */
function say(text) {
  const hint = document.getElementById(HINT_ID);
  if (hint) hint.textContent = text;
}

async function main() {
  const host = document.getElementById(HOST_ID);
  if (!host) return;

  let THREE = null;
  let engine = null;
  try {
    THREE = await import('three');
    engine = await import('./console-space.js');
  } catch {
    say(t('space.notloaded'));
    return;
  }

  const link = bridge();
  try {
    const scene = engine.createConsoleSpace(THREE, {
      container: host,
      tools: link.tools || [],
      onSelect: (name) => { if (link.onPick) link.onPick(name); },
      onHover: (name) => { if (link.onHover) link.onHover(name); },
    });
    link.scene = scene;
    if (link.tools && link.tools.length) scene.setTools(link.tools);
    const hint = document.getElementById(HINT_ID);
    if (hint && typeof hint.remove === 'function') hint.remove();
  } catch {
    say(t('space.notloaded'));
  }
}

main();
