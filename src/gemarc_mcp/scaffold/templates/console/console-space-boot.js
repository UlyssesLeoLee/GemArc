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
    say('三维未加载');
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
    say('三维未加载');
  }
}

main();
