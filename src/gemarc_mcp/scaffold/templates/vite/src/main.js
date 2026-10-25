/**
 * Vite profile entry point.
 *
 * The application source is shared with the static profile; only the module
 * resolution differs. Vite resolves the runtime through the alias configured
 * in vite.config.js, so the app never hard-codes a relative runtime path.
 */
import '../app.js';
import '../style.css';
